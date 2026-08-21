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

import io
import re
import time
import threading

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

_digest_cache = {}       # {s3_key: (etag, built_ts, digest_str, meta)}
_genpop_cache = {'ts': 0.0, 'map': None}
_cache_lock = threading.Lock()

GENPOP_KEY = 'Gen_Pop_2026.csv'
GENPOP_TTL_S = 3600


def _norm_cat(c):
    return re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())


def _norm_brand(b):
    return re.sub(r'[^a-z0-9]+', '', str(b or '').lower())


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
    """Fetch a profile CSV from S3. Returns (df, etag)."""
    resp = s3_client.get_object(Bucket=bucket, Key=s3_key)
    etag = (resp.get('ETag') or '').strip('"')
    content = resp['Body'].read().decode('utf-8', 'replace')
    df = pd.read_csv(io.StringIO(content)).fillna('')
    return df, etag


def _profile_meta(df, fallback_name):
    """Extract subject name, sample size, projection, window from
    metadata rows."""
    bp = _bp_col(df)
    raw_c = _fuzzy_col(df, 'raw')
    proj_c = _fuzzy_col(df, 'proj')
    name, sample, proj, window = fallback_name, None, None, None
    dates = []
    for _, row in df.iterrows():
        cat = _norm_cat(row.get('Column'))
        if cat not in METADATA_COLS:
            continue
        val = str(row.get('Value') or '')
        if cat == 'SUBJECT' and val and not name:
            name = val
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
            'bp_col': bp}


def load_genpop_map(s3_client, bucket):
    """(category, brand) -> gen pop BP, cached for an hour."""
    with _cache_lock:
        if (_genpop_cache['map'] is not None
                and time.time() - _genpop_cache['ts'] < GENPOP_TTL_S):
            return _genpop_cache['map']
    gp = {}
    try:
        df, _ = load_profile_df(s3_client, bucket, GENPOP_KEY)
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
        print(f"[prometheus] genpop map load failed: {e}")
    with _cache_lock:
        _genpop_cache['map'] = gp
        _genpop_cache['ts'] = time.time()
    return gp


def _fmt_row(brand, bp, gp_bp):
    s = f"{brand} {bp:.1f}"
    if gp_bp is not None and gp_bp >= 0.01:
        s += f" (idx {round(bp / gp_bp * 100)})"
    return s


def build_profile_digest(df, meta, genpop_map, subject_name=None,
                         max_rows=12, max_chars=26000):
    """Compact text digest of one profile CSV: metadata line, full
    demographics, then top rows per behavioral category with index vs
    US gen pop (100 = average)."""
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
            demo_lines.append(
                f"  {catU}: " + ' | '.join(
                    f"{b} {v:.1f}" for b, v in rows))
            continue
        shown, pinned = [], 0
        for b, v in rows:
            if v >= 99.99 or _norm_brand(b) == subj_norm:
                pinned += 1
                continue
            if len(shown) < max_rows:
                gp = genpop_map.get((catU, _norm_brand(b)))
                shown.append(_fmt_row(b, v, gp))
        if not shown:
            continue
        suffix = f" [{len(rows)} rows]" if len(rows) > max_rows else ""
        cat_lines.append(f"  {catU}{suffix}: " + '; '.join(shown))

    lines.append("DEMOGRAPHICS (% of audience):")
    lines.extend(demo_lines)
    lines.append("BEHAVIORAL CATEGORIES (top rows, % penetration of this "
                 "audience; idx = index vs US gen pop, 100 = average):")
    lines.extend(cat_lines)
    out = '\n'.join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n  [digest truncated]"
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
    lines.append("  BIGGEST OVER-INDEXES vs parent (pp = percentage points):")
    for _, dlt, catU, b, v, pv in over:
        lines.append(f"    {catU} / {b}: {v:.1f} vs {pv:.1f} (+{dlt:.1f}pp)")
    lines.append("  BIGGEST UNDER-INDEXES vs parent:")
    for _, dlt, catU, b, v, pv in under:
        lines.append(f"    {catU} / {b}: {v:.1f} vs {pv:.1f} ({dlt:.1f}pp)")
    out = '\n'.join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n  [cut digest truncated]"
    return out


