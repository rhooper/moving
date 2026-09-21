"""Reading a photo with Claude: the cloud half of `vision.hybrid`.

The API is given the draft's shape (`output_config.format`); the reply still
goes through `base.parse` so a reply with no usable draft raises. Photos are
shrunk to the API's own ceiling before sending, which leaves the reading
unchanged and the token count predictable.
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

#: The API resizes any image whose long edge is over this.
API_MAX_EDGE = 1568

#: An image costs about width x height / 750 input tokens.
TOKENS_PER_PIXEL = 750

#: Room for the reply; the draft itself is ~200 tokens.
MAX_TOKENS = 2048
DETAIL_MAX_TOKENS = 8192

#: The closer look's effort; `high` (the default) spends more than a photo is worth.
DETAIL_EFFORT = "medium"

#: Seconds. Short, so a wedged request falls back to the local model while
#: someone is still standing over the box.
TIMEOUT = 60.0

#: List prices, USD per million tokens, (input, output). A dated snapshot
#: (`claude-sonnet-5-20260514`) is priced by its longest matching prefix.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


def api_error_type(failure: Exception) -> str:
    """The API's own error type, e.g. "authentication_error"; never a value.

    Read from the structured body, where it is a fixed enum, not the free-text message.
    """
    body = getattr(failure, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return error["type"]
    return ""


def redact(text: str) -> str:
    """Take any API key out of a string before it is stored or shown.

    An API error can quote the key back, and messages reach `ai_jobs.error`,
    events and phone screens.
    """
    return _KEY.sub("[redacted]", text or "")


# --- the picture ----------------------------------------------------------


_MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}


def image_tokens(width: int, height: int) -> int:
    """Roughly what an image of this size costs in input tokens."""
    return math.ceil(width * height / TOKENS_PER_PIXEL)


def for_api(data: bytes, max_edge: int = API_MAX_EDGE) -> tuple[bytes, str]:
    """The bytes to send and their media type. Shrinks; never grows or re-encodes."""
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
    """`schema` with every property required and no extras, all the way down,
    as structured output requires.
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
    # Images before the instruction, as the API's own examples run.
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
        # Neither tier thinks: naming what is in a photo is perception. Measured
        # on the closer look too: same items, faster, no stray tags.
        "thinking": {"type": "disabled"},
        "output_config": output_config,
        "messages": [{"role": "user", "content": blocks}],
    }


# --- the reply ------------------------------------------------------------


def read_response(message: Any) -> base.BoxDraft:
    """Turn a Messages response into a draft, or raise DraftUnreadable.

    A refusal and a truncation are named: one recurs on retry, the other is our setting.
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
    # Cache tokens, should any appear, count at full price so the budget never flatters.
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

    An unrecognised Claude model is priced at the dearest known rate, so the
    budget errs towards stopping early.
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

    Always built with an explicit key: a bare `Anthropic()` would pick up
    whatever credentials are on the machine and spend them.
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
            # The error type only; never any part of the key.
            kind = api_error_type(failure)
            return (
                f"the Anthropic API refused the key (HTTP 401{', ' + kind if kind else ''}). "
                f"Check ANTHROPIC_API_KEY in .env"
            )
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

        # Before parsing: an unreadable reply was still billed.
        self.last = spent(message, model)
        return read_response(message)


def provider_for(api_key: str | None, *, detail_model: str | None) -> ClaudeProvider | None:
    """A provider, or None when there is no key. Never guesses at credentials."""
    if not (api_key or "").strip():
        return None
    return ClaudeProvider(api_key.strip(), detail_model=detail_model)
