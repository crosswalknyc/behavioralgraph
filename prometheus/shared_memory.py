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


def _sync():
    return os.environ.get('REGRESSION_TEST_MODE') == '1'


def _run(fn):
    if _sync():
        fn()
        return
    threading.Thread(target=fn, daemon=True).start()


# --------------------------------------------------------------- figures
def figures_from_reply(reply, limit=14):
    """[{name, value, definition}] for every figure a reply states, each
    with the sentence that carried it (the ledger's binding block shows
    the sentence, so the next answer reuses the figure in context).
    Prices, credits and bare years are skipped."""
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
            # a short label: the words right before the figure
            head = s[:m.start()].strip()
            label = ' '.join(head.split()[-6:]).strip(' ,;:(') or 'figure'
            out.append({'name': label[:48] or 'figure', 'label': label[:80], 'value': num,
                        'unit': unit, 'definition': s[:220]})
            if len(out) >= limit:
                return out
    return out


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
    Nothing is written for a reply with no figures. `figures` (the
    composed ledger) puts a source on every metric."""
    try:
        metrics = _with_sources(figures_from_reply(reply), figures)
        if not metrics or not str(subject or '').strip():
            return False
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


def held_sizing(il, subject, window=None):
    """(tu, avid, us_audience) the shared ring already holds for this
    subject and window, or None. Two readers sizing the same audience
    in the same window see one sample, whoever asked first."""
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
        metrics = [{'name': 'subject_raw_tu', 'label': 'Audience in window', 'value': tu, 'unit': 'individuals',
                    'definition': 'Individuals in the audience over the window.'}]
        if _int(avid):
            metrics.append({'name': 'subject_raw_avid', 'label': 'Avid tier', 'value': _int(avid), 'unit': 'individuals',
                            'definition': 'The avid tier of the audience.'})
        if _int(us_audience):
            metrics.append({'name': 'us_audience_estimate', 'label': 'US audience', 'value': _int(us_audience),
                            'unit': 'individuals', 'definition': 'US audience in the window.'})
        il.persist(subject=str(subject), metric_family=SIZING_FAMILY, question=str(question or f'size {subject}'),
                   route='sample_check', metrics=metrics, window_start=ws, window_end=we,
                   base_profile_key=str(base_profile_key or ''), provenance='operator', sync=_sync())
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


def distill_notes(claude, *, user, question, reply, subject=''):
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
            add_notes(user, clean, subject=subject)
        return clean
    except Exception:
        return []


def add_notes(user, notes, subject=''):
    """Append notes to the reader's memory file (newest first, capped,
    deduped case-insensitively, 90-day expiry on read)."""
    try:
        import prometheus_memory as pmm
    except Exception:
        return False
    now = time.time()
    new = [{'text': str(n)[:160], 'ts': now, 'subject': str(subject or '')[:120]} for n in notes if str(n or '').strip()]
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
    """The reader's newest notes, newest first."""
    try:
        import prometheus_memory as pmm
        doc, _etag = pmm._read_with_etag(user)
    except Exception:
        return []
    now = time.time()
    out = []
    for x in (doc or {}).get('notes') or []:
        if isinstance(x, dict) and now - float(x.get('ts') or 0) < NOTES_TTL_S and str(x.get('text') or '').strip():
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
        parts.append('ABOUT THIS READER (private notes from earlier conversations; tone and defaults only, never echoed):\n' +
                     '\n'.join(f'- {n}' for n in notes))
    return '\n\n'.join(parts)


def after_answer(il, claude, *, user, question, reply, subject='', followups=None,
                 base_profile_key='', window_start='', window_end='', window_label='', figures=None):
    """Both rings, after a page answer: the figures to the shared
    ledger (each with its source), a few notes to the reader's own
    memory. Non-blocking."""
    def _go():
        persist_answer(il, subject=subject, question=question, reply=reply, followups=followups,
                       base_profile_key=base_profile_key, window_start=window_start,
                       window_end=window_end, window_label=window_label, figures=figures)
        distill_notes(claude, user=user, question=question, reply=reply, subject=subject)
    _run(_go)
