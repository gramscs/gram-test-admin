"""Authenticated MIS views, immutable report history and private downloads."""
import io
import json
from datetime import UTC, datetime
from functools import wraps
from urllib.parse import urlsplit
from uuid import uuid4

from flask import current_app, jsonify, render_template, request, send_file, session, url_for
from sqlalchemy import func

from app import limiter
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.documents import cleanup_documents, open_stored_document
from app.admin.input_validation import clean_text, payload
from app.admin.mis_exports import MIMES, export_bytes
from app.admin.reporting import COLUMNS, DEFAULT_COLUMNS, IST, analyze, filter_options, normalize_filters, validate_columns
from app.models import db
from app import orm as models


def protect_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            if request.method != 'GET':
                origin = request.headers.get('Origin')
                if origin and urlsplit(origin).netloc != request.host:
                    return jsonify(success=False, message='Report changes must come from this admin app.'), 403
            return fn(*args, **kwargs)
        except ValueError as error:
            db.session.rollback()
            return jsonify(success=False, message=str(error)), 400
        except Exception:
            db.session.rollback()
            current_app.logger.exception('MIS operation failed')
            return jsonify(success=False, message='The report could not be completed. Check the database and storage, then try again.'), 500
    return wrapped


def output_format(value):
    if not isinstance(value, str) or value not in MIMES:
        raise ValueError('Choose Excel, PDF or CSV.')
    return value


def _creator():
    if models.using_er():
        from app.er_adapter import actor_id
        return actor_id()
    return session.get('admin_username')


def view_data(row):
    return {'id': row.id, 'name': row.name, 'notes': row.notes, 'filters': json.loads(row.filters_json), 'columns': json.loads(row.columns_json), 'format': row.output_format}


