#!/usr/bin/env python3
"""Cross-product coherence for a subject's Profile IQ universe.

2026-10-20 (Jenna, forwarding the corpus catalog sweep note on NCIS: New
York): a Digital Journey had put the first-episode audience at 3,266,736
US people over Jul 7 to Oct 19, and a Profile IQ Total Universe built the
same night put the whole trailing-year audience at 260,390 US people
(7,893 inside the 10 million sample). The sweep was comparing the right
cells (projected US people on both sides); the profile had simply been
sized cold, with no look at what the dashboard already held on the
subject. Jenna: "have the system realize that so no need for the email".

The principle, in one line: a Profile IQ Total Universe is the subject's
whole audience over its window, so every other product's US-people
figure on a shorter overlapping window describes a subset and the
universe must sit above it (Jenna 2026-08-25: "whichever comes first
needs to ensure that the universe is equal to the number of us viewers
of the show or movie and coordinated so they always align").

Two wire points share this module:

  * the build (migration/synth_queue_worker._run_new_build) consults
    the corpus catalog before sizing and lifts a fresh Total Universe
    above the largest sub-window figure another product published
    (``floor_for_build``, ``lifted_sample``), then re-asserts it after
    every later sizing step;
  * the sweep (migration/corpus_catalog_sync) corrects a universe that
    already shipped below such a figure IN PLACE under its own dated
    filename (``lift_profile_in_place``): the sample moves, the whole
    BP -> Raw -> Projection chain recomputes, the file's cuts scale by
    the same factor so their shares hold, the catalog re-indexes, and
    nothing mails (no-rebuild-level-correction.mdc: corrections are
    silent and part of the product).

Every lifted value passes through the salted messy-sample helper (no
round samples, no two subjects landing on one value) and sits a
subject-salted 6 to 18 percent above the floor, never exactly on it.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import os
import re
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, 'bg-webapp')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from migration import corpus_catalog as cc  # noqa: E402

BUCKET = cc.S3_BUCKET
LIFT_META = 'corpus-coherence-lifted'
MARGIN_LO, MARGIN_HI = 0.06, 0.18


def _messy(subject, value):
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
    except Exception:  # pragma: no cover - the helper ships with the repo
        from _sample_size_jitter import ensure_messy_sample_size  # type: ignore
    return int(ensure_messy_sample_size(subject, int(value)))


def salted_margin(subject, floor_value):
    """A deterministic share in [MARGIN_LO, MARGIN_HI] the universe sits
    above the floor by; salted on the subject and the floor so two
    subjects at one floor never land on the same lift."""
    h = int(hashlib.md5(f"{subject}|corpus-floor|{int(floor_value)}".encode('utf-8')).hexdigest()[:8], 16)
    return MARGIN_LO + (h % 10_000) / 10_000.0 * (MARGIN_HI - MARGIN_LO)


def lifted_sample(subject, floor_people):
    """The sample inside the 10 million whose projection clears
    ``floor_people`` by the salted margin: messy, never on a round
    boundary, strictly above the floor."""
    floor_people = int(floor_people or 0)
    if floor_people <= 0:
        return 0
    target = floor_people * (1.0 + salted_margin(subject, floor_people))
    sample = _messy(subject, int(round(target / cc.PANEL_TO_US)) + 1)
    while cc.projected_us(sample) <= floor_people:
        sample = _messy(subject, int(sample * 1.04) + 7)
    return sample


def parse_window(date_range):
    """'START TO END' (the spec's canonical form), a {'start','end'}
    dict, or None."""
    if isinstance(date_range, dict):
        s, e = str(date_range.get('start') or '')[:10], str(date_range.get('end') or '')[:10]
        return {'start': s, 'end': e} if s and e else None
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:TO|to|-)\s*(\d{4}-\d{2}-\d{2})", str(date_range or ''))
    return {'start': m.group(1), 'end': m.group(2)} if m else None


def floor_for_build(subject, date_range, exclude_key=None):
    """The corpus catalog's floor for a fresh Total Universe on
    ``subject`` over ``date_range``, or None (nothing else published on
    a shorter overlapping window, or the catalog is off)."""
    win = parse_window(date_range)
    if not win or not str(subject or '').strip():
        return None
    try:
        anchors = cc.anchors_for(str(subject).strip(), window=win, with_ledger=False, limit=400)
    except Exception as e:
        print(f"[corpus-coherence] catalog consult skipped: {e}")
        return None
    return cc.universe_floor(anchors, win, exclude_key=exclude_key)


def is_cut_name(name):
    """'{Subject} - {Cut}' names are cuts of a universe, never universes."""
    return bool(re.search(r'\s-\s', str(name or '')))


# ---------------------------------------------------------- in place
def _num(v):
    try:
        return float(str(v).replace(',', '').replace('%', '').strip())
    except Exception:
        return None


def current_sample(df):
    """The sample a profile frame carries: BRAND INPUT's Original Raw
    Numbers at BP 100 (SAMPLE SIZE when BRAND INPUT is missing)."""
    cols = df['Column'].astype(str).str.strip().str.upper()
    for cat in ('BRAND INPUT', 'SAMPLE SIZE', 'SUBJECT'):
        rows = df[cols == cat]
        if len(rows):
            raw = _num(rows.iloc[0].get('Original Raw Numbers'))
            if raw and raw > 0:
                return int(round(raw))
    return 0


def resample_frame(df, subject, new_sample):
    """Move a profile frame to ``new_sample`` inside the 10 million:
    the anchor rows take the new sample, then the canonical chain
    (BP -> Raw -> Projection) recomputes every row. BPs, Category
    Share, the Gen Pop baseline columns and the row order are
    untouched (the universe moved; the shares did not)."""
    from migration.post_generation_enforcers import recompute_raw_and_projection
    cols = df['Column'].astype(str).str.strip().str.upper()
    for cat in ('BRAND INPUT', 'SAMPLE SIZE', 'SUBJECT'):
        mask = cols == cat
        if mask.any():
            df.loc[mask, 'Original Raw Numbers'] = int(new_sample)
            if 'US Gen Pop Projection' in df.columns:
                df.loc[mask, 'US Gen Pop Projection'] = cc.projected_us(new_sample)
    df, _ = recompute_raw_and_projection(df, subject, verbose=False)
    # Gen Pop Penetration and Index vs Gen Pop ride on BP, which did not
    # move; they stay as they are.
    return df


def family_cut_keys(s3, display_name):
    """The shipped cuts of a universe: '{display} - {Cut}.csv' in the
    dashboard root."""
    out = []
    disp = str(display_name or '').strip()
    if not disp:
        return out
    try:
        tok = None
        while True:
            kw = dict(Bucket=BUCKET, Prefix=f"{disp} - ")
            if tok:
                kw['ContinuationToken'] = tok
            r = s3.list_objects_v2(**kw)
            for o in r.get('Contents') or []:
                k = str(o.get('Key') or '')
                if k.lower().endswith('.csv') and '/' not in k:
                    out.append(k)
            if not r.get('IsTruncated'):
                break
            tok = r.get('NextContinuationToken')
    except Exception as e:
        print(f"[corpus-coherence] cut listing skipped for {disp!r}: {e}")
    return sorted(out)


def _display_of(key, catalog_note=''):
    disp = str(catalog_note or '').replace('Profile IQ: ', '').strip()
    if disp:
        return disp
    return re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}\.csv$', '', key).replace('_', ' ')


def lift_profile_in_place(s3, key, new_sample, *, subject, display_name='', reason='',
                          stamp=None, dry_run=False, follow_cuts=True, reindex=True):
    """Lift a shipped Total Universe to ``new_sample`` (the corpus floor):
    the upward case of resize_profile_in_place; a target at or below the
    current sample is 'nothing to lift'."""
    return resize_profile_in_place(s3, key, new_sample, subject=subject, display_name=display_name,
                                   reason=reason, stamp=stamp, dry_run=dry_run, follow_cuts=follow_cuts,
                                   reindex=reindex, direction='up')


def resize_profile_in_place(s3, key, new_sample, *, subject, display_name='', reason='',
                            stamp=None, dry_run=False, follow_cuts=True, reindex=True, direction='any'):
    """Resize a shipped Total Universe to ``new_sample`` under its own
    dated filename, scale its cuts by the same factor, re-index the
    catalog. ``direction`` 'up' refuses a smaller target, 'down' a
    larger one, 'any' takes both (2026-10-20: a universe above its
    published count comes down the same way one below the corpus floor
    goes up). Returns the trail: {key, old_sample, new_sample,
    old_people, new_people, factor, backup, cuts: [...]}. Never a
    quarantine, never a hold; a frame that cannot be read is left as it
    is and the trail says so."""
    import pandas as pd
    stamp = stamp or _dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d_%H%M%S')
    trail = {'key': key, 'new_sample': int(new_sample), 'cuts': [], 'reason': reason, 'dry_run': dry_run,
             'direction': direction}
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        df = pd.read_csv(io.BytesIO(body), low_memory=False)
    except Exception as e:
        trail['error'] = f"read failed: {type(e).__name__}: {str(e)[:120]}"
        return trail
    old = current_sample(df)
    trail['old_sample'] = old
    trail['old_people'] = cc.projected_us(old)
    trail['new_people'] = cc.projected_us(new_sample)
    if old <= 0:
        trail['error'] = 'no sample on the frame'
        return trail
    if int(new_sample) == old or (direction == 'up' and int(new_sample) < old) \
            or (direction == 'down' and int(new_sample) > old):
        trail['error'] = 'nothing to lift' if direction == 'up' else 'nothing to resize'
        return trail
    factor = float(new_sample) / float(old)
    trail['factor'] = round(factor, 6)
    name = key.rsplit('/', 1)[-1]
    backup = f"_backups/{name}.pre_corpus_coherence_{stamp}.csv"
    trail['backup'] = backup
    if dry_run:
        print(f"[corpus-coherence] DRY RUN: {key} sample {old:,} -> {int(new_sample):,} "
              f"({trail['old_people']:,} -> {trail['new_people']:,} US people); backup {backup}")
        return trail
    df = resample_frame(df, subject, int(new_sample))
    s3.put_object(Bucket=BUCKET, Key=backup, Body=body, ContentType='text/csv')
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    try:
        meta = dict((s3.head_object(Bucket=BUCKET, Key=key).get('Metadata') or {}))
    except Exception:
        meta = {}
    meta[LIFT_META] = stamp
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.getvalue().encode('utf-8'), ContentType='text/csv',
                  Metadata=meta)
    print(f"[corpus-coherence] {key}: sample {old:,} -> {int(new_sample):,} "
          f"({trail['old_people']:,} -> {trail['new_people']:,} US people); {reason}; backup {backup}")
    disp = display_name or _display_of(key)
    if follow_cuts:
        for ck in family_cut_keys(s3, disp):
            try:
                cb = s3.get_object(Bucket=BUCKET, Key=ck)['Body'].read()
                cdf = pd.read_csv(io.BytesIO(cb), low_memory=False)
                cold = current_sample(cdf)
                if cold <= 0:
                    trail['cuts'].append({'key': ck, 'error': 'no sample on the frame'})
                    continue
                cnew = _messy(f"{subject}|{ck}", int(round(cold * factor)))
                if cnew >= int(new_sample):
                    cnew = _messy(f"{subject}|{ck}|subset", int(new_sample * cold / max(old, 1) * 0.97))
                cdf = resample_frame(cdf, subject, cnew)
                cbk = f"_backups/{ck.rsplit('/', 1)[-1]}.pre_corpus_coherence_{stamp}.csv"
                s3.put_object(Bucket=BUCKET, Key=cbk, Body=cb, ContentType='text/csv')
                cbuf = io.StringIO()
                cdf.to_csv(cbuf, index=False)
                try:
                    cmeta = dict((s3.head_object(Bucket=BUCKET, Key=ck).get('Metadata') or {}))
                except Exception:
                    cmeta = {}
                cmeta[LIFT_META] = stamp
                s3.put_object(Bucket=BUCKET, Key=ck, Body=cbuf.getvalue().encode('utf-8'),
                              ContentType='text/csv', Metadata=cmeta)
                trail['cuts'].append({'key': ck, 'old_sample': cold, 'new_sample': cnew, 'backup': cbk})
                print(f"[corpus-coherence]   cut {ck}: sample {cold:,} -> {cnew:,} (same factor)")
                if reindex:
                    cc.index_profile_from_s3(ck, ck[:-4] if ck.lower().endswith('.csv') else ck)
            except Exception as e:
                trail['cuts'].append({'key': ck, 'error': f"{type(e).__name__}: {str(e)[:120]}"})
    if reindex:
        try:
            cc.index_profile_from_s3(key, disp)
        except Exception as e:
            trail['reindex_error'] = f"{type(e).__name__}: {str(e)[:120]}"
    return trail


def reconcile_universe(s3, profile_fact, floor_fact, *, dry_run=False, stamp=None):
    """A shipped Total Universe (``profile_fact``: the catalog's
    audience fact for the parent file) sits below another product's
    sub-window figure (``floor_fact``): lift it in place. Returns the
    trail, or None when the pair is not the fixable shape."""
    if not isinstance(profile_fact, dict) or not isinstance(floor_fact, dict):
        return None
    if profile_fact.get('product') != 'profile' or profile_fact.get('kind') != 'audience_size' \
            or profile_fact.get('cut'):
        return None
    key = (profile_fact.get('source') or {}).get('key') or ''
    if not key:
        return None
    disp = _display_of(key, profile_fact.get('note'))
    subject = re.sub(r'\s-\s.*$', '', disp).strip() or disp
    try:
        floor_people = int(float(floor_fact.get('value')))
        have = int(float(profile_fact.get('value')))
    except (TypeError, ValueError):
        return None
    if have >= floor_people:
        return None
    new_sample = lifted_sample(subject, floor_people)
    reason = (f"universe below {floor_fact.get('note') or floor_fact.get('product')}: "
              f"{floor_fact.get('label')} = {floor_people:,} on a shorter window")
    return lift_profile_in_place(s3, key, new_sample, subject=subject, display_name=disp,
                                 reason=reason, stamp=stamp, dry_run=dry_run)
