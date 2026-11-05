"""Bulk builds on the partner API: ``POST /api/v1/profiles/bulk``
(Jenna 2026-11-05: "update the API to talk about bulk uploads").

The same door the chat opens for an uploaded list (prometheus.bulk_pull),
reached with a JSON body instead of an attachment:

    {"subjects": ["Taylor Swift", {"subject": "Dolly Parton", "category": "MUSICIAN/BAND", "notes": "country icon"}],
     "run_avid": true,
     "date_range": {"start": "2025-11-05", "end": "2026-11-05"}}

Plain names, 1 to 1000 of them. Duplicates collapse (case and
punctuation insensitive); blank rows, links and numbers are dropped and
counted. Every name is checked against the shared library first: a
Total Universe of exactly that name is reused on the spot (its key on
the row, ``reused: true``, no charge). The rest are new builds, charged
once at the partner's rate for a Profile IQ build, queued on one ledger
and fed to the profile engine as capacity allows (bulk_feeder, the same
throttle the chat's lists share). One digest email per list goes to the
key owner as builds finish, plus a final one; a build that cannot be
completed carries the partner-safe line and its charge comes back on
its own. ``GET /api/v1/profiles/<bulk_id>`` returns the ledger.

No override flags: the body carries intent only (the names, the avid
choice, the window). Idempotency rides the same ``Idempotency-Key``
header the build endpoint honors. One list in progress per key: a
second submission while one is open is a 409 naming the open bulk id.

Pure module: no Flask, no S3 client of its own. The route in app.py
hands in the key's user, the catalog and the S3 client; the tests hand
in fakes. Every string a partner reads passes the partner belt
(prometheus.guards) on the way out.
"""
from __future__ import annotations

import re
import time

from . import bulk_pull as BP

__all__ = ['MAX_SUBJECTS', 'KNOWN_FIELDS', 'ITEM_FIELDS', 'SOURCE', 'FILE_NAME', 'RATE_LIMIT', 'parse_body', 'read_from_items',
           'open_bulk_for', 'open_bulk_error', 'response_doc', 'status_doc', 'funds_error', 'scrub', 'violations', 'eta_words']

MAX_SUBJECTS = 1000
SUBJECT_MAX_LEN = 120
CATEGORY_MAX_LEN = 80
NOTES_MAX_LEN = 300
KNOWN_FIELDS = ('subjects', 'run_avid', 'date_range', 'window')
ITEM_FIELDS = ('subject', 'name', 'category', 'notes')
SOURCE = 'partner_api_v1'
FILE_NAME = 'the API'                 # "Your list from the API: 3 of 10 builds done"
RATE_LIMIT = (10, 100)                # per minute, per day, one key
STATUS_PATH = '/api/v1/profiles/'
_DATE_RX = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DIGEST_LINE = ('One summary email per list goes to the key owner as builds finish, plus a final one when every name is handled; '
               'there is no email per profile.')


class BodyError(ValueError):
    """A 400: the message is the partner-facing text."""


def _err(msg):
    return None, msg


