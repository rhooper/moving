"""Golden-image regression test for the label renderer.

Catches unintended visual drift -- a Pillow upgrade changing hinting, a font
swap, an accidental margin change -- that the behavioural tests would not see.

Regenerate deliberately, and *look at the result* before committing it:

    uv run python scripts/claude/render_samples.py tests/golden --golden
"""

from pathlib import Path

import pytest
from PIL import Image, ImageChops, features

from movingbox.labels import layout

GOLDEN = Path(__file__).parent / "golden"

#: Which renderer each golden exercises. Landscape is the default in use;
#: portrait is kept because it is still reachable and still cut-to-content.
ORIENTATIONS = {
    "typical": "portrait",
    "sparse": "portrait",
    "landscape": "landscape",
    "landscape_full": "landscape",
}

CASES = {
    "typical": layout.LabelData(
        code="B-0042",
        url="https://moving.example.ts.net/b/B-0042",
        room="Kitchen",
        source="Basement shelf 3",
        summary="pots, baking pans, stand mixer, 2 cutting boards, colander, sieve",
        flags=("FRAGILE",),
        footer="box 3 of 5 - 12.4 kg",
    ),
    "sparse": layout.LabelData(
        code="B-0007",
        url="https://moving.example.ts.net/b/B-0007",
    ),
    "landscape": layout.LabelData(
        code="B-0042",
        url="https://moving.example.ts.net/b/B-0042",
        room="Kitchen",
        source="Basement shelf 3",
        summary="pots, 3 baking pans, kettle, 2 cutting boards",
        flags=("FRAGILE",),
        footer="box 3 of 5 - 12.4 kg",
    ),
    "landscape_full": layout.LabelData(
        code="B-0123",
        url="https://moving.example.ts.net/b/B-0123",
        room="Upstairs Back Bedroom",
        source="Living room bookcase",
        summary="paperbacks A-M, photo albums, box files, atlas, framed prints",
        flags=("FRAGILE", "OPEN FIRST", "HEAVY"),
        footer="box 11 of 14 - 18.2 kg",
    ),
}


def test_text_is_shaped_as_the_goldens_were():
    # Without libfribidi, Pillow silently falls back from raqm to its basic
    # layout: no kerning and none of Inter's contextual forms (the hyphen in
    # "B-0042" sits lower). Every golden then differs, and says only where.
    assert features.check("raqm"), (
        "Pillow cannot use raqm text layout, so labels lose Inter's kerning and "
        "every golden differs. Install fribidi (`brew install fribidi`)."
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_label_matches_its_golden_reference(name):
    reference_path = GOLDEN / f"label_{name}.png"
    if not reference_path.exists():
        pytest.fail(
            f"No golden reference at {reference_path}. Generate with:\n"
            f"  uv run python scripts/claude/render_samples.py tests/golden --golden"
        )

    rendered = layout.render(CASES[name], orientation=ORIENTATIONS[name])
    reference = Image.open(reference_path)

    assert rendered.size == reference.size, (
        f"{name}: size changed {reference.size} -> {rendered.size}. "
        f"If intended, regenerate the golden reference."
    )
    difference = ImageChops.difference(rendered.convert("L"), reference.convert("L"))
    assert difference.getbbox() is None, (
        f"{name}: rendering changed inside {difference.getbbox()}. "
        f"If intended, regenerate the golden reference and eyeball it."
    )
