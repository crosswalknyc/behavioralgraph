"""'please adjust' right after a build started is a build correction,
never a priced read (Jenna 2026-11-06, Sydney McLain at WME).

Sydney asked for a new profile on Tomorrow x Together, the K-pop group.
The interpret read the ask as a cut of the catalog's Hurry Up Tomorrow
film, the build started, and her "please adjust" drew "I can pull that
read on Tomorrow x Together. Pulling this data will cost $100." She
approved and got a read, not a profile; the wrong cut finished and was
delivered. Two charges, nothing she asked for.

The rule: when the thread's last agent turns announced a build (the
"Profile building: X" / "X is building" / "X: cut ready" / "X is
finished" lines, or a turn carrying a build run id) and the reader's
next words say the build is wrong ("please adjust", "that's the wrong
subject", "wrong profile", "not what I asked", "I meant <X>"), the
turn routes HERE: the wrong run is stopped when it is still running, a
finished wrong build is flagged for removal and a refund, the intended
subject is confirmed (taken from the words when they carry it), and
the right build is queued through the normal build flow. No quote. No
offer. Nothing priced.

  build_turn(history)       the build the thread just announced
  wants_build_fix(text)     the words say the build is wrong
  intended_subject(text)    the subject the words name, or ''
  pending_fix(history)      the lane asked for the subject last turn
  answer(...)               the reply payload, or None to pass

Pure text plus the host for the engine (cancel) and the ops alert.
Regression: scripts/test_pm_build_subject_identity.py.
"""
from __future__ import annotations

import re

from . import faults as _faults

__all__ = ['build_turn', 'wants_build_fix', 'intended_subject', 'pending_fix', 'answer',
           'compose', 'rewrite_pending_subject']

# The lines the build flow writes into a thread when a run starts or lands.
_BUILD_LINE_RXS = (
    re.compile(r"^\s*Profile building:\s*(?P<subj>.+?)\s*(?:\(number\s+\d+\s+in\s+line\))?\s*[.!]?\s*(?:I will email|$)", re.I | re.S),
    re.compile(r"^\s*(?P<subj>.+?)\s+is\s+building(?:\s+now)?\b", re.I),
    re.compile(r"^\s*(?P<subj>.+?):\s+cut\s+ready\b", re.I),
    re.compile(r"^\s*(?P<subj>.+?)\s+is\s+finished\s+and\s+ready\b", re.I),
    re.compile(r"^\s*Queued:\s*(?P<subj>.+?)\s*(?:\(|\.|$)", re.I),
)
_ACTIVE = ('queued', 'running', 'pending', 'working', 'starting')
_DONE = ('complete', 'completed', 'done', 'delivered')

# The words that say the build is wrong. Short, anchored, no question.
_FIX_RXS = (
    re.compile(r"^\W*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+)?(?:adjust|fix|correct|change|redo|re-?run)"
               r"(?:\s+(?:it|this|that|the\s+(?:subject|profile|build|run|title|name)))?\s*(?:please)?\W*$", re.I),
    re.compile(r"\b(?:that(?:'s|\s+is)|this\s+is|it(?:'s|\s+is))\s+(?:the\s+)?wrong\b", re.I),
    re.compile(r"\bwrong\s+(?:subject|profile|build|title|one|artist|group|band|movie|film|show|series|person|brand|name|match)\b", re.I),
    re.compile(r"\bnot\s+(?:the\s+)?(?:right|correct)\s+(?:subject|profile|build|title|one|artist|group|movie|film|show|person|brand|name)\b", re.I),
    re.compile(r"\bnot\s+what\s+i\s+(?:asked|meant|wanted|requested|ordered)\b", re.I),
    re.compile(r"\b(?:that|this|it)\s+(?:is|was|seems|looks)\s+(?:wrong|incorrect|off|a\s+mistake|the\s+wrong\s+\w+)\b", re.I),
    re.compile(r"^\W*(?:no[,.!\s]+)?(?:i\s+meant|it\s+should\s+be|should\s+be|the\s+subject\s+is|i\s+asked\s+for|i\s+wanted)\b", re.I),
    re.compile(r"\b(?:it(?:'s|\s+is)|that(?:'s|\s+is))\s+(?:running|building)\s+(?:on|off|against|as)\s+the\s+wrong\b", re.I),
    re.compile(r"\bbased\s+on\s+the\s+wrong\b|\bmatched\s+the\s+wrong\b|\bpicked\s+the\s+wrong\b", re.I),
)
_NOT_FIX_RXS = (
    re.compile(r"^\W*(?:approve|yes|ok(?:ay)?|go\s+ahead|cancel|stop|status|eta)\W*$", re.I),
    re.compile(r"\bhow\s+(?:much|long)\b", re.I),
)
_SUBJECT_RXS = (
    re.compile(r"\b(?:i\s+meant|it\s+should\s+be|should\s+be\s+(?:on|for)?|the\s+subject\s+(?:is|should\s+be)|i\s+asked\s+for|"
               r"i\s+wanted|build\s+(?:it\s+)?(?:on|for)|run\s+(?:it\s+)?(?:on|for)|make\s+it|use|not\s+\w+(?:\s+\w+){0,4},\s*)\s*"
               r"(?P<subj>[^.?!,;]{2,80}?)(?:\s+(?:instead|please|not\s+.*))?\s*[.?!]?\s*$", re.I),
    re.compile(r"\binstead\s+of\b.*?[,:;]\s*(?P<subj>[^.?!]{2,80}?)\s*[.?!]?\s*$", re.I),
)
_TYPE_TAIL_RX = re.compile(r",?\s*\b(?:the\s+)?(?:k[\s-]*pop\s+(?:boy\s+|girl\s+)?(?:group|band)|band|group|movie|film|series|show|podcast|"
                           r"actor|actress|singer|rapper|brand|company|athlete|team|author|creator|influencer)\b\s*$", re.I)


