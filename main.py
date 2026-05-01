import sys
import os
import asyncio
import warnings
import logging
import argparse
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine
from qasync import QEventLoop
from lifu_connector import LIFUConnector, MIN_SDK_VERSION, check_sdk_version
from pathlib import Path

from version import get_version

APP_VERSION = get_version()

# run with lab supply
# set PYTHONPATH=%cd%\src;%PYTHONPATH%
# python main.py --hv-test-mode 

logger = logging.getLogger(__name__)

# Suppress PyQt6 DeprecationWarnings related to SIP
warnings.simplefilter("ignore", DeprecationWarning)

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
    return parser.parse_args()

def main():
    args = parse_arguments()

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

    # Initialize LIFUConnector with hv_test_mode from command-line argument
    lifu_connector = LIFUConnector(hv_test_mode=args.hv_test_mode)
    
    # Expose to QML
    engine.rootContext().setContextProperty("LIFUConnector", lifu_connector)
    engine.rootContext().setContextProperty("appVersion", APP_VERSION)
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
