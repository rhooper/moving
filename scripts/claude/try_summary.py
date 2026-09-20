"""Run the real summary model over realistic contents lists, and time it.

Not a test -- it calls a live model, so it is slow and non-deterministic. Use
it to confirm Ollama is reachable, the model is pulled, and that the line it
writes is one you would want printed on tape. It goes through the app's own
request, parser and fallback, so what it prints is what the button returns.

The contents lists are the shapes that actually turn up: a crate of kitchen
things, a box of cables, a container whose children are three unnamed bags,
and a list long enough that the assembler has to truncate it.

Usage:
    uv run python scripts/claude/try_summary.py
    uv run python scripts/claude/try_summary.py --model qwen2.5:7b --model gemma3:4b
    uv run python scripts/claude/try_summary.py --repeat 3
"""

import argparse
import statistics
import time

from movingbox import phrasing, summarise
from movingbox.config import from_env

#: (items, children) per case -- children shaped as store.children_of returns
#: them, so the mapping in summarise.describe is exercised too.
CASES: dict[str, tuple[list[dict], list[dict]]] = {
    "a crate of kitchen things": (
        [
            {"name": "stock pot", "qty": 1},
            {"name": "baking pan", "qty": 3},
            {"name": "stand mixer", "qty": 1},
            {"name": "colander", "qty": 1},
            {"name": "mixing bowl", "qty": 2},
            {"name": "tea towel", "qty": 6},
            {"name": "wooden spoon", "qty": 4},
            {"name": "chopping board", "qty": 1},
        ],
        [],
    ),
    "a box of cables and electronics": (
        [
            {"name": "HDMI cable", "qty": 4},
            {"name": "USB charger", "qty": 3},
            {"name": "power strip", "qty": 1},
            {"name": "old router", "qty": 1},
            {"name": "label maker", "qty": 1},
            {"name": "soldering iron", "qty": 1},
            {"name": "tin of resistors", "qty": 1},
            {"name": "ethernet cable", "qty": 2},
        ],
        [],
    ),
    "a crate holding three unnamed bags": (
        [],
        [{"kind": "bag", "content_summary": None, "size": None} for _ in range(3)],
    ),
    "a crate holding described things": (
        [{"name": "kettle", "qty": 1}],
        [
            {"kind": "bag", "content_summary": "winter coats and scarves", "size": None},
            {"kind": "box", "content_summary": None, "size": "large"},
            {"kind": "box", "content_summary": "books and photo albums", "size": None},
        ],
    ),
    "a long list the assembler has to cut": (
        [
            {"name": name, "qty": 1}
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
        ],
        [],
    ),
}


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
        "--no-warm",
        action="store_true",
        help="skip the warm ping, to see what a press costs on a model Ollama has unloaded",
    )
    args = parser.parse_args()

    config = from_env()
    models = args.models or [config.summary_model]

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
        for label, (items, children) in CASES.items():
            contents = summarise.contents(items, children)
            assembled = summarise.from_items(contents)
            print(f"{label}")
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
