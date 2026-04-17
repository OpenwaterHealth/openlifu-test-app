from __future__ import annotations

import json
import logging

from PyQt6.QtCore import pyqtSlot

from lifu_connector_constants import (
    STATE_DISCONNECTED,
    STATE_TX_CONNECTED,
    STATE_CONFIGURED,
    STATE_READY,
    STATE_RUNNING,
)

logger = logging.getLogger(__name__)


class TxSlotsMixin:
    """Qt slots for the TX transmitter device."""

    # -----------------------------------------------------------------------
    # Query / telemetry
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

    # -----------------------------------------------------------------------
    # Trigger control
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Sonication lifecycle
    # -----------------------------------------------------------------------

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
    # Device management
    # -----------------------------------------------------------------------

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
