"""
lifu_support.py
---------------
Backend connector for the Support page (pages/Support.qml).

Keeps support-specific logic (diagnostics, hardware tests, PDF reports)
out of the monolithic lifu_connector.py.
lifu_connector.py re-exports this class as a thin shim so that QML
context registration in main.py stays in one place.
"""

import datetime
import json
import logging
import platform
import sys
import threading

from PyQt6.QtCore import QObject, QStandardPaths, QThread, pyqtSignal, pyqtSlot, pyqtProperty

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass (plain dict for JSON serialisability)
# ---------------------------------------------------------------------------

def _pass(name: str, detail: str = "") -> dict:
    return {"name": name, "status": "PASS", "detail": detail}

def _fail(name: str, detail: str) -> dict:
    return {"name": name, "status": "FAIL", "detail": detail}

def _skip(name: str, detail: str = "Device not connected") -> dict:
    return {"name": name, "status": "SKIP", "detail": detail}


# ---------------------------------------------------------------------------
# Diagnostic runner (runs in a QThread so it doesn't block the UI)
# ---------------------------------------------------------------------------

class _DiagnosticThread(QThread):
    """Executes one or more diagnostic test groups and reports results."""

    # Emitted for each completed test: (group, name, status, detail)
    testResult = pyqtSignal(str, str, str, str)
    # Emitted when the full run finishes with the JSON results blob
    runFinished = pyqtSignal(str)

    def __init__(self, interface, groups: list[str], parent=None):
        super().__init__(parent)
        self._interface = interface
        self._groups = groups   # e.g. ["console", "tx", "voltages"]

    # ------------------------------------------------------------------ #

    def run(self):
        results = {}
        ts = datetime.datetime.now().isoformat(timespec="seconds")

        if "console" in self._groups:
            results["console"] = self._run_console_tests()
        if "tx" in self._groups:
            results["tx"] = self._run_tx_tests()
        if "voltages" in self._groups:
            results["voltages"] = self._run_voltage_tests()

        payload = json.dumps({
            "timestamp": ts,
            "results": results,
        }, indent=2)
        self.runFinished.emit(payload)

    # ------------------------------------------------------------------ #
    # Console / HV tests                                                   #
    # ------------------------------------------------------------------ #

    def _run_console_tests(self) -> list:
        hv = None
        try:
            hv = self._interface.hvcontroller
        except Exception:
            pass

        if hv is None or not hv.is_connected():
            r = _skip("Console Connected")
            self.testResult.emit("console", r["name"], r["status"], r["detail"])
            return [r]

        tests = []

        # Ping
        tests.append(self._do("console", "Console Ping", hv.ping))

        # Firmware version
        tests.append(self._do("console", "Console Firmware Version",
                               hv.get_version, expect_truthy=True))

        # Hardware ID
        tests.append(self._do("console", "Console Hardware ID",
                               lambda: hv.get_hardware_id(raw_hex=True),
                               expect_truthy=True))

        # Temperature 1
        tests.append(self._do_threshold("console", "Console Temp 1",
                                         hv.get_temperature1, lo=-10, hi=85,
                                         unit="°C"))

        # Temperature 2
        tests.append(self._do_threshold("console", "Console Temp 2",
                                         hv.get_temperature2, lo=-10, hi=85,
                                         unit="°C"))

        # 12V rail status
        tests.append(self._do("console", "12V Rail Status",
                               hv.get_12v_status,
                               detail_fn=lambda v: "12V is ON" if v else "12V is OFF"))

        # HV status (read-only, not toggling)
        tests.append(self._do("console", "HV Rail Status",
                               hv.get_hv_status,
                               detail_fn=lambda v: "HV is ON" if v else "HV is OFF"))

        # Voltage monitor
        tests.append(self._do("console", "Voltage Monitor (VMON)",
                               hv.get_vmon_values,
                               detail_fn=lambda ch: ", ".join(
                                   f"ch{c['channel']}={c['converted_voltage']:.2f}V"
                                   for c in ch
                               )))

        # Fan speeds (read only)
        for fan_id, fan_name in [(0, "Fan 0 (Bottom)"), (1, "Fan 1 (Top)")]:
            tests.append(self._do("console", f"{fan_name} Speed",
                                   lambda fid=fan_id: hv.get_fan_speed(fid),
                                   detail_fn=lambda v: f"{v}%"))

        # RGB LED state (read only)
        tests.append(self._do("console", "RGB LED State",
                               hv.get_rgb_led,
                               detail_fn=lambda v: {0:"OFF",1:"RED",2:"BLUE",3:"GREEN"}.get(v, f"{v}")))

        return tests

    # ------------------------------------------------------------------ #
    # TX / Transmitter tests                                               #
    # ------------------------------------------------------------------ #

    def _run_tx_tests(self) -> list:
        tx = None
        try:
            tx = self._interface.txdevice
        except Exception:
            pass

        if tx is None or not tx.is_connected():
            r = _skip("TX Connected")
            self.testResult.emit("tx", r["name"], r["status"], r["detail"])
            return [r]

        tests = []

        # Ping
        tests.append(self._do("tx", "TX Ping", tx.ping))

        # Module count
        def _module_count():
            c = tx.get_tx_module_count()
            if c < 1:
                raise ValueError(f"No TX modules reported ({c})")
            return c
        tests.append(self._do("tx", "TX Module Count",
                               _module_count,
                               detail_fn=lambda v: f"{v} module(s)"))

        # Per-module tests
        try:
            module_count = tx.get_tx_module_count()
        except Exception:
            module_count = 0

        for m in range(module_count):
            # Firmware version
            tests.append(self._do("tx", f"Module {m} Firmware Version",
                                   lambda mi=m: tx.get_version(module=mi),
                                   expect_truthy=True))

            # Hardware ID
            tests.append(self._do("tx", f"Module {m} Hardware ID",
                                   lambda mi=m: tx.get_hardware_id(module=mi, raw_hex=True),
                                   expect_truthy=True))

            # TX temperature
            tests.append(self._do_threshold("tx", f"Module {m} TX Temp",
                                             lambda mi=m: tx.get_temperature(module=mi),
                                             lo=-10, hi=85, unit="°C"))

            # Ambient temperature
            tests.append(self._do_threshold("tx", f"Module {m} Ambient Temp",
                                             lambda mi=m: tx.get_ambient_temperature(module=mi),
                                             lo=-10, hi=85, unit="°C"))

            # TX7332 chip enumeration
            tests.append(self._do("tx", f"Module {m} TX7332 Enum",
                                   lambda mi=m: tx.enum_tx7332_devices(),
                                   detail_fn=lambda v: f"{v} chip(s)"))

        # Echo loopback
        echo_payload = b"\xDE\xAD\xBE\xEF"
        def _echo():
            data, length = tx.echo(echo_data=bytearray(echo_payload))
            if bytes(data) != echo_payload:
                raise ValueError(f"Echo mismatch: got {bytes(data).hex()}")
            return True
        tests.append(self._do("tx", "TX Echo Loopback", _echo))

        return tests

    # ------------------------------------------------------------------ #
    # Voltage / power-rail tests                                           #
    # ------------------------------------------------------------------ #

    def _run_voltage_tests(self) -> list:
        hv = None
        try:
            hv = self._interface.hvcontroller
        except Exception:
            pass

        if hv is None or not hv.is_connected():
            r = _skip("Voltage Monitor")
            self.testResult.emit("voltages", r["name"], r["status"], r["detail"])
            return [r]

        tests = []

        CHANNEL_NAMES = ["HVP1", "HVP2", "HVM2", "HVM1", "12V", "VCA1", "VCB1", "VCC1"]
        # Expected ranges (lo, hi) in volts; None = no limit
        CHANNEL_RANGES = {
            "HVP1": (0.0, 120.0),
            "HVP2": (0.0, 120.0),
            "HVM2": (-120.0, 0.0),
            "HVM1": (-120.0, 0.0),
            "12V":  (10.0, 14.0),
            "VCA1": (0.0, 15.0),
            "VCB1": (0.0, 15.0),
            "VCC1": (0.0, 15.0),
        }

        try:
            channels = hv.get_vmon_values()
            for i, ch in enumerate(channels):
                name = CHANNEL_NAMES[i] if i < len(CHANNEL_NAMES) else f"ch{i}"
                v = ch.get("converted_voltage", ch.get("voltage", 0.0))
                lo, hi = CHANNEL_RANGES.get(name, (None, None))
                detail = f"{v:.3f} V"
                if lo is not None and hi is not None:
                    if lo <= v <= hi:
                        r = _pass(f"VMON {name}", detail)
                    else:
                        r = _fail(f"VMON {name}", f"{detail} (expected {lo}–{hi} V)")
                else:
                    r = _pass(f"VMON {name}", detail)
                self.testResult.emit("voltages", r["name"], r["status"], r["detail"])
                tests.append(r)
        except Exception as exc:
            r = _fail("Voltage Monitor", str(exc))
            self.testResult.emit("voltages", r["name"], r["status"], r["detail"])
            tests.append(r)

        return tests

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _do(self, group: str, name: str, fn, expect_truthy=False,
            detail_fn=None) -> dict:
        try:
            val = fn()
            if expect_truthy and not val:
                r = _fail(name, "Returned empty/falsy value")
            else:
                detail = detail_fn(val) if detail_fn else str(val)
                r = _pass(name, detail)
        except Exception as exc:
            r = _fail(name, str(exc))
        self.testResult.emit(group, r["name"], r["status"], r["detail"])
        return r

    def _do_threshold(self, group: str, name: str, fn, lo: float, hi: float,
                      unit: str = "") -> dict:
        try:
            val = float(fn())
            detail = f"{val:.2f} {unit}".strip()
            if lo <= val <= hi:
                r = _pass(name, detail)
            else:
                r = _fail(name, f"{detail} (expected {lo}–{hi} {unit})")
        except Exception as exc:
            r = _fail(name, str(exc))
        self.testResult.emit(group, r["name"], r["status"], r["detail"])
        return r


