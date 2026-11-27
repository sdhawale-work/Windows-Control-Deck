"""Control Deck: turn a phone into a remote control for this Windows PC.

Run:  python deck.py
Then open the printed address in Safari on the phone and enter the PIN.

Works with just Python 3.8+. Optional installs unlock more:
    pip install pillow   -> live screen preview (Screen tab)
    pip install pycaw    -> real volume slider (without it you get Vol -/+ buttons)
Spotify (Music tab) needs a free developer app and Spotify Premium. Setup is shown in the Music tab.
Edit deck.json (created on first run) to change the PIN, add buttons or app panels.
"""
import base64, ctypes, hashlib, hmac, io, json, os, random, secrets, socket, subprocess, sys
import threading, time, urllib.error, urllib.parse, urllib.request, webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "deck.json")
TOKEN_PATH = os.path.join(HERE, "spotify_token.json")

if os.name != "nt":
    sys.exit("Control Deck only runs on Windows.")

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

try:
    from PIL import ImageGrab
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# ---------- config ----------
DEFAULT_PANELS = [
    {"name": "OBS Studio",
     "note": "Set the same hotkeys in OBS under Settings > Hotkeys.",
     "buttons": [
        {"label": "Start / stop recording", "keys": "ctrl+alt+r"},
        {"label": "Start / stop streaming", "keys": "ctrl+alt+s"},
        {"label": "Mute microphone", "keys": "ctrl+alt+m"},
        {"label": "Next scene", "keys": "ctrl+alt+right"}]},
]

def load_cfg():
    cfg = None
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    if cfg is None:
        cfg = {"pin": "%04d" % random.randint(0, 9999),
               "buttons": [
                   {"label": "Calculator", "run": "calc"},
                   {"label": "Notepad", "run": "notepad"},
                   {"label": "Files", "run": "explorer"},
                   {"label": "Task manager", "keys": "ctrl+shift+esc"}]}
    cfg.setdefault("panels", DEFAULT_PANELS)
    cfg.setdefault("spotify_client_id", "")
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg

CFG = load_cfg()

# ---------- volume (optional, needs pycaw) ----------
try:
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL

    _tls = threading.local()

    def _make_endpoint():
        spk = AudioUtilities.GetSpeakers()
        if hasattr(spk, "EndpointVolume"):
            return spk.EndpointVolume
        i = spk.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return ctypes.cast(i, ctypes.POINTER(IAudioEndpointVolume))

    def _endpoint():
        ep = getattr(_tls, "ep", None)
        if ep is None:
            comtypes.CoInitialize()
            ep = _tls.ep = _make_endpoint()
        return ep

    def get_volume():
        for _ in range(2):
            try:
                return round(_endpoint().GetMasterVolumeLevelScalar(), 3)
            except Exception:
                _tls.ep = None
        return None

    def get_muted():
        try:
            return bool(_endpoint().GetMute())
        except Exception:
            _tls.ep = None
            return False

    def set_volume(v):
        for _ in range(2):
            try:
                _endpoint().SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(v))), None)
                return
            except Exception:
                _tls.ep = None
        raise ValueError("could not set volume")
except Exception:
    def get_volume():
        return None

    def get_muted():
        return False

    def set_volume(v):
        raise ValueError("pycaw not installed")

# ---------- brightness (laptop panel, via WMI) ----------
def ps(cmd, timeout=10):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                          text=True, timeout=timeout, creationflags=0x08000000)

def get_brightness():
    try:
        out = ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness").stdout.split()
        return int(out[0]) / 100 if out else None
    except Exception:
        return None

_br = {"target": None, "busy": False}

def set_brightness(v):
    _br["target"] = max(5, min(100, int(float(v) * 100)))  # never fully black
    if _br["busy"]:
        return
    _br["busy"] = True

    def run():
        try:
            while _br["target"] is not None:
                t, _br["target"] = _br["target"], None
                ps("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod "
                   "-MethodName WmiSetBrightness -Arguments @{Timeout=[uint32]1;Brightness=[byte]%d}" % t)
        finally:
            _br["busy"] = False
    threading.Thread(target=run, daemon=True).start()

# ---------- Spotify (Web API, PKCE, needs Premium) ----------
SP = {"q": {}, "verifier": None, "state": None, "tok": None}
REDIRECT = "http://127.0.0.1:%d/callback" % PORT
SCOPE = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

def sp_save(tok):
    SP["tok"] = tok
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(tok, f)

def sp_post_token(data):
    req = urllib.request.Request("https://accounts.spotify.com/api/token",
                                 data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def sp_token():
    if not CFG.get("spotify_client_id"):
        return None
    if SP["tok"] is None and os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, encoding="utf-8") as f:
                SP["tok"] = json.load(f)
        except Exception:
            pass
    t = SP["tok"]
    if not t:
        return None
    if t["exp"] < time.time() + 30:
        try:
            r = sp_post_token({"grant_type": "refresh_token", "refresh_token": t["refresh"],
                               "client_id": CFG["spotify_client_id"]})
            sp_save({"access": r["access_token"], "refresh": r.get("refresh_token", t["refresh"]),
                     "exp": time.time() + r["expires_in"]})
        except Exception:
            return None
    return SP["tok"]["access"]

def sp_login_url():
    SP["verifier"] = secrets.token_urlsafe(64)
    SP["state"] = secrets.token_urlsafe(16)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(SP["verifier"].encode()).digest()).rstrip(b"=").decode()
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": CFG["spotify_client_id"], "response_type": "code", "redirect_uri": REDIRECT,
        "scope": SCOPE, "code_challenge_method": "S256", "code_challenge": challenge, "state": SP["state"]})

def sp_callback(qs):
    if qs.get("state", [""])[0] != SP["state"] or "code" not in qs:
        return "Login failed or expired. Open /spotify/login on this PC again."
    try:
        r = sp_post_token({"grant_type": "authorization_code", "code": qs["code"][0], "redirect_uri": REDIRECT,
                           "client_id": CFG["spotify_client_id"], "code_verifier": SP["verifier"]})
        sp_save({"access": r["access_token"], "refresh": r["refresh_token"], "exp": time.time() + r["expires_in"]})
        return "Spotify connected. You can close this tab and use the Music tab on your phone."
    except Exception as e:
        return "Spotify login failed: %s. Check the Client ID and the redirect URI." % e

