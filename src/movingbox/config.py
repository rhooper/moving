"""Runtime configuration: the environment, then `moving.toml`, then defaults.

Every setting has a `MOVING_*` variable, and most also have a key in
`moving.toml` (see `moving.example.toml`). A variable beats the file, so a
throwaway server or a test can override one setting without editing it.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from . import secrets

# Repo root: src/movingbox/config.py -> movingbox -> src -> root
ROOT = Path(__file__).resolve().parents[2]

# Baked into every printed QR code. Must be HTTPS (Tailscale's certificate):
# the camera APIs are secure-context only, so a LAN address cannot scan.
# The default is a placeholder (.example is reserved): a real printer refuses
# to print with it, since a label pointing nowhere cannot be taken back.
DEFAULT_BASE_URL = "https://moving.example"


class ConfigError(ValueError):
    """`moving.toml` says something the app cannot use."""


def is_spec(name: str) -> bool:
    """Whether a provider setting names a plug-in (`package.module:factory`)
    rather than a built-in one. `providers.load` says whether it is a good one.
    """
    return ":" in name


def no_keys(name: str) -> str | None:
    """The key lookup of a Config built directly: every test's, so none finds a key."""
    return None


#: `moving.toml` keys, by section, and the variable each one stands for.
#: The file is read into these names, so there is one parser for both.
FILE_KEYS: dict[str, dict[str, tuple[str, type]]] = {
    "server": {
        "base_url": ("MOVING_BASE_URL", str),
        "api_key": ("MOVING_API_KEY", str),
    },
    "storage": {
        "db_path": ("MOVING_DB_PATH", Path),
        "photo_dir": ("MOVING_PHOTO_DIR", Path),
        "label_preview_dir": ("MOVING_LABEL_PREVIEW_DIR", Path),
        "backup_dir": ("MOVING_BACKUP_DIR", Path),
    },
    "printer": {
        "backend": ("MOVING_PRINTER_BACKEND", str),
        "model": ("MOVING_PRINTER_MODEL", str),
        "queue": ("MOVING_PRINTER_QUEUE", str),
        "label": ("MOVING_LABEL_ID", str),
        "orientation": ("MOVING_LABEL_ORIENTATION", str),
    },
    "vision": {
        "provider": ("MOVING_VISION_PROVIDER", str),
        "auto_analyse": ("MOVING_AUTO_ANALYSE", bool),
        "budget_usd": ("MOVING_VISION_BUDGET_USD", float),
        "cloud_model": ("MOVING_VISION_CLOUD_MODEL", str),
        "cloud_detail_model": ("MOVING_VISION_CLOUD_DETAIL_MODEL", str),
        "local_model": ("MOVING_VISION_MODEL", str),
        "local_detail_model": ("MOVING_VISION_DETAIL_MODEL", str),
        "stub_seconds": ("MOVING_VISION_STUB_SECONDS", float),
    },
    "summary": {
        "phrase": ("MOVING_PHRASE_SUMMARIES", bool),
        "provider": ("MOVING_SUMMARY_PROVIDER", str),
        "model": ("MOVING_SUMMARY_MODEL", str),
    },
    "ollama": {
        "url": ("MOVING_OLLAMA_URL", str),
    },
}


def config_file_path(e: Mapping[str, str], *, real: bool) -> Path | None:
    """Which `moving.toml` to read, if any: the same rule as `env_file_path`.

    `MOVING_CONFIG` names one; otherwise this checkout's own. A dict passed to
    `from_env` reads no file unless it names one, so no test finds the real
    database path or printer in it.
    """
    named = e.get("MOVING_CONFIG")
    if named:
        return Path(named)
    return ROOT / "moving.toml" if real else None


