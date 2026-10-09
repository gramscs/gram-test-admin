"""Print-friendly 4 x 6 inch B2B labels with optional Code 128 identifiers."""

import io
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from flask import render_template, request, send_file
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepInFrame, Paragraph
from sqlalchemy import or_

from app.admin import admin_bp
from app.admin.auth import require_admin
from app.models import Company, Consignment


# Embed fonts supplied by ReportLab so printing does not depend on local fonts.
_font_dir = Path(reportlab.__file__).resolve().parent / 'fonts'
for _name, _filename in (('LabelSans', 'Vera.ttf'), ('LabelSansBold', 'VeraBd.ttf')):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, str(_font_dir / _filename)))
pdfmetrics.registerFontFamily('LabelSans', normal='LabelSans', bold='LabelSansBold', italic='LabelSans', boldItalic='LabelSansBold')


def _label_rows():
    page = max(1, request.args.get('page', 1, type=int))
    search = request.args.get('search', '').strip()
    query = Consignment.query
    if search:
        query = query.filter(or_(Consignment.consignment_number.ilike(f'%{search}%'), Consignment.company.has(Company.name.ilike(f'%{search}%')),
                                Consignment.pickup_tag.ilike(f'%{search}%'), Consignment.drop_tag.ilike(f'%{search}%')))
    total = query.count()
    return dict(rows=query.order_by(Consignment.id.desc()).offset((page - 1) * 25).limit(25).all(), page=page, total=total, search=search)


@admin_bp.get('/admin/labels')
@require_admin
def labels_panel():
    return render_template('admin/labels.html', **_label_rows())


def _fit_text(pdf, text, x, y, width, height, font='LabelSans', size=10, leading=None):
    paragraph = Paragraph(text, ParagraphStyle('label', fontName=font, fontSize=size, leading=leading or size*1.2, textColor=colors.black))
    box = KeepInFrame(width, height, [paragraph], mode='shrink')
    _, used_height = box.wrapOn(pdf, width, height)
    box.drawOn(pdf, x, y-used_height)


def _address(pdf, title, name, address, pin, top, height, recipient=False):
    pdf.setFont('LabelSansBold', 8)
    pdf.drawString(22, top, title)
    pdf.setFont('LabelSansBold', 13 if recipient else 10)
    pdf.drawRightString(266, top, 'PIN: ' + (pin or '—'))
    text = '<b>' + escape(name or ('Recipient' if recipient else 'Sender')) + '</b><br/>' + escape(address or 'Address not provided').replace('\n', '<br/>')
    _fit_text(pdf, text, 22, top-13, 244, height, size=12 if recipient else 9, leading=15 if recipient else 11)


def generate_labels_pdf(rows, include_barcode=True):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(4*inch, 6*inch))
    pdf.setTitle('Gram SCS B2B Shipping Labels')
    pdf.setAuthor('Gram SCS')
    for row in rows:
        pieces = row.pieces or 1
        for piece in range(1, pieces+1):
            pdf.setStrokeColor(colors.HexColor('#444444'))
            pdf.setLineWidth(.6)
            pdf.roundRect(12, 12, 264, 408, 5)
            pdf.setFillColor(colors.black)
            pdf.setFont('LabelSansBold', 14)
            pdf.drawString(22, 403, 'GRAM SCS')
            pdf.setFont('LabelSans', 7)
            pdf.drawRightString(266, 404, 'B2B SHIPPING')
            pdf.line(22, 393, 266, 393)
            _fit_text(pdf, escape(row.company.name if row.company else 'B2B SHIPMENT'), 22, 382, 168, 18, font='LabelSansBold', size=8)
            pdf.setFillColor(colors.HexColor('#eeeeee'))
            pdf.roundRect(192, 368, 74, 20, 4, fill=1, stroke=0)
            pdf.setFillColor(colors.black)
            pdf.setFont('LabelSansBold', 8)
            pdf.drawCentredString(229, 375, f'Piece {piece} of {pieces}')
            pdf.setFont('LabelSans', 7)
            pdf.drawString(22, 354, 'SHIPMENT IDENTIFIER / ' + (row.identifier_type or 'LRN'))
            _fit_text(pdf, escape(row.consignment_number), 22, 346, 244, 30, font='LabelSansBold', size=17, leading=18)
            if include_barcode:
                barcode = code128.Code128(row.consignment_number, barHeight=33, barWidth=.8, humanReadable=False)
                pdf.saveState()
                pdf.translate(18, 279)
                pdf.scale(min(1, 252/barcode.width), 1)
                barcode.drawOn(pdf, 0, 0)
                pdf.restoreState()
            pdf.line(22, 270, 266, 270)
            _address(pdf, 'DELIVER TO', row.drop_tag, row.drop_address, row.drop_pincode, 253, 91, recipient=True)
            pdf.line(22, 143, 266, 143)
            _address(pdf, 'SHIP FROM', row.pickup_tag, row.pickup_address, row.pickup_pincode, 129, 57)
            pdf.line(22, 56, 266, 56)
            pdf.setFont('LabelSans', 6)
            pdf.drawString(22, 45, 'CHARGEABLE / SHIPMENT TOTALS')
            pdf.setFont('LabelSansBold', 10)
            weight = str(row.chargeable_weight) if row.chargeable_weight is not None else '—'
            volume = str(row.chargeable_volume) if row.chargeable_volume is not None else '—'
            pdf.drawString(22, 30, f'{weight} kg')
            pdf.drawString(145, 30, f'{volume} m3')
            pdf.setFont('LabelSans', 6)
            pdf.drawString(22, 20, 'Weight')
            pdf.drawString(145, 20, 'Volume')
            pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


@admin_bp.post('/admin/labels/generate')
@require_admin
def labels_generate():
    # Legacy callers keep barcode output; the new form always sends an explicit choice.
    include_barcode = request.form.get('barcode', '1') != '0'
    try:
        selected = list(dict.fromkeys(int(value) for value in request.form.getlist('consignment_ids')))
        if not selected or len(selected) > 500:
            raise ValueError('Select at least one saved shipment (maximum 500 labels per download).')
        found = {row.id: row for row in Consignment.query.filter(Consignment.id.in_(selected)).all()}
        if len(found) != len(selected):
            raise ValueError('A selected shipment no longer exists. Refresh the list and select again.')
        rows = [found[row_id] for row_id in selected]
        if sum(row.pieces or 1 for row in rows) > 500:
            raise ValueError('Selected shipments exceed 500 piece labels. Select fewer shipments.')
        if include_barcode and any(not row.consignment_number.isascii() or not all(32 <= ord(char) <= 126 for char in row.consignment_number) for row in rows):
            raise ValueError('Code 128 needs printable ASCII identifiers. Edit the identifier or print without a barcode.')
    except (ValueError, TypeError) as error:
        return render_template('admin/labels.html', error=str(error), **_label_rows()), 400
    return send_file(generate_labels_pdf(rows, include_barcode), as_attachment=request.form.get('delivery') != 'preview', download_name='b2b-shipping-labels.pdf', mimetype='application/pdf')
