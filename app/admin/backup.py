"""Complete admin-data and document archives, with explicit retrieval reports."""

from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from flask import current_app
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from sqlalchemy import select
from werkzeug.utils import secure_filename

from app.admin.documents import DOCUMENTS, open_stored_document
from app.models import db

TABLE_KEYS = {'consignment': 'consignments', 'company': 'companies', 'company_location': 'company_locations',
              'lead': 'leads', 'newsletter_subscriber': 'newsletter_subscribers'}


class DocumentReadError(Exception):
    pass


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return value.isoformat() if callable(getattr(value, 'isoformat', None)) else str(value)


def collect_admin_data(admin_user):
    """Include every column and row of every application-owned model table."""
    payload, counts = {}, {}
    for table in db.metadata.sorted_tables:
        key = TABLE_KEYS.get(table.name, table.name)
        result = db.session.execute(select(table).order_by(*table.primary_key.columns)).mappings()
        payload[key] = [{name: _json_value(value) for name, value in row.items()} for row in result]
        counts[key] = len(payload[key])
    payload['metadata'] = {'generated_at': datetime.now(UTC).isoformat(), 'generated_by': admin_user,
                           'table_counts': counts, 'total_rows': sum(counts.values()), 'format_version': 2}
    return payload


def _safe_name(name):
    return (secure_filename(str(name or '')) or 'document')[-180:]


def _reference_key(value):
    if value.startswith('supabase:'):
        return value
    try:
        return str((Path(current_app.instance_path, 'uploads') / value).resolve())
    except (ValueError, OSError, RuntimeError):
        return 'invalid:' + value


def _remote_uploads(client, bucket):
    """List the namespace used by this app, including nested legacy folders."""
    folders = ['consignments']
    while folders:
        folder = folders.pop(0)
        if len(folder.split('/')) > 64:
            raise ValueError('Storage folder nesting is too deep.')
        offset, seen = 0, set()
        while True:
            entries = client.storage.from_(bucket).list(folder, {'limit': 100, 'offset': offset, 'sortBy': {'column': 'name', 'order': 'asc'}})
            if not isinstance(entries, list):
                raise ValueError('Invalid storage listing.')
            for item in entries:
                name = item.get('name')
                if not name or name in ('.', '..') or '/' in name or '\\' in name or name in seen:
                    raise ValueError('Invalid or repeated storage listing entry.')
                seen.add(name)
                path = folder + '/' + name
                if item.get('id') is None and item.get('metadata') is None:
                    folders.append(path)
                else:
                    yield f'supabase:{bucket}/{path}'
            if len(entries) < 100:
                break
            offset += len(entries)


def _inventory(referenced, report):
    root = Path(current_app.instance_path, 'uploads')
    def listing_error(error):
        report['inventory_errors'].append({'source': 'local_uploads', 'message': 'Part of the local upload folder could not be listed.'})
    try:
        if root.exists():
            for directory, folders, files in os.walk(root, onerror=listing_error, followlinks=False):
                folders.sort()
                paths = [Path(directory, name) for name in sorted(files)]
                paths.extend(Path(directory, name) for name in folders if Path(directory, name).is_symlink())
                for path in paths:
                    value = str(path.relative_to(root))
                    if _reference_key(value) not in referenced:
                        yield value, 'local_uploads'
    except Exception:
        report['inventory_errors'].append({'source': 'local_uploads', 'message': 'The local upload folder could not be fully listed.'})
    from app.admin.consignment_controller import _get_supabase_client, _parse_supabase_pod_value
    buckets = set()
    for value in referenced:
        if value.startswith('supabase:'):
            try:
                buckets.add(_parse_supabase_pod_value(value)[0])
            except ValueError:
                report['inventory_errors'].append({'source': 'supabase_uploads', 'message': 'A legacy storage reference is invalid.'})
    configured = bool(os.getenv('SUPABASE_URL', '').strip() and os.getenv('SUPABASE_KEY', '').strip())
    if configured:
        buckets.add(os.getenv('SUPABASE_BUCKET', 'pod-uploads'))
    if not buckets:
        return
    client = _get_supabase_client()
    for bucket in sorted(buckets):
        try:
            if client is None:
                raise RuntimeError('Storage is unavailable.')
            for value in _remote_uploads(client, bucket):
                if value not in referenced:
                    yield value, 'supabase_uploads'
        except Exception:
            report['inventory_errors'].append({'source': 'supabase_uploads', 'bucket': bucket, 'namespace': 'consignments/',
                                                'message': 'The admin upload namespace could not be fully listed.'})


