import sys
import os
import asyncio
import warnings
import logging
import argparse
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine
from qasync import QEventLoop
from lifu.lifu_connector import LIFUConnector, MIN_SDK_VERSION, check_sdk_version
from lifu.lifu_support import LIFUSupportConnector
from pathlib import Path

from version import get_version

APP_VERSION = get_version()

# run with lab supply
# set PYTHONPATH=%cd%\src;%PYTHONPATH%
# python main.py --hv-test-mode 

logger = logging.getLogger(__name__)

def resource_path(rel: str) -> str:
    import sys, os
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(sys.executable if getattr(sys,"frozen",False) else __file__)))
    return os.path.join(base, rel)

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="LIFU Application")
    parser.add_argument(
        "--hv-test-mode",
        action="store_true",
        help="Enable HV test mode for LIFUConnector",
    )
    parser.add_argument(
        "--simulate",
        nargs="?",
        type=int,
        const=1,
        default=None,
        metavar="N",
        help=(
            "Run against an in-memory simulated device instead of real "
            "hardware. Optional N specifies the number of simulated TX "
            "modules (default 1)."
        ),
    )
    parser.add_argument(
        "--loglevel",
        default="info",
        type=str,
        help=(
            "Logging level for the lifu_connector logger "
            "(debug, info, warning, error, critical). Default: info."
        ),
    )
    parser.add_argument(
        "--sdk-loglevel",
        default=None,
        type=str,
        help=(
            "Logging level for the openlifu_sdk package logger. "
            "Defaults to the value of --loglevel. Useful for turning on "
            "verbose SDK chatter independently of the app's own logs."
        ),
    )
    return parser.parse_args()


# Tab id -> visible label, icon glyph, and QML page path. Consumed by
# SidebarMenu.qml via the appTabs context property.
TAB_DEFINITIONS = {
    "controller":   {"label": "Controller",  "icon": "\ueb34", "page": "pages/Controller.qml"},
    "transmitter":  {"label": "Transmitter", "icon": "\ueab9", "page": "pages/Transmitter.qml"},
    "console":      {"label": "Console",     "icon": "\ueaae", "page": "pages/Console.qml"},
    "testing":      {"label": "Verification","icon": "\ueb2f", "page": "pages/Testing.qml"},
    "settings":     {"label": "Settings",    "icon": "\ueabf", "page": "pages/Settings.qml"},
    "support":      {"label": "Support",     "icon": "\ueaaf", "page": "pages/Support.qml"},
}

# Engineering tabs shown in the sidebar.
ENGINEERING_TABS = ["controller", "transmitter", "console", "testing", "support", "settings"]


def build_app_tabs():
    """Return a list of tab dicts suitable for QML."""
    return [
        {"id": tid, **TAB_DEFINITIONS[tid]}
        for tid in ENGINEERING_TABS
        if tid in TAB_DEFINITIONS
    ]

