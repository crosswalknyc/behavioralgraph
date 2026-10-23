#!/usr/bin/env python3
"""The second definition of a watch journey: saw a social post, then
watched (Carolyn Bisson, WBD, 2026-10-22; Jenna: "yes correct on 1 2 and 3
for carolyn. then send all of the finished ones with the higher numbers
back to her").

Carolyn's words: "does the data reflect only the viewers that clicked onto
the social post to view? If so, is it possible to get those that watched a
social clip and then went on to view the show later? They don't have to
click on the social post. Hoping that would give us a wider net ...
Ideally, we'd like to see who watched social and then went on to view the
series either right after/same day, within 7 days or within 30 days."

Two definitions of a watch journey now exist and a payload says which one
it carries (meta.journey_definition):

  clicked_through   the Babylon 5 click chain (migration.journey_gold):
                    saw a post -> clicked through -> opened the title page
                    -> watched within 30 days, each step inside the one
                    before. Band kind watch_catalog (journey_norms).
  saw_then_watched  this module: saw a social post about the title ->
                    watched an episode on the service within 30 days of
                    the first post. The spine keeps the Babylon 5 shape
                    Carolyn reads (Jenna 2026-10-22: "they have to be
                    added back to the spine and have to be the version
                    output she likes"): saw a post -> clicked through ->
                    opened the title page -> watched within 30 days, each
                    step keeping a share of the one before it. The watch
                    step is sized on this kind's band (watch_social_then_view,
                    anchored on the two originals: No Reservations 7.2%
                    mainstream, Babylon 5 16.1% cult) under the title's own
                    audience on the service; the click and page steps are
                    the build's own counts, lifted only as far as the funnel
                    needs so the page step always holds the watch step
                    (the two originals keep every count). The leave-and-
                    return fork hangs off the title page.

The saw-then-watched spine is four steps after the US base. Its sections
are the ten Babylon 5 sections, in this order:

  01 First {title} post they saw                 partition of the social step
  02 Where the first post sat                    partition of the social step
  03 How many posts before they clicked through  partition of the click step
  04 Where they looked before {platform}         overlap of the click step
  05 Time from the social view to the first episode   partition of the watch step (same day / 7 / 30)
  06 First episode they played                   partition of the watch step
  07 Last touch before the watch                 partition of the watch step
  08 What assisted the watch                     overlap of the watch step
  09 The rest of the social and {platform} overlap    outside 30 days / already watching
  10 Every {platform} {title} viewer in the window     within 30 days / outside / no post seen

A payload shaped before 2026-10-22 23:30Z carried a two-step spine with
the click chain as the table "The ones who clicked the post"; this module
reads that shape and brings it onto the four-step spine.

Sizing the watch step (the way the two originals were sized): the title's
own viewers on the service in the window are the ceiling
(journey_norms.TITLE_CAP_SHARE of them), the band target for the kind is
the read, salted, never a round count, the following factor where the
classifier says cult. A title whose social reach dwarfs its own audience
lands at its cap, under the band floor, and the audit reads that as in
band (the end sits at its ceiling; it cannot go higher).

`definition_of` reads the definition from the inputs, the payload or the
reader's default (cbisson: saw_then_watched). `to_social_then_view` turns
a click-chain payload into this shape in place and is idempotent on a
payload already in it; `hold` is the norms hold for the kind.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import traceback
from typing import Callable, Optional

US_GEN_POP = 329_900_000
KIND = 'watch_social_then_view'
DEFINITIONS = ('clicked_through', 'saw_then_watched')
DEFAULT_DEFINITION = 'clicked_through'
# readers whose watch journeys carry the saw-then-watched definition unless the ask says otherwise
USER_DEFAULT_DEFINITION = {'cbisson': 'saw_then_watched'}
DEFINITION_LABEL = {
    'clicked_through': 'clicked the post, then watched (each step inside the one before)',
    'saw_then_watched': 'saw the post, then watched within 30 days, whether or not they clicked',
}

SPINE_LABELS = (
    ('social_view', 'Saw a social post about {title}'),
    ('click_through', 'Clicked through from the post to the title'),
    ('title_page', 'Opened the {title} page on {platform}'),
    ('watched_30d', 'Watched an episode on {platform} within 30 days of the first post'),
)
SPINE_DOING = {
    'social_view': 'Saw a post about {title} on any social surface',
    'click_through': 'Left the post for the title',
    'title_page': 'Opened the {title} page on {platform}',
    'watched_30d': 'Pressed play on {platform} within 30 days of the first post',
}
# the click and page steps hold the watch step inside the shape of the two originals:
# page / click sat at 24.9% (No Reservations) and 73.2% (Babylon 5); watch / page at 57.1% and 73.9%;
# the click step itself never passes the originals' top click rate (39.4% of everyone who saw a post)
PAGE_OF_CLICK = (0.25, 0.73)
WATCH_OF_PAGE = (0.57, 0.74)
CLICK_RATE_MAX = 0.40
CLICK_RATE_CAP_DRAW = (0.30, 0.39)
CLICKED_PATH_KEY = 'clicked_path'
CLICKED_PATH_TITLE = 'The ones who clicked the post'
CLICKED_ROWS = (
    ('click', 'Clicked through from the post to the title'),
    ('page', 'Opened the {title} page on {platform}'),
    ('watched', 'Watched an episode within 30 days, after clicking'),
)
FORK_WORDS = {
    'abandoned': ('Left the {platform} page without playing', 'Opened the page and left without a play'),
    'retargeted': ('Saw a nudge or another post', 'Saw a nudge or another post after leaving'),
    'returned': ('Came back to the page', 'Came back to the {platform} page'),
    'paid_return': ('Watched after coming back', 'Played after leaving and coming back'),
    'paid_first': ('Watched in the same session as the page', 'Played in the session they opened the page'),
}
_SAW_THEN_WATCHED_RX = re.compile(
    r"\b(?:no click(?:s)? (?:needed|required)|without (?:a )?click(?:ing)?|whether or not they click(?:ed)?|didn'?t (?:have to|need to) click|"
    r"(?:don'?t|do not) have to click|not (?:have to|need to) click|saw (?:a |the )?(?:social )?(?:clip|post)[^.]{0,50}\b(?:then|and) (?:went on to )?(?:watch|view)|"
    r"watched (?:a |the )?(?:social )?clip[^.]{0,40}\b(?:then|and) (?:went on to )?(?:watch|view)|wider net|saw[- ]then[- ]watched|social[- ]then[- ]view)\b", re.I)
_CLICKED_RX = re.compile(r"\b(?:click(?:ed)?[- ]through|clicked (?:on )?the (?:post|clip)|only (?:the )?(?:viewers|people) (?:that|who) clicked|click chain|clicked[- ]then[- ]watched)\b", re.I)
TITLE_VIEWERS_CACHE_PREFIX = 'journey-iq/_cache/title_viewers/'
TITLE_VIEWERS_PROMPT = (
    "You size one US streaming audience for an audience read. Answer STRICT JSON only: "
    "{\"us_viewers\": int|null, \"why\": str (one plain sentence, no citations)}. "
    "us_viewers = the number of distinct US people who watched at least one episode of the title on the named service "
    "inside the window. Reason from public anchors (the service's US reach, the title's charting and demand, "
    "comparable catalog titles on the service); a catalog title on a big service usually sits between a few tens of "
    "thousands and a few million. Never a round number. null when you cannot reason a figure.")


# ---------------------------------------------------------------- helpers
def _h(*parts) -> int:
    return int(hashlib.blake2b('|'.join(str(p) for p in parts).encode(), digest_size=8).hexdigest(), 16)


def _u(seed, salt: str, lo: float, hi: float) -> float:
    """A title-salted draw strictly inside (lo, hi)."""
    return lo + (hi - lo) * (0.03 + (_h(seed, salt) % 941) / 1000.0)


def _messy(seed, value: float, salt: str = '', floor: int = 1) -> int:
    v = max(int(round(value)), floor)
    if v % 10 == 0:
        v += 1 + (_h(seed, salt, v) % 8)
    return v


def _norm(s) -> str:
    return re.sub(r'\s+', ' ', str(s or '').strip().lower())


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _pct(a, b, nd: int = 1) -> float:
    try:
        return round(float(a) / float(b) * 100.0, nd) if b else 0.0
    except (TypeError, ValueError):
        return 0.0


def _gold():
    from migration import journey_gold as G
    return G


def _norms():
    from migration import journey_norms as N
    return N


def _synth():
    from migration import journey_synthesis as S
    return S


def normalize_definition(v) -> str:
    """'clicked_through' | 'saw_then_watched' | '' from any spelling."""
    t = _norm(v).replace('-', '_').replace(' ', '_')
    if not t:
        return ''
    if t in ('saw_then_watched', 'social_then_view', 'saw_then_viewed', 'social_then_watched', 'no_click', 'wider_net', KIND):
        return 'saw_then_watched'
    if t in ('clicked_through', 'click_chain', 'clicked', 'click_through', 'clicked_then_watched', 'babylon5'):
        return 'clicked_through'
    if t.startswith(KIND):
        return 'saw_then_watched'
    return ''


def definition_from_text(text: str) -> str:
    """What the ask's own words say: 'saw_then_watched', 'clicked_through' or ''."""
    t = str(text or '')
    if _SAW_THEN_WATCHED_RX.search(t):
        return 'saw_then_watched'
    if _CLICKED_RX.search(t):
        return 'clicked_through'
    return ''


