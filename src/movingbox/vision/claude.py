"""Reading a photo with Claude, through the official Anthropic SDK.

The cloud half of the hybrid. `vision.base.VisionProvider` was always the seam
for this; this is the thing that slots into it. The local provider stays, and
`vision.hybrid` is what falls back to it -- see that module for why.

Three decisions worth the words:

**Structured output, not mined JSON.** `base.parse` is deliberately forgiving
because small local models wrap their JSON in prose. The API can simply be told
the shape (`output_config.format`), so the reply *is* the object and there is
nothing to mine. `read_response` still goes through `base.parse`: the
discipline it enforces at the other end -- a reply with no usable draft raises
rather than reading as "the model saw an empty box" -- is the half that matters,
and a fenced or prefaced reply from some later model costs nothing to survive.

**No thinking on the quick tier.** Naming what is in a photograph is
perception. Thinking would add seconds and output tokens to every one of a
thousand photos and has no reasoning to do. The closer look keeps it, because
that tier exists for the hard ones -- handwriting, a brand name on a spine --
and is asked for by hand, one photo at a time.

**The picture is shrunk before it is sent.** The API resizes anything over
1568 px on the long edge anyway, so doing it here changes nothing about the
reading and makes both the upload and the token count predictable. What is
*stored* stays at 2048 px: see storage.py, and the note in CLAUDE.md about
1024 px having been a bad trade.
"""

from __future__ import annotations

import base64
import io
import math
import re
from typing import Any

import anthropic
from PIL import Image, UnidentifiedImageError

from . import base

#: The API resizes any image whose long edge is over this, so there is nothing
#: to gain by sending more -- only a slower upload.
API_MAX_EDGE = 1568

#: An image costs about width x height / 750 tokens. A stored 1536 x 2048 photo
#: becomes 1176 x 1568, which is ~2,460 tokens: the number the budget is built on.
TOKENS_PER_PIXEL = 750

#: Room for the reply. The draft itself is ~200 tokens; the closer look's
#: thinking is billed out of the same allowance, hence the larger one.
MAX_TOKENS = 2048
DETAIL_MAX_TOKENS = 8192

#: How hard the closer look tries. Thinking is left adaptive rather than
#: disabled -- on this model tier, explicitly disabling it is documented to
#: leak stray tags into the visible reply -- but `high` (the default) spends
#: more than reading a photograph is worth.
DETAIL_EFFORT = "medium"

#: 60 s is generous for one photo and one screenful of JSON, and short enough
#: that a wedged request falls back to the local model while somebody is still
#: standing over the box. The local provider's 20 minutes is the opposite
#: trade, and right there: nothing else can answer.
TIMEOUT = 60.0

#: Anthropic list prices, USD per million tokens, (input, output), read from
#: the API's own model table on 2026-09-20. Keyed by model family: a dated
#: snapshot (`claude-sonnet-5-20260514`) is priced by its longest matching
#: prefix. Only the models this app can be configured to use are listed.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    """Take any API key out of a string before it is stored or shown.

    A 400 from the API can quote the offending header back. That string goes
    into `ai_jobs.error`, into an event, and onto a phone screen -- so it is
    scrubbed at the one place every message passes through.
    """
    return _KEY.sub("[redacted]", text or "")


# --- the picture ----------------------------------------------------------


_MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}


def image_tokens(width: int, height: int) -> int:
    """Roughly what an image of this size costs in input tokens."""
    return math.ceil(width * height / TOKENS_PER_PIXEL)


def for_api(data: bytes, max_edge: int = API_MAX_EDGE) -> tuple[bytes, str]:
    """The bytes to send and their media type. Shrinks; never grows.

    An image already inside the ceiling is passed through untouched -- there is
    no reading to be gained by re-encoding it, and a re-encode would only lose
    a little of the detail the closer look exists to find.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as bad:
        raise base.DraftUnreadable("that photo could not be decoded") from bad

    if max(image.size) <= max_edge:
        return data, _MEDIA_TYPES.get(image.format or "", "image/jpeg")

    shrunk = image.convert("RGB")
    shrunk.thumbnail((max_edge, max_edge), Image.LANCZOS)
    buffer = io.BytesIO()
    shrunk.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue(), "image/jpeg"


# --- the request ----------------------------------------------------------


def strict(schema: dict[str, Any]) -> dict[str, Any]:
    """`base.SCHEMA`, tightened into what structured output requires.

    Every property required, no extras, all the way down. Written as a
    transform rather than a second copy of the schema so there stays one
    description of a draft, shared with the local provider.
    """
    if schema.get("type") == "object":
        properties = {key: strict(value) for key, value in schema.get("properties", {}).items()}
        return {
            **schema,
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        return {**schema, "items": strict(schema["items"])}
    return schema


def build_request(model: str, images: list[bytes], *, detail: bool) -> dict[str, Any]:
    """The Messages request for one photo (or several) on one tier."""
    blocks: list[dict[str, Any]] = []
    for image in images:
        data, media_type = for_api(image)
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode(),
                },
            }
        )
    # The picture first, then what to do with it: the instruction reads as
    # being about the thing above it, which is how the API's own examples run.
    blocks.append({"type": "text", "text": base.INSTRUCTION})

    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": strict(base.SCHEMA)}
    }
    if detail:
        output_config["effort"] = DETAIL_EFFORT

    return {
        "model": model,
        "max_tokens": DETAIL_MAX_TOKENS if detail else MAX_TOKENS,
        "system": base.SYSTEM,
        "thinking": {"type": "adaptive"} if detail else {"type": "disabled"},
        "output_config": output_config,
        "messages": [{"role": "user", "content": blocks}],
    }


