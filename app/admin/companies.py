"""Client company masters and reusable pickup/drop locations."""

from flask import jsonify, render_template, request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.input_validation import clean_text, email_address, payload, pincode, positive_id
from app.models import db
from app import orm as models


def serialize_company(row):
    return {
        'id': row.id, 'name': row.name, 'address': row.address or '',
        'email': row.email or '', 'phone': row.phone or '', 'active': row.active,
        'locations': [{'id': location.id, 'kind': location.kind, 'label': location.label,
                       'address': location.address, 'pincode': location.pincode or '',
                       'is_default': location.is_default} for location in row.locations],
    }


@admin_bp.get('/admin/companies')
@require_admin
def companies_panel():
    search = request.args.get('search', '').strip()
    archived = request.args.get('archived') == '1'
    page = max(1, request.args.get('page', 1, type=int))
    query = models.Company.query
    if not archived:
        query = query.filter_by(active=True)
    if search:
        query = query.filter(or_(models.Company.name.ilike(f'%{search}%'), models.Company.address.ilike(f'%{search}%')))
    total = query.count()
    rows = query.order_by(models.Company.active.desc(), models.Company.name.asc()).offset((page-1)*25).limit(25).all()
    return render_template('admin/companies.html', companies=[serialize_company(row) for row in rows], search=search, archived=archived, page=page, total=total)


@admin_bp.get('/admin/companies/options')
@require_admin
def companies_options():
    # Archived companies remain available for displaying an existing shipment's client.
    return jsonify(success=True, companies=[serialize_company(row) for row in models.Company.query.order_by(models.Company.name).all()])


def _save_company(company=None):
    data = payload()
    name = clean_text(data, 'name', 200, required=True)
    duplicate = models.Company.query.filter(func.lower(models.Company.name) == name.lower()).first()
    if duplicate and (company is None or duplicate.id != company.id):
        raise ValueError('A company with this name already exists. Edit or restore its master instead.')
    fields = {'name': name, 'address': clean_text(data, 'address', 5000),
              'email': email_address(clean_text(data, 'email', 120)),
              'phone': clean_text(data, 'phone', 30)}
    active = data.get('active', True)
    if not isinstance(active, bool):
        raise ValueError('Active must be true or false.')
    locations = data.get('locations', [])
    if not isinstance(locations, list) or len(locations) > 50:
        raise ValueError('Add at most 50 pickup/drop locations.')
    existing = {row.id: row for row in company.locations} if company else {}
    normalized, used_ids, defaults = [], set(), set()
    for item in locations:
        if not isinstance(item, dict) or item.get('kind') not in ('pickup', 'drop'):
            raise ValueError('Choose Pickup or Drop for each location.')
        location_id = positive_id(item['id']) if item.get('id') else None
        if location_id and (location_id not in existing or location_id in used_ids):
            raise ValueError('A location does not belong to this company or was listed twice.')
        if location_id:
            used_ids.add(location_id)
        default = item.get('is_default', False)
        if not isinstance(default, bool):
            raise ValueError('Location default must be true or false.')
        if default and item['kind'] in defaults:
            raise ValueError('Choose only one default location for each Pickup/Drop group.')
        if default:
            defaults.add(item['kind'])
        normalized.append((location_id, {'kind': item['kind'], 'label': clean_text(item, 'label', 100, True),
                                        'address': clean_text(item, 'address', 5000, True),
                                        'pincode': pincode(clean_text(item, 'pincode', 6)), 'is_default': default}))
    company = company or models.Company()
    for key, value in fields.items():
        setattr(company, key, value)
    company.active = active
    if models.using_er() and existing:
        # Release old defaults before replacing them, respecting the ER index.
        for location in existing.values():
            location.is_default = False
        db.session.flush()
        for id, location in existing.items():
            if id not in used_ids:
                from database.models import Consignment as Shipment
                if db.session.query(Shipment).filter((Shipment.pickup_location_id == id) | (Shipment.drop_location_id == id)).first():
                    raise ValueError('A location used by a shipment cannot be removed. Keep it or edit its address.')
                db.session.delete(location)
    new_locations = []
    for location_id, fields in normalized:
        if fields['kind'] not in defaults:
            fields['is_default'] = True
            defaults.add(fields['kind'])
        location = existing.get(location_id) or models.CompanyLocation()
        for key, value in fields.items():
            setattr(location, key, value)
        new_locations.append(location)
    company.locations = new_locations
    db.session.add(company)
    db.session.commit()
    return jsonify(success=True, company=serialize_company(company))


@admin_bp.post('/admin/companies')
@require_admin
def company_create():
    try:
        return _save_company()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        return jsonify(success=False, message=str(error) if isinstance(error, ValueError) else 'A company with this name already exists.'), 400


@admin_bp.put('/admin/companies/<company_id>')
@require_admin
def company_update(company_id):
    try:
        company_id = models.record_id(company_id)
    except ValueError:
        return jsonify(success=False, message='Company not found.'), 404
    company = db.session.get(models.Company, company_id)
    if not company:
        return jsonify(success=False, message='Company not found.'), 404
    try:
        return _save_company(company)
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        return jsonify(success=False, message=str(error) if isinstance(error, ValueError) else 'A company with this name already exists.'), 400


@admin_bp.post('/admin/companies/<company_id>/archive')
@require_admin
def company_archive(company_id):
    try:
        payload()
    except ValueError as error:
        return jsonify(success=False, message=str(error)), 400
    try:
        company_id = models.record_id(company_id)
    except ValueError:
        return jsonify(success=False, message='Company not found.'), 404
    company = db.session.get(models.Company, company_id)
    if not company:
        return jsonify(success=False, message='Company not found.'), 404
    company.active = False
    db.session.commit()
    return jsonify(success=True)
