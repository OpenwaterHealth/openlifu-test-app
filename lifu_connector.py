from __future__ import annotations

import asyncio
import base64
import glob
import json
import logging
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from openlifu_sdk import LIFUInterface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure the scripts package is importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from generate_ultrasound_plot import generate_ultrasound_plot_from_solution as _gen_plot
except ImportError:
    _gen_plot = None

# ---------------------------------------------------------------------------
# State machine constants
# ---------------------------------------------------------------------------
STATE_DISCONNECTED  = 0  # No TX connected
STATE_TX_CONNECTED  = 1  # TX connected, not yet configured
STATE_CONFIGURED    = 2  # TX programmed with beam parameters
STATE_READY         = 3  # Configured + HV/Console connected
STATE_RUNNING       = 4  # Sonication in progress

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------
RGB_STATE_NAMES = {0: "Off", 1: "Red", 2: "Blue", 3: "Green"}


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


class LIFUConnector(QObject):
    """Bridge between the QML UI and the openlifu-sdk hardware interface.

    Exposes Qt properties and signals that the QML layer reads/connects to, and
    Qt slots for every action the QML can invoke.  Connection-state tracking
    is done with a 500 ms polling timer that runs on the main Qt thread, so all
    signal emissions are inherently thread-safe.
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

    # User config
    userConfigRead          = pyqtSignal(str, str)       # target, json_str

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
    # TX Slots
    # -----------------------------------------------------------------------

    @pyqtSlot()
    def queryNumModules(self):
        if not self._tx_connected:
            return
        try:
            count = self._interface.transmitter.get_module_count()
            if count > 0:
                self._num_modules = count
        except Exception as exc:
            logger.error("queryNumModules: %s", exc)

    @pyqtSlot()
    def queryTxInfo(self):
        if not self._tx_connected:
            return
        try:
            modules = []
            for i in range(self._num_modules):
                fw    = self._interface.transmitter.get_version()
                hw_id = self._interface.transmitter.get_hardware_id() or "N/A"
                modules.append({"firmwareVersion": fw, "deviceId": hw_id})
            self.txDeviceInfoReceived.emit(modules)
        except Exception as exc:
            logger.error("queryTxInfo: %s", exc)

    @pyqtSlot()
    def queryTxTemperature(self):
        if not self._tx_connected:
            return
        try:
            for i in range(self._num_modules):
                tx_temp  = self._interface.transmitter.get_temperature(module=i) or 0.0
                amb_temp = self._interface.transmitter.get_ambient(module=i) or 0.0
                self.temperatureTxUpdated.emit(i, tx_temp, amb_temp)
        except Exception as exc:
            logger.error("queryTxTemperature: %s", exc)

    @pyqtSlot()
    def queryTriggerInfo(self):
        if not self._tx_connected:
            return
        try:
            trigger = self._interface.transmitter.get_trigger()
            if trigger is not None:
                self.txConfigStateChanged.emit(True)
        except Exception as exc:
            logger.error("queryTriggerInfo: %s", exc)

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendPingCommand(self, device: str, module: int = 0) -> bool:
        try:
            if device.upper() == "TX":
                if not self._tx_connected:
                    return False
                return self._interface.transmitter.ping()
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
                return self._interface.transmitter.toggle_led()
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
                _, length = self._interface.transmitter.echo()
                return length > 0
            else:
                if not self._hv_connected:
                    return False
                _, length = self._interface.console.echo()
                return length > 0
        except Exception as exc:
            logger.error("sendEchoCommand(%s): %s", device, exc)
        return False

    @pyqtSlot(str, result=bool)
    def setTrigger(self, json_string: str) -> bool:
        if not self._tx_connected:
            return False
        try:
            data   = json.loads(json_string)
            result = self._interface.transmitter.set_trigger(data)
            if result is not None:
                self.txConfigStateChanged.emit(True)
                return True
        except Exception as exc:
            logger.error("setTrigger: %s", exc)
        return False

    @pyqtSlot(result=bool)
    def toggleTrigger(self) -> bool:
        if not self._tx_connected:
            return False
        try:
            if self._trigger_enabled:
                ok = self._interface.transmitter.stop_trigger()
                if ok:
                    self._trigger_enabled = False
                    self.triggerStateChanged.emit(False)
                return ok
            else:
                ok = self._interface.transmitter.start_trigger()
                if ok:
                    self._trigger_enabled = True
                    self.triggerStateChanged.emit(True)
                return ok
        except Exception as exc:
            logger.error("toggleTrigger: %s", exc)
        return False

    @pyqtSlot('QVariant', 'QVariant', 'QVariant', 'QVariant', 'QVariant',
              'QVariant', 'QVariant', 'QVariant', 'QVariant', 'QVariant', str)
    def configure_transmitter(self, x, y, z, frequency, voltage,
                               pulse_interval, pulse_count,
                               train_interval, train_count,
                               duration, mode: str):
        """Set trigger parameters and advance the state machine to CONFIGURED."""
        if not self._tx_connected:
            logger.warning("configure_transmitter: TX not connected")
            return
        try:
            p_interval_s = float(pulse_interval) / 1000.0  # ms → s
            p_count      = int(float(pulse_count))
            t_interval_s = float(train_interval)            # already seconds
            t_count      = int(float(train_count))

            trigger_dict = {
                "pulse_interval":       p_interval_s,
                "pulse_count":          p_count,
                "pulse_train_interval": t_interval_s,
                "pulse_train_count":    t_count,
                "mode":                 mode.lower(),
            }

            result = self._interface.transmitter.set_trigger(trigger_dict)
            if result is not None:
                logger.info("configure_transmitter: trigger applied OK")
                self.txConfigStateChanged.emit(True)
                new_state = STATE_READY if self._hv_connected else STATE_CONFIGURED
                self._set_state(new_state)
            else:
                logger.error("configure_transmitter: set_trigger returned None")
        except Exception as exc:
            logger.error("configure_transmitter: %s", exc)

    @pyqtSlot(int)
    def softResetTXModule(self, module_index: int):
        if not self._tx_connected:
            return
        try:
            self._interface.transmitter.soft_reset()
        except Exception as exc:
            logger.error("softResetTXModule: %s", exc)

    @pyqtSlot(int)
    def readTxFirmwareVersion(self, module_index: int):
        if not self._tx_connected:
            return
        try:
            ver = self._interface.transmitter.get_version()
            self.fwVersionRead.emit(f"transmitter-{module_index}", ver)
        except Exception as exc:
            logger.error("readTxFirmwareVersion: %s", exc)

    @pyqtSlot(str)
    def readUserConfig(self, target: str):
        try:
            if target.startswith("TX"):
                if not self._tx_connected:
                    return
                module = int(target.split()[-1]) if " " in target else 0
                cfg = self._interface.transmitter.read_config(module=module)
            else:
                if not self._hv_connected:
                    return
                cfg = self._interface.console.read_config()

            if cfg is not None:
                json_str = (
                    cfg.json_data
                    if isinstance(cfg.json_data, str)
                    else json.dumps(cfg.json_data, indent=2)
                )
                self.userConfigRead.emit(target, json_str)
            else:
                self.userConfigRead.emit(target, "{}")
        except Exception as exc:
            logger.error("readUserConfig: %s", exc)

    @pyqtSlot(str, str)
    def writeUserConfig(self, target: str, json_str: str):
        try:
            if target.startswith("TX"):
                if not self._tx_connected:
                    return
                module = int(target.split()[-1]) if " " in target else 0
                self._interface.transmitter.write_config_json(json_str, module=module)
            else:
                if not self._hv_connected:
                    return
                self._interface.console.write_config_json(json_str)
        except Exception as exc:
            logger.error("writeUserConfig: %s", exc)

    @pyqtSlot()
    def start_sonication(self):
        if self._state != STATE_READY:
            logger.warning("start_sonication: not in READY state (current=%d)", self._state)
            return
        try:
            ok = self._interface.transmitter.start_trigger()
            if ok:
                self._trigger_enabled = True
                self._set_state(STATE_RUNNING)
                self.triggerStateChanged.emit(True)
        except Exception as exc:
            logger.error("start_sonication: %s", exc)

    @pyqtSlot()
    def stop_sonication(self):
        if self._state != STATE_RUNNING:
            return
        try:
            self._interface.transmitter.stop_trigger()
            self._trigger_enabled = False
            self.triggerStateChanged.emit(False)
            self._set_state(STATE_READY if self._hv_connected else STATE_CONFIGURED)
        except Exception as exc:
            logger.error("stop_sonication: %s", exc)

    @pyqtSlot()
    def reset_configuration(self):
        """Drop back to TX_CONNECTED (unconfigured) state."""
        if self._tx_connected:
            self._set_state(STATE_TX_CONNECTED)
        else:
            self._set_state(STATE_DISCONNECTED)
        self.txConfigStateChanged.emit(False)

    # -----------------------------------------------------------------------
    # Console (HV) Slots
    # -----------------------------------------------------------------------

    @pyqtSlot()
    def queryHvInfo(self):
        if not self._hv_connected:
            return
        try:
            ver   = self._interface.console.get_version()
            hw_id = self._interface.console.get_hardware_id() or "N/A"
            self.hvDeviceInfoReceived.emit(ver, hw_id)
        except Exception as exc:
            logger.error("queryHvInfo: %s", exc)

    @pyqtSlot()
    def queryHvTemperature(self):
        if not self._hv_connected:
            return
        try:
            t1 = self._interface.console.get_temperature1()
            t2 = self._interface.console.get_temperature2()
            self.temperatureHvUpdated.emit(t1, t2)
        except Exception as exc:
            logger.error("queryHvTemperature: %s", exc)

    @pyqtSlot()
    def queryPowerStatus(self):
        if not self._hv_connected:
            return
        try:
            v12 = self._interface.console.get_12v_status()
            hv  = self._interface.console.get_hv_status()
            if hv != self._hv_state:
                self._hv_state = hv
                self.hvStateChanged.emit()
            if v12 != self._v12_state:
                self._v12_state = v12
                self.v12StateChanged.emit()
            self.powerStatusReceived.emit(v12, hv)
        except Exception as exc:
            logger.error("queryPowerStatus: %s", exc)

    @pyqtSlot()
    def queryRGBState(self):
        if not self._hv_connected:
            return
        try:
            state = self._interface.console.get_rgb()
            label = RGB_STATE_NAMES.get(state, "Unknown")
            self.rgbStateReceived.emit(state, label)
        except Exception as exc:
            logger.error("queryRGBState: %s", exc)

    @pyqtSlot()
    def getMonitorVoltages(self):
        if not self._hv_connected:
            return
        try:
            voltages = self._interface.console.get_voltage_monitor()
            self.monVoltagesReceived.emit(voltages)
        except Exception as exc:
            logger.error("getMonitorVoltages: %s", exc)

    @pyqtSlot('QVariant', result=bool)
    def setHVCommand(self, voltage) -> bool:
        if not self._hv_connected:
            return False
        try:
            return self._interface.console.set_hv(float(voltage))
        except Exception as exc:
            logger.error("setHVCommand: %s", exc)
        return False

    @pyqtSlot()
    def toggleHV(self):
        if not self._hv_connected:
            return
        try:
            if self._hv_state:
                ok = self._interface.console.turn_hv_off()
                if ok:
                    self._hv_state = False
                    self.hvStateChanged.emit()
            else:
                ok = self._interface.console.turn_hv_on()
                if ok:
                    self._hv_state = True
                    self.hvStateChanged.emit()
        except Exception as exc:
            logger.error("toggleHV: %s", exc)

    @pyqtSlot()
    def turnOffHV(self):
        if not self._hv_connected:
            return
        try:
            self._interface.console.turn_hv_off()
            if self._hv_state:
                self._hv_state = False
                self.hvStateChanged.emit()
        except Exception as exc:
            logger.error("turnOffHV: %s", exc)

    @pyqtSlot(int, int, result=bool)
    def setFanLevel(self, fan_id: int, speed: int) -> bool:
        """Set fan speed.  fan_id: 0 = bottom, 1 = top.  speed: 0-100 %."""
        if not self._hv_connected:
            return False
        try:
            result = self._interface.console.set_fan(fan_id=fan_id, speed=speed)
            return result >= 0
        except Exception as exc:
            logger.error("setFanLevel(%d, %d): %s", fan_id, speed, exc)
        return False

    @pyqtSlot(int)
    def setRGBState(self, state: int):
        if not self._hv_connected:
            return
        try:
            self._interface.console.set_rgb(state)
            label = RGB_STATE_NAMES.get(state, "Unknown")
            self.rgbStateReceived.emit(state, label)
        except Exception as exc:
            logger.error("setRGBState: %s", exc)

    @pyqtSlot()
    def toggleV12(self):
        if not self._hv_connected:
            return
        try:
            if self._v12_state:
                ok = self._interface.console.turn_12v_off()
                if ok:
                    self._v12_state = False
                    self.v12StateChanged.emit()
                    self.powerStatusReceived.emit(False, self._hv_state)
            else:
                ok = self._interface.console.turn_12v_on()
                if ok:
                    self._v12_state = True
                    self.v12StateChanged.emit()
                    self.powerStatusReceived.emit(True, self._hv_state)
        except Exception as exc:
            logger.error("toggleV12: %s", exc)

    @pyqtSlot()
    def softResetHV(self):
        if not self._hv_connected:
            return
        try:
            self._interface.console.soft_reset()
        except Exception as exc:
            logger.error("softResetHV: %s", exc)

    @pyqtSlot()
    def readHvFirmwareVersion(self):
        if not self._hv_connected:
            return
        try:
            ver = self._interface.console.get_version()
            self.fwVersionRead.emit("console", ver)
        except Exception as exc:
            logger.error("readHvFirmwareVersion: %s", exc)

    @pyqtSlot(str)
    def updateConsoleFirmware(self, path: str):
        if not self._hv_connected:
            return
        try:
            self.fwUpdateProgress.emit("Entering DFU mode on console…", 10)
            self._interface.console.enter_dfu()
            self.fwUpdateProgress.emit("DFU mode entered. Flash firmware with DFU tool.", 100)
        except Exception as exc:
            logger.error("updateConsoleFirmware: %s", exc)
            self.fwUpdateProgress.emit(f"Error: {exc}", -1)

    @pyqtSlot(str, int)
    def updateTransmitterFirmware(self, path: str, module_index: int = 0):
        if not self._tx_connected:
            return
        try:
            self.fwUpdateProgress.emit("Entering DFU mode on transmitter…", 10)
            self._interface.transmitter.enter_dfu()
            self.fwUpdateProgress.emit("DFU mode entered. Flash firmware with DFU tool.", 100)
        except Exception as exc:
            logger.error("updateTransmitterFirmware: %s", exc)
            self.fwUpdateProgress.emit(f"Error: {exc}", -1)

    @pyqtSlot(bool)
    def setAsyncMode(self, enabled: bool):
        """Enable or disable the continuous trigger on the transmitter."""
        if not self._tx_connected:
            return
        try:
            if not enabled and self._trigger_enabled:
                self._interface.transmitter.stop_trigger()
                self._trigger_enabled = False
                self.triggerStateChanged.emit(False)
        except Exception as exc:
            logger.error("setAsyncMode: %s", exc)

    # -----------------------------------------------------------------------
    # Demo Slots
    # -----------------------------------------------------------------------

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def generate_plot(self, x: str, y: str, z: str,
                      frequency: str, voltage: str,
                      pulse_interval: str, pulse_count: str,
                      train_interval: str, train_count: str,
                      duration: str, mode: str):
        """Generate the ultrasound plot and emit :attr:`plotGenerated`."""
        if _gen_plot is None:
            logger.error("generate_plot: generate_ultrasound_plot module not found")
            return
        try:
            solution   = self._build_solution_dict(x, y, z, frequency, voltage,
                                                   pulse_interval, pulse_count,
                                                   train_interval, train_count, duration)
            image_data = _gen_plot(solution, mode="buffer")
            if image_data:
                # Strip data-URI prefix if present (QML handler prepends it)
                if image_data.startswith("data:image/png;base64,"):
                    image_data = image_data[len("data:image/png;base64,"):]
                self.plotGenerated.emit(image_data)
        except Exception as exc:
            logger.error("generate_plot: %s", exc)

    @pyqtSlot(str)
    def loadSolutionFromFile(self, path: str):
        """Load a solution JSON file and update solution state."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._loaded_solution  = data
            self._solution_loaded  = True
            self.solutionLoadedChanged.emit()
            self.solutionStateChanged.emit()
            name = data.get("name", os.path.basename(path))
            self.solutionFileLoaded.emit(name, f"Loaded: {path}")
        except Exception as exc:
            logger.error("loadSolutionFromFile: %s", exc)
            self._solution_loaded = False
            self._loaded_solution = None
            self.solutionLoadError.emit(str(exc))

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, str, str, str, result=bool)
    def saveSolutionToFile(self, solution_id: str, name: str, path: str,
                           num_modules: str, x: str, y: str, z: str,
                           frequency: str, voltage: str,
                           pulse_interval: str, pulse_count: str,
                           train_interval: str, train_count: str,
                           duration: str) -> bool:
        """Build a solution dict from UI parameters and write it to *path*."""
        try:
            solution = self._build_solution_dict(x, y, z, frequency, voltage,
                                                  pulse_interval, pulse_count,
                                                  train_interval, train_count, duration)
            solution["id"]          = solution_id
            solution["name"]        = name
            solution["num_modules"] = int(float(num_modules))
            dest = os.path.abspath(path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(solution, fh, indent=2)
            self.solutionSaveStatus.emit(True, f"Saved to {dest}")
            return True
        except Exception as exc:
            logger.error("saveSolutionToFile: %s", exc)
            self.solutionSaveStatus.emit(False, str(exc))
            return False

    @pyqtSlot(result='QVariant')
    def getPresetSolutions(self) -> list:
        """Return ``[{name, path}, ...]`` for every JSON in ``preset_solutions/``."""
        preset_dir = self.getPresetSolutionsPath()
        results: list[dict] = []
        try:
            for p in sorted(glob.glob(os.path.join(preset_dir, "*.json"))):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    results.append({"name": data.get("name", os.path.basename(p)), "path": p})
                except Exception:
                    results.append({"name": os.path.basename(p), "path": p})
        except Exception as exc:
            logger.warning("getPresetSolutions: %s", exc)
        return results

    @pyqtSlot(result=str)
    def getPresetSolutionsPath(self) -> str:
        return str(Path(__file__).parent / "preset_solutions")

    @pyqtSlot(result=str)
    def getDefaultSolutionFilePath(self) -> str:
        return str(Path(__file__).parent / "preset_solutions" / "default_solution.json")

    @pyqtSlot(result='QVariant')
    def getLoadedSolutionSettings(self) -> dict:
        if not self._loaded_solution:
            return self.getDefaultSolutionSettings()
        s   = self._loaded_solution
        seq = s.get("sequence", {})
        pls = s.get("pulse", {})
        pos = s.get("target", {}).get("position", [0, 0, 50])
        return {
            "xInput":        pos[0] if len(pos) > 0 else 0,
            "yInput":        pos[1] if len(pos) > 1 else 0,
            "zInput":        pos[2] if len(pos) > 2 else 50,
            "frequency":     pls.get("frequency", 400_000) / 1_000.0,   # Hz → kHz
            "duration":      pls.get("duration", 200e-6) * 1e6,         # s  → µs
            "voltage":       s.get("voltage", 12.0),
            "pulseInterval": seq.get("pulse_interval", 0.1) * 1_000.0,  # s  → ms
            "pulseCount":    seq.get("pulse_count", 1),
            "trainInterval": seq.get("pulse_train_interval", 0.0),
            "trainCount":    seq.get("pulse_train_count", 1),
            "numModules":    s.get("num_modules", self._manual_num_modules),
        }

    @pyqtSlot(result='QVariant')
    def getDefaultSolutionSettings(self) -> dict:
        default_path = self.getDefaultSolutionFilePath()
        if os.path.exists(default_path):
            try:
                with open(default_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._loaded_solution = data
                self._solution_loaded = True
                return self.getLoadedSolutionSettings()
            except Exception as exc:
                logger.warning("getDefaultSolutionSettings: could not load default: %s", exc)
        return {
            "xInput": 0, "yInput": 0, "zInput": 50,
            "frequency": 400.0, "duration": 200.0, "voltage": 12.0,
            "pulseInterval": 100.0, "pulseCount": 1,
            "trainInterval": 0.0, "trainCount": 1,
            "numModules": self._manual_num_modules,
        }

    @pyqtSlot()
    def makeLoadedSolutionEditable(self):
        """Unset solutionLoaded so UI controls become editable."""
        self._solution_loaded = False
        self.solutionLoadedChanged.emit()
        self.solutionStateChanged.emit()

    @pyqtSlot(int)
    def setManualNumModules(self, n: int):
        self._manual_num_modules = n
        if not self._tx_connected:
            self._num_modules = n

    # -----------------------------------------------------------------------
    # Settings Slots
    # -----------------------------------------------------------------------

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
        except Exception as exc:
            logger.error("loadTestReport: %s", exc)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _build_solution_dict(self, x, y, z, frequency, voltage,
                              pulse_interval, pulse_count,
                              train_interval, train_count, duration) -> dict:
        """Return a minimal solution dict suitable for generate_ultrasound_plot.

        If a solution is already loaded the beam geometry (transducer, delays,
        apodizations) is reused; timing and focus position come from the
        provided parameters.
        """
        freq_hz      = float(frequency) * 1_000.0       # kHz → Hz
        dur_s        = float(duration) / 1e6             # µs  → s
        p_interval_s = float(pulse_interval) / 1_000.0  # ms  → s
        p_count      = int(float(pulse_count))
        t_interval_s = float(train_interval)             # already seconds
        t_count      = int(float(train_count))
        volt         = float(voltage)
        x_mm, y_mm, z_mm = float(x), float(y), float(z)

        base = self._loaded_solution or {}

        # Normalise delays / apodizations to flat 1-D lists for the plot
        raw_delays = base.get("delays", [[0.0]])
        raw_apod   = base.get("apodizations", [[1.0]])
        delays     = raw_delays[0]  if isinstance(raw_delays[0], list) else raw_delays
        apodizations = raw_apod[0] if isinstance(raw_apod[0],   list) else raw_apod

        return {
            "id":           base.get("id", "solution"),
            "name":         base.get("name", "Solution"),
            "delays":       delays,
            "apodizations": apodizations,
            "transducer":   base.get("transducer", {}),
            "voltage":      volt,
            "pulse": {
                "frequency": freq_hz,
                "amplitude": 1.0,
                "duration":  dur_s,
            },
            "sequence": {
                "pulse_interval":       p_interval_s,
                "pulse_count":          p_count,
                "pulse_train_interval": t_interval_s,
                "pulse_train_count":    t_count,
            },
            "target": {
                "position": [x_mm, y_mm, z_mm],
                "units":    "mm",
            },
        }
