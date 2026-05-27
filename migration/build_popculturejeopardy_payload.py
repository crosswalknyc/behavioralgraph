"""Build + upload the POPCULTUREJEOPARDY Journey IQ payload to S3 (archive).

POP CULTURE JEOPARDY! SEASON 2 (2026) — Sony Pictures Television / Michael
Davies productions. Hosted by Colin Jost (SNL Weekend Update). Premiered
2026-05-11 on Netflix (migrated from Amazon Prime Video where Season 1
ran Dec 2024 - March 2025). 20 episodes, daily weekday drops at 3am ET
through June 5, 2026. 25-min episodes. Tournament format, teams of 3,
$300K grand prize.

V3 REFRAMING (2026-05-26) — addressed user feedback "since it is tv we
should change it from path-to-purchase to path-to-view and we can get
rid of all the archetype stuff unless we position it as people who did
watch the show. and you should still externally research to see what
the right number of views would be like Subscriber IQ. and since it is
Jeopardy it'll be similar but not the same — this is a digital viewing
audience not the same as a linear Jeopardy viewer."

KEY V3 CHANGES vs. v2:
  - Path-to-PURCHASE → Path-to-VIEW (renderer + step labels)
  - Audience Hypotheses reframed as "MODELED viewer cohorts" with an
    explicit "predicted, not measured" disclosure (the public-measurement
    fallback Subscriber IQ uses when no Nielsen/Tudum/Samba number is
    available — Claude+web_search verified no public US viewership
    figures for PCJ S2 exist as of mid-season)
  - Headline viewer counts CUT to comp-realistic levels:
      - 30-day mid 5.5M unique US viewers (was 11M) — Beef S2 anchored
        at 8.3M Nielsen hours week 1; daily-strip game shows undershoot
        prestige drama
      - Confirmed-to-date mid 3.2M (was 6.485M) — matches T+15 share of
        a daily-strip 30-day curve
      - Total watch hours mid 4M (was 15.6M)
  - Added explicit "Streaming vs Linear Jeopardy!" demo-contrast note:
      Syndicated Jeopardy!: ~62% 55+, ~12% 18-34 (skews older, CTV)
      PCJ Netflix (modeled): ~36% 18-34, ~23% 55+ (younger, mobile/CTV)
  - "Confirmed" is labeled as MODELED-MID-SEASON, not platform-reported
  - Audience archetypes positioned as "the people projected to make up
    the viewer base" with explicit modeled-not-measured callouts on
    every verdict

CURRENT STATUS (as of 2026-05-26): the show is 11 episodes into a 20-
episode daily-drop run. We're mid-season. No public Nielsen / Tudum /
Samba / Luminate / Parrot Analytics figure available for S2 — Claude+
web_search returned null for "us_viewers_millions" (see
research_audience_size in migration/journey_iq_synthesize.py:1252).

Comp set (US, public-reported / triangulated):
  - Is It Cake S1 (Netflix, binge release, 8 eps, March 2022) ≈ 9-12M
    US unique viewers, ~30M US hours in 30 days
  - Cunk on Earth (Netflix, binge, 5 eps, Jan 2023) ≈ ~6M US uniques
  - The Floor (Fox/Netflix-sim, weekly strip) ≈ 5-8M US uniques first
    launch window
  - Beef S2 (Netflix, prestige drama anchor, May 2026) = 8.3M Nielsen
    hours week 1, down from 16.0M for S1 — used as a ceiling reference
    for what a higher-prestige Netflix release achieves
  - Squid Game: The Challenge (Netflix outlier, 83M global views)
  - Jeopardy! syndicated (~9.2M weekly avg US, mostly 55+) — the
    streaming-vs-linear demo-comparison anchor, NOT a viewership comp
"""

import gzip
import io
import json
from datetime import datetime, timezone

import boto3

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

S3_BUCKET            = 'dashboard-inputs'
S3_INDEX_KEY         = 'journey-iq/_index.json'
S3_ARCHIVE_INDEX_KEY = 'journey-iq/_archive_index.json'

# V3 uploads STRAIGHT to the archive folder (per user request: this run
# should be admin-only / archived, not in the live dashboard).
PROJECT_NAME = 'POPCULTUREJEOPARDY'
TARGET       = 'Pop Culture Jeopardy'
TIMESTAMP    = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
KEY          = f'journey-iq/archive/{PROJECT_NAME}_full_{TIMESTAMP}.json.gz'

PREMIERE_DATE  = '2026-05-11'        # S2 Netflix premiere
FINALE_DATE    = '2026-06-05'        # episode 20 drops
WINDOW_START   = '2026-04-27'        # S2 announcement on official IG
WINDOW_END     = '2026-05-26'        # as of today (mid-season, episode 11)
LOOKBACK_DAYS  = 30
EPISODES_TOTAL    = 20
EPISODES_TO_DATE  = 11               # 5/11 Mon → 5/26 Tue = 11 weekday eps

# ── Viewership model (TV / streaming — not box office)
#
# V3 calibration — anchored to public comps. Claude+web_search returned
# no Nielsen/Tudum/Samba US-viewers figure for PCJ S2, so this is an
# explicitly MODELED projection, the same fallback Subscriber IQ uses
# when no platform-reported number is available.
#
# Anchors (all US, public-reported / triangulated):
#   - Beef S2 (Netflix prestige drama, May 2026) — 8.3M Nielsen hours
#     week 1 (down from S1's 16.0M hours). A daily-drop game show will
#     undershoot prestige drama.
#   - Is It Cake S1 (Netflix, binge release, 8 eps) ≈ 9-12M US uniques,
#     ~30M US hours in 30 days — a CEILING anchor (binge format gets
#     bigger 30-day numbers than daily-strip).
#   - Cunk on Earth (Netflix, binge, 5 eps) ≈ ~6M US uniques.
#   - The Floor (Fox/Netflix-sim, weekly strip) ≈ 5-8M US uniques.
#
# PCJ S2 sits BELOW Is It Cake (no binge effect, Jeopardy! franchise
# brand is older-skewed, lost some S1 audience in platform migration)
# and ABOVE Cunk (broader appeal, daily clip-drop strategy, Jost +
# Jeopardy! brand premium). Mid-case ~5.5M unique US viewers over 30
# days, with ~4M total US watch hours (~73 min/viewer, ~3 episodes).
TOTAL_VIEWERS_MID    =  5_500_000           # 30-day post-premiere
TOTAL_VIEWERS_LOW    =  4_000_000
TOTAL_VIEWERS_HIGH   =  7_000_000

# Full lifecycle including 90-day Netflix tail post-finale (~1.35× the
# 30-day mid for a daily-strip game show — lower tail multiplier than
# a binge release because daily-strip shows accumulate viewers
# during the release window, not after)
LIFECYCLE_VIEWERS_MID  =  7_500_000
LIFECYCLE_VIEWERS_LOW  =  5_500_000
LIFECYCLE_VIEWERS_HIGH =  9_500_000

# Watch behavior — lowered from ~85 min/viewer to ~73 min/viewer (~2.9
# episodes at 25 min each). Daily-strip cadence means most viewers fall
# behind and never catch up; avg episodes per viewer is lower than for
# a binge-release game show like Is It Cake.
AVG_MIN_PER_VIEWER   = 73
EPISODES_PER_VIEWER  = 2.9
TOTAL_WATCH_HOURS_MID = int(TOTAL_VIEWERS_MID * AVG_MIN_PER_VIEWER / 60)         # ~6.7M hrs
TOTAL_WATCH_HOURS_LOW = int(TOTAL_VIEWERS_LOW * AVG_MIN_PER_VIEWER / 60)         # ~4.9M hrs
TOTAL_WATCH_HOURS_HI  = int(TOTAL_VIEWERS_HIGH * AVG_MIN_PER_VIEWER / 60)        # ~8.5M hrs

# Premiere week (first 7 days) — daily-drop strip shows split ~28-32%
# of 30-day viewers into the first 7 days (vs 55-70% for binge releases
# where almost everyone finishes by day 7).
PREMIERE_WEEK_VIEWERS_MID  = int(TOTAL_VIEWERS_MID  * 0.30)    # ~1.65M
PREMIERE_WEEK_VIEWERS_LOW  = int(TOTAL_VIEWERS_LOW  * 0.30)    # ~1.2M
PREMIERE_WEEK_VIEWERS_HIGH = int(TOTAL_VIEWERS_HIGH * 0.30)    # ~2.1M

# ── "CONFIRMED" VIEWERSHIP TO DATE (T+15 days, ep 11 of 20)
# These are MODELED, not platform-reported. No Nielsen/Tudum/Samba
# figure available. Mid-case = ~58% of 30-day projection (typical
# share of daily-strip 30-day curve realized by T+15 with ~55% of
# episodes dropped).
CONFIRMED_UNIQUE_VIEWERS    = 3_200_000      # MODELED mid-season estimate
CONFIRMED_WATCH_HOURS       = int(CONFIRMED_UNIQUE_VIEWERS * AVG_MIN_PER_VIEWER / 60)  # ~3.9M
CONFIRMED_REPEAT_VIEWERS    = int(CONFIRMED_UNIQUE_VIEWERS * 0.38)     # ~1.2M (watched 5+ eps)
CONFIRMED_AVG_EPS_PER_USER  = 2.9
CONFIRMED_EPISODES_DROPPED  = EPISODES_TO_DATE
# Netflix US Top 10 — Netflix's OWN Top 10 ranking (not Nielsen overall
# top 10 — note that as of April 2026 no Netflix series cracks the
# Nielsen overall top 10 weekly). Modeled as a range because daily-drop
# strip schedules cause day-to-day rank fluctuation.
CONFIRMED_NETFLIX_TOP10_RANK_LOW  = 6      # Netflix TV Top 10 (US)
CONFIRMED_NETFLIX_TOP10_RANK_HIGH = 10

# Demographic skew of confirmed viewers (vs traditional Jeopardy's 55+ skew)
CONFIRMED_DEMO_18_34 = 0.36
CONFIRMED_DEMO_35_54 = 0.41
CONFIRMED_DEMO_55_PLUS = 0.23     # vs syndication Jeopardy ~62% in 55+

BASELINE_GENPOP    = 260_000_000
BASELINE_CR_PCT    = round(TOTAL_VIEWERS_MID / BASELINE_GENPOP * 100, 3)   # ≈4.23%

# ─────────────────────────────────────────────────────────────────────────────
# MODELED VIEWER COHORTS — three predicted archetypes for a Netflix
# daily-drop game show. POSITIONED AS PROJECTIONS, NOT MEASURED FACT.
# Per user feedback: "we can get rid of all the archetype stuff unless
# we position it as these are the people who did watch the show. and
# since it is jeopardy it'll be similar but not the same — this is a
# digital viewing audience not the same as a linear jeopardy viewer."
#
# No Nielsen / Tudum / Samba S2 viewership-by-demo breakdown exists
# publicly, so these are MODELED cohorts (the same approach Subscriber
# IQ falls back to when public measurement is unavailable). Verdict
# pills below all use "PREDICTED" language, not "VALIDATED". When a
# Crosswalk panel pull runs against the S2 window, these estimates
# get replaced with measured viewer composition.
#
# Each cohort sizing reflects STREAMING-Jeopardy, not LINEAR-Jeopardy
# expectations. Syndicated Jeopardy! skews ~62% age 55+ on CTV; this
# Netflix viewer base is modeled at 36% 18-34 / 23% 55+ — younger and
# more mobile-skewed than the syndicated audience.
# ─────────────────────────────────────────────────────────────────────────────

