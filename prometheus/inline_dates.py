"""Inline dates in an ask bind the build window (Grace Yin, Amazon,
2026-11-08).

She asked for a Hazbin Hotel Season 1 sign-ups profile 'from January 19
and February 15, 2024' (and, re-asking, 'between January 19 and
February 15, 2024' and 'Jan 19 to Feb 15 2024'). The interpret step
stamped the trailing-12-month default on all three and she typed the
dates again in the wizard. The window a person writes into the ask is
the window; the model never gets to lose it.

This module is the deterministic reader. ``parse_inline_window`` takes
the ask and returns the explicit window it states, as ISO dates:

* two dates joined by a range word or a dash: 'from A to B', 'from A
  and B', 'between A and B', 'A through B', 'A - B', 'A until B'.
  Month-name or numeric dates, a day of month that may carry its year
  or not; a day range inside one month ('January 19-21, 2024'); a
  month range ('April to June 2024'); a year range with a range word
  ('2023 to 2024').
* a span off a date: 'the first 28 days of/after <date>', 'the two
  weeks after <date>', '45 days from <date>'. The anchor day is day one.
* 'since <date>' and '<date> to date / to present / onward': through
  today.
* one period: a month and year ('January 2024'), a quarter ('Q4 2025',
  'the fourth quarter of 2025'), a half ('H1 2025'), a calendar year
  named as a window ('calendar 2025', 'for 2025', 'the 2025 calendar
  year'). These are COARSE: a draft that already carries a finer
  explicit window (the model resolved 'Super Bowl 2025' to the game
  day) keeps it; see the binder in app.py.

A missing year on the first date comes from the second ('January 19
and February 15, 2024' is 2024-01-19 to 2024-02-15; 'December 15 to
January 10, 2024' starts in 2023). A missing year on both takes the
most recent occurrence that has ended. A date that describes the
subject rather than the window ('the show that premiered in January
2024', 'the January 2024 premiere') is not a window; a bare year with
no window word around it ('Dune 2024', '(2024)') is not either.

Pure: no model, no S3, no host. The binder that writes the window onto
an interpret draft lives in app.py (``_bind_inline_date_window``) next
to the relative and shared-clause binders; the brief reader
(``prometheus.intake_reader.find_date_ranges``) falls back to this
parser when its own shapes find nothing. Regression:
scripts/test_pm_inline_dates_bind.py.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone

__all__ = ['parse_inline_window', 'window_label', 'COARSE_KINDS']

COARSE_KINDS = frozenset(('month', 'quarter', 'half', 'year'))

_MONTH_NUM = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_YR = r"(?:19|20)\d{2}"
_DAYN = r"(?:3[01]|[12]\d|0?[1-9])"
_ORD = r"(?:st|nd|rd|th)?"
_NUMWORD = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8,
    'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
    'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30,
    'forty': 40, 'fifty': 50, 'sixty': 60, 'ninety': 90,
}
_ORDWORD = {'first': 1, '1st': 1, 'second': 2, '2nd': 2, 'third': 3, '3rd': 3, 'fourth': 4, '4th': 4}

# The date tokens an ask can carry, each with the fields it states.
# Order matters only through the overlap rule below (longest token at a
# position wins; a quarter beats the bare year inside it).
_TOKEN_SHAPES = (
    ('iso', re.compile(r"(?<![\d-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d-])")),
    ('num', re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{4}|\d{2}))?(?![\d/])")),
    ('mdy', re.compile(r"\b(?P<mon>" + _MON + r")\.?\s+(?P<d>" + _DAYN + r")" + _ORD
                       + r"(?:,?\s*(?P<y>" + _YR + r"))?\b(?!\s*(?:%|percent|am|pm)\b)", re.I)),
    ('dmy', re.compile(r"\b(?P<d>" + _DAYN + r")" + _ORD + r"\s+(?:of\s+)?(?P<mon>" + _MON
                       + r")\.?(?:,?\s*(?P<y>" + _YR + r"))?\b", re.I)),
    ('my', re.compile(r"\b(?P<mon>" + _MON + r")\.?,?\s+(?:of\s+)?(?P<y>" + _YR + r")\b", re.I)),
    ('quarter', re.compile(r"\b(?:q(?P<q>[1-4])\s*(?:of\s+)?(?P<y>" + _YR + r")|(?P<y2>" + _YR + r")\s*q(?P<q2>[1-4])|"
                           r"(?P<qw>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+(?:of\s+)?(?P<y3>" + _YR + r"))\b", re.I)),
    ('half', re.compile(r"\b(?:h(?P<h>[12])\s*(?:of\s+)?(?P<y>" + _YR + r")|"
                        r"(?P<hw>first|second|1st|2nd)\s+half\s+(?:of\s+)?(?P<y3>" + _YR + r"))\b", re.I)),
    ('year', re.compile(r"\b(?P<y>" + _YR + r")\b")),
    ('mon', re.compile(r"\b(?P<mon>" + _MON + r")\b\.?", re.I)),
)
_RANGE_JOIN_RX = re.compile(r"^\s*,?\s*(?:(?P<w>to|through|thru|until|till|up\s+to|and)|(?P<dash>-|\u2013|\u2014))\s*(?:the\s+)?$", re.I)
_PAIR_LEAD_RX = re.compile(r"\b(?:from|between)\s+(?:the\s+)?$", re.I)
_DATE_LEAD_RX = re.compile(r"\b(?:from|between|window|dates?|period|run(?:ning)?|ran|span(?:ning)?)\b[^.?!]{0,30}$", re.I)
# a date that describes the subject, not the window
_DESCRIBES_BEFORE_RX = re.compile(
    r"\b(?:premiered|released|launched|debuted|aired|dropped|born|founded|opened|came\s+out|started|began|"
    r"announced|renewed|cancell?ed|filmed|wrapped|signed|drafted|published|hit\s+theaters|arrived)\s+(?:on\s+|in\s+|back\s+in\s+)?(?:the\s+)?$", re.I)
_DESCRIBES_AFTER_RX = re.compile(
    r"^\s*(?:premiere|launch|release|debut|episode|finale|drop|opener|season|edition|cohort|class|model)\b", re.I)
_YEAR_WINDOW_BEFORE_RX = re.compile(
    r"\b(?:in|for|during|across|over|throughout|within|calendar(?:\s+year)?|cy|fy|full[- ]year|all\s+of|the\s+whole\s+of|year)\s+(?:the\s+)?$", re.I)
_YEAR_WINDOW_AFTER_RX = re.compile(r"^\s+(?:calendar\s+year|only|full[- ]year|in\s+full)\b", re.I)
_SPAN_RX = re.compile(
    r"\b(?:the\s+)?(?:(?P<first>first)\s+)?(?P<n>\d{1,3}|" + "|".join(_NUMWORD) + r")[\s-]*(?P<unit>days?|weeks?|months?)\s+"
    r"(?:of|after|from|following|since|post|starting|beginning)\s+(?:the\s+|its\s+)?(?:(?:premiere|launch|release|debut|drop|start)\s+)?(?:on\s+|of\s+)?", re.I)
_SINCE_RX = re.compile(r"\b(?P<w>since|starting(?:\s+from)?|as\s+of|from)\s+(?:the\s+)?$", re.I)
_OPEN_END_RX = re.compile(r"^\s*(?:,?\s*(?:to|through|thru|until|till)\s+(?:date|today|now|present|the\s+present)|\s+onwards?|\s+on(?:ward)?\b)", re.I)
_TODAY_RX = re.compile(r"^\s*(?:to|through|thru|until|till)\s+(?:date|today|now|present|the\s+present)\b", re.I)


class _Tok:
    __slots__ = ('kind', 'start', 'end', 'year', 'month', 'day', 'part', 'weak', 'text')

    def __init__(self, kind, start, end, year=None, month=None, day=None, part=None, weak=False, text=''):
        self.kind, self.start, self.end = kind, start, end
        self.year, self.month, self.day, self.part = year, month, day, part
        self.weak, self.text = weak, text

    def grain(self):
        if self.kind in ('iso', 'num', 'mdy', 'dmy'):
            return 'day'
        if self.kind in ('my', 'mon'):
            return 'month'
        return self.kind

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<{self.kind} {self.text!r} y={self.year} m={self.month} d={self.day} p={self.part}>"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _mon(v):
    return _MONTH_NUM.get(str(v or '').lower().rstrip('.'))


def _tokens(text):
    """Every date token in the text, non-overlapping, in order. At one
    position the longest token wins, so 'Q4 2025' is a quarter, never the
    year 2025; 'January 19, 2024' is one day, never a month-year."""
    found = []
    for kind, rx in _TOKEN_SHAPES:
        for m in rx.finditer(text):
            g = m.groupdict()
            tok = None
            if kind == 'iso':
                y, mo, d = _int(g['y']), _int(g['m']), _int(g['d'])
                if y and mo and d and 1 <= mo <= 12 and 1 <= d <= 31:
                    tok = _Tok(kind, m.start(), m.end(), y, mo, d)
            elif kind == 'num':
                mo, d, y = _int(g['m']), _int(g['d']), _int(g['y'])
                if not (mo and d and 1 <= mo <= 12 and 1 <= d <= 31):
                    continue
                if y is not None and y < 100:
                    y += 2000
                tok = _Tok(kind, m.start(), m.end(), y, mo, d, weak=(y is None))
            elif kind in ('mdy', 'dmy'):
                tok = _Tok(kind, m.start(), m.end(), _int(g.get('y')), _mon(g['mon']), _int(g['d']))
            elif kind == 'my':
                tok = _Tok(kind, m.start(), m.end(), _int(g['y']), _mon(g['mon']))
            elif kind == 'quarter':
                q = g.get('q') or g.get('q2') or _ORDWORD.get(str(g.get('qw') or '').lower())
                y = g.get('y') or g.get('y2') or g.get('y3')
                tok = _Tok(kind, m.start(), m.end(), _int(y), part=_int(q))
            elif kind == 'half':
                h = g.get('h') or _ORDWORD.get(str(g.get('hw') or '').lower())
                y = g.get('y') or g.get('y3')
                tok = _Tok(kind, m.start(), m.end(), _int(y), part=_int(h))
            elif kind == 'year':
                tok = _Tok(kind, m.start(), m.end(), _int(g['y']))
            elif kind == 'mon':
                raw = m.group('mon')
                low = raw.lower()
                # 'may' the verb, 'march' the verb, 'august' the adjective:
                # a bare short month reads as a month only when capitalized
                if low in ('may', 'march', 'august', 'mar', 'dec', 'sep', 'jan') and not raw[:1].isupper():
                    continue
                tok = _Tok(kind, m.start(), m.end(), None, _mon(raw), weak=True)
            if tok is None or (tok.month is not None and not 1 <= tok.month <= 12):
                continue
            tok.text = text[tok.start:tok.end]
            found.append(tok)
    found.sort(key=lambda t: (t.start, -(t.end - t.start)))
    out = []
    for t in found:
        if out and t.start < out[-1].end:
            continue          # inside the longer token already kept
        out.append(t)
    return out


def _clamp_day(y, m, d):
    return max(1, min(int(d), calendar.monthrange(y, m)[1]))


def _span(tok, year=None, month=None):
    """(first_day, last_day) the token covers once its year is known."""
    y = year if year is not None else tok.year
    m = month if month is not None else tok.month
    g = tok.grain()
    if g == 'day':
        d = date(y, m, _clamp_day(y, m, tok.day))
        return d, d
    if g == 'month':
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    if g == 'quarter':
        m1 = 3 * (tok.part - 1) + 1
        return date(y, m1, 1), date(y, m1 + 2, calendar.monthrange(y, m1 + 2)[1])
    if g == 'half':
        return (date(y, 1, 1), date(y, 6, 30)) if tok.part == 1 else (date(y, 7, 1), date(y, 12, 31))
    return date(y, 1, 1), date(y, 12, 31)


def _md(tok):
    """(month, day) for ordering two tokens inside a year."""
    g = tok.grain()
    if g == 'day':
        return tok.month, tok.day
    if g == 'month':
        return tok.month, 1
    if g == 'quarter':
        return 3 * (tok.part - 1) + 1, 1
    if g == 'half':
        return (1 if tok.part == 1 else 7), 1
    return 1, 1


def _resolve_pair(a, b, today):
    """ISO (start, end) for two tokens read as a range, resolving a
    missing year on either side from the other, or both from today."""
    if a.grain() == 'year' or b.grain() == 'year':
        if a.grain() != 'year' or b.grain() != 'year':
            return None       # 'from 2023 to March 2024' is not a shape we read
        if a.year > b.year:
            return None
        return _span(a)[0].isoformat(), _span(b)[1].isoformat()
    ya, yb = a.year, b.year
    if ya is None and yb is None:
        if a.month is None or b.month is None:
            return None
        yb = today.year
        ya = yb - 1 if _md(a) > _md(b) else yb
        if _span(b, yb)[1] > today:
            ya, yb = ya - 1, yb - 1
    elif ya is None:
        if a.month is None:
            return None
        ya = yb - 1 if _md(a) > _md(b) else yb
    elif yb is None:
        if b.month is None:
            return None
        yb = ya + 1 if _md(b) < _md(a) else ya
    try:
        s = _span(a, ya)[0]
        e = _span(b, yb)[1]
    except ValueError:
        return None
    if s > e:
        return None
    return s.isoformat(), e.isoformat()


def _same_month_day_range(text, today):
    """'January 19-21, 2024', 'Jan 19 to 21, 2024', 'between January 19
    and 21' (the year may be missing): one month, two days."""
    rx = re.compile(r"\b(?P<mon>" + _MON + r")\.?\s+(?P<d1>" + _DAYN + r")" + _ORD
                    + r"\s*(?:(?P<w>to|through|thru|until|till|and)|-|\u2013|\u2014)\s*(?P<d2>" + _DAYN + r")" + _ORD
                    + r"(?:,?\s*(?P<y>" + _YR + r"))?\b(?!\s*(?:%|percent|/|:))", re.I)
    for m in rx.finditer(text):
        mo = _mon(m.group('mon'))
        d1, d2 = int(m.group('d1')), int(m.group('d2'))
        if not mo or d1 > d2:
            continue
        if (m.group('w') or '').lower() == 'and' and not _PAIR_LEAD_RX.search(text[:m.start()]):
            continue
        y = _int(m.group('y'))
        if y is None:
            y = today.year
            if date(y, mo, _clamp_day(y, mo, d2)) > today:
                y -= 1
        s, e = date(y, mo, _clamp_day(y, mo, d1)), date(y, mo, _clamp_day(y, mo, d2))
        return {'start': s.isoformat(), 'end': e.isoformat(), 'phrase': m.group(0), 'kind': 'range', 'span': m.span()}
    return None


