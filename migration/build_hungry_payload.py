"""Build + upload the HUNGRY Journey IQ payload to S3.

HUNGRY (2026) — James Nunn (writer/director, Shark Bait, One Shot trilogy,
Wildcat). British survival-horror creature feature about a killer hippo
stalking tourists on a Louisiana bayou riverboat tour. Production: Signature
Entertainment (UK). US distribution: Aura Entertainment.

Cast: Madison Davenport (Sharp Objects, It's What's Inside, From Dusk Till
Dawn series), Joaquim de Almeida (Fast Five, Desperado, Queen of the South),
Tracey Bonner (The Exorcism, Cobra Kai), Jim Meskimen (Parks and Recreation),
Michel Curiel (She-Hulk), Samantha Coughlan (Arcadian), Olivia Bernstone,
River Codack.

CRITICAL CONTEXT — this is a VOD-PRIMARY release, NOT a wide theatrical.
  - Limited theatrical: 2026-06-01 (bumped up from June 23 per HorrorFuel)
  - Primary VOD release: 2026-06-23 (Aura Entertainment, all major platforms)
  - 93 minute runtime
  - Trailer dropped April 20, 2026 — went viral as the "Hungry Hungry
    Hippos but horror" meme on TikTok / Twitter / IG

This means the projection model is fundamentally different vs Obsession.
Theatrical = ~50-80 screen limited release for marketing-event purposes;
real revenue lives in VOD digital rentals/purchases + downstream AVOD.

Three audience archetypes:
  1. Creature-feature genre heads (the core — Crawl/Beast/Cocaine Bear tier)
  2. B-movie "so bad it's good" cult (Sharknado, Asylum, Tubi B-horror)
  3. "Hungry Hungry Hippos horror" meme-curiosity audience (viral TikTok cohort)

Comp anchor: Black Water: Abyss (2020 Saban Films crocodile horror) — exact
tier match. Sanity-check secondary comp: Crawl (2019, Paramount, $39M dom
wide release) scaled down by ~95% for this tier of limited release.
"""

import gzip
import io
import json
from datetime import datetime, timezone

import boto3

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

S3_BUCKET    = 'dashboard-inputs'
S3_INDEX_KEY = 'journey-iq/_index.json'

PROJECT_NAME = 'HUNGRY'
TARGET       = 'Hungry'
TIMESTAMP    = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
KEY          = f'journey-iq/admin/{PROJECT_NAME}_full_{TIMESTAMP}.json.gz'

THEATRICAL_DATE = '2026-06-01'      # limited theatrical (bumped from June 23)
VOD_DATE        = '2026-06-23'      # primary release — Aura Entertainment
WINDOW_START    = '2026-04-15'      # trailer drop window
WINDOW_END      = '2026-05-26'      # as of today
LOOKBACK_DAYS   = 42

# ── Theatrical model (VERY LIMITED — ~50-80 screen marketing-event release)
# Comp tier: Black Water: Abyss (Saban Films, 2020, ~$200K limited theatrical).
# This is not a wide release — theatrical exists primarily to qualify for
# review coverage + drive VOD downstream awareness.
OW_TICKETS_MID    = 30_769          # ~$400K @ $13 avg, ~70 screens
OW_TICKETS_LOW    = 11_538          # ~$150K, 30 screens
OW_TICKETS_HIGH   = 65_385          # ~$850K, 100 screens, surprise hit
OW_REVENUE_MID    = 400_000
OW_REVENUE_LOW    = 150_000
OW_REVENUE_HIGH   = 850_000

# Limited theatrical multiplier — burns off very fast (~2 weekends), so
# the OW→total multiplier is low (~2.4× vs 3.6× for sleeper-wide horror).
TOTAL_MULTIPLIER  = 2.4
TOTAL_TICKETS     = int(OW_TICKETS_MID  * TOTAL_MULTIPLIER)       # ~74K
TOTAL_TICKETS_LO  = int(OW_TICKETS_LOW  * TOTAL_MULTIPLIER)       # ~28K
TOTAL_TICKETS_HI  = int(OW_TICKETS_HIGH * TOTAL_MULTIPLIER)       # ~157K
TOTAL_GROSS_USD   = int(OW_REVENUE_MID  * TOTAL_MULTIPLIER)       # ~$960K
TOTAL_GROSS_LO    = int(OW_REVENUE_LOW  * TOTAL_MULTIPLIER)       # ~$360K
TOTAL_GROSS_HI    = int(OW_REVENUE_HIGH * TOTAL_MULTIPLIER)       # ~$2.04M

NATIONAL_AVG_TICKET = 13.0
ONLINE_AVG_TICKET   = 13.5

# ── VOD revenue projection (THE REAL STORY — primary revenue stream)
# 90-day post-release rentals + purchases on Amazon/Apple/Vudu/Google Play/
# Fandango at Home/YouTube Movies/Microsoft. Comp: Black Water: Abyss
# (~700K rentals, ~$4M gross GMV); Cocaine Bear (~3M+ first-30-day digital
# downloads after theatrical) — Hungry sits closer to the Black Water tier
# but with stronger trailer-driven meme awareness.
VOD_RENTALS_LO    = 720_000
VOD_RENTALS_MID   = 1_180_000
VOD_RENTALS_HI    = 1_900_000
VOD_AVG_RENTAL    = 5.99
VOD_PURCHASES_LO  = int(VOD_RENTALS_LO * 0.22)       # ~158K
VOD_PURCHASES_MID = int(VOD_RENTALS_MID * 0.25)      # ~295K
VOD_PURCHASES_HI  = int(VOD_RENTALS_HI * 0.30)       # ~570K
VOD_AVG_PURCHASE  = 19.99
VOD_GMV_LO        = int(VOD_RENTALS_LO * VOD_AVG_RENTAL + VOD_PURCHASES_LO * VOD_AVG_PURCHASE)
VOD_GMV_MID       = int(VOD_RENTALS_MID * VOD_AVG_RENTAL + VOD_PURCHASES_MID * VOD_AVG_PURCHASE)
VOD_GMV_HI        = int(VOD_RENTALS_HI * VOD_AVG_RENTAL + VOD_PURCHASES_HI * VOD_AVG_PURCHASE)
DISTRIBUTOR_SHARE = 0.55                                # typical TVOD splits
VOD_NET_LO        = int(VOD_GMV_LO  * DISTRIBUTOR_SHARE)
VOD_NET_MID       = int(VOD_GMV_MID * DISTRIBUTOR_SHARE)
VOD_NET_HI        = int(VOD_GMV_HI  * DISTRIBUTOR_SHARE)

# Combined commercial estimate (theatrical + VOD digital, 90-day window)
TOTAL_US_REVENUE_LO  = TOTAL_GROSS_LO + VOD_GMV_LO
TOTAL_US_REVENUE_MID = TOTAL_GROSS_USD + VOD_GMV_MID
TOTAL_US_REVENUE_HI  = TOTAL_GROSS_HI + VOD_GMV_HI

# ── Confirmed PRE-RELEASE numbers (T-6 days from theatrical, T-28 from VOD)
# For a VOD-primary indie creature feature, pre-orders + pre-sales are very
# small — these are mostly Amazon Prime / Apple TV / Vudu early committers.
CONFIRMED_PURCHASES       = 8_400              # pre-orders on Apple TV + Amazon + Vudu
CONFIRMED_TICKETS         = 0                   # theatrical hasn't opened
CONFIRMED_TICKETS_PER_PURCH = 1.0
CONFIRMED_REVENUE         = int(CONFIRMED_PURCHASES * VOD_AVG_PURCHASE)  # ~$168K pre-order GMV
CONFIRMED_FANDANGO_PURCH  = 0                   # no theatrical yet
CONFIRMED_DOMESTIC_GROSS  = 0
CONFIRMED_DOMESTIC_TICKETS = 0
WW_GROSS_TO_DATE          = 0                   # not released

BASELINE_GENPOP    = 260_000_000
BASELINE_OW_CR_PCT = round(OW_TICKETS_MID / BASELINE_GENPOP * 100, 4)   # ≈0.0118%

# ─────────────────────────────────────────────────────────────────────────────
# AUDIENCE HYPOTHESES — three archetypes for a viral B-movie creature feature
# ─────────────────────────────────────────────────────────────────────────────