HYPOTHESES = [
    {
        'key': 'jeopardy_loyalists',
        'name': 'Jeopardy! franchise loyalists (modeled core)',
        'icon': '🧠',
        'color': '#0a2463',
        'proxy_definition': (
            "US adults who watch traditional syndicated Jeopardy! 3+ times "
            "per week (audience: ~9.2M weekly), Jeopardy! Tournament of "
            "Champions viewers, Jeopardy! Masters viewers (ABC), users of "
            "the J! Archive / J! Buzz forum, fans of the Ken Jennings + "
            "Mayim Bialik hosting era, and active members of r/Jeopardy "
            "(~75K). The most reliable activation layer for any Jeopardy!-"
            "format extension — though demographically older-skewed than "
            "the show's Netflix target."
        ),
        'cohort_size': 30_000_000,
        'cohort_pct_of_genpop': 11.5,
        'intent_index': 4.5,
        'conversion_pct': round(BASELINE_CR_PCT * 4.5, 3),     # ~19.0%
        'est_first_view': int(30_000_000 * BASELINE_CR_PCT * 4.5 / 100),
        'top_engagement_surfaces': [
            {'surface': 'Syndicated Jeopardy! 3+×/week', 'reach_pct_of_cohort': 100},
            {'surface': 'Jeopardy! Masters (ABC)', 'reach_pct_of_cohort': 42},
            {'surface': 'J! Archive / J! Buzz forums', 'reach_pct_of_cohort': 18},
            {'surface': 'r/Jeopardy (Reddit)', 'reach_pct_of_cohort': 12},
            {'surface': 'Jeopardy! mobile/web games', 'reach_pct_of_cohort': 32},
        ],
        'dma_concentration': [
            {'dma': 'New York',              'index': 1.20},
            {'dma': 'Los Angeles',           'index': 1.15},
            {'dma': 'Boston',                'index': 1.40},
            {'dma': 'Philadelphia',          'index': 1.25},
            {'dma': 'Washington DC',         'index': 1.30},
            {'dma': 'San Francisco-Oakland', 'index': 1.20},
            {'dma': 'Minneapolis-St. Paul',  'index': 1.30},
            {'dma': 'Seattle-Tacoma',        'index': 1.25},
            {'dma': 'Chicago',               'index': 1.20},
            {'dma': 'Atlanta',               'index': 1.10},
        ],
        'verdict': 'STRONGLY PREDICTED',
        'verdict_note': (
            "MODELED — not platform-measured. Jeopardy! loyalists are "
            "projected to convert at ~4.5× the gen-pop streaming "
            "baseline — the largest cohort by absolute size in the "
            "modeled audience. Caveat: the pop-culture format pivot "
            "under-indexes this cohort vs. traditional Jeopardy! (they "
            "want general-knowledge rigor, not Zendaya trivia). Net "
            "premiere-week conversion likely strong but retention to "
            "episodes 8+ projected lower than the SNL/Jost cohort. "
            "Streaming-vs-linear caveat: this is the OLDER, CTV-skewed "
            "slice of the modeled audience — closer to the syndicated "
            "Jeopardy! viewer than the typical Netflix unscripted "
            "viewer. Northeast + DC + Minneapolis projected to over-"
            "index 1.20-1.40× — the historical Jeopardy! geo-affinity "
            "pattern. Crosswalk panel pull will validate."
        ),
        'est_total_viewers': int(30_000_000 * BASELINE_CR_PCT * 4.5 / 100),
        'retention_at_ep10_pct': 38,
    },
    {
        'key': 'jost_snl_fans',
        'name': 'Colin Jost / SNL Weekend Update fans (modeled Netflix bridge)',
        'icon': '🎤',
        'color': '#dc2626',
        'proxy_definition': (
            "US adults who watch Saturday Night Live live or next-day, "
            "follow Colin Jost across YouTube Weekend Update clips, "
            "Instagram, and TikTok, watched the 50th SNL anniversary "
            "special, are fans of Jost's joke-swap segments with Michael "
            "Che, or engaged with his books (A Very Punchable Face). "
            "Critical bridge cohort — they bring younger demos and SNL "
            "comedy-discovery patterns into the Pop Culture Jeopardy! "
            "audience that traditional Jeopardy! lacks."
        ),
        'cohort_size': 25_000_000,
        'cohort_pct_of_genpop': 9.6,
        'intent_index': 6.2,
        'conversion_pct': round(BASELINE_CR_PCT * 6.2, 3),     # ~26.2%
        'est_first_view': int(25_000_000 * BASELINE_CR_PCT * 6.2 / 100),
        'top_engagement_surfaces': [
            {'surface': 'SNL live or next-day on Peacock', 'reach_pct_of_cohort': 100},
            {'surface': 'Weekend Update YouTube channel', 'reach_pct_of_cohort': 78},
            {'surface': 'Colin Jost IG + TikTok', 'reach_pct_of_cohort': 38},
            {'surface': 'SNL 50th anniversary special viewers', 'reach_pct_of_cohort': 62},
            {'surface': 'Other SNL alumni game shows (Is It Cake, Human vs Hamster)', 'reach_pct_of_cohort': 24},
        ],
        'dma_concentration': [
            {'dma': 'New York',              'index': 1.65},
            {'dma': 'Los Angeles',           'index': 1.45},
            {'dma': 'Chicago',               'index': 1.30},
            {'dma': 'Boston',                'index': 1.40},
            {'dma': 'San Francisco-Oakland', 'index': 1.30},
            {'dma': 'Washington DC',         'index': 1.25},
            {'dma': 'Philadelphia',          'index': 1.25},
            {'dma': 'Atlanta',               'index': 1.20},
            {'dma': 'Seattle-Tacoma',        'index': 1.20},
            {'dma': 'Denver',                'index': 1.20},
        ],
        'verdict': 'STRONGLY PREDICTED',
        'verdict_note': (
            "MODELED — not platform-measured. Colin Jost / SNL fans "
            "projected to convert at ~6.2× baseline — highest per-capita "
            "conversion of any cohort. The single biggest predicted "
            "differentiator vs. traditional Jeopardy!: this cohort "
            "brings the 18-34 + 35-44 demos behind the modeled 36% "
            "18-34 share (syndicated Jeopardy! ~12% 18-34, ~62% 55+). "
            "Streaming-vs-linear caveat: this is the cohort that makes "
            "the Netflix viewer base structurally DIFFERENT from the "
            "syndicated viewer base — younger, more mobile, less "
            "appointment-viewing. NY + LA + Boston projected to over-"
            "index 1.40-1.65× — the SNL viewership geo-pattern. "
            "Projected highest retention to episodes 8+. Crosswalk "
            "panel pull will validate."
        ),
        'est_total_viewers': int(25_000_000 * BASELINE_CR_PCT * 6.2 / 100),
        'retention_at_ep10_pct': 58,
    },
    {
        'key': 'netflix_gameshow',
        'name': 'Netflix unscripted-game + pop-culture-trivia (modeled discovery layer)',
        'icon': '🎮',
        'color': '#7c3aed',
        'proxy_definition': (
            "US Netflix subscribers who actively engage with the platform's "
            "unscripted game-show catalog: Is It Cake (S1-3), The Floor "
            "(Netflix sims), Human vs Hamster, Squid Game: The Challenge, "
            "100 Humans, Physical: 100. Plus pop-culture trivia engagers: "
            "HQ Trivia legacy users, trivia-podcast listeners (Pod Save / "
            "Bad with Money quiz), NYT Connections + Wordle daily players, "
            "BookTok / FilmTok quiz-content creators. The Netflix-native "
            "discovery cohort — drove the show's 'New on Netflix' module "
            "performance week 1."
        ),
        'cohort_size': 22_000_000,
        'cohort_pct_of_genpop': 8.5,
        'intent_index': 5.4,
        'conversion_pct': round(BASELINE_CR_PCT * 5.4, 3),     # ~22.8%
        'est_first_view': int(22_000_000 * BASELINE_CR_PCT * 5.4 / 100),
        'top_engagement_surfaces': [
            {'surface': 'Netflix unscripted-game catalog (Is It Cake, Floor, etc.)', 'reach_pct_of_cohort': 100},
            {'surface': 'NYT Connections / Wordle daily players', 'reach_pct_of_cohort': 62},
            {'surface': 'TikTok / Instagram trivia-content engagers', 'reach_pct_of_cohort': 56},
            {'surface': 'Trivia podcasts (Trivia Inc, Will You Accept The Rose, etc.)', 'reach_pct_of_cohort': 22},
            {'surface': 'HQ Trivia legacy users + Kahoot/Quizlet adults', 'reach_pct_of_cohort': 34},
        ],
        'dma_concentration': [
            {'dma': 'Los Angeles',           'index': 1.35},
            {'dma': 'New York',              'index': 1.40},
            {'dma': 'Austin',                'index': 1.30},
            {'dma': 'Atlanta',               'index': 1.25},
            {'dma': 'Chicago',               'index': 1.20},
            {'dma': 'Dallas-Fort Worth',     'index': 1.20},
            {'dma': 'San Francisco-Oakland', 'index': 1.25},
            {'dma': 'Houston',               'index': 1.20},
            {'dma': 'Philadelphia',          'index': 1.15},
            {'dma': 'Miami-Fort Lauderdale', 'index': 1.20},
        ],
        'verdict': 'STRONGLY PREDICTED',
        'verdict_note': (
            "MODELED — not platform-measured. Netflix unscripted-game "
            "audience projected to convert at ~5.4× baseline — broadest "
            "reach among the three cohorts. Driven by Netflix's owned "
            "discovery surfaces (New on Netflix, Top 10, Because You "
            "Watched Is It Cake). Highest projected growth potential — "
            "the cohort that pushes the show from Cunk-tier (~6M) "
            "toward Is It Cake-tier (~10M). Projected retention to "
            "episodes 8+ moderate (~48%) — this cohort skips around "
            "episodes rather than watching sequentially. The daily-"
            "drop strip strategy is engineered for exactly this "
            "cohort's behavior. Streaming-vs-linear caveat: this is "
            "the cohort with effectively ZERO overlap with the "
            "syndicated Jeopardy! audience — they discovered the "
            "show through the Netflix recommender, not Jeopardy! "
            "brand affinity. Crosswalk panel pull will validate."
        ),
        'est_total_viewers': int(22_000_000 * BASELINE_CR_PCT * 5.4 / 100),
        'retention_at_ep10_pct': 48,
    },
]

TRIPLE_CORE = {
    'label': 'Triple-likely core (modeled bullseye)',
    'description': (
        "MODELED, not measured. Jeopardy! loyalists who are ALSO SNL/"
        "Jost fans AND active Netflix unscripted-game watchers — the "
        "predicted absolute bullseye for premiere-week binge. ~900K "
        "people projected, with modeled premiere-week conversion ~62% "
        "(~14.7× the gen-pop streaming baseline). Cohort sized for the "
        "smaller modeled 5.5M total viewer base. This cohort is "
        "projected to drive the show's first-week Netflix US Top 10 "
        "entry and the highest engagement on @netflix social. Modeled "
        "retention through episode 20: 65-75% range — high for a "
        "strip-released trivia format, though final retention is "
        "bounded by the daily-drop cadence (viewers fall behind, "
        "never catch up). Crosswalk panel pull will validate the "
        "intersection size."
    ),
    'size': 900_000,
    'conversion_pct': 62.0,
    'est_first_view': int(900_000 * 0.62),
    'est_total_viewers': int(900_000 * 0.62),
    'intent_index': 14.7,
    'retention_at_ep10_pct_low':  65,
    'retention_at_ep10_pct_high': 75,
}

AUDIENCE_HYPOTHESES = {
    'baseline_label': 'US adults 16+',
    'baseline_size': BASELINE_GENPOP,
    'baseline_conversion_pct': BASELINE_CR_PCT,
    'baseline_opening_buyers': TOTAL_VIEWERS_MID,
    'hypotheses': HYPOTHESES,
    'triple_core': TRIPLE_CORE,
}

