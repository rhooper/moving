"""Plug-in providers: `provider = "package.module:factory"` for photos and summaries.

A vision plug-in takes the cloud tier's place, so everything the cloud tier
has -- the local model behind it, the budget, Settings saying when it is not
answering -- it has too. Nothing here reaches a network.
"""

from __future__ import annotations

import pytest

from movingbox import db, phrasing, providers, spend
from movingbox.config import Config, ConfigError, from_env, read_config_file
from movingbox.vision import base
from movingbox.vision.ollama import OllamaProvider
from movingbox.vision.stub import StubProvider

from . import fake_providers
from .test_spend import a_job, read

VISION = "tests.fake_providers:vision"
PHRASER = "tests.fake_providers:phraser"


def blank(**changes) -> Config:
    return Config(db_path="/tmp/x", photo_dir="/tmp/x", label_preview_dir="/tmp/x").replace(
        **changes
    )


def keyed(value: str | None = "k", **changes) -> Config:
    """A Config whose key lookup knows one name, as from_env's would."""
    known = {fake_providers.KEY: value} if value else {}
    return blank(key_lookup=known.get, **changes)


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


class TestNamingOne:
    def test_a_spec_names_the_factory(self):
        assert providers.load(VISION) is fake_providers.vision

    def test_a_plug_in_can_live_in_the_plugins_directory(self, tmp_path, monkeypatch):
        # The checkout's plugins/ (git-ignored) is searched, since `uv run`
        # manages the environment and a module dropped in there is simplest.
        (tmp_path / "dropped_in.py").write_text("def vision(config, key):\n    return None\n")
        monkeypatch.setattr("sys.path", list(__import__("sys").path))

        factory = providers.load("dropped_in:vision", search=(tmp_path,))

        assert factory(blank(), blank().key_lookup) is None

    @pytest.mark.parametrize("name", ["claude", "ollama", "stub"])
    def test_the_built_in_names_are_not_specs(self, name):
        assert not providers.is_spec(name)

    @pytest.mark.parametrize(
        "spec, says",
        [
            ("tests.nope:vision", "cannot import tests.nope"),
            ("tests.fake_providers:absent", "has no absent"),
            ("tests.fake_providers:not_callable", "is not callable"),
            ("tests.fake_providers:", "package.module:factory"),
            (":vision", "package.module:factory"),
            ("a:b:c", "package.module:factory"),
        ],
    )
    def test_a_bad_one_says_what_is_wrong(self, spec, says):
        with pytest.raises(ConfigError, match=says):
            providers.load(spec)

    @pytest.mark.parametrize("field", ["vision_provider", "summary_provider"])
    def test_a_bad_one_stops_startup(self, field):
        with pytest.raises(ConfigError, match="tests.nope"):
            providers.check(blank(**{field: "tests.nope:vision"}))

    def test_a_misspelt_built_in_stops_startup(self):
        # It used to fall through to local-only reading without a word.
        with pytest.raises(ConfigError, match="cluade"):
            providers.check(blank(vision_provider="cluade"))

    def test_the_summary_provider_has_one_built_in(self):
        with pytest.raises(ConfigError, match="claude"):
            providers.check(blank(summary_provider="claude"))

    @pytest.mark.parametrize("name", ["claude", "ollama", "stub", VISION])
    def test_every_good_one_starts(self, name):
        providers.check(blank(vision_provider=name, summary_provider=PHRASER))

    def test_the_app_checks_at_startup(self):
        from movingbox.api.app import create_app

        with pytest.raises(ConfigError, match="tests.nope"):
            create_app(blank(vision_provider="tests.nope:vision"))


class TestReadingPhotos:
    def test_the_plug_in_is_the_cloud_tier_with_the_local_model_behind_it(self):
        config = keyed(vision_provider=VISION)

        built = providers.vision(config)

        assert isinstance(built.cloud, fake_providers.Reader)
        assert built.cloud.key == "k"
        assert isinstance(built.local, OllamaProvider)
        assert built.fallbacks == config.vision_fallbacks()

    def test_no_key_leaves_the_pair_with_nothing_to_try(self):
        assert providers.vision(keyed(None, vision_provider=VISION)).cloud is None

    def test_a_config_built_by_hand_finds_no_key_even_in_the_environment(self, monkeypatch):
        # The suite's guarantee: a Config built directly can reach no service.
        monkeypatch.setenv(fake_providers.KEY, "from-the-shell")

        assert providers.vision(blank(vision_provider=VISION)).cloud is None

    def test_what_it_charged_is_what_the_job_records(self):
        built = providers.vision(keyed(vision_provider=VISION))

        found = built.draft([b"jpeg"], model="big-model")

        assert found.summary == "a kettle"
        assert built.last == base.Reading("fakecloud", "big-model", 100, 20, 0.02)

    def test_a_refusal_falls_back_and_is_still_charged(self):
        built = providers.vision(keyed(fake_providers.REFUSED, vision_provider=VISION))
        built.local = StubProvider(0)

        found = built.draft([b"jpeg"], model="big-model")

        assert found.summary == "kettle, mugs and a toaster"
        assert built.last.provider == "stub"
        assert built.last.cost_usd == 0.01

    def test_a_job_names_the_cloud_models(self):
        config = keyed(
            vision_provider=VISION, vision_cloud_model="big", vision_cloud_detail_model="bigger"
        )

        assert config.cloud_tier
        assert config.vision_model_for() == "big"
        assert config.vision_model_for(detail=True) == "bigger"

    @pytest.mark.parametrize("name", ["ollama", "stub"])
    def test_the_local_ones_are_not_a_cloud_tier(self, name):
        assert not blank(vision_provider=name).cloud_tier


