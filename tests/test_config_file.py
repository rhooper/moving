"""moving.toml: the file a person edits instead of a list of variables."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from movingbox.config import (
    DEFAULT_BASE_URL,
    FILE_KEYS,
    ROOT,
    Config,
    ConfigError,
    config_file_path,
    from_env,
    read_config_file,
)


def a_config_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "moving.toml"
    path.write_text(text)
    return path


def configured(tmp_path: Path, text: str, **env: str) -> Config:
    return from_env({"MOVING_CONFIG": str(a_config_file(tmp_path, text)), **env})


class TestReading:
    def test_the_llm_engine_is_set_from_the_file(self, tmp_path):
        config = configured(
            tmp_path,
            """
            [vision]
            provider = "ollama"
            local_model = "qwen3-vl:8b-instruct"
            budget_usd = 5
            auto_analyse = false

            [summary]
            phrase = false
            model = "llama3.2:3b"

            [ollama]
            url = "http://gpu-box:11434/"
            """,
        )

        assert config.vision_provider == "ollama"
        assert config.vision_model == "qwen3-vl:8b-instruct"
        assert config.vision_budget_usd == 5.0
        assert config.auto_analyse is False
        assert config.phrase_summaries is False
        assert config.summary_model == "llama3.2:3b"
        assert config.ollama_url == "http://gpu-box:11434"

    def test_the_environment_wins_over_the_file(self, tmp_path):
        config = configured(
            tmp_path,
            '[vision]\nprovider = "claude"\n',
            MOVING_VISION_PROVIDER="stub",
        )

        assert config.vision_provider == "stub"

    def test_relative_paths_are_relative_to_the_file(self, tmp_path):
        config = configured(tmp_path, '[storage]\ndb_path = "data/boxes.db"\n')

        assert config.db_path == tmp_path / "data" / "boxes.db"

    def test_an_absent_file_is_the_defaults(self, tmp_path):
        config = from_env({"MOVING_CONFIG": str(tmp_path / "missing.toml")})

        assert config.base_url == DEFAULT_BASE_URL
        assert config.vision_provider == "claude"

    def test_a_dict_of_variables_reads_no_file_unless_it_names_one(self):
        assert config_file_path({}, real=False) is None
        assert config_file_path({}, real=True) == ROOT / "moving.toml"


class TestMistakesAreLoud:
    # A misspelt key silently ignored is a setting that never took effect.
    @pytest.mark.parametrize(
        "text, says",
        [
            ("[visoin]\nprovider = 'x'\n", "unknown section [visoin]"),
            ("[vision]\nprovder = 'x'\n", "[vision] provder is not a setting"),
            ("[vision]\nauto_analyse = 'no'\n", "must be true or false"),
            ("[vision]\nbudget_usd = '30'\n", "must be a number"),
            ("[vision]\nbudget_usd = true\n", "must be a number"),
            ("[server]\nbase_url = 1\n", "must be a string"),
            ("vision = 1\n", "must be a [section]"),
            ("[vision\n", "moving.toml"),
        ],
    )
    def test_it_names_what_is_wrong(self, tmp_path, text, says):
        with pytest.raises(ConfigError, match=re.escape(says)):
            read_config_file(a_config_file(tmp_path, text))

    def test_the_api_key_has_no_key_in_the_file(self, tmp_path):
        # It belongs in .env, so moving.toml can be shared without a secret.
        with pytest.raises(ConfigError, match="not a setting"):
            read_config_file(a_config_file(tmp_path, "[vision]\nanthropic_api_key = 'x'\n"))


class TestTheExample:
    def test_every_setting_is_in_the_example(self):
        text = (ROOT / "moving.example.toml").read_text()
        for section, keys in FILE_KEYS.items():
            assert f"[{section}]" in text
            for key, (variable, _) in keys.items():
                assert re.search(rf"^# {key} = ", text, re.M), key
                assert variable in text, variable

    def test_uncommented_it_is_a_valid_file(self, tmp_path):
        text = (ROOT / "moving.example.toml").read_text()
        live = re.sub(r"^# (\w+ = )", r"\1", text, flags=re.M)

        assert len(read_config_file(a_config_file(tmp_path, live))) == sum(
            len(keys) for keys in FILE_KEYS.values()
        )

    def test_its_defaults_are_the_real_defaults(self, tmp_path):
        text = (ROOT / "moving.example.toml").read_text()
        live = re.sub(r"^# (\w+ = )", r"\1", text, flags=re.M)
        live = re.sub(r'^(queue|api_key) = ""', r"# \1", live, flags=re.M)

        assert configured(tmp_path, live) == from_env(
            {"MOVING_CONFIG": str(tmp_path / "none.toml")}
        ).replace(
            db_path=tmp_path / "var" / "moving.db",
            photo_dir=tmp_path / "var" / "photos",
            label_preview_dir=tmp_path / "var" / "labels" / "preview",
            backup_dir=tmp_path / "var" / "backups",
        )


def example_defaults() -> dict[tuple[str, str], str]:
    """(section, key) -> the default `moving.example.toml` shows, unquoted."""
    found, section = {}, None
    for line in (ROOT / "moving.example.toml").read_text().splitlines():
        if heading := re.match(r"^\[(\w+)\]", line):
            section = heading[1]
        elif setting := re.match(r'^# (\w+) = ("[^"]*"|\S+)', line):
            found[(section, setting[1])] = setting[2].strip('"')
    return found


class TestTheReference:
    """docs/configuration.md: every setting, with the same default as the example."""

    text = (ROOT / "docs" / "configuration.md").read_text()

    def rows(self) -> dict[tuple[str, str], tuple[str, str]]:
        found, section = {}, None
        for line in self.text.splitlines():
            if heading := re.match(r"^### `\[(\w+)\]`", line):
                section = heading[1]
            elif line.startswith("## "):
                section = None
            elif section and (row := re.match(r"^\| `(\w+)` \| `(\w+)` \| ([^|]+) \|", line)):
                found[(section, row[1])] = (row[2], row[3].strip())
        return found

    def test_every_setting_has_a_row_under_its_section(self):
        expected = {
            (section, key): variable
            for section, keys in FILE_KEYS.items()
            for key, (variable, _) in keys.items()
        }
        assert {where: variable for where, (variable, _) in self.rows().items()} == expected

    def test_each_default_is_the_examples(self):
        example = example_defaults()
        for where, (_, default) in self.rows().items():
            shown = "" if default == "unset" else default.strip("`")
            assert shown == example[where], where

    def test_every_variable_the_code_reads_is_documented(self):
        used = set()
        for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*")]:
            if path.is_file():
                used |= set(re.findall(r"\bMOVING_[A-Z_]+[A-Z]\b", path.read_text(errors="ignore")))
        assert used, "found no variables at all"
        assert sorted(v for v in used if f"`{v}`" not in self.text) == []


class TestThePlaceholderAddress:
    def blank(self, **changes) -> Config:
        return Config(db_path="/tmp/x", photo_dir="/tmp/x", label_preview_dir="/tmp/x").replace(
            **changes
        )

    def test_a_real_printer_refuses_it(self):
        assert "base_url" in self.blank(printer_backend="brother_ql").unprintable()

    def test_the_fake_printer_does_not_mind(self):
        assert self.blank(printer_backend="fake").unprintable() is None

    def test_a_real_address_prints(self):
        config = self.blank(printer_backend="brother_ql", base_url="https://box.tail.ts.net")

        assert config.unprintable() is None
