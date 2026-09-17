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

import subprocess
import warnings
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..config import Config
from .layout import PRINTABLE_WIDTH


class PrinterBackend(Protocol):
    def print_label(self, image: Image.Image, *, code: str, copies: int = 1) -> Path: ...


def build_instructions(image: Image.Image, *, model: str, label: str) -> bytes:
    """Convert a rendered label into QL raster instructions.

    Testable without hardware, which is most of the value: it proves the
    geometry is one the printer will accept before any tape is involved.
    """
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