def _ranges(text, toks, today):
    for i in range(len(toks) - 1):
        a, b = toks[i], toks[i + 1]
        gap = text[a.end:b.start]
        jm = _RANGE_JOIN_RX.match(gap)
        if not jm:
            continue
        word = (jm.group('w') or '').lower()
        lead = text[max(0, a.start - 40):a.start]
        if word == 'and' and not _PAIR_LEAD_RX.search(lead):
            continue
        if jm.group('dash') and a.grain() == 'year' and b.grain() == 'year':
            continue          # '2023-2024 season' is a season, not a window
        if a.kind == 'num' and b.kind == 'num' and a.weak and b.weak and not _DATE_LEAD_RX.search(lead):
            continue          # '1/2 to 3/4 of viewers' is a fraction
        if a.kind == 'mon' and b.kind == 'mon':
            continue          # two bare months carry no year
        pair = _resolve_pair(a, b, today)
        if not pair:
            continue
        kind = 'range'
        if a.grain() == 'year' and b.grain() == 'year':
            kind = 'year_range'
        return {'start': pair[0], 'end': pair[1], 'phrase': text[a.start:b.end], 'kind': kind, 'span': (a.start, b.end)}
    return None


def _spans(text, toks, today):
    """'the first 28 days after <date>', 'the two weeks after <date>':
    the anchor day is day one."""
    by_start = {t.start: t for t in toks}
    for m in _SPAN_RX.finditer(text):
        n = _int(m.group('n')) or _NUMWORD.get(m.group('n').lower())
        if not n or n > 400:
            continue
        pos = m.end()
        tok = by_start.get(pos)
        if tok is None or tok.grain() not in ('day', 'month') or tok.month is None:
            continue
        year = tok.year
        if year is None:
            year = today.year
            if _span(tok, year)[0] > today:
                year -= 1
        try:
            s = _span(tok, year)[0]
        except ValueError:
            continue
        unit = m.group('unit').lower()
        if unit.startswith('day'):
            e = s + timedelta(days=n - 1)
        elif unit.startswith('week'):
            e = s + timedelta(days=7 * n - 1)
        else:
            mo = s.month - 1 + n
            y, mo = s.year + mo // 12, mo % 12 + 1
            e = date(y, mo, _clamp_day(y, mo, s.day)) - timedelta(days=1)
        return {'start': s.isoformat(), 'end': e.isoformat(), 'phrase': text[m.start():tok.end], 'kind': 'span', 'span': (m.start(), tok.end)}
    return None


