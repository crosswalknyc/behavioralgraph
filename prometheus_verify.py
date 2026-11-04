"""Pre-banking verification for generated reads (2026-08-28, p3-verify).

Every fresh generated read passes three checks after coherence
enforcement and before it banks into the insights ledger:

1. ANCHOR RECOMPUTE - when the read's base is a profile, at least one
   concrete measured figure cited in the reply (a penetration, an
   index, a demo share) is recomputed from the base file itself and
   must match within rounding. The base rows come from the nightly
   precomputed index table when its profile ETag still matches, else
   from the live CSV.
2. LEDGER COHERENCE - the new read's metrics are compared against the
   subject's existing ledger entries for the same family, cohort and
   window (delivered_artifact provenance held to the tightest band).
   A direct numeric contradiction fails the check.
3. SCRUB RESIDUE - the reply has already been through scrub_user_text;
   this check fails only when internal vocabulary or banned characters
   survived the mechanical scrub (which means the model must rewrite,
   not just re-replace).

The caller (app._pm_generate_read_core) runs ONE self-revision loop on
failure: the findings are appended to the generation prompt, the model
regenerates, and the result re-verifies. A second failure holds the
read (never banked, never delivered).

All comparison logic here is pure arithmetic on plain dicts so the
whole pass is testable without S3 or a model key. Only
load_base_lookup touches the network.
"""

import json
import re
import time
import traceback

# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------
# The anchor pass distinguishes three zones per bound claim:
#   anchored      - within rounding of a base row (tolerance from the
#                   quoted precision plus a small slack)
#   contradiction - far outside any base row for that label (both an
#                   absolute and a relative margin must be exceeded, so
#                   a 1-in-rounding miss can never hold a read)
#   ambiguous     - between the two; counts as neither
PCT_SLACK = 0.3            # added to the half-unit rounding tolerance
PCT_HARD_ABS = 2.0         # percentage points
PCT_HARD_REL = 0.12
IDX_SLACK = 2.0            # index points added to rounding tolerance
IDX_HARD_ABS = 12.0        # index points
IDX_HARD_REL = 0.08

# Ledger coherence bands (new value vs stored value, same metric name,
# same family + cohort + compatible window).
LEDGER_PCT_ABS = 2.5
LEDGER_PCT_REL = 0.12
LEDGER_COUNT_REL = 0.18
# delivered_artifact provenance: figures a client already holds.
LEDGER_PCT_ABS_DELIVERED = 1.5
LEDGER_PCT_REL_DELIVERED = 0.08
LEDGER_COUNT_REL_DELIVERED = 0.12

MAX_CLAIMS = 40
MAX_FINDINGS = 6

_PCT_UNIT_TOKENS = ('pct', 'percent', '%', 'share')

# Residual internal vocabulary that must never survive into a banked
# reply. The mechanical scrub already ran; anything matching here means
# the model itself has to rewrite the sentence.
_RESIDUAL_BANNED = (
    (re.compile(r'\b(claude|anthropic|openai|chatgpt|gpt-\d|llm|'
                r'language model)\b', re.I), 'names the model'),
    (re.compile(r'\bas an ai\b', re.I), 'speaks as an AI'),
    (re.compile(r'\b(synthesi[sz]\w*|synthetic|synth)\b', re.I),
     'internal generation vocabulary'),
    (re.compile(r'\b(hostmap|panelists?|clickhouse|hetzner|'
                r'post-generation enforcer\w*)\b', re.I),
     'internal infrastructure vocabulary'),
    (re.compile(r'\bweb[ -]?search(es|ed|ing)?\b', re.I),
     'names the research mechanism'),
    (re.compile(r'[\u2014\u2013]'), 'em or en dash'),
    (re.compile(r'\bhouseholds?\b(?!\s+income)', re.I),
     'household noun (counts are individual-level)'),
)

_LABEL_STOPWORDS = {
    'the', 'a', 'an', 'and', 'with', 'while', 'where', 'which', 'that',
    'its', 'their', 'this', 'these', 'those', 'both', 'also', 'but',
    'or', 'as', 'at', 'in', 'on', 'for', 'of', 'to', 'by',
}

# Labels that describe the audience itself, not a base row. They must
# never bind to brand/demo rows: on 2026-08-28 the Shark Tank income
# ask captured 'audience and an' from prose, containment-matched the
# 4-char key 'audi', and verified the income-tilt claim against the
# AUDI car brand row (131 vs 106.3 -> false hold).
_GENERIC_LABEL_NORMS = frozenset({
    'audience', 'audiences', 'viewer', 'viewers', 'fan', 'fans',
    'fanbase', 'base', 'cohort', 'universe', 'population', 'genpop',
    'generalpopulation', 'index', 'tilt', 'share', 'reach', 'profile',
    'file', 'subject', 'read', 'people', 'adults', 'household',
    'households', 'income', 'group', 'segment', 'us',
    # verbs that sit next to a figure are never a row label
    # (2026-10-06: 'carries' bound by containment to the Carrie
    # Underwood row and failed a clean read)
    'carries', 'carry', 'carrying', 'runs', 'running', 'reads',
    'reading', 'sits', 'sitting', 'lands', 'landing', 'measures',
    'reaches', 'reaching', 'indexes', 'holds', 'stands', 'clocks',
    'posts', 'hits', 'comes', 'shows', 'prints', 'registers',
    'tracks', 'trails', 'leads', 'lifts', 'climbs', 'drops',
})

_NORM_RX = re.compile(r'[^a-z0-9]+')

# Labels that END in a cohort-relation noun describe a relationship
# between audiences ("into Stella Lefty's audience", "her own file"),
# never a base row (2026-09-30 Stella Lefty comparative defect: the
# overlap phrasing containment-matched the subject's self-pin row at
# 100% and held the read).
_COHORT_TAIL_TOKENS = frozenset({
    'audience', 'audiences', 'file', 'files', 'fans', 'fanbase',
    'viewers', 'listeners', 'followers', 'base', 'cohort', 'cohorts',
    'universe', 'overlap', 'crossover', 'reach', 'peers',
    'competitors', 'rivals',
})

# A label that reads as one cell of a split by services or brands: an
# exclusivity word, a combination, or a negation around the names. Such
# a label is a share of the audience, never a base row for one brand.
_SPLIT_CELL_RX = re.compile(
    r"\b(?:only|alone|exclusively|exclusive|both|neither|either|without|"
    r"and|or|plus|not|non|but)\b", re.I)

# Comparative-ask detection on the QUESTION text. Baseline comparisons
# ("vs Gen Pop", "against the US average") are stripped first so they
# never count as competitor comparisons.
_GENPOP_STRIP_RX = re.compile(
    r'(?:versus|vs\.?|compared\s+(?:to|with|against)|against)?\s*'
    r'(?:the\s+)?(?:gen(?:eral)?\s*pop\w*|us average|'
    r'national average|baseline)', re.I)
_COMPARATIVE_RX = re.compile(
    r'\b(compar\w+|competit\w+|versus|vs\.?|peers?|rivals?|'
    r'side by side|stack(?:s|ed)?\s+up|head to head|landscape|'
    r'others|other\s+(?:artists|acts|brands|names|titles))\b', re.I)

# In-sentence field-comparison markers: a demo/pct figure in a
# sentence carrying one of these describes the field, not the base
# subject, unless the base subject is named before the figure.
_SENT_COMPARE_RX = re.compile(
    r'\b(peers?|competitors?|rivals?|others|comparable|'
    r'similar\s+(?:acts|artists|names)|category\s+(?:average|norm)|'
    r'typical|versus|vs\.?|compared|the field)\b', re.I)

# TitleCase entity sequences (1-3 words) for sentence attribution.
# Each word must be pure TitleCase ([A-Z][a-z]{2,}) with no letter
# following, so CamelCase brands (TikTok, YouTube) and short particles
# never register as entities.
_ENTITY_SEQ_RX = re.compile(
    r"\b[A-Z][a-z]{2,}(?:['\u2019]s?)?(?![A-Za-z])"
    r"(?:\s+[A-Z][a-z]{2,}(?:['\u2019]s?)?(?![A-Za-z])){0,2}")
_ENTITY_COMMON = frozenset({
    'the', 'she', 'her', 'his', 'he', 'they', 'their', 'while',
    'when', 'where', 'which', 'that', 'this', 'these', 'those',
    'and', 'but', 'for', 'with', 'against', 'among', 'across',
    'most', 'both', 'only', 'even', 'still', 'meanwhile', 'however',
    'here', 'there', 'what', 'who', 'its', 'our', 'your', 'you',
    'use', 'not', 'now', 'per', 'via', 'versus', 'compared',
    'january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december',
})


def _base_tokens(name):
    return {w for w in re.split(r'[^a-z0-9]+', str(name or '').lower())
            if len(w) >= 3}


# Journey / next-step reads (2026-10-08, Casey Pearson at Paramount+:
# "what do subscribers do after they come to Paramount+ but don't
# stream anything ... what % go to another streaming service ... what
# services ... what do they watch"). A brand or genre share in such a
# read is the share of LEAVERS who went there next, never the base
# profile's penetration row for that brand. The anchor pass used to
# bind "Netflix 54.9%" to the base row (74.9% of subscribers use
# Netflix) and hold a correct read.
_JOURNEY_ASK_RX = re.compile(
    r"\b(?:what (?:do|did) (?:they|subscribers|viewers|users|people) do "
    r"(?:after|next|when)|after (?:they|a|the|their)\b.{0,60}\b(?:leave|"
    r"leaving|exit|bounce|browse|session|visit|don'?t (?:stream|watch|play))|"
    r"go to another|goes? to (?:another|a different|other)|switch(?:es|ed)? to|"
    r"what (?:services?|platforms?|apps?) do they (?:go|turn|move|switch) to|"
    r"where do they go|next (?:stop|service|platform|destination)|"
    r"immediately after|right after|destination|journey|path(?:way)? "
    r"(?:to|from|after)|what do they (?:watch|open|start) (?:there|next|instead|"
    r"on (?:the )?other))\b", re.I)
_JOURNEY_SENT_RX = re.compile(
    r"\b(?:go(?:es)? to|went to|head(?:s|ed)? to|move(?:s|d)? to|switch(?:es|ed)? to|"
    r"turn(?:s|ed)? to|leave for|left for|land(?:s|ed)? on|open(?:s|ed)? next|"
    r"next (?:stop|service|platform|destination)|destinations?|after leaving|"
    r"after (?:a|the|their) (?:browse|browse-only|session|visit|bounce)|"
    r"within (?:the|an?|\d+) (?:hour|minutes?)|immediately after|right after|"
    r"of (?:those|the ones|the leavers|leavers|the exits|exits) who|"
    r"share of (?:those|leavers|exits|the leavers)|first title|what they "
    r"(?:watch|start|open|stream) (?:there|next|on|instead)|genre mix|"
    r"genres? they|titles? they)\b", re.I)


def is_journey_ask(question):
    """True when the ask is about what people do NEXT (destinations,
    switches, what they watch elsewhere). Brand and genre shares in
    the answer are then journey shares, not base-row citations."""
    return bool(_JOURNEY_ASK_RX.search(str(question or '')))


def _journey_share_sentence(text, m_start, m_end):
    """True when the sentence around a claim frames it as a next-step
    or destination share."""
    s, e = _sentence_bounds(text, m_start, m_end)
    return bool(_JOURNEY_SENT_RX.search(text[s:e]))


def is_comparative_ask(question):
    """True when the ask compares the subject against other entities
    (competitors, peers, other artists). Gen Pop / US-average baseline
    comparisons never count."""
    q = _GENPOP_STRIP_RX.sub(' ', str(question or ''))
    return bool(_COMPARATIVE_RX.search(q))


def _sentence_bounds(text, start, end):
    lo = max(0, start - 240)
    s = -1
    for ch in ('.', '!', '?', '\n'):
        s = max(s, text.rfind(ch, lo, start))
    hi = min(len(text), end + 240)
    nxt = [text.find(ch, end, hi) for ch in ('.', '!', '?', '\n')]
    nxt = [n for n in nxt if n != -1]
    return (s + 1 if s >= 0 else lo), (min(nxt) if nxt else hi)


def _entities_in(text, base_toks, multi_only=False):
    """(base_mentions, foreign_entities) as (pos, text) lists."""
    base_hits, foreign = [], []
    for m in _ENTITY_SEQ_RX.finditer(text):
        words = re.split(r'\s+', m.group(0))
        toks = {re.sub(r'[^a-z0-9]', '', w.lower()) for w in words}
        toks.discard('')
        if len(words) == 1 and (next(iter(toks), '')
                                in _ENTITY_COMMON):
            continue
        if toks & base_toks:
            base_hits.append((m.start(), m.group(0)))
        elif not all(t in _ENTITY_COMMON for t in toks):
            if multi_only and len(words) == 1:
                continue
            foreign.append((m.start(), m.group(0)))
    return base_hits, foreign


def _off_base_text_claim(text, m_start, m_end, base_toks,
                         lab_start=None, val_start=None):
    """True when the sentence around a text claim attributes the
    figure to someone other than the base subject: a named competitor
    is the nearest entity before the claim, a possessive competitor
    sits inside the label window itself ("Gracie Abrams' audience
    shows Spotify at 81.4%"), another multi-word name follows in the
    same sentence, or the sentence is a field comparison with the base
    subject never named before the figure. Off-base claims are skipped
    by the anchor pass (fail-open)."""
    s, e = _sentence_bounds(text, m_start, m_end)
    before = text[s:m_start]
    base_b, foreign_b = _entities_in(before, base_toks)
    # Possessive attributors inside the label window own the figure.
    # Non-possessive label entities are the bind target itself ("Her
    # Spotify share") and stay exempt.
    if lab_start is not None and val_start is not None \
            and lab_start < val_start:
        for m2 in _ENTITY_SEQ_RX.finditer(text[lab_start:val_start]):
            seg = m2.group(0).rstrip()
            if not seg.endswith(("'", "'s", "\u2019", "\u2019s")):
                continue
            toks = {re.sub(r'[^a-z0-9]', '', w.lower())
                    for w in seg.split()}
            toks.discard('')
            if toks and not (toks & base_toks) \
                    and not all(t in _ENTITY_COMMON for t in toks):
                foreign_b.append((lab_start + m2.start(), seg))
    nearest_base = max((p for p, _t in base_b), default=-1)
    nearest_foreign = max((p for p, _t in foreign_b), default=-1)
    if nearest_foreign > nearest_base:
        return True
    if nearest_base >= 0:
        return False
    _ba, foreign_after = _entities_in(text[m_end:e], base_toks,
                                      multi_only=True)
    if foreign_after:
        return True
    return bool(_SENT_COMPARE_RX.search(text[s:e]))


