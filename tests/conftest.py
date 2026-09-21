import os

# Set before anything imports config, so no test can reach the physical printer.
# Not via pyproject's `env =` key, which silently does nothing without
# pytest-env.
os.environ["MOVING_PRINTER_BACKEND"] = "fake"

# Set before anything imports config, so no test can reach the Anthropic API:
# the key in `.env` is the owner's billed credential, and `make check` runs on
# every commit and deploy. `from_env()` with no argument reads the real
# environment *and* the real `.env`, and defaults to the cloud provider, so all
# three inputs are neutralised. test_no_test_reaches_the_api.py proves each.
os.environ["MOVING_VISION_PROVIDER"] = "stub"
os.environ.pop("ANTHROPIC_API_KEY", None)
# A path that cannot exist, so the real .env is never read. Parser tests pass
# their own fixture path.
os.environ["MOVING_ENV_FILE"] = "/nonexistent/moving-tests-never-read-a-real-env"

# pyzbar finds libzbar through ctypes.util.find_library, which does not search
# Homebrew's prefix on macOS. ctypes reads this at call time (dyld caches
# DYLD_LIBRARY_PATH at exec), so setting it before pyzbar is imported works.
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
