"""A brief drafted in the Prometheus app waits on the server until its reader approves it there (Jenna 2026-11-17).

Jenna was asked: "In the Prometheus app, a paying user still cannot
approve a priced build; they are sent to the dashboard to approve.
Should paying users be able to approve and be charged inside the app
too?" She chose: "Yes, build in-app approval for paying users".

Until this module the app had one way to start a build: a seat whose
plan covers it sent the order again and the build started in that turn.
A paying seat read its price on the brief card and "Review and approve
in the dashboard". The brief itself lived nowhere on the server: the
dashboard widget holds its draft in the tab and posts it back on
Approve, and the app holds nothing.

WHAT IS KEPT. When a reply drafts a brief for a reader in the app, the
draft is put on record under that reader and that conversation
(``STORE_PREFIX/<user>/<thread>.json``) with an id, the exact order (the
draft as the build step wrote it), what was quoted (the price the wallet
would meter for this seat, or that the plan includes it) and when. The
card the app draws carries the id; nothing else about the order goes to
the app and nothing comes back from it.

HOW IT IS APPROVED. The app's shell can only ask, so an approval is an
ask in the same conversation whose words are the card's own ("Approve
brief PB-7K3Q-X2M9"; "Dismiss brief ..."). ``turn`` reads it before the
question reaches a model:

  * the id is looked up under the reader the ask door gated and the
    conversation the ask names. An id from another reader or another
    conversation is simply not there. Nothing in the request body is
    read for the order, the price, the reader, the company or how to
    run: the order is the stored draft, the price is the stored quote.
  * the stored quote is compared with what the wallet would meter now.
    When the terms moved (a price change, a plan that no longer covers
    it, a profile that now exists) nothing starts and the reader is
    asked to draft it again. So what is charged is what was quoted.
  * the brief is claimed (a conditional write), the dashboard's own
    approve step runs on the stored draft (``_pm_approve_core``: the
    funds check and its refusal line, the plan allowance, the repeat
    approval check, the charge and its ledger entry, the launch record
    the finished line posts from), and the outcome is written back on
    the brief. A second submit of the same id answers with the first
    result; one that arrives while the first is in flight is told so.
    A claim not settled in ``CLAIM_S`` is dropped, and the approve
    step's own repeat check answers a run that did start.
  * a typed "approve" binds only when one brief is waiting in that
    conversation and it is the last thing said there; with more than
    one, or an older one, the reply asks which.

THE LIFE OF A BRIEF: ``LIFE_S``, 24 hours. It is the life a priced offer
already has in chat (``pending_offers.MAX_AGE_H``) and the window the
repeat approval check looks back over: a card left open overnight is
still good in the morning, and past a day the library, the window and
the price list may all have moved, so the brief is drafted again rather
than trusted. A brief is single use: approved, answered as a repeat,
dismissed, expired or out of date, it is closed for good.

WHO. The Prometheus app only (its own request signature, ``APP_UA``),
for a key that may build and is not a sandbox key; an admin can also
switch a key out with ``api_key_app_approve: false`` on its record. A
key that is not the app never has a brief put on record and is pointed
at the build endpoint it already uses. A probe identity leaves nothing
on record, starts nothing and is charged nothing.

WHERE THE APP STILL CANNOT. A brief that has a question open (which
person, which seasons, which parent profile) is settled in the
dashboard's card, as is one whose subject the build step could not yet
verify, and a profile that already exists has nothing to approve. Those
carry no id and the app keeps its old way out.

Nothing here raises into a request. Regression:
scripts/test_pm_app_brief_approval_2026_11_17.py.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
import time
from datetime import datetime, timezone

from . import contract_v1 as C
from . import faults as _faults
from . import partner_keys as K
from .host import host

__all__ = ['STORE_PREFIX', 'LIFE_S', 'CLAIM_S', 'APP_UA', 'APPROVABLE', 'is_app', 'parse', 'wants', 'stash', 'onto_rows',
           'turn', 'key_for', 'quote', 'approve_text', 'dismiss_text']

STORE_PREFIX = 'system/pm_pending_briefs/'
LIFE_S = 24 * 3600          # see THE LIFE OF A BRIEF above
CLAIM_S = 300               # an approval in flight: the engine post's own limit, one more try, and room
KEEP = 16                   # briefs kept per conversation
APP_UA = 'Crosswalk-Prometheus-Desktop/'
APPROVABLE = ('new_build', 'time_shifted_refresh', 'cut_needs_parent', 'derive_cut', 'subscriber_iq')

_ID_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'      # no 0, O, 1 or I
_ID = r"PB-[A-Z2-9]{4}-[A-Z2-9]{4}"
_APPROVE_ID_RX = re.compile(r"^\W*(?:approve|build|start|run)\s+(?:the\s+)?brief\s+(" + _ID + r")\W*$", re.I)
_DISMISS_ID_RX = re.compile(r"^\W*(?:dismiss|cancel|close)\s+(?:the\s+)?brief\s+(" + _ID + r")\W*$", re.I)
# the words that can only mean "approve the brief": these ask which one when the brief is not the last thing said
_EXPLICIT_RX = re.compile(
    r"^(?:yes[ ,.!]+)?(?:approved?|approve (?:it|that|this|the (?:brief|build|profile|order))|approve and (?:run|build|start)(?: it)?|"
    r"build it|start (?:it|the build)|run the build)(?:[ ,.!]+(?:please|now|thanks?))*$", re.I)
_DISMISS_RX = re.compile(r"^(?:dismiss(?: (?:it|that|this|the (?:brief|build)))?|cancel (?:the (?:brief|build)))$", re.I)

# ---------------------------------------------------------------- what the reader is told
DISMISSED = 'Dismissed. Nothing was started and nothing was charged.'
EXPIRED = ('That brief has expired, so I did not start it and nothing was charged. Ask for it again and I will draft '
           'a fresh one.')
UNKNOWN = ('I cannot find that brief in this conversation, so nothing was started and nothing was charged. Ask for '
           'the profile again and I will draft a fresh brief.')
OUT_OF_DATE = ('The terms on that brief have changed since I drafted it, so I did not start it and nothing was '
               'charged. Ask for it again and I will draft a fresh one.')
WAS_DISMISSED = ('That brief was dismissed, so nothing was started and nothing was charged. Ask for it again and I '
                 'will draft a fresh one.')
IN_FLIGHT = 'That approval is already going through. It will not be charged twice.'
NOT_HERE = ('Approving a brief is done in the Prometheus app. Through the API, start the build with '
            'POST /api/v1/profiles/run.')
NO_BUILDS = 'This key cannot start builds, so nothing was started and nothing was charged.'
SANDBOX = 'A sandbox key never bills, so it cannot start a build. Nothing was started.'
_LANDS = {'subiq': 'the Subscriber IQ tab', 'profile': 'Profile IQ under Select Profile'}


def _iso(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _squash(s):
    return ' '.join(str(s or '').split())


def _uname(user):
    u = user if isinstance(user, dict) else {}
    return str(u.get('username') or u.get('email') or '').strip()


# ---------------------------------------------------------------- who is asking
def _header(headers, name):
    try:
        for k, v in (headers or {}).items():
            if str(k).lower() == name.lower():
                return str(v or '')
    except Exception:
        pass
    return ''


def is_app(headers):
    """True when the request was made by the Prometheus app (its own
    request signature). The app is the one client that draws the brief
    card these ids go on."""
    return _header(headers, 'User-Agent').startswith(APP_UA)


def _is_probe(uname, user, headers=None):
    """A probe identity (a canary, the golden set, a replay, a sandbox
    key): nothing is put on record for it and nothing starts."""
    try:
        if _faults.is_probe():
            return True
    except Exception:
        pass
    if _header(headers, 'X-Prometheus-Caller').strip():
        return True
    if re.search(r'canary|smoke|regression|probe', _header(headers, 'User-Agent'), re.I):
        return True
    try:
        from . import probe_identity as _pi
        s3 = host.s3_client if host.has('s3_client') else None
        return bool(_pi.is_probe_user(uname, user if isinstance(user, dict) else None, s3))
    except Exception:
        return not uname


def _may_approve(user):
    """'' when this key may approve a brief in the app, else the line it
    reads. Read off the key's own record, never the request."""
    try:
        if K.is_sandbox(user):
            return SANDBOX
        if not K.has_scope(user, 'build') or (user or {}).get('api_key_app_approve') is False:
            return NO_BUILDS
    except Exception:
        return ''
    return ''