def _pack_document(archive, reference, archive_path):
    """Finish reading before creating an entry, so read failures leave no partial file."""
    with SpooledTemporaryFile(max_size=1024*1024, mode='w+b') as staged:
        size, digest = 0, hashlib.sha256()
        try:
            with open_stored_document(reference) as (source, _):
                while chunk := source.read(1024*1024):
                    staged.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except (FileNotFoundError, ValueError):
            raise
        except Exception as error:
            raise DocumentReadError() from error
        staged.seek(0)
        with archive.open(archive_path, 'w', force_zip64=True) as target:
            while chunk := staged.read(1024*1024):
                target.write(chunk)
    return size, digest.hexdigest()


def _add_document(archive, report, value, path, **details):
    entry = {**details, 'source_reference': value, 'archive_path': None, 'status': 'not_attached'}
    if value:
        try:
            size, checksum = _pack_document(archive, value, path)
            entry.update(archive_path=path, status='included', bytes=size, sha256=checksum)
        except FileNotFoundError:
            entry.update(status='missing', message='The referenced file is missing from storage.')
        except ValueError:
            entry.update(status='invalid_reference', message='The file reference is outside supported admin storage.')
        except DocumentReadError:
            entry.update(status='unavailable', message='The file could not be retrieved from storage.')
        except Exception:
            # ZIP write failures are fatal. Retrieval errors from the storage SDK
            # are caught before the archive entry is opened in _pack_document.
            raise
    report['documents'].append(entry)
    return entry


def _sheet(workbook, title, columns, rows):
    sheet = workbook.create_sheet(title[:31])
    sheet.append(columns)
    for row in rows:
        cells = []
        for name in columns:
            cell = WriteOnlyCell(sheet, value=row.get(name))
            if isinstance(cell.value, str):
                cell.data_type = 's'
            cells.append(cell)
        sheet.append(cells)


def _workbook(payload, report, table_columns):
    workbook = Workbook(write_only=True)
    for key, rows in payload.items():
        if key == 'metadata':
            continue
        columns = list(table_columns[key])
        if key == 'consignments':
            columns = ['backup_order'] + columns + ['pod_backup_path', 'pod_backup_status', 'invoice_backup_path', 'invoice_backup_status']
        if key == 'mis_report':
            columns += ['file_backup_path', 'file_backup_status']
        _sheet(workbook, key, columns, rows)
    columns = ['backup_order', 'consignment_id', 'consignment_number', 'mis_report_id', 'kind', 'source', 'original_name', 'status', 'archive_path', 'bytes', 'sha256', 'message']
    _sheet(workbook, 'document_report', columns, report['documents'])
    _sheet(workbook, 'inventory_errors', ['source', 'bucket', 'namespace', 'message'], report['inventory_errors'])
    return workbook


