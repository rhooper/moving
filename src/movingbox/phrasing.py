"""Asking a small local model to *phrase* a summary of a record's contents.

`summarise.py` assembles a flat list ("stock pot, 3 baking pans, stand
mixer..."); this turns it into one heading for the whole container and a few
examples ("Kitchen gear - stock pot, stand mixer, baking pans, etc.").

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

PROMPT_VERSION = "2026-10-04.1"

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


#: What the record itself says, beside its contents: ``{"kind": "box", "room":
#: "Office"}``, either key absent or None. The room is the one the label
#: prints (`store.label_rooms`).
Context = dict[str, Any]


class Phraser(Protocol):
    name: str

    def phrase(
        self, contents: list[dict[str, Any]], *, model: str, context: Context | None = None
    ) -> str: ...


# --- the prompt -------------------------------------------------------------

SYSTEM = (
    "You write the one line that is printed on a moving box's label. Somebody "
    "reads it from across a room to decide whether to open the box, so what "
    "matters is the kinds of things inside, far more than which exact items. "
    "Use only what you are given; never invent contents."
)

#: The model answers in fields and `compose` writes the line: "Kitchen gear -
#: stock pot, baking pans, stand mixer, etc.", or "Miscellaneous Office - ..."
#: for a jumble (asked for as "prefer summary descriptions. Eg B-0062 could be
#: Miscellaneous Office - lights, vacuum, leather, mug, figurine, etc.").
#:
#: Measured with qwen2.5:7b over 17 real records, three runs each: identical
#: every run, B-0062 a jumble and the rest named for their one kind. Asked
#: for the line itself instead, it copied whatever heading its examples showed
#: ("Electronic parts" for a box of music gear, "Miscellaneous" for a tub of
#: resistors). Asked to sort the lines into groups, it took 2-5 s and split
#: coherent boxes. Asked to mark each line as fitting, it lost count. This
#: wording is fragile: dropping "Never the room" or the room itself, or
#: rewording the system prompt, each turned B-0062 into "Office supplies" or
#: "Electronics and Accessories". Re-measure with try_summary.py on any change.
INSTRUCTION = (
    "Describe {container}:\n\n{contents}\n\n"
    "Return a JSON object and nothing else, with these fields in this order:\n"
    '- "kind": two or three words naming the one kind of thing that most of the list\n'
    '  is, in your own words ("Kitchen gear", "Winter clothes"). Never the room.\n'
    '- "most_of_the_list": true if that kind honestly covers more than half of the\n'
    "  things listed; false if the list is a jumble of unrelated kinds.\n"
    '- "examples": four to six of the most telling things, as short plain nouns:\n'
    '  no brands, models, colours or counts ("leather", not "6 leather hide pieces").\n'
    "Use only what is in the list. The room is where it is going, not what is in it."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "most_of_the_list": {"type": "boolean"},
        "examples": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind", "most_of_the_list", "examples"],
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


def container(context: Context | None) -> str:
    """How the prompt names the record: "a box going to the Office, holding"."""
    context = context or {}
    kind = str(context.get("kind") or "").strip().lower() or "container"
    room = str(context.get("room") or "").strip()
    article = "an" if kind[0] in "aeiou" else "a"
    return f"{article} {kind} going to the {room}, holding" if room else f"{article} {kind} holding"


def miscellaneous(context: Context | None) -> str:
    """The heading for a container of unrelated things: "Miscellaneous Office"."""
    room = str((context or {}).get("room") or "").strip()
    return f"Miscellaneous {room}" if room else "Miscellaneous"


def prompt(contents: list[dict[str, Any]], context: Context | None = None) -> tuple[str, str]:
    """The system prompt and the user's message, for any provider to send.

    A plug-in sends these with `SCHEMA` as the reply's shape, at temperature 0
    (see `build_request`), and hands what comes back to `finish` with the same
    contents and context.
    """
    return SYSTEM, INSTRUCTION.format(container=container(context), contents=as_lines(contents))


def build_request(
    model: str, contents: list[dict[str, Any]], context: Context | None = None
) -> dict:
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
            for role, text in zip(("system", "user"), prompt(contents, context), strict=True)
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


def read_response(
    payload: dict, contents: list[dict[str, Any]] = (), context: Context | None = None
) -> str:
    """The summary in a reply, or raise Unusable. An empty line is not an answer."""
    message = (payload or {}).get("message", {})
    text = (message.get("content") or "").strip()
    if not text:
        # With `format` set, Ollama 0.34 puts a thinking checkpoint's JSON in
        # `thinking` and leaves `content` empty.
        text = (message.get("thinking") or "").strip()
    if not text:
        raise Unusable(f"unexpected reply from ollama: {str(payload)[:200]}")
    return finish(text, contents, context)


def finish(text: str, contents: list[dict[str, Any]] = (), context: Context | None = None) -> str:
    """The label line in a model's reply text, or raise Unusable.

    Forgiving of the reply (the JSON may be fenced or wrapped in prose), strict
    about the outcome: no examples is not an answer. A reply that is already a
    line (``{"summary": ...}``, the shape before 2026-10-04) is used as it is.
    """
    reply = _find_json(text or "")
    if isinstance(reply.get("summary"), str) and "examples" not in reply:
        summary = _tidy(reply["summary"])
        if not summary:
            raise Unusable("the reply had no summary in it")
        return summary
    examples = reply.get("examples")
    if not isinstance(examples, list):
        raise Unusable("the reply had no examples in it")
    return compose(
        str(reply.get("kind") or ""),
        reply.get("most_of_the_list") is True,
        [str(example) for example in examples],
        contents,
        context,
    )


def compose(
    kind: str,
    most: bool,
    examples: list[str],
    contents: list[dict[str, Any]] = (),
    context: Context | None = None,
) -> str:
    """ "Kitchen gear - stock pot, stand mixer, baking pans, etc.", fitted to the tape.

    The heading is the model's kind when it covers most of the list, else
    "Miscellaneous <room>". Examples go in while they fit `summarise.MAX_LENGTH`
    with room for ", etc.", which is added when the list holds more than was
    named -- so a long line loses an example, never ends mid-word.
    """
    heading = " ".join(kind.split())
    if not most or not heading:
        heading = miscellaneous(context)
    heading = heading[0].upper() + heading[1:]

    named: list[str] = []
    seen: set[str] = set()
    for example in examples:
        word = " ".join(example.split()).strip(" ,;.-")
        if word and summarise.name_key(word) not in seen:
            seen.add(summarise.name_key(word))
            named.append(word)
    if not named:
        raise Unusable("the reply had no examples in it")

    distinct = len({summarise.name_key(str(t.get("name") or "")) for t in contents or ()})
    kept: list[str] = []
    for word in named[:6]:
        line = f"{heading} - {', '.join([*kept, word])}, etc."
        if len(line) > summarise.MAX_LENGTH:
            break
        kept.append(word)
    if not kept:
        return _tidy(f"{heading} - {named[0]}")
    more = len(kept) < max(distinct, len(named))
    return f"{heading} - {', '.join(kept)}" + (", etc." if more else "")


# --- providers --------------------------------------------------------------


class OllamaPhraser:
    name = "ollama"

    def __init__(self, url: str, timeout: float = TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def phrase(
        self, contents: list[dict[str, Any]], *, model: str, context: Context | None = None
    ) -> str:
        try:
            response = httpx.post(
                f"{self.url}/api/chat",
                json=build_request(model, contents, context),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise Unusable(f"ollama returned HTTP {exc.response.status_code} for {model}") from exc
        except httpx.HTTPError as exc:
            raise Unusable(f"nothing usable answered at {self.url}: {exc}") from exc
        return read_response(response.json(), contents, context)


class StubPhraser:
    """A deterministic phraser for checking the UI, selected by ``MOVING_VISION_PROVIDER=stub``."""

    name = "stub"

    def phrase(
        self, contents: list[dict[str, Any]], *, model: str, context: Context | None = None
    ) -> str:
        names = [str(t.get("name") or "") for t in contents]
        return compose("", False, names, contents, context)


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
    context: Context | None = None,
) -> tuple[str, str]:
    """The suggested summary and where it came from: "model" or "assembled".

    The assembled line is always built first; it is the answer whenever the
    model is not asked or fails. `context` is the record's kind and room; a
    plug-in written before it existed takes no `context`, and falls back here.
    """
    assembled = summarise.from_items(contents)
    if phraser is None or not assembled or len(contents) < ENOUGH:
        return assembled, "assembled"

    try:
        return phraser.phrase(contents, model=model, context=context), "model"
    except Exception as failure:  # noqa: BLE001 - a summary is never worth a 500
        log.info("phrasing fell back to the assembled summary: %s", failure)
        return assembled, "assembled"
