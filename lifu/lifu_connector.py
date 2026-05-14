from turtle import mode

from PyQt6.QtCore import QObject, QRecursiveMutex, QThread, QTimer, pyqtSignal, pyqtProperty, pyqtSlot
import asyncio
import contextlib
import logging
import os
import shutil
import sys
import glob

def _base_path():
    """Return the directory containing bundled data files.
    Works in both frozen (PyInstaller) and normal Python execution.
    In source mode this is the project root (the parent of the
    ``lifu/`` package), where pinmaps and preset folders live."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import threading
import time
from datetime import datetime, timedelta
import numpy as np
import re
import base58
import json
import copy
from plot.plot import generate_ultrasound_plot_from_solution  # Import the function directly
from test_reports.test_reports import read_test_report, test_report_to_config, check_config_against_device
from openlifu_sdk.io import LIFUInterface, LIFUInterfaceStatus
from openlifu_sdk.io.LIFUConfig import HW_ID_DATA_LENGTH
from openlifu_sdk.io.exceptions import (
    LIFUError,
    LIFUCommunicationError,
    LIFUDeviceError,
    LIFUHVSettleError,
    LIFUNotConnectedError,
    LIFUProtocolError,
    LIFUSolutionError,
    LIFUSonicationError,
)

# Shim: re-export support connector so callers can import from either module.
#from lifu.lifu_support import LIFUSupportConnector  # noqa: F401

# import verification-tests
from openlifu_verification.prodreqs_base_class import *
from openlifu_verification.prodreqs_tx_long_verification_test import TransmitterHeatingPlaceholder, parse_arguments
from openlifu_verification.prodreqs_voltage_accuracy_test import VoltageAccuracyTest, TEST_VOLTAGES
from openlifu_verification.prodreqs_tx_short_verification_test import TransmitterShortVerificationTest
from openlifu_verification.prodreqs_run_indefinitely_test import TransmitterIndefiniteRun


# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

# Uncomment to activate logging from the sdk
#sdklogger = logging.getLogger('openlifu_sdk.io')
#sdklogger.setLevel(logging.INFO)
#sdklogger.addHandler(ch)


# Minimum required openlifu-sdk version. Bump this whenever the test app
# starts depending on a new SDK feature/fix. Keep in sync with the
# `openlifu-sdk>=` pin in pyproject.toml.
MIN_SDK_VERSION = "1.0.7"


def _parse_sdk_version(version_str: str):
    """Parse an openlifu-sdk version string into a comparable tuple.

    Accepts PEP 440 strings (preferred path via ``packaging.version.Version``)
    and falls back to a regex on the leading ``MAJOR.MINOR.PATCH`` so that
    locally-installed editable builds from GitHub (e.g. setuptools_scm
    versions like ``1.0.6.dev3+g1a2b3c4`` or ``1.0.7.dev0+g....d20260501``)
    still compare cleanly against ``MIN_SDK_VERSION``.

    Returns ``None`` if no leading numeric version can be extracted.
    """
    if not version_str:
        return None
    try:
        from packaging.version import Version, InvalidVersion
        try:
            return Version(version_str)
        except InvalidVersion:
            pass
    except ImportError:
        pass
    m = re.match(r"\s*(\d+)\.(\d+)(?:\.(\d+))?", str(version_str))
    if not m:
        return None
    return tuple(int(p) if p is not None else 0 for p in m.groups())


def check_sdk_version(min_version: str = MIN_SDK_VERSION):
    """Verify the installed openlifu-sdk meets ``min_version``.

    Returns a tuple ``(ok, installed_version, message)``. ``ok`` is True
    when the installed version parses and is ``>= min_version``. The
    message is human-readable and suitable for surfacing to the user.
    """
    try:
        installed = LIFUInterface.get_sdk_version()
    except Exception as e:
        return False, "unknown", f"Could not determine openlifu-sdk version: {e}"

    parsed_installed = _parse_sdk_version(installed)
    parsed_min = _parse_sdk_version(min_version)
    if parsed_installed is None or parsed_min is None:
        return (
            False,
            installed,
            f"Could not parse openlifu-sdk version '{installed}' "
            f"(minimum required: {min_version}).",
        )
    # ``packaging.version.Version`` and the tuple fallback both support <.
    # Mixing them shouldn't happen (both branches use the same parser),
    # but guard anyway.
    try:
        ok = parsed_installed >= parsed_min
    except TypeError:
        return (
            False,
            installed,
            f"Could not compare openlifu-sdk version '{installed}' "
            f"to minimum '{min_version}'.",
        )
    if ok:
        return True, installed, f"openlifu-sdk {installed} (>= {min_version})"
    return (
        False,
        installed,
        f"openlifu-sdk {installed} is older than the required minimum {min_version}. "
        f"Please upgrade with: pip install --upgrade 'openlifu-sdk>={min_version}'",
    )

def _parse_tx_module(target: str):
    """Parse a target string like 'tx 0', 'tx_0', 'tx0' into an integer module index.
    Returns None if the target is not a TX target (e.g. 'console').
    """
    import re as _re
    m = _re.match(r'^tx[\s_]?(\d+)$', target.strip().lower())
    if m:
        return int(m.group(1))
    return None


# Define system states.
#
# These are app-level states surfaced to QML and DO NOT mirror
# ``LIFUInterfaceStatus`` from the SDK. We intentionally collapse the previous
# CONFIGURED/READY split (which conflated HV-rail readiness with solution
# configuration) into a single READY state that means "TX has a valid
# solution loaded". HV connection / energization is reported separately via
# ``hvConnected`` and ``powerStatusReceived``.
DISCONNECTED = 0
CONNECTED = 1            # TX device connected, no solution configured
READY = 2                # TX configured with a solution; ready to start
RUNNING = 3              # Sonication or verification test in progress
TEST_SCRIPT_READY = 4    # HV connected without TX (verification scripts)

# HV enable modes:
#   AUTO          - HV is held on whenever the TX has a solution loaded
#                   (state >= READY) and turned off whenever it isn't.
#   ON            - HV is held on continuously (requires HV connected).
#   OFF           - HV is held off; sonication is blocked.
#   WHILE_RUNNING - HV is energized only while sonication is actively running
#                   (turned on at start_sonication, off at stop_sonication).
#                   This was the old AUTO behavior.
HV_EN_AUTO = 0
HV_EN_ON = 1
HV_EN_OFF = 2
HV_EN_WHILE_RUNNING = 3
HV_EN_MODES = {
    HV_EN_AUTO: "AUTO",
    HV_EN_ON: "ON",
    HV_EN_OFF: "OFF",
    HV_EN_WHILE_RUNNING: "WHILE_RUNNING",
}

# Thermal-management thresholds for the TX. Values in degrees C, applied
# to the *hottest* module's reported temperature. Centralizing here keeps
# the QML layer free of safety policy.
THERMAL_COOLING_THRESHOLD_C = 50.0
THERMAL_SHUTDOWN_THRESHOLD_C = 75.0

#
SPEED_OF_SOUND = 1500  # Speed of sound in m/s, used for time-of-flight calculations
NUM_ELEMENTS_PER_MODULE = 64  # Assuming each module has 64 elements, adjust as needed

# How many extra times a device-write should be retried after a
# transient ``LIFUCommunicationError`` (timeout) before surfacing the
# error to the user. With MAX_TIMEOUT_RETRIES=3 we attempt a write up
# to 4 times total (1 initial + 3 retries); the user only sees the
# popup once we've failed more than ``MAX_TIMEOUT_RETRIES`` times in
# a row.
MAX_TIMEOUT_RETRIES = 3


# =============================================================================
# Run-scoped session logging
# =============================================================================
#
# A run-scoped ``logging.FileHandler`` attached to the root logger captures
# every module's output (lifu_connector + openlifu_sdk + verification +
# anything else using ``logging``) for the duration of one run.
# A small ``logging.Filter`` adds an ``elapsed`` attribute so the format
# string can include time-since-start without subclassing ``Formatter``.
# Unhandled exceptions are routed through ``sys.excepthook`` -> ``logger``
# while a run is active so a stray traceback ends up in the log too.

RUN_LOG_FORMAT = "%(asctime)s [+%(elapsed)8.3fs] %(levelname)-7s %(name)s: %(message)s"
RUN_LOG_DATEFMT = "%H:%M:%S"


class _SessionLogger:
    """Persisted session settings + run-scoped log file management."""

    SETTINGS_FILENAME = "session_settings.json"
    DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "openlifu_logs")

    def __init__(self, user_data_root):
        self._user_data_root = user_data_root
        self.session_name = ""
        self.save_logs = True
        self.log_folder = self.DEFAULT_FOLDER
        self._handler = None
        self._start_wall = 0.0
        self._prev_excepthook = None
        self._load_settings()

    # ---- settings persistence -------------------------------------------------

    def _settings_path(self):
        return os.path.join(self._user_data_root, self.SETTINGS_FILENAME)

    def _load_settings(self):
        try:
            with open(self._settings_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        # session_name is intentionally not persisted; users start each
        # app run with a blank name.
        if isinstance(data.get("save_logs"), bool):
            self.save_logs = data["save_logs"]
        folder = data.get("log_folder")
        if isinstance(folder, str) and folder.strip():
            self.log_folder = folder

    def save_settings(self):
        try:
            os.makedirs(self._user_data_root, exist_ok=True)
            with open(self._settings_path(), 'w', encoding='utf-8') as f:
                json.dump({
                    "save_logs": self.save_logs,
                    "log_folder": self.log_folder,
                }, f, indent=2)
        except OSError as e:
            logger.warning(f"Session log: failed to persist settings: {e}")

    @staticmethod
    def sanitize_id(name):
        """Snake-case an arbitrary session name; default ``"session"`` if empty."""
        s = re.sub(r'[^A-Za-z0-9]+', '_', str(name or "").strip()).strip('_').lower()
        return s if s else "session"

    @property
    def session_id(self):
        return self.sanitize_id(self.session_name)

    @property
    def is_active(self):
        return self._handler is not None

    @property
    def current_path(self):
        return self._handler.baseFilename if self._handler is not None else None

    # ---- run lifecycle --------------------------------------------------------

    def _next_run_number(self, datestr, session_id):
        """Return the next ``runNN`` index for the given date + session id."""
        try:
            entries = os.listdir(self.log_folder)
        except OSError:
            return 1
        prefix = f"{datestr}_{session_id}_run"
        max_n = 0
        for entry in entries:
            if not entry.startswith(prefix):
                continue
            m = re.match(r'^(\d+)', entry[len(prefix):])
            if m:
                max_n = max(max_n, int(m.group(1)))
        return max_n + 1

    def begin_run(self):
        """Attach a file handler to the root logger for this run.

        Returns the absolute log file path, or ``None`` if logging is
        disabled or the file could not be opened. Idempotent if a run
        log is already active.
        """
        if self.is_active:
            return self.current_path
        if not self.save_logs:
            return None
        self._start_wall = time.time()
        wall_dt = datetime.fromtimestamp(self._start_wall)
        sid = self.session_id
        try:
            os.makedirs(self.log_folder, exist_ok=True)
        except OSError as e:
            logger.error(f"Session log: cannot create '{self.log_folder}': {e}")
            return None
        run_num = self._next_run_number(wall_dt.strftime("%Y%m%d"), sid)
        filename = (f"{wall_dt.strftime('%Y%m%d')}_{sid}_run{run_num:02d}_"
                    f"{wall_dt.strftime('%H_%M_%S')}.log")
        path = os.path.join(self.log_folder, filename)
        try:
            handler = logging.FileHandler(path, mode='w', encoding='utf-8')
        except OSError as e:
            logger.error(f"Session log: cannot open '{path}': {e}")
            return None
        # Inject ``elapsed`` (seconds since run start) onto every record
        # so the format string can render it without a custom Formatter.
        # Also gate down third-party SDK chatter to WARNING+ so
        # openlifu_sdk's verbose DEBUG output doesn't drown out our own
        # log lines in the file.
        start_wall = self._start_wall
        def _run_log_filter(record):
            if (record.name == "openlifu_sdk"
                    or record.name.startswith("openlifu_sdk.")):
                if record.levelno < logging.WARNING:
                    return False
            record.elapsed = max(0.0, record.created - start_wall)
            return True
        handler.addFilter(_run_log_filter)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(RUN_LOG_FORMAT, datefmt=RUN_LOG_DATEFMT))
        # Make sure the root logger lets DEBUG records through to our
        # handler (handlers can only see records the logger admits).
        # Other handlers have their own ``setLevel`` so they aren't
        # affected by this.
        root = logging.getLogger()
        if root.level == logging.NOTSET or root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        self._handler = handler
        # Route uncaught exceptions through logging while the run is
        # active so any stray traceback ends up in the file.
        self._prev_excepthook = sys.excepthook
        sys.excepthook = self._log_uncaught
        return path

    def _log_uncaught(self, exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        if self._prev_excepthook is not None:
            self._prev_excepthook(exc_type, exc_value, exc_tb)

    def end_run(self):
        """Detach and close the file handler. Returns the closed path."""
        if self._handler is None:
            return None
        path = self._handler.baseFilename
        root = logging.getLogger()
        try:
            root.removeHandler(self._handler)
            self._handler.close()
        except Exception:
            pass
        self._handler = None
        if self._prev_excepthook is not None:
            sys.excepthook = self._prev_excepthook
            self._prev_excepthook = None
        return path


class _TelemetryPollThread(QThread):
    """QThread that polls hardware telemetry every 1 second.

    Using QThread (not threading.Thread) ensures Qt's cross-thread queued
    signal delivery works correctly so that signals emitted here are
    reliably dispatched to the main-thread event loop and received by QML.
    """

    def __init__(self, connector):
        super().__init__()
        self._connector = connector
        self._stop_event = threading.Event()

    def run(self):
        conn = self._connector
        _HV_FAIL_LIMIT = 3
        _TX_FAIL_LIMIT = 3
        while not self._stop_event.wait(timeout=1.0):
            if conn._monitoring_paused:
                continue
            try:
                if conn._state != RUNNING:
                    if conn._txConnected:
                        if conn._num_modules_connected <= 0:
                            # Guard: don't poll until TX firmware has had time to
                            # finish module enumeration (~2.5 s).  Querying too
                            # early races the init sequence and causes a timeout.
                            elapsed = time.monotonic() - (conn._tx_connect_time or 0.0)
                            if elapsed >= 3.0:
                                conn.queryNumModules()
                        # While sonicating, the firmware pushes unsolicited STATUS
                        # frames with temperature; polling the same endpoint races
                        # those frames and causes UART timeouts, so the outer
                        # conn._state != RUNNING guard skips this poll.
                        conn.queryTxTemperature()
                        if conn._tx_poll_failures >= _TX_FAIL_LIMIT:
                            logger.warning("TX: %d consecutive poll failures – closing interface and triggering disconnect", _TX_FAIL_LIMIT)
                            conn._tx_poll_failures = 0
                            # Close the underlying TX port so the SDK actually
                            # drops the connection; on_disconnected only
                            # updates flags/signals and would leave the SDK in
                            # a still-connected (but failing) state otherwise.
                            try:
                                conn.interface.txdevice.close()
                            except Exception as close_exc:
                                logger.debug("TX close during failure recovery: %s", close_exc)
                            conn.on_disconnected("TX", "")
                            continue
                    if conn._hvConnected:
                        # Re-check power status every cycle so AUTO-settle events
                        # are reflected in the UI promptly.
                        conn.queryPowerStatus()
                        if conn._hv_poll_failures >= _HV_FAIL_LIMIT:
                            logger.warning("HV: %d consecutive poll failures – closing interface and triggering disconnect", _HV_FAIL_LIMIT)
                            conn._hv_poll_failures = 0
                            # Close the underlying HV port so the SDK actually
                            # drops the connection; on_disconnected only
                            # updates flags/signals and would leave the SDK in
                            # a still-connected (but failing) state otherwise.
                            try:
                                conn.interface.hvcontroller.close()
                            except Exception as close_exc:
                                logger.debug("HV close during failure recovery: %s", close_exc)
                            conn.on_disconnected("HV", "")
                            continue
                        conn.getMonitorVoltages()
            except Exception as e:
                logger.warning(f"Telemetry poll loop error: {e}")

    def stop(self):
        self._stop_event.set()


class _Bridge(QObject):
    """Thread-safe bridge from OWSignal to pyqtSignal."""
    sig_connected = pyqtSignal(str, str) # (descriptor, port)
    sig_disconnected = pyqtSignal(str, str) # (descriptor, port)
    sig_data = pyqtSignal(str, str) # (descriptor, data)
    sig_error = pyqtSignal(str, int, str)
    
class LIFUConnector(QObject):
    plotGenerated = pyqtSignal(str)  # Signal to notify QML when a new plot is ready
    solutionConfigured = pyqtSignal(str)  # Signal for solution configuration feedback

    powerStatusReceived = pyqtSignal(bool, bool)  # Signal for power status updates
    rgbStateReceived = pyqtSignal(int, str)  # Emit both integer value and text

    # New Signals for data updates
    hvDeviceInfoReceived = pyqtSignal(str, str)  # (firmwareVersion, deviceId)
    monVoltagesReceived = pyqtSignal(list)  # Signal for voltage monitor readings
    txDeviceInfoReceived = pyqtSignal('QVariantList')  # list of {module, firmwareVersion, deviceId}
    temperatureHvUpdated = pyqtSignal(float, float)  # (temp1, temp2)
    temperatureTxUpdated = pyqtSignal(int, float, float)  # (tx_temp, amb_temp)
    numModulesUpdated    = pyqtSignal()  # (num_modules)

    # Signals exposed to QML for connection/data events
    signalConnected = pyqtSignal(str, str)     # (descriptor, port)
    signalDisconnected = pyqtSignal(str, str)  # (descriptor, port)
    signalDataReceived = pyqtSignal(str, str)  # (descriptor, data)

    stateChanged = pyqtSignal(int)  # Notifies QML when state changes
    connectionStatusChanged = pyqtSignal()  # 🔹 New signal for connection updates
    triggerStateChanged = pyqtSignal(bool)  # 🔹 New signal for trigger state change
    txConfigStateChanged = pyqtSignal(bool)  # 🔹 New signal for tx configured state change

    # Firmware update signals
    fwUpdateProgress = pyqtSignal(str, int, int)  # (label, written, total)
    fwUpdateStatus = pyqtSignal(str, bool, str)   # (device_type, success, message)
    fwVersionRead = pyqtSignal(str, str)           # (device_type, version)

    # Test sequence signals
    testProgressUpdated = pyqtSignal(float, float, str, str, str, str)  # (total_frac, case_frac, total_label, case_label, status_color, log_file_path)


    # User config signals
    userConfigRead = pyqtSignal(str, str)   # (target, json_str)  target: "console" | "tx_N"
    userConfigStatus = pyqtSignal(str, bool, str)  # (target, success, message)
    
    # Solution loading signals
    solutionFileLoaded = pyqtSignal(str, str)  # (solution_name, message)
    solutionLoadError = pyqtSignal(str)  # (error_message)
    solutionStateChanged = pyqtSignal()  # Notifies when solution is loaded/unloaded
    solutionSaveStatus = pyqtSignal(bool, str)  # (success, message)
    testReportLoaded = pyqtSignal(bool, str)  # (success, message)
    
    # HV enable mode signals
    hvEnableModeChanged = pyqtSignal(int)  # Notifies when HV enable mode changes

    # Sonication progress (parsed from unsolicited TX STATUS frames). Only
    # emitted while async_mode is enabled and a sonication is in progress.
    # Raw counts come straight from the firmware's PULSE_TRAIN:[curr/total]
    # and PULSE:[curr/total] fields. Pulse counts are typically (0, 0) on
    # current firmware (PULSE field is reserved); QML should ignore them
    # when total is 0.
    sonicationProgressUpdated = pyqtSignal(int, int, int, int)  # (pt_curr, pt_total, p_curr, p_total)

    # Generic device error signal for surfacing SDK failures to QML as popups.
    # Emitted whenever a LIFUError (or unexpected Exception) is caught while
    # talking to the hardware. The message already includes the [LIFU-<code>]
    # prefix for LIFUError subclasses.
    deviceError = pyqtSignal(str, str)  # (title, message)

    # Thermal-management signals.
    # ``coolingStateChanged`` flips when the TX crosses the cool threshold
    # in either direction. ``thermalShutdownEvent`` is emitted exactly
    # once per shutdown trigger so QML can pop a notification.
    coolingStateChanged = pyqtSignal(bool)
    thermalShutdownEvent = pyqtSignal(float, float, float)  # (observedTempC, shutdownThresholdC, coolingThresholdC)

    # Run-progress signals (drive the run progress bar). The state
    # is one of: "idle", "running", "paused", "finished", "aborted".
    runStateChanged = pyqtSignal(str)
    # Generic notify that any of the run-progress derived properties may
    # have changed (delivered trains, block count, elapsed, etc.).
    runProgressChanged = pyqtSignal()

    # Run-scoped session logging signals.
    sessionSettingsChanged = pyqtSignal()
    runLogStarted = pyqtSignal(str)    # absolute log file path
    runLogFinalized = pyqtSignal(str)  # absolute log file path
    # Fires when anything that affects ``getScaledVoltage`` changes
    # (active preset, cached module user_configs). QML bindings can
    # reference this signal via a notify-property so the displayed
    # scaled voltage refreshes.
    presetScalingChanged = pyqtSignal()

    def _make_interface(self, hv_test_mode=False):
        """Construct the underlying LIFUInterface.

        Subclasses (e.g. ``SimulatedLIFUConnector``) override this to
        substitute a fake implementation without re-running the rest
        of ``__init__``.
        """
        return LIFUInterface(HV_test_mode=hv_test_mode,
                             run_async=True,
                             voltage_table_selection="evt0",
                             sequence_time_selection="stress_test",
                             duty_cycle_selection="stress_test")

    def __init__(self, hv_test_mode=False, context: str | None = None):
        super().__init__()
        # Context name (e.g. "vet", "diathermy") selects which subfolder
        # under preset_settings/ ships the presets and constants.json.
        # ``None`` means "engineering mode" -- no presets and no
        # operator page; getPresets() returns an empty list.
        self._context = context
        self.interface = self._make_interface(hv_test_mode=hv_test_mode)
        self._txConnected = False
        self._hvConnected = False
        self._configured = False
        self._running = False
        self._abort_requested = False
        self.thermal_test_instance = None
        self._active_test_kind = ""
        self.running_thread = None
        self._state = DISCONNECTED
        self._trigger_state = False  # Internal state to track trigger status
        self._txconfigured_state = False  # Internal state to track trigger status
        self._num_modules_connected = 0
        self._tx_connect_time: float | None = None  # monotonic timestamp of last TX connect
        self._hv_poll_failures = 0   # consecutive HV telemetry failures
        self._tx_poll_failures = 0   # consecutive TX telemetry failures
        self._temp_poll_failures = 0  # consecutive temperature poll failures
        self._monitoring_paused = False  # set True while diagnostics tab is active
        
        # Solution loading state
        self._solution_loaded = False
        self._loaded_solution_data = None
        self._solution_name = ""
        
        # HV enable mode: 0=AUTO (only while running), 1=ON, 2=OFF
        self._hv_enable_mode = HV_EN_AUTO

        # Tracks whether the TX device's unsolicited STATUS stream is
        # currently enabled. The firmware only emits STATUS frames during
        # active sonication; we keep async OFF while the host is issuing
        # write_block-heavy commands (set_solution, direct setters) so the
        # TX response packet is not delayed by an interleaved STATUS frame
        # on the same CDC IN endpoint -- which is the dominant cause of
        # UART timeouts during configuration.
        self._async_mode_enabled = False

        # ------- Thermal management (TX) -------
        # Page-level UI used to own this state machine; centralizing here
        # keeps the QML layer thin and means we don't re-evaluate
        # heuristics on every QML binding tick. ``_cooling_down`` is True
        # whenever the hottest known TX module is above
        # ``THERMAL_COOLING_THRESHOLD_C``. Crossing
        # ``THERMAL_SHUTDOWN_THRESHOLD_C`` triggers a one-shot hard abort
        # (re-armed when the device cools back below the cool threshold).
        self._cooling_down = False
        self._thermal_shutdown_active = False
        # Latest per-module TX temperatures (mirrors what we emit so the
        # thermal evaluator and QML can both consult a single source).
        # Index is the module number; missing modules are NaN.
        self._tx_temperatures = []
        # HV enable mode at the moment we forced HV off for cooldown.
        # -1 means "nothing to restore". Restored on cooldown exit.
        self._pre_cooldown_hv_mode = -1

        # ------- Run-progress / pause-resume state -------
        # ``_run_state`` is a UI-friendly state machine that wraps the
        # device's RUNNING/READY transitions with awareness of pause/
        # resume "blocks". On a fresh Start we snapshot the original
        # sequence params here so that pause-shortened reconfigures (via
        # set_trigger only) don't lose the user's overall total. On Abort
        # / natural completion we restore the original train count.
        self._run_state = "idle"
        self._run_original_train_total = 0
        self._run_original_duration_s = 0.0
        self._run_original_pulse_count = 0
        self._run_trains_delivered_before_block = 0
        self._run_in_block_total = 0   # current block's trainCount on the device
        self._run_in_block_current = 0  # latest pt_curr reported for current block
        self._run_block_count = 0
        self._run_start_time_ms = 0.0
        self._run_elapsed_ms = 0.0
        # Snapshot of the most recent configure_transmitter args so we
        # can rebuild trigger settings via set_trigger (cheap) instead of
        # set_solution (slow) for pause/resume/abort transitions.
        self._last_configure_args = None

        # Active preset (delays/apodizations baked from disk).
        # When set, get_solution() overrides its computed delays /
        # apodizations with these so the operator page uses the per-preset
        # measured values rather than recomputing from element geometry.
        self._active_preset = None

        # Per-module ``user_config`` dicts (parsed JSON from
        # ``txdevice.read_config``), keyed by module index. Refreshed
        # by ``queryNumModules`` whenever the connected count changes.
        # Used in the Active preset path to scale voltage by the ratio of the
        # preset's calibration sensitivity to the connected device's
        # measured sensitivity at the run frequency.
        self._module_user_configs = {}

        self._interface_mutex = QRecursiveMutex()

        self._ensure_preset_solutions_seeded()

        # Run-scoped session logger. When running from source the
        # settings file lives in the repo root (gitignored) so the path
        # the developer chose persists with the checkout; in a frozen
        # build it falls back to the user data root since the bundled
        # _MEIPASS directory is read-only and ephemeral.
        if getattr(sys, 'frozen', False):
            session_settings_dir = self._get_user_data_root()
        else:
            session_settings_dir = _base_path()
        self._session_logger = _SessionLogger(session_settings_dir)

        self._bridge = _Bridge()

        # Wire OWSignals -> bridge
        self.interface.hvcontroller.signal_connected.connect(self._bridge.sig_connected.emit)
        self.interface.hvcontroller.signal_disconnected.connect(self._bridge.sig_disconnected.emit)
        self.interface.hvcontroller.signal_data_received.connect(self._bridge.sig_data.emit)
        self.interface.hvcontroller.signal_error.connect(self._bridge.sig_error.emit)

        self.interface.txdevice.signal_connected.connect(self._bridge.sig_connected.emit)
        self.interface.txdevice.signal_disconnected.connect(self._bridge.sig_disconnected.emit)
        self.interface.txdevice.signal_data_received.connect(self._bridge.sig_data.emit)
        self.interface.txdevice.signal_error.connect(self._bridge.sig_error.emit)

        # Wire bridge -> UI
        self._bridge.sig_connected.connect(self.on_connected)
        self._bridge.sig_disconnected.connect(self.on_disconnected)
        self._bridge.sig_data.connect(self.on_data_received)
        self._bridge.sig_error.connect(self.on_error)

        # Internal handlers: drive thermal + run-progress state machines
        # off the same signals we expose to QML so the policy lives in
        # one place and QML doesn't have to re-derive it on every binding
        # tick.
        self.temperatureTxUpdated.connect(self._on_tx_temperature_for_thermal)
        self.sonicationProgressUpdated.connect(self._on_progress_for_run_state)
        self.stateChanged.connect(self._on_state_changed_for_run_state)

        # Background telemetry polling thread (temperature + HV voltages).
        # QThread is used (not threading.Thread) so Qt's queued-connection
        # mechanism correctly delivers signals from the poll thread to the
        # main-thread event loop (and thus to QML).
        self._poll_thread = _TelemetryPollThread(self)
        self._poll_thread.start()

        QTimer.singleShot(0, lambda: asyncio.ensure_future(self.interface.start_monitoring()))

    def close(self):
        """Shut down the underlying LIFU interface cleanly.

        Best-effort: stop any active sonication and de-energize the HV
        rail before tearing the interface down so a crash-on-shutdown or
        forced quit doesn't leave the device in a transmitting / hot
        state.
        """
        # Signal the telemetry poll thread to stop and wait for it to finish
        # its current hardware operation before we start tearing down the
        # interface.  A 5-second timeout prevents an indefinite hang if the
        # device is unresponsive.
        self._poll_thread.stop()
        if not self._poll_thread.wait(5000):  # 5 000 ms
            logger.warning("Telemetry poll thread did not exit within timeout during close.")

        # Stop sonication first; this also turns the trigger off and (in
        # AUTO mode) drops HV via stop_sonication's own turn_hv_off path.
        if self._state == RUNNING:
            try:
                self.interface.stop_sonication(turn_hv_off=True)
            except Exception as e:
                logger.error(f"Error stopping sonication during close: {e}")
        # Independently force HV off, regardless of mode, in case the
        # user had it pinned ON or stop_sonication was skipped.
        if self._hvConnected:
            try:
                self.interface.hvcontroller.turn_hv_off()
            except Exception as e:
                logger.error(f"Error turning HV off during close: {e}")
        try:
            self.interface.close()
        except Exception as e:
            logger.error(f"Error closing LIFU interface: {e}")
        # Tear down any in-progress run log so the file is closed and
        # stderr is restored even on shutdown.
        try:
            self._close_run_log(reason="aborted_app_quit")
        except Exception:
            pass

    def _emit_device_error(self, title: str, message: str):
        """Log a device/communication failure and surface it to QML as a popup."""
        logger.error(f"{title}: {message}")
        try:
            self.deviceError.emit(title, message)
        except Exception as e:
            logger.error(f"Failed to emit deviceError signal: {e}")

    def _handle_lifu_error(self, title: str, exc: BaseException, context: str = ""):
        """Format a caught LIFUError (or other exception) and emit a popup.

        The message passed to the user includes the ``[LIFU-<code>]`` prefix
        that ``LIFUError`` embeds in its string representation, so operators
        can reference the exact error code when reporting issues.
        """
        if isinstance(exc, LIFUError):
            detail = str(exc)
        else:
            detail = f"{type(exc).__name__}: {exc}"
        if context:
            detail = f"{context}: {detail}"
        self._emit_device_error(title, detail)

    def update_state(self):
        """Update system state based on connection and configuration.

        State is purely a function of TX/HV connection + whether a solution
        has been programmed (or a verification test is running). RUNNING is
        set explicitly by start/stop_sonication and by the verification test
        runner via ``self._running``. HV connection/enable status is tracked
        independently and surfaced via ``hvConnected``/``powerStatusReceived``
        rather than folded into ``state`` (except for the TEST_SCRIPT_READY
        case where only HV is attached).
        """
        if self._running:
            self._state = RUNNING
        elif self._state == RUNNING and self._txConnected:
            # Sonication-driven RUNNING is owned by start/stop_sonication;
            # preserve it as long as the TX is still attached.
            pass
        elif not self._txConnected and not self._hvConnected:
            self._state = DISCONNECTED
        elif self._txConnected and self._configured:
            self._state = READY
        elif self._txConnected:
            self._state = CONNECTED
        else:
            # HV connected without TX – verification scripts can run.
            self._state = TEST_SCRIPT_READY
        self.stateChanged.emit(self._state)
        logger.debug(f"Updated state: {self._state}")

    def _hv_ready(self) -> bool:
        """Return True if HV is connected and not disabled by the user."""
        return self._hvConnected and self._hv_enable_mode != HV_EN_OFF

    def _call_with_comm_retry(self, label, func, *args, **kwargs):
        """Call ``func(*args, **kwargs)`` retrying on transient timeouts.

        ``LIFUCommunicationError`` is treated as a transient comms
        glitch and retried up to :data:`MAX_TIMEOUT_RETRIES` additional
        times (so ``MAX_TIMEOUT_RETRIES + 1`` attempts in total).
        Every retry is logged at WARNING with attempt counts so the
        run log makes it obvious that recovery happened. If the final
        attempt still fails the exception is re-raised so the caller's
        normal error-handling (popup, state rollback, etc.) runs.
        Any other exception is raised immediately without retry.
        """
        last_exc = None
        total_attempts = MAX_TIMEOUT_RETRIES + 1
        for attempt in range(1, total_attempts + 1):
            try:
                return func(*args, **kwargs)
            except LIFUCommunicationError as e:
                last_exc = e
                if attempt < total_attempts:
                    logger.warning(
                        "%s: communication timeout on attempt %d/%d (%s); retrying...",
                        label, attempt, total_attempts, e,
                    )
                else:
                    logger.error(
                        "%s: communication timeout after %d attempts (%s); giving up.",
                        label, total_attempts, e,
                    )
        # last_exc is guaranteed set here -- the loop only exits the
        # success path via ``return`` above.
        raise last_exc

    def _apply_auto_hv_for_state(self):
        """Drive the HV rail to match AUTO mode + current configured state.

        AUTO holds HV on whenever the TX has a solution loaded (state ==
        READY); off otherwise. Called by configure / reset transitions.
        Other modes (ON/OFF/WHILE_RUNNING) are no-ops here. No-op while
        RUNNING (start/stop_sonication owns the rail then).
        """
        if not self._hvConnected:
            return
        if self._hv_enable_mode != HV_EN_AUTO:
            return
        if self._state == RUNNING:
            return
        try:
            should_be_on = (self._state == READY)
            if should_be_on:
                self.interface.hvcontroller.turn_hv_on()
                logger.info("HV turned on (AUTO mode, configured)")
            else:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off (AUTO mode, not configured)")
            try:
                hv_state = self.interface.hvcontroller.get_hv_status()
                v12_state = self.interface.hvcontroller.get_12v_status()
                self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
            except Exception as e:
                logger.warning(f"Could not refresh power status after AUTO HV change: {e}")
        except LIFUError as e:
            self._handle_lifu_error("HV Auto", e,
                                    context="Failed to apply AUTO mode after state change")
        except Exception as e:
            self._handle_lifu_error("HV Auto", e, context="Unexpected error")

    def _update_trigger_state(self, trigger_data):
        """Helper method to update trigger state and emit signal."""
        try:
            trigger_status = trigger_data.get("TriggerStatus", "STOPPED")
            new_trigger_state = trigger_status == "RUNNING"

            if new_trigger_state != self._trigger_state:
                self._trigger_state = new_trigger_state
                self.triggerStateChanged.emit(self._trigger_state)

        except Exception as e:
            logger.error(f"Error updating trigger state: {e}")

    @pyqtSlot(str, result=dict)
    def parse_status_string(self, status_str):
        result = {
            "status": None,
            "mode": None,
            "pulse_train_current": None,
            "pulse_train_total": None,
            "pulse_current": None,
            "pulse_total": None,
            "pulse_train_percent": None,
            "pulse_percent": None,
            "temp_tx": None,
            "temp_ambient": None
        }

        try:
            # Try pattern WITH PULSE field
            pattern_with_pulse = re.compile(
                r"STATUS:(\w+),"
                r"MODE:(\w+),"
                r"PULSE_TRAIN:\[(\d+)/(\d+)\],"
                r"PULSE:\[(\d+)/(\d+)\],"
                r"TEMP_TX:([0-9.]+),"
                r"TEMP_AMBIENT:([0-9.]+)"
            )
            match = pattern_with_pulse.match(status_str.strip())

            if match:
                (
                    status,
                    mode,
                    pt_current, pt_total,
                    p_current, p_total,
                    temp_tx,
                    temp_ambient
                ) = match.groups()

                # Convert and compute percentages
                pt_current = int(pt_current)
                pt_total = int(pt_total)
                p_current = int(p_current)
                p_total = int(p_total)

                result["status"] = status
                result["mode"] = mode
                result["pulse_train_current"] = pt_current
                result["pulse_train_total"] = pt_total
                result["pulse_current"] = p_current
                result["pulse_total"] = p_total
                result["pulse_train_percent"] = (pt_current / pt_total * 100) if pt_total > 0 else 0
                result["pulse_percent"] = (p_current / p_total * 100) if p_total > 0 else 0
                result["temp_tx"] = float(temp_tx)
                result["temp_ambient"] = float(temp_ambient)

            else:
                # Try pattern WITHOUT PULSE field
                pattern_without_pulse = re.compile(
                    r"STATUS:(\w+),"
                    r"MODE:(\w+),"
                    r"PULSE_TRAIN:\[(\d+)/(\d+)\],"
                    r"TEMP_TX:([0-9.]+),"
                    r"TEMP_AMBIENT:([0-9.]+)"
                )
                match = pattern_without_pulse.match(status_str.strip())

                if not match:
                    raise ValueError("Input string format is invalid.")

                (
                    status,
                    mode,
                    pt_current, pt_total,
                    temp_tx,
                    temp_ambient
                ) = match.groups()

                # Convert and compute percentages
                pt_current = int(pt_current)
                pt_total = int(pt_total)

                result["status"] = status
                result["mode"] = mode
                result["pulse_train_current"] = pt_current
                result["pulse_train_total"] = pt_total
                result["pulse_current"] = None
                result["pulse_total"] = None
                result["pulse_train_percent"] = (pt_current / pt_total * 100) if pt_total > 0 else 0
                result["pulse_percent"] = None  # No pulse data available
                result["temp_tx"] = float(temp_tx)
                result["temp_ambient"] = float(temp_ambient)

            return result

        except Exception as e:
            logger.error(f"Failed to parse status string: {e}")
            return result

    def on_error(self, desc: str, pkt_id: int, msg: str):
        if desc != "Console":
            return
        logger.error(f"ERROR id={pkt_id} {msg}")

    @pyqtSlot(str, str)
    def on_connected(self, descriptor, port):
        """Handle device connection."""
        if descriptor == "TX":
            self._txConnected = True
            self._tx_connect_time = time.monotonic()
        elif descriptor == "HV":
            self._hvConnected = True
        self.signalConnected.emit(descriptor, port)
        self.connectionStatusChanged.emit() 
        self.update_state()

    @pyqtSlot(str, str)
    def on_disconnected(self, descriptor, port):
        """Handle device disconnection."""
        if descriptor == "TX":
            self._txConnected = False
            self._tx_connect_time = None
            self._tx_poll_failures = 0
            # The unsolicited STATUS stream is gone with the TX port; clear
            # our tracker so a future reconnect doesn't think it's still on.
            self._async_mode_enabled = False
        elif descriptor == "HV":
            self._hvConnected = False
            self._hv_poll_failures = 0
            # If HV was set to "ON" mode, automatically switch to "OFF" when disconnected
            if self._hv_enable_mode == HV_EN_ON:  # ON mode
                self._hv_enable_mode = HV_EN_OFF  # Switch to OFF
                self.hvEnableModeChanged.emit(self._hv_enable_mode)
                logger.info("HV enable mode automatically switched to OFF due to HV disconnection")
                
        self.signalDisconnected.emit(descriptor, port)
        self.connectionStatusChanged.emit() 
        self.update_state()

    @pyqtSlot(str, str)
    def on_data_received(self, descriptor, message):
        """Handle incoming data from the LIFU device."""
        self.signalDataReceived.emit(descriptor, message)

        if descriptor == "TX":
            try:
                parsed = self.parse_status_string(message)
                if parsed["status"] in {"RUNNING", "STOPPED"}:
                    # Structured DEBUG log of the unsolicited STATUS
                    # frame. The raw text is reconstructable from these
                    # fields, so we don't also log the wire payload.
                    pt_pct = parsed.get("pulse_train_percent")
                    p_pct = parsed.get("pulse_percent")
                    pt_str = f"{pt_pct:0.1f}%" if pt_pct is not None else "--"
                    p_str = f"{p_pct:0.1f}%" if p_pct is not None else "--"
                    temp_tx = parsed.get("temp_tx")
                    temp_amb = parsed.get("temp_ambient")
                    temp_tx_str = f"{temp_tx:0.1f}" if temp_tx is not None else "--"
                    temp_amb_str = f"{temp_amb:0.1f}" if temp_amb is not None else "--"
                    logger.debug(
                        "TX STATUS: status=%s mode=%s train=%s pulse=%s "
                        "temp_tx=%sC temp_amb=%sC",
                        parsed.get("status"),
                        parsed.get("mode"),
                        pt_str,
                        p_str,
                        temp_tx_str,
                        temp_amb_str,
                    )

                    # Update internal trigger state based on parsed status
                    new_trigger_state = parsed["status"] == "RUNNING"
                    
                    if new_trigger_state != self._trigger_state:
                        self._trigger_state = new_trigger_state
                        self.triggerStateChanged.emit(self._trigger_state)
                        logger.debug(f"Trigger state updated to: {'RUNNING' if self._trigger_state else 'STOPPED'}")
                    
                    if parsed["status"] == "STOPPED":
                        logger.debug("Trigger is stopped.")
                        self._state = READY
                        self.stateChanged.emit(self._state)

                    # Forward temperature data embedded in STATUS messages so that
                    # separate queryTxTemperature() calls are not needed while the
                    # SDK monitoring thread owns the serial port.
                    if parsed["temp_tx"] is not None and parsed["temp_ambient"] is not None:
                        self.temperatureTxUpdated.emit(0, float(parsed["temp_tx"]), float(parsed["temp_ambient"]))

                    # Forward sonication progress raw counts so the UI
                    # can drive a progress bar without doing its own
                    # parsing. Pulse counts are typically (0, 0) on
                    # current firmware (PULSE field reserved).
                    pt_curr = parsed.get("pulse_train_current")
                    pt_total = parsed.get("pulse_train_total")
                    if pt_curr is not None and pt_total is not None:
                        p_curr = parsed.get("pulse_current") or 0
                        p_total = parsed.get("pulse_total") or 0
                        self.sonicationProgressUpdated.emit(
                            int(pt_curr), int(pt_total), int(p_curr), int(p_total)
                        )

            except Exception as e:
                logger.error(f"Failed to parse and update trigger state: {e}")

    @pyqtSlot(str, float)
    def configureSolution(self, solutionName, amplitude):
        """Configures the solution and emits status to QML."""
        self._interface_mutex.lock()
        try:
            logger.debug("Configuring solution: %s with amplitude: %s", solutionName, amplitude)
            solution = None  # Replace with actual configuration logic
            self.interface.set_solution(solution)
            logger.info("Solution '%s' configured successfully.", solutionName)
            self.solutionConfigured.emit(f"Solution '{solutionName}' configured.")
        except LIFUError as e:
            self.solutionConfigured.emit("Configuration failed.")
            self._handle_lifu_error("Configure Solution", e,
                                    context=f"Failed to configure solution '{solutionName}'")
        except Exception as e:
            self.solutionConfigured.emit("Configuration error.")
            self._handle_lifu_error("Configure Solution", e,
                                    context="Unexpected error")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def generate_plot(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode="buffer"):
        """Generates an ultrasound plot and emits data to QML."""
        try:
            #logger.info(f"Generating plot: X={x}, Y={y}, Z={z}, Frequency={freq}, Cycles={cycles}, Trigger={trigger}, Mode={mode}")
            solution = self.get_solution(xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, validate=self._txConnected)
            image_data = generate_ultrasound_plot_from_solution(solution, mode)
            #image_data = generate_ultrasound_plot(x, y, z, freq, cycles, trigger, mode)
            if image_data == "ERROR":
                logger.error("Plot generation failed")
            else:
                logger.info("Plot generated successfully")
                self.plotGenerated.emit(image_data)  # Send image data to QML

        except Exception as e:
            logger.error(f"Error generating plot: {e}")

    def get_solution(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, validate=False):
        """Simulate configuring the transmitter."""
        num_modules = self._num_modules_connected if self._num_modules_connected > 0 else 1
        if self._solution_loaded:
            logger.info("Using loaded solution for configuration")
            solution = self._loaded_solution_data
            if solution['sequence']['pulse_train_interval'] == 0:
                solution['sequence']['pulse_train_interval'] = solution['sequence']['pulse_count'] * solution['sequence']['pulse_interval']
            #check if delays and apodizations match the number of elements in the loaded solution
            delays_arr = np.array(solution["delays"]).reshape(-1)  # Ensure it's a 1D array
            apodizations_arr = np.array(solution["apodizations"]).reshape(-1)  # Ensure it's a 1D array
            if validate:
                if delays_arr.ndim == 1:
                    n_delays = delays_arr.shape[0]
                else:
                    n_delays = delays_arr.shape[1]
                if n_delays != num_modules * NUM_ELEMENTS_PER_MODULE:
                    logger.error(f"Loaded solution has {len(delays_arr)} delays, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    self.solutionLoadError.emit(f"Loaded solution has {len(delays_arr)} delays, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    return
                if apodizations_arr.ndim == 1:
                    n_apodizations = apodizations_arr.shape[0]
                else:
                    n_apodizations = apodizations_arr.shape[1]
                if n_apodizations != num_modules * NUM_ELEMENTS_PER_MODULE:
                    logger.error(f"Loaded solution has {len(apodizations_arr)} apodizations, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    self.solutionLoadError.emit(f"Loaded solution has {len(apodizations_arr)} apodizations, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    return
        else:
            # Controller UI displays frequency in kHz, duration in microseconds, and pulse interval in ms.
            frequency_hz = float(freq) * 1e3
            duration_seconds = float(durationS) * 1e-6
            pulse_interval_seconds = float(pulseInterval) * 1e-3

            pulse = {"frequency": frequency_hz,
                    "duration": duration_seconds,
                    "amplitude": 1.0
                    }
            focus = np.array([float(xInput), float(yInput), float(zInput)])

            # When a Active preset is active its measured delays/apodizations
            # fully replace the geometric ones, so we can skip loading
            # the pinmap (and avoid touching disk for it) entirely.
            preset = self._active_preset
            preset_delays = None
            preset_apod = None
            if preset is not None:
                preset_delays = np.array(preset.get("delays", []), dtype=float).reshape(-1)
                preset_apod = np.array(preset.get("apodizations", []), dtype=float).reshape(-1)
                expected = num_modules * NUM_ELEMENTS_PER_MODULE
                if (preset_delays.size != expected
                        or preset_apod.size != expected):
                    logger.warning(
                        "Active Active preset '%s' has %d delays / %d apodizations, "
                        "expected %d; falling back to pinmap-derived values.",
                        preset.get("id", "?"), preset_delays.size,
                        preset_apod.size, expected,
                    )
                    preset_delays = None
                    preset_apod = None

            if preset_delays is not None:
                # Active preset path: no pinmap needed.
                delays = preset_delays
                apodizations = preset_apod
                numelements = delays.size
                # Minimal transducer dict – downstream consumers only
                # read ``elements`` (for plotting) and ``module_invert``;
                # neither is needed when using preset path.
                transducer_dummy = {
                    "id": preset.get("id", ""),
                    "name": preset.get("name", preset.get("id", "")),
                    "elements": [],
                }
            else:
                pinmap_data = self._load_pinmap_data(num_modules)
                element_positions = self._extract_element_positions_from_pinmap(pinmap_data)
                numelements = element_positions.shape[0]
                logger.debug(f"{num_modules}x config file loaded")
                distances = np.sqrt(np.sum((focus - element_positions)**2, 1))
                tof = distances*1e-3 / SPEED_OF_SOUND
                delays = tof.max() - tof
                apodizations = np.ones(numelements)
                transducer_dummy = self._build_transducer_from_pinmap(pinmap_data)

            pulse_count = int(pulseCount)
            pulse_train_interval = float(trainInterval)
            if pulse_train_interval == 0:
                pulse_train_interval = pulse_count * pulse_interval_seconds
            sequence = {"pulse_interval": pulse_interval_seconds,
                        "pulse_count": pulse_count,
                        "pulse_train_interval": pulse_train_interval,
                        "pulse_train_count": int(trainCount)}
            # NOTE: voltage is NOT scaled here. The operator page applies the
            # sensitivity scaling once via getScaledVoltage() before
            # calling configure_transmitter / directSetVoltage, so the
            # ``voltage`` argument we receive is already the
            # device-corrected value. Scaling again here would compound
            # the correction.
            solution = {
                "id": "solution",
                "name": "Solution",
                "delays": delays,
                "apodizations": apodizations,
                "pulse": pulse,
                "sequence": sequence,
                "voltage": float(voltage),
                "transducer": transducer_dummy}
        return solution

    def _device_sensitivity_at(self, freq_hz):
        """Average device sensitivity across modules at ``freq_hz``.

        Returns ``(mean, per_module_dict)`` or ``(None, {})`` if no
        connected module has sensitivity data. ``per_module_dict`` maps
        module index -> interpolated sensitivity for logging.
        """
        per_mod = {}
        logger.debug(
            "_device_sensitivity_at: freq=%.1fHz, cached_modules=%s",
            float(freq_hz), sorted(self._module_user_configs.keys()),
        )
        for idx, cfg in self._module_user_configs.items():
            mod = cfg.get("module") or {}
            sens = mod.get("sensitivity") or []
            if not sens:
                logger.debug(
                    "_device_sensitivity_at: module %d has no sensitivity data", idx,
                )
                continue
            try:
                pts = sorted((float(f), float(v)) for f, v in sens)
            except (TypeError, ValueError) as e:
                logger.debug(
                    "_device_sensitivity_at: module %d sensitivity malformed: %s",
                    idx, e,
                )
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            # numpy.interp clamps below/above the range to endpoints,
            # which is the behaviour we want here.
            interp = float(np.interp(float(freq_hz), xs, ys))
            per_mod[idx] = interp
            logger.debug(
                "_device_sensitivity_at: module %d sens(%.1fHz)=%.2f (table %d pts, range %.0f..%.0fHz)",
                idx, float(freq_hz), interp, len(pts), xs[0], xs[-1],
            )
        if not per_mod:
            logger.debug("_device_sensitivity_at: no usable sensitivity data")
            return None, {}
        mean = float(np.mean(list(per_mod.values())))
        logger.debug(
            "_device_sensitivity_at: mean=%.2f across %d modules",
            mean, len(per_mod),
        )
        return mean, per_mod

    def _apply_preset_sensitivity_scaling(self, voltage, freq_hz, preset):
        """Scale ``voltage`` by ``preset_sens / device_sens`` if available.

        Returns ``voltage`` unchanged when no Active preset is active, the
        preset has no calibration sensitivity, or no connected module
        reports sensitivity data.
        """
        logger.debug(
            "_apply_preset_sensitivity_scaling: input voltage=%.4fV freq=%.1fHz "
            "preset_id=%s",
            float(voltage), float(freq_hz),
            (preset or {}).get("id", "(none)"),
        )
        if preset is None:
            logger.debug("_apply_preset_sensitivity_scaling: no active preset")
            return voltage
        preset_sens = preset.get("sensitivity")
        if not preset_sens or float(preset_sens) <= 0:
            logger.debug(
                "_apply_preset_sensitivity_scaling: preset '%s' has no usable "
                "sensitivity (%r)", preset.get("id", "?"), preset_sens,
            )
            return voltage
        device_sens, per_mod = self._device_sensitivity_at(freq_hz)
        if not device_sens or device_sens <= 0:
            logger.info(
                "Active preset '%s': no device sensitivity available; "
                "using unscaled voltage %.3fV.",
                preset.get("id", "?"), voltage,
            )
            return voltage
        scale = float(preset_sens) / device_sens
        scaled = voltage * scale
        logger.info(
            "Active preset '%s': voltage %.3fV -> %.3fV (scale=%.3f, "
            "preset_sens=%s, device_sens=%.1f @ %.1fkHz, per_module=%s)",
            preset.get("id", "?"), voltage, scaled, scale,
            preset_sens, device_sens, freq_hz / 1e3,
            {i: round(v, 1) for i, v in per_mod.items()},
        )
        return scaled

    @pyqtSlot(str, str, result=str)
    def getScaledVoltage(self, voltage_str, freq_khz_str):
        """Return ``voltage_str`` scaled by the active preset's sensitivity ratio.

        Exposed to QML so the operator page can apply the scaling before
        pushing the HV setpoint via ``directSetVoltage`` and before
        showing the value in the UI. Returns the input unchanged on any
        parse failure.
        """
        try:
            v = float(voltage_str)
            f_hz = float(freq_khz_str) * 1e3
        except (TypeError, ValueError) as e:
            logger.warning(
                "getScaledVoltage: bad input (voltage=%r, freq_khz=%r): %s",
                voltage_str, freq_khz_str, e,
            )
            return str(voltage_str)
        scaled = self._apply_preset_sensitivity_scaling(
            v, f_hz, self._active_preset,
        )
        return f"{scaled:.6f}"

    def _load_pinmap_data(self, num_modules: int):
        """Load pinmap data for a given module count."""
        pinmap_path = os.path.join(_base_path(), f"pinmap_{num_modules}x.json")
        with open(pinmap_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _extract_element_positions_from_pinmap(self, pinmap_data):
        """Extract element positions (Nx3) in array/world coordinates from pinmap JSON."""
        if "type" in pinmap_data and pinmap_data["type"] == "TransducerArray":
            modules = []
            for module in pinmap_data.get('modules', []):
                module_transform = np.array(module['transform'])
                element_positions = np.array([elem['position'] for elem in module['elements']])
                element_positions = np.hstack((element_positions, np.ones((element_positions.shape[0], 1))))
                world_positions = (np.linalg.inv(module_transform) @ element_positions.T).T[:, :3]
                modules.append(world_positions)
            return np.vstack(modules)

        return np.array([elem['position'] for elem in pinmap_data.get('elements', [])])

    def _build_transducer_from_pinmap(self, pinmap_data):
        """Build a solution-compatible transducer object from pinmap JSON."""
        transducer = {
            "id": pinmap_data.get("id", ""),
            "name": pinmap_data.get("name", ""),
            "elements": []
        }

        if "type" in pinmap_data and pinmap_data["type"] == "TransducerArray":
            flattened_elements = []
            global_index = 1
            for module in pinmap_data.get("modules", []):
                module_transform = np.array(module["transform"])
                inv_transform = np.linalg.inv(module_transform)
                for element in module.get("elements", []):
                    element_copy = copy.deepcopy(element)
                    local_position = np.array(list(element_copy.get("position", [0.0, 0.0, 0.0])) + [1.0])
                    world_position = (inv_transform @ local_position)[:3]
                    element_copy["position"] = [float(world_position[0]), float(world_position[1]), float(world_position[2])]
                    element_copy["index"] = global_index
                    flattened_elements.append(element_copy)
                    global_index += 1
            transducer["elements"] = flattened_elements
            return transducer

        transducer["elements"] = copy.deepcopy(pinmap_data.get("elements", []))
        return transducer

    def _to_json_compatible(self, value):
        """Convert numpy and nested structures into JSON-serializable Python values."""
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {k: self._to_json_compatible(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._to_json_compatible(v) for v in value]
        if isinstance(value, tuple):
            return [self._to_json_compatible(v) for v in value]
        return value

    def _infer_num_modules_from_solution(self, data: dict) -> int:
        """Infer the number of TX modules from a solution's transducer element count."""
        try:
            elements = data.get('transducer', {}).get('elements', [])
            n = len(elements)
            if n > 0 and NUM_ELEMENTS_PER_MODULE > 0:
                return max(1, n // NUM_ELEMENTS_PER_MODULE)
        except Exception:
            pass
        return 0

    def _get_preset_templates_path(self) -> str:
        return os.path.join(_base_path(), "preset_templates")

    def _get_user_data_root(self) -> str:
        """Return the writable per-user application data directory."""
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, "OpenLIFU-TestApp")

    def _get_legacy_preset_solutions_path(self) -> str:
        """Return the legacy preset directory next to the executable, if applicable."""
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "preset_solutions")
        return os.path.join(_base_path(), "preset_solutions")

    def _get_runtime_preset_solutions_path(self) -> str:
        if getattr(sys, 'frozen', False):
            return os.path.join(self._get_user_data_root(), "preset_solutions")
        return os.path.join(_base_path(), "preset_solutions")

    def _get_default_solution_path(self) -> str:
        return os.path.join(self._get_runtime_preset_solutions_path(), "default_solution.json")

    # ------------------------------------------------------------------
    # Per-context preset settings (preset_settings/<id>/<id>_settings.json)
    # ------------------------------------------------------------------

    def _get_preset_settings_path(self) -> str:
        ctx = self._context or ""
        return os.path.join(_base_path(), "preset_settings", ctx)

    @pyqtSlot(result="QVariantMap")
    def getContextConstants(self):
        """Return UI constants (fixed params, duration choices, ...) for the active context.

        Reads ``preset_settings/<context>/constants.json``. Returns an
        empty dict when no context is active or the file is missing /
        unreadable so QML can fall back to its own defaults.
        """
        if not self._context:
            return {}
        path = os.path.join(self._get_preset_settings_path(), "constants.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("getContextConstants: cannot read %s: %s", path, e)
            return {}
        return data if isinstance(data, dict) else {}

    def _load_preset_file(self, preset_id: str):
        """Load and return the parsed JSON for a preset, or None on error."""
        if not preset_id:
            return None
        json_path = os.path.join(
            self._get_preset_settings_path(), preset_id, f"{preset_id}_settings.json"
        )
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            logger.error("Failed to load Active preset '%s': %s", preset_id, e)
            return None

    @pyqtSlot(result="QVariantList")
    def getPresets(self):
        """Return UI-facing metadata for every preset in preset_settings/.

        Each entry contains the parameters needed to drive the operator page
        ComboBox plus the per-preset analysis dict and the absolute
        path to the intensity plot PNG (as a file:// URL for QML Image).
        Delays/apodizations are intentionally NOT included here -- they
        are loaded on demand via setActivePreset() to avoid pushing
        large arrays through the QML/Qt variant layer.
        """
        root = self._get_preset_settings_path()
        presets = []
        if not self._context:
            return presets
        if not os.path.isdir(root):
            logger.warning("preset_settings folder not found at %s", root)
            return presets
        for preset_id in sorted(os.listdir(root)):
            preset_dir = os.path.join(root, preset_id)
            if not os.path.isdir(preset_dir):
                continue
            data = self._load_preset_file(preset_id)
            if data is None:
                continue
            png_path = os.path.join(preset_dir, f"{preset_id}_intensity_plot.png")
            png_url = ""
            if os.path.isfile(png_path):
                # QML Image accepts a file:// URL.
                png_url = "file:///" + png_path.replace("\\", "/").lstrip("/")
            presets.append({
                "id": preset_id,
                "label": data.get("label", preset_id),
                "voltage": float(data.get("voltage", 0.0)),
                "frequency_khz": float(data.get("frequency_khz", 0.0)),
                "pulse_length_us": float(data.get("pulse_length_us", 0.0)),
                "pulse_interval_ms": float(data.get("pulse_interval_ms", 0.0)),
                "pulse_count": int(data.get("pulse_count", 1)),
                "pulse_train_interval_s": float(data.get("pulse_train_interval_s", 0)),
                "depth_mm": float(data.get("depth_mm", 0.0)),
                "analysis": data.get("analysis", {}) or {},
                "intensityPlotUrl": png_url,
            })
        return presets

    @pyqtSlot(str, result=bool)
    def setActivePreset(self, preset_id):
        """Make the named preset's delays/apodizations active for get_solution()."""
        data = self._load_preset_file(preset_id)
        if data is None:
            self._active_preset = None
            return False
        self._active_preset = {
            "id": preset_id,
            "delays": data.get("delays", []),
            "apodizations": data.get("apodization", data.get("apodizations", [])),
            # Calibration sensitivity used by the preset's voltage
            # number; we compare against the connected device's
            # ``user_config['module']['sensitivity']`` to compute a
            # per-run voltage scale factor.
            "sensitivity": data.get("sensitivity"),
            "frequency_khz": float(data.get("frequency_khz", 0.0)),
            "voltage": float(data.get("voltage", 0.0)),
        }
        logger.info(
            "Active Active preset set to '%s' (calib_sens=%s @ %.1fkHz, calib_voltage=%.2fV)",
            preset_id, self._active_preset["sensitivity"],
            self._active_preset["frequency_khz"],
            self._active_preset["voltage"],
        )
        self.presetScalingChanged.emit()
        return True

    @pyqtSlot()
    def clearActivePreset(self):
        self._active_preset = None
        self.presetScalingChanged.emit()

    # ------------------------------------------------------------------
    # Run-scoped session-logging settings (persist across app runs)
    # ------------------------------------------------------------------

    @pyqtSlot(result="QVariantMap")
    def getSessionSettings(self):
        """Return the persisted session settings for the operator page."""
        s = self._session_logger
        return {
            "sessionName": s.session_name,
            "sessionId": s.session_id,
            "saveLogs": s.save_logs,
            "logFolder": s.log_folder,
        }

    @pyqtSlot(str, result=str)
    def sanitizeSessionId(self, name):
        """Snake-case a session name. Used by the QML id preview field."""
        return _SessionLogger.sanitize_id(name)

    @pyqtSlot(str)
    def setSessionName(self, name):
        s = self._session_logger
        new_name = str(name or "")
        if new_name == s.session_name:
            return
        s.session_name = new_name
        # Not persisted across runs; just emit so the QML preview
        # filename refreshes.
        self.sessionSettingsChanged.emit()

    @pyqtSlot(bool)
    def setSessionSaveLogs(self, enabled):
        s = self._session_logger
        new_val = bool(enabled)
        if new_val == s.save_logs:
            return
        s.save_logs = new_val
        s.save_settings()
        self.sessionSettingsChanged.emit()

    @pyqtSlot(str)
    def setSessionLogFolder(self, folder):
        s = self._session_logger
        # QML's FolderDialog returns a QUrl-style ``file:///...`` string.
        new_folder = str(folder or "").strip()
        if new_folder.lower().startswith("file:///"):
            # Strip the URL prefix and unquote percent-encoded characters.
            from urllib.parse import urlparse, unquote
            parsed = urlparse(new_folder)
            new_folder = unquote(parsed.path)
            # On Windows urlparse leaves a leading "/" before drive letter.
            if sys.platform == "win32" and re.match(r"^/[A-Za-z]:", new_folder):
                new_folder = new_folder[1:]
        elif new_folder.lower().startswith("file://"):
            new_folder = new_folder[7:]
        new_folder = os.path.normpath(new_folder) if new_folder else ""
        if not new_folder or new_folder == s.log_folder:
            return
        s.log_folder = new_folder
        s.save_settings()
        self.sessionSettingsChanged.emit()

    @pyqtSlot()
    def openLogFolder(self):
        """Open the configured log folder in the OS file browser."""
        path = self._session_logger.log_folder
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            self._emit_device_error("Open Log Folder", f"Could not create '{path}': {e}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - intentional shell exec on user folder
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._emit_device_error("Open Log Folder", f"Could not open '{path}': {e}")

    @pyqtSlot(result=str)
    def previewLogName(self):
        """Return the full path of the next projected run-log file."""
        s = self._session_logger
        now = datetime.now()
        datestr = now.strftime("%Y%m%d")
        run_num = s._next_run_number(datestr, s.session_id)
        filename = (f"{datestr}_{s.session_id}_run{run_num:02d}_"
                    f"{now.strftime('%H_%M_%S')}.log")
        return os.path.join(s.log_folder, filename)

    def _extract_solution_settings(self, data):
        """Extract UI-editable settings from a solution-like dict."""
        target = data.get('target', {})
        focus_position = target.get('position', [0, 0, 50])

        pulse = data.get('pulse', {})
        frequency = pulse.get('frequency', 400000)
        duration = pulse.get('duration', 2e-4)

        sequence = data.get('sequence', {})
        pulse_interval = sequence.get('pulse_interval', 0.1)
        pulse_count = sequence.get('pulse_count', 1)
        pulse_train_interval = sequence.get('pulse_train_interval', 1)
        pulse_train_count = sequence.get('pulse_train_count', 1)

        voltage = data.get('voltage', 12.0)

        def _r(value, digits):
            return round(float(value), digits)

        return {
            'xInput': _r(focus_position[0], 3),
            'yInput': _r(focus_position[1], 3),
            'zInput': _r(focus_position[2], 3),
            'frequency': _r(float(frequency) / 1e3, 3),
            'duration': _r(float(duration) * 1e6, 3),
            'voltage': _r(voltage, 3),
            'pulseInterval': _r(float(pulse_interval) * 1e3, 3),
            'pulseCount': int(pulse_count),
            'trainInterval': _r(pulse_train_interval, 6),
            'trainCount': int(pulse_train_count),
            'numModules': self._infer_num_modules_from_solution(data),
        }

    def _build_solution_export_data(self, solution_id, solution_name, num_modules,
                                    xInput, yInput, zInput, freq, voltage,
                                    pulseInterval, pulseCount, trainInterval, trainCount, durationS):
        solution = self.get_solution(
            xInput, yInput, zInput,
            freq, voltage, pulseInterval, pulseCount,
            trainInterval, trainCount, durationS,
            validate=True
        )
        if solution is None:
            raise ValueError("failed to build a valid solution")

        solution_data = self._to_json_compatible(solution)
        cleaned_id = (solution_id or "").strip() or "solution"
        cleaned_name = (solution_name or "").strip() or cleaned_id
        target_position = [float(xInput), float(yInput), float(zInput)]

        pinmap_data = self._load_pinmap_data(num_modules)
        solution_data["id"] = cleaned_id
        solution_data["name"] = cleaned_name
        solution_data["target"] = {
            "position": target_position,
            "units": "mm"
        }
        solution_data["foci"] = [{
            "position": target_position,
            "units": "mm"
        }]
        solution_data["transducer"] = self._build_transducer_from_pinmap(pinmap_data)
        return solution_data

    def _write_solution_json(self, file_path, solution_data):
        normalized_path = os.path.normpath(file_path)
        if not normalized_path.lower().endswith(".json"):
            normalized_path += ".json"

        parent_dir = os.path.dirname(normalized_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(normalized_path, 'w', encoding='utf-8') as f:
            json.dump(solution_data, f, indent=4)

        return normalized_path

    def _ensure_preset_solutions_seeded(self):
        """Seed the runtime preset directory from tracked templates if needed."""
        preset_dir = self._get_runtime_preset_solutions_path()
        os.makedirs(preset_dir, exist_ok=True)

        existing_json = glob.glob(os.path.join(preset_dir, "*.json"))
        if not existing_json:
            legacy_dir = self._get_legacy_preset_solutions_path()
            legacy_json = []
            if os.path.normcase(os.path.abspath(legacy_dir)) != os.path.normcase(os.path.abspath(preset_dir)):
                legacy_json = glob.glob(os.path.join(legacy_dir, "*.json"))

            if legacy_json:
                for legacy_path in legacy_json:
                    destination = os.path.join(preset_dir, os.path.basename(legacy_path))
                    shutil.copy2(legacy_path, destination)
                    logger.info(f"Migrated legacy preset file to user data: {destination}")
            else:
                template_dir = self._get_preset_templates_path()
                for template_path in glob.glob(os.path.join(template_dir, "*.json")):
                    destination = os.path.join(preset_dir, os.path.basename(template_path))
                    shutil.copy2(template_path, destination)
                    logger.info(f"Seeded preset file: {destination}")

        default_path = self._get_default_solution_path()
        if not os.path.exists(default_path):
            default_solution = self._build_solution_export_data(
                "default_solution",
                "Default Solution",
                1,
                "0", "0", "50",
                "400", "12.0",
                "100", "1", "0", "1", "200"
            )
            self._write_solution_json(default_path, default_solution)
            logger.info(f"Created default solution: {default_path}")

    @pyqtSlot(result=str)
    def getPresetSolutionsPath(self) -> str:
        """Return the absolute path to the preset_solutions folder for QML use."""
        self._ensure_preset_solutions_seeded()
        return self._get_runtime_preset_solutions_path()

    @pyqtSlot(result=str)
    def getDefaultSolutionFilePath(self) -> str:
        self._ensure_preset_solutions_seeded()
        return self._get_default_solution_path()

    @pyqtSlot(result='QVariantMap')
    def getDefaultSolutionSettings(self):
        """Return the boot-time default UI settings from default_solution.json."""
        try:
            self._ensure_preset_solutions_seeded()
            with open(self._get_default_solution_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            return self._extract_solution_settings(data)
        except Exception as e:
            logger.error(f"Error loading default solution settings: {e}")
            return {}

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, str, str, str, result=bool)
    def saveSolutionToFile(self, solution_id, solution_name, file_path, num_modules_str,
                           xInput, yInput, zInput, freq, voltage,
                           pulseInterval, pulseCount, trainInterval, trainCount, durationS):
        """Save the current solution to a JSON file.

        num_modules_str: number of TX modules to use for the transducer field.
        When TX is connected, this is read from hardware; when offline it comes from the UI spinbox.
        """
        try:
            try:
                num_modules = int(num_modules_str)
            except (ValueError, TypeError):
                num_modules = 0

            if num_modules <= 0:
                if self._txConnected:
                    self.queryNumModules()
                    num_modules = self._num_modules_connected
                if num_modules <= 0:
                    message = "Cannot save solution: number of TX modules must be > 0."
                    self.solutionSaveStatus.emit(False, message)
                    return False

            solution_data = self._build_solution_export_data(
                solution_id, solution_name, num_modules,
                xInput, yInput, zInput,
                freq, voltage, pulseInterval, pulseCount,
                trainInterval, trainCount, durationS
            )
            normalized_path = self._write_solution_json(file_path, solution_data)

            message = f"Saved solution '{solution_data['name']}' to {normalized_path}"
            logger.info(message)
            self.solutionSaveStatus.emit(True, message)
            return True
        except Exception as e:
            message = f"Error saving solution: {e}"
            logger.error(message)
            self.solutionSaveStatus.emit(False, message)
            return False

    @pyqtSlot(str, result=bool)
    def directSetVoltage(self, voltage_str):
        """Directly set the HV rail voltage without reconfiguring the solution."""
        if not self._hvConnected:
            logger.error("Cannot set voltage: No HV device connected")
            return False
        self._interface_mutex.lock()
        try:
            voltage = float(voltage_str)
            ok = self._call_with_comm_retry(
                "Set Voltage",
                self.interface.hvcontroller.set_voltage,
                voltage=voltage,
            )
            if ok:
                logger.info(f"Voltage directly set to {voltage} V")
                return True
            logger.error("Failed to directly set voltage")
            return False
        except LIFUCommunicationError as e:
            # Final retry attempt failed -- surface the timeout to the
            # user. Drop out of READY so they have to reconfigure before
            # starting (the device's solution state is now suspect).
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Set Voltage", e,
                                    context="Communication timeout")
            return False
        except (ValueError, TypeError) as e:
            self._emit_device_error("Set Voltage", f"Invalid voltage value: {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Voltage", e)
            return False
        except Exception as e:
            logger.error(f"Error in directSetVoltage: {e}")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, str, str, str, str, result=bool)
    def directSetSequence(self, pulseInterval, pulseCount, trainInterval, trainCount, mode):
        """Directly update trigger/sequence parameters without re-running the full configuration."""
        if not self._txConnected:
            self._emit_device_error("Set Sequence", "No TX device connected.")
            return False
        self._interface_mutex.lock()
        prev_async = self._async_mode_enabled
        self._set_async_mode(False, reason="directSetSequence")
        try:
            pulse_interval_s = float(pulseInterval) * 1e-3  # UI ms -> s
            pulse_count = int(pulseCount)
            pulse_train_interval_s = float(trainInterval)   # UI already in seconds
            if pulse_train_interval_s == 0:
                pulse_train_interval_s = pulse_count * pulse_interval_s
            pulse_train_count = int(trainCount)            
            trigger_mode = str(mode).lower()
            result = self._call_with_comm_retry(
                "Set Sequence",
                self.interface.txdevice.set_trigger,
                pulse_interval=pulse_interval_s,
                pulse_count=pulse_count,
                pulse_train_interval=pulse_train_interval_s,
                pulse_train_count=pulse_train_count,
                trigger_mode=trigger_mode,
            )
            self._update_trigger_state(result)
            # Mirror the new values into the cached configure args so a
            # fresh Start (which snapshots from _last_configure_args)
            # uses these settings rather than the previous configure's.
            if self._last_configure_args is None:
                self._last_configure_args = {}
            self._last_configure_args.update({
                "pulseInterval": str(pulseInterval),
                "pulseCount": str(pulseCount),
                "trainInterval": str(trainInterval),
                "trainCount": str(trainCount),
                "mode": str(mode),
            })
            # Run-progress bookkeeping was sized to the previous
            # trainCount; clear it so begin_run_progress re-snapshots
            # against the new values on the next Start.
            self._reset_run_state()
            logger.info(
                "Sequence settings directly updated "
                "(pulse_int=%sms pulses=%s train_int=%ss trains=%s mode=%s)",
                pulseInterval, pulseCount, trainInterval, trainCount, mode,
            )
            return True
        except LIFUCommunicationError as e:
            # All retries exhausted. The on-device trigger config is
            # now in an unknown state; force the user back through
            # Configure before another Start.
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Set Sequence", e,
                                    context="Communication timeout")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Sequence", e)
            return False
        except (ValueError, TypeError) as e:
            self._emit_device_error("Set Sequence", f"Invalid sequence parameters: {e}")
            return False
        except Exception as e:
            self._handle_lifu_error("Set Sequence", e, context="Unexpected error")
            return False
        finally:
            if prev_async and self._state == RUNNING:
                self._set_async_mode(True, reason="directSetSequence-restore")
            self._interface_mutex.unlock()

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, result=bool)
    def directSetPulse(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode):
        """Directly update pulse/transducer settings without touching the HV controller."""
        if not self._txConnected:
            self._emit_device_error("Set Pulse", "No TX device connected.")
            return False
        self._interface_mutex.lock()
        prev_async = self._async_mode_enabled
        self._set_async_mode(False, reason="directSetPulse")
        try:
            solution = self.get_solution(xInput, yInput, zInput, freq, voltage,
                                         pulseInterval, pulseCount, trainInterval, trainCount, durationS)
            if solution is None:
                self._emit_device_error("Set Pulse", "Failed to build a valid solution.")
                return False
            transducer = solution.get("transducer") if isinstance(solution, dict) else None
            invert_flag = bool(transducer["module_invert"]) if (
                transducer is not None and "module_invert" in transducer
            ) else False
            self._call_with_comm_retry(
                "Set Pulse (module invert)",
                self.interface.txdevice.set_module_invert,
                invert_flag,
            )
            self._call_with_comm_retry(
                "Set Pulse",
                self.interface.txdevice.set_solution,
                pulse=solution['pulse'],
                delays=solution['delays'],
                apodizations=solution['apodizations'],
                sequence=solution['sequence'],
                trigger_mode=str(mode).lower(),
            )
            # Mirror the new values into the cached configure args so a
            # fresh Start (which snapshots from _last_configure_args)
            # uses these settings rather than the previous configure's.
            if self._last_configure_args is None:
                self._last_configure_args = {}
            self._last_configure_args.update({
                "x": str(xInput), "y": str(yInput), "z": str(zInput),
                "freq": str(freq), "voltage": str(voltage),
                "pulseInterval": str(pulseInterval),
                "pulseCount": str(pulseCount),
                "trainInterval": str(trainInterval),
                "trainCount": str(trainCount),
                "durationUs": str(durationS),
                "mode": str(mode),
            })
            # Run-progress bookkeeping was sized to the previous
            # trainCount; clear it so begin_run_progress re-snapshots
            # against the new values on the next Start.
            self._reset_run_state()
            logger.info(
                "Pulse settings directly updated "
                "(voltage=%sV freq=%skHz focus=(%s,%s,%s)mm "
                "pulse_len=%sus pulse_int=%sms pulses=%s "
                "train_int=%ss trains=%s mode=%s)",
                voltage, freq, xInput, yInput, zInput,
                durationS, pulseInterval, pulseCount,
                trainInterval, trainCount, mode,
            )
            return True
        except LIFUCommunicationError as e:
            # All retries exhausted. The on-device pulse/solution is
            # now in an unknown state; force the user back through
            # Configure before another Start.
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Set Pulse", e,
                                    context="Communication timeout")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Pulse", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set Pulse", e, context="Unexpected error")
            return False
        finally:
            if prev_async and self._state == RUNNING:
                self._set_async_mode(True, reason="directSetPulse-restore")
            self._interface_mutex.unlock()

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def configure_transmitter(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode):
        """Simulate configuring the transmitter."""
        if not self._txConnected:
            self._emit_device_error("Configure Transmitter", "No TX device connected.")
            return
        self.queryNumModules()
        solution = self.get_solution(xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS)
        if solution is None:
            self._emit_device_error("Configure Transmitter", "Failed to build a valid solution.")
            return

        self._interface_mutex.lock()
        # Async STATUS frames share the TX device's CDC IN endpoint with
        # command responses; large set_solution writes (write_block chunks)
        # routinely race with STATUS emissions when async is left on.
        # Force async OFF for the duration of the write.
        self._set_async_mode(False, reason="configure_transmitter")
        try:
            self._call_with_comm_retry(
                "Configure Transmitter",
                self.interface.set_solution,
                solution,
                trigger_mode=mode,
            )
            self._configured = True
            # Snapshot the args used so pause/resume can rebuild the
            # trigger via set_trigger only (cheap) instead of redoing the
            # full set_solution write. Stored as a dict of strings to
            # match the QML-facing slot signature.
            self._last_configure_args = {
                "x": str(xInput), "y": str(yInput), "z": str(zInput),
                "freq": str(freq), "voltage": str(voltage),
                "pulseInterval": str(pulseInterval),
                "pulseCount": str(pulseCount),
                "trainInterval": str(trainInterval),
                "trainCount": str(trainCount),
                "durationUs": str(durationS),
                "mode": str(mode),
            }
            # Reset run-progress bookkeeping -- a fresh Configure
            # invalidates any in-progress run state.
            self._reset_run_state()
            self.update_state()
            self._apply_auto_hv_for_state()
            preset_id = (self._active_preset or {}).get("id", "(none)")
            logger.info(
                f"[CONFIGURE] Transmitter configured: preset={preset_id} "
                f"voltage={voltage}V freq={freq}kHz focus=({xInput},{yInput},{zInput})mm "
                f"pulse_len={durationS}us pulse_int={pulseInterval}ms pulses={pulseCount} "
                f"train_int={trainInterval}s trains={trainCount} mode={mode}"
            )
        except LIFUCommunicationError as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e,
                                    context="Communication timeout")
        except LIFUSolutionError as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e,
                                    context="Solution failed safety checks")
        except LIFUError as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e)
        except Exception as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def reset_configuration(self):
        """Reset system configuration to defaults.

        Also releases any solution that was loaded from file so the UI
        controls become editable again and the next Configure rebuilds
        the solution from the on-screen parameters rather than re-using
        the buffered file-loaded solution.
        """
        self._configured = False
        if self._solution_loaded:
            released = self._solution_name
            self._solution_loaded = False
            self._loaded_solution_data = None
            self._solution_name = ""
            self.solutionStateChanged.emit()
            logger.info(f"Released loaded solution '{released}' on reset")
        # Drop any cached configure args + run-progress state.
        self._last_configure_args = None
        self._active_preset = None
        self._reset_run_state()
        self.update_state()
        self._apply_auto_hv_for_state()
        logger.info("Configuration reset")

    @pyqtSlot()
    def start_sonication(self):
        """Start the beam, transitioning to RUNNING state."""
        if self._state != READY:
            return
        if not self._hv_ready():
            self._emit_device_error(
                "Start Sonication",
                "HV is not ready (disconnected or HV enable set to OFF).",
            )
            return
        self._interface_mutex.lock()
        try:
            # Determine HV control parameters based on enable mode.
            # AUTO already energized HV at Configure time, but turn_hv_on=True
            # is harmless if it's already on. WHILE_RUNNING turns it on now.
            turn_hv_on = self._hv_enable_mode == HV_EN_WHILE_RUNNING
            wait_for_settle = turn_hv_on  # Always wait for settle

            # Enable the unsolicited STATUS stream so the UI gets push-mode
            # temperature and trigger-state updates without polling the TX
            # device while it is sonicating.
            logger.info('[START] Turning on Sonication')
            self._call_with_comm_retry(
                "Start Sonication",
                self.interface.start_sonication,
                turn_hv_on=turn_hv_on,
                wait_for_settle=wait_for_settle,
                async_mode=True,
            )
            self._async_mode_enabled = True
            self._state = RUNNING
            self.stateChanged.emit(self._state)
            logger.info(f"[START] Sonication started "
                        f"(HV mode: {HV_EN_MODES[self._hv_enable_mode]}, "
                        f"turn_hv_on={turn_hv_on})")
        except LIFUCommunicationError as e:
            # SDK leaves async OFF on early-write failures. Stay in
            # READY -- start failure doesn't invalidate the loaded
            # solution, the user can just try Start again.
            self._async_mode_enabled = False
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e,
                                    context="Communication timeout")
        except LIFUHVSettleError as e:
            # SDK leaves async OFF on settle failure (start_sonication only
            # toggles it after the HV settle succeeds). Mirror that here.
            self._async_mode_enabled = False
            # Stay in READY state; notify UI of the failure.
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e,
                                    context="HV rail did not settle")
        except LIFUError as e:
            self._async_mode_enabled = False
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e)
        except Exception as e:
            self._async_mode_enabled = False
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def stop_sonication(self):
        """Stop the beam and return to READY state."""
        if self._state != RUNNING:
            return
        self._interface_mutex.lock()
        try:
            # AUTO keeps HV energized while still configured; only WHILE_RUNNING
            # drops the rail at sonication-stop.
            turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
            self._call_with_comm_retry(
                "Stop Sonication",
                self.interface.stop_sonication,
                turn_hv_off=turn_hv_off,
            )
            # SDK's stop_sonication() turns async OFF after stopping the
            # trigger; mirror that on our tracker so subsequent state
            # queries (e.g. directSet*) don't try to restore it.
            self._async_mode_enabled = False
            self._state = READY
            self.stateChanged.emit(self._state)
            logger.info(f"[STOP] Sonication stopped "
                        f"(HV mode: {HV_EN_MODES[self._hv_enable_mode]}, "
                        f"turn_hv_off={turn_hv_off})")
        except LIFUCommunicationError as e:
            # Do not change local state -- hardware may still be running.
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Stop Sonication", e,
                                    context="Communication timeout")
        except LIFUError as e:
            # Do not change local state if the stop failed – hardware may still be running.
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Stop Sonication", e)
        except Exception as e:
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Stop Sonication", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    # =====================================================================
    # Thermal management (TX)
    # =====================================================================

    def _max_tx_temperature(self):
        """Return the hottest known TX module temperature, or NaN if none."""
        max_t = float('nan')
        for t in self._tx_temperatures:
            if isinstance(t, (int, float)) and not (t != t):  # not NaN
                if max_t != max_t or t > max_t:  # NaN-safe
                    max_t = float(t)
        return max_t

    def _on_tx_temperature_for_thermal(self, module, tx_temp, amb_temp):
        """Latch latest temperature and run the cooldown/shutdown state
        machine. Called for every emission of ``temperatureTxUpdated``
        (both polled queries and STATUS-frame parses)."""
        # Latch latest per-module reading.
        if isinstance(tx_temp, (int, float)) and not (tx_temp != tx_temp):
            while len(self._tx_temperatures) <= module:
                self._tx_temperatures.append(float('nan'))
            self._tx_temperatures[module] = float(tx_temp)
        self._evaluate_thermal_state()

    def _evaluate_thermal_state(self):
        """Cooldown/shutdown state machine. Mirrors what OperatorInterface.qml used to
        do, but applied centrally so policy lives next to the device
        controls rather than in the page."""
        t = self._max_tx_temperature()
        if t != t:  # NaN -- no telemetry yet, don't latch cooling
            if self._cooling_down:
                self._cooling_down = False
                self.coolingStateChanged.emit(False)
            return

        # While running, only the >=75C hard-shutdown path may act.
        # Crossing 50C mid-run must NOT flip into cooldown (which would
        # try to toggle HV enable mode -- something setHvEnableMode
        # rejects with a "cannot change while running" warning).
        if self._state == RUNNING:
            if t >= THERMAL_SHUTDOWN_THRESHOLD_C:
                self._trigger_thermal_shutdown(t)
            return

        was_cooling = self._cooling_down
        now_cooling = (t > THERMAL_COOLING_THRESHOLD_C)

        if t >= THERMAL_SHUTDOWN_THRESHOLD_C:
            self._trigger_thermal_shutdown(t)
            now_cooling = True

        if now_cooling and not was_cooling:
            # Entering cooldown: force HV off, stash previous mode so
            # we can restore it once the device cools.
            if self._hv_enable_mode != HV_EN_OFF and self._pre_cooldown_hv_mode < 0:
                self._pre_cooldown_hv_mode = self._hv_enable_mode
            if self._hv_enable_mode != HV_EN_OFF:
                self.setHvEnableMode(HV_EN_OFF)

        if (not now_cooling) and was_cooling:
            # Just dropped below cool threshold. Re-arm the shutdown
            # one-shot and restore the previous HV mode so Start is
            # available again immediately.
            self._thermal_shutdown_active = False
            if self._pre_cooldown_hv_mode >= 0 and self._pre_cooldown_hv_mode != HV_EN_OFF:
                self.setHvEnableMode(self._pre_cooldown_hv_mode)
            self._pre_cooldown_hv_mode = -1

        if now_cooling != was_cooling:
            self._cooling_down = now_cooling
            self.coolingStateChanged.emit(now_cooling)

    def _trigger_thermal_shutdown(self, observed_temp):
        """One-shot hard abort on >=shutdown threshold. Idempotent until
        the device cools back below the cool threshold."""
        if self._thermal_shutdown_active:
            return
        self._thermal_shutdown_active = True
        # If we were running or paused, mark the run aborted (and stop
        # the trigger if it's still going). Skip the paused intermediate
        # state -- thermal shutdown is a hard abort.
        if self._run_state in ("running", "paused"):
            self._abort_run_internal(emit_state=True)
        if self._state == RUNNING:
            try:
                # Mirror stop_sonication's HV policy.
                turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
                self._call_with_comm_retry(
                    "Thermal shutdown stop",
                    self.interface.stop_sonication,
                    turn_hv_off=turn_hv_off,
                )
            except Exception as e:
                logger.warning(f"Thermal shutdown stop_sonication failed: {e}")
            self._async_mode_enabled = False
            self._state = READY
            self.stateChanged.emit(self._state)
        # Force HV off regardless of current enable mode. Stash previous
        # mode (if not already stashed) so cooldown-exit can restore it.
        if self._hv_enable_mode != HV_EN_OFF and self._pre_cooldown_hv_mode < 0:
            self._pre_cooldown_hv_mode = self._hv_enable_mode
        if self._hv_enable_mode != HV_EN_OFF:
            self.setHvEnableMode(HV_EN_OFF)
        logger.warning(f"[THERMAL] Shutdown triggered: TX {observed_temp:.1f} C >= "
                       f"{THERMAL_SHUTDOWN_THRESHOLD_C:.1f} C; HV forced off")
        self.thermalShutdownEvent.emit(float(observed_temp),
                                       float(THERMAL_SHUTDOWN_THRESHOLD_C),
                                       float(THERMAL_COOLING_THRESHOLD_C))

    # =====================================================================
    # Run-progress / pause-resume state machine
    # =====================================================================

    def _open_run_log(self):
        """Open the per-run log file (no-op if save-logs is disabled).

        The connector logs an opening banner via the regular logger so
        the message is also visible on the console handler. After
        ``begin_run`` returns, every ``logger.*`` call from any module
        is mirrored into the file until ``_close_run_log``.
        """
        path = self._session_logger.begin_run()
        # Banner first so it's the first thing in the file (and console).
        if path:
            logger.info("=" * 70)
            logger.info(
                "[SESSION] Run log opened: session='%s' id='%s' file='%s'",
                self._session_logger.session_name or "(unspecified)",
                self._session_logger.session_id,
                path,
            )
            self._log_run_summary(prefix="[CONFIG]")
            logger.info("=" * 70)
            self.runLogStarted.emit(path)
        elif not self._session_logger.save_logs:
            logger.info("[SESSION] Run started (logging disabled)")

    def _close_run_log(self, reason):
        """Close the per-run log file (no-op if no log is open)."""
        if not self._session_logger.is_active:
            return
        # Footer/summary first so it lands inside the still-attached file.
        delivered_trains = min(self._run_trains_delivered_before_block,
                               self._run_original_train_total)
        delivered_pulses = delivered_trains * self._run_original_pulse_count
        elapsed_s = self._run_elapsed_ms / 1000.0
        logger.info("=" * 70)
        logger.info(
            "[SESSION] Run ended: reason=%s trains=%d/%d pulses=%d blocks=%d elapsed=%.3fs",
            reason.upper(),
            delivered_trains,
            self._run_original_train_total,
            delivered_pulses,
            self._run_block_count,
            elapsed_s,
        )
        logger.info("=" * 70)
        path = self._session_logger.end_run()
        if path:
            self.runLogFinalized.emit(path)

    def _log_run_summary(self, prefix="[CONFIG]"):
        """Log the parameters that this run was configured with."""
        a = self._last_configure_args or {}
        preset_id = (self._active_preset or {}).get("id", "(none)")
        logger.info(
            "%s preset=%s voltage=%sV freq=%skHz focus=(%s,%s,%s)mm",
            prefix, preset_id, a.get("voltage", "?"), a.get("freq", "?"),
            a.get("x", "?"), a.get("y", "?"), a.get("z", "?"),
        )
        logger.info(
            "%s pulse_len=%sus pulse_int=%sms pulses=%s train_int=%ss trains=%s mode=%s",
            prefix, a.get("durationUs", "?"), a.get("pulseInterval", "?"),
            a.get("pulseCount", "?"), a.get("trainInterval", "?"),
            a.get("trainCount", "?"), a.get("mode", "?"),
        )
        logger.info(
            "%s total_duration=%.3fs total_trains=%d hv_mode=%s tx_modules=%s",
            prefix, self._run_original_duration_s, self._run_original_train_total,
            HV_EN_MODES.get(self._hv_enable_mode, "?"),
            self._num_modules_connected if self._num_modules_connected > 0
            else 1,
        )
        # Compact per-module transducer info. One line per module so a
        # multi-module run still yields only a few lines, but the full
        # user_config is still recoverable from these fields.
        for idx in sorted(self._module_user_configs.keys()):
            cfg = self._module_user_configs[idx]
            mod = cfg.get("module") or {}
            sens = mod.get("sensitivity") or []
            sens_str = ",".join(f"{int(f/1000)}k:{int(v)}" for f, v in sens) if sens else "(none)"
            logger.info(
                "%s tx[%d] sn=%s hwid=%s hw=%s fw=%s sdk=%s freq=%skHz "
                "module=%s name='%s' nx=%s ny=%s pitch=%s kerf=%s "
                "xt=%s/%s sens[%s]",
                prefix, idx,
                cfg.get("sn", "?"), cfg.get("hwid", "?"),
                cfg.get("hw_ver", "?"), cfg.get("fw_ver", "?"),
                cfg.get("sdk_ver", "?"), cfg.get("freq", "?"),
                mod.get("id", "?"), mod.get("name", "?"),
                mod.get("nx", "?"), mod.get("ny", "?"),
                mod.get("pitch", "?"), mod.get("kerf", "?"),
                mod.get("crosstalk_frac", "?"), mod.get("crosstalk_dist", "?"),
                sens_str,
            )

    def _set_run_state(self, new_state):
        if new_state == self._run_state:
            return
        self._run_state = new_state
        self.runStateChanged.emit(new_state)
        self.runProgressChanged.emit()
        # Finalize the log on terminal transitions (after emitting the
        # state change so any handlers logging from the slot still hit
        # the file).
        if new_state in ("finished", "aborted"):
            self._close_run_log(reason=new_state)

    def _reset_run_state(self):
        """Clear all run-progress bookkeeping back to idle."""
        self._run_original_train_total = 0
        self._run_original_duration_s = 0.0
        self._run_original_pulse_count = 0
        self._run_trains_delivered_before_block = 0
        self._run_in_block_total = 0
        self._run_in_block_current = 0
        self._run_block_count = 0
        self._run_start_time_ms = 0.0
        self._run_elapsed_ms = 0.0
        self._set_run_state("idle")

    def _snapshot_original_sequence(self):
        """Capture original sequence parameters from the last
        configure_transmitter args. Called on a fresh Start so pause/
        resume can shorten the device-side trainCount while the UI
        keeps reporting against the original total."""
        a = self._last_configure_args
        if not a:
            return
        try:
            train_count = int(float(a.get("trainCount", 0)))
        except (ValueError, TypeError):
            train_count = 0
        try:
            pulse_count = int(float(a.get("pulseCount", 0)))
        except (ValueError, TypeError):
            pulse_count = 0
        # Derive original duration from train_count * train_period (as the
        # firmware sees it).
        try:
            pulse_interval_s = float(a.get("pulseInterval", 0)) * 1e-3
            train_interval_s = float(a.get("trainInterval", 0))
        except (ValueError, TypeError):
            pulse_interval_s = 0.0
            train_interval_s = 0.0
        if train_interval_s <= 0:
            train_interval_s = max(0.0, pulse_count * pulse_interval_s)
        self._run_original_train_total = max(0, train_count)
        self._run_original_pulse_count = max(0, pulse_count)
        self._run_original_duration_s = max(0.0, train_count * train_interval_s)

    def _apply_train_count(self, train_count):
        """Push a new pulse_train_count to the device via set_trigger
        only (cheap), reusing the rest of the sequence parameters from
        the last configure_transmitter snapshot. Returns True on
        success."""
        a = self._last_configure_args
        if not a:
            return False
        try:
            pulse_interval_s = float(a.get("pulseInterval", 0)) * 1e-3
            pulse_count = int(float(a.get("pulseCount", 0)))
            train_interval_s = float(a.get("trainInterval", 0))
        except (ValueError, TypeError) as e:
            logger.warning(f"Cannot apply train count -- bad cached args: {e}")
            return False
        if train_interval_s == 0:
            train_interval_s = pulse_count * pulse_interval_s
        trigger_mode = str(a.get("mode", "Sequence")).lower()
        prev_async = self._async_mode_enabled
        self._interface_mutex.lock()
        self._set_async_mode(False, reason="apply_train_count")
        try:
            result = self.interface.txdevice.set_trigger(
                pulse_interval=pulse_interval_s,
                pulse_count=pulse_count,
                pulse_train_interval=train_interval_s,
                pulse_train_count=int(train_count),
                trigger_mode=trigger_mode,
            )
            self._update_trigger_state(result)
            return True
        except LIFUError as e:
            self._handle_lifu_error("Update Trigger", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Update Trigger", e, context="Unexpected error")
            return False
        finally:
            if prev_async and self._state == RUNNING:
                self._set_async_mode(True, reason="apply_train_count-restore")
            self._interface_mutex.unlock()

    def _on_progress_for_run_state(self, pt_curr, pt_total, p_curr, p_total):
        """Drive ``_run_state`` from STATUS-frame progress."""
        if self._run_state != "running":
            return
        if pt_total > 0 and pt_curr >= pt_total:
            # Current block finished. Roll into running total.
            delivered = self._run_in_block_total if self._run_in_block_total > 0 else pt_curr
            self._run_trains_delivered_before_block += delivered
            self._run_in_block_current = 0
            self._run_in_block_total = 0
            if self._run_trains_delivered_before_block >= self._run_original_train_total:
                # Natural completion of the full sequence.
                self._run_elapsed_ms = (time.monotonic() * 1000.0) - self._run_start_time_ms
                logger.info(f"[FINISH] Run completed: delivered "
                            f"{self._run_trains_delivered_before_block} trains "
                            f"in {self._run_elapsed_ms/1000.0:.2f} s "
                            f"(original total: {self._run_original_train_total} trains, "
                            f"{self._run_original_duration_s:.3f} s)")
                self._set_run_state("finished")
                # Restore original train count for the next run; safe to
                # do now since the device is finishing on its own.
                self._restore_original_trigger_async()
            else:
                # Block ended mid-sequence (shouldn't normally happen
                # outside a pause). Just refresh the UI.
                self.runProgressChanged.emit()
            return
        # Mid-block tick.
        self._run_in_block_current = pt_curr + 1
        if (self._run_in_block_total > 0
                and self._run_in_block_current > self._run_in_block_total):
            self._run_in_block_current = self._run_in_block_total
        self.runProgressChanged.emit()

    def _on_state_changed_for_run_state(self, state):
        """When the device naturally finishes, the SDK transitions
        RUNNING -> READY (via stop_sonication in queryTxTemperature).
        We've already finalized in ``_on_progress_for_run_state``; this
        handler just makes sure terminal states clean up if the progress
        signal didn't catch the boundary (e.g. firmware quirks).

        IMPORTANT: ``stateChanged`` is emitted synchronously from
        ``stop_sonication`` (and our own pause/abort paths). Pause/abort
        therefore flip ``_run_state`` to a non-"running" sentinel BEFORE
        invoking stop_sonication so this handler sees the new state and
        won't mis-mark the run as finished. Only the natural-completion
        path (firmware ran out of trains by itself, the SDK silently
        flips to READY in queryTxTemperature) reaches the body of this
        handler with ``_run_state == "running"``.
        """
        if state != RUNNING and self._run_state == "running":
            # Natural completion: only mark finished if the firmware
            # actually advanced the in-block counter to its full block
            # total. This avoids spurious "finished" transitions when
            # the device drops to READY for some other reason and the
            # block delivered count happens to coincide with the
            # original total.
            inblock = self._run_in_block_current if self._run_in_block_total > 0 else 0
            block_complete = (self._run_in_block_total > 0
                              and inblock >= self._run_in_block_total)
            total_delivered = self._run_trains_delivered_before_block + inblock
            if (block_complete
                    and total_delivered >= self._run_original_train_total > 0):
                self._run_trains_delivered_before_block = self._run_original_train_total
                self._run_in_block_current = 0
                self._run_in_block_total = 0
                self._run_elapsed_ms = (time.monotonic() * 1000.0) - self._run_start_time_ms
                logger.info(f"[FINISH] Run completed (via state change): delivered "
                            f"{total_delivered} trains "
                            f"in {self._run_elapsed_ms/1000.0:.2f} s "
                            f"(original total: {self._run_original_train_total} trains, "
                            f"{self._run_original_duration_s:.3f} s)")
                self._set_run_state("finished")
                self._restore_original_trigger_async()

    def _restore_original_trigger_async(self):
        """Reapply the originally-programmed trainCount via set_trigger.
        Safe to call any time the device is not actively running."""
        if self._state == RUNNING:
            return
        if not self._last_configure_args:
            return
        try:
            orig_count = int(float(self._last_configure_args.get("trainCount", 0)))
        except (ValueError, TypeError):
            return
        if orig_count <= 0:
            return
        self._apply_train_count(orig_count)

    def _abort_run_internal(self, emit_state=True):
        """Finalize the run as aborted using whatever has been delivered
        so far. Caller is responsible for stopping the trigger and any
        HV cleanup; this just updates the run-progress bookkeeping."""
        if self._run_state == "running":
            inblock = self._run_in_block_current if self._run_in_block_total > 0 else 0
            self._run_trains_delivered_before_block += inblock
        self._run_in_block_current = 0
        self._run_in_block_total = 0
        if self._run_start_time_ms > 0 and self._run_elapsed_ms <= 0:
            self._run_elapsed_ms = (time.monotonic() * 1000.0) - self._run_start_time_ms
        if emit_state:
            self._set_run_state("aborted")
        else:
            self._run_state = "aborted"
        # Restoring the original trigger after an abort happens via the
        # caller (after stop_sonication has settled) since set_trigger
        # cannot run while RUNNING.

    @pyqtSlot()
    def pause_sonication(self):
        """Pause an active sonication. Stops the trigger and
        immediately reprograms the device with a shorter trainCount
        covering only the *remaining* portion of the original sequence,
        so that ``resume_sonication()`` can simply call
        ``start_sonication()`` (no further set_trigger / set_solution
        needed)."""
        if self._state != RUNNING or self._run_state != "running":
            logger.warning("pause_sonication called while not running")
            return
        # 1. Snapshot delivered count from the current block. Be
        #    defensive about the in-block counter potentially over-
        #    running its block total (it is set to ``pt_curr+1`` on each
        #    STATUS frame, which can briefly exceed the block total
        #    around the natural-completion boundary).
        inblock = self._run_in_block_current if self._run_in_block_total > 0 else 0
        if self._run_in_block_total > 0 and inblock > self._run_in_block_total:
            inblock = self._run_in_block_total
        delivered_so_far = min(self._run_trains_delivered_before_block + inblock,
                               self._run_original_train_total)
        remaining = max(0, self._run_original_train_total - delivered_so_far)

        # 2. Flip our run-state to "paused" *before* invoking
        #    stop_sonication. stop_sonication emits stateChanged
        #    synchronously, which would otherwise re-enter
        #    ``_on_state_changed_for_run_state`` while ``_run_state`` is
        #    still "running" and double-count ``_run_in_block_current``
        #    against ``_run_trains_delivered_before_block`` -- causing
        #    the bar to jump to 100% and the run to be marked finished.
        #    Same hazard exists for any late STATUS-frame progress
        #    emission still in flight.
        self._run_trains_delivered_before_block = delivered_so_far
        self._run_in_block_current = 0
        self._run_in_block_total = remaining
        self._run_state = "paused"  # raw assignment; signal at end

        # 3. Stop the trigger.
        self._interface_mutex.lock()
        try:
            turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
            self.interface.stop_sonication(turn_hv_off=turn_hv_off)
            self._async_mode_enabled = False
            self._state = READY
            self.stateChanged.emit(self._state)
        except LIFUError as e:
            self._handle_lifu_error("Pause Sonication", e)
        except Exception as e:
            self._handle_lifu_error("Pause Sonication", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

        # 4. If anything remains, push the shortened trainCount so
        #    Resume only has to call start_sonication. If the user hit
        #    Pause exactly at sequence completion (remaining == 0)
        #    promote to "finished" instead of leaving a degenerate
        #    paused state with nothing to resume.
        if remaining > 0:
            self._apply_train_count(remaining)
            self.runStateChanged.emit("paused")
            self.runProgressChanged.emit()
            logger.info(f"[PAUSE] Sonication paused at "
                        f"{delivered_so_far}/{self._run_original_train_total} trains, "
                        f"{remaining} remaining")
        else:
            self._run_elapsed_ms = (time.monotonic() * 1000.0) - self._run_start_time_ms
            self._run_state = "finished"
            self.runStateChanged.emit("finished")
            self.runProgressChanged.emit()
            self._restore_original_trigger_async()
            logger.info("[PAUSE] Pause arrived at sequence completion; marking finished")
            self._close_run_log(reason="finished")

    @pyqtSlot()
    def resume_sonication(self):
        """Resume a previously-paused sonication. Assumes
        ``pause_sonication`` already pushed the shortened trigger
        settings, so this is just a start_sonication."""
        if self._run_state != "paused":
            logger.warning("resume_sonication called while not paused")
            return
        if self._run_in_block_total <= 0:
            # Defensive: paused with nothing left to do. Promote to
            # finished and restore the original trigger for the next run.
            if self._run_elapsed_ms <= 0 and self._run_start_time_ms > 0:
                self._run_elapsed_ms = (time.monotonic() * 1000.0) - self._run_start_time_ms
            self._set_run_state("finished")
            self._restore_original_trigger_async()
            return
        self._run_block_count += 1
        self._run_in_block_current = 0
        self._set_run_state("running")
        logger.info(f"[RESUME] Continuing run; block #{self._run_block_count}, "
                    f"{self._run_in_block_total} trains remaining")
        self.start_sonication()

    @pyqtSlot()
    def abort_sonication(self):
        """Hard-stop a running or paused sonication. If running, stops
        the trigger first. Restores the originally-programmed trainCount
        (via set_trigger) so the next Start replays the user's full
        selection."""
        if self._run_state not in ("running", "paused"):
            return
        # Snapshot delivered count and flip run-state to "aborted"
        # BEFORE stop_sonication. Same race rationale as pause: the
        # synchronous stateChanged emission must not see _run_state ==
        # "running" or it can mis-mark as finished.
        if self._run_state == "running":
            inblock = self._run_in_block_current if self._run_in_block_total > 0 else 0
            if self._run_in_block_total > 0 and inblock > self._run_in_block_total:
                inblock = self._run_in_block_total
            self._run_trains_delivered_before_block = min(
                self._run_trains_delivered_before_block + inblock,
                self._run_original_train_total
            )
        self._run_in_block_current = 0
        self._run_in_block_total = 0
        if self._run_start_time_ms > 0 and self._run_elapsed_ms <= 0:
            self._run_elapsed_ms = (time.monotonic() * 1000.0) - self._run_start_time_ms
        self._run_state = "aborted"  # raw assignment; signal after stop

        if self._state == RUNNING:
            self._interface_mutex.lock()
            try:
                turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
                self.interface.stop_sonication(turn_hv_off=turn_hv_off)
                self._async_mode_enabled = False
                self._state = READY
                self.stateChanged.emit(self._state)
            except LIFUError as e:
                self._handle_lifu_error("Abort Sonication", e)
            except Exception as e:
                self._handle_lifu_error("Abort Sonication", e, context="Unexpected error")
            finally:
                self._interface_mutex.unlock()
        self.runStateChanged.emit("aborted")
        self.runProgressChanged.emit()
        self._restore_original_trigger_async()
        delivered = self._run_trains_delivered_before_block
        logger.info(f"[ABORT] User abort at "
                    f"{delivered}/{self._run_original_train_total} trains "
                    f"after {self._run_elapsed_ms/1000.0:.2f} s")
        self._close_run_log(reason="aborted")

    @pyqtSlot()
    def begin_run_progress(self):
        """Start the run-progress state machine on a fresh Start. Should
        be called by the UI immediately before ``start_sonication()`` so
        the run-state transitions to ``"running"`` synchronously."""
        if self._run_state in ("running", "paused"):
            return
        self._snapshot_original_sequence()
        self._run_trains_delivered_before_block = 0
        self._run_in_block_total = self._run_original_train_total
        self._run_in_block_current = 0
        self._run_block_count = 1
        self._run_start_time_ms = time.monotonic() * 1000.0
        self._run_elapsed_ms = 0.0
        # Open the per-run log file BEFORE flipping run-state so the
        # state-change banner ends up in the file too.
        self._open_run_log()
        self._set_run_state("running")

    @pyqtSlot()
    def clear_run_progress(self):
        """Reset the run-progress state machine back to idle. Used by
        the UI when the user mutates parameters that invalidate the
        previous run's status (e.g. switching presets or changing the
        total duration via directSetSequence)."""
        if self._run_state in ("running", "paused"):
            return
        self._reset_run_state()

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def txConnected(self):
        """Expose TX connection status to QML."""
        return self._txConnected

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def hvConnected(self):
        """Expose HV connection status to QML."""
        return self._hvConnected

    @pyqtProperty(int, notify=stateChanged)
    def state(self):
        """Expose state as a QML property."""
        return self._state
    
    @pyqtProperty(bool, notify=connectionStatusChanged)
    def txConnected(self):
        """Expose TX connection status to QML."""
        return self._txConnected

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def hvConnected(self):
        """Expose HV connection status to QML."""
        return self._hvConnected
        
    @pyqtProperty(bool, notify=triggerStateChanged)
    def triggerEnabled(self):
        """Expose trigger enabled status to QML."""
        return self._trigger_state
    
    @pyqtProperty(bool, notify=solutionStateChanged)
    def solutionLoaded(self):
        """Expose solution loaded status to QML."""
        return self._solution_loaded
    
    @pyqtProperty(str, notify=solutionStateChanged)
    def solutionName(self):
        """Expose loaded solution name to QML."""
        return self._solution_name
    
    @pyqtProperty(int, notify=hvEnableModeChanged)
    def hvEnableMode(self):
        """Expose HV enable mode to QML."""
        return self._hv_enable_mode

    # ---- Thermal / cooldown properties (read-only to QML) ----
    @pyqtProperty(bool, notify=coolingStateChanged)
    def coolingDown(self):
        """True while the hottest TX module is above the cool threshold."""
        return self._cooling_down

    @pyqtProperty(float, constant=True)
    def coolingThresholdC(self):
        return float(THERMAL_COOLING_THRESHOLD_C)

    @pyqtProperty(float, constant=True)
    def shutdownThresholdC(self):
        return float(THERMAL_SHUTDOWN_THRESHOLD_C)

    # ---- Run-progress properties (read-only to QML) ----
    @pyqtProperty(str, notify=runStateChanged)
    def runState(self):
        """One of "idle", "running", "paused", "finished", "aborted"."""
        return self._run_state

    @pyqtProperty(int, notify=runProgressChanged)
    def runOriginalTrainTotal(self):
        return int(self._run_original_train_total)

    @pyqtProperty(float, notify=runProgressChanged)
    def runOriginalDurationS(self):
        return float(self._run_original_duration_s)

    @pyqtProperty(int, notify=runProgressChanged)
    def runOriginalPulseCount(self):
        return int(self._run_original_pulse_count)

    @pyqtProperty(int, notify=runProgressChanged)
    def runOverallDeliveredTrains(self):
        if self._run_state == "finished":
            return int(self._run_original_train_total)
        inblock = max(0, min(self._run_in_block_current,
                             self._run_in_block_total))
        return int(min(self._run_original_train_total,
                       self._run_trains_delivered_before_block + inblock))

    @pyqtProperty(float, notify=runProgressChanged)
    def runOverallFraction(self):
        if self._run_state == "idle":
            return 0.0
        if self._run_state == "finished":
            return 1.0
        if self._run_original_train_total <= 0:
            return 0.0
        return max(0.0, min(1.0,
                            float(self.runOverallDeliveredTrains)
                            / float(self._run_original_train_total)))

    @pyqtProperty(int, notify=runProgressChanged)
    def runBlockCount(self):
        return int(self._run_block_count)

    @pyqtProperty(float, notify=runProgressChanged)
    def runElapsedSeconds(self):
        if self._run_elapsed_ms > 0:
            return float(self._run_elapsed_ms) / 1000.0
        if self._run_start_time_ms > 0 and self._run_state in ("running", "paused"):
            return ((time.monotonic() * 1000.0) - self._run_start_time_ms) / 1000.0
        return 0.0
    
    @pyqtSlot(int)
    def setHvEnableMode(self, hv_en_mode):
        """Set HV enable mode (0=AUTO, 1=ON, 2=OFF, 3=WHILE_RUNNING)."""
        if hv_en_mode not in HV_EN_MODES:
            logger.warning(f"Invalid HV enable mode: {hv_en_mode}")
            return

        # Prevent changing HV mode while running
        if self._state == RUNNING:
            logger.warning("Cannot change HV enable mode while running")
            return

        # Prevent setting "ON" mode when HV is not connected
        if hv_en_mode == HV_EN_ON and not self._hvConnected:  # ON mode
            logger.warning("Cannot set HV to ON mode: HV device not connected")
            return

        old_mode = self._hv_enable_mode
        self._hv_enable_mode = hv_en_mode
        self.hvEnableModeChanged.emit(hv_en_mode)
        logger.info(f"HV enable mode changed: {HV_EN_MODES.get(old_mode, 'Unknown')} -> {HV_EN_MODES.get(hv_en_mode, 'Unknown')}")

        # Handle immediate HV changes for ON/OFF/AUTO/WHILE_RUNNING modes.
        # AUTO turns the rail on as soon as the device is configured (READY),
        # otherwise it stays off. WHILE_RUNNING never asserts here (waits for
        # start_sonication).
        if self._hvConnected:
            try:
                if hv_en_mode == HV_EN_ON:
                    self.interface.hvcontroller.turn_hv_on()
                    logger.info("HV turned on (ON mode)")
                elif hv_en_mode == HV_EN_OFF:
                    self.interface.hvcontroller.turn_hv_off()
                    logger.info("HV turned off (OFF mode)")
                elif hv_en_mode == HV_EN_AUTO:
                    if self._state >= READY:
                        self.interface.hvcontroller.turn_hv_on()
                        logger.info("HV turned on (AUTO mode, configured)")
                    else:
                        self.interface.hvcontroller.turn_hv_off()
                        logger.info("HV turned off (AUTO mode, not configured)")
                elif hv_en_mode == HV_EN_WHILE_RUNNING:
                    # Drop the rail unless we're already mid-run; only
                    # start_sonication should energize in this mode.
                    if self._state != RUNNING:
                        self.interface.hvcontroller.turn_hv_off()
                        logger.info("HV turned off (WHILE_RUNNING mode, not running)")
            except LIFUError as e:
                self._handle_lifu_error("HV Enable Mode", e,
                                        context=f"Failed to apply mode '{HV_EN_MODES.get(hv_en_mode, 'Unknown')}'")
            except Exception as e:
                self._handle_lifu_error("HV Enable Mode", e, context="Unexpected error")

            # Refresh the QML side immediately so the HV LED reacts without
            # waiting for the next telemetry poll.
            try:
                hv_state = self.interface.hvcontroller.get_hv_status()
                v12_state = self.interface.hvcontroller.get_12v_status()
                self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
            except Exception as e:
                logger.warning(f"Could not refresh power status after HV mode change: {e}")

        # Update state after mode change (important for OFF->ON transitions)
        self.update_state()
    
    @pyqtSlot(result='QStringList')
    def getHvEnableModes(self):
        """Return the list of HV enable mode options.

        Index order matches the HV_EN_* integer constants and is the
        contract used by QML ComboBoxes (Controller, Operator) for index-based
        selection. Append-only.
        """
        return ["AUTO", "ON", "OFF", "WHILE_RUNNING"]
        
    @pyqtSlot(result=bool)
    def canSetHvOn(self):
        """Return whether HV can be set to ON mode (requires HV connection)."""
        return self._hvConnected
    
    @pyqtSlot()
    def queryHvInfo(self):
        """Fetch and emit device information."""
        self._interface_mutex.lock()
        try:
            fw_version = self.interface.hvcontroller.get_version()
            hw_id = self.interface.hvcontroller.get_hardware_id(raw_hex=True)
            if hw_id:
                if len(hw_id) > HW_ID_DATA_LENGTH:
                    hw_id = base58.b58encode(bytes.fromhex(hw_id[:HW_ID_DATA_LENGTH])).decode('utf-8')
                device_id = hw_id
            else:
                device_id = 'N/A'
            self.hvDeviceInfoReceived.emit(fw_version, device_id)
            logger.info(f"Console - Firmware Version: {fw_version}, HWID: {device_id}")
        except LIFUError as e:
            self._handle_lifu_error("Console Info", e)
        except Exception as e:
            self._handle_lifu_error("Console Info", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot()
    def queryTxInfo(self):
        """Fetch and emit device information for all TX modules as a list."""
        self._interface_mutex.lock()
        try:
            module_count = self.interface.txdevice.get_module_count()
            modules_info = []
            for module_idx in range(module_count):
                try:
                    fw_version = self.interface.txdevice.get_version(module=module_idx)
                except LIFUError as e:
                    logger.warning(f"Module {module_idx}: failed to read firmware version: {e}")
                    fw_version = "N/A"
                try:
                    hw_id = self.interface.txdevice.get_hardware_id(module=module_idx, raw_hex=True)
                except LIFUError as e:
                    logger.warning(f"Module {module_idx}: failed to read hardware id: {e}")
                    hw_id = ""
                if hw_id:
                    if len(hw_id) > HW_ID_DATA_LENGTH:
                        hw_id = base58.b58encode(bytes.fromhex(hw_id[:HW_ID_DATA_LENGTH])).decode('utf-8')
                    device_id = hw_id
                else:
                    device_id = 'N/A'
                logger.info(f"Module {module_idx} - Firmware Version: {fw_version}, HWID: {device_id}")
                modules_info.append({
                    "module": module_idx,
                    "firmwareVersion": fw_version,
                    "deviceId": device_id
                })
            self.txDeviceInfoReceived.emit(modules_info)
        except LIFUError as e:
            self._handle_lifu_error("TX Info", e)
        except Exception as e:
            self._handle_lifu_error("TX Info", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtProperty(int, notify=numModulesUpdated)
    def queryNumModulesConnected(self):
        """Fetch and emit number of connected TX modules."""
        return self._num_modules_connected

    @pyqtSlot()
    def queryHvTemperature(self):
        """Fetch and emit temperature data."""
        self._interface_mutex.lock()
        try:
            temp1 = self.interface.hvcontroller.get_temperature1()
            temp2 = self.interface.hvcontroller.get_temperature2()
            self.temperatureHvUpdated.emit(temp1, temp2)
            logger.debug(f"Temperature Data - Temp1: {temp1}, Temp2: {temp2}")
        except LIFUError as e:
            # Avoid popups for periodic polling; log only.
            logger.warning(f"Failed to read console temperatures: {e}")
        except Exception as e:
            logger.error(f"Error querying temperature data: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryTxTemperature(self, warn_after_consecutive_failures=3):
        """Fetch and emit temperature data for all connected modules."""
        if self._num_modules_connected <= 0:
            return
        self._interface_mutex.lock()
        try:
            for module in range(0, self._num_modules_connected):
                try:
                    tx_temp = self.interface.txdevice.get_temperature(module=module)
                    amb_temp = self.interface.txdevice.get_ambient_temperature(module=module)
                    self._temp_poll_failures = 0  # reset on success
                except LIFUError as e:
                    self._temp_poll_failures += 1
                    if self._temp_poll_failures >= warn_after_consecutive_failures:
                        logger.warning(f"Module {module}: failed to read temperature {self._temp_poll_failures} consecutive times: {e}")
                    continue
                self.temperatureTxUpdated.emit(module, tx_temp, amb_temp)
                logger.debug(f"Module: {module} Temperature Data - Temp1: {tx_temp}, Temp2: {amb_temp}")
            self._tx_poll_failures = 0  # at least one module succeeded; reset counter
            try:
                is_running = self.interface.is_running()
                logger.debug(f"Running state during temperature update: {is_running}")
                if not is_running and self.interface.status == LIFUInterfaceStatus.STATUS_RUNNING:
                    # The sequence has completed on the hardware but we
                    # haven't updated our state yet. Mirror stop_sonication's
                    # HV policy: AUTO holds the rail on while still configured;
                    # only WHILE_RUNNING drops it at sonication end.
                    turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
                    self.interface.stop_sonication(turn_hv_off=turn_hv_off)
                    self._state = READY
                    self.stateChanged.emit(self._state)
            except LIFUError as e:
                logger.warning(f"Failed to query running state during temperature update: {e}")
                # LIFU-1001 = device not connected; count as a poll failure so
                # the poll loop can trigger an auto-disconnect.
                if "LIFU-1001" in str(e):
                    self._tx_poll_failures += 1
                    return  # skip further TX work this cycle
        except Exception as e:
            logger.error(f"Error querying Module temperature data: {e}")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot()
    def queryNumModules(self):
        """Fetch and emit number of connected TX modules."""
        self._interface_mutex.lock()
        try:
            count = self.interface.txdevice.get_tx_module_count()
            prev_count = self._num_modules_connected
            self._num_modules_connected = count
            self.numModulesUpdated.emit()
            logger.debug(f"Number of connected TX modules: {self._num_modules_connected}")
            # Refresh the cached per-module ``user_config`` whenever
            # the connected count changes (or when we go from 0 to N).
            # Best-effort: failures are logged at warning and the
            # cache is simply left empty for that module.
            if count != prev_count:
                self._refresh_module_user_configs_locked(count)
        except LIFUError as e:
            self._handle_lifu_error("TX Modules", e)
        except Exception as e:
            self._handle_lifu_error("TX Modules", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    def _refresh_module_user_configs_locked(self, count):
        """Read ``user_config`` from each connected module.

        Caller is responsible for holding ``_interface_mutex``.
        """
        self._module_user_configs = {}
        if count <= 0:
            self.presetScalingChanged.emit()
            return
        for module_idx in range(count):
            try:
                config = self.interface.txdevice.read_config(module=module_idx)
                cfg_dict = json.loads(config.get_json_str())
            except (LIFUError, ValueError, OSError) as e:
                logger.warning(
                    "Module %d: failed to read user_config: %s", module_idx, e,
                )
                continue
            self._module_user_configs[module_idx] = cfg_dict
            sn = cfg_dict.get("sn", "?")
            mod = cfg_dict.get("module", {}) or {}
            sens = mod.get("sensitivity") or []
            logger.info(
                "Module %d user_config: sn=%s hw=%s fw=%s freq=%skHz "
                "module_id=%s sensitivity_pts=%d",
                module_idx, sn, cfg_dict.get("hw_ver", "?"),
                cfg_dict.get("fw_ver", "?"), cfg_dict.get("freq", "?"),
                mod.get("id", "?"), len(sens),
            )
        self.presetScalingChanged.emit()


    @pyqtSlot(int)
    def setRGBState(self, state):
        """Set the RGB state using integer values."""
        self._interface_mutex.lock()
        try:
            valid_states = [0, 1, 2, 3]
            if state not in valid_states:
                self._emit_device_error("Set RGB State", f"Invalid RGB state value: {state}")
                return
            self.interface.hvcontroller.set_rgb_led(state)
            logger.info(f"RGB state set to: {state}")
        except LIFUError as e:
            self._handle_lifu_error("Set RGB State", e)
            # Re-query so the UI snaps back to the hardware's real state.
            self.queryRGBState()
        except Exception as e:
            self._handle_lifu_error("Set RGB State", e, context="Unexpected error")
            self.queryRGBState()
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryRGBState(self):
        """Fetch and emit RGB state."""
        self._interface_mutex.lock()
        try:
            state = self.interface.hvcontroller.get_rgb_led()
            state_text = {0: "Off", 1: "Red", 2: "Green", 3: "Blue"}.get(state, "Unknown")
            logger.info(f"RGB State: {state_text}")
            self.rgbStateReceived.emit(state, state_text)
        except LIFUError as e:
            self._handle_lifu_error("RGB State", e)
        except Exception as e:
            self._handle_lifu_error("RGB State", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryPowerStatus(self):
        """Fetch and emit HV state."""
        self._interface_mutex.lock()
        try:
            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
            self._hv_poll_failures = 0  # reset on success
        except LIFUError as e:
            # Don't emit a popup on poll failures – log only and let the
            # poll loop detect consecutive failures and trigger disconnect.
            self._hv_poll_failures += 1
            logger.warning(f"Power Status poll failure ({self._hv_poll_failures}): {e}")
        except Exception as e:
            self._handle_lifu_error("Power Status", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    def _set_async_mode(self, enable: bool, reason: str = "") -> bool:
        """Enable/disable the TX device's unsolicited STATUS stream.

        This is the only place that should call ``txdevice.async_mode()``.
        It serializes the toggle through the interface mutex, verifies the
        device echoed the requested state, and tracks the result on
        ``self._async_mode_enabled``. It is a no-op (and returns the
        cached state) when the TX device is not connected.

        Args:
            enable: target state.
            reason: short tag for the log line, for traceability.

        Returns:
            True if the device confirmed the requested state, False
            otherwise. Communication failures are logged but not raised
            so callers can use this in finally blocks without masking
            the original exception.
        """
        if not self._txConnected:
            self._async_mode_enabled = False
            return not enable
        if self._async_mode_enabled == enable:
            return True
        self._interface_mutex.lock()
        try:
            reported = self.interface.txdevice.async_mode(enable)
            if reported == enable:
                self._async_mode_enabled = enable
                tag = f" ({reason})" if reason else ""
                logger.debug(f"Async mode -> {enable}{tag}")
                return True
            logger.warning(
                f"TX device did not accept async mode {enable} "
                f"(reported {reported}); reason={reason}"
            )
            self._async_mode_enabled = bool(reported)
            return False
        except LIFUError as e:
            logger.warning(f"Async mode toggle failed ({reason}): {e}")
            return False
        except Exception as e:
            logger.warning(f"Async mode toggle failed ({reason}): {e}")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(bool)
    def pauseMonitoring(self, pause: bool):
        """Pause or resume background telemetry polling.

        Call with ``True`` while the diagnostics tab is active so that the
        poll thread does not race hardware calls made by the diagnostic tests.
        Call with ``False`` when leaving the diagnostics tab to resume normal
        telemetry.
        """
        self._monitoring_paused = pause
        logger.info("Telemetry polling %s", "PAUSED" if pause else "RESUMED")

    @pyqtSlot(bool)
    def setAsyncMode(self, enable: bool):
        """QML/diagnostic slot to manually toggle the TX async stream.

        Routes through :meth:`_set_async_mode` so the connector's tracked
        state stays consistent with the device.
        """
        if not self._set_async_mode(enable, reason="manual"):
            self._emit_device_error(
                "Async Mode",
                f"Device did not accept async mode {enable}.",
            )

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendPingCommand(self, target: str, index: int = 0):
        """Send a ping command to HV device."""
        self._interface_mutex.lock()
        try:
            if target == "HV":
                self.interface.hvcontroller.ping()
                logger.info("HV ping command sent successfully")
                return True
            elif target == "TX":
                self.interface.txdevice.ping(module=index)
                logger.info(f"TX module {index} ping command sent successfully")
                return True
            else:
                self._emit_device_error("Ping", f"Invalid target for ping command: {target}")
                return False
        except LIFUError as e:
            label = "HV" if target == "HV" else f"TX module {index}"
            self._handle_lifu_error("Ping", e, context=f"{label} ping failed")
            return False
        except Exception as e:
            self._handle_lifu_error("Ping", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendLedToggleCommand(self, target: str, index: int = 0):
        """Send a LED Toggle command to device."""
        self._interface_mutex.lock()
        try:
            if target == "HV":
                self.interface.hvcontroller.toggle_led()
                logger.info("HV LED toggle command sent successfully")
                return True
            elif target == "TX":
                self.interface.txdevice.toggle_led(module=index)
                logger.info(f"TX module {index} LED toggle command sent successfully")
                return True
            else:
                self._emit_device_error("LED Toggle", f"Invalid target for toggle command: {target}")
                return False
        except LIFUError as e:
            label = "HV" if target == "HV" else f"TX module {index}"
            self._handle_lifu_error("LED Toggle", e, context=f"{label} LED toggle failed")
            return False
        except Exception as e:
            self._handle_lifu_error("LED Toggle", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendEchoCommand(self, target: str, index: int = 0):
        """Send Echo command to device."""
        self._interface_mutex.lock()
        try:
            expected_data = b"Hello FROM Test Application!"
            if target == "HV":
                echoed_data, data_len = self.interface.hvcontroller.echo(echo_data=expected_data)
            elif target == "TX":
                echoed_data, data_len = self.interface.txdevice.echo(echo_data=expected_data, module=index)
            else:
                self._emit_device_error("Echo", f"Invalid target for echo command: {target}")
                return False

            if echoed_data == expected_data and data_len == len(expected_data):
                logger.info("Echo command successful - Data matched")
                return True
            self._emit_device_error("Echo", "Echo command failed - data mismatch.")
            return False
        except LIFUError as e:
            label = "HV" if target == "HV" else f"TX module {index}"
            self._handle_lifu_error("Echo", e, context=f"{label} echo failed")
            return False
        except Exception as e:
            self._handle_lifu_error("Echo", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    def setHVCommand(self, strval: str):
        """Set High voltage command to device."""
        self._interface_mutex.lock()
        try:
            voltage = float(strval)
            self.interface.hvcontroller.set_voltage(voltage=voltage)
            logger.info("Voltage set successfully")
            return True
        except ValueError as e:
            self._emit_device_error("Set HV Voltage", f"Invalid voltage value '{strval}': {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set HV Voltage", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set HV Voltage", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int, int, result=bool)
    def setFanLevel(self, fid: int, speed: int):
        """Set Fan Level to device."""
        self._interface_mutex.lock()
        try:
            result = self.interface.hvcontroller.set_fan_speed(fan_id=fid, fan_speed=speed)
            if result == speed:
                logger.info("Fan set successfully")
                return True
            self._emit_device_error(
                "Set Fan Speed",
                f"Fan {fid} did not accept speed {speed}% (reported {result})."
            )
            return False
        except ValueError as e:
            self._emit_device_error("Set Fan Speed", f"Invalid fan parameters: {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Fan Speed", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set Fan Speed", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()
    
    @pyqtSlot(str, result=bool)
    def setTrigger(self, triggerjson: str):
        """Set trigger settings on the device using JSON data."""
        self._interface_mutex.lock()
        try:
            json_trigger_data = json.loads(triggerjson)
            trigger_setting = self.interface.txdevice.set_trigger_json(data=json_trigger_data)
            self._update_trigger_state(trigger_setting)
            logger.info(f"Trigger Setting: {trigger_setting}")
            return True
        except json.JSONDecodeError as e:
            self._emit_device_error("Set Trigger", f"Failed to parse trigger JSON: {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Trigger", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set Trigger", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(result=bool)
    def toggleTrigger(self):
        """Toggle the trigger state (start or stop)."""
        self._interface_mutex.lock()
        previous_state = self._trigger_state
        try:
            if self._trigger_state:
                # Stop the trigger
                self.interface.txdevice.async_mode(False)
                self.interface.txdevice.stop_trigger()
                logger.info("Trigger stopped successfully.")
                self._trigger_state = False
            else:
                # Start the trigger
                self.interface.txdevice.async_mode(True)
                try:
                    self.interface.txdevice.start_trigger()
                except LIFUError:
                    # Revert to stopped on failure and make sure async mode is off.
                    try:
                        self.interface.txdevice.async_mode(False)
                    except Exception:
                        pass
                    raise
                logger.info("Trigger started successfully.")
                self._trigger_state = True

            self.triggerStateChanged.emit(self._trigger_state)
            return True
        except LIFUError as e:
            self._trigger_state = previous_state
            self.triggerStateChanged.emit(self._trigger_state)
            self._handle_lifu_error("Toggle Trigger", e)
            return False
        except Exception as e:
            self._trigger_state = previous_state
            self.triggerStateChanged.emit(self._trigger_state)
            self._handle_lifu_error("Toggle Trigger", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(result=bool)
    def queryTriggerInfo(self):
        """Query the trigger status and update the state accordingly.

        Returns:
            bool: True if the query was successful, False otherwise.
        """
        self._interface_mutex.lock()
        try:
            trigger_data = self.interface.txdevice.get_trigger_json()
            if isinstance(trigger_data, str):
                trigger_data = json.loads(trigger_data)
            self._update_trigger_state(trigger_data)
            return True
        except json.JSONDecodeError:
            self._emit_device_error("Trigger Status", "Failed to decode trigger status JSON.")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Trigger Status", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Trigger Status", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()
        
    @pyqtSlot()
    def softResetHV(self):
        """reset hardware HV device."""
        self._interface_mutex.lock()
        try:
            self.interface.hvcontroller.soft_reset()
            logger.info("Software Reset Sent")
        except LIFUError as e:
            self._handle_lifu_error("Soft Reset HV", e)
        except Exception as e:
            self._handle_lifu_error("Soft Reset HV", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def toggleHV(self):
        """Toggle HV on console."""
        self._interface_mutex.lock()
        try:
            # Check the current state of HV
            current_hv = self.interface.hvcontroller.get_hv_status()

            if current_hv:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off successfully")
            else:
                self.interface.hvcontroller.turn_hv_on()
                logger.info("HV turned on successfully")

            # Re-query the hardware and emit the real state.
            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except LIFUError as e:
            self._handle_lifu_error("Toggle HV", e)
            # Refresh UI with whatever the hardware currently reports.
            self._refresh_power_status_silent()
        except Exception as e:
            self._handle_lifu_error("Toggle HV", e, context="Unexpected error")
            self._refresh_power_status_silent()
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def turnOffHV(self):
        """Turn HV off on console (no-op if already off)."""
        self._interface_mutex.lock()
        try:
            current_hv = self.interface.hvcontroller.get_hv_status()
            if current_hv:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off successfully")

            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.debug(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except LIFUError as e:
            self._handle_lifu_error("Turn Off HV", e)
            self._refresh_power_status_silent()
        except Exception as e:
            self._handle_lifu_error("Turn Off HV", e, context="Unexpected error")
            self._refresh_power_status_silent()
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def toggleV12(self):
        """Toggle V12 on console."""
        self._interface_mutex.lock()
        try:
            current_v12 = self.interface.hvcontroller.get_12v_status()

            if current_v12:
                self.interface.hvcontroller.turn_12v_off()
                logger.info("V12 turned off successfully")
            else:
                self.interface.hvcontroller.turn_12v_on()
                logger.info("V12 turned on successfully")

            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except LIFUError as e:
            self._handle_lifu_error("Toggle 12V", e)
            self._refresh_power_status_silent()
        except Exception as e:
            self._handle_lifu_error("Toggle 12V", e, context="Unexpected error")
            self._refresh_power_status_silent()
        finally:
            self._interface_mutex.unlock()

    def _refresh_power_status_silent(self):
        """Best-effort re-query of HV/12V state to keep the UI in sync after a failure.

        Any further errors are logged but not surfaced as popups so that a
        single operation produces at most one popup.
        """
        try:
            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except Exception as e:
            logger.warning(f"Could not refresh power status after failure: {e}")

    @pyqtSlot()
    def getMonitorVoltages(self):
        """Get voltage monitor readings from console."""
        self._interface_mutex.lock()
        try:
            voltages = self.interface.hvcontroller.get_vmon_values()
            logger.debug(f"Voltage readings: {voltages}")
            self.monVoltagesReceived.emit(voltages)
        except LIFUError as e:
            # Do not spam popups on periodic polling; log only.
            logger.warning(f"Failed to read voltage monitor values: {e}")
        except Exception as e:
            logger.error(f"Error getting voltages: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def softResetTX(self):
        """reset hardware TX device."""
        self._interface_mutex.lock()
        try:
            self.interface.txdevice.soft_reset()
            logger.info("Software Reset Sent")
        except LIFUError as e:
            self._handle_lifu_error("Soft Reset TX", e)
        except Exception as e:
            self._handle_lifu_error("Soft Reset TX", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int)
    def softResetTXModule(self, module: int):
        """Soft reset a specific TX module by index."""
        self._interface_mutex.lock()
        try:
            self.interface.txdevice.soft_reset(module=module)
            logger.info(f"Software Reset Sent to module {module}")
        except LIFUError as e:
            self._handle_lifu_error("Soft Reset TX Module", e,
                                    context=f"Module {module}")
        except Exception as e:
            self._handle_lifu_error("Soft Reset TX Module", e,
                                    context=f"Module {module} unexpected error")
        finally:
            self._interface_mutex.unlock()

        
    @pyqtProperty(str, constant=True)
    def sdkVersion(self) -> str:
        """Expose SDK version as a constant QML property."""
        try:
            # Attempt to get SDK version from LIFUInterface
            return LIFUInterface.get_sdk_version()
        except Exception:
            # Fallback to default version if the function doesn't exist or package metadata is missing
            return "0.3.2"

    # ------------------------------------------------------------------
    # Firmware update
    # ------------------------------------------------------------------

    @pyqtSlot(str, result=str)
    def getDefaultFirmwarePath(self, device_type: str) -> str:
        """Return the bundled firmware file path for the given device type (console or transmitter)."""
        try:
            import importlib.util
            spec = importlib.util.find_spec("openlifu_sdk")
            if spec is None or spec.origin is None:
                return ""
            fw_dir = os.path.join(os.path.dirname(spec.origin), "firmware")
            names = {
                "console": "openlifu-console-fw.signed.bin",
                "transmitter": "openlifu-transmitter-fw.signed.bin",
            }
            name = names.get(device_type, "")
            return os.path.join(fw_dir, name) if name else ""
        except Exception as e:
            logger.error(f"Error locating default firmware for {device_type}: {e}")
            return ""

    @pyqtSlot(result=str)
    def readHvFirmwareVersion(self) -> str:
        """Read and return the current console (HV) firmware version."""
        self._interface_mutex.lock()
        try:
            version = self.interface.hvcontroller.get_version()
            self.fwVersionRead.emit("console", version)
            logger.info(f"Console firmware version: {version}")
            return version
        except LIFUError as e:
            self._handle_lifu_error("Firmware Version", e,
                                    context="Failed to read console firmware version")
            self.fwVersionRead.emit("console", "Error")
            return "Error"
        except Exception as e:
            self._handle_lifu_error("Firmware Version", e, context="Unexpected error")
            self.fwVersionRead.emit("console", "Error")
            return "Error"
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int, result=str)
    def readTxFirmwareVersion(self, module: int) -> str:
        """Read and return the current transmitter firmware version for a given module."""
        self._interface_mutex.lock()
        try:
            version = self.interface.txdevice.get_version(module=module)
            self.fwVersionRead.emit(f"transmitter_{module}", version)
            logger.info(f"Transmitter module {module} firmware version: {version}")
            return version
        except LIFUError as e:
            self._handle_lifu_error("Firmware Version", e,
                                    context=f"Failed to read transmitter module {module} firmware version")
            self.fwVersionRead.emit(f"transmitter_{module}", "Error")
            return "Error"
        except Exception as e:
            self._handle_lifu_error("Firmware Version", e,
                                    context=f"Module {module} unexpected error")
            self.fwVersionRead.emit(f"transmitter_{module}", "Error")
            return "Error"
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str)
    def updateConsoleFirmware(self, firmware_path: str) -> None:
        """Update the console (HV) firmware using DFU.  Runs in a background thread."""
        def _run():
            try:
                from openlifu_sdk.io.LIFUDFU import LIFUDFUManager

                def _progress(written: int, total: int, label: str) -> None:
                    self.fwUpdateProgress.emit(label, written, total)

                self.fwUpdateStatus.emit("console", False, "Starting console firmware update…")
                logger.info(f"Console firmware update: {firmware_path}")
                mgr = LIFUDFUManager(uart=self.interface.hvcontroller.uart)
                mgr.update_module(
                    module=0,
                    package_file=firmware_path,
                    enter_dfu_fn=self.interface.hvcontroller.enter_dfu,
                    vid=0x0483,
                    pid=0xDF11,
                    libusb_dll=None,
                    dfu_wait_s=5.0,
                    device_type="console",
                    progress_callback=_progress,
                )
                self.fwUpdateStatus.emit("console", True, "Console firmware update complete.")
                logger.info("Console firmware update complete.")
            except Exception as e:
                msg = f"Console update failed: {e}"
                logger.error(msg)
                self.fwUpdateStatus.emit("console", False, msg)

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, int)
    def updateTransmitterFirmware(self, firmware_path: str, module: int) -> None:
        """Update the transmitter firmware for a specific module. Runs in a background thread."""
        def _run():
            self._interface_mutex.lock()
            try:
                def _progress(written: int, total: int, label: str) -> None:
                    self.fwUpdateProgress.emit(label, written, total)

                self.fwUpdateStatus.emit("transmitter", False, f"Starting transmitter firmware update for module {module}…")
                logger.info(f"Transmitter module {module} firmware update: {firmware_path}")
                self.interface.txdevice.update_firmware(
                    module=module,
                    package_file=firmware_path,
                    vid=0x0483,
                    pid=0xDF11,
                    libusb_dll=None,
                    dfu_wait_s=5.0,
                    device_type="transmitter",
                    progress_callback=_progress,
                )
                self.fwUpdateStatus.emit("transmitter", True, f"Transmitter module {module} firmware update complete.")
                logger.info(f"Transmitter module {module} firmware update complete.")
            except Exception as e:
                msg = f"Transmitter module {module} update failed: {e}"
                logger.error(msg)
                self.fwUpdateStatus.emit("transmitter", False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str)
    def readUserConfig(self, target: str) -> None:
        """Read user configuration from the target device. Emits userConfigRead on success.

        target: "console" (reserved, not yet supported) or "tx_N" / "tx N" (module N).
        """
        def _run():
            self._interface_mutex.lock()
            try:
                module = _parse_tx_module(target)
                if module is None:
                    # Console not yet supported
                    self.userConfigStatus.emit(target, False, f"Unsupported target: {target}")
                    return

                config = self.interface.txdevice.read_config(module=module)
                json_str = config.get_json_str()
                logger.info(f"User config read from {target}: {json_str}")
                # Refresh the cached copy so subsequent solution
                # generation uses the freshly-read sensitivity table.
                try:
                    self._module_user_configs[module] = json.loads(json_str)
                    self.presetScalingChanged.emit()
                except ValueError:
                    pass
                self.userConfigRead.emit(target, json_str)
            except LIFUError as e:
                msg = f"{str(e)}"
                logger.error(f"Error reading config from {target}: {msg}")
                self.userConfigStatus.emit(target, False, msg)
            except Exception as e:
                msg = f"Error reading config from {target}: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, str)
    def writeUserConfig(self, target: str, json_str: str) -> None:
        """Write user configuration JSON to the target device.

        target: "console" (reserved, not yet supported) or "tx_N" / "tx N" (module N).
        """
        def _run():
            self._interface_mutex.lock()
            try:
                module = _parse_tx_module(target)
                if module is None:
                    self.userConfigStatus.emit(target, False, f"Unsupported target: {target}")
                    return

                updated = self.interface.txdevice.write_config_json(json_str, module=module)
                msg = f"Config written to {target}. Seq: {updated.header.seq}, CRC: 0x{updated.header.crc:04X}"
                logger.info(msg)
                self.userConfigStatus.emit(target, True, msg)
            except json.JSONDecodeError as e:
                msg = f"Invalid JSON: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            except LIFUError as e:
                msg = f"{str(e)}"
                logger.error(f"Error writing config to {target}: {msg}")
                self.userConfigStatus.emit(target, False, msg)
            except Exception as e:
                msg = f"Error writing config to {target}: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Solution loading functionality
    # ------------------------------------------------------------------

    @pyqtSlot(result='QVariantList')
    def getPresetSolutions(self):
        """Return preset solutions found in preset_solutions/*.json.

        Each item contains {"name": <solution name>, "path": <absolute file path>}.
        Invalid JSON files are skipped.
        """
        try:
            self._ensure_preset_solutions_seeded()
            preset_dir = self._get_runtime_preset_solutions_path()
            pattern = os.path.join(preset_dir, "*.json")
            preset_files = sorted(glob.glob(pattern))

            presets = []
            for file_path in preset_files:
                if os.path.basename(file_path).lower() == "default_solution.json":
                    continue
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        solution_data = json.load(f)

                    if not isinstance(solution_data, dict):
                        logger.warning(f"Skipping preset with invalid JSON root type: {file_path}")
                        continue

                    display_name = solution_data.get('name') or os.path.splitext(os.path.basename(file_path))[0]
                    presets.append({
                        "name": str(display_name),
                        "path": os.path.normpath(file_path)
                    })
                except Exception as e:
                    logger.warning(f"Skipping unreadable preset file {file_path}: {e}")

            presets.sort(key=lambda item: item.get("name", "").lower())
            logger.info(f"Discovered {len(presets)} preset solution(s)")
            return presets
        except Exception as e:
            logger.error(f"Error indexing preset solutions: {e}")
            return []

    @pyqtSlot(str, result=bool)
    def loadPresetSolution(self, file_path):
        """Load a preset solution by file path.

        This is a convenience wrapper that uses the same logic as loading any solution file.
        """
        return self.loadSolutionFromFile(file_path)
    
    @pyqtSlot(str, result=bool)
    def loadSolutionFromFile(self, file_path):
        """Load a solution from a JSON file and apply it to the UI controls.
        
        Args:
            file_path: The path to the solution JSON file
            
        Returns:
            bool: True if loading was successful, False otherwise
        """
        try:
            logger.info(f"Attempting to load solution from: {file_path}")
            
            # Normalize the path for the current OS
            normalized_path = os.path.normpath(file_path)
            logger.info(f"Normalized path: {normalized_path}")
            
            # Validate file exists and is readable
            if not os.path.exists(normalized_path):
                error_msg = f"File not found: {normalized_path}"
                logger.error(error_msg)
                self.solutionLoadError.emit(error_msg)
                return False
                
            if not os.path.isfile(normalized_path):
                error_msg = f"Path is not a file: {normalized_path}"
                logger.error(error_msg)
                self.solutionLoadError.emit(error_msg)
                return False
                
            with open(normalized_path, 'r', encoding='utf-8') as f:
                solution_data = json.load(f)
                
            logger.info(f"Successfully parsed JSON from {normalized_path}")
            logger.info(f"JSON data type: {type(solution_data)}")
            if isinstance(solution_data, dict):
                logger.info(f"JSON keys: {list(solution_data.keys())}")
            else:
                logger.warning(f"Unexpected JSON data type: {type(solution_data)}, value: {str(solution_data)[:100]}")
            
            # Validate solution structure
            if not self._validate_solution_format(solution_data):
                return False
                
            # If transducer is connected, verify element count matches modules
            if self._txConnected:
                self.queryNumModules()  # Update module count
                expected_elements = self._num_modules_connected * NUM_ELEMENTS_PER_MODULE
                actual_elements = len(solution_data.get('transducer', {}).get('elements', []))
                
                if expected_elements != actual_elements:
                    error_message = f"Element count mismatch!\nExpected: {expected_elements} elements ({self._num_modules_connected} modules × {NUM_ELEMENTS_PER_MODULE})\nFound in solution: {actual_elements} elements"
                    self.solutionLoadError.emit(error_message)
                    return False
            
            # Store loaded solution data
            self._loaded_solution_data = solution_data
            self._solution_loaded = True
            self._solution_name = solution_data.get('name', 'Unnamed Solution')

            # A freshly loaded solution must be re-Configured before it can
            # run; drop the configured flag and refresh the UI state.
            self._configured = False
            self.update_state()

            # Emit success signal with solution details
            if "name" in solution_data:
                message = f"Loaded solution '{solution_data['name']}' from file"
            else:
                message = f"Loaded solution with {len(solution_data.get('transducer', {}).get('elements', []))} elements"
            logger.info(message)
            self.solutionFileLoaded.emit(self._solution_name, message)
            self.solutionStateChanged.emit()
            
            logger.info(f"Successfully loaded solution: {self._solution_name}")
            return True
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON format: {str(e)}"
            logger.error(error_msg)
            self.solutionLoadError.emit(error_msg)
            return False
        except PermissionError as e:
            error_msg = f"Permission denied accessing file: {str(e)}"
            logger.error(error_msg)
            self.solutionLoadError.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Error loading solution: {str(e)}"
            logger.error(f"Error loading solution from {file_path}: {e}")
            self.solutionLoadError.emit(error_msg)
            return False
    
    def _validate_solution_format(self, solution_data):
        """Validate that the solution file has the required structure.
        
        Args:
            solution_data: The parsed JSON solution data
            
        Returns:
            bool: True if valid, False otherwise
        """
        try:
            # First check if solution_data is actually a dict
            if not isinstance(solution_data, dict):
                self.solutionLoadError.emit(f"Invalid solution format: expected JSON object, got {type(solution_data).__name__}")
                return False
            
            logger.info(f"Validating solution with keys: {list(solution_data.keys())}")
            
            # Check for required top-level fields
            required_fields = ['transducer', 'pulse', 'sequence']
            for field in required_fields:
                if field not in solution_data:
                    self.solutionLoadError.emit(f"Missing required field: {field}")
                    return False
            
            # Validate transducer structure
            transducer = solution_data['transducer']
            if not isinstance(transducer, dict):
                self.solutionLoadError.emit("Transducer field must be an object")
                return False
                
            if 'elements' not in transducer:
                self.solutionLoadError.emit("Missing 'elements' in transducer data")
                return False
                
            if not isinstance(transducer['elements'], list):
                self.solutionLoadError.emit("Transducer elements must be a list")
                return False
                
            # Validate pulse structure
            pulse = solution_data['pulse']
            if not isinstance(pulse, dict):
                self.solutionLoadError.emit("Pulse field must be an object")
                return False
                
            pulse_fields = ['frequency', 'duration']
            for field in pulse_fields:
                if field not in pulse:
                    self.solutionLoadError.emit(f"Missing pulse field: {field}")
                    return False
            
            # Validate sequence structure
            sequence = solution_data['sequence']
            if not isinstance(sequence, dict):
                self.solutionLoadError.emit("Sequence field must be an object")
                return False
                
            sequence_fields = ['pulse_interval', 'pulse_count']
            for field in sequence_fields:
                if field not in sequence:
                    self.solutionLoadError.emit(f"Missing sequence field: {field}")
                    return False
                    
            logger.info("Solution validation passed")
            return True
            
        except Exception as e:
            error_msg = f"Error validating solution format: {str(e)}"
            logger.error(error_msg)
            self.solutionLoadError.emit(error_msg)
            return False
    
    @pyqtSlot(result='QVariantMap')
    def getLoadedSolutionSettings(self):
        """Get the loaded solution settings to populate UI controls.
        
        Returns:
            QVariantMap: Dictionary containing solution settings
        """
        if not self._solution_loaded or not self._loaded_solution_data:
            return {}
        
        try:
            return self._extract_solution_settings(self._loaded_solution_data)
            
        except Exception as e:
            logger.error(f"Error extracting solution settings: {e}")
            return {}
    
    @pyqtSlot()
    def makeLoadedSolutionEditable(self):
        """Release the loaded solution data while preserving UI field values."""
        if self._hv_enable_mode == HV_EN_ON and self._hvConnected:
            try:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off to allow editing")
            except LIFUError as hv_e:
                self._handle_lifu_error("Edit Solution", hv_e,
                                        context="Failed to turn off HV")
            except Exception as hv_e:
                self._handle_lifu_error("Edit Solution", hv_e,
                                        context="Unexpected error turning off HV")
        if self._solution_loaded:
            solution_name = self._solution_name
            self._solution_loaded = False
            self._loaded_solution_data = None
            self._solution_name = ""
            self.solutionStateChanged.emit()
            logger.info(f"Released solution '{solution_name}' - UI fields preserved, controls are now editable")
    
    @pyqtSlot(str, str)
    def loadTestReport(self, file_path, target):
        """Load and validate test report against specified TXM module"""
        def _run():
            try:
                # Parse the target to get module number
                module = _parse_tx_module(target)
                if module is None:
                    self.testReportLoaded.emit(False, f"Unsupported target: {target}")
                    return
                
                # Convert file URL to local path
                if file_path.startswith("file:///"):
                    file_path_clean = file_path[8:]  # Remove file:/// prefix
                elif file_path.startswith("file://"):
                    file_path_clean = file_path[7:]  # Remove file:// prefix
                else:
                    file_path_clean = file_path
                    
                logger.info(f"Loading test report from: {file_path_clean} for {target}")
                
                # Read the test report
                report_df = read_test_report(file_path_clean)
                config = test_report_to_config(report_df)
                
                # Extract report information
                report_sn = config.get('sn', 'Unknown')
                report_hwid = config.get('hwid', 'Unknown')
                report_freq = config.get('freq', 'Unknown')
                
                report_info = f"SN: {report_sn}, HWID: {report_hwid}, Freq: {report_freq} kHz"
                
                # Check if we have a connected TXM to compare against
                
                if self._txConnected:
                    self._interface_mutex.lock()
                    try:
                        # Check against specified module
                        check_result = check_config_against_device(self.interface, config, module=module)
                        if check_result is not False:  # None means warnings but valid, False means mismatch
                            # Convert config to JSON string and populate User Config editor
                            import json
                            json_str = json.dumps(config, indent=2)
                            self.userConfigRead.emit(target, json_str)
                            
                            message = f"Test report matches {target}! {report_info} - Config loaded into editor."
                            self.testReportLoaded.emit(True, message)
                        else:
                            message = f"Test report does NOT match {target}. Report: {report_info}"
                            self.testReportLoaded.emit(False, message)
                    except Exception as e:
                        logger.warning(f"Could not verify report against device: {e}")
                        message = f"Test report loaded but could not verify against {target}: {e}"
                        self.testReportLoaded.emit(False, message)
                    finally:
                        self._interface_mutex.unlock()
                else:
                    message = f"Test report loaded. No TXM connected for verification. {report_info}"
                    self.testReportLoaded.emit(False, message)
                    
            except Exception as e:
                error_msg = f"Failed to load test report: {str(e)}"
                logger.error(error_msg)
                self.testReportLoaded.emit(False, error_msg)
                
        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(int, int)
    def runThermalTest(self, frequency, num_modules):
        """Run the short-duration verification test."""
        logger.info(f"runThermalTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "short",
            lambda: TransmitterShortVerificationTest(args=args),
            "short-duration verification",
        )

    @pyqtSlot(int, int)
    def runLongVerificationTest(self, frequency, num_modules):
        """Run the full long verification sequence over PRODREQS cases."""
        logger.info(f"runLongVerificationTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "long",
            lambda: TransmitterHeatingPlaceholder(args=args),
            "long verification",
        )

    @pyqtSlot(int, int)
    def runIndefiniteTest(self, frequency, num_modules):
        """Run the indefinite-loop verification test."""
        logger.info(f"runIndefiniteTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "indefinite",
            lambda: TransmitterIndefiniteRun(args=args),
            "indefinite run verification",
        )

    @pyqtSlot(int, int)
    def runVoltageAccuracyTest(self, frequency, num_modules):
        """Run console voltage-accuracy test sequence."""
        logger.info(f"runVoltageAccuracyTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "voltage",
            lambda: VoltageAccuracyTest(args=args),
            "voltage accuracy verification",
        )

    def _build_verification_args(self, frequency, num_modules, test_case=None):
        args = parse_arguments()
        args.frequency = int(frequency)
        args.num_modules = int(num_modules)
        args.interface = self.interface
        if test_case is not None:
            args.test_case = int(test_case)
        return args

    def _start_verification_test(self, test_kind, factory, display_name):
        if self.running_thread is not None and self.running_thread.is_alive():
            logger.warning("Previous test still shutting down, ignoring start request")
            return

        self._abort_requested = False
        self._running = True
        self._active_test_kind = test_kind
        self.update_state()

        try:
            self.thermal_test_instance = factory()
        except Exception as e:
            self._running = False
            self._active_test_kind = ""
            self.update_state()
            logger.error(f"Failed to initialize {display_name}: {e}")
            return

        self._start_progress_timer()

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                logger.info(f"Running {display_name}...")

                if self._abort_requested and self.thermal_test_instance:
                    with contextlib.suppress(Exception):
                        self.thermal_test_instance.shutdown_event.set()
                    self.thermal_test_instance.test_status = "aborted by user"
                    logger.info(f"{display_name} aborted before execution started")
                    return

                self.thermal_test_instance.run()

            except Exception as e:
                logger.exception(f"\n !! Fatal error in {display_name} worker: {e}")
                with contextlib.suppress(Exception):
                    if self.thermal_test_instance:
                        self.thermal_test_instance.shutdown_event.set()
            finally:
                self._running = False
                self.update_state()
                loop.close()
                logger.info(f"Updated state: {self._state}")

        self.running_thread = threading.Thread(target=_run, daemon=True)
        self.running_thread.start()
        logger.info("Thread started, returning to event loop")

    @pyqtSlot()
    def _stop_thermal_test(self):
        self._abort_requested = True
        if self.thermal_test_instance:
            with contextlib.suppress(Exception):
                self.thermal_test_instance.shutdown_event.set()
            self.thermal_test_instance.test_status = "aborted by user"
            logger.info("Thermal test stop requested")
        else:
            logger.info("Thermal test stop requested before runner initialization")

    @pyqtSlot()
    def stopVerificationTest(self):
        self._stop_thermal_test()

    def _start_progress_timer(self):
        logger.info("Starting progress timer for thermal test")
        if hasattr(self, "_progress_timer") and self._progress_timer.isActive():
            self._progress_timer.stop()
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(250)
        self._progress_timer.timeout.connect(self._emit_test_progress)
        self._progress_timer.start()
        logger.info(f"Timer active: {self._progress_timer.isActive()}")

    def _stop_progress_timer(self):
        if hasattr(self, "_progress_timer"):
            self._progress_timer.stop()

    def _emit_test_progress(self):
        runner = self.thermal_test_instance
        if runner is None:
            self._stop_progress_timer()
            return

        worker_alive = hasattr(self, "running_thread") and self.running_thread is not None and self.running_thread.is_alive()

        test_kind = self._active_test_kind or "short"

        status = str(getattr(runner, "test_status", "not started") or "not started")
        status_lower = status.lower()
        sequence_duration = float(getattr(runner, "sequence_duration", 0) or 0)
        test_case_start_time = float(getattr(runner, "test_case_start_time", 0.0) or 0.0)
        start_time = float(getattr(runner, "start_time", 0.0) or 0.0)
        is_in_cooldown = bool(getattr(runner, "is_in_cooldown", False))
        log_file_path = str(getattr(runner, "log_file_path", "") or "")

        terminal_status = {"passed", "temperature shutdown", "voltage deviation", "error", "aborted by user"}

        if test_kind == "indefinite":
            current_case = int(getattr(runner, "test_case_num", getattr(runner, "test_case", 1)) or 1)

            if status_lower == "running" and sequence_duration > 0 and test_case_start_time > 0:
                elapsed_case = time.time() - test_case_start_time
                case_frac = min(elapsed_case / sequence_duration, 1.0)
            elif status_lower in terminal_status and test_case_start_time > 0:
                case_frac = 1.0
            else:
                case_frac = 0.0

            total_frac = case_frac
            total_label = "Overall - indefinite run"
            if is_in_cooldown:
                check_time = datetime.now() + timedelta(seconds=TIME_BETWEEN_TESTS_TEMPERATURE_CHECK_SECONDS)
                case_label = f"Cycle test status: cooldown, checking again at {check_time.strftime('%H:%M')}"
            else:
                case_label = f"Cycle test case {current_case}: {status}"
        else:
            if test_kind == "short":
                total_cases = 1
                starting_case = 1
            elif test_kind == "voltage":
                total_cases = len(TEST_VOLTAGES)
                starting_case = int(getattr(runner, "starting_test_case", 1) or 1)
            else:
                total_cases = len(TEST_CASES)
                starting_case = int(getattr(runner, "starting_test_case", 1) or 1)

            current_case = int(getattr(runner, "test_case_num", starting_case) or starting_case)
            actual_total_cases = max(total_cases - starting_case + 1, 1)

            if status_lower == "running" and sequence_duration > 0 and test_case_start_time > 0:
                elapsed_case = time.time() - test_case_start_time
                case_frac = min(elapsed_case / sequence_duration, 1.0)
            elif status_lower in terminal_status and test_case_start_time > 0:
                case_frac = 1.0
            else:
                case_frac = 0.0

            cases_completed = max(current_case - starting_case, 0)
            if test_kind == "voltage":
                if is_in_cooldown:
                    total_frac = min(cases_completed / actual_total_cases, 1.0)
                else:
                    total_frac = min((cases_completed + case_frac) / actual_total_cases, 1.0)

                if (not worker_alive) and status_lower in terminal_status and current_case >= (starting_case + actual_total_cases - 1):
                    total_frac = 1.0

                total_label = f"Overall - case {min(cases_completed + 1, actual_total_cases)}/{actual_total_cases}"
            else:
                if is_in_cooldown:
                    total_frac = min(cases_completed / actual_total_cases, 1.0)
                else:
                    total_frac = min((cases_completed + case_frac) / actual_total_cases, 1.0)

                total_label = f"Overall - case {min(cases_completed + 1, actual_total_cases)}/{actual_total_cases}"

            if is_in_cooldown:
                check_time = datetime.now() + timedelta(seconds=TIME_BETWEEN_TESTS_TEMPERATURE_CHECK_SECONDS)
                case_label = f"Test Status: cooldown, checking again at {check_time.strftime('%H:%M')}"
            else:
                case_label = f"Test Status: {status}"

        if is_in_cooldown:
            status_color = "#3498DB"       # blue for cooldown
        elif status_lower == "running":
            status_color = "#E2A84A"       # yellow
        elif status_lower == "passed":
            status_color = "#2ECC71"       # green
        elif status_lower in ("temperature shutdown", "voltage deviation", "error"):
            status_color = "#E74C3C"       # red
        elif status_lower == "aborted by user":
            status_color = "#F39C12"       # orange
        else:
            status_color = "#BDC3C7"       # grey/idle

        self.testProgressUpdated.emit(total_frac, case_frac, total_label, case_label, status_color, log_file_path)

        # Keep polling until the worker thread has fully exited run(), including its finally block.
        if not worker_alive:
            self._stop_progress_timer()
