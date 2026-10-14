#!/usr/bin/env python3
"""The Babylon 5 journey is the gold standard for every Digital Journey
(Jenna, 2026-10-14: "the gold standard should be babylon5 ... I want all
outputs to look like that one so make sure that's what prometheus runs
from here out").

Carolyn pulled Babylon 5 on October 2 and No Reservations on October 13 and
got two different files: the same spine with different labels, then a
"Discovery mix" where Babylon 5 had the first post they saw and where it
sat, no count of posts before the click, no "where they looked before Prime
Video", no time from the social view to the first episode. The research
model chose its own detours each time. This module makes the shape a
contract that holds on every watch journey, however the research came back:

  Spine, four steps after the TAM, Babylon 5 labels:
    Saw a social post about {title}
    Clicked through from the post to the title
    Opened the {title} page on {platform}
    Watched an episode within 30 days of the post

  Sections, Babylon 5 order and titles:
    01 First {title} post they saw                 partition of the social step
    02 Where the first post sat                    partition of the social step
    03 How many posts before they clicked through  partition of the click step
    04 Where they looked before {platform}         overlap of the click step
    05 Time from the social view to the first episode   partition of the watch step
    06 First episode they played                   partition of the watch step
    07 Last touch before the watch                 partition of the watch step
    08 What assisted the watch                     overlap of the watch step
    09 The rest of the social and {platform} overlap    the two outside-the-window lines
    10 Every {platform} {title} viewer in the window     within 30 days / outside / no post seen

A section the research already produced is kept under the standard title
(renamed, rescaled to the standard base). A missing section is built: the
frequency, timing, overlap and every-viewer sections from the spine and the
norms' already-watching line with title-salted distributions; the four
title-specific sections (first post, where it sat, where they looked, first
episode, last touch, assists) from one small research call, with plain
deterministic rows when the call fails. Extra sections the research added
ride after the ten. Idempotent: a gold payload passes through unchanged.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import traceback
from typing import Callable, Optional

US_GEN_POP = 329_900_000

SPINE_LABELS = (
    ('social_view', 'Saw a social post about {title}'),
    ('click_through', 'Clicked through from the post to the title'),
    ('title_page', 'Opened the {title} page on {platform}'),
    ('watched_30d', 'Watched an episode within 30 days of the post'),
)

# key, title template, base, kind, doing template
STANDARD = (
    ('first_post', 'First {title} post they saw', 'social', 'partition', 'First {title} post on the account'),
    ('post_surface', 'Where the first post sat', 'social', 'partition', 'First {title} post surface'),
    ('posts_before', 'How many posts before they clicked through', 'click', 'partition', 'Posts on the account before title intent'),
    ('looked_before', 'Where they looked before {platform}', 'click', 'overlap', 'Looked for it before {platform}'),
    ('time_to_play', 'Time from the social view to the first episode', 'end', 'partition', 'Days from the post to the first play'),
    ('first_episode', 'First episode they played', 'end', 'partition', 'First play on the account'),
    ('last_touch', 'Last touch before the watch', 'end', 'partition', 'Last click before the play'),
    ('assists', 'What assisted the watch', 'end', 'overlap', 'Assist on the play'),
    ('rest_overlap', 'The rest of the social and {platform} overlap', 'rest', 'partition', 'Did both, outside the 30 days'),
    ('every_viewer', 'Every {platform} {title} viewer in the window', 'viewers', 'partition', '{platform} {title} viewers'),
)
RESEARCHED = ('first_post', 'post_surface', 'looked_before', 'first_episode', 'last_touch', 'assists')

# how an existing detour title maps onto a standard key (first match wins)
_MATCH = (
    ('first_post', re.compile(r"first post about .* they saw|first .*post they saw|post they saw|post type|kind of post|what post", re.I)),
    ('post_surface', re.compile(r"where the first post sat|discovery mix|first social surface|where .* first (?:met|encountered|saw)|first post surface", re.I)),
    ('posts_before', re.compile(r"how many posts|posts before|post frequency|frequency", re.I)),
    ('looked_before', re.compile(r"looked before|where they looked|searched before|before (?:prime video|the platform|netflix|hulu|peacock|max|disney)", re.I)),
    ('time_to_play', re.compile(r"time from|timing|same day|days from|how long", re.I)),
    ('first_episode', re.compile(r"first episode", re.I)),
    ('last_touch', re.compile(r"last touch", re.I)),
    ('assists', re.compile(r"assist", re.I)),
    ('rest_overlap', re.compile(r"rest of the social|outside the 30|did both", re.I)),
    ('every_viewer', re.compile(r"^every .*viewer|viewer(?:s)? in the window", re.I)),
)

POSTS_BEFORE = (('1 post', 28.07), ('2 to 3 posts', 34.15), ('4 to 6 posts', 22.67), ('7 to 10 posts', 10.18), ('11 or more', 4.93))
TIME_TO_PLAY = (('Same day', 59.87), ('Within 7 days (days 1 to 7)', 25.56), ('Within 30 days (days 8 to 30)', 14.57))

FALLBACK_ROWS = {
    'first_post': (('Clip from an episode', 27.4), ('Cast or creator news', 19.6), ('Rankings and recommendation posts', 17.8),
                   ('Fan edits and reactions', 14.9), ('Quotes and memorable scenes', 12.6), ('Other, meme, or reaction', 7.7)),
    'post_surface': (('YouTube, clips and Shorts', 31.4), ('TikTok', 24.6), ('Facebook', 17.3), ('Instagram', 12.8), ('Reddit', 9.1), ('X', 4.8)),
    'looked_before': (('Google, where to watch', 42.9), ('JustWatch or a where-to-watch guide', 26.1), ("Another service's title search", 21.6),
                      ('YouTube full-episode hunt', 11.2), ('Reddit where to watch', 9.6), ('Pluto TV or another FAST', 6.3)),
    'first_episode': (('Season 1, episode 1', 39.3), ('The pilot or premiere', 21.2), ('A later episode straight from a clip', 19.2),
                      ('A fan-favorite episode', 11.8), ('Resumed from a continue row, already mid-series', 8.5)),
    'last_touch': (('{platform} search for {title}', 35.4), ('{platform} title page from Google', 22.8), ('{platform} home or continue row', 17.9),
                   ('Link from the post or a comment', 15.5), ('A where-to-watch guide click-out', 8.4)),
    'assists': (('A where-to-watch search', 54.2), ('A Reddit thread', 33.8), ('A YouTube clip or recap after the post', 29.8),
                ('News or press about the title', 21.1), ('A second social post', 12.0)),
}

RESEARCH_PROMPT = """You complete the standard sections of a Crosswalk Digital Journey for a TV or film title on a streaming service. Return JSON only.

