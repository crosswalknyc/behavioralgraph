"""A repeat approval is answered, never run twice (Jenna 2026-11-17).

Naomi Inoshita at Sony, 2026-11-17: her Approve & run on a Subscriber IQ
was refused by a wallet fault, so the team started the same tracker for
her from the build server. It ran, was delivered and was charged once.
The Approve & run card for that very brief was still open in her tab,
the approve worked again by then, and one more click would have started
a second identical run and taken a second charge. The half-hour guard
that existed only knew chat-approved new profile builds: it did not
cover Subscriber IQ, and it could not see a run started any other way.

The rule: before anything is priced, charged or queued, an approval is
compared with what is already running or was delivered in the last day
for the same account or the same company wallet. When it is the same
order, nothing starts and nothing is charged; the reader is told it is
already running, or already delivered and where it is.

THE SAME ORDER (``spec_key``): the same product (a profile build, a
refresh, a cut, a Subscriber IQ tracker), the same subject or title
(case and punctuation insensitive; a tracker also by season), the same
platform where one applies, the same cut definition, and the same date
window. A different window, platform, subject or cut is a different
order and runs as it always has.

WHERE IT LOOKS, so a run is seen however it was started (chat Approve,
the partner route, the local operator path):

  1. The guard's own record (``STORE_KEY``, the file the half-hour
     guard has kept since 2026-09-29). Chat approvals and the operator
     path write the order's key here, with the run id once there is one.
     An entry is only ever a pointer: past its first half hour it counts
     only when the engine says that run is still going or finished.
  2. The engine's own run records (its recent runs, with the run owner
     mapping for a run listed under another identity). The engine stamps
     each run with the same key (``order_key``), so a run the partner
     route or anything else started compares exactly. A run from before
     that stamp is matched by name and product: still going, it is the
     same order (the engine holds one active run per subject and product
     anyway); finished, it is the same order only when its delivery
     record carries the same window.
  3. The delivered file itself: a run the engine calls finished whose
     file is no longer where we would send the reader is not "already
     delivered", and the approval runs.

THE WINDOW. Two tiers, one guard:

  * ``RESERVE_S`` (30 minutes): the 2026-09-29 rule, now for every
    product. A second approval of the same order within half an hour is
    a duplicate; for a profile build it still is when its window drifted
    by up to ``DRIFT_DAYS`` (smclain's Trinity Tatum ran twice on a
    6-day drift of the default window). It stands on the guard's record
    alone, so a double click is caught before the engine has the run.
  * ``WINDOW_S`` (24 hours): the identical order, to the day. Naomi's
    card was under an hour stale; a card left open overnight is the
    plausible worst case, and a day covers it. It is not longer because
    a deliberate re-run of the same order a day later is a real request,
    and it is exact (no drift) because past the first half hour a moved
    window is a different order, not a double click.

NO OVERRIDE. Nothing in a request can switch this off (the standing
no-override rule for the dashboard and the partner route). A deliberate
second run inside the window is an operator's call, made on the build
server through ``migration/local_override_profile.py``, which posts to
the engine itself and never passes through this check.

FAIL OPEN. Every lookup that errors or times out counts as "nothing
found": a first, legitimate approval is never blocked by this module.
The one exception is the half-hour tier, which needs no lookup and
behaves as it has since September.

Pure apart from the callables on ``Records``. Never raises. Regression:
scripts/test_pm_repeat_approval_guard.py.
"""
from __future__ import annotations

import json
import re
import secrets
import time
import traceback
from datetime import datetime, timezone

__all__ = ['STORE_KEY', 'OWNERS_KEY', 'WINDOW_S', 'RESERVE_S', 'DRIFT_DAYS', 'IN_FLIGHT_S', 'KINDS', 'norm', 'window_of', 'spec_key',
           'same_entry', 'run_state', 'find_prior', 'reserve', 'confirm', 'release', 'note_started', 'reply', 'payload',
           'Records', 'records_for', 'Gate', 'legacy_hold', 'note_operator_start', 'active_thread_id']

STORE_KEY = 'system/usage/recent_builds.json'     # the guard's record since 2026-09-29; the name is kept
OWNERS_KEY = 'system/api_run_owners.json'         # run id -> the seat it belongs to (partner route, operator path)
DELIVERY_PREFIX = 'system/pm_deliveries/'         # prometheus.delivered writes one record per delivered run
SUBIQ_BUCKET = 'svod-acquisition'                 # Subscriber IQ trackers live here

WINDOW_S = 24 * 3600        # the identical order: see THE WINDOW above
RESERVE_S = 30 * 60         # the half-hour duplicate rule of 2026-09-29
DRIFT_DAYS = 21             # inside the half hour, a window this close is the same order
ACTIVE_STALE_S = 6 * 3600   # the engine's own rule: a run not heard from in six hours no longer holds its subject
IN_FLIGHT_S = 120           # an order on its way to the engine: the post's own time limit, one more try, and room
_KEEP_S = WINDOW_S + 2 * 3600
_MAX_ENTRIES = 600
_ENGINE_TIMEOUT_S = 5
_ENGINE_LIST_LIMIT = 400

