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
plain TAM. The TAM row itself never changes."""


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
    return payload


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = 'prometheus') -> dict:
    start, end = _dates(inputs)
    user_prompt = json.dumps({
        'subject': inputs['subject'],
        'platform': inputs['platform'],
        'conversion_event': inputs.get('conversion_event') or '',
        'journey_kind': inputs.get('journey_kind') or 'purchase',
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
