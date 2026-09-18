"""A quick read by default, and a closer look when asked.

Measured on this machine, on the owner's own photos (2026-09-18): the old
default, `qwen3-vl:30b`, is the *thinking* checkpoint and spent ~30 s reasoning
before each answer without being more accurate for it. `qwen3-vl:4b-instruct`
answered in 7.4 s (median, n=69) with the most specific names of any model and
no parse failures; `qwen3-vl:8b-instruct` took ~10 s and read handwriting and
brand names best. So: the 4b reads every photo, and the 8b is the closer look.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from movingbox import analysis, config, db, storage, store
from movingbox.api.app import create_app
from movingbox.vision import base, ollama


def a_jpeg(colour=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class Records:
    """A provider that remembers which model it was asked to use."""

    name = "stub"

    def __init__(self):
        self.models = []

    def draft(self, images, *, model):
        self.models.append(model)
        return base.BoxDraft(summary="things", items=[base.DraftItem(name=f"seen by {model}")])


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


def photographed(conn, cfg):
    box = store.create_box(conn)
    photo = storage.save_photo(conn, cfg, box["code"], a_jpeg(), filename="p.jpg")
    return box, photo


def run(cfg, provider):
    worker = analysis.Analyst(cfg, provider_factory=lambda: provider, publish=lambda *a: None)
    while worker.run_once():
        pass


class TestTheDefaults:
    def test_the_everyday_model_is_the_quick_one(self):
        assert config.from_env({}).vision_model == "qwen3-vl:4b-instruct"

    def test_the_closer_look_is_a_different_more_careful_model(self):
        cfg = config.from_env({})

        assert cfg.vision_detail_model == "qwen3-vl:8b-instruct"
        assert cfg.vision_detail_model != cfg.vision_model

    def test_both_can_be_changed_from_the_environment(self):
        cfg = config.from_env(
            {"MOVING_VISION_MODEL": "a:1b", "MOVING_VISION_DETAIL_MODEL": "qwen3-vl:30b"}
        )

        assert (cfg.vision_model, cfg.vision_detail_model) == ("a:1b", "qwen3-vl:30b")


class TestTheRequest:
    def test_the_model_is_kept_loaded_between_boxes(self):
        # Ollama unloads after five idle minutes, and boxes in a packing
        # session are often further apart than that. Per request, so nobody
        # has to edit a Homebrew plist.
        assert ollama.build_request("m", [b"x"])["keep_alive"] == "30m"

    def test_the_context_is_sized_for_one_photo_not_a_novel(self):
        # Left alone, Ollama picks a 262k context and a 4b model takes 25 GB.
        assert ollama.build_request("m", [b"x"])["options"]["num_ctx"] == 8192

    def test_a_reply_that_arrives_in_the_thinking_field_is_still_read(self):
        # Ollama 0.34 with `format` set and thinking off puts the JSON in
        # `thinking` and leaves `content` empty. 42 of 42 replies were lost
        # that way in the benchmark, with a usable draft sitting in each.
        reply = json.dumps({"summary": "tea things", "items": [{"name": "kettle", "qty": 1}]})

        draft = ollama.read_response({"message": {"content": "", "thinking": reply}})

        assert [i.name for i in draft.items] == ["kettle"]

    def test_content_wins_when_both_are_there(self):
        good = json.dumps({"summary": "s", "items": [{"name": "kettle", "qty": 1}]})

        draft = ollama.read_response(
            {"message": {"content": good, "thinking": "Let me look at the photo..."}}
        )

        assert [i.name for i in draft.items] == ["kettle"]

    def test_thinking_that_is_only_thinking_is_not_mistaken_for_an_answer(self):
        with pytest.raises(base.DraftUnreadable):
            ollama.read_response({"message": {"content": "", "thinking": "Hmm, a box of..."}})


class TestTheQueue:
    def test_an_upload_gets_the_quick_model(self, conn, config):
        _, photo = photographed(conn, config)
        analysis.enqueue(conn, config, photo["id"])
        seen = Records()

        run(config, seen)

        assert seen.models == [config.vision_model]
        assert analysis.state_of(conn, photo["id"])["detail"] is False

    def test_a_closer_look_gets_the_careful_one(self, conn, config):
        _, photo = photographed(conn, config)
        analysis.enqueue(conn, config, photo["id"])
        seen = Records()
        run(config, seen)

        analysis.enqueue(conn, config, photo["id"], again=True, detail=True)
        run(config, seen)

        assert seen.models == [config.vision_model, config.vision_detail_model]
        assert analysis.state_of(conn, photo["id"])["detail"] is True

    def test_a_closer_look_adds_to_what_the_quick_read_found(self, conn, config):
        box, photo = photographed(conn, config)
        analysis.enqueue(conn, config, photo["id"])
        seen = Records()
        run(config, seen)
        analysis.enqueue(conn, config, photo["id"], again=True, detail=True)
        run(config, seen)

        names = {i["name"] for i in store.list_items(conn, box["code"])}

        assert names == {f"seen by {config.vision_model}", f"seen by {config.vision_detail_model}"}

    def test_each_model_is_timed_against_its_own_history(self, conn, config):
        # The closer look is slower; it must not make the next quick read's
        # countdown longer, or the other way round.
        _, photo = photographed(conn, config)
        analysis.enqueue(conn, config, photo["id"])
        conn.execute("UPDATE ai_jobs SET status='done', duration_ms=7000 WHERE photo_id=?",
                     (photo["id"],))
        analysis.enqueue(conn, config, photo["id"], again=True, detail=True)
        conn.execute("UPDATE ai_jobs SET status='done', duration_ms=10000 WHERE detail=1")

        assert analysis.estimate_ms(conn, config.vision_model) == 7000
        assert analysis.estimate_ms(conn, config.vision_detail_model) == 10000

    def test_a_retry_after_a_failed_closer_look_is_still_a_closer_look(self, conn, config):
        _, photo = photographed(conn, config)
        analysis.enqueue(conn, config, photo["id"], detail=True)
        conn.execute("UPDATE ai_jobs SET status='error', error='offline'")

        analysis.enqueue(conn, config, photo["id"], again=True, detail=True)

        assert analysis.state_of(conn, photo["id"])["detail"] is True


class TestOverTheApi:
    def upload(self, client, code):
        return client.post(
            f"/api/boxes/{code}/photos", files={"file": ("p.jpg", a_jpeg(), "image/jpeg")}
        ).json()

    def test_asking_for_a_closer_look(self, client, config):
        code = client.post("/api/boxes", json={}).json()["code"]
        photo = self.upload(client, code)
        run(config, Records())

        response = client.post(f"/photos/{photo['id']}/analyse", params={"detail": "true"})

        assert response.status_code == 202
        assert response.json()["analysis"]["detail"] is True
        assert response.json()["analysis"]["status"] == "pending"

    def test_a_plain_retry_is_a_quick_read(self, client, config):
        code = client.post("/api/boxes", json={}).json()["code"]
        photo = self.upload(client, code)
        run(config, Records())

        response = client.post(f"/photos/{photo['id']}/analyse")

        assert response.json()["analysis"]["detail"] is False

    def test_an_uploaded_photo_says_it_was_a_quick_read(self, client):
        code = client.post("/api/boxes", json={}).json()["code"]

        assert self.upload(client, code)["analysis"]["detail"] is False
