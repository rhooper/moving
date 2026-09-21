"""Box lifecycle operations: creation, edits, status, location, items, events."""

import pytest

from movingbox import search, store


def test_creating_a_box_allocates_the_next_code(conn):
    assert store.create_box(conn)["code"] == "B-0001"
    assert store.create_box(conn)["code"] == "B-0002"


def test_a_new_box_starts_open(conn):
    assert store.create_box(conn)["status"] == "open"


def test_a_new_box_is_immediately_searchable(conn):
    # Packing is fast; a box you just made must be findable without a reindex step.
    box = store.create_box(conn, content_summary="winter coats")

    assert search.search(conn, "winter") == [box["id"]]


def test_creating_a_box_records_a_create_event(conn):
    box = store.create_box(conn)

    kinds = [e["kind"] for e in store.events_for(conn, box["code"])]
    assert kinds == ["create"]


def test_editing_the_summary_updates_what_search_finds(conn):
    box = store.create_box(conn, content_summary="winter coats")

    store.update_box(conn, box["code"], content_summary="garden tools")

    assert search.search(conn, "garden") == [box["id"]]
    assert search.search(conn, "winter") == []


def test_advancing_status_records_the_transition(conn):
    box = store.create_box(conn)

    store.set_status(conn, box["code"], "packed")

    event = store.events_for(conn, box["code"])[-1]
    assert (event["kind"], event["from_value"], event["to_value"]) == ("status", "open", "packed")
    assert store.get_box(conn, box["code"])["status"] == "packed"


def test_setting_status_to_its_current_value_records_nothing(conn):
    box = store.create_box(conn)
    store.set_status(conn, box["code"], "packed")

    store.set_status(conn, box["code"], "packed")

    status_events = [e for e in store.events_for(conn, box["code"]) if e["kind"] == "status"]
    assert len(status_events) == 1


def test_an_unknown_status_is_rejected(conn):
    box = store.create_box(conn)

    with pytest.raises(store.InvalidStatus):
        store.set_status(conn, box["code"], "teleporting")


def test_moving_a_box_records_the_location_change(conn):
    box = store.create_box(conn)
    store.set_location(conn, box["code"], "truck")

    store.set_location(conn, box["code"], "garage stack 3")

    event = store.events_for(conn, box["code"])[-1]
    assert (event["from_value"], event["to_value"]) == ("truck", "garage stack 3")


def test_current_location_is_independent_of_destination_room(conn):
    # A box bound for the kitchen can be on the truck; asking where it is must
    # not answer where it is going.
    room_id = store.create_room(conn, "Kitchen")["id"]
    box = store.create_box(conn, destination_room_id=room_id)

    store.set_location(conn, box["code"], "truck")

    fetched = store.get_box(conn, box["code"])
    assert fetched["current_location"] == "truck"
    assert fetched["destination_room_id"] == room_id


def test_adding_an_item_makes_the_box_findable_by_it(conn):
    box = store.create_box(conn)

    store.add_item(conn, box["code"], name="cafetiere")

    assert search.search(conn, "cafetiere") == [box["id"]]


def test_items_are_manual_unless_stated_otherwise(conn):
    box = store.create_box(conn)

    assert store.add_item(conn, box["code"], name="kettle")["source"] == "manual"
    assert store.add_item(conn, box["code"], name="urn", source="ai")["source"] == "ai"


def test_removing_an_item_stops_it_matching(conn):
    box = store.create_box(conn)
    item = store.add_item(conn, box["code"], name="cafetiere")

    store.delete_item(conn, item["id"])

    assert search.search(conn, "cafetiere") == []


def test_deleting_a_box_removes_it_from_search(conn, config):
    box = store.create_box(conn, content_summary="winter coats")

    assert store.delete_box(conn, config, box["code"]) is True
    assert search.search(conn, "winter") == []
    assert store.get_box(conn, box["code"]) is None


def test_operating_on_an_unknown_box_raises(conn):
    with pytest.raises(store.UnknownBox):
        store.set_status(conn, "B-9999", "packed")


def test_listing_filters_by_status(conn):
    packed = store.create_box(conn)
    store.set_status(conn, packed["code"], "packed")
    store.create_box(conn)  # stays open

    found = store.list_boxes(conn, status="packed")

    assert [b["code"] for b in found] == [packed["code"]]


def test_listing_filters_by_destination_room(conn):
    kitchen = store.create_room(conn, "Kitchen")["id"]
    wanted = store.create_box(conn, destination_room_id=kitchen)
    store.create_box(conn)

    found = store.list_boxes(conn, room_id=kitchen)

    assert [b["code"] for b in found] == [wanted["code"]]


def test_listing_filters_by_free_text(conn):
    wanted = store.create_box(conn, content_summary="espresso machine")
    store.create_box(conn, content_summary="garden tools")

    found = store.list_boxes(conn, q="espresso")

    assert [b["code"] for b in found] == [wanted["code"]]


def test_listing_combines_a_text_query_with_a_filter(conn):
    loaded = store.create_box(conn, content_summary="espresso machine")
    store.set_status(conn, loaded["code"], "loaded")
    store.create_box(conn, content_summary="espresso cups")  # matches text, wrong status

    found = store.list_boxes(conn, q="espresso", status="loaded")

    assert [b["code"] for b in found] == [loaded["code"]]
