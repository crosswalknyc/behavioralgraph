"""Digital Twins IQ, the read side (2026-10-26).

The index and the gz payloads under ``digital-twins/``, who may open a
twin, the CSV rows, the view summary Prometheus reads off the page, the
plain lines a deck restates, the pricing defaults, and the How to text
that is the in-product directions (the How to tab, the empty states and
the Prometheus answer to "how do I use Digital Twins IQ").

Every twin joins the shared library (``partner-api-shared-catalog``):
anyone with ``has_digital_twins_iq_access`` sees the list, gated by
``allowed_digital_twin_runs`` (default ``['*']``); the creator always
sees their own; admins see everything.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import time
from typing import Optional

from migration.digital_twin_engine import (  # noqa: F401
    GREENLIGHT_RETIRED_LINE, RETIRED_RUN_KINDS, is_retired_run, product_runs,
)
from migration.digital_twin_synthesis import (  # noqa: F401
    S3_BUCKET, S3_INDEX_KEY, S3_PREFIX, load_twin, remove_from_index, save_twin,
)

JOBS_PREFIX = 'system/digital_twins/jobs/'
REQUESTS_PREFIX = 'system/digital_twins/requests/'
ACCESS_FLAG = 'has_digital_twins_iq_access'
RUNS_FIELD = 'allowed_digital_twin_runs'
PRODUCT_KEY = 'digitalTwinsIQ'
PRODUCT_LABEL = 'Digital Twins IQ'

# Pricing defaults (Jenna 2026-10-26). One line each; the live price an
# admin sets in the billing panel wins over these (wallet.tool_price_usd).
# Prometheus usage on a build and in a room is metered ON TOP of these
# (Jenna 2026-10-27: 'aside from these fees there needs to be billed
# prometheus metered pricing'): the model calls carry the seat's
# pay-per-use attribution, so the session sweep bills them at the
# published meter like any other Prometheus work.
PRICING = {
    'digital_twins_iq': 750.0,          # a twin build: the twin and its first two runs (the old Greenlight run left the seed 2026-10-28)
    'digital_twin_run': 100.0,          # each further Launch, content, pricing, platform or compare run
    'digital_twin_spend': 250.0,        # Spend Engine plan activation for the plan window, daily runs included
    'digital_twin_panel': 5500.0,       # a Focus Group IQ session, 12 questions included (Jenna 2026-10-26: 'A session is $5,500 (twelve questions, any room)')
    'digital_twin_panel_more': 5500.0,  # each further 12 questions is another session at the session price
    'digital_twin_panelist': 550.0,     # each panelist beyond the 12 a room seats, billed with the session (Jenna 2026-10-27: 'extra panelists for an extra $550 x panelist')
    'digital_twin_pilot': 5500.0,       # a Pilot Testing session, same as a Focus Group IQ session (Jenna 2026-10-28)
    'digital_twin_pilot_more': 5500.0,  # each further 12 questions in a Pilot Testing session
    # The screening modes (Jenna 2026-10-28, 'price where you think it makes sense'): each one runs on the
    # Pilot Testing room and is priced for what it tests. A trailer or TV spot is priced per asset; titles and
    # key art carry up to six options at one price; a recut re-test keeps the same room at half the session.
    'digital_twin_trailer': 2500.0,     # a trailer or TV spot test, per asset (up to three side by side)
    'digital_twin_title': 1500.0,       # a title / logline test, up to six options
    'digital_twin_key_art': 1500.0,     # a key art test, up to six options
    'digital_twin_concept': 2500.0,     # Greenlight, Concept: a concept test (logline, synopsis, one-sheet or deck text)
    'digital_twin_script': 3500.0,      # Greenlight, Script read: the script text or a recorded table read
    'digital_twin_recut': 2750.0,       # a recut re-test on the same room (cut B after cut A)
}
PANEL_QUESTIONS_INCLUDED = 12
PANEL_SEATS_INCLUDED = 12               # panelists a session covers; every seat past this is a priced panelist
PANEL_SEATS_MAX = 40
PULL_TYPES = {
    'digital_twins_iq': 'Digital Twins IQ', 'digital_twin_run': 'Digital Twins IQ Run', 'digital_twin_spend': 'Digital Twins IQ Spend',
    'digital_twin_panel': 'Digital Twins IQ Focus Group', 'digital_twin_panel_more': 'Digital Twins IQ Focus Group More',
    'digital_twin_panelist': 'Digital Twins IQ Focus Group Panelist',
    'digital_twin_pilot': 'Digital Twins IQ Pilot Testing', 'digital_twin_pilot_more': 'Digital Twins IQ Pilot Testing More',
    'digital_twin_trailer': 'Digital Twins IQ Trailer Test', 'digital_twin_title': 'Digital Twins IQ Title Test',
    'digital_twin_key_art': 'Digital Twins IQ Key Art Test', 'digital_twin_concept': 'Digital Twins IQ Concept Test',
    'digital_twin_script': 'Digital Twins IQ Script Read', 'digital_twin_recut': 'Digital Twins IQ Recut Re-test',
}
# the price each screening mode carries on first Play (the session price for the watch modes)
MODE_PRICE_KEY = {
    'pilot': 'digital_twin_pilot', 'feature': 'digital_twin_pilot', 'episode': 'digital_twin_pilot', 'binge': 'digital_twin_pilot',
    'trailer': 'digital_twin_trailer', 'spot': 'digital_twin_trailer', 'title': 'digital_twin_title', 'key_art': 'digital_twin_key_art',
    'concept': 'digital_twin_concept', 'script': 'digital_twin_script', 'critics': 'digital_twin_pilot', 'recut': 'digital_twin_recut',
}


def extra_panelists(in_room) -> int:
    """How many seats in a room are priced panelists: the seats past the
    included twelve, never negative."""
    try:
        n = int(in_room or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, min(n, PANEL_SEATS_MAX) - PANEL_SEATS_INCLUDED)


def panelists_for(payload: dict) -> int:
    """The seats a room on this twin opens with: the number chosen when
    the twin was built, else twelve."""
    try:
        n = int(((payload or {}).get('meta') or {}).get('panelists') or 0)
    except (TypeError, ValueError):
        n = 0
    return max(2, min(n, PANEL_SEATS_MAX)) if n else PANEL_SEATS_INCLUDED


# ------------------------------------------------------------- the index
def list_runs(s3, *, username: Optional[str] = None, limit: int = 500) -> list:
    if s3 is None:
        return []
    try:
        idx = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)['Body'].read().decode('utf-8')) or {}
        runs = [r for r in (idx.get('runs') or []) if isinstance(r, dict) and r.get('key')]
    except Exception:
        runs = []
    for r in runs:
        r.pop('ltv_5y', None)
        r.pop('ltv_10y', None)
        r.pop('has_ltv', None)
    runs.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
    if username:
        runs = [r for r in runs if r.get('created_by') == username]
    return runs[:limit]


def _role(user) -> str:
    return str((user or {}).get('role') or 'user').lower().replace(' ', '_')


def is_admin(user) -> bool:
    return _role(user) in ('admin', 'super_admin', 'superadmin')


def has_access(user) -> bool:
    """The product flag, or admin."""
    if not isinstance(user, dict):
        return False
    if is_admin(user):
        return True
    return bool(user.get(ACCESS_FLAG))


def allowed_keys(user):
    """['*'] (everything), or the explicit list."""
    raw = (user or {}).get(RUNS_FIELD)
    if isinstance(raw, list):
        return raw
    return ['*']


def can_open(user, key: str, run: Optional[dict] = None) -> bool:
    if not isinstance(user, dict):
        return False
    uname = str(user.get('username') or '')
    if is_admin(user):
        return True
    if run and uname and str(run.get('created_by') or '') == uname:
        return True
    if not has_access(user):
        return False
    allowed = allowed_keys(user)
    if '*' in allowed:
        return True
    return key in allowed or (run and str(run.get('name') or '') in allowed)


def visible_runs(runs: list, user) -> list:
    return [r for r in runs if can_open(user, str(r.get('key') or ''), r)]


DELETED_TWINS_PREFIX = 'system/digital_twins/deleted_twins/'
DELETED_TWIN_KEEP_DAYS = 30
BACKUPS_PREFIX = '_backups/'


def _twin_created_by(payload: Optional[dict] = None, run: Optional[dict] = None) -> str:
    if isinstance(payload, dict):
        who = str(((payload.get('meta') or {}).get('created_by') or '')).strip()
        if who:
            return who
    if isinstance(run, dict):
        return str(run.get('created_by') or '').strip()
    return ''


def can_delete_twin(payload: Optional[dict], username: str, is_admin: bool = False, run: Optional[dict] = None) -> bool:
    """The twin's creator, or an admin / super_admin."""
    if is_admin:
        return True
    who = _twin_created_by(payload, run)
    return bool(username) and who == str(username)


