"""What the cloud tier has cost, and the cap that stops it costing more.

A budget nobody can see is a budget that gets exceeded, so the number is on
the Settings page. A budget nothing enforces is the same thing, so past the cap
the cloud tier is simply not offered and reading falls back to the local model.
"""

import pytest

from movingbox import db, spend, store
from movingbox.vision import base

from .schema_history import roll_back_to


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


def a_job(conn, *, model="claude-sonnet-5", provider="claude", status="done"):
    box = store.create_box(conn)
    cursor = conn.execute(
        """
        INSERT INTO ai_jobs (box_id, photo_id, provider, model, prompt_version, status)
        VALUES (?, 1, ?, ?, 'v1', ?)
        """,
        (box["id"], provider, model, status),
    )
    return cursor.lastrowid


def read(provider="claude", model="claude-sonnet-5", cost=0.0075, tokens=(2760, 200)):
    return base.Reading(
        provider=provider,
        model=model,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cost_usd=cost,
    )


class TestRecording:
    def test_what_answered_is_written_back_onto_the_job(self, conn):
        # The job named claude-sonnet-5 when it was queued; the local model is
        # what actually read the photo. A wrong item has to be traceable to
        # the model that wrote it.
        job = a_job(conn)

        spend.record(conn, job, read(provider="ollama", model="qwen3-vl:4b-instruct", cost=0.0))

        row = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (job,)).fetchone()
        assert (row["provider"], row["model"]) == ("ollama", "qwen3-vl:4b-instruct")
        assert row["cost_usd"] == 0.0

    def test_the_tokens_and_the_dollars_are_both_kept(self, conn):
        job = a_job(conn)

        spend.record(conn, job, read())

        row = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (job,)).fetchone()
        assert (row["input_tokens"], row["output_tokens"]) == (2760, 200)
        assert row["cost_usd"] == pytest.approx(0.0075)

    def test_nothing_to_record_leaves_the_job_alone(self, conn):
        job = a_job(conn)

        spend.record(conn, job, None)

        row = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (job,)).fetchone()
        assert (row["provider"], row["model"]) == ("claude", "claude-sonnet-5")
        assert row["cost_usd"] is None


class TestTheRunningTotal:
    def test_an_empty_database_has_spent_nothing(self, conn):
        assert spend.total_usd(conn) == 0.0

    def test_every_job_that_cost_something_counts(self, conn):
        for _ in range(3):
            spend.record(conn, a_job(conn), read(cost=0.0075))

        assert spend.total_usd(conn) == pytest.approx(0.0225)

    def test_a_job_that_failed_after_the_call_still_counts(self, conn):
        # A reply that cost money and then failed to parse still cost money.
        spend.record(conn, a_job(conn, status="error"), read(cost=0.0075))

        assert spend.total_usd(conn) == pytest.approx(0.0075)

    def test_local_reads_add_nothing(self, conn):
        spend.record(conn, a_job(conn), read(provider="ollama", cost=0.0))

        assert spend.total_usd(conn) == 0.0


class TestTheCap:
    def cloud(self, config, **changes):
        return config.replace(vision_provider="claude", **changes)

    def test_under_it_the_cloud_is_offered(self, conn, config):
        spend.record(conn, a_job(conn), read(cost=1.0))

        assert spend.over_cap(conn, self.cloud(config, vision_budget_usd=30.0)) is False

    def test_at_it_the_cloud_is_not(self, conn, config):
        spend.record(conn, a_job(conn), read(cost=30.0))

        assert spend.over_cap(conn, self.cloud(config, vision_budget_usd=30.0)) is True

    def test_a_cap_of_zero_means_never(self, conn, config):
        assert spend.over_cap(conn, self.cloud(config, vision_budget_usd=0.0)) is True

    def test_a_local_only_setup_is_never_over(self, conn, config):
        # Nothing to cap: there is no cloud tier to withdraw.
        spend.record(conn, a_job(conn), read(cost=100.0))

        assert spend.over_cap(conn, config.replace(vision_provider="ollama")) is False


