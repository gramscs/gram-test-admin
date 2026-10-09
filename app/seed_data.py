"""Repeatable sample records for local SQLite development."""

import os
from datetime import UTC, datetime, timedelta

import click
from flask.cli import with_appcontext

from app.models import Consignment, Lead, NewsletterSubscriber, db


@click.command("seed-demo")
@with_appcontext
def seed_demo():
    """Add sample shipments and enquiries to the local SQLite database."""
    if os.getenv("FLASK_ENV", "development").strip().lower() == "production" or db.engine.dialect.name != "sqlite":
        raise click.ClickException("Demo data is only supported in local SQLite development. Set DATABASE_URL=sqlite:///instance/admin.db for this command.")

    db.create_all()
    today = datetime.now(UTC).date()
    statuses = ["Pickup Scheduled", "In Transit", "Out for Delivery", "Delivered"]
    origins = [("Delhi", "110001"), ("Mumbai", "400001"), ("Kolkata", "700001"), ("Bengaluru", "560001"), ("Chennai", "600001")]
    added = {"shipments": 0, "enquiries": 0, "subscribers": 0}
    try:
        for index in range(1, 26):
            number = f"DEMO{index:04d}"
            if Consignment.query.filter_by(consignment_number=number).first():
                continue
            origin, pickup_pin = origins[(index - 1) % len(origins)]
            destination, drop_pin = origins[index % len(origins)]
            status = statuses[(index - 1) % len(statuses)]
            pickup_date = today - timedelta(days=1 + index % 7)
            drop_date = today - timedelta(days=index % 4) if status == "Delivered" else today + timedelta(days=1 + index % 4)
            db.session.add(Consignment(
                consignment_number=number,
                status=status,
                pickup_address=f"Demo Warehouse {index}, {origin}",
                pickup_pincode=pickup_pin,
                pickup_tag=origin,
                pickup_date=pickup_date.isoformat(),
                drop_address=f"Demo Customer {index}, {destination}",
                drop_pincode=drop_pin,
                drop_tag=destination,
                drop_date=drop_date.isoformat(),
                eta=f"{drop_date.isoformat()} 18:00",
            ))
            added["shipments"] += 1

        subjects = ["Warehouse enquiry", "Transport quotation", "Shipment tracking", "Delivery schedule", "Bulk shipping enquiry"]
        for index, subject in enumerate(subjects, start=1):
            email = f"demo.lead.{index}@example.test"
            if Lead.query.filter_by(email=email).first():
                continue
            db.session.add(Lead(
                name=f"Demo Customer {index}", email=email,
                phone=f"900000000{index}", subject=subject,
                message=f"Sample enquiry for testing the admin panel: {subject.lower()}.",
                created_at=datetime.now(UTC) - timedelta(days=index - 1),
            ))
            added["enquiries"] += 1

        for index in range(1, 4):
            email = f"demo.subscriber.{index}@example.test"
            if not NewsletterSubscriber.query.filter_by(email=email).first():
                db.session.add(NewsletterSubscriber(email=email))
                added["subscribers"] += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise click.ClickException("Unable to add demo data; database changes were rolled back.") from None

    click.echo(f"Added {added['shipments']} demo shipments, {added['enquiries']} enquiries, and {added['subscribers']} newsletter subscribers.")
    click.echo("Existing records were preserved. Running this command again will not duplicate demo records.")
