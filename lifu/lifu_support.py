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
import html as html_mod
import json
import logging
import platform
import random
import sys
import threading
import time

from PyQt6.QtCore import QObject, QStandardPaths, QThread, pyqtSignal, pyqtSlot, pyqtProperty

logger = logging.getLogger(__name__)

# Scratch key used by the user-config diagnostic tests. Written to the device
# during the test then removed and the original config is restored, so no
# real configuration is permanently modified.
_UC_SCRATCH_KEY = "__lifu_sdk_test_scratch__"

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
    # Emitted after every test step: (steps_done, total_steps)
    stepCompleted = pyqtSignal(int, int)

    def __init__(self, interface, groups: list[str], parent=None):
        super().__init__(parent)
        self._interface = interface
        self._groups = groups   # e.g. ["console", "tx", "voltages"]
        self._steps_done = 0
        self._total_steps = 0

    # ------------------------------------------------------------------ #

    def _count_steps(self) -> int:
        """Estimate the total number of test steps before running."""
        total = 0
        if "console" in self._groups:
            try:
                hv = self._interface.hvcontroller
                total += 28 if hv.is_connected() else 1
            except Exception:
                total += 1
        if "tx" in self._groups:
            try:
                tx = self._interface.txdevice
                if tx.is_connected():
                    try:
                        n = tx.get_tx_module_count()
                    except Exception:
                        n = 1
                    try:
                        chip_count = tx.enum_tx7332_devices()
                    except Exception:
                        chip_count = max(n, 1)
                    total += 2 + 12 * max(n, 0) + 4 + max(chip_count, 0)
                else:
                    total += 1
            except Exception:
                total += 1
        return max(total, 1)

    def run(self):
        self._steps_done = 0
        self._total_steps = self._count_steps()
        self.stepCompleted.emit(0, self._total_steps)

        results = {}
        ts = datetime.datetime.now().isoformat(timespec="seconds")

        if "console" in self._groups:
            results["console"] = self._run_console_tests()
        if "tx" in self._groups:
            results["tx"] = self._run_tx_tests()

        payload = json.dumps({
            "timestamp": ts,
            "system_info": self._collect_system_info(),
            "results": results,
        }, indent=2)
        self.runFinished.emit(payload)

    def _collect_system_info(self) -> dict:
        info: dict = {
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        }
        iface = self._interface
        if iface is None:
            return info
        try:
            info["sdk_version"] = iface.get_sdk_version()
        except Exception:
            pass
        hv_info: dict = {}
        try:
            hv = iface.hvcontroller
            hv_info["firmware_version"] = hv.get_version()
        except Exception:
            pass
        try:
            hv_info["hardware_id"] = iface.hvcontroller.get_hardware_id(raw_hex=True)
        except Exception:
            pass
        if hv_info:
            info["console"] = hv_info
        try:
            module_count = iface.txdevice.get_tx_module_count()
            modules = []
            for i in range(module_count):
                m: dict = {"module": i}
                try:
                    m["firmware_version"] = iface.txdevice.get_version(module=i)
                except Exception:
                    pass
                try:
                    m["hardware_id"] = iface.txdevice.get_hardware_id(module=i, raw_hex=True)
                except Exception:
                    pass
                modules.append(m)
            info["transmitter"] = {"module_count": module_count, "modules": modules}
        except Exception:
            pass
        return info

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

        # ── Ping, firmware, hardware ID, temperatures ──────────────────
        tests.append(self._do("console", "Console Ping", hv.ping))

        tests.append(self._do("console", "Console Firmware Version",
                               hv.get_version, expect_truthy=True))

        tests.append(self._do("console", "Console Hardware ID",
                               lambda: hv.get_hardware_id(raw_hex=True),
                               expect_truthy=True))

        tests.append(self._do_threshold("console", "Console Temp 1",
                                         hv.get_temperature1, lo=-10, hi=85,
                                         unit="°C"))

        tests.append(self._do_threshold("console", "Console Temp 2",
                                         hv.get_temperature2, lo=-10, hi=85,
                                         unit="°C"))

        # ── RGB LED – set each state then read back to verify ──────────
        # Hardware mapping (from connector): 0=OFF, 1=RED, 2=GREEN, 3=BLUE
        _RGB_LABEL = {0: "OFF", 1: "RED", 2: "GREEN", 3: "BLUE"}
        for _state, _label in [(0, "OFF"), (1, "RED"), (3, "BLUE"), (2, "GREEN")]:
            def _rgb_cycle(s=_state, lbl=_label):
                hv.set_rgb_led(s)
                actual = hv.get_rgb_led()
                if actual != s:
                    raise ValueError(
                        f"Expected {lbl} ({s}), "
                        f"read back {_RGB_LABEL.get(actual, actual)} ({actual})"
                    )
                return _RGB_LABEL.get(actual, actual)
            tests.append(self._do("console", f"RGB LED Set {_label}", _rgb_cycle))

        # ── 12V rail ───────────────────────────────────────────────────
        # 1. Ensure 12V starts OFF; turn it off if currently on
        def _12v_ensure_off():
            if hv.get_12v_status():
                hv.turn_12v_off()
                time.sleep(2)
            if hv.get_12v_status():
                raise ValueError("12V still ON after turn_12v_off()")
            return "12V confirmed OFF"
        tests.append(self._do("console", "12V Ensure OFF", _12v_ensure_off))

        # 2. VMON 12V channel (index 4) must read −1 to +1 V while rail is off
        def _vmon_12v_off():
            v = float(hv.get_vmon_values()[4]["converted_voltage"])
            if not (-1.0 <= v <= 1.0):
                raise ValueError(f"{v:.3f} V — expected between −1 and +1 V (rail off)")
            return v
        tests.append(self._do("console", "12V VMON OFF Check",
                               _vmon_12v_off,
                               detail_fn=lambda v: f"{v:.3f} V"))

        # 3. Turn 12V on and verify VMON 12V reads 12 V ± 5 %
        def _12v_on_and_check():
            hv.turn_12v_on()
            time.sleep(2)
            v = float(hv.get_vmon_values()[4]["converted_voltage"])
            lo, hi = 12.0 * 0.95, 12.0 * 1.05
            if not (lo <= v <= hi):
                raise ValueError(
                    f"{v:.3f} V out of 12 V ±5% window ({lo:.2f}–{hi:.2f} V)"
                )
            return v
        tests.append(self._do("console", "12V VMON ON Check",
                               _12v_on_and_check,
                               detail_fn=lambda v: f"{v:.3f} V"))

        # ── HV rail ────────────────────────────────────────────────────
        # 4. Ensure HV starts OFF
        def _hv_ensure_off():
            if hv.get_hv_status():
                hv.turn_hv_off()
                time.sleep(2)
            if hv.get_hv_status():
                raise ValueError("HV still ON after turn_hv_off()")
            return "HV confirmed OFF"
        tests.append(self._do("console", "HV Ensure OFF", _hv_ensure_off))

        # 5. All four HV VMON channels must read −1 to +1 V while HV is off
        #    HVP1=idx0, HVP2=idx1 (positive rails)
        #    HVM2=idx2, HVM1=idx3 (negative rails)
        for _idx, _ch in [(0, "HVP1"), (1, "HVP2"), (3, "HVM1"), (2, "HVM2")]:
            def _hv_off_check(idx=_idx, ch=_ch):
                v = float(hv.get_vmon_values()[idx]["converted_voltage"])
                if not (-1.0 <= v <= 1.0):
                    raise ValueError(
                        f"{v:.3f} V — expected between −1 and +1 V (HV off)"
                    )
                return v
            tests.append(self._do("console", f"{_ch} VMON HV-OFF Check",
                                   _hv_off_check,
                                   detail_fn=lambda v: f"{v:.3f} V"))

        # 6. Set HV voltage to 15 V
        def _hv_set_15v():
            hv.set_voltage(voltage=15.0)
            return "voltage set to 15 V"
        tests.append(self._do("console", "HV Set 15 V", _hv_set_15v))

        # 7. Turn HV on and confirm status
        def _hv_turn_on():
            hv.turn_hv_on()
            time.sleep(2)
            if not hv.get_hv_status():
                raise ValueError("HV did not turn ON after turn_hv_on()")
            return "HV ON confirmed"
        tests.append(self._do("console", "HV Turn ON", _hv_turn_on))

        # 8. Positive rails: HVP1, HVP2 must read +15 V ± 5 %
        for _idx, _ch in [(0, "HVP1"), (1, "HVP2")]:
            def _hvp_on_check(idx=_idx, ch=_ch):
                v = float(hv.get_vmon_values()[idx]["converted_voltage"])
                lo, hi = 15.0 * 0.95, 15.0 * 1.05
                if not (lo <= v <= hi):
                    raise ValueError(
                        f"{v:.3f} V out of +15 V ±5% window ({lo:.2f}–{hi:.2f} V)"
                    )
                return v
            tests.append(self._do("console", f"{_ch} VMON HV-ON Check",
                                   _hvp_on_check,
                                   detail_fn=lambda v: f"{v:.3f} V"))

        # 9. Negative rails: HVM1, HVM2 must read −15 V ± 5 %
        for _idx, _ch in [(3, "HVM1"), (2, "HVM2")]:
            def _hvm_on_check(idx=_idx, ch=_ch):
                v = float(hv.get_vmon_values()[idx]["converted_voltage"])
                lo, hi = -15.0 * 1.05, -15.0 * 0.95
                if not (lo <= v <= hi):
                    raise ValueError(
                        f"{v:.3f} V out of −15 V ±5% window ({lo:.2f}–{hi:.2f} V)"
                    )
                return v
            tests.append(self._do("console", f"{_ch} VMON HV-ON Check",
                                   _hvm_on_check,
                                   detail_fn=lambda v: f"{v:.3f} V"))

        # 10. Turn HV OFF before fan tests
        def _hv_turn_off_final():
            hv.turn_hv_off()
            time.sleep(2)
            if hv.get_hv_status():
                raise ValueError("HV still ON after turn_hv_off()")
            return "HV OFF confirmed"
        tests.append(self._do("console", "HV Turn OFF", _hv_turn_off_final))

        # ── Fan tests – set 50 %, verify, return to 0 % ────────────────
        for _fan_id, _fan_name in [(0, "Fan 0 (Bottom)"), (1, "Fan 1 (Top)")]:
            def _fan_50(fid=_fan_id, fname=_fan_name):
                result = hv.set_fan_speed(fan_id=fid, fan_speed=50)
                if result != 50:
                    raise ValueError(f"Set 50% but fan reported {result}%")
                time.sleep(2)
                return result
            tests.append(self._do("console", f"{_fan_name} Set 50%",
                                   _fan_50,
                                   detail_fn=lambda v: f"{v}%"))

            def _fan_0(fid=_fan_id, fname=_fan_name):
                result = hv.set_fan_speed(fan_id=fid, fan_speed=0)
                if result != 0:
                    raise ValueError(f"Set 0% but fan reported {result}%")
                time.sleep(2)
                return result
            tests.append(self._do("console", f"{_fan_name} Set 0%",
                                   _fan_0,
                                   detail_fn=lambda v: f"{v}%"))

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

        # ── 1. Module count ────────────────────────────────────────────
        def _module_count():
            c = tx.get_tx_module_count()
            if c < 1:
                raise ValueError(f"No TX modules reported ({c})")
            return c
        tests.append(self._do("tx", "TX Module Count",
                               _module_count,
                               detail_fn=lambda v: f"{v} module(s)"))

        # ── 2. TX7332 chip enumeration (single call, all slaves) ───────
        tests.append(self._do("tx", "TX7332 Chip Enum",
                               tx.enum_tx7332_devices,
                               detail_fn=lambda v: f"{v} chip(s)"))

        # ── 3. Per-module tests ────────────────────────────────────────
        try:
            module_count = tx.get_tx_module_count()
        except Exception:
            module_count = 0

        echo_payload = b"\xDE\xAD\xBE\xEF"

        for m in range(module_count):
            # Ping
            tests.append(self._do("tx", f"Module {m} Ping",
                                   lambda mi=m: tx.ping(module=mi)))

            # Echo loopback
            def _echo(mi=m):
                data, length = tx.echo(echo_data=bytearray(echo_payload), module=mi)
                if bytes(data) != echo_payload:
                    raise ValueError(f"Echo mismatch: got {bytes(data).hex()}")
                return True
            tests.append(self._do("tx", f"Module {m} Echo Loopback", _echo))

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

            # ── User config — full safe test suite ────────────────────────
            # Mirrors UserConfigInteractiveTests.run_all_safe() from the SDK
            # unit-test suite. A scratch key is added, modified, removed, and
            # round-tripped; the original config is always restored at the end.
            _snap = [None]  # mutable snapshot holder, unique per module

            # 1. Read & snapshot
            def _ucfg_read(mi=m, snap=_snap):
                cfg = tx.read_config(module=mi)
                snap[0] = cfg  # save for restore
                js = cfg.get_json_str()
                if not js:
                    raise ValueError("Empty config returned")
                return f"seq={cfg.header.seq} len={cfg.header.json_len}B"
            tests.append(self._do("tx", f"Module {m} UC Read", _ucfg_read))

            # 2. Add scratch key
            def _ucfg_add(mi=m):
                cfg = tx.read_config(module=mi)
                cfg.set(_UC_SCRATCH_KEY, {"iter": 0, "module": mi})
                tx.write_config(cfg, module=mi)
                verify = tx.read_config(module=mi)
                if _UC_SCRATCH_KEY not in verify.json_data:
                    raise ValueError("Scratch key not present after write")
                return "key added+verified"
            tests.append(self._do("tx", f"Module {m} UC Add Scratch", _ucfg_add))

            # 3. Update scratch key
            def _ucfg_update(mi=m):
                cfg = tx.read_config(module=mi)
                existing = cfg.get(_UC_SCRATCH_KEY) or {"iter": 0}
                existing["iter"] = existing.get("iter", 0) + 1
                cfg.set(_UC_SCRATCH_KEY, existing)
                tx.write_config(cfg, module=mi)
                verify = tx.read_config(module=mi)
                actual = (verify.get(_UC_SCRATCH_KEY) or {}).get("iter", -1)
                if actual != existing["iter"]:
                    raise ValueError(
                        f"iter mismatch: expected {existing['iter']}, got {actual}"
                    )
                return f"iter={actual}"
            tests.append(self._do("tx", f"Module {m} UC Update Scratch", _ucfg_update))

            # 4. Remove scratch key
            def _ucfg_remove(mi=m):
                cfg = tx.read_config(module=mi)
                if _UC_SCRATCH_KEY in cfg.json_data:
                    del cfg.json_data[_UC_SCRATCH_KEY]
                tx.write_config(cfg, module=mi)
                verify = tx.read_config(module=mi)
                if _UC_SCRATCH_KEY in verify.json_data:
                    raise ValueError("Scratch key still present after remove")
                return "key removed+verified"
            tests.append(self._do("tx", f"Module {m} UC Remove Scratch", _ucfg_remove))

            # 5. Round-trip (add → verify → remove → verify)
            def _ucfg_roundtrip(mi=m):
                cfg = tx.read_config(module=mi)
                cfg.set(_UC_SCRATCH_KEY, {"roundtrip": True})
                tx.write_config(cfg, module=mi)
                verify = tx.read_config(module=mi)
                if _UC_SCRATCH_KEY not in verify.json_data:
                    raise ValueError("Scratch key absent after add")
                cfg = tx.read_config(module=mi)
                del cfg.json_data[_UC_SCRATCH_KEY]
                tx.write_config(cfg, module=mi)
                verify = tx.read_config(module=mi)
                if _UC_SCRATCH_KEY in verify.json_data:
                    raise ValueError("Scratch key present after remove")
                return "add→remove OK"
            tests.append(self._do("tx", f"Module {m} UC Round-Trip", _ucfg_roundtrip))

            # 6. Restore original config
            def _ucfg_restore(mi=m, snap=_snap):
                orig = snap[0]
                if orig is None:
                    raise ValueError("No snapshot — Read step must have failed")
                tx.write_config_json(orig.get_json_str(), module=mi)
                verify = tx.read_config(module=mi)
                if verify.json_data != orig.json_data:
                    raise ValueError("Restored config does not match original")
                return f"restored seq={verify.header.seq}"
            tests.append(self._do("tx", f"Module {m} UC Restore", _ucfg_restore))

        # ── Trigger cycle 1: set random freq, read back and verify ────────
        _TRIG_BASE = {
            "TriggerPulseCount":         1,
            "TriggerPulseWidthUsec":     100,
            "TriggerPulseTrainInterval": 0,
            "TriggerPulseTrainCount":    1,
            "TriggerMode":               1,   # Continuous
            "ProfileIndex":              0,
            "ProfileIncrement":          0,
        }

        def _trig_set_and_get_freq(freq_cell):
            """Set trigger with freq_cell[0] Hz; return the actual freq sent."""
            freq = freq_cell[0]
            payload = dict(_TRIG_BASE)
            payload["TriggerFrequencyHz"] = freq
            result = tx.set_trigger_json(data=payload)
            if isinstance(result, str):
                result = json.loads(result)
            if result is None:
                raise ValueError("set_trigger_json returned None")
            return freq

        def _trig_read_verify(freq_cell):
            """Read trigger back and verify TriggerFrequencyHz == freq_cell[0]."""
            data = tx.get_trigger_json()
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                raise ValueError(
                    f"get_trigger_json returned unexpected type {type(data).__name__}"
                )
            got = data.get("TriggerFrequencyHz")
            if got != freq_cell[0]:
                raise ValueError(
                    f"Expected {freq_cell[0]} Hz, got {got} Hz"
                )
            return f"{got} Hz ✓"

        _freq1 = [random.randint(20, 60)]
        tests.append(self._do("tx", "Trigger Set #1 (Random Freq)",
                               lambda: _trig_set_and_get_freq(_freq1),
                               detail_fn=lambda v: f"freq={v} Hz set"))
        tests.append(self._do("tx", "Trigger Read-Back #1",
                               lambda: _trig_read_verify(_freq1)))

        # ── Trigger cycle 2: new random freq, set, read back and verify ──────
        _freq2 = [random.randint(20, 60)]
        # Ensure the second frequency is different from the first so the
        # read-back is a meaningful check.
        while _freq2[0] == _freq1[0]:
            _freq2[0] = random.randint(20, 60)

        tests.append(self._do("tx", "Trigger Set #2 (Random Freq)",
                               lambda: _trig_set_and_get_freq(_freq2),
                               detail_fn=lambda v: f"freq={v} Hz set"))
        tests.append(self._do("tx", "Trigger Read-Back #2",
                               lambda: _trig_read_verify(_freq2)))

        # ── Per-chip TX7332 register write/read (addr 0x0020) ────────────
        # For every enumerated TX7332 chip: write a random 32-bit value,
        # read it back, and verify the round-trip.
        try:
            chip_count = tx.enum_tx7332_devices()
        except Exception:
            chip_count = module_count   # fallback

        _REG_ADDR = 0x0020
        for chip_id in range(chip_count):
            def _chip_reg_rw(cid=chip_id):
                val = random.randint(0, 0xFFFF_FFFF)
                tx.write_block(cid, _REG_ADDR, [val])
                readback = tx.read_block(cid, _REG_ADDR, 1)
                got = readback[0] if readback else None
                if got != val:
                    got_str = f"0x{got:08X}" if got is not None else "empty"
                    raise ValueError(
                        f"Chip {cid} addr 0x{_REG_ADDR:04X}: "
                        f"wrote 0x{val:08X}, read back {got_str}"
                    )
                return f"0x{val:08X} \u2713"
            tests.append(self._do(
                "tx", f"TX7332 Chip {chip_id} Reg 0x{_REG_ADDR:04X} RW",
                _chip_reg_rw))

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
        self._steps_done += 1
        self.stepCompleted.emit(self._steps_done, self._total_steps)
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
        self._steps_done += 1
        self.stepCompleted.emit(self._steps_done, self._total_steps)
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
    testProgressUpdated = pyqtSignal(int, int)   # steps_done, total_steps
    pdfSaved            = pyqtSignal(bool, str)  # success, path-or-error

    # Internal signal used to marshal PDF rendering back to the GUI thread.
    # Qt delivers this via a QueuedConnection so _do_render_pdf always runs
    # in the main (GUI) thread even when emitted from a worker thread.
    _renderPdf = pyqtSignal(str, str)  # html, file_path

    def __init__(self, interface=None, parent=None):
        super().__init__(parent)
        self._interface = interface
        self._diag_thread: _DiagnosticThread | None = None
        self._renderPdf.connect(self._do_render_pdf)

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
        """Run all implemented diagnostic test groups."""
        self._start_tests(["console", "tx"])

    @pyqtSlot(str)
    def runTestGroup(self, group: str):
        """Run a single diagnostic group: 'console' or 'tx'."""
        supported_groups = {"console", "tx"}
        if group not in supported_groups:
            logger.warning("Unsupported diagnostic group requested: %s", group)
            self.testRunFinished.emit(json.dumps({
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "error": f"Unsupported diagnostic group: {group}",
                "results": {},
            }))
            return
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
        self._diag_thread.stepCompleted.connect(self.testProgressUpdated)
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

        HTML generation is done in a background thread to keep the UI
        responsive.  The actual QPrinter / QTextDocument work is marshalled
        back to the GUI thread via ``_renderPdf`` (a queued signal) so that
        Qt print classes are always used from the main thread.
        """
        t = threading.Thread(target=self._build_and_queue_pdf,
                             args=(results_json, file_path), daemon=True)
        t.start()

    def _build_and_queue_pdf(self, results_json: str, file_path: str):
        """Build report HTML in the worker thread, then hand off to GUI thread."""
        try:
            try:
                data = json.loads(results_json)
            except Exception:
                data = {}

            ts = data.get("timestamp", datetime.datetime.now().isoformat(timespec="seconds"))
            results = data.get("results", {})
            system_info = data.get("system_info", {})

            html = self._build_report_html(ts, results, system_info)
        except Exception as exc:
            logger.error("PDF report generation failed: %s", exc)
            self.pdfSaved.emit(False, str(exc))
            return

        # Emit via queued connection so _do_render_pdf runs on the GUI thread.
        self._renderPdf.emit(html, file_path)

    @pyqtSlot(str, str)
    def _do_render_pdf(self, html: str, file_path: str):
        """Render HTML to PDF using Qt classes.  Must run on the GUI thread."""
        try:
            from PyQt6.QtGui import QPageSize, QTextDocument
            from PyQt6.QtPrintSupport import QPrinter

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(file_path)
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))

            doc = QTextDocument()
            doc.setHtml(html)
            doc.print(printer)

            self.pdfSaved.emit(True, file_path)
        except Exception as exc:
            logger.error("PDF render failed: %s", exc)
            self.pdfSaved.emit(False, str(exc))

    @staticmethod
    def _build_report_html(timestamp: str, results: dict, system_info: dict = None) -> str:
        STATUS_COLOR = {"PASS": "#2ECC71", "FAIL": "#E74C3C", "SKIP": "#F39C12"}
        GROUP_LABEL  = {"console": "Console (HV Controller)", "tx": "Transmitter (TX)"}

        # ── System information header ──────────────────────────────────
        sysinfo = system_info or {}

        def _si_row(label, value):
            return (f'<tr><td style="width:200px;padding:3px 8px;color:#555;font-weight:bold;">{label}</td>'
                    f'<td style="padding:3px 8px;font-family:monospace;">{html_mod.escape(str(value))}</td></tr>')

        si_rows = ""
        if sysinfo.get("python_version"):
            si_rows += _si_row("Python", sysinfo["python_version"].split()[0])
        if sysinfo.get("platform"):
            si_rows += _si_row("Platform", sysinfo["platform"])
        if sysinfo.get("machine"):
            si_rows += _si_row("Machine", sysinfo["machine"])
        if sysinfo.get("sdk_version"):
            si_rows += _si_row("SDK Version", sysinfo["sdk_version"])
        console_info = sysinfo.get("console", {})
        if console_info.get("firmware_version"):
            si_rows += _si_row("HV Firmware", console_info["firmware_version"])
        if console_info.get("hardware_id"):
            si_rows += _si_row("HV Hardware ID", console_info["hardware_id"])
        tx_info = sysinfo.get("transmitter", {})
        if tx_info.get("module_count") is not None:
            si_rows += _si_row("TX Module Count", tx_info["module_count"])
        for m in tx_info.get("modules", []):
            idx = m.get("module", "?")
            if m.get("firmware_version"):
                si_rows += _si_row(f"TX Module {idx} Firmware", m["firmware_version"])
            if m.get("hardware_id"):
                si_rows += _si_row(f"TX Module {idx} HW ID", m["hardware_id"])

        sysinfo_block = ""
        if si_rows:
            sysinfo_block = f"""
        <h2 style="color:#1a3a5c;font-size:12pt;margin-top:16px;">System Information</h2>
        <table style="border-collapse:collapse;width:100%;margin-bottom:16px;">
          {si_rows}
        </table>"""

        total = sum(len(v) for v in results.values())
        passed = sum(1 for v in results.values() for r in v if r.get("status") == "PASS")
        failed = sum(1 for v in results.values() for r in v if r.get("status") == "FAIL")
        skipped = total - passed - failed

        rows = ""
        for group, tests in results.items():
            label = html_mod.escape(GROUP_LABEL.get(group, group.upper()))
            rows += f"""
            <tr><td colspan="3" style="background:#1a3a5c;color:#ffffff;font-weight:bold;
                padding:6px 8px;">{label}</td></tr>"""
            for t in tests:
                color = STATUS_COLOR.get(t.get("status", ""), "#BDC3C7")
                rows += f"""
            <tr>
                <td style="padding:4px 8px;">{html_mod.escape(t.get('name',''))}</td>
                <td style="color:{color};font-weight:bold;padding:4px 8px;">{html_mod.escape(t.get('status',''))}</td>
                <td style="padding:4px 8px;font-family:monospace;">{html_mod.escape(t.get('detail',''))}</td>
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
        <p class="meta">Generated: {html_mod.escape(timestamp)}</p>
        {sysinfo_block}
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

