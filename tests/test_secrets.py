"""Where the Anthropic API key comes from, and where it must never go.

Nothing here reads the real `.env`: every test either passes a dict of
variables (which reads no file at all) or points `MOVING_ENV_FILE` at a
fixture. A test that found a real key would be a test that could spend money.
"""

import logging

from movingbox import secrets
from movingbox.config import Config, from_env


def an_env_file(tmp_path, text, mode=0o600):
    path = tmp_path / ".env"
    path.write_text(text)
    path.chmod(mode)
    return path


class TestTheGrammar:
    def test_a_plain_assignment(self):
        assert secrets.parse("ANTHROPIC_API_KEY=sk-ant-plain") == {
            "ANTHROPIC_API_KEY": "sk-ant-plain"
        }

    def test_comments_and_blank_lines_are_not_settings(self):
        text = "# the key\n\n  # indented\nANTHROPIC_API_KEY=sk-ant-x\n"

        assert secrets.parse(text) == {"ANTHROPIC_API_KEY": "sk-ant-x"}

    def test_quotes_are_stripped(self):
        assert secrets.parse("A=\"one\"\nB='two'\n") == {"A": "one", "B": "two"}

    def test_a_lone_quote_is_part_of_the_value(self):
        assert secrets.parse('A="one') == {"A": '"one'}

    def test_surrounding_space_goes(self):
        assert secrets.parse("  ANTHROPIC_API_KEY =  sk-ant-x  ") == {
            "ANTHROPIC_API_KEY": "sk-ant-x"
        }

    def test_an_export_prefix_is_tolerated(self):
        # Because half of everyone pastes the line they exported by hand.
        assert secrets.parse("export ANTHROPIC_API_KEY=sk-ant-x") == {
            "ANTHROPIC_API_KEY": "sk-ant-x"
        }

    def test_a_line_that_is_not_a_setting_is_skipped_not_fatal(self):
        # Half a file of settings should not stop the app starting.
        assert secrets.parse("nonsense\nANTHROPIC_API_KEY=sk-ant-x\n=novalue\n") == {
            "ANTHROPIC_API_KEY": "sk-ant-x"
        }

    def test_other_peoples_settings_are_left_alone(self):
        # The file will grow. Nothing here has an opinion about the rest of it.
        assert secrets.parse("MOVING_BASE_URL=https://x\nANTHROPIC_API_KEY=sk-ant-x") == {
            "MOVING_BASE_URL": "https://x",
            "ANTHROPIC_API_KEY": "sk-ant-x",
        }


class TestReadingTheFile:
    def test_a_file_that_is_not_there_is_simply_no_settings(self, tmp_path):
        assert secrets.read(tmp_path / "nope.env") == {}

    def test_a_loose_file_mode_warns_and_still_works(self, tmp_path, caplog):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-x", mode=0o644)

        with caplog.at_level(logging.WARNING):
            found = secrets.read(path)

        assert found == {"ANTHROPIC_API_KEY": "sk-ant-x"}
        assert "chmod 600" in caplog.text

    def test_a_warning_never_quotes_the_file(self, tmp_path, caplog):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-secret-value", mode=0o644)

        with caplog.at_level(logging.WARNING):
            secrets.read(path)

        assert "sk-ant" not in caplog.text

    def test_a_private_file_says_nothing(self, tmp_path, caplog):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-x", mode=0o600)

        with caplog.at_level(logging.WARNING):
            secrets.read(path)

        assert caplog.text == ""


class TestWhereTheKeyComesFrom:
    def test_the_environment_wins(self, tmp_path):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-from-file")

        key = secrets.anthropic_api_key({"ANTHROPIC_API_KEY": "sk-ant-from-env"}, env_file=path)

        assert key == "sk-ant-from-env"

    def test_an_empty_variable_is_not_a_key(self, tmp_path):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-from-file")

        key = secrets.anthropic_api_key({"ANTHROPIC_API_KEY": "  "}, env_file=path)

        assert key == "sk-ant-from-file"

    def test_the_file_is_next(self, tmp_path):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-from-file")

        assert secrets.anthropic_api_key({}, env_file=path) == "sk-ant-from-file"

    def test_an_empty_line_in_the_file_is_not_a_key(self, tmp_path):
        # .env.example ships with exactly this: the name and no value.
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=\n")

        assert secrets.anthropic_api_key({}, env_file=path) is None

    def test_no_key_anywhere_is_not_an_error(self, tmp_path):
        # The hybrid falls back to the local model, so an absent key degrades
        # the reading rather than breaking the app. Somebody who starts the
        # service before putting the key in place gets local analysis.
        assert secrets.anthropic_api_key({}, env_file=tmp_path / "nope.env") is None
        assert secrets.anthropic_api_key({}, env_file=None) is None


