"""The output guard for Digital Twins IQ copy (Jenna 2026-11-05).

Jenna, verbatim: "for focus groups. don;t ever say this on the output:
Calibrated to NCIS: New York's first 2 episodes as aired. but if there is
published data about something (like it already aired) you can calibrate
to tht just don't source it. so if I put in an episode of an old show and
you can get neilsen on it grteat to calibrate to but just dont say it
does this make sense"

Rooms, pilots, reports, exports and notices calibrate to published
performance whenever it exists, and never say so. This module is the one
place that decides what a reader may see about it:

* ``strip_calibration_language(text)`` rewrites a sentence that names a
  source so the fact stays and the sourcing goes (``per Variety,`` and
  ``according to Nielsen,`` clauses come off, ``the published split``
  becomes ``the split``), then drops any sentence that still says the
  numbers were calibrated, anchored, benchmarked or matched to aired,
  published or outside figures, or that names a ratings service.
* ``strip_calibration_lines(lines)`` does the same to a list and drops
  the lines that end up empty.
* ``calibration_hits(text)`` lists what the guard would act on (the
  audit the tests run alongside the house banned-word audits).

The ``Compared to <norm>`` line on a pilot stays: that is the user's own
setting, not a source. The anchors themselves (the forecast's calibration
block and its recorded actual, the room's stored anchor, the ledger) stay
in the record for internal audit; this module governs rendered text only.
No em dashes.
"""
from __future__ import annotations

import re

# ratings and measurement services: a sentence that names one is a sourcing sentence ('The Numbers' is left to the
# room's own name swap; as a pattern it would read every 'the numbers' in plain prose as the tracker)
_SERVICES = (r"Nielsen|Comscore|Samba\s?TV|VideoAmp|iSpot(?:\.tv)?|Luminate|Parrot Analytics|Circana|NPD(?: Group)?|Box Office Mojo|"
             r"Sensor Tower|data\.ai|App Annie|Statista|eMarketer|Insider Intelligence|YouGov|Pew(?: Research(?: Center)?)?|MRI[- ]Simmons|Kantar|Antenna Analytics")
_PUBLICATIONS = (r"Variety|Deadline|The Hollywood Reporter|Hollywood Reporter|THR|TVLine|Vulture|Tudum|Media Play News|Home Media Magazine|"
                 r"the trade press|the trades|trade press|press reports|SEC filings?|earnings(?: call| report)?s?")
_ATTRIB = _SERVICES + r"|" + _PUBLICATIONS + r"|the network|the platform|the studio|the service"

# 1. rewrites: the fact stays, the sourcing goes
_REWRITES = (
    # 'according to Nielsen, week one drew 4.1M' -> 'week one drew 4.1M'
    (re.compile(r"\b(?:according to|per|as reported by|as measured by|as published by|as tracked by|as counted by|as quoted by|via)\s+(?:the\s+)?(?:" + _ATTRIB + r")(?:'s)?\s*(?:[,;:]\s*|\s+)", re.I), ''),
    # 'week one drew 4.1M, per Nielsen.' -> 'week one drew 4.1M.'
    (re.compile(r",?\s*\b(?:according to|per|as reported by|as measured by|as published by|as tracked by|via)\s+(?:the\s+)?(?:" + _ATTRIB + r")(?:'s)?(?:\s+(?:figures?|numbers?|count(?:s|ing)?|data|report(?:s|ing)?))?(?=[.!?;,]|$)", re.I), ''),
    # '(Nielsen)' / '(per Variety)' / '(source: the trades)' tags
    (re.compile(r"\s*\((?:source:\s*|per\s+|via\s+|according to\s+)?(?:the\s+)?(?:" + _SERVICES + r"|" + _PUBLICATIONS + r")[^)]{0,40}\)", re.I), ''),
    # 'the published split' -> 'the split'; 'latest published numbers' -> 'latest numbers'; 'as published' goes
    (re.compile(r"\b(?:as|when|once)\s+(?:first\s+)?published\b,?\s*", re.I), ''),
    (re.compile(r"\bpublicly\s+(?:reported|published|measured|counted)\s+", re.I), ''),
    (re.compile(r"\b(?:published|reported|measured|official|outside|external|third[- ]party)\s+(?=(?:figures?|numbers?|data|performance|audience|split|polls?|poll|count|counts|results?|ratings?|viewership|totals?|tallies|tally|vote)\b)", re.I), ''),
)