def sp_call(method, path, params=None):
    tok = sp_token()
    if not tok:
        return 401, None
    url = "https://api.spotify.com/v1" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, data=b"" if method in ("PUT", "POST") else None, method=method,
                                 headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

def _small(imgs):
    return imgs[-1]["url"] if imgs else ""

def sp_state():
    if not CFG.get("spotify_client_id"):
        return {"cfg": False}
    if not sp_token():
        return {"cfg": True, "login": False}
    st, p = sp_call("GET", "/me/player")
    base = {"cfg": True, "login": True, "active": False}
    if st == 403:
        base["err"] = "Spotify Premium is required to control playback."
        return base
    if not p or not p.get("item"):
        return base
    it = p["item"]
    alb = it.get("album") or {}
    imgs = alb.get("images") or (it.get("images") or [])
    out = dict(base, active=True, playing=bool(p.get("is_playing")), progress=p.get("progress_ms") or 0,
               duration=it.get("duration_ms") or 0, uri=it.get("uri", ""), title=it.get("name", ""),
               artist=", ".join(a["name"] for a in it.get("artists", [])) or (it.get("show") or {}).get("name", ""),
               album=alb.get("name", ""), img=imgs[0]["url"] if imgs else "")
    c = SP["q"]
    if c.get("uri") != out["uri"] or time.time() - c.get("t", 0) > 20:
        st2, q = sp_call("GET", "/me/player/queue")
        items = []
        for x in ((q or {}).get("queue") or [])[:25]:
            xi = (x.get("album") or {}).get("images") or x.get("images") or []
            items.append({"uri": x.get("uri", ""), "title": x.get("name", ""),
                          "artist": ", ".join(a["name"] for a in x.get("artists", [])), "img": _small(xi)})
        SP["q"] = c = {"uri": out["uri"], "t": time.time(), "items": items}
    out["queue"] = c.get("items", [])
    return out

def sp_cmd(r):
    op = r["op"]
    if op == "play":
        sp_call("PUT", "/me/player/play")
    elif op == "pause":
        sp_call("PUT", "/me/player/pause")
    elif op == "next":
        sp_call("POST", "/me/player/next")
    elif op == "prev":
        sp_call("POST", "/me/player/previous")
    elif op == "seek":
        sp_call("PUT", "/me/player/seek", {"position_ms": max(0, int(r["ms"]))})
    elif op == "skip":
        n = max(1, min(int(r["n"]), 25))

        def run():
            for _ in range(n):
                sp_call("POST", "/me/player/next")
                time.sleep(0.35)
        threading.Thread(target=run, daemon=True).start()
    SP["q"] = {}

# ---------- input injection ----------
VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "tab": 9, "esc": 0x1B,
      "enter": 0x0D, "backspace": 8, "space": 0x20, "delete": 0x2E, "left": 0x25,
      "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "play": 0xB3, "next": 0xB0, "prev": 0xB1,
      "mute": 0xAD, "voldown": 0xAE, "volup": 0xAF, "printscreen": 0x2C}
for _i in range(1, 13):
    VK["f%d" % _i] = 0x6F + _i
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E, 0x5B}

def vk(name):
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError("unknown key: " + name)

def key_event(code, up):
    user32.keybd_event(code, 0, (1 if code in EXTENDED else 0) | (2 if up else 0), 0)

def press(combo):
    codes = [vk(k) for k in combo.split("+")]
    for c in codes:
        key_event(c, False)
    for c in reversed(codes):
        key_event(c, True)

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]