# what an approval can start, by the decision it carries
KINDS = {'new_build': 'profile', 'time_shifted_refresh': 'refresh', 'derive_cut': 'cut', 'cut_needs_parent': 'cut',
         'subscriber_iq': 'subiq'}
_DONE = frozenset(('complete', 'completed', 'done'))
# ended without a deliverable; the charge has gone back, so the same order may be placed again
_DEAD = frozenset(('failed', 'error', 'cancelled', 'canceled', 'refunded', 'cancel_requested'))
_KEY_FIELDS = ('kind', 'subject', 'platform', 'cut', 'ws', 'we')
_DATE_RX = re.compile(r'\d{4}-\d{2}-\d{2}')
_STAMP_RX = re.compile(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$')


def _note(where, exc):
    try:
        from . import faults as _faults
        _faults.note('repeat_approval.' + where, exc)
    except Exception:
        pass


def norm(s):
    """Case and punctuation insensitive: lower case, apostrophes closed
    up, every other mark a space."""
    s = re.sub(r"['\u2018\u2019\u02bc`]", '', str(s or '').lower())
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def window_of(v):
    """(start, end) as YYYY-MM-DD from a {start, end} dict, a pair, or
    the engine's 'START TO END' string; ('', '') when there is none."""
    if isinstance(v, dict):
        s, e = v.get('start'), v.get('end')
    elif isinstance(v, (list, tuple)) and len(v) >= 2:
        s, e = v[0], v[-1]
    else:
        found = _DATE_RX.findall(str(v or ''))
        s, e = (found[0], found[-1]) if len(found) >= 2 else ('', '')
    s, e = str(s or '').strip()[:10], str(e or '').strip()[:10]
    return (s, e) if (_DATE_RX.fullmatch(s) and _DATE_RX.fullmatch(e)) else ('', '')


def _plain(name):
    """A name fit for a reply: one line, no long dashes."""
    return ' '.join(str(name or '').replace('\u2014', '-').replace('\u2013', '-').split())[:160]


def spec_key(decision, spec, draft=None, *, derive_type='', parent_key='', cut_label=''):
    """The order's identity: what makes two approvals the same order.

    Built from the spec the engine is sent (and the draft where the spec
    has not been shaped yet), so the chat approve, the operator path and
    the engine itself all arrive at the same key for the same order."""
    spec = spec if isinstance(spec, dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    dec = str(decision or '').strip().lower()
    kind = KINDS.get(dec, dec or 'profile')
    name = str(spec.get('name') or draft.get('subject') or draft.get('name') or '').strip()
    ws, we = window_of(spec.get('date_range'))
    if not ws:
        ws, we = window_of(draft.get('date_range'))
    scope = spec.get('platform_scope')
    platform = ' '.join(sorted(norm(p) for p in scope if norm(p))) if isinstance(scope, (list, tuple)) else norm(scope)
    subject, label, display, cut = norm(name), '', name, ''
    if kind == 'subiq':
        sq = spec.get('subiq') if isinstance(spec.get('subiq'), dict) else (
            draft.get('subiq') if isinstance(draft.get('subiq'), dict) else {})
        title = str(sq.get('title') or name).split(' - ', 1)[0].strip()
        subject = norm(title)
        try:
            if sq.get('season') is not None and str(sq.get('season')).strip() != '':
                subject += f" season {int(sq.get('season'))}"
        except (TypeError, ValueError):
            pass
        if str(sq.get('medium') or '').strip().lower() == 'movie':
            subject += ' movie'
        platform = norm(sq.get('platform')) or platform
        a_s, a_e = window_of(sq.get('air_window'))
        if a_s:
            # the window the tracker is measured on: a season still airing stops at its through date
            thr = str(sq.get('through_date') or '').strip()[:10]
            if sq.get('season_to_date') and _DATE_RX.fullmatch(thr) and thr < a_e:
                a_e = thr
            ws, we = a_s, a_e
        label = norm(sq.get('deliverable_label'))
        display = str(sq.get('deliverable_label') or title or name).strip()
        cut = ' '.join(p for p in ('with profile' if sq.get('addon_profile') else '',
                                   norm(sq.get('movie_scope'))) if p)
    elif kind == 'cut':
        c_s, c_e = window_of(spec.get('cut_date_range'))
        if c_s:
            ws, we = c_s, c_e
        parent = str(parent_key or '').rsplit('/', 1)[-1].lower()
        cut = ' | '.join((norm(derive_type), norm(cut_label), parent))
        if str(cut_label or '').strip():
            display = f"{name} - {str(cut_label).strip()}"
    else:
        cuts = spec.get('addon_cuts') if isinstance(spec.get('addon_cuts'), list) else draft.get('addon_cuts')
        ids = sorted(norm(c.get('cut_id') or c.get('label')) for c in (cuts or [])
                     if isinstance(c, dict) and (c.get('cut_id') or c.get('label')))
        cut = ' + '.join(i for i in ids if i)
    return {'kind': kind, 'subject': subject, 'name': norm(name), 'label': label, 'platform': platform,
            'ws': ws, 'we': we, 'cut': cut, 'display': _plain(display or name)}


# ------------------------------------------------------------- the guard's record
def _days(s):
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d')
    except Exception:
        return None


def same_entry(entry, key, now):
    """'reserve' when the entry is the half-hour duplicate of ``key``,
    'exact' when it is the identical order inside the day, else ''."""
    try:
        age = float(now) - float(entry.get('t') or 0)
    except (TypeError, ValueError):
        return ''
    if age < -300 or age > WINDOW_S:        # two servers' clocks are never exactly one clock
        return ''
    if (entry.get('k') or 'profile') != key.get('kind') or entry.get('s') != key.get('subject'):
        return ''
    if (entry.get('p') or '') != (key.get('platform') or ''):
        return ''
    same_cut = (entry.get('c') or '') == (key.get('cut') or '')
    same_win = (str(entry.get('ws') or '') == str(key.get('ws') or '')
                and str(entry.get('we') or '') == str(key.get('we') or ''))
    if age <= RESERVE_S:
        if key.get('kind') == 'profile':
            # the 2026-09-29 rule as it was written: a profile's default window is recomputed on every draft, so
            # a window within three weeks of the first is the same order; one that moved further is a correction
            ws0, we0 = _days(entry.get('ws')), _days(entry.get('we'))
            ws1, we1 = _days(key.get('ws')), _days(key.get('we'))
            if not (ws0 and we0 and ws1 and we1) or max(abs((ws1 - ws0).days), abs((we1 - we0).days)) <= DRIFT_DAYS:
                return 'reserve'
        elif same_win and (same_cut or key.get('kind') != 'cut'):
            # a tracker, a refresh or a cut names its own window, so only the same window is the same order; two
            # different cuts of one parent are two orders even a minute apart
            return 'reserve'
    return 'exact' if (same_cut and same_win) else ''


def _load_entries(records, now):
    doc = records.store_get() or {}
    out = []
    for e in (doc.get('entries') if isinstance(doc, dict) else None) or []:
        try:
            if isinstance(e, dict) and float(now) - float(e.get('t') or 0) < _KEEP_S:
                out.append(e)
        except (TypeError, ValueError):
            continue
    return out


def _save_entries(records, entries):
    records.store_put({'entries': list(entries)[-_MAX_ENTRIES:]})


def _entry(key, username, now, via, run_id=''):
    e = {'u': str(username), 's': key.get('subject') or '', 'ws': key.get('ws') or '', 'we': key.get('we') or '',
         't': float(now), 'k': key.get('kind') or 'profile', 'p': key.get('platform') or '', 'c': key.get('cut') or '',
         'd': str(key.get('display') or '')[:160], 'via': str(via or 'chat')[:20], 'id': secrets.token_hex(6)}
    if run_id:
        e['r'] = str(run_id)[:80]
    return e


def _eid(e):
    """An entry's own name: its id, or who, what and when for an entry written before ids."""
    return e.get('id') or (e.get('u'), e.get('s'), e.get('t'))


def _users_of(ctx, username):
    who = (ctx or {}).get('who') if isinstance(ctx, dict) else None
    users = set((who or {}).get('users') or ())
    users.add(str(username or '').strip().lower())
    return users


def reserve(key, *, username, records, now=None, via='chat', ctx=None):
    """Hold the order just before it is sent to the engine. Returns
    (earlier entry, '') when the same order was placed in the last half
    hour (the 2026-09-29 duplicate rule, now for every product), else
    (None, token) with this order on record. Storage trouble means no
    block and no token."""
    try:
        now = float(now or time.time())
        username = str(username or '').strip()
        if not isinstance(key, dict) or not key.get('subject') or not username:
            return None, ''
        try:
            entries, readable = _load_entries(records, now), True
        except Exception as e:                          # noqa: BLE001 - could not look is not a duplicate
            _note('reserve.read', e)
            entries, readable = [], False
        users = _users_of(ctx, username)
        dead = set((ctx or {}).get('dead') or ()) if isinstance(ctx, dict) else set()
        for e in reversed(entries):
            if str(e.get('u') or '').strip().lower() not in users or _eid(e) in dead:
                continue
            if same_entry(e, key, now) == 'reserve':
                return e, ''
        if not readable:
            return None, ''                             # never write over a record that could not be read
        ent = _entry(key, username, now, via)
        try:
            _save_entries(records, entries + [ent])
        except Exception as e:                          # noqa: BLE001
            _note('reserve.write', e)
            return None, ''
        return None, ent['id']
    except Exception:
        traceback.print_exc()
        return None, ''


def _edit(records, token, fn, now=None):
    try:
        if not token:
            return False
        now = float(now or time.time())
        entries = _load_entries(records, now)
        out, hit = [], False
        for e in entries:
            if e.get('id') == token:
                hit = True
                e = fn(dict(e))
                if e is None:
                    continue
            out.append(e)
        if hit:
            _save_entries(records, out)
        return hit
    except Exception as e:                              # noqa: BLE001
        _note('edit', e)
        return False


def confirm(token, run_id, *, records, now=None):
    """The engine took the order: its run id goes on the held entry."""
    return _edit(records, token, lambda e: dict(e, r=str(run_id or '')[:80]), now) if run_id else False


def release(token, *, records, now=None):
    """The order did not start: the hold comes off, so sending it again works."""
    return _edit(records, token, lambda e: None, now)


def note_started(key, *, username, run_id, records, via='operator', now=None):
    """Put a started order on the guard's record without checking it
    (the operator path: it is the override, so it is never refused, and
    what it starts must still be seen by the next approval)."""
    try:
        now = float(now or time.time())
        username = str(username or '').strip()
        if not isinstance(key, dict) or not key.get('subject') or not username or not run_id:
            return False
        entries = _load_entries(records, now)
        _save_entries(records, entries + [_entry(key, username, now, via, run_id)])
        return True
    except Exception as e:                              # noqa: BLE001
        _note('note_started', e)
        return False


# ------------------------------------------------------------- the engine's records
def _epoch(v):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v or '').strip()
        if not s:
            return None
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def run_state(doc, now=None):
    """'running' | 'delivered' | 'dead' for an engine run record, or
    None when it says nothing we can stand on (unknown, unreadable, or a
    run not heard from in six hours)."""
    if not isinstance(doc, dict):
        return None
    st = str(doc.get('status') or '').strip().lower()
    if not st or st in ('unknown', 'read_error'):
        return None
    if st in _DONE:
        return 'delivered'
    if st in _DEAD:
        return 'dead'
    heard = _epoch(doc.get('updated_at') or doc.get('started_at') or doc.get('queued_at'))
    if heard is not None and float(now or time.time()) - heard > ACTIVE_STALE_S:
        return None
    return 'running'


class _Engine:
    """The engine's answers for one check. One failure and it is left
    alone for the rest of the check (fail open, and no second wait)."""

    def __init__(self, records):
        self.records, self.down, self._seen = records, False, {}

    def status(self, run_id):
        rid = str(run_id or '')
        if not rid or self.down:
            return None
        if rid not in self._seen:
            try:
                doc = self.records.run_status(rid)
                self._seen[rid] = doc if isinstance(doc, dict) else None
            except Exception as e:                      # noqa: BLE001
                _note('engine.status', e)
                self.down = True
                return None
        return self._seen.get(rid)

    def recent(self):
        if self.down:
            return []
        try:
            return [d for d in (self.records.recent_runs() or []) if isinstance(d, dict)]
        except Exception as e:                          # noqa: BLE001
            _note('engine.recent', e)
            self.down = True
            return []


def _file_label(key):
    stem = str(key or '').rsplit('/', 1)[-1]
    stem = stem[:-4] if stem.lower().endswith('.csv') else stem
    return norm(_STAMP_RX.sub('', stem))


def _doc_matches(doc, key):
    """'exact' when the run carries the same order key, 'name' when it
    carries none and is the same product under the same name, else ''."""
    ok = doc.get('order_key')
    if isinstance(ok, dict) and ok.get('kind'):
        return 'exact' if all(str(ok.get(f) or '') == str(key.get(f) or '') for f in _KEY_FIELDS) else ''
    dec = str(doc.get('decision') or '').strip().lower()
    if KINDS.get(dec, dec) != key.get('kind') or key.get('kind') == 'cut':
        return ''                       # a cut is the same cut only with its own key on record
    subj = norm(doc.get('subject'))
    if subj and subj == key.get('name'):
        return 'name'
    if key.get('kind') == 'subiq' and key.get('label') and doc.get('subiq_s3_key') \
            and _file_label(doc.get('subiq_s3_key')) == key.get('label') \
            and key.get('platform') and f" {key['platform']} " in f" {subj} ":
        return 'name'                   # the tracker file is this title and season, on this platform
    return ''


def _who(records, username, user=None):
    who = {'users': {str(username or '').strip().lower()}, 'emails': set(), 'by_email': {}}
    try:
        got = records.members(username) or {}
        who['users'] |= {str(u).strip().lower() for u in (got.get('users') or ()) if str(u).strip()}
        who['emails'] |= {str(e).strip().lower() for e in (got.get('emails') or ()) if str(e).strip()}
        who['by_email'].update({str(k).strip().lower(): str(v).strip() for k, v in (got.get('by_email') or {}).items()})
    except Exception as e:                              # noqa: BLE001 - the account alone is still checked
        _note('members', e)
    if isinstance(user, dict):
        em = str(user.get('email') or '').strip().lower()
        if em:
            who['emails'].add(em)
            who['by_email'].setdefault(em, str(username or '').strip())
    who['users'].discard('')
    return who


def _delivery_window(records, who, doc, owner, now):
    """The window on the delivery record written for this run, or None."""
    rid = str(doc.get('run_id') or '')
    ident = str(doc.get('user_email') or '').strip().lower()
    names = []
    for n in (owner, who['by_email'].get(ident), ident.split('@')[0] if '@' in ident else ident):
        n = str(n or '').strip()
        if n and n.lower() not in [x.lower() for x in names]:
            names.append(n)
    for n in names[:3]:
        try:
            for rec in records.deliveries(n, now - _KEEP_S) or []:
                if isinstance(rec, dict) and str(rec.get('job_id') or '') == rid:
                    return window_of(rec.get('window'))
        except Exception as e:                          # noqa: BLE001
            _note('deliveries', e)
    return None


def _gone(records, key, doc):
    """True only when the delivered file is known to be gone."""
    try:
        if not isinstance(doc, dict):
            return False
        subiq = key.get('kind') == 'subiq'
        fkey = doc.get('subiq_s3_key') if subiq else doc.get('tu_key')
        if not fkey:
            return False
        return records.exists('subiq' if subiq else 'profile', str(fkey)) is False
    except Exception as e:                              # noqa: BLE001
        _note('exists', e)
        return False


def _prior(state, key, run_id, via):
    return {'state': state, 'kind': key.get('kind'), 'name': key.get('display') or '', 'run_id': str(run_id or ''),
            'via': str(via or '')}


def find_prior(key, *, username, records, user=None, now=None, ctx=None):
    """The same order, already running or delivered for this account or
    its company wallet: {'state': 'running' | 'delivered', 'kind',
    'name', 'run_id', 'via'}, or None. Reads only. Never raises; any
    trouble is None (the approval goes ahead)."""
    try:
        return _find_prior(key, str(username or '').strip(), records, user, float(now or time.time()),
                           ctx if isinstance(ctx, dict) else {})
    except Exception:
        traceback.print_exc()
        return None


def _find_prior(key, username, records, user, now, ctx):
    if not isinstance(key, dict) or not key.get('subject') or not username:
        return None
    who = _who(records, username, user)
    ctx['who'] = who
    dead = ctx.setdefault('dead', set())
    engine = _Engine(records)
    judged = set()

    # 1) the guard's own record: what chat approvals and the operator path wrote
    try:
        entries = _load_entries(records, now)
    except Exception as e:                              # noqa: BLE001
        _note('find.read', e)
        entries = []
    held = []                   # placed in the last half hour, with nothing from the engine behind them yet
    for e in sorted(entries, key=lambda x: float(x.get('t') or 0), reverse=True):
        if str(e.get('u') or '').strip().lower() not in who['users']:
            continue
        how = same_entry(e, key, now)
        if not how:
            continue
        rid = str(e.get('r') or '')
        doc = engine.status(rid) if rid else None
        state = run_state(doc, now)
        if rid:
            judged.add(rid)
        if state == 'dead':
            dead.add(_eid(e))
            continue                                    # it ended with nothing delivered: the order may be placed again
        if state == 'delivered' and _gone(records, key, doc):
            continue
        if state in ('running', 'delivered'):
            return _prior(state, key, rid, e.get('via') or 'chat')
        if how == 'reserve':
            held.append(e)
        # past the half hour with nothing from the engine to stand on: not a block

    # 2) the engine's own run records: anything started any other way
    owners = None
    for d in engine.recent():
        rid = str(d.get('run_id') or '')
        if not rid or rid in judged:
            continue
        state = run_state(d, now)
        if state not in ('running', 'delivered'):
            continue
        when = _epoch(d.get('completed_at') or d.get('updated_at') or d.get('started_at') or d.get('queued_at'))
        if when is None or now - when > WINDOW_S:
            continue
        how = _doc_matches(d, key)
        if not how:
            continue
        ident, owner = str(d.get('user_email') or '').strip().lower(), ''
        if ident not in who['emails'] and ident not in who['users']:
            if owners is None:
                try:
                    owners = records.owners() or {}
                except Exception as e:                  # noqa: BLE001
                    _note('owners', e)
                    owners = {}
            owner = str(owners.get(rid) or '').strip()
            if not owner or owner.lower() not in who['users']:
                continue
        if how == 'name' and state == 'delivered':
            # no key on the run: a finished run is the same order only when its delivery record says the same window
            win = _delivery_window(records, who, d, owner, now)
            if not win or not win[0] or win != (key.get('ws') or '', key.get('we') or ''):
                continue
        if state == 'delivered' and _gone(records, key, d):
            continue
        return _prior(state, key, rid, 'engine')

    # 3) an order placed in the last half hour that the engine has not shown us. When the engine could not be
    # asked, the hold stands on its own, as it has since 2026-09-29. When the engine answered and holds no such
    # run, an order still on its way (the last two minutes) is running; an older one never started (a post that
    # could not be confirmed and did not land), so "send it again" works.
    for e in held:
        if engine.down or now - float(e.get('t') or 0) <= IN_FLIGHT_S:
            return _prior('running', key, str(e.get('r') or ''), e.get('via') or 'chat')
        dead.add(_eid(e))
    return None


# ------------------------------------------------------------- what the reader is told
def reply(prior, key=None):
    """One or two plain sentences. No price, no internal word."""
    prior = prior if isinstance(prior, dict) else {}
    name = _plain(prior.get('name') or (key or {}).get('display') or '') or 'That one'
    kind = prior.get('kind') or (key or {}).get('kind')
    if prior.get('state') == 'delivered':
        if kind == 'subiq':
            return f"{name} is already delivered. It is in the Subscriber IQ tab, and there is nothing more to approve."
        return (f"{name} is already delivered. It is in Profile IQ under Select Profile, and there is nothing more "
                "to approve.")
    return f"{name} is already running, so I did not start it again. The ready notice comes when it finishes."


def payload(prior, key=None):
    """The approve reply for a repeat: guidance the chat shows as it is."""
    prior = prior if isinstance(prior, dict) else {}
    name = _plain(prior.get('name') or (key or {}).get('display') or '')
    out = {'success': False, 'guidance': True, 'error': reply(prior, key), 'already': prior.get('state') or 'running',
           'followups': []}
    if name:
        out['subject'] = name
        if prior.get('state') == 'delivered':
            out['followups'] = [f"Open {name}"[:200]]
    return out


# ------------------------------------------------------------- the real records
class Records:
    """The callables the check reads through. Each may raise; the check
    treats a raise as "could not look"."""

    def __init__(self, **fns):
        for name in ('store_get', 'store_put', 'run_status', 'recent_runs', 'owners', 'deliveries', 'exists', 'members'):
            setattr(self, name, fns.get(name) or self._absent(name))

    @staticmethod
    def _absent(name):
        def _fn(*_a, **_k):
            raise RuntimeError(f'{name} is not available here')
        return _fn


def _attr(h, name, default=None):
    try:
        v = getattr(h, name)
        return default if v is None else v
    except Exception:
        return default


def _missing(exc):
    text = f"{type(exc).__name__} {exc}"
    return isinstance(exc, KeyError) or any(t in text for t in ('NoSuchKey', 'Not Found', 'NotFound', '404'))


def _company_members(h, username):
    """Every seat that draws on the same company wallet as ``username``
    (the seat itself when it pays on its own)."""
    data = h.load_users() or {}
    users = data.get('users') if isinstance(data.get('users'), dict) else {}
    me = users.get(username) if isinstance(users.get(username), dict) else {}
    out = {'users': {str(username)}, 'emails': set(), 'by_email': {}}
    if me.get('email'):
        out['emails'].add(str(me['email']))
        out['by_email'][str(me['email']).strip().lower()] = str(username)
    if str(me.get('billing_source') or '').strip().lower() != 'company':
        return out
    import wallet as _w
    _subj, kind, skey = _w.resolve_billing_subject(me, data)
    if kind != 'company' or not skey:
        return out
    for uname, rec in users.items():
        if not isinstance(rec, dict) or str(rec.get('billing_source') or '').strip().lower() != 'company':
            continue
        _s2, k2, key2 = _w.resolve_billing_subject(rec, data)
        if k2 == 'company' and key2 == skey:
            out['users'].add(str(uname))
            if rec.get('email'):
                out['emails'].add(str(rec['email']))
                out['by_email'].setdefault(str(rec['email']).strip().lower(), str(uname))
    return out


def records_for(h):
    """``Records`` over a host: any object with ``s3_client``,
    ``S3_BUCKET``, ``SYNTH_QUEUE_URL``, ``SYNTH_QUEUE_SECRET`` and
    ``load_users`` (the legacy host proxy in the web app, the app module
    itself on the build server)."""
    s3 = _attr(h, 's3_client')
    bucket = _attr(h, 'S3_BUCKET', 'dashboard-inputs')
    subiq_bucket = _attr(h, 'SUBSCRIBER_S3_BUCKET', SUBIQ_BUCKET)

    def _engine_get(path, params=None):
        url = str(_attr(h, 'SYNTH_QUEUE_URL', '') or '').rstrip('/')
        secret = str(_attr(h, 'SYNTH_QUEUE_SECRET', '') or '')
        if not url or not secret:
            raise RuntimeError('engine not configured')
        import requests as _rq
        return _rq.get(f"{url}{path}", params=params or None, timeout=_ENGINE_TIMEOUT_S,
                       headers={'X-Synth-Auth': secret})

    def _json(key, b=None):
        return json.loads(s3.get_object(Bucket=b or bucket, Key=key)['Body'].read().decode('utf-8') or '{}')

    def store_get():
        try:
            return _json(STORE_KEY)
        except Exception as e:                          # noqa: BLE001
            if _missing(e):
                return {}
            raise

    def store_put(doc):
        s3.put_object(Bucket=bucket, Key=STORE_KEY, Body=json.dumps(doc).encode('utf-8'),
                      ContentType='application/json')

    def run_status(run_id):
        rid = re.sub(r'[^A-Za-z0-9_-]', '', str(run_id or ''))[:80]
        resp = _engine_get(f"/synth/status/{rid}")
        if resp.status_code == 404:
            return {'run_id': rid, 'status': 'unknown'}
        if resp.status_code != 200:
            raise RuntimeError(f'engine status {resp.status_code}')
        return resp.json() or {}

    def recent_runs():
        early = __import__('prometheus.brief_speed', fromlist=['prefetched']).prefetched('engine_list')
        if early is not None:           # the approve path started this read before the spec build (brief_speed, 2026-11-24)
            return early.get(_ENGINE_TIMEOUT_S + 1)
        resp = _engine_get('/synth/list', {'limit': _ENGINE_LIST_LIMIT})
        if resp.status_code != 200:
            raise RuntimeError(f'engine list {resp.status_code}')
        return resp.json() or []

    def owners():
        try:
            doc = _json(OWNERS_KEY)
        except Exception as e:                          # noqa: BLE001
            if _missing(e):
                return {}
            raise
        return doc if isinstance(doc, dict) else {}

    def deliveries(username, since):
        safe = ''.join(c for c in str(username or 'anon') if c.isalnum() or c in '-_.@').lower() or 'anon'
        prefix = f"{DELIVERY_PREFIX}{safe}/"
        floor = time.strftime('%Y%m%dT%H%M%S', time.gmtime(float(since)))   # the records are named by when they landed
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, StartAfter=prefix + floor)
        keys = sorted((o['Key'] for o in (resp.get('Contents') or [])
                       if str(o.get('Key') or '').rsplit('/', 1)[-1][:15] >= floor), reverse=True)[:12]
        out = []
        for k in keys:
            try:
                out.append(_json(k))
            except Exception:
                continue
        return out

    def exists(kind, key):
        try:
            s3.head_object(Bucket=subiq_bucket if kind == 'subiq' else bucket, Key=str(key))
            return True
        except Exception as e:                          # noqa: BLE001
            return False if _missing(e) else None

    return Records(store_get=store_get, store_put=store_put, run_status=run_status, recent_runs=recent_runs,
                   owners=owners, deliveries=deliveries, exists=exists, members=lambda u: _company_members(h, u))