The reader already has the path counts. You supply the named rows for the sections listed in `wanted`, each a list of 5 to 7 rows of {"label": str, "pct": float, "doing": str}:
- first_post: the kinds of posts about the title people met first (real post families for THIS title: specific clip subjects, cast or revival news, rankings, fan edits, quotes). Partition, pcts sum to about 100.
- post_surface: the platform the first post sat on (YouTube clips and Shorts, TikTok, Facebook, Instagram, Reddit, X, others that matter for this title). Partition.
- looked_before: where people looked for the title before reaching the service (Google where to watch, JustWatch or a guide, other services' searches, YouTube full-episode hunt, Reddit, FAST). Overlap, rows may sum past 100.
- first_episode: the REAL first episode or film people played first (season 1 episode 1 by its actual name, the pilot, a fan-favorite by name, a later episode straight from a clip, a continue row). Partition.
- last_touch: the last click before the play (service search, title page from Google, home or continue row, link from the post, a guide click-out). Partition.
- assists: what else appeared in the path (a where-to-watch search, a Reddit thread, a YouTube recap, press, a second post). Overlap.

Rules: labels name real things for this title and service, never placeholders. Shares are messy (no .0 or .5 endings), no two shares identical, the largest row under 60. `doing` is one plain phrase. Nothing else in the output."""


def _h(*parts) -> int:
    return int(hashlib.blake2b('|'.join(str(p) for p in parts).encode(), digest_size=8).hexdigest(), 16)


def _messy(seed, value: float, floor: int = 1) -> int:
    v = max(int(round(value)), floor)
    if v % 10 == 0:
        v += 1 + (_h(seed, v) % 8)
    return v


def _salt_pct(seed, pct: float, spread: float = 0.12) -> float:
    """A share moved by up to +/- spread of itself, title-salted, never on .0 / .5."""
    f = 1.0 + ((_h(seed, 'salt') % 2001) / 1000.0 - 1.0) * spread
    v = round(pct * f, 2)
    if abs(v * 10 - round(v * 10)) < 1e-9:   # lands on a .X0 / .X5 boundary
        v += 0.03 + (_h(seed, 'tail') % 5) / 100.0
    return round(v, 2)


def _fill(title: str, platform: str, text: str) -> str:
    """Fill the title and platform slots. A title that starts with an
    article ("The Following") reads as a phrase, not an adjective: "Opened
    the page for The Following on Prime Video", never "the The Following
    page"."""
    text = str(text or '')
    if title.lower().startswith(('the ', 'a ', 'an ')):
        text = (text.replace('Opened the {title} page on {platform}', 'Opened the page for {title} on {platform}')
                    .replace('First {title} post they saw', 'First post about {title} they saw')
                    .replace('First {title} post on the account', 'First post about {title} on the account')
                    .replace('First {title} post surface', 'Surface of the first post about {title}')
                    .replace('Every {platform} {title} viewer in the window', 'Every viewer of {title} on {platform} in the window')
                    .replace('{platform} {title} viewers', 'Viewers of {title} on {platform}')
                    .replace('No {title} social post seen', 'No social post about {title} seen')
                    .replace('{platform} search for {title}', 'Search on {platform} for {title}'))
    return text.replace('{title}', title).replace('{platform}', platform)


def _title_platform(payload: dict, inputs: Optional[dict] = None) -> tuple:
    meta = payload.get('meta') or {}
    subj = str((inputs or {}).get('subject') or '').strip()
    plat = str((inputs or {}).get('platform') or '').strip()
    name = str(meta.get('target_name') or meta.get('project_name') or meta.get('target') or '')
    if not subj:
        subj = name.rsplit(' on ', 1)[0].strip() if ' on ' in name else name.strip()
    if not plat and ' on ' in name:
        plat = name.rsplit(' on ', 1)[1].strip()
    if not plat:
        plat = str(meta.get('customer_brand') or '').strip()
    return subj or 'the title', plat or 'the service'


def is_watch_journey(payload: dict, inputs: Optional[dict] = None) -> bool:
    meta = payload.get('meta') or {}
    kind = str((inputs or {}).get('journey_kind') or meta.get('journey_kind') or '').lower()
    if kind == 'watch' or str(meta.get('target_type') or '') == 'watch_journey':
        return True
    j = payload.get('fragrance_shop_journey') or {}
    end = (j.get('spine') or [{}])[-1].get('label', '')
    return bool(re.search(r"\bwatch", str(end), re.I))


# ----------------------------------------------------------------- spine
def standardize_spine(j: dict, title: str, platform: str, seed: str) -> dict:
    """Four Babylon 5 steps after the TAM. A longer research spine folds:
    the first step is the social view, the last is the watch, the step
    before the watch is the title page, and the step right after the
    social view is the click-through. Folded steps keep their counts in
    the trail (the search step becomes the base of 'where they looked').
    Returns {'folded': [...], 'search_count': int|None}."""
    spine = j.get('spine') or []
    trail = {'folded': [], 'search_count': None}
    if len(spine) < 3:
        return trail
    tam, steps = spine[0], spine[1:]
    if len(steps) > 4:
        # the search / lookup step, if the research named one, is the base of section 04
        for s in steps[1:-2]:
            if re.search(r"search|look(?:ed)? up|where to watch", str(s.get('label') or ''), re.I):
                trail['search_count'] = int(s.get('accounts') or 0)
        keep = [steps[0], steps[1], steps[-2], steps[-1]]
        trail['folded'] = [{'id': s.get('id'), 'label': s.get('label'), 'accounts': s.get('accounts')} for s in steps if s not in keep]
        steps = keep
    elif len(steps) == 3:
        # social -> page -> watch: a click step is synthesized between
        social, page, watch = steps
        mid = _messy((seed, 'click'), (int(social['accounts']) * 0.386), floor=int(page['accounts']) + 1)
        mid = max(min(mid, int(social['accounts']) - 1), int(page['accounts']) + 1)
        steps = [social, {'id': 'click_through', 'accounts': mid, 'doing': 'Clicked through from the post to the title',
                          'where': 'Title search, where-to-watch guide, or the link in the post', 'surface': '', 'job': '', 'timing': ''}, page, watch]
    ids = [k for k, _ in SPINE_LABELS]
    prev = int(tam['accounts'])
    out = [tam]
    for (sid, label), s in zip(SPINE_LABELS, steps):
        s = dict(s)
        s['id'] = sid
        s['label'] = _fill(title, platform, label)
        acc = int(s.get('accounts') or 0)
        s['kept'] = round(acc / prev * 100, 4) if prev else 0.0
        s['dropped'] = max(prev - acc, 0)
        s['ofUs'] = round(acc / US_GEN_POP * 100, 4)
        out.append(s)
        prev = acc
    for a, b in zip(out, out[1:]):
        a['next'] = b['label']
    out[-1]['next'] = ''
    j['spine'] = out
    if len(out) == 5:
        j['spine'][1].setdefault('where', _fill(title, platform, 'First {title} post on the account'))
    return trail


# ------------------------------------------------------------------ fork
def refit_degenerate_fork(j: dict, seed: str) -> bool:
    """The leave-and-return fork off the title page, in the Babylon 5
    shape, when the research left it degenerate (No Reservations shipped
    'Converted after coming back: 8' against 213,670 in the same session).
    Headline counts never move: paid_first + paid_return still equals the
    watch step, abandoned still equals page minus same-session. Returns
    True when the fork was refit."""
    spine = j.get('spine') or []
    fork = j.get('fork') or []
    if len(spine) < 5 or not fork:
        return False
    page, watched = int(spine[3]['accounts']), int(spine[4]['accounts'])
    by = {f.get('id'): f for f in fork if isinstance(f, dict)}
    pr, pf, ret = by.get('paid_return'), by.get('paid_first'), by.get('returned')
    if not (pr and pf) or page <= 0 or watched <= 0:
        return False
    paid_return, paid_first = int(pr.get('accounts') or 0), int(pf.get('accounts') or 0)
    returned = int((ret or {}).get('accounts') or 0)
    degenerate = (paid_return < 0.02 * watched or paid_first > 0.95 * watched or returned < paid_return
                  or paid_first + paid_return != watched)
    if not degenerate:
        return False
    # Babylon 5 ratios, title-salted: same-session share of the page, retarget, return and play-after-return rates
    same = 0.28 + (_h(seed, 'fork', 'same') % 81) / 1000.0            # 0.28 to 0.36 of the page watch in the same session
    paid_first = min(watched - 1, max(1, _messy((seed, 'fork', 'pf'), page * same)))
    paid_return = watched - paid_first
    abandoned = page - paid_first
    if abandoned <= paid_return:                 # the page cannot lose fewer people than came back
        paid_first = max(1, watched - max(1, (page - watched) // 2))
        paid_return = watched - paid_first
        abandoned = page - paid_first
    r_ret = 0.90 + (_h(seed, 'fork', 'r_ret') % 61) / 1000.0           # share of returners who then played
    returned = max(paid_return + 1, min(abandoned - 1, _messy((seed, 'fork', 'returned'), paid_return / r_ret)))
    retargeted = max(returned + 1, min(abandoned, _messy((seed, 'fork', 'retargeted'), returned / (0.72 + (_h(seed, 'fork', 'rt') % 91) / 1000.0))))
    vals = {'abandoned': abandoned, 'retargeted': retargeted, 'returned': returned, 'paid_return': paid_return, 'paid_first': paid_first}
    parents = {'abandoned': page, 'retargeted': abandoned, 'returned': retargeted, 'paid_return': returned, 'paid_first': page}
    for fid, f in by.items():
        if fid in vals:
            f['accounts'] = vals[fid]
            f['kept'] = round(vals[fid] / parents[fid] * 100, 4) if parents[fid] else 0.0
    by['abandoned']['dropped'] = paid_first if 'abandoned' in by else 0
    if 'retargeted' in by:
        by['retargeted']['dropped'] = abandoned - retargeted
    if 'returned' in by:
        by['returned']['dropped'] = retargeted - returned
    pr['dropped'] = returned - paid_return
    pf['dropped'] = abandoned
    return True


def _tidy_extra(d: dict) -> None:
    """A table the research added on its own rides after the ten: the
    title starts with a capital, and a count quoted in the title agrees
    with the rows under it (No Reservations said 212,599 over rows that
    summed to 213,678)."""
    try:
        title = str(d.get('title') or '').strip()
        if not title:
            return
        rows = d.get('rows') or []
        total = sum(int(r.get('accounts') or 0) for r in rows if isinstance(r, dict))
        nums = re.findall(r'\d{1,3}(?:,\d{3})+|\d{4,}', title)
        if len(nums) == 1 and total and int(nums[0].replace(',', '')) != total:
            title = title.replace(nums[0], f"{total:,}")
        d['title'] = title[:1].upper() + title[1:]
    except Exception:
        pass


# -------------------------------------------------------------- sections
def _bases(j: dict, payload: dict, trail: dict) -> dict:
    # the click-through step is the base of sections 03 and 04, as in the
    # Babylon 5 file (a folded search step stays in the trail only, so a
    # second pass over a gold payload lands on the same numbers)
    spine = j.get('spine') or []
    social = int(spine[1]['accounts']) if len(spine) > 1 else 0
    click = int(spine[2]['accounts']) if len(spine) > 2 else social
    end = int(spine[-1]['accounts']) if spine else 0
    return {'social': social, 'click': click, 'end': end}


def _match_key(title: str) -> Optional[str]:
    t = str(title or '')
    for key, rx in _MATCH:
        if rx.search(t):
            return key
    return None


def _rows_from_shares(shares, base: int, seed, kind: str, doing: str, title: str, platform: str) -> list:
    rows = []
    for label, pct in shares:
        label = _fill(title, platform, label)
        p = _salt_pct((seed, label), float(pct))
        acc = _messy((seed, label), base * p / 100.0)
        acc = max(1, min(acc, base - 1 if base > 1 else 1))
        rows.append({'label': label, 'accounts': acc, 'doing': doing})
    if kind == 'partition' and rows and base > 0:
        diff = base - sum(r['accounts'] for r in rows)
        big = max(rows, key=lambda r: r['accounts'])
        big['accounts'] = max(1, big['accounts'] + diff)
    for r in rows:
        r['pct'] = round(r['accounts'] / base * 100, 4) if base else 0.0
    return rows


def _rescale(rows: list, base: int, kind: str, seed) -> list:
    """Existing rows onto the standard base: proportional, exact for a partition."""
    rows = [dict(r) for r in rows if isinstance(r, dict)]
    tot = sum(int(r.get('accounts') or 0) for r in rows) or 1
    if kind == 'partition':
        for r in rows:
            r['accounts'] = max(1, int(round(int(r.get('accounts') or 0) / tot * base)))
        diff = base - sum(r['accounts'] for r in rows)
        if rows:
            big = max(rows, key=lambda r: r['accounts'])
            big['accounts'] = max(1, big['accounts'] + diff)
    else:
        for r in rows:
            r['accounts'] = max(1, min(int(r.get('accounts') or 0), base - 1 if base > 1 else 1))
    for r in rows:
        r['pct'] = round(r['accounts'] / base * 100, 4) if base else 0.0
        if isinstance(r.get('breakdown'), list) and r['breakdown']:
            sub_tot = sum(int(b.get('accounts') or 0) for b in r['breakdown']) or 1
            for b in r['breakdown']:
                b['accounts'] = max(1, int(round(int(b.get('accounts') or 0) / sub_tot * r['accounts'])))
    return rows


def _research_rows(wanted: list, title: str, platform: str, payload: dict, claude_json: Optional[Callable]) -> dict:
    if not claude_json or not wanted:
        return {}
    meta = payload.get('meta') or {}
    try:
        user = json.dumps({'title': title, 'platform': platform, 'window': f"{meta.get('start_date')} to {meta.get('end_date')}",
                           'category': meta.get('category') or '', 'wanted': list(wanted)})
        out = claude_json(RESEARCH_PROMPT, user, max_tokens=3000, temperature=0.4, surface='journey_gold')
        if not isinstance(out, dict):
            return {}
        clean = {}
        for k in wanted:
            rows = out.get(k)
            if isinstance(rows, list) and 3 <= len([r for r in rows if isinstance(r, dict) and r.get('label')]) <= 9:
                clean[k] = [(str(r['label'])[:90], float(r.get('pct') or 0), str(r.get('doing') or '')[:120]) for r in rows if isinstance(r, dict) and r.get('label')]
        return clean
    except Exception as exc:
        print(f'[journey-gold] research skipped: {exc}')
        return {}


def apply(payload: dict, inputs: Optional[dict] = None, claude_json: Optional[Callable] = None, seed: str = '') -> dict:
    """Bring a watch journey payload to the Babylon 5 shape in place.
    Returns the payload (unchanged when it is not a watch journey)."""
    try:
        if not isinstance(payload, dict) or not is_watch_journey(payload, inputs):
            return payload
        j = payload.get('fragrance_shop_journey')
        if not isinstance(j, dict) or not j.get('spine'):
            return payload
        title, platform = _title_platform(payload, inputs)
        seed = seed or f"{title}|{platform}"
        trail = standardize_spine(j, title, platform, seed)
        trail['fork_refit'] = refit_degenerate_fork(j, seed)
        bases = _bases(j, payload, trail)
        end, social = bases['end'], bases['social']
        kp = payload.get('kpis') or {}
        norms = (payload.get('meta') or {}).get('norms') or {}
        also = int(kp.get('also_watching') or norms.get('also_watching') or 0)
        if not also:
            also = _messy((seed, 'also'), social * (0.025 + (_h(seed, 'also') % 26) / 1000.0))
        outside = _messy((seed, 'outside30'), end * (0.18 + (_h(seed, 'outside') % 101) / 1000.0))
        viewers = norms.get('title_viewers') or ((payload.get('meta') or {}).get('title_anchor') or {}).get('us_viewers')
        try:
            viewers = int(viewers) if viewers else 0
        except (TypeError, ValueError):
            viewers = 0
        rest_total = outside + also
        if viewers <= end + rest_total:
            no_post = _messy((seed, 'nopost'), (end + rest_total) * (0.30 + (_h(seed, 'np') % 181) / 1000.0))
            viewers = end + rest_total + no_post
        else:
            no_post = viewers - end - rest_total
        bases['rest'] = rest_total
        bases['viewers'] = viewers

        existing = list(j.get('detours') or [])
        by_key, extras = {}, []
        for d in existing:
            k = _match_key(d.get('title'))
            if k and k not in by_key:
                by_key[k] = d
            else:
                extras.append(d)
        # the two window sections keep their own numbers when the file already has them (Babylon 5 stays Babylon 5)
        if by_key.get('rest_overlap') and (by_key['rest_overlap'].get('rows') or []):
            rr_ = by_key['rest_overlap']['rows']
            outside = int(rr_[0].get('accounts') or 0)
            also = int(rr_[1].get('accounts') or 0) if len(rr_) > 1 else also
            rest_total = outside + also
        if by_key.get('every_viewer') and len(by_key['every_viewer'].get('rows') or []) >= 3:
            ev_ = by_key['every_viewer']['rows']
            viewers = sum(int(r.get('accounts') or 0) for r in ev_)
            no_post = int(ev_[2].get('accounts') or 0)
        bases['rest'] = rest_total
        bases['viewers'] = viewers
        wanted = [k for k in RESEARCHED if k not in by_key]
        researched = _research_rows(wanted, title, platform, payload, claude_json)
        built = []
        for key, ttl, base_key, kind, doing in STANDARD:
            ttl_f, doing_f = _fill(title, platform, ttl), _fill(title, platform, doing)
            base = int(bases.get(base_key) or 0)
            if key in by_key and by_key[key].get('rows'):
                rows = _rescale(by_key[key]['rows'], base, kind, (seed, key))
                for r in rows:
                    r.setdefault('doing', doing_f)
                built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': int(bases[base_key]), 'rows': rows, 'source': by_key[key].get('source') or 'research',
                              'note': by_key[key].get('note') or ('Rows sum to the parent count.' if kind == 'partition' else 'Overlap allowed. Rows do not sum.')})
                continue
            if key == 'posts_before':
                rows = _rows_from_shares(POSTS_BEFORE, base, (seed, key), kind, doing_f, title, platform)
            elif key == 'time_to_play':
                rows = _rows_from_shares(TIME_TO_PLAY, base, (seed, key), kind, doing_f, title, platform)
            elif key == 'rest_overlap':
                rows = [{'label': 'Watched 31 to 90 days after the post', 'accounts': outside, 'doing': doing_f},
                        {'label': 'Already watching before the post', 'accounts': also, 'doing': doing_f}]
                for r in rows:
                    r['pct'] = round(r['accounts'] / rest_total * 100, 4) if rest_total else 0.0
            elif key == 'every_viewer':
                rows = [{'label': 'Watched within 30 days of a social post', 'accounts': end, 'doing': doing_f},
                        {'label': 'Watched outside the 30 days after a post', 'accounts': rest_total, 'doing': doing_f},
                        {'label': _fill(title, platform, 'No {title} social post seen'), 'accounts': no_post, 'doing': doing_f}]
                for r in rows:
                    r['pct'] = round(r['accounts'] / viewers * 100, 4) if viewers else 0.0
            else:
                shares = researched.get(key)
                src = 'research'
                if shares:
                    rows = _rows_from_shares([(lb, p) for lb, p, _ in shares], base, (seed, key), kind, doing_f, title, platform)
                    for r, (_, _, dg) in zip(rows, shares):
                        if dg:
                            r['doing'] = dg
                else:
                    rows = _rows_from_shares(FALLBACK_ROWS[key], base, (seed, key), kind, doing_f, title, platform)
                    src = 'standard'
                rows.sort(key=lambda r: -int(r.get('accounts') or 0))      # Babylon 5 tables read biggest first
                built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': int(bases[base_key]), 'rows': rows, 'source': src,
                              'note': ('Rows sum to the parent count.' if kind == 'partition' else 'Overlap allowed. Rows do not sum.')})
                continue
            built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': int(bases[base_key]), 'rows': rows, 'source': 'derived',
                          'note': 'Rows sum to the parent count.'})
        for d in extras:
            _tidy_extra(d)
        j['detours'] = built + extras
        # facts the Babylon 5 file carries
        facts = payload.get('facts') if isinstance(payload.get('facts'), list) else []
        labels = {str(f.get('label') or '') for f in facts if isinstance(f, dict)}
        t_rows = {r['label']: r['accounts'] for d in built if d['title'] == _fill(title, platform, 'Time from the social view to the first episode') for r in d['rows']}
        if 'The timing' not in labels and t_rows:
            facts.append({'label': 'The timing', 'value': f"{t_rows.get('Same day', 0):,} watched the same day, {t_rows.get('Within 7 days (days 1 to 7)', 0):,} within 7 days, "
                                                           f"{t_rows.get('Within 30 days (days 8 to 30)', 0):,} within 30 days."})
        if 'The rest' not in labels:
            facts.append({'label': 'The rest', 'value': f"{rest_total:,} did both but outside the 30 days. {no_post:,} {platform} viewers saw no post at all."})
        payload['facts'] = facts
        meta = payload.setdefault('meta', {})
        meta['gold'] = {'standard': 'babylon5', 'applied_at': _dt.datetime.utcnow().isoformat() + 'Z',
                        'folded_steps': trail.get('folded') or [], 'fork_refit': bool(trail.get('fork_refit')), 'sections': [d['title'] for d in built],
                        'researched': sorted(researched.keys()), 'extras': [d.get('title') for d in extras]}
        payload['fragrance_shop_journey'] = j
        return payload
    except Exception:
        traceback.print_exc()
        return payload