def deleted_twin_key(twin_key: str) -> str:
    """system/digital_twins/deleted_twins/<safe_user>/<slug>_<id>.json.gz"""
    stem = str(twin_key or '')
    if stem.startswith(S3_PREFIX):
        stem = stem[len(S3_PREFIX):]
    stem = re.sub(r"\.json(\.gz)?$", '', stem)
    parts = stem.split('/', 1)
    if len(parts) == 2:
        user, slug = parts
    else:
        user, slug = 'anon', parts[0]
    safe_user = re.sub(r'[^A-Za-z0-9._-]+', '_', user).strip('_') or 'anon'
    slug = re.sub(r'[^A-Za-z0-9._-]+', '_', slug).strip('_') or 'twin'
    return f"{DELETED_TWINS_PREFIX}{safe_user}/{slug}.json.gz"


def deleted_twin_sidecar_key(twin_key: str) -> str:
    k = deleted_twin_key(twin_key)
    return (k[:-8] if k.endswith('.json.gz') else k) + '.meta.json'


def drop_twin_from_allowed_runs(users_doc: dict, key: str) -> int:
    """Drop the key from every explicit-list seat. Leave ``['*']``
    untouched. Returns how many seats changed."""
    n = 0
    if not key or not isinstance(users_doc, dict):
        return 0
    users = users_doc.get('users')
    if not isinstance(users, dict):
        return 0
    for u in users.values():
        if not isinstance(u, dict):
            continue
        cur = u.get(RUNS_FIELD)
        if not isinstance(cur, list) or '*' in cur:
            continue
        nxt = [x for x in cur if str(x) != str(key)]
        if nxt != list(cur):
            u[RUNS_FIELD] = nxt
            n += 1
    return n


def _revoke_twin_access(key: str, users_doc: Optional[dict] = None) -> int:
    """Drop the key from explicit lists. ``users_doc`` is for tests;
    live seats go through the app compare-and-swap. Never raises."""
    if users_doc is not None:
        try:
            return drop_twin_from_allowed_runs(users_doc, key)
        except Exception as e:
            print(f"[twin] users list drop skipped: {e}")
            return 0
    try:
        import sys as _sys
        app = _sys.modules.get('app')
        if app is None or not hasattr(app, '_users_cas_mutate'):
            return 0
        n = [0]

        def mut(data):
            n[0] = drop_twin_from_allowed_runs(data, key)
            return data if n[0] else None
        app._users_cas_mutate(mut)
        return n[0]
    except Exception as e:
        print(f"[twin] users list drop skipped: {e}")
        return 0


def _gz_bytes(payload: dict) -> bytes:
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8'))
    return body.getvalue()


def _object_bytes(s3, key: str, payload: Optional[dict] = None) -> Optional[bytes]:
    if s3 is not None and key:
        try:
            raw = s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read()
            if raw:
                return raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode('utf-8')
        except Exception:
            pass
    if isinstance(payload, dict):
        try:
            return _gz_bytes(payload)
        except Exception:
            return None
    return None


def delete_twin(s3, key: str, payload: Optional[dict] = None, deleted_by: str = '',
                users_doc: Optional[dict] = None, run: Optional[dict] = None) -> Optional[dict]:
    """Take the twin out of the library. A copy of the payload (and a
    small sidecar with enough index metadata to restore) sits under
    ``deleted_twins`` for 30 days. The index entry and any explicit
    ``allowed_digital_twin_runs`` listing drop. ``['*']`` is left
    alone. Never raises; None when it could not."""
    key = str(key or '')
    if not key:
        return None
    try:
        return _delete_twin(s3, key, payload, deleted_by, users_doc, run)
    except Exception as e:
        print(f"[twin] delete failed: {e}")
        return None


