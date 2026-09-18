"""Printer backends for the Brother QL-800.

Everything goes through :class:`PrinterBackend` so that swapping the underlying
library touches one file. That matters here: ``brother_ql`` is effectively
unmaintained (it warns about ``brother_ql.devicedependent`` on import, and
still calls ``PIL.Image.ANTIALIAS``, which Pillow removed in v10). The
drop-in replacement, if it comes to that, is the GPL-3.0 fork
``luxardolabs/brother_ql``.

The default backend is ``fake``, which writes a PNG preview. Real printing is
opt-in, so no test and no accidental run burns tape.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import threading
import warnings
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..config import Config
from .layout import PRINTABLE_WIDTH

#: Brother's USB vendor id. The QL-800 is 0x04f9:0x209b.
BROTHER_VENDOR = 0x04F9
QL800_PRODUCT = 0x209B

#: Null bytes the QL-800 expects before a command, to flush any partial one it
#: may be mid-way through. 400 for the QL-800 and later; 200 for older models.
QL800_INVALIDATE = 400

log = logging.getLogger(__name__)

#: Serialises every access to the USB printer. FastAPI runs sync endpoints in
#: a threadpool, so two print requests arriving together would otherwise write
#: to the same device at the same time and interleave their rasters.
_ACCESS = threading.Lock()


@contextlib.contextmanager
def exclusive():
    """Hold the printer for the duration of one job."""
    with _ACCESS:
        yield


def auto_power_off_command(invalidate: int = QL800_INVALIDATE) -> bytes:
    """Bytes that switch the printer's auto power-off off, permanently.

    The framing comes from i3labelstation, which drives the same QL-800:

        invalidate x 0x00     flush any half-sent command
        1B 40                 ESC @      initialise
        1B 69 55 41 00 00     ESC i U A  auto power-off, timeout 0 = never

    Written to the printer's own memory, so it survives a power cycle and only
    needs sending once -- the same setting Brother's Printer Setting Tool
    writes through its GUI.
    """
    return bytes(invalidate) + b"\x1b\x40" + b"\x1b\x69\x55\x41\x00\x00"


class _UsbLink:
    """An open QL-800: write to it, then close it.

    Closing is the point. pyusb keeps the device claimed until its resources
    are disposed, and this runs inside the long-lived service -- a link left
    open holds the printer exclusively for the life of the process, and every
    later print job (brother_ql opens the device itself) is refused.
    """

    def __init__(self, device, endpoint):
        self._device = device
        self._endpoint = endpoint

    def write(self, payload: bytes) -> None:
        self._endpoint.write(payload)

    def close(self) -> None:
        import usb.util

        usb.util.dispose_resources(self._device)


def _open_ql800():
    """The printer as an open link, or None. The caller must close() it."""
    import usb.core
    import usb.util

    device = usb.core.find(idVendor=BROTHER_VENDOR, idProduct=QL800_PRODUCT)
    if device is None:
        return None

    try:
        with contextlib.suppress(NotImplementedError, Exception):
            if device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)

        device.set_configuration()
        interface = device.get_active_configuration()[(0, 0)]
        endpoint = usb.util.find_descriptor(
            interface,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )
    except Exception:
        # Half-opened is still opened: let go before reporting the failure.
        usb.util.dispose_resources(device)
        raise
    return _UsbLink(device, endpoint)


def disable_auto_power_off(config: Config, find_device=None) -> bool:
    """Tell the printer never to switch itself off. True if it was sent.

    Failure is reported, never raised: this runs in a background thread at
    startup, and a printer being unplugged is the ordinary case, not an error.
    """
    opener = find_device or _open_ql800
    try:
        with exclusive():
            link = opener()
            if link is None:
                return False
            try:
                link.write(auto_power_off_command())
            finally:
                link.close()
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        log.info("could not disable auto power-off: %s", exc)
        return False
    log.info("auto power-off disabled on the printer")
    return True


class AutoOffWatcher(threading.Thread):
    """Waits for the printer to appear, disables auto power-off, then stops.

    It stops rather than polling forever because the setting persists in the
    printer: once it lands there is nothing left to do. Directing *all* printer
    traffic through a single thread would be the alternative, but the only
    thing that actually needs serialising is concurrent access, and
    `exclusive()` does that with far less machinery.
    """

    def __init__(self, config: Config, *, send=None, interval: float = 30.0, attempts: int = 40):
        super().__init__(name="printer-auto-off", daemon=True)
        self.config = config
        self._send = send or disable_auto_power_off
        self.interval = interval
        self.attempts = attempts
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if self.config.printer_backend == "fake":
            # Nothing to talk to: the fake backend writes preview images.
            return
        for _ in range(self.attempts):
            if self._stop.is_set():
                return
            if self._send(self.config):
                return
            if self._stop.wait(self.interval):
                return
        log.info("gave up waiting for the printer to disable auto power-off")


class PrintFailed(RuntimeError):
    """The printer was asked to print and could not."""


class PrinterBackend(Protocol):
    def print_label(self, image: Image.Image, *, code: str, copies: int = 1) -> Path: ...


def _find_brother_device():
    import usb.core

    return usb.core.find(idVendor=BROTHER_VENDOR)


def status(config: Config, find_device=None) -> dict:
    """What the UI needs to say about printing right now.

    `prints` is separate from `ready` on purpose. The fake backend is perfectly
    ready and prints nothing, and reporting that as simply "ready" is what let a
    dead Print button look healthy.
    """
    backend = config.printer_backend

    if backend == "fake":
        return {
            "backend": backend,
            "ready": True,
            "prints": False,
            "detail": "Preview only - labels are saved as images, not printed.",
        }

    if backend == "cups_raw":
        if not config.printer_queue:
            return {
                "backend": backend,
                "ready": False,
                "prints": True,
                "detail": "No CUPS queue configured; set MOVING_PRINTER_QUEUE.",
            }
        return {
            "backend": backend,
            "ready": True,
            "prints": True,
            "detail": f"Sending raw jobs to the CUPS queue {config.printer_queue}.",
        }

    find_device = find_device or _find_brother_device
    try:
        device = find_device()
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never raised
        return {
            "backend": backend,
            "ready": False,
            "prints": True,
            "detail": f"Could not check USB: {exc}",
        }

    if device is None:
        return {
            "backend": backend,
            "ready": False,
            "prints": True,
            "detail": (
                "No Brother printer on USB. Check it is plugged in, switched on, "
                "and that Editor Lite mode is off."
            ),
        }
    return {
        "backend": backend,
        "ready": True,
        "prints": True,
        "detail": f"{config.printer_model} connected over USB.",
    }


def to_raster(image: Image.Image) -> Image.Image:
    """Orient a readable design for the tape.

    The printer always lays PRINTABLE_WIDTH dots across the tape, so a
    landscape design -- laid out along the tape and therefore that many dots
    *tall* -- is rotated a quarter turn. Portrait designs pass through.
    """
    if image.width == PRINTABLE_WIDTH:
        return image
    if image.height == PRINTABLE_WIDTH:
        # expand=True keeps every pixel; -90 puts the start of the design at
        # the leading edge of the tape, so it reads the same way up as the
        # preview once the label is turned.
        return image.rotate(-90, expand=True)
    raise ValueError(
        f"a label must be {PRINTABLE_WIDTH}px on one side to fit 62mm tape; "
        f"this one is {image.width}x{image.height}"
    )


def build_instructions(image: Image.Image, *, model: str, label: str) -> bytes:
    """Convert a rendered label into QL raster instructions.

    Testable without hardware, which is most of the value: it proves the
    geometry is one the printer will accept before any tape is involved.
    """
    # Rotate first: a landscape design is the right size, just the wrong way
    # round, and rejecting it for its width would be wrong.
    image = to_raster(image)
    if image.width != PRINTABLE_WIDTH:
        # brother_ql would try to rescale, and its rescale path calls
        # PIL.Image.ANTIALIAS -- removed in Pillow 10 -- so the real failure
        # would surface as an unrelated AttributeError deep in the library.
        raise ValueError(
            f"label is {image.width}px wide; the QL-800 needs exactly "
            f"{PRINTABLE_WIDTH}px for 62mm tape"
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # brother_ql.devicedependent deprecation
        from brother_ql.conversion import convert
        from brother_ql.raster import BrotherQLRaster

    raster = BrotherQLRaster(model)
    raster.exception_on_warning = True
    return bytes(
        convert(qlr=raster, images=[image], label=label, cut=True, dither=False, rotate="0")
    )


class FakePrinter:
    """Writes a PNG preview instead of printing. The development default."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def print_label(self, image: Image.Image, *, code: str, copies: int = 1) -> Path:
        self.config.label_preview_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.label_preview_dir / f"{code}.png"
        image.save(path)
        return path


