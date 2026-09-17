"""Render a manifest PDF from made-up data, to eyeball the layout.

Usage: uv run python scripts/claude/sample_manifest.py [out.pdf]
"""

import random
import sys
import tempfile
from pathlib import Path

from movingbox import db, export, store
from movingbox.config import Config
from movingbox.manifest_pdf import render

ROOMS = {
    "Kitchen": 14,
    "Living Room": 9,
    "Main Bedroom": 11,
    "Back Bedroom": 6,
    "Office": 17,
    "Bathroom": 3,
    "Garage": 22,
}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "var/sample-manifest.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp())
    config = Config(
        db_path=tmp / "moving.db",
        photo_dir=tmp / "photos",
        label_preview_dir=tmp / "labels",
        backup_dir=tmp / "backups",
    )
    conn = db.connect(config.db_path)
    random.seed(7)
    for name, count in ROOMS.items():
        room = store.create_room(conn, name)["id"]
        for _ in range(count):
            weight = round(random.uniform(3, 24), 1) if random.random() > 0.2 else None
            store.create_box(conn, destination_room_id=room, weight_kg=weight)
    for _ in range(4):
        store.create_box(conn)  # no destination decided yet

    groups = export.manifest(conn)
    conn.close()

    with out.open("wb") as handle:
        render(groups, handle)
    print(f"{out}  ({out.stat().st_size:,} bytes, {sum(g['count'] for g in groups)} boxes)")


if __name__ == "__main__":
    main()
