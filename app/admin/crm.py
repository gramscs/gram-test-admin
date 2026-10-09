"""Enquiry editing, bulk management, export and mail-app drafts."""

import io
from datetime import UTC, date
from email.message import EmailMessage
from email.utils import formatdate
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request, send_file
from openpyxl import Workbook
from sqlalchemy import func, or_
from sqlalchemy.exc import DatabaseError

from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.input_validation import clean_text, email_address, payload, selected_ids
from app.models import Lead, db

STATUSES = ('New', 'Contacted', 'Qualified', 'Won', 'Closed')


def serialize_lead(row):
    created = row.created_at.replace(tzinfo=UTC) if row.created_at and row.created_at.tzinfo is None else row.created_at
    return {key: getattr(row, key) or '' for key in ('id', 'name', 'email', 'phone', 'subject', 'message', 'company_name', 'notes')} | {
        'status': row.status or 'New',
        'follow_up_date': row.follow_up_date.isoformat() if row.follow_up_date else '',
        'created_at': created.isoformat() if created else '',
        'created_at_display': created.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p') if created else '',
    }


def _query():
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '')
    sort = request.args.get('sort', 'newest')
    query = Lead.query
    if search:
        query = query.filter(or_(*[getattr(Lead, name).ilike(f'%{search}%') for name in ('name', 'email', 'phone', 'company_name', 'subject', 'message', 'notes')]))
    if status in STATUSES:
        query = query.filter(Lead.status == status)
    else:
        status = ''
    orders = {'newest': [Lead.created_at.desc(), Lead.id.desc()], 'oldest': [Lead.created_at.asc(), Lead.id.asc()],
              'name': [Lead.name.asc(), Lead.id.asc()], 'follow_up': [Lead.follow_up_date.asc().nulls_last(), Lead.id.desc()]}
    if sort not in orders:
        sort = 'newest'
    return query.order_by(*orders[sort]), search, status, sort


@admin_bp.get('/admin/leads')
@require_admin
def leads_panel():
    page = max(1, request.args.get('page', 1, type=int))
    try:
        query, search, status, sort = _query()
        total = query.count()
        rows = [serialize_lead(row) for row in query.offset((page-1)*25).limit(25).all()]
        counts = dict(db.session.query(Lead.status, func.count(Lead.id)).group_by(Lead.status).all())
        return render_template('admin/leads.html', leads=rows, statuses=STATUSES, search=search, status=status, sort=sort, page=page, total=total, counts=counts)
    except DatabaseError:
        db.session.rollback()
        return render_template('admin/leads.html', leads=[], statuses=STATUSES, search='', status='', sort='newest', page=1, total=0, counts={}, error='Unable to load enquiries. Check the database connection and run upgrade-db if this database uses an older schema.')


def _save(row=None):
    data = payload()
    row = row or Lead()
    fields = {}
    for key, maximum, required in [('name', 100, True), ('email', 120, True), ('phone', 30, False), ('company_name', 200, False),
                                  ('subject', 200, False), ('message', 20000, True), ('notes', 10000, False)]:
        fields[key] = clean_text(data, key, maximum, required, default=getattr(row, key, '') or '')
    email_address(fields['email'], required=True)
    status = data.get('status', row.status or 'New')
    if status not in STATUSES:
        raise ValueError('Choose a valid enquiry status.')
    follow_up = data.get('follow_up_date', row.follow_up_date.isoformat() if row.follow_up_date else '')
    try:
        follow_up = date.fromisoformat(follow_up) if follow_up else None
    except (ValueError, TypeError):
        raise ValueError('Follow-up date must be a valid YYYY-MM-DD date.') from None
    for key, value in fields.items():
        setattr(row, key, value)
    row.status, row.follow_up_date = status, follow_up
    db.session.add(row)
    db.session.commit()
    return jsonify(success=True, lead=serialize_lead(row))


@admin_bp.post('/admin/leads')
@require_admin
def lead_create():
    try:
        return _save()
    except ValueError as error:
        db.session.rollback()
        return jsonify(success=False, message=str(error)), 400