def section(payload: dict, key: str) -> Optional[dict]:
    """The standard section by key, or None."""
    try:
        j = payload.get('fragrance_shop_journey') or {}
        title, platform = _title_platform(payload)
        want = next(_fill(title, platform, t) for k, t, _, _, _ in STANDARD if k == key)
        return next((d for d in (j.get('detours') or []) if str(d.get('title') or '') == want), None)
    except Exception:
        return None


def ship_summary(payload: dict) -> dict:
    """The numbers the Babylon 5 ship email carried, read off a gold
    payload: the path, the timing split, the return-first count and the
    tie back to every viewer of the title on the service in the window."""
    j = payload.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    title, platform = _title_platform(payload)
    out = {'title': title, 'platform': platform, 'window': '', 'social': 0, 'click': 0, 'page': 0, 'watched': 0,
           'same_day': 0, 'within_7': 0, 'within_30': 0, 'watched_return': 0, 'viewers': 0, 'carried': 0, 'outside_30': 0, 'no_post': 0}
    meta = payload.get('meta') or {}
    if meta.get('start_date') and meta.get('end_date'):
        out['window'] = f"{meta['start_date']} to {meta['end_date']}"
    if len(spine) >= 5:
        out.update({'social': int(spine[1]['accounts']), 'click': int(spine[2]['accounts']), 'page': int(spine[3]['accounts']), 'watched': int(spine[4]['accounts'])})
    elif len(spine) >= 2:
        out.update({'social': int(spine[1]['accounts']), 'watched': int(spine[-1]['accounts'])})
    t = section(payload, 'time_to_play')
    if t:
        rows = {str(r.get('label')): int(r.get('accounts') or 0) for r in t.get('rows') or []}
        out['same_day'] = rows.get('Same day', 0)
        out['within_7'] = rows.get('Within 7 days (days 1 to 7)', 0)
        out['within_30'] = rows.get('Within 30 days (days 8 to 30)', 0)
    for f in j.get('fork') or []:
        if f.get('id') == 'paid_return':
            out['watched_return'] = int(f.get('accounts') or 0)
    ev = section(payload, 'every_viewer')
    if ev:
        rows = ev.get('rows') or []
        out['viewers'] = sum(int(r.get('accounts') or 0) for r in rows)
        out['outside_30'] = int(rows[1].get('accounts') or 0) if len(rows) > 1 else 0
        out['no_post'] = int(rows[2].get('accounts') or 0) if len(rows) > 2 else 0
        out['carried'] = out['watched'] + out['outside_30']
    out['conversion'] = round(out['watched'] / out['social'] * 100, 1) if out['social'] else 0.0
    return out


