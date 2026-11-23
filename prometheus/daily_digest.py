"""The morning Prometheus digest: what went wrong yesterday (Jenna 2026-11-23).

One plain email each morning, 07:00 US Pacific, to jenna@ and jessie@
only (the house failure and system recipients; never Liz, never a user),
from the house ops sender. It covers everything since the last digest
(24 hours when there was none) and sends NOTHING on a day with nothing to
report. It replaces the 18:00 UTC same-day digest of 2026-10-20, keeps
its S3 record and its entry point (migration/pm_daily_digest.py).

What it lists, each with the person, the question in short, when, and a
link that opens the chat for an admin:

  unfinished    Prometheus said "Working on it" or "On it" and nothing
                came back within 15 minutes (never, late, or with an
                error); the held-promise records; the replies that
                faulted
  misrouted     a question sent to the wrong place: the recorded answer
                class disagrees with the Understanding the ask carried
                at high confidence; an intake step that captured a
                question; a build offer on a question; "which audience"
                asked twice in one chat
  pushback      the person said the answer was wrong, corrected it, or
                asked the same thing again
  deck_failures a deck or a file that did not come back
  bounces       an email that bounced or never went out
  reversals     a charge that was reversed

The body: the counts first, the worst ten in full, then one line per
remaining item. No internal vocabulary, no dashes.

    window(now, s3)                 -> (since, until) as UTC datetimes
    collect(day=None, s3=None, ...) -> the day's document
    render(doc)                     -> the plain-text body
    send(doc, ...)                  -> SES + system/ops/pm_daily_digest/<day>.json
    main()                          -> the timer entry point

S3 is the record (the ask log, the read and deck job documents, the held
replies, the thread store, the outbound mail log, users.json); SES's own
suppression list is the record of bounces. Fail-safe: a missing prefix is
an empty list, a bad record is skipped, nothing here raises into the
timer. Offline-safe: every S3 and SES touch goes through an injected
client so the suite can hand it fakes.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import traceback
from urllib.parse import quote as _quote

BUCKET = 'dashboard-inputs'
ASK_PREFIX = 'system/usage/ask_log/'
ASK_BY_JOB_PREFIX = 'system/usage/ask_log/_by_job/'
SIGNAL_PREFIX = 'system/usage/ask_log/_signals/'
HELD_PREFIX = 'system/ops/held_replies/'
READ_PREFIX = 'system/prometheus_reads/'
DECK_PREFIX = 'system/prometheus_decks/'
THREADS_PREFIX = 'system/synth_chat_threads/'
MAIL_PREFIX = 'system/outbound_mail/'
USERS_KEY = 'system/users.json'
OUT_PREFIX = 'system/ops/pm_daily_digest/'
LAST_KEY = OUT_PREFIX + '_last.json'
RECIPIENTS = ['jenna@crosswalknyc.com', 'jessie@crosswalknyc.com']
SENDER = 'BehavioralGraph <jenna@crosswalknyc.com>'          # the house ops sender (the failure and system alerts)
DASHBOARD = str(os.environ.get('PM_DASHBOARD_URL') or 'https://dashboard.crosswalknyc.com').rstrip('/')
STALL_S = 15 * 60          # a promise with nothing behind it after this long is unfinished
WORST = 10                 # items printed in full; the rest get one line
HIGH_CONF = 0.80           # the Understanding has to be this sure before a disagreement is a misroute
_SKIP_USERS = ('', 'unknown', 'canary', 'replay')
_HELD_OUTCOMES = ('held', 'faulted', 'promised')
# an ask with a job behind it is a promise still open only while the job
# is in flight; a settled outcome (landed, superseded by a correction,
# answered by one, declined, cancelled) is never listed (2026-10-22:
# Alexia's superseded 14:47 ask read as a held promise)
_IN_FLIGHT = ('', 'promised', 'held', 'working', 'pending', 'running', 'queued', 'started')
_LANDED = ('done', 'landed', 'delivered', 'ready', 'complete', 'completed', 'finished')
_FAILED = ('error', 'failed', 'faulted', 'cancelled', 'canceled')
_QUESTION_CLASSES = ('read', 'page_analysis', 'sizing', 'catalog', 'definition', 'provenance', 'capability',
                     'pricing', 'wayfinding', 'insight', 'status', 'multi_ask')
_PROMISE_RX = re.compile(r"^\s*(?:working on it|on it)\b", re.I)
_WHICH_RX = re.compile(r"\bwhich (?:audience|subject|campaign|profile|title) (?:should|is|do|did|would)\b", re.I)
_PUSHBACK_RX = re.compile(
    r"(?:\b(?:that|this|it)(?:'s| is| was)? (?:wrong|incorrect|not right|not correct|not what i (?:asked|meant|wanted|said))\b"
    r"|\bnot what i (?:asked|meant|wanted|said)\b|\byou (?:got|read|have) (?:it|this|that|the \w+) wrong\b"
    r"|\bwrong (?:answer|number|subject|audience|title|profile|campaign|dates?|window|run)\b"
    r"|\bstill wrong\b|\btry again\b|\bi (?:asked|said|meant|gave) (?:for |you )?(?:the |a )?(?:\w+ ){0,3}(?:not|wrong|different)\b"
    r"|\bi had given the wrong\b|\bincorrect\b|\bthat did not answer\b|\byou did not answer\b|\bdidn'?t answer\b"
    r"|\bthis is for\b.*\bnot\b|\bno[,.!]? (?:i|we) (?:asked|said|meant|want|wanted|need)\b)", re.I)
_INTERNAL_RX = re.compile(
    r"\b(?:synth\w*|modeled|modelled|hostmap|clickstream|panelists?|tier\s*[12]|gen\s*pop|claude|anthropic|openai|gpt\w*|"
    r"pipeline|lanes?|workers?|queue|hetzner|replay|router|routed|routing|regex|enforcers?|s3|json)\b", re.I)
# plain words for the classes the ask log records (prometheus.intent_classes):
# what Prometheus did with the message, and what the message was
_PLAIN_ROUTE = {
    'wizard': 'an intake step', 'build': 'a build offer', 'read': 'a read', 'page_analysis': 'an answer on the open page',
    'multi_ask': 'several answers at once', 'deck': 'a deck', 'status': 'a status check', 'wayfinding': 'directions to a tab',
    'dashboard_issue': 'a dashboard problem report', 'capability': 'a can-we answer', 'pricing': 'a price', 'sizing': 'an audience size',
    'send_file': 'a file send', 'insight': 'an insight answer', 'catalog': 'a library lookup', 'definition': 'a definition',
    'provenance': 'a note on where a number came from', 'offer': 'a priced offer', 'short_reply': 'a short reply',
    'continuation': 'a follow-up on the last answer', 'data_floor': 'a note that the date is before 2021', 'cancel': 'a cancel',
    'order': 'an order', 'other': 'something else',
}
_PLAIN_INTENT = {
    'wizard': 'an intake request', 'build': 'a build request', 'read': 'a data question', 'page_analysis': 'a question about the open page',
    'multi_ask': 'several questions at once', 'deck': 'a deck request', 'status': 'a status question', 'wayfinding': 'a where-is-it question',
    'dashboard_issue': 'a dashboard problem', 'capability': 'a can-we question', 'pricing': 'a price question', 'sizing': 'a how-big question',
    'send_file': 'a file request', 'insight': 'an insight question', 'catalog': 'a do-we-have-it question',
    'definition': 'a what-does-this-mean question', 'provenance': 'a where-did-this-number-come-from question', 'offer': 'an answer to a priced offer',
    'short_reply': 'a short reply', 'continuation': 'a correction or follow-up on the last answer', 'data_floor': 'a question about a date before 2021',
    'cancel': 'a cancel', 'order': 'an order', 'other': 'something else',
}
_SEVERITY = {'unfinished': 3, 'reversal': 3, 'deck_failure': 3, 'bounce': 2, 'correction': 2, 'misrouted': 2,
             'wizard_captured': 2, 'build_offered': 2, 'which_twice': 1, 'repeat': 1}


# ----------------------------------------------------------------- time
def _pt():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo('America/Los_Angeles')
    except Exception:
        return _dt.timezone(_dt.timedelta(hours=-8))


def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(v):
    """A UTC datetime from an ISO string, an epoch (seconds or ms, number
    or string), or None."""
    if v is None or v == '':
        return None
    if isinstance(v, _dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)
    try:
        f = float(v)
        if f > 1e12:
            f /= 1000.0
        if f > 1e8:
            return _dt.datetime.fromtimestamp(f, _dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        s = str(v).strip().replace('Z', '+00:00')
        d = _dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except Exception:
        return None


def _iso(d):
    return d.astimezone(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if d else ''


def _in(d, since, until):
    return d is not None and since <= d < until


def _when(d):
    """'Sun Nov 22, 8:14 PM PT' for a reader in Pacific time."""
    if not d:
        return ''
    p = d.astimezone(_pt())
    return p.strftime('%a %b %-d, %-I:%M %p PT')


def _day_label(d):
    return d.astimezone(_pt()).strftime('%a %b %-d') if d else ''


def _days_between(since, until):
    """The UTC day prefixes the window touches."""
    out, d = [], since.date()
    while d <= until.date():
        out.append(d.strftime('%Y-%m-%d'))
        d += _dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------- S3
def _list(s3, prefix, cap=20000):
    keys, tok = [], None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=prefix)
        if tok:
            kw['ContinuationToken'] = tok
        try:
            r = s3.list_objects_v2(**kw)
        except Exception:
            return keys
        for o in r.get('Contents', []) or []:
            k = str(o.get('Key') or '')
            if k.endswith('.json'):
                keys.append(k)
        if not r.get('IsTruncated') or len(keys) >= cap:
            return keys
        tok = r.get('NextContinuationToken')


def _get(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8', 'replace'))
    except Exception:
        return None


def _live_user(u):
    """A real person: the one predicate every surface shares
    (prometheus.probe_identity). canary:*, the sandbox key's owner, the
    smoke and the graders never make the digest."""
    u = str(u or '')
    if u in _SKIP_USERS or u.startswith('canary'):
        return False
    try:
        from . import probe_identity as _pid
        return _pid.live(u)
    except Exception:
        return True


def window(now=None, s3=None, day=None):
    """(since, until): from the last digest's end (24 hours when there is
    none, never more than 48 or less than 6) up to now. ``day`` pins a UTC
    calendar day instead (the old same-day shape the suites use)."""
    if day:
        d0 = _dt.datetime.strptime(str(day)[:10], '%Y-%m-%d').replace(tzinfo=_dt.timezone.utc)
        return d0, d0 + _dt.timedelta(days=1)
    until = now or _utcnow()
    since = None
    if s3 is not None:
        last = _get(s3, LAST_KEY)
        if isinstance(last, dict):
            since = _parse_ts(last.get('until'))
    if since is None or not (_dt.timedelta(hours=6) <= until - since <= _dt.timedelta(hours=48)):
        since = until - _dt.timedelta(hours=24)
    return since, until


# ------------------------------------------------------------- the items
def _clean(s, n=220):
    s = ' '.join(str(s or '').split())
    s = _INTERNAL_RX.sub('', s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    s = s.replace('\u2014', '-').replace('\u2013', '-')
    return (s[:n].rstrip() + '...') if len(s) > n else s


_THREAD_ID_RX = re.compile(r"'id':\s*'([A-Za-z0-9_-]+)'")


def _thread_of(rec):
    """The chat an ask record belongs to. The record keeps extra.thread as a
    small dict; the log writer stores a dict as its text, so both shapes
    are read. A thread entry that only names a subject has no id."""
    ex = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
    t = ex.get('thread')
    if isinstance(t, dict):
        return str(t.get('id') or '')
    t = str(t or '')
    if not t:
        return ''
    if t.startswith('{'):
        m = _THREAD_ID_RX.search(t)
        return m.group(1) if m else ''
    return t if re.fullmatch(r'[A-Za-z0-9_-]+', t) else ''


def chat_link(user, thread_id):
    """The admin's read-only view of one person's chat (prometheus.legacy
    .threads, super admins only). Empty when the thread is unknown."""
    if not user or not thread_id:
        return ''
    return f"{DASHBOARD}/admin/prometheus/chat/{_quote(str(user), safe='')}/{_quote(str(thread_id), safe='')}"


def _item(kind, when, user, question, what, thread='', job_id='', reply='', outcome='', label='asked'):
    return {'kind': kind, 'severity': _SEVERITY.get(kind, 1), 'ts': _iso(when) if isinstance(when, _dt.datetime) else str(when or ''),
            'user': str(user or ''), 'question': _clean(question), 'what': what, 'thread': thread or '',
            'link': chat_link(user, thread), 'job_id': job_id or '', 'reply': _clean(reply, 160), 'outcome': outcome or '', 'label': label}


def _u_of(rec):
    """(intent, confidence) the ask carried: the Understanding first, the
    Reader's reading when that is all the record has."""
    ex = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
    try:
        from . import understanding as _un
        c = str(ex.get('u') or '')
        if c:
            p = _un.parse_compact(c)
            if p.get('intent'):
                return str(p.get('intent')), float(p.get('conf') or 0.0)
    except Exception:
        pass
    rd = ex.get('reading') if isinstance(ex.get('reading'), dict) else None
    if rd and rd.get('intent'):
        try:
            return str(rd.get('intent')), float(rd.get('confidence') or 0.0)
        except (TypeError, ValueError):
            return str(rd.get('intent')), 0.0
    return '', 0.0


