"""Plug-in providers, written the way a third party would write one.

Named in tests as ``tests.fake_providers:vision`` and ``tests.fake_providers:phraser``.
Nothing here reaches a network: "the API" is a canned reply.
"""

from __future__ import annotations

from movingbox import phrasing
from movingbox.vision import base

#: The variable these providers look their key up by.
KEY = "FAKE_CLOUD_KEY"
#: A key the fake "API" refuses, after charging for the attempt.
REFUSED = "refused"


class Reader:
    name = "fakecloud"

    def __init__(self, key: str) -> None:
        self.key = key
        self.last: base.Reading | None = None
        self.models: list[str] = []

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        self.models.append(model)
        if self.key == REFUSED:
            self.last = base.Reading(self.name, model, input_tokens=10, cost_usd=0.01)
            raise base.DraftUnreadable("the fake API refused the key")
        self.last = base.Reading(
            self.name, model, input_tokens=100, output_tokens=20, cost_usd=0.02
        )
        return base.parse('{"summary": "a kettle", "items": [{"name": "kettle"}]}')


def vision(config, key):
    found = key(KEY)
    return Reader(found) if found else None


class Phraser:
    name = "fakephrase"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def phrase(self, contents, *, model: str) -> str:
        self.prompts.append(phrasing.prompt(contents))
        return phrasing.finish(f'{{"summary": "Kitchen things, by {model}."}}')


def phraser(config, key):
    return Phraser() if key(KEY) else None


not_callable = 3
