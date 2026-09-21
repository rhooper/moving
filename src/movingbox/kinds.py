"""What a labelled record is: a container with contents, or a single thing.

Every record, whatever its kind, is a row in `boxes`, so codes, scanning,
search and export need no special cases.
"""

from __future__ import annotations

#: Ordered for the picker. `copies` is the default number of labels: two for
#: what gets stacked, one for the rest; Settings can override each
#: (prefs.label_copies).
KINDS: dict[str, dict[str, object]] = {
    "box": {"label": "Box", "contents": True, "copies": 2},
    "parts": {"label": "Parts", "contents": True, "copies": 1},
    "tub": {"label": "Tub", "contents": True, "copies": 2},
    "crate": {"label": "Crate", "contents": True, "copies": 2},
    "bag": {"label": "Bag", "contents": True, "copies": 1},
    "item": {"label": "Loose item", "contents": False, "copies": 1},
    "furniture": {"label": "Furniture", "contents": False, "copies": 1},
}

DEFAULT = "box"

#: How big a container is. Optional, and only for something that holds contents.
SIZES = ("small", "medium", "large", "extra large")


def valid(kind: str) -> bool:
    return kind in KINDS


def check(kind: str) -> str:
    if not valid(kind):
        raise ValueError(f"{kind!r} is not a kind; expected one of {', '.join(KINDS)}")
    return kind


def holds_contents(kind: str) -> bool:
    """Whether a list of contents makes sense for this kind.

    A single thing is described by what it *is*: its label shows a title, and
    the print gate asks for a name rather than contents.
    """
    return bool(KINDS.get(kind, KINDS[DEFAULT])["contents"])


def default_copies(kind: str) -> int:
    return int(KINDS.get(kind, KINDS[DEFAULT])["copies"])


def sizes_for(kind: str) -> tuple[str, ...]:
    """The sizes this kind can be: all of them for a container, none for a thing."""
    return SIZES if holds_contents(kind) else ()


def check_size(size: str | None, kind: str) -> str | None:
    if size is None:
        return None
    if size not in SIZES:
        raise ValueError(f"{size!r} is not a size; expected one of {', '.join(SIZES)}")
    if not holds_contents(kind):
        raise ValueError(f"a {label_for(kind).lower()} has no size: it is not a container")
    return size


def label_for(kind: str) -> str:
    entry = KINDS.get(kind)
    return str(entry["label"]) if entry else kind
