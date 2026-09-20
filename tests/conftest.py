import os

# Set before importing anything that reads config, so no test can ever reach the
# physical printer. Done here rather than via pyproject's `env =` key, which
# silently does nothing unless pytest-env is installed.
os.environ["MOVING_PRINTER_BACKEND"] = "fake"

# Set before importing anything that reads config, so no test can ever reach
# the Anthropic API. The key in `.env` is the owner's billed credential, and
# `make check` runs on every commit and every deploy -- a suite that picked it
# up would spend their money every run.
#
# All three inputs are neutralised, because `from_env()` with no argument reads
# the real environment *and* the real `.env`, and the cloud tier made "claude"
# its default provider: one bare call anywhere in the suite would be enough.
# `tests/test_no_test_reaches_the_api.py` proves each one.
os.environ["MOVING_VISION_PROVIDER"] = "stub"
os.environ.pop("ANTHROPIC_API_KEY", None)
# A path that cannot exist, so the real .env is never the file that is read.
# Tests that exercise the parser pass their own fixture path explicitly.
os.environ["MOVING_ENV_FILE"] = "/nonexistent/moving-tests-never-read-a-real-env"

# pyzbar resolves libzbar through ctypes.util.find_library, which on macOS does
# not search Homebrew's prefix. ctypes reads this from os.environ at call time
# (unlike DYLD_LIBRARY_PATH, which dyld caches at exec), so setting it here --
# before pyzbar is imported -- is enough, and beats making everyone remember to
# export it. Harmless on platforms where zbar is already on the search path.
os.environ.setdefault(
    "DYLD_FALLBACK_LIBRARY_PATH",
    "/opt/homebrew/lib:/usr/local/lib:/usr/lib",
)

import pytest  # noqa: E402

from movingbox import db  # noqa: E402
from movingbox.config import Config  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    """A migrated, isolated database per test."""
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def config(tmp_path):
    """Config pointing entirely inside the test's tmp dir, printer stubbed."""
    return Config(
        db_path=tmp_path / "moving.db",
        photo_dir=tmp_path / "photos",
        label_preview_dir=tmp_path / "labels",
        base_url="https://test.example.ts.net",
        api_key=None,
        printer_backend="fake",
    )