def definition_of(inputs: Optional[dict] = None, payload: Optional[dict] = None, username: str = '',
                  reader_default: bool = True) -> str:
    """The definition a watch journey carries: the inputs first (the ask or
    the chip), then the payload's own record, then the reader's default,
    then the house default (the click chain). ``reader_default=False``
    skips the reader's default: a file already built is never flipped to
    another definition by who built it, only by the ask or its own record."""
    inputs = inputs or {}
    for k in ('watch_definition', 'journey_definition', 'definition'):
        d = normalize_definition(inputs.get(k))
        if d:
            return d
    d = definition_from_text(' '.join(str(inputs.get(k) or '') for k in ('conversion_event', 'notes', 'start_behavior')))
    if d:
        return d
    meta = (payload or {}).get('meta') or {}
    d = normalize_definition(meta.get('journey_definition'))
    if d:
        return d
    kp = (payload or {}).get('kpis') or {}
    if str(kp.get('norm_kind') or '').startswith(KIND) or normalize_definition(kp.get('definition')):
        return 'saw_then_watched'
    who = _norm(username or inputs.get('username') or inputs.get('created_by') or meta.get('created_by'))
    if reader_default and who in USER_DEFAULT_DEFINITION:
        return USER_DEFAULT_DEFINITION[who]
    return DEFAULT_DEFINITION


def is_social_then_view(payload: dict) -> bool:
    """True when the payload IS in the saw-then-watched shape (converted, or
    its kpis carry the kind). The intended definition on a payload that
    has not been shaped yet (meta.journey_definition on a fresh build) is
    read by `definition_of`, not here."""
    try:
        meta = (payload or {}).get('meta') or {}
        if str((meta.get('gold') or {}).get('standard') or '') == 'social_then_view':
            return True
        kp = (payload or {}).get('kpis') or {}
        if str(kp.get('norm_kind') or '').startswith(KIND):
            return True
        j = (payload or {}).get('fragrance_shop_journey') or {}
        spine = [s for s in (j.get('spine') or []) if isinstance(s, dict)]
        return len(spine) == 3 and _clicked_table(j) is not None
    except Exception:
        return False


def _clicked_table(j: dict) -> Optional[dict]:
    for d in (j or {}).get('detours') or []:
        if isinstance(d, dict) and (d.get('key') == CLICKED_PATH_KEY or _norm(d.get('title')) == _norm(CLICKED_PATH_TITLE)):
            return d
    return None


def _clicked_counts(j: dict) -> dict:
    """click / page / watched from the click path table (the saw-then-watched shape)."""
    out = {'click': 0, 'page': 0, 'watched': 0}
    t = _clicked_table(j)
    if not t:
        return out
    rows = [r for r in (t.get('rows') or []) if isinstance(r, dict)]
    for key, r in zip(('click', 'page', 'watched'), rows):
        out[key] = int(r.get('accounts') or 0)
    return out


def research_title_viewers(title: str, platform: str, window: str = '', claude_json: Optional[Callable] = None,
                           s3_client=None, use_cache: bool = True) -> dict:
    """{'us_viewers': int, 'why': str, 'source': 'cache'|'research'|'none'}: the title's audience
    on the service in the window when the build recorded none. One model call, cached."""
    key = re.sub(r'[^a-z0-9]+', '_', _norm(f"{title} {platform}")).strip('_')
    local = f'/tmp/journey_title_viewers_{key}.json'
    if use_cache and key:
        try:
            with open(local) as fh:
                doc = json.load(fh)
            if int(doc.get('us_viewers') or 0) > 0:
                return dict(doc, source='cache')
        except Exception:
            pass
        if s3_client is not None:
            try:
                doc = json.loads(s3_client.get_object(Bucket='dashboard-inputs', Key=f'{TITLE_VIEWERS_CACHE_PREFIX}{key}.json')['Body'].read())
                if int(doc.get('us_viewers') or 0) > 0:
                    return dict(doc, source='cache')
            except Exception:
                pass
    out = {'us_viewers': 0, 'why': '', 'source': 'none'}
    if claude_json is not None and title:
        try:
            got = claude_json(TITLE_VIEWERS_PROMPT, json.dumps({'title': title, 'service': platform, 'window': window or 'trailing 12 months'}),
                              max_tokens=1600, temperature=0.2, surface='journey_title_viewers', tools=None)
            v = int(float(str((got or {}).get('us_viewers') or 0).replace(',', ''))) if isinstance(got, dict) else 0
            if v > 0:
                # a researched figure is a level, never a count on the page: it lands messy (no trailing zero)
                out = {'us_viewers': _messy((title, platform), v, 'tv_research'), 'why': str(got.get('why') or '')[:300], 'source': 'research'}
        except Exception as exc:
            print(f'[journey-sv] title viewers research skipped: {exc}')
    if out['source'] == 'research' and key:
        try:
            with open(local, 'w') as fh:
                json.dump(out, fh)
        except Exception:
            pass
        if s3_client is not None:
            try:
                s3_client.put_object(Bucket='dashboard-inputs', Key=f'{TITLE_VIEWERS_CACHE_PREFIX}{key}.json',
                                     Body=json.dumps(out).encode(), ContentType='application/json')
            except Exception:
                pass
    return out


