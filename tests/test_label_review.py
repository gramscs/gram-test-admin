"""Reviewed commercial labels, durable sequences and unchanged databases."""
from auth_support import sign_in, sign_out
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import io
import json
from uuid import UUID, uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import inspect, text

from app.models import db
from app.admin import label_runs, labels
from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine
from label_helpers import label_form


@pytest.fixture
def panel(app, admin_client):
    return app, admin_client


def create(client, count=1):
    rows = [{'consignment_number': f'LABEL-{index:03d}', 'identifier_type': 'AWB', 'pieces': 2,
        'pickup_date': '2026-10-09', 'pickup_address': '429 Jagraj Gram, Delhi',
        'drop_address': 'Ms Ashiya, Delhi', 'pickup_pincode': '110001', 'drop_pincode': '110037',
        'weight': '100', 'volume': '1.5',
        'measurement_mode': 'volume' if index % 2 else 'weight'} for index in range(count)]
    saved = client.post('/admin/consignments/save', json={'rows': rows})
    assert saved.status_code == 200, saved.json
    found = client.get('/admin/consignments/list', query_string={'search': 'LABEL-', 'per_page': 100}).json['rows']
    return sorted(found, key=lambda row: row['consignment_number'])


def snapshot(app, *, include_label_runs=True):
    with app.app_context():
        with db.engine.connect() as connection:
            return {name: [tuple(row) for row in connection.execute(text(f'SELECT * FROM "{name}"')).all()]
                    for name in sorted(inspect(db.engine).get_table_names())
                    if include_label_runs or name != 'label_runs'}


def pages(response):
    assert response.status_code == 200, response.json
    return PdfReader(io.BytesIO(response.data)).pages


def test_review_and_pdf_use_db_fields_without_changing_shipment_records(panel, monkeypatch):
    app, client = panel
    rows = create(client, 2)
    before = snapshot(app, include_label_runs=False)
    review = client.post('/admin/labels/review', json={'consignment_ids': [row['id'] for row in rows]})
    assert review.status_code == 200 and review.json['shipments'][0]['pickup_date'] == '09/10/2026'
    data = label_form([row['id'] for row in rows])
    details = json.loads(data['label_details'])
    details[0].update(invoice_amount='6000.50', payment_mode='to-pay', eway_bill_exists=True, eway_bill_number='123456789012')
    details[1].update(invoice_amount='8000', payment_mode='cash-on-delivery', goods_description='Tyres & wheels <boxed>', eway_bill_number='ignore unchecked')
    data['label_details'] = json.dumps(details)
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 10, 11))
    output = pages(client.post('/admin/labels/generate', data=data))
    assert len(output) == 4
    for index, page in enumerate(output):
        extracted = page.extract_text()
        assert 'GRAM SCS' not in extracted and 'GRAM B2B' not in extracted
        assert 'SHIPMENT /' not in extracted and 'AWB' not in extracted
        assert '09/10/2026' in extracted
        assert rows[index // 2]['consignment_number'] in extracted
        if index < 2:
            assert '100.000 kg' in extracted and '1.500 ft³' not in extracted
        else:
            assert '1.500 ft³' in extracted and '100.000 kg' not in extracted
        assert 'PIECE' in extracted and f'{index % 2 + 1} of 2' in extracted
        assert f'B2B-20261011-{index // 2 + 1:04d}' in extracted
        assert f'B2B-2026-27-{index // 2 + 1:04d}' in extracted
        assert '429 Jagraj Gram' in extracted and 'Ms Ashiya' in extracted
        assert list(page.images), 'GRAM logo must be embedded'
        embedded = [font.get_object().get('/BaseFont', '') for font in page['/Resources']['/Font'].values()]
        assert any('LiberationSans' in name for name in embedded)
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (288, 432)
        if index < 2:
            assert 'Rs 6,000.50' in extracted and 'TO PAY' in extracted
            assert 'EWBN:' in extracted and '123456789012' in extracted
        else:
            assert 'CASH ON DELIVERY' in extracted and 'Tyres & wheels <boxed>' in extracted
            assert 'EWBN' not in extracted and 'ignore unchecked' not in extracted
    assert snapshot(app, include_label_runs=False) == before
    from database.models import LabelRun
    with app.app_context():
        run = db.session.get(LabelRun, UUID(data['job_id']))
        assert run.shipment_count == 2 and run.financial_year == '2026-27'


def test_fifty_shipments_and_reprints_keep_one_number_per_shipment(panel, monkeypatch):
    app, client = panel
    rows = create(client, 50)
    ids = [row['id'] for row in rows]
    reviewed = client.post('/admin/labels/review', json={'consignment_ids': ids})
    assert reviewed.status_code == 200 and len(reviewed.json['shipments']) == 50
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 10, 11))
    data = label_form(ids, delivery='preview')
    first = pages(client.post('/admin/labels/generate', data=data))
    second = pages(client.post('/admin/labels/generate', data={**data, 'delivery': 'download', 'barcode': '0'}))
    assert len(first) == len(second) == 100
    for index in range(50):
        code = f'B2B-20261011-{index + 1:04d}'
        assert code in first[index * 2].extract_text() and code in first[index * 2 + 1].extract_text()
        assert code in second[index * 2].extract_text()
    from database.models import LabelRun
    with app.app_context():
        run = db.session.get(LabelRun, UUID(data['job_id']))
        assert run.shipment_count == 50 and run.financial_year == '2026-27'
    third = pages(client.post('/admin/labels/generate', data=label_form(ids[:1])))
    assert 'B2B-20261011-0051' in third[0].extract_text()
    assert 'B2B-2026-27-0051' in third[0].extract_text()