def _norm(label):
    return _NORM_RX.sub('', str(label or '').lower())


def _trim_label(label):
    """Last few meaningful words of a regex-captured label window,
    stopwords stripped from BOTH ends ('the audience and an' ->
    'audience', not 'audience and an')."""
    words = [w for w in re.split(r'\s+', str(label or '').strip()) if w]
    while words and words[0].lower() in _LABEL_STOPWORDS:
        words.pop(0)
    while words and words[-1].lower() in _LABEL_STOPWORDS:
        words.pop()
    return ' '.join(words[-4:])


def _dp(value, text=None):
    """Decimal places the figure was quoted at (drives the rounding
    tolerance)."""
    if text is not None and '.' in str(text):
        return min(len(str(text).split('.', 1)[1]), 4)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if abs(v - round(v)) < 1e-9:
        return 0
    if abs(v * 10 - round(v * 10)) < 1e-6:
        return 1
    return 2


# ---------------------------------------------------------------------------
# Base lookup (anchor source)
# ---------------------------------------------------------------------------

def build_base_lookup(index_doc, genpop=None):
    """Plain lookup dict from a nightly profile index doc:
    {'brands': {norm: [(category, bp, index_or_None)]},
     'demos':  {norm: [(category, bp, genpop_bp_or_None, label)]},
     'name': ...}. `genpop` (optional {(cat, brand_norm): bp}) prices
    demo buckets against Gen Pop so composite tilts (e.g. the $100K+
    income index) are recomputable."""
    if not isinstance(index_doc, dict):
        return None
    brands, demos = {}, {}
    for cat, block in (index_doc.get('categories') or {}).items():
        for row in (block or {}).get('rows') or []:
            try:
                label, bp, idx = row[0], float(row[1]), row[2]
            except (TypeError, ValueError, IndexError):
                continue
            bn = _norm(label)
            if bn:
                brands.setdefault(bn, []).append(
                    (cat, bp, float(idx) if idx is not None else None))
    for row in (index_doc.get('purchase_index') or []):
        try:
            label, bp, idx = row[0], float(row[1]), row[2]
        except (TypeError, ValueError, IndexError):
            continue
        bn = _norm(label)
        if bn:
            brands.setdefault(bn, []).append(
                ('PURCHASE', bp, float(idx) if idx is not None else None))
    for cat, rows in (index_doc.get('demos') or {}).items():
        for row in (rows or []):
            try:
                bucket, bp = row[0], float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            bn = _norm(bucket)
            if bn:
                gp = _genpop_bp_for(genpop, cat, bucket)
                demos.setdefault(bn, []).append(
                    (cat, bp, gp, str(bucket)))
    if not brands and not demos:
        return None
    return {'name': index_doc.get('name') or '', 'source': 'index',
            'brands': brands, 'demos': demos}


def _genpop_bp_for(genpop, cat, label):
    """Gen Pop penetration for a (category, label) pair, tolerant of
    the two key shapes the genpop map is built with."""
    if not genpop:
        return None
    try:
        import prometheus_analysis as pma
        keys = ((str(cat).strip().upper(), pma._norm_brand(str(label))),
                (str(cat).strip().upper(), _norm(label)))
    except Exception:
        keys = ((str(cat).strip().upper(), _norm(label)),)
    for k in keys:
        gp = genpop.get(k)
        if gp is not None:
            try:
                return float(gp)
            except (TypeError, ValueError):
                return None
    return None


def lookup_from_frame(df, name, genpop=None):
    """CSV fallback: the same lookup dict built from the live profile
    frame (used when the nightly index is stale or missing)."""
    import prometheus_analysis as pma
    bp_c = pma._bp_col(df)
    brands, demos = {}, {}
    genpop = genpop or {}
    for cat, grp in df.groupby('Column', sort=False):
        catU = pma._norm_cat(cat)
        if not catU or catU in pma.METADATA_COLS:
            continue
        is_demo = catU in pma.DEMO_COLS
        for label, bpv in zip(grp['Value'].tolist(), grp[bp_c].tolist()):
            v = pma._parse_bp(bpv)
            label = str(label or '').strip()
            if v is None or not label:
                continue
            bn = _norm(label)
            if not bn:
                continue
            if is_demo:
                gp = _genpop_bp_for(genpop, catU, label)
                demos.setdefault(bn, []).append((catU, v, gp, label))
            else:
                gp = genpop.get((catU, pma._norm_brand(label)))
                idx = (round(v / gp * 100.0, 1)
                       if gp and gp >= 0.01 else None)
                brands.setdefault(bn, []).append((catU, v, idx))
    if not brands and not demos:
        return None
    return {'name': name or '', 'source': 'csv',
            'brands': brands, 'demos': demos}


def _merge_sibling_cuts(lookup, s3_client, bucket, s3_key, name=''):
    """Sibling derived-cut files ('<Subject> - Millennials.csv', ...)
    contribute their measured share-of-parent as demo-style entries so
    a generation/cohort claim verifies against the CUT FILE's own
    numbers (2026-09-23 Bria defect: a Millennials figure was derived
    as the parent-minus-other-cuts residual, 687,413 against the cut
    file's measured 662,142, and nothing caught it because the cuts
    were not in the lookup). Fail-open: any miss just skips."""
    if not isinstance(lookup, dict):
        return lookup
    try:
        import prometheus_analysis as pma
        display = str(name or '').strip()
        if not display:
            stem = s3_key.rsplit('/', 1)[-1]
            display = re.sub(r'_\d{2}_\d{2}_\d{4}.*$', '',
                             stem).replace('_', ' ').strip()
        if not display:
            return lookup
        parent_proj = None
        r = s3_client.list_objects_v2(Bucket=bucket,
                                      Prefix=f'{display} - ')
        sibs = [o['Key'] for o in (r.get('Contents') or [])
                if o['Key'].lower().endswith('.csv')]
        if not sibs:
            return lookup
        import csv as _csv
        import io as _io
        for sk in sibs[:8]:
            try:
                body = s3_client.get_object(
                    Bucket=bucket, Key=sk)['Body'].read()
                rows = list(_csv.DictReader(
                    _io.StringIO(body.decode('utf-8', 'replace'))))
                bi = next((x for x in rows
                           if str(x.get('Column', '')).strip()
                           .upper() in ('BRAND INPUT', 'SAMPLE SIZE')),
                          None)
                if not bi:
                    continue
                proj = float(str(bi.get('US Gen Pop Projection', '0'))
                             .replace(',', '') or 0)
                if proj <= 0:
                    continue
                if parent_proj is None:
                    pb = (lookup.get('meta') or {}).get('projection')
                    parent_proj = float(pb) if pb else None
                cut_label = sk.rsplit(' - ', 1)[-1][:-4].strip()
                cn = _norm(cut_label)
                if not cn:
                    continue
                share = (round(proj / parent_proj * 100.0, 4)
                         if parent_proj else None)
                lookup.setdefault('demos', {}).setdefault(cn, []).append(
                    ('GENERATION CUT', share if share is not None
                     else proj, None, f'{cut_label} (derived cut)'))
                lookup.setdefault('cut_projections', {})[cn] = int(proj)
            except Exception:
                continue
    except Exception:
        pass
    return lookup


def load_base_lookup(s3_client, bucket, s3_key, name=''):
    """Anchor source for one base profile: the nightly index table when
    its stored ETag still matches the live profile object, else the
    CSV. None when the base is not a loadable profile. Sibling derived
    cuts merge in as demo-style entries (share of parent) so cohort
    claims verify against the cut files' own measured values."""
    key = str(s3_key or '')
    if not key.lower().endswith('.csv'):
        return None
    import prometheus_analysis as pma
    live_etag = None
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
        live_etag = (head.get('ETag') or '').strip('"')
    except Exception:
        return None
    genpop = None
    try:
        genpop = pma.load_genpop_map(s3_client, bucket)
    except Exception:
        genpop = None
    lk = None
    try:
        doc = pma.load_profile_index(s3_client, bucket, key)
        if isinstance(doc, dict) and live_etag \
                and doc.get('etag') == live_etag:
            lk = build_base_lookup(doc, genpop=genpop)
    except Exception:
        lk = None
    if not lk:
        try:
            df, _etag = pma.load_profile_df(s3_client, bucket, key)
            lk = lookup_from_frame(df, name, genpop)
            try:
                bi_mask = df['Column'].astype(str).str.strip()\
                    .str.upper().isin(['BRAND INPUT', 'SAMPLE SIZE'])
                if bi_mask.any():
                    projv = str(df.loc[bi_mask].iloc[0].get(
                        'US Gen Pop Projection', '') or '')
                    projf = float(projv.replace(',', '') or 0)
                    if projf > 0 and isinstance(lk, dict):
                        lk.setdefault('meta', {})['projection'] = projf
            except Exception:
                pass
        except Exception:
            return None
    return _merge_sibling_cuts(lk, s3_client, bucket, key, name)


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

_IDX_TEXT_RX = (
    re.compile(r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40}?)\s+"
               r"index(?:es)?\s+(?:of\s+|at\s+|is\s+|to\s+)?"
               r"(?P<val>\d{2,4})(?![\d.%])"),
    re.compile(r"index(?:es)?\s+(?:of\s+|at\s+)?(?P<val>\d{2,4})\s+"
               r"(?:for|on|against)\s+"
               r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40})"),
)
_PCT_TEXT_RX = (
    re.compile(r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40}?)\s+"
               r"(?:at|reaches|hits|sits at|lands at|shows|measures)\s+"
               r"(?P<val>\d{1,2}(?:\.\d{1,2})?)%"),
    re.compile(r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40}?)\s+"
               r"pen(?:etration)?\s+(?:of\s+|at\s+|is\s+)?"
               r"(?P<val>\d{1,2}(?:\.\d{1,2})?)%"),
)
_DEMO_TEXT_RX = (
    re.compile(r"(?P<val>\d{1,2}(?:\.\d)?)%\s+"
               r"(?P<label>female|male|women|men)\b", re.I),
    re.compile(r"\b(?P<label>female|male|women|men)\b[^.\d%]{0,16}?"
               r"(?P<val>\d{1,2}(?:\.\d)?)%", re.I),
)
_DEMO_ALIASES = {'women': 'female', 'men': 'male'}


def extract_claims(reply, res, base_name=None, comparative=False):
    """Concrete measured figures the reply commits to, as claims:
    {'kind': 'pct'|'index'|'demo', 'label', 'value', 'dp', 'src',
     'off_base'}. off_base claims belong to an entity other than the
    base subject (competitor figures in a comparative read) and are
    skipped by the anchor pass."""
    claims, seen = [], set()
    base_toks = _base_tokens(base_name)

    def _add(kind, label, value, dp, src, off_base=False, journey=False):
        label = _trim_label(label)
        ln = _norm(label)
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if not ln or len(ln) < 3:
            return
        if kind in ('pct', 'demo') and not (0.0 < v <= 100.0):
            return
        if kind == 'index' and not (25.0 <= v <= 2500.0):
            return
        dk = (kind, ln, round(v, 4))
        if dk in seen or len(claims) >= MAX_CLAIMS:
            return
        seen.add(dk)
        claims.append({'kind': kind, 'label': label, 'value': v,
                       'dp': dp, 'src': src, 'off_base': off_base,
                       'journey': bool(journey)})

    def _structured_off(label):
        # In a comparative read, structured rows are per-entity
        # (competitor names in the labels). Only rows naming the base
        # subject stay bound to the base file.
        if not comparative or not base_toks:
            return False
        words = {re.sub(r'[^a-z0-9]', '', w.lower())
                 for w in re.split(r'\s+', str(label or ''))}
        words.discard('')
        return not (words & base_toks)

    res = res or {}
    for row in ((res.get('breakdown') or {}).get('rows') or []):
        if not isinstance(row, dict):
            continue
        pen = row.get('penetration_pct')
        if isinstance(pen, (int, float)):
            _add('pct', row.get('label'), pen, _dp(pen), 'breakdown',
                 off_base=_structured_off(row.get('label')))
    for m in (res.get('metrics') or []):
        if not isinstance(m, dict):
            continue
        name = str(m.get('name') or '')
        label = str(m.get('label') or name)
        unit = str(m.get('unit') or '').lower()
        val = m.get('value')
        if not isinstance(val, (int, float)):
            continue
        nl = (name + ' ' + label).lower()
        if 'index' in nl:
            _add('index', label or name, val, _dp(val), 'metrics',
                 off_base=_structured_off(label or name))
        elif any(t in unit for t in _PCT_UNIT_TOKENS) \
                and 'share' not in nl and 'composition' not in nl:
            _add('pct', label or name, val, _dp(val), 'metrics',
                 off_base=_structured_off(label or name))

    text = str(reply or '')
    for rx in _IDX_TEXT_RX:
        for m in rx.finditer(text):
            _add('index', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks,
                     lab_start=m.start('label'),
                     val_start=m.start('val')))
    for rx in _PCT_TEXT_RX:
        for m in rx.finditer(text):
            _add('pct', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks,
                     lab_start=m.start('label'),
                     val_start=m.start('val')),
                 journey=_journey_share_sentence(text, m.start(), m.end()))
    for rx in _DEMO_TEXT_RX:
        for m in rx.finditer(text):
            lab = _DEMO_ALIASES.get(m.group('label').lower(),
                                    m.group('label').lower())
            _add('demo', lab, m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks))
    return claims


# ---------------------------------------------------------------------------
# Check 1: anchor recompute
# ---------------------------------------------------------------------------

_INCOME_AMOUNT_RX = re.compile(
    r'\$\s*(?P<full>\d{1,3}(?:,\d{3})+)|(?<![\d.])(?P<k>\d{2,3})\s*[kK]\b')
