"""Deleting is reversible; only a deliberate purge destroys anything.

Mid-move, an accidental delete on a phone is a real prospect and a permanent
one is unrecoverable. So a delete marks the record and keeps everything —
rows, photos, files — and a second, explicit action is what actually removes
them.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox import db, search, storage, store
from movingbox.api.app import create_app


def a_jpeg(colour=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


@pytest.fixture
def box(config, conn):
    made = store.create_box(conn, content_summary="winter coats")
    store.add_item(conn, made["code"], name="scarf")
    storage.save_photo(conn, config, made["code"], a_jpeg(), filename="a.jpg")
    return made


class TestDeleting:
    def test_the_record_survives_the_delete(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        assert store.get_box(conn, box["code"], include_deleted=True) is not None

    def test_it_is_gone_from_the_ordinary_view(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        assert store.get_box(conn, box["code"]) is None
        assert store.list_boxes(conn) == []

    def test_it_is_gone_from_search(self, config, conn, box):
        # Otherwise a deleted box keeps turning up when you look for the coats
        # that are no longer in it.
        store.delete_box(conn, config, box["code"])

        assert search.search(conn, "winter") == []

    def test_the_photo_files_are_kept(self, config, conn, box):
        photo = storage.list_photos(conn, box["code"])[0]
        full = config.photo_dir / photo["filename"]

        store.delete_box(conn, config, box["code"])

        # Nothing is destroyed by a reversible action.
        assert full.is_file()

    def test_the_items_are_kept(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        restored = store.restore_box(conn, box["code"])

        assert [i["name"] for i in store.list_items(conn, restored["code"])] == ["scarf"]

    def test_when_it_was_deleted_is_recorded(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        assert store.get_box(conn, box["code"], include_deleted=True)["deleted_at"]

    def test_deleting_is_written_to_the_timeline(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        kinds = [e["kind"] for e in store.events_for(conn, box["code"])]
        assert "delete" in kinds


class TestRestoring:
    def test_a_deleted_record_comes_back(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        store.restore_box(conn, box["code"])

        assert store.get_box(conn, box["code"]) is not None

    def test_it_is_searchable_again(self, config, conn, box):
        store.delete_box(conn, config, box["code"])
        store.restore_box(conn, box["code"])

        assert search.search(conn, "winter") == [box["id"]]

    def test_restoring_something_that_was_not_deleted_is_harmless(self, config, conn, box):
        assert store.restore_box(conn, box["code"])["deleted_at"] is None

    def test_restoring_is_written_to_the_timeline(self, config, conn, box):
        store.delete_box(conn, config, box["code"])
        store.restore_box(conn, box["code"])

        kinds = [e["kind"] for e in store.events_for(conn, box["code"])]
        assert "restore" in kinds


class TestListingWhatWasDeleted:
    def test_deleted_records_can_be_listed_on_request(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        assert [b["code"] for b in store.deleted_boxes(conn)] == [box["code"]]

    def test_living_records_are_not_in_that_list(self, config, conn, box):
        assert store.deleted_boxes(conn) == []

    def test_the_most_recently_deleted_comes_first(self, config, conn):
        first = store.create_box(conn, content_summary="one")
        second = store.create_box(conn, content_summary="two")
        store.delete_box(conn, config, first["code"])
        store.delete_box(conn, config, second["code"])

        assert [b["code"] for b in store.deleted_boxes(conn)][0] == second["code"]


class TestPurging:
    def test_purging_removes_the_record(self, config, conn, box):
        store.delete_box(conn, config, box["code"])

        assert store.purge_box(conn, config, box["code"]) is True
        assert store.get_box(conn, box["code"], include_deleted=True) is None

    def test_purging_removes_the_photo_files(self, config, conn, box):
        photo = storage.list_photos(conn, box["code"])[0]
        full = config.photo_dir / photo["filename"]
        thumb = config.photo_dir / photo["thumb_filename"]
        store.delete_box(conn, config, box["code"])

        store.purge_box(conn, config, box["code"])

        assert not full.exists()
        assert not thumb.exists()

    def test_a_living_record_cannot_be_purged_by_accident(self, config, conn, box):
        # Purging is the destructive one; it only applies to something already
        # in the bin.
        with pytest.raises(store.NotDeleted):
            store.purge_box(conn, config, box["code"])

        assert store.get_box(conn, box["code"]) is not None

    def test_purging_something_absent_says_so(self, config, conn):
        assert store.purge_box(conn, config, "B-9999") is False

    def test_the_code_is_never_handed_out_again(self, config, conn, box):
        store.delete_box(conn, config, box["code"])
        store.purge_box(conn, config, box["code"])

        assert store.create_box(conn)["code"] != box["code"]


class TestOverHttp:
    @pytest.fixture
    def client(self, config):
        with TestClient(create_app(config)) as c:
            yield c

    @pytest.fixture
    def code(self, client):
        return client.post("/api/boxes", json={"content_summary": "winter coats"}).json()["code"]

    def test_delete_is_reversible(self, client, code):
        client.delete(f"/api/boxes/{code}")

        assert client.post(f"/api/boxes/{code}/restore").status_code == 200
        assert client.get(f"/api/boxes/{code}").status_code == 200

    def test_a_deleted_record_is_still_reachable_by_its_code(self, client, code):
        # Reachable only if you know the code -- it is out of the list and out
        # of search. Returning 404 here would mean scanning a box you had just
        # deleted by mistake told you it never existed, with no way back.
        client.delete(f"/api/boxes/{code}")

        found = client.get(f"/api/boxes/{code}")

        assert found.status_code == 200
        assert found.json()["deleted_at"]

    def test_a_record_that_never_existed_is_still_404(self, client):
        assert client.get("/api/boxes/B-9999").status_code == 404

    def test_the_bin_can_be_listed(self, client, code):
        client.delete(f"/api/boxes/{code}")

        assert [b["code"] for b in client.get("/api/boxes/deleted").json()] == [code]

    def test_scanning_a_deleted_label_still_finds_it(self, client, code):
        # You deleted it by mistake, then scanned the box to check. Landing on
        # "no such box" would be the wrong answer to a question you can still
        # fix.
        client.delete(f"/api/boxes/{code}")

        assert client.get(f"/b/{code}", follow_redirects=False).status_code == 307

    def test_purging_over_http_needs_the_record_deleted_first(self, client, code):
        assert client.delete(f"/api/boxes/{code}/purge").status_code == 409

    def test_purging_a_deleted_record_works(self, client, code):
        client.delete(f"/api/boxes/{code}")

        assert client.delete(f"/api/boxes/{code}/purge").status_code == 204
        assert client.post(f"/api/boxes/{code}/restore").status_code == 404

    def test_restoring_announces_itself(self, client, code):
        client.delete(f"/api/boxes/{code}")

        with client.websocket_connect("/api/events") as socket:
            client.post(f"/api/boxes/{code}/restore")

            assert socket.receive_json() == {"kind": "box.restored", "code": code}

    def test_a_deleted_record_is_not_in_the_manifest(self, client, code):
        client.delete(f"/api/boxes/{code}")

        counts = {g["room"]: g["count"] for g in client.get("/api/manifest").json()}
        assert counts == {}

    def test_a_deleted_record_is_not_in_the_export(self, client, code):
        client.delete(f"/api/boxes/{code}")

        assert client.get("/api/export.json").json()["box_count"] == 0


class TestReadingSomethingDeleted:
    """You look at a deleted record to decide whether to restore it."""

    @pytest.fixture
    def client(self, config):
        with TestClient(create_app(config)) as c:
            yield c

    @pytest.fixture
    def deleted(self, client):
        code = client.post("/api/boxes", json={"content_summary": "coats"}).json()["code"]
        client.post(f"/api/boxes/{code}/items", json={"name": "scarf"})
        client.post(f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")})
        client.delete(f"/api/boxes/{code}")
        return code

    def test_its_contents_can_still_be_read(self, client, deleted):
        # The box page loads these in parallel; a 404 on any one and the page
        # cannot render, so there is no way to reach Restore.
        assert [i["name"] for i in client.get(f"/api/boxes/{deleted}/items").json()] == ["scarf"]

    def test_its_photos_can_still_be_read(self, client, deleted):
        assert len(client.get(f"/api/boxes/{deleted}/photos").json()) == 1

    def test_its_timeline_can_still_be_read(self, client, deleted):
        kinds = [e["kind"] for e in client.get(f"/api/boxes/{deleted}/events").json()]

        assert "delete" in kinds

    def test_changing_it_says_to_restore_it_first(self, client, deleted):
        # 404 would claim it never existed; the useful answer is what to do.
        response = client.patch(f"/api/boxes/{deleted}", json={"content_summary": "x"})

        assert response.status_code == 409
        assert "restore" in response.json()["detail"].lower()

    def test_it_cannot_be_moved_while_deleted(self, client, deleted):
        response = client.post(f"/api/boxes/{deleted}/status", json={"status": "packed"})

        assert response.status_code == 409
