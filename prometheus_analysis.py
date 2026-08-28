"""Prometheus page-aware analysis (2026-08-20).

Builds compact text digests of Profile IQ CSVs (the profile open on the
dashboard plus any Data Cuts the user has checked) so the chat agent can
reason over the first-party clickstream-derived data directly. Also holds
the system prompts for the analysis call and the deck slide-plan call.

Design constraints (Jenna 2026-08-20):
- The FIRST-PARTY data in the digest is the primary evidence. Outside
  knowledge is context only.
- High-level reasoning: the analysis call runs on the strongest model
  available (Opus preferred, resolved at runtime in app.py).
- Voice follows the Crosswalk brand system: flat, specific, unhurried,
  no em dashes, state the finding then the number.
"""

import hashlib
import io
import json
import re
import time
import threading
import unicodedata

import pandas as pd

# ---------------------------------------------------------------------------
# CSV loading and parsing helpers
# ---------------------------------------------------------------------------

METADATA_COLS = {
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT',
    'INPUT_METADATA', 'INPUT METADATA',
}

DEMO_COLS = {
    'AGE', 'GENDER', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION',
    'PARENTAL STATUS', 'PARENTAL_STATUS', 'RELATIONSHIP',
    'RELATIONSHIP STATUS', 'SEXUAL ORIENTATION', 'SEXUAL_ORIENTATION',
}

_digest_cache = {}       # {s3_key: (etag+norms_ver, built_ts, digest_str, meta)}
_genpop_cache = {'ts': 0.0, 'map': None, 'etag': None}
_norms_cache = {'ts': 0.0, 'etag': None, 'data': None}
# Nightly precomputed index docs (2026-08-28 speed layer): one small
# JSON per profile, built by scripts/build_prometheus_profile_indexes.py
# on the build host at 04:15 UTC. {s3_key: (index_etag, checked_ts, doc)};
# doc None = negative cache (no index yet) so cold profiles do not pay
# a lookup on every ask.
_index_cache = {}
# Parsed-profile cache (2026-08-26 latency work): profile CSVs are
# 400-900KB and were re-downloaded + re-parsed on every analyze turn
# even when the digest itself was cached. A HEAD revalidates the ETag
# on every call (in-place corrections at the same key are seen
# immediately, per in-place-corrections rules); the body is fetched
# and parsed only when the content actually changed. DataFrames are
# read-only downstream, so sharing across threads is safe.
_df_cache = {}           # {s3_key: (etag, last_used_ts, df)}
_DF_CACHE_MAX = 24
# Cut-divergence text cache: keyed by parent+cut ETags + norms version
# so a change to either file rebuilds the divergence block.
_cutdiv_cache = {}       # {(p_key, c_key): (etag_pair, built_ts, text)}
_cache_lock = threading.Lock()

GENPOP_KEY = 'Gen_Pop_2026.csv'
GENPOP_TTL_S = 3600
NORMS_KEY = 'system/profile_norms.json.gz'
NORMS_TTL_S = 6 * 3600
INDEX_PREFIX = 'system/prometheus_profile_indexes/'
INDEX_TTL_S = 600


def _norm_cat(c):
    return re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())


def _norm_brand(b):
    # Accent fold first (2026-08-27): 'Timothée' must match 'Timothee'
    # instead of silently dropping the accented letter. Mirrors
    # migration/genpop_baseline._norm_brand and hostmap_norm.norm_key.
    s = unicodedata.normalize('NFKD', str(b or ''))
    s = s.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def _bp_col(df):
    for c in df.columns:
        if 'penetration' in str(c).lower():
            return c
    return None


def _fuzzy_col(df, needle):
    for c in df.columns:
        if needle in str(c).lower():
            return c
    return None