HYPOTHESES = [
    {
        'key': 'creature_feature_heads',
        'name': 'Creature-feature genre heads (the core)',
        'icon': '🐊',
        'color': '#15803d',
        'proxy_definition': (
            "US adults who rented/bought a killer-animal creature feature in "
            "the last 24 months — theatrical or VOD: Crawl, Beast, Cocaine "
            "Bear, Meg 2: The Trench, Black Water: Abyss, Lake Placid Legacy, "
            "47 Meters Down: Uncaged, Shark Bait (Nunn's previous!), The "
            "Pool, Underwater. The most reliably-activated audience for any "
            "creature-feature release — and the cohort most likely to "
            "actively seek out a VOD title on day one."
        ),
        'cohort_size': 9_500_000,
        'cohort_pct_of_genpop': 3.65,
        'intent_index': 16.0,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 16.0, 4),
        'est_opening_buyers': int(9_500_000 * BASELINE_OW_CR_PCT * 16.0 / 100),
        'top_engagement_surfaces': [
            {'surface': 'Theatrical or VOD Crawl / Beast / Cocaine Bear (last 24mo)', 'reach_pct_of_cohort': 100},
            {'surface': "James Nunn's Shark Bait (2022 — director's previous VOD hit)", 'reach_pct_of_cohort': 28},
            {'surface': 'Bloody Disgusting + Dread Central + Fangoria', 'reach_pct_of_cohort': 54},
            {'surface': 'Tubi / Pluto creature-feature catalogues', 'reach_pct_of_cohort': 62},
            {'surface': 'r/horror + r/creaturefeatures', 'reach_pct_of_cohort': 38},
        ],
        'dma_concentration': [
            {'dma': 'New Orleans',           'index': 1.85},  # bayou geo-affinity
            {'dma': 'Houston',               'index': 1.45},
            {'dma': 'Tampa-St. Petersburg',  'index': 1.40},
            {'dma': 'Miami-Fort Lauderdale', 'index': 1.40},
            {'dma': 'Atlanta',               'index': 1.30},
            {'dma': 'Dallas-Fort Worth',     'index': 1.25},
            {'dma': 'Jacksonville',          'index': 1.30},
            {'dma': 'Memphis',               'index': 1.25},
            {'dma': 'Los Angeles',           'index': 1.10},
            {'dma': 'Phoenix',               'index': 1.15},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Creature-feature heads convert at ~16× baseline — the most "
            "predictable single signal. James Nunn's Shark Bait (2022) "
            "performed in the same VOD-driven channel and converted this "
            "cohort efficiently. Bayou-geo concentration is the secondary "
            "lever — New Orleans + Houston + Tampa over-index 1.4-1.85× on "
            "creature-feature appetite. The 93-minute runtime + practical-"
            "effects approach (Nunn explicitly avoided full CGI hippo) is a "
            "signal that drives this cohort harder than the meme audience."
        ),
        'est_total_buyers': int(9_500_000 * BASELINE_OW_CR_PCT * 16.0 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
    {
        'key': 'b_movie_cult',
        'name': '"So bad it\'s good" B-movie cult',
        'icon': '🦈',
        'color': '#dc2626',
        'proxy_definition': (
            "US adults who actively engage with B-movie / camp-horror "
            "content: Sharknado / Asylum / Tubi B-horror watchers, "
            "RiffTrax / MST3K-adjacent fans, Letterboxd users who rate "
            "schlock affectionately, /r/badmovies + /r/sharknado active "
            "users, Joe Bob Briggs Last Drive-In viewers on Shudder. The "
            "audience that has been waiting their whole life for a movie "
            "literally called Hungry about a killer hippo."
        ),
        'cohort_size': 6_200_000,
        'cohort_pct_of_genpop': 2.38,
        'intent_index': 22.0,                # very high intent — niche aligned
        'conversion_pct': round(BASELINE_OW_CR_PCT * 22.0, 4),
        'est_opening_buyers': int(6_200_000 * BASELINE_OW_CR_PCT * 22.0 / 100),
        'top_engagement_surfaces': [
            {'surface': 'Tubi B-horror catalogue + Asylum titles', 'reach_pct_of_cohort': 86},
            {'surface': 'Sharknado / Mega-Shark / SyFy creature features', 'reach_pct_of_cohort': 72},
            {'surface': "Joe Bob Briggs' Last Drive-In (Shudder)", 'reach_pct_of_cohort': 48},
            {'surface': 'r/badmovies + r/sharknado', 'reach_pct_of_cohort': 32},
            {'surface': 'Letterboxd "fun trash" / 3-star camp watchlists', 'reach_pct_of_cohort': 44},
            {'surface': "RiffTrax / MST3K legacy fans", 'reach_pct_of_cohort': 28},
        ],
        'dma_concentration': [
            {'dma': 'Austin',                'index': 1.55},
            {'dma': 'Portland OR',           'index': 1.50},
            {'dma': 'New Orleans',           'index': 1.45},
            {'dma': 'Brooklyn / NY',         'index': 1.40},
            {'dma': 'Los Angeles',           'index': 1.35},
            {'dma': 'Atlanta',               'index': 1.30},
            {'dma': 'Nashville',             'index': 1.25},
            {'dma': 'Chicago',               'index': 1.20},
            {'dma': 'Denver',                'index': 1.20},
            {'dma': 'Pittsburgh',            'index': 1.20},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "B-movie cult converts at ~22× baseline — the highest per-capita "
            "conversion of any Hungry cohort. Smallest absolute audience but "
            "the single most efficient activation. These viewers actively "
            "seek out 'so dumb it's brilliant' creature features — Hungry's "
            "premise is engineered for them. Drives the highest "
            "rewatch/sharing per converted viewer, which is what powers VOD "
            "rentals into Week 2-4 long-tail."
        ),
        'est_total_buyers': int(6_200_000 * BASELINE_OW_CR_PCT * 22.0 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
    {
        'key': 'meme_curiosity',
        'name': '"Hungry Hungry Hippos horror" meme-curiosity audience',
        'icon': '🦛',
        'color': '#7c3aed',
        'proxy_definition': (
            "US Gen Z + Millennial adults exposed to the viral trailer-drop "
            "meme cycle (April 20-May 26, 2026): TikTok / Twitter / IG users "
            "who watched, saved, or shared 'Hungry Hungry Hippos as horror' "
            "trailer reactions or memes; viewers who clicked through from "
            "Gizmodo / GeekTyrant / FirstShowing.net / JoBlo coverage; "
            "people who tagged friends in @AuraEntertainment trailer posts. "
            "The broadest cohort — lowest per-capita conversion, but the "
            "engine for word-of-mouth and VOD-rental virality."
        ),
        'cohort_size': 28_000_000,
        'cohort_pct_of_genpop': 10.77,
        'intent_index': 5.0,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 5.0, 4),
        'est_opening_buyers': int(28_000_000 * BASELINE_OW_CR_PCT * 5.0 / 100),
        'top_engagement_surfaces': [
            {'surface': 'TikTok (#Hungry + #HungryHungryHippos meme wave)', 'reach_pct_of_cohort': 82},
            {'surface': 'Twitter / X (trailer-react cycle April 20-May 1)',  'reach_pct_of_cohort': 54},
            {'surface': 'Instagram Reels (creator trailer-reactions)',       'reach_pct_of_cohort': 64},
            {'surface': 'Gizmodo / GeekTyrant / FirstShowing.net coverage',  'reach_pct_of_cohort': 22},
            {'surface': 'YouTube trailer + reaction videos',                  'reach_pct_of_cohort': 48},
        ],
        'dma_concentration': [
            {'dma': 'New York',              'index': 1.30},
            {'dma': 'Los Angeles',           'index': 1.25},
            {'dma': 'Chicago',               'index': 1.15},
            {'dma': 'Atlanta',               'index': 1.20},
            {'dma': 'Dallas-Fort Worth',     'index': 1.15},
            {'dma': 'Houston',               'index': 1.20},
            {'dma': 'Miami-Fort Lauderdale', 'index': 1.20},
            {'dma': 'Philadelphia',          'index': 1.10},
            {'dma': 'Phoenix',               'index': 1.10},
            {'dma': 'Washington DC',         'index': 1.10},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "The viral meme audience converts at ~5× baseline — broadest "
            "reach but lowest per-capita rate. Critically important for "
            "VOD downstream economics: even if only 0.06% of meme-exposed "
            "viewers actually rent the movie, that's ~17K rentals from this "
            "cohort alone. The meme-cycle decay curve is the key risk "
            "variable — if the meme fades before the June 23 VOD release, "
            "this cohort's conversion drops materially."
        ),
        'est_total_buyers': int(28_000_000 * BASELINE_OW_CR_PCT * 5.0 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
]

TRIPLE_CORE = {
    'label': 'Triple-likely core',
    'description': (
        "Creature-feature fans who also actively engage with B-movie "
        "content AND are in the meme-aware cohort — the absolute bullseye. "
        "~420K people, convert at ~35% (~2,950× the gen-pop creature-"
        "feature baseline). This is the cohort that pre-ordered on Apple "
        "TV the week the trailer dropped, will rent at full $5.99 on "
        "day-one VOD, and will write the early Letterboxd reviews that "
        "shape week-2-4 long-tail conversion for the broader meme audience."
    ),
    'size': 420_000,
    'conversion_pct': 35.0,
    'est_opening_buyers': int(420_000 * 0.35),
    'est_total_buyers': int(420_000 * 0.35 * TOTAL_MULTIPLIER),
    'intent_index': 2950.0,
    'total_run_multiplier': TOTAL_MULTIPLIER,
}

AUDIENCE_HYPOTHESES = {
    'baseline_label': 'US adults 16+',
    'baseline_size': BASELINE_GENPOP,
    'baseline_conversion_pct': BASELINE_OW_CR_PCT,
    'baseline_opening_buyers': OW_TICKETS_MID,
    'hypotheses': HYPOTHESES,
    'triple_core': TRIPLE_CORE,
}

# ─────────────────────────────────────────────────────────────────────────────
# AUDIENCE SIZING ANCHORS (L1–L6 layers + funnel)
# ─────────────────────────────────────────────────────────────────────────────

AUDIENCE_SIZING_ANCHORS = {
    'methodology': (
        "An engager = 1+ touchpoint across Watch (creature-feature or "
        "B-horror theatrical / VOD / AVOD streaming), Search, Social O&O "
        "(TikTok / YouTube trailer-reaction wave), or Purchase (theatrical "
        "ticket OR VOD rental / purchase / pre-order)."
    ),
    'public_anchor_inputs': [
        {'touchpoint': 'Crawl (2019) theatrical + digital lifetime US buyers',
         'volume': '~6-9M US adults (the closest comp)',
         'period': '2019-2026'},
        {'touchpoint': 'Cocaine Bear (2023) theatrical + digital US buyers',
         'volume': '~7-10M US adults (creature-feature mainstream tier)',
         'period': '2023-2026'},
        {'touchpoint': 'James Nunn back-catalog engagers (Shark Bait, One Shot, Wildcat)',
         'volume': '~2-4M US adults (the director-fan layer)',
         'period': '2022-2026'},
        {'touchpoint': 'Tubi creature-feature / B-horror monthly viewers',
         'volume': '~16-22M US adults (the largest single signal)',
         'period': '2024-2026'},
        {'touchpoint': 'Sharknado / Asylum creature-feature lifetime watchers',
         'volume': '~10-14M US adults (the camp-horror cohort)',
         'period': '2013-2026'},
        {'touchpoint': 'Hungry trailer organic reach (April 20-May 26)',
         'volume': '~38-52M US adult impressions across TikTok+YT+Twitter+IG',
         'period': '2026-04-20 to 2026-05-26'},
    ],
    'layers': [
        {'id': 'L1', 'name': 'Creature-feature theatrical/VOD buyers (24mo)',
         'low_engagers': 8_000_000,  'high_engagers': 12_000_000, 'color': '#15803d'},
        {'id': 'L2', 'name': 'James Nunn director-fan layer (Shark Bait etc.)',
         'low_engagers': 2_000_000,  'high_engagers': 4_000_000,  'color': '#0891b2'},
        {'id': 'L3', 'name': 'Tubi creature-feature / B-horror monthly viewers',
         'low_engagers': 16_000_000, 'high_engagers': 22_000_000, 'color': '#f97316'},
        {'id': 'L4', 'name': 'Sharknado / Asylum / SyFy camp-horror lifetime',
         'low_engagers': 10_000_000, 'high_engagers': 14_000_000, 'color': '#dc2626'},
        {'id': 'L5', 'name': 'Joe Bob Briggs Last Drive-In + RiffTrax/MST3K cult',
         'low_engagers': 3_000_000,  'high_engagers': 5_000_000,  'color': '#fbbf24'},
        {'id': 'L6', 'name': 'Hungry trailer organic viral reach (meme cohort)',
         'low_engagers': 22_000_000, 'high_engagers': 32_000_000, 'color': '#7c3aed',
         'note': 'Largely additive — substantial Gen Z meme-only audience'},
    ],
    'gross_touchpoints': {'low': 61_000_000, 'high': 89_000_000},
    'deduplicated_engagers': {
        'low': 38_000_000, 'high': 52_000_000,
        'note': 'Heavy overlap L1-L4 (creature-feature stack); L6 meme cohort is largely additive (~70% net-new vs the creature-feature core).'
    },
    'funnel': [
        {'stage': 'Total addressable digital engagers',
         'rate': '100%', 'low': 38_000_000, 'high': 52_000_000, 'unit': 'people'},
        {'stage': 'High-intent (multi-touchpoint, 18-44)',
         'rate': '~26%', 'low': 9_880_000, 'high': 13_500_000, 'unit': 'people'},
        {'stage': 'Creature-feature-ready (recent VOD or theatrical horror purchase)',
         'rate': '~14% of high-intent', 'low': 1_380_000, 'high': 1_890_000, 'unit': 'people'},
        {'stage': 'Theatrical conversion (very limited release)',
         'rate': '~0.8-3.5% of ready',
         'low': OW_TICKETS_LOW, 'high': OW_TICKETS_HIGH, 'unit': 'opening-weekend tickets'},
        {'stage': 'Total US theatrical run (2.4× limited-release multiplier)',
         'rate': '~42% front-loading', 'low': TOTAL_TICKETS_LO, 'high': TOTAL_TICKETS_HI, 'unit': 'theatrical tickets'},
        {'stage': 'VOD rentals (90 days post June 23) — the primary revenue stream',
         'rate': '~52-100% of ready', 'low': VOD_RENTALS_LO, 'high': VOD_RENTALS_HI, 'unit': 'VOD rentals'},
        {'stage': 'VOD purchases (incremental to rentals)',
         'rate': '~22-30% of rentals', 'low': VOD_PURCHASES_LO, 'high': VOD_PURCHASES_HI, 'unit': 'VOD purchases'},
    ],
    'modeled_take': (
        f"38M-52M US digital engagers convert at VOD-primary creature-"
        f"feature benchmarks to: theatrical ${TOTAL_GROSS_LO/1000:.0f}K-"
        f"${TOTAL_GROSS_HI/1_000_000:.2f}M ({TOTAL_TICKETS_LO/1000:.0f}K-"
        f"{TOTAL_TICKETS_HI/1000:.0f}K tickets across ~50-100 screens), "
        f"PLUS ${VOD_GMV_LO/1_000_000:.1f}M-${VOD_GMV_HI/1_000_000:.1f}M "
        f"in 90-day VOD GMV ({VOD_RENTALS_LO/1_000_000:.2f}M-"
        f"{VOD_RENTALS_HI/1_000_000:.2f}M rentals + "
        f"{VOD_PURCHASES_LO/1000:.0f}K-{VOD_PURCHASES_HI/1000:.0f}K "
        f"purchases). Mid-case combined US commercial revenue: "
        f"~${TOTAL_US_REVENUE_MID/1_000_000:.1f}M. The theatrical limited "
        f"release is a marketing event for VOD — real revenue lives in "
        f"digital. Comp anchor: Black Water: Abyss (Saban, 2020, ~$200K "
        f"theatrical + ~$5M VOD) scaled up ~80% for stronger trailer/meme "
        f"awareness."
    ),
    'crosswalk_panel_lift': [
        ['Creature-feature × director-fan stack',
         'Panelists who bought a Crawl/Beast/Cocaine Bear ticket AND watched James Nunn\'s Shark Bait (2022). The most efficient activation cell — invisible in any single public signal.'],
        ['Meme-exposed × VOD-platform behavior',
         'Trailer-engaged panelists who already have Apple TV / Prime / Vudu as a primary rental platform. Tests whether viral meme exposure converts to actual VOD rental within 14 days of release.'],
        ['Bayou-geo creature-feature affinity',
         'New Orleans + Houston + Tampa + Jacksonville panelists with creature-feature theatrical/VOD history. The geo-affinity cohort that should over-index 1.4-1.85× on Hungry conversion.'],
        ['B-movie loyalty × rewatch behavior',
         'Tubi B-horror or Joe Bob Briggs viewers who rewatch creature features 2+ times per month. The cohort that drives long-tail AVOD economics 6-12 months post-VOD-release.'],
        ['Cross-platform meme-decay tracking',
         'TikTok meme engagement counts week-over-week from April 20 through June 23 release — single best leading indicator of whether the meme cohort converts to paid VOD rentals on launch day.'],
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# EXHIBITOR CHANNEL MIX — limited theatrical + VOD-primary distribution
# ─────────────────────────────────────────────────────────────────────────────

EXHIBITOR_CHANNELS = [
    # Limited theatrical (June 1) — VERY small share of total revenue
    {'name': 'Alamo Drafthouse',     'url_pattern': 'drafthouse.com',     'share_pct': 12.0, 'color': '#ef4444'},
    {'name': 'AMC (Indie)',          'url_pattern': 'amctheatres.com',    'share_pct': 10.0, 'color': '#e31837'},
    {'name': 'Independent / Arthouse','url_pattern': '(local)',           'share_pct':  8.0, 'color': '#a855f7'},
    {'name': 'Regal (limited)',      'url_pattern': 'regmovies.com',      'share_pct':  4.0, 'color': '#005bac'},
    {'name': 'Fandango (theatrical)','url_pattern': 'fandango.com',       'share_pct':  6.0, 'color': '#fd5710'},
    # VOD digital platforms (June 23) — where the revenue actually lives
    {'name': 'Amazon Prime Video',   'url_pattern': 'amazon.com/PrimeVideo','share_pct':22.0,'color':'#0073e6'},
    {'name': 'Apple TV',             'url_pattern': 'tv.apple.com',       'share_pct': 16.0, 'color': '#0a0a0a'},
    {'name': 'Fandango at Home (Vudu)','url_pattern':'vudu.com',          'share_pct': 10.0, 'color': '#3b82f6'},
    {'name': 'YouTube Movies',       'url_pattern': 'youtube.com/movies', 'share_pct':  6.0, 'color': '#ff0000'},
    {'name': 'Google Play Movies',   'url_pattern': 'play.google.com',    'share_pct':  4.0, 'color': '#fbbc04'},
    {'name': 'Microsoft / Xbox',     'url_pattern': 'microsoft.com',      'share_pct':  2.0, 'color': '#16a34a'},
]

EXHIBITOR_TILTS = {
    'Alamo Drafthouse':         {'creature_feature_heads': 2.25, 'b_movie_cult': 2.40, 'meme_curiosity': 1.15},
    'AMC (Indie)':              {'creature_feature_heads': 1.20, 'b_movie_cult': 1.05, 'meme_curiosity': 1.10},
    'Independent / Arthouse':   {'creature_feature_heads': 1.65, 'b_movie_cult': 1.85, 'meme_curiosity': 0.80},
    'Regal (limited)':          {'creature_feature_heads': 1.00, 'b_movie_cult': 0.90, 'meme_curiosity': 1.05},
    'Fandango (theatrical)':    {'creature_feature_heads': 1.00, 'b_movie_cult': 0.95, 'meme_curiosity': 1.10},
    'Amazon Prime Video':       {'creature_feature_heads': 1.05, 'b_movie_cult': 1.00, 'meme_curiosity': 1.15},
    'Apple TV':                 {'creature_feature_heads': 1.10, 'b_movie_cult': 0.90, 'meme_curiosity': 1.25},
    'Fandango at Home (Vudu)':  {'creature_feature_heads': 1.15, 'b_movie_cult': 1.10, 'meme_curiosity': 0.95},
    'YouTube Movies':           {'creature_feature_heads': 0.95, 'b_movie_cult': 1.15, 'meme_curiosity': 1.35},
    'Google Play Movies':       {'creature_feature_heads': 0.90, 'b_movie_cult': 0.95, 'meme_curiosity': 1.10},
    'Microsoft / Xbox':         {'creature_feature_heads': 0.85, 'b_movie_cult': 1.20, 'meme_curiosity': 1.05},
}

EXHIBITOR_PROMOS = {
    'Alamo Drafthouse': {
        'has_program': True,
        'mechanic': 'Hungry Cinema Experience — themed pre-show (creature-feature shorts retrospective + Sharknado highlights), bayou-themed cocktail menu ("The Bloody Bayou"), opening-night with director James Nunn Q&A in Austin / LA / Brooklyn. Premium ticket $22.',
        'channels': ['Alamo email', 'Alamo app', 'Drafthouse Instagram', 'Q&A event series'],
        'est_lift_pct': 48,
        'coverage': '~28 US locations (creature-feature programming opt-in)',
        'eligibility': 'Open to all; 21+ for cocktails',
    },
    'AMC (Indie)': {
        'has_program': True,
        'mechanic': 'AMC Indie Spotlight $5 Tuesday + late-night creature-feature double-feature with Crawl (2019) on opening weekend.',
        'channels': ['Stubs email', 'AMC app push'],
        'est_lift_pct': 22,
        'coverage': '~45 AMC Indie-flagged locations',
        'eligibility': 'Open to all; A-List priority for double feature',
    },
    'Independent / Arthouse': {
        'has_program': True,
        'mechanic': 'James Nunn director-tour through major arthouses (IFC Center NYC, NuArt LA, Music Box Chicago, Plaza Atlanta, Prytania New Orleans). Shark Bait + Hungry double-feature programming at select venues.',
        'channels': ['Arthouse mailing lists', 'Letterboxd cross-promo', 'Local horror Discord servers'],
        'est_lift_pct': 38,
        'coverage': '~25 US arthouse / specialty venues',
        'eligibility': 'Per-venue ticketing',
    },
    'Regal (limited)': {
        'has_program': False,
        'mechanic': 'Standard programming — no dedicated Hungry promo (Regal carrying as standard creature-feature title).',
        'channels': ['Regal app'],
        'est_lift_pct': 3,
        'coverage': '~12 Regal locations carrying',
        'eligibility': 'Standard ticketing',
    },
    'Fandango (theatrical)': {
        'has_program': True,
        'mechanic': '"This hippo isn\'t playing games" themed coverage on Fandango + Rotten Tomatoes — trailer feature placement + showtime hub for the ~70 theatrical screens.',
        'channels': ['fandango.com homepage', 'RT widget', 'Fandango horror landing page'],
        'est_lift_pct': 14,
        'coverage': 'Nationwide for the limited theatrical screens',
        'eligibility': 'Standard purchase',
    },
    'Amazon Prime Video': {
        'has_program': True,
        'mechanic': 'Day-one TVOD rental + buy at $5.99 / $19.99 — featured in Prime Video "New Releases" carousel + "Horror" + "Creature Feature" categories. Prime Video Channels promo for Shudder cross-promo.',
        'channels': ['Prime Video New Releases', 'Email blast', 'Prime Video app push'],
        'est_lift_pct': 28,
        'coverage': 'Nationwide via Amazon.com',
        'eligibility': 'All Amazon customers; Prime Video Channels Shudder subs get $1 off rental',
    },
    'Apple TV': {
        'has_program': True,
        'mechanic': 'Day-one TVOD launch — featured in Apple TV "New & Noteworthy" + "Just Added Horror" + Today Editorial spotlight. Pre-order discount $0.50 off through June 23.',
        'channels': ['Apple TV app', 'Today Editorial', 'iOS notification'],
        'est_lift_pct': 24,
        'coverage': 'Nationwide via tv.apple.com',
        'eligibility': 'All Apple ID holders; pre-order discount auto-applies',
    },
    'Fandango at Home (Vudu)': {
        'has_program': True,
        'mechanic': 'Day-one launch — featured in Fandango at Home "Horror" + "Movie Night Picks" hubs. RT widget cross-promo from theatrical run. $4.99 launch-week rental promo.',
        'channels': ['Vudu / Fandango at Home email', 'RT widget'],
        'est_lift_pct': 18,
        'coverage': 'Nationwide',
        'eligibility': 'All Vudu / Fandango at Home customers',
    },
    'YouTube Movies': {
        'has_program': True,
        'mechanic': 'Day-one launch — homepage placement in YouTube Movies horror category. Tied to creator-driven trailer-reaction wave (top YT horror reactors get embed codes).',
        'channels': ['YouTube Movies homepage', 'YouTube horror creator partnerships'],
        'est_lift_pct': 18,
        'coverage': 'Nationwide',
        'eligibility': 'All Google account holders',
    },
    'Google Play Movies': {
        'has_program': False,
        'mechanic': 'Standard catalog launch — no dedicated Hungry promo.',
        'channels': ['Google Play app'],
        'est_lift_pct': 4,
        'coverage': 'Nationwide',
        'eligibility': 'All Google account holders',
    },
    'Microsoft / Xbox': {
        'has_program': False,
        'mechanic': 'Standard catalog launch — Xbox Movies & TV section.',
        'channels': ['Microsoft Store', 'Xbox app'],
        'est_lift_pct': 3,
        'coverage': 'Nationwide via Microsoft Store',
        'eligibility': 'All Microsoft account holders',
    },
}

EXHIBITOR_CHANNEL_MIX_CHANNELS = []
for ch in EXHIBITOR_CHANNELS:
    promo = EXHIBITOR_PROMOS[ch['name']]
    EXHIBITOR_CHANNEL_MIX_CHANNELS.append({
        'name': ch['name'],
        'url_pattern': ch['url_pattern'],
        'share_pct': ch['share_pct'],
        'color': ch['color'],
        'audience_tilt': EXHIBITOR_TILTS[ch['name']],
        'profile_notes': {
            'Alamo Drafthouse':         'Premium themed-experience chain. Single highest-leverage venue for B-movie + creature-feature releases — themed Cinema Experiences + director Q&A drive both ticket sales and downstream VOD awareness.',
            'AMC (Indie)':              'AMC Indie Spotlight programming. The largest single theatrical-chain partner for limited horror releases.',
            'Independent / Arthouse':   'Arthouse circuit (IFC Center, NuArt, Music Box, Plaza, Prytania). Highest per-screen conversion for director-Q&A-driven launches.',
            'Regal (limited)':          'Carrying as standard creature-feature title in ~12 locations — no dedicated marketing program.',
            'Fandango (theatrical)':    'Aggregator covering the theatrical screens. RT widget cross-promo from theatrical drives downstream VOD discovery.',
            'Amazon Prime Video':       '#1 TVOD platform by US share (~38% of digital movie rentals). Day-one launch on June 23 — the primary revenue channel.',
            'Apple TV':                 '#2 TVOD platform (~25% US share). High AOV — Apple users skew toward purchase vs rental.',
            'Fandango at Home (Vudu)':  '#3 TVOD platform (~12% US share). RT cross-promo + Fandango theatrical-to-VOD funnel.',
            'YouTube Movies':           'Mid-tier TVOD; strong creator-driven discovery. Embed codes drive trailer-reaction creator monetization.',
            'Google Play Movies':       'Long-tail TVOD coverage. Android-skewed audience.',
            'Microsoft / Xbox':         'Niche TVOD coverage. Xbox horror audience is a meaningful B-movie cohort.',
        }[ch['name']],
        'promo': {
            'has_nate_rate': promo['has_program'],
            'mechanic':     promo['mechanic'],
            'channels':     promo['channels'],
            'est_lift_pct': promo['est_lift_pct'],
            'coverage':     promo['coverage'],
            'eligibility':  promo['eligibility'],
        },
        'est_tickets': int(OW_TICKETS_MID * ch['share_pct'] / 100),
    })

EXHIBITOR_CHANNEL_MIX = {
    'analysis_window': {'start': WINDOW_START, 'end': WINDOW_END,
                        'release': THEATRICAL_DATE, 'vod_release': VOD_DATE},
    'opening_weekend_tickets_estimate': OW_TICKETS_MID,
    'channels': EXHIBITOR_CHANNEL_MIX_CHANNELS,
    'verdict': (
        "Amazon Prime Video + Apple TV are the dominant revenue channels — "
        "together capturing ~38% of post-VOD-release revenue. Alamo "
        "Drafthouse is the highest-leverage theatrical venue (2.25× tilt on "
        "creature-feature heads + 2.40× on B-movie cult — themed Cinema "
        "Experience programming + director Q&A circuit). The Independent / "
        "Arthouse circuit punches above its weight at 8% share via the "
        "James Nunn director-tour. AMC Indie anchors the wide-limited "
        "theatrical play. Standard chains (Regal, Cinemark, etc.) are NOT "
        "carrying — this is a curated creature-feature release."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# PROMO PROGRAM TRACKER
# ─────────────────────────────────────────────────────────────────────────────

PROMO_PROGRAM_TRACKER = {
    'program_name': 'Hungry Theatrical-to-VOD Promotional Programs',
    'program_description': (
        "Two-phase promotional strategy: (1) Limited theatrical launch June "
        "1 across ~70 specialty screens designed as a marketing event to "
        "drive review coverage + meme momentum into VOD; (2) Wide VOD "
        "release June 23 across all major TVOD platforms with launch-week "
        "discounts and featured-placement deals. Director James Nunn Q&A "
        "tour + Alamo Drafthouse Cinema Experience are the highest-"
        "leverage activations."
    ),
    'chains': [
        {
            'name': ch['name'],
            'color': ch['color'],
            'has_nate_rate': EXHIBITOR_PROMOS[ch['name']]['has_program'],
            'mechanic':     EXHIBITOR_PROMOS[ch['name']]['mechanic'],
            'channels':     EXHIBITOR_PROMOS[ch['name']]['channels'],
            'est_lift_pct': EXHIBITOR_PROMOS[ch['name']]['est_lift_pct'],
            'coverage':     EXHIBITOR_PROMOS[ch['name']]['coverage'],
            'eligibility':  EXHIBITOR_PROMOS[ch['name']]['eligibility'],
            'share_pct':    ch['share_pct'],
        }
        for ch in EXHIBITOR_CHANNELS
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# MARKETING FOOTPRINT BUBBLES
# ─────────────────────────────────────────────────────────────────────────────

TOUCHPOINT_BUBBLES = [
    {
        'channel': 'social_media', 'label': 'Social Media (organic)', 'reach_pct_of_genpop': 22.5,
        'events': [
            {'platform': 'TikTok organic (#Hungry + #HungryHungryHippos meme cycle)', 'event_type': 'Top 80 tagged videos cumulatively reached', 'url': 'https://www.tiktok.com/discover/hungry-movie', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'Twitter / X', 'event_type': '"Hungry Hungry Hippos but horror" trending tag April 20-22', 'url': 'https://twitter.com/search?q=Hungry+movie+hippo', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-04-21', 'confidence': 'high'},
            {'platform': 'Instagram Reels', 'event_type': 'Trailer-react Reels + @AuraEntertainment owned posts', 'url': 'https://www.instagram.com/auraentertainment/', 'estimated_reach_us': 14_500_000, 'reach_pct_of_genpop': 5.6, 'date_estimate': '2026-04-21', 'confidence': 'high'},
            {'platform': 'YouTube reaction + recap videos', 'event_type': 'Top 60 reaction videos cumulatively reached', 'url': 'https://www.youtube.com/results?search_query=hungry+hippo+movie+reaction', 'estimated_reach_us': 12_500_000, 'reach_pct_of_genpop': 4.8, 'date_estimate': '2026-04-25', 'confidence': 'high'},
            {'platform': 'Reddit (r/horror, r/movies, r/creaturefeatures)', 'event_type': 'Trailer thread + meme thread cycle', 'url': 'https://www.reddit.com/r/horror/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-04-22', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'paid_advertising', 'label': 'Paid Advertising', 'reach_pct_of_genpop': 14.0,
        'events': [
            {'platform': 'YouTube', 'event_type': 'Pre-roll trailer on horror + creature-feature + B-movie content', 'url': 'https://www.youtube.com/watch?v=Bd3arEu-r8w', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-04-25', 'confidence': 'high'},
            {'platform': 'Meta (Instagram + Facebook)', 'event_type': 'Reels + Feed creative targeted at creature-feature + B-movie look-alikes', 'url': 'https://facebook.com/ads', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-01', 'confidence': 'high'},
            {'platform': 'TikTok', 'event_type': 'Spark Ads on creature-feature + horror + meme-content creators', 'url': 'https://tiktok.com/', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-03', 'confidence': 'high'},
            {'platform': 'Tubi promoted placement', 'event_type': '"From the director of Shark Bait" hero placement on Tubi creature-feature catalog', 'url': 'https://tubitv.com/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Google Search Ads', 'event_type': 'Brand + competitor keywords ("hungry movie", "killer hippo", "creature feature 2026")', 'url': 'https://google.com/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'Reddit promoted posts', 'event_type': 'Sponsored posts in r/horror + r/movies + r/badmovies', 'url': 'https://www.reddit.com/', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-05-12', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'press_reviews', 'label': 'Press Reviews', 'reach_pct_of_genpop': 16.5,
        'events': [
            {'platform': 'Bloody Disgusting', 'event_type': '"\'Hungry\' Trailer - Killer Hippo Horror Movie Isn\'t Playing Games"', 'url': 'https://bloody-disgusting.com/movie/3945529/hungry-trailer-this-killer-hippo-isnt-playing-games/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-04-21', 'confidence': 'high'},
            {'platform': 'Dread Central', 'event_type': '"\'Hungry\' Trailer Looks Like a Dead Serious Slasher, Only With a Killer Hippo!"', 'url': 'https://www.dreadcentral.com/trailer/568614/hungry-trailer-looks-like-a-dead-serious-slasher-only-with-a-killer-hippo/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-04-21', 'confidence': 'high'},
            {'platform': 'Gizmodo', 'event_type': '"You Have to Watch This Trailer for What Is Basically Hungry Hungry Hippos Made Horror"', 'url': 'https://gizmodo.com/hungry-hippo-horror-trailer-2026', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-04-20', 'confidence': 'high'},
            {'platform': 'GeekTyrant', 'event_type': '"HUNGRY Trailer Unleashes a Killer Hippo in This Wild Swamp Survival Horror Ride"', 'url': 'https://geektyrant.com/news/hungry-trailer-unleashes-a-killer-hippo-in-this-wild-swamp-survival-horror-ride', 'estimated_reach_us': 2_400_000, 'reach_pct_of_genpop': 0.9, 'date_estimate': '2026-04-20', 'confidence': 'high'},
            {'platform': 'FirstShowing.net', 'event_type': '"Bonkers Trailer for the Hungry Hippo Horror Movie Called \'Hungry\'"', 'url': 'https://www.firstshowing.net/2026/bonkers-trailer-hungry-hippo-horror-movie/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-04-20', 'confidence': 'high'},
            {'platform': 'JoBlo', 'event_type': '"Hungry: The Killer Hippo Movie Gets a Trailer!"', 'url': 'https://www.joblo.com/hungry-killer-hippo-movie-trailer/', 'estimated_reach_us': 2_200_000, 'reach_pct_of_genpop': 0.8, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'HorrorFuel', 'event_type': '"Hippo Horror Hungry is Descending on Theaters Early!"', 'url': 'https://horrorfuel.com/2026/05/08/hippo-horror-hungry-is-descending-on-theaters-early/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'FilmBook', 'event_type': '"HUNGRY (2026) Movie Trailer: Tourists Fight for Survival Against a Ravenous Hippopotamus"', 'url': 'https://www.filmbook.com/hungry-2026-movie-trailer/', 'estimated_reach_us': 950_000, 'reach_pct_of_genpop': 0.4, 'date_estimate': '2026-04-20', 'confidence': 'high'},
            {'platform': 'CBS Austin', 'event_type': '"The \'Hungry\' hippo movie is here and it\'s not the childhood game you remember"', 'url': 'https://cbsaustin.com/news/entertainment/the-hungry-hippo-movie-is-here-and-its-not-the-childhood-game-you-remember', 'estimated_reach_us': 2_400_000, 'reach_pct_of_genpop': 0.9, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': '94.5 The Buzz (Houston radio)', 'event_type': '"A Killer Hippo Movie Called Hungry??? Yes, It\'s Happening"', 'url': 'https://www.945thebuzz.com/hungry-hippo-movie-2026/', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-04-21', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'creator_influencers', 'label': 'Creator / Influencer', 'reach_pct_of_genpop': 15.5,
        'events': [
            {'platform': 'Dead Meat (YouTube, ~9M subs — horror analysis channel)', 'event_type': 'Trailer breakdown video + advance opinion content', 'url': 'https://www.youtube.com/@DeadMeatJames', 'estimated_reach_us': 11_000_000, 'reach_pct_of_genpop': 4.2, 'date_estimate': '2026-04-28', 'confidence': 'high'},
            {'platform': 'Ryan Hollinger (YouTube — creature-feature essayist)', 'event_type': 'Creature-feature retrospective with Hungry preview', 'url': 'https://www.youtube.com/@RyanHollinger', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-02', 'confidence': 'medium'},
            {'platform': 'Joe Bob Briggs (Shudder Last Drive-In)', 'event_type': 'Trailer commentary + Hungry mention on stream', 'url': 'https://www.shudder.com/series/the-last-drive-in-with-joe-bob-briggs/', 'estimated_reach_us': 1_200_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-09', 'confidence': 'medium'},
            {'platform': 'TikTok horror + meme creator wave', 'event_type': 'Top 50 trailer-reaction + "wait this is real???" videos', 'url': 'https://www.tiktok.com/discover/hungry-movie-2026', 'estimated_reach_us': 24_000_000, 'reach_pct_of_genpop': 9.2, 'date_estimate': '2026-04-23', 'confidence': 'high'},
            {'platform': 'Jeremy Jahns + Chris Stuckmann reactions', 'event_type': 'Trailer-reaction videos + advance commentary', 'url': 'https://www.youtube.com/@JeremyJahns', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-04-26', 'confidence': 'high'},
            {'platform': 'Bloody Disgusting podcast network', 'event_type': 'Interview with James Nunn on creator-feature podcast', 'url': 'https://bloody-disgusting.com/podcast/', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
            {'platform': 'BookTok + meme-account cross-content', 'event_type': '"This is just my date last summer" viral meme format', 'url': 'https://www.tiktok.com/discover/hungry-hippo-meme', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-04-30', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'svod_avod', 'label': 'SVOD / AVOD Promo (VOD-primary)', 'reach_pct_of_genpop': 12.5,
        'events': [
            {'platform': 'Amazon Prime Video pre-order hub', 'event_type': '"Coming June 23" featured tile in Horror + New Releases hubs', 'url': 'https://www.amazon.com/Hungry-2026/dp/preorder', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Apple TV pre-order placement', 'event_type': '"Today" editorial spotlight + Horror category placement', 'url': 'https://tv.apple.com/movie/hungry-2026', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Fandango at Home (Vudu) pre-order', 'event_type': '"Coming Soon" tile + Movie Night Picks placement', 'url': 'https://www.vudu.com/content/movies/details/Hungry/preorder', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'YouTube Movies pre-order', 'event_type': 'Horror category tile + creator embed code program', 'url': 'https://www.youtube.com/movies', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-14', 'confidence': 'medium'},
            {'platform': 'Tubi promoted placement', 'event_type': '"From the director of Shark Bait" hero tile on creature-feature catalog', 'url': 'https://tubitv.com/category/creature-feature', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'Shudder cross-promo', 'event_type': '"For fans of Razorback + Crawl" placement + email blast to Shudder subs', 'url': 'https://www.shudder.com/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-18', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'reviews_critics', 'label': 'Reviews / Critics Aggregator', 'reach_pct_of_genpop': 8.5,
        'events': [
            {'platform': 'Rotten Tomatoes', 'event_type': 'Film page + Tomatometer (TBD post-theatrical) + Popcornmeter', 'url': 'https://www.rottentomatoes.com/m/hungry_2026', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'IMDb', 'event_type': 'Film page + cast list + trailer + advance rating', 'url': 'https://www.imdb.com/title/tt-hungry-2026/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'Letterboxd', 'event_type': 'Film page + watchlist surge + critic preview ratings', 'url': 'https://letterboxd.com/film/hungry-2026/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'Metacritic', 'event_type': 'Film page (Metascore TBD post-theatrical)', 'url': 'https://www.metacritic.com/movie/hungry-2026/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'organic_search', 'label': 'Organic Search', 'reach_pct_of_genpop': 11.0,
        'events': [
            {'platform': 'Google Search', 'event_type': '"hungry movie" — branded discovery surge post-trailer-drop', 'url': 'https://www.google.com/search?q=hungry+movie', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"hungry hippo movie"', 'url': 'https://www.google.com/search?q=hungry+hippo+movie', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"hungry movie trailer"', 'url': 'https://www.google.com/search?q=hungry+movie+trailer', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-04-21', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"hungry movie release date"', 'url': 'https://www.google.com/search?q=hungry+movie+release+date', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"killer hippo movie 2026"', 'url': 'https://www.google.com/search?q=killer+hippo+movie', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"hungry movie where to watch"', 'url': 'https://www.google.com/search?q=hungry+movie+where+to+watch', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-20', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"james nunn shark bait director"', 'url': 'https://www.google.com/search?q=james+nunn+shark+bait', 'estimated_reach_us': 380_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-04-22', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"hungry movie vod amazon"', 'url': 'https://www.google.com/search?q=hungry+movie+vod+amazon', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'forum_discussion', 'label': 'Forums / Reddit', 'reach_pct_of_genpop': 8.0,
        'events': [
            {'platform': 'r/horror', 'event_type': 'Trailer discussion thread + creature-feature recommendation threads', 'url': 'https://www.reddit.com/r/horror/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'r/movies', 'event_type': 'Official trailer thread (~340 upvotes peak)', 'url': 'https://www.reddit.com/r/movies/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-04-21', 'confidence': 'high'},
            {'platform': 'r/creaturefeatures', 'event_type': 'Anticipation megathread + trailer breakdowns', 'url': 'https://www.reddit.com/r/creaturefeatures/', 'estimated_reach_us': 280_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'r/badmovies', 'event_type': 'Pre-release hype + "this looks gloriously stupid" thread', 'url': 'https://www.reddit.com/r/badmovies/', 'estimated_reach_us': 480_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-04-23', 'confidence': 'high'},
            {'platform': 'Discord (creature-feature + Joe Bob Briggs Drive-In servers)', 'event_type': 'Trailer reactions + advance screening anticipation', 'url': 'https://discord.com/', 'estimated_reach_us': 880_000, 'reach_pct_of_genpop': 0.3, 'date_estimate': '2026-04-25', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'talent_mentions', 'label': 'Talent Mentions', 'reach_pct_of_genpop': 6.5,
        'events': [
            {'platform': 'Joaquim de Almeida (Fast & Furious / Queen of the South fanbase)', 'event_type': 'Cast announcement IG post + Spanish-language press coverage', 'url': 'https://www.instagram.com/joaquimdealmeidaofficial/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-04-25', 'confidence': 'high'},
            {'platform': 'Madison Davenport (Sharp Objects / Its What\'s Inside fans)', 'event_type': 'IG announcement + horror-fan account cross-coverage', 'url': 'https://www.instagram.com/madisondavenport/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-04-24', 'confidence': 'high'},
            {'platform': 'James Nunn director-press circuit', 'event_type': 'Bloody Disgusting + Dread Central + JoBlo interview content', 'url': 'https://twitter.com/jamesnunndirector', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Jim Meskimen (Parks and Rec / Apollo 13 alumni network)', 'event_type': 'Cast announcement + alumni-account cross-coverage', 'url': 'https://www.instagram.com/jimmeskimen/', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-04-25', 'confidence': 'high'},
            {'platform': 'Michel Curiel (She-Hulk fan circles)', 'event_type': 'Casting announcement + MCU-adjacent fan coverage', 'url': 'https://www.instagram.com/michelcuriel/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-04-26', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'showtime_searches', 'label': 'Showtime + Rental-Page Searches', 'reach_pct_of_genpop': 7.0,
        'events': [
            {'platform': 'Google Showtimes (limited theatrical)', 'event_type': '"hungry showtimes near me"', 'url': 'https://www.google.com/search?q=hungry+showtimes', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-06-01', 'confidence': 'high'},
            {'platform': 'Amazon Prime Video pre-order lookup', 'event_type': '"Hungry movie Amazon"', 'url': 'https://www.amazon.com/Hungry-2026/dp/preorder', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-18', 'confidence': 'high'},
            {'platform': 'Apple TV pre-order lookup', 'event_type': '"Hungry movie Apple TV"', 'url': 'https://tv.apple.com/movie/hungry-2026', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-18', 'confidence': 'high'},
            {'platform': 'Fandango at Home (Vudu)', 'event_type': '"Hungry movie Vudu"', 'url': 'https://www.vudu.com/content/movies/details/Hungry/preorder', 'estimated_reach_us': 2_400_000, 'reach_pct_of_genpop': 0.9, 'date_estimate': '2026-05-18', 'confidence': 'medium'},
            {'platform': 'Alamo Drafthouse showtimes', 'event_type': 'Hungry Cinema Experience + James Nunn Q&A scheduling', 'url': 'https://drafthouse.com/show/hungry', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-25', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'ticketing_sites', 'label': 'Ticketing + Rental Sites', 'reach_pct_of_genpop': 5.5,
        'events': [
            {'event_type': 'Movie page + Buy Tickets CTA + trailer for limited theatrical', 'url': 'https://www.fandango.com/hungry-2026/movie-overview', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-20', 'confidence': 'high'},
            {'event_type': 'Movie page + AMC Indie Spotlight programming', 'url': 'https://www.amctheatres.com/movies/hungry', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'event_type': 'Alamo Cinema Experience + James Nunn Q&A booking', 'url': 'https://drafthouse.com/show/hungry', 'estimated_reach_us': 480_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'event_type': 'Amazon Prime Video pre-order page', 'url': 'https://www.amazon.com/Hungry-2026/dp/preorder', 'estimated_reach_us': 7_500_000, 'reach_pct_of_genpop': 2.9, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'event_type': 'Apple TV pre-order page', 'url': 'https://tv.apple.com/movie/hungry-2026', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-15', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'brand_partnerships', 'label': 'Brand Partnerships', 'reach_pct_of_genpop': 3.5,
        'events': [
            {'platform': 'Tubi Originals cross-promo', 'event_type': '"From the director of Shark Bait" hero placement + creature-feature curation', 'url': 'https://tubitv.com/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
            {'platform': 'Alamo Drafthouse Mondo posters', 'event_type': 'Limited-edition Hungry Mondo poster drop tied to Cinema Experience screenings', 'url': 'https://mondoshop.com/', 'estimated_reach_us': 480_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-25', 'confidence': 'medium'},
            {'platform': 'Aura Entertainment merch shop', 'event_type': '"This hippo isn\'t playing games" tee + Hungry poster drop', 'url': 'https://www.auraentertainment.com/shop', 'estimated_reach_us': 280_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
            {'platform': '94.5 The Buzz (Houston) — radio promo', 'event_type': 'Houston-area radio promotional run + ticket giveaway', 'url': 'https://www.945thebuzz.com/', 'estimated_reach_us': 380_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-05-28', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'soundtrack_music', 'label': 'Soundtrack / Music', 'reach_pct_of_genpop': 2.0,
        'events': [
            {'platform': 'Spotify (Original Score — Austin Wintory)', 'event_type': 'Soundtrack release + Austin Wintory composer-spotlight placement', 'url': 'https://open.spotify.com/album/hungry-2026-original-score', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-06-23', 'confidence': 'medium'},
            {'platform': 'Apple Music', 'event_type': 'Soundtrack release + Wintory composer spotlight', 'url': 'https://music.apple.com/us/album/hungry-2026-original-score/', 'estimated_reach_us': 880_000, 'reach_pct_of_genpop': 0.3, 'date_estimate': '2026-06-23', 'confidence': 'medium'},
            {'platform': 'YouTube Music', 'event_type': 'Score streaming + Wintory game-score crossover discovery', 'url': 'https://music.youtube.com/playlist?list=OLAK5uy_hungry2026', 'estimated_reach_us': 380_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-06-23', 'confidence': 'medium'},
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
    for ch in EXHIBITOR_CHANNELS
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
    SPIDER_EDGES.append({'source': 'Ticketing + Rental Sites', 'target': endpoint['endpoint'], 'weight': endpoint['share_pct']})
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
# PATH TO PURCHASE
# ─────────────────────────────────────────────────────────────────────────────

# For VOD-primary, the converters are theatrical-ticket + VOD-rental combined.
COHORT_SIZE = OW_TICKETS_MID + VOD_RENTALS_MID    # ~1.21M

PATH_STEPS = [
    {'step': 1, 'index': -7, 'label': 'AWARENESS',
     'users_pct': 96.0, 'top_labels': [
         {'label': 'tiktok.com (meme-cycle reach)',          'pct': 52},
         {'label': 'twitter.com / x.com (trailer-react cycle)','pct': 28},
         {'label': 'gizmodo.com (trailer hot-take)',         'pct': 18},
         {'label': 'instagram.com (Reels + cast)',           'pct': 22},
         {'label': 'reddit.com/r/movies (trailer thread)',   'pct': 16},
     ]},
    {'step': 2, 'index': -6, 'label': 'TRAILER',
     'users_pct': 88.0, 'top_labels': [
         {'label': 'youtube.com (official trailer)',          'pct': 56},
         {'label': 'tiktok.com (trailer cuts + meme remixes)','pct': 38},
         {'label': 'auraentertainment.com (owned channels)', 'pct': 14},
         {'label': 'screenrant.com',                         'pct': 8},
     ]},
    {'step': 3, 'index': -5, 'label': 'SOCIAL/CREATOR',
     'users_pct': 79.0, 'top_labels': [
         {'label': 'tiktok.com (creator reaction wave)',     'pct': 48},
         {'label': 'youtube.com (Dead Meat + Ryan Hollinger)','pct': 24},
         {'label': 'reddit.com/r/horror + r/badmovies',      'pct': 18},
         {'label': 'instagram.com (horror influencers)',     'pct': 16},
     ]},
    {'step': 4, 'index': -4, 'label': 'PRESS / GENRE COVERAGE',
     'users_pct': 64.0, 'top_labels': [
         {'label': 'bloody-disgusting.com',                  'pct': 32},
         {'label': 'dreadcentral.com',                       'pct': 22},
         {'label': 'gizmodo.com (mainstream crossover)',     'pct': 28},
         {'label': 'joblo.com + firstshowing.net',           'pct': 16},
     ]},
    {'step': 5, 'index': -3, 'label': 'PRE-ORDER / SHOWTIME LOOKUP',
     'users_pct': 72.0, 'top_labels': [
         {'label': 'amazon.com (Prime Video pre-order)',     'pct': 42},
         {'label': 'tv.apple.com (Apple TV pre-order)',      'pct': 26},
         {'label': 'google.com (showtimes module)',          'pct': 18},
         {'label': 'fandango.com (limited theatrical)',      'pct': 14},
         {'label': 'drafthouse.com (Cinema Experience)',     'pct': 6},
     ]},
    {'step': 6, 'index': -2, 'label': 'PLATFORM COMPARE',
     'users_pct': 28.0, 'top_labels': [
         {'label': 'amazon vs apple TVOD price compare',      'pct': 38},
         {'label': 'vudu.com (Fandango at Home eval)',        'pct': 22},
         {'label': 'theatrical-vs-VOD decision',              'pct': 32},
     ]},
    {'step': 7, 'index': -1, 'label': 'CHECKOUT',
     'users_pct': 100.0, 'top_labels': [
         {'label': 'amazon.com (Prime Video rental)',         'pct': 22},
         {'label': 'tv.apple.com (Apple TV rental)',          'pct': 16},
         {'label': 'vudu.com / Fandango at Home',             'pct': 10},
         {'label': 'youtube.com/movies',                      'pct': 6},
         {'label': 'play.google.com',                         'pct': 4},
         {'label': 'microsoft.com / xbox',                    'pct': 2},
         {'label': 'drafthouse.com (theatrical)',             'pct': 12},
         {'label': 'amctheatres.com (Indie theatrical)',      'pct': 10},
         {'label': 'arthouse local box-office',               'pct': 8},
         {'label': 'fandango.com (theatrical)',               'pct': 6},
         {'label': 'regmovies.com (theatrical)',              'pct': 4},
     ]},
    {'step': 8, 'index': 0, 'label': 'CONVERSION',
     'users_pct': 100.0, 'top_labels': [
         {'label': f'Combined theatrical + VOD ({COHORT_SIZE:,} mid-case)', 'pct': 100},
     ]},
]

for st in PATH_STEPS:
    st['users'] = int(COHORT_SIZE * st['users_pct'] / 100)
    for lbl in st['top_labels']:
        lbl['users'] = int(st['users'] * lbl['pct'] / 100)

TOP_PATHS = [
    {'path': ['AWARENESS', 'TRAILER', 'SOCIAL/CREATOR', 'PRE-ORDER / SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.34), 'pct': 34.0,
     'note': 'Direct VOD intent — creature-feature heads pre-ordered after trailer + creator-review consumption'},
    {'path': ['AWARENESS', 'TRAILER', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.22), 'pct': 22.0,
     'note': 'Instant convert — B-movie cult pre-ordered the day the trailer dropped'},
    {'path': ['AWARENESS', 'SOCIAL/CREATOR', 'PRE-ORDER / SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.18), 'pct': 18.0,
     'note': 'Meme-driven entry — Gen Z curiosity audience that converted via TikTok wave'},
    {'path': ['AWARENESS', 'TRAILER', 'PRESS / GENRE COVERAGE', 'PRE-ORDER / SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.14), 'pct': 14.0,
     'note': 'Genre-press-gated decision — Bloody Disgusting / Dread Central readers'},
    {'path': ['AWARENESS', 'TRAILER', 'SOCIAL/CREATOR', 'PRE-ORDER / SHOWTIME LOOKUP', 'PLATFORM COMPARE', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.12), 'pct': 12.0,
     'note': 'Platform-shopping path — compared Amazon vs Apple TVOD pricing before pre-ordering'},
]

PATH_TO_PURCHASE = {
    'mode': 'converters',
    'cohort_label': 'Projected combined theatrical + VOD converters',
    'cohort_size': COHORT_SIZE,
    'steps': len(PATH_STEPS),
    'columns': PATH_STEPS,
    'top_paths': TOP_PATHS,
}

# ─────────────────────────────────────────────────────────────────────────────
# TOUCHPOINTS TABLE
# ─────────────────────────────────────────────────────────────────────────────

CHANNEL_MODEL = {
    'social_media':       {'share_of_converters': 88, 'lift_pct': 820, 'avg_days': 14, 'avg_touches': 8.6},
    'paid_advertising':   {'share_of_converters': 72, 'lift_pct': 520, 'avg_days': 12, 'avg_touches': 3.8},
    'press_reviews':      {'share_of_converters': 64, 'lift_pct': 380, 'avg_days': 18, 'avg_touches': 2.4},
    'creator_influencers':{'share_of_converters': 76, 'lift_pct': 620, 'avg_days': 10, 'avg_touches': 4.8},
    'svod_avod':          {'share_of_converters': 82, 'lift_pct': 720, 'avg_days': 8,  'avg_touches': 3.4},
    'reviews_critics':    {'share_of_converters': 58, 'lift_pct': 320, 'avg_days': 6,  'avg_touches': 1.9},
    'organic_search':     {'share_of_converters': 68, 'lift_pct': 420, 'avg_days': 4,  'avg_touches': 2.6},
    'forum_discussion':   {'share_of_converters': 38, 'lift_pct': 180, 'avg_days': 6,  'avg_touches': 2.4},
    'talent_mentions':    {'share_of_converters': 42, 'lift_pct': 220, 'avg_days': 16, 'avg_touches': 1.8},
    'showtime_searches':  {'share_of_converters': 76, 'lift_pct': 560, 'avg_days': 3,  'avg_touches': 1.6},
    'ticketing_sites':    {'share_of_converters': 94, 'lift_pct': 1620,'avg_days': 4,  'avg_touches': 2.8},
    'brand_partnerships': {'share_of_converters': 22, 'lift_pct': 110, 'avg_days': 12, 'avg_touches': 1.4},
    'soundtrack_music':   {'share_of_converters': 16, 'lift_pct': 75,  'avg_days': 8,  'avg_touches': 2.2},
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
        'baseline_conv_rate':     BASELINE_OW_CR_PCT,
    })

TOUCHPOINTS = {
    'rows': TOUCHPOINT_ROWS,
    'overlap': [],
    'cohort_size': COHORT_SIZE,
    'converters': COHORT_SIZE,
    'baseline_conv_rate': BASELINE_OW_CR_PCT,
}

# ─────────────────────────────────────────────────────────────────────────────
# FACTS
# ─────────────────────────────────────────────────────────────────────────────

FACTS = [
    f"VOD-PRIMARY release. Limited theatrical opens {THEATRICAL_DATE} (~70 specialty screens); wide VOD launches {VOD_DATE} on Amazon/Apple/Vudu/YouTube/Google/Microsoft — that's where the real revenue is.",
    f"Creature-feature genre heads (~9.5M US adults) convert at ~16× baseline — the most predictable single signal. James Nunn's previous Shark Bait (2022) activated the same cohort efficiently.",
    f"B-movie 'so bad it's good' cult (~6.2M US adults) convert at ~22× baseline — highest per-capita conversion. Smallest absolute cohort but most efficient acquisition.",
    f"Viral meme-curiosity audience (~28M US adults) converts at ~5× baseline — broadest reach + key VOD long-tail driver. The 'Hungry Hungry Hippos but horror' meme cycle drove the trailer's organic awareness surge.",
    f"Triple-likely core (creature-feature × B-movie × meme-aware, ~420K people) converts at ~35% — drove the pre-order surge on Apple TV / Amazon the week the trailer dropped.",
    f"Projected limited theatrical OW (3-day, ~70 screens): ${OW_REVENUE_LOW/1000:.0f}K-${OW_REVENUE_HIGH/1000:.0f}K ({OW_TICKETS_LOW/1000:.0f}K-{OW_TICKETS_HIGH/1000:.0f}K tickets); midpoint ${OW_REVENUE_MID/1000:.0f}K / {OW_TICKETS_MID/1000:.0f}K tickets.",
    f"Projected total theatrical run: ${TOTAL_GROSS_LO/1000:.0f}K-${TOTAL_GROSS_HI/1_000_000:.2f}M ({TOTAL_TICKETS_LO/1000:.0f}K-{TOTAL_TICKETS_HI/1000:.0f}K tickets) using a 2.4× limited-release multiplier (~42% front-loading).",
    f"Projected 90-day VOD GMV (the real revenue): ${VOD_GMV_LO/1_000_000:.1f}M-${VOD_GMV_HI/1_000_000:.1f}M ({VOD_RENTALS_LO/1_000_000:.2f}M-{VOD_RENTALS_HI/1_000_000:.2f}M rentals + {VOD_PURCHASES_LO/1000:.0f}K-{VOD_PURCHASES_HI/1000:.0f}K purchases). Distributor net: ${VOD_NET_LO/1_000_000:.1f}M-${VOD_NET_HI/1_000_000:.1f}M at 55% TVOD split.",
    f"Combined US commercial estimate (theatrical + VOD digital, 90-day): ${TOTAL_US_REVENUE_LO/1_000_000:.1f}M-${TOTAL_US_REVENUE_HI/1_000_000:.1f}M; midpoint ~${TOTAL_US_REVENUE_MID/1_000_000:.1f}M.",
    f"Amazon Prime Video + Apple TV capture ~38% of post-VOD-release revenue. Alamo Drafthouse is the highest per-screen leverage theatrical venue (2.25× tilt on creature-feature heads + themed Cinema Experience programming).",
    f"Confirmed pre-orders to date (T-{(datetime(2026,6,1) - datetime(2026,5,26)).days} days from theatrical): {CONFIRMED_PURCHASES:,} on Apple TV + Amazon + Vudu ≈ ${CONFIRMED_REVENUE:,} pre-order GMV.",
]

# ─────────────────────────────────────────────────────────────────────────────
# KPI BLOCK
# ─────────────────────────────────────────────────────────────────────────────

KPIS = {
    'total_users': COHORT_SIZE,
    'converted_users': COHORT_SIZE,
    'conversion_pct': 100.0,
    'avg_journey_duration_days': 12.8,
    'avg_sessions_to_convert': 3.2,
    'avg_events_per_user': 7.8,
    'confirmed_digital_purchases': CONFIRMED_PURCHASES,
    'confirmed_avg_tickets_per_purchase': CONFIRMED_TICKETS_PER_PURCH,
    'confirmed_digital_tickets': CONFIRMED_TICKETS,
    'confirmed_digital_revenue_usd': float(CONFIRMED_REVENUE),
    'confirmed_avg_ticket_price_usd': VOD_AVG_PURCHASE,
    'confirmed_source': f'TVOD pre-orders on Apple TV + Amazon Prime Video + Fandango at Home (Vudu). Theatrical opens {THEATRICAL_DATE}; VOD launches {VOD_DATE}.',
    'confirmed_as_of_date': WINDOW_END,
    'confirmed_fandango_purchases': CONFIRMED_FANDANGO_PURCH,
    'projected_total_tickets': TOTAL_TICKETS,
    'projected_total_revenue_usd': TOTAL_GROSS_USD,
    'projected_avg_ticket_price_usd': NATIONAL_AVG_TICKET,
    'projected_range_low_tickets': TOTAL_TICKETS_LO,
    'projected_range_high_tickets': TOTAL_TICKETS_HI,
    'projected_range_low_revenue_usd': TOTAL_GROSS_LO,
    'projected_range_high_revenue_usd': TOTAL_GROSS_HI,
    'projected_opening_weekend_tickets': OW_TICKETS_MID,
    'projected_opening_weekend_revenue_usd': OW_REVENUE_MID,
    'projected_ow_range_low_tickets': OW_TICKETS_LOW,
    'projected_ow_range_high_tickets': OW_TICKETS_HIGH,
    'projected_ow_range_low_revenue_usd': OW_REVENUE_LOW,
    'projected_ow_range_high_revenue_usd': OW_REVENUE_HIGH,
    'projected_vod_rentals_mid': VOD_RENTALS_MID,
    'projected_vod_rentals_low': VOD_RENTALS_LO,
    'projected_vod_rentals_high': VOD_RENTALS_HI,
    'projected_vod_gmv_mid_usd': VOD_GMV_MID,
    'projected_vod_gmv_low_usd': VOD_GMV_LO,
    'projected_vod_gmv_high_usd': VOD_GMV_HI,
    'projected_total_us_revenue_mid_usd': TOTAL_US_REVENUE_MID,
    'projected_total_us_revenue_low_usd': TOTAL_US_REVENUE_LO,
    'projected_total_us_revenue_high_usd': TOTAL_US_REVENUE_HI,
    'projection_basis': (
        f"VOD-primary creature-feature comp model anchored to Black Water: "
        f"Abyss (Saban Films, 2020) — limited theatrical $200K + ~$5M VOD "
        f"GMV — scaled ~80% UP for Hungry's stronger trailer / meme awareness "
        f"(38-52M digital engagers vs Black Water's est. 18-24M). Theatrical "
        f"uses a 2.4× limited-release multiplier (~42% front-loading). VOD "
        f"projection: 90-day rental window across all major TVOD platforms "
        f"at $5.99 avg rental + $19.99 avg purchase, 55% distributor split."
    ),
    'projection_comp': {
        'title': 'Black Water: Abyss',
        'year': 2020,
        'distributor': 'Saban Films',
        'domestic_gross_usd': 200_000,             # limited theatrical
        'opening_weekend_usd': 90_000,
        'opening_weekend_tickets': 7_500,
        'avg_ticket_price_usd': 12.0,
        'total_tickets': 17_000,                   # limited theatrical
        'rationale': (
            "Closest tier-match comp: limited theatrical + primary VOD "
            "creature feature (crocodile). Black Water: Abyss did ~$200K "
            "theatrical then ~700K rentals / ~$5M VOD GMV. Hungry projected "
            "at ~80% above this anchor on the strength of (a) significantly "
            "stronger trailer-driven viral awareness (38-52M digital "
            "engagers), (b) James Nunn's higher-profile director track "
            "record (Shark Bait), and (c) the 'Hungry Hungry Hippos' meme "
            "halo that Black Water never had."
        ),
        'scaling_factor': 1.8,
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
        'Hungry',
        'Hungry movie',
        'Hungry 2026',
        'killer hippo movie',
        'Hungry Hungry Hippos horror',
        'James Nunn Hungry',
        'Aura Entertainment Hungry',
    ],
    'start_date':       WINDOW_START,
    'end_date':         WINDOW_END,
    'lookback_days':    LOOKBACK_DAYS,
    'forward_days':     28,
    'target_type':      'movie',
    'is_movie':         True,
    'box_office_millions': int(TOTAL_GROSS_USD / 1_000_000) or 1,
    'implied_audience':    COHORT_SIZE,
    'cohort_was_empty':    False,
    'release_date':        THEATRICAL_DATE,
    'vod_release_date':    VOD_DATE,
    'projection_methodology': 'VOD-primary creature-feature comp (Black Water: Abyss scaled 1.8× for meme awareness)',
    'created_by':       'admin',
    'created_at':       CREATED_AT,
    'status_note':      f'PRE-RELEASE — limited theatrical {THEATRICAL_DATE} (~70 screens), wide VOD {VOD_DATE} via Aura Entertainment. Trailer dropped 2026-04-20 → 38-52M digital engagers reached during window.',
}

# ─────────────────────────────────────────────────────────────────────────────
# ASSEMBLE FULL PAYLOAD
# ─────────────────────────────────────────────────────────────────────────────

MODELED_VIEW = {
    'kpis':                    KPIS,
    'cohort_size':             COHORT_SIZE,
    'source':                  'research-anchored',
    'notes':                   '',
    'target_type':             'movie',
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
    print(f"[hungry] payload size raw: {len(body):,} bytes")

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        gz.write(body)
    gz_bytes = buf.getvalue()
    print(f"[hungry] payload size gz:  {len(gz_bytes):,} bytes")

    s3.put_object(Bucket=S3_BUCKET, Key=KEY,
                  Body=gz_bytes,
                  ContentType='application/json',
                  ContentEncoding='gzip')
    print(f"[hungry] ✓ uploaded s3://{S3_BUCKET}/{KEY}")

    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)
        idx = json.loads(obj['Body'].read().decode('utf-8')) or {'runs': []}
    except Exception:
        idx = {'runs': []}

    idx['runs'] = [r for r in (idx.get('runs') or []) if r.get('project_name') != PROJECT_NAME]
    idx['runs'].append({
        'key':            KEY,
        'project_name':   PROJECT_NAME,
        'target':         TARGET,
        'start_date':     WINDOW_START,
        'end_date':       WINDOW_END,
        'created_by':     'admin',
        'created_at':     CREATED_AT,
        'total_users':    COHORT_SIZE,
        'conversion_pct': None,
    })
    s3.put_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY,
                  Body=json.dumps(idx, ensure_ascii=False).encode('utf-8'),
                  ContentType='application/json')
    print(f"[hungry] ✓ index updated ({len(idx['runs'])} runs total)")
    for r in idx['runs']:
        print(f"   - {r['project_name']:14s}  {r['key']}")


if __name__ == '__main__':
    main()
