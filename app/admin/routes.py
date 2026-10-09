"""Shipment dashboard and database backup routes."""

import io
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request, send_file, session
from sqlalchemy.exc import DatabaseError, OperationalError

from app import limiter
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.models import db

logger = logging.getLogger(__name__)


@admin_bp.route("/admin/dashboard", methods=["GET"])
@require_admin
def dashboard():
    from app.admin.reporting import analyze, chart_data, filter_options, normalize_filters
    try:
        filters = normalize_filters(request.args.to_dict())
        report = analyze(filters)
        return render_template('admin/dashboard.html', metrics=report['metrics'], recent_shipments=report['recent'], report=report, chart_data=chart_data(report), filters=filters, options=filter_options())
    except ValueError as error:
        return render_template('admin/dashboard.html', metrics={}, recent_shipments=[], error=str(error)), 400
    except (OperationalError, DatabaseError):
        db.session.rollback()
        logger.exception('Database error loading dashboard overview')
        return render_template('admin/dashboard.html', metrics={}, recent_shipments=[], error='The database overview is unavailable right now. You can still open your workspace tools.')


@admin_bp.route("/admin/generate-backup", methods=["GET"])
@limiter.limit("3 per minute")
@require_admin
def generate_backup():
    from app.admin.backup import build_complete_backup, collect_admin_data
    output_format = request.args.get('format', 'zip')
    if output_format not in ('zip', 'json'):
        return jsonify(success=False, message='Choose ZIP or JSON backup format.'), 400
    admin_user = session.get('admin_username') or 'unknown'
    try:
        payload = collect_admin_data(admin_user)
        stamp = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y%m%d_%H%M%S_IST')
        if output_format == 'json':
            buffer = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))
            response = send_file(buffer, as_attachment=True, download_name=f'backup_{stamp}.json', mimetype='application/json')
        else:
            buffer, summary = build_complete_backup(payload)
            suffix = '_incomplete' if summary['status'] == 'incomplete' else ''
            response = send_file(buffer, as_attachment=True, download_name=f'backup_{stamp}{suffix}.zip', mimetype='application/zip')
            response.headers['X-Backup-Status'] = summary['status']
        response.call_on_close(buffer.close)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except Exception:
        logger.exception('Admin backup generation failed')
        return jsonify(success=False, message='Failed to generate backup. Try again or download data-only JSON.'), 500
