"""The live change channel a second device listens on.

The whole point is that a phone in the garage sees a box move without anyone
reloading. What travels is *what changed*, never the change itself: every
client re-fetches through the ordinary REST endpoints, so there stays exactly
one code path deciding what a box looks like, and a missed or duplicated
notification is harmless rather than corrupting.
"""

import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from movingbox.api import events
from movingbox.api.app import create_app


def a_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (120, 90, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def listening(client):
    """One connected client, already past the handshake."""
    with client.websocket_connect("/api/events") as socket:
        yield socket


@pytest.fixture
def code(client):
    return client.post("/api/boxes", json={}).json()["code"]


class TestWhatIsAnnounced:
    def test_a_new_box(self, client, listening):
        code = client.post("/api/boxes", json={}).json()["code"]

        assert listening.receive_json() == {"kind": "box.created", "code": code}

    def test_an_edit(self, client, code, listening):
        client.patch(f"/api/boxes/{code}", json={"content_summary": "kettle"})

        assert listening.receive_json() == {"kind": "box.updated", "code": code}

    def test_a_status_change(self, client, code, listening):
        client.post(f"/api/boxes/{code}/status", json={"status": "packed"})

        assert listening.receive_json() == {"kind": "box.status", "code": code}

    def test_a_move(self, client, code, listening):
        client.post(f"/api/boxes/{code}/location", json={"current_location": "truck"})

        assert listening.receive_json() == {"kind": "box.location", "code": code}

    def test_a_box_being_deleted(self, client, code, listening):
        client.delete(f"/api/boxes/{code}")

        assert listening.receive_json() == {"kind": "box.deleted", "code": code}

    def test_an_item_being_added(self, client, code, listening):
        client.post(f"/api/boxes/{code}/items", json={"name": "kettle"})

        assert listening.receive_json() == {"kind": "items.changed", "code": code}

    def test_an_item_being_removed_names_the_box_not_the_item(self, client, code, listening):
        # The client re-fetches by box code, so an item id would be useless to
        # it -- and the item is gone by the time the message lands anyway.
        item = client.post(f"/api/boxes/{code}/items", json={"name": "kettle"}).json()
        assert listening.receive_json()["kind"] == "items.changed"

        client.delete(f"/api/items/{item['id']}")

        assert listening.receive_json() == {"kind": "items.changed", "code": code}

    def test_a_label_being_printed(self, client, code, listening):
        # allow_empty because the shared fixture box has no contents recorded,
        # and printing one is otherwise refused. This test is about the event,
        # not the gate -- see test_print_gate.py for that.
        client.post("/api/labels/print", json={"codes": [code], "allow_empty": True})

        assert listening.receive_json() == {"kind": "label.printed", "code": code}

    def test_a_photo_arriving(self, client, code, listening):
        client.post(f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")})

        assert listening.receive_json() == {"kind": "photos.changed", "code": code}

    def test_a_photo_being_deleted(self, client, code, listening):
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()
        assert listening.receive_json()["kind"] == "photos.changed"

        client.delete(f"/photos/{photo['id']}")

        assert listening.receive_json() == {"kind": "photos.changed", "code": code}

    def test_a_new_cover_being_chosen(self, client, code, listening):
        # The other phone is looking at the same list, and the picture on the
        # row it is showing has just changed.
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()
        assert listening.receive_json()["kind"] == "photos.changed"

        client.post(f"/photos/{photo['id']}/cover")

        assert listening.receive_json() == {"kind": "photos.changed", "code": code}

    def test_a_read_announces_nothing(self, client, code, listening):
        # Otherwise one phone merely looking at a box would make every other
        # phone refetch. Silence cannot be proven by waiting for it, so make a
        # real change afterwards: the queue preserves order, and if any of
        # these reads had announced something it would arrive first.
        client.get("/api/boxes")
        client.get(f"/api/boxes/{code}")
        client.get(f"/api/boxes/{code}/items")
        client.get(f"/api/boxes/{code}/events")

        client.post(f"/api/boxes/{code}/status", json={"status": "packed"})

        assert listening.receive_json() == {"kind": "box.status", "code": code}


class TestThePayload:
    def test_it_carries_a_kind_and_a_code_and_nothing_else(self, client, listening):
        # Small on purpose: this goes to every connected phone on every change,
        # and a whole box body would be stale by the time it arrived.
        client.post("/api/boxes", json={})

        assert set(listening.receive_json()) <= {"kind", "code", "origin"}

    def test_a_change_is_tagged_with_the_device_that_made_it(self, client, listening):
        # The device that made the change has already redrawn. Without this tag
        # it redraws again on its own echo, throwing away whatever the user
        # started typing in between.
        client.post("/api/boxes", json={}, headers={"X-Client-Id": "the-phone"})

        assert listening.receive_json()["origin"] == "the-phone"

    def test_an_untagged_change_carries_no_origin(self, client, listening):
        client.post("/api/boxes", json={})

        assert "origin" not in listening.receive_json()


class TestManyListeners:
    def test_every_listener_hears_it(self, client):
        with (
            client.websocket_connect("/api/events") as one,
            client.websocket_connect("/api/events") as two,
        ):
            code = client.post("/api/boxes", json={}).json()["code"]

            assert one.receive_json()["code"] == code
            assert two.receive_json()["code"] == code

    def test_nobody_listening_is_not_an_error(self, client):
        assert client.post("/api/boxes", json={}).status_code == 201


class TestStayingAlive:
    def test_the_socket_says_something_even_when_nothing_happens(self, app, client):
        # A phone that roams off wifi leaves a socket that still looks open at
        # both ends. The heartbeat is what makes either end notice.
        app.state.events.heartbeat = 0.05

        with client.websocket_connect("/api/events") as socket:
            assert socket.receive_json() == {"kind": "ping"}

    def test_a_client_that_hangs_up_is_forgotten(self, app, client):
        with client.websocket_connect("/api/events"):
            pass

        # The teardown happens on the server's side of the socket, so give the
        # event loop turns to run it before looking.
        for _ in range(100):
            if app.state.events.subscribers == 0:
                break
            client.get("/health")
        assert app.state.events.subscribers == 0


class TestTheKey:
    @pytest.fixture
    def guarded(self, config):
        with TestClient(create_app(config.replace(api_key="sesame"))) as c:
            yield c

    def test_the_socket_is_refused_without_the_key(self, guarded):
        with pytest.raises(WebSocketDisconnect), guarded.websocket_connect("/api/events"):
            pass  # pragma: no cover - the handshake never completes

    def test_the_socket_is_refused_with_the_wrong_key(self, guarded):
        with pytest.raises(WebSocketDisconnect), guarded.websocket_connect("/api/events?key=no"):
            pass  # pragma: no cover - the handshake never completes

    def test_the_key_may_travel_in_the_query_because_a_browser_cannot_send_headers(self, guarded):
        # A browser's WebSocket constructor takes a URL and nothing else: there
        # is no way to attach X-API-Key to the handshake.
        with guarded.websocket_connect("/api/events?key=sesame") as socket:
            guarded.post("/api/boxes", json={}, headers={"X-API-Key": "sesame"})

            assert socket.receive_json()["kind"] == "box.created"

    def test_an_open_server_needs_no_key(self, client, listening):
        client.post("/api/boxes", json={})

        assert listening.receive_json()["kind"] == "box.created"


class TestTheHub:
    """The broadcast hub on its own, away from HTTP."""

    def test_an_event_published_from_a_worker_thread_arrives(self):
        # This is the whole trick. Every route that touches the database is a
        # sync `def`, so it runs in FastAPI's threadpool -- while the socket
        # and its asyncio.Queue live on the event loop. Publishing has to cross
        # that boundary, and asyncio.Queue is not thread-safe.
        async def scenario():
            hub = events.Hub()
            with hub.subscribe() as queue:
                await asyncio.to_thread(hub.publish, "box.updated", "B-0001")
                return await asyncio.wait_for(queue.get(), timeout=5)

        assert asyncio.run(scenario()) == {"kind": "box.updated", "code": "B-0001"}

    def test_a_listener_that_falls_behind_is_told_to_start_over(self):
        # A phone in a dead spot cannot be caught up event by event, and a
        # queue that grows without bound is a leak. One "start over" marker
        # says everything the backlog said, because clients re-fetch anyway.
        async def scenario():
            hub = events.Hub(backlog=4)
            with hub.subscribe() as queue:
                for n in range(50):
                    hub.publish("box.updated", f"B-{n:04d}")
                await asyncio.sleep(0)
                return [queue.get_nowait() for _ in range(queue.qsize())]

        drained = asyncio.run(scenario())

        assert len(drained) <= 4, "the backlog grew without bound"
        # The marker is the *first* thing the client reads, so it refetches
        # before acting on anything that arrived after the gap. Whatever
        # follows it is a handful of redundant refetches, which cost nothing.
        assert drained[0] == {"kind": "resync"}

    def test_a_listener_is_forgotten_when_it_leaves(self):
        async def scenario():
            hub = events.Hub()
            with hub.subscribe():
                during = hub.subscribers
            return during, hub.subscribers

        assert asyncio.run(scenario()) == (1, 0)

    def test_publishing_to_nobody_is_harmless(self):
        assert events.Hub().publish("box.updated", "B-0001") == {
            "kind": "box.updated",
            "code": "B-0001",
        }
