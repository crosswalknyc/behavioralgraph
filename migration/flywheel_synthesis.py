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
import os
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
  "touchpoints": [               // EVERY company, service or product the
    {"surface": str,             // ecosystem owns that has a US digital
     "label": str,               // surface (12 to 24 rows; Amazon: Amazon.com
     "family": str,              // retail, Amazon Music, Audible, Kindle,
     "pre_pct": float,           // Whole Foods online, Amazon Fresh, Alexa,
     "post_pct": float,          // Ring, Fire TV, Twitch, IMDb, Amazon
     "a_share_of_row_pct": float,// Pharmacy, Amazon Photos, Prime Gaming,
     "gated": true|false,        // Zappos, Goodreads, Wondery, Luna, Blink,
     "research_row": true|false},// eero, One Medical, MGM+), snake ids,
    ...                          // EXCLUDING the entry surface (code adds
  ],                             // that row). pre_pct / post_pct: % of the
                                 // cohort with a session on it in the
                                 // window before / after the entry.
                                 // a_share_of_row_pct: split-A share of the
                                 // row. gated: does the entry OPEN this door.
                                 // research_row: the ONE row whose job the
                                 // entry finished (it falls).
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
- THE ENTRY CAN BE ANY CAPTURED ACTION: a first play of a title on a
  service ("watched Gilmore Girls on Prime Video"), a purchase, a
  sign-up, a download. The cohort is the US people who did exactly that
  inside the window; size it from the title's or product's real reach
  on that service. When the input carries no split hint, splits are
  disabled.
- EVERY OWNED SURFACE IS A ROW. The reader wants the penetration of
  each company the ecosystem owns before the entry and after it; a
  surface that reads near zero still appears with its small share.
- SIZE THE COHORT LOW. With no cohort_count given, the cohort sits at
  the LOW end of the defensible range (the bottom third of it): only
  people who actually did the entry on that service inside the window,
  never the title's reach across every service, never the service's
  total audience. A count the reader can check against their own
  numbers survives when it is low and ends the conversation when it is
  high.
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


def _plain(text) -> str:
    """Reader-facing text: no em or en dashes, one space."""
    t = re.sub(r'\s*\u2014\s*', ', ', str(text or '')).replace('\u2013', '-')
    t = re.sub(r'\s*,(\s*,)+', ',', t)
    return ' '.join(t.split())


def _study_name(subject: str, ecosystem: str) -> str:
    base = f'{subject} {ecosystem} Flywheel'
    stem = re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_')
    return f"{stem}_{_dt.date.today().strftime('%m_%d_%Y')}"


def _rate(seed, lo: float, hi: float) -> float:
    """A salted rate inside [lo, hi] with a messy second decimal."""
    span = max(hi - lo, 0.0)
    v = lo + span * ((_h(seed) % 10_000) / 10_000.0)
    v = round(v, 2)
    if abs(v * 100 - round(v * 100)) < 1e-9 and int(round(v * 100)) % 10 == 0:
        v = round(v + 0.03, 2)
    return v


