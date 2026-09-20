"""The icon family, inlined once, and used only where it was approved.

The marks are one evenodd path each on the home mark's 16-unit grid (source and
rationale in `docs/design/icons/`). They ship as a single inline `<symbol>`
sprite in index.html, referenced with `<use href="#i-...">`: no build step, no
icon font, no request per glyph, and `currentColor` so one file serves both
themes and follows a chip into its `on` state.

Three things are worth pinning:

- a `<use>` pointing at a symbol that is not there draws nothing at all, in
  silence -- there is no console error and no broken-image box;
- `fill` does **not** reach the cloned symbol from the sprite's own root: the
  clone's ancestors are the referencing `<svg>`, so the fill has to be set
  there. A missed rule is black-on-black in dark mode;
- the words carry the meaning and the icons are decoration, so every icon is
  `aria-hidden` and no labelled control was allowed to lose its name.
"""

import re
from pathlib import Path

from movingbox import kinds

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web" / "index.html"
COVERS = ROOT / "web" / "covers.js"
APP = ROOT / "web" / "app.js"
SPRITE = ROOT / "docs" / "design" / "icons" / "sprite.svg"

#: The three approved homes for a mark. Anything else is a regression -- the
#: status track, the nesting buttons, the section headings, Delete, "Look
#: closer" and the size row were each drawn, tried and turned down
#: (docs/design/icons/NOTES.md).
NAV = ["i-items", "i-scan", "i-new", "i-settings"]
FLAGS = {"fragile": "i-fragile", "heavy": "i-heavy", "open_first": "i-open-first"}


def markup() -> dict[str, str]:
    """Every file that draws an icon -- index.html without its stylesheet.

    A CSS comment explaining the rule is not markup, and saying so here is
    cheaper than writing the explanation around the thing it explains.
    """
    page = INDEX.read_text()
    return {
        "index.html": page[: page.index("<style>")] + page[page.index("</style>") :],
        "app.js": APP.read_text(),
        "covers.js": COVERS.read_text(),
    }


def stylesheet() -> str:
    page = INDEX.read_text()
    return page[page.index("<style>") : page.index("</style>")]


def rule(selector: str) -> str:
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", stylesheet())
    assert found, f"no rule for {selector!r}"
    return found.group(1)


def symbols(source: str) -> set[str]:
    return set(re.findall(r'<symbol\s+id="([^"]+)"', source))


def used(source: str) -> set[str]:
    """Every mark a file names.

    In markup that is a `<use href="#i-...">`; in JS the name is a value, so a
    string literal shaped like a symbol id is the only honest thing to read.
    """
    fragments = re.findall(r'<use\s+href="#(i-[a-z-]+)"', source)
    literals = re.findall(r'"(i-[a-z-]+)"', source)
    return set(fragments) | set(literals)


def test_the_sprite_is_inlined_rather_than_fetched():
    # One sprite in the page: an external file would be a request the offline
    # shell would have to cache, and `<use>` across documents is blocked in
    # every browser. Every reference is therefore a bare fragment.
    page = INDEX.read_text()
    assert symbols(page) == symbols(SPRITE.read_text())
    for source in markup().values():
        assert not re.search(r'<use\s+href="[^#]', source)


def test_every_mark_referenced_is_one_the_sprite_has():
    page = symbols(INDEX.read_text())
    for name, source in markup().items():
        missing = used(source) - page
        assert not missing, f"{name} points at symbols that do not exist: {sorted(missing)}"


def test_something_actually_references_a_mark():
    # A guard that matches nothing guards nothing.
    all_used = set().union(*(used(source) for source in markup().values()))
    assert len(all_used) >= 10, sorted(all_used)


def test_every_icon_is_decoration_as_far_as_a_screen_reader_is_concerned():
    for name, source in markup().items():
        for tag in re.findall(r"<svg\b[^>]*>(?:(?!</svg>).)*?<use\b", source, re.S):
            opening = tag[: tag.index(">") + 1]
            assert 'aria-hidden="true"' in opening, f"{name}: bare icon {opening}"


def test_the_mark_built_by_hand_is_hidden_too():
    # iconNode() sets its attributes rather than writing markup, so the scan
    # above cannot see it. The rows in a list are all built that way.
    source = APP.read_text()
    built = re.search(r"function iconNode\b.*?\n}", source, re.S)
    assert built, "iconNode is gone; the scan above is now the only guard"
    assert 'setAttribute("aria-hidden", "true")' in built.group(0)


def test_the_referencing_element_sets_its_own_fill():
    # The sprite's own root carries fill="currentColor", but a <use> clone's
    # ancestors are the referencing <svg>, not the sprite -- so the attribute
    # on the sprite never reaches the paths. This is that rule.
    css = stylesheet()
    assert re.search(r"\.i\b[^{]*\{[^}]*fill:\s*currentcolor", css), "no fill on the icon class"


def test_the_nav_wears_the_word_as_well_as_the_mark():
    page = INDEX.read_text()
    bar = page[page.index('<nav class="bar">') : page.index("</nav>")]
    assert used(bar) == set(NAV), sorted(used(bar))
    # "Items" and "New" are not guessable from a list glyph and a plus.
    for word in ("Items", "Scan", "New", "Settings"):
        assert f"<span>{word}</span>" in bar, word


def test_every_kind_of_record_has_a_mark_for_its_empty_thumbnail():
    page = symbols(INDEX.read_text())
    source = COVERS.read_text()
    for kind in kinds.KINDS:
        assert f"i-{kind}" in page, kind
        assert f'"i-{kind}"' in source, f"covers.js maps no icon for {kind}"


def test_the_three_handling_flags_carry_the_label_s_own_glyphs():
    page = symbols(INDEX.read_text())
    source = COVERS.read_text()
    for flag, mark in FLAGS.items():
        assert mark in page, mark
        assert flag in source and f'"{mark}"' in source, flag