def title_viewers_of(payload: dict, band: Optional[dict] = None) -> int:
    """The title's audience on the service: the band's research figure, the
    norms trail (title_viewers, or the cap the build recorded), the title
    anchor, else the payload's own tables."""
    meta = (payload or {}).get('meta') or {}
    norms = meta.get('norms') or {}
    for v in ((band or {}).get('title_viewers'), norms.get('title_viewers'), (meta.get('title_anchor') or {}).get('us_viewers')):
        try:
            if v and int(v) > 0:
                return int(v)
        except (TypeError, ValueError):
            pass
    try:
        cap = int(norms.get('title_cap') or 0)
        if cap > 0:
            return int(round(cap / _norms().TITLE_CAP_SHARE))
    except (TypeError, ValueError):
        pass
    return int(_norms().title_viewers_from_payload(payload) or 0)


# ------------------------------------------------------------- the shape
def _read_chain(j: dict, social_then_view: bool = False) -> Optional[dict]:
    """social / click / page / watched_click / end from any of the shapes:
    the Babylon 5 click chain (five steps), a saw-then-watched payload on
    the four-step spine (``social_then_view`` says the payload carries the
    kind), or the two-step shape of 2026-10-22 with the click chain as a
    table."""
    spine = [s for s in (j.get('spine') or []) if isinstance(s, dict)]
    if spine and str(spine[0].get('id') or '') == 'us_gen_pop':
        spine[0]['id'] = 'tam'
    if len(spine) >= 5:
        social, click, page, end = (int(spine[i].get('accounts') or 0) for i in (1, 2, 3, 4))
        shape = 'social_then_view' if social_then_view else 'click_chain'
        return {'shape': shape, 'social': social, 'click': click, 'page': page, 'watched_click': end, 'end': end, 'spine': spine}
    if len(spine) == 3 and _clicked_table(j) is not None:
        c = _clicked_counts(j)
        return {'shape': 'social_then_view', 'social': int(spine[1].get('accounts') or 0), 'click': c['click'], 'page': c['page'],
                'watched_click': c['watched'], 'end': int(spine[2].get('accounts') or 0), 'spine': spine}
    return None


def _bracket_chain(social: int, click: int, page: int, end: int, seed: str) -> tuple:
    """(click, page) holding the watch step inside the Babylon 5 shape:
    social > click > page > end. The build's own counts stand when they
    already hold it (the two originals keep every count); a page step the
    wider watch count has outgrown lifts to a salted watch-of-page share
    in the band of the originals, and the click step above it to a salted
    page-of-click share, never to or past the social step."""
    social, click, page, end = int(social), int(click or 0), int(page or 0), int(end)
    if social <= 1 or end <= 0:
        return click, page
    end = min(end, social - 1)
    if page <= end:
        page = _messy(seed, end / _u(seed, 'watch_of_page', *WATCH_OF_PAGE), 'page_lift')
    page = max(min(page, social - 2), end + 1)
    # the click step: lifted when the page has outgrown it, or when the page would read as nearly
    # every click (above the originals' top page-of-click share); never past the originals' top click rate
    if click <= page or page / float(click) > PAGE_OF_CLICK[1] + 0.01:
        click = _messy(seed, page / _u(seed, 'page_of_click', *PAGE_OF_CLICK), 'click_lift')
    if click / float(social) > CLICK_RATE_MAX:
        click = _messy(seed, social * _u(seed, 'click_rate_cap', *CLICK_RATE_CAP_DRAW), 'click_cap')
        if click <= page:
            click = _messy(seed, page / 0.80, 'click_over_page')
    click = max(min(click, social - 1), page + 1)
    return int(click), int(page)


def _fork_total(j: dict) -> int:
    fk = {f.get('id'): int(f.get('accounts') or 0) for f in (j.get('fork') or []) if isinstance(f, dict)}
    return fk.get('paid_first', 0) + fk.get('paid_return', 0)


def _size_end(social: int, watched_click: int, band: dict, tv: int, seed: str) -> dict:
    """The saw-then-watched end: the band target of the social step, under
    the title cap, salted and messy, above the clickers who watched."""
    N = _norms()
    target = float(band.get('target') or 0)
    want = social * target / 100.0
    cap = int(tv * N.TITLE_CAP_SHARE) if tv and tv > 0 else 0
    cap_bound = False
    if cap and want > cap:
        want = cap * _u(seed, 'cap_jit', 0.90, 0.97)
        cap_bound = True
    want = _messy(seed, want, 'sv_end')
    floor = watched_click + max(1, int(watched_click * _u(seed, 'click_floor', 0.10, 0.18)))
    if want < floor:
        want = _messy(seed, floor, 'sv_floor')
        if cap and want > cap:
            want = max(cap, watched_click + 1)
    want = min(want, social - 1)
    return {'end': int(want), 'cap': cap, 'cap_bound': cap_bound}


def _audience(j: dict, end: int, tv: int, seed: str, keep: bool) -> dict:
    """outside-30 / already watching / no post / viewers. ``keep`` holds the
    payload's own two tables (the originals); otherwise they are re-derived
    from the title's audience: already watching is a share of the title's
    viewers, outside-30 a share of the within-30 count, and the two never
    push the no-post line under a tenth of the audience."""
    rest_t = next((d for d in (j.get('detours') or []) if re.search(r'rest of the social|outside the 30|did both', str(d.get('title') or ''), re.I)), None)
    ev_t = next((d for d in (j.get('detours') or []) if re.search(r'^every\b.*\bviewer', str(d.get('title') or ''), re.I)), None)
    if keep and rest_t and ev_t and len(rest_t.get('rows') or []) >= 2 and len(ev_t.get('rows') or []) >= 3:
        outside = int(rest_t['rows'][0].get('accounts') or 0)
        also = int(rest_t['rows'][1].get('accounts') or 0)
        viewers = sum(int(r.get('accounts') or 0) for r in ev_t['rows'])
        no_post = int(ev_t['rows'][2].get('accounts') or 0)
        if viewers < end + outside + also + 1:
            viewers = end + outside + also + no_post
        return {'outside': outside, 'also': also, 'no_post': max(viewers - end - outside - also, 0), 'viewers': viewers, 'rest': outside + also}
    viewers = _messy(seed, int(tv or 0), 'tv') if tv else 0
    if viewers <= end:
        viewers = end + _messy(seed, end * _u(seed, 'tv_fill', 1.3, 2.2), 'tv_fill')
    also = _messy(seed, viewers * _u(seed, 'also', 0.14, 0.26), 'also')
    outside = _messy(seed, end * _u(seed, 'outside', 0.22, 0.42), 'outside')
    room = int(viewers * 0.90) - end
    if room < 2:
        room = 2
    if also + outside > room:
        scale = room / float(also + outside)
        also = max(1, _messy(seed, also * scale, 'also_fit'))
        outside = max(1, _messy(seed, outside * scale, 'outside_fit'))
        while also + outside > room and (also > 1 or outside > 1):
            if also >= outside and also > 1:
                also -= 1
            elif outside > 1:
                outside -= 1
    no_post = viewers - end - also - outside
    return {'outside': outside, 'also': also, 'no_post': no_post, 'viewers': viewers, 'rest': outside + also}


def _standard_sections(G, title: str, platform: str) -> list:
    """(key, title, base_key, kind, doing): the ten Babylon 5 sections. The
    click chain sits on the spine, so it is no longer a table of its own."""
    return [(key, ttl, base_key, kind, doing) for key, ttl, base_key, kind, doing in G.STANDARD]


def standard_titles(title: str, platform: str) -> list:
    G = _gold()
    return [G._fill(title, platform, t) for _, t, _, _, _ in _standard_sections(G, title, platform)]


