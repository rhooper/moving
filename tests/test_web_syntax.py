"""Every front-end module must actually parse.

`node --check web/app.js` looks like this check and is not: on a `.js` file
containing `import`, Node (23.3 here) exits 0 without parsing it as a module,
so a missing brace sails through. `node --input-type=module --check < file` is
the form that works, and `test_the_check_can_fail` keeps this file honest.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
NODE = shutil.which("node")

#: Vendored, and a classic script rather than a module.
SKIP = {"jsQR.js", "sw.js"}

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")


def check(source: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, "--input-type=module", "--check"],
        input=source,
        capture_output=True,
        text=True,
        timeout=60,
    )


def modules() -> list[Path]:
    return sorted(p for p in WEB.glob("*.js") if p.name not in SKIP)


def test_the_scan_finds_the_modules_it_guards():
    assert "app.js" in {p.name for p in modules()}


def test_the_check_can_fail():
    # A guard that cannot fail is the bug this file replaces.
    broken = 'import x from "/y.js";\nfunction f( {\n'

    assert check(broken).returncode != 0


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_it_parses(module):
    result = check(module.read_text())

    assert result.returncode == 0, f"{module.name} does not parse:\n{result.stderr[:600]}"


def test_the_service_worker_parses():
    # A classic script, so plain --check is the right tool for this one.
    result = subprocess.run(
        [NODE, "--check", str(WEB / "sw.js")], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr[:600]
