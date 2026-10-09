from app import create_app
from app.models import Consignment, Lead, NewsletterSubscriber, db


def test_seed_demo_is_repeatable_and_visible_in_admin(app, admin_client):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    assert "Added 25 demo shipments, 5 enquiries, and 3 newsletter subscribers" in result.output
    response = admin_client.get("/admin/consignments/list", query_string={"search": "DEMO", "per_page": 100})
    assert response.status_code == 200
    assert response.get_json()["total"] == 25
    rows = response.get_json()["rows"]
    assert {row["status"] for row in rows} == {"Pickup Scheduled", "In Transit", "Out for Delivery", "Delivered"}
    assert all(row["pickup_address"] and row["drop_address"] for row in rows)
    assert b"Demo Customer 1" in admin_client.get("/admin/leads").data
    repeated = app.test_cli_runner().invoke(args=["seed-demo"])
    assert repeated.exit_code == 0
    assert "Added 0 demo shipments, 0 enquiries, and 0 newsletter subscribers" in repeated.output
    with app.app_context():
        assert Consignment.query.count() == 25
        assert Lead.query.count() == 5
        assert NewsletterSubscriber.query.count() == 3


def test_seed_demo_preserves_existing_records(app):
    with app.app_context():
        db.session.add(Consignment(consignment_number="DEMO0001", status="Delivered", pickup_address="Keep this address"))
        db.session.add(Consignment(consignment_number="REAL001", status="In Transit"))
        db.session.add(Lead(name="Keep this name", email="demo.lead.1@example.test", message="Keep this message"))
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0
    with app.app_context():
        assert Consignment.query.count() == 26
        assert Consignment.query.filter_by(consignment_number="DEMO0001").one().pickup_address == "Keep this address"
        assert Lead.query.filter_by(email="demo.lead.1@example.test").one().name == "Keep this name"


def test_seed_demo_refuses_production(app, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 1
    with app.app_context():
        assert Consignment.query.count() == 0


def test_seed_demo_refuses_remote_postgres_without_connecting(app, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:dummy@db.example.supabase.co:5432/postgres")
    remote = create_app({"INSTANCE_PATH": str(tmp_path / "remote"), "AUTO_CREATE_TABLES": False})
    result = remote.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 1
    assert "only supported in local SQLite" in result.output
    with remote.app_context():
        db.engine.dispose()
