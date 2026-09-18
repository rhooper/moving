"""Code 128, code set B: enough to put a box number under a laser.

For a keyboard-wedge barcode reader -- the kind that types what it scans --
so the payload is the box number and nothing else. The phone uses the QR.

Hand-written rather than a dependency: the symbology is a fixed table and a
checksum. That table was typed by hand, so tests/test_code128.py makes zbar
read back every one of its values, including the eight that can only ever
appear as a checksum.
"""

from __future__ import annotations

#: Bar/space widths for symbol values 0-105, then the 13-module stop. Each
#: pattern starts with a bar and alternates; every one but the stop is eleven
#: modules wide.
PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213",
    "122312", "132212", "221213", "221312", "231212", "112232", "122132",
    "122231", "113222", "123122", "123221", "223211", "221132", "221231",
    "213212", "223112", "312131", "311222", "321122", "321221", "312212",
    "322112", "322211", "212123", "212321", "232121", "111323", "131123",
    "131321", "112313", "132113", "132311", "211313", "231113", "231311",
    "112133", "112331", "132131", "113123", "113321", "133121", "313121",
    "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212",
    "124112", "124211", "411212", "421112", "421211", "212141", "214121",
    "412121", "111143", "111341", "131141", "114113", "114311", "411113",
    "411311", "113141", "114131", "311141", "411131", "211412", "211214",
    "211232", "2331112",
)  # fmt: skip

START_B = 104
STOP = 106


def _values(text: str) -> list[int]:
    values = []
    for char in text:
        value = ord(char) - 32
        if not 0 <= value <= 94:
            raise ValueError(f"Code 128 set B cannot encode {char!r}")
        values.append(value)
    return values


def checksum(text: str) -> int:
    """The check symbol's value: start plus position-weighted data, mod 103."""
    total = START_B + sum(index * value for index, value in enumerate(_values(text), start=1))
    return total % 103


def modules(text: str) -> list[bool]:
    """The barcode as modules, True for a bar."""
    out: list[bool] = []
    for value in [START_B, *_values(text), checksum(text), STOP]:
        bar = True
        for width in PATTERNS[value]:
            out.extend([bar] * int(width))
            bar = not bar
    return out


def width(text: str, module: int) -> int:
    """Pixels across, bars only -- the caller owns the quiet zones."""
    return len(modules(text)) * module


def draw(canvas_draw, x: int, y: int, text: str, *, module: int, height: int) -> None:
    """Bars only, top-left at (x, y). Leave ten modules of white either side."""
    for index, bar in enumerate(modules(text)):
        if bar:
            left = x + index * module
            canvas_draw.rectangle([left, y, left + module - 1, y + height - 1], fill=0)
