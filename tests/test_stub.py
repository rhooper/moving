"""The stub: one inch of tape carrying just the number and the QR.

For the moment a box is created, before anything is in it: stick the stub on,
pack, and print the full label at the end. It reads *across* the tape -- a
quarter turn from the main label -- because that is the only way a big number
and a scannable QR both fit in an inch.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox.api.app import create_app
from movingbox.labels import layout, printer

pyzbar = pytest.importorskip("pyzbar.pyzbar", reason="needs `brew install zbar`")

URL = "https://test.example.ts.net/b/B-0042"


def a_stub(**overrides):
    fields = {"code": "B-0042", "url": URL}
    fields.update(overrides)
    return layout.LabelData(**fields)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


class TestTheStubItself:
    def test_it_is_one_inch_of_tape(self):
        image = layout.render_stub(a_stub())

        # 300 dots at 300 dpi along the tape; the full printable width across.
        assert (image.width, image.height) == (layout.PRINTABLE_WIDTH, 300)
        assert layout.STUB_LENGTH == 300

    def test_its_qr_scans_to_the_box(self):
        found = pyzbar.decode(layout.render_stub(a_stub()))

        assert found and found[0].data.decode() == URL

    def test_it_carries_the_number_beside_the_qr(self):
        image = layout.render_stub(a_stub()).convert("L")

        number_side = image.crop((0, 0, image.width // 2, image.height))
        assert number_side.getextrema()[0] == 0, "no ink where the box number should be"

    def test_it_carries_nothing_else(self):
        # Room, flags and summary all belong to the full label. A stub printed
        # before packing would only ever show stale or empty ones.
        bare = layout.render_stub(a_stub())
        dressed = layout.render_stub(
            a_stub(room="Kitchen", summary="pots and pans", flags=("FRAGILE", "OPEN FIRST"))
        )

        assert bare.tobytes() == dressed.tobytes()

    def test_the_printer_feeds_it_as_it_is(self):
        # Already the tape's width, so no quarter turn: it prints across the
        # tape, which is what puts it at 90 degrees to the main label.
        stub = layout.render_stub(a_stub())

        assert printer.to_raster(stub).size == stub.size

    def test_a_long_code_shrinks_rather_than_hitting_the_qr(self):
        found = pyzbar.decode(layout.render_stub(a_stub(code="WAREHOUSE-000123")))

        assert found and found[0].data.decode() == URL


class TestOverTheApi:
    def test_the_preview_can_be_a_stub(self, client):
        code = client.post("/api/boxes", json={}).json()["code"]

        response = client.get(f"/api/labels/preview/{code}.png", params={"stub": "true"})

        assert response.status_code == 200
        assert Image.open(io.BytesIO(response.content)).size == (layout.PRINTABLE_WIDTH, 300)

    def test_a_stub_prints_for_a_box_with_nothing_in_it(self, client):
        # The full label refuses an empty box (409). The stub exists *for* the
        # empty box, so the gate does not apply to it.
        code = client.post("/api/boxes", json={}).json()["code"]

        assert client.post("/api/labels/print", json={"codes": [code]}).status_code == 409
        response = client.post("/api/labels/print", json={"codes": [code], "stub": True})

        assert response.status_code == 200
        assert response.json()["printed"][0]["code"] == code

    def test_what_comes_out_is_the_stub_not_the_label(self, client, config):
        code = client.post("/api/boxes", json={"content_summary": "kettle"}).json()["code"]

        output = client.post("/api/labels/print", json={"codes": [code], "stub": True}).json()
        with Image.open(output["printed"][0]["output"]) as written:
            assert written.size == (layout.PRINTABLE_WIDTH, 300)

    def test_a_printed_stub_counts_as_a_label_in_circulation(self, client):
        # Purging warns when something scannable is stuck to a box. A stub is.
        code = client.post("/api/boxes", json={}).json()["code"]
        client.post("/api/labels/print", json={"codes": [code], "stub": True})

        assert client.get(f"/api/boxes/{code}").json()["label_print_count"] == 1