def _open_ended(text, toks, today):
    """'since <date>', '<date> to date', 'from <date> onward': through today."""
    for t in toks:
        if t.kind == 'mon' or t.weak:
            continue
        after = text[t.end:t.end + 24]
        lead_at = max(0, t.start - 24)
        lead = text[lead_at:t.start]
        sm = _SINCE_RX.search(lead)
        om = _OPEN_END_RX.match(after)
        if not (sm or om):
            continue
        if sm and not om and sm.group('w').lower() == 'from':
            continue          # 'from <date>' with no end is a start, not a window
        year = t.year
        if year is None:
            year = today.year
            if _span(t, year)[0] > today:
                year -= 1
        try:
            s = _span(t, year)[0]
        except ValueError:
            continue
        if s > today:
            continue
        start_pos = lead_at + sm.start() if sm else t.start
        end_pos = t.end + (om.end() if om else 0)
        return {'start': s.isoformat(), 'end': today.isoformat(), 'phrase': text[start_pos:end_pos],
                'kind': 'since', 'span': (start_pos, end_pos)}
    return None


def _single_period(text, toks):
    """One month, quarter, half or calendar year named as the window."""
    for t in toks:
        g = t.grain()
        if g not in ('month', 'quarter', 'half', 'year') or t.year is None or t.kind == 'mon':
            continue
        lead = text[max(0, t.start - 40):t.start]
        after = text[t.end:t.end + 24]
        if _DESCRIBES_BEFORE_RX.search(lead) or _DESCRIBES_AFTER_RX.match(after):
            continue
        if g == 'year' and not (_YEAR_WINDOW_BEFORE_RX.search(lead) or _YEAR_WINDOW_AFTER_RX.match(after)):
            continue
        s, e = _span(t)
        return {'start': s.isoformat(), 'end': e.isoformat(), 'phrase': t.text, 'kind': g, 'span': (t.start, t.end)}
    return None


