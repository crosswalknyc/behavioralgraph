"""No data can be pulled from before January 1, 2021 (Jenna 2026-11-18).

Jenna, verbatim: "It should always say no data can be pulled from before
jan 1 2021. Paula Vazquez". Paula, at Dick Wolf Productions, asked for
viewing minutes on Prime Video for Psych from 12/30/2019, then for
Law & Order: Special Victims Unit and Chicago P.D. "for the year 2020",
and was handed figures for 2019 and 2020 as if they were on file.

The rule, on every surface: a request whose dates, year, season window
or "since" reaches before the floor is told so in plain words.

  * All of it before the floor: the sentence, nothing starts, nothing
    is charged, and the same question from the floor forward is offered.
  * It starts before and ends on or after: the sentence, the window
    starts on the floor, and the dates actually used are stated. For
    anything that costs money the brief shows the corrected window
    before approval.
  * Never a silent clamp, never figures presented as covering earlier
    time, never a build run quietly on another window.

The date itself lives in ``migration.event_window`` (``DATA_FLOOR``,
twin-synced with the engine host) and nowhere else; this module reads
it. What is here:

  ``read``            the windows a message states or implies, judged
                      against the floor. It leans on
                      ``prometheus.inline_dates`` so a year that is part
                      of a title or a name ("1923", "Blade Runner 2049",
                      "2020 Tokyo Olympics", "Class of 2019"), a birth or
                      founding year, and a question about an older
                      title's audience now never trip it.
  ``gate``            the chat turn: stop with the sentence, or rewrite
                      the ask onto the corrected window and carry the
                      note onto the reply.
  ``attach_note``     the note, placed first on the reply at the one
                      exit every reply passes (``envelope.wrap``).
  ``guard_draft``     a brief (profile, tracker, the key surface) after
                      every window binder has run.
  ``approve_stop``    the last check before an approval charges.
  ``read_note`` / ``hold_read``  the read model's hard rule, and the
                      check on what it returned.
  ``intake`` / ``valuation`` / ``tracker_window``  the guided flows.
  ``form_error`` / ``window_error``  the dashboard forms and the twins.

Pure apart from the clock. No model, no storage, no host import.
Regression: scripts/test_data_floor_2021.py.
"""
from __future__ import annotations

import contextvars
import re
from datetime import date, datetime, timedelta, timezone

from migration import event_window as _ew
from . import inline_dates as _idw

__all__ = ['FLOOR', 'SENTENCE', 'REFUSAL_CODE', 'read', 'gate', 'capability', 'stop_payload', 'set_note', 'pending_note',
           'reset_note', 'attach_note', 'guard_draft', 'approve_stop', 'read_note', 'study_note', 'analysis_note', 'hold_read', 'intake', 'valuation',
           'tracker_window', 'form_error', 'window_error', 'brief_stop_line', 'api_refusal_line', 'form_line']

FLOOR = _ew.DATA_FLOOR                       # read from the one shared place
SENTENCE = _ew.data_floor_sentence()         # "No data can be pulled from before January 1, 2021."
FLOOR_LABEL = _ew.data_floor_label()         # "January 1, 2021"
REFUSAL_CODE = 'window_before_data'          # the key surface's refusal_code for a window before the floor
MAX_LEN = 1500                               # a longer message (a pasted brief, a document) is the model's to read

_YR = r"(?:19|20)\d{2}"
_AUD = (r"(?:viewers?|viewing|viewership|fans?|audiences?|subscribers?|subs|sign[- ]?ups?|buyers?|purchases?|purchasers?|"
        r"orders?|users?|listeners?|players?|shoppers?|customers?|streams?|streaming|sales|minutes|hours|data|activity|"
        r"numbers?|figures?|totals?|reach|plays|views|watch\s*time)")
_NUMWORD = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20, 'a': 1, 'couple of': 2, 'a couple of': 2, 'few': 3, 'a few': 3}


# ---------------------------------------------------------------- small helpers
def _today(today=None):
    if isinstance(today, datetime):
        return today.date()
    if isinstance(today, date):
        return today
    return datetime.now(timezone.utc).date()


def _long(iso):
    return _ew.long_date_label(iso)


def _status(start, end=None):
    return _ew.floor_status(start, end)


def brief_stop_line():
    """A brief whose whole window is before the floor (the dashboard)."""
    return (f"{SENTENCE} I can run the same request from {FLOOR_LABEL} forward. Reply with a window that starts on or "
            f"after {FLOOR_LABEL} and I will line it up.")


def api_refusal_line():
    """The key surface's refusal_message."""
    return f"{SENTENCE} Submit a window that starts on or after {FLOOR_LABEL}."


def form_line():
    """A dashboard form whose start date is before the floor."""
    return f"{SENTENCE} Choose a start date on or after {FLOOR_LABEL}."


# ---------------------------------------------------------------- reading the words
_YEAR_JOIN_RX = re.compile(
    r"^\s*(?P<j>,|and|&|or|vs\.?|versus|against|compared\s+(?:to|with)|through|thru|to|until|-|\u2013|\u2014)\s*(?:the\s+)?"
    r"(?:year\s+)?(?P<y>" + _YR + r")\b(?![/-]\d)", re.I)
_COMPARE_JOIN_RX = re.compile(r"^(?:or|vs\.?|versus|against|compared\s+(?:to|with))$", re.I)
_DASH_RANGE_RX = re.compile(r"(?<![\d/(-])(?P<a>" + _YR + r")\s*(?:-|\u2013|\u2014)\s*(?P<b>" + _YR + r")(?![\d/)-])")
_DASH_LEAD_RX = re.compile(
    r"(?:\b(?:for|from|during|across|over|throughout|within|between|covering|window|dates?|date\s+range|period|in)\s*:?\s+"
    r"(?:the\s+)?(?:years?\s+)?|\b" + _AUD + r"\s+(?:in\s+|from\s+|for\s+)?)$", re.I)
_DASH_AFTER_SKIP_RX = re.compile(r"^\s*(?:seasons?|school\s+year|tour|edition|model|cohort|class|lineup|roster|draft)\b", re.I)
_NOUN_YEAR_RX = re.compile(r"\b" + _AUD + r"\s*[,:-]?\s*(?:in\s+)?(?P<y>" + _YR + r")(?:\s+only)?\s*(?=$|[.?!,;)])", re.I)
_YEAR_WORD_RX = re.compile(r"\byear\s*(?:-|\u2013|\u2014|:|=|,)\s*(?P<y>" + _YR + r")\b(?![/-]\d)", re.I)
_FISCAL_RX = re.compile(r"\b(?:(?:fy|cy)\s*-?\s*'?|fiscal(?:\s+year)?\s+)(?P<y>" + _YR + r")\b", re.I)
_REL_WORD_RX = re.compile(
    r"\b(?:trailing|last|past|previous|prior)\s+(?P<n>" + "|".join(sorted((k for k in _NUMWORD if k not in ('a',)), key=len, reverse=True))
    + r")\s+(?P<u>day|week|month|year|decade)s?\b", re.I)
