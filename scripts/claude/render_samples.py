"""Render sample labels to a contact sheet for visual review.

Purpose: eyeball layout changes without printing tape.
Usage:   uv run python scripts/claude/render_samples.py [outdir]
"""

import sys
from pathlib import Path

from PIL import Image

from movingbox.labels import layout

SAMPLES = {
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
    "busy": layout.LabelData(
        code="B-0123",
        url="https://moving.example.ts.net/b/B-0123",
        room="Upstairs Back Bedroom",
        source="Living room bookcase, top two shelves",
        summary=(
            "paperbacks A-M, photo albums, box files, atlas, three framed "
            "prints wrapped in tea towels"
        ),
        flags=("FRAGILE", "OPEN FIRST", "HEAVY"),
        footer="box 11 of 14 - 18.2 kg",
    ),
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    golden = "--golden" in sys.argv
    outdir = Path(args[0] if args else "var/labels/preview")
    outdir.mkdir(parents=True, exist_ok=True)

    if golden:
        # The golden cases live with their test, so there is one definition.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.test_label_golden import CASES, ORIENTATIONS

        for name, data in CASES.items():
            image = layout.render(data, orientation=ORIENTATIONS[name])
            image.save(outdir / f"label_{name}.png")
            print(f"golden {name} -> {outdir / f'label_{name}.png'}")
        return

    gap = 20
    sheet = Image.new(
        "1",
        (layout.PRINTABLE_WIDTH * len(SAMPLES) + gap * (len(SAMPLES) - 1), layout.DEFAULT_HEIGHT),
        1,
    )
    for index, (name, data) in enumerate(SAMPLES.items()):
        image = layout.render(data)
        image.save(outdir / f"label_{name}.png")
        sheet.paste(image, (index * (layout.PRINTABLE_WIDTH + gap), 0))
        print(f"{name:8} {image.size} {image.mode}")

    sheet.save(outdir / "label_sheet.png")
    print(f"sheet -> {outdir / 'label_sheet.png'}")


if __name__ == "__main__":
    main()
