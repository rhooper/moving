"""Turn an itemised list into the one line that goes on the label.

Plain assembly, no model: instant, deterministic and offline.
"""

from __future__ import annotations

from typing import Any

from . import kinds

#: Characters, and it is the *tape* that sets it. A landscape label wraps the
#: summary and keeps only the lines that fit above the handling chips: three
#: with a room band, two if the label also carries a chip. Measured on real
#: text at the label's own font and width, three lines hold about 100
#: characters. Past that the renderer cuts mid-sentence with an ellipsis,
#: where cutting here ends on "and N more", which at least says how much is
#: missing. (vision.base.SUMMARY_MAX is still 240: a photo's own sentence is
#: capped where the model is read, not where a label is printed.)
MAX_LENGTH = 100

#: Words already plural or unchanged in the plural.
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


def name_key(name: str) -> str:
    """What makes two item names the same thing: case, spacing, a trailing plural.

    Deliberately crude: a wrong merge loses an item, a missed one only leaves a
    near-duplicate to delete. One rule, shared with what a photo's reading
    merges into a record (`analysis`) -- and it lives here because that module
    already imports this one and the dependency may not run both ways.
    """
    key = " ".join(name.lower().split())
    for ending in ("es", "s"):
        stem = key[: -len(ending)]
        if key.endswith(ending) and len(stem) > 2 and not stem.endswith("s"):
            return stem
    return key


def grouped_items(nodes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The items of a record and of everything nested inside it, by record.

    What the read-only view lists when its contents line is expanded: "include
    items from subitems with a small item-id heading, putting them after
    box-level items. sort and deduplicate items when showing them."

    Merged and sorted *within* a record and never across them: the same name in
    a crate and in a tub inside it is two things in two places, which is what
    the heading exists to say. The order is `store.subtree`'s own -- the record
    itself, then what is directly inside it, then deeper. A record that lists
    nothing is left out rather than being an empty heading; this is a list of
    items, so a record described only by a summary contributes none.
    """
    groups = []
    for node in nodes or ():
        merged: dict[str, dict[str, Any]] = {}
        for item in node.get("items") or ():
            name = " ".join(str(item.get("name") or "").split())
            if not name:
                continue
            try:
                qty = max(1, int(item.get("qty") or 1))
            except (TypeError, ValueError):
                qty = 1
            found = merged.setdefault(name_key(name), {"name": name, "qty": 0})
            found["qty"] += qty
        if merged:
            groups.append(
                {
                    "code": node.get("code"),
                    "items": sorted(merged.values(), key=lambda item: item["name"].lower()),
                }
            )
    return groups


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
    """A label-ready summary of ``contents``; too long, it ends "..., and N more"."""
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
    """What one nested record contributes: its summary, else what it is ("large box").

    The size is spelled in full, unlike the list's "XL".
    """
    summary = str(child.get("content_summary") or "").strip()
    if summary:
        return summary
    kind = kinds.label_for(str(child.get("kind") or kinds.DEFAULT)).lower()
    size = str(child.get("size") or "").strip()
    return f"{size} {kind}" if size else kind


#: How many distinct things a summary is built from, nearest first, however
#: deep the tree goes.
MAX_THINGS = 40


def node_contents(node: dict[str, Any], *, holds: bool = False) -> list[dict[str, Any]]:
    """What one nested record contributes: the most concrete text it has.

    In order: a summary a person typed; its items; an autogenerated summary;
    what it is ("large box") -- unless it `holds` something, which then speaks
    for it.

    Items go ahead of an autogenerated summary because that summary usually
    rephrases those same items; taking it would blur the line further at each
    level of nesting.
    """
    summary = str(node.get("content_summary") or "").strip()
    items = node.get("items") or []

    if summary and node.get("summary_source") != "auto":
        return [{"name": summary, "qty": 1}]
    if items:
        return [dict(item) for item in items]
    if summary:
        return [{"name": summary, "qty": 1}]
    return [] if holds else [{"name": describe(node), "qty": 1}]


def contents(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Everything a record holds, from `store.subtree`'s nodes, merged and nearest first.

    The record's own summary is not read: it is the line about to be written,
    and would otherwise describe itself more vaguely with every press. Repeats
    merge across levels, so three empty bags read as "3 bags".
    """
    # Records with something inside them, which speaks for them instead.
    holding = {n.get("parent_id") for n in nodes or () if n.get("parent_id") is not None}

    gathered: list[dict[str, Any]] = []
    for node in nodes or ():
        if node.get("depth", 0) == 0:
            gathered.extend(dict(item) for item in node.get("items") or [])
        else:
            gathered.extend(node_contents(node, holds=node.get("id") in holding))

    return [{"name": name, "qty": qty} for name, qty in _merge(gathered)[:MAX_THINGS]]


def from_subtree(nodes: list[dict[str, Any]], max_length: int = MAX_LENGTH) -> str:
    """A label-ready summary of a record and everything nested inside it."""
    return from_items(contents(nodes), max_length)