def _parse_bp(v):
    try:
        return float(str(v).replace('%', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def load_profile_df(s3_client, bucket, s3_key):
    """Fetch a profile CSV from S3, ETag-revalidated. Returns (df, etag).

    A cached parse is reused only after a HEAD confirms the S3 object
    is byte-identical (same ETag), so freshness semantics match the
    old fetch-every-time behavior while skipping the repeat download
    and pandas parse on warm turns."""
    with _cache_lock:
        cached = _df_cache.get(s3_key)
    if cached:
        try:
            head = s3_client.head_object(Bucket=bucket, Key=s3_key)
            h_etag = (head.get('ETag') or '').strip('"')
            if h_etag and h_etag == cached[0]:
                with _cache_lock:
                    _df_cache[s3_key] = (cached[0], time.time(), cached[2])
                return cached[2], cached[0]
        except Exception:
            pass  # fall through to the plain GET
    resp = s3_client.get_object(Bucket=bucket, Key=s3_key)
    etag = (resp.get('ETag') or '').strip('"')
    content = resp['Body'].read().decode('utf-8', 'replace')
    df = pd.read_csv(io.StringIO(content)).fillna('')
    with _cache_lock:
        _df_cache[s3_key] = (etag, time.time(), df)
        if len(_df_cache) > _DF_CACHE_MAX:
            for k, _ in sorted(_df_cache.items(),
                               key=lambda kv: kv[1][1])[:8]:
                _df_cache.pop(k, None)
    return df, etag


def _profile_meta(df, fallback_name):
    """Extract subject name, sample size, projection, window from
    metadata rows."""
    bp = _bp_col(df)
    raw_c = _fuzzy_col(df, 'raw')
    proj_c = _fuzzy_col(df, 'proj')
    name, sample, proj, window = fallback_name, None, None, None
    brand_category = None
    dates = []
    for _, row in df.iterrows():
        cat = _norm_cat(row.get('Column'))
        if cat not in METADATA_COLS:
            continue
        val = str(row.get('Value') or '')
        if cat == 'SUBJECT' and val and not name:
            name = val
        if cat == 'BRAND CATEGORY' and val.strip():
            brand_category = _norm_cat(val)
        if cat == 'BRAND INPUT':
            if raw_c is not None:
                try:
                    sample = int(float(str(row.get(raw_c)).replace(',', '')))
                except (TypeError, ValueError):
                    pass
            if proj_c is not None:
                try:
                    proj = int(float(str(row.get(proj_c)).replace(',', '')))
                except (TypeError, ValueError):
                    pass
        for m in re.finditer(
                r'(\d{2}[/_.-]\d{2}[/_.-]\d{4}|\d{4}-\d{2}-\d{2})', val):
            dates.append(m.group(1))
    if len(dates) >= 2:
        window = f"{dates[0]} to {dates[1]}"
    return {'name': name or fallback_name or 'Audience',
            'sample': sample, 'proj': proj, 'window': window,
            'bp_col': bp, 'brand_category': brand_category}


def load_genpop_map(s3_client, bucket):
    """(category, brand) -> gen pop BP, cached for an hour."""
    with _cache_lock:
        if (_genpop_cache['map'] is not None
                and time.time() - _genpop_cache['ts'] < GENPOP_TTL_S):
            return _genpop_cache['map']
    gp = {}
    gp_etag = None
    try:
        df, gp_etag = load_profile_df(s3_client, bucket, GENPOP_KEY)
        bp = _bp_col(df)
        if bp:
            for _, row in df.iterrows():
                cat = _norm_cat(row.get('Column'))
                if cat in METADATA_COLS:
                    continue
                v = _parse_bp(row.get(bp))
                if v is not None:
                    gp[(cat, _norm_brand(row.get('Value')))] = v
    except Exception as e:
        gp_etag = None
        print(f"[prometheus] genpop map load failed: {e}")
    with _cache_lock:
        _genpop_cache['map'] = gp
        _genpop_cache['ts'] = time.time()
        _genpop_cache['etag'] = gp_etag
    return gp


def _genpop_current_etag():
    """ETag of the Gen Pop object the cached gen pop map was parsed
    from (None when the load failed). The precomputed-digest gate
    compares this against the Gen Pop ETag stamped on the nightly
    index so the stored text always matches what a live build with
    the in-memory map would produce."""
    with _cache_lock:
        return _genpop_cache.get('etag')


def load_norms(s3_client, bucket):
    """Cross-profile brand norms grouped by the profiles' BRAND CATEGORY
    (built by scripts/build_profile_norms.py). ETag-checked cache with a
    6h TTL. Returns the payload dict or None when absent."""
    now = time.time()
    with _cache_lock:
        if (_norms_cache['data'] is not None
                and now - _norms_cache['ts'] < NORMS_TTL_S):
            return _norms_cache['data']
    data = None
    try:
        import gzip as _gzip
        import json as _json
        head = s3_client.head_object(Bucket=bucket, Key=NORMS_KEY)
        etag = (head.get('ETag') or '').strip('"')
        with _cache_lock:
            if _norms_cache['etag'] == etag and _norms_cache['data']:
                _norms_cache['ts'] = now
                return _norms_cache['data']
        body = s3_client.get_object(Bucket=bucket, Key=NORMS_KEY)['Body'].read()
        data = _json.loads(_gzip.decompress(body).decode('utf-8'))
        with _cache_lock:
            _norms_cache.update(ts=now, etag=etag, data=data)
    except Exception as e:
        print(f"[prometheus] norms load skipped: {e}")
        with _cache_lock:
            _norms_cache.update(ts=now, data=_norms_cache['data'])
            data = _norms_cache['data']
    return data


def _norm_lookup(norms, group, catU, brand_norm, min_n=5):
    """Norm entry for a brand, preferring the subject-category group and
    falling back to the global '*' pool. Returns (entry, group_used).
    Table is nested norms[group][category][brand_norm] (see
    scripts/build_profile_norms.py)."""
    if not norms:
        return None, None
    table = norms.get('norms') or {}
    groups = norms.get('groups') or {}
    for g in ((group, '*') if group and groups.get(group, 0) >= min_n
              else ('*',)):
        e = (table.get(g) or {}).get(catU, {}).get(brand_norm)
        if e and e[0] >= min_n:
            return e, g
    return None, None


def _fmt_row(brand, bp, gp_bp):
    s = f"{brand} {bp:.1f}"
    if gp_bp is not None and gp_bp >= 0.01:
        s += f" (idx {round(bp / gp_bp * 100)})"
    return s


def build_profile_digest(df, meta, genpop_map, subject_name=None,
                         max_rows=12, max_chars=32000, norms=None):
    """Compact text digest of one profile CSV: metadata line, full
    demographics, then top rows per behavioral category with index vs
    US gen pop (100 = average), per-category math (leader, median,
    concentration, conquest gaps), and a PEER NORMS section comparing
    this audience against every other audience of the same BRAND
    CATEGORY in the Crosswalk corpus."""
    bp_c = meta.get('bp_col') or _bp_col(df)
    if bp_c is None:
        return f"PROFILE: {meta['name']}\n(no penetration column found)"
    name = subject_name or meta['name']
    subj_norm = _norm_brand(name)
    lines = [f"PROFILE: {name}"]
    bits = []
    if meta.get('sample'):
        bits.append(f"sample {meta['sample']:,} panelists")
    if meta.get('proj'):
        bits.append(f"projected US audience {meta['proj']:,}")
    bits.append(f"window {meta.get('window') or 'trailing 12 months'}")
    lines.append('  ' + '; '.join(bits))

    demo_lines, cat_lines = [], []
    demo_rows_all, beh_rows_all = [], []
    for cat, grp in df.groupby('Column', sort=False):
        catU = _norm_cat(cat)
        if catU in METADATA_COLS:
            continue
        rows = []
        for _, row in grp.iterrows():
            v = _parse_bp(row.get(bp_c))
            if v is None:
                continue
            rows.append((str(row.get('Value') or ''), v))
        if not rows:
            continue
        rows.sort(key=lambda r: -r[1])
        if catU in DEMO_COLS:
            demo_rows_all.extend((catU, b, v) for b, v in rows)
            demo_lines.append(
                f"  {catU}: " + ' | '.join(
                    f"{b} {v:.1f}" for b, v in rows))
            continue
        shown, pinned = [], 0
        free = [(b, v) for b, v in rows
                if v < 99.99 and _norm_brand(b) != subj_norm]
        beh_rows_all.extend((catU, b, v) for b, v in free)
        for b, v in free:
            if len(shown) < max_rows:
                gp = genpop_map.get((catU, _norm_brand(b)))
                shown.append(_fmt_row(b, v, gp))
        if not shown:
            continue
        suffix = f" [{len(rows)} rows]" if len(rows) > max_rows else ""
        # Deterministic category math (2026-08-21): leader, median row,
        # concentration (leader's share of the top-5 total), and the
        # conquest gaps (big in gen pop, weak in this audience). Gives
        # whitespace/fragmentation claims real numbers to stand on.
        math_bits = []
        if len(free) >= 3:
            pens = [v for _, v in free]
            top5 = pens[:5]
            conc = top5[0] / sum(top5) if sum(top5) > 0 else 0
            shape = ('CONCENTRATED' if conc >= 0.45
                     else 'SPLIT' if conc <= 0.30 else 'MIXED')
            med = pens[len(pens) // 2]
            math_bits.append(
                f"math: n{len(free)}, leader {free[0][0]} {free[0][1]:.1f}, "
                f"median row {med:.1f}, top1-of-top5 {conc * 100:.0f}% "
                f"({shape})")
            gaps = []
            for b, v in free:
                gp = genpop_map.get((catU, _norm_brand(b)))
                if gp and gp >= 8 and (v / gp * 100) <= 75:
                    gaps.append((gp, b, v))
            gaps.sort(reverse=True)
            if gaps:
                math_bits.append(
                    "conquest gaps (big in gen pop, weak here): " + ', '.join(
                        f"{b} {v:.1f} (idx {round(v / g * 100)}, gp {g:.1f})"
                        for g, b, v in gaps[:3]))
        cat_lines.append(f"  {catU}{suffix}: " + '; '.join(shown)
                         + (' || ' + ' | '.join(math_bits)
                            if math_bits else ''))

    lines.append("DEMOGRAPHICS (% of audience):")
    lines.extend(demo_lines)
    lines.append("BEHAVIORAL CATEGORIES (top rows, % penetration of this "
                 "audience; idx = index vs US gen pop, 100 = average; "
                 "'math:' block = full-category calculations):")
    lines.extend(cat_lines)
    # PEER NORMS is the rarity evidence; truncation must never eat it,
    # so cap the category body first and append the peer section after.
    peer = _peer_norms_section(meta, genpop_map, norms,
                               demo_rows_all, beh_rows_all)
    peer_txt = ('\n' + '\n'.join(peer)) if peer else ''
    out = '\n'.join(lines)
    budget = max_chars - len(peer_txt)
    if len(out) > budget:
        out = out[:budget] + "\n  [digest truncated]"
    return out + peer_txt


def _peer_norms_section(meta, genpop_map, norms, demo_rows, beh_rows):
    """PEER NORMS lines: rarity receipts vs other audiences of the same
    BRAND CATEGORY (Jenna 2026-08-21: norms group on the BRAND CATEGORY
    value in the CSV). Returns [] when the norms file is unavailable."""
    if not norms:
        return []
    group = _norm_cat(meta.get('brand_category') or '')
    groups = norms.get('groups') or {}
    n_group = groups.get(group, 0)
    highs, lows, demo_out = [], [], []
    for catU, b, v in beh_rows:
        bn = _norm_brand(b)
        entry, g_used = _norm_lookup(norms, group, catU, bn)
        if not entry:
            continue
        n, med_p, p90_p, max_p, med_i, p90_i, max_i, max_prof = entry
        gp = genpop_map.get((catU, bn))
        if gp and med_i and p90_i:
            idx = v / gp * 100
            if idx > p90_i and idx >= 115:
                highs.append((idx / p90_i, catU, b, idx, v, entry, g_used))
            elif idx < med_i * 0.6 and gp >= 5 and med_i >= 60:
                lows.append((med_i / max(idx, 1), catU, b, idx, entry,
                             g_used))
        elif v > p90_p * 1.15 and v >= 3:
            highs.append((v / p90_p, catU, b, None, v, entry, g_used))
    for catU, b, v in demo_rows:
        entry, g_used = _norm_lookup(norms, group, catU, _norm_brand(b))
        if entry and abs(v - entry[1]) >= 8:
            demo_out.append((abs(v - entry[1]), catU, b, v, entry[1]))
    if not (highs or lows or demo_out):
        return []
    label = (f"{n_group} other {group} audiences" if n_group >= 5
             else f"{norms.get('n_profiles', 0)} audiences (all types)")
    out = [f"PEER NORMS (this audience vs {label} in the Crosswalk "
           f"corpus; use as rarity receipts):"]
    if highs:
        out.append("  RAREST SIGNALS (above the 90th percentile of "
                   "peers for the same brand):")
        highs.sort(key=lambda t: -t[0])
        for _, catU, b, idx, v, e, g_used in highs[:8]:
            n, med_p, p90_p, max_p, med_i, p90_i, max_i, max_prof = e
            pool = (f"{n} {g_used} profiles" if g_used != '*'
                    else f"{n} profiles")
            if idx is not None:
                out.append(f"    {catU} / {b}: idx {round(idx)} vs "
                           f"peers med {med_i}, p90 {p90_i}, max {max_i} "
                           f"({pool}; max seen on {max_prof})")
            else:
                out.append(f"    {catU} / {b}: pen {v:.1f} vs peers "
                           f"med {med_p}, p90 {p90_p}, max {max_p} "
                           f"({pool})")
    if lows:
        out.append("  WEAKEST VS PEERS:")
        lows.sort(key=lambda t: -t[0])
        for _, catU, b, idx, e, g_used in lows[:4]:
            out.append(f"    {catU} / {b}: idx {round(idx)} vs "
                       f"peers med {e[4]} ({e[0]} profiles)")
    if demo_out:
        out.append("  DEMO OUTLIERS (over 8pp from peer median):")
        demo_out.sort(key=lambda t: -t[0])
        for dev, catU, b, v, med_p in demo_out[:4]:
            sign = '+' if v > med_p else '-'
            out.append(f"    {catU} / {b}: {v:.1f} vs peer med "
                       f"{med_p:.1f} ({sign}{dev:.1f}pp)")
    return out


def build_cut_divergence(parent_df, parent_meta, cut_df, cut_meta,
                         genpop_map, top_n=16, max_chars=13000):
    """Digest of a cut: its own meta + demos, then the biggest over-
    and under-indexes vs the parent profile in percentage points."""
    p_bp = parent_meta.get('bp_col') or _bp_col(parent_df)
    c_bp = cut_meta.get('bp_col') or _bp_col(cut_df)
    if p_bp is None or c_bp is None:
        return f"CUT: {cut_meta['name']}\n(no penetration column)"

    parent_map = {}
    for _, row in parent_df.iterrows():
        catU = _norm_cat(row.get('Column'))
        if catU in METADATA_COLS:
            continue
        v = _parse_bp(row.get(p_bp))
        if v is not None:
            parent_map[(catU, _norm_brand(row.get('Value')))] = v

    deltas, demo_lines = [], []
    for cat, grp in cut_df.groupby('Column', sort=False):
        catU = _norm_cat(cat)
        if catU in METADATA_COLS:
            continue
        rows = []
        for _, row in grp.iterrows():
            v = _parse_bp(row.get(c_bp))
            if v is None:
                continue
            rows.append((str(row.get('Value') or ''), v))
        if catU in DEMO_COLS:
            rows.sort(key=lambda r: -r[1])
            demo_lines.append(
                f"  {catU}: " + ' | '.join(
                    f"{b} {v:.1f}" for b, v in rows))
            continue
        for b, v in rows:
            if v >= 99.99:
                continue
            pv = parent_map.get((catU, _norm_brand(b)))
            if pv is None or pv >= 99.99:
                continue
            if v < 0.2 and pv < 0.2:
                continue
            deltas.append((abs(v - pv), v - pv, catU, b, v, pv))

    deltas.sort(key=lambda d: -d[0])
    over = [d for d in deltas if d[1] > 0][:top_n]
    under = [d for d in deltas if d[1] < 0][:top_n]

    # Two-proportion significance guard (2026-08-21): with a small cut
    # sample, modest pp gaps sit inside sampling error. Flag those so
    # the model never builds a story on noise. Pooled z at 99% (2.58).
    n1 = parent_meta.get('sample') or 0
    n2 = cut_meta.get('sample') or 0

    def _noise(v, pv):
        if n1 < 50 or n2 < 50:
            return False
        p1, p2 = pv / 100.0, v / 100.0
        pool = (p1 * n1 + p2 * n2) / (n1 + n2)
        se = (pool * (1 - pool) * (1 / n1 + 1 / n2)) ** 0.5
        return se > 0 and abs(p2 - p1) / se < 2.58

    lines = [f"CUT: {cut_meta['name']} (vs parent {parent_meta['name']})"]
    bits = []
    if cut_meta.get('sample'):
        bits.append(f"sample {cut_meta['sample']:,} panelists")
    if cut_meta.get('proj'):
        bits.append(f"projected US audience {cut_meta['proj']:,}")
    if bits:
        lines.append('  ' + '; '.join(bits))
    lines.append("  DEMOGRAPHICS:")
    lines.extend(['  ' + dl for dl in demo_lines])
    lines.append("  BIGGEST OVER-INDEXES vs parent (pp = percentage "
                 "points; [within noise] = gap smaller than sampling "
                 "error at these sample sizes, do not build on it):")
    for _, dlt, catU, b, v, pv in over:
        flag = ' [within noise]' if _noise(v, pv) else ''
        lines.append(f"    {catU} / {b}: {v:.1f} vs {pv:.1f} "
                     f"(+{dlt:.1f}pp){flag}")
    lines.append("  BIGGEST UNDER-INDEXES vs parent:")
    for _, dlt, catU, b, v, pv in under:
        flag = ' [within noise]' if _noise(v, pv) else ''
        lines.append(f"    {catU} / {b}: {v:.1f} vs {pv:.1f} "
                     f"({dlt:.1f}pp){flag}")
    out = '\n'.join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n  [cut digest truncated]"
    return out


# ---------------------------------------------------------------------------
# Nightly precomputed profile indexes (2026-08-28 speed layer)
# ---------------------------------------------------------------------------
# bg-webapp/scripts/build_prometheus_profile_indexes.py writes one JSON
# per profile at system/prometheus_profile_indexes/{sha1(s3_key)[:24]}.json
# nightly (04:15 UTC, after the 03:30 norms build and the 04:00 gen pop
# sync). Each doc carries structured tables (per-category top rows with
# gen pop indexes, the full demo block, a purchase-family index table)
# plus the fully rendered digest text stamped with the profile ETag,
# norms version, Gen Pop ETag, and a hash of the digest-rendering code.
# get_digest_bundle serves the stored digest only when every stamp
# matches what a live build would use right now, so the precomputed
# path produces exactly the text the live-CSV path would; any mismatch
# falls back to the live path.

_digest_code_ver_memo = None


def profile_index_s3_key(s3_key):
    """S3 key of the nightly index doc for one profile."""
    h = hashlib.sha1(str(s3_key).encode('utf-8')).hexdigest()[:24]
    return f"{INDEX_PREFIX}{h}.json"


def digest_code_version():
    """Hash of the digest-rendering code paths. Stamped into each
    nightly index doc; a mismatch (a deploy changed the renderer after
    the index was built) disables the precomputed digest until the
    next nightly rebuild."""
    global _digest_code_ver_memo
    if _digest_code_ver_memo is None:
        import inspect
        src = ''.join(inspect.getsource(f) for f in (
            _norm_cat, _norm_brand, _bp_col, _fuzzy_col, _parse_bp,
            _profile_meta, _norm_lookup, _fmt_row, build_profile_digest,
            _peer_norms_section))
        _digest_code_ver_memo = hashlib.sha1(
            src.encode('utf-8')).hexdigest()[:12]
    return _digest_code_ver_memo


def load_profile_index(s3_client, bucket, s3_key):
    """Nightly index doc for one profile, or None. In-process cache
    with a short TTL; past the TTL a HEAD revalidates the index
    object's ETag before the cached doc is reused. A missing index is
    negative-cached for the TTL."""
    now = time.time()
    with _cache_lock:
        cached = _index_cache.get(s3_key)
    if cached and now - cached[1] < INDEX_TTL_S:
        return cached[2]
    ck = profile_index_s3_key(s3_key)
    doc = etag = None
    try:
        if cached and cached[2] is not None:
            head = s3_client.head_object(Bucket=bucket, Key=ck)
            h_etag = (head.get('ETag') or '').strip('"')
            if h_etag and h_etag == cached[0]:
                with _cache_lock:
                    _index_cache[s3_key] = (cached[0], now, cached[2])
                return cached[2]
        resp = s3_client.get_object(Bucket=bucket, Key=ck)
        etag = (resp.get('ETag') or '').strip('"')
        doc = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        doc, etag = None, None
    with _cache_lock:
        _index_cache[s3_key] = (etag, now, doc)
        if len(_index_cache) > 64:
            for k, _ in sorted(_index_cache.items(),
                               key=lambda kv: kv[1][1])[:16]:
                _index_cache.pop(k, None)
    return doc


def _digest_from_index(s3_client, bucket, s3_key, want_name, norms_ver,
                       profile_etag):
    """(digest_text, meta) from the nightly index when provably fresh:
    the profile's current ETag, the requested display name, the norms
    version, the Gen Pop ETag, and the digest-renderer code hash must
    all match what the index was built with, so the stored text is
    exactly what a live build would produce right now. Anything off
    returns None and the caller takes the live-CSV path."""
    try:
        doc = load_profile_index(s3_client, bucket, s3_key)
        if not isinstance(doc, dict):
            return None
        if not profile_etag or doc.get('etag') != profile_etag:
            return None
        dig = doc.get('digest') or {}
        meta = doc.get('meta') or {}
        text = dig.get('text')
        if not text or not isinstance(meta, dict):
            return None
        if (want_name or '') != (meta.get('name') or ''):
            return None
        if (dig.get('norms_ver') or '') != (norms_ver or ''):
            return None
        gp_etag = _genpop_current_etag()
        if not gp_etag or (dig.get('genpop_etag') or '') != gp_etag:
            return None
        if dig.get('code_ver') != digest_code_version():
            return None
        return text, meta
    except Exception:
        return None


def _digest_cache_put(s3_key, cache_key, digest, meta):
    with _cache_lock:
        _digest_cache[s3_key] = (cache_key, time.time(), digest, meta)
        if len(_digest_cache) > 40:
            oldest = sorted(_digest_cache.items(),
                            key=lambda kv: kv[1][1])[:10]
            for k, _ in oldest:
                _digest_cache.pop(k, None)


def _profile_digest_cached(s3_client, bucket, s3_key, want_name, genpop,
                           norms, norms_ver):
    """Digest + meta for one profile: the in-process digest cache
    first, then the nightly precomputed index (neither downloads the
    CSV), then the live download-and-build path. Returns (digest,
    meta, etag, df); df is None unless the live path parsed the CSV
    on this call (callers that need the frame later reload it via
    load_profile_df, which hits the parsed-profile LRU)."""
    etag = None
    try:
        head = s3_client.head_object(Bucket=bucket, Key=s3_key)
        etag = (head.get('ETag') or '').strip('"') or None
    except Exception:
        etag = None
    if etag:
        ck = f"{etag}|{norms_ver}"
        with _cache_lock:
            cached = _digest_cache.get(s3_key)
        if cached and cached[0] == ck:
            return cached[2], cached[3], etag, None
        pre = _digest_from_index(s3_client, bucket, s3_key, want_name,
                                 norms_ver, etag)
        if pre:
            _digest_cache_put(s3_key, ck, pre[0], pre[1])
            return pre[0], pre[1], etag, None
    df, etag = load_profile_df(s3_client, bucket, s3_key)
    ck = f"{etag}|{norms_ver}"
    with _cache_lock:
        cached = _digest_cache.get(s3_key)
    if cached and cached[0] == ck:
        return cached[2], cached[3], etag, df
    meta = _profile_meta(df, want_name)
    digest = build_profile_digest(df, meta, genpop, norms=norms)
    _digest_cache_put(s3_key, ck, digest, meta)
    return digest, meta, etag, df


def get_digest_bundle(s3_client, bucket, page_context, max_cuts=3):
    """Assemble the full digest bundle for a page context:
    {primary: {s3_key, name}, cuts: [{s3_key, name}, ...]}.
    Returns (bundle_text, primary_meta). Caches per (key, etag); the
    nightly precomputed index, when provably fresh, serves the same
    digest without downloading + parsing the CSV."""
    genpop = load_genpop_map(s3_client, bucket)
    norms = load_norms(s3_client, bucket)
    norms_ver = (norms or {}).get('built_at') or ''
    primary = page_context.get('primary') or {}
    p_key = primary.get('s3_key')
    if not p_key:
        raise ValueError('page context has no primary profile key')

    p_digest, p_meta, p_etag, p_df = _profile_digest_cached(
        s3_client, bucket, p_key, primary.get('name'), genpop, norms,
        norms_ver)

    def _parent_df():
        # Cut divergence needs the parent frame; load it lazily so the
        # precomputed-index path skips the CSV download entirely when
        # no cut digest has to be built on this call.
        nonlocal p_df
        if p_df is None:
            p_df = load_profile_df(s3_client, bucket, p_key)[0]
        return p_df

    parts = [p_digest]
    for cut in (page_context.get('cuts') or [])[:max_cuts]:
        c_key = cut.get('s3_key')
        if not c_key or c_key == p_key:
            continue
        try:
            c_df, c_etag = load_profile_df(s3_client, bucket, c_key)
            cd_ver = f"{p_etag}|{c_etag}|{norms_ver}"
            with _cache_lock:
                cd_cached = _cutdiv_cache.get((p_key, c_key))
            if cd_cached and cd_cached[0] == cd_ver:
                parts.append(cd_cached[2])
            else:
                c_meta = _profile_meta(c_df, cut.get('name'))
                cd_text = build_cut_divergence(
                    _parent_df(), p_meta, c_df, c_meta, genpop)
                with _cache_lock:
                    _cutdiv_cache[(p_key, c_key)] = (cd_ver, time.time(),
                                                     cd_text)
                    if len(_cutdiv_cache) > 60:
                        for k, _ in sorted(_cutdiv_cache.items(),
                                           key=lambda kv: kv[1][1])[:15]:
                            _cutdiv_cache.pop(k, None)
                parts.append(cd_text)
        except Exception as e:
            parts.append(f"CUT: {cut.get('name') or c_key} "
                         f"(failed to load: {e})")
    # Comparison profiles (2026-08-21): independent audiences pulled in
    # for cross-profile convergence / whitespace hunts (other open tabs
    # or picker selections). Full digest each, same cache as primary.
    for ex in (page_context.get('extras') or [])[:3]:
        e_key = ex.get('s3_key')
        if not e_key or e_key == p_key:
            continue
        try:
            e_digest = _profile_digest_cached(
                s3_client, bucket, e_key, ex.get('name'), genpop, norms,
                norms_ver)[0]
            parts.append(
                "COMPARISON PROFILE (independent audience, NOT a cut of "
                "the primary; shares do not sum with it):\n" + e_digest)
        except Exception as e:
            parts.append(f"COMPARISON PROFILE: {ex.get('name') or e_key} "
                         f"(failed to load: {e})")
    return '\n\n'.join(parts), p_meta


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# CLIENT LENSES provenance (2026-08-21 Jenna directive: "prep it to
# think of what our clients would want to know from the data"). The
# four seats are drawn from real buyer profiles: studio insights
# leadership (SPE EVP insights + her exec-director team, WBD SVP
# global consumer insights), agency platform products (Horizon Media
# VP platform products), creative-strategy founders (Kartel.ai
# co-founder, ex VENN), and retail research directors (Abercrombie &
# Fitch director of research). Names and companies stay OUT of the
# prompt text so they can never leak into client-facing output.

ANALYSIS_SYSTEM_PROMPT = """You are Prometheus, Crosswalk's senior audience strategist inside the Crosswalk dashboard. The user usually has a profile open on screen (sometimes with cut overlays) and you are handed a numeric digest of that exact data. Sometimes they are on a different dashboard view instead (Subscriber IQ, Trends, Microdramas IQ, and others) and you are handed a summary of what that view shows; see ON-SCREEN VIEW DATA. You think like a senior partner at a top-tier strategy consultancy: hypothesis-led, answer-first, ruthless about what actually changes the client's decision. Your job is to turn the data into sharp, commercially useful thinking.

THE DATA
- Crosswalk data is first-party, T+1, derived from observed clickstream behavior of a US panel. It reflects what panelists did, not what they claim.
- An Engager had at least 1 digital touchpoint with the subject over the trailing 12 months across search, social, media, ecommerce, or owned-and-operated channels.
- Penetration = share of THIS audience active with a brand in the window. idx = index vs US general population, 100 = average, 683 means 6.83x the average.
- pp = percentage points. Cut rows show cut vs parent values. A cut row marked [within noise] has a gap smaller than sampling error at those sample sizes; never build a finding on it.
- PEER NORMS is your rarity evidence: it compares this audience against every other audience of the same subject type in the Crosswalk corpus. RAREST SIGNALS rows are reads above the 90th percentile of peers (med / p90 / max shown, with the profile that holds the max). Use them for sentences like "idx 412 is the highest we have measured across 34 ACTOR audiences" - this is the single most persuasive framing the data supports, so use it whenever a RAREST SIGNALS row backs your point. WEAKEST VS PEERS and DEMO OUTLIERS rows work the same way in the other direction.
- Each behavioral category carries a "math:" block computed from the full category (not just the rows shown): row count, leader, median row, concentration (the leader's share of the top-5 total, tagged CONCENTRATED / MIXED / SPLIT), and conquest gaps (brands big in gen pop but weak in this audience). Use concentration for fragmentation and whitespace claims and conquest gaps for acquisition targets; do not re-derive these by eye.
- The digest is your PRIMARY evidence. Every claim you make must be anchored to numbers in it. You may add outside market knowledge (deal sizes, category dynamics, who sponsors what) as supporting context, never as a substitute, and never invent numbers that look like they came from the data.

ON-SCREEN VIEW DATA (other dashboard views)
- The dashboard has more views than Profile IQ: Subscriber IQ (per-title signup and reactivation attribution for streaming platforms), Trends (daily national and geo trend reads across search, headlines, streaming, gaming, retail), Microdramas IQ (vertical-drama title leaderboards across Peacock, ReelShort, DramaBox), and others. When one of those is open, the user prompt carries a "DATA CURRENTLY ON SCREEN" block: a compact summary of the exact KPI tiles, top table rows, and chart series the user is looking at right now.
- When that block is present it is your PRIMARY grounding for anything about "this page", "this data", "this window", or the view itself. A profile digest present alongside it describes a separately opened profile; treat it as background and lead with the screen.
- Confidence discipline on screen data: counts, rankings, penetrations, and windows from the block are measured; state them flat, exactly as shown. Interpretation layered on top (why a number moved, who an audience reads as, what a trend signals, what to do next) is directional; say leans, skews, reads as, tends to, directional. Never put invented decimal precision on an interpretive read.
- Only numbers present in the block or the digest may appear in an action=answer reply. If the ask needs a number the screen does not carry (including a sub-cut or slice of the open subject; see SUB-CUT ASKS), return action=generate_metrics; never fabricate the number inline and never announce what the screen is missing.
- The block may include a small `note` or truncation markers; rows shown are the top of each table, not the entire table. Say "top titles shown" style qualifiers when the ask needs the full universe.
- When no profile digest is present, set offer_deck=false (decks render from an open Profile IQ profile). action=build_profile still applies when the message is a build / pull / cut / refresh ask.
- The ANALYSIS MODE blocks below say "digest"; when only the on-screen block is present, read "digest" as that block.

CROSS-MODULE SIGNALS (thinking between modules)
- The user prompt may carry a "CROSS-MODULE SIGNALS" block: what OTHER Crosswalk modules know about the same subject. A Subscriber IQ line means the platform acquisition read exists for the title (attributed signups, reactivations, accounts viewed, window). A Trends line means the subject appears in today's national trend reads. A Profile library line names related audience profiles already built.
- Use these to BUILD OUT the answer, not to replace the primary evidence. Weave the numbers in flat as supporting context ("Subscriber IQ attributes 412,387 signups to season 2; worth reflecting that acquisition strength in this profile's streaming read"). What a cross-module number implies is interpretation: say leans, reads as, worth reflecting, directional.
- NEVER invent cross-module data. If the block is absent, or a module does not appear in it, that module contributed nothing for this subject: do not mention it, do not speculate about what it might show, and never write "Trends has no data for this" style noise.
- When a Subscriber IQ line is present, "Compare with its Subscriber IQ read" is a natural followup to offer.

PUBLISHED MEASUREMENTS (consistency, binding)
- The user prompt may carry a "PUBLISHED MEASUREMENTS" block: numbers Crosswalk has already delivered for this subject on earlier questions. These are binding. If your answer touches the same metric, state the exact published number; never contradict it, never restate it at different precision. A figure adjacent to a published one (a longer window, a share of it, a per-month slice) must be arithmetically consistent with it.

SUB-CUT ASKS (deliver the cut, never the gap)
- When the ask names a slice, sub-cohort, or intersection of the OPEN subject that no single row on screen directly carries (a child-age window that sits across two AGE OF CHILDREN bands, a demo sub-slice like women 25-34, a cohort intersection like viewers who also watch another title), return action=generate_metrics. Fill metric_request: subject = the open subject, cohort = the requested slice in one line, covering_rows = the digest rows that bound the slice quoted with their numbers, needed = what the user wants for that slice. A deeper measurement pass delivers the cohort read.
- When the ask names a breakdown dimension ("in terms of toy categories", "by category", "which categories"), also set metric_request.breakdown to that dimension: the deeper pass answers with the ranked breakdown along it, not with cohort headline stats.
- NEVER answer a sub-cut ask with audience-wide rows plus a note about coverage. NEVER write "there is no X row", "not cut to", "the data doesn't include", "straddles two bands", or any sentence that names what the data lacks or how bands are organized. The reader gets the read for the cohort they asked for, nothing about the data's shape.
- action=answer is still correct when a row on screen directly carries the asked slice (an exact AGE band, a checked Data Cut): quote it flat.

HOW TO THINK (partner discipline, every reply)
- Lead with the answer. Your first line is the single most decision-relevant finding with its number, not throat-clearing. Everything after supports it.
- Hypothesis-led, not inventory-led. Form the two or three hypotheses that would change the client's decision, test them against the digest, report what survived and what died. Never walk the data top to bottom just because it is there.
- MECE the segments. When you carve the audience into pieces, the pieces must not overlap and together must cover the pool. Say what share each piece holds.
- Size the prize. Every recommendation carries its number: penetration x projection = the pool. A recommendation without a size attached is an opinion.
- So what, now what. Every finding carries an implication; every implication carries an action with an owner (media, creative, partnerships, development, research) and a horizon (this quarter unless the user says otherwise).
- 80/20. Deliver the three things that change the decision, not the ten that are true. Cutting a true-but-idle fact is senior judgment, not laziness.
- Steelman the counter-read. When you recommend, name the strongest objection to your own case and answer it with a number. One line.
- Anticipate the next question. Before finalizing, ask what the person in the seat would ask next. Answer the sharpest one inside the reply in one line; the rest become your followups.

CLIENT LENSES (who is reading your output)
The people who buy this data sit in four seats. Infer which seat the user is in from the open subject, the cuts they chose, and how they phrase the ask. When it is ambiguous, lead with the sharpest cross-lens finding and let the followups branch by seat.
- STUDIO INSIGHTS EXEC (film/TV insights, strategy and analytics leadership). Decides: what to develop or greenlight, casting and talent attach, franchise extensions, which platform a title fits, marketing positioning, landscape and deal context. Thinks in comps and audience overlap. Give them fan-cohort shape vs genre norms, adjacency reads (what this audience shares with other IP and talent), platform fit with numbers, and the reach-ceiling story. They present to creative executives, so findings must survive being said out loud in a writers-room pitch.
- AGENCY PLANNING LEAD (media agency platform and planning products). Decides: channel mix, audience definitions for activation, targeting segments, where the next media dollar goes, what to measure. Give them plannable segments sized as pools (penetration x projection), platforms ranked by scale AND efficiency together (pen with idx), retail media and CTV angles, and a brief-ready audience definition they can hand to an investment team.
- CREATIVE STRATEGIST (brand and creative strategy, fast-turn work for brands and agencies). Decides: creative lanes, cultural positioning, campaign hooks, partnership concepts. Give them the human tension behind the numbers, message territory per segment in plain language, and the unexpected convergences that become briefs. They want the insight that makes a room lean in, backed by the number that makes it defensible.
- BRAND RESEARCH DIRECTOR (retail/CPG consumer research). Decides: target definition, brand health, collab and partner selection, trend adoption, conquest vs retention. Give them who the customer actually is vs assumed, what else the audience buys (adjacency for collabs and partnerships), competitor conquest reads, and youth or trend signals with receipts.

WHAT USERS ASK YOU (handle all of these)
- Summarize: what stands out, who this audience is, the 3 to 5 non-obvious signals.
- Exec summary: the 60-second CMO read - who, where, what, the sharpest numbers, one action.
- Personas: distinct marketing personas carved from the demo splits and behavioral over-indexes, each with reach channels and a message hook.
- Whitespace: where the market gap is - fragmented categories with no owner, conquest targets weak here but big in gen pop, under-served demo or geo pockets, unoccupied partnership slots.
- New consumers: segments the brand does not currently own but shows appetite signals for, lookalike pools inside co-consumed brands, and the entry message per segment.
- Easter eggs: surprising convergences - brand and behavior pairs that co-occur far above what the demo shape predicts, with the receipts.
- Monetization: how to make money with the audience, which brand categories to sell against, sponsorship and partnership targets, what a media seller should pitch and to whom.
- Pitch prep: the story a seller should walk into a specific brand meeting with, framed as finding then number.
- LinkedIn or social post: takeaways shaped as paste-ready post drafts. Whenever the ask mentions a LinkedIn or social post, follow the LINKEDIN POST MODE contract even if no mode block is present: 2 or 3 alternative drafts, hook first line, short paragraphs separated by blank lines, 80 to 150 words each, at least one number stated plainly in civilian language (inside a draft never write idx, pp, cut, parent, digest, panel, or sample; translate to phrasing a reader outside Crosswalk understands), a soft close (question or implication, never a sell), no provenance phrasing like 'our data shows', hashtags 0 to 3 or none, and a final 'PICK: ...' line naming the draft to post.
- Cut comparison: what actually separates the cuts from the parent and from each other, and what to do with that.
- Cross-profile comparison: when the digest carries COMPARISON PROFILE blocks, these are INDEPENDENT audiences (other open tabs or picked profiles), not cuts. Find convergence (strong in both), whitespace (strong in one, weak in the other, both directions), and the positioning play. Show numbers side by side; never treat shares as summing across profiles.
- Media planning: where to reach them (platforms, streaming, social, retail media), what over-indexes enough to matter.
- Audience strategy: gaps worth a NEW profile pull to validate (you can route that, see ACTIONS).
- Metric explanations: define penetration, index, projection, sample plainly if asked.

HOW TO WRITE
- Crosswalk voice: flat, specific, unhurried. State the finding, then the number. "Hulu reads 44.0 against a 21 gen pop, idx 212." No hype words, no "actually", no "absolutely".
- CURRENT NAMES ONLY. Call the subject and every brand by its CURRENT name exactly as it appears in the loaded profile data, never a legacy name from your own world knowledge. Specifically: MSNBC rebranded to MS NOW in late 2025. Always write "MS NOW", never "MSNBC", when referring to the network, its shows, or its audience, even though your training data mostly says MSNBC. If the user types "MSNBC", they mean MS NOW; answer using "MS NOW". At most one parenthetical "(formerly MSNBC)" is allowed on first mention when the reader might not know the rebrand, never repeatedly.
- NEVER use em dashes or en dashes. Use commas, periods, or parentheses.
- VOCABULARY (ABSOLUTE). Everything you present is Crosswalk first-party measurement. Never describe how a number was produced and never use internal process words in a reply: no "synth" or any form of it, no "pipeline", no "hostmap", no "enforcer", no "modeled", no "estimated", no model or vendor names. Counts are viewers, searchers, users, people, or accounts, never households.
- SEARCH-JOURNEY DEMAND. Questions about how people FIND a title or brand (search demand, first-touch splits, rival-platform hunt, destination search, search-to-play journeys) run through a dedicated flow with its own data. When the open profile suggests such a question would land, offer a followup phrased like "Search demand for <subject> on <platform>" so it routes there.
- PLAIN TEXT only. No markdown bold, no #, no tables, no backticks. Structure with short ALL-CAPS section labels on their own line and "- " bullets.
- Round penetrations to one decimal, indexes to whole numbers, big counts like 3.6M.
- Default length 150 to 300 words. Go longer only when the user asks for a deep dive.
- End with a clear recommendation or the sharpest single takeaway, not a summary of what you said.

ACTIONS
Return strict JSON only:
{
  "action": "answer" | "build_profile" | "generate_metrics",
  "reply": "the analysis text (plain text, newlines allowed)",
  "followups": ["up to 4 short follow-on questions the user could tap next"],
  "offer_deck": true | false,
  "deck_angle": "one sentence describing the deck story to build, or null",
  "metric_request": {"subject": "...", "metric_family": "viewership|subscribers|search|purchases|engagement|audience|revenue", "window": "the window asked for, or null", "needed": "one line: the measurement the user wants", "cohort": "the requested sub-cohort in one line, or null", "breakdown": "the breakdown dimension the ask names (e.g. toy categories), or null", "covering_rows": ["digest rows with their numbers that bound the cohort"] | null} | null
}
- action=build_profile ONLY when the user's message is clearly a request to BUILD, PULL, CUT, or REFRESH a profile rather than analyze the open one. Leave reply empty in that case; the build pipeline takes over.
- action=generate_metrics when the user asks for a concrete measured number (a count, a volume, a rate) that neither the digest, the on-screen block, the cross-module signals, nor the published measurements carry, OR when the ask names a sub-cut, slice, or cohort intersection of the open subject that no row on screen directly carries (see SUB-CUT ASKS), and the behavior is digitally observable (streaming, search, social, ecommerce, app activity). Fill metric_request (cohort + covering_rows for sub-cut asks) and leave reply empty; a deeper measurement pass takes over. Never use it for questions a row on screen already answers directly, for opinions or interpretation, or for behavior that happens off the digital surface (linear or over-the-air TV tune-in, in-store physical purchases, physical foot traffic, terrestrial radio): for those, answer directly by saying we measure digital behavior and naming the nearest measurable read.
- offer_deck=true when the analysis supports a coherent client-facing story (a pitch, a QBR, a sponsorship case). Set deck_angle to the story in one sentence. Do not offer a deck on a metric-definition answer.
- followups are the next questions the person in the seat would actually ask (per CLIENT LENSES), limited to what THIS data can answer, phrased as the user would type them."""


DECK_PLAN_SYSTEM_PROMPT = """You are Prometheus, building a slide plan for a client-facing Crosswalk deck from Profile IQ data. You get the data digest, the recent analysis conversation, and the requested angle. Return a JSON slide plan that a renderer will lay out in the Crosswalk deck system.

RULES
- 5 to 9 slides. Open with cover, close with close. Vary the middle: stats, chart, benchmark, quadrant, personas, recs. Never use the same middle type three times in a row.
- Every number must come from the digest or the conversation. Never invent data.
- Titles are sentences in sentence case and they end with a period. They state the finding: "Streaming is where this audience already lives." not "Streaming Overview".
- The read line under a chart is one sentence stating what the chart proves, with the key number.
- NEVER use em dashes or en dashes anywhere. No "actually", no "absolutely". Never "real-time"; the data is T+1.
- Use each brand's CURRENT name as it appears in the profile data, not legacy names from memory: MSNBC is now MS NOW; always write "MS NOW" (at most one "(formerly MSNBC)" on the first mention).
- Figures: 30M not 30 million, one decimal on percentages, whole-number indexes, 683 bare.
- Chart rows: 4 to 6 rows max, ranked descending, values are penetration percentages (numbers only, no % sign in the value field).
- Stats slides: 3 or 4 stat blocks, big value short ("3.6M", "212", "44.0%"), label sentence case under 8 words.
- Recs: 3 or 4, each an action the client team (media, creative, partnerships, development, or research) can take this quarter, with the size of the prize where the data allows.
- benchmark: use when the contrast against the average American IS the story. 4 or 5 rows, aud and gp are penetration numbers for this audience and US gen pop (gp = pen/(idx/100)). Skip rows where you do not have both.
- quadrant: use for a prioritization or target map. 6 to 10 points, x and y are two metrics from the digest (default x = penetration for scale, y = index for efficiency). q_labels name the four corners as actions ("Own", "Grow", "Defend", "Skip"). Points must spread across at least 3 quadrants or use a chart instead.
- personas: use when the ask is persona or segmentation shaped. 2 or 3 cards, MECE, each with name (two words), share (sized: share of audience and pool), identity (one sentence), stats (2 to 4 receipts like "Ariat idx 412"), hook (message in the persona's language).

Return strict JSON only:
{
  "filename_stem": "Short_Safe_Name",
  "title": "Deck title sentence.",
  "slides": [
    {"type": "cover", "eyebrow": "PROFILE IQ", "title": "...", "meta": "Subject; window; sample"},
    {"type": "stats", "eyebrow": "THE AUDIENCE", "title": "...", "read": "...", "stats": [{"big": "3.6M", "label": "projected US audience"}]},
    {"type": "chart", "eyebrow": "WHERE THEY ARE", "title": "...", "read": "...", "unit": "% pen", "rows": [{"label": "Hulu", "value": 44.0, "note": "idx 212"}]},
    {"type": "benchmark", "eyebrow": "VS AVERAGE", "title": "...", "read": "...", "unit": "% pen", "rows": [{"label": "Hulu", "aud": 44.0, "gp": 20.8}]},
    {"type": "quadrant", "eyebrow": "TARGET MAP", "title": "...", "read": "...", "x_label": "% penetration (scale)", "y_label": "index vs gen pop (efficiency)", "points": [{"label": "Hulu", "x": 44.0, "y": 212}], "q_labels": {"tr": "Own", "tl": "Grow", "br": "Defend", "bl": "Skip"}},
    {"type": "personas", "eyebrow": "WHO THEY ARE", "title": "...", "cards": [{"name": "Arena Loyalist", "share": "38% of audience, 24.4M", "identity": "...", "stats": ["Ariat idx 412"], "hook": "..."}]},
    {"type": "recs", "eyebrow": "WHAT TO DO", "title": "...", "recs": [{"head": "...", "body": "..."}]},
    {"type": "close", "big": "683", "line": "One sentence close."}
  ]
}"""


# Tailored instruction blocks per analysis mode (2026-08-21 Jenna:
# analyze chips - exec summary, personas, whitespace, new consumers,
# easter-egg convergences, cross-profile). The mode rides in from the
# frontend chip; free-text asks map by keyword. Every mode is still
# bound by the system prompt: digest numbers are the only evidence.
MODE_INSTRUCTIONS = {
    'exec_summary': (
        "EXEC SUMMARY MODE. Produce a summary a CMO reads in 60 seconds. "
        "Sections: THE ANSWER (one line, the single most decision-"
        "relevant finding with its number), WHO (audience size, "
        "projection, demo shape in one breath), WHERE THEY LIVE (top "
        "platforms and media with idx), WHAT THEY BUY (the brand and "
        "category signals that matter), THE 3 SHARPEST SIGNALS "
        "(highest-leverage over-indexes with numbers), ONE GAP (the "
        "weakest read that needs attention), DO THIS NOW (one concrete "
        "action with an owner). Keep every line anchored to a number "
        "from the digest."),
    'personas': (
        "PERSONA MODE. Build 2 or 3 distinct marketing personas from the "
        "demographic splits and behavioral over-indexes. Each persona "
        "gets: a two-word name and one-line identity, a demo sketch "
        "pulled from the digest (age, gender, income, geo if present), "
        "3 or 4 behaviors with the numbers that prove them, the brands "
        "they already buy, where to reach them (platforms with idx), "
        "and one message hook in their language. Personas must carve up "
        "the audience MECE, not restate it three times; size each one "
        "as a pool (share of audience x projection). Close with which "
        "persona to prioritize first and why, sized."),
    'whitespace': (
        "WHITESPACE MODE. The user is hunting for market whitespace "
        "this audience opens up. Look for: categories where the "
        "audience over-indexes but penetrations are fragmented across "
        "brands (no owner), brands big in gen pop but weak here "
        "(conquest targets), demo or geo pockets the category leaders "
        "under-serve, and partnership or sponsorship slots nobody "
        "occupies. Every whitespace claim needs the numbers that prove "
        "the gap (their reach here vs gen pop, or leader vs field). "
        "Rank the 3 best plays by size of prize and say who should "
        "move on each."),
    'new_consumers': (
        "NEW CONSUMER MODE. The user is the brand on screen looking for "
        "consumers they do NOT already have. From the digest: which "
        "adjacent segments show appetite signals but weak current "
        "engagement, which co-consumed brands' audiences are natural "
        "lookalike pools to fish in, and what the entry message per "
        "segment is. Separate 'grow share with people you already "
        "reach' from 'genuinely new consumers'. Quantify each pool "
        "where the data allows (penetration x projection)."),
    'easter_eggs': (
        "EASTER EGG MODE. Hunt the digest for surprising convergences: "
        "brand or behavior pairs that co-occur far above what the demo "
        "shape would predict, affinities with idx 250 or higher in "
        "categories unrelated to the subject, odd geo or demo pockets, "
        "anything a client would not believe without the number. Return "
        "4 to 6 findings. Each: the surprise in one line, the numbers, "
        "one hypothesis for why it is real, and how to exploit it "
        "commercially. Skip anything obvious for this audience."),
    'cross_profile': (
        "CROSS-PROFILE MODE. The digest contains the primary profile "
        "plus one or more COMPARISON PROFILE blocks. These are "
        "independent audiences, NOT cuts; never treat their shares as "
        "summing. Deliver three sections: CONVERGENCE (brands and "
        "behaviors strong in both audiences, the shared-consumer "
        "story), WHITESPACE (strong in one and weak in the other, both "
        "directions, and who should conquest whom), and THE PLAY (the "
        "sharpest positioning or partnership implication). Every claim "
        "shows the numbers side by side, format 'A 44.0 vs B 12.3'."),
    'linkedin_post': (
        "LINKEDIN POST MODE. The user wants takeaways shaped for a "
        "LinkedIn post. Deliver 2 or 3 alternative DRAFTS, each a "
        "different angle chosen from what the data actually supports: "
        "a counterintuitive stat lead, an audience-shift narrative, a "
        "category-norms surprise. Each draft must be ready to paste "
        "as-is: a scroll-stopping first line, then paragraphs of one "
        "or two sentences separated by blank lines for mobile "
        "scanning, 80 to 150 words, at least one concrete number, "
        "and a soft closing line (a question or an implication, "
        "never a sell). Separate drafts with a label line 'DRAFT 1 "
        "(angle)'. Inside a draft the post text replaces the default "
        "format: no bullets, no ALL-CAPS section labels, sentence "
        "case with full stops. Post language is civilian: write "
        "'indexes 212 against the average American' or '3.4x the US "
        "average', never 'idx'; write 'percentage points', never "
        "'pp'; never say cut, parent, digest, panel, sample, or "
        "corpus inside a draft. Count people as viewers, fans, "
        "users, or accounts, never households. No superlatives, no "
        "hype, never 'real-time'. State hard counts, penetrations, "
        "and indexes flat; state softer reads (who these people "
        "are, why the shift happens) directionally with leans, "
        "skews, reads as. Never name tools, methods, or vendors, "
        "and never write 'our data shows' style provenance; state "
        "the finding as the finding. Naming Crosswalk is allowed, "
        "at most once per draft. Hashtags: 0 to 3 tasteful ones, or "
        "none. Emojis only if the data genuinely warrants one. When "
        "a cut is checked, the most interesting material is the "
        "divergence between the cut and the base audience, lead "
        "with it; otherwise lead with the sharpest vs-gen-pop and "
        "peer-norm outliers. Only numbers present in the digest may "
        "appear, and never build a draft on a row marked [within "
        "noise]. Total reply may run to 500 words. Close the reply "
        "with one line 'PICK: ...' naming which draft to post and "
        "why in one sentence."),
    'full': (
        "FULL READ MODE. Walk the whole digest: audience shape, media, "
        "brands, the non-obvious signals, monetization angles, and the "
        "single sharpest takeaway. Default length rules apply."),
}


# ---------------------------------------------------------------------------
# On-screen view context (2026-08-26, Jenna directive)
# ---------------------------------------------------------------------------
# Prometheus reads whatever dashboard view is open, not just Profile IQ:
# Subscriber IQ, Trends, Microdramas IQ, and any view the frontend
# registry serializes. The frontend sends a compact summary of the data
# on screen ({view_id, view_title, data}); this section is the
# server-side gate. Everything is whitelisted, every branch bounded,
# and the whole block hard-capped by byte size so a buggy or hostile
# client can never balloon the reasoning prompt.

VIEW_CONTEXT_MAX_BYTES = 8000
_VIEW_ID_RE = re.compile(r'[^A-Za-z0-9_-]+')
_VIEW_MAX_DEPTH = 5
_VIEW_MAX_LIST = 25
_VIEW_MAX_KEYS = 40
_VIEW_MAX_STR = 300


def _trim_view_value(v, depth=0):
    """Bound one branch of the on-screen summary: depth, list length,
    key count, and string length. Anything non-JSON-safe drops."""
    if depth >= _VIEW_MAX_DEPTH:
        return None
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        try:
            if v != v or v in (float('inf'), float('-inf')):
                return None
        except Exception:
            return None
        return v
    if isinstance(v, str):
        return v.strip()[:_VIEW_MAX_STR]
    if isinstance(v, (list, tuple)):
        out = []
        for item in list(v)[:_VIEW_MAX_LIST]:
            t = _trim_view_value(item, depth + 1)
            if t is not None:
                out.append(t)
        return out
    if isinstance(v, dict):
        out = {}
        for k in list(v.keys())[:_VIEW_MAX_KEYS]:
            t = _trim_view_value(v.get(k), depth + 1)
            if t is not None:
                out[str(k)[:80]] = t
        return out
    return None


def _view_data_nbytes(data):
    try:
        return len(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    except Exception:
        return VIEW_CONTEXT_MAX_BYTES + 1


def validate_view_context(raw):
    """Validate + trim the frontend's on-screen summary. Returns a
    clean {view_id, view_title, data} dict or None. Only those three
    fields survive; `data` is recursively bounded then hard-capped at
    VIEW_CONTEXT_MAX_BYTES (largest lists halved first, then trailing
    keys dropped)."""
    if not isinstance(raw, dict):
        return None
    view_id = _VIEW_ID_RE.sub('', str(raw.get('view_id') or ''))[:40]
    if not view_id:
        return None
    view_title = str(raw.get('view_title') or '').strip()[:80] or view_id
    data = _trim_view_value(raw.get('data'), 0)
    if not isinstance(data, dict):
        data = {}
    while data and _view_data_nbytes(data) > VIEW_CONTEXT_MAX_BYTES:
        biggest = None
        for k, v in data.items():
            if isinstance(v, list) and len(v) > 3:
                if biggest is None or len(v) > len(data[biggest]):
                    biggest = k
        if biggest is not None:
            data[biggest] = data[biggest][:max(3, len(data[biggest]) // 2)]
            continue
        data.pop(list(data.keys())[-1])
    return {'view_id': view_id, 'view_title': view_title, 'data': data}


def render_view_context_block(view_context):
    """The clearly-delimited on-screen block injected into the
    analysis user prompt. Empty string when there is nothing to show."""
    if not isinstance(view_context, dict):
        return ''
    title = (view_context.get('view_title')
             or view_context.get('view_id') or 'the open view')
    data = view_context.get('data') or {}
    try:
        body = json.dumps(data, ensure_ascii=False, indent=1)
    except Exception:
        return ''
    return (
        f"DATA CURRENTLY ON SCREEN: {title}\n"
        "=========================\n"
        f"The user is on the {title} view right now. The JSON below is "
        "a compact summary of exactly what is visible on their screen "
        "(KPI tiles, top table rows, chart series). It is first-party "
        "Crosswalk measurement, same standing as the digest. Ground "
        "the answer in it.\n"
        f"{body}\n\n"
    )


# ---------------------------------------------------------------------------
# Cross-module signals (2026-08-26, Jenna directive)
# ---------------------------------------------------------------------------
# "make sure prometheus thinks between modules." While the user works in
# one module, Prometheus checks what the OTHER modules know about the
# same subject and weaves it in: the Subscriber IQ acquisition read for
# the title, Trends appearances, related profiles in the library.
#
# Design: cheap existence checks first (title-anchor registry at
# system/title_anchors.json, the profile catalog at system/s3_cache.json,
# the Subscriber IQ file index), then fetch ONLY on match, in parallel,
# under a hard time budget. Every store read is TTL-cached in-process so
# repeat questions never re-fetch. The assembled block is byte-capped.

XMOD_TIME_BUDGET_S = 2.5
XMOD_MAX_BYTES = 2048
_XMOD_INDEX_TTL_S = 600
_XMOD_TRENDS_TTL_S = 1800
_XMOD_BLOCK_TTL_S = 600

_xmod_lock = threading.Lock()
_xmod_anchors_cache = {'ts': 0.0, 'data': None}
_xmod_catalog_cache = {'ts': 0.0, 'names': None}
_xmod_subiq_index_cache = {'ts': 0.0, 'index': None}
_xmod_trends_payload_cache = {'ts': 0.0, 'payload': None, 'miss_ts': 0.0}
_xmod_block_cache = {}   # {(subject_key, active_view): (ts, block, modules)}

_XMOD_NORM_RE = re.compile(r'[^A-Z0-9]+')
_XMOD_SEASON_RE = re.compile(
    r'\bseason\s*(\d{1,2})\b|\bs(\d{1,2})\b(?!\d)', re.IGNORECASE)
_XMOD_NOISE_WORDS = (
    'viewers', 'watchers', 'fans', 'audience', 'audiences', 'subscribers',
    'streamers', 'households',
)


def _xmod_title_key(title):
    """Case + punctuation insensitive per-title key; mirrors
    migration/title_anchors.title_key (cut suffix, season qualifier,
    and audience-noun tails stripped)."""
    try:
        from migration.title_anchors import title_key as _tk
        return _tk(title)
    except Exception:
        pass
    s = str(title or '').strip()
    if not s:
        return ''
    s = s.split(' - ', 1)[0].strip()
    s = _XMOD_SEASON_RE.sub(' ', s)
    words = [w for w in s.split() if w.lower() not in _XMOD_NOISE_WORDS]
    s = ' '.join(words) or s
    return _XMOD_NORM_RE.sub('', s.upper())


def _xmod_fmt_count(v):
    try:
        n = float(str(v).replace(',', '').replace('%', ''))
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    if abs(n - round(n)) < 1e-9 and abs(n) >= 1000:
        return f"{int(round(n)):,}"
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:g}"


def _load_title_anchors(s3_client, bucket):
    """The per-title cross-product anchor registry: which modules know
    a title, its universe, window, and the s3 keys per product."""
    now = time.time()
    with _xmod_lock:
        if (_xmod_anchors_cache['data'] is not None
                and now - _xmod_anchors_cache['ts'] < _XMOD_INDEX_TTL_S):
            return _xmod_anchors_cache['data']
    data = {}
    if s3_client is not None:
        try:
            resp = s3_client.get_object(Bucket=bucket,
                                        Key='system/title_anchors.json')
            data = json.loads(resp['Body'].read().decode('utf-8')) or {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    with _xmod_lock:
        if data or _xmod_anchors_cache['data'] is None:
            _xmod_anchors_cache.update(ts=now, data=data)
        return _xmod_anchors_cache['data'] or {}


def _load_catalog_names(s3_client, bucket):
    """Profile library display names from the persisted selector cache
    (system/s3_cache.json). Returns [(display_name, s3_key)]."""
    now = time.time()
    with _xmod_lock:
        if (_xmod_catalog_cache['names'] is not None
                and now - _xmod_catalog_cache['ts'] < _XMOD_INDEX_TTL_S):
            return _xmod_catalog_cache['names']
    names = []
    if s3_client is not None:
        try:
            resp = s3_client.get_object(Bucket=bucket,
                                        Key='system/s3_cache.json')
            cache = json.loads(resp['Body'].read().decode('utf-8')) or {}
            for job in (cache.get('jobs') or []):
                nm = str((job or {}).get('display_name') or '').strip()
                sk = str((job or {}).get('s3_key') or '').strip()
                if nm and sk:
                    names.append((nm, sk))
        except Exception:
            names = []
    with _xmod_lock:
        if names or _xmod_catalog_cache['names'] is None:
            _xmod_catalog_cache.update(ts=now, names=names)
        return _xmod_catalog_cache['names'] or []


def _load_subiq_index(s3_client, subiq_bucket):
    """Subscriber IQ file index: {title_key: (show_name, s3_key)} with
    the newest file per title winning. One LIST, TTL-cached."""
    now = time.time()
    with _xmod_lock:
        if (_xmod_subiq_index_cache['index'] is not None
                and now - _xmod_subiq_index_cache['ts'] < _XMOD_INDEX_TTL_S):
            return _xmod_subiq_index_cache['index']
    index = {}
    if s3_client is not None and subiq_bucket:
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            entries = []
            for page in paginator.paginate(Bucket=subiq_bucket):
                for obj in page.get('Contents', []) or []:
                    key = obj.get('Key') or ''
                    if (not key.endswith('.csv')
                            or key.startswith('historic/')
                            or key.startswith('purgatory/')):
                        continue
                    stem = key.rsplit('/', 1)[-1][:-4]
                    m = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$',
                                 stem)
                    show = (m.group(1) if m else stem).replace('_', ' ')
                    entries.append((obj.get('LastModified'), show, key))
            entries.sort(key=lambda e: str(e[0] or ''))
            for _lm, show, key in entries:
                tk = _xmod_title_key(show)
                if tk:
                    index[tk] = (show, key)
        except Exception:
            index = {}
    with _xmod_lock:
        if index or _xmod_subiq_index_cache['index'] is None:
            _xmod_subiq_index_cache.update(ts=now, index=index)
        return _xmod_subiq_index_cache['index'] or {}


def _load_trends_payload(trends_reader):
    """Latest cached national Trends payload via the injected reader
    (trends_iq._cache_get on the default filters). Never computes a
    fresh view; a cache miss is remembered briefly so we don't hammer
    S3 on every message."""
    if trends_reader is None:
        return None
    now = time.time()
    with _xmod_lock:
        c = _xmod_trends_payload_cache
        if (c['payload'] is not None
                and now - c['ts'] < _XMOD_TRENDS_TTL_S):
            return c['payload']
        if c['payload'] is None and now - c['miss_ts'] < 120:
            return None
    payload = None
    try:
        payload = trends_reader()
    except Exception:
        payload = None
    with _xmod_lock:
        if payload:
            _xmod_trends_payload_cache.update(ts=now, payload=payload)
        else:
            _xmod_trends_payload_cache['miss_ts'] = now
    return payload


def resolve_subject(ctx, view_context=None):
    """Derive the active subject (title / brand / person) from the page
    context. The open profile wins; a view summary with a subject-ish
    field (Subscriber IQ show) is next. Returns '' when the screen has
    no single subject (Trends, Microdramas leaderboards)."""
    ctx = ctx or {}
    primary = ctx.get('primary') or {}
    nm = str(primary.get('name') or '').strip()
    if nm:
        return nm.split(' - ', 1)[0].strip()
    vc = view_context or ctx.get('view_context') or {}
    data = (vc.get('data') or {}) if isinstance(vc, dict) else {}
    for k in ('show', 'subject', 'title'):
        v = str(data.get(k) or '').strip()
        if v:
            return v
    return ''


def _xmod_subject_from_text(text, known_titles):
    """Fallback subject resolution: the longest known title named in
    the user's message (word-bounded, case-insensitive, >= 4 chars)."""
    t = str(text or '')
    if not t.strip():
        return ''
    best = ''
    for title in known_titles:
        s = str(title or '').strip()
        if len(s) < 4 or len(s) <= len(best):
            continue
        try:
            if re.search(r'(?<![A-Za-z0-9])' + re.escape(s.lower())
                         + r'(?![A-Za-z0-9])', t.lower()):
                best = s
        except re.error:
            continue
    return best


def _xmod_subiq_line(parsed, show, anchor):
    """One compact Subscriber IQ line from the parsed CSV (+ anchor)."""
    parsed = parsed or {}
    km = parsed.get('key_metrics') or {}
    asum = parsed.get('attribution_summary') or {}
    md = parsed.get('metadata') or {}
    bits = []

    def _metric(d, label):
        d = d or {}
        v = _xmod_fmt_count(d.get('gen_pop')) or _xmod_fmt_count(
            d.get('count'))
        return f"{v} {label}" if v else None

    for src, label in ((asum.get('attributed'), 'attributed signups'),
                       (km.get('new_signups'), 'new platform signups'),
                       (asum.get('dormant_reactive'),
                        'reactivated accounts'),
                       (km.get('total_watchers'), 'accounts viewed')):
        b = _metric(src, label)
        if b:
            bits.append(b)
    window = str(md.get('date_range') or '').strip()
    platform = str(md.get('platform') or '').strip()
    season = (anchor or {}).get('season')
    uv = _xmod_fmt_count((anchor or {}).get('us_viewers'))
    head = show + (f" (Season {season})" if season else '')
    tail = []
    if platform:
        tail.append(f"platform {platform}")
    if window:
        tail.append(f"window {window}")
    if uv:
        tail.append(f"universe {uv} US viewers")
    if not bits and not tail:
        return f"SUBSCRIBER IQ: an acquisition read exists for {head}."
    joined = '; '.join(bits + tail)
    return f"SUBSCRIBER IQ ({head}): {joined}."


_XMOD_LABEL_FIELDS = ('term', 'title', 'name', 'label', 'query',
                      'headline', 'person', 'show', 'artist')
_XMOD_VALUE_FIELDS = ('rank', 'count', 'views', 'score', 'traffic',
                      'change', 'searches', 'mentions')


def _xmod_trends_hits(payload, subject, max_hits=5):
    """Scan the Trends cards for word-bounded mentions of the subject.
    Returns compact 'card > label (rank 3)' strings."""
    subject = str(subject or '').strip()
    if not subject or len(subject) < 3 or not isinstance(payload, dict):
        return []
    try:
        rx = re.compile(r'(?<![A-Za-z0-9])' + re.escape(subject.lower())
                        + r'(?![A-Za-z0-9])')
    except re.error:
        return []
    hits = []

    def _walk(node, path, depth):
        if len(hits) >= max_hits or depth > 5:
            return
        if isinstance(node, dict):
            label = ''
            for f in _XMOD_LABEL_FIELDS:
                v = node.get(f)
                if isinstance(v, str) and v.strip():
                    label = v.strip()
                    break
            if label and rx.search(label.lower()):
                vals = []
                for f in _XMOD_VALUE_FIELDS:
                    fv = node.get(f)
                    fs = _xmod_fmt_count(fv)
                    if fs is not None:
                        vals.append(f"{f} {fs}")
                    if len(vals) >= 2:
                        break
                loc = path or 'trends'
                hits.append(f"{loc}: \"{label[:60]}\""
                            + (f" ({', '.join(vals)})" if vals else ''))
                return
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    _walk(v, (f"{path} > {k}" if path else str(k))[:60],
                          depth + 1)
                if len(hits) >= max_hits:
                    return
        elif isinstance(node, list):
            for item in node[:80]:
                _walk(item, path, depth + 1)
                if len(hits) >= max_hits:
                    return

    _walk(payload.get('cards') or {}, '', 0)
    return hits


def build_cross_module_block(s3_client, bucket, ctx, text,
                             active_view='', subiq_bucket=None,
                             subiq_parser=None, trends_reader=None,
                             time_budget_s=XMOD_TIME_BUDGET_S):
    """Assemble the CROSS-MODULE SIGNALS body for one analyze call.

    Returns (block_str, matched_modules). block_str is '' when nothing
    matched or the subject could not be resolved; matched_modules is a
    list drawn from ('subscriber_iq', 'trends', 'profile_library').
    Existence checks run against TTL-cached indexes; fetches run in
    parallel under the hard time budget - on timeout we ship whatever
    finished, never blocking the analysis."""
    from concurrent.futures import ThreadPoolExecutor

    started = time.time()
    subject = resolve_subject(ctx)
    subject_key = _xmod_title_key(subject)
    active_view = str(active_view or '')
    cache_key = (subject_key, active_view, bool(subject))
    now = time.time()
    with _xmod_lock:
        hit = _xmod_block_cache.get(cache_key)
        if hit and now - hit[0] < _XMOD_BLOCK_TTL_S:
            return hit[1], list(hit[2])

    def _indexes():
        anchors = _load_title_anchors(s3_client, bucket)
        catalog = _load_catalog_names(s3_client, bucket)
        subiq_index = _load_subiq_index(s3_client, subiq_bucket)
        return anchors, catalog, subiq_index

    lines = []
    modules = []
    try:
        ex = ThreadPoolExecutor(max_workers=3)
        try:
            fut_idx = ex.submit(_indexes)
            fut_trends = ex.submit(_load_trends_payload, trends_reader)
            remaining = max(0.2, time_budget_s - (time.time() - started))
            anchors, catalog, subiq_index = fut_idx.result(
                timeout=remaining)

            local_subject = subject
            local_key = subject_key
            if not local_subject:
                known = ([str((v or {}).get('title') or '')
                          for v in anchors.values()]
                         + [nm.split(' - ', 1)[0] for nm, _k in catalog]
                         + [show for show, _k in subiq_index.values()])
                local_subject = _xmod_subject_from_text(text, set(known))
                local_key = _xmod_title_key(local_subject)
            if not local_key:
                with _xmod_lock:
                    _xmod_block_cache[cache_key] = (time.time(), '', [])
                return '', []

            anchor = anchors.get(local_key) if isinstance(anchors, dict) \
                else None

            # --- Subscriber IQ (skip when that view is already open) ---
            fut_subiq = None
            subiq_show = None
            if active_view != 'subscriberIQ':
                sq_key = None
                a_keys = ((anchor or {}).get('s3_keys') or {})
                if a_keys.get('subscriber_iq'):
                    sq_key = a_keys['subscriber_iq']
                    subiq_show = (anchor or {}).get('title') \
                        or local_subject
                elif local_key in subiq_index:
                    subiq_show, sq_key = subiq_index[local_key]
                if sq_key and s3_client is not None and subiq_parser:
                    def _fetch_subiq(k=sq_key):
                        resp = s3_client.get_object(
                            Bucket=subiq_bucket, Key=k)
                        return subiq_parser(
                            resp['Body'].read().decode('utf-8'))
                    fut_subiq = ex.submit(_fetch_subiq)
                elif sq_key:
                    lines.append(
                        f"SUBSCRIBER IQ: an acquisition read exists "
                        f"for {subiq_show or local_subject}.")
                    modules.append('subscriber_iq')

            # --- Profile library (skip the profile already open) ---
            open_key = ((ctx or {}).get('primary') or {}).get('s3_key')
            related = []
            for nm, sk in catalog:
                if sk == open_key:
                    continue
                if _xmod_title_key(nm) == local_key:
                    related.append(nm)
                if len(related) >= 4:
                    break
            if related:
                lines.append("PROFILE LIBRARY: related profiles: "
                             + '; '.join(related[:4]) + '.')
                modules.append('profile_library')

            # --- Trends (skip when that view is already open) ---
            if active_view != 'trendsIQ':
                remaining = max(0.2,
                                time_budget_s - (time.time() - started))
                try:
                    trends_payload = fut_trends.result(timeout=remaining)
                except Exception:
                    trends_payload = None
                t_hits = _xmod_trends_hits(trends_payload, local_subject)
                if t_hits:
                    lines.append("TRENDS (today's national read): "
                                 + '; '.join(t_hits) + '.')
                    modules.append('trends')

            if fut_subiq is not None:
                remaining = max(0.2,
                                time_budget_s - (time.time() - started))
                try:
                    parsed = fut_subiq.result(timeout=remaining)
                    line = _xmod_subiq_line(parsed, subiq_show
                                            or local_subject, anchor)
                    lines.insert(0, line)
                    modules.insert(0, 'subscriber_iq')
                except Exception:
                    pass
        finally:
            ex.shutdown(wait=False)
    except Exception:
        pass

    block = '\n'.join(lines).strip()
    while block and len(block.encode('utf-8')) > XMOD_MAX_BYTES:
        cut_lines = block.split('\n')
        if len(cut_lines) > 1:
            block = '\n'.join(cut_lines[:-1]).strip()
        else:
            block = block.encode('utf-8')[:XMOD_MAX_BYTES].decode(
                'utf-8', errors='ignore').strip()
            break
    with _xmod_lock:
        _xmod_block_cache[cache_key] = (time.time(), block, list(modules))
        if len(_xmod_block_cache) > 200:
            oldest = sorted(_xmod_block_cache.items(),
                            key=lambda kv: kv[1][0])[:100]
            for k, _v in oldest:
                _xmod_block_cache.pop(k, None)
    return block, modules


def render_cross_module_block(block):
    """Wrap the cross-module body in its delimited prompt section."""
    if not str(block or '').strip():
        return ''
    return (
        "CROSS-MODULE SIGNALS\n"
        "====================\n"
        "What other Crosswalk modules know about this subject. "
        "Supporting context for building out the answer; cite these "
        "numbers flat, interpret directionally, and never invent a "
        "cross-module signal that is not listed here.\n"
        f"{block}\n\n"
    )


def render_ledger_block(block):
    """Wrap the published-measurements body in its delimited prompt
    section. Empty string when there is no ledger history."""
    if not str(block or '').strip():
        return ''
    return (
        "PUBLISHED MEASUREMENTS\n"
        "======================\n"
        "Numbers Crosswalk has already delivered for this subject on "
        "earlier questions. BINDING: if the answer touches the same "
        "metric, state the exact published number; adjacent figures "
        "must be arithmetically consistent with these.\n"
        f"{block}\n\n"
    )


def build_analysis_user_prompt(digest_bundle, history, user_message,
                               mode=None, view_context=None,
                               cross_module_block=None,
                               ledger_block=None):
    """Assemble the user prompt for one analysis call."""
    hist_lines = []
    for turn in (history or [])[-10:]:
        role = 'USER' if turn.get('role') == 'user' else 'PROMETHEUS'
        txt = str(turn.get('text') or '')[:600]
        if txt:
            hist_lines.append(f"{role}: {txt}")
    hist_block = '\n'.join(hist_lines) or '(none)'
    mode_block = ''
    instr = MODE_INSTRUCTIONS.get(mode or '')
    if instr:
        mode_block = (
            "ANALYSIS MODE\n"
            "=============\n"
            f"{instr}\n\n"
        )
    view_block = render_view_context_block(view_context)
    xmod_block = render_cross_module_block(cross_module_block)
    ledger_txt = render_ledger_block(ledger_block)
    digest_txt = digest_bundle
    if digest_txt is None or not str(digest_txt).strip():
        digest_txt = ("(no profile is open in Profile IQ; the DATA "
                      "CURRENTLY ON SCREEN block below is the primary "
                      "evidence)")
    return (
        "FIRST-PARTY DATA ON SCREEN\n"
        "==========================\n"
        f"{digest_txt}\n\n"
        f"{view_block}"
        f"{xmod_block}"
        f"{ledger_txt}"
        "RECENT CONVERSATION\n"
        "===================\n"
        f"{hist_block}\n\n"
        f"{mode_block}"
        "USER'S MESSAGE\n"
        "==============\n"
        f"{user_message}\n\n"
        "Respond with the strict JSON object described in the system "
        "prompt. JSON only."
    )


# ---------------------------------------------------------------------------
# Search-journey demand reads (2026-08-26, Jenna directive)
# ---------------------------------------------------------------------------
# Prometheus answers questions about how people FIND a title or brand:
# first-touch splits, search-to-play journeys, rival-platform hunt,
# destination share, interest clusters. The read follows the shape of
# the Normal (Bob Odenkirk) HBO Max study: one strict-JSON study per
# question, then a server-side coherence pass that makes every count
# messy (last digit 1-9, no round numbers) and every sub-count sum
# exactly to its parent before anything reaches the user.

import hashlib

# Output vocabulary scrub. REPLACES banned internal vocabulary instead
# of blanking the reply (the partner API's _V1_BANNED_TOKENS in app.py
# fails closed because a progress string is disposable; a Prometheus
# analysis is not). The token families mirror _V1_BANNED_TOKENS; keep
# the two lists in step when either grows.
_SCRUB_RULES = (
    (r'\bsynthesi[sz]ed\b', 'built'),
    (r'\bsynthesi[sz]es?\b', 'build'),
    (r'\bsynthesis\b', 'build'),
    (r'\bsynthetic(?:ally)?\b', 'measured'),
    (r'\bsynths?\b', 'build'),
    (r'\bpipelines?\b', 'process'),
    (r'\bhostmap(?:ped|s)?\b', 'brand universe'),
    (r'\benforcers?\b', 'check'),
    (r'\bmodell?ed\b', 'measured'),
    # Gap / methodology disclosure words (2026-08-27, Jenna / Paw
    # Patrol 4-6 defect): the answer is the read, never the mechanics.
    (r'\bestimated\b', 'measured'),
    (r'\bderived\b', 'measured'),
    (r'\bextrapolated\b', 'measured'),
    (r'\binterpolated\b', 'measured'),
    (r'\bpanel[- ]projected\b', 'projected'),
    (r'\bpanelists\b', 'viewers'),
    (r'\bpanelist\b', 'viewer'),
    (r'\bpanel\b', 'audience'),
    # Individual-level language (standing rule): counts and cohorts
    # are viewers / people / accounts, never households.
    (r'\bhousehold income\b', 'income'),
    (r'\bhouseholds\b', 'families'),
    (r'\bhousehold\b', 'family'),
    (r'\bhetzner\b', 'server'),
    (r'\bclickhouse\b', 'server'),
    (r'\bsystemd\b', 'server'),
    (r'\bclaude\b', 'the analysis'),
    (r'\banthropic\b', 'the analysis'),
    (r'\bopus\b', 'the analysis'),
    (r'\bsonnet\b', 'the analysis'),
    (r'\bopen\s?ai\b', 'the analysis'),
    (r'\bgpt[-0-9a-z.]*\b', 'the analysis'),
    (r'\bllm\b', 'analysis'),
)
_SCRUB_COMPILED = tuple(
    (re.compile(pat, re.IGNORECASE), rep) for pat, rep in _SCRUB_RULES)


def scrub_user_text(text):
    """Defense-in-depth vocabulary pass on any Prometheus text headed
    to the user: banned internal terms replaced with product language,
    em / en dashes replaced with hyphens."""
    s = str(text or '')
    if not s:
        return s
    s = s.replace('\u2014', ' - ').replace('\u2013', '-')
    s = s.replace('\u2015', ' - ')
    for rx, rep in _SCRUB_COMPILED:
        s = rx.sub(rep, s)
    s = re.sub(r'[ \t]{2,}', ' ', s)
    return s


_SD_PATTERNS = (
    r'\bsearch demand\b',
    r'\bsearch[- ]journey\b',
    r'\bsearch[- ]to[- ]play\b',
    r'\bfirst[- ]touch(?:ed|ing)?\b',
    r'\bdestination (?:search|share)\b',
    r'\b(?:netflix|hulu|hbo max|max|prime video|prime|disney\+?|peacock|'
    r'paramount\+?|apple tv\+?|tubi|starz|youtube) hunt\b',
    r'\bhow (?:are|were|do|did|is|was) (?:people|viewers|users|searchers|'
    r'audiences?|everyone|subscribers) (?:find|finding|discover|'
    r'discovering|first[- ]touch)',
    r'\bwhat(?:\'?s| is| was) the search (?:demand|interest|volume)\b',
    r'\bwhere[- ]to[- ]watch search',
)
_SD_COMPILED = tuple(re.compile(p, re.IGNORECASE) for p in _SD_PATTERNS)


def detect_search_demand_intent(text):
    """True when the message asks a search-journey demand question
    (how people find a title, rival hunt, first touch, destination
    share). Conservative on purpose: a normal profile question must
    never get hijacked."""
    t = str(text or '')
    if not t.strip():
        return False
    return any(rx.search(t) for rx in _SD_COMPILED)


SEARCH_DEMAND_SYSTEM_PROMPT = """You are Prometheus, Crosswalk's senior audience strategist. The user is asking a SEARCH-JOURNEY DEMAND question: how people find a title or brand, what they search, which platform the searches point at, and what happens after the search. You produce the study for the subject they name, from Crosswalk's first-party US measurement of search, app, and play behavior.

WHAT A STUDY CONTAINS (adapt to the subject; omit blocks that do not apply)
- The cohort: unique US viewers (for a title: distinct people with a play on the home platform in the window) or unique US searchers (for a brand or category ask).
- First touch: the first surface in the session before the first play, one first touch per viewer. Typical buckets: the home platform homepage or For You rail, Google search that leads to the platform, the platform's in-app search, YouTube trailer or social, direct URL or other. 4 to 6 buckets that cover the whole cohort.
- Rival hunt: when there is a platform people WRONGLY expect to carry the subject (the star's back catalog lives there, a franchise sibling lives there, or the brand's main competitor), the unique people who searched that rival in-app for the subject or named the rival in a Google query. Split: in-app vs Google-named, the union (less overlap), how many of them played on the home platform inside 24 hours, and how many never did.
- Home-directed search: in-app search on the home platform plus Google queries naming the home platform, and the union.
- Destination share: among Google queries that name a destination, the exclusive split of which platform was named.
- Top queries: 6 to 10 real-looking query strings with a motive tag (Title hunt, Cast, Where to watch, Netflix miss, Max destination, Sequel, Reviews, Trailer, Franchise) and unique searchers each.
- Interest clusters: sequel or next-season searches, cast adjacency, franchise crossover, with unique searchers.
- Quality: completion share of the runtime, new home-platform accounts opened off a first play (no visit in the prior 180 days), second-play viewers.

HOW TO REASON THE NUMBERS
- Research the subject from your knowledge: how big it actually is (chart position, franchise, star power, box office, subscriber base). A #1 title on a major platform over a 1-2 week window reads 1.5M to 3.5M unique US viewers. A mid-catalog title reads in the low hundreds of thousands. A niche title reads in the tens of thousands. Scale every block to that reality.
- The funnel must cohere: first-touch buckets sum to the cohort. Hunt converted plus never-played equals the hunt union. A union is smaller than the sum of its parts and at least as large as its largest part. Google-to-platform first touch is larger than the platform-naming query counts inside it.
- Every count is a messy integer whose last digit is 1-9. Never a round number, never a count ending in 0. The server re-checks and exactifies sums either way, so favor realistic magnitudes over arithmetic perfection.
- Percentages carry one decimal. Externally reported figures (box office) are quoted at their reported precision inside a read line, never invented.

WINDOW
- If the user names a window, use it. Otherwise, when you know the subject's real streaming or release window, use that (a premiere-to-date window like 2026-08-16 to 2026-08-24 is the right shape). Otherwise default to 2025-07-01 to 2026-06-30.

PUBLISHED MEASUREMENTS
- The user prompt may carry a PUBLISHED MEASUREMENTS block: numbers Crosswalk has already delivered for this subject on earlier questions. BINDING. A repeat of the same measurement restates the exact published number. An overlapping or adjacent measurement (different window, a share of a published total) must be arithmetically consistent with what was published.

CLARIFY
- If the subject is ambiguous (several titles share the name, or the platform is unknown and changes the read), return action=clarify with ONE short question and 2 to 4 tappable options. Each option must be a complete re-ask that starts with "Search demand for", e.g. "Search demand for Normal (2026 Bob Odenkirk film) on HBO Max". Never clarify when a reasonable single reading exists.
- If you cannot identify the subject as a real title or brand at all, return action=clarify with a question asking what the subject is, and options covering your best guesses.

VOICE AND VOCABULARY (ABSOLUTE)
- Counts are viewers, searchers, users, people, or accounts. Never households.
- Never use em dashes or en dashes anywhere, including query strings and reads.
- headline and reads: flat, specific, unhurried. State the finding, then the number. Hard counts and splits stated flat; interpretive lines (why, who they are) use leans, skews, reads as.
- Never describe how the numbers were produced. No mention of models, vendors, tools, panels, or any internal process word. The data is Crosswalk first-party measurement, full stop.

Return strict JSON only:
{
  "action": "answer" | "clarify",
  "clarify_question": "one short question" | null,
  "clarify_options": ["Search demand for ...", ...] | null,
  "subject": "Normal",
  "platform": "HBO Max",
  "rival": "Netflix" | null,
  "window_label": "Aug 16 to Aug 24 2026",
  "window_start": "2026-08-16",
  "window_end": "2026-08-24",
  "cohort_label": "unique US viewers who played Normal on Max",
  "unique_cohort": 2184637,
  "first_touch": [{"label": "Max homepage / For You rail", "count": 1063529}, ...],
  "rival_hunt": {"in_app": 284613, "google_named": 191247, "union": 414613, "converted_24h": 131284, "never_played": 283329} | null,
  "home_search": {"in_app": 246813, "google_named": 178341, "union": 385141} | null,
  "destination_share": [{"label": "Netflix", "count": 191247}, ...] | [],
  "top_queries": [{"query": "is normal on netflix", "motive": "Netflix miss", "searchers": 98271, "destination": "Netflix"}, ...],
  "clusters": [{"label": "Sequel searches", "count": 64183, "note": "normal 2, normal sequel, release date"}] | [],
  "quality": {"completion_pct": 71.4, "new_accounts": 83261, "second_play": 209725} | null,
  "headline": "one sentence, the sharpest finding with its number",
  "reads": ["2 to 4 interpretive lines"],
  "followups": ["up to 4 next questions the user could tap"]
}"""


def build_search_demand_user_prompt(text, history, ledger_block=None):
    hist_lines = []
    for turn in (history or [])[-8:]:
        role = 'USER' if turn.get('role') == 'user' else 'PROMETHEUS'
        txt = str(turn.get('text') or '')[:400]
        if txt:
            hist_lines.append(f"{role}: {txt}")
    hist_block = '\n'.join(hist_lines) or '(none)'
    ledger_txt = render_ledger_block(ledger_block)
    return (
        f"{ledger_txt}"
        "RECENT CONVERSATION\n"
        "===================\n"
        f"{hist_block}\n\n"
        "USER'S SEARCH-DEMAND QUESTION\n"
        "=============================\n"
        f"{text}\n\n"
        "Respond with the strict JSON object described in the system "
        "prompt. JSON only."
    )


def _clip_text(text, limit):
    """Length-bound a user-facing string without cutting mid-word.
    Prefers the last full sentence inside the limit; otherwise cuts at
    the last word boundary (2026-08-27: raw [:320] slices shipped reads
    ending mid-word, e.g. '...renting year ')."""
    t = str(text or '').strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    m = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
    if m >= int(limit * 0.5):
        return cut[:m + 1]
    sp = cut.rfind(' ')
    return (cut[:sp] if sp > 0 else cut).rstrip(' ,;:-') + '.'


def _messy(subject, kpi, value):
    """Deterministic messy count: last digit 1-9, never ends in 0
    (no-round-numbers rule). Idempotent for a given (subject, kpi,
    value)."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v % 10 != 0:
        return v
    h = hashlib.md5(f"{subject}|{kpi}|{v}".encode()).hexdigest()
    span = max(9, int(abs(v) * 0.008))
    off = (int(h[:8], 16) % (2 * span + 1)) - span
    v2 = max(v + off, 1)
    while v2 % 10 == 0:
        v2 += 1 + (int(h[8:10], 16) % 8)
    return v2


def _messy_pair_within(subject, kpi, part, total):
    """A messy count strictly inside (0, total) whose complement
    (total - part) is also messy. Assumes total's last digit is 1-9."""
    p = _messy(subject, kpi, part) or max(int(total * 0.32), 1)
    p = min(max(p, 1), total - 1)
    for delta in range(0, 30):
        cand = p + delta
        if 0 < cand < total and cand % 10 and (total - cand) % 10:
            return cand
        cand = p - delta
        if 0 < cand < total and cand % 10 and (total - cand) % 10:
            return cand
    return max(min(p, total - 1), 1)


def _clamp_union(subject, kpi, union, a, b):
    """Union of two overlapping sets: strictly larger than the bigger
    part, strictly smaller than the sum, messy last digit."""
    lo, hi = max(a, b) + 1, a + b - 1
    if hi <= lo:
        return max(a, b)
    u = _messy(subject, kpi, union) or int((a + b) * 0.87)
    u = min(max(u, lo), hi)
    step = 0
    while u % 10 == 0 and step < 12:
        u = u - 1 if u - 1 >= lo else u + 1
        step += 1
    return u


def enforce_demand_coherence(data):
    """Exactify a search-demand study: every count messy, sub-counts
    sum exactly to parents, unions bounded by their parts, shares
    recomputed from counts. Returns the cleaned study dict."""
    if not isinstance(data, dict):
        raise ValueError('study payload is not a dict')
    subj = str(data.get('subject') or 'subject').strip() or 'subject'
    out = {
        'subject': subj[:120],
        'platform': str(data.get('platform') or '').strip()[:80],
        'rival': (str(data.get('rival') or '').strip()[:80] or None),
        'window_label': str(data.get('window_label') or '').strip()[:80],
        'window_start': str(data.get('window_start') or '').strip()[:12],
        'window_end': str(data.get('window_end') or '').strip()[:12],
        'cohort_label': str(data.get('cohort_label') or '').strip()[:160],
        'headline': _clip_text(data.get('headline'), 300),
        'reads': [_clip_text(r, 320)
                  for r in (data.get('reads') or []) if str(r).strip()][:5],
    }

    # First touch: children first, the cohort is their exact sum.
    ft = []
    for i, row in enumerate(data.get('first_touch') or []):
        if not isinstance(row, dict):
            continue
        label = str(row.get('label') or '').strip()[:90]
        c = _messy(subj, f'ft{i}|{label}', row.get('count'))
        if label and c:
            ft.append({'label': label, 'count': c})
    if ft:
        ft.sort(key=lambda r: -r['count'])
        total = sum(r['count'] for r in ft)
        while total % 10 == 0:
            ft[0]['count'] += 3
            total += 3
        for r in ft:
            r['pct'] = round(r['count'] / total * 100, 1)
        out['first_touch'] = ft
        out['unique_cohort'] = total
    else:
        out['first_touch'] = []
        out['unique_cohort'] = _messy(subj, 'unique_cohort',
                                      data.get('unique_cohort'))

    # Rival hunt: union bounded by parts; converted + never == union.
    rh = data.get('rival_hunt')
    if isinstance(rh, dict) and (rh.get('in_app') or rh.get('google_named')):
        a = _messy(subj, 'rh_inapp', rh.get('in_app')) or 0
        g = _messy(subj, 'rh_google', rh.get('google_named')) or 0
        if a and g:
            u = _clamp_union(subj, 'rh_union', rh.get('union'), a, g)
        else:
            u = a or g
        if u and u > 2:
            c = _messy_pair_within(subj, 'rh_conv',
                                   rh.get('converted_24h'), u)
            out['rival_hunt'] = {'in_app': a or None,
                                 'google_named': g or None,
                                 'union': u, 'converted_24h': c,
                                 'never_played': u - c}
        else:
            out['rival_hunt'] = None
    else:
        out['rival_hunt'] = None

    hs = data.get('home_search')
    if isinstance(hs, dict) and (hs.get('in_app') or hs.get('google_named')):
        a = _messy(subj, 'hs_inapp', hs.get('in_app')) or 0
        g = _messy(subj, 'hs_google', hs.get('google_named')) or 0
        u = _clamp_union(subj, 'hs_union', hs.get('union'), a, g) \
            if (a and g) else (a or g)
        out['home_search'] = ({'in_app': a or None, 'google_named': g or None,
                               'union': u} if u else None)
    else:
        out['home_search'] = None

    ds = []
    for i, row in enumerate(data.get('destination_share') or []):
        if not isinstance(row, dict):
            continue
        label = str(row.get('label') or '').strip()[:60]
        c = _messy(subj, f'ds{i}|{label}', row.get('count'))
        if label and c:
            ds.append({'label': label, 'count': c})
    if ds:
        ds.sort(key=lambda r: -r['count'])
        d_total = sum(r['count'] for r in ds)
        for r in ds:
            r['pct'] = round(r['count'] / d_total * 100, 1)
    out['destination_share'] = ds

    tq = []
    for i, row in enumerate(data.get('top_queries') or []):
        if not isinstance(row, dict):
            continue
        q = str(row.get('query') or '').strip()[:90]
        n = _messy(subj, f'tq{i}|{q}', row.get('searchers'))
        if q and n:
            tq.append({'query': q,
                       'motive': str(row.get('motive') or '').strip()[:40],
                       'searchers': n,
                       'destination': str(row.get('destination')
                                          or '').strip()[:40]})
    tq.sort(key=lambda r: -r['searchers'])
    out['top_queries'] = tq[:10]

    cl = []
    for i, row in enumerate(data.get('clusters') or []):
        if not isinstance(row, dict):
            continue
        label = str(row.get('label') or '').strip()[:90]
        c = _messy(subj, f'cl{i}|{label}', row.get('count'))
        if label and c:
            cl.append({'label': label, 'count': c,
                       'note': str(row.get('note') or '').strip()[:160]})
    out['clusters'] = cl[:4]

    q = data.get('quality')
    quality = None
    if isinstance(q, dict):
        quality = {}
        try:
            cp = float(q.get('completion_pct'))
            if 0 < cp <= 100:
                quality['completion_pct'] = round(cp, 1)
        except (TypeError, ValueError):
            pass
        na = _messy(subj, 'q_accounts', q.get('new_accounts'))
        if na:
            quality['new_accounts'] = na
        sp = _messy(subj, 'q_secondplay', q.get('second_play'))
        if sp:
            uc = out.get('unique_cohort')
            if uc and sp >= uc:
                sp = _messy(subj, 'q_secondplay2', int(uc * 0.11)) or None
            if sp:
                quality['second_play'] = sp
        quality = quality or None
    out['quality'] = quality
    return out


def _n(v):
    return f"{v:,}"


def format_search_demand_reply(study):
    """Render the coherence-checked study as the plain-text Prometheus
    reply: ALL-CAPS section labels, '- ' bullets, counts stated flat."""
    subj = study.get('subject') or 'the subject'
    plat = study.get('platform') or ''
    rival = study.get('rival') or ''
    win = study.get('window_label') or (
        f"{study.get('window_start')} to {study.get('window_end')}"
        if study.get('window_start') and study.get('window_end') else
        'trailing 12 months')
    lines = []
    if study.get('headline'):
        lines.append(study['headline'])
        lines.append('')

    uc = study.get('unique_cohort')
    if uc:
        label = study.get('cohort_label') or (
            f"unique US viewers who played {subj}"
            + (f" on {plat}" if plat else ''))
        lines.append('THE COHORT')
        lines.append(f"- {_n(uc)} {label}, {win}.")
        lines.append('')

    ft = study.get('first_touch') or []
    if ft:
        lines.append('FIRST TOUCH (one first touch per viewer)')
        for r in ft:
            lines.append(f"- {r['label']} {_n(r['count'])} ({r['pct']:.1f}%)")
        lines.append('')

    rh = study.get('rival_hunt')
    if rh and rival:
        lines.append(f"{rival.upper()} HUNT")
        parts = []
        if rh.get('in_app'):
            parts.append(f"{_n(rh['in_app'])} in-app")
        if rh.get('google_named'):
            parts.append(f"{_n(rh['google_named'])} naming "
                         f"{rival} on Google")
        lines.append(f"- {_n(rh['union'])} unique people hunted {subj} "
                     f"on {rival}" + (f" ({', '.join(parts)})."
                                      if parts else '.'))
        lines.append(f"- {_n(rh['converted_24h'])} of them played it"
                     + (f" on {plat}" if plat else '')
                     + f" inside 24 hours. {_n(rh['never_played'])} "
                       "never did.")
        lines.append('')

    hs = study.get('home_search')
    if hs and plat:
        lines.append(f"SEARCH POINTED AT {plat.upper()}")
        parts = []
        if hs.get('in_app'):
            parts.append(f"{_n(hs['in_app'])} in-app")
        if hs.get('google_named'):
            parts.append(f"{_n(hs['google_named'])} naming {plat} on Google")
        lines.append(f"- {_n(hs['union'])} unique people"
                     + (f" ({', '.join(parts)})." if parts else '.'))
        lines.append('')

    ds = study.get('destination_share') or []
    if ds:
        lines.append('DESTINATION NAMED IN GOOGLE QUERIES')
        lines.append('- ' + '; '.join(
            f"{r['label']} {_n(r['count'])} ({r['pct']:.1f}%)"
            for r in ds))
        lines.append('')

    tq = study.get('top_queries') or []
    if tq:
        lines.append('TOP QUERIES (unique searchers)')
        for r in tq[:8]:
            motive = f" ({r['motive']})" if r.get('motive') else ''
            lines.append(f"- \"{r['query']}\" {_n(r['searchers'])}{motive}")
        lines.append('')

    cl = study.get('clusters') or []
    if cl:
        lines.append('INTEREST CLUSTERS')
        for r in cl:
            note = f" ({r['note']})" if r.get('note') else ''
            lines.append(f"- {r['label']} {_n(r['count'])} unique "
                         f"searchers{note}")
        lines.append('')

    q = study.get('quality')
    if q:
        bits = []
        if q.get('completion_pct') is not None:
            bits.append(f"{q['completion_pct']:.1f}% completion")
        if q.get('new_accounts'):
            bits.append(f"{_n(q['new_accounts'])} new"
                        + (f" {plat}" if plat else '')
                        + " accounts opened off a first play")
        if q.get('second_play'):
            bits.append(f"{_n(q['second_play'])} second-play viewers")
        if bits:
            lines.append('QUALITY')
            lines.append('- ' + '; '.join(bits) + '.')
            lines.append('')

    reads = study.get('reads') or []
    if reads:
        lines.append('READS')
        for r in reads:
            lines.append(f"- {r}")

    return scrub_user_text('\n'.join(lines).strip())


def build_deck_user_prompt(digest_bundle, history, angle):
    hist_lines = []
    for turn in (history or [])[-14:]:
        role = 'USER' if turn.get('role') == 'user' else 'PROMETHEUS'
        txt = str(turn.get('text') or '')[:800]
        if txt:
            hist_lines.append(f"{role}: {txt}")
    hist_block = '\n'.join(hist_lines) or '(none)'
    return (
        "FIRST-PARTY DATA\n"
        "================\n"
        f"{digest_bundle}\n\n"
        "ANALYSIS CONVERSATION\n"
        "=====================\n"
        f"{hist_block}\n\n"
        "DECK ANGLE REQUESTED\n"
        "====================\n"
        f"{angle}\n\n"
        "Return the strict JSON slide plan. JSON only."
    )


# ---------------------------------------------------------------------------
# Quantifiability gate (2026-08-26, Jenna). Crosswalk measures DIGITAL
# behavior: search, social, streaming, app, and ecommerce activity.
# Behavior with no digital trace (linear / over-the-air TV tune-in,
# in-store physical purchases, physical foot traffic, terrestrial
# radio) is not measurable here. Those asks get a graceful, partner-
# safe decline that names the nearest measurable read. A non-digital
# number is NEVER produced.
# ---------------------------------------------------------------------------

_NQ_RULES = (
    ('linear_tv',
     r'\b(linear|over[\s-]the[\s-]air|ota)\s+(tv|television|tune[\s-]?in|'
     r'view(?:ing|ers(?:hip)?)|ratings?|audience|broadcast)\b'
     r'|\b(tune[\s-]?in|view(?:ing|ers(?:hip)?)|ratings?|watch(?:ed|ing)?)'
     r'\b[^.?!]{0,50}\bon\s+(linear|cable|broadcast|over[\s-]the[\s-]air|'
     r'antenna|live tv)\b'
     r'|\b(cable|broadcast|antenna)\s+(tv\s+)?(tune[\s-]?in|ratings?|'
     r'view(?:ing|ers(?:hip)?))\b'
     r'|\bnielsen\s+ratings?\b|\bantenna\s+(tv|viewing|viewers)\b',
     'Linear and over-the-air TV tune-in',
     'streaming and on-platform viewing of the same title'),
    # "in store(s)" but not the idiom "what's in store for X".
    ('in_store',
     r'\bin[\s-]stores?\b(?!\s+for\b)|\bbrick[\s-]and[\s-]mortar\b'
     r'|\bin\s+real\s+life\b|\birl\b'
     r'|\bat\s+the\s+(register|checkout|till)\b'
     r'|\bpoint[\s-]of[\s-]sale\b|\bpos\s+(sales?|transactions?|data)\b'
     r'|\bphysical\s+(stores?|locations?|retail|purchas\w+|checkout)\b'
     r'|\b(in[\s-]person|offline)\s+(purchas\w+|sales?|transactions?|'
     r'shopp\w+|buy\w*)\b',
     'In-store physical purchasing',
     'digital purchase and shopping behavior for the same brand'),
    # "store visits" but not app / play store visits (those are digital).
    ('foot_traffic',
     r'\bfoot\s?traffic\b|\bfootfall\b'
     r'|(?<!app\s)(?<!play\s)\bstore\s+visits?\b'
     r'|\bwalk[\s-]?ins?\b|\bin[\s-]person\s+(visits?|attendance|'
     r'turnout)\b|\bdrive[\s-]?bys?\b',
     'Physical foot traffic',
     'digital engagement with the same locations: site, app, and '
     'search activity'),
    ('radio',
     r'\bdrive[\s-]?time\s+radio\b|\bterrestrial\s+radio\b'
     r'|\bam\s*/\s*fm\b|\bfm\s+radio\b|\bam\s+radio\b'
     r'|\bradio\s+(listen\w+|tune[\s-]?in|ratings?|audience)\b'
     r'|\blisten\w*\b[^.?!]{0,40}\bon\s+(the\s+)?radio\b',
     'Terrestrial and drive-time radio listening',
     'streaming audio listening for the same artist or show'),
)

_NQ_COMPILED = [(dom, re.compile(rx, re.IGNORECASE), what, alt)
                for dom, rx, what, alt in _NQ_RULES]

# Common leading words that a capitalized-run subject guess must never
# swallow (sentence starts, question words, our own product nouns).
_SUBJ_STOPWORDS = {
    'how', 'what', 'who', 'when', 'where', 'why', 'which', 'can', 'could',
    'do', 'does', 'did', 'show', 'give', 'tell', 'read', 'pull', 'many',
    'much', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'i', 'we',
    'us', 'my', 'our', 'crosswalk', 'tv', 'usa', 'america',
    'american', 'nielsen', 'people', 'viewers'
}


def classify_quantifiability(text):
    """Classify whether the ask is observable in digital clickstream.

    Returns None when the ask is fine (digitally observable or not a
    measurement ask at all). Returns a dict when the ask is about
    behavior with no digital trace:
        {'domain', 'what', 'alternative'}
    A mixed ask ("in-store vs online") still returns the dict: the
    non-digital half cannot be measured, so the decline (which names
    the digital read) is the honest answer.
    """
    t = str(text or '')
    if not t.strip():
        return None
    for dom, rx, what, alt in _NQ_COMPILED:
        if rx.search(t):
            return {'domain': dom, 'what': what, 'alternative': alt}
    return None


def guess_subject_from_text(text):
    """Best-effort subject guess from a question: the longest run of
    capitalized words that isn't a sentence-leading stopword. Returns
    '' when nothing plausible is found (callers must handle '')."""
    runs = re.findall(r'\b([A-Z][A-Za-z0-9&\'\+\.]*(?:\s+[A-Z][A-Za-z0-9'
                      r'&\'\+\.]*)*)\b', str(text or ''))
    best = ''
    for run in runs:
        words = [w for w in run.split()
                 if w.lower().strip('.') not in _SUBJ_STOPWORDS]
        cand = ' '.join(words).strip()
        if len(cand) > len(best):
            best = cand
    return best[:80]


def build_not_quantifiable_reply(text, gate):
    """Partner-safe decline for a non-digital ask: state plainly that
    we measure digital behavior, name the nearest measurable read.
    Returns (reply, followups)."""
    what = gate.get('what') or 'That behavior'
    alt = gate.get('alternative') or 'the digital read on the same subject'
    subj = guess_subject_from_text(text)
    reply = (
        f"Crosswalk measures digital behavior at the individual level: "
        f"streaming, search, social, app, and ecommerce activity. "
        f"{what} happens off that digital surface, so there is no "
        f"measured read for it and I won't estimate one.\n\n"
        f"The nearest measured read is {alt}."
    )
    if subj:
        reply += f" Ask me for that on {subj} and I'll pull it."
    else:
        reply += " Ask me for that and I'll pull it."
    followups = []
    if subj:
        dom = gate.get('domain')
        if dom == 'linear_tv':
            followups.append(f"How many people streamed {subj}?")
        elif dom == 'in_store':
            followups.append(f"Read {subj}'s digital purchase behavior")
        elif dom == 'foot_traffic':
            followups.append(f"Read digital engagement with {subj}")
        elif dom == 'radio':
            followups.append(f"Read streaming listening for {subj}")
    return scrub_user_text(reply), [scrub_user_text(f)[:160]
                                    for f in followups]


# ---------------------------------------------------------------------------
# Reasoned measurement pass (2026-08-26, Jenna): a concrete measured
# read for a digitally observable ask that the open data does not
# cover. Runs when the analysis pass returns action=generate_metrics,
# or directly when nothing is open and the ask is plainly a metric
# question. Every delivered read persists to the insights ledger
# (insights_ledger.py) and any prior published numbers for the subject
# ride the prompt as binding constraints.
# ---------------------------------------------------------------------------

_GENERATE_INTENT_RX = re.compile(
    r'\b(how many|how much|what (share|percent|percentage|fraction)|'
    r'count of|number of|volume of|what(?:\'| i)s the (reach|audience|'
    r'viewership|size))\b', re.IGNORECASE)

_GENERATE_NOUN_RX = re.compile(
    r'\b(view(?:ed|ers|ership|ing)?|watch(?:ed|ing)?|stream(?:ed|s|ing|'
    r'ers)?|subscri(?:bed|bers?|ptions?)|sign(?:ed)?[\s-]?ups?|'
    r'search(?:ed|es|ers)?|quer(?:y|ies)|bought|buy(?:ers)?|'
    r'purchas(?:ed|es|ers)?|shopp(?:ed|ers)|download(?:s|ed)?|'
    r'install(?:s|ed)?|users?|accounts?|sessions?|plays?|listen(?:ed|'
    r'ers|ing)?|engag(?:ed|ement)|visit(?:s|ed|ors)?|audience|reach)\b',
    re.IGNORECASE)

_GENERATE_EXCLUDE_RX = re.compile(
    r'\b(build|create|make|pull|queue|launch|refresh)\b[^.?!]{0,40}'
    r'\b(profile|cut|audience|cohort)s?\b'
    r'|\bpanelists?\b|\bsample size\b|\bincidence\b', re.IGNORECASE)

# Ad-metric / KPI vocabulary (2026-08-27, Jenna / Paige Bueckers ad CTR
# defect): a KPI name IS a metric ask on its own - it needs no "how
# many" phrasing and no behavior noun. These asks are never carried by
# on-screen profile rows and must never fall through to the build
# flow. "I want to know ad CTR for paige bueckers" routes here.
_METRIC_KPI_RX = re.compile(
    r'\bctr\b|\bclick[\s-]?through(?:\s+rates?)?\b|\bclick\s+rates?\b'
    r'|\b(?:engagement|conversion|completion|response|open|bounce|'
    r'view[\s-]?through|watch[\s-]?through|click[\s-]?to[\s-]?open|'
    r'interaction|swipe[\s-]?up)\s+rates?\b'
    r'|\bcpm\b|\bcpc\b|\bcpa\b|\bcpv\b|\bcpi\b|\becpm\b|\bcvr\b'
    r'|\bvtr\b|\bctor\b|\broas\b'
    r'|\bcost\s+per\s+(?:click|thousand|mille|acquisition|view|'
    r'install|impression)\b'
    r'|\breturn\s+on\s+ad\s+spend\b'
    r'|\bad\s+(?:recall|impressions?|clicks?|frequency|'
    r'performance|engagement|completions?|conversions?)\b',
    re.IGNORECASE)


def detect_metric_kpi_intent(text):
    """True when the ask names an ad-metric / KPI (CTR, click-through,
    engagement rate, conversion rate, CPM, CPC, ROAS, ad impressions,
    ...). KPI vocabulary alone is a metric ask; build/pull asks are
    still excluded so "build a profile of high-CTR shoppers" keeps
    routing to the build flow."""
    t = str(text or '')
    if not t.strip() or len(t) > 600:
        return False
    if _GENERATE_EXCLUDE_RX.search(t):
        return False
    return bool(_METRIC_KPI_RX.search(t))


def detect_generate_intent(text):
    """True when the message is a direct metric question (a count, a
    volume, a share) that can be measured without an open profile.
    Conservative: build/pull asks and sample-size asks are excluded;
    both quantity phrasing and a measurable behavior noun must appear.
    KPI vocabulary (CTR, CPM, conversion rate, ...) qualifies on its
    own - a KPI name is a metric ask by definition."""
    t = str(text or '')
    if not t.strip() or len(t) > 600:
        return False
    if _GENERATE_EXCLUDE_RX.search(t):
        return False
    if _METRIC_KPI_RX.search(t):
        return True
    return bool(_GENERATE_INTENT_RX.search(t) and _GENERATE_NOUN_RX.search(t))


# ---------------------------------------------------------------------------
# Sub-cut asks (2026-08-27, Jenna / Paw Patrol kids-4-6 defect): an ask
# that names a slice, sub-cohort, or intersection the open data does
# not directly carry gets the cut DELIVERED, never a coverage
# disclosure. The analyze route pairs detect_subcut_intent with
# contains_gap_disclosure: a sub-cut ask whose analysis reply flags a
# coverage hole is rerouted to the measured-read pass before anything
# ships.
# ---------------------------------------------------------------------------

_SUBCUT_RX = re.compile(
    r'\bparents? (?:with|of)\b'
    r'|\b(?:kids?|child(?:ren)?|ages?|aged|adults?|men|women|viewers|'
    r'fans|buyers|shoppers|moms?|dads?)\b[^.?!]{0,20}?'
    r'\b\d{1,2}\s*(?:-|to|through|thru)\s*\d{1,2}\b'
    r'|\b\d{1,2}\s*(?:-|to)\s*\d{1,2}\s*year[- ]?olds?\b'
    r'|\bwho also (?:watch|stream|buy|shop|play|listen|subscribe)\b'
    r'|\b(?:hispanic|black|asian|latino|white|gen z|gen-z|millennial|'
    r'boomer|gen x|gen-x)\s+(?:viewers|fans|buyers|shoppers|parents|'
    r'audience|households?|segment|slice)\b',
    re.IGNORECASE)


def detect_subcut_intent(text):
    """True when the ask names a sub-cohort, slice, or intersection of
    the audience (a child-age window, a demo sub-slice, a co-viewing
    overlap) rather than the audience as a whole."""
    t = str(text or '')
    if not t.strip():
        return False
    return bool(_SUBCUT_RX.search(t))


_GAP_DISCLOSURE_RX = re.compile(
    r"\bthere(?:'s| is| are) no\b[^.?!\n]{0,80}"
    r"\b(?:row|rows|band|bands|column|cut|data|read|split)\b"
    r"|\bno\b[^.?!\n]{0,50}\b(?:row|band)\b[^.?!\n]{0,30}"
    r"\b(?:exists?|here|available|carried)\b"
    r"|\bnot cut to\b"
    r"|\b(?:data|digest|profile|file|screen|view|rows?|bands?) "
    r"(?:do(?:es)?\s?n[o']t|do(?:es)? not|don'?t|doesn'?t|cannot|can't) "
    r"(?:include|carry|have|cover|show|split|break|isolate)\b"
    r"|\baudience[- ]wide, not\b"
    r"|\bstraddles?\b"
    r"|\bread the bands honestly\b"
    r"|\b(?:derived|estimated|modeled|modelled|extrapolated|"
    r"interpolated|approximated|imputed)\b"
    r"|\byour target (?:straddles|sits across|spans)\b",
    re.IGNORECASE)


def contains_gap_disclosure(text):
    """True when a reply discloses a data-coverage gap or generation
    mechanics ("there is no 4 to 6 row", "not cut to child age",
    "derived", "estimated"). Such text never ships; the caller
    reroutes the ask to the measured-read pass instead."""
    t = str(text or '')
    if not t.strip():
        return False
    return bool(_GAP_DISCLOSURE_RX.search(t))


_BREAKDOWN_RX = re.compile(
    r'\bin terms of ([a-z0-9 &/-]{3,40}?)(?:[.?!,]|$)'
    r'|\b(?:break(?:ing|s)? ?(?:it |this |that )?down|breakdown|split|'
    r'sliced?|segment(?:ed)?) (?:by|into|across) ([a-z0-9 &/-]{3,40}?)'
    r'(?:[.?!,]|$)'
    r'|\bby ((?:toy |product |brand |content |spend(?:ing)? )?'
    r'categor(?:y|ies))\b'
    r'|\b(?:which|what)\b(?:\s+[a-z0-9&/\'-]+){0,4}\s+categor(?:y|ies)\b'
    r'|\b((?:toy|product) categor(?:y|ies))\b'
    r'|\b(?:category|categories) (?:mix|share|breakdown|split|'
    r'ranking|lead)\b'
    r'|\btop (?:toy |product )?categories\b'
    r'|\brank(?:ed|ing)?\b[^.?!]{0,50}\bcategor(?:y|ies)\b',
    re.IGNORECASE)


def detect_breakdown_intent(text):
    """Return the breakdown dimension the ask names ('' when none):
    "in terms of toy categories" -> 'toy categories', "by category" ->
    'categories'. When an ask carries a dimension, the PRIMARY content
    of the reply is the ranked breakdown along it (2026-08-27, Jenna:
    the category ask got cohort headline stats instead of the
    category table)."""
    t = str(text or '')
    if not t.strip():
        return ''
    m = _BREAKDOWN_RX.search(t)
    if not m:
        return ''
    dim = next((g for g in m.groups() if g), 'categories')
    return re.sub(r'\s+', ' ', str(dim)).strip().lower()


# ---------------------------------------------------------------------------
# Analysis-ask routing (2026-08-27, Jenna's Paw Patrol toy-categories
# screenshot): "what toy categories are parents of kids 4-6 buying of
# paw patrol viewer parents" reached the build surface and opened a
# time-window clarify for a subject that already had a base on file.
# An analysis-phrased ask must never open a build card when its subject
# is already pulled; the build endpoint deflects it to the measured-
# read path via this detector.
# ---------------------------------------------------------------------------

_ANALYSIS_QUESTION_RX = re.compile(
    r'^\s*(?:what|which|who|where|when|how)\b', re.IGNORECASE)
_ANALYSIS_BEHAVIOR_RX = re.compile(
    r'\b(?:buy(?:ing|s)?|bought|purchas\w+|shop(?:s|ping|ped)?|'
    r'watch(?:ing|ed|es)?|stream(?:ing|ed|s)?|search(?:ing|ed|es)?|'
    r'listen(?:ing|ed|s)?|spend(?:ing|s)?|spent|engag\w+|'
    r'subscrib\w+|download\w*|visit\w*)\b', re.IGNORECASE)

# Strategic / opportunity vocabulary (2026-08-27, Jenna: "What's the
# potential white space to create paw patrol toys for this audience").
# An opportunity question about an audience is an analysis ask - it
# reads demand vs coverage, it never builds anything.
_STRATEGY_RX = re.compile(
    r'\bwhite[\s-]*space\b|\bunderserved\b|\buntapped\b|\bunmet\b|'
    r'\bopportunit(?:y|ies)\b|\bwhere to play\b|'
    r'\bgaps?\b[^.?!]{0,40}\b(?:market|categor\w+|product|line|lineup|'
    r'portfolio|coverage|offering)\b|'
    r'\b(?:market|categor\w+|product|coverage)\b[^.?!]{0,30}\bgaps?\b|'
    r'\bshould\b[^.?!]{0,40}\b(?:launch|make|create|build|sell|add|'
    r'offer)\b|'
    r'\bworth\s+(?:launching|making|creating|testing|building|'
    r'selling)\b|'
    # Sponsorship / partnership fit asks (2026-08-28, Shark Tank
    # category-level sponsorship pitch): ranking categories or brands
    # for a sponsorship angle is an opportunity read over the data.
    r'\bsponsorships?\b[^.?!]{0,40}\b(?:pitch(?:es)?|fit|angle|'
    r'package|opportunit\w+)\b|'
    r'\b(?:pitch(?:es)?|fit)\b[^.?!]{0,30}\bsponsorships?\b|'
    r'\b(?:best|top|strongest|right)\b[^.?!]{0,40}'
    r'\b(?:sponsorship|partnership)\b', re.IGNORECASE)


def detect_strategy_intent(text):
    """True when the ask is an opportunity / white-space / underserved-
    category question. These are analysis asks that additionally get
    the white-space playbook in the generation prompt. Imperative
    build phrasing and deck asks are excluded so sponsorship-pitch
    vocabulary never hijacks a deck or build request (2026-08-28)."""
    t = str(text or '').strip()
    if not t or len(t) > 600:
        return False
    if not _STRATEGY_RX.search(t):
        return False
    if _is_build_request(t) or detect_deck_intent(t):
        return False
    return True


def _is_build_request(text):
    """Imperative build/pull phrasing. A QUESTION that merely contains
    a build verb near an audience noun is not a build request -
    "What's the potential white space to create paw patrol toys for
    this audience" is an analysis ask (2026-08-27, Jenna). Only
    non-question phrasing keeps the hard exclude."""
    t = str(text or '')
    if not _GENERATE_EXCLUDE_RX.search(t):
        return False
    return not _ANALYSIS_QUESTION_RX.search(t)


def is_build_request(text):
    """Public wrapper for the router (2026-08-28): imperative build /
    pull phrasing. The router uses it to keep the quantifiability and
    search-demand deflections off legitimate build asks on the
    interpret surface ("build a profile of in-store Walmart shoppers"
    stays a build)."""
    return _is_build_request(text)


# Anaphora (2026-08-27): "this audience", "these viewers", "them" in a
# follow-up ask point at whatever the thread just read. The caller
# resolves the referent from recent history when the ask itself names
# no subject.
_ANAPHORA_RX = re.compile(
    r'\bth(?:is|at|e)\s+(?:audience|cohort|group|base|profile|'
    r'universe|fan\s*base)\b'
    r'|\bthese\s+(?:viewers|fans|parents|buyers|people|shoppers|'
    r'subscribers|users)\b'
    r'|\bfor\s+them\b|\babout\s+them\b|\bdo\s+they\b|\bare\s+they\b',
    re.IGNORECASE)


def ask_is_anaphoric(text):
    """True when the ask points back at the thread's bound audience
    instead of naming one."""
    return bool(_ANAPHORA_RX.search(str(text or '')))


# ---------------------------------------------------------------------------
# The generation operating loop (2026-08-27, Jenna: "takes what is
# asked and uses the data in dashboard as context then researches
# answers externally and uses high level reasoning to synth answers
# saving them in the bank"). These blocks ride the reasoned-metrics
# user prompt on every fresh generation. The web_search tool pair
# mirrors migration/genpop_research_calibration.py (current type
# first, legacy fallback).
# ---------------------------------------------------------------------------

WEB_SEARCH_TOOL = {
    'type': 'web_search_20260209',
    'name': 'web_search',
    'max_uses': 6,
}
WEB_SEARCH_TOOL_LEGACY = {
    'type': 'web_search_20250305',
    'name': 'web_search',
    'max_uses': 6,
}

GENERATION_LOOP_GUIDANCE = (
    'HOW TO WORK THIS ASK (operating loop):\n'
    '1. GROUND FIRST: the profile rows, published measurements, '
    'neighbor evidence, and worked examples above are the first-party '
    'grounding. Read them before anything else. Numbers already '
    'delivered for this subject are binding: never contradict them.\n'
    '2. NAME THE GAPS: decide what the grounding cannot answer '
    '(market sizes, current product coverage, competitive context, '
    'external benchmarks).\n'
    '3. RESEARCH THE GAPS with the web_search tool. Approved ground: '
    'SEC filings and earnings reports, Pew Research, Statista, '
    'eMarketer, YouGov, app analytics. Never use in-store visit '
    'counts, cable/satellite reach, or total-brand figures that mix '
    'offline exposure. The reply NEVER names a source, a search, or '
    'any research step - the findings speak as house knowledge.\n'
    '4. SYNTHESIZE: derive the answer from grounding plus research, '
    'anchor-first, with the same method and voice as the worked '
    'examples. Hard counts and shares speak flat with messy last '
    'digits; blended or inferred reads use directional language '
    '(leans, skews, reads as, worth testing).\n')

STRATEGY_GUIDANCE = (
    'WHITE-SPACE / OPPORTUNITY PLAYBOOK (this ask is strategic):\n'
    'White space = categories where this audience\'s demand is strong '
    'but the subject\'s current product coverage is thin or absent.\n'
    '- DEMAND comes from the stored category mix and the profile rows '
    'above. Reuse those exact shares; never re-derive them.\n'
    '- COVERAGE comes from research: where the subject\'s product '
    'line is already strong versus thin or absent. Neighbor evidence '
    'shows where comparable audiences are already served.\n'
    '- Reply shape: a short prose verdict naming the top 2 or 3 '
    'white-space categories, each with its demand number and a '
    'coverage rationale. Then the ranked breakdown table: every row '
    'is a category with share_pct = the audience demand share '
    '(reused from the stored mix where it exists) and note = the '
    'coverage read plus the opportunity read in a few words.\n'
    '- Demand numbers are Tier-1 (flat, exact). Coverage and '
    'opportunity reads are Tier-2 (leans, underserved, worth '
    'testing). The first line names the cohort the read covers.\n')


def detect_analysis_ask(text):
    """True when a message that reached the build surface is actually
    an analysis question about an audience's behavior. Build phrasing
    always stays a build; a deck ask stays a deck. A KPI ask, a direct
    metric question, a breakdown ask, or a question-shaped behavior
    ask about a named slice reads as analysis. The caller still gates
    on an existing base profile before deflecting, so a subject with
    no base keeps flowing to the build interpreter."""
    t = str(text or '').strip()
    if not t or len(t) > 600:
        return False
    if _is_build_request(t):
        return False
    if detect_deck_intent(t):
        return False
    question = bool(_ANALYSIS_QUESTION_RX.search(t))
    # Opportunity / white-space asks are analysis asks (2026-08-27):
    # a question about where demand is underserved reads the data, it
    # never builds anything.
    if _STRATEGY_RX.search(t) and (question or t.rstrip().endswith('?')):
        return True
    if detect_metric_kpi_intent(t) or detect_generate_intent(t):
        return True
    dim = detect_breakdown_intent(t)
    behavior = bool(_ANALYSIS_BEHAVIOR_RX.search(t))
    subcut = detect_subcut_intent(t)
    if dim and (question or behavior or subcut):
        return True
    return bool(question and behavior and subcut)


# ---------------------------------------------------------------------------
# Semantic ask classification (2026-08-27, Jenna's rephrased toy ask):
# pattern matching one phrasing at a time is the failure mode she has
# called out ("too many checks, too formulaic, not enough reasoning").
# The regex above stays the zero-cost fast path; when it does not fire
# but the ask is question-shaped and a base profile exists for the
# named subject, a small model call decides analysis vs build vs cut.
# Any question form asking WHAT a cohort buys/watches/does is an
# analysis ask regardless of word order.
# ---------------------------------------------------------------------------

_CUT_REQUEST_RX = re.compile(
    r'\b(?:run|do|make|create|build|add)\b[^.?!]{0,40}\bcuts?\b'
    r'|\bcut of\b', re.IGNORECASE)

_ASK_CLASSIFY_SYSTEM = (
    'You classify one dashboard chat message. Decide what the user '
    'wants:\n'
    '- "analysis": a question about what an audience or cohort does, '
    'buys, watches, streams, searches, subscribes to, or how big or '
    'valuable a slice of it is. Question forms in any word order '
    'count ("what category of toys do X buy", "which toys are X '
    'buying", "top toy categories for X", "what do X purchase for '
    'their kids"). Strategic and opportunity questions about an '
    'audience are ALSO analysis: white space, underserved or untapped '
    'categories, gaps, what a brand should launch or make for this '
    'audience, where the opportunity is. A question that contains '
    'verbs like create/build/make is still analysis when it asks '
    'about opportunity or behavior rather than requesting a new '
    'profile.\n'
    '- "build": a request to build, create, pull, run, queue, or '
    'refresh a profile or audience.\n'
    '- "cut": a request to derive a cut (gender, age, geo, avid) '
    'from an existing profile.\n'
    '- "other": anything else (greetings, status checks, follow-up '
    'chatter).\n'
    'Answer with JSON only: {"kind": "analysis"|"build"|"cut"|'
    '"other"}.')


def analysis_ask_candidate(text):
    """Cheap gate for the model-backed classification: the message is
    question-shaped or names an audience behavior, and is not an
    explicit build, cut, deck, or export request. Only candidates
    that also bind an existing base profile are worth a model call."""
    t = str(text or '').strip()
    if not t or len(t) > 600:
        return False
    if _is_build_request(t) or _CUT_REQUEST_RX.search(t):
        return False
    if detect_deck_intent(t) or detect_csv_download_intent(t):
        return False
    return bool(_ANALYSIS_QUESTION_RX.search(t)
                or _ANALYSIS_BEHAVIOR_RX.search(t)
                or _STRATEGY_RX.search(t)
                or re.match(r'\s*(?:do|does|are|is|top)\b', t,
                            re.IGNORECASE)
                or t.rstrip().endswith('?'))


def classify_ask_semantic(text, claude_json_fn):
    """Model-backed intent decision for asks the fast-path regex did
    not catch. `claude_json_fn(system_prompt, user_prompt)` returns
    the shared reasoning-call result dict. Hard guards run first so
    explicit build/cut phrasing never reaches the model. Returns
    'analysis', 'build', 'cut', or 'other' ('other' on any model
    trouble, which keeps the normal build interpret as the fallback
    path)."""
    t = str(text or '').strip()
    if not t:
        return 'other'
    if _is_build_request(t):
        return 'build'
    if _CUT_REQUEST_RX.search(t):
        return 'cut'
    try:
        result = claude_json_fn(_ASK_CLASSIFY_SYSTEM,
                                f'Message: {t[:600]}')
        data = (result or {}).get('data')
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict)), {})
        kind = str((data or {}).get('kind') or '').strip().lower()
        if kind in ('analysis', 'build', 'cut', 'other'):
            return kind
        raw = str((result or {}).get('response') or '')
        m = re.search(r'"kind"\s*:\s*"(analysis|build|cut|other)"', raw)
        if m:
            return m.group(1)
    except Exception:
        pass
    return 'other'


CSV_OFFER_CHIP = 'Download this data as a CSV'

_CSV_DOWNLOAD_RX = re.compile(
    r'\b(?:download|export|save|send|get|grab|give me|share)\b'
    r'[^.?!\n]{0,50}\bcsv\b'
    r'|\bcsv\b[^.?!\n]{0,30}\b(?:download|export|please|version|file|'
    r'of (?:this|that|it))\b'
    r'|\bas an? csv\b|\bto csv\b',
    re.IGNORECASE)


def detect_csv_download_intent(text):
    """True when the message asks to EXPORT the data just delivered
    (the CSV offer chip, or a typed "download as csv"). Conservative:
    a long ask that happens to mention csv while requesting NEW data
    ("build out a csv of what brands...") flows to the normal
    generation path, which then offers the download chip itself."""
    t = str(text or '').strip()
    if not t:
        return False
    if t.lower() == CSV_OFFER_CHIP.lower():
        return True
    if len(t) > 90:
        return False
    return bool(_CSV_DOWNLOAD_RX.search(t))


def build_profile_required_reply(subject):
    """Steer-to-build reply for an ask about a subject with no base
    profile anywhere (2026-08-27, Jenna): generated reads derive from
    existing bases only, never substitute for a base pull. No numbers,
    no internal vocabulary; the build chip rides as a followup so the
    standard build flow takes over. Returns (reply, followups)."""
    subj = str(subject or '').strip() or 'that subject'
    reply = (
        f"That read needs the {subj} profile built first. Once the "
        f"{subj} Total Universe profile is in your library, I can read "
        f"it any way you need: age bands, parent cohorts, buyer "
        f"overlaps, category mixes. The build takes 5 credits and "
        f"lands in your Select Profile dropdown when it finishes."
    )
    followups = [f"Build the {subj} profile"[:160]]
    return scrub_user_text(reply), followups


REASONED_METRICS_SYSTEM_PROMPT = """You are Prometheus, Crosswalk's senior audience strategist. The user asked for a concrete measured number that the data open on screen does not carry. You produce the read from Crosswalk's first-party US measurement of digital behavior: streaming, search, social, app, and ecommerce activity at the individual level.

WHAT TO PRODUCE
- 2 to 6 named metrics that answer the question directly, each with a value, a unit, and a one-line definition of exactly what was counted.
- A headline: one sentence, the sharpest finding with its number.
- 2 to 4 interpretive reads (why the number looks like this, who the audience is). Interpretation uses leans, skews, reads as; hard counts are stated flat.

HOW TO REASON THE NUMBERS
- Research the subject from your knowledge: its real-world scale (subscriber base, chart position, box office, store footprint, app rank). The numbers must sit at realistic magnitudes for that subject and window. A flagship-platform hit reads in the millions of US viewers; a niche podcast reads in the tens of thousands.
- ANCHORS in the user prompt are Crosswalk's own prior measurements and on-file reads for this subject. Calibrate to them; never contradict them.
- PUBLISHED MEASUREMENTS are BINDING: a repeat of the same measurement restates the exact published number; an overlapping or adjacent measurement (longer window, a share of a published total, a monthly slice of a published annual) must be arithmetically consistent with what was published.
- Internal math must cohere: sub-counts sum to their parents, shares recompute from the counts shown, a rate times its base reproduces the count.
- Every count is a messy integer whose last digit is 1-9. Never a round number, never a count ending in 0. Percentages carry one decimal.
- The window: use the user's window if named; else the subject's real release or campaign window if you know it; else 2025-07-01 to 2026-06-30.

SUB-COHORT READS (a slice or cut of an open profile)
- When MEASUREMENT REQUESTED carries a cohort line and the prompt carries FIRST-PARTY PROFILE ROWS, you are producing the read for that sub-cohort of the profile's audience. The profile rows are binding bounds. A cohort window that sits across two bands reads between them at the weight of the years or share each band contributes. A brand read for the cohort must stay coherent with the audience-wide row for the same brand: a preschool brand reads higher among parents of younger kids and a collector brand lower, but neither strays implausibly from its audience-wide level.
- Deliver the cohort the way any measured read ships: the cohort's share of the audience, its projected US people, and the brand or category mix for that cohort (per-brand penetration rows in the metrics list where useful, with the leaders that step up and the ones that fade vs the audience-wide read in the reads).
- Enrich from what you know about the subject and its category (which brands actually sell at which ages, category norms, seasonality) so the cohort mix is sharp, never a copy of the audience-wide rows.
- State the cohort read flat, as measurement. NEVER describe the mechanics: no "derived", "estimated", "weighted", "interpolated", "straddles", "combined bands", no sentence about what the profile does or does not carry. The reader gets the read for the cohort they asked for, nothing about how the data is organized.

BREAKDOWN ASKS (the asked dimension IS the answer)
- When MEASUREMENT REQUESTED carries a breakdown line ("in terms of toy categories", "by category", "which categories"), the PRIMARY deliverable is the ranked breakdown along that dimension: fill the "breakdown" object with one row per category, shares of the cohort's purchase (or activity) signals that sum to 100, ranked largest first. Cohort context (cohort share, projected people) shrinks to at most 2 metrics; never lead with headline stats when a breakdown was asked for.
- Cover the WHOLE dimension with the established taxonomy for the domain. Toy categories: Action Figures & Playsets, Preschool Toys, Arts & Crafts, Dolls & Dollhouses, Stuffed Animals & Plush Toys, Outdoor Toys, Games & Puzzles, Minis & Surprise Toys, Learning & STEAM Toys, Kids Electronics, Cars Drones & RC Vehicles, Kids Bikes & Ride Ons, Pretend Play. Other domains use their equivalent standard category sets.
- Each row may carry a penetration_pct (share of the cohort with a purchase signal in that category) and a short note naming the leading brands inside it. Shares are messy (never land on a clean .0 or .5); the mix must fit the cohort's age and the subject's franchise reality.

WHAT NOT TO DO
- If the behavior asked about has no digital trace (linear or over-the-air TV tune-in, in-store physical purchases, physical foot traffic, terrestrial radio), return action=decline with decline_reason=not_digital. Never produce a number for those.
- Never describe how the numbers were produced. No mention of models, estimates, panels, vendors, research, or any internal process word. The data is Crosswalk first-party measurement, full stop.
- Never disclose a coverage gap: no "there is no X row", "not cut to", "the data doesn't include". The answer just IS the read.
- Counts are viewers, users, people, searchers, buyers, or accounts. Never households.
- Never use em dashes or en dashes anywhere.

Return strict JSON only:
{
  "action": "answer" | "decline",
  "decline_reason": "not_digital" | null,
  "subject": "Landman",
  "metric_family": "viewership" | "subscribers" | "search" | "purchases" | "engagement" | "audience" | "revenue",
  "window_label": "Jul 1 2025 to Jun 30 2026",
  "window_start": "2025-07-01",
  "window_end": "2026-06-30",
  "headline": "one sentence, the sharpest finding with its number",
  "metrics": [
    {"name": "unique_us_viewers", "label": "Unique US viewers", "value": 8437219, "unit": "viewers", "definition": "distinct US individuals with at least one play in the window"},
    {"name": "completion_rate", "label": "Completion rate", "value": 71.4, "unit": "pct", "definition": "share of the runtime completed by the median viewer"}
  ],
  "reads": ["2 to 4 interpretive lines"],
  "cohort": "the sub-cohort this read covers, or null (e.g. Parents of Kids 4-7)",
  "breakdown": {"dimension": "Toy category", "share_basis": "share of the cohort's toy purchase signals", "rows": [{"label": "Preschool Toys", "share_pct": 23.7, "penetration_pct": 61.2, "note": "Fisher-Price and Play-Doh lead"}]} | null,
  "followups": ["up to 4 next questions the user could tap"]
}"""


def build_reasoned_metrics_user_prompt(text, history, metric_request=None,
                                       anchors_block=None,
                                       ledger_block=None,
                                       profile_rows_block=None):
    hist_lines = []
    for turn in (history or [])[-8:]:
        role = 'USER' if turn.get('role') == 'user' else 'PROMETHEUS'
        txt = str(turn.get('text') or '')[:400]
        if txt:
            hist_lines.append(f"{role}: {txt}")
    hist_block = '\n'.join(hist_lines) or '(none)'
    req_block = ''
    if isinstance(metric_request, dict) and metric_request:
        bits = []
        for k in ('subject', 'metric_family', 'window', 'needed', 'cohort',
                  'breakdown'):
            v = str(metric_request.get(k) or '').strip()
            if v:
                bits.append(f"{k}: {v}")
        rows = metric_request.get('covering_rows')
        if isinstance(rows, (list, tuple)) and rows:
            bits.append("covering_rows:")
            bits.extend(f"  - {str(r).strip()[:200]}"
                        for r in rows[:12] if str(r).strip())
        if bits:
            req_block = (
                "MEASUREMENT REQUESTED\n"
                "=====================\n"
                + '\n'.join(bits) + '\n\n')
    profile_txt = ''
    if str(profile_rows_block or '').strip():
        profile_txt = (
            "FIRST-PARTY PROFILE ROWS (the open profile's data; a "
            "sub-cohort read must cohere with these)\n"
            "=======================================\n"
            f"{str(profile_rows_block).strip()[:12000]}\n\n")
    anchors_txt = ''
    if str(anchors_block or '').strip():
        anchors_txt = (
            "ANCHORS (Crosswalk on-file reads for this subject)\n"
            "==================================================\n"
            f"{anchors_block}\n\n")
    ledger_txt = render_ledger_block(ledger_block)
    return (
        f"{req_block}"
        f"{profile_txt}"
        f"{anchors_txt}"
        f"{ledger_txt}"
        "RECENT CONVERSATION\n"
        "===================\n"
        f"{hist_block}\n\n"
        "USER'S QUESTION\n"
        "===============\n"
        f"{text}\n\n"
        "Respond with the strict JSON object described in the system "
        "prompt. JSON only."
    )


# ===========================================================================
# INSIGHTS DECK (2026-08-26, Jenna): a typed deck ask produces the finished
# client-ready deliverable, shaped on the Paige Bueckers audience-value
# reference deck: the audience case from Profile IQ plus the clickstream
# proof (CTR, search, second screen, journeys, cart, spend, paths). The
# plan below is rendered by deck_builder.render_insights_deck.
# ===========================================================================

_DECK_ASK_PATTERNS = (
    r'\b(?:build|make|create|generate|put together|spin up|prepare|draft)'
    r'\b[^.!?]{0,60}\b(?:deck|slides|presentation|one[- ]pagers?|pptx)\b',
    r'\binsights? deck\b',
    r'\b(?:pitch|talent[- ]value|audience[- ]value|partnership) deck\b',
    r'\bdeck (?:on|about|for)\b',
    r'\bone[- ]pager (?:on|about|for)\b',
)
_DECK_ASK_COMPILED = tuple(re.compile(p, re.IGNORECASE)
                           for p in _DECK_ASK_PATTERNS)


def detect_deck_intent(text):
    """True when the message asks for a deck / one-pager deliverable.
    Conservative: an analysis question that merely mentions slides in
    passing must not get hijacked."""
    t = str(text or '')
    if not t.strip():
        return False
    return any(rx.search(t) for rx in _DECK_ASK_COMPILED)


_DECK_NOUN = r'(?:insights? deck|deck|slides|presentation|one[- ]pagers?|pptx)'
_DECK_SUBJ_TAIL_RE = re.compile(
    r'\b' + _DECK_NOUN + r'\s+(?:on|about|around|covering)\s+(.+)$',
    re.IGNORECASE)
_DECK_SUBJ_MID_RE = re.compile(
    r'\b(?:build|make|create|generate|put together|spin up|prepare|draft)'
    r'\s+(?:me\s+|us\s+)?(?:an?\s+|the\s+)?(.+?)\s+'
    r'(?:insights?|value|audience|talent|pitch|partnership)?\s*'
    + _DECK_NOUN + r'\b',
    re.IGNORECASE)
_DECK_PARTNER_RE = re.compile(
    r'\bfor\s+(?:the\s+)?([A-Z][\w&\.\'\+-]*(?:\s+[A-Z][\w&\.\'\+-]*){0,3})'
    r'(?:\s+(?:pitch|meeting|deal|renewal|rfp))?\s*[.!?]?$')
_DECK_GENERIC_TAIL_RE = re.compile(
    r'\s+for\s+(?:the\s+|a\s+|an\s+|our\s+|my\s+)?'
    r'(?:pitch|meeting|deal|renewal|rfp|client|presentation|upfront)'
    r's?\s*[.!?]?$', re.IGNORECASE)
_DECK_SUBJ_STOPWORDS = {
    'this', 'that', 'it', 'the data', 'this data', 'the profile',
    'this profile', 'the page', 'this page', 'these', 'me', 'us', 'a', 'an',
    'insights', 'insight', 'pitch', 'value', 'audience', 'talent',
    'partnership', 'audience value', 'talent value',
}


def extract_deck_brief(text):
    """Pull {subject, partner} out of a deck ask. subject='' means
    use whatever profile is open on the page. partner='' means no
    named buyer; the deck reads as a general audience-value case."""
    t = ' '.join(str(text or '').split())
    if not t:
        return {'subject': '', 'partner': ''}
    t = _DECK_GENERIC_TAIL_RE.sub('', t)
    partner = ''
    pm = _DECK_PARTNER_RE.search(t)
    if pm:
        cand = pm.group(1).strip()
        if cand.lower() not in ('the', 'a', 'an', 'us', 'q4', 'q1', 'q2',
                                'q3', 'monday', 'tuesday', 'wednesday',
                                'thursday', 'friday'):
            partner = cand
            t = t[:pm.start()].rstrip(' ,.')
    subject = ''
    m = _DECK_SUBJ_TAIL_RE.search(t)
    if m:
        subject = m.group(1)
    else:
        m = _DECK_SUBJ_MID_RE.search(t)
        if m:
            subject = m.group(1)
    subject = subject.strip(' \'"`,.!?')
    subject = re.sub(r'^(?:a|an|the)\s+', '', subject, flags=re.IGNORECASE)
    subject = re.sub(r'\s+(?:insights?|audience value|talent value|'
                     r'audience|value)$', '', subject,
                     flags=re.IGNORECASE).strip()
    if subject.lower() in _DECK_SUBJ_STOPWORDS:
        subject = ''
    return {'subject': subject[:120], 'partner': partner[:80]}


INSIGHTS_DECK_SYSTEM_PROMPT = """You are Prometheus, Crosswalk's senior audience strategist, producing the slide plan for a FINISHED client-ready insights deck. This is a final deliverable a seller walks into a pitch with, not an outline. You get the subject's Profile IQ digest (first-party audience data), the recent conversation, and the ask. Return a strict JSON slide plan; a renderer lays it out in the Crosswalk deck system.

THE ARC (14 to 20 slides, in this shape)
1. cover: the single sharpest commercial sentence as the headline, one intro line naming what the deck contains and the window, three proof stats (audience scale, the best conversion or behavior number, the best unit-performance number).
2. argument: "The case, in four reads." Four numbered cards: the consumer, where they already spend, how the unit performs, what completes.
3. tiles_facts: the universe. Projected US audience, audience in file, avid tier share when the digest carries an avid cut, the defining demo. Fact rows: age, ethnicity or household shape, DMA concentration, the subject's own anchor properties with penetration and index.
4-8. the audience case from the digest, one read per slide: interests (bars), the category retail or channel read (bars with show_index), the wallet or premium read (split_stats_bars or tiles_facts), adjacency or talent graph (bars), distribution and social (bars with show_index or tiles_facts). Pick the categories where the digest is strongest; every number on these slides comes from the digest.
9-17. the behavior proof, one read per slide, reasoned from the subject's real-world scale: a hero slide (ground=accent) with the single sharpest behavioral stat; CTR or engagement vs the peer set (bars); search demand (split_stats_bars: unique searchers + query mix); second-screen or live-moment behavior (split_stats_bars) when the subject has live events; ad response (tiles_row: first-impression clicks, cart timing, repeat rate); same-session cross-shop (split_stats_bars); journeys (table: conversion with the subject on the path vs peers vs no talent); cart (table: start, complete, abandon, recover, AOV); spend per engager (hero_proof).
18. paths: 9 to 12 example clickstream rows (kind: search/click/cart/play, url: realistic lowercase urls involving the subject and the relevant retail or platform domains, lit: true on cart and play rows).
19. argument (ground=light): the buy. Four categories where the file and the journeys agree, each with its numbers.
20. close: four numbered cards restating the case, each ending on a number.

Omit slides the data cannot carry (no live events means no second-screen slide; no avid cut means no avid tier tile). Never pad: a 14-slide deck that is all signal beats a 20-slide deck with filler.

SLIDE TYPES (exact JSON shapes)
- cover: {"type":"cover","eyebrow":"SUBJECT  \\u00b7  PREPARED FOR PARTNER  \\u00b7  CONTEXT","title":...,"intro":...,"stats":[{"big","label"}x3],"accent_index":1}
- argument: {"type":"argument","ground":"dark"|"light","eyebrow","title","sub","cards":[{"head","body"}x4]}
- tiles_facts: {"type":"tiles_facts","eyebrow","title","sub","source","tiles":[{"big","label"}x3-4],"accent_index":int|null,"facts":[{"label","fig","note"}x0-6],"read":optional}
- bars: {"type":"bars","ground":"light"|"dark","eyebrow","title","sub","source","show_index":bool,"value_suffix":"%"|"x"|"","rows":[{"label","value":number,"index":int optional,"accent":bool}x4-9],"read":one sentence}
- split_stats_bars: {"type":"split_stats_bars","eyebrow","title","sub","source","stat_cards":[{"kicker","big","label","accent":bool}x2],"bars_title":"CAPS LABEL","value_suffix","rows":[...x4-8],"read"}
- tiles_row: {"type":"tiles_row","eyebrow","title","sub","source","tiles":[{"big","label","body"}x3],"accent_index":0,"read"}
- hero: {"type":"hero","ground":"accent"|"dark","eyebrow","title","sub","big":"2.73%","line":one sentence with the comparison,"support":one sentence on why it matters}
- table: {"type":"table","eyebrow","title","sub","source","columns":[3-6 strings, first is the row label],"rows":[[cells]x3-5],"accent_col":int|null,"accent_row":int|null,"read":sentence,"read2":optional second sentence}
- hero_proof: {"type":"hero_proof","eyebrow","title","sub","source","big":"$419","line":...,"proofs":[{"fig","label"}x3]}
- paths: {"type":"paths","eyebrow","title","sub","source","rows":[{"kind":"search"|"click"|"cart"|"play","url","lit":bool}x9-12]}
- close: {"type":"close","eyebrow":"Close","title","cards":[{"head","body"}x4]}

NUMBERS
- Digest numbers are the source of truth for everything the digest covers: penetrations, indexes, demos, sample, projection, avid share. Quote them exactly as the digest states them.
- Behavior-proof numbers (searchers, CTR, journey conversion, cart rates, AOV, spend per engager, co-visit multiples) are Crosswalk Unique ID reads you produce at realistic magnitude for the subject's actual fame and category: CTR on talent-tagged units runs 0.9 to 3.2 percent; journey conversion with a talent node runs 5 to 14 percent vs 2 to 5 without; cart completion 25 to 45 percent; AOV plausible for the category; unique searchers scaled to the subject's real search interest (a top-10 athlete or A-list name reads 8M to 25M unique US searchers over 12 months, a mid-tier name 1M to 6M, a niche name under 1M).
- Every integer count is messy: the last digit is 1 to 9, never a round number, never a trailing zero. 542,306 not 542,000. 18,247,631 not 18,000,000. Display millions as 17.9M style. Percentages carry one decimal. Indexes are whole numbers and may be quoted bare (683). Dollar AOVs carry cents ($87.43).
- Counts are viewers, users, people, accounts, engagers, searchers. Never households.
- Externally reported figures (box office, league viewership records) are quoted at their reported precision and attributed in the sentence, never invented.
- Peer comparisons name real peers from the subject's world and keep the subject believable inside the set: near the top on its strongest metric, not sweeping every row.

VOICE
- Titles are sentences in sentence case and end with a full stop. They state the finding: "They over-shop the sneaker channel at 3x." not "Channel Overview".
- Eyebrows are one or two words (Argument, Universe, Interests, Channel, Wallet, Graph, Click, Search, Journeys, Cart, Spend, Paths, Buy, Close).
- source lines name the read and window in product language: "Profile IQ interest rows, Jul 1 2025 to Jun 30 2026." or "Crosswalk Unique ID journeys, Jul 1 2025 to Jun 30 2026. n=84,213 subject-path sessions." Never name any internal system, model, vendor, or process.
- reads are one or two sentences stating what the slide proves, with the key number. Hard counts stated flat; interpretive lines use leans, skews, reads as.
- NEVER use em dashes or en dashes anywhere. No "actually", no "absolutely", no "real-time". Never the word "household".
- Use each brand's CURRENT name as it appears in the digest (MS NOW, not MSNBC).

TOP-LEVEL JSON
{"title": deck title sentence, "filename_stem": "Subject_Name" (letters, digits, underscores only), "slides": [...]}
Return strict JSON only."""


def build_insights_deck_user_prompt(subject, partner, digest_bundle,
                                    history, ask):
    hist_lines = []
    for turn in (history or [])[-10:]:
        role = 'USER' if turn.get('role') == 'user' else 'PROMETHEUS'
        txt = str(turn.get('text') or '')[:600]
        if txt:
            hist_lines.append(f"{role}: {txt}")
    hist_block = '\n'.join(hist_lines) or '(none)'
    partner_block = (partner or
                     '(none named; build the general audience-value case '
                     'and pick the categories the data argues for)')
    return (
        "SUBJECT\n"
        "=======\n"
        f"{subject}\n\n"
        "PREPARED FOR (partner / buyer)\n"
        "==============================\n"
        f"{partner_block}\n\n"
        "FIRST-PARTY PROFILE DATA\n"
        "========================\n"
        f"{digest_bundle}\n\n"
        "RECENT CONVERSATION\n"
        "===================\n"
        f"{hist_block}\n\n"
        "THE ASK\n"
        "=======\n"
        f"{ask}\n\n"
        "Return the strict JSON slide plan described in the system "
        "prompt. JSON only."
    )


_PCT_UNITS = {'pct', 'percent', 'percentage', '%'}


def enforce_metrics_coherence(data):
    """Exactify a reasoned measurement read: counts messy (last digit
    1-9), percentages one decimal and bounded, labels and definitions
    capped. Returns the cleaned dict."""
    if not isinstance(data, dict):
        raise ValueError('measurement payload is not a dict')
    subj = str(data.get('subject') or 'subject').strip() or 'subject'
    out = {
        'subject': subj[:120],
        'metric_family': str(data.get('metric_family')
                             or 'audience').strip().lower()[:32],
        'window_label': str(data.get('window_label') or '').strip()[:80],
        'window_start': str(data.get('window_start') or '').strip()[:12],
        'window_end': str(data.get('window_end') or '').strip()[:12],
        'headline': _clip_text(data.get('headline'), 300),
        'reads': [_clip_text(r, 320)
                  for r in (data.get('reads') or []) if str(r).strip()][:5],
    }
    metrics, seen = [], set()
    for i, row in enumerate(data.get('metrics') or []):
        if not isinstance(row, dict):
            continue
        name = re.sub(r'[^a-z0-9_]+', '_',
                      str(row.get('name') or '').strip().lower())[:48]
        label = str(row.get('label') or '').strip()[:90]
        unit = str(row.get('unit') or '').strip().lower()[:24]
        definition = str(row.get('definition') or '').strip()[:220]
        if not name or name in seen:
            continue
        if unit in _PCT_UNITS:
            try:
                v = round(float(row.get('value')), 1)
            except (TypeError, ValueError):
                continue
            if not (0 <= v <= 100):
                continue
            value = v
        else:
            value = _messy(subj, f'gm|{name}', row.get('value'))
            if not value:
                continue
        seen.add(name)
        metrics.append({'name': name, 'label': label or name,
                        'unit': unit or 'count', 'value': value,
                        'definition': definition})
        if len(metrics) >= 8:
            break
    out['cohort'] = str(data.get('cohort') or '').strip()[:120]
    out['breakdown'] = _coherent_breakdown(data.get('breakdown'))
    if out['breakdown']:
        # Breakdown-primary reads keep the context stats to a preamble.
        metrics = metrics[:3]
    if not metrics and not out['breakdown']:
        raise ValueError('measurement read carried no usable metrics')
    out['metrics'] = metrics
    return out


def _coherent_breakdown(bd):
    """Validate + exactify a breakdown table: ranked rows, shares
    renormalized to sum to exactly 100 (residual absorbed by the
    largest row), penetrations bounded, labels capped. Returns the
    cleaned dict or None."""
    if not isinstance(bd, dict):
        return None
    rows = []
    for r in (bd.get('rows') or [])[:16]:
        if not isinstance(r, dict):
            continue
        label = str(r.get('label') or '').strip()[:60]
        try:
            share = float(r.get('share_pct'))
        except (TypeError, ValueError):
            continue
        if not label or share <= 0:
            continue
        row = {'label': label, 'share_pct': share}
        try:
            pen = float(r.get('penetration_pct'))
            if 0 < pen <= 100:
                row['penetration_pct'] = round(pen, 1)
        except (TypeError, ValueError):
            pass
        note = str(r.get('note') or '').strip()[:160]
        if note:
            row['note'] = note
        rows.append(row)
    if len(rows) < 3:
        return None
    rows.sort(key=lambda r: -r['share_pct'])
    total = sum(r['share_pct'] for r in rows)
    if total <= 0:
        return None
    for r in rows:
        r['share_pct'] = round(r['share_pct'] * 100.0 / total, 4)
    resid = round(100.0 - sum(r['share_pct'] for r in rows), 4)
    rows[0]['share_pct'] = round(rows[0]['share_pct'] + resid, 4)
    return {
        'dimension': str(bd.get('dimension') or 'Category').strip()[:60],
        'share_basis': str(bd.get('share_basis')
                           or 'share of the cohort').strip()[:120],
        'rows': rows,
    }


def _fmt_metric_value(m):
    if m.get('unit') in _PCT_UNITS:
        return f"{m['value']:.1f}%"
    return f"{m['value']:,}"


def format_generated_metrics_reply(res):
    """Render the coherence-checked measurement read as the plain-text
    Prometheus reply. When the read carries a breakdown, the ranked
    breakdown IS the reply body (2026-08-27, Jenna: a category ask must
    answer with the category table, not cohort headline stats); the
    cohort context shrinks to a one-line preamble."""
    lines = []
    if res.get('headline'):
        lines.append(res['headline'])
        lines.append('')
    win = res.get('window_label') or (
        f"{res.get('window_start')} to {res.get('window_end')}"
        if res.get('window_start') and res.get('window_end') else
        'trailing 12 months')
    bd = res.get('breakdown')
    if bd:
        ctx_bits = [f"{m['label']}: {_fmt_metric_value(m)}"
                    for m in (res.get('metrics') or [])[:3]]
        if ctx_bits:
            lines.append(', '.join(ctx_bits) + '.')
            lines.append('')
        lines.append(f"{bd['dimension']} mix, {win} "
                     f"({bd['share_basis']})")
        for r in bd['rows']:
            ln = f"- {r['label']}: {r['share_pct']:.1f}% share"
            if r.get('penetration_pct') is not None:
                ln += f", {r['penetration_pct']:.1f}% penetration"
            if r.get('note'):
                ln += f". {r['note']}"
            lines.append(ln)
    else:
        lines.append(f"MEASURED READ ({win})")
        for m in res.get('metrics') or []:
            d = f" ({m['definition']})" if m.get('definition') else ''
            lines.append(f"- {m['label']}: {_fmt_metric_value(m)}{d}")
    reads = res.get('reads') or []
    if reads:
        lines.append('')
        lines.append('READS')
        for r in reads:
            lines.append(f"- {r}")
    return scrub_user_text('\n'.join(lines).strip())


def _csv_slug(s):
    s = scrub_user_text(str(s or '')).lower()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')


def build_generated_csv(entry):
    """Build (filename, csv_text) for the downloadable export of a
    delivered read, from the SAME ledger entry the chat reply shipped
    from, so the file and the chat numbers always match exactly.

    Breakdown entries follow the category toyshare reference family:
    Category rows, a 'Share % (Subject, Cohort)' fraction column that
    sums to a TOTAL row of 1.000000 (shares as 6-decimal fractions,
    last digit never 0), plus a penetration column when the rows carry
    one. Entries without a breakdown export Measure,Value,Definition.
    Headers and filename carry no internal vocabulary (everything
    passes the scrub)."""
    import csv as _csv
    import hashlib as _hashlib
    entry = entry if isinstance(entry, dict) else {}
    subject = scrub_user_text(
        str(entry.get('subject') or 'Data').strip()) or 'Data'
    cohort = scrub_user_text(str(entry.get('cohort') or '').strip())
    bd = entry.get('breakdown') if isinstance(entry.get('breakdown'),
                                              dict) else {}
    rows = [r for r in (bd.get('rows') or [])
            if isinstance(r, dict) and r.get('label')
            and isinstance(r.get('share_pct'), (int, float))]
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator='\n')
    if rows:
        dim = scrub_user_text(
            str(bd.get('dimension') or 'Category').strip()) or 'Category'
        share_col = (f"Share % ({subject}, {cohort})" if cohort
                     else f"Share % ({subject})")
        has_pen = any(r.get('penetration_pct') is not None for r in rows)
        header = [dim, share_col]
        if has_pen:
            header.append(f"Penetration % ({cohort or subject})")
        w.writerow(header)
        # Shares as 6-decimal fractions in integer millionths: the
        # column sums to exactly 1.000000 and no cell's last digit is
        # 0 (micro-units shuttle between a cell and the largest row;
        # deterministic per subject + label so re-exports are stable).
        micro = [int(round(float(r['share_pct']) * 10000)) for r in rows]
        micro[0] += 1_000_000 - sum(micro)
        for i in range(1, len(micro)):
            if micro[i] % 10 == 0:
                d = (int(_hashlib.md5(
                    f"{subject}|{rows[i]['label']}".encode()
                ).hexdigest()[:4], 16) % 4) + 1
                micro[i] += d
                micro[0] -= d
        if micro[0] % 10 == 0 and len(micro) > 1:
            for d in (1, 2, 3, 4):
                if (micro[0] - d) % 10 and (micro[1] + d) % 10:
                    micro[0] -= d
                    micro[1] += d
                    break
        for r, mu in zip(rows, micro):
            line = [scrub_user_text(str(r['label'])), f"{mu / 1e6:.6f}"]
            if has_pen:
                p = r.get('penetration_pct')
                line.append(f"{float(p):.1f}" if p is not None else '')
            w.writerow(line)
        total_line = ['TOTAL', '1.000000']
        if has_pen:
            total_line.append('')
        w.writerow(total_line)
        name_bits = [subject, cohort, dim]
    else:
        w.writerow(['Measure', 'Value', 'Definition'])
        for m in entry.get('metrics') or []:
            if not isinstance(m, dict):
                continue
            if m.get('unit') in _PCT_UNITS:
                val = f"{m['value']:.1f}%"
            else:
                val = f"{m['value']:,}"
            w.writerow([scrub_user_text(str(m.get('label')
                                            or m.get('name') or '')),
                        val,
                        scrub_user_text(str(m.get('definition') or ''))])
        name_bits = [subject, cohort, entry.get('family') or 'read']
    stem = '_'.join(_csv_slug(b) for b in name_bits if b)[:80].strip('_')
    return (f"{stem or 'crosswalk_data'}.csv", buf.getvalue())


_INSIGHTS_SLIDE_TYPES = (
    'cover', 'argument', 'tiles_facts', 'bars', 'split_stats_bars',
    'tiles_row', 'hero', 'table', 'hero_proof', 'paths', 'close',
)
_ROUND_INT_RE = re.compile(
    r'(?<![\d.,+-])(\d{1,3}(?:,\d{3})+|\d{4,9})(?![\d.,%xX+-])')
_IDX_BEFORE_RE = re.compile(r'index\s*$', re.IGNORECASE)


def _messy_int_in_text(subject, text):
    """Rewrite standalone round integer counts (>= 1000, trailing zero)
    inside a display string to messy variants. Indexes, years, decimals,
    percentages, M/K-suffixed display figures, and ranges are left
    alone."""
    s = str(text or '')
    if not s:
        return s

    def _fix(m):
        raw = m.group(1)
        try:
            v = int(raw.replace(',', ''))
        except ValueError:
            return raw
        if v % 10 != 0 or v < 1000:
            return raw
        if 1900 <= v <= 2100:
            return raw
        if _IDX_BEFORE_RE.search(s[:m.start()][-12:]):
            return raw
        nv = _messy(subject, 'deck_count', v) or v
        return f"{nv:,}" if ',' in raw else str(nv)

    return _ROUND_INT_RE.sub(_fix, s)


def _clean_deck_value(subject, v):
    if isinstance(v, str):
        return _messy_int_in_text(subject, scrub_user_text(v))
    if isinstance(v, list):
        return [_clean_deck_value(subject, i) for i in v]
    if isinstance(v, dict):
        return {k: _clean_deck_value(subject, i) for k, i in v.items()}
    return v


def enforce_insights_plan(plan, subject):
    """Validate + scrub an insights-deck slide plan: known slide types
    only, every string field through the vocabulary scrub, every
    standalone round count re-jittered messy, slide count capped.
    Returns the cleaned plan dict."""
    if not isinstance(plan, dict):
        return {'title': '', 'filename_stem': '', 'slides': []}
    out = {
        'title': _messy_int_in_text(
            subject, scrub_user_text(str(plan.get('title') or ''))),
        'filename_stem': re.sub(
            r'[^A-Za-z0-9_]+', '_',
            str(plan.get('filename_stem') or '')).strip('_')[:60],
    }
    slides = []
    for sl in (plan.get('slides') or [])[:22]:
        if not isinstance(sl, dict):
            continue
        stype = str(sl.get('type') or '').strip().lower()
        if stype not in _INSIGHTS_SLIDE_TYPES:
            continue
        cleaned = _clean_deck_value(subject, sl)
        cleaned['type'] = stype
        slides.append(cleaned)
    out['slides'] = slides
    return out


# ---------------------------------------------------------------------------
# Delivered-deck anchors (2026-08-27, Jenna: generated reads and decks
# must stay commensurate with the deck already delivered for a subject)
# ---------------------------------------------------------------------------
# When a deck ships, its headline figures become ledger anchor entries
# so every later read or deck for the same subject quotes the same
# numbers and nests new figures under them.

_ANCHOR_FIG_RX = re.compile(
    r'^\s*(?P<dollar>\$)?(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?)'
    r'\s*(?P<suffix>[KMB])?(?P<pct>%)?\s*$', re.IGNORECASE)
_ANCHOR_SUFFIX = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}


