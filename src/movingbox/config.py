"""Runtime configuration, read from the environment with sane local defaults."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path

from . import secrets

# Repo root: src/movingbox/config.py -> movingbox -> src -> root
ROOT = Path(__file__).resolve().parents[2]

# Baked into every printed QR code. Must be HTTPS (Tailscale's certificate):
# the camera APIs are secure-context only, so a LAN address cannot scan.
DEFAULT_BASE_URL = "https://moving.example.ts.net"


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
    #: "ollama", "claude" (cloud first, local behind it), or "stub" (a canned
    #: draft after a delay, for checking the UI).
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

    def replace(self, **changes) -> Config:
        return dataclasses.replace(self, **changes)

    def vision_model_for(self, *, detail: bool = False) -> str:
        """Which model a job names when it is queued: the one tried first.

        Only "claude" names cloud models: the stub recognises a closer look by
        `vision_detail_model`.
        """
        if self.vision_provider == "claude":
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
    )
