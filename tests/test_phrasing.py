"""Asking a small model to phrase a summary, and falling back when it cannot.

Nothing here calls a model. The request built is the one meant, a reply is
mined as forgivingly as a vision draft is, and **every** way this can go wrong
ends in the assembled line rather than an error, so offline keeps working.
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

    def phrase(self, contents, *, model, context=None):
        self.calls += 1
        self.context = context
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
        assert "examples" in request["format"]["properties"]

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


OFFICE = {"kind": "Box", "room": "Office"}


def reply(kind="Kitchen gear", most=True, examples=("stock pot", "stand mixer")):
    return json.dumps({"kind": kind, "most_of_the_list": most, "examples": list(examples)})


class TestTheShapeAskedFor:
    """One heading for the whole container, then examples, then "etc.".

    Asked for as "prefer summary descriptions. Eg B-0062 could be Miscellaneous
    Office - lights, vacuum, leather, mug, figurine, etc. based on the contents
    and box information". The model answers in fields; `compose` writes the
    line, so the shape is code's and only the judgement is the model's.
    """

    def test_it_is_told_what_the_record_is_and_where_it_is_going(self):
        asked = phrasing.build_request("m", KITCHEN, OFFICE)["messages"][-1]["content"]

        assert "a box going to the Office, holding" in asked

    def test_with_no_room_it_is_just_the_kind(self):
        asked = phrasing.build_request("m", KITCHEN, {"kind": "Loose item"})["messages"][-1][
            "content"
        ]

        assert "a loose item holding" in asked

    def test_with_no_context_it_is_a_container(self):
        assert "a container holding" in phrasing.prompt(KITCHEN)[1]

    def test_it_asks_for_the_kind_whether_it_covers_most_and_examples(self):
        request = phrasing.build_request("m", KITCHEN)

        assert request["format"]["required"] == ["kind", "most_of_the_list", "examples"]
        asked = request["messages"][-1]["content"]
        # "Never the room": without it, B-0062 came back "Office supplies".
        assert "Never the room" in asked
        assert "jumble" in asked

    def test_the_same_contents_ask_the_same_question_every_time(self):
        # temperature 0: measured over seventeen real records, three runs each,
        # every answer was the same line.
        assert phrasing.build_request("m", KITCHEN)["options"]["temperature"] == 0

    def test_the_prompt_is_versioned_for_the_new_shape(self):
        assert phrasing.PROMPT_VERSION == "2026-10-04.1"


class TestComposingTheLine:
    def test_a_kind_that_covers_most_is_the_heading(self):
        line = phrasing.finish(reply(), KITCHEN * 2)

        assert line == "Kitchen gear - stock pot, stand mixer, etc."

    def test_a_jumble_is_miscellaneous_and_the_room(self):
        line = phrasing.finish(
            reply("Electronics", False, ["lights", "vacuum", "leather", "mug", "figurine"]),
            things(*"abcdefghij"),
            OFFICE,
        )

        assert line == "Miscellaneous Office - lights, vacuum, leather, mug, figurine, etc."

    def test_a_jumble_going_nowhere_yet_is_just_miscellaneous(self):
        line = phrasing.finish(reply(most=False), things(*"abcdef"))

        assert line.startswith("Miscellaneous - ")

    def test_an_empty_heading_is_miscellaneous(self):
        assert phrasing.finish(reply(kind="  "), KITCHEN * 2).startswith("Miscellaneous - ")

    def test_the_heading_starts_with_a_capital(self):
        assert phrasing.finish(reply(kind="filament spools"), KITCHEN).startswith("Filament")

    def test_no_etc_when_every_thing_was_named(self):
        line = phrasing.finish(reply(examples=["pot", "mixer", "pan", "colander"]), KITCHEN)

        assert line == "Kitchen gear - pot, mixer, pan, colander"

    def test_at_most_six_examples(self):
        line = phrasing.finish(reply(examples=list("abcdefgh")), things(*"abcdefgh"))

        assert line == "Kitchen gear - a, b, c, d, e, f, etc."

    def test_repeated_examples_are_named_once(self):
        line = phrasing.finish(
            reply(examples=["mug", "mugs", "Mug", "vase"]), things("mug", "vase")
        )

        assert line == "Kitchen gear - mug, vase"

    def test_a_long_line_drops_examples_rather_than_cutting_one(self):
        long = [f"something rather long number {n}" for n in range(6)]

        line = phrasing.finish(reply(examples=long), things(*"abcdefgh"))

        assert len(line) <= summarise.MAX_LENGTH
        assert line.endswith(", etc.")
        assert "…" not in line

    def test_no_examples_is_not_an_answer(self):
        with pytest.raises(phrasing.Unusable):
            phrasing.finish(reply(examples=[" ", ""]), KITCHEN)

    def test_examples_that_are_not_a_list_are_not_an_answer(self):
        with pytest.raises(phrasing.Unusable):
            phrasing.finish('{"kind": "Books", "most_of_the_list": true, "examples": "a, b"}')

    def test_a_reply_in_the_old_shape_is_used_as_it_is(self):
        # A plug-in written before 2026-10-04 asks for {"summary": ...}.
        assert phrasing.finish('{"summary": "Garage tools - clamps"}') == "Garage tools - clamps"


class TestTheReply:
    def test_a_well_formed_reply_is_the_summary(self):
        payload = {"message": {"content": reply()}}

        assert (
            phrasing.read_response(payload, KITCHEN)
            == "Kitchen gear - stock pot, stand mixer, etc."
        )

    def test_a_reply_wrapped_in_a_markdown_fence_still_reads(self):
        body = '```json\n{"summary": "Garage tools - clamps"}\n```'

        assert phrasing.read_response({"message": {"content": body}}) == "Garage tools - clamps"

    def test_prose_around_the_json_is_tolerated(self):
        body = 'Sure! Here you go:\n{"summary": "Winter gear - a coat"}\nHope that helps.'

        assert phrasing.read_response({"message": {"content": body}}) == "Winter gear - a coat"

    def test_an_answer_left_in_thinking_is_still_found(self):
        # Ollama 0.34 with `format` set puts a thinking checkpoint's JSON in
        # `thinking` and leaves `content` empty.
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
        # Measured against the label's own capacity: up to three distinct
        # things, the assembled list still fits the two lines a label with a
        # room band and handling chips leaves, so there is nothing to
        # summarise. It is also the band where the model invents -- three
        # unrelated things came back as "Home decor: paint, photo albums,
        # kitchen helper", a category the list does not support.
        said = Says("Kitchen essentials: kettles, toasters and mugs")

        summary, source = phrasing.summary_for(things("kettle", "toaster", "mug"), said, model="m")

        assert (summary, source) == ("kettle, toaster, mug", "assembled")
        assert said.calls == 0

    def test_enough_things_are_sent(self):
        # Four is where an assembled list stops fitting those two lines.
        said = Says("Kitchen gear: cookware and utensils, with a stand mixer")

        assert phrasing.summary_for(KITCHEN, said, model="m")[1] == "model"
        assert said.calls == 1

    def test_the_record_is_passed_to_the_phraser(self):
        said = Says("Kitchen gear - pot")

        phrasing.summary_for(KITCHEN, said, model="m", context=OFFICE)

        assert said.context == OFFICE

    def test_a_plug_in_written_before_context_falls_back(self):
        # Its phrase() takes no `context`: a TypeError, which is a failure
        # like any other, so the line is the assembled one rather than a 500.
        class Older:
            name = "older"

            def phrase(self, contents, *, model):
                return "never reached"

        assert phrasing.summary_for(KITCHEN, Older(), model="m", context=OFFICE)[1] == "assembled"


class TestTheStub:
    def test_it_is_miscellaneous_and_the_room_with_the_names(self):
        line = phrasing.StubPhraser().phrase(KITCHEN, model="m", context=OFFICE)

        assert line == "Miscellaneous Office - stock pot, stand mixer, baking pan, colander"


class TestKeepingTheModelWarm:
    """The first press of an evening must not pay for a model load."""

    def test_it_does_nothing_when_phrasing_is_off(self, config):
        # A directly built Config has it off, so no test can reach a model.
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
