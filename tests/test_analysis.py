"""Photo analysis in the background: queueing, merging, summaries, estimates.

The worker is exercised one job at a time through `run_once()`, on the test's
own thread and with a stub provider -- no sleeping, no model, no races. The
thread that wraps it in production is covered separately and briefly.

See docs/superpowers/specs/2026-09-18-photo-analysis.md for the rules these pin.
"""

import io
import json

import pytest
from PIL import Image

from movingbox import analysis, db, storage, store
from movingbox.vision import base


def a_jpeg(colour=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class Seen:
    """A provider that reports what it was told to see."""

    name = "stub"

    def __init__(self, *drafts):
        self.drafts = list(drafts)
        self.calls = 0

    def draft(self, images, *, model):
        self.calls += 1
        draft = self.drafts.pop(0)
        if isinstance(draft, Exception):
            raise draft
        return draft


def saw(*items, summary="things", fragile=False):
    return base.BoxDraft(
        summary=summary,
        items=[base.DraftItem(name=n, qty=q) for n, q in items],
        fragile=fragile,
    )


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


@pytest.fixture
def box(conn):
    return store.create_box(conn)


def photographed(conn, config, code, colour=(120, 90, 60)):
    photo = storage.save_photo(conn, config, code, a_jpeg(colour), filename="p.jpg")
    analysis.enqueue(conn, config, photo["id"])
    return photo


def worker(config, provider):
    return analysis.Analyst(config, provider_factory=lambda: provider, publish=lambda *a: None)


def names(conn, code):
    return sorted((i["name"], i["qty"], i["source"]) for i in store.list_items(conn, code))


class TestQueueing:
    def test_a_photo_is_queued_once(self, conn, config, box):
        photo = photographed(conn, config, box["code"])
        analysis.enqueue(conn, config, photo["id"])  # the phone retried the upload

        jobs = conn.execute("SELECT status FROM ai_jobs WHERE photo_id = ?", (photo["id"],))
        assert [row["status"] for row in jobs] == ["pending"]

    def test_a_loose_thing_is_not_inventoried(self, conn, config):
        # A bicycle has no contents; there is nothing for the model to list.
        bike = store.create_box(conn, kind="item", content_summary="Bicycle")
        photo = storage.save_photo(conn, config, bike["code"], a_jpeg(), filename="p.jpg")

        assert analysis.enqueue(conn, config, photo["id"]) is None

    def test_an_idle_worker_does_nothing(self, config, conn):
        assert worker(config, Seen()).run_once() is False

    def test_jobs_run_oldest_first(self, conn, config, box):
        first = photographed(conn, config, box["code"], (1, 2, 3))
        photographed(conn, config, box["code"], (9, 9, 9))

        worker(config, Seen(saw(("kettle", 1)))).run_once()

        done = conn.execute("SELECT photo_id FROM ai_jobs WHERE status = 'done'").fetchall()
        assert [row["photo_id"] for row in done] == [first["id"]]

    def test_a_job_the_last_process_died_holding_is_run_again(self, conn, config, box):
        # A deploy restarts the service mid-analysis, often.
        photo = photographed(conn, config, box["code"])
        conn.execute("UPDATE ai_jobs SET status = 'running', started_at = datetime('now')")

        analysis.recover(conn)

        row = conn.execute(
            "SELECT status, started_at FROM ai_jobs WHERE photo_id = ?", (photo["id"],)
        ).fetchone()
        assert (row["status"], row["started_at"]) == ("pending", None)

    def test_asking_again_requeues_a_finished_photo(self, conn, config, box):
        photo = photographed(conn, config, box["code"])
        worker(config, Seen(saw(("kettle", 1)))).run_once()

        analysis.enqueue(conn, config, photo["id"], again=True)

        assert analysis.state_of(conn, photo["id"])["status"] == "pending"


class TestWhatIsFoundIsKeptWithThePhoto:
    def test_the_result_is_stored_on_the_photos_job(self, conn, config, box):
        photo = photographed(conn, config, box["code"])

        worker(config, Seen(saw(("kettle", 1), ("mug", 3), summary="tea things"))).run_once()

        row = conn.execute("SELECT * FROM ai_jobs WHERE photo_id = ?", (photo["id"],)).fetchone()
        kept = json.loads(row["raw_response"])
        assert row["status"] == "done"
        assert kept["summary"] == "tea things"
        assert [i["name"] for i in kept["items"]] == ["kettle", "mug"]
        assert row["duration_ms"] is not None and row["duration_ms"] >= 0

    def test_the_photo_reports_how_much_was_found(self, conn, config, box):
        photo = photographed(conn, config, box["code"])
        worker(config, Seen(saw(("kettle", 1), ("mug", 3)))).run_once()

        state = analysis.state_of(conn, photo["id"])

        assert (state["status"], state["remaining_ms"], state["items_found"], state["error"]) == (
            "done",
            0,
            2,
            None,
        )

    def test_the_photo_can_say_what_was_seen_in_it(self, conn, config, box):
        # For the viewer: this photo's own findings, not the box's merged list.
        photo = photographed(conn, config, box["code"])
        worker(config, Seen(saw(("kettle", 1), ("mug", 3), summary="tea things"))).run_once()

        state = analysis.state_of(conn, photo["id"])

        assert state["summary"] == "tea things"
        assert state["items"] == [{"name": "kettle", "qty": 1}, {"name": "mug", "qty": 3}]

    def test_what_was_seen_is_per_photo_not_per_box(self, conn, config, box):
        first = photographed(conn, config, box["code"], (1, 1, 1))
        second = photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("kettle", 1)), saw(("toaster", 1))))
        both.run_once()
        both.run_once()

        assert [i["name"] for i in analysis.state_of(conn, first["id"])["items"]] == ["kettle"]
        assert [i["name"] for i in analysis.state_of(conn, second["id"])["items"]] == ["toaster"]

    def test_a_photo_still_being_read_has_seen_nothing_yet(self, conn, config, box):
        photo = photographed(conn, config, box["code"])

        state = analysis.state_of(conn, photo["id"])

        assert (state["items"], state["summary"]) == (None, None)

    def test_a_failure_is_kept_too_and_says_why(self, conn, config, box):
        photo = photographed(conn, config, box["code"])

        worker(config, Seen(base.DraftUnreadable("the model replied with a poem"))).run_once()

        state = analysis.state_of(conn, photo["id"])
        assert state["status"] == "error"
        assert "poem" in state["error"]
        assert names(conn, box["code"]) == []

    def test_a_crash_in_the_provider_does_not_take_the_worker_down(self, conn, config, box):
        photo = photographed(conn, config, box["code"])

        assert worker(config, Seen(ConnectionError("ollama is not running"))).run_once() is True

        assert analysis.state_of(conn, photo["id"])["status"] == "error"

    def test_a_photo_deleted_mid_queue_is_skipped_quietly(self, conn, config, box):
        photo = photographed(conn, config, box["code"])
        storage.delete_photo(conn, config, photo["id"])
        provider = Seen(saw(("kettle", 1)))

        worker(config, provider).run_once()

        assert provider.calls == 0
        assert analysis.state_of(conn, photo["id"]) is None

    def test_a_photo_that_was_never_queued_has_no_state(self, conn, config, box):
        photo = storage.save_photo(conn, config, box["code"], a_jpeg(), filename="p.jpg")

        assert analysis.state_of(conn, photo["id"]) is None


