import os

# Set before importing anything that reads config, so no test can ever reach the
# physical printer. Done here rather than via pyproject's `env =` key, which
# silently does nothing unless pytest-env is installed.
os.environ["MOVING_PRINTER_BACKEND"] = "fake"

import pytest  # noqa: E402

from movingbox import db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    """A migrated, isolated database per test."""
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()
