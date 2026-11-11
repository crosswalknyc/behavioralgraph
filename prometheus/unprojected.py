"""Raw, unprojected counts on request (Jenna 2026-11-08).

Jenna, verbatim: "if someone asks for a raw or unprojected or not
modeled or panel size or something like that number you can give them
whatever the genpop projected number is if you unproject it".

Every projected US figure in a Profile IQ file or a read derives from
the 10 million US consumers we measure: Projection = Raw x 329,900,000
/ 10,000,000, so Raw = Projection x 10,000,000 / 329,900,000. The
factor lives once, in prometheus.projection (``project`` / ``unproject``);
a profile's SAMPLE SIZE / BRAND INPUT row carries the subject's own
measured count and is read directly when the ask is about a profile's
audience.

A question for the raw, unprojected, measured, actual-count, "not
modeled", "panel size", "how many in your data" or "before projection"
number is answered with the figure brought back to that base:

  Unprojected, that is 182,739 of the 10 million US consumers we
  measure (the projected US figure is 6,028,562).

The figure comes, in order, from: a count quoted in the ask; a subject
the dashboard holds a profile on (its SAMPLE SIZE row); the figure the
last read in the thread carried; the profile open on the screen. With
nothing to unproject the lane asks which number, in one line. A size
ask on a subject the dashboard does not hold is left to the sizing lane
(a new count is still the priced sizing read); this lane only ever
reads a figure already on the table. The reply never says modeled,
synthetic, estimated or panel as a noun; the house phrase is "the 10
million US consumers we measure".

Pure functions over the words, the thread, the page and the catalog;
no model, no price. Kill switch ``PM_UNPROJECTED=0``.
"""
from __future__ import annotations

import os
import re

from . import projection as _pj

TEN_M_PHRASE = "the 10 million US consumers we measure"
WHICH_NUMBER = ("Which number should I bring back to the 10 million US consumers we measure? Name the figure, "
                "or the subject it is on, and I will unproject it.")

# the explicit vocabulary: the base count rather than the projected one
_EXPLICIT = (
    r"un-?projected|before (?:the |any |you )?project(?:ion|ed)|pre-?projection|without (?:the |any )?projection|"
    r"not (?:modell?ed|projected|extrapolated|scaled(?: up)?|weighted|blown up)|un-?modell?ed|un-?weighted|un-?scaled|"
    r"non-?(?:modell?ed|projected)|"
    r"raw (?:number|numbers|count|counts|figure|figures|total|audience|size|value|people|viewers|readers|listeners|users|"
    r"buyers|shoppers|subscribers|fans|reach|sample|n|one|version|form)\b|"
    r"in raw (?:form|terms)|raw,? (?:not|rather than|instead of) projected|"
    r"(?:measured|counted|underlying|observed) (?:number|count|figure|n|people|size|total|value)\b|actual (?:count|n)\b|"
    r"(?:of|in|inside|within|out of) the (?:10|ten) ?(?:m|mm|million)\b|"
    r"\bn\s*=\s*\?|what(?:'s| is| was) (?:the |your )?n\b"
)
# the softer shapes: the size of the measured set behind a subject
_SOFT = (
    r"panel (?:size|count|number|n|figure|total)|"
    r"(?:how many|number|count|total) (?:of )?(?:the )?(?:people|panelists|consumers|users|viewers|readers|listeners|buyers|"
    r"fans|folks|individuals)?\s*(?:are |is |were |was |do you have |did you (?:see|count|measure) )?(?:actually |really )?"
    r"(?:in|inside|within|from|of|across) (?:your|the|our) (?:data|dataset|data set|sample|measured (?:set|group|population|base)|"
    r"(?:10|ten) ?(?:m|mm|million)\b)"
)
_EXPLICIT_RX = re.compile(r"\b(?:" + _EXPLICIT + r")", re.I)
_ASK_RX = re.compile(r"\b(?:" + _EXPLICIT + r"|" + _SOFT + r")", re.I)
# a figure the ask quotes: a grouped integer, a bare integer of 4+
# digits, or an abbreviated count (6.0M, 182k); percents never unproject
_NUM_RX = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?|\d+(?:\.\d+)?\s?(?:m|mm|million|k|thousand)\b", re.I)
_PCT_RX = re.compile(r"\d+(?:\.\d+)?\s?(?:%|percent)")
_YEAR_RX = re.compile(r"^(?:19|20)\d{2}$")
# the words point back at a figure already on the table
_REF_RX = re.compile(r"\b(?:that|this|it|those|these|the (?:number|figure|count|total|audience|one|read|last (?:number|figure|read)))\b", re.I)
_POINTS_AT_PAGE_RX = re.compile(r"\b(?:this (?:profile|audience|file|page|one)|here|on (?:my )?screen|open (?:profile|tab|file)|the (?:open )?profile)\b", re.I)
# a subject named after the verb or the preposition: "watched Gilmore Girls",
# "for Howie Mandel", "the Howie Mandel audience"
_AFTER_VERB_RX = re.compile(
    r"\b(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|bought|buy|purchas(?:ed|e)|read|listen(?:ed)? to|play(?:ed)?|follow(?:ed|s)?|"
    r"visit(?:ed)?|use[ds]?|engag(?:ed|e) with|searched(?: for)?|subscrib(?:ed|e) to|shopp?(?:ed)? at)\s+(?P<name>[^?.,;]{2,80}?)\s*"
    r"(?:\?|$|,|\.| in (?:the|your)\b| on (?:the|your)\b| last\b| this\b| over\b| during\b)", re.I)
