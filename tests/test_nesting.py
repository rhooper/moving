"""Things inside things: a bag in a box in a crate.

Nesting is arbitrarily deep. Nested records stay out of the top-level list but
are found by search.
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

    def test_each_step_out_says_where_it_is_going_and_whether_it_is_fragile(self, client):
        # A nested record goes where its container goes, and a fragile thing
        # makes its containers fragile: the page needs both from the path.
        room = client.post("/api/rooms", json={"name": "Kitchen"}).json()["id"]
        crate = made(client, kind="crate", destination_room_id=room, fragile=True)
        box = made(client, kind="box", parent_code=crate)
        bag = made(client, kind="bag", parent_code=box)

        path = client.get(f"/api/boxes/{bag}").json()["path"]

        assert [(s["code"], s["destination_room_id"], s["fragile"]) for s in path] == [
            (crate, room, 1),
            (box, None, 0),
        ]

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


class TestTheLabel:
    def test_a_nested_record_with_no_room_prints_its_containers_room(self, client, conn, config):
        from movingbox.api.labels import _label_for

        room = client.post("/api/rooms", json={"name": "Kitchen"}).json()["id"]
        crate = made(client, kind="crate", destination_room_id=room)
        box = made(client, kind="box", parent_code=crate)
        bag = made(client, kind="bag", parent_code=box)

        assert _label_for(conn, bag, config).room == "Kitchen"

    def test_the_containers_room_wins_over_a_room_of_its_own(self, client, conn, config):
        # A garage box put in a kitchen crate goes to the kitchen. Its own room
        # stays in the database for when it is taken out again.
        from movingbox.api.labels import _label_for

        kitchen = client.post("/api/rooms", json={"name": "Kitchen"}).json()["id"]
        garage = client.post("/api/rooms", json={"name": "Garage"}).json()["id"]
        crate = made(client, kind="crate", destination_room_id=kitchen)
        own = made(client, kind="box", parent_code=crate, destination_room_id=garage)

        assert _label_for(conn, own, config).room == "Kitchen"
        assert client.get(f"/api/boxes/{own}").json()["destination_room_id"] == garage
        client.patch(f"/api/boxes/{own}", json={"parent_code": None})
        assert _label_for(conn, own, config).room == "Garage"

    def test_no_container_with_a_room_falls_back_to_its_own(self, client, conn, config):
        from movingbox.api.labels import _label_for

        garage = client.post("/api/rooms", json={"name": "Garage"}).json()["id"]
        crate = made(client, kind="crate")
        own = made(client, kind="box", parent_code=crate, destination_room_id=garage)
        bare = made(client, kind="bag", parent_code=crate)
        alone = made(client, kind="box")

        assert _label_for(conn, own, config).room == "Garage"
        assert _label_for(conn, bare, config).room is None
        assert _label_for(conn, alone, config).room is None


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

        # The match leads; the crate comes with it so the page can group them.
        assert codes(found) == [bag, crate]
        assert found[0]["parent_code"] == crate

    def test_search_brings_back_the_containers_a_match_is_inside(self, client):
        # The page groups results under their containers, so the containers
        # come back too, without a request per result.
        crate = made(client, kind="crate", content_summary="kitchen")
        box = made(client, kind="box", content_summary="tea things", parent_code=crate)
        bag = made(client, kind="bag", content_summary="the samovar", parent_code=box)

        found = client.get("/api/search", params={"q": "samovar"}).json()

        # Matches first, in relevance order, then the containers they need.
        assert codes(found)[0] == bag
        assert sorted(codes(found)) == sorted([bag, box, crate])
        seen = {row["code"]: row for row in found}
        assert seen[bag]["matched"] is True
        assert seen[box]["matched"] is False and seen[crate]["matched"] is False
        assert seen[bag]["ancestry"] == [crate, box]
        assert seen[box]["ancestry"] == [crate]
        assert seen[crate]["ancestry"] == []

    def test_a_container_that_matched_comes_back_once_and_as_a_match(self, client):
        # The case that would draw a record twice: it is both a result and the
        # place another result lives.
        crate = made(client, kind="crate", content_summary="kitchen crate")
        bag = made(client, kind="bag", content_summary="kitchen cutlery", parent_code=crate)

        found = client.get("/api/search", params={"q": "kitchen"}).json()

        assert sorted(codes(found)) == sorted([crate, bag])
        assert {row["code"]: row["matched"] for row in found} == {crate: True, bag: True}

    def test_a_top_level_match_needs_no_context_and_says_so(self, client):
        loose = made(client, kind="box", content_summary="a lone kettle")

        found = client.get("/api/search", params={"q": "kettle"}).json()

        assert codes(found) == [loose]
        assert found[0]["ancestry"] == [] and found[0]["matched"] is True

    def test_the_matches_keep_their_order_and_a_container_is_not_counted_as_one(self, client):
        # Whatever else comes back, the heading counts what was found.
        crate = made(client, kind="crate", content_summary="kitchen")
        made(client, kind="bag", content_summary="samovar", parent_code=crate)

        found = client.get("/api/search", params={"q": "samovar"}).json()

        # Context rows are not results. Matches lead, so the first row is still
        # the best match.
        assert sum(1 for row in found if row["matched"]) == 1
        assert found[0]["matched"] is True
        assert [row["matched"] for row in found] == sorted(
            (row["matched"] for row in found), reverse=True
        )

    def test_browsing_is_untouched_by_any_of_it(self, client):
        crate = made(client, kind="crate")
        made(client, kind="bag", parent_code=crate)

        rows = client.get("/api/boxes").json()

        assert codes(rows) == [crate]

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


class TestGatheringTheWholeSubtree:
    """`store.subtree` is the material a summary is built from.

    Not just what is directly inside: a crate holding a box of twenty things
    can say what those things are.
    """

    def nest(self, conn, *, depth):
        """A chain of containers, each holding the next, outermost first."""
        codes_made = []
        parent = None
        for level in range(depth):
            box = store.create_box(conn, kind="box", parent_code=parent)
            store.add_item(conn, box["code"], name=f"thing at depth {level}")
            codes_made.append(box["code"])
            parent = box["code"]
        return codes_made

    def test_the_record_itself_comes_first_at_depth_zero(self, conn):
        box = store.create_box(conn)
        store.add_item(conn, box["code"], name="kettle")

        nodes = store.subtree(conn, box["code"])

        assert [n["depth"] for n in nodes] == [0]
        assert [i["name"] for i in nodes[0]["items"]] == ["kettle"]

    def test_children_and_grandchildren_come_with_their_depth(self, conn):
        outer, middle, inner = self.nest(conn, depth=3)

        nodes = store.subtree(conn, outer)

        assert [(n["code"], n["depth"]) for n in nodes] == [
            (outer, 0),
            (middle, 1),
            (inner, 2),
        ]

    def test_each_node_carries_its_own_items(self, conn):
        outer, middle, _ = self.nest(conn, depth=3)

        nodes = store.subtree(conn, outer)

        assert [i["name"] for i in nodes[1]["items"]] == ["thing at depth 1"]

    def test_it_carries_what_a_record_is_and_how_its_summary_was_written(self, conn):
        box = store.create_box(conn, kind="crate", size="large", content_summary="tea things")

        node = store.subtree(conn, box["code"])[0]

        assert (node["kind"], node["size"]) == ("crate", "large")
        assert node["summary_source"] in ("manual", "auto")

    def test_each_node_says_what_it_is_inside(self, conn):
        # summarise.contents needs this to tell an empty bag (worth naming)
        # from a box with books in it (whose books are in the list already).
        outer, middle, inner = self.nest(conn, depth=3)

        nodes = {n["code"]: n for n in store.subtree(conn, outer)}

        assert nodes[outer]["parent_id"] is None
        assert nodes[middle]["parent_id"] == nodes[outer]["id"]
        assert nodes[inner]["parent_id"] == nodes[middle]["id"]

    def test_it_stops_at_the_depth_limit(self, conn):
        # A guard, not a feature: the store forbids a cycle, but a button
        # press should never be able to raise a recursion error.
        made_codes = self.nest(conn, depth=6)

        nodes = store.subtree(conn, made_codes[0], max_depth=2)

        assert [n["depth"] for n in nodes] == [0, 1, 2]

    def test_a_binned_descendant_is_left_out(self, conn, config):
        # Only a leaf can go: the store refuses to bin a container with things
        # still inside it, which is why nothing dangles below one.
        outer, middle, inner = self.nest(conn, depth=3)
        store.delete_box(conn, config, inner)

        nodes = store.subtree(conn, outer)

        assert [n["code"] for n in nodes] == [outer, middle]

    def test_an_unknown_record_gathers_nothing(self, conn):
        assert store.subtree(conn, "B-9999") == []

    def test_the_whole_subtree_costs_a_fixed_number_of_queries(self, conn):
        # One recursive CTE plus one pass for the items, not two queries per
        # node on a button someone is waiting on.
        self.nest(conn, depth=6)
        wide = store.create_box(conn)
        for _ in range(8):
            child = store.create_box(conn, parent_code=wide["code"])
            for n in range(5):
                store.add_item(conn, child["code"], name=f"thing {n}")

        statements = []
        conn.set_trace_callback(statements.append)
        try:
            store.subtree(conn, wide["code"])
        finally:
            conn.set_trace_callback(None)

        assert len(statements) <= 3, statements


class TestTheExpandedContentsList:
    """What the read-only view shows when its contents line is opened.

    A separate request, made only on opening it: B-0015 is 59 items of its own
    plus twenty tubs' worth, and putting that on every record GET would slow
    down opening any record for a list most people never open.
    """

    def test_it_lists_the_record_s_own_items_first(self, client):
        crate = made(client, kind="crate")
        client.post(f"/api/boxes/{crate}/items", json={"name": "tape"})
        tub = made(client, kind="tub", parent_code=crate)
        client.post(f"/api/boxes/{tub}/items", json={"name": "wire"})

        groups = client.get(f"/api/boxes/{crate}/contents").json()

        assert [g["code"] for g in groups] == [crate, tub]
        assert [i["name"] for i in groups[0]["items"]] == ["tape"]

    def test_it_reaches_past_the_things_directly_inside(self, client):
        crate = made(client, kind="crate")
        tub = made(client, kind="tub", parent_code=crate)
        bag = made(client, kind="bag", parent_code=tub)
        client.post(f"/api/boxes/{bag}/items", json={"name": "clips"})

        groups = client.get(f"/api/boxes/{crate}/contents").json()

        assert [g["code"] for g in groups] == [bag]

    def test_repeats_in_one_record_are_one_line_summed(self, client):
        box = made(client)
        client.post(f"/api/boxes/{box}/items", json={"name": "resistors", "qty": 3})
        client.post(f"/api/boxes/{box}/items", json={"name": "Resistor"})

        groups = client.get(f"/api/boxes/{box}/contents").json()

        assert groups[0]["items"] == [{"name": "resistors", "qty": 4}]

    def test_a_record_with_nothing_listed_anywhere_has_no_groups(self, client):
        assert client.get(f"/api/boxes/{made(client)}/contents").json() == []

    def test_an_unknown_code_is_a_404(self, client):
        assert client.get("/api/boxes/B-9999/contents").status_code == 404