# ---------------------------------------------------------------- the words
def approve_text(bid):
    return f"Approve brief {bid}"


def dismiss_text(bid):
    return f"Dismiss brief {bid}"


def parse(text):
    """{'verb': 'approve' | 'dismiss', 'id': 'PB-....' | '', 'explicit':
    bool} when the words approve or dismiss a brief, else None. A card's
    own words carry the id; a bare word carries none."""
    t = _squash(text)
    if not t or len(t) > 120 or '?' in t:
        return None
    m = _APPROVE_ID_RX.match(t)
    if m:
        return {'verb': 'approve', 'id': m.group(1).upper(), 'explicit': True}
    m = _DISMISS_ID_RX.match(t)
    if m:
        return {'verb': 'dismiss', 'id': m.group(1).upper(), 'explicit': True}
    bare = t.strip(' .!,').lower()
    if _DISMISS_RX.match(bare):
        return {'verb': 'dismiss', 'id': '', 'explicit': True}
    if _EXPLICIT_RX.match(bare):
        return {'verb': 'approve', 'id': '', 'explicit': True}
    try:
        from . import pending_offers as _po
        if _po.is_approval(t):
            return {'verb': 'approve', 'id': '', 'explicit': False}
    except Exception:
        pass
    return None


def wants(text, headers=None):
    """True when ``turn`` should be asked about this text at all: the
    card's own words from anyone (they are never a question for a
    model), a bare word only from the app."""
    act = parse(text)
    return bool(act and (act['id'] or is_app(headers)))


