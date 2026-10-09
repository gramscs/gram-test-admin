"""
Session-based authentication utilities for the admin module.

Environment variables
---------------------
SECRET_KEY           Flask session signing key (required by app config).
ADMIN_USERNAME       Admin login username (default: admin).
ADMIN_PASSWORD_HASH  Werkzeug-hashed password for the admin user (required in production).
"""

import logging
import os
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

ADMIN_USERNAME: str = (os.environ.get("ADMIN_USERNAME") or "admin").strip() or "admin"


def _resolve_admin_password_hash() -> str:
    configured = (os.environ.get("ADMIN_PASSWORD_HASH") or "").strip()
    if configured:
        return configured

    plain_password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    if plain_password:
        return generate_password_hash(plain_password)

    e2e_password = (os.environ.get("ADMIN_E2E_PASSWORD") or "").strip()
    if e2e_password:
        return generate_password_hash(e2e_password)

    if os.getenv("FLASK_ENV", "").strip().lower() != "production":
        logger.warning(
            "ADMIN_PASSWORD_HASH not set; using local development default password (admin-pass)."
        )
        return generate_password_hash("admin-pass")

    raise RuntimeError("ADMIN_PASSWORD_HASH is required and must be set in environment variables.")


def _resolve_admin_username() -> str:
    return (os.environ.get("ADMIN_USERNAME") or "admin").strip() or "admin"


ADMIN_PASSWORD_HASH: str = _resolve_admin_password_hash()
ADMIN_SESSION_KEY = "admin_authenticated"
ADMIN_SESSION_USERNAME_KEY = "admin_username"


def check_admin_credentials(username: str, password: str) -> bool:
    """Return True when username and password match configured admin credentials."""
    if username != _resolve_admin_username():
        return False

    try:
        return check_password_hash(ADMIN_PASSWORD_HASH, password)
    except Exception:
        return False


def login_admin(username: str | None = None) -> None:
    """Mark the current session as authenticated admin."""
    session[ADMIN_SESSION_KEY] = True
    if username:
        session[ADMIN_SESSION_USERNAME_KEY] = username


def logout_admin() -> None:
    """Clear admin authentication state from session."""
    session.pop(ADMIN_SESSION_KEY, None)
    session.pop(ADMIN_SESSION_USERNAME_KEY, None)


def is_admin_authenticated() -> bool:
    """Return True when current session is authenticated as admin."""
    return bool(session.get(ADMIN_SESSION_KEY))


def require_admin(f):
    """View decorator that enforces admin session authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if is_admin_authenticated():
            return f(*args, **kwargs)

        wants_json = (
            request.path.startswith("/api/")
            or "application/json" in (request.accept_mimetypes.best or "")
            or (request.content_type or "").startswith("application/json")
        )
        if wants_json:
            return jsonify({"error": "Authentication required"}), 401

        return redirect(url_for("admin.login"))

    return decorated
