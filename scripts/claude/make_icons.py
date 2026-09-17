"""Generate the PWA app icons.

The mark is the label's knockout band: a black field with a white bar across
it, so the home-screen icon reads as the same object as the tape on the box.
Maskable-safe -- the bar sits well inside the 80% safe zone.

Usage: uv run python scripts/claude/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parents[2] / "web"


def draw(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), "#000000")
    drawing = ImageDraw.Draw(image)
    # Band: centred, 22% tall, inset 26% each side -> inside the maskable
    # safe zone at any crop.
    inset = round(size * 0.26)
    half = round(size * 0.11)
    drawing.rectangle(
        [inset, size // 2 - half, size - inset, size // 2 + half],
        fill="#fafaf8",
    )
    return image


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        path = WEB / f"icon-{size}.png"
        draw(size).save(path)
        print(f"{path}  {size}x{size}")


if __name__ == "__main__":
    main()