# ---------------------------------------------------------------- the store
def _safe(s):
    return ''.join(c for c in str(s or '') if c.isalnum() or c in '-_.@')[:96]


def key_for(uname, thread_id):
    return f"{STORE_PREFIX}{_safe(uname)}/{_safe(thread_id)}.json"


def _code(exc):
    """(error code, http status) off a store error, '' and 0 when it carries none."""
    try:
        resp = getattr(exc, 'response', None) or {}
        return (str((resp.get('Error') or {}).get('Code') or ''),
                int((resp.get('ResponseMetadata') or {}).get('HTTPStatusCode') or 0))
    except Exception:
        return '', 0


def _missing(exc):
    code, status = _code(exc)
    text = f"{type(exc).__name__} {exc}"
    return isinstance(exc, KeyError) or code in ('NoSuchKey', '404', 'NotFound') or status == 404 \
        or any(t in text for t in ('NoSuchKey', 'NotFound'))


def _conflict(exc):
    """Another writer got there first: the conditional write was refused."""
    code, status = _code(exc)
    text = f"{type(exc).__name__} {exc}"
    return code in ('PreconditionFailed', 'ConditionalRequestConflict', '412', '409') or status in (412, 409) \
        or 'PreconditionFailed' in text or 'ConditionalRequestConflict' in text


def _read(key):
    """(doc, etag); an absent record is an empty one. Raises when the
    store cannot be read."""
    try:
        resp = host.s3_client.get_object(Bucket=host.bucket, Key=key)
    except Exception as e:                              # noqa: BLE001
        if _missing(e):
            return {'briefs': []}, None
        raise
    doc = json.loads(resp['Body'].read().decode('utf-8') or '{}')
    if not isinstance(doc, dict):
        doc = {}
    if not isinstance(doc.get('briefs'), list):
        doc['briefs'] = []
    return doc, resp.get('ETag')


_COND = {'ok': True}


def _write(key, doc, etag):
    """True when written; False when another writer got there first."""
    kwargs = {'Bucket': host.bucket, 'Key': key, 'Body': json.dumps(doc, default=str).encode('utf-8'),
              'ContentType': 'application/json'}
    if _COND['ok']:
        cond = dict(kwargs)
        if etag:
            cond['IfMatch'] = str(etag).strip('"')
        else:
            cond['IfNoneMatch'] = '*'
        try:
            host.s3_client.put_object(**cond)
            return True
        except Exception as e:                          # noqa: BLE001
            if _conflict(e):
                return False
            if 'ParamValidation' not in type(e).__name__:
                raise
            _COND['ok'] = False     # an old client library: no conditional write; the claim is then best effort
    host.s3_client.put_object(**kwargs)
    return True


def _update(key, fn, tries=6):
    """Read, ``fn(doc)`` (return None to leave it), conditional write,
    again on a conflict. Returns the doc as written, or None when left."""
    for attempt in range(tries):
        doc, etag = _read(key)
        out = fn(doc)
        if out is None:
            return None
        if _write(key, out, etag):
            return out
        time.sleep(0.05 * (attempt + 1))
    raise RuntimeError('pending brief: the record kept changing')


def _load(uname, thread_id):
    try:
        return _read(key_for(uname, thread_id))[0]
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.load', e)
        return None


def _find(doc, bid):
    for b in (doc or {}).get('briefs') or []:
        if isinstance(b, dict) and str(b.get('id') or '') == bid:
            return b
    return None


def _state(b, now):
    """'open' | 'approving' | 'done' | 'dismissed' | 'expired'."""
    st = str(b.get('state') or 'open')
    if st in ('done', 'dismissed', 'expired'):
        return st
    if st == 'approving' and now - float(b.get('claim_t') or 0) <= CLAIM_S:
        return 'approving'
    if now - float(b.get('t') or 0) > LIFE_S:
        return 'expired'
    return 'open'


def _new_id(taken):
    for _ in range(20):
        raw = ''.join(secrets.choice(_ID_ALPHABET) for _ in range(8))
        bid = f"PB-{raw[:4]}-{raw[4:]}"
        if bid not in taken:
            return bid
    raise RuntimeError('pending brief: no id')


