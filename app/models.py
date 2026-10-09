from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, UTC
from sqlalchemy import false, true

db = SQLAlchemy()

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    address = db.Column(db.Text)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=true())
    locations = db.relationship("CompanyLocation", cascade="all, delete-orphan", lazy="selectin", order_by="CompanyLocation.id")


class CompanyLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    address = db.Column(db.Text, nullable=False)
    pincode = db.Column(db.String(6))
    is_default = db.Column(db.Boolean, nullable=False, default=False, server_default=false())


class Consignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consignment_number = db.Column(db.String(64), unique=True, nullable=False)
    identifier_type = db.Column(db.String(12), nullable=False, default="LRN", server_default="LRN")
    pieces = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    chargeable_weight = db.Column(db.Numeric(12, 3))
    chargeable_volume = db.Column(db.Numeric(12, 3))
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), index=True)
    company = db.relationship("Company", lazy="joined")
    status = db.Column(db.String(200))
    pickup_pincode = db.Column(db.String(6))
    pickup_address = db.Column(db.Text)
    pickup_tag = db.Column(db.String(100))
    pickup_date = db.Column(db.String(100))
    drop_pincode = db.Column(db.String(6))
    drop_address = db.Column(db.Text)
    drop_tag = db.Column(db.String(100))
    drop_date = db.Column(db.String(100))
    eta = db.Column(db.String(100))
    eta_debug_json = db.Column(db.Text)
    # URL or internal path to the Proof-Of-Delivery (POD) image/file
    pod_image = db.Column(db.String(1024))
    pod_original_name = db.Column(db.String(255))
    invoice_file = db.Column(db.String(1024))
    invoice_original_name = db.Column(db.String(255))


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(30))
    subject = db.Column(db.String(200))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    company_name = db.Column(db.String(200))
    status = db.Column(db.String(20), nullable=False, default="New", server_default="New")
    notes = db.Column(db.Text)
    follow_up_date = db.Column(db.Date)


class NewsletterSubscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    subscribed_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))


class MisView(db.Model):
    """Reusable reporting settings; relative periods refresh when loaded."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    notes = db.Column(db.String(500), nullable=False, default='')
    filters_json = db.Column(db.Text, nullable=False)
    columns_json = db.Column(db.Text, nullable=False)
    output_format = db.Column(db.String(10), nullable=False, default='xlsx')
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    created_by = db.Column(db.String(120))


class MisReport(db.Model):
    """Immutable generated report files, with an editable display name and notes."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    notes = db.Column(db.String(500), nullable=False, default='')
    output_format = db.Column(db.String(10), nullable=False)
    filters_json = db.Column(db.Text, nullable=False)
    columns_json = db.Column(db.Text, nullable=False)
    summary_json = db.Column(db.Text, nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    generated_by = db.Column(db.String(120))
    row_count = db.Column(db.Integer, nullable=False)
    file_ref = db.Column(db.String(1024), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
