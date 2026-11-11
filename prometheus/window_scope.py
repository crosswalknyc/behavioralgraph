"""The window a build or a read runs on is the one the reader asked for
(Jenna 2026-11-10, after Armine Meseldzhyan at Lionsgate and Liz's first
read through the key surface).

Three slips on one day, one rule each:

1. A year that describes the subject is not a window. Armine wrote
   "Cruel Summer series was in 2021 and it only has 2 seasons for now",
   then "can we build a profile for cruel summer series"; the binder
   read the earlier turn's "in 2021" and the build ran on Jan 1 to Dec
   31, 2021. Only words that ask for a window set one ("for 2021", "in
   calendar 2021", "viewers in 2021", dates written out). The reader in
   ``prometheus.inline_dates`` now skips a described year on every
   surface and reads a reply against the question it answers; this
   module carries the two pieces the binder in app.py needs around it:
   ``agent_question_before`` (the question a turn answers) and
   ``drop_described_year_window`` (a calendar year the model stamped on
   the draft from a description goes back to the standing default).

2. An open end lands on yesterday. "to date", "so far", "to the most
   recent date there is data", "year to date", "this year" end on the
   UTC day before today: never today, never a future date, never an
   earlier day the model happened to pick (her Mad Men 2025 vs 2026
   compare read "latest data" as Sep 14 on Nov 10). ``read_windows``
   resolves the windows a read ask states, ``read_window_note`` hands
   them to the read as a hard note, ``hold_read_window`` holds the
   finished read to them.

3. The retired Jul 1 2025 to Jun 30 2026 pair is never produced.
   ``standing_default`` is the one fallback every caller shares: the
   trailing 12 months from ``migration.event_window.default_window``,
   computed by hand when that module cannot be loaded, never a frozen
   pair. ``default_label`` is its plain label.

Pure apart from the clock. Regression:
scripts/test_pm_window_scope_2026_11_10.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from . import inline_dates as _idw

__all__ = ['agent_question_before', 'drop_described_year_window', 'standing_default', 'default_label', 'label_for',
           'read_windows', 'read_window_note', 'hold_read_window', 'yesterday_iso']


# ---------------------------------------------------------------- the default
def _today(today=None):
    if isinstance(today, datetime):
        return today.date()
    if isinstance(today, date):
        return today
    return datetime.now(timezone.utc).date()


def standing_default(today=None):
    """(start_iso, end_iso): the trailing 12 calendar months ending
    today UTC. Never the retired fiscal pair."""
    try:
        from migration.event_window import default_window as _dw
        pair = _dw(today)
        if pair and pair[0] and pair[1]:
            return str(pair[0])[:10], str(pair[1])[:10]
    except Exception:
        pass
    t = _today(today)
    try:
        s = t.replace(year=t.year - 1)
    except ValueError:                       # Feb 29
        s = t.replace(year=t.year - 1, day=28)
    return s.isoformat(), t.isoformat()


def default_label(today=None):
    """'Nov 11, 2025 to Nov 11, 2026' for the standing default."""
    s, e = standing_default(today)
    try:
        from migration.event_window import format_window_label as _fl
        lbl = _fl(s, e)
        if lbl:
            return lbl
    except Exception:
        pass
    return _idw.window_label(s, e) or f"{s} to {e}"


def label_for(start, end):
    """The plain label of a window ('Jan 19 to Feb 15, 2024'), or ''."""
    try:
        from migration.event_window import format_window_label as _fl
        return _fl(start, end) or ''
    except Exception:
        return _idw.window_label(start, end) if (start and end) else ''


def yesterday_iso(today=None):
    return _idw.yesterday(_today(today)).isoformat()


# ---------------------------------------------------------------- the build
def agent_question_before(history):
    """The agent turn the next user message answers: the last turn of the
    history when it is the agent's, else ''. (A user turn in between
    means the message is not a reply to it.)"""
    for t in reversed(list(history or [])):
        if not isinstance(t, dict):
            continue
        role = str(t.get('role') or '').lower()
        if role in ('agent', 'assistant', 'prometheus', 'bot'):
            return str(t.get('text') or t.get('content') or '')
        if role == 'user':
            return ''
    return ''


def _user_texts(user_text, history, turns=4):
    out = [str(user_text or '')]
    recent = [t for t in (history or [])[-8:] if isinstance(t, dict) and str(t.get('role') or '').lower() == 'user']
    out += [str(t.get('text') or t.get('content') or '') for t in recent[-turns:]]
    return [t for t in out if t.strip()]


def drop_described_year_window(draft, user_text, history=None, today=None, is_default=None):
    """Undo a calendar-year window that came from a description.

    The model reads the thread too. When the draft carries a window that
    sits inside one calendar year, the reader's own words (this ask and
    the last four user turns) mention that year only to describe the
    subject ("was in 2021", "came out in 2021", "aired in 2021"), and no
    turn states a window, the window is removed: the range, the explicit
    flag, the label and the engine / cut stamps go, and the caller's
    standing default binds next. A confident event window is left alone.
    Returns the year dropped, or None. Never raises."""
    try:
        if not isinstance(draft, dict):
            return None
        ew = draft.get('event_window')
        if isinstance(ew, dict) and ew.get('confident'):
            return None
        dr = draft.get('date_range') if isinstance(draft.get('date_range'), dict) else {}
        start, end = str(dr.get('start') or '').strip(), str(dr.get('end') or '').strip()
        if not (re.match(r"^\d{4}-\d{2}-\d{2}$", start) and re.match(r"^\d{4}-\d{2}-\d{2}$", end)):
            return None
        if start[:4] != end[:4]:
            return None
        try:
            if callable(is_default) and is_default(start, end):
                return None
        except Exception:
            pass
        year = int(start[:4])
        texts = _user_texts(user_text, history)
        told = set()
        for t in texts:
            told |= _idw.described_years(t)
        if year not in told:
            return None
        td = _today(today)
        for t in texts:
            w = _idw.parse_inline_window(t, today=td)
            if w and w['start'][:4] <= str(year) <= w['end'][:4]:
                return None          # the reader also asked for that year as a window
        for k in ('date_range', 'date_range_explicit', 'date_window_label', 'engine_date_range',
                  'cut_date_range', 'cut_window_label'):
            draft.pop(k, None)
        try:
            print(f"[window-scope] {draft.get('subject') or draft.get('name')!r}: {year} only describes the "
                  f"subject in the reader's words; the {start} to {end} window is dropped for the standing default")
        except Exception:
            pass
        return year
    except Exception:
        return None


# ---------------------------------------------------------------- the read
_VS_YEARS_RX = re.compile(r"\b((?:19|20)\d{2})\s*(?:vs\.?|versus|against|compared\s+(?:to|with)|and)\s*((?:19|20)\d{2})\b", re.I)


def read_windows(text, today=None):
    """The windows a read ask states, open ends resolved to yesterday.

    [{'start', 'end', 'open': bool, 'phrase'}], in the order written;
    [] when the ask states none or no open end is involved (a closed
    window is the model's to read as before). 'compare 2025 vs 2026 to
    date' is calendar 2025 and Jan 1 2026 through yesterday; '1/1/2025
    - 12/30/2025 vs 1/1/2026 - to most recent date there is data' is the
    first range as written and Jan 1 2026 through yesterday."""
    t = str(text or '')
    if not t.strip() or not _idw.open_end_phrase(t):
        return []
    td = _today(today)
    yday = _idw.yesterday(td)
    wins = _idw.all_windows(t, today=td)
    out = []
    for w in wins:
        out.append({'start': w['start'], 'end': w['end'], 'open': w.get('kind') == 'since',
                    'phrase': w.get('phrase') or ''})
    for w in out:
        if w['open'] or w['end'] > yday.isoformat():
            w['end'] = max(w['start'], yday.isoformat())    # '2025 to 2026 to date' closes on yesterday too
            w['open'] = True
    if not any(w['open'] for w in out):
        return []
    # 'compare 2025 vs 2026 to date': the closed year beside the open one
    m = _VS_YEARS_RX.search(t)
    if m and len(out) == 1:
        a, b = int(m.group(1)), int(m.group(2))
        if out[0]['start'][:4] == str(b) and a < b:
            out.insert(0, {'start': f"{a}-01-01", 'end': f"{a}-12-31", 'open': False, 'phrase': str(a)})
    return out


def _lbl(iso):
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day} {d.year}"


def read_window_note(text, today=None):
    """The hard note a read prompt carries when the ask leaves a range
    open, or ''. States each window in ISO and in the house label."""
    wins = read_windows(text, today=today)
    if not wins:
        # no open end; a year the words only use to describe the subject is still worth saying once
        try:
            years = sorted(_idw.described_years(text))
            if years and not _idw.parse_inline_window(str(text or ''), today=_today(today)):
                ys = ', '.join(str(y) for y in years)
                return ("STATED WINDOW (HARD RULE)\n=========================\n"
                        f"The ask mentions {ys} only to describe the subject (when it aired, came out or was released). "
                        "That is not a window. Use the standing window unless the ask states another one in so many words.\n\n")
        except Exception:
            pass
        return ''
    yday = yesterday_iso(today)
    lines = [
        "STATED WINDOW (HARD RULE)",
        "=========================",
        f"The ask leaves its range open ('{_idw.open_end_phrase(text)}'). An open end is the last full day, "
        f"{yday} ({_lbl(yday)}): never today, never a later date, never an earlier date of your choosing.",
    ]
    for i, w in enumerate(wins, 1):
        lines.append(f"Window {i}: {w['start']} to {w['end']} ({_lbl(w['start'])} to {_lbl(w['end'])})"
                     + (", open end" if w['open'] else ''))
    last = wins[-1]
    lines.append(f"Use exactly these dates in every label and line. window_start is {wins[0]['start']} and "
                 f"window_end is {last['end']}; the window line states {_lbl(last['end'])} as the last day covered.")
    return '\n'.join(lines) + '\n\n'


_LABEL_DATE_RX = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b")
_MON_NUM = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}


def _swap_end_in(textv, old_iso, new_iso):
    """Rewrite a label's stale end date ('Sep 14 2026', 'Sep 14, 2026',
    '2026-09-14') to the held one, keeping the label's own style."""
    s = str(textv or '')
    if not s or old_iso == new_iso:
        return s
    s = s.replace(old_iso, new_iso)
    try:
        od, nd = date.fromisoformat(old_iso), date.fromisoformat(new_iso)
    except ValueError:
        return s

    def _sub(m):
        mon = _MON_NUM.get(m.group(1)[:3].lower())
        if mon == od.month and int(m.group(2)) == od.day and int(m.group(3)) == od.year:
            comma = ',' if ',' in m.group(0) else ''
            return f"{nd.strftime('%b')} {nd.day}{comma} {nd.year}"
        return m.group(0)
    s = _LABEL_DATE_RX.sub(_sub, s)
    # 'so far in 2026 through Sep 14': the day with no year behind a range word
    bare = re.compile(r"\b(through|thru|to|until|till|ending)\s+" + od.strftime('%b') + r"[a-z]*\.?\s+" + str(od.day)
                      + r"(?:st|nd|rd|th)?\b(?!,?\s*(?:19|20)\d{2})", re.I)
    return bare.sub(lambda m: f"{m.group(1)} {nd.strftime('%b')} {nd.day}", s)