# ---------------------------------------------------------------- the order and what it costs
def _drafts(raw):
    if not isinstance(raw, dict):
        return []
    d = raw.get('spec_drafts') or ([raw['spec_draft']] if isinstance(raw.get('spec_draft'), dict) else [])
    return [x for x in (d if isinstance(d, list) else []) if isinstance(x, dict)][:24]


def _legacy():
    from prometheus.legacy import H
    return H


def _decision(draft):
    """The decision the approve step would settle on for this draft (a
    copy is read: the stored order is never touched)."""
    d = copy.deepcopy(draft)
    try:
        return str(_legacy()._normalize_v1_decision(d)[0] or 'new_build').strip().lower()
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.decision', e)
        return str(d.get('decision') or 'new_build').strip().lower()


def _open_question(draft, decision=''):
    """True when the brief still has a question for its reader (which
    person, which seasons, which parent), or names a subject the build
    step could not yet verify (the approve step would look it up and
    might come back with a question or another subject): the dashboard's
    card walks those; the app has nowhere to ask them, and what it
    approves must be exactly what was drafted."""
    if any(str(k).startswith('ask_') and v for k, v in draft.items()):
        return True
    return decision in ('new_build', 'cut_needs_parent') and not draft.get('tu_demos')


def _billable_cuts(draft):
    import wallet as _w
    return _w.billable_cuts(draft)      # the approve step's own list


def quote(uname, user, draft, decision):
    """What approving this draft costs this seat right now:
    {'included', 'price_usd', 'label', 'allowance', 'covered'}. The
    price is the one the wallet would meter (``wallet.pull_funding``, the
    figure the approve step debits), so the card, the stored quote and
    the charge are one number; a seat with no dollar wallet reads the
    price table's. ``included`` is the one shared plan check."""
    out = {'included': False, 'price_usd': None, 'label': '', 'allowance': False, 'covered': True}
    rec, data = (user if isinstance(user, dict) else {}), {}
    try:
        data = host.load_users() or {}
        rec = (data.get('users') or {}).get(uname) or rec
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.users', e)
    usd, cuts, quarters = None, len(_billable_cuts(draft)), 0
    try:
        import wallet as _w
        quarters = len(_w.billable_quarter_reads(draft, decision))     # dated quarter reads charge with the build (2026-11-18)
        verdict = _w.pull_funding(rec, data, _w.chat_pull_type(decision), addon_cuts=cuts,
                                  quarter_reads=quarters)              # the pull the approve step names
        out['allowance'] = bool(verdict.get('allowance'))
        out['covered'] = bool(verdict.get('covered'))
        if verdict.get('paying') and float(verdict.get('usd') or 0) > 0:
            usd = round(float(verdict['usd']), 2)
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.wallet', e)
    if usd is None:
        try:
            from . import ask_outcome as _ao
            table = host.price_table_for(uname) if host.has('price_table_for') else None
            v = _ao.draft_price_usd({'decision': decision, 'addon_cuts': draft.get('addon_cuts'),
                                     'quarter_cuts': draft.get('quarter_cuts'),
                                     'price_table': table if isinstance(table, dict) else draft.get('price_table')})
            usd = round(float(v), 2) if v is not None and float(v) > 0 else None
        except Exception as e:                          # noqa: BLE001
            _faults.note('pending_briefs.table', e)
    try:
        from . import priced_read as _pr
        out['included'] = bool(_pr.included_with_plan(rec, uname, None, amount_usd=usd))
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.included', e)
    if not out['included'] and usd is not None:
        out['price_usd'] = usd
        try:
            import wallet as _w
            out['label'] = _w.money_label(usd, _w.display_currency(rec, data))
        except Exception:
            out['label'] = f"${usd:,.0f}" if abs(usd - round(usd)) < 0.009 else f"${usd:,.2f}"
    return out


def _same_terms(a, b):
    a, b = (a or {}), (b or {})
    if bool(a.get('included')) != bool(b.get('included')):
        return False
    pa, pb = a.get('price_usd'), b.get('price_usd')
    if pa is None or pb is None:
        return pa is None and pb is None
    return abs(float(pa) - float(pb)) < 0.005


def _order(draft, decision, raw=None, single=True):
    win = _squash(draft.get('date_window') or draft.get('date_window_label') or draft.get('cut_window_label')
                  or ((raw or {}).get('date_window') if single else '') or '')
    return {'subject': _squash(draft.get('subject') or draft.get('universe_label') or draft.get('name') or '')[:160],
            'decision': decision, 'window': win[:120], 'kind': 'subiq' if decision == 'subscriber_iq' else 'profile'}