def read_config_file(path: Path) -> dict[str, str]:
    """`moving.toml` as the `MOVING_*` variables it sets. An absent file sets none.

    Strict, because a misspelt key silently ignored is a setting that never
    took effect: an unknown section or key, or a value of the wrong type, is a
    `ConfigError` naming it. Relative paths are relative to the file.
    """
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as bad:
        raise ConfigError(f"{path}: {bad}") from None

    found: dict[str, str] = {}
    for section, values in data.items():
        if section not in FILE_KEYS:
            raise ConfigError(f"{path}: unknown section [{section}]")
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: {section} must be a [section]")
        for key, value in values.items():
            where = f"{path}: [{section}] {key}"
            if key not in FILE_KEYS[section]:
                raise ConfigError(f"{where} is not a setting")
            name, kind = FILE_KEYS[section][key]
            if kind is bool:
                if not isinstance(value, bool):
                    raise ConfigError(f"{where} must be true or false")
                found[name] = "1" if value else "0"
            elif kind is float:
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise ConfigError(f"{where} must be a number")
                found[name] = str(value)
            else:
                if not isinstance(value, str):
                    raise ConfigError(f"{where} must be a string")
                if kind is Path and value:
                    value = str(path.parent / Path(value).expanduser())
                found[name] = value
    return found


@dataclasses.dataclass(frozen=True)
class Config:
    db_path: Path
    photo_dir: Path
    label_preview_dir: Path
    backup_dir: Path | None = None  # defaults to db_path's parent / "backups"
    base_url: str = DEFAULT_BASE_URL
    # repr=False on both keys, so a traceback or log line never carries a secret.
    api_key: str | None = dataclasses.field(default=None, repr=False)
    # `fake` writes a PNG preview instead of printing.
    printer_backend: str = "fake"
    printer_model: str = "QL-800"
    printer_queue: str | None = None  # CUPS queue name, for the cups_raw backend
    label_id: str = "62"  # 62 mm continuous DK-2205; 696 printable dots
    #: "landscape" is a fixed 990 x 696 px (3.3 in along 62 mm tape) with no
    #: item list; "portrait" is the older form, cut to its content.
    label_orientation: str = "landscape"
    ollama_url: str = "http://localhost:11434"
    #: Reads every uploaded photo. Keep the "-instruct": the bare qwen3-vl tags
    #: are *thinking* checkpoints, several times slower and no more accurate.
    vision_model: str = "qwen3-vl:4b-instruct"
    #: The closer look, run only when asked for.
    vision_detail_model: str = "qwen3-vl:8b-instruct"
    #: Phrases the "From contents" summary.
    summary_model: str = "qwen2.5:7b"
    #: The cloud tier, tried first when `vision_provider` is "claude".
    vision_cloud_model: str = "claude-sonnet-5"
    #: The closer look, run only when asked for.
    vision_cloud_detail_model: str = "claude-opus-5"
    #: From ANTHROPIC_API_KEY, else the project root's .env (see secrets.py).
    #: None is a working configuration: the hybrid reads locally.
    anthropic_api_key: str | None = dataclasses.field(default=None, repr=False)
    #: Dollars, cumulative over every cloud job. Past it, photos are read locally.
    vision_budget_usd: float = 30.0
    #: "ollama", "claude" (cloud first, local behind it), "stub" (a canned
    #: draft after a delay, for checking the UI), or a plug-in's
    #: `package.module:factory`, which takes Claude's place as the cloud tier.
    #:
    #: This default and the two flags below keep a Config built directly -- as
    #: every test builds one -- away from the API and from any model; from_env
    #: is what turns them on.
    vision_provider: str = "ollama"
    vision_stub_seconds: float = 3.0
    #: Whether the app runs the background photo-analysis thread.
    auto_analyse: bool = False
    #: Whether "From contents" asks a model to phrase the summary, and the app
    #: keeps that model warm. Off, the button assembles the line itself.
    phrase_summaries: bool = False
    #: Who phrases it: "ollama", or a plug-in's `package.module:factory`.
    summary_provider: str = "ollama"
    #: A plug-in's way to its API key: a name in, the value or None out. From
    #: the environment, then the `.env` file (see from_env); never shown, and
    #: never compared, so two Configs differing only by it are equal.
    key_lookup: Callable[[str], str | None] = dataclasses.field(
        default=no_keys, repr=False, compare=False
    )

    @property
    def cloud_tier(self) -> bool:
        """Whether photos are offered to a cloud provider first: Claude or a plug-in."""
        return self.vision_provider == "claude" or is_spec(self.vision_provider)

    def replace(self, **changes) -> Config:
        return dataclasses.replace(self, **changes)

    def unprintable(self) -> str | None:
        """Why labels must not go to tape with this configuration, or None.

        Only a real printer is refused: `fake` writes a preview, which is how
        a fresh checkout and the tests use it.
        """
        if self.printer_backend != "fake" and self.base_url == DEFAULT_BASE_URL:
            return (
                "base_url is still the placeholder, and every label's QR code would "
                "point at it. Set [server] base_url in moving.toml (or MOVING_BASE_URL)."
            )
        return None

    def vision_model_for(self, *, detail: bool = False) -> str:
        """Which model a job names when it is queued: the one tried first.

        Only a cloud tier names cloud models: the stub recognises a closer look
        by `vision_detail_model`.
        """
        if self.cloud_tier:
            return self.vision_cloud_detail_model if detail else self.vision_cloud_model
        return self.vision_detail_model if detail else self.vision_model

    def vision_fallbacks(self) -> dict[str, str]:
        """Each cloud model and the local model that stands in for it."""
        return {
            self.vision_cloud_model: self.vision_model,
            self.vision_cloud_detail_model: self.vision_detail_model,
        }


