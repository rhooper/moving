"""A proof sheet of landscape labels, shown inline in the terminal.

Purpose: a "test print" without tape. Renders the cases that stress the
         layout (0-3 handling flags, a loose item, a long summary, no room),
         plus any real codes you name, onto one sheet -- and if this is a
         terminal that speaks iTerm's inline image protocol, draws it right
         there with imgcat.
Date:    2026-09-18
Usage:   uv run python scripts/claude/label_proof.py [CODE ...]
         make proof                 # the stress cases
         make proof CODES="B-0003"  # ...plus real records from the database

Reads the database only when codes are given, and never writes to it.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from movingbox import db, store
from movingbox.config import ROOT, from_env
from movingbox.labels import layout

URL = "https://moving.example.ts.net"
OUT = ROOT / "var" / "label_proof.png"


def sample(code, *, kind="box", room="Kitchen", summary=None, **flags):
    row = {
        "code": code,
        "kind": kind,
        "fragile": 0,
        "open_first": 0,
        "heavy": 0,
        "content_summary": summary or "pots, 3 baking pans, kettle, 2 cutting boards",
    }
    row.update(flags)
    return layout.from_box(row, base_url=URL, room_name=room)


def stress_cases():
    return [
        sample("B-0042"),
        sample("B-0043", fragile=1),
        sample("B-0044", fragile=1, heavy=1, room="Main Bedroom"),
        sample("B-0045", open_first=1, room="Office"),
        sample(
            "B-0123",
            fragile=1,
            open_first=1,
            heavy=1,
            room="Guest Room",
            summary="paperbacks A-M, photo albums, box files, atlas, framed prints",
        ),
        sample("I-0007", kind="item", room="Garage", summary="Bicycle (Trek hybrid)", heavy=1),
        sample("CAM-001", fragile=1, room="Living Room", summary="camera bodies, 3 lenses, tripod"),
        sample(
            "Z06-001",
            room=None,
            summary="U-Haul boxes, plastic wrap, storage containers, tools, duffel bag, "
            "cables, power supply",
        ),
    ]


def real_cases(codes):
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        rooms = {r["id"]: r["name"] for r in store.list_rooms(conn)}
        for code in codes:
            box = store.get_box(conn, code, include_deleted=True)
            if box is None:
                print(f"no such record: {code}", file=sys.stderr)
                continue
            yield layout.from_box(
                dict(box),
                base_url=config.base_url,
                room_name=rooms.get(box["destination_room_id"]),
            )
    finally:
        conn.close()


def sheet_of(cases):
    # One column, so the sheet is barely wider than a label and a terminal can
    # show it at 1:1 -- one image pixel per printer dot -- without scaling.
    width, height, gap, caption = layout.LANDSCAPE_LENGTH, layout.PRINTABLE_WIDTH, 28, 30
    sheet = Image.new("L", (width + gap * 2, (height + gap + caption) * len(cases) + gap), 200)
    draw = ImageDraw.Draw(sheet)
    for index, data in enumerate(cases):
        x = gap
        y = gap + index * (height + gap + caption)
        sheet.paste(layout.render(data, orientation="landscape").convert("L"), (x, y + caption))
        draw.text((x, y + 8), data.code, fill=0)
    return sheet


def with_stub(sheet, data):
    """The one-inch stub under the sheet, turned the way it comes off the roll.

    On tape it is 1 inch long and reads across the 62 mm width -- a quarter
    turn from the labels above. Shown both ways so the proportion is honest.
    """
    stub = layout.render_stub(data).convert("L")
    gap, caption = 28, 30
    out = Image.new(
        "L", (sheet.width, sheet.height + caption + stub.height + gap + stub.width + gap), 200
    )
    out.paste(sheet, (0, 0))
    draw = ImageDraw.Draw(out)
    note = f"stub for {data.code}: as read, and as it sits on the tape"
    draw.text((gap, sheet.height + 8), note, fill=0)
    out.paste(stub, (gap, sheet.height + caption))
    out.paste(stub.rotate(90, expand=True), (gap, sheet.height + caption + stub.height + gap))
    return out


def show(path: Path) -> None:
    viewer = shutil.which("imgcat") or str(Path.home() / "bin" / "imgcat")
    inline = os.environ.get("TERM_PROGRAM") == "iTerm.app" or os.environ.get("LC_TERMINAL")
    if sys.stdout.isatty() and inline and Path(viewer).exists():
        # -W with a pixel width asks iTerm for native size rather than
        # fit-to-window; on a Retina display that is still 1 image px : 1 px.
        with Image.open(path) as image:
            native = f"{image.width}px"
        subprocess.run([viewer, "-W", native, str(path)], check=False)
    else:
        print("(not an inline-image terminal; open the file instead)")


def main() -> None:
    cases = stress_cases() + list(real_cases(sys.argv[1:]))
    sheet = with_stub(sheet_of(cases), cases[0])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 1:1 -- one pixel per printer dot, never resampled. Barcode bars are
    # 3 dots wide; any scaling here would misrepresent exactly what matters.
    sheet.save(OUT)
    print(f"proof -> {OUT}")
    show(OUT)


if __name__ == "__main__":
    main()