def _turns(uname, thread_id):
    try:
        from . import service as _svc
        turns = _svc.load_thread(uname, thread_id)
        return turns if isinstance(turns, list) else []
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.thread', e)
        return None


def _thread_mark(uname, thread_id, turns=None):
    """How the conversation stood when the brief was drafted: a typed
    "approve" binds only while this still holds (the brief is the last
    thing said)."""
    turns = _turns(uname, thread_id) if turns is None else turns
    if turns is None:
        return None
    last = turns[-1] if turns and isinstance(turns[-1], dict) else {}
    return {'n': len(turns), 'ts': str(last.get('ts') or '')}


def _offer_open(uname, thread_id, turns):
    """True when a priced read is waiting on its own Approve in this
    conversation: a bare word said after the brief is that offer's."""
    try:
        from . import pending_offers as _po
        return bool(_po.anything_open(uname, turns or [], thread_id))
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.offer', e)
        return True


# ---------------------------------------------------------------- putting a brief on record
def stash(user, cid, raw, headers=None, *, thread_id='', question='', now=None):
    """Put the briefs a reply drafted on record for the reader and the
    conversation, and note them on the payload (``_pending_briefs``, one
    entry per drafted brief, None for one the app cannot approve) for
    the contract to draw. Returns that list; [] when nothing was put on
    record (not the app, no conversation, a probe, a key that cannot
    build). Never raises."""
    try:
        drafts = _drafts(raw)
        uname = _uname(user)
        if not drafts or not is_app(headers) or not cid or not thread_id or not uname:
            return []
        if _is_probe(uname, user, headers) or _may_approve(user):
            return []
        now = float(now or time.time())
        single = len(drafts) == 1
        waits = single and (raw.get('clarify_steps') or raw.get('chip_options'))
        mark = _thread_mark(uname, thread_id)
        new, held = [], []
        for d in drafts:
            decision = _decision(d)
            if waits or decision not in APPROVABLE or _open_question(d, decision):
                held.append(None)
                continue
            q = quote(uname, user, d, decision)
            rec = {'id': '', 'state': 'open', 't': now, 'expires': now + LIFE_S, 'decision': decision,
                   'order': _order(d, decision, raw, single), 'quote': q, 'draft': copy.deepcopy(d),
                   'question': str(question or '')[:4000], 'turns': mark}
            new.append(rec)
            held.append(rec)
        if not new:
            return []

        def _add(doc):
            keep = [b for b in doc.get('briefs') or [] if isinstance(b, dict) and now - float(b.get('t') or 0) <= LIFE_S + 6 * 3600]
            taken = {str(b.get('id')) for b in keep}
            for rec in new:
                rec['id'] = _new_id(taken)
                taken.add(rec['id'])
            doc['briefs'] = (keep + new)[-KEEP:]
            doc['user'], doc['thread_id'], doc['conversation_id'] = uname, str(thread_id), str(cid)
            return doc
        _update(key_for(uname, thread_id), _add)
        out = [None if rec is None else {'id': rec['id'], 'expires_at': _iso(rec['expires']), 'included': bool(rec['quote']['included']),
                                         'price_usd': rec['quote']['price_usd'], 'price_label': rec['quote']['label']} for rec in held]
        raw['_pending_briefs'] = out
        return out
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.stash', e)
        return []


def onto_rows(declined, raw):
    """The ids and the stored quotes onto the brief rows of a
    ``build_required`` reply, in place: ``id``, ``approve`` and
    ``dismiss`` (the words that act on it through this same door),
    ``expires_at``, and the price as it was put on record. A row with no
    id is one the app cannot approve."""
    try:
        held = (raw or {}).get('_pending_briefs')
        rows = (declined or {}).get('briefs')
        if not isinstance(held, list) or not isinstance(rows, list) or len(held) != len(rows):
            return
        moved = False
        for row, h in zip(rows, held):
            if not isinstance(h, dict) or not h.get('id'):
                continue
            moved = moved or row.get('price_usd') != h.get('price_usd') or bool(row.get('included')) != bool(h.get('included'))
            row.update({'id': h['id'], 'approve': approve_text(h['id']), 'dismiss': dismiss_text(h['id']),
                        'expires_at': h.get('expires_at'), 'included': bool(h.get('included')), 'price_usd': h.get('price_usd')})
            if h.get('price_label'):
                row['price_label'] = h['price_label']
        if all(r.get('included') for r in rows):
            declined['included'] = True
        else:
            declined.pop('included', None)
        if moved:       # the words carry the same figure the card does
            parts = []
            for r in rows:
                what = 'included with your plan' if r.get('included') else (r.get('price_label') or '')
                parts.append(f"{r.get('subject')}" + (f" ({what})" if what else ''))
            declined['text'] = 'Brief drafted: ' + '; '.join(parts) + '. Approve it to start the build.'
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.rows', e)


