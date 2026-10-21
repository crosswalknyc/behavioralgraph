"""Numbers the user stated: one extractor for every lane that matches a
typed number to a figure (2026-10-21, Bria Dotson at Crosswalk).

Bria asked "How many viewers who watched Law & Order: SVU on Peacock from
September 2025 to March 2026 also watched the series on Hulu?" with the
Peacock profile open. The number-explainer lane took the 2025 out of
"September 2025" as a figure, found a 2,025-person row on a Peacock
journey and explained that row. The day before, the evidence binder had
been taught that a year inside a date is never a count (Tania Nasr's BET
read); the lesson lived in one module. Jenna: "fix so this missread
doesnt happen again".

This module is the one place that decides what a typed number is:

  is_date_token(text, start, end)   the token is part of a date or a
                                     season / episode marker, never a
                                     stated number
  stated(text, ...)                 every stated number with its unit
  counts(text, min_value, lane)     the plausible count figures for a lane
  is_figure_question(text)          the ask is about a figure on screen
                                     (why / what is behind / how is this
                                     calculated / what makes up)
  is_measurement_ask(text)          the ask names a subject, a platform
                                     or a pair of them and a window, or
                                     asks how many of one audience did
                                     something: a read, never an
                                     explainer

Used by the drill-down lane (prometheus.drilldown), the evidence binder
(prometheus.smarter), the figure table (prometheus.figures), the thread
number bank (prometheus.kpi_definitions, prometheus.shared_memory) and
anything else that reads a number out of a person's words. Pure
functions; no Flask, no network.
"""
from __future__ import annotations

import re

__all__ = ['is_date_token', 'is_year_token', 'stated', 'counts', 'plausible_figure',
           'is_figure_question', 'is_measurement_ask', 'MONTHS_RX']

_MONTHS = (r"jan(?:uary|\.)?|feb(?:ruary|\.)?|mar(?:ch|\.)?|apr(?:il|\.)?|may|jun(?:e|\.)?|jul(?:y|\.)?|"
           r"aug(?:ust|\.)?|sep(?:t(?:ember)?|\.)?|oct(?:ober|\.)?|nov(?:ember|\.)?|dec(?:ember|\.)?")
MONTHS_RX = re.compile(r"\b(?:" + _MONTHS + r")\b", re.I)
_MONTH_BEFORE_RX = re.compile(r"(?:" + _MONTHS + r")\s*,?\s*(?:of\s+)?(?:\d{1,2}(?:st|nd|rd|th)?\s*,?\s*)?$", re.I)
_MONTH_AFTER_RX = re.compile(r"^\s*,?\s*(?:of\s+)?(?:" + _MONTHS + r")\b", re.I)
_DATE_WORD_BEFORE_RX = re.compile(
    r"\b(?:in|since|from|to|through|thru|until|till|by|between|and|of|during|before|after|for|year|fy|q[1-4]|"
    r"calendar|fiscal|spring|summer|fall|autumn|winter|early|late|mid)\s*-?\s*$", re.I)
_RANGE_AFTER_RX = re.compile(r"^\s*(?:-|to|through|thru|until|till|and|/)\s*(?:19|20)?\d{2}\b", re.I)
_SEASON_EP_BEFORE_RX = re.compile(r"\b(?:s|se|season|ep|e|episode|ch|chapter|part|vol(?:ume)?|wk|week|day|q)\s*\.?\s*$", re.I)
_SEASON_EP_AFTER_RX = re.compile(r"^\s*(?:e|ep|episode|x)\s*\d{1,3}\b", re.I)
_COUNT_UNIT_AFTER_RX = re.compile(
    r"^\s*(?:people|persons|viewers|users|accounts|subscribers|subs|readers|listeners|players|buyers|shoppers|"
    r"households|individuals|panelists|customers|visitors|fans|members|signups|sign-ups|transactions|rentals|"
    r"sales|views|plays|streams|hours|minutes|sessions|visits|clicks|posts|assets|creators|titles|episodes)\b", re.I)
