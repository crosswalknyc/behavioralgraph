"""Compiled house-knowledge loader for Prometheus (2026-08-27).

Jenna: "how do we get prometheus to have the knowledge base you have on
our history. background, rules, etc so that it can produce outputs
correctly at your levels?"

The knowledge pack is compiled from the workspace rules by
scripts/build_prometheus_knowledge_pack.py (parent repo). It is a
distillation, not a copy: the DO side of every rule stated positively,
with zero internal vocabulary, zero infrastructure, zero banned-word
lists (those live in the compiler's leak gate and in
prometheus_analysis._SCRUB_RULES, never in prompt text).

Injection is tiered for cost and latency:

- CANON (voice, vocabulary, confidence calibration, number rules,
  boundaries, window default) merges into the cached system prompt via
  with_canon(). The block is byte-stable per pack content_hash, so the
  provider-side prompt cache keeps holding; it only changes when the
  pack itself is republished.
- TOPICAL modules (naming, universe definitions, category taxonomy,
  deck standards, search-demand shape) attach selectively by intent to
  the per-request user prompt via knowledge_block(), riding the
  existing surface/intent split (analysis vs measured read vs deck vs
  search demand).
- DECISIONS OF RECORD (system/prometheus_decisions.json) are standing
  definitional decisions in scrubbed form, retrieved per query by match
  terms and appended alongside the topical modules.

Loading follows the users.json pattern: ETag-conditional GET against
S3 with a short TTL between revalidations, the bundled repo copy as
the fallback so a fresh deploy answers correctly before S3 is ever
reachable. Every failure path returns the last good pack (or the
bundled one); knowledge loading never blocks or breaks an analysis.
"""

import json
import os
import re
import threading
import time

PACK_KEY = 'system/prometheus_knowledge_pack.json'
DECISIONS_KEY = 'system/prometheus_decisions.json'
REVALIDATE_S = 300          # conditional-GET at most every 5 minutes
KNOWLEDGE_BLOCK_MAX_CHARS = 6000

_BUNDLED_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_PACK = os.path.join(_BUNDLED_DIR, 'prometheus_knowledge_pack.json')
_BUNDLED_DECISIONS = os.path.join(_BUNDLED_DIR, 'prometheus_decisions.json')

_lock = threading.Lock()
_pack_cache = {'ts': 0.0, 'etag': None, 'pack': None,
               'canon': '', 'canon_hash': None}
_decisions_cache = {'ts': 0.0, 'etag': None, 'doc': None}


