"""US only or global: a definition question about the previous answer
(Jenna 2026-11-04, Jay Patel at Amazon with Paramount+ open).

Jay asked "are these numbers U.S only On the current window, 51,131,927
US people use both HBO Max and Paramount+, 27.0% of the HBO Max audience
and 54.6% of the Paramount+ audience, up 5,918,373 people on the 2025
read. or global", three times in three phrasings, and each time drew an
interest-mix read of the open profile. Nothing in the deterministic path
knew the question: the continuation classifier's about-the-answer cues and
the figure-provenance cues ask where a number came from, not what it
covers; the Reader called it a definition but no executor owns that
intent and the glossary has no entry for it; so the ask fell through to
the open-profile binding and a fresh read.

The question is a definition question about the answer in hand, never a
new read. The house definition: every figure Prometheus reports is US
people, measured on US consumers and projected to the US population;
nothing is global. This lane answers it from that definition without a
model call and without re-running anything:

  * the figures the reader pasted are quoted back (or, with none pasted,
    the headline figures of the last read in the thread), with the read
    they came from when the thread names it and its window when the
    read's counting block carries one;
  * then the one line on scope.

Shapes it answers: "are these numbers US only or global?", "is this US or
worldwide?", "is that the US figure?", "global or domestic?", "US only?",
"is the 51 million U.S or global?". Shapes it leaves alone: a question
that asks FOR a global or US figure ("how many global subscribers does
Netflix report", "size the US audience"), an order ("compare US vs global
for Netflix"), a public-figure or box office question.

Runs in prometheus.service.ask ahead of number_provenance, the
continuation gate, the Reader, the sizing lanes and the open-profile
binding, on both client surfaces. Pure text plus the thread; no Flask, no
S3, no model. Kill switch ``PM_US_SCOPE=0``. No em dashes.
"""
from __future__ import annotations

import os
import re

from . import stated_numbers as _sn

__all__ = ['wants_us_scope', 'quoted_figures', 'origin', 'answer', 'enabled', 'SCOPE_LINE', 'OPENER']

OPENER = 'US only.'
SCOPE_LINE = ('Every figure we report is measured on US consumers and projected to the US population; '
              'nothing is global.')

_US = r"(?:us|u\.s\.?a?|usa|united states|domestic|stateside)"
_WORLD = r"(?:global(?:ly)?|world ?wide|the world|world|international(?:ly)?|intl|ww|everywhere|all countries|other countries|outside (?:of )?the us|non[- ]us)"

# the cues: each one asks about the scope of a figure in hand, never for a figure to fetch
_US_ONLY_RX = re.compile(rf"\b{_US}[- ]?(?:only|specific)\b|\b(?:only|just)\s+(?:in\s+)?(?:the\s+)?{_US}\b", re.I)
_US_OR_WORLD_RX = re.compile(rf"\b{_US}\b\W*(?:only\W*)?(?:or|vs\.?|versus|and not|not)\W*(?:just\s+|only\s+)?(?:the\s+)?{_WORLD}\b", re.I)
_WORLD_OR_US_RX = re.compile(rf"\b{_WORLD}\b\W*(?:or|vs\.?|versus)\W*(?:just\s+|only\s+)?(?:in\s+)?(?:the\s+)?{_US}\b", re.I)
_US_FIGURE_RX = re.compile(
    rf"\b(?:is|are|was|were|be)\s+(?:this|that|these|those|it|they|the|all|everything|your)\b[^.?!]{{0,50}}?"
    rf"\b{_US}\s+(?:figures?|numbers?|counts?|totals?|data|people|audience|projections?|users|viewers)\s*(?:only\s*)?(?:\?|$|[,;])", re.I)
_WORLD_FIGURE_RX = re.compile(
    rf"\b(?:is|are|was|were|be)\s+(?:this|that|these|those|it|they|the|all|everything|your)\b[^.?!]{{0,50}}?"
    rf"\b{_WORLD}\s*(?:figures?|numbers?|counts?|totals?|data|people|audience|projections?|users|viewers)?\s*(?:\?|$|[,;])", re.I)
_BARE_US_Q_RX = re.compile(rf"\b(?:is|are|was|were)\s+(?:this|that|these|those|it|they)\s+(?:all\s+)?(?:the\s+)?{_US}\s*(?:\?|$|[,;])", re.I)
_CUES = (_US_ONLY_RX, _US_OR_WORLD_RX, _WORLD_OR_US_RX, _US_FIGURE_RX, _WORLD_FIGURE_RX, _BARE_US_Q_RX)

