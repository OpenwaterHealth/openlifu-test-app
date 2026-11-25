"""Shared test guards."""
from __future__ import annotations

import pytest

from lifu import dfu_driver, usb_hub_power


@pytest.fixture(autouse=True)
def no_real_dfu_driver_install(monkeypatch):
    """Updates install the DFU USB driver (UAC) when it's missing. Tests see
    it as installed unless they say otherwise, and a real install fails loudly."""
    monkeypatch.setattr(dfu_driver, "is_driver_installed", lambda: True)

    def _refuse():
        raise AssertionError("test tried to install the real DFU driver")

    monkeypatch.setattr(dfu_driver, "install_driver", _refuse)
    monkeypatch.setattr(dfu_driver, "_run_elevated_powershell", lambda script: _refuse())


@pytest.fixture(autouse=True)
def no_real_usb_hub_changes(monkeypatch):
    """Console updates may look up the console's USB hub and ask Windows (UAC)
    to keep it awake. Tests must never touch the real machine: no hub is found
    unless a test says otherwise, and the elevated call fails loudly."""
    monkeypatch.setattr(usb_hub_power, "console_hub_instances", lambda: [])

    def _refuse(instances):
        raise AssertionError(f"test tried to change real USB hub settings: {instances}")

    monkeypatch.setattr(usb_hub_power, "keep_hubs_awake", _refuse)