# ------------------------------------------------------------- the approve path
class Gate:
    """One approval's way through the check, for the approve path
    (prometheus.legacy.chat._pm_approve_core): ``check`` before anything
    is priced, ``hold`` just before the order goes to the engine,
    ``started`` when the engine hands back a run id, ``not_started``
    when it did not take the order. Each returns the approve reply to
    send back, or None to go on. Nothing here reads the request; no
    method raises; any trouble is "go on"."""

    def __init__(self, h, user, username, probe=False):
        self.h = h
        self.user = user if isinstance(user, dict) else {}
        self.username = str(username or '').strip()
        self.probe = bool(probe)          # a probe is still checked (reads only) but never held or put on record
        self.key, self.ctx, self.token = None, {}, ''

    def check(self, decision, spec, draft, derive_type='', parent_key=''):
        """The same order, already running or delivered in the last day for this account or its company
        wallet, however that run was started: the reply. Else None."""
        try:
            draft = draft if isinstance(draft, dict) else {}
            cut = ''
            if str(decision or '').strip().lower() in ('derive_cut', 'cut_needs_parent'):
                cut = str(draft.get('cut_label_clean') or draft.get('cut_label') or '').strip()
                scrub = _attr(self.h, '_scrub_spec_text')
                if cut and callable(scrub):     # the label as the engine is sent it
                    cut = str(scrub(cut, field='payload_cut_label', subject=(spec or {}).get('name') or '',
                                    max_len=160, single_line=True) or '')
            self.key = spec_key(decision, spec, draft, derive_type=derive_type or '', parent_key=parent_key or '',
                                cut_label=cut)
            prior = find_prior(self.key, username=self.username, user=self.user, records=records_for(self.h),
                               ctx=self.ctx)
            if prior is None:
                return None
            print(f"[repeat-approval] {self.username}: {self.key.get('kind')} {self.key.get('display')!r} is already "
                  f"{prior.get('state')} (run {prior.get('run_id') or 'pending'}, via {prior.get('via')}); "
                  f"nothing started, nothing charged")
            return payload(prior, self.key)
        except Exception as e:                          # noqa: BLE001 - could not look: the approval goes ahead
            _note('gate.check', e)
            return None

    def hold(self):
        """The half-hour duplicate rule, on the guard's record alone: the reply when the same order was placed
        in the last half hour, else this order is put on record and None."""
        try:
            if not self.key or self.probe:
                return None
            hit, self.token = reserve(self.key, username=self.username, records=records_for(self.h), ctx=self.ctx)
            if hit is None:
                return None
            return payload({'state': 'running', 'kind': self.key.get('kind'), 'name': self.key.get('display')},
                           self.key)
        except Exception as e:                          # noqa: BLE001
            _note('gate.hold', e)
            return None

    def started(self, run_id):
        """The engine took the order: its run id goes on the hold, so the day-long check can ask about it."""
        try:
            if self.token and run_id:
                confirm(self.token, run_id, records=records_for(self.h))
        except Exception as e:                          # noqa: BLE001
            _note('gate.started', e)

    def not_started(self):
        """The order did not start: the hold comes off, so "send it again" works."""
        try:
            if self.token:
                release(self.token, records=records_for(self.h))
                self.token = ''
        except Exception as e:                          # noqa: BLE001
            _note('gate.not_started', e)


