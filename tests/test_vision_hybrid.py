"""The hybrid: cloud first, the local model when the cloud cannot answer.

The point of the fallback is a house move. The Mac gets unplugged and carried
to a van; the tailnet survives things the internet does not. A read that cannot
reach the API should get worse, not stop.
"""

import pytest

from movingbox.vision import base, hybrid


class Answers:
    """A provider that answers, or fails, and remembers what it was asked."""

    def __init__(self, name, draft=None, failure=None, cost=0.0):
        self.name = name
        self._draft = draft or base.BoxDraft(summary=name, items=[])
        self._failure = failure
        self._cost = cost
        self.asked: list[str] = []
        self.last: base.Reading | None = None

    def draft(self, images, *, model):
        self.asked.append(model)
        if self._failure is not None:
            self.last = None
            raise self._failure
        self.last = base.Reading(provider=self.name, model=model, cost_usd=self._cost)
        return self._draft


FALLBACKS = {"claude-sonnet-5": "qwen3-vl:4b-instruct", "claude-opus-5": "qwen3-vl:8b-instruct"}


def both(cloud, local, **kwargs):
    return hybrid.Hybrid(cloud=cloud, local=local, fallbacks=FALLBACKS, **kwargs)


class TestWhenTheCloudAnswers:
    def test_the_local_model_is_not_disturbed(self):
        cloud, local = Answers("claude", cost=0.0075), Answers("ollama")

        draft = both(cloud, local).draft([b"jpeg"], model="claude-sonnet-5")

        assert draft.summary == "claude"
        assert local.asked == []

    def test_what_it_cost_is_what_is_recorded(self):
        cloud, local = Answers("claude", cost=0.0075), Answers("ollama")
        pair = both(cloud, local)

        pair.draft([b"jpeg"], model="claude-sonnet-5")

        assert pair.last == base.Reading(
            provider="claude", model="claude-sonnet-5", cost_usd=0.0075
        )


class TestWhenItCannot:
    @pytest.mark.parametrize(
        "failure",
        [
            base.DraftUnreadable("the Anthropic API could not be reached"),
            base.DraftUnreadable("the Anthropic API key was refused"),
            base.DraftUnreadable("the Anthropic API is rate limiting this key"),
            base.DraftUnreadable("the model declined to read this photo"),
        ],
    )
    def test_the_local_model_takes_over(self, failure):
        cloud = Answers("claude", failure=failure)
        local = Answers("ollama")

        draft = both(cloud, local).draft([b"jpeg"], model="claude-sonnet-5")

        assert draft.summary == "ollama"

    def test_the_local_model_is_asked_for_its_own_model(self):
        # "claude-opus-5" means nothing to Ollama. Each cloud tier names the
        # local model that stands in for it.
        cloud = Answers("claude", failure=base.DraftUnreadable("nope"))
        local = Answers("ollama")

        both(cloud, local).draft([b"jpeg"], model="claude-opus-5")

        assert local.asked == ["qwen3-vl:8b-instruct"]

    def test_the_job_records_the_model_that_actually_read_it(self):
        cloud = Answers("claude", failure=base.DraftUnreadable("nope"))
        local = Answers("ollama")
        pair = both(cloud, local)

        pair.draft([b"jpeg"], model="claude-sonnet-5")

        assert pair.last == base.Reading(
            provider="ollama", model="qwen3-vl:4b-instruct", cost_usd=0.0
        )

    def test_when_neither_can_read_it_both_reasons_are_given(self):
        cloud = Answers("claude", failure=base.DraftUnreadable("the API could not be reached"))
        local = Answers("ollama", failure=base.DraftUnreadable("nothing answered at localhost"))

        with pytest.raises(base.DraftUnreadable) as failure:
            both(cloud, local).draft([b"jpeg"], model="claude-sonnet-5")

        assert "the API could not be reached" in str(failure.value)
        assert "nothing answered at localhost" in str(failure.value)


class TestWhenThereIsNoCloudAtAll:
    def test_no_key_means_the_local_model_and_no_attempt(self):
        local = Answers("ollama")
        pair = hybrid.Hybrid(cloud=None, local=local, fallbacks=FALLBACKS)

        draft = pair.draft([b"jpeg"], model="claude-sonnet-5")

        assert draft.summary == "ollama"
        assert local.asked == ["qwen3-vl:4b-instruct"]
        assert pair.last.provider == "ollama"

    def test_past_the_budget_the_cloud_is_simply_not_offered(self):
        cloud, local = Answers("claude"), Answers("ollama")

        both(cloud, local).local_only().draft([b"jpeg"], model="claude-sonnet-5")

        assert cloud.asked == []
        assert local.asked == ["qwen3-vl:4b-instruct"]

    def test_local_only_does_not_change_the_pair_it_came_from(self):
        cloud, local = Answers("claude"), Answers("ollama")
        pair = both(cloud, local)

        pair.local_only()
        pair.draft([b"jpeg"], model="claude-sonnet-5")

        assert cloud.asked == ["claude-sonnet-5"]


class TestBookkeeping:
    def test_a_provider_that_says_nothing_about_cost_still_gets_a_reading(self):
        class Silent:
            name = "ollama"

            def draft(self, images, *, model):
                return base.BoxDraft(summary="quiet", items=[])

        pair = hybrid.Hybrid(cloud=None, local=Silent(), fallbacks=FALLBACKS)
        pair.draft([b"jpeg"], model="claude-sonnet-5")

        assert pair.last == base.Reading(provider="ollama", model="qwen3-vl:4b-instruct")

    def test_a_failed_read_leaves_no_stale_reading(self):
        cloud = Answers("claude", failure=base.DraftUnreadable("no"))
        local = Answers("ollama", failure=base.DraftUnreadable("no"))
        pair = both(cloud, local)

        with pytest.raises(base.DraftUnreadable):
            pair.draft([b"jpeg"], model="claude-sonnet-5")

        assert pair.last is None