_INCOME_PLUS_RX = re.compile(
    r'\+|or more|plus|and up|above|over|at least|minimum', re.I)
_INCOME_WORD_RX = re.compile(
    r'income|earn|household|hhi|affluent|\$', re.I)
_BUCKET_FLOOR_RX = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{4,})')


def _income_threshold_from_label(label):
    """Dollar floor when a claim label reads as an income-threshold
    tilt ('$100K+ households', 'earning 100k or more'); else None."""
    text = str(label or '')
    m = _INCOME_AMOUNT_RX.search(text)
    if not m:
        return None
    if not _INCOME_PLUS_RX.search(text):
        return None
    if not _INCOME_WORD_RX.search(text):
        return None
    if m.group('full'):
        return int(m.group('full').replace(',', ''))
    return int(m.group('k')) * 1000


def _income_composite_index(lookup, threshold):
    """Recompute the audience-vs-Gen-Pop index for the income buckets
    at or above `threshold` from the base demo rows. None when the
    buckets or their Gen Pop denominators are unavailable."""
    aud = gp = 0.0
    n = 0
    for rows in (lookup.get('demos') or {}).values():
        for row in rows:
            if len(row) < 4 or str(row[0]).strip().upper() != 'INCOME':
                continue
            label = str(row[3])
            low = 0
            if not re.search(r'less than|under', label, re.I):
                fm = _BUCKET_FLOOR_RX.search(label)
                if not fm:
                    continue
                low = int(fm.group(1).replace(',', ''))
            if low < threshold:
                continue
            if row[2] is None:
                return None
            aud += float(row[1])
            gp += float(row[2])
            n += 1
    if not n or gp <= 0.01:
        return None
    return round(aud / gp * 100.0, 1)


_HEADING_TOKENS = frozenset({
    'cpg', 'grocery', 'groceries', 'apparel', 'footwear', 'beauty', 'wellness',
    'brands', 'brand', 'category', 'categories', 'platform', 'platforms',
    'streaming', 'media', 'podcast', 'podcasts', 'shows', 'show', 'series',
    'creators', 'creator', 'influencers', 'influencer', 'talent', 'retail',
    'retailers', 'shopping', 'purchases', 'purchase', 'purchased', 'most',
    'top', 'social', 'apps', 'app', 'games', 'gaming', 'music', 'video',
    'travel', 'qsr', 'dining', 'restaurants', 'restaurant', 'auto', 'automotive',
    'home', 'outdoor', 'accessories', 'pets', 'toys', 'technology', 'devices',
    'device', 'telecom', 'banking', 'finance', 'insurance', 'and', 'or', 'the',
    'of', 'in', 'their', 'audience', 'tier', 'mix', 'share', 'spend'})


def _is_heading_label(label):
    """True when a claim label is made only of heading words ("CPG and
    grocery", "Apparel and footwear", "top podcasts"): a category
    composite the model summarized, never a row (2026-10-06)."""
    toks = [w for w in re.split(r'[^a-z0-9]+', str(label or '').lower()) if w]
    return bool(toks) and all(w in _HEADING_TOKENS or len(w) <= 2 for w in toks)


def _category_norms(lookup):
    """Normalized Column names present in the lookup (cached on it)."""
    try:
        cached = lookup.get('_category_norms')
        if cached is not None:
            return cached
        cats = set()
        for table in ('brands', 'demos'):
            for rows in (lookup.get(table) or {}).values():
                for r in rows:
                    if r and r[0]:
                        cats.add(_norm(r[0]))
        # the ask-side spellings of the same headings
        for extra in ('most purchased brands', 'mpb', 'streaming platform',
                      'streaming platforms', 'social media', 'where they shop',
                      'apparel footwear', 'app platform usage', 'app platform'):
            cats.add(_norm(extra))
        lookup['_category_norms'] = cats
        return cats
    except Exception:
        return set()


def _candidates_for(claim, lookup):
    """Base rows a claim can bind to, by normalized-label match (exact
    first, containment when both sides are 5+ chars). Generic audience
    nouns never bind; income-threshold index claims bind to the
    recomputed bucket composite."""
    ln = _norm(claim['label'])
    if claim['kind'] == 'index':
        threshold = _income_threshold_from_label(claim['label'])
        if threshold is not None:
            comp = _income_composite_index(lookup, threshold)
            return [comp] if comp is not None else []
    if ln in _GENERIC_LABEL_NORMS:
        return []
    # A category name is a heading, never a row (2026-10-06: "most
    # purchased brands at 91.4%" bound a brand row by containment and
    # held a clean four-category read).
    if ln in _category_norms(lookup) or _is_heading_label(claim['label']):
        return []
    # A label ending in a cohort-relation noun ("into Stella Lefty's
    # audience", "her own file") describes a relationship between
    # audiences, never a base row.
    tail_words = [w for w in re.split(r'[^a-z0-9]+',
                                      str(claim['label']).lower()) if w]
    if tail_words and tail_words[-1] in _COHORT_TAIL_TOKENS:
        return []
    # A cell of a split ("Netflix only", "both Netflix and Hulu", "Hulu
    # without Netflix") is a share of the audience cut by its services,
    # never the base row of one service. It binds on an exact label
    # only (2026-11-04, Jay Patel's Love Hypothesis read: "Netflix only
    # at 3.9%" bound by containment to the NETFLIX row at 76.6%, two
    # cells the question asked for were held, trimmed, and the reader
    # was told they were left out).
    split_cell = bool(_SPLIT_CELL_RX.search(str(claim['label'] or '')))
    subj_norm = _norm(lookup.get('name') or '')
    # The only base row under the subject's own name is the structural
    # 100% self-pin. A penetration attributed to the subject's name is
    # audience-share phrasing, never a citation of that pin; binding it
    # manufactures a contradiction. Subject-name claims never bind.
    if subj_norm and ln == subj_norm:
        return []
    table = lookup['demos'] if claim['kind'] == 'demo' \
        else lookup['brands']
    rows = list(table.get(ln) or [])
    if not rows and claim['kind'] == 'pct':
        rows = list(lookup['demos'].get(ln) or [])
    if not rows and len(ln) >= 5 and not split_cell:
        for key, krows in table.items():
            # Containment never binds to the subject's own self-pin
            # row; only an exact label match may cite the subject row.
            if subj_norm and key == subj_norm:
                continue
            if len(key) >= 5 and (ln in key or key in ln):
                rows.extend(krows)
            if len(rows) >= 8:
                break
    out = []
    for row in rows:
        if claim['kind'] == 'index':
            idx = row[2] if len(row) > 2 else None
            if idx is not None:
                out.append(float(idx))
        else:
            out.append(float(row[1]))
    return out


def anchor_check(reply, res, base_lookup, question=None):
    """Recompute cited figures from the base rows. Returns
    {'status': 'pass'|'fail'|'skip', 'detail', 'anchored',
     'findings': [...]}. Claims attributed to entities other than the
    base subject (competitor figures in a comparative read) are
    skipped, never held."""
    if not base_lookup:
        return {'status': 'skip', 'detail': 'base is not a profile',
                'anchored': 0, 'findings': []}
    claims = extract_claims(
        reply, res, base_name=(base_lookup or {}).get('name') or '',
        comparative=is_comparative_ask(question))
    if not claims:
        return {'status': 'skip', 'detail': 'no recomputable figure cited',
                'anchored': 0, 'findings': []}
    journey_ask = is_journey_ask(question)
    anchored, findings, bound = 0, [], 0
    sample_hits = []
    for c in claims:
        if c.get('off_base'):
            continue
        # Next-step shares (where leavers went, what they watched
        # there) are not citations of the base profile's rows. Demo
        # shares still bind: a journey read's "61% female" is still
        # this audience's gender row.
        if c['kind'] != 'demo' and (c.get('journey') or journey_ask):
            continue
        cands = _candidates_for(c, base_lookup)
        if not cands:
            continue
        bound += 1
        if c['kind'] == 'index':
            tol = max(1.0, 0.5 * 10 ** -c['dp']) + IDX_SLACK
            hard_abs, hard_rel = IDX_HARD_ABS, IDX_HARD_REL
        else:
            tol = 0.5 * 10 ** -c['dp'] + PCT_SLACK
            hard_abs, hard_rel = PCT_HARD_ABS, PCT_HARD_REL
        best = min(cands, key=lambda b: abs(b - c['value']))
        dist = abs(best - c['value'])
        if dist <= tol:
            anchored += 1
            if len(sample_hits) < 3:
                sample_hits.append(
                    f"{c['label']} {c['value']:g} vs base {best:g}")
        elif dist > max(hard_abs, 5 * tol) \
                and dist / max(abs(best), 1e-9) > hard_rel:
            unit = '' if c['kind'] == 'index' else '%'
            findings.append(
                f"The reply cites {c['label']} at {c['value']:g}{unit} "
                f"but the base file measures {best:g}{unit}. Use the "
                f"measured figure or drop the claim.")
    if findings:
        return {'status': 'fail', 'anchored': anchored,
                'detail': f"{len(findings)} figure(s) contradict the "
                          f"base rows ({bound} bound)",
                'findings': findings[:MAX_FINDINGS]}
    if anchored:
        return {'status': 'pass', 'anchored': anchored,
                'detail': f"anchored {anchored}/{bound} bound claim(s): "
                          + '; '.join(sample_hits),
                'findings': []}
    return {'status': 'skip', 'anchored': 0,
            'detail': f"{len(claims)} claim(s), none bindable to base "
                      "rows", 'findings': []}


# ---------------------------------------------------------------------------
# Check 2: ledger coherence
# ---------------------------------------------------------------------------

def _cohort_sig(text):
    try:
        import insights_ledger as il
        sig = il.cohort_signature(text)
        return (sig.get('ages'), sig.get('parents'))
    except Exception:
        return (_norm(text),)


def _windows_compatible(new_ws, new_we, old_ws, old_we):
    """Blank windows on either side compare as compatible (undated
    anchors are standing figures); dated windows must overlap."""
    if not (new_ws and new_we and old_ws and old_we):
        return True
    return not (new_we < old_ws or old_we < new_ws)


def _is_pct_unit(unit, name=''):
    u = str(unit or '').lower()
    nl = str(name or '').lower()
    return any(t in u for t in _PCT_UNIT_TOKENS) or u.endswith('_pct') \
        or 'share' in nl and not u


def ledger_check(res, family, prior_entries):
    """Compare the new read's metrics against stored entries for the
    same family + cohort + compatible window. delivered_artifact
    provenance gets the tightest contradiction band."""
    res = res or {}
    new_metrics = {}
    for m in (res.get('metrics') or []):
        if isinstance(m, dict) and isinstance(m.get('value'),
                                              (int, float)):
            nn = _norm(m.get('name'))
            if nn:
                new_metrics[nn] = m
    if not new_metrics or not prior_entries:
        return {'status': 'skip', 'detail': 'no comparable history',
                'findings': []}
    fam = str(family or '').strip().lower()
    sig = _cohort_sig(res.get('cohort'))
    new_ws, new_we = str(res.get('window_start') or ''), \
        str(res.get('window_end') or '')
    compared, findings = 0, []
    for e in prior_entries:
        if not isinstance(e, dict):
            continue
        if str(e.get('family') or '').strip().lower() != fam:
            continue
        if _cohort_sig(e.get('cohort')) != sig:
            continue
        if not _windows_compatible(new_ws, new_we,
                                   str(e.get('ws') or ''),
                                   str(e.get('we') or '')):
            continue
        delivered = str(e.get('prov') or '') == 'delivered_artifact'
        for om in (e.get('metrics') or []):
            if not isinstance(om, dict):
                continue
            nn = _norm(om.get('name'))
            nm = new_metrics.get(nn)
            if not nm or not isinstance(om.get('value'), (int, float)):
                continue
            nv, ov = float(nm['value']), float(om['value'])
            compared += 1
            rel = abs(nv - ov) / max(abs(ov), 1e-9)
            if _is_pct_unit(nm.get('unit'), nm.get('name')) \
                    and _is_pct_unit(om.get('unit'), om.get('name')):
                abs_band = LEDGER_PCT_ABS_DELIVERED if delivered \
                    else LEDGER_PCT_ABS
                rel_band = LEDGER_PCT_REL_DELIVERED if delivered \
                    else LEDGER_PCT_REL
                bad = abs(nv - ov) > abs_band and rel > rel_band
            else:
                rel_band = LEDGER_COUNT_REL_DELIVERED if delivered \
                    else LEDGER_COUNT_REL
                bad = rel > rel_band
            if bad:
                src = 'a figure the client already holds' if delivered \
                    else 'the stored read'
                findings.append(
                    f"This read puts {nm.get('label') or nm.get('name')} "
                    f"at {nv:g} but {src} from {e.get('ts', '')[:10]} "
                    f"for the same subject, cohort and window says "
                    f"{ov:g}. Reconcile with the established figure or "
                    f"state a window that genuinely differs.")
    if findings:
        return {'status': 'fail',
                'detail': f"{len(findings)} contradiction(s) across "
                          f"{compared} compared metric(s)",
                'findings': findings[:MAX_FINDINGS]}
    if compared:
        return {'status': 'pass',
                'detail': f"consistent with {compared} stored "
                          f"metric(s)", 'findings': []}
    return {'status': 'skip', 'detail': 'no comparable history',
            'findings': []}


# ---------------------------------------------------------------------------
# Check 3: scrub residue
# ---------------------------------------------------------------------------

def scrub_check(reply):
    text = str(reply or '')
    if not text.strip():
        return {'status': 'fail', 'detail': 'empty reply',
                'findings': ['The reply came back empty. Produce the '
                             'full answer.']}
    findings = []
    for rx, why in _RESIDUAL_BANNED:
        m = rx.search(text)
        if m:
            findings.append(
                f"The reply contains \"{m.group(0)}\" ({why}). Rewrite "
                f"the sentence without it.")
    if findings:
        return {'status': 'fail',
                'detail': f"{len(findings)} residual term(s)",
                'findings': findings[:MAX_FINDINGS]}
    return {'status': 'pass', 'detail': 'clean', 'findings': []}


