"""Digital Journey synthesis for Prometheus.

Jenna 2026-09-16: "update prometheus to be able to pull a digital
journey. and pull a digital journey should be a chip. it would need to
walk the user through getting what it needs to pull one and would
follow this path: reports/Luxury_Fragrance_TTS_Journey_Build_Playbook
_2026_09_16.md and would then display it in the digital journey tab in
the dashboard like how the Luxury fragrance on TikTok Shop is
displayed."

This module encodes that playbook as a generation engine:

  * TAM on the top row, nested conversion ladder below it, every step
    a strict subset of the one above (asserts, not hopes).
  * A leave / retarget / return fork off the bag-equivalent step, with
    paid_first + paid_return == paid.
  * Where-tables beside the nest: partitions sum exactly to their
    parent; overlaps are labeled and each row stays under its parent.
  * Attribution on the conversion count only (first / last partition,
    assists overlap).
  * Counts are messy unique-US-account values; kept / dropped / share
    of US gen pop computed in code, never hand-written.

The output payload lands in the Journey IQ store (same index + loader
as every run) with meta.story_mode = 'fragrance_shop_journey', so the
Digital Journey tab renders it through the exact renderer the Luxury
Fragrance on TikTok Shop read uses - spine, fork, detour tables, and
facts all come from the data.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import traceback
from typing import Callable, Optional

US_GEN_POP = 329_900_000

PARSE_SYSTEM_PROMPT = """You extract Digital Journey inputs from a
user's message. Return STRICT JSON only:
{
  "subject": str|null,          // the category or title the journey
                                // follows (e.g. "luxury fragrance",
                                // "running shoes", "Young Sheldon")
  "platform": str|null,         // where the END STEP happens (TikTok
                                // Shop, Amazon, a DTC site, Peacock)
  "conversion_event": str|null, // ONE sentence naming the end step.
                                // Either a PAID event ("paid $95+ for
                                // a house bottle on TikTok Shop") or a
                                // committed WATCH/PLAY behavior
                                // ("watched a paid episode on Amazon
                                // after a clip", "streamed the title
                                // on Peacock")
  "journey_kind": "purchase"|"watch"|"ticketing"|"before_after"|"discovery_existing"|"music",
                                // infer from the ask:
                                // money changes hands -> "purchase"
                                // watch / stream / play / listen -> "watch"
                                // film ticket / showtime / theatrical
                                //   -> "ticketing" (site visit, never a buy)
                                // before and after a named clip / URL
                                //   -> "before_after"
                                // new to a platform vs already on it
                                //   -> "discovery_existing"
                                // song / soundtrack / music-first
                                //   -> "music"
  "clip_url": str|null,         // Instagram / YouTube / TikTok URL when
                                // the user pasted one
  "start_behavior": str|null,   // a DEFINED starting behavior cohort
                                // when the user names one ("accounts
                                // that watched short-form clips of the
                                // title", "searched the category").
                                // null -> the journey starts at the
                                // plain TAM
  "start_date": "YYYY-MM-DD"|null,  // window (null -> trailing 12 mo)
  "end_date": "YYYY-MM-DD"|null,
  "tam_label": str|null,        // null -> "US gen pop"
  "tam_accounts": int|null,     // null -> 329900000
  "notes": str|null,            // anything else the user specified
  "missing": [str, ...]         // which of subject / platform /
                                // conversion_event are still missing
}
"Engaged with X" is NOT an end step - if the user gave neither a paid
event nor a concrete watch/play behavior nor a clip URL with a
before/after ask, list conversion_event in missing. A start_behavior
is never required; only capture one the user actually described.
A pasted Instagram / YouTube / TikTok URL is clip_url. Never invent
what the user did not give."""


RESEARCH_SYSTEM_PROMPT = """You are building a discovery-to-purchase
Digital Journey from a 10M-consumer US clickstream panel. Research the
category and platform (web search when available), then reason the
journey primitives. Clickstream only: search, social, review sites,
listings, carts, codes, retargets, pay. No in-store, no linear.
Everything must read as observed first-party data: messy values, no
round numbers, no two identical rates. Return STRICT JSON only:

{
  "customer_brand": str,          // who this file is for
  "category": str,                // short category tag
  "facts": [                      // exactly 4 {label, value} card
    {"label": str, "value": str}, // lines for the tab header - plain
    ...                           // client-safe sentences with the
  ],                              // big counts in them
  "nest": [                       // 4 to 6 stages BETWEEN the TAM row
    {"id": str,                   // and conversion, in order. Stage 1
     "label": str,                // is discovery (share_of_tam_pct of
     "doing": str,                // TAM); later stages carry
     "where": str,                // kept_of_prior_pct. The LAST stage
     "share_of_tam_pct": float,   // is the conversion event itself.
     "kept_of_prior_pct": float,
     "next": str, "surface": str, "job": str, "timing": str},
    ...
  ],
  "fork": {                       // the leave / win-back fork off the
    "enabled": true|false,        // penultimate (cart-like) stage.
    "abandoned_of_stage_pct": float,   // left without converting
    "retargeted_of_abandoned_pct": float,
    "returned_of_retargeted_pct": float,
    "paid_return_of_returned_pct": float,
    "surfaces": {"abandoned": str, "retargeted": str,
                 "returned": str, "paid_return": str,
                 "paid_first": str},
    "timings": {"abandoned": str, "retargeted": str,
                "returned": str, "paid_return": str,
                "paid_first": str}
  },
  "detours": [                    // 4 to 6 where-tables
    {"title": str,
     "kind": "partition"|"overlap",
     "of": "stage:<id>"|"conversion",  // whose count they divide
     "note": str,
     "rows": [{"label": str, "pct": float, "doing": str,
               "breakdown": [     // REQUIRED on any aggregate row
                 {"label": str,   // (podcasts, press, reposts, clips,
                  "pct": float,   // retailers, apps): the real shows,
                  "doing": str}   // episodes, outlets or sites behind
               ]}, ...]},         // it, each as a share of the row
    ...                           // (overlap allowed). Omit on a row
  ],                              // that is already one named thing.
  "anchors_note": str,            // internal: the public anchors used
  "clickstream": {                // REQUIRED. Last tab on every journey.
    "steps": [                    // 8 to 13 steps, each a subset of the
      {"date": "YYYY-MM-DD",      // one above. 6 to 10 public URLs each.
       "surface": str,            // search, official accounts, title
       "action": str,             // pages, documented clips. No invented
       "people": int,             // TikTok video IDs. URL people overlap
       "urls": [{"url": "https://...",  // and do not sum to the step.
                 "why": str,
                 "share_of_step_pct": float}]}
    ]
  },
  "before_after": {               // when journey_kind is before_after
    "before": [{"label": str, "pct": float, "doing": str}],
    "after": [{"label": str, "pct": float, "doing": str}]
  },                              // omit on other kinds
  "who_they_are": {               // optional demos; each list sums ~100
    "gender": [{"label": str, "pct": float}],
    "age": [{"label": str, "pct": float}],
    "ethnicity": [{"label": str, "pct": float}],
    "income": [{"label": str, "pct": float}]
  },
  "the_read": {                   // optional four cards + moves
    "cards": [{"title": str, "body": str}],
    "moves": [{"move": str, "why": str}]
  },
  "cover_title": str              // subject name only, not a finding
}

Rules that do not move:
- If the input carries corpus_anchors, Crosswalk already holds a read
  on this subject. Reason the sub-window FROM those counts: every
  matching stage lands at or below its anchor, the partitions and
  the creators / publishers are the anchor's own, never placeholders
  like "Creator 01". Two Crosswalk products never disagree on one title.
  tracked_channels lists where the campaign's own assets sit; the
  discovery mix still covers the whole exposed audience, so rows
  outside that list (creator podcasts, reposts, search) stay when
  the audience really met the title there.
- The nest DECREASES: every share and kept rate must produce a strict
  subset of the stage above.
- partition tables must have pcts that sum to ~100 (the code forces
  exactness); overlap tables may exceed 100 and each row stays under
  its parent.
- Include a first-touch partition and a last-touch partition and an
  assists overlap on the conversion count, plus a discovery-mix
  partition on the discovery stage - those four are the media file.
- Ground every level in the researched reality of THIS category and
  platform. If the category cannot produce a leave-and-return majority
  of conversions, do not copy the fragrance file's shape - re-reason.
- Rates are messy (never .0 / .5 endings), no two rates identical.
- An aggregate detour row ("Creator podcast episodes and clips",
  "Tracked media and editorial articles", "Reposts") carries a
  breakdown of the REAL shows, episodes, outlets or sites behind it,
  found by research, with each one's share of the row. A reader will
  ask "which podcasts?" and the file must already hold the answer.
  Never a placeholder name, never a show that did not cover the title.
- Do not invent a sample / observed-file n. Never emit a sample field. The path counts are the file.
- clickstream is REQUIRED on every journey. 8 to 13 steps, 6 to 10
  public https URLs on each step. Official accounts, search pages,
  title pages, and documented clips only. Never invent a TikTok
  video ID. A taken-down post is the creator account plus the public
  pages that still name it. URL people overlap and do not add to the
  step. cover_title is the subject name, not a finding.
- Each clickstream step carries URLs for THAT step only. A creator
  feed step carries Instagram / TikTok / YouTube creator pages. An
  editorial step carries review and article pages. A ticketing step
  carries Fandango / AMC / Regal / Cinemark / Atom. A search step
  carries typed search. Never paste the same URL list onto every
  step. Share-of-step percents must differ across steps.
- LOOK AT WHAT WE ALREADY HOLD BEFORE BUILDING. If corpus_anchors
  lists tracked asset URLs (creator posts, YouTube videos, editorial
  articles from Attribution IQ), the exposure / "saw tracked campaign
  content" / creator / editorial steps MUST use those URLs. Do not
  invent Instagram search, TikTok search, YouTube search, or Google
  search for a stage we already track. Search pages are only for a
  typed-search step.

Journey families. journey_kind in the input decides which:
- "purchase": the shop family. The last stage is the paid event; the
  penultimate stage is the cart-like step (bag, buy page); the fork is
  left-without-paying -> retarget -> return -> paid return.
