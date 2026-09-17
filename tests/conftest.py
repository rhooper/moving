import os

# Set before importing anything that reads config, so no test can ever reach the
# physical printer. Done here rather than via pyproject's `env =` key, which
# silently does nothing unless pytest-env is installed.
os.environ["MOVING_PRINTER_BACKEND"] = "fake"

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
