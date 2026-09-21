"""The command line builds and counts a label exactly as the API does."""

import pytest

from movingbox import cli, db, store
from movingbox.labels import layout


@pytest.fixture
def crate_and_bag(config, monkeypatch):
    """A bag headed for the garage, inside a crate headed for the kitchen."""
    monkeypatch.setenv("MOVING_DB_PATH", str(config.db_path))
    monkeypatch.setenv("MOVING_LABEL_PREVIEW_DIR", str(config.label_preview_dir))
    monkeypatch.setenv("MOVING_PRINTER_BACKEND", "fake")
    conn = db.connect(config.db_path)
    kitchen = store.create_room(conn, "Kitchen")["id"]
    garage = store.create_room(conn, "Garage")["id"]
    crate = store.create_box(
        conn, kind="crate", content_summary="pots", destination_room_id=kitchen
    )["code"]
    bag = store.create_box(
        conn,
        kind="bag",
        content_summary="tea towels",
        destination_room_id=garage,
        parent_code=crate,
    )["code"]
    yield conn, bag
    conn.close()


@pytest.fixture
def rooms_named(monkeypatch):
    named = []
    real = layout.from_box

    def spy(box, **fields):
        named.append(fields.get("room_name"))
        return real(box, **fields)

    monkeypatch.setattr(layout, "from_box", spy)
    return named


def test_printing_a_nested_record_names_its_containers_room(crate_and_bag, rooms_named):
    _, bag = crate_and_bag

    assert cli.main(["print", bag]) == 0

    assert rooms_named == ["Kitchen"]


def test_previewing_a_nested_record_names_its_containers_room(crate_and_bag, rooms_named, tmp_path):
    _, bag = crate_and_bag

    assert cli.main(["preview", bag, "-o", str(tmp_path / "bag.png")]) == 0

    assert rooms_named == ["Kitchen"]


def test_printing_counts_every_copy(crate_and_bag):
    conn, bag = crate_and_bag

    assert cli.main(["print", bag, "--copies", "3"]) == 0

    assert store.get_box(conn, bag)["label_print_count"] == 3
