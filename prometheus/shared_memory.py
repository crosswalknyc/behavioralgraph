"""One brain, two rings (Jenna, 2026-10-13).

Jenna: "it should be an individual ring but the results should be
shared across the entire corpus essentially. so that two users asking
similar questions could never get different answers."

The shared ring is the insights ledger: every figure Prometheus states
goes in, keyed by subject and window, and every later ask on that
subject reads it first, so the figures bind whoever is asking. Until
today only generated reads, panel facts and search-demand answers were
persisted; the answer a reader got about the profile on screen (the
most common answer there is) and the audience sizings were not, so two
people asking the same thing about the same page could be handed two
sets of numbers. This module closes that.

The individual ring is the reader's own memory (``prometheus_memory``):
what they are working on, how they like answers, what they have been
told. It is written as short notes after an answer and read into the
"who is asking" block of the next one. It never carries figures (those
belong to the shared ring) and it never informs another reader.

A note belongs to the thread it was written in (Jenna 2026-11-11: a new
chat never carries a subject from an earlier one). "Is comparing Mad Men
viewers year over year" is a fine note inside the Mad Men chat and a
leak in the prompt of a new chat about Cruel Summer, so notes are
stamped with their thread and read back only there
(prometheus.thread_scope). Standing preferences ("always", "from now
on") are rules the reader set for every answer and stay with the reader.

Everything here is best effort and non-blocking: a write that fails
costs nothing but the note; a read that fails means an ask runs the
way it did yesterday.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

ANSWER_ROUTE = 'page_analysis'
SIZING_FAMILY = 'audience_size'
NOTES_MAX = 24
NOTES_TTL_S = 90 * 24 * 3600

_SENT_SPLIT_RX = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(\[])|\n+')
_NUM_RX = re.compile(
    r'(?<![\w.])(?:\$\s?)?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?'
    r'|(?<![\w.])\d+(?:\.\d+)?\s?(?:%|x|pp|M|K|B|million|billion|thousand)(?![\w])'
    r'|(?<![\w.])\d+(?:\.\d+)?(?=\s+(?:people|viewers|accounts|users|individuals|subscribers|hours|sessions|visits))',
    re.IGNORECASE)
_SKIP_SENT_RX = re.compile(
    r'\b(?:\$\s?\d+(?:,\d{3})*(?:\.\d\d)?\s+(?:per|a|each|on approval)|credits?|runs \$|it runs|price[sd]? at)\b'
    r'|\b(?:19|20)\d\d\b(?!\S)', re.IGNORECASE)
_YEAR_RX = re.compile(r'^(?:19|20)\d\d$')
_AFTER_UNIT_RX = re.compile(r"\s+(?:total\s+|viewing\s+|watch\s+|streaming\s+|listening\s+)?"
                            r"(hours?|hrs|minutes?|mins|sessions?|visits?|views|plays|streams|episodes?|titles?|orders?|"
                            r"transactions?|impressions?|clicks?|searches|posts?|videos?)\b", re.I)
_AFTER_UNITS = {'hour': 'hours', 'hr': 'hours', 'minute': 'minutes', 'min': 'minutes', 'session': 'sessions', 'visit': 'visits',
                'view': 'views', 'play': 'plays', 'stream': 'streams', 'episode': 'episodes', 'title': 'titles', 'order': 'orders',
                'transaction': 'transactions', 'impression': 'impressions', 'click': 'clicks', 'searche': 'searches',
                'search': 'searches', 'post': 'posts', 'video': 'videos'}


def _sync():
    return os.environ.get('REGRESSION_TEST_MODE') == '1'


def _run(fn):
    if _sync():
        fn()
        return
    try:   # the thread has no request: the asker seen here rides with it (a probe never banks, 2026-11-11)
        from . import ledger_gate as _lg
        fn = _lg.carry(fn)
    except Exception:
        pass
    threading.Thread(target=fn, daemon=True).start()


# --------------------------------------------------------------- figures
def figures_from_reply(reply, limit=14, evidence=None):
    """[{name, label, value, unit, definition}] for every figure a reply
    states, each with the sentence that carried it (the ledger's binding
    block shows the sentence, so the next answer reuses the figure in
    context). A figure is named only by what the reply or the read's
    `evidence` ({(value, kind): label}, see _evidence_names) says it
    measures; one the reply never names has an empty name and is never
    banked as a metric. Prices, credits and bare years are skipped."""
    out, seen = [], set()
    for sent in _SENT_SPLIT_RX.split(str(reply or '')):
        s = ' '.join(sent.split())
        if not s or len(s) > 360:
            continue
        for m in _NUM_RX.finditer(s):
            v = m.group(0).strip()
            if not v or v in seen or _YEAR_RX.match(v):
                continue
            try:
                from . import stated_numbers as _sn
                if _sn.is_date_token(s, m.start(), m.end()):
                    continue   # a year inside a date is never a figure (2026-10-21)
            except Exception:
                pass
            if _SKIP_SENT_RX.search(s) and ('$' in v or 'credit' in s.lower()):
                continue
            seen.add(v)
            num, unit = _numeric(v)
            if num is None:
                continue
            if unit == 'count':
                # the noun right after the figure is its unit: hours,
                # sessions and visits are not people (2026-11-11, a
                # measure in hours was stored as a count of viewers)
                _nm = _AFTER_UNIT_RX.match(s[m.end():])
                if _nm:
                    unit = _AFTER_UNITS.get(_nm.group(1).lower().rstrip('s'), unit)
            label = _evidence_name(evidence, num, unit, s[m.end():]) or _figure_name(s, m.start(), m.end())
            out.append({'name': _slug(label), 'label': label[:80], 'value': num,
                        'unit': unit, 'definition': s[:220]})
            if len(out) >= limit:
                return out
    return out


# What a figure measures, as the reply or the read's evidence names it
# (2026-11-24: 'read: The Nine Club sits at' was stored as a metric name).
_MEASURE_AFTER_RX = re.compile(r"\s*(penetration|share|index|reach|rate|completion|conversion)\b", re.I)
_LINK_WORDS = frozenset(('reads', 'read', 'sits', 'sat', 'is', 'are', 'was', 'were', 'at', 'with', 'has', 'had',
                         'holds', 'held', 'carries', 'carried', 'scores', 'scored', 'hits', 'hit', 'posts', 'posted',
                         'shows', 'showed', 'lands', 'landed', 'reaches', 'reached', 'of', 'to', 'about', 'around',
                         'nearly', 'roughly', 'just', 'over', 'under', 'only', 'still', 'now', 'by'))
_INNER_WORDS = frozenset(('in', 'of', 'on', 'at', 'for', '&', 'and', 'the', 'x'))
_NOT_A_NAME = frozenset(('its', 'their', 'this', 'that', 'these', 'those', 'it', 'they', 'he', 'she', 'his', 'her',
                         'our', 'your', 'a', 'an', 'the', 'each', 'every', 'which', 'what', 'but', 'and', 'or', 'so'))
_VERBISH = frozenset(('is', 'are', 'was', 'were', 'sits', 'sit', 'reads', 'read', 'drew', 'draws', 'has', 'have',
                      'had', 'holds', 'held', 'carries', 'carried', 'comes', 'came', 'leads', 'led', 'shows',
                      'showed', 'means', 'mean', 'gets', 'got', 'makes', 'made', 'goes', 'went', 'watched', 'played'))
_MEASURE_ONLY = frozenset(('penetration', 'share', 'index', 'reach', 'rate', 'figure', 'total', 'count', 'value',
                           'typed', 'by', 'the', 'model', 'of', 'pct', 'percent', 'us', 'people'))


def _slug(label):
    return re.sub(r'[^a-z0-9]+', '_', str(label or '').lower()).strip('_')[:48]


def _figure_name(s, start, end):
    """'' or what the reply says the figure measures, in one of three shapes:
    the words before a colon ('Unique US viewers: 416,287'), a name before
    and a measure after ('Zoom reads 58.0% penetration'), or the noun after
    a count ('912,447 sessions', '64,638,049,641 hours on YouTube'). Never
    the run of prose before the figure."""
    head, tail = s[:start].rstrip(), s[end:]
    if head.endswith(':'):
        seg = head[:-1]
        cut = max(seg.rfind('. '), seg.rfind(' - '), seg.rfind('; '), seg.rfind(', '), seg.rfind('('), seg.rfind(': '))
        words = (seg[cut + 1:] if cut >= 0 else seg).strip(' -,(').split()
        if 1 <= len(words) <= 8 and not any(re.search(r'\d', w) for w in words) \
                and not ({w.lower().strip(',.;:') for w in words} & _VERBISH):
            return ' '.join(words)
    mm = _MEASURE_AFTER_RX.match(tail)
    if mm:
        words = head.split()
        while words and words[-1].lower().strip(',;:') in _LINK_WORDS:
            words.pop()
        name = []
        for w in reversed(words):
            ws = w.strip(',;:()')
            if ws and ws[0].isupper():
                name.insert(0, ws)
            elif name and ws.lower() in _INNER_WORDS:
                name.insert(0, ws)
            else:
                break
            if len(name) >= 6:
                break
        while name and name[0].lower() in _INNER_WORDS - {'the'}:
            name.pop(0)
        if any(w.lower() not in _NOT_A_NAME and w.lower() not in _INNER_WORDS for w in name):
            return f"{' '.join(name)} {mm.group(1).lower()}"
    named = _count_noun(tail)
    if named:
        return named
    om = _SHARE_OF_RX.match(tail)
    if om:
        return f"share of the {om.group(1).lower()}"
    return ''


_COUNT_NOUNS = frozenset((
    'people', 'persons', 'viewers', 'watchers', 'listeners', 'accounts', 'users', 'individuals', 'subscribers', 'fans',
    'buyers', 'shoppers', 'members', 'searchers', 'visitors', 'customers', 'readers', 'players', 'hour', 'hours',
    'minute', 'minutes', 'session', 'sessions', 'visit', 'visits', 'views', 'plays', 'streams', 'episodes', 'orders',
    'transactions', 'impressions', 'clicks', 'searches', 'posts', 'videos', 'downloads', 'installs', 'signups',
    'sign-ups', 'tickets', 'engagers'))
_NOT_A_MODIFIER = frozenset(('of', 'the', 'a', 'an', 'in', 'on', 'and', 'or', 'to', 'were', 'was', 'are', 'is', 'who',
                             'that', 'which', 'with', 'from', 'at', 'by', 'for', 'than', 'as', 'per', 'across', 'over',
                             'into', 'out', 'up', 'down', 'had', 'have', 'has', 'did', 'do', 'does', 'more', 'less'))
_SHARE_OF_RX = re.compile(r"\s+of\s+(?:the|their|this|that|its)\s+(audience|cohort|base|group|panel|universe)\b", re.I)
_ON_NAME_RX = re.compile(r"\s+(?:on|of|for|in|at|via)\s+(?:the\s+)?[A-Z][\w'&+.-]*(?:\s+[A-Z][\w'&+.-]*){0,3}")


def _count_noun(tail):
    """The noun phrase after a count ('912,447 sessions', '392,312,555
    insurance-active individuals', '64,638,049,641 hours on YouTube'): up
    to three modifier words and a noun that counts something, plus an
    'on <Name>' tail. '' when the words after the count are prose."""
    words = re.findall(r"\S+", str(tail or '')[:120])
    for i, w in enumerate(words[:4]):
        lw = w.lower().strip(',.;:()')
        if lw in _COUNT_NOUNS:
            mods = [x.strip(',;:()') for x in words[:i]]
            if any(re.search(r'\d', x) or x.lower() in _NOT_A_MODIFIER for x in mods):
                return ''
            phrase = ' '.join(mods + [w.strip(',.;:()')])
            if w[-1:] not in ',.;:)':
                rest = str(tail)[str(tail).find(w) + len(w):]
                om = _ON_NAME_RX.match(rest)
                if om:
                    phrase += om.group(0).rstrip(',.;: ')
            return ' '.join(phrase.split())
        if re.search(r'\d', w) or lw in _NOT_A_MODIFIER:
            return ''
    return ''


def _evidence_names(figures):
    """{(value, kind): label} for the figures the read's own evidence carries
    (the composed figure ledger: a real source and a label that names
    something, never 'typed by the model' or a bare measure word)."""
    out = {}
    for f in figures or []:
        if not isinstance(f, dict) or str(f.get('source') or '') in ('', 'model_typed', 'unsourced'):
            continue
        lab = ' '.join(str(f.get('label') or '').split())
        if not lab or not ({w.lower().strip('(),') for w in lab.split()} - _MEASURE_ONLY):
            continue
        try:
            out.setdefault((round(float(f.get('value')), 4), str(f.get('kind') or '')), lab)
        except (TypeError, ValueError):
            continue
    return out


def _evidence_name(evidence, num, unit, tail):
    if not evidence:
        return ''
    kind = 'pct' if unit == 'pct' else ('x' if unit == 'x' else 'count')
    try:
        lab = evidence.get((round(float(num), 4), kind)) or ''
    except (TypeError, ValueError):
        lab = ''
    if not lab:
        return ''
    mm = _MEASURE_AFTER_RX.match(tail or '')
    if mm and mm.group(1).lower() not in lab.lower():
        lab = f"{lab} {mm.group(1).lower()}"
    return lab


_SCALE = {'k': 1e3, 'm': 1e6, 'b': 1e9, 'thousand': 1e3, 'million': 1e6, 'billion': 1e9}


def _numeric(v):
    """('980,197' -> (980197, 'count')), ('58.0%' -> (58.0, 'pct')),
    ('2.1x' -> (2.1, 'x')), ('$1.2M' -> (1200000, 'usd')); (None, '')
    when the token is not a figure."""
    t = str(v or '').strip().replace(',', '')
    unit = 'count'
    if t.startswith('$'):
        unit = 'usd'
        t = t[1:].strip()
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(%|x|pp|[kmb]|thousand|million|billion)?$', t, re.I)
    if not m:
        return None, ''
    num = float(m.group(1))
    suf = (m.group(2) or '').lower()
    if suf == '%':
        unit = 'pct'
    elif suf == 'x':
        unit = 'x'
    elif suf == 'pp':
        unit = 'pp'
    elif suf in _SCALE:
        num *= _SCALE[suf]
    if unit in ('count', 'usd') and float(num).is_integer():
        num = int(num)
    return num, unit


def _with_sources(metrics, figures):
    """Provenance on the ledger's metrics (audit item 4): a metric whose
    value matches a composed figure carries that figure's source; the
    rest are marked model_typed."""
    try:
        by_val = {}
        for f in figures or []:
            try:
                by_val.setdefault(round(float(f.get('value')), 4), f)
            except (TypeError, ValueError):
                continue
        for m in metrics or []:
            try:
                f = by_val.get(round(float(m.get('value')), 4))
            except (TypeError, ValueError):
                f = None
            m['source'] = (f or {}).get('source') or ('model_typed' if figures else 'unsourced')
    except Exception:
        pass
    return metrics


def persist_answer(il, *, subject, question, reply, followups=None,
                   base_profile_key='', window_start='', window_end='',
                   window_label='', route=ANSWER_ROUTE, figures=None):
    """A page answer joins the shared ring: its figures and the reply,
    under the profile's subject and window, so the next ask on that
    page (anyone's) binds to them and an identical ask replays them.
    `figures` (the composed ledger) puts a source on every metric and
    names the figures the read's evidence carries.

    Only a figure the reply or the read's evidence names becomes a binding
    metric; the reply is kept either way, so an identical ask replays it.
    Nothing is written for a reply with no figure, for an answer about
    what a figure means (it restates figures already given), or for a
    reply the ledger holds back (Jenna 2026-11-24: a set-aside answer never
    feeds a later read, for anyone): its words were set aside, or it
    restates figures of a retired read."""
    try:
        if not str(subject or '').strip():
            return False
        explains = getattr(il, 'is_explanation_ask', None)
        if callable(explains) and explains(question):
            return False
        evidence = _evidence_names(figures)
        held = getattr(il, 'held_back', None)
        if callable(held):
            vt = getattr(il, 'value_tokens', None)
            exempt = {vt(v) for v, _k in evidence} - {''} if callable(vt) else set()
            if held(str(reply or ''), subject=str(subject), exempt=exempt):
                return False
        stated = figures_from_reply(reply, limit=40, evidence=evidence)
        if not stated:
            return False
        metrics = _with_sources([m for m in stated if m.get('name')][:14], figures)
        family = ''
        try:
            family = il.family_from_question(question) or ''
        except Exception:
            family = ''
        il.persist(subject=str(subject), metric_family=family or 'profile_read',
                   question=str(question or ''), route=route, metrics=metrics,
                   reply=str(reply or '')[:4000], followups=list(followups or [])[:4],
                   base_profile_key=str(base_profile_key or ''),
                   window_start=str(window_start or ''), window_end=str(window_end or ''),
                   window_label=str(window_label or ''), provenance='operator',
                   sync=_sync())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- sizing
def _window_tuple(w):
    if isinstance(w, dict):
        return str(w.get('start') or ''), str(w.get('end') or '')
    if isinstance(w, (list, tuple)) and len(w) >= 2:
        return str(w[0] or ''), str(w[1] or '')
    return '', ''


def held_sizing(il, subject, window=None, ask=None):
    """(tu, avid, us_audience) the shared ring already holds for this
    subject and window, or None. Two readers sizing the same audience
    in the same window see one sample, whoever asked first. With ``ask``
    (a sample check's own words), a saved audience size for the subject
    and window answers first, unless the ask names a size of its own
    (migration/saved_subject_size, 2026-11-20)."""
    if ask is not None:
        try:
            from migration.saved_subject_size import for_check, projected
            got = for_check(subject, window, 0, 0, text=ask)
            if got:
                return got[0], got[1], projected(got[0])
        except Exception:
            pass
    try:
        ws, we = _window_tuple(window)
        led = il.consult(subject=subject, metric_family=SIZING_FAMILY,
                         window_start=ws or None, window_end=we or None)
        best = None
        for e in reversed(list((led or {}).get('entries') or [])):
            if str(e.get('route') or '') != 'sample_check':
                continue
            ews, ewe = str(e.get('ws') or ''), str(e.get('we') or '')
            if (ws or we) and (ews, ewe) != (ws, we):
                continue
            vals = {str(m.get('name') or ''): m.get('value') for m in (e.get('metrics') or [])}
            tu = _int(vals.get('subject_raw_tu'))
            if tu:
                best = (tu, _int(vals.get('subject_raw_avid')), _int(vals.get('us_audience_estimate')))
                break
        return best
    except Exception:
        return None


def persist_sizing(il, *, subject, window=None, tu, avid=0, us_audience=0,
                   question='', base_profile_key=''):
    """A sizing joins the shared ring under its subject and window."""
    try:
        tu = _int(tu)
        if not tu or not str(subject or '').strip():
            return False
        ws, we = _window_tuple(window)
        # A sizing counts people with at least one digital touchpoint with
        # the subject in the window. The count in the data (tu) is the
        # unprojected figure behind the US audience; it is stored on each
        # metric so a later reconcile ask reads it back (2026-11-04).
        universe = f"US people with at least one digital touchpoint with {subject} in the window"
        metrics = [{'name': 'subject_raw_tu', 'label': 'Audience in window', 'value': tu, 'unit': 'individuals',
                    'definition': f'People in the data with at least one digital touchpoint with {subject} over the window (unprojected).',
                    'sample_people': tu}]
        if _int(avid):
            metrics.append({'name': 'subject_raw_avid', 'label': 'Avid tier', 'value': _int(avid), 'unit': 'individuals',
                            'definition': 'The most engaged tier of the audience, people in the data (unprojected).',
                            'sample_people': _int(avid)})
        if _int(us_audience):
            metrics.append({'name': 'us_audience_estimate', 'label': 'US audience', 'value': _int(us_audience),
                            'unit': 'individuals', 'definition': universe + ', projected to the US.',
                            'sample_people': tu})
        il.persist(subject=str(subject), metric_family=SIZING_FAMILY, question=str(question or f'size {subject}'),
                   route='sample_check', metrics=metrics, window_start=ws, window_end=we,
                   base_profile_key=str(base_profile_key or ''), provenance='operator', sync=_sync(),
                   universe=universe)
        return True
    except Exception:
        return False


def _int(v):
    try:
        return int(round(float(str(v).replace(',', ''))))
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------- notes
NOTES_SYSTEM = (
    "You keep short private notes about one reader of an analytics chat. "
    "From the question and the answer, write at most three notes worth "
    "remembering next time this person asks something: what they are "
    "working on, who they work for or with, how they like answers "
    "(currency, format, depth), a decision they made. Plain sentences, "
    "each under 140 characters. Never write a number, a price, a figure "
    "or a statistic; never restate the answer. Return JSON: "
    '{"notes": ["...", "..."]}. Return {"notes": []} when nothing is worth keeping.')


def distill_notes(claude, *, user, question, reply, subject='', thread_id=''):
    """Private notes for the individual ring, via one small model call
    (``claude(system, user, **kw) -> dict``). Figures never make it in:
    anything with a digit is dropped. Returns the list written."""
    try:
        if not user or not str(question or '').strip() or not callable(claude):
            return []
        prompt = (f"Subject on screen: {subject or 'none'}\n\nQUESTION:\n{str(question)[:800]}\n\n"
                  f"ANSWER (for context only; do not copy figures):\n{str(reply or '')[:1600]}")
        data = claude(NOTES_SYSTEM, prompt, max_tokens=300, temperature=0.1, surface='reader_notes') or {}
        notes = data.get('notes') if isinstance(data, dict) else None
        clean = []
        for n in (notes or [])[:3]:
            t = ' '.join(str(n or '').split()).strip(' .')
            if not t or len(t) < 8 or re.search(r'\d', t):
                continue
            clean.append(t[:140] + '.')
        if clean:
            add_notes(user, clean, subject=subject, thread_id=thread_id)
        return clean
    except Exception:
        return []


def _thread_now():
    try:
        from . import thread_scope as _tsc
        return _tsc.current_thread_id()
    except Exception:
        return ''


def add_notes(user, notes, subject='', thread_id=''):
    """Append notes to the reader's memory file (newest first, capped,
    deduped case-insensitively, 90-day expiry on read), stamped with the
    thread they were written in."""
    try:
        import prometheus_memory as pmm
    except Exception:
        return False
    now = time.time()
    tid = str(thread_id or '') or _thread_now()
    new = [{'text': str(n)[:160], 'ts': now, 'subject': str(subject or '')[:120], 'thread': tid[:96]}
           for n in notes if str(n or '').strip()]
    if not new:
        return False

    def _mut(doc):
        doc = doc if isinstance(doc, dict) else {}
        cur = [x for x in (doc.get('notes') or []) if isinstance(x, dict)
               and now - float(x.get('ts') or 0) < NOTES_TTL_S]
        have = {str(x.get('text') or '').casefold() for x in cur}
        merged = [n for n in new if n['text'].casefold() not in have] + cur
        doc['notes'] = merged[:NOTES_MAX]
        return doc
    try:
        pmm._update(user, _mut)
        return True
    except Exception:
        return False


def recall_notes(user, k=8):
    """The reader's newest notes from this thread, newest first. A note
    written in another chat is never read into this one (2026-11-11)."""
    try:
        import prometheus_memory as pmm
        doc, _etag = pmm._read_with_etag(user)
    except Exception:
        return []
    try:
        from . import thread_scope as _tsc
        _mine = _tsc.mine
    except Exception:
        _mine = lambda _x: True   # noqa: E731
    now = time.time()
    out = []
    for x in (doc or {}).get('notes') or []:
        if isinstance(x, dict) and now - float(x.get('ts') or 0) < NOTES_TTL_S and str(x.get('text') or '').strip() \
                and _mine(x):
            out.append(str(x['text']))
        if len(out) >= k:
            break
    return out


# Standing preferences (2026-10-13 audit). A reader who says "always",
# "from now on", "going forward", "every time", "I prefer", "never ... me"
# is setting a rule for every later answer and deliverable, not giving a
# one-time instruction. Captured without a model call, kept without
# expiry, shown first in the who-is-asking block as rules to follow.
_PREF_RX = re.compile(
    r"(?:^|[.;,]\s*)((?:please\s+)?(?:always|never|from now on|going forward|every time|each time|whenever|"
    r"in (?:the )?future|by default|i (?:always )?prefer|i'd prefer|i would prefer|don't ever|do not ever|stop (?:adding|sending|including|asking))"
    r"[^.;\n]{6,160})", re.I)
_PREF_MAX = 12


def extract_preferences(text):
    """Standing-rule sentences in a message, cleaned, no figures required."""
    out = []
    for m in _PREF_RX.finditer(str(text or '')):
        t = ' '.join(m.group(1).split()).strip(' .;,')
        if len(t) < 12 or re.search(r'\?$', t):
            continue
        if re.match(r'(?i)^(?:always|never)\s+(?:a|an|the)\b', t) and len(t.split()) < 5:
            continue
        out.append(t[0].upper() + t[1:] + '.')
    return out[:3]


def strip_preferences(text):
    """The message with its standing-rule sentences removed, when a real
    ask remains (three or more words); otherwise the original text. Keeps a
    preference from steering the routing of the question beside it."""
    t = str(text or '')
    prefs = extract_preferences(t)
    if not prefs:
        return t
    rest = t
    for m in list(_PREF_RX.finditer(t))[::-1]:
        a, b = m.span(1)
        # take the sentence terminator with it
        while b < len(rest) and rest[b] in '.;!':
            b += 1
        rest = rest[:a] + rest[b:]
    rest = re.sub(r'\s+', ' ', rest).strip(' ,;.')
    return rest if len(rest.split()) >= 3 else t


def capture_preferences(user, text):
    """Store any standing preferences in the message. Returns the list kept."""
    prefs = extract_preferences(text)
    if not (user and prefs):
        return []
    try:
        import prometheus_memory as pmm
    except Exception:
        return []
    now = time.time()

    def _mut(doc):
        doc = doc if isinstance(doc, dict) else {}
        cur = [x for x in (doc.get('preferences') or []) if isinstance(x, dict)]
        have = {str(x.get('text') or '').casefold() for x in cur}
        new = [{'text': p[:200], 'ts': now} for p in prefs if p.casefold() not in have]
        doc['preferences'] = (new + cur)[:_PREF_MAX]
        return doc
    try:
        pmm._update(user, _mut)
        return prefs
    except Exception:
        return []


def recall_preferences(user, k=_PREF_MAX):
    try:
        import prometheus_memory as pmm
        doc, _etag = pmm._read_with_etag(user)
    except Exception:
        return []
    out = []
    for x in (doc or {}).get('preferences') or []:
        if isinstance(x, dict) and str(x.get('text') or '').strip():
            out.append(str(x['text']))
        if len(out) >= k:
            break
    return out


def preferences_block(user):
    prefs = recall_preferences(user)
    if not prefs:
        return ''
    return ('STANDING PREFERENCES FROM THIS READER (rules; follow them on every answer and every file, '
            'without repeating them back):\n' + '\n'.join(f'- {p}' for p in prefs))


def notes_block(user, k=8):
    """Prompt block for the who-is-asking section, '' when none.
    Standing preferences lead; private notes follow."""
    parts = []
    pb = preferences_block(user)
    if pb:
        parts.append(pb)
    notes = recall_notes(user, k=k)
    if notes:
        parts.append('ABOUT THIS READER (private notes from earlier in this conversation; tone and defaults only, never echoed):\n' +
                     '\n'.join(f'- {n}' for n in notes))
    return '\n\n'.join(parts)


def after_answer(il, claude, *, user, question, reply, subject='', followups=None,
                 base_profile_key='', window_start='', window_end='', window_label='', figures=None):
    """Both rings, after a page answer: the figures to the shared
    ledger (each with its source), a few notes to the reader's own
    memory. Non-blocking."""
    _tid = _thread_now()   # read here, on the request: the notes are written off it

    def _go():
        persist_answer(il, subject=subject, question=question, reply=reply, followups=followups,
                       base_profile_key=base_profile_key, window_start=window_start,
                       window_end=window_end, window_label=window_label, figures=figures)
        distill_notes(claude, user=user, question=question, reply=reply, subject=subject, thread_id=_tid)
    _run(_go)
