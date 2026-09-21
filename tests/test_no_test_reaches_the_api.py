"""The suite must never reach the Anthropic API.

Not a style rule: the key in `.env` is the owner's billed credential, funded
for the app's own photo reading. A test suite that picked it up would spend
their money every run, and `make check` runs on every commit and every deploy.

The hole this closes is specific. `from_env()` with no argument reads the real
environment *and* the real `.env`, and since the cloud tier landed its default
provider is "claude" -- so one bare call anywhere in the suite, now or later,
would be enough. `conftest.py` neutralises all three of its inputs before
anything imports config, the same way it forces the printer to `fake`.

Nothing here ever prints a key. The assertions compare booleans, because
pytest shows the compared values on failure and a failure here would otherwise
put the credential in the output -- which is the thing being prevented.
"""

from __future__ import annotations

from movingbox import config


class TestTheRealEnvironmentCannotReachTheSuite:
    def test_a_bare_from_env_finds_no_key(self):
        # `from_env()` -- no argument -- is the running service's own call.
        found = config.from_env().anthropic_api_key is not None
        assert not found, "a real API key reached the test suite"

    def test_a_bare_from_env_does_not_choose_the_cloud(self):
        assert config.from_env().vision_provider != "claude"

    def test_the_stub_is_what_it_chooses(self):
        assert config.from_env().vision_provider == "stub"


class TestTheGuardIsWhereItCannotBeForgotten:
    def test_conftest_sets_it_before_anything_imports_config(self):
        # Set at import time in conftest, not in a fixture: a fixture only
        # protects the tests that ask for it, and the point is the ones that
        # do not think to.
        source = (config.ROOT / "tests" / "conftest.py").read_text()
        assert 'os.environ["MOVING_VISION_PROVIDER"] = "stub"' in source
        assert "ANTHROPIC_API_KEY" in source
        assert "MOVING_ENV_FILE" in source
