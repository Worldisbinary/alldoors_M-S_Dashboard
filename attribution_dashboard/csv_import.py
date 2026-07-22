"""CSV parsing/import logic for leads, ad spend, and bookings.

Kept separate from views.py so the request handlers stay thin. Each
import_* function reads an uploaded file object, validates row by row
(one bad row is skipped, not fatal to the rest of the file), and
returns an ImportResult.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, date

from .models import Project, Lead, LeadStageHistory, AdCampaign, Booking, CampaignSummary

MAX_ROWS = 20000

# Fallback tag when a row supplies no lead_status/tag — derived from stage,
# matching seed_dummy_data.py's convention so imported leads feed the funnel
# and quality metrics consistently with seeded ones.
STAGE_FALLBACK_TAG = {
    'Not Yet Connected': 'No Tag', 'Lead Registered': 'Potential',
    'Initial Contacted': 'Potential', 'Site Visited': 'Hot',
    'EOI Collected': 'Super Hot', 'Booking Confirmed': 'Super Hot',
}

DATE_FORMATS = [
    '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y',
    '%b %d, %Y', '%B %d, %Y', '%d %b %Y', '%d %B %Y',
]

# Canonical field -> accepted header spellings (lowercased on match). Covers
# common Google Ads / Meta Ads Manager export headers plus generic CRM exports.
HEADER_ALIASES = {
    'leads': {
        'name':          ['name', 'lead name', 'customer name'],
        'source':        ['source', 'platform', 'channel'],
        'campaign_name': ['campaign_name', 'campaign', 'campaign name'],
        'created_date':  ['created_date', 'date', 'created date', 'lead date'],
        'current_stage': ['current_stage', 'stage', 'crm stage'],
        'call_status':   ['call_status', 'call status'],
        'lead_status':   ['lead_status', 'status', 'lead status'],
        'tag':           ['tag'],
        'project':       ['project', 'property'],
    },
    'ad_spend': {
        'campaign_name': ['campaign_name', 'campaign', 'campaign name'],
        'date':          ['date', 'day', 'reporting starts'],
        'impressions':   ['impressions', 'impr.', 'impr'],
        'clicks':        ['clicks', 'link clicks'],
        'spend':         ['spend', 'cost', 'amount spent (inr)', 'amount spent'],
    },
    'bookings': {
        'lead_name':      ['lead_name', 'lead name', 'name'],
        'created_date':   ['created_date', 'lead created date', 'lead date'],
        'revenue_amount': ['revenue_amount', 'revenue', 'commission'],
        'gtv_amount':     ['gtv_amount', 'gtv', 'transaction value'],
        'booking_date':   ['booking_date', 'date', 'booking date'],
    },
    # Matches the "AllDoors | Combined Campaign Data" sheet format: one row
    # per campaign summarizing a date-range snapshot. The rate/CPQL columns
    # (Quality Rate %, Site Visit Rate %, High-Intent Rate %, Cost per
    # Quality Lead, CPL) are all derived from the raw counts below, so they're
    # intentionally not read here — recomputed instead, the same way the rest
    # of the dashboard is a single source of truth.
    'campaign_summary': {
        'platform':      ['platform'],
        'campaign_name': ['campaign', 'campaign name'],
        'status':        ['status'],
        'spend':         ['spend (₹)', 'spend(₹)', 'spend (rs)', 'spend'],
        'leads':         ['leads'],
        'potential':     ['potential'],
        'hot_super_hot': ['hot/super hot', 'hot super hot', 'hot/superhot', 'hot / super hot'],
        'site_visits':   ['site visits', 'site visit'],
    },
}

# Real ad-platform exports often label the platform column "Google Ads" /
# "Meta Ads" rather than the bare choice value — strip a trailing " ads".
PLATFORM_VALUE_ALIASES = {'google ads': 'Google', 'meta ads': 'Meta', 'linkedin ads': 'LinkedIn'}

# The one field each row must have to count as a real data row (used both to
# pick the right header row/sheet in a multi-tab workbook, and to recognize
# footer rows like "Total" that aren't data).
REQUIRED_FIELD = {
    'leads': 'name', 'ad_spend': 'campaign_name',
    'bookings': 'lead_name', 'campaign_summary': 'campaign_name',
}
FOOTER_VALUES = {'total', 'totals', 'grand total'}


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)  # list[str], "row N: reason"

    @property
    def failed(self):
        return len(self.errors)


def normalize_headers(fieldnames, alias_map):
    """Map raw CSV headers to our canonical field names. Unrecognized
    headers are left out of the mapping (and therefore ignored)."""
    lookup = {}
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            lookup[alias] = canonical
    mapping = {}
    for raw in fieldnames or []:
        key = raw.strip().lower()
        if key in lookup:
            mapping[raw] = lookup[key]
    return mapping


def parse_date(raw):
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    raw = str(raw).strip()
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def parse_number(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = str(raw).strip().replace('₹', '').replace(',', '').replace('%', '')
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_csv_rows(fileobj):
    wrapper = io.TextIOWrapper(fileobj.file, encoding='utf-8-sig')
    reader = csv.DictReader(wrapper)
    return reader.fieldnames, reader


MAX_HEADER_SCAN_ROWS = 20  # how deep to look for the header row in each sheet


def _read_xlsx_rows(fileobj, import_type):
    """Real workbooks are rarely 'header in row 1' — this file has a title
    row, a note row, a blank row, *then* the header, and may have several
    tabs (Combined Data / Meta Ads Data / Google Ads Data / ...). Scan every
    sheet and every row in the first MAX_HEADER_SCAN_ROWS for the row that
    best matches this import type's expected columns, and use whichever
    sheet/row scores highest — rather than assuming the active sheet's first
    row is the header."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(fileobj, data_only=True)
    except Exception as exc:
        raise ValueError(f"Could not read this as an Excel file: {exc}")

    alias_map = HEADER_ALIASES[import_type]
    required = REQUIRED_FIELD[import_type]
    best = None  # (score, sheet_rows, header_row_idx, mapping)

    for ws in wb.worksheets:
        sheet_rows = list(ws.iter_rows(values_only=True))
        for idx, row in enumerate(sheet_rows[:MAX_HEADER_SCAN_ROWS]):
            headers = [str(c).strip() if c is not None else '' for c in row]
            mapping = normalize_headers(headers, alias_map)
            if required not in mapping.values():
                continue
            score = len(mapping)
            if best is None or score > best[0]:
                best = (score, sheet_rows, idx, mapping)

    if best is None:
        raise ValueError(
            "Couldn't find a header row matching this import type in any sheet of "
            "this workbook (looked for a column like "
            f"'{alias_map[required][0]}' in the first {MAX_HEADER_SCAN_ROWS} rows of each tab)."
        )

    _, sheet_rows, header_idx, mapping = best
    header = [str(c).strip() if c is not None else '' for c in sheet_rows[header_idx]]

    def gen():
        for row in sheet_rows[header_idx + 1:]:
            if all(c is None or str(c).strip() == '' for c in row):
                continue
            yield {header[i]: row[i] for i in range(len(header)) if i < len(row)}
    return gen(), mapping