def hold_read_window(res, question, today=None):
    """Hold a finished read to the open end the ask stated.

    When the ask leaves its range open, the read's ``window_end`` is
    yesterday; a different end the model wrote (an earlier day it took
    for 'latest', today, a future date) is replaced in ``window_end``,
    the window label, the headline, the reads and the metric / breakdown
    labels. With no open end in the ask, only a window that ends after
    today is pulled back to yesterday. Returns the dict. Never raises."""
    try:
        if not isinstance(res, dict):
            return res
        td = _today(today)
        yday = _idw.yesterday(td).isoformat()
        we = str(res.get('window_end') or '').strip()[:10]
        wins = read_windows(question, today=td)
        target = ''
        if wins:
            target = wins[-1]['end']
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", we) and we > td.isoformat():
            target = yday
        if not target or not re.match(r"^\d{4}-\d{2}-\d{2}$", we) or we == target:
            if wins and not we:
                res['window_end'] = target
                res['window_start'] = res.get('window_start') or wins[0]['start']
            return res
        res['window_end'] = target
        if wins:
            res['window_start'] = wins[0]['start']
        for k in ('window_label', 'headline', 'cohort'):
            if res.get(k):
                res[k] = _swap_end_in(res[k], we, target)
        if isinstance(res.get('reads'), list):
            res['reads'] = [_swap_end_in(r, we, target) for r in res['reads']]
        for row in (res.get('metrics') or []):
            if isinstance(row, dict):
                for k in ('label', 'definition'):
                    if row.get(k):
                        row[k] = _swap_end_in(row[k], we, target)
        bd = res.get('breakdown')
        if isinstance(bd, dict):
            for k in ('title', 'label', 'basis', 'note'):
                if bd.get(k):
                    bd[k] = _swap_end_in(bd[k], we, target)
        return res
    except Exception:
        return res
