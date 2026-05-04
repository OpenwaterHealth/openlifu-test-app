"""
lifu_support.py
---------------
Backend connector for the Support page (pages/Support.qml).

Keeps support-specific logic out of the monolithic lifu_connector.py.
lifu_connector.py re-exports this class as a thin shim so that QML
context registration in main.py stays in one place.
"""

import json
import logging
import platform
import sys

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty

logger = logging.getLogger(__name__)


class LIFUSupportConnector(QObject):
    """QObject backend for the Support page.

    Accepts an optional reference to the shared ``LIFUInterface`` so that
    support operations can communicate with hardware when needed.
    """

    # ------------------------------------------------------------------ #
    # Signals                                                              #
    # ------------------------------------------------------------------ #

    # Emitted with (success: bool, message: str) after any support action.
    supportActionResult = pyqtSignal(bool, str)

    # Emitted when diagnostic information has been collected.
    diagnosticsReady = pyqtSignal(str)  # JSON-encoded dict

    def __init__(self, interface=None, parent=None):
        super().__init__(parent)
        self._interface = interface  # shared LIFUInterface; may be None

    # ------------------------------------------------------------------ #
    # Properties                                                           #
    # ------------------------------------------------------------------ #

    @pyqtProperty(bool)
    def hasHardware(self) -> bool:
        """True when a LIFUInterface was provided at construction time."""
        return self._interface is not None

    # ------------------------------------------------------------------ #
    # Slots (callable from QML)                                           #
    # ------------------------------------------------------------------ #

    @pyqtSlot(result=str)
    def collectDiagnostics(self) -> str:
        """Gather diagnostic info and return it as a formatted JSON string.

        Includes Python/platform versions, SDK version, and (where available)
        connected device firmware/ID information.  Also emits
        ``diagnosticsReady`` so QML can react asynchronously.
        """
        info: dict = {
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        }

        if self._interface is not None:
            try:
                info["sdk_version"] = self._interface.get_sdk_version()
            except Exception as exc:
                info["sdk_version_error"] = str(exc)

            # Console / HV device
            hv_info: dict = {}
            try:
                hv_info["firmware_version"] = self._interface.hvcontroller.get_version()
            except Exception as exc:
                hv_info["firmware_version_error"] = str(exc)
            try:
                hv_info["hardware_id"] = self._interface.hvcontroller.get_hardware_id(raw_hex=True)
            except Exception as exc:
                hv_info["hardware_id_error"] = str(exc)
            if hv_info:
                info["console"] = hv_info

            # TX modules
            try:
                module_count = self._interface.txdevice.get_module_count()
                modules = []
                for i in range(module_count):
                    m: dict = {"module": i}
                    try:
                        m["firmware_version"] = self._interface.txdevice.get_version(module=i)
                    except Exception as exc:
                        m["firmware_version_error"] = str(exc)
                    try:
                        m["hardware_id"] = self._interface.txdevice.get_hardware_id(module=i, raw_hex=True)
                    except Exception as exc:
                        m["hardware_id_error"] = str(exc)
                    modules.append(m)
                info["transmitter"] = {"module_count": module_count, "modules": modules}
            except Exception as exc:
                info["transmitter_error"] = str(exc)

        result = json.dumps(info, indent=2)
        self.diagnosticsReady.emit(result)
        return result

    @pyqtSlot(str, result=bool)
    def sendSupportLog(self, destination: str) -> bool:
        """Placeholder: send the application log to *destination*.

        Returns True on success. Replace with real implementation as needed.
        """
        logger.info("sendSupportLog called with destination=%s", destination)
        self.supportActionResult.emit(True, f"Log sent to {destination}")
        return True

