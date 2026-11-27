# Windows-Control-Deck
Python-based, web-controlled Windows PC remote that allows users to control their computer from a phone through a browser.

# 🎮 Control Deck

**Turn your smartphone into a wireless remote control for your Windows PC.**

Control Deck is a lightweight, browser-based remote control application built with Python, HTML, CSS, and JavaScript.

It allows you to control essential Windows functions, manage media playback, control your mouse and keyboard, adjust system settings, and launch applications directly from your smartphone.

No mobile application installation is required. Simply run Control Deck on your Windows PC and access its interface using a browser on your phone.

## ✨ Features

### 1. Deck – Quick Controls
- Media playback controls (Previous, Play/Pause, Next).
- System volume control.
- Screen brightness control (supported laptop displays).
- Windows shortcuts:
  - Switch applications.
  - Show desktop.
  - Task View.
  - Close active window.
  - Screenshot.
  - Search.
  - Copy, Paste, and Undo.
- Custom application shortcuts.

### 2. Pad – Wireless Touchpad
- Move the PC cursor using your smartphone.
- Left-click and right-click.
- Two-finger scrolling.
- Type text directly from your phone.
- Keyboard shortcuts and navigation keys.

### 3. Screen – Live Screen Preview
- View your PC screen remotely.
- Click directly on the screen preview.
- Switch between left-click and right-click.
- Requires the optional Pillow dependency.

### 4. Music – Spotify Controller
- Play and pause Spotify.
- Skip to previous or next track.
- View currently playing track information.
- View album artwork and upcoming tracks.
- Seek through the current track.
- Spotify Web API integration using OAuth PKCE.

**Requirements:** Spotify Developer application and Spotify Premium.

### 5. Apps – Custom Application Panels
- Create custom application control panels.
- Add custom keyboard shortcuts.
- Launch applications using configurable buttons.
- Includes a default OBS Studio panel.

### 6. Power – Windows Power Controls
- Lock the PC.
- Put the PC to sleep.
- Restart the PC.
- Shut down the PC.

Power operations include confirmation prompts where applicable.

---

## 🖥️ System Requirements

| Requirement | Details |
|---|---|
| Operating System | Windows 10 / Windows 11 |
| Python | Python 3.8 or newer |
| Smartphone | Android or iPhone |
| Browser | Safari, Chrome, or another modern browser |
| Network | PC and smartphone connected to the same Wi-Fi/LAN |
| Installation | No mobile app installation required |

**Note:** The application runs on Windows because it uses Windows-specific APIs for keyboard, mouse, brightness, volume, and power operations.

---

## 📦 Dependencies

Control Deck uses Python's standard library for its core functionality.

### Core dependencies

No third-party Python packages are required for basic operation.

The application uses built-in Python modules, including:

- `http.server`
- `socket`
- `threading`
- `subprocess`
- `ctypes`
- `json`
- `urllib`
- `hashlib`
- `hmac`
- `secrets`
- `webbrowser`

### Optional dependencies

| Package | Purpose | Required? |
|---|---|---|
| Pillow | Live screen capture and preview | Optional |
| pycaw | Windows system volume slider | Optional |
| comtypes | Windows audio interface support | Installed as a pycaw dependency |

Install optional dependencies:

```powershell
pip install -r requirements-optional.txt
```

Alternatively, install features individually:

```powershell
pip install pillow
pip install pycaw
```

Without Pillow, the Screen tab's live preview is unavailable.

Without pycaw, volume can still be controlled using Volume Up, Volume Down, and Mute buttons.

---

## 🚀 Installation and Setup

### Step 1: Install Python

Download and install Python 3.8 or newer:

https://www.python.org/downloads/

During installation, enable:

`Add Python to PATH`

Verify the installation:

```powershell
python --version
```

### Step 2: Download Control Deck

Clone the repository:

```powershell
git clone https://github.com/YOUR-USERNAME/Control-Deck.git
```

Navigate into the project directory:

```powershell
cd Control-Deck
```

Alternatively, download the repository as a ZIP file and extract it.

### Step 3: Install optional dependencies

For all optional features:

```powershell
pip install -r requirements-optional.txt
```

### Step 4: Start Control Deck

Run:

```powershell
python deck.py
```

On successful startup, the terminal displays:

- Control Deck status.
- PC's local IP address.
- Access PIN.
- Screen preview availability.
- Volume slider availability.
- Brightness availability.
- Spotify connection status.

### Step 5: Connect your smartphone

1. Connect your smartphone and Windows PC to the same Wi-Fi network.
2. Open Safari or Chrome on your smartphone.
3. Enter the URL displayed in the PC terminal.

Example:

```text
http://192.168.1.100:8765
```

4. Enter the PIN displayed in the terminal.
5. Start controlling your PC.

