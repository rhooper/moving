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
    found = pyzbar.decode(image)
    return found[0].data.decode() if found else ""


def test_a_landscape_label_is_three_inches_along_the_tape(conn):
    image = layout.render(a_label(), orientation="landscape")

    # 900 dots at 300 dpi = 3 inches; 696 dots is the tape's printable width.
    assert (image.width, image.height) == (layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH)
    assert layout.LANDSCAPE_LENGTH == 900


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


# --- type 50% larger, where the label can afford it --------------------------
#
# Asked for: every font on the label half as big again. A flat 1.5x does not
# survive handling flags -- the chips cannot share a row beside the QR, so two
# flags squeeze the summary out and three push the room band off the tape. So
# the scale steps down in notches, and only as far as it must.


def test_an_ordinary_label_gets_the_full_fifty_percent(conn):
    assert layout.landscape_scale(a_label()) == 1.5
    assert layout.landscape_scale(a_label(flags=())) == 1.5


def test_a_crowded_label_steps_down_rather_than_overflowing(conn):
    crowded = a_label(flags=("FRAGILE", "OPEN FIRST", "HEAVY"))

    assert 1.0 <= layout.landscape_scale(crowded) < 1.5


def test_nothing_runs_off_the_bottom_of_the_tape(conn):
    # The room band is full-width black: if it overflowed, the clipped canvas
    # would carry ink right to the bottom edge.
    crowded = a_label(flags=("FRAGILE", "OPEN FIRST", "HEAVY"), room="Upstairs Back Bedroom")
    image = layout.render(crowded, orientation="landscape").convert("L")

    bottom = image.crop((0, image.height - 8, image.width, image.height))
    assert bottom.getextrema() == (255, 255), "ink in the bottom margin: something overflowed"


@pytest.mark.parametrize("flags", [(), ("FRAGILE",), ("FRAGILE", "HEAVY"),
                                   ("FRAGILE", "OPEN FIRST", "HEAVY")])
def test_the_summary_is_never_squeezed_out(conn, flags):
    with_summary = layout.render(a_label(flags=flags), orientation="landscape")
    without = layout.render(a_label(flags=flags, summary=None), orientation="landscape")

    assert with_summary.tobytes() != without.tobytes()


def test_the_qr_is_not_scaled(conn):
    # "Font size" is type. The QR was sized on tape for scanning distance and
    # a bigger one would take the width the larger box number needs.
    big = layout.render(a_label(), orientation="landscape")

    assert decode(big) == a_label().url
