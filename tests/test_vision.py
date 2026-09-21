"""Vision providers.

These tests exercise the mapping between a model's reply and a BoxDraft, using
canned responses. They never call a model: the point is that our parsing is
correct and defensive, not that qwen3-vl is any good.
"""

import base64
import io
import json

import pytest
from PIL import Image

from movingbox.vision import base, ollama


def a_reply(**overrides) -> dict:
    payload = {
        "summary": "pots, baking pans, stand mixer",
        "items": [
            {"name": "stock pot", "qty": 1, "category": "cookware"},
            {"name": "baking pan", "qty": 3, "category": "cookware"},
        ],
        "fragile": True,
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


class TestParsing:
    def test_a_well_formed_reply_becomes_a_draft(self):
        draft = base.parse(json.dumps(a_reply()))

        assert draft.summary == "pots, baking pans, stand mixer"
        assert [i.name for i in draft.items] == ["stock pot", "baking pan"]
        assert draft.items[1].qty == 3
        assert draft.fragile is True

    def test_a_reply_wrapped_in_a_markdown_fence_still_parses(self):
        # Small local models do this constantly despite being asked not to.
        fenced = f"```json\n{json.dumps(a_reply())}\n```"

        draft = base.parse(fenced)

        assert draft.summary.startswith("pots")

    def test_prose_around_the_json_is_tolerated(self):
        chatty = f"Sure! Here is what I can see:\n{json.dumps(a_reply())}\nHope that helps."

        draft = base.parse(chatty)

        assert len(draft.items) == 2

    def test_a_missing_quantity_defaults_to_one(self):
        draft = base.parse(json.dumps(a_reply(items=[{"name": "kettle"}])))

        assert draft.items[0].qty == 1

    def test_a_nonsense_quantity_is_clamped_rather_than_crashing(self):
        draft = base.parse(
            json.dumps(
                a_reply(items=[{"name": "spoons", "qty": "lots"}, {"name": "forks", "qty": -4}])
            )
        )

        assert [i.qty for i in draft.items] == [1, 1]

    def test_items_without_a_name_are_dropped(self):
        draft = base.parse(json.dumps(a_reply(items=[{"name": ""}, {"qty": 2}, {"name": "ladle"}])))

        assert [i.name for i in draft.items] == ["ladle"]

    def test_a_reply_with_no_json_at_all_raises(self):
        with pytest.raises(base.DraftUnreadable):
            base.parse("I'm sorry, I can't see any boxes in this image.")

    def test_a_truncated_reply_raises(self):
        with pytest.raises(base.DraftUnreadable):
            base.parse('{"summary": "pots", "items": [{"name": "pot"')

    def test_the_summary_is_capped_so_it_can_fit_a_label(self):
        draft = base.parse(json.dumps(a_reply(summary="thing, " * 200)))

        assert len(draft.summary) <= base.SUMMARY_MAX

    def test_an_absurd_number_of_items_is_capped(self):
        many = [{"name": f"thing {n}"} for n in range(500)]

        draft = base.parse(json.dumps(a_reply(items=many)))

        assert len(draft.items) == base.ITEMS_MAX


def user_message(request: dict) -> dict:
    """The message carrying the photos, found by role rather than position."""
    return next(m for m in request["messages"] if m["role"] == "user")


class TestOllama:
    def test_the_system_prompt_is_its_own_message(self):
        request = ollama.build_request("qwen3-vl:30b", [b"x"])

        assert request["messages"][0]["role"] == "system"

    def test_the_request_asks_for_structured_output(self):
        request = ollama.build_request("qwen3-vl:30b", [b"fake image bytes"])

        assert request["model"] == "qwen3-vl:30b"
        assert request["stream"] is False
        # Without a schema, a local model returns prose more often than not.
        assert request["format"]["type"] == "object"
        assert "items" in request["format"]["properties"]

    def test_images_are_sent_base64_encoded(self):
        request = ollama.build_request("qwen3-vl:8b", [b"abc"])

        assert user_message(request)["images"] == ["YWJj"]

    def test_several_photos_go_in_one_message(self):
        request = ollama.build_request("qwen3-vl:8b", [b"a", b"b", b"c"])

        assert len(user_message(request)["images"]) == 3

    def test_the_reply_content_is_what_gets_parsed(self):
        draft = ollama.read_response({"message": {"content": json.dumps(a_reply())}})

        assert draft.summary.startswith("pots")

    def test_an_unexpected_shape_raises_rather_than_returning_nothing(self):
        with pytest.raises(base.DraftUnreadable):
            ollama.read_response({"error": "model not found"})


class TestOllamaErrors:
    """Error text has to name the actual problem; these are read while packing."""

    def test_a_missing_model_says_so_and_how_to_fix_it(self):
        # Ollama answers 404 when the model is not pulled. Reporting that as
        # "could not reach ollama" sends you debugging the wrong thing.
        provider = ollama.OllamaProvider("http://localhost:11434")

        message = provider.explain(404, "qwen3-vl:8b")

        assert "qwen3-vl:8b" in message
        assert "ollama pull qwen3-vl:8b" in message
        assert "could not reach" not in message

    def test_a_connection_failure_says_ollama_is_not_running(self):
        provider = ollama.OllamaProvider("http://localhost:11434")

        message = provider.explain(None, "qwen3-vl:30b")

        assert "http://localhost:11434" in message
        assert "running" in message.lower()


def a_jpeg(size) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 90, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


class TestWhatTheLocalModelIsSent:
    # Photos are kept larger than the local model was tuned for; 2048 on the
    # long edge is where it reads small text without running out of context.
    def test_a_photo_kept_larger_is_shrunk_to_the_local_model_s_size(self):
        sent = ollama.for_local(a_jpeg((2048, 2731)))

        assert Image.open(io.BytesIO(sent)).size == (1536, 2048)

    def test_a_photo_that_already_fits_is_sent_as_it_is(self):
        original = a_jpeg((1536, 2048))

        assert ollama.for_local(original) is original

    def test_a_photo_that_cannot_be_read_is_refused_not_passed_on(self):
        with pytest.raises(base.DraftUnreadable):
            ollama.for_local(b"not an image")

    def test_the_request_carries_the_shrunk_photo(self, monkeypatch):
        sent = {}

        class Reply:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": json.dumps(a_reply())}}

        def post(url, json, timeout):
            sent.update(json)
            return Reply()

        monkeypatch.setattr(ollama.httpx, "post", post)

        ollama.OllamaProvider("http://ollama").draft([a_jpeg((2048, 2731))], model="m")

        image = base64.b64decode(user_message(sent)["images"][0])
        assert Image.open(io.BytesIO(image)).size == (1536, 2048)
