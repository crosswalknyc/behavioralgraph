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
    if not rows and len(ln) >= 5:
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
    """One plain line for the reader when a figure the question asked
    for was left out; '' when the dropped items were the model's own
    additions (the correction is then silent, in-place-corrections)."""
    q = _norm(question)
    asked = [lab for lab in dropped_labels or []
             if _norm(lab) and len(_norm(lab)) >= 3 and _norm(lab) in q]
    if not asked:
        return ''
    names = ', '.join(dict.fromkeys(asked))
    return (f"I do not have a figure I can stand behind for {names} in this window, "
            f"so it is not in this read.")


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
