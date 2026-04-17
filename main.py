import sys
import os
import asyncio
import warnings
import logging
import argparse
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine
from qasync import QEventLoop
from pathlib import Path

from version import get_version

APP_VERSION = get_version()

# ---------------------------------------------------------------------------
# Logging – configure before any other imports that use the logging framework
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# run with lab supply:
#   set PYTHONPATH=%cd%\src;%PYTHONPATH%
#   python main.py --hv-test-mode

# Suppress PyQt6 DeprecationWarnings related to SIP
warnings.simplefilter("ignore", DeprecationWarning)

# ---------------------------------------------------------------------------
# Make openlifu_sdk importable when running directly from source
# (no-op when the package is installed in the active venv)
# ---------------------------------------------------------------------------
_SDK_SRC = Path(__file__).parent.parent / "lifu" / "openlifu-sdk" / "src"
if _SDK_SRC.is_dir() and str(_SDK_SRC) not in sys.path:
    sys.path.insert(0, str(_SDK_SRC))
    logger.debug("Added SDK source path: %s", _SDK_SRC)

from lifu_connector import LIFUConnector  # noqa: E402 – must come after path setup

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

    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Material"
    os.environ["QT_QUICK_CONTROLS_MATERIAL_THEME"] = "Dark"
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"

    app = QGuiApplication(sys.argv)
    app.setWindowIcon(QIcon("assets/images/favicon.png"))

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

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def main_async():
        """Start LIFU monitoring before event loop runs."""
        logger.info("Starting LIFU monitoring...")
        await lifu_connector.start_monitoring()

    def handle_exit():
        """Stop monitoring and cancel pending tasks synchronously on app quit."""
        logger.info("Application closing...")
        lifu_connector.stop_monitoring()

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
            loop.create_task(main_async())  # Schedule monitoring as a background task
            loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Application interrupted.")
    finally:
        loop.close()

if __name__ == "__main__":
    main()
