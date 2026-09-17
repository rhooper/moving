"""Label preview and printing."""

from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import store
from ..config import Config
from ..labels import layout, printer
from .app import get_config, get_conn
from .schemas import PrintRequest

router = APIRouter(prefix="/api/labels", tags=["labels"])


def _label_for(conn: sqlite3.Connection, code: str, config: Config) -> layout.LabelData:
    box = store.get_box(conn, code)
    if box is None:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    return layout.from_box(
        box,
        base_url=config.base_url,
        room_name=store.room_name(conn, box["destination_room_id"]),
    )


@router.get("/preview/{code}.png")
def preview(
    code: str,
    height: int | None = Query(default=None, description="Exact cut height; omit to fit content"),
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
) -> Response:
    image = layout.render(_label_for(conn, code, config), height=height)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@router.post("/print")
def print_labels(
    body: PrintRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
) -> dict:
    # Resolve every code before printing anything. A partly-printed batch
    # wastes tape and leaves you unsure which labels actually came out.
    labels = [(code, _label_for(conn, code, config)) for code in body.codes]

    backend = printer.get_backend(config)
    printed = []
    for code, data in labels:
        try:
            written = backend.print_label(
                layout.render(data, height=body.height), code=code, copies=body.copies
            )
        except Exception as failure:  # noqa: BLE001 - every backend fails differently
            # 502: we are the gateway to the hardware, and the hardware failed.
            # record_print is deliberately not reached -- a print count that
            # rises when no tape came out is worse than no count at all.
            raise HTTPException(
                status_code=502,
                detail=(f"Could not print {code}: {failure}. {printer.status(config)['detail']}"),
            ) from failure
        store.record_print(conn, code)
        printed.append({"code": code, "output": str(written)})

    return {"printed": printed, **printer.status(config)}