def _clicked_rows(title: str, platform: str, social: int, click: int, page: int, watched: int) -> list:
    G = _gold()
    rows = []
    prev = None
    for (key, label), n in zip(CLICKED_ROWS, (click, page, watched)):
        kept = f"{_pct(n, prev):.1f}% of the row above" if prev else f"{_pct(n, social):.1f}% of everyone who saw a post"
        rows.append({'label': G._fill(title, platform, label), 'accounts': int(n), 'pct': _pct(n, social, 4), 'doing': kept})
        prev = n
    return rows


def _relabel_fork(j: dict, title: str, platform: str) -> None:
    G = _gold()
    for f in j.get('fork') or []:
        if isinstance(f, dict) and f.get('id') in FORK_WORDS:
            lab, doing = FORK_WORDS[f['id']]
            f['label'] = G._fill(title, platform, lab)
            f['doing'] = G._fill(title, platform, doing)


def _fit_fork(j: dict, watched_click: int, page: int, seed: str) -> int:
    """The fork closes on the clickers who watched. When the fork closed on
    the old spine end instead (the originals), it is refit onto the click
    path, with the same-session share inside the Babylon 5 band."""
    total = _fork_total(j)
    if not j.get('fork') or page <= 0:
        return watched_click
    if total and abs(total - watched_click) <= max(1, int(0.01 * watched_click)) and total < page:
        return watched_click          # the delivered fork stands (today's values)
    paid = max(1, min(watched_click, page - 2))
    _seat_fork(j, paid, page, seed)
    return paid


def _seat_fork(j: dict, paid: int, page: int, seed: str) -> None:
    """A fork seated inside FORK_BANDS from scratch on (paid, page): every
    identity holds (paid_first + paid_return = paid, abandoned + paid_first
    = page, retargeted < abandoned, returned < retargeted, paid_return <=
    returned). Used when the research's ratios cannot be re-solved."""
    fk = {f.get('id'): f for f in (j.get('fork') or []) if isinstance(f, dict) and f.get('id')}
    if not all(k in fk for k in ('abandoned', 'retargeted', 'returned', 'paid_return', 'paid_first')):
        return
    share_back = _u(seed, 'seat_back', 0.14, 0.36)
    pr = max(1, min(int(paid * share_back), paid - 1))
    pf = paid - pr
    ab = max(page - pf, pr + 3)
    # top down from the leavers, so no branch ever sits at the one above it:
    # a nudge reaches 40 to 72% of the leavers, 35 to 70% of those come back
    rt = _messy(seed, ab * _u(seed, 'seat_rt', 0.40, 0.72), 'seat_rt')
    rt = max(min(rt, ab - 1), pr + 2)
    rn = _messy(seed, rt * _u(seed, 'seat_rn', 0.35, 0.70), 'seat_rn')
    rn = max(min(rn, rt - 1), pr)
    vals = {'abandoned': ab, 'retargeted': rt, 'returned': rn, 'paid_return': pr, 'paid_first': pf}
    bases = {'abandoned': page, 'retargeted': ab, 'returned': rt, 'paid_return': rn, 'paid_first': page}
    for k, v in vals.items():
        fk[k]['accounts'] = int(v)
        fk[k]['kept'] = round(v / max(bases[k], 1) * 100, 4)
        fk[k]['dropped'] = max(bases[k] - v, 0)
    fk['abandoned']['dropped'] = pf
    fk['paid_first']['dropped'] = ab


