"""Local vision drafting through Ollama: free, private, and works offline."""

from __future__ import annotations

import base64
import io

import httpx
from PIL import Image, UnidentifiedImageError

from . import base

#: Seconds. Generous: a cold load plus a *thinking* checkpoint (any bare
#: qwen3-vl tag) can take minutes, and a tight timeout loses a good draft.
TIMEOUT = 1200.0

#: How long Ollama keeps the model loaded; its default of five minutes is
#: shorter than the gap between boxes.
KEEP_ALIVE = "30m"

#: Left alone, Ollama sizes the context at 262k tokens (25 GB for a 4b model);
#: 8192 needs under 4 GB and is as fast.
CONTEXT = 8192

#: Photos are kept larger than this; the local models were measured reading
#: small text well at 2048 on the long edge, within CONTEXT.
LOCAL_MAX_EDGE = 2048


def for_local(data: bytes) -> bytes:
    """The photo at the local model's size. Shrinks; never grows or re-encodes."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as bad:
        raise base.DraftUnreadable("that photo could not be decoded") from bad
    if max(image.size) <= LOCAL_MAX_EDGE:
        return data
    shrunk = image.convert("RGB")
    shrunk.thumbnail((LOCAL_MAX_EDGE, LOCAL_MAX_EDGE), Image.LANCZOS)
    buffer = io.BytesIO()
    shrunk.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def build_request(model: str, images: list[bytes]) -> dict:
    """The /api/chat payload, with `format` constraining the reply to base.SCHEMA."""
    return {
        "model": model,
        "stream": False,
        "format": base.SCHEMA,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.2, "num_ctx": CONTEXT},
        "messages": [
            {
                "role": "system",
                "content": base.SYSTEM,
            },
            {
                "role": "user",
                "content": base.INSTRUCTION,
                "images": [base64.b64encode(image).decode() for image in images],
            },
        ],
    }


def read_response(payload: dict) -> base.BoxDraft:
    message = (payload or {}).get("message", {})
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return base.parse(content)

    # With `format` set and thinking switched off on a thinking checkpoint,
    # Ollama 0.34 puts the JSON in `thinking` and leaves `content` empty.
    thinking = message.get("thinking")
    if isinstance(thinking, str) and thinking.strip():
        return base.parse(thinking)

    raise base.DraftUnreadable(f"unexpected reply from ollama: {str(payload)[:200]}")


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, timeout: float = TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def explain(self, status: int | None, model: str) -> str:
        """A failure message that tells a missing model from a missing server."""
        if status == 404:
            return (
                f"the model {model} is not installed on this machine. "
                f"Install it with:  ollama pull {model}"
            )
        if status is None:
            return (
                f"nothing answered at {self.url}. Check ollama is running "
                f"(`ollama serve`, or open the Ollama app)."
            )
        return f"ollama returned HTTP {status} for model {model}"

    def draft(self, images: list[bytes], *, model: str) -> base.BoxDraft:
        try:
            response = httpx.post(
                f"{self.url}/api/chat",
                json=build_request(model, [for_local(image) for image in images]),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise base.DraftUnreadable(self.explain(exc.response.status_code, model)) from exc
        except httpx.HTTPError as exc:
            raise base.DraftUnreadable(self.explain(None, model)) from exc
        return read_response(response.json())
