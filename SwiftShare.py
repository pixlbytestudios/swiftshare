#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   SwiftShare v2.0  —  LAN File, Clipboard, Chat & Voice Notes   ║
║   Share files, chat, and voice notes between PCs on the LAN     ║
╚══════════════════════════════════════════════════════════════════╝

Requirements:
    pip install customtkinter pyperclip pyaudio pillow pystray

Usage:
    Run this script on BOTH computers.
    Click "Scan" to discover peers, select one, then chat!

New in v2.0:
    ✦ Real-time LAN chat with per-peer history
    ✦ WhatsApp-style voice notes (hold to record, release to send)
    ✦ SQLite chat database (auto-expires after 7 days)
    ✦ System tray background mode + desktop notifications
    ✦ Unread badge counters in sidebar
"""

import os
import sys
import json
import socket
import struct
import threading
import time
import queue
import subprocess
import sqlite3
import base64
import tempfile
import wave
import io
import signal
import zipfile
from pathlib import Path
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import winsound
except Exception:
    winsound = None

try:
    import tkinter.dnd as tkdnd
    from tkinter import DND_FILES
    HAS_DND = True
except Exception:
    HAS_DND = False


def resource_path(relative: str) -> Path:
    """
    Resolve path to a bundled resource in both normal and PyInstaller-frozen mode.
    Normal run  : relative to this script's directory.
    Frozen (.exe): relative to sys._MEIPASS (the temp extraction folder).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative

# ── Optional imports ─────────────────────────────────────────────────────────
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

try:
    import pyperclip
    HAS_CLIP = True
except ImportError:
    HAS_CLIP = False

try:
    import pyaudio
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    # Pre-import the platform backend so PyInstaller bundles it AND so that
    # the first real import of pystray doesn't have to discover it at runtime.
    # This is the inline hidden-import hint the .spec also declares.
    if sys.platform == "win32":
        try:
            import pystray._win32   # noqa: F401  — must come BEFORE `import pystray`
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import pystray._darwin  # noqa: F401
        except Exception:
            pass
    else:
        try:
            import pystray._xorg    # noqa: F401
        except Exception:
            pass
    import pystray
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

# ── Check customtkinter ───────────────────────────────────────────────────────
if not HAS_CTK:
    import tkinter.messagebox as mb
    root = tk.Tk()
    root.withdraw()
    mb.showerror(
        "Missing Dependency",
        "Please install required packages first:\n\n"
        "    pip install customtkinter pyperclip pyaudio pillow pystray\n\n"
        "Then re-run the script.",
    )
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
APP_NAME    = "SwiftShare"
APP_VERSION = "2.0.0"
BUILD_DATE  = datetime.now().strftime("%Y-%m-%d")
STUDIO = "PixlByte Studios"
TCP_PORT    = 57832
UDP_PORT    = 57833
CHAT_PORT   = 57834          # dedicated chat channel
CHUNK_SIZE  = 16384
SAVE_DIR    = Path.home() / "SwiftShare_Downloads"
SAVE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH     = Path.home() / ".swiftshare_chat.db"
AUDIO_RATE  = 16000
AUDIO_CHUNK = 1024
AUDIO_CHANNELS = 1
CHAT_EXPIRE_DAYS = 7

# ── Design tokens ──────────────────────────────────────────────────────────────
C = {
    "bg"       : "#0d0d14",
    "panel"    : "#13131f",
    "card"     : "#1a1a2e",
    "card2"    : "#1f1f35",
    "border"   : "#2a2a45",
    "accent"   : "#6c63ff",
    "accent2"  : "#ff6584",
    "success"  : "#00d4aa",
    "warning"  : "#ffb347",
    "error"    : "#ff4d6d",
    "text"     : "#e8e8f0",
    "sub"      : "#6b6b8a",
    "dim"      : "#3d3d5c",
    "online"   : "#00d4aa",
    # Chat bubble colors — WhatsApp warmth + Telegram depth + Snapchat pop
    "bubble_me"       : "#1e3a5f",    # deep teal-navy sent bubble
    "bubble_them"     : "#1c1c2e",    # deep slate received bubble
    "bubble_me_text"  : "#d6eeff",    # icy blue-white for sent text
    "bubble_them_text": "#e8e8f0",    # soft white for received text
    "chat_bg"         : "#0a0a12",    # chat message area bg
    "chat_input_bg"   : "#141420",    # input bar background
    "chat_hdr"        : "#0f0f1e",    # chat header bg
    "voice_btn"       : "#ff6584",
    "voice_rec"       : "#ff2244",
    "voice_wave_me"   : "#4dabf7",    # waveform color for my voice notes
    "voice_wave_them" : "#6c63ff",    # waveform color for their voice notes
    "avatar_me"       : "#6c63ff",    # avatar bg for self
    "avatar_them"     : "#ff6584",    # avatar bg for peer
    "pill_border_me"  : "#2a4a7a",    # bubble border for sent
    "pill_border_them": "#2a2a4a",    # bubble border for received
    "tick_read"       : "#4c9bff",
}

# ═══════════════════════════════════════════════════════════════════════════════
# Database — Chat Storage
# ═══════════════════════════════════════════════════════════════════════════════

class ChatDB:
    """SQLite-backed message store. Travels with the user, auto-expires old rows."""

    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        with self._connect() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_ip    TEXT    NOT NULL,
                    peer_name  TEXT    NOT NULL,
                    direction  TEXT    NOT NULL,   -- 'sent' | 'recv'
                    mtype      TEXT    NOT NULL,   -- 'text' | 'voice'
                    content    TEXT,               -- text body or base64 wav
                    duration   REAL,               -- voice seconds
                    status     TEXT DEFAULT 'sent',
                    reactions  TEXT DEFAULT '',
                    reply_to   TEXT DEFAULT '',
                    ts         REAL    NOT NULL
                )
            """)
            cx.execute("CREATE INDEX IF NOT EXISTS idx_peer ON messages (peer_ip, ts)")
            existing = [row[1] for row in cx.execute("PRAGMA table_info(messages)").fetchall()]
            if "status" not in existing:
                cx.execute("ALTER TABLE messages ADD COLUMN status TEXT DEFAULT 'sent'")
            if "reactions" not in existing:
                cx.execute("ALTER TABLE messages ADD COLUMN reactions TEXT DEFAULT ''")
            if "reply_to" not in existing:
                cx.execute("ALTER TABLE messages ADD COLUMN reply_to TEXT DEFAULT ''")

    def _connect(self):
        return sqlite3.connect(str(self.path), check_same_thread=False)

    def add(self, peer_ip: str, peer_name: str, direction: str,
            mtype: str, content: str, duration: float = 0.0,
            status: str = "sent", reactions: str = "", reply_to: str = "") -> int:
        with self._lock:
            with self._connect() as cx:
                cur = cx.execute(
                    "INSERT INTO messages (peer_ip,peer_name,direction,mtype,content,duration,status,reactions,reply_to,ts)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (peer_ip, peer_name, direction, mtype, content, duration,
                     status, reactions, reply_to, time.time())
                )
                return cur.lastrowid

    def get_history(self, peer_ip: str, limit: int = 200) -> list:
        with self._lock:
            with self._connect() as cx:
                rows = cx.execute(
                    "SELECT id,peer_ip,peer_name,direction,mtype,content,duration,status,reactions,reply_to,ts"
                    " FROM messages WHERE peer_ip=? ORDER BY ts DESC LIMIT ?",
                    (peer_ip, limit)
                ).fetchall()
        return list(reversed(rows))

    def update_status(self, msg_id: int, status: str):
        with self._lock:
            with self._connect() as cx:
                cx.execute("UPDATE messages SET status=? WHERE id=?", (status, msg_id))

    def update_reactions(self, msg_id: int, reactions: str):
        with self._lock:
            with self._connect() as cx:
                cx.execute("UPDATE messages SET reactions=? WHERE id=?", (reactions, msg_id))

    def update_reply(self, msg_id: int, reply_to: str):
        with self._lock:
            with self._connect() as cx:
                cx.execute("UPDATE messages SET reply_to=? WHERE id=?", (reply_to, msg_id))

    def expire_old(self):
        cutoff = time.time() - CHAT_EXPIRE_DAYS * 86400
        with self._lock:
            with self._connect() as cx:
                cx.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))

    def delete(self, msg_id: int):
        with self._lock:
            with self._connect() as cx:
                cx.execute("DELETE FROM messages WHERE id=?", (msg_id,))

    def unread_count(self, peer_ip: str, since: float) -> int:
        with self._lock:
            with self._connect() as cx:
                row = cx.execute(
                    "SELECT COUNT(*) FROM messages WHERE peer_ip=? AND direction='recv' AND ts>?",
                    (peer_ip, since)
                ).fetchone()
        return row[0] if row else 0


# ═══════════════════════════════════════════════════════════════════════════════
# Notification helper
# ═══════════════════════════════════════════════════════════════════════════════

def show_notification(title: str, message: str):
    """
    Cross-platform desktop notification (best-effort).

    Windows: Uses a PowerShell script that:
      - Creates the NotifyIcon, shows the balloon for 4 s
      - Then explicitly hides and disposes the icon so it does NOT linger
        in the tray after the balloon is dismissed.
      - Runs in a hidden window on a daemon thread so it never blocks.

    macOS  : osascript display notification (auto-dismissed by OS).
    Linux  : notify-send with -t 4000 (auto-dismissed by notification daemon).
    """
    def _fire():
        try:
            if sys.platform == "win32":
                # Sanitise strings for embedding in PowerShell
                safe_title = title.replace("'", "''").replace('"', '`"')
                safe_msg   = message.replace("'", "''").replace('"', '`"')
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "Add-Type -AssemblyName System.Drawing; "
                    "$n = New-Object System.Windows.Forms.NotifyIcon; "
                    "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                    "$n.BalloonTipIcon  = [System.Windows.Forms.ToolTipIcon]::Info; "
                    f"$n.BalloonTipTitle = '{safe_title}'; "
                    f"$n.BalloonTipText  = '{safe_msg}'; "
                    "$n.Visible = $True; "
                    "$n.ShowBalloonTip(2000); "
                    "Start-Sleep -Milliseconds 1000; "   # wait for balloon to expire
                    "$n.Visible = $False; "              # remove from tray — no lingering icon
                    "$n.Dispose()"
                )
                subprocess.Popen(
                    ["powershell", "-WindowStyle", "Hidden", "-NonInteractive",
                     "-NoProfile", "-Command", ps],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "darwin":
                safe_title = title.replace('"', '\\"')
                safe_msg   = message.replace('"', '\\"')
                subprocess.Popen(
                    ["osascript", "-e",
                     f'display notification "{safe_msg}" with title "{safe_title}"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                # Linux — notify-send; -t in ms, auto-dismissed by the daemon
                subprocess.Popen(
                    ["notify-send", "-t", "4000", "-a", APP_NAME, title, message],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    # Run in a daemon thread — never block the UI thread waiting for PowerShell
    threading.Thread(target=_fire, daemon=True, name="notify").start()


# ═══════════════════════════════════════════════════════════════════════════════
# Audio helpers — WhatsApp-style voice notes
# ═══════════════════════════════════════════════════════════════════════════════

class VoiceRecorder:
    """Record mic audio into a WAV buffer. Press-and-hold style."""

    def __init__(self):
        self._pa     = None
        self._stream = None
        self._frames = []
        self._running = False
        self._lock   = threading.Lock()

    def _get_pa(self):
        if self._pa is None and HAS_AUDIO:
            self._pa = pyaudio.PyAudio()
        return self._pa

    def start(self):
        pa = self._get_pa()
        if pa is None:
            return False
        self._frames = []
        self._running = True
        self._stream = pa.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            frames_per_buffer=AUDIO_CHUNK,
        )
        threading.Thread(target=self._record_loop, daemon=True).start()
        return True

    def _record_loop(self):
        while self._running:
            try:
                data = self._stream.read(AUDIO_CHUNK, exception_on_overflow=False)
                with self._lock:
                    self._frames.append(data)
            except Exception:
                break

    def stop(self) -> bytes | None:
        """Stop recording, return WAV bytes or None."""
        self._running = False
        time.sleep(0.1)
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None
        except Exception:
            pass

        with self._lock:
            frames = list(self._frames)
        if not frames:
            return None

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(AUDIO_CHANNELS)
            wf.setsampwidth(2)   # paInt16 = 2 bytes
            wf.setframerate(AUDIO_RATE)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    def duration(self) -> float:
        with self._lock:
            samples = len(self._frames) * AUDIO_CHUNK
        return samples / AUDIO_RATE


class VoicePlayer:
    """Play a WAV bytes buffer asynchronously."""

    @staticmethod
    def play(wav_bytes: bytes):
        if not HAS_AUDIO:
            messagebox.showwarning(APP_NAME, "pyaudio not installed.\npip install pyaudio")
            return
        # Copy bytes so the closure is independent of caller's buffer
        data_copy = bytes(wav_bytes)
        threading.Thread(target=VoicePlayer._play_thread,
                         args=(data_copy,), daemon=True).start()

    @staticmethod
    def _play_thread(wav_bytes: bytes):
        pa = None
        stream = None
        try:
            pa  = pyaudio.PyAudio()
            buf = io.BytesIO(wav_bytes)
            wf  = wave.open(buf, "rb")
            stream = pa.open(
                format=pa.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
            )
            # readframes takes a FRAME count, not byte count
            chunk_frames = 1024
            data = wf.readframes(chunk_frames)
            while data:
                stream.write(data)
                data = wf.readframes(chunk_frames)
            wf.close()
        except Exception as e:
            print(f"[VoicePlayer] playback error: {e}")
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                if pa:
                    pa.terminate()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Network helpers (unchanged from v1)
# ═══════════════════════════════════════════════════════════════════════════════

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed unexpectedly")
        buf.extend(chunk)
    return bytes(buf)


def send_msg(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def recv_msg(sock: socket.socket) -> dict:
    header = recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    if length > 64 * 1024 * 1024:
        raise ValueError(f"Message too large: {length} bytes")
    raw = recv_exact(sock, length)
    return json.loads(raw.decode("utf-8"))


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def open_path(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror(APP_NAME, f"Cannot open path:\n{e}")


def play_notification_sound(kind: str) -> None:
    try:
        if winsound is not None and sys.platform == "win32":
            if kind == "message":
                winsound.Beep(880, 80)
            elif kind == "file":
                winsound.Beep(660, 120)
            elif kind == "voice":
                winsound.Beep(1040, 90)
            else:
                winsound.Beep(800, 60)
        else:
            # Fallback simple audible cue
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def _zip_folder(folder_path: str) -> str:
    folder = Path(folder_path)
    if not folder.is_dir():
        raise ValueError("Not a folder")
    temp_file = Path(tempfile.gettempdir()) / f"{folder.name}.zip"
    with zipfile.ZipFile(temp_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder):
            for filename in files:
                full = Path(root) / filename
                zf.write(full, full.relative_to(folder.parent))
    return str(temp_file)


def do_send_file(ip: str, filepath: str, progress_cb=None) -> bool:
    p    = Path(filepath)
    size = p.stat().st_size
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((ip, TCP_PORT))
    sock.settimeout(15)
    send_msg(sock, {"type": "file_start", "name": p.name, "size": size})
    ack = recv_msg(sock)
    if ack.get("type") != "ack":
        sock.close()
        raise RuntimeError("Peer rejected the file transfer")
    sent = 0
    with open(p, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)
            sent += len(chunk)
            if progress_cb:
                progress_cb(sent, size)
    fin = recv_msg(sock)
    sock.close()
    return fin.get("type") == "done"


def do_send_clipboard(ip: str, text: str) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((ip, TCP_PORT))
    sock.settimeout(None)
    send_msg(sock, {"type": "clipboard", "content": text})
    sock.close()


def do_ping(ip: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    sock.connect((ip, TCP_PORT))
    sock.settimeout(None)
    send_msg(sock, {"type": "ping"})
    resp = recv_msg(sock)
    sock.close()
    return resp.get("host", ip)


def do_send_chat(ip: str, payload: dict) -> None:
    """Send a chat message (text, voice, or delete) to peer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((ip, CHAT_PORT))
    sock.settimeout(None)
    send_msg(sock, payload)
    sock.close()


