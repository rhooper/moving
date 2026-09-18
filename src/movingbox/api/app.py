"""FastAPI application factory, request-scoped database, and API-key auth."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from .. import analysis, db, store
from ..config import ROOT, Config, from_env
from ..labels import printer as printing
from ..labels.layout import FONT_PATH
from . import events

WEB_ROOT = ROOT / "web"

#: Written by scripts/claude/deploy.sh (and install-service.sh) with the commit
#: being deployed, just before the launchd agent is restarted. Gitignored along
#: with the rest of var/.
REVISION_FILE = ROOT / "var" / "deployed-revision"


def deployed_revision() -> str:
    """The commit this process was started from, or ``"unknown"``.

    Read **once, at startup** rather than per request, and that is the whole
    point: a value re-read on each request would report the new commit the
    moment the file was written, whether or not the restart that was supposed
    to follow ever happened. Read at startup, /health answering with the new
    revision is proof that this process is running that code -- which is what
    the deploy script checks before calling a deploy successful.
    """
    try:
        return REVISION_FILE.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_events(
    request: Request, x_client_id: str | None = Header(default=None)
) -> events.Publisher:
    """The change channel, bound to the device that made this request.

    Tagging each event with its origin is what lets that device ignore its own
    echo: it has already redrawn from the response it got, and redrawing again
    a moment later is how a half-typed item name disappears.
    """
    return events.Publisher(request.app.state.events, origin=x_client_id)


def get_conn(config: Config = Depends(get_config)) -> Iterator[sqlite3.Connection]:
    """One connection per request, never shared between requests.

    Per-request isolation is necessary but **not sufficient**: a single request
    does not stay on one thread. FastAPI runs this generator's setup, the
    endpoint body, and its teardown on potentially three different threadpool
    workers, so the connection itself must tolerate the handoff -- see the
    check_same_thread note in db.connect().

    WAL mode makes the per-request open cheap and keeps readers from blocking
    the writer.
    """
    conn = db.connect(config.db_path)
    try:
        yield conn
    finally:
        conn.close()


def build_vision_provider(config: Config):
    """The configured vision provider. One place, for routes and the worker.

    Built per use rather than at startup so Ollama coming back up does not
    need a restart.
    """
    if config.vision_provider == "stub":
        from ..vision.stub import StubProvider

        return StubProvider(config.vision_stub_seconds, config.vision_detail_model)

    from ..vision.ollama import OllamaProvider

    return OllamaProvider(config.ollama_url)


def get_vision_provider(config: Config = Depends(get_config)):
    """The vision provider for drafting. Overridden in tests with a stub."""
    return build_vision_provider(config)


def get_analyst(request: Request):
    """The background photo worker, or None where it is not running (tests)."""
    return request.app.state.analyst


def require_api_key(
    config: Config = Depends(get_config),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Guard the /api surface when a key is configured.

    With no key set the API is open, which is the right default for a tool that
    normally runs on localhost behind Tailscale. `/b/{code}` is deliberately not
    guarded: a QR code is opened by a stock camera app that cannot send headers.
    """
    if config.api_key is None:
        return
    if x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


