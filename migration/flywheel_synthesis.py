"""Flywheel synthesis for the Prometheus 'Build a Flywheel' pull.

Method: reports/Gilmore_Girls_Acquired_Reactivated_Amazon_Flywheel_
Build_Playbook_2026_09_17.md (the revised playbook). The defining
rule: THE NEST STARTS AT THE CAPTURED USERS - the people who did the
thing the ask wants to capture - NEVER at US gen pop, never at a
platform total. The country is not a row. A larger parent file is a
ceiling to respect, not step 0.

Shape: three-row spine (captured cohort -> second owned surface ->
conversion), a checkout/leftover fork, pre-window and post-window
surface tables, a two-way split of who is in the file, and partition
tables that sum exactly. The payload renders through the flywheel
card (same visual as the Gilmore Girls files).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from typing import Callable, Optional

PARSE_SYSTEM_PROMPT = """You extract Flywheel inputs from a user's
message. A flywheel follows people who did ONE capturable thing into
the rest of an owned ecosystem, before and after that thing. Return
STRICT JSON only:
{
  "subject": str|null,          // the title / brand the event is
                                // about (e.g. "Gilmore Girls")
  "captured_action": str|null,  // THE STARTING POINT: what the
                                // captured users did, in one sentence
                                // ("acquired or reactivated Prime
                                // after a first Gilmore Girls play
                                // following 180 days off"). The
                                // flywheel NEVER starts at US gen pop
                                // or a platform total - only at these
                                // captured users.
  "cohort_count": int|null,     // if the user hands a count ("use
                                // 22,764"), capture it VERBATIM - it
                                // is a locked anchor, not a guess
  "ecosystem": str|null,        // the owned-surface family the
                                // flywheel lives on ("Amazon-owned",
                                // "TikTok Shop ecosystem")
  "conversion_event": str|null, // the conversion inside that
                                // ecosystem, one sentence
                                // ("Amazon-owned checkout inside 30
                                // days of the play")
  "pre_days": int|null,         // pre window, default 180
  "post_days": int|null,        // post window, default 30
  "splits_hint": str|null,      // a two-way split the user named
                                // ("new vs reactivated"), else null
  "start_date": "YYYY-MM-DD"|null,
  "end_date": "YYYY-MM-DD"|null,
  "notes": str|null,
  "missing": [str, ...]         // which of subject / captured_action
                                // / ecosystem / conversion_event are
                                // still missing
}
Never invent what the user did not give. "Fans of X" is not a
captured action - the action must be a concrete observable event
(played, signed up, reactivated, bought, subscribed)."""


RESEARCH_SYSTEM_PROMPT = """You are building a flywheel file from a
10M-consumer US clickstream panel: the owned-ecosystem digital life of
a captured cohort, before and after the capture event. Research the
title, the ecosystem, and public US footprints (web search when
available), then reason the primitives. Everything must read as
observed first-party data: messy values, no round numbers, no two
identical rates. Return STRICT JSON only:

{
  "cohort": int,                 // the captured-cohort count. If the
                                 // input carries cohort_count, return
                                 // it VERBATIM (it is a locked
                                 // anchor). Otherwise derive it from
                                 // researched anchors and make it
                                 // messy (last digit 1-9).
  "cohort_label": str,           // one line naming the captured users
  "customer_brand": str,
  "category": str,
  "splits": {                    // two-way partition of the cohort
    "enabled": true|false,       // (new vs reactivated, first-time vs
    "a_label": str, "b_label": str,   // returning). MUST sum to the
    "a_pct": float               // cohort; the two sides must
  },                             // disagree in composition direction.
  "second_surface_pct": float,   // % of cohort that opened a second
                                 // owned surface in the post window
  "checkout_pct_of_second": float,  // % of those that converted
  "surfaces": [                  // 8 to 14 owned surfaces. pre_pct
    {"label": str,               // and post_pct are % of cohort with
     "pre_pct": float,           // a session in the pre / post
     "post_pct": float,          // window. The ENTRY surface is 0 in
     "note": str},               // pre by definition. Perk-style
    ...                          // surfaces lift post; research-style
  ],                             // surfaces may fall.
  "checkout_what": [             // overlap of the conversion count
    {"label": str, "pct": float}, ...],
  "copy": {
    "title_html": str,           // short title, may carry <br>
    "lead": str,                 // one paragraph, client-safe
    "deck": str,                 // one line under the title
    "fork_head": str, "fork_read": str, "detour_head": str,
    "kpis": [{"l": str, "v": str, "hot": true|false}, ...]  // 3 tiles
  },
  "facts": [{"label": str, "value": str}, ...],  // exactly 4
  "anchors_note": str            // internal: anchors used
}

