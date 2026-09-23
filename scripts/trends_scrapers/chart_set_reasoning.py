"""Reason a published chart as a set, in one call, instead of title by
title.

Why this exists
---------------
Where a service publishes a ranked list we mirror its order, and the
readings beside it then have to descend across that order. Asking for
those readings one title at a time cannot deliver it. Each call sees a
single title and cannot see its neighbours, so the model has nothing
to be consistent with, and on 2026-09-23 it was not: given Netflix's
own published figures AND the exact position, it returned 3.9M a day
for a title Netflix reports at 6.2M views worldwide for the whole
week, roughly eleven times too high, while honouring the same anchor
correctly one row above. Which rows obey and which do not is
arbitrary, and no wording fixes it, because the information needed to
be consistent is not in the call.

So the chart is reasoned as a chart: ten titles, one platform, one
answer that descends. One call per chart per day, which is cheaper
than ten independent ones as well as being the only shape that can
satisfy the constraint.

Two kinds of chart
------------------
**Quantified.** Netflix publishes `weekly_views` and
`weekly_hours_viewed` per title, worldwide, on the same file its
ranking comes from. Those are the strongest provenance anywhere on
this board and the level is derived from them rather than reasoned:
what the call supplies is the US share per title and the week-to-day
factor for the chart.

The US share has to be per title and cannot be one number for the
chart, which is the subtlety that makes this worth doing carefully. A
US rank is by US viewing while the published figure is worldwide, so
the two genuinely disagree: The Whisper Man sat at US #6 on 6.2M
worldwide views while the title at US #2 had 4.6M. Scaling every row
by one share would keep that inversion and there would be nothing
honest to do about it. Per-title shares explain it, because a title
that ranks higher in the US than its worldwide figure implies simply
has a US-skewed audience, and the model can see both columns at once
and say so.

**Ordered but unquantified.** Lionsgate+ and Prime Video publish an
order and no figures. The call gets the platform's real US audience as
an envelope so the set is anchored rather than free-floating, plus the
researched band structure the platform already carries, and returns a
descending set inside it.

Both shapes make the call state its own descent check, so a violation
shows up in the call's own output rather than being discovered two
passes downstream.

Fail-safe throughout. A failed or unusable call leaves the per-item
values exactly as they were, and the coherence pass downstream still
gets its say. Nothing here can empty a rail.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MODEL = (os.environ.get('CHART_SET_MODEL')
          or os.environ.get('STREAM_ESTIMATES_MODEL_HI')
          or 'claude-sonnet-4-5')
# Headroom. The joint share constraint made the answers longer (a
# reason per share plus a notes block), and at 4,000 the JSON was
# being truncated mid-object, which `_extract_json` correctly refused
# and which surfaced as "no usable answer" for a whole chart. A
# silent truncation that looks like a model failure is expensive to
# diagnose, so the budget sits well clear of what the format needs.
_MAX_TOKENS = int(os.environ.get('CHART_SET_MAX_TOKENS') or 12000)

# A returned value further than this from what the published figure
# implies is not a US-share judgement any more, it is the call
# ignoring the anchor. Those rows fall back to the derived value.
_SHARE_MIN = 0.04
_SHARE_MAX = 0.75

# Separation between neighbouring published positions, as a fraction
# of the one above, drawn per title inside this band so a chart
# forced to descend does not descend in even steps.
_SEP_MIN = 0.015
_SEP_MAX = 0.060

# Day factor: a week converted to one day. 1/7 is the flat case and
# real day-of-week effects sit either side of it.
_DAY_MIN = 0.07
_DAY_MAX = 0.26


def _lazy():
    try:
        from .stream_estimates import (_h01, _natural_last_digits,
                                       _cp_normalize)
    except ImportError:
        from scripts.trends_scrapers.stream_estimates import (
            _h01, _natural_last_digits, _cp_normalize)
    return _h01, _natural_last_digits, _cp_normalize


def _extract_json(text: str) -> Optional[dict]:
    """The JSON object in a reply, however it is wrapped.

    An earlier version took the span from the first brace to the LAST
    one, which fails the moment a reply carries a fenced block plus a
    sentence of prose after it: the span then runs past the end of
    the object and no longer parses. A whole Netflix chart was lost
    that way, reported as "no usable answer" while the model had in
    fact answered correctly and had even flagged the one pair it
    could not reconcile. Scanning for the balanced close brace is the
    fix, and it costs nothing.
    """
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^\s*```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```\s*$', '', t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # A thousands separator inside a numeric literal is not valid
    # JSON and cost the films chart three runs: the prompt's own
    # example of a "messy" number was written 348,637, which taught
    # the model to emit `"us_daily": 241,371`. The example is fixed,
    # and this tolerates it regardless, because losing a whole chart
    # to a comma is a poor trade.
    t = re.sub(r'(:\s*-?\d{1,3})(?:,(\d{3}))+(?=\s*[,}\]])',
               lambda mm: mm.group(0).replace(',', ''), t)
    start = t.find('{')
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _quantified_prompt(chart_label: str, platform_label: str,
                       rows: list[dict], target_date_iso: str,
                       ceiling: int, anchors: str) -> str:
    lines = []
    for i, r in enumerate(rows):
        v = r.get('weekly_views')
        h = r.get('weekly_hours_viewed')
        wk = r.get('weeks_in_top10')
        bits = []
        if v:
            bits.append(f'{int(v):,} views')
        if h:
            bits.append(f'{int(h):,} hours')
        if bits:
            extra = f', {int(wk)} week(s) on the chart' if wk else ''
            fig = f'[worldwide, chart week: {" and ".join(bits)}{extra}]'
        else:
            # No figure for this one, so say what brackets it. A
            # title at position N sold fewer than N-1 and more than
            # N+1, and where those two have figures the interval is
            # arithmetic rather than a question.
            up = next((x.get('weekly_views') for x in reversed(rows[:i])
                       if x.get('weekly_views')), None)
            dn = next((x.get('weekly_views') for x in rows[i + 1:]
                       if x.get('weekly_views')), None)
            if up and dn:
                fig = (f'[no figure published. BRACKETED: it drew less '
                       f'than the {int(up):,}-view title above it and '
                       f'more than the {int(dn):,}-view title below it]')
            elif dn:
                fig = (f'[no figure published, and it is at the TOP of '
                       f'the chart: it drew MORE than the {int(dn):,}-'
                       f'view title below it]')
            elif up:
                fig = (f'[no figure published, and it is at the BOTTOM '
                       f'of the chart: it drew LESS than the '
                       f'{int(up):,}-view title above it]')
            else:
                fig = '[no figure published]'
        lines.append(f'  #{r["published_rank"]}  {r["title"]}  {fig}')
    return (
        f"You are sizing the US daily audience for every title on "
        f"{platform_label}'s own published chart, the {chart_label}, "
        f"for {target_date_iso}. You are sizing the WHOLE CHART IN ONE "
        f"GO, not one title at a time, because the answers have to be "
        f"consistent with each other.\n\n"
        f"THE CHART, in the order {platform_label} publishes it (this "
        f"order is a US ranking and is not negotiable):\n"
        + '\n'.join(lines) + '\n\n'
        f"WHAT THE FIGURES ARE. The views and hours above are "
        f"{platform_label}'s own reported numbers for the chart week "
        f"and they are WORLDWIDE. They are the strongest anchor you "
        f"have and the level must come from them, not from a tier.\n\n"
        f"THE ONE THING THAT NEEDS REAL JUDGEMENT. The ranking is by "
        f"US viewing and the figures are worldwide, so the two "
        f"genuinely disagree in places: a title can sit high on the US "
        f"chart on a modest worldwide figure, or low on a large one. "
        f"That difference is the US share of its audience and it is "
        f"per title, not one number for the chart. Reason each share "
        f"from what the title actually is: US-produced, US-cast or "
        f"US-subject work skews high; a UK, Korean, Indian, Spanish-"
        f"language or otherwise internationally-led title skews low; a "
        f"global animated or franchise release sits in between. Your "
        f"shares must be individually defensible AND must reproduce "
        f"the published US order. If reproducing the order needs a "
        f"share you cannot defend, say so in `notes` rather than "
        f"forcing it.\n\n"
        f"THEN THE DAY. Convert the chart week to {target_date_iso} "
        f"with one factor for the whole chart. Flat would be 0.143. "
        f"Reason the real one from the day of week and anything "
        f"specific about this date, and state it.\n\n"
        f"HARD CONSTRAINTS:\n"
        f"  - The shares are NOT free parameters. The chart is "
        f"ordered by US viewing and the figures are worldwide, so for "
        f"every adjacent pair the PRODUCT must descend:\n"
        f"        worldwide(n) x share(n)  >  worldwide(n+1) x "
        f"share(n+1)\n"
        f"    Solve that jointly across the whole chart rather than "
        f"picking a share per title and hoping the order falls out. "
        f"It is informative, not just restrictive: a title sitting "
        f"high on modest worldwide views MUST have a high US share, "
        f"and the chart is telling you so.\n"
        f"  - Daily US audience therefore descends strictly down the "
        f"chart: #1 is the largest number, #2 the next, and so on to "
        f"the bottom. This is the whole point of the exercise.\n"
        f"  - A share is still a real quantity. If the only way to "
        f"satisfy the order is a share you cannot defend for that "
        f"title, say which pair in `notes` rather than returning an "
        f"absurd one.\n"
        f"  - Gaps between neighbours must VARY. A chart whose values "
        f"step down by a near-constant amount or a near-constant ratio "
        f"reads as manufactured. Real charts have a big drop somewhere "
        f"near the top and a long flat tail, and the shape differs "
        f"week to week.\n"
        f"  - No value above {ceiling:,} on any single day. That is this service's published daily cap for its top slot and it is a hard limit, not a target.\n"
        f"  - A title marked BRACKETED has no published figure and "
        f"is NOT a free-standing question. Its bounds are given and "
        f"they are hard: place it INSIDE that interval. Do not ask "
        f"what its audience is in general, ask what value belongs "
        f"between those two numbers. A famous library film at #5 "
        f"still drew less last week than whatever the service put at "
        f"#4, or it would be at #4, and its worldwide fame is not "
        f"evidence against the service's own ranking. Where several "
        f"bracketed titles sit next to each other they share one "
        f"interval and must descend within it, spaced unevenly.\n"
        f"  - Never return a worldwide number. Every value is US, and "
        f"daily.\n"
        f"  - Give exact integers, not round ones: 348637 rather "
        f"than 350000. Write every number as PLAIN DIGITS with no "
        f"thousands separators, because a comma inside a number is "
        f"not valid JSON and the whole answer is lost.\n\n"
        f"PLATFORM CONTEXT:\n{anchors}\n\n"
        f"Return ONLY JSON:\n"
        f'{{\n'
        f'  "day_factor": <float, week to this day>,\n'
        f'  "day_factor_reason": "<one sentence>",\n'
        f'  "titles": [\n'
        f'    {{"rank": <int>, "title": "<exactly as given>", '
        f'"us_share": <float 0-1>, "us_daily": <int>, '
        f'"share_reason": "<max 12 words>"}}\n'
        f'  ],\n'
        f'  "descends": <true|false, YOUR OWN CHECK that us_daily '
        f'falls strictly as rank rises>,\n'
        f'  "notes": "<anything you could not reconcile>"\n'
        f'}}'
    )


def _envelope_prompt(chart_label: str, platform_label: str,
                     rows: list[dict], target_date_iso: str,
                     ceiling: int, anchors: str) -> str:
    lines = [f'  #{r["published_rank"]}  {r["title"]}'
             + (f'  [{r["category"]}]' if r.get('category') else '')
             for r in rows]
    return (
        f"You are sizing the US daily audience for every title on "
        f"{platform_label}'s own published chart, the {chart_label}, "
        f"for {target_date_iso}. You are sizing the WHOLE CHART IN ONE "
        f"GO, not one title at a time, because the answers have to be "
        f"consistent with each other.\n\n"
        f"THE CHART, in the order {platform_label} publishes it (this "
        f"order is a US ranking and is not negotiable):\n"
        + '\n'.join(lines) + '\n\n'
        f"{platform_label} publishes the order and no figures, so the "
        f"level comes from the platform's own audience below. Treat "
        f"that as the envelope the whole chart sits inside: the chart "
        f"is the most watched part of this service and the rest of the "
        f"catalog sits under it, so the top of the chart should read "
        f"like a real share of the service's daily audience and the "
        f"bottom of the chart should still read like one of its "
        f"better-watched titles rather than like deep catalog.\n\n"
        f"HARD CONSTRAINTS:\n"
        f"  - Daily US audience descends strictly down the chart: #1 "
        f"is the largest number, #2 the next, and so on to the bottom. "
        f"This is the whole point of the exercise.\n"
        f"  - Gaps between neighbours must VARY. A chart whose values "
        f"step down by a near-constant amount or a near-constant ratio "
        f"reads as manufactured. Real charts have a big drop somewhere "
        f"near the top and a long flat tail.\n"
        f"  - Differentiate on what each title IS: a marquee franchise "
        f"entry, a current original, a library evergreen and an older "
        f"TV season do not draw alike even when they sit next to each "
        f"other on the chart.\n"
        f"  - No value above {ceiling:,} on any single day. That is this service's published daily cap for its top slot and it is a hard limit, not a target.\n"
        f"  - Every value is US, and daily, not weekly.\n"
        f"  - Give exact integers, not round ones, written as PLAIN "
        f"DIGITS with no thousands separators: a comma inside a "
        f"number is not valid JSON and the whole answer is lost.\n\n"
        f"PLATFORM AUDIENCE AND BANDS:\n{anchors}\n\n"
        f"Return ONLY JSON:\n"
        f'{{\n'
        f'  "titles": [\n'
        f'    {{"rank": <int>, "title": "<exactly as given>", '
        f'"us_daily": <int>, "basis": "<short, what this title is and '
        f'why it lands here>"}}\n'
        f'  ],\n'
        f'  "descends": <true|false, YOUR OWN CHECK that us_daily '
        f'falls strictly as rank rises>,\n'
        f'  "notes": "<anything you could not reconcile>"\n'
        f'}}'
    )


def _call(client, prompt: str) -> Optional[dict]:
    try:
        # Temperature 0. Two runs of the same chart on the same day
        # were returning different orderings, which made the descent
        # a coin toss and the whole pass unreproducible. A chart is a
        # measurement, not a draft.
        resp = client.messages.create(
            model=_MODEL, max_tokens=_MAX_TOKENS, temperature=0,
            messages=[{'role': 'user', 'content': prompt}])
    except TypeError:
        resp = client.messages.create(
            model=_MODEL, max_tokens=_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}])
    except Exception as e:
        logger.warning("chart_set: model call failed: %s", e)
        return None
    text = ''
    for block in getattr(resp, 'content', None) or []:
        if getattr(block, 'type', None) == 'text':
            text += getattr(block, 'text', '') or ''
    return _extract_json(text)


def _apply_published_scale(values: dict, rows: list[dict],
                           ordered: list[str], slug: str,
                           df: float) -> list:
    """Let the published figures set the chart's SCALE, and today's
    order decide the positions.

    The pairwise form of this asked, for every adjacent pair, that
    worldwide(n) x share(n) exceed worldwide(n+1) x share(n+1). That
    is the right constraint when both inputs describe the same week,
    and ours deliberately do not: since this morning the ORDER comes
    from Netflix's live daily US rail and the FIGURES come from last
    week's published worldwide file. A title that is hot today but
    was modest last week can then only satisfy the pair with an
    implausible share, and the call said so unprompted: putting Why
    Did I Get Married Again below its neighbours needed a US share
    under 0.046, indefensible for a Tyler Perry film, and it named
    the period mismatch as the likely cause. It was right.

    So the two sources each do what they are actually good for, which
    has been the principle throughout. The published figures are the
    best evidence anywhere for how big this chart is in absolute
    terms, and poor evidence for today's relative ordering, which is
    exactly what the daily rail is for.

    The chart's total daily US audience comes from the published
    worldwide total times the chart-level US share the call reasoned.
    That total is then distributed across today's positions by a
    decay drawn per chart, so the ordering is today's and the
    magnitude is the published data's, and descent holds by
    construction rather than by correction.

    A per-title implied share that lands outside the credible band is
    REPORTED, not corrected. It is a real signal about the gap
    between the two periods and worth seeing rather than smoothing.
    """
    _h01, _nat, _cp = _lazy()
    anchors = [(i, r) for i, r in enumerate(rows)
               if r.get('weekly_views') and values.get(r['title'])]
    if len(anchors) < 2:
        return []

    total_ww = sum(float(r['weekly_views']) for _i, r in anchors)
    if total_ww <= 0:
        return []

    # The chart-level share comes from the SHARES the call reasoned,
    # weighted by each title's worldwide figure, not from the values
    # it produced. Deriving it from the values is circular when those
    # values are the thing being replaced: a first attempt did that
    # and, fed the same bad numbers, clamped to the top of the band
    # and put three titles above a 100% US share, which is more US
    # viewers than the title had worldwide.
    num = den = 0.0
    for _i, r in anchors:
        sh = r.get('_us_share')
        ww = float(r['weekly_views'])
        if isinstance(sh, (int, float)) and _SHARE_MIN <= sh <= _SHARE_MAX:
            num += sh * ww
            den += ww
    chart_share = (num / den) if den else 0.38
    chart_share = min(_SHARE_MAX, max(_SHARE_MIN, chart_share))
    scale = total_ww * chart_share * df

    # Weights strictly decreasing down today's order, with the step
    # drawn per title so the chart does not descend in even
    # increments. Real charts drop hard near the top and flatten out.
    weights, w = [], 1.0
    for _i, r in anchors:
        weights.append(w)
        w *= 0.62 + _h01(f'{slug}|{r["title"]}|scaledecay') * 0.26
    wsum = sum(weights) or 1.0

    # The distribution says where each title sits; its own worldwide
    # figure says what it could possibly be. A title cannot draw a
    # larger US audience than its whole worldwide count, so each
    # value is boxed by the share band applied to its OWN figure and
    # the sequence is walked down inside those boxes. Where a box
    # binds, the order is honoured and the deviation reported: that
    # is the gap between today's rail and last week's file, and it
    # is worth seeing rather than smoothing away.
    unmet = []
    ceiling_v = None
    for (_i, r), wt in zip(anchors, weights):
        ww = float(r['weekly_views'])
        lo = ww * _SHARE_MIN * df
        hi = ww * _SHARE_MAX * df
        want = max(1.0, scale * wt / wsum)
        cap = hi if ceiling_v is None else min(
            hi, ceiling_v * (1.0 - (_SEP_MIN + _h01(
                f'{slug}|{r["title"]}|scalesep') * (_SEP_MAX - _SEP_MIN))))
        v = min(max(want, lo), max(cap, 1.0))
        if ceiling_v is not None and v >= ceiling_v:
            v = ceiling_v * 0.97
        values[r['title']] = max(1, int(round(v)))
        ceiling_v = v
        implied = v / (ww * df) if df else 0.0
        if implied < _SHARE_MIN * 0.99 or implied > _SHARE_MAX * 1.01:
            unmet.append((r['title'], implied))

    if unmet:
        for title, implied in unmet:
            logger.info(
                "chart_set %s: %r sits where today's rail puts it, "
                "which implies a US share of %.0f%% against last "
                "week's worldwide figure. The two cover different "
                "periods, so this is the gap between them rather "
                "than a fault in either.",
                slug, title, implied * 100)
    return unmet


def _bracket_unpublished(values: dict, rows: list[dict],
                         ordered: list[str], slug: str,
                         quantified: bool,
                         ceiling: Optional[int]) -> dict:
    """Place every title the service published no figure for INSIDE
    the interval its neighbours define.

    A title at published position N is not a free-standing question.
    It sold fewer than the title at N-1 and more than the title at
    N+1, and where those two have published figures the interval is
    known arithmetic rather than a hint. Asking the call what Top Gun:
    Maverick drew produced 1,683,644 beside neighbours near 250,000;
    asking what belongs between those neighbours cannot.

    So the published figures are HARD BOUNDS here, applied after the
    call rather than trusted to it. A run of consecutive unpublished
    titles shares one interval and descends within it.

    Two edges need their own rule because interpolation has nothing
    to work with:
      * position 1 unpublished has only a lower bound, so it sits a
        drawn step ABOVE the first published figure below it
      * the last position unpublished has only an upper bound, so it
        sits a drawn step BELOW the last published figure above it

    Spacing is drawn per title on the log scale. Even spacing between
    two anchors is the easiest ladder in the world to produce
    accidentally and would be visible as one.
    """
    _h01, natural_digits, _cp = _lazy()
    if not quantified:
        # Nothing published a figure, so there is no interval to
        # place anything inside and the whole set was reasoned
        # against the platform envelope instead.
        return values

    anchored: set = set()
    for r in rows:
        if r.get('weekly_views') and values.get(r['title']):
            anchored.add(r['title'])
    if not anchored:
        return values

    n = len(ordered)

    def _val(i):
        return values.get(ordered[i])

    i = 0
    while i < n:
        if ordered[i] in anchored and _val(i):
            i += 1
            continue
        # A maximal run of positions with no published figure.
        j = i
        while j < n and not (ordered[j] in anchored and _val(j)):
            j += 1
        # Bound against a MONOTONE ENVELOPE of the anchors rather
        # than against the two nearest ones. Before the coherence
        # pass runs the published values do not necessarily descend
        # among themselves, so the nearest pair can be inverted and
        # no value satisfies both. The envelope is the tightest
        # bound that is actually satisfiable: the smallest anchor
        # anywhere above, and the largest anchor anywhere below.
        # Nothing here rewrites an anchor; a published block that
        # genuinely conflicts with itself is the coherence pass's
        # problem and it holds the rail rather than papering over it.
        upper = None
        for k in range(i - 1, -1, -1):
            if ordered[k] in anchored and _val(k):
                upper = _val(k) if upper is None else min(upper, _val(k))
        lower = None
        for k in range(j, n):
            if ordered[k] in anchored and _val(k):
                lower = _val(k) if lower is None else max(lower, _val(k))
        run = ordered[i:j]
        if upper is None and lower is None:
            i = j
            continue
        if upper is None:
            # Unpublished at the top of the chart: above everything
            # below it, by a drawn margin rather than a fixed one.
            base = lower
            step = 1.0
            for pos, title in enumerate(reversed(run), start=1):
                step *= 1.10 + _h01(f'{slug}|{title}|headroom') * 0.34
                v = base * step
                if ceiling:
                    v = min(v, float(ceiling))
                values[title] = max(1, int(round(v)))
            i = j
            continue
        if lower is None:
            # Unpublished at the bottom: below everything above it.
            v = float(upper)
            for title in run:
                v *= 0.88 - _h01(f'{slug}|{title}|tailroom') * 0.22
                values[title] = max(1, int(round(max(v, 1.0))))
            i = j
            continue

        # The ordinary case: a known interval, shared by the run.
        import math
        hi, lo = float(upper), float(lower)
        if hi <= lo:
            # The anchors above and below cross, so there is no
            # interval to sit in. Place on the geometric mean and
            # leave it: the coherence pass sees the same conflict
            # and reports the rail rather than inventing a way out.
            mid = math.sqrt(max(hi, 1.0) * max(lo, 1.0))
            hi, lo = mid * 1.04, mid * 0.96
        lhi, llo = math.log(max(hi, 1.0)), math.log(max(lo, 1.0))
        weights = [0.55 + _h01(f'{slug}|{t}|bracket') for t in run]
        weights.append(0.55 + _h01(f'{slug}|{run[-1]}|bracket|tail'))
        total = sum(weights) or 1.0
        acc = 0.0
        for idx, title in enumerate(run):
            acc += weights[idx]
            v = math.exp(lhi - (lhi - llo) * (acc / total))
            if ceiling:
                v = min(v, float(ceiling))
            values[title] = max(1, int(round(v)))
        i = j

    # Natural last digits can nudge a bracketed value onto or past a
    # neighbour, so settle strict descent afterwards while keeping
    # the digits natural.
    for idx, title in enumerate(ordered):
        v = values.get(title)
        if not v:
            continue
        nv = max(1, natural_digits(int(v), title, f'{slug}|bracket'))
        values[title] = nv
    # Settle only the titles this pass placed. A published row that
    # still reads out of order is the coherence pass's business: it
    # has a move budget, it reports what it cannot reconcile, and it
    # holds a rail rather than forcing one. Quietly pulling a
    # figure-derived value down here would bypass all three.
    for idx in range(1, n):
        a, b = ordered[idx - 1], ordered[idx]
        if a not in values or b not in values:
            continue
        if b in anchored:
            continue
        if values[b] >= values[a]:
            drop = 0.955 - _h01(f'{slug}|{b}|settle') * 0.06
            values[b] = max(1, int(round(values[a] * drop)))
            while values[b] >= values[a] and values[b] > 1:
                values[b] -= 1
    return values


_TAIL_CHUNK = 40


def reason_catalog_tail(client, *, slug: str, platform_label: str,
                        titles: list[str], ceiling_value: int,
                        target_date_iso: str, anchors: str = '',
                        floor_hint: int = 0) -> dict:
    """Size the catalog BELOW a published chart, as a set.

    The chart is only the top of a rail. Everything under it is
    catalog, and on Lionsgate+ that catalog was still carrying
    readings derived from where each title happened to sit on a
    storefront page: 84 of 99 titles read above the chart's own last
    position, topping out at 21,004 a day on a service whose entire
    audience is around 38,600 a day. A title the service does not
    chart cannot out-draw the one it ranks last, so those are not
    levels, they are the old page-position regime still in place.

    Reasoned against the platform's real audience with the chart's
    floor as a hard ceiling, in chunks so a long catalog does not
    have to fit one response, each chunk starting below the previous
    one's floor. Titles the call skips keep what they had, so a
    partial answer never empties a rail.
    """
    out: dict[str, int] = {}
    if not titles or ceiling_value <= 1:
        return out
    ceiling = float(ceiling_value)
    # A floor as well as a ceiling. Carrying each chunk's minimum
    # forward as the next chunk's ceiling compounds: on a 99-title
    # catalog the third chunk inherited a ceiling of 29 and the
    # deepest titles landed near 2 viewers a day, which is not a
    # reading of anything. A service's deepest catalog sits roughly
    # an order of magnitude below its chart floor, not three, so the
    # running ceiling is not allowed under that.
    floor = max(float(floor_hint or 0), ceiling / 12.0, 1.0)
    for start in range(0, len(titles), _TAIL_CHUNK):
        chunk = titles[start:start + _TAIL_CHUNK]
        listing = '\n'.join(f'  {i + 1}. {t}'
                             for i, t in enumerate(chunk))
        prompt = (
            f"Size the daily US audience for {len(chunk)} titles in "
            f"{platform_label}'s CATALOG for {target_date_iso}. These "
            f"sit BELOW the service's own published chart, so none of "
            f"them is among its most watched.\n\n"
            f"HARD CEILING: {int(ceiling):,} daily US viewers. That is "
            f"what the title at the BOTTOM of {platform_label}'s "
            f"published chart draws. A title the service did not "
            f"chart cannot out-draw the one it ranks last, or the "
            f"service would have charted it. Every number you return "
            f"must be below it, and most should be well below.\n\n"
            f"TITLES (this order is the page's, not a ranking, and "
            f"carries no popularity signal):\n{listing}\n\n"
            f"Differentiate on what each title IS. A marquee "
            f"franchise entry, a recognisable catalog film, an older "
            f"TV season and a long-tail title do not draw alike, and "
            f"the spread across a catalog should be WIDE rather than "
            f"clustered. Write every number as PLAIN DIGITS with no "
            f"thousands separators, exact rather than round.\n\n"
            f"PLATFORM AUDIENCE:\n{anchors or '(none supplied)'}\n\n"
            f'Return ONLY JSON: {{"titles": [{{"title": "<exactly as '
            f'given>", "us_daily": <int>, "basis": "<max 10 words>"}}]}}')
        parsed = _call(client, prompt)
        if not isinstance(parsed, dict):
            logger.info("chart_set %s: catalog chunk %d unreadable, "
                         "those titles keep what they had",
                         slug, start // _TAIL_CHUNK + 1)
            continue
        _h01, natural_digits, cp_norm = _lazy()
        by_norm = {cp_norm(t): t for t in chunk}
        got = []
        for row in (parsed.get('titles') or []):
            if not isinstance(row, dict):
                continue
            t = by_norm.get(cp_norm(str(row.get('title') or '')))
            if not t:
                continue
            try:
                v = int(float(row.get('us_daily') or 0))
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            # The ceiling is not advisory.
            if v >= ceiling:
                v = int(ceiling * (0.80 - _h01(f'{slug}|{t}|tailcap')
                                   * 0.25))
            if v < floor:
                v = int(round(floor * (1.0 + _h01(f'{slug}|{t}|tailfloor')
                                       * 0.35)))
            got.append((t, max(1, v)))
        for t, v in got:
            out[t] = max(1, natural_digits(
                int(v), t, f'{slug}|catalog|{target_date_iso}'))
        if got:
            ceiling = max(min(v for _t, v in got), floor * 1.25)
        logger.info("chart_set %s: catalog chunk %d sized %d of %d "
                     "title(s), next ceiling %d", slug,
                     start // _TAIL_CHUNK + 1, len(got), len(chunk),
                     int(ceiling))
    return out


def reason_chart(client, *, slug: str, platform_label: str,
                 chart_label: str, rows: list[dict],
                 target_date_iso: str, ceiling: int,
                 anchors: str = '') -> dict:
    """Size one published chart in one call.

    `rows` are dicts with at least `title` and `published_rank`, in
    chart order, optionally carrying `weekly_views` /
    `weekly_hours_viewed` when the service publishes them.

    Returns `{'values': {title -> int}, 'quantified': bool,
    'day_factor': float|None, 'notes': str, 'model_descends': bool}`.
    An empty `values` means the caller keeps what it had.
    """
    out: dict[str, Any] = {'values': {}, 'quantified': False,
                           'day_factor': None, 'notes': '',
                           'model_descends': None}
    rows = [r for r in rows
            if r.get('title') and isinstance(r.get('published_rank'), int)]
    if len(rows) < 2:
        return out
    rows = sorted(rows, key=lambda r: r['published_rank'])
    quantified = any(r.get('weekly_views') or r.get('weekly_hours_viewed')
                     for r in rows)
    out['quantified'] = quantified

    prompt = (_quantified_prompt if quantified else _envelope_prompt)(
        chart_label, platform_label, rows, target_date_iso, ceiling,
        anchors or '(no platform notes supplied)')

    _h01, natural_digits, cp_norm = _lazy()
    by_norm = {cp_norm(r['title']): r for r in rows}
    ordered = [r['title'] for r in rows]

    def _build(parsed: dict, df: float) -> dict:
        """The values we would actually write, given one answer."""
        values: dict[str, int] = {}
        for t in (parsed.get('titles') or []):
            if not isinstance(t, dict):
                continue
            src = by_norm.get(cp_norm(str(t.get('title') or '')))
            if not src:
                continue
            try:
                v = int(float(t.get('us_daily') or 0))
            except (TypeError, ValueError):
                v = 0
            if quantified:
                # A guard, not a second opinion. The call reasoned
                # the whole chart together and that is the only thing
                # here that can be internally consistent, so its
                # value stands wherever it is anywhere near the
                # published figure. Substituting an independently
                # derived number for some rows and not others is what
                # broke the first version: the raw answer descended,
                # the partial overwrite put four inversions back into
                # it, and the descent check was reading the raw
                # answer so it never noticed.
                ww = int(src.get('weekly_views') or 0)
                try:
                    share = float(t.get('us_share') or 0)
                except (TypeError, ValueError):
                    share = 0.0
                if ww > 0:
                    usable = _SHARE_MIN <= share <= _SHARE_MAX
                    implied = int(round(ww * share * df)) if usable else 0
                    if implied > 0 and v > 0 and \
                            0.30 <= (v / implied) <= 3.0:
                        pass                  # agrees with its own share
                    elif implied > 0:
                        # The value and the share it came with do not
                        # describe the same title. The arithmetic on a
                        # figure the service published wins over a
                        # number that contradicts it: an earlier
                        # version kept the value whenever the share
                        # LOOKED plausible, and shipped a Netflix #1
                        # at 7,837,259 a day against 9.7M worldwide
                        # views for the week, which needs a US share
                        # of 5.21.
                        v = implied
                    else:
                        fb = min(_SHARE_MAX, max(_SHARE_MIN, 0.38))
                        v = int(round(ww * fb * df))
            if quantified:
                try:
                    sh = float(t.get('us_share') or 0)
                except (TypeError, ValueError):
                    sh = 0.0
                if sh > 0:
                    src['_us_share'] = sh
            if v <= 0:
                continue
            if ceiling:
                v = min(v, int(ceiling))
            values[src['title']] = max(1, v)

        if quantified:
            # Settle the figure-carrying rows against each other
            # first. Bracketing an unpublished title between two
            # anchors only means something once those anchors are
            # themselves in order.
            _apply_published_scale(values, rows, ordered, slug, df)
        return _bracket_unpublished(values, rows, ordered, slug,
                                    quantified, ceiling)

    def _violations(values: dict) -> list:
        """Neighbouring published positions this answer got backwards,
        measured on the values we would write rather than on the raw
        response, and never on the call's own `descends` flag: a model
        asked to mark its own homework marks it wrong in both
        directions."""
        seq = [(r['published_rank'], r['title'], values.get(r['title']))
               for r in rows if values.get(r['title'])]
        seq.sort()
        return [(a, at, av, b, bt, bv)
                for (a, at, av), (b, bt, bv) in zip(seq, seq[1:])
                if bv >= av]

    # One retry, and only when the answer does not descend. The call
    # is asked to check itself, so a failure there is specific and
    # checkable and worth one more attempt with the offending pairs
    # named. Not a general retry loop: a chart that cannot descend
    # for a real reason says so twice and the caller keeps what it
    # had.
    best: dict = {}
    parsed = None
    df = 1.0 / 7.0
    for attempt in (1, 2):
        p = prompt
        if attempt == 2:
            bad = _violations(best)
            if not bad:
                break
            p = prompt + (
                '\n\nYOUR PREVIOUS ANSWER DID NOT DESCEND. These '
                'neighbours came back the wrong way round:\n'
                + '\n'.join(f'  #{a} {at} = {av:,} but #{b} {bt} = '
                             f'{bv:,}' for a, at, av, b, bt, bv in bad)
                + '\n\nFix the levels so every one of those descends, '
                  'keeping each value defensible on its own terms. A '
                  'title with no published figure belongs between its '
                  'neighbours. If one genuinely cannot be reconciled, '
                  'leave it and say which in `notes` rather than '
                  'forcing the rest out of shape.')
        got = _call(client, p)
        if not isinstance(got, dict):
            if attempt == 1:
                logger.info("chart_set %s/%s: unusable answer, retrying "
                             "once", slug, chart_label)
            continue
        parsed = got
        try:
            cand_df = float(got.get('day_factor') or 0)
        except (TypeError, ValueError):
            cand_df = 0.0
        if quantified and not (_DAY_MIN <= cand_df <= _DAY_MAX):
            cand_df = 1.0 / 7.0
        vals = _build(got, cand_df)
        if not best or len(_violations(vals)) < len(_violations(best)):
            best, df = vals, cand_df
        if not _violations(best):
            break
        if attempt == 1:
            logger.info("chart_set %s/%s: first answer left %d "
                         "inversion(s), retrying once with them named",
                         slug, chart_label, len(_violations(best)))

    if not isinstance(parsed, dict) or not best:
        logger.warning("chart_set %s/%s: no usable answer, keeping "
                       "per-item values", slug, chart_label)
        return out

    out['notes'] = str(parsed.get('notes') or '')[:400]
    out['model_descends'] = parsed.get('descends')
    out['day_factor'] = df if quantified else None
    left = _violations(best)
    out['unreconciled'] = [(a, at, b, bt) for a, at, _, b, bt, _ in left]
    if left:
        logger.warning(
            "chart_set %s/%s: %d position pair(s) still do not "
            "descend after a retry; the coherence pass will decide "
            "whether they can be closed: %s", slug, chart_label,
            len(left), '; '.join(f'#{a} vs #{b}' for a, _, b, _
                                 in out['unreconciled'][:4]))

    # Natural last digits on everything we set, so roughly one value
    # in ten still ends in zero and the digit guard stays clean. A
    # mechanical scaling of a published series would otherwise inherit
    # the trailing zeros of the source figures wholesale.
    for title, v in list(best.items()):
        best[title] = max(1, natural_digits(
            int(v), title, f'{slug}|{chart_label}|{target_date_iso}'))

    out['values'] = best
    return out