_REL_DECADE_RX = re.compile(r"\b(?:the\s+)?(?:last|past|previous|prior)\s+decade\b", re.I)
_REL_DIGIT_RX = re.compile(r"\b(?:trailing|last|past|previous|prior)\s+(?P<n>\d{1,3})\s*(?P<u>day|week|month|year|decade)s?\b", re.I)
_REL_LEAD_RX = re.compile(r"\b(?:(?:over|in|for|during|across|within|throughout)\s+)?(?:the\s+)?$", re.I)
_ALL_TIME_RX = re.compile(
    r"\ball[- ]time\s+(?=" + _AUD + r"\b)"
    r"|\b" + _AUD + r"\s+(?:(?:for|over|across|in|of)\s+)?all[- ]time\b"
    r"|\b(?:for|over|across)\s+all\s+time\b"
    r"|\b(?:the\s+|its\s+|their\s+)?(?:entire|full|whole|complete)\s+history\b"
    r"|\bas\s+far\s+back\s+as\s+(?:you\s+can(?:\s+go)?|possible|it\s+goes|the\s+data\s+goes|we\s+can(?:\s+go)?|you\s+have|there\s+is(?:\s+data)?)"
    r"|\bsince\s+the\s+(?:very\s+)?beginning\b(?!\s+of)|\bsince\s+day\s+one\b"
    r"|\b(?:lifetime|life[- ]to[- ]date)\s+" + _AUD + r"\b", re.I)
# an open start the words leave to the subject's own history: only the read model knows the date
_ORIGIN_RX = re.compile(
    r"\bsince\s+(?:it|they|she|he|the\s+(?:show|series|film|movie|title|season|brand|app|service|platform|channel|podcast))\s+"
    r"(?:first\s+)?(?:premiered|launched|debuted|came\s+out|started|began|aired|dropped|was\s+released|opened)\b"
    r"|\bsince\s+(?:its\s+|the\s+)?(?:launch|premiere|debut|release|inception|start)\b|\ball[- ]time\b|\bof\s+all\s+time\b", re.I)


# a date that tells the reader about the subject (when it ran, came out, was founded, ended), not about the window
_DESC_LEAD_RX = re.compile(
    r"\b(?:ran|run|runs|running|aired|airing|airs|was\s+on|were\s+on|on\s+the\s+air|lasted|spanned|played|toured|broadcast|"
    r"existed|operated|released|premiered|launched|debuted|published|made|produced|filmed|shot|recorded|written|created|founded|"
    r"established|opened|born|formed|introduced|ended|finished|concluded|closed|cancell?ed|retired|died|won|lost|joined|left|"
    r"acquired|bought|sold|merged|rebranded|relaunched|peaked|came\s+out|went\s+public|dates|dated|dating)\s+"
    r"(?:(?:back\s+)?(?:in|on|from|between|during|through|until|till|since|around|circa|before|after|by)\s+)?(?:the\s+)?"
    r"(?:years?\s+)?$", re.I)
_DESC_VERB_RX = re.compile(
    r"\b(?:premiered|launched|debuted|released|came\s+out|aired|was\s+founded|were\s+founded|was\s+born|opened|ended|"
    r"was\s+cancell?ed|was\s+published|dropped|first\s+aired|last\s+aired|went\s+public|was\s+established)\b", re.I)
_PAST_EVENT_AFTER_RX = re.compile(
    r"^[^.?!;]{0,70}\b(?:launched|premiered|released|founded|debuted|came\s+out|started|began|opened|ended|won|aired|bought|"
    r"acquired|introduced|created|announced|signed|joined|left|died|retired|was\s+born)\b", re.I)
_SENT_START_RX = re.compile(r"(?:^|[.?!;:]\s*)\s*(?:back\s+)?(?:in|during)?\s*(?:the\s+)?(?:year\s+)?$", re.I)
# a question about the audience now, beside a year that only sets the scene
_NOW_Q_RX = re.compile(r"\b(?:who|what|which|how\s+many|how\s+much|are|is|do|does)\b[^.?!]{0,70}\b(?:now|today|currently|these\s+days|"
                       r"nowadays|still)\b", re.I)
_VS_YEARS_RX = re.compile(r"(?<![\d/-])(?P<a>" + _YR + r")\s*(?:vs\.?|versus|against|compared\s+(?:to|with))\s*(?:the\s+)?(?P<b>" + _YR
                          + r")(?![\d/-])", re.I)
_COMPARE_AND_RX = re.compile(r"\bcompar\w*\b[^.?!]{0,50}?(?<![\d/-])(?P<a>" + _YR + r")\s+(?:and|to|with)\s+(?:the\s+)?(?P<b>" + _YR
                             + r")(?![\d/-])", re.I)


def _in_passing(text, w):
    """True when a window the reader found only tells us about the
    subject: 'The Office ran from 2005 to 2013', 'films released between
    2015 and 2019', 'In 2019 Disney launched Disney+', or a single
    period beside a question about the audience now ('It was huge in
    2019. Who watches it now?')."""
    a, b = w.get('span') or (0, 0)
    if _DESC_LEAD_RX.search(text[max(0, a - 48):a]):
        return True
    # 'Hamilton premiered on Disney+ in July 2020': the verb sits a few words back in the same sentence, with no
    # audience word between it and the date
    m = None
    for m in _DESC_VERB_RX.finditer(text[max(0, a - 70):a]):
        pass
    if m:
        between = text[max(0, a - 70):a][m.end():]
        if not re.search(r"[.?!;]", between) and not re.search(r"\b" + _AUD + r"\b", between, re.I):
            return True
    if w.get('kind') in ('year', 'month', 'quarter', 'half'):
        if _SENT_START_RX.search(text[:a]) and _PAST_EVENT_AFTER_RX.match(text[b:]):
            return True
        if _NOW_Q_RX.search(text[:a] + ' ' + text[b:]):
            return True
    return False


def _year_window(y):
    return f"{int(y):04d}-01-01", f"{int(y):04d}-12-31"


def _described(text, a, b):
    try:
        return bool(_idw.describes_subject_year(text, a, b))
    except Exception:
        return False


def _mask(text, spans):
    t = text
    for a, b in spans:
        t = t[:a] + ('#' * max(0, b - a)) + t[b:]
    return t


