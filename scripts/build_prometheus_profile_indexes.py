#!/usr/bin/env python3
"""Nightly per-profile Prometheus index tables + corpus digest warm.

Modeled on scripts/build_profile_norms.py. For every profile CSV in the
dashboard catalog this emits one compact JSON at
s3://dashboard-inputs/system/prometheus_profile_indexes/{sha1(s3_key)[:24]}.json
containing:

- the profile object's ETag (staleness key), sample size + projection
- per-Column category rollups: for every behavioral Column the top
  TOP_ROWS rows by penetration with [value, bp, index vs Gen Pop where
  Gen Pop carries the brand, raw, projection] plus the full row count
- the full demo block (all demographic categories, all buckets)
- a purchase-family index table (MOST PURCHASED BRANDS + its rule-3b
  mirror categories, merged by brand with indexes)
- the fully rendered profile digest text, stamped with the norms
  version, Gen Pop ETag, and a hash of the digest-rendering code, so
  prometheus_analysis.get_digest_bundle can serve it without
  downloading the CSV whenever every stamp still matches (any drift
  falls back to the live-CSV path)

The same pass warms the Prometheus corpus digest cache
(system/prometheus_corpus_digests/) for every catalog profile via
prometheus_corpus.warm_neighbor_digest, so the lazy path in
neighbor_digest almost never builds cold at ask time.

Re-runs are cheap: a profile whose current ETag (plus norms version,
Gen Pop ETag, and code hash) matches the stamps on its existing index
object is skipped without downloading the CSV. Individual file
failures are logged and never stop the run.

Run:  python3 bg-webapp/scripts/build_prometheus_profile_indexes.py
      [--dry-run] [--force] [--limit N] [--only KEY] [--workers N]
Refresh cadence: nightly 04:15 UTC on the build host (after the 03:30
norms build and the 04:00 gen pop sync); see
migration/systemd/prometheus-profile-indexes.timer.
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor

_BG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BG_DIR not in sys.path:
    sys.path.insert(0, _BG_DIR)

BUCKET = 'dashboard-inputs'
CATALOG_KEY = 'system/s3_cache.json'
TOP_ROWS = 25
PURCHASE_TOP = 40
DEFAULT_WORKERS = 12

# Purchase family per profile-iq-pipeline-rules 3b: MPB is the source
# of truth and these categories mirror it brand-for-brand.
PURCHASE_FAMILY = {
    'MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS',
    'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TOYS', 'TECHNOLOGY BRAND',
    'TECHNOLOGY/DEVICE', 'HEAVY MACHINERY', 'WHERE THEY SHOP',
}


def _int_or_none(v):
    try:
        s = str(v).replace(',', '').strip()
        if not s:
            return None
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _is_missing_err(e):
    try:
        code = str(((getattr(e, 'response', None) or {})
                    .get('Error') or {}).get('Code') or '')
    except Exception:
        code = ''
    return code in ('404', 'NoSuchKey', 'NotFound')


def list_profile_keys(s3):
    """Root-level profile CSVs (skips prefixed keys like _backups/,
    system/, reports/ via the '/' filter, plus Gen Pop and anything
    carrying a backup token in the name)."""
    keys = []
    token = None
    while True:
        kw = dict(Bucket=BUCKET)
        if token:
            kw['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get('Contents', []):
            k = obj['Key']
            if not k.lower().endswith('.csv') or '/' in k:
                continue
            if k.startswith('Gen_Pop') or '_backups' in k.lower():
                continue
            keys.append(k)
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')
    return keys


def load_catalog_names(s3):
    """{s3_key: display_name} from the dashboard catalog. The display
    name matters: the runtime digest hook only trusts an index whose
    stored name matches the page context's name, and the page context
    carries the catalog display name."""
    try:
        body = s3.get_object(Bucket=BUCKET, Key=CATALOG_KEY)['Body'].read()
        jobs = json.loads(body).get('jobs') or []
        return {j.get('s3_key'): str(j.get('display_name')
                                     or j.get('name') or '').strip()
                for j in jobs if j.get('s3_key')}
    except Exception as e:
        print(f"catalog names unavailable ({e}); using key stems")
        return {}


def _name_from_key(key):
    stem = key.rsplit('.csv', 1)[0]
    stem = re.sub(r'_[0-9]{2}_[0-9]{2}_[0-9]{4}_[0-9]{2}_[0-9]{2}$', '', stem)
    return stem.replace('_', ' ').strip()


def _stamps(profile_etag, ctx):
    """S3 object metadata stamped on each index (lowercase keys: S3
    returns user metadata keys lowercased). The skip check and the
    runtime freshness gate both key off these."""
    return {
        'profile-etag': profile_etag or 'none',
        'norms-ver': ctx.get('norms_ver') or 'none',
        'genpop-etag': ctx.get('genpop_etag') or 'none',
        'code-ver': ctx.get('code_ver') or 'none',
    }


def _index_fresh(s3, out_key, stamps):
    try:
        head = s3.head_object(Bucket=BUCKET, Key=out_key)
        md = {str(k).lower(): v for k, v in
              (head.get('Metadata') or {}).items()}
        return all(md.get(k) == v for k, v in stamps.items())
    except Exception:
        return False


def build_index_doc(df, meta, name, s3_key, etag, ctx, digest_text):
    """The index JSON document for one profile (see module docstring
    for the field inventory)."""
    import prometheus_analysis as pma
    bp_c = meta.get('bp_col') or pma._bp_col(df)
    raw_c = pma._fuzzy_col(df, 'raw')
    proj_c = pma._fuzzy_col(df, 'proj')
    genpop = ctx['genpop']

    acc = {}
    for cat, grp in df.groupby('Column', sort=False):
        catU = pma._norm_cat(cat)
        if not catU or catU in pma.METADATA_COLS:
            continue
        vv = grp['Value'].tolist()
        bb = grp[bp_c].tolist()
        rr = grp[raw_c].tolist() if raw_c is not None else [None] * len(vv)
        pp = grp[proj_c].tolist() if proj_c is not None else [None] * len(vv)
        rows = acc.setdefault(catU, [])
        for label, bpv, raw, proj in zip(vv, bb, rr, pp):
            v = pma._parse_bp(bpv)
            label = str(label or '').strip()
            if v is None or not label:
                continue
            rows.append((label, v, _int_or_none(raw), _int_or_none(proj)))

    demos, cats = {}, {}
    purchase = {}
    for catU, rows in acc.items():
        if not rows:
            continue
        rows.sort(key=lambda r: -r[1])
        if catU in pma.DEMO_COLS:
            demos[catU] = [[b, round(v, 4), raw, proj]
                           for b, v, raw, proj in rows]
            continue
        top = []
        for b, v, raw, proj in rows[:TOP_ROWS]:
            gp = genpop.get((catU, pma._norm_brand(b)))
            idx = round(v / gp * 100) if gp and gp >= 0.01 else None
            top.append([b, round(v, 4), idx, raw, proj])
        cats[catU] = {'n_rows': len(rows), 'rows': top}
        if catU in PURCHASE_FAMILY:
            for b, v, raw, proj in rows:
                bn = pma._norm_brand(b)
                if not bn:
                    continue
                cur = purchase.get(bn)
                if cur is None:
                    purchase[bn] = [b, v, raw, proj, [catU]]
                else:
                    if catU not in cur[4]:
                        cur[4].append(catU)
                    if v > cur[1]:
                        cur[0], cur[1], cur[2], cur[3] = b, v, raw, proj

    purchase_rows = []
    for bn, (b, v, raw, proj, in_cats) in sorted(
            purchase.items(), key=lambda kv: -kv[1][1])[:PURCHASE_TOP]:
        gp = genpop.get(('MOST PURCHASED BRANDS', bn))
        if not gp:
            for c in in_cats:
                gp = genpop.get((c, bn))
                if gp:
                    break
        idx = round(v / gp * 100) if gp and gp >= 0.01 else None
        purchase_rows.append([b, round(v, 4), idx, raw, proj, in_cats])

    return {
        'version': 1,
        's3_key': s3_key,
        'etag': etag,
        'built_at': time.time(),
        'name': meta.get('name') or name,
        'meta': meta,
        'sample': meta.get('sample'),
        'projection': meta.get('proj'),
        'row_schema': {
            'demos': '[bucket, bp, raw, projection]',
            'categories': '[value, bp, index_vs_genpop_or_null, raw, '
                          'projection]',
            'purchase_index': '[brand, bp, index_vs_genpop_or_null, raw, '
                              'projection, [categories]]',
        },
        'demos': demos,
        'categories': cats,
        'purchase_index': purchase_rows,
        'digest': {
            'text': digest_text,
            'norms_ver': ctx.get('norms_ver') or '',
            'genpop_etag': ctx.get('genpop_etag') or '',
            'code_ver': ctx.get('code_ver') or '',
        },
    }


def build_one_profile(s3, key, name, ctx):
    """Index build + corpus digest warm for one profile. Returns
    (status, nbytes, warm) where status is 'built' | 'fresh' |
    'missing' and warm is prometheus_corpus.warm_neighbor_digest's
    verdict. Raises on unexpected trouble (caller logs + continues)."""
    import prometheus_analysis as pma
    import prometheus_corpus as pmc
    out_key = pma.profile_index_s3_key(key)
    try:
        head = s3.head_object(Bucket=BUCKET, Key=key)
    except Exception as e:
        if _is_missing_err(e):
            return 'missing', 0, 'skipped'
        raise
    etag = (head.get('ETag') or '').strip('"')
    entry = {'s3_key': key, 'display_name': name}
    if not ctx.get('force') and _index_fresh(s3, out_key,
                                             _stamps(etag, ctx)):
        warm = ('skipped' if ctx.get('dry_run')
                else pmc.warm_neighbor_digest(s3, BUCKET, entry))
        return 'fresh', 0, warm
    df, live_etag = pma.load_profile_df(s3, BUCKET, key)
    etag = live_etag or etag
    meta = pma._profile_meta(df, name)
    digest_text = pma.build_profile_digest(df, meta, ctx['genpop'],
                                           norms=ctx['norms'])
    doc = build_index_doc(df, meta, name, key, etag, ctx, digest_text)
    blob = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    if not ctx.get('dry_run'):
        s3.put_object(Bucket=BUCKET, Key=out_key, Body=blob,
                      ContentType='application/json',
                      Metadata=_stamps(etag, ctx))
    warm = ('skipped' if ctx.get('dry_run')
            else pmc.warm_neighbor_digest(s3, BUCKET, entry))
    return 'built', len(blob), warm


def _make_ctx(s3, dry_run=False, force=False):
    import prometheus_analysis as pma
    genpop = pma.load_genpop_map(s3, BUCKET)
    norms = pma.load_norms(s3, BUCKET)
    return {
        'genpop': genpop,
        'norms': norms,
        'norms_ver': (norms or {}).get('built_at') or '',
        'genpop_etag': pma._genpop_current_etag() or '',
        'code_ver': pma.digest_code_version(),
        'dry_run': dry_run,
        'force': force,
    }


# --- process-pool plumbing (one boto3 client + genpop/norms load per
# worker; tasks are (key, name) tuples) ---------------------------------
_W = {}


def _init_worker(dry_run, force):
    import boto3
    s3 = boto3.client('s3')
    _W['s3'] = s3
    _W['ctx'] = _make_ctx(s3, dry_run=dry_run, force=force)


def _worker_one(task):
    key, name = task
    t0 = time.time()
    try:
        status, nbytes, warm = build_one_profile(_W['s3'], key, name,
                                                 _W['ctx'])
        return key, status, nbytes, warm, time.time() - t0, ''
    except Exception as e:
        return (key, 'failed', 0, 'failed', time.time() - t0,
                f'{type(e).__name__}: {e}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dry-run', action='store_true',
                    help='build but do not upload or warm')
    ap.add_argument('--force', action='store_true',
                    help='rebuild even when the existing index is fresh')
    ap.add_argument('--limit', type=int, default=0,
                    help='only the first N profiles (debugging)')
    ap.add_argument('--only', action='append', default=[],
                    help='only this s3 key (repeatable)')
    ap.add_argument('--workers', type=int,
                    default=int(os.environ.get('PROM_INDEX_WORKERS',
                                               DEFAULT_WORKERS)))
    args = ap.parse_args(argv)

    import boto3
    t0 = time.time()
    s3 = boto3.client('s3')
    keys = list_profile_keys(s3)
    names = load_catalog_names(s3)
    if args.only:
        only = set(args.only)
        keys = [k for k in keys if k in only]
    if args.limit:
        keys = keys[:args.limit]
    tasks = [(k, names.get(k) or _name_from_key(k)) for k in sorted(keys)]
    print(f"profiles to index: {len(tasks)} "
          f"(workers={args.workers}, dry_run={args.dry_run}, "
          f"force={args.force})")

    counts = {'built': 0, 'fresh': 0, 'missing': 0, 'failed': 0}
    warm_counts = {}
    total_bytes = 0
    failures = []
    done = 0

    def _ingest(res):
        nonlocal total_bytes, done
        key, status, nbytes, warm, secs, err = res
        counts[status] = counts.get(status, 0) + 1
        warm_counts[warm] = warm_counts.get(warm, 0) + 1
        total_bytes += nbytes
        done += 1
        if status == 'failed':
            failures.append((key, err))
            print(f"  FAIL {key}: {err}")
        elif status == 'missing':
            print(f"  MISSING {key} (catalog points at a deleted object)")
        if done % 100 == 0:
            print(f"  ...{done}/{len(tasks)} "
                  f"(built {counts['built']}, fresh {counts['fresh']}, "
                  f"{time.time() - t0:.0f}s)")

    if args.workers <= 1:
        ctx = _make_ctx(s3, dry_run=args.dry_run, force=args.force)
        for task in tasks:
            key, name = task
            ts = time.time()
            try:
                status, nbytes, warm = build_one_profile(s3, key, name, ctx)
                _ingest((key, status, nbytes, warm, time.time() - ts, ''))
            except Exception as e:
                _ingest((key, 'failed', 0, 'failed', time.time() - ts,
                         f'{type(e).__name__}: {e}'))
    else:
        with ProcessPoolExecutor(
                max_workers=args.workers, initializer=_init_worker,
                initargs=(args.dry_run, args.force)) as pool:
            for res in pool.map(_worker_one, tasks, chunksize=8):
                _ingest(res)

    wall = time.time() - t0
    print(f"done in {wall:.0f}s: {counts['built']} built, "
          f"{counts['fresh']} fresh-skipped, {counts['missing']} missing, "
          f"{counts['failed']} failed; "
          f"{total_bytes / 1e6:.1f} MB uploaded")
    print("corpus digest warm: " + ', '.join(
        f"{k} {v}" for k, v in sorted(warm_counts.items())))
    if failures:
        print(f"failures ({len(failures)}):")
        for key, err in failures[:50]:
            print(f"  {key}: {err}")
    ok = counts['built'] + counts['fresh']
    return 0 if (ok > 0 or not tasks) else 1


if __name__ == '__main__':
    sys.exit(main())