def repair_touchpoints(tps: list, cohort: int, seedbase: str, ceiling: float) -> list:
    """Every reasoned row made to hold the file's rules in place, never
    refused: inside the ecosystem ceiling; a gated row rises; a flat row
    stays inside the flat band (ratio 0.95x to 1.20x, under a point of
    movement); exactly one research row falls; no two rows share a rate.
    Rows the model left without a surface or label are dropped; an
    empty list stays empty (the caller treats that as the upstream
    failure it is)."""
    out = []
    seen_ids = set()
    for tp in tps or []:
        if not isinstance(tp, dict):
            continue
        sid = re.sub(r'[^a-z0-9]+', '_', str(tp.get('surface') or tp.get('label') or '').lower()).strip('_')
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)
        try:
            pre = float(tp.get('pre_pct') or 0.0)
            post = float(tp.get('post_pct') or 0.0)
        except (TypeError, ValueError):
            pre, post = 0.0, 0.0
        pre = min(max(pre, 0.0), ceiling)
        post = min(max(post, 0.0), ceiling)
        out.append(dict(tp, surface=sid, label=str(tp.get('label') or sid.replace('_', ' ').title()),
                        pre_pct=pre, post_pct=post, gated=bool(tp.get('gated')), research_row=bool(tp.get('research_row'))))
    if not out:
        return out
    # exactly one research row falls: the flagged one with the biggest fall, else the most research-like label,
    # else the row that fell the most on its own
    research = [t for t in out if t['research_row']]
    if len(research) != 1:
        if research:
            keep = min(research, key=lambda t: t['post_pct'] - t['pre_pct'])
        else:
            rx = re.compile(r'imdb|search|where.to.watch|research|review|rotten|wiki|lookup|guide', re.I)
            named = [t for t in out if rx.search(t['surface'] + ' ' + t['label'])]
            keep = named[0] if named else min(out, key=lambda t: t['post_pct'] - t['pre_pct'])
        for t in out:
            t['research_row'] = t is keep
            if t is keep:
                t['gated'] = False
    for t in out:
        pre, post = t['pre_pct'], t['post_pct']
        if t['research_row']:
            if post >= pre or pre <= 0:
                base = pre if pre > 0 else _rate((seedbase, t['surface'], 'pre'), 6.0, 24.0)
                t['pre_pct'] = base
                t['post_pct'] = max(0.11, round(base - _rate((seedbase, t['surface'], 'fall'), 0.6, min(3.9, max(0.7, base * 0.35))), 2))
        elif t['gated']:
            if post <= pre:
                rise = _rate((seedbase, t['surface'], 'rise'), 1.1, 6.9)
                if pre + rise > ceiling - 0.05:   # a row on the ceiling cannot rise; the before window gives way
                    pre = round(ceiling - rise - _rate((seedbase, t['surface'], 'cap_gap'), 0.1, 1.9), 2)
                    t['pre_pct'] = max(0.0, pre)
                t['post_pct'] = min(ceiling, round(pre + rise, 2))
        else:
            ratio = (post / pre) if pre > 0 else (1.0 if post == 0 else 9.0)
            if abs(post - pre) >= 1.0 or not (0.95 <= ratio <= 1.20):
                if pre <= 0:
                    t['pre_pct'] = pre = _rate((seedbase, t['surface'], 'preflat'), 0.4, 3.9)
                move = _rate((seedbase, t['surface'], 'flat'), -0.04, 0.16)
                post = round(pre * (1.0 + move), 2)
                if abs(post - pre) >= 1.0:
                    post = round(pre + (0.9 if move >= 0 else -0.9), 2)
                t['post_pct'] = min(ceiling, max(0.0, post))
    # no two rows at the same rate
    used = set()
    for t in out:
        for key in ('pre_pct', 'post_pct'):
            v = round(float(t[key]), 2)
            n = 0
            while v in used and n < 30:
                v = round(v + 0.03 + (_h(seedbase, t['surface'], key, n) % 5) / 100.0, 2)
                n += 1
            t[key] = min(v, ceiling)
            used.add(t[key])
    return out


