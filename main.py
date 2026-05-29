import sys
import os
import asyncio
import warnings
import logging
import argparse
from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
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
            "Logging level for the application's own loggers "
            "(``lifu.*`` and ``qt``): debug, info, warning, error, "
            "critical. Default: info."
        ),
    )
    parser.add_argument(
        "--sdk-loglevel",
        default="derive",
        type=str,
        help=(
            "Logging level for the openlifu_sdk transport logger. "
            "The SDK is a low-level component whose ``info`` and "
            "``debug`` levels are much more verbose per unit of "
            "operator-visible value than the app's own levels, so by "
            "default it is configured one step quieter than --loglevel "
            "(floored at WARNING). Pass an explicit level (debug, "
            "info, warning, error, critical) to override -- e.g. "
            "``--loglevel=info --sdk-loglevel=debug`` for a deep dive "
            "into UART traffic while keeping the app's own logs at "
            "INFO. Default: derive."
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
    # at the package level controls all of them at once.
    level_name = str(args.loglevel).upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        print(
            f"WARNING: invalid --loglevel '{args.loglevel}', falling back to INFO.",
            file=sys.stderr,
        )
        level = logging.INFO

    # Resolve --sdk-loglevel. ``derive`` (default) keeps the SDK one
    # step quieter than the app's level, with a floor of WARNING so
    # routine operation stays uncluttered even if the app is at DEBUG.
    # An explicit value (debug/info/warning/error/critical) bypasses
    # the derivation -- useful for targeted UART traces.
    sdk_arg = str(args.sdk_loglevel).lower()
    if sdk_arg == "derive":
        # One step quieter than the app, floored at WARNING.
        # logging level numbers go up as severity goes up (DEBUG=10,
        # INFO=20, WARNING=30), so "one step quieter" = +10.
        sdk_level = max(level + 10, logging.WARNING)
    else:
        sdk_level = logging.getLevelName(sdk_arg.upper())
        if not isinstance(sdk_level, int):
            print(
                f"WARNING: invalid --sdk-loglevel '{args.sdk_loglevel}', "
                "falling back to derive.",
                file=sys.stderr,
            )
            sdk_level = max(level + 10, logging.WARNING)

    # Root logging configuration. Without this, library loggers (e.g.
    # ``openlifu_sdk.io.uart`` -- the source of "TX id=... timed out"
    # WARNINGs) fall through to Python's "last resort" handler which
    # emits records as bare "LEVEL:logger:message" with no timestamp.
    # Configure root once, with a timestamped format, so every logger in
    # the process (ours + the SDK) is timestamped and consistently
    # formatted. ``force=True`` replaces any handler a prior import may
    # have installed (basicConfig is a no-op if root already has one).
    #
    # Root threshold is the most permissive of the configured levels so
    # the handler does not silently drop records that a per-logger
    # level would otherwise allow through.
    root_level = min(level, sdk_level)
    logging.basicConfig(
        level=root_level,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s [%(threadName)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    # Per-package levels: app code at --loglevel, SDK at --sdk-loglevel
    # (or its derived default). This decoupling matters because the
    # SDK's INFO/DEBUG is much more verbose per unit of operator-visible
    # value than the app's own levels -- so e.g. --loglevel=info should
    # not flood stderr with per-packet framing chatter from the
    # transport layer.
    logging.getLogger("lifu").setLevel(level)
    logging.getLogger("openlifu_sdk").setLevel(sdk_level)
    # Tame known-noisy third-party libraries that would otherwise drown
    # the operator at INFO/DEBUG (Qt internals, PIL, matplotlib, etc.).
    for noisy in ("PIL", "matplotlib", "asyncio", "qasync"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    # Replace Qt's default message handler. Two goals:
    #   1. Drop QML ``console.log(...)`` (QtDebugMsg) entirely -- it's
    #      unstructured developer-trace clutter that gets emitted with a
    #      ``qml:`` prefix bypassing Python's logging config (no
    #      timestamps, no level filtering). Any QML-side event worth
    #      surfacing has an equivalent Python-side INFO log in
    #      lifu_connector.* mixins.
    #   2. Funnel real Qt warnings/criticals through the Python logging
    #      pipeline so they pick up timestamps and the same formatting
    #      as everything else (and are searchable in log files).
    _qt_logger = logging.getLogger("qt")
    _QT_LEVEL_MAP = {
        QtMsgType.QtDebugMsg: None,      # drop entirely
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    def _qt_message_handler(mode, ctx, msg):
        py_level = _QT_LEVEL_MAP.get(mode)
        if py_level is None:
            return  # silently drop QML console.log noise
        # Include the Qt logging category if present (e.g. ``qt.qpa.*``)
        # so filterable categories remain visible.
        category = getattr(ctx, "category", None) or ""
        if category and category != "default":
            _qt_logger.log(py_level, "[%s] %s", category, msg)
        else:
            _qt_logger.log(py_level, "%s", msg)
    qInstallMessageHandler(_qt_message_handler)

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