# never this lane: an order, work asked for, a published or box office figure
_ORDER_RX = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+|would you\s+)?(?:build|pull|run|create|generate|size|make|email|send|draft|queue|launch|"
    r"compare|cut|break\s+(?:out|down)|rank|list|give\s+me|show\s+me|get\s+me|find\s+me)\b", re.I)
# a question that asks FOR a figure ("how many US subscribers does Netflix have") is a read; a short one
# ("how many of these are US?") still asks about the figures in hand
_FETCH_OPEN_RX = re.compile(
    r"^\W*(?:how\s+(?:many|much|big|large)|what\s+(?:share|percent(?:age)?|fraction|portion|is\s+the\s+(?:size|number|count|total))|who)\b", re.I)
_NOT_OURS_RX = re.compile(
    r"\b(?:report(?:s|ed)?|announc\w+|earnings|press release|filing|10-?k|officially?|box office|gross(?:es|ed)?|ticket sales|"
    r"subscriber count|subscribers? (?:does|do|did)\b)", re.I)
_INTERROGATIVE_OPEN_RX = re.compile(r"^\W*(?:is|are|was|were|does|do|did|so|and|but|or|which|what|wait|sorry|ok(?:ay)?|hmm|just|only)\b", re.I)
_SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+|\n+")
_READ_HEAD_RX = re.compile(r"^On\s+(?P<who>[^:\n(]{1,120}?)(?:\s*\([^)]*\))?\s*(?:-\s*[^:\n]{1,80})?:", re.I)
_WINDOW_LINE_RX = re.compile(r"(?m)^\s*-?\s*Window:\s*(?P<w>[^\n]+?)\.?\s*$")
_PROMISE_RX = re.compile(r"^\s*On it\b|\bthe read will land right here\b|\bWorking on it\b|^\s*Already on it\b", re.I)
_OWN_RX = re.compile(r"^\s*" + re.escape(OPENER))
_SAMPLE_MENTION_RX = re.compile(r"\b(?:across|inside|within|in)\s+(?:a\s+|the\s+)?$", re.I)
_BLOCK_LINE_RX = re.compile(r"^\s*-?\s*(?:window|base|what we count as a view|carriage paths|sessions|threshold):", re.I)


