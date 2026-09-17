"""Landscape labels: ~4 inches along the tape, contents beside the identity."""

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
        "items": ["stock pot", "3 baking pans", "kettle", "2 cutting boards"],
        "flags": ("FRAGILE",),
        "footer": "box 3 of 5 - 12.4 kg",
    }
    fields.update(overrides)
    return layout.LabelData(**fields)


def decode(image):
    found = pyzbar.decode(image)
    return found[0].data.decode() if found else ""


def test_a_landscape_label_is_four_inches_along_the_tape(conn):
    image = layout.render(a_label(), orientation="landscape")

    # 1200 dots at 300 dpi = 4 inches; 696 dots is the tape's printable width.
    assert (image.width, image.height) == (layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH)
    assert layout.LANDSCAPE_LENGTH == 1200


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


def test_the_contents_list_is_rendered(conn):
    # Hard to assert text in a raster, so compare against the same label with
    # no items: the contents column must put ink on the page.
    with_items = layout.render(a_label(), orientation="landscape")
    without = layout.render(a_label(items=[]), orientation="landscape")

    assert with_items.tobytes() != without.tobytes()


def test_a_long_contents_list_is_truncated_not_overflowed(conn):
    data = a_label(items=[f"item number {n}" for n in range(200)])

    image = layout.render(data, orientation="landscape")

    assert (image.width, image.height) == (layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH)


def test_a_box_with_no_items_still_renders(conn):
    image = layout.render(a_label(items=[], summary=None), orientation="landscape")

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
