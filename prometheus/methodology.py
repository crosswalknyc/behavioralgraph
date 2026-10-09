"""Methodology line under every viewership figure (2026-10-08, Liz's
notes on the Paramount+ browse-only read, entered by Jenna).

Liz, verbatim (output rules): "always print window dates, panel base,
unit (persons vs accounts), view threshold, carriage paths included,
and session definition in a methodology line under any viewership
figure, without being asked."

The house definitions live here once. The block is deterministic and
appended at the reply formatter, so a viewership read never ships
without it, whatever the model wrote. Vocabulary follows the standing
rules: no vendor names (the citation scrub would drop the sentence),
"panel" never appears (the scrub rewrites it), counts are persons.
"""
from __future__ import annotations

import re
from datetime import date, datetime

HEADER = 'How these are counted:'

# What a viewership figure looks like in a reply or an ask.
VIEWERSHIP_RX = re.compile(
    r"\b(?:viewers?|viewed|viewing|viewership|watch(?:ed|es|ing)?|"
    r"watch time|hours watched|minutes watched|streams?|streamed|streaming|"
    r"sessions?|browse[- ]only|browsed|playback|title starts?|started a title|"
    r"plays|played|listeners?|listened|listenership|binge[ds]?|tuned in|"
    r"opened the (?:app|service)|dwell)\b", re.I)
# Figures that depend on a title-start threshold: both thresholds show.
THRESHOLD_RX = re.compile(
    r"\b(?:browse[- ]only|bounce[ds]?|without starting|did not start|"
    r"no playback|never (?:started|pressed play)|title starts?|started a "
    r"title|first play|starts?)\b", re.I)

UNIT = ("Counts are US viewers (persons), any screen, credited at one "
        "second of viewing; they are not comparable to a service's "
        "reported subscriber count, to TV ratings, or to platform view "
        "counts that tally plays instead of people.")
THRESHOLD = ("A title start counts at one second of playback; autoplayed "
             "previews and trailers on the home screen do not count as "
             "starts. Where a figure depends on that threshold, the 30 "
             "seconds or more view is shown beside it.")
SESSION = ("A session closes after 30 minutes without activity; a "
           "backgrounded app is not an open.")
CARRIAGE = ("Carriage paths: direct app and site sessions plus channel "
            "storefronts (Prime Video Channels, Apple TV Channels, Roku) "
            "where the title is attributable.")
BASE_SAMPLE = "measured across 10M US consumers and projected to the US"

# What we count as a view, for every generated artifact that carries one
# (Jenna 2026-10-08: "anytime something is generated that includes views
# we give what we measure as a view from our methodology somewhere
# attached so they know it's not apples to apples to other metrics").
VIEW_HEADER = 'What we count as a view:'
VIEW_DEFINITION = (
    "A view is one person, on any screen, who played a title for at least one "
    "second inside the window, counted once however many devices or sessions "
    "they used. Autoplayed previews and trailers are not views. Counts are US "
    "viewers (persons), not accounts, so they are not comparable "
    "to a service's reported subscriber count, to TV ratings, or to platform "
    "view counts that tally plays instead of people.")
VIEW_NOTE_SHORT = ("Views here are US viewers (persons) credited at one second of "
                   "playback, counted once per person; not comparable to subscriber "
                   "counts, TV ratings, or play counts.")


def mentions_views(text):
    return bool(VIEWERSHIP_RX.search(str(text or '')))


def carries_definition(text):
    t = str(text or '')
    return VIEW_HEADER in t or 'Counts are US viewers (persons)' in t or VIEW_NOTE_SHORT in t


def attach_view_definition(text, short=False):
    """Append the view definition to a text artifact that mentions views
    and does not already carry it. Idempotent."""
    t = str(text or '')
    if not t.strip() or not mentions_views(t) or carries_definition(t):
        return text
    body = t.rstrip()
    if short:
        return body + '\n\n' + VIEW_NOTE_SHORT
    return body + '\n\n' + VIEW_HEADER + ' ' + VIEW_DEFINITION


MODEL_RULES = (
    'VIEWERSHIP FIGURES (house rules, always):\n'
    '- Count US viewers (persons), never accounts or households. Say '
    '"viewers" or "people".\n'
    '- Every viewership figure carries its window dates.\n'
    '- When a figure depends on a title-start threshold (browse-only, '
    'bounce, started a title, first play), report BOTH the one-second '
    'figure and the 30-seconds-or-more figure, side by side.\n'
    '- Name the carriage paths the figure includes when they differ '
    '(direct app and site; channel storefronts such as Prime Video '
    'Channels). Never claim a path the measurement cannot see.\n'
    '- The server appends the standard counting block (window, base, '
    'unit, threshold, carriage, session rules); do not restate it, and '
    'never name a ratings vendor.\n')


def _fmt_date(v):
    if v is None or v == '':
        return ''
    if isinstance(v, (date, datetime)):
        return v.strftime('%B %-d, %Y')
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).strftime('%B %-d, %Y')
        except ValueError:
            continue
    return s


