"""The "From contents" button: GET /api/boxes/{code}/summary-suggestion.

Proposed, never applied -- the page puts the line in the field and the
autosaver decides the rest. Two things are being pinned here: that what is
nested inside a container counts as its contents, and that a model writing the
line is an *improvement* on the assembled one rather than a dependency. Every
route to "the model did not answer" has to end in the assembled line.

No test reaches a model: a Config built directly has `phrase_summaries` off,
and the ones that want a phraser pass their own.
"""

import pytest
from fastapi.testclient import TestClient

from movingbox.api import app as app_module
from movingbox.api.app import create_app


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def empty(client):
    return client.post("/api/boxes", json={}).json()["code"]


def phrased(client, answer):
    """Put a phraser behind the endpoint. `answer` may be an exception."""

    class Says:
        name = "stub"

        def __init__(self):
            self.seen = None

        def phrase(self, contents, *, model):
            self.seen = contents
            if isinstance(answer, Exception):
                raise answer
            return answer

    said = Says()
    client.app.dependency_overrides[app_module.get_phraser] = lambda: said
    return said


def stock(client, code, *names):
    for name in names:
        client.post(f"/api/boxes/{code}/items", json={"name": name})


class TestTheAssembledSummary:
    def test_a_summary_is_suggested_from_the_items(self, client, empty):
        client.post(f"/api/boxes/{empty}/items", json={"name": "baking pan", "qty": 3})
        client.post(f"/api/boxes/{empty}/items", json={"name": "kettle"})

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion["summary"] == "3 baking pans, kettle"

    def test_suggesting_does_not_change_the_box(self, client, empty):
        # Same rule as the photo draft: propose, never apply.
        client.post(f"/api/boxes/{empty}/items", json={"name": "kettle"})

        client.get(f"/api/boxes/{empty}/summary-suggestion")

        assert client.get(f"/api/boxes/{empty}").json()["content_summary"] is None

    def test_what_is_nested_inside_is_suggested_too(self, client, empty):
        # Three bags in a crate: "3 bags", not an empty suggestion and not
        # "bag, bag, bag".
        for _ in range(3):
            client.post("/api/boxes", json={"kind": "bag", "parent_code": empty})

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion["summary"] == "3 bags"

    def test_a_nested_record_offers_its_own_summary_to_its_container(self, client, empty):
        client.post(
            "/api/boxes",
            json={"kind": "bag", "parent_code": empty, "content_summary": "winter coats"},
        )

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion["summary"] == "winter coats"

    def test_a_box_with_no_items_suggests_nothing(self, client, empty):
        assert client.get(f"/api/boxes/{empty}/summary-suggestion").json()["summary"] == ""

    def test_an_unknown_box_is_404(self, client):
        assert client.get("/api/boxes/B-9999/summary-suggestion").status_code == 404


class TestWhenAModelWritesIt:
    def test_the_model_line_is_used_and_labelled_as_its_own(self, client, empty):
        stock(client, empty, "stock pot", "stand mixer", "baking pan", "colander")
        phrased(client, "Kitchen gear - a stock pot, a stand mixer and baking pans")

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion == {
            "summary": "Kitchen gear - a stock pot, a stand mixer and baking pans",
            "source": "model",
        }

    def test_the_model_is_shown_what_is_nested_inside_as_well(self, client, empty):
        stock(client, empty, "kettle", "toaster")
        for _ in range(3):
            client.post("/api/boxes", json={"kind": "bag", "parent_code": empty})
        said = phrased(client, "Kitchen things and three bags")

        client.get(f"/api/boxes/{empty}/summary-suggestion")

        assert [thing["name"] for thing in said.seen] == ["kettle", "toaster", "bag"]
        assert said.seen[-1]["qty"] == 3

    def test_a_model_that_does_not_answer_costs_nothing_but_the_phrasing(self, client, empty):
        # The whole point: offline, the button still works.
        stock(client, empty, "stock pot", "stand mixer", "baking pan")
        phrased(client, ConnectionError("nothing answered at http://localhost:11434"))

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion == {
            "summary": "stock pot, stand mixer, baking pan",
            "source": "assembled",
        }

    def test_an_empty_box_is_never_sent_to_a_model(self, client, empty):
        said = phrased(client, "Empty - nothing at all")

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion == {"summary": "", "source": "assembled"}
        assert said.seen is None


class TestNoTestReachesAModel:
    def test_the_suite_assembles_rather_than_phrasing(self, client, empty):
        # A Config built directly has phrase_summaries off, so the endpoint
        # gets no phraser at all. If this fails, every press of the button in
        # the suite is a call to Ollama.
        stock(client, empty, "stock pot", "stand mixer", "baking pan", "colander")

        suggestion = client.get(f"/api/boxes/{empty}/summary-suggestion").json()

        assert suggestion["source"] == "assembled"

    def test_there_is_no_phraser_unless_the_config_asks_for_one(self, config):
        assert app_module.build_phraser(config) is None

    def test_the_stub_provider_phrases_without_a_model(self, config):
        # What MOVING_VISION_PROVIDER=stub gives the browser checks.
        stubbed = config.replace(phrase_summaries=True, vision_provider="stub")

        phraser = app_module.build_phraser(stubbed)

        assert phraser.name == "stub"
        assert phraser.phrase([{"name": "kettle", "qty": 1}], model="m").startswith("Assorted")