def _open_reader(fileobj, import_type):
    name = (getattr(fileobj, 'name', '') or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        return _read_xlsx_rows(fileobj, import_type)
    fieldnames, rows = _read_csv_rows(fileobj)
    mapping = normalize_headers(fieldnames, HEADER_ALIASES[import_type])
    return rows, mapping


def _canon_row(raw_row, mapping):
    out = {}
    for raw_key, canonical in mapping.items():
        val = raw_row.get(raw_key)
        out[canonical] = val if isinstance(val, (date, datetime)) else str(val).strip() if val is not None else ''
    return out


def _is_footer_row(identifier):
    """True for summary/footer rows like 'Total' that aren't real data —
    common as the last row of a spreadsheet table."""
    return bool(identifier) and identifier.strip().lower() in FOOTER_VALUES


def _match_choice(value, choices):
    """Case-insensitive match of `value` against a Django choices list of
    (db_value, label) tuples. Returns the canonical db_value or None."""
    if not value:
        return None
    value_lc = value.strip().lower()
    for db_value, _label in choices:
        if db_value.lower() == value_lc:
            return db_value
    return None


def import_leads(fileobj, default_project):
    result = ImportResult()
    reader, mapping = _open_reader(fileobj, 'leads')

    project_cache = {p.name.lower(): p for p in Project.objects.all()}

    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        if i - 1 > MAX_ROWS:
            result.errors.append(f"row {i}: file exceeds {MAX_ROWS} row limit — stopped early")
            break

        row = _canon_row(raw_row, mapping)
        if _is_footer_row(row.get('name')):
            continue
        try:
            name = row.get('name')
            if not name:
                result.skipped += 1
                result.errors.append(f"row {i}: missing name — skipped")
                continue

            project = default_project
            if row.get('project'):
                project = project_cache.get(row['project'].lower())
                if project is None:
                    result.skipped += 1
                    result.errors.append(f"row {i}: unknown project '{row['project']}' — skipped")
                    continue
            if project is None:
                result.skipped += 1
                result.errors.append(f"row {i}: no project specified — skipped")
                continue

            source = _match_choice(row.get('source'), Lead.PLATFORM_CHOICES)
            if source is None:
                result.skipped += 1
                result.errors.append(f"row {i}: unrecognized source '{row.get('source')}' — skipped")
                continue

            created_date = parse_date(row.get('created_date'))
            if created_date is None:
                result.skipped += 1
                result.errors.append(f"row {i}: unparseable created_date '{row.get('created_date')}' — skipped")
                continue

            current_stage = _match_choice(row.get('current_stage'), Lead.STAGE_CHOICES) or 'Not Yet Connected'
            call_status = _match_choice(row.get('call_status'), Lead.CALL_STATUS_CHOICES) or ''

            lead_status_raw = row.get('lead_status')
            lead_status = _match_choice(lead_status_raw, Lead.STATUS_CHOICES) or ''
            if lead_status:
                tag = Lead.STATUS_TAG_MAP[lead_status]
            else:
                tag = (_match_choice(row.get('tag'), Lead.TAG_CHOICES)
                       or STAGE_FALLBACK_TAG.get(current_stage, 'No Tag'))

            lead = Lead.objects.create(
                name=name,
                source=source,
                campaign_name=row.get('campaign_name') or 'Imported',
                created_date=created_date,
                current_stage=current_stage,
                call_status=call_status,
                tag=tag,
                lead_status=lead_status,
                project=project,
            )
            LeadStageHistory.objects.create(
                lead=lead, stage=current_stage, date_entered=created_date,
            )
            result.created += 1
        except Exception as exc:
            result.errors.append(f"row {i}: {exc} — skipped")
            result.skipped += 1

    return result


def import_ad_spend(fileobj, platform, project):
    result = ImportResult()
    reader, mapping = _open_reader(fileobj, 'ad_spend')

    for i, raw_row in enumerate(reader, start=2):
        if i - 1 > MAX_ROWS:
            result.errors.append(f"row {i}: file exceeds {MAX_ROWS} row limit — stopped early")
            break

        row = _canon_row(raw_row, mapping)
        if _is_footer_row(row.get('campaign_name')):
            continue
        try:
            campaign_name = row.get('campaign_name')
            if not campaign_name:
                result.skipped += 1
                result.errors.append(f"row {i}: missing campaign name — skipped")
                continue

            date = parse_date(row.get('date'))
            if date is None:
                result.skipped += 1
                result.errors.append(f"row {i}: unparseable date '{row.get('date')}' — skipped")
                continue

            impressions = parse_number(row.get('impressions')) or 0
            clicks = parse_number(row.get('clicks')) or 0
            spend = parse_number(row.get('spend'))
            if spend is None:
                result.skipped += 1
                result.errors.append(f"row {i}: unparseable spend '{row.get('spend')}' — skipped")
                continue

            AdCampaign.objects.create(
                platform=platform,
                campaign_name=campaign_name,
                date=date,
                impressions=int(impressions),
                clicks=int(clicks),
                spend=spend,
                project=project,
            )
            result.created += 1
        except Exception as exc:
            result.errors.append(f"row {i}: {exc} — skipped")
            result.skipped += 1

    return result


def import_bookings(fileobj, default_project):
    result = ImportResult()
    reader, mapping = _open_reader(fileobj, 'bookings')

    for i, raw_row in enumerate(reader, start=2):
        if i - 1 > MAX_ROWS:
            result.errors.append(f"row {i}: file exceeds {MAX_ROWS} row limit — stopped early")
            break

        row = _canon_row(raw_row, mapping)
        if _is_footer_row(row.get('lead_name')):
            continue
        try:
            lead_name = row.get('lead_name')
            created_date = parse_date(row.get('created_date'))
            booking_date = parse_date(row.get('booking_date'))
            revenue_amount = parse_number(row.get('revenue_amount'))
            gtv_amount = parse_number(row.get('gtv_amount'))

            if not lead_name or created_date is None:
                result.skipped += 1
                result.errors.append(f"row {i}: missing lead_name/created_date — skipped")
                continue
            if booking_date is None or revenue_amount is None or gtv_amount is None:
                result.skipped += 1
                result.errors.append(f"row {i}: missing/unparseable booking_date, revenue_amount or gtv_amount — skipped")
                continue

            lead_qs = Lead.objects.filter(name=lead_name, created_date=created_date)
            if default_project:
                lead_qs = lead_qs.filter(project=default_project)
            lead = lead_qs.first()
            if lead is None:
                result.skipped += 1
                result.errors.append(f"row {i}: no matching lead found for '{lead_name}' ({created_date}) — skipped")
                continue
            if Booking.objects.filter(lead=lead).exists():
                result.skipped += 1
                result.errors.append(f"row {i}: booking already exists for '{lead_name}' — skipped")
                continue

            Booking.objects.create(
                lead=lead, revenue_amount=revenue_amount,
                gtv_amount=gtv_amount, booking_date=booking_date,
            )
            if lead.current_stage != 'Booking Confirmed':
                lead.current_stage = 'Booking Confirmed'
                lead.tag = 'Super Hot'
                lead.lead_status = 'Closed'
                lead.save()
                LeadStageHistory.objects.create(
                    lead=lead, stage='Booking Confirmed', date_entered=booking_date,
                )
            result.created += 1
        except Exception as exc:
            result.errors.append(f"row {i}: {exc} — skipped")
            result.skipped += 1

    return result


def import_campaign_summary(fileobj, default_project, period_start, period_end, default_platform=''):
    result = ImportResult()
    reader, mapping = _open_reader(fileobj, 'campaign_summary')
    project_cache = {p.name.lower(): p for p in Project.objects.all()}

    for i, raw_row in enumerate(reader, start=2):
        if i - 1 > MAX_ROWS:
            result.errors.append(f"row {i}: file exceeds {MAX_ROWS} row limit — stopped early")
            break

        row = _canon_row(raw_row, mapping)
        if _is_footer_row(row.get('campaign_name')):
            continue
        try:
            campaign_name = row.get('campaign_name')
            if not campaign_name:
                result.skipped += 1
                result.errors.append(f"row {i}: missing campaign name — skipped")
                continue

            # No Platform column? Falls back to the platform picked on the
            # form — real sheets are often split one-tab-per-platform, with
            # the platform implied by the tab rather than a per-row column.
            platform_raw = (row.get('platform') or '').strip()
            platform = (PLATFORM_VALUE_ALIASES.get(platform_raw.lower())
                        or _match_choice(platform_raw, CampaignSummary.PLATFORM_CHOICES)
                        or (default_platform or None))
            if platform is None:
                result.skipped += 1
                result.errors.append(f"row {i}: unrecognized platform '{platform_raw}' — skipped")
                continue

            spend = parse_number(row.get('spend'))
            if spend is None:
                result.skipped += 1
                result.errors.append(f"row {i}: unparseable spend '{row.get('spend')}' — skipped")
                continue

            # Campaign names in this format are often the project name itself
            # (e.g. "Embassy Green Knowledge Park") — auto-attribute when it
            # matches, else fall back to the project picked in the form.
            project = project_cache.get(campaign_name.strip().lower()) or default_project

            CampaignSummary.objects.create(
                platform=platform,
                campaign_name=campaign_name,
                project=project,
                status=row.get('status') or '',
                period_start=period_start,
                period_end=period_end,
                spend=spend,
                leads=int(parse_number(row.get('leads')) or 0),
                potential=int(parse_number(row.get('potential')) or 0),
                hot_super_hot=int(parse_number(row.get('hot_super_hot')) or 0),
                site_visits=int(parse_number(row.get('site_visits')) or 0),
            )
            result.created += 1
        except Exception as exc:
            result.errors.append(f"row {i}: {exc} — skipped")
            result.skipped += 1

    return result


TEMPLATE_ROWS = {
    'leads': (
        ['name', 'source', 'campaign_name', 'created_date', 'current_stage', 'lead_status', 'project'],
        ['Rahul Sharma', 'Google', 'Google_Search_Brand', '2026-07-01', 'Initial Contacted', 'Interested', 'Gopalan Urban Woods'],
    ),
    'ad_spend': (
        ['campaign_name', 'date', 'impressions', 'clicks', 'spend'],
        ['Meta_Prospecting_Lookalike', '2026-07-01', '4200', '210', '12500'],
    ),
    'bookings': (
        ['lead_name', 'created_date', 'booking_date', 'revenue_amount', 'gtv_amount'],
        ['Rahul Sharma', '2026-07-01', '2026-07-15', '316400', '14000000'],
    ),
    'campaign_summary': (
        ['Platform', 'Campaign', 'Status', 'Spend (₹)', 'Leads', 'CPL (₹)', 'Potential',
         'Hot/Super Hot', 'Site Visits', 'Quality Rate (%)', 'Site Visit Rate (%)',
         'High-Intent Rate (%)', 'Cost per Quality Lead (₹)'],
        ['Meta Ads', 'Embassy Green Knowledge Park', 'Active', '34991', '73', '479', '24',
         '0', '1', '32.9%', '1.4%', '32.9%', '1458'],
    ),
}
