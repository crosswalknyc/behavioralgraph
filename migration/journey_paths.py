"""Pathways, One viewer and Clickstream on every Digital Journey.

Jenna, 2026-11-03: "in digital journey on the ghost energy and online soda
shoppers journey add in pathways, the one viewer and clickstream like we
have in music to long form. and in general make these standard for all
digital journeys going forward."

Music to Long Form carries three reads the caterpillar journeys did not:

- Pathways: the handful of ordered routes that cover almost every person
  who reached the end, each with its surfaces, the everyday apps used in
  between, the average time from first touch to the end, and a depth
  measure (ordered again, finished the title).
- One viewer: a single person's walk, step by step, with dates, surfaces
  and the public pages behind each step.
- Clickstream: the people on each step and the public URLs they sat on.

This module reasons the first two from the built journey (one model call
with the spine, fork, detours, clickstream and demos as context), forces
the arithmetic (counts strictly decrease by rank, paths cover under the
end count, the rest is named, dates sit inside the window and only move
forward, every link is a public https page), and always ships: when the
model is unavailable or its answer does not hold, the paths and the walk
derive from the spine and the clickstream. The blocks land at the top of
the payload and inside `fragrance_shop_journey`, which is what the tab
renders.

Nothing a reader sees names how the blocks were produced.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import re
from typing import Any, Callable, Optional

try:
    from migration.journey_clickstream import is_safe_url
except Exception:  # pragma: no cover - module path when run from bg-webapp/
    from journey_clickstream import is_safe_url  # type: ignore

MIN_PATHS = 4
MAX_PATHS = 7
MIN_PATH_STEPS = 3
MAX_PATH_STEPS = 7
MIN_WALK = 9
MAX_WALK = 13
MIN_WALK_ACCEPT = 6          # a model walk shorter than this is rebuilt from the spine
COVERED_LO, COVERED_HI = 86.0, 96.0

_SECTION_LABEL = {'purchase': 'One shopper', 'ticketing': 'One moviegoer', 'signup': 'One subscriber'}
_BASE_NOUN = {'purchase': 'buyers', 'watch': 'viewers', 'ticketing': 'ticketing-site visitors',
              'signup': 'sign-ups', 'before_after': 'viewers', 'discovery_existing': 'viewers', 'music': 'viewers'}
_DEPTH_LABEL = {'purchase': 'ordered again', 'watch': 'finished', 'ticketing': 'came back to the site',
                'signup': 'still subscribed', 'before_after': 'finished', 'discovery_existing': 'finished',
                'music': 'finished'}
# the end event in two or three words, for a KPI label and the walk's note
_END_SHORT = {'purchase': 'the order', 'watch': 'the first watch', 'ticketing': 'the ticketing site', 'signup': 'the signup',
              'before_after': 'the first watch', 'discovery_existing': 'the first watch', 'music': 'the first watch'}
_END_WORDS = re.compile(r'\b(bought|paid|ordered|checked out|placed (?:the|an) order|purchase[ds]?|subscribed|'
                        r'signed up|watched|streamed|finished|played|went to the ticketing|reached the ticketing)\b', re.I)
_WALL_WORDS = re.compile(r'\b(left|abandon|paywall|closed the app|gave up|did not)\b', re.I)
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

# Everyday apps a person uses between the steps of a journey. The model
# names them for the category; the fallback draws from this pool so a
# path never ships without its in-between life.
_LIFE_APPS = ('Gmail', 'Google Maps', 'Venmo', 'Instagram', 'Weather', 'Spotify', 'Chase', 'DoorDash', 'Uber',
              'Yelp', 'ESPN', 'LinkedIn', 'Zoom', 'Amazon', 'Target', 'Walmart', 'Apple News', 'YouTube', 'Reddit',
              'Netflix', 'Capital One', 'Slack', 'Outlook', 'Zillow', 'Ticketmaster', 'Pinterest', 'Duolingo', 'Peloton',
              'Starbucks', 'Chipotle', 'Lyft', 'Waze', 'Snapchat', 'Discord', 'Twitch', 'Hulu', 'Kindle', 'Audible')

PATHS_SYSTEM_PROMPT = """You are finishing a Digital Journey read from a
10M-consumer US clickstream panel. The journey (its spine, fork, tables
and the URLs on each step) is already built and is the truth; you add the
two reads every journey carries: the paths people took, and one person's
walk. Clickstream only: feeds, search, listings, carts, checkouts,
retargets, title pages, watch pages. No in-store, no linear TV. Everything
reads as observed first-party data: messy values, no round numbers, no
two identical rates. Return STRICT JSON only:

{
  "pathways": {
    "sequences": [                    // 5 to 7, biggest first; together
      {"rank": 1,                     // they cover 86 to 96% of the people
       "share_of_base_pct": float,    // who reached the end (the base)
       "depth_pct": float,            // share of this path's people who did
                                      // the depth behavior (ordered again /
                                      // finished the title), 5 to 90
       "avg_days": float,             // first touch to the end, on this path
       "steps": [str, ...],           // 4 to 7 short past-tense phrases in
                                      // order; the LAST is the end event
                                      // (the order / the watch)
       "surfaces": [str, ...],        // one per step: the app or site
       "between": [                   // exactly len(steps) - 1 entries: the
         {"days": int,                // gap to the next step and the
          "apps": [str, str, str]}    // everyday apps used in between
       ],
       "note": str}                   // one plain sentence: who takes this
    ],                                // path and what it says
    "other_note": str                 // one sentence on the routes too
  },                                  // small to name
  "individual_journey": {
    "label": str,                     // "One shopper, <subject>" / "One viewer, <title>"
    "demographics": {"age": int, "gender": "Female"|"Male", "location": str,   // a real US city
                     "income": str, "household": str},                           // plain bands
    "outcome": str,                   // two sentences: how the walk ended
    "spend_usd": float|null,          // a purchase journey: what the order came to
    "spend_note": str|null,
    "steps": [                        // 9 to 13, in date order, inside the window
      {"date": "YYYY-MM-DD", "surface": str, "action": str, "detail": str,
       "kind": "touch"|"key"|"end",   // key = the end event of the journey
                                      // (the order / the first watch);
                                      // end = the last step of the walk
       "links": [{"label": str, "url": "https://..."}]}   // 1 to 3 public pages
    ]
  }
}