_TOKEN_RX = re.compile(
    r"(?<![\w.,/$-])(\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$?\d+(?:\.\d+)?)"
    r"(\s*(?:%|percent|pct|[KMB]\b|million|billion|thousand))?(?![\w,]|\.\d)", re.I)
_YEAR_RX = re.compile(r"^(?:19|20)\d{2}$")
_MD_PAIR_RX = re.compile(r"^\d{1,2}$")


def is_year_token(raw):
    """A bare 1900 to 2099 number with no comma and no decimal."""
    return bool(_YEAR_RX.match(str(raw or '').strip()))


def is_date_token(text, start, end):
    """True when text[start:end] is part of a date or a season / episode
    marker. Covers: a year next to a month name (September 2025, Sep. 2025,
    2025 September, March of 2026), a year inside m/d/yyyy, yyyy-mm-dd,
    yyyy/mm/dd or mm/yyyy, a year range (2025-2026, 2025/26, 2025 to 2026),
    a year after a date word (in 2025, since 2024, from 2025 to 2026, FY
    2026, Q3 2026), a day of month next to a month (September 23), a
    month/day pair (9/16, 9/16 - 9/30), and S2 E1 / S02E01 / season 2 /
    episode 1. A comma-formatted number (2,025) is never a date."""
    text = str(text or '')
    tok = text[start:end]
    bare = tok.replace('$', '').strip()
    if ',' in bare or '.' in bare:
        return False
    before = text[max(0, start - 24):start]
    after = text[end:end + 24]
    if _SEASON_EP_BEFORE_RX.search(before) and len(bare) <= 3:
        return True
    if _SEASON_EP_AFTER_RX.match(after) and len(bare) <= 3:
        return True
    if re.match(r"^\s*/\s*\d{1,2}(?![\d,])(?:\s*/\s*\d{2,4})?", after) and len(bare) <= 2:
        return True   # 9/16, 9/16/2026
    if re.search(r"\d{1,2}\s*/\s*$", before) and len(bare) <= 4:
        return True   # the day or year after a slash
    if _MONTH_BEFORE_RX.search(before) and len(bare) <= 4:
        return True   # September 2025, September 23, Sep 23, 2025
    if _MONTH_AFTER_RX.match(after) and len(bare) <= 4:
        return True   # 2025 September, 23 September
    if not is_year_token(bare):
        return False
    # a bare year
    if re.match(r"^\s*-\s*\d{1,2}\b", after) or re.search(r"\d{1,2}\s*-\s*$", before):
        return True   # 2026-09-23 / 23-09-2026
    if _RANGE_AFTER_RX.match(after):
        return True   # 2025-2026, 2025 to 2026, 2025/26
    if _DATE_WORD_BEFORE_RX.search(before):
        return True   # in 2025, from 2025, FY 2026, Q3 2026, between 2024 and 2025
    if _COUNT_UNIT_AFTER_RX.match(after):
        return False  # "2025 viewers" is a count
    return True       # a bare 1900 to 2099 number on its own reads as a year


