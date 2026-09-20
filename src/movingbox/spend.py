"""What the cloud tier has cost, and the cap that stops it costing more.

A move was funded at $20-30, on the understanding that models are chosen on
merit rather than on price. Two things follow, and they are the whole of this
module:

- **The number is visible.** A budget nobody can see is a budget that gets
  exceeded. Settings shows the running total against the cap, broken down by
  model, and says plainly when photos are being read locally.
- **The cap is enforced.** Past it the cloud tier is simply not offered and
  reading falls back to the local model. A budget nothing enforces is the same
  thing as one nobody can see.

The total is summed from `ai_jobs` rather than kept in a counter: the jobs are
the record, they survive every restart, and a counter would be one more thing
to get out of step. Every job costs its own row, so there is nothing to
reconcile.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .config import Config
from .vision import base


def record(conn: sqlite3.Connection, job_id: int, reading: base.Reading | None) -> None:
    """Write back who answered and what it cost.

    `provider` and `model` were set when the job was queued, naming what would
    be *tried* first. With a fallback in play that is not necessarily what
    answered, and a wrong item has to be traceable to the model that wrote it.

    A provider that reported nothing leaves the row as it was: a reading of
    "unknown" is worse than the guess already on it.
    """
    if reading is None:
        return
    conn.execute(
        """
        UPDATE ai_jobs
           SET provider = ?, model = ?, input_tokens = ?, output_tokens = ?, cost_usd = ?
         WHERE id = ?
        """,
        (
            reading.provider,
            reading.model,
            reading.input_tokens,
            reading.output_tokens,
            reading.cost_usd,
            job_id,
        ),
    )


def total_usd(conn: sqlite3.Connection) -> float:
    """Every dollar this database has ever spent on reading photos.

    Failed jobs included: a reply that cost money and then failed to parse
    still cost money, and a budget that forgave those would drift.
    """
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) AS spent FROM ai_jobs").fetchone()
    return float(row["spent"] or 0.0)


def over_cap(conn: sqlite3.Connection, config: Config) -> bool:
    """Whether the cloud tier is withdrawn.

    False for a local-only setup whatever has been spent: there is no cloud
    tier to withdraw, and saying "over budget" about the free one would be a
    lie on the Settings page.
    """
    if config.vision_provider != "claude":
        return False
    return total_usd(conn) >= config.vision_budget_usd


def by_model(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """What each model has cost, dearest first."""
    rows = conn.execute("""
        SELECT model, COUNT(*) AS photos, SUM(cost_usd) AS spent
          FROM ai_jobs
         WHERE cost_usd IS NOT NULL AND cost_usd > 0
         GROUP BY model
         ORDER BY spent DESC, model
        """).fetchall()
    return [
        {"model": row["model"], "photos": row["photos"], "spent_usd": float(row["spent"] or 0.0)}
        for row in rows
    ]


def status(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    """Everything the Settings page says about the cloud tier.

    Carries whether there *is* a key, never the key: `key` is a boolean, and
    nothing in this dict is built from the value.
    """
    spent = total_usd(conn)
    over = over_cap(conn, config)
    has_key = bool(config.anthropic_api_key)
    counted = conn.execute(
        "SELECT COUNT(*) AS n FROM ai_jobs WHERE cost_usd IS NOT NULL AND cost_usd > 0"
    ).fetchone()
    return {
        "provider": config.vision_provider,
        "model": config.vision_cloud_model,
        "detail_model": config.vision_cloud_detail_model,
        "local_model": config.vision_model,
        "local_detail_model": config.vision_detail_model,
        "key": has_key,
        "spent_usd": spent,
        "cap_usd": config.vision_budget_usd,
        "over": over,
        "photos": counted["n"],
        "by_model": by_model(conn),
        # The one thing somebody actually wants to know from this panel: are
        # my photos being read by the good model right now, or not?
        "reading_locally": config.vision_provider != "claude" or not has_key or over,
    }