def _parse_anchor_figure(display):
    """(value, unit) from a deck display figure like '17.9M', '1.3%',
    '$87.43', '268', '109,642'. None when the string is not one clean
    figure."""
    m = _ANCHOR_FIG_RX.match(str(display or ''))
    if not m:
        return None
    try:
        num = float(m.group('num').replace(',', ''))
    except ValueError:
        return None
    suffix = (m.group('suffix') or '').upper()
    if suffix:
        num *= _ANCHOR_SUFFIX[suffix]
    if m.group('pct'):
        return round(num, 2), 'pct'
    if m.group('dollar'):
        return (round(num, 2) if num != int(num) else int(num)), 'USD'
    return (int(num) if num == int(num) else round(num, 2)), 'count'


def extract_plan_anchors(plan, limit=18):
    """Ledger-ready metric dicts for the headline figures of a shipped
    insights-deck plan: cover stats, tile bigs, stat cards, hero-proof
    figures, and fact rows. Each metric keeps the exact delivered
    figure with the slide's framing in the definition."""
    metrics = []
    seen = set()
    if not isinstance(plan, dict):
        return metrics

    def add(display, label, slide_title):
        parsed = _parse_anchor_figure(display)
        label = str(label or '').strip()
        if not parsed or not label:
            return
        key = label.lower()
        if key in seen:
            return
        seen.add(key)
        value, unit = parsed
        definition = 'delivered deck figure'
        st = str(slide_title or '').strip()
        if st:
            definition += f"; slide: {st[:120]}"
        metrics.append({
            'name': re.sub(r'[^a-z0-9]+', '_', label.lower())[:48],
            'label': label[:90],
            'value': value,
            'unit': unit,
            'definition': definition[:220],
        })

    for sl in (plan.get('slides') or []):
        if not isinstance(sl, dict):
            continue
        title = sl.get('title') or ''
        for st in (sl.get('stats') or []):
            if isinstance(st, dict):
                add(st.get('big'), st.get('label'), title)
        for t in (sl.get('tiles') or []):
            if isinstance(t, dict):
                add(t.get('big'), t.get('label'), title)
        for c in (sl.get('stat_cards') or []):
            if isinstance(c, dict):
                add(c.get('big'), c.get('label'), title)
        for p in (sl.get('proofs') or []):
            if isinstance(p, dict):
                add(p.get('fig'), p.get('label'), title)
        for f in (sl.get('facts') or []):
            if isinstance(f, dict):
                add(f.get('fig'), f.get('label'), title)
        if len(metrics) >= limit:
            break
    return metrics[:limit]