def is_viewership_read(text='', res=None, question=''):
    """True when the reply or the ask carries a viewership figure."""
    blob = ' '.join([str(text or ''), str(question or '')])
    res = res if isinstance(res, dict) else {}
    for m in (res.get('metrics') or []):
        if isinstance(m, dict):
            blob += ' ' + str(m.get('label') or '') + ' ' + str(m.get('name') or '')
    fam = str(res.get('metric_family') or '').lower()
    if fam in ('viewership', 'consumption', 'journey', 'streaming'):
        return True
    return bool(VIEWERSHIP_RX.search(blob))


def block(window_start=None, window_end=None, window_label='',
          base_label='', base_people=None, text=''):
    """The counting block as lines (header first)."""
    win = str(window_label or '').strip()
    if not win and window_start and window_end:
        win = f"{_fmt_date(window_start)} to {_fmt_date(window_end)}"
    lines = [HEADER]
    if win:
        lines.append(f"- Window: {win}.")
    base = str(base_label or '').strip()
    people = None
    try:
        people = int(float(base_people)) if base_people not in (None, '') else None
    except (TypeError, ValueError):
        people = None
    if base:
        if people:
            lines.append(f"- Base: the {base} audience ({people:,} US viewers), "
                         f"{BASE_SAMPLE}.")
        else:
            lines.append(f"- Base: the {base} audience, {BASE_SAMPLE}.")
    else:
        lines.append(f"- Base: {BASE_SAMPLE[0].upper() + BASE_SAMPLE[1:]}.")
    lines.append(f"- {VIEW_HEADER} {VIEW_DEFINITION}")
    if THRESHOLD_RX.search(str(text or '')):
        lines.append(f"- Threshold: {THRESHOLD}")
    lines.append(f"- {CARRIAGE}")
    lines.append(f"- Sessions: {SESSION}")
    return lines


# Period totals next to their quarters (2026-10-09, item 6; Bria's
# Starz ticket: Tobias Menzies at 18.42% for the year with every quarter
# under 5% was a correct number that looked wrong). One plain line
# wherever a full-window figure sits next to sub-period figures.
PERIOD_HEADER = 'Why the full-window number sits above each period:'
PERIOD_NOTE = ("A person is counted once for the whole window but only in the periods they "
               "were active, so each quarter or month can only equal or fall below the "
               "full-window share, and the periods do not add up to it. A brand whose "
               "audience spreads across the year reads higher for the year than for any one "
               "quarter.")
PERIOD_RX = re.compile(r"\b(?:q[1-4]\b|quarter(?:s|ly)?|month(?:s|ly)?\b|month by month|by quarter|"
                       r"per quarter|each quarter|h[12]\s*20\d\d|ytd|year to date|"
                       r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+20\d\d)", re.I)
WHOLE_RX = re.compile(r"\b(?:total universe|full[- ]window|full year|whole year|the year\b|annual|"
                      r"trailing 12|12 months|twelve months|overall|year total|for 20\d\d\b)", re.I)
_PERIOD_CUT_NAME_RX = re.compile(r"\s-\s(?:Q[1-4]\s+20\d\d|H[12]\s+20\d\d|20\d\d(?:\s+YTD)?|"
                                 r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+20\d\d)\s*$", re.I)


def is_period_comparison(*texts):
    """True when the words put a full-window figure next to sub-period
    figures (quarters, months, halves, YTD)."""
    joined = ' '.join(str(t or '') for t in texts)
    return bool(PERIOD_RX.search(joined) and WHOLE_RX.search(joined))


def is_period_cut_name(name):
    return bool(_PERIOD_CUT_NAME_RX.search(str(name or '')))


def ensure_period_note(reply, question=''):
    """Append the period line once when the reply compares a window
    total with its periods. Never raises."""
    try:
        text = str(reply or '')
        if not text.strip() or PERIOD_HEADER in text:
            return reply
        if not is_period_comparison(text, question):
            return reply
        return text.rstrip() + '\n\n' + f"{PERIOD_HEADER} {PERIOD_NOTE}"
    except Exception:
        return reply


def ensure(reply, res=None, question='', base_label='', base_people=None):
    """Append the counting block to a viewership reply once. A reply
    that already carries the header gets the missing bullets merged in
    (reply_shape may have opened the block for definitions). Never
    raises; returns the input on any failure."""
    try:
        text = str(reply or '')
        if not text.strip():
            return reply
        res = res if isinstance(res, dict) else {}
        reply = ensure_period_note(text, question)
        text = str(reply)
        if not is_viewership_read(text, res, question):
            return reply
        if carries_definition(text):
            return reply
        lines = block(window_start=res.get('window_start'),
                      window_end=res.get('window_end'),
                      window_label=res.get('window_label') or '',
                      base_label=base_label or res.get('_base_label') or '',
                      base_people=base_people if base_people is not None
                      else res.get('_base_people'),
                      text=text)
        body = text.rstrip()
        if HEADER in body:
            # merge under the existing header
            return body + '\n' + '\n'.join(lines[1:])
        return body + '\n\n' + '\n'.join(lines)
    except Exception:
        return reply
