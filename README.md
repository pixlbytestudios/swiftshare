# SwiftShare

Fast, end-to-end encrypted LAN file sharing, chat, and voice notes — no internet, no accounts, no cloud.

**PixlByte Studios** • Offline-first • LAN-based • Zero-telemetry

---

## Features

- 🔒 **End-to-end encryption** — X25519 ECDH key exchange + Fernet for every message and file
- 📁 **Reliable file transfers** — pause, cancel, and resume, backed by SQLite persistence
- 💬 **Familiar chat UI** — WhatsApp-style bubbles for LAN messaging
- 🎙️ **Voice notes** — tap-to-record with pause/discard/send, and accurate pause/resume playback
- 🔍 **Auto peer discovery** — finds other SwiftShare devices on your network, no setup required
- 🔔 **Smart notifications** — native Windows alerts via winotify, gated so you're not spammed while the app is focused
- ❤️ **Background health checks** — keeps peer connections alive and reports status live

## Download

Grab the latest build from the [Releases page](https://github.com/pixlbytestudios/swiftshare/releases/latest) — single `.exe`, no install required.

| Platform | File |
|---|---|
| Windows 10/11 | `SwiftShare-v3.0-win64.exe` |

## How It Works

SwiftShare runs a lightweight peer service on each device on your local network. Devices discover each other automatically over LAN broadcast, then negotiate a shared key via X25519 ECDH before any message, file, or voice note is exchanged — everything stays encrypted end-to-end and never touches an external server. If LAN/Wi-Fi isn't available, it falls back to a direct Bluetooth RFCOMM connection.

## Tech Stack

- Python 3.13
- CustomTkinter (desktop UI)
- Kivy / KivyMD (Android client)
- `cryptography` (X25519 ECDH, Fernet)
- SQLite (transfer state persistence)
- winrt (Bluetooth RFCOMM fallback)
- winotify (native notifications)
- PyInstaller (onefile Windows packaging)

## Build From Source

```bash
git clone https://github.com/pixlbytestudios/swiftshare.git
cd swiftshare
pip install -r requirements.txt
python main.py
```

Build the Windows executable:

```bash
pyinstaller swiftshare.spec
```

## Version History

- **v3.0** — End-to-end encryption, Bluetooth fallback, Android client, background health checks
- **v2.0** — Chat bubbles, voice notes, transfer resume
- **v1.0** — Initial LAN file sharing (LANShare)

## License

MIT — see [LICENSE](LICENSE).

## Author

**Imisioluwa** — PixlByte Studios