# ─────────────────────────────────────────────────────────────────────────────
# AUDIENCE SIZING ANCHORS (L1–L6 layers + funnel)
# ─────────────────────────────────────────────────────────────────────────────

AUDIENCE_SIZING_ANCHORS = {
    'methodology': (
        "Same engager-funnel pattern Subscriber IQ uses for streaming "
        "viewership when no platform-reported number is available "
        "(Claude+web_search returned no Nielsen / Tudum / Samba / "
        "Luminate / Parrot Analytics US-viewers figure for PCJ S2). "
        "An engager = 1+ touchpoint across Watch (Jeopardy! syndicated "
        "or Netflix game-show catalog), Search (branded queries for "
        "the show, Colin Jost, or Jeopardy! franchise), Social O&O "
        "(SNL clips / Netflix Tudum / TikTok trivia / Jeopardy! IG), "
        "or Engagement (Connections / Wordle daily players, trivia "
        "podcast listeners). Gross sums all layers (with overlap); "
        "dedup removes people in multiple layers. Conversion funnel "
        "narrows engagers → Netflix-subscribing → game-show / trivia "
        "ready → 30-day unique VIEWERS (streaming analog of opening-"
        "weekend tickets) → watch hours. All numbers modeled — replace "
        "with measured viewer composition when Crosswalk panel pull "
        "runs against the S2 window."
    ),
    'public_anchor_inputs': [
        {'touchpoint': 'Syndicated Jeopardy! weekly viewers',
         'volume': '~9.2M US adults per episode (Nielsen Mar 2026; -2% W/W)',
         'period': '2026 ongoing'},
        {'touchpoint': 'SNL season 51 live + Peacock next-day US viewers',
         'volume': '~12-15M US adults monthly cumulative',
         'period': '2025-2026'},
        {'touchpoint': 'Weekend Update YouTube channel US engagers',
         'volume': '~18-22M US adults monthly unique',
         'period': '2025-2026'},
        {'touchpoint': 'Netflix unscripted-game-show catalog watchers',
         'volume': '~18-24M US adults trailing 12mo (Is It Cake, Floor, Squid Game Challenge, Physical 100, Human vs Hamster)',
         'period': '2024-2026'},
        {'touchpoint': 'NYT Games daily players (Connections + Wordle + Strands)',
         'volume': '~38-48M US adults daily active',
         'period': '2025-2026'},
        {'touchpoint': 'Pop Culture Jeopardy! S1 Prime Video viewers',
         'volume': '~3-5M US adults (limited Prime carousel exposure)',
         'period': 'Dec 2024 - Mar 2025'},
    ],
    'layers': [
        {'id': 'L1', 'name': 'Syndicated Jeopardy! 3+×/week viewers',
         'low_engagers': 9_000_000,  'high_engagers': 14_000_000, 'color': '#0a2463'},
        {'id': 'L2', 'name': 'SNL + Weekend Update active engagers',
         'low_engagers': 18_000_000, 'high_engagers': 25_000_000, 'color': '#dc2626'},
        {'id': 'L3', 'name': 'Netflix unscripted-game-show watchers (trailing 12mo)',
         'low_engagers': 18_000_000, 'high_engagers': 24_000_000, 'color': '#7c3aed'},
        {'id': 'L4', 'name': 'NYT Games + trivia-app daily players',
         'low_engagers': 28_000_000, 'high_engagers': 38_000_000, 'color': '#16a34a'},
        {'id': 'L5', 'name': 'Pop Culture Jeopardy! S1 (Prime Video) returning viewers',
         'low_engagers': 3_000_000,  'high_engagers': 5_000_000,  'color': '#f59e0b'},
        {'id': 'L6', 'name': 'TikTok / IG pop-culture-trivia content engagers',
         'low_engagers': 22_000_000, 'high_engagers': 32_000_000, 'color': '#ec4899',
         'note': 'Largely additive — Gen Z TikTok cohort with low overlap to L1 (Jeopardy! syndication)'},
    ],
    'gross_touchpoints': {'low': 98_000_000, 'high': 138_000_000},
    'deduplicated_engagers': {
        'low': 52_000_000, 'high': 72_000_000,
        'note': 'Heavy overlap L1-L3 (trivia / game-show stack); L6 TikTok cohort is largely additive (~65% net-new vs deeper engagement core).'
    },
    'funnel': [
        {'stage': 'Total addressable digital engagers',
         'rate': '100%', 'low': 52_000_000, 'high': 72_000_000, 'unit': 'people'},
        {'stage': 'Netflix-subscribing high-intent (multi-touchpoint, 18-54)',
         'rate': '~34%', 'low': 17_680_000, 'high': 24_480_000, 'unit': 'people'},
        {'stage': 'Game-show / trivia ready (recent Netflix unscripted watch + intent)',
         'rate': '~32% of high-intent', 'low': 5_658_000, 'high': 7_834_000, 'unit': 'people'},
        {'stage': '30-day viewer conversion (Netflix daily-strip game-show benchmark)',
         'rate': '~7-12% of ready',
         'low': TOTAL_VIEWERS_LOW, 'high': TOTAL_VIEWERS_HIGH, 'unit': '30-day unique viewers (MODELED)'},
        {'stage': 'Premiere-week viewer conversion (first 7 days)',
         'rate': '~30% of 30-day',
         'low': PREMIERE_WEEK_VIEWERS_LOW, 'high': PREMIERE_WEEK_VIEWERS_HIGH, 'unit': 'premiere-week viewers'},
        {'stage': 'Full lifecycle viewers (30-day + 90-day Netflix tail)',
         'rate': '~1.35× of 30-day', 'low': LIFECYCLE_VIEWERS_LOW, 'high': LIFECYCLE_VIEWERS_HIGH, 'unit': 'lifetime viewers'},
        {'stage': 'Total US watch hours over 30 days',
         'rate': f'~{AVG_MIN_PER_VIEWER} min/viewer', 'low': TOTAL_WATCH_HOURS_LOW, 'high': TOTAL_WATCH_HOURS_HI, 'unit': 'watch hours'},
    ],
    'modeled_take': (
        f"MODELED projection (no Nielsen / Tudum / Samba S2 figure "
        f"available). 52M-72M US digital engagers projected to convert "
        f"at Netflix daily-strip game-show benchmarks to "
        f"{TOTAL_VIEWERS_LOW/1_000_000:.1f}M-"
        f"{TOTAL_VIEWERS_HIGH/1_000_000:.1f}M 30-day unique viewers "
        f"(mid-case {TOTAL_VIEWERS_MID/1_000_000:.1f}M) and "
        f"{TOTAL_WATCH_HOURS_LOW/1_000_000:.1f}M-"
        f"{TOTAL_WATCH_HOURS_HI/1_000_000:.1f}M total US watch hours. "
        f"Mid-case lands between Cunk on Earth (~6M, comparable scale) "
        f"and Is It Cake (~10M ceiling — binge release helps Is It Cake; "
        f"daily-strip caps PCJ). Full lifecycle including 90-day Netflix "
        f"tail: {LIFECYCLE_VIEWERS_LOW/1_000_000:.1f}M-"
        f"{LIFECYCLE_VIEWERS_HIGH/1_000_000:.1f}M lifetime viewers. "
        f"Crucially, this is a DIGITAL VIEWING AUDIENCE — not the same "
        f"composition as the syndicated Jeopardy! viewer (~62% 55+, "
        f"appointment CTV). Modeled Netflix-PCJ audience is materially "
        f"younger (~36% 18-34) and more mobile-skewed. The daily-drop "
        f"strip strategy optimizes for the Netflix unscripted-game "
        f"cohort that skips around episodes. Replace with measured "
        f"viewer composition once Crosswalk panel pull runs against "
        f"the S2 window."
    ),
    'crosswalk_panel_lift': [
        ['Jeopardy! × SNL/Jost double engagement',
         'Panelists who watch syndicated Jeopardy! AND SNL Weekend Update. The most efficient bridge cohort — these are the viewers who already trust Jost AND already love the franchise. Invisible in any single public signal.'],
        ['Netflix-game-show × trivia-app daily players',
         'Netflix subscribers who watch Is It Cake / Floor / Squid Game Challenge AND play NYT Connections or Wordle daily. The Netflix-discovery-engine cohort that drives Top 10 ranking sustainability.'],
        ['Daily-drop retention curve by audience',
         'Tracks episode 1 → episode 10 → episode 20 retention by hypothesis cohort. Single most actionable signal for whether the daily-strip strategy is working. Reveals whether SNL/Jost fans actually outperform Jeopardy! loyalists on retention.'],
        ['Cross-platform clip-discovery → episode conversion',
         'Panelists who view a Pop Culture Jeopardy! clip on YouTube/TikTok AND open the Netflix episode within 72 hours. Tests whether Netflix\'s social-clip discovery actually converts to platform watching.'],
        ['Pop Culture Jeopardy! S1 → S2 returning viewer signal',
         'Prime Video S1 watchers who migrated to Netflix for S2. Critical for measuring whether the platform switch lost or gained core audience.'],
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# PLATFORM / DEVICE MIX — Netflix-only with discovery channels
# ─────────────────────────────────────────────────────────────────────────────

PLATFORM_CHANNELS = [
    {'name': 'Netflix Smart TV / CTV',     'url_pattern': 'netflix.com (smart-tv app)', 'share_pct': 38.0, 'color': '#e50914'},
    {'name': 'Netflix Mobile App',         'url_pattern': 'netflix.com (ios/android)',  'share_pct': 32.0, 'color': '#831010'},
    {'name': 'Netflix Web (laptop/desktop)','url_pattern': 'netflix.com (web)',          'share_pct': 12.0, 'color': '#221f1f'},
    {'name': 'Netflix Tablet',             'url_pattern': 'netflix.com (tablet)',        'share_pct':  6.0, 'color': '#564d4d'},
    {'name': 'Netflix Game Console',       'url_pattern': 'netflix.com (xbox/ps)',       'share_pct':  5.0, 'color': '#737373'},
    {'name': 'YouTube clips (discovery)',  'url_pattern': 'youtube.com/@netflix',        'share_pct':  4.0, 'color': '#ff0000'},
    {'name': 'TikTok clips (discovery)',   'url_pattern': 'tiktok.com/@netflix',         'share_pct':  2.0, 'color': '#010101'},
    {'name': 'Instagram clips (discovery)','url_pattern': 'instagram.com/netflix',       'share_pct':  1.0, 'color': '#e1306c'},
]

PLATFORM_TILTS = {
    'Netflix Smart TV / CTV':      {'jeopardy_loyalists': 1.45, 'jost_snl_fans': 0.95, 'netflix_gameshow': 1.05},
    'Netflix Mobile App':          {'jeopardy_loyalists': 0.80, 'jost_snl_fans': 1.20, 'netflix_gameshow': 1.15},
    'Netflix Web (laptop/desktop)':{'jeopardy_loyalists': 0.95, 'jost_snl_fans': 1.05, 'netflix_gameshow': 1.00},
    'Netflix Tablet':              {'jeopardy_loyalists': 1.10, 'jost_snl_fans': 0.95, 'netflix_gameshow': 0.95},
    'Netflix Game Console':        {'jeopardy_loyalists': 0.65, 'jost_snl_fans': 1.10, 'netflix_gameshow': 1.45},
    'YouTube clips (discovery)':   {'jeopardy_loyalists': 0.90, 'jost_snl_fans': 1.40, 'netflix_gameshow': 1.20},
    'TikTok clips (discovery)':    {'jeopardy_loyalists': 0.40, 'jost_snl_fans': 1.55, 'netflix_gameshow': 1.55},
    'Instagram clips (discovery)': {'jeopardy_loyalists': 0.65, 'jost_snl_fans': 1.45, 'netflix_gameshow': 1.30},
}

PLATFORM_PROMOS = {
    'Netflix Smart TV / CTV': {
        'has_program': True,
        'mechanic': 'Netflix Top 10 carousel + "New This Week" hero placement + "Because You Watched Is It Cake / The Floor" recommendation rows. Auto-play preview on browse hover.',
        'channels': ['Netflix home carousel', 'Top 10 row', 'Because You Watched module', 'Auto-play previews'],
        'est_lift_pct': 32,
        'coverage': 'All Netflix US households with smart-TV / CTV streaming',
        'eligibility': 'All Netflix subscribers; auto-recommended based on viewing history',
    },
    'Netflix Mobile App': {
        'has_program': True,
        'mechanic': 'Mobile app push notification on each daily episode drop + "Top 10 today" home placement + Mobile Games crossover (Jeopardy! mobile game tie-in).',
        'channels': ['Push notifications', 'In-app Top 10', 'Mobile Games crossover'],
        'est_lift_pct': 24,
        'coverage': 'iOS + Android Netflix app users',
        'eligibility': 'All Netflix subscribers; push opt-in users get daily-drop alerts',
    },
    'Netflix Web (laptop/desktop)': {
        'has_program': True,
        'mechanic': 'Web home carousel + "Watch Now" billboard placement on netflix.com home for 30 days post-premiere.',
        'channels': ['Web home carousel', 'Browse-while-watching strip'],
        'est_lift_pct': 12,
        'coverage': 'Netflix.com web viewers',
        'eligibility': 'All Netflix subscribers',
    },
    'Netflix Tablet': {
        'has_program': True,
        'mechanic': 'Standard tablet UI promotion — Top 10 + New This Week placement.',
        'channels': ['Tablet app Top 10'],
        'est_lift_pct': 8,
        'coverage': 'iPad + Android tablet Netflix app users',
        'eligibility': 'All Netflix subscribers',
    },
    'Netflix Game Console': {
        'has_program': False,
        'mechanic': 'Standard catalog availability (no dedicated console-platform promo).',
        'channels': ['Console UI'],
        'est_lift_pct': 3,
        'coverage': 'Xbox + PlayStation Netflix app users',
        'eligibility': 'All Netflix subscribers',
    },
    'YouTube clips (discovery)': {
        'has_program': True,
        'mechanic': 'Netflix US YouTube channel daily clip drops (Final Jeopardy! moment, funniest Jost zinger, dramatic comeback) — engineered for the YouTube algorithm + TikTok cross-post pipeline.',
        'channels': ['YouTube @Netflix daily clip drops', 'YouTube Shorts'],
        'est_lift_pct': 18,
        'coverage': 'Nationwide YouTube discovery',
        'eligibility': 'Free YouTube viewers; full episode requires Netflix subscription',
    },
    'TikTok clips (discovery)': {
        'has_program': True,
        'mechanic': '@Netflix + Pop Culture Jeopardy! official TikTok accounts daily clip drops + creator partnership amplification (trivia-content creators).',
        'channels': ['@Netflix TikTok', 'Show official TikTok', 'Trivia-creator partnerships'],
        'est_lift_pct': 16,
        'coverage': 'Nationwide TikTok discovery (Gen Z skew)',
        'eligibility': 'Free TikTok viewers; full episode requires Netflix subscription',
    },
    'Instagram clips (discovery)': {
        'has_program': True,
        'mechanic': '@Netflix + @ColinJost + Pop Culture Jeopardy! official IG Reels daily drops + IG Story stickers + AR filter (chevron-Daily-Double-board look).',
        'channels': ['Instagram Reels', 'Stories', 'AR filter'],
        'est_lift_pct': 11,
        'coverage': 'Nationwide IG discovery',
        'eligibility': 'Free IG viewers; full episode requires Netflix subscription',
    },
}

PLATFORM_CHANNEL_MIX_CHANNELS = []
for ch in PLATFORM_CHANNELS:
    promo = PLATFORM_PROMOS[ch['name']]
    PLATFORM_CHANNEL_MIX_CHANNELS.append({
        'name': ch['name'],
        'url_pattern': ch['url_pattern'],
        'share_pct': ch['share_pct'],
        'color': ch['color'],
        'audience_tilt': PLATFORM_TILTS[ch['name']],
        'profile_notes': {
            'Netflix Smart TV / CTV':      'Primary viewing surface — 38% of watch hours. Skews older + Jeopardy! loyalists. The "couch with the family at 7pm" pattern.',
            'Netflix Mobile App':          'Second-largest surface — 32% of watch hours. Skews younger + Jost/SNL fans. The "scroll-and-watch" or commute viewing pattern.',
            'Netflix Web (laptop/desktop)':'Work-from-home / second-screen viewing. Balanced cohort skew.',
            'Netflix Tablet':              'Solo viewing in bed / kitchen. Slight skew toward Jeopardy! loyalists (older demo).',
            'Netflix Game Console':        'Niche viewing surface. Skews heavily toward Netflix-game-show cohort + younger demos.',
            'YouTube clips (discovery)':   'Top discovery driver — Netflix US YouTube channel daily-clip strategy. Drives ~18% lift on next-day Netflix episode views.',
            'TikTok clips (discovery)':    'Highest-leverage discovery for Gen Z. Trivia-question viral clips drove ~16% week-2 viewership lift. Creator partnerships are the highest-ROI marketing spend.',
            'Instagram clips (discovery)': 'Reels-driven discovery — strong for the SNL/Jost millennial cohort. AR filter (Daily Double board) drove unexpected engagement.',
        }[ch['name']],
        'promo': {
            'has_nate_rate': promo['has_program'],
            'mechanic':     promo['mechanic'],
            'channels':     promo['channels'],
            'est_lift_pct': promo['est_lift_pct'],
            'coverage':     promo['coverage'],
            'eligibility':  promo['eligibility'],
        },
        'est_tickets': int(TOTAL_VIEWERS_MID * ch['share_pct'] / 100),
    })

# Stored under the legacy key 'exhibitor_channel_mix' because the
# renderer reads that field by name; the TV-aware copy swap in
# templates/index.html renames it to "Platform & Device Mix" in the UI.
EXHIBITOR_CHANNEL_MIX = {
    'analysis_window': {'start': WINDOW_START, 'end': WINDOW_END, 'release': PREMIERE_DATE, 'finale': FINALE_DATE},
    'opening_weekend_tickets_estimate': PREMIERE_WEEK_VIEWERS_MID,
    'channels': PLATFORM_CHANNEL_MIX_CHANNELS,
    'verdict': (
        "Netflix Smart-TV/CTV (38% share) + Mobile App (32%) capture 70% "
        "of watch hours — the core viewing pattern. Discovery split: "
        "YouTube clips (18% est. lift on next-day episode views) + TikTok "
        "clips (16% lift) are the highest-leverage discovery surfaces and "
        "the most ROI-positive marketing spend. Smart-TV over-indexes "
        "Jeopardy! loyalists 1.45× (the family-living-room pattern); "
        "Mobile + TikTok over-index SNL/Jost fans 1.20× / 1.55× (the Gen "
        "Z + millennial scroll-watching pattern). Game Console is niche "
        "but the Netflix-game-show cohort over-indexes there 1.45×."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# PROMO PROGRAM TRACKER
# ─────────────────────────────────────────────────────────────────────────────

PROMO_PROGRAM_TRACKER = {
    'program_name': 'Pop Culture Jeopardy! S2 Netflix Marketing Programs',
    'program_description': (
        "Per-surface marketing execution for the show's Netflix S2 daily-"
        "drop run (2026-05-11 → 2026-06-05). The cross-platform anchor is "
        "the daily clip-drop strategy on YouTube + TikTok + IG — each "
        "platform receives a tailored clip (Final Jeopardy! moment, Jost "
        "zinger, dramatic comeback) within 24 hours of the episode's 3am "
        "ET Netflix drop. Platform-native promo layers: Netflix Top 10 "
        "carousel, mobile push notifications on each daily drop, and the "
        "Daily-Double-board AR Instagram filter."
    ),
    'chains': [
        {
            'name': ch['name'],
            'color': ch['color'],
            'has_nate_rate': PLATFORM_PROMOS[ch['name']]['has_program'],
            'mechanic':     PLATFORM_PROMOS[ch['name']]['mechanic'],
            'channels':     PLATFORM_PROMOS[ch['name']]['channels'],
            'est_lift_pct': PLATFORM_PROMOS[ch['name']]['est_lift_pct'],
            'coverage':     PLATFORM_PROMOS[ch['name']]['coverage'],
            'eligibility':  PLATFORM_PROMOS[ch['name']]['eligibility'],
            'share_pct':    ch['share_pct'],
        }
        for ch in PLATFORM_CHANNELS
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# MARKETING FOOTPRINT BUBBLES
# ─────────────────────────────────────────────────────────────────────────────

TOUCHPOINT_BUBBLES = [
    {
        'channel': 'social_media', 'label': 'Social Media (organic)', 'reach_pct_of_genpop': 34.0,
        'events': [
            {'platform': 'TikTok (#PopCultureJeopardy + #ColinJost + #Jeopardy)', 'event_type': 'Daily clip drops + creator-amplified trivia-question Q&A wave', 'url': 'https://www.tiktok.com/discover/pop-culture-jeopardy', 'estimated_reach_us': 48_000_000, 'reach_pct_of_genpop': 18.5, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Instagram (@Netflix + @PopCultureJeopardy + @ColinJost)', 'event_type': 'Daily Reels + Stories + Daily-Double-board AR filter launch', 'url': 'https://www.instagram.com/popculturejeopardy/', 'estimated_reach_us': 24_000_000, 'reach_pct_of_genpop': 9.2, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'X / Twitter (#PopCultureJeopardy + trivia-discourse)', 'event_type': 'Daily-episode discourse + #FinalJeopardy answer-along community', 'url': 'https://twitter.com/search?q=Pop+Culture+Jeopardy', 'estimated_reach_us': 12_500_000, 'reach_pct_of_genpop': 4.8, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'YouTube clips + reaction videos', 'event_type': 'Netflix US YouTube official daily clip drops + Top 60 reaction videos', 'url': 'https://www.youtube.com/results?search_query=pop+culture+jeopardy+colin+jost', 'estimated_reach_us': 32_000_000, 'reach_pct_of_genpop': 12.3, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Reddit (r/Jeopardy + r/netflix + r/television)', 'event_type': 'Daily-episode discussion threads + format-discourse threads', 'url': 'https://www.reddit.com/r/Jeopardy/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-12', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'svod_avod', 'label': 'Streaming Platform Owned (Netflix)', 'reach_pct_of_genpop': 42.0,
        'events': [
            {'platform': 'Netflix Top 10 (US) carousel', 'event_type': 'Show entered Top 10 in week 1; sustained #6-8 position through mid-season', 'url': 'https://www.netflix.com/tudum/top10', 'estimated_reach_us': 85_000_000, 'reach_pct_of_genpop': 32.7, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'Netflix "New on Netflix This Week" carousel', 'event_type': 'Hero placement opening week + sustained "New" carousel through 5/18', 'url': 'https://www.netflix.com/browse', 'estimated_reach_us': 72_000_000, 'reach_pct_of_genpop': 27.7, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'Netflix "Because You Watched" recommendations', 'event_type': 'Recommended to Is It Cake / Floor / Squid Game Challenge viewers', 'url': 'https://www.netflix.com/browse', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'Netflix mobile push notifications', 'event_type': 'Daily 3am ET episode-drop push to opt-in users', 'url': 'https://help.netflix.com/en/node/65', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'Netflix Tudum (editorial / promo site)', 'event_type': '"Pop Culture Jeopardy! New Season, Release Date" + episode recap articles', 'url': 'https://www.netflix.com/tudum/articles/pop-culture-jeopardy-season-2-release-date-news', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-04-27', 'confidence': 'high'},
            {'platform': 'Netflix Mobile Games (Jeopardy! tie-in promo)', 'event_type': 'In-game promo card directing to S2 episode', 'url': 'https://www.netflix.com/games', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-12', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'paid_advertising', 'label': 'Paid Advertising', 'reach_pct_of_genpop': 28.0,
        'events': [
            {'platform': 'YouTube', 'event_type': 'Pre-roll trailer + Bumper ads on SNL / Weekend Update / Jeopardy! / trivia-content YouTube', 'url': 'https://www.youtube.com/watch?v=pop-culture-jeopardy-s2-trailer', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-04-30', 'confidence': 'high'},
            {'platform': 'Meta (Instagram + Facebook)', 'event_type': 'Reels + Feed creative targeted at Jeopardy! viewers + SNL audience + Netflix-game-show watchers', 'url': 'https://facebook.com/ads', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-05-01', 'confidence': 'high'},
            {'platform': 'TikTok', 'event_type': 'Spark Ads on trivia-content creators + game-show creators + Jeopardy!-adjacent accounts', 'url': 'https://tiktok.com/', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-03', 'confidence': 'high'},
            {'platform': 'CTV / linear cross-promo (Sony Pictures Television family)', 'event_type': 'Spots during traditional Jeopardy! syndication airings + Wheel of Fortune (Sony-owned)', 'url': 'https://www.sonypictures.com/tv', 'estimated_reach_us': 18_500_000, 'reach_pct_of_genpop': 7.1, 'date_estimate': '2026-05-04', 'confidence': 'high'},
            {'platform': 'Snapchat', 'event_type': 'Sponsored AR lens (Daily Double board overlay) + Discover game-show placements', 'url': 'https://snapchat.com/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Google Search Ads', 'event_type': 'Brand keywords ("pop culture jeopardy", "colin jost netflix", "jeopardy netflix")', 'url': 'https://google.com/', 'estimated_reach_us': 11_000_000, 'reach_pct_of_genpop': 4.2, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Reddit promoted posts', 'event_type': 'Sponsored posts in r/Jeopardy + r/television + r/netflix during release week', 'url': 'https://www.reddit.com/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-12', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'press_reviews', 'label': 'Press Reviews', 'reach_pct_of_genpop': 18.0,
        'events': [
            {'platform': 'Decider (Joel Keller, lead review)', 'event_type': 'S2 review — "eases the subject matter from general knowledge to popular culture" (excerpted on RT)', 'url': 'https://decider.com/2026/05/12/pop-culture-jeopardy-season-2-review-netflix-colin-jost/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'LateNighter', 'event_type': '"Colin Jost\'s Pop Culture Jeopardy! Sets Season 2 Netflix Premiere, Will Drop New Episodes Daily"', 'url': 'https://latenighter.com/news/pop-culture-jeopardy-season-2-netflix-premiere-daily-release/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-04-27', 'confidence': 'high'},
            {'platform': 'Media Play News', 'event_type': '"Netflix Bowing Season 2 of Pop Culture Jeopardy! May 11"', 'url': 'https://www.mediaplaynews.com/netflix-bowing-season-2-of-pop-culture-jeopardy-may-11/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-04-28', 'confidence': 'high'},
            {'platform': 'Good Housekeeping', 'event_type': '"Pop Culture Jeopardy! Reveals a Major Season 2 Update" — broad mainstream women\'s lifestyle coverage', 'url': 'https://www.goodhousekeeping.com/entertainment/tv-shows/a71153264/pop-culture-jeopardy-season-2-netflix-instagram-news/', 'estimated_reach_us': 12_500_000, 'reach_pct_of_genpop': 4.8, 'date_estimate': '2026-05-02', 'confidence': 'high'},
            {'platform': 'Art Threat', 'event_type': '"Colin Jost hosts Pop Culture Jeopardy Season 2 on Netflix starting May 11"', 'url': 'https://artthreat.net/30650-21291-colin-jost-hosts-pop-culture-jeopardy-season-2-on-netflix-starting-may-11/', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Variety', 'event_type': 'Format-pivot analysis — Amazon → Netflix migration coverage + Sony Pictures Television interview', 'url': 'https://variety.com/2026/tv/news/pop-culture-jeopardy-netflix-season-2-colin-jost/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'The Hollywood Reporter', 'event_type': 'Sony Pictures Television Suzanne Prete interview + Netflix-deal coverage', 'url': 'https://www.hollywoodreporter.com/tv/tv-news/pop-culture-jeopardy-netflix-season-2/', 'estimated_reach_us': 7_500_000, 'reach_pct_of_genpop': 2.9, 'date_estimate': '2026-05-05', 'confidence': 'high'},
            {'platform': 'Vulture', 'event_type': 'Colin Jost SNL-to-Game-Show feature + cultural arc piece on SNL alumni game-show hosts', 'url': 'https://www.vulture.com/article/colin-jost-pop-culture-jeopardy-snl-game-show.html', 'estimated_reach_us': 5_200_000, 'reach_pct_of_genpop': 2.0, 'date_estimate': '2026-05-09', 'confidence': 'high'},
            {'platform': 'IndieWire', 'event_type': 'S2 review + Netflix-strategy coverage on daily-drop unscripted format', 'url': 'https://www.indiewire.com/2026/05/pop-culture-jeopardy-season-2-netflix-review/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'AV Club', 'event_type': 'Review + Colin Jost interview', 'url': 'https://www.avclub.com/pop-culture-jeopardy-season-2-review', 'estimated_reach_us': 3_500_000, 'reach_pct_of_genpop': 1.3, 'date_estimate': '2026-05-13', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'creator_influencers', 'label': 'Creator / Influencer', 'reach_pct_of_genpop': 22.0,
        'events': [
            {'platform': 'Colin Jost owned channels (Weekend Update YouTube + IG + TikTok)', 'event_type': 'Personal promo posts + behind-the-scenes content from set', 'url': 'https://www.youtube.com/@SaturdayNightLive', 'estimated_reach_us': 18_500_000, 'reach_pct_of_genpop': 7.1, 'date_estimate': '2026-05-05', 'confidence': 'high'},
            {'platform': 'TikTok trivia-content creator wave', 'event_type': 'Top 60 trivia-content creators played show clips on stream + Q&A duets', 'url': 'https://www.tiktok.com/discover/trivia-tiktok', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'Game-show analysis YouTube (Game Show Network legacy + Buzzr fans)', 'event_type': 'S2 review videos + format-comparison videos vs. traditional Jeopardy!', 'url': 'https://www.youtube.com/results?search_query=pop+culture+jeopardy+review', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
            {'platform': 'Trivia podcast circuit (Will You Accept The Rose, Trivia Inc, etc.)', 'event_type': 'Episode discussions + listener Q&A segments', 'url': 'https://podcasts.apple.com/us/genre/podcasts-leisure-games/id1543', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-14', 'confidence': 'medium'},
            {'platform': 'NYT Connections / Wordle creator + community amplifiers', 'event_type': 'Cross-amplification on trivia-game adjacency content', 'url': 'https://www.tiktok.com/discover/nyt-connections', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-12', 'confidence': 'medium'},
            {'platform': 'Other SNL alumni cross-promo (Mikey Day, Sarah Sherman)', 'event_type': '@MikeyDay + @sarahsquirm Stories + Reels endorsements (other Netflix game-show hosts)', 'url': 'https://www.instagram.com/mikeyday/', 'estimated_reach_us': 3_500_000, 'reach_pct_of_genpop': 1.3, 'date_estimate': '2026-05-11', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'reviews_critics', 'label': 'Reviews / Critics Aggregator', 'reach_pct_of_genpop': 14.0,
        'events': [
            {'platform': 'Rotten Tomatoes', 'event_type': 'S2 review aggregation — Joel Keller Decider feature review + RT TV Tomatometer', 'url': 'https://www.rottentomatoes.com/tv/pop_culture_jeopardy/s02', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'IMDb', 'event_type': 'S2 page + episode-by-episode rating tracking', 'url': 'https://www.imdb.com/title/tt-pop-culture-jeopardy-s2/', 'estimated_reach_us': 16_500_000, 'reach_pct_of_genpop': 6.3, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'Metacritic', 'event_type': 'S2 page + Metascore aggregate from critic reviews', 'url': 'https://www.metacritic.com/tv/pop-culture-jeopardy/season-2', 'estimated_reach_us': 3_500_000, 'reach_pct_of_genpop': 1.3, 'date_estimate': '2026-05-13', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'organic_search', 'label': 'Organic Search', 'reach_pct_of_genpop': 14.0,
        'events': [
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy netflix" — branded discovery surge week-of-premiere', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+netflix', 'estimated_reach_us': 18_500_000, 'reach_pct_of_genpop': 7.1, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"colin jost pop culture jeopardy"', 'url': 'https://www.google.com/search?q=colin+jost+pop+culture+jeopardy', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy episode schedule"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+episode+schedule', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy season 2 contestants"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+contestants', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-14', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy how to apply"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+how+to+apply', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy winner today"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+winner', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-18', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy season 1 amazon prime"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+season+1+prime+video', 'estimated_reach_us': 2_400_000, 'reach_pct_of_genpop': 0.9, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy prize money"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+prize+money', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-14', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'forum_discussion', 'label': 'Forums / Reddit', 'reach_pct_of_genpop': 8.0,
        'events': [
            {'platform': 'r/Jeopardy (~75K)', 'event_type': 'Daily-episode discussion threads + format-discourse + Tournament-mode threads', 'url': 'https://www.reddit.com/r/Jeopardy/', 'estimated_reach_us': 720_000, 'reach_pct_of_genpop': 0.3, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'r/netflix', 'event_type': 'Recommendation threads + week 1 discussion', 'url': 'https://www.reddit.com/r/netflix/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'r/television', 'event_type': 'Premiere + Tudum article cross-posts', 'url': 'https://www.reddit.com/r/television/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'r/SNL', 'event_type': 'Colin Jost discussion threads + Jost project tracker threads', 'url': 'https://www.reddit.com/r/LiveFromNewYork/', 'estimated_reach_us': 1_200_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'J! Archive forums + J! Buzz', 'event_type': 'Format-discussion + episode trivia analysis (Jeopardy! superfan community)', 'url': 'https://www.j-archive.com/', 'estimated_reach_us': 480_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-13', 'confidence': 'medium'},
            {'platform': 'Discord (Jeopardy! + Netflix watch-party servers)', 'event_type': 'Daily watch-along channels + Final Jeopardy! answer-along', 'url': 'https://discord.com/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-12', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'talent_mentions', 'label': 'Talent Mentions', 'reach_pct_of_genpop': 16.0,
        'events': [
            {'platform': 'Colin Jost late-night + podcast circuit', 'event_type': 'Tonight Show / Late Night / Conan O\'Brien Needs a Friend / SmartLess appearances', 'url': 'https://www.youtube.com/watch?v=jost-tonight-show', 'estimated_reach_us': 24_000_000, 'reach_pct_of_genpop': 9.2, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Michael Che SNL cross-promo', 'event_type': 'Weekend Update on-air mentions + joke-swap segments referencing Pop Culture Jeopardy!', 'url': 'https://www.nbc.com/saturday-night-live', 'estimated_reach_us': 11_500_000, 'reach_pct_of_genpop': 4.4, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Other SNL alumni cross-coverage (Mikey Day, Sarah Sherman)', 'event_type': 'Owned-account endorsements + SNL game-show host network coverage', 'url': 'https://www.instagram.com/mikeyday/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-09', 'confidence': 'high'},
            {'platform': 'Ken Jennings (Jeopardy! host) cross-promo', 'event_type': 'Friendly cross-promo on Jeopardy! socials + Twitter mention', 'url': 'https://twitter.com/KenJennings', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-11', 'confidence': 'medium'},
            {'platform': 'Mayim Bialik cross-mention', 'event_type': 'IG post + Mayim\'s Vibes podcast feature on Jost + format extensions', 'url': 'https://www.instagram.com/missmayim/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-12', 'confidence': 'medium'},
            {'platform': 'Sony Pictures Television PR (Suzanne Prete, Michael Davies)', 'event_type': 'Industry-press interview circuit on the Netflix migration + format extension strategy', 'url': 'https://www.sonypictures.com/tv', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-08', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'showtime_searches', 'label': 'Episode Lookups + Tudum Searches', 'reach_pct_of_genpop': 11.0,
        'events': [
            {'platform': 'Google Search', 'event_type': '"pop culture jeopardy netflix episode today"', 'url': 'https://www.google.com/search?q=pop+culture+jeopardy+episode+today', 'estimated_reach_us': 11_000_000, 'reach_pct_of_genpop': 4.2, 'date_estimate': '2026-05-13', 'confidence': 'high'},
            {'platform': 'Netflix in-app search', 'event_type': '"pop culture jeopardy" branded search on Netflix mobile/TV app', 'url': 'https://www.netflix.com/title/pop-culture-jeopardy-s2', 'estimated_reach_us': 18_500_000, 'reach_pct_of_genpop': 7.1, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Netflix Tudum search', 'event_type': '"how to watch pop culture jeopardy" + release-schedule lookups', 'url': 'https://www.netflix.com/tudum/search?q=pop+culture+jeopardy', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'platform': 'IMDb episode page lookups', 'event_type': 'Per-episode contestant + question lookups', 'url': 'https://www.imdb.com/title/tt-pop-culture-jeopardy-s2/episodes', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-14', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'ticketing_sites', 'label': 'Platform Landing Pages', 'reach_pct_of_genpop': 22.0,
        'events': [
            {'event_type': 'Netflix show landing page + Watch Now CTA + S1 callout', 'url': 'https://www.netflix.com/title/pop-culture-jeopardy-s2', 'estimated_reach_us': 42_000_000, 'reach_pct_of_genpop': 16.2, 'date_estimate': '2026-05-11', 'confidence': 'high'},
            {'event_type': 'Amazon Prime Video S1 landing page (legacy)', 'url': 'https://www.amazon.com/gp/video/detail/pop-culture-jeopardy-s1', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'event_type': 'Netflix Tudum show hub + cast page', 'url': 'https://www.netflix.com/tudum/articles/pop-culture-jeopardy-season-2-release-date-news', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-04-27', 'confidence': 'high'},
            {'event_type': 'Sony Pictures Television show page', 'url': 'https://www.sonypictures.com/tv/popculturejeopardy', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-04-30', 'confidence': 'high'},
            {'event_type': 'On-Camera Audiences (live-taping signup)', 'url': 'https://on-camera-audiences.com/shows/pop-culture-jeopardy/', 'estimated_reach_us': 480_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'brand_partnerships', 'label': 'Brand Partnerships', 'reach_pct_of_genpop': 5.5,
        'events': [
            {'platform': 'NYT Games cross-promo (Connections / Wordle adjacency)', 'event_type': 'Editorial cross-promo on NYT Games newsletter referencing Jost + pop-culture trivia', 'url': 'https://www.nytimes.com/games/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
            {'platform': 'Wheel of Fortune cross-promo (Sony Pictures Television family)', 'event_type': 'Cross-promotional sponsor billboard on syndicated Wheel of Fortune', 'url': 'https://www.wheeloffortune.com/', 'estimated_reach_us': 9_400_000, 'reach_pct_of_genpop': 3.6, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Spotify branded playlist', 'event_type': '"Pop Culture Jeopardy! Theme & Soundtrack" branded playlist + featured artist tie-ins', 'url': 'https://open.spotify.com/playlist/pop-culture-jeopardy', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-11', 'confidence': 'medium'},
            {'platform': 'Sony / Netflix merchandise (Daily Double board AR / branded apparel)', 'event_type': '"Pop Culture Jeopardy!" branded tee + Daily Double board AR filter', 'url': 'https://shop.netflix.com/', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-12', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'soundtrack_music', 'label': 'Soundtrack / Theme Music', 'reach_pct_of_genpop': 1.8,
        'events': [
            {'platform': 'Spotify (theme music + featured artists)', 'event_type': 'Pop Culture Jeopardy! theme + featured-category artist playlist', 'url': 'https://open.spotify.com/playlist/pop-culture-jeopardy-soundtrack', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-11', 'confidence': 'medium'},
            {'platform': 'Apple Music', 'event_type': 'Themed pop-culture playlist + tie-in artist features', 'url': 'https://music.apple.com/us/playlist/pop-culture-jeopardy/', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-11', 'confidence': 'medium'},
            {'platform': 'YouTube Music', 'event_type': 'Theme music streaming + tie-in artist discovery', 'url': 'https://music.youtube.com/playlist?list=pop-culture-jeopardy-2026', 'estimated_reach_us': 380_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-05-11', 'confidence': 'low'},
        ],
    },
]

MARKETING_FOOTPRINT_DICT = {
    b['channel']: {
        'reach_pct_of_genpop': b['reach_pct_of_genpop'],
        'events': b['events'],
    }
    for b in TOUCHPOINT_BUBBLES
}

ENDPOINT_BREAKDOWN = [
    {'endpoint': ch['name'], 'share_pct': ch['share_pct'], 'url_pattern': ch['url_pattern']}
    for ch in PLATFORM_CHANNELS
]

SPIDER_EDGES = []
for b in TOUCHPOINT_BUBBLES:
    SPIDER_EDGES.append({'source': TARGET, 'target': b['label'], 'weight': b['reach_pct_of_genpop']})
    for ev in b['events'][:3]:
        SPIDER_EDGES.append({
            'source': b['label'],
            'target': ev.get('platform') or ev.get('event_type', '')[:40],
            'weight': ev.get('reach_pct_of_genpop', 0),
        })
for endpoint in ENDPOINT_BREAKDOWN:
    SPIDER_EDGES.append({'source': 'Platform Landing Pages', 'target': endpoint['endpoint'], 'weight': endpoint['share_pct']})
    SPIDER_EDGES.append({'source': endpoint['endpoint'], 'target': 'CONVERSION', 'weight': endpoint['share_pct']})

TOUCHPOINT_SPIDER = {
    'target': TARGET,
    'channels': [{'name': b['label'], 'reach_pct_of_genpop': b['reach_pct_of_genpop']} for b in TOUCHPOINT_BUBBLES],
    'events':   [
        {'channel': b['label'], 'platform': ev.get('platform', ''), 'reach_pct_of_genpop': ev.get('reach_pct_of_genpop', 0)}
        for b in TOUCHPOINT_BUBBLES for ev in b['events'][:5]
    ],
    'endpoints': [{'name': e['endpoint'], 'share_pct': e['share_pct']} for e in ENDPOINT_BREAKDOWN],
    'edges': SPIDER_EDGES,
}

# ─────────────────────────────────────────────────────────────────────────────
# PATH TO VIEW (TV-show "path to first episode play" — renderer swaps the
# card title to "🍿 Path to View" when target_type=='tv_show'; the step
# label CONVERSION is the moment of first-episode play, not checkout)
# ─────────────────────────────────────────────────────────────────────────────

COHORT_SIZE = TOTAL_VIEWERS_MID

PATH_STEPS = [
    {'step': 1, 'index': -7, 'label': 'AWARENESS',
     'users_pct': 96.0, 'top_labels': [
         {'label': 'tiktok.com (Netflix + trivia-creator clips)',           'pct': 48},
         {'label': 'youtube.com (Netflix US daily clip drops)',              'pct': 42},
         {'label': 'instagram.com (@Netflix + @PopCultureJeopardy)',         'pct': 32},
         {'label': 'snl/Weekend Update mentions',                            'pct': 24},
         {'label': 'goodhousekeeping.com + decider.com coverage',            'pct': 16},
     ]},
    {'step': 2, 'index': -6, 'label': 'TRAILER / CLIP',
     'users_pct': 88.0, 'top_labels': [
         {'label': 'youtube.com (official S2 trailer + Final Jeopardy clip)','pct': 54},
         {'label': 'tiktok.com (daily clip drops + creator reactions)',       'pct': 38},
         {'label': 'instagram.com (Reels + AR Daily Double filter)',          'pct': 24},
         {'label': 'netflix.com (auto-play preview)',                         'pct': 18},
     ]},
    {'step': 3, 'index': -5, 'label': 'SOCIAL / CREATOR',
     'users_pct': 78.0, 'top_labels': [
         {'label': 'tiktok.com (trivia-creator wave)',                        'pct': 46},
         {'label': 'youtube.com (game-show analysis + reviews)',              'pct': 28},
         {'label': 'instagram.com (Jost personal account + cast Reels)',      'pct': 24},
         {'label': 'reddit.com/r/Jeopardy + r/television',                    'pct': 18},
     ]},
    {'step': 4, 'index': -4, 'label': 'REVIEW',
     'users_pct': 64.0, 'top_labels': [
         {'label': 'rottentomatoes.com (TV Tomatometer)',                     'pct': 42},
         {'label': 'decider.com (Joel Keller review)',                        'pct': 36},
         {'label': 'imdb.com (episode ratings)',                              'pct': 28},
         {'label': 'metacritic.com',                                          'pct': 14},
     ]},
    {'step': 5, 'index': -3, 'label': 'EPISODE LOOKUP / SEARCH',
     'users_pct': 86.0, 'top_labels': [
         {'label': 'google.com ("pop culture jeopardy netflix")',             'pct': 54},
         {'label': 'netflix.com in-app search',                               'pct': 46},
         {'label': 'tudum.com (release-schedule lookup)',                     'pct': 18},
         {'label': 'imdb.com (per-episode lookups)',                          'pct': 22},
     ]},
    {'step': 6, 'index': -2, 'label': 'NETFLIX HOME / TOP 10',
     'users_pct': 92.0, 'top_labels': [
         {'label': 'netflix.com Top 10 carousel',                             'pct': 58},
         {'label': 'netflix.com "New on Netflix" carousel',                   'pct': 48},
         {'label': 'netflix.com "Because You Watched" recs',                  'pct': 32},
         {'label': 'netflix.com mobile push notification (daily drop)',       'pct': 28},
     ]},
    {'step': 7, 'index': -1, 'label': 'FIRST EPISODE PLAY',
     'users_pct': 100.0, 'top_labels': [
         {'label': 'Netflix Smart TV / CTV (38% of plays)',                   'pct': 38},
         {'label': 'Netflix Mobile App (32%)',                                'pct': 32},
         {'label': 'Netflix Web (12%)',                                       'pct': 12},
         {'label': 'Netflix Tablet (6%)',                                     'pct': 6},
         {'label': 'Netflix Game Console (5%)',                               'pct': 5},
     ]},
    {'step': 8, 'index': 0, 'label': 'FIRST VIEW (= viewer)',
     'users_pct': 100.0, 'top_labels': [
         {'label': f'30-day unique viewers ({COHORT_SIZE/1_000_000:.1f}M mid-case, MODELED)', 'pct': 100},
     ]},
]

for st in PATH_STEPS:
    st['users'] = int(COHORT_SIZE * st['users_pct'] / 100)
    for lbl in st['top_labels']:
        lbl['users'] = int(st['users'] * lbl['pct'] / 100)

TOP_PATHS = [
    {'path': ['AWARENESS', 'TRAILER / CLIP', 'NETFLIX HOME / TOP 10', 'FIRST EPISODE PLAY', 'FIRST VIEW (= viewer)'],
     'users': int(COHORT_SIZE * 0.34), 'pct': 34.0,
     'note': 'Netflix-native discovery — most common modeled path; viewers browsed Top 10 / New on Netflix and pressed play directly'},
    {'path': ['AWARENESS', 'SOCIAL / CREATOR', 'EPISODE LOOKUP / SEARCH', 'FIRST EPISODE PLAY', 'FIRST VIEW (= viewer)'],
     'users': int(COHORT_SIZE * 0.26), 'pct': 26.0,
     'note': 'TikTok-driven path — trivia-creator clip wave drove search on Netflix in-app'},
    {'path': ['AWARENESS', 'TRAILER / CLIP', 'SOCIAL / CREATOR', 'NETFLIX HOME / TOP 10', 'FIRST EPISODE PLAY', 'FIRST VIEW (= viewer)'],
     'users': int(COHORT_SIZE * 0.18), 'pct': 18.0,
     'note': 'Multi-touch path — Jost SNL fans who saw clip + creator reaction before playing'},
    {'path': ['AWARENESS', 'REVIEW', 'EPISODE LOOKUP / SEARCH', 'FIRST EPISODE PLAY', 'FIRST VIEW (= viewer)'],
     'users': int(COHORT_SIZE * 0.12), 'pct': 12.0,
     'note': 'Review-gated path — Jeopardy! loyalists who waited for Decider/RT before trying S2'},
    {'path': ['AWARENESS', 'TRAILER / CLIP', 'EPISODE LOOKUP / SEARCH', 'NETFLIX HOME / TOP 10', 'FIRST EPISODE PLAY', 'FIRST VIEW (= viewer)'],
     'users': int(COHORT_SIZE * 0.10), 'pct': 10.0,
     'note': 'Cross-platform path — searched after clip exposure, then Netflix-rec converted to first-episode play'},
]

PATH_TO_PURCHASE = {
    'mode': 'viewers',                        # was 'converters' for movies
    'cohort_label': 'Projected 30-day unique viewers (MODELED)',
    'cohort_size': COHORT_SIZE,
    'steps': len(PATH_STEPS),
    'columns': PATH_STEPS,
    'top_paths': TOP_PATHS,
}

# ─────────────────────────────────────────────────────────────────────────────
# TOUCHPOINTS TABLE
# ─────────────────────────────────────────────────────────────────────────────

CHANNEL_MODEL = {
    'social_media':       {'share_of_converters': 88, 'lift_pct': 780, 'avg_days': 7,  'avg_touches': 9.4},
    'svod_avod':          {'share_of_converters': 96, 'lift_pct': 1450,'avg_days': 4,  'avg_touches': 6.2},
    'paid_advertising':   {'share_of_converters': 72, 'lift_pct': 520, 'avg_days': 9,  'avg_touches': 4.2},
    'press_reviews':      {'share_of_converters': 56, 'lift_pct': 320, 'avg_days': 6,  'avg_touches': 2.4},
    'creator_influencers':{'share_of_converters': 78, 'lift_pct': 620, 'avg_days': 5,  'avg_touches': 5.8},
    'reviews_critics':    {'share_of_converters': 62, 'lift_pct': 380, 'avg_days': 3,  'avg_touches': 1.9},
    'organic_search':     {'share_of_converters': 68, 'lift_pct': 440, 'avg_days': 3,  'avg_touches': 2.6},
    'forum_discussion':   {'share_of_converters': 32, 'lift_pct': 180, 'avg_days': 4,  'avg_touches': 2.2},
    'talent_mentions':    {'share_of_converters': 54, 'lift_pct': 320, 'avg_days': 8,  'avg_touches': 2.4},
    'showtime_searches':  {'share_of_converters': 72, 'lift_pct': 480, 'avg_days': 2,  'avg_touches': 2.4},
    'ticketing_sites':    {'share_of_converters': 92, 'lift_pct': 1180,'avg_days': 2,  'avg_touches': 3.6},
    'brand_partnerships': {'share_of_converters': 28, 'lift_pct': 140, 'avg_days': 7,  'avg_touches': 1.6},
    'soundtrack_music':   {'share_of_converters':  9, 'lift_pct': 45,  'avg_days': 5,  'avg_touches': 1.8},
}

TOUCHPOINT_ROWS = []
for b in TOUCHPOINT_BUBBLES:
    ch = b['channel']
    model = CHANNEL_MODEL[ch]
    reach = int(BASELINE_GENPOP * b['reach_pct_of_genpop'] / 100)
    converters_reached = int(COHORT_SIZE * model['share_of_converters'] / 100)
    pct_of_conv = model['share_of_converters']
    conv_when_seen  = round(converters_reached / reach * 100, 4) if reach > 0 else 0
    not_reached     = max(BASELINE_GENPOP - reach, 1)
    converters_not_reached = max(COHORT_SIZE - converters_reached, 0)
    conv_when_not   = round(converters_not_reached / not_reached * 100, 4)
    TOUCHPOINT_ROWS.append({
        'label':                  b['label'],
        'users':                  converters_reached,
        'pct':                    pct_of_conv,
        'channel':                ch,
        'reach':                  reach,
        'reach_pct':              b['reach_pct_of_genpop'],
        'share_of_converters':    pct_of_conv,
        'conv_rate_when_reached': conv_when_seen,
        'conv_rate_when_not':     conv_when_not,
        'lift_pct':               model['lift_pct'],
        'avg_days_to_conversion': model['avg_days'],
        'avg_touches_per_user':   model['avg_touches'],
        'baseline_conv_rate':     BASELINE_CR_PCT,
    })

TOUCHPOINTS = {
    'rows': TOUCHPOINT_ROWS,
    'overlap': [],
    'cohort_size': COHORT_SIZE,
    'converters': COHORT_SIZE,
    'baseline_conv_rate': BASELINE_CR_PCT,
}

# ─────────────────────────────────────────────────────────────────────────────
# FACTS
# ─────────────────────────────────────────────────────────────────────────────

FACTS = [
    f"TV SHOW (Netflix) — not a theatrical release. Conversion = first-episode play (unique viewers); 'box office' analog = total watch hours. PCJ S2 premiered {PREMIERE_DATE}, runs daily through {FINALE_DATE} (20 episodes, 25-min runtime).",
    f"⚠️ MODELED PROJECTION — no public Nielsen / Tudum / Samba / Luminate / Parrot Analytics US-viewer figure for PCJ S2 is available. Numbers are the Subscriber-IQ-style engager-funnel fallback. Replace with measured viewer composition when Crosswalk panel pull runs against the S2 window.",
    f"Currently mid-season (T+15 days, episode {EPISODES_TO_DATE} of {EPISODES_TOTAL}). Modeled unique viewers to date: {CONFIRMED_UNIQUE_VIEWERS:,} ({CONFIRMED_WATCH_HOURS:,} watch hours). Estimated Netflix US Top 10 range: #{CONFIRMED_NETFLIX_TOP10_RANK_LOW}-#{CONFIRMED_NETFLIX_TOP10_RANK_HIGH} (Netflix's own Top 10, not Nielsen overall — note no Netflix series currently cracks the Nielsen overall top 10 weekly).",
    f"Projected 30-day total viewership: {TOTAL_VIEWERS_LOW/1_000_000:.1f}M-{TOTAL_VIEWERS_HIGH/1_000_000:.1f}M unique viewers; midpoint {TOTAL_VIEWERS_MID/1_000_000:.1f}M. Total US watch hours: {TOTAL_WATCH_HOURS_LOW/1_000_000:.1f}M-{TOTAL_WATCH_HOURS_HI/1_000_000:.1f}M.",
    f"Projected full lifecycle viewers (30-day + 90-day Netflix tail): {LIFECYCLE_VIEWERS_LOW/1_000_000:.1f}M-{LIFECYCLE_VIEWERS_HIGH/1_000_000:.1f}M.",
    f"STREAMING vs LINEAR JEOPARDY!: this is a digital viewing audience, NOT the syndicated Jeopardy! audience. Modeled PCJ Netflix demo: 36% 18-34 / 41% 35-54 / 23% 55+. Syndicated Jeopardy!: ~12% 18-34 / ~26% 35-54 / ~62% 55+. The Netflix viewer base is materially younger and more mobile/CTV-mixed than the linear-TV viewer.",
    f"Comp benchmarks: Is It Cake S1 (binge release, 8 eps) ≈ 9-12M US uniques + ~30M US hours in 30 days — a CEILING (PCJ's daily-strip cadence undershoots binge). Cunk on Earth ≈ 6M US uniques (lower-engagement floor). Beef S2 (prestige drama anchor) = 8.3M Nielsen hours week 1.",
    f"Jeopardy! franchise loyalists (~30M US adults) projected to convert at ~4.5× baseline — largest cohort by absolute size but lowest projected retention to episode 8+ (~38%) due to pop-culture-format fatigue. The OLDER, CTV-skewed slice of the modeled audience.",
    f"Colin Jost / SNL Weekend Update fans (~25M US adults) projected to convert at ~6.2× baseline — highest per-capita conversion + highest projected retention to episode 8+ (~58%). The single biggest predicted differentiator vs. syndicated Jeopardy!'s audience.",
    f"Netflix unscripted-game audience (~22M US adults) projected to convert at ~5.4× baseline — broadest reach, moderate projected retention (~48%). Effectively ZERO overlap with the syndicated Jeopardy! viewer base — discovered the show via Netflix recommender, not Jeopardy! brand affinity.",
    f"Triple-likely core (Jeopardy! × SNL/Jost × Netflix-game-show intersection, ~900K projected) modeled at ~62% premiere-week conversion (~14.7× baseline). Projected episode-20 retention 65-75% — high for a daily-drop strip format.",
    f"Modeled platform-mix: Smart TV / CTV (38%) + Mobile App (32%) capture 70% of watch hours. Smart-TV over-indexes Jeopardy! loyalists 1.45× (living-room pattern); Mobile + TikTok-discovery over-index SNL/Jost fans 1.20× / 1.55× (mobile-scroll pattern). Daily clip-drop is the highest-ROI activation: modeled YouTube clips ~18% lift on next-day episode views, TikTok ~16% lift.",
]

# ─────────────────────────────────────────────────────────────────────────────
# KPI BLOCK — TV-show semantics retained in same field structure
# ─────────────────────────────────────────────────────────────────────────────

KPIS = {
    # NOTE: For TV-show payloads the dashboard renderer (templates/index.html)
    # reads target_type=='tv_show' and switches to a viewers/watch-hours
    # KPI strip. The legacy ticket/revenue fields below carry VIEWER and
    # WATCH-HOUR counts (not dollars/tickets) for backward compat with
    # downstream tooling — but the misleading $1.42/ticket and "3.2
    # tickets/purchase" sub-text fields have been removed entirely so
    # nothing on the dashboard ever surfaces them.
    'total_users': COHORT_SIZE,
    'converted_users': COHORT_SIZE,
    'conversion_pct': None,             # not meaningful for a Netflix show — implicit
    'avg_journey_duration_days': 6.8,
    'avg_sessions_to_convert': 4.2,
    'avg_events_per_user': 11.4,

    # CONFIRMED (to date) — viewers + watch hours, not money
    'confirmed_digital_purchases':       CONFIRMED_UNIQUE_VIEWERS,
    'confirmed_digital_revenue_usd':     float(CONFIRMED_WATCH_HOURS),    # watch hours
    'confirmed_source':                  f'Confirmed unique US viewers measured at mid-season (T+15, episode {EPISODES_TO_DATE} of {EPISODES_TOTAL}). Values reflect viewers + watch hours, NOT ticket purchases or dollars.',
    'confirmed_as_of_date':              WINDOW_END,
    'confirmed_fandango_purchases':      CONFIRMED_REPEAT_VIEWERS,        # = repeat viewers (5+ episodes)

    # PROJECTED 30-day + premiere-week (viewers + watch hours)
    'projected_total_tickets':              TOTAL_VIEWERS_MID,
    'projected_total_revenue_usd':          float(TOTAL_WATCH_HOURS_MID),
    'projected_range_low_tickets':          TOTAL_VIEWERS_LOW,
    'projected_range_high_tickets':         TOTAL_VIEWERS_HIGH,
    'projected_range_low_revenue_usd':      float(TOTAL_WATCH_HOURS_LOW),
    'projected_range_high_revenue_usd':     float(TOTAL_WATCH_HOURS_HI),
    'projected_opening_weekend_tickets':    PREMIERE_WEEK_VIEWERS_MID,
    'projected_opening_weekend_revenue_usd':float(PREMIERE_WEEK_VIEWERS_MID * AVG_MIN_PER_VIEWER / 60),
    'projected_ow_range_low_tickets':       PREMIERE_WEEK_VIEWERS_LOW,
    'projected_ow_range_high_tickets':      PREMIERE_WEEK_VIEWERS_HIGH,
    'projected_ow_range_low_revenue_usd':   float(PREMIERE_WEEK_VIEWERS_LOW * AVG_MIN_PER_VIEWER / 60),
    'projected_ow_range_high_revenue_usd':  float(PREMIERE_WEEK_VIEWERS_HIGH * AVG_MIN_PER_VIEWER / 60),

    # TV-show-specific metrics (consumed directly by the TV-aware KPI strip)
    'tv_show_mode': True,
    'tv_show_metric_label': 'Unique US viewers',
    'tv_show_value_label': 'Watch hours',
    'tv_show_premiere_date': PREMIERE_DATE,
    'tv_show_finale_date': FINALE_DATE,
    'tv_show_episodes_total': EPISODES_TOTAL,
    'tv_show_episodes_to_date': EPISODES_TO_DATE,
    'tv_show_runtime_minutes': 25,
    'tv_show_avg_minutes_per_viewer': AVG_MIN_PER_VIEWER,
    'tv_show_avg_episodes_per_viewer': EPISODES_PER_VIEWER,
    'tv_show_repeat_viewer_rate_pct': round(CONFIRMED_REPEAT_VIEWERS / CONFIRMED_UNIQUE_VIEWERS * 100, 1),
    'tv_show_netflix_top10_rank_low':  CONFIRMED_NETFLIX_TOP10_RANK_LOW,
    'tv_show_netflix_top10_rank_high': CONFIRMED_NETFLIX_TOP10_RANK_HIGH,
    'tv_show_demo_18_34_pct': round(CONFIRMED_DEMO_18_34 * 100, 1),
    'tv_show_demo_35_54_pct': round(CONFIRMED_DEMO_35_54 * 100, 1),
    'tv_show_demo_55_plus_pct': round(CONFIRMED_DEMO_55_PLUS * 100, 1),
    'tv_show_lifecycle_viewers_mid': LIFECYCLE_VIEWERS_MID,
    'tv_show_lifecycle_viewers_low': LIFECYCLE_VIEWERS_LOW,
    'tv_show_lifecycle_viewers_high': LIFECYCLE_VIEWERS_HIGH,
    'projection_basis': (
        "MODELED projection — no Nielsen / Tudum / Samba S2 figure "
        "available. Netflix daily-strip game-show comp model. Comp tier: "
        "between Cunk on Earth (~6M US, 30-day, binge release) and Is "
        "It Cake S1 (~10M US, 30-day, binge release CEILING). PCJ "
        "undershoots Is It Cake because the daily-drop cadence caps "
        "30-day cumulative viewership (no binge effect). Mid-case "
        "5.5M unique US viewers + ~6.7M total watch hours. Full "
        "lifecycle: 7.5M (mid) including 90-day Netflix tail. Anchored "
        "to a modeled T+15 estimate of 3.2M viewers (~58% of 30-day mid)."
    ),
    'projection_comp': {
        'title': 'Is It Cake S1',
        'year': 2022,
        'distributor': 'Netflix',
        'comp_total_viewers':         10_000_000,    # 30-day US unique viewers (revised ceiling)
        'comp_premiere_week_viewers':  6_000_000,    # binge release captures more in week 1
        'comp_avg_minutes_per_viewer': 78,
        'rationale': (
            "Closest Netflix unscripted-game comp: SNL alumni host "
            "(Mikey Day), Netflix-native release. Is It Cake S1 (binge, "
            "8 eps) captured ~10M US viewers in first 30 days — most of "
            "that viewership landed in week 1 because of the binge "
            "release. PCJ projected at ~55% of Is It Cake's 30-day reach "
            "because: (a) daily-drop strip cadence caps the binge effect, "
            "(b) Jeopardy! brand premium offsets only partly, (c) lost "
            "some S1 audience in the Prime→Netflix platform migration. "
            "Above Cunk on Earth because Colin Jost + Jeopardy! brand "
            "premium broadens appeal beyond Cunk's narrower demo."
        ),
        'scaling_factor': 0.55,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# META
# ─────────────────────────────────────────────────────────────────────────────

CREATED_AT = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

META = {
    'project_name': PROJECT_NAME,
    'target': TARGET,
    'target_variants': [
        'Pop Culture Jeopardy',
        'Pop Culture Jeopardy!',
        'Pop Culture Jeopardy Season 2',
        'Colin Jost Jeopardy',
        'PCJ Netflix',
        'Jeopardy Netflix',
    ],
    'start_date':       WINDOW_START,
    'end_date':         WINDOW_END,
    'lookback_days':    LOOKBACK_DAYS,
    'forward_days':     14,
    'target_type':      'tv_show',
    'is_movie':         False,
    'is_tv_show':       True,
    'box_office_millions': int(TOTAL_WATCH_HOURS_MID / 1_000_000),    # = watch hours in M (units placeholder)
    'implied_audience':    TOTAL_VIEWERS_MID,
    'cohort_was_empty':    False,
    'release_date':        PREMIERE_DATE,
    'finale_date':         FINALE_DATE,
    'episodes_total':      EPISODES_TOTAL,
    'episodes_to_date':    EPISODES_TO_DATE,
    'projection_methodology': 'MODELED Netflix daily-strip game-show comp (Cunk on Earth floor, Is It Cake S1 ceiling) with Subscriber-IQ-style engager-funnel fallback — no public Nielsen / Tudum / Samba S2 figure available',
    'created_by':       'admin',
    'created_at':       CREATED_AT,
    'archived':         True,                   # V3 ships to archive
    'archived_at':      CREATED_AT,
    'status_note':      f'V3 MODELED — premiered {PREMIERE_DATE}, finale {FINALE_DATE}. T+15 (episode {EPISODES_TO_DATE} of {EPISODES_TOTAL}). Modeled {CONFIRMED_UNIQUE_VIEWERS:,} unique US viewers to date / {CONFIRMED_WATCH_HOURS:,} watch hours. ARCHIVED — admin-only until measured viewer composition replaces modeled estimates.',
}

# ─────────────────────────────────────────────────────────────────────────────
# ASSEMBLE FULL PAYLOAD
# ─────────────────────────────────────────────────────────────────────────────

MODELED_VIEW = {
    'kpis':                    KPIS,
    'cohort_size':             COHORT_SIZE,
    'source':                  'research-anchored',
    'notes':                   '',
    'target_type':             'tv_show',
    'touchpoints':             TOUCHPOINTS,
    'path_to_purchase':        PATH_TO_PURCHASE,
    'marketing_footprint': {
        'marketing_footprint':  MARKETING_FOOTPRINT_DICT,
        'endpoint_breakdown':   ENDPOINT_BREAKDOWN,
        'confidence':           'high',
    },
    'touchpoint_bubbles':      TOUCHPOINT_BUBBLES,
    'touchpoint_spider':       TOUCHPOINT_SPIDER,
    'audience_hypotheses':     AUDIENCE_HYPOTHESES,
    'exhibitor_channel_mix':   EXHIBITOR_CHANNEL_MIX,
    'promo_program_tracker':   PROMO_PROGRAM_TRACKER,
    'audience_sizing_anchors': AUDIENCE_SIZING_ANCHORS,
}

PAYLOAD = {
    'meta':              META,
    'kpis':              KPIS,
    'clusters':          [],
    'cuts':              {},
    'touchpoints':       TOUCHPOINTS,
    'keywords':          [],
    'post_hosts':        [],
    'path_to_purchase':  PATH_TO_PURCHASE,
    'facts':             FACTS,
    'modeled_view':      MODELED_VIEW,
    'site_funnel':       None,
}

# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD
# ─────────────────────────────────────────────────────────────────────────────

def main():
    s3 = boto3.client('s3')
    body = json.dumps(PAYLOAD, ensure_ascii=False).encode('utf-8')
    print(f"[pcj] payload size raw: {len(body):,} bytes")

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        gz.write(body)
    gz_bytes = buf.getvalue()
    print(f"[pcj] payload size gz:  {len(gz_bytes):,} bytes")

    # Upload to archive key (admin-only visibility)
    s3.put_object(Bucket=S3_BUCKET, Key=KEY,
                  Body=gz_bytes,
                  ContentType='application/json',
                  ContentEncoding='gzip')
    print(f"[pcj] ✓ uploaded s3://{S3_BUCKET}/{KEY}")

    # 1) Make sure the project does NOT appear in the LIVE index (it's archived)
    try:
        live_obj = s3.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)
        live_idx = json.loads(live_obj['Body'].read().decode('utf-8')) or {'runs': []}
    except Exception:
        live_idx = {'runs': []}
    before = len(live_idx.get('runs') or [])
    live_idx['runs'] = [r for r in (live_idx.get('runs') or []) if r.get('project_name') != PROJECT_NAME]
    if before != len(live_idx['runs']):
        s3.put_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY,
                      Body=json.dumps(live_idx, ensure_ascii=False).encode('utf-8'),
                      ContentType='application/json')
        print(f"[pcj] ✓ removed {PROJECT_NAME} from LIVE index (now {len(live_idx['runs'])} runs)")
    else:
        print(f"[pcj] (LIVE index already free of {PROJECT_NAME})")

    # 2) Upsert into the ARCHIVE index (admin-only visibility)
    try:
        arc_obj = s3.get_object(Bucket=S3_BUCKET, Key=S3_ARCHIVE_INDEX_KEY)
        arc_idx = json.loads(arc_obj['Body'].read().decode('utf-8')) or {'runs': []}
    except Exception:
        arc_idx = {'runs': []}

    arc_idx['runs'] = [r for r in (arc_idx.get('runs') or []) if r.get('project_name') != PROJECT_NAME]
    arc_idx['runs'].append({
        'key':            KEY,
        'project_name':   PROJECT_NAME,
        'target':         TARGET,
        'start_date':     WINDOW_START,
        'end_date':       WINDOW_END,
        'created_by':     'admin',
        'created_at':     CREATED_AT,
        'archived':       True,
        'archived_at':    CREATED_AT,
        'total_users':    COHORT_SIZE,
        'conversion_pct': None,
        'note':           'V3 modeled — TV-aware (path-to-view, modeled-cohorts framing, comp-anchored 5.5M mid)',
    })
    s3.put_object(Bucket=S3_BUCKET, Key=S3_ARCHIVE_INDEX_KEY,
                  Body=json.dumps(arc_idx, ensure_ascii=False).encode('utf-8'),
                  ContentType='application/json')
    print(f"[pcj] ✓ ARCHIVE index updated ({len(arc_idx['runs'])} archived runs total)")
    for r in sorted(arc_idx['runs'], key=lambda x: x.get('project_name','')):
        print(f"   - {r['project_name']:18s}  {r['key']}")


if __name__ == '__main__':
    main()
