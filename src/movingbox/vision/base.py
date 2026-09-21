"""The draft a vision model produces, and how a model's reply becomes one.

Parsing is forgiving of the reply -- JSON is mined out of fences and prose, and
every field is coerced -- but strict about the outcome: a reply with no usable
draft raises rather than reading as "the model saw an empty box".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Long enough to be useful on a label, short enough to fit one.
SUMMARY_MAX = 240
#: A box with more than this many distinct things in it is a box of "misc".
ITEMS_MAX = 40
QTY_MAX = 999

PROMPT_VERSION = "2026-09-17.1"


class DraftUnreadable(ValueError):
    """The model's reply contained no usable draft."""


@dataclass(frozen=True)
class DraftItem:
    name: str
    qty: int = 1
    category: str | None = None


@dataclass(frozen=True)
class BoxDraft:
    summary: str
    items: list[DraftItem] = field(default_factory=list)
    fragile: bool = False
    confidence: str | None = None


@dataclass(frozen=True)
class Reading:
    """Who actually answered, and what it cost (zero for a local model).

    With a fallback, this can differ from the model a job named when queued.
    """

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class VisionProvider(Protocol):
    name: str

    #: What the last `draft` cost. Optional: read with
    #: `getattr(provider, "last", None)`; absent means it cost nothing.
    last: Reading | None

    def draft(self, images: list[bytes], *, model: str) -> BoxDraft: ...


# --- coercion -------------------------------------------------------------


def _qty(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    if number < 1 or number > QTY_MAX:
        return 1
    return number


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _items(raw: Any) -> list[DraftItem]:
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"), 120)
        if not name:
            continue  # an unnamed item is not an inventory record
        items.append(
            DraftItem(
                name=name,
                qty=_qty(entry.get("qty", 1)),
                category=_text(entry.get("category"), 60) or None,
            )
        )
        if len(items) >= ITEMS_MAX:
            break
    return items


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _find_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a reply that may be wrapped in prose."""
    candidates = [match.group(1) for match in _FENCE.finditer(text)]
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        # Narrow to the outermost braces so leading and trailing chatter goes.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise DraftUnreadable("no JSON object found in the model's reply")


def parse(text: str) -> BoxDraft:
    """Turn a model reply into a draft, or raise DraftUnreadable."""
    payload = _find_json(text or "")
    items = _items(payload.get("items"))
    summary = _text(payload.get("summary"), SUMMARY_MAX)

    if not summary and not items:
        raise DraftUnreadable("the reply had neither a summary nor any items")

    return BoxDraft(
        summary=summary,
        items=items,
        fragile=bool(payload.get("fragile")),
        confidence=_text(payload.get("confidence"), 20) or None,
    )


# --- prompt ---------------------------------------------------------------

SYSTEM = (
    "You are cataloguing the contents of a moving box from a photograph taken "
    "just before it was taped shut. List only what you can actually see. Do not "
    "guess at things that might be underneath. Use the everyday name for each "
    "object, as the person packing would say it."
)

INSTRUCTION = (
    "Return a JSON object with these keys and nothing else:\n"
    '  "summary": one short line naming the main contents, under 200 characters,\n'
    "             suitable for printing on a label\n"
    '  "items":   a list of {"name", "qty", "category"} objects\n'
    '  "fragile": true if anything visible is breakable (glass, ceramic, screens)\n'
    '  "confidence": "high", "medium" or "low"\n'
)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "qty": {"type": "integer"},
                    "category": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        "fragile": {"type": "boolean"},
        "confidence": {"type": "string"},
    },
    "required": ["summary", "items"],
}