def main():
    args = parse_arguments()

    # Apply the requested log level to the lifu package logger before
    # any of its modules log anything interesting. The handler is
    # attached to the ``lifu`` package logger in ``lifu_connector.py``
    # so every submodule (lifu.lifu_controller, lifu.lifu_settings,
    # lifu.lifu_transmitter, ...) inherits it; setting the level here
    # at the package level controls all of them at once. The same
    # handler is shared with the ``openlifu_sdk`` logger so SDK output
    # is routed to the same terminal stream.
    def _resolve_level(name: str, fallback: int) -> int:
        resolved = logging.getLevelName(str(name).upper())
        if not isinstance(resolved, int):
            print(
                f"WARNING: invalid log level '{name}', falling back to "
                f"{logging.getLevelName(fallback)}.",
                file=sys.stderr,
            )
            return fallback
        return resolved

    level = _resolve_level(args.loglevel, logging.INFO)
    logging.getLogger("lifu").setLevel(level)

    sdk_level = _resolve_level(args.sdk_loglevel, level) if args.sdk_loglevel is not None else level
    logging.getLogger("openlifu_sdk").setLevel(sdk_level)

    # Tell Windows to treat this as its own app (not python.exe) so the
    # taskbar shows our icon instead of the Python icon.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "openwater.openlifu.testapp"
            )
        except Exception:
            pass

    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Material"
    os.environ["QT_QUICK_CONTROLS_MATERIAL_THEME"] = "Dark"
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"

    app = QGuiApplication(sys.argv)

    # Verify the installed openlifu-sdk meets our minimum required version
    # before we start touching hardware. Editable installs from GitHub may
    # report PEP 440 local versions (e.g. "1.0.6.dev3+g1a2b3c4"); the
    # parser in check_sdk_version handles those.
    sdk_ok, sdk_version, sdk_message = check_sdk_version(MIN_SDK_VERSION)
    if sdk_ok:
        logger.info(sdk_message)
    else:
        logger.error(sdk_message)
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            # QGuiApplication doesn't own QWidget; spin up a temporary
            # QApplication only for the dialog. If QtWidgets isn't
            # available (slim install) we still have the stderr path.
            _msg_app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "Incompatible openlifu-sdk version",
                f"{sdk_message}\n\nThe application will now exit.",
            )
        except Exception:
            print(f"ERROR: {sdk_message}", file=sys.stderr)
        sys.exit(2)

    # Use the multi-size .ico on Windows so the taskbar/alt-tab/title-bar
    # each pick an appropriately sized image. A single large .png often
    # fails to render in the 16x16 taskbar slot.
    app_icon = QIcon(resource_path("assets/images/favicon.ico"))
    app.setWindowIcon(app_icon)

    engine = QQmlApplicationEngine()

    # Initialize the connector. --simulate swaps in an in-memory fake of
    # LIFUInterface so the rest of the app exercises real code paths
    # without requiring USB hardware.
    simulating = args.simulate is not None
    if simulating:
        from lifu.simulated_lifu_connector import SimulatedLIFUConnector
        sim_modules = args.simulate
        print(f"Initializing simulated connector with {sim_modules} module(s)...")

        lifu_connector = SimulatedLIFUConnector(num_modules=sim_modules)
    else:
        lifu_connector = LIFUConnector(hv_test_mode=args.hv_test_mode)
    lifu_support_connector = LIFUSupportConnector(interface=lifu_connector.interface)

    # Expose to QML
    engine.rootContext().setContextProperty("LIFUConnector", lifu_connector)
    engine.rootContext().setContextProperty("LIFUSupportConnector", lifu_support_connector)
    engine.rootContext().setContextProperty("appVersion", APP_VERSION)
    engine.rootContext().setContextProperty("appSimulate", simulating)
    engine.rootContext().setContextProperty("appTabs", build_app_tabs())
    app.setProperty("appVersion", APP_VERSION)

    engine.load(resource_path("main.qml"))

    if not engine.rootObjects():
        print("Error: Failed to load QML file")
        sys.exit(-1)

    # Frameless QQuickWindows on Windows don't always inherit the
    # QGuiApplication window icon for the taskbar. Push it explicitly.
    for obj in engine.rootObjects():
        try:
            obj.setIcon(app_icon)
        except AttributeError:
            pass

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    def handle_exit():
        """Stop monitoring and cancel pending tasks synchronously on app quit."""
        logger.info("Application closing...")
        lifu_connector.close()
        pending_tasks = [t for t in asyncio.all_tasks() if not t.done()]
        if pending_tasks:
            logger.info(f"Cancelling {len(pending_tasks)} pending tasks...")
            for task in pending_tasks:
                task.cancel()

        engine.deleteLater()

    # Connect shutdown process to app quit event
    app.aboutToQuit.connect(handle_exit)

    try:
        with loop:
            loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Application interrupted.")
    finally:
        loop.close()

if __name__ == "__main__":
    main()