# ---------------------------------------------------------------- the replies
def _brief_block(b, state, closed):
    q = b.get('quote') or {}
    o = b.get('order') or {}
    return {'id': str(b.get('id') or ''), 'state': state, 'closed': bool(closed), 'subject': str(o.get('subject') or ''),
            'included': bool(q.get('included')), 'price_usd': q.get('price_usd')}


def _answer(text, cid, brief):
    v1 = C._frame('answer', None, cid, None, answer={'text': str(text), 'facts': [], 'followups': []})
    v1['brief'] = brief
    return v1


def _declined(reason, text, cid, brief=None, next_action=None):
    v1 = C.declined(reason, text, next_action=next_action, conversation_id=cid)
    if brief:
        v1['brief'] = brief
    return v1


def _gone(bid, state, text, cid):
    return _declined('unsupported', text, cid, {'id': bid, 'state': state, 'closed': True, 'subject': ''})


def _result_v1(b, cid, replayed=False):
    """The reply a closed brief stands for: its first result, again."""
    r = b.get('result') or {}
    blk = _brief_block(b, str(r.get('state') or 'closed'), True)
    v1 = _answer(r.get('text') or '', cid, blk) if r.get('kind') == 'answer' else _declined('unsupported', r.get('text') or '', cid, blk)
    if replayed:
        v1['replayed'] = True
    return v1


def started_line(b, user):
    """The short line a started build reads, in the chat's own voice."""
    o, q = (b.get('order') or {}), (b.get('quote') or {})
    name = o.get('subject') or 'Your profile'
    if q.get('included'):
        head = f"Included with your plan. {name} is building now."
    elif q.get('label'):
        head = f"Approved. {name} is building now. The price is {q['label']}, as quoted on the brief."
    else:
        head = f"Approved. {name} is building now."
    where = _LANDS.get(o.get('kind') or 'profile', _LANDS['profile'])
    if str((user or {}).get('email') or '').strip():
        return f"{head} I will email you when it is ready, and it will be in {where}."
    return f"{head} The ready notice will post in this conversation, and it will be in {where}."


def _which(briefs, cid, verb='approve'):
    n = len(briefs)
    opts = []
    for b in briefs[:8]:
        o, q = (b.get('order') or {}), (b.get('quote') or {})
        tag = '' if q.get('included') or not q.get('label') else f" ({q['label']})"
        if verb == 'dismiss':
            opts.append({'label': f"Dismiss {o.get('subject')}"[:120], 'send': dismiss_text(b['id'])})
        else:
            opts.append({'label': f"Approve {o.get('subject')}{tag}"[:120], 'send': approve_text(b['id'])})
    if n == 1:
        o = briefs[0].get('order') or {}
        q = f"One brief is waiting in this conversation: {o.get('subject')}. {'Dismiss' if verb == 'dismiss' else 'Approve'} it?"
    else:
        q = f"{n} briefs are waiting in this conversation. Which one should I {'dismiss' if verb == 'dismiss' else 'approve'}?"
    return C._frame('clarify', None, cid, None, clarify={'question': q, 'options': opts})


# ---------------------------------------------------------------- closing a brief
def _settle(key, bid, state, result=None, token=None):
    """Write a brief's end state. With ``token`` only the claim that
    holds it may; without one, only a brief nobody holds and nothing has
    closed is written. Returns the brief as written, or None."""
    box = {}

    def _fn(doc):
        b = _find(doc, bid)
        if b is None:
            return None
        if token is not None and b.get('claim') != token:
            return None
        if token is None and _state(b, time.time()) in ('done', 'dismissed', 'approving'):
            return None               # closed already, or another approval holds it: its outcome stands
        b['state'], b['done_t'] = state, time.time()
        if result is not None:
            b['result'] = result
        b.pop('draft', None)          # a closed brief keeps what was ordered and quoted, never the order itself
        b.pop('claim', None)
        box['b'] = copy.deepcopy(b)
        return doc
    try:
        _update(key, _fn)
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.settle', e)
    return box.get('b')


def _release(key, bid, token):
    """The approval did not start: the brief is open again."""
    def _fn(doc):
        b = _find(doc, bid)
        if b is None or b.get('claim') != token:
            return None
        b['state'] = 'open'
        b.pop('claim', None)
        b.pop('claim_t', None)
        return doc
    try:
        _update(key, _fn)
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.release', e)