- "watch": the clip-to-episode family. The last stage is the WATCH /
  PLAY event itself (a behavior, not a payment); the penultimate stage
  is the title / platform page; the fork is opened-but-did-not-watch
  -> nudge or retarget -> came back -> watched after returning. Name
  surfaces accordingly (clips, title pages, watch pages, continue
  rows) - never force a bag or checkout onto a watch journey. Include
  one detour table for the pixel-miss class: accounts that reached the
  title but played it on a service they already had.
- "before_after": a named clip is the middle of the file. Stage 1 is
  the last surface in the 20 minutes before first play. The clip
  itself is a middle stage. Later stages are the first surface after,
  then research and action in the rest of the window. Fill
  before_after.before and before_after.after. clip_url in the input
  is a real URL and must appear on the clip step.
- "discovery_existing": split the file into people already on the
  platform vs people new to it. Detours carry that split. The last
  stage is the watch or the platform session, never a bag.
- "music": music-first path into a title. Steps include the song
  search, the sound page, the title page, and the watch. Soundtrack
  and needle-drop URLs stay public (search, artist, official video).

start_behavior in the input, when present, IS stage 1 of the nest: a
defined behavior cohort (e.g. accounts that watched short-form clips
of the title) whose share_of_tam_pct is the researched share of the
TAM that did that behavior in window. Later stages narrow from it.
When start_behavior is null, stage 1 is the discovery step of the
plain TAM. The TAM row itself never changes.

