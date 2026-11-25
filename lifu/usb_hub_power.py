"""Keep the console's on-board USB hub awake during a firmware update (Windows).

The console's MCU sits behind a USB2514 hub inside the console. During a
firmware update the app detaches from USB and resets into its bootloader. With
no transmitter attached, the console is that hub's only device, so Windows may
selectively suspend the hub in the gap. Console firmware hands over with the
hub in that state, and the bootloader's brief hub-reset pulse then leaves the
suspended hub unable to report the bootloader's DFU device: the update times
out and the console sits invisible in its bootloader
(OpenwaterHealth/open-lifu-console-bl#2). No released console firmware fixes
this on the device side yet.

The host-side fix is the hub's "Allow the computer to turn off this device to
save power" setting, cleared for the console's own hub only: the
hub is found as the parent of the console's USB device, so other hubs -- even
other USB2514s, a common dock chip -- are untouched. Reading the setting needs
no privileges; changing it does, so :func:`keep_hubs_awake` runs one elevated
PowerShell command and Windows shows its UAC prompt. Windows keys the setting
by USB port, so a console moved to a new port is asked about again.

Everything here is a no-op off Windows.
"""
from __future__ import annotations

import base64
import ctypes
import logging
import re
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CONSOLE_USB_PREFIX = "USB\\VID_0483&PID_57A0\\"
CONSOLE_HUB_PREFIX = "USB\\VID_0424&PID_2514\\"

# Only device instance IDs of exactly this shape are ever put into the
# elevated command (they are quoted, and nothing else may be interpolated).
_HUB_INSTANCE_RE = re.compile(r"^USB\\VID_0424&PID_2514\\[0-9A-Za-z&_.]{1,64}$")

_ERROR_CANCELLED = 1223          # the user dismissed the UAC prompt
_ELEVATED_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class HubPowerResult:
    """Outcome of :func:`keep_hubs_awake`.

    ``status`` is ``"applied"`` (every hub now stays awake), ``"declined"``
    (the UAC prompt was dismissed), ``"failed"`` or ``"unsupported"`` (not
    Windows).
    """
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "applied"


def is_valid_hub_instance(instance: str) -> bool:
    """True if ``instance`` looks like a USB2514 hub device instance ID."""
    return bool(_HUB_INSTANCE_RE.match(instance or ""))


# ---------------------------------------------------------------------------
# Finding the console's hub (cfgmgr32, no privileges needed)
# ---------------------------------------------------------------------------
def _split_multi_sz(buf: str) -> list[str]:
    return [s for s in buf.split("\0") if s]


def _present_usb_instances() -> list[str]:
    cfg = ctypes.WinDLL("cfgmgr32")
    flags = 0x00000001 | 0x00000100   # CM_GETIDLIST_FILTER_ENUMERATOR | _PRESENT
    size = ctypes.c_ulong()
    if cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "USB", flags) != 0:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cfg.CM_Get_Device_ID_ListW("USB", buf, size.value, flags) != 0:
        return []
    return _split_multi_sz(buf[:size.value])


def _parent_instance(instance: str) -> str | None:
    cfg = ctypes.WinDLL("cfgmgr32")
    devinst, parent = ctypes.c_ulong(), ctypes.c_ulong()
    if cfg.CM_Locate_DevNodeW(ctypes.byref(devinst), instance, 0) != 0:
        return None
    if cfg.CM_Get_Parent(ctypes.byref(parent), devinst, 0) != 0:
        return None
    buf = ctypes.create_unicode_buffer(512)
    if cfg.CM_Get_Device_IDW(parent, buf, len(buf), 0) != 0:
        return None
    return buf.value