def _delete_twin(s3, key: str, payload: Optional[dict], deleted_by: str,
                 users_doc: Optional[dict], run: Optional[dict]) -> Optional[dict]:
    if payload is None:
        try:
            payload = load_twin(s3, key)
        except Exception:
            payload = None
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    undo_until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + DELETED_TWIN_KEEP_DAYS * 86400))
    name = ''
    if isinstance(payload, dict):
        name = str(((payload.get('meta') or {}).get('name') or (payload.get('meta') or {}).get('subject') or ''))
    if not name and isinstance(run, dict):
        name = str(run.get('name') or run.get('subject') or '')
    raw = _object_bytes(s3, key, payload if isinstance(payload, dict) else None)
    if raw is None:
        return None
    undo_key = deleted_twin_key(key)
    side_key = deleted_twin_sidecar_key(key)
    entry = dict(run) if isinstance(run, dict) else {}
    if isinstance(payload, dict) and not entry:
        try:
            from migration.digital_twin_synthesis import index_entry
            entry = index_entry(key, payload)
        except Exception:
            entry = {'key': key, 'name': name, 'created_by': _twin_created_by(payload, run)}
    sidecar = {
        'original_key': key, 'deleted_by': deleted_by or '', 'deleted_at': now,
        'undo_until': undo_until, 'index': entry, 'name': name,
        'created_by': _twin_created_by(payload, run or entry),
        'rooms': len((payload or {}).get('panel_sessions') or []) if isinstance(payload, dict) else 0,
    }
    if s3 is not None:
        if raw:
            try:
                s3.put_object(Bucket=S3_BUCKET, Key=undo_key, Body=raw,
                              ContentType='application/json', ContentEncoding='gzip',
                              Metadata={'undo-until': undo_until})
            except Exception as e:
                print(f"[twin] undo copy failed (twin still deleted): {e}")
            try:
                stamp = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
                bak = BACKUPS_PREFIX + re.sub(r'[/]+', '_', key) + f'.pre_twin_delete_{stamp}.json.gz'
                s3.put_object(Bucket=S3_BUCKET, Key=bak, Body=raw,
                              ContentType='application/json', ContentEncoding='gzip')
            except Exception as e:
                print(f"[twin] backup copy skipped: {e}")
        try:
            s3.put_object(Bucket=S3_BUCKET, Key=side_key,
                          Body=json.dumps(sidecar, ensure_ascii=False, default=str).encode('utf-8'),
                          ContentType='application/json', Metadata={'undo-until': undo_until})
        except Exception as e:
            print(f"[twin] undo sidecar failed (twin still deleted): {e}")
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=key)
        except Exception as e:
            print(f"[twin] object drop failed: {e}")
            return None
    try:
        dropped = remove_from_index(s3, key) if s3 is not None else entry or None
        if dropped:
            sidecar['index'] = dropped
    except Exception as e:
        print(f"[twin] index drop skipped: {e}")
    try:
        _revoke_twin_access(key, users_doc)
    except Exception as e:
        print(f"[twin] users list drop skipped: {e}")
    return {'deleted': key, 'undo_until': undo_until, 'undo_key': undo_key,
            'sidecar_key': side_key, 'name': name}


def find_twin(runs: list, name: str) -> Optional[dict]:
    """The index entry whose name or subject the words carry; the newest
    when several match."""
    low = ' ' + re.sub(r"[^a-z0-9]+", ' ', str(name or '').lower()) + ' '
    best, best_score = None, 0.0
    for r in runs:
        for field in ('name', 'subject'):
            toks = [t for t in re.sub(r"[^a-z0-9]+", ' ', str(r.get(field) or '').lower()).split() if len(t) > 1 and t not in ('the', 'viewers', 'fans', 'twin', 'audience', 'of')]
            if not toks:
                continue
            present = sum(1 for t in toks if f" {t} " in low)
            if present and present >= max(1, len(toks) - (1 if len(toks) > 2 else 0)):
                score = present / len(toks) + (0.1 if field == 'name' else 0.0)
                if score > best_score or (score == best_score and str(r.get('created_at') or '') > str((best or {}).get('created_at') or '')):
                    best, best_score = r, score
    return best


# -------------------------------------------------------------- the rows
def csv_rows(payload: dict) -> list:
    """Identity, traits, lifecycle, runs, the Spend Engine ledger
    and the Focus Group IQ sessions as flat rows."""
    m = payload.get('meta') or {}
    name = m.get('name') or m.get('subject')
    rows = [['Section', 'Item', 'Label', 'Value', 'Unit', 'Note']]
    rows.append(['Twin', 'name', name, m.get('size_us'), 'US people', f"{m.get('window_start')} to {m.get('window_end')}"])
    rows.append(['Twin', 'platform home', m.get('platform_home'), m.get('avid_share_pct'), 'avid share %', m.get('format')])
    ident = payload.get('identity') or {}
    for cat, items in (ident.get('demographics') or {}).items():
        for it in items:
            rows.append(['Identity', cat, it.get('label'), it.get('pct'), '%', ''])
    for it in ident.get('geography') or []:
        rows.append(['Identity', 'geography', it.get('label'), it.get('pct'), '%', ''])
    for it in ident.get('platforms') or []:
        rows.append(['Identity', 'platforms', it.get('label'), it.get('reach_pct'), '% reach', it.get('group') or ''])
    for it in ident.get('purchase') or []:
        rows.append(['Identity', 'purchase', it.get('label'), it.get('pct'), '%', it.get('group') or ''])
    for k, v in (payload.get('traits') or {}).items():
        rows.append(['Traits', k, v.get('label'), v.get('value'), '0-100', v.get('read')])
    for k, v in (payload.get('lifecycle') or {}).items():
        if k.endswith('_pct'):
            rows.append(['Lifecycle', k[:-4], k[:-4], v, '%', ''])
    for r in product_runs(payload):
        o = r.get('outputs') or {}
        unit = 'USD' if o.get('unit') == 'usd' else (o.get('noun') or 'people')
        rows.append(['Runs', r.get('id'), f"{r.get('label')}: {r.get('question')}", o.get('expected'), unit, f"range {o.get('low')} to {o.get('high')}" + (f"; odds {o.get('odds_pct')}%" if o.get('odds_pct') is not None else '')])
        for lv in (o.get('levers') or [])[:6]:
            rows.append(['Runs', r.get('id'), f"lever: {lv.get('lever')}", lv.get('delta_people'), unit, f"{lv.get('delta_pct')}%"])
    for sp in ((payload.get('labs') or {}).get('spend')) or []:
        led = sp.get('ledger') or {}
        rows.append(['Spend Engine', sp.get('plan_id'), sp.get('label'), led.get('saved_total'), 'USD saved', led.get('read')])
        for d in sp.get('days') or []:
            for b in d.get('by_channel') or []:
                rows.append(['Spend Engine', sp.get('plan_id'), f"day {d.get('day')} {b.get('channel')}", b.get('first_actions'), 'first actions', f"spend {b.get('spend')}, cpa {b.get('cpa')}"])
    for ps in payload.get('panel_sessions') or []:
        for q in ps.get('questions') or []:
            for r in q.get('distribution') or []:
                rows.append(['Focus Group IQ', ps.get('id'), f"{q.get('q')}: {r.get('label')}", r.get('pct'), '%', f"{r.get('people')} people"])
    for ps in payload.get('pilot_sessions') or []:
        sc = ps.get('scorecard') or {}
        head = ((sc.get('battery') or {}).get('headline')) or {}
        rows.append(['Pilot Testing', ps.get('id'), ps.get('title'), head.get('total_positive_pct') if head.get('total_positive_pct') is not None else sc.get('overall_like'),
                     'total positive %' if head.get('total_positive_pct') is not None else 'dial average',
                     f"excellent {head.get('excellent_pct')}, definitely recommend {head.get('definitely_recommend_pct')}, dial {sc.get('overall_like')}"])
        for e in (ps.get('events') or [])[:40]:
            rows.append(['Pilot Testing', ps.get('id'), f"{e.get('type')} at {e.get('t')}", e.get('intensity'), 'event', e.get('line')])
    return rows


