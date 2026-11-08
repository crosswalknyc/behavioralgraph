"""Reconciliation from the record (Jenna 2026-11-04, Jay Patel's Statham
thread).

Jay, with the Jason Statham profile open: "Your three answers to the
Statham question give 6.2M, 8.5M and 43.2M total viewers, and 46.4%,
58.2% and 38.6% not on Prime Video. Reconcile them. State one universe
definition, one window, and the unprojected panel sample size." The
sizing lane answered with three sizing blocks of the open profile and
of the words "window" and "unprojected panel" (100.1M people in the US,
"audience size for window (no subject provided)", "General US Digital
Data ... ~312.2M"). Jenna: "the data should always be storing it so that
it reconciles and should always keep track ... this needs to be really
well reasoned."

A reconciliation ask is answered FROM THE RECORD of what Prometheus
already said: the agent turns of the thread and the ledger entries that
carry the figures (their definitions, windows, bases and the count in the
data behind each projected figure). Never a fresh sizing, never a model
call, never a charge. The reply:

  1. what each quoted figure counted: its universe, its window, the
     line that carried it, and the people in the data behind it;
  2. the one definition and the one window to use from here, chosen by
     reason: the read that names the asked subject itself, on a full
     window, and the lowest of the totals when more than one qualifies
     (calibrate low when a choice must be made);
  3. the figures restated on that basis, with the unprojected count;
  4. why each other figure does not replace it (a different audience, a
     different window, a different cut of the share).

Vocabulary: plain. The count before projection is "the people in the
data behind the figure"; never sample, panel, model, estimate, research,
pipeline or ledger. No em dashes.

Runs in prometheus.service.ask ahead of number_provenance (a single
"where does this figure come from" is provenance; two or more figures
that must agree is a reconciliation), before the continuation gate, the
Reader, the sizing lanes and the open-profile binding. Kill switch
``PM_RECONCILE_LANE=0``. Pure text plus the thread and the ledger; no
Flask.
"""
from __future__ import annotations

import os
import re
import traceback

from . import stated_numbers as _sn

try:
    from insights_ledger import US_PER_SAMPLE
except Exception:   # pragma: no cover - the ledger module is always present on the dashboard
    US_PER_SAMPLE = 32.99

__all__ = ['wants_reconcile', 'quoted_figures', 'record', 'answer', 'enabled', 'HEADER']

HEADER = 'What each figure counted:'
_ONE_HEADER = 'One definition from here:'

# cues that stand on their own
_STRONG_RX = re.compile(
    r"\bone (?:universe|audience|base|population|cohort) (?:definition|and (?:one )?window)\b|"
    r"\bstate (?:one|a single|the) (?:universe|definition|window|base|audience)\b|"
    r"\b(?:same|single|one) (?:universe|definition) (?:and|,) (?:one |the same |a single )?window\b", re.I)
# "unprojected" / "before projection" reconcile the figures the ask
# quotes or the answers it points back to; alone, in a question about
# how a count is made ("what is the raw count before projection?"), they
# are a methodology question and the provenance answer takes it (Grace
# Yin 2026-11-08).
_UNPROJECTED_RX = re.compile(r"\bunprojected\b|\bbefore projection\b", re.I)
# cues that need two quoted figures or a reference to earlier answers
_CUE_RX = re.compile(
    r"\breconcil(?:e|ed|ing|iation)\b|"
    r"\b(?:do not|don'?t|doesn'?t|does not|cannot|can'?t|won'?t|will not|did not|didn'?t) (?:agree|match|reconcile|line up|add up|square|tie out|tie)\b|"
    r"\b(?:disagree|contradict(?:s|ed|ory|ion)?|inconsistent|conflicting|mismatch(?:ed|es)?)\b|"
    r"\bwhich (?:of (?:these|those|them)|one|number|figure|total|answer) is (?:right|correct|the right one|the one to use)\b|"
    r"\bwhy (?:do|are|did|would) (?:these|those|they|the (?:numbers|figures|totals|answers)) (?:differ|different|not (?:the same|agree|match)|so different|so far apart)\b|"
    r"\bone window\b|\bone (?:universe|definition|base)\b|\bsame (?:universe|definition|basis)\b", re.I)
_PRIOR_RX = re.compile(
    r"\byour (?:\w+ )?(?:answers?|numbers?|figures?|replies|reads?|totals?|counts?)\b|"
    r"\byou (?:gave|said|told|quoted|stated|reported|wrote|answered|showed|sent)\b|"
    r"\b(?:earlier|previous|prior|last|above|before|first|second|third) (?:answer|reply|read|number|figure|total|time)\b|"
    r"\b(?:three|two|3|2) (?:different )?(?:answers|numbers|figures|totals|reads)\b|"
    r"\b(?:these|those|them|the) (?:numbers|figures|totals|answers|counts|shares|reads)\b|\bwhich of (?:these|those|them)\b|"
    r"\b(?:these|those|they) (?:differ|are different|do not|don'?t|disagree)\b", re.I)