def type_text(text):
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        unit = units[i] | (units[i + 1] << 8)
        if unit in (10, 13):
            press("enter")
            continue
        for flags in (4, 4 | 2):
            inp = INPUT(type=1)
            inp.u.ki = KEYBDINPUT(0, unit, flags, 0, 0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def mouse(flags, data=0, dx=0, dy=0):
    user32.mouse_event(flags, dx, dy, data, 0)

def click(button):
    if button == "r":
        mouse(0x08); mouse(0x10)
    else:
        mouse(0x02); mouse(0x04)

def power(mode):
    if mode == "lock":
        user32.LockWorkStation()
    elif mode == "sleep":
        subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
    elif mode == "restart":
        subprocess.Popen("shutdown /r /t 5")
    elif mode == "off":
        subprocess.Popen("shutdown /s /t 5")

def run_item(b):
    if "keys" in b:
        press(b["keys"])
    elif "run" in b:
        subprocess.Popen(b["run"], shell=True)

def grab(width):
    img = ImageGrab.grab()
    width = max(200, min(int(width), 1600))
    img = img.resize((width, max(1, int(img.height * width / img.width))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=55)
    return buf.getvalue()

# ---------- actions ----------
def handle(req):
    a = req.get("a")
    if a == "deck":
        return {"buttons": [b.get("label", "?") for b in CFG.get("buttons", [])],
                "panels": [{"name": p.get("name", "Panel"), "note": p.get("note", ""),
                            "buttons": [b.get("label", "?") for b in p.get("buttons", [])]}
                           for p in CFG.get("panels", [])],
                "volume": get_volume(), "bright": get_brightness(), "shot": HAVE_PIL}
    if a == "key":
        press(str(req["keys"]))
    elif a == "type":
        type_text(str(req["text"]))
    elif a == "move":
        mouse(0x01, 0, int(req["dx"]), int(req["dy"]))
    elif a == "scroll":
        mouse(0x0800, int(req["dy"]) * 6)
    elif a == "click":
        click(req.get("b", "l"))
    elif a == "tap":
        x = min(1.0, max(0.0, float(req["x"]))); y = min(1.0, max(0.0, float(req["y"])))
        user32.SetCursorPos(int(x * (user32.GetSystemMetrics(0) - 1)), int(y * (user32.GetSystemMetrics(1) - 1)))
        click(req.get("b", "l"))
    elif a == "item":
        run_item(CFG["buttons"][int(req["i"])])
    elif a == "panel":
        run_item(CFG["panels"][int(req["p"])]["buttons"][int(req["i"])])
    elif a == "vol":
        return {"v": get_volume(), "m": get_muted()}
    elif a == "setvol":
        set_volume(req["v"])
    elif a == "setbright":
        set_brightness(req["v"])
    elif a == "sp":
        return sp_state()
    elif a == "spc":
        sp_cmd(req)
    elif a == "power":
        power(req["m"])
    else:
        raise ValueError("unknown action")
    return {"ok": True}

# ---------- http ----------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def text(self, msg):
        page = "<meta name=viewport content='width=device-width'><body style='font:20px system-ui;padding:40px'>%s</body>" % msg
        self.reply(200, page.encode(), "text/html; charset=utf-8")

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        local = self.client_address[0] in ("127.0.0.1", "::1")
        if p.path == "/":
            self.reply(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif p.path == "/spotify/login" and local:
            if not CFG.get("spotify_client_id"):
                self.text("Add your Spotify Client ID to deck.json as spotify_client_id, then restart deck.py.")
            else:
                self.reply(302, b"", "text/plain", {"Location": sp_login_url()})
        elif p.path == "/callback" and local:
            self.text(sp_callback(urllib.parse.parse_qs(p.query)))
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        if self.path not in ("/api", "/shot"):
            return self.reply(404, {"error": "not found"})
        if not hmac.compare_digest(self.headers.get("X-Pin", ""), str(CFG["pin"])):
            time.sleep(1)
            return self.reply(401, {"error": "wrong pin"})
        try:
            req = json.loads(raw)
            if self.path == "/shot":
                if not HAVE_PIL:
                    return self.reply(501, {"error": "pip install pillow"})
                return self.reply(200, grab(req.get("w", 720)), "image/jpeg")
            self.reply(200, handle(req))
        except Exception as e:
            self.reply(400, {"error": str(e)})

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "your-pc-ip"
    finally:
        s.close()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Deck">
<meta name="theme-color" content="#0b0d1f">
<title>Deck</title>
<style>
:root{--sec:rgba(235,235,245,.66);--blue:#64a8ff;--red:#ff6b62}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;touch-action:manipulation}
html{background:#0b0d1f}
html,body{margin:0;height:100%;color:#fff;font:17px/1.3 -apple-system,"SF Pro Text",system-ui,sans-serif;overscroll-behavior:none}
body{display:flex;flex-direction:column;padding-top:env(safe-area-inset-top)}
body::before{content:"";position:fixed;inset:0;z-index:-1;background:
 radial-gradient(60% 38% at 12% 8%,#7a3cff,transparent 72%),radial-gradient(50% 36% at 95% 28%,#00b8ff,transparent 72%),
 radial-gradient(62% 42% at 15% 88%,#ff3d81,transparent 72%),radial-gradient(48% 36% at 92% 92%,#ffa21f,transparent 72%),#0b0d1f}
main{flex:1;overflow:auto;-webkit-overflow-scrolling:touch;padding:0 14px 16px}
h1{font:700 34px/1.15 -apple-system,"SF Pro Display",system-ui,sans-serif;letter-spacing:.37px;margin:14px 4px 12px;text-shadow:0 2px 14px rgba(0,0,0,.3)}
.hd{font-size:13px;color:var(--sec);text-transform:uppercase;margin:22px 16px 7px}
.ft{font-size:13px;color:var(--sec);margin:8px 16px 0}
.gf,.gl{background:linear-gradient(135deg,rgba(255,255,255,.25),rgba(255,255,255,.07));border:1px solid rgba(255,255,255,.26);
 box-shadow:inset 0 1px 0 rgba(255,255,255,.6),inset 0 -1px 0 rgba(255,255,255,.08),0 10px 26px rgba(0,0,0,.28)}
.gl{-webkit-backdrop-filter:blur(18px) saturate(170%);backdrop-filter:blur(18px) saturate(170%)}
.ib{width:30px;height:30px;flex:none;border-radius:9px;display:flex;align-items:center;justify-content:center;color:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.45),0 2px 6px rgba(0,0,0,.3)}
.ib.l{width:46px;height:46px;border-radius:14px}
.pk{background:linear-gradient(145deg,#ff8ab4,#ff2d78)}.or{background:linear-gradient(145deg,#ffb35c,#ff7a00)}.yl{background:linear-gradient(145deg,#ffe066,#ffb800)}
.gr{background:linear-gradient(145deg,#6ee79a,#17b855)}.tl{background:linear-gradient(145deg,#5ef0e0,#00b3a4)}.bl{background:linear-gradient(145deg,#6cc4ff,#0a7aff)}
.in{background:linear-gradient(145deg,#8e8cff,#4b3df5)}.pu{background:linear-gradient(145deg,#d28cff,#a03cf0)}.rd{background:linear-gradient(145deg,#ff8a80,#ff3b30)}.gy{background:linear-gradient(145deg,#c4c8d4,#7a8090)}
nav{display:flex;margin:6px 10px calc(8px + env(safe-area-inset-bottom));padding:5px;border-radius:30px}
nav button{flex:1;background:none;border:0;border-radius:24px;padding:7px 0 4px;color:rgba(255,255,255,.75);font:600 10px -apple-system,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;gap:2px}
nav button.on{background:rgba(255,255,255,.22);box-shadow:inset 0 1px 0 rgba(255,255,255,.5);color:#fff}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.t{height:92px;border:0;border-radius:22px;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;font:600 12px -apple-system,system-ui,sans-serif;transition:transform .12s,filter .12s}
.t:active,.t.hit{transform:scale(.95);filter:brightness(1.3)}
.t.sm{height:50px;flex-direction:row;font-size:15px;border-radius:16px}
.group{border-radius:20px;overflow:hidden}
.row{position:relative;display:flex;align-items:center;gap:12px;min-height:50px;padding:10px 16px}
.row+.row::before{content:"";position:absolute;top:0;left:58px;right:0;border-top:.5px solid rgba(255,255,255,.2)}
.row.plain+.row::before,.row.plain::before{left:16px}
.row.tp:active{background:none}.row.tp.press{background:rgba(255,255,255,.16)}
.row .rl{flex:1;min-width:0}.row .rd{color:var(--sec);font-size:15px}
.row .th{width:44px;height:44px;border-radius:8px;object-fit:cover;flex:none;background:rgba(255,255,255,.12)}
.row .s2{display:block;color:var(--sec);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .s1{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row.red .rl{color:var(--red)}.row.blue .rl{color:var(--blue)}
.vol{display:flex;gap:10px;margin-top:10px}
.bar{position:relative;flex:1;height:58px;border-radius:20px;overflow:hidden;touch-action:none}
.bar i{position:absolute;left:0;top:0;bottom:0;width:var(--p,0%);box-shadow:inset -1px 0 0 rgba(255,255,255,.55)}
.bar svg{position:absolute;left:16px;top:17px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.45));pointer-events:none}
.bar.muted i{opacity:.35}.bar.sk{height:10px;border-radius:5px;flex:none}
.pad{height:40vh;margin-top:4px;border-radius:24px;display:flex;align-items:center;justify-content:center;color:var(--sec);text-align:center;padding:24px;font-size:15px;touch-action:none}
.seg{display:flex;border-radius:11px;padding:3px;margin-top:10px}
.seg button{flex:1;border:0;background:none;border-radius:8px;padding:7px 0;font:600 13px -apple-system,system-ui,sans-serif;color:#fff}
.seg button.on{background:rgba(255,255,255,.28);box-shadow:inset 0 1px 0 rgba(255,255,255,.5)}
.row2{display:flex;gap:10px;margin-top:10px}.row2>*{flex:1}
input.f{-webkit-user-select:text;user-select:text;font:17px -apple-system,system-ui,sans-serif;padding:0 14px;height:46px;border-radius:16px;color:#fff;min-width:0;outline:0}
input.f::placeholder{color:rgba(255,255,255,.5)}
.go{flex:0 0 78px;color:#fff;border:0;border-radius:16px;font:600 17px -apple-system,system-ui,sans-serif;background:linear-gradient(145deg,#6cc4ff,#0a7aff)}
.go:active{opacity:.7}
.shot{margin-top:10px;border-radius:18px;overflow:hidden;min-height:120px}
.shot img{display:block;width:100%;height:auto}
.np{border-radius:28px;padding:16px;margin-top:4px}
.art{display:block;width:100%;max-width:300px;aspect-ratio:1;margin:0 auto 14px;border-radius:20px;object-fit:cover;background:rgba(255,255,255,.12);box-shadow:0 14px 34px rgba(0,0,0,.45)}
.ti{font:700 22px/1.2 -apple-system,system-ui,sans-serif}.ar{color:var(--sec);margin-top:2px}.al{color:var(--sec);font-size:13px}
.tm{display:flex;justify-content:space-between;font-size:12px;color:var(--sec);margin:8px 2px 0;font-variant-numeric:tabular-nums}
.ctl{display:flex;justify-content:center;align-items:center;gap:26px;margin-top:10px}
.cb{width:58px;height:58px;border-radius:50%;border:0;color:#fff;display:flex;align-items:center;justify-content:center}.cb.big{width:74px;height:74px}
.cb:active{transform:scale(.92)}
.card{border-radius:20px;padding:16px;color:#fff;font-size:15px;line-height:1.45}.card b{display:block;font-size:17px;margin-bottom:6px}
.card code{background:rgba(0,0,0,.3);border-radius:6px;padding:1px 6px;font-size:13px;-webkit-user-select:text;user-select:text}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;z-index:9}
.sheet{margin:auto auto calc(8px + env(safe-area-inset-bottom));width:calc(100% - 16px);max-width:460px;display:flex;flex-direction:column;gap:8px}
.sg{border-radius:20px;overflow:hidden;background:rgba(50,52,78,.62)}
.st{padding:14px;text-align:center;color:var(--sec);font-size:13px;border-bottom:.5px solid rgba(255,255,255,.2)}
.sb{width:100%;border:0;background:rgba(50,52,78,.62);color:var(--blue);font:400 20px -apple-system,system-ui,sans-serif;padding:17px 0;border-radius:20px;-webkit-backdrop-filter:blur(24px);backdrop-filter:blur(24px)}
.sg .sb{border-radius:0;background:none}.sb.red{color:var(--red)}.sb.cancel{font-weight:600}.sb:active{background:rgba(255,255,255,.2)}
.alert{margin:auto;width:280px;border-radius:24px;text-align:center;overflow:hidden;background:rgba(50,52,78,.66);-webkit-backdrop-filter:blur(24px);backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,.26)}
.alert .at{padding:20px 16px 14px}.alert b{display:block;font-size:17px;margin-bottom:4px}.alert span{font-size:13px;color:var(--sec)}
.alert input{width:100%;text-align:center;font-size:22px;letter-spacing:.3em;height:40px;border-radius:12px;border:.5px solid rgba(255,255,255,.3);background:rgba(0,0,0,.25);color:#fff;margin-top:12px;outline:0;-webkit-user-select:text;user-select:text}
.alert .sb{border-top:.5px solid rgba(255,255,255,.2);border-radius:0;font-weight:600;font-size:17px;padding:13px 0;background:none}
#lock{display:none}
@media (orientation:landscape) and (max-height:520px){
body{flex-direction:row;padding-top:0}
nav{flex-direction:column;width:76px;flex:none;margin:8px 0 8px calc(8px + env(safe-area-inset-left));padding:4px;overflow-y:auto}
nav button{padding:6px 0 3px}
main{padding:0 calc(14px + env(safe-area-inset-right)) 12px 12px}
h1{font-size:28px;margin:8px 4px 8px}.hd{margin-top:14px}
.tiles{grid-template-columns:repeat(auto-fill,minmax(104px,1fr))}
.t{height:80px}.t.sm{height:46px}
.pw{display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:start}
.pw>*{margin-top:0!important}
.pw .pad{grid-row:1/span 3;height:calc(100vh - 100px);min-height:150px}
.mb{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0 14px;align-items:start}
.mb>.card{grid-column:1/-1}
.mb .np{grid-column:1;grid-row:1/span 2;padding:12px}.mb .hd{grid-column:2;grid-row:1;margin-top:0}.mb .qq{grid-column:2;grid-row:2}
.art{max-width:150px;margin-bottom:10px}.ti{font-size:19px}.ctl{gap:20px;margin-top:6px}
.cb{width:50px;height:50px}.cb.big{width:62px;height:62px}
#lock{align-items:flex-start}.alert{margin:10px auto}
}
</style></head><body>
<main id="main"></main>
<nav class="gl" id="nav"></nav>
<div class="ov" id="lock"><div class="alert"><div class="at"><b>Enter PIN</b><span id="msg"></span>
<input id="pin" type="password" inputmode="numeric" autocomplete="off"></div><button class="sb" id="go">Connect</button></div></div>
<script>
const $=s=>document.querySelector(s);
const E=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e};
const IC={prev:['M6 6h2v12H6zM9.5 12l9 6V6z',0],next:['M16 6h2v12h-2zM14.5 12l-9 6V6z',0],play:['M7 4.5v15l12-7.5z',0],pause:['M6.5 5h4v14h-4zM13.5 5h4v14h-4z',0],
 mute:['M4 9v6h4l5 4V5L8 9zM17 9l4 6M21 9l-4 6',1],vdn:['M4 9v6h4l5 4V5L8 9zM16.5 12h4',1],vup:['M4 9v6h4l5 4V5L8 9zM16.5 8.5a5 5 0 010 7M19 6a8.5 8.5 0 010 12',1],
 sun:['M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5',1],
 swap:['M4 7h12v10H4zM8 3h12v10',1],mon:['M3 4h18v12H3zM8 20h8M12 16v4',1],layers:['M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',1],x:['M6 6l12 12M18 6L6 18',1],
 cam:['M4 8h3l2-3h6l2 3h3v11H4zM12 17a3.5 3.5 0 110-7 3.5 3.5 0 010 7z',1],search:['M11 4a7 7 0 110 14 7 7 0 010-14zM20 20l-4-4',1],copy:['M9 9h11v11H9zM5 15V5h10',1],
 clip:['M8 4h8v3H8zM6 5H5v16h14V5h-1',1],undo:['M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3',1],star:['M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',1],
 bolt:['M13 2L4 14h7l-1 8 9-12h-7z',1],lock:['M6 11h12v9H6zM8 11V8a4 4 0 118 0v3',1],moon:['M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z',1],
 restart:['M20 12a8 8 0 11-3-6.2M20 4v5h-5',1],power:['M12 3v8M6.5 6.5a8 8 0 1011 0',1],note:['M9 18V6l10-2v12M9 18a3 3 0 11-6 0 3 3 0 016 0zM19 16a3 3 0 11-6 0 3 3 0 016 0z',1],
 grid:['M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',1],pad:['M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2zM3 14h18',1]};
const I=(n,z,c)=>{const [d,s]=IC[n];return '<svg viewBox="0 0 24 24" width="'+z+'" height="'+z+'" style="'+(c?'color:'+c:'')+'" fill="'+(s?'none':'currentColor')+'" stroke="'+(s?'currentColor':'none')+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="'+d+'"/></svg>'};
let pin='';try{pin=localStorage.getItem('pin')||''}catch(e){}
let custom=[],panels=[],caps={volume:null,bright:null,shot:false},cur='deck',gen=0;
const H=()=>({'X-Pin':pin,'Content-Type':'application/json'});
async function api(b){const r=await fetch('/api',{method:'POST',headers:H(),body:JSON.stringify(b)});
  if(r.status===401){showLock('That PIN is wrong. Try again.');throw 0}return r.json()}
const send=b=>api(b).catch(()=>{});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function showLock(m){$('#msg').textContent=m;$('#lock').style.display='flex'}
$('#go').onclick=()=>{pin=$('#pin').value.trim();try{localStorage.setItem('pin',pin)}catch(e){}$('#lock').style.display='none';boot()};

function rep(el,fn){let t1,t2;const stop=()=>{clearTimeout(t1);clearInterval(t2);el.classList.remove('hit')};
  el.addEventListener('touchstart',e=>{e.preventDefault();el.classList.add('hit');fn();t1=setTimeout(()=>{t2=setInterval(fn,110)},350)},{passive:false});
  ['touchend','touchcancel'].forEach(n=>el.addEventListener(n,stop))}
function tile(icon,label,fn,o){o=o||{};const b=E('button','t '+(o.c?'gf':'gf')+(o.cls?' '+o.cls:''));
  if(icon)b.appendChild(E('span','ib l '+(o.c||'gy'),I(icon,26)));const s=E('span');s.textContent=label;if(label)b.appendChild(s);
  if(o.rep)rep(b,fn);else b.onclick=()=>fn(b);return b}
function row(label,detail,fn,o){o=o||{};const r=E('div','row'+(o.cls?' '+o.cls:'')+(o.icon?'':' plain'));
  if(o.icon)r.appendChild(E('span','ib '+(o.c||'gy'),I(o.icon,18)));
  const a=E('span','rl');a.textContent=label;r.appendChild(a);
  if(detail){const d=E('span','rd');d.textContent=detail;r.appendChild(d)}r.classList.add('tp');onTap(r,fn);return r}
function group(m,title,rows,foot){if(title){const h=E('div','hd');h.textContent=title;m.appendChild(h)}
  const g=E('div','group gl');rows.forEach(x=>g.appendChild(x));m.appendChild(g);
  if(foot){const f=E('div','ft');f.textContent=foot;m.appendChild(f)}return g}
const fmt=k=>k.split('+').map(s=>s[0].toUpperCase()+s.slice(1)).join('+');
const mmss=ms=>{ms=Math.max(0,Math.round(ms/1000));return Math.floor(ms/60)+':'+String(ms%60).padStart(2,'0')};
const title=(m,t)=>{const h=E('h1');h.textContent=t;m.appendChild(h)};
function sheet(msg,label,fn,red){const o=E('div','ov');o.innerHTML='<div class="sheet"><div class="sg gl"><div class="st"></div><button class="sb'+(red?' red':'')+'"></button></div><button class="sb cancel">Cancel</button></div>';
  o.querySelector('.st').textContent=msg;const b=o.querySelector('.sg .sb');b.textContent=label;
  b.onclick=()=>{o.remove();fn()};o.querySelector('.cancel').onclick=()=>o.remove();o.onclick=e=>{if(e.target===o)o.remove()};document.body.appendChild(o)}
function mkBar(icon,fill,val,move,end,cls){const s=E('div','bar gl'+(cls?' '+cls:''),'<i style="background:'+fill+'"></i>'+(icon?I(icon,24,'#fff'):''));const o={el:s,val:val,drag:0};
  const set=x=>{o.val=x;s.style.setProperty('--p',(x*100)+'%')};o.set=set;set(val);let tm=0;
  const at=e=>{const r=s.getBoundingClientRect(),t=e.touches[0]||e.changedTouches[0];return Math.max(0,Math.min(1,(t.clientX-r.left)/r.width))};
  const push=()=>{if(move&&!tm)tm=setTimeout(()=>{tm=0;move(o.val)},90)};
  s.addEventListener('touchstart',e=>{e.preventDefault();o.drag=1;set(at(e));push()},{passive:false});
  s.addEventListener('touchmove',e=>{e.preventDefault();set(at(e));push()},{passive:false});
  ['touchend','touchcancel'].forEach(n=>s.addEventListener(n,()=>{o.drag=0;clearTimeout(tm);tm=0;end(o.val)}));return o}

let lastScroll=0;$('#main').addEventListener('scroll',()=>{lastScroll=Date.now()},{passive:true});
function onTap(el,fn){let x=0,y=0,t=0,mv=0;
  el.addEventListener('touchstart',e=>{const p=e.touches[0];x=p.clientX;y=p.clientY;t=Date.now();mv=Date.now()-lastScroll<150?1:0;if(!mv)el.classList.add('press')},{passive:true});
  el.addEventListener('touchmove',e=>{const p=e.touches[0];if(Math.abs(p.clientX-x)>8||Math.abs(p.clientY-y)>8){mv=1;el.classList.remove('press')}},{passive:true});
  el.addEventListener('touchend',e=>{el.classList.remove('press');if(!mv&&Date.now()-t<600&&Date.now()-lastScroll>150){e.preventDefault();fn()}});
  el.addEventListener('touchcancel',()=>{mv=1;el.classList.remove('press')})}
const view={
  deck(m){title(m,'Deck');
    const t=E('div','tiles');
    t.append(tile('prev','Previous',()=>send({a:'key',keys:'prev'}),{c:'in'}),tile('play','Play / pause',()=>send({a:'key',keys:'play'}),{c:'gr'}),tile('next','Next',()=>send({a:'key',keys:'next'}),{c:'pk'}));m.appendChild(t);
    const v=E('div','vol');
    if(caps.volume!==null){const b=mkBar('vup','linear-gradient(90deg,#5ac8fa,#0a84ff)',caps.volume,x=>send({a:'setvol',v:x}),x=>send({a:'setvol',v:x}));
      const sync=async()=>{if(b.drag)return;try{const d=await api({a:'vol'});if(d.v!=null&&!b.drag){b.set(d.v);b.el.classList.toggle('muted',!!d.m)}}catch(e){}};
      const my=gen;const poll=()=>setTimeout(()=>{if(my!==gen)return;sync();poll()},2000);poll();sync();v.appendChild(b.el);
      const mu=tile('mute','',()=>{send({a:'key',keys:'mute'});setTimeout(sync,300)},{c:'rd'});mu.style.cssText='flex:0 0 58px;height:58px;border-radius:20px';mu.firstChild.style.cssText='width:36px;height:36px';v.appendChild(mu);m.appendChild(v)}
    else{v.style.display='grid';v.style.gridTemplateColumns='repeat(3,1fr)';
      v.append(tile('vdn','Volume down',()=>send({a:'key',keys:'voldown'}),{rep:1,c:'bl'}),tile('mute','Mute',()=>send({a:'key',keys:'mute'}),{c:'rd'}),tile('vup','Volume up',()=>send({a:'key',keys:'volup'}),{rep:1,c:'bl'}));m.appendChild(v)}
    if(caps.bright!==null){const bb=mkBar('sun','linear-gradient(90deg,#ffe066,#ff9f0a)',caps.bright,x=>send({a:'setbright',v:x}),x=>send({a:'setbright',v:x}));bb.el.style.cssText+=';margin-top:10px;flex:none;width:100%';m.appendChild(bb.el)}
    const W=[['Switch app','alt+tab','swap','in'],['Show desktop','win+d','mon','bl'],['Task view','win+tab','layers','pu'],['Close window','alt+f4','x','rd'],['Screenshot','win+shift+s','cam','or'],['Search','win+s','search','tl'],['Copy','ctrl+c','copy','gy'],['Paste','ctrl+v','clip','gr'],['Undo','ctrl+z','undo','pk']];
    const wh=E('div','hd');wh.textContent='Windows';m.appendChild(wh);
    const wg=E('div','tiles');W.forEach(([l,k,i,c])=>wg.appendChild(tile(i,l,()=>send({a:'key',keys:k}),{c})));m.appendChild(wg);
    if(custom.length)group(m,'Yours',custom.map((l,i)=>row(l,'',()=>send({a:'item',i}),{icon:'star',c:'yl'})),'Edit deck.json on your PC to change these.');
  },
  pad(M){title(M,'Pad');const m=E('div','pw');M.appendChild(m);
    const p=E('div','pad gl','Drag to move the cursor.<br>Tap to click. Scroll with two fingers; tap with two fingers to right-click.');m.appendChild(p);
    const r=E('div','row2');r.append(tile(0,'Left click',()=>send({a:'click',b:'l'}),{cls:'sm'}),tile(0,'Right click',()=>send({a:'click',b:'r'}),{cls:'sm'}));m.appendChild(r);
    const t=E('div','row2');t.innerHTML='<input class="f gl" id="t" placeholder="Type on your PC" autocomplete="off" autocapitalize="off" autocorrect="off" enterkeyhint="send">';
    const s=E('button','go','Send');s.onclick=sendText;t.appendChild(s);m.appendChild(t);
    $('#t').addEventListener('keydown',e=>{if(e.key==='Enter'){$('#t').value?sendText():send({a:'key',keys:'enter'})}});
    const ks=E('div','tiles');ks.style.cssText='grid-template-columns:repeat(4,1fr);margin-top:10px';
    [['Esc','esc'],['Tab','tab'],['Delete','backspace'],['Enter','enter'],['\u2190','left'],['\u2191','up'],['\u2193','down'],['\u2192','right']].forEach(([l,k])=>ks.appendChild(tile(0,l,()=>send({a:'key',keys:k}),{cls:'sm'})));
    m.appendChild(ks);wirePad(p)},
  screen(m){title(m,'Screen');
    if(!caps.shot){group(m,'',[row('Preview needs Pillow','',()=>{},{icon:'mon',c:'gy'})],'On your PC run: pip install pillow, then restart deck.py.');return}
    let mode='l';const sg=E('div','seg gl','<button class="on">Click</button><button>Right click</button>');
    [...sg.children].forEach((b,i)=>b.onclick=()=>{mode=i?'r':'l';[...sg.children].forEach(x=>x.classList.toggle('on',x===b))});m.appendChild(sg);
    const box=E('div','shot gl');const img=E('img');img.draggable=false;box.appendChild(img);m.appendChild(box);
    box.addEventListener('click',e=>{const r=img.getBoundingClientRect();if(!r.width)return;send({a:'tap',x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height,b:mode})});
    const my=gen;(async()=>{let last='';while(my===gen){try{const r=await fetch('/shot',{method:'POST',headers:H(),body:JSON.stringify({w:720})});
      if(r.status===401){showLock('That PIN is wrong. Try again.');return}if(!r.ok)throw 0;const u=URL.createObjectURL(await r.blob());
      await new Promise(res=>{img.onload=img.onerror=res;img.src=u});if(last)URL.revokeObjectURL(last);last=u}catch(e){await sleep(1500)}await sleep(250)}})()},
  music(m){title(m,'Music');const box=E('div','mb');m.appendChild(box);const my=gen;
    let ui=null,base=0,t0=0,dur=1,playing=0,lastUri='',qsig='';
    const card=h=>{box.innerHTML='';ui=null;box.appendChild(E('div','card gl',h))};
    const setPP=()=>{ui.pp.innerHTML=I(playing?'pause':'play',34)};
    const build=()=>{box.innerHTML='';const np=E('div','np gl');ui={};
      ui.art=E('img','art');ui.ti=E('div','ti');ui.ar=E('div','ar');ui.al=E('div','al');
      ui.tl=E('span');ui.tr=E('span');
      ui.bar=mkBar('','linear-gradient(90deg,#fff,#cfe8ff)',0,v=>{ui.tl.textContent=mmss(v*dur);ui.tr.textContent='-'+mmss((1-v)*dur)},v=>{base=v*dur;t0=Date.now();send({a:'spc',op:'seek',ms:Math.round(base)})},'sk');
      const tm=E('div','tm');tm.append(ui.tl,ui.tr);
      const ctl=E('div','ctl');const cb=(ic,c,fn,big)=>{const b=E('button','cb gf '+c+(big?' big':''),I(ic,big?34:26));b.onclick=fn;return b};
      ui.pp=cb('play','gr',()=>{playing=playing?0:1;t0=Date.now();setPP();send({a:'spc',op:playing?'play':'pause'})},1);
      ctl.append(cb('prev','in',()=>send({a:'spc',op:'prev'})),ui.pp,cb('next','pk',()=>send({a:'spc',op:'next'})));
      np.append(ui.art,ui.ti,ui.ar,ui.al,E('div','',''),ui.bar.el,tm,ctl);ui.bar.el.style.marginTop='14px';box.appendChild(np);
      ui.hd=E('div','hd');ui.hd.textContent='Up next';ui.q=E('div','qq');box.append(ui.hd,ui.q)};
    const paint=d=>{
      if(!d.cfg){card('<b>Connect Spotify</b>1. Go to <code>developer.spotify.com/dashboard</code> and create an app (Web API).<br>2. Add this redirect URI: <code>http://127.0.0.1:8765/callback</code><br>3. Copy the Client ID into <code>deck.json</code> as <code>spotify_client_id</code>.<br>4. Restart <code>deck.py</code>. It opens a browser on your PC to sign in.<br><br>Spotify requires Premium for this.');return}
      if(!d.login){card('<b>Sign in to Spotify</b>On your PC, open <code>http://127.0.0.1:8765/spotify/login</code> and approve access. This page updates by itself.');return}
      if(!d.active){card('<b>Nothing playing</b>'+(d.err?d.err:'Open Spotify on your PC and play a song.'));return}
      if(!ui)build();
      if(d.uri!==lastUri){lastUri=d.uri;ui.art.src=d.img;ui.ti.textContent=d.title;ui.ar.textContent=d.artist;ui.al.textContent=d.album}
      base=d.progress;t0=Date.now();dur=d.duration||1;playing=d.playing?1:0;setPP();
      const sig=d.queue.map(x=>x.uri).join();
      if(sig!==qsig){qsig=sig;ui.q.innerHTML='';
        if(!d.queue.length){ui.hd.style.display='none'}else{ui.hd.style.display='';
          const g=E('div','group gl');d.queue.forEach((x,i)=>{const r=E('div','row plain tp');const im=E('img','th');im.src=x.img;const w=E('span','rl');
            const a=E('span','s1');a.textContent=x.title;const b=E('span','s2');b.textContent=x.artist;w.append(a,b);r.append(im,w);
            onTap(r,()=>send({a:'spc',op:'skip',n:i+1}));g.appendChild(r)});ui.q.appendChild(g);
          const f=E('div','ft');f.textContent='Tap a song to jump to it.';ui.q.appendChild(f)}}};
    (async()=>{while(my===gen){try{paint(await api({a:'sp'}));await sleep(1500)}catch(e){if(e!==0)card('<b>Can\u2019t reach the PC</b>');await sleep(3000)}}})();
    const tick=setInterval(()=>{if(my!==gen){clearInterval(tick);return}
      if(ui&&!ui.bar.drag&&playing){const p=Math.min(dur,base+Date.now()-t0);ui.bar.set(p/dur);ui.tl.textContent=mmss(p);ui.tr.textContent='-'+mmss(dur-p)}},500)},
  apps(m){title(m,'Apps');
    if(!panels.length)group(m,'',[row('No panels yet','',()=>{},{icon:'bolt',c:'gy'})],'Add "panels" to deck.json on your PC.');
    const cs=['or','pu','tl','pk','in'];panels.forEach((p,pi)=>group(m,p.name,p.buttons.map((l,i)=>row(l,'',()=>send({a:'panel',p:pi,i}),{icon:'bolt',c:cs[pi%5]})),p.note))},
  power(m){title(m,'Power');
    group(m,'',[row('Lock','',()=>send({a:'power',m:'lock'}),{icon:'lock',c:'bl'}),row('Sleep','',()=>sheet('Put the PC to sleep?','Sleep',()=>send({a:'power',m:'sleep'})),{icon:'moon',c:'in'}),
      row('Restart','',()=>sheet('Restart the PC in 5 seconds?','Restart',()=>send({a:'power',m:'restart'}),1),{icon:'restart',c:'or',cls:'red'}),
      row('Shut down','',()=>sheet('Shut down the PC in 5 seconds?','Shut down',()=>send({a:'power',m:'off'}),1),{icon:'power',c:'rd',cls:'red'})],
      'You can\u2019t wake a PC from this app once it is asleep or off.')}
};
function sendText(){const i=$('#t');if(i.value){send({a:'type',text:i.value});i.value=''}}
function wirePad(p){let last,start=0,moved=0,maxF=0,tmr=0,ax=0,ay=0,as=0;
  const pos=ts=>{let x=0,y=0;for(const t of ts){x+=t.clientX;y+=t.clientY}return{x:x/ts.length,y:y/ts.length}};
  const flush=()=>{tmr=0;const ix=Math.round(ax),iy=Math.round(ay),is=Math.round(as);ax-=ix;ay-=iy;as-=is;
    if(ix||iy)send({a:'move',dx:ix,dy:iy});if(is)send({a:'scroll',dy:is})};
  const q=()=>{if(!tmr)tmr=setTimeout(flush,25)};
  p.addEventListener('touchstart',e=>{e.preventDefault();if(e.touches.length===1){start=Date.now();moved=0;maxF=1}maxF=Math.max(maxF,e.touches.length);last=pos(e.touches)},{passive:false});
  p.addEventListener('touchmove',e=>{e.preventDefault();const c=pos(e.touches),dx=c.x-last.x,dy=c.y-last.y;moved+=Math.abs(dx)+Math.abs(dy);last=c;
    if(e.touches.length>=2)as+=dy;else{ax+=dx*1.7;ay+=dy*1.7}q()},{passive:false});
  p.addEventListener('touchend',e=>{e.preventDefault();if(e.touches.length===0){if(Date.now()-start<250&&moved<10)send({a:'click',b:maxF>=2?'r':'l'});maxF=0}else last=pos(e.touches)},{passive:false})}

const TABS=[['deck','Deck','grid','#6cc4ff'],['pad','Pad','pad','#6ee79a'],['screen','Screen','mon','#5ef0e0'],['music','Music','note','#ff8ab4'],['apps','Apps','layers','#d28cff'],['power','Power','power','#ff8a80']];
TABS.forEach(([id,l,ic,c])=>{const b=E('button','',I(ic,25,c));const s=E('span');s.textContent=l;b.appendChild(s);b.dataset.t=id;b.onclick=()=>show(id);$('#nav').appendChild(b)});
function show(t){cur=t;gen++;[...$('#nav').children].forEach(b=>b.classList.toggle('on',b.dataset.t===t));const m=$('#main');m.innerHTML='';m.scrollTop=0;view[t](m)}
document.addEventListener('touchstart',()=>{},true);
document.addEventListener('touchmove',e=>{if(!e.target.closest('main'))e.preventDefault()},{passive:false});
async function boot(){try{const d=await api({a:'deck'});custom=d.buttons||[];panels=d.panels||[];caps={volume:d.volume,bright:d.bright,shot:!!d.shot};show(cur)}
  catch(e){if(e!==0)showLock('Can\u2019t reach the PC. Check that deck.py is running and both devices are on the same Wi-Fi.')}}
if(!pin)showLock('Shown in the window running deck.py on your PC.');else boot();
</script></body></html>
"""

if __name__ == "__main__":
    ip = lan_ip()
    print("\n  Control Deck is running")
    print("  Open on the phone:  http://%s:%d" % (ip, PORT))
    print("  PIN:                %s   (change it in deck.json)\n" % CFG["pin"])
    print("  Screen preview:     %s" % ("on" if HAVE_PIL else "off (pip install pillow)"))
    print("  Volume slider:      %s" % ("on" if get_volume() is not None else "off (pip install pycaw)"))
    print("  Brightness:         %s" % ("on" if get_brightness() is not None else "off (needs a laptop display)"))
    if not CFG.get("spotify_client_id"):
        print("  Spotify:            off (see the Music tab on the phone for setup)")
    elif not sp_token():
        print("  Spotify:            signing in... a browser will open on this PC")
        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:%d/spotify/login" % PORT)).start()
    else:
        print("  Spotify:            connected")
    print("\n  Allow Python through Windows Firewall on Private networks if prompted.")
    print("  Press Ctrl+C to stop.\n")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
