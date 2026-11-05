"""Company scope for Digital Twins IQ (Jenna 2026-11-05).

Jenna, verbatim: "on digital twins, everyone should only see the twins
and pilots, etc that their company has pulled. everything should not
auto populate with what someone else has pulled ... so everyone has to
pay to create their own for their shared wallet company things".

The unit is the shared-wallet company already in users.json: the
``company`` field on a user record (the same key the shared wallet
resolves through ``wallet.resolve_billing_subject`` and the Digital
Twins company lock reads in ``wallet.digital_twins_menu_locked``),
folded through ``wallet.canonical_company_name``. A seat with no
company is its own company (``user:<username>``). Crosswalk staff
(super_admin, crosswalknyc.com seats, the standing staff usernames)
belong to the ``Crosswalk`` company and see every company, with a
company filter so they can look at what one client sees.

A twin's company derives lazily from its builder (the ``created_by``
on the record, else the owner segment of the key
``digital-twins/<owner>/...``) through that owner's user record. An
explicit ``company`` on the twin (what ``assign`` writes, on the
payload meta and on the index entry) wins. Pilots, rooms, saved setups
and reports live inside the twin payload, so they move with it.

Nothing here raises into a request: a users.json that cannot be read
resolves every seat to its own company, which only ever narrows what
is visible.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

STAFF_COMPANY = 'Crosswalk'
COMPANY_FIELD = 'company'
OWN_PREFIX = 'user:'
NOT_IN_LIBRARY = "That twin is not in your company's library."
S3_TWIN_PREFIX = 'digital-twins/'

_USERS_TTL = 20.0
_lock = threading.Lock()
_cache: dict = {'at': 0.0, 'data': None}
_users_loader: Optional[Callable[[], dict]] = None


# ------------------------------------------------------------- users
def set_users_loader(fn: Optional[Callable[[], dict]]) -> None:
    """Tests (and any host without the Flask app) hand in the users.json
    reader. None goes back to the app's ``load_users``."""
    global _users_loader
    _users_loader = fn
    reset_cache()


def reset_cache() -> None:
    with _lock:
        _cache['at'] = 0.0
        _cache['data'] = None


def users_data(force: bool = False) -> dict:
    """users.json (``{'users': {...}, 'companies': {...}}``), cached
    briefly. ``{}`` when it cannot be read."""
    now = time.time()
    with _lock:
        if not force and _cache['data'] is not None and now - _cache['at'] < _USERS_TTL:
            return _cache['data']
    data = None
    try:
        if _users_loader is not None:
            data = _users_loader()
        else:
            app = sys.modules.get('app')
            if app is not None and hasattr(app, 'load_users'):
                data = app.load_users()
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    with _lock:
        _cache['at'] = now
        _cache['data'] = data
    return data


# ---------------------------------------------------------- identity
def canonical(name) -> str:
    try:
        from wallet import canonical_company_name
        return str(canonical_company_name(name) or '').strip()
    except Exception:
        return str(name or '').strip()


def _username(user, username: str = '') -> str:
    if username:
        return str(username).strip()
    if isinstance(user, dict):
        return str(user.get('username') or user.get('email') or '').strip()
    return ''


def is_staff(user, username: str = '') -> bool:
    """Crosswalk staff: super_admin, a crosswalknyc.com seat, or one of
    the standing staff usernames. They see every company."""
    if not isinstance(user, dict):
        user = {}
    uname = _username(user, username)
    try:
        import wallet as w
        return bool(w.is_internal_staff_seat(user, uname))
    except Exception:
        role = str(user.get('role') or '').strip().lower().replace(' ', '_')
        if role in ('super_admin', 'superadmin'):
            return True
        email = str(user.get('email') or '').strip().lower()
        if email.endswith('@crosswalknyc.com'):
            return True
        return uname.lower() in ('admin', 'jenna', 'jessie', 'liz', 'anastasia')


