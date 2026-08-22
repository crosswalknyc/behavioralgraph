#!/usr/bin/env python3
"""Build cross-profile brand norms, grouped by BRAND CATEGORY.

Jenna directive 2026-08-21: norms are computed per the BRAND CATEGORY
metadata value in each profile CSV (ACTOR, MUSICIAN/BAND, SERIES - *,
QSR, ...), so Prometheus can say "idx 412 is the highest we have
measured across 34 ACTOR audiences" instead of comparing against a
mixed pool. A global '*' group is kept as the fallback for subject
categories with too few profiles.

Scans every profile CSV at the root of s3://dashboard-inputs/, skips
backups/system/metadata prefixes and Gen Pop, and aggregates into a
nested table norms[group][category][brand_norm] =

    [n, med_pen, p90_pen, max_pen, med_idx, p90_idx, max_idx, max_profile]

idx aggregates are only present where Gen Pop carries the brand.
Entries with n < 5 are dropped (n < 8 for the '*' global pool) because
the lookup in prometheus_analysis requires n >= 5 anyway; nesting +
the n floor keeps the in-memory footprint web-service safe (~4,182
profiles produce ~15 MB gz flat vs a much lighter nested table).
Output is gzipped JSON at s3://dashboard-inputs/system/profile_norms.json.gz,
which prometheus_analysis.load_norms() consumes with an ETag-checked
cache.

Run:  python3 scripts/build_profile_norms.py [--dry-run]
Refresh cadence: daily systemd timer on Hetzner
(systemd/profile-norms-refresh.timer in this repo).
"""

import gzip
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median

import boto3
import pandas as pd

BUCKET = 'dashboard-inputs'
OUT_KEY = 'system/profile_norms.json.gz'
GENPOP_KEY = 'Gen_Pop_2026.csv'
MIN_N = 5
MIN_N_GLOBAL = 8
SKIP_PREFIXES = ('_backups/', 'system/', 'metadata/', 'reports/',
                 'decks/', 'briefs/')

METADATA_COLS = {
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT',
    'INPUT_METADATA', 'INPUT METADATA',
}


def _norm_cat(c):
    return re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())


def _norm_brand(b):
    return re.sub(r'[^a-z0-9]+', '', str(b or '').lower())


def _bp_col(df):
    for c in df.columns:
        if 'penetration' in str(c).lower():
            return c
    return None


def _parse_bp(v):
    try:
        return float(str(v).replace('%', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def list_profile_keys(s3):
    keys = []
    token = None
    while True:
        kw = dict(Bucket=BUCKET)
        if token:
            kw['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get('Contents', []):
            k = obj['Key']
            if not k.lower().endswith('.csv'):
                continue
            if any(k.startswith(p) for p in SKIP_PREFIXES) or '/' in k:
                continue
            if k.startswith('Gen_Pop'):
                continue
            keys.append(k)
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')
    return keys


def load_genpop(s3):
    body = s3.get_object(Bucket=BUCKET, Key=GENPOP_KEY)['Body'].read()
    df = pd.read_csv(io.BytesIO(body)).fillna('')
    bp = _bp_col(df)
    gp = {}
    for _, row in df.iterrows():
        cat = _norm_cat(row.get('Column'))
        if cat in METADATA_COLS:
            continue
        v = _parse_bp(row.get(bp))
        if v is not None and v >= 0.01:
            gp[(cat, _norm_brand(row.get('Value')))] = v
    return gp


def extract_rows(s3, key):
    """One profile -> (subject_group, profile_name, [(cat, brand, pen)])."""
    body = s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
    df = pd.read_csv(io.BytesIO(body), dtype=str).fillna('')
    if 'Column' not in df.columns or 'Value' not in df.columns:
        return None
    bp = _bp_col(df)
    if bp is None:
        return None
    group, name = 'UNKNOWN', key.rsplit('.csv', 1)[0]
    rows = []
    for _, row in df.iterrows():
        cat = _norm_cat(row.get('Column'))
        val = str(row.get('Value') or '').strip()
        if cat == 'BRAND CATEGORY' and val:
            group = _norm_cat(val)
            continue
        if cat == 'SUBJECT' and val:
            name = val
            continue
        if cat in METADATA_COLS:
            continue
        v = _parse_bp(row.get(bp))
        if v is None or v >= 99.99 or not val:
            continue
        rows.append((cat, val, v))
    return group, name, rows


def p90(sorted_vals):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(round(0.9 * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def main(dry_run=False):
    t0 = time.time()
    s3 = boto3.client('s3')
    keys = list_profile_keys(s3)
    print(f"profiles to scan: {len(keys)}")
    genpop = load_genpop(s3)
    print(f"gen pop brands: {len(genpop):,}")

    # (group, cat, brand_norm) -> list of (pen, profile_name)
    obs = {}
    group_counts = {}
    ok = fail = 0

    def _ingest(res):
        nonlocal ok
        group, name, rows = res
        for g in (group, '*'):
            group_counts[g] = group_counts.get(g, 0) + 1
        for cat, val, v in rows:
            bn = _norm_brand(val)
            if not bn:
                continue
            for g in (group, '*'):
                obs.setdefault((g, cat, bn), []).append((v, name))
        ok += 1

    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(extract_rows, s3, k): k for k in keys}
        for fut in as_completed(futs):
            try:
                res = fut.result()
                if res is None:
                    fail += 1
                    continue
                _ingest(res)
            except Exception as e:
                fail += 1
                print(f"  skip {futs[fut]}: {e}")
            if (ok + fail) % 50 == 0:
                print(f"  ...{ok + fail}/{len(keys)}")

    print(f"parsed {ok} profiles ({fail} skipped), "
          f"{len(obs):,} raw (group,cat,brand) buckets")

    norms = {}
    kept = 0
    for (g, cat, bn), vals in obs.items():
        floor = MIN_N_GLOBAL if g == '*' else MIN_N
        if len(vals) < floor:
            continue
        pens = sorted(v for v, _ in vals)
        max_pen, max_prof = max(vals, key=lambda t: t[0])
        gp = genpop.get((cat, bn))
        med_i = p90_i = max_i = None
        if gp:
            med_i = round(median(pens) / gp * 100)
            p90_i = round(p90(pens) / gp * 100)
            max_i = round(max_pen / gp * 100)
        norms.setdefault(g, {}).setdefault(cat, {})[bn] = [
            len(pens), round(median(pens), 2), round(p90(pens), 2),
            round(max_pen, 2), med_i, p90_i, max_i, max_prof[:40]]
        kept += 1

    payload = {
        'built_at': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
        'n_profiles': ok,
        'min_n': MIN_N,
        'groups': group_counts,
        'norms': norms,
    }
    blob = gzip.compress(json.dumps(payload, separators=(',', ':'))
                         .encode('utf-8'))
    print(f"norm entries kept (n>={MIN_N}/{MIN_N_GLOBAL}*): {kept:,}; "
          f"gz size {len(blob) / 1e6:.1f} MB; "
          f"groups: {sorted((g, n) for g, n in group_counts.items() if n >= MIN_N)}")
    if dry_run:
        print("dry run; not uploading")
        return
    s3.put_object(Bucket=BUCKET, Key=OUT_KEY, Body=blob,
                  ContentType='application/json',
                  ContentEncoding='gzip')
    print(f"uploaded s3://{BUCKET}/{OUT_KEY} in {time.time() - t0:.0f}s")


if __name__ == '__main__':
    main(dry_run='--dry-run' in sys.argv)
