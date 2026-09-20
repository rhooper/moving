"""Turn an itemised list into the one line that goes on the label.

Deliberately not a model. The items have already been typed, or reviewed after
a photo draft, so summarising them is a formatting job: it should be instant,
give the same answer every time, and work with no network.
"""

from __future__ import annotations

from typing import Any

from . import kinds

#: Matches the label renderer's cap, so the summary never arrives pre-broken.
MAX_LENGTH = 240

#: Words that are already plural, or unchanged in the plural. Short on purpose:
#: a wrong plural on tape is mildly embarrassing, a missing one is invisible.
UNCOUNTED = {
    "scissors",
    "trousers",
    "glasses",
    "clothes",
    "linens",
    "tongs",
    "pliers",
    "shears",
}


def plural(name: str, qty: int) -> str:
    if qty <= 1:
        return name
    lowered = name.lower()
    if lowered in UNCOUNTED or lowered.endswith(("s", "x", "ch", "sh")):
        return name
    return f"{name}s"


def _merge(contents: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Combine repeats of the same thing, keeping first-seen order."""
    order: list[str] = []
    totals: dict[str, int] = {}
    labels: dict[str, str] = {}

    for item in contents:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in totals:
            order.append(key)
            labels[key] = name
            totals[key] = 0
        try:
            totals[key] += max(1, int(item.get("qty") or 1))
        except (TypeError, ValueError):
            totals[key] += 1

    return [(labels[key], totals[key]) for key in order]


def from_items(contents: list[dict[str, Any]], max_length: int = MAX_LENGTH) -> str:
    """A label-ready summary of ``contents``.

    Over-long lists are cut at a whole item and finished with "and N more",
    which tells you something useful; a sentence severed mid-word does not.
    """
    merged = _merge(contents or [])
    if not merged:
        return ""

    parts = [f"{qty} {plural(name, qty)}" if qty > 1 else name for name, qty in merged]

    summary = ", ".join(parts)
    if len(summary) <= max_length:
        return summary

    kept: list[str] = []
    for index, part in enumerate(parts):
        # Reserve room for the "and N more" that will follow if we stop here.
        still_to_come = len(parts) - index - 1
        candidate = ", ".join([*kept, part])
        if len(candidate) + len(f", and {still_to_come} more") > max_length:
            break
        kept.append(part)

    if not kept:  # a single item longer than the whole budget
        return parts[0][: max_length - 1].rstrip(" ,") + "…"

    left = len(parts) - len(kept)
    return ", ".join(kept) + (f", and {left} more" if left else "")


def describe(child: dict[str, Any]) -> str:
    """What one nested record contributes to its container's summary.

    Its own summary when it has one -- a bag that says "winter coats" is far
    more use on the outer label than the word "bag". Otherwise what it *is*,
    in the words the list rows use: "large box", "bag", "loose item".

    The size is spelled in full here where the list abbreviates "extra large"
    to "XL": that abbreviation exists for a narrow cell that cannot wrap, and
    a summary is a sentence.
    """
    summary = str(child.get("content_summary") or "").strip()
    if summary:
        return summary
    kind = kinds.label_for(str(child.get("kind") or kinds.DEFAULT)).lower()
    size = str(child.get("size") or "").strip()
    return f"{size} {kind}" if size else kind


def contents(
    items: list[dict[str, Any]], children: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Everything a record holds -- its items *and* what is nested inside it.

    One function rather than mapping children to item-shaped dicts at each
    call site, because there are three of those (the suggestion endpoint, the
    photo worker, and the model prompt) and they must agree on what a child
    contributes. Going through `_merge` here is also what makes three bags
    read as "3 bags": the counting is already written, it only needs feeding
    with well-formed names.
    """
    both = [*(items or []), *({"name": describe(c), "qty": 1} for c in children or ())]
    return [{"name": name, "qty": qty} for name, qty in _merge(both)]


def from_contents(
    items: list[dict[str, Any]],
    children: list[dict[str, Any]] | None = None,
    max_length: int = MAX_LENGTH,
) -> str:
    """A label-ready summary of everything a record holds."""
    return from_items(contents(items, children), max_length)
