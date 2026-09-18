"""Local vision drafting through Ollama.

The default. Free per photo, private, and works with no internet -- which
matters when the machine is in a half-packed house.
"""

from __future__ import annotations

import base64

import httpx

from . import base

#: 20 minutes. The models in use answer in ten seconds or so, but a *thinking*
#: checkpoint (any bare qwen3-vl tag) has been seen to reason for over two
#: minutes about a photo of two closed boxes, and a cold model has to load
#: first. The cost of a generous timeout is a request that hangs; the cost of
#: a tight one is losing a draft that would have succeeded.
TIMEOUT = 1200.0

#: How long Ollama keeps the model in memory after a request. Its default is
#: five minutes, and boxes in a packing session are often further apart than
#: that -- so without this, most photos pay for a model load. Sent with the
#: request so nobody has to edit a Homebrew plist.
KEEP_ALIVE = "30m"

#: Left alone, Ollama sizes the context at 262,144 tokens and a 4b model takes
#: 25 GB of memory. One photo and a short prompt need a fraction of that; at
#: 8192 the same model takes under 4 GB and is exactly as fast.
CONTEXT = 8192


def build_request(model: str, images: list[bytes]) -> dict:
    """The /api/chat payload.

    `format` carries the JSON schema. Without it a local model returns prose
    around its JSON often enough to matter -- base.parse copes, but
    constraining the output is cheaper than mining it.
    """
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

    # Ollama 0.34, with `format` set and thinking switched off on a thinking
    # checkpoint, puts the JSON in `thinking` and leaves `content` empty. Every
    # one of 42 such replies in the benchmark had a usable draft in it. If it
    # is *only* thinking, base.parse finds no JSON and raises, as it should.
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
        """A failure message that names the actual problem.

        These are read on a phone, mid-pack. "Could not reach ollama" when
        ollama is fine and the model simply is not pulled sends you debugging
        the wrong thing entirely.
        """
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
                json=build_request(model, images),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise base.DraftUnreadable(self.explain(exc.response.status_code, model)) from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, timeout: nothing answered.
            raise base.DraftUnreadable(self.explain(None, model)) from exc
        return read_response(response.json())