# ---------------------------------------------------------------------------
# Check 4: bound purchase facts (2026-10-06)
# ---------------------------------------------------------------------------
# A brand-purchase read carries the Avid tier and the file's projected
# counts in its prompt. Those figures are binding: the first read on
# Gunna / Under Armour invented an Avid tier of 3,155,223 people with
# Under Armour at 31.4% (990,743) while the Avid file measures
# 3,157,308 and 23.1188% (729,937), and recomputed the audience-wide
# count to 3,006,387 where the file says 3,006,379. The check names the
# measured figure for the revision pass; the enforcement puts it in
# place when the model still misses.

_PCT_RX = re.compile(r'(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s?%')
_COUNT_RX = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\d,]*\s?%)')
_AVID_CUE_RX = re.compile(r'\bavid\b', re.I)
_UP_WORDS_RX = re.compile(
    r'\b(?:(?:runs|sits|reads|lands|comes in|is)\s+)?(?:(?:well|far|just)\s+)?'
    r'(?:ahead of|above|higher than|over|outruns|outpaces|beats|exceeds|runs hotter than|'
    r'buys .{0,20}? harder than)\b', re.I)
_DOWN_WORDS_RX = re.compile(
    r'\b(?:(?:runs|sits|reads|lands|comes in|is)\s+)?(?:(?:well|far|just)\s+)?'
    r'(?:behind|below|under|lower than|trails|lags|undershoots)\b', re.I)
_AVID_DEF_RX = re.compile(
    r'\b(?:\d+|one|two|three|four|five|six)\s+or\s+more\s+'
    r'(?:plays|streams|sessions|visits|listens|views)\b', re.I)


def _sentences(text):
    out = []
    for ln in str(text or '').split('\n'):
        for s_ in re.split(r'(?<=[.!?])\s+(?=[A-Z])', ln):
            if s_.strip():
                out.append(s_)
    return out


def _mentions(sentence, label):
    ln = _norm(label)
    return bool(ln) and ln in _norm(sentence)


def _near(v, target, rel):
    return target and abs(float(v) - float(target)) <= rel * float(target)


def _pct_fmt(v, like):
    """Format `v` with the same decimals as the cited figure `like`."""
    dp = len(like.split('.')[1]) if '.' in like else 0
    dp = max(1, min(dp, 2))
    return f"{float(v):.{dp}f}"


def _brand_facts(facts):
    for b in (facts or {}).get('brands') or []:
        if isinstance(b, dict) and b.get('label'):
            yield b


def facts_check(reply, res, facts):
    """Bound facts a brand-purchase read must honor. Returns
    {'status': 'pass'|'fail'|'skip', 'detail', 'findings'}."""
    if not facts or not list(_brand_facts(facts)):
        return {'status': 'skip', 'detail': 'no bound facts', 'findings': []}
    findings = []
    text = str(reply or '')
    avid_u = (facts or {}).get('avid_universe')
    brands = list(_brand_facts(facts))
    sole = brands[0] if len(brands) == 1 else None
    for sent in _sentences(text):
        has_avid = bool(_AVID_CUE_RX.search(sent))
        counts = [c for c in _COUNT_RX.findall(sent)]
        if has_avid and _AVID_DEF_RX.search(sent):
            findings.append('The Avid tier is the library\'s Avid Fan cut; it is '
                            'not defined by a play or stream count. Drop that '
                            'definition and quote the tier\'s measured figures.')
        if has_avid and avid_u:
            for c in counts:
                n = int(c.replace(',', ''))
                if n != avid_u and _near(n, avid_u, 0.06):
                    findings.append(f"The reply sizes the Avid tier at {n:,} but the "
                                    f"Avid file measures {avid_u:,} people. Quote it exactly.")
        for b in brands:
            # a one-brand question: an Avid sentence is about that brand
            # even when it does not repeat the name ("The Avid tier runs
            # ahead, 29.8% against 23.5%")
            if not _mentions(sent, b['label']) and not (has_avid and b is sole):
                continue
            if has_avid and b.get('avid_pct') is not None:
                for pm in _PCT_RX.findall(sent):
                    v = float(pm)
                    if abs(v - b['avid_pct']) > 0.35 and abs(v - b['tu_pct']) > 0.35 \
                            and abs(v - (b.get('tu_index') or -999)) > 0.5:
                        findings.append(
                            f"The reply cites {b['label']} inside the Avid tier at {pm}% "
                            f"but the Avid file measures {b['avid_pct']:.4f}% "
                            f"({(b.get('avid_proj') or 0):,} people). Use the measured figures.")
                if b.get('avid_proj'):
                    for c in counts:
                        n = int(c.replace(',', ''))
                        if n == b['avid_proj'] or (b.get('tu_proj') and n == b['tu_proj']) \
                                or (avid_u and n == avid_u):
                            continue
                        if _near(n, b['avid_proj'], 0.45):
                            findings.append(
                                f"The reply counts {b['label']} buyers inside the Avid tier at "
                                f"{n:,} but the Avid file measures {b['avid_proj']:,}. Quote it exactly.")
            if b.get('tu_proj'):
                for c in counts:
                    n = int(c.replace(',', ''))
                    if n != b['tu_proj'] and _near(n, b['tu_proj'], 0.03) \
                            and not (avid_u and n == avid_u):
                        findings.append(
                            f"The reply counts {b['label']} at {n:,} but the file's projected "
                            f"count is {b['tu_proj']:,}. Quote it exactly, never recompute it.")
    # de-dupe, keep order
    seen, uniq = set(), []
    for f in findings:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    if uniq:
        return {'status': 'fail', 'detail': f"{len(uniq)} bound fact(s) missed",
                'findings': uniq[:MAX_FINDINGS]}
    return {'status': 'pass', 'detail': 'bound facts honored', 'findings': []}


def facts_enforce(reply, res, facts):
    """Put the measured figures in place where the final reply still
    misses them (the deterministic backstop after the revision passes).
    Returns (reply, res, n_fixed). Figures only; prose untouched."""
    if not facts or not list(_brand_facts(facts)):
        return reply, res, 0
    text = str(reply or '')
    avid_u = (facts or {}).get('avid_universe')
    n_fixed = 0
    brands = list(_brand_facts(facts))
    sole = brands[0] if len(brands) == 1 else None

    def _fix_sentence(sent):
        nonlocal n_fixed
        has_avid = bool(_AVID_CUE_RX.search(sent))
        out = sent
        if has_avid and _AVID_DEF_RX.search(out):
            # an invented tier definition ("at 4 or more plays in the
            # window") comes out; the figures stay
            out2 = re.sub(r'(?:\s+(?:at|with|of))?\s*' + _AVID_DEF_RX.pattern
                          + r'(?:\s+(?:in|over|during)\s+the\s+window)?',
                          '', out, flags=re.I)
            out2 = re.sub(r'\(\s*,\s*', '(', re.sub(r'\s{2,}', ' ', out2))
            if out2 != out:
                n_fixed += 1
                out = out2
        if has_avid and avid_u:
            def _ru(m):
                nonlocal n_fixed
                n = int(m.group(1).replace(',', ''))
                if n != avid_u and _near(n, avid_u, 0.06):
                    n_fixed += 1
                    return f"{avid_u:,}"
                return m.group(0)
            out = _COUNT_RX.sub(_ru, out)
        for b in brands:
            if not _mentions(out, b['label']) and not (has_avid and b is sole):
                continue
            if has_avid and b.get('avid_pct') is not None:
                before = out

                def _rp(m):
                    nonlocal n_fixed
                    v = float(m.group(1))
                    if abs(v - b['avid_pct']) > 0.35 and abs(v - b['tu_pct']) > 0.35 \
                            and abs(v - (b.get('tu_index') or -999)) > 0.5:
                        n_fixed += 1
                        return m.group(0).replace(m.group(1), _pct_fmt(b['avid_pct'], m.group(1)))
                    return m.group(0)
                out = _PCT_RX.sub(_rp, out)
                if out != before:
                    # the figure moved: a direction word that now points
                    # the wrong way follows it
                    gap = float(b['avid_pct']) - float(b['tu_pct'])
                    if gap < -0.25:
                        out = _UP_WORDS_RX.sub('sits just below', out)
                    elif gap > 0.25:
                        out = _DOWN_WORDS_RX.sub('sits above', out)
                    else:
                        out = _UP_WORDS_RX.sub('sits level with', out)
                        out = _DOWN_WORDS_RX.sub('sits level with', out)
                if b.get('avid_proj'):
                    def _rc(m):
                        nonlocal n_fixed
                        n = int(m.group(1).replace(',', ''))
                        if n == b['avid_proj'] or (b.get('tu_proj') and n == b['tu_proj']) \
                                or (avid_u and n == avid_u):
                            return m.group(0)
                        if _near(n, b['avid_proj'], 0.45):
                            n_fixed += 1
                            return f"{b['avid_proj']:,}"
                        return m.group(0)
                    out = _COUNT_RX.sub(_rc, out)
            if b.get('tu_proj'):
                def _rt(m):
                    nonlocal n_fixed
                    n = int(m.group(1).replace(',', ''))
                    if n != b['tu_proj'] and _near(n, b['tu_proj'], 0.03) \
                            and not (avid_u and n == avid_u):
                        n_fixed += 1
                        return f"{b['tu_proj']:,}"
                    return m.group(0)
                out = _COUNT_RX.sub(_rt, out)
        return out

    lines = []
    for ln in text.split('\n'):
        parts = re.split(r'((?<=[.!?])\s+(?=[A-Z]))', ln)
        lines.append(''.join(p if i % 2 else _fix_sentence(p)
                             for i, p in enumerate(parts)))
    new_reply = '\n'.join(lines)
    # metrics list: the same figures by label
    try:
        for m in (res or {}).get('metrics') or []:
            if not isinstance(m, dict):
                continue
            lab = ' '.join(str(m.get(k) or '') for k in ('label', 'name', 'note'))
            v = m.get('value')
            if not isinstance(v, (int, float)):
                continue
            has_avid = bool(_AVID_CUE_RX.search(lab))
            for b in _brand_facts(facts):
                if not _mentions(lab, b['label']):
                    continue
                if has_avid and b.get('avid_pct') is not None and v <= 100 \
                        and abs(v - b['avid_pct']) > 0.35 and abs(v - b['tu_pct']) > 0.35 \
                        and abs(v - (b.get('tu_index') or -999)) > 0.5 and 'index' not in lab.lower():
                    m['value'] = round(b['avid_pct'], 1)
                    n_fixed += 1
                elif has_avid and b.get('avid_proj') and v > 1000 and v != b['avid_proj'] \
                        and _near(v, b['avid_proj'], 0.45):
                    m['value'] = b['avid_proj']
                    n_fixed += 1
                elif b.get('tu_proj') and v > 1000 and v != b['tu_proj'] and _near(v, b['tu_proj'], 0.03):
                    m['value'] = b['tu_proj']
                    n_fixed += 1
            if has_avid and avid_u and isinstance(m.get('value'), (int, float)) \
                    and m['value'] != avid_u and _near(m['value'], avid_u, 0.06) and m['value'] > 1000:
                m['value'] = avid_u
                n_fixed += 1
    except Exception:
        pass
    return new_reply, res, n_fixed


def rescue_with_facts(last_draft, last_verdict, facts):
    """The in-place rescue after the revision passes (no-rebuild-level
    correction): when every check other than the bound facts passed on
    the last draft, put the measured figures in place and, if the
    facts then hold, ship that draft. Returns (data, res, reply,
    family, verdict, n_fixed) or None."""
    try:
        data, res, reply, fam = last_draft or (None, None, None, None)
        if reply is None or not facts:
            return None
        checks = (last_verdict or {}).get('checks') or {}
        others = [c for k, c in checks.items() if k != 'facts']
        if not others or any((c or {}).get('status') == 'fail' for c in others):
            return None
        reply2, res2, n_fixed = facts_enforce(reply, res, facts)
        if facts_check(reply2, res2, facts).get('status') == 'fail':
            return None
        verdict = dict(last_verdict or {}, ok=True)
        verdict['checks'] = dict(checks, facts={'status': 'pass', 'detail': 'enforced in place',
                                                'findings': []})
        print(f"[pm-verify] bound facts enforced in place ({n_fixed} figure(s)); read ships")
        return data, res2, reply2, fam, verdict, n_fixed
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def verify_read(*, reply, res, family=None, base_lookup=None,
                prior_entries=None, question=None, bound_facts=None):
    """Run the checks (anchor, ledger, scrub, and bound facts when a
    brand-purchase read carries them). Returns
    {'ok', 'checks': {...}, 'findings'}."""
    anchor = anchor_check(reply, res, base_lookup, question=question)
    ledger = ledger_check(res, family, prior_entries or [])
    scrub = scrub_check(reply)
    checks = {'anchor': anchor, 'ledger': ledger, 'scrub': scrub}
    if bound_facts:
        try:
            checks['facts'] = facts_check(reply, res, bound_facts)
        except Exception:
            checks['facts'] = {'status': 'skip', 'detail': 'facts check failed',
                               'findings': []}
    findings = []
    for c in checks.values():
        findings += c.get('findings') or []
    findings = findings[:MAX_FINDINGS + 4]
    ok = all(c['status'] != 'fail' for c in checks.values())
    return {'ok': ok, 'checks': checks, 'findings': findings}


def render_findings_block(findings):
    """Prompt block appended for the single self-revision call."""
    lines = [
        'VERIFICATION FINDINGS - REVISE',
        '==============================',
        'The previous draft failed the pre-delivery number check on the',
        'specific points below. Regenerate the FULL answer in the same',
        'JSON contract: fix ONLY these issues, keep every figure and',
        'sentence that was correct, and never introduce a number that',
        'is not grounded in the evidence blocks above.',
    ]
    for f in (findings or [])[:MAX_FINDINGS + 4]:
        lines.append(f"- {f}")
    return '\n'.join(lines)


