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
# `openlifu-sdk>=` pin in pyproject.toml. The parse/check helpers live in
# the SDK (``openlifu_sdk.ui``); we just pin the version here.
MIN_SDK_VERSION = "1.0.7"

from openlifu_sdk.ui import (
    BaseConnector,
    TelemetryPollThread,
    check_sdk_version as _sdk_check_sdk_version,
    parse_sdk_version as _parse_sdk_version,
    parse_status_string as _sdk_parse_status_string,
)


def check_sdk_version(min_version: str = MIN_SDK_VERSION):
    """Thin wrapper around the SDK's :func:`check_sdk_version` that pins the
    default minimum to this app's ``MIN_SDK_VERSION``."""
    return _sdk_check_sdk_version(min_version)

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
# Application-level state, HV enable modes, thermal thresholds, physics
# constants, retry policy, and run-log format strings are defined in
# :mod:`lifu.lifu_constants` so they can also be imported by mixin
# modules without creating a circular dependency.
from lifu.lifu_constants import (
    DISCONNECTED,
    CONNECTED,
    READY,
    RUNNING,
    TEST_SCRIPT_READY,
    HV_EN_AUTO,
    HV_EN_ON,
    HV_EN_OFF,
    HV_EN_WHILE_RUNNING,
    HV_EN_MODES,
    THERMAL_COOLING_THRESHOLD_C,
    THERMAL_SHUTDOWN_THRESHOLD_C,
    SPEED_OF_SOUND,
    NUM_ELEMENTS_PER_MODULE,
    MAX_TIMEOUT_RETRIES,
    RUN_LOG_FORMAT,
    RUN_LOG_DATEFMT,
)


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


class _Bridge(QObject):
    """Thread-safe bridge from OWSignal to pyqtSignal."""
    sig_connected = pyqtSignal(str, str) # (descriptor, port)
    sig_disconnected = pyqtSignal(str, str) # (descriptor, port)
    sig_data = pyqtSignal(str, str) # (descriptor, data)
    sig_error = pyqtSignal(str, int, str)
    
from lifu.lifu_testing import TestingMixin
from lifu.lifu_settings import SettingsMixin
from lifu.lifu_console import ConsoleMixin
from lifu.lifu_transmitter import TransmitterMixin
from lifu.lifu_controller import ControllerMixin


class LIFUConnector(TestingMixin, SettingsMixin, ConsoleMixin, TransmitterMixin, ControllerMixin, BaseConnector):
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
        # main-thread event loop (and thus to QML). The thread itself is
        # provided by the SDK; per-tick behavior lives in our
        # ``poll_tx_tick`` / ``poll_hv_tick`` hook methods below.
        self._poll_thread = TelemetryPollThread(self, interval_s=1.0)
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

    # ------------------------------------------------------------------
    # TelemetryPollThread hooks (called from the SDK's poll thread).
    # The thread itself handles the sleep, monitoring-paused check, and
    # the "skip TX/HV polling while RUNNING" gate. These hooks just do
    # the per-tick work, including consecutive-failure recovery.
    # ------------------------------------------------------------------
    _HV_POLL_FAIL_LIMIT = 3
    _TX_POLL_FAIL_LIMIT = 3

    def poll_tx_tick(self):
        """Per-tick TX telemetry poll: module enumeration + temperature.

        Called by the SDK's ``TelemetryPollThread`` once per second while
        ``txConnected`` is True and ``state != RUNNING``.
        """
        if self._num_modules_connected <= 0:
            # Guard: don't poll until TX firmware has had time to finish
            # module enumeration (~2.5 s). Querying too early races the
            # init sequence and causes a timeout.
            elapsed = time.monotonic() - (self._tx_connect_time or 0.0)
            if elapsed >= 3.0:
                self.queryNumModules()
        # While sonicating, the firmware pushes unsolicited STATUS frames
        # with temperature; polling the same endpoint races those frames
        # and causes UART timeouts -- the thread's RUNNING gate prevents
        # that here.
        self.queryTxTemperature()
        if self._tx_poll_failures >= self._TX_POLL_FAIL_LIMIT:
            logger.warning(
                "TX: %d consecutive poll failures - closing interface and triggering disconnect",
                self._TX_POLL_FAIL_LIMIT,
            )
            self._tx_poll_failures = 0
            # Close the underlying TX port so the SDK actually drops the
            # connection; on_disconnected only updates flags/signals and
            # would leave the SDK in a still-connected (but failing)
            # state otherwise.
            try:
                self.interface.txdevice.close()
            except Exception as close_exc:
                logger.debug("TX close during failure recovery: %s", close_exc)
            self.on_disconnected("TX", "")

    def poll_hv_tick(self):
        """Per-tick HV telemetry poll: power status + VMONs.

        Called by the SDK's ``TelemetryPollThread`` once per second while
        ``hvConnected`` is True and ``state != RUNNING``.
        """
        # Re-check power status every cycle so AUTO-settle events are
        # reflected in the UI promptly.
        self.queryPowerStatus()
        if self._hv_poll_failures >= self._HV_POLL_FAIL_LIMIT:
            logger.warning(
                "HV: %d consecutive poll failures - closing interface and triggering disconnect",
                self._HV_POLL_FAIL_LIMIT,
            )
            self._hv_poll_failures = 0
            try:
                self.interface.hvcontroller.close()
            except Exception as close_exc:
                logger.debug("HV close during failure recovery: %s", close_exc)
            self.on_disconnected("HV", "")
            return
        self.getMonitorVoltages()

    @pyqtSlot(str, result=dict)
    def parse_status_string(self, status_str):
        """Parse a TX STATUS frame into a dict. Delegates to the SDK parser."""
        return _sdk_parse_status_string(status_str)

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
    
    @pyqtProperty(int, notify=numModulesUpdated)
    def queryNumModulesConnected(self):
        """Fetch and emit number of connected TX modules."""
        return self._num_modules_connected

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
    # Solution loading functionality
    # ------------------------------------------------------------------