def stated(text, *, min_value=0):
    """Every number the words state, in order: [{'value', 'raw', 'unit',
    'start', 'end'}]. Dates, season / episode markers and bare years are
    left out. unit is '' for a plain count, '%' for a percent, 'K' / 'M' /
    'B' for a scaled figure, '$' for money."""
    out = []
    text = str(text or '')
    for m in _TOKEN_RX.finditer(text):
        raw, suffix = m.group(1), (m.group(2) or '').strip().lower()
        if is_date_token(text, m.start(1), m.end(1)):
            continue
        unit = ''
        if raw.startswith('$'):
            unit = '$'
        if suffix in ('%', 'percent', 'pct'):
            unit = '%'
        elif suffix in ('k', 'thousand'):
            unit = 'K'
        elif suffix in ('m', 'million'):
            unit = 'M'
        elif suffix in ('b', 'billion'):
            unit = 'B'
        try:
            value = float(raw.replace('$', '').replace(',', ''))
        except ValueError:
            continue
        scaled = value * {'K': 1e3, 'M': 1e6, 'B': 1e9}.get(unit, 1)
        if unit in ('', '$', 'K', 'M', 'B') and scaled < min_value:
            continue
        out.append({'value': int(round(scaled)) if unit != '%' else value, 'raw': m.group(0).strip(),
                    'unit': unit, 'start': m.start(1), 'end': m.end(1),
                    'counted': bool(_COUNT_UNIT_AFTER_RX.match(text[m.end():m.end() + 24]))})
    return out


def plausible_figure(item, lane='generic'):
    """Could this stated number be a figure the lane holds? A percent or a
    dollar amount is never a count row; the drill-down lane wants a count
    of at least 100 written as a count (a comma, or more than four digits,
    or a unit word), never a bare year."""
    if not isinstance(item, dict) or item.get('unit') in ('%', '$'):
        return False
    try:
        v = int(item.get('value') or 0)
    except (TypeError, ValueError):
        return False
    raw = str(item.get('raw') or '')
    if lane == 'drilldown':
        if v < 100:
            return False
        bare = re.sub(r"\s*(?:k|m|b|million|billion|thousand)\b", '', raw, flags=re.I).strip()
        if is_year_token(bare) and not item.get('counted'):
            return False   # a bare year never triggers the explainer; "2025 viewers" is a count
        return True
    return v >= 1


def counts(text, min_value=100, lane='generic'):
    """The plausible count figures the words state, as ints, deduped, in
    order."""
    out = []
    for item in stated(text, min_value=min_value):
        if plausible_figure(item, lane) and item['value'] not in out:
            out.append(int(item['value']))
    return out


# ------------------------------------------------------------ the asks
_FIGURE_CUE_RX = re.compile(
    r"\bwhy (?:is|are|does|do|did|was|were)\b|"
    r"\bwhat(?:'s| is| are| was| were)? (?:behind|in|inside|included in|driving|making up|under)\b|"
    r"\bwhat (?:makes|made|make) up\b|\bwhat(?:'s| is) (?:the|this|that) (?:number|figure|count|row|total)\b|"
    r"\bhow (?:is|was|are|were) (?:this|that|it|the)\b[^?]{0,40}\b(?:calculated|computed|counted|derived|measured|arrived)\b|"
    r"\bbreak ?down\b|\bbreakdown\b|\bsplit (?:of|out|up)\b|\bcomposition\b|\bconsists? of\b|\bcomprise[sd]?\b|"
    r"\bwhich [^?]{0,40}\b(?:are|is|were|was) (?:behind|in|included|inside)\b|"
    r"\b(?:what|which) [^?]{0,60}\b(?:included|include|behind)\b|\bexplain (?:the|this|that)\b|"
    r"\b(?:the|this|that) (?:row|stage|detour|section|number|figure|count|table|chart)\b|\b(?:on|in) (?:this|that|the) (?:journey|table|chart|page|view)\b|"
    r"\bwhat(?:'s| is| are| was| were) (?:the |this |that )?\$?\d", re.I)
_POINTER_RX = re.compile(r"\b(?:this|that|the) (?:number|figure|count|row|total|stage|detour|section|table|chart|line|bar)\b", re.I)
_CONSUMPTION_RX = re.compile(
    r"\b(?:watch(?:ed|es|ing)?|view(?:ed|s|ing)?|stream(?:ed|s|ing)?|saw|seen|listen(?:ed|s|ing)?(?: to)?|read|"
    r"play(?:ed|s|ing)?|bought|buy|purchas(?:ed|es|ing)|subscrib(?:ed|es|ing)|visit(?:ed|s|ing)?|used|use|using|"
    r"attend(?:ed|s|ing)?|rent(?:ed|s|ing)?|shopp?(?:ed|ing|s)?|search(?:ed|es|ing)?|follow(?:ed|s|ing)?)\b", re.I)
