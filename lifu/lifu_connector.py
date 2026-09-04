from PyQt6.QtCore import QObject, QRecursiveMutex, QThread, QTimer, pyqtSignal, pyqtProperty, pyqtSlot
import asyncio
import contextlib
import csv
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
import time
import numpy as np
import json
import copy
from plot.plot import generate_ultrasound_plot_from_solution, profile_color_hex  # Import the function directly
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


# Set up logging.
#
# Root logging (timestamped format, default level, stderr handler) is
# configured once in ``main.py`` via ``logging.basicConfig`` so that
# every logger in the process -- our ``lifu.*`` modules AND the SDK's
# ``openlifu_sdk.*`` modules -- emits through a single, consistently
# formatted handler. We do NOT attach a separate StreamHandler to the
# ``lifu`` package logger here: doing so used to cause every lifu log
# line to print twice (once from the package handler, once from the
# root handler after propagation). Module loggers below just call
# ``logging.getLogger(__name__)`` and inherit the root handler via the
# normal propagation chain.
logger = logging.getLogger(__name__)


# Minimum required openlifu-sdk version. Bump this whenever the test app
# starts depending on a new SDK feature/fix. Keep in sync with the
# `openlifu-sdk>=` pin in pyproject.toml. The parse/check helpers live in
# the SDK (``openlifu_sdk.ui``); we just pin the version here.
MIN_SDK_VERSION = "2.0.16"

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
    SPEED_OF_SOUND,
    NUM_ELEMENTS_PER_MODULE,
    MAX_FOCUS_POINTS,
    MIN_PROFILE_SWITCH_INTERVAL_S,
    MAX_TIMEOUT_RETRIES,
    FW_COMPLIANCE_OK,
    FW_COMPLIANCE_UNKNOWN,
    FW_COMPLIANCE_UPDATE_AVAILABLE,
    FW_COMPLIANCE_UPDATE_REQUIRED,
    MIN_CONSOLE_FW_VERSION,
    MIN_TRANSMITTER_FW_VERSION,
    firmware_compliance,
    packaged_console_fw_version,
    packaged_transmitter_fw_version,
)


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
    connectionStatusChanged = pyqtSignal()  # ðŸ”¹ New signal for connection updates
    triggerStateChanged = pyqtSignal(bool)  # ðŸ”¹ New signal for trigger state change
    txConfigStateChanged = pyqtSignal(bool)  # ðŸ”¹ New signal for tx configured state change

    # Firmware update signals
    fwUpdateProgress = pyqtSignal(str, int, int)  # (label, written, total)
    fwUpdateStatus = pyqtSignal(str, bool, str)   # (device_type, success, message)
    fwVersionRead = pyqtSignal(str, str)           # (device_type, version)

    # (The firmware "check for updates" signal was retired with the GitHub
    # download flow — the SDK no longer polls or downloads firmware; both
    # devices flash the image bundled with the SDK, or a file the operator
    # selects manually.)

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

    # Emitted when the solution safety-limit bypass is turned on or off.
    safetyBypassChanged = pyqtSignal(bool)

    # Firmware-compliance signal. Emitted whenever the per-device
    # compliance buckets (consoleFirmwareCompliance,
    # transmitterFirmwareCompliance) change, so QML rebinds the System
    # Status text + button enable states.
    firmwareComplianceChanged = pyqtSignal()

    # Sonication progress (parsed from unsolicited TX STATUS frames). Only
    # emitted while async_mode is enabled and a sonication is in progress.
    # Raw counts come straight from the firmware's PULSE_TRAIN:[curr/total]
    # and PULSE:[curr/total] fields. Pulse counts are typically (0, 0) on
    # current firmware (PULSE field is reserved); QML should ignore them
    # when total is 0.
    sonicationProgressUpdated = pyqtSignal(int, int, int, int)  # (pt_curr, pt_total, p_curr, p_total)

    # Controller-page telemetry logging state (enabled, active log path).
    controllerTelemetryLoggingChanged = pyqtSignal(bool, str)

    # Generic device error signal for surfacing SDK failures to QML as popups.
    # Emitted whenever a LIFUError (or unexpected Exception) is caught while
    # talking to the hardware. The message already includes the [LIFU-<code>]
    # prefix for LIFUError subclasses.
    deviceError = pyqtSignal(str, str)  # (title, message)

    def _make_interface(self, hv_test_mode=False):
        """Construct the underlying LIFUInterface.

        Subclasses (e.g. ``SimulatedLIFUConnector``) override this to
        substitute a fake implementation without re-running the rest
        of ``__init__``.
        """
        return LIFUInterface(HV_test_mode=hv_test_mode,
                             run_async=True)

    def __init__(self, hv_test_mode=False):
        super().__init__()
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
        # Set True for the duration of a firmware update. A console update
        # power-cycles the TX modules, so they disconnect and re-enumerate;
        # any TX query caught in that window times out. Those failures are
        # EXPECTED, so they are logged but never surfaced as error popups
        # (the update has its own modal progress/status dialog).
        self._fw_update_active = False
        # Slave-module temperature polling while RUNNING (see poll_pre_tick).
        self._last_slave_temp_poll = 0.0   # monotonic timestamp of last slave poll
        self._next_slave_temp_module = 1   # round-robin cursor over modules 1..N-1

        # ----- Cached static device-info -----
        # Each ``queryXxxInfo`` / ``readXxxFirmwareVersion`` slot is
        # invoked by every QML page on its own ``Component.onCompleted``
        # (Console, Settings, Transmitter, Support all need the same
        # firmware/HWID strings to populate their headers). Without a
        # cache, every page triggers an independent UART round-trip and
        # logs the same line again. With these caches the first call
        # after connect hits the device and emits + logs; subsequent
        # calls just re-emit the cached values so QML signal handlers
        # still fire. Caches are cleared in ``on_disconnected`` (and on
        # firmware-update completion) so a fresh device or new firmware
        # is picked up the next time a page asks.
        self._cached_hv_info: tuple[str, str] | None = None         # (fw, hwid)
        self._cached_hv_fw_version: str | None = None
        self._cached_rgb_state: tuple[int, str] | None = None        # (code, label)
        self._cached_tx_info: list[dict] | None = None              # per-module dicts
        self._cached_tx_fw_version: dict[int, str] = {}             # module -> version

        # Solution loading state
        self._solution_loaded = False
        self._loaded_solution_data = None
        self._solution_name = ""

        # Optional controller-page telemetry CSV logging.
        # ``_controller_telemetry_logging_enabled`` is the user's armed
        # preference (checkbox state). A CSV file is opened only while
        # sonication is actively RUNNING.
        self._controller_telemetry_logging_enabled = False
        self._controller_telemetry_log_path = ""
        self._controller_telemetry_t0 = 0.0
        self._controller_telemetry_csv = None
        self._controller_telemetry_file_handle = None
        self._controller_telemetry_sample_timer = QTimer(self)
        self._controller_telemetry_sample_timer.setInterval(1000)
        self._controller_telemetry_sample_timer.timeout.connect(
            self._controller_telemetry_sample_tick
        )

        # HV enable mode: 0=AUTO (only while running), 1=ON, 2=OFF
        self._hv_enable_mode = HV_EN_AUTO

        # Engineering override: when True, Configure skips the SDK's
        # check_solution() safety pass (duty-cycle / voltage / sequence-
        # duration limits), allowing drive levels up to 100% duty cycle.
        # Deliberately in-memory only -- it must never survive a restart,
        # and it is cleared whenever the TX device disconnects. The HV
        # controller's hard 5-100 V rail clamp is unaffected.
        self._bypass_safety_checks = False

        # Per-device firmware compliance buckets (FW_COMPLIANCE_*).
        # ``_console_fw_compliance`` covers the HV/console; the TX dict
        # holds one bucket per known module index. Aggregate properties
        # (consoleFirmwareCompliance, transmitterFirmwareCompliance,
        # firmwareUpdateRequired, firmwareUpdateAvailable) derive from
        # these and gate the Configure / Write Config / Add Device
        # Configuration buttons in QML.
        self._console_fw_compliance = FW_COMPLIANCE_UNKNOWN
        self._tx_fw_compliance: dict[int, int] = {}

        # Tracks whether the TX device's unsolicited STATUS stream is
        # currently enabled. The firmware only emits STATUS frames during
        # active sonication; we keep async OFF while the host is issuing
        # write_block-heavy commands (set_solution, direct setters) so the
        # TX response packet is not delayed by an interleaved STATUS frame
        # on the same CDC IN endpoint -- which is the dominant cause of
        # UART timeouts during configuration.
        self._async_mode_enabled = False

        self._interface_mutex = QRecursiveMutex()

        self._ensure_preset_solutions_seeded()

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

        # Drive the firmware-compliance check from the connector itself,
        # not from individual UI pages, so the lockout/advisory state is
        # populated even when the operator never opens Settings/Console.
        # ``connectionStatusChanged`` fires after on_connected updates
        # ``_hvConnected``/``_txConnected``; ``numModulesUpdated`` fires
        # once the TX has finished enumerating its modules. Both queries
        # are cached, so re-emits from UI pages don't cause duplicate
        # UART round-trips.
        self.connectionStatusChanged.connect(self._check_firmware_on_connect)
        self.numModulesUpdated.connect(self._check_tx_firmware_on_modules)

        # Optional CSV capture of Controller telemetry. We subscribe at the
        # signal layer so all existing emit sites (poll thread and status-frame
        # parser) are covered without altering their call paths.
        self.temperatureHvUpdated.connect(self._log_controller_hv_temperature)
        self.temperatureTxUpdated.connect(self._log_controller_tx_temperature)
        self.monVoltagesReceived.connect(self._log_controller_voltage_readings)

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
        self._close_controller_telemetry_logger()

    @pyqtSlot(bool)
    def setControllerTelemetryLoggingEnabled(self, enabled: bool):
        """Arm/disarm controller telemetry logging.

        When armed, logging starts on the next successful Start and stops
        automatically when the run ends (Stop button or STATUS:STOPPED).
        """
        if bool(enabled):
            self._controller_telemetry_logging_enabled = True
            # If the user arms logging while already RUNNING, begin
            # immediately for the active run.
            if self._state == RUNNING and self._controller_telemetry_csv is None:
                self._start_controller_telemetry_logger()
            else:
                self.controllerTelemetryLoggingChanged.emit(True, self._controller_telemetry_log_path)
            return

        self._controller_telemetry_logging_enabled = False
        # Disarming always closes any active file.
        self._close_controller_telemetry_logger(clear_enabled=False)
        self.controllerTelemetryLoggingChanged.emit(False, "")

    @pyqtSlot(result=bool)
    def isControllerTelemetryLoggingEnabled(self):
        return bool(self._controller_telemetry_logging_enabled)

    @pyqtSlot(result=str)
    def getControllerTelemetryLogPath(self):
        return self._controller_telemetry_log_path or ""

    def _start_controller_telemetry_run_if_armed(self):
        """Open a fresh CSV file for the current run if logging is armed."""
        if not self._controller_telemetry_logging_enabled:
            return
        # Always rotate file per run.
        self._close_controller_telemetry_logger(clear_enabled=False)
        self._start_controller_telemetry_logger()
        self._controller_telemetry_sample_timer.start()

    def _stop_controller_telemetry_run(self):
        """Close the active run CSV but preserve the armed checkbox state."""
        self._controller_telemetry_sample_timer.stop()
        armed = self._controller_telemetry_logging_enabled
        if self._controller_telemetry_csv is not None:
            self._append_controller_telemetry_row("run_stopped")
        self._close_controller_telemetry_logger(clear_enabled=False)
        self.controllerTelemetryLoggingChanged.emit(bool(armed), "")

    def _controller_telemetry_sample_tick(self):
        """Periodic run-time telemetry sample while logging is active."""
        if self._state != RUNNING:
            return
        self._capture_controller_telemetry_snapshot("periodic_snapshot")

    def _capture_controller_telemetry_snapshot(self, reading: str):
        """Best-effort one-shot telemetry sample for run-boundary logging."""
        if not self._controller_telemetry_logging_enabled or self._controller_telemetry_csv is None:
            return

        tx_temp = None
        ambient_temp = None
        hv_temp1 = None
        hv_temp2 = None
        hv_positive = None
        hv_negative = None

        self._interface_mutex.lock()
        try:
            if self._txConnected:
                try:
                    tx_temp = self.interface.txdevice.get_temperature(module=0)
                    ambient_temp = self.interface.txdevice.get_ambient_temperature(module=0)
                except Exception:
                    tx_temp = None
                    ambient_temp = None

            if self._hvConnected:
                try:
                    hv_temp1 = self.interface.hvcontroller.get_temperature1()
                    hv_temp2 = self.interface.hvcontroller.get_temperature2()
                except Exception:
                    hv_temp1 = None
                    hv_temp2 = None

                try:
                    voltages = self.interface.hvcontroller.get_vmon_values()
                    if len(voltages) > 0:
                        hv_positive = self._extract_converted_voltage(voltages[0])
                    if len(voltages) > 3:
                        hv_negative = self._extract_converted_voltage(voltages[3])
                except Exception:
                    hv_positive = None
                    hv_negative = None
        finally:
            self._interface_mutex.unlock()

        self._append_controller_telemetry_row(
            reading,
            module=0,
            tx_temp=tx_temp,
            ambient_temp=ambient_temp,
            hv_temp1=hv_temp1,
            hv_temp2=hv_temp2,
            hv_positive=hv_positive,
            hv_negative=hv_negative,
        )

    def _start_controller_telemetry_logger(self):
        """Open a CSV file for controller telemetry samples."""
        try:
            logs_dir = os.path.join(os.getcwd(), "logs")
            os.makedirs(logs_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            log_path = os.path.join(logs_dir, f"controller_telemetry_{stamp}.csv")

            fh = open(log_path, "w", newline="", encoding="utf-8")
            writer = csv.writer(fh)
            writer.writerow([
                "timestamp_utc",
                "elapsed_s",
                "reading",
                "module",
                "tx_temp_c",
                "ambient_temp_c",
                "hv_temp1_c",
                "hv_temp2_c",
                "hv_positive_v",
                "hv_negative_v",
            ])

            self._controller_telemetry_file_handle = fh
            self._controller_telemetry_csv = writer
            self._controller_telemetry_t0 = time.monotonic()
            self._controller_telemetry_log_path = log_path
            logger.info("Controller telemetry logging enabled: %s", log_path)
            self._append_controller_telemetry_row("run_started")
            self._capture_controller_telemetry_snapshot("run_start_snapshot")
            self.controllerTelemetryLoggingChanged.emit(
                bool(self._controller_telemetry_logging_enabled),
                log_path,
            )
        except Exception as e:
            logger.error("Failed to enable controller telemetry logging: %s", e)
            self._controller_telemetry_log_path = ""
            self._controller_telemetry_t0 = 0.0
            self._controller_telemetry_csv = None
            if self._controller_telemetry_file_handle is not None:
                try:
                    self._controller_telemetry_file_handle.close()
                except Exception:
                    pass
            self._controller_telemetry_file_handle = None
            self.controllerTelemetryLoggingChanged.emit(
                bool(self._controller_telemetry_logging_enabled),
                "",
            )

    def _close_controller_telemetry_logger(self, clear_enabled: bool = True):
        """Close the active controller telemetry CSV file if one is open."""
        self._controller_telemetry_sample_timer.stop()
        if self._controller_telemetry_file_handle is not None:
            try:
                self._controller_telemetry_file_handle.close()
            except Exception as e:
                logger.warning("Error closing controller telemetry log: %s", e)
        if self._controller_telemetry_logging_enabled and self._controller_telemetry_log_path:
            logger.info("Controller telemetry logging disabled: %s", self._controller_telemetry_log_path)
        self._controller_telemetry_file_handle = None
        self._controller_telemetry_csv = None
        self._controller_telemetry_t0 = 0.0
        self._controller_telemetry_log_path = ""
        if clear_enabled:
            self._controller_telemetry_logging_enabled = False

    def _append_controller_telemetry_row(
        self,
        reading: str,
        module="",
        tx_temp=None,
        ambient_temp=None,
        hv_temp1=None,
        hv_temp2=None,
        hv_positive=None,
        hv_negative=None,
    ):
        if not self._controller_telemetry_logging_enabled or self._controller_telemetry_csv is None:
            return
        try:
            now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            elapsed = time.monotonic() - self._controller_telemetry_t0
            self._controller_telemetry_csv.writerow([
                now_utc,
                f"{elapsed:.3f}",
                reading,
                module,
                "" if tx_temp is None else f"{float(tx_temp):.3f}",
                "" if ambient_temp is None else f"{float(ambient_temp):.3f}",
                "" if hv_temp1 is None else f"{float(hv_temp1):.3f}",
                "" if hv_temp2 is None else f"{float(hv_temp2):.3f}",
                "" if hv_positive is None else f"{float(hv_positive):.3f}",
                "" if hv_negative is None else f"{float(hv_negative):.3f}",
            ])
            if self._controller_telemetry_file_handle is not None:
                self._controller_telemetry_file_handle.flush()
        except Exception as e:
            logger.warning("Failed to append controller telemetry row: %s", e)

    def _extract_converted_voltage(self, entry):
        """Best-effort extraction of a voltage value from SDK monitor entries."""
        if isinstance(entry, dict):
            value = entry.get("converted_voltage", entry.get("voltage", entry.get("value")))
            return value
        for attr in ("converted_voltage", "voltage", "value"):
            if hasattr(entry, attr):
                return getattr(entry, attr)
        return None

    def _log_controller_hv_temperature(self, temp1: float, temp2: float):
        self._append_controller_telemetry_row(
            "hv_temperature",
            hv_temp1=temp1,
            hv_temp2=temp2,
        )

    def _log_controller_tx_temperature(self, module: int, tx_temp: float, amb_temp: float):
        self._append_controller_telemetry_row(
            "tx_temperature",
            module=module,
            tx_temp=tx_temp,
            ambient_temp=amb_temp,
        )

    def _log_controller_voltage_readings(self, voltages):
        hv_positive = None
        hv_negative = None
        try:
            if len(voltages) > 0:
                hv_positive = self._extract_converted_voltage(voltages[0])
            if len(voltages) > 3:
                hv_negative = self._extract_converted_voltage(voltages[3])
        except Exception:
            hv_positive = None
            hv_negative = None

        self._append_controller_telemetry_row(
            "hv_voltage",
            hv_positive=hv_positive,
            hv_negative=hv_negative,
        )

    def _emit_device_error(self, title: str, message: str):
        """Log a device/communication failure and surface it to QML as a popup.

        While a firmware update is running (``_fw_update_active``) the popup
        is suppressed: a console update power-cycles the TX modules, so any
        query that races their disconnect/re-enumeration times out. Those
        failures are expected and would otherwise stack error dialogs behind
        the update's own progress dialog. They are still logged.
        """
        if self._fw_update_active:
            logger.warning("%s: %s (suppressed - firmware update in progress)",
                           title, message)
            return
        logger.error(f"{title}: {message}")
        try:
            self.deviceError.emit(title, message)
        except Exception as e:
            logger.error(f"Failed to emit deviceError signal: {e}")

    def _invalidate_device_caches(self, side: str) -> None:
        """Drop cached device-info so the next query slot re-fetches from
        the hardware. Called on disconnect (so a freshly plugged-in
        device shows its true firmware/HWID) and after firmware updates
        complete (so the new version replaces the cached old one).

        ``side`` is ``"HV"``, ``"TX"`` or ``"all"``.
        """
        if side in ("HV", "all"):
            self._cached_hv_info = None
            self._cached_hv_fw_version = None
            self._cached_rgb_state = None
        if side in ("TX", "all"):
            self._cached_tx_info = None
            self._cached_tx_fw_version = {}
        # Clear compliance buckets too so a successful firmware update
        # doesn't leave the previous (older) version's lockout/advisory
        # state visible until something re-reads. The next queryHvInfo /
        # queryTxInfo (auto-fired by the connect handlers, or by a UI
        # page) repopulates them.
        self._reset_firmware_compliance(side)

    def _update_firmware_compliance(self, side: str, version: str | None,
                                    module: int | None = None) -> None:
        """Recompute per-device firmware compliance from a fresh version read.

        ``side`` is ``"HV"`` (console) or ``"TX"`` (transmitter). For TX,
        ``module`` is the module index the version belongs to. ``version``
        may be ``None`` / ``""`` / ``"Error"`` to mark the device as
        ``FW_COMPLIANCE_UNKNOWN`` (e.g. after a failed read). Always emits
        ``firmwareComplianceChanged`` so QML rebinds the System Status
        tooltip with the freshly read version even when the compliance
        bucket itself didn't move (e.g. a same-tier point-release upgrade).
        """
        if side == "HV":
            if version and version != "Error":
                self._console_fw_compliance = firmware_compliance(
                    version,
                    MIN_CONSOLE_FW_VERSION,
                    packaged_console_fw_version(),
                )
            else:
                self._console_fw_compliance = FW_COMPLIANCE_UNKNOWN
        elif side == "TX":
            if module is None:
                return
            if version and version != "Error":
                self._tx_fw_compliance[module] = firmware_compliance(
                    version,
                    MIN_TRANSMITTER_FW_VERSION,
                    packaged_transmitter_fw_version(),
                )
            else:
                self._tx_fw_compliance[module] = FW_COMPLIANCE_UNKNOWN
        self.firmwareComplianceChanged.emit()

    def _reset_firmware_compliance(self, side: str) -> None:
        """Drop compliance state for a side after disconnect."""
        changed = False
        if side in ("HV", "all"):
            if self._console_fw_compliance != FW_COMPLIANCE_UNKNOWN:
                self._console_fw_compliance = FW_COMPLIANCE_UNKNOWN
                changed = True
        if side in ("TX", "all"):
            if self._tx_fw_compliance:
                self._tx_fw_compliance = {}
                changed = True
        if changed:
            self.firmwareComplianceChanged.emit()

    def _aggregate_tx_compliance(self) -> int:
        """Return the worst (numerically largest) TX module compliance bucket.

        With no module data we report ``UNKNOWN`` rather than ``OK`` so
        gated UI stays disabled until a real version is read.
        """
        if not self._tx_fw_compliance:
            return FW_COMPLIANCE_UNKNOWN
        return max(self._tx_fw_compliance.values())

    @pyqtSlot()
    def _check_firmware_on_connect(self):
        """Auto-populate firmware compliance whenever a device connects.

        Only kicks the HV side here; TX needs the module count first
        (handled by ``_check_tx_firmware_on_modules``).
        """
        if self._hvConnected and self._cached_hv_info is None:
            try:
                self.queryHvInfo()
            except Exception as e:
                logger.warning(f"Auto firmware-compliance HV query failed: {e}")

    @pyqtSlot()
    def _check_tx_firmware_on_modules(self):
        """Auto-populate TX firmware compliance once modules have enumerated."""
        if (self._txConnected
                and self._num_modules_connected > 0
                and self._cached_tx_info is None):
            try:
                self.queryTxInfo()
            except Exception as e:
                logger.warning(f"Auto firmware-compliance TX query failed: {e}")

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
            # HV connected without TX â€“ verification scripts can run.
            self._state = TEST_SCRIPT_READY
        self.stateChanged.emit(self._state)
        logger.debug(f"Updated state: {self._state}")

    def _hv_ready(self) -> bool:
        """Return True if HV is connected and not disabled by the user."""
        return self._hvConnected and self._hv_enable_mode != HV_EN_OFF

    @contextlib.contextmanager
    def _pause_polling_during_burst(self):
        """Temporarily pause telemetry polling for the duration of a burst.

        Used around large multi-round-trip SDK operations (set_solution,
        direct setters, firmware update) so the 1 Hz poll thread cannot
        inject temperature / voltage / module-count queries between the
        burst's individual chunks. With polling paused the per-command
        queue depth stays at 1, which materially reduces the chance that
        any individual chunk's response is scheduler-starved past the
        per-command timeout.

        Nestable: if polling is already paused (e.g. by the diagnostics
        tab) the previous state is restored on exit rather than being
        force-unpaused.
        """
        prev = self._monitoring_paused
        self._monitoring_paused = True
        try:
            yield
        finally:
            self._monitoring_paused = prev

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
    # Minimum spacing between slave-module temperature reads while RUNNING.
    # Deliberately much slower than the 1 Hz stopped-state poll: each read
    # shares the TX CDC endpoint with the unsolicited STATUS stream, so we
    # keep the collision window small.
    _SLAVE_TEMP_POLL_INTERVAL_S = 3.0

    def poll_pre_tick(self):
        """Poll slave-module temperatures while a sonication is RUNNING.

        The poll thread skips ``poll_tx_tick`` during RUNNING because the
        firmware's unsolicited STATUS frames already push the *master*
        module's temperature -- but those frames carry no slave-module
        data, so without this hook every module except 0 freezes at its
        last stopped-state reading for the whole run.

        This hook fires every cycle regardless of state; we act only while
        RUNNING (the stopped-state path in ``queryTxTemperature`` already
        covers all modules at 1 Hz). One slave module is read per poll, at
        most every ``_SLAVE_TEMP_POLL_INTERVAL_S`` seconds, round-robin.
        Failures are expected occasionally (a STATUS frame can interleave
        with the response on the shared CDC IN endpoint and time the read
        out); they are logged and dropped without touching the
        ``_tx_poll_failures`` disconnect counter -- the STATUS stream
        itself is proof the TX link is alive.
        """
        if self._state != RUNNING or not self._txConnected:
            return
        if self._num_modules_connected <= 1:
            return
        now = time.monotonic()
        if now - self._last_slave_temp_poll < self._SLAVE_TEMP_POLL_INTERVAL_S:
            return
        self._last_slave_temp_poll = now

        module = self._next_slave_temp_module
        if module < 1 or module >= self._num_modules_connected:
            module = 1
        self._next_slave_temp_module = module + 1

        self._interface_mutex.lock()
        try:
            tx_temp = self.interface.txdevice.get_temperature(module=module)
            amb_temp = self.interface.txdevice.get_ambient_temperature(module=module)
        except LIFUError as e:
            logger.debug("Module %d: temperature poll while RUNNING failed "
                         "(likely raced a STATUS frame): %s", module, e)
            return
        except Exception as e:
            logger.warning("Module %d: unexpected error polling temperature "
                           "while RUNNING: %s", module, e)
            return
        finally:
            self._interface_mutex.unlock()
        self.temperatureTxUpdated.emit(module, float(tx_temp), float(amb_temp))
        logger.debug("Module %d (RUNNING poll): Temp: %s, Ambient: %s",
                     module, tx_temp, amb_temp)

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
                "TX: %d consecutive poll failures - dropping port and triggering disconnect",
                self._TX_POLL_FAIL_LIMIT,
            )
            self._tx_poll_failures = 0
            # Drop the underlying TX serial port so the SDK actually releases
            # the connection; on_disconnected only updates flags/signals and
            # would leave the SDK in a still-connected (but failing) state
            # otherwise. IMPORTANT: use disconnect() (not close()) so the
            # SDK's USB monitor thread keeps running and can auto-reconnect
            # when the device returns after e.g. a power cycle. close()
            # would stop the monitor thread and leave the port permanently
            # unwatched.
            try:
                self.interface.txdevice.disconnect()
            except Exception as close_exc:
                logger.debug("TX disconnect during failure recovery: %s", close_exc)
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
                "HV: %d consecutive poll failures - dropping port and triggering disconnect",
                self._HV_POLL_FAIL_LIMIT,
            )
            self._hv_poll_failures = 0
            # Drop the underlying HV serial port (don't call close(), which
            # would also stop the SDK's USB monitor thread and prevent
            # auto-reconnect after a console power cycle). disconnect()
            # closes the serial handle but leaves the monitor running so
            # signal_connected fires again when the device returns.
            try:
                self.interface.hvcontroller.disconnect()
            except Exception as close_exc:
                logger.debug("HV disconnect during failure recovery: %s", close_exc)
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
        logger.info("%s connected on %s", descriptor, port)
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
            # The TX firmware loses its RAM state on power cycle, so any
            # previously programmed solution (delays, apodizations, pulse,
            # trigger) is gone. Re-enumerate modules and drop the
            # "configured" flag so the UI returns to CONNECTED (not READY)
            # on reconnect and the operator is forced to re-configure.
            self._num_modules_connected = 0
            self._configured = False
            self._invalidate_device_caches("TX")
            # Re-enable safety checks on disconnect 
            if self._bypass_safety_checks:
                self._bypass_safety_checks = False
                self.safetyBypassChanged.emit(False)
                logger.warning("[SAFETY] Solution safety limits re-enabled "
                               "automatically on TX disconnect")
        elif descriptor == "HV":
            self._hvConnected = False
            self._hv_poll_failures = 0
            # HV firmware loses its voltage setting on power cycle, so any
            # configured solution is no longer valid. Drop the configured
            # flag for the same reason as above; otherwise a power cycle
            # while configured leaves the UI in a stale READY state.
            self._configured = False
            self._invalidate_device_caches("HV")
            # If HV was set to "ON" mode, automatically switch to "OFF" when disconnected
            if self._hv_enable_mode == HV_EN_ON:  # ON mode
                self._hv_enable_mode = HV_EN_OFF  # Switch to OFF
                self.hvEnableModeChanged.emit(self._hv_enable_mode)
                logger.info("HV enable mode automatically switched to OFF due to HV disconnection")

        logger.info("%s disconnected (port=%s)", descriptor, port or "?")
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

                    # Log TX status telemetry directly while RUNNING/STOPPED.
                    # Do this before STOPPED closes the run file.
                    self._append_controller_telemetry_row(
                        "tx_status",
                        module=0,
                        tx_temp=parsed.get("temp_tx"),
                        ambient_temp=parsed.get("temp_ambient"),
                    )
                    
                    if parsed["status"] == "STOPPED":
                        logger.debug("Trigger is stopped.")
                        self._async_mode_enabled = False
                        self._capture_controller_telemetry_snapshot("run_stop_snapshot")
                        self._stop_controller_telemetry_run()
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

    @staticmethod
    def _extract_focus_points(data, fallback_position):
        """Return a solution's foci as ``[{'x':, 'y':, 'z':}, ...]`` for QML.

        Prefers the ``foci`` list (present on multi-focus solutions and on
        anything this app has saved); falls back to the single ``target``
        position for older or hand-written files.
        """
        points = []
        for entry in data.get('foci', []) or []:
            position = entry.get('position') if isinstance(entry, dict) else entry
            if position is None or len(position) < 3:
                continue
            try:
                points.append({
                    'x': round(float(position[0]), 3),
                    'y': round(float(position[1]), 3),
                    'z': round(float(position[2]), 3),
                })
            except (TypeError, ValueError):
                logger.warning(f"Skipping focus with non-numeric position: {position}")

        if not points:
            points = [{
                'x': round(float(fallback_position[0]), 3),
                'y': round(float(fallback_position[1]), 3),
                'z': round(float(fallback_position[2]), 3),
            }]
        return points

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

        focus_points = self._extract_focus_points(data, focus_position)
        try:
            execution_order = [int(v) for v in (data.get('execution_order') or [])]
        except (TypeError, ValueError):
            logger.warning("Solution has a non-integer execution_order; using the default order.")
            execution_order = []
        if not execution_order:
            execution_order = list(range(1, len(focus_points) + 1))

        return {
            # xInput/yInput/zInput mirror the first focus. Kept for the
            # single-focus callers (Transmitter page, operator interface)
            # that predate multi-focus support.
            'xInput': focus_points[0]['x'],
            'yInput': focus_points[0]['y'],
            'zInput': focus_points[0]['z'],
            'foci': focus_points,
            'executionOrder': execution_order,
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
                                    pulseInterval, pulseCount, trainInterval, trainCount, durationS,
                                    foci=None, executionOrder=None):
        solution = self.get_solution(
            xInput, yInput, zInput,
            freq, voltage, pulseInterval, pulseCount,
            trainInterval, trainCount, durationS,
            validate=True,
            foci=foci, executionOrder=executionOrder
        )
        if solution is None:
            raise ValueError("failed to build a valid solution")

        solution_data = self._to_json_compatible(solution)
        cleaned_id = (solution_id or "").strip() or "solution"
        cleaned_name = (solution_name or "").strip() or cleaned_id

        # get_solution already populated "foci" and "execution_order" from
        # the focus list; "target" mirrors the first focus so single-focus
        # files keep the exact shape older readers expect.
        exported_foci = solution_data.get("foci") or []
        target_position = (exported_foci[0]["position"] if exported_foci
                           else [float(xInput), float(yInput), float(zInput)])

        pinmap_data = self._load_pinmap_data(num_modules)
        solution_data["id"] = cleaned_id
        solution_data["name"] = cleaned_name
        solution_data["target"] = {
            "position": target_position,
            "units": "mm"
        }
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
    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, str, str, str,
              'QVariantList', 'QVariantList', result=bool)
    def saveSolutionToFile(self, solution_id, solution_name, file_path, num_modules_str,
                           xInput, yInput, zInput, freq, voltage,
                           pulseInterval, pulseCount, trainInterval, trainCount, durationS,
                           foci=None, executionOrder=None):
        """Save the current solution to a JSON file.

        num_modules_str: number of TX modules to use for the transducer field.
        When TX is connected, this is read from hardware; when offline it comes from the UI spinbox.

        foci/executionOrder: optional multi-focus configuration. When
        omitted the single focus given by xInput/yInput/zInput is saved.
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
                trainInterval, trainCount, durationS,
                foci=foci, executionOrder=executionOrder
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

    @pyqtProperty(bool, notify=safetyBypassChanged)
    def safetyBypassEnabled(self):
        """Expose the solution safety-limit bypass to QML."""
        return self._bypass_safety_checks

    # Hardware limits, surfaced so QML validates against the SDK's numbers
    # instead of restating them. ``constant=True`` -- these cannot change
    # for the lifetime of the process, so QML binds them once.

    @pyqtProperty(int, constant=True)
    def maxFocusPoints(self):
        """Delay-RAM profile slots available for foci (SDK-derived)."""
        return MAX_FOCUS_POINTS

    @pyqtProperty('QStringList', constant=True)
    def focusProfileColors(self):
        """Per-focus colours as '#rrggbb', indexed 0-based by focus.

        Comes from the plot module so the dropdown swatches and the markers
        on the element map are the same colours by construction. Long enough
        to cover every programmable focus; the cycle repeats past 10, exactly
        as the plot's does.
        """
        return profile_color_hex(MAX_FOCUS_POINTS)

    @pyqtProperty(float, constant=True)
    def minProfileSwitchIntervalMs(self):
        """Inter-pulse dead time the firmware needs to switch focus, in ms.

        Exposed in milliseconds because the Controller page works in the
        units of its own fields (pulse interval in ms), not SI seconds.
        """
        return MIN_PROFILE_SWITCH_INTERVAL_S * 1e3

    @pyqtProperty(int, notify=firmwareComplianceChanged)
    def consoleFirmwareCompliance(self) -> int:
        """Compliance bucket (FW_COMPLIANCE_*) for the console firmware."""
        return self._console_fw_compliance

    @pyqtProperty(int, notify=firmwareComplianceChanged)
    def transmitterFirmwareCompliance(self) -> int:
        """Aggregate (worst) compliance bucket across known TX modules."""
        return self._aggregate_tx_compliance()

    @pyqtProperty(bool, notify=firmwareComplianceChanged)
    def firmwareUpdateRequired(self) -> bool:
        """True iff any connected device firmware is below its hard minimum.

        Drives the lockout on Configure (Controller) and on Write Config
        / Add Device Configuration (Settings). Only checks compliance
        for sides that are currently connected so a missing device does
        not falsely lock the UI.
        """
        if (self._txConnected
                and self._aggregate_tx_compliance() == FW_COMPLIANCE_UPDATE_REQUIRED):
            return True
        if (self._hvConnected
                and self._console_fw_compliance == FW_COMPLIANCE_UPDATE_REQUIRED):
            return True
        return False

    @pyqtProperty(bool, notify=firmwareComplianceChanged)
    def firmwareUpdateAvailable(self) -> bool:
        """True iff any connected device firmware is below the packaged version."""
        if (self._txConnected
                and self._aggregate_tx_compliance() == FW_COMPLIANCE_UPDATE_AVAILABLE):
            return True
        if (self._hvConnected
                and self._console_fw_compliance == FW_COMPLIANCE_UPDATE_AVAILABLE):
            return True
        return False

    @pyqtProperty(str, notify=firmwareComplianceChanged)
    def firmwareStatusReport(self) -> str:
        """Multi-line summary of every connected device's live firmware
        version vs the app's hard minimum and SDK-packaged versions.

        Surfaced as the System Status tooltip on the Controller page so
        an operator hovering the status text can see the actual values
        coming back from each module rather than just a "Firmware
        Update Required" banner. Empty when nothing is connected.
        """
        labels = {
            FW_COMPLIANCE_OK: "[OK]",
            FW_COMPLIANCE_UPDATE_AVAILABLE: "[update available]",
            FW_COMPLIANCE_UPDATE_REQUIRED: "[UPDATE REQUIRED]",
            FW_COMPLIANCE_UNKNOWN: "[unknown]",
        }
        if not (self._hvConnected or self._txConnected):
            return ""
        lines: list[str] = []
        lines.append(
            f"Min required: console v{MIN_CONSOLE_FW_VERSION}, "
            f"transmitter v{MIN_TRANSMITTER_FW_VERSION}"
        )
        pcv = packaged_console_fw_version() or "?"
        ptv = packaged_transmitter_fw_version() or "?"
        lines.append(f"SDK-packaged:  console v{pcv}, transmitter v{ptv}")
        lines.append("")
        if self._hvConnected:
            cv = self._cached_hv_fw_version or "(reading…)"
            tag = labels.get(self._console_fw_compliance, "[unknown]")
            lines.append(f"Console:  v{cv}  {tag}")
        if self._txConnected:
            if not self._cached_tx_fw_version:
                lines.append("TX modules: (reading…)")
            else:
                for module in sorted(self._cached_tx_fw_version):
                    v = self._cached_tx_fw_version[module]
                    tag = labels.get(
                        self._tx_fw_compliance.get(module, FW_COMPLIANCE_UNKNOWN),
                        "[unknown]",
                    )
                    lines.append(f"TX {module}:  v{v}  {tag}")
        return "\n".join(lines)

    @pyqtProperty(str, constant=True)
    def minConsoleFirmwareVersion(self) -> str:
        return MIN_CONSOLE_FW_VERSION

    @pyqtProperty(str, constant=True)
    def minTransmitterFirmwareVersion(self) -> str:
        return MIN_TRANSMITTER_FW_VERSION

    @pyqtProperty(str, constant=True)
    def packagedConsoleFirmwareVersion(self) -> str:
        # Stable for the life of the process; the SDK's bundled-or-
        # downloaded firmware set is captured at startup, so QML can
        # treat this as constant.
        return packaged_console_fw_version() or ""

    @pyqtProperty(str, constant=True)
    def packagedTransmitterFirmwareVersion(self) -> str:
        return packaged_transmitter_fw_version() or ""

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

