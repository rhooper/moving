"""Asking a small local model to *phrase* a summary of a record's contents.

`summarise.py` assembles a flat list ("stock pot, 3 baking pans, stand
mixer..."); this turns it into the kind of things inside and a few examples
("Kitchen gear - a stock pot, a stand mixer and baking pans").

Only the "From contents" button phrases; the background photo worker assembles.
Falling back to the assembled line is the normal case, not an error: no model,
Ollama down, a slow or unreadable answer all return `summarise.from_items`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Protocol

import httpx

from . import summarise
from .config import Config

log = logging.getLogger(__name__)

PROMPT_VERSION = "2026-09-22.1"

#: How long Ollama keeps the model loaded; its default of five minutes is
#: shorter than the gap between boxes.
KEEP_ALIVE = "30m"
KEEP_ALIVE_SECONDS = 30 * 60

#: How often the warmer pings: well inside KEEP_ALIVE, because Ollama holds only
#: three models by default (OLLAMA_MAX_LOADED_MODELS), this app wants exactly
#: three, and anything else on the machine can evict this one early.
REFRESH = KEEP_ALIVE_SECONDS / 6

#: Seconds between pings while Ollama is unreachable.
RETRY = 60.0

#: Must match between the warm ping and the real request: Ollama keys a loaded
#: model by its runner options, so a different context reloads the model.
CONTEXT = 8192

#: Seconds. A person is waiting on the button, and a press that lands while
#: the photo worker holds Ollama should fall back rather than hang.
TIMEOUT = 5.0

#: Fewer distinct things than this are not sent to a model: there is nothing
#: to generalise from, and models pad the answer from the prompt's example.
ENOUGH = 4


class Unusable(ValueError):
    """The model's reply contained no usable summary."""


class Phraser(Protocol):
    name: str

    def phrase(self, contents: list[dict[str, Any]], *, model: str) -> str: ...


# --- the prompt -------------------------------------------------------------

SYSTEM = (
    "You write the one line that is printed on a moving box's label. Somebody "
    "reads it from across a room to decide whether to open the box, so what "
    "matters is the kinds of things inside, far more than which exact items. "
    "Use only what you are given; never invent contents."
)

INSTRUCTION = (
    "Write the label line for a container holding:\n\n{contents}\n\n"
    'Return a JSON object and nothing else: {{"summary": "<the line>"}}\n\n'
    "Rules:\n"
    "- Say what KINDS of things are in there: two or three groups that between them\n"
    "  cover most of the list.\n"
    "- Then, at the end, add a few specific examples in support.\n"
    "- Describe only what is in the list. Do not name a kind of thing the list does\n"
    "  not support, and do not guess what is inside something it does not describe.\n"
    "- One line, about 90 characters, no full stop at the end.\n\n"
    "For a list of 1 circular saw, 4 clamps, 1 tin of screws, 2 spirit levels, "
    "a box of drill bits and 3 sanding blocks:\n"
    '  {{"summary": "Garage tools and fixings: power tools, hand tools and abrasives, '
    'with a tin of screws"}}'
)

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def as_lines(contents: list[dict[str, Any]]) -> str:
    """The contents as the prompt shows them: one thing a line, counted and
    pluralised as the assembled summary would be.
    """
    lines = []
    for thing in contents:
        name = str(thing.get("name") or "").strip()
        if not name:
            continue
        qty = max(1, int(thing.get("qty") or 1))
        lines.append(f"- {qty} {summarise.plural(name, qty)}" if qty > 1 else f"- {name}")
    return "\n".join(lines)


def prompt(contents: list[dict[str, Any]]) -> tuple[str, str]:
    """The system prompt and the user's message, for any provider to send.

    A plug-in sends these with `SCHEMA` as the reply's shape, at temperature 0
    (see `build_request`), and hands what comes back to `finish`.
    """
    return SYSTEM, INSTRUCTION.format(contents=as_lines(contents))


def build_request(model: str, contents: list[dict[str, Any]]) -> dict:
    """The /api/chat payload. Text only -- there is nothing to look at."""
    return {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "keep_alive": KEEP_ALIVE,
        # Greedy. Measured over seven real records, three runs each: this took
        # repeated answers from identical on 5 of 7 records to 7 of 7. Nothing
        # here wants invention, and pressing the button twice should not
        # rewrite the label.
        "options": {"temperature": 0, "num_ctx": CONTEXT},
        "messages": [
            {"role": role, "content": text}
            for role, text in zip(("system", "user"), prompt(contents), strict=True)
        ],
    }


def warm_request(model: str) -> dict:
    """The /api/generate payload that loads a model and generates nothing.

    Ollama answers this with ``done_reason: "load"``. Its options must match
    `build_request`'s or the real request reloads the model it just warmed.
    """
    return {"model": model, "keep_alive": KEEP_ALIVE, "options": {"num_ctx": CONTEXT}}