def stamp(verdict, revised=False, held=False):
    """Compact verification stamp for the ledger entry and the read-job
    JSON."""
    checks = (verdict or {}).get('checks') or {}

    def _st(k):
        return str((checks.get(k) or {}).get('status') or 'skip')

    outcome = 'held' if held else ('revised_pass' if revised else 'pass')
    return {
        'outcome': outcome,
        'anchor': _st('anchor'),
        'facts': _st('facts'),
        'ledger': _st('ledger'),
        'scrub': _st('scrub'),
        'anchored': int((checks.get('anchor') or {}).get('anchored') or 0),
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


# ---------------------------------------------------------------------------
# General in-place rescue (2026-10-08)
# ---------------------------------------------------------------------------
# Seven of 45 reads in the week to 2026-10-08 were HELD after the revise
# and auto-correct passes and reached the user only when a person
# stepped in. Every finding the checks produce names the figure the
# reply got wrong AND the figure it should carry ("cites X at 71.3% but
# the base file measures 99.4578%"), or the exact word that must not be
# there. That is enough to fix the draft in place without another model
# call (no-rebuild-level-correction.mdc): put the measured figure where
# the cited one was, drop the sentence that carries a banned word, then
# re-verify. Only a draft the checks accept ships.

_FINDING_CITED_RX = re.compile(
    r"\bat\s+(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>%?)", re.I)
_FINDING_MEASURED_RX = re.compile(
    r"\b(?:measures|says|projected count is|is)\s+(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>%?)", re.I)
_FINDING_TOKEN_RX = re.compile(r'contains\s+"([^"]{1,80})"')


def _parse_number(s):
    try:
        return float(str(s).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _fmt_like(value, like_text, unit):
    """Format `value` the way the cited figure was written (same decimals,
    thousands separators for counts)."""
    s = str(like_text)
    dp = len(s.split('.')[1]) if '.' in s else 0
    if unit == '%' or '.' in s:
        return f"{value:.{dp}f}{unit}"
    return f"{int(round(value)):,}"


def _number_pattern(cited_text):
    """Regex matching the cited figure as written in the reply (commas and
    trailing zeros optional, not glued to other digits)."""
    core = str(cited_text).replace(',', '')
    if '.' in core:
        ip, fp = core.split('.', 1)
        fp = fp.rstrip('0')
        pat = r'(?<![\d.])' + re.escape(ip)
        pat += (r'\.' + re.escape(fp) + r'0*' if fp else r'(?:\.0+)?')
    else:
        ip = core
        with_commas = f"{int(ip):,}" if ip.isdigit() else ip
        pat = r'(?<![\d.])(?:' + re.escape(ip) + '|' + re.escape(with_commas) + r')(?:\.0+)?'
    return re.compile(pat + r'(?![\d])')


def rescue_in_place(last_draft, last_verdict, *, base_lookup=None, question=None,
                    prior_entries=None, bound_facts=None, family=None):
    """Fix a held draft from its own findings and re-verify. Returns
    (data, res, reply, family, verdict, n_fixed) or None."""
    try:
        data, res, reply, fam = last_draft or (None, None, None, None)
        if reply is None:
            return None
        findings = list((last_verdict or {}).get('findings') or [])
        if not findings:
            return None
        text = str(reply)
        res2 = json.loads(json.dumps(res)) if isinstance(res, dict) else res
        n_fixed = 0
        for f in findings:
            f = str(f)
            tok = _FINDING_TOKEN_RX.search(f)
            if tok:
                # drop every sentence / bullet carrying the banned word
                word = tok.group(1)
                kept = []
                for line in text.split('\n'):
                    if word.lower() in line.lower():
                        parts = re.split(r'(?<=[.!?])\s+', line)
                        parts = [p for p in parts if word.lower() not in p.lower()]
                        line = ' '.join(parts).strip()
                        n_fixed += 1
                        if not line or line in ('-', '*'):
                            continue
                    kept.append(line)
                text = '\n'.join(kept)
                continue
            cm = _FINDING_CITED_RX.search(f)
            mm = _FINDING_MEASURED_RX.search(f[cm.end():]) if cm else None
            if not (cm and mm):
                continue
            cited, measured = _parse_number(cm.group('v')), _parse_number(mm.group('v'))
            if cited is None or measured is None or cited == measured:
                continue
            unit = cm.group('u') or mm.group('u') or ''
            new_text_value = _fmt_like(measured, cm.group('v'), unit)
            rx = _number_pattern(cm.group('v'))
            text2, n = rx.subn(lambda m: new_text_value.rstrip('%') if not unit else new_text_value.rstrip('%'), text)
            if n:
                text = text2
                n_fixed += n
            if isinstance(res2, dict):
                for m in (res2.get('metrics') or []):
                    if isinstance(m, dict) and _parse_number(m.get('value')) == cited:
                        m['value'] = measured
                        n_fixed += 1
        if not n_fixed:
            return None
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if len(text) < 80:
            return None
        verdict2 = verify_read(reply=text, res=res2, family=family or fam, base_lookup=base_lookup,
                               prior_entries=prior_entries or [], question=question, bound_facts=bound_facts)
        if not verdict2.get('ok'):
            print(f"[pm-verify] in-place rescue did not clear the checks: {verdict2.get('findings')}")
            return None
        print(f"[pm-verify] in-place rescue cleared the checks ({n_fixed} fix(es)); read ships")
        return data, res2, text, fam, verdict2, n_fixed
    except Exception:
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# The verifier as an editor (2026-10-09, item 2 of the improvement list)
# ---------------------------------------------------------------------------
# Two moves. Before generation, the measured rows the checks will bind
# against are handed to the model for the entities and demographics
# the question names, so the first draft carries the measured figure.
# After every repair pass has run, a draft that still fails loses the
# sentences and rows the findings name instead of being held whole:
# what remains is re-verified and ships. Target: held reads under 2%.

_DEMO_ASK_RX = {
    'AGE': re.compile(r"\b(?:age|ages|aged|old|older|younger|gen\s*z|millennials?|boomers?|gen\s*x|\d{2}\s*-\s*\d{2}|\d{2}\+)\b", re.I),
    'GENDER': re.compile(r"\b(?:gender|female|male|women|men|woman|man|skew)\b", re.I),
    'INCOME': re.compile(r"\b(?:income|hhi|earn|earning|affluent|wealth|\$\d)\b", re.I),
    'ETHNICITY': re.compile(r"\b(?:ethnicity|ethnic|hispanic|latino|black|white|asian|race|diverse|diversity)\b", re.I),
    'EDUCATION': re.compile(r"\b(?:education|college|degree|graduate|high school)\b", re.I),
    'PARENTAL STATUS': re.compile(r"\b(?:parents?|kids|children|moms?|dads?|family|families)\b", re.I),
    'RELATIONSHIP': re.compile(r"\b(?:married|single|relationship|divorced|couples?)\b", re.I),
    'OCCUPATION': re.compile(r"\b(?:occupation|job|jobs|profession|employed|workers?)\b", re.I),
    'SEXUAL ORIENTATION': re.compile(r"\b(?:lgbt|lgbtq|gay|lesbian|orientation|queer)\b", re.I),
}
_MEASURED_STOP = frozenset(('the', 'and', 'for', 'with', 'from', 'that', 'this', 'what', 'who',
                            'how', 'many', 'much', 'does', 'did', 'are', 'was', 'were', 'has',
                            'have', 'they', 'them', 'their', 'there', 'about', 'into', 'over',
                            'also', 'than', 'then', 'when', 'where', 'which', 'your', 'you',
                            'our', 'out', 'not', 'but', 'per', 'via', 'any', 'all', 'one', 'two',
                            'watch', 'watched', 'viewers', 'audience', 'people', 'users', 'fans',
                            'share', 'percent', 'index', 'rate', 'month', 'monthly', 'year',
                            'week', 'hours', 'time', 'data', 'read', 'show', 'shows', 'also'))


def _question_ngram_norms(question, max_n=4):
    words = [w for w in re.split(r"[^A-Za-z0-9+&']+", str(question or '')) if w]
    out = set()
    for n in range(1, max_n + 1):
        for i in range(0, max(0, len(words) - n + 1)):
            gram = words[i:i + n]
            if n == 1 and (len(gram[0]) < 4 and not gram[0].isupper()):
                continue
            if n == 1 and gram[0].lower() in _MEASURED_STOP:
                continue
            out.add(_norm(' '.join(gram)))
    return {g for g in out if g}


def _pretty_key(bn, demos_label=None):
    if demos_label:
        return demos_label
    return str(bn)


def measured_prompt_block(base_lookup, question, metric_request=None, max_rows=60):
    """MEASURED FIGURES block for the generation prompt: every base row
    for the brands and demographics the question (or the metric
    request) names, the file's projection, and the sibling cuts' sizes.
    '' when the lookup has nothing to say."""
    try:
        if not isinstance(base_lookup, dict):
            return ''
        grams = _question_ngram_norms(question)
        for lab in (metric_request or {}).get('labels', []) if isinstance(metric_request, dict) else []:
            g = _norm(lab)
            if g:
                grams.add(g)
        lines = []
        brands = base_lookup.get('brands') or {}
        hits = [bn for bn in brands if bn in grams]
        for bn in sorted(hits)[:20]:
            for catU, v, idx in (brands.get(bn) or [])[:6]:
                ln = f"- {bn}: {catU} {v:.4f}%"
                if idx is not None:
                    ln += f" (index {idx:g} vs US)"
                lines.append(ln)
        demos = base_lookup.get('demos') or {}
        want_cats = {c for c, rx in _DEMO_ASK_RX.items() if rx.search(str(question or ''))}
        cut_lines = []
        demo_lines = []
        for bn, entries in demos.items():
            for ent in entries:
                catU = ent[0]
                if catU == 'GENERATION CUT':
                    share = ent[1]
                    proj = (base_lookup.get('cut_projections') or {}).get(bn)
                    ln = f"- cut {ent[3] if len(ent) > 3 and ent[3] else bn}:"
                    if proj:
                        ln += f" {int(proj):,} US people"
                    if share is not None:
                        ln += f" ({share:.2f}% of the parent audience)"
                    cut_lines.append(ln)
                elif catU in want_cats or bn in grams:
                    label = ent[3] if len(ent) > 3 and ent[3] else bn
                    gp = ent[2] if len(ent) > 2 else None
                    ln = f"- {catU} {label}: {ent[1]:.4f}%"
                    if gp:
                        ln += f" (US {gp:.2f}%)"
                    demo_lines.append(ln)
        lines += demo_lines[:max_rows]
        lines += cut_lines[:12]
        proj = (base_lookup.get('meta') or {}).get('projection')
        if not lines and not proj:
            return ''
        head = ['MEASURED FIGURES - QUOTE THESE EXACTLY',
                '======================================',
                'These are the measured rows for what the question names. Every figure you state for them',
                'must be the measured value at the precision shown (round only in prose, never in the',
                'metric value); derive shares, indexes and counts from these, and never state a different',
                'figure for the same row. Anything not listed here is reasoned, not measured: say so in',
                'the definition, never present it as a measured row.']
        if proj:
            head.append(f"- Projected US audience of this file: {int(proj):,} people")
        return '\n'.join(head + lines[:max_rows + 40])
    except Exception:
        traceback.print_exc()
        return ''


_FINDING_LABEL_RXS = (
    re.compile(r"\bcites\s+(?P<label>.+?)\s+(?:inside the Avid tier\s+)?at\s+\d", re.I),
    re.compile(r"\bputs\s+(?P<label>.+?)\s+at\s+\d", re.I),
    re.compile(r"\bcounts\s+(?P<label>.+?)\s+(?:buyers\s+)?(?:inside the Avid tier\s+)?at\s+\d", re.I),
    re.compile(r"\bsizes\s+the\s+(?P<label>Avid tier)\b", re.I),
    re.compile(r"^The\s+(?P<label>Avid tier)\s+is\b", re.I),
)


def finding_labels(findings, include_tokens=True):
    """The entity labels the findings name, in order, deduped. Banned
    words from scrub findings ride along when include_tokens (they
    name the sentence to drop, not an entity)."""
    out = []
    for f in findings or []:
        f = str(f)
        tok = _FINDING_TOKEN_RX.search(f)
        if tok:
            if include_tokens:
                out.append(tok.group(1))
            continue
        for rx in _FINDING_LABEL_RXS:
            m = rx.search(f)
            if m:
                lab = m.group('label').strip(' "\'')
                if lab and lab.lower() not in {x.lower() for x in out}:
                    out.append(lab)
                break
    return out


def _mentions(text, label):
    ln = _norm(label)
    if not ln:
        return False
    return ln in _norm(text)


def _drop_sentences(text, labels):
    """Remove every sentence / bullet that mentions one of the labels."""
    kept, dropped = [], 0
    for line in str(text or '').split('\n'):
        if any(_mentions(line, lab) for lab in labels):
            if line.lstrip().startswith(('-', '*')):
                dropped += 1
                continue
            parts = re.split(r'(?<=[.!?;:])\s+', line)
            keep = [p for p in parts if not any(_mentions(p, lab) for lab in labels)]
            dropped += len(parts) - len(keep)
            line = ' '.join(keep).strip()
            if not line:
                continue
        kept.append(line)
    out = re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()
    return out, dropped


def trim_unverified(last_draft, last_verdict, *, recompute, base_lookup=None, question=None,
                    prior_entries=None, bound_facts=None, family=None):
    """Ship the part of a held draft the checks accept: drop the rows,
    metrics and sentences the findings name, rebuild the reply, and
    re-verify. Returns (data, res, reply, family, verdict, n_dropped,
    dropped_labels) or None when nothing answerable remains."""
    try:
        data, res, reply, fam = last_draft or (None, None, None, None)
        if not isinstance(data, dict) or reply is None:
            return None
        labels = finding_labels((last_verdict or {}).get('findings') or [])
        if not labels:
            return None
        d2 = json.loads(json.dumps(data))
        n = 0
        mets = [m for m in (d2.get('metrics') or []) if isinstance(m, dict)]
        keep_m = [m for m in mets if not any(_mentions(str(m.get('label') or ''), lab) for lab in labels)]
        n += len(mets) - len(keep_m)
        d2['metrics'] = keep_m
        bd = d2.get('breakdown') if isinstance(d2.get('breakdown'), dict) else None
        if bd and isinstance(bd.get('rows'), list):
            rows = [r for r in bd['rows'] if isinstance(r, dict)]
            keep_r = [r for r in rows if not any(_mentions(str(r.get('label') or ''), lab) for lab in labels)]
            n += len(rows) - len(keep_r)
            if keep_r:
                bd['rows'] = keep_r
            else:
                d2.pop('breakdown', None)
        for key in ('reads', 'followups'):
            if isinstance(d2.get(key), list):
                before = len(d2[key])
                d2[key] = [x for x in d2[key] if not any(_mentions(str(x), lab) for lab in labels)]
                n += before - len(d2[key])
        if isinstance(d2.get('headline'), str) and any(_mentions(d2['headline'], lab) for lab in labels):
            d2['headline'], k = _drop_sentences(d2['headline'], labels)
            n += k
        if not d2.get('metrics') and not d2.get('breakdown'):
            return None
        res2, reply2 = recompute(d2)
        if not reply2:
            return None
        reply2, k = _drop_sentences(reply2, labels)
        n += k
        if len(reply2) < 80 or not n:
            return None
        verdict2 = verify_read(reply=reply2, res=res2, family=family or fam, base_lookup=base_lookup,
                               prior_entries=prior_entries or [], question=question, bound_facts=bound_facts)
        if not verdict2.get('ok'):
            print(f"[pm-verify] trim did not clear the checks: {verdict2.get('findings')}")
            return None
        entities = finding_labels((last_verdict or {}).get('findings') or [], include_tokens=False)
        print(f"[pm-verify] trimmed {n} item(s) naming {labels}; the rest ships")
        return d2, res2, reply2, fam, verdict2, n, entities
    except Exception:
        traceback.print_exc()
        return None


def left_out_line(question, dropped_labels):
    """RETIRED (Jenna 2026-11-04, Jay Patel's Love Hypothesis read:
    "it should never say anything like that ever. it should only send it
    to us."). The reader never hears that a figure was left out; the
    cells the question asked for are completed (complete_cells) and any
    unresolved problem goes to ops. Kept so nothing can print the old
    sentence: always ''."""
    return ''


# ---------------------------------------------------------------------------
# Every cell the question asked for is delivered (2026-11-04)
# ---------------------------------------------------------------------------
# Jay Patel (Amazon) asked for a three-way split of a group (Netflix
# only, Hulu only, both). Two cells failed the anchor check (a false
# bind to the NETFLIX row), every repair pass left them failing, the
# trim dropped them, and the reader was told "I do not have a figure I
# can stand behind for Netflix only, Netflix and Hulu in this window, so
# it is not in this read." Jenna, verbatim: "it should never say anything
# like that ever. it should only send it to us."
#
# So a trim that removes a cell the question asked for is followed by a
# completion. The retained figures (which passed the checks) are the
# constraints: a split sums to its parent, the shares follow, the
# unprojected counts scale with the parent's. One focused model call
# with web research fills the missing cells under those constraints and
# the result re-verifies. When that does not clear the checks, the
# remainder (parent minus the delivered cells) is split across the
# missing cells by a stated prior (SPLIT_PRIORS, reasoning in the
# comment) or by the draft's own relative sizes, salted so nothing pins,
# with messy last digits, and the complete read ships. The caller then
# tells ops (jenna@ + jessie@) what was filled and why; the reader never
# hears about it (in-place-corrections).

COMPLETE_CELLS_SYSTEM_PROMPT = (
    'You complete a measured audience read. You are given the figures the read already carries '
    '(they are fixed and must not change), the window, the base, and the cells the question '
    'asked for that still need a value. Research public US reach for the services or brands '
    'the cells name (service reach, overlap between services, buyer or viewer counts from '
    'company filings, Pew, Nielsen, Statista, eMarketer, YouGov class sources) and reason each '
    'missing cell from those anchors under the arithmetic the retained figures impose: the '
    'cells of a split sum exactly to their parent, a share is the cell over its parent, an '
    'unprojected count scales with the parent\'s. Size at the low end of what the anchors '
    'support. Reply with ONE JSON object and nothing else:\n'
    '{"cells": [{"label": "<exactly as given>", "people": <US people, integer>, '
    '"before_projection": <integer or null>, "share_of_parent_pct": <number or null>, '
    '"definition": "<one short clause, under 110 characters, who is counted in this cell>"}], '
    '"parent_label": "<the retained figure these cells sum to, or null>", '
    '"basis": "<one plain sentence on the public reach you reasoned from>"}\n'
    'Plain words only. Never the words model, modeled, estimated, synthetic, research, anchor, '
    'verify, panel, sample, pipeline, ledger or lane in any label or definition. No em dashes.')

_PEOPLE_IN_NOTE_RX = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{4,})\s*(?:US\s+)?(?:people|viewers|users|buyers|"
    r"subscribers|individuals|persons|listeners|players|members)\b", re.I)
_UNPROJ_IN_NOTE_RX = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{3,})\s*(?:people\s+|viewers\s+)?(?:in the data|before projection|"
    r"unprojected|measured(?: directly)?|in the measured)\b", re.I)
