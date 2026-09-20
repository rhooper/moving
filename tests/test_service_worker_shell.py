"""The service worker must pre-cache every module the app cannot start without.

`web/app.js` imports its helpers statically, so if any one of them is missing
the whole app fails to load -- there is no partial failure. The worker serves
cache-first and pre-caches the list in `SHELL`; a module left out of it is only
cached once it happens to be fetched online. So a phone that picks up a new
`app.js` and then opens the app in a dead spot gets a blank page. `covers.js`
was left out once, and `autosave.js` would have been the second.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"

STATIC_IMPORT = re.compile(r"^import\s[^;]*?from\s*[\"'](/[^\"']+)[\"'];", re.MULTILINE | re.DOTALL)
DYNAMIC_IMPORT = re.compile(r"import\(\s*[\"'](/[^\"']+)[\"']\s*\)")


def shell() -> set[str]:
    source = (WEB / "sw.js").read_text()
    block = re.search(r"const SHELL = \[(.*?)\];", source, re.DOTALL)
    assert block, "sw.js no longer declares SHELL as a literal list"
    return set(re.findall(r"[\"'](/[^\"']*)[\"']", block.group(1)))


def needed_by(path: str, seen: set[str]) -> set[str]:
    """Every module reachable from `path`, itself included."""
    if path in seen:
        return seen
    seen.add(path)
    source = (WEB / path.lstrip("/")).read_text()
    for found in STATIC_IMPORT.findall(source) + DYNAMIC_IMPORT.findall(source):
        needed_by(found, seen)
    return seen


def test_the_walk_can_see_imports():
    # Guards the guard: a pattern that stopped matching would pass everything.
    assert "/live.js" in needed_by("/app.js", set())
    # The scanner is loaded with import(), not a static import.
    assert "/scan.js" in needed_by("/app.js", set())


def test_every_module_the_app_loads_is_precached():
    missing = sorted(needed_by("/app.js", set()) - shell())

    assert (
        missing == []
    ), f"imported by the app but not in sw.js SHELL, so not available offline: {missing}"