class BrotherQLPrinter:
    """Raster straight to the printer over USB."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def print_label(self, image: Image.Image, *, code: str, copies: int = 1) -> Path:
        from brother_ql.backends.helpers import send

        data = build_instructions(
            image, model=self.config.printer_model, label=self.config.label_id
        )
        # One device: two jobs arriving together would interleave their
        # rasters and produce two ruined labels.
        with exclusive():
            for _ in range(copies):
                send(
                    instructions=data,
                    printer_identifier=self.config.printer_queue or "usb://0x04f9:0x209b",
                    backend_identifier="pyusb",
                    blocking=True,
                )
        return Path(f"usb:{code}")


def _lp_command(queue: str | None) -> list[str]:
    """The `lp` invocation for a raw job.

    A queue name is required rather than defaulting: without `-d`, `lp` sends
    to the system default printer, which would push a 40 KB raster at whatever
    laser printer happens to be first in the list.
    """
    if not queue:
        raise ValueError(
            "the cups_raw backend needs MOVING_PRINTER_QUEUE set to the QL-800's CUPS queue name"
        )
    return ["lp", "-d", queue, "-o", "raw"]


class CupsRawPrinter:
    """Pipe raster bytes through CUPS.

    The fallback for when macOS's USB printing class driver has claimed the
    device and pyusb cannot take it.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    def print_label(self, image: Image.Image, *, code: str, copies: int = 1) -> Path:
        data = build_instructions(
            image, model=self.config.printer_model, label=self.config.label_id
        )
        command = _lp_command(self.config.printer_queue)
        for _ in range(copies):
            subprocess.run(command, input=data, check=True)
        return Path(f"cups:{code}")


BACKENDS = {
    "fake": FakePrinter,
    "brother_ql": BrotherQLPrinter,
    "cups_raw": CupsRawPrinter,
}


def get_backend(config: Config) -> PrinterBackend:
    try:
        return BACKENDS[config.printer_backend](config)
    except KeyError:
        raise ValueError(
            f"unknown printer backend {config.printer_backend!r}; "
            f"expected one of {', '.join(sorted(BACKENDS))}"
        ) from None
