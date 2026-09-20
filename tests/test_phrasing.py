"""Asking a small model to phrase a summary, and falling back when it cannot.

Nothing here calls a model: the point is that the request we build is the one
we meant, that a reply is mined as forgivingly as a vision draft is, and above
all that **every** way this can go wrong ends in the assembled line rather than
in an error. Offline has to keep working.
"""

import json

import pytest

from movingbox import phrasing, summarise


def things(*names):
    return [{"name": n, "qty": 1} for n in names]


KITCHEN = things("stock pot", "stand mixer", "baking pan", "colander")


class Says:
    """A phraser that answers, or fails, as the test tells it to."""

    name = "stub"

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def phrase(self, contents, *, model):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class TestTheRequest:
    def test_it_names_the_model_and_asks_for_one_answer(self):
        request = phrasing.build_request("qwen2.5:7b", KITCHEN)

        assert request["model"] == "qwen2.5:7b"
        assert request["stream"] is False

    def test_it_asks_for_structured_output(self):
        # Same reason as the vision request: constraining the output is
        # cheaper than mining prose for it.
        request = phrasing.build_request("qwen2.5:7b", KITCHEN)

        assert request["format"]["type"] == "object"
        assert "summary" in request["format"]["properties"]

    def test_the_context_size_matches_the_warm_ping(self):
        # Ollama keys a loaded model by its runner options: ask for a
        # different num_ctx than the one it was warmed with and it unloads and
        # reloads, which is the cold start the warmer exists to prevent.
        request = phrasing.build_request("qwen2.5:7b", KITCHEN)

        assert (
            request["options"]["num_ctx"]
            == phrasing.warm_request("qwen2.5:7b")["options"]["num_ctx"]
        )

    def test_every_request_keeps_the_model_in_memory(self):
        request = phrasing.build_request("qwen2.5:7b", KITCHEN)

        assert request["keep_alive"] == phrasing.KEEP_ALIVE
        assert phrasing.warm_request("qwen2.5:7b")["keep_alive"] == phrasing.KEEP_ALIVE

    def test_the_system_prompt_is_its_own_message(self):
        request = phrasing.build_request("qwen2.5:7b", KITCHEN)

        assert request["messages"][0]["role"] == "system"

    def test_no_images_are_sent(self):
        # Text only, whatever kind of model is named: a vision model asked for
        # a summary of a list has nothing to look at.
        request = phrasing.build_request("qwen3-vl:4b-instruct", KITCHEN)

        assert all("images" not in message for message in request["messages"])

    def test_the_contents_are_in_the_prompt_with_their_counts(self):
        request = phrasing.build_request("qwen2.5:7b", [{"name": "baking pan", "qty": 3}])

        asked = request["messages"][-1]["content"]

        assert "3 baking pans" in asked

    def test_a_single_thing_is_not_given_a_count(self):
        request = phrasing.build_request("qwen2.5:7b", [{"name": "kettle", "qty": 1}])

        assert "kettle" in request["messages"][-1]["content"]
        assert "1 kettle" not in request["messages"][-1]["content"]


class TestTheReply:
    def test_a_well_formed_reply_is_the_summary(self):
        payload = {"message": {"content": json.dumps({"summary": "Kitchen gear - a stock pot"})}}

        assert phrasing.read_response(payload) == "Kitchen gear - a stock pot"

    def test_a_reply_wrapped_in_a_markdown_fence_still_reads(self):
        body = '```json\n{"summary": "Garage tools - clamps"}\n```'

        assert phrasing.read_response({"message": {"content": body}}) == "Garage tools - clamps"

    def test_prose_around_the_json_is_tolerated(self):
        body = 'Sure! Here you go:\n{"summary": "Winter gear - a coat"}\nHope that helps.'

        assert phrasing.read_response({"message": {"content": body}}) == "Winter gear - a coat"

    def test_an_answer_left_in_thinking_is_still_found(self):
        # Ollama 0.34 with `format` set puts a thinking checkpoint's JSON in
        # `thinking` and leaves `content` empty. 42 of 42 vision replies were
        # lost that way once; this path is the same trap.
        body = {"message": {"content": "", "thinking": '{"summary": "Books - two albums"}'}}

        assert phrasing.read_response(body) == "Books - two albums"

    def test_several_lines_are_folded_into_one(self):
        # It is printed on a single line of tape.
        body = {"message": {"content": json.dumps({"summary": "Kitchen gear\n- a stock pot"})}}

        assert "\n" not in phrasing.read_response(body)

    def test_a_long_answer_is_cut_to_fit_a_label(self):
        body = {"message": {"content": json.dumps({"summary": "thing, " * 200})}}

        assert len(phrasing.read_response(body)) <= summarise.MAX_LENGTH

    def test_an_empty_summary_raises_rather_than_being_used(self):
        # An empty line is not an answer; it would read as "nothing in here".
        with pytest.raises(phrasing.Unusable):
            phrasing.read_response({"message": {"content": json.dumps({"summary": "   "})}})

    def test_a_reply_with_no_json_raises(self):
        with pytest.raises(phrasing.Unusable):
            phrasing.read_response({"message": {"content": "I cannot help with that."}})

    def test_an_unexpected_shape_raises(self):
        with pytest.raises(phrasing.Unusable):
            phrasing.read_response({"error": "model not found"})


