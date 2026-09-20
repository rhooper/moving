"""Migration 0006: jobs belong to photos, and a summary has an author.

The upgrade path is the one the live database takes, so it is tested from a
database that already has boxes, summaries and old box-level drafting jobs.
"""

import pytest

from movingbox import analysis, db, store

from .schema_history import roll_back_to


@pytest.fixture
def old(config):
    """A database as it was at version 5, with data in it."""
    conn = db.connect(config.db_path)
    roll_back_to(conn, 5)
    conn.execute("INSERT INTO boxes (code, content_summary) VALUES ('B-0001', 'pots and pans')")
    conn.execute("INSERT INTO boxes (code) VALUES ('B-0002')")
    conn.execute("""
        INSERT INTO ai_jobs (box_id, provider, model, prompt_version, status, raw_response)
        VALUES (1, 'ollama', 'qwen3-vl:30b', 'old', 'done', '{"items": []}')
        """)
    conn.close()
    return config


def upgraded(config):
    return db.connect(config.db_path)  # connect() migrates


def test_it_lands_on_version_six(old):
    conn = upgraded(old)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 6
    finally:
        conn.close()


def test_every_existing_summary_is_treated_as_a_persons(old):
    # Some were accepted from a draft -- but accepting was a decision, and
    # guessing otherwise would let the next photo overwrite it.
    conn = upgraded(old)
    try:
        sources = [r["summary_source"] for r in conn.execute("SELECT summary_source FROM boxes")]
        assert sources == ["manual", "manual"]
    finally:
        conn.close()


def test_an_empty_summary_is_still_the_models_to_fill(old):
    conn = upgraded(old)
    try:
        store.add_item(conn, "B-0002", name="kettle", source="ai")
        assert analysis.refresh_summary(conn, "B-0002") is True
        assert analysis.refresh_summary(conn, "B-0001") is False
    finally:
        conn.close()


def test_old_box_level_jobs_survive_and_are_not_mistaken_for_photo_jobs(old):
    conn = upgraded(old)
    try:
        job = conn.execute("SELECT * FROM ai_jobs").fetchone()
        assert (job["status"], job["photo_id"], job["duration_ms"]) == ("done", None, None)
        # Not queued work, and not history for the estimate.
        assert analysis.recover(conn) == 0
        assert analysis.estimate_ms(conn, "qwen3-vl:30b") == analysis.DEFAULT_ESTIMATE_MS
    finally:
        conn.close()


def test_an_old_running_draft_is_not_requeued_as_a_photo(old):
    conn = upgraded(old)
    try:
        conn.execute("UPDATE ai_jobs SET status = 'running'")
        assert analysis.recover(conn) == 0
        assert (
            analysis.Analyst(old, provider_factory=lambda: None, publish=lambda *a: None).run_once()
            is False
        )
    finally:
        conn.close()
