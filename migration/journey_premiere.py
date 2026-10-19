#!/usr/bin/env python3
"""A series premiere's first-episode audience (journey_kind 'premiere',
2026-10-19, Jenna: "build one showing about people who watched the first
episode of NCIS: New York and show where they came from and how they
found out about the show").

The universe is everyone who watched the first episode to the end on a
digital surface inside the premiere week. The spine walks the country to
them: heard about the show, looked it up, started the episode, watched it
through. The fork off "started" is finished in one sitting, or stopped and
came back to finish. The tables answer the two questions in the ask (where
they came from, how they found out) and a third the ask leaned on: who
brought them.

Every table derives from ONE model of the cast draws. Each star (and two
non-star groups: the franchise name itself, and everything else) has a
share of the first-episode audience and a set of measures over the people
it brought: watched live, finished in one sitting, came back for episode 2,
posted about it, engaged with the star's own posts before the premiere,
new to the franchise, and clicked an ad and bought during the episode (the
second screen). The fork's same-session count, the "new to the franchise"
origin row, the episode 2 return row, the live row and the second-screen
purchase row are all sums over those groups, so the story cannot disagree
with itself anywhere on the page.

The ordering the read carries is set by the inputs, not by the research
pass: `cast` lists the stars in DRAW order (first = biggest draw, with a
narrow salted gap to the second), `involvement_order` ranks them on every
involvement measure, `new_fans_order` on the share who were new to the
franchise. The research pass sizes everything against public anchors; the
shaping pass then holds those orders with salted gaps and plausibility
bands, so a star who must sit above another does, on every measure, in
share and in count.

CLI (on the build box, keys loaded):
    python3 -m migration.journey_premiere --subject "NCIS: New York" \
        --network CBS --platform "Paramount+" --premiere-date 2026-10-06 \
        --cast "LL Cool J|Sam Hanna|NCIS: Los Angeles" \
        --cast "Jennifer Beals|Robyn Wells|The L Word" \
        --cast "Scott Caan|Nick Schaeffer|Hawaii Five-0" \
        --involvement-order "Jennifer Beals,LL Cool J,Scott Caan" \
        --new-fans-order "Jennifer Beals,Scott Caan,LL Cool J" \
        --pdf-dir /root/mw_out --prim-json /root/mw_out/ncisny.prim.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
_WEBAPP = _HERE.parent
for p in (str(_WEBAPP), str(_WEBAPP.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from migration.journey_media_week import (  # noqa: E402
    US_GEN_POP, US_PER_PANELIST, _claude_json_cli, _clean, _dates, _h, _messy,
    _rows_partition, _window_label, republish,
)

KIND = 'premiere'
BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')

# the measures every star is shown on; the same list, the same order, for
# each of them (Jenna 2026-10-19: "any stats you show for jennifer beals
# need to also be shown for scott cahn and ll cool j")
MEASURES = (
    {'id': 'draw', 'label': 'People they brought to the first episode', 'basis': 'share of everyone who watched the first episode',
     'kind': 'draw'},
    {'id': 'live', 'label': 'Watched it live on premiere night', 'basis': 'share of the people they brought', 'kind': 'pct'},
    {'id': 'one_sitting', 'label': 'Finished the episode in one sitting', 'basis': 'share of the people they brought', 'kind': 'pct'},
    {'id': 'episode2', 'label': 'Came back for episode 2', 'basis': 'share of the people they brought', 'kind': 'pct'},
    {'id': 'posted', 'label': 'Posted, commented or shared about the show', 'basis': 'share of the people they brought', 'kind': 'pct'},
    {'id': 'followed_star', 'label': "Engaged with the star's own posts before the premiere", 'basis': 'share of the people they brought',
     'kind': 'pct'},
    {'id': 'new_fan', 'label': 'New to the franchise: no episode of any earlier series in the past year',
     'basis': 'share of the people they brought', 'kind': 'pct'},
    {'id': 'ad_buy', 'label': 'Clicked an ad and bought something during the episode', 'basis': 'share of the people they brought',
     'kind': 'pct'},
)
_INVOLVEMENT = ('live', 'one_sitting', 'episode2', 'posted', 'followed_star', 'ad_buy')

# plausibility bands for a star's measures (share of the people they brought)
_BANDS = {
    'live': (28.0, 78.0), 'one_sitting': (62.0, 96.0), 'episode2': (40.0, 92.0), 'posted': (3.0, 45.0),
    'followed_star': (2.0, 60.0), 'new_fan': (8.0, 64.0), 'ad_buy': (0.6, 12.0),
}
# the salted gap between consecutive stars in an order, per measure (lo, hi) in
# points: a value already inside the band keeps the research number, one
# outside it moves to the salted gap. The first pair is narrow where the ask
# said "just slightly below"; the second pair is wide so a distant third can
# stay distant.
_GAPS = {
    'draw': ((1.6, 3.4), (0.0, 0.0)),            # the third star is a RATIO of the second (see _DRAW_THIRD_RATIO)
    'live': ((2.5, 12.0), (1.0, 20.0)), 'one_sitting': ((1.5, 8.0), (1.0, 15.0)), 'episode2': ((2.5, 12.0), (2.0, 20.0)),
    'posted': ((4.0, 14.0), (3.0, 20.0)), 'followed_star': ((4.0, 14.0), (6.0, 40.0)), 'new_fan': ((10.0, 24.0), (2.0, 30.0)),
    'ad_buy': ((0.4, 1.2), (0.2, 0.9)),
}
_DRAW_THIRD_RATIO = (0.34, 0.44)   # the third star's draw as a share of the second's
_STAR_SUM_CAP = 86.0          # the non-star groups keep at least 14 points of the audience
_STAR_SUM_FLOOR = 75.0        # and the stars carry the audience: the ask is that they were the drivers
_DEFAULT_GROUPS = (
    {'id': 'franchise', 'name': 'The {franchise} name and the {night} block',
     'came_from': 'Regular viewers of the franchise block who would have watched whatever ran in the slot'},
    {'id': 'other', 'name': 'Something else', 'came_from': 'The setting, the case, another cast member, a friend'},
)


# ------------------------------------------------------------------ research
RESEARCH_SYSTEM_PROMPT = """You are the analyst behind Crosswalk's Digital Journey IQ. You are describing the people who watched the FIRST EPISODE of a new series through to the end, as the Crosswalk clickstream panel of 10M US consumers sees it: where they came from, how they found out about the show, who brought them, and what they did during and after the episode. Return JSON only.

THE PANEL SEES DIGITAL ONLY. A first-episode viewer in this read watched on a digital surface: the streaming home's live feed or next-day on-demand copy, the network's own app or site, or a live TV streaming service (YouTube TV, Hulu + Live TV, DirecTV Stream, Fubo, Sling). A person who only watched on a cable box or an antenna is outside the panel and outside this universe. Every other behavior must be digital too: promos seen inside a stream, posts, articles, trailers on YouTube, search, the streaming home's own screens, podcasts, group chats.

Use web search to anchor: the premiere's published night-of audience and its lead-in, the slot and the streaming arrangement, the cast's prior series and their audiences, the cast's own social accounts and their size, the casting news and the first-look post and how fans reacted to each, the fan communities (subreddits, Facebook groups), the franchise's current viewers, and the streaming home's US reach. Reason from those anchors to the shares below. Anchors stay internal; nothing in the output names a source, a rating service, a survey or a methodology word.