async def _until_the_client_goes(socket: WebSocket) -> None:
    """Finish when the browser hangs up.

    Nothing else ever reads this socket, so without a reader a phone that
    walked out of range would leave the sending coroutine parked on its queue
    until the next heartbeat failed. Anything the client sends is discarded:
    the channel is one-way by design.
    """
    try:
        while True:
            if (await socket.receive())["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError):
        return


def create_app(config: Config | None = None) -> FastAPI:
    settings = config or from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The printer switches itself off after a while, and the only way to
        # stop it is to tell it once -- the setting persists in the printer.
        # So: wait for it to appear, say so, and stop. A daemon thread, and
        # stopped on shutdown, so it never holds the service up.
        watcher = printing.AutoOffWatcher(settings)
        app.state.printer_watcher = watcher
        watcher.start()

        # Photo analysis runs here rather than in the request that uploaded the
        # photo: a vision model takes seconds to tens of seconds, and nobody
        # should hold a phone still for that. Off unless the config asks -- a
        # Config built directly, as every test builds one, never starts it.
        if settings.auto_analyse:
            app.state.analyst = analysis.Analyst(
                settings,
                provider_factory=lambda: build_vision_provider(settings),
                publish=app.state.events.publish,
            )
            app.state.analyst.start()
        try:
            yield
        finally:
            watcher.stop()
            if app.state.analyst is not None:
                app.state.analyst.stop()

    app = FastAPI(title="Moving Box Tracker", version="0.1.0", lifespan=lifespan)
    app.state.config = settings
    app.state.revision = deployed_revision()
    app.state.events = events.Hub()
    app.state.analyst = None

    from . import admin, boxes, labels, photos, rooms
    from . import settings as settings_routes

    app.include_router(boxes.router, dependencies=[Depends(require_api_key)])
    app.include_router(rooms.router, dependencies=[Depends(require_api_key)])
    app.include_router(labels.router, dependencies=[Depends(require_api_key)])
    app.include_router(admin.router, dependencies=[Depends(require_api_key)])
    app.include_router(photos.router, dependencies=[Depends(require_api_key)])
    app.include_router(settings_routes.router, dependencies=[Depends(require_api_key)])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "revision": app.state.revision}

    @app.websocket("/api/events")
    async def changes(socket: WebSocket) -> None:
        """Tell connected clients which box just changed.

        Deliberately takes no `get_conn`: a websocket endpoint must be `async
        def`, and an async endpoint holding a connection opened in the
        threadpool is the exact crash documented on get_conn. It needs none --
        every message is a kind and a code, and clients re-fetch over REST.

        The key travels in the query string because it has to: a browser's
        WebSocket constructor takes a URL and nothing else, so X-API-Key
        cannot be attached to the handshake. That does put the key in the
        server's access log, which is why `/api/events` is refused outright
        rather than degraded when the key is wrong.
        """
        settings: Config = socket.app.state.config
        if settings.api_key is not None and socket.query_params.get("key") != settings.api_key:
            # Closing before accepting rejects the handshake outright, which
            # is what a browser reports as a failed connection.
            await socket.close(code=1008)
            return

        await socket.accept()
        hub: events.Hub = socket.app.state.events
        hanging_up = asyncio.create_task(_until_the_client_goes(socket))
        try:
            with hub.subscribe() as queue:
                while not hanging_up.done():
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=hub.heartbeat)
                    except TimeoutError:
                        message = {"kind": events.PING}
                    if hanging_up.done():
                        break
                    await socket.send_json(message)
        except WebSocketDisconnect:
            pass
        finally:
            hanging_up.cancel()

    @app.get("/b/{code}")
    def scanned(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        """Where a scanned QR code lands. Redirects into the PWA.

        The Location is **relative** on purpose. Tailscale terminates TLS and
        proxies plain HTTP to us, so an absolute redirect rebuilt from the
        request would say `http://` and drop the phone out of HTTPS — which
        breaks the camera, since getUserMedia needs a secure context.
        """
        # include_deleted: scanning a box you deleted by mistake should take
        # you to it, where it can be restored, not to "no such box".
        if store.get_box(conn, code, include_deleted=True) is None:
            raise HTTPException(status_code=404, detail=f"No box {code}")
        return RedirectResponse(url=f"/#/b/{quote(code)}", status_code=307)

    # The label's typeface, served from the package rather than duplicated into
    # web/ so there is one copy of the file in the repo.
    @app.get("/Inter.ttf", include_in_schema=False)
    def font() -> FileResponse:
        return FileResponse(FONT_PATH, media_type="font/ttf")

    # Mounted last: every route above wins, so /api and /b are not shadowed.
    # html=True serves index.html at / so the hash router can take over.
    if WEB_ROOT.is_dir():

        @app.get("/sw.js", include_in_schema=False)
        def service_worker() -> Response:
            """sw.js with its shell-cache version pinned to the revision.

            The version was a hand-bumped literal, and nobody bumped it -- so
            deployed phones kept a stale app.js against a newer API until
            buttons errored. Substituting the deployed revision rolls the
            cache on every deploy: the browser re-checks sw.js, sees new
            bytes, and reinstalls the shell. Left alone in dev (revision
            "unknown"), where pinning every client to one name would recreate
            exactly the staleness this exists to end.
            """
            body = (WEB_ROOT / "sw.js").read_text()
            if app.state.revision != "unknown":
                body = re.sub(
                    r'const VERSION = "[^"]*"',
                    f'const VERSION = "{app.state.revision}"',
                    body,
                    count=1,
                )
            return Response(
                body,
                media_type="application/javascript",
                # An HTTP-cached sw.js would defeat the whole point.
                headers={"Cache-Control": "no-cache"},
            )

        app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")

    return app