@pytest.mark.parametrize('change', [
    {'invoice_amount': ''}, {'invoice_amount': '-1'}, {'invoice_amount': 'NaN'},
    {'invoice_amount': 'Infinity'}, {'invoice_amount': '1.001'}, {'invoice_amount': True},
    {'invoice_amount': '1000000000'}, {'payment_mode': 'cash'}, {'goods_description': ''},
    {'goods_description': 'x' * 161}, {'eway_bill_exists': 'yes'},
    {'sender_name': 'x' * 121}, {'receiver_name': None}, {'sender_name': 123},
    {'receiver_name': 'Name\nInjected'}, {'sender_name': 'Name\x7f'},
    {'eway_bill_exists': True, 'eway_bill_number': '123'},
])
def test_invalid_label_details_do_not_allocate_or_touch_shipments(app, admin_client, change):
    row = create(admin_client)[0]
    before = snapshot(app)
    data = label_form([row['id']])
    details = json.loads(data['label_details']); details[0].update(change)
    data['label_details'] = json.dumps(details)
    rejected = admin_client.post('/admin/labels/generate', data=data)
    assert rejected.status_code == 400 and rejected.json['success'] is False
    assert snapshot(app) == before


@pytest.mark.parametrize('box_amounts', [[], ['50.25', '7000'], ['NaN', True]])
def test_old_invoice_drafts_use_only_the_shared_shipment_amount(app, admin_client, box_amounts):
    row = create(admin_client)[0]
    before = snapshot(app, include_label_runs=False)
    data = label_form([row['id']])
    details = json.loads(data['label_details'])
    details[0]['box_invoice_amounts'] = box_amounts
    data['label_details'] = json.dumps(details)
    output = pages(admin_client.post('/admin/labels/generate', data=data))
    assert len(output) == 2
    assert all('Rs 6,000.00' in page.extract_text() for page in output)
    assert snapshot(app, include_label_runs=False) == before


def test_run_cannot_be_reused_for_different_details_or_owner(app, admin_client):
    row = create(admin_client)[0]
    data = label_form([row['id']])
    assert admin_client.post('/admin/labels/generate', data=data).status_code == 200
    details = json.loads(data['label_details']); details[0]['invoice_amount'] = '9'
    changed = admin_client.post('/admin/labels/generate', data={**data, 'label_details': json.dumps(details)})
    assert changed.status_code == 409
    from database.models import AdminUser
    with app.app_context():
        from conftest import AUTH_USER_ID
        db.session.query(AdminUser).filter_by(username='admin').one().supabase_user_id = None
        db.session.flush()
        db.session.add(AdminUser(username='another-admin', role='administrator', active=True, supabase_user_id=AUTH_USER_ID))
        db.session.commit()
    assert sign_in(admin_client).status_code == 302
    assert admin_client.post('/admin/labels/generate', data=data).status_code == 409


def test_render_failure_allocates_nothing(app, admin_client, monkeypatch):
    row = create(admin_client)[0]
    before = snapshot(app)
    def fail(*args): raise RuntimeError('Simulated PDF error')
    monkeypatch.setattr(labels, 'generate_labels_pdf', fail)
    response = admin_client.post('/admin/labels/generate', data=label_form([row['id']]))
    assert response.status_code == 500
    assert 'server error log' in response.json['message']
    assert 'numbering files' not in response.json['message']
    assert snapshot(app) == before


