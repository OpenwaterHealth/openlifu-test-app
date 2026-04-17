from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from openlifu_sdk import LIFUInterface

from lifu_connector_constants import (
    STATE_DISCONNECTED,
    STATE_TX_CONNECTED,
    STATE_CONFIGURED,
    STATE_READY,
    STATE_RUNNING,
    RGB_STATE_NAMES,
)
from lifu_connector_hv import HvSlotsMixin
from lifu_connector_solution import SolutionSlotsMixin
from lifu_connector_tx import TxSlotsMixin

logger = logging.getLogger(__name__)


def _get_sdk_version() -> str:
    try:
        from importlib.metadata import version
        return version("openlifu-sdk")
    except Exception:
        pass
    try:
        import openlifu_sdk  # noqa: F401
        return getattr(openlifu_sdk, "__version__", "unknown")
    except Exception:
        return "unknown"


class LIFUConnector(QObject, TxSlotsMixin, HvSlotsMixin, SolutionSlotsMixin):
    """Bridge between the QML UI and the openlifu-sdk hardware interface.

    Connection-state tracking runs on a 500 ms polling timer on the main Qt
    thread so all signal emissions are inherently thread-safe.

    Functional domains are split into focused mixin modules:
      - lifu_connector_tx.py       – TX transmitter slots
      - lifu_connector_hv.py       – Console (HV) slots
      - lifu_connector_solution.py – Solution / plot / preset slots
    """

    # -----------------------------------------------------------------------
    # Qt Signals
    # -----------------------------------------------------------------------

    # Connection state
    txConnectedChanged  = pyqtSignal()
    hvConnectedChanged  = pyqtSignal()
    stateChanged        = pyqtSignal()
    solutionLoadedChanged = pyqtSignal()

    # TX device info / telemetry
    txDeviceInfoReceived    = pyqtSignal(list)            # list of module dicts
    temperatureTxUpdated    = pyqtSignal(int, float, float)  # module, tx_temp, amb_temp
    triggerStateChanged     = pyqtSignal(bool)
    txConfigStateChanged    = pyqtSignal(bool)

    # Console (HV) device info / telemetry
    hvDeviceInfoReceived    = pyqtSignal(str, str)        # fw_version, device_id
    temperatureHvUpdated    = pyqtSignal(float, float)   # temp1, temp2
    powerStatusReceived     = pyqtSignal(bool, bool)     # v12_on, hv_on
    monVoltagesReceived     = pyqtSignal(list)           # 8-channel voltage dicts
    rgbStateReceived        = pyqtSignal(int, str)       # state_value, state_text

    # Power state changes
    hvStateChanged          = pyqtSignal()
    v12StateChanged         = pyqtSignal()

    # Generic hardware events (consumed by Demo.qml)
    signalConnected         = pyqtSignal(str, str)       # descriptor, port
    signalDisconnected      = pyqtSignal(str, str)       # descriptor, port
    signalDataReceived      = pyqtSignal(str, str)       # descriptor, message

    # Solution lifecycle
    solutionStateChanged    = pyqtSignal()
    solutionFileLoaded      = pyqtSignal(str, str)       # name, message
    solutionLoadError       = pyqtSignal(str)            # error_message
    solutionSaveStatus      = pyqtSignal(bool, str)      # success, message

    # Plot
    plotGenerated           = pyqtSignal(str)            # raw base64 PNG data

    # Firmware
    fwVersionRead           = pyqtSignal(str, str)       # device_type, version
    fwUpdateProgress        = pyqtSignal(str, int)       # message, percent (-1 = error)
    fwUpdateStatus          = pyqtSignal(str, bool, str) # device_type, success, message

    # User config
    userConfigRead          = pyqtSignal(str, str)       # target, json_str
    userConfigStatus        = pyqtSignal(str, bool, str) # target, success, message

    # TX module count updated
    numModulesUpdated       = pyqtSignal()

    # Test report
    testReportLoaded        = pyqtSignal(bool, str)      # success, message

    # -----------------------------------------------------------------------
    # Initialisation
    # -----------------------------------------------------------------------

    def __init__(self, hv_test_mode: bool = False, parent=None):
        super().__init__(parent)

        self._hv_test_mode = hv_test_mode
        self._interface    = LIFUInterface()

        # Internal state ---------------------------------------------------
        self._tx_connected    : bool       = False
        self._hv_connected    : bool       = False
        self._state           : int        = STATE_DISCONNECTED
        self._trigger_enabled : bool       = False
        self._hv_state        : bool       = False
        self._v12_state       : bool       = False
        self._solution_loaded : bool       = False
        self._loaded_solution : dict | None = None
        self._num_modules     : int        = 1
        self._manual_num_modules: int      = 1
        self._sdk_version     : str        = _get_sdk_version()

        # Last-known ports (populated by SDK signal hooks) -----------------
        self._tx_port: str = ""
        self._hv_port: str = ""

        # Hook SDK OWSignals to capture port info (runs on SDK threads) ----
        self._interface.transmitter.signal_connected.connect(self._on_sdk_tx_connected)
        self._interface.transmitter.signal_disconnected.connect(self._on_sdk_tx_disconnected)
        self._interface.console.signal_connected.connect(self._on_sdk_hv_connected)
        self._interface.console.signal_disconnected.connect(self._on_sdk_hv_disconnected)

        # Polling timer – runs on main Qt thread ---------------------------
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_connection_state)

    # -----------------------------------------------------------------------
    # Qt Properties
    # -----------------------------------------------------------------------

    @pyqtProperty(bool, notify=txConnectedChanged)
    def txConnected(self) -> bool:
        return self._tx_connected

    @pyqtProperty(bool, notify=hvConnectedChanged)
    def hvConnected(self) -> bool:
        return self._hv_connected

    @pyqtProperty(int, notify=stateChanged)
    def state(self) -> int:
        return self._state

    @pyqtProperty(str)
    def sdkVersion(self) -> str:
        return self._sdk_version

    @pyqtProperty(bool, notify=solutionLoadedChanged)
    def solutionLoaded(self) -> bool:
        return self._solution_loaded

    @pyqtProperty(bool)
    def triggerEnabled(self) -> bool:
        return self._trigger_enabled

    @pyqtProperty(bool, notify=hvStateChanged)
    def hvState(self) -> bool:
        return self._hv_state

    @pyqtProperty(str, notify=v12StateChanged)
    def v12State(self) -> str:
        return "On" if self._v12_state else "Off"

    @pyqtProperty(int)
    def queryNumModulesConnected(self) -> int:
        return self._num_modules

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start_monitoring(self):
        """Start SDK background threads and the connection poll timer."""
        self._interface.start()
        self._poll_timer.start()
        logger.info("LIFUConnector monitoring started (sdk=%s)", self._sdk_version)

    def stop_monitoring(self):
        """Stop poll timer and SDK background threads."""
        self._poll_timer.stop()
        try:
            self._interface.stop()
        except Exception as exc:
            logger.warning("stop_monitoring: %s", exc)
        logger.info("LIFUConnector monitoring stopped.")

    # -----------------------------------------------------------------------
    # SDK signal hooks (called from SDK background threads – minimal work only)
    # -----------------------------------------------------------------------

    def _on_sdk_tx_connected(self, desc: str, port: str):
        self._tx_port = port

    def _on_sdk_tx_disconnected(self, desc: str):
        self._tx_port = ""

    def _on_sdk_hv_connected(self, desc: str, port: str):
        self._hv_port = port

    def _on_sdk_hv_disconnected(self, desc: str):
        self._hv_port = ""

    # -----------------------------------------------------------------------
    # Connection polling (main-thread safe, called by QTimer)
    # -----------------------------------------------------------------------

    def _poll_connection_state(self):
        tx = self._interface.transmitter.is_connected()
        hv = self._interface.console.is_connected()

        if tx != self._tx_connected:
            self._tx_connected = tx
            self.txConnectedChanged.emit()
            if tx:
                self.signalConnected.emit("TX", self._tx_port)
            else:
                self._trigger_enabled = False
                port = self._tx_port
                self._tx_port = ""
                self.signalDisconnected.emit("TX", port)

        if hv != self._hv_connected:
            self._hv_connected = hv
            self.hvConnectedChanged.emit()
            if hv:
                self.signalConnected.emit("HV", self._hv_port)
            else:
                self._hv_state = False
                port = self._hv_port
                self._hv_port = ""
                self.signalDisconnected.emit("HV", port)

        self._update_state()

    def _update_state(self):
        """Recompute and publish the system state integer."""
        if self._state == STATE_RUNNING:
            # Emergency stop if TX drops while running
            if not self._tx_connected:
                self._trigger_enabled = False
                self._set_state(STATE_DISCONNECTED)
            return

        if not self._tx_connected:
            if self._state != STATE_DISCONNECTED:
                self._set_state(STATE_DISCONNECTED)
            return

        # TX is connected
        if self._state == STATE_DISCONNECTED:
            self._set_state(STATE_TX_CONNECTED)
            return

        if self._state == STATE_TX_CONNECTED:
            return  # stay until configure_transmitter is called

        if self._state == STATE_CONFIGURED:
            if self._hv_connected:
                self._set_state(STATE_READY)
            return

        if self._state == STATE_READY:
            if not self._hv_connected:
                self._set_state(STATE_CONFIGURED)
            return

    def _set_state(self, new_state: int):
        if new_state != self._state:
            self._state = new_state
            self.stateChanged.emit()

    # -----------------------------------------------------------------------
    # Shared comms slots (TX + HV)
    # -----------------------------------------------------------------------

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendPingCommand(self, device: str, module: int = 0) -> bool:
        try:
            if device.upper() == "TX":
                if not self._tx_connected:
                    return False
                return self._interface.transmitter.ping(module=module)
            else:  # HV / CONSOLE
                if not self._hv_connected:
                    return False
                return self._interface.console.ping()
        except Exception as exc:
            logger.error("sendPingCommand(%s): %s", device, exc)
        return False

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendLedToggleCommand(self, device: str, module: int = 0) -> bool:
        try:
            if device.upper() == "TX":
                if not self._tx_connected:
                    return False
                return self._interface.transmitter.toggle_led(module=module)
            else:
                if not self._hv_connected:
                    return False
                return self._interface.console.toggle_led()
        except Exception as exc:
            logger.error("sendLedToggleCommand(%s): %s", device, exc)
        return False

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendEchoCommand(self, device: str, module: int = 0) -> bool:
        try:
            if device.upper() == "TX":
                if not self._tx_connected:
                    return False
                _, length = self._interface.transmitter.echo(module=module)
                return length > 0
            else:
                if not self._hv_connected:
                    return False
                _, length = self._interface.console.echo()
                return length > 0
        except Exception as exc:
            logger.error("sendEchoCommand(%s): %s", device, exc)
        return False

    # -----------------------------------------------------------------------
    # Settings slots
    # -----------------------------------------------------------------------

    @pyqtSlot(str)
    def readUserConfig(self, target: str):
        try:
            if target.upper().startswith("TX"):
                if not self._tx_connected:
                    return
                module = int(target.split()[-1]) if " " in target else 0
                cfg = self._interface.transmitter.read_config(module=module)
            else:
                if not self._hv_connected:
                    return
                cfg = self._interface.console.read_config()

            if cfg is not None:
                self.userConfigRead.emit(target, cfg.get_json_str())
            else:
                self.userConfigRead.emit(target, "{}")
        except Exception as exc:
            logger.error("readUserConfig: %s", exc)

    @pyqtSlot(str, str)
    def writeUserConfig(self, target: str, json_str: str):
        try:
            if target.upper().startswith("TX"):
                if not self._tx_connected:
                    return
                module = int(target.split()[-1]) if " " in target else 0
                self._interface.transmitter.write_config_json(json_str, module=module)
            else:
                if not self._hv_connected:
                    return
                self._interface.console.write_config_json(json_str)
            self.userConfigStatus.emit(target, True, "Config written successfully")
        except Exception as exc:
            logger.error("writeUserConfig: %s", exc)
            self.userConfigStatus.emit(target, False, str(exc))

    @pyqtSlot(str, result=str)
    def getDefaultFirmwarePath(self, device_type: str) -> str:
        fw_dir = Path(__file__).parent / "firmware"
        if not fw_dir.exists():
            return ""
        name = device_type.lower()
        candidates = (
            list(fw_dir.glob(f"*{name}*.dfu")) +
            list(fw_dir.glob(f"*{name}*.bin"))
        )
        return str(candidates[0]) if candidates else ""

    @pyqtSlot(str, str)
    def loadTestReport(self, file_path: str, target: str):
        """Load a test-report JSON and emit :attr:`userConfigRead` for *target*."""
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            payload  = data.get("config", data)
            json_str = json.dumps(payload, indent=2)
            self.userConfigRead.emit(target, json_str)
            self.testReportLoaded.emit(True, "Test report loaded successfully")
        except Exception as exc:
            logger.error("loadTestReport: %s", exc)
            self.testReportLoaded.emit(False, str(exc))