def enabled():
    return str(os.environ.get('PM_US_SCOPE', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


# ------------------------------------------------------------- detection
def _sentences(text):
    return [s.strip() for s in _SENT_SPLIT_RX.split(str(text or '')) if s.strip()]


def _cue_sentence(text):
    """The sentence that carries the scope cue, or '' when none does."""
    t = ' '.join(str(text or '').split())
    for s in _sentences(t):
        if any(rx.search(s) for rx in _CUES):
            return s
    # a cue split by a pasted line ("are these numbers U.S only <the read>. or global")
    if any(rx.search(t) for rx in _CUES):
        return t
    return ''


def _question_shaped(sentence, whole):
    s = str(sentence or '')
    if '?' in s or '?' in str(whole or ''):
        return True
    if _INTERROGATIVE_OPEN_RX.match(s):
        return True
    return len(s.split()) <= 8


def wants_us_scope(text):
    """A question about whether figures in hand are US or global."""
    if not enabled():
        return False
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 1200:
        return False
    if _ORDER_RX.match(t) or _NOT_OURS_RX.search(t):
        return False
    cue = _cue_sentence(t)
    if not cue:
        return False
    if _ORDER_RX.match(cue):
        return False
    if _FETCH_OPEN_RX.match(cue) and len(cue.split()) > 8:
        return False
    return _question_shaped(cue, t)


# --------------------------------------------------------------- figures
def quoted_figures(text):
    """The figures the reader pasted, in order: [{'raw', 'value', 'kind'
    ('count' | 'pct'), 'tol'}]. Counts under a thousand, money, bare years
    and the sample mention ("across 10M US consumers") stay out. An
    abbreviated figure (51 million) matches a thread figure to half its
    last written digit; a full figure matches exactly."""
    t = str(text or '')
    out, seen = [], set()
    for it in _sn.stated(t):
        unit = str(it.get('unit') or '')
        raw = str(it.get('raw') or '').strip()
        if unit == '$' or not raw or raw in seen:
            continue
        start, end = int(it.get('start') or 0), int(it.get('end') or 0)
        before, after = t[max(0, start - 12):start], t[end:end + 24]
        if _SAMPLE_MENTION_RX.search(before) and re.match(r"\s*(?:m\b|million)?\s*(?:us\s+)?consumers\b", after, re.I):
            continue
        if unit == '%':
            dec = len(raw.split('.')[1].rstrip('%').strip()) if '.' in raw else 0
            out.append({'raw': raw, 'value': float(it['value']), 'kind': 'pct', 'tol': 0.5 * (10 ** -dec) if dec else 0.5})
            seen.add(raw)
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
            tol = 0.5 * (10 ** -dec) * {'K': 1e3, 'M': 1e6, 'B': 1e9}[unit]
        else:
            tol = 0.5
        out.append({'raw': raw, 'value': v, 'kind': 'count', 'tol': tol})
        seen.add(raw)
    return out


def _turn_figures(txt):
    out = []
    for it in _sn.stated(txt):
        unit = str(it.get('unit') or '')
        if unit == '$':
            continue
        try:
            val = float(it['value'])
        except (TypeError, ValueError):
            continue
        kind = 'pct' if unit == '%' else 'count'
        if kind == 'count' and val < 1000:
            continue
        out.append({'raw': str(it.get('raw') or ''), 'value': val, 'kind': kind})
    return out


def _match(q, f):
    return q['kind'] == f['kind'] and abs(float(f['value']) - float(q['value'])) <= float(q['tol'])


def _agent_turns(history):
    out = []
    for i, t in enumerate(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '').lower() not in ('agent', 'assistant'):
            continue
        txt = str(t.get('text') or t.get('content') or '')
        if not txt.strip() or _OWN_RX.match(txt) or _PROMISE_RX.search(txt[:120]):
            continue
        out.append((i, t, txt))
    return out


def _fmt(f):
    raw = str(f.get('raw') or '')
    if f['kind'] == 'pct':
        return raw
    if ',' in raw or raw.isdigit():
        try:
            return f"{int(round(float(f['value']))):,}"
        except (TypeError, ValueError):
            return raw
    return raw


def _join(items):
    items = [x for x in items if x]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def _head(txt, meta):
    m = _READ_HEAD_RX.match(txt.strip())
    who = m.group('who').strip() if m else ''
    return who or str((meta or {}).get('subject') or '').strip()


def _window(txt):
    """The window the read's own counting block names; '' otherwise (a
    month inside the prose is never read as the window)."""
    m = _WINDOW_LINE_RX.search(txt)
    return m.group('w').strip() if m else ''


def origin(figures, history):
    """The agent turn the pasted figures came from: the newest turn that
    carries the most of them. {'turn', 'head', 'window', 'hits'} or None.
    With no figures pasted, the newest read that carries figures, with its
    headline figures as 'hits'."""
    turns = _agent_turns(history)
    if not turns:
        return None
    if figures:
        best = None
        for i, t, txt in turns:
            tf = _turn_figures(txt)
            hits = [q for q in figures if any(_match(q, f) for f in tf)]
            if hits and (best is None or len(hits) >= len(best['hits'])):
                best = {'turn': i, 'head': _head(txt, t.get('meta')), 'window': _window(txt), 'hits': hits}
        return best
    for i, t, txt in reversed(turns):
        for s in _sentences(txt):
            if _READ_HEAD_RX.match(s) or _BLOCK_LINE_RX.match(s) or 'How these are counted' in s:
                continue
            figs = _turn_figures(s)
            if figs:
                hits = []
                for f in figs:
                    if f['raw'] not in [h['raw'] for h in hits]:
                        hits.append(f)
                return {'turn': i, 'head': _head(txt, t.get('meta')), 'window': _window(txt), 'hits': hits[:4], 'headline': s}
    return None


# ---------------------------------------------------------------- answer
def answer(text, history, ctx=None):
    """The scope answer payload, or None when the turn is not a scope
    question. Never raises past its own border."""
    try:
        if not wants_us_scope(text):
            return None
        figs = quoted_figures(text)
        org = origin(figs, history)
        head = str((org or {}).get('head') or '')
        win = str((org or {}).get('window') or '')
        quoted = [_fmt(f) for f in figs]
        parts = [OPENER]
        if quoted and org:
            where = f" (the read on {head})" if head else ''
            parts.append(f"Every figure in that read{where} is US people: {_join(quoted)}.")
        elif org and org.get('hits'):
            where = f" (the read on {head})" if head else ''
            parts.append(f"The figures in the last read here{where} are US people: {_join(_fmt(f) for f in org['hits'])}.")
        parts.append(SCOPE_LINE)
        if win:
            parts.append(f"Window: {win}.")
        if quoted and not org:
            they = 'they came' if len(quoted) > 1 else 'it came'
            parts.append(f"I do not see {_join(quoted)} in anything delivered in this thread; if {they} from one of our files "
                         f"or exports, the same holds there.")
        reply = ' '.join(parts)
        out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False, 'deck_angle': None,
               'us_scope': {'figures': quoted, 'turn': (org or {}).get('turn'), 'window': win}}
        if head:
            out['subject'] = head
        return out
    except Exception:
        return None
