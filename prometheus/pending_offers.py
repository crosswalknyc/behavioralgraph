"""Pending priced offers: a typed approval is the chip (2026-10-22).

Jenna, verbatim: "if someone writes approve instead of hitting a chip
that should work too." Casey Pearson (Paramount+) received the priced
offer "I can pull that read on ... Pulling this data will cost $100."
with Approve / Cancel, typed Approve, and read "Nothing is waiting on
an approval right now" twice. The offer lived only in the widget's
armed state (``_pmPanelOffer``), which a thread switch, a history
re-adoption or a reload drops, and the dashboard client persists its
own threads, so the stored agent turn carried the chips and not the
offer. The server had nothing to bind a typed approval to.

The offer is now a first-class pending item on the server:

  * every priced offer a lane produces is recorded here under the
    user (``system/pm_pending_offers/<user>.json``: the question, the
    subject, the kind, the price, the thread it was offered on), open
    until it is approved, cancelled or superseded by a newer offer;
  * a bare approval ("Approve", "approve", "approved", "yes run it",
    "go ahead", "accept", "ok do it", the chip's own label) binds to
    the most recent open offer across the user's recent threads, so a
    thread-follow switch between the offer and the approval cannot
    lose it, and runs the offered question with the same
    ``panel_confirm`` the chip sends; a bare cancel closes it with the
    nothing-was-charged line;
  * the stored thread is the fallback when the store holds nothing
    (an offer made before this module existed, or written by a client
    that persists its own turns): the last agent turn carrying the
    offer copy or the Approve / Cancel chips, with no real question
    after it, is an open offer and its question is the user turn
    before it;
  * "Nothing is waiting on an approval" is correct only when no offer
    (and no brief) is open anywhere in the user's recent threads.

Never raises into a request; every S3 touch is fail-safe.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from .host import host
from . import faults as _faults

STORE_PREFIX = 'system/pm_pending_offers/'
MAX_AGE_H = 24          # an offer older than this is stale, never approved by a bare word
KEEP = 30               # offers kept per user
SCAN_THREADS = 6        # other recent threads the fallback reads

_NO_PENDING_RX = re.compile(r"^\s*Nothing is waiting on (?:an approval|a yes)\b", re.I)
_DECLINED_RX = re.compile(r"^\s*(?:No problem - nothing was charged|Closed\.)", re.I)
_OFFER_CHIP_LABELS = ('approve', 'cancel', 'accept', 'decline')

DECLINE_REPLY = 'No problem - nothing was charged. Ask me anything else when ready.'


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(ts):
    try:
        s = str(ts or '').strip().replace('Z', '+00:00')
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def _fresh(ts, now=None, max_age_h=MAX_AGE_H):
    d = _parse(ts)
    if d is None:
        return True            # a record with no stamp is judged by its position, not its age
    now = now or datetime.now(timezone.utc)
    return (now - d).total_seconds() <= max_age_h * 3600


def _safe(uname):
    return ''.join(c for c in str(uname or '') if c.isalnum() or c in '-_.@')[:80]


def key_for(uname):
    return f"{STORE_PREFIX}{_safe(uname)}.json"


def _load(uname):
    try:
        doc = host.s3_json(key_for(uname), {}) if host.has('s3_json') else {}
    except Exception as e:
        _faults.note('pending_offers.load', e)
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault('offers', [])
    return doc


def _save(uname, doc):
    try:
        doc['offers'] = list(doc.get('offers') or [])[-KEEP:]
        if host.has('s3_put_json'):
            host.s3_put_json(key_for(uname), doc)
            return True
    except Exception as e:
        _faults.note('pending_offers.save', e)
    return False


# ------------------------------------------------------------------ words
_APPROVE_WORD = (r"(?:approve[ds]?|accept(?:ed)?|yes|yeah|yep|yup|ok(?:ay)?|sure|go ahead|run it|do it|"
                 r"charge (?:it|me)|proceed|confirm(?:ed)?|approve it|run that|go for it|let'?s do it|sounds good)")
_APPROVE_RX = re.compile(
    r"^(?:" + _APPROVE_WORD + r")(?:[ ,.!]+(?:" + _APPROVE_WORD + r"|please|now|thanks?|go|and run it))*$", re.I)
_LEGACY_RUN_CHIP_RX = re.compile(r"^(?:run the full read|run the breakdown)\b", re.I)


def is_approval(text):
    """A bare approval: the chip's label typed, or the words that mean
    it ("Approve", "approved", "yes run it", "go ahead", "accept", "ok
    do it"). An approval that names something ("approve the run on
    Shannen Doherty") is not bare and is not this."""
    t = ' '.join(str(text or '').split()).strip(' .!,')
    if not t or len(t) > 80 or '?' in t:
        return False
    low = t.lower()
    if low.startswith('approve ('):           # the staff chip label "Approve ($100, not billed ...)"
        return True
    if _LEGACY_RUN_CHIP_RX.match(low):          # the chips the offer carried before 2026-10-21
        return True
    return bool(_APPROVE_RX.match(low))


_CANCEL_RX = re.compile(
    r"^(?:cancel(?: (?:that|it))?|decline[d]?|no(?: thanks?| thank you)?|nope|not now|never ?mind|skip(?: it)?|pass|stop|forget it)$", re.I)


def is_cancel(text):
    """A bare cancel: the chip's label typed, or the words that mean it.
    "No, I meant Netflix" is a correction, not this."""
    t = ' '.join(str(text or '').split()).strip(' .!,')
    if not t or len(t) > 60 or '?' in t:
        return False
    return bool(_CANCEL_RX.match(t.lower()))


def _offer_turn(turn):
    """True when an agent turn is a priced offer: it carries the offer
    body, the Approve / Cancel chips, or the offer copy."""
    if not isinstance(turn, dict) or str(turn.get('role') or '') != 'agent':
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    if isinstance(meta.get('panel_offer'), dict):
        return True
    labels = []
    for o in (meta.get('options') or turn.get('options') or []):
        lbl = str((o.get('send') or o.get('label')) if isinstance(o, dict) else o or '').strip().lower()
        if lbl:
            labels.append(lbl.split(' (')[0])
    if labels and all(l in _OFFER_CHIP_LABELS for l in labels) and any(l in ('approve', 'accept') for l in labels):
        return True
    try:
        from . import priced_read as _pr
        return bool(_pr._OFFER_COPY_RX.search(str(turn.get('text') or '')))
    except Exception:
        return bool(re.search(r"Pulling this data will cost", str(turn.get('text') or '')))


def _is_real_ask(text):
    """A user turn that is a new question or order, not an approval,
    a cancel, or a bare acknowledgement."""
    t = ' '.join(str(text or '').split())
    if not t:
        return False
    if is_approval(t) or is_cancel(t):
        return False
    return len(t.split()) >= 3 or '?' in t


# ------------------------------------------------------------------ record
def record(uname, tid, raw, question, ts=None):
    """Keep the offer a lane just produced. ``raw`` is the lane payload
    carrying ``panel_offer``. Returns the offer id ('' when nothing to
    keep). A newer offer supersedes every older open one on the same
    thread (the chips a reader can see are the newest ones)."""
    try:
        po = (raw or {}).get('panel_offer') if isinstance(raw, dict) else None
        if not isinstance(po, dict) or not uname:
            return ''
        doc = _load(uname)
        oid = uuid.uuid4().hex[:12]
        now = ts or _now()
        for o in doc['offers']:
            if o.get('status') == 'open' and str(o.get('thread_id') or '') == str(tid or ''):
                o['status'] = 'superseded'
                o['closed'] = now
        doc['offers'].append({
            'id': oid, 'thread_id': str(tid or ''), 'ts': now, 'status': 'open',
            'question': str(po.get('question') or question or '')[:600],
            'subject': str(po.get('subject') or (raw or {}).get('subject') or '')[:120],
            'kind': str(po.get('kind') or ''), 'frame': str(po.get('frame') or ''),
            'dimension': str(po.get('dimension') or ''),
            'years': po.get('years') or 1,
            'price_label': str(po.get('price_label') or ''),
        })
        _save(uname, doc)
        return oid
    except Exception as e:
        _faults.note('pending_offers.record', e)
        return ''


def close(uname, oid, status='approved', ts=None):
    try:
        if not uname or not oid:
            return False
        doc = _load(uname)
        hit = False
        for o in doc['offers']:
            if o.get('id') == oid:
                o['status'] = status
                o['closed'] = ts or _now()
                hit = True
        if hit:
            _save(uname, doc)
        return hit
    except Exception as e:
        _faults.note('pending_offers.close', e)
        return False


# ------------------------------------------------------------------ find
def open_in_history(history, ctx=None, now=None):
    """The open priced offer in one thread's turns, reconstructed from
    the turns themselves: the last offer turn, with no real question
    after it (an approval the server missed and the no-pending reply it
    drew do not close it; a cancel does). None when nothing is open."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for i in range(len(turns) - 1, -1, -1):
        t = turns[i]
        if str(t.get('role') or '') == 'user':
            txt = str(t.get('text') or t.get('content') or '')
            if _is_real_ask(txt):
                return None            # the reader moved on; nothing older is approvable
            continue
        if not _offer_turn(t):
            # an agent turn after the offer that is neither the no-pending
            # line nor the decline line answered something: the offer is done
            txt = str(t.get('text') or '')
            if _NO_PENDING_RX.match(txt):
                continue
            if _DECLINED_RX.match(txt):
                return None
            return None
        if not _fresh(t.get('ts'), now):
            return None
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        po = meta.get('panel_offer') if isinstance(meta.get('panel_offer'), dict) else {}
        # a cancel typed after the offer closed it
        for u in turns[i + 1:]:
            if str(u.get('role') or '') == 'user' and is_cancel(str(u.get('text') or u.get('content') or '')):
                return None
        question = str(po.get('question') or '').strip()
        if not question:
            for u in reversed(turns[:i]):
                if str(u.get('role') or '') == 'user':
                    question = str(u.get('text') or u.get('content') or '').strip()
                    break
        if not question:
            return None
        offer = {'id': '', 'thread_id': '', 'ts': str(t.get('ts') or ''), 'status': 'open',
                 'question': question[:600], 'subject': str(po.get('subject') or '')[:120],
                 'kind': str(po.get('kind') or ''), 'frame': str(po.get('frame') or ''),
                 'dimension': str(po.get('dimension') or ''), 'years': po.get('years') or 1,
                 'price_label': str(po.get('price_label') or ''), 'source': 'history'}
        if not offer['subject'] or not offer['kind']:
            _fill_from_question(offer, turns[:i], ctx)
        return offer
    return None


def _fill_from_question(offer, history_before, ctx):
    """Subject and kind for an offer the thread kept only as chips."""
    try:
        from . import priced_read as _pr
        hit = _pr.classify(offer['question'], history_before, ctx)
        if hit and not offer.get('kind'):
            offer['kind'] = str(hit.get('kind') or '')
            offer['frame'] = offer.get('frame') or str(hit.get('frame') or '')
        if not offer.get('subject'):
            offer['subject'] = str(_pr.bound_subject(offer['question'], history_before, ctx,
                                                     fallback=_pr._page_name(ctx)) or '')[:120]
    except Exception as e:
        _faults.note('pending_offers.fill', e)


def find(uname, history=None, tid=None, ctx=None, now=None):
    """The most recent open priced offer for this user: the store first
    (the current thread's open offer, else the newest open offer on any
    recent thread), then the stored threads read back (the current one,
    then the others updated inside the window). None when nothing is
    open anywhere."""
    now = now or datetime.now(timezone.utc)
    # the current thread's own turns first: what the reader is looking at
    hit = open_in_history(history, ctx, now)
    if hit:
        stored = _store_match(uname, hit, tid)
        if stored:
            return stored
        hit['thread_id'] = str(tid or '')
        return hit
    doc = _load(uname)
    opens = [o for o in doc.get('offers') or [] if o.get('status') == 'open' and _fresh(o.get('ts'), now)]
    opens.sort(key=lambda o: str(o.get('ts') or ''), reverse=True)
    for o in opens:
        if str(o.get('thread_id') or '') == str(tid or ''):
            return dict(o, source='store')
    # the store across threads (the thread-follow switched between the
    # offer and the approval), then the other recent threads' turns
    if opens:
        return dict(opens[0], source='store')
    try:
        from . import service as _svc
        idx = _svc.threads_index(uname)
        others = sorted([t for t in (idx.get('threads') or []) if t.get('id') and t.get('id') != tid],
                        key=lambda t: str(t.get('updated') or ''), reverse=True)[:SCAN_THREADS]
        for th in others:
            if not _fresh(th.get('updated'), now):
                continue
            turns = _svc.load_thread(uname, th['id'])
            found = open_in_history(turns, ctx, now)
            if found:
                found['thread_id'] = str(th['id'])
                return found
    except Exception as e:
        _faults.note('pending_offers.scan', e)
    return None


def _store_match(uname, hit, tid):
    """The store's record for an offer the history also shows (so the
    close lands on the right id)."""
    try:
        doc = _load(uname)
        q = ' '.join(str(hit.get('question') or '').lower().split())
        for o in sorted(doc.get('offers') or [], key=lambda o: str(o.get('ts') or ''), reverse=True):
            if o.get('status') != 'open':
                continue
            if ' '.join(str(o.get('question') or '').lower().split()) == q:
                return dict(o, source='store')
    except Exception:
        pass
    return None


def confirm_body(offer):
    """The ``panel_confirm`` the chip sends, built from the offer."""
    body = {'subject': str(offer.get('subject') or ''), 'kind': str(offer.get('kind') or ''),
            'dimension': str(offer.get('dimension') or '')}
    if offer.get('years') and offer.get('years') != 1:
        body['years'] = offer['years']
    if offer.get('id'):
        body['offer_id'] = offer['id']
    return body


def resolve_reply(uname, tid, text, history, ctx=None):
    """What a bare approval or cancel means for this user right now:
    {'action': 'approve' | 'cancel', 'offer': {...}} when an offer is
    open; None when the words are not a bare approval / cancel or
    nothing is open (the caller's own no-pending reply then stands)."""
    if is_approval(text):
        action = 'approve'
    elif is_cancel(text):
        action = 'cancel'
    else:
        return None
    offer = find(uname, history, tid, ctx)
    if not offer:
        return None
    return {'action': action, 'offer': offer}


def anything_open(uname, history=None, tid=None):
    """True when an offer is open anywhere in the user's recent threads."""
    try:
        return find(uname, history, tid) is not None
    except Exception:
        return False
