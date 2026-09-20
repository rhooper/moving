"""Asking a small local model to *phrase* a summary of a record's contents.

`summarise.py` assembles a summary; this rephrases one. The difference is what
the owner asked for: not "stock pot, 3 baking pans, stand mixer, colander,
2 mixing bowls, 6 tea towels" but "Kitchen gear - a stock pot, a stand mixer
and baking pans" -- what kind of things are in there, then a few examples. On
tape read from across a room, the second is the useful one; the full list is
one scan away in the app, which is the same trade the printed label already
makes by not itemising.

Two rules shape everything below.

**Only the button phrases.** "From contents" is a person waiting on a reply, so
it can afford a model. `analysis.refresh_summary` -- the background worker that
fills a summary after a photo -- stays on plain assembly: it already waits on a
vision call, and a second round-trip would make every photo slower for a line
nobody is watching.

**Falling back is the normal case, not the error case.** Ollama down, the model
not pulled, a slow answer, a reply with no JSON in it: every one of those hands
back `summarise.from_items` and says so. This tool is used in a half-packed
house; offline has to keep working, and that was a deliberate project choice
long before this module existed.
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

PROMPT_VERSION = "2026-09-20.1"

#: How long Ollama keeps the model in memory after a request, sent on the warm
#: ping and on every real request. The same 30 minutes the vision path uses,
#: for the same reason: boxes in a packing session are further apart than
#: Ollama's own five-minute default.
KEEP_ALIVE = "30m"
KEEP_ALIVE_SECONDS = 30 * 60

#: How often the warmer pings. Derived from KEEP_ALIVE rather than typed
#: separately, so a refresh can never end up slower than the idle timeout it
#: exists to beat. A sixth of it rather than most of it, because the idle
#: timeout is not the only thing that unloads a model: Ollama holds three at
#: once by default (OLLAMA_MAX_LOADED_MODELS), and this app already wants
#: exactly three -- the summary model and the two vision ones. Anything else
#: on the machine evicts one of them, and if it is this one the next press of
#: the button falls back. Five minutes is how long that lasts. The ping costs
#: nothing when the model is already there: measured at 10-40 ms.
REFRESH = KEEP_ALIVE_SECONDS / 6

#: Retried this often while Ollama is unreachable: often enough to pick up a
#: machine that woke before Ollama did, rarely enough to keep quiet about it.
RETRY = 60.0

#: Left alone, Ollama sizes the context at 262,144 tokens. This prompt is a
#: short list; 8192 is generous for it. It must match the warm ping's exactly
#: -- Ollama keys a loaded model by its runner options, so asking for a
#: different context unloads and reloads the model, which is the cold start
#: the warmer exists to prevent.
CONTEXT = 8192

#: A button someone is waiting on, not a background job -- so this is nothing
#: like the vision path's twenty minutes. Measured on this machine with the
#: model kept warm: 0.27 s median, 0.6 s worst over 21 runs. Five seconds is
#: ten times that, under the point where a button reads as broken, and short
#: enough that a press landing while the photo worker holds Ollama falls back
#: to the assembled line instead of hanging.
TIMEOUT = 5.0

#: How many distinct things it takes before a model can say anything the
#: assembler cannot. Below this there is nothing to generalise from -- and,
#: measured, it is where the models misbehave: given one or two lines every
#: candidate padded its answer out of the prompt's own worked example
#: ("Kitchen essentials - a kettle, clamps and a tin of screws"). "kettle,
#: toaster" is already the best line those contents have.
ENOUGH = 3


class Unusable(ValueError):
    """The model's reply contained no usable summary."""


class Phraser(Protocol):
    name: str

    def phrase(self, contents: list[dict[str, Any]], *, model: str) -> str: ...


# --- the prompt -------------------------------------------------------------

SYSTEM = (
    "You write the one line that is printed on a moving box's label. Somebody "
    "reads it from across a room to decide whether to open the box. Say what "
    "kind of things are inside, then name a few of them as examples. Use only "
    "what you are given; never invent contents."
)

