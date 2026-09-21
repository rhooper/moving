"""Render a box label for 62 mm continuous DK-2205 tape.

``brother_ql`` reports label ``62`` as ``dots_printable=(696, 0)`` at 300 dpi,
so 696 px is the only width that prints 1:1; the tape is endless, so the length
is ours. The tape is black-only, so the destination room is knocked out white
on a black band.

Drawn in 8-bit greyscale for antialiased text, then thresholded to 1-bit:
converting directly would dither the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont

from .. import kinds
from . import code128

#: Printable dots across 62 mm tape at 300 dpi, per brother_ql.labels.
PRINTABLE_WIDTH = 696
#: ~90 mm at 300 dpi: the cap on a portrait label cut to its content.
DEFAULT_HEIGHT = 1063
#: Below this there is no room for the QR plus a readable room name.
MIN_HEIGHT = 300

MARGIN = 24
QR_TARGET = 240
BAND_HEIGHT = 120
BAND_PADDING = 20
#: Scratch canvas to lay out on before cropping to the measured height.
_WORK_HEIGHT = 4000

#: 3.3 inches at 300 dpi, laid out along the tape; printer.to_raster rotates it.
LANDSCAPE_LENGTH = 990

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
    #: What a loose thing *is* ("Bicycle"), set instead of a summary.
    title: str | None = None


@lru_cache(maxsize=64)
def _font(size: int, weight: int = 400, optical: int = 32) -> ImageFont.FreeTypeFont:
    """Inter at a given size and weight.

    Bundled, so golden-image tests cannot break under a system font update.
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