def legacy_hold(h, username, subject, ws='', we='', probe=False):
    """The guard as it was called before 2026-11-17 (a profile build of
    ``subject`` on ``ws`` to ``we``): the earlier entry when the same
    order was placed in the last half hour, else this one is put on
    record and None. Fail-open: any storage trouble means no block."""
    try:
        if probe:
            return None
        key = spec_key('new_build', {'name': subject, 'date_range': {'start': str(ws or '')[:10],
                                                                    'end': str(we or '')[:10]}})
        return reserve(key, username=username, records=records_for(h))[0]
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------------------------------------- the operator path
def active_thread_id(h, username):
    """The seat's active chat, read only ('' when it cannot be read)."""
    try:
        key_fn = _attr(h, '_pm_threads_index_key')
        if not callable(key_fn):
            return ''
        s3, bucket = _attr(h, 's3_client'), _attr(h, 'S3_BUCKET', 'dashboard-inputs')
        idx = json.loads(s3.get_object(Bucket=bucket, Key=key_fn(username))['Body'].read().decode('utf-8'))
        return str((idx if isinstance(idx, dict) else {}).get('active') or '')[:96]
    except Exception:
        return ''


def note_operator_start(h, *, username, run_id, decision, spec, derive_type='', parent_key='', cut_label='',
                        thread_id='', engine_user='', now=None):
    """What the local operator path leaves behind for a run it started on
    a seat's behalf: the order on the guard's record (so the seat's next
    approval of the same brief is answered, not run), and, when the chat
    it belongs to is known, the launch record the finished line is posted
    from. Local only: nothing on the dashboard or the partner route
    reaches this. Returns {'guard': bool, 'launch': bool}. Never raises."""
    out = {'guard': False, 'launch': False}
    try:
        username = str(username or '').strip()
        if not username or not run_id:
            return out
        spec = spec if isinstance(spec, dict) else {}
        key = spec_key(decision, spec, derive_type=derive_type, parent_key=parent_key, cut_label=cut_label)
        out['guard'] = bool(note_started(key, username=username, run_id=run_id, records=records_for(h),
                                         via='operator', now=now))
        tid = str(thread_id or '').strip()
        if tid.lower() == 'active':
            tid = active_thread_id(h, username)
        if tid:
            from . import build_ready as _br
            s3, bucket = _attr(h, 's3_client'), _attr(h, 'S3_BUCKET', 'dashboard-inputs')

            def _put(k, doc):
                s3.put_object(Bucket=bucket, Key=k, Body=json.dumps(doc, indent=2).encode('utf-8'),
                              ContentType='application/json')
            out['launch'] = bool(_br.note_launch(_put, run_id=run_id, user=username, thread_id=tid,
                                                 subject=spec.get('name'), decision=decision, via='operator',
                                                 engine_user=engine_user or username, now=now))
    except Exception as e:                              # noqa: BLE001
        _note('operator', e)
    return out
