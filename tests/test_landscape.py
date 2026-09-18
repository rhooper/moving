"""Landscape labels: 3 inches along the tape, all identity."""

import pytest

from movingbox.labels import layout, printer

pyzbar = pytest.importorskip("pyzbar.pyzbar", reason="needs `brew install zbar`")


def a_label(**overrides):
    fields = {
        "code": "B-0042",
        "url": "https://test.example.ts.net/b/B-0042",
        "room": "Kitchen",
        "source": "Basement shelf 3",
        "summary": "pots, pans, kettle",
        "flags": ("FRAGILE",),
        "footer": "box 3 of 5 - 12.4 kg",
    }
    fields.update(overrides)
    return layout.LabelData(**fields)


def decode(image):
    # QR only: the label also carries a Code 128 of the box number.
    found = pyzbar.decode(image, symbols=[pyzbar.ZBarSymbol.QRCODE])
    return found[0].data.decode() if found else ""


def test_a_landscape_label_is_three_point_three_inches_along_the_tape(conn):
    image = layout.render(a_label(), orientation="landscape")

    # 990 dots at 300 dpi = 3.3 inches; 696 dots is the tape's printable width.
    assert (image.width, image.height) == (layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH)
    assert layout.LANDSCAPE_LENGTH == 990


def test_the_design_is_readable_before_the_printer_rotates_it(conn):
    # What you preview on screen reads normally; rotation is the printer's
    # business, not the layout's.
    image = layout.render(a_label(), orientation="landscape")

    assert image.width > image.height


def test_the_raster_is_rotated_to_the_tape_width(conn):
    design = layout.render(a_label(), orientation="landscape")

    raster = printer.to_raster(design)

    assert raster.width == layout.PRINTABLE_WIDTH
    assert raster.height == layout.LANDSCAPE_LENGTH


def test_a_portrait_label_is_passed_through_untouched(conn):
    design = layout.render(a_label(), orientation="portrait")

    raster = printer.to_raster(design)

    assert raster.size == design.size
    assert raster.width == layout.PRINTABLE_WIDTH


def test_the_qr_survives_rotation(conn):
    # The single property the whole system rests on, re-checked after the
    # image is turned on its side.
    data = a_label()

    raster = printer.to_raster(layout.render(data, orientation="landscape"))

    assert decode(raster) == data.url


def test_the_label_has_no_field_for_itemised_contents(conn):
    # Removed on purpose: the list is one scan away in the app, and the tape is
    # for finding the box. If someone adds the field back, this says why not.
    assert "items" not in layout.LabelData.__dataclass_fields__


def test_a_label_with_no_summary_still_renders(conn):
    image = layout.render(a_label(summary=None), orientation="landscape")

    assert decode(image) == a_label().url


def test_landscape_rendering_is_deterministic(conn):
    first = layout.render(a_label(), orientation="landscape")
    second = layout.render(a_label(), orientation="landscape")

    assert first.tobytes() == second.tobytes()


def test_an_unknown_orientation_is_rejected(conn):
    with pytest.raises(ValueError):
        layout.render(a_label(), orientation="sideways")


def test_build_instructions_accepts_a_landscape_design(conn):
    # It must rotate before the 696px width check, or a valid landscape label
    # would be rejected as the wrong size.
    design = layout.render(a_label(), orientation="landscape")

    data = printer.build_instructions(design, model="QL-800", label="62")

    assert data[:3] == b"\x1b\x69\x61"


# --- the fixed layout ---------------------------------------------------------
#
# Asked for, after a round of stepped scaling: no dynamic font sizing. Every
# label sets the same element at the same size, so a shelf of boxes reads as
# one system. What made that possible is the 3.3 inch length and chips that
# sit side by side instead of stacking.

ROOMS = ["Living Room", "Dining Room", "Kitchen", "Bathroom", "Basement",
         "Office", "Guest Room", "Main Bedroom", "Garage"]
CODES = ["B-0042", "D001", "Z06-001", "CAM-001"]


def test_type_sizes_never_depend_on_the_content(conn):
    reference = layout.type_sizes(a_label())
    variations = [
        a_label(flags=()),
        a_label(flags=("FRAGILE", "HEAVY")),
        a_label(flags=("FRAGILE", "OPEN FIRST", "HEAVY")),
        a_label(summary="kettle"),
        a_label(summary="pots, pans, " * 30),
        a_label(summary=None, title="Bicycle (Trek hybrid)"),
        *[a_label(room=room) for room in ROOMS],
        *[a_label(code=code) for code in CODES],
    ]
    for label in variations:
        assert layout.type_sizes(label) == reference, label


def test_a_loose_things_name_is_set_like_any_summary(conn):
    # "Bicycle (Trek hybrid)" used to print huge because it was short.
    sizes = layout.type_sizes(a_label(summary=None, title="Bicycle (Trek hybrid)"))

    assert sizes["title"] == sizes["summary"]


def test_the_qr_sits_flush_in_the_top_right_corner(conn):
    image = layout.render(a_label(), orientation="landscape").convert("L")

    # The corner finder pattern starts one quiet zone in from both edges, and
    # nowhere near the old 24 px margin.
    quiet = layout.QR_QUIET * layout.QR_MODULE
    assert image.getpixel((image.width - quiet - 2, quiet + 1)) == 0
    assert quiet < 24