class TestMerging:
    def test_what_is_found_is_added_as_autogenerated(self, conn, config, box):
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("kettle", 1), ("mug", 3)))).run_once()

        assert names(conn, box["code"]) == [("kettle", 1, "ai"), ("mug", 3, "ai")]

    def test_a_second_photo_of_the_same_things_adds_nothing(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("kettle", 1)), saw(("Kettle ", 1), ("toaster", 1))))

        both.run_once()
        both.run_once()

        assert names(conn, box["code"]) == [("kettle", 1, "ai"), ("toaster", 1, "ai")]

    @pytest.mark.parametrize("again", ["mugs", "MUG", " mug ", "Mugs"])
    def test_a_plural_or_a_capital_is_the_same_thing(self, conn, config, box, again):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("mug", 2)), saw((again, 2))))

        both.run_once()
        both.run_once()

        assert len(store.list_items(conn, box["code"])) == 1

    def test_a_better_count_raises_an_autogenerated_quantity(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("mug", 2)), saw(("mug", 5))))

        both.run_once()
        both.run_once()

        assert names(conn, box["code"]) == [("mug", 5, "ai")]

    def test_a_worse_count_does_not_lower_it(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("mug", 5)), saw(("mug", 1))))

        both.run_once()
        both.run_once()

        assert names(conn, box["code"]) == [("mug", 5, "ai")]

    def test_what_a_person_typed_is_never_touched(self, conn, config, box):
        store.add_item(conn, box["code"], name="Mugs", qty=4)
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("mug", 12), ("kettle", 1)))).run_once()

        assert names(conn, box["code"]) == [("Mugs", 4, "manual"), ("kettle", 1, "ai")]

    def test_a_renamed_autogenerated_item_becomes_the_persons(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        worker(config, Seen(saw(("mug", 2)))).run_once()
        item = store.list_items(conn, box["code"])[0]

        renamed = store.update_item(conn, item["id"], name="espresso cups")

        assert (renamed["name"], renamed["source"]) == ("espresso cups", "manual")

    def test_a_deleted_record_is_left_alone(self, conn, config, box):
        photographed(conn, config, box["code"])
        store.delete_box(conn, config, box["code"])

        worker(config, Seen(saw(("kettle", 1)))).run_once()

        assert names(conn, box["code"]) == []


class TestTheSummary:
    def summary(self, conn, code):
        found = store.get_box(conn, code)
        return found["content_summary"], found["summary_source"]

    def test_an_empty_summary_is_written_from_what_was_found(self, conn, config, box):
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("kettle", 1), ("mug", 3)))).run_once()

        assert self.summary(conn, box["code"]) == ("kettle, 3 mugs", "auto")

    def test_an_autogenerated_summary_is_rewritten_as_more_is_found(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("kettle", 1)), saw(("toaster", 1))))

        both.run_once()
        both.run_once()

        assert self.summary(conn, box["code"]) == ("kettle, toaster", "auto")

    def test_a_summary_a_person_typed_is_never_overwritten(self, conn, config):
        mine = store.create_box(conn, content_summary="Grandma's tea set")
        photographed(conn, config, mine["code"])

        worker(config, Seen(saw(("kettle", 1)))).run_once()

        assert self.summary(conn, mine["code"]) == ("Grandma's tea set", "manual")

    def test_typing_over_an_autogenerated_summary_makes_it_yours(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(("kettle", 1)), saw(("toaster", 1))))
        both.run_once()

        store.update_box(conn, box["code"], content_summary="breakfast things")
        both.run_once()

        assert self.summary(conn, box["code"]) == ("breakfast things", "manual")

    def test_clearing_a_summary_hands_it_back(self, conn, config, box):
        store.update_box(conn, box["code"], content_summary="breakfast things")
        store.update_box(conn, box["code"], content_summary=None)
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("kettle", 1)))).run_once()

        assert self.summary(conn, box["code"]) == ("kettle", "auto")

    def test_a_photo_described_but_not_itemised_still_gives_the_record_a_summary(
        self, conn, config, box
    ):
        # Seen for real: a cabinet of labelled drawers came back as one good
        # sentence and no items, and the record was left saying nothing at all.
        photographed(conn, config, box["code"])

        described = saw(summary="A plastic organiser with many labelled drawers")
        worker(config, Seen(described)).run_once()

        assert self.summary(conn, box["code"]) == (
            "A plastic organiser with many labelled drawers",
            "auto",
        )

    def test_once_there_are_items_the_summary_is_made_from_them(self, conn, config, box):
        photographed(conn, config, box["code"], (1, 1, 1))
        photographed(conn, config, box["code"], (2, 2, 2))
        both = worker(config, Seen(saw(summary="A shelf of kitchen things"), saw(("kettle", 1))))

        both.run_once()
        both.run_once()

        assert self.summary(conn, box["code"]) == ("kettle", "auto")

    def test_the_models_sentence_never_replaces_a_persons(self, conn, config):
        mine = store.create_box(conn, content_summary="Grandma's tea set")
        photographed(conn, config, mine["code"])

        worker(config, Seen(saw(summary="Assorted crockery"))).run_once()

        assert self.summary(conn, mine["code"]) == ("Grandma's tea set", "manual")

    def test_what_is_nested_inside_counts_towards_the_summary(self, conn, config, box):
        # A crate holding three bags is not an empty crate, and its label
        # should not read as one.
        for _ in range(3):
            store.create_box(conn, kind="bag", parent_code=box["code"])
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("kettle", 1)))).run_once()

        assert self.summary(conn, box["code"]) == ("kettle, 3 bags", "auto")

    def test_the_background_summary_is_never_written_by_a_model(self):
        # The worker runs behind a vision call that is already the bottleneck,
        # so its summary stays plain assembly: instant, offline, the same every
        # time. Only "From contents" -- which a person is waiting on -- phrases.
        # Checked on the imports rather than the text, so saying so in a
        # docstring does not trip it.
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(analysis))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom | ast.Import)
            for alias in node.names
        }

        assert "phrasing" not in imported

    def test_the_new_summary_is_searchable(self, conn, config, box):
        from movingbox import search

        photographed(conn, config, box["code"])
        worker(config, Seen(saw(("samovar", 1)))).run_once()

        assert search.search(conn, "samovar") == [box["id"]]


