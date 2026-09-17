"""A connection must survive the thread handoff FastAPI performs.

FastAPI runs a sync generator dependency's `__enter__` via run_in_threadpool,
the endpoint body via the threadpool again, and `__exit__` under a *separate*
CapacityLimiter (fastapi/concurrency.py). So within a single request the setup,
the body and the teardown can each land on a different worker thread.

sqlite3 rejects that by default, which took the live service down with 500s on
/api/rooms and /api/boxes/{code}/items as soon as the phone issued its four
parallel requests. Access is still strictly sequential -- the handoff is
awaited -- so the connection is never used by two threads at once.
"""

import concurrent.futures
import threading

from movingbox import db, store


def run_in_another_thread(function):
    """Call `function` on a fresh thread, re-raising whatever it raises."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(function).result()


def test_a_connection_can_be_queried_from_another_thread(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    store.create_box(conn, content_summary="pots")

    codes = run_in_another_thread(
        lambda: [r[0] for r in conn.execute("SELECT code FROM boxes")]
    )

    conn.close()
    assert codes == ["B-0001"]


def test_a_connection_can_be_closed_from_another_thread(tmp_path):
    # This is the exact failure: get_conn's `finally: conn.close()` runs under
    # the exit limiter, not on the thread that opened the connection.
    conn = db.connect(tmp_path / "t.db")

    run_in_another_thread(conn.close)


def test_the_handoff_works_across_three_threads(tmp_path):
    """Open, use, and close each on a different thread, as a request does."""
    conn = run_in_another_thread(lambda: db.connect(tmp_path / "t.db"))
    run_in_another_thread(lambda: store.create_box(conn, content_summary="kettle"))
    count = run_in_another_thread(
        lambda: conn.execute("SELECT count(*) FROM boxes").fetchone()[0]
    )
    run_in_another_thread(conn.close)

    assert count == 1


def test_separate_threads_get_separate_connections(tmp_path):
    """Each request opens its own; they must not interfere."""
    path = tmp_path / "t.db"
    db.connect(path).close()
    errors = []

    def write(n):
        try:
            conn = db.connect(path)
            store.create_box(conn, content_summary=f"box {n}")
            conn.close()
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM boxes").fetchone()[0] == 8
    conn.close()
