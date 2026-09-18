"""Label preview and print endpoints.

Boxes here are created with a summary: printing a box whose contents are not
recorded is refused (see test_print_gate.py), and these tests are about the
printing path rather than that gate.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox.api.app import create_app

pyzbar = pytest.importorskip("pyzbar.pyzbar", reason="needs `brew install zbar`")


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


def test_the_preview_is_a_png(client):
    code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    response = client.get(f"/api/labels/preview/{code}.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_the_preview_qr_resolves_to_this_boxs_page(client, config):
    # End to end: a box created over HTTP produces a label whose QR, read by a
    # scanner, points back at that box on the configured host.
    code = client.post("/api/boxes", json={"content_summary": "kettle"}).json()["code"]

    response = client.get(f"/api/labels/preview/{code}.png")
    image = Image.open(io.BytesIO(response.content))

    decoded = [d.data.decode() for d in pyzbar.decode(image)]
    assert decoded == [f"{config.base_url}/b/{code}"]


def test_the_preview_shows_the_destination_room(client):
    room = client.post("/api/rooms", json={"name": "Kitchen"}).json()
    code = client.post("/api/boxes", json={"destination_room_id": room["id"]}).json()["code"]

    response = client.get(f"/api/labels/preview/{code}.png")

    # The room band makes the label taller than a bare code-and-QR label.
    bare = client.post("/api/boxes", json={}).json()["code"]
    plain = client.get(f"/api/labels/preview/{bare}.png")
    assert len(response.content) != len(plain.content)


def test_previewing_an_unknown_box_is_404(client):
    assert client.get("/api/labels/preview/B-9999.png").status_code == 404


def test_printing_writes_through_the_fake_backend(client, config):
    code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    response = client.post("/api/labels/print", json={"codes": [code]})

    assert response.status_code == 200
    assert (config.label_preview_dir / f"{code}.png").exists()


def test_printing_is_recorded_on_the_box(client):
    code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    client.post("/api/labels/print", json={"codes": [code]})

    box = client.get(f"/api/boxes/{code}").json()
    assert box["label_print_count"] == 1
    assert box["label_printed_at"] is not None
    kinds = [e["kind"] for e in client.get(f"/api/boxes/{code}/events").json()]
    assert "print" in kinds


def test_reprinting_increments_rather_than_resets(client):
    # Labels get lost and boxes get re-taped; knowing a label was reprinted
    # explains why two labels with the same code exist.
    code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    client.post("/api/labels/print", json={"codes": [code]})
    client.post("/api/labels/print", json={"codes": [code]})

    assert client.get(f"/api/boxes/{code}").json()["label_print_count"] == 2


def test_printing_several_boxes_in_one_request(client, config):
    first = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]
    second = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    response = client.post("/api/labels/print", json={"codes": [first, second]})

    assert response.status_code == 200
    assert len(response.json()["printed"]) == 2


def test_printing_an_unknown_box_is_404_and_prints_nothing(client, config):
    good = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    response = client.post("/api/labels/print", json={"codes": [good, "B-9999"]})

    assert response.status_code == 404
    # The whole request is rejected: printing half a batch wastes tape and
    # leaves you unsure which labels came out.
    assert not (config.label_preview_dir / f"{good}.png").exists()


def test_the_itemised_contents_never_reach_the_tape(client):
    # The list lives in the app, one scan away; the tape is for finding the
    # box from across a room. So itemising a box must not change its label:
    # same box, before and after items are added, byte-identical preview.
    code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]
    before = client.get(f"/api/labels/preview/{code}.png").content

    for name in ("stock pot", "baking pan", "kettle"):
        assert client.post(f"/api/boxes/{code}/items", json={"name": name}).status_code == 201

    after = client.get(f"/api/labels/preview/{code}.png").content
    assert after == before