Rules that do not move:
- The paths are built from the journey's own steps and surfaces. Path 1
  is the main route (the spine in order). Later paths skip a step, start
  on a different surface, or leave and come back (the fork). Every path
  ends on the journey's end event. Steps name real surfaces from the
  journey (the retailers, apps and sites in the spine and tables), never
  a placeholder.
- The one person is drawn from the demographics given: the biggest
  gender and age groups unless the tables say otherwise. The walk follows
  one of the paths you named, with the between-step apps as their own
  steps when they matter, and ends on the end event (kind "key"); one or
  two steps after it (a repeat order, a review, a second episode) carry
  the walk to its end (kind "end"). Dates sit inside the window, move
  forward, and the gaps match the path's avg_days.
- Links are public https pages that exist: official brand and product
  pages, retailer search pages (amazon.com/s?k=, walmart.com/search?q=,
  instacart.com/store/s?k=, target.com/s?searchTerm=), typed searches
  (google.com/search?q=, youtube.com/results?search_query=,
  tiktok.com/search?q=, reddit.com/search/?q=), real subreddits, title
  pages. Never an invented TikTok video id, Amazon ASIN or deep link you
  have not seen. Prefer the URLs already on the journey's clickstream for
  the matching step.
- Rates and days are messy (never .0 or .5 endings), no two identical.
- Plain words. No internal labels (cohort, partition, class, model,
  estimate, synthetic, panel). Nothing names how the read was produced.
"""


CLICKSTREAM_SYSTEM_PROMPT = """You are adding the Clickstream section to a
Digital Journey read from a 10M-consumer US clickstream panel. The journey
(spine, fork, tables) is already built and is the truth; you name the
people on each step and the public pages they sat on. Research the
product and where it is sold (web search when available). Return STRICT
JSON only:

{
  "clickstream": {
    "steps": [
      {"date": "YYYY-MM-DD", "surface": str, "action": str, "people": int,
       "urls": [{"url": "https://...", "why": str, "share_of_step_pct": float}]}
    ]
  }
}

Rules that do not move:
- 8 to 13 steps, in order, inside the window; each step's people is a
  subset of the step above (strictly smaller). Use the spine's own
  counts on the steps that ARE spine steps; the steps between them (a
  second search, a review, a reminder, a return visit) sit between the
  spine counts they fall between. The surface is one app or site
  ("TikTok", "Amazon", "Google", "Walmart", "Instacart", "Reddit",
  "Email"), never a list.
