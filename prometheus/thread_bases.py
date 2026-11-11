"""A named base holds across a thread's reads (2026-11-10, Liz's
Amazon Channels vs direct survival thread).

Her first read in the thread tracked 52,605,814 direct starts and
15,808,113 Amazon Prime Video Channels starts from their own day 0. Her
next message, "Show the same two paths on an access-end cancel basis
instead of request date", changed the basis and nothing else; the read
that landed tracked 23,272,291 direct signups and 9,187,463 Channels
signups. Same cohorts, two reads apart, bases less than half the size.
The stored-figure binding (prometheus.figure_binding) could not hold them
because the two reads banked under different subject labels and the
cohort counts carried different metric names.

The rule here is mechanical. Before a read ships, the cohort bases the
thread's earlier reads stated (a count whose label says it is a start,
signup, cohort or base count, or a count the prose wrote as 'of the N
direct starts') are collected, first stated first. A base the new read
states for the same cohort (the same distinctive words: 'direct';
'Prime Video Channels'; 'Apple TV') is held to the thread's figure, in
the result's metrics and in the prose, unless the ask named a window of
its own or asked for a fresh draw. A combined total that was the sum of
two held bases follows them. One plain line at the end says what was
kept. Shares are never rewritten; the arithmetic pass that follows this
one recomputes any 'X% (N people)' pair that now sits on a changed base.

Pure functions over the history, the job documents and the result dict;
the caller passes a job loader. Never raises into a read.
Regression: scripts/test_pm_thread_bases.py.
"""
from __future__ import annotations

import re

__all__ = ['thread_reads', 'bases_from_reads', 'hold', 'held_line', 'ask_names_window']

_COUNT_RX = re.compile(r'(?<![\d.$])(?<!\d,)(\d{1,3}(?:,\d{3})+|\d{5,})(?!\d|,\d|\s*%)')
# the words that say a count is a cohort base
_COHORT_WORDS = frozenset((
    'starts', 'start', 'started', 'signups', 'signup', 'sign', 'ups', 'cohort', 'cohorts', 'base',
    'bases', 'accounts', 'subscriptions', 'activations', 'activated', 'joined', 'opened', 'tracked',
    'new', 'subscribers'))
# words that never distinguish one cohort from another
_GENERIC = frozenset((
    'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'by', 'with', 'and', 'or', 'vs', 'versus',
    'its', 'their', 'those', 'these', 'that', 'this', 'from', 'through', 'via', 'inside', 'into',
    'us', 'u', 's', 'people', 'users', 'viewers', 'audience', 'total', 'all', 'combined', 'both',
    'paths', 'path', 'ten', 'nine', 'eight', 'services', 'service', 'premium', 'streaming', 'own',
    'site', 'app', 'apps', 'sites', 'window', 'day', 'days', 'first', 'own', 'each', 'per', 'still',
    'paid', 'across', 'over', 'same', 'two', 'three', 'store', 'storefront', 'billing', 'billed',
    'relationship', 'cohort', 'cohorts', 'base', 'bases')) | _COHORT_WORDS
# 'of the 52,605,814 direct starts', 'the 15,808,113 Amazon Prime Video Channels starts',
# '52,605,814 starts opened on the service's own site'
_PROSE_BASE_RX = re.compile(
    r'(?:\b(?:of|from|across|among)\s+)?(?:the|its|their|those|these|all)?\s*(?P<n>\d{1,3}(?:,\d{3})+|\d{5,})\s+'
    r'(?P<label>(?:[A-Za-z][\w+\'’-]*\s+){0,6}?(?:starts|signups|sign-ups|new accounts|new subscriptions|cohort|'
    r'accounts|subscriptions)(?:\s+(?:opened|started|activated|that started|billed)\s+(?:[A-Za-z][\w+\'’-]*\s*){0,6})?)',
    re.I)
# a count that is a state of a cohort ('Channels starts still active at
# day 30', 'direct starts gone within 35 days'), never the cohort itself
_NOT_BASE_WORDS = frozenset((
    'active', 'inactive', 'gone', 'cancel', 'cancels', 'cancelled', 'canceled', 'cancelling', 'canceling',
    'churn', 'churned', 'churners', 'retained', 'retention', 'remaining', 'remain', 'lapsed', 'switched',
    'switchers', 'returned', 'returners', 'winback', 'holding', 'lost', 'kept', 'sampler', 'samplers',
    'month', 'months', 'share', 'rate', 'pct', 'surviving', 'survived', 'still', 'watched', 'played',
    'churning', 'leaving', 'left', 'reactivated', 'restarted', 'live', 'lapse', 'lapsing'))