# --- reading the reply ------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _find_json(text: str) -> dict[str, Any]:
    """The first JSON object in a reply that may be wrapped in prose or fences."""
    for candidate in [*(m.group(1) for m in _FENCE.finditer(text)), text]:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise Unusable("no JSON object found in the model's reply")


def _tidy(text: str) -> str:
    """One line, label length. The prompt asks for both; this enforces them."""
    line = " ".join(text.split())
    if len(line) <= summarise.MAX_LENGTH:
        return line
    cut = line[: summarise.MAX_LENGTH].rsplit(" ", 1)[0] or line[: summarise.MAX_LENGTH - 1]
    return cut.rstrip(" ,;-") + "…"


def read_response(payload: dict) -> str:
    """The summary in a reply, or raise Unusable. An empty line is not an answer."""
    message = (payload or {}).get("message", {})
    text = (message.get("content") or "").strip()
    if not text:
        # With `format` set, Ollama 0.34 puts a thinking checkpoint's JSON in
        # `thinking` and leaves `content` empty.
        text = (message.get("thinking") or "").strip()
    if not text:
        raise Unusable(f"unexpected reply from ollama: {str(payload)[:200]}")
    return finish(text)


def finish(text: str) -> str:
    """The label line in a model's reply text, tidied, or raise Unusable.

    Forgiving of the reply (the JSON may be fenced or wrapped in prose), strict
    about the outcome: an empty line is not an answer.
    """
    summary = _tidy(str(_find_json(text or "").get("summary") or ""))
    if not summary:
        raise Unusable("the reply had no summary in it")
    return summary


# --- providers --------------------------------------------------------------


class OllamaPhraser:
    name = "ollama"

    def __init__(self, url: str, timeout: float = TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def phrase(self, contents: list[dict[str, Any]], *, model: str) -> str:
        try:
            response = httpx.post(
                f"{self.url}/api/chat",
                json=build_request(model, contents),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise Unusable(f"ollama returned HTTP {exc.response.status_code} for {model}") from exc
        except httpx.HTTPError as exc:
            raise Unusable(f"nothing usable answered at {self.url}: {exc}") from exc
        return read_response(response.json())


class StubPhraser:
    """A deterministic phraser for checking the UI, selected by ``MOVING_VISION_PROVIDER=stub``."""

    name = "stub"

    def phrase(self, contents: list[dict[str, Any]], *, model: str) -> str:
        names = [str(t.get("name") or "").strip() for t in contents]
        examples = ", ".join(n for n in names if n)[: summarise.MAX_LENGTH - 20]
        return _tidy(f"Assorted things - {examples}")


def warm(config: Config) -> bool:
    """Load the summary model into memory now, generating nothing."""
    response = httpx.post(
        f"{config.ollama_url.rstrip('/')}/api/generate",
        json=warm_request(config.summary_model),
        # Nobody is waiting on this, and a cold load takes seconds.
        timeout=120.0,
    )
    response.raise_for_status()
    return True


class Warmer(threading.Thread):
    """Keeps the summary model resident, so a press is never a cold start.

    Runs until stopped, and never dies: Ollama being down or the model not
    pulled is logged and retried.
    """

    def __init__(
        self,
        config: Config,
        *,
        ping=None,
        interval: float = REFRESH,
        retry: float = RETRY,
    ) -> None:
        super().__init__(name="summary-warmer", daemon=True)
        self.config = config
        self._ping = ping or warm
        self.interval = interval
        self.retry = retry
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if (
            not self.config.phrase_summaries
            or self.config.vision_provider == "stub"
            # A plug-in's model is not Ollama's to load.
            or self.config.summary_provider != "ollama"
        ):
            return
        while not self._stop.is_set():
            warmed = False
            try:
                warmed = bool(self._ping(self.config))
            except Exception as failure:  # noqa: BLE001 - the warmer must outlive anything
                # Debug: Ollama starting after the app is routine, not actionable.
                log.debug("could not warm %s: %s", self.config.summary_model, failure)
            if self._stop.wait(self.interval if warmed else self.retry):
                return


# --- what the endpoint actually calls ---------------------------------------


def summary_for(
    contents: list[dict[str, Any]],
    phraser: Phraser | None,
    *,
    model: str,
) -> tuple[str, str]:
    """The suggested summary and where it came from: "model" or "assembled".

    The assembled line is always built first; it is the answer whenever the
    model is not asked or fails.
    """
    assembled = summarise.from_items(contents)
    if phraser is None or not assembled or len(contents) < ENOUGH:
        return assembled, "assembled"

    try:
        return phraser.phrase(contents, model=model), "model"
    except Exception as failure:  # noqa: BLE001 - a summary is never worth a 500
        log.info("phrasing fell back to the assembled summary: %s", failure)
        return assembled, "assembled"
