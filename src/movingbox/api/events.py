"""In-process broadcast of "something changed", for live-updating clients.

An event carries a *kind* and a box *code*, never a box: clients refetch over
REST, so a duplicated, reordered or dropped event costs one GET, and the
websocket endpoint (which must be ``async def``) never needs the database.

The hub is per-application, not a module global, so apps cannot cross-talk.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from typing import Any

# web/live.js decides which views each kind redraws, and is tested against
# these names.
BOX_CREATED = "box.created"
BOX_UPDATED = "box.updated"
BOX_DELETED = "box.deleted"
BOX_RESTORED = "box.restored"
BOX_STATUS = "box.status"
BOX_LOCATION = "box.location"
ITEMS_CHANGED = "items.changed"
PHOTOS_CHANGED = "photos.changed"
LABEL_PRINTED = "label.printed"

#: Sent when idle: a socket that roamed off wifi looks open until written to.
PING = "ping"

#: Replaces a backlog a client cannot catch up on. Means "refetch everything".
RESYNC = "resync"

#: Seconds of silence before a PING.
HEARTBEAT_SECONDS = 25.0

#: Events held for a client that is not reading, before collapsing to RESYNC.
BACKLOG = 64


class _Subscriber:
    """One connected client: a queue, and the loop that queue belongs to."""

    def __init__(self, loop: asyncio.AbstractEventLoop, backlog: int) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=backlog)

    def offer(self, event: dict[str, Any]) -> None:
        """Hand an event over from whatever thread published it.

        asyncio.Queue is not thread-safe and routes publish from the threadpool,
        so the put is bounced onto the loop that owns the queue.
        """
        try:
            self.loop.call_soon_threadsafe(self.deliver, event)
        except RuntimeError:
            # The loop is closed; this subscriber is on its way out.
            pass

    def deliver(self, event: dict[str, Any]) -> None:
        """Queue an event. Runs on the subscriber's own loop."""
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # The client stopped reading. Every event only means "refetch",
            # so one RESYNC loses nothing and bounds the queue.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"kind": RESYNC})


class Hub:
    """Fan-out of change notifications to every connected client."""

    def __init__(self, *, heartbeat: float = HEARTBEAT_SECONDS, backlog: int = BACKLOG) -> None:
        self.heartbeat = heartbeat
        self.backlog = backlog
        self._subscribers: set[_Subscriber] = set()
        # Publishers are threadpool workers; subscribers live on the event loop.
        self._lock = threading.Lock()

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, kind: str, code: str | None = None, *, origin: str | None = None) -> dict:
        """Tell every connected client that `code` changed. Safe from any thread.

        Returns the event.
        """
        event: dict[str, Any] = {"kind": kind}
        if code is not None:
            event["code"] = code
        if origin:
            # Lets the device that made the change ignore its own echo, which
            # would otherwise redraw over what its user is typing.
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
    """A hub bound to one request's ``X-Client-Id``, which rides on every event as `origin`."""

    __slots__ = ("hub", "origin")

    def __init__(self, hub: Hub, origin: str | None = None) -> None:
        self.hub = hub
        self.origin = origin

    def publish(self, kind: str, code: str | None = None) -> dict:
        return self.hub.publish(kind, code, origin=self.origin)