def build_complete_backup(payload):
    buffer = SpooledTemporaryFile(max_size=8*1024*1024, mode='w+b')
    report = {'format_version': 2, 'generated_at': payload['metadata']['generated_at'], 'ordering': 'Consignments ordered by database ID ascending; numeric folder prefixes match backup_order in data and Excel.',
              'documents': [], 'inventory_errors': [], 'storage_scope': ['instance/uploads/', 'configured/referenced Supabase buckets: consignments/ namespace and all directly referenced files']}
    table_columns = {TABLE_KEYS.get(table.name, table.name): [column.name for column in table.columns] for table in db.metadata.sorted_tables}
    referenced = set()
    try:
        with ZipFile(buffer, 'w', ZIP_DEFLATED, allowZip64=True) as archive:
            for order, row in enumerate(payload['consignments'], start=1):
                row['backup_order'] = order
                folder = f"documents/{order:06d}_{_safe_name(row['consignment_number'])}_id-{row['id']}"
                for kind, (field, name_field) in DOCUMENTS.items():
                    value = row.get(field)
                    if value:
                        referenced.add(_reference_key(value))
                    name = row.get(name_field) or (Path(value).name if value else kind)
                    path = folder + '/' + kind + '_' + _safe_name(name)
                    entry = _add_document(archive, report, value, path, backup_order=order, consignment_id=row['id'],
                                          consignment_number=row['consignment_number'], kind=kind, source='shipment', original_name=name if value else None)
                    row[kind + '_backup_path'] = entry['archive_path']
                    row[kind + '_backup_status'] = entry['status']
            for row in payload.get('mis_report', []):
                value = row.get('file_ref')
                if value:
                    referenced.add(_reference_key(value))
                name = row.get('file_name') or Path(value or 'report').name
                path = f"mis_reports/{row['id']:06d}_" + _safe_name(name)
                entry = _add_document(archive, report, value, path, mis_report_id=row['id'], kind='mis_report', source='report', original_name=name)
                row['file_backup_path'], row['file_backup_status'] = entry['archive_path'], entry['status']
            for index, (value, source) in enumerate(_inventory(referenced, report), start=1):
                path = f'other_uploads/{index:06d}_' + _safe_name(Path(value).name)
                _add_document(archive, report, value, path, source=source, kind='unlinked_upload', original_name=Path(value).name)
            counts = dict(Counter(row['status'] for row in report['documents']))
            incomplete = any(counts.get(status, 0) for status in ('missing', 'unavailable', 'invalid_reference')) or bool(report['inventory_errors'])
            report['summary'] = {'status': 'incomplete' if incomplete else 'complete', 'document_counts': counts, 'inventory_errors': len(report['inventory_errors'])}
            payload['metadata']['backup_status'] = report['summary']['status']
            payload['metadata']['document_counts'] = counts
            archive.writestr('data.json', json.dumps(payload, ensure_ascii=False, indent=2))
            archive.writestr('document_report.json', json.dumps(report, ensure_ascii=False, indent=2))
            with SpooledTemporaryFile(max_size=1024*1024, mode='w+b') as spreadsheet:
                _workbook(payload, report, table_columns).save(spreadsheet)
                spreadsheet.seek(0)
                with archive.open('admin-data.xlsx', 'w', force_zip64=True) as target:
                    while chunk := spreadsheet.read(1024*1024):
                        target.write(chunk)
            text = f"""Gram SCS complete admin backup

Backup status: {report['summary']['status'].upper()}
Files included: {counts.get('included', 0)}
File retrieval issues: {sum(counts.get(key, 0) for key in ('missing', 'unavailable', 'invalid_reference'))}
Upload listing issues: {len(report['inventory_errors'])}

All admin database tables and columns are in data.json and admin-data.xlsx,
including company masters, saved locations, shipments, MIS views/report history
and retained legacy records. Saved MIS files are in mis_reports/.
JSON is the authoritative full-data copy; Excel is for browsing the records.

Consignment folders start with the same backup_order number as the consignment
data/Excel sheet (database ID ascending). Each contains POD and invoice files
when attached. Unlinked uploaded files are in other_uploads/. Files are copied
exactly. SHA-256 checksums and missing/unavailable file details are in
document_report.json and the Excel report sheets.

Supabase inventory covers the admin app's consignments/ namespace in
configured/referenced buckets, plus all files directly referenced by records.
Unrelated website storage and environment credentials are not part of admin data.

This is a read-only export. No automatic restore interface is included.
Concurrent changes to uploads during a backup can produce retrieval issues;
inspect the report.
"""
            archive.writestr('README.txt', text)
        buffer.seek(0)
        return buffer, report['summary']
    except Exception:
        buffer.close()
        raise