def _ask_records(s3, since, until):
    out = []
    for day in _days_between(since, until):
        for k in _list(s3, f'{ASK_PREFIX}{day}/'):
            rec = _get(s3, k)
            if not isinstance(rec, dict) or not rec.get('question') or not _live_user(rec.get('user')):
                continue
            if _in(_parse_ts(rec.get('ts')), since, until):
                rec['_key'] = k
                out.append(rec)
    out.sort(key=lambda r: str(r.get('ts') or ''))
    return out


def _job_doc(s3, rec):
    jid = str(rec.get('job_id') or '').strip()
    if not jid:
        return None
    try:
        from . import intent_classes as _ic
        cls = _ic.class_of(rec.get('route'), rec.get('outcome'), rec.get('surface'))
    except Exception:
        cls = ''
    first, second = (DECK_PREFIX, READ_PREFIX) if cls == 'deck' else (READ_PREFIX, DECK_PREFIX)
    return _get(s3, f'{first}{jid}.json') or _get(s3, f'{second}{jid}.json')


def _unfinished(s3, recs, since, until, out):
    seen = set()
    try:
        from . import intent_classes as _ic
    except Exception:
        _ic = None
    for r in recs:
        t = _parse_ts(r.get('ts'))
        oc = str(r.get('outcome') or '')
        tid, jid = _thread_of(r), str(r.get('job_id') or '')
        key = (r.get('user'), tid or jid or r.get('ts'))
        cls = _ic.class_of(r.get('route'), r.get('outcome'), r.get('surface')) if _ic else ''
        if cls in ('deck', 'send_file'):
            continue   # a deck or a file that did not come back is listed once, by the deck scan
        if cls in ('build', 'order', 'wizard', 'offer') and oc not in ('faulted', 'error'):
            continue   # a profile build runs for a while by design and has its own failure mail; an intake step promises no answer
        if oc in ('faulted', 'error'):
            out.append(_item('unfinished', t, r.get('user'), r.get('question'),
                             'The reply did not come back; it stopped with an error.', tid, jid, r.get('reply'), oc))
            seen.add(key)
            continue
        if jid or oc in _IN_FLIGHT:
            doc = _job_doc(s3, r) if jid else None
            status = str((doc or {}).get('status') or '').lower()
            landed_at = _parse_ts((doc or {}).get('finished_at')) or _parse_ts(r.get('settled_at'))
            started = _parse_ts((doc or {}).get('started_at')) or t
            if status in _FAILED:
                out.append(_item('unfinished', t, r.get('user'), r.get('question'),
                                 'Prometheus said it was working on it; the answer stopped with an error.', tid, jid, r.get('reply'), status))
                seen.add(key)
            elif status in _LANDED or (not doc and oc in ('landed', 'answered', 'superseded', 'declined', 'cancelled', 'charged')):
                if landed_at and started and (landed_at - started).total_seconds() > STALL_S:
                    mins = int((landed_at - started).total_seconds() // 60)
                    out.append(_item('unfinished', t, r.get('user'), r.get('question'),
                                     f'Prometheus said it was working on it; the answer came {mins} minutes later.', tid, jid, r.get('reply'), 'late'))
                    seen.add(key)
            elif (jid or oc in _HELD_OUTCOMES) and t and (until - t).total_seconds() >= STALL_S:
                out.append(_item('unfinished', t, r.get('user'), r.get('question'),
                                 'Prometheus said it was working on it and nothing came back.', tid, jid, r.get('reply'), oc or status or 'promised'))
                seen.add(key)
    for day in _days_between(since, until):
        for k in _list(s3, f'{HELD_PREFIX}{day}/'):
            h = _get(s3, k)
            if not isinstance(h, dict) or not _live_user(h.get('user')):
                continue
            opened = _parse_ts(h.get('opened_at'))
            if not _in(opened, since, until):
                continue
            is_open = str(h.get('status') or 'open') == 'open'
            told = str(h.get('told') or '')
            if not (is_open or _PROMISE_RX.match(told) or str(h.get('kind') or '') in ('promise', 'read_failed', 'client')):
                continue
            how = ''
            d = h.get('delivered')
            if isinstance(d, dict) and d.get('how'):
                how = _clean(d.get('how'), 90).rstrip('.')
                how = ' ' + how[:1].upper() + how[1:] + '.'
            what = ('Prometheus said it was working on it and nothing came back; still open.' if is_open
                    else 'Prometheus said it was working on it and nothing came back on its own.' + how)
            tid = str(h.get('thread') or '')
            question = str(h.get('question') or '').strip()
            if not question or not tid:
                before = [r for r in recs if str(r.get('user') or '') == str(h.get('user') or '') and (_parse_ts(r.get('ts')) or until) <= opened]
                if before:
                    last = before[-1]
                    question = question or str(last.get('question') or '')
                    tid = tid or _thread_of(last)
            if not tid and h.get('job_id'):
                ptr = _get(s3, f"{ASK_BY_JOB_PREFIX}{h.get('job_id')}.json")
                ask = _get(s3, str(ptr.get('key'))) if isinstance(ptr, dict) and ptr.get('key') else None
                tid = _thread_of(ask) if isinstance(ask, dict) else ''
            if not tid:
                want = ' '.join(str(h.get('question') or '').lower().split())
                tid = next((_thread_of(r) for r in recs if str(r.get('user') or '') == str(h.get('user') or '')
                            and ' '.join(str(r.get('question') or '').lower().split()) == want and _thread_of(r)), '')
            out.append(_item('unfinished', opened, h.get('user'), question, what, tid, h.get('job_id') or h.get('id') or '', told, 'held'))


def _misrouted(recs, out):
    try:
        from . import intent_classes as _ic
    except Exception:
        return
    for r in recs:
        t = _parse_ts(r.get('ts'))
        intent, conf = _u_of(r)
        if not intent:
            continue
        tid = _thread_of(r)
        cls_route = _ic.class_of(r.get('route'), r.get('outcome'), r.get('surface'))
        cls_intent = _ic.class_of_intent(intent)
        if cls_route == 'wizard' and cls_intent in _QUESTION_CLASSES and conf >= 0.5:
            out.append(_item('wizard_captured', t, r.get('user'), r.get('question'),
                             f"A question was taken as an intake step; it reads as {_PLAIN_INTENT.get(cls_intent, 'a question')}.",
                             tid, r.get('job_id'), r.get('reply'), r.get('outcome')))
            continue
        offered = str(r.get('outcome') or '') == 'panel_offer' or cls_route in ('build', 'offer', 'order')
        if offered and cls_intent in _QUESTION_CLASSES and cls_intent not in ('read', 'multi_ask') and conf >= 0.5:
            out.append(_item('build_offered', t, r.get('user'), r.get('question'),
                             f"A question got a build offer instead of an answer; it reads as {_PLAIN_INTENT.get(cls_intent, 'a question')}.",
                             tid, r.get('job_id'), r.get('reply'), r.get('outcome')))
            continue
        if conf >= HIGH_CONF and cls_intent != 'other' and cls_route != 'other' \
                and not (_ic.near(cls_intent, cls_route) or _ic.near(cls_route, cls_intent)):
            out.append(_item('misrouted', t, r.get('user'), r.get('question'),
                             f"Handled as {_PLAIN_ROUTE.get(cls_route, 'something else')} while the message looked like {_PLAIN_INTENT.get(cls_intent, 'a question')}.",
                             tid, r.get('job_id'), r.get('reply'), r.get('outcome')))


def _pushback(s3, recs, since, until, out):
    seen = set()
    for day in _days_between(since, until):
        for k in _list(s3, f'{SIGNAL_PREFIX}{day}/'):
            sig = _get(s3, k)
            if not isinstance(sig, dict) or not _live_user(sig.get('user')):
                continue
            t = _parse_ts(sig.get('ts'))
            if not _in(t, since, until):
                continue
            reason = str(sig.get('reason') or '')
            kind = 'repeat' if reason == 'repeat' else 'correction'
            what = 'Asked the same thing again.' if kind == 'repeat' else 'The person said the answer was wrong.'
            tid = str(sig.get('thread') or '')
            out.append(_item(kind, t, sig.get('user'), sig.get('question'), what, tid, '', sig.get('prior_reply')))
            seen.add((str(sig.get('user') or ''), _iso(t)[:16]))
    try:
        from . import user_signal as _us
        grades = _us.grade_sequence(recs)
    except Exception:
        grades = []
    by_ts = {(str(r.get('user') or ''), str(r.get('ts') or '')): r for r in recs}
    for g in grades:
        nxt = _parse_ts(g.get('next_ts'))
        if not _in(nxt, since, until):
            continue
        key = (str(g.get('user') or ''), _iso(nxt)[:16])
        if key in seen:
            continue
        seen.add(key)
        kind = 'repeat' if g.get('signal') == 'repeat' else 'correction'
        what = 'Asked the same thing again.' if kind == 'repeat' else 'The person pushed back: ' + _clean(g.get('next_question'), 100)
        src = by_ts.get((str(g.get('user') or ''), str(g.get('ts') or ''))) or {}
        out.append(_item(kind, nxt, g.get('user'), g.get('question'), what, _thread_of(src), src.get('job_id'), src.get('reply')))
    return seen


def _threads(s3, recs, since, until, out, seen_push):
    """The thread store: 'which audience' asked twice in one chat, a
    pushback written in the chat, a promise with no answer after it."""
    pairs = []
    for r in recs:
        tid = _thread_of(r)
        if tid and (r.get('user'), tid) not in pairs:
            pairs.append((str(r.get('user')), tid))
    for user, tid in pairs[:200]:
        safe_u = ''.join(c for c in user if c.isalnum() or c in '-_.@').lower()
        safe_t = ''.join(c for c in tid if c.isalnum() or c in '-_')
        turns = _get(s3, f'{THREADS_PREFIX}{safe_u}/{safe_t}.json')
        if not isinstance(turns, list):
            continue
        first_q = next((str(t.get('text') or '') for t in turns if isinstance(t, dict) and t.get('role') == 'user'), '')
        which = []
        prev_role = ''
        for i, t in enumerate(turns):
            if not isinstance(t, dict):
                continue
            when = _parse_ts(t.get('ts'))
            role, text = str(t.get('role') or ''), str(t.get('text') or '')
            if role == 'user':
                if prev_role and prev_role != 'user' and _in(when, since, until) and _PUSHBACK_RX.search(text):
                    key = (user, _iso(when)[:16])
                    if key not in seen_push:
                        seen_push.add(key)
                        asked = next((str(x.get('text') or '') for x in reversed(turns[:i]) if isinstance(x, dict) and x.get('role') == 'user'), first_q)
                        out.append(_item('correction', when, user, asked, 'The person pushed back: ' + _clean(text, 100), tid))
            else:
                if _WHICH_RX.search(text) and _in(when, since, until):
                    which.append((when, text))
                if _PROMISE_RX.match(text) and _in(when, since, until) and when and (until - when).total_seconds() >= STALL_S:
                    later = [x for x in turns[i + 1:] if isinstance(x, dict) and x.get('role') != 'user'
                             and not _PROMISE_RX.match(str(x.get('text') or ''))]
                    if not later:
                        asked = next((str(x.get('text') or '') for x in reversed(turns[:i]) if isinstance(x, dict) and x.get('role') == 'user'), first_q)
                        if not any(it['kind'] == 'unfinished' and it['user'] == user and it['thread'] == tid for it in out):
                            out.append(_item('unfinished', when, user, asked, 'Prometheus said it was working on it and nothing came back in the chat.', tid, '', text))
            prev_role = role
        if len(which) >= 2:
            out.append(_item('which_twice', which[1][0], user, first_q,
                             f"Asked which audience {len(which)} times in one chat.", tid, '', which[1][1]))


def _deck_failures(s3, recs, since, until, out):
    try:
        from . import intent_classes as _ic
    except Exception:
        _ic = None
    seen = set()
    for r in recs:
        cls = _ic.class_of(r.get('route'), r.get('outcome'), r.get('surface')) if _ic else ''
        if cls not in ('deck', 'send_file'):
            continue
        oc = str(r.get('outcome') or '')
        res = str((r.get('extra') or {}).get('result') or '') if isinstance(r.get('extra'), dict) else ''
        if oc in _FAILED or oc.startswith('declined') or res in _FAILED:
            noun = 'deck' if cls == 'deck' else 'file'
            out.append(_item('deck_failure', _parse_ts(r.get('ts')), r.get('user'), r.get('question'),
                             f"The {noun} did not come back.", _thread_of(r), r.get('job_id'), r.get('reply'), oc))
            seen.add(str(r.get('job_id') or ''))
    for k in _list(s3, DECK_PREFIX, cap=2000):
        if '/' in k[len(DECK_PREFIX):]:
            continue
        d = _get(s3, k)
        if not isinstance(d, dict) or not _live_user(d.get('user')):
            continue
        jid = k[len(DECK_PREFIX):-5]
        if jid in seen:
            continue
        started = _parse_ts(d.get('started_at')) or _parse_ts(d.get('created_at')) or _parse_ts(d.get('queued_at'))
        if not _in(started, since, until):
            continue
        status = str(d.get('status') or '').lower()
        if status in _FAILED or d.get('error'):
            out.append(_item('deck_failure', started, d.get('user'), d.get('question') or d.get('title') or d.get('subject'),
                             'The deck did not come back; it stopped with an error.', str(d.get('thread_id') or ''), jid, '', status,
                             label='asked' if d.get('question') else 'deck'))
        elif status not in _LANDED and (until - started).total_seconds() >= STALL_S:
            out.append(_item('deck_failure', started, d.get('user'), d.get('question') or d.get('title') or d.get('subject'),
                             'The deck never finished.', str(d.get('thread_id') or ''), jid, '', status or 'open',
                             label='asked' if d.get('question') else 'deck'))


def _bounces(s3, ses, since, until, out):
    mails = []
    for day in _days_between(since - _dt.timedelta(days=7), until):
        for k in _list(s3, f'{MAIL_PREFIX}{day}/'):
            m = _get(s3, k)
            if isinstance(m, dict):
                mails.append(m)
    for m in mails:
        t = _parse_ts(m.get('logged_at'))
        reason = str(m.get('reason') or '')
        if _in(t, since, until) and not m.get('sent') and reason.startswith('send failed'):
            out.append(_item('bounce', t, m.get('to'), m.get('subject'), 'The email did not go out.', '', '', '', 'send failed', label='email to'))
    if ses is None:
        return
    try:
        resp = ses.list_suppressed_destinations(Reasons=['BOUNCE', 'COMPLAINT'], StartDate=since, EndDate=until, PageSize=100)
    except Exception as e:
        print(f"[digest] bounce list skipped: {e}")
        return
    for s in resp.get('SuppressedDestinationSummaries', []) or []:
        addr = str(s.get('EmailAddress') or '')
        when = _parse_ts(s.get('LastUpdateTime')) or until
        if not _in(when, since, until):
            continue
        hit = next((m for m in sorted(mails, key=lambda x: str(x.get('logged_at') or ''), reverse=True)
                    if addr.lower() in {str(m.get('to') or '').lower()} | {str(x).lower() for x in (m.get('destinations') or [])}), None)
        what = 'The email bounced.' if str(s.get('Reason') or '') == 'BOUNCE' else 'The email was reported as unwanted.'
        if hit:
            what += f" Sent {_when(_parse_ts(hit.get('logged_at')))}."
        out.append(_item('bounce', when, addr, (hit or {}).get('subject') or 'no email from the chat to this address in the last week',
                         what, '', '', '', str(s.get('Reason') or ''), label='email to'))


def _reversals(s3, since, until, out):
    users = _get(s3, USERS_KEY)
    if not isinstance(users, dict):
        return
    for bucket in ('users', 'companies'):
        for name, rec in (users.get(bucket) or {}).items():
            if not isinstance(rec, dict):
                continue
            for row in rec.get('credit_usage_history') or []:
                if not isinstance(row, dict) or str(row.get('pull_type') or '') != 'refund':
                    continue
                t = _parse_ts(row.get('used_at'))
                if not _in(t, since, until):
                    continue
                credits = abs(int(row.get('credits_used') or 0))
                usd = abs(float(row.get('amount_usd') or 0.0))
                desc = re.sub(r'^REFUND(?: \(correction\))?:\s*', '', str(row.get('description') or '')).strip()
                amount = f"{credits} credits" if credits else ''
                if usd:
                    amount = (amount + ', ' if amount else '') + f"${usd:,.2f}"
                jid = str(row.get('job_id') or row.get('charge_job_id') or '')
                tid, question = '', ''
                ptr = _get(s3, f'{ASK_BY_JOB_PREFIX}{jid}.json') if jid else None
                if isinstance(ptr, dict) and ptr.get('key'):
                    ask = _get(s3, str(ptr.get('key')))
                    if isinstance(ask, dict):
                        tid, question = _thread_of(ask), str(ask.get('question') or '')
                who = name if bucket == 'users' else f"{name} (company)"
                out.append(_item('reversal', t, who, question or desc, f"A charge of {amount or 'the pull'} was reversed: {_clean(desc, 120) or 'the work did not finish'}.", tid, jid))


def collect(day=None, s3=None, ses=None, now=None, since=None, until=None):
    """The window's document. ``day`` (YYYY-MM-DD, UTC) pins one calendar
    day as the older same-day digest did; otherwise the window runs from
    the last digest to now."""
    if s3 is None:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
    if day:
        since, until = window(day=day)
    else:
        until = until or now or _utcnow()
        if since is None:
            since, _ = window(now=until, s3=s3)
    if ses is None and os.environ.get('REGRESSION_TEST_MODE') != '1' and os.environ.get('PM_DIGEST_SES', '1') != '0':
        try:
            import boto3
            ses = boto3.client('sesv2', region_name='us-east-2')
        except Exception:
            ses = None
    doc = {'date': until.astimezone(_pt()).strftime('%Y-%m-%d') if not day else str(day)[:10], 'since': _iso(since), 'until': _iso(until),
           'items': [], 'asks': 0, 'users': 0, 'corrections': [], 'repeats': [], 'held': [], 'faults': []}
    items = []
    try:
        recs = _ask_records(s3, since, until)
        doc['asks'] = len(recs)
        doc['users'] = len({str(r.get('user') or '') for r in recs})
        _unfinished(s3, recs, since, until, items)
        _misrouted(recs, items)
        seen_push = _pushback(s3, recs, since, until, items)
        _threads(s3, recs, since, until, items, seen_push)
        _deck_failures(s3, recs, since, until, items)
        _bounces(s3, ses, since, until, items)
        _reversals(s3, since, until, items)
    except Exception:
        traceback.print_exc()
    items = _collapse(items)
    items.sort(key=lambda it: it['ts'], reverse=True)      # newest first within a severity
    items.sort(key=lambda it: -it['severity'])             # stable: the worst kinds first
    doc['items'] = items
    # the older shape, for the surfaces and suites that read it
    for it in items:
        if it['kind'] == 'unfinished':
            doc['held' if it.get('outcome') != 'error' else 'faults'].append(
                {'ts': it['ts'], 'user': it['user'], 'question': it['question'], 'job_id': it['job_id'], 'outcome': it['outcome'] or 'held', 'subject': ''})
        elif it['kind'] == 'repeat':
            doc['repeats'].append({'ts': it['ts'], 'user': it['user'], 'question': it['question'], 'reply': it['reply'], 'reason': 'repeat'})
        elif it['kind'] == 'correction':
            doc['corrections'].append({'ts': it['ts'], 'user': it['user'], 'question': it['question'], 'reply': it['reply'], 'reason': 'correction'})
    doc['counts'] = counts(doc)
    return doc


def _collapse(items):
    """The same thing said twice about one chat (two promise lines with
    nothing behind them) is one item that says how many times."""
    out, seen = [], {}
    for it in sorted(items, key=lambda x: x['ts']):
        key = (it['kind'], it['user'], it['thread'], it['question'], it['what'])
        if key in seen and it['thread']:
            prev = seen[key]
            prev['times'] = int(prev.get('times') or 1) + 1
            prev['ts'] = max(prev['ts'], it['ts'])
            continue
        seen[key] = it
        out.append(it)
    for it in out:
        n = int(it.get('times') or 1)
        if n > 1:
            it['what'] = it['what'].rstrip('.') + f" ({n} times)."
    return out


_GROUPS = (('unfinished', ('unfinished',), 'answers never finished'),
           ('misrouted', ('misrouted', 'wizard_captured', 'build_offered', 'which_twice'), 'sent to the wrong place'),
           ('pushback', ('correction', 'repeat'), 'pushed back on or asked again'),
           ('deck_failures', ('deck_failure',), 'decks or files that failed'),
           ('bounces', ('bounce',), 'emails that bounced'),
           ('reversals', ('reversal',), 'charges reversed'))


def _items_of(doc):
    items = list(doc.get('items') or [])
    if not items:   # a document in the older shape (corrections / repeats / held / faults)
        for it in doc.get('corrections') or []:
            items.append(_item('correction', it.get('ts'), it.get('user'), it.get('question'), 'The person said the answer was wrong.', '', '', it.get('reply')))
        for it in doc.get('repeats') or []:
            items.append(_item('repeat', it.get('ts'), it.get('user'), it.get('question'), 'Asked the same thing again.', '', '', it.get('reply')))
        for it in doc.get('held') or []:
            items.append(_item('unfinished', it.get('ts'), it.get('user'), it.get('question'), 'Prometheus said it was working on it and nothing came back.', '', it.get('job_id')))
        for it in doc.get('faults') or []:
            items.append(_item('unfinished', it.get('ts'), it.get('user'), it.get('question'), 'The reply did not come back; it stopped with an error.', '', it.get('job_id'), '', 'error'))
    return items


def counts(doc):
    items = _items_of(doc)
    return {g: sum(1 for it in items if it['kind'] in kinds) for g, kinds, _label in _GROUPS}


def _line(it, full):
    t = _parse_ts(it.get('ts'))
    when = _when(t) if t else ''
    who = _clean(it.get('user'), 60) or 'someone'
    q = it.get('question') or ''
    label = str(it.get('label') or 'asked')
    if label == 'email to':
        head = f"{when}  email to {who}" if when else f"email to {who}"
    else:
        head = f"{when}  {who}" if when else who
    if not q:
        s = head
    elif label == 'deck':
        s = f"{head}, deck: \"{q}\""
    elif label == 'email to':
        s = f"{head}: \"{q}\""
    else:
        s = f"{head} asked: \"{q}\""
    s += f"\n    {it.get('what') or ''}"
    if full and it.get('reply') and it['kind'] in ('misrouted', 'wizard_captured', 'build_offered', 'correction', 'repeat'):
        s += f"\n    What they got: \"{it['reply']}\""
    if it.get('link'):
        s += f"\n    Open the chat: {it['link']}"
    return s


def render(doc):
    """Plain text: the counts, the worst ten in full, one line per
    remaining item. No internal terms, no dashes. Empty when there is
    nothing to report."""
    items = _items_of(doc)
    items.sort(key=lambda it: str(it.get('ts') or ''), reverse=True)
    items.sort(key=lambda it: -int(it.get('severity') or 1))
    since, until = _parse_ts(doc.get('since')), _parse_ts(doc.get('until'))
    if since and until:
        span = f"{_day_label(since)} {since.astimezone(_pt()).strftime('%-I:%M %p')} to {_day_label(until)} {until.astimezone(_pt()).strftime('%-I:%M %p')} PT"
    else:
        span = str(doc.get('date') or '')
    if not items:
        return ''
    c = counts(doc)
    parts = [f"Prometheus, what went wrong: {span}.",
             f"{len(items)} thing{'s' if len(items) != 1 else ''} to look at, out of {int(doc.get('asks') or 0)} questions from {int(doc.get('users') or 0)} people.", '',
             'Counts: ' + ', '.join(f"{c[g]} {label}" for g, _k, label in _GROUPS) + '.', '']
    worst, rest = items[:WORST], items[WORST:]
    parts.append('The worst ten:' if len(items) > WORST else ('The items:' if len(items) > 1 else 'The item:'))
    for i, it in enumerate(worst, 1):
        parts.append(f"{i}. {_line(it, True)}")
    if rest:
        parts += ['', 'The rest, one line each:']
        for it in rest:
            t = _parse_ts(it.get('ts'))
            parts.append(f"- {_when(t) if t else ''}  {_clean(it.get('user'), 60) or 'someone'}: {it.get('what') or ''}"
                         + (f" \"{(it.get('question') or '')[:80]}\"" if it.get('question') else '')
                         + (f"  {it['link']}" if it.get('link') else ''))
    parts += ['', 'Nothing in this note went to a user.']
    return '\n'.join(parts)


def _covers(doc):
    """The day (or days) the window covers, for the subject line."""
    since, until = _parse_ts(doc.get('since')), _parse_ts(doc.get('until'))
    if not since or not until:
        return str(doc.get('date') or '')
    a, b = _day_label(since), _day_label(until - _dt.timedelta(seconds=1))
    return a if a == b or (until - since) <= _dt.timedelta(hours=25) else f"{a} to {b}"


def subject_line(doc):
    items = _items_of(doc)
    return f"Prometheus: {len(items)} thing{'s' if len(items) != 1 else ''} went wrong, {_covers(doc)}"


def send(doc, s3=None, dry=False, recipients=None):
    """Write the day's record to S3 and, when there is something to report,
    send it to the ops recipients. Returns {'sent', 'key', 'body', 'subject',
    'empty'}."""
    body = render(doc)
    subject = subject_line(doc)
    key = f"{OUT_PREFIX}{doc.get('date')}.json"
    out = {'sent': False, 'key': key, 'body': body, 'subject': subject, 'empty': not body}
    if s3 is None and not dry:
        try:
            import boto3
            s3 = boto3.client('s3', region_name='us-east-2')
        except Exception:
            s3 = None
    if s3 is not None:
        try:
            s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps({**doc, 'body': body, 'subject': subject}).encode('utf-8'),
                          ContentType='application/json')
            s3.put_object(Bucket=BUCKET, Key=LAST_KEY, Body=json.dumps({'until': doc.get('until'), 'date': doc.get('date'),
                                                                        'items': len(doc.get('items') or []), 'sent': bool(body)}).encode('utf-8'),
                          ContentType='application/json')
        except Exception:
            traceback.print_exc()
    if not body:
        return out   # a day with nothing to report sends nothing
    if dry or os.environ.get('PM_MAIL_OFF') == '1' or os.environ.get('REGRESSION_TEST_MODE') == '1':
        return out
    try:
        from .mail_guard import sending_allowed
        if not sending_allowed():
            return out
    except Exception:
        pass
    try:
        import boto3
        boto3.client('ses', region_name='us-east-2').send_email(
            Source=SENDER, Destination={'ToAddresses': list(recipients or RECIPIENTS)},
            Message={'Subject': {'Data': subject}, 'Body': {'Text': {'Data': body}}})
        out['sent'] = True
    except Exception:
        traceback.print_exc()
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='The morning Prometheus digest: what went wrong since the last one.')
    ap.add_argument('--day', default=None, help='YYYY-MM-DD (UTC) to pin one calendar day instead of the window since the last digest')
    ap.add_argument('--since', default=None, help='ISO UTC start of the window (with --until)')
    ap.add_argument('--until', default=None, help='ISO UTC end of the window')
    ap.add_argument('--dry-run', action='store_true', help='print the body, send nothing, write nothing')
    args = ap.parse_args(argv)
    since = _parse_ts(args.since) if args.since else None
    until = _parse_ts(args.until) if args.until else None
    doc = collect(args.day, since=since, until=until)
    res = send(doc, dry=args.dry_run)
    print(res['subject'])
    print(res['body'] if res['body'] else '(nothing to report; no email)')
    print(f"[digest] sent={res['sent']} empty={res['empty']} key={res['key']} window={doc.get('since')}..{doc.get('until')} items={len(doc.get('items') or [])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
