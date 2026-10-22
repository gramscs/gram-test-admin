"""Bulk documents, bucket separation and measurement choices on both models."""
from auth_support import sign_in, sign_out
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from test_er_dashboard import migrated_admin, populated, legacy_engine, disposable_engine


def image():
    content = io.BytesIO()
    Image.new('RGB', (12, 12), 'green').save(content, 'PNG')
    return content.getvalue()


def pdf():
    content = io.BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=288, height=432); writer.write(content)
    return content.getvalue()


def create(client, number, **fields):
    result = client.post('/admin/consignments/save', json={'rows': [{'consignment_number': number, 'status': 'In Transit', **fields}]})
    assert result.status_code == 200, result.json
    return client.get('/admin/consignments/list', query_string={'search': number}).json['rows'][0]


def upload(client, files, apply=False):
    return client.post('/admin/consignments/documents/bulk-upload', data={
        'apply': str(apply).lower(), 'files': [(io.BytesIO(content), name) for name, content in files]})


def review_and_named_zip(client, app, monkeypatch):
    first, second = create(client, 'BULK-A'), create(client, 'BULK-B')
    files = [('BULK-A_pod.png', image()), ('BULK-A_invoice.pdf', pdf()), ('bulk-b_POD.pdf', pdf())]
    checked = upload(client, files)
    assert checked.status_code == 200 and checked.json['count'] == 3 and not checked.json['applied']
    assert client.get(f"/admin/consignments/{first['id']}/pod").status_code == 404
    applied = upload(client, files, apply=True)
    assert applied.status_code == 200 and applied.json['applied'] and len(applied.json['documents']) == 3
    response = client.post('/admin/consignments/documents/bulk-download', json={'ids': [first['id'], second['id']], 'kind': 'both'})
    assert response.status_code == 200 and response.mimetype == 'application/zip'
    with ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {'BULK-A_pod.png', 'BULK-A_invoice.pdf', 'BULK-B_pod.pdf', 'document-manifest.json'}
        manifest = json.loads(archive.read('document-manifest.json'))
        assert manifest['downloaded'] == 3 and manifest['shipment_count'] == 2
        assert sum(item['status'] == 'not_uploaded' for item in manifest['documents']) == 1
        assert len(PdfReader(io.BytesIO(archive.read('BULK-A_invoice.pdf'))).pages) == 1
        with Image.open(io.BytesIO(archive.read('BULK-A_pod.png'))) as content:
            assert content.size == (12, 12)
    response = client.post('/admin/consignments/documents/bulk-download', json={'search': 'BULK-A', 'kind': 'invoice'})
    with ZipFile(io.BytesIO(response.data)) as archive:
        assert 'BULK-A_invoice.pdf' in archive.namelist() and 'BULK-B_pod.pdf' not in archive.namelist()


def invalid_batch_changes_nothing(client, app, monkeypatch):
    row = create(client, 'BULK-INVALID')
    files = [('BULK-INVALID_pod.png', image()), ('UNKNOWN_invoice.pdf', pdf()), ('BULK-INVALID_invoice.pdf', b'not a PDF')]
    response = upload(client, files, apply=True)
    assert response.status_code == 400 and len(response.json['errors']) == 2
    assert client.get(f"/admin/consignments/{row['id']}/pod").status_code == 404
    duplicate = upload(client, [('BULK-INVALID_pod.png', image()), ('BULK-INVALID_pod.pdf', pdf())], apply=True)
    assert duplicate.status_code == 400 and 'only one' in duplicate.json['errors'][0]['message']
    unsafe = upload(client, [('BULK-INVALID_invoice.svg', b'<svg onload="alert(1)"/>')], apply=True)
    assert unsafe.status_code == 400


def ambiguous_names_and_missing_files(client, app, monkeypatch):
    create(client, 'SANITIZED/A'); create(client, 'SANITIZED_A')
    result = upload(client, [('SANITIZED_A_pod.png', image())], apply=True)
    assert result.status_code == 400 and 'multiple' in result.json['errors'][0]['message']
    row = create(client, 'BULK-MISSING')
    assert upload(client, [('BULK-MISSING_pod.png', image())], apply=True).status_code == 200
    stored = client.get('/admin/consignments/list', query_string={'search': 'BULK-MISSING'}).json['rows'][0]['pod_image']
    from app.admin import consignment_controller
    bucket, object_path = consignment_controller._parse_supabase_pod_value(stored)
    consignment_controller._get_supabase_client().storage.from_(bucket).remove([object_path])
    result = client.post('/admin/consignments/documents/bulk-download', json={'ids': [row['id']], 'kind': 'pod'})
    assert result.status_code == 404 and result.json['documents'][0]['status'] == 'missing'