def _text_of(turn):
    return str((turn or {}).get('text') or (turn or {}).get('content') or '').strip()


def _build_subject(turn):
    """The subject a build-flow line names, with its run id and status,
    or None when the turn is not a build line."""
    if not isinstance(turn, dict) or str(turn.get('role') or '') == 'user':
        return None
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    text = _text_of(turn)
    run_id = str(meta.get('run_id') or '').strip()
    status = str(meta.get('status') or '').strip().lower()
    subj = str(meta.get('subject') or '').strip()
    hit = None
    for rx in _BUILD_LINE_RXS:
        m = rx.match(text)
        if m:
            hit = (m.group('subj') or '').strip().rstrip('.').strip()
            break
    if not run_id and hit is None:
        return None
    subj = subj or hit or ''
    if not subj:
        return None
    if not status:
        low = text.lower()
        if 'finished' in low or 'cut ready' in low or 'is ready' in low:
            status = 'complete'
        elif 'building' in low or 'queued' in low:
            status = 'running'
    return {'subject': subj[:120], 'run_id': run_id, 'status': status or 'running', 'text': text[:200]}


def build_turn(history, lookback=4):
    """The build the thread's latest agent turns announced: the agent
    turns since the reader's last message (at most `lookback` of them),
    newest first. None when the thread is not sitting on a build."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    seen = 0
    found = None
    for t in reversed(turns):
        if str(t.get('role') or '') == 'user':
            break
        seen += 1
        if seen > lookback:
            break
        hit = _build_subject(t)
        if not hit:
            continue
        if found is None:
            found = dict(hit)
            continue
        # the newest line names the state; an earlier line may carry the
        # run id ("Profile building: X" has it, "X is building." does not)
        if not found.get('run_id') and hit.get('run_id'):
            found['run_id'] = hit['run_id']
        if found.get('status') in ('running',) and hit.get('status') in _DONE:
            found['status'] = hit['status']
    return found


def wants_build_fix(text):
    """True when the words say the build that just started is wrong."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 400:
        return False
    for rx in _NOT_FIX_RXS:
        if rx.search(t):
            return False
    return any(rx.search(t) for rx in _FIX_RXS)


def intended_subject(text):
    """The subject the correction names ('I meant Tomorrow X Together,
    the K-pop group' -> 'Tomorrow X Together'), or '' when the words
    only say the build is wrong."""
    t = ' '.join(str(text or '').split()).strip()
    if not t:
        return ''
    t = _TYPE_TAIL_RX.sub('', t).strip()
    for rx in _SUBJECT_RXS:
        m = rx.search(t)
        if m:
            subj = (m.group('subj') or '').strip().strip('"\'' + '\u201c\u201d\u2018\u2019').strip()
            subj = re.sub(r"^(?:on|for|to|about|the\s+profile\s+(?:on|for))\s+", '', subj, flags=re.I).strip()
            subj = _TYPE_TAIL_RX.sub('', subj).strip().rstrip(',').strip()
            if subj and 2 <= len(subj) <= 80 and not wants_build_fix(subj) \
                    and not re.fullmatch(r"(?:wrong|it|this|that|the\s+\w+)", subj, re.I):
                return subj
    return ''


def entity_type_phrase(text):
    """'the K-pop group' / 'the movie' when the words name what the
    subject is, so the right build carries it; else ''."""
    m = re.search(r"\b(?:the\s+)?(k[\s-]*pop\s+(?:boy\s+|girl\s+)?(?:group|band)|boy\s*band|girl\s*group|band|"
                  r"music\s+group|rapper|singer|movie|film|tv\s+series|series|show|podcast|actor|actress|"
                  r"comedian|athlete|author|creator|influencer|brand|company)\b", str(text or ''), re.I)
    return ('the ' + ' '.join(m.group(1).split())) if m else ''