class TestWhatSettingsIsTold:
    def test_it_says_what_has_been_spent_against_what_is_allowed(self, conn, config):
        spend.record(conn, a_job(conn), read(cost=0.0075))
        spend.record(conn, a_job(conn), read(model="claude-opus-5", cost=0.0188))

        told = spend.status(conn, config.replace(vision_budget_usd=30.0, vision_provider="claude"))

        assert told["spent_usd"] == pytest.approx(0.0263)
        assert told["cap_usd"] == 30.0
        assert told["over"] is False
        assert told["photos"] == 2

    def test_it_breaks_the_total_down_by_model(self, conn, config):
        spend.record(conn, a_job(conn), read(cost=0.0075))
        spend.record(conn, a_job(conn), read(cost=0.0075))
        spend.record(conn, a_job(conn), read(model="claude-opus-5", cost=0.0188))

        told = spend.status(conn, config)

        assert told["by_model"] == [
            {"model": "claude-opus-5", "photos": 1, "spent_usd": pytest.approx(0.0188)},
            {"model": "claude-sonnet-5", "photos": 2, "spent_usd": pytest.approx(0.015)},
        ]

    def test_a_key_that_is_set_but_never_answers_is_visible(self, conn, config):
        # A refused key looks exactly like a working one until you compare the
        # items. Two photos in a row read locally is what says otherwise.
        for _ in range(2):
            spend.record(
                conn, a_job(conn, status="done"), read(provider="ollama", model="qwen3", cost=0.0)
            )

        told = spend.status(conn, config.replace(vision_provider="claude"))

        assert (told["recent_reads"], told["recent_local"]) == (2, 2)

    def test_a_cloud_read_among_them_is_not(self, conn, config):
        spend.record(conn, a_job(conn), read(provider="ollama", model="qwen3", cost=0.0))
        spend.record(conn, a_job(conn), read())

        told = spend.status(conn, config.replace(vision_provider="claude"))

        assert (told["recent_reads"], told["recent_local"]) == (2, 1)

    def test_it_says_which_tiers_are_configured(self, conn, config):
        told = spend.status(conn, config.replace(vision_provider="claude"))

        assert told["provider"] == "claude"
        assert told["model"] == "claude-sonnet-5"
        assert told["detail_model"] == "claude-opus-5"

    def test_it_says_whether_there_is_a_key_without_saying_what_it_is(self, conn, config):
        told = spend.status(conn, config.replace(anthropic_api_key="sk-ant-secret-value"))

        assert told["key"] is True
        assert "sk-ant-secret-value" not in repr(told)

    def test_no_key_reads_as_local_only_rather_than_broken(self, conn, config):
        told = spend.status(conn, config.replace(vision_provider="claude"))

        assert told["key"] is False
        assert told["reading_locally"] is True

    def test_over_the_cap_also_reads_as_local_only(self, conn, config):
        spend.record(conn, a_job(conn), read(cost=99.0))

        told = spend.status(
            conn,
            config.replace(
                vision_provider="claude", anthropic_api_key="sk-ant-x", vision_budget_usd=30.0
            ),
        )

        assert told["over"] is True
        assert told["reading_locally"] is True


class TestTheMigration:
    @pytest.fixture
    def old(self, config):
        conn = db.connect(config.db_path)
        roll_back_to(conn, 9)
        conn.execute("INSERT INTO boxes (code) VALUES ('B-0001')")
        conn.execute("""
            INSERT INTO ai_jobs (box_id, photo_id, provider, model, prompt_version, status)
            VALUES (1, 1, 'ollama', 'qwen3-vl:4b-instruct', 'v1', 'done')
            """)
        conn.close()
        return config

    def test_it_lands_on_version_ten(self, old):
        conn = db.connect(old.db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] >= 10
        finally:
            conn.close()

    def test_jobs_from_before_the_cloud_cost_nothing_rather_than_an_unknown(self, old):
        # Every job on the live database was local. Nothing to backfill, and a
        # NULL would make the running total unsummable.
        conn = db.connect(old.db_path)
        try:
            assert spend.total_usd(conn) == 0.0
            row = conn.execute("SELECT * FROM ai_jobs").fetchone()
            assert row["cost_usd"] is None
            assert row["input_tokens"] is None
        finally:
            conn.close()
