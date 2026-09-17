"""Runtime configuration, read from the environment with sane local defaults."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

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
    api_key: str | None = None
    # `fake` writes a PNG preview instead of printing. Anything else needs the
    # QL-800 attached, so it is never the default.
    printer_backend: str = "fake"
    printer_model: str = "QL-800"
    printer_queue: str | None = None  # CUPS queue name, for the cups_raw backend
    label_id: str = "62"  # 62 mm continuous DK-2205; 696 printable dots
    ollama_url: str = "http://localhost:11434"
    vision_model: str = "qwen3-vl:30b"

    def replace(self, **changes) -> Config:
        return dataclasses.replace(self, **changes)


def from_env(env: dict[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
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
        ollama_url=e.get("MOVING_OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        vision_model=e.get("MOVING_VISION_MODEL", "qwen3-vl:30b"),
    )
