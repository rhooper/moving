"""Refusing to print a label for a box whose contents are not recorded.

A label with no contents is the expensive mistake: the tape is spent, it goes
on the box, and the box is then indistinguishable from any other unlabelled
one until it is opened.
"""

import pytest
from fastapi.testclient import TestClient

from movingbox.api.app import create_app


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def empty(client):
    return client.post("/api/boxes", json={}).json()["code"]


def test_printing_an_empty_box_is_refused(client, empty):
    response = client.post("/api/labels/print", json={"codes": [empty]})

    assert response.status_code == 409
    assert "contents" in response.json()["detail"].lower()


def test_the_refusal_names_the_box(client, empty):
    detail = client.post("/api/labels/print", json={"codes": [empty]}).json()["detail"]

    assert empty in detail


def test_nothing_is_printed_when_one_box_in_a_batch_is_empty(client, empty, config):
    full = client.post("/api/boxes", json={"content_summary": "pots"}).json()["code"]

    client.post("/api/labels/print", json={"codes": [full, empty]})

    # The whole batch is rejected, as with an unknown code: half a run of tape
    # with no way to tell which labels came out is worse than none.
    assert not (config.label_preview_dir / f"{full}.png").exists()


def test_a_summary_is_enough_to_print(client):
    code = client.post("/api/boxes", json={"content_summary": "pots and pans"}).json()["code"]

    assert client.post("/api/labels/print", json={"codes": [code]}).status_code == 200


def test_items_alone_are_enough_to_print(client, empty):
    client.post(f"/api/boxes/{empty}/items", json={"name": "kettle"})

    assert client.post("/api/labels/print", json={"codes": [empty]}).status_code == 200


def test_whitespace_does_not_count_as_contents(client, empty):
    client.patch(f"/api/boxes/{empty}", json={"content_summary": "   "})

    assert client.post("/api/labels/print", json={"codes": [empty]}).status_code == 409


def test_the_override_prints_anyway(client, empty):
    response = client.post("/api/labels/print", json={"codes": [empty], "allow_empty": True})

    assert response.status_code == 200


def test_the_override_is_off_unless_asked_for(client, empty):
    # Default-on would make the whole gate pointless.
    assert client.post("/api/labels/print", json={"codes": [empty]}).status_code == 409


class TestSuggestedSummary:
    def test_a_summary_is_suggested_from_the_items(self, client, empty):
        client.post(f"/api/boxes/{empty}/items", json={"name": "baking pan", "qty": 3})
        client.post(f"/api/boxes/{empty}/items", json={"name": "kettle"})

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion["summary"] == "3 baking pans, kettle"

    def test_suggesting_does_not_change_the_box(self, client, empty):
        # Same rule as the photo draft: propose, never apply.
        client.post(f"/api/boxes/{empty}/items", json={"name": "kettle"})

        client.get(f"/api/boxes/{empty}/summary-suggestion")

        assert client.get(f"/api/boxes/{empty}").json()["content_summary"] is None

    def test_a_box_with_no_items_suggests_nothing(self, client, empty):
        assert client.get(f"/api/boxes/{empty}/summary-suggestion").json()["summary"] == ""

    def test_an_unknown_box_is_404(self, client):
        assert client.get("/api/boxes/B-9999/summary-suggestion").status_code == 404
