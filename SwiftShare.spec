# SwiftShare.spec  —  PyInstaller build spec
#
# Usage:
#   pyinstaller SwiftShare.spec
#
# Requirements:
#   pip install pyinstaller customtkinter pyperclip pyaudio pillow pystray
#
# Put icon.png in the same folder as SwiftShare.py before running.
# The spec auto-converts it to icon.ico for the .exe titlebar/taskbar,
# and also bundles the .png so the tray icon can load it at runtime.
# ─────────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
import customtkinter

block_cipher = None

# ── Locate customtkinter asset folder ────────────────────────────────────────
ctk_path = Path(customtkinter.__file__).parent

# ── Convert icon.png → icon.ico at build time ─────────────────────────────────
# PyInstaller's EXE() requires a .ico for the Windows titlebar and taskbar.
# We generate it from your icon.png using Pillow so you never need a separate
# .ico file — just keep icon.png next to SwiftShare.py.
_icon_png = Path("icon.png")
_icon_ico = Path("icon.ico")

if _icon_png.exists():
    from PIL import Image as _PILImg
    _pil_img = _PILImg.open(str(_icon_png)).convert("RGBA")
    _pil_img.save(
        str(_icon_ico),
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    EXE_ICON = str(_icon_ico)
    print(f"[spec] icon.ico created from icon.png  ({_icon_ico.resolve()})")
else:
    EXE_ICON = None
    print("[spec] WARNING: icon.png not found next to SwiftShare.py — .exe will use default Windows icon")

# ─────────────────────────────────────────────────────────────────────────────
# Analysis
# ─────────────────────────────────────────────────────────────────────────────
a = Analysis(
    ["SwiftShare.py"],
    pathex=[],
    binaries=[],

    datas=[
        # customtkinter themes / images
        (str(ctk_path / "assets"), "customtkinter/assets"),
        # icon.png bundled at the root of _MEIPASS so resource_path("icon.png")
        # finds it at runtime inside the frozen .exe
        ("icon.png", "."),
    ],

    hiddenimports=[
        # ── pystray backends (loaded via importlib — PyInstaller misses them) ──
        "pystray._win32",       # Windows  <- the main culprit
        "pystray._darwin",      # macOS
        "pystray._xorg",        # Linux X11
        "pystray._gtk",         # Linux GTK fallback

        # ── PIL / Pillow plugins ──────────────────────────────────────────────
        "PIL._imagingtk",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.BmpImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.IcoImagePlugin",
        "PIL._imaging",

        # ── audio / UI ───────────────────────────────────────────────────────
        "pyaudio",
        "customtkinter",

        # ── stdlib sometimes missed ───────────────────────────────────────────
        "wave",
        "io",
        "sqlite3",
        "json",
        "struct",
        "socket",
        "threading",
        "queue",
        "base64",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",

        # ── Windows internals used by pystray._win32 ──────────────────────────
        "win32api",
        "win32con",
        "win32gui",
        "win32gui_struct",
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "scipy", "pandas", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ─────────────────────────────────────────────────────────────────────────────
# PYZ archive
# ─────────────────────────────────────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ─────────────────────────────────────────────────────────────────────────────
# EXE
# ─────────────────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SwiftShare",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,      # no black terminal window on double-click
    uac_admin=False,    # no UAC prompt needed
    onefile=True,       # single portable .exe in dist/

    # icon.ico was generated from icon.png at the top of this spec.
    # Falls back to None (default Windows icon) if icon.png was missing.
    icon=EXE_ICON,
)