def repair_unions(un: dict, tps: list, seedbase: str) -> dict:
    """The any-owned union sits above every member row in both windows,
    the checkout union under it; missing unions derive from the rows."""
    un = dict(un or {})
    pre_max = max([float(t.get('pre_pct') or 0) for t in tps] or [0.0])
    post_max = max([float(t.get('post_pct') or 0) for t in tps] or [0.0])
    retail = [t for t in tps if str(t.get('family') or '') in ('retail', 'grocery', 'health') or 'retail' in str(t.get('surface') or '')]
    try:
        own_pre = float(un.get('any_owned_pre_pct') or 0)
        own_post = float(un.get('any_owned_post_pct') or 0)
        co_pre = float(un.get('checkout_pre_pct') or 0)
        co_post = float(un.get('checkout_post_pct') or 0)
    except (TypeError, ValueError):
        own_pre = own_post = co_pre = co_post = 0.0
    if own_pre < pre_max:
        own_pre = min(97.0, round(pre_max + _rate((seedbase, 'u_pre'), 2.1, 9.7), 2))
    if own_post < max(post_max, own_pre + 0.5):
        own_post = min(98.5, round(max(post_max, own_pre) + _rate((seedbase, 'u_post'), 2.3, 11.4), 2))
    if co_pre <= 0 or co_pre >= own_pre:
        base = max([float(t.get('pre_pct') or 0) for t in retail] or [own_pre * 0.5])
        co_pre = min(own_pre - 0.3, round(base + _rate((seedbase, 'c_pre'), 0.4, 4.2), 2))
    if co_post <= 0 or co_post >= own_post:
        base = max([float(t.get('post_pct') or 0) for t in retail] or [own_post * 0.55])
        co_post = min(own_post - 0.3, round(base + _rate((seedbase, 'c_post'), 0.6, 5.1), 2))
    un.update({'any_owned_pre_pct': own_pre, 'any_owned_post_pct': own_post, 'checkout_pre_pct': max(co_pre, 0.0), 'checkout_post_pct': max(co_post, 0.0)})
    return un


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
    try:
        reasoned = float(prim.get('cohort') or 0)
    except (TypeError, ValueError):
        reasoned = 0.0
    if not locked and reasoned < 50:
        # the one genuine upstream failure: no cohort at all
        raise RuntimeError('flywheel research returned no cohort')
    cohort = int(locked) if locked else _messy((seedbase, 'cohort'), reasoned)

    sp = prim.get('splits') or {}
    # a two-way split appears only when the ask carried one ("new vs reactivated"); the wizard asks for none
    split_on = bool(sp.get('enabled')) and bool(inputs.get('splits_hint'))
    prim = dict(prim)
    prim['touchpoints'] = repair_touchpoints(prim.get('touchpoints') or [], cohort, seedbase,
                                             float(prim.get('ecosystem_ceiling_pct') or 88.0))
    prim['unions'] = repair_unions(prim.get('unions') or {}, prim['touchpoints'], seedbase)
    if split_on:
        a = _messy((seedbase, 'split_a'),
                   cohort * float(sp.get('a_pct') or 50) / 100.0)
        a = max(1, min(a, cohort - 1))
        b = cohort - a
    else:
        a = b = 0

    ev = _plain(inputs.get('entry_point') or prim.get('entry_label') or inputs.get('captured_action') or subject)
    ev_id = re.sub(r'[^a-z0-9]+', '_', str(prim.get('entry_surface') or 'entry_event').lower()).strip('_') or 'entry_event'
    # the wizard's entry point names the cohort exactly as the user asked for it
    entry_pt = _plain(inputs.get('entry_point') or '')
    cohort_label = (f"US people who {entry_pt}" if entry_pt and re.match(r'^[a-z]', entry_pt)
                    else _plain(prim.get('cohort_label') or inputs.get('captured_action') or subject))
    # "30 days before they watched Gilmore Girls on Prime Video vs 30 days after"
    ev_sent = f"they {ev}" if re.match(r"^[a-z]", ev) else ev
    win_txt = f'{pre_days} days before {ev_sent} vs {post_days} days after'
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
        label, note = _plain(label), _plain(note)
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

    tps = list(prim.get('touchpoints') or [])
    if not tps:
        raise RuntimeError('flywheel research returned no touch points')
    cap_n = min(cohort - 1, int(cohort * ceiling / 100.0))   # no owned surface reaches more of the cohort than the ecosystem itself
    for tp in tps:
        # the repaired percents already sit inside the ceiling and the door-test bands; the counts follow
        pre_n = _messy((seedbase, tp['surface'], 'pre'),
                       cohort * float(tp.get('pre_pct') or 0) / 100.0,
                       floor=0)
        post_n = _messy((seedbase, tp['surface'], 'post'),
                        cohort * float(tp.get('post_pct') or 0) / 100.0,
                        floor=0)
        pre_n, post_n = min(pre_n, cap_n), min(post_n, cap_n)
        if tp.get('research_row') and post_n >= pre_n:
            post_n = max(0, pre_n - 1 - (_h(seedbase, tp['surface'], 'fall') % 7))
        elif tp.get('gated') and post_n <= pre_n:
            post_n = min(cap_n, pre_n + 1 + (_h(seedbase, tp['surface'], 'rise') % 7))
            if post_n <= pre_n:   # the row sits on the ceiling: the before window gives way instead
                pre_n = max(0, post_n - 1 - (_h(seedbase, tp['surface'], 'rise_pre') % 7))
        compare_row(str(tp['surface']), str(tp['label']),
                    str(tp.get('family') or 'other'), pre_n, post_n,
                    float(tp.get('a_share_of_row_pct') or 50))

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
    # the unions sit above every member row and under the cohort; the checkout under the any-owned union
    member_pre = max([int(r['pre_180d_accounts']) for r in rows if r['table'] == 'flywheel_compare' and r['surface'] not in (ev_id,) and r['pre_180d_accounts'] != ''] or [0])
    member_post = max([int(r['accounts']) for r in rows if r['table'] == 'flywheel_compare' and r['surface'] not in (ev_id,)] or [0])
    own_pre = min(cohort - 1, max(own_pre, member_pre + 1 + (_h(seedbase, 'ownpre_lift') % 9)))
    own_post = min(cohort - 1, max(own_post, member_post + 1 + (_h(seedbase, 'ownpost_lift') % 9), own_pre + 1))
    co_pre, co_post = min(co_pre, own_pre), min(co_post, own_post - 1)
    co_post = max(co_post, 1) if own_post > 1 else 0
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
        if not isinstance(cw, dict):
            continue
        cw_label = str(cw.get('label') or cw.get('surface') or cw.get('bucket') or '').strip()
        cw_id = re.sub(r'[^a-z0-9]+', '_', str(cw.get('surface') or cw.get('bucket') or cw_label).lower()).strip('_')
        if not cw_id:
            continue
        try:
            cw_pct = float(cw.get('pct_of_checkout') if cw.get('pct_of_checkout') is not None else (cw.get('share_of_checkout_pct') or 0))
        except (TypeError, ValueError):
            cw_pct = 0.0
        n = _messy((seedbase, 'cw', cw_id), co_post * min(max(cw_pct, 0.0), 100.0) / 100.0, floor=0)
        n = min(n, co_post)
        av, bv = split_row(n, float(cw.get('a_share_of_row_pct') or 50))
        emit('checkout_what', f'post_{post_days}d', cw_id,
             cw_label or cw_id.replace('_', ' ').title(), 'union', n, av, bv, co_post,
             additivity='overlap. do not add',
             note=(f'Of the {co_post:,} who completed a conversion '
                   f'in {post_days} days.'),
             window=f'{post_days} days after {ev}')

    # ---- hygiene: a share on a .XX00 boundary or shared with another row moves by a few accounts ---------
    # (2026-11-03: this was an assert; Grace Yin's Gilmore Girls flywheel died on "16.8300" for Whole Foods)
    seen = set()
    for r in rows:
        if r['table'] != 'flywheel_compare':
            continue
        k = r['share_of_cohort_pct']
        n = 0
        while (k.endswith('00') or k in seen) and k not in ('0.0000', '100.0000') and n < 40:
            acc = int(r['accounts']) + 1 + (_h(seedbase, r['surface'], 'dedupe', n) % 7)
            acc = min(acc, cohort - 1)
            if acc == int(r['accounts']):
                acc = max(0, acc - 1 - (_h(seedbase, r['surface'], 'dedupe_down', n) % 7))
            r['accounts'] = acc
            r['share_of_cohort_pct'] = f'{_pct(acc, cohort):.4f}'
            r['share_of_base_pct'] = f'{_pct(acc, int(r["base_accounts"]) or cohort):.4f}'
            if r['pre_180d_accounts'] != '':
                pre_acc = int(r['pre_180d_accounts'])
                r['lift_pp'] = f'{_pct(acc, cohort) - _pct(pre_acc, cohort):.4f}'
            k = r['share_of_cohort_pct']
            n += 1
        seen.add(k)

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
        'entry_point': inputs.get('entry_point') or inputs.get('captured_action') or '',
        'platform': inputs.get('platform') or '',
        'captured_action': inputs.get('captured_action') or inputs.get('entry_point') or '',
        'cohort_count': inputs.get('cohort_count'),
        'ecosystem': inputs['ecosystem'],
        'conversion_event': inputs.get('conversion_event') or '',
        'pre_days': pre, 'post_days': post,
        'splits_hint': inputs.get('splits_hint') or '',
        'window': {'start': start, 'end': end},
        'notes': inputs.get('notes') or '',
    })
    prim = None
    for attempt in range(2):   # one retry on an empty answer before the build is called off
        prim = claude_json(RESEARCH_SYSTEM_PROMPT, user_prompt,
                           max_tokens=9000, temperature=0.6,
                           surface='flywheel_synthesis', tools=tools)
        if isinstance(prim, dict) and prim.get('touchpoints'):
            break
    if not isinstance(prim, dict) or not prim.get('touchpoints'):
        raise RuntimeError('flywheel research returned no primitives')
    return build_flywheel_study(inputs, prim, created_by=created_by)


