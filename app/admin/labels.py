"""Printable 4 x 6 inch B2B shipping labels."""

import io
from xml.sax.saxutils import escape

from flask import render_template, request, send_file
from reportlab.graphics.barcode import code128
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepInFrame, Paragraph

from app.admin import admin_bp
from app.admin.auth import require_admin
from app.models import Consignment


def _label_rows():
    page = max(1, request.args.get("page", 1, type=int))
    search = request.args.get("search", "").strip()
    query = Consignment.query
    if search:
        query = query.filter(Consignment.consignment_number.ilike(f"%{search}%"))
    total = query.count()
    return dict(rows=query.order_by(Consignment.id.desc()).offset((page - 1) * 25).limit(25).all(), page=page, total=total, search=search)


@admin_bp.get("/admin/labels")
@require_admin
def labels_panel():
    return render_template("admin/labels.html", **_label_rows())


def _address(pdf, title, name, address, pin, y):
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(18, y, title)
    style = ParagraphStyle("address", fontName="Helvetica", fontSize=10, leading=13)
    paragraph = Paragraph("<b>" + escape(name or "") + "</b><br/>" + escape(address or "Address not provided").replace("\n", "<br/>") + "<br/><b>PIN: " + escape(pin or "Not provided") + "</b>", style)
    box = KeepInFrame(252, 86, [paragraph], mode="shrink")
    _, height = box.wrapOn(pdf, 252, 86)
    box.drawOn(pdf, 18, y - 15 - height)


def generate_labels_pdf(rows):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(4 * inch, 6 * inch))
    pdf.setTitle("Gram SCS B2B Shipping Labels")
    for row in rows:
        pieces = row.pieces or 1
        for piece in range(1, pieces + 1):
            pdf.setFont("Helvetica-Bold", 15)
            pdf.drawString(18, 407, "GRAM SCS | B2B")
            pdf.setFont("Helvetica", 11)
            pdf.drawRightString(270, 386, f"Piece {piece} of {pieces}")
            pdf.drawString(18, 386, row.identifier_type or "LRN")
            identifier = Paragraph(escape(row.consignment_number), ParagraphStyle("identifier", fontName="Helvetica-Bold", fontSize=12, leading=14))
            box = KeepInFrame(252, 30, [identifier], mode="shrink")
            box.wrapOn(pdf, 252, 30)
            box.drawOn(pdf, 18, 351)
            barcode = code128.Code128(row.consignment_number, barHeight=37, barWidth=0.8, humanReadable=False)
            pdf.saveState()
            pdf.translate(18, 306)
            pdf.scale(min(1, 252 / barcode.width), 1)
            barcode.drawOn(pdf, 0, 0)
            pdf.restoreState()
            pdf.line(18, 295, 270, 295)
            _address(pdf, "SHIP FROM", row.pickup_tag, row.pickup_address, row.pickup_pincode, 280)
            _address(pdf, "SHIP TO", row.drop_tag, row.drop_address, row.drop_pincode, 172)
            pdf.line(18, 66, 270, 66)
            pdf.setFont("Helvetica", 8)
            weight = str(row.chargeable_weight) if row.chargeable_weight is not None else "—"
            volume = str(row.chargeable_volume) if row.chargeable_volume is not None else "—"
            pdf.drawString(18, 50, f"Shipment chargeable weight: {weight} kg")
            pdf.drawString(18, 36, f"Shipment chargeable volume: {volume} m3")
            pdf.drawString(18, 22, "Shipment total pieces: " + str(pieces))
            pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


@admin_bp.post("/admin/labels/generate")
@require_admin
def labels_generate():
    try:
        selected = list(dict.fromkeys(int(value) for value in request.form.getlist("consignment_ids")))
        if not selected or len(selected) > 500:
            raise ValueError("Select at least one saved shipment (maximum 500 labels per download).")
        found = {row.id: row for row in Consignment.query.filter(Consignment.id.in_(selected)).all()}
        if len(found) != len(selected):
            raise ValueError("A selected shipment no longer exists. Refresh the list and select again.")
        rows = [found[row_id] for row_id in selected]
        if sum(row.pieces or 1 for row in rows) > 500:
            raise ValueError("Selected shipments exceed 500 piece labels. Select fewer shipments.")
        if any(not row.consignment_number.isascii() or not all(32 <= ord(char) <= 126 for char in row.consignment_number) for row in rows):
            raise ValueError("Barcodes need printable ASCII identifiers. Edit the selected shipment identifier first.")
    except (ValueError, TypeError) as error:
        return render_template("admin/labels.html", error=str(error), **_label_rows()), 400
    return send_file(generate_labels_pdf(rows), as_attachment=True, download_name="b2b-shipping-labels.pdf", mimetype="application/pdf")