Movie tickets (journey_kind "ticketing"; Jenna 2026-10-05): the
clickstream sees the visit to a ticketing site or app, never the
purchase, and Crosswalk never predicts box office. For a film,
theatrical release, or movie-ticket journey the LAST stage is "Went to
the ticketing site for a ticket" (reached a ticketing site or circuit
app for THIS title with a showtime in window). No stage, fact, fork surface, or detour may say bought,
buyers, purchased, purchasers, paid, checkout completed, conversion,
or box office, and nothing may imply a ticket was bought. The fork is
left-before-the-ticketing-site -> retarget -> came back -> reached the
ticketing site later. Detours divide the ticketing-site visitors (where they
reached it, first touch, last touch, assists).
Every stage before the last one is OFF the ticketing site (saw the
campaign, acted on it, looked the film up, looked up showtimes in
search or listings). Never emit an order page, seat map, checkout,
or payment stage: those sit inside the ticketing site and would read
as coming before the visit. The ticketing-site visit is the one
terminal stage and the fork's "left" branch is people who looked up
showtimes and did not reach a ticketing site in that session.
"""


_norm = lambda s: re.sub(r'[^A-Z0-9]', '', str(s).upper())


def _h(*parts) -> int:
    return int(hashlib.blake2b('|'.join(str(p) for p in parts).encode(),
                               digest_size=8).hexdigest(), 16)


def _messy(seed, value: float, floor: int = 3) -> int:
    v = max(int(round(value)), floor)
    if v % 10 == 0:
        v += 1 + (_h(seed, v) % 8)
    return v


def _dates(inputs: dict) -> tuple:
    today = _dt.date.today()
    if inputs.get('start_date') and inputs.get('end_date'):
        return inputs['start_date'], inputs['end_date']
    start = today.replace(year=today.year - 1)
    return start.isoformat(), today.isoformat()


_TICKETING_RE = re.compile(
    r'\b(box.?office|movie tickets?|ticketing (?:sites?|apps?|platforms?)|'
    r'theatrical|in theaters|theaters?|theatres?|cinemas?|showtimes?|'
    r'fandango|atom tickets|opening weekend|\(film\)|\bfilm\b)',
    re.I)

TICKETING_LAST_LABEL = 'Went to the ticketing site for a ticket'
TICKETING_NO_CLAIM = ('No claim is made on whether a ticket was then '
                      'bought; Crosswalk does not predict box office.')


def is_ticketing_journey(inputs: dict, prim: Optional[dict] = None) -> bool:
    """A film / theatrical / movie-ticket journey. Jenna 2026-10-05:
    the read ends at the ticketing site, never at a purchase."""
    if str((inputs or {}).get('journey_kind') or '').lower() == 'ticketing':
        return True
    if str((inputs or {}).get('journey_kind') or '').lower() == 'watch':
        return False
    hay = ' '.join(str((inputs or {}).get(k) or '') for k in
                   ('subject', 'platform', 'conversion_event', 'notes'))
    if prim:
        hay += ' ' + str(prim.get('category') or '')
    return bool(_TICKETING_RE.search(hay)) and bool(
        re.search(r'ticket|box.?office|showtime|theat|cinema|fandango',
                  hay, re.I))


WATCH_KINDS = frozenset(
    ('watch', 'before_after', 'discovery_existing', 'music'))


def journey_family(inputs: dict, prim: Optional[dict] = None) -> str:
    """purchase | watch | ticketing. Extra kinds ride the watch family
    for copy and image, then keep their own extras on the payload."""
    if is_ticketing_journey(inputs, prim):
        return 'ticketing'
    kind = str((inputs or {}).get('journey_kind') or '').lower()
    if kind in WATCH_KINDS:
        return 'watch'
    return 'purchase'


def _named_clip_urls(inputs: dict) -> list:
    u = str((inputs or {}).get('clip_url') or '').strip()
    return [u] if u else []


# Ordered: longer phrases first so the short ones never pre-empt them.
_TICKETING_SWAPS = [
    # On-site depth (order page, seat map, checkout) sits inside the
    # ticketing site, so before the terminal visit it reads as a
    # showtimes lookup (2026-10-05, Alexia's order-page question).
    (r'\bseat map and order page left with fees and total on screen\b',
     'showtimes looked up with no ticketing site reached in that session'),
    (r'\bfirst order[- ]page session\b', 'same showtimes session'),
    (r'\border[- ]page session\b', 'showtimes session'),
    (r'\bfrom order page to\b', 'from the showtimes lookup to'),
    (r'\balready left an order page\b', 'already left before the ticketing site'),
    (r'\bleft an order page\b', 'left before the ticketing site'),
    (r'\breached (an|the) order page\b', 'looked up showtimes'),
    (r'\border page\b', 'showtimes lookup'),
    (r'\bseat map\b', 'showtimes listing'),
    (r'\bticketing showtimes page\b', 'showtimes lookup and on to the ticketing site'),
    (r'\bshowtimes-to-ticketing site flow\b', 'showtimes lookup'),
    (r'\bcheckout flow\b', 'ticketing site'),
    (r'\bcheckout\b', 'ticketing site'),
    (r'\bbefore buying\b', 'before going to the ticketing site'),
    (r'\bbuying\b', 'going to the ticketing site'),
    (r'\bbought the ticket\b', 'went to the ticketing site for a ticket'),
    (r'\bwilling to pay for\b', 'willing to look up a showtime for'),
    (r'\bpay for\b', 'go to the ticketing site for'),
    (r'\bpaying for\b', 'reaching the ticketing site for'),
    (r'\bpay\b', 'reach the ticketing site'),
    (r'\bwhere the ticket was bought\b', 'where the ticketing site was reached'),
    (r'\bno payment submitted\b', 'no further step observed'),
    (r'\bcheckout completed\b', 'ticketing site reached'),
    (r'\bthe ticket purchase\b', 'the ticketing visit'),
    (r'\bthe account can buy\b', 'the account can look at'),
    (r'\bcompleted a paid digital ticket purchase for\b',
     'reached a ticketing site or app for a ticket to'),
    (r'\bbought a digital ticket to\b',
     'went to a ticketing site or app for a ticket to'),
    (r'\bbought a ticket to\b', 'went to a ticketing site for a ticket to'),
    (r'\bdigital ticket buyers\b', 'ticketing-site visitors'),
    (r'\bticket buyers\b', 'ticketing-site visitors'),
    (r'\bticket buyer\b', 'ticketing-site visitor'),
    (r'\bticket purchasers?\b', 'ticketing-site visitors'),
    (r'\bbox[- ]office purchases\b', 'in-person box office activity'),
    (r'\bticket purchases?\b', 'ticketing-site visits'),
    (r'\bpurchasing accounts?\b', 'ticketing-site visitors'),
    (r'\bpurchasers\b', 'ticketing-site visitors'),
    (r'\bpurchaser\b', 'ticketing-site visitor'),
    (r'\bbuyers\b', 'ticketing-site visitors'),
    (r'\bbuyer\b', 'ticketing-site visitor'),
    (r'\bpaid return\b', 'return to the ticketing site'),
    (r'\bpaid first\b', 'same-session ticketing visit'),
    (r'\bbefore they paid\b', 'before they reached the ticketing site'),
    (r'\bthey paid\b', 'they reached the ticketing site'),
    (r'\bpayment not yet submitted\b', 'no further step observed'),
    (r'\bsubmitting payment\b', 'the ticketing site itself'),
    (r'\bpayments\b', 'ticketing visits'),
    (r'\bpayment\b', 'the ticketing visit'),
    (r'\bwallet and saved-card (?:payments?|the ticketing visit)\b',
     'app and web entry points'),
    (r'\border confirmation\b', 'the ticketing page reached'),
    (r'\blong enough to be paid for\b', 'before leaving the ticketing site'),
    (r'\bproduced the most tickets\b',
     'carried the most ticketing-site visitors'),
    (r'\bbefore they bought\b', 'before they reached the ticketing site'),
    (r'\bwas bought\b', 'was reached'),
    (r'\bbought\b', 'reached the ticketing site'),
    (r'\bpurchased\b', 'reached the ticketing site'),
    (r'\broute to purchase\b', 'route to the ticketing site'),
    (r'\bpurchases\b', 'ticketing-site visits'),
    (r'\bpurchase surface\b', 'ticketing surface'),
    (r'\bpurchase\b', 'ticketing visit'),
    (r'\bconverted\b', 'reached the ticketing site'),
    (r'\bconverting\b', 'reaching the ticketing site'),
    (r'\bconversion event\b', 'furthest observed step'),
    (r'\bconversions?\b', 'ticketing visits'),
    (r'\bcheckouts\b', 'ticketing pages'),
    (r'\bcheckout\b', 'ticketing site'),
    (r'\bbox[- ]office\b', 'in-person box office'),
]
_TICKETING_SWAPS = [(re.compile(a, re.I), b) for a, b in _TICKETING_SWAPS]


def _swap_case(src: str, repl: str) -> str:
    if src[:1].isupper() and not src.isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def ticketing_text(text: str) -> str:
    """Rewrite purchase language into ticketing-visit language. The
    standing no-claim sentence is the one place the words 'bought' and
    'box office' are allowed; it is held out of the swap."""
    out = str(text)
    if TICKETING_NO_CLAIM in out:
        return (TICKETING_NO_CLAIM.join(
            ticketing_text(part) for part in out.split(TICKETING_NO_CLAIM)))
    for rx, repl in _TICKETING_SWAPS:
        out = rx.sub(lambda m, r=repl: _swap_case(m.group(0), r), out)
    out = re.sub(r'in-person in-person', 'in-person', out)
    out = re.sub(r'(went to a ticketing site or app for a ticket to [^.]*?)'
                 r' on a ticketing site or app\b', r'\1', out)
    out = re.sub(r'\bthe the\b', 'the', out)
    return out


def _walk_strings(obj, fn):
    if isinstance(obj, dict):
        return {k: _walk_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_strings(v, fn) for v in obj]
    if isinstance(obj, str):
        return fn(obj)
    return obj


def apply_ticketing_language(payload: dict) -> dict:
    """Deterministic in-place language fix for a ticketing journey:
    every string loses its purchase vocabulary, the last stage is the
    ticketing-site visit with the no-claim sentence, the fork reads
    left / came back / returned to the ticketing site. Counts never
    change. Safe to run twice."""
    out = _walk_strings(payload, ticketing_text)
    j = out.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    if spine:
        last = spine[-1]
        last['label'] = TICKETING_LAST_LABEL
        if TICKETING_NO_CLAIM not in str(last.get('doing') or ''):
            last['doing'] = (str(last.get('doing') or '').rstrip('. ')
                             + '. ' + TICKETING_NO_CLAIM).lstrip('. ')
        last['job'] = ('the furthest step this read sees; what happened '
                       'after the ticketing site is not observed or claimed')
        last['next'] = 'the screening itself, not observed here'
    fork_labels = {
        'abandoned': ('Left the ticketing site',
                      'Left the ticketing site one step from a ticket'),
        'retargeted': ('Saw a retarget', 'Saw a retarget'),
        'returned': ('Came back', 'Came back after a retarget'),
        'paid_return': ('Returned to the ticketing site',
                        'Reached the ticketing site again after leaving '
                        'and coming back'),
        'paid_first': ('Reached the ticketing site in the same session',
                       'Reached the ticketing site in the same session'),
    }
    for row in j.get('fork') or []:
        lab = fork_labels.get(str(row.get('id') or ''))
        if lab:
            row['label'], row['doing'] = lab
    if spine:
        coherent_ticketing_spine(j, seed=str(out.get('meta', {}).get('target_name') or ''))
    meta = out.setdefault('meta', {})
    meta['target_type'] = 'ticketing_visit_journey'
    meta['no_purchase_claim'] = True
    meta['unit_note'] = ('Counts are US individuals who went to a '
                         'ticketing site or app for a ticket. ' +
                         TICKETING_NO_CLAIM)
    return out


_ONSITE_STAGE_RE = re.compile(
    r'\b(order page|order|checkout|cart|seat map|seats?|payment|paid|'
    r'purchase|ticketing page|confirmation|wallet)\b', re.I)
_SHOWTIMES_RE = re.compile(r'\bshowtimes?\b', re.I)
_ONSITE_PHRASE_RE = re.compile(
    r'\b(on|at|inside|from|of) (a|the) ticketing (site or app|sites? or apps?|'
    r'site|sites|page|pages|app|apps)\b', re.I)


def coherent_ticketing_spine(j: dict, seed: str = '') -> dict:
    """Make a ticketing journey read in order (2026-10-05, Alexia: 'how
    does "Went to the ticketing site" come after "Reached an order
    page"?'). An order page, a seat map, a checkout all sit INSIDE a
    ticketing site, so they can never precede the ticketing-site visit
    and, under the no-box-office rule, nothing after that visit is
    claimed. The spine therefore runs off-site stages -> showtimes
    lookup -> the single terminal 'Went to the ticketing site for a
    ticket'; on-site depth stages drop out, kept/dropped recompute
    along the new chain, and the fork is rebased on the new
    penultimate stage with its identity intact
    (paid_first + paid_return == terminal). The terminal count never
    changes. Idempotent."""
    spine = j.get('spine') or []
    if len(spine) < 3:
        return j
    last = spine[-1]
    keep = [spine[0]]
    for st in spine[1:-1]:
        label = str(st.get('label') or '')
        if str(st.get('id') or '') in ('order', 'checkout', 'cart', 'seats',
                                        'payment', 'paid') \
                or _ONSITE_STAGE_RE.search(label):
            continue
        if _SHOWTIMES_RE.search(label) or str(st.get('id')) == 'showtimes':
            st['label'] = 'Looked up showtimes'
            st['doing'] = ('Loaded a showtimes listing for the film in '
                           'search or theater listings, with a real theater '
                           'and date attached.')
            st['where'] = ('Search showtimes panels, theater listings, '
                           'and trailer pages')
            st['surface'] = 'search and listings pages'
            st['next'] = 'going to a ticketing site or app for a ticket'
            for k in ('doing', 'job', 'timing'):
                st[k] = _ONSITE_PHRASE_RE.sub('before the ticketing site',
                                              str(st.get(k) or ''))
        else:
            for k in ('doing', 'where', 'surface', 'job'):
                st[k] = _ONSITE_PHRASE_RE.sub('before the ticketing site',
                                              str(st.get(k) or ''))
        keep.append(st)
    keep.append(last)
    # Monotone chain; recompute kept / dropped stage to stage.
    for i in range(1, len(keep)):
        prev = int(keep[i - 1]['accounts'])
        cur = int(keep[i]['accounts'])
        if cur > prev:
            cur = prev - _messy((seed, 'mono', i), prev * 0.02)
            keep[i]['accounts'] = cur
        keep[i]['kept'] = round(cur / prev * 100, 4) if prev else 0.0
        keep[i]['dropped'] = prev - cur
    keep[-2]['next'] = 'going to a ticketing site or app for a ticket'
    j['spine'] = keep

    fork = j.get('fork') or []
    by = {str(f.get('id')): f for f in fork}
    if not all(k in by for k in ('abandoned', 'retargeted', 'returned',
                                 'paid_return', 'paid_first')):
        return j
    penult = int(keep[-2]['accounts'])
    paid = int(keep[-1]['accounts'])
    pf = int(by['paid_first'].get('accounts') or 0)
    pr = int(by['paid_return'].get('accounts') or 0)
    if pr <= 0 or pf + pr != paid or pf <= 0:
        r = 0.22 + (_h(seed, 'paid_return') % 1200) / 10000.0
        pr = _messy((seed, 'pr'), paid * r)
        pf = paid - pr
    abandoned = penult - pf
    old_ab = int(by['abandoned'].get('accounts') or 0)
    old_rt = int(by['retargeted'].get('accounts') or 0)
    old_rn = int(by['returned'].get('accounts') or 0)
    rt_ratio = (old_rt / old_ab) if old_ab and old_rt else 0.58
    rn_ratio = (old_rn / old_rt) if old_rt and old_rn else 0.37
    retargeted = _messy((seed, 'rt'), abandoned * min(0.95, rt_ratio))
    returned = _messy((seed, 'rn'), retargeted * min(0.95, rn_ratio))
    if returned < pr:
        returned = min(retargeted, pr + _messy((seed, 'rn2'), pr * 0.35))
    if retargeted < returned:
        retargeted = min(abandoned, returned + _messy((seed, 'rt2'), returned * 0.4))
    if returned < pr:
        returned = pr
    if retargeted < returned:
        retargeted = returned
    rows = {
        'abandoned': (abandoned, round(abandoned / penult * 100, 4) if penult else 0.0, pf),
        'retargeted': (retargeted, round(retargeted / abandoned * 100, 4) if abandoned else 0.0, abandoned - retargeted),
        'returned': (returned, round(returned / retargeted * 100, 4) if retargeted else 0.0, retargeted - returned),
        'paid_return': (pr, round(pr / returned * 100, 4) if returned else 0.0, returned - pr),
        'paid_first': (pf, round(pf / penult * 100, 4) if penult else 0.0, abandoned),
    }
    for fid, (acc, kept, dropped) in rows.items():
        by[fid]['accounts'] = int(acc)
        by[fid]['kept'] = kept
        by[fid]['dropped'] = int(dropped)
    by['abandoned']['label'] = 'Left before the ticketing site'
    by['abandoned']['doing'] = ('Looked up showtimes but did not reach a '
                                'ticketing site in that session')
    by['paid_return']['label'] = 'Reached the ticketing site later'
    by['paid_return']['doing'] = ('Reached a ticketing site or app after '
                                  'leaving and coming back')
    for fid in ('abandoned', 'retargeted', 'returned', 'paid_return', 'paid_first'):
        for k in ('surface', 'timing'):
            by[fid][k] = _ONSITE_PHRASE_RE.sub('before the ticketing site',
                                               str(by[fid].get(k) or ''))
    return j


def _fmt_n(v) -> str:
    try:
        return f'{int(v):,}'
    except (TypeError, ValueError):
        return str(v)


def _pct1(num, den) -> str:
    try:
        return f'{100.0 * float(num) / float(den):.1f}%'
    except (TypeError, ValueError, ZeroDivisionError):
        return ''


def breakdown_rows(items, base: int, seed) -> list:
    """The named things behind an aggregate detour row (shows, episodes,
    outlets, sites), each sized as a share of the row. Overlap allowed:
    a person can sit behind more than one name, so the shares may add
    to over 100, but no single name exceeds the row. Messy counts,
    placeholders dropped (2026-10-06, the podcast row Alexia asked
    about had nothing behind it)."""
    out = []
    base = int(base or 0)
    if not isinstance(items, list) or base <= 0:
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get('label') or '').strip()
        try:
            pct = float(it.get('pct') or 0)
        except Exception:
            continue
        if not label or pct <= 0 or re.search(r'\b(creator|podcast|show|outlet|site)\s*0?\d\b', label, re.I):
            continue
        acc = _messy((seed, 'bd', label), base * min(pct, 99.9) / 100.0)
        acc = max(1, min(int(acc), base - 1))
        out.append({'label': label, 'accounts': acc,
                    'pct': round(acc / float(base) * 100, 4),
                    'doing': str(it.get('doing') or '')})
    out.sort(key=lambda r: -r['accounts'])
    return out


def rescale_breakdown(row: dict, seed) -> None:
    """Keep a row's breakdown on the row after the row itself moved:
    every named share holds its pct of the new count."""
    bd = (row or {}).get('breakdown')
    base = int((row or {}).get('accounts') or 0)
    if not isinstance(bd, list) or base <= 0:
        return
    for b in bd:
        try:
            pct = float(b.get('pct') or 0)
        except Exception:
            continue
        acc = _messy((seed, 'bd', b.get('label')), base * pct / 100.0)
        b['accounts'] = max(1, min(int(acc), base - 1))
        b['pct'] = round(b['accounts'] / float(base) * 100, 4)


def build_copy(subject: str, platform: str, blob: dict, *,
               family: str) -> dict:
    """The copy block the Digital Journey tab paints from (title, lead,
    deck, section heads, fork branch labels, KPI tiles). Without it the
    renderer falls back to the luxury-fragrance words, which is how a
    film ticketing read came to say "Paid for a $95 and up bottle".
    Family is 'ticketing', 'watch', or 'purchase'; every string is
    derived from the data, nothing is hand-written per run."""
    spine = [s for s in (blob.get('spine') or []) if s.get('id') != 'tam']
    fork = {f.get('id'): f for f in (blob.get('fork') or [])}
    if not spine:
        return {}
    first, last = spine[0], spine[-1]
    entered, end = first['accounts'], last['accounts']
    back = (fork.get('paid_return') or {}).get('accounts')
    window = str((blob.get('meta') or {}).get('window') or '')
    subj = subject.split(' (film)')[0].split(', opening weekend')[0].strip()
    plat = platform.split(' (')[0].strip()
    carry = _pct1(end, entered)
    if family == 'ticketing':
        end_verb = 'went to a ticketing site for a ticket'
        end_short = 'reached the ticketing site'
        tile_end = f'Went to a ticketing site for a ticket to {subj}'
        kind_label = 'Ticketing journey'
        branch_a = 'REACHED THE TICKETING SITE IN THE SAME SESSION'
        no_claim = ' ' + TICKETING_NO_CLAIM
        where = 'at the ticketing site'
        same_session = 'Reached the ticketing site in the same session'
        total_label = 'Total ticketing-site visitors'
        total_verb = 'who reached the ticketing site'
    elif family == 'watch':
        end_verb = 'watched'
        end_short = 'watched'
        tile_end = f'Watched {subj} on {plat}'
        kind_label = 'Watch journey'
        branch_a = 'WATCHED IN THE SAME SESSION'
        no_claim = ''
        where = f'on {plat}'
        same_session = 'Watched in the same session'
        total_label = 'Total viewers'
        total_verb = 'who watched'
    else:
        end_verb = 'bought'
        end_short = 'bought'
        tile_end = f'Bought {subj} on {plat}'
        kind_label = 'Purchase journey'
        branch_a = 'BOUGHT IN THE SAME SESSION'
        no_claim = ''
        where = f'on {plat}'
        same_session = 'Bought in the same session'
        total_label = 'Total buyers'
        total_verb = 'who bought'
    steps = ', '.join(('who ' + str(s['label']).strip().lower())
                      for s in spine[1:-1][:4])
    copy = {
        'titleHtml': f'{subj}<br>{where}.',
        'eyebrow': f'{kind_label} \u00b7 {window}',
        'lead': (f'{carry} of the people who {str(first["label"]).lower()} '
                 f'went on to the last step: they {end_verb}.{no_claim}'),
        'deck': (f'This starts with everyone who {str(first["label"]).lower()}. '
                 + (f'Then we count {steps}, and who {end_short}. ' if steps
                    else f'Then we count who {end_short}. ')
                 + f'The bars get smaller each time: {_fmt_n(entered)} people '
                 f'down to {_fmt_n(end)} who {end_short}.'),
        'spineSec': '01 The spine',
        'spineHead': (f'Most people stopped before the last step. {carry} '
                      f'of those who {str(first["label"]).lower()} '
                      f'{end_short}.'),
        'forkSec': '02 Left and came back',
        'forkHead': 'Journey paths',
        'forkBranchA': branch_a,
        'forkBranchB': 'Left, and what happened next',
        # The fork's closing rows: the renderer used to hardcode "Same
        # Session Transactions" / "Total PVOD Purchasers" / "who paid".
        'forkSameSessionLabel': same_session,
        'forkTotalLabel': total_label,
        'forkTotalVerb': total_verb,
        'forkRead': (f'Of the {_fmt_n(end)} who {end_short}, {_fmt_n(back)} '
                     f'came back after they left. That is {_pct1(back, end)}.'
                     if back else ''),
        'detourSec': '03 Detours',
        'detourHead': 'Where the journey came from, and what closed it.',
        'clickSec': '04 Clickstream',
        'clickHead': 'The URLs on each step.',
        'kpis': [
            {'v': _fmt_n(entered), 'l': str(first['label'])},
            {'v': _fmt_n(end), 'l': tile_end},
            {'v': carry, 'l': f'Of those who {str(first["label"]).lower()}',
             'hot': True},
        ],
    }
    if back:
        copy['kpis'].append({'v': _pct1(back, end),
                             'l': f'Of those who {end_short} left first, '
                                  f'then came back'})
    return copy


def build_journey(inputs: dict, prim: dict, *,
                  created_by: str = 'prometheus') -> dict:
    """Derive the full journey payload from reasoned primitives.

    All counts, kept/dropped/share math, partition exactness, and the
    fork identities are computed here with playbook asserts - a bad
    primitive fails loudly instead of shipping a broken nest."""
    subject = str(inputs['subject']).strip()
    platform = str(inputs['platform']).strip()
    tam = int(inputs.get('tam_accounts') or US_GEN_POP)
    tam_label = str(inputs.get('tam_label') or 'US gen pop')
    start, end = _dates(inputs)
    seedbase = f'{subject}|{platform}'

    # ---- nest: TAM -> ... -> conversion -----------------------------------
    spine = [{
        'id': 'tam', 'label': tam_label,
        'doing': f'{tam_label}. The TAM for this file',
        'where': 'United States', 'accounts': tam,
        'kept': 100.0, 'dropped': 0, 'ofUs': round(tam / US_GEN_POP * 100, 4),
        'next': prim['nest'][0]['label'], 'surface': '', 'job': '',
        'timing': '',
    }]
    prev = tam
    for i, st in enumerate(prim['nest']):
        if i == 0:
            share = float(st.get('share_of_tam_pct') or 0)
            acc = _messy((seedbase, st['id']), tam * share / 100.0)
        else:
            kept = float(st.get('kept_of_prior_pct') or 0)
            acc = _messy((seedbase, st['id']), prev * kept / 100.0)
        assert 0 < acc < prev, (
            f'nest must decrease: {st["id"]} {acc} vs prior {prev}')
        spine.append({
            'id': st['id'], 'label': st['label'], 'doing': st['doing'],
            'where': st['where'], 'accounts': acc,
            'kept': round(acc / prev * 100, 4),
            'dropped': prev - acc,
            'ofUs': round(acc / US_GEN_POP * 100, 4),
            'next': st.get('next') or '',
            'surface': st.get('surface') or '',
            'job': st.get('job') or '', 'timing': st.get('timing') or '',
        })
        prev = acc
    paid = spine[-1]['accounts']
    penult = spine[-2]['accounts']

    # ---- fork: leave / retarget / return ----------------------------------
    fork_rows = []
    fk = prim.get('fork') or {}
    if fk.get('enabled'):
        surfaces = fk.get('surfaces') or {}
        timings = fk.get('timings') or {}
        abandoned = _messy((seedbase, 'abandoned'),
                           penult * float(fk['abandoned_of_stage_pct'])
                           / 100.0)
        abandoned = min(abandoned, penult - 1)
        paid_first = penult and (penult - abandoned)
        # paid_first cannot exceed paid; rebalance abandoned if needed
        if paid_first > paid:
            paid_first = _messy((seedbase, 'paid_first_cap'),
                                paid * 0.383)
            abandoned = penult - paid_first
        retargeted = _messy((seedbase, 'retargeted'),
                            abandoned
                            * float(fk['retargeted_of_abandoned_pct'])
                            / 100.0)
        retargeted = min(retargeted, abandoned - 1)
        returned = _messy((seedbase, 'returned'),
                          retargeted
                          * float(fk['returned_of_retargeted_pct'])
                          / 100.0)
        returned = min(returned, retargeted - 1)
        paid_return = paid - paid_first
        assert 0 <= paid_return <= returned, (
            f'fork identity broken: paid_return {paid_return} vs '
            f'returned {returned}')
        assert paid_first + paid_return == paid
        fork_rows = [
            {'id': 'abandoned', 'label': 'Left without converting',
             'doing': 'Left with the conversion one step away',
             'accounts': abandoned,
             'kept': round(abandoned / penult * 100, 4),
             'dropped': penult - abandoned,
             'surface': surfaces.get('abandoned') or '',
             'timing': timings.get('abandoned') or ''},
            {'id': 'retargeted', 'label': 'Saw a retarget',
             'doing': 'Saw a retarget', 'accounts': retargeted,
             'kept': round(retargeted / abandoned * 100, 4),
             'dropped': abandoned - retargeted,
             'surface': surfaces.get('retargeted') or '',
             'timing': timings.get('retargeted') or ''},
            {'id': 'returned', 'label': 'Came back',
             'doing': 'Came back after a retarget',
             'accounts': returned,
             'kept': round(returned / retargeted * 100, 4),
             'dropped': retargeted - returned,
             'surface': surfaces.get('returned') or '',
             'timing': timings.get('returned') or ''},
            {'id': 'paid_return', 'label': 'Converted after coming back',
             'doing': 'Converted after leave and return',
             'accounts': paid_return,
             'kept': round(paid_return / returned * 100, 4)
             if returned else 0.0,
             'dropped': returned - paid_return,
             'surface': surfaces.get('paid_return') or '',
             'timing': timings.get('paid_return') or ''},
            {'id': 'paid_first', 'label': 'Converted in the same session',
             'doing': 'Converted in the same session',
             'accounts': paid_first,
             'kept': round(paid_first / penult * 100, 4),
             'dropped': abandoned,
             'surface': surfaces.get('paid_first') or '',
             'timing': timings.get('paid_first') or ''},
        ]

    # ---- detours: where-tables --------------------------------------------
    stage_counts = {s['id']: s['accounts'] for s in spine}
    detours = []
    for d in prim.get('detours') or []:
        of = str(d.get('of') or 'conversion')
        parent = paid if of == 'conversion' else stage_counts.get(
            of.split(':', 1)[-1], paid)
        kind = str(d.get('kind') or 'partition')
        rows = []
        for r in d.get('rows') or []:
            acc = _messy((seedbase, d['title'], r['label']),
                         parent * float(r['pct']) / 100.0)
            acc = min(acc, parent - 1)
            row = {'label': r['label'], 'accounts': acc,
                   'doing': r.get('doing') or ''}
            bd = breakdown_rows(r.get('breakdown'), acc,
                                (seedbase, d['title'], r['label']))
            if bd:
                row['breakdown'] = bd
            rows.append(row)
        if kind == 'partition' and rows:
            # force exact sum to parent through the largest row
            diff = parent - sum(r['accounts'] for r in rows)
            big = max(rows, key=lambda r: r['accounts'])
            big['accounts'] += diff
            assert big['accounts'] > 0, f'partition overflow: {d["title"]}'
            assert sum(r['accounts'] for r in rows) == parent
        for r in rows:
            r['pct'] = round(r['accounts'] / parent * 100, 4)
        note = d.get('note') or (
            'Rows sum to the parent count.' if kind == 'partition'
            else 'Overlap allowed. Rows do not sum.')
        detours.append({'title': d['title'], 'note': note, 'rows': rows})

    conversion_pct = round(paid / US_GEN_POP * 100, 4)
    proj_name = f'{subject} on {platform}'.strip()
    blob = {
        'meta': {
            'window': f'{start} to {end}',
            'unit': 'unique US accounts',
            'usGenPop': US_GEN_POP,
        },
        'spine': spine,
        'fork': fork_rows,
        'detours': detours,
    }
    now = _dt.datetime.utcnow().isoformat() + 'Z'
    payload = {
        'meta': {
            'story_mode': 'fragrance_shop_journey',
            'target_name': proj_name,
            'target_display': f'{subject} - {platform} journey',
            'project_name': proj_name,
            'target': proj_name,
            'customer_brand': prim.get('customer_brand') or subject,
            'start_date': start, 'end_date': end,
            'target_type': ('watch_journey'
                            if journey_family(inputs, prim) == 'watch'
                            else 'purchase_journey'),
            'journey_kind': (
                'ticketing' if is_ticketing_journey(inputs, prim)
                else str(inputs.get('journey_kind') or 'purchase')),
            'clip_url': str(inputs.get('clip_url') or '') or None,
            'category': str(prim.get('category') or 'brands'),
            'created_by': created_by, 'created_at': now,
        },
        'fragrance_shop_journey': blob,
        'facts': list(prim.get('facts') or [])[:5],
        'kpis': {
            'total_users': paid,
            'conversion_pct': conversion_pct,
        },
        'diagnostics': {
            'data_provenance': 'synthetic_estimate',
            'anchors_note': str(prim.get('anchors_note') or ''),
        },
    }
    ticketing = is_ticketing_journey(inputs, prim)
    if ticketing:
        payload = apply_ticketing_language(payload)
    family = journey_family(inputs, prim)
    payload['fragrance_shop_journey']['copy'] = build_copy(
        subject, platform, payload['fragrance_shop_journey'], family=family)
    if prim.get('cover_title'):
        payload['fragrance_shop_journey']['copy']['titleHtml'] = (
            str(prim['cover_title']).strip().rstrip('.') + '.')
    if prim.get('before_after'):
        payload['before_after'] = prim['before_after']
        payload['fragrance_shop_journey']['before_after'] = prim['before_after']
    if prim.get('who_they_are'):
        payload['who_they_are'] = prim['who_they_are']
        payload['fragrance_shop_journey']['who_they_are'] = prim['who_they_are']
    if prim.get('the_read'):
        payload['the_read'] = prim['the_read']
        payload['fragrance_shop_journey']['the_read'] = prim['the_read']
    from migration.journey_clickstream import attach_clickstream
    attach_clickstream(payload, prim, inputs)
    return payload



# ---------------------------------------------------------------------------
# Corpus anchors (2026-10-05, Jenna). A journey never starts from zero on a
# subject Crosswalk already holds a read on. The Influencer Project journey
# was researched cold while an Attribution IQ campaign on the same title
# (64 tracked assets, a nest of US counts, the ticketing partition, per
# asset converters) sat in the corpus; the two products disagreed by 20x
# and the journey invented "Creator 01" where the real creators were known.
# Before research, the campaign is handed to the prompt as the anchor set.
# After the build, the journey is held at or below the campaign's counts
# for the stages they share and the campaign's partitions replace the
# researched ones. Fail-safe: no S3, no match, or JOURNEY_CORPUS_ANCHORS=0
# means the build proceeds exactly as before.
# ---------------------------------------------------------------------------
_ANCHOR_LABEL_TOKENS = frozenset((
    'the', 'a', 'an', 'of', 'on', 'for', 'and', 'in', 'to', 'at', 'film',
    'films', 'movie', 'movies', 'opening', 'weekend', 'week', 'any',
    'digital', 'ticketing', 'ticket', 'tickets', 'platform', 'platforms',
    'site', 'sites', 'app', 'apps', 'journey', 'viewers', 'fans', 'buyers',
    'audience', 'series', 'season', 'release', 'theatrical', 'campaign',
))


def _anchor_tokens(text: str) -> list:
    toks = re.findall(r'[a-z0-9]+', str(text or '').lower())
    return [t for t in toks if t not in _ANCHOR_LABEL_TOKENS and len(t) > 1]


def _anchors_enabled() -> bool:
    import os
    return os.environ.get('JOURNEY_CORPUS_ANCHORS', '1') != '0'


def _default_s3():
    try:
        import boto3
        return boto3.client('s3', region_name='us-east-2')
    except Exception:
        return None


def find_attribution_campaign(subject: str, s3=None,
                              bucket: str = 'dashboard-inputs') -> Optional[dict]:
    """The Attribution IQ campaign on this subject, if one exists:
    the latest fitted nest plus the asset list. None when nothing in
    intent/ covers the subject's distinctive tokens."""
    want = _anchor_tokens(subject)
    if not want:
        return None
    s3 = s3 or _default_s3()
    if s3 is None:
        return None
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix='intent/', Delimiter='/')
    except Exception:
        return None
    best = None
    for cp in resp.get('CommonPrefixes') or []:
        slug = cp['Prefix'][len('intent/'):].strip('/')
        have = set(_anchor_tokens(slug.replace('_', ' ')))
        if not have:
            continue
        cover = sum(1 for t in want if t in have) / float(len(want))
        reverse = sum(1 for t in have if t in want) / float(len(have))
        # Both directions: the subject's words sit in the slug AND the
        # slug is mostly the subject, so "Influencer" alone never
        # anchors to the film and "Chime" never anchors to Chime MyPay.
        if cover >= 0.8 and reverse >= 0.5 and (best is None or cover + reverse > best[0]):
            best = (cover + reverse, slug)
    if not best:
        return None
    slug = best[1]
    try:
        assets = json.loads(s3.get_object(
            Bucket=bucket, Key=f'intent/{slug}/source/normalized_assets.json'
        )['Body'].read())
    except Exception:
        assets = {}
    try:
        keys = sorted(o['Key'] for o in (s3.list_objects_v2(
            Bucket=bucket, Prefix=f'intent/{slug}/mta/coefficients_'
        ).get('Contents') or []))
        fit = json.loads(s3.get_object(Bucket=bucket, Key=keys[-1])['Body'].read()) if keys else {}
    except Exception:
        fit = {}
    return attribution_anchor_from(slug, assets, fit)


