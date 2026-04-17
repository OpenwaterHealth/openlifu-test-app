from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSlot

from lifu_connector_constants import RGB_STATE_NAMES

logger = logging.getLogger(__name__)


class HvSlotsMixin:
    """Qt slots for the Console (HV) device."""

    # -----------------------------------------------------------------------
    # Query / telemetry
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

    # -----------------------------------------------------------------------
    # HV power control
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # 12V control
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Fan control
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # RGB LED
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Device management
    # -----------------------------------------------------------------------

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
            self.fwUpdateStatus.emit("console", True, "DFU mode entered. Flash firmware with DFU tool.")
        except Exception as exc:
            logger.error("updateConsoleFirmware: %s", exc)
            self.fwUpdateProgress.emit(f"Error: {exc}", -1)
            self.fwUpdateStatus.emit("console", False, str(exc))
