"""Run the front-end scanner tests as part of the normal suite.

The code-parsing in web/scan.js decides what a scanned label resolves to, which
matters as much as anything on the Python side -- and it silently stopped
matching two of the three configurable code shapes once the format became
configurable. `uv run pytest` should catch that.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


def test_the_scanner_parses_codes_correctly():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; front-end tests not run")

    files = sorted(str(p) for p in TESTS.glob("*.test.mjs"))
    assert files, "no .test.mjs files found"

    result = subprocess.run(
        [node, "--test", *files],
        capture_output=True,
        text=True,
        cwd=TESTS.parent,
    )
    assert result.returncode == 0, result.stdout + result.stderr
