"""The live change channel a second device listens on.

What travels is *what changed*, never the change itself: every client
re-fetches through the ordinary REST endpoints, so one code path decides what a
box looks like and a missed or duplicated notification is harmless.
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
        # Clients re-fetch by box code, and the item is gone by the time the
        # message lands anyway.
        item = client.post(f"/api/boxes/{code}/items", json={"name": "kettle"}).json()
        assert listening.receive_json()["kind"] == "items.changed"

        client.delete(f"/api/items/{item['id']}")

        assert listening.receive_json() == {"kind": "items.changed", "code": code}

    def test_a_label_being_printed(self, client, code, listening):
        # allow_empty: the fixture box has no contents, and printing one is
        # otherwise refused.
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
        # The cover is drawn on the list rows other phones are showing.
        photo = client.post(
            f"/api/boxes/{code}/photos", files={"file": ("a.jpg", a_jpeg(), "image/jpeg")}
        ).json()
        assert listening.receive_json()["kind"] == "photos.changed"

        client.post(f"/photos/{photo['id']}/cover")

        assert listening.receive_json() == {"kind": "photos.changed", "code": code}

    def test_a_read_announces_nothing(self, client, code, listening):
        # Silence cannot be proven by waiting for it, so a real change follows:
        # the queue preserves order, so anything a read announced would arrive
        # first.
        client.get("/api/boxes")
        client.get(f"/api/boxes/{code}")
        client.get(f"/api/boxes/{code}/items")
        client.get(f"/api/boxes/{code}/events")

        client.post(f"/api/boxes/{code}/status", json={"status": "packed"})

        assert listening.receive_json() == {"kind": "box.status", "code": code}


class TestThePayload:
    def test_it_carries_a_kind_and_a_code_and_nothing_else(self, client, listening):
        # A whole box body would be stale by the time it arrived.
        client.post("/api/boxes", json={})

        assert set(listening.receive_json()) <= {"kind", "code", "origin"}

    def test_a_change_is_tagged_with_the_device_that_made_it(self, client, listening):
        # The device that made the change has already redrawn; redrawing on its
        # own echo would throw away whatever the user has typed since.
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
        # Database routes are sync and run in FastAPI's threadpool, while the
        # socket's asyncio.Queue lives on the event loop and is not thread-safe.
        async def scenario():
            hub = events.Hub()
            with hub.subscribe() as queue:
                await asyncio.to_thread(hub.publish, "box.updated", "B-0001")
                return await asyncio.wait_for(queue.get(), timeout=5)

        assert asyncio.run(scenario()) == {"kind": "box.updated", "code": "B-0001"}

    def test_a_listener_that_falls_behind_is_told_to_start_over(self):
        # An unbounded queue is a leak. Clients re-fetch anyway, so one "start
        # over" marker says everything the backlog said.
        async def scenario():
            hub = events.Hub(backlog=4)
            with hub.subscribe() as queue:
                for n in range(50):
                    hub.publish("box.updated", f"B-{n:04d}")
                await asyncio.sleep(0)
                return [queue.get_nowait() for _ in range(queue.qsize())]

        drained = asyncio.run(scenario())

        assert len(drained) <= 4, "the backlog grew without bound"
        # First, so the client refetches before acting on anything after the gap.
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