class TestFallingBackIsNormal:
    """Never an error path: the assembled line is always there to hand back."""

    def test_a_good_answer_is_used_and_says_it_came_from_the_model(self):
        said = Says("Kitchen gear - a stock pot and a stand mixer")

        assert phrasing.summary_for(KITCHEN, said, model="m") == (
            "Kitchen gear - a stock pot and a stand mixer",
            "model",
        )

    def test_a_model_that_cannot_be_reached_gives_the_assembled_line(self):
        said = Says(phrasing.Unusable("nothing answered at http://localhost:11434"))

        summary, source = phrasing.summary_for(KITCHEN, said, model="m")

        assert (summary, source) == (summarise.from_items(KITCHEN), "assembled")

    def test_any_failure_at_all_gives_the_assembled_line(self):
        # Not just the ones we thought of: a summary is never worth a 500.
        said = Says(RuntimeError("something nobody predicted"))

        assert phrasing.summary_for(KITCHEN, said, model="m")[1] == "assembled"

    def test_with_no_phraser_it_is_plain_assembly(self):
        assert phrasing.summary_for(KITCHEN, None, model="m") == (
            summarise.from_items(KITCHEN),
            "assembled",
        )

    def test_nothing_in_the_box_is_not_worth_asking_about(self):
        said = Says("Empty - nothing at all")

        assert phrasing.summary_for([], said, model="m") == ("", "assembled")
        assert said.calls == 0

    def test_too_few_things_to_generalise_are_not_sent_to_the_model(self):
        # Measured, not guessed: on a one- or two-line list the models tested
        # padded the answer out of the prompt's own example ("a kettle, clamps
        # and a tin of screws"). There is nothing to generalise from two
        # things, and "kettle, toaster" is already the best line there is.
        said = Says("Kitchen essentials - a kettle, clamps and a tin of screws")

        summary, source = phrasing.summary_for(things("kettle", "toaster"), said, model="m")

        assert (summary, source) == ("kettle, toaster", "assembled")
        assert said.calls == 0

    def test_enough_things_are_sent(self):
        said = Says("Kitchen gear - a stock pot")

        assert phrasing.summary_for(KITCHEN, said, model="m")[1] == "model"
        assert said.calls == 1


class TestKeepingTheModelWarm:
    """The first press of an evening must not pay for a model load."""

    def test_it_does_nothing_when_phrasing_is_off(self, config):
        # Which is every test: a Config built directly has it off, so nothing
        # in this suite can reach a model.
        pings = []
        warmer = phrasing.Warmer(config, ping=lambda c: pings.append(c) or True)

        warmer.run()

        assert pings == []

    def test_it_does_nothing_for_a_stub_provider(self, config):
        pings = []
        warmer = phrasing.Warmer(
            config.replace(phrase_summaries=True, vision_provider="stub"),
            ping=lambda c: pings.append(c) or True,
        )

        warmer.run()

        assert pings == []

    def test_it_pings_until_stopped(self, config):
        pings = []

        def ping(_):
            pings.append(1)
            if len(pings) == 3:
                warmer.stop()
            return True

        warmer = phrasing.Warmer(
            config.replace(phrase_summaries=True), ping=ping, interval=0, retry=0
        )
        warmer.run()

        assert len(pings) == 3

    def test_ollama_being_down_does_not_kill_the_thread(self, config):
        # It has to survive a laptop that is awake before Ollama is.
        tries = []

        def ping(_):
            tries.append(1)
            if len(tries) == 3:
                warmer.stop()
                return True
            raise OSError("connection refused")

        warmer = phrasing.Warmer(
            config.replace(phrase_summaries=True), ping=ping, interval=0, retry=0
        )
        warmer.run()

        assert len(tries) == 3

    def test_it_refreshes_before_the_model_would_lapse(self):
        # Derived from keep_alive rather than typed twice: a refresh slower
        # than the model's idle timeout warms nothing.
        assert phrasing.REFRESH < phrasing.KEEP_ALIVE_SECONDS