@admin_bp.put('/admin/leads/<int:lead_id>')
@require_admin
def lead_update(lead_id):
    row = db.session.get(Lead, lead_id)
    if not row:
        return jsonify(success=False, message='Enquiry not found.'), 404
    try:
        return _save(row)
    except ValueError as error:
        db.session.rollback()
        return jsonify(success=False, message=str(error)), 400


def _selected(values, maximum=500):
    ids = selected_ids(values, maximum)
    found = {row.id: row for row in Lead.query.filter(Lead.id.in_(ids)).all()}
    if len(found) != len(ids):
        raise ValueError('A selected enquiry no longer exists. Refresh the page and select again.')
    return [found[row_id] for row_id in ids]


@admin_bp.post('/admin/leads/bulk')
@require_admin
def leads_bulk():
    try:
        data = payload()
        rows = _selected(data.get('ids'))
        action = data.get('action')
        if action == 'status' and data.get('status') in STATUSES:
            for row in rows:
                row.status = data['status']
        elif action == 'delete':
            for row in rows:
                db.session.delete(row)
        else:
            raise ValueError('Choose Delete or a valid status update.')
        db.session.commit()
        return jsonify(success=True, changed=len(rows))
    except ValueError as error:
        db.session.rollback()
        return jsonify(success=False, message=str(error)), 400


@admin_bp.get('/admin/leads/export.xlsx')
@require_admin
def leads_export():
    try:
        ids = request.args.getlist('ids')
        rows = _selected(ids) if ids else _query()[0].all()
    except ValueError as error:
        return jsonify(success=False, message=str(error)), 400
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Enquiries'
    fields = ['id', 'name', 'email', 'phone', 'company_name', 'subject', 'message', 'status', 'notes', 'follow_up_date', 'created_at']
    sheet.append(fields)
    for row in rows:
        values = serialize_lead(row)
        sheet.append([values[key] for key in fields])
        # Keep messages and international phone numbers as text, never spreadsheet formulas.
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='customer-enquiries.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@admin_bp.post('/admin/leads/email-preview')
@require_admin
def email_preview():
    try:
        data = payload()
        rows = _selected(data.get('ids'), maximum=50)
        include_notes = data.get('include_notes', False)
        if not isinstance(include_notes, bool):
            raise ValueError('Include notes must be true or false.')
        sections = ['Please find the selected customer enquiries below.']
        for row in rows:
            item = serialize_lead(row)
            section = [f"Enquiry #{row.id}: {row.subject or 'Customer enquiry'}", f"Name: {row.name}", f"Email: {row.email}", f"Phone: {row.phone or 'Not provided'}",
                       f"Company: {row.company_name or 'Not provided'}", f"Received: {item['created_at_display']} IST", '', 'Message:', row.message]
            if include_notes:
                section.extend(['', f'Status: {row.status}', f"Follow-up: {item['follow_up_date'] or 'Not set'}", 'Internal notes:', row.notes or 'None'])
            sections.append('\n'.join(section))
        subject = f"Customer enquiry: {rows[0].subject or rows[0].name}" if len(rows) == 1 else f'Customer enquiries ({len(rows)})'
        subject = subject.replace('\r', ' ').replace('\n', ' ')[:200]
        return jsonify(success=True, subject=subject, body='\n\n--------------------\n\n'.join(sections))
    except ValueError as error:
        return jsonify(success=False, message=str(error)), 400


@admin_bp.post('/admin/leads/email-draft')
@require_admin
def email_draft():
    """Download an unsent draft; this endpoint never sends an email."""
    try:
        _selected(request.form.getlist('ids'), maximum=50)
        recipients = [email_address(value.strip(), required=True) for value in request.form.get('to', '').split(',')]
        if len(recipients) > 10:
            raise ValueError('Use at most ten recipients.')
        subject = clean_text(request.form, 'subject', 200, True)
        if '\n' in subject or '\r' in subject:
            raise ValueError('Subject must be a single line.')
        body = clean_text(request.form, 'body', 1600000, True)
    except ValueError as error:
        return jsonify(success=False, message=str(error)), 400
    message = EmailMessage()
    message['To'] = ', '.join(recipients)
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=False)
    message['X-Unsent'] = '1'
    message.set_content(body)
    return send_file(io.BytesIO(message.as_bytes()), as_attachment=True, download_name='enquiry-draft.eml', mimetype='message/rfc822')
