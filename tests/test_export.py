"""Exports. The data should outlive this application."""

import csv
import io
import json

from movingbox import db, export, store


def populate(config):
    conn = db.connect(config.db_path)
    kitchen = store.create_room(conn, "Kitchen")["id"]
    box = store.create_box(
        conn,
        destination_room_id=kitchen,
        content_summary="pots and pans",
        source_location="Basement shelf 3",
        fragile=1,
    )
    store.add_item(conn, box["code"], name="cafetiere", qty=2)
    store.add_item(conn, box["code"], name="kettle", source="ai")
    store.set_status(conn, box["code"], "packed")
    store.set_location(conn, box["code"], "truck")
    store.create_box(conn, content_summary="garden tools")
    return conn


def test_json_export_nests_items_inside_their_box(config):
    conn = populate(config)

    data = json.loads(export.to_json(conn))
    conn.close()

    first = next(b for b in data["boxes"] if b["code"] == "B-0001")
    assert [i["name"] for i in first["items"]] == ["cafetiere", "kettle"]


def test_json_export_resolves_the_room_to_its_name(config):
    # An export holding only room ids is useless once the database is gone.
    conn = populate(config)

    data = json.loads(export.to_json(conn))
    conn.close()

    first = next(b for b in data["boxes"] if b["code"] == "B-0001")
    assert first["destination_room"] == "Kitchen"


def test_json_export_keeps_ai_provenance(config):
    conn = populate(config)

    data = json.loads(export.to_json(conn))
    conn.close()

    first = next(b for b in data["boxes"] if b["code"] == "B-0001")
    sources = {i["name"]: i["source"] for i in first["items"]}
    assert sources == {"cafetiere": "manual", "kettle": "ai"}


def test_json_export_includes_the_event_timeline(config):
    conn = populate(config)

    data = json.loads(export.to_json(conn))
    conn.close()

    first = next(b for b in data["boxes"] if b["code"] == "B-0001")
    # Items are part of the history too (migration 0012), the photo reader's
    # marked as its own.
    assert [(e["kind"], e["actor"]) for e in first["events"]] == [
        ("create", None),
        ("item-add", None),
        ("item-add", "ai"),
        ("status", None),
        ("location", None),
    ]


def test_json_export_records_when_it_was_taken(config):
    conn = populate(config)

    data = json.loads(export.to_json(conn))
    conn.close()

    assert data["exported_at"]
    assert data["box_count"] == 2


def test_csv_export_is_one_row_per_box(config):
    conn = populate(config)

    rows = list(csv.DictReader(io.StringIO(export.to_csv(conn))))
    conn.close()

    assert [r["code"] for r in rows] == ["B-0001", "B-0002"]


def test_csv_export_flattens_items_into_a_readable_column(config):
    conn = populate(config)

    rows = list(csv.DictReader(io.StringIO(export.to_csv(conn))))
    conn.close()

    assert rows[0]["items"] == "cafetiere x2; kettle"


def test_csv_export_names_the_room_not_its_id(config):
    conn = populate(config)

    rows = list(csv.DictReader(io.StringIO(export.to_csv(conn))))
    conn.close()

    assert rows[0]["destination_room"] == "Kitchen"


def test_csv_export_of_an_empty_database_still_has_a_header(config):
    conn = db.connect(config.db_path)

    text = export.to_csv(conn)
    conn.close()

    assert text.splitlines()[0].startswith("code,")


def test_manifest_groups_boxes_by_destination_room(config):
    conn = populate(config)

    manifest = export.manifest(conn)
    conn.close()

    by_room = {group["room"]: group for group in manifest}
    assert by_room["Kitchen"]["count"] == 1
    # A box with no destination must still be listed, or it goes missing from
    # the count the movers work to.
    assert "Unassigned" in by_room


def test_manifest_totals_the_weight_it_knows_about(config):
    conn = db.connect(config.db_path)
    room = store.create_room(conn, "Garage")["id"]
    store.create_box(conn, destination_room_id=room, weight_kg=10.5)
    store.create_box(conn, destination_room_id=room, weight_kg=4.5)
    store.create_box(conn, destination_room_id=room)  # unweighed

    manifest = export.manifest(conn)
    conn.close()

    garage = next(g for g in manifest if g["room"] == "Garage")
    assert garage["count"] == 3
    assert garage["weight_kg"] == 15.0


def test_manifest_totals_disclose_how_many_boxes_are_unweighed(config):
    conn = db.connect(config.db_path)
    room = store.create_room(conn, "Garage")["id"]
    store.create_box(conn, destination_room_id=room, weight_kg=10.0)
    store.create_box(conn, destination_room_id=room)
    store.create_box(conn, destination_room_id=room)

    totals = export.manifest_totals(export.manifest(conn))
    conn.close()

    # A bare "10 kg" against three boxes reads as the shipment weight.
    assert totals == {"boxes": 3, "weight_kg": 10.0, "unweighed": 2}
