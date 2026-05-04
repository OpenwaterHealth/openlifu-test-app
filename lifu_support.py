"""
lifu_support.py
---------------
Backend connector for the Support page (pages/Support.qml).

Keeps support-specific logic out of the monolithic lifu_connector.py.
lifu_connector.py re-exports this class as a thin shim so that QML
context registration in main.py stays in one place.
"""

import logging

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
        """Gather basic diagnostic info and return it as a JSON string.

        Also emits ``diagnosticsReady`` so QML can react asynchronously.
        """
        import json
        import sys

        info: dict = {
            "python_version": sys.version,
        }

        if self._interface is not None:
            try:
                info["sdk_version"] = self._interface.get_sdk_version()
            except Exception as exc:
                info["sdk_version_error"] = str(exc)

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