DEFINITIONS
- universe.live_same_day_viewers: the published night-of audience of the premiere (people). digital_live_share_pct: the share of that night-of audience that watched on a digital surface (a live TV streaming service, the streaming home's live feed, the network app). catch_up_pct_of_live: the people who watched the episode on demand on a digital surface in the 7 days after, as a share of the night-of audience. The universe is night-of digital plus catch-up.
- heard_share_of_us_pct: share of the US population that heard about the show on a digital surface before or during the premiere week. looked_up_pct_of_heard: of those, the share who looked it up (searched it, watched the trailer, opened its page, read an article, opened a cast post). finished_pct_of_started: of the people who started the episode, the share who watched it through.
- cast: one entry per name in the input's cast list AND one for each name in the input's other_groups. share_pct is the share of first-episode viewers each one brought (the cast member or reason whose content, coverage or following the person engaged with on the way in; the shares sum to 100). The other measures are shares of the people THAT entry brought: live_pct watched live on premiere night; one_sitting_pct finished the episode in one sitting; episode2_pct watched episode 2 within 7 days of its air; posted_pct posted, commented or shared about the show in the window; followed_star_pct engaged with that star's own posts before the premiere (0 for a non-star group); new_to_franchise_pct had watched no episode of any earlier series in the franchise in the past year; ad_click_buy_pct clicked an ad and completed a purchase during the episode's own hour on a second screen.
- The input carries a story block: the order of the draws and who leads the involvement measures and the new-to-the-franchise share. Size your numbers so that order holds; it is the read Crosswalk already has on this premiere.
- origin_franchise: the first-episode viewers who were NOT new to the franchise, split three ways (shares of that franchise-side group, summing to 100): stayed from the lead-in; franchise regulars who came for it on purpose; lapsed fans of a star's earlier franchise series who came back for that star.
- discovery is the FIRST digital touch that put the show in front of each first-episode viewer (partition). A row that aggregates several things (a cast member's own posts, press, the network's social accounts, podcasts) carries a breakdown of the real accounts, outlets or shows behind it, each as a share of the row. When a cast member's posts are a row, the breakdown names each star in the cast list.
- last_touch is the last digital surface before the person pressed play (partition). assists are the surfaces in between (overlap). where_watched is the surface the episode played on (partition). when_after_live splits the people who did NOT watch live by when they did (partition of that group). second_screen rows are shares of all first-episode viewers who did each thing during the episode's hour (overlap); do not include an ad-purchase row, the code derives it.
- who: the shape of the first-episode audience, in plain words.

NUMBER RULES
- Shares are messy: two decimals, never ending in 0 or 5, no two shares equal anywhere in the output, no share above 96.
- A partition's rows sum between 98 and 102 before rounding.
- A breakdown row's shares are shares of its row and may overlap.
- Notes are one plain phrase, concrete to this show and its people, never generic.

OUTPUT (strict JSON, every key present):
{
 "title": str, "network": str, "streaming_home": str, "franchise": str (e.g. "NCIS"), "premiere_date": "YYYY-MM-DD", "episode_title": str,
 "air_slot": str (e.g. "Tuesday 9pm ET"), "lead_in": str, "episode2_title": str, "episode2_date": "YYYY-MM-DD",
 "cover_title": str (one sentence, at most 12 words, the single biggest finding),
 "universe": {"live_same_day_viewers": int, "digital_live_share_pct": float, "catch_up_pct_of_live": float, "streaming_home_us_reach": int, "basis": str (one sentence with the numbers in it)},
 "spine": {"heard_share_of_us_pct": float, "looked_up_pct_of_heard": float, "finished_pct_of_started": float},
 "fork": {"met_nudge_pct_of_stopped": float (of the people who stopped before the end, the share who then met a clip, a cast post, a recap or the continue-watching row), "came_back_pct_of_nudged": float (of those, the share who came back to the episode), "finished_pct_of_returned": float (of those who came back, the share who watched it through)},
 "cast": [{"name": str, "share_pct": float, "live_pct": float, "one_sitting_pct": float, "episode2_pct": float, "posted_pct": float, "followed_star_pct": float, "new_to_franchise_pct": float, "ad_click_buy_pct": float, "fan_base": str (one plain sentence: who these people are and where they came from), "evidence": str (one plain sentence on the public signal behind the read)}],
 "origin_franchise": [{"label": str, "pct": float, "doing": str}] (exactly three rows, in the order: stayed from the lead-in; came for it on purpose; came back for a star from an earlier franchise series),
 "discovery": [{"label": str, "pct": float, "doing": str, "breakdown": [{"label": str, "pct": float, "doing": str}]}] (7 to 9 rows, partition),
 "last_touch": [{"label": str, "pct": float, "doing": str}] (6 to 8 rows, partition),
 "assists": [{"label": str, "pct": float, "doing": str}] (6 to 8 rows, overlap),
 "where_watched": [{"label": str, "pct": float, "doing": str}] (5 to 7 rows, partition),
 "when_after_live": [{"label": one of ["Later that night, on demand","The next day","Days 2 to 4","Days 5 to 7"], "pct": float, "doing": str}] (all four, partition of the people who did not watch live),
 "second_screen": [{"label": str, "pct": float, "doing": str}] (4 rows, in this order: had a second screen open during the episode; searched the show or the cast during the episode; posted or commented during the episode; clicked an ad during the episode),
 "who": {"female_pct": float, "age_lead": str (e.g. "45 to 64"), "age_lead_pct": float, "ethnicity_lead": str, "income_lead": str, "read": str (two sentences, plain; if it names one star's following it names every star's)},
 "facts": [str] (5 one-sentence facts with a number each),
 "anchors_note": str (internal, the public anchors used)
}
Plain words throughout, no em dashes, no internal vocabulary (no modeled, estimated, panelists, cohort, benchmark, survey, research, ratings)."""

COMPACT_RULE = "\nKeep every string short. Notes under 100 characters. No prose outside the JSON."


def _story_block(inputs: dict) -> dict:
    cast = inputs.get('cast') or []
    names = [c['name'] for c in cast]
    return {
        'draw_order': names,
        'draw_note': ('The first name is the largest draw by a narrow margin over the second; together the first two bring '
                      'most of the first-episode audience; the third is a distant third.' if len(names) >= 3 else ''),
        'involvement_leader': (inputs.get('involvement_order') or names)[0] if names else '',
        'involvement_order': inputs.get('involvement_order') or names,
        'new_fans_order': inputs.get('new_fans_order') or inputs.get('involvement_order') or names,
        'always_above': inputs.get('always_above') or [],
    }


def _research(inputs: dict, claude_json: Callable, tools=None) -> dict:
    start, end = _dates(inputs)
    user = json.dumps({
        'subject': inputs['subject'], 'network': inputs.get('network') or '', 'streaming_home': inputs.get('platform') or '',
        'premiere_date': inputs.get('premiere_date') or '', 'window': {'start': start, 'end': end},
        'cast': [{'name': c['name'], 'role': c.get('role') or '', 'came_from': c.get('came_from') or ''} for c in (inputs.get('cast') or [])],
        'other_groups': [g['name'] for g in _groups(inputs)],
        'story': _story_block(inputs), 'known_anchors': inputs.get('known_anchors') or {}, 'notes': inputs.get('notes') or '',
    })
    prim = claude_json(RESEARCH_SYSTEM_PROMPT + COMPACT_RULE, user, max_tokens=20000, temperature=0.5,
                       surface='journey_premiere', tools=tools)
    if not isinstance(prim, dict) or not prim.get('universe') or not prim.get('cast'):
        print('[premiere] research returned nothing usable; one compact re-ask')
        prim = claude_json(RESEARCH_SYSTEM_PROMPT + COMPACT_RULE + "\nAnswer in under 9000 tokens.", user,
                           max_tokens=20000, temperature=0.35, surface='journey_premiere_retry', tools=tools)
    if not isinstance(prim, dict) or not prim.get('universe') or not prim.get('cast'):
        raise RuntimeError('premiere research returned no primitives')
    return prim


# ------------------------------------------------------------------ shaping
def _groups(inputs: dict) -> list:
    out = []
    franchise = str(inputs.get('franchise') or '').strip() or str(inputs.get('subject') or '').split(':')[0].strip()
    night = str(inputs.get('night') or 'Tuesday').strip()
    for g in (inputs.get('other_groups') or _DEFAULT_GROUPS):
        out.append({'id': g['id'], 'name': _clean(str(g['name']).format(franchise=franchise, night=night)),
                    'came_from': _clean(g.get('came_from') or '')})
    return out


def _f(v, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x else default


def _gap(seed, lo: float, hi: float) -> float:
    return round(lo + (_h(seed, 'gap') % 1001) / 1000.0 * (hi - lo), 2)


def _hold_order(vals: dict, order: list, gaps: tuple, band: tuple, seed) -> dict:
    """Hold vals[order[0]] > vals[order[1]] > ... with a salted gap between
    each pair (gaps[i] is the (lo, hi) band for the gap under order[i]),
    inside band. A value already in order and inside the gap band keeps
    the research number; one out of order, or further below than the band
    allows, moves to the salted gap under the one above it."""
    lo, hi = band
    out = dict(vals)
    if not order:
        return out
    top = order[0]
    out[top] = min(hi, max(lo, out.get(top, (lo + hi) / 2)))
    for i in range(1, len(order)):
        a, b = order[i - 1], order[i]
        g_lo, g_hi = gaps[min(i - 1, len(gaps) - 1)]
        g = _gap((seed, a, b), g_lo, g_hi)
        cur = out.get(b, out[a] - g)
        if cur > out[a] - g_lo or cur < out[a] - g_hi:
            # out of order, or further below than the band allows ("just
            # slightly below" is a band, not a floor): the salted gap
            cur = out[a] - g
        out[b] = max(lo, cur)
    # a floor hit lower down pushes the ones above it up by the gap
    for i in range(len(order) - 1, 0, -1):
        a, b = order[i - 1], order[i]
        g_lo, _ = gaps[min(i - 1, len(gaps) - 1)]
        if out[a] - out[b] < g_lo:
            out[a] = min(hi, out[b] + _gap((seed, a, b, 'up'), g_lo, g_lo + 1.5))
    return out


def _messy_pct(seed, v: float, lo: float, hi: float) -> float:
    """Two live decimals, never ending in 0 or 5, inside [lo, hi]."""
    x = round(float(v) + ((_h(seed, 'p') % 181) - 90) / 1000.0, 2)
    x = min(hi, max(lo, x))
    tries = 0
    while int(round(x * 100)) % 10 in (0, 5) and tries < 8:
        x = round(x + 0.01 + (0.01 * (_h(seed, tries) % 3)), 2)
        x = min(hi, x)
        tries += 1
    return x


def shape_cast(inputs: dict, prim_cast: list, seed) -> list:
    """The one model every table derives from: each star and group with a
    draw share and its measures, holding the orders the inputs set."""
    stars = [dict(c) for c in (inputs.get('cast') or [])]
    groups = _groups(inputs)
    names = [s['name'] for s in stars]
    by_name = {}
    for c in (prim_cast or []):
        if isinstance(c, dict) and c.get('name'):
            by_name[_norm(c['name'])] = c
    def got(name, key, default):
        return _f((by_name.get(_norm(name)) or {}).get(key), default)

    # draw shares: the stars in draw order with the salted gaps, capped so the
    # non-star groups keep their room, then the two groups split the rest
    draws = {n: got(n, 'share_pct', 30.0 - 10.0 * i) for i, n in enumerate(names)}
    draws = _hold_order(draws, names[:2], _GAPS['draw'][:1], (3.0, 60.0), (seed, 'draw'))
    if len(names) >= 3:
        lo_r, hi_r = _DRAW_THIRD_RATIO
        second = draws[names[1]]
        third = draws.get(names[2], second * (lo_r + hi_r) / 2)
        if not (second * lo_r <= third <= second * hi_r):
            third = second * (lo_r + (_h(seed, 'third') % 1001) / 1000.0 * (hi_r - lo_r))
        draws[names[2]] = third
        prev = third
        for n in names[3:]:
            v = draws.get(n, prev * 0.6)
            draws[n] = min(v, prev * 0.85)
            prev = draws[n]
    tot = sum(draws.values())
    if tot > _STAR_SUM_CAP or tot < _STAR_SUM_FLOOR:
        target = _STAR_SUM_CAP if tot > _STAR_SUM_CAP else _STAR_SUM_FLOOR
        target = target - 0.5 + (_h(seed, 'starsum') % 1001) / 1000.0   # a salted point inside the band's edge
        draws = {n: v * target / tot for n, v in draws.items()}
    rest = 100.0 - sum(draws.values())
    g_raw = {g['name']: max(0.5, got(g['name'], 'share_pct', 8.0 if g['id'] == 'franchise' else 4.0)) for g in groups}
    g_tot = sum(g_raw.values()) or 1.0
    g_share = {n: rest * v / g_tot for n, v in g_raw.items()}
    # 'other' never dominates the franchise group
    if len(groups) == 2:
        fr, ot = groups[0]['name'], groups[1]['name']
        if g_share[ot] > g_share[fr] * 0.7:
            g_share[ot] = rest * 0.36
            g_share[fr] = rest - g_share[ot]

    inv_order = [n for n in (inputs.get('involvement_order') or names) if n in names]
    new_order = [n for n in (inputs.get('new_fans_order') or inv_order) if n in names]
    measures = {}
    for mid in _INVOLVEMENT + ('new_fan',):
        key = {'new_fan': 'new_to_franchise_pct', 'ad_buy': 'ad_click_buy_pct'}.get(mid, f'{mid}_pct')
        vals = {n: got(n, key, sum(_BANDS[mid]) / 2.0) for n in names}
        order = new_order if mid == 'new_fan' else inv_order
        vals = _hold_order(vals, order, _GAPS[mid], _BANDS[mid], (seed, mid))
        measures[mid] = {n: _messy_pct((seed, mid, n), v, *_BANDS[mid]) for n, v in vals.items()}
        # the pairs the inputs insist on (name above name), on every measure
        for above, below in (inputs.get('always_above') or []):
            if above in measures[mid] and below in measures[mid] and measures[mid][above] <= measures[mid][below]:
                measures[mid][above] = _messy_pct((seed, mid, above, 'fix'), measures[mid][below] + _gap((seed, mid, above), 0.8, 2.4),
                                                  _BANDS[mid][0], min(_BANDS[mid][1], 96.0))
        # the stars re-checked after the messy pass: the order must survive it
        for i in range(1, len(order)):
            a, b = order[i - 1], order[i]
            if measures[mid][b] >= measures[mid][a]:
                measures[mid][b] = _messy_pct((seed, mid, b, 'under'), measures[mid][a] - _gap((seed, mid, b), 0.4, 1.2),
                                              _BANDS[mid][0], _BANDS[mid][1])
    out = []
    for i, s in enumerate(stars):
        n = s['name']
        rec = {'id': f'star{i + 1}', 'name': n, 'role': _clean(s.get('role') or ''), 'came_from': _clean(s.get('came_from') or ''),
               'star': True, 'share_pct': round(draws[n], 4), 'fan_base': _clean(got_text(by_name, n, 'fan_base')),
               'evidence': _clean(got_text(by_name, n, 'evidence'))}
        for mid in _INVOLVEMENT + ('new_fan',):
            rec[mid] = measures[mid][n]
        out.append(rec)
    for g in groups:
        n = g['name']
        rec = {'id': g['id'], 'name': n, 'role': '', 'came_from': g['came_from'], 'star': False, 'share_pct': round(g_share[n], 4),
               'fan_base': _clean(got_text(by_name, n, 'fan_base')), 'evidence': ''}
        for mid in _INVOLVEMENT + ('new_fan',):
            key = {'new_fan': 'new_to_franchise_pct', 'ad_buy': 'ad_click_buy_pct'}.get(mid, f'{mid}_pct')
            lo, hi = _BANDS[mid]
            if mid == 'followed_star':
                lo, hi = 0.0, 6.0
            if mid == 'new_fan' and g['id'] == 'franchise':
                lo, hi = 2.0, 12.0
            if mid == 'posted' and not g['id'] == 'franchise':
                hi = min(hi, 20.0)
            v = got(n, key, (lo + hi) / 2.0)
            rec[mid] = _messy_pct((seed, mid, n), v, lo, hi) if hi > 0 else 0.0
        out.append(rec)
    return out


def got_text(by_name: dict, name: str, key: str) -> str:
    return str((by_name.get(_norm(name)) or {}).get(key) or '')


def _norm(s) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


# ------------------------------------------------------------------ build
def build_payload(inputs: dict, prim: dict, *, created_by: str = 'prometheus') -> dict:
    subject = _clean(inputs['subject'])
    network = _clean(inputs.get('network') or prim.get('network') or '')
    platform = _clean(inputs.get('platform') or prim.get('streaming_home') or 'the streaming home')
    franchise = _clean(inputs.get('franchise') or prim.get('franchise') or subject.split(':')[0])
    start, end = _dates(inputs)
    seed = f'{subject}|{platform}|{KIND}'
    premiere = str(inputs.get('premiere_date') or prim.get('premiere_date') or start)
    try:
        pday = _dt.date.fromisoformat(premiere)
        night_label = pday.strftime('%B %-d')
        ep2_default = (pday + _dt.timedelta(days=7)).isoformat()
    except ValueError:
        pday, night_label, ep2_default = None, 'premiere night', ''
    ep1 = _clean(prim.get('episode_title') or 'the first episode')
    ep2_title = _clean(prim.get('episode2_title') or 'episode 2')
    ep2_date = str(prim.get('episode2_date') or ep2_default)

    # ---- the universe: night-of digital viewers plus the week's catch-up
    uni = prim.get('universe') or {}
    lsd = _f(uni.get('live_same_day_viewers'), 0)
    if lsd <= 0:
        raise ValueError('research carried no night-of audience')
    d_live = min(0.30, max(0.12, _f(uni.get('digital_live_share_pct'), 19.0) / 100.0))
    catch = min(0.95, max(0.35, _f(uni.get('catch_up_pct_of_live'), 55.0) / 100.0))
    us_viewers = lsd * (d_live + catch)
    cap = _f(uni.get('streaming_home_us_reach'), 0) or _f((inputs.get('ceilings') or {}).get('home_us_reach'), 0)
    if cap > 0:
        us_viewers = min(us_viewers, cap * 0.92)
    sample = _messy((seed, 'sample'), us_viewers / US_PER_PANELIST)
    v4 = int(round(sample * US_PER_PANELIST))
    while v4 % 10 == 0:
        sample += 1
        v4 = int(round(sample * US_PER_PANELIST))

    sp = prim.get('spine') or {}
    finished = min(0.92, max(0.70, _f(sp.get('finished_pct_of_started'), 82.0) / 100.0))
    v3 = max(v4 + 3, _messy((seed, 'started'), v4 / finished))
    heard = min(0.25, max(0.07, _f(sp.get('heard_share_of_us_pct'), 13.5) / 100.0))
    looked = min(0.60, max(0.22, _f(sp.get('looked_up_pct_of_heard'), 38.0) / 100.0))
    v1 = _messy((seed, 'heard'), US_GEN_POP * heard)
    v2 = _messy((seed, 'looked'), v1 * looked)
    if v2 <= v3 * 1.15:
        v2 = _messy((seed, 'looked', 'lift'), v3 * 1.15 / looked * looked + v3 * 0.35)
        v2 = max(v2, int(v3 * 1.18))
    if v1 <= v2 * 1.3:
        v1 = _messy((seed, 'heard', 'lift'), v2 * 1.6)
    assert v1 > v2 > v3 > v4 > 0, (v1, v2, v3, v4)

    # ---- the cast model and every count that hangs off it
    cast = shape_cast(inputs, prim.get('cast') or [], seed)
    draw_rows = _rows_partition((seed, 'draw'), v4, [{'label': c['name'], 'pct': c['share_pct'], 'note': c['came_from']} for c in cast])
    draw_by = {r['label']: r['accounts'] for r in draw_rows}
    for c in cast:
        c['accounts'] = int(draw_by[c['name']])
        c['share_pct'] = round(c['accounts'] / v4 * 100, 4)
        c['m'] = {'draw': {'accounts': c['accounts'], 'pct': c['share_pct']}}
        for mid in _INVOLVEMENT + ('new_fan',):
            n = min(c['accounts'] - 1, _messy((seed, mid, c['name'], 'n'), c['accounts'] * c[mid] / 100.0))
            c['m'][mid] = {'accounts': int(n), 'pct': round(n / c['accounts'] * 100, 4)}
    stars = [c for c in cast if c['star']]
    # the pairs the inputs insist on hold in COUNT too (a bigger draw can
    # otherwise outnumber a higher share)
    for above, below in (inputs.get('always_above') or []):
        a = next((c for c in cast if c['name'] == above), None)
        b = next((c for c in cast if c['name'] == below), None)
        if a and b:
            for mid in _INVOLVEMENT + ('new_fan',):
                if a['m'][mid]['accounts'] <= b['m'][mid]['accounts']:
                    n = min(a['accounts'] - 1, b['m'][mid]['accounts'] + 1 + (_h(seed, mid, 'cnt') % 2500))
                    a['m'][mid] = {'accounts': int(n), 'pct': round(n / a['accounts'] * 100, 4)}
    new_leader = (inputs.get('new_fans_order') or [None])[0]
    if new_leader:
        lead = next((c for c in cast if c['name'] == new_leader), None)
        if lead:
            top_other = max((c['m']['new_fan']['accounts'] for c in cast if c is not lead), default=0)
            if lead['m']['new_fan']['accounts'] <= top_other:
                n = min(lead['accounts'] - 1, int(top_other * (1.12 + (_h(seed, 'newlead') % 900) / 10000.0)))
                lead['m']['new_fan'] = {'accounts': int(n), 'pct': round(n / lead['accounts'] * 100, 4)}
    # a summed row never lands on a round count: a few people move onto the
    # involvement leader's line (which only strengthens the orders held above)
    bump = next((c for c in stars if c['name'] == (inputs.get('involvement_order') or [None])[0]), stars[0] if stars else cast[0])
    for mid in _INVOLVEMENT + ('new_fan',):
        tries = 0
        while sum(c['m'][mid]['accounts'] for c in cast) % 10 == 0 and tries < 12:
            n = min(bump['accounts'] - 1, bump['m'][mid]['accounts'] + 1 + (_h(seed, mid, 'bump', tries) % 3))
            bump['m'][mid] = {'accounts': int(n), 'pct': round(n / bump['accounts'] * 100, 4)}
            tries += 1
    totals = {mid: sum(c['m'][mid]['accounts'] for c in cast) for mid in _INVOLVEMENT + ('new_fan',)}
    for mid in totals:
        assert 0 < totals[mid] < v4 and totals[mid] % 10, (mid, totals[mid], v4)

    # ---- the fork off "started": finished in one sitting, or stopped and came back
    paid = v4
    paid_first = totals['one_sitting']
    paid_return = paid - paid_first
    fk = prim.get('fork') or {}
    met = min(0.95, max(0.50, _f(fk.get('met_nudge_pct_of_stopped'), 68.0) / 100.0))
    back = min(0.92, max(0.45, _f(fk.get('came_back_pct_of_nudged'), 74.0) / 100.0))
    fin = min(0.97, max(0.60, _f(fk.get('finished_pct_of_returned'), 86.0) / 100.0))
    # The stopped branch comes from the finish rate (started minus the
    # one-sitting finishers). The return chain inside it is walked up from
    # the people it must carry (finished after coming back <- came back <-
    # met a nudge <- stopped), each leg a real share of the one above it, so
    # no branch reads as 100% kept. When the research's leg rates cannot fit
    # the chain inside the stopped branch, the two middle legs meet in the
    # middle; the started step grows only when even that cannot hold them.
    returned = max(paid_return + 1, _messy((seed, 'returned'), paid_return / fin))
    abandoned = v3 - paid_first
    if abandoned < returned * 1.25:
        abandoned = _messy((seed, 'abandoned', 'grow'), returned * 1.25)
        v3 = paid_first + abandoned
        while v3 % 10 == 0:
            v3 += 1
            abandoned += 1
    ideal = _messy((seed, 'retargeted'), returned / back)
    if ideal >= abandoned * 0.97:
        ideal = _messy((seed, 'retargeted', 'mid'), (returned * abandoned) ** 0.5)
    retargeted = min(abandoned - 1, max(returned + 1, ideal))
    penult = v3
    if v2 <= v3 * 1.12:
        v2 = _messy((seed, 'looked', 'refit'), v3 * 1.18)
        if v1 <= v2 * 1.3:
            v1 = _messy((seed, 'heard', 'refit'), v2 * 1.6)
    assert paid_first + paid_return == paid and 0 < paid_return < returned < retargeted < abandoned < penult < v2 < v1, (
        paid_first, paid_return, returned, retargeted, abandoned, penult, v2, v1)

    spine = [
        {'id': 'tam', 'label': 'US population', 'doing': 'US population. The reach this file is read against', 'where': 'United States',
         'accounts': US_GEN_POP, 'kept': 100.0, 'dropped': 0, 'ofUs': 100.0, 'next': f'Heard about {subject}', 'surface': '', 'job': '', 'timing': ''},
        {'id': 'heard', 'label': f'Heard about {subject} before or during premiere week',
         'doing': 'A promo inside a stream, a cast post, an article, the trailer, a group chat, the streaming home\'s own screens',
         'where': 'Social, press, YouTube, search, the streaming home', 'accounts': v1, 'kept': round(v1 / US_GEN_POP * 100, 4),
         'dropped': US_GEN_POP - v1, 'ofUs': round(v1 / US_GEN_POP * 100, 4), 'next': 'Looked it up', 'surface': 'promo, social, press', 'job': 'hear', 'timing': 'before the premiere'},
        {'id': 'looked', 'label': 'Looked it up', 'doing': 'Searched the title, watched the trailer, opened its page, read an article, opened a cast post',
         'where': 'Search, YouTube, the show page, press', 'accounts': v2, 'kept': round(v2 / v1 * 100, 4), 'dropped': v1 - v2,
         'ofUs': round(v2 / US_GEN_POP * 100, 4), 'next': 'Started the first episode', 'surface': 'search, YouTube, show page', 'job': 'look', 'timing': 'premiere week'},
        {'id': 'started', 'label': f'Started the first episode within 7 days of {night_label}',
         'doing': f'Pressed play on {ep1} on {platform}, the {network or "network"} app or a live TV stream', 'where': platform,
         'accounts': v3, 'kept': round(v3 / v2 * 100, 4), 'dropped': v2 - v3, 'ofUs': round(v3 / US_GEN_POP * 100, 4),
         'next': 'Watched it through', 'surface': platform, 'job': 'start', 'timing': 'within 7 days'},
        {'id': 'watched', 'label': 'Watched the first episode through to the end',
         'doing': f'Finished {ep1} inside the premiere week, in one sitting or after coming back', 'where': platform,
         'accounts': v4, 'kept': round(v4 / v3 * 100, 4), 'dropped': v3 - v4, 'ofUs': round(v4 / US_GEN_POP * 100, 4),
         'next': '', 'surface': platform, 'job': 'watch', 'timing': 'within 7 days'},
    ]
    fork = [
        {'id': 'abandoned', 'label': 'Stopped before the end of that sitting', 'doing': 'Left the episode running or closed it before the end',
         'accounts': abandoned, 'kept': round(abandoned / penult * 100, 4), 'dropped': penult - abandoned, 'surface': platform, 'timing': 'first sitting'},
        {'id': 'retargeted', 'label': 'Met a clip, a cast post, a recap or the continue-watching row',
         'doing': 'The streaming home\'s row, YouTube clips, the cast\'s posts, a recap in the feed', 'accounts': retargeted,
         'kept': round(retargeted / abandoned * 100, 4), 'dropped': abandoned - retargeted, 'surface': 'social, YouTube, the streaming home', 'timing': 'the next days'},
        {'id': 'returned', 'label': 'Came back to the episode', 'doing': f'Reopened {ep1} on {platform} or the {network or "network"} app',
         'accounts': returned, 'kept': round(returned / retargeted * 100, 4), 'dropped': retargeted - returned, 'surface': platform, 'timing': 'days 1 to 7'},
        {'id': 'paid_return', 'label': 'Finished it after coming back', 'doing': 'Watched it through inside the premiere week',
         'accounts': paid_return, 'kept': round(paid_return / returned * 100, 4), 'dropped': returned - paid_return, 'surface': platform, 'timing': 'within 7 days'},
        {'id': 'paid_first', 'label': 'Finished it in one sitting', 'doing': 'Watched it through the first time they pressed play',
         'accounts': paid_first, 'kept': round(paid_first / penult * 100, 4), 'dropped': abandoned, 'surface': platform, 'timing': 'first sitting'},
    ]

    # ---- the tables
    detours = []
    # where they came from: the franchise side split three ways, the newcomers by who brought them
    new_total = totals['new_fan']
    franchise_side = v4 - new_total
    of = [dict(r) for r in (prim.get('origin_franchise') or []) if isinstance(r, dict) and r.get('label')][:3]
    if len(of) < 3:
        of = [{'label': f'Stayed from {_clean(prim.get("lead_in") or "the lead-in")}', 'pct': 48.3, 'doing': 'Watched the lead-in and kept the stream running'},
              {'label': f'{franchise} regulars who came for it on purpose', 'pct': 33.9, 'doing': 'Watch the franchise most weeks and tuned in for the new one'},
              {'label': 'Came back for a star from an earlier series', 'pct': 17.8, 'doing': 'Lapsed from the current shows, returned for a familiar face'}]
    # the lapsed-fan row cannot outnumber the franchise-side people the first star brought
    fr_rows = _rows_partition((seed, 'origin_fr'), franchise_side, [{'label': r['label'], 'pct': r.get('pct'), 'note': r.get('doing') or r.get('note')} for r in of])
    star1 = stars[0] if stars else None
    if star1:
        lapsed_cap = int((star1['accounts'] - star1['m']['new_fan']['accounts']) * 0.8)
        if fr_rows[2]['accounts'] > lapsed_cap:
            diff = fr_rows[2]['accounts'] - lapsed_cap
            fr_rows[2]['accounts'] = lapsed_cap
            fr_rows[0]['accounts'] += diff
            while fr_rows[2]['accounts'] % 10 == 0 or fr_rows[0]['accounts'] % 10 == 0:
                fr_rows[2]['accounts'] -= 1; fr_rows[0]['accounts'] += 1
            for r in fr_rows:
                r['pct'] = round(r['accounts'] / franchise_side * 100, 4)
    new_bd = sorted(({'label': c['name'], 'accounts': c['m']['new_fan']['accounts'], 'doing': c['fan_base'] or c['came_from']} for c in cast),
                    key=lambda r: -r['accounts'])
    for b in new_bd:
        b['pct'] = round(b['accounts'] / new_total * 100, 4)
    star_names = [c['name'] for c in stars]
    origin_rows = [dict(r) for r in fr_rows] + [{'label': f'New to the franchise: no episode of an earlier {franchise} series in the past year',
                                               'accounts': new_total, 'pct': 0.0, 'breakdown': new_bd,
                                               'doing': 'By who brought them: ' + ', '.join(f'{b["label"]} {b["pct"]:.1f}%' for b in new_bd if b['label'] in star_names)}]
    for r in origin_rows:
        r['pct'] = round(r['accounts'] / v4 * 100, 4)
    assert sum(r['accounts'] for r in origin_rows) == v4
    detours.append({'title': 'Where they came from', 'kind': 'partition',
                    'note': 'Rows sum to every first-episode viewer. The newcomers are split by who brought them.', 'rows': origin_rows})

    # how they found out: the first digital touch, with the real things behind each aggregate row
    disc_in = [{'label': r['label'], 'pct': r.get('pct'), 'note': r.get('doing')}
               for r in (prim.get('discovery') or []) if isinstance(r, dict) and r.get('label')]
    if not disc_in:
        disc_in = [{'label': f'A promo inside another {network or "network"} stream', 'pct': 31.4, 'note': 'The lead-in and the rest of the week'},
                   {'label': f'{platform}\'s own screens', 'pct': 17.8, 'note': 'The hero row and the new-this-week rail'},
                   {'label': 'A cast member\'s own post', 'pct': 14.6, 'note': 'Instagram, X, TikTok'}, {'label': 'An article', 'pct': 11.3, 'note': 'Casting and premiere coverage'},
                   {'label': 'The trailer on YouTube', 'pct': 9.7, 'note': 'The first look and the trailer'}, {'label': 'A text or group chat', 'pct': 8.1, 'note': 'Word of mouth'},
                   {'label': 'Search', 'pct': 7.1, 'note': 'Typed the title or a star'}]
    disc = _rows_partition((seed, 'discovery'), v4, disc_in)
    disc_src = {_norm(r['label']): r for r in (prim.get('discovery') or []) if isinstance(r, dict) and r.get('label')}
    for r in disc:
        src = disc_src.get(_norm(r['label'])) or {}
        bd = _breakdown((seed, 'disc', r['label']), r['accounts'], src.get('breakdown'), inputs)
        if bd:
            r['breakdown'] = bd
    detours.append({'title': 'How they found out: the first touch', 'kind': 'partition',
                    'note': 'The first digital surface that put the show in front of each viewer. Rows sum to every first-episode viewer.', 'rows': disc})

    # who brought them: the partition, each row carrying its measures as counts
    who_rows = []
    for c in cast:
        kids = [{'label': _measure_label(m, night_label, ep2_title).lower(), 'accounts': c['m'][m['id']]['accounts'],
                 'pct': c['m'][m['id']]['pct'], 'doing': ''} for m in MEASURES if m['id'] != 'draw']
        who_rows.append({'label': c['name'] + (f" as {c['role']}" if c.get('role') else ''), 'accounts': c['accounts'], 'pct': c['share_pct'],
                         'doing': c['fan_base'] or c['came_from'], 'breakdown': kids})
    detours.append({'title': 'Who brought them to the first episode', 'kind': 'partition',
                    'note': 'The cast member or reason each viewer engaged with on the way in. Rows sum to every first-episode viewer; '
                            'the lines under each name are the same measures for the people that name brought.', 'rows': who_rows})

    # when they watched: live is the sum of the groups' live counts
    live_total = totals['live']
    not_live = v4 - live_total
    wal = _rows_partition((seed, 'when'), not_live, [{'label': r['label'], 'pct': r.get('pct'), 'note': r.get('doing')}
                                                    for r in (prim.get('when_after_live') or []) if isinstance(r, dict) and r.get('label')]
                          or [{'label': 'Later that night, on demand', 'pct': 22.4}, {'label': 'The next day', 'pct': 38.7},
                              {'label': 'Days 2 to 4', 'pct': 26.1}, {'label': 'Days 5 to 7', 'pct': 12.8}],
                          order=('Later that night, on demand', 'The next day', 'Days 2 to 4', 'Days 5 to 7'))
    when_rows = [{'label': f'Live on {night_label}', 'accounts': live_total, 'doing': f'The live stream at {_clean(prim.get("air_slot") or "air time")}', 'pct': 0.0}] + wal
    for r in when_rows:
        r['pct'] = round(r['accounts'] / v4 * 100, 4)
    detours.append({'title': 'When they watched it', 'kind': 'partition', 'note': 'Rows sum to every first-episode viewer.', 'rows': when_rows})

    # where it played
    where = _rows_partition((seed, 'where'), v4, [{'label': r['label'], 'pct': r.get('pct'), 'note': r.get('doing')}
                                                 for r in (prim.get('where_watched') or []) if isinstance(r, dict) and r.get('label')]
                            or [{'label': f'{platform}, on demand', 'pct': 44.1}, {'label': f'{platform}, the live feed', 'pct': 17.3},
                                {'label': f'The {network or "network"} app or site', 'pct': 12.7}, {'label': 'YouTube TV', 'pct': 11.9},
                                {'label': 'Hulu + Live TV', 'pct': 7.6}, {'label': 'Other live TV streamers', 'pct': 6.4}])
    detours.append({'title': 'Where it played', 'kind': 'partition', 'note': 'Rows sum to every first-episode viewer.', 'rows': where})

    # last touch + assists
    lt = _rows_partition((seed, 'last'), v4, [{'label': r['label'], 'pct': r.get('pct'), 'note': r.get('doing')}
                                             for r in (prim.get('last_touch') or []) if isinstance(r, dict) and r.get('label')]
                         or [{'label': 'The streaming home\'s own screens', 'pct': 31.2}, {'label': 'The lead-in\'s end card', 'pct': 27.9},
                             {'label': 'A cast post', 'pct': 14.3}, {'label': 'Search', 'pct': 11.8}, {'label': 'A text or group chat', 'pct': 8.7},
                             {'label': 'An article', 'pct': 6.1}])
    detours.append({'title': 'Last touch before they pressed play', 'kind': 'partition', 'note': 'Rows sum to every first-episode viewer.', 'rows': lt})
    assists = []
    for r in (prim.get('assists') or [])[:8]:
        if not isinstance(r, dict) or not r.get('label'):
            continue
        pct = min(88.0, max(0.4, _f(r.get('pct'), 5.0)))
        n = min(v4 - 1, _messy((seed, 'assist', r['label']), v4 * pct / 100.0))
        assists.append({'label': _clean(r['label']), 'accounts': n, 'pct': round(n / v4 * 100, 4), 'doing': _clean(r.get('doing') or '')})
    assists.sort(key=lambda r: -r['accounts'])
    if assists:
        detours.append({'title': 'What else was in the mix on the way in', 'kind': 'overlap', 'base': v4,
                        'note': 'Overlap allowed: one viewer can have several. Rows do not sum.', 'rows': assists})

    # the second screen during the episode: the purchase row is the groups' sum
    ad_buy_total = totals['ad_buy']
    ss_src = [r for r in (prim.get('second_screen') or []) if isinstance(r, dict) and r.get('label')][:4]
    ss_default = [('Had a second screen open during the episode', 58.3), ('Searched the show or the cast during the episode', 21.7),
                  ('Posted or commented during the episode', 9.4), ('Clicked an ad during the episode', 13.1)]
    ss_rows = []
    for i, (lab, dflt) in enumerate(ss_default):
        src = ss_src[i] if i < len(ss_src) else {}
        pct = min(90.0, max(0.5, _f(src.get('pct'), dflt)))
        n = min(v4 - 1, _messy((seed, 'ss', lab), v4 * pct / 100.0))
        ss_rows.append({'label': _clean(src.get('label') or lab), 'accounts': n, 'doing': _clean(src.get('doing') or '')})
    click = ss_rows[3]
    floor_click = int(ad_buy_total * (1.9 + (_h(seed, 'click') % 700) / 1000.0))
    if click['accounts'] < floor_click:
        click['accounts'] = min(v4 - 1, _messy((seed, 'click', 'lift'), floor_click))
    if ss_rows[0]['accounts'] <= max(r['accounts'] for r in ss_rows[1:]):
        ss_rows[0]['accounts'] = min(v4 - 1, _messy((seed, 'open', 'lift'), max(r['accounts'] for r in ss_rows[1:]) * 1.9))
    ad_bd = sorted(({'label': c['name'], 'accounts': c['m']['ad_buy']['accounts'], 'doing': ''} for c in cast), key=lambda r: -r['accounts'])
    for b in ad_bd:
        b['pct'] = round(b['accounts'] / ad_buy_total * 100, 4)
    ss_rows.append({'label': 'Clicked an ad and bought something during the episode', 'accounts': ad_buy_total, 'breakdown': ad_bd,
                    'doing': 'By who brought the buyer: ' + ', '.join(f'{b["label"]} {b["pct"]:.1f}%' for b in ad_bd if b['label'] in star_names)})
    for r in ss_rows:
        r['pct'] = round(r['accounts'] / v4 * 100, 4)
    detours.append({'title': 'The second screen during the episode', 'kind': 'overlap', 'base': v4,
                    'note': 'Share of first-episode viewers who did this during the episode\'s own hour. Rows do not sum. '
                            'The purchase row is split by who brought the buyer.', 'rows': ss_rows})

    # came back for episode 2
    e2 = totals['episode2']
    e2_rows = [{'label': f'Watched {ep2_title} within 7 days of {_fmt_day(ep2_date)}', 'accounts': e2, 'doing': 'Came back for the second episode'},
               {'label': 'Had not watched episode 2 yet', 'accounts': v4 - e2, 'doing': 'Inside the window, no second episode'}]
    for r in e2_rows:
        r['pct'] = round(r['accounts'] / v4 * 100, 4)
    detours.append({'title': 'Did they come back for episode 2', 'kind': 'partition', 'note': 'Rows sum to every first-episode viewer.', 'rows': e2_rows})

    # ---- the cast comparison block (the same measures, every star)
    all_m = {'draw': {'accounts': v4, 'pct': 100.0}}
    for mid in _INVOLVEMENT + ('new_fan',):
        all_m[mid] = {'accounts': totals[mid], 'pct': round(totals[mid] / v4 * 100, 4)}
    leaders = {}
    for m in MEASURES:
        key = 'accounts' if m['kind'] == 'draw' else 'pct'
        leaders[m['id']] = max(stars, key=lambda c: c['m'][m['id']][key])['name'] if stars else ''
    cast_block = {
        'measures': [dict(m, label=_measure_label(m, night_label, ep2_title)) for m in MEASURES],
        'stars': [{'name': c['name'], 'role': c['role'], 'came_from': c['came_from'], 'fan_base': c['fan_base'], 'evidence': c['evidence'],
                   'accounts': c['accounts'], 'share_pct': c['share_pct'], 'm': c['m']} for c in stars],
        'groups': [{'name': c['name'], 'accounts': c['accounts'], 'share_pct': c['share_pct'], 'm': c['m']} for c in cast if not c['star']],
        'all': all_m, 'leaders': leaders,
        'note': 'Each line is the share of the people that star brought to the first episode who did the thing; '
                '"all viewers" is the same measure over everyone who watched it through.',
    }

    # ---- copy, kpis, who, meta
    window = f'{start} to {end}'
    s1, s2 = (stars + [None, None])[:2]
    two_share = round((s1['accounts'] + s2['accounts']) / v4 * 100, 1) if s1 and s2 else 0.0
    inv_lead = next((c for c in stars if c['name'] == (inputs.get('involvement_order') or [None])[0]), s2 or s1)
    new_lead = max(stars, key=lambda c: c['m']['new_fan']['accounts']) if stars else None
    who = dict(prim.get('who') or {})
    # the header tiles name every star or none (Jenna 2026-10-19: "take
    # jennifer beals out of the header if you don't list all 3 marquee cast
    # members"); the star-by-star numbers live in the cast card below
    star_sum = sum(c['accounts'] for c in stars)
    kpis = [{'l': 'Watched the first episode through', 'v': f'{v4:,}', 'hot': True}]
    if stars:
        kpis.append({'l': 'Brought by ' + _list_names([c['name'] for c in stars], 'or'), 'v': f'{star_sum / v4 * 100:.1f}%'})
    kpis.append({'l': 'New to the franchise', 'v': f'{new_total / v4 * 100:.1f}%'})
    kpis.append({'l': 'Came back for episode 2', 'v': f'{e2 / v4 * 100:.1f}%'})
    kpis.append({'l': 'Clicked an ad and bought during the episode', 'v': f'{ad_buy_total / v4 * 100:.1f}%'})
    kpis.append({'l': 'Finished it in one sitting', 'v': f'{paid_first / penult * 100:.1f}%'})
    shares_line = ', '.join(f'{c["name"]} {c["share_pct"]:.1f}%' for c in stars)
    copy = {
        'eyebrow': f'Premiere journey. {_window_label(start, end)}',
        'titleHtml': _clean(inputs.get('cover_title') or subject).rstrip('.') + '.',   # the subject, never a finding that names one star
        'deck': f'The people who watched the first episode of {subject} through to the end on the Crosswalk clickstream panel of 10M US consumers, '
                f'projected to the United States. Where they came from, how they found out, and who brought them. Every count is a person.',
        'lead': _clean(who.get('read') or ''),
        'kpis': kpis,
        'spineSec': '01', 'spineHead': f'{v1:,} people heard about it; {v4:,} watched the first episode through.',
        'forkSec': '02', 'forkHead': 'One sitting, or stopped and came back', 'forkSub': 'Who finished the first time they pressed play and who came back to finish',
        'forkBranchA': 'FINISHED IT IN ONE SITTING', 'forkBranchB': 'Stopped before the end, and what happened next',
        'forkSameSessionLabel': 'Finished in one sitting', 'forkTotalLabel': 'First-episode viewers', 'forkTotalVerb': 'who watched it through',
        'forkRead': f'{paid_first / penult * 100:.1f}% of the people who started the episode finished it in one sitting; '
                    f'{paid_return:,} more stopped, met a clip, a cast post or the continue-watching row, and came back to finish inside the week.',
        'castSec': '03', 'castHead': (f'The leads brought {star_sum / v4 * 100:.1f}% of the first-episode audience: {shares_line}.'
                                       if stars else 'The stars, the same measures.'),
        'castSub': 'The same measures for every star. The top value on each line is marked',
        'detourSec': '04', 'detourHead': 'Where they came from, how they found out, and what they did during and after the episode',
        'tldr': [], 'reads': [], 'recommendations': [],
    }
    proj_name = f'{subject} premiere, where the first-episode audience came from'
    blob = {
        'inputs': _inputs_for_meta(inputs),
        'meta': {'window': window, 'unit': 'unique US people', 'usGenPop': US_GEN_POP, 'kind': KIND, 'network': network, 'streaming_home': platform,
                 'franchise': franchise, 'premiere_date': premiere, 'episode_title': ep1, 'episode2_title': ep2_title, 'episode2_date': ep2_date,
                 'air_slot': _clean(prim.get('air_slot') or ''), 'lead_in': _clean(prim.get('lead_in') or ''), 'panel_sample': sample},
        'spine': spine, 'fork': fork, 'detours': detours, 'cast_drivers': cast_block, 'copy': copy,
        'who_they_are': {k: (_clean(v) if isinstance(v, str) else v) for k, v in who.items()},
    }
    hold_all_or_none(blob, [c['name'] for c in stars])
    now = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = {
        'meta': {
            'story_mode': 'fragrance_shop_journey', 'target_name': f'{subject} on {platform}', 'target_display': proj_name, 'project_name': proj_name,
            'target': f'{subject} on {platform}', 'customer_brand': subject, 'start_date': start, 'end_date': end,
            'window_label': _window_label(start, end), 'target_type': 'premiere_journey', 'journey_kind': KIND, 'category': 'tv',
            'network': network, 'streaming_home': platform, 'premiere_date': premiere, 'created_by': created_by, 'created_at': now,
        },
        'fragrance_shop_journey': blob,
        'facts': [_clean(x) for x in (prim.get('facts') or [])][:5],
        'kpis': {'total_users': v4, 'conversion_pct': round(v4 / v1 * 100, 4), 'share_of_us_pct': round(v4 / US_GEN_POP * 100, 4),
                 'one_sitting_pct': round(paid_first / penult * 100, 4), 'live_pct': round(live_total / v4 * 100, 4),
                 'new_to_franchise_pct': round(new_total / v4 * 100, 4), 'episode2_pct': round(e2 / v4 * 100, 4)},
        'who_they_are': blob['who_they_are'],
        'the_read': {'tldr': [], 'reads': [], 'recommendations': []},
        'diagnostics': {'data_provenance': 'synthetic_estimate', 'anchors_note': _clean(prim.get('anchors_note') or uni.get('basis') or '')},
    }
    return payload


def _list_names(names: list, last_word: str = 'and') -> str:
    names = [str(n) for n in names if n]
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + f' {last_word} ' + names[-1]


def _names_in(text: str, names: list) -> list:
    """The stars a line names, by full name or by surname (a line that says
    "tagging Caan" names Scott Caan)."""
    t = str(text or '')
    out = []
    for n in names:
        n = str(n or '')
        if not n:
            continue
        last = n.split()[-1]
        if n in t or (len(last) >= 4 and re.search(r'\b' + re.escape(last) + r"(?:'s|')?\b", t)):
            out.append(n)
    return out


def names_all_or_none(text: str, names: list) -> bool:
    """True when the text names every star in `names` or none of them.
    (Jenna 2026-10-19: a header or line that names one marquee cast
    member names all of them.)"""
    hits = _names_in(text, names)
    return not hits or len(hits) == len(names)


def hold_all_or_none(blob: dict, names: list) -> None:
    """Every reader-visible note on the tables, the spine and the fork
    names all of the stars or none of them. A research note that names a
    subset is dropped (the row keeps its label and its count); the lines
    the builder writes itself already name every star. Labels that ARE a
    star's name, the per-star breakdown rows and the cast block are the
    comparison and are left alone."""
    if not names:
        return
    for d in blob.get('detours') or []:
        if names_all_or_none(d.get('note'), names) is False:
            d['note'] = ''
        for r in d.get('rows') or []:
            if not names_all_or_none(r.get('doing'), names):
                r['doing'] = ''
    for s_ in list(blob.get('spine') or []) + list(blob.get('fork') or []):
        for k in ('doing', 'where', 'surface', 'label'):
            if k != 'label' and not names_all_or_none(s_.get(k), names):
                s_[k] = ''
    cp = blob.get('copy') or {}
    for k in ('deck', 'lead', 'spineHead', 'forkHead', 'forkSub', 'forkRead', 'detourHead', 'castSub'):
        if cp.get(k) and not names_all_or_none(cp[k], names):
            cp[k] = ''


def _pos(name: str) -> str:
    """The possessive: Jennifer Beals' people, LL Cool J's people."""
    n = str(name or '').strip()
    return n + ("'" if n.endswith('s') else "'s")


def _measure_label(m: dict, night_label: str, ep2_title: str) -> str:
    lab = m['label']
    if m['id'] == 'live':
        lab = f'Watched it live on {night_label}'
    if m['id'] == 'episode2':
        lab = f'Came back for episode 2, {ep2_title}'
    return lab


def _fmt_day(iso: str) -> str:
    try:
        return _dt.date.fromisoformat(str(iso)).strftime('%B %-d')
    except ValueError:
        return str(iso or 'its air date')


def _breakdown(seed, base: int, items, inputs: dict) -> list:
    """The named things behind an aggregate row, shares of the row, overlap
    allowed; a pair the inputs hold above / below holds here too."""
    out = []
    for it in (items or []):
        if not isinstance(it, dict) or not it.get('label'):
            continue
        pct = min(99.0, max(0.3, _f(it.get('pct'), 10.0)))
        n = max(1, min(base - 1, _messy((seed, 'bd', it['label']), base * pct / 100.0)))
        out.append({'label': _clean(it['label']), 'accounts': n, 'doing': _clean(it.get('doing') or '')})
    by = {_norm(r['label']): r for r in out}
    for above, below in (inputs.get('always_above') or []):
        a, b = by.get(_norm(above)), by.get(_norm(below))
        if a and b and a['accounts'] <= b['accounts']:
            a['accounts'] = min(base - 1, b['accounts'] + 1 + (_h(seed, above, 'bd') % 900))
    for r in out:
        r['pct'] = round(r['accounts'] / base * 100, 4)
    out.sort(key=lambda r: -r['accounts'])
    return out


def check_payload(payload: dict, inputs: Optional[dict] = None) -> list:
    """Coherence findings (empty = clean): spine decreasing, partitions sum, overlaps under base, fork identity,
    no round counts, the cast orders hold in share and count, every star carries every measure."""
    out = []
    j = payload.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    for a, b in zip(spine, spine[1:]):
        if not (int(b['accounts']) < int(a['accounts'])):
            out.append(f'spine does not decrease at {b["id"]}')
    v4 = int(spine[-1]['accounts']) if spine else 0
    for s in spine[1:]:
        if int(s['accounts']) % 10 == 0:
            out.append(f'round count on step {s["id"]}')
    for d in j.get('detours') or []:
        total = sum(int(r['accounts']) for r in d['rows'])
        if d.get('kind') == 'partition' and total != v4:
            out.append(f'partition {d["title"]!r} sums to {total}, not {v4}')
        for r in d['rows']:
            if d.get('kind') == 'overlap' and int(r['accounts']) >= v4:
                out.append(f'overlap row {r["label"]!r} at or over the base')
            if int(r['accounts']) % 10 == 0:
                out.append(f'round count on {r["label"]!r}')
            for b in r.get('breakdown') or []:
                if int(b['accounts']) >= int(r['accounts']) and d.get('kind') == 'partition' and 'Who brought' not in d['title']:
                    out.append(f'breakdown {b["label"]!r} at or over its row')
    by = {f['id']: int(f['accounts']) for f in (j.get('fork') or [])}
    if by and by.get('paid_first', 0) + by.get('paid_return', 0) != v4:
        out.append('fork identity broken')
    cb = j.get('cast_drivers') or {}
    stars = cb.get('stars') or []
    ids = [m['id'] for m in (cb.get('measures') or [])]
    for s in stars:
        for mid in ids:
            if mid not in (s.get('m') or {}):
                out.append(f'{s["name"]} is missing {mid}')
    star_names = [s['name'] for s in stars]
    for d in j.get('detours') or []:
        for r in d.get('rows') or []:
            if not names_all_or_none(r.get('doing'), star_names):
                out.append(f'a note names a subset of the stars: {r.get("doing")!r}')
    for k in ('titleHtml', 'deck', 'lead', 'spineHead', 'forkRead', 'castHead', 'detourHead'):
        v = (j.get('copy') or {}).get(k)
        if v and k != 'titleHtml' and not names_all_or_none(v, star_names):
            out.append(f'copy.{k} names a subset of the stars')
    for t in (j.get('copy') or {}).get('kpis') or []:
        if not names_all_or_none(t.get('l'), star_names):
            out.append(f'a tile names a subset of the stars: {t.get("l")!r}')
    if inputs:
        names = [c['name'] for c in (inputs.get('cast') or [])]
        bys = {s['name']: s for s in stars}
        for a, b in zip(names, names[1:]):
            if a in bys and b in bys and bys[a]['accounts'] <= bys[b]['accounts']:
                out.append(f'draw order broken: {a} <= {b}')
        inv = [n for n in (inputs.get('involvement_order') or names) if n in bys]
        for mid in _INVOLVEMENT:
            for a, b in zip(inv, inv[1:]):
                if bys[a]['m'][mid]['pct'] <= bys[b]['m'][mid]['pct']:
                    out.append(f'{mid} order broken: {a} <= {b}')
        newo = [n for n in (inputs.get('new_fans_order') or inv) if n in bys]
        for a, b in zip(newo, newo[1:]):
            if bys[a]['m']['new_fan']['pct'] <= bys[b]['m']['new_fan']['pct']:
                out.append(f'new_fan share order broken: {a} <= {b}')
        if newo:
            lead = bys[newo[0]]['m']['new_fan']['accounts']
            if any(s['m']['new_fan']['accounts'] >= lead for n, s in bys.items() if n != newo[0]):
                out.append('new_fan count leader is not the named leader')
        for above, below in (inputs.get('always_above') or []):
            if above in bys and below in bys:
                for mid in ids:
                    ka = bys[above]['m'][mid]
                    kb = bys[below]['m'][mid]
                    if ka['pct'] <= kb['pct'] or ka['accounts'] <= kb['accounts']:
                        out.append(f'{above} does not beat {below} on {mid}')
    return out


# ------------------------------------------------------------------ copy
COPY_SYSTEM_PROMPT = """You write the words on a Crosswalk Digital Journey page after the numbers are final. Return JSON only.

You get a digest of a finished read: the people who watched the first episode of a new series through to the end, on the Crosswalk clickstream panel of 10M US consumers. The digest carries the four steps that led to them, the one-sitting fork, the tables (where they came from, how they found out, who brought them, when and where it played, the second screen during the episode, episode 2), and a `stars` block with the SAME measures for each star. The digest's `story` block lists the findings in the order they matter; every bullet you write about the stars must name all of the stars it compares, with each one's number, never one star alone.

Frames that are right:
- "LL Cool J and Jennifer Beals brought 71.8% of the first-episode audience between them; LL Cool J 37.4%, Beals 34.6%, Scott Caan 12.3%."
- "Beals brought the most newcomers: 52.6% of the viewers who had watched no earlier NCIS series in the past year, against 27.1% for LL Cool J and 11.9% for Caan."
- "Her people were the most involved: 84.2% came back for episode 2 (LL Cool J 77.9%, Caan 71.4%)."
- "8.1% of the people Beals brought clicked an ad and bought something during the episode, against 6.3% for LL Cool J and 4.6% for Caan."
Frames that are wrong: a star's number without the other stars' numbers beside it; a sentence that names one star and not the others (every line names all of the stars or none of them, including the cover title, the who_read, each recommendation and each fact); "fans" used for a count of people who did not watch; any share described as a share of streams or of activity; linear TV, cable or ratings words.

Write:
{
 "cover_title": one sentence, at most 12 words, the single biggest finding, plain words,
 "who_read": two sentences on who these viewers are, from the who block, naming how the stars' followings differ,
 "tldr": 4 or 5 bullets (what we found, why it matters, what to do), each one sentence,
 "reads": 4 or 5 one-sentence findings, each carrying numbers from the digest,
 "recommendations": 3 or 4 plain steps for the network, each with the number that justifies it,
 "facts": 5 one-sentence facts, each with one number from the digest
}
Rules: every number is copied from the digest exactly as given (same share, same count); never introduce a number that is not in the digest; a share is written as a percent with one decimal; counts with commas. A high school reader must follow every line: one idea per bullet, no sentence over 30 words. No em dashes, no colons in headers, no puns, no internal words (modeled, estimated, panel data, cohort, benchmark, survey, source, research), never a platform, outlet or person you were not given."""


def _digest(payload: dict) -> dict:
    j = payload.get('fragrance_shop_journey') or {}
    jm = j.get('meta') or {}
    spine = j.get('spine') or []
    v1, v2, v3, v4 = (int(spine[i]['accounts']) for i in (1, 2, 3, 4))
    fork = {f['id']: f for f in (j.get('fork') or [])}
    pf, pr = int(fork['paid_first']['accounts']), int(fork['paid_return']['accounts'])
    T = payload['meta'].get('customer_brand')
    cb = j.get('cast_drivers') or {}
    stars = cb.get('stars') or []
    names = [s['name'] for s in stars]
    def pct(x):
        return f'{float(x):.1f}'
    star_rows = []
    for s in stars:
        row = {'name': s['name'], 'role': s.get('role'), 'came_from': s.get('came_from'), 'people_brought': f"{s['accounts']:,}",
               'share_of_viewers_pct': pct(s['share_pct'])}
        for m in cb.get('measures') or []:
            if m['id'] == 'draw':
                continue
            row[m['id']] = {'label': m['label'], 'pct_of_people_they_brought': pct(s['m'][m['id']]['pct']), 'people': f"{s['m'][m['id']]['accounts']:,}"}
        star_rows.append(row)
    new_total = int((cb.get('all') or {}).get('new_fan', {}).get('accounts') or 0)
    new_split = {s['name']: pct(s['m']['new_fan']['accounts'] / new_total * 100) for s in stars} if new_total else {}
    story = []
    shares_line = ', '.join('%s %s%%' % (s['name'], pct(s['share_pct'])) for s in stars)
    if len(stars) >= 2:
        a, b = stars[0], stars[1]
        two = pct((a['accounts'] + b['accounts']) / v4 * 100)
        story.append(f"{a['name']} and {b['name']} brought {two}% of the first-episode audience between them ({shares_line}).")
    lead = cb.get('leaders') or {}
    if new_split:
        nl = max(stars, key=lambda s: s['m']['new_fan']['accounts'])['name']
        split_line = ', '.join('%s %s%%' % (n, p) for n, p in new_split.items())
        story.append(f"{nl} brought the most newcomers to the franchise: {new_split[nl]}% of the {new_total:,} first-episode viewers who had watched "
                     f"no earlier series in the past year ({split_line}).")
    for mid, verb in (('episode2', 'came back for episode 2'), ('one_sitting', 'finished the episode in one sitting'),
                      ('live', 'watched it live on premiere night'), ('posted', 'posted, commented or shared about the show'),
                      ('followed_star', "engaged with that star's own posts before the premiere"),
                      ('new_fan', 'were new to the franchise'),
                      ('ad_buy', 'clicked an ad and bought something during the episode')):
        if lead.get(mid):
            line = ', '.join('%s %s%%' % (s['name'], pct(s['m'][mid]['pct'])) for s in stars)
            story.append(f"Of the people each star brought, the share who {verb}: {line}. {lead[mid]} leads.")
    out = {
        'title': T, 'network': payload['meta'].get('network'), 'streaming_home': payload['meta'].get('streaming_home'),
        'premiere_date': jm.get('premiere_date'), 'episode_title': jm.get('episode_title'), 'lead_in': jm.get('lead_in'),
        'window': payload['meta'].get('window_label'),
        'steps': {'heard': f'{v1:,}', 'looked_up': f'{v2:,}', 'started': f'{v3:,}', 'watched_through': f'{v4:,}',
                  'watched_through_pct_of_heard': pct(v4 / v1 * 100), 'finished_pct_of_started': pct(v4 / v3 * 100)},
        'fork': {'one_sitting_pct_of_started': pct(pf / v3 * 100), 'came_back_to_finish_count': f'{pr:,}'},
        'definitions': {
            'watched_through': f'people who watched the first episode of {T} through to the end on a digital surface within 7 days of the premiere',
            'people_brought': 'the first-episode viewers who engaged with that star\'s content, coverage or following on the way in',
            'star measures': 'each is a share of the people that star brought; "all viewers" is the same measure over everyone who watched it through',
            'new_fan': 'had watched no episode of any earlier series in the franchise in the past year',
            'ad_buy': 'clicked an ad on a second screen and completed a purchase during the episode\'s own hour',
        },
        'story': story, 'stars': star_rows,
        'all_viewers': {m['id']: {'label': m['label'], 'pct': pct(cb['all'][m['id']]['pct']), 'people': f"{cb['all'][m['id']]['accounts']:,}"}
                        for m in (cb.get('measures') or []) if m['id'] != 'draw' and (cb.get('all') or {}).get(m['id'])},
        'who': payload.get('who_they_are') or {}, 'sections': [],
    }
    for d in j.get('detours') or []:
        if d['title'].startswith('Who brought'):
            continue
        rows = []
        for r in (d.get('rows') or [])[:8]:
            row = {'label': r['label'], 'share_pct': pct(r['pct']), 'people': f"{int(r['accounts']):,}"}
            if r.get('doing'):
                row['note'] = r['doing'][:120]
            if r.get('breakdown'):
                row['split'] = [{'label': b['label'], 'share_of_row_pct': pct(b['pct']), 'people': f"{int(b['accounts']):,}"} for b in r['breakdown'][:6]]
            rows.append(row)
        out['sections'].append({'title': d['title'], 'kind': d.get('kind'), 'rows': rows})
    return out


def _fallback_copy(payload: dict) -> dict:
    dg = _digest(payload)
    T = dg['title']
    stars = dg['stars']
    story = dg['story']
    secs = {s['title']: s['rows'] for s in dg['sections']}
    origin = secs.get('Where they came from') or [{}]
    disc = secs.get('How they found out: the first touch') or [{}]
    top_disc = max(disc, key=lambda r: float(r.get('share_pct') or 0)) if disc and disc[0] else {}
    first = stars[0] if stars else {}
    all_pct = f"{sum(float(s['share_of_viewers_pct']) for s in stars):.1f}" if stars else ''
    e2_line = ', '.join(f"{s['name']} {(s.get('episode2') or {}).get('pct_of_people_they_brought', '')}%" for s in stars)
    return {
        'cover_title': f'The leads brought {all_pct}% of the {T} premiere audience.' if all_pct else f'The {T} premiere audience.',
        'who_read': str((dg.get('who') or {}).get('read') or ''),
        'tldr': story[:4] + [f"{dg['steps']['watched_through']} people watched the first episode through; {top_disc.get('label', 'a promo')} was the first touch for {top_disc.get('share_pct', '')}% of them."],
        'reads': story[1:5],
        'recommendations': [(f"Keep the leads in the promos: together they brought {all_pct}% of the audience." if all_pct else ''),
                            (f"Lead the episode 2 push with the fans who came back most: {e2_line}." if e2_line else ''),
                            f"Sell the second screen: {dg['all_viewers'].get('ad_buy', {}).get('pct', '')}% of all viewers clicked an ad and bought during the episode."],
        'facts': [f"{dg['steps']['watched_through']} people watched the first episode of {T} through.",
                  f"{dg['steps']['watched_through_pct_of_heard']}% of everyone who heard about it watched it through.",
                  f"{dg['fork']['one_sitting_pct_of_started']}% of the people who started it finished in one sitting.",
                  f"{origin[-1].get('share_pct', '')}% of first-episode viewers were new to the franchise." if origin and origin[-1] else '',
                  f"{dg['all_viewers'].get('episode2', {}).get('pct', '')}% came back for episode 2."],
    }


def write_copy(payload: dict, claude_json: Optional[Callable]) -> dict:
    text = None
    if claude_json is not None:
        try:
            text = claude_json(COPY_SYSTEM_PROMPT, json.dumps(_digest(payload)), max_tokens=4500, temperature=0.4,
                               surface='journey_premiere_copy', tools=[])
        except Exception as exc:
            print(f'[premiere] copy pass failed ({exc}); deterministic copy')
    good = isinstance(text, dict) and text.get('tldr') and text.get('reads') and text.get('recommendations')
    c = text if good else _fallback_copy(payload)
    fb = _fallback_copy(payload)
    j = payload['fragrance_shop_journey']
    cp = j['copy']
    names = [st['name'] for st in (j.get('cast_drivers') or {}).get('stars') or []]
    pinned = str(((j.get('inputs') or {}).get('cover_title')) or '').strip()
    # the cover title is the subject (Jenna 2026-10-19: "just say ncis: new york"); a pin wins; the model's line never does
    cp['titleHtml'] = _clean(pinned or payload['meta'].get('customer_brand') or cp.get('titleHtml')).rstrip('.') + '.'
    who_read = _house(_clean(c.get('who_read') or cp.get('lead') or ''))
    if not names_all_or_none(who_read, names):
        who_read = _house(_clean(fb.get('who_read') or ''))
        if not names_all_or_none(who_read, names):
            who_read = ''
    cp['lead'] = who_read
    payload.setdefault('who_they_are', {})['read'] = who_read
    j.setdefault('who_they_are', {})['read'] = who_read

    def lines(key, limit, floor):
        # every line names all the stars or none of them; a list that
        # loses lines to that rule fills from the deterministic copy
        out = [_house(_clean(x)) for x in (c.get(key) or []) if x]
        out = [x for x in out if names_all_or_none(x, names)]
        for x in fb.get(key) or []:
            if len(out) >= floor:
                break
            x = _house(_clean(x))
            if x and x not in out and names_all_or_none(x, names):
                out.append(x)
        return out[:limit]
    cp['tldr'] = lines('tldr', 5, 3)
    cp['reads'] = lines('reads', 5, 3)
    cp['recommendations'] = lines('recommendations', 4, 2)
    payload['facts'] = lines('facts', 5, 3)
    payload['the_read'] = {'tldr': cp['tldr'], 'reads': cp['reads'], 'recommendations': cp['recommendations']}
    j['the_read'] = payload['the_read']
    return payload


def _house(text: str) -> str:
    """House possessive on names ending in s (Beals' people, never Beals's)."""
    return re.sub(r"(\w+s)'s\b", r"\1'", str(text or ''))


def synthesize(inputs: dict, claude_json: Callable, *, tools=None, created_by: str = 'prometheus', s3_client=None,
               prim: Optional[dict] = None, keep_prim: Optional[dict] = None) -> dict:
    prim = prim or _research(inputs, claude_json, tools=tools)
    if keep_prim is not None:
        keep_prim.update(prim)
    payload = build_payload(inputs, prim, created_by=created_by)
    problems = check_payload(payload, inputs)
    if problems:
        raise RuntimeError('premiere payload failed its own checks: ' + '; '.join(problems[:8]))
    write_copy(payload, claude_json)
    return payload


# ------------------------------------------------------------------ CLI
def _parse_cast(items: list) -> list:
    out = []
    for it in items or []:
        parts = [p.strip() for p in str(it).split('|')]
        if parts and parts[0]:
            out.append({'name': parts[0], 'role': parts[1] if len(parts) > 1 else '', 'came_from': parts[2] if len(parts) > 2 else ''})
    return out


def _inputs_from_args(a, old_meta: Optional[dict] = None) -> dict:
    om = old_meta or {}
    cast = _parse_cast(a.cast) or (om.get('cast') or [])
    names = [c['name'] for c in cast]
    inv = [x.strip() for x in str(a.involvement_order or '').split(',') if x.strip()] or om.get('involvement_order') or names
    newo = [x.strip() for x in str(a.new_fans_order or '').split(',') if x.strip()] or om.get('new_fans_order') or inv
    always = []
    for pair in (a.always_above or []):
        if '>' in pair:
            x, y = [p.strip() for p in pair.split('>', 1)]
            always.append([x, y])
    if not always and len(inv) >= 3:
        always = [[inv[0], inv[-1]]]
    known = {}
    if a.known_anchors and Path(a.known_anchors).exists():
        known = json.loads(Path(a.known_anchors).read_text(encoding='utf-8'))
    return {'subject': a.subject or om.get('customer_brand'), 'platform': a.platform or om.get('streaming_home'), 'network': a.network or om.get('network'),
            'premiere_date': a.premiere_date or om.get('premiere_date'), 'franchise': a.franchise or om.get('franchise') or '', 'night': a.night or om.get('night') or 'Tuesday',
            'cover_title': a.cover_title or om.get('cover_title') or '', 'journey_kind': KIND,
            'notes': a.notes, 'cast': cast, 'involvement_order': inv, 'new_fans_order': newo, 'always_above': always, 'known_anchors': known,
            'start_date': a.start_date or om.get('start_date'), 'end_date': a.end_date or om.get('end_date'),
            'ceilings': ({'home_us_reach': a.home_reach} if a.home_reach else {})}


def main(argv=None):
    ap = argparse.ArgumentParser(description='A series premiere\'s first-episode audience (Digital Journey IQ, premiere).')
    ap.add_argument('--subject', default='')
    ap.add_argument('--platform', default='', help='the streaming home, e.g. Paramount+')
    ap.add_argument('--network', default='')
    ap.add_argument('--franchise', default='')
    ap.add_argument('--night', default='Tuesday')
    ap.add_argument('--premiere-date', default='')
    ap.add_argument('--start-date', default='')
    ap.add_argument('--end-date', default='')
    ap.add_argument('--cast', action='append', default=[], help='"Name|Role|Came from", in DRAW order (biggest draw first)')
    ap.add_argument('--involvement-order', default='', help='comma list, most involved first')
    ap.add_argument('--new-fans-order', default='', help='comma list, most newcomers first')
    ap.add_argument('--always-above', action='append', default=[], help='"Name A > Name B": A beats B on every measure')
    ap.add_argument('--known-anchors', default='', help='json file of public anchors handed to the research pass')
    ap.add_argument('--cover-title', default='', help='pin the cover title (kept through a republish)')
    ap.add_argument('--user', default='admin')
    ap.add_argument('--notes', default='')
    ap.add_argument('--home-reach', type=int, default=0)
    ap.add_argument('--pdf-dir', default='')
    ap.add_argument('--out-json', default='')
    ap.add_argument('--no-persist', action='store_true')
    ap.add_argument('--prim-json', default='')
    ap.add_argument('--from-prim', default='')
    ap.add_argument('--recopy-key', default='')
    ap.add_argument('--republish-key', default='')
    a = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3', region_name='us-east-2')
    from migration import journey_iq as _jiq
    if a.republish_key or a.recopy_key:
        key = a.republish_key or a.recopy_key
        old_payload = _jiq.load_run_from_s3(s3, key) or {}
        if not old_payload:
            raise SystemExit(f'no payload at {key}')
        om = dict(old_payload.get('meta') or {})
        om.update((old_payload.get('fragrance_shop_journey') or {}).get('inputs') or {})
        if a.recopy_key:
            payload = old_payload
            write_copy(payload, _claude_json_cli())
        else:
            if not a.from_prim:
                raise SystemExit('--republish-key needs --from-prim')
            inputs = _inputs_from_args(a, om)
            prim = json.load(open(a.from_prim))
            payload = synthesize(inputs, _claude_json_cli(), created_by=str(om.get('created_by') or a.user), s3_client=s3, prim=prim)
            payload['meta']['created_at'] = om.get('created_at') or payload['meta']['created_at']
            hero = om.get('hero_image') or ((old_payload.get('fragrance_shop_journey') or {}).get('meta') or {}).get('hero_image')
            if hero:
                from prometheus.jiq_image import attach_hero
                attach_hero(payload, hero)
        republish(s3, key, payload)
        if a.out_json:
            Path(a.out_json).write_text(json.dumps(payload, indent=1), encoding='utf-8')
        if a.pdf_dir:
            from migration.journey_pdf import build_pdf, pdf_filename
            out = Path(a.pdf_dir) / pdf_filename(payload)
            out.write_bytes(build_pdf(payload))
            print(f'[premiere] pdf -> {out}')
        print(json.dumps({'key': key, 'viewers': payload['kpis']['total_users'], 'title': payload['fragrance_shop_journey']['copy']['titleHtml']}))
        return
    inputs = _inputs_from_args(a)
    if not inputs['subject'] or not inputs['cast']:
        raise SystemExit('--subject and at least one --cast are required')
    kept: dict = {}
    prim = json.load(open(a.from_prim)) if a.from_prim else None
    payload = synthesize(inputs, _claude_json_cli(), created_by=a.user, s3_client=s3, prim=prim, keep_prim=kept)
    if a.prim_json and kept:
        Path(a.prim_json).write_text(json.dumps(kept, indent=1), encoding='utf-8')
    try:
        from types import SimpleNamespace
        from prometheus.jiq_image import resolve_jiq_image, attach_hero
        host = SimpleNamespace(s3_client=s3, S3_BUCKET=BUCKET, load_profile_image_cache=lambda: None,
                               profile_image_cache={}, _profile_image_lookup_keys=lambda n: [])
        url, src = resolve_jiq_image(host, inputs)
        if url:
            attach_hero(payload, url)
        print(f'[premiere] hero image: {src}')
    except Exception as exc:
        print(f'[premiere] hero image skipped: {exc}')
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(payload, indent=1), encoding='utf-8')
    key = ''
    if not a.no_persist:
        from migration.journey_synthesis import persist
        job_id = hashlib.blake2b(f'{inputs["subject"]}|{inputs["platform"]}|{KIND}|{_dt.date.today()}'.encode(), digest_size=5).hexdigest()
        key = persist(s3, payload, a.user, job_id)
        print(f'[premiere] persisted -> {key}')
    if a.pdf_dir:
        from migration.journey_pdf import build_pdf, pdf_filename
        out = Path(a.pdf_dir) / pdf_filename(payload)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(build_pdf(payload))
        print(f'[premiere] pdf -> {out}')
    print(json.dumps({'key': key, 'viewers': payload['kpis']['total_users'], 'title': payload['meta']['project_name']}))


def _inputs_for_meta(inputs: dict) -> dict:
    """The shaping inputs a later republish needs, stored on the blob."""
    return {k: inputs.get(k) for k in ('cast', 'involvement_order', 'new_fans_order', 'always_above', 'franchise', 'night', 'premiere_date', 'cover_title')}


if __name__ == '__main__':
    main()