Rules that do not move:
- THE SPINE STARTS AT THE CAPTURED USERS. No US gen pop row, no
  platform-total row, no country row. Three rows only: captured
  cohort, second owned surface, conversion.
- The nest DECREASES strictly.
- Splits and partitions sum to their parent (the code forces
  exactness); overlap tables may exceed and each row stays under its
  parent.
- Stay on the named ecosystem's owned surfaces. No off-ecosystem
  watch table, no signup-path table, no leak table.
- Every surface needs a researched US footprint ceiling in mind; on a
  small cohort the ceiling rarely binds but check it.
- Pre window: the entry surface is zero by definition. Post window is
  short (default 30 days): incidence can sit below a 180-day pre rate
  even when the daily rate is hotter - write that on the row note.
- If a parent file plausibly exists (a bigger flywheel on the same
  title), keep this cut's shares ABOVE the parent on conversion-side
  behavior and BELOW on open-ended behaviors; never let the cut read
  like the parent with a smaller N.
- THE LEAN SHAPE (control 2026-09-17): the page is three tables -
  the cohort (spine + the two-way split), the before/after surface
  compare, and what the conversion was. Do NOT emit depth,
  first-next, time-to-next, same-session, leak, signup-path,
  qualifying-watch, or second-screen tables. Less is the design.