class TestEstimates:
    def finish(self, conn, config, box, *durations_ms):
        for index, duration in enumerate(durations_ms):
            photo = photographed(conn, config, box["code"], (index, index, index))
            conn.execute(
                "UPDATE ai_jobs SET status = 'done', duration_ms = ?, "
                "completed_at = datetime('now') WHERE photo_id = ?",
                (duration, photo["id"]),
            )

    def test_with_no_history_it_is_a_stated_guess(self, conn, config):
        assert analysis.estimate_ms(conn, config.vision_model) == analysis.DEFAULT_ESTIMATE_MS

    def test_it_is_the_median_of_recent_runs(self, conn, config, box):
        # One cold start at 31 s must not make every later photo look slow.
        self.finish(conn, config, box, 6000, 7000, 31000, 6500, 7500)

        assert analysis.estimate_ms(conn, config.vision_model) == 7000

    def test_only_the_last_ten_count(self, conn, config, box):
        self.finish(conn, config, box, *([60000] * 5), *([5000] * 10))

        assert analysis.estimate_ms(conn, config.vision_model) == 5000

    def test_another_models_history_is_not_this_models(self, conn, config, box):
        self.finish(conn, config, box, 5000, 5000, 5000)

        assert analysis.estimate_ms(conn, "some-other-model") == analysis.DEFAULT_ESTIMATE_MS

    def test_a_queued_photo_waits_for_the_ones_ahead_of_it(self, conn, config, box):
        self.finish(conn, config, box, 10000, 10000, 10000)
        first = photographed(conn, config, box["code"], (200, 1, 1))
        second = photographed(conn, config, box["code"], (201, 2, 2))

        assert analysis.state_of(conn, first["id"])["remaining_ms"] == 10000
        assert analysis.state_of(conn, second["id"])["remaining_ms"] == 20000

    def test_the_whole_span_never_reads_as_less_than_what_is_left(self, conn, config, box):
        # fraction complete = 1 - remaining / total, so total < remaining would
        # draw a ring that is less than empty.
        photo = photographed(conn, config, box["code"])

        state = analysis.state_of(conn, photo["id"])

        assert state["total_ms"] >= state["remaining_ms"] > 0

    def test_asking_again_does_not_throw_away_what_the_last_run_taught(self, conn, config, box):
        # Re-queueing used to delete the photo's finished job -- which was the
        # history. With one photo that put the estimate back to the default.
        photo = photographed(conn, config, box["code"])
        conn.execute(
            "UPDATE ai_jobs SET status = 'done', duration_ms = 4000, "
            "completed_at = datetime('now') WHERE photo_id = ?",
            (photo["id"],),
        )

        analysis.enqueue(conn, config, photo["id"], again=True)

        assert analysis.estimate_ms(conn, config.vision_model) == 4000
        assert analysis.state_of(conn, photo["id"])["remaining_ms"] == 4000

    def test_a_job_running_past_its_estimate_has_nothing_left_not_less(self, conn, config, box):
        photo = photographed(conn, config, box["code"])
        conn.execute(
            "UPDATE ai_jobs SET status = 'running', "
            "started_at = datetime('now', '-10 minutes') WHERE photo_id = ?",
            (photo["id"],),
        )

        assert analysis.state_of(conn, photo["id"])["remaining_ms"] == 0


