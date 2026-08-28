"""Corpus-wide retrieval for Prometheus generated reads.

2026-08-27, Jenna: "it doesn't just have to look at the data on the
profile that's open. It can go through all the profiles or subscriber,
IQs, etc., that we have and do external research."

The library holds ~4,200 profiles; a fresh generated read cannot load
them all. This module is the lightweight retrieval layer:

1. SELECT: one small, fast model call scans the catalog metadata
   (display name + category, prefiltered) and picks the 3-8 profiles
   that are useful comparables for THIS ask (competitive/adjacent
   audiences, same genre or space). The asked subject and its own
   cuts are excluded - they are already the grounding.
2. DIGEST: each chosen profile gets a compact digest (headline size,
   top rows of its most relevant category grids, demo shape) built
   lazily from the CSV and cached in S3 keyed by the profile object's
   ETag - an in-place correction changes the ETag, so the digest
   rebuilds itself on next use.
3. RENDER: the digests become one NEIGHBOR EVIDENCE prompt block with
   guardrail language: neighbors inform the reasoning, the bound
   subject's own numbers always win.

Everything here is fail-safe: any trouble returns '' and the read
proceeds on the bound subject alone. The trail of which neighbors a
read drew on goes to logs only.
"""

import hashlib
import json
import re
import time

DIGEST_PREFIX = 'system/prometheus_corpus_digests/'
DIGEST_MAX_CHARS = 2600
MAX_NEIGHBORS = 8

# Categories that identify cuts / derived files we never pick as
# neighbors (the parent carries the evidence).
_CUT_SUFFIX_RX = re.compile(
    r' - (?:avid|casual|female|male|f$|m$|1[0-9]-[0-9]{2}|'
    r'[0-9]{2}-[0-9]{2}|gen z|millennial|boomer)', re.IGNORECASE)

_SELECT_SYSTEM = (
    'You pick comparable audience profiles from a library catalog to '
    'ground an analysis. Given the ask, the subject it is about, and '
    'the catalog list, choose up to {k} OTHER profiles that are '
    'genuinely useful evidence: direct competitors, adjacent titles '
    'or brands in the same space, and audiences serving the same '
    'life stage or genre. Match on what the subject IS, not on '
    'shared words: for a preschool kids franchise, other kids '
    'franchises and kids brands (shows, toy brands, kids creators) '
    'are the right picks even when their names share nothing with '
    'the ask; an adult title that happens to contain a matching word '
    'is a wrong pick. Never pick the asked subject itself, its cuts, '
    'or profiles with no bearing on the ask. Fewer good picks beat '
    'more weak ones. Answer with JSON only: '
    '{{"picks": ["<display name>", ...]}}.')


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def catalog_candidates(catalog, subject, limit=4000):
    """Prefilter the raw catalog for the selection call: drop the
    subject itself and its cuts, drop obvious derived files. The whole
    library stays scannable (the selection call is one small pass per
    fresh read and rides the async job), so a kids-franchise ask can
    surface neighbors that share no token with the ask."""
    subj_norm = _norm(subject)
    out, seen = [], set()
    for e in catalog or []:
        name = str(e.get('display_name') or e.get('subject') or '')
        key = e.get('s3_key') or ''
        if not name or not key:
            continue
        n = _norm(name)
        if not n or n in seen:
            continue
        if subj_norm and (subj_norm in n or n in subj_norm):
            continue
        if ' - ' in name and _CUT_SUFFIX_RX.search(name):
            continue
        seen.add(n)
        out.append({'display_name': name, 's3_key': key,
                    'category': str(e.get('category') or '')})
        if len(out) >= limit:
            break
    return out


def _render_listing(cands):
    """Category-grouped catalog listing so the selection call scans
    the library the way an operator would."""
    groups = {}
    for c in cands:
        groups.setdefault(c['category'] or 'OTHER', []).append(
            c['display_name'])
    lines = []
    for cat in sorted(groups):
        lines.append(f"[{cat}]")
        lines.append('; '.join(groups[cat]))
    return '\n'.join(lines)