# a box office reconcile is the standing refusal's (Jenna 2026-10-05: we never predict or reconcile to box
# office, however the ask is put); that lane answers it with the ticketing-site count, not this one
_NOT_RX = re.compile(r"\b(?:deck|slides?|email (?:me|it)|send me|download|csv|build a profile|run a profile|journey|valuation|box[- ]?office|gross(?:es|ed)?)\b", re.I)
# a turn this lane wrote is never part of the record it reads
_OWN_RX = re.compile(re.escape(HEADER) + r"|" + re.escape(_ONE_HEADER))
_PROMISE_RX = re.compile(r"^\s*On it\b|\bthe read will land right here\b|\bWorking on it\b|^\s*Already on it\b", re.I)
_READ_HEAD_RX = re.compile(r"^On\s+(?P<who>[^:\n]{1,160}?)\s*:", re.I)
_WINDOW_LINE_RX = re.compile(r"(?m)^\s*-?\s*Window:\s*(?P<w>[^\n]+?)\.?\s*$")
_BASE_LINE_RX = re.compile(r"(?m)^\s*-?\s*Base:\s*(?P<b>[^\n]+?)(?:,\s*measured across[^\n]*)?\.?\s*$")
_SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")
_CALC_RX = re.compile(r"\s*\(?\[calc:[^\]]*\]\)?", re.I)
_STOP = frozenset((
    'the', 'a', 'an', 'of', 'for', 'on', 'in', 'and', 'or', 'to', 'at', 'by', 'with', 'from', 'that', 'this',
    'these', 'those', 'them', 'they', 'your', 'you', 'our', 'we', 'is', 'are', 'was', 'were', 'be', 'it', 'its',
    'not', 'no', 'one', 'two', 'three', 'four', 'answers', 'answer', 'question', 'questions', 'give', 'gave', 'total',
    'totals', 'viewers', 'viewer', 'people', 'users', 'reconcile', 'state', 'universe', 'definition', 'window', 'windows',
    'unprojected', 'panel', 'sample', 'size', 'number', 'numbers', 'figure', 'figures', 'which', 'why', 'what', 'how',
    'do', 'does', 'did', 'same', 'different', 'differ', 'agree', 'match', 'video', 'prime', 'us', 'audience', 'read', 'reads',
    'data', 'in', 'as', 'all', 'any', 'each', 'other', 'their', 'his', 'her', 'who', 'watched', 'watch', 'streaming',
    'service', 'services', 'free', 'supported', 'ad', 'share', 'pct', 'percent', 'base', 'basis', 'window'))
# words a reader never hears from this lane, however the record spelled them
_WORD_SWAPS = (
    (re.compile(r"\bpanelists?\b", re.I), 'people'),
    (re.compile(r"\bthe panel\b", re.I), 'the data'),
    (re.compile(r"\bpanel\b", re.I), 'data'),
    (re.compile(r"\bsample size\b", re.I), 'count'),
    (re.compile(r"\bthe sample\b", re.I), 'the data'),
    (re.compile(r"\bsample\b", re.I), 'data'),
    (re.compile(r"\b(?:estimated|modeled|modelled|synthetic|synthesized|synthesised|derived|researched|reasoned) ", re.I), ''),
    (re.compile(r"\bmeasured across 10M US consumers and projected to the US\b", re.I), 'projected to the US'),
)


