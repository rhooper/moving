"""Run the real summary model over realistic contents, and time it.

Not a test -- it calls a live model, so it is slow and non-deterministic. Use
it to confirm Ollama is reachable, the model is pulled, and that the line it
writes is one you would want printed on tape. It goes through the app's own
gatherer, request, parser and fallback, so what it prints is what the button
returns.

The built-in cases are the shapes that turn up: a crate of kitchen things, a
box of cables, a container whose children are three unnamed bags, a container
whose children are described, and a list long enough that the assembler has to
truncate it. `--code` reads a real record's whole subtree out of a database
instead, which is the only way to see what deep nesting actually does.

Usage:
    uv run python scripts/claude/try_summary.py
    uv run python scripts/claude/try_summary.py --model qwen2.5:7b --model gemma3:4b
    MOVING_DB_PATH=/tmp/copy.db uv run python scripts/claude/try_summary.py --code B-0015

Point MOVING_DB_PATH at a *copy* for --code. The live database is in the main
checkout, and db.connect migrates whatever it opens.
"""

import argparse
import statistics
import time

from movingbox import phrasing, summarise
from movingbox.config import from_env


#: Each case is a subtree, shaped as store.subtree returns one: the record at
#: depth 0, then what is nested inside it, each node carrying its own items.
def _node(box_id, parent_id, depth, kind, *, summary=None, source="manual", size=None, items=()):
    return {
        "id": box_id,
        "parent_id": parent_id,
        "depth": depth,
        "code": f"X-{box_id:04d}",
        "kind": kind,
        "size": size,
        "content_summary": summary,
        "summary_source": source,
        "items": [{"name": name, "qty": qty} for name, qty in items],
    }


KITCHEN = [
    ("stock pot", 1),
    ("baking pan", 3),
    ("stand mixer", 1),
    ("colander", 1),
    ("mixing bowl", 2),
    ("tea towel", 6),
    ("wooden spoon", 4),
    ("chopping board", 1),
]
CABLES = [
    ("HDMI cable", 4),
    ("USB charger", 3),
    ("power strip", 1),
    ("old router", 1),
    ("label maker", 1),
    ("soldering iron", 1),
    ("tin of resistors", 1),
    ("ethernet cable", 2),
]
WINTER = [
    (name, 1)
    for name in (
        "winter coat",
        "wool scarf",
        "leather gloves",
        "snow boots",
        "thermal socks",
        "ski goggles",
        "woolly hat",
        "down jacket",
        "fleece",
        "rain shell",
        "hiking boots",
        "waterproof trousers",
        "balaclava",
        "hand warmers",
        "gaiters",
    )
]

CASES: dict[str, list[dict]] = {
    "a crate of kitchen things": [_node(1, None, 0, "crate", items=KITCHEN)],
    "a box of cables and electronics": [_node(1, None, 0, "box", items=CABLES)],
    "a crate holding three unnamed bags": [
        _node(1, None, 0, "crate"),
        *(_node(n, 1, 1, "bag") for n in (2, 3, 4)),
    ],
    # The crate should say what the box inside it holds, not "large box".
    "a crate holding described things": [
        _node(1, None, 0, "crate", items=[("kettle", 1)]),
        _node(2, 1, 1, "bag", summary="winter coats and scarves"),
        _node(3, 1, 1, "box", size="large"),
        _node(4, 1, 1, "box", summary="books and photo albums", source="auto"),
        _node(5, 3, 2, "bag", items=[("hardback novel", 12), ("photo album", 3)]),
    ],
    "a long list the assembler has to cut": [_node(1, None, 0, "box", items=WINTER)],
}


def real_subtrees(config, codes: list[str]) -> dict[str, list[dict]]:
    """Whole subtrees read out of a database, by code."""
    from movingbox import db, store

    found = {}
    conn = db.connect(config.db_path)
    try:
        for code in codes:
            nodes = store.subtree(conn, code)
            if not nodes:
                print(f"  (no record {code} in {config.db_path})")
                continue
            deepest = max(n["depth"] for n in nodes)
            found[f"{code} ({len(nodes)} records, {deepest} deep)"] = nodes
    finally:
        conn.close()
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        metavar="M",
        help="model to try; repeat to compare (default: config.summary_model)",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="how many times to ask each case, for a timing median"
    )
    parser.add_argument(
        "--code",
        action="append",
        dest="codes",
        metavar="CODE",
        help="summarise a real record's whole subtree; repeat for several",
    )
    parser.add_argument(
        "--no-warm",
        action="store_true",
        help="skip the warm ping, to see what a press costs on a model Ollama has unloaded",
    )
    args = parser.parse_args()

    config = from_env()
    models = args.models or [config.summary_model]
    cases = dict(CASES)
    if args.codes:
        print(f"database {config.db_path}")
        cases = real_subtrees(config, args.codes)

    print(f"ollama   {config.ollama_url}")
    print(
        f"timeout  {phrasing.TIMEOUT}s   keep_alive {phrasing.KEEP_ALIVE}   "
        f"num_ctx {phrasing.CONTEXT}"
    )
    print(f"prompt   {phrasing.PROMPT_VERSION}")
    print(f"at least {phrasing.ENOUGH} distinct things before a model is asked at all")

    phraser = phrasing.OllamaPhraser(config.ollama_url)

    for model in models:
        print(f"\n{'=' * 72}\n{model}\n{'=' * 72}")
        if not args.no_warm:
            started = time.monotonic()
            try:
                phrasing.warm(config.replace(summary_model=model))
                print(f"warmed in {time.monotonic() - started:.2f}s\n")
            except Exception as failure:  # noqa: BLE001 - this is the diagnostic
                print(f"could not warm it ({failure}) -- carrying on\n")

        times: list[float] = []
        fell_back = 0
        for label, nodes in cases.items():
            contents = summarise.contents(nodes)
            assembled = summarise.from_items(contents)
            print(f"{label}")
            print(f"  gathered   {len(contents)} distinct things from {len(nodes)} record(s)")
            print(f"  assembled  {assembled}")
            for _ in range(args.repeat):
                started = time.monotonic()
                summary, source = phrasing.summary_for(contents, phraser, model=model)
                elapsed = time.monotonic() - started
                if source == "model":
                    times.append(elapsed)
                    print(f"  model      {summary}   [{elapsed:.2f}s]")
                else:
                    fell_back += 1
                    print(
                        f"  FELL BACK  (under {phrasing.ENOUGH} things, or the model "
                        f"did not answer in {phrasing.TIMEOUT}s) [{elapsed:.2f}s]"
                    )
            print()

        if times:
            print(
                f"  -- median {statistics.median(times):.2f}s  max {max(times):.2f}s  "
                f"n={len(times)}; fell back {fell_back} time(s)"
            )
        else:
            print("  -- nothing was phrased at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