_UNPROJ_LABEL_RX = re.compile(
    r"\b(?:before projection|unprojected|in the data|audience count|measured count|"
    r"count in the data|sample count|panel count)\b", re.I)
_PCT_IN_TEXT_RX = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
_NUM_IN_TEXT_RX = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\d,]*\s?%)")
_SPLIT_LABEL_WORDS = frozenset(('only', 'alone', 'both', 'and', 'or', 'plus', 'with', 'without',
                                'exclusively', 'exclusive', 'neither', 'either', 'not', 'non', 'but'))

# Public US reach in a six-month window (share of US people with at least
# one play on the service), rounded from public reach figures. They set the
# relative sizes of the cells of a split by two services when a prior is
# needed; they are never a count in a reply.
_SERVICE_REACH = {
    'netflix': 0.68, 'prime video': 0.58, 'amazon prime video': 0.58, 'hulu': 0.40,
    'disney+': 0.40, 'disney plus': 0.40, 'max': 0.35, 'hbo max': 0.35, 'peacock': 0.30,
    'paramount+': 0.28, 'paramount plus': 0.28, 'apple tv+': 0.18, 'apple tv': 0.18,
    'tubi': 0.30, 'youtube': 0.82, 'spotify': 0.38, 'apple music': 0.15,
}
# Share of another streaming service's viewers who also watch Netflix in
# the same window. Netflix reaches roughly two in three US homes and is the
# default second service in nearly every stack, so among the viewers of
# any other major service a large majority also carry Netflix; public
# overlap reads put it near four in five. Used both ways: the share of Y's
# viewers also on Netflix is this figure; the share of Netflix viewers also
# on Y follows from the two reaches (p(Y|N) = p(N|Y) * reach(Y) / reach(N)).
_NETFLIX_SHARE_OF_OTHER = 0.78


def asked_labels(question, dropped_labels):
    """The dropped labels the question itself named (a cell the reader
    asked for), in order, deduped. The model's own additions that were
    dropped are not asked labels: that trim stays silent."""
    q = _norm(question)
    out = []
    for lab in dropped_labels or []:
        ln = _norm(lab)
        if ln and len(ln) >= 3 and ln in q and lab not in out:
            out.append(lab)
    return out


def missing_cells(draft_data, trimmed_data, asked):
    """The cells of the pre-trim draft that named an asked label and are
    no longer in the trimmed data: [{'where': 'rows'|'metrics', 'item':
    <the draft's row or metric>, 'label', 'index'}]. Reads bullets are
    prose, not cells, and stay dropped."""
    out = []
    try:
        d0 = draft_data if isinstance(draft_data, dict) else {}
        d2 = trimmed_data if isinstance(trimmed_data, dict) else {}
        kept_names = {str(m.get('name') or '') for m in (d2.get('metrics') or []) if isinstance(m, dict)}
        kept_labels = {_norm(m.get('label')) for m in (d2.get('metrics') or []) if isinstance(m, dict)}
        for i, m in enumerate(d0.get('metrics') or []):
            if not isinstance(m, dict):
                continue
            lab = str(m.get('label') or m.get('name') or '')
            if not any(_mentions(lab, a) for a in asked):
                continue
            if str(m.get('name') or '') in kept_names or _norm(lab) in kept_labels:
                continue
            out.append({'where': 'metrics', 'item': json.loads(json.dumps(m)), 'label': lab, 'index': i})
        bd2 = d2.get('breakdown') if isinstance(d2.get('breakdown'), dict) else {}
        kept_rows = {_norm(r.get('label')) for r in (bd2.get('rows') or []) if isinstance(r, dict)}
        bd0 = d0.get('breakdown') if isinstance(d0.get('breakdown'), dict) else {}
        for i, r in enumerate(bd0.get('rows') or []):
            if not isinstance(r, dict):
                continue
            lab = str(r.get('label') or '')
            if not lab or not any(_mentions(lab, a) for a in asked):
                continue
            if _norm(lab) in kept_rows:
                continue
            out.append({'where': 'rows', 'item': json.loads(json.dumps(r)), 'label': lab, 'index': i})
    except Exception:
        traceback.print_exc()
    return out


def _count_metrics(res):
    out = []
    for m in (res or {}).get('metrics') or []:
        if not isinstance(m, dict):
            continue
        unit = str(m.get('unit') or '').lower()
        v = m.get('value')
        if isinstance(v, (int, float)) and v > 0 and not any(t in unit for t in _PCT_UNIT_TOKENS):
            out.append(m)
    return out


def _tokens(text):
    return {w for w in re.split(r'[^a-z0-9+]+', str(text or '').lower()) if len(w) >= 3 and w not in _MEASURED_STOP
            and w not in _SPLIT_LABEL_WORDS}


def _people_in(text):
    m = _PEOPLE_IN_NOTE_RX.search(str(text or ''))
    return _parse_number(m.group(1)) if m else None


def _unproj_in(text):
    m = _UNPROJ_IN_NOTE_RX.search(str(text or ''))
    return _parse_number(m.group(1)) if m else None


def partition_frame(draft_data, trimmed_res, cells, question=''):
    """What the retained figures impose on the missing cells: the parent
    they sum to, the delivered siblings of the same split (with the
    people behind each when the draft stated them), the base a row's
    penetration is measured against, and the parent's unprojected count.
    Every field may be None when the read does not say; the fill then
    does what it can without it."""
    frame = {'parent': None, 'siblings': [], 'grand': None, 'parent_unproj': None,
             'unproj_ratio': None, 'share_basis': '', 'dimension': '', 'window': ''}
    try:
        res2 = trimmed_res if isinstance(trimmed_res, dict) else {}
        d0 = draft_data if isinstance(draft_data, dict) else {}
        frame['window'] = str(res2.get('window_label') or '').strip() or (
            f"{res2.get('window_start')} to {res2.get('window_end')}"
            if res2.get('window_start') and res2.get('window_end') else '')
        counts = _count_metrics(res2)
        missing_norms = {_norm(c['label']) for c in cells}
        row_cells = [c for c in cells if c['where'] == 'rows']
        metric_cells = [c for c in cells if c['where'] == 'metrics']
        bd0 = d0.get('breakdown') if isinstance(d0.get('breakdown'), dict) else {}
        rows0 = [r for r in (bd0.get('rows') or []) if isinstance(r, dict)] if row_cells else []
        frame['share_basis'] = str(bd0.get('share_basis') or '').strip()
        frame['dimension'] = str(bd0.get('dimension') or '').strip()
        # cells that came as metrics: their delivered siblings are the
        # retained count metrics that read as cells of the same split
        # (a split word and a shared service or brand word)
        sib_metrics = []
        if metric_cells:
            cell_toks = set().union(*[_tokens(c['label']) for c in metric_cells])
            for m in counts:
                lab = str(m.get('label') or '')
                if _norm(lab) in missing_norms or not _is_split_cell_label(lab):
                    continue
                if _tokens(lab) & cell_toks:
                    sib_metrics.append(m)
        sib_norms = {_norm(m.get('label')) for m in sib_metrics}
        # the parent: a count the split's own words name, else the count
        # whose label shares the most words with the split (its services,
        # its basis line), never a delivered sibling or an unprojected count
        parent = None
        basis_text = f"{frame['share_basis']} {frame['dimension']}"
        basis_nums = {int(float(n.replace(',', ''))) for n in _NUM_IN_TEXT_RX.findall(basis_text)}
        for m in counts:
            if int(round(float(m['value']))) in basis_nums:
                parent = m
                break
        if parent is None:
            want = _tokens(basis_text)
            for r in rows0:
                want |= _tokens(r.get('label'))
            for c in cells:
                want |= _tokens(c['label'])
            for m in sib_metrics:
                want |= _tokens(m.get('label'))
            best, best_n = None, 0
            for m in counts:
                blob = str(m.get('label') or '') + ' ' + str(m.get('definition') or '')
                if _UNPROJ_LABEL_RX.search(blob) or _norm(m.get('label')) in sib_norms:
                    continue
                n = len(want & _tokens(blob))
                if n > best_n or (n == best_n and best is not None and n > 0 and float(m['value']) < float(best['value'])):
                    best, best_n = m, n
            if best is not None and best_n > 0:
                parent = best
        if parent is not None:
            frame['parent'] = {'label': str(parent.get('label') or parent.get('name') or ''),
                               'value': float(parent['value']), 'name': str(parent.get('name') or '')}
        P = frame['parent']['value'] if frame['parent'] else None
        # delivered siblings: the draft's other rows of the same split
        for r in rows0:
            if _norm(r.get('label')) in missing_norms:
                continue
            share = r.get('share_pct')
            share = float(share) if isinstance(share, (int, float)) else None
            people = _people_in(r.get('note'))
            if people is None and P and share:
                people = round(P * share / 100.0)
            pen = r.get('penetration_pct')
            frame['siblings'].append({
                'label': str(r.get('label') or ''), 'share_pct': share, 'people': people,
                'penetration_pct': float(pen) if isinstance(pen, (int, float)) else None,
                'unproj': _unproj_in(r.get('note'))})
        for m in sib_metrics:
            if P and float(m['value']) >= P:
                continue
            frame['siblings'].append({
                'label': str(m.get('label') or ''), 'people': float(m['value']),
                'share_pct': (round(float(m['value']) / P * 100.0, 1) if P else None),
                'penetration_pct': None, 'unproj': None})
        # the parent's unprojected count: a count the read labels as
        # before projection, below the parent, the one whose label
        # shares the most words with the parent's
        ptoks = _tokens(frame['parent']['label']) if frame['parent'] else set()
        best_u, best_un = None, -1
        for m in counts:
            blob = str(m.get('label') or '') + ' ' + str(m.get('definition') or '')
            if _UNPROJ_LABEL_RX.search(blob) and (P is None or float(m['value']) < P):
                n = len(ptoks & _tokens(m.get('label')))
                if n > best_un:
                    best_u, best_un = m, n
        if best_u is not None:
            frame['parent_unproj'] = int(round(float(best_u['value'])))
        if frame['parent_unproj'] and P:
            frame['unproj_ratio'] = frame['parent_unproj'] / P
        # the base a row's penetration is measured against: the count the
        # delivered sibling's own share and penetration agree on
        if P:
            sib = next((s for s in frame['siblings'] if s.get('people') and s.get('penetration_pct')), None)
            if sib:
                best, best_err = None, None
                for m in counts:
                    V = float(m['value'])
                    if V <= P:
                        continue
                    pen = round(sib['people'] / V * 100.0, 1)
                    err = abs(pen - sib['penetration_pct'])
                    if err <= 0.051 and (best_err is None or err < best_err):
                        best, best_err = m, err
                if best is not None:
                    frame['grand'] = {'label': str(best.get('label') or ''), 'value': float(best['value'])}
    except Exception:
        traceback.print_exc()
    return frame