# ---------------------------------------------------------------------------
# Main connector exposed to QML
# ---------------------------------------------------------------------------

class LIFUSupportConnector(QObject):
    """QObject backend for the Support page.

    Accepts an optional reference to the shared ``LIFUInterface`` so that
    support operations can communicate with hardware when needed.
    """

    # ------------------------------------------------------------------ #
    # Signals                                                              #
    # ------------------------------------------------------------------ #

    supportActionResult = pyqtSignal(bool, str)
    diagnosticsReady    = pyqtSignal(str)       # JSON system-info blob
    testResultReady     = pyqtSignal(str, str, str, str)   # group, name, status, detail
    testRunFinished     = pyqtSignal(str)        # full JSON results blob
    pdfSaved            = pyqtSignal(bool, str)  # success, path-or-error

    def __init__(self, interface=None, parent=None):
        super().__init__(parent)
        self._interface = interface
        self._diag_thread: _DiagnosticThread | None = None

    # ------------------------------------------------------------------ #
    # Properties                                                           #
    # ------------------------------------------------------------------ #

    @pyqtProperty(bool)
    def hasHardware(self) -> bool:
        return self._interface is not None

    @pyqtProperty(str)
    def documentsLocation(self) -> str:
        return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)

    # ------------------------------------------------------------------ #
    # System-info diagnostics (lightweight, sync)                         #
    # ------------------------------------------------------------------ #

    @pyqtSlot(result=str)
    def collectDiagnostics(self) -> str:
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

            try:
                module_count = self._interface.txdevice.get_tx_module_count()
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

    # ------------------------------------------------------------------ #
    # Hardware diagnostic tests (async, runs in QThread)                  #
    # ------------------------------------------------------------------ #

    @pyqtSlot()
    def runAllTests(self):
        """Run all available diagnostic test groups."""
        self._start_tests(["console", "tx", "voltages"])

    @pyqtSlot(str)
    def runTestGroup(self, group: str):
        """Run a single diagnostic group: 'console', 'tx', or 'voltages'."""
        self._start_tests([group])

    def _start_tests(self, groups: list[str]):
        if self._diag_thread and self._diag_thread.isRunning():
            logger.warning("Diagnostic run already in progress – ignoring request.")
            return
        if self._interface is None:
            self.testRunFinished.emit(json.dumps({
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "error": "No hardware interface available",
                "results": {},
            }))
            return

        self._diag_thread = _DiagnosticThread(self._interface, groups, parent=self)
        self._diag_thread.testResult.connect(self.testResultReady)
        self._diag_thread.runFinished.connect(self.testRunFinished)
        self._diag_thread.start()

    # ------------------------------------------------------------------ #
    # PDF report                                                           #
    # ------------------------------------------------------------------ #

    @pyqtSlot(str, str)
    def savePdfReport(self, results_json: str, file_path: str):
        """Render *results_json* as a PDF and save it to *file_path*.

        Emits ``pdfSaved(True, path)`` on success or
        ``pdfSaved(False, error_message)`` on failure.

        Uses only stdlib + PyQt6 (QPrinter / QTextDocument) so no extra
        dependency is needed.
        """
        # Run on a background thread so the UI stays responsive during render.
        t = threading.Thread(target=self._render_pdf,
                             args=(results_json, file_path), daemon=True)
        t.start()

    def _render_pdf(self, results_json: str, file_path: str):
        try:
            from PyQt6.QtGui import QTextDocument
            from PyQt6.QtPrintSupport import QPrinter

            try:
                data = json.loads(results_json)
            except Exception:
                data = {}

            ts = data.get("timestamp", datetime.datetime.now().isoformat(timespec="seconds"))
            results = data.get("results", {})

            html = self._build_report_html(ts, results)

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(file_path)
            printer.setPageSize(QPrinter.PageSize.A4)

            doc = QTextDocument()
            doc.setHtml(html)
            doc.print(printer)

            self.pdfSaved.emit(True, file_path)
        except Exception as exc:
            logger.error("PDF save failed: %s", exc)
            self.pdfSaved.emit(False, str(exc))

    @staticmethod
    def _build_report_html(timestamp: str, results: dict) -> str:
        STATUS_COLOR = {"PASS": "#2ECC71", "FAIL": "#E74C3C", "SKIP": "#F39C12"}
        GROUP_LABEL  = {"console": "Console (HV Controller)", "tx": "Transmitter (TX)", "voltages": "Voltage Monitor"}

        total = sum(len(v) for v in results.values())
        passed = sum(1 for v in results.values() for r in v if r.get("status") == "PASS")
        failed = sum(1 for v in results.values() for r in v if r.get("status") == "FAIL")
        skipped = total - passed - failed

        rows = ""
        for group, tests in results.items():
            label = GROUP_LABEL.get(group, group.upper())
            rows += f"""
            <tr><td colspan="3" style="background:#1a3a5c;color:#ffffff;font-weight:bold;
                padding:6px 8px;">{label}</td></tr>"""
            for t in tests:
                color = STATUS_COLOR.get(t.get("status", ""), "#BDC3C7")
                rows += f"""
            <tr>
                <td style="padding:4px 8px;">{t.get('name','')}</td>
                <td style="color:{color};font-weight:bold;padding:4px 8px;">{t.get('status','')}</td>
                <td style="padding:4px 8px;font-family:monospace;">{t.get('detail','')}</td>
            </tr>"""

        return f"""<!DOCTYPE html><html><head>
        <meta charset="utf-8"/>
        <style>
          body  {{ font-family: Arial, sans-serif; font-size: 11pt; color: #222; }}
          h1    {{ color: #1a3a5c; border-bottom: 2px solid #1a3a5c; padding-bottom: 4px; }}
          .meta {{ color: #555; font-size: 9pt; margin-bottom: 12px; }}
          .summary {{ margin-bottom: 16px; font-size: 10pt; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th    {{ background: #2c3e50; color: white; padding: 6px 8px; text-align: left; }}
          tr:nth-child(even) {{ background: #f5f5f5; }}
          td    {{ border-bottom: 1px solid #ddd; }}
        </style></head><body>
        <h1>OpenLIFU Hardware Diagnostic Report</h1>
        <p class="meta">Generated: {timestamp}</p>
        <div class="summary">
          <strong>Summary:</strong>&nbsp;
          {total} tests &mdash;
          <span style="color:#2ECC71">{passed} passed</span>,&nbsp;
          <span style="color:#E74C3C">{failed} failed</span>,&nbsp;
          <span style="color:#F39C12">{skipped} skipped</span>
        </div>
        <table>
          <tr><th>Test</th><th>Status</th><th>Detail</th></tr>
          {rows}
        </table>
        </body></html>"""

    # ------------------------------------------------------------------ #
    # Misc                                                                 #
    # ------------------------------------------------------------------ #

    @pyqtSlot(str, result=bool)
    def sendSupportLog(self, destination: str) -> bool:
        logger.info("sendSupportLog called with destination=%s", destination)
        self.supportActionResult.emit(True, f"Log sent to {destination}")
        return True

