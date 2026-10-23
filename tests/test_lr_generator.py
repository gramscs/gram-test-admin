"""LR generation uses shipment reads and in-memory PDFs, without a database."""
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pypdf import PdfReader

from app import create_app
from flask import session
from app.admin import lr
from app.admin.lr_fields import FIELDS
from app.admin.lr_pdf import generate_lr_pdf
from app.models import db


@pytest.fixture
def shipment():
    return SimpleNamespace(
        id=1, consignment_number='GRAM-LR-0107', pickup_date='2026-10-19',
        company=SimpleNamespace(name='Example Consumer Private Limited'), pieces=1020,
        pickup_address='Warehouse, Delhi', pickup_pincode='110030',
        drop_address='Distribution Centre, Mumbai 400001', drop_pincode='400001',
        weight=7000, volume=None, volume_unit='ft3', package_contents={'kind': 'box'},
        status='In Transit',
    )


@pytest.fixture
def lr_app(monkeypatch, shipment):
    application = create_app({
        'TESTING': True, 'SECRET_KEY': 'lr-unit-test-key', 'AUTO_CREATE_TABLES': False,
        'SQLALCHEMY_DATABASE_URI': 'postgresql://unused:unused@127.0.0.1/unused',
        'RATELIMIT_ENABLED': False, 'ADMIN_DATABASE_MODEL': 'legacy',
    })
    monkeypatch.setattr(lr.models, 'using_er', lambda: False)
    from app.admin import auth
    monkeypatch.setattr(auth, 'current_admin_user', lambda: SimpleNamespace(id=1, username='admin', role='administrator') if session.get('admin_username') else None)

    def forbidden(*args, **kwargs):
        raise AssertionError('LRs must not execute SQL or persist form details.')

    monkeypatch.setattr(db.session, 'commit', forbidden)
    monkeypatch.setattr(db.session, 'execute', forbidden)

    class ShipmentReads:
        def filter(self, expression):
            self.ids = expression.right.value
            return self

        def all(self):
            return [shipment] if shipment.id in self.ids else []

    monkeypatch.setattr(lr.models, 'shipment_query', ShipmentReads)
    monkeypatch.setattr(lr, '_label_rows', lambda: dict(
        rows=[shipment], label_page_rows=[dict(id='1', consignment_number=shipment.consignment_number,
        identifier_type='LRN', company=shipment.company.name, pieces=1020, weight='7000',
        volume=None, measurement_mode='weight', drop_address=shipment.drop_address)],
        label_id_format='integer', page=1, total=1, search='',
    ))
    return application


@pytest.fixture
def lr_client(lr_app):
    client = lr_app.test_client()
    with client.session_transaction() as session:
        session['admin_username'] = 'admin'
    return client


@pytest.fixture
def details(shipment):
    item = lr.lr_shipment(shipment)['values']
    item.update(id='1', vehicle_number='WB17AB8495', vehicle_type='32 Feet SXL',
                consignee_name='Example Retail Private Limited', goods_description='Packaged supplies',
                chargeable_weight='7000', cargo_value='2699424.30', invoice_number='INV/327/26-27',
                invoice_date='2026-10-18', invoice_value='2699424.30')
    return item


def test_review_prefills_saved_fields_without_treating_volume_as_weight(lr_client, shipment):
    shipment.weight = None
    shipment.volume = 25
    result = lr_client.post('/admin/lr-generator/review', json={'consignment_ids': ['1']})
    assert result.status_code == 200
    row = result.json['shipments'][0]
    assert row['values']['packages'] == '1020'  # LR limit counts shipments, not boxes.
    assert row['values']['consignor_address'] == 'Warehouse, Delhi\n110030'
    assert row['values']['consignee_address'] == shipment.drop_address  # PIN is not duplicated.
    assert row['values']['lr_number'] == shipment.consignment_number
    assert row['values']['lr_date'] == '2026-10-19'
    assert row['values']['consignor_name'] == shipment.company.name
    assert row['values']['gross_weight'] == row['values']['volume_weight'] == ''
    assert row['volume_ft3'] == '25.000'
    assert 'cargo_status' in row['sources'] and 'invoice_number' not in row['sources']


def test_panel_has_lr_navigation_and_only_template_fields(lr_client):
    routes = [rule.endpoint for rule in lr_client.application.url_map.iter_rules()
              if rule.rule == '/admin/lr-generator']
    assert routes == ['admin.lr_generator_panel']
    result = lr_client.get('/admin/lr-generator')
    assert result.status_code == 200
    assert b'LR Generator' in result.data and b'GRAM-LR-0107' in result.data
    assert b'lr-field-data' in result.data
    assert b'Receipt acknowledgement (optional)' not in result.data
    assert not any(field['name'] in ('receipt_reporting_date', 'receipt_reporting_time', 'gate_out_date',
                                    'gate_out_time', 'detention_reason', 'incidental_expenses', 'pod_remarks')
                   for field in FIELDS)
    assert not any(field['name'] in ('company_pan', 'company_gstin', 'authorised_signatory') for field in FIELDS)


