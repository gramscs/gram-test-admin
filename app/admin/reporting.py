"""One definition of shipment reporting shared by dashboard and MIS exports."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from app.models import Company, Consignment

IST = ZoneInfo('Asia/Kolkata')
STATUSES = ('Pickup Scheduled', 'In Transit', 'Out for Delivery', 'Delivered')
PERIODS = {'all': 'All time', '7d': 'Last 7 days', '30d': 'Last 30 days', 'month': 'This month', 'quarter': 'This quarter', 'custom': 'Custom dates'}
COLUMNS = {
    'consignment_number': 'Shipment identifier', 'identifier_type': 'Identifier type', 'client': 'Client',
    'status': 'Current status', 'pickup_date': 'Pickup date', 'drop_date': 'Expected drop date',
    'pickup_tag': 'Pickup location', 'pickup_pincode': 'Pickup pincode', 'pickup_address': 'Pickup address',
    'drop_tag': 'Drop location', 'drop_pincode': 'Drop pincode', 'drop_address': 'Drop address',
    'pieces': 'Pieces', 'chargeable_weight': 'Chargeable weight (kg)', 'chargeable_volume': 'Chargeable volume (m³)',
    'pod': 'POD uploaded', 'invoice': 'Invoice uploaded', 'issues': 'Attention flags',
}
DEFAULT_COLUMNS = ['consignment_number', 'identifier_type', 'client', 'status', 'pickup_date', 'drop_date', 'pickup_tag', 'drop_tag', 'pieces', 'chargeable_weight', 'chargeable_volume', 'pod', 'invoice', 'issues']
DEFINITION = 'Current status of shipments grouped by pickup date. Expected drop dates are estimates; no actual delivery time or on-time delivery metric is recorded. Document indicators show attached references, not file availability.'


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    if 'T' in value or ' ' in value:
        try:
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return result.astimezone(IST).date() if result.tzinfo else result.date()
        except ValueError:
            pass
    for pattern in ('%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    return None


def normalize_filters(values, today=None):
    if not isinstance(values, dict):
        raise ValueError('Report filters must be an object.')
    today = today or datetime.now(IST).date()
    period = values.get('period', 'all')
    if not isinstance(period, str) or period not in PERIODS:
        raise ValueError('Choose a valid reporting period.')
    start = end = None
    if period in ('7d', '30d'):
        end, start = today, today - timedelta(days=6 if period == '7d' else 29)
    elif period in ('month', 'quarter'):
        end = today
        start = today.replace(day=1, month=today.month if period == 'month' else ((today.month-1)//3)*3+1)
    elif period == 'custom':
        try:
            start, end = date.fromisoformat(values.get('start', '')), date.fromisoformat(values.get('end', ''))
        except (ValueError, TypeError):
            raise ValueError('Choose a start and end date in YYYY-MM-DD format.')
        if start > end:
            raise ValueError('Start date must be before or equal to end date.')
    company = values.get('company', '')
    if not isinstance(company, (str, int)) or isinstance(company, bool):
        raise ValueError('Choose a valid client.')
    company = str(company)
    if company not in ('', 'unassigned') and (not company.isdigit() or not Company.query.filter_by(id=int(company)).first()):
        raise ValueError('This client no longer exists. Choose another client.')
    status = values.get('status', '')
    if not isinstance(status, str) or status not in ('', *STATUSES, 'No status', 'Other'):
        raise ValueError('Choose a valid shipment status.')
    search = values.get('search', '')
    if not isinstance(search, str) or len(search.strip()) > 200:
        raise ValueError('Search must contain at most 200 characters.')
    return {'search': search.strip(), 'period': period, 'start': start.isoformat() if start else '', 'end': end.isoformat() if end else '', 'company': company, 'status': status}


def validate_columns(value):
    if not isinstance(value, list) or not value or len(value) > len(COLUMNS) or any(not isinstance(key, str) or key not in COLUMNS for key in value):
        raise ValueError('Select one or more valid shipment columns.')
    return list(dict.fromkeys(value))


def status_group(value):
    value = (value or '').strip()
    return value if value in STATUSES else ('No status' if not value else 'Other')


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def search_shipments(query, search):
    if not search:
        return query
    pattern = f'%{search}%'
    return query.filter(or_(
        *(getattr(Consignment, key).ilike(pattern) for key in (
            'consignment_number', 'status', 'identifier_type', 'pickup_tag', 'drop_tag',
            'pickup_pincode', 'drop_pincode', 'pickup_address', 'drop_address')),
        Consignment.company.has(Company.name.ilike(pattern)),
    ))


def analyze(filters, today=None):
    today = today or datetime.now(IST).date()
    query = search_shipments(Consignment.query, filters.get('search', ''))
    if filters['company'] == 'unassigned':
        query = query.filter(Consignment.company_id.is_(None))
    elif filters['company']:
        query = query.filter_by(company_id=int(filters['company']))
    shipments, excluded = [], 0
    for row in query.order_by(Consignment.id).all():
        if filters['status'] and status_group(row.status) != filters['status']:
            continue
        pickup = parse_date(row.pickup_date)
        if filters['start']:
            if not pickup:
                excluded += 1
                continue
            if not date.fromisoformat(filters['start']) <= pickup <= date.fromisoformat(filters['end']):
                continue
        shipments.append(row)
    counts = {key: 0 for key in (*STATUSES, 'No status', 'Other')}
    totals = {'total': len(shipments), 'pieces': 0, 'weight': Decimal(0), 'volume': Decimal(0), 'weight_recorded': 0, 'volume_recorded': 0, 'past_expected': 0, 'missing_pod': 0, 'missing_invoice': 0, 'undated': 0, 'unassigned': 0, 'invalid_measurements': 0}
    clients, routes, dates, details, exceptions = {}, {}, {}, [], []
    for row in shipments:
        group = status_group(row.status)
        counts[group] += 1
        pickup, expected = parse_date(row.pickup_date), parse_date(row.drop_date) or parse_date(row.eta)
        client = row.company.name if row.company else 'Unassigned client'
        client_key = str(row.company_id) if row.company else 'unassigned'
        pieces = row.pieces if isinstance(row.pieces, int) and row.pieces > 0 else 0
        weight, volume = number(row.chargeable_weight), number(row.chargeable_volume)
        flags = []
        if group != 'Delivered' and expected and expected < today:
            totals['past_expected'] += 1
            flags.append('Open past expected date')
        if group == 'Delivered' and not row.pod_image:
            totals['missing_pod'] += 1
            flags.append('Delivered without POD')
        if not row.invoice_file:
            totals['missing_invoice'] += 1
            flags.append('Invoice missing')
        if not pickup:
            totals['undated'] += 1
            flags.append('Pickup date missing or invalid')
        if not row.company_id:
            totals['unassigned'] += 1
        if any(value is not None and number(value) is None for value in (row.chargeable_weight, row.chargeable_volume)) or not pieces:
            totals['invalid_measurements'] += 1
            flags.append('Invalid measurement')
        totals['pieces'] += pieces
        for key, value in (('weight', weight), ('volume', volume)):
            if value is not None:
                totals[key] += value
                totals[key+'_recorded'] += 1
        breakdown = clients.setdefault(client_key, {'key': client_key, 'label': client, 'count': 0, 'delivered': 0, 'pieces': 0, 'weight': Decimal(0)})
        breakdown['count'] += 1
        breakdown['delivered'] += group == 'Delivered'
        breakdown['pieces'] += pieces
        breakdown['weight'] += weight or Decimal(0)
        route = f'{row.pickup_tag or row.pickup_pincode or "Unspecified pickup"} → {row.drop_tag or row.drop_pincode or "Unspecified drop"}'
        routes[route] = routes.get(route, 0) + 1
        if pickup:
            dates[pickup] = dates.get(pickup, 0) + 1
        detail = {key: getattr(row, key, '') or '' for key in COLUMNS if hasattr(row, key)}
        detail.update(client=client, status=row.status or 'No status', pieces=pieces, chargeable_weight=float(weight) if weight is not None else '', chargeable_volume=float(volume) if volume is not None else '', pod='Yes' if row.pod_image else 'No', invoice='Yes' if row.invoice_file else 'No', issues='; '.join(flags))
        details.append(detail)
        if flags:
            exceptions.append({'identifier': row.consignment_number, 'client': client, 'issues': flags})
    for key in ('weight', 'volume'):
        totals[key] = float(totals[key])
    totals.update(delivered=counts['Delivered'], in_transit=counts['In Transit'], out_for_delivery=counts['Out for Delivery'], open=len(shipments)-counts['Delivered'], delivered_share=round(counts['Delivered']/len(shipments)*100, 1) if shipments else 0, excluded_undated=excluded)
    trend, grain = [], 'daily'
    start = date.fromisoformat(filters['start']) if filters['start'] else min(dates, default=today)
    end = date.fromisoformat(filters['end']) if filters['end'] else max(dates, default=today)
    span = (end-start).days
    if span > 1095:
        grain = 'yearly'
    elif span > 90:
        grain = 'monthly'
    def bucket(value):
        return value.isoformat() if grain == 'daily' else value.strftime('%Y-%m') if grain == 'monthly' else str(value.year)
    grouped = {}
    for value, count in dates.items():
        key = bucket(value)
        grouped[key] = grouped.get(key, 0) + count
    cursor = start
    while cursor <= end:
        key = bucket(cursor)
        trend.append({'label': key, 'count': grouped.get(key, 0)})
        if grain == 'daily':
            if cursor == date.max:
                break
            cursor += timedelta(days=1)
        elif grain == 'monthly':
            if cursor.year == 9999 and cursor.month == 12:
                break
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        else:
            if cursor.year == 9999:
                break
            cursor = date(cursor.year+1, 1, 1)
    # With no dated data there is nothing to plot, rather than a fabricated zero point.
    if not dates:
        trend = []
    clients = sorted(clients.values(), key=lambda item: (-item['count'], item['label'].lower()))
    for item in clients:
        item['weight'] = float(item['weight'])
    company_label = next((item.name for item in Company.query.all() if str(item.id) == filters['company']), 'Unassigned client' if filters['company'] else 'All clients')
    period_label = f'{filters["start"]} to {filters["end"]}' if filters['start'] else 'All time'
    return {'metrics': totals, 'saved_total': Consignment.query.count(), 'statuses': [{'label': key, 'count': value} for key, value in counts.items() if value], 'trend': trend, 'grain': grain, 'clients': clients, 'routes': [{'label': key, 'count': value} for key, value in sorted(routes.items(), key=lambda item: (-item[1], item[0]))], 'exceptions': exceptions, 'details': details, 'recent': list(reversed(shipments[-5:])), 'filters': filters, 'period_label': period_label, 'client_label': company_label, 'status_label': filters['status'] or 'All statuses', 'generated_at': datetime.now(IST).strftime('%d %b %Y, %I:%M %p IST'), 'definition': DEFINITION}


def filter_options():
    return {'periods': PERIODS, 'companies': Company.query.order_by(Company.name).all(), 'statuses': (*STATUSES, 'No status', 'Other')}


def chart_data(report):
    return {key: report[key] for key in ('statuses', 'trend', 'clients', 'routes', 'metrics', 'grain')}