def _campaign_asset_urls(assets: dict) -> list[dict]:
    """The public URLs Attribution IQ already tracks on this title."""
    out = []
    seen = set()
    for a in (assets or {}).get('assets') or []:
        url = str(a.get('url') or '').strip()
        key = url.rstrip('/').lower()
        if not url.startswith('https://') or key in seen:
            continue
        if 'argentina-vs-argelia' in key:
            continue
        seen.add(key)
        out.append({
            'url': url,
            'channel': a.get('channel') or '',
            'asset_type': a.get('asset_type') or '',
            'title': a.get('action_label') or a.get('asset_title') or '',
            'views': int(a.get('ext_view_count') or 0),
        })
    return out


def attribution_anchor_from(slug: str, assets: dict, fit: dict) -> Optional[dict]:
    """Shape the campaign documents into the anchor the journey uses.
    Pure; the tests feed it fixtures."""
    overall = (fit or {}).get('overall') or {}
    paths = overall.get('paths') or {}
    nest = paths.get('nest') or []
    if not nest:
        return None
    title = (assets or {}).get('title') or {}
    phases = (assets or {}).get('phases') or []
    starts = [p.get('start_date') for p in phases if p.get('start_date')]
    sample = int(paths.get('panel_sample') or overall.get('sample_size') or 0)
    rate = float(overall.get('conversion_rate') or 0)
    converters_n = int(round(sample * rate)) if sample and rate else 0
    tps = []
    for t in overall.get('touchpoints') or []:
        if t.get('asset_title') and t.get('converted_n'):
            tps.append({'asset_title': t['asset_title'],
                        'channel': t.get('channel') or '',
                        'converted_n': int(t['converted_n']),
                        'exposed_n': int(t.get('exposed_n') or 0)})
    tps.sort(key=lambda t: -t['converted_n'])
    return {
        'slug': slug,
        'display_name': fit.get('display_name') or title.get('display_name') or slug,
        'as_of': fit.get('as_of') or '',
        'campaign_start': min(starts) if starts else '',
        'opening_date': title.get('opening_date') or '',
        'title_type': fit.get('title_type') or '',
        'conversion_noun': fit.get('conversion_noun') or '',
        'nest': [{'stage': n.get('stage'), 'label': n.get('label'),
                  'us_accounts': int(n.get('us_accounts') or 0)} for n in nest],
        'ticketer_partition': (paths.get('where') or {}).get('ticketer_partition') or [],
        'first_touch': (paths.get('attribution') or {}).get('first_touch') or [],
        'last_touch': (paths.get('attribution') or {}).get('last_touch') or [],
        'assists': (paths.get('attribution') or {}).get('assists') or [],
        'converters_n': converters_n,
        'touchpoints': tps[:12],
        'asset_count': len((assets or {}).get('assets') or []),
        'channel_mix': channel_mix_from(assets, overall.get('touchpoints') or []),
        'assets': _campaign_asset_urls(assets),
    }


