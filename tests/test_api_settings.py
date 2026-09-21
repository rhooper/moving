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
        # Reissuing a code already on a printed label is what the counter
        # exists to prevent.
        client.post("/api/boxes", json={})  # B-0001

        response = client.put("/api/settings/next-number", json={"number": 1})

        assert response.status_code == 409
        assert "B-0001" in response.json()["detail"]


class TestWhatThePhotoReadingCosts:
    def cloud(self, config):
        return config.replace(
            vision_provider="claude",
            anthropic_api_key="sk-ant-secret-value",
            vision_budget_usd=30.0,
        )

    def test_a_fresh_move_has_spent_nothing(self, client):
        body = client.get("/api/settings/spend").json()

        assert body["spent_usd"] == 0.0
        assert body["photos"] == 0
        assert body["by_model"] == []

    def test_it_names_the_tiers_and_the_cap(self, config):
        with TestClient(create_app(self.cloud(config))) as client:
            body = client.get("/api/settings/spend").json()

        assert body["model"] == "claude-sonnet-5"
        assert body["detail_model"] == "claude-opus-5"
        assert body["cap_usd"] == 30.0
        assert body["reading_locally"] is False

    def test_the_key_is_a_yes_or_no_and_never_a_value(self, config):
        with TestClient(create_app(self.cloud(config))) as client:
            response = client.get("/api/settings/spend")

        assert response.json()["key"] is True
        assert "sk-ant" not in response.text

    def test_a_local_only_setup_says_so_rather_than_looking_broken(self, client):
        body = client.get("/api/settings/spend").json()

        assert body["provider"] == "ollama"
        assert body["reading_locally"] is True
        assert body["local_model"] == "qwen3-vl:4b-instruct"

    def test_what_has_been_spent_shows_up(self, config):
        from movingbox import db, spend
        from movingbox.vision import base

        conn = db.connect(config.db_path)
        conn.execute(
            "INSERT INTO ai_jobs (provider, model, prompt_version, status) "
            "VALUES ('claude', 'claude-sonnet-5', 'v1', 'done')"
        )
        spend.record(
            conn,
            conn.execute("SELECT MAX(id) AS id FROM ai_jobs").fetchone()["id"],
            base.Reading(
                provider="claude",
                model="claude-sonnet-5",
                input_tokens=2760,
                output_tokens=200,
                cost_usd=0.0075,
            ),
        )
        conn.commit()
        conn.close()

        with TestClient(create_app(self.cloud(config))) as client:
            body = client.get("/api/settings/spend").json()

        assert body["spent_usd"] == pytest.approx(0.0075)
        assert body["photos"] == 1
        assert body["by_model"] == [
            {"model": "claude-sonnet-5", "photos": 1, "spent_usd": pytest.approx(0.0075)}
        ]
