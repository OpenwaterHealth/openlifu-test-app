"""Firmware images the app ships and installs.

openlifu-sdk 2.1.0-rc.4 and later carry no firmware (openlifu-sdk 87cd427):
every update path takes its images from the caller, so the app ships them in
``firmware/`` and passes them in explicitly. Older SDKs accept the same
arguments, so the app runs on either.

The application images and the bootloader updaters are the latest official
(non-pre-release) firmware releases, fetched by ``scripts/fetch_firmware.py``
(CI runs it before every build). ``firmware/manifest.json`` records the
release of every file. The legacy updaters and slave DFU stubs have no
releases; they are the copies openlifu-sdk 2.1.0-rc.3 bundled.

Images (the SDK's former ``bundled_*()`` helpers in brackets):

- console signed app            (``bundled_signed_app``)
- console production image      (``bundled_production_image``)
- console legacy RAM updater    (``LIFUDFU.bundled_updater_path``)
- transmitter signed app        (``bundled_transmitter_signed_app``)
- transmitter production image  (``bundled_transmitter_production_image``)
- transmitter legacy updater    (``bundled_transmitter_legacy_updater``)
- transmitter DFU stub, raw     (``bundled_transmitter_dfu_stub``): slave
  legacy migration
- transmitter DFU stub, signed  (``bundled_transmitter_dfu_stub_signed``):
  slave production reflash

Plus the bootloader updaters the SDK never bundled (``bl_updater``, from the
open-lifu-{console,transmitter}-bl-updater releases). openlifu-sdk
2.1.0-rc.18 and later install one first on a secure bootloader older than
1.1.0 (console app 1.2.6 / 1.2.7, transmitter app 2.0.8 and the bootloaders
the production images above carry) and refuse the update without it. The
replacement is permanent and research (unlocked) units only. On a locked
unit the updater refuses to touch the bootloader, but the old bootloader
has already written it over the application: the unit is left in DFU with
no application. SDKs without the argument (up to
2.1.0-rc.3) never replace the bootloader, so it is passed only when the
installed SDK accepts it.

Any new image must also be in ``firmware/`` (bundled by
``OpenLIFU-TestApp.spec``) and ``firmware/manifest.json``.
"""
from __future__ import annotations

import functools
import inspect
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONSOLE_SIGNED_APP = "openlifu-console-fw-signed.bin"
CONSOLE_PRODUCTION = "openlifu-console-fw-production.bin"
CONSOLE_LEGACY_UPDATER = "openlifu-console-legacy-updater.bin"
TRANSMITTER_SIGNED_APP = "openlifu-transmitter-fw-signed.bin"
TRANSMITTER_PRODUCTION = "openlifu-transmitter-fw-production.bin"
TRANSMITTER_LEGACY_UPDATER = "openlifu-transmitter-legacy-updater.bin"
TRANSMITTER_DFU_STUB = "openlifu-transmitter-dfu-stub.bin"
TRANSMITTER_DFU_STUB_SIGNED = "openlifu-transmitter-dfu-stub-signed.bin"
CONSOLE_BL_UPDATER = "open-lifu-console-bl-updater-signed.bin"
TRANSMITTER_BL_UPDATER = "open-lifu-transmitter-bl-updater-signed.bin"