def enabled():
    return str(os.environ.get('PM_RECONCILE_LANE', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


# ------------------------------------------------------------- detection
def quoted_figures(text):
    """The figures the ask quotes, in order: [{'raw', 'value', 'kind'
    ('count' | 'pct'), 'tol'}]. An abbreviated figure (6.2M) matches a
    record value to half its last written digit (6.15M to 6.25M); a full
    figure (6,195,357) matches exactly; a percent to 0.05 of a point."""
    out = []
    for it in _sn.stated(text):
        unit = str(it.get('unit') or '')
        raw = str(it.get('raw') or '').strip()
        if unit == '%':
            dec = len(raw.split('.')[1].rstrip('%').strip()) if '.' in raw else 0
            out.append({'raw': raw, 'value': float(it['value']), 'kind': 'pct', 'tol': 0.5 * (10 ** -dec) if dec else 0.5})
            continue
        if unit == '$':
            continue
        try:
            v = int(it['value'])
        except (TypeError, ValueError):
            continue
        if v < 1000:
            continue
        if unit in ('K', 'M', 'B'):
            head = re.match(r"^\$?([\d,]+(?:\.(\d+))?)", raw)
            dec = len(head.group(2)) if head and head.group(2) else 0
            mult = {'K': 1e3, 'M': 1e6, 'B': 1e9}[unit]
            tol = 0.5 * (10 ** -dec) * mult
        else:
            tol = 0.5
        out.append({'raw': raw, 'value': v, 'kind': 'count', 'tol': tol})
    return out


def wants_reconcile(text):
    """A question that asks for figures Prometheus gave to be squared
    with one another, or for the one definition, window and unprojected
    count behind them."""
    if not enabled():
        return False
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 900 or _NOT_RX.search(t):
        return False
    # A numbered list of two or more questions is several asks; each
    # routes on its own through prometheus.multi_ask (Grace Yin
    # 2026-11-08: four numbered questions on how a Prime activation and
    # a play are detected were taken whole as a reconciliation because
    # one of them said "before projection", and came back as the record
    # of one figure).
    try:
        from . import multi_ask as _ma
        if len(_ma.numbered_items(text)) >= 2:
            return False
    except Exception:
        pass
    if _STRONG_RX.search(t):
        return True
    figs = quoted_figures(t)
    if _UNPROJECTED_RX.search(t):
        return bool(figs) or bool(_PRIOR_RX.search(t))
    if not _CUE_RX.search(t):
        return False
    return len(figs) >= 2 or bool(_PRIOR_RX.search(t))


# ---------------------------------------------------------------- record
def _clean(s, limit=0):
    x = _CALC_RX.sub('', str(s or ''))
    x = ' '.join(x.split()).strip(' ,;:')
    for rx, rep in _WORD_SWAPS:
        x = rx.sub(rep, x)
    x = re.sub(r"\s{2,}", ' ', x).strip()
    if limit and len(x) > limit:
        x = x[:limit].rsplit(' ', 1)[0].rstrip(' ,;:(')
    # a definition cut at the store's limit ends on a dangling word
    x = re.sub(r"[,;:]?\s+(?:and|or|a|an|the|of|on|in|for|with|to|from|at|by|jul|aug|sep|oct|nov|dec|jan|feb|mar|apr|may|jun)$", '', x, flags=re.I)
    return x


def _fmt_int(v):
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v):
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _is_pct_unit(unit):
    return bool(re.search(r"pct|percent|%|share", str(unit or ''), re.I))


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9']+", str(s or '').lower()) if len(w) > 2 and w not in _STOP and not w.isdigit()}


def _agent_turns(history):
    out = []
    for i, t in enumerate(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '').lower() not in ('agent', 'assistant'):
            continue
        txt = str(t.get('text') or t.get('content') or '')
        if not txt.strip() or _OWN_RX.search(txt) or _PROMISE_RX.search(txt[:120]):
            continue
        out.append((i, t, txt))
    return out


_DATE_TXT_RX = re.compile(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+(\d{4})\b")


def _parse_text_date(mon, day, year):
    from datetime import datetime as _dt
    for fmt in ('%b %d %Y', '%B %d %Y'):
        try:
            return _dt.strptime(f"{mon} {day} {year}", fmt).date()
        except ValueError:
            continue
    return None


def _window_days(entry):
    """The length in days of an entry's window (ws/we) or of a window
    written in words ('Jul 1 2022 to Jun 30 2023', 'June 2023'); 0 when
    unknown."""
    try:
        from datetime import date as _d
        a = _d.fromisoformat(str(entry.get('ws') or '')[:10])
        b = _d.fromisoformat(str(entry.get('we') or '')[:10])
        return (b - a).days + 1
    except Exception:
        pass
    w = str(entry.get('window') or '')
    if re.search(r"\b12 months\b|\btrailing year\b|\bpast year\b|\blast year\b", w, re.I):
        return 365
    ds = [_parse_text_date(*m.groups()) for m in _DATE_TXT_RX.finditer(w)]
    ds = [d for d in ds if d]
    if len(ds) >= 2:
        return abs((ds[1] - ds[0]).days) + 1
    if re.match(r"^\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*$", w):
        return 30
    return 0


def _all_entries(ledger):
    """Every ledger entry, newest first; [] when the ledger is away."""
    try:
        doc = ledger._load_index() if ledger is not None else __import__('insights_ledger')._load_index()
    except Exception:
        return []
    out = []
    for sk, bucket in ((doc or {}).get('subjects') or {}).items():
        for e in (bucket or {}).get('entries') or []:
            if isinstance(e, dict) and not e.get('retired'):
                out.append(dict(e, _bucket=sk, _bucket_subject=str((bucket or {}).get('subject') or '')))
    out.sort(key=lambda e: str(e.get('ts') or ''), reverse=True)
    return out


def _entry_for_turn(txt, entries):
    """The ledger entry that banked this agent turn: the one whose stored
    reply opens the same way, else the newest whose count metrics all
    appear in the turn."""
    head = ' '.join(txt.split())[:160].lower()
    for e in entries:
        rep = ' '.join(str(e.get('reply') or '').split())[:160].lower()
        if rep and head and rep == head:
            return e
    best = None
    for e in entries:
        vals = [m.get('value') for m in (e.get('metrics') or []) if isinstance(m, dict) and not _is_pct_unit(m.get('unit'))]
        vals = [v for v in vals if isinstance(v, (int, float)) and v >= 1000]
        if vals and all(_fmt_int(v) in txt for v in vals):
            best = e
            break
    return best


def _entry_window(e):
    w = str(e.get('wl') or '').strip()
    if w:
        return w
    if e.get('ws') and e.get('we'):
        return f"{e.get('ws')} to {e.get('we')}"
    return ''


def _universe_of(e, head=''):
    u = str((e or {}).get('universe') or '').strip()
    if u:
        return _clean(u)
    subj = str((e or {}).get('subject') or '').strip()
    coh = str((e or {}).get('cohort') or '').strip()
    if subj and coh and coh.lower() not in subj.lower():
        return _clean(f"{subj}, {coh}")
    return _clean(subj or head)


def record(history, ledger=None):
    """The record: one dict per agent turn that carried figures:
    {'turn', 'head', 'window', 'base', 'sentences', 'figures': [{'value',
    'raw', 'kind', 'sentence', 'metric'}], 'entry', 'universe'}."""
    entries = _all_entries(ledger)
    recs = []
    for i, t, txt in _agent_turns(history):
        head_m = _READ_HEAD_RX.match(txt.strip())
        head = head_m.group('who').strip() if head_m else ''
        wm = _WINDOW_LINE_RX.search(txt)
        bm = _BASE_LINE_RX.search(txt)
        entry = _entry_for_turn(txt, entries)
        sents = [s.strip() for s in _SENT_SPLIT_RX.split(txt) if s.strip()]
        figs = []
        for s in sents:
            for it in _sn.stated(s):
                unit = str(it.get('unit') or '')
                if unit == '$':
                    continue
                kind = 'pct' if unit == '%' else 'count'
                try:
                    val = float(it['value'])
                except (TypeError, ValueError):
                    continue
                if kind == 'count' and val < 1000:
                    continue
                figs.append({'value': val, 'raw': str(it.get('raw') or ''), 'kind': kind, 'sentence': s, 'metric': None})
        # the ledger's metrics name and define the figures they match
        for f in figs:
            for m in ((entry or {}).get('metrics') or []):
                if not isinstance(m, dict):
                    continue
                mp = _is_pct_unit(m.get('unit'))
                if mp != (f['kind'] == 'pct'):
                    continue
                try:
                    mv = float(m.get('value'))
                except (TypeError, ValueError):
                    continue
                if abs(mv - f['value']) <= (0.051 if mp else 0.5):
                    f['metric'] = m
                    break
        if not figs:
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        recs.append({
            'turn': i, 'head': head or str(meta.get('subject') or ''),
            'window': (wm.group('w').strip() if wm else '') or _entry_window(entry or {}),
            'base': _clean(bm.group('b')) if bm else '',
            'sentences': sents, 'figures': figs, 'entry': entry,
            'universe': _universe_of(entry, head or str(meta.get('subject') or '')),
            'window_days': _window_days(entry or {'window': (wm.group('w') if wm else '')}),
        })
    return recs


# ---------------------------------------------------------------- answer
def _match(q, f):
    if q['kind'] != f['kind']:
        return False
    return abs(float(f['value']) - float(q['value'])) <= float(q['tol'])


def _find(q, recs, prefer_turns=()):
    """(rec, fig) for a quoted figure: a turn already chosen for a
    partner figure first (same sentence first), then the earliest turn
    that carried it as a stored metric, then the earliest that carried
    it at all."""
    hits = []
    for r in recs:
        for f in r['figures']:
            if _match(q, f):
                hits.append((r, f))
    if not hits:
        return None, None
    for r, f in hits:
        if r['turn'] in prefer_turns:
            return r, f
    with_metric = [(r, f) for r, f in hits if f.get('metric')]
    pool = with_metric or hits
    pool.sort(key=lambda rf: rf[0]['turn'])
    return pool[0]


def _sample_people(fig, rec):
    m = fig.get('metric') or {}
    sp = m.get('sample_people')
    if isinstance(sp, (int, float)) and sp > 0:
        return int(sp)
    if str(fig.get('kind') or '') == 'pct' or _is_pct_unit(m.get('unit')):
        # a share has no people count of its own ("100%" is not 3 people;
        # Grace Yin 2026-11-08 read "Unprojected: 3 people in the data
        # behind the 100")
        return None
    e = rec.get('entry') or {}
    if str(e.get('route') or '') == 'sample_check':
        for mm in (e.get('metrics') or []):
            if str(mm.get('name') or '') == 'subject_raw_tu' and isinstance(mm.get('value'), (int, float)):
                return int(mm['value'])
    try:
        return max(1, int(round(float(fig['value']) / US_PER_SAMPLE)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _definition(fig, rec):
    m = fig.get('metric') or {}
    d = _clean(m.get('definition') or '', 220)
    uni = rec.get('universe') or ''
    if d and d.lower() != uni.lower() and d.lower() != str(m.get('label') or '').lower():
        return d
    return ''


def _label(fig):
    return _clean((fig.get('metric') or {}).get('label') or '')


_VERB_CUT_RX = re.compile(
    r"\s+(?:runs?|ran|is|was|were|are|counts?|counted|reach(?:es|ed)?|stands?|stood|sits?|sat|comes?|came|totals?|totaled|"
    r"holds?|held|numbers?|numbered|lands?|landed|measures?|measured|came in|comes in|at|of)\s*$", re.I)


_LEAD_STOP_RX = re.compile(r"^(?:the|of the|of|about|roughly|around|some|only|just|over|under|nearly|almost)(?:\s+|$)", re.I)


def _noun_phrase_ok(phrase):
    words = str(phrase or '').split()
    if not (2 <= len(words) <= 10) or re.search(r"\d", phrase):
        return False
    return any(len(w) >= 3 and w.lower().strip("'") not in _STOP | {'who', 'that', 'with', 'from'} for w in words)


def _sentence_subject(sentence, raw):
    """The audience a sentence names around its figure: the noun phrase
    ahead of it ('The Statham film audience runs 43,186,927 US people'
    -> 'Statham film audience'), else the one right after it ('Of the
    8,528,344 US people who watched Extraction on Netflix, ...' -> 'US
    people who watched Extraction on Netflix'); '' when neither is a
    short noun phrase."""
    s = str(sentence or '')
    pos = s.find(str(raw or ''))
    if pos < 0:
        return ''
    before = ' '.join(s[:pos].split())
    before = re.sub(r"^\s*(?:\d+\.\s*)?", '', before)          # a list number
    before = re.sub(r"^(?:[A-Z][^.]{0,80}\.\s+)", '', before)    # a short sentence in front
    before = _VERB_CUT_RX.sub('', before).strip(' ,;:')
    for _ in range(2):
        before = _LEAD_STOP_RX.sub('', before).strip()
    if _noun_phrase_ok(before):
        return before
    after = ' '.join(s[pos + len(str(raw or '')):].split())
    after = re.split(r",|;|:|\(|\bin the window\b|\bin (?:june|july|august|september|october|november|december|january|february|march|april|may)\b|"
                     r"\b(?:also|and|but|while|whereas|which|did|had|were|was|are|is)\b", after, maxsplit=1, flags=re.I)[0]
    after = after.strip(' .,;:')
    if _noun_phrase_ok(after):
        return after
    return ''


def _pct_clause(f):
    """The clause of the sentence that carries a share ('16,670,153 of
    them (38.6%) never opened Prime Video'; 'Did not watch Statham on
    Prime Video (1 second or more): 58.2%'); '' when the clause would be
    the whole sentence."""
    s, raw = str(f.get('sentence') or ''), str(f.get('raw') or '')
    parts = re.split(r";\s+|,\s+(?=(?:and|but|while|with)\b)|,\s+(?=[A-Z][a-z])", s)
    for clause in parts:
        if raw and raw in clause:
            c = clause.strip(' .;,')
            c = re.sub(r"^(?:and|but|while|with|so)\s+", '', c, flags=re.I)
            if len(parts) == 1 or ' '.join(c.split()) == ' '.join(s.strip(' .;,').split()):
                return ''
            return _clean(c, 160)
    return ''


def _count_sentences(block):
    """Every sentence in the block's turn that carries its count."""
    v = float(block['count']['value'])
    return {f['sentence'] for f in block['rec']['figures'] if f['kind'] == 'count' and abs(float(f['value']) - v) <= 0.5}


def _pair_pcts(blocks, quoted_pcts, used):
    """Each quoted percent joins the count it was stated with: the count
    whose sentence carries it first (6,195,357 and 58.2% on one line),
    then any count from the same turn. Each percent is used once."""
    for q in quoted_pcts:
        if id(q) in used:
            continue
        hit = None
        for b in blocks:
            sents = _count_sentences(b)
            f = next((f for f in b['rec']['figures'] if _match(q, f) and f['sentence'] in sents), None)
            if f is not None:
                hit = (b, f)
                break
        if hit is None:
            for b in blocks:
                f = next((f for f in b['rec']['figures'] if _match(q, f)), None)
                if f is not None:
                    hit = (b, f)
                    break
        if hit is not None:
            hit[0]['pcts'].append((q, hit[1]))
            used.add(id(q))


def _asked_tokens(text):
    return _tokens(text)


def _score(block, asked):
    uni = _tokens(block['universe'])
    sent = _tokens(block['count']['sentence'])
    subj = len(uni & asked) + 0.5 * len((sent - uni) & asked)
    return (subj, 1 if block['window_days'] >= 300 else 0, -float(block['count']['value']))


def _cap(s):
    s = str(s or '')
    return s[:1].upper() + s[1:] if s else s


def _audience_name(b):
    """What the block's figure is a count of, as the reader would say it:
    the stored metric's label, else the noun phrase the sentence named
    ahead of the figure, else the read's universe."""
    cnt = b['count']
    if cnt.get('metric'):
        return _label(cnt) or b['universe']
    return _sentence_subject(cnt['sentence'], cnt['raw']) or b['universe']


def _share_text(q, f, rec, sents):
    """One sentence on a share: its stored label and definition, else the
    clause that carried it."""
    pl, pd = _label(f), _definition(f, rec)
    if pl and pd:
        return f"The {q['raw']} is \"{pl}\": {pd}."
    if pl:
        return f"The {q['raw']} is \"{pl}\"."
    clause = _pct_clause(f)
    if clause and f['sentence'] not in sents:
        return f"The {q['raw']}: \"{clause}.\""
    if clause:
        return f"The {q['raw']} is the clause \"{clause}\" in that line."
    if f['sentence'] not in sents:
        return f"The {q['raw']}: \"{_clean(f['sentence'], 240)}\""
    return ''


def _block_text(n, b):
    """One numbered paragraph: what the figure counted."""
    cnt = b['count']
    parts = []
    label = _label(cnt)
    defn = _definition(cnt, b['rec'])
    uni = b['universe']
    if b.get('_pct_only'):
        lead = f"{n}. {b['raw']}: "
        if label and defn:
            desc = f"\"{label}\": {defn}"
        elif label:
            desc = f"\"{label}\", from the read on {uni}" if uni else f"\"{label}\""
        else:
            desc = f"a share stated inside the read on {uni}" if uni else 'a share stated in an earlier reply here'
        parts.append(lead + desc + '.')
        if b['window']:
            parts.append(f"Window: {b['window']}.")
        if not (label and defn):
            parts.append(f"The line that carried it: \"{_clean(cnt['sentence'], 320)}\"")
        return ' '.join(parts)
    shares = ', '.join(q['raw'] for q, _f in b['pcts'])
    lead = f"{n}. {b['raw']} ({_fmt_int(cnt['value'])})" + (f" with {shares}" if shares else '') + ': '
    named = _sentence_subject(cnt['sentence'], cnt['raw'])
    if label and defn:
        desc = f"\"{label}\": {defn}"
    elif defn:
        desc = defn
    elif label:
        desc = f"\"{label}\", from the read on {uni}" if uni else f"\"{label}\""
    elif named and uni and not (_tokens(named) & _tokens(uni)):
        # a figure about one audience written inside another audience's read
        desc = f"{_cap(named)}, a line inside the read on {uni}, not a read of its own"
    elif named:
        desc = f"\"{_cap(named)}\"" + (f", from the read on {uni}" if uni else '')
    else:
        desc = f"from the read on {uni}" if uni else 'stated in an earlier reply here'
    parts.append(lead + desc + '.')
    if b['window']:
        parts.append(f"Window: {b['window']}.")
    if not (label and defn):
        parts.append(f"The line that carried it: \"{_clean(cnt['sentence'], 320)}\"")
    sents = _count_sentences(b)
    for q, f in b['pcts']:
        st = _share_text(q, f, b['rec'], sents)
        if st:
            parts.append(st)
    sp = _sample_people(cnt, b['rec'])
    if sp:
        parts.append(f"Behind the {_fmt_int(cnt['value'])}: {_fmt_int(sp)} people in the data, projected to the US.")
    return ' '.join(parts)


def _same_audience(b, canon):
    a, c = _tokens(_audience_name(b)) | _tokens(b['universe']), _tokens(_audience_name(canon)) | _tokens(canon['universe'])
    return bool(a & c) or b['universe'].lower() == canon['universe'].lower()


def _reason_not(b, canon):
    reasons = []
    same = _same_audience(b, canon)
    if not same:
        nm = _audience_name(b)
        reasons.append(f"a different audience ({nm})" if nm else 'a different audience')
    if b['window'] and canon['window'] and b['window'].lower() != canon['window'].lower():
        reasons.append(f"a different window ({b['window']})")
    if same and (b['pcts'] or b.get('_pct_only')):
        cuts = []
        for q, f in (b['pcts'] or [(None, b['count'])]):
            cuts.append(_label(f) or _pct_clause(f) or (q['raw'] if q else ''))
        cuts = [c for c in cuts if c]
        if cuts:
            reasons.append(f"a different cut of the share ({'; '.join(cuts)})")
    if not reasons:
        reasons.append('a different cut of the figures')
    shares = [q['raw'] for q, _f in b['pcts']]
    who = f"The {b['raw']}" + (f" and the {', '.join(shares)}" if shares else '')
    verb = 'come from' if shares else 'comes from'
    they = 'they do' if shares else 'it does'
    return f"{who} {verb} {' and '.join(reasons)}; {they} not replace the figure above."


def _also_stated(blocks, pcts, recs):
    """A quoted share that another read also stated, under its own
    label: one line each, so a share that rode two different bases is
    visible ('46.4%' as the Extraction share and as the June 2023
    Statham action-film share)."""
    out = []
    used = {(id(q), b['rec']['turn']) for b in blocks for q, _f in b['pcts']}
    for q in pcts:
        turns_seen = {t for (qid, t) in used if qid == id(q)}
        for r in recs:
            if r['turn'] in turns_seen:
                continue
            f = next((f for f in r['figures'] if _match(q, f) and f.get('metric')), None)
            if f is None:
                continue
            lab = _label(f)
            cnt = next((c for c in r['figures'] if c['kind'] == 'count' and c.get('metric') and c['sentence'] == f['sentence']), None)
            tail = f" ({_fmt_int(cnt['value'])} people)" if cnt else ''
            where = f"the read on {r['universe']}" if r['universe'] else 'an earlier read here'
            win = f", {r['window']}" if r['window'] else ''
            out.append(f"{q['raw']} also appears in {where}{win} as \"{lab}\"{tail}." if lab
                       else f"{q['raw']} also appears in {where}{win}.")
            turns_seen.add(r['turn'])
            if len(out) >= 3:
                return out
    return out


def _why(canon, asked, blocks):
    bits = []
    hit = sorted(_tokens(canon['universe']) & asked)
    if hit:
        # the asked word in the universe's own spelling
        words = {w.lower().strip("'"): w for w in re.findall(r"[A-Za-z0-9']+", canon['universe'])}
        named = ' '.join(words.get(h, h) for h in hit[:2])
        bits.append(f"it is the read that counts {named} itself")
    if canon['window_days'] >= 300:
        bits.append('it sits on a full window')
    full = [b for b in blocks if not b.get('_pct_only')]
    if len(full) > 1 and all(float(b['count']['value']) >= float(canon['count']['value']) for b in full):
        bits.append('it is the lowest of the totals, the figure to plan against')
    if not bits:
        return ''
    return 'Why this one: ' + '; '.join(bits) + '.'


def answer(text, history, ctx=None, ledger=None, host=None):
    """The reconciliation payload, or None when the ask is not one.
    Reads the thread and the ledger; never sizes, never prices."""
    try:
        if not wants_reconcile(text):
            return None
        quoted = quoted_figures(text)
        recs = record(history, ledger)
        counts = [q for q in quoted if q['kind'] == 'count']
        pcts = [q for q in quoted if q['kind'] == 'pct']
        blocks, missing, used_pct, used_turns = [], [], set(), []
        for q in counts:
            r, f = _find(q, recs, prefer_turns=())
            if r is None:
                missing.append(q['raw'])
                continue
            blocks.append({'raw': q['raw'], 'count': f, 'rec': r, 'universe': r['universe'], 'window': r['window'],
                           'window_days': r['window_days'], 'pcts': []})
            used_turns.append(r['turn'])
        _pair_pcts(blocks, pcts, used_pct)
        for q in pcts:
            if id(q) in used_pct:
                continue
            r, f = _find(q, recs, prefer_turns=tuple(used_turns))
            if r is None:
                missing.append(q['raw'])
                continue
            # a share with no total of its own: its own paragraph
            blocks.append({'raw': q['raw'], 'count': f, 'rec': r, 'universe': r['universe'], 'window': r['window'],
                           'window_days': r['window_days'], 'pcts': [], '_pct_only': True})
            used_pct.add(id(q))
        if not quoted and recs:
            # no figures quoted ("state one universe definition and one window"):
            # the record's count figures, newest read first
            for r in recs[-3:]:
                cs = [f for f in r['figures'] if f['kind'] == 'count' and f.get('metric')]
                if cs:
                    f = max(cs, key=lambda x: float(x['value']))
                    blocks.append({'raw': _fmt_int(f['value']), 'count': f, 'rec': r, 'universe': r['universe'],
                                   'window': r['window'], 'window_days': r['window_days'], 'pcts': []})
        if not blocks:
            recent = []
            for r in reversed(recs):
                for f in r['figures'][:6]:
                    tok = _fmt_pct(f['value']) if f['kind'] == 'pct' else _fmt_int(f['value'])
                    if tok not in recent:
                        recent.append(tok)
                if len(recent) >= 6:
                    break
            if not recent:
                reply = ("I do not see those figures in anything delivered in this thread. Tell me which read they came from "
                         "(or paste the lines) and I will set them side by side: what each counted, its window, and the "
                         "people in the data behind it.")
            else:
                reply = (f"I do not see {', '.join(missing or [q['raw'] for q in quoted])} in anything delivered in this thread. "
                         f"The figures in the last reads here are {', '.join(recent[:6])}. Tell me which read yours came from "
                         "and I will set them side by side: what each counted, its window, and the people in the data behind it.")
            return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
                    'deck_angle': None, 'reconcile': {'found': False, 'quoted': [q['raw'] for q in quoted]}}
        asked = _asked_tokens(text)
        full = [b for b in blocks if not b.get('_pct_only')] or blocks
        canon = max(full, key=lambda b: _score(b, asked))
        n_fig = len(blocks)
        opener = (f"Here is what {'each of those figures' if n_fig > 1 else 'that figure'} counted, from the reads already "
                  f"delivered in this thread, and the one basis to use from here.")
        lines = [opener, '', HEADER, '']
        for n, b in enumerate(blocks, 1):
            lines.append(_block_text(n, b))
            lines.append('')
        if missing:
            lines.append(f"I do not see {', '.join(missing)} in anything delivered in this thread.")
            lines.append('')
        # the one definition and window
        cnt = canon['count']
        defn = _definition(cnt, canon['rec'])
        uni = canon['universe'] or _label(cnt) or 'the audience above'
        one = f"{_ONE_HEADER} {uni}" + (f": {defn}" if defn else '') + '.'
        if canon['window']:
            one += f" One window: {canon['window']}."
        lines.append(one)
        why = _why(canon, asked, blocks)
        if why:
            lines.append(why)
        # the figures restated on that basis; a share and its count under
        # one label read as one item ("...: 58.2% (3,605,944)")
        e = canon['rec'].get('entry') or {}
        items, order = {}, []
        for m in (e.get('metrics') or []):
            if not isinstance(m, dict) or not isinstance(m.get('value'), (int, float)):
                continue
            lab = _clean(m.get('label') or m.get('name') or '')
            slot = items.setdefault(lab, {'pct': None, 'count': None})
            if lab not in order:
                order.append(lab)
            if _is_pct_unit(m.get('unit')):
                slot['pct'] = _fmt_pct(m['value'])
            else:
                slot['count'] = _fmt_int(m['value'])
        restated = []
        for lab in order:
            slot = items[lab]
            if slot['pct'] and slot['count']:
                restated.append(f"{lab}: {slot['pct']} ({slot['count']})")
            else:
                restated.append(f"{lab}: {slot['pct'] or slot['count']}")
        if not restated:
            restated.append(f"{_label(cnt) or _cap(_sentence_subject(cnt['sentence'], cnt['raw'])) or uni}: {_fmt_int(cnt['value'])}")
            for q, f in canon['pcts']:
                clause = _pct_clause(f)
                restated.append(clause if (clause and q['raw'] in clause) else f"{_label(f) or 'share'}: {q['raw']}")
        lines.append('On that basis: ' + '; '.join(restated) + '.')
        headline = next((s for s in canon['rec']['sentences'] if s != cnt['sentence'] and not _READ_HEAD_RX.match(s)
                         and re.search(r"\d", s) and not s.lower().startswith(('window:', 'base:', '- window', '- base'))
                         and 'How these are counted' not in s), '')
        if headline and len(headline) <= 400:
            lines.append(f"In words: {_clean(headline, 400)}")
        sp = _sample_people(cnt, canon['rec'])
        if sp:
            lines.append(f"Unprojected: {_fmt_int(sp)} people in the data behind the {_fmt_int(cnt['value'])}. "
                         f"The US figure is that count projected to the US; a share reads the same before and after projection.")
        lines.append('')
        others = [_reason_not(b, canon) for b in blocks if b is not canon]
        lines.extend(others)
        also = _also_stated(blocks, pcts, recs)
        if also:
            lines.append('')
            lines.append('Also on the record: ' + ' '.join(also))
        reply = '\n'.join(lines).strip()
        reply = re.sub(r"\n{3,}", '\n\n', reply)
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'subject': uni,
                'reconcile': {'found': True, 'quoted': [q['raw'] for q in quoted],
                              'canonical': {'universe': uni, 'window': canon['window'], 'count': cnt['value'],
                                            'sample_people': sp},
                              'blocks': [{'raw': b['raw'], 'universe': b['universe'], 'window': b['window'],
                                          'value': b['count']['value']} for b in blocks]}}
    except Exception:
        traceback.print_exc()
        return None
