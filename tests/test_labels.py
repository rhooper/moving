"""Label rendering for 62 mm continuous DK-2205 tape on a QL-800.

These cover the *portrait* renderer, which is cut to content along the tape.
Landscape (the default, a fixed 4 inches) is covered in test_landscape.py, so
every render() call here passes orientation explicitly.
"""

import pytest

from movingbox.labels import layout

# zbar is the decoder real barcode scanners are built on. Needs `brew install
# zbar`; conftest points ctypes at Homebrew's prefix so no env var is required.
pyzbar = pytest.importorskip(
    "pyzbar.pyzbar", reason="zbar not available -- run `brew install zbar`"
)


def decode_qr(image) -> str:
    """Read the QR back out of a rendered label, the way a phone would."""
    found = pyzbar.decode(image)
    return found[0].data.decode() if found else ""


def a_label(**overrides) -> layout.LabelData:
    fields = {
        "code": "B-0042",
        "url": "https://test.example.ts.net/b/B-0042",
        "room": "Kitchen",
        "source": "Basement shelf 3",
        "summary": "pots, baking pans, stand mixer, 2 cutting boards",
        "flags": ("FRAGILE",),
        "footer": "box 3 of 5 · 12.4 kg",
    }
    fields.update(overrides)
    return layout.LabelData(**fields)


def test_label_is_the_printable_width_of_62mm_tape(conn):
    # brother_ql reports dots_printable=(696, 0) for label '62'. Rendering any
    # other width means the printer scales or clips it.
    image = layout.render(a_label(), orientation="portrait")

    assert image.width == layout.PRINTABLE_WIDTH == 696


def test_label_is_monochrome_for_black_only_tape(conn):
    image = layout.render(a_label(), orientation="portrait")

    assert image.mode == "1"


def test_the_rendered_qr_decodes_back_to_the_box_url(conn):
    # The single property the whole system depends on. If placement, scaling or
    # the quiet zone are wrong the label looks fine and scans not at all.
    data = a_label()

    assert decode_qr(layout.render(data, orientation="portrait")) == data.url


def test_the_qr_still_decodes_with_a_long_url(conn):
    data = a_label(url="https://moving.example.ts.net/b/B-0042?from=scan&ref=truck")

    assert decode_qr(layout.render(data, orientation="portrait")) == data.url


def test_a_summary_far_too_long_is_truncated_not_overflowed(conn):
    data = a_label(summary="thing, " * 400)

    image = layout.render(data, orientation="portrait")

    # Never longer than the cap, but it should have filled the tape rather than
    # been truncated down to nothing.
    assert image.height <= layout.DEFAULT_HEIGHT
    assert image.height > layout.DEFAULT_HEIGHT - 60
    # Truncation must not eat the footer: the bottom strip stays partly white.
    bottom = image.crop((0, image.height - 40, image.width, image.height)).convert("L")
    assert bottom.getextrema()[1] == 255


def test_a_box_with_almost_no_data_still_renders(conn):
    sparse = layout.LabelData(code="B-0001", url="https://test.example.ts.net/b/B-0001")

    image = layout.render(sparse, orientation="portrait")

    assert decode_qr(image) == sparse.url


def test_a_very_long_room_name_is_shrunk_to_fit(conn):
    data = a_label(room="Upstairs Back Bedroom Wardrobe")

    image = layout.render(data, orientation="portrait")

    assert image.width == layout.PRINTABLE_WIDTH


def test_rendering_is_deterministic(conn):
    # Golden-image comparison is only meaningful if the same input gives the
    # same bytes -- hence a bundled font rather than a system one.
    first = layout.render(a_label(), orientation="portrait")
    second = layout.render(a_label(), orientation="portrait")

    assert first.tobytes() == second.tobytes()


def test_label_height_can_be_overridden_for_a_longer_cut(conn):
    image = layout.render(a_label(), height=1400, orientation="portrait")

    assert image.height == 1400


class TestFromBox:
    def test_the_url_is_built_from_the_configured_base(self, conn):
        box = {"code": "B-0007", "fragile": 0, "open_first": 0, "heavy": 0}

        data = layout.from_box(box, base_url="https://test.example.ts.net")

        assert data.url == "https://test.example.ts.net/b/B-0007"

    def test_flags_come_from_the_box_booleans(self, conn):
        box = {"code": "B-0007", "fragile": 1, "open_first": 1, "heavy": 0}

        data = layout.from_box(box, base_url="https://x.test")

        assert data.flags == ("FRAGILE", "OPEN FIRST")

    def test_the_footer_shows_grouping_and_weight(self, conn):
        box = {
            "code": "B-0007",
            "fragile": 0,
            "open_first": 0,
            "heavy": 0,
            "group_index": 3,
            "group_total": 5,
            "weight_kg": 12.4,
        }

        data = layout.from_box(box, base_url="https://x.test")

        assert "3 of 5" in data.footer
        assert "12.4" in data.footer


@pytest.mark.parametrize("bad", [0, -5, 100])
def test_an_unprintable_height_is_rejected(conn, bad):
    with pytest.raises(ValueError):
        layout.render(a_label(), height=bad, orientation="portrait")


class TestSourceOnLabel:
    """The `from:` line joins the source room with the detail inside it."""

    def test_the_room_and_the_detail_within_it_are_joined(self, conn):
        box = {
            "code": "B-1",
            "fragile": 0,
            "open_first": 0,
            "heavy": 0,
            "source_location": "shelf 3",
        }

        data = layout.from_box(box, base_url="https://x.test", source_name="Basement")

        assert data.source == "Basement shelf 3"

    def test_a_source_room_alone_is_enough(self, conn):
        box = {"code": "B-1", "fragile": 0, "open_first": 0, "heavy": 0}

        data = layout.from_box(box, base_url="https://x.test", source_name="Garage")

        assert data.source == "Garage"

    def test_a_detail_alone_still_works(self, conn):
        # Boxes created before source rooms existed only have free text.
        box = {
            "code": "B-1",
            "fragile": 0,
            "open_first": 0,
            "heavy": 0,
            "source_location": "under the stairs",
        }

        data = layout.from_box(box, base_url="https://x.test")

        assert data.source == "under the stairs"

    def test_neither_leaves_the_line_off_entirely(self, conn):
        box = {"code": "B-1", "fragile": 0, "open_first": 0, "heavy": 0}

        assert layout.from_box(box, base_url="https://x.test").source is None


class TestContentsColumnText:
    def test_a_quantity_pluralises_like_the_summary_does(self, conn):
        # "3 baking pan" in the contents column beside "3 baking pans" in the
        # summary reads as a bug, because it is one.
        assert layout.display_items([{"name": "baking pan", "qty": 3}]) == ["3 baking pans"]

    def test_a_single_thing_is_not_counted(self, conn):
        assert layout.display_items([{"name": "kettle", "qty": 1}]) == ["kettle"]

    def test_an_already_plural_word_is_left_alone(self, conn):
        assert layout.display_items([{"name": "scissors", "qty": 2}]) == ["2 scissors"]