def _load_bundled(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def _conditional_fetch(s3_client, bucket, key, cache):
    """ETag-conditional refresh of one cached S3 JSON doc. Mutates
    `cache` in place under _lock. Any failure keeps the cached copy."""
    now = time.time()
    with _lock:
        fresh_enough = (cache.get('_body') is not None
                        and now - cache['ts'] < REVALIDATE_S)
        etag = cache.get('etag')
    if fresh_enough:
        return
    body = None
    new_etag = None
    try:
        kwargs = {'Bucket': bucket, 'Key': key}
        if etag:
            kwargs['IfNoneMatch'] = etag
        resp = s3_client.get_object(**kwargs)
        body = resp['Body'].read().decode('utf-8')
        new_etag = (resp.get('ETag') or '').strip('"') or None
    except Exception as ce:
        resp_meta = getattr(ce, 'response', None) or {}
        meta = resp_meta.get('ResponseMetadata') or {}
        code = (resp_meta.get('Error') or {}).get('Code', '')
        if meta.get('HTTPStatusCode') == 304 or code in ('304',
                                                         'NotModified'):
            with _lock:
                cache['ts'] = now
            return
        # NoSuchKey, network trouble, missing client: keep what we have.
        with _lock:
            cache['ts'] = now
        return
    try:
        doc = json.loads(body)
    except Exception:
        with _lock:
            cache['ts'] = now
        return
    with _lock:
        cache['ts'] = now
        cache['etag'] = new_etag
        cache['_body'] = body
        cache['doc'] = doc


def get_pack(s3_client=None, bucket=None):
    """The current knowledge pack dict: S3 copy when reachable (ETag
    revalidated), else the bundled repo copy, else None."""
    if s3_client is not None and bucket:
        _conditional_fetch(s3_client, bucket, PACK_KEY, _pack_cache)
        with _lock:
            if _pack_cache.get('doc'):
                return _pack_cache['doc']
    with _lock:
        if _pack_cache.get('doc'):
            return _pack_cache['doc']
    bundled = _load_bundled(_BUNDLED_PACK)
    if bundled:
        with _lock:
            if not _pack_cache.get('doc'):
                _pack_cache['doc'] = bundled
                _pack_cache['_body'] = 'bundled'
    return bundled


def canon_text(pack):
    """The always-on canon block. Built once per content_hash and byte-
    stable across calls, so appending it to a system prompt keeps the
    provider prompt cache warm."""
    if not isinstance(pack, dict):
        return ''
    chash = pack.get('content_hash')
    with _lock:
        if chash and _pack_cache.get('canon_hash') == chash:
            return _pack_cache['canon']
    sections = pack.get('canon') or []
    lines = [
        "CROSSWALK HOUSE CANON (standing rules; these bind every reply)",
        "=============================================================",
    ]
    for sec in sections:
        title = str(sec.get('title') or '').strip()
        text = str(sec.get('text') or '').strip()
        if not text:
            continue
        lines.append(f"{title}. {text}")
    block = '\n\n'.join([lines[0] + '\n' + lines[1]] + lines[2:]) \
        if len(lines) > 2 else ''
    with _lock:
        _pack_cache['canon'] = block
        _pack_cache['canon_hash'] = chash
    return block


def with_canon(system_prompt, s3_client=None, bucket=None):
    """Append the canon block to a system prompt. On any failure the
    base prompt ships unchanged."""
    try:
        pack = get_pack(s3_client=s3_client, bucket=bucket)
        block = canon_text(pack)
        if block:
            return f"{system_prompt}\n\n{block}"
    except Exception:
        pass
    return system_prompt


# ---------------------------------------------------------------------------
# Topical module selection (rides the existing surface/intent split)
# ---------------------------------------------------------------------------

_NAMING_RX = re.compile(
    r'\b(name|named|names|naming|rename|call it|called|what do (?:you|we) '
    r'call|title of|label(?:ed)?|filed under|formerly|rebrand\w*)\b',
    re.IGNORECASE)
_UNIVERSE_RX = re.compile(
    r'\b(avid|casual|total universe|\bTU\b|cuts?|parent profile|subset|'
    r'under[- ]18|parents of|co[- ]?view\w*|season \d{1,2}|franchise|'
    r'universe|tiers?|superfans?)\b',
    re.IGNORECASE)
_CATEGORY_RX = re.compile(
    r'\b(categor(?:y|ies)|taxonomy|filed|classif\w+|what kind of profile)\b',
    re.IGNORECASE)


def select_topic_modules(surface, text='', ctx=None, mode=None):
    """Module names for this request, in stable order, max 3.

    Surface-driven: deck surfaces always carry 'decks'; the search-
    demand surface always carries 'search_demand'. Text/context-driven:
    naming-shaped asks attach 'naming'; cut overlays, viewers subjects,
    or universe vocabulary attach 'universes'; category vocabulary
    attaches 'categories'."""
    t = str(text or '')
    mods = []
    if surface in ('deck', 'insights_deck', 'deck_plan'):
        mods.append('decks')
    if surface == 'search_demand':
        mods.append('search_demand')
    ctx = ctx if isinstance(ctx, dict) else {}
    primary_name = str(((ctx.get('primary') or {}) or {}).get('name') or '')
    has_cuts = bool(ctx.get('cuts'))
    viewers_subject = 'viewer' in primary_name.lower() \
        or ' - ' in primary_name
    if _UNIVERSE_RX.search(t) or has_cuts or viewers_subject \
            or mode == 'cross_profile':
        mods.append('universes')
    if _NAMING_RX.search(t):
        mods.append('naming')
    if _CATEGORY_RX.search(t):
        mods.append('categories')
    seen, out = set(), []
    for m in mods:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out[:3]


def module_text(pack, names):
    """Concatenated topical module text, stable order by `names`."""
    if not isinstance(pack, dict) or not names:
        return ''
    topical = pack.get('topical') or {}
    parts = []
    for name in names:
        mod = topical.get(name)
        if not isinstance(mod, dict):
            continue
        title = str(mod.get('title') or name).strip()
        text = str(mod.get('text') or '').strip()
        if text:
            parts.append(f"{title}. {text}")
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Decisions of record
# ---------------------------------------------------------------------------

# Server-side trigger patterns for asks phrased in vocabulary that the
# scrubbed decisions corpus cannot itself carry (the compiler's leak
# gate keeps those words out of the JSON). Detection lives here, in
# code, per the standing security boundary.
_EXTRA_TRIGGERS = {
    'individual_level': re.compile(r'\bhouse\s?holds?\b|\bHHs?\b'),
    'clickstream_boundary': re.compile(
        r'\b(tune[- ]in|linear tv|over[- ]the[- ]air|foot traffic|'
        r'in[- ]store|brick[- ]and[- ]mortar)\b', re.IGNORECASE),
}


def get_decisions(s3_client=None, bucket=None):
    """The decisions-of-record list: S3 copy when reachable, else the
    bundled repo copy, else empty."""
    if s3_client is not None and bucket:
        _conditional_fetch(s3_client, bucket, DECISIONS_KEY,
                           _decisions_cache)
        with _lock:
            doc = _decisions_cache.get('doc')
        if isinstance(doc, dict):
            return doc.get('decisions') or []
    with _lock:
        doc = _decisions_cache.get('doc')
    if isinstance(doc, dict):
        return doc.get('decisions') or []
    bundled = _load_bundled(_BUNDLED_DECISIONS)
    if isinstance(bundled, dict):
        with _lock:
            if not _decisions_cache.get('doc'):
                _decisions_cache['doc'] = bundled
                _decisions_cache['_body'] = 'bundled'
        return bundled.get('decisions') or []
    return []


def match_decisions(text, subject='', decisions=None, limit=4):
    """Decisions whose match terms (or server-side trigger patterns)
    appear in the ask. Scored by hit count, top `limit` returned."""
    hay = f"{text or ''} {subject or ''}".lower()
    if not hay.strip():
        return []
    scored = []
    for d in (decisions or []):
        if not isinstance(d, dict) or not d.get('statement'):
            continue
        score = 0
        for term in (d.get('match_terms') or []):
            t = str(term or '').lower().strip()
            if t and t in hay:
                score += 2 if len(t) > 6 else 1
        rx = _EXTRA_TRIGGERS.get(d.get('id'))
        if rx is not None and rx.search(f"{text or ''} {subject or ''}"):
            score += 3
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda sd: (-sd[0], str(sd[1].get('id'))))
    return [d for _s, d in scored[:limit]]