@pytest.mark.parametrize('ids', [None, [], ['2'], ['bad'], ['1'] * 101])
def test_selection_requires_existing_ids_and_has_a_batch_limit(lr_client, ids):
    result = lr_client.post('/admin/lr-generator/review', json={'consignment_ids': ids})
    assert result.status_code == 400 and not result.json['success']


def test_deduplicated_selection_and_details_have_one_to_one_mapping(lr_client, details):
    response = lr_client.post('/admin/lr-generator/generate', json={
        'consignment_ids': ['1', '1'], 'lr_details': [details],
    })
    assert response.status_code == 200
    assert len(PdfReader(BytesIO(response.data)).pages) == 2
    details['id'] = '2'
    response = lr_client.post('/admin/lr-generator/generate', json={
        'consignment_ids': ['1'], 'lr_details': [details],
    })
    assert response.status_code == 400


@pytest.mark.parametrize('field,value', [
    ('lr_number', ''), ('lr_number', 'हिंदी'), ('lr_number', 'bad\x00number'),
    ('invoice_value', 'NaN'), ('invoice_value', '-1'), ('invoice_value', '10.001'),
    ('invoice_value', '1000000000'), ('gross_weight', 'Infinity'), ('gross_weight', '1.0001'),
    ('packages', '0'), ('packages', '1.5'), ('packages', True), ('package_type', 'INVALID'),
    ('invoice_date', '2026-02-30'), ('lr_date', '20261019'),
    ('consignor_address', 'x' * 401), ('goods_description', '<script>\x00</script>'),
])
def test_invalid_form_values_are_rejected_on_server(lr_client, details, field, value):
    details[field] = value
    result = lr_client.post('/admin/lr-generator/generate', json={
        'consignment_ids': ['1'], 'lr_details': [details],
    })
    assert result.status_code == 400 and not result.json['success']


def test_eway_validity_is_checked(lr_client, details):
    details.update(eway_bill_date='2026-10-20', eway_valid_until='2026-10-19')
    result = lr_client.post('/admin/lr-generator/generate', json={
        'consignment_ids': ['1'], 'lr_details': [details],
    })
    assert result.status_code == 400 and 'validity' in result.json['message']


def test_duplicate_lr_numbers_are_rejected(lr_app, shipment, details):
    second = SimpleNamespace(id=2)
    with lr_app.app_context(), pytest.raises(ValueError, match='different LR number'):
        lr._review_details([details, {**details, 'id': '2'}], [shipment, second])


def test_generation_is_authenticated_and_same_origin(lr_app, lr_client, details):
    payload = dict(consignment_ids=['1'], lr_details=[details])
    assert lr_app.test_client().post('/admin/lr-generator/generate', json=payload).status_code == 401
    assert lr_client.post('/admin/lr-generator/generate', json=payload,
                          headers={'Origin': 'https://other.example'}).status_code == 403


@pytest.mark.parametrize('delivery,disposition', [('preview', 'inline'), ('download', 'attachment')])
def test_download_has_two_pages_fixed_artwork_and_no_shipment_writes(lr_client, shipment, details, delivery, disposition):
    baseline = deepcopy(vars(shipment))
    result = lr_client.post('/admin/lr-generator/generate', json={
        'consignment_ids': ['1'], 'lr_details': [details], 'delivery': delivery,
    })
    assert result.status_code == 200 and result.mimetype == 'application/pdf'
    assert result.headers['Content-Disposition'].startswith(disposition)
    assert result.headers['Cache-Control'] == 'no-store'
    reader = PdfReader(BytesIO(result.data))
    assert len(reader.pages) == 2
    text = reader.pages[0].extract_text()
    assert 'GRAM-LR-0107' in text and 'INV/327/26-27' in text
    assert '26,99,424.30' in text and '1020 BOX' in text and '1 Of 2' in text
    assert '07AAGCG9803J1ZC' in text and 'Authorised Signatory' in text
    assert 'TERMS & CONDITIONS' in reader.pages[1].extract_text()
    assert len(reader.pages[0].images) == 2  # Original logo and authorised stamp.
    assert 'LAKONS' not in text and 'DL00000120' not in text and '15-03-2022' not in text
    assert vars(shipment) == baseline


def test_batch_pairs_terms_with_each_lr_and_uses_one_ist_timestamp(lr_app, shipment, details):
    with lr_app.app_context():
        receipt = lr._review_details([details], [shipment])[0]
    second = {**receipt, 'lr_number': 'GRAM-LR-0108', 'consignee_address': ('Long address line & <unit>\n' * 12).strip()}
    now = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo('UTC'))
    result = PdfReader(generate_lr_pdf([receipt, second], generated_at=now))
    assert len(result.pages) == 4
    assert 'GRAM-LR-0107' in result.pages[0].extract_text()
    assert 'GRAM-LR-0108' in result.pages[2].extract_text()
    assert all('19-10-2026 17:30:00 IST' in page.extract_text() for page in result.pages)
    assert result.pages[1].extract_text() == result.pages[3].extract_text()
    template = PdfReader(Path(__file__).resolve().parents[1] / 'app/assets/lr-template.pdf')
    # The whole terms page survives byte-for-byte as the original base content.
    assert template.pages[1].get_contents().get_data() in result.pages[1].get_contents().get_data()