# 2. drops: a sentence that still carries any of these never reaches a reader
_DROP_RX = re.compile(
    r"\bcalibrat\w*"
    r"|\bbenchmark(?:ed|ing)\b|\bbenchmarks?\s+(?:to|against|on)\b"     # the verb; a seat's 'the Crunchinator benchmark' is ordinary English
    r"|\banchored\b"
    r"|\banchor(?:s|ing)?\s+(?:to|on|against)\b"
    r"|\bas\s+(?:it\s+|they\s+|originally\s+)?aired\b"
    r"|\baired\s+(?:episodes?|figures?|numbers?|performance|audience|totals?|counts?)\b"
    r"|\bepisodes?\s+as\s+aired\b"
    r"|\bactuals\b"
    r"|\bmatched\s+(?:to|against)\s+(?:the\s+)?(?:aired|published|reported|measured|outside|external)\b"
    r"|\b(?:" + _SERVICES + r")\b"
    r"|\bratings?\s+(?:service|services|data|book|books|figures?|numbers?|report|reports|bureau|provider)\b"
    r"|\bLive\s*\+\s*(?:same[- ]day|SD|\d)\w*"
    r"|\bsame[- ]day\s+(?:ratings?|numbers?|audience)\b"
    r"|\bovernights\b"
    r"|\bthe\s+ledger\b"
    r"|\bpublished\s+(?:figures?|numbers?|data|performance|ratings?|viewership|audience)\b",
    re.I,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'\u201c\u2018(\[A-Z0-9])")


def calibration_hits(text: str) -> list:
    """What the guard would drop in ``text``: the matched words, in order.
    Empty when the text is clean."""
    return [m.group(0) for m in _DROP_RX.finditer(str(text or ''))]


def _rewrite(sentence: str) -> str:
    s = sentence
    for rx, rep in _REWRITES:
        s = rx.sub(rep, s)
    s = re.sub(r"\s{2,}", ' ', s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    s = re.sub(r"\(\s*\)", '', s)
    s = s.strip()
    if s and s[0].islower() and sentence[:1].isupper():
        s = s[0].upper() + s[1:]
    return s


def _strip_line(line: str) -> str:
    raw = str(line or '')
    if not raw.strip():
        return raw
    lead = raw[:len(raw) - len(raw.lstrip())]
    body = raw.strip()
    kept = []
    for sent in _SENTENCE_SPLIT.split(body):
        sent = _rewrite(sent)
        if not sent or _DROP_RX.search(sent):
            continue
        kept.append(sent)
    return lead + ' '.join(kept)


def strip_calibration_language(text: str) -> str:
    """The guard. Newlines are kept (a notice body is many lines); each
    line is read sentence by sentence; a sentence that names a source is
    rewritten without it; a sentence that still says calibrated, anchored,
    benchmarked, as aired, or names a ratings service is dropped. A text
    that was nothing but such sentences comes back empty."""
    t = str(text or '')
    if not t:
        return t
    if not _DROP_RX.search(t) and not any(rx.search(t) for rx, _ in _REWRITES):
        return t
    return '\n'.join(_strip_line(ln) for ln in t.split('\n'))


def strip_calibration_lines(lines) -> list:
    """The guard over a list of strings; lines that end up empty go."""
    out = []
    for x in (lines or []):
        if x is None:
            continue
        s = strip_calibration_language(str(x))
        if s.strip():
            out.append(s)
    return out


def strip_calibration_in(obj):
    """The guard over every string in a nested dict or list (a copy; the
    stored record is never changed by rendering)."""
    if isinstance(obj, str):
        return strip_calibration_language(obj)
    if isinstance(obj, list):
        out = []
        for x in obj:
            y = strip_calibration_in(x)
            if isinstance(x, str) and not str(y).strip():
                continue
            out.append(y)
        return out
    if isinstance(obj, dict):
        return {k: strip_calibration_in(v) for k, v in obj.items()}
    return obj


__all__ = ['strip_calibration_language', 'strip_calibration_lines', 'strip_calibration_in', 'calibration_hits']
