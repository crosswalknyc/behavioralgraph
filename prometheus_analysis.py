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

import difflib
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

# Sections where "share of time / share of activity across X" is a
# natural ask (2026-09-24, BET: "% of total time spent on each
# streaming platform"). Their digest rows carry the profile's own
# Category Share so the split is read straight off the section
# instead of being reasoned from penetrations, and the answer can
# never be swapped for a title ranking that shares the same words.
SHARE_SECTIONS = {
    'STREAMING/PLATFORM', 'STREAMING PLATFORM', 'STREAMING VIDEO',
    'STREAMING MUSIC', 'STREAMING/MUSIC', 'SOCIAL MEDIA',
    'SEARCH ENGINE/AI', 'SEARCH ENGINE', 'VIRTUAL MVPD/FAST',
    'VIRTUAL MVPD FAST', 'VMVPD/FAST', 'VMVPD', 'FAST PLATFORM',
    'FAST CHANNEL', 'APP/PLATFORM', 'APP/PLATFORM USAGE', 'GAMES',
    'PODCAST', 'MEDIA', 'BROADCAST/CABLE',
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
    return {'name': _humanize_subject(name or fallback_name) or 'Audience',
            'sample': sample, 'proj': proj, 'window': window,
            'bp_col': bp, 'brand_category': brand_category}


_KEEP_CAPS = {'TV', 'NFL', 'NBA', 'MLB', 'NHL', 'MLS', 'WNBA', 'UFC', 'HBO',
              'ESPN', 'CBS', 'NBC', 'ABC', 'AMC', 'BET', 'MTV', 'CNN', 'BBC',
              'USA', 'UK', 'US', 'LA', 'NYC', 'DC', 'AI', 'IQ', 'TU', 'EST',
              'TVOD', 'SVOD', 'AVOD', 'FAST', 'PVOD', 'QSR', 'CPG', 'DIY',
              'GOAT', 'ESPN+', 'HBO', 'FX', 'TLC', 'HGTV', 'PBS', 'NPR',
              'YTD', 'II', 'III', 'IV', 'VR', 'AR', 'OG'}


def _humanize_subject(s):
    """'THE_ROKU_CHANNEL' -> 'The Roku Channel'. Mixed-case names and
    names with lowercase letters pass through untouched (2026-10-02:
    a file key leaked into a reply as the audience name)."""
    s = str(s or '').strip()
    if not s:
        return s
    if '_' not in s and not (s.isupper() and len(s) > 3):
        return s
    words = re.split(r'[_\s]+', s)
    out = []
    for w in words:
        if not w:
            continue
        if w.upper() in _KEEP_CAPS:
            out.append(w.upper())
        elif re.fullmatch(r'\d+[A-Za-z]*', w):
            out.append(w.upper())
        elif "'" in w:
            out.append("'".join(part.capitalize() for part in w.split("'")))
        elif out and w.lower() in ('on', 'of', 'the', 'and', 'in', 'at',
                                   'to', 'for', 'a', 'an', 'vs', 'x'):
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return ' '.join(out)


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


def _fmt_row(brand, bp, gp_bp, share=None):
    s = f"{brand} {bp:.1f}"
    bits = []
    if gp_bp is not None and gp_bp >= 0.01:
        bits.append(f"idx {round(bp / gp_bp * 100)}")
    if share is not None:
        bits.append(f"share {share:.1f}")
    if bits:
        s += " (" + ", ".join(bits) + ")"
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
        bits.append(f"panel sample {meta['sample']:,} (internal, never "
                    "stated to the reader)")
    if meta.get('proj'):
        bits.append(f"projected US audience {meta['proj']:,}")
    bits.append(f"window {meta.get('window') or 'trailing 12 months'}")
    lines.append('  ' + '; '.join(bits))

    demo_lines, cat_lines = [], []
    demo_rows_all, beh_rows_all = [], []
    share_c = _fuzzy_col(df, 'category share')
    share_sections_seen = False
    for cat, grp in df.groupby('Column', sort=False):
        catU = _norm_cat(cat)
        if catU in METADATA_COLS:
            continue
        rows = []
        share_by_brand = {}
        want_share = share_c is not None and catU in SHARE_SECTIONS
        for _, row in grp.iterrows():
            v = _parse_bp(row.get(bp_c))
            if v is None:
                continue
            b = str(row.get('Value') or '')
            rows.append((b, v))
            if want_share:
                sv = _parse_bp(row.get(share_c))
                if sv is not None:
                    share_by_brand[b] = sv
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
                shown.append(_fmt_row(b, v, gp, share_by_brand.get(b)))
        if not shown:
            continue
        # The subject's own row (BET+ on the BET profile) sits at 100
        # penetration and is dropped from the ranking above, but it
        # still holds a real share of the section's activity. Surface
        # that share so a split across the section sums to 100.
        if want_share and shown:
            share_sections_seen = True
            for b, v in rows:
                if v >= 99.99 or _norm_brand(b) == subj_norm:
                    sv = share_by_brand.get(b)
                    if sv is not None:
                        shown.insert(0, f"{b} (own service, share {sv:.1f})")
                    break
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
                 "'math:' block = full-category calculations"
                 + ("; 'share' = this row's share of the section's total "
                    "activity, the section's shares sum to 100 - a 'share "
                    "of time' or 'split across platforms' ask is answered "
                    "from these share figures, never from a title list"
                    if share_sections_seen else "")
                 + "):")
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
        bits.append(f"panel sample {cut_meta['sample']:,} (internal, never "
                    "stated to the reader)")
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
# ---------------------------------------------------------------------------
# Named-entity exact rows (2026-10-01, Jenna: "do flavor 1 and 2" -
# deep corpus reach). The digest keeps each category's top rows, so a
# question naming a mid-tail brand, title, or person used to reason
# blind even though the exact cell exists in the shipped file. This
# block hands the model the verbatim rows for every entity the
# question names, with the Gen Pop baseline alongside, and instructs
# it to use them as stated.

# Single-token brand names that are also everyday English words match
# only when the question carries the Capitalized form, so 'our target
# demo' never pulls Target's rows while 'how big is Target here' does.
# Multi-token and distinctive names match case-blind.
_ENTITY_COMMON_WORDS = {
    'target', 'apple', 'gap', 'coach', 'shell', 'ring', 'mint',
    'total', 'boost', 'sonic', 'subway', 'dove', 'tide', 'crest',
    'glad', 'bounce', 'bounty', 'prime', 'max', 'peacock', 'sprint',
    'uber', 'chime', 'mars', 'vans', 'guess', 'gain', 'all'}


# Categories that carry the same brand at ONE identical value by
# construction (profile-iq-pipeline-rules 3b: MOST PURCHASED BRANDS is
# the anchor for the purchase family; AUTOMOBILE anchors AUTOMOTIVE
# PARTS; TALENT anchors its role categories; SPORTS TEAM its league
# companions). A brand showing in two of these at the same value is the
# same people counted once, listed under two headings - never two
# behaviors (2026-10-06: a read on Under Armour called MOST PURCHASED
# BRANDS 'purchase behavior' and APPAREL/FOOTWEAR 'shopping behavior'
# and concluded 'anyone touching the brand is converting').
_MIRROR_ANCHOR_ORDER = (
    'MOST PURCHASED BRANDS', 'AUTOMOBILE', 'TALENT', 'SPORTS TEAM',
    'QSR', 'PODCAST')


def _mirror_rank(cat):
    cu = _norm_cat(cat)
    for i, a in enumerate(_MIRROR_ANCHOR_ORDER):
        if cu == a:
            return i
    return len(_MIRROR_ANCHOR_ORDER)


def _fmt_people(n):
    try:
        n = int(round(float(str(n).replace(',', ''))))
    except (TypeError, ValueError):
        return None
    return f"{n:,}" if n > 0 else None


_ENTITY_ROWS_HEADER = (
    'EXACT ROWS FOR ENTITIES THIS QUESTION NAMES (verbatim cells from '
    'the base file; these are measured values - quote them as stated, '
    'never re-derive or round them away). A "projected US people" '
    'count is the file\'s own figure: quote it exactly, never '
    'recompute it from the percentage. A row marked "also listed '
    'under ..." is ONE measurement shown under more than one heading '
    '(the same people counted once), never two behaviors: a "shop at '
    'or buy" question has one answer, the brand\'s row.')


def _entity_row_groups(df, genpop_map, text):
    """{entity: [(cat, bp, gp, proj)]} for every non-demo row whose
    Value the question names. Shared by the entity rows block and the
    purchase context."""
    bp_col = _bp_col(df)
    if bp_col is None:
        return {}
    proj_c = _fuzzy_col(df, 'proj')
    qn = (' ' + re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).strip() + ' ')
    raw = str(text)
    by_ent = {}
    for _, row in df.iterrows():
        cat = _norm_cat(row.get('Column'))
        if cat in METADATA_COLS or cat in DEMO_COLS:
            continue
        val = str(row.get('Value') or '').strip()
        if len(val) < 3:
            continue
        ent_sp = re.sub(r'[^a-z0-9]+', ' ', val.lower()).strip()
        if len(ent_sp) < 3 or f' {ent_sp} ' not in qn:
            continue
        if (' ' not in ent_sp
                and ent_sp in _ENTITY_COMMON_WORDS
                and val not in raw
                and val.capitalize() not in raw
                and val.title() not in raw):
            continue
        bp = _parse_bp(row.get(bp_col))
        if bp is None or bp >= 99.99:
            # the subject's own self-pin row is not an entity row
            continue
        gp = genpop_map.get((cat, _norm_brand(val))) if genpop_map else None
        proj = _fmt_people(row.get(proj_c)) if proj_c is not None else None
        by_ent.setdefault(val, []).append((cat, bp, gp, proj))
    return by_ent


def _entity_lines(val, rows, max_rows_per_entity=4, who='this audience'):
    """One line per DISTINCT value for an entity; categories carrying
    the same value collapse into one line naming every heading."""
    groups = {}
    for cat, bp, gp, proj in rows:
        groups.setdefault(round(bp, 4), []).append((cat, bp, gp, proj))
    lines = []
    for key in sorted(groups, reverse=True)[:max_rows_per_entity]:
        g = sorted(groups[key], key=lambda r: (_mirror_rank(r[0]), r[0]))
        cat, bp, gp, proj = g[0]
        heading = cat
        if len(g) > 1:
            heading += (' (also listed under '
                        + ', '.join(r[0] for r in g[1:])
                        + ' at the same value: one measurement, not two behaviors)')
        gp = next((r[2] for r in g if r[2] is not None), gp)
        proj = next((r[3] for r in g if r[3]), proj)
        bits = f"- {val} | {heading}: {bp:.4f}% of {who}"
        if proj:
            bits += f" | projected US people {proj} (quote verbatim)"
        if gp is not None and gp >= 0.01:
            bits += f" | gen pop {gp:.4f}% | {bp / gp:.1f}x (index {round(bp / gp * 100)})"
        lines.append(bits)
    return lines


def build_named_entity_rows(df, genpop_map, text, limit=8,
                            max_rows_per_entity=4):
    """Verbatim base-file rows for entities the question names, with
    the file's own projected count and mirrored categories collapsed
    to one line. Returns '' when the question names nothing the file
    carries. Never raises."""
    try:
        if df is None or not str(text or '').strip():
            return ''
        by_ent = _entity_row_groups(df, genpop_map, text)
        if not by_ent:
            return ''
        ranked = sorted(by_ent.items(),
                        key=lambda kv: -max(r[1] for r in kv[1]))
        lines = []
        for val, rows in ranked[:limit]:
            lines.extend(_entity_lines(val, rows, max_rows_per_entity))
        if not lines:
            return ''
        return _ENTITY_ROWS_HEADER + '\n' + '\n'.join(
            lines[:limit * max_rows_per_entity])
    except Exception as e:
        print(f"[prometheus] named-entity rows failed: {e}")
        return ''


# ---------------------------------------------------------------------------
# Brand purchase questions (2026-10-06, Jenna: "the Avid tier and the
# retail channel pulled into any brand-purchase question by default").
# ---------------------------------------------------------------------------

_PURCHASE_ASK_RX = re.compile(
    r"\b(buy|buys|buying|bought|purchas\w*|shop(?:s|ped|ping)?\b|shopper\w*|"
    r"customer\w*|spend\w*\s+(?:at|on|with)|own(?:s|ed)?\s+(?:a|an|the)?\s*\w*"
    r"|wear\w*|subscribe\w*\s+to|order\w*\s+from|eat\w*\s+at|dine\w*)\b", re.I)

_PURCHASE_FAMILY = {
    'MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'APPAREL',
    'FOOTWEAR', 'BEAUTY/WELLNESS', 'BEAUTY', 'HOME/OUTDOOR', 'ACCESSORIES',
    'PETS', 'TOYS', 'TOY', 'TECHNOLOGY BRAND', 'TECHNOLOGY/DEVICE',
    'TECHNOLOGY BRAND/DEVICE', 'HEAVY MACHINERY', 'WHERE THEY SHOP',
    'RETAILERS', 'QSR', 'WHERE THEY DINE', 'CASUAL DINING', 'AUTOMOBILE',
    'AUTOMOTIVE PARTS', 'GROCERY', 'ACTIVEWEAR', 'JEWELRY', 'INTIMATES',
    'BEVERAGE', 'TRAVEL', 'TELECOM', 'BANKING', 'BANKS', 'DIGITAL BANKING',
    'CREDIT PROVIDER', 'INSURANCE', 'PHARMACY', 'BETTING', 'TICKETING',
    'WORKOUT FACILITY', 'STREAMING/PLATFORM', 'STREAMING MUSIC'}

_RETAIL_CHANNEL_CATS = ('WHERE THEY SHOP', 'RETAILERS', 'RETAILER')


def is_brand_purchase_ask(text):
    """True for a question about buying, shopping, owning, wearing,
    subscribing to, or being a customer of something."""
    return bool(_PURCHASE_ASK_RX.search(str(text or '')))


def find_avid_key(s3_client, bucket, subject, exclude_key=None):
    """S3 key of the subject's Avid Fan cut in the library
    ('<Subject> - Avid Fan'), or None. Never raises."""
    try:
        subj = _norm_brand(str(subject or ''))
        if not subj:
            return None
        for nm, sk in _load_catalog_names(s3_client, bucket):
            parts = str(nm).split(' - ', 1)
            if len(parts) != 2 or 'avid' not in parts[1].lower():
                continue
            if _norm_brand(parts[0]) == subj and sk != exclude_key:
                return sk
    except Exception:
        pass
    return None


