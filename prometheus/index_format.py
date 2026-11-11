"""How an index prints, everywhere a reader sees one (2026-11-11).

An index of four digits or more carries a thousands separator: 5,548,
never 5548. A regenerated deck card read "Costa Sunglasses reaches 6.5%
at index 5548"; the same bare figure reached the Profiles page category
line and chat reads. Two entry points, used by every surface:

  fmt_index(v)              a number (or a numeric string) as the index
                            a reader sees: '119', '5,548'. '' when v is
                            not a number.
  separate_index_figures(t) text a model or a builder already wrote:
                            'index 5548' becomes 'index 5,548'. Only a
                            bare whole number of four digits or more
                            right after the word index (or idx) is
                            touched; a year, a percent, a decimal and a
                            link are left alone.

The dashboard's own formatter (formatIndex in templates/index.html)
follows the same rule.
"""
from __future__ import annotations

import re

__all__ = ['fmt_index', 'separate_index_figures']


def fmt_index(v) -> str:
    """'5,548' for 5548 / '5548' / 5548.4; '' when v is not a number."""
    if v is None or isinstance(v, bool):
        return ''
    try:
        n = float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return ''
    if n != n or n in (float('inf'), float('-inf')):
        return ''
    return f"{int(round(n)):,}"


# the word, an optional short connective, then a bare whole number of four
# digits or more. Not inside a link or a key (index=5548, /index/5548), not
# a percent, a decimal, a range of years or a figure already separated.
_INDEX_FIG_RX = re.compile(
    r"(?<![\w/?&=.#-])(?P<lead>(?:over[- ]?|under[- ]?)?(?:index(?:es|ed|ing)?|idx)"
    r"(?:\s+(?:of|at|is|was|to|near|around|about|above|over|under|reaches|reached|hits|hit|sits\s+at|stands\s+at))?"
    r"(?:\s*:\s+|\s+))(?P<num>\d{4,7})(?!\d|,\d|\.\d|%|\s*%|-\d|/\d|\s+(?:to|through|and)\s+(?:19|20)\d{2}\b)", re.I)


def separate_index_figures(text):
    """'index 5548' -> 'index 5,548' in text a reader is about to see.
    Returns the input unchanged when there is nothing to do."""
    if not isinstance(text, str) or len(text) < 7:
        return text
    low = text.lower()
    if 'index' not in low and 'idx' not in low:
        return text
    return _INDEX_FIG_RX.sub(lambda m: m.group('lead') + f"{int(m.group('num')):,}", text)