def select_neighbors(text, subject, catalog, claude_json_fn, k=6):
    """Model-picked neighbor profiles for this ask. Returns a list of
    catalog entries (possibly empty). Never raises."""
    k = max(1, min(int(k or 6), MAX_NEIGHBORS))
    try:
        cands = catalog_candidates(catalog, subject)
        if not cands:
            return []
        by_norm = {_norm(c['display_name']): c for c in cands}
        listing = _render_listing(cands)
        user = (f"Ask: {str(text or '')[:400]}\n"
                f"Subject of the ask: {subject or 'unknown'}\n\n"
                f"Catalog (grouped by category):\n{listing}")
        result = claude_json_fn(_SELECT_SYSTEM.format(k=k), user)
        data = (result or {}).get('data')
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict)), {})
        picks = (data or {}).get('picks') or []
        chosen = []
        for p in picks:
            c = by_norm.get(_norm(p))
            if c and c not in chosen:
                chosen.append(c)
            if len(chosen) >= k:
                break
        return chosen
    except Exception as e:
        print(f"[pm-corpus] neighbor selection failed: {e}")
        return []


def _digest_cache_key(s3_key):
    h = hashlib.sha1(str(s3_key).encode('utf-8')).hexdigest()[:24]
    return f"{DIGEST_PREFIX}{h}.json"


def _build_compact_digest(df, name, meta, focus_tokens=()):
    """Small, prompt-ready digest of one profile: headline size + the
    top rows of its most relevant category grids + demo shape."""
    import prometheus_analysis as pma
    bp_col = meta.get('bp_col')
    lines = [f"NEIGHBOR: {name}"]
    if meta.get('proj'):
        lines.append(f"  projected US audience: {meta['proj']:,}")
    cats = {}
    for _, row in df.iterrows():
        cat = pma._norm_cat(row.get('Column'))
        if not cat or cat in pma.METADATA_COLS:
            continue
        cats.setdefault(cat, []).append(row)
    demo = [c for c in ('AGE', 'GENDER') if c in cats]
    focus = [t.upper() for t in focus_tokens if t]
    scored = []
    for cat, rows in cats.items():
        if cat in ('AGE', 'GENDER'):
            continue
        score = len(rows)
        if any(f in cat for f in focus):
            score += 10000
        scored.append((score, cat))
    picked = [c for _, c in sorted(scored, reverse=True)[:6]]
    for cat in demo + picked:
        rows = cats.get(cat) or []
        vals = []
        for r in rows:
            try:
                bp = float(str(r.get(bp_col)).replace('%', ''))
            except (TypeError, ValueError):
                continue
            label = str(r.get('Value') or '').strip()
            if label and bp < 100.0:
                vals.append((bp, label))
        vals.sort(reverse=True)
        top = vals[:4] if cat in ('AGE', 'GENDER') else vals[:6]
        if not top:
            continue
        body = ', '.join(f"{lbl} {bp:.1f}%" for bp, lbl in top)
        lines.append(f"  {cat}: {body}")
    return '\n'.join(lines)[:DIGEST_MAX_CHARS]


def _build_and_store_digest(s3_client, bucket, s3_key, name, etag,
                            focus_tokens=()):
    """Build the digest text for one profile and store it in the S3
    digest cache under the profile's current ETag. Returns the text
    ('' on trouble). Never raises."""
    try:
        import prometheus_analysis as pma
        df, _ = pma.load_profile_df(s3_client, bucket, s3_key)
        meta = pma._profile_meta(df, name)
        text = _build_compact_digest(df, name, meta,
                                     focus_tokens=focus_tokens)
    except Exception as e:
        print(f"[pm-corpus] digest build failed for {s3_key}: {e}")
        return ''
    try:
        s3_client.put_object(
            Bucket=bucket, Key=_digest_cache_key(s3_key),
            Body=json.dumps({'etag': etag, 's3_key': s3_key,
                             'name': name, 'built_at': time.time(),
                             'text': text}).encode('utf-8'),
            ContentType='application/json')
    except Exception:
        pass
    return text