**Important:** Allow Python through Windows Defender Firewall on Private networks if prompted.

---

## ⚙️ Configuration

Control Deck automatically creates a `deck.json` configuration file on its first run.

You can edit this file to customize the PIN, application shortcuts, and control panels.

### Example configuration

```json
{
    "pin": "1234",
    "buttons": [
        {
            "label": "Calculator",
            "run": "calc"
        },
        {
            "label": "Notepad",
            "run": "notepad"
        },
        {
            "label": "File Explorer",
            "run": "explorer"
        },
        {
            "label": "Task Manager",
            "keys": "ctrl+shift+esc"
        }
    ],
    "panels": [
        {
            "name": "OBS Studio",
            "note": "Configure matching hotkeys in OBS.",
            "buttons": [
                {
                    "label": "Start Recording",
                    "keys": "ctrl+alt+r"
                },
                {
                    "label": "Start Streaming",
                    "keys": "ctrl+alt+s"
                },
                {
                    "label": "Mute Microphone",
                    "keys": "ctrl+alt+m"
                },
                {
                    "label": "Next Scene",
                    "keys": "ctrl+alt+right"
                }
            ]
        }
    ],
    "spotify_client_id": ""
}
```

Restart Control Deck after modifying the configuration.

### Adding custom keyboard shortcuts

Use the `keys` property:

```json
{
    "label": "Switch Window",
    "keys": "alt+tab"
}
```

Supported keys include:

- `ctrl`
- `shift`
- `alt`
- `win`
- `enter`
- `esc`
- `tab`
- `backspace`
- `delete`
- `left`, `right`, `up`, `down`
- `f1` through `f12`
- Alphanumeric keys

Multiple keys can be combined using `+`.

### Adding custom applications

Use the `run` property:

```json
{
    "label": "Launch Notepad",
    "run": "notepad"
}
```

The application command must be available on the Windows PC.

---

## 🎵 Spotify Integration

Spotify integration is optional.

To enable it:

1. Visit https://developer.spotify.com/dashboard
2. Create a Spotify Developer application.
3. Enable the Spotify Web API.
4. Add the following Redirect URI to your application:

```text
http://127.0.0.1:8765/callback
```

5. Copy the Spotify Client ID.
6. Add it to `deck.json`:

```json
"spotify_client_id": "YOUR_SPOTIFY_CLIENT_ID"
```

7. Restart Control Deck.
8. Open the Music tab and complete Spotify authorization on your PC.

The application uses OAuth PKCE authentication.

Spotify Premium is required for playback control.

---

## 🔐 Security Considerations

Control Deck can send keyboard and mouse input, launch applications, and perform power operations on the Windows PC.

Please follow these precautions:

- Use Control Deck only on a trusted private network.
- Change the default generated PIN to a strong, non-obvious PIN.
- Do not expose port `8765` directly to the public internet.
- Do not configure public router port forwarding.
- Do not share your `deck.json` or `spotify_token.json` files.
- Review custom application commands before adding them.
- Stop the application when it is not required.

**Security limitation:** The current implementation uses HTTP rather than HTTPS and a four-digit PIN. The PIN is not equivalent to production-grade authentication. Use only on trusted networks. Additional security hardening is recommended before broader deployment.

---

## 🛠️ Troubleshooting

| Issue | Suggested solution |
|---|---|
| Phone cannot connect | Ensure both devices are on the same network |
| Connection refused | Check whether `deck.py` is running |
| Incorrect PIN | Check the PIN displayed in the terminal |
| Screen preview unavailable | Install Pillow and restart the application |
| Volume slider unavailable | Install pycaw and restart |
| Brightness unavailable | Check laptop display and Windows WMI support |
| Spotify not connecting | Verify Client ID and redirect URI |
| OBS buttons not working | Configure matching hotkeys in OBS |
| Firewall blocks access | Allow Python on the Windows Private network profile |

---

## ⏹️ Stopping Control Deck

Go to the terminal where Control Deck is running and press:

```text
Ctrl + C
```

The local web server will stop.

---

## 🧰 Technology Stack

- Python – Backend server and Windows system integration.
- HTML – Web interface structure.
- CSS – Responsive interface and styling.
- JavaScript – Frontend interactions and API communication.
- Windows API – Keyboard, mouse, audio, display, and power controls.
- Spotify Web API – Optional music integration.

---

## 🤝 Contributing

Contributions, suggestions, bug reports, and feature requests are welcome.

1. Fork this repository.
2. Create a feature branch.
3. Make your changes.
4. Test the application.
5. Submit a Pull Request.

---

## 📄 License

See the LICENSE file for usage and distribution terms.

---

**Control Deck – Your PC, in the palm of your hand.**