_AFTER_PREP_RX = re.compile(
    r"\b(?:for|on|of|about|behind)\s+(?P<name>[^?.,;]{2,80}?)\s*(?:\?|$|,|\.| in (?:the|your)\b| on (?:the|your)\b| audience\b| viewers\b| fans\b)", re.I)
_THE_AUDIENCE_RX = re.compile(r"\b(?:the|that)\s+(?P<name>[^?.,;]{2,80}?)\s+(?:audience|viewers|fans|readers|listeners|buyers|users|subscribers)\b", re.I)
_STOP_NAMES = frozenset(('that', 'this', 'it', 'them', 'those', 'these', 'the', 'number', 'figure', 'count', 'total', 'audience', 'data',
                         'dataset', 'data set', 'sample', 'us', 'people', 'viewers', 'raw', 'projection', 'read', 'last read', 'one',
                         'page', 'profile', 'file', 'screen', 'last', 'previous', 'above', 'below', 'your data', 'the data', 'your sample',
                         'your dataset', 'the dataset', 'the number', 'the figure', 'the projection', 'that number', 'this number',
                         'the raw number', 'the read', 'the last read', 'the 10 million', 'the ten million', '10 million', 'ten million'))
_COMMON_WORDS = frozenset(('the', 'a', 'an', 'that', 'this', 'those', 'these', 'it', 'number', 'numbers', 'figure', 'figures', 'count',
                           'counts', 'total', 'audience', 'data', 'dataset', 'set', 'sample', 'read', 'one', 'page', 'profile', 'file',
                           'screen', 'last', 'previous', 'above', 'below', 'raw', 'projection', 'projected', 'us', 'people', 'viewers',
                           'million', 'ten', '10', 'your', 'our', 'my', 'measured', 'actual', 'unprojected', 'here', 'there', 'open',
                           'is', 'are', 'was', 'were', 'be', 'in', 'of', 'for', 'on', 'at', 'to', 'and', 'or', 'with', 'from', 'behind'))
_AUDIENCE_TAIL_RX = re.compile(r"\s+(?:audience|viewers|fans|readers|listeners|buyers|users|subscribers|watchers|people)\s*$", re.I)
_BANNED_RX = re.compile(r"\b(?:modell?ed|synthetic|estimated?|panel(?:ists?)?)\b", re.I)