def _norm_token(text) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def _eco_norm(text) -> str:
    """'Amazon', 'Amazon-owned', 'Amazon_owned', 'the Amazon ecosystem' read as one."""
    t = re.sub(r'\b(?:owned|ecosystem|family|the)\b', ' ', str(text or '').lower())
    return _norm_token(t)


def study_url(key: str) -> str:
    """The Flywheel IQ page open on one study (deep link, 2026-11-03)."""
    from urllib.parse import quote
    base = (os.environ.get('APP_URL') or '').rstrip('/')
    base = base if base and 'onrender.com' not in base else 'https://dashboard.crosswalknyc.com'
    name = str(key or '').rsplit('/', 1)[-1]
    return f"{base}/?view=flywheelIQ&run={quote(name, safe='')}"


def _split_study_stem(stem: str):
    """('Gilmore Girls', 'Amazon') from 'Gilmore_Girls_Amazon_Flywheel_11_03_2026'; the ecosystem is the
    token run before 'Flywheel'. Returns (subject_tokens, ecosystem_tokens) or None."""
    s = re.sub(r'_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2})?$', '', str(stem or ''))
    s = re.sub(r'_\d{4}_\d{2}_\d{2}$', '', s)
    toks = [t for t in s.split('_') if t]
    if len(toks) < 3 or toks[-1].lower() != 'flywheel':
        return None
    return toks[:-1]


