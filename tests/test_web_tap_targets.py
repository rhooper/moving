"""The way out of a nested record is a target for a thumb, not a caption.

Asked for as "make the parent container font larger to make it easy to click".
The link to the containing record is in two places on a nested record's page --
the breadcrumb above the code and the line in "What it is inside" -- and both
were set at caption size, which is a small thing to hit one-handed while the
other hand is holding the box.

Two properties, and the second is the one that is easy to lose:

- the hit area is a full `--tap` on the touch dimension, and the type is the
  body size rather than the caption size;
- **the line keeps that height whether or not there is a link in it.** The
  record page redraws its parts in place, so "Not inside anything." and
  "Inside B-0012 (crate)." have to be the same height or a move shifts the
  section under a finger. An Undo button that slid sideways between press and
  release is the bug this project already learned that from.

`scripts/claude/nesting_check.mjs` measures both for real, in both states, in
a browser. This is the cheap guard that runs in the deploy gate.
"""

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"

#: Both links, styled by one rule so the two cannot drift apart.
WAY_OUT = ".trail a, #inside-of a"


def stylesheet() -> str:
    page = INDEX.read_text()
    return page[page.index("<style>") : page.index("</style>")]


def rule(selector: str) -> str:
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", stylesheet())
    assert found, f"no rule for {selector!r}"
    return found.group(1)


def test_the_two_links_out_are_styled_by_one_rule():
    # A guard that matches nothing guards nothing -- and two rules would let
    # the breadcrumb and the line drift apart.
    assert rule(WAY_OUT)


def test_the_link_out_is_a_tap_sized_target():
    assert "min-height: var(--tap)" in rule(WAY_OUT)
    # A min-height only does anything on something that is not a bare inline.
    assert "display: inline-flex" in rule(WAY_OUT)


def test_it_is_set_at_the_body_size_and_not_as_a_caption():
    body = rule(WAY_OUT)
    assert "font-size: 1rem" in body, body
    # 0.9375rem is .meta, which is what both of these used to be.
    assert "0.9375rem" not in body


def test_the_line_that_holds_it_is_the_same_height_with_or_without_it():
    # The whole point: a move redraws this line in place, and the section
    # below it must not jump.
    inside = rule("#inside-of")
    assert "min-height: var(--tap)" in inside, inside
    assert "font-size: 1rem" in inside, inside