def parse_inline_window(text, today=None):
    """The explicit window the ask states, or None.

    {'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD', 'phrase': <the words>,
     'kind': 'range' | 'year_range' | 'span' | 'since' | 'month' |
             'quarter' | 'half' | 'year',
     'coarse': True for a single period (month, quarter, half, year),
     'span': (start_offset, end_offset) in the text}

    A two-sided range or a span off a date wins over a single period
    named elsewhere in the ask. Never raises."""
    try:
        t = str(text or '')
        if not t.strip() or len(t) > 6000:
            return None
        if today is None:
            today = datetime.now(timezone.utc).date()
        toks = _tokens(t)
        found = None
        if toks:
            found = _ranges(t, toks, today)
        if not found:
            found = _same_month_day_range(t, today)
        if not found and toks:
            found = _spans(t, toks, today) or _open_ended(t, toks, today) or _single_period(t, toks)
        if not found:
            return None
        if found['start'] > found['end']:
            return None
        found['coarse'] = found['kind'] in COARSE_KINDS
        found['phrase'] = ' '.join(str(found.get('phrase') or '').split())
        return found
    except Exception:
        return None


def window_label(start, end):
    """'Jan 19 2024 to Feb 15 2024' for the confirm copy (the house
    shape the reads print: month, day, year, no commas)."""
    try:
        s = datetime.strptime(str(start), '%Y-%m-%d').date()
        e = datetime.strptime(str(end), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return ''
    if s == e:
        return f"{s.strftime('%b')} {s.day} {s.year}"
    return f"{s.strftime('%b')} {s.day} {s.year} to {e.strftime('%b')} {e.day} {e.year}"