def _claim(key, bid, token, now):
    """Take the brief for one approval. Returns (claimed, the brief as it
    stands)."""
    box = {'ok': False, 'b': None}

    def _fn(doc):
        b = _find(doc, bid)
        box['ok'], box['b'] = False, (copy.deepcopy(b) if b else None)
        if b is None or _state(b, now) != 'open':
            return None
        b['state'], b['claim'], b['claim_t'] = 'approving', token, now
        box['ok'] = True
        return doc
    _update(key, _fn)
    return box['ok'], box['b']


def _unpack(resp):
    from . import service as _svc
    return _svc._unpack(resp)


def _core():
    from prometheus.legacy import C as _chat
    return _chat._pm_approve_core


def _persist(uname, thread_id, said, reply):
    """The approval and its reply go on the conversation, so it reads
    whole when it is opened again."""
    try:
        if not (uname and thread_id and said and reply):
            return
        from . import service as _svc
        now = _svc._now()
        _svc.save_thread(uname, thread_id, [{'role': 'user', 'text': str(said), 'ts': now},
                                            {'role': 'agent', 'text': str(reply), 'ts': now, 'meta': {'kind': 'answer', 'surface': 'approve'}}])
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.persist', e)


def _text_of(v1):
    for blk, field in (('answer', 'text'), ('declined', 'text'), ('clarify', 'question')):
        b = v1.get(blk)
        if isinstance(b, dict) and str(b.get(field) or '').strip():
            return str(b[field]).strip()
    return ''


# ---------------------------------------------------------------- the turn
def turn(user, cid, text, headers=None, *, thread_id='', now=None, core=None):
    """The v1 reply to an ask that approves or dismisses a brief, or
    None when the ask is not one (it then goes on as any question).

    ``user`` is the reader the ask door gated; ``cid`` and ``thread_id``
    are the conversation the ask names. Nothing else is read from the
    request: the order and its price are the stored ones. Never raises."""
    try:
        act = parse(text)
        if act is None:
            return None
        uname = _uname(user)
        app = is_app(headers)
        now = float(now or time.time())
        if act['id']:
            if not app:
                return _declined('unsupported', NOT_HERE, cid, None, {'action': 'build', 'endpoint': C.BUILD_ENDPOINT})
            if not cid or not thread_id or not uname or _is_probe(uname, user, headers):
                return _gone(act['id'], 'unknown', UNKNOWN, cid)
            doc = _load(uname, thread_id)
            b = _find(doc, act['id']) if doc is not None else None
            if b is None:
                return _gone(act['id'], 'unknown', UNKNOWN, cid)
            said = f"{'Dismiss' if act['verb'] == 'dismiss' else 'Approve'} the brief for {(b.get('order') or {}).get('subject') or 'this profile'}"
        else:
            if not app or not cid or not thread_id or not uname or _is_probe(uname, user, headers):
                return None
            doc = _load(uname, thread_id)
            live = [x for x in (doc or {}).get('briefs') or [] if isinstance(x, dict) and _state(x, now) in ('open', 'approving')]
            if not live:
                return None
            turns = _turns(uname, thread_id)
            mark = _thread_mark(uname, thread_id, turns)
            fresh = [x for x in live if mark and x.get('turns') == mark]
            if not fresh and (not (act['explicit'] and act['verb'] == 'approve') or _offer_open(uname, thread_id, turns)):
                return None                 # a word about something said since the brief is not about the brief
            if not (len(live) == 1 and fresh):
                return _which(live, cid, act['verb'])
            b, said = live[0], _squash(text)
        key = key_for(uname, thread_id)
        v1 = _dismiss(key, b, cid, now) if act['verb'] == 'dismiss' else _approve(user, uname, key, b, cid, thread_id, now, core)
        _persist(uname, thread_id, said, _text_of(v1))
        return v1
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.turn', e)
        try:
            from . import order_truth as _ot
            return _declined('unsupported', _ot.UNCONFIRMED, cid)
        except Exception:
            return None


def _closed_reply(b, cid, st):
    """The reply for a brief that is already closed, or in flight."""
    bid = str(b.get('id') or '')
    if st == 'done':
        return _result_v1(b, cid, replayed=True)
    if st == 'dismissed':
        return _declined('unsupported', WAS_DISMISSED, cid, _brief_block(b, 'dismissed', True))
    if st == 'expired':
        return _declined('unsupported', EXPIRED, cid, _brief_block(b, 'expired', True))
    if st == 'approving':
        return _declined('unsupported', IN_FLIGHT, cid, _brief_block(b, 'approving', False))
    return _gone(bid, 'unknown', UNKNOWN, cid)


