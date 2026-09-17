import os

# Set before importing anything that reads config, so no test can ever reach the
# physical printer. Done here rather than via pyproject's `env =` key, which
# silently does nothing unless pytest-env is installed.
os.environ["MOVING_PRINTER_BACKEND"] = "fake"

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