def firmware_dir() -> Path:
    """``firmware/`` beside the app: the PyInstaller bundle root when frozen,
    the project root when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "firmware"
    return Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "firmware"


def image(name: str) -> Path:
    """Path of a shipped image. Raises FileNotFoundError if it is missing,
    so an update stops before the device is touched."""
    path = firmware_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"Firmware image missing from the app: {path}")
    return path


def _optional(name: str) -> str | None:
    """Path of a shipped image as a string, or None (logged) if missing."""
    try:
        return str(image(name))
    except FileNotFoundError as e:
        logger.warning("%s", e)
        return None


def image_version(path: str | Path) -> str:
    """Version an image carries: the FwVersion in an SBSFU 'SFU1' signed
    header, else the first ``MAJOR.MINOR.PATCH`` in the file (the app's
    git-describe string). Same rule as the SDK's
    ``util.firmware.firmware_image_version`` (formerly
    ``_get_firmware_version``). Raises ValueError if none is found."""
    data = Path(path).read_bytes()
    if data[:4] == b"SFU1":
        from openlifu_sdk.io import LIFUCrypto
        try:
            header = LIFUCrypto.FirmwareHeader.from_bytes(
                data[:LIFUCrypto.HEADER_TOTAL_LEN])
            return header.fw_version_str
        except LIFUCrypto.LIFUCryptoError as e:
            logger.warning("SFU1 header parse failed for %s (%s); falling "
                           "back to string scan", path, e)
    match = re.search(rb"\d+\.\d+\.\d+", data)
    if match:
        return match.group().decode()
    raise ValueError(f"No firmware version found in {path}")


@functools.lru_cache(maxsize=None)
def shipped_version(name: str) -> str | None:
    """Version of a shipped image, or None (logged) if missing/unreadable."""
    try:
        return image_version(image(name))
    except Exception as e:
        logger.warning("Could not read the version of %s: %s", name, e)
        return None


# ---------------------------------------------------------------- update args
# Keyword arguments for the SDK's update calls. A missing image is left out,
# so the SDK refuses that route itself before anything is written.

def _kwargs(**names: str) -> dict[str, str]:
    out = {}
    for key, name in names.items():
        path = _optional(name)
        if path:
            out[key] = path
    return out


@functools.lru_cache(maxsize=None)
def sdk_takes_bl_updater(method: str) -> bool:
    """True if the installed SDK's *method* (``"LIFUFirmwareUpdate.update"``,
    ``"LIFUTransmitterFirmwareUpdate.update"`` or
    ``"LIFUTransmitterFirmwareUpdate.update_slave"``) has ``bl_updater``."""
    from openlifu_sdk.io import LIFUFirmwareUpdate as mod
    cls, name = method.split(".")
    try:
        fn = getattr(getattr(mod, cls), name)
        return "bl_updater" in inspect.signature(fn).parameters
    except (AttributeError, TypeError, ValueError):
        return False


def _add_bl_updater(kw: dict[str, str], name: str, method: str) -> dict[str, str]:
    if sdk_takes_bl_updater(method):
        path = _optional(name)
        if path:
            kw["bl_updater"] = path
    return kw


def console_update_images(signed_app: str | None = None) -> dict[str, str]:
    """For ``LIFUFirmwareUpdate.update()``: the shipped production image
    (no-bootloader / ROM route), legacy updater (legacy-bootloader route) and
    bootloader updater (1.0.x secure bootloader), plus *signed_app* or the
    shipped signed app."""
    kw = _kwargs(production_image=CONSOLE_PRODUCTION,
                 updater_bin=CONSOLE_LEGACY_UPDATER)
    app = signed_app or _optional(CONSOLE_SIGNED_APP)
    if app:
        kw["signed_app"] = app
    return _add_bl_updater(kw, CONSOLE_BL_UPDATER, "LIFUFirmwareUpdate.update")


def transmitter_update_images(signed_app: str | None = None,
                              production_image: str | None = None) -> dict[str, str]:
    """For ``LIFUTransmitterFirmwareUpdate.update()`` on the master: the
    shipped legacy updater and bootloader updater, and *signed_app* /
    *production_image* or the shipped ones."""
    kw = _kwargs(updater_bin=TRANSMITTER_LEGACY_UPDATER)
    app = signed_app or _optional(TRANSMITTER_SIGNED_APP)
    if app:
        kw["signed_app"] = app
    prod = production_image or _optional(TRANSMITTER_PRODUCTION)
    if prod:
        kw["production_image"] = prod
    return _add_bl_updater(kw, TRANSMITTER_BL_UPDATER,
                           "LIFUTransmitterFirmwareUpdate.update")


def transmitter_slave_images(legacy: bool | None,
                             signed_app: str | None = None) -> dict:
    """For ``LIFUTransmitterFirmwareUpdate.update_slave()``. The legacy
    migration (slave app 2.0.4 - 2.0.7) takes the raw DFU stub and the
    production reflash of a secure slave the signed one, both through
    ``stub_bin``.

    *legacy* is the route the app read from the slave's version, passed on
    as ``legacy=`` so the SDK does not detect it again and pair the stub
    with the other route. ``None`` (version unreadable) passes no stub and
    no route: the SDK detects it, and the two stub routes refuse before
    writing anything, while a secure update (or a slave already parked in
    its bootloader) needs no stub. A secure slave on a 1.0.x bootloader
    gets the bootloader updater over I2C first."""
    kw: dict = _kwargs(production_image=TRANSMITTER_PRODUCTION)
    if legacy is not None:
        kw["legacy"] = legacy
        stub = _optional(TRANSMITTER_DFU_STUB if legacy else TRANSMITTER_DFU_STUB_SIGNED)
        if stub:
            kw["stub_bin"] = stub
    app = signed_app or _optional(TRANSMITTER_SIGNED_APP)
    if app:
        kw["signed_app"] = app
    return _add_bl_updater(kw, TRANSMITTER_BL_UPDATER,
                           "LIFUTransmitterFirmwareUpdate.update_slave")