def bucket_separation(client, app, monkeypatch):
    objects = {}
    class Bucket:
        def __init__(self, name): self.name = name
        def upload(self, path, content, options): objects[self.name, path] = content
        def download(self, path):
            if (self.name, path) not in objects:
                raise FileNotFoundError('Object not found')
            return objects[self.name, path]
        def info(self, path):
            if (self.name, path) not in objects:
                raise FileNotFoundError('Object not found')
            return {'name': path}
        def remove(self, paths):
            for path in paths: objects.pop((self.name, path), None)
    class Storage:
        def from_(self, name): return Bucket(name)
    class Client:
        storage = Storage()
    from app.admin import consignment_controller
    monkeypatch.setenv('SUPABASE_BUCKET', 'pod-uploads')
    monkeypatch.setenv('SUPABASE_INVOICE_BUCKET', 'invoice-uploads')
    monkeypatch.setattr(consignment_controller, '_get_supabase_client', lambda: Client())
    row = create(client, 'BULK-REMOTE')
    response = upload(client, [('BULK-REMOTE_pod.png', image()), ('BULK-REMOTE_invoice.pdf', pdf())], apply=True)
    assert response.status_code == 200, response.json
    stored = client.get('/admin/consignments/list', query_string={'search': 'BULK-REMOTE'}).json['rows'][0]
    assert stored['pod_image'].startswith('supabase:pod-uploads/')
    assert stored['invoice_file'].startswith('supabase:invoice-uploads/')
    assert {bucket for bucket, _ in objects} == {'pod-uploads', 'invoice-uploads'}
    result = client.post('/admin/consignments/documents/bulk-download', json={'ids': [row['id']]})
    assert result.status_code == 200
    with ZipFile(io.BytesIO(result.data)) as archive:
        assert 'BULK-REMOTE_invoice.pdf' in archive.namelist()
    # The modal/single-file path shares the same routing, not just bulk uploads.
    result = client.post(f"/admin/consignments/{row['id']}/invoice", data={'file': (io.BytesIO(image()), 'replacement.png')})
    assert result.status_code == 200 and result.json['invoice_file'].startswith('supabase:invoice-uploads/')


def failed_storage_rolls_back_batch(client, app, monkeypatch):
    row = create(client, 'BULK-ROLLBACK')
    from app.admin import consignment_controller
    real_store = consignment_controller._store_pod_bytes
    def fail_second(filename, content, mime=None, bucket_name=None):
        if Path(filename).stem.endswith('_invoice'): raise RuntimeError('Simulated storage failure')
        return real_store(filename, content, mime, bucket_name)
    monkeypatch.setattr(consignment_controller, '_store_pod_bytes', fail_second)
    result = upload(client, [('BULK-ROLLBACK_pod.png', image()), ('BULK-ROLLBACK_invoice.pdf', pdf())], apply=True)
    assert result.status_code == 500
    assert client.get(f"/admin/consignments/{row['id']}/pod").status_code == 404


def measurement_choice_roundtrip(client, app, monkeypatch):
    row = create(client, 'BULK-VOLUME', measurement_mode='volume', volume='1.234')
    assert row['measurement_mode'] == 'volume' and row['volume'] == '1.234' and row['weight'] is None
    result = client.post('/admin/consignments/save', json={'rows': [{**row, 'measurement_mode': 'weight', 'weight': '20.500'}]})
    assert result.status_code == 200
    row = client.get('/admin/consignments/list', query_string={'search': 'BULK-VOLUME'}).json['rows'][0]
    assert row['measurement_mode'] == 'weight' and row['volume'] == '1.234'
    result = client.post('/admin/consignments/save', json={'rows': [{**row, 'measurement_mode': 'wrong'}]})
    assert result.status_code == 400


SCENARIOS = [review_and_named_zip, invalid_batch_changes_nothing, ambiguous_names_and_missing_files,
             bucket_separation, failed_storage_rolls_back_batch, measurement_choice_roundtrip]


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda fn: fn.__name__)
def test_legacy_documents(admin_client, app, monkeypatch, scenario):
    scenario(admin_client, app, monkeypatch)


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda fn: fn.__name__)
def test_er_documents(migrated_admin, monkeypatch, scenario):
    app, client, _, _ = migrated_admin
    scenario(client, app, monkeypatch)


def test_bulk_upload_can_exceed_normal_form_limit_within_its_own_limit(admin_client):
    import random
    noise = Image.frombytes('RGB', (700, 1600), random.Random(42).randbytes(700 * 1600 * 3))
    content = io.BytesIO(); noise.save(content, 'PNG')
    image_content = content.getvalue()
    files = []
    for index in range(7):
        number = f'LARGE-BULK-{index}'
        create(admin_client, number)
        files.append((number + '_pod.png', image_content))
    assert 20 * 1024 * 1024 < len(image_content) * 7 < 50 * 1024 * 1024
    response = upload(admin_client, files)
    assert response.status_code == 200 and response.json['count'] == 7


def test_bulk_routes_require_authentication_and_block_foreign_origin(client, admin_client):
    sign_out(client)
    assert client.post('/admin/consignments/documents/bulk-download', json={}).status_code == 401
    assert sign_in(admin_client).status_code == 302
    result = admin_client.post('/admin/consignments/documents/bulk-upload', headers={'Origin': 'https://unrelated.example.test'})
    assert result.status_code == 403


def test_er_viewer_can_download_but_cannot_bulk_upload(migrated_admin):
    app, client, storage, destination = migrated_admin
    from sqlalchemy import update
    from database.models import Base
    users = Base.metadata.tables['admin_users']
    with destination.begin() as connection:
        connection.execute(update(users).where(users.c.username == 'admin').values(role='viewer'))
    result = client.post('/admin/consignments/documents/bulk-download', json={'search': 'LRN-001', 'kind': 'pod'})
    assert result.status_code == 200 and result.mimetype == 'application/zip'
    result = upload(client, [('LRN-001_pod.png', image())], apply=True)
    assert result.status_code == 403
