"""Golden-image regression test for the label renderer.

Catches unintended visual drift -- a Pillow upgrade changing hinting, a font
swap, an accidental margin change -- that the behavioural tests would not see.

Regenerate deliberately, and *look at the result* before committing it:

    uv run python scripts/claude/render_samples.py tests/golden --golden
"""

from pathlib import Path

import pytest
from PIL import Image, ImageChops

from movingbox.labels import layout

GOLDEN = Path(__file__).parent / "golden"

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
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_label_matches_its_golden_reference(name):
    reference_path = GOLDEN / f"label_{name}.png"
    if not reference_path.exists():
        pytest.fail(
            f"No golden reference at {reference_path}. Generate with:\n"
            f"  uv run python scripts/claude/render_samples.py tests/golden --golden"
        )

    rendered = layout.render(CASES[name])
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
