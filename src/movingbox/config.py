"""Runtime configuration, read from the environment with sane local defaults."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path

from . import secrets

# Repo root: src/movingbox/config.py -> movingbox -> src -> root
ROOT = Path(__file__).resolve().parents[2]

# Baked into every printed QR code. Tailscale issues a real certificate for this
# name, which is what makes the camera work at all -- getUserMedia and
# BarcodeDetector are secure-context only, so a plain LAN address cannot scan.
DEFAULT_BASE_URL = "https://moving.example.ts.net"


@dataclasses.dataclass(frozen=True)
class Config:
    db_path: Path
    photo_dir: Path
    label_preview_dir: Path
    backup_dir: Path | None = None  # defaults to db_path's parent / "backups"
    base_url: str = DEFAULT_BASE_URL
    # repr=False on both keys: a traceback that renders a Config, or one stray
    # log line, would otherwise put a secret in var/log/moving.err.log for good.
    api_key: str | None = dataclasses.field(default=None, repr=False)
    # `fake` writes a PNG preview instead of printing. Anything else needs the
    # QL-800 attached, so it is never the default.
    printer_backend: str = "fake"
    printer_model: str = "QL-800"
    printer_queue: str | None = None  # CUPS queue name, for the cups_raw backend
    label_id: str = "62"  # 62 mm continuous DK-2205; 696 printable dots
    #: "landscape" is a fixed 4 inches along the tape with room for the
    #: itemised contents; "portrait" is the older cut-to-content form.
    label_orientation: str = "landscape"
    ollama_url: str = "http://localhost:11434"
    #: Reads every uploaded photo. Measured on this machine on real photos
    #: (2026-09-18): 7.4 s median, no parse failures in 73 runs, and the most
    #: specific names of any model tried. The previous default, qwen3-vl:30b,
    #: is the *thinking* checkpoint: ~37 s a photo, ~30 s of it reasoning that
    #: did not make it more accurate. The "-instruct" matters -- the bare tags
    #: (qwen3-vl:4b, :8b, :30b) are all thinking checkpoints.
    vision_model: str = "qwen3-vl:4b-instruct"
    #: The closer look, run only when asked for: ~10 s, and the best of those
    #: tried at handwriting and brand names.
    vision_detail_model: str = "qwen3-vl:8b-instruct"
    #: The cloud tier, tried first when `vision_provider` is "claude". Chosen
    #: on merit rather than price: the local 4b averages 19.6 s a photo here
    #: with a 92 s worst case and 5 errors in 47 jobs, against roughly three
    #: quarters of a cent a photo for Sonnet. See CLAUDE.md for the numbers.
    vision_cloud_model: str = "claude-sonnet-5"
    #: The closer look, run only when asked for.
    vision_cloud_detail_model: str = "claude-opus-5"
    #: Read once at startup from ANTHROPIC_API_KEY, else the project root's
    #: .env (see secrets.py). None is not an error: the hybrid reads locally.
    anthropic_api_key: str | None = dataclasses.field(default=None, repr=False)
    #: Dollars, cumulative over every cloud job ever run. Past it the cloud
    #: tier is not offered and reading falls back to the local model, which is
    #: what makes this a cap rather than a number on a screen. The move's
    #: budget was set at $20-30; the default is the top of that.
    vision_budget_usd: float = 30.0
    #: "ollama", "claude" (cloud first, local behind it), or "stub": a canned
    #: provider that sleeps and returns a fixed draft, for building and
    #: checking the UI without a model. **The dataclass default is local**, so
    #: a Config built directly -- which is what every test does -- can never
    #: reach the API. from_env is what turns the cloud on.
    vision_provider: str = "ollama"
    vision_stub_seconds: float = 3.0
    #: Whether the app starts the background thread that analyses uploaded
    #: photos. **Off unless asked for**, so a Config built directly -- which is
    #: what every test does -- never starts a thread that talks to a model.
    #: from_env turns it on: the running service is the one place it belongs.
    auto_analyse: bool = False

    def replace(self, **changes) -> Config:
        return dataclasses.replace(self, **changes)

    def vision_model_for(self, *, detail: bool = False) -> str:
        """Which model a job names when it is queued: the one tried first.

        Only the cloud provider names cloud models. The stub decides which
        canned draft to return by comparing what it is given against
        `vision_detail_model`, so naming a cloud model under "stub" would
        silently stop the browser checks ever seeing a closer look.
        """
        if self.vision_provider == "claude":
            return self.vision_cloud_detail_model if detail else self.vision_cloud_model
        return self.vision_detail_model if detail else self.vision_model

    def vision_fallbacks(self) -> dict[str, str]:
        """Each cloud tier and the local model that stands in for it.

        "claude-sonnet-5" means nothing to Ollama, so the pair that falls back
        has to be told what to ask for instead.
        """
        return {
            self.vision_cloud_model: self.vision_model,
            self.vision_cloud_detail_model: self.vision_detail_model,
        }


def env_file_path(e: Mapping[str, str], *, real: bool) -> Path | None:
    """Which `.env` to read, if any.

    `MOVING_ENV_FILE` names one -- that is how a test or a throwaway server
    points at a fixture. Otherwise it is the project root's own `.env`, which
    `ROOT` makes per-checkout: **a worktree has its own**, and should, since a
    dev server in a worktree must not quietly pick up the main checkout's key.

    A *dict* of variables is not this machine, so `from_env({...})` reads no
    file unless it names one. That is what keeps the suite from ever finding a
    real key, in the same spirit as `auto_analyse` being off by default.
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
        vision_cloud_model=e.get("MOVING_VISION_CLOUD_MODEL", "claude-sonnet-5"),
        vision_cloud_detail_model=e.get("MOVING_VISION_CLOUD_DETAIL_MODEL", "claude-opus-5"),
        anthropic_api_key=secrets.anthropic_api_key(e, env_file=env_file),
        vision_budget_usd=float(e.get("MOVING_VISION_BUDGET_USD", "30")),
        # The hybrid is the default for a running service: cloud first, the
        # local model whenever the cloud cannot answer. MOVING_VISION_PROVIDER
        # pins it to "ollama" or "stub".
        vision_provider=e.get("MOVING_VISION_PROVIDER", "claude"),
        vision_stub_seconds=float(e.get("MOVING_VISION_STUB_SECONDS", "3")),
        auto_analyse=e.get("MOVING_AUTO_ANALYSE", "1") not in ("0", "false", "no", "off"),
    )
