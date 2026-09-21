"""How many copies of a label print when a request says, and what gets counted.

What prints when a request does *not* say is per kind of thing, in
test_kind_copies_and_size.py.
"""

import pytest
from fastapi.testclient import TestClient

from movingbox.api.app import create_app
from movingbox.labels import printer


class Spy:
    """A printer backend that remembers what it was asked for."""

    def __init__(self):
        self.jobs = []

    def print_label(self, image, *, code, copies=1):
        self.jobs.append((code, copies, image.size))
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


def a_box(client, **fields):
    return client.post("/api/boxes", json={"content_summary": "pots", **fields}).json()["code"]


class TestTheDefault:
    def test_a_box_that_does_not_say_gets_two(self, client, spy):
        code = a_box(client)

        response = client.post("/api/labels/print", json={"codes": [code]})

        assert [(c, n) for c, n, _ in spy.jobs] == [(code, 2)]
        assert response.json()["printed"][0]["copies"] == 2

    def test_a_print_that_says_is_obeyed(self, client, spy):
        code = a_box(client)

        client.post("/api/labels/print", json={"codes": [code], "copies": 1})

        assert [(c, n) for c, n, _ in spy.jobs] == [(code, 1)]

    def test_the_stub_is_one_unless_asked(self, client, spy):
        # The default is about labelling more than one face of a packed box. A
        # stub goes on an empty one, once.
        code = a_box(client)

        client.post("/api/labels/print", json={"codes": [code], "stub": True})
        client.post("/api/labels/print", json={"codes": [code], "stub": True, "copies": 3})

        assert [n for _, n, _ in spy.jobs] == [1, 3]


class TestWhatAPrintMayAsk:
    @pytest.mark.parametrize("bad", [0, 11])
    def test_a_print_cannot_ask_for_nonsense_either(self, client, spy, bad):
        code = a_box(client)

        response = client.post("/api/labels/print", json={"codes": [code], "copies": bad})

        assert response.status_code == 422
        assert spy.jobs == []


class TestTheCount:
    def test_it_counts_labels_not_button_presses(self, client, spy):
        # The count explains why several labels with one code are in circulation.
        code = a_box(client)

        client.post("/api/labels/print", json={"codes": [code]})
        client.post("/api/labels/print", json={"codes": [code], "copies": 1})

        assert client.get(f"/api/boxes/{code}").json()["label_print_count"] == 3
