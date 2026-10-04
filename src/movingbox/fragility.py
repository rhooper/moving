"""Marking a record fragile when something breakable goes into it.

Asked for as "given it contains a mug or figurine, auto-add fragile". Words,
not a model: offline, the same answer every time, and a rule a person can
read. The photo reader's own `fragile` (what it saw, not what it named) is
applied by `analysis` through `mark` too.

Only ever adds the flag, and never against a person: once somebody has
cleared fragile on a record, nothing here marks it again.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import store

#: Breakable on their own, as whole words in either number.
BREAKABLE = frozenset("""
    mug mugs teacup teacups glass glasses glassware wineglass wineglasses
    ceramic ceramics porcelain china pottery stoneware earthenware crystal
    figurine figurines statue statues statuette sculpture sculptures
    ornament ornaments vase vases mirror mirrors bulb bulbs lightbulb lightbulbs
    monitor monitors screen screens tv tvs television terrarium aquarium
    decanter carafe teapot
    """.split())

#: Breakable only together: a picture frame has glass, a frame of a bed not.
PHRASES = (("picture", "frame"), ("picture", "frames"), ("photo", "frame"), ("photo", "frames"))

#: A word that says it is not the breakable kind after all.
STURDY = frozenset("""
    plastic metal steel stainless aluminium aluminum tin silicone enamel enamelled
    acrylic paper wooden wood rubber fabric cloth foam weight suction travel case
    """.split())

_WORD = re.compile(r"[a-z0-9]+")


def breakable(name: str) -> bool:
    """Whether a thing by this name should make its container fragile."""
    words = _WORD.findall(str(name or "").lower())
    if not words or STURDY.intersection(words):
        return False
    if BREAKABLE.intersection(words):
        return True
    return any(pair in PHRASES for pair in zip(words, words[1:], strict=False))


def reasons(nodes: list[dict[str, Any]]) -> list[str]:
    """The breakable things in `store.subtree`'s nodes, nearest first.

    Each record's items; a nested record that lists nothing is read by its
    summary. Something fragile inside a tub makes the crate it sits in
    fragile too, which is how "fragile climbs" is meant.
    """
    found: list[str] = []
    for node in nodes or ():
        names = [str(item.get("name") or "") for item in node.get("items") or ()]
        if not names and node.get("depth", 0) > 0:
            names = [str(node.get("content_summary") or "")]
        found.extend(name for name in names if breakable(name) and name not in found)
    return found


def cleared_by_a_person(conn: sqlite3.Connection, code: str) -> bool:
    """Whether anybody but the machine has ever turned fragile off here."""
    return (
        conn.execute(
            """
            SELECT 1 FROM events
             WHERE box_code = ? AND kind = 'edit' AND field = 'fragile' AND to_value = '0'
               AND COALESCE(actor, '') NOT IN ('ai', 'auto')
             LIMIT 1
            """,
            (code,),
        ).fetchone()
        is not None
    )


def markable(conn: sqlite3.Connection, code: str) -> bool:
    """Not fragile yet, not binned, and not cleared by a person."""
    box = store.get_box(conn, code)
    return bool(box) and not box["fragile"] and not cleared_by_a_person(conn, code)


def mark(conn: sqlite3.Connection, code: str, *, because: str, actor: str) -> bool:
    """Mark the record fragile, logged with why. Returns whether it changed."""
    if not markable(conn, code):
        return False
    store.update_box(conn, code, fragile=1, actor=actor, note=f"holds {because}")
    return True


def mark_for_contents(conn: sqlite3.Connection, code: str, *, actor: str) -> list[str]:
    """Mark the record fragile if anything in it is breakable; the reasons if it was."""
    if not markable(conn, code):
        return []
    found = reasons(store.subtree(conn, code))
    if found:
        mark(conn, code, because=found[0], actor=actor)
    return found
