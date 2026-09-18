"""HTTP surface: CRUD, status/location transitions, search and auth."""

import pytest
from fastapi.testclient import TestClient

from movingbox.api import app as app_module
from movingbox.api.app import create_app


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


def test_creating_a_box_returns_its_allocated_code(client):
    response = client.post("/api/boxes", json={"content_summary": "winter coats"})

    assert response.status_code == 201
    assert response.json()["code"] == "B-0001"


def test_a_created_box_can_be_fetched_back(client):
    code = client.post("/api/boxes", json={"content_summary": "winter coats"}).json()["code"]

    response = client.get(f"/api/boxes/{code}")

    assert response.status_code == 200
    assert response.json()["content_summary"] == "winter coats"


def test_fetching_an_unknown_box_is_404(client):
    assert client.get("/api/boxes/B-9999").status_code == 404


def test_patching_a_box_changes_it(client):
    code = client.post("/api/boxes", json={}).json()["code"]

    response = client.patch(f"/api/boxes/{code}", json={"content_summary": "garden tools"})

    assert response.status_code == 200
    assert response.json()["content_summary"] == "garden tools"


def test_status_cannot_be_smuggled_through_patch(client):
    # Editing status directly would bypass the event log, leaving a hole in the
    # record of where a box has been.
    code = client.post("/api/boxes", json={}).json()["code"]

    response = client.patch(f"/api/boxes/{code}", json={"status": "loaded"})

    assert response.status_code == 422
    assert client.get(f"/api/boxes/{code}").json()["status"] == "open"


def test_advancing_status_is_recorded_in_the_timeline(client):
    code = client.post("/api/boxes", json={}).json()["code"]

    response = client.post(f"/api/boxes/{code}/status", json={"status": "packed"})

    assert response.status_code == 200
    assert response.json()["status"] == "packed"
    events = client.get(f"/api/boxes/{code}/events").json()
    assert [e["kind"] for e in events] == ["create", "status"]


def test_an_invalid_status_is_rejected(client):
    code = client.post("/api/boxes", json={}).json()["code"]

    response = client.post(f"/api/boxes/{code}/status", json={"status": "teleporting"})

    assert response.status_code == 422


def test_moving_a_box_records_its_new_location(client):
    code = client.post("/api/boxes", json={}).json()["code"]

    response = client.post(f"/api/boxes/{code}/location", json={"current_location": "truck"})

    assert response.status_code == 200
    assert response.json()["current_location"] == "truck"


def test_items_can_be_added_and_listed(client):
    code = client.post("/api/boxes", json={}).json()["code"]

    client.post(f"/api/boxes/{code}/items", json={"name": "cafetiere", "qty": 1})

    names = [i["name"] for i in client.get(f"/api/boxes/{code}/items").json()]
    assert names == ["cafetiere"]


def test_search_finds_a_box_by_an_item_inside_it(client):
    code = client.post("/api/boxes", json={}).json()["code"]
    client.post(f"/api/boxes/{code}/items", json={"name": "cafetiere"})

    found = client.get("/api/search", params={"q": "cafetiere"}).json()

    assert [b["code"] for b in found] == [code]


def test_listing_boxes_filters_by_status(client):
    packed = client.post("/api/boxes", json={}).json()["code"]
    client.post(f"/api/boxes/{packed}/status", json={"status": "packed"})
    client.post("/api/boxes", json={})

    found = client.get("/api/boxes", params={"status": "packed"}).json()

    assert [b["code"] for b in found] == [packed]


def test_deleting_a_box_takes_it_out_of_the_list(client):
    # Reversible: the record stays reachable by its code so it can be
    # restored. See test_soft_delete.py.
    code = client.post("/api/boxes", json={}).json()["code"]

    assert client.delete(f"/api/boxes/{code}").status_code == 204
    assert client.get("/api/boxes").json() == []


def test_rooms_can_be_created_and_listed(client):
    assert client.post("/api/rooms", json={"name": "Kitchen"}).status_code == 201

    assert [r["name"] for r in client.get("/api/rooms").json()] == ["Kitchen"]


def test_a_box_scanned_by_its_code_resolves_to_its_page(client):
    code = client.post("/api/boxes", json={}).json()["code"]

    response = client.get(f"/b/{code}", follow_redirects=False)

    assert response.status_code in (200, 307)


class TestDeployedRevision:
    """/health reports the commit the process started from.

    This is what scripts/claude/deploy.sh checks to tell "the service restarted
    on the new code" from "the service is up", which are not the same thing --
    the whole reason the deploy is automated is that finished work kept sitting
    merged but not actually served.
    """

    def test_health_reports_the_revision_recorded_at_startup(self, config, tmp_path, monkeypatch):
        recorded = tmp_path / "deployed-revision"
        recorded.write_text("0d302631c0ffee\n")
        monkeypatch.setattr(app_module, "REVISION_FILE", recorded)

        with TestClient(create_app(config)) as c:
            assert c.get("/health").json() == {"status": "ok", "revision": "0d302631c0ffee"}

    def test_a_missing_file_is_unknown_rather_than_an_error(self, config, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "REVISION_FILE", tmp_path / "nope")

        with TestClient(create_app(config)) as c:
            assert c.get("/health").json()["revision"] == "unknown"

    def test_the_revision_is_not_re_read_per_request(self, config, tmp_path, monkeypatch):
        # Writing the file is not deploying: the file is written *before* the
        # restart, so a per-request read would claim the new build was live
        # while the old process was still serving.
        recorded = tmp_path / "deployed-revision"
        recorded.write_text("old\n")
        monkeypatch.setattr(app_module, "REVISION_FILE", recorded)

        with TestClient(create_app(config)) as c:
            recorded.write_text("new\n")

            assert c.get("/health").json()["revision"] == "old"


class TestAuth:
    @pytest.fixture
    def secured(self, config):
        with TestClient(create_app(config.replace(api_key="s3cret"))) as c:
            yield c

    def test_requests_without_the_key_are_rejected(self, secured):
        assert secured.get("/api/boxes").status_code == 401

    def test_requests_with_the_key_are_allowed(self, secured):
        response = secured.get("/api/boxes", headers={"X-API-Key": "s3cret"})

        assert response.status_code == 200

    def test_a_wrong_key_is_rejected(self, secured):
        assert secured.get("/api/boxes", headers={"X-API-Key": "nope"}).status_code == 401

    def test_health_is_reachable_without_a_key(self, secured):
        # Needed so a launchd/monitoring check does not have to hold the secret.
        assert secured.get("/health").status_code == 200