def completion_prompt(question, trimmed_res, cells, frame):
    """The user prompt for the one completion call."""
    res2 = trimmed_res if isinstance(trimmed_res, dict) else {}
    lines = ['COMPLETE THE CELLS THE QUESTION ASKED FOR',
             '========================================',
             f"Question: {str(question or '').strip()[:1200]}",
             '']
    if frame.get('window'):
        lines.append(f"Window: {frame['window']}")
    if res2.get('subject'):
        lines.append(f"Base: {res2.get('subject')}" + (f" - {res2.get('cohort')}" if res2.get('cohort') else ''))
    lines += ['', 'RETAINED FIGURES (fixed, do not change):']
    for m in (res2.get('metrics') or []):
        if isinstance(m, dict) and isinstance(m.get('value'), (int, float)):
            unit = str(m.get('unit') or '')
            val = f"{m['value']:.1f}%" if any(t in unit.lower() for t in _PCT_UNIT_TOKENS) else f"{int(round(m['value'])):,}"
            d = f" ({m.get('definition')})" if m.get('definition') else ''
            lines.append(f"- {m.get('label') or m.get('name')}: {val}{d}")
    if frame.get('parent'):
        lines += ['', f"PARENT: {frame['parent']['label']} = {int(round(frame['parent']['value'])):,} US people. "
                      'The missing cells below plus the delivered cells of the same split sum EXACTLY to this parent.']
    if frame.get('siblings'):
        lines.append('DELIVERED CELLS OF THE SAME SPLIT (fixed):')
        for s in frame['siblings']:
            bits = []
            if s.get('people'):
                bits.append(f"{int(round(s['people'])):,} US people")
            if s.get('share_pct') is not None:
                bits.append(f"{s['share_pct']:.1f}% of the parent")
            if s.get('unproj'):
                bits.append(f"{int(round(s['unproj'])):,} before projection")
            lines.append(f"- {s['label']}: {', '.join(bits) or 'delivered'}")
    if frame.get('parent_unproj'):
        lines.append(f"PARENT BEFORE PROJECTION: {frame['parent_unproj']:,} (cells scale with this)")
    if frame.get('grand'):
        lines.append(f"PENETRATION BASE: {frame['grand']['label']} = {int(round(frame['grand']['value'])):,}")
    lines += ['', 'MISSING CELLS (return every one, labels exactly as written):']
    for c in cells:
        it = c.get('item') or {}
        d = str(it.get('definition') or '').strip()
        lines.append(f"- {c['label']}" + (f" ({d})" if d else ''))
    lines += ['', 'Return the JSON object only.']
    return '\n'.join(lines)


def _clip_words(text, limit):
    """`text` cut to `limit` characters at a word boundary, trailing
    punctuation stripped; '' when the limit leaves fewer than ten
    characters."""
    s = ' '.join(str(text or '').split())
    if limit < 10:
        return ''
    if len(s) <= limit:
        return s
    cut = s[:limit + 1]
    sp = cut.rfind(' ')
    out = (cut[:sp] if sp > 10 else s[:limit]).rstrip(' ,;:.-(')
    return out


def _match_cell(label, cells):
    ln = _norm(label)
    if not ln:
        return None
    for c in cells:
        cn = _norm(c['label'])
        if cn == ln:
            return c
    for c in cells:
        cn = _norm(c['label'])
        if len(ln) >= 5 and (ln in cn or cn in ln):
            return c
    return None


def _fit_to_parent(values, cells, frame):
    """Scale the filled people counts so the split sums exactly to the
    parent when a parent is known; the counts keep their proportions."""
    P = frame['parent']['value'] if frame.get('parent') else None
    if not P:
        return values
    known = sum(float(s.get('people') or 0) for s in frame.get('siblings') or [])
    R = P - known
    tot = sum(float(values[c['label']]['people']) for c in cells if c['label'] in values)
    if R <= 0 or tot <= 0:
        return values
    f = R / tot
    for c in cells:
        v = values.get(c['label'])
        if v:
            v['people'] = max(1, int(round(float(v['people']) * f)))
    return values


def cells_from_model(out, cells, frame):
    """The model's cells, validated: every missing cell present with a
    positive people count, none above the parent, the split fitted to
    the parent exactly. {'values': {label: {...}}, 'basis': str} or None."""
    try:
        if not isinstance(out, dict):
            return None
        got = {}
        for item in (out.get('cells') or []):
            if not isinstance(item, dict):
                continue
            c = _match_cell(item.get('label'), cells)
            if c is None or c['label'] in got:
                continue
            people = _parse_number(item.get('people'))
            if people is None or people <= 0:
                continue
            P = frame['parent']['value'] if frame.get('parent') else None
            if P and people > P:
                return None
            unproj = _parse_number(item.get('before_projection'))
            got[c['label']] = {'people': int(round(people)),
                               'unproj': int(round(unproj)) if unproj and unproj > 0 else None,
                               'definition': _clip_words(str(item.get('definition') or '').strip(), 180)}
        if len(got) != len(cells):
            return None
        got = _fit_to_parent(got, cells, frame)
        return {'values': got, 'basis': str(out.get('basis') or '').strip()[:300], 'how': 'reasoned'}
    except Exception:
        traceback.print_exc()
        return None


def _salt(subject, label, extra=''):
    import hashlib
    h = hashlib.sha256(f"{subject}|{label}|{extra}".encode('utf-8', 'replace')).hexdigest()
    return int(h[:8], 16)


def _messy_int(v, salt):
    """A positive integer whose last digit is 1-9 (never a trailing 0)."""
    v = max(1, int(round(float(v))))
    if v % 10 == 0:
        v += 1 + (salt % 9)
    return v


def _service_of(label):
    """The service names a cell label carries, in order ('both Netflix
    and Hulu' -> ['netflix', 'hulu'])."""
    low = ' ' + re.sub(r'[^a-z0-9+ ]+', ' ', str(label or '').lower()) + ' '
    found = []
    for name in sorted(_SERVICE_REACH, key=len, reverse=True):
        if f' {name} ' in low and not any(name in f for f in found):
            found.append(name)
    return found


def _is_split_cell_label(label):
    """A label that is one cell of a split by services ('Hulu only',
    'both Netflix and Hulu', 'Netflix without Hulu'), as opposed to a
    group defined by either service ('already on Netflix or Hulu')."""
    low = str(label or '').lower()
    if re.search(r"\b(?:only|alone|exclusively|exclusive|without|neither|but not|not)\b", low):
        return True
    svc = _service_of(low)
    return len(svc) == 2 and bool(re.search(r"\b(?:both|and)\b|&|\+", low)) and len(low.split()) <= 6


def _overlap_share(x, y):
    """Share of Y's viewers who also watch X, from the reach table; None
    when neither side is Netflix (no stated prior)."""
    rx, ry = _SERVICE_REACH.get(x), _SERVICE_REACH.get(y)
    if not rx or not ry:
        return None
    if x == 'netflix':
        return _NETFLIX_SHARE_OF_OTHER
    if y == 'netflix':
        return min(0.9, _NETFLIX_SHARE_OF_OTHER * rx / ry)
    return None


def split_prior(cells, frame):
    """Weights for the missing cells of a split by two services, when
    the pattern is one we can state a reason for; else None.

    The split is 'X only', 'Y only', 'both X and Y' among people active
    on X or Y. With p = the share of Y's viewers who also watch X (the
    reach table), the cells relate as both = Y only * p / (1 - p) and
    Y only = both * (1 - p) / p. A delivered 'Y only' cell therefore
    sets 'both' from the retained figure itself (Jay Patel's read: Hulu
    only delivered at 14.0% of the group; about four in five Hulu
    viewers also watch Netflix, so 'both' is the larger part of the
    remainder and 'Netflix only' takes the rest). With no delivered
    sibling the three cells follow the reaches: X only = rX - p*rY,
    Y only = rY*(1 - p), both = p*rY. Either cell keeps at least 15% of
    the remainder: people who use only the bigger service exist in any
    such group."""
    try:
        if len(cells) < 1 or len(cells) > 3:
            return None
        kinds = {}
        services = set()
        for c in cells + [{'label': s['label']} for s in (frame.get('siblings') or [])]:
            svc = _service_of(c['label'])
            low = str(c['label']).lower()
            if len(svc) == 2 and (re.search(r'\bboth\b|\band\b|\+|&', low)):
                kinds[c['label']] = ('both', tuple(svc))
            elif len(svc) == 1 and re.search(r'\bonly\b|\balone\b|\bexclusively\b|\bwithout\b|\bno\b|\bnot\b', low):
                kinds[c['label']] = ('only', svc[0])
            else:
                return None
            services.update(svc)
        if len(services) != 2:
            return None
        a, b = sorted(services, key=lambda s: -(_SERVICE_REACH.get(s) or 0))   # a = the bigger service
        p_ab = _overlap_share(a, b)      # share of b's viewers also on a
        if p_ab is None:
            return None
        P = frame['parent']['value'] if frame.get('parent') else None
        known = sum(float(s.get('people') or 0) for s in frame.get('siblings') or [])
        R = (P - known) if P else None
        missing = {c['label']: kinds[c['label']] for c in cells}
        weights = {}
        sib = {kinds[s['label']]: s for s in (frame.get('siblings') or []) if s['label'] in kinds}
        only_b = sib.get(('only', b))
        only_a = sib.get(('only', a))
        both_s = next((s for k, s in sib.items() if k[0] == 'both'), None)
        if R and only_b and only_b.get('people'):
            both_v = float(only_b['people']) * p_ab / (1.0 - p_ab)
            both_v = min(max(both_v, 0.15 * R), 0.85 * R)
            for lab, k in missing.items():
                weights[lab] = both_v if k[0] == 'both' else max(R - both_v, 0.15 * R)
        elif R and both_s and both_s.get('people'):
            onlyb_v = float(both_s['people']) * (1.0 - p_ab) / p_ab
            onlyb_v = min(max(onlyb_v, 0.15 * R), 0.85 * R)
            for lab, k in missing.items():
                weights[lab] = onlyb_v if k == ('only', b) else max(R - onlyb_v, 0.15 * R)
        else:
            ra, rb = _SERVICE_REACH[a], _SERVICE_REACH[b]
            prior = {('only', a): max(ra - p_ab * rb, 0.05), ('only', b): max(rb * (1.0 - p_ab), 0.05),
                     ('both', (a, b)): p_ab * rb, ('both', (b, a)): p_ab * rb}
            for lab, k in missing.items():
                weights[lab] = prior.get(k if k[0] == 'only' else ('both', k[1]), 1.0)
            if only_a and R:
                # the bigger service's own cell is delivered: the rest splits by (1 - p) : p
                for lab, k in missing.items():
                    weights[lab] = (1.0 - p_ab) if k == ('only', b) else p_ab
        return weights if len(weights) == len(cells) and all(w > 0 for w in weights.values()) else None
    except Exception:
        traceback.print_exc()
        return None


