"""Cloud first, the local model when the cloud cannot answer.

This is the whole point of the hybrid, and it is not a nicety. During an
actual house move the Mac gets unplugged and carried to a van; the tailnet
survives things the internet does not, and the router is in a box. A read that
cannot reach the API should get *worse* -- a local model that names things less
precisely -- not stop. Everything that makes the cloud unavailable comes to the
same place: unreachable, unauthorised, rate limited, refused, over budget, or
simply no key configured.

Which one answered is recorded on `last`, because with two models in play a
wrong item has to be traceable to the model that wrote it -- and the model
named on the job when it was queued is only the one that was *tried* first.

The two tiers have different names on either side (`claude-sonnet-5` means
nothing to Ollama), so the pair carries a mapping from the cloud model to the
local one that stands in for it.
"""

from __future__ import annotations

import logging

from . import base

log = logging.getLogger(__name__)


class Hybrid:
    """A cloud provider with a local one behind it."""

    name = "hybrid"

    def __init__(
        self,
        *,
        cloud: base.VisionProvider | None,
        local: base.VisionProvider,
        fallbacks: dict[str, str] | None = None,
        use_cloud: bool = True,
    ) -> None:
        self.cloud = cloud
        self.local = local
        self.fallbacks = dict(fallbacks or {})
        self.use_cloud = use_cloud
        self.last: base.Reading | None = None

    def local_only(self) -> Hybrid:
        """The same pair with the cloud tier withdrawn.

        A new object rather than a flag flipped in place: the decision is made
        per job (the budget can be crossed mid-queue) and the pair it came
        from may be the one the next job uses.
        """
        return Hybrid(cloud=self.cloud, local=self.local, fallbacks=self.fallbacks, use_cloud=False)

    def _reading(self, provider: base.VisionProvider, model: str) -> base.Reading:
        # A provider that says nothing about cost cost nothing -- which is the
        # honest answer for a local model, and keeps third-party providers
        # (and the suite's own fakes) working without a bookkeeping method.
        return getattr(provider, "last", None) or base.Reading(provider=provider.name, model=model)

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        self.last = None
        refused: base.DraftUnreadable | None = None

        if self.use_cloud and self.cloud is not None:
            try:
                found = self.cloud.draft(images, model=model)
            except base.DraftUnreadable as failure:
                # The message has already been through redact(); it is safe to
                # log and safe to put in front of somebody.
                log.warning("cloud read failed, falling back to the local model: %s", failure)
                refused = failure
            else:
                self.last = self._reading(self.cloud, model)
                return found

        local_model = self.fallbacks.get(model, model)
        try:
            found = self.local.draft(images, model=local_model)
        except base.DraftUnreadable as failure:
            if refused is None:
                raise
            # Both reasons, cloud first: the local one is usually the more
            # actionable ("ollama is not running"), so it reads last.
            raise base.DraftUnreadable(f"{refused}; and the local model: {failure}") from failure

        self.last = self._reading(self.local, local_model)
        return found