def channel_mix_from(assets: dict, touchpoints: list) -> list:
    """Where the campaign's exposed audience met the title, by tracked
    channel: one row per channel the campaign actually tracks, with the
    tracked asset count, the asset types behind it, and its share of
    tracked exposure. Exposure comes from every fitted touchpoint
    (converters or not); when the fit carries no exposure counts the
    share falls back to the tracked asset count. Context for the research
    call and the anchor trail: the discovery mix covers the whole exposed
    audience, so surfaces outside this list (a creator podcast, a repost)
    still belong when the audience met the title there, each with a
    breakdown naming what sits behind it (2026-10-06, Alexia's 27,559
    creator-podcast row on The Influencer Project)."""
    by = {}

    def _slot(ch):
        ch = str(ch or '').strip() or 'Other'
        return by.setdefault(ch, {'channel': ch, 'assets': 0, 'exposed_n': 0,
                                  'converted_n': 0, 'types': {}})
    for t in touchpoints or []:
        d = _slot(t.get('channel'))
        d['assets'] += 1
        d['exposed_n'] += int(t.get('exposed_n') or 0)
        d['converted_n'] += int(t.get('converted_n') or 0)
    for a in ((assets or {}).get('assets') or []):
        ch = str(a.get('channel') or '').strip()
        if not ch:
            continue
        d = _slot(ch)
        kind = str(a.get('asset_type') or '').strip()
        if kind:
            d['types'][kind] = d['types'].get(kind, 0) + 1
        if not touchpoints:
            d['assets'] += 1
    by = {k: v for k, v in by.items() if v['assets'] > 0}
    if not by:
        return []
    tot = float(sum(d['exposed_n'] for d in by.values()))
    if tot <= 0:
        tot = float(sum(d['assets'] for d in by.values())) or 1.0
        for d in by.values():
            d['pct'] = round(d['assets'] / tot * 100, 4)
    else:
        for d in by.values():
            d['pct'] = round(d['exposed_n'] / tot * 100, 4)
    return sorted(by.values(), key=lambda d: (-d['pct'], d['channel']))


