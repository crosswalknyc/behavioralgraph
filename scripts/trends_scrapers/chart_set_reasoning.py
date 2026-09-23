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
_MAX_TOKENS = int(os.environ.get('CHART_SET_MAX_TOKENS') or 4000)

# A returned value further than this from what the published figure
# implies is not a US-share judgement any more, it is the call
# ignoring the anchor. Those rows fall back to the derived value.
_SHARE_MIN = 0.04
_SHARE_MAX = 0.75

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
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^```(?:json)?|```$', '', t, flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def _quantified_prompt(chart_label: str, platform_label: str,
                       rows: list[dict], target_date_iso: str,
                       ceiling: int, anchors: str) -> str:
    lines = []
    for r in rows:
        v = r.get('weekly_views')
        h = r.get('weekly_hours_viewed')
        wk = r.get('weeks_in_top10')
        bits = []
        if v:
            bits.append(f'{int(v):,} views')
        if h:
            bits.append(f'{int(h):,} hours')
        fig = ' and '.join(bits) if bits else 'not published for this title'
        extra = f', {int(wk)} week(s) on the chart' if wk else ''
        lines.append(f'  #{r["published_rank"]}  {r["title"]}  '
                     f'[worldwide, chart week: {fig}{extra}]')
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
        f"  - Daily US audience descends strictly down the chart: #1 "
        f"is the largest number, #2 the next, and so on to the bottom. "
        f"This is the whole point of the exercise.\n"
        f"  - Gaps between neighbours must VARY. A chart whose values "
        f"step down by a near-constant amount or a near-constant ratio "
        f"reads as manufactured. Real charts have a big drop somewhere "
        f"near the top and a long flat tail, and the shape differs "
        f"week to week.\n"
        f"  - No value above {ceiling:,}.\n"
        f"  - A title with NO published figure is still ON THIS "
        f"CHART, between the titles either side of it, so its value "
        f"sits between theirs. That is the entire information content "
        f"of its rank and it is not a licence to size the title "
        f"freely: a well-known library film at #5 still drew less "
        f"last week than whatever the service put at #4, or it would "
        f"be at #4.\n"
        f"  - Never return a worldwide number. Every value is US, and "
        f"daily.\n"
        f"  - Give exact integers, not round ones. 348,637 not "
        f"350,000.\n\n"
        f"PLATFORM CONTEXT:\n{anchors}\n\n"
        f"Return ONLY JSON:\n"
        f'{{\n'
        f'  "day_factor": <float, week to this day>,\n'
        f'  "day_factor_reason": "<one sentence>",\n'
        f'  "titles": [\n'
        f'    {{"rank": <int>, "title": "<exactly as given>", '
        f'"us_share": <float 0-1>, "us_daily": <int>, '
        f'"share_reason": "<short, why this title skews US or not>"}}\n'
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
        f"  - No value above {ceiling:,}.\n"
        f"  - Every value is US, and daily, not weekly.\n"
        f"  - Give exact integers, not round ones.\n\n"
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
                    if v > 0 and implied > 0 and \
                            0.30 <= (v / implied) <= 3.0:
                        pass
                    elif v > 0 and usable:
                        pass
                    elif implied > 0:
                        v = implied
                    elif v <= 0:
                        fb = min(_SHARE_MAX, max(_SHARE_MIN, 0.38))
                        v = int(round(ww * fb * df))
            if v <= 0:
                continue
            if ceiling:
                v = min(v, int(ceiling))
            values[src['title']] = max(1, v)

        # A title the service charts but the call skipped is placed
        # between its neighbours rather than dropped: it is on the
        # chart, so it has an audience, and leaving a hole is what
        # puts the rail back out of order.
        for i, title in enumerate(ordered):
            if title in values:
                continue
            above = next((values[ordered[j]] for j in range(i - 1, -1, -1)
                          if ordered[j] in values), None)
            below = next((values[ordered[j]]
                          for j in range(i + 1, len(ordered))
                          if ordered[j] in values), None)
            if above and below:
                frac = 0.38 + _h01(f'{slug}|{title}|gapfill') * 0.24
                v = below + (above - below) * frac
            elif above:
                v = above * (0.72 + _h01(f'{slug}|{title}|tailfill') * 0.16)
            elif below:
                v = below * (1.16 + _h01(f'{slug}|{title}|headfill') * 0.22)
            else:
                continue
            values[title] = max(1, int(round(v)))
        return values

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
