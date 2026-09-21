"""Label preview and printing."""

from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import prefs, store
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
    return layout.from_box(box, base_url=config.base_url, **store.label_rooms(conn, box))


@router.get("/preview/{code}.png")
def preview(
    code: str,
    height: int | None = Query(default=None, description="Exact cut height; omit to fit content"),
    orientation: str | None = Query(default=None, description="landscape or portrait"),
    stub: bool = Query(default=False, description="The one-inch stub: number and QR only"),
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
) -> Response:
    data = _label_for(conn, code, config)
    image = (
        layout.render_stub(data)
        if stub
        else layout.render(data, height=height, orientation=orientation or config.label_orientation)
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
    # Resolve every code before printing anything, so a bad code wastes no tape.
    labels = [(code, _label_for(conn, code, config)) for code in body.codes]

    # The stub exists for a box with nothing in it yet.
    if not body.allow_empty and not body.stub:
        blank = [code for code in body.codes if not store.has_contents(conn, code)]
        if blank:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Nothing is recorded in {', '.join(blank)}. Add contents first, "
                    f"or send allow_empty to label an empty box."
                ),
            )

    backend = printer.get_backend(config)
    printed = []
    for code, data in labels:
        # Per label, so a mixed batch gets each kind's own number.
        copies = body.copies or (
            1 if body.stub else prefs.label_copies(conn, store.get_box(conn, code)["kind"])
        )
        try:
            image = (
                layout.render_stub(data)
                if body.stub
                else layout.render(
                    data,
                    height=body.height,
                    orientation=body.orientation or config.label_orientation,
                )
            )
            written = backend.print_label(image, code=code, copies=copies)
        except Exception as failure:  # noqa: BLE001 - every backend fails differently
            # Not counted as printed: no tape came out.
            raise HTTPException(
                status_code=502,
                detail=(f"Could not print {code}: {failure}. {printer.status(config)['detail']}"),
            ) from failure
        store.record_print(conn, code, copies=copies)
        changes.publish(events.LABEL_PRINTED, code)
        printed.append({"code": code, "output": str(written), "copies": copies})

    return {"printed": printed, **printer.status(config)}
