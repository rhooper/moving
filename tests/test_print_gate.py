"""Refusing to print a label for a box whose contents are not recorded.

A label with no contents is the expensive mistake: the tape is spent, and the
box is indistinguishable from any other until it is opened.
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

    # As with an unknown code: half a run of tape is worse than none.
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