class TestItsSpending:
    def test_the_cap_withdraws_it(self, conn, config):
        spend.record(conn, a_job(conn, provider="fakecloud"), read(provider="fakecloud", cost=30.0))

        assert spend.over_cap(conn, config.replace(vision_provider=VISION, vision_budget_usd=30.0))

    def test_its_reads_are_not_counted_as_local(self, conn, config):
        spend.record(conn, a_job(conn, provider="fakecloud"), read(provider="fakecloud"))
        spend.record(conn, a_job(conn, provider="ollama"), read(provider="ollama", cost=0.0))

        assert spend.recent_reads(conn, config.replace(vision_provider=VISION)) == (2, 1)

    def test_settings_is_told_there_is_a_cloud_tier_and_whether_it_has_a_key(self, conn, config):
        with_key = spend.status(
            conn, config.replace(vision_provider=VISION, key_lookup={fake_providers.KEY: "k"}.get)
        )
        without = spend.status(conn, config.replace(vision_provider=VISION))

        assert with_key["cloud"] and with_key["key"] and not with_key["reading_locally"]
        assert without["cloud"] and not without["key"] and without["reading_locally"]

    def test_claude_is_still_told_by_its_own_key(self, conn, config):
        told = spend.status(
            conn, config.replace(vision_provider="claude", anthropic_api_key="sk-ant-x")
        )

        assert told["cloud"] and told["key"]
        assert not spend.status(conn, config.replace(vision_provider="ollama"))["cloud"]


class TestPhrasingSummaries:
    def test_the_plug_in_phrases(self):
        phraser = providers.phraser(keyed(phrase_summaries=True, summary_provider=PHRASER))

        line = phraser.phrase([{"name": "kettle", "qty": 1}], model="small-model")

        assert line == "Kitchen things, by small-model."
        assert phraser.prompts == [phrasing.prompt([{"name": "kettle", "qty": 1}])]

    def test_no_key_assembles_the_line_instead(self):
        assert (
            providers.phraser(keyed(None, phrase_summaries=True, summary_provider=PHRASER)) is None
        )

    def test_phrasing_off_is_still_off(self):
        assert providers.phraser(keyed(phrase_summaries=False, summary_provider=PHRASER)) is None

    def test_the_stub_still_wins_for_ui_work(self):
        stubbed = keyed(phrase_summaries=True, summary_provider=PHRASER, vision_provider="stub")

        assert providers.phraser(stubbed).name == "stub"

    def test_ollama_is_the_default(self):
        assert providers.phraser(blank(phrase_summaries=True)).name == "ollama"

    def test_nothing_warms_a_model_ollama_does_not_serve(self):
        pinged = []
        config = keyed(phrase_summaries=True, summary_provider=PHRASER)

        phrasing.Warmer(config, ping=pinged.append).run()

        assert pinged == []


class TestThePromptHelpers:
    def test_the_prompt_is_the_one_ollama_is_sent(self):
        contents = [{"name": "kettle", "qty": 2}]
        sent = phrasing.build_request("m", contents)["messages"]

        assert phrasing.prompt(contents) == (sent[0]["content"], sent[1]["content"])

    def test_finishing_tidies_what_the_model_said(self):
        assert (
            phrasing.finish('Sure! {"summary": "  Tools   and\\nfixings  "}') == "Tools and fixings"
        )

    @pytest.mark.parametrize("reply", ["", "no json here", '{"summary": ""}'])
    def test_finishing_nothing_usable_raises(self, reply):
        with pytest.raises(phrasing.Unusable):
            phrasing.finish(reply)


class TestKeys:
    def test_the_environment_first(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"{fake_providers.KEY}=from-file\n")

        config = from_env({fake_providers.KEY: "from-env", "MOVING_ENV_FILE": str(env_file)})

        assert config.key_lookup(fake_providers.KEY) == "from-env"

    def test_then_the_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f'{fake_providers.KEY}="from-file"\n')
        env_file.chmod(0o600)

        config = from_env({"MOVING_ENV_FILE": str(env_file)})

        assert config.key_lookup(fake_providers.KEY) == "from-file"

    def test_then_none(self):
        assert from_env({"MOVING_CONFIG": "/nonexistent.toml"}).key_lookup("ANY_KEY") is None

    def test_an_empty_value_is_no_key(self):
        assert from_env({fake_providers.KEY: "  "}).key_lookup(fake_providers.KEY) is None

    def test_the_lookup_is_never_shown(self):
        config = from_env({fake_providers.KEY: "sk-secret"})

        assert "sk-secret" not in repr(config)
        assert config == config.replace()  # and never compared


class TestTheSettingsFile:
    def test_the_summary_provider_is_a_setting(self, tmp_path):
        path = tmp_path / "moving.toml"
        path.write_text(f'[summary]\nprovider = "{PHRASER}"\n')

        assert read_config_file(path) == {"MOVING_SUMMARY_PROVIDER": PHRASER}
        assert from_env({"MOVING_CONFIG": str(path)}).summary_provider == PHRASER

    def test_its_default_is_ollama(self):
        assert from_env({"MOVING_CONFIG": "/nonexistent.toml"}).summary_provider == "ollama"
