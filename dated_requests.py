"""Dashboard requests that carry a date: the server refusal behind the page.

Jenna, 2026-11-19 (verbatim): "Yes, add the same refusal behind all
four". The page already holds every date field to the data floor and
says so. Four places had no refusal on the server behind that, so a
request sent another way would have been run:

  1. the Attribution IQ campaign form (launch date, end date, phases)
  2. the Microdramas custom range
  3. the Attribution IQ as-of date and the asset From / To dates
  4. a Rankers end date before the board's first day (that one lives
     in trends_iq.first_day_refusal; a board has its own later limit)

This module is the caller for 1 to 3. It adds no rule of its own about
the floor: the date, the sentence and the judgement all come from the
one shared place (migration.event_window through prometheus.data_floor,
which this module only reads).

What a refused request is told, the same way the other dashboard forms
are told:

  a start before the floor   the standing sentence, then "Choose a
                             start date on or after <the floor>."
  an end before the floor    ... "Choose an end date on or after ..."
  a single date before it    ... "Choose a date on or after ..."

A range that starts before the floor and ends on or after it is refused
too, with the start-date line. That is the standing rule for a
dashboard form: the person picks a start on or after the floor and
sends it again, and nothing is moved for them without a word.

A value that is not a date is not judged against the floor (the shared
rule: nothing to judge). Where the date goes straight into a data
query (`strict=True`), a value that is not a whole YYYY-MM-DD date is
refused instead of passed on.

Nothing here charges, queues or stores anything. Every function is
fail-safe: a fault lets the request through exactly as before.
"""
from __future__ import annotations

import re
from datetime import datetime

__all__ = ['refusal', 'UNREADABLE', 'is_floor_refusal', 'floor_iso']

UNREADABLE = 'That date could not be read. Choose a date from the calendar.'

_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _floor():
    from prometheus import data_floor as dfl
    return dfl


def _text(value) -> str:
    if value is None or isinstance(value, bool):
        return ''
    return str(value).strip()


def _whole_date(text: str) -> bool:
    """A whole YYYY-MM-DD calendar day and nothing else."""
    if not _ISO.match(text):
        return False
    try:
        datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        return False
    return True


def _before_floor(dfl, text: str) -> bool:
    """The shared judgement, asked about one day: a window that starts
    and ends on it is 'before' only when the day is before the floor.
    A value that is not a date is never before it."""
    return bool(text) and bool(dfl.window_error(text, text))


def refusal(start=None, end=None, on=None, strict=False) -> str:
    """The line a dated request is refused with, or '' when it may run.

    start / end   the two ends of a range (either may be missing)
    on            a single date (an as-of date)
    strict        the value goes straight into a data query: anything
                  that is not a whole YYYY-MM-DD date is refused

    Never raises; '' on any fault."""
    try:
        dfl = _floor()
        s, e, o = _text(start), _text(end), _text(on)
        if strict:
            for v in (s, e, o):
                if v and not _whole_date(v):
                    return UNREADABLE
        if s and _before_floor(dfl, s):
            return dfl.form_line()
        if e and _before_floor(dfl, e):
            return f'{dfl.SENTENCE} Choose an end date on or after {dfl.FLOOR_LABEL}.'
        if o and _before_floor(dfl, o):
            return f'{dfl.SENTENCE} Choose a date on or after {dfl.FLOOR_LABEL}.'
    except Exception:
        return ''
    return ''


def is_floor_refusal(line: str) -> bool:
    """True when the line is one of the floor lines (not the
    could-not-be-read line)."""
    try:
        return bool(line) and str(line).startswith(_floor().SENTENCE)
    except Exception:
        return False


def floor_iso() -> str:
    """The floor as the shared constant writes it, '' on any fault."""
    try:
        return str(_floor().FLOOR or '')
    except Exception:
        return ''
