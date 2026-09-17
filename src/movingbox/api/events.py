"""In-process broadcast of "something changed", for live-updating clients.

The channel carries an event *kind* and a box *code*. It never carries a box.
That is a deliberate constraint rather than laziness:

* Rendering a box already has one code path -- the REST endpoints -- and a
  second one that reassembles it from a notification would drift from it.
* A payload is stale the moment it is queued. A client that re-fetches on
  hearing "B-0042 changed" always draws what the database says now, so a
  duplicated, reordered or dropped notification costs a wasted GET and
  nothing else.
* It keeps the socket endpoint free of the database entirely, which matters
  here more than usual: a websocket endpoint has no choice about being
  ``async def``, and every route that takes ``get_conn`` must be sync
  (see the note in ``app.get_conn``). Needing no connection is the only way
  those two rules can both hold.

The hub is per-application, not a module global, so tests are isolated from
each other and two apps in one process cannot cross-talk.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from typing import Any

# What changed. The box list only redraws for the kinds that alter a row it
# shows (plus items, which feed the search index); a box page redraws for
# anything naming its own code. The split lives in web/live.js, which is
# tested against these names.
BOX_CREATED = "box.created"
BOX_UPDATED = "box.updated"
BOX_DELETED = "box.deleted"
BOX_STATUS = "box.status"
BOX_LOCATION = "box.location"
ITEMS_CHANGED = "items.changed"
PHOTOS_CHANGED = "photos.changed"
LABEL_PRINTED = "label.printed"

#: Sent when the connection is idle. Proves the socket is still a socket: a
#: phone that roams off wifi leaves one that looks open from both ends until
#: somebody tries to write to it.
PING = "ping"

#: Sent instead of a backlog nobody can catch up on. Means "refetch
#: everything"; it is safe precisely because clients refetch anyway.
RESYNC = "resync"

#: Seconds of silence before a heartbeat. Short enough that a dead socket is
#: noticed while the phone is still in your hand, long enough to be free.
HEARTBEAT_SECONDS = 25.0

#: Events held for a client that is not reading. Beyond this the backlog is
#: replaced by a single RESYNC -- see _Subscriber.deliver.
BACKLOG = 64


class _Subscriber:
    """One connected client: a queue, and the loop that queue belongs to."""

    def __init__(self, loop: asyncio.AbstractEventLoop, backlog: int) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=backlog)

    def offer(self, event: dict[str, Any]) -> None:
        """Hand an event over from whatever thread published it.

        asyncio.Queue is not thread-safe and publishing happens in FastAPI's
        threadpool (every route touching the database is a sync ``def``), so
        the put has to be bounced onto the loop that owns the queue.
        """
        try:
            self.loop.call_soon_threadsafe(self.deliver, event)
        except RuntimeError:
            # The loop is closing or closed; this subscriber is on its way out
            # and there is nobody left to tell.
            pass

    def deliver(self, event: dict[str, Any]) -> None:
        """Queue an event. Runs on the subscriber's own loop."""
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # This client has stopped reading -- a phone in a dead spot, or one
            # that slept mid-frame. It cannot be caught up event by event and
            # the queue must not grow without bound, so collapse the whole
            # backlog into one "start over" marker. That loses no information:
            # every event only ever meant "refetch".
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"kind": RESYNC})


class Hub:
    """Fan-out of change notifications to every connected client."""

    def __init__(self, *, heartbeat: float = HEARTBEAT_SECONDS, backlog: int = BACKLOG) -> None:
        self.heartbeat = heartbeat
        self.backlog = backlog
        self._subscribers: set[_Subscriber] = set()
        # Publishers are threadpool workers and subscribers come and go on the
        # event loop, so the membership set genuinely is shared across threads.
        self._lock = threading.Lock()

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, kind: str, code: str | None = None, *, origin: str | None = None) -> dict:
        """Tell every connected client that `code` changed.

        Safe to call from any thread, and safe to call with nobody listening --
        which is the normal case for the CLI and for a server nobody has opened.
        Returns the event, which makes it easy to assert on.
        """
        event: dict[str, Any] = {"kind": kind}
        if code is not None:
            event["code"] = code
        if origin:
            # Who made the change, so that device can ignore its own echo
            # rather than redrawing over whatever its user started typing.
            event["origin"] = origin

        with self._lock:
            listeners = list(self._subscribers)
        for listener in listeners:
            listener.offer(event)
        return event

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        """Register the calling coroutine's queue for the life of the block."""
        subscriber = _Subscriber(asyncio.get_running_loop(), self.backlog)
        with self._lock:
            self._subscribers.add(subscriber)
        try:
            yield subscriber.queue
        finally:
            with self._lock:
                self._subscribers.discard(subscriber)


class Publisher:
    """A hub bound to one request, so routes need not thread the client id.

    Handed to endpoints as a dependency. The `origin` comes from the caller's
    ``X-Client-Id`` header and rides along on everything this request
    publishes.
    """

    __slots__ = ("hub", "origin")

    def __init__(self, hub: Hub, origin: str | None = None) -> None:
        self.hub = hub
        self.origin = origin

    def publish(self, kind: str, code: str | None = None) -> dict:
        return self.hub.publish(kind, code, origin=self.origin)
