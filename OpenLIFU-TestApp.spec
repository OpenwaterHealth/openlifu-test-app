# PyInstaller spec for the OpenLIFU Test App.
#
# Builds three EXEs sharing a single set of Qt/SDK assets:
#   - TestApp.exe          (default GUI, no console window)
#   - TestApp_console.exe  (same app, console attached for debugging)
#   - OpenLIFU_Vet.exe     (launches directly into Vet-mode kiosk UI)
#
# The Vet variant is built from a thin wrapper (``main_vet.py``) that
# pre-injects ``--vet`` into ``sys.argv`` before calling ``main.main``.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

APP_NAME = "TestApp"
ENTRY = "main.py"
VET_ENTRY = "main_vet.py"
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
    "preset_vet_settings",
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
# ``__init__`` to be bundled, not just its submodules. Listing only the
# submodules in ``hiddenimports`` ships them but ALSO causes PyInstaller
# to skip the package init in some configurations, leaving you with an
# empty ``serial`` module at runtime. ``collect_all`` pulls the init,
# all submodules, and any data files together.
ser_datas, ser_bins, ser_hidden = collect_all("serial")
datas    += ser_datas
binaries += ser_bins
hidden   += ser_hidden

# pyusb -- same story; the package init exposes ``usb.core`` etc.
usb_datas, usb_bins, usb_hidden = collect_all("usb")
datas    += usb_datas
binaries += usb_bins
hidden   += usb_hidden

# ---- analyses, PYZ, and EXEs ----------------------------------------------

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

a_vet = Analysis(
    [VET_ENTRY],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["PySide6", "shiboken6", "PySide2", "PyQt5"],
    noarchive=False,
    optimize=0,
)

# MERGE deduplicates shared modules/binaries between the two analyses
# so COLLECT ships only one copy of Qt, openlifu_sdk, etc.
MERGE((a_main, "main", APP_NAME),
      (a_vet, "main_vet", "OpenLIFU_Vet"))

pyz_main = PYZ(a_main.pure)
pyz_vet  = PYZ(a_vet.pure)

exe_gui = EXE(
    pyz_main, a_main.scripts, [], exclude_binaries=True,
    name=APP_NAME, console=False, icon=ICON_FILE, upx=True,
)
exe_cli = EXE(
    pyz_main, a_main.scripts, [], exclude_binaries=True,
    name=f"{APP_NAME}_console", console=True, icon=ICON_FILE, upx=True,
)
exe_vet = EXE(
    pyz_vet, a_vet.scripts, [], exclude_binaries=True,
    name="OpenLIFU_Vet", console=True, icon=ICON_FILE, upx=True,
)

coll = COLLECT(
    exe_gui, exe_cli, exe_vet,
    a_main.binaries, a_main.zipfiles, a_main.datas,
    a_vet.binaries, a_vet.zipfiles, a_vet.datas,
    strip=False, upx=True, upx_exclude=[], name=APP_NAME,
)
