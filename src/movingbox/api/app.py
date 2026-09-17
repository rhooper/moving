"""FastAPI application factory, request-scoped database, and API-key auth."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import db, store
from ..config import Config, from_env


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_conn(config: Config = Depends(get_config)) -> Iterator[sqlite3.Connection]:
    """One connection per request.

    Connections are not shared: sqlite3 objects are not thread-safe and FastAPI
    runs sync endpoints in a threadpool. WAL mode makes the per-request open
    cheap and keeps readers from blocking the writer.
    """
    conn = db.connect(config.db_path)
    try:
        yield conn
    finally:
        conn.close()


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


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="Moving Box Tracker", version="0.1.0")
    app.state.config = config or from_env()

    from . import boxes, rooms

    app.include_router(boxes.router, dependencies=[Depends(require_api_key)])
    app.include_router(rooms.router, dependencies=[Depends(require_api_key)])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/b/{code}")
    def scanned(code: str, conn: sqlite3.Connection = Depends(get_conn)):
        """Where a scanned QR code lands. Redirects into the PWA."""
        if store.get_box(conn, code) is None:
            raise HTTPException(status_code=404, detail=f"No box {code}")
        return RedirectResponse(url=f"/#/b/{code}", status_code=307)

    return app