INSTRUCTION = (
    "Write the label line for a container holding:\n\n{contents}\n\n"
    'Return a JSON object and nothing else: {{"summary": "<the line>"}}\n\n'
    "Rules:\n"
    "- Begin with a few words naming the kind of things these are.\n"
    "- Then name two or three of them, taken word for word from the list, as examples.\n"
    "- Every thing you name must appear in the list above. Do not add anything else, "
    "and do not guess what is inside something the list does not describe.\n"
    "- One line, under 200 characters, no full stop at the end.\n\n"
    "For example, for a list of 1 circular saw, 4 clamps, 1 tin of screws and "
    "2 spirit levels:\n"
    '  {{"summary": "Garage tools - a circular saw, clamps and a tin of screws"}}'
)

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def as_lines(contents: list[dict[str, Any]]) -> str:
    """The contents as the prompt shows them: one thing a line, counted.

    Pluralised the same way the assembled summary is, so the model is reading
    the same English a person would have read on the label.
    """
    lines = []
    for thing in contents:
        name = str(thing.get("name") or "").strip()
        if not name:
            continue
        qty = max(1, int(thing.get("qty") or 1))
        lines.append(f"- {qty} {summarise.plural(name, qty)}" if qty > 1 else f"- {name}")
    return "\n".join(lines)


def build_request(model: str, contents: list[dict[str, Any]]) -> dict:
    """The /api/chat payload. Text only -- there is nothing to look at."""
    return {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.2, "num_ctx": CONTEXT},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": INSTRUCTION.format(contents=as_lines(contents))},
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
    """The first JSON object in a reply that may be wrapped in prose or fences.

    Same forgiveness as `vision.base`, for the same reason: small local models
    fence their JSON and prepend "Sure! Here is" however firmly told not to.
    """
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
    """The summary in a reply, or raise Unusable.

    Forgiving about the wrapper, strict about the outcome: an empty line is not
    an answer, and handing one back would read on the label as "nothing in
    here" rather than "the model had nothing to say".
    """
    message = (payload or {}).get("message", {})
    text = (message.get("content") or "").strip()
    if not text:
        # Ollama 0.34, with `format` set, puts a thinking checkpoint's JSON in
        # `thinking` and leaves `content` empty. The vision path lost 42 of 42
        # replies that way before this fallback existed.
        text = (message.get("thinking") or "").strip()
    if not text:
        raise Unusable(f"unexpected reply from ollama: {str(payload)[:200]}")

    summary = _tidy(str(_find_json(text).get("summary") or ""))
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
            # Refused, timed out, no DNS: nothing usable answered in time.
            raise Unusable(f"nothing usable answered at {self.url}: {exc}") from exc
        return read_response(response.json())


class StubPhraser:
    """A deterministic phraser, for checking the UI without a model.

    Selected by ``MOVING_VISION_PROVIDER=stub``, the same switch the stub
    vision provider uses -- the browser checks set it once and neither path
    reaches Ollama. It answers in the shape a real one does, from the contents
    it was given, so a check can assert on it.
    """

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
        # Generous: this is a background thread nobody is waiting on, and a
        # cold load of a 7b model is seconds.
        timeout=120.0,
    )
    response.raise_for_status()
    return True


class Warmer(threading.Thread):
    """Keeps the summary model resident, so the first press is not a cold start.

    Shaped like `printer.AutoOffWatcher` -- a daemon thread started by the
    app's lifespan handler -- but it does not stop after one success: the
    point is that the model is still there hours later, when the next box is
    packed. Measured on this machine, that is the difference between 0.3 s and
    the several seconds a load takes, and the whole reason the button is worth
    putting a model behind at all.

    It never fails startup and never dies: Ollama may be down, may come back,
    or the model may simply not be pulled. All of that is logged and retried.
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
        if not self.config.phrase_summaries or self.config.vision_provider == "stub":
            # Nothing to keep warm: either the button assembles its line, or a
            # stub answers it. Checked here rather than at the call site so a
            # test of the thread covers the decision.
            return
        while not self._stop.is_set():
            warmed = False
            try:
                warmed = bool(self._ping(self.config))
            except Exception as failure:  # noqa: BLE001 - the warmer must outlive anything
                # Debug, not warning: a machine that wakes before Ollama does
                # would otherwise fill the log with something nobody can act on.
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

    The assembled line is built first and always: it is both the answer when
    there is no model to ask and the thing handed back when asking fails. The
    caller reports the source so the page can say which it got -- and so a test
    can tell a fallback from a lucky guess.
    """
    assembled = summarise.from_items(contents)
    if phraser is None or not assembled or len(contents) < ENOUGH:
        return assembled, "assembled"

    try:
        return phraser.phrase(contents, model=model), "model"
    except Exception as failure:  # noqa: BLE001 - a summary is never worth a 500
        log.info("phrasing fell back to the assembled summary: %s", failure)
        return assembled, "assembled"
