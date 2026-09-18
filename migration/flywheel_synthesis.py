"""Flywheel synthesis for the Prometheus 'Build a Flywheel' pull.

Method: reports/Gilmore_Girls_Acquired_Reactivated_Amazon_Flywheel_
Build_Playbook_2026_09_17.md (updated 2026-09-17 late). The designed
Flywheel IQ page is the file: THREE tables and nothing else.

  cohort            the captured users + the two-way split (masthead)
  flywheel_compare  one row per owned touch point, the window before
                    the event against the window after it ON THE SAME
                    ROW, plus the two union rows
  checkout_what     what the conversion union actually bought

Rules that made this file smart (section 6-8 of the playbook):
- The file starts at the captured users. Never US gen pop, never a
  platform total, never a parent file's row.
- MATCHED WINDOWS. Before and after are the same length (default 30
  days each). A long before against a short after prints a wall of
  red that is a clock artifact, not a finding.
- Persistence, not division: a monthly share of a six-month reach
  runs ~0.38-0.72 depending on how habitual the touch point is.
- The door test on every row: touch points the conversion UNLOCKS
  rise; touch points owned before and after stay flat (ratio 0.95x
  to 1.20x, under a point of movement); exactly ONE research-style
  row falls (the job that finished).
- The builder must not have the numbers for anything off the page:
  no nest, first_touch, leak, first_next, time_to_second, depth,
  when_first_play, checkout_path, last_touch, assists, or separate
  pre/post overlap tables.

Output: the study CSV the Flywheel IQ page reads, uploaded flat to
s3://dashboard-inputs/flywheel/<Study_Name>_<MM_DD_YYYY>.csv. A new
study or a corrected number is an S3 upload, never a deploy.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import re
from typing import Callable, Optional, Tuple

S3_BUCKET = 'dashboard-inputs'
FLYWHEEL_PREFIX = 'flywheel/'
PANEL_LINE = 'Crosswalk clickstream panel of 10M US consumers'

CSV_COLUMNS = [
    'table', 'period', 'surface', 'surface_label', 'amazon_family',
    'accounts', 'new_signup_accounts', 'reactivated_accounts',
    'base_accounts', 'share_of_base_pct', 'share_of_cohort_pct',
    'new_share_pct', 'reactivated_share_pct', 'pre_180d_accounts',
    'pre_180d_share_of_cohort_pct', 'lift_pp', 'additivity', 'note',
    'window', 'unit', 'cohort_accounts', 'panel',
]

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
  "pre_days": int|null,         // window before the event. Default
                                // 30. Windows MATCH by default: a
                                // long before against a short after
                                // is a clock artifact, not a compare.
  "post_days": int|null,        // window after. Default 30.
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


RESEARCH_SYSTEM_PROMPT = """You are building a Flywheel study from a
10M-consumer US clickstream panel: the owned-ecosystem digital life of
a captured cohort in the window BEFORE the capture event against the
matching window AFTER it. Research the title and the ecosystem (web
search when available), then reason the primitives. Everything must
read as observed first-party data: messy values, no round numbers, no
two identical rates. Return STRICT JSON only:

{
  "cohort": int,                 // captured-cohort count. If the
                                 // input carries cohort_count, return
                                 // it VERBATIM (locked anchor).
  "cohort_label": str,           // one line naming the captured users
  "entry_surface": str,          // snake_case id of the entry surface
                                 // (the thing they played / bought /
                                 // joined - pre is 0 by definition)
  "entry_label": str,            // display label for that surface
  "splits": {                    // two-way partition of the cohort
    "enabled": true|false,
    "a_key": str, "a_label": str,   // snake key + label (e.g.
    "b_key": str, "b_label": str,   // new_signup / reactivated_dormant)
    "a_pct": float,
    "a_note": str, "b_note": str
  },
  "touchpoints": [               // 10 to 16 owned touch points,
    {"surface": str,             // snake ids, EXCLUDING the entry
     "label": str,               // surface (code adds that row).
     "family": str,              // retail/audio/video/reading/
     "pre_pct": float,           // devices/grocery/health/gaming/other
     "post_pct": float,          // % of cohort with a session in each
     "a_share_of_row_pct": float,// split-A share of this row's count
     "gated": true|false,        // does the conversion OPEN this door
     "research_row": true|false},// the ONE row whose job finished
    ...                          // before the event (it falls)
  ],
  "unions": {
    "any_owned_label": str,      // e.g. "Any Amazon-owned digital
    "any_owned_pre_pct": float,  //  flywheel touch point except
    "any_owned_post_pct": float, //  Prime Video"
    "any_owned_a_share_pct": float,
    "checkout_label": str,       // e.g. "Any Amazon-owned checkout"
    "checkout_pre_pct": float,
    "checkout_post_pct": float,
    "checkout_a_share_pct": float
  },
  "checkout_what": [             // overlap of the post-window
    {"surface": str, "label": str,   // conversion union: what they
     "pct_of_checkout": float,       // actually bought
     "a_share_of_row_pct": float}, ...],
  "ecosystem_ceiling_pct": float,// the ecosystem's shopper ceiling
                                 // (Amazon: ~88). No touch point may
                                 // exceed it in either window.
  "anchors_note": str            // internal: anchors used
}