def corpus_anchors(inputs: dict, s3=None) -> dict:
    """Everything Crosswalk already holds on this subject that a journey
    must stay coherent with: the Attribution IQ campaign (deterministic
    hold), the corpus catalog's published figures on the subject
    (Profile IQ sizes, Brand Partnership reads, earlier chat answers;
    binding in the research prompt), and a prior Digital Journey on the
    same subject (replayed outright when subject, platform, kind and
    window all match; otherwise binding in the prompt)."""
    out = {'attribution': None, 'catalog': None, 'prior_journey': None,
           'catalog_block': ''}
    if not _anchors_enabled():
        return out
    try:
        out['attribution'] = find_attribution_campaign(
            str(inputs.get('subject') or ''), s3=s3)
    except Exception:
        out['attribution'] = None
    try:
        from migration import corpus_catalog as _cc
        start, end = _dates(inputs)
        win = {'start': start, 'end': end}
        cat = _cc.anchors_for(str(inputs.get('subject') or ''), window=win)
        out['catalog'] = cat
        out['catalog_block'] = _cc.anchors_block(cat, max_lines=30)
        out['prior_journey'] = _cc.prior_journey(cat, win)
    except Exception as exc:
        print(f'[journey] catalog anchors skipped: {exc}')
    return out


def _fold(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower().replace('&', ' and ')).strip()


def find_replayable_journey(inputs: dict, anchors: dict, s3=None) -> Optional[dict]:
    """The stored payload of a prior journey that IS this ask: same
    subject, same platform, same journey kind, same window. Replayed
    instead of rebuilt, so two users asking the same thing see the
    same numbers (the ledger rule, applied to journeys)."""
    prior = (anchors or {}).get('prior_journey')
    if not prior or not prior.get('source_key'):
        return None
    try:
        start, end = _dates(inputs)
        pw = prior.get('window') or {}
        if str(pw.get('start') or '')[:10] != str(start)[:10] \
                or str(pw.get('end') or '')[:10] != str(end)[:10]:
            return None
        s3 = s3 or _default_s3()
        import gzip as _gz
        body = s3.get_object(Bucket='dashboard-inputs', Key=prior['source_key'])['Body'].read()
        payload = json.loads(_gz.decompress(body).decode('utf-8'))
        meta = payload.get('meta') or {}
        kind_new = 'ticketing' if is_ticketing_journey(inputs) else str(inputs.get('journey_kind') or 'purchase')
        kind_old = 'ticketing' if meta.get('no_purchase_claim') else str(meta.get('journey_kind') or 'purchase')
        if kind_new != kind_old:
            return None
        plat_new = _fold(inputs.get('platform'))
        tname = _fold(meta.get('target_name') or meta.get('project_name'))
        if plat_new and plat_new not in tname:
            return None
        return payload
    except Exception as exc:
        print(f'[journey] replay lookup skipped: {exc}')
        return None


def anchors_prompt_block(anchors: dict) -> Optional[dict]:
    camp = (anchors or {}).get('attribution')
    extra = {}
    if (anchors or {}).get('catalog_block'):
        extra['published_figures'] = anchors['catalog_block']
    prior = (anchors or {}).get('prior_journey')
    if prior and prior.get('stages'):
        extra['prior_journey'] = {
            'note': ("Crosswalk already published a Digital Journey on this "
                     "subject. A new journey on an overlapping window must "
                     "agree with it where the stages match and sit inside it "
                     "for a sub-window."),
            'window': prior.get('window'), 'stages': prior.get('stages'),
            'end_point_total': prior.get('total')}
    if not camp:
        return extra or None
    return {
        **extra,
        'note': ("Crosswalk already holds an Attribution IQ read on this "
                 "title. These are US counts over the whole campaign to "
                 "date. A journey for a sub-window must land AT OR BELOW "
                 "each matching stage, and must reuse this partition and "
                 "these creators and publishers instead of inventing "
                 "placeholders. Never contradict these numbers."),
        'campaign': camp['display_name'], 'as_of': camp['as_of'],
        'campaign_start': camp['campaign_start'],
        'opening_date': camp['opening_date'],
        'nest_us_counts': camp['nest'],
        'ticketing_partition': camp['ticketer_partition'],
        'first_touch': camp['first_touch'], 'last_touch': camp['last_touch'],
        'assists': camp['assists'],
        'top_assets_by_converters': camp['touchpoints'][:8],
        'tracked_asset_count': camp['asset_count'],
        'tracked_asset_urls': (camp.get('assets') or [])[:12],
        'use_these_urls': (
            "The exposure / saw-tracked-campaign-content / creator / "
            "editorial clickstream steps MUST use a few of these URLs "
            "(the top video, a creator post, an editorial page, then a "
            "couple more). They are the creator posts, YouTube videos, "
            "and editorial articles Attribution IQ already tracks. Do "
            "not invent search pages for a stage we already track. Do "
            "not list every tracked asset."),
        'tracked_channels': [
            {'channel': d['channel'], 'tracked_assets': d['assets'],
             'share_of_exposure_pct': d['pct']}
            for d in (camp.get('channel_mix') or [])],
        'tracked_channels_note': (
            "Where the campaign's tracked assets sit, with each channel's "
            "share of tracked exposure. The discovery mix covers the whole "
            "exposed audience, so it may also carry surfaces outside this "
            "list where the audience met the title. Every aggregate row "
            "(podcasts, press, reposts, clips) carries a breakdown naming "
            "the real shows, episodes or outlets behind it."),
    }


def _nest_count(camp: dict, prefix: str) -> int:
    for n in camp.get('nest') or []:
        if str(n.get('stage') or '').startswith(prefix):
            return int(n.get('us_accounts') or 0)
    return 0


