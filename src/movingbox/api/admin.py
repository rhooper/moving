"""Export, manifest and backup endpoints."""

from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from .. import backup, export
from ..config import Config
from .app import get_config, get_conn

router = APIRouter(prefix="/api", tags=["admin"])


def _download(content: str | bytes, *, filename: str, media_type: str) -> Response:
    """Offer the body as a file rather than rendering it in the browser."""
    return Response(
        content=content,
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.json")
def export_json(conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    return _download(
        export.to_json(conn), filename="moving-export.json", media_type="application/json"
    )


@router.get("/export.csv")
def export_csv(conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    return _download(export.to_csv(conn), filename="moving-export.csv", media_type="text/csv")


@router.get("/manifest")
def manifest(conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
    return JSONResponse(export.manifest(conn))


@router.get("/manifest.pdf")
def manifest_pdf(conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    from ..manifest_pdf import render

    buffer = io.BytesIO()
    render(export.manifest(conn), buffer)
    return Response(content=buffer.getvalue(), media_type="application/pdf")


@router.post("/backup")
def take_backup(config: Config = Depends(get_config)) -> dict:
    try:
        written = backup.create(config)
    except backup.BackupFailed as failure:
        # 500, not 200-with-an-error: a backup that silently did not happen is
        # the worst possible outcome here.
        raise HTTPException(status_code=500, detail=str(failure)) from failure

    return {
        "file": str(written),
        "bytes": written.stat().st_size,
        "kept": len(backup.existing(config)),
    }
