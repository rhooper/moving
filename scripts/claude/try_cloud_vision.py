"""Purpose: prove the cloud vision tier end to end, through the app's own code.

Not a test -- it uploads real photos to a running server, which reads them with
a real model, which costs real money. Use it once after setting a key, to see
what the money buys: per-photo latency, the items found, and the cost the app
recorded from the API's own `usage` numbers.

It talks to **the app**, never to the Anthropic API: every call goes through
the same upload -> queue -> worker -> merge path a phone uses, so what it
measures is what a person would experience, and nothing here can spend the key
on anything the app was not built to do.

It creates records, so it refuses the live service (port 8787) and anything
that is not loopback -- the same rule the writing browser checks follow.

Usage:
    # a throwaway server, with its own database and the key from .env
    MOVING_DB_PATH=/tmp/try/moving.db MOVING_PHOTO_DIR=/tmp/try/photos \\
    MOVING_LABEL_PREVIEW_DIR=/tmp/try/labels MOVING_BACKUP_DIR=/tmp/try/backups \\
    MOVING_PRINTER_BACKEND=fake MOVING_VISION_PROVIDER=claude \\
    MOVING_ENV_FILE=/path/to/.env \\
    uv run moving serve --port 8799 &

    uv run python scripts/claude/try_cloud_vision.py --url http://127.0.0.1:8799 \\
        --sample 3 --detail
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from movingbox.config import ROOT

#: The live service. Creating records there would put real boxes in the real
#: database, and reading them would spend the real budget.
LIVE_PORT = 8787

#: How long one photo may take before this gives up on it. The local fallback
#: has a 20 minute timeout of its own, so a hung read shows as a long wait
#: rather than as an error; this is the point at which it is not worth waiting.
PATIENCE = 300.0
POLL = 1.0


def refuse_the_live_service(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing {url}: this writes records, so it only runs on loopback")
    if (parsed.port or 80) == LIVE_PORT:
        raise SystemExit(
            f"refusing port {LIVE_PORT}: that is the live service. Start a throwaway "
            f"server on another port -- see the usage note at the top of this file."
        )


def sample_photos(count: int) -> list[Path]:
    """The newest photos from the main checkout's store.

    Real photographs of real boxes, which is the only honest input for this:
    a synthetic drawing tells you the plumbing works and nothing about whether
    the model can read a handwritten label.
    """
    store = ROOT / "var" / "photos"
    if not store.is_dir():
        # A worktree has its own empty var/. The main checkout is where the
        # photos are, and this script only ever reads them.
        store = Path("/path/to/moving/var/photos")
    if not store.is_dir():
        raise SystemExit(f"no photo store at {store}; pass image paths instead")
    # *-thumb.jpg are the list thumbnails, not what gets read.
    photos = sorted(
        (p for p in store.glob("*.jpg") if not p.stem.endswith("-thumb")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not photos:
        raise SystemExit(f"no photos in {store}; pass image paths instead")
    return photos[:count]


def wait_for_the_read(client: httpx.Client, code: str, photo_id: int) -> tuple[dict, float]:
    """Poll the record until this photo has been read. Returns (state, seconds)."""
    started = time.monotonic()
    while time.monotonic() - started < PATIENCE:
        photos = client.get(f"/api/boxes/{code}/photos").raise_for_status().json()
        state = next((p["analysis"] for p in photos if p["id"] == photo_id), None)
        if state and state["status"] in ("done", "error"):
            return state, time.monotonic() - started
        time.sleep(POLL)
    return {"status": "timeout", "error": f"nothing after {PATIENCE:.0f}s"}, PATIENCE


def describe(state: dict, seconds: float, *, label: str) -> None:
    who = state.get("model") or "?"
    if state["status"] != "done":
        print(f"  {label:<12} {seconds:6.1f}s  FAILED via {who}: {state.get('error')}")
        return
    items = state.get("items") or []
    print(f"  {label:<12} {seconds:6.1f}s  {len(items):2d} items via {who}")
    if state.get("summary"):
        print(f"               summary: {state['summary']}")
    for item in items:
        qty = f" x{item['qty']}" if item.get("qty", 1) > 1 else ""
        print(f"               - {item['name']}{qty}")


def spend_of(client: httpx.Client) -> dict:
    return client.get("/api/settings/spend").raise_for_status().json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", type=Path, help="photos to read")
    parser.add_argument("--url", default="http://127.0.0.1:8799")
    parser.add_argument("--sample", type=int, default=0, help="take N photos from var/photos")
    parser.add_argument("--detail", action="store_true", help="also run the closer look")
    args = parser.parse_args()

    refuse_the_live_service(args.url)
    images = list(args.images) or sample_photos(args.sample or 2)

    with httpx.Client(base_url=args.url, timeout=60.0) as client:
        health = client.get("/health").raise_for_status().json()
        before = spend_of(client)
        print(f"server   {args.url}  (revision {health['revision']}, v{health['version']})")
        print(f"provider {before['provider']}   key: {'yes' if before['key'] else 'no'}")
        print(f"models   {before['model']}  /  {before['detail_model']} (closer look)")
        print(f"local    {before['local_model']}  /  {before['local_detail_model']}")
        print(f"budget   {before['spent_usd']:.4f} of {before['cap_usd']:.2f} USD spent")
        if before["reading_locally"]:
            print("         NOTE: reading locally -- no key, wrong provider, or over budget")
        print()

        for image in images:
            code = client.post("/api/boxes", json={}).raise_for_status().json()["code"]
            print(f"{image.name}  ->  {code}  ({image.stat().st_size:,} bytes)")
            with image.open("rb") as handle:
                photo = (
                    client.post(
                        f"/api/boxes/{code}/photos",
                        files={"file": (image.name, handle, "image/jpeg")},
                    )
                    .raise_for_status()
                    .json()
                )
            state, seconds = wait_for_the_read(client, code, photo["id"])
            describe(state, seconds, label="quick read")

            if args.detail:
                client.post(
                    f"/photos/{photo['id']}/analyse", params={"detail": "true"}
                ).raise_for_status()
                state, seconds = wait_for_the_read(client, code, photo["id"])
                describe(state, seconds, label="look closer")
            print()

        after = spend_of(client)
        spent = after["spent_usd"] - before["spent_usd"]
        print(f"this run cost ${spent:.4f} over {len(images)} photos")
        if spent and images:
            print(f"  which is ${spent / len(images) * 100:.2f} per 100 photos at this mix")
        for row in after["by_model"]:
            print(f"  {row['model']:<28} {row['photos']:3d} reads  ${row['spent_usd']:.4f}")
        print(f"budget now ${after['spent_usd']:.4f} of ${after['cap_usd']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