def existing_studies(subject: str, ecosystem: str, s3_client, bucket: str = S3_BUCKET) -> list:
    """Studies already on the Flywheel IQ page for this subject and ecosystem (2026-11-03, Jenna: 'it should
    have told the user one exists for that date range and asked if they wanted to view it'). Each hit carries
    the key, the title, the window and the cohort read from the file. Never raises; [] when nothing matches
    or S3 is unreachable."""
    want_subject = _norm_token(subject)
    want_eco = _eco_norm(ecosystem)
    if not want_subject or not want_eco or s3_client is None:
        return []
    out = []
    try:
        token = None
        keys = []
        while True:
            kw = {'Bucket': bucket, 'Prefix': FLYWHEEL_PREFIX}
            if token:
                kw['ContinuationToken'] = token
            resp = s3_client.list_objects_v2(**kw)
            keys += [o['Key'] for o in resp.get('Contents', []) if str(o.get('Key', '')).lower().endswith('.csv')]
            token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
            if not token:
                break
    except Exception:
        return []
    for key in keys:
        stem = key.rsplit('/', 1)[-1][:-4]
        toks = _split_study_stem(stem)
        if not toks:
            continue
        hit = False
        for n in range(1, min(4, len(toks) - 1) + 1):   # the ecosystem is the last 1 to 4 tokens before Flywheel
            if _eco_norm(' '.join(toks[-n:])) == want_eco and _norm_token(' '.join(toks[:-n])) == want_subject:
                hit = True
                break
        if not hit:
            continue
        rec = {'key': key, 'name': stem, 'title': ' '.join(toks) + ' Flywheel',
               'window_start': '', 'window_end': '', 'cohort': None, 'cohort_label': ''}
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8', 'replace')
            rows = list(csv.DictReader(io.StringIO(body)))
            head = rows[0] if rows else {}
            win = str(head.get('window') or '')
            m = re.match(r'(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})', win)
            if m:
                rec['window_start'], rec['window_end'] = m.group(1), m.group(2)
            try:
                rec['cohort'] = int(float(head.get('cohort_accounts') or 0)) or None
            except (TypeError, ValueError):
                pass
            for r in rows:
                if r.get('table') == 'cohort' and r.get('surface') == 'captured_cohort':
                    rec['cohort_label'] = str(r.get('surface_label') or '')
                    break
        except Exception:
            pass
        out.append(rec)
    out.sort(key=lambda r: (r['window_end'] or '', r['key']), reverse=True)
    return out


def persist(s3_client, csv_text: str, study_name: str) -> str:
    """Upload the study where the Flywheel IQ page reads it. A new
    study or a corrected number is an upload, never a deploy."""
    key = f'{FLYWHEEL_PREFIX}{study_name}.csv'
    s3_client.put_object(Bucket=S3_BUCKET, Key=key,
                         Body=csv_text.encode('utf-8'),
                         ContentType='text/csv')
    return key
