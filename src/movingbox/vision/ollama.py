"""Local vision drafting through Ollama.

The default. Free per photo, private, and works with no internet -- which
matters when the machine is in a half-packed house.
"""

from __future__ import annotations

import base64

import httpx

from . import base

#: 20 minutes. A warm 30B MoE on an M2 Ultra answers in about six seconds, but
#: a cold model has to load ~20 GB from disk, and a larger model pulled in
#: later could take far longer still. The cost of a generous timeout is a
#: request that hangs; the cost of a tight one is losing a draft that would
#: have succeeded.
TIMEOUT = 1200.0


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
        "options": {"temperature": 0.2},
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
    content = (payload or {}).get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise base.DraftUnreadable(f"unexpected reply from ollama: {str(payload)[:200]}")
    return base.parse(content)


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