def test_the_qr_is_drawn_at_a_whole_number_of_pixels_per_module(conn):
    # The previous "bigger QR" changed a target number and nothing on the tape,
    # because the size snaps to whole pixels. So the module size is the setting.
    assert layout.QR_MODULE == 6


def test_fragile_and_heavy_share_one_row(conn):
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("L", (layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH), 255))
    room = layout.LANDSCAPE_LENGTH - 2 * layout.MARGIN
    one = layout._chips(draw, layout.MARGIN, 100, room, ("FRAGILE",), layout.CHIP_SIZE)
    two = layout._chips(draw, layout.MARGIN, 100, room, ("FRAGILE", "HEAVY"), layout.CHIP_SIZE)

    assert one == two


def test_open_first_is_a_border_not_a_chip(conn):
    plain = layout.render(a_label(flags=("FRAGILE",)), orientation="landscape").convert("L")
    first = layout.render(
        a_label(flags=("FRAGILE", "OPEN FIRST")), orientation="landscape").convert("L")

    # Low on the left edge: clear of the code, the chips and the room band.
    y = plain.height - 60
    line, gap = layout.BORDER_LINE, layout.BORDER_GAP
    assert plain.getpixel((0, y)) == 255
    # A double rule, run right to the very edge of the tape: ink at pixel 0,
    # white between the two lines, ink again for the inner one.
    assert first.getpixel((0, y)) == 0
    assert first.getpixel((line + gap // 2, y)) == 255
    assert first.getpixel((line + gap + 1, y)) == 0
    # ...and clear of the text, which starts at the margin.
    assert 2 * line + gap < layout.MARGIN
    # ...and it took no space: the chips row is where it was.
    bordered = a_label(flags=("FRAGILE", "OPEN FIRST"))
    assert layout.type_sizes(bordered) == layout.type_sizes(a_label())


def test_nothing_runs_off_the_bottom_of_the_tape(conn):
    crowded = a_label(flags=("FRAGILE", "HEAVY"), room="Main Bedroom", summary="pots, " * 40)
    image = layout.render(crowded, orientation="landscape").convert("L")

    bottom = image.crop((0, image.height - 8, image.width, image.height))
    assert bottom.getextrema() == (255, 255), "ink in the bottom margin: something overflowed"


@pytest.mark.parametrize("flags", [(), ("FRAGILE",), ("FRAGILE", "HEAVY"),
                                   ("FRAGILE", "OPEN FIRST", "HEAVY")])
def test_the_summary_always_has_room(conn, flags):
    with_summary = layout.render(a_label(flags=flags), orientation="landscape")
    without = layout.render(a_label(flags=flags, summary=None), orientation="landscape")

    assert with_summary.tobytes() != without.tobytes()


def test_a_code_too_long_to_fit_shrinks_rather_than_hitting_the_qr(conn):
    # The one exception to fixed sizes, and it is a guard, not a design: a
    # prefix nobody has configured yet must not print over the QR.
    sizes = layout.type_sizes(a_label(code="WAREHOUSE-000123"))

    assert sizes["code"] < layout.type_sizes(a_label())["code"]


# --- the room band holds still; the chips live at the bottom -----------------


def band_top(image):
    """The first row that is ink from edge to edge: the top of the room band."""
    grey = image.convert("L")
    for y in range(grey.height):
        if grey.crop((0, y, grey.width, y + 1)).getextrema() == (0, 0):
            return y
    raise AssertionError("no room band found")


@pytest.mark.parametrize("flags", [("FRAGILE",), ("HEAVY",), ("FRAGILE", "HEAVY")])
def test_the_room_band_does_not_move_when_a_box_is_flagged(conn, flags):
    plain = layout.render(a_label(flags=()), orientation="landscape")
    flagged = layout.render(a_label(flags=flags), orientation="landscape")

    assert band_top(flagged) == band_top(plain)


def test_the_chips_sit_at_the_bottom_of_the_label(conn):
    plain = layout.render(a_label(flags=(), summary=None), orientation="landscape").convert("L")
    flagged = layout.render(
        a_label(flags=("FRAGILE",), summary=None), orientation="landscape").convert("L")

    # The strip just above the bottom margin: empty without a flag, inked with.
    strip = (layout.MARGIN, plain.height - layout.MARGIN - 20,
             layout.MARGIN + 200, plain.height - layout.MARGIN - 10)
    assert plain.crop(strip).getextrema() == (255, 255)
    assert flagged.crop(strip).getextrema()[0] == 0


def test_the_summary_stops_short_of_the_chips(conn):
    # A long summary on a flagged box is cut, not printed through the chips:
    # the chip's own black must be the only ink in its row band.
    long = a_label(flags=("FRAGILE",), summary="pots, pans, " * 40)
    image = layout.render(long, orientation="landscape").convert("L")

    chip_top = image.height - layout.MARGIN - round(68 * layout.CHIP_SIZE / 48)
    # Right of where a lone FRAGILE chip ends, level with it: no summary text.
    beside = image.crop((600, chip_top, image.width - layout.MARGIN, image.height - layout.MARGIN))
    assert beside.getextrema() == (255, 255)


def test_the_contents_are_set_two_points_larger(conn):
    # 2 pt at 300 dpi is 8.3 dots: 51 -> 59.
    assert layout.SUMMARY_SIZE == 59