def _dismiss(key, b, cid, now):
    bid = str(b.get('id') or '')
    st = _state(b, now)
    if st == 'expired' and str(b.get('state')) != 'expired':
        _settle(key, bid, 'expired')
    if st != 'open':
        return _closed_reply(b, cid, st)
    box = {}

    def _fn(doc):
        cur = _find(doc, bid)
        box['st'] = _state(cur, now) if cur else 'gone'
        if cur is None or box['st'] != 'open':
            box['b'] = copy.deepcopy(cur) if cur else None
            return None
        cur['state'], cur['done_t'] = 'dismissed', now
        cur.pop('draft', None)
        return doc
    _update(key, _fn)
    if box.get('st') != 'open':
        return _closed_reply(box.get('b') or b, cid, box.get('st'))
    return _answer(DISMISSED, cid, _brief_block(b, 'dismissed', True))


def _approve(user, uname, key, b, cid, thread_id, now, core=None):
    bid = str(b.get('id') or '')
    st = _state(b, now)
    if st == 'expired' and str(b.get('state')) != 'expired':
        _settle(key, bid, 'expired')
    if st != 'open':
        return _closed_reply(b, cid, st)
    open_blk = _brief_block(b, 'open', False)
    # the gates the dashboard's approve route runs, in its order; none of them closes the brief
    why = _may_approve(user)
    if why:
        return _declined('scope', why, cid, open_blk)
    try:
        gate = host.funds_gate(user)
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.funds_gate', e)
        gate = None
    if gate is not None:
        data, _s = _unpack(gate)
        return _declined('insufficient_funds', str(data.get('reply') or data.get('error') or 'Your account cannot cover this run right now. Top up to keep going.'),
                         cid, open_blk, {'action': 'top_up', 'endpoint': C.TOP_UP_ENDPOINT})
    if not host.gate_pull(user):
        data, _s = _unpack(host.gate_refusal('pull'))
        return _declined('scope', str(data.get('error') or NO_BUILDS), cid, open_blk)
    from . import order_truth as _ot
    if not (host.has('queue_secret') and host.queue_secret and host.has('queue_url') and host.queue_url):
        return _declined('unsupported', _ot.NOT_STARTED, cid, open_blk)
    draft = b.get('draft')
    if not isinstance(draft, dict):
        return _gone(bid, 'unknown', UNKNOWN, cid)
    # what is charged is what was quoted: the same order, on the same terms, or nothing starts
    decision = _decision(draft)
    q_now = quote(uname, user, draft, decision)
    q_was = b.get('quote') or {}
    ran_out = bool(q_was.get('included')) and not q_now['included'] and q_now['allowance'] and not q_now['covered']
    if decision != b.get('decision') or (not _same_terms(q_was, q_now) and not ran_out):
        done = _settle(key, bid, 'done', {'state': 'out_of_date', 'kind': 'declined', 'text': OUT_OF_DATE})
        return _result_v1(done or dict(b, result={'state': 'out_of_date', 'kind': 'declined', 'text': OUT_OF_DATE}), cid)
    token = secrets.token_hex(8)
    claimed, cur = _claim(key, bid, token, now)
    if not claimed:
        return _closed_reply(cur or b, cid, _state(cur, now) if cur else 'gone')
    body = {'spec_draft': copy.deepcopy(draft), 'run_avid': bool(draft.get('run_avid', True)),
            'email_to': str((user or {}).get('email') or '').strip(), 'thread_id': str(thread_id),
            'prompt': str(b.get('question') or '')}
    try:
        data, status = _unpack((core or _core())(user, body))
    except Exception as e:                              # noqa: BLE001
        _faults.note('pending_briefs.core', e)
        _release(key, bid, token)
        return _declined('unsupported', _ot.UNCONFIRMED, cid, open_blk)
    name = (b.get('order') or {}).get('subject') or 'That one'
    if data.get('success') and data.get('run_id'):
        result = {'state': 'started', 'kind': 'answer', 'text': started_line(b, user), 'run_id': str(data.get('run_id'))[:80]}
    elif data.get('already'):
        result = {'state': 'already_delivered' if str(data.get('already')) == 'delivered' else 'already_running', 'kind': 'answer',
                  'text': str(data.get('error') or '')}
    elif data.get('success') and data.get('reused_existing'):
        result = {'state': 'already_delivered', 'kind': 'answer',
                  'text': f"{name} is already in your library. It is in Profile IQ under Select Profile, and there is nothing more to approve."}
    else:
        _release(key, bid, token)
        text = str(data.get('error') or data.get('reply') or _ot.UNCONFIRMED)
        if status == 402 or data.get('top_up_url') or data.get('no_funds'):
            return _declined('insufficient_funds', text, cid, open_blk, {'action': 'top_up', 'endpoint': C.TOP_UP_ENDPOINT})
        return _declined('unsupported', text, cid, open_blk)
    done = _settle(key, bid, 'done', result, token)
    return _result_v1(done or dict(b, result=result), cid)