def own_company(username: str) -> str:
    return f"{OWN_PREFIX}{str(username or '').strip()}" if str(username or '').strip() else ''


def company_of_user(user, username: str = '', data: Optional[dict] = None) -> str:
    """The company a seat belongs to.

    Staff -> Crosswalk. Else the users.json ``company`` field
    (canonical). Else the seat is its own company (``user:<username>``).
    """
    if not isinstance(user, dict):
        user = {}
    uname = _username(user, username)
    if is_staff(user, uname):
        return STAFF_COMPANY
    company = canonical(user.get(COMPANY_FIELD))
    if company:
        return company
    return own_company(uname)


def company_of_username(username: str, data: Optional[dict] = None) -> str:
    """The company of a seat named by username, read from users.json.
    An unknown username is its own company."""
    uname = str(username or '').strip()
    if not uname:
        return ''
    d = data if isinstance(data, dict) else users_data()
    users = d.get('users') if isinstance(d.get('users'), dict) else {}
    rec = users.get(uname)
    if not isinstance(rec, dict):
        low = uname.lower()
        for k, cand in users.items():
            if isinstance(cand, dict) and (str(k).lower() == low or str(cand.get('email') or '').strip().lower() == low):
                rec = cand
                uname = str(k)
                break
    if not isinstance(rec, dict):
        rec = {}
    return company_of_user(rec, uname, d)


def caller_company(user, username: str = '', data: Optional[dict] = None) -> str:
    return company_of_user(user, username, data)


# -------------------------------------------------------------- twins
def twin_owner(payload: Optional[dict] = None, run: Optional[dict] = None, key: str = '') -> str:
    """Who built the twin: the record's created_by, else the owner
    segment of the key."""
    if isinstance(payload, dict):
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        who = str(meta.get('created_by') or payload.get('created_by') or '').strip()
        if who:
            return who
    if isinstance(run, dict):
        who = str(run.get('created_by') or '').strip()
        if who:
            return who
    k = str(key or '')
    if k.startswith(S3_TWIN_PREFIX):
        rest = k[len(S3_TWIN_PREFIX):]
        if '/' in rest:
            return rest.split('/', 1)[0].strip()
    return ''


def explicit_company(payload: Optional[dict] = None, run: Optional[dict] = None) -> str:
    """The company written on the twin (assign), if any."""
    if isinstance(payload, dict):
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        c = canonical(meta.get(COMPANY_FIELD) or payload.get(COMPANY_FIELD))
        if c:
            return c
    if isinstance(run, dict):
        c = canonical(run.get(COMPANY_FIELD))
        if c:
            return c
    return ''


def twin_company(payload: Optional[dict] = None, run: Optional[dict] = None, key: str = '', data: Optional[dict] = None) -> str:
    """The company a twin belongs to: the explicit field, else the
    builder's company."""
    c = explicit_company(payload, run)
    if c:
        return c
    owner = twin_owner(payload, run, key)
    if owner:
        return company_of_username(owner, data)
    return ''


def can_see(user, payload: Optional[dict] = None, run: Optional[dict] = None, key: str = '', data: Optional[dict] = None, username: str = '') -> bool:
    """Staff see everything. Everyone else sees their own company's
    twins only."""
    if not isinstance(user, dict):
        return False
    uname = _username(user, username)
    if is_staff(user, uname):
        return True
    mine = company_of_user(user, uname, data)
    if not mine:
        return False
    return twin_company(payload, run, key, data) == mine


def company_label(company: str) -> str:
    c = str(company or '')
    if c.startswith(OWN_PREFIX):
        return f"{c[len(OWN_PREFIX):]} (own seat)"
    return c


def annotate(runs: list, data: Optional[dict] = None) -> list:
    """Each index entry with its resolved ``company`` (in place)."""
    d = data if isinstance(data, dict) else users_data()
    for r in runs or []:
        if isinstance(r, dict):
            r[COMPANY_FIELD] = twin_company(None, r, str(r.get('key') or ''), d)
    return runs