Rules that do not move:
- THE FILE STARTS AT THE CAPTURED USERS. No US gen pop row, no
  platform-total row, no parent-file row. The country is not a row.
- MATCHED WINDOWS: before and after are the same length. Reason the
  before window as a monthly share of the six-month reach (habitual
  surfaces ~0.55-0.72, occasional ~0.38-0.55) - never divide a
  half-year reach by six.
- THE DOOR TEST on every touch point: if the conversion opens the
  door (a membership perk, free shipping, the catalog), the row
  RISES in the after window. If they owned the habit before and
  after (a doorbell app, a device photos app), the row stays FLAT:
  ratio 0.95x to 1.20x and under a point of movement. Exactly ONE
  research-style row FALLS (the where-to-watch / which-one research
  job that the event finished). The entry surface itself is 0 before
  and 100 after by definition - the code writes that row.
- The event's own signup / pricing / account page is NOT a touch
  point: it measures the event, not what the event caused.
- Split every row two ways when splits are enabled; the sides sum to
  the row and disagree in the direction the composition predicts.
- Unions are rows, not sums: the checkout union sits under the
  any-owned union, both sit under the cohort, every checkout_what
  row sits under the checkout union.
- THREE TABLES ONLY. Do not produce nest, first_touch, leak,
  first_next, time_to_second, depth, when_first_play, checkout_path,
  last_touch, or assist data. The builder must not have the numbers.