class TestTheThread:
    def test_it_works_through_the_queue_and_can_be_stopped(self, conn, config, box):
        import threading

        photographed(conn, config, box["code"])
        told = []
        finished = threading.Event()

        def publish(kind, code):
            told.append((kind, code))
            if kind == "items.changed":
                finished.set()

        analyst = analysis.Analyst(
            config, provider_factory=lambda: Seen(saw(("kettle", 1))), publish=publish, idle=0.01
        )
        analyst.start()
        try:
            assert finished.wait(timeout=5), "the worker never applied the result"
        finally:
            analyst.stop()
            analyst.join(timeout=5)

        assert not analyst.is_alive()
        assert ("photos.changed", box["code"]) in told
        assert ("box.updated", box["code"]) in told
        assert names(conn, box["code"]) == [("kettle", 1, "ai")]


class TestWhatItCostAndWhoAnswered:
    """The budget, from the worker's side.

    Nothing here reaches a model: the cloud half of the pair is a fake that
    answers or fails on command, as everywhere else in the suite.
    """

    def pair(self, cloud, local):
        from movingbox.vision import hybrid

        return hybrid.Hybrid(
            cloud=cloud,
            local=local,
            fallbacks={"claude-sonnet-5": "qwen3-vl:4b-instruct"},
        )

    def cloud(self, config):
        return config.replace(vision_provider="claude", anthropic_api_key="sk-ant-test")

    class Cloud:
        """A stand-in for the Claude provider: answers, or fails, for a price."""

        name = "claude"

        def __init__(self, draft=None, failure=None, cost=0.0075):
            self._draft = draft
            self._failure = failure
            self._cost = cost
            self.asked = []
            self.last = None

        def draft(self, images, *, model):
            self.asked.append(model)
            if self._failure is not None:
                self.last = None
                raise self._failure
            self.last = base.Reading(
                provider="claude",
                model=model,
                input_tokens=2760,
                output_tokens=200,
                cost_usd=self._cost,
            )
            return self._draft

    def test_the_job_records_the_model_that_answered_and_the_price(self, conn, config, box):
        config = self.cloud(config)
        photo = photographed(conn, config, box["code"])
        cloud = self.Cloud(draft=saw(("kettle", 1)))

        worker(config, self.pair(cloud, Seen())).run_once()

        job = conn.execute("SELECT * FROM ai_jobs WHERE photo_id = ?", (photo["id"],)).fetchone()
        assert (job["provider"], job["model"]) == ("claude", "claude-sonnet-5")
        assert (job["input_tokens"], job["output_tokens"]) == (2760, 200)
        assert job["cost_usd"] == pytest.approx(0.0075)

    def test_a_fallback_is_recorded_as_the_local_model(self, conn, config, box):
        # The job was queued naming claude-sonnet-5; Ollama is what read it.
        config = self.cloud(config)
        photo = photographed(conn, config, box["code"])
        cloud = self.Cloud(failure=base.DraftUnreadable("the API could not be reached"))

        worker(config, self.pair(cloud, Seen(saw(("kettle", 1))))).run_once()

        job = conn.execute("SELECT * FROM ai_jobs WHERE photo_id = ?", (photo["id"],)).fetchone()
        assert (job["provider"], job["model"]) == ("stub", "qwen3-vl:4b-instruct")
        assert job["cost_usd"] == 0.0
        assert names(conn, box["code"]) == [("kettle", 1, "ai")]

    def test_a_read_that_failed_after_the_call_still_records_what_it_cost(self, conn, config, box):
        config = self.cloud(config)
        photo = photographed(conn, config, box["code"])
        cloud = self.Cloud(failure=base.DraftUnreadable("ran out of room"))
        cloud.last = None
        pair = self.pair(cloud, Seen(base.DraftUnreadable("nothing answered")))

        # Make the cloud's failure a *paid* one.
        def draft(images, *, model):
            cloud.asked.append(model)
            cloud.last = base.Reading(provider="claude", model=model, cost_usd=0.0075)
            raise base.DraftUnreadable("ran out of room")

        cloud.draft = draft
        worker(config, pair).run_once()

        job = conn.execute("SELECT * FROM ai_jobs WHERE photo_id = ?", (photo["id"],)).fetchone()
        assert job["status"] == "error"
        assert job["cost_usd"] == pytest.approx(0.0075)

    def test_past_the_cap_the_cloud_is_not_offered(self, conn, config, box):
        config = self.cloud(config).replace(vision_budget_usd=1.0)
        spend_it(conn, 1.0)
        photo = photographed(conn, config, box["code"])
        cloud = self.Cloud(draft=saw(("kettle", 1)))

        worker(config, self.pair(cloud, Seen(saw(("mug", 2))))).run_once()

        assert cloud.asked == []  # never even tried
        job = conn.execute("SELECT * FROM ai_jobs WHERE photo_id = ?", (photo["id"],)).fetchone()
        assert job["provider"] == "stub"
        assert names(conn, box["code"]) == [("mug", 2, "ai")]

    def test_under_the_cap_it_is(self, conn, config, box):
        config = self.cloud(config).replace(vision_budget_usd=1.0)
        spend_it(conn, 0.5)
        photographed(conn, config, box["code"])
        cloud = self.Cloud(draft=saw(("kettle", 1)))

        worker(config, self.pair(cloud, Seen())).run_once()

        assert cloud.asked == ["claude-sonnet-5"]

    def test_the_cap_is_checked_per_job_not_once_at_startup(self, conn, config, box):
        # A queue of photos can cross the cap halfway down it.
        config = self.cloud(config).replace(vision_budget_usd=0.005)
        photographed(conn, config, box["code"], colour=(10, 20, 30))
        photographed(conn, config, box["code"], colour=(40, 50, 60))
        cloud = self.Cloud(draft=saw(("kettle", 1)), cost=0.0075)
        hand = worker(config, self.pair(cloud, Seen(saw(("mug", 1)))))

        hand.run_once()
        hand.run_once()

        assert cloud.asked == ["claude-sonnet-5"]  # the second one was over
        providers = [
            row["provider"]
            for row in conn.execute("SELECT provider FROM ai_jobs ORDER BY id").fetchall()
        ]
        assert providers == ["claude", "stub"]


def spend_it(conn, dollars):
    """A finished job that cost this much, so the cap has something to see."""
    conn.execute(
        """
        INSERT INTO ai_jobs (box_id, photo_id, provider, model, prompt_version, status, cost_usd)
        VALUES (NULL, NULL, 'claude', 'claude-sonnet-5', 'v1', 'done', ?)
        """,
        (dollars,),
    )