# Global singleton voice player — ensures only one plays at a time
class _VoicePlayerManager:
    """Singleton that manages one playing stream at a time."""
    def __init__(self):
        self._lock      = threading.Lock()
        self._stop_evt  = threading.Event()
        self._active_id: str | None = None     # id of currently playing bubble
        self._on_state  = None                 # callback(id, state) where state in play|pause|stop

    def set_state_cb(self, cb):
        self._on_state = cb

    def _notify(self, vid: str, state: str):
        if self._on_state:
            try:
                self._on_state(vid, state)
            except Exception:
                pass

    def play(self, vid: str, wav_bytes: bytes):
        """Start or resume playing voice note with id `vid`."""
        with self._lock:
            if self._active_id == vid:
                return   # already playing this one
            # Stop whatever is currently playing
            self._stop_evt.set()
            self._active_id = vid
            self._stop_evt  = threading.Event()
            stop_evt = self._stop_evt

        self._notify(vid, "play")
        data_copy = bytes(wav_bytes)
        threading.Thread(target=self._thread, args=(vid, data_copy, stop_evt),
                         daemon=True).start()

    def stop(self, vid: str | None = None):
        """Stop playback. If vid given, only stops that bubble."""
        with self._lock:
            if vid is not None and self._active_id != vid:
                return
            self._stop_evt.set()
            stopped_id      = self._active_id
            self._active_id = None
        if stopped_id:
            self._notify(stopped_id, "stop")

    def _thread(self, vid: str, wav_bytes: bytes, stop_evt: threading.Event):
        pa = stream = None
        try:
            if not HAS_AUDIO:
                return
            pa  = pyaudio.PyAudio()
            buf = io.BytesIO(wav_bytes)
            wf  = wave.open(buf, "rb")
            stream = pa.open(
                format=pa.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
            )
            chunk_frames = 1024
            data = wf.readframes(chunk_frames)
            while data and not stop_evt.is_set():
                stream.write(data)
                data = wf.readframes(chunk_frames)
            wf.close()
        except Exception as e:
            print(f"[VoicePlayer] error: {e}")
        finally:
            try:
                if stream:
                    stream.stop_stream(); stream.close()
            except Exception: pass
            try:
                if pa: pa.terminate()
            except Exception: pass
            with self._lock:
                if self._active_id == vid:
                    self._active_id = None
            self._notify(vid, "stop")


VOICE_PLAYER = _VoicePlayerManager()


def _safe_dest(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    n = 1
    while dest.exists():
        stem = Path(name).stem
        suf  = Path(name).suffix
        dest = dest_dir / f"{stem}_{n}{suf}"
        n += 1
    return dest


# ═══════════════════════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════════════════════

class Server:
    """TCP (files/clipboard) + TCP (chat) + UDP (discovery) servers."""

    def __init__(self, evt_queue: queue.Queue):
        self.q     = evt_queue
        self.alive = False
        self._tcp  = None
        self._chat = None

    def start(self):
        self.alive = True
        threading.Thread(target=self._tcp_loop,  daemon=True, name="tcp-server").start()
        threading.Thread(target=self._chat_loop, daemon=True, name="chat-server").start()
        threading.Thread(target=self._udp_loop,  daemon=True, name="udp-server").start()

    def stop(self):
        self.alive = False
        for s in (self._tcp, self._chat):
            try:
                if s: s.close()
            except Exception:
                pass

    # ── TCP file/clipboard server ─────────────────────────────────────────────
    def _tcp_loop(self):
        try:
            self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcp.bind(("", TCP_PORT))
            self._tcp.listen(10)
            self._tcp.settimeout(1.0)
            self.q.put(("server_ready", TCP_PORT))
            while self.alive:
                try:
                    conn, addr = self._tcp.accept()
                    threading.Thread(target=self._handle_file_conn,
                                     args=(conn, addr[0]), daemon=True).start()
                except socket.timeout:
                    pass
        except Exception as e:
            if self.alive:
                self.q.put(("log", f"TCP server crashed: {e}", "error"))
        finally:
            try: self._tcp.close()
            except Exception: pass

    def _handle_file_conn(self, conn: socket.socket, ip: str):
        try:
            conn.settimeout(60)
            msg   = recv_msg(conn)
            mtype = msg.get("type")

            if mtype == "ping":
                send_msg(conn, {"type": "pong", "host": socket.gethostname()})

            elif mtype == "clipboard":
                content = msg.get("content", "")
                self.q.put(("clip_recv", ip, content, time.time()))

            elif mtype == "file_start":
                name = msg["name"]
                size = msg["size"]
                self.q.put(("file_recv_start", ip, name, size))
                dest = _safe_dest(SAVE_DIR, name)
                send_msg(conn, {"type": "ack"})
                received = 0
                with open(dest, "wb") as f:
                    while received < size:
                        conn.settimeout(10)
                        chunk = conn.recv(min(CHUNK_SIZE, size - received))
                        if not chunk:
                            raise ConnectionError("Connection lost during transfer")
                        f.write(chunk)
                        received += len(chunk)
                        self.q.put(("recv_progress", str(dest), received, size))
                send_msg(conn, {"type": "done"})
                self.q.put(("file_recv_done", ip, str(dest), name, received == size))
        except Exception as e:
            self.q.put(("log", f"Error handling {ip}: {e}", "error"))
        finally:
            try: conn.close()
            except Exception: pass

    # ── Chat server ───────────────────────────────────────────────────────────
    def _chat_loop(self):
        try:
            self._chat = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._chat.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._chat.bind(("", CHAT_PORT))
            self._chat.listen(20)
            self._chat.settimeout(1.0)
            while self.alive:
                try:
                    conn, addr = self._chat.accept()
                    threading.Thread(target=self._handle_chat_conn,
                                     args=(conn, addr[0]), daemon=True).start()
                except socket.timeout:
                    pass
        except Exception as e:
            if self.alive:
                self.q.put(("log", f"Chat server crashed: {e}", "error"))
        finally:
            try: self._chat.close()
            except Exception: pass

    def _handle_chat_conn(self, conn: socket.socket, ip: str):
        try:
            conn.settimeout(30)
            msg = recv_msg(conn)
            mtype = msg.get("type")
            if mtype in ("chat_text", "chat_voice", "chat_delete"):
                self.q.put(("chat_recv", ip, msg))
        except Exception as e:
            self.q.put(("log", f"Chat error from {ip}: {e}", "error"))
        finally:
            try: conn.close()
            except Exception: pass

    # ── UDP discovery ─────────────────────────────────────────────────────────
    def _udp_loop(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("", UDP_PORT))
            s.settimeout(0.1)
            while self.alive:
                try:
                    data, addr = s.recvfrom(4096)
                    msg = json.loads(data.decode("utf-8"))
                    if msg.get("type") == "discover":
                        reply = json.dumps({
                            "type": "discover_reply",
                            "host": socket.gethostname(),
                            "ip":   get_local_ip(),
                        }).encode("utf-8")
                        s.sendto(reply, addr)
                        peer_ip = msg.get("ip", addr[0])
                        self.q.put(("peer_found", msg.get("host", "?"), peer_ip))
                    elif msg.get("type") == "discover_reply":
                        self.q.put(("peer_found", msg["host"], msg["ip"]))
                except socket.timeout:
                    pass
                except Exception:
                    pass
        except Exception as e:
            if self.alive:
                self.q.put(("log", f"UDP server crashed: {e}", "error"))


def broadcast_discover(q: queue.Queue):
    try:
        local_ip   = get_local_ip()
        local_host = socket.gethostname()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception: pass
        s.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)
        s.settimeout(0.1)
        msg = json.dumps({"type": "discover", "host": local_host, "ip": local_ip}).encode("utf-8")
        for _ in range(3):
            try: s.sendto(msg, ("255.255.255.255", UDP_PORT))
            except Exception: pass
            time.sleep(0.15)
        discovered = set()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(4096)
                r = json.loads(data.decode("utf-8"))
                if r.get("type") == "discover_reply":
                    ip = r["ip"]
                    if ip != local_ip and ip not in discovered:
                        discovered.add(ip)
                        q.put(("peer_found", r["host"], ip))
            except socket.timeout: pass
            except Exception: pass
        s.close()
        q.put(("scan_done",))
    except Exception as e:
        q.put(("log", f"Scan error: {e}", "error"))
        q.put(("scan_done",))


