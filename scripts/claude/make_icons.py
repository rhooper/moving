"""Generate the PWA app icons and the favicon.

The app icons: the label's knockout band, a black field with a white bar
across it, so the home-screen icon reads as the same object as the tape on the
box. Maskable-safe -- the bar sits well inside the 80% safe zone.

The favicon: the home mark (docs/design/home-mark.svg, the one source of its
path), a QR finder pattern with a house inside. Drawn on paper, white inside
the ring as a finder pattern is on the tape: with a transparent inside, the
mark would all but vanish on a dark tab bar. Sizes are the mark's grid --
16, 32, 48 -- never one between, where its 2-unit ring goes soft.

Usage: uv run python scripts/claude/make_icons.py
"""

import re
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
HOME_MARK = ROOT / "docs" / "design" / "home-mark.svg"

INK, PAPER = "#000000", "#ffffff"
FAVICON_SIZES = (16, 32, 48)
#: Drawn this many times larger, then reduced, so the roof's diagonal is smooth.
SUPERSAMPLE = 16


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


def home_mark_path() -> str:
    return re.search(r'\sd="([^"]+)"', HOME_MARK.read_text())[1]


def subpaths(d: str) -> list[list[tuple[float, float]]]:
    """The polygons of a path of straight lines (M L H V Z, either case)."""
    shapes: list[list[tuple[float, float]]] = []
    x = y = 0.0
    for command, numbers in re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", d):
        values = [float(n) for n in re.findall(r"-?[\d.]+", numbers)]
        relative = command.islower()
        kind = command.upper()
        if kind == "Z":
            continue
        if kind in "ML":
            pairs = list(zip(values[::2], values[1::2], strict=True))
            for index, (dx, dy) in enumerate(pairs):
                x, y = (x + dx, y + dy) if relative else (dx, dy)
                if kind == "M" and index == 0:
                    shapes.append([])
                shapes[-1].append((x, y))
        elif kind == "H":
            for value in values:
                x = x + value if relative else value
                shapes[-1].append((x, y))
        elif kind == "V":
            for value in values:
                y = y + value if relative else value
                shapes[-1].append((x, y))
    return shapes


def inked(x: float, y: float, edges: list) -> bool:
    """Whether (x, y) is inside the path under the even-odd rule."""
    inside = False
    for (x0, y0), (x1, y1) in edges:
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def favicon(size: int) -> Image.Image:
    """The home mark at `size` pixels, each pixel the share of it that is ink.

    Sampled rather than drawn with ImageDraw.polygon, which fills both edges
    of a shape and so spills a line of grey along every right and bottom edge.
    Samples sit at sub-pixel centres, never on the 16-unit grid the mark is
    drawn on: every edge on the grid comes out pure, and only the roof's
    diagonal is antialiased.
    """
    edges = [
        (shape[i], shape[(i + 1) % len(shape)])
        for shape in subpaths(home_mark_path())
        for i in range(len(shape))
    ]
    unit = 16 / size  # mark units per pixel
    image = Image.new("L", (size, size))
    for py in range(size):
        for px in range(size):
            ink = sum(
                inked(
                    (px + (sx + 0.5) / SUPERSAMPLE) * unit,
                    (py + (sy + 0.5) / SUPERSAMPLE) * unit,
                    edges,
                )
                for sy in range(SUPERSAMPLE)
                for sx in range(SUPERSAMPLE)
            )
            image.putpixel((px, py), round(255 * (1 - ink / SUPERSAMPLE**2)))
    return image.convert("RGB")


def favicon_svg() -> str:
    d = home_mark_path()
    inside = subpaths(d)[1]
    paper = "M" + "L".join(f"{x:g} {y:g}" for x, y in inside) + "Z"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<path fill="{PAPER}" d="{paper}"/>'
        f'<path fill="{INK}" fill-rule="evenodd" d="{d}"/></svg>\n'
    )


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        path = WEB / f"icon-{size}.png"
        draw(size).save(path)
        print(f"{path}  {size}x{size}")

    (WEB / "favicon.svg").write_text(favicon_svg())
    print(WEB / "favicon.svg")
    # Each size drawn for itself, not scaled down from the largest by Pillow.
    images = [favicon(size) for size in FAVICON_SIZES]
    images[-1].save(
        WEB / "favicon.ico",
        sizes=[(s, s) for s in FAVICON_SIZES],
        append_images=images[:-1],
    )
    print(f"{WEB / 'favicon.ico'}  {', '.join(str(s) for s in FAVICON_SIZES)}")


if __name__ == "__main__":
    main()