def build_purchase_context(df, genpop_map, text, avid_df=None,
                           subject=None, max_retail=10, max_peers=5):
    """The block a brand-purchase question answers from, so the Avid
    tier and the retail channel are IN the answer rather than offered
    as follow-ups:

      - the named brand's row(s) in the Avid Fan cut (same brand, same
        heading, the cut's own projected count verbatim);
      - where this audience buys: the retail-channel rows (WHERE THEY
        SHOP / RETAILERS) ranked by index, penetration at least 1%;
      - the brand's peers: the leading rows of its own sub-category.

    Returns '' when the question names no purchase-family row. Never
    raises."""
    try:
        if df is None or not str(text or '').strip():
            return ''
        by_ent = _entity_row_groups(df, genpop_map, text)
        named = {val: rows for val, rows in by_ent.items()
                 if any(_norm_cat(r[0]) in _PURCHASE_FAMILY for r in rows)}
        if not named and not is_brand_purchase_ask(text):
            return ''
        if not named:
            return ''
        out = ['PURCHASE CONTEXT FOR THIS QUESTION (part of the answer, '
               'never a follow-up offer): lead with the audience-wide row, '
               'then the Avid tier for the same brand, then where this '
               'audience buys, then the peer brands.']
        # Avid tier rows for the named brands.
        if avid_df is not None:
            try:
                a_meta = _profile_meta(avid_df, 'avid')
            except Exception:
                a_meta = {}
            a_rows = _entity_row_groups(avid_df, genpop_map, text)
            a_lines = []
            for val in named:
                if val in a_rows:
                    lines_v = _entity_lines(val, a_rows[val], 2, who='the Avid tier')
                    # the direction against the audience-wide row, in
                    # words, so the read never asserts a lift the file
                    # does not measure
                    try:
                        tu_bp = max(r[1] for r in named[val])
                        av_bp = max(r[1] for r in a_rows[val])
                        gap = av_bp - tu_bp
                        word = ('ABOVE' if gap > 0.25 else 'BELOW' if gap < -0.25 else 'LEVEL WITH')
                        lines_v[0] += (f" | vs audience-wide {tu_bp:.4f}%: the Avid tier reads "
                                       f"{word} the audience-wide level ({gap:+.1f} points)")
                    except Exception:
                        pass
                    a_lines.extend(lines_v)
            if a_lines:
                head = ('AVID TIER (the subject\'s avid fans, the library\'s '
                        'Avid Fan cut; never describe it by a play count or '
                        'any other definition')
                if a_meta.get('proj'):
                    head += f"; projected US people {a_meta['proj']:,}, quote verbatim"
                head += '):'
                out.append(head)
                out.extend(a_lines)
        # Retail channel: where this audience buys.
        bp_col = _bp_col(df)
        proj_c = _fuzzy_col(df, 'proj')
        retail = []
        for _, row in df.iterrows():
            cat = _norm_cat(row.get('Column'))
            if not any(cat == c or cat.startswith(c) for c in _RETAIL_CHANNEL_CATS):
                continue
            val = str(row.get('Value') or '').strip()
            bp = _parse_bp(row.get(bp_col))
            if not val or bp is None or bp < 1.0 or bp >= 99.99:
                continue
            gp = genpop_map.get((cat, _norm_brand(val))) if genpop_map else None
            idx = (bp / gp * 100) if gp and gp >= 0.01 else None
            proj = _fmt_people(row.get(proj_c)) if proj_c is not None else None
            retail.append((idx if idx is not None else -1, val, cat, bp, gp, proj))
        if retail:
            def _rline(r):
                idx, val, cat, bp, gp, proj = r
                line = f"- {val} | {cat}: {bp:.4f}%"
                if proj:
                    line += f" | projected US people {proj} (quote verbatim)"
                if idx is not None and idx >= 0:
                    line += f" | gen pop {gp:.4f}% | index {round(idx)}"
                return line
            n_reach = max(3, max_retail // 3)
            # the biggest doors by reach, then the strongest leans
            # weighted by reach (penetration x index, retailers at 5%
            # or more): a 0.3% boutique at index 1,300 is not where
            # this audience buys, and a 24% retailer at index 357 is
            by_reach = sorted(retail, key=lambda r: -r[3])[:n_reach]
            seen = {r[1] for r in by_reach}
            by_lean = [r for r in sorted(retail, key=lambda r: -(max(r[0], 0) * r[3]))
                       if r[3] >= 5.0 and r[0] > 100 and r[1] not in seen][:max_retail - n_reach]
            out.append('WHERE THIS AUDIENCE BUYS, largest retail doors by reach:')
            out.extend(_rline(r) for r in by_reach)
            if by_lean:
                out.append('WHERE THIS AUDIENCE BUYS, strongest leans with real reach (index, retailers at 5% or more):')
                out.extend(_rline(r) for r in by_lean)
        # Peers: the leading rows of the named brand's own sub-category
        # (the sub-category, not MOST PURCHASED BRANDS, so the peers are
        # the brand's real competitive set).
        peer_cats = []
        for val, rows in named.items():
            for cat, bp, gp, proj in rows:
                cu = _norm_cat(cat)
                if cu in _PURCHASE_FAMILY and cu != 'MOST PURCHASED BRANDS' \
                        and cu not in peer_cats:
                    peer_cats.append(cu)
        named_norm = {_norm_brand(v) for v in named}
        for cu in peer_cats[:2]:
            grp = df[df['Column'].map(_norm_cat) == cu]
            peers = []
            for _, row in grp.iterrows():
                val = str(row.get('Value') or '').strip()
                bp = _parse_bp(row.get(bp_col))
                if not val or bp is None or bp >= 99.99 or _norm_brand(val) in named_norm:
                    continue
                gp = genpop_map.get((cu, _norm_brand(val))) if genpop_map else None
                peers.append((bp, val, gp))
            peers.sort(key=lambda r: -r[0])
            if peers:
                out.append(f"PEERS IN {cu} (leading rows):")
                for bp, val, gp in peers[:max_peers]:
                    line = f"- {val}: {bp:.4f}%"
                    if gp is not None and gp >= 0.01:
                        line += f" | gen pop {gp:.4f}% | index {round(bp / gp * 100)}"
                    out.append(line)
        return '\n'.join(out) if len(out) > 1 else ''
    except Exception as e:
        print(f"[prometheus] purchase context failed: {e}")
        return ''


def purchase_facts(df, genpop_map, text, avid_df=None):
    """The binding figures behind a brand-purchase read, for the verify
    pass and the in-place enforcement (2026-10-06: a read invented an
    Avid tier of 3,155,223 with Under Armour at 31.4% / 990,743 while
    the Avid file measures 3,157,308 and 23.1188% / 729,937).

    {'tu_universe', 'avid_universe', 'brands': [{'label', 'tu_pct',
    'tu_proj', 'tu_index', 'avid_pct', 'avid_proj', 'avid_index'}]}
    or {} when the question names no purchase-family row."""
    try:
        if df is None:
            return {}
        by_ent = _entity_row_groups(df, genpop_map, text)
        named = {val: rows for val, rows in by_ent.items()
                 if any(_norm_cat(r[0]) in _PURCHASE_FAMILY for r in rows)}
        if not named:
            return {}
        out = {'brands': []}
        try:
            out['tu_universe'] = int(_profile_meta(df, 'tu').get('proj') or 0) or None
        except Exception:
            out['tu_universe'] = None
        a_rows = {}
        if avid_df is not None:
            try:
                out['avid_universe'] = int(_profile_meta(avid_df, 'avid').get('proj') or 0) or None
            except Exception:
                out['avid_universe'] = None
            a_rows = _entity_row_groups(avid_df, genpop_map, text)

        def _best(rows):
            rows = sorted(rows, key=lambda r: (_mirror_rank(r[0]), -r[1]))
            cat, bp, gp, proj = rows[0]
            gp = next((r[2] for r in rows if r[2] is not None), gp)
            proj = next((r[3] for r in rows if r[3]), proj)
            try:
                projn = int(str(proj).replace(',', '')) if proj else None
            except (TypeError, ValueError):
                projn = None
            idx = round(bp / gp * 100) if gp and gp >= 0.01 else None
            return bp, projn, idx

        for val, rows in named.items():
            bp, projn, idx = _best(rows)
            b = {'label': val, 'tu_pct': bp, 'tu_proj': projn, 'tu_index': idx,
                 'avid_pct': None, 'avid_proj': None, 'avid_index': None}
            if val in a_rows:
                abp, aproj, aidx = _best(a_rows[val])
                b.update(avid_pct=abp, avid_proj=aproj, avid_index=aidx)
            out['brands'].append(b)
        return out
    except Exception as e:
        print(f"[prometheus] purchase facts failed: {e}")
        return {}


def build_entity_and_purchase_blocks(s3_client, bucket, base, text):
    """(entity_rows_block, purchase_block, purchase_facts) for a read on `base`
    (2026-10-01 deep corpus reach; 2026-10-06 purchase questions). The
    digest keeps top rows per category; a named mid-tail brand's
    verbatim cells ride the prompt from the full base file with Gen
    Pop baselines so the model quotes measured values instead of
    re-deriving them. A brand purchase question also carries the Avid
    tier (the library's '<Subject> - Avid Fan' cut when one exists),
    where the audience buys, and the brand's peers; the facts dict
    (purchase_facts) binds the verify pass. ('', '', {}) on any
    failure; never raises."""
    try:
        key = str((base or {}).get('s3_key') or '')
        if not key.lower().endswith('.csv'):
            return '', '', {}
        df, _ = load_profile_df(s3_client, bucket, key)
        gp_map = load_genpop_map(s3_client, bucket)
        entity_rows = build_named_entity_rows(df, gp_map, text)
        if not entity_rows or not is_brand_purchase_ask(text):
            return entity_rows, '', {}
        avid_df, ak = None, None
        try:
            # the subject as the base names it, else the catalog display
            # name of the base file itself (a base can arrive with a
            # file-stem subject that never matches a cut name)
            names = [str((base or {}).get('subject') or '').strip()]
            for nm, sk in _load_catalog_names(s3_client, bucket):
                if sk == key and nm and nm not in names:
                    names.append(nm)
            for nm in names:
                ak = find_avid_key(s3_client, bucket, nm, exclude_key=key) if nm else None
                if ak:
                    break
            if ak:
                avid_df, _ = load_profile_df(s3_client, bucket, ak)
        except Exception as e:
            print(f"[prometheus] avid cut load failed: {e}")
        block = build_purchase_context(
            df, gp_map, text, avid_df=avid_df,
            subject=(base or {}).get('subject'))
        facts = purchase_facts(df, gp_map, text, avid_df=avid_df) if block else {}
        print(f"[pm-read] purchase context: brands={[b.get('label') for b in facts.get('brands') or []]} "
              f"avid={'yes (' + str(ak) + ')' if avid_df is not None else 'no'} "
              f"subject={(base or {}).get('subject')!r}")
        return entity_rows, block, facts
    except Exception as e:
        print(f"[prometheus] entity/purchase blocks failed: {e}")
        return '', '', {}


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


def is_missing_key_error(err) -> bool:
    """True when an exception is S3 telling us the object is gone
    (NoSuchKey / 404). Stale page contexts carry profile keys that
    were deleted or retitled after the browser cached them (2026-09-21:
    a Rankers-view ask died on a deleted profile the question never
    needed); callers use this to drop the stale profile and proceed
    instead of failing the whole ask."""
    try:
        code = str(((getattr(err, "response", None) or {})
                    .get("Error") or {}).get("Code") or "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return True
    except Exception:
        pass
    return "NoSuchKey" in str(err)


_bundle_ttl_cache = {}


def get_digest_bundle(s3_client, bucket, page_context, max_cuts=3):
    """Assemble the full digest bundle for a page context:
    {primary: {s3_key, name}, cuts: [{s3_key, name}, ...]}.
    Returns (bundle_text, primary_meta). Caches per (key, etag); the
    nightly precomputed index, when provably fresh, serves the same
    digest without downloading + parsing the CSV. A 90-second TTL
    layer on top (2026-09-28, Phase 2 latency) lets rapid follow-up
    asks in the same conversation skip the S3 freshness round-trips
    entirely; in-place corrections still surface within the TTL."""
    _ttl_key = None
    try:
        _prim = (page_context.get('primary') or {}).get('s3_key') or ''
        _cutk = tuple((c.get('s3_key') or '')
                      for c in (page_context.get('cuts') or [])[:max_cuts])
        _extk = tuple((x.get('s3_key') or '')
                      for x in (page_context.get('extras') or [])[:3])
        _ttl_key = (_prim, _cutk, _extk, max_cuts)
        hit = _bundle_ttl_cache.get(_ttl_key)
        if hit and time.time() - hit[0] < 90:
            return hit[1], hit[2]
    except Exception:
        _ttl_key = None
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
    _bundle_text = '\n\n'.join(parts)
    if _ttl_key is not None:
        try:
            _bundle_ttl_cache[_ttl_key] = (time.time(), _bundle_text,
                                           p_meta)
            if len(_bundle_ttl_cache) > 40:
                for k, _ in sorted(_bundle_ttl_cache.items(),
                                   key=lambda kv: kv[1][0])[:10]:
                    _bundle_ttl_cache.pop(k, None)
        except Exception:
            pass
    return _bundle_text, p_meta


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

CROSS-TAB ASKS (one trait conditioned on another)
- When the ask conditions one audience trait on another ("which DMAs do ONLY the LGBTQ+ members sit in", "what share of the Costco shoppers also bought the album", "the age split of just the TikTok users"), the file carries each trait's share of the WHOLE audience, not the joint split. Never present a whole-audience row as if it were the conditioned slice.
- Answer in three beats: (1) the whole-audience numbers for both traits, stated flat; (2) the conditioned read as a directional lean ("the LGBTQ+ side of this audience skews the same coastal DMAs the whole file leads with, likely heavier in New York and Los Angeles") - leans / skews / reads-as language, never invented precision; (3) the precise path: a derived cut of this audience scoped to that group carries the exact split. Offer it by name as a followup phrased the way the user would type it ("Cut this audience by LGBTQ+ members").
- Never decline these asks and never pretend the joint numbers are on screen.

CHALLENGED NUMBERS (the reader questions a figure)
- When the user pushes back on a number ("seems very high", "why is this smaller than the previous analysis", "how is this calculated"), treat it as a definition-and-reconcile ask, not an insult and not a correction order.
- First restate in plain words what the figure measures: the audience it counts, the behavior that qualifies someone into it, and the window. Most challenges dissolve when the definition is plain (an Engager count includes anyone with a single digital touchpoint in the trailing year; a subscriber projection counts accounts-holding individuals, not households).
- Then reconcile like for like: if the user cites a different or earlier figure, name what differs (window, cohort, definition, cut vs whole audience) before anything else. Two figures that measure different things are both right; say which is which.
- Never revise, walk back, or re-derive a previously delivered figure inline. If the two reads genuinely measure the same thing in the same window and still disagree, say the team is reviewing it and the confirmed number will follow by email. Never speculate about internal causes.

US PROJECTION (binding, every count, every surface)
- Every count the reader sees is projected to the US population: accounts, viewers, signups, reactivations, sessions, hours, hits, daily and monthly series, cohort sizes, table cells, CSV cells. The headline and the breakdown sit on the same US scale; a US headline over a panel-count daily series is a defect.
- Screen data and evidence carry counts in pairs: panel_<key> / <key>_us, or "panel (US)". The _us or US figure is the only one you state. A panel count never appears in an answer, a table, or a CSV, not even alongside its US figure.
- When a count exists only at panel level, multiply it by the us_projection_factor (or the file's own ratio of US to panel on its headline count) and state the result as the US figure. Sums, shares, and comparisons are computed on US figures. Label count columns "US" where a header exists (new_signups_us, accounts_us).
- Percentages, rates, indexes, days, and dollar figures are not counts and are stated as given.

BOX OFFICE AND MOVIE TICKETS (binding; Jenna 2026-10-05)
- Crosswalk never predicts, estimates, validates, or reconciles box office. The furthest point the read sees for a film is the ticketing site: the count is US individuals who went to a ticketing site or app for a ticket to the title in the window, projected to the US general population. It makes no claim on whether any of them bought a ticket.
- Never call that count buyers, purchasers, ticket sales, admissions, or "bought the ticket", and never divide a box office gross by a ticket price to test it. When a user cites a box office figure or pushes for a purchase number, give the same answer every time, in the same plain words: we do not predict box office performance; here is how many people went to the ticketing site; no claim on purchase. Do not add a reconciliation, a "the team is reviewing it" line, or a different framing on the second or third push.

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
- The user prompt may carry a "PUBLISHED MEASUREMENTS" block: numbers Crosswalk has already delivered for this subject on earlier questions. These are binding. If your answer touches the same metric, state the exact published number; never contradict it, never restate it at different precision. A figure adjacent to a published one (a longer window, a share of it, a per-month slice) must be arithmetically consistent with it. Cohort counts sit strictly inside their published parent count (a female or Gen Z slice can never exceed the subject total), platform shares sum to at most 100, and a monthly figure sits inside its yearly one.
- MULTIPLE QUESTIONS IN ONE ASK: answer every one, each under its own short plain heading, in the order asked. Never answer only the first and stop.
- NEVER DECLINE: you never ask the reader to rephrase, narrow, re-aim, or pick a different question, and you never say a number cannot be locked down. When the screen tables do not carry the exact split asked for, derive it from the audience measures you do have and state the read plainly.

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
    # US projection (2026-10-05, Jenna): every count row is rewritten
    # as panel_<key> + <key>_us before the model sees it, so the
    # number it can quote is the US figure.
    try:
        from prometheus import projection as _proj
        data, _ = _proj.project_view_data(
            data, salt=str(view_context.get('view_id') or ''))
    except Exception:
        pass
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
        "the answer in it. Counts come in pairs: panel_<key> is the "
        "internal panel count and <key>_us is the US figure. State "
        "only the _us figure, in prose, tables, and CSVs alike.\n"
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
                            or key.startswith('purgatory/')
                            or key.startswith('_backups/')
                            or '/_backups/' in key
                            or '.pre_' in key):
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
            # Season-aware per-file list for the library lookup and
            # the multi-title evidence path (2026-10-02): the title
            # key collapses "Season 1" and "Season 2" of one show
            # into a single slot, which is right for anchoring but
            # wrong for "do you see the Season 1 read".
            shows = []
            for lm, show, key in reversed(entries):
                try:
                    lm_s = lm.strftime('%Y-%m-%dT%H:%MZ') if lm else ''
                except Exception:
                    lm_s = ''
                shows.append((show, key, lm_s))
            with _xmod_lock:
                _xmod_subiq_index_cache['shows'] = shows
        except Exception:
            index = {}
    with _xmod_lock:
        if index or _xmod_subiq_index_cache['index'] is None:
            _xmod_subiq_index_cache.update(ts=now, index=index)
        return _xmod_subiq_index_cache['index'] or {}


def list_subiq_shows(s3_client, subiq_bucket):
    """Every Subscriber IQ file in the library as (show, s3_key,
    last_modified_iso), newest first, one entry per file (season-aware).
    Shares the index LIST and its TTL cache."""
    _load_subiq_index(s3_client, subiq_bucket)
    with _xmod_lock:
        return list(_xmod_subiq_index_cache.get('shows') or [])


_SUBIQ_NORM_DOTS_RE = re.compile(r'\b(?:[A-Za-z]\.){2,}')


def _subiq_norm(s):
    """Lowercase, 'S.W.A.T.' -> 'swat', punctuation to spaces, single
    spaces. Season words normalized so 'season 1', 'S1', 'ssn 1' agree."""
    t = str(s or '')
    t = _SUBIQ_NORM_DOTS_RE.sub(lambda m: m.group(0).replace('.', ''), t)
    t = t.lower()
    t = re.sub(r'\b(?:ssn|sea|seas)\.?\s*(\d{1,2})\b', r'season \1', t)
    t = re.sub(r'\bs(\d{1,2})\b(?!\d)', r'season \1', t)
    t = re.sub(r'[^a-z0-9]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


_SUBIQ_LOOKUP_DROP = frozenset((
    'the', 'a', 'an', 'my', 'our', 'read', 'report', 'file', 'run',
    'build', 'pull', 'on', 'for', 'of', 'series', 'show', 'title',
    'complete', 'ssn', 'latest', 'new', 'viewers', 'watchers',
    'subscribers', 'audience', 'fans',
))


def match_subiq_shows(s3_client, subiq_bucket, phrase, limit=3):
    """Library entries that answer a lookup for ``phrase``: exact
    normalized name first, then every entry whose tokens contain all
    the phrase's tokens (so 'SWAT Exiles' finds 'SWAT Exiles Season 1'
    and 'SWAT Exiles Season 2'), then the reverse containment when the
    phrase is more specific than the file name. Newest first, at most
    ``limit``. [] when nothing in the library matches."""
    want_full = _subiq_norm(phrase)
    want = {w for w in want_full.split() if w not in _SUBIQ_LOOKUP_DROP}
    if not want:
        return []
    exact, contains, contained = [], [], []
    for show, key, lm in list_subiq_shows(s3_client, subiq_bucket):
        have_full = _subiq_norm(show)
        have = {w for w in have_full.split() if w not in _SUBIQ_LOOKUP_DROP}
        if not have:
            continue
        if have_full == want_full or have == want:
            exact.append((show, key, lm))
        elif want <= have:
            contains.append((show, key, lm))
        elif have <= want and len(have) >= 2:
            contained.append((show, key, lm))
    out, seen = [], set()
    for row in exact + contains + contained:
        if row[0] in seen:
            continue
        seen.add(row[0])
        out.append(row)
        if len(out) >= limit:
            break
    return out


_SUBIQ_BASE_TAIL_RE = re.compile(
    r'\b(?:season \d{1,2}|limited series|miniseries|part \d{1,2}|'
    r'vol(?:ume)? \d{1,2}|the movie|movie|film)\b')
_SUBIQ_ALIAS_STOP = frozenset(('of', 'the', 'a', 'an', 'and', 'my', 'in',
                               'on', 'to', 'for'))


def _subiq_title_aliases(show):
    """Normalized strings that name one library file in a message.

    full      'swat exiles season 1'
    base      'swat exiles'            (season / qualifier tail dropped)
    initials  'outlander bomb'         (a run of 3+ words collapsed to
                                        its initials, so the house
                                        shorthand for Outlander Blood
                                        of My Blood resolves)
    Returns [(alias, kind)] with kind in {'full', 'base', 'initials'}.
    2026-10-02 (Bria): "Analyze SWAT Exiles" found nothing because the
    file is "SWAT Exiles Season 1", so the answer came from a reasoned
    read instead of the file.
    """
    full = _subiq_norm(show)
    out = []
    if len(full) >= 4:
        out.append((full, 'full'))
    base = re.sub(r'\s+', ' ', _SUBIQ_BASE_TAIL_RE.sub(' ', full)).strip()
    base = re.sub(r'\s+\d{1,2}$', '', base).strip()
    if len(base) >= 4 and base != full:
        out.append((base, 'base'))
    words = base.split()
    seen = {a for a, _k in out}
    for i in range(len(words)):
        for j in range(i + 3, len(words) + 1):
            run = words[i:j]
            if sum(1 for w in run if w not in _SUBIQ_ALIAS_STOP) < 2:
                continue
            acro = ''.join(w[0] for w in run)
            if len(acro) < 3:
                continue
            alias = ' '.join(words[:i] + [acro] + words[j:]).strip()
            # An initialism on its own ('bomb') is too loose; it needs
            # at least one real word of the title beside it, or to be
            # the whole title at 4+ letters.
            if alias == acro and len(acro) < 4:
                continue
            if alias not in seen:
                seen.add(alias)
                out.append((alias, 'initials'))
    return out


def find_subiq_titles_in_text(s3_client, subiq_bucket, text, limit=3):
    """Every library title the message names (season-aware, whitespace
    and punctuation tolerant), longest name first, one entry per show.
    Used to answer compare / which-title questions from the library
    instead of drafting a new build.

    A message that names the base title without a season ("SWAT
    Exiles") resolves to that title's files; when the message names a
    season, only the matching season counts. House initialisms
    ("Outlander BOMB") resolve through _subiq_title_aliases."""
    t = ' ' + _subiq_norm(text) + ' '
    if len(t) < 6:
        return []
    hits = []
    for show, key, lm in list_subiq_shows(s3_client, subiq_bucket):
        full = _subiq_norm(show)
        file_seasons = set(re.findall(r'\bseason (\d{1,2})\b', full))
        best = None
        for alias, kind in _subiq_title_aliases(show):
            if (' ' + alias + ' ') not in t:
                continue
            if kind != 'full' and file_seasons:
                # Seasons named right after THIS title in the message
                # ("Outlander BOMB Season 2"): when present, only the
                # matching season's file counts. Seasons named next to
                # another title in the same sentence do not bleed over.
                asked = set()
                for m in re.finditer(
                        r'\b' + re.escape(alias) + r' season (\d{1,2})\b'
                        r'(?:(?:\s+\w+){0,3}?\s+(?:vs|versus|and|to|against|with|'
                        r'compared (?:to|with))\s+season (\d{1,2})\b)?', t):
                    asked.update(g for g in m.groups() if g)
                if asked and not (asked & file_seasons):
                    continue
            score = (len(alias), 2 if kind == 'full' else 1)
            if best is None or score > best:
                best = score
        if best is not None:
            hits.append((best, show, key, lm))
    # Longest alias first, full-name hits ahead of base hits, newest
    # file first on ties.
    hits.sort(key=lambda h: (h[0][0], h[0][1], h[3] or ''), reverse=True)
    out, seen = [], set()
    for _sc, show, key, lm in hits:
        if show in seen:
            continue
        seen.add(show)
        out.append((show, key, lm))
        if len(out) >= limit:
            break
    return out


_deliv_index_cache = {'ts': 0.0, 'data': None}


def _load_deliverable_indexes(s3_client, bucket):
    """Per-product shipped-deliverable indexes for the cross-module
    block: Digital Journey IQ runs, Attribution IQ campaigns, Brand
    Partnership IQ studies, Flywheel studies. Jenna 2026-10-01:
    "it should pull in attribution iq, pretty much everything it can
    considering all of that our corpus" - everything Prometheus has
    already shipped is corpus, so existence plus headline numbers
    ride every analyze call and answers stay consistent with
    delivered work. One cached load per TTL; any store that cannot
    load contributes an empty list, never an error."""
    now = time.time()
    with _xmod_lock:
        c = _deliv_index_cache
        if c['data'] is not None and now - c['ts'] < _XMOD_INDEX_TTL_S:
            return c['data']
    data = {'journey_iq': [], 'attribution_iq': [],
            'brand_partnership_iq': [], 'flywheel': []}
    if s3_client is not None and bucket:
        def _j(key, default):
            try:
                resp = s3_client.get_object(Bucket=bucket, Key=key)
                return json.loads(resp['Body'].read().decode('utf-8'))
            except Exception:
                return default
        try:
            data['journey_iq'] = list(
                (_j('journey-iq/_index.json', {}) or {}).get('runs')
                or [])[-150:]
        except Exception:
            pass
        try:
            data['attribution_iq'] = list(
                (_j('intent/registry.json', {}) or {}).get('titles')
                or [])[-150:]
        except Exception:
            pass
        try:
            meta = _j('system/brand_partnership_iq_metadata.json', {})
            if isinstance(meta, dict):
                data['brand_partnership_iq'] = [
                    {'key': k,
                     'display_name': str((v or {}).get('display_name')
                                         or k)}
                    for k, v in meta.items() if isinstance(v, dict)
                ][-150:]
        except Exception:
            pass
        try:
            resp = s3_client.list_objects_v2(
                Bucket=bucket, Prefix='flywheel/', MaxKeys=300)
            data['flywheel'] = [
                o['Key'] for o in resp.get('Contents') or []
                if str(o.get('Key') or '').lower().endswith('.csv')]
        except Exception:
            pass
    with _xmod_lock:
        if any(data.values()) or _deliv_index_cache['data'] is None:
            _deliv_index_cache.update(ts=now, data=data)
        return _deliv_index_cache['data'] or data


def _xmod_deliverable_lines(deliv, local_subject, local_key):
    """Compact (line, module) pairs for shipped deliverables whose
    subject matches the resolved ask subject. Containment both ways on
    the folded title key, so 'Nip/Tuck' matches the journey named
    'Nip/Tuck on Prime Video' and the campaign 'Nip/Tuck S1 Launch'."""
    out = []
    if not local_key:
        return out

    def _match(name):
        k = _xmod_title_key(name)
        if not k:
            return False
        return k == local_key or local_key in k or k in local_key

    for r in list(reversed(deliv.get('journey_iq') or [])):
        name = str(r.get('project_name') or r.get('target') or '')
        if not _match(name):
            continue
        win = ''
        if r.get('start_date') and r.get('end_date'):
            win = f" ({r['start_date']} to {r['end_date']})"
        kp = ''
        try:
            tu = int(r.get('total_users') or 0)
            if tu > 0:
                kp = f": {tu:,} conversions"
                cp = r.get('conversion_pct')
                if cp is not None:
                    kp += f" at {float(cp):.1f}% conversion"
        except (TypeError, ValueError):
            pass
        out.append((f"DIGITAL JOURNEY IQ: a journey read exists - "
                    f"{name}{win}{kp}. Any answer about this path "
                    f"must agree with that read.", 'journey_iq'))
        if sum(1 for _l, m in out if m == 'journey_iq') >= 2:
            break

    for t in list(reversed(deliv.get('attribution_iq') or [])):
        name = str(t.get('display_name') or t.get('slug') or '')
        if not _match(name):
            continue
        bits = [f"ATTRIBUTION IQ: a campaign read exists - {name}"]
        if t.get('conversion_event'):
            bits.append(f"conversion: {t['conversion_event']}")
        try:
            ac = int(t.get('asset_count') or 0)
            if ac > 0:
                bits.append(f"{ac} assets tracked")
        except (TypeError, ValueError):
            pass
        od = str(t.get('opening_date') or '')[:10]
        if od:
            bits.append(f"opened {od}")
        out.append((', '.join(bits) + '. Attribution numbers in an '
                    'answer must agree with that campaign.',
                    'attribution_iq'))
        if sum(1 for _l, m in out if m == 'attribution_iq') >= 2:
            break

    for b in list(reversed(deliv.get('brand_partnership_iq') or [])):
        name = str(b.get('display_name') or '')
        if not _match(name):
            continue
        out.append((f"BRAND PARTNERSHIP IQ: a partnership study "
                    f"exists - {name}.", 'brand_partnership_iq'))
        break

    fw_n = 0
    for k in deliv.get('flywheel') or []:
        nm = str(k)[len('flywheel/'):]
        nm = nm[:-4] if nm.lower().endswith('.csv') else nm
        nm = nm.replace('_', ' ').strip()
        if not _match(nm):
            continue
        out.append((f"FLYWHEEL: a flywheel study exists - {nm}.",
                    'flywheel'))
        fw_n += 1
        if fw_n >= 2:
            break
    return out


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
    list drawn from ('subscriber_iq', 'trends', 'profile_library',
    'journey_iq', 'attribution_iq', 'brand_partnership_iq',
    'flywheel').
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
        deliv = _load_deliverable_indexes(s3_client, bucket)
        return anchors, catalog, subiq_index, deliv

    lines = []
    modules = []
    try:
        ex = ThreadPoolExecutor(max_workers=3)
        try:
            fut_idx = ex.submit(_indexes)
            fut_trends = ex.submit(_load_trends_payload, trends_reader)
            remaining = max(0.2, time_budget_s - (time.time() - started))
            anchors, catalog, subiq_index, deliv = fut_idx.result(
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

            # --- Shipped deliverables: Digital Journey IQ,
            # Attribution IQ, Brand Partnership IQ, Flywheel
            # (2026-10-01 Jenna: "it should pull in attribution iq,
            # pretty much everything it can considering all of that
            # our corpus"). Everything already delivered for this
            # subject rides the context so fresh answers stay
            # consistent with shipped work.
            try:
                for ln, mod in _xmod_deliverable_lines(
                        deliv, local_subject, local_key):
                    lines.append(ln)
                    if mod not in modules:
                        modules.append(mod)
            except Exception:
                pass

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


# ---------------------------------------------------------------------------
# Subscriber IQ evidence block (2026-08-28, p4-subiq-parity)
# ---------------------------------------------------------------------------
# The generated-read path previously saw Subscriber IQ data only as the
# one-line cross-module signal. When the ask (or its base) references a
# title with a Subscriber IQ file, the full payload is parsed
# server-side and a compact structured block (signups, windows,
# cohorts, key drivers) rides the evidence, byte-capped. Parsed
# payloads are cached in-process by ETag + TTL like the other loaders.

SUBIQ_EVIDENCE_MAX_BYTES = 3072
_SUBIQ_PAYLOAD_TTL_S = 600
_subiq_payload_cache = {}   # {s3_key: {'etag', 'ts', 'parsed'}}


_SUBIQ_OUT_OF_READ_RE = re.compile(
    r"\b(revenue|arpu|ltv|lifetime value|forecast|predict\w*|"
    r"next (?:month|quarter|season|year)|how many will|"
    r"profile iq|brand penetration|index vs)\b", re.I)

SUBIQ_FORCE_ANSWER_NOTE = (
    "SUBSCRIBER IQ ANSWER IN PLACE: the Subscriber IQ read for the "
    "title(s) in this question is on screen and/or in the SUBSCRIBER "
    "ACQUISITION EVIDENCE block above. Answer with action=answer from "
    "those numbers only: accounts viewed, new signups, conversion, "
    "attributed vs reactivated, episode drivers, signup timing by day, "
    "first visits after signup, monthly platform signups, monthly "
    "platform churn (platform-level, as labeled), signup demographics, "
    "and platform overlap. Do not return generate_metrics. If the ask "
    "names a figure this read does not carry, give the nearest figure "
    "the read does carry, say in plain words which measure it is, and "
    "stop. Never invent the missing figure and never present a number "
    "that is not in the read as if it were.")


# Cohort churn (2026-10-05, Emma / Jenna): "What was monthly churn for
# Peacock accounts that signed up in July 2026, July through
# September?" went to the build flow and came back with no written
# answer. A Subscriber IQ read measures PLATFORM churn, not the churn
# of one signup cohort, so a cohort-churn ask is a derived read: the
# deeper pass takes the platform's published monthly churn from
# approved sources, the read's own platform churn and signup counts,
# and shapes the cohort's month-by-month curve. Never a build, never
# a fork, never "the read does not carry that".
_COHORT_CHURN_RE = re.compile(
    r"\b(?:churn\w*|retention|retain\w*|cancell?\w*|lapse\w*|"
    r"still (?:subscribed|active|paying))\b", re.I)
_COHORT_FRAME_RE = re.compile(
    r"\b(?:signed up|sign[- ]?ups?|subscribed|joined|acquired|activated|"
    r"cohorts?|new (?:accounts|subs|subscribers|signups)|"
    r"(?:accounts|subscribers|subs|users|viewers) (?:that|who)|"
    r"month (?:one|1|two|2|three|3)|first (?:\d+|thirty|sixty|ninety) "
    r"days|(?:30|60|90)[- ]day)\b", re.I)


# ---- Box office / movie-ticket asks (2026-10-05, Jenna) ----------------
# "I dont want to get in the habit of predicting box office ever. instead
# if someone asks for box office the best we can do is say an estimate of
# people who went to the ticket sites ... we make no admission of if they
# purchased or not. no matter how hard the user pushes we just keep saying
# we do not predict box office performance all we can do is tell you how
# many people went to the ticketing site."
_BOX_OFFICE_RE = re.compile(
    r"\b(box[- ]?office|opening[- ]weekend (?:gross|take|number|total|"
    r"estimate|forecast)|gross(?:ed|es)?\b|admissions|"
    r"tickets? (?:(?:were|was|got|been) )?(?:sold|sales|bought|purchased|purchases?)|"
    r"ticket (?:buyers?|purchasers?)|bought (?:\w+ )?tickets?|"
    r"purchasers?)", re.I)
_MOVIE_RE = re.compile(
    r"\b(film|movie|movies|theat(?:er|re|rical)s?|cinema|showtimes?|"
    r"fandango|atom tickets|opening weekend|walk[- ]up|screening|"
    r"box[- ]?office|ticketing (?:site|app|platform)s?)\b", re.I)
_TICKET_STAGE_RE = re.compile(
    r"ticket|box[- ]?office|showtime|fandango|checkout|order page", re.I)


def is_box_office_ask(text, view_context=None):
    """True when the ask is about a film's box office or its ticket
    purchases (how many bought, reconcile against the gross, validate
    the purchasers, admissions). Live-event ticketing without movie
    vocabulary is not this lane, unless the open view is a film
    ticketing read, in which case "how many bought tickets" is."""
    t = str(text or '')
    if not _BOX_OFFICE_RE.search(t):
        return False
    if re.search(r"\bbox[- ]?office\b", t, re.I):
        return True
    if _MOVIE_RE.search(t):
        return True
    if view_context:
        _t, count, _w = _ticketing_count_from_view(view_context)
        return count is not None
    return False


def _ticketing_count_from_view(view_context):
    """(title, count, window) for the open Journey IQ ticketing read,
    or (title, None, window) when the view carries no ticketing step."""
    vc = view_context if isinstance(view_context, dict) else {}
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    title = str(data.get('title') or vc.get('view_title') or '').strip()
    window = str(data.get('window') or '').strip()
    hay = json.dumps(data)[:20000].lower()
    is_ticketing = bool(_TICKET_STAGE_RE.search(hay)) and bool(
        re.search(r"film|movie|theat|showtime|opening weekend|fandango", hay))
    count = None
    hb = data.get('headline_block') if isinstance(
        data.get('headline_block'), dict) else {}
    hdata = hb.get('data') if isinstance(hb.get('data'), dict) else {}
    spine = hdata.get('spine') if isinstance(hdata.get('spine'), list) else []
    for st in reversed(spine):
        if not isinstance(st, dict):
            continue
        lab = f"{st.get('label', '')} {st.get('doing', '')}"
        if _TICKET_STAGE_RE.search(lab):
            try:
                count = int(st.get('accounts'))
            except (TypeError, ValueError):
                count = None
            break
    if count is None and is_ticketing:
        kp = data.get('kpis') if isinstance(data.get('kpis'), dict) else {}
        try:
            count = int(kp.get('total_users'))
        except (TypeError, ValueError):
            count = None
    if not is_ticketing:
        count = None
    if title and ' - ' in title:
        title = title.split(' - ', 1)[0].strip()
    title = re.sub(r"\s*\((?:film|movie)\)\s*,?.*$", "", title).strip()
    return title, count, window


def box_office_reply(text, view_context=None, subject_hint=''):
    """The one answer for every box office ask. Same words on every
    push; the only variable parts are the title, the count, and the
    window read off the open Journey IQ ticketing read."""
    title, count, window = _ticketing_count_from_view(view_context)
    if not title:
        title = str(subject_hint or '').strip()
        m = re.search(r"(?:for|to|of)\s+(?:the film\s+)?([A-Z][\w'&:!.-]*"
                      r"(?:\s+[A-Z0-9][\w'&:!.-]*){0,6})", str(text or ''))
        if not m:
            m = re.search(r"(?<![.!?]\s)(?<!^)\b(?!(?:What|How|Please|Give|"
                          r"Tell|Can|Just|The|Crosswalk|US|U\.S\.|I)\b)"
                          r"([A-Z][\w'&:!.-]+(?:\s+(?:of|the|and|"
                          r"[A-Z][\w'&:!.-]+))*)", str(text or ''))
        if m and not title:
            title = m.group(1).strip()
    title = title or 'the film'
    lines = ["Crosswalk does not predict box office performance, and no "
             "Crosswalk figure says who bought a ticket. The furthest "
             "point we see is the ticketing site."]
    if count:
        when = f" between {window.replace(' to ', ' and ')}" if window else ""
        lines.append(
            f"{count:,} people in the US went to a ticketing site or app "
            f"for a ticket to {title}{when}. That figure is projected to "
            f"the US general population: it is the incidence of US "
            f"individuals who visited a ticketing site for the title.")
    else:
        lines.append(
            f"What we can tell you for {title} is how many people in the "
            f"US went to a ticketing site or app for a ticket to it, "
            f"projected to the US general population. Open the title's "
            f"Digital Journey read, or ask for one, and that is the "
            f"number you will get.")
    lines.append(
        "It makes no prediction or claim on whether any of them then "
        "bought a ticket, so it is not a box office number and should "
        "not be reconciled against one. Reported box office, walk-up "
        "sales, and in-person purchases sit outside what we measure.")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------
# Sample size (Jenna 2026-10-05: "the answer will always be 10 million
# us gen pop panel ... the panel size is always that 10m"). Scott asked
# for "the size of the Crosswalk sample audience from January 1, 2026
# to date" and Prometheus tried to build a profile of "Teh Crosswalk
# Sample". The sample is a fixed 10 million US consumers in every
# window; the question never names an audience to build.
SAMPLE_SIZE = 10_000_000
US_GEN_POP_SIZE = 329_900_000
def is_sample_size_ask(text) -> bool:
    """A question about how big the Crosswalk sample itself is (single
    implementation in prometheus.guards; the router uses the same)."""
    from prometheus import guards as _g
    return _g.is_sample_size_ask(text)


def sample_size_reply(text) -> str:
    t = str(text or '')
    m = re.search(r"(?:from|since|between)\s+([A-Z][a-z]+ \d{1,2}, \d{4}|"
                  r"\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{4})"
                  r"(?:\s+(?:to|through|until|-)\s+(today|date|now|"
                  r"[A-Z][a-z]+ \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2}))?", t)
    window = ''
    if m:
        end = m.group(2) or ''
        end = 'today' if end.lower() in ('today', 'date', 'now', '') else end
        window = f" From {m.group(1)} to {end} it was {SAMPLE_SIZE // 1_000_000} million."
    return (
        f"The Crosswalk sample is {SAMPLE_SIZE // 1_000_000} million US "
        f"consumers, and it is the same size on every date.{window}\n\n"
        "The sample does not grow or shrink by window. What changes by "
        "window is how many of those 10 million did a given thing: "
        "watched a title, visited a site, signed up, searched a brand. "
        "Every count on the dashboard is that activity projected from the "
        f"10 million to the US general population of "
        f"{US_GEN_POP_SIZE / 1_000_000:.1f} million, so the figures read "
        "as US people, not as sample members.\n\n"
        "If you want the size of a specific audience inside the sample for "
        "that window, name the brand, title, or behavior and I will pull it."
    )


def is_cohort_churn_ask(text):
    """True for a churn / retention question about a SIGNUP COHORT
    (accounts that signed up in a month, a title's new subscribers,
    month-1 / month-2 retention), as opposed to platform-level churn."""
    t = str(text or '')
    if not t.strip():
        return False
    return bool(_COHORT_CHURN_RE.search(t) and _COHORT_FRAME_RE.search(t))


COHORT_CHURN_GUIDANCE = (
    'COHORT CHURN (this ask is the churn of a signup cohort, derived):\n'
    '1. The Subscriber IQ evidence carries the cohort size (new and '
    'reactivated signups, US figures) and the PLATFORM churn for the '
    'month. Platform churn is the base rate for the whole subscriber '
    'base, not the cohort; it anchors the floor.\n'
    '2. Research the platform\'s monthly churn with web_search from '
    'approved ground only: the parent company\'s earnings commentary '
    'and SEC filings, subscription-measurement firms (Antenna-class '
    'monthly churn and survival reads), eMarketer, Statista. Take the '
    'most recent trailing figure and its recent trend.\n'
    '3. Shape the cohort curve: a cohort acquired by one title churns '
    'well above the platform base in its first full month (binge '
    'titles highest, weekly cadence lower), steps down in month two, '
    'and approaches the platform base by month three. Reactivated '
    'accounts churn faster than net-new in month one. Ad-supported '
    'tiers churn faster than premium.\n'
    '4. Deliver a month-by-month table: starting cohort, churned, '
    'churn rate, remaining, every count projected to the US (never a '
    'panel count), messy last digits, each month\'s remaining equal '
    'to the next month\'s start. Then the share of the cohort still '
    'active at the end of the window and what it means for the next '
    'drop. Hard counts speak flat; the curve shape reads as '
    'directional language (leans, tends to).\n'
    '5. Never name a source, a firm, or a research step; never say the '
    'read does not carry cohort churn; never offer a build instead of '
    'the number.\n')


def subiq_answer_in_place(text):
    """True when a Subscriber IQ ask should be answered from the read
    rather than handed to the reasoned-read pass.

    Everything an insight / summary / comparison / platform-churn /
    timing / demographic question needs is in the file. Asks that name
    a measure the read never carries (revenue, forecasts, Profile IQ
    style indexes) and cohort-churn asks (derived from platform churn
    plus researched benchmarks) hand off to the deeper pass.
    """
    t = str(text or '')
    if not t.strip():
        return False
    if is_cohort_churn_ask(t):
        return False
    return not _SUBIQ_OUT_OF_READ_RE.search(t)


def _apply_dashboard_subiq_adjustments(key, parsed):
    """Make the off-screen parse match what the dashboard shows.

    The Subscriber IQ data route applies two serve-time adjustments the
    raw parser does not: admin episode overrides and the demographic
    projection rescale (age / gender projections sum to the projected
    new signups). Prometheus quoted the raw parse, so an off-screen
    answer could carry different projections than the open page
    (2026-10-02, Bria). Resolve the host lazily; never raise.
    """
    try:
        import sys as _sys
        host = _sys.modules.get('app')
        if host is None:
            return
        fn = getattr(host, 'apply_subscriber_episode_overrides', None)
        if callable(fn):
            fn(key, parsed)
        fn = getattr(host, 'normalize_demographics_gen_pop_to_nps', None)
        if callable(fn):
            fn(parsed)
    except Exception:
        pass


def _subiq_payload_cached(s3_client, subiq_bucket, key, parser):
    """Full parsed Subscriber IQ payload for one file. Within the TTL
    the cached parse is reused as-is; past it a HEAD revalidates the
    ETag before re-downloading."""
    now = time.time()
    with _xmod_lock:
        c = _subiq_payload_cache.get(key)
    if c and now - c['ts'] < _SUBIQ_PAYLOAD_TTL_S:
        return c['parsed']
    try:
        if c:
            head = s3_client.head_object(Bucket=subiq_bucket, Key=key)
            etag = (head.get('ETag') or '').strip('"')
            if etag and etag == c['etag']:
                with _xmod_lock:
                    _subiq_payload_cache[key] = {**c, 'ts': now}
                return c['parsed']
        resp = s3_client.get_object(Bucket=subiq_bucket, Key=key)
        etag = (resp.get('ETag') or '').strip('"')
        parsed = parser(resp['Body'].read().decode('utf-8'))
        _apply_dashboard_subiq_adjustments(key, parsed)
    except Exception:
        return c['parsed'] if c else None
    with _xmod_lock:
        _subiq_payload_cache[key] = {'etag': etag, 'ts': now,
                                     'parsed': parsed}
        if len(_subiq_payload_cache) > 32:
            for k, _ in sorted(_subiq_payload_cache.items(),
                               key=lambda kv: kv[1]['ts'])[:8]:
                _subiq_payload_cache.pop(k, None)
    return parsed


def find_subiq_title(s3_client, subiq_bucket, text, subject_hint='',
                     prefer_text=False):
    """(show, s3_key) when the subject hint or the ask names a title in
    the Subscriber IQ index; (None, None) otherwise.

    The hint wins by default (generated reads pass the resolved base
    subject). On the screen path the hint is the OPEN profile, so a
    title the question names outranks it (prefer_text=True): with
    Landman open, "how did Tulsa King's signups compare" must pull
    Tulsa King's acquisition read, not Landman's again."""
    index = _load_subiq_index(s3_client, subiq_bucket)
    if not index:
        return None, None

    def _from_hint():
        tk = _xmod_title_key(subject_hint)
        return index.get(tk) if tk else None

    def _from_text():
        # Base-name / initialism aware first (2026-10-02): "Analyze
        # SWAT Exiles" resolves to the SWAT Exiles Season 1 file.
        try:
            hits = find_subiq_titles_in_text(s3_client, subiq_bucket,
                                             text, limit=1)
        except Exception:
            hits = []
        if hits:
            show, key, _lm = hits[0]
            return (show, key)
        shows = {show for show, _k in index.values()}
        tk = _xmod_title_key(_xmod_subject_from_text(text, shows))
        return index.get(tk) if tk else None

    order = (_from_text, _from_hint) if prefer_text \
        else (_from_hint, _from_text)
    for fn in order:
        hit = fn()
        if hit:
            return hit
    return None, None


def render_subiq_evidence(parsed, show):
    """The compact structured evidence block from one parsed Subscriber
    IQ payload. Pure rendering (testable on a fixture); byte-capped at
    SUBIQ_EVIDENCE_MAX_BYTES by dropping the lowest-priority sections
    first."""
    parsed = parsed or {}
    md = parsed.get('metadata') or {}
    km = parsed.get('key_metrics') or {}
    asum = parsed.get('attribution_summary') or {}

    def _cnt(d, key='count'):
        return _xmod_fmt_count((d or {}).get(key))

    # US projection (2026-10-05, Jenna: "never dont project"). The
    # file's own Gen Pop column rides every count; a row without one
    # is projected at the file's factor so no panel count ever stands
    # alone in the prompt.
    try:
        from prometheus import projection as _proj
    except Exception:
        _proj = None
    _seed = []
    for _d in (km.get('total_watchers'), km.get('new_signups'),
               asum.get('total')):
        if isinstance(_d, dict) and _proj is not None:
            _pc, _pu = _proj._to_num(_d.get('count')), \
                _proj._to_num(_d.get('gen_pop'))
            if _pc and _pu and _pu > _pc:
                _seed.append((_pc, _pu))
    _factor = _proj.factor_from_pairs(_seed) if _proj is not None \
        else 32.99

    def _us(d, key):
        """'<panel> (<us> US)' for one count cell, projecting when the
        row carries no Gen Pop twin."""
        d = d or {}
        c = _xmod_fmt_count(d.get(key))
        if not c:
            return None
        us = _xmod_fmt_count(d.get('gen_pop'))
        if not us and _proj is not None:
            pv = _proj._to_num(d.get(key))
            if pv:
                us = _xmod_fmt_count(_proj.messy_projection(
                    pv, _factor, f"{show}|{key}"))
        return f"{c} ({us} US)" if us else c

    def _pair(d, label):
        d = d or {}
        c = _cnt(d)
        us = _xmod_fmt_count(d.get('gen_pop'))
        if c and us:
            return f"{label} {c} ({us} US)"
        if c and _proj is not None:
            return f"{label} {_us(d, 'count')}"
        if c or us:
            return f"{label} {c or us}"
        return None

    head_bits = []
    for k, lab in (('platform', 'platform'), ('date_range', 'window'),
                   ('genre', 'genre'), ('content_cadence', 'cadence')):
        v = str(md.get(k) or '').strip()
        if v:
            head_bits.append(f"{lab} {v}")

    # (priority, line): lower priority survives the byte cap longer.
    tagged = [
        (0, f"SUBSCRIBER ACQUISITION EVIDENCE ({show})"
            + (': ' + '; '.join(head_bits) if head_bits else '')),
        (0, "Measured show-to-platform acquisition for this title. "
            "These are the authoritative subscriber numbers: cite them "
            "flat and never contradict them. Every count below is "
            "written as 'panel (US)': the US figure is the one the "
            "reader gets, in prose, tables, and CSVs alike; the panel "
            "count never appears in an answer."),
    ]

    key_bits = [b for b in (
        _pair(km.get('total_watchers'), 'accounts viewed'),
        _pair(km.get('pre_existing'), 'pre-existing viewers'),
        _pair(km.get('clean_sample'), 'first-time viewers'),
        _pair(km.get('new_signups'), 'new signups'),
    ) if b]
    for k, lab in (('clean_conversion_rate', 'first-time conversion'),
                   ('total_conversion_rate', 'overall conversion'),
                   ('completion_rate', 'completion'),
                   ('second_screen_activity', 'second-screen'),
                   ('avg_days_to_signup', 'avg days to signup')):
        v = str(km.get(k) or '').strip()
        if v:
            key_bits.append(f"{lab} {v}")
    if key_bits:
        tagged.append((1, 'KEY METRICS: ' + '; '.join(key_bits) + '.'))

    attr_bits = [b for b in (
        _pair(asum.get('attributed'), 'attributed signups'),
        _pair(asum.get('dormant_reactive'), 'reactivated accounts'),
        _pair(asum.get('total'), 'total signups'),
    ) if b]
    if attr_bits:
        tagged.append((1, 'SIGNUPS ATTRIBUTED: ' + '; '.join(attr_bits)
                       + '.'))

    eps = [e for e in (parsed.get('episode_attribution') or [])
           if isinstance(e, dict)
           and isinstance(e.get('signups'), (int, float))]
    eps.sort(key=lambda e: -float(e['signups']))
    ep_bits = []
    for e in eps[:6]:
        lab = str(e.get('episode') or '').strip()
        if not lab:
            continue
        lab = lab if '/' in lab else f"Ep {lab}"
        d = str(e.get('episode_date') or '').strip()
        s = _us(e, 'signups')
        ep_bits.append(f"{lab}{f' ({d})' if d else ''} {s} signups")
    if ep_bits:
        tagged.append((2, 'TOP EPISODES (key drivers, by signups): '
                       + '; '.join(ep_bits) + '.'))

    tim_bits = []
    for t in (parsed.get('signup_timing') or [])[:8]:
        if not isinstance(t, dict):
            continue
        lab = str(t.get('timing') or '').strip()
        s = _us(t, 'signups')
        if lab and s:
            tim_bits.append(f"{lab} {s}")
    if tim_bits:
        tagged.append((3, 'SIGNUP TIMING (after availability): '
                       + '; '.join(tim_bits) + '.'))

    plat = str(md.get('platform') or '').strip() or 'the platform'
    mo_bits = []
    for m in (parsed.get('monthly_signups') or [])[-12:]:
        if not isinstance(m, dict):
            continue
        s = _us(m, 'signups')
        if m.get('month') and s:
            w = _us({'watched_show': m.get('watched_show')}, 'watched_show')
            pct = str(m.get('percentage') or '').strip()
            tail = ''
            if w and pct:
                tail = f" ({w} watched the show, {pct})"
            elif w:
                tail = f" ({w} watched the show)"
            mo_bits.append(f"{m['month']} {s}{tail}")
    if mo_bits:
        tagged.append((4, f'MONTHLY {plat.upper()} SIGNUPS (all new '
                       f'{plat} signups that month, show watchers in '
                       'parentheses): ' + '; '.join(mo_bits) + '.'))

    tp_bits = []
    for t in (parsed.get('post_signup_touchpoints') or [])[:5]:
        if not isinstance(t, dict):
            continue
        lab = str(t.get('touchpoint') or '').strip()
        u = _us(t, 'users')
        pct = str(t.get('percentage') or '').strip()
        if lab and lab.lower() != 'total' and u:
            tp_bits.append(f"{lab} visit {u}" + (f" ({pct})" if pct else ''))
    if tp_bits:
        tagged.append((4, 'SHOW AS FIRST VISITS AFTER SIGNUP (how soon '
                       'the new signup went to the show): '
                       + '; '.join(tp_bits) + '.'))

    demo = parsed.get('demographics') or {}
    demo_bits = []
    for a in (demo.get('age') or [])[:8]:
        if isinstance(a, dict) and a.get('age_range') \
                and str(a.get('percentage') or '').strip():
            demo_bits.append(f"{a['age_range']} {a['percentage']}")
    for g in (demo.get('gender') or [])[:3]:
        if isinstance(g, dict) and g.get('gender') \
                and str(g.get('percentage') or '').strip():
            demo_bits.append(f"{g['gender']} {g['percentage']}")
    if demo_bits:
        tagged.append((2, 'SIGNUP COHORTS (who converted): '
                       + '; '.join(demo_bits) + '.'))

    comp_bits = []
    for cpl in (parsed.get('competitive_platforms') or [])[:5]:
        if isinstance(cpl, dict) and cpl.get('platform') \
                and str(cpl.get('percentage') or '').strip():
            comp_bits.append(f"{cpl['platform']} {cpl['percentage']}")
    if comp_bits:
        tagged.append((5, 'ALSO SUBSCRIBED (overlap): '
                       + '; '.join(comp_bits) + '.'))

    ch_bits = []
    for m in (parsed.get('monthly_churn') or [])[-6:]:
        if isinstance(m, dict) and m.get('month') \
                and _xmod_fmt_count(m.get('churned')):
            pct = str(m.get('percentage') or '').strip()
            ch_bits.append(f"{m['month']} {_us(m, 'churned')}"
                           + (f" ({pct})" if pct else ''))
    if ch_bits:
        # 2026-10-02 (Bria): churn moves up the keep order and carries
        # its definition. This read measures PLATFORM churn (all
        # accounts on the platform that stopped visiting that month),
        # not the churn of this show's own signup cohort. Prometheus
        # must say which one it is quoting and never present a reasoned
        # cohort number as if it came from this read.
        tagged.append((3, f'MONTHLY {plat.upper()} CHURN (all {plat} '
                       'accounts that stopped visiting that month, as '
                       'a share of the platform base; this is NOT the '
                       "churn of this show's signup cohort, which this "
                       'read does not measure): ' + '; '.join(ch_bits)
                       + '. If asked about churn of the signups this '
                       'show brought in (cohort churn), return '
                       'action=generate_metrics with the cohort and '
                       'window; the deeper pass derives the cohort '
                       'curve from this platform churn plus the '
                       "platform's published monthly churn. Never say "
                       'the read does not carry it.'))

    if len(tagged) <= 2:
        return ''

    def _assemble(items):
        return '\n'.join(ln for _p, ln in items)

    keep = list(tagged)
    while len(_assemble(keep).encode('utf-8')) > SUBIQ_EVIDENCE_MAX_BYTES:
        drop_i, drop_p = None, -1
        for i in range(len(keep) - 1, -1, -1):
            if keep[i][0] > drop_p:
                drop_i, drop_p = i, keep[i][0]
        if drop_i is None or drop_p == 0:
            body = _assemble(keep).encode('utf-8')
            return body[:SUBIQ_EVIDENCE_MAX_BYTES].decode(
                'utf-8', errors='ignore').strip()
        keep.pop(drop_i)
    return _assemble(keep).strip()


def build_subiq_evidence_block(s3_client, subiq_bucket, parser, text,
                               subject_hint='', prefer_text=False,
                               skip_show=''):
    """The full Subscriber IQ evidence block for a generated read or a
    screen ask. Returns (block, show); ('' , None) when no Subscriber
    IQ title matches the ask or its base subject, the matched title is
    the one already on screen (skip_show), or the file cannot be
    parsed."""
    if s3_client is None or not subiq_bucket or parser is None:
        return '', None
    try:
        # Several library titles named in one ask ("compare the first
        # three days of X to Y", 2026-10-02): every named read rides
        # the prompt so the comparison is answered from the data, on
        # the screen path and the no-screen generated-read path alike.
        named = find_subiq_titles_in_text(s3_client, subiq_bucket, text)
        if len(named) >= 2:
            blocks, shows = [], []
            for show, key, _lm in named:
                if skip_show and _subiq_norm(skip_show) == _subiq_norm(show):
                    continue
                parsed = _subiq_payload_cached(s3_client, subiq_bucket,
                                               key, parser)
                if parsed:
                    blocks.append(render_subiq_evidence(parsed, show))
                    shows.append(show)
            if blocks:
                return '\n\n'.join(blocks), shows[0]
        show, key = find_subiq_title(s3_client, subiq_bucket, text,
                                     subject_hint, prefer_text=prefer_text)
        if not key:
            return '', None
        if skip_show and _xmod_title_key(skip_show) == _xmod_title_key(show):
            return '', None
        parsed = _subiq_payload_cached(s3_client, subiq_bucket, key,
                                       parser)
        if not parsed:
            return '', None
        return render_subiq_evidence(parsed, show), show
    except Exception:
        return '', None


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


# First-party frame guards (2026-09-28, Phase 2 of the improvement
# plan): the prompts forbid source citations and off-clickstream
# claims, but nothing checked the OUTPUT side. These detectors close
# that: a sentence that cites a research vendor or asserts behavior a
# clickstream cannot observe (awareness, ad recall, stated intent,
# in-store traffic, linear tune-in) is removed whole inside
# scrub_user_text - a reply minus one sentence stays coherent, a
# shipped citation breaks the product frame. Word-swapping a citation
# is never attempted: "according to [the analysis]" reads wrong.
_CITATION_RX = re.compile(
    r'\b(?:statista|nielsen|pew(?:\s+research)?|emarketer|yougov|'
    r'comscore|sensor\s*tower|data\.ai|mri[\s-]?simmons|kantar|'
    r'parrot\s+analytics|antenna|samba\s*tv|luth)\b'
    r'|\baccording to (?!the profile\b|the file\b|the data\b|'
    r'your dashboard\b|crosswalk\b|the read\b)'
    r'|\bas reported by\b|\bsourced? from\b|\bper (?:a|an|the)? ?'
    r'(?:survey|study|report)\b|\bsurveys? (?:found|show|suggest)\b'
    r'|\bindustry (?:reports?|estimates?) (?:say|show|suggest)\b',
    re.IGNORECASE)

_OFFCLICK_RX = re.compile(
    r'\b(?:brand\s+)?awareness\b|\bad\s+recall\b|\brecall(?:ed)?\s+'
    r'seeing\b|\bintend(?:s)?\s+to\s+(?:buy|purchase|subscribe)\b'
    r'|\bstated\s+intent\b|\bfoot\s*traffic\b|\bfootfall\b'
    r'|\bin[\s-]store\s+(?:visits?|traffic|purchases?)\b'
    r'|\b(?:watched|viewed|tuned\s+in)\s+on\s+(?:cable|linear|'
    r'broadcast\s+tv)\b|\bover[\s-]the[\s-]air\b'
    r'|\bword\s+of\s+mouth\b|\battended\s+in\s+person\b',
    re.IGNORECASE)

_SENTENCE_SPLIT_RX = re.compile(r'(?<=[.!?])\s+')


def contains_source_citation(text):
    return bool(_CITATION_RX.search(str(text or '')))


def contains_offclickstream_claim(text):
    return bool(_OFFCLICK_RX.search(str(text or '')))


def _drop_frame_breaking_sentences(s):
    """Remove whole sentences that cite a vendor or assert an
    off-clickstream behavior. Never empties a reply: when every
    sentence would drop, the text returns unchanged (the calm layers
    upstream own that case)."""
    if not (_CITATION_RX.search(s) or _OFFCLICK_RX.search(s)):
        return s
    out_lines = []
    changed = False
    for line in s.split('\n'):
        parts = _SENTENCE_SPLIT_RX.split(line) if line.strip() else [line]
        kept = [p for p in parts
                if not (_CITATION_RX.search(p) or _OFFCLICK_RX.search(p))]
        if len(kept) != len(parts):
            changed = True
        out_lines.append(' '.join(kept).strip() if line.strip()
                         else line)
    out = '\n'.join(out_lines)
    out = re.sub(r'\n{3,}', '\n\n', out).strip()
    if not out:
        return s
    if changed:
        try:
            print('[scrub] dropped frame-breaking sentence(s) '
                  '(citation or off-clickstream claim)')
        except Exception:
            pass
    return out


def scrub_user_text(text):
    """Defense-in-depth vocabulary pass on any Prometheus text headed
    to the user: banned internal terms replaced with product language,
    em / en dashes replaced with hyphens, and whole sentences dropped
    when they cite a research vendor or assert off-clickstream
    behavior (2026-09-28 Phase 2)."""
    s = str(text or '')
    if not s:
        return s
    s = s.replace('\u2014', ' - ').replace('\u2013', '-')
    s = s.replace('\u2015', ' - ')
    for rx, rep in _SCRUB_COMPILED:
        s = rx.sub(lambda m, _r=rep: (_r[:1].upper() + _r[1:]) if m.group(0)[:1].isupper() else _r, s)
    # 'estimate' as a noun (2026-10-06): a figure, never an estimate.
    s = re.sub(r'\b([Ee])stimates?\b',
               lambda m: ('Figure' if m.group(1) == 'E' else 'figure') + ('s' if m.group(0).endswith('s') else ''), s)
    s = _drop_frame_breaking_sentences(s)
    # Method language (2026-10-06, Jenna: "never mention anything that
    # sounds synthetic"): sentences about how a figure was made go;
    # the figures stay. One rule, every exit that calls this.
    try:
        from prometheus import guards as _guards
        s = _guards.scrub_method_language(s)
    except Exception:
        pass
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


_SEARCH_DEMAND_SYSTEM_PROMPT_T = """You are Prometheus, Crosswalk's senior audience strategist. The user is asking a SEARCH-JOURNEY DEMAND question: how people find a title or brand, what they search, which platform the searches point at, and what happens after the search. You produce the study for the subject they name, from Crosswalk's first-party US measurement of search, app, and play behavior.

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
- Today is __TODAY__. Resolve relative windows (last 12 months, past 90 days, since January) against today.
- If the user names a window, use it. Otherwise, when you know the subject's real streaming or release window, use that (a premiere-to-date window like 2026-08-16 to 2026-08-24 is the right shape). Otherwise default to the trailing 12 months, __T12_START__ to __T12_END__.

PUBLISHED MEASUREMENTS
- The user prompt may carry a PUBLISHED MEASUREMENTS block: numbers Crosswalk has already delivered for this subject on earlier questions. BINDING. A repeat of the same measurement restates the exact published number. An overlapping or adjacent measurement (different window, a share of a published total) must be arithmetically consistent with what was published.
- TWO-AUDIENCE READS: when the data carries a COMPARISON PROFILE, deliver the genuinely two-sided read the ask wants - both audiences quantified on the same definitions, side by side. Any overlap count sits at or below the smaller audience; a deduped union sits between the larger audience and the sum; each side stays consistent with its own published measurements. Cohort counts sit strictly inside their parent totals.

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
    # No sentence boundary: end on the last complete list item (a
    # comma or 'and' past the midpoint) so a number-dense read never
    # ships a dangling 'Culture.' where 'Culture Kings (5.5% ...)' was
    # (2026-10-06), else the last word boundary.
    li = max(cut.rfind(', '), cut.rfind(' and '))
    if li >= int(limit * 0.6):
        head = cut[:li]
        if head.count('(') == head.count(')'):
            return head.rstrip(' ,;:-') + '.'
    sp = cut.rfind(' ')
    out = (cut[:sp] if sp > 0 else cut)
    if out.count('(') > out.count(')'):
        out = out[:out.rfind('(')]
    return out.rstrip(' ,;:-') + '.'


def _clip_label(text, limit=120):
    """Length-bound a subject / cohort label without cutting mid-word
    or stranding an open parenthetical (2026-10-01: a 14-title list
    subject shipped as '...Almost Heroes, Nip/T' - the raw [:120]
    slice cut inside a title). If the cut lands inside an unbalanced
    '(...)', the whole parenthetical is dropped so a list subject
    collapses to its clean stem ('14-title catalog set')."""
    t = str(text or '').strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    if cut.count('(') > cut.count(')'):
        stem = cut[:cut.rfind('(')].rstrip(' ,;:-')
        if len(stem) >= 8:
            return stem
    m = max(cut.rfind(' '), cut.rfind(','))
    if m >= int(limit * 0.4):
        cut = cut[:m]
    return cut.rstrip(' ,;:-(')


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
        'subject': _clip_label(subj),
        'platform': str(data.get('platform') or '').strip()[:80],
        'rival': (str(data.get('rival') or '').strip()[:80] or None),
        'window_label': str(data.get('window_label') or '').strip()[:80],
        'window_start': str(data.get('window_start') or '').strip()[:12],
        'window_end': str(data.get('window_end') or '').strip()[:12],
        'cohort_label': str(data.get('cohort_label') or '').strip()[:160],
        'headline': _clip_text(data.get('headline'), 300),
        'reads': [_clip_text(r, 480)
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
    'compare', 'analyze', 'analyse',
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
    # The audience named after "fans of / audience of / viewers of /
    # followers of / listeners of" IS the subject, even when a brand in
    # the same sentence is longer ("are fans of Gunna more likely to buy
    # Under Armour": Gunna, not Under Armour; 2026-10-06).
    m_aud = re.search(
        r'\b(?:fans|fanbase|audience|viewers|followers|listeners|watchers|'
        r'subscribers|customers|buyers|shoppers)\s+of\s+(?:the\s+)?'
        r'((?:[A-Z][A-Za-z0-9&\'\+\.]*)(?:\s+(?:[A-Z][A-Za-z0-9&\'\+\.]*|of|the|and|&))*)',
        str(text or ''))
    if m_aud:
        aud = re.sub(r'\s+(?:of|the|and|&)$', '', m_aud.group(1).strip())
        if aud and aud.lower().strip('.') not in _SUBJ_STOPWORDS:
            return aud[:80]
    runs = re.findall(r'\b([A-Z][A-Za-z0-9&\'\+\.]*(?:\s+[A-Z][A-Za-z0-9'
                      r'&\'\+\.]*)*)\b', str(text or ''))
    best = ''
    for run in runs:
        parts = run.split()
        words = [w for w in parts
                 if w.lower().strip('.') not in _SUBJ_STOPWORDS]
        # A capitalized "The" that opens a title inside the run stays
        # with it: "The Office", "The Bear" (2026-10-08). A sentence-
        # leading "The" with no title after it still drops.
        if words and len(parts) >= 2:
            for i, w in enumerate(parts[:-1]):
                if w == 'The' and parts[i + 1] == words[0] \
                        and (i > 0 or len(words) <= 3):
                    words = ['The'] + words
                    break
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
    r'viewership|size)|'
    # A named window is quantity phrasing (2026-10-01 eval wave):
    # "unique viewers for the trailing 90 days" is a count ask.
    r'trailing \d{1,3}[\s-]?(?:day|week|month)s?|'
    r'(?:last|past) \d{1,3}[\s-]?(?:day|week|month)s?)\b',
    re.IGNORECASE)

_GENERATE_NOUN_RX = re.compile(
    r'\b(view(?:ed|ers|ership|ing)?|watch(?:ed|ing)?|stream(?:ed|s|ing|'
    r'ers)?|subscri(?:bed|bers?|ptions?)|sign(?:ed)?[\s-]?ups?|'
    r'search(?:ed|es|ers)?|quer(?:y|ies)|bought|buy(?:ers)?|'
    r'purchas(?:ed|es|ers)?|shopp(?:ed|ers)|download(?:s|ed)?|'
    r'install(?:s|ed)?|users?|accounts?|sessions?|plays?|listen(?:ed|'
    r'ers|ing)?|engag(?:ed|ement)|visit(?:s|ed|ors)?|audience|reach|'
    r'universe|cuts?)\b',
    re.IGNORECASE)

# Movement / refresh phrasings (2026-10-01): "what moved since my
# last read", the quarter-end wrap, and the tracker-mover follow-up
# are metric asks by construction - they are the house proactive
# openers - and need no quantity phrasing or behavior noun. Build/
# pull exclusions still run first.
_METRIC_REFRESH_RX = re.compile(
    r"\bwhat(?:'s| is| has)? (?:moved|changed|shifted)\b"
    r"|\bsince (?:my|our|the) last (?:read|ask|pull|look)\b"
    r"|\bquarter[\s-]?(?:end|close) read\b"
    r"|\bbiggest (?:shifts|movers|changes)\b"
    r"|\bbehind the (?:move|jump|spike|drop|surge)\b"
    r"|\blatest tracked week\b",
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
    if _METRIC_REFRESH_RX.search(t):
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


# ---------------------------------------------------------------------------
# So-what asks (2026-10-06, Jenna on Emmet's "why does this matter and
# what can I do with these insights?": the reply "was more a read than
# telling him why it mattered and what to do with the data to make
# money"). A so-what ask is answered as a plan: why it matters in money
# terms, then the moves, each tied to a number, then the first step.
# ---------------------------------------------------------------------------
_SO_WHAT_RX = re.compile(
    r"\bwhy\s+(?:does|do|would|should|did)\s+(?:this|that|it|these|any\s+of\s+this)\s+matter\b"
    r"|\bso\s+what\b"
    r"|\bwhat\s+(?:can|should|do|could|would)\s+(?:i|we|he|she|they|brock|the\s+creator|a\s+creator)\s+do\s+with\b"
    r"|\bhow\s+(?:do|can|should|could|would)\s+(?:i|we|he|she|they)\s+(?:use|monetize|make\s+money|act\s+on|apply|leverage|turn)\b"
    r"|\bmake\s+money\b|\bmonetiz\w+"
    r"|\bwhat\s+should\s+(?:i|we|he|she|they)\s+do\b"
    r"|\bnext\s+steps?\b"
    r"|\bhow\s+(?:do|can|should)\s+(?:i|we)\s+(?:sell|pitch|price|package|position)\b"
    r"|\bwhat\s+(?:are|is)\s+the\s+(?:takeaways?|implications?|plays?|moves?|so\s+what|upshot)\b"
    r"|\bwhat\s+(?:would|should|do)\s+(?:you|we)\s+recommend\b"
    r"|\bwhy\s+(?:would|does|should)\s+(?:this|that|it)\s+matter\s+(?:for|to)\b"
    r"|\bwhat\s+does\s+(?:this|that|it)\s+mean\s+for\s+(?:me|us|my|our|the\s+business|revenue|sponsors?)\b",
    re.I)


def is_so_what_ask(text):
    """True for a why-does-this-matter / what-do-I-do-with-it ask. Build
    phrasing and deck asks are excluded as for the strategy playbook."""
    t = str(text or '').strip()
    if not t or len(t) > 600:
        return False
    if not _SO_WHAT_RX.search(t):
        return False
    try:
        if _is_build_request(t) or detect_deck_intent(t):
            return False
    except Exception:
        pass
    return True


SO_WHAT_GUIDANCE = (
    'SO-WHAT PLAYBOOK (this ask is "why does this matter / what do I do '
    'with it"):\n'
    'The reader does not want another read. They want to know why the '
    'numbers matter to their money and exactly what to do next. Fill the '
    'JSON this way:\n'
    '- "headline": one sentence that answers the money question flat '
    '(what this audience is worth to them and the single biggest move).\n'
    '- "metrics": at most 3, only the figures the moves lean on.\n'
    '- "reads": the plan. reads[0] is WHY IT MATTERS: two or three '
    'sentences in plain words on what these numbers mean for revenue, '
    'pricing power, or who will pay (who the audience is to a buyer, what '
    'it lets the reader charge or sell, what it rules out). Every '
    'remaining read is one MOVE: start with an imperative verb (Pitch, '
    'Price, Package, Build, Lead with, Skip), name who to go to and with '
    'what offer, give the number from the profile that justifies it and '
    'the dollar or outcome it points to (a rate per thousand, a sponsor '
    'tier, a deal size, a conversion the audience will deliver). Three to '
    'five moves, most valuable first. The LAST read is "First step this '
    'week: ..." one concrete action the reader can take in the next '
    'seven days.\n'
    '- Plain words a smart 16-year-old follows. No index without a plain '
    'comparison next to it ("2x the US average"). No methodology, no '
    'hedging, no "this reads as" for the moves - say what to do.\n'
    '- Dollars: when you price inventory or a deal, show the arithmetic '
    'inside the sentence (people x rate = dollars), put a $ sign on every '
    'dollar figure including the result, and keep every figure messy '
    '(never a round number).\n')


_DOLLAR_RESULT_RX = re.compile(r'(=\s*)(?<!\$)(\d{1,3}(?:,\d{3})+|\d{4,})(?![\d,]*\s*(?:people|viewers|seats|buyers|users|accounts|companies|signups|%))')
_PER_THOUSAND_RX = re.compile(r'(?<![\$\d])(\d{1,3}(?:\.\d+)?)(\s+per\s+thousand)')


def _dollarize(text):
    """A plain reader cannot tell 53,068 from $53,068. In a sentence that
    already prices something in dollars, the result of the arithmetic
    and any 'N per thousand' rate carry the sign (2026-10-06)."""
    out = []
    parts = re.split(r'((?<=[.!?])\s+)', str(text or ''))  # separators kept
    for k, sent in enumerate(parts):
        if k % 2 == 0 and ('$' in sent or re.search(r'\bper thousand\b', sent)):
            sent = _PER_THOUSAND_RX.sub(r'$\1\2', sent)
            if '$' in sent:
                sent = _DOLLAR_RESULT_RX.sub(r'\1$\2', sent)
        out.append(sent)
    return ''.join(out)


# ---------------------------------------------------------------------------
# Multi-question messages (2026-10-06, Jenna on Emmet's three questions in
# one message: Prometheus "should have realized these were 3 questions and
# answered them all"). Every question gets its own answered part.
# ---------------------------------------------------------------------------
_Q_OPEN_RX = re.compile(
    r"^\s*(?:can|could|would|will|do|does|did|is|are|was|were|should|how|what|which|who|"
    r"why|where|when|whats|what's|tell me|show me|list|identify|define|explain|compare)\b", re.I)


def split_questions(text):
    """The distinct questions inside one message, in order. A part counts
    when it ends in '?' or opens like a question; fragments under four
    words are folded into their neighbor. Returns [] when the message
    carries fewer than two questions."""
    t = str(text or '').strip()
    if not t or len(t) > 1500:
        return []
    raw_parts = [p.strip() for p in re.split(r'(?<=\?)\s+|\n\s*\n|\n', t) if p.strip()]
    parts = []   # [text, is_question]
    for p in raw_parts:
        p = re.sub(r'^\s*(?:[-*\u2022]|\d+[.)])\s*', '', p).strip()
        if not p:
            continue
        is_q = p.endswith('?') or bool(_Q_OPEN_RX.match(p))
        if parts and len(p.split()) < 4 and not p.endswith('?'):
            parts[-1][0] = parts[-1][0] + ' ' + p   # a trailing fragment rides its question
            continue
        parts.append([p, is_q])
    qs = [p for p, is_q in parts if is_q]
    if len(qs) < 2:
        return []
    return qs[:6]


def multi_question_guidance(qs):
    lines = ['THIS MESSAGE CARRIES SEVERAL QUESTIONS. Answer every one, in order, '
             'in the JSON "reads" list: reads[i] answers question i and STARTS with '
             'the tag "[Q{n}] " followed by a plain restatement of the question in '
             'six words or fewer, a colon, then the answer in two to five sentences '
             'with the measured numbers (penetration, projected US people, and the '
             'plain comparison to the US average). Never answer only the first '
             'question. The headline covers the most important finding across them. '
             'Metrics: at most three, the ones the answers lean on.']
    for i, q in enumerate(qs, start=1):
        lines.append(f'  Q{i}: {q}')
    return '\n'.join(lines)


def format_multi_question_reply(res):
    """One numbered part per question; the model's [Qn] tags become the
    part headers; metrics ride inline."""
    lines = []
    if res.get('headline'):
        lines.append(str(res['headline']).strip())
        lines.append('')
    mets = [m for m in (res.get('metrics') or []) if isinstance(m, dict)][:3]
    if mets:
        lines.append('The numbers behind this: ' + '; '.join(
            f"{m.get('label')} {_fmt_metric_value(m)}" for m in mets) + '.')
        lines.append('')
    qs = list(res.get('_multi') or [])
    reads = [str(r).strip() for r in (res.get('reads') or []) if str(r).strip()]
    n = 0
    for r in reads:
        m = re.match(r'^\[?Q\s*(\d+)\]?\s*[:.\-]?\s*(.*)$', r, re.S | re.I)
        body = m.group(2).strip() if m else r
        head, sep, rest = body.partition(':')
        n += 1
        if sep and len(head.split()) <= 9:
            lines.append(f"{n}. {head.strip()}")
            lines.append(rest.strip())
        else:
            q = qs[n - 1] if n - 1 < len(qs) else ''
            if q:
                lines.append(f"{n}. {q.rstrip('?').strip()}")
            lines.append(body)
        lines.append('')
    missing = len(qs) - n
    if missing > 0 and qs:
        lines.append('Still open: ' + '; '.join(q.rstrip('?') for q in qs[n:]) + '. Ask me again and I take those next.')
    return scrub_user_text('\n'.join(lines).strip())


def format_so_what_reply(res):
    """Action-shaped reply for a so-what ask: the money answer, why it
    matters, numbered moves, the first step. Metrics ride inline."""
    lines = []
    if res.get('headline'):
        lines.append(str(res['headline']).strip())
        lines.append('')
    mets = [m for m in (res.get('metrics') or []) if isinstance(m, dict)][:3]
    if mets:
        lines.append('The numbers behind this: ' + '; '.join(
            f"{m.get('label')} {_fmt_metric_value(m)}" for m in mets) + '.')
        lines.append('')
    reads = [str(r).strip() for r in (res.get('reads') or []) if str(r).strip()]
    # the model sometimes labels the parts itself; the headers below do that
    reads = [re.sub(r'^(?:why\s+it\s+matters|what\s+to\s+do(?:\s+with\s+it)?|move\s*\d+|'
                    r'step\s*\d+|\d+[.)])\s*[:\-.]?\s*', '', r, flags=re.I).strip() for r in reads]
    reads = [_dollarize(r) for r in reads]
    first_step = None
    moves = []
    why = None
    for r in reads:
        rl = r.lower()
        if first_step is None and rl.startswith('first step'):
            first_step = r
        elif why is None:
            why = r
        else:
            moves.append(r)
    if why:
        lines.append('Why it matters')
        lines.append(why)
        lines.append('')
    if moves:
        lines.append('What to do with it')
        for i, mv in enumerate(moves, start=1):
            lines.append(f"{i}. {mv}")
        lines.append('')
    if first_step:
        lines.append(first_step)
    return scrub_user_text('\n'.join(lines).strip())


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



# ---------------- Panel facts (2026-09-30, Jenna) -----------------
# "lets now do the panel-fact queries upgrades": a factual ask the
# shipped base file answers exactly returns the file's own numbers in
# one step. Scope is what already sits in the bucket (profile files
# plus the US baseline) - never the clickstream. The detector is
# deliberately conservative: anything analytical, comparative, or
# ambiguous returns None and rides the full read unchanged.

_PANEL_FACT_BLOCKERS = re.compile(
    r"\b(?:why|should|recommend|compare[ds]?|versus|vs\.?|strategy|"
    r"white\s*space|opportunit|journey|campaign|creative|pitch|deck|"
    r"churn|over.?index|overlap|trend|trajector|drove|convert|"
    r"partnership|value|worth|report|insight|analy[sz]|deep dive|"
    r"story|angle|persona|summar|against|"
    # 2026-10-02 audit: evaluative and retention asks are judgments,
    # not lookups ("is this a strong number?", "what % stayed?").
    r"strong|weak|good|bad|healthy|typical|normal|benchmark|average|"
    r"mean|stayed|stay|carried|retain|retention|kept|lapsed|"
    r"cancel|dropped|drop.?off|lift|growth|grew|decline|fell|"
    r"higher|lower|better|worse|improv)\b", re.I)

# A panel fact is one short question. Two sentences or two question
# marks is a conversation, which rides the full read (2026-10-02).
_PANEL_FACT_MULTI_RX = re.compile(r"[.?!]\s+\S")

_PANEL_FACT_FRAME = re.compile(
    r"(?:%|percent(?:age)?|\bshare\b|\bsplit\b|\bbreakdown\b|"
    r"\bmix\b|\bskew\b|how\s+(?:old|many)|what\s+is|what'?s|"
    r"\bpenetration\b)", re.I)

# demo keyword -> (canonical Column, bucket or None)
_PANEL_FACT_DEMOS = (
    (re.compile(r"\bfemales?\b|\bwomen\b", re.I), 'GENDER', 'FEMALE'),
    (re.compile(r"\bmales?\b|\bmen\b", re.I), 'GENDER', 'MALE'),
    (re.compile(r"\bgender\b", re.I), 'GENDER', None),
    (re.compile(r"\bages?\b|\bhow old\b|\bage mix\b", re.I),
     'AGE', None),
    (re.compile(r"\bincome\b|\bhhi\b|\bearn\b", re.I),
     'INCOME', None),
    (re.compile(r"\bhispanic\b|\blatino\b", re.I),
     'ETHNICITY', 'HISPANIC'),
    (re.compile(r"\bethnicit", re.I), 'ETHNICITY', None),
    (re.compile(r"\beducation\b|\bcollege\b", re.I),
     'EDUCATION', None),
    (re.compile(r"\bparents?\b|\bparental\b", re.I),
     'PARENTAL STATUS', None),
    (re.compile(r"\bmarried\b|\bsingle\b|\brelationship\b", re.I),
     'RELATIONSHIP', None),
    (re.compile(r"\blgbtq?\+?\b|\borientation\b", re.I),
     'SEXUAL ORIENTATION', None),
    (re.compile(r"\boccupation\b|\bjobs?\b", re.I),
     'OCCUPATION', None),
)

_PANEL_FACT_BRAND_RES = (
    re.compile(
        r"(?:what|how)\s+(?:%|percent(?:age)?\b|share\b|many\b)"
        r"[^.?!\n]{0,44}?\b(?:use|uses|watch(?:es)?|stream(?:s)?|"
        r"shop(?:s)?(?:\s+at)?|bu(?:y|ys)|subscribe(?:s)?\s+to|"
        r"are\s+on|on)\s+"
        r"(?P<brand>[A-Za-z0-9&+'.\- ]{2,40}?)\s*\??$", re.I),
    re.compile(
        r"(?P<brand>[A-Za-z0-9&+'.\- ]{2,40}?)(?:'s)?\s+"
        r"penetration\b", re.I),
    re.compile(
        r"\bpenetration\s+(?:of|for)\s+"
        r"(?P<brand>[A-Za-z0-9&+'.\- ]{2,40}?)\s*\??$", re.I),
    re.compile(
        r"\bshare\s+(?:that|who)\s+(?:use|watch|stream|shop\s+at|"
        r"buy|subscribe\s+to)\s+"
        r"(?P<brand>[A-Za-z0-9&+'.\- ]{2,40}?)\s*\??$", re.I),
)

_PANEL_FACT_BRAND_TAIL = re.compile(
    r"\s*(?:in|for|on|across|among)\s+(?:this|that|the|their|her|his"
    r")\b.*$|\s*(?:of\s+them|here|right\s+now|today)\s*$", re.I)

_PANEL_FACT_TOP_RE = re.compile(
    r"\b(?:top|biggest|most\s+(?:used|watched|shopped|popular))\s+"
    r"(?:(?P<n>\d{1,2})\s+)?"
    r"(?P<cat>[A-Za-z][A-Za-z /&']{2,34}?)"
    r"(?=\s+(?:for|in|on|of|among|across|here)\b|\s*\??$)", re.I)

_PANEL_FACT_SIZE_RE = re.compile(
    r"\bhow\s+(?:big|large)\b|"
    r"\baudience\s+size\b|\bsize\s+of\s+(?:this|the|that)\s+"
    r"audience\b|\btotal\s+audience\b", re.I)

# generic hint -> ordered candidate Columns (first present wins)
_PANEL_FACT_CAT_ALIASES = (
    (('qsr', 'fast food'), ('QSR',)),
    (('streaming service', 'streaming platform', 'streaming video',
      'streaming', 'svod'),
     ('STREAMING/PLATFORM', 'STREAMING VIDEO')),
    (('music',), ('STREAMING MUSIC',)),
    (('social',), ('SOCIAL MEDIA',)),
    (('search',), ('SEARCH ENGINE/AI',)),
    (('retailer', 'store', 'shop'), ('WHERE THEY SHOP', 'RETAILERS')),
    (('app', 'platform'), ('APP/PLATFORM', 'APP/PLATFORM USAGE')),
    (('brand',), ('MOST PURCHASED BRANDS',)),
    (('talent', 'celebrit'), ('TALENT',)),
    (('podcast',), ('PODCAST',)),
    (('game', 'gaming'), ('GAMES',)),
    (('bank',), ('BANKS', 'BANKING', 'DIGITAL BANKING')),
    (('travel',), ('TRAVEL',)),
    (('car', 'auto'), ('AUTOMOBILE',)),
    (('tv', 'cable', 'broadcast'), ('BROADCAST/CABLE',)),
)


def detect_panel_fact(text):
    """Parse a short factual ask into a lookup the base file can
    answer exactly. Returns {'kind': ...} or None (None = ride the
    full read)."""
    t = str(text or '').strip()
    if not t or len(t) > 220:
        return None
    if _PANEL_FACT_BLOCKERS.search(t):
        return None
    if _PANEL_FACT_MULTI_RX.search(t) or t.count('?') > 1:
        return None
    if _PANEL_FACT_SIZE_RE.search(t):
        return {'kind': 'size'}
    m_top = _PANEL_FACT_TOP_RE.search(t)
    if m_top:
        hint = re.sub(r"\s+", ' ', m_top.group('cat') or '').strip()
        hint = re.sub(
            r"\b(?:brands?|services?|platforms?|stores?|retailers?|"
            r"apps?|channels?|picks?|names?)\s*$", '', hint,
            flags=re.I).strip()
        if hint:
            n = 5
            try:
                n = max(2, min(10, int(m_top.group('n') or 5)))
            except (TypeError, ValueError):
                pass
            return {'kind': 'top', 'cat_hint': hint, 'n': n}
    if _PANEL_FACT_FRAME.search(t):
        for rx in _PANEL_FACT_BRAND_RES:
            m = rx.search(t)
            if m:
                brand = _PANEL_FACT_BRAND_TAIL.sub(
                    '', m.group('brand') or '').strip(" .,'\"")
                if 2 <= len(brand) <= 40 \
                        and not _PANEL_FACT_BLOCKERS.search(brand):
                    return {'kind': 'brand', 'brand': brand}
        for rx, col, bucket in _PANEL_FACT_DEMOS:
            if rx.search(t):
                return {'kind': 'demo', 'column': col,
                        'bucket': bucket}
    return None


def _panel_fact_rows(df, bp_col, want_col):
    """(value, bp) rows of one Column, BP-parsed, sorted desc."""
    want = _norm_cat(want_col)
    out = []
    for _, row in df.iterrows():
        if _norm_cat(row.get('Column')) != want:
            continue
        v = _parse_bp(row.get(bp_col))
        val = str(row.get('Value') or '').strip()
        if v is None or not val:
            continue
        out.append((val, v))
    out.sort(key=lambda r: -r[1])
    return out


def _panel_fact_column(df, hint):
    """Resolve a spoken category hint to a Column present on the
    file. None when nothing matches confidently."""
    h = _norm_cat(hint)
    if not h:
        return None
    present = []
    seen = set()
    for c in df.get('Column', []):
        n = _norm_cat(c)
        if n and n not in seen and n not in METADATA_COLS \
                and n not in DEMO_COLS:
            seen.add(n)
            present.append((n, str(c)))
    h_toks = h.split()
    for keys, cands in _PANEL_FACT_CAT_ALIASES:
        hit = False
        for k in keys:
            ku = k.upper()
            if ' ' in ku:
                hit = ku in h
            else:
                # Token-exact (plus plural / long-stem prefix) so a
                # short key never hijacks a longer word: 'app' must
                # not resolve 'apparel'.
                hit = any(tok == ku or tok == ku + 'S'
                          or (len(ku) >= 4 and tok.startswith(ku))
                          for tok in h_toks)
            if hit:
                break
        if hit:
            for cand in cands:
                for n, orig in present:
                    if n == _norm_cat(cand):
                        return orig
    for n, orig in present:
        if h == n or h in n:
            return orig
    return None


_PANEL_FACT_ACRONYMS = {
    'QSR', 'CPG', 'B2B', 'AI', 'TV', 'MLB', 'NBA', 'NFL', 'NHL',
    'MLS', 'WNBA', 'MILB', 'EST', 'TVOD', 'PVOD', 'SVOD', 'AVOD',
    'VMVPD', 'MVPD', 'DMA', 'LGBTQ', 'AL', 'NL', 'AFC', 'NFC',
}


def _panel_fact_cat_display(col):
    """Reader-facing category name: title case with acronyms kept
    upper (QSR stays QSR, never Qsr)."""
    parts = re.split(r'([/\s]+)', str(col or ''))
    out = []
    for p in parts:
        if not p or re.fullmatch(r'[/\s]+', p):
            out.append(p)
        elif p.upper() in _PANEL_FACT_ACRONYMS:
            out.append(p.upper())
        else:
            out.append(p.title())
    return ''.join(out)


def _panel_fact_pct(v):
    return f"{v:.1f}%"


def answer_panel_fact(fact, df, meta, genpop_map=None):
    """Answer one detected fact from the loaded base file. Returns
    {'reply', 'family', 'metrics', 'breakdown', 'followups'} or None
    when the file cannot answer exactly."""
    bp_col = (meta or {}).get('bp_col')
    if not bp_col or not isinstance(fact, dict):
        return None
    name = (meta or {}).get('name') or 'this audience'
    kind = fact.get('kind')
    if kind == 'size':
        proj = (meta or {}).get('proj')
        if not proj:
            return None
        window = (meta or {}).get('window')
        reply = (f"The {name} audience projects to {proj:,} people "
                 f"in the US"
                 + (f" across {window}." if window else "."))
        return {
            'reply': reply, 'family': 'audience size',
            'metrics': [{'name': 'projected_us_audience',
                         'label': 'Projected US audience',
                         'value': int(proj), 'unit': 'people',
                         'definition': 'Projected US audience for '
                                       'the base profile'}],
            'breakdown': None,
            'followups': ['Gender split for this audience',
                          'Top brands for this audience']}
    if kind == 'demo':
        col = fact.get('column') or ''
        rows = _panel_fact_rows(df, bp_col, col)
        if not rows and col == 'RELATIONSHIP':
            rows = _panel_fact_rows(df, bp_col, 'RELATIONSHIP STATUS')
        if not rows:
            return None
        dim = col.title().replace('_', ' ')
        bucket = fact.get('bucket')
        metrics = []
        if bucket:
            hit = next((r for r in rows
                        if bucket in _norm_cat(r[0])), None)
            if not hit:
                return None
            if _norm_cat(col) == 'GENDER' and len(rows) >= 2:
                other = next((r for r in rows
                              if _norm_cat(r[0]) != _norm_cat(hit[0])),
                             None)
                reply = (f"The {name} audience is "
                         f"{_panel_fact_pct(hit[1])} "
                         f"{hit[0].strip().lower()}"
                         + (f" and {_panel_fact_pct(other[1])} "
                            f"{other[0].strip().lower()}."
                            if other else "."))
            else:
                reply = (f"{hit[0].strip().title()} is "
                         f"{_panel_fact_pct(hit[1])} of the "
                         f"{name} audience.")
            metrics.append({
                'name': f"{_norm_brand(hit[0])[:40]}_share",
                'label': f"{hit[0].strip().title()} share",
                'value': round(hit[1], 4),
                'unit': 'pct_of_audience',
                'definition': f"{dim} bucket share of the audience"})
        else:
            lead = ', '.join(
                f"{v.strip()} {_panel_fact_pct(b)}"
                for v, b in rows[:6])
            reply = f"{name} {dim.lower()} split: {lead}."
            if len(rows) > 6:
                reply += " The full split rides the CSV."
            for v, b in rows[:8]:
                metrics.append({
                    'name': f"{_norm_brand(v)[:40]}_share",
                    'label': f"{v.strip().title()} share",
                    'value': round(b, 4),
                    'unit': 'pct_of_audience',
                    'definition': f"{dim} bucket share of the "
                                  f"audience"})
        breakdown = {'dimension': dim,
                     'share_basis': 'share of audience',
                     'rows': [{'label': v.strip(),
                               'share_pct': round(b, 4)}
                              for v, b in rows]}
        return {'reply': reply, 'family': 'demographics',
                'metrics': metrics, 'breakdown': breakdown,
                'followups': [f'Full demographic read on {name}',
                              'Top brands for this audience']}
    if kind == 'brand':
        want = _norm_brand(fact.get('brand'))
        if not want:
            return None
        best = None
        for _, row in df.iterrows():
            cat = _norm_cat(row.get('Column'))
            if cat in METADATA_COLS or cat in DEMO_COLS:
                continue
            val = str(row.get('Value') or '').strip()
            if _norm_brand(val) != want:
                continue
            v = _parse_bp(row.get(bp_col))
            if v is None or v >= 99.95:
                continue
            if best is None or v > best[2]:
                best = (val, str(row.get('Column')), v)
        if not best:
            return None
        b_val, b_cat, b_bp = best
        reply = (f"{b_val} reaches {_panel_fact_pct(b_bp)} of the "
                 f"{name} audience.")
        gp = None
        gmap = genpop_map or {}
        gp = gmap.get((_norm_cat(b_cat), want))
        if gp is None:
            cands = [v for (c, b), v in gmap.items() if b == want]
            gp = max(cands) if cands else None
        if gp and gp > 0:
            ratio = b_bp / gp
            reply += (f" The US average is {_panel_fact_pct(gp)}, "
                      f"so this audience runs {ratio:.1f}x the "
                      f"average.")
        peers = [r for r in _panel_fact_rows(df, bp_col, b_cat)
                 if r[1] < 99.95][:10]
        breakdown = {'dimension': _panel_fact_cat_display(b_cat),
                     'share_basis': 'audience reach',
                     'rows': [{'label': v,
                               'penetration_pct': round(b, 4)}
                              for v, b in peers]}
        metrics = [{'name': f"{want[:40]}_reach",
                    'label': f"{b_val} reach",
                    'value': round(b_bp, 4),
                    'unit': 'pct_of_audience',
                    'definition': f"Share of the audience reached "
                                  f"by {b_val} "
                                  f"({_panel_fact_cat_display(b_cat)})"}]
        return {'reply': reply, 'family': 'brand reach',
                'metrics': metrics, 'breakdown': breakdown,
                'followups': [f'Top {_panel_fact_cat_display(b_cat)} for this '
                              f'audience',
                              'Gender split for this audience']}
    if kind == 'top':
        col = _panel_fact_column(df, fact.get('cat_hint'))
        if not col:
            return None
        subj_norm = _norm_brand(name)
        rows = [r for r in _panel_fact_rows(df, bp_col, col)
                if r[1] < 99.95 and _norm_brand(r[0]) != subj_norm]
        if len(rows) < 2:
            return None
        n = int(fact.get('n') or 5)
        picks = rows[:n]
        listing = ', '.join(f"{v} {_panel_fact_pct(b)}"
                            for v, b in picks)
        reply = (f"Top {len(picks)} {_panel_fact_cat_display(col)} for the {name} "
                 f"audience: {listing}.")
        breakdown = {'dimension': _panel_fact_cat_display(col),
                     'share_basis': 'audience reach',
                     'rows': [{'label': v,
                               'penetration_pct': round(b, 4)}
                              for v, b in rows[:max(n, 10)]]}
        metrics = [{'name': f"{_norm_brand(v)[:40]}_reach",
                    'label': f"{v} reach", 'value': round(b, 4),
                    'unit': 'pct_of_audience',
                    'definition': f"Share of the audience reached "
                                  f"by {v} "
                                  f"({_panel_fact_cat_display(col)})"}
                   for v, b in picks[:3]]
        return {'reply': reply, 'family': 'category rank',
                'metrics': metrics, 'breakdown': breakdown,
                'followups': ['Gender split for this audience',
                              f'How {picks[0][0]} compares to the '
                              f'US average']}
    return None


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


def seat_price_label(amount):
    """A price in the seat's currency ($300 / £300). Falls back to
    dollars outside a request (Jenna 2026-10-08, GBP seats)."""
    try:
        import sys as _sys
        _app = _sys.modules.get('app')
        if _app is not None and hasattr(_app, '_usd_label'):
            lbl = _app._usd_label(amount)
            if lbl:
                return lbl
    except Exception:
        pass
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return ''
    return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"


def the_subject(subject):
    """'the X' phrasing that never doubles the article: 'the Nike' stays
    'the Nike', 'the The Office' becomes 'The Office' (2026-10-08)."""
    s = str(subject or '').strip()
    if re.match(r'^(?:the|a|an)\b', s, re.IGNORECASE):
        return s
    return f"the {s}"


def build_profile_required_reply(subject):
    """Steer-to-build reply for an ask about a subject with no base
    profile anywhere (2026-08-27, Jenna): generated reads derive from
    existing bases only, never substitute for a base pull. No numbers,
    no internal vocabulary; the build chip rides as a followup so the
    standard build flow takes over. Returns (reply, followups)."""
    subj = str(subject or '').strip()
    if not subj or subj.lower() in ('that subject', 'this subject',
                                    'the subject', 'none', 'null'):
        # No subject resolved (2026-10-02 replay found "the that
        # subject profile" shipping): ask for the audience instead of
        # printing the placeholder.
        reply = (
            "That read needs a profile built first, and I did not catch "
            "which audience it is about. Name the person, brand, title, "
            "or group and I will build the Total Universe profile, then "
            "read it any way you need: age bands, parent cohorts, buyer "
            "overlaps, category mixes. The build is " + seat_price_label(300) + " and lands in "
            "your Select Profile dropdown when it finishes."
        )
        return scrub_user_text(reply), ["Build a profile for ..."]
    reply = (
        f"That read needs {the_subject(subj)} profile built first. Once "
        f"{the_subject(subj)} Total Universe profile is in your library, I can read "
        f"it any way you need: age bands, parent cohorts, buyer "
        f"overlaps, category mixes. The build is " + seat_price_label(300) + " and lands in "
        f"your Select Profile dropdown when it finishes."
    )
    followups = [f"Build {the_subject(subj)} profile"[:160]]
    return scrub_user_text(reply), followups


# ---------------------------------------------------------------------------
# Panel research report (2026-09-14, Jenna: "before it puts together
# any report outside of a simple analysis of what already exists it
# should charge them. if they request something that doesnt have a set
# price it should charge $550."). A question about a subject with no
# base anywhere no longer dead-ends at the steer-to-build reply: the
# chat offers the full put-together read as a priced deliverable. The
# price is quoted BEFORE anything is generated; the charge lands on
# confirm, before generation starts.
# ---------------------------------------------------------------------------

PANEL_RUN_CHIP_PREFIX = 'Run the full read'


def panel_report_eligible(text, subject):
    """Whether an ask about a no-base subject qualifies for the priced
    research-report offer: a named subject plus a question-shaped or
    behavior-shaped ask that is not an explicit build, cut, deck, or
    export request. Anything that fails this keeps the steer-to-build
    reply."""
    subj = str(subject or '').strip()
    t = str(text or '').strip()
    if not subj or len(subj) < 2:
        return False
    if not t or len(t) > 600:
        return False
    if _is_build_request(t) or _CUT_REQUEST_RX.search(t):
        return False
    if detect_deck_intent(t) or detect_csv_download_intent(t):
        return False
    if _ANALYSIS_QUESTION_RX.search(t) or _ANALYSIS_BEHAVIOR_RX.search(t) \
            or _STRATEGY_RX.search(t) or t.rstrip().endswith('?') \
            or re.match(r'\s*(?:do|does|are|is|top|how|what|which|'
                        r'who|where|when|why|compare|show me|tell me|'
                        r'give me)\b', t, re.IGNORECASE):
        return True
    # A metric over time or a question stated as a wish is a read too
    # (2026-10-08, East Tree Media: "Show me monthly consumption for
    # The Office US" fell through to the plain steer-to-build copy).
    try:
        from prometheus.understand import _STATEMENT_ASK_RX, _TIME_SERIES_RX
        return bool(_TIME_SERIES_RX.search(t) or _STATEMENT_ASK_RX.search(t))
    except Exception:
        return False


def build_panel_report_offer(subject, price_label, question='',
                             kind='report', years=1):
    """The priced offer for a full read on a no-base subject. Returns
    (reply, followups, offer) where `offer` is the payload the widget
    arms so the confirm chip re-sends the ask with panel_confirm.
    Price is stated up front (2026-08-18 house rule: what you see is
    what you pay); the charge itself only lands on confirm, server
    side, at the server's price. kind='viewership' is the per-year
    viewership-over-time read (Jenna 2026-10-08)."""
    subj = str(subject or '').strip() or 'that subject'
    price = str(price_label or '').strip()
    run_chip = (f"{PANEL_RUN_CHIP_PREFIX} - {price}"
                if price else PANEL_RUN_CHIP_PREFIX)[:160]
    if kind == 'viewership':
        try:
            yrs = max(int(years or 1), 1)
        except (TypeError, ValueError):
            yrs = 1
        span = 'one year' if yrs == 1 else f'{yrs} years'
        reply = (
            f"That is a viewership read on {subj}: month by month viewers "
            f"and hours watched across {span}, delivered right here in the "
            f"chat." + (f" It runs {price}." if price else ""))
        followups = [run_chip, 'Never mind']
        offer = {'question': str(question or '')[:600],
                 'subject': subj[:120], 'kind': 'viewership', 'years': yrs}
        return scrub_user_text(reply), followups, offer
    reply = (
        f"{subj} is not in your library yet, so this one is a full "
        f"put-together read, not a lookup. I research {the_subject(subj)} "
        f"audience end to end and deliver the numbers right here in "
        f"the chat. It runs {price}." if price else
        f"{subj} is not in your library yet, so this one is a full "
        f"put-together read, not a lookup. I research {the_subject(subj)} "
        f"audience end to end and deliver the numbers right here in "
        f"the chat."
    )
    reply += (
        f"\n\nIf you want {subj}'s complete profile in your Select "
        f"Profile dropdown instead (every category, every cut on "
        f"tap), the {seat_price_label(300)} build is the better buy."
    )
    followups = [run_chip,
                 f"Build {the_subject(subj)} profile instead"[:160],
                 'Never mind']
    offer = {'question': str(question or '')[:600],
             'subject': subj[:120]}
    return scrub_user_text(reply), followups, offer


PANEL_REPORT_GUIDANCE = (
    'FULL RESEARCH REPORT (no first-party rows exist for this subject '
    'yet - this read is the deliverable the user just paid for):\n'
    '1. This subject has no profile rows on file. Do NOT invent a '
    'profile row block or cite one. The grounding is: published '
    'measurements for this subject (binding), neighbor evidence from '
    'comparable audiences (calibration), and gap research.\n'
    '2. Research the subject thoroughly with the web_search tool '
    'before any number: real-world scale (subscriber base, box '
    'office, chart position, follower counts, store footprint, app '
    'rank), audience composition (age, gender, geography, intensity), '
    'and what happened in the window (release, tour, season, viral '
    'moment). Approved ground only: SEC filings and earnings, Pew, '
    'Statista, eMarketer, YouGov, app analytics. The reply NEVER '
    'names a source or a research step.\n'
    '3. A title or IP with a parent (a creator, an author, a '
    'franchise) has two universes: the title audience and the parent '
    'halo. Size them separately; never hand the whole halo to the '
    'title.\n'
    '4. Every number must sit at a realistic magnitude for the '
    'subject and stay inside its real-world ceiling: a projection '
    'can never exceed the researched US footprint of the thing '
    'counted. Digital behavior only - streaming, search, social, '
    'app, and ecommerce activity. No linear TV, no in-store, no '
    'foot traffic.\n'
    '5. Hard counts speak flat with messy last digits (never a round '
    'number); blended or inferred reads use directional language '
    '(leans, skews, reads as). State the window in the reply.\n')


_REASONED_METRICS_SYSTEM_PROMPT_T = """You are Prometheus, Crosswalk's senior audience strategist. The user asked for a concrete measured number that the data open on screen does not carry. You produce the read from Crosswalk's first-party US measurement of digital behavior: streaming, search, social, app, and ecommerce activity at the individual level.

WHAT TO PRODUCE
- 2 to 6 named metrics that answer the question directly, each with a value, a unit, and a one-line definition of exactly what was counted.
- A headline: one sentence, the sharpest finding with its number.
- 2 to 4 interpretive reads (why the number looks like this, who the audience is). Interpretation uses leans, skews, reads as; hard counts are stated flat.

HOW TO REASON THE NUMBERS
- Research the subject from your knowledge: its real-world scale (subscriber base, chart position, box office, store footprint, app rank). The numbers must sit at realistic magnitudes for that subject and window. A flagship-platform hit reads in the millions of US viewers; a niche podcast reads in the tens of thousands.
- ANCHORS in the user prompt are Crosswalk's own prior measurements and on-file reads for this subject. Calibrate to them; never contradict them.
- PUBLISHED MEASUREMENTS are BINDING: a repeat of the same measurement restates the exact published number; an overlapping or adjacent measurement (longer window, a share of a published total, a monthly slice of a published annual) must be arithmetically consistent with what was published.
- Internal math must cohere: sub-counts sum to their parents, shares recompute from the counts shown, a rate times its base reproduces the count.
- MIRRORED ROWS ARE ONE MEASUREMENT. A brand that appears under more than one heading at the same value (MOST PURCHASED BRANDS with APPAREL/FOOTWEAR, CPG, BEAUTY/WELLNESS, HOME/OUTDOOR, ACCESSORIES, PETS, TOYS, TECHNOLOGY BRAND, WHERE THEY SHOP; AUTOMOBILE with AUTOMOTIVE PARTS; TALENT with its role categories; SPORTS TEAM with its league) is the same people counted once and listed twice. Never read two mirrored rows as two behaviors: not shopping versus buying, browsing versus purchasing, looking versus converting, exposure versus action. A "shop at or buy" question has ONE answer, the brand's row, and the reply says so in one sentence.
- VERBATIM COUNTS. When a prompt block carries a projected US people count for a row, quote it exactly as given. Never recompute a count from a percentage when the file's own count is in front of you.
- BRAND PURCHASE QUESTIONS. When the prompt carries a PURCHASE CONTEXT block, the answer itself carries, in this order: the brand's audience-wide row (penetration, projected US people, index), the Avid tier row for the same brand, where this audience buys it (the retail rows given, by index), and the brand's peers in its own sub-category. These are paragraphs of the answer, never follow-up offers or chips.
- Every count is a messy integer whose last digit is 1-9. Never a round number, never a count ending in 0. Percentages carry one decimal.
- MULTIPLE QUESTIONS IN ONE ASK: answer every one, each under its own short plain heading, in the order asked. Never answer only the first and stop.
- NEVER DECLINE: you never ask the reader to rephrase, narrow, re-aim, or pick a different question, and you never say a number cannot be locked down. When the screen tables do not carry the exact split asked for, derive it from the audience measures you do have and state the read plainly.
- TREND ASKS ("since January", "month by month", "how has it moved"): deliver the series, not one point - monthly or weekly figures across the window. Endpoints and published measurements bind; movement between points stays plausible and organic.
- Today is __TODAY__. Resolve relative windows (last 12 months, past 90 days, since January) against today.
- The window: use the user's window if named; else the subject's real release or campaign window if you know it; else the trailing 12 months, __T12_START__ to __T12_END__.

SUB-COHORT READS (a slice or cut of an open profile)
- When the library already holds a DERIVED CUT FILE for a cohort (the anchors or catalog name '<Subject> - Millennials', '<Subject> - Gen Z', and so on), that file's measured values ARE the cohort's numbers: cite its projected people and sample exactly. NEVER derive a cohort as a residual (the parent total minus the other cohorts) - the cuts of a file do not partition it exactly, and a residual invents a number no file measures (2026-09-23: a Millennials residual shipped 687,413 against the cut file's measured 662,142).
- Generation bands follow the library's cut convention: Gen Z 18-24, Millennials 25-44, Gen X 45-64, Boomers 65 or older. An index against the general population uses the SAME band on both sides, and the general-population side comes from the US baseline file's AGE rows as provided in the prompt anchors - never from memory (2026-09-23: an invented 20.1% Gen Z denominator turned a population-rate band into a fake underindex).
- When MEASUREMENT REQUESTED carries a cohort line and the prompt carries FIRST-PARTY PROFILE ROWS, you are producing the read for that sub-cohort of the profile's audience. The profile rows are binding bounds. A cohort window that sits across two bands reads between them at the weight of the years or share each band contributes. A brand read for the cohort must stay coherent with the audience-wide row for the same brand: a preschool brand reads higher among parents of younger kids and a collector brand lower, but neither strays implausibly from its audience-wide level.
- Deliver the cohort the way any measured read ships: the cohort's share of the audience, its projected US people, and the brand or category mix for that cohort (per-brand penetration rows in the metrics list where useful, with the leaders that step up and the ones that fade vs the audience-wide read in the reads).
- Enrich from what you know about the subject and its category (which brands actually sell at which ages, category norms, seasonality) so the cohort mix is sharp, never a copy of the audience-wide rows.
- State the cohort read flat, as measurement. NEVER describe the mechanics: no "derived", "estimated", "weighted", "interpolated", "straddles", "combined bands", no sentence about what the profile does or does not carry. The reader gets the read for the cohort they asked for, nothing about how the data is organized.

BREAKDOWN ASKS (the asked dimension IS the answer)
- When MEASUREMENT REQUESTED carries a breakdown line ("in terms of toy categories", "by category", "which categories"), the PRIMARY deliverable is the ranked breakdown along that dimension: fill the "breakdown" object with one row per category, shares of the cohort's purchase (or activity) signals that sum to 100, ranked largest first. Cohort context (cohort share, projected people) shrinks to at most 2 metrics; never lead with headline stats when a breakdown was asked for.
- Cover the WHOLE dimension with the established taxonomy for the domain. Toy categories: Action Figures & Playsets, Preschool Toys, Arts & Crafts, Dolls & Dollhouses, Stuffed Animals & Plush Toys, Outdoor Toys, Games & Puzzles, Minis & Surprise Toys, Learning & STEAM Toys, Kids Electronics, Cars Drones & RC Vehicles, Kids Bikes & Ride Ons, Pretend Play. Other domains use their equivalent standard category sets.
- Each row may carry a penetration_pct (share of the cohort with a purchase signal in that category) and a short note naming the leading brands inside it. Shares are messy (never land on a clean .0 or .5); the mix must fit the cohort's age and the subject's franchise reality.
- A SPLIT ACROSS A PROFILE SECTION ("% of total time spent on each streaming platform", "share of streaming time by service", "how their social time splits", "search engine share") is answered FROM THAT SECTION of the FIRST-PARTY PROFILE ROWS: one breakdown row per platform in the section, share_pct = the row's 'share' figure (the section's shares already sum to 100), penetration_pct = the row's penetration, dimension named for the section ("Streaming platform"). The subject's own service row carries its share too. Never answer a platform split with a list of shows or titles, and never answer a top-titles ask with a platform split: the dimension the user named is the dimension you rank.
- LEADERS INSIDE A COHORT ROW (the "note" naming which services, brands, or titles lead a genre, category, or sub-cohort) are reasoned for THAT cohort from category reality, never copied from the audience-wide ranking. Disney+ leads a Kids & Family or Animation cohort even when Netflix outranks it audience-wide; ESPN and Peacock lead Sports; Hulu and Bravo-on-Peacock lead Reality; HBO Max leads prestige Drama; Crunchyroll leads Anime. The audience-wide row is the BASE a cohort moves off, not the ranking: a service at 43% of the whole audience reads well above that inside a genre cohort where it is the category home and can be first there, while a service at 75% audience-wide can sit second or third inside a cohort it does not program for. Name services exactly as the profile's own rows label them (Disney+/Hulu when that is the row) so the reader can tie the note back to the section. Apply one consistent logic across every row of the table; if Disney+ leads Animation it also leads Kids & Family.
- A "how penetrated is each service inside each cohort" ask wants a GRID: for every cohort row, per-service penetration for the major services in the profile's section (at least the top 6 by audience-wide penetration plus the subject's own service), carried in the note as "Netflix 81.2, Disney+/Hulu 78.4, Prime 61.7, ..." so the reader gets the full cross-tab, not one audience-wide number and a pair of leaders. Each cohort's per-service figures move off that service's audience-wide row: above it where the cohort is the service's category home, below it where the service does not program for that genre, and the weighted average across cohorts stays coherent with the audience-wide row.

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
  "window_label": "__T12_LABEL__",
  "window_start": "__T12_START__",
  "window_end": "__T12_END__",
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
    # Deck-artifact language wins even without a build verb (Jenna
    # 2026-08-31, Unlikely Collaborators): a Venn diagram or a single-
    # slide deliverable is unmistakably a deck ask.
    r'\bvenn diagram\b',
    r'\bon a single slide\b',
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


# ---------------------------------------------------------------------------
# Deck subject resolution: fuzzy family matching + suggestion chips
# ---------------------------------------------------------------------------
# When a typed deck ask names a subject that does not resolve to one
# exact profile, the deck flow offers the closest catalog profiles as
# clickable confirm chips instead of a generic punt (Jenna 2026-08-31:
# fuzzy-match the typed name and suggest the similar-named profiles).
# These helpers are pure (catalog in, chip payloads out) so they can be
# unit-tested offline; app.py layers the catalog source, the interpret
# path's token-overlap shortlister, and per-user access gating on top.

DECK_FUZZY_THRESHOLD = 0.34
DECK_MAX_SUGGESTIONS = 6
_DECK_COMBO_KEYWORDS = ('all social', 'all platforms', 'all three',
                        'combined', 'combo', 'all')


def _deck_norm(s):
    """Lowercase, strip punctuation, collapse whitespace."""
    if not s:
        return ''
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def deck_family_base(display_name):
    """The entity name before the first ' - ' cut / platform suffix.
    'Unlikely Collaborators - TikTok - Avid Fan' -> 'Unlikely
    Collaborators'. A family groups every platform, total, and Avid cut
    that shares this base."""
    return str(display_name or '').split(' - ', 1)[0].strip()


def deck_suffix_is_avid(display_name):
    """True when the display name carries an Avid Fan cut suffix."""
    parts = str(display_name or '').split(' - ', 1)
    return len(parts) > 1 and 'avid' in parts[1].lower()


def _deck_combo_rank(display_name):
    """Rank a total-universe member so a combined / all-platform member
    leads the family (it makes the strongest deck primary)."""
    d = _deck_norm(display_name)
    for i, kw in enumerate(_DECK_COMBO_KEYWORDS):
        if kw in d:
            return (0, i)
    return (1, 0)


def deck_order_members(members):
    """Order a family's members for display: total-universe members
    first (a combined / all-platform member leads), then Avid cuts.
    Stable within each group by normalized display name."""
    def _key(m):
        disp = str(m.get('display_name') or '')
        is_avid = 1 if deck_suffix_is_avid(disp) else 0
        combo = _deck_combo_rank(disp)
        return (is_avid, combo[0], combo[1], _deck_norm(disp))
    return sorted(members, key=_key)


def _deck_ref(entry):
    return {'s3_key': str(entry.get('s3_key') or '').strip(),
            'name': str(entry.get('display_name') or '')[:200]}


def deck_build_family_bind(members):
    """Split a family's members into a deck-ready page context:
    {primary, cuts, extras}. Total-universe members become the primary
    plus comparison profiles (extras); Avid cuts become cuts. Mirrors
    exactly what get_digest_bundle consumes (primary + up to 3 cuts +
    up to 3 extras), so a whole family builds one combined deck."""
    ordered = deck_order_members(members)
    tu = [m for m in ordered
          if not deck_suffix_is_avid(m.get('display_name'))]
    avid = [m for m in ordered
            if deck_suffix_is_avid(m.get('display_name'))]
    if not tu:                       # avid-only family
        tu, avid = avid[:1], avid[1:]
    primary = _deck_ref(tu[0])
    extras = [_deck_ref(m) for m in tu[1:4]]
    cuts = [_deck_ref(m) for m in avid[:3]]
    return {'primary': primary, 'cuts': cuts, 'extras': extras}


def deck_single_bind(member, members):
    """Bind one member as the deck primary, attaching that member's own
    Avid cut when the family carries it (mirrors the total + Avid pair
    the exact-match path already ships)."""
    m_disp = str(member.get('display_name') or '')
    m_norm = _deck_norm(m_disp)
    cuts = []
    for c in members:
        if c.get('s3_key') == member.get('s3_key'):
            continue
        cd = str(c.get('display_name') or '')
        if deck_suffix_is_avid(cd) and _deck_norm(cd).startswith(m_norm + ' '):
            cuts.append(_deck_ref(c))
    return {'primary': _deck_ref(member), 'cuts': cuts[:3], 'extras': []}


def deck_family_subtitle(members):
    """A short factual descriptor for a family set chip, e.g.
    '3 total + 3 Avid'. No em dashes, no internal terms."""
    tu = sum(1 for m in members
             if not deck_suffix_is_avid(m.get('display_name')))
    avid = sum(1 for m in members
               if deck_suffix_is_avid(m.get('display_name')))
    parts = []
    if tu:
        parts.append(f"{tu} total")
    if avid:
        parts.append(f"{avid} Avid")
    return ' + '.join(parts)


def deck_match_families(query, catalog, ranked=None,
                        threshold=DECK_FUZZY_THRESHOLD):
    """Score catalog families against a typed subject / ask and return
    matched families best-first:
    [(base_norm, base_disp, ordered_members, score)].

    Three complementary signals, per family base:
      1. ranked scores from app.py's _shortlist_profile_matches (the
         interpret path's own token-overlap matcher) when provided.
      2. base-token containment in the query - catches a family whose
         base sits inside a longer ask ('New Project: Unlikely
         Collaborators ...'), which token-COVERAGE scoring dilutes to
         near zero on long prompts.
      3. difflib ratio - catches typos and short near-miss queries, and
         is the ONLY signal for single-token bases so a common word
         inside a long ask never false-fires.
    """
    q = str(query or '').strip()
    if not q or not catalog:
        return []
    q_norm = _deck_norm(q)
    q_tokens = set(t for t in q_norm.split() if len(t) >= 3)

    fam_disp, fam_members = {}, {}
    for c in catalog:
        base = deck_family_base(c.get('display_name'))
        bnorm = _deck_norm(base)
        if not bnorm:
            continue
        fam_disp.setdefault(bnorm, base)
        fam_members.setdefault(bnorm, [])
        k = str(c.get('s3_key') or '').strip()
        if k and all(m.get('s3_key') != k for m in fam_members[bnorm]):
            fam_members[bnorm].append(c)

    fam_score = {}
    # Signal 1: folded-in shortlister scores.
    for c in (ranked or []):
        s = float(c.get('_score') or 0)
        if s < threshold:
            continue
        bnorm = _deck_norm(deck_family_base(c.get('display_name')))
        if bnorm in fam_members:
            fam_score[bnorm] = max(fam_score.get(bnorm, 0.0), s)

    # Signals 2 + 3: containment and difflib over the query.
    for bnorm in fam_members:
        b_tokens = set(t for t in bnorm.split() if len(t) >= 3)
        best = fam_score.get(bnorm, 0.0)
        if len(b_tokens) >= 2:
            contain = len(b_tokens & q_tokens) / len(b_tokens)
            if contain >= 0.8:
                best = max(best, 0.6 + 0.4 * contain)
            elif contain >= 0.5:
                best = max(best, 0.34 + 0.3 * contain)
        ratio = difflib.SequenceMatcher(None, q_norm, bnorm).ratio()
        if ratio >= 0.72:
            best = max(best, ratio)
        if best >= threshold:
            fam_score[bnorm] = best

    matched = [(b, fam_disp[b], deck_order_members(fam_members[b]),
                fam_score[b])
               for b in fam_score if fam_members.get(b)]
    matched.sort(key=lambda x: (-x[3], x[0]))
    return matched


def build_deck_suggestions(query, catalog, ranked=None,
                           max_suggestions=DECK_MAX_SUGGESTIONS,
                           threshold=DECK_FUZZY_THRESHOLD):
    """Return clickable confirm-chip payloads for a fuzzy deck subject.
    Each item: {kind: 'set'|'profile', label, subtitle, bind}. bind is a
    page-context shape {primary, cuts, extras} the frontend sends back to
    the deck route to bind that profile (or the whole family set) and
    continue the deck flow. [] when nothing clears the threshold."""
    matched = deck_match_families(query, catalog, ranked=ranked,
                                  threshold=threshold)
    if not matched:
        return []
    suggestions = []
    # Pass 1: one lead chip per matched family - the whole set when the
    # family has more than one profile, else the single profile.
    for _bnorm, base_disp, members, _score in matched:
        if not members:
            continue
        if len(members) > 1:
            bind = deck_build_family_bind(members)
            if bind['primary'].get('s3_key'):
                suggestions.append({
                    'kind': 'set',
                    'label': f"{base_disp} (all {len(members)} profiles)",
                    'subtitle': deck_family_subtitle(members),
                    'bind': bind})
        else:
            m = members[0]
            if str(m.get('s3_key') or '').strip():
                suggestions.append({
                    'kind': 'profile',
                    'label': str(m.get('display_name') or '')[:200],
                    'subtitle': '',
                    'bind': deck_single_bind(m, members)})
        if len(suggestions) >= max_suggestions:
            return suggestions[:max_suggestions]
    # Pass 2: when a single family matched, expand its members so the
    # user can pick one platform instead of the whole set.
    if len(matched) == 1 and len(matched[0][2]) > 1:
        members = matched[0][2]
        for m in members:
            if len(suggestions) >= max_suggestions:
                break
            if not str(m.get('s3_key') or '').strip():
                continue
            suggestions.append({
                'kind': 'profile',
                'label': str(m.get('display_name') or '')[:200],
                'subtitle': '',
                'bind': deck_single_bind(m, members)})
    return suggestions[:max_suggestions]


INSIGHTS_DECK_SYSTEM_PROMPT = """You are Prometheus, Crosswalk's senior audience strategist, producing the slide plan for a FINISHED client-ready insights deck. This is a final deliverable a seller walks into a pitch with, not an outline. You get the subject's Profile IQ digest (first-party audience data), the recent conversation, and the ask. Return a strict JSON slide plan; a renderer lays it out in the Crosswalk deck system.

THE ARC (14 to 20 slides, in this shape)
1. cover: the single sharpest commercial sentence as the headline, one intro line naming what the deck contains and the window, three proof stats (audience scale, the best conversion or behavior number, the best unit-performance number).
2. argument: the TLDR. Four numbered cards a reader could stop at: what we found, why it matters for the partner, what to do, each card ending on its number. A reader who sees only this slide leaves with the answer.
3. tiles_facts: the universe. Projected US audience, audience in file, avid tier share when the digest carries an avid cut, the defining demo. Fact rows: age, ethnicity or household shape, DMA concentration, the subject's own anchor properties with penetration and index.
4-8. the audience case from the digest, one read per slide: interests (bars), the category retail or channel read (bars with show_index), the wallet or premium read (split_stats_bars or tiles_facts), adjacency or talent graph (bars), distribution and social (bars with show_index or tiles_facts). Pick the categories where the digest is strongest; every number on these slides comes from the digest.
9-17. the behavior proof, one read per slide, reasoned from the subject's real-world scale: a hero slide (ground=accent) with the single sharpest behavioral stat; CTR or engagement vs the peer set (bars); search demand (split_stats_bars: unique searchers + query mix); second-screen or live-moment behavior (split_stats_bars) when the subject has live events; ad response (tiles_row: first-impression clicks, cart timing, repeat rate); same-session cross-shop (split_stats_bars); journeys (table: conversion with the subject on the path vs peers vs no talent); cart (table: start, complete, abandon, recover, AOV); spend per engager (hero_proof).
18. paths: 9 to 12 example clickstream rows (kind: search/click/cart/play, url: realistic lowercase urls involving the subject and the relevant retail or platform domains, lit: true on cart and play rows).
19. argument (ground=light): the buy. Four categories where the file and the journeys agree, each with its numbers.
20. close: four numbered cards restating the case, each ending on a number.

Omit slides the data cannot carry (no live events means no second-screen slide; no avid cut means no avid tier tile). Never pad: a 14-slide deck that is all signal beats a 20-slide deck with filler.

ART DIRECTION. Set a top-level "image_subject": the one person, brand, or title the deck is about, spelled exactly as publicly known, and "image_kind": one of "person", "title", "brand". The renderer places real photography of that subject under a dark scrim on the cover, the first big statement page, and the close. Set "photo": false on any of those slides that must stay type-only.

HEADLINES FIT THE PAGE
- Every "title" is ONE sentence of 12 words or fewer, plain words, full stop. The cover title included: one clause, one idea. Two figures in one title is one too many; move the second figure to a stat or the intro.
- "sub" and "intro" stay under 30 words. Card "head" under 8 words; card "body" under 28 words; tile "label" under 10 words; bar row "label" under 4 words; "big" is a figure, never a sentence.

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
- Titles are sentences in sentence case and end with a full stop. They state the finding: "They over-shop the sneaker channel at 3x." not "Channel Overview". Aim for 6 to 10 words; never more than 12. No puns, rhetorical questions, teasers, colon reveals, or metaphor headers.
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


def enforce_metrics_coherence(data, so_what=False, multi=None):
    """Exactify a reasoned measurement read: counts messy (last digit
    1-9), percentages one decimal and bounded, labels and definitions
    capped. Returns the cleaned dict. `so_what` (2026-10-06) marks the
    result so the formatter renders a plan instead of a read."""
    if not isinstance(data, dict):
        raise ValueError('measurement payload is not a dict')
    subj = str(data.get('subject') or 'subject').strip() or 'subject'
    out = {
        'subject': _clip_label(subj),
        'metric_family': str(data.get('metric_family')
                             or 'audience').strip().lower()[:32],
        'window_label': str(data.get('window_label') or '').strip()[:80],
        'window_start': str(data.get('window_start') or '').strip()[:12],
        'window_end': str(data.get('window_end') or '').strip()[:12],
        'headline': _clip_text(data.get('headline'), 300),
        'reads': [_clip_text(r, 480)
                  for r in (data.get('reads') or []) if str(r).strip()][:7 if (so_what or multi) else 5],
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
    out['cohort'] = _clip_label(data.get('cohort'))
    out['breakdown'] = _coherent_breakdown(data.get('breakdown'))
    if out['breakdown']:
        # Breakdown-primary reads keep the context stats to a preamble.
        metrics = metrics[:3]
    if not metrics and not out['breakdown']:
        raise ValueError('measurement read carried no usable metrics')
    out['metrics'] = metrics
    if so_what:
        out['_so_what'] = True
    if multi:
        out['_multi'] = list(multi)
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
    cohort context shrinks to a one-line preamble. A so-what ask
    (res['_so_what'], 2026-10-06) renders as a plan instead of a read."""
    if res.get('_multi') and not res.get('breakdown'):
        return format_multi_question_reply(res)
    if res.get('_so_what') and not res.get('breakdown'):
        return format_so_what_reply(res)
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


_IMG_SUBJECT_STOP = {'the', 'a', 'an', 'of', 'and', 'audience', 'fans', 'viewers',
                     'profile', 'insights', 'deck', 'tu', 'universe', 'total', 'avid', 'fan',
                     'cut', 'q1', 'q2', 'q3', 'q4', 'cy2025', 'cy2026', '2025', '2026'}


def _img_tokens(s):
    return {w for w in re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).split()
            if len(w) >= 3 and w not in _IMG_SUBJECT_STOP}


def deck_image_subject(proposed, subject, plan=None):
    """The subject whose photography opens the deck. The model's pick
    stands when it shares a distinctive token with the deck subject or
    appears in a slide title (a talent the deck is about); anything
    else falls back to the subject itself. Never empty when a subject
    exists."""
    subj = str(subject or '').strip()
    prop = str(proposed or '').strip()
    if not prop:
        return subj
    if not subj:
        return prop
    st, pt = _img_tokens(subj), _img_tokens(prop)
    if pt and (pt & st):
        return prop
    titles = ' '.join(str((sl or {}).get('title') or '') for sl in ((plan or {}).get('slides') or []) if isinstance(sl, dict)).lower()
    if pt and prop.lower() in titles:
        return prop
    print(f"[deck] image_subject {prop!r} is off the deck subject {subj!r}; using the subject")
    return subj


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
    # Art direction rides through (2026-09-30 deck photography), held
    # to the deck's own subject (2026-10-07, Bria: a Starz insights
    # deck opened on a Microsoft Teams marketing image because the
    # plan's image_subject drifted off the subject). The model's pick
    # survives only when it shares a distinctive token with the
    # subject or is named in a slide title; otherwise the subject is
    # the photo subject.
    _img = scrub_user_text(str(plan.get('image_subject') or '')).strip()[:80]
    out['image_subject'] = deck_image_subject(_img, subject, plan)
    _ik = str(plan.get('image_kind') or '').strip().lower()
    if _ik not in ('person', 'title', 'brand'):
        _ik = ''
    if out['image_subject'] != _img:
        _ik = ''   # the kind is re-read from the subject's category downstream
    out['image_kind'] = _ik
    slides = []
    for sl in (plan.get('slides') or [])[:22]:
        if not isinstance(sl, dict):
            continue
        stype = str(sl.get('type') or '').strip().lower()
        if stype not in _INSIGHTS_SLIDE_TYPES:
            continue
        cleaned = _clean_deck_value(subject, sl)
        cleaned['type'] = stype
        if sl.get('photo') is False:
            cleaned['photo'] = False
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


# ---------------------------------------------------------------------------
# Standing-default window stamping (2026-10-01). The read-engine prompts
# carried the retired Jul 2025 - Jun 2026 fiscal pair as the default
# window and no TODAY anchor, so relative asks ("last 12 months") fell
# back to the stale pair (surfaced on Carolyn Bisson's 14-title
# screener). The prompt bodies now carry __TODAY__ / __T12_START__ /
# __T12_END__ / __T12_LABEL__ tokens; this module __getattr__ stamps
# them with the computed trailing-12 window on EVERY attribute access,
# so long-lived processes never drift a day.
# ---------------------------------------------------------------------------

def _stamp_window_tokens(text):
    """Fill the window tokens with today's trailing-12 pair."""
    import datetime as _dt
    start = end = None
    try:
        from migration.event_window import default_window as _dw
        start, end = _dw()
        start, end = str(start)[:10], str(end)[:10]
    except Exception:
        pass
    if not (start and end):
        t = _dt.date.today()
        try:
            s_dt = t.replace(year=t.year - 1)
        except ValueError:                      # Feb 29
            s_dt = t.replace(year=t.year - 1, day=28)
        start, end = s_dt.isoformat(), t.isoformat()

    def _lbl(iso):
        d = _dt.date.fromisoformat(iso)
        return f"{d.strftime('%b')} {d.day} {d.year}"

    # TODAY and the trailing-12 end share one clock (UTC, the one
    # default_window uses); the host's local date drifts past it for
    # two hours each night and the two anchors disagreed.
    today_iso = end or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    return (text
            .replace('__T12_START__', start)
            .replace('__T12_END__', end)
            .replace('__T12_LABEL__', f"{_lbl(start)} to {_lbl(end)}")
            .replace('__TODAY__', today_iso))


def __getattr__(name):
    if name == 'SEARCH_DEMAND_SYSTEM_PROMPT':
        return _stamp_window_tokens(_SEARCH_DEMAND_SYSTEM_PROMPT_T)
    if name == 'REASONED_METRICS_SYSTEM_PROMPT':
        return _stamp_window_tokens(_REASONED_METRICS_SYSTEM_PROMPT_T)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}")
