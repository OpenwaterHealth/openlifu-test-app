"""Advanced-run safety (Copilot review of PR #58).

The advanced run is a state machine on a 1 s Qt timer. These tests drive
``AdvancedRunMixin`` step by step against a fake connector, with no Qt event
loop or hardware: time is faked through ``time.monotonic``, and the fake's
start/stop/configure only move ``_state`` the way the real ones do.
"""
from __future__ import annotations

import pytest

from lifu import lifu_advanced_run
from lifu.lifu_advanced_run import AdvancedRunMixin
from lifu.lifu_constants import CONNECTED, HV_EN_ON, READY, RUNNING
from lifu.lifu_controller import ControllerMixin

# Sequence, 10 trains 1 s apart: the running stale limit is 30 s.
ARGS = ["0", "0", "40", "400", "20", "100", "10", "1", "10", "200", "Sequence", [], []]


class Emitter:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class Mutex:
    def lock(self):
        pass

    def unlock(self):
        pass


class Timer:
    def __init__(self):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class HV:
    def __init__(self):
        self.hv_on = True
        self.calls = []

    def turn_hv_off(self):
        self.calls.append("hv_off")
        self.hv_on = False

    def turn_hv_on(self):
        self.calls.append("hv_on")
        self.hv_on = True

    def turn_12v_off(self):
        self.calls.append("12v_off")

    def turn_12v_on(self):
        self.calls.append("12v_on")

    def get_hv_status(self):
        return self.hv_on


class Interface:
    def __init__(self):
        self.hvcontroller = HV()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Run(ControllerMixin, AdvancedRunMixin):
    """Just enough of LIFUConnector for the advanced run to execute."""

    def __init__(self, modules=1):
        self.interface = Interface()
        self._interface_mutex = Mutex()
        self._ar_timer = Timer()
        self.advancedRunChanged = Emitter()
        self.advancedRunRepStarted = Emitter()
        self._state = READY
        self._txConnected = True
        self._hvConnected = True
        self._hv_enable_mode = HV_EN_ON
        self._num_modules_connected = modules
        self._cached_tx_info = []
        self._solution_loaded = False
        self._loaded_solution_path = ""
        self.errors = []
        self.events = []
        # Outcomes the next calls produce.
        self.stop_works = True
        self.temps = {m: 30.0 for m in range(modules)}
        self._ar_phase = "idle"
        self._ar_status = ""
        self._ar_settings = {}
        self._ar_configure_args = []
        self._ar_modules = 0
        self._ar_rep = 0
        self._ar_deadline = 0.0
        self._ar_wait_started = 0.0
        self._ar_rep_started = 0.0
        self._ar_last_cool_log = 0.0
        self._ar_temps = {}
        self._ar_power_cycling = False
        self._ar_needs_configure = False
        self._ar_hv_restore = False
        self._ar_reconfiguring = False
        self._ar_pending_status = ""
        self._ar_stop_attempts = 0
        self._ar_hv_confirmed_off = False
        self._ar_log_path = ""
        self._ar_log_handler = None

    # Stand-ins for the connector's device paths.
    def _hv_ready(self):
        return True

    def _emit_device_error(self, title, message):
        self.errors.append((title, message))

    def queryTxTemperature(self):
        for module, temp in self.temps.items():
            self._ar_on_temperature(module, temp, 25.0)

    def configure_transmitter(self, *args):
        self.events.append(("configure", list(args)))
        self._state = READY

    def directSetRunProfile(self, profile, *args):
        if self._locked_by_advanced_run("Set Run Profile"):
            return False
        self.events.append(("pin", profile))
        return True

    def _start_sonication(self, force_hv_on=False):
        self.events.append(("start", force_hv_on))
        self._state = RUNNING

    def stop_sonication(self):
        self.events.append(("stop",))
        if self.stop_works:
            self._state = READY


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(lifu_advanced_run.time, "monotonic", clock)
    return clock


@pytest.fixture
def no_log(monkeypatch):
    monkeypatch.setattr(Run, "_ar_open_log", lambda self: None)


def start(run, **settings):
    values = {"repetitions": 2, "startC": 40, "shutoffC": 60, "restMinutes": 1, "powerOff": False}
    values.update(settings)
    return run.startAdvancedRun(list(ARGS), values)


def finish_run(run, clock):
    """The firmware ends the current run."""
    run._state = READY
    clock.now += 1
    run._ar_tick()


def test_log_setup_failure_reports_instead_of_raising(monkeypatch, clock):
    run = Run()

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr(Run, "_ar_open_log", fail)
    assert start(run) is False
    assert run._ar_phase == "idle"
    assert "disk full" in run.errors[-1][1]
    assert not run._ar_timer.active


def test_every_later_run_is_reprogrammed_from_the_captured_settings(clock, no_log):
    run = Run()
    assert start(run, runProfile=2)
    assert run.events == [("start", False)]  # run 1 uses the user's Configure
    finish_run(run, clock)  # rest with the transmitter on
    run.events.clear()
    clock.now += 61
    run.queryTxTemperature()
    run._ar_tick()
    assert run.events == [("configure", ARGS), ("pin", 2), ("start", False)]