def _qr(url: str, target: int = 0, border: int = 4, module: int | None = None) -> Image.Image:
    """A QR drawn at a whole number of pixels per module, never resampled.

    `target` is snapped down to a whole module size, so nearby targets draw the
    same QR (210 and 242 are both 5 px). Pass `module` to set the size exactly.
    """
    matrix = [list(row) for row in segno.make(url, error="m").matrix]
    modules = len(matrix) + 2 * border
    scale = module or max(1, target // modules)
    size = modules * scale

    image = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(image)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                left, top = (border + x) * scale, (border + y) * scale
                draw.rectangle([left, top, left + scale - 1, top + scale - 1], fill=0)
    return image


def _fragile_icon(size: int) -> Image.Image:
    """The goblet that means fragile. White on black, to sit inside a chip."""
    image = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(image)
    unit = size / 16

    # Bowl: a cup tapering to the stem.
    draw.polygon(
        [
            (3 * unit, 2 * unit),
            (13 * unit, 2 * unit),
            (9.5 * unit, 8.5 * unit),
            (6.5 * unit, 8.5 * unit),
        ],
        fill=255,
    )
    # Stem and foot.
    draw.rectangle([7.3 * unit, 8.5 * unit, 8.7 * unit, 12.5 * unit], fill=255)
    draw.rectangle([4.5 * unit, 12.5 * unit, 11.5 * unit, 14 * unit], fill=255)
    # A crack, so it reads as *broken* glass rather than a drink.
    draw.line(
        [(11 * unit, 3 * unit), (8.6 * unit, 5 * unit), (10.4 * unit, 6.4 * unit)],
        fill=0,
        width=max(2, int(unit * 0.9)),
    )
    return image


def _heavy_icon(size: int) -> Image.Image:
    """The weight that means heavy. White on black, to sit inside a chip."""
    image = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(image)
    unit = size / 16

    # Handle: narrower than the body, or the whole thing reads as a padlock.
    draw.arc(
        [5.5 * unit, 2 * unit, 10.5 * unit, 7.5 * unit],
        180,
        360,
        fill=255,
        width=max(2, int(unit * 1.2)),
    )
    # Body: a kettlebell-ish trapezoid, clearly wider at the base.
    draw.polygon(
        [
            (5 * unit, 6 * unit),
            (11 * unit, 6 * unit),
            (14 * unit, 14 * unit),
            (2 * unit, 14 * unit),
        ],
        fill=255,
    )
    return image


ICONS = {"FRAGILE": _fragile_icon, "HEAVY": _heavy_icon}


def _chips(draw, x: int, y: int, max_width: int, flags: tuple[str, ...], size: int = 48) -> int:
    """Draw flags as white-on-black chips, wrapping within ``max_width``."""
    scale = size / 48
    font = _font(size, weight=800)
    pad_x, chip_h, gap = round(18 * scale), round(68 * scale), round(10 * scale)
    icon_size = int(chip_h * 0.72)
    left, top = x, y

    for flag in flags:
        icon = ICONS.get(flag)
        text_width = int(draw.textlength(flag, font=font))
        width = text_width + 2 * pad_x + (icon_size + 10 if icon else 0)
        if left > x and left + width > x + max_width:
            left, top = x, top + chip_h + gap

        draw.rectangle([left, top, left + width, top + chip_h], fill=0)
        text_x = left + pad_x
        if icon:
            glyph = icon(icon_size)
            return_y = top + (chip_h - icon_size) // 2
            draw._image.paste(glyph, (text_x, return_y), glyph)
            text_x += icon_size + 10
        draw.text((text_x, top + chip_h // 2), flag, font=font, fill=255, anchor="lm")
        left += width + gap
    return top + chip_h


def render(
    data: LabelData,
    *,
    width: int = PRINTABLE_WIDTH,
    height: int | None = None,
    orientation: str = "landscape",
) -> Image.Image:
    """Render a label as it reads on screen; printer.to_raster rotates it onto the tape."""
    if orientation == "landscape":
        return _render_landscape(data)
    if orientation != "portrait":
        raise ValueError(f"orientation must be 'landscape' or 'portrait', not {orientation!r}")
    return _render_portrait(data, width=width, height=height)


def _render_portrait(
    data: LabelData, *, width: int = PRINTABLE_WIDTH, height: int | None = None
) -> Image.Image:
    """A 1-bit portrait label, cut to its content between MIN_HEIGHT and
    DEFAULT_HEIGHT unless ``height`` gives an exact cut.
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

    # The footer's space is reserved first, so a long summary truncates rather
    # than pushing the footer off the tape.
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


def from_box(
    box: dict,
    *,
    base_url: str,
    room_name: str | None = None,
    source_name: str | None = None,
) -> LabelData:
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

    source = " ".join(part for part in (source_name, box.get("source_location")) if part) or None

    # A single thing's description is its title, not a contents summary.
    container = kinds.holds_contents(box.get("kind") or kinds.DEFAULT)
    summary = box.get("content_summary")

    return LabelData(
        code=box["code"],
        url=f"{base_url.rstrip('/')}/b/{box['code']}",
        room=room_name,
        source=source,
        summary=summary if container else None,
        title=None if container else summary,
        flags=tuple(flags),
        footer=" - ".join(footer_parts) or None,
    )


# --- the landscape label: fixed sizes, no dynamic type --------------------------
#
# Every label sets the same element at the same size, so a shelf of boxes
# reads as one system.
CODE_SIZE = 160  # fits B-0042, Z06-001 and CAM-001 beside the QR
CHIP_SIZE = 72  # FRAGILE and HEAVY share one row at this size
ROOM_SIZE = 108  # fits MAIN BEDROOM, the longest room in the house
SUMMARY_SIZE = 59  # two lines always fit

#: Pixels per QR module; this, not a target size, sets the QR's size (see _qr).
QR_MODULE = 6
#: Modules of quiet zone. Two, not four: the QR sits flush in the corner and
#: the tape's unprintable edge adds white.
QR_QUIET = 2

#: OPEN FIRST is a double rule round the whole label, run to the edge; both
#: lines and the gap stay inside MARGIN, so no text touches them.
BORDER_LINE = 5
BORDER_GAP = 5

#: Where the room band starts, on every label; everything else works around it.
BAND_TOP = 232
#: White above and below the band.
BAND_GAP = 10

#: Code 128 of the box number, for a keyboard-wedge reader. 3 px modules are
#: 10 mil at 300 dpi.
BARCODE_MODULE = 3
BARCODE_HEIGHT = 40
BARCODE_TOP = 162
#: A full ten-module quiet zone clear of the OPEN FIRST rule, so the rule is
#: never read as a bar.
BARCODE_LEFT = 2 * BORDER_LINE + BORDER_GAP + 10 * BARCODE_MODULE


def _landscape_fonts(draw, data: LabelData, width: int, qr_width: int) -> dict:
    """The fonts a label is set in: fixed sizes.

    The one guard: `_fit` shrinks a code or room name only when it cannot
    physically fit beside the QR or on the band.
    """
    code_room = width - qr_width - MARGIN - 16
    band_room = width - 2 * MARGIN - 2 * BAND_PADDING
    return {
        "code": _fit(draw, data.code, code_room, start=CODE_SIZE, weight=800),
        "room": _fit(draw, (data.room or "").upper(), band_room, start=ROOM_SIZE, weight=800),
        "chip": _font(CHIP_SIZE, weight=800),
        "summary": _font(SUMMARY_SIZE, weight=400),
        # A loose thing's name is set like a summary, never fitted large.
        "title": _font(SUMMARY_SIZE, weight=400),
    }


def type_sizes(data: LabelData) -> dict[str, int]:
    """The point size of every element on this label."""
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    qr = _qr(data.url, border=QR_QUIET, module=QR_MODULE)
    fonts = _landscape_fonts(draw, data, LANDSCAPE_LENGTH, qr.width)
    return {name: font.size for name, font in fonts.items()}


def _render_landscape(data: LabelData) -> Image.Image:
    """A 3.3-inch label along the tape: fixed length, fixed type, no item list."""
    width, height = LANDSCAPE_LENGTH, PRINTABLE_WIDTH
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)

    if "OPEN FIRST" in data.flags:
        # Drawn first, so the QR's quiet zone and the room band sit over it.
        for inset in (0, BORDER_LINE + BORDER_GAP):
            draw.rectangle(
                [inset, inset, width - inset - 1, height - inset - 1],
                outline=0,
                width=BORDER_LINE,
            )

    # Flush in the top right corner: no margin, only its own quiet zone.
    qr = _qr(data.url, border=QR_QUIET, module=QR_MODULE)
    canvas.paste(qr, (width - qr.width, 0))

    fonts = _landscape_fonts(draw, data, width, qr.width)
    draw.text((MARGIN, MARGIN), data.code, font=fonts["code"], fill=0, anchor="lt")

    # Skipped, not shrunk, if its quiet zone would reach the QR.
    quiet = 10 * BARCODE_MODULE
    try:
        bars = code128.width(data.code, BARCODE_MODULE)
    except ValueError:
        bars = None  # a character Code 128 set B cannot carry
    if bars is not None and BARCODE_LEFT + bars + quiet <= width - qr.width:
        code128.draw(
            draw,
            BARCODE_LEFT,
            BARCODE_TOP,
            data.code,
            module=BARCODE_MODULE,
            height=BARCODE_HEIGHT,
        )
    # Only a QR taller than expected (a much longer base URL) moves the band.
    y = max(BAND_TOP, qr.height + BAND_GAP)

    # Anchored to the bottom margin, whatever else is on the label.
    floor = height - MARGIN
    chips = tuple(flag for flag in data.flags if flag != "OPEN FIRST")
    if chips:
        chip_height = round(68 * CHIP_SIZE / 48)
        _chips(draw, MARGIN, floor - chip_height, width - 2 * MARGIN, chips, CHIP_SIZE)
        floor -= chip_height + BAND_GAP

    if data.room:
        band_height = int(ROOM_SIZE * 1.66)
        draw.rectangle([0, y, width, y + band_height], fill=0)
        draw.text(
            (width // 2, y + band_height // 2),
            data.room.upper(),
            font=fonts["room"],
            fill=255,
            anchor="mm",
        )
        y += band_height + BAND_GAP

    # Source, weight and box-of-N are not printed on this layout.
    text = data.title or data.summary
    if text:
        font = fonts["title" if data.title else "summary"]
        line_height = font.size + 6
        # Stops short of the chips.
        room_for = max(0, (floor - y) // line_height)
        lines = _wrap(draw, text, font, width - 2 * MARGIN)
        if len(lines) > room_for:
            lines = lines[:room_for]
            if lines:
                lines[-1] = lines[-1].rstrip(" ,") + "..."
        for line in lines:
            draw.text((MARGIN, y), line, font=font, fill=0, anchor="lt")
            y += line_height

    return canvas.point(lambda p: 255 if p > 128 else 0).convert("1")


# --- the stub: one inch, number and QR ---------------------------------------------
#
# Reads *across* the tape, a quarter turn from the main label; being already
# the tape's width, printer.to_raster passes it through unrotated.
STUB_LENGTH = 300  # 1 inch at 300 dpi
STUB_CODE_SIZE = 105  # fits B-0042 beside the QR; longer codes shrink
STUB_QR_MODULE = 7  # 259 px for the codes in use, inside the stub's 300


def render_stub(data: LabelData) -> Image.Image:
    """The number and the QR only: a stub is printed before anything is packed."""
    width, height = PRINTABLE_WIDTH, STUB_LENGTH
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)

    qr = _qr(data.url, border=QR_QUIET, module=STUB_QR_MODULE)
    canvas.paste(qr, (width - qr.width, (height - qr.height) // 2))

    font = _fit(draw, data.code, width - qr.width - MARGIN - 12, start=STUB_CODE_SIZE, weight=800)
    draw.text((MARGIN, height // 2), data.code, font=font, fill=0, anchor="lm")

    return canvas.point(lambda p: 255 if p > 128 else 0).convert("1")
