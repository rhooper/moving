"""Photo upload/serving and AI draft endpoints."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox import renditions
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

    def test_a_binned_record_takes_no_photo(self, client, code):
        """A binned record is gone as far as an upload is concerned, which is
        why the sheet draws no camera on one: Restore is the honest press."""
        client.delete(f"/api/boxes/{code}")

        response = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
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
        # A draft is a proposal: applying it could overwrite what someone typed.
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


class TestCover:
    """Choosing which photo represents a box, and carrying it on list rows.

    A cover id per row keeps a list at one request instead of one per row.
    """

    def upload(self, client, code, colour):
        return client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(colour), "image/jpeg")}
        ).json()

    def row(self, client, code):
        return next(b for b in client.get("/api/boxes").json() if b["code"] == code)

    def test_a_photo_can_be_made_the_cover(self, client, code):
        self.upload(client, code, (1, 1, 1))
        second = self.upload(client, code, (2, 2, 2))

        response = client.post(f"/photos/{second['id']}/cover")

        assert response.status_code == 200
        assert response.json()["is_primary"] == 1

    def test_choosing_a_cover_demotes_the_previous_one(self, client, code):
        first = self.upload(client, code, (1, 1, 1))
        second = self.upload(client, code, (2, 2, 2))

        client.post(f"/photos/{second['id']}/cover")

        photos = client.get(f"/api/boxes/{code}/photos").json()
        assert [p["id"] for p in photos if p["is_primary"]] == [second["id"]]
        assert next(p for p in photos if p["id"] == first["id"])["is_primary"] == 0

    def test_the_cover_leads_the_photo_list(self, client, code):
        self.upload(client, code, (1, 1, 1))
        second = self.upload(client, code, (2, 2, 2))

        client.post(f"/photos/{second['id']}/cover")

        assert client.get(f"/api/boxes/{code}/photos").json()[0]["id"] == second["id"]

    def test_covering_an_unknown_photo_is_404(self, client):
        assert client.post("/photos/9999/cover").status_code == 404

    def test_the_box_list_carries_the_cover_so_a_row_needs_no_extra_request(self, client, code):
        photo = self.upload(client, code, (1, 1, 1))

        assert self.row(client, code)["cover_photo_id"] == photo["id"]

    def test_a_box_with_no_photo_says_so_rather_than_leaving_the_field_out(self, client, code):
        # The row reserves space for a thumbnail either way, so "no cover" is
        # said rather than left to a missing key.
        row = self.row(client, code)

        assert "cover_photo_id" in row
        assert row["cover_photo_id"] is None

    def test_the_list_cover_follows_the_choice(self, client, code):
        self.upload(client, code, (1, 1, 1))
        second = self.upload(client, code, (2, 2, 2))

        client.post(f"/photos/{second['id']}/cover")

        assert self.row(client, code)["cover_photo_id"] == second["id"]

    def test_search_results_carry_the_cover_too(self, client, code):
        photo = self.upload(client, code, (1, 1, 1))
        client.patch(f"/api/boxes/{code}", json={"content_summary": "camping stove"})

        found = client.get("/api/search", params={"q": "camping stove"}).json()

        assert [b["cover_photo_id"] for b in found] == [photo["id"]]

    def test_the_cover_a_row_points_at_is_the_small_image(self, client, code):
        # 400 px, not the 2048 px original: a long list must not pull full-size
        # photographs.
        photo = self.upload(client, code, (1, 1, 1))
        cover = self.row(client, code)["cover_photo_id"]

        thumb = client.get(f"/photos/{cover}/thumb")

        assert len(thumb.content) < len(client.get(f"/photos/{photo['id']}/full").content)

    def test_deleting_the_cover_promotes_another_rather_than_leaving_none(self, client, code):
        first = self.upload(client, code, (1, 1, 1))
        second = self.upload(client, code, (2, 2, 2))

        client.delete(f"/photos/{first['id']}")

        assert self.row(client, code)["cover_photo_id"] == second["id"]

    def test_deleting_a_spare_photo_does_not_change_the_cover(self, client, code):
        self.upload(client, code, (1, 1, 1))
        second = self.upload(client, code, (2, 2, 2))
        third = self.upload(client, code, (3, 3, 3))
        client.post(f"/photos/{second['id']}/cover")

        client.delete(f"/photos/{third['id']}")

        assert self.row(client, code)["cover_photo_id"] == second["id"]

    def test_deleting_the_last_photo_leaves_the_row_coverless(self, client, code):
        only = self.upload(client, code, (1, 1, 1))

        client.delete(f"/photos/{only['id']}")

        assert self.row(client, code)["cover_photo_id"] is None


def uploaded(client, code, size=(3024, 4032)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (140, 110, 80)).save(buffer, format="JPEG")
    response = client.post(
        f"/api/boxes/{code}/photos", files={"file": ("p.jpg", buffer.getvalue(), "image/jpeg")}
    )
    assert response.status_code == 201
    return response.json()


class TestTheStripImage:
    """GET /photos/{id}/strip?v=...

    A strip URL names one set of bytes forever: the service worker is
    cache-first and the response is cached for a year.
    """

    def test_the_current_version_is_served_and_cached_hard(self, client, code):
        photo = uploaded(client, code)

        response = client.get(f"/photos/{photo['id']}/strip?v={renditions.VERSION}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        # Safe to pin: the version names these exact bytes.
        assert "immutable" in response.headers["cache-control"]
        assert "max-age=31536000" in response.headers["cache-control"]
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (600, 800)

    def test_any_other_version_is_not_found_rather_than_answered_with_this_one(self, client, code):
        # A fallback here would be cached as *that* version for a year.
        photo = uploaded(client, code)

        response = client.get(f"/photos/{photo['id']}/strip?v=0123456789")

        assert response.status_code == 404

    def test_asking_without_a_version_is_not_found(self, client, code):
        photo = uploaded(client, code)

        assert client.get(f"/photos/{photo['id']}/strip").status_code == 404

    def test_one_not_made_yet_is_made_when_it_is_asked_for(self, client, code, config):
        # So an older photo is sharp as soon as its page is opened, whether or
        # not `moving thumbnails` has been run.
        photo = uploaded(client, code)
        strip = config.photo_dir / renditions.strip_name(photo["filename"])
        made_at_upload = strip.read_bytes()
        strip.unlink()

        response = client.get(f"/photos/{photo['id']}/strip?v={renditions.VERSION}")

        assert response.status_code == 200
        # The same bytes it would have had: this URL is answered one way only.
        assert response.content == made_at_upload
        assert strip.read_bytes() == made_at_upload

    def test_a_photo_whose_full_image_is_gone_has_no_strip(self, client, code, config):
        photo = uploaded(client, code)
        (config.photo_dir / renditions.strip_name(photo["filename"])).unlink()
        (config.photo_dir / photo["filename"]).unlink()

        response = client.get(f"/photos/{photo['id']}/strip?v={renditions.VERSION}")

        assert response.status_code == 404

    def test_an_unknown_photo_has_no_strip(self, client):
        assert client.get(f"/photos/999999/strip?v={renditions.VERSION}").status_code == 404

    def test_the_photo_list_offers_both_sizes(self, client, code):
        photo = uploaded(client, code)

        (listed,) = client.get(f"/api/boxes/{code}/photos").json()

        key = photo["key"]
        assert listed["srcset"] == (
            f"/photos/{photo['id']}/thumb?k={key} 300w, "
            f"/photos/{photo['id']}/strip?v={renditions.VERSION}&k={key} 600w"
        )

    def test_the_upload_response_offers_both_sizes_too(self, client, code):
        # The strip draws a new photo from this response before it next lists.
        photo = uploaded(client, code)

        assert f"strip?v={renditions.VERSION}" in photo["srcset"]


class TestADeletedPhotoIsNeverShownAgain:
    """B-0057's new photo showed as the one just deleted from B-0056.

    The deleted photo was the newest, so the next one taken was given its id,
    and with it URLs a phone had cached for a year.
    """

    def upload(self, client, code, colour):
        return client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(colour), "image/jpeg")}
        ).json()

    def test_the_next_photo_does_not_get_the_deleted_one_s_id(self, client):
        first, second = (client.post("/api/boxes", json={}).json()["code"] for _ in range(2))
        deleted = self.upload(client, first, (1, 1, 1))
        client.delete(f"/photos/{deleted['id']}")

        taken = self.upload(client, second, (2, 2, 2))

        assert taken["id"] > deleted["id"]

    def test_a_url_keyed_to_other_bytes_is_not_found(self, client, code):
        # As for a photo whose id was reused before ids stopped being reused:
        # never answered with the new photo's bytes.
        photo = self.upload(client, code, (3, 3, 3))

        for size in ("thumb", "full"):
            assert client.get(f"/photos/{photo['id']}/{size}?k=000000000000").status_code == 404
        strip = f"/photos/{photo['id']}/strip?v={renditions.VERSION}&k=000000000000"
        assert client.get(strip).status_code == 404

    def test_its_own_key_is_served(self, client, code):
        photo = self.upload(client, code, (4, 4, 4))

        for size in ("thumb", "full"):
            assert client.get(f"/photos/{photo['id']}/{size}?k={photo['key']}").status_code == 200
        strip = f"/photos/{photo['id']}/strip?v={renditions.VERSION}&k={photo['key']}"
        assert client.get(strip).status_code == 200

    def test_no_key_is_still_served_for_a_page_from_before_the_deploy(self, client, code):
        photo = self.upload(client, code, (5, 5, 5))

        assert client.get(f"/photos/{photo['id']}/thumb").status_code == 200

    def test_the_list_row_carries_the_cover_s_key(self, client, code):
        photo = self.upload(client, code, (6, 6, 6))

        row = next(b for b in client.get("/api/boxes").json() if b["code"] == code)

        assert (row["cover_photo_id"], row["cover_photo_key"]) == (photo["id"], photo["key"])
        assert photo["key"] == photo["sha256"][:12]
