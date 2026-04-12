# top of spec
import os
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

APP_NAME = "TestApp"
ENTRY = "main.py"
ICON_FILE = os.path.abspath("assets/images/favicon.ico")

datas = []
hidden = []
binaries = []

# --- your existing resource folders (keep what you already had) ---
for item in ("main.qml", "pinmap_1x.json", "pinmap_2x.json"):
    if os.path.exists(item):
        datas.append((item, "."))
for folder in ("pages", "components", "assets", "models", "preset_templates"):
    if os.path.isdir(folder):
        datas.append((folder, folder))

# --- PyQt6 (keep as before) ---
qt_datas, qt_bins, qt_hidden = collect_all("PyQt6")
datas += qt_datas
binaries += qt_bins
hidden  += qt_hidden
hidden  += collect_submodules("PyQt6")
hidden  += ["qasync"]

# --- ✅ add olifu explicitly ---
om_datas, om_bins, om_hidden = collect_all("olifu")
datas   += om_datas
binaries += om_bins
hidden  += om_hidden

# --- openlifu_sdk (ships libusb-1.0.dll for win32/win64) ---
sdk_datas, sdk_bins, sdk_hidden = collect_all("openlifu_sdk")
datas    += sdk_datas
binaries += sdk_bins
hidden   += sdk_hidden

# --- force include pyserial dependency ---
hidden += [
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "usb",
    "usb.core",
    "usb.util"
]

a = Analysis(
    [ENTRY],
    pathex=[],                      # you can leave this empty now
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=['PySide6','shiboken6','PySide2','PyQt5'],  # avoid mixed Qt
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME,
              console=False, icon=ICON_FILE, upx=True)
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name=f"{APP_NAME}_console",
              console=True,  icon=ICON_FILE, upx=True)

coll = COLLECT(exe_gui, exe_cli, a.binaries, a.zipfiles, a.datas,
               strip=False, upx=True, upx_exclude=[], name=APP_NAME)