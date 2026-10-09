from flask import Blueprint

admin_bp = Blueprint("admin", __name__)

# Import route modules for their side effects after the blueprint exists.
from app.admin import auth_routes  # noqa: E402,F401
from app.admin import routes  # noqa: E402,F401
from app.admin import consignment_controller  # noqa: E402,F401