def test_settings_are_locked_during_a_rest(clock, no_log):
    run = Run()
    assert start(run)
    finish_run(run, clock)
    assert run._ar_phase == "resting"
    assert run.directSetSequence("100", "10", "1", "10", "Continuous") is False
    assert run.errors[-1][0] == "Set Sequence"
    run._ar_finish("done")
    assert run._locked_by_advanced_run("Set Sequence") is False


def test_hv_is_restored_after_a_power_off_rest(clock, no_log):
    run = Run()
    assert start(run, powerOff=True)
    finish_run(run, clock)
    assert run._ar_phase == "powered_off"
    assert "hv_off" in run.interface.hvcontroller.calls
    clock.now += 61
    run._ar_tick()  # 12 V back on
    assert run._ar_phase == "powering_on"
    run.events.clear()
    run._ar_tick()  # TX is back: reprogram, check, start
    assert run.events[0][0] == "configure"
    assert run.events[-1] == ("start", True)
    assert run._ar_hv_restore is False


def test_a_module_with_a_stale_reading_blocks_the_start(clock, no_log):
    run = Run(modules=2)
    run.temps = {0: 30.0}  # module 1 never reports
    assert start(run) is False
    assert "No transmitter temperature" in run.errors[-1][1]
    assert ("start", False) not in run.events


def test_hottest_uses_every_module(clock):
    run = Run(modules=2)
    run._ar_modules = 2
    run._ar_on_temperature(0, 30.0, 25.0)
    run._ar_on_temperature(1, 80.0, 25.0)
    assert run._ar_hottest()[0] == 80.0
    clock.now += lifu_advanced_run.TEMP_MAX_AGE_S + 1
    run._ar_on_temperature(0, 30.0, 25.0)
    assert run._ar_hottest() is None  # module 1's 80 °C went stale


def test_a_run_without_fresh_readings_is_stopped(clock, no_log):
    run = Run()
    assert start(run)
    assert run._ar_phase == "running"
    limit = run._ar_running_stale_limit_s()
    clock.now += limit - 1
    run._ar_tick()
    assert run._ar_phase == "running"
    clock.now += 2
    run._ar_tick()
    assert run._ar_phase == "idle"
    assert ("stop",) in run.events
    assert "can't be enforced" in run.errors[-1][1]


def test_stale_limit_covers_the_gap_between_status_frames():
    run = Run()
    run._ar_configure_args = ["0", "0", "40", "400", "20", "100", "10", "60", "5", "200", "Sequence"]
    assert run._ar_running_stale_limit_s() == 130  # trains 60 s apart
    run._ar_configure_args[10] = "Single"
    assert run._ar_running_stale_limit_s() == 30  # one 1 s train


def test_a_failed_stop_keeps_the_run_supervised_and_cuts_hv(clock, no_log):
    run = Run()
    assert start(run)
    run.stop_works = False
    run.stopAdvancedRun()
    assert run._ar_phase == "stopping"
    assert run._ar_timer.active
    assert "hv_off" in run.interface.hvcontroller.calls
    run.stop_works = True
    clock.now += lifu_advanced_run.STOP_RETRY_INTERVAL_S
    run._ar_tick()
    assert run._ar_phase == "idle"
    assert run._ar_status == "Stopped by user."


def test_a_stop_that_never_works_is_reported_after_the_retries(clock, no_log):
    run = Run()
    assert start(run)
    run.stop_works = False
    run.stopAdvancedRun()
    for _ in range(lifu_advanced_run.STOP_RETRIES):
        clock.now += lifu_advanced_run.STOP_RETRY_INTERVAL_S
        run._ar_tick()
    assert run._ar_phase == "idle"
    assert "did not stop" in run._ar_status
    assert "HV is off" in run.errors[-1][1]
    assert run._state == RUNNING  # still shown as running so Stop stays available


class ScaledConnector(ControllerMixin):
    """A loaded JSON file whose voltage was scaled to two modules."""

    def __init__(self):
        self._interface_mutex = Mutex()
        self._solution_loaded = True
        self._num_modules_connected = 2
        self.uids = ["aa", "bb"]
        self._loaded_solution_module_uids = ["aa", "bb"]
        outer = self

        class Tx:
            def get_hardware_id(self, module, raw_hex=False):
                if outer.uids is None:
                    raise OSError("port closed")
                return outer.uids[module]

        class Iface:
            txdevice = Tx()

        self.interface = Iface()
        self.solution = {"voltage_calibration": {"enabled": True, "pending": False,
                                                 "module_sensitivities": [3500.0, 3600.0]}}


def test_scaled_file_passes_with_the_same_modules():
    assert ScaledConnector()._scaled_for_other_modules(ScaledConnector().solution) is False


@pytest.mark.parametrize("uids", [["aa", "cc"], ["aa"], None], ids=["swapped", "fewer", "unreadable"])
def test_scaled_file_is_refused_after_a_module_change(uids):
    connector = ScaledConnector()
    connector.uids = uids
    connector._num_modules_connected = 2 if uids is None else len(uids)
    assert connector._scaled_for_other_modules(connector.solution) is True


def test_unscaled_file_is_not_checked():
    connector = ScaledConnector()
    connector.uids = ["xx", "yy"]
    connector.solution["voltage_calibration"]["enabled"] = False
    assert connector._scaled_for_other_modules(connector.solution) is False