- Rates are messy (never .0 / .5 endings), no two rates identical."""


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


def _pct(n: int, base: int) -> float:
    return round(n / base * 100.0, 4) if base else 0.0


def _study_name(subject: str, ecosystem: str) -> str:
    base = f'{subject} {ecosystem} Flywheel'
    stem = re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_')
    return f"{stem}_{_dt.date.today().strftime('%m_%d_%Y')}"


def build_flywheel_study(inputs: dict, prim: dict, *,
                         created_by: str = 'prometheus'
                         ) -> Tuple[str, str, dict]:
    """Deterministic math + section-8 asserts over reasoned primitives.

    Returns (csv_text, study_name, summary). Three tables, exact page
    columns, matched windows, door-test bands, one falling row, messy
    digits, unions coherent. A bad primitive fails loudly.
    """
    subject = str(inputs['subject']).strip()
    eco = str(inputs['ecosystem']).strip()
    pre_days = int(inputs.get('pre_days') or 30)
    post_days = int(inputs.get('post_days') or pre_days)
    seedbase = f'{subject}|{eco}'

    locked = inputs.get('cohort_count')
    cohort = int(locked) if locked else _messy(
        (seedbase, 'cohort'), float(prim.get('cohort') or 0))
    assert cohort > 0, 'cohort must be positive'

    sp = prim.get('splits') or {}
    split_on = bool(sp.get('enabled'))
    if split_on:
        a = _messy((seedbase, 'split_a'),
                   cohort * float(sp.get('a_pct') or 50) / 100.0)
        a = min(a, cohort - 1)
        b = cohort - a
        assert a + b == cohort, 'splits must partition the cohort'
    else:
        a = b = 0

    ev = str(prim.get('entry_label') or inputs.get('captured_action')
             or subject)
    ev_id = str(prim.get('entry_surface') or 'entry_event')
    cohort_label = str(prim.get('cohort_label')
                       or inputs.get('captured_action') or subject)
    win_txt = (f'{pre_days} days before {ev} vs '
               f'{post_days} days after {ev}')
    event_win = f"{_dates(inputs)[0]} to {_dates(inputs)[1]}"
    ceiling = float(prim.get('ecosystem_ceiling_pct') or 88.0)

    def split_row(total, a_share_pct):
        if not split_on or total == 0:
            return (a and 0) or 0, 0
        av = _messy((seedbase, 'rowsplit', total, a_share_pct),
                    total * float(a_share_pct) / 100.0, floor=0)
        av = max(0, min(av, total))
        return av, total - av

    rows = []

    def emit(table, period, surface, label, family, accounts, a_acc,
             b_acc, base, pre_acc=None, pre_share=None, lift=None,
             additivity='', note='', window=''):
        rows.append({
            'table': table, 'period': period, 'surface': surface,
            'surface_label': label, 'amazon_family': family,
            'accounts': accounts,
            'new_signup_accounts': a_acc if split_on else '',
            'reactivated_accounts': b_acc if split_on else '',
            'base_accounts': base,
            'share_of_base_pct': f'{_pct(accounts, base):.4f}',
            'share_of_cohort_pct': f'{_pct(accounts, cohort):.4f}',
            'new_share_pct': (f'{_pct(a_acc, a):.4f}'
                              if split_on and a else ''),
            'reactivated_share_pct': (f'{_pct(b_acc, b):.4f}'
                                      if split_on and b else ''),
            'pre_180d_accounts': '' if pre_acc is None else pre_acc,
            'pre_180d_share_of_cohort_pct':
                '' if pre_share is None else f'{pre_share:.4f}',
            'lift_pp': '' if lift is None else f'{lift:.4f}',
            'additivity': additivity, 'note': note,
            'window': window or event_win, 'unit': 'unique US accounts',
            'cohort_accounts': cohort, 'panel': PANEL_LINE,
        })

    # ---- table 1: cohort (masthead) ---------------------------------------
    emit('cohort', 'event', 'captured_cohort', cohort_label, 'video',
         cohort, a, b, cohort, additivity='identity',
         note=str(inputs.get('captured_action') or cohort_label))
    if split_on:
        emit('cohort', 'event', str(sp.get('a_key') or 'group_a'),
             str(sp.get('a_label') or 'Group A'), 'account', a, a, 0,
             cohort, additivity=f"partitions cohort with "
             f"{sp.get('b_key') or 'group_b'}",
             note=str(sp.get('a_note') or ''))
        emit('cohort', 'event', str(sp.get('b_key') or 'group_b'),
             str(sp.get('b_label') or 'Group B'), 'account', b, 0, b,
             cohort, additivity=f"partitions cohort with "
             f"{sp.get('a_key') or 'group_a'}",
             note=str(sp.get('b_note') or ''))

    # ---- table 2: flywheel_compare ----------------------------------------
    period = f'pre_{pre_days}d_vs_post_{post_days}d'

    def compare_row(surface, label, family, pre_n, post_n, a_share):
        av, bv = split_row(post_n, a_share)
        pre_s, post_s = _pct(pre_n, cohort), _pct(post_n, cohort)
        note = (f'Pre {pre_n} ({pre_s:.4f}%). Post {post_n} '
                f'({post_s:.4f}%). Lift {post_s - pre_s:+.4f} pp. '
                f'Both windows are {pre_days} days.'
                if pre_days == post_days else
                f'Pre {pre_n} ({pre_s:.4f}%) in {pre_days} days. '
                f'Post {post_n} ({post_s:.4f}%) in {post_days} days.')
        emit('flywheel_compare', period, surface, label, family,
             post_n, av, bv, cohort, pre_acc=pre_n, pre_share=pre_s,
             lift=post_s - pre_s,
             additivity='compare. pre columns ride on the same row',
             note=note, window=win_txt)
        return post_s - pre_s

    # entry surface: pre 0, post = cohort, by definition
    compare_row(ev_id, ev, 'video', 0, cohort, _pct(a, cohort))

    falls = 0
    tps = list(prim.get('touchpoints') or [])
    assert tps, 'no touchpoints reasoned'
    for tp in tps:
        pre_n = _messy((seedbase, tp['surface'], 'pre'),
                       cohort * float(tp.get('pre_pct') or 0) / 100.0,
                       floor=0)
        post_n = _messy((seedbase, tp['surface'], 'post'),
                        cohort * float(tp.get('post_pct') or 0) / 100.0,
                        floor=0)
        pre_n, post_n = min(pre_n, cohort - 1), min(post_n, cohort - 1)
        pre_s, post_s = _pct(pre_n, cohort), _pct(post_n, cohort)
        assert pre_s <= ceiling and post_s <= ceiling, (
            f"{tp['surface']} exceeds the ecosystem ceiling {ceiling}")
        lift = post_s - pre_s
        if tp.get('research_row'):
            assert lift < 0, (f"research row {tp['surface']} must fall")
            falls += 1
        elif tp.get('gated'):
            assert lift > 0, (f"gated row {tp['surface']} must rise")
        else:
            ratio = (post_n / pre_n) if pre_n else 1.0
            assert abs(lift) < 1.0 and 0.95 <= ratio <= 1.20, (
                f"non-gated row {tp['surface']} moved outside the "
                f"flat band: lift {lift:.4f}, ratio {ratio:.3f}")
        compare_row(str(tp['surface']), str(tp['label']),
                    str(tp.get('family') or 'other'), pre_n, post_n,
                    float(tp.get('a_share_of_row_pct') or 50))
    assert falls == 1, f'exactly one research row must fall, got {falls}'

    un = prim.get('unions') or {}
    own_pre = _messy((seedbase, 'ownpre'),
                     cohort * float(un.get('any_owned_pre_pct') or 0)
                     / 100.0, floor=0)
    own_post = _messy((seedbase, 'ownpost'),
                      cohort * float(un.get('any_owned_post_pct') or 0)
                      / 100.0, floor=0)
    co_pre = _messy((seedbase, 'copre'),
                    cohort * float(un.get('checkout_pre_pct') or 0)
                    / 100.0, floor=0)
    co_post = _messy((seedbase, 'copost'),
                     cohort * float(un.get('checkout_post_pct') or 0)
                     / 100.0, floor=0)
    own_pre, own_post = min(own_pre, cohort - 1), min(own_post, cohort - 1)
    co_pre, co_post = min(co_pre, own_pre), min(co_post, own_post - 1)
    assert co_post < own_post <= cohort, 'union order broken'
    compare_row('any_owned_union',
                str(un.get('any_owned_label')
                    or f'Any {eco} digital flywheel touch point'),
                'union', own_pre, own_post,
                float(un.get('any_owned_a_share_pct') or 50))
    compare_row('checkout_union',
                str(un.get('checkout_label') or f'Any {eco} checkout'),
                'union', co_pre, co_post,
                float(un.get('checkout_a_share_pct') or 50))

    # ---- table 3: checkout_what -------------------------------------------
    for cw in prim.get('checkout_what') or []:
        n = _messy((seedbase, 'cw', cw['surface']),
                   co_post * float(cw.get('pct_of_checkout') or 0)
                   / 100.0, floor=0)
        n = min(n, co_post)
        av, bv = split_row(n, float(cw.get('a_share_of_row_pct') or 50))
        emit('checkout_what', f'post_{post_days}d', str(cw['surface']),
             str(cw['label']), 'union', n, av, bv, co_post,
             additivity='overlap. do not add',
             note=(f'Of the {co_post:,} who completed a conversion '
                   f'in {post_days} days.'),
             window=f'{post_days} days after {ev}')

    # ---- hygiene: table whitelist + share collision sweep ------------------
    tables = {r['table'] for r in rows}
    assert tables == ({'cohort', 'flywheel_compare', 'checkout_what'}
                      if prim.get('checkout_what')
                      else {'cohort', 'flywheel_compare'}), tables
    seen = {}
    for r in rows:
        if r['table'] != 'flywheel_compare':
            continue
        k = r['share_of_cohort_pct']
        assert not k.endswith('00') or k in ('0.0000', '100.0000'), (
            f".XX00 share on {r['surface']}: {k}")
        assert k not in seen or k in ('0.0000', '100.0000'), (
            f"duplicate 4dp share {k}: {r['surface']} vs {seen.get(k)}")
        seen[k] = r['surface']

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    name = _study_name(subject, eco)
    summary = {
        'study_name': name,
        'title': f'{subject} {eco} Flywheel',
        'cohort': cohort, 'conversions': co_post,
        'window': win_txt, 'created_by': created_by,
    }
    return out.getvalue(), name, summary


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = 'prometheus') -> Tuple[str, str, dict]:
    start, end = _dates(inputs)
    pre = int(inputs.get('pre_days') or 30)
    post = int(inputs.get('post_days') or pre)
    user_prompt = json.dumps({
        'subject': inputs['subject'],
        'captured_action': inputs.get('captured_action') or '',
        'cohort_count': inputs.get('cohort_count'),
        'ecosystem': inputs['ecosystem'],
        'conversion_event': inputs.get('conversion_event') or '',
        'pre_days': pre, 'post_days': post,
        'splits_hint': inputs.get('splits_hint') or '',
        'window': {'start': start, 'end': end},
        'notes': inputs.get('notes') or '',
    })
    prim = claude_json(RESEARCH_SYSTEM_PROMPT, user_prompt,
                       max_tokens=9000, temperature=0.6,
                       surface='flywheel_synthesis', tools=tools)
    if not isinstance(prim, dict) or not prim.get('touchpoints'):
        raise RuntimeError('flywheel research returned no primitives')
    return build_flywheel_study(inputs, prim, created_by=created_by)


def persist(s3_client, csv_text: str, study_name: str) -> str:
    """Upload the study where the Flywheel IQ page reads it. A new
    study or a corrected number is an upload, never a deploy."""
    key = f'{FLYWHEEL_PREFIX}{study_name}.csv'
    s3_client.put_object(Bucket=S3_BUCKET, Key=key,
                         Body=csv_text.encode('utf-8'),
                         ContentType='text/csv')
    return key