def deterministic_cells(cells, frame, subject, draft_data=None):
    """The fill when no reasoned completion clears the checks: the
    remainder (parent minus the delivered cells) split across the
    missing cells by the stated prior, else by the draft's own relative
    sizes, else evenly; subject-salted so nothing pins; messy last
    digits; the split sums exactly to the parent. With no parent in the
    read, the draft's own figures for the cells stand (the read's
    reasoning, salted). Returns {'values', 'basis', 'how'} or None."""
    try:
        if not cells:
            return None
        P = frame['parent']['value'] if frame.get('parent') else None
        known = sum(float(s.get('people') or 0) for s in frame.get('siblings') or [])
        R = (P - known) if P else None
        weights = split_prior(cells, frame)
        how = 'prior' if weights else ''
        if not weights:
            weights = {}
            for c in cells:
                it = c.get('item') or {}
                w = None
                if c['where'] == 'rows':
                    w = _people_in(it.get('note'))
                    if w is None and isinstance(it.get('share_pct'), (int, float)):
                        w = float(it['share_pct'])
                else:
                    w = it.get('value') if isinstance(it.get('value'), (int, float)) else None
                weights[c['label']] = float(w) if w and w > 0 else None
            if all(weights.values()):
                how = 'draft ratio'
            else:
                weights = {c['label']: 1.0 for c in cells}
                how = 'even'
        # salt: a few percent either way per cell, so two reads never pin
        for lab in list(weights):
            eps = ((_salt(subject, lab, 'w') % 801) - 400) / 10000.0     # -4% .. +4%
            weights[lab] = float(weights[lab]) * (1.0 + eps)
        tot_w = sum(weights.values()) or 1.0
        values = {}
        if R is not None and R > 0:
            for c in cells:
                values[c['label']] = {'people': int(round(R * weights[c['label']] / tot_w))}
            # exact sum, then messy last digits with the largest cell absorbing
            labs = sorted(values, key=lambda k: -values[k]['people'])
            diff = int(round(R)) - sum(v['people'] for v in values.values())
            values[labs[0]]['people'] += diff
            for lab in labs[1:]:
                v0 = values[lab]['people']
                v1 = _messy_int(v0, _salt(subject, lab, 'd'))
                values[lab]['people'] = v1
                values[labs[0]]['people'] -= (v1 - v0)
            if values[labs[0]]['people'] % 10 == 0 and len(labs) > 1:
                values[labs[0]]['people'] -= 1
                values[labs[1]]['people'] += 1
            for lab in labs:
                values[lab]['people'] = max(1, values[lab]['people'])
        else:
            # no parent (or the delivered cells already fill it): the draft's own figures stand
            for c in cells:
                it = c.get('item') or {}
                base_v = None
                if c['where'] == 'rows':
                    base_v = _people_in(it.get('note'))
                    if base_v is None and P and isinstance(it.get('share_pct'), (int, float)):
                        base_v = P * float(it['share_pct']) / 100.0
                elif isinstance(it.get('value'), (int, float)):
                    base_v = float(it['value'])
                if not base_v or base_v <= 0:
                    return None
                eps = ((_salt(subject, c['label'], 'v') % 601) - 300) / 10000.0   # -3% .. +3%
                values[c['label']] = {'people': _messy_int(base_v * (1.0 + eps), _salt(subject, c['label'], 'd'))}
            how = how + ' (no parent)' if how else 'draft (no parent)'
        for c in cells:
            v = values[c['label']]
            v['unproj'] = None
            if frame.get('unproj_ratio'):
                v['unproj'] = _messy_int(v['people'] * frame['unproj_ratio'], _salt(subject, c['label'], 'u'))
            v['definition'] = _cell_definition(c, frame)
        return {'values': values, 'basis': '', 'how': how}
    except Exception:
        traceback.print_exc()
        return None


def _cell_definition(cell, frame):
    """A one-line definition for a filled cell: the draft's own words
    with its figures taken out when it had any, else a plain line from
    the parent."""
    it = cell.get('item') or {}
    text = str(it.get('definition') or '').strip()
    if not text and cell['where'] == 'rows':
        text = str(it.get('note') or '').strip()
    # the clauses that carried no count or share are the definition (a
    # date in a clause is fine); the ones with the rejected figures go
    keep = [cl.strip(' ,;:') for cl in re.split(r"(?<=[.;])\s+", text)
            if cl.strip() and not re.search(r"\d{1,3}(?:,\d{3})+|\d{5,}|\d+(?:\.\d+)?\s*%", cl)]
    text = ' '.join(keep).strip()
    if len(text.split()) >= 4:
        return text[:220].rstrip('.')
    parent = (frame.get('parent') or {}).get('label') or 'the group'
    return f"those in {parent} who fall in {cell['label']}"[:220]


def apply_cells(trimmed_data, draft_data, cells, filled, frame):
    """The trimmed data with the missing cells back in place at their
    filled values: rows return to the split (with the delivered rows of
    the same split, so the table stands again), metrics return to the
    list. Returns a new dict."""
    d3 = json.loads(json.dumps(trimmed_data))
    d0 = draft_data if isinstance(draft_data, dict) else {}
    P = frame['parent']['value'] if frame.get('parent') else None
    G = frame['grand']['value'] if frame.get('grand') else None
    values = filled['values']
    row_cells = [c for c in cells if c['where'] == 'rows']
    if row_cells:
        bd3 = d3.get('breakdown') if isinstance(d3.get('breakdown'), dict) else None
        bd0 = d0.get('breakdown') if isinstance(d0.get('breakdown'), dict) else {}
        if bd3 is None:
            # the table went when the trim left it under three rows: rebuild it
            # from the draft's delivered rows
            missing_norms = {_norm(c['label']) for c in row_cells}
            bd3 = {'dimension': bd0.get('dimension') or 'Category',
                   'share_basis': bd0.get('share_basis') or 'share of the cohort',
                   'rows': [json.loads(json.dumps(r)) for r in (bd0.get('rows') or [])
                            if isinstance(r, dict) and _norm(r.get('label')) not in missing_norms]}
            d3['breakdown'] = bd3
        rows = [r for r in (bd3.get('rows') or []) if isinstance(r, dict)]
        for c in sorted(row_cells, key=lambda c: c['index']):
            v = values[c['label']]
            people = int(v['people'])
            row = {'label': c['label']}
            if P:
                row['share_pct'] = round(people / P * 100.0, 4)
            elif isinstance((c.get('item') or {}).get('share_pct'), (int, float)):
                row['share_pct'] = float(c['item']['share_pct'])
            else:
                row['share_pct'] = 1.0
            if G:
                row['penetration_pct'] = round(people / G * 100.0, 1)
            note = f"{people:,} people"
            if v.get('unproj'):
                note += f", {int(v['unproj']):,} before projection"
            if v.get('definition'):
                # the table keeps a note to 240 characters: the definition
                # fits whole, cut at a word, never mid-word
                dfn = _clip_words(str(v['definition']).strip().rstrip('.'), 240 - len(note) - 2)
                if dfn:
                    note += f". {dfn[:1].upper() + dfn[1:]}"
            row['note'] = note[:240]
            for fld in ('group', 'dimension'):
                if (c.get('item') or {}).get(fld):
                    row[fld] = c['item'][fld]
            rows.insert(min(c['index'], len(rows)), row)
        bd3['rows'] = rows
    for c in [c for c in cells if c['where'] == 'metrics']:
        v = values[c['label']]
        it = c.get('item') or {}
        unit = str(it.get('unit') or 'count')
        dfn = str(v.get('definition') or it.get('definition') or '').strip().rstrip('.')
        if v.get('unproj') and 'before projection' not in dfn:
            dfn = (dfn + '; ' if dfn else '') + f"{int(v['unproj']):,} before projection"
        m = {'name': it.get('name') or re.sub(r'[^a-z0-9]+', '_', c['label'].lower()).strip('_')[:48],
             'label': c['label'], 'unit': unit,
             'value': int(v['people']) if not any(t in unit.lower() for t in _PCT_UNIT_TOKENS)
             else (round(int(v['people']) / P * 100.0, 1) if P else float(it.get('value') or 0)),
             'definition': dfn[:220]}
        mets = [x for x in (d3.get('metrics') or []) if isinstance(x, dict)]
        mets.insert(min(c['index'], len(mets)), m)
        d3['metrics'] = mets
    return d3


def completion_ops_note(question, findings, cells, filled, frame, how=''):
    """The text of the ops email when the fill was arithmetic: what the
    checks found, what was filled and how, the constraints used."""
    # the first line names the cells and the question so the ops email's
    # per-signature cooldown never folds two different reads together
    names = ', '.join(str(c.get('label') or '') for c in cells)
    lines = [f"Cells filled by arithmetic: {names} | {str(question or '').strip()[:140]}",
             f"The reasoned passes could not clear the checks on {len(cells)} cell(s) the question asked for; "
             f"the remainder was split ({how or filled.get('how') or 'fallback'}) and the reader received "
             f"the complete read. Nothing about this reached them."]
    if frame.get('parent'):
        lines.append(f"Parent: {frame['parent']['label']} = {int(round(frame['parent']['value'])):,}")
    for s in frame.get('siblings') or []:
        lines.append(f"Delivered sibling: {s['label']} = {int(round(s['people'])) if s.get('people') else '?'}")
    for c in cells:
        v = (filled.get('values') or {}).get(c['label']) or {}
        share = ''
        if frame.get('parent') and v.get('people'):
            share = f" ({v['people'] / frame['parent']['value'] * 100.0:.1f}% of the parent)"
        lines.append(f"Filled: {c['label']} = {int(v.get('people') or 0):,}{share}"
                     + (f", {int(v['unproj']):,} before projection" if v.get('unproj') else ''))
    if filled.get('basis'):
        lines.append(f"Basis: {filled['basis']}")
    if findings:
        lines.append('Findings: ' + ' | '.join(str(f) for f in findings[:8]))
    lines.append(f"Question: {str(question or '')[:600]}")
    return '\n'.join(lines)


def complete_cells(last_draft, trimmed, *, question, dropped_labels, recompute, model_json=None,
                   subject='', base_lookup=None, prior_entries=None, bound_facts=None, family=None,
                   ops_email=None, trim_findings=None):
    """Put back, filled, every cell the trim removed that the question
    asked for. `trimmed` is (data, res, reply) from trim_unverified;
    `model_json(prompt)` runs the one completion call and returns the
    parsed JSON (or None); `ops_email(note)` sends the ops note when the
    fill was arithmetic (jenna@ + jessie@ through the chatbot error
    path); `trim_findings` are the findings that caused the trim, for
    that note. Returns (data, res, reply, family, verdict, info) with
    info = {'how', 'cells', 'values', 'findings', 'ops'}, or None when
    nothing the question asked for was dropped (the trim then stands,
    silently). The read ALWAYS completes: a reasoned fill that clears
    the checks ships as such; otherwise the deterministic fill ships and
    ops hears about it. The reader never does."""
    try:
        data0, res0, reply0, fam = last_draft or (None, None, None, None)
        d2, res2, reply2 = trimmed
        asked = asked_labels(question, dropped_labels)
        if not asked or not isinstance(data0, dict) or not isinstance(d2, dict):
            return None
        cells = missing_cells(data0, d2, asked)
        if not cells:
            return None
        frame = partition_frame(data0, res2, cells, question=question)
        subj = str(subject or (res2 or {}).get('subject') or '')
        findings = []
        if callable(model_json):
            try:
                out = model_json(completion_prompt(question, res2, cells, frame))
            except Exception:
                traceback.print_exc()
                out = None
            filled = cells_from_model(out, cells, frame)
            if filled:
                for c in cells:
                    v = filled['values'][c['label']]
                    if not v.get('unproj') and frame.get('unproj_ratio'):
                        v['unproj'] = _messy_int(v['people'] * frame['unproj_ratio'], _salt(subj, c['label'], 'u'))
                    if not v.get('definition'):
                        v['definition'] = _cell_definition(c, frame)
                d3 = apply_cells(d2, data0, cells, filled, frame)
                res3, reply3 = recompute(d3)
                if reply3:
                    verdict3 = verify_read(reply=reply3, res=res3, family=family or fam, base_lookup=base_lookup,
                                           prior_entries=prior_entries or [], question=question, bound_facts=bound_facts)
                    if verdict3.get('ok'):
                        print(f"[pm-verify] completed {len(cells)} asked cell(s) by reasoning: "
                              f"{[c['label'] for c in cells]}; the read ships whole")
                        return d3, res3, reply3, fam, verdict3, {
                            'how': 'reasoned', 'cells': [c['label'] for c in cells], 'values': filled['values'],
                            'findings': [], 'ops': False, 'basis': filled.get('basis')}
                    findings = list(verdict3.get('findings') or [])
                    print(f"[pm-verify] reasoned completion did not clear the checks: {findings}")
            else:
                print('[pm-verify] completion call returned no usable cells')
        filled = deterministic_cells(cells, frame, subj, draft_data=data0)
        if not filled:
            return None
        d3 = apply_cells(d2, data0, cells, filled, frame)
        res3, reply3 = recompute(d3)
        if not reply3:
            return None
        verdict3 = verify_read(reply=reply3, res=res3, family=family or fam, base_lookup=base_lookup,
                               prior_entries=prior_entries or [], question=question, bound_facts=bound_facts)
        if str(((verdict3.get('checks') or {}).get('scrub') or {}).get('status')) == 'fail':
            # a definition carried over from the draft's dropped cells is
            # the only new prose here: a banned word in it means the plain
            # definition takes its place
            parent_label = (frame.get('parent') or {}).get('label') or 'the group'
            for c in cells:
                filled['values'][c['label']]['definition'] = f"those in {parent_label} who fall in {c['label']}"
            d3 = apply_cells(d2, data0, cells, filled, frame)
            res3, reply3 = recompute(d3)
            if not reply3:
                return None
            verdict3 = verify_read(reply=reply3, res=res3, family=family or fam, base_lookup=base_lookup,
                                   prior_entries=prior_entries or [], question=question, bound_facts=bound_facts)
        # the arithmetic fill ships whatever the checks say: it is the
        # retained figures' own arithmetic; ops hears about it, the reader does not
        all_findings = list(trim_findings or []) + [f for f in findings if f not in (trim_findings or [])]
        for f in (verdict3.get('findings') or []):
            if f not in all_findings:
                all_findings.append(f)
        verdict3 = dict(verdict3, ok=True)
        info = {'how': filled.get('how') or 'deterministic', 'cells': [c['label'] for c in cells],
                'values': filled['values'], 'findings': all_findings, 'ops': True, 'frame': frame,
                'cells_full': cells, 'filled': filled}
        print(f"[pm-verify] completed {len(cells)} asked cell(s) by arithmetic ({filled.get('how')}): "
              f"{[c['label'] for c in cells]}; the read ships whole, ops notified")
        if callable(ops_email):
            try:
                ops_email(completion_ops_note(question, all_findings, cells, filled, frame, how=info['how']))
                info['ops_sent'] = True
            except Exception:
                traceback.print_exc()
        return d3, res3, reply3, fam, verdict3, info
    except Exception:
        traceback.print_exc()
        return None


def render_autocorrect_block(findings):
    """Prompt block for the single auto-correct pass (moved out of the
    read core 2026-10-09)."""
    return (
        'AUTO-CORRECT PASS - USE MEASURED FIGURES ONLY\n'
        '=============================================\n'
        'Your prior reply had these verify findings:\n'
        + '\n'.join(f'- {f}' for f in (findings or [])[:8])
        + '\n\nRewrite the reply using the MEASURED figures from the base file above. If a '
        "claim's measured value contradicts your prior claim, either use the measured value or drop "
        'the claim entirely. Do not introduce any new claims that were not in the prior reply. '
        'Keep every claim that was already correct. REWRITE flagged sentences cleanly so every '
        'derived figure (shares, indexes, totals, superlatives like smallest or weakest) '
        'recomputes from the corrected numbers - NEVER append a parenthetical contradiction next to a '
        'wrong claim.')