def decisions_text(matched):
    if not matched:
        return ''
    lines = ["STANDING DECISIONS (Crosswalk decisions of record; "
             "these bind):"]
    for d in matched:
        lines.append(f"- {str(d.get('statement') or '').strip()}")
    return '\n'.join(lines)


def knowledge_block(surface, text='', ctx=None, mode=None, subject='',
                    s3_client=None, bucket=None):
    """The per-request HOUSE KNOWLEDGE block for a user prompt: the
    intent-selected topical modules plus any matching decisions of
    record. Returns '' when nothing applies. Never raises."""
    try:
        pack = get_pack(s3_client=s3_client, bucket=bucket)
        mods = select_topic_modules(surface, text=text, ctx=ctx, mode=mode)
        mod_txt = module_text(pack, mods)
        matched = match_decisions(
            text, subject=subject,
            decisions=get_decisions(s3_client=s3_client, bucket=bucket))
        dec_txt = decisions_text(matched)
        body = '\n\n'.join(p for p in (mod_txt, dec_txt) if p)
        if not body:
            return ''
        block = (
            "HOUSE KNOWLEDGE FOR THIS ASK\n"
            "============================\n"
            f"{body}"
        )
        if len(block) > KNOWLEDGE_BLOCK_MAX_CHARS:
            block = block[:KNOWLEDGE_BLOCK_MAX_CHARS].rsplit('\n', 1)[0]
        return block
    except Exception:
        return ''
