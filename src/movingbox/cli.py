"""Command line entry point: ``uv run moving <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import backup, codes, db, export, search, store
from .config import from_env
from .labels import layout, printer

# The rooms this move uses. The pickers filter on `kind`.
STARTER_ROOMS = [
    # Destinations in the new place.
    ("Living Room", "destination"),
    ("Dining Room", "destination"),
    ("Kitchen", "destination"),
    ("Bathroom", "destination"),
    ("Guest Room", "destination"),
    ("Main Bedroom", "destination"),
    # Both: packed from here, and boxes end up here too.
    ("Office", "both"),
    ("Garage", "both"),
    ("Basement", "both"),
    # Sources only.
    ("Bedroom", "source"),
    ("Other", "source"),
]


def cmd_serve(args) -> int:
    import uvicorn

    config = from_env()
    print(f"database   {config.db_path}")
    print(f"base url   {config.base_url}")
    print(f"printer    {config.printer_backend}")
    if config.api_key is None:
        print("auth       OPEN (set MOVING_API_KEY to require a key)")
    print()
    print("The camera needs HTTPS. If this is not localhost, run:")
    print(f"    tailscale serve --bg {args.port}")
    uvicorn.run(
        "movingbox.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        # Tailscale terminates TLS and sends X-Forwarded-Proto: https; without
        # trusting it, request.url says http:// and the camera stops working.
        proxy_headers=True,
        forwarded_allow_ips=args.trust_proxy,
    )
    return 0


def cmd_preview(args) -> int:
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        box = store.get_box(conn, args.code)
        if box is None:
            print(f"No box {args.code}", file=sys.stderr)
            return 1
        data = layout.from_box(
            box,
            base_url=config.base_url,
            room_name=store.room_name(conn, box["destination_room_id"]),
            source_name=store.room_name(conn, box["source_room_id"]),
        )
    finally:
        conn.close()

    image = layout.render(
        data, height=args.height, orientation=args.orientation or config.label_orientation
    )
    out = Path(args.output) if args.output else config.label_preview_dir / f"{args.code}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(f"{out}  ({image.width}x{image.height})")
    return 0


def cmd_print(args) -> int:
    config = from_env()
    if args.backend:
        config = config.replace(printer_backend=args.backend)

    conn = db.connect(config.db_path)
    try:
        # Resolve every code before printing anything, so a bad code wastes no tape.
        jobs = []
        for code in args.codes:
            box = store.get_box(conn, code)
            if box is None:
                print(f"No box {code}", file=sys.stderr)
                return 1
            jobs.append(
                (
                    code,
                    layout.from_box(
                        box,
                        base_url=config.base_url,
                        room_name=store.room_name(conn, box["destination_room_id"]),
                        source_name=store.room_name(conn, box["source_room_id"]),
                    ),
                )
            )

        backend = printer.get_backend(config)
        for code, data in jobs:
            written = backend.print_label(
                layout.render(
                    data,
                    height=args.height,
                    orientation=args.orientation or config.label_orientation,
                ),
                code=code,
                copies=args.copies,
            )
            store.record_print(conn, code)
            print(f"{code} -> {written}  [{config.printer_backend}]")
    finally:
        conn.close()
    return 0


def cmd_backup(args) -> int:
    config = from_env()
    try:
        written = backup.create(config, keep=args.keep)
    except backup.BackupFailed as failure:
        print(f"Backup failed: {failure}", file=sys.stderr)
        return 1
    size_mb = written.stat().st_size / 1_048_576
    kept = backup.existing(config)
    print(f"{written}  ({size_mb:.1f} MB)")
    print(f"{len(kept)} backup{'' if len(kept) == 1 else 's'} kept in {backup.directory(config)}")
    return 0


def cmd_export(args) -> int:
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        text = export.to_csv(conn) if args.format == "csv" else export.to_json(conn)
    finally:
        conn.close()

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"{out}  ({len(text):,} bytes)")
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_manifest(args) -> int:
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        groups = export.manifest(conn)
    finally:
        conn.close()

    if not groups:
        print("No boxes yet.")
        return 0

    width = max(len(g["room"]) for g in groups)
    total_boxes = total_weight = 0
    for group in groups:
        weight = f"{group['weight_kg']:.1f} kg" if group["weight_kg"] else "-"
        unweighed = f"  ({group['unweighed']} unweighed)" if group["unweighed"] else ""
        print(f"{group['room']:<{width}}  {group['count']:>3}  {weight:>9}{unweighed}")
        total_boxes += group["count"]
        total_weight += group["weight_kg"]
    print(f"{'':<{width}}  {'---':>3}")
    print(f"{'Total':<{width}}  {total_boxes:>3}  {total_weight:>6.1f} kg")
    return 0


def cmd_code_format(args) -> int:
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        if args.prefix is None and args.digits is None and args.separator is None:
            shape = codes.get_format(conn)
        else:
            current = codes.get_format(conn)
            try:
                shape = codes.set_format(
                    conn,
                    prefix=current["prefix"] if args.prefix is None else args.prefix,
                    separator=(current["separator"] if args.separator is None else args.separator),
                    digits=current["digits"] if args.digits is None else args.digits,
                )
            except ValueError as bad:
                print(bad, file=sys.stderr)
                return 1

        example = codes.render(1, **shape)
        later = codes.render(42, **shape)
        print(f"prefix     {shape['prefix']}")
        print(f"separator  {shape['separator']!r}")
        print(f"digits     {shape['digits']}")
        print(f"example    {example}  ...  {later}")
        print()
        print("Existing boxes keep the codes already on their labels.")
    finally:
        conn.close()
    return 0


def cmd_set_sequence(args) -> int:
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        shape = codes.get_format(conn)
        try:
            codes.set_sequence(conn, args.number)
        except ValueError as bad:
            print(bad, file=sys.stderr)
            return 1
        print(f"next code for prefix {shape['prefix']}: {codes.render(args.number, **shape)}")
    finally:
        conn.close()
    return 0


def cmd_reindex(args) -> int:
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        print(f"reindexed {search.reindex_all(conn)} boxes")
    finally:
        conn.close()
    return 0


def cmd_thumbnails(args) -> int:
    """Give every photo its strip image. Touches nothing else; see renditions.backfill."""
    from . import renditions

    config = from_env()
    conn = db.connect(config.db_path)
    try:
        report = renditions.backfill(conn, config.photo_dir, dry_run=args.dry_run, prune=args.prune)
    finally:
        conn.close()

    verb = "would make" if args.dry_run else "made"
    print(
        f"{verb} {report['made']} strip image(s) at version {renditions.VERSION}; "
        f"{report['present']} already there"
    )
    if args.prune:
        verb = "would remove" if args.dry_run else "removed"
        print(f"{verb} {report['pruned']} from an older recipe")
    for name in report["missing"]:
        print(f"  skipped {name}: its full image is not on disk")
    return 0


def cmd_seed_rooms(args) -> int:
    """Create the standard rooms, and correct the kind of any that already exist.

    Rooms not in the list are kept: boxes may point at them.
    """
    config = from_env()
    conn = db.connect(config.db_path)
    try:
        existing = {r["name"]: r for r in store.list_rooms(conn)}
        wanted = {name for name, _ in STARTER_ROOMS}

        for index, (name, kind) in enumerate(STARTER_ROOMS):
            current = existing.get(name)
            if current is None:
                store.create_room(conn, name, kind=kind, sort_order=index)
                print(f"+ {name:<13} {kind}")
            elif current["kind"] != kind or current["sort_order"] != index:
                conn.execute(
                    "UPDATE rooms SET kind = ?, sort_order = ? WHERE id = ?",
                    (kind, index, current["id"]),
                )
                print(f"~ {name:<13} {current['kind']} -> {kind}")

        for name, room in existing.items():
            if name not in wanted:
                used = conn.execute(
                    "SELECT count(*) FROM boxes "
                    "WHERE destination_room_id = ? OR source_room_id = ?",
                    (room["id"], room["id"]),
                ).fetchone()[0]
                note = f"{used} box(es) still use it" if used else "unused"
                print(f"? {name:<13} not in the standard list; kept ({note})")

        print(f"{len(store.list_rooms(conn))} rooms")
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moving", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API and web UI")
    # Loopback: `tailscale serve` proxies to 127.0.0.1, and binding every
    # interface would expose the app unauthenticated on the LAN.
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--trust-proxy",
        default="127.0.0.1",
        help="hosts whose X-Forwarded-* headers are trusted "
        "(default: loopback, i.e. tailscale serve)",
    )
    serve.set_defaults(func=cmd_serve)

    preview = sub.add_parser("preview", help="render a label to a PNG without printing")
    preview.add_argument("code")
    preview.add_argument("-o", "--output")
    preview.add_argument("--height", type=int, help="portrait only: exact cut height")
    preview.add_argument("--orientation", choices=("landscape", "portrait"))
    preview.set_defaults(func=cmd_preview)

    print_ = sub.add_parser("print", help="print one or more labels")
    print_.add_argument("codes", nargs="+")
    print_.add_argument("--copies", type=int, default=1)
    print_.add_argument("--height", type=int)
    print_.add_argument("--orientation", choices=("landscape", "portrait"))
    print_.add_argument(
        "--backend",
        choices=sorted(printer.BACKENDS),
        help="override MOVING_PRINTER_BACKEND (default: fake, which only writes a preview)",
    )
    print_.set_defaults(func=cmd_print)

    back = sub.add_parser("backup", help="write a verified backup and prune old ones")
    back.add_argument("--keep", type=int, default=backup.DEFAULT_KEEP)
    back.set_defaults(func=cmd_backup)

    exp = sub.add_parser("export", help="dump everything to json or csv")
    exp.add_argument("--format", choices=("json", "csv"), default="json")
    exp.add_argument("-o", "--output", help="write to a file instead of stdout")
    exp.set_defaults(func=cmd_export)

    man = sub.add_parser("manifest", help="box counts and weight per destination room")
    man.set_defaults(func=cmd_manifest)

    fmt = sub.add_parser("code-format", help="show or set the box code format")
    fmt.add_argument("--prefix", help="e.g. B, CAM, Z06")
    fmt.add_argument("--separator", help="'-', '_', '.' or '' for none")
    fmt.add_argument("--digits", type=int, help="length of the number, e.g. 3 for 001")
    fmt.set_defaults(func=cmd_code_format)

    seq = sub.add_parser("set-sequence", help="set the next number for the current prefix")
    seq.add_argument("number", type=int)
    seq.set_defaults(func=cmd_set_sequence)

    reindex = sub.add_parser("reindex", help="rebuild the search index")
    reindex.set_defaults(func=cmd_reindex)

    thumbs = sub.add_parser(
        "thumbnails", help="make the sharp photo-strip image for every photo that lacks one"
    )
    thumbs.add_argument(
        "--dry-run", action="store_true", help="say what would be made, and make nothing"
    )
    thumbs.add_argument(
        "--prune", action="store_true", help="also remove strip images from an older recipe"
    )
    thumbs.set_defaults(func=cmd_thumbnails)

    seed = sub.add_parser("seed-rooms", help="create a starter set of rooms")
    seed.set_defaults(func=cmd_seed_rooms)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
