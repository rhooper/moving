"""The movers' manifest as a PDF.

One page, box counts and weight per destination room, plus the codes so a
count can be checked against what actually came off the truck. Printed on A4
and handed over, so it has to be legible in a hallway rather than pretty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import IO, Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from . import export

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 18 * mm


def render(groups: list[dict[str, Any]], out: IO[bytes]) -> None:
    pdf = pdfcanvas.Canvas(out, pagesize=A4)
    pdf.setTitle("Moving manifest")

    y = PAGE_HEIGHT - MARGIN

    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(MARGIN, y, "Moving manifest")
    y -= 7 * mm
    pdf.setFont("Helvetica", 9)
    pdf.drawString(MARGIN, y, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    y -= 10 * mm

    if not groups:
        pdf.setFont("Helvetica", 11)
        pdf.drawString(MARGIN, y, "No boxes recorded yet.")
        pdf.showPage()
        pdf.save()
        return

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(MARGIN, y, "Room")
    pdf.drawRightString(MARGIN + 105 * mm, y, "Boxes")
    pdf.drawRightString(MARGIN + 140 * mm, y, "Weight")
    y -= 2 * mm
    pdf.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    y -= 6 * mm

    totals = export.manifest_totals(groups)

    for group in groups:
        if y < MARGIN + 30 * mm:
            pdf.showPage()
            y = PAGE_HEIGHT - MARGIN

        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(MARGIN, y, group["room"])
        pdf.drawRightString(MARGIN + 105 * mm, y, str(group["count"]))
        weight = f"{group['weight_kg']:.1f} kg" if group["weight_kg"] else "-"
        pdf.drawRightString(MARGIN + 140 * mm, y, weight)
        if group["unweighed"]:
            pdf.setFont("Helvetica", 8)
            pdf.drawString(MARGIN + 145 * mm, y, f"{group['unweighed']} unweighed")
        y -= 5 * mm

        # Codes, so the count can be checked box by box on arrival.
        pdf.setFont("Helvetica", 8)
        line = ""
        for code in group["codes"]:
            candidate = f"{line}  {code}".strip()
            if pdf.stringWidth(candidate, "Helvetica", 8) > PAGE_WIDTH - 2 * MARGIN:
                pdf.drawString(MARGIN, y, line)
                y -= 4 * mm
                line = code
            else:
                line = candidate
        if line:
            pdf.drawString(MARGIN, y, line)
            y -= 4 * mm
        y -= 4 * mm

    pdf.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    y -= 7 * mm
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(MARGIN, y, "Total")
    pdf.drawRightString(MARGIN + 105 * mm, y, str(totals["boxes"]))
    pdf.drawRightString(MARGIN + 140 * mm, y, f"{totals['weight_kg']:.1f} kg")

    # The weight is only what we know. Saying so on the page matters: someone
    # load-planning from an unqualified total would be reading a number that
    # omits most of the boxes.
    if totals["unweighed"]:
        y -= 5 * mm
        pdf.setFont("Helvetica-Oblique", 9)
        pdf.drawString(
            MARGIN,
            y,
            f"Weight covers {totals['boxes'] - totals['unweighed']} of "
            f"{totals['boxes']} boxes; {totals['unweighed']} have not been weighed.",
        )

    pdf.showPage()
    pdf.save()
