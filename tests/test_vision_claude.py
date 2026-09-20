"""The Claude vision provider: what it sends, what it reads back, what it costs.

Nothing here touches the network. The SDK client is faked at the boundary --
the one method the provider calls -- so request building, response reading,
error mapping and the cost arithmetic are all exercised as plain functions.
"""

import io
import json
from types import SimpleNamespace

import anthropic
import pytest
from PIL import Image

from movingbox.vision import base, claude


def a_jpeg(size=(1536, 2048)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 90, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def a_png(size=(400, 300)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 60, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


REPLY = json.dumps(
    {
        "summary": "kettle, mugs and a toaster",
        "items": [{"name": "kettle", "qty": 1, "category": "kitchen"}, {"name": "mug", "qty": 3}],
        "fragile": True,
        "confidence": "high",
    }
)


def reply(text=REPLY, *, stop_reason="end_turn", input_tokens=2760, output_tokens=200):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeMessages:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def create(self, **request):
        self.sent.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeClient:
    def __init__(self, *answers):
        self.messages = FakeMessages(*answers)


def provider(*answers, detail_model="claude-opus-5"):
    client = FakeClient(*answers)
    return claude.ClaudeProvider("sk-ant-secret", client=client, detail_model=detail_model), client


# --- the picture ----------------------------------------------------------


class TestWhatIsSent:
    def test_a_stored_photo_is_shrunk_to_the_api_s_own_ceiling(self):
        data, media_type = claude.for_api(a_jpeg((1536, 2048)))

        assert media_type == "image/jpeg"
        assert Image.open(io.BytesIO(data)).size == (1176, 1568)

    def test_a_photo_that_already_fits_is_sent_as_it_is(self):
        original = a_png((400, 300))

        data, media_type = claude.for_api(original)

        assert data is original
        assert media_type == "image/png"

    def test_the_token_cost_of_a_picture_is_its_area(self):
        # 1176 x 1568 is what a stored 1536 x 2048 photo becomes.
        assert claude.image_tokens(1176, 1568) == 2459

    def test_the_request_carries_the_picture_then_the_instruction(self):
        model, client = provider(reply())

        model.draft([a_jpeg()], model="claude-sonnet-5")

        sent = client.messages.sent[0]
        assert sent["model"] == "claude-sonnet-5"
        assert sent["system"] == base.SYSTEM
        blocks = sent["messages"][0]["content"]
        assert [block["type"] for block in blocks] == ["image", "text"]
        assert blocks[0]["source"]["media_type"] == "image/jpeg"
        assert blocks[1]["text"] == base.INSTRUCTION

    def test_the_reply_is_constrained_to_the_schema(self):
        sent = claude.build_request("claude-sonnet-5", [a_jpeg()], detail=False)

        schema = sent["output_config"]["format"]
        assert schema["type"] == "json_schema"
        assert schema["schema"]["required"] == ["summary", "items", "fragile", "confidence"]
        assert schema["schema"]["additionalProperties"] is False
        # Strictness has to reach the nested item objects too.
        item = schema["schema"]["properties"]["items"]["items"]
        assert item["additionalProperties"] is False
        assert item["required"] == ["name", "qty", "category"]

    def test_the_quick_read_does_not_think(self):
        # Naming what is in a photograph is perception, not reasoning: thinking
        # buys nothing here and costs both latency and output tokens.
        sent = claude.build_request("claude-sonnet-5", [a_jpeg()], detail=False)

        assert sent["thinking"] == {"type": "disabled"}
        assert "effort" not in sent["output_config"]

    def test_the_closer_look_thinks(self):
        sent = claude.build_request("claude-opus-5", [a_jpeg()], detail=True)

        assert sent["thinking"] == {"type": "adaptive"}
        assert sent["output_config"]["effort"] == claude.DETAIL_EFFORT

    def test_the_detail_model_gets_the_careful_request(self):
        model, client = provider(reply(), detail_model="claude-opus-5")

        model.draft([a_jpeg()], model="claude-opus-5")

        assert client.messages.sent[0]["thinking"] == {"type": "adaptive"}


# --- the reply ------------------------------------------------------------


class TestWhatIsReadBack:
    def test_a_constrained_reply_becomes_a_draft(self):
        draft = claude.read_response(reply())

        assert draft.summary == "kettle, mugs and a toaster"
        assert [(i.name, i.qty) for i in draft.items] == [("kettle", 1), ("mug", 3)]
        assert draft.fragile is True

    def test_prose_around_the_json_is_still_read(self):
        # base.parse's discipline is kept even though the format makes it
        # unnecessary: a future model, or a fallback path, may wrap it.
        draft = claude.read_response(reply(f"Sure! Here it is:\n```json\n{REPLY}\n```"))

        assert draft.summary == "kettle, mugs and a toaster"

    def test_a_refusal_is_not_an_empty_box(self):
        with pytest.raises(base.DraftUnreadable) as failure:
            claude.read_response(reply("", stop_reason="refusal"))

        assert "declined" in str(failure.value)

    def test_a_truncated_reply_says_so(self):
        with pytest.raises(base.DraftUnreadable) as failure:
            claude.read_response(reply('{"summary": "kett', stop_reason="max_tokens"))

        assert "ran out of room" in str(failure.value)

    def test_a_reply_with_no_text_at_all_raises(self):
        empty = SimpleNamespace(content=[], stop_reason="end_turn", stop_details=None, usage=None)

        with pytest.raises(base.DraftUnreadable):
            claude.read_response(empty)


# --- what it cost ---------------------------------------------------------


class TestCost:
    def test_a_photo_on_the_quick_tier(self):
        # 2,460 image tokens + a ~300 token prompt, 200 out: 75 cents a hundred.
        assert claude.cost_usd("claude-sonnet-5", 2760, 200) == pytest.approx(0.00752)

    def test_a_photo_on_the_closer_look(self):
        assert claude.cost_usd("claude-opus-5", 2760, 200) == pytest.approx(0.0188)

    def test_a_dated_snapshot_is_priced_as_its_family(self):
        assert claude.cost_usd("claude-sonnet-5-20260514", 1_000_000, 0) == pytest.approx(2.0)

    def test_an_unknown_model_is_priced_at_the_dearest_rate(self):
        # Never guess low: a budget that under-counts is a budget that is
        # quietly exceeded.
        dearest = max(rate for rate, _ in claude.PRICES.values())

        assert claude.cost_usd("claude-something-new", 1_000_000, 0) == pytest.approx(dearest)

    def test_a_local_model_costs_nothing(self):
        assert claude.cost_usd("qwen3-vl:4b-instruct", 2760, 200) == 0.0

    def test_the_draft_records_what_it_cost(self):
        model, _ = provider(reply(input_tokens=2760, output_tokens=200))

        model.draft([a_jpeg()], model="claude-sonnet-5")

        assert model.last == base.Reading(
            provider="claude",
            model="claude-sonnet-5",
            input_tokens=2760,
            output_tokens=200,
            cost_usd=pytest.approx(0.00752),
        )


# --- failing ---------------------------------------------------------------


def an_api_error(cls, status):
    response = SimpleNamespace(status_code=status, headers={}, request=None)
    return cls("boom", response=response, body=None)


class TestFailing:
    @pytest.mark.parametrize(
        ("error", "says"),
        [
            (anthropic.AuthenticationError, "key"),
            (anthropic.RateLimitError, "rate"),
            (anthropic.PermissionDeniedError, "not allowed"),
        ],
    )
    def test_an_api_refusal_is_a_readable_failure(self, error, says):
        model, _ = provider(an_api_error(error, 401))

        with pytest.raises(base.DraftUnreadable) as failure:
            model.draft([a_jpeg()], model="claude-sonnet-5")

        assert says in str(failure.value).lower()

    def test_no_network_is_a_readable_failure(self):
        model, _ = provider(anthropic.APIConnectionError(request=None))

        with pytest.raises(base.DraftUnreadable) as failure:
            model.draft([a_jpeg()], model="claude-sonnet-5")

        assert "could not be reached" in str(failure.value)

    def test_a_key_never_reaches_a_message(self):
        # An error that quotes the request back would otherwise put the key in
        # the job row, the event log and the phone's screen.
        leaky = anthropic.BadRequestError(
            "bad x-api-key: sk-ant-api03-SECRETVALUE-xyz",
            response=SimpleNamespace(status_code=400, headers={}, request=None),
            body=None,
        )
        model, _ = provider(leaky)

        with pytest.raises(base.DraftUnreadable) as failure:
            model.draft([a_jpeg()], model="claude-sonnet-5")

        assert "sk-ant" not in str(failure.value)

    def test_redaction_leaves_the_rest_of_the_message(self):
        assert claude.redact("bad key sk-ant-api03-abc_DEF-123 sent") == "bad key [redacted] sent"


class TestConstruction:
    def test_no_key_means_no_provider(self):
        # The one thing that must never happen by accident: a client built
        # with no key of our own, which the SDK would then resolve from
        # whatever credentials happen to be on the machine.
        assert claude.provider_for(None, detail_model="claude-opus-5") is None

    def test_a_key_makes_one(self):
        made = claude.provider_for("sk-ant-test", detail_model="claude-opus-5")

        assert made is not None
        assert made.name == "claude"


class TestSayingWhyTheKeyWasRefused:
    def refusal(self, body):
        return anthropic.AuthenticationError(
            "invalid x-api-key",
            response=SimpleNamespace(status_code=401, headers={}, request=None),
            body=body,
        )

    def test_the_api_s_own_error_type_is_carried(self):
        model, _ = provider(self.refusal({"error": {"type": "authentication_error"}}))

        with pytest.raises(base.DraftUnreadable) as failure:
            model.draft([a_jpeg()], model="claude-sonnet-5")

        assert "authentication_error" in str(failure.value)
        assert "ANTHROPIC_API_KEY in .env" in str(failure.value)

    def test_a_body_that_says_nothing_still_gives_a_usable_message(self):
        model, _ = provider(self.refusal(None))

        with pytest.raises(base.DraftUnreadable) as failure:
            model.draft([a_jpeg()], model="claude-sonnet-5")

        assert "HTTP 401" in str(failure.value)

    def test_no_part_of_the_key_is_ever_shown(self):
        model, _ = provider(
            self.refusal({"error": {"type": "authentication_error", "key": "sk-ant-secret"}})
        )

        with pytest.raises(base.DraftUnreadable) as failure:
            model.draft([a_jpeg()], model="claude-sonnet-5")

        assert "sk-ant" not in str(failure.value)