# ═══════════════════════════════════════════════════════════════════════════════
# GUI — SwiftShare v2.0
# ═══════════════════════════════════════════════════════════════════════════════

def _sec_label(parent, text: str, color: str):
    ctk.CTkLabel(
        parent,
        text=text,
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=color,
    ).pack(anchor="w", padx=16, pady=(12, 6))


class ChatPanel(ctk.CTkFrame):
    """
    Inline chat panel — renders inside the main window's content area.
    Replaces the CTkTabview when a chat is opened; back arrow restores it.
    One instance is reused for all peers (peer context is swapped via load_peer).
    """

    def __init__(self, master, db, send_cb, local_name: str, back_cb):
        super().__init__(master, fg_color=C["bg"], corner_radius=0)
        self.db          = db
        self.send_cb     = send_cb        # send_cb(ip, payload_dict)
        self.local_name  = local_name
        self.back_cb     = back_cb        # called when ← is pressed

        self.peer_ip:   str | None = None
        self.peer_name: str | None = None

        self._recorder   = VoiceRecorder()
        self._recording  = False
        self._rec_start  = 0.0
        self._rec_timer_id = None

        self._typing_timer = None
        self._peer_is_typing = False
        self._reply_context: dict | None = None
        self._status_labels: dict[int, ctk.CTkLabel] = {}
        self._bubble_map: dict[int, ctk.CTkFrame] = {}

        # Maps bubble_id -> (play_btn, stop_btn, stop_btn_frame) for live updates
        self._voice_bubbles: dict[str, tuple] = {}
        # Register for voice-player state changes
        VOICE_PLAYER.set_state_cb(self._on_voice_state)

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Header bar ────────────────────────────────────────────────────────
        # Telegram-style header: dark, with avatar initials + name + status
        hdr = ctk.CTkFrame(self, fg_color=C["chat_hdr"], height=64, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Separator line under header (Telegram touch)
        hdr_sep = ctk.CTkFrame(self, fg_color="#1a1a30", height=1, corner_radius=0)
        hdr_sep.pack(fill="x")

        # Back button — Snapchat-style chunky arrow
        ctk.CTkButton(
            hdr, text="‹", width=36, height=36,
            font=ctk.CTkFont(size=26, weight="bold"),
            fg_color="transparent", hover_color="#1a1a30",
            text_color=C["accent"], corner_radius=18,
            command=self.back_cb,
        ).pack(side="left", padx=(8, 0))

        # Avatar — circular initials badge (Telegram/WhatsApp style)
        self._avatar_frame = ctk.CTkFrame(
            hdr, fg_color=C["avatar_them"], width=40, height=40, corner_radius=20,
        )
        self._avatar_frame.pack(side="left", padx=(8, 0))
        self._avatar_frame.pack_propagate(False)
        self._avatar_lbl = ctk.CTkLabel(
            self._avatar_frame, text="?",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#ffffff",
        )
        self._avatar_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # Name + status column
        name_col = ctk.CTkFrame(hdr, fg_color="transparent")
        name_col.pack(side="left", padx=(10, 0))

        self._hdr_name = ctk.CTkLabel(
            name_col, text="",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=C["text"], anchor="w",
        )
        self._hdr_name.pack(anchor="w")

        # Status row: green dot + "Online" text
        status_row = ctk.CTkFrame(name_col, fg_color="transparent")
        status_row.pack(anchor="w")
        self._hdr_dot = ctk.CTkLabel(
            status_row, text="●", font=ctk.CTkFont(size=8),
            text_color=C["online"],
        )
        self._hdr_dot.pack(side="left")
        self._hdr_ip = ctk.CTkLabel(
            status_row, text="",
            font=ctk.CTkFont(size=11),
            text_color=C["sub"],
        )
        self._hdr_ip.pack(side="left", padx=(4, 0))

        # ── Message scroll area ───────────────────────────────────────────────
        # WhatsApp: subtle dot-grid pattern feel via the dark bg
        self._msg_frame = ctk.CTkScrollableFrame(
            self, fg_color=C["chat_bg"], corner_radius=0,
        )
        self._msg_frame.pack(fill="both", expand=True)

        self._reply_preview = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=8)
        self._reply_preview.pack(fill="x", padx=10, pady=(8, 0))
        self._reply_preview.pack_forget()
        self._reply_label = ctk.CTkLabel(
            self._reply_preview, text="", font=ctk.CTkFont(size=12),
            text_color=C["text"], anchor="w", wraplength=620, justify="left"
        )
        self._reply_label.pack(side="left", padx=(12, 8), pady=10, expand=True)
        ctk.CTkButton(
            self._reply_preview, text="✕", width=32, height=32,
            fg_color=C["card2"], hover_color=C["border"],
            text_color=C["text"], corner_radius=16,
            command=self._clear_reply,
        ).pack(side="right", padx=10, pady=10)

        # ── Recording indicator bar ───────────────────────────────────────────
        # Snapchat-style — bright red pill that appears above input
        self._rec_bar = ctk.CTkFrame(
            self, fg_color="#1a0a0e", height=0, corner_radius=0,
        )
        self._rec_bar.pack(fill="x", side="bottom")
        self._rec_bar.pack_propagate(False)
        self._rec_lbl = ctk.CTkLabel(
            self._rec_bar, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=C["voice_rec"],
        )
        self._rec_lbl.pack(expand=True)

        # ── Input bar ─────────────────────────────────────────────────────────
        # WhatsApp: pill input + send fab + mic fab
        input_bar = ctk.CTkFrame(
            self, fg_color=C["chat_input_bg"], height=70, corner_radius=0,
        )
        input_bar.pack(fill="x", side="bottom")
        input_bar.pack_propagate(False)

        # Inner pill wrapper for the text box (extra rounded container)
        pill_wrap = ctk.CTkFrame(
            input_bar, fg_color=C["card"], corner_radius=24,
            border_color=C["border"], border_width=1,
        )
        pill_wrap.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=14)

        self._text_entry = ctk.CTkEntry(
            pill_wrap, placeholder_text="Message…",
            height=36, font=ctk.CTkFont(size=13),
            fg_color="transparent", border_width=0,
            text_color=C["text"], corner_radius=20,
        )
        self._text_entry.pack(fill="x", padx=(10, 10), pady=4)
        self._text_entry.bind("<Return>", lambda _: self._send_text())
        self._text_entry.bind("<KeyRelease>", lambda _: self._on_type())

        # Send FAB — Snapchat vivid filled circle
        self._send_fab = ctk.CTkButton(
            input_bar, text="▲", width=44, height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=C["accent"], hover_color="#5550dd",
            corner_radius=22, command=self._send_text,
        )
        self._send_fab.pack(side="left", padx=(0, 6), pady=13)

        # Mic FAB — Snapchat vivid pink
        self._mic_btn = ctk.CTkButton(
            input_bar, text="🎙", width=44, height=44,
            font=ctk.CTkFont(size=16),
            fg_color=C["voice_btn"], hover_color="#dd4466",
            corner_radius=22, command=None,
        )
        self._mic_btn.pack(side="left", padx=(0, 14), pady=13)
        self._mic_btn.bind("<ButtonPress-1>",   self._voice_press)
        self._mic_btn.bind("<ButtonRelease-1>", self._voice_release)
        if not HAS_AUDIO:
            self._mic_btn.configure(state="disabled", text="🎙✗")

    # ── Load peer ─────────────────────────────────────────────────────────────
    def load_peer(self, ip: str, name: str):
        """Swap the panel to show a different peer's history."""
        # Stop any playing audio
        VOICE_PLAYER.stop()
        self._voice_bubbles.clear()

        self.peer_ip   = ip
        self.peer_name = name

        # Update header
        self._hdr_name.configure(text=name)
        self._hdr_ip.configure(text=f"Online  ·  {ip}")

        # Update avatar — first letter of peer name, uppercase
        initial = name[0].upper() if name else "?"
        self._avatar_lbl.configure(text=initial)

        # Clear message area
        for w in self._msg_frame.winfo_children():
            w.destroy()

        self._load_history()

    # ── History ───────────────────────────────────────────────────────────────
    def _load_history(self):
        rows = self.db.get_history(self.peer_ip)
        for row in rows:
            msg_id, _, peer_name, direction, mtype, content, duration, status, reactions, reply_to, ts = row
            sender = self.local_name if direction == "sent" else peer_name
            is_me  = direction == "sent"
            if mtype == "text":
                self._render_text_bubble(content, is_me, ts, sender, msg_id,
                                          status=status, reactions=reactions,
                                          reply_to=reply_to)
            elif mtype == "voice":
                try:
                    wav = base64.b64decode(content)
                    self._render_voice_bubble(wav, duration, is_me, ts, sender,
                                              msg_id, status=status,
                                              reactions=reactions,
                                              reply_to=reply_to)
                except Exception:
                    pass
        self._scroll_bottom()

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _render_text_bubble(self, text: str, is_me: bool, ts: float,
                             sender: str, msg_id: int | None = None,
                             status: str = "sent", reactions: str = "",
                             reply_to: str = ""):
        """
        Render a text message bubble.
        • Sent   (is_me): right-aligned, deep navy, rounded pill shape,
                          small avatar with my initial (Telegram style).
        • Received      : left-aligned, dark slate, avatar with peer initial.
        Timestamp + delivery tick bottom-right of bubble (WhatsApp style).
        """
        align   = "e"    if is_me else "w"
        bg      = C["bubble_me"]   if is_me else C["bubble_them"]
        fg_text = C["bubble_me_text"] if is_me else C["bubble_them_text"]
        av_bg   = C["avatar_me"]   if is_me else C["avatar_them"]
        initial = (self.local_name[0].upper() if is_me
                   else (self.peer_name or "?")[0].upper())
        padl    = (56, 8)  if is_me else (8, 56)    # (left, right) outer padding

        outer = ctk.CTkFrame(self._msg_frame, fg_color="transparent")
        outer.pack(fill="x", padx=10, pady=(3, 3), anchor=align)

        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(anchor=align)

        def _make_avatar(parent):
            av = ctk.CTkFrame(parent, fg_color=av_bg, width=30, height=30,
                              corner_radius=15)
            av.pack_propagate(False)
            ctk.CTkLabel(av, text=initial,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#ffffff").place(relx=0.5, rely=0.5,
                                                     anchor="center")
            return av

        # Avatar left side for received, hidden for sent (packed after bubble)
        if not is_me:
            av = _make_avatar(row)
            av.pack(side="left", anchor="s", padx=(0, 6), pady=(0, 2))

        # Bubble — pill with asymmetric corner radii via corner_radius
        # CustomTkinter doesn't support per-corner radii natively,
        # so we use a high corner_radius for an overall pill look.
        bubble = ctk.CTkFrame(
            row, fg_color=bg, corner_radius=18,
            border_width=1,
            border_color=C["pill_border_me"] if is_me else C["pill_border_them"],
        )
        bubble.pack(side="left" if not is_me else "right",
                    padx=padl, anchor=align)

        text_label = ctk.CTkLabel(
            bubble, text=text,
            font=ctk.CTkFont(size=13), text_color=fg_text,
            wraplength=300,
            justify="right" if is_me else "left",
            anchor="w",
        )
        text_label.pack(padx=14, pady=(9, 3), anchor="w")

        if reply_to:
            try:
                quote = json.loads(reply_to)
                preview = f"Reply to {quote.get('sender','?')}: {quote.get('text','')[:80]}"
            except Exception:
                preview = f"Reply to: {reply_to[:80]}"
            ctk.CTkLabel(
                bubble, text=preview,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=C["sub"], wraplength=280,
                justify="left", anchor="w",
            ).pack(padx=14, pady=(0, 3), anchor="w")

        if reactions:
            try:
                items = json.loads(reactions)
                if items:
                    ctk.CTkLabel(
                        bubble, text=" ".join(items),
                        font=ctk.CTkFont(size=11),
                        text_color=C["accent2"], anchor="w",
                    ).pack(padx=14, pady=(0, 3), anchor="w")
            except Exception:
                pass

        def _bind_bubble_actions(widget):
            if msg_id is None:
                return
            widget.bind("<Button-1>",
                        lambda e, s=sender, t=text: self._prepare_reply(s, t))
            widget.bind("<Button-3>",
                        lambda e, mid=msg_id: self._show_reaction_picker(mid))

        _bind_bubble_actions(bubble)

        # Footer: timestamp + tick (WhatsApp style)
        foot = ctk.CTkFrame(bubble, fg_color="transparent")
        foot.pack(fill="x", padx=(10, 10), pady=(0, 7))

        ctk.CTkLabel(foot, text=fmt_ts(ts),
                     font=ctk.CTkFont(size=10),
                     text_color=C["sub"]).pack(side="right", padx=(4, 0))

        if is_me:
            status_text = "✓✓ Read" if status == "read" else "✓✓"
            status_label = ctk.CTkLabel(foot, text=status_text,
                                       font=ctk.CTkFont(size=10),
                                       text_color=C["tick_read"])
            status_label.pack(side="right")
            if msg_id is not None:
                self._status_labels[msg_id] = status_label

        if is_me and msg_id is not None:
            ctk.CTkButton(
                foot, text="✕", width=18, height=18,
                font=ctk.CTkFont(size=10),
                fg_color="transparent", hover_color=C["card"],
                text_color=C["sub"], corner_radius=9,
                command=lambda mid=msg_id, o=outer: self._delete_message(mid, o),
            ).pack(side="left")

        # Avatar right side for sent messages
        if is_me:
            av = _make_avatar(row)
            av.pack(side="right", anchor="s", padx=(6, 0), pady=(0, 2))

    def _render_voice_bubble(self, wav_bytes: bytes, duration: float, is_me: bool,
                              ts: float, sender: str, msg_id: int | None = None,
                              status: str = "sent", reactions: str = "",
                              reply_to: str = ""):
        """
        Render a voice note bubble.
        WhatsApp-style waveform + duration.
        Telegram-style play/pause FAB.
        Snapchat-style vivid wave bar colors per direction.
        """
        import random as _random
        align    = "e"    if is_me else "w"
        bg       = C["bubble_me"]   if is_me else C["bubble_them"]
        av_bg    = C["avatar_me"]   if is_me else C["avatar_them"]
        wave_col = C["voice_wave_me"] if is_me else C["voice_wave_them"]
        initial  = (self.local_name[0].upper() if is_me
                    else (self.peer_name or "?")[0].upper())
        dur_s    = f"{int(duration // 60):01d}:{int(duration % 60):02d}"
        vid      = f"voice_{msg_id}_{ts}"
        wav_data = bytes(wav_bytes)
        padl     = (56, 8) if is_me else (8, 56)

        outer = ctk.CTkFrame(self._msg_frame, fg_color="transparent")
        outer.pack(fill="x", padx=10, pady=(3, 3), anchor=align)

        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(anchor=align)

        def _make_avatar(parent):
            av = ctk.CTkFrame(parent, fg_color=av_bg, width=30, height=30,
                              corner_radius=15)
            av.pack_propagate(False)
            ctk.CTkLabel(av, text=initial,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#ffffff").place(relx=0.5, rely=0.5,
                                                     anchor="center")
            return av

        if not is_me:
            av = _make_avatar(row)
            av.pack(side="left", anchor="s", padx=(0, 6), pady=(0, 2))

        bubble = ctk.CTkFrame(
            row, fg_color=bg, corner_radius=18,
            border_width=1,
            border_color=C["pill_border_me"] if is_me else C["pill_border_them"],
        )
        bubble.pack(side="left" if not is_me else "right",
                    padx=padl, anchor=align)

        # Inner voice row — mic icon + play/stop + waveform + duration
        inner = ctk.CTkFrame(bubble, fg_color="transparent")
        inner.pack(padx=12, pady=(10, 6))

        # Mic icon label (static)
        ctk.CTkLabel(inner, text="🎙",
                     font=ctk.CTkFont(size=14),
                     text_color=wave_col).pack(side="left", padx=(0, 6))

        # Play FAB
        play_btn = ctk.CTkButton(
            inner, text="▶", width=34, height=34,
            font=ctk.CTkFont(size=14),
            fg_color=C["accent"], hover_color="#5550dd",
            corner_radius=17,
            command=lambda: self._voice_play(vid, wav_data),
        )
        play_btn.pack(side="left", padx=(0, 6))

        # Stop button (hidden until playing)
        stop_frame = ctk.CTkFrame(inner, fg_color="transparent")
        stop_btn = ctk.CTkButton(
            stop_frame, text="⏹", width=34, height=34,
            font=ctk.CTkFont(size=14),
            fg_color=C["error"], hover_color="#cc2244",
            corner_radius=17,
            command=lambda: VOICE_PLAYER.stop(vid),
        )
        stop_btn.pack()
        # Not packed yet — shown on play

        # Waveform — Snapchat-pop colored bars, varied heights (seeded by content)
        wave_canvas = tk.Canvas(inner, width=110, height=34,
                                bg=bg, highlightthickness=0)
        wave_canvas.pack(side="left")
        rng = _random.Random(hash(wav_bytes[:8]))
        for i in range(20):
            x  = 4 + i * 5
            h  = int(6 + rng.random() * 20)
            y1 = 17 - h // 2
            y2 = 17 + h // 2
            alpha_hex = "%02x" % int(120 + rng.random() * 135)
            wave_canvas.create_rectangle(
                x, y1, x + 3, y2,
                fill=wave_col, outline="",
            )

        ctk.CTkLabel(inner, text=dur_s,
                     font=ctk.CTkFont(size=11),
                     text_color=C["sub"]).pack(side="left", padx=(6, 0))

        if reply_to:
            try:
                quote = json.loads(reply_to)
                preview = f"Reply to {quote.get('sender','?')}: {quote.get('text','')[:80]}"
            except Exception:
                preview = f"Reply to: {reply_to[:80]}"
            ctk.CTkLabel(
                bubble, text=preview,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=C["sub"], wraplength=280,
                justify="left", anchor="w",
            ).pack(padx=14, pady=(0, 3), anchor="w")

        if reactions:
            try:
                items = json.loads(reactions)
                if items:
                    ctk.CTkLabel(
                        bubble, text=" ".join(items),
                        font=ctk.CTkFont(size=11),
                        text_color=C["accent2"], anchor="w",
                    ).pack(padx=14, pady=(0, 3), anchor="w")
            except Exception:
                pass

        def _bind_bubble_actions(widget):
            if msg_id is None:
                return
            widget.bind("<Button-1>",
                        lambda e, s=sender, t=f"Voice note from {sender}": self._prepare_reply(s, t))
            widget.bind("<Button-3>",
                        lambda e, mid=msg_id: self._show_reaction_picker(mid))

        _bind_bubble_actions(bubble)

        # Footer: timestamp + tick
        foot = ctk.CTkFrame(bubble, fg_color="transparent")
        foot.pack(fill="x", padx=(10, 10), pady=(0, 7))

        ctk.CTkLabel(foot, text=fmt_ts(ts),
                     font=ctk.CTkFont(size=10),
                     text_color=C["sub"]).pack(side="right", padx=(4, 0))

        if is_me:
            status_text = "✓✓ Read" if status == "read" else "✓✓"
            status_label = ctk.CTkLabel(foot, text=status_text,
                                       font=ctk.CTkFont(size=10),
                                       text_color=C["tick_read"])
            status_label.pack(side="right")
            if msg_id is not None:
                self._status_labels[msg_id] = status_label

        if is_me and msg_id is not None:
            ctk.CTkButton(
                foot, text="✕", width=18, height=18,
                font=ctk.CTkFont(size=10),
                fg_color="transparent", hover_color=C["card"],
                text_color=C["sub"], corner_radius=9,
                command=lambda mid=msg_id, o=outer: self._delete_message(mid, o),
            ).pack(side="left")

        if is_me:
            av = _make_avatar(row)
            av.pack(side="right", anchor="s", padx=(6, 0), pady=(0, 2))

        self._voice_bubbles[vid] = (play_btn, stop_btn, stop_frame, inner)

    def _prepare_reply(self, sender: str, text: str):
        self._reply_context = {"sender": sender, "text": text[:140]}
        self._reply_label.configure(text=f"Replying to {sender}: {self._reply_context['text']}")
        self._reply_preview.pack(fill="x", padx=10, pady=(8, 0))
        self._text_entry.focus_set()

    def _clear_reply(self):
        self._reply_context = None
        self._reply_preview.pack_forget()
        self._reply_label.configure(text="")

    def _show_reaction_picker(self, msg_id: int):
        menu = tk.Toplevel(self)
        menu.title("React")
        menu.geometry("240x80")
        menu.attributes("-topmost", True)
        menu.resizable(False, False)
        menu.grab_set()
        frame = ctk.CTkFrame(menu, fg_color=C["panel"], corner_radius=10)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        for emoji in ("👍", "❤️", "😂"):
            ctk.CTkButton(
                frame, text=emoji, width=60, height=34,
                font=ctk.CTkFont(size=16),
                fg_color=C["card2"], hover_color=C["accent2"],
                command=lambda e=emoji, m=msg_id: [self._apply_reaction(m, e), menu.destroy()],
            ).pack(side="left", expand=True, padx=4)

    def _apply_reaction(self, msg_id: int, emoji: str):
        try:
            with self.db._connect() as cx:
                row = cx.execute("SELECT reactions FROM messages WHERE id=?", (msg_id,)).fetchone()
            current = []
            if row and row[0]:
                try:
                    current = json.loads(row[0])
                except Exception:
                    current = []
            if emoji not in current:
                current.append(emoji)
            self.db.update_reactions(msg_id, json.dumps(current))
            self._reload_ui()
        except Exception:
            pass

    def _on_type(self):
        if not self.peer_ip:
            return
        if self._typing_timer:
            self.after_cancel(self._typing_timer)
        payload = {"type": "chat_typing", "from": self.local_name, "ts": time.time()}
        threading.Thread(target=lambda: self.send_cb(self.peer_ip, payload), daemon=True).start()
        self._typing_timer = self.after(1200, self._send_typing_stopped)

    def _send_typing_stopped(self):
        if not self.peer_ip:
            return
        payload = {"type": "chat_typing_stop", "from": self.local_name, "ts": time.time()}
        threading.Thread(target=lambda: self.send_cb(self.peer_ip, payload), daemon=True).start()

    def mark_message_read(self, msg_id: int):
        self.db.update_status(msg_id, "read")
        status_label = self._status_labels.get(msg_id)
        if status_label:
            status_label.configure(text="✓✓ Read")

    def _send_read_receipt(self, msg_id: int | None):
        if not self.peer_ip or msg_id is None:
            return
        payload = {"type": "chat_read", "from": self.local_name,
                   "msg_id": msg_id, "ts": time.time()}
        threading.Thread(target=lambda: self.send_cb(self.peer_ip, payload), daemon=True).start()

    def _clear_typing_status(self):
        self._peer_is_typing = False
        if self.peer_ip:
            self._hdr_ip.configure(text=f"Online  ·  {self.peer_ip}")

    def _voice_play(self, vid: str, wav_data: bytes):
        """Toggle play/pause for a voice note."""
        if VOICE_PLAYER._active_id == vid:
            # Already playing this one — stop it (acts as pause/stop)
            VOICE_PLAYER.stop(vid)
        else:
            VOICE_PLAYER.play(vid, wav_data)

    def _on_voice_state(self, vid: str, state: str):
        """Called by VOICE_PLAYER on play/stop — update buttons on main thread."""
        self.after(0, lambda: self._apply_voice_state(vid, state))

    def _apply_voice_state(self, vid: str, state: str):
        entry = self._voice_bubbles.get(vid)
        if not entry:
            return
        play_btn, stop_btn, stop_frame, row = entry
        try:
            if state == "play":
                play_btn.configure(text="⏸")
                stop_frame.pack(side="left", padx=(4, 0), after=play_btn)
            else:  # stop
                play_btn.configure(text="▶")
                stop_frame.pack_forget()
        except Exception:
            pass

    def _scroll_bottom(self):
        self.after(80, lambda: self._msg_frame._parent_canvas.yview_moveto(1.0))

    # ── Delete ────────────────────────────────────────────────────────────────
    def _delete_message(self, msg_id: int, bubble_outer: ctk.CTkFrame):
        self.db.delete(msg_id)
        try:
            bubble_outer.destroy()
        except Exception:
            pass
        # Notify peer
        if self.peer_ip:
            payload = {"type": "chat_delete", "msg_id": msg_id,
                       "from": self.local_name, "ts": time.time()}
            threading.Thread(target=lambda: self.send_cb(self.peer_ip, payload),
                             daemon=True).start()

    # ── Send ──────────────────────────────────────────────────────────────────
    def _send_text(self):
        if not self.peer_ip:
            return
        text = self._text_entry.get().strip()
        if not text:
            return
        reply_to_payload = None
        if self._reply_context:
            reply_to_payload = json.dumps(self._reply_context)
        self._text_entry.delete(0, "end")
        self._clear_reply()
        ts = time.time()
        payload = {
            "type": "chat_text", "from": self.local_name,
            "text": text, "ts": ts,
            "msg_id": None,
            "reply_to": reply_to_payload,
        }
        msg_id = self.db.add(self.peer_ip, self.peer_name, "sent", "text", text,
                             status="sent", reply_to=reply_to_payload or "")
        payload["msg_id"] = msg_id
        threading.Thread(target=lambda: self.send_cb(self.peer_ip, payload),
                         daemon=True).start()
        self._render_text_bubble(text, True, ts, self.local_name, msg_id,
                                  status="sent", reply_to=reply_to_payload or "")
        self._scroll_bottom()
        preview = text[:60] + ("…" if len(text) > 60 else "")
        show_notification(f"SwiftShare — 💬 Sent to {self.peer_name}", preview)

    def _voice_press(self, _evt):
        if self._recording or not HAS_AUDIO or not self.peer_ip:
            return
        VOICE_PLAYER.stop()   # stop any playback before recording
        self._recording = True
        self._rec_start = time.time()
        self._recorder.start()
        self._mic_btn.configure(fg_color=C["voice_rec"], text="⏹")
        try:
            self._rec_bar.configure(height=28)   # reveal recording bar
        except Exception:
            pass
        self._update_rec_label()

    def _voice_release(self, _evt):
        if not self._recording:
            return
        self._recording = False
        if self._rec_timer_id:
            self.after_cancel(self._rec_timer_id)
        self._rec_lbl.configure(text="")
        try:
            self._rec_bar.configure(height=0)    # collapse the indicator bar
        except Exception:
            pass
        self._mic_btn.configure(fg_color=C["voice_btn"], text="🎙")
        duration  = time.time() - self._rec_start
        wav_bytes = self._recorder.stop()
        if wav_bytes is None or duration < 0.5:
            return
        reply_to_payload = None
        if self._reply_context:
            reply_to_payload = json.dumps(self._reply_context)
        self._clear_reply()
        b64    = base64.b64encode(wav_bytes).decode("ascii")
        ts     = time.time()
        payload = {
            "type": "chat_voice", "from": self.local_name,
            "wav_b64": b64, "duration": duration,
            "ts": ts, "msg_id": None,
            "reply_to": reply_to_payload,
        }
        msg_id = self.db.add(self.peer_ip, self.peer_name, "sent", "voice", b64, duration,
                             status="sent", reply_to=reply_to_payload or "")
        payload["msg_id"] = msg_id
        threading.Thread(target=lambda: self.send_cb(self.peer_ip, payload),
                         daemon=True).start()
        self._render_voice_bubble(wav_bytes, duration, True, ts, self.local_name,
                                  msg_id, status="sent",
                                  reply_to=reply_to_payload or "")
        self._scroll_bottom()
        dur_s = f"{int(duration // 60):01d}:{int(duration % 60):02d}"
        show_notification(f"SwiftShare — 🎙 Voice note sent to {self.peer_name}",
                          f"Duration {dur_s}")

    def _update_rec_label(self):
        if not self._recording:
            return
        elapsed = time.time() - self._rec_start
        self._rec_lbl.configure(
            text=f"🔴  Recording  {elapsed:.1f}s  —  release to send"
        )
        # Expand bar on first tick
        try:
            if self._rec_bar.cget("height") == 0:
                self._rec_bar.configure(height=28)
        except Exception:
            pass
        self._rec_timer_id = self.after(100, self._update_rec_label)

    # ── Receive ───────────────────────────────────────────────────────────────
    def receive_message(self, msg: dict):
        """Deliver an incoming message into this panel."""
        mtype  = msg.get("type")
        ts     = msg.get("ts", time.time())
        sender = msg.get("from", self.peer_name or "?")
        if mtype == "chat_text":
            text     = msg.get("text", "")
            reply_to = msg.get("reply_to", "")
            msg_id   = self.db.add(self.peer_ip, self.peer_name, "recv", "text",
                                   text, 0.0, status="received",
                                   reply_to=reply_to or "")
            self._render_text_bubble(text, False, ts, sender, msg_id,
                                      status="received",
                                      reply_to=reply_to or "")
            self._scroll_bottom()
            self._send_read_receipt(msg.get("msg_id"))
        elif mtype == "chat_voice":
            b64      = msg.get("wav_b64", "")
            dur      = msg.get("duration", 0.0)
            reply_to = msg.get("reply_to", "")
            try:
                wav = base64.b64decode(b64)
            except Exception:
                return
            msg_id = self.db.add(self.peer_ip, self.peer_name, "recv", "voice",
                                 b64, dur, status="received",
                                 reply_to=reply_to or "")
            self._render_voice_bubble(wav, dur, False, ts, sender, msg_id,
                                      status="received",
                                      reply_to=reply_to or "")
            self._scroll_bottom()
            self._send_read_receipt(msg.get("msg_id"))
        elif mtype == "chat_typing":
            self._peer_is_typing = True
            self._hdr_ip.configure(text="Typing…")
            if self._typing_timer:
                self.after_cancel(self._typing_timer)
            self._typing_timer = self.after(1800, self._clear_typing_status)
        elif mtype == "chat_typing_stop":
            self._clear_typing_status()
        elif mtype == "chat_read":
            mid = msg.get("msg_id")
            if mid is not None:
                self.mark_message_read(mid)
        elif mtype == "chat_delete":
            mid = msg.get("msg_id")
            if mid is not None:
                self.db.delete(mid)
                # Remove bubble from UI by re-rendering (simplest safe approach)
                self._reload_ui()

    def _reload_ui(self):
        """Refresh message list after a remote delete."""
        for w in self._msg_frame.winfo_children():
            w.destroy()
        self._voice_bubbles.clear()
        self._load_history()


class SwiftShareApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1040x720")
        self.minsize(860, 580)
        self.configure(fg_color=C["bg"])

        self.local_ip   = get_local_ip()
        self.hostname   = socket.gethostname()
        self.peers: dict[str, str] = {}
        self.active_ip: str | None = None
        self._sending   = False
        self._scanning  = False
        self._minimized = False
        self._theme_mode = "dark"
        self._transfer_queue: list[tuple[str, str]] = []
        self._queue_lock = threading.Lock()
        self._queue_running = False

        # Unread counts per peer ip
        self._unread: dict[str, int] = {}
        # Last-seen timestamps per peer ip (for unread calc)
        self._last_seen: dict[str, float] = {k: time.time() for k in []}

        # Single inline chat panel (reused for all peers)
        self._chat_panel: ChatPanel | None = None
        self._chat_panel_ip: str | None = None   # which peer is currently loaded

        self.db     = ChatDB()
        self.db.expire_old()

        self.q      = queue.Queue()
        self.server = Server(self.q)
        self.server.start()
        threading.Thread(target=self._auto_discovery_loop, daemon=True, name="auto-discover").start()

        self._build_ui()
        self._poll()
        self._build_tray_icon()   # start system tray icon

        # Background minimise handler
        self.protocol("WM_DELETE_WINDOW", self._on_close_btn)
        self.bind("<Unmap>",  self._on_minimize)
        self.bind("<Map>",    self._on_restore)

    # ═══════════════════════════════════════════════════════════════════════════
    # UI Construction
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self._build_header()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self._build_content(body)

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=C["panel"], height=58, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        logo_row = ctk.CTkFrame(hdr, fg_color="transparent")
        logo_row.pack(side="left", padx=20)

        ctk.CTkLabel(logo_row, text="⚡",
                     font=ctk.CTkFont(size=26),
                     text_color=C["accent"]).pack(side="left")
        ctk.CTkLabel(logo_row, text="  SwiftShare",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=C["text"]).pack(side="left")
        ctk.CTkLabel(logo_row, text=f"  v{APP_VERSION}",
                     font=ctk.CTkFont(size=11),
                     text_color=C["sub"]).pack(side="left", pady=(6, 0))
        ctk.CTkLabel(logo_row, text=f"  by {STUDIO}",
                     font=ctk.CTkFont(size=11),
                     text_color=C["sub"]).pack(side="left", pady=(6, 0))

        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.pack(side="right", padx=20)

        self._scan_btn = ctk.CTkButton(
            right, text="🔍  Scan Network", width=130, height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=C["accent"], hover_color="#5550dd",
            corner_radius=8, command=self._start_scan,
        )
        self._scan_btn.pack(side="right", padx=(12, 0))

        ctk.CTkButton(
            right, text="⚙️ Settings", width=110, height=34,
            font=ctk.CTkFont(size=12),
            fg_color=C["card2"], hover_color=C["card"],
            corner_radius=8, command=self._show_settings,
        ).pack(side="right", padx=(0, 12))

        self._status_lbl = ctk.CTkLabel(
            right,
            text=f"🖥  {self.hostname}  ·  {self.local_ip}  ·  Port {TCP_PORT}",
            font=ctk.CTkFont(size=12),
            text_color=C["sub"],
        )
        self._status_lbl.pack(side="right")

    def _build_sidebar(self, parent):
        sb = ctk.CTkFrame(parent, fg_color=C["panel"], width=230, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        ctk.CTkLabel(sb, text="DISCOVERED PEERS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=C["sub"]).pack(anchor="w", padx=16, pady=(18, 6))

        self._peer_scroll = ctk.CTkScrollableFrame(sb, fg_color="transparent", height=260)
        self._peer_scroll.pack(fill="x", padx=8)

        self._no_peer_lbl = ctk.CTkLabel(
            self._peer_scroll,
            text="No peers found.\nClick Scan Network.",
            font=ctk.CTkFont(size=12),
            text_color=C["dim"],
            justify="center",
        )
        self._no_peer_lbl.pack(pady=24)

        ctk.CTkFrame(sb, fg_color=C["border"], height=1).pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(sb, text="MANUAL CONNECT",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=C["sub"]).pack(anchor="w", padx=16, pady=(0, 6))

        self._ip_entry = ctk.CTkEntry(
            sb, placeholder_text="e.g. 192.168.1.42",
            height=36, font=ctk.CTkFont(size=13),
            fg_color=C["card"], border_color=C["border"],
            text_color=C["text"],
        )
        self._ip_entry.pack(fill="x", padx=12, pady=(0, 6))
        self._ip_entry.bind("<Return>", lambda _: self._manual_connect())

        ctk.CTkButton(
            sb, text="Connect →", height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=C["card2"], hover_color=C["success"],
            text_color=C["text"], corner_radius=8,
            command=self._manual_connect,
        ).pack(fill="x", padx=12, pady=(0, 16))

        ctk.CTkFrame(sb, fg_color=C["border"], height=1).pack(fill="x", padx=16)

        ctk.CTkLabel(sb, text="SAVE LOCATION",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=C["sub"]).pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(sb, text="~/SwiftShare_Downloads",
                     font=ctk.CTkFont(size=11),
                     text_color=C["dim"],
                     wraplength=190, justify="left").pack(anchor="w", padx=16)
        ctk.CTkButton(
            sb, text="📂  Open Folder", height=30,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color=C["card"],
            text_color=C["sub"], border_width=1,
            border_color=C["border"], corner_radius=6,
            command=lambda: open_path(str(SAVE_DIR)),
        ).pack(fill="x", padx=12, pady=(8, 0))

    def _build_content(self, parent):
        # Outer container — transfers view and chat panel are stacked here
        self._content_main = ctk.CTkFrame(parent, fg_color="transparent")
        self._content_main.pack(side="left", fill="both", expand=True, padx=16, pady=16)

        # ── Transfer view (banner + tabs) ─────────────────────────────────────
        self._transfer_view = ctk.CTkFrame(self._content_main, fg_color="transparent")
        self._transfer_view.pack(fill="both", expand=True)

        # Active peer banner
        self._banner = ctk.CTkFrame(self._transfer_view, fg_color=C["card"],
                                     corner_radius=10, height=52)
        self._banner.pack(fill="x", pady=(0, 12))
        self._banner.pack_propagate(False)
        self._banner_lbl = ctk.CTkLabel(
            self._banner,
            text="⬡   No peer selected — scan the network or enter an IP address",
            font=ctk.CTkFont(size=13),
            text_color=C["sub"],
        )
        self._banner_lbl.pack(expand=True)

        # Chat launch button (shown when a peer is selected)
        self._chat_btn = ctk.CTkButton(
            self._banner,
            text="💬  Open Chat",
            width=120, height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=C["success"],
            hover_color="#00a883",
            text_color="#000000",
            corner_radius=8,
            command=self._open_chat,
        )
        # Packed when a peer is selected

        self._tabs = ctk.CTkTabview(
            self._transfer_view,
            fg_color=C["panel"],
            segmented_button_fg_color=C["card"],
            segmented_button_selected_color=C["accent"],
            segmented_button_selected_hover_color="#5550dd",
            segmented_button_unselected_color=C["card"],
            segmented_button_unselected_hover_color=C["card2"],
            text_color=C["text"],
            corner_radius=10,
        )
        self._tabs.pack(fill="both", expand=True)
        self._tabs.add("📁  Files")
        self._tabs.add("📋  Clipboard")
        self._tabs.add("📜  Activity Log")

        self._build_files_tab(self._tabs.tab("📁  Files"))
        self._build_clip_tab(self._tabs.tab("📋  Clipboard"))
        self._build_log_tab(self._tabs.tab("📜  Activity Log"))

        # ── Chat panel (hidden until a chat is opened) ────────────────────────
        self._chat_panel = ChatPanel(
            self._content_main,
            db=self.db,
            send_cb=self._chat_send,
            local_name=self.hostname,
            back_cb=self._close_chat_panel,
        )
        # Not packed yet — shown by _open_chat_for

    def _build_files_tab(self, parent):
        parent.configure(fg_color="transparent")

        send_card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10)
        send_card.pack(fill="x", padx=2, pady=(6, 4))
        _sec_label(send_card, "SEND FILE", C["accent"])

        row = ctk.CTkFrame(send_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))

        self._file_entry = ctk.CTkEntry(
            row, placeholder_text="Choose a file or folder to send to the active peer…",
            height=38, font=ctk.CTkFont(size=13),
            fg_color=C["card2"], border_color=C["border"],
            text_color=C["text"],
        )
        self._file_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="Browse", width=80, height=38,
                      fg_color=C["card2"], hover_color=C["dim"],
                      text_color=C["text"], border_width=1,
                      border_color=C["border"],
                      command=self._browse_file).pack(side="left", padx=(0, 8))
        self._send_btn = ctk.CTkButton(
            row, text="▶  Send", width=100, height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=C["accent"], hover_color="#5550dd",
            command=self._send_file,
        )
        self._send_btn.pack(side="left")
        self._register_drag_drop()

        prog_row = ctk.CTkFrame(send_card, fg_color="transparent")
        prog_row.pack(fill="x", padx=16, pady=(0, 4))
        self._prog_var = ctk.DoubleVar(value=0)
        self._prog_bar = ctk.CTkProgressBar(
            prog_row, variable=self._prog_var,
            height=6, corner_radius=3,
            fg_color=C["card2"], progress_color=C["accent"],
        )
        self._prog_bar.pack(fill="x")
        self._prog_lbl = ctk.CTkLabel(send_card, text="",
                                       font=ctk.CTkFont(size=11),
                                       text_color=C["sub"])
        self._prog_lbl.pack(anchor="w", padx=16, pady=(0, 8))

        queue_card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10)
        queue_card.pack(fill="x", padx=2, pady=(4, 4))
        _sec_label(queue_card, "TRANSFER QUEUE", C["accent"])
        self._queue_box = ctk.CTkFrame(queue_card, fg_color="transparent")
        self._queue_box.pack(fill="x", padx=12, pady=(0, 10))
        self._refresh_transfer_queue()

        recv_card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10)
        recv_card.pack(fill="both", expand=True, padx=2, pady=4)
        _sec_label(recv_card, "RECEIVED FILES", C["success"])
        self._recv_scroll = ctk.CTkScrollableFrame(recv_card, fg_color="transparent")
        self._recv_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._no_recv_lbl = ctk.CTkLabel(
            self._recv_scroll,
            text="Files you receive will appear here.",
            font=ctk.CTkFont(size=12),
            text_color=C["dim"],
        )
        self._no_recv_lbl.pack(pady=20)

    def _build_clip_tab(self, parent):
        parent.configure(fg_color="transparent")

        send_card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10)
        send_card.pack(fill="x", padx=2, pady=(6, 4))
        _sec_label(send_card, "SEND CLIPBOARD", C["accent"])

        self._clip_box = ctk.CTkTextbox(
            send_card, height=120,
            font=ctk.CTkFont(family="Courier New", size=13),
            fg_color=C["card2"], border_color=C["border"],
            border_width=1, text_color=C["text"], corner_radius=8,
        )
        self._clip_box.pack(fill="x", padx=16, pady=(0, 8))

        btn_row = ctk.CTkFrame(send_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(btn_row, text="📋  Paste from Clipboard",
                      height=36, fg_color=C["card2"],
                      hover_color=C["dim"], text_color=C["text"],
                      border_width=1, border_color=C["border"],
                      command=self._paste_clip).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Clear", width=70, height=36,
                      fg_color="transparent", hover_color=C["card2"],
                      text_color=C["sub"], border_width=1,
                      border_color=C["border"],
                      command=lambda: self._clip_box.delete("1.0", "end")).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="▶  Send", width=100, height=36,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=C["accent"], hover_color="#5550dd",
                      command=self._send_clip).pack(side="left")

        if not HAS_CLIP:
            ctk.CTkLabel(send_card,
                         text="⚠  pyperclip not installed — paste manually or: pip install pyperclip",
                         font=ctk.CTkFont(size=11),
                         text_color=C["warning"]).pack(anchor="w", padx=16, pady=(0, 8))

        recv_card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10)
        recv_card.pack(fill="both", expand=True, padx=2, pady=4)
        _sec_label(recv_card, "RECEIVED CLIPBOARD", C["success"])
        self._clip_recv_scroll = ctk.CTkScrollableFrame(recv_card, fg_color="transparent")
        self._clip_recv_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._no_clip_lbl = ctk.CTkLabel(
            self._clip_recv_scroll,
            text="Clipboard messages you receive will appear here.",
            font=ctk.CTkFont(size=12),
            text_color=C["dim"],
        )
        self._no_clip_lbl.pack(pady=20)

    def _build_log_tab(self, parent):
        parent.configure(fg_color="transparent")
        self._log_box = ctk.CTkTextbox(
            parent, font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=C["card"], text_color=C["text"], corner_radius=8,
        )
        self._log_box.pack(fill="both", expand=True, padx=2, pady=(6, 4))
        self._log_box.configure(state="disabled")
        inner: tk.Text = self._log_box._textbox
        inner.tag_config("success", foreground=C["success"])
        inner.tag_config("error",   foreground=C["error"])
        inner.tag_config("warning", foreground=C["warning"])
        inner.tag_config("info",    foreground=C["sub"])
        inner.tag_config("ts",      foreground=C["dim"])
        ctk.CTkButton(
            parent, text="🗑  Clear Log", width=110, height=30,
            fg_color=C["card"], hover_color=C["card2"],
            text_color=C["sub"], border_width=1,
            border_color=C["border"], command=self._clear_log,
        ).pack(anchor="e", padx=2, pady=(0, 2))

    # ═══════════════════════════════════════════════════════════════════════════
    # Event pump
    # ═══════════════════════════════════════════════════════════════════════════

    def _poll(self):
        try:
            while True:
                self._dispatch(self.q.get_nowait())
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _dispatch(self, evt):
        t = evt[0]

        if t == "server_ready":
            self.log(f"Server listening on {self.local_ip}:{TCP_PORT}  (chat:{CHAT_PORT})", "success")

        elif t == "peer_found":
            _, host, ip = evt
            if ip == self.local_ip:
                return
            is_new = ip not in self.peers
            if self.peers.get(ip) != host:
                self.peers[ip] = host
                self._refresh_peers()
            if is_new:
                self.log(f"Peer discovered: {host} @ {ip}", "info")

        elif t == "scan_done":
            self._scanning = False
            self._scan_btn.configure(text="🔍  Scan Network", state="normal")
            count = len(self.peers)
            self.log(f"Scan complete — {count} peer(s) found", "success" if count else "warning")

        elif t == "clip_recv":
            _, ip, content, ts = evt
            self._add_clip_recv(ip, content, ts)
            self.log(f"Clipboard received from {self.peers.get(ip, ip)}", "success")
            preview = content[:60].replace("\n", " ") + ("…" if len(content) > 60 else "")
            show_notification(
                f"SwiftShare — 📋 Clipboard from {self.peers.get(ip, ip)}",
                preview
            )
            play_notification_sound("message")
            if HAS_CLIP:
                try: pyperclip.copy(content)
                except Exception: pass

        elif t == "notify":
            # Generic notification event posted from background threads
            _, title, body = evt
            show_notification(title, body)

        elif t == "file_recv_start":
            _, ip, name, size = evt
            self.log(f"Incoming file: {name} ({fmt_size(size)}) from {self.peers.get(ip, ip)}", "info")

        elif t == "recv_progress":
            _, path, done, total = evt
            if total > 0:
                self._prog_var.set(done / total)
                pct  = int(done / total * 100)
                name = Path(path).name
                self._prog_lbl.configure(
                    text=f"Receiving {name} — {fmt_size(done)} / {fmt_size(total)}  ({pct}%)"
                )

        elif t == "file_recv_done":
            _, ip, path, name, ok = evt
            self._prog_var.set(0)
            self._prog_lbl.configure(text="")
            if ok:
                self.log(f"✓ Received {name} → saved to Downloads", "success")
                self._add_file_recv(ip, path)
                show_notification(
                    "SwiftShare — 📄 File Received",
                    f"{name}  ·  from {self.peers.get(ip, ip)}"
                )
                play_notification_sound("file")
            else:
                self.log(f"Transfer of {name} was incomplete", "error")

        elif t == "send_progress":
            _, done, total, name = evt
            if total > 0:
                self._prog_var.set(done / total)
                pct = int(done / total * 100)
                self._prog_lbl.configure(
                    text=f"Sending {name} — {fmt_size(done)} / {fmt_size(total)}  ({pct}%)"
                )

        elif t == "send_done":
            self._prog_var.set(0)
            self._prog_lbl.configure(text="")
            self._sending = False

        elif t == "chat_recv":
            _, ip, msg = evt
            peer_name = self.peers.get(ip, ip)
            mtype = msg.get("type")

            # chat_delete is handled entirely inside ChatPanel.receive_message
            # which also calls db.delete; nothing to persist here.
            panel_is_open = (self._chat_panel_ip == ip and
                             self._chat_panel.winfo_ismapped())

            if panel_is_open:
                # Panel already visible for this peer — deliver directly
                # (ChatPanel.receive_message handles DB persistence for text/voice)
                self._chat_panel.receive_message(msg)
            else:
                # Panel not open — persist text/voice ourselves, bump badge
                if mtype == "chat_text":
                    self.db.add(ip, peer_name, "recv", "text", msg.get("text", ""),
                                0.0, status="received",
                                reply_to=msg.get("reply_to", "") or "")
                    self._unread[ip] = self._unread.get(ip, 0) + 1
                    self._refresh_peers()
                elif mtype == "chat_voice":
                    self.db.add(ip, peer_name, "recv", "voice",
                                msg.get("wav_b64", ""), msg.get("duration", 0),
                                status="received",
                                reply_to=msg.get("reply_to", "") or "")
                    self._unread[ip] = self._unread.get(ip, 0) + 1
                    self._refresh_peers()
                elif mtype == "chat_read":
                    mid = msg.get("msg_id")
                    if mid is not None:
                        self.db.update_status(mid, "read")
                elif mtype == "chat_delete":
                    mid = msg.get("msg_id")
                    if mid is not None:
                        self.db.delete(mid)

            # Always notify for text and voice
            if mtype == "chat_text":
                body = msg.get("text", "")
                preview = body[:80] + ("…" if len(body) > 80 else "")
                show_notification(f"💬 {peer_name}", preview)
                play_notification_sound("message")
                self.log(f"💬 Message from {peer_name}", "info")
            elif mtype == "chat_voice":
                dur = msg.get("duration", 0)
                show_notification(
                    f"🎙 {peer_name}",
                    f"Voice message  ·  {int(dur // 60):01d}:{int(dur % 60):02d}"
                )
                play_notification_sound("voice")
                self.log(f"🎙 Voice note from {peer_name}", "info")

        elif t == "log":
            level = evt[2] if len(evt) > 2 else "info"
            self.log(evt[1], level)

    def _auto_discovery_loop(self):
        while True:
            try:
                broadcast_discover(self.q)
            except Exception:
                pass
            time.sleep(45)

    def _enqueue_transfer(self, ip: str, path: str):
        with self._queue_lock:
            self._transfer_queue.append((ip, path))
            self._refresh_transfer_queue()
            if not self._queue_running:
                self._queue_running = True
                threading.Thread(target=self._process_queue, daemon=True).start()

    def _process_queue(self):
        while True:
            with self._queue_lock:
                if not self._transfer_queue:
                    self._queue_running = False
                    self._refresh_transfer_queue()
                    self.q.put(("send_done",))
                    return
                ip, path = self._transfer_queue.pop(0)
            p = Path(path)
            temp_zip = None
            try:
                if p.is_dir():
                    self.q.put(("log", f"Zipping folder {p.name}…", "info"))
                    temp_zip = _zip_folder(path)
                    send_path = temp_zip
                else:
                    send_path = path
                self.q.put(("log", f"Sending {Path(send_path).name} → {self.peers.get(ip, ip)}", "info"))
                def prog(done, total, name=Path(send_path).name):
                    self.q.put(("send_progress", done, total, name))
                ok = do_send_file(ip, send_path, prog)
                if ok:
                    self.q.put(("log", f"✓ {Path(send_path).name} delivered successfully", "success"))
                    self.q.put(("notify", "SwiftShare — 📤 File Sent",
                                f"{Path(send_path).name}  ·  to {self.peers.get(ip, ip)}"))
                    play_notification_sound("file")
                else:
                    self.q.put(("log", f"Transfer of {Path(send_path).name} may be incomplete", "warning"))
            except Exception as e:
                self.q.put(("log", f"Send failed: {e}", "error"))
            finally:
                if temp_zip and Path(temp_zip).exists():
                    try:
                        Path(temp_zip).unlink()
                    except Exception:
                        pass

    def _refresh_transfer_queue(self):
        if not hasattr(self, "_queue_box"):
            return
        for w in self._queue_box.winfo_children():
            w.destroy()
        if not self._transfer_queue:
            ctk.CTkLabel(
                self._queue_box,
                text="No queued transfers.",
                font=ctk.CTkFont(size=11),
                text_color=C["dim"],
            ).pack(pady=8)
            return
        for ip, path in self._transfer_queue:
            ctk.CTkLabel(
                self._queue_box,
                text=f"{Path(path).name} → {self.peers.get(ip, ip)}",
                font=ctk.CTkFont(size=11),
                text_color=C["text"],
                wraplength=360,
                justify="left",
            ).pack(anchor="w", padx=10, pady=2)

    def _toggle_theme(self):
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        ctk.set_appearance_mode(self._theme_mode)
        self.configure(fg_color=C["bg"])
        self.log(f"Theme switched to {self._theme_mode}", "info")

    def _show_settings(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Settings")
        dlg.geometry("320x220")
        dlg.transient(self)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text="Settings",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=C["text"]).pack(pady=(16, 8))
        # theme_button = ctk.CTkButton(
        #     dlg, text=f"Switch to {'light' if self._theme_mode == 'dark' else 'dark'} mode",
        #     width=220, height=34,
        #     fg_color=C["accent"], hover_color="#5550dd",
        #     command=lambda: [self._toggle_theme(), theme_button.configure(text=f"Switch to {'light' if self._theme_mode == 'dark' else 'dark'} mode")],
        # )
        # theme_button.pack(pady=(0, 12))
        ctk.CTkButton(
            dlg, text="About PixlByte Studios",
            width=220, height=34,
            fg_color=C["card2"], hover_color=C["card"],
            command=self._show_about,
        ).pack(pady=(0, 12))
        ctk.CTkButton(
            dlg, text="Close", width=220, height=34,
            fg_color=C["card"], hover_color=C["card2"],
            command=dlg.destroy,
        ).pack(pady=(0, 16))

    def _show_about(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("About SwiftShare")
        dlg.geometry("340x260")
        dlg.transient(self)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text="SwiftShare",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=C["text"]).pack(pady=(16, 8))
        ctk.CTkLabel(dlg, text=f"Version {APP_VERSION}",
                     font=ctk.CTkFont(size=12),
                     text_color=C["sub"]).pack()
        ctk.CTkLabel(dlg, text=f"Build date: {BUILD_DATE}",
                     font=ctk.CTkFont(size=12),
                     text_color=C["sub"]).pack(pady=(0, 12))
        ctk.CTkLabel(dlg, text="PixlByte Studios",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C["accent"]).pack(pady=(0, 6))
        ctk.CTkLabel(dlg,
                     text="A polished LAN transfer, chat, voice note and file-sharing app.",
                     wraplength=300, justify="center",
                     font=ctk.CTkFont(size=12), text_color=C["text"]).pack(padx=20, pady=(0, 16))
        ctk.CTkButton(
            dlg, text="Visit PixlByte Studios",
            width=200, height=36,
            fg_color=C["accent"], hover_color="#5550dd",
            command=lambda: open_path("https://www.pixlbyte.studio"),
        ).pack(pady=(0, 12))
        ctk.CTkButton(
            dlg, text="Close", width=200, height=36,
            fg_color=C["card"], hover_color=C["card2"],
            command=dlg.destroy,
        ).pack()

    def _register_drag_drop(self):
        if not HAS_DND:
            return
        try:
            if hasattr(self._file_entry, "drop_target_register"):
                self._file_entry.drop_target_register(DND_FILES)
                self._file_entry.dnd_bind("<<Drop>>", self._on_drop_file)
            elif hasattr(self, "drop_target_register"):
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop_file)
        except Exception:
            pass

    def _on_drop_file(self, event):
        data = event.data
        if not data:
            return "break"
        try:
            path = data.strip().strip('{}')
            if path.startswith('{') and path.endswith('}'):
                path = path[1:-1]
            if os.path.exists(path):
                self._file_entry.delete(0, 'end')
                self._file_entry.insert(0, path)
                return "break"
        except Exception:
            pass
        return "break"

    # ═══════════════════════════════════════════════════════════════════════════
    # Peer sidebar
    # ═══════════════════════════════════════════════════════════════════════════

    def _select_peer(self, ip: str):
        self.active_ip = ip
        name = self.peers.get(ip, ip)
        self._banner_lbl.configure(
            text=f"🔗  Connected to  {name}  ·  {ip}",
            text_color=C["success"],
        )
        # Show chat button in banner
        self._chat_btn.pack(side="right", padx=12)
        self._refresh_peers()

    def _refresh_peers(self):
        for w in self._peer_scroll.winfo_children():
            w.destroy()

        if not self.peers:
            ctk.CTkLabel(
                self._peer_scroll,
                text="No peers found.\nClick Scan Network.",
                font=ctk.CTkFont(size=12),
                text_color=C["dim"],
                justify="center",
            ).pack(pady=24)
            return

        for ip, host in self.peers.items():
            active   = ip == self.active_ip
            unread   = self._unread.get(ip, 0)
            fg       = C["accent"] if active else C["card2"]
            card     = ctk.CTkFrame(self._peer_scroll, fg_color=fg, corner_radius=8)
            card.pack(fill="x", pady=3, padx=2)

            dot_row = ctk.CTkFrame(card, fg_color="transparent")
            dot_row.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(dot_row, text="●",
                         font=ctk.CTkFont(size=10),
                         text_color=C["online"]).pack(side="left")
            ctk.CTkLabel(dot_row, text=f"  {host}",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=C["text"]).pack(side="left")

            if unread > 0:
                ctk.CTkLabel(dot_row, text=f" ● {unread}",
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=C["accent2"]).pack(side="right")

            ctk.CTkLabel(card, text=ip,
                         font=ctk.CTkFont(size=11),
                         text_color=C["sub"] if not active else C["text"],
                         ).pack(anchor="w", padx=10, pady=(0, 4))

            btn_row = ctk.CTkFrame(card, fg_color="transparent")
            btn_row.pack(fill="x", padx=10, pady=(0, 8))

            btn_text = "✓ Selected" if active else "Select"
            ctk.CTkButton(
                btn_row, text=btn_text, height=26, width=72,
                font=ctk.CTkFont(size=11),
                fg_color=C["panel"] if active else C["card"],
                hover_color=C["success"],
                text_color=C["text"], corner_radius=5,
                command=lambda i=ip: self._select_peer(i),
            ).pack(side="left", padx=(0, 4))

            ctk.CTkButton(
                btn_row, text="💬 Chat", height=26, width=72,
                font=ctk.CTkFont(size=11),
                fg_color=C["success"] if unread else C["card"],
                hover_color="#00a883",
                text_color="#000000" if unread else C["text"],
                corner_radius=5,
                command=lambda i=ip: self._open_chat_for(i),
            ).pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════════
    # Chat
    # ═══════════════════════════════════════════════════════════════════════════

    def _open_chat(self):
        if not self.active_ip:
            messagebox.showwarning(APP_NAME, "Select a peer first.")
            return
        self._open_chat_for(self.active_ip)

    def _open_chat_for(self, ip: str):
        # Clear unread badge
        self._unread[ip] = 0
        self._last_seen[ip] = time.time()
        self._refresh_peers()

        peer_name = self.peers.get(ip, ip)

        # If the panel is already showing this peer, do nothing extra
        if self._chat_panel_ip == ip and not self._transfer_view.winfo_ismapped():
            return

        # Load the peer into the shared panel (swaps history automatically)
        self._chat_panel.load_peer(ip, peer_name)
        self._chat_panel_ip = ip

        # Slide: hide transfer view, show chat panel
        self._transfer_view.pack_forget()
        self._chat_panel.pack(fill="both", expand=True)

    def _close_chat_panel(self):
        """Back arrow — hide chat panel, restore transfer view."""
        VOICE_PLAYER.stop()
        self._chat_panel.pack_forget()
        self._transfer_view.pack(fill="both", expand=True)
        self._chat_panel_ip = None

    def _chat_send(self, ip: str, payload: dict):
        do_send_chat(ip, payload)

    # ═══════════════════════════════════════════════════════════════════════════
    # File actions
    # ═══════════════════════════════════════════════════════════════════════════

    def _browse_file(self):
        path = filedialog.askopenfilename(title="Select a file to send", parent=self)
        if not path:
            path = filedialog.askdirectory(title="Select a folder to send", parent=self)
        if path:
            self._file_entry.delete(0, "end")
            self._file_entry.insert(0, path)

    def _send_file(self):
        if not self.active_ip:
            messagebox.showwarning(APP_NAME, "Select a peer first.")
            return
        path = self._file_entry.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning(APP_NAME, "Please select a valid file or folder.")
            return
        self._enqueue_transfer(self.active_ip, path)
        self._file_entry.delete(0, "end")
        self._prog_lbl.configure(text=f"Queued {Path(path).name} for transfer.")

    def _do_send_file(self, ip: str, path: str):
        try:
            p = Path(path)
            if p.is_dir():
                self.q.put(("log", f"Zipping folder {p.name}…", "info"))
                temp_zip = _zip_folder(path)
                send_path = temp_zip
            else:
                send_path = path
            size = Path(send_path).stat().st_size
            self.q.put(("log", f"Sending {Path(send_path).name} ({fmt_size(size)}) → {self.peers.get(ip, ip)}", "info"))
            def prog(done, total):
                self.q.put(("send_progress", done, total, Path(send_path).name))
            ok = do_send_file(ip, send_path, prog)
            if ok:
                self.q.put(("log", f"✓ {Path(send_path).name} delivered successfully", "success"))
                self.q.put(("notify", "SwiftShare — 📤 File Sent",
                            f"{Path(send_path).name}  ·  to {self.peers.get(ip, ip)}"))
            else:
                self.q.put(("log", f"Transfer of {Path(send_path).name} may be incomplete", "warning"))
            if p.is_dir() and temp_zip and Path(temp_zip).exists():
                try:
                    Path(temp_zip).unlink()
                except Exception:
                    pass
        except Exception as e:
            self.q.put(("log", f"Send failed: {e}", "error"))
        finally:
            self.q.put(("send_done",))

    # ═══════════════════════════════════════════════════════════════════════════
    # Clipboard actions
    # ═══════════════════════════════════════════════════════════════════════════

    def _paste_clip(self):
        if HAS_CLIP:
            try:
                text = pyperclip.paste()
                self._clip_box.delete("1.0", "end")
                self._clip_box.insert("1.0", text)
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Clipboard read error:\n{e}")
        else:
            messagebox.showinfo(APP_NAME, "pyperclip not installed.\nPaste manually (Ctrl+V).")

    def _send_clip(self):
        if not self.active_ip:
            messagebox.showwarning(APP_NAME, "Select a peer first.")
            return
        text = self._clip_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning(APP_NAME, "Text box is empty.")
            return
        threading.Thread(target=self._do_send_clip,
                         args=(self.active_ip, text), daemon=True).start()

    def _do_send_clip(self, ip: str, text: str):
        try:
            do_send_clipboard(ip, text)
            self.q.put(("log", f"✓ Clipboard sent to {self.peers.get(ip, ip)}", "success"))
            preview = text[:60].replace("\n", " ") + ("…" if len(text) > 60 else "")
            self.q.put(("notify", f"SwiftShare — 📋 Clipboard Sent",
                        f"To {self.peers.get(ip, ip)}  ·  {preview}"))
        except Exception as e:
            self.q.put(("log", f"Clipboard send failed: {e}", "error"))

    # ═══════════════════════════════════════════════════════════════════════════
    # Network actions
    # ═══════════════════════════════════════════════════════════════════════════

    def _start_scan(self):
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.configure(text="⏳  Scanning…", state="disabled")
        self.log("Broadcasting discovery on LAN…", "info")
        threading.Thread(target=broadcast_discover, args=(self.q,), daemon=True).start()

    def _manual_connect(self):
        ip = self._ip_entry.get().strip()
        if not ip:
            messagebox.showwarning(APP_NAME, "Enter an IP address to connect.")
            return
        self.log(f"Pinging {ip}…", "info")
        threading.Thread(target=self._do_ping, args=(ip,), daemon=True).start()

    def _do_ping(self, ip: str):
        try:
            host = do_ping(ip)
            self.q.put(("peer_found", host, ip))
            self.q.put(("log", f"Connected to {host} @ {ip}", "success"))
            self.after(0, lambda: self._select_peer(ip))
        except Exception as e:
            self.q.put(("log", f"Cannot reach {ip}: {e}", "error"))

    # ═══════════════════════════════════════════════════════════════════════════
    # Received items
    # ═══════════════════════════════════════════════════════════════════════════

    def _add_file_recv(self, ip: str, path: str):
        if self._no_recv_lbl and self._no_recv_lbl.winfo_exists():
            self._no_recv_lbl.destroy()
        p    = Path(path)
        size = p.stat().st_size
        name = self.peers.get(ip, ip)
        card = ctk.CTkFrame(self._recv_scroll, fg_color=C["card2"], corner_radius=8)
        card.pack(fill="x", pady=3, padx=2)
        left = ctk.CTkFrame(card, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(left, text=f"📄  {p.name}",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C["text"], anchor="w").pack(anchor="w")
        ctk.CTkLabel(left,
                     text=f"from {name}  ·  {fmt_size(size)}  ·  {datetime.now().strftime('%H:%M:%S')}",
                     font=ctk.CTkFont(size=11),
                     text_color=C["sub"]).pack(anchor="w")
        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="right", padx=10, pady=10)
        ctk.CTkButton(right, text="Open", width=62, height=30,
                      fg_color=C["success"], hover_color="#00a883",
                      text_color="#000000",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      corner_radius=6,
                      command=lambda p_=str(p): open_path(p_)).pack(side="left", padx=(0, 4))
        ctk.CTkButton(right, text="Folder", width=62, height=30,
                      fg_color=C["card"], hover_color=C["dim"],
                      text_color=C["text"], corner_radius=6,
                      command=lambda d=str(p.parent): open_path(d)).pack(side="left")

    def _add_clip_recv(self, ip: str, content: str, ts: float):
        if self._no_clip_lbl and self._no_clip_lbl.winfo_exists():
            self._no_clip_lbl.destroy()
        sender  = self.peers.get(ip, ip)
        preview = content[:200].replace("\n", " ↵ ")
        card    = ctk.CTkFrame(self._clip_recv_scroll, fg_color=C["card2"], corner_radius=8)
        card.pack(fill="x", pady=3, padx=2)
        left = ctk.CTkFrame(card, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(left, text=f"from {sender}  ·  {fmt_ts(ts)}",
                     font=ctk.CTkFont(size=11),
                     text_color=C["sub"]).pack(anchor="w")
        ctk.CTkLabel(left, text=preview + ("…" if len(content) > 200 else ""),
                     font=ctk.CTkFont(family="Courier New", size=12),
                     text_color=C["text"], wraplength=500,
                     justify="left", anchor="w").pack(anchor="w")
        def _copy():
            if HAS_CLIP:
                try: pyperclip.copy(content)
                except Exception: pass
            self._clip_box.delete("1.0", "end")
            self._clip_box.insert("1.0", content)
            self._tabs.set("📋  Clipboard")
        ctk.CTkButton(card, text="📋 Copy", width=72, height=30,
                      fg_color=C["success"], hover_color="#00a883",
                      text_color="#000000",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      corner_radius=6, command=_copy).pack(side="right", padx=12, pady=10)

    # ═══════════════════════════════════════════════════════════════════════════
    # Log
    # ═══════════════════════════════════════════════════════════════════════════

    def log(self, msg: str, level: str = "info"):
        icons = {"success": "✓", "error": "✗", "warning": "⚠", "info": "·"}
        icon  = icons.get(level, "·")
        ts    = datetime.now().strftime("%H:%M:%S")
        inner: tk.Text = self._log_box._textbox
        self._log_box.configure(state="normal")
        inner.insert("end", f"[{ts}] ", ("ts",))
        inner.insert("end", f"{icon}  {msg}\n", (level,))
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ═══════════════════════════════════════════════════════════════════════════
    # Window lifecycle — system tray + background behaviour
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_minimize(self, _evt=None):
        self._minimized = True

    def _on_restore(self, _evt=None):
        self._minimized = False

    def _on_close_btn(self):
        """Hide to system tray instead of quitting."""
        self.withdraw()          # hide window completely
        self._minimized = True
        show_notification(
            "SwiftShare is still running",
            "SwiftShare is in the system tray. Right-click the tray icon to exit."
        )

    def _build_tray_icon(self):
        """
        Build the pystray Icon object and store it — does NOT start it yet.
        Call _start_tray_icon() after mainloop begins (via self.after).

        Why the split?
          pystray's Win32 backend creates a hidden HWND and pumps Win32 messages.
          If you call icon.run() before Tk's own message loop is running the two
          message pumps can deadlock or the tray window never gets painted.
          Scheduling the start with after(500, ...) lets Tk settle first.
        """
        if not (HAS_TRAY and HAS_PIL):
            return

        try:
            icon_file = resource_path("icon.png")
            if icon_file.exists():
                img = Image.open(str(icon_file)).convert("RGBA").resize((64, 64), Image.LANCZOS)
            else:
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.ellipse([2, 2, 62, 62], fill="#13131f")
                bolt = [(34, 4), (18, 34), (30, 34), (22, 60), (46, 28), (34, 28), (42, 4)]
                draw.polygon(bolt, fill="#6c63ff")

            self._tray_image = img  # strong ref — prevents GC

            def _show(_icon, _item):
                self.after(0, self._tray_show)

            def _quit(_icon, _item):
                _icon.stop()
                self.after(0, self.on_close)

            menu = pystray.Menu(
                pystray.MenuItem("Show SwiftShare", _show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", _quit),
            )

            self._tray = pystray.Icon("SwiftShare", self._tray_image, "SwiftShare", menu)

            # Delay actual start until after Tk mainloop is running
            self.after(500, self._start_tray_icon)

        except Exception as e:
            print(f"[Tray] Build failed: {e}")
            self._tray = None

    def _start_tray_icon(self):
        """
        Launch pystray in a plain (non-daemon) thread.

        Rules that must ALL be satisfied for the tray to appear:
          1. icon.run() must be called from its OWN thread — not the Tk thread.
          2. That thread must NOT be a daemon thread on Windows; daemon threads
             get killed before the Win32 message pump can process WM_CREATE on
             the hidden notification window, so the icon never registers.
          3. The thread must stay alive for the lifetime of the app — we keep
             the reference in self._tray_thread so it is never GC'd.
          4. We set self._tray.visible = True explicitly after run() is called
             because some pystray versions default to hidden.
        """
        if not hasattr(self, "_tray") or self._tray is None:
            return

        def _run():
            try:
                # visible must be set inside the thread, after run() starts
                self._tray.run(self._tray_setup)
            except Exception as e:
                print(f"[Tray] run() error: {e}")

        # Non-daemon so Win32 pump survives Tk teardown ordering
        self._tray_thread = threading.Thread(target=_run, name="tray-icon", daemon=False)
        self._tray_thread.start()

    def _tray_setup(self, icon):
        """Called by pystray from inside the tray thread once the pump is ready."""""
        icon.visible = True

    def _tray_show(self):
        """Restore window from tray."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self._minimized = False

    def on_close(self):
        """Hard quit — stop tray thread, stop server, destroy window."""
        try:
            if hasattr(self, "_tray") and self._tray:
                self._tray.stop()
        except Exception:
            pass
        try:
            # Join the non-daemon tray thread so the process exits cleanly.
            # Timeout=2 prevents hanging if something goes wrong.
            if hasattr(self, "_tray_thread") and self._tray_thread.is_alive():
                self._tray_thread.join(timeout=2)
        except Exception:
            pass
        self.server.stop()
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = SwiftShareApp()
    signal.signal(signal.SIGINT, lambda *_: app.on_close())
    app.mainloop()