"""How many labels each kind of thing gets, and how big a container is.

Asked for: two labels for a box or a crate (more than one face gets seen in a
stack), one for furniture, a loose item or a bag. And an optional size for a
container -- small, medium, large, extra large -- because "the large box for the
kitchen" is how people actually look for one.
"""

import pytest
from fastapi.testclient import TestClient

from movingbox import db, kinds, store
from movingbox.api.app import create_app
from movingbox.labels import printer


class Spy:
    def __init__(self):
        self.jobs = []

    def print_label(self, image, *, code, copies=1):
        self.jobs.append((code, copies))
        return f"spy:{code}"


@pytest.fixture
def spy(monkeypatch):
    backend = Spy()
    monkeypatch.setattr(printer, "get_backend", lambda config: backend)
    return backend


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


def made(client, **fields):
    return client.post("/api/boxes", json={"content_summary": "things", **fields}).json()["code"]


def kinds_listed(client):
    return {k["kind"]: k for k in client.get("/api/settings/kinds").json()}


WANTED = {"box": 2, "crate": 2, "tub": 2, "bag": 1, "item": 1, "furniture": 1}


class TestCopiesPerKind:
    def test_each_kind_has_the_number_asked_for(self, client):
        assert {k: v["copies"] for k, v in kinds_listed(client).items()} == WANTED

    @pytest.mark.parametrize("kind,copies", sorted(WANTED.items()))
    def test_a_print_that_does_not_say_gets_its_kinds_number(self, client, spy, kind, copies):
        code = made(client, kind=kind)

        client.post("/api/labels/print", json={"codes": [code]})

        assert spy.jobs == [(code, copies)]

    def test_a_mixed_batch_prints_each_at_its_own_number(self, client, spy):
        crate, bag = made(client, kind="crate"), made(client, kind="bag")

        client.post("/api/labels/print", json={"codes": [crate, bag]})

        assert spy.jobs == [(crate, 2), (bag, 1)]

    def test_a_print_that_says_is_obeyed_whatever_the_kind(self, client, spy):
        code = made(client, kind="furniture")

        client.post("/api/labels/print", json={"codes": [code], "copies": 4})

        assert spy.jobs == [(code, 4)]

    def test_a_stub_is_still_one(self, client, spy):
        code = made(client, kind="crate")

        client.post("/api/labels/print", json={"codes": [code], "stub": True})

        assert spy.jobs == [(code, 1)]

    def test_the_number_for_a_kind_can_be_changed(self, client, spy):
        code = made(client, kind="bag")

        saved = client.put("/api/settings/kind-copies", json={"kind": "bag", "copies": 3})
        client.post("/api/labels/print", json={"codes": [code]})

        assert saved.json() == {"kind": "bag", "copies": 3}
        assert kinds_listed(client)["bag"]["copies"] == 3
        assert kinds_listed(client)["box"]["copies"] == 2   # the others are untouched
        assert spy.jobs == [(code, 3)]

    @pytest.mark.parametrize("body", [
        {"kind": "bag", "copies": 0},
        {"kind": "bag", "copies": 11},
        {"kind": "suitcase", "copies": 2},
        {"kind": "bag"},
    ])
    def test_nonsense_is_refused(self, client, body):
        assert client.put("/api/settings/kind-copies", json=body).status_code == 422

    def test_the_single_global_number_is_gone(self, client):
        # It lasted two days and nobody ever set it. One number cannot be right
        # for a crate and a lamp at once.
        assert client.get("/api/settings/printing").status_code == 404
        assert "label_copies" not in client.get("/api/printer").json()


class TestSize:
    def test_the_sizes_are_the_four_asked_for(self):
        assert kinds.SIZES == ("small", "medium", "large", "extra large")

    def test_a_container_says_which_sizes_it_can_be_and_a_thing_says_none(self, client):
        listed = kinds_listed(client)

        for kind in ("box", "tub", "crate", "bag"):
            assert listed[kind]["sizes"] == list(kinds.SIZES)
        for kind in ("item", "furniture"):
            assert listed[kind]["sizes"] == []

    def test_it_is_optional(self, client):
        assert client.get(f"/api/boxes/{made(client)}").json()["size"] is None

    def test_it_can_be_given_at_creation_and_changed_later(self, client):
        code = made(client, size="large")
        assert client.get(f"/api/boxes/{code}").json()["size"] == "large"

        client.patch(f"/api/boxes/{code}", json={"size": "extra large"})
        assert client.get(f"/api/boxes/{code}").json()["size"] == "extra large"

    def test_it_can_be_cleared(self, client):
        code = made(client, size="small")

        client.patch(f"/api/boxes/{code}", json={"size": None})

        assert client.get(f"/api/boxes/{code}").json()["size"] is None

    @pytest.mark.parametrize("bad", ["huge", "Large", "", "xl"])
    def test_anything_else_is_refused(self, client, bad):
        assert client.post("/api/boxes", json={"size": bad}).status_code == 422

    def test_the_list_carries_it(self, client):
        code = made(client, size="medium")

        row = next(b for b in client.get("/api/boxes").json() if b["code"] == code)

        assert row["size"] == "medium"

    def test_a_container_that_becomes_a_single_thing_loses_its_size(self, client):
        # "Large item" means nothing, and the size picker is not shown for one.
        code = made(client, kind="box", size="large")

        client.patch(f"/api/boxes/{code}", json={"kind": "furniture"})

        assert client.get(f"/api/boxes/{code}").json()["size"] is None

    def test_changing_between_containers_keeps_it(self, client):
        code = made(client, kind="box", size="large")

        client.patch(f"/api/boxes/{code}", json={"kind": "crate"})

        assert client.get(f"/api/boxes/{code}").json()["size"] == "large"

    def test_a_single_thing_cannot_be_given_one(self, client):
        code = made(client, kind="item")

        response = client.patch(f"/api/boxes/{code}", json={"size": "large"})

        assert response.status_code == 422

    def test_it_is_in_the_export(self, client):
        made(client, size="extra large")

        assert "extra large" in client.get("/api/export.csv").text
        assert "size" in client.get("/api/export.csv").text.splitlines()[0]

    def test_the_store_checks_it_too(self, conn):
        # The CLI writes through the store, not the API.
        with pytest.raises(ValueError):
            store.create_box(conn, size="enormous")