_STATE_WORDS = frozenset((
    'active', 'inactive', 'gone', 'cancelled', 'canceled', 'churned', 'retained', 'remaining', 'lapsed',
    'switched', 'returned', 'holding', 'lost', 'kept', 'surviving', 'survived', 'still', 'churning',
    'leaving', 'left', 'reactivated', 'restarted', 'live', 'lapsing'))
_OF_THE_RX = re.compile(r'\b(?:of|from|across|among|out of)\s+(?:the|its|their|those|these|all|every)?\s*$', re.I)
# the started-read line is a promise, never a landed read
_PROMISE_RX = re.compile(r"^\s*On it\b|\bthe read will land right here\b|\bWorking on it\b|^\s*Already on it\b|^\s*Still on it\b|"
                         r"^\s*Included with your plan\.\s*On it\b", re.I)
_FRESH_RX = re.compile(
    r"\b(?:independent(?:ly)?\s+(?:verif|recomput|re-?run|re-?read)|recompute[^.]{0,60}without\s+reusing|"
    r"without\s+reusing\s+any\s+figure|do\s+not\s+reuse|don'?t\s+reuse|from\s+scratch|fresh\s+draw|"
    r"start\s+over|ignore\s+(?:the\s+)?(?:earlier|previous|prior)\s+(?:figures?|numbers?|reads?|bases?|cohorts?)|"
    r"new\s+cohorts?\s+window|re-?size\s+the\s+cohorts?)\b", re.I)
_MON = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
_WINDOW_RX = re.compile(
    r'\b' + _MON + r'\.?\s+(?:\d{1,2},?\s+)?20\d\d\b|\b20\d\d-\d\d-\d\d\b|\b(?:last|past|trailing|previous)\s+\d+\s+(?:days?|weeks?|months?|years?)\b|'
    r'\b(?:q[1-4]|h[12])\s+20\d\d\b|\bcalendar\s+20\d\d\b|\b(?:ytd|year to date)\b', re.I)
_SAME_WINDOW_RX = re.compile(r'\bsame\s+(?:ten\s+services\s+and\s+)?window\b|\bsame\s+(?:cohorts?|bases?|two paths)\b', re.I)
TOLERANCE = 0.01          # a base within 1% of the held figure is the same figure
MAX_READS = 8


