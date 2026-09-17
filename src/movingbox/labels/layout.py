"""Render a box label for 62 mm continuous DK-2205 tape.

The geometry is fixed by the hardware: ``brother_ql`` reports label ``62`` as
``dots_printable=(696, 0)`` at 300 dpi, so 696 px wide is the only width that
prints 1:1. The tape is endless, so the height is ours to choose.

The tape is black-only, which removes colour as a way to tell boxes apart
across a room. The destination room is therefore knocked out white on a solid
black band -- the strongest mark available on mono stock, and the thing you
actually read from three metres away.

Everything is drawn in 8-bit greyscale for antialiased text, then thresholded
to 1-bit at the end. Converting directly would dither the text into mush.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont

#: Printable dots across 62 mm tape at 300 dpi, per brother_ql.labels.
PRINTABLE_WIDTH = 696
#: ~90 mm at 300 dpi. The tape is endless, so this is the *cap* on an
#: auto-sized label rather than a fixed height -- a label is cut to its
#: content, and a sparse box should not waste 40 mm of blank tape.
DEFAULT_HEIGHT = 1063
#: Below this there is no room for the QR plus a readable room name.
MIN_HEIGHT = 300

MARGIN = 24
QR_TARGET = 240
BAND_HEIGHT = 120
BAND_PADDING = 20
#: Scratch canvas to lay out on before cropping to the measured height.
_WORK_HEIGHT = 4000

FONT_PATH = Path(__file__).resolve().parent / "fonts" / "Inter.ttf"


@dataclass(frozen=True)
class LabelData:
    code: str
    url: str
    room: str | None = None
    source: str | None = None
    summary: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)
    footer: str | None = None


@lru_cache(maxsize=64)
def _font(size: int, weight: int = 400, optical: int = 32) -> ImageFont.FreeTypeFont:
    """Inter at a given size and weight.

    Bundled rather than resolved from the system so that golden-image tests
    compare against a font that cannot change under a macOS update.
    """
    font = ImageFont.truetype(FONT_PATH, size)
    font.set_variation_by_axes([optical, weight])
    return font


def _fit(draw, text: str, max_width: int, start: int, weight: int, minimum: int = 12):
    """Largest font size at or below ``start`` that keeps ``text`` within width."""
    size = start
    while size > minimum:
        font = _font(size, weight)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return _font(minimum, weight)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _qr(url: str, target: int, border: int = 4) -> Image.Image:
    """A QR drawn at an integer module size.

    Scaling a QR by a non-integer factor blurs module edges and is a common
    reason labels stop scanning, so the size is snapped down to a whole number
    of pixels per module instead of resampled to fit.
    """
    matrix = [list(row) for row in segno.make(url, error="m").matrix]
    modules = len(matrix) + 2 * border
    scale = max(1, target // modules)
    size = modules * scale

    image = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(image)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                left, top = (border + x) * scale, (border + y) * scale
                draw.rectangle([left, top, left + scale - 1, top + scale - 1], fill=0)
    return image


def _chips(draw, x: int, y: int, max_width: int, flags: tuple[str, ...]) -> int:
    """Draw flags as inverted chips, wrapping within ``max_width``.

    Knocked-out white on black rather than a bullet and plain text: FRAGILE is
    the most urgent thing on the label and has to survive being read at a
    glance, upside down, across a room.
    """
    font = _font(30, weight=800)
    pad_x, chip_h, gap = 14, 44, 8
    left, top = x, y
    for flag in flags:
        width = int(draw.textlength(flag, font=font)) + 2 * pad_x
        if left > x and left + width > x + max_width:
            left, top = x, top + chip_h + gap
        draw.rectangle([left, top, left + width, top + chip_h], fill=0)
        draw.text((left + width // 2, top + chip_h // 2), flag, font=font, fill=255, anchor="mm")
        left += width + gap
    return top + chip_h


def render(
    data: LabelData, *, width: int = PRINTABLE_WIDTH, height: int | None = None
) -> Image.Image:
    """Render a label. Returns a 1-bit image ready for the printer.

    With no ``height`` the label is cut to its content, between MIN_HEIGHT and
    DEFAULT_HEIGHT -- the tape is continuous, so a sparse box gets a short
    label instead of a long mostly-blank one. Pass ``height`` for an exact cut.
    """
    if height is not None and height < MIN_HEIGHT:
        raise ValueError(f"height {height} is below the {MIN_HEIGHT}px minimum for a usable label")

    cap = height or DEFAULT_HEIGHT
    canvas = Image.new("L", (width, _WORK_HEIGHT), 255)
    draw = ImageDraw.Draw(canvas)
    inner = width - 2 * MARGIN

    # --- header: code on the left, QR on the right ---
    qr = _qr(data.url, QR_TARGET)
    qr_x = width - MARGIN - qr.width
    canvas.paste(qr, (qr_x, MARGIN))

    text_width = qr_x - MARGIN - 16
    code_font = _fit(draw, data.code, text_width, start=112, weight=800)
    draw.text((MARGIN, MARGIN), data.code, font=code_font, fill=0, anchor="lt")

    y = MARGIN + code_font.size + 16
    if data.flags:
        y = _chips(draw, MARGIN, y, text_width, data.flags)

    y = max(y, MARGIN + qr.height) + 20

    # --- destination room: the sorting cue, inverted for contrast ---
    if data.room:
        draw.rectangle([0, y, width, y + BAND_HEIGHT], fill=0)
        room_font = _fit(draw, data.room.upper(), inner - 2 * BAND_PADDING, start=76, weight=800)
        draw.text(
            (width // 2, y + BAND_HEIGHT // 2),
            data.room.upper(),
            font=room_font,
            fill=255,
            anchor="mm",
        )
        y += BAND_HEIGHT + 20

    if data.source:
        source_font = _font(30, weight=500)
        draw.text((MARGIN, y), f"from: {data.source}", font=source_font, fill=0, anchor="lt")
        y += source_font.size + 14

    # The footer flows directly under the content, but its space is reserved
    # from the cap first so an overlong summary truncates instead of pushing
    # the weight and box-of-N off the end of the tape.
    footer_font = _font(28, weight=500)
    footer_space = (footer_font.size + 16) if data.footer else 0

    if data.summary:
        summary_font = _font(38, weight=400)
        line_height = summary_font.size + 8
        lines = _wrap(draw, data.summary, summary_font, inner)
        room_for = max(0, (cap - MARGIN - footer_space - y) // line_height)
        if len(lines) > room_for:
            lines = lines[:room_for]
            if lines:
                lines[-1] = lines[-1].rstrip(" ,") + "..."
        for line in lines:
            draw.text((MARGIN, y), line, font=summary_font, fill=0, anchor="lt")
            y += line_height

    if data.footer:
        y += 16
        draw.text((MARGIN, y), data.footer, font=footer_font, fill=0, anchor="lt")
        y += footer_font.size

    final_height = height or min(max(y + MARGIN, MIN_HEIGHT), DEFAULT_HEIGHT)
    canvas = canvas.crop((0, 0, width, final_height))

    # Threshold rather than convert("1") directly, which would dither.
    return canvas.point(lambda p: 255 if p > 128 else 0).convert("1")


def from_box(box: dict, *, base_url: str, room_name: str | None = None) -> LabelData:
    """Build label content from a box row."""
    flags = []
    if box.get("fragile"):
        flags.append("FRAGILE")
    if box.get("open_first"):
        flags.append("OPEN FIRST")
    if box.get("heavy"):
        flags.append("HEAVY")

    footer_parts = []
    if box.get("group_index") and box.get("group_total"):
        footer_parts.append(f"box {box['group_index']} of {box['group_total']}")
    if box.get("weight_kg"):
        footer_parts.append(f"{box['weight_kg']:g} kg")

    return LabelData(
        code=box["code"],
        url=f"{base_url.rstrip('/')}/b/{box['code']}",
        room=room_name,
        source=box.get("source_location"),
        summary=box.get("content_summary"),
        flags=tuple(flags),
        footer=" - ".join(footer_parts) or None,
    )
