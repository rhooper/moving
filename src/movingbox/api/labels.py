"""Label preview and printing."""

from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import store
from ..config import Config
from ..labels import layout, printer
from . import events
from .app import get_config, get_conn, get_events
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
        source_name=store.room_name(conn, box["source_room_id"]),
        items=store.list_items(conn, code),
    )


def has_contents(conn: sqlite3.Connection, code: str) -> bool:
    """Whether anything is recorded about what is in the box."""
    box = store.get_box(conn, code)
    if box is None:
        return False
    if (box.get("content_summary") or "").strip():
        return True
    return bool(store.list_items(conn, code))


@router.get("/preview/{code}.png")
def preview(
    code: str,
    height: int | None = Query(default=None, description="Exact cut height; omit to fit content"),
    orientation: str | None = Query(default=None, description="landscape or portrait"),
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
) -> Response:
    image = layout.render(
        _label_for(conn, code, config),
        height=height,
        orientation=orientation or config.label_orientation,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@router.post("/print")
def print_labels(
    body: PrintRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    # Resolve every code before printing anything. A partly-printed batch
    # wastes tape and leaves you unsure which labels actually came out.
    labels = [(code, _label_for(conn, code, config)) for code in body.codes]

    if not body.allow_empty:
        blank = [code for code in body.codes if not has_contents(conn, code)]
        if blank:
            # 409, not 400: the request is fine, the box's state is not.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Nothing is recorded in {', '.join(blank)}. Add contents first, "
                    f"or tick 'print anyway' to label an empty box."
                ),
            )

    backend = printer.get_backend(config)
    printed = []
    for code, data in labels:
        try:
            written = backend.print_label(
                layout.render(
                    data,
                    height=body.height,
                    orientation=body.orientation or config.label_orientation,
                ),
                code=code,
                copies=body.copies,
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
        # Per label, not per batch: a long batch should light up each box page
        # as its tape comes out, not all at the end.
        changes.publish(events.LABEL_PRINTED, code)
        printed.append({"code": code, "output": str(written)})

    return {"printed": printed, **printer.status(config)}