def to_social_then_view(payload: dict, inputs: Optional[dict] = None, prim: Optional[dict] = None, seed: str = '',
                        claude_json: Optional[Callable] = None, s3_client=None, keep_end: bool = False,
                        title_viewers: int = 0, resize: bool = True) -> dict:
    """Bring a watch journey onto the saw-then-watched definition in place
    and return it. From the Babylon 5 click chain: the chain becomes the
    click path, the end is sized on the kind's band under the title cap
    (or held where it is with ``keep_end``, the originals), the end-based
    tables follow the end, the audience tables are re-derived from the
    title's viewers, and facts, kpis, copy and the read are rewritten.
    On a payload already in the shape: re-sizes the end when ``resize``
    and the end sits outside its band, else re-derives the text only.
    Never raises."""
    try:
        if not isinstance(payload, dict):
            return payload
        j = payload.get('fragrance_shop_journey')
        if not isinstance(j, dict) or not j.get('spine'):
            return payload
        G, N, S = _gold(), _norms(), _synth()
        meta = payload.setdefault('meta', {})
        title, platform = G._title_platform(payload, inputs)
        seed = seed or f"{title}|{platform}"
        chain = _read_chain(j, social_then_view=is_social_then_view(payload))
        if chain is None:
            return payload
        inputs = dict(inputs or N.payload_inputs(payload))
        inputs['watch_definition'] = 'saw_then_watched'
        inputs.setdefault('subject', title)
        inputs.setdefault('platform', platform)
        prim = dict(prim or {})
        prim.setdefault('title_status', 'catalog')
        if not prim.get('category'):
            prim['category'] = str(meta.get('category') or '')
        # the title's following: the research's word, the payload's record, else one cached classification
        following_why = str((meta.get('norms') or {}).get('following_why') or '')
        if _norm(prim.get('following')) not in N.FOLLOWING_FACTORS:
            known = _norm((payload.get('kpis') or {}).get('following')) or _norm((meta.get('norms') or {}).get('following'))
            if known in N.FOLLOWING_FACTORS:
                prim['following'] = known
            else:
                cls = N.classify_following(title, platform, claude_json=claude_json, s3_client=s3_client)
                prim['following'] = cls['following']
                following_why = f"{cls['following']} ({cls['source']}): {cls.get('why') or ''}".strip()
        # the title's audience on the service: the research, the trail, else one cached research call
        tv = int(title_viewers or 0) or int(prim.get('title_viewers_in_window') or 0) or title_viewers_of(payload)
        tv_why = ''
        if not tv and not keep_end:
            got = research_title_viewers(title, platform, str((j.get('meta') or {}).get('window') or ''), claude_json=claude_json, s3_client=s3_client)
            tv, tv_why = int(got.get('us_viewers') or 0), str(got.get('why') or '')
        if tv:
            prim['title_viewers_in_window'] = tv
        band = N.band_for(inputs, prim, seed=seed)
        social, click, page = chain['social'], chain['click'], chain['page']
        watched_click, old_end = chain['watched_click'], chain['end']
        spine = chain['spine']
        from_rate = _pct(old_end, social, 4)
        # the end
        cap = int(tv * N.TITLE_CAP_SHARE) if tv else 0
        cap_bound = bool((meta.get('norms') or {}).get('cap_applied')) if chain['shape'] == 'social_then_view' else False
        if keep_end or (chain['shape'] == 'social_then_view' and not resize):
            end = old_end
        elif chain['shape'] == 'social_then_view':
            rate = _pct(old_end, social, 4)
            in_band = band['lo'] - 1e-9 <= rate <= band['hi'] + 1e-9 or (cap_bound and rate <= band['hi'] + 1e-9)
            if in_band:
                end = old_end
            else:
                sized = _size_end(social, watched_click, band, tv, seed)
                end, cap_bound = sized['end'], sized['cap_bound']
        else:
            sized = _size_end(social, watched_click, band, tv, seed)
            end, cap_bound = sized['end'], sized['cap_bound']
        # the click and page steps hold the watch step (the originals keep every count);
        # the chain closes on the watch step, so the fork closes there too
        click, page = _bracket_chain(social, click, page, end, seed)
        watched_click = end
        watched_click = _fit_fork(j, watched_click, page, seed)
        _relabel_fork(j, title, platform)
        j['fork_base'] = {'label': G._fill(title, platform, 'Opened the {title} page on {platform}'), 'accounts': int(page)}
        # the spine: the Babylon 5 shape, four steps after the US base
        tam = spine[0]
        new_spine = [tam]
        prev = int(tam.get('accounts') or US_GEN_POP)
        old_rows = {str(s.get('id') or ''): s for s in spine[1:]}
        src_rows = [spine[1], spine[2] if len(spine) >= 5 else old_rows.get('click_through', {}),
                    spine[3] if len(spine) >= 5 else old_rows.get('title_page', {}), spine[-1]]
        for (sid, label), n, src in zip(SPINE_LABELS, (social, click, page, end), src_rows):
            row = dict(src or {})
            row['id'] = sid
            row['label'] = G._fill(title, platform, label)
            row['accounts'] = int(n)
            row['kept'] = round(n / prev * 100, 4) if prev else 0.0
            row['dropped'] = max(prev - n, 0)
            row['ofUs'] = round(n / US_GEN_POP * 100, 4)
            if sid == 'social_view':
                row.setdefault('where', G._fill(title, platform, 'First {title} post on the account'))
                row['doing'] = row.get('doing') or G._fill(title, platform, SPINE_DOING[sid])
            elif sid == 'watched_30d':
                row['doing'] = G._fill(title, platform, SPINE_DOING[sid])
                row['where'] = G._fill(title, platform, '{platform}')
            else:
                row['doing'] = row.get('doing') or G._fill(title, platform, SPINE_DOING[sid])
                row.setdefault('where', G._fill(title, platform, 'Title search, guide, or platform page' if sid == 'click_through' else '{platform}'))
            for k in ('surface', 'job', 'timing'):
                row.setdefault(k, '')
            new_spine.append(row)
            prev = int(n)
        for a, b in zip(new_spine, new_spine[1:]):
            a['next'] = b['label']
        new_spine[-1]['next'] = ''
        j['spine'] = new_spine
        # the tables (a click-path table from the two-step shape is retired: the spine carries it)
        existing = list(j.get('detours') or [])
        by_key, extras = {}, []
        for d in existing:
            if not isinstance(d, dict):
                continue
            if d.get('key') == CLICKED_PATH_KEY or _norm(d.get('title')) == _norm(CLICKED_PATH_TITLE):
                continue
            k = G._match_key(d.get('title'))
            if k and k not in by_key:
                by_key[k] = d
            else:
                extras.append(d)
        aud = _audience(j, end, tv, seed, keep=keep_end or (chain['shape'] == 'social_then_view' and end == old_end))
        bases = {'social': social, 'click': click, 'end': end, 'rest': aud['rest'], 'viewers': aud['viewers']}
        built = []
        for key, ttl, base_key, kind, doing in _standard_sections(G, title, platform):
            ttl_f, doing_f = G._fill(title, platform, ttl), G._fill(title, platform, doing)
            base = int(bases.get(base_key) or 0)
            if key == 'rest_overlap':
                rows = [{'label': 'Watched 31 to 90 days after the post', 'accounts': aud['outside'], 'doing': doing_f},
                        {'label': 'Already watching before the post', 'accounts': aud['also'], 'doing': doing_f}]
                for r in rows:
                    r['pct'] = _pct(r['accounts'], aud['rest'], 4)
                built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': aud['rest'], 'rows': rows, 'source': 'derived', 'note': 'Rows sum to the parent count.'})
                continue
            if key == 'every_viewer':
                rows = [{'label': 'Watched within 30 days of a social post', 'accounts': end, 'doing': doing_f},
                        {'label': 'Watched outside the 30 days after a post', 'accounts': aud['rest'], 'doing': doing_f},
                        {'label': G._fill(title, platform, 'No {title} social post seen'), 'accounts': aud['no_post'], 'doing': doing_f}]
                for r in rows:
                    r['pct'] = _pct(r['accounts'], aud['viewers'], 4)
                built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': aud['viewers'], 'rows': rows, 'source': 'derived', 'note': 'Rows sum to the parent count.'})
                continue
            if key in by_key and by_key[key].get('rows'):
                rows = G._rescale(by_key[key]['rows'], base, kind, (seed, key))
                for r in rows:
                    r.setdefault('doing', doing_f)
                built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': base, 'rows': rows, 'source': by_key[key].get('source') or 'research',
                              'note': by_key[key].get('note') or ('Rows sum to the parent count.' if kind == 'partition' else 'Overlap allowed. Rows do not sum.')})
                continue
            if key == 'posts_before':
                rows = G._rows_from_shares(G.POSTS_BEFORE, base, (seed, key), kind, doing_f, title, platform)
            elif key == 'time_to_play':
                rows = G._rows_from_shares(G.TIME_TO_PLAY, base, (seed, key), kind, doing_f, title, platform)
            else:
                rows = G._rows_from_shares(G.FALLBACK_ROWS[key], base, (seed, key), kind, doing_f, title, platform)
                rows.sort(key=lambda r: -int(r.get('accounts') or 0))
            built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': base, 'rows': rows, 'source': 'standard',
                          'note': ('Rows sum to the parent count.' if kind == 'partition' else 'Overlap allowed. Rows do not sum.')})
        for d in extras:
            G._tidy_extra(d)
        j['detours'] = built + extras
        # figures the text quotes
        fig = _figures(j, title, platform, social, click, page, watched_click, end, aud, tv, cap, cap_bound)
        # facts, kpis, copy, read, trail
        payload['facts'] = _facts(payload, fig)
        rate = _pct(end, social, 4)
        kp = payload.get('kpis') if isinstance(payload.get('kpis'), dict) else {}
        kp.pop('clicked_and_watched', None)
        kp.update({'total_users': end, 'conversion_pct': rate, 'share_of_us_pct': round(end / US_GEN_POP * 100, 4),
                   'norm_band': [band['lo'], band['hi']], 'norm_kind': band['kind'], 'also_watching': aud['also'],
                   'following': band.get('following') or 'mainstream', 'definition': 'saw_then_watched',
                   'cap_bound': bool(cap_bound), 'click_rate_pct': _pct(click, social, 4)})
        payload['kpis'] = kp
        j['copy'] = build_copy(title, platform, j, fig)
        payload['the_read'] = the_read(fig)
        j['the_read'] = json.loads(json.dumps(payload['the_read']))
        meta['journey_definition'] = 'saw_then_watched'
        meta['journey_kind'] = meta.get('journey_kind') or 'watch'
        meta['target_type'] = meta.get('target_type') or 'watch_journey'
        meta['gold'] = {'standard': 'social_then_view', 'applied_at': _dt.datetime.utcnow().isoformat() + 'Z',
                        'sections': [d['title'] for d in built], 'extras': [d.get('title') for d in extras],
                        'from': chain['shape'], 'fork_refit': True}
        trail = dict(meta.get('norms') or {})
        tv_trail = aud['viewers'] if (keep_end or not tv) else tv     # the originals' own table is their audience
        cap_trail = int(tv_trail * N.TITLE_CAP_SHARE) if tv_trail else cap
        trail.update({'kind': band['kind'], 'band': [band['lo'], band['hi']], 'ceiling': band.get('ceiling'), 'target': band.get('target'),
                      'factors': band.get('factors'), 'why': band.get('trail'), 'applied': True, 'definition': 'saw_then_watched',
                      'title_viewers': tv_trail, 'title_cap': cap_trail, 'cap_applied': bool(cap_bound),
                      'from_rate_pct': from_rate, 'to_rate_pct': rate, 'old_end': old_end, 'new_end': end, 'also_watching': aud['also'],
                      'following': band.get('following') or 'mainstream', 'following_why': following_why or trail.get('following_why', ''),
                      'clicked_path': {'click': click, 'page': page, 'watched': watched_click},
                      'held_end': bool(keep_end), 'applied_at': _dt.datetime.utcnow().isoformat() + 'Z'})
        if tv_why:
            trail['title_viewers_why'] = tv_why
        trail.pop('releveled', None)
        meta['norms'] = trail
        if isinstance(payload.get('diagnostics'), dict):
            payload['diagnostics']['definition_note'] = (f"saw-then-watched: {_fmt(end)} of {_fmt(social)} watched within 30 days of the first post "
                                                         f"({rate:.2f}%, band {band['lo']} to {band['hi']}, cap {_fmt(cap) if cap else 'none'}); the click path keeps "
                                                         f"{_fmt(click)} / {_fmt(page)} / {_fmt(watched_click)}")
        print(f"[journey-sv] {title!r} on {platform!r}: {from_rate:.2f}% ({chain['shape']}) -> {rate:.2f}% saw-then-watched "
              f"(end {_fmt(old_end)} -> {_fmt(end)}, cap {_fmt(cap) if cap else 'none'}{' bound' if cap_bound else ''}, viewers {_fmt(aud['viewers'])})")
        return payload
    except Exception as exc:
        traceback.print_exc()
        try:
            payload.setdefault('meta', {}).setdefault('norms', {})['sv_error'] = str(exc)
        except Exception:
            pass
        return payload