def test_missing_label_table_recovers_with_additive_upgrade_and_keeps_existing_records(app, admin_client):
    from database.models import LabelRun
    row = create(admin_client)[0]
    before = snapshot(app, include_label_runs=False)
    # Only this test's isolated PostgreSQL schema is changed.
    with app.app_context():
        db.session.remove()
        LabelRun.__table__.drop(db.engine)
    runner = app.test_cli_runner()
    check = runner.invoke(args=['check-db'])
    assert check.exit_code != 0 and 'table label_runs' in check.output

    data = label_form([row['id']], delivery='preview')
    response = admin_client.post('/admin/labels/generate', data=data)
    assert response.status_code == 503
    assert 'python migrations/002_label_runs.py --apply' in response.json['message']
    assert 'numbering files' not in response.json['message']
    with app.app_context():
        assert not inspect(db.engine).has_table('label_runs'), 'Requests must not create schema automatically'
        assert db.session.execute(text('SELECT 1')).scalar() == 1
    assert snapshot(app, include_label_runs=False) == before

    upgraded = runner.invoke(args=['upgrade-db'])
    assert upgraded.exit_code == 0, upgraded.output
    assert runner.invoke(args=['check-db']).exit_code == 0
    assert snapshot(app, include_label_runs=False) == before
    first = pages(admin_client.post('/admin/labels/generate', data=data))
    assert len(first) == row['pieces']
    assert row['consignment_number'] in first[0].extract_text()
    with app.app_context():
        run = db.session.get(LabelRun, UUID(data['job_id']))
        assignments = list(run.assignments)
        assert run.shipment_count == 1

    # Running the command again preserves numbering and reuses preview/download assignments.
    assert runner.invoke(args=['upgrade-db']).exit_code == 0
    second = pages(admin_client.post('/admin/labels/generate', data={**data, 'delivery': 'download'}))
    assert [page.extract_text() for page in first] == [page.extract_text() for page in second]
    with app.app_context():
        assert db.session.query(LabelRun).count() == 1
        assert db.session.get(LabelRun, UUID(data['job_id'])).assignments == assignments
    assert snapshot(app, include_label_runs=False) == before


def test_locked_sequences_parallel_requests_date_and_financial_year_rollovers(app, monkeypatch):
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 3, 31))
    def allocate(index):
        with app.app_context():
            return label_runs.render_label_run(str(uuid4()), 'admin', [{'id': str(index)}], lambda assignments: assignments)[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(allocate, range(20)))
    assert {run['order_id'] for run in runs} == {f'B2B-20260331-{number:04d}' for number in range(1, 21)}
    assert {run['invoice_number'] for run in runs} == {f'B2B-2025-26-{number:04d}' for number in range(1, 21)}
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 4, 1))
    april = allocate(21)
    assert april['order_id'] == 'B2B-20260401-0001' and april['invoice_number'] == 'B2B-2026-27-0001'
    monkeypatch.setattr(label_runs, 'order_date', lambda: date(2026, 4, 2))
    next_day = allocate(22)
    assert next_day['order_id'] == 'B2B-20260402-0001' and next_day['invoice_number'] == 'B2B-2026-27-0002'


def test_numbering_survives_new_app_context_and_rejects_changed_batch(app):
    first_job = str(uuid4())
    with app.app_context():
        first = label_runs.render_label_run(first_job, 'admin', [{'id': 'shipment'}], lambda assignments: assignments)
    with app.app_context():
        assert label_runs.render_label_run(first_job, 'admin', [{'id': 'shipment'}], lambda assignments: assignments) == first
        with pytest.raises(label_runs.LabelRunConflict):
            label_runs.render_label_run(first_job, 'admin', [{'id': 'different'}], lambda assignments: assignments)


def test_review_limits_missing_ids_validation_origin_and_login(app, admin_client, client):
    row = create(admin_client)[0]
    for ids in [[], [999999], [row['id']] * 501]:
        assert admin_client.post('/admin/labels/review', json={'consignment_ids': ids}).status_code == 400
    assert admin_client.post('/admin/labels/review', json={'consignment_ids': [row['id']]}, headers={'Origin': 'https://other.test'}).status_code == 403
    assert admin_client.post('/admin/labels/generate', data={'consignment_ids': [row['id']]}).status_code == 400
    sign_out(admin_client)
    assert client.post('/admin/labels/review', json={'consignment_ids': [row['id']]}).status_code == 401


def test_label_id_format_and_outdated_detail_ids_are_actionable_and_do_not_allocate(panel):
    app, client = panel
    row = create(client)[0]
    assert 'data-id-format="uuid"' in client.get('/admin/labels').get_data(as_text=True)
    invalid = client.post('/admin/labels/review', json={'consignment_ids': ['label-card-0']})
    assert invalid.status_code == 400 and 'selection is out of date' in invalid.json['message']
    data = label_form([row['id']])
    details = json.loads(data['label_details']); details[0]['id'] = 'label-amount-0'
    data['label_details'] = json.dumps(details)
    before = snapshot(app)
    rejected = client.post('/admin/labels/generate', data=data)
    assert rejected.status_code == 400 and 'details are out of date' in rejected.json['message']
    assert snapshot(app) == before
    assert client.post('/admin/labels/generate', data=label_form([row['id']])).status_code == 200
