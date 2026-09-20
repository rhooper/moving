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

from .. import kinds
from . import code128

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

#: 3.3 inches at 300 dpi. A landscape label is laid out along the tape rather
#: than across it, so the code, QR and room band get the long dimension.
#: The printer still lays 696 dots across the tape, so the design is rotated at
#: raster time -- see printer.to_raster.
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
    #: Itemised contents, already rendered as display strings ("3 baking pans").
    #: What a loose thing *is* ("Bicycle"). Set instead of a contents list, and
    #: printed as the headline rather than as a line of small print.
    title: str | None = None


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


def _qr(url: str, target: int = 0, border: int = 4, module: int | None = None) -> Image.Image:
    """A QR drawn at an integer module size.

    Scaling a QR by a non-integer factor blurs module edges and is a common
    reason labels stop scanning, so the size is snapped down to a whole number
    of pixels per module instead of resampled to fit.

    That snapping makes `target` a trap: 210 and 242 both come out at 5 px per
    module, so "make the QR 15% bigger" once changed a number and nothing on
    the tape. Pass `module` to say what you actually mean.
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
    """A weight. Reads instantly and needs no words."""
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
    """Draw flags as inverted chips, wrapping within ``max_width``.

    Knocked-out white on black rather than a bullet and plain text: FRAGILE is
    the most urgent thing on the label and has to survive being read at a
    glance, upside down, across a room.
    """
    # 60% larger than it was: FRAGILE is the most urgent thing on the label and
    # was losing to the box number.
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
            # The canvas is greyscale and the chip is black, so pasting the
            # white-on-black glyph straight in is the whole job.
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
    """Render a label as a readable image.

    The result reads normally on screen whichever orientation is used;
    rotating a landscape design onto the tape is the printer's job
    (printer.to_raster), so that a preview is never shown sideways.
    """
    if orientation == "landscape":
        return _render_landscape(data)
    if orientation != "portrait":
        raise ValueError(f"orientation must be 'landscape' or 'portrait', not {orientation!r}")
    return _render_portrait(data, width=width, height=height)


def _render_portrait(
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


def from_box(
    box: dict,
    *,
    base_url: str,
    room_name: str | None = None,
    source_name: str | None = None,
) -> LabelData:
    """Build label content from a box row.

    The `from:` line joins the source room and the detail within it, so
    "Basement" plus "shelf 3" reads as "Basement shelf 3" on the tape.
    """
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

    # A loose thing is named, not inventoried: its description becomes the
    # headline rather than a summary line.
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
# reads as one system. These are the 50%-larger sizes; what lets them be fixed
# is the 3.3 inch length and chips that sit side by side instead of stacking.
CODE_SIZE = 160  # fits B-0042, Z06-001 and CAM-001 beside the QR
CHIP_SIZE = 72  # FRAGILE and HEAVY share one row at this size
ROOM_SIZE = 108  # fits MAIN BEDROOM, the longest room in the house
SUMMARY_SIZE = 59  # 51 + 2 pt (8 dots at 300 dpi); two lines always fit

#: Pixels per QR module, and modules of quiet zone. The module size *is* the
#: QR's size -- see _qr. 6 px is the next real step up from 5 (+20%).
QR_MODULE = 6
#: Two modules rather than the textbook four: the QR sits flush in the corner,
#: and the tape's own unprintable edge supplies more white above it.
QR_QUIET = 2

#: OPEN FIRST is a double rule around the whole label rather than a chip: it
#: takes no room from anything else, and it reads from further away than a word
#: does. Run right to the very edge of the tape; both lines and the gap between
#: them stay inside MARGIN, so no text ever touches them.
BORDER_LINE = 5
BORDER_GAP = 5

#: Where the room band starts, on every label. Fixed rather than derived: the
#: chips, the barcode and the summary all work around it, never the reverse.
BAND_TOP = 232
#: White above and below the band.
BAND_GAP = 10

#: Code 128 of the box number, in the gap between the number and the band --
#: for a keyboard-wedge reader, which types what it scans. Thin on purpose; 3 px
#: modules are 10 mil at 300 dpi, comfortable for any laser or CCD reader.
BARCODE_MODULE = 3
BARCODE_HEIGHT = 40
BARCODE_TOP = 162
#: Clear of the OPEN FIRST double rule by a full ten-module quiet zone, on
#: every label, so the rule is never read as another bar.
BARCODE_LEFT = 2 * BORDER_LINE + BORDER_GAP + 10 * BARCODE_MODULE


def _landscape_fonts(draw, data: LabelData, width: int, qr_width: int) -> dict:
    """The fonts a label is set in. Fixed -- with one guard.

    `_fit` starts at the fixed size and only shrinks when the text physically
    cannot fit: a code prefix nobody has configured yet must not print over
    the QR, and a new room name must not run off the band. For the codes and
    rooms in use it never triggers, and a test pins that.
    """
    code_room = width - qr_width - MARGIN - 16
    band_room = width - 2 * MARGIN - 2 * BAND_PADDING
    return {
        "code": _fit(draw, data.code, code_room, start=CODE_SIZE, weight=800),
        "room": _fit(draw, (data.room or "").upper(), band_room, start=ROOM_SIZE, weight=800),
        "chip": _font(CHIP_SIZE, weight=800),
        "summary": _font(SUMMARY_SIZE, weight=400),
        # A loose thing's name is set exactly like a box's summary. It used to
        # be fitted as large as it would go, so "Bicycle" printed enormous and
        # a longer name did not.
        "title": _font(SUMMARY_SIZE, weight=400),
    }


def type_sizes(data: LabelData) -> dict[str, int]:
    """The point size of every element on this label."""
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    qr = _qr(data.url, border=QR_QUIET, module=QR_MODULE)
    fonts = _landscape_fonts(draw, data, LANDSCAPE_LENGTH, qr.width)
    return {name: font.size for name, font in fonts.items()}


def _render_landscape(data: LabelData) -> Image.Image:
    """A 3.3-inch label, laid out along the tape: all identity, no inventory.

    Fixed length and fixed type. A row of boxes with labels of matching size
    is far easier to read along a shelf. The itemised contents are
    deliberately not printed -- they are one scan away in the app.
    """
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

    # Skipped, not shrunk, if it cannot keep its quiet zone short of the QR: a
    # barcode that does not scan is worse than none.
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
    # The room band starts here on every label. The chips used to sit above it
    # and push it down, so a shelf of boxes had its bands at two heights; they
    # live at the bottom now, and the band holds still.
    # (A QR taller than expected -- a much longer base URL -- pushes it down
    # rather than being printed over.)
    y = max(BAND_TOP, qr.height + BAND_GAP)

    # Anchored to the bottom margin, whatever else is on the label.
    floor = height - MARGIN
    chips = tuple(flag for flag in data.flags if flag != "OPEN FIRST")
    if chips:
        chip_height = round(68 * CHIP_SIZE / 48)
        _chips(draw, MARGIN, floor - chip_height, width - 2 * MARGIN, chips, CHIP_SIZE)
        floor -= chip_height + BAND_GAP

    if data.room:
        # A third of the type's height in padding above and below: the room is
        # what you read from across a room of boxes.
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

    # Where it came from, its weight and its position in a run are all
    # deliberately absent: the space belongs to what is in the box.
    text = data.title or data.summary
    if text:
        font = fonts["title" if data.title else "summary"]
        line_height = font.size + 6
        # `floor` is the bottom margin, or the top of the chips when there are
        # any: the text stops short of them rather than printing through.
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
# For the moment a box is created, before anything is in it. It reads *across*
# the tape -- a quarter turn from the main label -- because that is the only
# way a big number and a scannable QR both fit in an inch. Being already the
# tape's width, printer.to_raster passes it through unrotated.
STUB_LENGTH = 300  # 1 inch at 300 dpi
STUB_CODE_SIZE = 105  # fits B-0042 beside the QR; longer codes shrink
STUB_QR_MODULE = 7  # the largest whole module size that fits in an inch


def render_stub(data: LabelData) -> Image.Image:
    """The number and the QR, and deliberately nothing else.

    Room, flags and summary belong to the full label: a stub is printed before
    packing, when those are empty or about to change.
    """
    width, height = PRINTABLE_WIDTH, STUB_LENGTH
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)

    qr = _qr(data.url, border=QR_QUIET, module=STUB_QR_MODULE)
    canvas.paste(qr, (width - qr.width, (height - qr.height) // 2))

    font = _fit(draw, data.code, width - qr.width - MARGIN - 12, start=STUB_CODE_SIZE, weight=800)
    draw.text((MARGIN, height // 2), data.code, font=font, fill=0, anchor="lm")

    return canvas.point(lambda p: 255 if p > 128 else 0).convert("1")