def _extend_years(text, w):
    """A calendar year the words join to another ('across 2020 and
    2021', '2019, 2020 and 2021') is one range; a year set against
    another ('2020 vs 2025') is two windows. Returns the list."""
    out = [w]
    a, b = w['span']
    cur = w
    for _ in range(6):
        m = _YEAR_JOIN_RX.match(text[b:])
        if not m:
            break
        y2 = int(m.group('y'))
        y_at = b + m.start('y')
        if _described(text, y_at, y_at + 4):
            break
        s2, e2 = _year_window(y2)
        if _COMPARE_JOIN_RX.match(m.group('j').strip()):
            cur = {'start': s2, 'end': e2, 'kind': 'year', 'phrase': str(y2), 'span': (y_at, y_at + 4)}
            out.append(cur)
        elif e2 > cur['end']:
            cur['end'] = e2
            cur['kind'] = 'year_range'
            cur['span'] = (cur['span'][0], b + m.end())
            cur['phrase'] = text[cur['span'][0]:cur['span'][1]]
        elif s2 < cur['start']:
            cur['start'] = s2
            cur['kind'] = 'year_range'
            cur['span'] = (cur['span'][0], b + m.end())
            cur['phrase'] = text[cur['span'][0]:cur['span'][1]]
        b = b + m.end()
    return out