def _clean_window(raw):
    """{'start','end','label'} from the body's date_range / window object,
    None when absent, or raises BodyError."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BodyError('"date_range" must be an object with "start" and "end" as YYYY-MM-DD.')
    extra = [k for k in raw.keys() if k not in ('start', 'end', 'label')]
    if extra:
        raise BodyError(f'unknown field in "date_range": "{extra[0]}". Accepted fields: start, end.')
    s, e = str(raw.get('start') or '').strip(), str(raw.get('end') or '').strip()
    if not s and not e:
        return None
    if not (s and e):
        raise BodyError('"date_range" needs both "start" and "end" as YYYY-MM-DD.')
    if not (_DATE_RX.match(s) and _DATE_RX.match(e)):
        raise BodyError('"date_range" start and end are YYYY-MM-DD.')
    from datetime import date as _date
    try:
        ds, de = _date.fromisoformat(s), _date.fromisoformat(e)
    except ValueError:
        raise BodyError('"date_range" start and end are real dates, YYYY-MM-DD.')
    if ds > de:
        raise BodyError('"date_range" start must be on or before end.')
    w = BP._normalize_window({'start': s, 'end': e, 'label': f'{s} to {e}'})
    if (w['start'], w['end']) != (s, e):
        raise BodyError('"date_range" start and end are YYYY-MM-DD.')
    return w


def parse_body(body):
    """(parsed, error_text). ``parsed`` = {'rows': [[subject, category, notes], ...], 'run_avid': bool,
    'window': {...} or None}. The rows keep the partner's order; the
    library read (``read_from_items``) collapses duplicates and drops the
    unusable ones the same way the chat door reads an uploaded sheet."""
    if not isinstance(body, dict):
        return _err('request body must be a JSON object')
    for k in body.keys():
        if k not in KNOWN_FIELDS:
            return _err(f'unknown field: "{k}". Accepted fields: subjects (required), run_avid, date_range. See GET /api/v1/schema.')
    subjects = body.get('subjects')
    if subjects is None:
        return _err('required field: "subjects" (a list of 1 to 1000 plain names)')
    if not isinstance(subjects, list):
        return _err('"subjects" must be a list of plain names')
    if not subjects:
        return _err('"subjects" is empty; send 1 to 1000 plain names')
    if len(subjects) > MAX_SUBJECTS:
        return _err(f'"subjects" accepts at most {MAX_SUBJECTS:,} names per list; send the rest as the next list once this one is complete')
    run_avid = body.get('run_avid', True)
    if not isinstance(run_avid, bool):
        return _err('"run_avid" must be a boolean')
    if body.get('date_range') is not None and body.get('window') is not None:
        return _err('send "date_range" or "window", not both')
    try:
        window = _clean_window(body.get('date_range') if body.get('date_range') is not None else body.get('window'))
    except BodyError as e:
        return _err(str(e))
    rows = []
    for i, it in enumerate(subjects):
        if isinstance(it, str):
            subj, cat, notes = it, '', ''
        elif isinstance(it, dict):
            for k in it.keys():
                if k not in ITEM_FIELDS:
                    return _err(f'unknown field in "subjects[{i}]": "{k}". Accepted fields: subject, category, notes.')
            subj = it.get('subject', it.get('name'))
            if not isinstance(subj, str):
                return _err(f'"subjects[{i}].subject" must be a string')
            cat = it.get('category') or ''
            notes = it.get('notes') or ''
            if not isinstance(cat, str) or not isinstance(notes, str):
                return _err(f'"subjects[{i}]" category and notes must be strings')
            if len(cat) > CATEGORY_MAX_LEN:
                return _err(f'"subjects[{i}].category" exceeds {CATEGORY_MAX_LEN} characters')
            if len(notes) > NOTES_MAX_LEN:
                return _err(f'"subjects[{i}].notes" exceeds {NOTES_MAX_LEN} characters')
        else:
            return _err(f'"subjects[{i}]" must be a plain name or an object with "subject"')
        subj = ' '.join(str(subj or '').split())
        if len(subj) > SUBJECT_MAX_LEN:
            return _err(f'"subjects[{i}]" exceeds {SUBJECT_MAX_LEN} characters; send a plain name')
        rows.append([subj, ' '.join(str(cat).split()), ' '.join(str(notes).split())])
    return {'rows': rows, 'run_avid': run_avid, 'window': window}, None


def read_from_items(parsed):
    """The chat door's own read of a list ({'items','dropped','duplicates',
    'columns'}): duplicates collapsed, blanks / links / numbers dropped,
    the optional category and notes kept per name."""
    rows = [['subject', 'category', 'notes']] + [list(r) for r in (parsed or {}).get('rows') or []]
    read = BP.subjects_from_rows(rows)
    read['columns'] = {'subject': 'subject', 'category': 'category', 'notes': 'notes', 'run_avid': ''}
    return read


# ---------------------------------------------------------------- one list at a time
def open_bulk_for(username, s3=None, bucket=None):
    """The API list still in progress for this key owner, or None."""
    try:
        for led in BP.list_ledgers(s3=s3, bucket=bucket, active_only=True, username=username, limit=50):
            if str(led.get('source') or '') != SOURCE:
                continue
            if BP.counts(led)['open'] > 0:
                return led
    except Exception:
        return None
    return None


def open_bulk_error(ledger, host_url=''):
    """The 409 body: plain words naming the open list."""
    bid = str((ledger or {}).get('bulk_id') or '')
    c = BP.counts(ledger or {})
    return {'success': False,
            'error': (f'This key already has a list in progress ({bid}: {c["open"]:,} of {c["total"]:,} names still to go). '
                      f'One list runs at a time. Poll GET {STATUS_PATH}{bid} and send the next list when it is complete.'),
            'open_bulk_id': bid,
            'status_url': f"{str(host_url or '').rstrip('/')}{STATUS_PATH}{bid}"}


def funds_error(text, *, price_usd, balance_usd, each_usd, n_new):
    return {'success': False, 'error': str(text or 'insufficient balance for this list'), 'price_usd': round(float(price_usd), 2),
            'price_each_usd': round(float(each_usd), 2), 'new_builds': int(n_new), 'balance_usd': balance_usd}


# ---------------------------------------------------------------- the documents
def eta_words(n_new, cap=None):
    return BP.eta_words(n_new, cap)


def response_doc(ledger, *, read=None, host_url='', balance_usd=None, cap=None):
    """The body of POST /api/v1/profiles/bulk: the ledger's status
    document plus the price, the time and what was dropped at intake."""
    doc = status_doc(ledger, host_url=host_url)
    c = BP.counts(ledger)
    ch = ledger.get('charged') or {}
    n_new = int(ch.get('rows') or 0)
    each = float(ch.get('usd_each') or 0.0)
    charged = 0.0 if ch.get('free_seat') else round(each * n_new, 2)
    mins = BP.eta_minutes(n_new, cap)
    doc.update({
        'success': True,
        'duplicates': int((read or {}).get('duplicates') or 0),
        'dropped': int((read or {}).get('dropped') or 0),
        'price_usd': round(each * n_new, 2),
        'price_each_usd': round(each, 2),
        'charge_usd': charged,
        'balance_usd': balance_usd,
        'estimated_minutes': int(mins),
        'estimated_completion': eta_words(n_new, cap),
        'digest': DIGEST_LINE,
    })
    if c['open'] == 0:
        doc['note'] = 'Every name on the list is already in the library. Nothing was charged.'
    return doc


def status_doc(ledger, *, host_url=''):
    """The body of GET /api/v1/profiles/<bulk_id> (the route adds the
    download links for complete rows)."""
    doc = {'success': True, 'run_id': str((ledger or {}).get('bulk_id') or '')}
    doc.update(BP.partner_status(ledger or {}))
    doc['status_url'] = f"{str(host_url or '').rstrip('/')}{STATUS_PATH}{doc['bulk_id']}"
    return doc


# ---------------------------------------------------------------- the belt
_KEEP = frozenset(('subject', 'bulk_id', 'run_id', 'open_bulk_id', 's3_key', 'avid_s3_key', 'status_url', 'download_url',
                   'avid_download_url', 'date_window', 'created_at', 'completed_at'))


def _skip_keys():
    try:
        from . import guards as _g
        return _g._PARTNER_SKIP_KEYS | _KEEP
    except Exception:
        return _KEEP


def scrub(doc):
    """The partner belt over every string in a bulk response, in place.
    Identifiers, keys, links and the partner's own subject names are left
    as they are; every other string is checked and cleaned."""
    try:
        from . import guards as _g
        _g.scrub_partner_payload(doc, skip_keys=_skip_keys())
    except Exception:
        pass
    return doc


def violations(doc):
    """[(path, words)] for any string in a bulk response that still carries
    a banned word (the tests grep every fixture with this)."""
    try:
        from . import guards as _g
        return _g.partner_payload_violations(doc, skip_keys=_skip_keys())
    except Exception:
        return []


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
