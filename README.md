# SwiftShare — LAN File & Clipboard Sharing

Share files and clipboard text between two PCs/Laptops on the **same Wi-Fi or Ethernet network** — no internet required, no accounts, no cloud.

---

## Requirements

- Python 3.10+
- Both computers on the **same local network**

---

## Quick Start

### 1. Install dependencies

```bash
pip install customtkinter pyperclip
```

### 2. Run on BOTH computers

```bash
python SwiftShare.py
```

---

## How to Use

### Connecting to a Peer

**Option A — Auto Discovery (recommended)**
1. Click **Scan Network** on both PCs
2. Your peer appears in the sidebar
3. Click **Select** to make them the active peer

**Option B — Manual IP**
1. Find the other PC's IP (shown in the SwiftShare header, or `ipconfig` / `ifconfig`)
2. Enter it in the "Manual Connect" box
3. Press Enter or click **Connect**

---

### Sending a File

1. Select your peer (sidebar)
2. Go to **📁 Files** tab
3. Click **Browse** → pick any file
4. Click **▶ Send**

The file is saved to `~/SwiftShare_Downloads/` on the recipient's PC. A progress bar tracks the transfer.

---

### Sharing Clipboard

1. Select your peer
2. Go to **📋 Clipboard** tab
3. Click **Paste from Clipboard** (reads your system clipboard) — or type/paste manually
4. Click **▶ Send**

The text is instantly received on the other PC and auto-copied to their clipboard (if pyperclip is installed). Click **📋 Copy** in the received section to copy it manually.

---

## Security Notes

- Works **LAN-only** — no data leaves your local network
- No encryption — suitable for trusted home/office networks
- Firewall: allow **TCP port 57832** and **UDP port 57833** if transfers fail

---

## Firewall Setup (if needed)

**Windows:**
```
netsh advfirewall firewall add rule name="SwiftShare" dir=in action=allow protocol=TCP localport=57832
netsh advfirewall firewall add rule name="SwiftShare-UDP" dir=in action=allow protocol=UDP localport=57833
```

---

## File Save Location

Received files are saved to:
```
~/SwiftShare_Downloads/
```
Click **📂 Open Folder** in the sidebar to open it directly.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Peers not discovered | Use Manual Connect with the other PC's IP |
| Transfer hangs | Check firewall — allow TCP 57832 |
| "pyperclip not installed" | `pip install pyperclip` |
| Port already in use | Close other instances of SwiftShare |
