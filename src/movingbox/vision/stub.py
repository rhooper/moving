"""A vision provider that sees the same thing every time.

For building and checking the UI without a model: it waits, then returns a
fixed draft. Deterministic, offline, and as slow as you tell it to be -- which
matters, because the thing being checked is usually the countdown.

Selected with ``MOVING_VISION_PROVIDER=stub``; ``MOVING_VISION_STUB_SECONDS``
sets the wait. Never the default, and never used by the test suite, which
passes its own providers in directly.
"""

from __future__ import annotations

import time

from . import base


class StubProvider:
    name = "stub"

    def __init__(self, seconds: float = 3.0, detail_model: str | None = None):
        self.seconds = seconds
        self.detail_model = detail_model

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        time.sleep(self.seconds)
        items = [
            base.DraftItem(name="kettle", qty=1),
            base.DraftItem(name="mug", qty=3),
            base.DraftItem(name="toaster", qty=1),
        ]
        if model == self.detail_model:
            # A closer look finds more, and counts better.
            items[1] = base.DraftItem(name="mug", qty=4)
            items.append(base.DraftItem(name="Dualit toaster manual", qty=1))
            return base.BoxDraft(summary="kettle, four mugs, a toaster and its manual", items=items)
        return base.BoxDraft(summary="kettle, mugs and a toaster", items=items)
