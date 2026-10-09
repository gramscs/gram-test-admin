from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, UTC

db = SQLAlchemy()

class Consignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consignment_number = db.Column(db.String(16), unique=True, nullable=False)
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


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(30))
    subject = db.Column(db.String(200))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))


class NewsletterSubscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    subscribed_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))