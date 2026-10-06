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
  "journey_kind": "purchase"|"watch",
                                // infer from the end step: money
                                // changes hands -> "purchase"; the end
                                // step is watching / streaming /
                                // playing / listening -> "watch"
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
event nor a concrete watch/play behavior, list conversion_event in
missing. A start_behavior is never required; only capture one the user
actually described. Never invent what the user did not give."""


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
     "rows": [{"label": str, "pct": float, "doing": str}, ...]},
    ...
  ],
  "anchors_note": str             // internal: the public anchors used
}

Rules that do not move:
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
- Do not invent a sample / observed-file n. Never emit a sample field. The path counts are the file.

Two journey families. journey_kind in the input decides which:
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
the ticketing site for a ticket" (reached a ticketing site, circuit
app, or showtimes-to-checkout flow for THIS title with a showtime in
window). No stage, fact, fork surface, or detour may say bought,
buyers, purchased, purchasers, paid, checkout completed, conversion,
or box office, and nothing may imply a ticket was bought. The fork is
left-the-ticketing-site -> retarget -> came back -> returned to the
ticketing site. Detours divide the ticketing-site visitors (where they
reached it, first touch, last touch, assists)."""


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


# Ordered: longer phrases first so the short ones never pre-empt them.
_TICKETING_SWAPS = [
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
    meta = out.setdefault('meta', {})
    meta['target_type'] = 'ticketing_visit_journey'
    meta['no_purchase_claim'] = True
    meta['unit_note'] = ('Counts are US individuals who went to a '
                         'ticketing site or app for a ticket. ' +
                         TICKETING_NO_CLAIM)
    return out


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
            rows.append({'label': r['label'], 'accounts': acc,
                         'doing': r.get('doing') or ''})
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
                            if (inputs.get('journey_kind') == 'watch')
                            else 'purchase_journey'),
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
    family = ('ticketing' if ticketing
              else 'watch' if inputs.get('journey_kind') == 'watch'
              else 'purchase')
    payload['fragrance_shop_journey']['copy'] = build_copy(
        subject, platform, payload['fragrance_shop_journey'], family=family)
    return payload


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = 'prometheus') -> dict:
    start, end = _dates(inputs)
    user_prompt = json.dumps({
        'subject': inputs['subject'],
        'platform': inputs['platform'],
        'conversion_event': inputs.get('conversion_event') or '',
        'journey_kind': ('ticketing' if is_ticketing_journey(inputs)
                         else (inputs.get('journey_kind') or 'purchase')),
        'start_behavior': inputs.get('start_behavior') or None,
        'window': {'start': start, 'end': end},
        'tam_label': inputs.get('tam_label') or 'US gen pop',
        'tam_accounts': int(inputs.get('tam_accounts') or US_GEN_POP),
        'notes': inputs.get('notes') or '',
    })
    prim = claude_json(RESEARCH_SYSTEM_PROMPT, user_prompt,
                       max_tokens=9000, temperature=0.6,
                       surface='journey_synthesis', tools=tools)
    if not isinstance(prim, dict) or not prim.get('nest'):
        raise RuntimeError('journey research returned no primitives')
    return build_journey(inputs, prim, created_by=created_by)


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
