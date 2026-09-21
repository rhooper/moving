"""The suite must never reach the Anthropic API.

The key in `.env` is the owner's billed credential, and `make check` runs on
every commit and deploy. `from_env()` with no argument reads the real
environment *and* the real `.env` and defaults to the cloud provider, so
`conftest.py` neutralises all three inputs before anything imports config.

The assertions compare booleans, never values: pytest prints compared values
on failure, which here would print the credential.
"""

from __future__ import annotations

from movingbox import config


class TestTheRealEnvironmentCannotReachTheSuite:
    def test_a_bare_from_env_finds_no_key(self):
        found = config.from_env().anthropic_api_key is not None
        assert not found, "a real API key reached the test suite"

    def test_a_bare_from_env_does_not_choose_the_cloud(self):
        assert config.from_env().vision_provider != "claude"

    def test_the_stub_is_what_it_chooses(self):
        assert config.from_env().vision_provider == "stub"


class TestTheGuardIsWhereItCannotBeForgotten:
    def test_conftest_sets_it_before_anything_imports_config(self):
        # At import time, not in a fixture: a fixture protects only the tests
        # that ask for it.
        source = (config.ROOT / "tests" / "conftest.py").read_text()
        assert 'os.environ["MOVING_VISION_PROVIDER"] = "stub"' in source
        assert "ANTHROPIC_API_KEY" in source
        assert "MOVING_ENV_FILE" in source
