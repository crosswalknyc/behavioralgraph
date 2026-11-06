"""A window label is printed whole or not at all (2026-11-05).

A Subscriber IQ overlap read shipped its counting block twice with the
line ``Window: Nov 5 2025 to Nov 5 2026 (S.W.A.T. Exiles Season 1 to
date, Sep 25 to Oct 29 202.`` The model's label ran 82 characters; the
coherence pass clipped it to a raw ``[:80]`` and the block appended its
full stop, so the year lost a digit and the parenthesis never closed.
The same raw slice sat on the ledger entry (``wl``) and the Reader's
window phrase.

The rule for every window or date fragment a reader sees:

* it fits whole up to WINDOW_LABEL_MAX, a bound no real label reaches;
* when it is longer, the trailing parenthetical (a sub-window, an
  airing note) comes off whole, never from the middle;
* a cut never lands inside a token, inside a parenthesis, or on a year
  with fewer than four digits;
* a line that still reads cut (an older record, another writer) is
  repaired from the window dates the read carries, or the dangling
  parenthetical is dropped, before it prints.

Pure functions, no imports beyond ``re``. prometheus_analysis (the
coherence passes), insights_ledger, prometheus.reader,
prometheus.methodology (the counting block) and prometheus_verify (the
cell completion pass) call in. Regression:
scripts/test_pm_window_label_whole.py.
"""
from __future__ import annotations

import re

__all__ = ['WINDOW_LABEL_MAX', 'clip_window_label', 'cut_mid_token', 'window_line_fragments',
           'repair_window_label', 'repair_window_lines']

# Generous: the longest honest label (a full window plus a parenthetical
# sub-window with a title in it) runs under 120. The old bound was 80.
WINDOW_LABEL_MAX = 200

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# a day-month pair followed by a year with fewer than four digits at the
# very end: "Oct 29 202", "Oct 29, 2", "29 Oct 20"
_CUT_YEAR_RX = re.compile(
    r"(?:" + _MONTH + r"\s+\d{1,2},?|\d{1,2}\s+" + _MONTH + r")\s+(?:19|20)?\d{1,3}$", re.I)
# a full four-digit year closing the fragment (so "Oct 29 2026" is whole)
_FULL_YEAR_RX = re.compile(r"(?:19|20)\d{2}$")
# a label ending on a connective or an open punctuation mark is not whole
_DANGLING_RX = re.compile(r"(?:\b(?:to|through|thru|and|or|from|the|a|an|of|in|on)|[,;:(\-])$", re.I)
_WINDOW_LINE_RX = re.compile(r"(?m)^(?P<lead>\s*-?\s*Window:\s*)(?P<w>[^\n]*?)(?P<dot>\.?)\s*$")
_ISO_RX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _s(text):
    return ' '.join(str(text or '').split())


def cut_mid_token(label):
    """True when a window or date fragment reads cut: an open
    parenthesis with no close, a year with fewer than four digits where
    a year belongs, or a trailing connective / punctuation."""
    t = _s(label).rstrip('.')
    if not t:
        return False
    if t.count('(') != t.count(')'):
        return True
    if _CUT_YEAR_RX.search(t) and not _FULL_YEAR_RX.search(t):
        return True
    if _DANGLING_RX.search(t):
        return True
    return False


def _drop_dangling_paren(t):
    """The text before an unclosed '(' , trimmed."""
    if t.count('(') > t.count(')'):
        head = t[:t.rfind('(')]
        return head.rstrip(' ,;:-')
    return t


def clip_window_label(label, limit=WINDOW_LABEL_MAX):
    """The label whole when it fits; otherwise the longest honest head:
    the trailing parenthetical dropped whole first, then a cut at a
    word boundary that never leaves an open parenthesis or a short
    year. Never ends mid-token."""
    t = _s(label)
    if not t:
        return ''
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = WINDOW_LABEL_MAX
    if limit <= 0 or len(t) <= limit:
        return t
    # a sub-window rides in a trailing parenthetical: it comes off whole
    m = re.search(r"\s*\([^()]*\)\s*$", t)
    if m and m.start() > 0:
        head = t[:m.start()].rstrip(' ,;:-')
        if head and len(head) <= limit and not cut_mid_token(head):
            return head
        t = head or t
        if len(t) <= limit:
            return t
    cut = t[:limit]
    cut = _drop_dangling_paren(cut)
    sp = cut.rfind(' ')
    if sp > 0 and len(cut) == limit:
        cut = cut[:sp]
    cut = cut.rstrip(' ,;:-(')
    # a cut that stops on a short year or a connective backs up a token
    guard = 0
    while cut and cut_mid_token(cut) and guard < 12:
        cut = _drop_dangling_paren(cut)
        sp = cut.rfind(' ')
        cut = (cut[:sp] if sp > 0 else '').rstrip(' ,;:-(')
        guard += 1
    return cut


def _fmt_date(v):
    s = _s(v)
    if not s:
        return ''
    if _ISO_RX.match(s):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(s[:10], '%Y-%m-%d').strftime('%b %-d %Y')
        except Exception:
            return s[:10]
    return s


def repair_window_label(label, window_start=None, window_end=None, full_label=''):
    """A label that reads cut, made whole: the full label when one is
    known and is itself whole; else the window dates the read carries;
    else the dangling parenthetical dropped. A whole label passes
    through unchanged."""
    t = _s(label).rstrip('.')
    if not t or not cut_mid_token(t):
        return t
    full = _s(full_label).rstrip('.')
    if full and not cut_mid_token(full) and (not t or full.lower().startswith(t[: max(8, len(t) // 2)].lower())):
        return full
    head = _drop_dangling_paren(t)
    if head != t and head and not cut_mid_token(head):
        return head
    ws, we = _fmt_date(window_start), _fmt_date(window_end)
    if ws and we:
        return f"{ws} to {we}"
    # back off token by token until the fragment reads whole
    guard = 0
    while head and cut_mid_token(head) and guard < 12:
        sp = head.rfind(' ')
        head = (head[:sp] if sp > 0 else '').rstrip(' ,;:-(')
        guard += 1
    return head or t


def window_line_fragments(reply):
    """The ``Window:`` lines in a reply that read cut."""
    out = []
    for m in _WINDOW_LINE_RX.finditer(str(reply or '')):
        w = m.group('w').strip()
        if w and cut_mid_token(w):
            out.append(w)
    return out


def repair_window_lines(reply, res=None):
    """Every ``Window:`` line in a reply printed whole: a cut line is
    rebuilt from the read's own window (``res``: window_label,
    window_start, window_end) or trimmed to its last whole token. The
    reply is returned unchanged when every line is already whole."""
    text = str(reply or '')
    if not text or 'Window:' not in text:
        return reply
    res = res if isinstance(res, dict) else {}
    full = _s(res.get('window_label') or '')
    ws, we = res.get('window_start'), res.get('window_end')

    def _fix(m):
        w = m.group('w').strip()
        if not w or not cut_mid_token(w):
            return m.group(0)
        fixed = repair_window_label(w, ws, we, full_label=full)
        if not fixed:
            return m.group(0)
        return f"{m.group('lead')}{fixed}{m.group('dot')}"

    try:
        return _WINDOW_LINE_RX.sub(_fix, text)
    except Exception:
        return reply