# --- the reply ------------------------------------------------------------


def read_response(message: Any) -> base.BoxDraft:
    """Turn a Messages response into a draft, or raise DraftUnreadable.

    Both failure modes that are not simply "bad JSON" are named, because the
    fallback logs them and they mean different things: a refusal will happen
    again on a retry, a truncation is a setting of ours.
    """
    if getattr(message, "stop_reason", None) == "refusal":
        raise base.DraftUnreadable("the model declined to read this photo")

    text = "\n".join(
        block.text for block in (getattr(message, "content", None) or []) if block.type == "text"
    )
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise base.DraftUnreadable("the model ran out of room before finishing the list")
    if not text.strip():
        raise base.DraftUnreadable("the model returned nothing to read")
    return base.parse(text)


def spent(message: Any, model: str) -> base.Reading:
    """What the API says this call used, priced."""
    usage = getattr(message, "usage", None)
    incoming = int(getattr(usage, "input_tokens", 0) or 0)
    # Cached reads are billed at a tenth, but nothing here is cacheable: every
    # photo is different and the prompt is far short of the minimum prefix.
    # Counted at full price if it ever appears, so the budget never flatters.
    incoming += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    incoming += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    outgoing = int(getattr(usage, "output_tokens", 0) or 0)
    return base.Reading(
        provider=ClaudeProvider.name,
        model=model,
        input_tokens=incoming,
        output_tokens=outgoing,
        cost_usd=cost_usd(model, incoming, outgoing),
    )


# --- what it cost ---------------------------------------------------------


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollars for one call. Zero for anything that is not a Claude model.

    An unrecognised *Claude* model is priced at the dearest rate known rather
    than at nothing: a budget that under-counts is a budget that gets quietly
    exceeded, and the failure should be "stopped too early", not "kept going".
    """
    if not model.startswith("claude-"):
        return 0.0
    matches = [name for name in PRICES if model.startswith(name)]
    if matches:
        rate_in, rate_out = PRICES[max(matches, key=len)]
    else:
        rate_in = max(rate for rate, _ in PRICES.values())
        rate_out = max(rate for _, rate in PRICES.values())
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


# --- the provider ---------------------------------------------------------


class ClaudeProvider:
    """Reads a photo with Claude. One call, no loop, no tools.

    The client is always built with an explicit key. A bare `Anthropic()`
    would resolve credentials from whatever is on the machine -- an env var, a
    stored OAuth profile -- and spend somebody's money without being asked.
    """

    name = "claude"

    def __init__(
        self,
        api_key: str,
        *,
        detail_model: str | None = None,
        client: Any | None = None,
        timeout: float = TIMEOUT,
    ) -> None:
        if not api_key:
            raise ValueError("ClaudeProvider needs an API key")
        self.detail_model = detail_model
        self.last: base.Reading | None = None
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout)

    def explain(self, failure: Exception) -> str:
        """A failure message that names the problem and carries no secret."""
        if isinstance(failure, anthropic.AuthenticationError):
            return "the Anthropic API key was refused; check ANTHROPIC_API_KEY"
        if isinstance(failure, anthropic.PermissionDeniedError):
            return "this Anthropic API key is not allowed to use that model"
        if isinstance(failure, anthropic.RateLimitError):
            return "the Anthropic API is rate limiting this key"
        if isinstance(failure, anthropic.APIConnectionError):
            return "the Anthropic API could not be reached"
        status = getattr(failure, "status_code", None)
        if status is not None:
            return redact(f"the Anthropic API returned HTTP {status}: {failure}")
        return redact(f"the Anthropic API failed: {failure}")

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        request = build_request(model, images, detail=model == self.detail_model)
        try:
            message = self._client.messages.create(**request)
        except anthropic.AnthropicError as failure:
            self.last = None
            raise base.DraftUnreadable(self.explain(failure)) from failure

        # Recorded before parsing: a reply that cost money and then failed to
        # read still cost money, and the budget has to know.
        self.last = spent(message, model)
        return read_response(message)


def provider_for(api_key: str | None, *, detail_model: str | None) -> ClaudeProvider | None:
    """A provider, or None when there is no key. Never guesses at credentials."""
    if not (api_key or "").strip():
        return None
    return ClaudeProvider(api_key.strip(), detail_model=detail_model)