class TestTheKeyStaysOutOfSight:
    def blank(self, **changes):
        return Config(db_path="/tmp/x", photo_dir="/tmp/x", label_preview_dir="/tmp/x").replace(
            **changes
        )

    def test_it_is_not_in_a_config_s_repr(self):
        # A traceback that renders a Config, or a stray log line, would
        # otherwise put the key in var/log/moving.err.log for good.
        config = self.blank(anthropic_api_key="sk-ant-secret-value", api_key="moving-secret")

        assert "sk-ant-secret-value" not in repr(config)
        assert "moving-secret" not in repr(config)

    def test_it_survives_a_replace(self):
        config = self.blank(anthropic_api_key="sk-ant-secret-value")

        assert config.replace(base_url="https://x").anthropic_api_key == "sk-ant-secret-value"

    def test_a_config_built_by_hand_has_no_key(self):
        # Every test builds one directly. None of them can reach the API.
        assert self.blank().anthropic_api_key is None
        assert self.blank().vision_provider == "ollama"


class TestFromEnv:
    def test_a_dict_of_variables_reads_no_file_unless_it_names_one(self, tmp_path):
        # from_env(dict) is what tests and scripts use, and a dict is not this
        # machine. Only from_env() -- the real environment, the running
        # service -- falls back to the project root's own .env.
        assert from_env({"MOVING_DB_PATH": "/tmp/x"}).anthropic_api_key is None

    def test_a_named_file_is_read(self, tmp_path):
        path = an_env_file(tmp_path, "ANTHROPIC_API_KEY=sk-ant-fixture")

        assert from_env({"MOVING_ENV_FILE": str(path)}).anthropic_api_key == "sk-ant-fixture"

    def test_the_default_is_this_checkout_s_own_env(self):
        from movingbox.config import ROOT, env_file_path

        # Per-checkout on purpose: a dev server in a worktree must not quietly
        # pick up the main checkout's key.
        assert env_file_path({}, real=True) == ROOT / ".env"

    def test_the_running_service_reads_the_cloud_provider(self):
        assert from_env({}).vision_provider == "claude"

    def test_the_provider_can_still_be_pinned_local(self):
        assert from_env({"MOVING_VISION_PROVIDER": "ollama"}).vision_provider == "ollama"


class TestWhichModelIsTriedFirst:
    def blank(self, **changes):
        return Config(db_path="/tmp/x", photo_dir="/tmp/x", label_preview_dir="/tmp/x").replace(
            **changes
        )

    def test_the_cloud_tiers_when_the_cloud_is_configured(self):
        config = self.blank(vision_provider="claude")

        assert config.vision_model_for(detail=False) == "claude-sonnet-5"
        assert config.vision_model_for(detail=True) == "claude-opus-5"

    def test_the_local_tiers_otherwise(self):
        config = self.blank(vision_provider="ollama")

        assert config.vision_model_for(detail=False) == "qwen3-vl:4b-instruct"
        assert config.vision_model_for(detail=True) == "qwen3-vl:8b-instruct"

    def test_the_stub_keeps_the_local_names(self):
        # StubProvider decides which draft to return by comparing the model it
        # is given against config.vision_detail_model. Naming a cloud model
        # here would silently stop the browser checks seeing a closer look.
        assert self.blank(vision_provider="stub").vision_model_for(detail=True) == (
            "qwen3-vl:8b-instruct"
        )

    def test_each_cloud_tier_names_its_local_stand_in(self):
        assert self.blank().vision_fallbacks() == {
            "claude-sonnet-5": "qwen3-vl:4b-instruct",
            "claude-opus-5": "qwen3-vl:8b-instruct",
        }


class TestWhatTheAppBuilds:
    """The guarantee that matters: no test can reach the API."""

    def blank(self, **changes):
        return Config(db_path="/tmp/x", photo_dir="/tmp/x", label_preview_dir="/tmp/x").replace(
            **changes
        )

    def test_a_config_built_by_hand_gets_the_local_provider(self):
        from movingbox.api.app import build_vision_provider
        from movingbox.vision.ollama import OllamaProvider

        assert isinstance(build_vision_provider(self.blank()), OllamaProvider)

    def test_the_cloud_provider_is_a_pair_with_the_local_one_behind_it(self):
        from movingbox.api.app import build_vision_provider
        from movingbox.vision.ollama import OllamaProvider

        built = build_vision_provider(
            self.blank(vision_provider="claude", anthropic_api_key="sk-ant-test")
        )

        assert built.cloud.name == "claude"
        assert isinstance(built.local, OllamaProvider)
        assert built.fallbacks == self.blank().vision_fallbacks()

    def test_no_key_leaves_the_pair_with_nothing_to_try(self):
        from movingbox.api.app import build_vision_provider

        built = build_vision_provider(self.blank(vision_provider="claude"))

        assert built.cloud is None  # local-only, and not an error

    def test_the_stub_is_still_the_stub(self):
        from movingbox.api.app import build_vision_provider
        from movingbox.vision.stub import StubProvider

        assert isinstance(build_vision_provider(self.blank(vision_provider="stub")), StubProvider)
