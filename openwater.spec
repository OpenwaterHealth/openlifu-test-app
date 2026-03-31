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
for item in ("main.qml",):
    if os.path.exists(item):
        datas.append((item, "."))
for folder in ("pages", "components", "assets", "models"):
    if os.path.isdir(folder):
        datas.append((folder, folder))

# --- PyQt6 (keep as before) ---
qt_datas, qt_bins, qt_hidden = collect_all("PyQt6")
datas += qt_datas
binaries += qt_bins
hidden  += qt_hidden
hidden  += collect_submodules("PyQt6")
hidden  += ["qasync"]

# --- ✅ add openlifu_sdk explicitly ---
om_datas, om_bins, om_hidden = collect_all("openlifu_sdk")
datas   += om_datas
binaries += om_bins
hidden  += om_hidden
hidden  += collect_submodules("openlifu_sdk")

# --- libusb DLLs: must be placed at openlifu_sdk/libusb/<arch>/ so that
#     LIFUDFU._find_bundled_libusb_dll() can locate them via Path(__file__)
#     when running as a frozen exe. Add as datas (not binaries) to preserve
#     the subdirectory structure under _MEIPASS. ---
import struct as _struct
_libusb_src = os.path.abspath(
    os.path.join(".venv", "Lib", "site-packages", "openlifu_sdk", "libusb")
)
for _arch in ("win64", "win32"):
    _dll = os.path.join(_libusb_src, _arch, "libusb-1.0.dll")
    if os.path.isfile(_dll):
        datas.append((_dll, os.path.join("openlifu_sdk", "libusb", _arch)))

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