def csv_text(payload: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in csv_rows(payload):
        w.writerow(['' if v is None else v for v in r])
    return buf.getvalue()


def csv_filename(payload: dict) -> str:
    m = payload.get('meta') or {}
    return re.sub(r"[^A-Za-z0-9]+", '_', f"{m.get('name') or m.get('subject')} Digital Twin").strip('_') + '.csv'


# ------------------------------------------------------------- the summary
def view_summary(payload: dict, run_id: str = '', session_id: str = '') -> dict:
    """What the page hands Prometheus as view_context.data: small,
    every headline figure, the open run or session when there is one."""
    m = payload.get('meta') or {}
    out = {'twin': m.get('name'), 'subject': m.get('subject'), 'key': m.get('key'), 'size_us': m.get('size_us'), 'window': f"{m.get('window_start')} to {m.get('window_end')}",
           'platform_home': m.get('platform_home'), 'format': m.get('format'), 'ip_related': m.get('ip_related'),
           'source': m.get('source'), 'panelists': panelists_for(payload),
           'identity_read': (payload.get('identity') or {}).get('read'),
           'traits': {k: {'value': v.get('value'), 'read': v.get('read')} for k, v in (payload.get('traits') or {}).items()},
           'lifecycle': {k: v for k, v in (payload.get('lifecycle') or {}).items() if k.endswith('_pct')},
           'tldr': (payload.get('the_read') or {}).get('tldr'),
           'runs': [{'id': r.get('id'), 'kind': r.get('kind'), 'question': r.get('question'), 'expected': (r.get('outputs') or {}).get('expected'),
                     'low': (r.get('outputs') or {}).get('low'), 'high': (r.get('outputs') or {}).get('high'), 'odds_pct': (r.get('outputs') or {}).get('odds_pct'),
                     'recommendation': r.get('recommendation')} for r in product_runs(payload)[:8]]}
    if payload.get('standing_questions'):
        out['standing_questions'] = payload['standing_questions']
    if run_id:
        r = next((x for x in product_runs(payload) if x.get('id') == run_id), None)
        if r:
            out['open_run'] = {k: r.get(k) for k in ('id', 'kind', 'question', 'outputs', 'drivers', 'recommendation', 'read')}
    if session_id:
        s = next((x for x in payload.get('panel_sessions') or [] if x.get('id') == session_id), None)
        if s:
            out['open_panel_session'] = {'id': s.get('id'), 'title': s.get('title'), 'in_room': (s.get('group') or {}).get('in_room'),
                                         'questions': [{'q': q.get('q'), 'kind': q.get('kind'), 'read': q.get('read'), 'distribution': q.get('distribution')} for q in (s.get('questions') or [])[-6:]]}
        p = next((x for x in payload.get('pilot_sessions') or [] if x.get('id') == session_id), None)
        if p:
            sc = p.get('scorecard') or {}
            head = ((sc.get('battery') or {}).get('headline')) or {}
            out['open_pilot_session'] = {'id': p.get('id'), 'title': p.get('title'), 'status': p.get('status'), 'format': sc.get('format'), 'genre': p.get('genre'),
                                         'dial_average': sc.get('overall_like'), 'excellent_pct': head.get('excellent_pct'), 'total_positive_pct': head.get('total_positive_pct'),
                                         'definitely_recommend_pct': head.get('definitely_recommend_pct'), 'finish_pct': head.get('finish_pct'), 'tune_in_pct': head.get('tune_in_pct'),
                                         'norm_lines': sc.get('norm_lines') or [], 'quadrants': ((sc.get('battery') or {}).get('quadrants')) or [],
                                         'favorite_moment': sc.get('favorite_moment'), 'dead_spot': sc.get('dead_spot'),
                                         'events_n': len(p.get('events') or []),
                                         'questions': [{'q': q.get('q'), 'playhead_clock': q.get('playhead_clock'), 'read': q.get('read')} for q in (p.get('questions') or [])[-6:]]}
    spends = ((payload.get('labs') or {}).get('spend')) or []
    if spends:
        sp = spends[0]
        out['spend_engine'] = {'plan_id': sp.get('plan_id'), 'label': sp.get('label'), 'ledger': sp.get('ledger'), 'mode': sp.get('mode')}
    return out


def twin_lines(payload: dict) -> list:
    """The twin as plain lines a deck restates slide for slide."""
    from migration.digital_twin_engine import fmt_money, human_people
    m = payload.get('meta') or {}
    out = []
    rd = payload.get('the_read') or {}
    if rd.get('tldr'):
        out.append('TLDR:\n' + '\n'.join(rd['tldr']))
    ident = payload.get('identity') or {}
    demo = ident.get('demographics') or {}
    lines = [f"{m.get('name')}: {int(m.get('size_us') or 0):,} people in the US, {m.get('window_start')} to {m.get('window_end')}; home: {m.get('platform_home')}."]
    for cat in ('gender', 'age', 'income'):
        rows = demo.get(cat) or []
        if rows:
            lines.append(f"{cat.title()}: " + '; '.join(f"{r['label']} {float(r['pct']):.1f}%" for r in rows[:8]))
    if ident.get('platforms'):
        lines.append('Platforms: ' + '; '.join(f"{p['label']} {float(p['reach_pct']):.1f}%" for p in ident['platforms'][:8]))
    if ident.get('purchase'):
        lines.append('They buy: ' + '; '.join(f"{p['label']} {float(p['pct']):.1f}%" for p in ident['purchase'][:8]))
    out.append('WHO THEY ARE:\n' + '\n'.join(lines))
    traits = payload.get('traits') or {}
    if traits:
        out.append('TRAITS (0 to 100):\n' + '\n'.join(f"{v.get('label')}: {v.get('value')}; they {v.get('read')}" for v in traits.values()))
    lc = payload.get('lifecycle') or {}
    if lc:
        out.append('LIFECYCLE:\n' + '; '.join(f"{k[:-4]} {v}%" for k, v in lc.items() if k.endswith('_pct')))
    for r in product_runs(payload)[:6]:
        o = r.get('outputs') or {}
        unit = 'usd' if o.get('unit') == 'usd' else (o.get('noun') or 'people')
        fmt = (lambda v: fmt_money(v)) if unit == 'usd' else (lambda v: f"{int(v or 0):,} {unit}")
        blk = [f"{r.get('label')}: {r.get('question')}", f"Expected {fmt(o.get('expected'))}, range {fmt(o.get('low'))} to {fmt(o.get('high'))}"
               + (f"; odds {o.get('odds_pct')}% ({o.get('odds_words')})" if o.get('odds_pct') is not None else '') + '.']
        blk += list(r.get('drivers') or [])[:3]
        if r.get('recommendation'):
            blk.append('What to do: ' + r['recommendation'])
        out.append('RUN:\n' + '\n'.join(blk))
    sq = payload.get('standing_questions') or {}
    if sq:
        out.append('THE STANDING QUESTIONS:\n' + '\n'.join([sq.get('relative_popularity') or '', sq.get('demographics_line') or '',
                   f"Prime Video in the last 30 days: {sq.get('prime_video_t30d_pct')}%. SVOD churn: {sq.get('svod_churn_pct')}%. On Prime Video: {sq.get('on_prime_pct')}%; the rest sit on " +
                   ', '.join(f"{p['label']} {p['pct']:.1f}%" for p in (sq.get('off_prime_platforms') or [])[:5]) + '.']))
    for sp in (((payload.get('labs') or {}).get('spend')) or [])[:2]:
        led = sp.get('ledger') or {}
        out.append('SPEND ENGINE:\n' + '\n'.join([sp.get('label') or '', led.get('read') or '', (sp.get('holdout') or {}).get('lift_read') or '']))
    for ps in (payload.get('panel_sessions') or [])[:2]:
        qs = ps.get('questions') or []
        if qs:
            out.append('FOCUS GROUP IQ, ' + str(ps.get('title')) + ':\n' + '\n'.join(f"{q.get('q')}: " + '; '.join(f"{r['label']} {r['pct']}%" for r in (q.get('distribution') or [])[:5]) for q in qs[-5:]))
    for ps in (payload.get('pilot_sessions') or [])[:2]:
        sc = ps.get('scorecard') or {}
        fav = sc.get('favorite_moment') or {}
        head = ((sc.get('battery') or {}).get('headline')) or {}
        out.append('PILOT TESTING, ' + str(ps.get('title')) + ':\n' +
                   (f"Excellent {head.get('excellent_pct')}%, total positive {head.get('total_positive_pct')}%, definitely recommend {head.get('definitely_recommend_pct')}%. "
                    if head.get('total_positive_pct') is not None else f"Dial average {sc.get('overall_like')}. ") +
                   ' '.join(sc.get('norm_lines') or []) + ' ' +
                   f"Favorite moment {fav.get('clock')}: {fav.get('line')}")
    if rd.get('what_to_do'):
        out.append('WHAT TO DO:\n' + '\n'.join(rd['what_to_do']))
    return [b for b in out if b.strip()]


def deck_context(run: dict, payload: dict) -> dict:
    """The deck core's ctx for a twin: view_context with material."""
    m = payload.get('meta') or {}
    name = str(m.get('name') or m.get('subject') or 'the twin')
    summ = view_summary(payload)
    data = {k: v for k, v in summ.items() if k in ('twin', 'subject', 'size_us', 'window', 'platform_home', 'format', 'ip_related')}
    material = [f"THE DIGITAL TWIN, {name}, part {i + 1}:\n{blk}" for i, blk in enumerate(twin_lines(payload))]
    return {'primary': None, 'cuts': [], 'extras': [], 'deck_source': 'twin',
            'view_context': {'view_id': PRODUCT_KEY, 'view_title': name[:80], 'data': data, 'material': material}}


# ---------------------------------------------------------------- how to
def pricing_lines(price_fn=None) -> list:
    """The twin pricing lines in the seat's currency; defaults without
    a price function."""
    def p(key):
        d = PRICING[key]
        if price_fn is None:
            return d
        try:
            v = float(price_fn(key, d) or 0)
            return v if v > 0 else d
        except Exception:
            return d

    def money(v):
        return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"
    return [f"Digital Twin - {money(p('digital_twins_iq'))} for the twin and its first two runs",
            f"Each further Launch, content, pricing, platform or compare run - {money(p('digital_twin_run'))}",
            f"Spend Engine - {money(p('digital_twin_spend'))} to activate a plan for its window, daily runs included",
            f"Focus Group IQ - {money(p('digital_twin_panel'))} a session with {PANEL_QUESTIONS_INCLUDED} questions, then {money(p('digital_twin_panel_more'))} for each further {PANEL_QUESTIONS_INCLUDED}",
            f"Pilot Testing - {money(p('digital_twin_pilot'))} a session with {PANEL_QUESTIONS_INCLUDED} questions on the clock, then {money(p('digital_twin_pilot_more'))} for each further {PANEL_QUESTIONS_INCLUDED} (same as Focus Group IQ); a feature, an episode, a binge test or a critics room is a session at this price",
            f"Trailers - {money(p('digital_twin_trailer'))} per trailer or TV spot, up to three side by side",
            f"Titles and Key Art - {money(p('digital_twin_title'))} for a title or logline test and {money(p('digital_twin_key_art'))} for a key art test, up to six options each",
            f"Greenlight Concept - {money(p('digital_twin_concept'))} a concept test",
            f"Greenlight Script read - {money(p('digital_twin_script'))} a script read",
            f"Recut re-test - {money(p('digital_twin_recut'))} for cut B on the same room",
            f"Extra panelist - {money(p('digital_twin_panelist'))} for each panelist past the {PANEL_SEATS_INCLUDED} a session seats, billed with the session",
            "Prometheus usage - metered at the published rate on top of these, for the build and for every question in a room"]


HOW_TO = [
    {
        'id': 'twin', 'title': 'Build a twin',
        'answers': 'Who an audience is and how it behaves: its people, platforms and buying, twelve dials for how it responds, and where it sits in its lifecycle.',
        'put_in': ('Build a twin on the Twins tab walks you through it. First, what the twin stands for: a defined group from a profile (the audience of a title, a talent or a brand). '
                   'A named subject sits on its Total Universe. If none is on file, one is pulled first and that file is what the twin and every room use. '
                   'The seats in a room match that file\'s mix (gender, orientation, education, occupation) and each persona is built from those attributes. '
                   'Or a group you shape by demographics (gender, age, income, ethnicity, markets). Then what you will use it for, '
                   f'and how many panelists sit in its focus group rooms ({PANEL_SEATS_INCLUDED} come with a session; each one past that is priced). Or type the ask to Prometheus with a window if it is not the last 12 months.'),
        'type_this': ['Build a twin of Gilmore Girls Hulu viewers', 'Build a twin of Mark Rober for a product launch', 'Build a twin of women 25 to 44 in Dallas who shop at Target'],
        'attach': 'Nothing is needed. If you have a brief or a deck on the subject, attach it with the paper clip and the twin reads it.',
        'comes_back': ('The twin lands on the Twins tab in a few minutes, with its first two runs. Prometheus confirms in the chat; there is no email. '
                       'Delete a twin from the Twins tab; it leaves the library and you can undo for 30 days.'),
        'cost_key': 'digital_twins_iq', 'export': 'Twins tab: CSV, Deck or PDF at the top of the twin page. Delete sits on the card and on the open twin.',
    },
    {
        # Jenna 2026-10-28: "remove what we currently have since I think it's nothing and replace with two options within it
        # script reads and concepts." Greenlight is the tab; Script read and Concept are the two options inside it.
        'id': 'greenlight', 'title': 'Greenlight',
        'answers': ('Two ways to put a project to the room before cameras roll or money is committed. Script read: paste or upload the script and the room reads it '
                    'scene by scene on a reading clock, with a dial per scene and story, structure and character notes (where it runs long, where a character '
                    'arrives without a want, where the turn lands); or upload a recorded table read and it runs like a pilot. Concept: a logline, a synopsis, a '
                    'one-sheet or deck text; interest and definite interest, how much it stands out, who it is for, would they watch, the strongest element and '
                    'the biggest doubt, each next to the concept norm and by quadrant. Seat two twins in the room to see two audiences on the same idea.'),
        'put_in': ('Pick Script read or Concept at the top of the Greenlight tab. Script read: a name for the test, the twin, and the script (paste the text, '
                   'upload a pdf or txt, or upload the recorded read as mp4, mov or webm); scenes are split on the sluglines (INT. and EXT.). Concept: a name '
                   'for the test, the twin, and the concept text (a logline is enough; a synopsis or one-sheet gives the room more to react to).'),
        'type_this': ['Test this concept in Greenlight on the Gilmore Girls twin: a chef returns to her hometown diner after a scandal',
                      'Read this script in Greenlight on the Mark Rober twin',
                      'Test this concept in Greenlight on the Gilmore Girls and Mark Rober twins: a chef returns to her hometown diner after a scandal'],
        'attach': 'For a script read, attach the script (pdf or txt) with the paper clip, or upload it on the tab. For a concept, paste the text, or attach the one-sheet or deck.',
        'comes_back': ('The test opens on the Greenlight tab under its option, Script read or Concept, and sits in that option\'s past tests; Prometheus confirms in the chat '
                       'with a chip to open it. Script read: the dial scene by scene, the notes per scene, the scorecard with its norm, the characters rated, pace '
                       'and confusion by act, and the scenes liked best and least. Concept: the concept read with the norm beside every number and the pick by quadrant.'),
        'options': [{'id': 'script', 'title': 'Script read', 'cost_key': 'digital_twin_script', 'line': 'Paste or upload a script; the room reads it scene by scene with story, structure and character notes.'},
                    {'id': 'concept', 'title': 'Concept', 'cost_key': 'digital_twin_concept', 'line': 'A logline, synopsis or one-sheet; interest, stands out, who it is for, would watch, strongest element, biggest doubt.'}],
        'cost_key': 'digital_twin_concept', 'export': 'Greenlight tab: CSV, PDF, Deck or Report on the test.',
    },
    {
        'id': 'launch', 'title': 'Launch Lab',
        'answers': 'What a plan and budget do before any money goes out: three plans side by side (as allocated, signal-led, and the minimum that still hits the goal), odds and ranges week by week, the levers ranked by what they earn, and a holdout sized to prove lift at the lowest spend.',
        'put_in': 'The channels with spend (Meta $120K, Google $80K, TikTok $40K), the assets, the window in weeks, the goal.',
        'type_this': ['Run Launch Lab on the Gilmore Girls twin: Meta $120K, Google $80K, TikTok $40K over 6 weeks, goal 3M viewers',
                      'Run Launch Lab on this plan'],
        'attach': 'Attach the budget sheet (xlsx or csv) or paste the channel list; the channels and spend are read from it.',
        'comes_back': 'The three plans land on the Launch Lab tab with the week-by-week band; Set it live sends the plan you choose to the Spend Engine.',
        'cost_key': 'digital_twin_run', 'export': 'The run exports with the twin; the chosen plan carries into the Spend Engine moves list.',
    },
    {
        'id': 'spend', 'title': 'Spend Engine',
        'answers': 'The plan run forward day by day on the twin: daily spend and the signal by channel, the moves that earn more each day (move money, raise or cut, swap the lead asset) with the delta in plain words, A/B tests to run inside the flight, a lift read against a matched holdout, and a ledger of what the moves saved against the plan as allocated.',
        'put_in': 'A plan (channels with spend, assets, window, goal) or the Launch Lab plan you approved. Pick a mode: approve each move by chip, or set a rule (max daily shift, a floor per channel) and the moves inside it approve on their own.',
        'type_this': ['Put the signal-led plan in the Spend Engine on the rule: max 15% a day', 'Activate this plan in the Spend Engine: Meta $60K, YouTube $40K over 14 days, approve by chip',
                      'Approve the day 3 move on the Gilmore Girls plan'],
        'attach': 'Attach the budget sheet (xlsx or csv) or paste the channels.',
        'comes_back': 'The day strip, the channel table, the moves feed and the ledger on the Spend Engine tab. Approving a move writes it to the ledger and the moves list for your buyers. Export moves gives the CSV; the Attribution IQ tracking intake measures the plan with the package prefilled. Connected-account execution is switched on per account by Crosswalk; the Connect accounts card files the request.',
        'cost_key': 'digital_twin_spend', 'export': 'Export moves (CSV) on the Spend Engine tab; the plan also exports with the twin.',
    },
    {
        'id': 'panel', 'title': 'Focus Group IQ',
        'answers': 'A focus group Prometheus moderates for you (Focus Group IQ). Pick the twins or the cohorts in the room, ask anything in plain words, and Prometheus runs the room: the %s and people counts, the split by segment, a one-line read and a few panelist voices, in a minute or two. Yes/no, multiple choice, rating 1 to 5, would you pay $X, A vs B, a show of hands, and probes (why? what would change your mind?) that follow the last question.',
        'put_in': (f'A name for the room (you can change it later on the room page), the twins or cohorts in the room (for example Gilmore Girls Hulu viewers, women 25 to 44; lapsed Netflix subscribers in Dallas), '
                   f'how many panelists sit in the room ({PANEL_SEATS_INCLUDED} come with a session; each one past that is priced and billed with the session), then your questions. '
                   'The seats match the twin\'s profile (the gender split, orientation, education and the rest); each persona is built from those attributes.'),
        'type_this': ['Open a focus group with the Mark Rober twin and ask whether they would pay $29 for the Build Box', 'Ask them why', 'Show of hands, who watched season 1?',
                      'Ask the room: trailer A vs trailer B', 'Re-ask at $19'],
        'attach': 'Nothing is needed. Attach a trailer link, two loglines or a price sheet if the question is about them.',
        'comes_back': 'The % table, the voices and the read in the chat and on the Focus Group IQ tab, where the room, the live bars and the transcript sit. The session is saved on the twin and resumes where you left it.',
        'cost_key': 'digital_twin_panel', 'export': 'Focus Group IQ tab: Export (CSV or PDF) on the session.',
    },
    {
        'id': 'pilot', 'title': 'Pilot Testing',
        'answers': ('A live watch of a pilot, movie, trailer or episode. Prometheus moderates. The twins in the room watch the cut itself, '
                    'scene by scene, from the moment you press Play: a dial of how the room feels second by second, laughs and loves marked '
                    'on the clock in each person\'s own words, and you can ask them a question at any timestamp; the question carries the '
                    'last minute of what played, so "would you keep watching?" at 12:40 is about minute 12. After the cut: the scorecard '
                    'studios use (excellent / very good and total positive, definitely recommend, would finish, would watch the next one, '
                    'how soon, against expectations, worth full price), each next to the norm and the genre norm, the four quadrants '
                    '(women and men, under and over 35), the dial split by any group in the room (age, ethnicity, orientation, education, '
                    'where they are with the title, avid or casual), the favorite moment, the dead spot, the transcript.'),
        'put_in': (f'A name for the test, the twin (or a second twin) in the room, how many panelists sit '
                   f'({PANEL_SEATS_INCLUDED} come with a session; each one past that is priced), and the cut: upload an mp4, mov or webm, '
                   'or paste a title and a runtime if the file is not in hand yet. The seats match the twin\'s profile '
                   '(gender, orientation, education, occupation); each persona is built from those attributes. '
                   'The genre is read from the cut; change it with the chip on the test if it reads wrong.'),
        'type_this': ['Open Pilot Testing on the Gilmore Girls twin and play the season 2 pilot',
                      'Ask the room at 12:40: would you keep watching?',
                      'Show of hands, who laughed at the diner scene?'],
        'attach': 'Upload the cut (mp4, mov or webm) on the Pilot Testing tab. A title and runtime still run the clock on a slate if the file is not ready.',
        'comes_back': ('Once the room has watched the cut (a few minutes for an episode) Play is ready. The dial draws as the cut plays, '
                       'events land on the scrubber, a suggested question appears at each dip and peak, and a moderator guide sits beside '
                       'the player with questions at the act turns and the ending. Answers to your questions carry the playhead timestamp and '
                       'name the scene. The scorecard and its norms land on Stop. The test is saved on the twin and resumes where you left it. '
                       'Delete a test from the Pilot Testing tab; you can undo for 30 days.'),
        'protect': ('How we protect your cut: the file plays through links that expire after ten minutes and only for the person who '
                    'uploaded it, your name sits on every frame while it plays, and a switch on the test removes the file the moment the '
                    'scorecard is final (the read, the dial and the transcript stay).'),
        'cost_key': 'digital_twin_pilot', 'export': 'Pilot Testing tab: Export CSV, PDF, Deck or the full Report on the test.',
    },
    {
        'id': 'trailer', 'title': 'Trailers',
        'answers': ('A trailer or TV spot test, 15 seconds to three minutes, up to three assets side by side in the same room. Interest in the title before the '
                    'trailer plays (from the title and logline alone) and after, the lift in definite interest, what they understood it is about, what they '
                    'expect, the moments that sold it on the clock, who it reads as being for (gender and age), would they watch or see it, would they '
                    'recommend it, each next to the trailer norm and by quadrant.'),
        'put_in': 'A name for the test, the twin, the title and a one-line logline, and the trailer files (mp4, mov or webm). Add a second and third asset to compare cuts of the same spot.',
        'type_this': ['Test the two trailers for Hurry Up Tomorrow on the Gilmore Girls twin', 'Which of these three spots sells the premiere best?'],
        'attach': 'Upload each trailer on the Trailers tab. The title and logline are what the room reads before the first one plays.',
        'comes_back': 'The dial and the moments for each asset, the interest before and after, the lift, the winner and why, by quadrant. Everything a Pilot Testing session carries (questions on the clock, splits, the moderator guide) is here too.',
        'protect': 'Trailers play through links that expire after ten minutes and only for the person who uploaded them, with your name on every frame.',
        'cost_key': 'digital_twin_trailer', 'export': 'Trailers tab: CSV, PDF, Deck or Report on the test.',
    },
    {
        'id': 'titles', 'title': 'Titles and Key Art',
        'answers': ('Up to six titles, or up to six pieces of key art, shown to the same room. Titles: appeal from the title alone, fit once the logline is read, '
                    'how much it stands out, the preferred option with each person\'s reason, by quadrant. Key art: preference, the first thing they look at, '
                    'what the art says the title is, and the lift in interest from seeing it, by quadrant.'),
        'put_in': 'A name for the test, the twin, the logline, and the options: type up to six titles, or upload up to six images (jpg, png or webp).',
        'type_this': ['Test these six titles on the Gilmore Girls twin: ...', 'Which key art sells the show to women under 35?'],
        'attach': 'Upload the art on the Titles and Key Art tab; titles are typed in. Images play through expiring links like a cut.',
        'comes_back': 'A ranked list of the options with the share who prefer each, the reasons in the room\'s own words, appeal and fit per option, and the pick by quadrant.',
        'cost_key': 'digital_twin_title', 'export': 'Titles and Key Art tab: CSV, PDF, Deck or Report on the test.',
    },
    {
        'id': 'norms', 'title': 'Norms',
        'answers': 'The norms every scorecard is read against: by format (features, pilots, episodes, trailers, titles, key art, scripts, concepts) and by genre, with the count of our own tests behind each cell. Every screening adds to these.',
        'put_in': 'Nothing. Pick a genre on the Norms tab to see its column next to the all-genres column.',
        'type_this': ['What is the norm for definitely recommend on a comedy pilot?', 'Show me the trailer norms'],
        'attach': 'Nothing.',
        'comes_back': 'The table on the Norms tab, and the norm column on every scorecard.',
        'cost_key': '', 'export': 'The norms sit on every CSV, PDF, Deck and Report export.',
    },
]


def how_to(price_fn=None) -> dict:
    """The directions as data: six things, each with what it answers,
    what to put in, the lines to type, how to attach, what comes back,
    the cost and how to export. The dashboard renders it; Prometheus
    reads it aloud."""
    prices = {}
    for k, d in PRICING.items():
        v = d
        if price_fn is not None:
            try:
                v = float(price_fn(k, d) or 0) or d
            except Exception:
                v = d
        prices[k] = v

    def money(v):
        return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"
    items = []
    for it in HOW_TO:
        row = dict(it)
        if it['id'] == 'panel':
            row['cost'] = (f"{money(prices['digital_twin_panel'])} a session with {PANEL_QUESTIONS_INCLUDED} questions, then {money(prices['digital_twin_panel_more'])} for each further {PANEL_QUESTIONS_INCLUDED}; "
                           f"{money(prices['digital_twin_panelist'])} for each panelist past {PANEL_SEATS_INCLUDED}; Prometheus usage metered on top")
        elif it['id'] == 'pilot':
            row['cost'] = (f"{money(prices['digital_twin_pilot'])} a session (same as Focus Group IQ), charged on first Play, with {PANEL_QUESTIONS_INCLUDED} questions on the clock, "
                           f"then {money(prices['digital_twin_pilot_more'])} for each further {PANEL_QUESTIONS_INCLUDED}; "
                           f"{money(prices['digital_twin_panelist'])} for each panelist past {PANEL_SEATS_INCLUDED}; Prometheus usage metered on top")
        elif it['id'] == 'twin':
            row['cost'] = f"{money(prices['digital_twins_iq'])}, with its first two runs included; Prometheus usage on the build metered on top"
        elif it['id'] == 'spend':
            row['cost'] = f"{money(prices['digital_twin_spend'])} for the plan window, daily runs included"
        elif it['id'] == 'trailer':
            row['cost'] = (f"{money(prices['digital_twin_trailer'])} per trailer or TV spot, charged on first Play, with {PANEL_QUESTIONS_INCLUDED} questions on the clock; "
                           f"{money(prices['digital_twin_panelist'])} for each panelist past {PANEL_SEATS_INCLUDED}; Prometheus usage metered on top")
        elif it['id'] == 'titles':
            row['cost'] = (f"{money(prices['digital_twin_title'])} for a title test and {money(prices['digital_twin_key_art'])} for a key art test, up to six options each, charged on first Play; "
                           f"{money(prices['digital_twin_panelist'])} for each panelist past {PANEL_SEATS_INCLUDED}; Prometheus usage metered on top")
        elif it['id'] == 'greenlight':
            seat = f"{money(prices['digital_twin_panelist'])} for each panelist past {PANEL_SEATS_INCLUDED}; Prometheus usage metered on top"
            option_cost = {
                'script': f"{money(prices['digital_twin_script'])} a script read, charged on first Play, with {PANEL_QUESTIONS_INCLUDED} questions on the clock; {seat}",
                'concept': f"{money(prices['digital_twin_concept'])} a concept test, charged on first Play; {seat}",
            }
            row['options'] = [dict(o, cost=option_cost.get(o['id'], ''), price=money(prices[o['cost_key']])) for o in it.get('options') or []]
            row['cost'] = (f"Concept {money(prices['digital_twin_concept'])} a concept test; Script read {money(prices['digital_twin_script'])} a script read; "
                           f"each charged on first Play; {seat}")
        elif it['id'] == 'norms':
            row['cost'] = 'Nothing. The norms come with every test.'
        else:
            row['cost'] = f"{money(prices[it['cost_key']])} a run"
        items.append(row)
    return {'title': 'How to use Digital Twins IQ',
            'intro': ('Digital Twins IQ builds a twin of an audience from what people actually do, then runs your plan, title or idea forward on it. '
                      'Everything here can be typed to Prometheus or done on this page. Type the example lines as written, with your own subject, '
                      'and attach files with the paper clip in the chat.'),
            'items': items, 'pricing': pricing_lines(price_fn),
            'where': ('Twins land on the Twins tab; script reads and concept tests on the Greenlight tab under their option; Launch runs on the Launch tab and in '
                      'the twin\'s Runs list; focus group sessions on the Focus Group IQ tab; watches on the Pilot Testing tab; trailer, title and key art tests on '
                      'their own tabs; the norms on the Norms tab. Prometheus confirms every one in the chat. Delete a twin from the Twins tab; you can undo for 30 days.')}


def how_to_text(topic: str = '', price_fn=None) -> str:
    """The directions as plain text for a chat answer: one thing when
    the question names it, all six otherwise."""
    doc = how_to(price_fn)
    low = str(topic or '').lower()
    pick = []
    if re.search(r"\bdigital twins? iq\b|\btwin iq\b|\b(all|everything|whole|five)\b", low):
        pick = []            # the whole product: all six things
    elif re.search(r"\b(green ?light|logline|idea|script|table read|screenplay|concept|one-sheet|one sheet|synopsis)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'greenlight']
    elif re.search(r"\b(launch lab|launch|plan|budget)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'launch']
    elif re.search(r"\b(spend|moves?|daily|rule)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'spend']
    elif re.search(r"\b(trailer|tv spot|spot test)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'trailer']
    elif re.search(r"\b(title test|titles?|key art|poster|logline test)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'titles']
    elif re.search(r"\b(norms?|benchmarks?)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'norms']
    elif re.search(r"\b(pilot test|pilot testing|movie test|dial test|watch the cut|critics room|recut|re-test)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'pilot']
    elif re.search(r"\b(panel|focus group|survey|room|moderat)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'panel']
    elif re.search(r"\b(build|make|create)\b.*\btwin\b|\btwin\b.*\b(build|built)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'twin']
    items = pick or doc['items']
    out = [doc['intro']] if not pick else []
    for it in items:
        out.append(f"{it['title']}. {it['answers']}")
        if it.get('options'):
            out.append('The options: ' + ' | '.join(f"{o['title']} ({o.get('price') or ''}): {o.get('line') or ''}".replace(' ()', '') for o in it['options']))
        out.append(f"Put in: {it['put_in']}")
        out.append('Type to Prometheus: ' + ' | '.join(f'"{t}"' for t in it['type_this'][:3]))
        out.append(f"Files: {it['attach']}")
        out.append(f"What comes back: {it['comes_back']}")
        if it.get('protect'):
            out.append(it['protect'])
        out.append(f"Cost: {it['cost']}. Export: {it['export']}")
    if not pick:
        out.append(doc['where'])
    return '\n\n'.join(out)


HOW_TO_RX = re.compile(r"\b(how (?:do|can|would|should) (?:i|we|you) (?:use|run|open|build|start|work|get)|how does .* work|what (?:is|does) (?:digital twins?|twin iq|greenlight(?: lab| tab)?|launch lab|spend engine|panel lab|focus group iq|focus group|pilot testing|trailer test|title test|key art test|script read|concept test|the norms)|directions|instructions|walk me through|how to (?:use|run|build|open|test))\b", re.I)
TWIN_WORDS_RX = re.compile(r"\b(digital twins?|twins? iq|twin|greenlight(?: lab| tab)?|launch lab|spend engine|panel lab|focus group iq|focus group|pilot testing|pilot test|trailer test|title test|key art test|script read|concept test|norms tab)\b", re.I)


def is_how_to_ask(text: str) -> bool:
    t = ' '.join(str(text or '').split())
    return bool(TWIN_WORDS_RX.search(t) and HOW_TO_RX.search(t))


__all__ = ['list_runs', 'load_twin', 'save_twin', 'has_access', 'is_admin', 'can_open', 'visible_runs', 'allowed_keys', 'find_twin', 'csv_rows', 'csv_text', 'csv_filename',
           'view_summary', 'twin_lines', 'deck_context', 'how_to', 'how_to_text', 'is_how_to_ask', 'pricing_lines', 'PRICING', 'PULL_TYPES', 'MODE_PRICE_KEY', 'PANEL_QUESTIONS_INCLUDED',
           'product_runs', 'is_retired_run', 'RETIRED_RUN_KINDS', 'GREENLIGHT_RETIRED_LINE',
           'PANEL_SEATS_INCLUDED', 'PANEL_SEATS_MAX', 'extra_panelists', 'panelists_for',
           'can_delete_twin', 'delete_twin', 'deleted_twin_key', 'deleted_twin_sidecar_key', 'drop_twin_from_allowed_runs',
           'DELETED_TWINS_PREFIX', 'DELETED_TWIN_KEEP_DAYS',
           'ACCESS_FLAG', 'RUNS_FIELD', 'PRODUCT_KEY', 'PRODUCT_LABEL', 'JOBS_PREFIX', 'REQUESTS_PREFIX', 'S3_PREFIX', 'S3_INDEX_KEY', 'S3_BUCKET', 'HOW_TO']
