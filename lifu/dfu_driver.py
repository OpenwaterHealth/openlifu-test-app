"""WinUSB driver for the console / transmitter USB DFU device (Windows).

Every DFU environment of both devices (STM32 ROM loader, legacy and secure
bootloaders) enumerates as ``USB\\VID_0483&PID_DF11``. Windows has no inbox
driver for it, so without one libusb cannot open the device and every
firmware update or recovery fails. Installing STM32CubeProgrammer used to be
the workaround; the app now ships the driver itself in ``drivers/``:

  - ``DFU_in_FS_Mode.inf`` / ``.cat`` -- binds DF11 to Windows' own
    WinUSB. Vendored unchanged from the OpenMotion driver package
    (openmotion-sdk ``driver-msi.yml``); the catalog is EV-signed and covers
    the INF's exact bytes, so never edit either file.
  - ``openlifu_signing_cert.cer`` -- the public Openwater EV certificate the
    catalog is signed with. Added to the machine's TrustedPublisher store so
    Windows installs the driver without a "trust this publisher?" dialog.

Checking needs no privileges; installing does, so :func:`install_driver`
runs one elevated PowerShell command and Windows shows its UAC prompt.
Everything here is a no-op off Windows.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DFU_ID_RE = re.compile(r"VID_0483&PID_DF11(?![0-9A-F])")
DRIVER_INF = "DFU_in_FS_Mode.inf"
DRIVER_CAT = "DFU_in_FS_Mode.cat"
SIGNING_CERT = "openlifu_signing_cert.cer"
# Openwater EV cert (SSL.com, valid to 2027-11-06). Update on renewal,
# together with a re-signed catalog.
SIGNING_CERT_SHA1 = "ADFCD7CB6A7900A204F91EDF9E5ADADB5A8C1AB4"

_ERROR_CANCELLED = 1223          # the user dismissed the UAC prompt
_ELEVATED_TIMEOUT_MS = 120_000
# pnputil: 0 ok, 259 nothing to update (already current), 3010 reboot needed.
_PNPUTIL_OK = (0, 259, 3010)

_installed_cache = False         # positive result only; a missing driver is rechecked


@dataclass(frozen=True)
class DriverResult:
    """Outcome of :func:`ensure_driver` / :func:`install_driver`.

    ``status`` is ``"present"`` (already installed), ``"installed"``,
    ``"declined"`` (UAC prompt dismissed), ``"failed"`` or ``"unsupported"``
    (not Windows).
    """
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("present", "installed", "unsupported")


def driver_dir() -> Path:
    """Folder holding the bundled driver files (frozen or source run)."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root / "drivers"


# ---------------------------------------------------------------------------
# Is a WinUSB driver for DF11 already in the driver store? (no privileges)
# ---------------------------------------------------------------------------
def _read_inf_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("latin-1")


def inf_is_dfu_winusb(text: str) -> bool:
    """True if an INF's text binds DF11 to WinUSB (ours, Zadig's, or
    CubeProgrammer's WinUSB-based one). ST's old DfuSe driver doesn't count:
    libusb can't use it. Matches the bare VID/PID because libwdi INFs build
    the ID as ``USB\\%DeviceID%``."""
    up = text.upper()
    return bool(_DFU_ID_RE.search(up)) and "WINUSB" in up


def _driver_store_infs() -> list[Path]:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
        return sorted((windir / "INF").glob("oem*.inf"))
    except OSError:
        return []


def is_driver_installed() -> bool:
    """True if a WinUSB driver for DF11 is installed. Scans the published
    ``%SystemRoot%\\INF\\oem*.inf`` copies, which avoids parsing pnputil's
    localized output. False off Windows."""
    global _installed_cache
    if sys.platform != "win32":
        return False
    if _installed_cache:
        return True
    for inf in _driver_store_infs():
        try:
            if inf_is_dfu_winusb(_read_inf_text(inf)):
                logger.debug("DFU WinUSB driver found: %s", inf.name)
                _installed_cache = True
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Installing (elevated, UAC prompt)
# ---------------------------------------------------------------------------
def check_bundle(folder: Path) -> str | None:
    """Error text if the bundled driver files are missing or the cert isn't
    Openwater's (it goes into TrustedPublisher, so never trust it blindly)."""
    for name in (DRIVER_INF, DRIVER_CAT, SIGNING_CERT):
        if not (folder / name).is_file():
            return f"bundled driver file missing: {folder / name}"
    thumb = hashlib.sha1((folder / SIGNING_CERT).read_bytes()).hexdigest().upper()
    if thumb != SIGNING_CERT_SHA1:
        return f"bundled signing certificate has unexpected thumbprint {thumb}"
    return None


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def build_install_script(folder: Path) -> str:
    """PowerShell that trusts the signing cert, then adds + installs the INF.
    Exits 0 on success, 10 if certutil failed, 20 if pnputil failed."""
    sys32 = "$env:SystemRoot\\System32"
    return (
        f"& \"{sys32}\\certutil.exe\" -f -addstore TrustedPublisher "
        f"{_ps_quote(folder / SIGNING_CERT)} | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { exit 10 }; "
        f"& \"{sys32}\\pnputil.exe\" /add-driver {_ps_quote(folder / DRIVER_INF)} /install | Out-Null; "
        f"if (@({', '.join(str(c) for c in _PNPUTIL_OK)}) -notcontains $LASTEXITCODE) {{ exit 20 }}; "
        "exit 0"
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


def install_driver() -> DriverResult:
    """Install the bundled driver via one elevated command; Windows prompts
    for permission. Blocks until done -- call from a worker thread."""
    global _installed_cache
    if sys.platform != "win32":
        return DriverResult("unsupported")
    folder = driver_dir()
    problem = check_bundle(folder)
    if problem:
        return DriverResult("failed", problem)
    outcome, code = _run_elevated_powershell(build_install_script(folder))
    if outcome == "declined":
        return DriverResult("declined", "Windows permission was not granted")
    if outcome == "failed":
        return DriverResult("failed", f"could not run the elevated command (error {code})")
    if code == 0:
        _installed_cache = True
        return DriverResult("installed")
    reason = {10: "the signing certificate could not be trusted (certutil)",
              20: "Windows rejected the driver package (pnputil)"}.get(code, f"exit code {code}")
    return DriverResult("failed", reason)


def ensure_driver() -> DriverResult:
    """Install the DFU driver unless one is already present."""
    if sys.platform != "win32":
        return DriverResult("unsupported")
    if is_driver_installed():
        return DriverResult("present")
    logger.info("No WinUSB driver for the DFU device; installing the bundled one")
    result = install_driver()
    log = logger.info if result.ok else logger.warning
    log("DFU driver install: %s %s", result.status, result.message)
    return result
