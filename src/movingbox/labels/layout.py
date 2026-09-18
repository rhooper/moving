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

from .. import kinds, summarise

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

#: 3 inches at 300 dpi. A landscape label is laid out along the tape rather
#: than across it, giving room for the itemised contents beside the identity.
#: The printer still lays 696 dots across the tape, so the design is rotated at
#: raster time -- see printer.to_raster.
LANDSCAPE_LENGTH = 900
#: Fraction of the length given to the identity block; the rest lists contents.
IDENTITY_SHARE = 0.55

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
    items: list[str] = field(default_factory=list)
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
    draw.arc([5.5 * unit, 2 * unit, 10.5 * unit, 7.5 * unit], 180, 360,
             fill=255, width=max(2, int(unit * 1.2)))
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


def _chips(draw, x: int, y: int, max_width: int, flags: tuple[str, ...]) -> int:
    """Draw flags as inverted chips, wrapping within ``max_width``.

    Knocked-out white on black rather than a bullet and plain text: FRAGILE is
    the most urgent thing on the label and has to survive being read at a
    glance, upside down, across a room.
    """
    # 60% larger than it was: FRAGILE is the most urgent thing on the label and
    # was losing to the box number.
    font = _font(48, weight=800)
    pad_x, chip_h, gap = 18, 68, 10
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


def display_items(items: list[dict]) -> list[str]:
    """Item rows as the short strings that go on the label."""
    lines = []
    for item in items or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        qty = item.get("qty") or 1
        # Same pluralisation as the summary, so the contents column does
        # not read "3 baking pan" beside a summary saying "3 baking pans".
        lines.append(f"{qty} {summarise.plural(name, qty)}" if qty and qty > 1 else name)
    return lines


def from_box(
    box: dict,
    *,
    base_url: str,
    room_name: str | None = None,
    source_name: str | None = None,
    items: list[dict] | None = None,
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
    # headline and no contents column is printed, however many item rows have
    # been attached to it.
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
        items=display_items(items or []) if container else [],
    )


def _render_landscape(data: LabelData) -> Image.Image:
    """A 4-inch label: identity on the left, itemised contents on the right.

    Fixed length rather than cut-to-content. A row of boxes with labels of
    matching size is far easier to read along a shelf, and at this size there
    is room for the contents list that makes opening the right box possible.
    """
    width, height = LANDSCAPE_LENGTH, PRINTABLE_WIDTH
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)

    # With nothing to list, the contents column would be an empty heading over
    # dead space, so the identity takes the whole label instead -- which also
    # gives the room band the full four inches to be read across.
    has_items = bool(data.items)
    split = int(width * IDENTITY_SHARE) if has_items else width
    left_width = split - 2 * MARGIN

    # --- identity column ---
    # 15% bigger: it is scanned in poor light, often at arm's length.
    qr = _qr(data.url, 242)
    qr_x = split - MARGIN - qr.width
    canvas.paste(qr, (qr_x, MARGIN))

    # 20% bigger: the number is how you find the box on a shelf.
    code_font = _fit(draw, data.code, qr_x - MARGIN - 16, start=115, weight=800)
    draw.text((MARGIN, MARGIN), data.code, font=code_font, fill=0, anchor="lt")

    y = MARGIN + code_font.size + 12
    if data.flags:
        y = _chips(draw, MARGIN, y, qr_x - MARGIN - 16, data.flags)
    y = max(y, MARGIN + qr.height) + 14

    if data.room:
        # 25% larger type, and a third of its height in padding above and
        # below: the room is what you read from across a room of boxes.
        room_font = _fit(
            draw, data.room.upper(), left_width - 2 * BAND_PADDING, start=72, weight=800
        )
        band_height = int(room_font.size * 1.66)
        band_right = (split - MARGIN // 2) if has_items else width
        draw.rectangle([0, y, band_right, y + band_height], fill=0)
        draw.text(
            (MARGIN + (left_width - 2 * BAND_PADDING) // 2 + BAND_PADDING, y + band_height // 2),
            data.room.upper(),
            font=room_font,
            fill=255,
            anchor="mm",
        )
        y += band_height + 12

    # Where it came from, its weight and its position in a run are all
    # deliberately absent: at 3 inches the space belongs to what is in the box.

    if data.title:
        # The name of a loose thing is the point of its label -- as big as it
        # can be and still fit, where a box would be listing its contents.
        title_font = _fit(draw, data.title, left_width, start=86, weight=800)
        for line in _wrap(draw, data.title, title_font, left_width)[:2]:
            draw.text((MARGIN, y), line, font=title_font, fill=0, anchor="lt")
            y += title_font.size + 6
        y += 8

    if data.summary:
        summary_font = _font(34, weight=400)
        line_height = summary_font.size + 6
        room_for = max(0, (height - MARGIN - y) // line_height)
        lines = _wrap(draw, data.summary, summary_font, left_width)
        if len(lines) > room_for:
            lines = lines[:room_for]
            if lines:
                lines[-1] = lines[-1].rstrip(" ,") + "..."
        for line in lines:
            draw.text((MARGIN, y), line, font=summary_font, fill=0, anchor="lt")
            y += line_height

    if not has_items:
        return canvas.point(lambda p: 255 if p > 128 else 0).convert("1")

    # --- contents column ---
    draw.line([(split, MARGIN), (split, height - MARGIN)], fill=0, width=2)

    item_x = split + MARGIN
    item_width = width - item_x - MARGIN
    heading = _font(24, weight=700)
    draw.text((item_x, MARGIN), "CONTENTS", font=heading, fill=0, anchor="lt")
    item_y = MARGIN + heading.size + 8

    item_font = _font(32, weight=400)
    line_height = item_font.size + 7
    room_for = max(0, (height - MARGIN - item_y) // line_height)

    shown = data.items[:room_for]
    hidden = len(data.items) - len(shown)
    if hidden > 0 and shown:
        # Spend the last line saying how much is not listed, rather than
        # stopping mid-list and implying the box holds only what is printed.
        shown = shown[:-1]
        hidden = len(data.items) - len(shown)

    for item in shown:
        text = item
        while draw.textlength(text, font=item_font) > item_width and len(text) > 4:
            text = text[:-2]
        if text != item:
            text = text.rstrip(" ,") + "..."
        draw.text((item_x, item_y), text, font=item_font, fill=0, anchor="lt")
        item_y += line_height

    if hidden > 0:
        draw.text(
            (item_x, item_y),
            f"+ {hidden} more",
            font=_font(28, weight=600),
            fill=0,
            anchor="lt",
        )

    return canvas.point(lambda p: 255 if p > 128 else 0).convert("1")
