"""Where the Anthropic API key comes from, and where it must never go.

Nothing here touches the real keychain: the lookup is injected, and the one
test that exercises the `security` call fakes the subprocess. A test that read
the machine's keychain would be a test that could spend money.
"""

import dataclasses
import subprocess
from types import SimpleNamespace

from movingbox import secrets
from movingbox.config import Config, from_env


def ran(stdout="", returncode=0):
    """A stand-in for subprocess.run that remembers how it was called."""

    def run(argv, **kwargs):
        run.argv, run.kwargs = argv, kwargs
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    return run


class TestWhereTheKeyComesFrom:
    def test_the_environment_wins(self):
        called = []

        key = secrets.anthropic_api_key(
            {"ANTHROPIC_API_KEY": "sk-ant-from-env"}, lookup=lambda: called.append(1)
        )

        assert key == "sk-ant-from-env"
        assert called == []  # the keychain is not even consulted

    def test_an_empty_variable_is_not_a_key(self):
        key = secrets.anthropic_api_key({"ANTHROPIC_API_KEY": "  "}, lookup=lambda: "sk-ant-kc")

        assert key == "sk-ant-kc"

    def test_the_keychain_is_next(self):
        assert secrets.anthropic_api_key({}, lookup=lambda: "sk-ant-kc") == "sk-ant-kc"

    def test_no_key_anywhere_is_not_an_error(self):
        # The hybrid falls back to the local model, so an absent key degrades
        # the reading rather than breaking the app. Someone who restarts the
        # service before authenticating gets working local analysis.
        assert secrets.anthropic_api_key({}, lookup=lambda: None) is None


class TestTheKeychainCall:
    def test_it_asks_for_the_documented_item(self):
        run = ran(stdout="sk-ant-stored\n")

        assert secrets.from_keychain(run=run) == "sk-ant-stored"
        assert run.argv == [
            secrets.SECURITY,
            "find-generic-password",
            "-s",
            secrets.SERVICE,
            "-a",
            secrets.ACCOUNT,
            "-w",
        ]

    def test_it_cannot_hang_the_service(self):
        # `security` can prompt for keychain access, and this runs under
        # launchd where nobody is there to answer.
        assert run_kwargs_of(ran(stdout="x"))["timeout"] == secrets.TIMEOUT

    def test_no_such_item_is_no_key(self):
        assert secrets.from_keychain(run=ran(returncode=44)) is None

    def test_a_prompt_that_never_gets_answered_is_no_key(self):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        assert secrets.from_keychain(run=run) is None

    def test_no_security_binary_is_no_key(self):
        def run(argv, **kwargs):
            raise FileNotFoundError(secrets.SECURITY)

        assert secrets.from_keychain(run=run) is None

    def test_an_empty_item_is_no_key(self):
        assert secrets.from_keychain(run=ran(stdout="\n")) is None


def run_kwargs_of(run):
    secrets.from_keychain(run=run)
    return run.kwargs


class TestTheKeyStaysOutOfSight:
    def test_it_is_not_in_a_config_s_repr(self):
        # A traceback that renders a Config, or a stray log line, would
        # otherwise put the key in var/log/moving.err.log for good.
        config = Config(
            db_path="/tmp/x",
            photo_dir="/tmp/x",
            label_preview_dir="/tmp/x",
            anthropic_api_key="sk-ant-secret-value",
            api_key="moving-secret",
        )

        assert "sk-ant-secret-value" not in repr(config)
        assert "moving-secret" not in repr(config)

    def test_it_survives_a_replace(self):
        config = Config(
            db_path="/tmp/x",
            photo_dir="/tmp/x",
            label_preview_dir="/tmp/x",
            anthropic_api_key="sk-ant-secret-value",
        )

        assert config.replace(base_url="https://x").anthropic_api_key == "sk-ant-secret-value"

    def test_a_config_built_by_hand_has_no_key(self):
        # Every test builds one directly. None of them can reach the API.
        blank = Config(db_path="/tmp/x", photo_dir="/tmp/x", label_preview_dir="/tmp/x")

        assert blank.anthropic_api_key is None
        assert blank.vision_provider == "ollama"
        assert dataclasses.fields(Config)  # the dataclass, not a namedtuple


class TestFromEnv:
    def test_a_dict_of_variables_never_reaches_the_machine_s_keychain(self):
        # from_env(dict) is what tests and scripts use. Only from_env() -- the
        # real environment, the running service -- asks the keychain.
        config = from_env({"MOVING_DB_PATH": "/tmp/x"})

        assert config.anthropic_api_key is None

    def test_the_lookup_can_be_supplied(self):
        config = from_env({}, api_key_lookup=lambda: "sk-ant-injected")

        assert config.anthropic_api_key == "sk-ant-injected"

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