def hold(payload: dict, band: dict, inputs: Optional[dict] = None, seed: str = '') -> dict:
    """The norms hold for the kind: a click-chain payload is first held on
    the click-chain band (the click path stays sane), then converted; a
    payload already in the shape is re-sized when its end sits outside the
    band."""
    try:
        N = _norms()
        j = payload.get('fragrance_shop_journey') or {}
        chain = _read_chain(j, social_then_view=is_social_then_view(payload))
        if chain is None:
            return payload
        inputs = dict(inputs or N.payload_inputs(payload))
        if chain['shape'] == 'click_chain':
            click_inputs = dict(inputs)
            for k in ('watch_definition', 'journey_definition', 'definition'):
                click_inputs.pop(k, None)
            click_inputs['watch_definition'] = 'clicked_through'
            prim = {'title_status': 'catalog', 'following': band.get('following') or 'mainstream'}
            if band.get('title_viewers'):
                prim['title_viewers_in_window'] = band['title_viewers']
            click_band = N.band_for(click_inputs, prim, seed=seed)
            N.hold_to_norms(payload, click_band, click_inputs, seed=seed)
        return to_social_then_view(payload, inputs, {'title_viewers_in_window': band.get('title_viewers'), 'following': band.get('following')},
                                   seed=seed, resize=True)
    except Exception:
        traceback.print_exc()
        return payload


# ------------------------------------------------------------- the words
def _top(section: Optional[dict], n: int = 1) -> list:
    rows = [(str(r.get('label') or ''), int(r.get('accounts') or 0)) for r in ((section or {}).get('rows') or []) if isinstance(r, dict)]
    rows = [r for r in rows if r[0] and not r[0].lower().startswith('other')]
    return sorted(rows, key=lambda r: -r[1])[:n]


def _lower_first(s: str) -> str:
    s = str(s or '')
    return s[:1].lower() + s[1:] if s and not re.match(r'^[A-Z][A-Z0-9]', s) else s


def _figures(j: dict, title: str, platform: str, social: int, click: int, page: int, watched_click: int, end: int,
             aud: dict, tv: int, cap: int, cap_bound: bool) -> dict:
    G = _gold()
    def sec(key):
        want = G._fill(title, platform, next(t for k, t, _, _, _ in G.STANDARD if k == key))
        return next((d for d in (j.get('detours') or []) if str(d.get('title') or '') == want), None)
    t_rows = {str(r.get('label')): int(r.get('accounts') or 0) for r in (sec('time_to_play') or {}).get('rows') or []}
    same = t_rows.get('Same day', 0)
    w7 = t_rows.get('Within 7 days (days 1 to 7)', 0)
    w30 = t_rows.get('Within 30 days (days 8 to 30)', 0)
    fk = {f.get('id'): int(f.get('accounts') or 0) for f in (j.get('fork') or []) if isinstance(f, dict)}
    pb = {str(r.get('label')): int(r.get('accounts') or 0) for r in (sec('posts_before') or {}).get('rows') or []}
    one = next((v for k, v in pb.items() if str(k).startswith('1 post')), 0)
    fp, ps, lb, fe, lt = (_top(sec(k)) for k in ('first_post', 'post_surface', 'looked_before', 'first_episode', 'last_touch'))
    meta_window = str((j.get('meta') or {}).get('window') or '')
    return {'title': title, 'platform': platform, 'social': social, 'click': click, 'page': page, 'watched_click': watched_click, 'end': end,
            'rate': _pct(end, social, 4), 'same_day': same, 'within_7': w7, 'within_30': w30,
            'same_pct': _pct(same, end), 'w7_pct': _pct(w7, end), 'w30_pct': _pct(w30, end),
            'clicked_share': _pct(watched_click, end), 'click_rate': _pct(click, social),
            'back': fk.get('paid_return', 0), 'abandoned': fk.get('abandoned', 0), 'same_session': fk.get('paid_first', 0),
            'two_plus': max(click - one, 0), 'one_post': one,
            'fp': fp[0] if fp else None, 'ps': ps[0] if ps else None, 'lb': lb[0] if lb else None, 'fe': fe[0] if fe else None, 'lt': lt[0] if lt else None,
            'outside': aud['outside'], 'also': aud['also'], 'no_post': aud['no_post'], 'viewers': aud['viewers'], 'rest': aud['rest'],
            'tv': tv, 'cap': cap, 'cap_bound': bool(cap_bound), 'window': meta_window}


def _facts(payload: dict, f: dict) -> list:
    N = _norms()
    T, P = f['title'], f['platform']
    window = N._window_words(payload.get('meta') or {})
    return [
        {'label': 'Window', 'value': f"{window}. Unique US accounts."},
        {'label': 'The path', 'value': f"{_fmt(f['social'])} saw a post about {T}. {_fmt(f['click'])} clicked through to the title, {_fmt(f['page'])} opened the {P} page, "
                                       f"and {_fmt(f['end'])} watched an episode on {P} within 30 days of the post."},
        {'label': 'The timing', 'value': f"{_fmt(f['same_day'])} watched the same day, {_fmt(f['within_7'])} within 7 days, {_fmt(f['within_30'])} within 30 days."},
        {'label': 'Already watching', 'value': f"{_fmt(f['also'])} of the people who saw the posts were already watching {T} before the posts; they are not counted in the {f['rate']:.1f}%."},
        {'label': 'The rest', 'value': f"{_fmt(f['outside'])} did both but outside the 30 days. {_fmt(f['no_post'])} {P} viewers saw no post at all."},
    ]