def get_digest_bundle(s3_client, bucket, page_context, max_cuts=3):
    """Assemble the full digest bundle for a page context:
    {primary: {s3_key, name}, cuts: [{s3_key, name}, ...]}.
    Returns (bundle_text, primary_meta). Caches per (key, etag)."""
    genpop = load_genpop_map(s3_client, bucket)
    primary = page_context.get('primary') or {}
    p_key = primary.get('s3_key')
    if not p_key:
        raise ValueError('page context has no primary profile key')

    p_df, p_etag = load_profile_df(s3_client, bucket, p_key)
    p_meta = _profile_meta(p_df, primary.get('name'))
    with _cache_lock:
        cached = _digest_cache.get(p_key)
    if cached and cached[0] == p_etag:
        p_digest = cached[2]
    else:
        p_digest = build_profile_digest(p_df, p_meta, genpop)
        with _cache_lock:
            _digest_cache[p_key] = (p_etag, time.time(), p_digest, p_meta)
            if len(_digest_cache) > 40:
                oldest = sorted(_digest_cache.items(),
                                key=lambda kv: kv[1][1])[:10]
                for k, _ in oldest:
                    _digest_cache.pop(k, None)

    parts = [p_digest]
    for cut in (page_context.get('cuts') or [])[:max_cuts]:
        c_key = cut.get('s3_key')
        if not c_key or c_key == p_key:
            continue
        try:
            c_df, _ = load_profile_df(s3_client, bucket, c_key)
            c_meta = _profile_meta(c_df, cut.get('name'))
            parts.append(build_cut_divergence(
                p_df, p_meta, c_df, c_meta, genpop))
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
            e_df, e_etag = load_profile_df(s3_client, bucket, e_key)
            e_meta = _profile_meta(e_df, ex.get('name'))
            with _cache_lock:
                e_cached = _digest_cache.get(e_key)
            if e_cached and e_cached[0] == e_etag:
                e_digest = e_cached[2]
            else:
                e_digest = build_profile_digest(e_df, e_meta, genpop)
                with _cache_lock:
                    _digest_cache[e_key] = (e_etag, time.time(),
                                            e_digest, e_meta)
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

ANALYSIS_SYSTEM_PROMPT = """You are Prometheus, Crosswalk's senior audience strategist inside the Profile IQ dashboard. The user has a profile open on screen (sometimes with cut overlays) and you are handed a numeric digest of that exact data. You think like a senior partner at a top-tier strategy consultancy: hypothesis-led, answer-first, ruthless about what actually changes the client's decision. Your job is to turn the data into sharp, commercially useful thinking.

THE DATA
- Crosswalk data is first-party, T+1, derived from observed clickstream behavior of a US panel. It reflects what panelists did, not what they claim.
- An Engager had at least 1 digital touchpoint with the subject over the trailing 12 months across search, social, media, ecommerce, or owned-and-operated channels.
- Penetration = share of THIS audience active with a brand in the window. idx = index vs US general population, 100 = average, 683 means 6.83x the average.
- pp = percentage points. Cut rows show cut vs parent values.
- The digest is your PRIMARY evidence. Every claim you make must be anchored to numbers in it. You may add outside market knowledge (deal sizes, category dynamics, who sponsors what) as supporting context, never as a substitute, and never invent numbers that look like they came from the data.

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
- Cut comparison: what actually separates the cuts from the parent and from each other, and what to do with that.
- Cross-profile comparison: when the digest carries COMPARISON PROFILE blocks, these are INDEPENDENT audiences (other open tabs or picked profiles), not cuts. Find convergence (strong in both), whitespace (strong in one, weak in the other, both directions), and the positioning play. Show numbers side by side; never treat shares as summing across profiles.
- Media planning: where to reach them (platforms, streaming, social, retail media), what over-indexes enough to matter.
- Audience strategy: gaps worth a NEW profile pull to validate (you can route that, see ACTIONS).
- Metric explanations: define penetration, index, projection, sample plainly if asked.

HOW TO WRITE
- Crosswalk voice: flat, specific, unhurried. State the finding, then the number. "Hulu reads 44.0 against a 21 gen pop, idx 212." No hype words, no "actually", no "absolutely".
- NEVER use em dashes or en dashes. Use commas, periods, or parentheses.
- PLAIN TEXT only. No markdown bold, no #, no tables, no backticks. Structure with short ALL-CAPS section labels on their own line and "- " bullets.
- Round penetrations to one decimal, indexes to whole numbers, big counts like 3.6M.
- Default length 150 to 300 words. Go longer only when the user asks for a deep dive.
- End with a clear recommendation or the sharpest single takeaway, not a summary of what you said.

ACTIONS
Return strict JSON only:
{
  "action": "answer" | "build_profile",
  "reply": "the analysis text (plain text, newlines allowed)",
  "followups": ["up to 4 short follow-on questions the user could tap next"],
  "offer_deck": true | false,
  "deck_angle": "one sentence describing the deck story to build, or null"
}
- action=build_profile ONLY when the user's message is clearly a request to BUILD, PULL, CUT, or REFRESH a profile rather than analyze the open one. Leave reply empty in that case; the build pipeline takes over.
- offer_deck=true when the analysis supports a coherent client-facing story (a pitch, a QBR, a sponsorship case). Set deck_angle to the story in one sentence. Do not offer a deck on a metric-definition answer.
- followups are the next questions the person in the seat would actually ask (per CLIENT LENSES), limited to what THIS data can answer, phrased as the user would type them."""


