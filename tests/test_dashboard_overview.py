from app.models import Consignment, Lead, db


def test_dashboard_overview_counts_saved_shipments_and_shows_latest_five(app, admin_client):
    with app.app_context():
        for index, status in enumerate(['Delivered', 'In Transit', 'In Transit', 'Out for Delivery', '', 'Pickup Scheduled']):
            db.session.add(Consignment(consignment_number=f'OVERVIEW{index}', status=status))
        db.session.add(Lead(name='Customer', email='customer@example.test', phone='9876543210', message='Test enquiry'))
        db.session.commit()
    response = admin_client.get('/admin/dashboard')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for name, count in [('total', 6), ('in_transit', 2), ('delivered', 1), ('out_for_delivery', 1)]:
        assert f'data-metric="{name}">{count}</div>' in html
    assert '/admin/leads' not in html
    assert 'Customer leads' not in html
    assert 'OVERVIEW0' not in html
    for index in range(1, 6):
        assert f'OVERVIEW{index}' in html


def test_empty_dashboard_shows_zero_counts_and_onboarding_message(admin_client):
    html = admin_client.get('/admin/dashboard').get_data(as_text=True)
    assert html.count('>0</div>') == 4
    assert 'Add a consignment to get going.' in html


def test_dashboard_keeps_navigation_available_if_database_overview_fails(app, admin_client):
    with app.app_context():
        Consignment.__table__.drop(db.engine)
    response = admin_client.get('/admin/dashboard')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'The database overview is unavailable right now.' in html
    assert 'data-metric="total">—</div>' in html
    assert '/admin/consignments' in html
    assert '/admin/labels' in html