def env_file_path(e: Mapping[str, str], *, real: bool) -> Path | None:
    """Which `.env` to read, if any.

    `MOVING_ENV_FILE` names one; otherwise this checkout's own (a worktree has
    its own). `from_env({...})` with a dict reads no file unless it names one,
    so the test suite can never find a real key.
    """
    named = e.get("MOVING_ENV_FILE")
    if named:
        return Path(named)
    return ROOT / ".env" if real else None


def from_env(
    env: dict[str, str] | None = None,
    *,
    env_file: Path | None = None,
) -> Config:
    """Configuration for a running process."""
    real = env is None
    e = os.environ if real else env
    if env_file is None:
        env_file = env_file_path(e, real=real)
    config_file = config_file_path(e, real=real)
    if config_file is not None:
        # The environment wins over the file, setting by setting.
        e = {**read_config_file(config_file), **e}
    var = ROOT / "var"
    return Config(
        db_path=Path(e.get("MOVING_DB_PATH", var / "moving.db")),
        photo_dir=Path(e.get("MOVING_PHOTO_DIR", var / "photos")),
        label_preview_dir=Path(e.get("MOVING_LABEL_PREVIEW_DIR", var / "labels" / "preview")),
        backup_dir=Path(e.get("MOVING_BACKUP_DIR", var / "backups")),
        base_url=e.get("MOVING_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        api_key=e.get("MOVING_API_KEY") or None,
        printer_backend=e.get("MOVING_PRINTER_BACKEND", "fake"),
        printer_model=e.get("MOVING_PRINTER_MODEL", "QL-800"),
        printer_queue=e.get("MOVING_PRINTER_QUEUE") or None,
        label_id=e.get("MOVING_LABEL_ID", "62"),
        label_orientation=e.get("MOVING_LABEL_ORIENTATION", "landscape"),
        ollama_url=e.get("MOVING_OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        vision_model=e.get("MOVING_VISION_MODEL", "qwen3-vl:4b-instruct"),
        vision_detail_model=e.get("MOVING_VISION_DETAIL_MODEL", "qwen3-vl:8b-instruct"),
        summary_model=e.get("MOVING_SUMMARY_MODEL", "qwen2.5:7b"),
        vision_cloud_model=e.get("MOVING_VISION_CLOUD_MODEL", "claude-sonnet-5"),
        vision_cloud_detail_model=e.get("MOVING_VISION_CLOUD_DETAIL_MODEL", "claude-opus-5"),
        anthropic_api_key=secrets.anthropic_api_key(e, env_file=env_file),
        vision_budget_usd=float(e.get("MOVING_VISION_BUDGET_USD", "30")),
        vision_provider=e.get("MOVING_VISION_PROVIDER", "claude"),
        vision_stub_seconds=float(e.get("MOVING_VISION_STUB_SECONDS", "3")),
        auto_analyse=e.get("MOVING_AUTO_ANALYSE", "1") not in ("0", "false", "no", "off"),
        phrase_summaries=e.get("MOVING_PHRASE_SUMMARIES", "1") not in ("0", "false", "no", "off"),
        summary_provider=e.get("MOVING_SUMMARY_PROVIDER", "ollama"),
        key_lookup=secrets.lookup(e, env_file=env_file),
    )