def _cached_digest_text(s3_client, bucket, s3_key, etag):
    """Warm digest text from the S3 cache when it matches the
    profile's current ETag, else None."""
    try:
        resp = s3_client.get_object(Bucket=bucket,
                                    Key=_digest_cache_key(s3_key))
        doc = json.loads(resp['Body'].read().decode('utf-8'))
        if doc.get('etag') == etag and doc.get('text'):
            return doc['text']
    except Exception:
        pass
    return None


def neighbor_digest(s3_client, bucket, entry, focus_tokens=()):
    """Digest text for one catalog entry. S3-cached keyed by the
    profile object's ETag; a corrected-in-place profile (new ETag)
    rebuilds lazily on next use. The nightly warm pass
    (bg-webapp/scripts/build_prometheus_profile_indexes.py calling
    warm_neighbor_digest) keeps the cache current for the whole
    catalog so this almost never builds cold at ask time. Never
    raises; '' on trouble."""
    s3_key = entry.get('s3_key') or ''
    name = entry.get('display_name') or s3_key
    try:
        head = s3_client.head_object(Bucket=bucket, Key=s3_key)
        etag = (head.get('ETag') or '').strip('"')
    except Exception:
        return ''
    text = _cached_digest_text(s3_client, bucket, s3_key, etag)
    if text is not None:
        return text
    return _build_and_store_digest(s3_client, bucket, s3_key, name, etag,
                                   focus_tokens=focus_tokens)


def warm_neighbor_digest(s3_client, bucket, entry):
    """Nightly warm for one catalog entry: ensure the S3 digest cache
    holds a current-ETag digest. Returns 'warm' (already current),
    'built', or 'failed'. Never raises."""
    s3_key = entry.get('s3_key') or ''
    name = entry.get('display_name') or s3_key
    try:
        head = s3_client.head_object(Bucket=bucket, Key=s3_key)
        etag = (head.get('ETag') or '').strip('"')
    except Exception:
        return 'failed'
    if _cached_digest_text(s3_client, bucket, s3_key, etag) is not None:
        return 'warm'
    text = _build_and_store_digest(s3_client, bucket, s3_key, name, etag)
    return 'built' if text else 'failed'


_FOCUS_STOP = {'what', 'which', 'the', 'for', 'are', 'this', 'that',
               'buying', 'buy', 'audience', 'their', 'they', 'them',
               'kids', 'parents', 'and', 'with', 'about', 'terms'}


def focus_tokens_from_ask(text):
    """Category-grid hints from the ask ('toys' -> the TOYS grid rises
    in the digest)."""
    # 3-letter words count: 'toy' is exactly the grid hint this exists
    # for (TOYS rises in the digest).
    toks = [w for w in _norm(text).split()
            if len(w) >= 3 and w not in _FOCUS_STOP]
    return toks[:8]


def gather_neighbor_evidence(s3_client, bucket, text, subject, catalog,
                             claude_json_fn, k=6):
    """Full corpus pass: select neighbors, digest each, render the
    NEIGHBOR EVIDENCE block. Returns (block_text, [names]); ('' , [])
    when nothing useful. Never raises."""
    try:
        chosen = select_neighbors(text, subject, catalog,
                                  claude_json_fn, k=k)
        if not chosen:
            return '', []
        focus = focus_tokens_from_ask(text)
        digests, names = [], []
        for c in chosen:
            d = neighbor_digest(s3_client, bucket, c,
                                focus_tokens=focus)
            if d:
                digests.append(d)
                names.append(c['display_name'])
        if not digests:
            return '', []
        block = (
            'NEIGHBOR EVIDENCE - comparable audiences from the same '
            'library, chosen for this ask. Use them as competitive '
            'and coverage context (where demand is already served, '
            'how similar audiences shape up). They inform the '
            'reasoning ONLY: the asked subject\'s own rows and '
            'delivered figures always win, and no neighbor number '
            'may ever be reported as the asked subject\'s.\n\n'
            + '\n\n'.join(digests))
        return block, names
    except Exception as e:
        print(f"[pm-corpus] gather failed: {e}")
        return '', []