def _num(s):
    try:
        return int(str(s).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _fmt(n):
    return f"{int(n):,}"


def _tokens(label):
    return [w for w in re.findall(r"[a-z0-9+]+", str(label or '').lower()) if w]


def _distinct(label):
    """The words that say which cohort a base counts ('direct';
    'amazon prime video channels'); nothing generic."""
    return frozenset(w for w in _tokens(label) if w not in _GENERIC and len(w) > 1)


def _is_cohort_label(label):
    """The label names a cohort base, not a state of one."""
    toks = set(_tokens(label))
    return bool(toks & _COHORT_WORDS) and not (toks & _NOT_BASE_WORDS)


def _prose_bases(text):
    """(n, label) for every cohort base the prose states. 'X% of the N
    direct starts are still active' names N as the base; 'N Channels
    starts still active' names a state count and is skipped."""
    out = []
    s = str(text or '')
    for pm in _PROSE_BASE_RX.finditer(s):
        if not _OF_THE_RX.search(s[max(0, pm.start('n') - 24):pm.start('n')]):
            after = _tokens(s[pm.end():pm.end() + 48])[:3]
            if any(w in _STATE_WORDS for w in after):
                continue
            if set(_tokens(pm.group('label'))) & _NOT_BASE_WORDS:
                continue
        out.append((pm.group('n'), pm.group('label')))
    return out


def ask_names_window(text):
    """True when the ask names a window of its own (a month and year, a
    date, a trailing span), which releases every held base. 'Same
    window' / 'same two paths' never does."""
    t = str(text or '')
    if _WINDOW_RX.search(t):
        return True
    return False


def thread_reads(history, load_job=None, limit=MAX_READS):
    """The thread's earlier landed reads, oldest first: {'reply',
    'metrics', 'ts', 'job_id'}. A landed read turn carries its text; a
    promise turn carries the job id, resolved through ``load_job`` (the
    API door lands its reads in the job document, not always in the
    thread the ask was posted to)."""
    out, seen = [], set()
    for t in list(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if meta.get('corrected_at') or meta.get('retired') or meta.get('withdrawn'):
            continue
        jid = str(meta.get('read_job_id') or '')
        text = str(t.get('text') or t.get('content') or '').strip()
        promise = bool(_PROMISE_RX.search(text[:400]))
        landed = not promise and (str(meta.get('kind') or '') == 'read'
                                  or (text.startswith('On ') and ':' in text[:160]))
        if landed and text and (jid or text) not in seen:
            seen.add(jid or text)
            out.append({'reply': text, 'metrics': list(meta.get('metrics') or []), 'ts': str(t.get('ts') or ''), 'job_id': jid})
            continue
        if jid and jid not in seen and callable(load_job):
            try:
                doc = load_job(jid) or {}
            except Exception:
                doc = {}
            if not isinstance(doc, dict) or str(doc.get('status') or '') != 'done':
                continue
            if doc.get('corrected_at') or doc.get('retired'):
                continue
            payload = doc.get('payload') if isinstance(doc.get('payload'), dict) else {}
            reply = str(payload.get('reply') or '').strip()
            if not reply:
                continue
            seen.add(jid)
            out.append({'reply': reply, 'metrics': list(payload.get('metrics') or []),
                        'ts': str(doc.get('finished_at') or t.get('ts') or ''), 'job_id': jid})
    return out[-limit:]


def bases_from_reads(reads):
    """The cohort bases the reads stated, first stated first; one per
    cohort (the first figure wins; a later read that disagreed is the
    drift this module exists to stop). Each: {'n', 'label', 'tokens',
    'job_id'}."""
    bases = []

    def _add(n, label, jid):
        n = _num(n)
        if not n or n < 1000:
            return
        toks = _distinct(label)
        if not toks:
            return
        for b in bases:
            if b['tokens'] == toks or (toks <= b['tokens'] or b['tokens'] <= toks):
                return          # this cohort already has its base
        bases.append({'n': n, 'label': ' '.join(str(label).split())[:90], 'tokens': toks, 'job_id': jid})

    for r in reads or []:
        jid = str((r or {}).get('job_id') or '')
        for m in (r or {}).get('metrics') or []:
            if not isinstance(m, dict) or not isinstance(m.get('value'), (int, float)):
                continue
            unit = str(m.get('unit') or 'count').lower()
            if unit in ('pct', 'percent', '%', 'usd', 'months', 'pct_pts', 'points', 'index'):
                continue
            label = f"{m.get('label') or ''} {m.get('name') or ''}".replace('_', ' ')
            if _is_cohort_label(label):
                _add(m['value'], label, jid)
        for n, label in _prose_bases((r or {}).get('reply')):
            _add(n, label, jid)
        for m in (r or {}).get('metrics') or []:
            if isinstance(m, dict):
                for n, label in _prose_bases(m.get('definition')):
                    _add(n, label, jid)
    return bases


def _match(toks, bases):
    for b in bases:
        if toks and (toks == b['tokens'] or toks <= b['tokens'] or b['tokens'] <= toks):
            return b
    return None


def hold(res, reply, bases, ask_text=''):
    """Hold the new read's cohort bases to the thread's. Returns (res,
    reply, held) where held is a list of {'label', 'from', 'to'}. In
    place on res; never raises."""
    held = []
    try:
        if not bases or not isinstance(res, dict):
            return res, reply, held
        if _FRESH_RX.search(str(ask_text or '')) or ask_names_window(ask_text):
            return res, reply, held
        swaps = {}          # old formatted count -> new formatted count
        new_bases = []      # (tokens, old, new) for the combined-total rule
        for m in (res.get('metrics') or []):
            if not isinstance(m, dict) or not isinstance(m.get('value'), (int, float)):
                continue
            unit = str(m.get('unit') or 'count').lower()
            if unit in ('pct', 'percent', '%', 'usd', 'months', 'pct_pts', 'points', 'index'):
                continue
            label = f"{m.get('label') or ''} {m.get('name') or ''}".replace('_', ' ')
            if not _is_cohort_label(label):
                continue
            toks = _distinct(label)
            b = _match(toks, bases)
            if not b:
                continue
            old = int(round(float(m['value'])))
            if old <= 0 or abs(old - b['n']) / float(b['n']) <= TOLERANCE:
                new_bases.append((toks, old, old))
                continue
            m['value'] = int(b['n'])
            swaps[_fmt(old)] = _fmt(b['n'])
            new_bases.append((toks, old, int(b['n'])))
            held.append({'label': str(m.get('label') or m.get('name') or 'cohort base'), 'from': old, 'to': int(b['n'])})
        # bases the prose states but the metrics do not carry
        for n_txt, label in _prose_bases(reply):
            old = _num(n_txt)
            if not old or _fmt(old) in swaps:
                continue
            toks = _distinct(label)
            b = _match(toks, bases)
            if not b or abs(old - b['n']) / float(b['n']) <= TOLERANCE:
                continue
            swaps[_fmt(old)] = _fmt(b['n'])
            new_bases.append((toks, old, int(b['n'])))
            held.append({'label': ' '.join(str(label).split())[:90], 'from': old, 'to': int(b['n'])})
        # a combined total that was the sum of two held bases follows them,
        # wherever the read states it (the prose, the headline, the table's
        # share basis, a definition)
        changed = [(o, n) for _t, o, n in new_bases if o != n]
        if len(new_bases) >= 2 and changed:
            old_sum = sum(o for _t, o, _n in new_bases)
            new_sum = sum(n for _t, _o, n in new_bases)
            texts = [str(reply or ''), str(res.get('headline') or ''), str(res.get('universe') or '')]
            texts += [str(r) for r in (res.get('reads') or []) if isinstance(r, str)]
            bd0 = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
            texts.append(str(bd0.get('share_basis') or ''))
            texts += [str(m.get('definition') or '') for m in (res.get('metrics') or []) if isinstance(m, dict)]
            for blob in texts:
                for cm in _COUNT_RX.finditer(blob):
                    v = _num(cm.group(1))
                    if v and old_sum and abs(v - old_sum) / float(old_sum) <= TOLERANCE and _fmt(v) not in swaps:
                        swaps[_fmt(v)] = _fmt(new_sum)
                        held.append({'label': 'combined cohorts', 'from': v, 'to': new_sum})
            for m in (res.get('metrics') or []):
                if isinstance(m, dict) and isinstance(m.get('value'), (int, float)):
                    v = int(round(float(m['value'])))
                    if old_sum and abs(v - old_sum) / float(old_sum) <= TOLERANCE and v != new_sum:
                        m['value'] = new_sum
        if not swaps:
            return res, reply, held
        # the prose and every stored string follow the metrics
        def _swap_text(s):
            if not isinstance(s, str) or not s:
                return s
            for o, n in swaps.items():
                s = re.sub(r'(?<!\d)(?<!\d,)' + re.escape(o) + r'(?!\d|,\d)', n, s)
            return s
        reply = _swap_text(reply)
        for key in ('headline', 'universe', 'cohort'):
            if isinstance(res.get(key), str):
                res[key] = _swap_text(res[key])
        if isinstance(res.get('reads'), list):
            res['reads'] = [_swap_text(r) for r in res['reads']]
        for m in (res.get('metrics') or []):
            if isinstance(m, dict):
                for k in ('definition', 'label'):
                    if isinstance(m.get(k), str):
                        m[k] = _swap_text(m[k])
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else None
        if bd:
            if isinstance(bd.get('share_basis'), str):
                bd['share_basis'] = _swap_text(bd['share_basis'])
            for r in (bd.get('rows') or []):
                if isinstance(r, dict):
                    for k in ('note', 'definition'):
                        if isinstance(r.get(k), str):
                            r[k] = _swap_text(r[k])
        res['_thread_bases'] = [dict(h) for h in held]
        return res, reply, held
    except Exception:
        return res, reply, held


def held_line(held):
    """'Cohort bases kept as first reported in this thread: Direct new
    signups 52,605,814; Prime Video Channels new signups 15,808,113.'
    One line; '' when nothing was held."""
    items = [h for h in (held or []) if isinstance(h, dict) and h.get('label') != 'combined cohorts']
    if not items:
        return ''
    seen, parts = set(), []
    for h in items:
        key = (str(h.get('label') or '').lower(), int(h.get('to') or 0))
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{str(h.get('label') or 'cohort base').strip().rstrip(':')} {_fmt(h.get('to') or 0)}")
    return 'Cohort bases kept as first reported in this thread: ' + '; '.join(parts[:6]) + '.'