DECK_PLAN_SYSTEM_PROMPT = """You are Prometheus, building a slide plan for a client-facing Crosswalk deck from Profile IQ data. You get the data digest, the recent analysis conversation, and the requested angle. Return a JSON slide plan that a renderer will lay out in the Crosswalk deck system.

RULES
- 5 to 8 slides. Open with cover, close with close. Vary the middle: stats, chart, recs.
- Every number must come from the digest or the conversation. Never invent data.
- Titles are sentences in sentence case and they end with a period. They state the finding: "Streaming is where this audience already lives." not "Streaming Overview".
- The read line under a chart is one sentence stating what the chart proves, with the key number.
- NEVER use em dashes or en dashes anywhere. No "actually", no "absolutely". Never "real-time"; the data is T+1.
- Figures: 30M not 30 million, one decimal on percentages, whole-number indexes, 683 bare.
- Chart rows: 4 to 6 rows max, ranked descending, values are penetration percentages (numbers only, no % sign in the value field).
- Stats slides: 3 or 4 stat blocks, big value short ("3.6M", "212", "44.0%"), label sentence case under 8 words.
- Recs: 3 or 4, each an action the client team (media, creative, partnerships, development, or research) can take this quarter, with the size of the prize where the data allows.

Return strict JSON only:
{
  "filename_stem": "Short_Safe_Name",
  "title": "Deck title sentence.",
  "slides": [
    {"type": "cover", "eyebrow": "PROFILE IQ", "title": "...", "meta": "Subject; window; sample"},
    {"type": "stats", "eyebrow": "THE AUDIENCE", "title": "...", "read": "...", "stats": [{"big": "3.6M", "label": "projected US audience"}]},
    {"type": "chart", "eyebrow": "WHERE THEY ARE", "title": "...", "read": "...", "unit": "% pen", "rows": [{"label": "Hulu", "value": 44.0, "note": "idx 212"}]},
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
    'full': (
        "FULL READ MODE. Walk the whole digest: audience shape, media, "
        "brands, the non-obvious signals, monetization angles, and the "
        "single sharpest takeaway. Default length rules apply."),
}


def build_analysis_user_prompt(digest_bundle, history, user_message,
                               mode=None):
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
    return (
        "FIRST-PARTY DATA ON SCREEN\n"
        "==========================\n"
        f"{digest_bundle}\n\n"
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
