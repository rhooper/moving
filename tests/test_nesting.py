"""Things inside things: a bag in a box in a crate.

Asked for: nested containers, which generally will not have a label of their
own. A record with things inside shows them; nesting is arbitrarily deep,
because the nested box can be opened like any other; nested records stay out of
the top-level list (for now) but are found by search.
"""

import pytest
from fastapi.testclient import TestClient

from movingbox import db, store
from movingbox.api.app import create_app


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
    response = client.post("/api/boxes", json=fields)
    assert response.status_code == 201, response.text
    return response.json()["code"]


def codes(rows):
    return [row["code"] for row in rows]


class TestPuttingOneThingInsideAnother:
    def test_a_record_can_be_created_inside_a_container(self, client):
        crate = made(client, kind="crate", content_summary="kitchen")

        bag = made(client, kind="bag", content_summary="cutlery", parent_code=crate)

        assert client.get(f"/api/boxes/{bag}").json()["parent"]["code"] == crate

    def test_an_existing_record_can_be_moved_inside_one(self, client):
        crate, bag = made(client, kind="crate"), made(client, kind="bag")

        response = client.patch(f"/api/boxes/{bag}", json={"parent_code": crate})

        assert response.status_code == 200
        assert response.json()["parent"]["code"] == crate

    def test_and_taken_out_again(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", parent_code=crate)

        client.patch(f"/api/boxes/{bag}", json={"parent_code": None})

        assert client.get(f"/api/boxes/{bag}").json()["parent"] is None

    def test_anything_can_go_inside_a_container_not_only_containers(self, client):
        box = made(client, kind="box")

        lamp = made(client, kind="item", content_summary="Desk lamp", parent_code=box)

        assert client.get(f"/api/boxes/{lamp}").json()["parent"]["code"] == box

    def test_nothing_can_go_inside_a_single_thing(self, client):
        lamp = made(client, kind="item", content_summary="Desk lamp")

        response = client.post("/api/boxes", json={"kind": "bag", "parent_code": lamp})

        assert response.status_code == 422
        assert "container" in response.json()["detail"]

    def test_the_parent_has_to_exist(self, client):
        assert client.post("/api/boxes", json={"parent_code": "B-9999"}).status_code == 422

    def test_not_inside_itself(self, client):
        box = made(client, kind="box")

        assert client.patch(f"/api/boxes/{box}", json={"parent_code": box}).status_code == 422

    def test_not_inside_something_that_is_inside_it(self, client):
        # crate > box > bag, then trying crate inside bag.
        crate = made(client, kind="crate")
        box = made(client, kind="box", parent_code=crate)
        bag = made(client, kind="bag", parent_code=box)

        response = client.patch(f"/api/boxes/{crate}", json={"parent_code": bag})

        assert response.status_code == 422
        assert "inside" in response.json()["detail"]

    def test_not_inside_something_in_the_bin(self, client):
        crate, bag = made(client, kind="crate"), made(client, kind="bag")
        client.delete(f"/api/boxes/{crate}")

        assert client.patch(f"/api/boxes/{bag}", json={"parent_code": crate}).status_code == 422

    def test_a_container_holding_things_cannot_become_a_single_thing(self, client):
        box = made(client, kind="box")
        made(client, kind="bag", parent_code=box)

        response = client.patch(f"/api/boxes/{box}", json={"kind": "furniture"})

        assert response.status_code == 422


class TestWhatTheParentShows:
    def test_a_container_lists_what_is_inside_it(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", content_summary="cutlery", parent_code=crate)
        tin = made(client, kind="box", content_summary="tea", parent_code=crate)

        inside = client.get(f"/api/boxes/{crate}").json()["children"]

        assert codes(inside) == [bag, tin]
        assert inside[0]["content_summary"] == "cutlery"
        assert {"kind", "status", "cover_photo_id", "child_count"} <= set(inside[0])

    def test_only_its_own_level(self, client):
        crate = made(client, kind="crate")
        box = made(client, kind="box", parent_code=crate)
        bag = made(client, kind="bag", parent_code=box)

        assert codes(client.get(f"/api/boxes/{crate}").json()["children"]) == [box]
        assert codes(client.get(f"/api/boxes/{box}").json()["children"]) == [bag]
        assert client.get(f"/api/boxes/{crate}").json()["children"][0]["child_count"] == 1

    def test_something_in_the_bin_is_not_listed_inside(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", parent_code=crate)
        client.delete(f"/api/boxes/{bag}")

        assert client.get(f"/api/boxes/{crate}").json()["children"] == []

    def test_a_nested_record_knows_the_way_out(self, client):
        # For a breadcrumb: outermost first, arbitrarily deep.
        crate = made(client, kind="crate", content_summary="kitchen")
        box = made(client, kind="box", parent_code=crate)
        bag = made(client, kind="bag", parent_code=box)

        path = client.get(f"/api/boxes/{bag}").json()["path"]

        assert codes(path) == [crate, box]
        assert path[0]["content_summary"] == "kitchen"

    def test_a_top_level_record_has_no_way_out_and_nothing_inside(self, client):
        box = client.get(f"/api/boxes/{made(client)}").json()

        assert (box["parent"], box["path"], box["children"]) == (None, [], [])

    def test_what_is_inside_counts_as_contents(self, client):
        # A crate of three bags says enough about itself to be worth a label,
        # even with nothing typed and no items of its own.
        crate = made(client, kind="crate")
        assert client.post("/api/labels/print", json={"codes": [crate]}).status_code == 409

        made(client, kind="bag", parent_code=crate)

        assert client.post("/api/labels/print", json={"codes": [crate]}).status_code == 200


class TestTheListAndSearch:
    def test_nested_records_stay_out_of_the_top_level_list(self, client):
        crate = made(client, kind="crate")
        made(client, kind="bag", parent_code=crate)
        loose = made(client, kind="box")

        assert sorted(codes(client.get("/api/boxes").json())) == sorted([crate, loose])

    def test_the_list_says_how_much_is_inside(self, client):
        crate = made(client, kind="crate")
        made(client, kind="bag", parent_code=crate)
        made(client, kind="bag", parent_code=crate)

        row = client.get("/api/boxes").json()[0]

        assert (row["code"], row["child_count"]) == (crate, 2)

    def test_search_finds_them_and_says_where_they_are(self, client):
        crate = made(client, kind="crate", content_summary="kitchen")
        bag = made(client, kind="bag", content_summary="samovar", parent_code=crate)

        found = client.get("/api/search", params={"q": "samovar"}).json()

        assert codes(found) == [bag]
        assert found[0]["parent_code"] == crate

    def test_taking_something_out_puts_it_back_in_the_list(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", parent_code=crate)

        client.patch(f"/api/boxes/{bag}", json={"parent_code": None})

        assert bag in codes(client.get("/api/boxes").json())

    def test_a_scanned_label_opens_a_nested_record_like_any_other(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", parent_code=crate)

        assert client.get(f"/b/{bag}", follow_redirects=False).status_code == 307


class TestDeleting:
    def test_a_container_with_things_inside_cannot_be_deleted(self, client):
        # Binning it would strand what is inside: out of the list because
        # nested, and out of reach because the parent is gone.
        crate = made(client, kind="crate")
        made(client, kind="bag", parent_code=crate)

        response = client.delete(f"/api/boxes/{crate}")

        assert response.status_code == 409
        assert "inside" in response.json()["detail"]
        assert client.get(f"/api/boxes/{crate}").json()["deleted_at"] is None

    def test_emptied_it_can_be(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", parent_code=crate)
        client.patch(f"/api/boxes/{bag}", json={"parent_code": None})

        assert client.delete(f"/api/boxes/{crate}").status_code == 204

    def test_a_nested_record_can_be_deleted_and_restored_where_it_was(self, client):
        crate = made(client, kind="crate")
        bag = made(client, kind="bag", parent_code=crate)

        client.delete(f"/api/boxes/{bag}")
        client.post(f"/api/boxes/{bag}/restore")

        assert codes(client.get(f"/api/boxes/{crate}").json()["children"]) == [bag]


class TestTellingOtherDevices:
    def test_both_ends_of_a_move_are_announced(self, client, config):
        told = []
        client.app.state.events.publish = lambda kind, code=None, **kw: told.append((kind, code))
        crate, tub = made(client, kind="crate"), made(client, kind="tub")
        bag = made(client, kind="bag", parent_code=crate)
        told.clear()

        client.patch(f"/api/boxes/{bag}", json={"parent_code": tub})

        # The bag moved; the crate lost something; the tub gained something.
        assert {code for kind, code in told if kind == "box.updated"} == {bag, crate, tub}


class TestTheStore:
    def test_the_export_says_what_each_thing_is_inside(self, client):
        crate = made(client, kind="crate")
        made(client, kind="bag", content_summary="cutlery", parent_code=crate)

        lines = client.get("/api/export.csv").text.splitlines()

        assert "parent_code" in lines[0]
        assert any("cutlery" in line and crate in line for line in lines[1:])

    def test_nesting_goes_through_the_store_for_the_cli_too(self, conn):
        crate = store.create_box(conn, kind="crate")
        bag = store.create_box(conn, kind="bag")

        store.set_parent(conn, bag["code"], crate["code"])

        assert [c["code"] for c in store.children_of(conn, crate["code"])] == [bag["code"]]
        with pytest.raises(ValueError):
            store.set_parent(conn, crate["code"], bag["code"])