def pending_fix(history):
    """The lane's own question from the previous agent turn, when it
    asked which subject the build should be on: meta.build_fix with
    'awaiting_subject'. None otherwise."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns):
        if str(t.get('role') or '') == 'user':
            return None
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        bf = meta.get('build_fix')
        if isinstance(bf, dict) and bf.get('awaiting_subject'):
            return bf
    return None


def rewrite_pending_subject(text, pending):
    """The reader answered the lane's question with the subject: the
    words become the build order the normal flow understands. '' when
    the words are not a plain subject (a question, a cancel, a long
    message)."""
    t = ' '.join(str(text or '').split()).strip().strip('"\'' + '\u201c\u201d').strip()
    if not pending or not t or len(t) > 120 or '?' in t:
        return ''
    if re.match(r"^\W*(?:cancel|stop|never\s*mind|no\b|nothing|forget\s+it)", t, re.I):
        return ''
    if re.match(r"^\W*(?:build|run|make|create|pull)\b", t, re.I):
        return t
    subj = intended_subject(t) or _TYPE_TAIL_RX.sub('', t).strip().rstrip(',').strip() or t
    return f"Build a new profile on {subj}"


def _cancel(host, run_id):
    """Stop a run through the engine. 'cancelled' | 'stopping' | 'failed'."""
    try:
        url = str(getattr(host, 'queue_url', '') or '').rstrip('/') if host.has('queue_url') else ''
        secret = str(host.queue_secret or '') if host.has('queue_secret') else ''
        if not url or not secret or not run_id:
            return 'failed'
        import requests as _rq
        resp = _rq.post(f"{url}/synth/cancel/{run_id}", headers={'X-Synth-Auth': secret}, timeout=12)
        if resp.status_code != 200:
            return 'failed'
        st = str((resp.json() or {}).get('status') or '')
        return 'cancelled' if st == 'cancelled' else 'stopping'
    except Exception as e:
        _faults.note('build_fix.cancel', e)
        return 'failed'


def compose(wrong_subject, intended, outcome, finished=False, type_phrase=''):
    """The reply. One plain paragraph: what was stopped or flagged,
    what happens to the charge, and the one question or the handoff."""
    wrong = wrong_subject or 'that build'
    if finished:
        lead = (f"{wrong} was built against the wrong subject. I have flagged it for removal and the "
                f"charge comes back to your balance; nothing else was touched.")
    elif outcome == 'cancelled':
        lead = f"Stopped. {wrong} was cancelled before it started and the charge comes back to your balance automatically."
    elif outcome == 'stopping':
        lead = f"Stopping {wrong} now. It unwinds at the next safe point and the charge comes back to your balance automatically."
    else:
        lead = f"{wrong} is being pulled back and the charge comes back to your balance; I have flagged it so it does not ship."
    if intended:
        tail = (f" Starting the right one now: a new profile on {intended}"
                + (f", {type_phrase}" if type_phrase else '') + ". The brief follows below.")
        return lead + tail
    return (lead + " Which subject should the profile be on? Reply with the name, and what it is if a title "
                   "could share a word with it (for example 'Tomorrow X Together, the K-pop group'), and I will start it. "
                   "Nothing is charged until you approve the brief.")


def answer(text, uname, tid, history, *, host, user=None):
    """The build-fix reply payload, or None when the turn is not a build
    correction. The payload carries `build_fix` (persisted on the turn's
    meta) and, when the words named the subject, `rebuild_text`: the
    build order the dispatcher runs next in the same turn."""
    try:
        if not wants_build_fix(text):
            return None
        hit = build_turn(history)
        if not hit:
            return None
        intended = intended_subject(text)
        type_phrase = entity_type_phrase(text)
        status = str(hit.get('status') or '').lower()
        finished = status in _DONE
        outcome = 'skipped'
        if not finished and hit.get('run_id'):
            outcome = _cancel(host, hit['run_id'])
        # a finished wrong build (or a cancel the engine refused) is an
        # ops action: take the file down and refund. Failure / system
        # alerts go to Jenna and Jessie only.
        if finished or outcome == 'failed':
            try:
                if host.has('error_email'):
                    host.error_email(
                        'pm/build_fix',
                        RuntimeError(f"wrong build to take down and refund: {hit.get('subject')!r} "
                                     f"run {hit.get('run_id') or '-'} ({status or 'unknown'}); "
                                     f"intended {intended or 'unknown'!r}; words: {text[:200]!r}"),
                        user_email=((user or {}).get('email') or uname),
                        payload={'run_id': hit.get('run_id'), 'subject': hit.get('subject'), 'status': status,
                                 'intended': intended, 'question': text[:300], 'thread_id': tid})
            except Exception as e:
                _faults.note('build_fix.alert', e)
        reply = compose(hit.get('subject'), intended, outcome, finished=finished, type_phrase=type_phrase)
        out = {
            'success': True, 'action': 'answer', 'reply': reply, 'followups': [],
            'offer_deck': False, 'deck_angle': None,
            'build_fix': {'wrong_subject': hit.get('subject'), 'run_id': hit.get('run_id') or '',
                          'wrong_status': status, 'cancel': outcome, 'intended': intended,
                          'awaiting_subject': not bool(intended)},
        }
        if intended:
            out['rebuild_text'] = (f"Build a new profile on {intended}" + (f", {type_phrase}" if type_phrase else ''))
        return out
    except Exception as e:
        _faults.note('build_fix.answer', e)
        return None
