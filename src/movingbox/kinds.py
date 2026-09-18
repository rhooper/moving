"""What a labelled record actually is.

Everything the tracker labels — a box, a tub, a bicycle — needs a code, a QR, a
destination, a status and a location. Only some of them have *contents*. Making
that a `kind` on one record rather than a second table means codes, scanning,
search, the event stream, the manifest and the export all keep working with no
special cases; the only thing that varies is how the label reads.
"""

from __future__ import annotations

#: Ordered for the picker: the common containers first.
KINDS: dict[str, dict[str, object]] = {
    "box": {"label": "Box", "contents": True},
    "tub": {"label": "Tub", "contents": True},
    "crate": {"label": "Crate", "contents": True},
    "bag": {"label": "Bag", "contents": True},
    "item": {"label": "Loose item", "contents": False},
    "furniture": {"label": "Furniture", "contents": False},
}

DEFAULT = "box"


def valid(kind: str) -> bool:
    return kind in KINDS


def check(kind: str) -> str:
    if not valid(kind):
        raise ValueError(f"{kind!r} is not a kind; expected one of {', '.join(KINDS)}")
    return kind


def holds_contents(kind: str) -> bool:
    """Whether a list of contents makes sense for this kind.

    A bicycle is described by what it *is*. Listing its pedals is a mistake
    rather than a description, so the label titles it instead of printing a
    contents column, and the print gate asks for a name rather than contents.
    """
    return bool(KINDS.get(kind, KINDS[DEFAULT])["contents"])


def label_for(kind: str) -> str:
    entry = KINDS.get(kind)
    return str(entry["label"]) if entry else kind