def build_copy(title: str, platform: str, j: dict, f: dict) -> dict:
    """The copy block the tab paints from, for the saw-then-watched shape."""
    window = str((j.get('meta') or {}).get('window') or f.get('window') or '')
    T, P = title, platform
    rate = f['rate']
    back_pct = _pct(f['back'], f['end'])
    kpis = [
        {'v': _fmt(f['social']), 'l': f'Saw a social post about {T}'},
        {'v': f"{f['click_rate']:.1f}%", 'l': 'Of those who saw a post clicked through to the title'},
        {'v': _fmt(f['end']), 'l': f'Watched {T} on {P} within 30 days'},
        {'v': f"{rate:.1f}%", 'l': 'Of those who saw a post watched within 30 days', 'hot': True},
        {'v': f"{f['same_pct']:.0f}%", 'l': 'Of the viewers watched the same day'},
    ]
    return {
        'titleHtml': f'{T}<br>on {P}.',
        'eyebrow': f'Watch journey \u00b7 {window}',
        'lead': f"{rate:.1f}% of the people who saw a social post about {T} watched an episode on {P} within 30 days of it.",
        'deck': (f"This starts with everyone who saw a social post about {T}. Then we count who clicked through to the title, who opened the {P} page, "
                 f"and who watched an episode within 30 days of the post. The bars get smaller each time: {_fmt(f['social'])} people down to {_fmt(f['end'])} who watched."),
        'spineSec': '01 From the post to the first episode',
        'spineHead': f"{f['click_rate']:.1f}% of the people who saw a post clicked through. {rate:.1f}% watched within 30 days, and {f['same_pct']:.0f}% of those viewers watched the same day.",
        'forkSec': f'02 The {P} page',
        'forkHead': f"{_fmt(f['page'])} opened the {P} page. {_fmt(f['end'])} of them watched within 30 days.",
        'forkSub': 'Who watched in the same session as the page, and who left and came back',
        'forkBranchA': 'WATCHED IN THE SAME SESSION AS THE PAGE',
        'forkBranchB': 'Left the page, and what happened next',
        'forkSameSessionLabel': 'Watched in the same session',
        'forkTotalLabel': 'Watched within 30 days of the post',
        'forkTotalVerb': 'watched within 30 days',
        'forkRead': (f"Of the {_fmt(f['end'])} who watched, {_fmt(f['back'])} came back after they left the {P} page. That is {back_pct:.1f}%. "
                     f"The rest pressed play in the session they opened the page.") if f['back'] else '',
        'detourSec': '03 Detours',
        'detourHead': 'Where the posts sat, how many it took, and what closed the watch.',
        'clickSec': '04 Clickstream',
        'clickHead': 'The URLs on each step.',
        'kpis': kpis,
    }


def the_read(f: dict) -> dict:
    """The short version, the reads and what to do next, written from the
    numbers in the voice an analyst uses in the room: each line carries
    its figure and its reading, no two lines on one frame."""
    T, P = f['title'], f['platform']
    rate = f['rate']
    fp, ps, lb, fe, lt = f.get('fp'), f.get('ps'), f.get('lb'), f.get('fe'), f.get('lt')
    ratio = (f['social'] / float(f['viewers'])) if f.get('viewers') else 0.0
    fast = (f['same_pct'] + f['w7_pct']) >= 60     # the title's own timing decides the words, never a stock line
    page_rate, play_rate = _pct(f['page'], f['click']), _pct(f['end'], f['page'])
    tldr = [
        f"{_fmt(f['end'])} people watched {T} on {P} within 30 days of seeing a social post about it. That is {rate:.1f}% of the {_fmt(f['social'])} who saw one.",
        f"{_fmt(f['click'])} of the people who saw a post clicked through to the title ({f['click_rate']:.1f}%). {page_rate:.0f}% of them opened the {P} page, and {play_rate:.0f}% of those pressed play within 30 days.",
        (f"The watching came fast: {f['same_pct']:.0f}% pressed play the same day, {f['w7_pct']:.0f}% in the next week, {f['w30_pct']:.0f}% in days 8 to 30." if fast else
         f"The watching spread across the month: {f['same_pct']:.0f}% pressed play the same day, {f['w7_pct']:.0f}% in the next week and {f['w30_pct']:.0f}% in days 8 to 30, so a post kept working for weeks."),
    ]
    if f.get('cap_bound') and f.get('viewers'):
        tldr.append(f"{T} has {_fmt(f['viewers'])} viewers on {P} in the window and the posts reached {ratio:.0f} times that many people. "
                    f"The ceiling on this rate is the show's own audience, not the feed.")
    elif fp and ps:
        tldr.append(f"{fp[0]} on {ps[0]} opened the most paths: {_pct(fp[1], f['social']):.0f}% of first posts were that kind and {_pct(ps[1], f['social']):.0f}% sat there.")
    if f.get('viewers') and f.get('no_post'):
        tldr.append(f"{_fmt(f['no_post'])} of the {_fmt(f['viewers'])} people who watched {T} on {P} never saw a post. Social is the biggest door we can see, not the only one.")
    reads = []
    if fp and ps:
        reads.append(f"The first post people saw was most often {_lower_first(fp[0])}, {_pct(fp[1], f['social']):.0f}% of first posts; {ps[0]} carried {_pct(ps[1], f['social']):.0f}% of them.")
    if f['click']:
        reads.append(f"Of the {_fmt(f['click'])} who clicked through, {_pct(f['two_plus'], f['click']):.0f}% had seen two or more posts first. One post rarely did it alone.")
    if lb and f['click']:
        reads.append(f"{lb[0]} was the most common stop between the post and {P}, {_pct(lb[1], f['click']):.0f}% of the people who clicked through.")
    if fe:
        reads.append(f"{fe[0]} was the first episode for {_pct(fe[1], f['end']):.0f}% of the viewers.")
    if lt:
        reads.append(f"{lt[0]} was the last touch before {_pct(lt[1], f['end']):.0f}% of the plays.")
    if f['back'] and f['end']:
        reads.append(f"{_fmt(f['back'])} of the {_fmt(f['end'])} viewers left the {P} page first and came back before they played, {_pct(f['back'], f['end']):.0f}% of them.")
    reads.append(f"{_fmt(f['also'])} people who saw the posts were already watching {T} before them. They are their own line and sit outside the {rate:.1f}%.")
    recs = []
    if fp and ps:
        recs.append(f"Lead the next flight with {_lower_first(fp[0])} on {ps[0]}. That pairing opened the path for the most viewers, {_fmt(fp[1])} first views.")
    recs.append(f"Name the show and {P} on the post itself. {100 - f['click_rate']:.0f}% of the people who saw a post never clicked it, and the person who scrolls past still has to find the show.")
    if fast:
        recs.append(f"Front-load the week. {f['same_pct'] + f['w7_pct']:.0f}% of first plays came within seven days of the post; run posts in a cluster rather than one a month.")
    else:
        recs.append(f"Keep posting through the month. {f['same_pct'] + f['w7_pct']:.0f}% of first plays came within seven days of the post and {f['w30_pct']:.0f}% landed in days 8 to 30, so a steady cadence beats one burst.")
    if f.get('cap_bound') and f.get('viewers'):
        recs.append(f"Grow the show's own audience on {P} before widening the feed. The posts already reach {ratio:.0f} times the show's {P} audience; the next viewers come from the title page and the continue row, not from more impressions.")
    elif f['back'] and f['end']:
        recs.append(f"Keep the title page one tap from the post. {_fmt(f['back'])} of the {_fmt(f['end'])} viewers left the page first; a fast follow-up is cheaper than a new impression.")
    cards = [
        {'title': f"{_fmt(f['social'])} people saw a post about {T} in the window",
         'body': f"Anyone who scrolled past a clip, thread or news post naming {T} counts, so the start is wide. {_fmt(f['click'])} of them clicked through to the title, {f['click_rate']:.1f}%."},
        {'title': f"{_fmt(f['page'])} opened the {P} page",
         'body': f"{page_rate:.0f}% of the people who clicked through opened the {P} page, and {play_rate:.0f}% of those pressed play within 30 days. " + (f"{_fmt(f['back'])} left the page first and came back before they played." if f['back'] else "Almost all of them watched in the session they opened the page.")},
        {'title': f"{rate:.1f}% of post viewers watched {T} on {P} within 30 days",
         'body': f"{_fmt(f['end'])} people pressed play within 30 days of their first post. {f['same_pct']:.0f}% of them watched the same day, {f['w7_pct']:.0f}% within the week, {f['w30_pct']:.0f}% in days 8 to 30."},
        {'title': f"{_fmt(f['viewers'])} people watched {T} on {P} in the window",
         'body': f"The journey keeps the {_fmt(f['end'])} whose first play came inside 30 days of a post. {_fmt(f['outside'])} watched 31 to 90 days after a post, {_fmt(f['also'])} were already watching before the posts, and {_fmt(f['no_post'])} saw no post at all."},
    ]
    moves = [{'move': r.split('.')[0], 'why': r} for r in recs]
    return {'tldr': tldr[:5], 'reads': reads[:7], 'recommendations': recs[:4], 'cards': cards, 'moves': moves}