def _parse_day(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def window_shares(payload: dict, camp: dict, seed: str) -> dict:
    """What share of the campaign-to-date counts a journey window can
    hold. Opening weekend of a film carries the bulk of ticketing
    (salted band, never a constant); a window that spans the whole
    campaign sits just under it; anything else is proportional to the
    days it covers."""
    meta = payload.get('meta') or {}
    ws, we = _parse_day(meta.get('start_date')), _parse_day(meta.get('end_date'))
    cs = _parse_day(camp.get('campaign_start'))
    ce = _parse_day(camp.get('as_of')) or _dt.date.today()
    op = _parse_day(camp.get('opening_date'))
    h = (_h(seed, 'window_share') % 1000) / 1000.0
    if ws and we and op and ws <= op <= we and (we - ws).days <= 5:
        return {'exposed': round(0.35 + 0.13 * h, 4),
                'infoseek': round(0.45 + 0.13 * h, 4),
                'bottom': round(0.55 + 0.15 * h, 4), 'basis': 'opening_weekend'}
    if ws and we and cs and ws <= cs and we >= ce:
        f = round(0.93 + 0.06 * h, 4)
        return {'exposed': f, 'infoseek': f, 'bottom': f, 'basis': 'whole_campaign'}
    if ws and we and cs:
        span = max((ce - cs).days, 1)
        f = max(0.08, min(1.0, ((we - ws).days + 1) / float(span)))
        f = round(min(1.0, f) * (0.93 + 0.06 * h), 4)
        return {'exposed': f, 'infoseek': f, 'bottom': f, 'basis': 'proportional'}
    f = round(0.93 + 0.06 * h, 4)
    return {'exposed': f, 'infoseek': f, 'bottom': f, 'basis': 'ceiling'}


_INFOSEEK_RE = re.compile(r'search|look|info|research|review|trailer', re.I)


def _clean_asset_label(title: str) -> str:
    parts = [p.strip() for p in str(title or '').split('\u00b7')]
    if len(parts) >= 3:
        chan, who, what = parts[0], parts[1], ' '.join(parts[2:])
        return f"{who} on {chan}: {what}"
    return str(title or '').strip()


def refit_fork(j: dict, paid: int, penult: int, old_paid: int, seed: str) -> None:
    """Re-solve the leave / win-back fork on a new terminal and
    penultimate count, keeping the shape the research gave (the four
    branch ratios) and every identity: paid_first + paid_return = paid,
    abandoned + paid_first = penult, retargeted < abandoned, returned <
    retargeted, paid_return <= returned."""
    fork = j.get('fork') or []
    if not fork:
        return
    fk = {r['id']: r for r in fork if isinstance(r, dict) and r.get('id')}
    if 'paid_return' not in fk or 'paid_first' not in fk or 'abandoned' not in fk:
        return
    o_paid = max(float(old_paid or paid), 1.0)
    o_pr = float(fk['paid_return']['accounts'])
    o_rn = max(float(fk.get('returned', {}).get('accounts', 0)), 1.0)
    o_rt = max(float(fk.get('retargeted', {}).get('accounts', 0)), 1.0)
    o_ab = max(float(fk['abandoned']['accounts']), 1.0)
    share_back = o_pr / o_paid
    b_ = max(min(o_pr / o_rn, 0.98), 0.05)   # paid_return of returned
    c_ = max(min(o_rn / o_rt, 0.98), 0.05)   # returned of retargeted
    d_ = max(min(o_rt / o_ab, 0.98), 0.05)   # retargeted of leavers
    denom = paid * share_back * (1.0 / (b_ * c_) - d_)
    t = ((penult - paid) * d_ / denom) if denom > 0 else 1.0
    if t < 1.0:
        t *= 0.97 + 0.025 * ((_h(seed, 'fork_t') % 100) / 100.0)
    pr = _messy((seed, 'anchor_pr'), paid * share_back * min(t, 1.0))
    pr = max(1, min(pr, paid - 1))
    pf = paid - pr
    ab = penult - pf
    rn = max(_messy((seed, 'anchor_rn'), pr / b_), pr + 1)
    rt = max(_messy((seed, 'anchor_rt'), rn / c_), rn + 1)
    if rt >= ab:
        rt = ab - 1 - (_h(seed, 'rt_trim') % 5)
        rn = min(rn, rt - 1)
        pr = min(pr, rn)
        pf = paid - pr
        ab = penult - pf
    vals = {'abandoned': ab, 'retargeted': rt, 'returned': rn,
            'paid_return': pr, 'paid_first': pf}
    bases = {'abandoned': penult, 'retargeted': ab, 'returned': rt,
             'paid_return': rn, 'paid_first': penult}
    assert pf + pr == paid and ab + pf == penult
    assert rt < ab and rn < rt and pr <= rn and pf > 0 and pr > 0
    for r in fork:
        if r.get('id') in vals:
            r['accounts'] = int(vals[r['id']])
            b = max(bases[r['id']], 1)
            r['kept'] = round(r['accounts'] / b * 100, 4)
            if 'dropped' in r:
                r['dropped'] = max(b - r['accounts'], 0)


def distinct_kept_rates(payload: dict, seed: str = '', min_gap_pp: float = 0.6,
                        ceilings: Optional[dict] = None) -> list:
    """No two consecutive stages keep the same share of the one before
    (the house rule: no two identical rates). A geometric fill between
    two measured points produces exactly that (62.0% then 62.0%; Alexia,
    2026-10-06), so the stage between two equal steps is moved off the
    midpoint by a salted tilt, strictly inside its neighbours, and the
    fork re-solves on the new penultimate count. Returns the ids moved.
    Stage counts the campaign anchored (first, info-seek, terminal) are
    never the ones moved: only a middle stage sitting between two equal
    rates. `ceilings` ({stage_id: max_count}) keeps a moved stage under
    a count it must not exceed (a campaign stage, or its own published
    value); when the salted tilt would cross it, the tilt flips down."""
    ceilings = {k: int(v) for k, v in (ceilings or {}).items() if v}
    j = (payload or {}).get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    if len(spine) < 4:
        return []
    seed = seed or str((payload.get('meta') or {}).get('target_name') or '')
    ids = [s['id'] for s in spine]
    acc = {s['id']: int(s['accounts']) for s in spine}
    moved = []
    for _ in range(3):
        changed = False
        kept = {}
        for k in range(1, len(ids)):
            prev, cur = acc[ids[k - 1]], acc[ids[k]]
            kept[ids[k]] = cur / float(prev) * 100 if prev else 0.0
        for k in range(1, len(ids) - 1):
            a, b = ids[k], ids[k + 1]
            if abs(kept[a] - kept[b]) < min_gap_pp:
                lo, hi = acc[ids[k + 1]], acc[ids[k - 1]]
                mid = (lo * hi) ** 0.5
                tilt = (0.04 + 0.05 * ((_h(seed, 'tilt', a) % 100) / 100.0))
                if _h(seed, 'tilt_sign', a) % 2:
                    tilt = -tilt
                cap = ceilings.get(a)
                if cap and mid * (1 + abs(tilt)) >= cap:
                    tilt = -abs(tilt)
                new = _messy((seed, 'distinct', a), mid * (1 + tilt))
                new = max(lo + 1, min(hi - 1, new))
                if cap:
                    new = min(new, cap - 1)
                    new = max(lo + 1, new)
                if new != acc[a]:
                    acc[a] = new
                    moved.append(a)
                    changed = True
        if not changed:
            break
    if not moved:
        return []
    old_paid = int(spine[-1]['accounts'])
    prev = int(spine[0]['accounts'])
    for s in spine[1:]:
        s['accounts'] = acc[s['id']]
        s['kept'] = round(s['accounts'] / prev * 100, 4)
        s['dropped'] = prev - s['accounts']
        s['ofUs'] = round(s['accounts'] / US_GEN_POP * 100, 4)
        prev = s['accounts']
    if ids[-2] in moved:
        try:
            refit_fork(j, int(spine[-1]['accounts']), int(spine[-2]['accounts']), old_paid, seed)
        except Exception:
            traceback.print_exc()
    return sorted(set(moved), key=ids.index)


def apply_attribution_anchors(payload: dict, camp: dict, inputs: dict,
                              seed: str = '') -> dict:
    """Hold the journey at or below the Attribution IQ campaign on the
    stages they share, then replace the researched partitions with the
    campaign's own. Deterministic, in place, recomputes every
    downstream cell (kept, dropped, fork identity, detours, facts,
    kpis, copy)."""
    j = payload.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    if len(spine) < 3 or not camp:
        return payload
    seed = seed or str((payload.get('meta') or {}).get('target_name') or '')
    shares = window_shares(payload, camp, seed)
    ticketing = bool((payload.get('meta') or {}).get('no_purchase_claim'))
    old = {s['id']: int(s['accounts']) for s in spine}
    ids = [s['id'] for s in spine[1:]]

    targets = {}
    c_exp = _nest_count(camp, '1_')
    if c_exp:
        targets[ids[0]] = c_exp * shares['exposed']
    c_mid = _nest_count(camp, '2_')
    mid_id = next((i for i in ids[1:-1] if _INFOSEEK_RE.search(
        i + ' ' + next(s['label'] for s in spine if s['id'] == i))), None)
    if c_mid and mid_id:
        targets[mid_id] = c_mid * shares['infoseek']
    # Film ladder (no-box-office-prediction.mdc, labels settled
    # 2026-10-06): campaign stage 3 is the showtimes page, stage 4 is
    # the ticketing-site visit for a ticket. The journey's terminal
    # "went to a ticketing site or app for a ticket" holds to stage 4;
    # a showtimes stage in the journey holds to stage 3.
    if ticketing:
        c_show = _nest_count(camp, '3_')
        show_id = next((i for i in ids[1:-1] if i != mid_id and re.search(
            r'showtime', i + ' ' + next(s['label'] for s in spine if s['id'] == i), re.I)), None)
        if c_show and show_id:
            targets[show_id] = c_show * shares['bottom']
        c_bot = _nest_count(camp, '4_') or c_show
    else:
        c_bot = _nest_count(camp, str(camp['nest'][-1]['stage'])[:2])
    if c_bot:
        targets[ids[-1]] = c_bot * shares['bottom']
    if not targets:
        return payload

    # Only ever pull down. A stage already under its anchor keeps its
    # own ratio to the nearest anchored neighbour.
    new = dict(old)
    for i in ids:
        if i in targets and targets[i] < old[i]:
            new[i] = _messy((seed, 'anchor', i), targets[i])
    anchored = [i for i in ids if new[i] != old[i]]
    # A journey already under its anchors keeps its stage counts, but
    # the campaign's own partitions (ticketing sites, creators, first
    # and last touch, assists, the tracked channel mix) still replace
    # the reasoned ones below (2026-10-06: before this the early return
    # left "Creator 01" and untracked surfaces in place whenever no
    # stage needed pulling down).
    # Unanchored stages: geometric interpolation between the nearest
    # anchored (or TAM / terminal) neighbours so the chain stays smooth.
    pos = {i: k for k, i in enumerate(ids)}
    fixed = set(anchored)
    for i in ids:
        if i in fixed or not anchored:
            continue
        left = next((x for x in reversed(ids[:pos[i]]) if x in fixed), None)
        right = next((x for x in ids[pos[i] + 1:] if x in fixed), None)
        if left and right:
            lo, hi = new[left], new[right]
            span = pos[right] - pos[left]
            k = pos[i] - pos[left]
            val = lo * (hi / float(lo)) ** (k / float(span))
            new[i] = _messy((seed, 'interp', i), val)
        elif left:
            new[i] = _messy((seed, 'interp', i),
                            new[left] * old[i] / float(old[left]))
        elif right:
            new[i] = _messy((seed, 'interp', i),
                            new[right] * old[i] / float(old[right]))
    # Monotone, strictly decreasing.
    if anchored:
        prev = int(spine[0]['accounts'])
        for i in ids:
            if new[i] >= prev:
                new[i] = _messy((seed, 'mono', i), prev * 0.9)
            prev = new[i]
        prev = int(spine[0]['accounts'])
        for s in spine[1:]:
            s['accounts'] = new[s['id']]
            s['kept'] = round(s['accounts'] / prev * 100, 4)
            s['dropped'] = prev - s['accounts']
            s['ofUs'] = round(s['accounts'] / US_GEN_POP * 100, 4)
            prev = s['accounts']
    if anchored:
        # A geometric fill leaves equal consecutive rates; move the
        # middle stage off the midpoint (no two identical rates).
        distinct_kept_rates(payload, seed, ceilings={k: int(v) for k, v in targets.items()})
        for s in spine[1:]:
            new[s['id']] = int(s['accounts'])
    paid, penult = spine[-1]['accounts'], spine[-2]['accounts']
    f_paid = paid / float(old[ids[-1]])

    # Fork: scale, then restore the identities.
    if anchored:
        refit_fork(j, paid, penult, old[ids[-1]], seed)

    # Detours: rebase every row on the new count of its base; swap the
    # campaign's own partitions in where they exist.
    def _rows(items, base, kind_overlap=False):
        out = []
        for it in items:
            pct = float(it.get('pct') or 0)
            out.append({'label': it.get('surface') or it.get('touchpoint') or it.get('label'),
                        'pct': round(pct + ((_h(seed, it.get('surface') or it.get('touchpoint') or '') % 9) - 4) / 100.0, 4),
                        'accounts': _messy((seed, 'det', str(it)), base * pct / 100.0),
                        'doing': it.get('doing') or ''})
        out.sort(key=lambda r: -float(r['pct']))
        return out
    for d in j.get('detours') or []:
        rows = d.get('rows') or []
        title = str(d.get('title') or '')
        if ticketing and camp.get('ticketer_partition') and re.search(
                r'where the ticketing site was reached|ticketing (site|platform)s? (reached|used)', title, re.I):
            d['rows'] = _rows(camp['ticketer_partition'], paid)
            d['note'] = 'Share of ticketing-site visitors by the site or app they reached.'
            continue
        if camp.get('touchpoints') and camp.get('converters_n') and re.search(
                r'creator|publisher|asset', title, re.I):
            tot = float(camp['converters_n'])
            items = [{'label': _clean_asset_label(t['asset_title']),
                      'pct': round(t['converted_n'] / tot * 100, 1)}
                     for t in camp['touchpoints'][:6]]
            d['rows'] = _rows(items, paid)
            d['kind'] = 'overlap'
            d['note'] = ('Tracked creators and publishers, by the share of '
                         'ticketing-site visitors who touched each one. '
                         'Overlapping, so the shares do not sum to 100.')
            continue
        if camp.get('first_touch') and re.search(r'first touch', title, re.I):
            d['rows'] = _rows(camp['first_touch'], paid); continue
        if camp.get('last_touch') and re.search(r'last touch', title, re.I):
            d['rows'] = _rows(camp['last_touch'], paid); continue
        if camp.get('assists') and re.search(r'assist', title, re.I):
            d['rows'] = _rows(camp['assists'], paid); d['kind'] = 'overlap'; continue
        if not rows or not anchored:
            continue
        r0 = rows[0]
        try:
            base_old = float(r0['accounts']) / (float(r0['pct']) / 100.0)
        except Exception:
            base_old = 0
        base_new = None
        for sid, ov in old.items():
            if ov and abs(base_old - ov) / float(ov) < 0.03:
                base_new = new.get(sid, ov)
                break
        if base_new is None:
            base_new = base_old * f_paid
        for r in rows:
            r['accounts'] = _messy((seed, 'det', title, r.get('label')),
                                   base_new * float(r.get('pct') or 0) / 100.0)
            rescale_breakdown(r, (seed, 'det', title, r.get('label')))

    # Facts, kpis, copy.
    payload['kpis'] = {'total_users': paid,
                       'conversion_pct': round(paid / float(spine[1]['accounts']) * 100, 4)}
    if ticketing:
        cold_share = 0.33 + (_h(seed, 'cold') % 90) / 1000.0
        total = _messy((seed, 'total_visitors'), paid / (1 - cold_share))
        cold = total - paid
        part = camp.get('ticketer_partition') or []
        lead = ' and '.join(p['surface'] for p in part[:2])
        old_facts = payload.get('facts') or []
        path_fact = next((f for f in old_facts
                          if re.search(r'path|route', str(f.get('label') or ''), re.I)
                          and not re.search(r'exposed|cold', str(f.get('label') or ''), re.I)), None)
        facts = [
            {'label': 'Opening-weekend ticketing-site visitors',
             'value': (f"{total:,} US individuals went to a ticketing site or app for a ticket to the film in the window"
                       + (f"; {lead} carried the largest shares." if lead else '.'))},
            {'label': 'Exposed ticketing-site visitors vs cold ticketing-site visitors',
             'value': f"{paid:,} ({paid / total * 100:.1f}%) had touched tracked creator or editorial content first; {cold:,} arrived with no tracked touch."},
            {'label': 'Exposed universe',
             'value': f"{spine[1]['accounts']:,} US individuals saw tracked campaign content in the window, {payload['kpis']['conversion_pct']:.1f}% of whom reached a ticketing site."},
        ]
        if path_fact:
            val = str(path_fact.get('value') or '')
            m_ = re.search(r'(\d{1,3}(?:,\d{3})+)[^.%]{0,80}?(\d+(?:\.\d+)?)%', val)
            if m_:
                pct = float(m_.group(2))
                n_ = _messy((seed, 'path'), paid * pct / 100.0)
                val = val.replace(m_.group(1), f"{n_:,}")
            facts.append({'label': path_fact.get('label'), 'value': val})
        payload['facts'] = facts
    elif anchored:
        for f in payload.get('facts') or []:
            def _sc(m_):
                n_ = int(m_.group(0).replace(',', ''))
                return f"{_messy((seed, 'fact', n_), n_ * f_paid):,}"
            f['value'] = re.sub(r'\d{1,3}(?:,\d{3})+', _sc, str(f.get('value') or ''))
    family = journey_family(inputs)
    try:
        j['copy'] = build_copy(str(inputs.get('subject') or ''), str(inputs.get('platform') or ''),
                               j, family=family)
    except Exception:
        pass
    payload.setdefault('meta', {})['anchored_to'] = {
        'attribution_iq': camp['slug'], 'as_of': camp['as_of'],
        'basis': shares['basis'], 'changed': bool(anchored),
        'tracked_channels': [d_['channel'] for d_ in (camp.get('channel_mix') or [])],
        'shares': {k: v for k, v in shares.items() if k != 'basis'}}
    return payload


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = 'prometheus') -> dict:
    start, end = _dates(inputs)
    anchors = corpus_anchors(inputs)
    # Same ask, same answer (2026-10-05): a stored journey that IS this
    # ask replays with fresh run metadata instead of a cold rebuild.
    replay = find_replayable_journey(inputs, anchors)
    if replay:
        meta = replay.setdefault('meta', {})
        meta['created_by'] = created_by
        meta['created_at'] = _dt.datetime.utcnow().isoformat() + 'Z'
        meta['replayed_from'] = (anchors.get('prior_journey') or {}).get('source_key')
        print(f"[journey] replayed prior journey for {inputs.get('subject')!r}")
        try:
            from migration.journey_clickstream import attach_clickstream
            attach_clickstream(replay, {}, inputs)
        except Exception as exc:
            print(f'[journey] clickstream attach on replay skipped: {exc}')
        return replay
    user_prompt = json.dumps({
        'corpus_anchors': anchors_prompt_block(anchors),
        'subject': inputs['subject'],
        'platform': inputs['platform'],
        'conversion_event': inputs.get('conversion_event') or '',
        'journey_kind': ('ticketing' if is_ticketing_journey(inputs)
                         else (inputs.get('journey_kind') or 'purchase')),
        'clip_url': inputs.get('clip_url') or None,
        'start_behavior': inputs.get('start_behavior') or None,
        'window': {'start': start, 'end': end},
        'tam_label': inputs.get('tam_label') or 'US gen pop',
        'tam_accounts': int(inputs.get('tam_accounts') or US_GEN_POP),
        'notes': inputs.get('notes') or '',
    })
    prim = claude_json(RESEARCH_SYSTEM_PROMPT, user_prompt,
                       max_tokens=12000, temperature=0.6,
                       surface='journey_synthesis', tools=tools)
    if not isinstance(prim, dict) or not prim.get('nest'):
        raise RuntimeError('journey research returned no primitives')
    if anchors.get('attribution'):
        prim = dict(prim)
        prim['tracked_assets'] = anchors['attribution'].get('assets') or []
    payload = build_journey(inputs, prim, created_by=created_by)
    if anchors.get('attribution'):
        try:
            payload = apply_attribution_anchors(
                payload, anchors['attribution'], inputs,
                seed=f"{inputs['subject']}|{inputs['platform']}")
        except Exception as exc:
            print(f'[journey] corpus anchor pass skipped: {exc}')
    try:
        from migration.journey_clickstream import attach_clickstream
        attach_clickstream(payload, prim, inputs)
    except Exception as exc:
        print(f'[journey] clickstream attach skipped: {exc}')
    return scrub_payload_text(payload)


def scrub_payload_text(payload: dict) -> dict:
    """Every string a reader sees in the journey (copy, notes, labels,
    facts) passes the house vocabulary + method-language scrub
    (2026-10-06). Identifiers, URLs and dates are untouched."""
    try:
        from prometheus import guards as _g
        _g.scrub_tree(payload)
    except Exception as exc:
        print(f'[journey] text scrub skipped: {exc}')
    return payload


def persist(s3_client, payload: dict, username: str, job_id: str) -> str:
    """Write the run through the Journey IQ store (gzip payload +
    index append) so the tab lists and loads it like any other run."""
    try:
        from migration import journey_iq as _jiq
    except ImportError:
        import journey_iq as _jiq  # type: ignore
    return _jiq._persist(s3_client, payload,
                         payload['meta']['project_name'],
                         username, job_id)
