"""
Canonical Profile IQ categories and the CW IQ scoring core.

This module used to drive the Talent / Brands leaderboard. That surface is
retired and everything specific to it has been removed: the nightly
clickstream pass, the ClickHouse persistence and leaderboard aggregation,
the timeseries reads, the tracker auto-provisioning, the backfills, and the
per-profile context builder.

What remains is the part other live callers still depend on:

1.  `MASTER_CATEGORIES` - the canonical BRAND CATEGORY list. This is the
    source of truth referenced by the workspace category rule, imported by
    the chat interpret paths in `app.py`, and ast-parsed (not imported) by
    `migration/final_ship_gate.py`. Keep the module-level
    `MASTER_CATEGORIES: dict[str, list[str]] = {...}` shape: the ship gate
    matches that annotated assignment and silently falls back to a stale
    embedded snapshot if it cannot find it.
2.  Subcategory normalization and master-bucket lookup.
3.  `read_brand_input_from_csv`, `_build_ranker_brand_terms` and
    `_iter_profile_jobs` - profile selection and term resolution, reused by
    the scraped-signal scripts under `scripts/trends_scrapers/`.
4.  `compute_cw_iq_score` - the 0..100 composite, unchanged, so scores stay
    comparable to the history already stored.

No ClickHouse, no Snowflake, no S3 writes. Callers inject `s3_client` where
a read is needed.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from typing import Iterable


# Master → list of subcategory strings (matches the Profile IQ optgroups in
# templates/index.html so the UI sub-tabs line up exactly).
MASTER_CATEGORIES: dict[str, list[str]] = {
    "BRAND": [
        "ACCESSORIES", "ACTIVEWEAR", "AMUSEMENT PARKS", "APPAREL",
        "APPAREL/FOOTWEAR", "AUTOMOBILE",
        # 2026-09-01 (Jenna): AUTOMOTIVE PARTS is a sub-cut of AUTOMOBILE
        # (hostmap SECTION "Automobile, Automotive Parts"), mirroring
        # AUTOMOBILE's values the same way CPG mirrors MOST PURCHASED
        # BRANDS. Parts brands (Edelbrock, K&N Filters, JEGS, Bilstein,
        # Fox Shocks, Flowmaster, aFe Power, ...) land in AUTOMOBILE +
        # AUTOMOTIVE PARTS at one identical value.
        "AUTOMOTIVE PARTS",
        # 'BANK' is the canonical value the Profile-IQ pipeline writes into
        # the BRAND CATEGORY row for traditional retail banks (Bank of
        # America, Citibank, Wells Fargo, BMO, Bread Financial, ...). Older
        # 'BANKS' (plural) is kept for legacy files; 'BANKING' covered for
        # forward compatibility. Without these, bank profiles fall through
        # to 'OTHER' and are hidden from the leaderboard tabs.
        "B2B", "BANK", "BANKS", "BANKING",
        "BEAUTY", "BETTING", "BEVERAGE", "CASUAL DINING", "CPG",
        "CREDIT PROVIDERS", "CREDIT PROVIDER", "DIGITAL BANKING", "EVENTS",
        "FESTIVAL",
        "FOOTWEAR", "FRANCHISE", "GROCERY", "INTIMATES", "JEWELRY",
        "LOYALTY PROGRAMS",
        "MEMBERSHIP",
        "NON PROFIT/CHARITY", "PHARMA", "QSR", "RETAILERS", "SECURITY",
        "SHOPPING INTENT",
        "SWEEPSTAKES",
        # 2026-08-27: TECHNOLOGY/DEVICE added as a canonical BRAND
        # category (mirrors the hostmap section name). Covers TV/device
        # maker universes (Vizio TV Owners, LG TV Owners, Samsung TV)
        # that don't fit RETAILERS or B2B. Icon 💻 (already wired in
        # the behavioral maps).
        "TECHNOLOGY/DEVICE",
        # 2026-08-25 (Jenna): TRADING added as a new canonical BRAND
        # category for the finance family (Robinhood, E*TRADE, Coinbase,
        # Charles Schwab active-trader tools, options/crypto/forex
        # platforms). Sits alongside BANKING, DIGITAL BANKING, CREDIT
        # PROVIDER, INVESTMENTS. Icon: 💹 (chart with yen, reads as
        # active markets, distinct from INVESTMENTS's 📈 which reads as
        # long-term investing).
        "TELECOM", "TICKETING", "TOY", "TRADING", "TRAVEL", "VENUE",
        "WHERE THEY SHOP",
        "WORKOUT FACILITY",
    ],
    "TALENT": [
        "ACTOR", "ATHLETE", "COMEDIAN", "INFLUENCER/CREATOR",
        "EMERGING TALENT", "HOST/PERSONALITY", "MUSICIAN/BAND", "PODCASTER",
        "POLITICIAN", "POLITICS/ACTIVIST", "WRITER/DIRECTOR/AUTHOR/ARTIST",
    ],
    "CONTENT": [
        "GAME PLAYERS", "GAMES", "GAMES - PLAYERS", "MOVIE", "PODCAST",
        "VERTICAL SHORTS", "VIDEO GAME",
        # SERIES variants are handled by startswith("SERIES") below.
    ],
    # 2026-08-14 (Jenna): FAST PLATFORM + FAST CHANNEL live under
    # PLATFORMS as sub-values (not as their own top-level bucket).
    # Values are SINGULAR + UPPERCASE to match the BRAND CATEGORY
    # tags in dashboard-inputs; the frontend MASTER_CATEGORIES
    # lookup is case-sensitive.
    "PLATFORMS": [
        "APP/PLATFORM", "BROADCAST/CABLE",
        "FAST CHANNEL", "FAST PLATFORM",
        "MEDIA", "MOVIE THEATER", "PLATFORMS",
        "SEARCH ENGINE", "SEARCH ENGINE/AI",
        "SOCIAL MEDIA", "STREAMING MUSIC",
        "STREAMING PLATFORM", "STREAMING VIDEO", "STREAMING/PLATFORM",
        "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD/FAST", "VMVPD",
    ],
    "SPORT": ["MILB", "MLB", "NBA", "NFL", "SPORTS ORGANIZATIONS",
              "SPORTS ORGANIZATION", "SPORTS TEAM", "WNBA"],
    # 2026-08-03 (Jenna). New canonical top-level bucket. Renders in the
    # profile selector immediately after SPORT and before TRENDS. Holds
    # health-system, hospital-network, insurer, pharma-adjacent, and
    # digital-health brands + subjects that don't cleanly belong under
    # BRAND (retail) or PLATFORMS (media / digital services).
    "HEALTHCARE": ["HEALTHCARE"],
    "TRENDS": ["TRENDS"],
}


# Aliases that fold into a canonical subcategory at ingest time. Keys are
# case-normalized (UPPER, stripped). Add new aliases here as profile data
# drifts — keeps the leaderboard tabs and the persisted rows consistent.
SUBCATEGORY_ALIASES: dict[str, str] = {
    "CREATOR/INFLUENCER": "INFLUENCER/CREATOR",
    # 2026-07-20 (Jenna audit). Non-canonical values that older Profile-IQ
    # runs stamped into BRAND CATEGORY, all present in live S3 profiles.
    # Collapse into the canonical bucket so leaderboards + selector agree.
    "POLITICIAN": "POLITICS/ACTIVIST",
    "SEARCH ENGINE": "SEARCH ENGINE/AI",
    "STREAMING PLATFORM": "STREAMING/PLATFORM",
    "FESTIVAL": "EVENTS",
    "VIDEO GAME": "GAMES",
}


def normalize_subcategory(subcategory: str) -> str:
    """Canonicalize a raw subcategory string.

    Upper-cases, strips, and applies SUBCATEGORY_ALIASES so equivalent
    spellings collapse into one bucket (e.g. CREATOR/INFLUENCER →
    INFLUENCER/CREATOR). Empty / None becomes 'UNCATEGORIZED'.
    """
    if not subcategory:
        return "UNCATEGORIZED"
    sub = str(subcategory).upper().strip()
    return SUBCATEGORY_ALIASES.get(sub, sub)


def get_master_category(subcategory: str) -> str:
    """Return the master bucket for a raw BRAND CATEGORY value.

    Mirrors the UI's getMasterCategory() so backend filters match the
    optgroup the user saw when creating the profile.
    """
    if not subcategory:
        return "OTHER"
    sub = normalize_subcategory(subcategory)
    if sub == "SVOD ACQUISITION":
        return "SVOD ACQUISITION"
    for master, subs in MASTER_CATEGORIES.items():
        if sub in subs:
            return master
    if sub.startswith("SERIES"):
        return "CONTENT"
    return "OTHER"


# ── CW IQ Score weights (env-tunable) ───────────────────────────────────────
CW_IQ_WEIGHT_VOLUME    = float(os.environ.get("CW_IQ_WEIGHT_VOLUME",    "0.50"))


CW_IQ_WEIGHT_REACH     = float(os.environ.get("CW_IQ_WEIGHT_REACH",     "0.30"))


CW_IQ_WEIGHT_MOMENTUM  = float(os.environ.get("CW_IQ_WEIGHT_MOMENTUM",  "0.15"))


CW_IQ_WEIGHT_RECENCY   = float(os.environ.get("CW_IQ_WEIGHT_RECENCY",   "0.05"))


# CW_IQ_WEIGHT_SENTIMENT was removed when sentiment was pulled off the
# IQ Rankers dashboard. The 0.20 weight was redistributed: +0.05 to
# Volume (now 0.50), +0.10 to Reach (now 0.30), +0.05 to Momentum (now
# 0.15). Recency stays at 0.05. The four weights still sum to 1.0,
# so the sigmoid input range is preserved and the new published scores
# fall in roughly the same percentile band as before for profiles
# whose net sentiment was near zero.

# How many days of history we use as the per-entity baseline for the z-score.
CW_IQ_BASELINE_DAYS    = int(os.environ.get("CW_IQ_BASELINE_DAYS", "28"))


# ============================================================================
# Profile → brand-terms extraction
# ============================================================================


def read_brand_input_from_csv(s3_client, bucket: str, key: str) -> list[str]:
    """Read just enough of the profile CSV to recover the BRAND INPUT row.

    The BRAND INPUT row sits at the very top of every Profile IQ CSV
    (inserted by bg.py before save), so we only need the first ~32KB of
    the object.
    """
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key, Range="bytes=0-32768")
        body = resp["Body"].read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[iq_rankers] read brand_input from {key} failed: {e}")
        return []
    try:
        reader = csv.reader(io.StringIO(body))
        for row in reader:
            if not row:
                continue
            if (row[0] or "").strip().upper() == "BRAND INPUT":
                value = (row[1] if len(row) > 1 else "") or ""
                return [t.strip() for t in value.split(",") if t.strip()]
    except Exception as e:
        print(f"[iq_rankers] parse brand_input from {key} failed: {e}")
    return []


def _build_ranker_brand_terms(
    project_name: str,
    csv_terms: list[str],
) -> list[str]:
    """Return profile-specific brand terms for the IQ Ranker daily-metrics SQL.

    Many Profile IQ CSVs were generated from broad panel-study terms (just a
    first name like "Anthony") because the original study was scoped that
    way. The CSV's BRAND INPUT row therefore says "Anthony" for every Anthony
    profile (Mackie, Lapaglia, Carrigan, ...), which makes their daily
    `multiSearchAny(lower(URL), [...])` filter resolve to the SAME superset
    of clickstream rows -- so they all end up with identical raw_mentions /
    unique_uids / projected Engagements. Sentiment IQ trackers keep using the
    csv_terms (so existing tracker data stays intact); the Ranker overrides
    them here so each profile's leaderboard signal is actually about that
    person.

    Strategy:
    - Multi-word project_name -> emit name variants (space, dash, joined)
      and drop any csv_term that is a strict substring of the project_name
      (those are the broad stragglers like bare "Anthony").
    - Single-word project_name -> fall through to csv_terms as-is. Such
      profiles are filtered upstream by `_build_covered_single_names()` if
      they have a multi-word sibling (e.g. bare "Anthony" is dropped from
      the ranker because "Anthony Mackie" / "Anthony Lapaglia" already cover
      the same surface area at higher specificity).

    All returned terms are lowercased to match the SQL's lower(URL/COMMON_NAME)
    side of the comparison.
    """
    pn_lc = (project_name or "").strip().lower()
    pn_clean = re.sub(r"[^a-z0-9 ]+", " ", pn_lc).strip()
    pn_clean = re.sub(r" +", " ", pn_clean)
    words = [w for w in pn_clean.split(" ") if w]

    out: list[str] = []
    if len(words) >= 2:
        space_form = " ".join(words)
        dash_form  = "-".join(words)
        cat_form   = "".join(words)
        out.append(space_form)
        if dash_form != space_form:
            out.append(dash_form)
        # Only emit the dashless concat for longer names so we don't
        # false-positive on something like "the rock" -> "therock"
        # matching unrelated URLs containing "therock" as a brand suffix.
        if len(cat_form) >= 8:
            out.append(cat_form)
    elif len(words) == 1:
        out.append(words[0])

    pn_full = " ".join(words)
    for t in (csv_terms or []):
        tl = (t or "").strip().lower()
        if not tl:
            continue
        # Drop terms that are a strict substring of the project name (the
        # bare-first-name stragglers). Keep terms that ARE the full project
        # name or that bring NEW signal (aliases / company brands).
        if pn_full and tl in pn_full and tl != pn_full:
            continue
        if tl not in out:
            out.append(tl)

    seen: set[str] = set()
    final: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            final.append(t)
    return final


_ARTIFACT_NAME_TOKENS = (".bak", "prepatch", "pre_patch", "_backups/")


# A profile_subject carrying an embedded dated-filename stamp
# (`..._08_28_2026_04_00`, anywhere in the name) is an ingestion
# artifact: the subject should be the clean entity name, and the same
# entity usually exists again under a different timestamp
# (AMASS_CUSTOMERS appears twice; Home_Internet_Shopping carries the
# stamp mid-name), which renders duplicate leaderboard rows. The full
# five-component MM_DD_YYYY_HH_MM shape never occurs in a real entity
# name, so matching anywhere is safe.
_ARTIFACT_TS_SUFFIX = re.compile(r"_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}")


def _is_backup_artifact_name(*parts: str) -> bool:
    """True when any of the given name/key fragments looks like a backup
    file artifact rather than a real profile. A bad ingest on 2026-06-01
    swept ~225 `<Subject>_<TS>.prepatch.bak` files into
    `reference.profile_iq_daily_metrics`; their multi-word cleaned names
    then wrongly 'covered' real single-word subjects (the Paramount+
    disappearance). Artifacts must never be processed by the nightly
    cron, never cover a sibling, and never render on the leaderboard.
    """
    for p in parts:
        low = (p or "").lower()
        if any(tok in low for tok in _ARTIFACT_NAME_TOKENS):
            return True
        if _ARTIFACT_TS_SUFFIX.search(low):
            return True
    return False


# Master bucket + subcategory labels that identify a PERSON profile.
# The covered-singles suppression below only ever applies to people:
# a bare first-name profile ("Anthony") duplicates the fallback match
# surface of its multi-word siblings ("Anthony Mackie"). A single-word
# BRAND / CONTENT / PLATFORM name ("Netflix", "Marriott", "Power") is a
# complete entity in its own right and must rank alongside its more
# specific cohort siblings ("Netflix AVOD Subscribers") - Jenna
# 2026-09-04. s3_cache jobs carry the subcategory-level label in
# `category` (ACTOR, ATHLETE, ...); ClickHouse rows carry the master
# bucket in `category` (TALENT) and the label in `subcategory`, so the
# check accepts either spelling on either field.
_PERSON_CATEGORY_KEYS = frozenset(
    {"TALENT"}
    | {c.strip().upper() for c in MASTER_CATEGORIES.get("TALENT", [])}
    | {"CREATOR/INFLUENCER"}
)


def _is_person_profile(job: dict) -> bool:
    """True when the job's category labels identify a person profile.

    Unknown / empty categories count as person so legacy rows with no
    category keep the historical suppression behavior (never let an
    unlabeled bare first name surface as a duplicate).
    """
    labels = [(job.get("category") or "").strip().upper(),
              (job.get("subcategory") or "").strip().upper()]
    if not any(labels):
        return True
    return any(v in _PERSON_CATEGORY_KEYS for v in labels if v)


def _build_covered_single_names(jobs: list[dict]) -> set[str]:
    """Identify single-name Profile IQ profiles that are 'covered' by a
    multi-word sibling and should therefore be skipped from the IQ Ranker.

    A profile P with project_name = "Joseph" is covered when at least one
    other profile has project_name starting with "Joseph " (e.g. "Joseph
    Gordon Levitt"). The bare-"Joseph" profile's URL/COMMON_NAME match
    superset is identical to its sibling's broad-fallback match, which
    creates the duplicate-Engagement-count problem the user surfaces in
    the leaderboard.

    Returns a set of profile_subject values (the canonical grouping key
    used everywhere else in this module). The Sentiment IQ tracker, the
    Profile IQ CSV, and any historical metrics rows for these subjects
    are left intact -- only the Ranker's view filter / nightly cron is
    affected.

    ONLY PERSON PROFILES can be covered (Jenna 2026-09-04): "parent
    brands should appear alongside their more specific sibling
    profiles". A single-word BRAND / CONTENT / PLATFORM subject
    ("Netflix", "Marriott", "Power", "YouTube") always ranks even when
    a multi-word sibling shares its first word ("Netflix AVOD
    Subscribers", "YouTube TV"). The suppression is a person-name
    dedupe and nothing more; `_is_person_profile` holds the category
    check.

    A derived cut NEVER covers its own base profile (fixed 2026-09-04).
    Cut files follow the "{Subject} - {Cut}" naming convention
    ("Paramount+ - Avid Fan", "Peacock - Female"), so only the subject
    part before the first " - " decides whether a name is a genuine
    multi-word entity. Before this fix, "Paramount+ - Avid Fan" cleaned
    to "Paramount Avid Fan" (multi-word, first word "paramount") and
    wrongly covered the single-word "Paramount+" profile it was cut
    from, which silently dropped Paramount+, Peacock, and every other
    single-word brand with an Avid cut out of the nightly ranker.
    """
    multiword_first_words: set[str] = set()
    singles_by_word: dict[str, list[str]] = {}
    for j in jobs or []:
        pn = (j.get("project_name") or j.get("display_name")
              or j.get("name") or "").strip()
        subj = j.get("profile_subject") or ""
        if not pn or not subj:
            continue
        # Backup-file artifacts (e.g. "Paramount+_..._23_00.prepatch.bak")
        # are not profiles and must not cover anything.
        if _is_backup_artifact_name(subj, pn):
            continue
        # Judge single vs multi-word on the subject part only, so a
        # "{Subject} - {Cut}" sibling can't cover its own parent.
        base = pn.split(" - ", 1)[0].strip() or pn
        clean = re.sub(r"[^a-zA-Z0-9 ]+", " ", base).strip()
        words = [w for w in clean.split() if w]
        if len(words) >= 2:
            multiword_first_words.add(words[0].lower())
        elif len(words) == 1:
            # Only PERSON profiles are candidates for coverage (Jenna
            # 2026-09-04): parent brands, titles, and platforms rank
            # alongside their cohort siblings; bare first names stay
            # suppressed by their multi-word person siblings.
            if _is_person_profile(j):
                singles_by_word.setdefault(words[0].lower(), []).append(subj)
    covered: set[str] = set()
    for fw, subs in singles_by_word.items():
        if fw in multiword_first_words:
            covered.update(subs)
    return covered


# ============================================================================
# CW IQ Score
# ============================================================================


def _z(value: float, mean: float, std: float) -> float:
    """Stable z-score — std=0 collapses to 0 instead of NaN."""
    if std <= 1e-9:
        return 0.0
    return (value - mean) / std


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def compute_cw_iq_score(
    *,
    today: dict[str, float],
    history: list[dict[str, float]],
    snapshot_date: str | None = None,
    damping_basis: str = "rows",
) -> float:
    """Compose the 0..100 CW IQ Score.

    history is a list of prior-day metric dicts (most-recent first), used
    only as the per-entity baseline for the z-scores. If the entity is
    brand-new (no history), z collapses to 0 and the score is driven by
    momentum and recency alone.
    """
    mentions       = float(today.get("mentions") or 0)
    unique_uids    = float(today.get("unique_uids") or 0)

    # Per-entity baselines
    h_mentions = [float((d or {}).get("mentions") or 0) for d in history[:CW_IQ_BASELINE_DAYS]]
    h_uids     = [float((d or {}).get("unique_uids") or 0) for d in history[:CW_IQ_BASELINE_DAYS]]
    if h_mentions:
        m_mu = sum(h_mentions) / len(h_mentions)
        m_var = sum((x - m_mu) ** 2 for x in h_mentions) / max(len(h_mentions), 1)
        m_sd = math.sqrt(m_var)
    else:
        m_mu = m_sd = 0.0
    if h_uids:
        u_mu = sum(h_uids) / len(h_uids)
        u_var = sum((x - u_mu) ** 2 for x in h_uids) / max(len(h_uids), 1)
        u_sd = math.sqrt(u_var)
    else:
        u_mu = u_sd = 0.0

    z_volume = _z(math.log1p(mentions),    math.log1p(m_mu), math.log1p(m_sd) or 0.5)
    z_reach  = _z(math.log1p(unique_uids), math.log1p(u_mu), math.log1p(u_sd) or 0.5)

    # Cold-start damping: with <3 days of history, z-scores are unstable —
    # ramp them in linearly so brand-new profiles can't outscore established
    # ones purely on first-day novelty noise.
    #
    # `damping_basis` decides what counts as a day of history.
    #   "rows"      counts every stored day, including days the entity was
    #               not seen at all. This is the original behaviour and
    #               stays the default so nothing already scored moves.
    #   "observed"  counts only days the entity actually registered. A
    #               nightly pass that writes a row for every entity every
    #               day (both paths do) otherwise hands a full history
    #               factor to an entity that has never been seen, and its
    #               first day of signal scores against a baseline of pure
    #               zeros, which lands on 100.0. Counting observed days is
    #               what the ramp was always meant to do.
    if damping_basis == "observed":
        observed = sum(1 for x in h_mentions if x > 0)
        history_factor = min(1.0, observed / 3.0)
    else:
        history_factor = min(1.0, len(h_mentions) / 3.0) if h_mentions else 0.0
    z_volume *= history_factor
    z_reach  *= history_factor

    # Day-over-day momentum: % change vs yesterday, capped at ±2 (clip to keep
    # one extreme day from dominating).
    yest = h_mentions[0] if h_mentions else 0.0
    if yest > 0:
        dod = (mentions - yest) / yest
    else:
        dod = 1.0 if mentions > 0 else 0.0
    dod_capped = max(-2.0, min(2.0, dod))

    # Recency bonus: small constant when there are mentions today, fades for
    # entities that haven't been mentioned in days.
    if mentions > 0:
        recency = 1.0
    elif h_mentions and h_mentions[0] > 0:
        recency = 0.5
    else:
        recency = 0.0

    # Linear combination → sigmoid → 0..100. Sentiment used to contribute
    # the third term here at 0.20 weight; it was removed when the
    # sentiment columns came off the dashboard, and its weight was
    # redistributed across Volume / Reach / Momentum (see weight constants
    # above for the rationale).
    raw = (
        CW_IQ_WEIGHT_VOLUME    * z_volume
      + CW_IQ_WEIGHT_REACH     * z_reach
      + CW_IQ_WEIGHT_MOMENTUM  * dod_capped
      + CW_IQ_WEIGHT_RECENCY   * recency
    )
    return round(100.0 * _sigmoid(raw), 2)


# ============================================================================
# Daily orchestration — run for every profile
# ============================================================================


def _iter_profile_jobs(s3_cache_jobs: list[dict]) -> Iterable[dict]:
    """Yield Profile IQ job entries we care about (skip non-profile sources
    and items in purgatory).

    Avid Fan profiles (`<NAME> - Avid Fan.csv`) are panel-segmentation
    files used by the audience-overlap views, not standalone Profile IQ
    entities. They have no BRAND INPUT block and shouldn't appear in
    the IQ Ranker leaderboard. We skip them here so the nightly cron
    stops computing daily metrics for them; the leaderboard SQL also
    filters them out so any pre-existing rows already in
    `reference.profile_iq_daily_metrics` are hidden from the dashboard.

    Bare-first-name profiles whose name is also the first word of some
    multi-word sibling (e.g. "Anthony" when "Anthony Mackie" /
    "Anthony Lapaglia" exist) are also skipped. Their URL/COMMON_NAME
    match superset is a duplicate of their siblings' fallback match
    surface and creates the identical-Engagement-count ties seen on
    the leaderboard.
    """
    covered_singles = _build_covered_single_names(s3_cache_jobs or [])
    seen: set[str] = set()
    for j in s3_cache_jobs or []:
        s3_key = j.get("s3_key") or j.get("job_id") or ""
        if not s3_key:
            continue
        if "purgatory" in s3_key.lower():
            continue
        # Hardcoded match on the literal " - Avid Fan" suffix used by the
        # panel-segmentation file naming convention. Matching on bare
        # "avid" would falsely catch real names like "David Letterman"
        # whose substring "avid" is unrelated.
        haystack = " | ".join(str(j.get(k) or "") for k in
            ("project_name", "display_name", "profile_subject", "s3_key")).lower()
        if "avid fan" in haystack:
            continue
        # Backup-file artifacts must never get nightly metrics rows.
        # Check NAME fields only, never the s3_key: every legitimate
        # root profile key embeds the dated-filename stamp
        # (Netflix_05_22_2026_21_45.csv), so running the timestamp
        # pattern against the key would flag the entire catalog.
        if _is_backup_artifact_name(j.get("profile_subject"),
                                    j.get("project_name"),
                                    j.get("display_name")):
            continue
        # We dedupe on profile_subject so multi-year runs of the same person
        # only get one row per day.
        subject = j.get("profile_subject") or ""
        if not subject:
            continue
        if subject in seen:
            continue
        if subject in covered_singles:
            continue
        seen.add(subject)
        yield j


__all__ = [
    "MASTER_CATEGORIES",
    "SUBCATEGORY_ALIASES",
    "normalize_subcategory",
    "get_master_category",
    "CW_IQ_BASELINE_DAYS",
    "read_brand_input_from_csv",
    "compute_cw_iq_score",
    "_build_ranker_brand_terms",
    "_build_covered_single_names",
    "_iter_profile_jobs",
]
