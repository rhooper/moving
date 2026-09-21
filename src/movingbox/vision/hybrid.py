"""Cloud first, the local model when the cloud cannot answer.

Every way the cloud can be unavailable -- unreachable, unauthorised, rate
limited, refused, over budget, no key -- makes the read worse, never stops it.
`last` records which model actually answered. `fallbacks` maps each cloud
model name to the local model that stands in for it.
"""

from __future__ import annotations

import dataclasses
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
        """A copy of the pair with the cloud tier withdrawn; the original is unchanged."""
        return Hybrid(cloud=self.cloud, local=self.local, fallbacks=self.fallbacks, use_cloud=False)

    def _reading(self, provider: base.VisionProvider, model: str) -> base.Reading:
        # A provider that reports no cost cost nothing.
        return getattr(provider, "last", None) or base.Reading(provider=provider.name, model=model)

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        self.last = None
        refused: base.DraftUnreadable | None = None
        #: A cloud call that could not be read but was billed all the same.
        wasted: base.Reading | None = None

        if self.use_cloud and self.cloud is not None:
            try:
                found = self.cloud.draft(images, model=model)
            except base.DraftUnreadable as failure:
                # Already redacted by the cloud provider, so safe to log and show.
                log.warning("cloud read failed, falling back to the local model: %s", failure)
                refused = failure
                wasted = getattr(self.cloud, "last", None)
                self.last = wasted
            else:
                self.last = self._reading(self.cloud, model)
                return found

        local_model = self.fallbacks.get(model, model)
        try:
            found = self.local.draft(images, model=local_model)
        except base.DraftUnreadable as failure:
            if refused is None:
                raise
            # `last` stays at the wasted attempt, so the job still records its cost.
            raise base.DraftUnreadable(f"{refused}; and the local model: {failure}") from failure

        reading = self._reading(self.local, local_model)
        if wasted is not None:
            # `model` names who answered; the cost is what the whole job cost.
            reading = dataclasses.replace(
                reading,
                input_tokens=reading.input_tokens + wasted.input_tokens,
                output_tokens=reading.output_tokens + wasted.output_tokens,
                cost_usd=reading.cost_usd + wasted.cost_usd,
            )
        self.last = reading
        return found
