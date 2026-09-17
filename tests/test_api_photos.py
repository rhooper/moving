"""Photo upload/serving and AI draft endpoints."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox.api import app as app_module
from movingbox.api.app import create_app
from movingbox.vision import base


def a_jpeg(colour=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 900), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class StubProvider:
    """Stands in for a vision model. Records what it was asked."""

    name = "stub"

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def draft(self, images, *, model):
        self.calls.append({"images": images, "model": model})
        if self.error:
            raise self.error
        return base.parse(json.dumps(self.reply))


@pytest.fixture
def stub():
    return StubProvider(
        reply={
            "summary": "pots, pans, stand mixer",
            "items": [{"name": "stock pot", "qty": 1}, {"name": "baking pan", "qty": 3}],
            "fragile": True,
            "confidence": "high",
        }
    )


@pytest.fixture
def client(config, stub):
    app = create_app(config)
    app.dependency_overrides[app_module.get_vision_provider] = lambda: stub
    with TestClient(app) as c:
        yield c


@pytest.fixture
def code(client):
    return client.post("/api/boxes", json={}).json()["code"]


class TestUpload:
    def test_a_photo_can_be_uploaded(self, client, code):
        response = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("box.jpg", a_jpeg(), "image/jpeg")}
        )

        assert response.status_code == 201
        assert response.json()["width"] > 0

    def test_uploaded_photos_are_listed(self, client, code):
        client.post(f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")})

        assert len(client.get(f"/api/boxes/{code}/photos").json()) == 1

    def test_the_full_image_and_thumbnail_are_both_served(self, client, code):
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()

        full = client.get(f"/photos/{photo['id']}/full")
        thumb = client.get(f"/photos/{photo['id']}/thumb")

        assert full.headers["content-type"] == "image/jpeg"
        assert thumb.headers["content-type"] == "image/jpeg"
        assert len(thumb.content) < len(full.content)

    def test_a_non_image_upload_is_rejected_with_a_useful_status(self, client, code):
        response = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", b"not an image", "image/jpeg")}
        )

        assert response.status_code == 415

    def test_uploading_to_an_unknown_box_is_404(self, client):
        response = client.post(
            "/api/boxes/B-9999/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        )

        assert response.status_code == 404

    def test_a_photo_can_be_deleted(self, client, code):
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()

        assert client.delete(f"/photos/{photo['id']}").status_code == 204
        assert client.get(f"/api/boxes/{code}/photos").json() == []

    def test_a_caption_can_be_set_and_makes_the_box_searchable(self, client, code):
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()

        client.patch(f"/photos/{photo['id']}", json={"caption": "camping stove"})

        found = client.get("/api/search", params={"q": "camping stove"}).json()
        assert [b["code"] for b in found] == [code]


class TestDraft:
    def upload(self, client, code, colour=(10, 20, 30)):
        return client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(colour), "image/jpeg")}
        ).json()

    def test_a_draft_is_returned_for_a_boxs_photos(self, client, code):
        self.upload(client, code)

        response = client.post(f"/api/boxes/{code}/ai/draft", json={})

        assert response.status_code == 200
        body = response.json()
        assert body["draft"]["summary"] == "pots, pans, stand mixer"
        assert [i["name"] for i in body["draft"]["items"]] == ["stock pot", "baking pan"]

    def test_the_draft_is_not_applied_to_the_box(self, client, code):
        # The whole design: a model proposes, a human accepts. Auto-applying
        # would silently overwrite what someone typed.
        self.upload(client, code)

        client.post(f"/api/boxes/{code}/ai/draft", json={})

        box = client.get(f"/api/boxes/{code}").json()
        assert box["content_summary"] is None
        assert client.get(f"/api/boxes/{code}/items").json() == []

    def test_the_photo_bytes_are_handed_to_the_provider(self, client, code, stub):
        self.upload(client, code)

        client.post(f"/api/boxes/{code}/ai/draft", json={})

        assert len(stub.calls) == 1
        assert stub.calls[0]["images"][0].startswith(b"\xff\xd8")  # a real JPEG

    def test_the_configured_model_is_used_by_default(self, client, code, stub, config):
        self.upload(client, code)

        client.post(f"/api/boxes/{code}/ai/draft", json={})

        assert stub.calls[0]["model"] == config.vision_model

    def test_a_specific_model_can_be_requested(self, client, code, stub):
        self.upload(client, code)

        client.post(f"/api/boxes/{code}/ai/draft", json={"model": "qwen3-vl:8b"})

        assert stub.calls[0]["model"] == "qwen3-vl:8b"

    def test_specific_photos_can_be_chosen(self, client, code, stub):
        first = self.upload(client, code, (1, 1, 1))
        self.upload(client, code, (250, 250, 250))

        client.post(f"/api/boxes/{code}/ai/draft", json={"photo_ids": [first["id"]]})

        assert len(stub.calls[0]["images"]) == 1

    def test_drafting_with_no_photos_explains_itself(self, client, code):
        response = client.post(f"/api/boxes/{code}/ai/draft", json={})

        assert response.status_code == 400
        assert "photo" in response.json()["detail"].lower()

    def test_the_job_is_recorded_with_its_model_and_prompt_version(self, client, code):
        self.upload(client, code)

        body = client.post(f"/api/boxes/{code}/ai/draft", json={}).json()

        job = client.get(f"/api/ai/jobs/{body['job_id']}").json()
        assert job["status"] == "done"
        assert job["prompt_version"] == base.PROMPT_VERSION
        assert job["provider"] == "stub"

    def test_a_provider_failure_is_recorded_and_reported(self, client, code, stub):
        self.upload(client, code)
        stub.error = base.DraftUnreadable("ollama is not running")

        response = client.post(f"/api/boxes/{code}/ai/draft", json={})

        assert response.status_code == 502
        assert "ollama" in response.json()["detail"]

    def test_a_failed_job_is_still_recorded(self, client, code, stub, config):
        from movingbox import db

        self.upload(client, code)
        stub.error = base.DraftUnreadable("model not found")

        client.post(f"/api/boxes/{code}/ai/draft", json={})

        conn = db.connect(config.db_path)
        row = conn.execute("SELECT status, error FROM ai_jobs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert row["status"] == "error"
        assert "model not found" in row["error"]