def filter_runs(runs: list, user, data: Optional[dict] = None, username: str = '', company: Optional[str] = None) -> list:
    """The index entries this seat may see. Staff: all, or one company
    when ``company`` is given. Others: their own company; ``company``
    is ignored."""
    d = data if isinstance(data, dict) else users_data()
    uname = _username(user, username)
    out = []
    if is_staff(user, uname):
        want = canonical(company) if company else ''
        for r in runs or []:
            if not isinstance(r, dict):
                continue
            c = twin_company(None, r, str(r.get('key') or ''), d)
            r[COMPANY_FIELD] = c
            if want and c != want:
                continue
            out.append(r)
        return out
    mine = company_of_user(user, uname, d)
    if not mine:
        return []
    for r in runs or []:
        if not isinstance(r, dict):
            continue
        c = twin_company(None, r, str(r.get('key') or ''), d)
        if c == mine:
            r[COMPANY_FIELD] = c
            out.append(r)
    return out


def companies(runs: Optional[list] = None, data: Optional[dict] = None) -> list:
    """Every company a staff seat can filter or assign to: the companies
    in users.json, every seat's company, and the companies the twins
    resolve to. ``[{'company', 'label', 'twins'}]`` sorted by name."""
    d = data if isinstance(data, dict) else users_data()
    counts: dict = {}
    seen: dict = {}

    def _add(c, n=0):
        c = str(c or '').strip()
        if not c:
            return
        seen[c] = seen.get(c, 0) + n
    _add(STAFF_COMPANY)
    comps = d.get('companies') if isinstance(d.get('companies'), dict) else {}
    for name in comps:
        _add(canonical(name))
    users = d.get('users') if isinstance(d.get('users'), dict) else {}
    for uname, rec in users.items():
        if isinstance(rec, dict):
            _add(company_of_user(rec, str(uname), d))
    for r in runs or []:
        if isinstance(r, dict):
            c = r.get(COMPANY_FIELD) or twin_company(None, r, str(r.get('key') or ''), d)
            _add(c, 1)
    out = [{'company': c, 'label': company_label(c), 'twins': int(n)} for c, n in seen.items()]
    out.sort(key=lambda x: (x['company'].startswith(OWN_PREFIX), x['label'].lower()))
    return out


# ------------------------------------------------------------- assign
def assign(s3, key: str, company: str, by: str = '') -> dict:
    """Write ``company`` on the twin (payload meta + index entry). The
    twin's pilots, rooms and saved setups live inside the payload and
    move with it. Returns ``{'key', 'company', 'was'}``; raises
    ValueError on a bad key or empty company."""
    from migration import digital_twins as DT
    from migration import digital_twin_synthesis as SYN
    key = str(key or '')
    target = canonical(company)
    if not key.startswith(S3_TWIN_PREFIX) or not key.endswith('.json.gz'):
        raise ValueError('twin not found')
    if not target:
        raise ValueError('company required')
    payload = DT.load_twin(s3, key)
    if not isinstance(payload, dict):
        raise ValueError('twin not found')
    was = twin_company(payload, None, key)
    meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
    meta[COMPANY_FIELD] = target
    meta['company_assigned_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if by:
        meta['company_assigned_by'] = str(by)
    payload['meta'] = meta
    DT.save_twin(s3, key, payload)
    try:
        SYN.update_index(s3, key, payload)
    except Exception:
        pass
    return {'key': key, 'company': target, 'was': was}


__all__ = ['STAFF_COMPANY', 'COMPANY_FIELD', 'OWN_PREFIX', 'NOT_IN_LIBRARY', 'set_users_loader', 'reset_cache', 'users_data',
           'canonical', 'is_staff', 'own_company', 'company_of_user', 'company_of_username', 'caller_company', 'twin_owner',
           'explicit_company', 'twin_company', 'can_see', 'company_label', 'annotate', 'filter_runs', 'companies', 'assign']