def console_hub_instances() -> list[str]:
    """Device instance IDs of the USB2514 hub of every connected console.

    A console behind something other than its own USB2514 (not expected) is
    skipped rather than guessed at. Empty off Windows or on any error.
    """
    if sys.platform != "win32":
        return []
    hubs: list[str] = []
    try:
        for inst in _present_usb_instances():
            if not inst.upper().startswith(CONSOLE_USB_PREFIX):
                continue
            parent = _parent_instance(inst)
            if parent and parent.upper().startswith(CONSOLE_HUB_PREFIX) and parent not in hubs:
                hubs.append(parent)
            else:
                logger.info("Console %s is not behind its USB2514 hub (parent %s)", inst, parent)
    except Exception as e:  # noqa: BLE001 - best effort, never block an update
        logger.warning("Could not locate the console's USB hub: %s", e)
        return []
    return hubs


# ---------------------------------------------------------------------------
# Reading the setting (registry, readable by everyone)
# ---------------------------------------------------------------------------
def hub_may_sleep(instance: str) -> bool:
    """True unless the hub's power management is known to be off.

    Reads ``Device Parameters\\WDF\\IdleInWorkingState`` (0 = "Allow the
    computer to turn off this device" cleared). A missing value is the
    driver default, which lets the hub sleep; an unreadable one is treated
    the same way so the caller errs towards asking.
    """
    if sys.platform != "win32":
        return False
    import winreg
    key = rf"SYSTEM\CurrentControlSet\Enum\{instance}\Device Parameters\WDF"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            value, _ = winreg.QueryValueEx(k, "IdleInWorkingState")
        return int(value) != 0
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Changing the setting (elevated, UAC prompt)
# ---------------------------------------------------------------------------
def build_keep_awake_script(instances: list[str]) -> str:
    """PowerShell that clears power management on each hub via the same WMI
    switch Device Manager uses (takes effect immediately), then exits 0 only
    if every hub reads back as staying awake. Raises ValueError on an
    instance ID that is not a USB2514 hub."""
    for inst in instances:
        if not is_valid_hub_instance(inst):
            raise ValueError(f"not a console hub instance: {inst!r}")
    names = ", ".join(f"'{inst}_0'" for inst in instances)
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"foreach ($n in @({names})) {{ "
        "$i = Get-CimInstance -Namespace root\\wmi -ClassName MSPower_DeviceEnable "
        "| Where-Object { $_.InstanceName -ieq $n }; "
        "if (-not $i) { exit 2 }; "
        "if ($i.Enable) { Set-CimInstance -InputObject $i -Property @{ Enable = $false } }; "
        "$c = Get-CimInstance -Namespace root\\wmi -ClassName MSPower_DeviceEnable "
        "| Where-Object { $_.InstanceName -ieq $n }; "
        "if ($c.Enable) { exit 3 } }; exit 0"
    )


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong), ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _run_elevated_powershell(script: str) -> tuple[str, int]:
    """("ok", exit_code) | ("declined", -1) | ("failed", winerror)."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00008000   # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    info.lpParameters = f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"
    info.nShow = 0                          # SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        return ("declined", -1) if err == _ERROR_CANCELLED else ("failed", err)
    try:
        if kernel32.WaitForSingleObject(ctypes.c_void_p(info.hProcess), _ELEVATED_TIMEOUT_MS) != 0:
            return "failed", -2
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(info.hProcess), ctypes.byref(code))
        return "ok", code.value
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))


def keep_hubs_awake(instances: list[str]) -> HubPowerResult:
    """Clear power management on ``instances`` (console hubs) via one
    elevated command; Windows prompts for permission. Blocks until done --
    call from a worker thread."""
    if sys.platform != "win32":
        return HubPowerResult("unsupported")
    try:
        script = build_keep_awake_script(instances)
    except ValueError as e:
        return HubPowerResult("failed", str(e))
    outcome, code = _run_elevated_powershell(script)
    if outcome == "declined":
        return HubPowerResult("declined", "Windows permission was not granted")
    if outcome == "failed":
        return HubPowerResult("failed", f"could not run the elevated command (error {code})")
    if code == 0:
        return HubPowerResult("applied")
    reason = {2: "the hub's power setting was not found",
              3: "the hub's power setting did not change"}.get(code, f"exit code {code}")
    return HubPowerResult("failed", reason)
