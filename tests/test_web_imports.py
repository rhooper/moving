"""Front-end modules must use what they import.

There is no JS linter here, and an unused import is the fingerprint of an edit
that half-landed: the import was added, the code that was supposed to call it
was not. That exact failure shipped once — `splitItems` was imported and never
called, so dictated lists were silently never split, while the commit message
said they were.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"

#: Vendored third-party code is not ours to tidy.
SKIP = {"jsQR.js"}

IMPORT = re.compile(r"^import\s*\{([^}]*)\}\s*from\s*[\"'][^\"']+[\"'];", re.MULTILINE)


def imported_names(source: str) -> list[str]:
    names = []
    for match in IMPORT.finditer(source):
        for part in match.group(1).split(","):
            name = part.split(" as ")[-1].strip()
            if name:
                names.append(name)
    return names


def test_every_imported_name_is_used():
    dead = []
    for path in sorted(WEB.glob("*.js")):
        if path.name in SKIP:
            continue
        source = path.read_text()
        body = IMPORT.sub("", source)
        for name in imported_names(source):
            if not re.search(rf"\b{re.escape(name)}\b", body):
                dead.append(f"{path.name}: {name}")

    assert dead == [], "imported but never used — an edit probably half-landed: " + ", ".join(dead)


def test_the_check_can_actually_see_imports():
    # Guards the guard: if the import pattern stopped matching, the test above
    # would pass while checking nothing.
    source = (WEB / "app.js").read_text()

    assert imported_names(source), "no imports parsed out of app.js"
