"""Fragile, marked when something breakable lands: typed, read from a photo,
or already there when "From contents" is pressed.

Asked for as "given it contains a mug or figurine, auto-add fragile". The rule
itself is `fragility`'s (test_fragility.py); these are the places it runs.
"""

import pytest
from fastapi.testclient import TestClient

from movingbox import analysis, db, store
from movingbox.api.app import create_app
from tests.test_analysis import Seen, photographed, saw, worker


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def conn(client, config):
    c = db.connect(config.db_path)
    yield c
    c.close()


@pytest.fixture
def code(client):
    return client.post("/api/boxes", json={}).json()["code"]


def told(client):
    heard = []
    client.app.state.events.publish = lambda kind, code=None, **kw: heard.append((kind, code))
    return heard


def fragile(client, code):
    return client.get(f"/api/boxes/{code}").json()["fragile"]


class TestTypingAnItem:
    def test_a_mug_marks_the_box_and_says_so(self, client, code):
        added = client.post(f"/api/boxes/{code}/items", json={"name": "black devs mug"}).json()

        assert added["marked_fragile"] is True
        assert added["name"] == "black devs mug"
        assert fragile(client, code)

    def test_other_devices_are_told_the_box_changed(self, client, code):
        heard = told(client)

        client.post(f"/api/boxes/{code}/items", json={"name": "axolotl figurine"})

        assert ("box.updated", code) in heard

    def test_a_weight_plate_marks_nothing(self, client, code):
        heard = told(client)

        added = client.post(f"/api/boxes/{code}/items", json={"name": "weight plate"}).json()

        assert added["marked_fragile"] is False
        assert not fragile(client, code)
        assert ("box.updated", code) not in heard

    def test_it_is_in_the_history_as_automatic(self, client, code):
        client.post(f"/api/boxes/{code}/items", json={"name": "small vase"})

        lines = client.get(f"/api/boxes/{code}/events").json()
        marked = [line for line in lines if line.get("field") == "fragile"]

        assert [(m["actor"], m["note"], m["to_value"]) for m in marked] == [
            ("auto", "holds small vase", "1")
        ]

    def test_a_person_who_cleared_it_is_not_overruled(self, client, code):
        client.post(f"/api/boxes/{code}/items", json={"name": "mug"})
        client.patch(f"/api/boxes/{code}", json={"fragile": False})

        added = client.post(f"/api/boxes/{code}/items", json={"name": "vase"}).json()

        assert added["marked_fragile"] is False
        assert not fragile(client, code)


class TestFromContents:
    def test_it_names_what_would_make_it_fragile_and_writes_nothing(self, client, conn, code):
        # A record that already held a mug before marking existed: B-0062.
        for name in ("weight plate", "black devs mug", "remote organizer"):
            store.add_item(conn, code, name=name)
        conn.commit()

        suggestion = client.get(f"/api/boxes/{code}/summary-suggestion").json()

        assert suggestion["breakable"] == ["black devs mug"]
        assert not fragile(client, code)

    def test_nothing_when_it_is_already_fragile(self, client, code):
        client.post(f"/api/boxes/{code}/items", json={"name": "mug"})

        assert client.get(f"/api/boxes/{code}/summary-suggestion").json()["breakable"] == []

    def test_nothing_when_a_person_cleared_it(self, client, code):
        client.post(f"/api/boxes/{code}/items", json={"name": "mug"})
        client.patch(f"/api/boxes/{code}", json={"fragile": False})

        assert client.get(f"/api/boxes/{code}/summary-suggestion").json()["breakable"] == []

    def test_something_breakable_inside_counts(self, client, conn, code):
        tub = client.post("/api/boxes", json={"kind": "tub", "parent_code": code}).json()["code"]
        store.add_item(conn, tub, name="wine glasses")
        conn.commit()

        assert client.get(f"/api/boxes/{code}/summary-suggestion").json()["breakable"] == [
            "wine glasses"
        ]


class TestReadingAPhoto:
    @pytest.fixture
    def box(self, conn):
        return store.create_box(conn)

    def test_the_model_seeing_something_breakable_marks_it(self, conn, config, box):
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("cardboard box", 1), fragile=True))).run_once()

        assert store.get_box(conn, box["code"])["fragile"] == 1

    def test_a_breakable_thing_it_names_marks_it(self, conn, config, box):
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("ceramic candle holder", 1)))).run_once()

        assert store.get_box(conn, box["code"])["fragile"] == 1
        logged = conn.execute(
            "SELECT actor, note FROM events WHERE box_code = ? AND field = 'fragile'",
            (box["code"],),
        ).fetchall()
        assert [tuple(row) for row in logged] == [("ai", "holds ceramic candle holder")]

    def test_nothing_breakable_marks_nothing(self, conn, config, box):
        photographed(conn, config, box["code"])

        worker(config, Seen(saw(("weight plate", 2)))).run_once()

        assert store.get_box(conn, box["code"])["fragile"] == 0

    def test_other_devices_are_told(self, conn, config, box):
        photographed(conn, config, box["code"])
        heard = []
        analyst = analysis.Analyst(
            config,
            provider_factory=lambda: Seen(saw(fragile=True)),
            publish=lambda kind, code=None: heard.append((kind, code)),
        )

        analyst.run_once()

        assert ("box.updated", box["code"]) in heard