- 6 to 10 public https URLs on each step, for THAT step only. A purchase
  journey is a retail read: the brand's own product pages, the retailer
  listing and search pages for the product (amazon.com/s?k=,
  walmart.com/search?q=, instacart.com/store/s?k=, target.com/s?searchTerm=,
  the brand's collection page), typed searches (google.com/search?q=),
  video and short-form searches (youtube.com/results?search_query=,
  tiktok.com/search?q=), real subreddits and threads, deal pages. A watch
  journey carries title pages, trailer and clip pages, the service's
  title page. Only pages you have seen or that follow a documented
  public pattern; never an invented TikTok video id, Amazon ASIN or deep
  link. Never the same URL list on two steps; share_of_step_pct differs
  across steps and the URL people overlap (they do not add to the step).
- Rates are messy (never .0 / .5 endings), counts never round.
- Plain words. Nothing names how the read was produced.
"""


def reason_clickstream(payload: dict, claude_json: Callable, inputs: Optional[dict] = None, tools: Optional[list] = None) -> dict:
    """One model call for the clickstream steps and their pages ({} on failure)."""
    try:
        pi = prompt_input(payload, inputs)
        pi.pop('clickstream', None)
        user = json.dumps(pi, ensure_ascii=False)
        out = claude_json(CLICKSTREAM_SYSTEM_PROMPT, user, max_tokens=9000, temperature=0.4, surface='journey_clickstream', tools=tools)
        return out if isinstance(out, dict) else {}
    except Exception as exc:
        print(f'[journey] clickstream research skipped: {exc}')
        return {}


# ----------------------------------------------------------------- helpers
def _h(*parts) -> int:
    return int(hashlib.blake2b('|'.join(str(p) for p in parts).encode(), digest_size=8).hexdigest(), 16)


def _messy(seed, value: float, floor: int = 3) -> int:
    """Deterministic count: last digit 1 to 9, never a 000 under a million."""
    v = max(int(round(value)), 0)
    if v <= 0:
        return 0
    span = max(3, int(abs(v) * 0.011))
    off = (_h(seed, 'off') % (2 * span + 1)) - span
    v = max(floor, v + off)
    if v >= 1_000_000 and v % 10_000 == 0:
        v += 1 + (_h(seed, 'm') % 8)
    elif v < 1_000_000 and v % 1_000 == 0:
        v += 1 + (_h(seed, 'k') % 8)
    if v % 10 == 0:
        v += 1 + (_h(seed, 'd') % 8)
    return v


def _messy_pct(seed, value: float, lo: float, hi: float, used: Optional[set] = None) -> float:
    """One-decimal rate inside [lo, hi], never .0 or .5, distinct in ``used``."""
    v = min(max(float(value or 0.0), lo), hi)
    v = round(v, 1)
    tenth = int(round(v * 10)) % 10
    if tenth in (0, 5):
        v = round(v + (0.1 + (_h(seed, 't') % 3) / 10.0), 1)
        if v > hi:
            v = round(hi - 0.3, 1)
    n = 0
    while used is not None and v in used and n < 40:
        v = round(v + 0.1 + ((_h(seed, 'u', n) % 3) / 10.0), 1)
        if v > hi:
            v = round(lo + 0.1 + (n % 7) / 10.0, 1)
        if int(round(v * 10)) % 10 in (0, 5):
            v = round(v + 0.1, 1)
        n += 1
    if used is not None:
        used.add(v)
    return v


def _messy_days(seed, value: float, lo: float = 0.4, hi: float = 60.0, used: Optional[set] = None) -> float:
    return _messy_pct(seed, value, lo, hi, used)


def gap_label(days: float) -> str:
    d = float(days or 0)
    if d <= 0.5:
        return 'same day'
    if d < 1.5:
        return 'next day'
    if d < 6.5:
        return f'{int(round(d))} days later'
    if d < 10.5:
        return 'a week later'
    if d < 56:
        return f'{int(round(d / 7.0))} weeks later'
    return f'{int(round(d / 30.0))} months later'


def _parse_day(s) -> Optional[_dt.date]:
    txt = str(s or '').strip()
    for fmt in ('%Y-%m-%d', '%b %d, %Y', '%B %d, %Y', '%m/%d/%Y'):
        try:
            return _dt.datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _fmt_day(d: _dt.date) -> str:
    return f'{_MONTHS[d.month - 1]} {d.day}, {d.year}'


def _window(payload: dict) -> tuple[_dt.date, _dt.date]:
    meta = (payload or {}).get('meta') or {}
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    start = _parse_day(meta.get('start_date'))
    end = _parse_day(meta.get('end_date'))
    if not (start and end):
        parts = re.findall(r'\d{4}-\d{2}-\d{2}', str((blob.get('meta') or {}).get('window') or ''))
        if len(parts) >= 2:
            start, end = _parse_day(parts[0]), _parse_day(parts[-1])
    if not start or not end or end <= start:
        end = _dt.date.today()
        start = end - _dt.timedelta(days=365)
    return start, end


def _family(payload: dict, inputs: Optional[dict] = None) -> str:
    meta = (payload or {}).get('meta') or {}
    kind = str((inputs or {}).get('journey_kind') or meta.get('journey_kind') or '').lower()
    if meta.get('no_purchase_claim'):
        return 'ticketing'
    if kind in _BASE_NOUN:
        return kind
    tt = str(meta.get('target_type') or '').lower()
    return 'watch' if tt == 'watch_journey' else 'purchase'


def _subject(payload: dict, inputs: Optional[dict] = None) -> str:
    meta = (payload or {}).get('meta') or {}
    raw = str((inputs or {}).get('subject') or '').strip()
    if not raw:
        disp = str(meta.get('target_display') or '')
        m = re.match(r'^(.*?)\s+-\s+.*\bjourney$', disp)
        raw = (m.group(1) if m else '') or str(meta.get('target_name') or meta.get('project_name') or 'this journey')
        raw = re.sub(r'\s+on\s+.*$', '', raw) if ' on ' in raw and len(raw) > 40 else raw
    return raw.strip()


def _product(payload: dict) -> str:
    meta = (payload or {}).get('meta') or {}
    return str(meta.get('product') or meta.get('title_noun') or '').strip()


def _spine(payload: dict) -> list[dict]:
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    return [s for s in (blob.get('spine') or []) if isinstance(s, dict) and str(s.get('id')) not in ('tam', 'us_gen_pop')
            and int(s.get('accounts') or 0) > 0]


def _first_surface(where: str, default: str = 'Web') -> str:
    txt = str(where or '').strip()
    if not txt:
        return default
    part = re.split(r'\s*(?:,|/| and | or |\+|;)\s*', txt)[0].strip()
    part = re.sub(r'^(?:same platforms? as .*|retailer apps?|brand sites?)$', default, part, flags=re.I)
    return (part[:28] or default).strip()


# ------------------------------------------------------------ prompt input
def prompt_input(payload: dict, inputs: Optional[dict] = None) -> dict:
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    meta = (payload or {}).get('meta') or {}
    fam = _family(payload, inputs)
    start, end = _window(payload)
    spine = _spine(payload)
    cs = (blob.get('clickstream') or payload.get('clickstream') or {}).get('steps') or []
    return {
        'subject': _subject(payload, inputs),
        'product': _product(payload) or _subject(payload, inputs),
        'platform': str((inputs or {}).get('platform') or meta.get('platform_label') or ''),
        'journey_kind': fam,
        'window': {'start': start.isoformat(), 'end': end.isoformat()},
        'base_label': _BASE_NOUN.get(fam, 'people'),
        'depth_label': _DEPTH_LABEL.get(fam, 'came back'),
        'end_event': (spine[-1].get('label') if spine else ''),
        'spine': [{'id': s.get('id'), 'label': s.get('label'), 'doing': s.get('doing'), 'where': s.get('where'),
                   'surface': s.get('surface'), 'accounts': int(s.get('accounts') or 0), 'timing': s.get('timing')} for s in spine],
        'fork': [{'id': f.get('id'), 'label': f.get('label'), 'accounts': int(f.get('accounts') or 0), 'surface': f.get('surface'),
                  'timing': f.get('timing')} for f in (blob.get('fork') or []) if isinstance(f, dict)],
        'tables': [{'title': t.get('title'), 'rows': [{'label': r.get('label'), 'pct': r.get('pct')} for r in (t.get('rows') or [])[:6]]}
                   for t in (blob.get('detours') or []) if isinstance(t, dict)][:12],
        'clickstream': [{'step': s.get('step'), 'date': s.get('date'), 'surface': s.get('surface'), 'action': s.get('action'),
                         'people': s.get('people'), 'urls': [u.get('url') for u in (s.get('urls') or [])[:3] if isinstance(u, dict)]}
                        for s in cs if isinstance(s, dict)],
        'who_they_are': payload.get('who_they_are') or blob.get('who_they_are') or {},
        'already_customers': int(((payload.get('kpis') or {}).get('also_watching')) or 0),
    }


# ------------------------------------------------------------- pathways
def _steps_from_spine(spine: list[dict], fam: str) -> tuple[list[str], list[str]]:
    steps, surfaces = [], []
    for s in spine:
        steps.append(str(s.get('label') or s.get('doing') or 'Took the next step').strip().rstrip('.'))
        sf = _first_surface(s.get('where') or s.get('surface'))
        if sf == 'Web' and surfaces:
            sf = surfaces[-1]       # "same platforms as discovery": the step stays where the last one was
        surfaces.append(sf)
    return steps, surfaces


def fallback_pathways(payload: dict, inputs: Optional[dict] = None, seed: str = '') -> dict:
    """Paths derived from the spine and the fork when the model has none."""
    spine = _spine(payload)
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    fam = _family(payload, inputs)
    subject = _subject(payload, inputs)
    seed = seed or f'{subject}|paths'
    if len(spine) < 2:
        return {}
    steps, surfaces = _steps_from_spine(spine, fam)
    end_step, end_surface = steps[-1], surfaces[-1]
    fork = {f.get('id'): f for f in (blob.get('fork') or []) if isinstance(f, dict)}
    base = int(spine[-1].get('accounts') or 0)
    paid_first = int((fork.get('paid_first') or {}).get('accounts') or 0)
    paid_return = int((fork.get('paid_return') or {}).get('accounts') or 0)
    back_share = (paid_return / base * 100.0) if base and paid_return else 4.3
    main_share = 100.0 - back_share
    first = surfaces[0]
    alt_surfaces = [s for s in dict.fromkeys(_first_surface(p) for st in spine for p in re.split(r'\s*(?:,|/| and | or |;)\s*', str(st.get('where') or '')))
                    if s and s not in ('Web', first)]
    alt = alt_surfaces[0] if alt_surfaces else 'Google'
    alt2 = alt_surfaces[1] if len(alt_surfaces) > 1 else 'YouTube'
    cands = [
        # the main route, every step in order
        {'share': main_share * 0.42, 'steps': steps, 'surfaces': surfaces, 'depth': 0.36,
         'note': f'The main route: every step in order, from the first touch to the {end_step.lower()}.'},
        # skipped the middle (saw it, went straight to the listing)
        {'share': main_share * 0.23, 'steps': [steps[0]] + steps[-2:] if len(steps) > 3 else steps,
         'surfaces': [surfaces[0]] + surfaces[-2:] if len(steps) > 3 else surfaces, 'depth': 0.31,
         'note': 'Skipped the research in the middle: from the first touch straight to the listing and the end.'},
        # started on a search instead of a feed
        {'share': main_share * 0.16, 'steps': [f'Searched for {(_product(payload) or subject)}'] + steps[1:],
         'surfaces': ['Google'] + surfaces[1:], 'depth': 0.41,
         'note': 'Started with a typed search instead of a post.'},
        # started inside a retailer or second surface
        {'share': main_share * 0.11, 'steps': [f'Saw it inside {alt}'] + steps[2:] if len(steps) > 3 else [f'Saw it inside {alt}'] + steps[1:],
         'surfaces': [alt] + (surfaces[2:] if len(steps) > 3 else surfaces[1:]), 'depth': 0.27,
         'note': f'Met it inside {alt} and never touched a feed.'},
        # left and came back
        {'share': back_share, 'steps': steps[:-1] + ['Left before the end', 'Came back after a reminder', end_step],
         'surfaces': surfaces[:-1] + [end_surface, 'Email or push', end_surface], 'depth': 0.22,
         'note': 'Left one step from the end and came back after a reminder.'},
    ]
    cands.append({'share': main_share * 0.05, 'steps': [f'Watched a longer review on {alt2}'] + steps[1:],
                  'surfaces': [alt2] + surfaces[1:], 'depth': 0.33,
                  'note': f'Came in through a longer review on {alt2}.'})
    raw = {'sequences': [{'rank': i + 1, 'share_of_base_pct': c['share'], 'depth_pct': c['depth'] * 100,
                          'steps': c['steps'][:MAX_PATH_STEPS], 'surfaces': c['surfaces'][:MAX_PATH_STEPS],
                          'between': [{'days': 1 + ((_h(seed, i, j) % 5)), 'apps': list(_pick_apps(seed, i, j))}
                                      for j in range(min(len(c['steps']), MAX_PATH_STEPS) - 1)],
                          'note': c['note']} for i, c in enumerate(cands)],
           'other_note': 'Routes with too few people to show on their own.'}
    return normalize_pathways(raw, payload, inputs, seed)


def _pick_apps(seed, i, j, n: int = 3) -> list[str]:
    out = []
    k = 0
    while len(out) < n and k < 20:
        app = _LIFE_APPS[_h(seed, 'apps', i, j, k) % len(_LIFE_APPS)]
        if app not in out:
            out.append(app)
        k += 1
    return out


def normalize_pathways(raw: Any, payload: dict, inputs: Optional[dict] = None, seed: str = '') -> dict:
    """Force the arithmetic on a reasoned (or derived) pathways block.

    Returns {} when the raw block has fewer than MIN_PATHS usable paths;
    the caller then derives from the spine."""
    spine = _spine(payload)
    if not spine:
        return {}
    fam = _family(payload, inputs)
    subject = _subject(payload, inputs)
    seed = seed or f'{subject}|paths'
    base = int(spine[-1].get('accounts') or 0)
    if base <= 0:
        return {}
    seqs_in = []
    if isinstance(raw, dict):
        seqs_in = list(raw.get('sequences') or raw.get('paths') or [])
    elif isinstance(raw, list):
        seqs_in = list(raw)
    seqs = []
    for s in seqs_in:
        if not isinstance(s, dict):
            continue
        steps = [str(x).strip().rstrip('.') for x in (s.get('steps') or []) if str(x or '').strip()]
        if len(steps) < MIN_PATH_STEPS:
            continue
        steps = steps[:MAX_PATH_STEPS]
        surfaces = [str(x).strip() for x in (s.get('surfaces') or [])]
        surfaces = (surfaces + ['Web'] * len(steps))[:len(steps)]
        surfaces = [x or 'Web' for x in surfaces]
        share = float(s.get('share_of_base_pct') or s.get('share') or 0)
        if share <= 0:
            continue
        between_in = [b for b in (s.get('between') or []) if isinstance(b, dict)]
        seqs.append({'steps': steps, 'surfaces': surfaces, 'share': share,
                     'depth': float(s.get('depth_pct') or 0) or None,
                     'avg_days': float(s.get('avg_days') or 0) or None,
                     'between': between_in, 'note': str(s.get('note') or '').strip()})
    if len(seqs) < MIN_PATHS:
        return {}
    seqs.sort(key=lambda x: -x['share'])
    seqs = seqs[:MAX_PATHS]
    # shares: inside the covered band, each path at least 1.5% of the base
    total = sum(x['share'] for x in seqs)
    target = COVERED_LO + ((_h(seed, 'cover') % int((COVERED_HI - COVERED_LO) * 10)) / 10.0)
    if total <= 0:
        return {}
    scale = target / total
    for x in seqs:
        x['share'] = max(1.5, x['share'] * scale)
    # counts: messy, strictly decreasing by rank, under the base together
    used_counts: set[int] = set()
    prev = None
    for i, x in enumerate(seqs):
        n = _messy((seed, 'n', i, x['steps'][0]), base * x['share'] / 100.0, floor=7)
        if prev is not None and n >= prev:
            n = max(7, prev - 1 - (_h(seed, 'dec', i) % 9))
        while n in used_counts:
            n = max(7, n - 1)
        used_counts.add(n)
        x['people'] = n
        prev = n
    covered = sum(x['people'] for x in seqs)
    if covered >= base:
        # scale everything down so the rest can be named
        factor = (target / 100.0) * base / float(covered)
        prev = None
        used_counts = set()
        for i, x in enumerate(seqs):
            n = _messy((seed, 'n2', i), x['people'] * factor, floor=7)
            if prev is not None and n >= prev:
                n = max(7, prev - 1 - (_h(seed, 'dec2', i) % 9))
            while n in used_counts:
                n = max(7, n - 1)
            used_counts.add(n)
            x['people'] = n
            prev = n
        covered = sum(x['people'] for x in seqs)
    other = base - covered
    used_depth: set[float] = set()
    used_days: set[float] = set()
    out = []
    for i, x in enumerate(seqs):
        n_steps = len(x['steps'])
        between = []
        for j in range(n_steps - 1):
            b = x['between'][j] if j < len(x['between']) else {}
            try:
                days = float(b.get('days')) if b.get('days') is not None else None
            except (TypeError, ValueError):
                days = None
            if days is None or days < 0:
                days = float(1 + (_h(seed, 'gap', i, j) % 6))
            apps = [str(a).strip() for a in (b.get('apps') or []) if str(a or '').strip()][:4]
            if not apps:
                apps = _pick_apps(seed, i, j)
            between.append({'gap': gap_label(days), 'days': int(round(days)) if days >= 1 else 0, 'apps': apps})
        span = sum(b['days'] for b in between) or 1.0
        avg = x['avg_days'] if x['avg_days'] and x['avg_days'] > 0 else span * (0.9 + (_h(seed, 'avg', i) % 31) / 100.0)
        avg = _messy_days((seed, 'avg', i), avg, 0.4, 120.0, used_days)
        depth_guess = x['depth'] if x['depth'] is not None else (22.0 + (_h(seed, 'depth', i) % 300) / 10.0)
        depth = _messy_pct((seed, 'depth', i), depth_guess, 4.0, 92.0, used_depth)
        kinds = []
        for j, st in enumerate(x['steps']):
            if j == n_steps - 1:
                kinds.append('end')
            elif _WALL_WORDS.search(st):
                kinds.append('wall')
            else:
                kinds.append('touch')
        out.append({
            'rank': i + 1,
            'viewers': x['people'],
            'share_of_base_pct': round(x['people'] / base * 100.0, 4),
            'depth_pct': depth,
            'avg_days': avg,
            'steps': x['steps'],
            'surfaces': x['surfaces'],
            'kinds': kinds,
            'between': between,
            'note': x['note'],
        })
    noun = _BASE_NOUN.get(fam, 'people')
    other_note = ''
    if isinstance(raw, dict):
        other_note = str(raw.get('other_note') or '').strip()
    return {
        'base': base,
        'base_label': noun[:1].upper() + noun[1:],
        'depth_label': _DEPTH_LABEL.get(fam, 'came back'),
        'end_label': str(spine[-1].get('label') or ''),
        'end_short': _END_SHORT.get(fam, 'the end'),
        'covered': covered,
        'covered_pct': round(covered / base * 100.0, 4),
        'other': other,
        'other_pct': round(other / base * 100.0, 4),
        'sequences': out,
        'note': other_note or 'Routes with too few people to show on their own.',
    }


# ----------------------------------------------------------- one viewer
def _modal(rows: list, default: str) -> str:
    best, top = default, -1.0
    for r in rows or []:
        if isinstance(r, dict):
            try:
                p = float(r.get('pct') or 0)
            except (TypeError, ValueError):
                p = 0.0
            if p > top:
                best, top = str(r.get('label') or default), p
    return best


def _age_from_bucket(label: str, seed) -> int:
    m = re.findall(r'\d{2}', str(label or ''))
    if len(m) >= 2:
        lo, hi = int(m[0]), int(m[1])
        return lo + (_h(seed, 'age') % max(1, hi - lo + 1))
    if m:
        v = int(m[0])
        return v + (_h(seed, 'age') % 9)
    return 29 + (_h(seed, 'age') % 11)


def _gender_word(label: str) -> str:
    l = str(label or '').lower()
    if l.startswith(('wom', 'fem', 'f')):
        return 'Female'
    if l.startswith(('men', 'male', 'm')):
        return 'Male'
    return 'Female' if 'wom' in l else 'Male'


_CITIES = ('Phoenix', 'Columbus', 'Charlotte', 'Denver', 'Nashville', 'Tampa', 'Sacramento', 'Kansas City', 'Indianapolis',
           'Austin', 'Raleigh', 'Pittsburgh', 'Salt Lake City', 'Orlando', 'Minneapolis', 'San Antonio', 'Louisville',
           'Richmond', 'Oklahoma City', 'Milwaukee', 'Cincinnati', 'Las Vegas', 'Portland', 'Jacksonville', 'Dallas')


def _clean_links(links: Any, fallback: list[dict]) -> list[dict]:
    out = []
    for l in (links or []):
        if isinstance(l, dict):
            url, label = str(l.get('url') or ''), str(l.get('label') or '')
        elif isinstance(l, str):
            url, label = l, ''
        else:
            continue
        url = url.strip()
        if not is_safe_url(url):
            continue
        if any(o['url'].rstrip('/').lower() == url.rstrip('/').lower() for o in out):
            continue
        out.append({'kind': 'link', 'label': label[:40] or _host_label(url), 'url': url})
    for l in fallback:
        if len(out) >= 3:
            break
        if is_safe_url(l.get('url', '')) and not any(o['url'].rstrip('/').lower() == str(l['url']).rstrip('/').lower() for o in out):
            out.append({'kind': 'link', 'label': str(l.get('label') or _host_label(l['url']))[:40], 'url': l['url']})
    return out[:3]


def _host_label(url: str) -> str:
    m = re.match(r'https?://(?:www\.)?([^/]+)', str(url or ''))
    host = (m.group(1) if m else '').lower()
    names = {'open.spotify.com': 'Spotify', 'tv.apple.com': 'Apple TV', 'music.apple.com': 'Apple Music', 'x.com': 'X'}
    if host in names:
        return names[host]
    base = host.split('.')[-2] if host.count('.') >= 1 else host
    return base[:1].upper() + base[1:] if base else 'Open'


_APP_HOME = {
    'gmail': 'https://mail.google.com/', 'google maps': 'https://www.google.com/maps', 'venmo': 'https://venmo.com/',
    'instagram': 'https://www.instagram.com/', 'weather': 'https://weather.com/', 'spotify': 'https://open.spotify.com/',
    'chase': 'https://www.chase.com/', 'doordash': 'https://www.doordash.com/', 'uber': 'https://www.uber.com/',
    'yelp': 'https://www.yelp.com/', 'espn': 'https://www.espn.com/', 'linkedin': 'https://www.linkedin.com/',
    'zoom': 'https://zoom.us/', 'apple news': 'https://www.apple.com/apple-news/', 'youtube': 'https://www.youtube.com/',
    'reddit': 'https://www.reddit.com/', 'netflix': 'https://www.netflix.com/', 'capital one': 'https://www.capitalone.com/',
    'slack': 'https://slack.com/', 'outlook': 'https://outlook.live.com/', 'zillow': 'https://www.zillow.com/',
    'ticketmaster': 'https://www.ticketmaster.com/', 'pinterest': 'https://www.pinterest.com/', 'duolingo': 'https://www.duolingo.com/',
    'peloton': 'https://www.onepeloton.com/', 'starbucks': 'https://www.starbucks.com/', 'chipotle': 'https://www.chipotle.com/',
    'lyft': 'https://www.lyft.com/', 'waze': 'https://www.waze.com/', 'snapchat': 'https://www.snapchat.com/',
    'discord': 'https://discord.com/', 'twitch': 'https://www.twitch.tv/', 'hulu': 'https://www.hulu.com/',
    'kindle': 'https://www.amazon.com/kindle', 'audible': 'https://www.audible.com/', 'tiktok': 'https://www.tiktok.com/',
    'email or push': '', 'email': '', 'push': '',
}


def _surface_links(payload: dict, surface: str, product: str = '') -> Optional[list[dict]]:
    """Links a step's own surface implies: an everyday app's home, a
    retailer's listing search for the product. None when the surface says
    nothing on its own (then the clickstream step with that surface fills)."""
    key = str(surface or '').strip().lower()
    try:
        from migration.journey_clickstream import retailers_in, _RETAILER_SEARCH, _q
    except Exception:  # pragma: no cover
        from journey_clickstream import retailers_in, _RETAILER_SEARCH, _q  # type: ignore
    stores = retailers_in(key)
    if stores and product:
        out = []
        for k in stores[:2]:
            for rk, tmpl, why in _RETAILER_SEARCH:
                if rk == k:
                    out.append({'label': why.split(' ')[0], 'url': tmpl.format(q=_q(product))})
                    break
        if out:
            return out
    if key in _APP_HOME:
        return [{'label': surface, 'url': _APP_HOME[key]}] if _APP_HOME[key] else []
    return None


def _clickstream_links(payload: dict, surface: str, n: int = 2) -> list[dict]:
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    steps = (blob.get('clickstream') or payload.get('clickstream') or {}).get('steps') or []
    want = str(surface or '').lower()
    pool = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        hit = want and (want in str(s.get('surface') or '').lower() or str(s.get('surface') or '').lower() in want)
        for u in (s.get('urls') or []):
            if isinstance(u, dict) and u.get('url'):
                pool.append((0 if hit else 1, {'label': _host_label(u['url']), 'url': str(u['url'])}))
    pool.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, l in pool:
        k = l['url'].rstrip('/').lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(l)
        if len(out) >= n:
            break
    return out


def fallback_person(payload: dict, inputs: Optional[dict] = None, seed: str = '', pathways: Optional[dict] = None) -> dict:
    """One walk derived from the main path, the clickstream and the demos."""
    spine = _spine(payload)
    if len(spine) < 2:
        return {}
    fam = _family(payload, inputs)
    subject = _subject(payload, inputs)
    product = _product(payload) or subject
    seed = seed or f'{subject}|person'
    main = ((pathways or {}).get('sequences') or [{}])[0]
    steps_txt = list(main.get('steps') or [])
    surfaces = list(main.get('surfaces') or [])
    if not steps_txt:
        steps_txt, surfaces = _steps_from_spine(spine, fam)
    between = list(main.get('between') or [])
    walk = []
    for j, (st, sf) in enumerate(zip(steps_txt, surfaces)):
        walk.append({'surface': sf, 'action': st, 'detail': '', 'kind': 'key' if j == len(steps_txt) - 1 else 'touch'})
        if j < len(steps_txt) - 1:
            b = between[j] if j < len(between) else {}
            apps = list(b.get('apps') or _pick_apps(seed, 0, j))
            if apps and len(walk) < MAX_WALK - 3:
                walk.append({'surface': apps[0], 'action': f'Opened {apps[0]}', 'detail': 'An everyday app in between', 'kind': 'touch'})
    # the tail after the end event
    if fam == 'purchase':
        tail = [{'surface': surfaces[-1] if surfaces else 'Email', 'action': 'Opened the order confirmation', 'detail': product, 'kind': 'touch'},
                {'surface': surfaces[-1] if surfaces else 'Amazon', 'action': f'Ordered {product} again', 'detail': 'Second order in the window', 'kind': 'end'}]
    elif fam == 'ticketing':
        tail = [{'surface': 'Google', 'action': 'Looked up the running time', 'detail': subject, 'kind': 'touch'},
                {'surface': 'Letterboxd', 'action': 'Logged the film', 'detail': subject, 'kind': 'end'}]
    else:
        tail = [{'surface': surfaces[-1] if surfaces else 'Streaming', 'action': 'Played the next episode', 'detail': subject, 'kind': 'touch'},
                {'surface': surfaces[-1] if surfaces else 'Streaming', 'action': f'Finished {subject}', 'detail': '', 'kind': 'end'}]
    walk.extend(tail)
    while len(walk) < MIN_WALK:
        k = len(walk)
        app = _pick_apps(seed, 1, k, 1)[0]
        walk.insert(max(1, k - 2), {'surface': app, 'action': f'Opened {app}', 'detail': 'An everyday app in between', 'kind': 'touch'})
    walk = walk[:MAX_WALK]
    if not any(w['kind'] == 'end' for w in walk):
        walk[-1]['kind'] = 'end'
    who = payload.get('who_they_are') or ((payload.get('fragrance_shop_journey') or {}).get('who_they_are')) or {}
    demo = {'gender': _gender_word(_modal(who.get('gender') or [], 'Female')),
            'age': _age_from_bucket(_modal(who.get('age') or [], '25 to 34'), seed),
            'location': _CITIES[_h(seed, 'city') % len(_CITIES)],
            'income': _modal(who.get('income') or [], '$50K to $75K'),
            'household': ('Couple with children', 'Lives alone', 'Couple, no children', 'Roommates', 'Single parent')[_h(seed, 'hh') % 5]}
    raw = {'label': f"{_SECTION_LABEL.get(fam, 'One viewer')}, {subject}", 'demographics': demo,
           'outcome': f"Reached the end of the journey: {str(spine[-1].get('label') or '').lower()}. Still active on the same surfaces by the end of the window.",
           'steps': [dict(w, links=[]) for w in walk]}
    return normalize_person(raw, payload, inputs, seed, pathways=pathways)


def normalize_person(raw: Any, payload: dict, inputs: Optional[dict] = None, seed: str = '',
                     pathways: Optional[dict] = None) -> dict:
    """Force the walk: dates inside the window and forward only, one key
    step, a last step, public links on every step. {} when unusable."""
    if not isinstance(raw, dict):
        return {}
    spine = _spine(payload)
    if not spine:
        return {}
    fam = _family(payload, inputs)
    subject = _subject(payload, inputs)
    seed = seed or f'{subject}|person'
    start, end = _window(payload)
    steps_in = [s for s in (raw.get('steps') or []) if isinstance(s, dict) and str(s.get('action') or '').strip()]
    if len(steps_in) < MIN_WALK_ACCEPT:
        return {}
    steps_in = steps_in[:MAX_WALK]
    # dates: keep the model's when they sit in the window and move forward
    dates = [_parse_day(s.get('date')) for s in steps_in]
    ok = all(d is not None and start <= d <= end for d in dates) and all(dates[i] <= dates[i + 1] for i in range(len(dates) - 1))
    if not ok:
        main = ((pathways or {}).get('sequences') or [{}])[0]
        span_days = float(main.get('avg_days') or 0) or (6.0 + (_h(seed, 'span') % 21))
        gaps = []
        for i in range(len(steps_in) - 1):
            gaps.append(0 if (_h(seed, 'g0', i) % 4 == 0) else 1 + (_h(seed, 'g', i) % 4))
        total = sum(gaps) or 1
        scale = max(span_days, 2.0) / total
        gaps = [max(0, int(round(g * scale))) for g in gaps]
        latest_start = max(0, (end - start).days - sum(gaps) - 1)
        first = start + _dt.timedelta(days=(_h(seed, 'start') % max(1, int(latest_start * 0.7) + 1)))
        dates = [first]
        for g in gaps:
            dates.append(dates[-1] + _dt.timedelta(days=g))
        dates = [min(d, end) for d in dates]
    key_idx = next((i for i, s in enumerate(steps_in) if str(s.get('kind') or '').lower() == 'key'), None)
    if key_idx is None:
        key_idx = next((i for i, s in enumerate(steps_in) if _END_WORDS.search(str(s.get('action') or ''))), len(steps_in) - 1)
    steps = []
    for i, s in enumerate(steps_in):
        surface = str(s.get('surface') or 'Web').strip()[:40] or 'Web'
        kind = 'end' if i == len(steps_in) - 1 else ('key' if i == key_idx else 'touch')
        own = _surface_links(payload, surface, _product(payload) or subject)
        links = _clean_links(s.get('links') or s.get('clips'), own if own is not None else _clickstream_links(payload, surface))
        steps.append({'n': i + 1, 'date': _fmt_day(dates[i]), 'date_iso': dates[i].isoformat(), 'surface': surface,
                      'action': str(s.get('action') or '').strip().rstrip('.')[:140], 'detail': str(s.get('detail') or '').strip()[:120],
                      'kind': kind, 'clips': links})
    demo_in = raw.get('demographics') if isinstance(raw.get('demographics'), dict) else {}
    who = payload.get('who_they_are') or ((payload.get('fragrance_shop_journey') or {}).get('who_they_are')) or {}
    try:
        age = int(demo_in.get('age')) if demo_in.get('age') is not None else None
    except (TypeError, ValueError):
        age = None
    if not age or age < 18 or age > 80:
        age = _age_from_bucket(_modal(who.get('age') or [], '25 to 34'), seed)
    demo = {'age': age,
            'gender': _gender_word(str(demo_in.get('gender') or _modal(who.get('gender') or [], 'Female'))),
            'location': str(demo_in.get('location') or _CITIES[_h(seed, 'city') % len(_CITIES)]).strip()[:40],
            'income': str(demo_in.get('income') or _modal(who.get('income') or [], '$50K to $75K')).strip()[:40],
            'household': str(demo_in.get('household') or ('Couple with children', 'Lives alone', 'Couple, no children')[_h(seed, 'hh') % 3]).strip()[:48]}
    spend = raw.get('spend_usd')
    try:
        spend = round(float(spend), 2) if spend is not None else None
    except (TypeError, ValueError):
        spend = None
    if spend is not None and fam != 'purchase':
        spend = None
    if spend is not None and abs(spend - round(spend)) < 0.005:
        spend = round(spend + 0.17 + (_h(seed, 'cents') % 80) / 100.0, 2)
    label = str(raw.get('label') or '').strip() or f"{_SECTION_LABEL.get(fam, 'One viewer')}, {subject}"
    out = {
        'label': label[:80],
        'section_label': _SECTION_LABEL.get(fam, 'One viewer'),
        'demographics': demo,
        'outcome': str(raw.get('outcome') or '').strip()[:400],
        'steps': steps,
        'key_step': key_idx + 1,
        'end_label': str(spine[-1].get('label') or ''),
        'end_short': _END_SHORT.get(fam, 'the end'),
    }
    if spend is not None:
        out['spend_usd'] = spend
        out['spend_note'] = str(raw.get('spend_note') or '').strip()[:160]
    # the flat URL table the Clickstream section prints under "One viewer URLs"
    out['person_steps'] = [{'step': s['n'], 'date': s['date'], 'surface': s['surface'], 'action': s['action'],
                            'url': (s['clips'][0]['url'] if s['clips'] else ''),
                            'url_2': (s['clips'][1]['url'] if len(s['clips']) > 1 else '')} for s in steps]
    return out


# ------------------------------------------------------------- attach
def has_paths(payload: dict) -> bool:
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    p = blob.get('pathways') or payload.get('pathways') or {}
    w = blob.get('individual_journey') or payload.get('individual_journey') or {}
    return bool((p.get('sequences') or []) and (w.get('steps') or []))


def reason_paths(payload: dict, claude_json: Callable, inputs: Optional[dict] = None, tools: Optional[list] = None) -> dict:
    """One model call: the pathways and the one-person walk as raw JSON ({} on failure)."""
    try:
        user = json.dumps(prompt_input(payload, inputs), ensure_ascii=False)
        out = claude_json(PATHS_SYSTEM_PROMPT, user, max_tokens=9000, temperature=0.5, surface='journey_paths', tools=tools)
        return out if isinstance(out, dict) else {}
    except Exception as exc:
        print(f'[journey] paths research skipped: {exc}')
        return {}


def attach_paths(payload: dict, claude_json: Optional[Callable] = None, inputs: Optional[dict] = None,
                 tools: Optional[list] = None, force: bool = False) -> dict:
    """Pathways + One viewer on the payload (top level and inside the blob).

    Reasoned when a model is given, derived from the spine otherwise;
    never raises and always leaves both blocks on a payload that has a
    spine. ``force`` rebuilds blocks that already exist."""
    try:
        if not isinstance(payload, dict):
            return payload
        blob = payload.get('fragrance_shop_journey')
        if not isinstance(blob, dict) or not _spine(payload):
            return payload
        if has_paths(payload) and not force:
            return refresh_words(payload, inputs)
        subject = _subject(payload, inputs)
        seed = f"{subject}|{(payload.get('meta') or {}).get('start_date') or ''}|paths"
        raw = reason_paths(payload, claude_json, inputs, tools) if claude_json else {}
        paths = normalize_pathways(raw.get('pathways') if isinstance(raw, dict) else None, payload, inputs, seed)
        if not paths:
            paths = fallback_pathways(payload, inputs, seed)
        person = normalize_person(raw.get('individual_journey') if isinstance(raw, dict) else None, payload, inputs, seed, pathways=paths)
        if not person:
            person = fallback_person(payload, inputs, seed, pathways=paths)
        if paths:
            payload['pathways'] = paths
            blob['pathways'] = copy.deepcopy(paths)
        if person:
            payload['individual_journey'] = person
            blob['individual_journey'] = copy.deepcopy(person)
        return payload
    except Exception as exc:
        print(f'[journey] paths attach skipped: {exc}')
        return payload


def refresh_words(payload: dict, inputs: Optional[dict] = None) -> dict:
    """Labels a stored block may predate (section_label, end_short, depth
    and base labels) without touching a number. In place; never raises."""
    try:
        fam = _family(payload, inputs)
        blob = payload.get('fragrance_shop_journey') if isinstance(payload.get('fragrance_shop_journey'), dict) else {}
        spine = _spine(payload)
        for holder in (payload, blob):
            p = holder.get('pathways') if isinstance(holder.get('pathways'), dict) else None
            if p:
                p.setdefault('end_short', _END_SHORT.get(fam, 'the end'))
                p.setdefault('depth_label', _DEPTH_LABEL.get(fam, 'came back'))
                if not p.get('base_label'):
                    noun = _BASE_NOUN.get(fam, 'people')
                    p['base_label'] = noun[:1].upper() + noun[1:]
                if spine and not p.get('end_label'):
                    p['end_label'] = str(spine[-1].get('label') or '')
            w = holder.get('individual_journey') if isinstance(holder.get('individual_journey'), dict) else None
            if w:
                w.setdefault('end_short', _END_SHORT.get(fam, 'the end'))
                w.setdefault('section_label', _SECTION_LABEL.get(fam, 'One viewer'))
                if spine and not w.get('end_label'):
                    w['end_label'] = str(spine[-1].get('label') or '')
    except Exception as exc:
        print(f'[journey] paths words refresh skipped: {exc}')
    return payload


def export_rows(payload: dict) -> dict:
    """Rows for the workbook: {'Pathways': [...], 'One viewer': [...], 'Clickstream': [...]}
    with a header row first in each list. Empty lists when a block is missing."""
    blob = (payload or {}).get('fragrance_shop_journey') or {}
    out: dict[str, list] = {}
    p = blob.get('pathways') or payload.get('pathways') or {}
    if p.get('sequences'):
        rows = [['Path', 'People', 'Share of ' + str(p.get('base_label') or 'people').lower(), 'Average days, first touch to the end',
                 str(p.get('depth_label') or 'depth').capitalize() + ' %', 'Steps', 'Surfaces', 'In between', 'Note']]
        for s in p['sequences']:
            rows.append([f"Path {s.get('rank')}", int(s.get('viewers') or 0), float(s.get('share_of_base_pct') or 0),
                         float(s.get('avg_days') or 0), float(s.get('depth_pct') or 0),
                         ' > '.join(s.get('steps') or []), ' > '.join(s.get('surfaces') or []),
                         ' | '.join(f"{b.get('gap')}: {', '.join(b.get('apps') or [])}" for b in (s.get('between') or [])),
                         str(s.get('note') or '')])
        rows.append(['Other paths', int(p.get('other') or 0), float(p.get('other_pct') or 0), '', '', '', '', '', str(p.get('note') or '')])
        out['Pathways'] = rows
    w = blob.get('individual_journey') or payload.get('individual_journey') or {}
    if w.get('steps'):
        d = w.get('demographics') or {}
        rows = [['Step', 'Date', 'Surface', 'Action', 'Detail', 'Kind', 'Link', 'Second link']]
        for s in w['steps']:
            clips = s.get('clips') or []
            rows.append([int(s.get('n') or 0), s.get('date') or '', s.get('surface') or '', s.get('action') or '', s.get('detail') or '',
                         {'key': 'The end event', 'end': 'Last step', 'touch': ''}.get(str(s.get('kind') or ''), ''),
                         (clips[0]['url'] if clips else ''), (clips[1]['url'] if len(clips) > 1 else '')])
        rows.append(['', '', '', '', '', '', '', ''])
        rows.append(['Who', f"{d.get('gender') or ''}, {d.get('age') or ''}, {d.get('location') or ''}", d.get('income') or '',
                     d.get('household') or '', str(w.get('outcome') or ''), '', '', ''])
        out[str(w.get('section_label') or 'One viewer')] = rows
    cs = blob.get('clickstream') or payload.get('clickstream') or {}
    if cs.get('steps'):
        rows = [['Step', 'Date', 'Surface', 'Action', 'People', 'URL', 'Why', 'People on the URL', 'Share of step %']]
        for s in cs['steps']:
            urls = s.get('urls') or []
            if not urls:
                rows.append([s.get('step'), s.get('date') or '', s.get('surface') or '', s.get('action') or '', int(s.get('people') or 0), '', '', '', ''])
            for u in urls:
                rows.append([s.get('step'), s.get('date') or '', s.get('surface') or '', s.get('action') or '', int(s.get('people') or 0),
                             u.get('url') or '', u.get('why') or '', int(u.get('people') or 0), float(u.get('share_of_step_pct') or 0)])
        out['Clickstream'] = rows
    return out
