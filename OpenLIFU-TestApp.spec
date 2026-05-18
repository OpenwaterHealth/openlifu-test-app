# PyInstaller spec for the OpenLIFU Test App.
#
# Always builds two EXEs that share a single set of Qt/SDK assets:
#   - TestApp.exe          (default GUI, no console window)
#   - TestApp_console.exe  (same app, console attached for debugging)
#
# Operator-kiosk builds live in a separate repo
# (openlifu-operator-interface); this spec is engineering-only.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

APP_NAME = "TestApp"
ENTRY = "main.py"
ICON_FILE = os.path.abspath("assets/images/favicon.ico")

# ---- bundled data + hidden imports ----------------------------------------
datas = []
hidden = []
binaries = []

# Top-level data files and folders (kept relative to the dist root).
for item in ("main.qml", "pinmap_1x.json", "pinmap_2x.json"):
    if os.path.exists(item):
        datas.append((item, "."))
for folder in (
    "pages",
    "components",
    "assets",
    "preset_templates",
    "preset_solutions",
):
    if os.path.isdir(folder):
        datas.append((folder, folder))

# PyQt6 -- bundle the full Qt runtime so the frozen app has its plugins
# and QML imports available.
qt_datas, qt_bins, qt_hidden = collect_all("PyQt6")
datas    += qt_datas
binaries += qt_bins
hidden   += qt_hidden
hidden   += collect_submodules("PyQt6")
hidden   += ["qasync"]

# openlifu_sdk -- ships libusb-1.0.dll for win32/win64 plus several
# pure-Python submodules that PyInstaller can't always discover.
sdk_datas, sdk_bins, sdk_hidden = collect_all("openlifu_sdk")
datas    += sdk_datas
binaries += sdk_bins
hidden   += sdk_hidden

# pyserial -- importing the ``serial`` package directly (e.g. for
# ``serial.Serial`` / ``serial.SerialException``) requires its
# ``__init__`` to be bundled, not just its submodules. ``collect_all``
# pulls the init, all submodules, and any data files together.
ser_datas, ser_bins, ser_hidden = collect_all("serial")
datas    += ser_datas
binaries += ser_bins
hidden   += ser_hidden

# pyusb -- same story; the package init exposes ``usb.core`` etc.
usb_datas, usb_bins, usb_hidden = collect_all("usb")
datas    += usb_datas
binaries += usb_bins
hidden   += usb_hidden


# ---- main analysis (shared by GUI + console EXEs) -------------------------

a_main = Analysis(
    [ENTRY],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["PySide6", "shiboken6", "PySide2", "PyQt5"],
    noarchive=False,
    optimize=0,
)

pyz_main = PYZ(a_main.pure)

exe_gui = EXE(
    pyz_main, a_main.scripts, [], exclude_binaries=True,
    name=APP_NAME, console=False, icon=ICON_FILE, upx=True,
)
exe_cli = EXE(
    pyz_main, a_main.scripts, [], exclude_binaries=True,
    name=f"{APP_NAME}_console", console=True, icon=ICON_FILE, upx=True,
)


# ---- COLLECT (shared bundle) ----------------------------------------------

coll = COLLECT(
    exe_gui, exe_cli,
    a_main.binaries, a_main.zipfiles, a_main.datas,
    strip=False, upx=True, upx_exclude=[], name=APP_NAME,
)