def enabled():
    return str(os.environ.get('PM_UNPROJECTED', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(str(text or '').split())


def _fmt(n):
    return f"{int(n):,}"


def _norm_num(tok):
    return re.sub(r"[,\s]", '', str(tok or '')).lower()


def _value_of(tok):
    """The integer a quoted figure stands for; None for a percent, a
    year or a token that is not a count."""
    s = _norm_num(tok)
    if not s or s.endswith('%') or _YEAR_RX.match(s):
        return None
    mult = 1.0
    for suffix, m in (('million', 1e6), ('thousand', 1e3), ('mm', 1e6), ('m', 1e6), ('k', 1e3)):
        if s.endswith(suffix):
            s, mult = s[:-len(suffix)], m
            break
    try:
        v = float(s) * mult
    except ValueError:
        return None
    return int(round(v)) if v >= 100 else None


def quoted_figures(text):
    """The counts the ask itself quotes, in order; percents skipped."""
    t = _clean(text)
    pct_spans = [(m.start(), m.end()) for m in _PCT_RX.finditer(t)]
    out = []
    for m in _NUM_RX.finditer(t):
        if any(a <= m.start() < b for a, b in pct_spans):
            continue
        v = _value_of(m.group(0))
        if v is not None:
            out.append((m.group(0).strip(), v))
    return out


def explicit(text):
    """True when the words carry the raw / unprojected vocabulary itself
    (not only the softer 'how many in your data' shape)."""
    return bool(_EXPLICIT_RX.search(_clean(text)))


def wants_unprojected(text):
    """True when the words ask for the base count behind a projected
    figure. A question about the size of the measured set itself ("how
    big is your sample") is the product fact's, never this lane's; a
    file ask ("send me the raw data") is not a count."""
    if not enabled():
        return False
    t = _clean(text)
    if not t or len(t) > 600 or not _ASK_RX.search(t):
        return False
    try:
        from . import orders as _orders
        if _orders.is_order(t):
            return False      # an order is queued, never deflected (Grace Yin 2026-11-10): "Deliver as a CSV with the unprojected count" rides the brief
    except Exception:
        pass
    try:
        from . import segment_sizing as _seg
        if _seg.is_segment_ask(t):
            return False      # a demographic filter ("income $50k-$200k") is a segment to size, never a figure to unproject (2026-11-11)
    except Exception:
        pass
    if re.search(r"\braw (?:data|file|csv|export|rows|table|download)\b", t, re.I) and not quoted_figures(t) \
            and not re.search(r"\b(?:number|count|figure|how many|total)\b", t, re.I):
        return False
    try:
        from . import guards as _g
        if _g.is_provenance_question(t):
            return False      # "how is a play detected, and what is the raw count before projection": the methodology answer's
        if _g.is_sample_size_ask(t) and not quoted_figures(t) and not _named_subject(t, '', None):
            return False
    except Exception:
        pass
    return True


def _page_name(ctx):
    try:
        return str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def _plausible_name(name):
    n = _clean(name).strip(" '\".,;:")
    n = re.sub(r"^(?:the|a|an|your|our|my|that|this|those|these)\s+", '', n, flags=re.I)
    n = _AUDIENCE_TAIL_RX.sub('', n).strip()
    if not n or n.lower() in _STOP_NAMES or len(n) < 2:
        return ''
    if re.fullmatch(r"[\d,.\s%]+", n) or re.search(r"\b(?:10|ten) ?(?:m|mm|million)\b", n, re.I):
        return ''
    words = [w for w in re.findall(r"[A-Za-z0-9'&+.-]+", n.lower())]
    if not words or all(w in _COMMON_WORDS for w in words):
        return ''
    if re.match(r"^(?:raw|unprojected|number|figure|count|total|projection|projected|data|sample|measured|actual)\b", n, re.I):
        return ''
    try:
        from . import subject_rules as _sr
        if _sr.is_metric_phrase(n):
            return ''
    except Exception:
        pass
    return n


def _named_subject(text, page, history):
    """The subject the words name, or ''. The binder first (its library
    spelling when it has one), then the plain shapes: the audience
    phrase ("the Howie Mandel audience"), after the verb ("watched
    Gilmore Girls"), after the preposition ("for Howie Mandel")."""
    t = _clean(text)
    try:
        from . import subject_rules as _sr
        s = _sr.bind(t, history, page or '')
        if s and s.source == 'words' and s.kind not in ('metric', 'none', 'audience') and s.display:
            n = _plausible_name(s.display)
            if n:
                return n
    except Exception:
        pass
    for rx in (_THE_AUDIENCE_RX, _AFTER_VERB_RX, _AFTER_PREP_RX):
        for m in rx.finditer(t):
            n = _plausible_name(m.group('name'))
            if n:
                return n
    return ''


def _profile_hit(name, ctx):
    """{'name','sample','us'} for a subject the dashboard holds a profile
    on (the SAMPLE SIZE row's count and the projected audience), or None."""
    if not name:
        return None
    try:
        from . import sizing_lane as _sz
        hit = _sz.resolve(name, ctx)
    except Exception:
        hit = None
    if not hit or not hit.get('sample'):
        return None
    return hit


def _last_read_figure(history):
    """(value, label, subject) for the headline count of the last agent
    turn that carried one: the read's own figures ledger first, else the
    first grouped count in its text. None when no turn carried a count."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns):
        if str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        txt = _clean(t.get('text') or '')
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if re.search(r"Pulling this data will cost", txt) or (meta.get('unprojected') and not meta.get('figures')):
            continue
        subject = str(meta.get('subject') or '').strip()
        if not subject:
            m = re.match(r"^On ([^:]{2,120}?)(?:\s+-\s+[^:]{1,120})?:\s", txt)
            subject = m.group(1).strip() if m else ''
        for f in (meta.get('figures') or []):
            if not isinstance(f, dict):
                continue
            unit = str(f.get('unit') or f.get('kind') or '').lower()
            if 'percent' in unit or 'share' in unit or '%' in str(f.get('label') or ''):
                continue
            try:
                v = float(f.get('value'))
            except (TypeError, ValueError):
                continue
            if v >= 100 and abs(v - round(v)) < 1e-6:
                return int(round(v)), str(f.get('label') or '').strip(), subject
        figs = quoted_figures(txt)
        if figs:
            return figs[0][1], '', subject
    return None


def _line(raw, projected, lead):
    return (f"Unprojected, {lead} {_fmt(raw)} of {TEN_M_PHRASE} "
            f"(the projected US figure is {_fmt(projected)}).")


def _payload(reply, found, **detail):
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
            'deck_angle': None, 'unprojected': dict(detail, found=found)}


def answer(text, history=None, ctx=None):
    """The unprojected payload, or None when the words do not ask for a
    base count (or name a subject the dashboard does not hold, which the
    sizing lane prices as a new count). Resolution order: a figure
    quoted in the ask; a subject the catalog holds (its SAMPLE SIZE
    row); the last read's headline figure; the open profile. Otherwise
    the one-line which-number ask."""
    if not wants_unprojected(text):
        return None
    t = _clean(text)
    page = _page_name(ctx)
    # 1. a figure quoted in the ask
    figs = quoted_figures(t)
    if figs:
        tok, val = figs[0]
        raw = _pj.unproject(val)
        return _payload(_line(raw, val, f"{tok} is"), True, basis='quoted', projected=int(val), raw=int(raw))
    # 2. a subject named in the words that the dashboard holds a profile on
    named = _named_subject(t, page, history)
    if named:
        hit = _profile_hit(named, ctx)
        if hit:
            raw, us = int(hit['sample']), int(hit.get('us') or _pj.project(hit['sample']))
            subj = str(hit.get('name') or named)
            return _payload(_line(raw, us, f"the {subj} audience is"), True, basis='sample_size_row', subject=subj,
                            projected=us, raw=raw)
        if not _REF_RX.search(t):
            if not explicit(t):
                return None       # a new count on a subject not on file: the sizing lane's (priced) read
            # one line, the question only: never "say run a profile on X" (an order was never this lane's to ask for;
            # a reader who already said it was being told to repeat herself, Grace Yin 2026-11-10)
            reply = (f"I do not hold a profile on {named} yet, so there is no figure to bring back to {TEN_M_PHRASE}. "
                     f"Which projected number should I unproject?")
            return _payload(reply, False, basis='no_profile', subject=named)
    # 3. the figure the last read in the thread carried
    if not named or _REF_RX.search(t):
        last = _last_read_figure(history)
        if last:
            val, label, subject = last
            raw = _pj.unproject(val)
            if label and subject:
                lead = f"the {label} on {subject} is"
            elif subject:
                lead = f"the {_fmt(val)} on {subject} is"
            else:
                lead = 'that is'
            return _payload(_line(raw, val, lead), True, basis='last_read', projected=int(val), raw=int(raw),
                            subject=subject or None)
    # 4. the profile open on the screen
    if page:
        hit = _profile_hit(page, ctx)
        if hit:
            raw, us = int(hit['sample']), int(hit.get('us') or _pj.project(hit['sample']))
            subj = str(hit.get('name') or page)
            return _payload(_line(raw, us, f"the {subj} audience is"), True, basis='open_profile', subject=subj,
                            projected=us, raw=raw)
    return _payload(WHICH_NUMBER, False, basis='which')


def is_clean(reply):
    """True when a reply carries none of the words this lane never says."""
    return not _BANNED_RX.search(str(reply or ''))