def _shift_years(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def _relative(text, td):
    """'the last ten years', 'past 6 years', 'trailing 72 months', 'the
    last decade': a window that ends today and opens that far back."""
    out = []
    for rx in (_REL_DIGIT_RX, _REL_WORD_RX):
        for m in rx.finditer(text):
            raw_n = m.group('n').lower()
            n = int(raw_n) if raw_n.isdigit() else _NUMWORD.get(' '.join(raw_n.split()))
            if not n or n > 400:
                continue
            unit = m.group('u').lower()
            if unit == 'day':
                start = td - timedelta(days=n)
            elif unit == 'week':
                start = td - timedelta(days=7 * n)
            elif unit == 'month':
                start = _shift_years(td, n // 12)
                mth = start.month - (n % 12)
                yr = start.year
                while mth <= 0:
                    mth += 12
                    yr -= 1
                try:
                    start = start.replace(year=yr, month=mth)
                except ValueError:
                    start = start.replace(year=yr, month=mth, day=28)
            elif unit == 'decade':
                start = _shift_years(td, 10 * n)
            else:
                start = _shift_years(td, n)
            out.append((m.start(), m.end(), start))
    for m in _REL_DECADE_RX.finditer(text):
        if not any(a <= m.start() < b for a, b, _ in out):
            out.append((m.start(), m.end(), _shift_years(td, 10)))
    wins = []
    for a, b, start in out:
        lead = _REL_LEAD_RX.search(text[max(0, a - 24):a])
        a2 = a - (len(lead.group(0)) if lead else 0)
        wins.append({'start': start.isoformat(), 'end': _idw.yesterday(td).isoformat(), 'kind': 'relative',
                     'phrase': text[a2:b], 'span': (a2, b), 'open': True})
    return wins


def windows(text, today=None, question=None):
    """Every window the words state or imply, in the order written, each
    {'start', 'end', 'kind', 'phrase', 'span', 'status'}. ``question``
    is the agent turn this text answers (a year in a reply about the
    subject describes the subject, per prometheus.inline_dates)."""
    t = str(text or '')
    if not t.strip() or len(t) > MAX_LEN:
        return []
    td = _today(today)
    out = []
    try:
        qk = _idw.question_kind(question) if question else ''
        if qk == 'subject' and len(t.split()) >= 5 and re.search(r"\b" + _AUD + r"\b", t, re.I):
            qk = ''     # a full ask typed under a which-subject question ('chicago pd minutes viewed in 2020') is read as an ask
        if qk:
            first = _idw.parse_inline_window(t, today=td, question=question)
            found = [first] if first and first.get('span') else []
            if qk != 'subject' and found:
                a, b = found[0]['span']
                found += _idw.all_windows(t[:a] + ('#' * (b - a)) + t[b:], today=td, limit=5)
        else:
            found = _idw.all_windows(t, today=td, limit=6)
        subject_reply = qk == 'subject'
        for w in found:
            w = dict(w)
            if w.get('kind') == 'year':
                out += _extend_years(t, w)
            else:
                out.append(w)
        out = [w for w in out if not _in_passing(t, w)]
        if not subject_reply:
            masked = _mask(t, [w['span'] for w in out])
            for m in _DASH_RANGE_RX.finditer(masked):
                a, b = int(m.group('a')), int(m.group('b'))
                if b < a or b - a > 60:
                    continue
                lead = masked[max(0, m.start() - 44):m.start()]
                if _DASH_AFTER_SKIP_RX.match(masked[m.end():m.end() + 20]) or _described(t, m.start(), m.start() + 4) \
                        or _DESC_LEAD_RX.search(t[max(0, m.start() - 48):m.start()]):
                    continue
                if not (_DASH_LEAD_RX.search(lead) or not lead.strip()):
                    continue
                out.append({'start': _year_window(a)[0], 'end': _year_window(b)[1], 'kind': 'year_range',
                            'phrase': m.group(0), 'span': m.span()})
            masked = _mask(t, [w['span'] for w in out])
            for rx in (_VS_YEARS_RX, _COMPARE_AND_RX):
                for m in rx.finditer(masked):
                    for g in ('a', 'b'):
                        ya = m.start(g)
                        if _described(t, ya, ya + 4) or _DESC_LEAD_RX.search(t[max(0, ya - 48):ya]):
                            continue
                        s, e = _year_window(m.group(g))
                        out.append({'start': s, 'end': e, 'kind': 'year', 'phrase': m.group(g), 'span': (ya, ya + 4)})
            masked = _mask(t, [w['span'] for w in out])
            for rx in (_YEAR_WORD_RX, _FISCAL_RX, _NOUN_YEAR_RX):
                for m in rx.finditer(masked):
                    ya = m.start('y')
                    if _described(t, ya, ya + 4) or any(a0 <= ya < b0 for a0, b0 in (w['span'] for w in out)):
                        continue
                    s, e = _year_window(m.group('y'))
                    span = (ya, m.end()) if rx is _NOUN_YEAR_RX else m.span()
                    w = {'start': s, 'end': e, 'kind': 'year', 'phrase': t[span[0]:span[1]], 'span': span}
                    if not _in_passing(t, w):
                        out.append(w)
            masked = _mask(t, [w['span'] for w in out])
            out += _relative(masked, td)
            masked = _mask(t, [w['span'] for w in out])
            m = _ALL_TIME_RX.search(masked)
            if m:
                out.append({'start': '', 'end': _idw.yesterday(td).isoformat(), 'kind': 'all_time', 'phrase': m.group(0),
                            'span': m.span(), 'open': True})
    except Exception:
        return []
    for w in out:
        w['status'] = 'straddle' if w.get('kind') == 'all_time' else _status(w.get('start'), w.get('end'))
        w['phrase'] = ' '.join(str(w.get('phrase') or '').split())
    out.sort(key=lambda w: (w.get('span') or (0, 0))[0])
    return out


def read(text, today=None, question=None):
    """What the words ask for against the floor, or None when nothing
    they state reaches before it.

    {'status': 'before' | 'straddle' | 'compare', 'windows': [...],
     'flagged': [the windows that reach before the floor]}

    'compare' is an ask with more than one window where at least one
    sits wholly before the floor ('2020 vs 2025'): the earlier side
    cannot be read, so nothing is clamped."""
    wins = windows(text, today=today, question=question)
    flagged = [w for w in wins if w.get('status') in ('before', 'straddle')]
    if not flagged:
        return None
    before = [w for w in flagged if w['status'] == 'before']
    if before and len(wins) > 1:
        status = 'compare' if any(w.get('status') in ('ok', 'straddle') for w in wins) else 'before'
    elif before:
        status = 'before'
    else:
        status = 'straddle'
    return {'status': status, 'windows': wins, 'flagged': flagged}


# ---------------------------------------------------------------- rewriting the ask
_FWD_LEAD_RX = re.compile(
    r"(?:\b(?:in|for|during|across|over|throughout|within|from|between|beginning|starting|of|covering)\s+)?(?:the\s+)?"
    r"(?:(?:calendar\s+|full\s+|whole\s+)?years?\s+(?:of\s+)?)?$", re.I)
_FWD_AFTER_RX = re.compile(r"^\s+(?:calendar\s+)?year\b", re.I)
_BETWEEN_LEAD_RX = re.compile(r"\bbetween\s+(?:the\s+)?$", re.I)


def _replace(text, a, b, new):
    left, right = text[:a], text[b:]
    if left and not left.endswith((' ', '(', '\n')):
        left += ' '
    if right and not right.startswith((' ', '.', ',', '?', '!', ')', ';', ':', '\n')):
        right = ' ' + right
    return left + new + right


_YEAR_SPAN_RX = re.compile(r"^(?P<a>" + _YR + r")(?P<j>\s*(?:to|through|thru|until|till|-|\u2013|\u2014)\s*(?:the\s+)?)(?P<b>" + _YR + r")$", re.I)


def _rewrite(text, flagged, forward=False, today=None):
    """The ask on the corrected window: each window that reaches before
    the floor is rewritten in place, last first so offsets hold. The
    rest of the message, its line breaks included, is left as typed.
    ``forward``: every flagged window becomes 'since <floor>'. A span of
    years keeps its own style ('2019 to 2026' reads '2021 to 2026'); an
    end that has not come yet is the last full day."""
    t = str(text or '')
    tail = ''
    floor = _ew.data_floor_date()
    yday = _idw.yesterday(_today(today)).isoformat()
    for w in sorted(flagged, key=lambda w: w['span'][0], reverse=True):
        a, b = w['span']
        kind = w.get('kind')
        if kind == 'all_time':
            m = re.search(r"(?:\b(?:for|over|across|in|of)\s+)?\ball[- ]time\b", t[a:b], re.I)
            if m:      # 'all time viewers of X' -> 'viewers of X since <floor>'
                t = t[:a + m.start()] + ' ' + t[a + m.end():]
                tail = f" since {FLOOR_LABEL}"
            continue
        if forward or kind in ('relative', 'since') or w.get('open'):
            lead = _FWD_LEAD_RX.search(t[max(0, a - 40):a])
            if lead and kind not in ('since',):
                a -= len(lead.group(0))
            aft = _FWD_AFTER_RX.match(t[b:b + 16])
            if aft:
                b += aft.end()
            t = _replace(t, a, b, f"since {FLOOR_LABEL}")
            continue
        ym = _YEAR_SPAN_RX.match(t[a:b].strip())
        if ym and (floor.month, floor.day) == (1, 1) and int(ym.group('b')) > floor.year:
            # '2019 through 2026', '2019-2026': the first year becomes the floor's own year, the rest stays as written
            lead_ws = len(t[a:b]) - len(t[a:b].lstrip())
            t = t[:a + lead_ws] + str(floor.year) + ym.group('j') + ym.group('b') + t[a + lead_ws + len(ym.group(0)):]
            continue
        new = f"{FLOOR_LABEL} through {_long(min(str(w['end']), yday))}"
        lead = _BETWEEN_LEAD_RX.search(t[max(0, a - 16):a])
        if lead:
            a -= len(lead.group(0))
            new = 'from ' + new
        t = _replace(t, a, b, new)
    return (t.rstrip() + tail).strip() if tail else t.strip()


# ---------------------------------------------------------------- "how far back does the data go"
_HOW_FAR_RX = re.compile(
    r"\bhow\s+far\s+back\b"
    r"|\b(?:when|what\s+(?:date|point|year|month)|(?:at|from)\s+what\s+(?:point|date))\s+(?:does|do|did|is|are)\s+"
    r"(?:the\s+|your\s+|our\s+|you\s+have\s+)?(?:data|history|numbers?|records?|coverage|measurement|tracking)\b[^?.!]{0,40}"
    r"\b(?:begin|start|go\s+back|goes?\s+back|available|cover)"
    r"|\b(?:earliest|oldest)\s+(?:date|data|day|year|month|window)\b(?=[^?.!]{0,40}\b(?:available|you\s+have|we\s+have|"
    r"i\s+can|can\s+i|you\s+can|on\s+file|on\s+record|in\s+the\s+data|covered|pull|go(?:es)?\s+back)\b)"
    r"|\b(?:data|history|numbers|records|coverage)\s+(?:go(?:es)?|went|reach(?:es)?|extends?)\s+back\b"
    r"|\bgo(?:es)?\s+back\s+(?:that\s+far|to\s+(?:19|20)\d{2})\b", re.I)
_TAG_Q_RX = re.compile(r"^\W*(?:does\s+it|do\s+you|can\s+(?:you|i|we)|is\s+(?:it|that)(?:\s+(?:possible|available))?|will\s+it)\W*$", re.I)


def asks_how_far_back(text):
    t = str(text or '')
    return bool(t.strip()) and len(t) <= MAX_LEN and bool(_HOW_FAR_RX.search(t))


def _strip_capability(text):
    """The ask with the 'does the data go back that far' part taken off,
    or '' when nothing else is left."""
    parts = re.split(r"(?<=[.?!])\s+|\s+\bbut\b\s+|\s*;\s*", str(text or ''))
    keep = [p.strip() for p in parts if p and p.strip() and not _HOW_FAR_RX.search(p) and not _TAG_Q_RX.match(p)]
    out = ' '.join(keep).strip(' ,')
    return '' if (not out or _HOW_FAR_RX.search(out)) else out


def capability(text, today=None):
    """'At what point does the data begin?': the date, in one line. Only
    when nothing the words state reaches before the floor (those asks
    get the stop or the corrected window). Returns a reply or ''."""
    if not asks_how_far_back(text):
        return ''
    wins = windows(text, today=today)
    if any(w.get('status') in ('before', 'straddle') for w in wins):
        return ''
    line = f"The data begins {FLOOR_LABEL}. {SENTENCE}"
    ok = [w for w in wins if w.get('status') == 'ok' and w.get('start') and w.get('end')]
    if ok:
        line += f" {_long(ok[0]['start'])} to {_long(ok[0]['end'])} is in range."
    return line


# ---------------------------------------------------------------- the chat turn
def _payload(reply, chips, status):
    out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
           'deck_angle': None, 'data_floor': status}
    chips = [c for c in (chips or []) if isinstance(c, dict) and c.get('label') and c.get('send')]
    if chips:
        out['options'] = chips      # a chip with its own send text (envelope.wrap carries label + send)
    return out


_CHIP_FILLER = frozenset(('only', 'need', 'just', 'year', 'years', 'want', 'that', 'this', 'please', 'give', 'have', 'from',
                          'with', 'what', 'about', 'same', 'thing', 'again', 'does', 'will', 'would', 'could', 'should'))


def _chip_ok(text):
    """A one-tap rerun is offered only when the ask still reads as a
    question once the window words are set aside (a bare 'only one year,
    2020' gets the sentence and no chip)."""
    t = str(text or '')
    for lab in (f"since {FLOOR_LABEL}", FLOOR_LABEL):
        t = t.replace(lab, ' ')
    t = re.sub(r"\b(?:through|from|to)\s+[A-Z][a-z]+ \d{1,2}, \d{4}", ' ', t)
    words = re.findall(r"[A-Za-z][A-Za-z'&+.-]*", t)
    solid = [w for w in words if len(w) >= 4 and w.lower() not in _CHIP_FILLER]
    return len(words) >= 2 and bool(solid)


def gate(text, today=None, question=None):
    """One chat turn against the floor.

    None                               nothing in the words reaches before it
    {'action': 'stop', 'payload': {...}}   the sentence is the reply; nothing runs
    {'action': 'rewrite', 'text': ..., 'note': ..., 'end': ...}
                                       the ask on the corrected window; the note leads the reply

    Never raises."""
    try:
        t = str(text or '')
        cap = capability(t, today=today)
        if cap:
            return {'action': 'stop', 'status': 'capability', 'payload': _payload(cap, [], 'capability')}
        hit = read(t, today=today, question=question)
        if not hit:
            return None
        status, flagged = hit['status'], hit['flagged']
        asks_cap = asks_how_far_back(t)
        base = _strip_capability(t) if asks_cap else t
        if status in ('before', 'compare'):
            chips = []
            if status == 'before':
                fwd = _rewrite(base, read(base, today=today)['flagged'], forward=True, today=today) if (base and read(base, today=today)) else ''
                if fwd and _chip_ok(fwd) and not read(fwd, today=today):
                    chips = [{'label': f"Run it from {FLOOR_LABEL} forward", 'send': fwd}]
                reply = _ew.floor_refusal_line()
            else:
                ok = [w for w in hit['windows'] if w.get('status') == 'ok' and w.get('start') and w.get('end')]
                reply = SENTENCE + (f" I can run {_long(ok[0]['start'])} to {_long(ok[0]['end'])} on its own, or any "
                                    f"comparison that starts on or after {FLOOR_LABEL}." if ok else
                                    f" I can run any comparison that starts on or after {FLOOR_LABEL}.")
            return {'action': 'stop', 'status': status, 'payload': _payload(reply, chips, status)}
        # the window crosses the floor: the ask runs on the corrected window and the reply says so first
        _yday = _idw.yesterday(_today(today)).isoformat()
        end = min(str(flagged[0].get('end') or _yday), _yday)
        hit2 = read(base, today=today) if base else None
        new = _rewrite(base, hit2['flagged'], today=today) if hit2 else ''
        clean = bool(new) and not read(new, today=today)
        if asks_cap or not clean:
            # a yes / no question about the data's reach, or words the rewrite could not settle: the sentence and the offer
            chips = [{'label': f"Run it from {FLOOR_LABEL} to {_long(end)}", 'send': new}] if (clean and _chip_ok(new)) else []
            reply = f"{SENTENCE} I can run this from {FLOOR_LABEL} to {_long(end)}."
            return {'action': 'stop', 'status': 'straddle', 'payload': _payload(reply, chips, 'straddle')}
        return {'action': 'rewrite', 'status': 'straddle', 'text': new, 'end': end, 'note': _ew.floor_window_line(end)}
    except Exception:
        return None


def stop_payload(text, today=None):
    """For a lane that prices or starts work straight from words (the
    priced read gate): the reply to give instead, or None when the
    words are clear of the floor. A crossing window stops here too,
    with the corrected window one tap away; nothing is quoted or
    charged on dates that cannot be read."""
    out = gate(text, today=today)
    if not out or out.get('status') == 'capability':
        return None
    if out['action'] == 'stop':
        return out['payload']
    reply = f"{SENTENCE} I can run this from {FLOOR_LABEL} to {_long(out.get('end'))}."
    chips = [{'label': f"Run it from {FLOOR_LABEL} to {_long(out.get('end'))}", 'send': out['text']}] if _chip_ok(out['text']) else []
    return _payload(reply, chips, 'straddle')


# ---------------------------------------------------------------- the note on the reply
_NOTE = contextvars.ContextVar('pm_data_floor_note', default=None)


def set_note(line, end=''):
    _NOTE.set({'line': str(line or ''), 'end': str(end or '')} if line else None)


def reset_note():
    _NOTE.set(None)


def pending_note():
    v = _NOTE.get()
    return v if isinstance(v, dict) and v.get('line') else None


def attach_note(env, raw, *, via='session'):
    """Place the floor note first on the reply to an ask that was moved
    onto the corrected window. Runs at the one exit every reply passes;
    once per ask. A reply that already says the sentence is left alone."""
    note = pending_note()
    if not note or not isinstance(env, dict):
        return env
    raw = raw if isinstance(raw, dict) else {}
    try:
        if env.get('kind') == 'error':
            return env
        line = note['line']
        text = str(env.get('text') or '')
        raw['data_floor'] = raw.get('data_floor') or 'straddle'
        if SENTENCE in text:
            return env
        env['text'] = (line + '\n\n' + text) if text.strip() else line
        for k in ('reply', 'error', 'message', 'question', 'summary', 'text'):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                if SENTENCE not in v:
                    raw[k] = line + '\n\n' + v
                break
    finally:
        reset_note()
    return env


# ---------------------------------------------------------------- briefs
_PAIR_RX = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+TO\s+(\d{4}-\d{2}-\d{2})\s*$", re.I)


def _label(start, end):
    return _ew.format_window_label(start, end) or f"{start} to {end}"


def guard_draft(draft, text='', decision=None, allow_ask=True, *, route=None, echo=None, today=None):
    """A brief after every window binder has run (profile, tracker, the
    key surface).

    All of a window before the floor: on the dashboard returns
    {'question': <the sentence and what to send>}; on a one-shot
    surface stashes the partner-safe refusal on the draft
    (``_v1_guard_refusal``) and returns None. Nothing is priced.

    A window that crosses the floor: it starts on the floor, the label
    and the engine / cut stamps follow, and the sentence with the dates
    used rides the draft (``data_floor_note``, and the Subject line
    through ``echo``) so the card shows the real window before anyone
    approves.

    ``route(draft, decision, start, end, win_label, date_label)`` and
    ``echo(draft, clause)`` are the host's own helpers. Never raises."""
    try:
        if not isinstance(draft, dict):
            return None
        d = str(decision or draft.get('decision') or '').strip().lower()

        def _stop(event=False):
            draft['data_floor_refusal'] = True
            if allow_ask:
                # an event that took place before the floor cannot be re-dated: the reader is not asked for other dates
                return {'question': (f"{SENTENCE} That event took place before that date, so it cannot be read. I can run "
                                     f"the same subject on any window from {FLOOR_LABEL} forward.") if event else brief_stop_line()}
            draft['_v1_guard_refusal'] = {'code': REFUSAL_CODE, 'message': api_refusal_line()}
            return None

        def _bind(end):
            lbl = _label(FLOOR, end)
            draft['date_range'] = {'start': FLOOR, 'end': end}
            draft['date_range_explicit'] = True
            draft['date_window_label'] = lbl
            if callable(route):
                try:
                    route(draft, d, FLOOR, end, lbl, lbl)
                except Exception:
                    pass

        ends = []
        ew = draft.get('event_window') if isinstance(draft.get('event_window'), dict) else None
        subiq = draft.get('subiq') if isinstance(draft.get('subiq'), dict) else None
        swd = draft.get('subiq_window_data') if isinstance(draft.get('subiq_window_data'), dict) else None
        # A tracker on a title that aired before the floor (a library show measured on later viewing). The window
        # the brief researched is the title's own air dates; the window the reader gave, when there is one, is the
        # one that counts. With a window of their own on or after the floor, that window stands and the air dates
        # are set aside. With none, the reader is told and asked for one: the air dates are never run.
        if swd and isinstance(swd.get('air_window'), dict) and swd['air_window'].get('start'):
            air, user_win = swd['air_window'], swd.get('user_window') if isinstance(swd.get('user_window'), dict) else None
            air_st = _status(air.get('start'), swd.get('through_date') or air.get('end'))
            if air_st in ('before', 'straddle') and user_win and _status(user_win.get('start'), user_win.get('end')) == 'ok':
                keep = {'start': str(user_win['start'])[:10], 'end': str(user_win['end'])[:10]}
                swd['air_window'], swd['user_window'], swd['kind'] = dict(keep), None, 'confirm'
                swd['through_date'], swd['season_to_date'] = None, False
                if subiq is not None:
                    subiq['air_window'] = dict(keep)
                    subiq.pop('season_to_date', None)
                    subiq.pop('through_date', None)
                draft['date_range'] = dict(keep)
                draft['date_range_explicit'] = True
            elif air_st == 'before' and not user_win:
                draft['data_floor_refusal'] = True
                title = str(swd.get('title') or (subiq or {}).get('title') or draft.get('subject') or 'This title').strip()
                if allow_ask:
                    out = {'question': (f"{SENTENCE} {title} aired before that date. Reply with the window to measure, starting "
                                        f"on or after {FLOOR_LABEL}, and I will line it up.")}
                    t0 = ' '.join(str(text or '').split())
                    if t0 and len(t0) <= 140 and not read(t0, today=today):
                        out['chip'] = f"{t0.rstrip('.?! ')} from {FLOOR_LABEL} to today"
                    return out
                draft['_v1_guard_refusal'] = {'code': REFUSAL_CODE, 'message': api_refusal_line()}
                return None
        if ew and ew.get('confident') and _status(ew.get('start'), ew.get('end')) == 'before':
            return _stop(event=True)
        dr = draft.get('date_range') if isinstance(draft.get('date_range'), dict) else {}
        # 1. the words themselves ('all time', 'the last ten years', a year no binder wrote onto the draft)
        hit = read(text, today=today) if str(text or '').strip() else None
        if hit and hit['status'] in ('before', 'compare'):
            return _stop()
        if hit and hit['status'] == 'straddle':
            bound = _status(dr.get('start'), dr.get('end'))
            if bound in ('', 'ok') and not (ew and ew.get('confident')):
                end = hit['flagged'][0].get('end') or _idw.yesterday(_today(today)).isoformat()
                _bind(end)
                ends.append(end)
                dr = draft['date_range']
        # 2. the windows on the draft
        st = _status(dr.get('start'), dr.get('end'))
        if st == 'before':
            return _stop()
        if st == 'straddle':
            end = str(dr.get('end') or '')[:10] or _idw.yesterday(_today(today)).isoformat()
            _bind(end)
            ends.append(end)
        for key, lbl_key in (('cut_date_range', 'cut_window_label'), ('engine_date_range', None)):
            m = _PAIR_RX.match(str(draft.get(key) or ''))
            if not m:
                continue
            st = _status(m.group(1), m.group(2))
            if st == 'before':
                return _stop()
            if st == 'straddle':
                draft[key] = f"{FLOOR} TO {m.group(2)}"
                if lbl_key:
                    draft[lbl_key] = _label(FLOOR, m.group(2))
                ends.append(m.group(2))
        if ew and ew.get('start'):
            st = _status(ew.get('start'), ew.get('end'))
            if st == 'before':
                return _stop()
            if st == 'straddle':
                ew['start'] = FLOOR
                ends.append(str(ew.get('end') or '')[:10])
        for holder, key in ((subiq, 'air_window'), (swd, 'air_window'), (swd, 'user_window')):
            win = holder.get(key) if isinstance(holder, dict) and isinstance(holder.get(key), dict) else None
            if not win or not win.get('start'):
                continue
            w_end = (subiq or {}).get('through_date') if (holder is subiq) else None
            st = _status(win.get('start'), w_end or win.get('end'))
            if st == 'before':
                return _stop()
            if st == 'straddle':
                win['start'] = FLOOR
                ends.append(str(w_end or win.get('end') or '')[:10])
        note = pending_note()
        if ends or note:
            end = next((e for e in ends if e), '') or (note or {}).get('end') or ''
            line = _ew.floor_window_line(end or None)
            draft['data_floor_note'] = line
            try:
                (echo if callable(echo) else _echo_identity)(draft, line)
            except Exception:
                pass
        return None
    except Exception:
        return None


def _echo_identity(draft, clause):
    """Carry a line onto the brief's Subject line (``resolved_identity``),
    the way the app's own bound-value echo does. Idempotent."""
    clause = ' '.join(str(clause or '').split())
    if not clause:
        return
    rid = str(draft.get('resolved_identity') or '').strip()
    if clause.lower() in rid.lower():
        return
    if rid:
        draft['resolved_identity'] = f"{rid.rstrip('.')}; {clause}"
    else:
        subject = str(draft.get('subject') or draft.get('name') or '').strip()
        draft['resolved_identity'] = f"{subject}; {clause}" if subject else clause


def approve_stop(draft):
    """The last check before an approval charges or starts anything: a
    draft whose window still starts before the floor (a brief drawn up
    before this rule, a stale card) is not run. Returns the line to
    give instead, or ''. Never raises."""
    try:
        if not isinstance(draft, dict):
            return ''
        if _ew.spec_floor_status(draft) in ('before', 'straddle'):
            return brief_stop_line()
    except Exception:
        return ''
    return ''


# ---------------------------------------------------------------- reads
def read_note():
    """The hard rule a read prompt carries, every time."""
    return (
        "DATA FLOOR (HARD RULE)\n"
        "======================\n"
        f"No data exists before {FLOOR} ({FLOOR_LABEL}). window_start is never earlier than {FLOOR}, and no figure, "
        "row, label or sentence states or implies a count for any day before it. This holds whatever an earlier turn "
        "of the conversation said.\n"
        f"- When the ask reaches before that date and also covers time on or after it (a range that starts earlier, "
        f"'since' an earlier date, 'all time', 'since it premiered' for something that premiered earlier, a season that "
        f"aired across it): read from {FLOOR} only, set window_start to {FLOOR}, and add \"floor_applied\": true to the "
        "JSON object.\n"
        "- When the ask wants figures for a period that ended before that date (a calendar year, a season's original "
        "run, a premiere week, an opening weekend): return action=decline with decline_reason=\"before_data_floor\". "
        "Produce no number.\n"
        "- A question about an older title, brand, event or person with no window stated is a normal question on the "
        "standing window: read who its audience is now. A year that is part of a title or a name, a birth year, a "
        "founding year or a release year is not a window.\n\n")


def analysis_note():
    """The same rule for a page analysis: it answers in prose, so the
    rule is about what it may say and offer."""
    return (
        "DATA FLOOR (HARD RULE)\n"
        "======================\n"
        f"No data exists before {FLOOR} ({FLOOR_LABEL}). Never state or imply a figure for any day before it, and never "
        "offer to pull, build, quote or look up a window that starts before it, whatever an earlier turn said. When the "
        f"message asks for earlier time, say this sentence, verbatim: \"{SENTENCE}\" Then offer the same question from "
        f"{FLOOR_LABEL} forward. A year that is part of a title or a name, a birth year, a founding year or a release "
        "year is not a window, and a question about an older title's audience now is a normal question.\n\n")


def study_note():
    """The same rule for a study prompt whose answer carries a window
    and no decline field."""
    return (
        "DATA FLOOR (HARD RULE)\n"
        "======================\n"
        f"No data exists before {FLOOR} ({FLOOR_LABEL}). window_start is never earlier than {FLOOR}, and no figure, "
        "row, label or sentence states or implies a count for any day before it, whatever an earlier turn said. When the "
        f"window the ask points at opened earlier, it opens on {FLOOR} and the headline begins with this sentence, "
        f"verbatim: \"{SENTENCE}\" A year that is part of a title or a name, or a release year, is not a window.\n\n")


_TIME_DIM_RX = re.compile(r"\b(?:month|months|monthly|year|years|yearly|annual|quarter|quarters|quarterly|week|weeks|weekly|"
                          r"day|days|daily|date|dates|period|periods|time|timeline|trend|series)\b", re.I)
_NOT_TIME_DIM_RX = re.compile(r"\b(?:release|released|premiere|premiered|birth|born|model|launch|launched|founded|vintage|"
                              r"cohort|class|graduat|built|made|publication|published|age|decade\s+of)\b", re.I)
_LABEL_PERIOD_RX = re.compile(
    r"\b(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?(?P<y1>" + _YR + r")"
    r"|q[1-4]\s*(?P<y2>" + _YR + r")|(?P<y3>" + _YR + r")\s*q[1-4]|^(?P<y4>" + _YR + r")\b)", re.I)


def _label_before_floor(label):
    m = _LABEL_PERIOD_RX.search(str(label or '').strip())
    if not m:
        return False
    y = next((m.group(g) for g in ('y1', 'y2', 'y3', 'y4') if m.group(g)), None)
    return bool(y) and int(y) < _ew.data_floor_date().year


def read_reaches_before(data):
    """True when a read the model returned states a window, or a time
    series row, before the floor."""
    if not isinstance(data, dict):
        return False
    if str(data.get('decline_reason') or '').strip().lower() == 'before_data_floor':
        return True
    if _status(data.get('window_start'), data.get('window_end')) in ('before', 'straddle'):
        return True
    bd = data.get('breakdown') if isinstance(data.get('breakdown'), dict) else {}
    dim = ' '.join(str(bd.get(k) or '') for k in ('dimension', 'title'))
    if dim.strip() and _TIME_DIM_RX.search(dim) and not _NOT_TIME_DIM_RX.search(dim):
        for row in (bd.get('rows') or []):
            if isinstance(row, dict) and _label_before_floor(row.get('label')):
                return True
    return False


def hold_read(data, question='', today=None):
    """The check on what the read model returned.

    A read that states a window or a time row before the floor never
    lands: the reply is the sentence with the offer, it carries no
    figure, and a charge on it is returned by the caller (the payload
    is marked ``data_floor``). A read the model moved onto the floor
    (``floor_applied``, or an all-time / since-it-premiered ask that
    now starts on the floor) says the sentence first. Returns the
    payload to give instead, or None (``data`` may be changed in
    place). Never raises."""
    try:
        if not isinstance(data, dict):
            return None
        if read_reaches_before(data):
            out = _payload(_ew.floor_refusal_line(), [], 'before')
            out['not_quantifiable'] = 'data_floor'     # a stated reason: the read lands as this line, never as an empty pass
            return out
        ws = str(data.get('window_start') or '').strip()[:10]
        moved = bool(data.get('floor_applied')) or (ws == FLOOR and bool(_ORIGIN_RX.search(str(question or ''))))
        if moved and ws and _status(ws, data.get('window_end')) == 'ok':
            head = str(data.get('headline') or '').strip()
            if SENTENCE not in head:
                data['headline'] = (SENTENCE + ' ' + head).strip()
            data['_data_floor'] = 'straddle'
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- guided flows
def intake(parsed):
    """A guided brief the reader is about to confirm (a journey, a twin):
    None when its window is clear; {'refuse': reply} when all of it is
    before the floor; {'note': line} when it crossed and now starts on
    the floor (``parsed`` is changed in place). Never raises."""
    try:
        if not isinstance(parsed, dict):
            return None
        holders = []
        if parsed.get('start_date'):
            holders.append((parsed, 'start_date', 'end_date'))
        w = parsed.get('window') if isinstance(parsed.get('window'), dict) else None
        if w and w.get('start'):
            holders.append((w, 'start', 'end'))
        ends = []
        for h, ks, ke in holders:
            st = _status(h.get(ks), h.get(ke))
            if st == 'before':
                return {'refuse': f"{SENTENCE} Send a window that starts on or after {FLOOR_LABEL} and I will line it up."}
            if st == 'straddle':
                h[ks] = FLOOR
                if h is w and w.get('label'):
                    w['label'] = _label(FLOOR, str(w.get(ke) or '')[:10]) if w.get(ke) else w['label']
                ends.append(str(h.get(ke) or '')[:10])
        if ends:
            return {'note': _ew.floor_window_line(next((e for e in ends if e), None))}
    except Exception:
        return None
    return None


def valuation(draft):
    """A valuation's campaign dates and its before window.

    ('', '')               clear
    ('refuse', reply)      the campaign ended before the floor
    ('note', line)         the campaign crossed the floor (it now starts
                           on it; ``draft`` is changed in place) and/or
                           its matched before window reaches before the
                           floor (that window starts on the floor)
    Never raises."""
    try:
        d = draft if isinstance(draft, dict) else {}
        cs, ce = str(d.get('campaign_start') or '')[:10], str(d.get('campaign_end') or '')[:10]
        st = _status(cs, ce)
        if st in ('', ):
            return '', ''
        if st == 'before':
            return 'refuse', f"{SENTENCE} Which campaign dates on or after {FLOOR_LABEL} should I use?"
        parts = []
        if st == 'straddle':
            d['campaign_start'] = cs = FLOOR
            if d.get('attr_start') and str(d['attr_start'])[:10] < FLOOR:
                d['attr_start'] = FLOOR
            parts.append(f"The campaign window starts {FLOOR_LABEL}.")
        a, b = _ew.parse_iso_date(cs), _ew.parse_iso_date(ce)
        if a and b and (a - timedelta(days=max((b - a).days + 1, 1))) < _ew.data_floor_date():
            parts.append(f"The before window starts {FLOOR_LABEL}.")
        if not parts:
            return '', ''
        return 'note', SENTENCE + ' ' + ' '.join(parts)
    except Exception:
        return '', ''


def tracker_window(start, end, lookback_days=180):
    """A Subscriber IQ tracker's window as it is locked in.

    {'refuse': message}                 all of it is before the floor
    {'start': iso, 'lead': '', 'lookback': ''}   clear
    {'start': floor, 'lead': <sentence + the window starts ...>, 'lookback': ...}
    ``lookback`` is the line for an exclusion look-back that would
    reach before the floor. Never raises."""
    out = {'start': start, 'lead': '', 'lookback': ''}
    try:
        st = _status(start, end)
        if st == 'before':
            return {'refuse': (f"{SENTENCE} Send a window that starts on or after {FLOOR_LABEL}, as two dates like "
                               f"2025-11-16 to 2026-01-11, and I will lock it in.")}
        s = start
        if st == 'straddle':
            s = FLOOR
            out.update(start=FLOOR, lead=f"{SENTENCE} The window starts {FLOOR_LABEL}. ")
        sd = _ew.parse_iso_date(str(s)[:10])
        if sd and (sd - timedelta(days=int(lookback_days))) < _ew.data_floor_date():
            out['lookback'] = (f" That look-back starts {FLOOR_LABEL} as well." if out['lead'] else
                               f" {SENTENCE} That look-back starts {FLOOR_LABEL}.")
    except Exception:
        return {'start': start, 'lead': '', 'lookback': ''}
    return out


# ---------------------------------------------------------------- forms and direct windows
_FORM_PATH_RX = re.compile(
    r"^/api/(?:submit|submit-rerun|check-cache|incidence-check|attribution/[\w-]+|talent-fit/[\w-]+|roas-iq/submit|"
    r"ecommerce-iq/submit|brand-partnership-iq/submit|journey-iq/(?:submit|estimate-audience)|sentiment-iq/submit|"
    r"share-of-time/(?:run|analyze))/?$")
_FORM_START_KEYS = ('start_date', 'sample_start', 'behavior_start', 'campaign_start', 'sample_start_date', 'behavior_start_date',
                    'event_start', 'pre_start', 'compare_start_date')


def window_error(start, end=None):
    """The line for a window handed in as dates (a form, a twin build),
    or '' when it starts on or after the floor or carries no date."""
    return form_line() if _status(start, end) in ('before', 'straddle') else ''


def form_error(path, method, payload):
    """A dashboard form submit whose start date is before the floor: the
    line the form shows, or ''. Only the submit routes that start a pull
    are read; nothing else in the app is touched. Never raises."""
    try:
        if str(method or '').upper() != 'POST' or not _FORM_PATH_RX.match(str(path or '')):
            return ''
        if not isinstance(payload, dict):
            return ''
        for k in _FORM_START_KEYS:
            v = payload.get(k)
            if v and _status(str(v).strip()[:10]) in ('before', 'straddle'):
                return form_line()
        # a journey's look-back sits before its start date: it cannot reach behind the floor either
        if 'journey-iq' in str(path) and payload.get('start_date') and payload.get('lookback_days') not in (None, ''):
            sd = _ew.parse_iso_date(str(payload.get('start_date')).strip()[:10])
            n = int(float(payload.get('lookback_days')))
            if sd and n > 0 and (sd - timedelta(days=n)) < _ew.data_floor_date():
                first = _ew.data_floor_date() + timedelta(days=n)
                return (f"{SENTENCE} With a {n}-day look-back, choose a start date on or after "
                        f"{_long(first.isoformat())}.")
    except Exception:
        return ''
    return ''
