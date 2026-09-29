"""What the cloud tier has cost, and the cap past which photos are read locally.

The total is summed from `ai_jobs`, never kept in a counter: the jobs are the
record, and they survive every restart.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import providers
from .config import Config, is_spec
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
    if not config.cloud_tier:
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
#: The providers that read on this machine. Anything else answering -- Claude
#: or a plug-in -- is the cloud tier.
LOCAL = ("ollama", "stub")


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
    return len(rows), sum(1 for row in rows if row["provider"] in LOCAL)


def status(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    """Everything the Settings page says about the cloud tier.

    `key` says whether the cloud tier has one -- whether Claude has its key, or
    a plug-in's factory built a provider; nothing here is built from a value.
    """
    spent = total_usd(conn)
    over = over_cap(conn, config)
    if is_spec(config.vision_provider):
        has_key = providers.cloud(config) is not None
    else:
        has_key = bool(config.anthropic_api_key)
    counted = conn.execute(
        "SELECT COUNT(*) AS n FROM ai_jobs WHERE cost_usd IS NOT NULL AND cost_usd > 0"
    ).fetchone()
    reads, locally = recent_reads(conn, config)
    return {
        "provider": config.vision_provider,
        "cloud": config.cloud_tier,
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
        "reading_locally": not config.cloud_tier or not has_key or over,
    }
