"""Deleting a record, and taking its photos with it."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox import db, storage, store
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


def test_the_record_goes(config, conn):
    box = store.create_box(conn, content_summary="pots")

    assert store.delete_box(conn, config, box["code"]) is True
    assert store.get_box(conn, box["code"]) is None


def test_deleting_something_that_is_not_there_says_so(config, conn):
    assert store.delete_box(conn, config, "B-9999") is False


def test_the_photo_files_go_with_it(config, conn):
    # Rows cascade, but the JPEGs are on disk with nothing left pointing at
    # them -- so they can never be found again, or cleaned up.
    box = store.create_box(conn, content_summary="pots")
    photo = storage.save_photo(conn, config, box["code"], a_jpeg(), filename="a.jpg")
    full = config.photo_dir / photo["filename"]
    thumb = config.photo_dir / photo["thumb_filename"]
    assert full.is_file() and thumb.is_file()

    store.delete_box(conn, config, box["code"])

    assert not full.exists()
    assert not thumb.exists()


def test_every_photo_goes_not_only_the_cover(config, conn):
    box = store.create_box(conn, content_summary="pots")
    files = [
        config.photo_dir
        / storage.save_photo(conn, config, box["code"], a_jpeg(shade), filename=f"{n}.jpg")[
            "filename"
        ]
        for n, shade in enumerate([(1, 1, 1), (9, 9, 9), (200, 30, 30)])
    ]

    store.delete_box(conn, config, box["code"])

    assert [f for f in files if f.exists()] == []


def test_another_records_photos_are_left_alone(config, conn):
    keep = store.create_box(conn, content_summary="keep")
    kept = (
        config.photo_dir
        / storage.save_photo(conn, config, keep["code"], a_jpeg((5, 5, 5)), filename="k.jpg")[
            "filename"
        ]
    )
    doomed = store.create_box(conn, content_summary="go")
    storage.save_photo(conn, config, doomed["code"], a_jpeg((250, 250, 250)), filename="d.jpg")

    store.delete_box(conn, config, doomed["code"])

    assert kept.is_file()


def test_a_missing_file_does_not_stop_the_delete(config, conn):
    # Half-deleted is worse than deleted: a file already gone by other means
    # must not leave the row behind.
    box = store.create_box(conn, content_summary="pots")
    photo = storage.save_photo(conn, config, box["code"], a_jpeg(), filename="a.jpg")
    (config.photo_dir / photo["filename"]).unlink()

    assert store.delete_box(conn, config, box["code"]) is True
    assert store.get_box(conn, box["code"]) is None


def test_the_code_is_not_handed_out_again(config, conn):
    # A printed label for the deleted box may still be on something.
    first = store.create_box(conn)
    store.delete_box(conn, config, first["code"])

    assert store.create_box(conn)["code"] != first["code"]


class TestOverHttp:
    @pytest.fixture
    def client(self, config):
        with TestClient(create_app(config)) as c:
            yield c

    def test_a_box_can_be_deleted(self, client):
        code = client.post("/api/boxes", json={"content_summary": "pots"}).json()["code"]

        assert client.delete(f"/api/boxes/{code}").status_code == 204
        assert client.get(f"/api/boxes/{code}").status_code == 404

    def test_deleting_an_unknown_box_is_404(self, client):
        assert client.delete("/api/boxes/B-9999").status_code == 404

    def test_the_photos_are_cleaned_up_over_http_too(self, client, config):
        code = client.post("/api/boxes", json={"content_summary": "pots"}).json()["code"]
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()
        full = config.photo_dir / photo["filename"]

        client.delete(f"/api/boxes/{code}")

        assert not full.exists()

    def test_deleting_announces_itself_to_other_devices(self, client):
        code = client.post("/api/boxes", json={"content_summary": "pots"}).json()["code"]

        with client.websocket_connect("/api/events") as socket:
            client.delete(f"/api/boxes/{code}")

            assert socket.receive_json() == {"kind": "box.deleted", "code": code}