- Rates are messy (never .0 / .5 endings), no two rates identical."""


US_GEN_POP = 329_900_000
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


def _rows_from_pcts(seed, items, parent, doing, *, partition: bool,
                    cap_each: bool = True):
    rows = []
    for it in items or []:
        label = str(it.get('label') or '').strip()
        if not label:
            continue
        acc = _messy((seed, label), parent * float(it.get('pct') or 0)
                     / 100.0)
        if cap_each:
            acc = min(acc, parent - 1)
        rows.append({'label': label, 'accounts': acc,
                     'doing': it.get('note') or doing})
    if partition and rows:
        diff = parent - sum(r['accounts'] for r in rows)
        big = max(rows, key=lambda r: r['accounts'])
        big['accounts'] += diff
        assert big['accounts'] > 0, f'partition overflow under {doing}'
        assert sum(r['accounts'] for r in rows) == parent
    for r in rows:
        r['pct'] = round(r['accounts'] / parent * 100, 4)
    return rows


def build_flywheel(inputs: dict, prim: dict, *,
                   created_by: str = 'prometheus') -> dict:
    """Deterministic math + playbook asserts over reasoned primitives.

    Section 8 hygiene, generalized: three-row spine starting at the
    captured users (no gen pop row, no platform-total row), strictly
    decreasing nest, splits and partitions sum exactly, fork identity
    holds, no client count ends in 0 except a locked identity.
    """
    subject = str(inputs['subject']).strip()
    eco = str(inputs['ecosystem']).strip()
    start, end = _dates(inputs)
    pre_days = int(inputs.get('pre_days') or 180)
    post_days = int(inputs.get('post_days') or 30)
    seedbase = f'{subject}|{eco}'

    locked = inputs.get('cohort_count')
    cohort = int(locked) if locked else _messy(
        (seedbase, 'cohort'), float(prim.get('cohort') or 0))
    assert cohort > 0, 'cohort must be positive'

    second = _messy((seedbase, 'second'),
                    cohort * float(prim['second_surface_pct']) / 100.0)
    second = min(second, cohort - 1)
    checkout = _messy((seedbase, 'checkout'),
                      second * float(prim['checkout_pct_of_second'])
                      / 100.0)
    checkout = min(checkout, second - 1)
    assert 0 < checkout < second < cohort, 'nest must decrease'
    leftover = second - checkout

    cohort_label = str(prim.get('cohort_label')
                       or inputs.get('captured_action') or subject)
    conv_label = str(inputs.get('conversion_event') or 'Converted')

    spine = [
        {'id': 'captured', 'label': cohort_label,
         'doing': (f'{cohort_label}. The captured users this file '
                   f'starts at - never the country'),
         'where': eco, 'accounts': cohort, 'kept': 100.0, 'dropped': 0,
         'ofUs': round(cohort / US_GEN_POP * 100, 4),
         'next': 'A second owned surface, or stop', 'surface': eco,
         'job': 'win', 'timing': f'{start} to {end}'},
        {'id': 'second_surface',
         'label': f'Opened a second {eco} surface',
         'doing': (f'A second {eco} surface inside {post_days} days '
                   f'of the capture event'),
         'where': eco, 'accounts': second,
         'kept': round(second / cohort * 100, 4),
         'dropped': cohort - second,
         'ofUs': round(second / US_GEN_POP * 100, 4),
         'next': 'Conversion, or leftover', 'surface': eco,
         'job': 'win', 'timing': f'Inside {post_days} days'},
        {'id': 'conversion', 'label': conv_label,
         'doing': conv_label, 'where': eco, 'accounts': checkout,
         'kept': round(checkout / second * 100, 4),
         'dropped': leftover,
         'ofUs': round(checkout / US_GEN_POP * 100, 4),
         'next': 'Done for this conversion',
         'surface': 'Do not retarget the same conversion',
         'job': 'hold', 'timing': 'After the second surface'},
    ]
    assert all(s['id'] != 'us_gen_pop' for s in spine)

    fork = [
        {'id': 'checkout', 'label': conv_label,
         'doing': conv_label, 'accounts': checkout,
         'kept': round(checkout / second * 100, 4),
         'dropped': leftover,
         'surface': f'{eco} is the close',
         'timing': f'Inside {post_days} days'},
        {'id': 'leftover',
         'label': 'Opened a second surface and did not convert',
         'doing': 'A second owned surface, no conversion',
         'accounts': leftover,
         'kept': round(leftover / second * 100, 4),
         'dropped': checkout,
         'surface': 'The warm pool the store cannot see',
         'timing': 'Same window'},
    ]
    assert checkout + leftover == second, 'fork identity broken'

    detours = []
    sp = prim.get('splits') or {}
    if sp.get('enabled'):
        a = _messy((seedbase, 'split_a'),
                   cohort * float(sp.get('a_pct') or 50) / 100.0)
        a = min(a, cohort - 1)
        b = cohort - a
        detours.append({'title': 'Who is in this file', 'note': '',
                        'rows': [
            {'label': str(sp.get('a_label') or 'Group A'),
             'accounts': a, 'pct': round(a / cohort * 100, 4),
             'doing': 'Partitions this file'},
            {'label': str(sp.get('b_label') or 'Group B'),
             'accounts': b, 'pct': round(b / cohort * 100, 4),
             'doing': 'Partitions this file'},
        ]})

    surfaces = prim.get('surfaces') or []
    pre_rows = _rows_from_pcts(
        (seedbase, 'pre'),
        [{'label': s['label'], 'pct': s.get('pre_pct') or 0,
          'note': s.get('note') or ''} for s in surfaces
         if float(s.get('pre_pct') or 0) > 0],
        cohort, 'Overlap of the captured users', partition=False)
    post_rows = _rows_from_pcts(
        (seedbase, 'post'),
        [{'label': s['label'], 'pct': s.get('post_pct') or 0,
          'note': s.get('note') or ''} for s in surfaces
         if float(s.get('post_pct') or 0) > 0],
        cohort, 'Overlap of the captured users', partition=False)
    if pre_rows:
        detours.append({
            'title': f'{eco} surfaces in the {pre_days} days before',
            'note': 'overlap', 'rows': pre_rows})
    if post_rows:
        detours.append({
            'title': f'{eco} surfaces in the {post_days} days after',
            'note': 'overlap', 'rows': post_rows})
    cw = _rows_from_pcts((seedbase, 'checkout_what'),
                         prim.get('checkout_what'), checkout,
                         'Overlap of the conversion', partition=False)
    if cw:
        detours.append({'title': 'What the conversion was',
                        'note': 'overlap', 'rows': cw})

    pcopy = prim.get('copy') or {}
    kpis = pcopy.get('kpis') or [
        {'l': 'Captured users', 'v': f'{cohort:,}', 'hot': False},
        {'l': 'Second surface', 'v': f'{second:,}', 'hot': False},
        {'l': 'Converted', 'v': f'{checkout:,}', 'hot': True},
    ]
    copy = {
        'titleHtml': pcopy.get('title_html') or f'{subject} flywheel',
        'eyebrow': f'{eco} flywheel  \u00b7  {start} to {end}',
        'lead': pcopy.get('lead') or '',
        'deck': pcopy.get('deck') or '',
        'spineSec': '01 The spine',
        'forkSec': '02 After the event',
        'forkHead': pcopy.get('fork_head') or '',
        'forkBranchA': conv_label,
        'forkBranchB': 'Opened a surface and did not convert',
        'forkRead': pcopy.get('fork_read') or '',
        'detourSec': '03 The flywheel',
        'detourHead': pcopy.get('detour_head') or '',
        'kpis': kpis[:4],
    }
    blob = {
        'meta': {'window': f'{start} to {end}',
                 'unit': 'unique US accounts', 'sample': cohort,
                 'usGenPop': US_GEN_POP},
        'copy': copy, 'spine': spine, 'fork': fork, 'detours': detours,
    }
    now = _dt.datetime.utcnow().isoformat() + 'Z'
    proj_name = f'{subject} {eco} flywheel'
    payload = {
        'meta': {
            'story_mode': 'flywheel',
            'target_name': subject,
            'target_display': f'{subject} \u00b7 {eco} flywheel',
            'project_name': proj_name,
            'target': proj_name,
            'customer_brand': str(prim.get('customer_brand') or subject),
            'start_date': start, 'end_date': end,
            'target_type': 'flywheel',
            'category': str(prim.get('category') or 'brands'),
            'created_by': created_by, 'created_at': now,
        },
        'flywheel': blob,
        'facts': list(prim.get('facts') or [])[:5],
        'kpis': {
            'total_users': checkout,
            'conversion_pct': round(checkout / cohort * 100, 4),
        },
        'diagnostics': {
            'data_provenance': 'synthetic_estimate',
            'anchors_note': str(prim.get('anchors_note') or ''),
        },
    }
    return payload


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = 'prometheus') -> dict:
    start, end = _dates(inputs)
    user_prompt = json.dumps({
        'subject': inputs['subject'],
        'captured_action': inputs.get('captured_action') or '',
        'cohort_count': inputs.get('cohort_count'),
        'ecosystem': inputs['ecosystem'],
        'conversion_event': inputs.get('conversion_event') or '',
        'pre_days': int(inputs.get('pre_days') or 180),
        'post_days': int(inputs.get('post_days') or 30),
        'splits_hint': inputs.get('splits_hint') or '',
        'window': {'start': start, 'end': end},
        'notes': inputs.get('notes') or '',
    })
    prim = claude_json(RESEARCH_SYSTEM_PROMPT, user_prompt,
                       max_tokens=9000, temperature=0.6,
                       surface='flywheel_synthesis', tools=tools)
    if not isinstance(prim, dict) or not prim.get('surfaces'):
        raise RuntimeError('flywheel research returned no primitives')
    return build_flywheel(inputs, prim, created_by=created_by)


def persist(s3_client, payload: dict, username: str, job_id: str) -> str:
    """Write through the Journey IQ store so the tab lists and loads
    it like the Gilmore Girls flywheel cards."""
    try:
        from migration import journey_iq as _jiq
    except ImportError:
        import journey_iq as _jiq  # type: ignore
    return _jiq._persist(s3_client, payload,
                         payload['meta']['project_name'],
                         username, job_id)
