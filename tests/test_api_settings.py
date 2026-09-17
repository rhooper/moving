"""Settings endpoints behind the settings panel."""

import pytest
from fastapi.testclient import TestClient

from movingbox.api.app import create_app


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


class TestCodeFormat:
    def test_the_current_format_is_reported_with_an_example(self, client):
        body = client.get("/api/settings/code-format").json()

        assert body["prefix"] == "B"
        assert body["digits"] == 4
        assert body["example"] == "B-0001"

    def test_the_format_can_be_changed(self, client):
        response = client.put(
            "/api/settings/code-format",
            json={"prefix": "CAM", "separator": "-", "digits": 3},
        )

        assert response.status_code == 200
        assert response.json()["example"] == "CAM-001"

    def test_a_changed_format_is_used_by_the_next_box(self, client):
        client.put(
            "/api/settings/code-format",
            json={"prefix": "CAM", "separator": "-", "digits": 3},
        )

        assert client.post("/api/boxes", json={}).json()["code"] == "CAM-001"

    def test_a_separator_free_format_works(self, client):
        client.put("/api/settings/code-format", json={"prefix": "D", "separator": "", "digits": 3})

        assert client.post("/api/boxes", json={}).json()["code"] == "D001"

    def test_a_bad_prefix_is_rejected_with_a_readable_reason(self, client):
        response = client.put(
            "/api/settings/code-format",
            json={"prefix": "CA M", "separator": "-", "digits": 3},
        )

        assert response.status_code == 422
        assert "prefix" in response.json()["detail"].lower()

    def test_a_bad_separator_is_rejected(self, client):
        response = client.put(
            "/api/settings/code-format",
            json={"prefix": "CAM", "separator": "/", "digits": 3},
        )

        assert response.status_code == 422

    def test_a_rejected_change_leaves_the_format_alone(self, client):
        client.put(
            "/api/settings/code-format", json={"prefix": "CAM", "separator": "-", "digits": 3}
        )

        client.put("/api/settings/code-format", json={"prefix": "", "separator": "-", "digits": 3})

        assert client.get("/api/settings/code-format").json()["prefix"] == "CAM"


class TestNextNumber:
    def test_the_next_number_can_be_set(self, client):
        response = client.put("/api/settings/next-number", json={"number": 42})

        assert response.status_code == 200
        assert response.json()["next"] == "B-0042"

    def test_the_next_box_uses_it(self, client):
        client.put("/api/settings/next-number", json={"number": 42})

        assert client.post("/api/boxes", json={}).json()["code"] == "B-0042"

    def test_zero_is_rejected(self, client):
        assert client.put("/api/settings/next-number", json={"number": 0}).status_code == 422

    def test_it_will_not_walk_back_onto_an_existing_code(self, client):
        # Reissuing a code that is already on a printed label is the one thing
        # the counter exists to prevent; setting it by hand must not bypass that.
        client.post("/api/boxes", json={})  # B-0001

        response = client.put("/api/settings/next-number", json={"number": 1})

        assert response.status_code == 409
        assert "B-0001" in response.json()["detail"]
