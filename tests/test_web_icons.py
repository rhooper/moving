"""The icon family, inlined once, and used only where it was approved.

The marks are one evenodd path each on the home mark's 16-unit grid (source in
`docs/design/icons/`), shipped as a single inline `<symbol>` sprite in
index.html and referenced with `<use href="#i-...">`, filled with
`currentColor` so they follow the theme and a chip's `on` state.

- a `<use>` pointing at a symbol that is not there draws nothing, silently --
  no console error and no broken-image box;
- `fill` does **not** reach the cloned symbol from the sprite's own root: the
  clone's ancestors are the referencing `<svg>`, so the fill has to be set
  there. A missed rule is black-on-black in dark mode;
- the icons are decoration, so every one is `aria-hidden` and no labelled
  control loses its name.
"""

import re
from pathlib import Path

import pytest

from movingbox import kinds

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web" / "index.html"
COVERS = ROOT / "web" / "covers.js"
APP = ROOT / "web" / "app.js"
SPRITE = ROOT / "docs" / "design" / "icons" / "sprite.svg"

#: The four approved homes for a mark. The status track, the nesting buttons,
#: the section headings, Delete, "Look closer" and the size row were each tried
#: and turned down.
NAV = ["i-items", "i-scan", "i-new", "i-settings"]
FLAGS = {"fragile": "i-fragile", "heavy": "i-heavy", "open_first": "i-open-first"}
#: The fourth: the record sheet's camera, beside Edit -- the one control that
#: is a mark alone, so it has to carry its name some other way.
CAMERA = "i-camera"


def markup() -> dict[str, str]:
    """Every file that draws an icon -- index.html without its stylesheet,
    whose comments are not markup."""
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
    # A <use> clone's ancestors are the referencing <svg>, not the sprite, so
    # the sprite's own fill="currentColor" never reaches the paths.
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


def test_the_one_wordless_button_says_its_name():
    """The sheet's camera button has no word beside its mark: the header is
    the code as hero with Edit at its end. A screen reader needs the name
    from somewhere, so the button carries it itself."""
    app = APP.read_text()
    button = re.search(r'<button[^>]*\bid="take-photo"[^>]*>(.*?)</button>', app, re.S)
    assert button, "the sheet draws no #take-photo button"
    opening = button.group(0)[: button.group(0).index(">") + 1]
    assert re.search(r'aria-label="[^"]*[Pp]hoto', opening), opening
    assert 'title="' in opening, "the same words on hover"
    assert f'"{CAMERA}"' in button.group(1), "its mark is the camera"


def test_the_camera_mark_has_one_home():
    # One button, one place: not a picker, not a heading, not a tile.
    assert APP.read_text().count(f'"{CAMERA}"') == 1
    assert CAMERA not in COVERS.read_text()


def test_the_three_handling_flags_carry_the_label_s_own_glyphs():
    page = symbols(INDEX.read_text())
    source = COVERS.read_text()
    for flag, mark in FLAGS.items():
        assert mark in page, mark
        assert flag in source and f'"{mark}"' in source, flag


# --- the 4 px radius, as a token -----------------------------------------

#: Box-like surfaces. A rule (.section, .err) and a glyph (an icon, the home
#: mark) are not surfaces and stay square.
ROUNDED = [
    "input, textarea, button, select",
    # The row, not its buttons: a pushbutton row is one control, so only its
    # outside is rounded and the seams between buttons stay square.
    ".seg-row",
    ".boxlist .t",
    "dialog",
    ".shots figure",
    "#live",
    ".say",
    # These two mirror the square-cornered printed label most directly; kept
    # last so their radius can come off alone.
    ".band",
    ".flag",
]


def test_the_radius_is_a_token():
    assert re.search(r":root\s*\{[^}]*--radius:\s*4px", stylesheet())


@pytest.mark.parametrize("selector", ROUNDED)
def test_the_box_like_surfaces_are_rounded(selector):
    assert "border-radius: var(--radius)" in rule(selector), selector


def test_the_seams_between_pushbuttons_stay_square():
    # The row clips its children, which squares the inside and rounds the
    # outside at once -- so the buttons must not round themselves.
    button = rule(".seg-row span")
    assert "border-radius: 0" in button, button
    assert "overflow: hidden" in rule(".seg-row")


def test_no_site_hard_codes_the_number():
    # One token, so "slightly rounded" stays one decision rather than twelve.
    values = re.findall(r"border-radius:\s*([^;]+);", stylesheet())
    stray = [v.strip() for v in values if v.strip() not in {"var(--radius)", "50%", "0"}]
    assert not stray, stray


# --- the rows inside a container -----------------------------------------


def px(text: str, name: str) -> int:
    found = re.search(rf"{re.escape(name)}:\s*(\d+)px", text)
    assert found, f"{name} is not a plain px value in {text!r}"
    return int(found.group(1))


def test_a_nested_row_s_thumbnail_is_half_again_the_size_of_a_list_row_s():
    root = rule(":root")
    inside = rule("#inside")
    assert px(inside, "--thumb") == round(px(root, "--thumb") * 1.5)


def test_the_kind_mark_in_it_grows_to_match_and_lands_on_whole_pixels():
    root = rule(":root")
    inside = rule("#inside")
    grown = px(inside, "--kind-icon")
    # 26 x 1.5 is 39; multiples of 8 land every edge of a 16-unit glyph on a
    # pixel.
    assert grown % 8 == 0
    assert round(px(root, "--kind-icon") * 1.5) <= grown <= round(px(root, "--kind-icon") * 1.6)


def test_the_sizes_the_marks_are_drawn_at_are_the_ones_that_were_approved():
    css = stylesheet()
    assert "width: 16px" in rule(".i"), "the base mark is 16px (a chip's text size)"
    assert "width: 24px" in rule(".bar a .i"), "24px stacked over the word on a phone"
    # The camera button: a full tap square, its mark at 24 (16 vanishes on a
    # 44px square, 32 crowds it, 28 is the unkind size).
    assert "width: 24px" in rule(".top .mark .i")
    assert "width: var(--tap)" in rule(".top .mark")
    assert "min-height: var(--tap)" in rule(".top .btn"), "Edit's own rule stands"
    # 20px beside the word in the desktop bar, inside the wide-screen block.
    wide = css[css.index("@media (min-width: 46rem)") :]
    assert re.search(r"\.bar a \.i\s*\{[^}]*width:\s*20px", wide)
