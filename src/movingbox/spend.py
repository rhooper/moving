"""What the cloud tier has cost, and the cap past which photos are read locally.

The total is summed from `ai_jobs`, never kept in a counter: the jobs are the
record, and they survive every restart.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .config import Config
from .vision import base


def record(conn: sqlite3.Connection, job_id: int, reading: base.Reading | None) -> None:
    """Write back who answered and what it cost, over the model the job was queued with.

    With no reading, the row is left as it was.
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
    """Every dollar this database has ever spent on reading photos, failed jobs included."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) AS spent FROM ai_jobs").fetchone()
    return float(row["spent"] or 0.0)


def over_cap(conn: sqlite3.Connection, config: Config) -> bool:
    """Whether the cloud tier is withdrawn. Always False without a cloud tier."""
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


#: How far back "recently" looks when asking whether the cloud tier is
#: actually answering.
RECENT = 10


def recent_reads(conn: sqlite3.Connection, config: Config) -> tuple[int, int]:
    """Of the last few photos read, how many, and how many read locally.

    A refused key looks like a working one until the items are compared; this
    lets Settings say so.
    """
    rows = conn.execute(
        """
        SELECT provider FROM ai_jobs
         WHERE photo_id IS NOT NULL AND status IN ('done', 'error')
         ORDER BY id DESC LIMIT ?
        """,
        (RECENT,),
    ).fetchall()
    return len(rows), sum(1 for row in rows if row["provider"] != "claude")


def status(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    """Everything the Settings page says about the cloud tier.

    `key` says whether there is one; nothing here is built from its value.
    """
    spent = total_usd(conn)
    over = over_cap(conn, config)
    has_key = bool(config.anthropic_api_key)
    counted = conn.execute(
        "SELECT COUNT(*) AS n FROM ai_jobs WHERE cost_usd IS NOT NULL AND cost_usd > 0"
    ).fetchone()
    reads, locally = recent_reads(conn, config)
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
        "recent_reads": reads,
        "recent_local": locally,
        "reading_locally": config.vision_provider != "claude" or not has_key or over,
    }