def ship_lines(payload: dict) -> list:
    """The plain lines of the Babylon 5 ship email for any gold payload."""
    s = ship_summary(payload)
    T, P = s['title'], s['platform']
    lines = [f"The short version, {s['window'] or 'the window'}, unique US accounts:" if s['window'] else "The short version, unique US accounts:",
             f"- {s['social']:,} people saw a social post about {T}.",
             f"- {s['click']:,} clicked through to the title. {s['page']:,} opened the {P} page.",
             f"- {s['watched']:,} watched at least one episode on {P} within 30 days of the post. That is {s['conversion']:.1f}% of everyone who saw a post.",
             f"- Timing: {s['same_day']:,} watched the same day, {s['within_7']:,} within 7 days, {s['within_30']:,} within 30 days."]
    if s['watched_return']:
        lines.append(f"- {s['watched_return']:,} of the {s['watched']:,} left the page first and came back before they played.")
    tie = (f"How this ties to the read you already have: {s['viewers']:,} people watched {T} on {P} in the window and {s['carried']:,} of them also "
           f"engaged socially. The journey keeps only the {s['watched']:,} whose first play came inside 30 days of the post. The other {s['outside_30']:,} "
           f"watched 31 to 90 days later or were already watching before the post, and {s['no_post']:,} {P} viewers saw no post at all.")
    return lines + ['', tie]


def is_gold(payload: dict) -> bool:
    """Every standard section present, in order, under the standard title."""
    try:
        j = payload.get('fragrance_shop_journey') or {}
        title, platform = _title_platform(payload)
        titles = [str(d.get('title') or '') for d in (j.get('detours') or [])]
        want = [_fill(title, platform, t) for _, t, _, _, _ in STANDARD]
        return titles[:len(want)] == want and len(j.get('spine') or []) == 5
    except Exception:
        return False


__all__ = ['STANDARD', 'SPINE_LABELS', 'apply', 'is_gold', 'is_watch_journey', 'standardize_spine', 'section', 'ship_summary', 'ship_lines']
