"""Admin dashboard and lead-management routes."""

import io
import json
import logging
from datetime import UTC, datetime

from flask import flash, jsonify, render_template, redirect, send_file, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import DatabaseError, OperationalError

from app import limiter
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.models import Consignment, Lead, NewsletterSubscriber, db

logger = logging.getLogger(__name__)


@admin_bp.route("/admin/dashboard", methods=["GET"])
@require_admin
def dashboard():
    return render_template("admin/dashboard.html")


def _to_json_safe(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _serialize_model_row(model_row, excluded_fields=None):
    excluded_fields = set(excluded_fields or [])
    payload = {}
    for column in model_row.__table__.columns:
        if column.name in excluded_fields:
            continue
        payload[column.name] = _to_json_safe(getattr(model_row, column.name))
    return payload


@admin_bp.route("/admin/generate-backup", methods=["GET"])
@limiter.limit("3 per minute")
@require_admin
def generate_backup():
    admin_user = session.get("admin_username") or "unknown"
    started_at = datetime.now(UTC).isoformat()

    try:
        table_specs = [
            ("consignments", Consignment, {"eta_debug_json"}),
            ("leads", Lead, set()),
            ("newsletter_subscribers", NewsletterSubscriber, set()),
        ]

        backup_payload = {}
        table_counts = {}
        for table_name, model_class, excluded_fields in table_specs:
            rows = model_class.query.order_by(model_class.id.asc()).all()
            backup_payload[table_name] = [
                _serialize_model_row(row, excluded_fields=excluded_fields) for row in rows
            ]
            table_counts[table_name] = len(rows)

        backup_payload["metadata"] = {
            "generated_at": started_at,
            "generated_by": admin_user,
            "table_counts": table_counts,
            "total_rows": sum(table_counts.values()),
        }

        buffer = io.BytesIO(json.dumps(backup_payload, ensure_ascii=True, indent=2).encode("utf-8"))
        buffer.seek(0)

        filename = f"backup_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/json")
    except Exception as exc:
        logger.error("Admin backup generation failed for %s: %s", admin_user, exc, exc_info=True)
        return jsonify({"success": False, "message": "Failed to generate backup."}), 500


@admin_bp.route("/admin/leads", methods=["GET"], endpoint="leads_panel")
@require_admin
def leads_panel():
    try:
        leads = Lead.query.order_by(Lead.created_at.desc(), Lead.id.desc()).all()
        rows = [
            {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "subject": lead.subject,
                "message": lead.message,
                "created_at": lead.created_at.strftime("%Y-%m-%d %H:%M:%S") if lead.created_at else "",
            }
            for lead in leads
        ]
        return render_template("admin/leads.html", leads=rows)
    except (OperationalError, DatabaseError):
        logger.exception("Database error loading leads panel")
        return render_template("admin/leads.html", leads=[], error="Unable to load leads right now.")
    except Exception:
        logger.exception("Unexpected error loading leads panel")
        return render_template("admin/leads.html", leads=[], error="An unexpected error occurred.")


@admin_bp.route("/admin/leads/reject-empty-phone", methods=["POST"])
@require_admin
def reject_empty_phone_leads():
    try:
        deleted_count = (
            Lead.query.filter(
                or_(Lead.phone.is_(None), func.trim(Lead.phone) == "")
            ).delete(synchronize_session=False)
        )
        db.session.commit()
        flash(f"Rejected {deleted_count} lead(s) with empty phone numbers.", "success")
    except Exception:
        db.session.rollback()
        logger.exception("Failed to reject blank-phone leads")
        flash("Unable to reject blank-phone leads right now.", "error")

    return redirect(url_for("admin.leads_panel"))
