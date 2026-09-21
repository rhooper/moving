"""Fire concurrent requests at a running server, the way the phone does.

Parallel requests are what expose cross-thread sqlite failures; sequential curl
does not.

Usage: uv run python scripts/claude/hammer.py [base_url] [rounds]
"""

import collections
import concurrent.futures
import sys
import urllib.error
import urllib.request

# Exactly what viewBox() requests in parallel.
BOX_PAGE = [
    "/api/boxes/{code}",
    "/api/boxes/{code}/items",
    "/api/rooms",
    "/api/boxes/{code}/photos",
]
OTHER = ["/api/boxes?limit=100", "/api/search?q=pots", "/api/manifest", "/health"]


def fetch(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except Exception:  # noqa: BLE001 - reported as 0 in the tally
        return 0


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 25

    code = "B-0001"
    urls = [base + p.format(code=code) for p in BOX_PAGE + OTHER]

    tally: collections.Counter[int] = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetch, url) for _ in range(rounds) for url in urls]
        for future in concurrent.futures.as_completed(futures):
            tally[future.result()] += 1

    total = sum(tally.values())
    print(f"{total} requests, {rounds} rounds of {len(urls)} in parallel")
    for status, count in sorted(tally.items()):
        label = {0: "connection failed"}.get(status, "")
        print(f"  {status or '---'}  {count:>4}  {label}")

    bad = total - tally.get(200, 0) - tally.get(307, 0)
    print("OK" if bad == 0 else f"{bad} non-2xx responses")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
