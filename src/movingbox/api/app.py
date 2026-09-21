"""FastAPI application factory, request-scoped database, and API-key auth."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

import movingbox

from .. import analysis, db, phrasing, store
from ..config import ROOT, Config, from_env
from ..labels import printer as printing
from ..labels.layout import FONT_PATH
from . import assets, events

WEB_ROOT = ROOT / "web"

#: Written by scripts/claude/deploy.sh just before the service restarts.
REVISION_FILE = ROOT / "var" / "deployed-revision"


def deployed_revision() -> str:
    """The commit this process was started from, or ``"unknown"``.

    Call once, at startup: re-read per request, /health would report the new
    commit as soon as the file was written, whether or not the restart happened.
    """
    # Lets a throwaway server (the browser checks) serve versioned asset URLs
    # the way production does.
    named = os.environ.get("MOVING_REVISION", "").strip()
    if named:
        return named
    try:
        return REVISION_FILE.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_events(
    request: Request, x_client_id: str | None = Header(default=None)
) -> events.Publisher:
    """The change channel, tagged with the requesting device so it can ignore its own echo."""
    return events.Publisher(request.app.state.events, origin=x_client_id)


def get_conn(config: Config = Depends(get_config)) -> Iterator[sqlite3.Connection]:
    """One connection per request, never shared between requests.

    Setup, endpoint and teardown may each run on a different threadpool worker,
    so the connection must tolerate the handoff (see db.connect). Routes taking
    this must be sync ``def``: an ``async def`` endpoint runs on the event loop,
    and sqlite refuses a handle that crosses to it.
    """
    conn = db.connect(config.db_path)
    try:
        yield conn
    finally:
        conn.close()


def build_vision_provider(config: Config):
    """The configured vision provider, for routes and the worker.

    Built per use so a provider that comes back up needs no restart. "claude"
    is the cloud tier with the local model behind it; with no key it is still a
    pair, with nothing to try first, so photos are read locally.
    """
    if config.vision_provider == "stub":
        from ..vision.stub import StubProvider

        return StubProvider(config.vision_stub_seconds, config.vision_detail_model)

    from ..vision.ollama import OllamaProvider

    local = OllamaProvider(config.ollama_url)
    if config.vision_provider != "claude":
        return local

    from ..vision import claude, hybrid

    return hybrid.Hybrid(
        cloud=claude.provider_for(
            config.anthropic_api_key, detail_model=config.vision_cloud_detail_model
        ),
        local=local,
        fallbacks=config.vision_fallbacks(),
    )


def get_vision_provider(config: Config = Depends(get_config)):
    """Overridden in tests with a stub."""
    return build_vision_provider(config)


def build_phraser(config: Config):
    """The "From contents" phraser, or None to assemble the line without a model.

    A Config built directly (every test) gives None, so the suite never reaches
    a model.
    """
    if not config.phrase_summaries:
        return None
    if config.vision_provider == "stub":
        from ..phrasing import StubPhraser

        return StubPhraser()

    from ..phrasing import OllamaPhraser

    return OllamaPhraser(config.ollama_url)


def get_phraser(config: Config = Depends(get_config)):
    """Overridden in tests that want one."""
    return build_phraser(config)


def get_analyst(request: Request):
    """The background photo worker, or None where it is not running (tests)."""
    return request.app.state.analyst


def require_api_key(
    config: Config = Depends(get_config),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Guard the /api surface when a key is configured; with none, it is open.

    `/b/{code}` is not guarded: a stock camera app opening a QR cannot send headers.
    """
    if config.api_key is None:
        return
    if x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


async def _until_the_client_goes(socket: WebSocket) -> None:
    """Finish when the browser hangs up; anything it sends is discarded.

    Without a reader, a vanished client would leave the sender parked on its
    queue until the next heartbeat failed.
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
        watcher = printing.AutoOffWatcher(settings)
        app.state.printer_watcher = watcher
        watcher.start()

        warmer = phrasing.Warmer(settings)
        app.state.summary_warmer = warmer
        warmer.start()

        # Off in a Config built directly, so no test starts the worker.
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
            warmer.stop()
            if app.state.analyst is not None:
                app.state.analyst.stop()

    app = FastAPI(title="Moving Box Tracker", version=movingbox.__version__, lifespan=lifespan)
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
    def health(response: Response) -> dict[str, str]:
        # web/reload.js polls this to notice a deploy; a cached answer would
        # hide one.
        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "ok",
            "revision": app.state.revision,
            "version": movingbox.__version__,
        }

    @app.websocket("/api/events")
    async def changes(socket: WebSocket) -> None:
        """Tell connected clients which box just changed.

        Must never take `get_conn`: a websocket endpoint has to be `async def`,
        and a sqlite handle cannot cross onto the event loop. Messages carry a
        kind and a code only; clients refetch over REST.

        The key is in the query string because a browser's WebSocket
        constructor cannot send headers, so it does appear in the access log.
        """
        settings: Config = socket.app.state.config
        if settings.api_key is not None and socket.query_params.get("key") != settings.api_key:
            # Closing before accepting rejects the handshake.
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

        The Location must stay relative: Tailscale terminates TLS and proxies
        plain HTTP, so an absolute URL rebuilt from the request would say
        `http://`, leave the secure context and break the camera.
        """
        # A binned box opens where it can be restored.
        if store.get_box(conn, code, include_deleted=True) is None:
            raise HTTPException(status_code=404, detail=f"No box {code}")
        return RedirectResponse(url=f"/#/b/{quote(code)}", status_code=307)

    # The label's typeface, served from the package so there is one copy.
    @app.get("/Inter.ttf", include_in_schema=False)
    def font(v: str | None = None) -> FileResponse:
        return FileResponse(
            FONT_PATH,
            media_type="font/ttf",
            headers={"Cache-Control": assets.cache_header(v, app.state.revision)},
        )

    # Mounted last, so /api and /b are not shadowed.
    if WEB_ROOT.is_dir():

        @app.get("/sw.js", include_in_schema=False)
        def service_worker() -> Response:
            """sw.js with its cache version set to the revision, so each deploy
            reinstalls the shell. Served as written in dev (revision "unknown").
            """
            body = (WEB_ROOT / "sw.js").read_text()
            if app.state.revision != "unknown":
                body = re.sub(
                    r'const VERSION = "[^"]*"',
                    f'const VERSION = "{app.state.revision}"',
                    body,
                    count=1,
                )
            # The shell list must name the same versioned URLs the page asks for.
            body = assets.versioned(body, app.state.revision)
            return Response(
                body,
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/", include_in_schema=False)
        @app.get("/index.html", include_in_schema=False)
        def page() -> Response:
            # Never cached: it names the versioned files.
            return assets.respond(
                WEB_ROOT / "index.html",
                asked_for=None,
                revision=app.state.revision,
                version=movingbox.__version__,
            )

        @app.get("/{name}", include_in_schema=False)
        def asset(name: str, v: str | None = None) -> Response:
            # One path segment by construction of the route, so no traversal.
            path = WEB_ROOT / name
            if name.startswith(".") or not path.is_file():
                raise HTTPException(status_code=404, detail="Not found")
            return assets.respond(path, asked_for=v, revision=app.state.revision)

        app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")

    return app
