# PyInstaller spec for the OpenLIFU Test App.
#
# Always builds two EXEs that share a single set of Qt/SDK assets:
#   - TestApp.exe          (default GUI, no console window)
#   - TestApp_console.exe  (same app, console attached for debugging)
#
# Optional: when the environment variable OPENLIFU_CONTEXT is set
# (e.g. "vet", "diathermy"), a third kiosk EXE is also built that
# auto-launches into operator-kiosk mode for that context:
#   - OpenLIFUDeviceController-<context>.exe
#
# This keeps the GitHub release pipeline vendor-neutral by default; the
# context-specific EXE is opt-in via a workflow_dispatch input. See
# .github/workflows/release-build.yml.
import os
import tempfile
from PyInstaller.utils.hooks import collect_all, collect_submodules

APP_NAME = "TestApp"
ENTRY = "main.py"
ICON_FILE = os.path.abspath("assets/images/favicon.ico")

# Context-specific kiosk build is gated on the OPENLIFU_CONTEXT env var.
# Empty / unset means "ship the default release EXEs only".
CONTEXT = os.environ.get("OPENLIFU_CONTEXT", "").strip().lower()

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
    "preset_settings",
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

# ---- optional context-specific kiosk analysis -----------------------------

context_exes = []
context_analyses = []
if CONTEXT:
    # Generate a tiny wrapper script at spec-evaluation time that
    # injects ``--context=<CONTEXT>`` into sys.argv before delegating
    # to main.main. This avoids checking a separate file into the repo
    # for every supported context.
    wrapper_src = (
        '"""Auto-generated kiosk entry point for context: {ctx}\n'
        'Created by OpenLIFU-TestApp.spec from $OPENLIFU_CONTEXT.\n'
        '"""\n'
        'import sys\n'
        'if not any(a == "--context" or a.startswith("--context=") for a in sys.argv):\n'
        '    sys.argv.insert(1, "--context={ctx}")\n'
        'from main import main\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    ).format(ctx=CONTEXT)
    wrapper_path = os.path.join(
        tempfile.gettempdir(), f"_openlifu_kiosk_{CONTEXT}.py"
    )
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper_src)

    a_kiosk = Analysis(
        [wrapper_path],
        pathex=[os.path.abspath(".")],
        binaries=binaries,
        datas=datas,
        hiddenimports=hidden,
        excludes=["PySide6", "shiboken6", "PySide2", "PyQt5"],
        noarchive=False,
        optimize=0,
    )
    # MERGE deduplicates shared modules/binaries between the two
    # analyses so COLLECT ships only one copy of Qt, openlifu_sdk, etc.
    MERGE(
        (a_main, "main", APP_NAME),
        (a_kiosk, os.path.basename(wrapper_path)[:-3],
         f"OpenLIFUDeviceController-{CONTEXT}"),
    )
    context_analyses.append(a_kiosk)

# PYZ/EXE must be built after MERGE (if any) so the merged module lists
# end up in the right archive.

pyz_main = PYZ(a_main.pure)

exe_gui = EXE(
    pyz_main, a_main.scripts, [], exclude_binaries=True,
    name=APP_NAME, console=False, icon=ICON_FILE, upx=True,
)
exe_cli = EXE(
    pyz_main, a_main.scripts, [], exclude_binaries=True,
    name=f"{APP_NAME}_console", console=True, icon=ICON_FILE, upx=True,
)

for a_kiosk in context_analyses:
    pyz_kiosk = PYZ(a_kiosk.pure)
    exe_kiosk = EXE(
        pyz_kiosk, a_kiosk.scripts, [], exclude_binaries=True,
        name=f"OpenLIFUDeviceController-{CONTEXT}",
        console=False, icon=ICON_FILE, upx=True,
    )
    context_exes.append(exe_kiosk)


# ---- COLLECT (shared bundle) ----------------------------------------------

_collect_args = [exe_gui, exe_cli]
_collect_args.extend(context_exes)
_collect_args.append(a_main.binaries)
_collect_args.append(a_main.zipfiles)
_collect_args.append(a_main.datas)
for a in context_analyses:
    _collect_args.append(a.binaries)
    _collect_args.append(a.zipfiles)
    _collect_args.append(a.datas)

coll = COLLECT(
    *_collect_args,
    strip=False, upx=True, upx_exclude=[], name=APP_NAME,
)
