"""Boxes, tubs and loose items are all labelled records.

A bicycle needs a code, a QR, a destination room, a status and a location just
as much as a box does — it simply has no contents. Making it a `kind` on the
same record rather than a second table means codes, scanning, search, events,
the manifest and the export all keep working with no special cases.
"""

import pytest

from movingbox import kinds, store


def test_a_record_is_a_box_unless_told_otherwise(conn):
    # Every row that existed before this feature is a box.
    assert store.create_box(conn)["kind"] == "box"


@pytest.mark.parametrize("kind", ["box", "tub", "bag", "crate", "item", "furniture"])
def test_each_supported_kind_can_be_created(conn, kind):
    assert store.create_box(conn, kind=kind)["kind"] == kind


def test_an_unknown_kind_is_refused(conn):
    with pytest.raises(ValueError):
        store.create_box(conn, kind="submarine")


def test_the_kind_can_be_changed_afterwards(conn):
    # "I thought it was a box, it's really a crate" has to be fixable.
    box = store.create_box(conn)

    store.update_box(conn, box["code"], kind="crate")

    assert store.get_box(conn, box["code"])["kind"] == "crate"


class TestHoldsContents:
    def test_containers_hold_contents(self):
        assert kinds.holds_contents("box")
        assert kinds.holds_contents("tub")
        assert kinds.holds_contents("crate")
        assert kinds.holds_contents("bag")

    def test_a_loose_thing_does_not(self):
        # A bicycle is not a container; a contents list for one is nonsense.
        assert not kinds.holds_contents("item")
        assert not kinds.holds_contents("furniture")


class TestPrintGate:
    """A loose item is described by what it *is*, not by what is inside it."""

    def test_an_item_with_a_name_can_be_printed(self, conn):
        item = store.create_box(conn, kind="item", content_summary="Bicycle")

        assert store.has_contents(conn, item["code"])

    def test_an_item_with_no_name_cannot(self, conn):
        item = store.create_box(conn, kind="item")

        assert not store.has_contents(conn, item["code"])

    def test_items_inside_a_loose_item_do_not_count_as_a_name(self, conn):
        # Adding contents to a bicycle is a mistake, not a description of it.
        item = store.create_box(conn, kind="item")
        store.add_item(conn, item["code"], name="pedal")

        assert not store.has_contents(conn, item["code"])

    def test_a_container_is_described_by_either(self, conn):
        with_items = store.create_box(conn, kind="tub")
        store.add_item(conn, with_items["code"], name="kettle")
        with_summary = store.create_box(conn, kind="box", content_summary="pots")

        assert store.has_contents(conn, with_items["code"])
        assert store.has_contents(conn, with_summary["code"])


class TestCodePrefixes:
    def test_every_kind_uses_the_global_prefix_by_default(self, conn):
        assert store.create_box(conn, kind="item")["code"] == "B-0001"
        assert store.create_box(conn, kind="box")["code"] == "B-0002"

    def test_a_kind_can_be_given_its_own_prefix(self, conn):
        from movingbox import codes

        codes.set_kind_prefix(conn, "item", "I")

        assert store.create_box(conn, kind="item")["code"] == "I-0001"
        assert store.create_box(conn, kind="box")["code"] == "B-0001"

    def test_each_prefix_counts_independently(self, conn):
        from movingbox import codes

        codes.set_kind_prefix(conn, "item", "I")
        store.create_box(conn, kind="item")

        assert store.create_box(conn, kind="item")["code"] == "I-0002"

    def test_clearing_a_kind_prefix_returns_it_to_the_global_one(self, conn):
        from movingbox import codes

        codes.set_kind_prefix(conn, "item", "I")
        codes.set_kind_prefix(conn, "item", None)

        assert store.create_box(conn, kind="item")["code"] == "B-0001"

    def test_a_kind_prefix_is_validated_like_any_other(self, conn):
        from movingbox import codes

        with pytest.raises(ValueError):
            codes.set_kind_prefix(conn, "item", "I I")


class TestLabel:
    def test_a_container_label_carries_its_contents(self, conn):
        from movingbox.labels import layout

        box = {
            "code": "B-1",
            "kind": "box",
            "fragile": 0,
            "open_first": 0,
            "heavy": 0,
            "content_summary": "pots and pans",
        }

        data = layout.from_box(box, base_url="https://x.test")

        assert data.title is None
        assert data.summary == "pots and pans"

    def test_a_loose_item_is_titled_by_what_it_is(self, conn):
        from movingbox.labels import layout

        item = {
            "code": "I-1",
            "kind": "item",
            "fragile": 0,
            "open_first": 0,
            "heavy": 0,
            "content_summary": "Bicycle",
        }

        data = layout.from_box(item, base_url="https://x.test")

        # The name is the headline, not a line of small print.
        assert data.title == "Bicycle"
        assert data.summary is None

    def test_an_item_label_renders(self, conn):
        from movingbox.labels import layout

        data = layout.LabelData(
            code="I-0007", url="https://x.test/b/I-0007", room="Garage", title="Bicycle"
        )

        image = layout.render(data, orientation="landscape")

        assert (image.width, image.height) == (layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH)
