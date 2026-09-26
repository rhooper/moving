"""Every change to a record is in its history, with before and after, for good.

Asked for as: track dates and times of everything, keep a log. Until
migration 0012 the log had lifecycle events only (create, delete, restore,
status, location, print), and purging a record erased them.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox import analysis, db, storage, store
from movingbox.api.app import create_app

from .schema_history import roll_back_to


def a_jpeg(colour=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


def history(conn, code, *kinds):
    return [
        {k: e[k] for k in ("kind", "field", "ref", "from_value", "to_value", "actor", "note")}
        for e in store.events_for(conn, code)
        if not kinds or e["kind"] in kinds
    ]


def age_the_log(conn, seconds):
    """As if every line so far were written `seconds` ago."""
    conn.execute("UPDATE events SET created_at = datetime(created_at, ?)", (f"-{seconds} seconds",))


class TestEdits:
    def test_a_field_s_old_and_new_values_are_kept(self, conn):
        code = store.create_box(conn, content_summary="pots")["code"]

        store.update_box(conn, code, content_summary="pots and pans", fragile=1)

        assert history(conn, code, "edit") == [
            {
                "kind": "edit",
                "field": "content_summary",
                "ref": None,
                "from_value": "pots",
                "to_value": "pots and pans",
                "actor": None,
                "note": None,
            },
            {
                "kind": "edit",
                "field": "fragile",
                "ref": None,
                "from_value": "0",
                "to_value": "1",
                "actor": None,
                "note": None,
            },
        ]

    def test_a_room_is_logged_by_name(self, conn):
        kitchen = store.create_room(conn, "Kitchen")["id"]
        code = store.create_box(conn)["code"]

        store.update_box(conn, code, destination_room_id=kitchen)

        (edit,) = history(conn, code, "edit")
        assert (edit["field"], edit["from_value"], edit["to_value"]) == (
            "destination_room_id",
            None,
            "Kitchen",
        )

    def test_saving_a_value_that_did_not_change_logs_nothing(self, conn):
        code = store.create_box(conn, content_summary="pots")["code"]

        store.update_box(conn, code, content_summary="pots")

        assert history(conn, code, "edit") == []

    def test_every_line_says_when(self, conn):
        code = store.create_box(conn)["code"]
        store.update_box(conn, code, notes="tape twice")

        assert all(e["created_at"] for e in store.events_for(conn, code))


class TestTypingIsOneChange:
    # Text saves itself every pause in typing; the history should say what a
    # person changed, not every pause they made.

    def test_saves_of_one_field_in_quick_succession_are_one_line(self, conn):
        code = store.create_box(conn, content_summary="pots")["code"]

        for text in ("pots a", "pots and", "pots and pans"):
            store.update_box(conn, code, content_summary=text)

        assert [(e["from_value"], e["to_value"]) for e in history(conn, code, "edit")] == [
            ("pots", "pots and pans")
        ]

    def test_typing_it_back_as_it_was_leaves_no_line(self, conn):
        code = store.create_box(conn, content_summary="pots")["code"]

        store.update_box(conn, code, content_summary="pots!")
        store.update_box(conn, code, content_summary="pots")

        assert history(conn, code, "edit") == []

    def test_a_pause_longer_than_the_window_starts_a_new_line(self, conn):
        code = store.create_box(conn, content_summary="pots")["code"]
        store.update_box(conn, code, content_summary="pots and pans")
        age_the_log(conn, store.MERGE_SECONDS + 1)

        store.update_box(conn, code, content_summary="pots, pans, kettle")

        assert [(e["from_value"], e["to_value"]) for e in history(conn, code, "edit")] == [
            ("pots", "pots and pans"),
            ("pots and pans", "pots, pans, kettle"),
        ]

    def test_anything_logged_in_between_starts_a_new_line(self, conn):
        code = store.create_box(conn, content_summary="pots")["code"]
        store.update_box(conn, code, content_summary="pots and pans")
        store.set_status(conn, code, "packed")

        store.update_box(conn, code, content_summary="pots, pans, kettle")

        assert len(history(conn, code, "edit")) == 2

    def test_another_field_is_its_own_line(self, conn):
        code = store.create_box(conn)["code"]

        store.update_box(conn, code, notes="tape twice")
        store.update_box(conn, code, source_location="garage")

        assert [e["field"] for e in history(conn, code, "edit")] == ["notes", "source_location"]


class TestNesting:
    def test_moving_in_and_out_is_logged_with_both_containers(self, conn):
        crate = store.create_box(conn, kind="crate")["code"]
        tub = store.create_box(conn, kind="tub")["code"]
        bag = store.create_box(conn, kind="bag", parent_code=crate)["code"]

        store.set_parent(conn, bag, tub)
        store.set_parent(conn, bag, None)

        assert history(conn, bag, "create")[0]["note"] == f"inside {crate}"
        assert [(e["from_value"], e["to_value"]) for e in history(conn, bag, "nest")] == [
            (crate, tub),
            (tub, None),
        ]


class TestItems:
    def test_adding_changing_and_removing_an_item(self, conn):
        code = store.create_box(conn)["code"]
        item = store.add_item(conn, code, name="kettle")
        store.update_item(conn, item["id"], name="electric kettle")
        age_the_log(conn, store.MERGE_SECONDS + 1)
        store.update_item(conn, item["id"], qty=2)
        store.delete_item(conn, item["id"])

        ref = f"item:{item['id']}"
        assert [
            (e["kind"], e["field"], e["ref"], e["from_value"], e["to_value"])
            for e in history(conn, code, "item-add", "item-edit", "item-remove")
        ] == [
            ("item-add", None, ref, None, "kettle"),
            ("item-edit", "name", ref, "kettle", "electric kettle"),
            ("item-edit", "qty", ref, "1", "2"),
            ("item-remove", None, ref, "electric kettle", None),
        ]

    def test_what_the_photo_reader_adds_is_marked_as_its(self, conn):
        code = store.create_box(conn)["code"]
        item = store.add_item(conn, code, name="mug", source="ai")
        store.update_item(conn, item["id"], qty=3, keep_source=True)

        assert [e["actor"] for e in history(conn, code, "item-add", "item-edit")] == ["ai", "ai"]

    def test_a_count_is_noted_when_it_is_not_one(self, conn):
        code = store.create_box(conn)["code"]
        store.add_item(conn, code, name="mug", qty=4)

        assert history(conn, code, "item-add")[0]["note"] == "qty 4"


class TestPhotos:
    def test_adding_captioning_covering_and_removing(self, conn, config):
        code = store.create_box(conn)["code"]
        first = storage.save_photo(conn, config, code, a_jpeg((1, 1, 1)))
        second = storage.save_photo(conn, config, code, a_jpeg((200, 2, 2)))
        storage.set_caption(conn, second["id"], "the shelf")
        storage.set_cover(conn, second["id"])
        storage.delete_photo(conn, config, second["id"])

        one, two = f"photo:{first['id']}", f"photo:{second['id']}"
        assert [
            (e["kind"], e["ref"], e["from_value"], e["to_value"], e["note"])
            for e in history(conn, code, "photo-add", "photo-edit", "cover", "photo-remove")
        ] == [
            ("photo-add", one, None, first["filename"], None),
            ("photo-add", two, None, second["filename"], None),
            ("photo-edit", two, None, "the shelf", None),
            ("cover", None, one, two, None),
            ("photo-remove", two, second["filename"], None, f"cover passed to {one}"),
        ]

    def test_a_retried_upload_of_the_same_bytes_logs_nothing_more(self, conn, config):
        code = store.create_box(conn)["code"]
        storage.save_photo(conn, config, code, a_jpeg())
        storage.save_photo(conn, config, code, a_jpeg())

        assert len(history(conn, code, "photo-add")) == 1


class TestTheSummaryTheReaderWrites:
    def test_is_logged_as_the_reader_s(self, conn):
        code = store.create_box(conn)["code"]
        store.add_item(conn, code, name="kettle", source="ai")

        assert analysis.refresh_summary(conn, code) is True

        (edit,) = history(conn, code, "edit")
        assert (edit["field"], edit["from_value"], edit["actor"]) == ("content_summary", None, "ai")
        assert edit["to_value"]


class TestAfterAPurge:
    def purged(self, conn, config):
        code = store.create_box(conn, content_summary="old paint")["code"]
        store.add_item(conn, code, name="paint tin")
        store.delete_box(conn, config, code)
        store.purge_box(conn, config, code)
        return code

    def test_the_history_is_still_there_ending_with_the_purge(self, conn, config):
        code = self.purged(conn, config)

        assert [e["kind"] for e in history(conn, code)] == [
            "create",
            "item-add",
            "delete",
            "purge",
        ]
        assert store.get_box(conn, code, include_deleted=True) is None

    def test_it_can_be_read_over_the_api(self, config):
        conn = db.connect(config.db_path)
        code = self.purged(conn, config)
        conn.close()

        with TestClient(create_app(config)) as client:
            found = client.get(f"/api/boxes/{code}/events")
            never = client.get("/api/boxes/B-9999/events")

        assert found.status_code == 200
        assert found.json()[-1]["kind"] == "purge"
        assert never.status_code == 404


class TestTheMigration:
    def test_existing_lines_keep_their_record_s_code(self, config):
        conn = db.connect(config.db_path)
        roll_back_to(conn, 11)
        conn.execute("INSERT INTO boxes (code) VALUES ('B-0001')")
        conn.execute("INSERT INTO events (box_id, kind, to_value) VALUES (1, 'create', 'B-0001')")
        conn.close()

        conn = db.connect(config.db_path)
        try:
            assert [(e["kind"], e["box_code"]) for e in store.events_for(conn, "B-0001")] == [
                ("create", "B-0001")
            ]
        finally:
            conn.close()