_AUDIENCE_NOUN_RX = re.compile(
    r"\b(?:viewers|audience|audiences|users|fans|readers|listeners|buyers|subscribers|shoppers|visitors|players|"
    r"customers|members|households|people who|those who|anyone who)\b", re.I)
_HOW_MANY_RX = re.compile(
    r"\b(?:how many|how much of|what (?:share|percent(?:age)?|fraction|portion)|share of|percent(?:age)? of|"
    r"number of|count of|size of|how big)\b", re.I)
_WINDOW_RX = re.compile(
    r"\b(?:from|between|since|in|during|over|for|through)\s+(?:" + _MONTHS + r")\.?\s*,?\s*(?:\d{1,2}\s*,?\s*)?(?:19|20)\d{2}\b|"
    r"\b(?:" + _MONTHS + r")\.?\s*,?\s*(?:19|20)\d{2}\s*(?:-|to|through|thru|until|and)\s*(?:" + _MONTHS + r")\b|"
    r"\b(?:last|past|trailing|previous|next)\s+(?:\d+|twelve|six|three|two)\s+(?:months?|weeks?|days?|years?)\b|"
    r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*(?:-|to|through)\s*\d{1,2}/\d{1,2}|"
    r"\bq[1-4]\s*(?:19|20)\d{2}\b|\b(?:calendar|fiscal)\s+(?:year\s+)?(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\s+(?:to|through|-)\s+(?:19|20)\d{2}\b|"
    r"\bbetween\s+(?:19|20)\d{2}\s+and\s+(?:19|20)\d{2}\b|\bin\s+(?:19|20)\d{2}\b|"
    r"\b(?:the )?(?:30|60|90|7|14|28) days\b", re.I)
_ON_PLATFORM_RX = re.compile(
    r"\b(?:on|via|through|across)\s+(?:the\s+)?(?:[A-Z][\w+&'.:-]*(?:\s+[A-Z][\w+&'.:-]*){0,3}|netflix|hulu|peacock|max|hbo|disney\+?|"
    r"prime video|amazon|apple tv\+?|paramount\+?|youtube|tiktok|spotify|roku|tubi|pluto|starz|showtime|amc\+?)\b")
_ALSO_RX = re.compile(r"\b(?:also|too|as well|both|either|neither|or)\b", re.I)


def is_figure_question(text):
    """The ask is about a figure on screen: a why / what is behind / how is
    this calculated / what makes up question that carries a count written
    as a count, or points at a row. Never true for a bare year or a date."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 600:
        return False
    if not (counts(t, lane='drilldown') or _POINTER_RX.search(t)):
        return False
    return bool(_FIGURE_CUE_RX.search(t))


def is_measurement_ask(text):
    """The ask wants a count or a share of an audience doing something (who
    watched X on A, also watched on B, from Month Year to Month Year): a
    read, never a number explainer. True when it asks how many / what
    share of people did something, or names a consumption verb with a
    platform or a window, or names two platforms with 'also' / 'both'."""
    t = ' '.join(str(text or '').split())
    if not t:
        return False
    if _HOW_MANY_RX.search(t) and (_CONSUMPTION_RX.search(t) or _AUDIENCE_NOUN_RX.search(t)):
        return True
    if (_CONSUMPTION_RX.search(t) or _AUDIENCE_NOUN_RX.search(t)) and (_WINDOW_RX.search(t) or len(_ON_PLATFORM_RX.findall(t)) >= 2):
        return True
    if _ALSO_RX.search(t) and len(_ON_PLATFORM_RX.findall(t)) >= 2 and _CONSUMPTION_RX.search(t):
        return True
    return False