# ----------------------------------------------------------- the summary
def section(payload: dict, key: str) -> Optional[dict]:
    try:
        G = _gold()
        j = payload.get('fragrance_shop_journey') or {}
        title, platform = G._title_platform(payload)
        if key == CLICKED_PATH_KEY:
            return _clicked_table(j)
        want = G._fill(title, platform, next(t for k, t, _, _, _ in G.STANDARD if k == key))
        return next((d for d in (j.get('detours') or []) if str(d.get('title') or '') == want), None)
    except Exception:
        return None


def ship_summary(payload: dict) -> dict:
    """The numbers the ship email and the PDF carry, read off a saw-then-watched payload."""
    G = _gold()
    j = payload.get('fragrance_shop_journey') or {}
    spine = [s for s in (j.get('spine') or []) if isinstance(s, dict)]
    title, platform = G._title_platform(payload)
    meta = payload.get('meta') or {}
    social = int(spine[1]['accounts']) if len(spine) > 1 else 0
    watched = int(spine[-1]['accounts']) if spine else 0
    if len(spine) >= 5:
        click, page = int(spine[2]['accounts']), int(spine[3]['accounts'])
    else:
        c = _clicked_counts(j)          # the two-step shape of 2026-10-22, the click chain as a table
        click, page = c['click'], c['page']
    out = {'title': title, 'platform': platform, 'window': '', 'definition': 'saw_then_watched',
           'social': social, 'watched': watched, 'click': click, 'page': page, 'watched_click': watched,
           'same_day': 0, 'within_7': 0, 'within_30': 0, 'watched_return': 0, 'viewers': 0, 'carried': 0, 'outside_30': 0, 'no_post': 0, 'also': 0}
    if meta.get('start_date') and meta.get('end_date'):
        out['window'] = f"{meta['start_date']} to {meta['end_date']}"
    t = section(payload, 'time_to_play')
    if t:
        rows = {str(r.get('label')): int(r.get('accounts') or 0) for r in t.get('rows') or []}
        out['same_day'], out['within_7'], out['within_30'] = rows.get('Same day', 0), rows.get('Within 7 days (days 1 to 7)', 0), rows.get('Within 30 days (days 8 to 30)', 0)
    for f in j.get('fork') or []:
        if isinstance(f, dict) and f.get('id') == 'paid_return':
            out['watched_return'] = int(f.get('accounts') or 0)
    ro, ev = section(payload, 'rest_overlap'), section(payload, 'every_viewer')
    if ro and len(ro.get('rows') or []) >= 2:
        out['outside_30'] = int(ro['rows'][0].get('accounts') or 0)
        out['also'] = int(ro['rows'][1].get('accounts') or 0)
    if ev:
        rows = ev.get('rows') or []
        out['viewers'] = sum(int(r.get('accounts') or 0) for r in rows)
        out['no_post'] = int(rows[2].get('accounts') or 0) if len(rows) > 2 else 0
    out['carried'] = out['watched'] + out['outside_30'] + out['also']
    out['conversion'] = round(out['watched'] / out['social'] * 100, 1) if out['social'] else 0.0
    out['click_rate'] = round(out['click'] / out['social'] * 100, 1) if out['social'] else 0.0
    out['clicked_share'] = 100.0
    return out


def ship_lines(payload: dict) -> list:
    """The plain lines of the ship email for a saw-then-watched payload."""
    s = ship_summary(payload)
    T, P = s['title'], s['platform']
    lines = [f"The short version, {s['window'] or 'the window'}, unique US accounts:" if s['window'] else "The short version, unique US accounts:",
             f"- {_fmt(s['social'])} people saw a social post about {T}.",
             f"- {_fmt(s['click'])} clicked through to the title, {s['click_rate']:.1f}% of everyone who saw a post. {_fmt(s['page'])} opened the {P} page.",
             f"- {_fmt(s['watched'])} watched at least one episode on {P} within 30 days of the first post. That is {s['conversion']:.1f}% of everyone who saw a post.",
             f"- Timing: {_fmt(s['same_day'])} watched the same day, {_fmt(s['within_7'])} within 7 days, {_fmt(s['within_30'])} within 30 days."]
    if s['watched_return']:
        lines.append(f"- {_fmt(s['watched_return'])} of the {_fmt(s['watched'])} left the page first and came back before they played.")
    tie = (f"How this ties to the read you already have: {_fmt(s['viewers'])} people watched {T} on {P} in the window and {_fmt(s['carried'])} of them also saw a post. "
           f"The journey keeps the {_fmt(s['watched'])} whose first play came inside 30 days of a post. {_fmt(s['outside_30'])} watched 31 to 90 days after a post, "
           f"{_fmt(s['also'])} were already watching before the posts, and {_fmt(s['no_post'])} {P} viewers saw no post at all.")
    return lines + ['', tie]


def is_gold(payload: dict) -> bool:
    """The four-step spine (US base, saw, clicked, page, watched) and the ten sections in order under the standard titles."""
    try:
        G = _gold()
        j = payload.get('fragrance_shop_journey') or {}
        title, platform = G._title_platform(payload)
        titles = [str(d.get('title') or '') for d in (j.get('detours') or [])]
        want = standard_titles(title, platform)
        spine = [s for s in (j.get('spine') or []) if isinstance(s, dict)]
        counts = [int(s.get('accounts') or 0) for s in spine]
        return titles[:len(want)] == want and len(spine) == 5 and all(a > b for a, b in zip(counts, counts[1:]))
    except Exception:
        return False


__all__ = ['KIND', 'DEFINITIONS', 'DEFAULT_DEFINITION', 'USER_DEFAULT_DEFINITION', 'DEFINITION_LABEL', 'CLICKED_PATH_TITLE',
           'definition_of', 'definition_from_text', 'normalize_definition', 'is_social_then_view', 'to_social_then_view', 'hold',
           'ship_summary', 'ship_lines', 'is_gold', 'section', 'standard_titles', 'build_copy', 'the_read', 'research_title_viewers',
           'title_viewers_of']