def report_response(content, filename, fmt):
    response = send_file(io.BytesIO(content), mimetype=MIMES[fmt], as_attachment=True, download_name=filename, max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@admin_bp.get('/admin/mis')
@require_admin
@protect_errors
def mis_panel():
    filters = normalize_filters(request.args.to_dict())
    page = max(1, request.args.get('page', 1, type=int))
    reports = models.MisReport.query.order_by(models.MisReport.generated_at.desc(), models.MisReport.id.desc()).paginate(page=page, per_page=15, error_out=False)
    report_rows = [{'id': row.id, 'name': row.name, 'notes': row.notes, 'format': row.output_format, 'row_count': row.row_count, 'generated_at': row.generated_at.replace(tzinfo=UTC).astimezone(IST).strftime('%d %b %Y, %I:%M %p IST'), 'filters': json.loads(row.filters_json), 'summary': json.loads(row.summary_json)} for row in reports.items]
    return render_template('admin/mis.html', filters=filters, options=filter_options(), columns=COLUMNS, default_columns=DEFAULT_COLUMNS, views=[view_data(row) for row in models.MisView.query.order_by(models.MisView.name).all()], reports=report_rows, pagination=reports)


@admin_bp.get('/admin/mis/export')
@limiter.limit('6 per minute')
@require_admin
@protect_errors
def mis_export():
    return export_response(output_format(request.args.get('format', 'xlsx')))


@protect_errors
def export_response(fmt):
    filters = normalize_filters(request.args.to_dict())
    columns = validate_columns(request.args.getlist('columns') or DEFAULT_COLUMNS)
    report = analyze(filters)
    filename = f'shipment-mis_{datetime.now(IST):%Y%m%d_%H%M%S_IST}.{fmt}'
    return report_response(export_bytes(report, columns, fmt), filename, fmt)


@admin_bp.route('/admin/mis/views', methods=['POST'])
@admin_bp.route('/admin/mis/views/<view_id>', methods=['PUT', 'DELETE'])
@require_admin
@protect_errors
def mis_view(view_id=None):
    data = payload()
    row = db.session.get(models.MisView, models.record_id(view_id)) if view_id else models.MisView()
    if row is None:
        return jsonify(success=False, message='Saved view not found.'), 404
    if request.method == 'DELETE':
        db.session.delete(row)
        db.session.commit()
        return jsonify(success=True)
    name = clean_text(data, 'name', 120, required=True)
    duplicate = models.MisView.query.filter(func.lower(models.MisView.name) == name.lower()).first()
    if duplicate and duplicate.id != row.id:
        raise ValueError('A view with that name already exists. Load it and update it, or use a different name.')
    row.name, row.notes = name, clean_text(data, 'notes', 500)
    row.filters_json = json.dumps(normalize_filters(data.get('filters', {})))
    row.columns_json = json.dumps(validate_columns(data.get('columns', DEFAULT_COLUMNS)))
    row.output_format = output_format(data.get('format', 'xlsx'))
    row.updated_at = datetime.now(UTC)
    if not models.using_er() or row.created_by is None:
        row.created_by = _creator()
    db.session.add(row)
    db.session.commit()
    return jsonify(success=True, view=view_data(row))


@admin_bp.post('/admin/mis/reports')
@limiter.limit('6 per minute')
@require_admin
@protect_errors
def mis_generate():
    from app.admin.consignment_controller import _store_pod_bytes
    data = payload()
    name, notes = clean_text(data, 'name', 120, required=True), clean_text(data, 'notes', 500)
    filters = normalize_filters(data.get('filters', {}))
    columns = validate_columns(data.get('columns', DEFAULT_COLUMNS))
    fmt = output_format(data.get('format', 'xlsx'))
    view_options = {}
    if models.using_er() and data.get('view_id'):
        view = db.session.get(models.MisView, models.record_id(data['view_id']))
        if not view:
            raise ValueError('This saved view no longer exists. Reload the report page.')
        view_options['view_id'] = view.id
    report = analyze(filters)
    content = export_bytes(report, columns, fmt)
    stored = _store_pod_bytes(f'mis-{uuid4().hex}.{fmt}', content, MIMES[fmt])
    try:
        row = models.MisReport(name=name, notes=notes, filters_json=json.dumps(filters), columns_json=json.dumps(columns), summary_json=json.dumps({**report['metrics'], 'client_label': report['client_label'], 'status_label': report['status_label'], 'period_label': report['period_label']}), output_format=fmt, row_count=report['metrics']['total'], file_ref=stored, file_name=f'shipment-mis_{datetime.now(IST):%Y%m%d_%H%M%S_IST}.{fmt}', generated_by=_creator(), **view_options)
        db.session.add(row)
        db.session.commit()
        report_id = row.id
    except Exception:
        db.session.rollback()
        cleanup_documents([stored])
        raise
    return jsonify(success=True, id=report_id, download_url=url_for('admin.mis_download', report_id=report_id))


@admin_bp.get('/admin/mis/reports/<report_id>/download')
@require_admin
@protect_errors
def mis_download(report_id):
    row = db.session.get(models.MisReport, models.record_id(report_id))
    if not row:
        return jsonify(success=False, message='Report not found.'), 404
    try:
        with open_stored_document(row.file_ref) as (source, _):
            return report_response(source.read(), row.file_name, row.output_format)
    except FileNotFoundError:
        return jsonify(success=False, message='The report file is missing. Generate a new report from the saved view.'), 404


@admin_bp.route('/admin/mis/reports/<report_id>', methods=['PUT', 'DELETE'])
@require_admin
@protect_errors
def mis_report_manage(report_id):
    data = payload()
    row = db.session.get(models.MisReport, models.record_id(report_id))
    if not row:
        return jsonify(success=False, message='Report not found.'), 404
    if request.method == 'DELETE':
        stored = row.file_ref
        file = row.file if models.using_er() else None
        db.session.delete(row)
        if file:
            from app.er_adapter import ShipmentDocument, MisReport as ERReport
            db.session.flush()
            if not db.session.query(ShipmentDocument).filter_by(file_id=file.id).first() and not db.session.query(ERReport).filter_by(file_id=file.id).first():
                db.session.delete(file)
        db.session.commit()
        cleanup_documents([stored])
    else:
        row.name = clean_text(data, 'name', 120, required=True)
        row.notes = clean_text(data, 'notes', 500)
        db.session.commit()
    return jsonify(success=True)
