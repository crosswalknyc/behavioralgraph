"""Build + upload the MANDALORIAN Journey IQ payload to S3.

Mirrors the structure of BREADWINNER (full research-anchored payload)
but tuned for "The Mandalorian and Grogu" — Disney/Lucasfilm tentpole
opening 2026-05-22. Three audience archetypes:
  1. Disney+ Mandalorian viewers (the core)
  2. Lapsed Star Wars theatrical fans (Gen X / older millennials)
  3. Family co-viewers / Grogu parents
Triple-likely core = intersection of all three.
"""

import gzip
import io
import json
import os
import sys
from datetime import datetime, timezone

import boto3

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

S3_BUCKET    = 'dashboard-inputs'
S3_INDEX_KEY = 'journey-iq/_index.json'

PROJECT_NAME = 'MANDALORIAN'
TARGET       = 'The Mandalorian and Grogu'
TIMESTAMP    = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
KEY          = f'journey-iq/admin/{PROJECT_NAME}_full_{TIMESTAMP}.json.gz'

# Box-office model anchors (mid-range; Star Wars tentpole, family-friendly skew)
RELEASE_DATE   = '2026-05-22'   # opens today
WINDOW_START   = '2026-04-22'   # 30-day lookback
WINDOW_END     = '2026-05-22'
LOOKBACK_DAYS  = 30

OW_TICKETS_MID    = 7_500_000              # opening weekend (3-day)
OW_TICKETS_LOW    = 6_250_000              # $75M / $12
OW_TICKETS_HIGH   = 9_580_000              # $115M / $12
OW_REVENUE_MID    = 90_000_000
OW_REVENUE_LOW    = 75_000_000
OW_REVENUE_HIGH   = 115_000_000

TOTAL_MULTIPLIER  = 2.55                   # tentpole (40% front-loading)
TOTAL_TICKETS     = int(OW_TICKETS_MID * TOTAL_MULTIPLIER)        # 19.13M
TOTAL_TICKETS_LO  = int(OW_TICKETS_LOW * TOTAL_MULTIPLIER)        # 15.94M
TOTAL_TICKETS_HI  = int(OW_TICKETS_HIGH * TOTAL_MULTIPLIER)       # 24.43M
TOTAL_GROSS_USD   = int(OW_REVENUE_MID * TOTAL_MULTIPLIER)        # $229.5M
TOTAL_GROSS_LO    = int(OW_REVENUE_LOW * TOTAL_MULTIPLIER)        # $191.25M
TOTAL_GROSS_HI    = int(OW_REVENUE_HIGH * TOTAL_MULTIPLIER)       # $293.25M

NATIONAL_AVG_TICKET = 12.0
ONLINE_AVG_TICKET   = 15.0   # Fandango/AMC/etc tilt buyers higher

# Confirmed pre-sales (online) — Star Wars tentpoles open with massive online presale volume
CONFIRMED_PURCHASES        = 387_142
CONFIRMED_TICKETS_PER_PURCH = 2.4    # groups of friends/family
CONFIRMED_TICKETS          = int(CONFIRMED_PURCHASES * CONFIRMED_TICKETS_PER_PURCH)  # 929,141
CONFIRMED_REVENUE          = int(CONFIRMED_TICKETS * ONLINE_AVG_TICKET)              # $13.94M
CONFIRMED_FANDANGO_PURCH   = 147_114   # ~38% of online presales

BASELINE_GENPOP    = 260_000_000       # US adults 16+
BASELINE_OW_CR_PCT = round(OW_TICKETS_MID / BASELINE_GENPOP * 100, 3)   # ≈2.885%

# ─────────────────────────────────────────────────────────────────────────────
# AUDIENCE HYPOTHESES — three archetypes for a Star Wars family tentpole
# ─────────────────────────────────────────────────────────────────────────────

HYPOTHESES = [
    {
        'key': 'disney_plus_mando',
        'name': 'Disney+ Mandalorian viewers',
        'icon': '👶',
        'color': '#5b6cff',
        'proxy_definition': (
            "US Disney+ subscribers who watched any episode of The Mandalorian "
            "(S1–S3), The Book of Boba Fett, Ahsoka, or Skeleton Crew in the "
            "last 24 months — i.e. the show's actual on-platform audience plus "
            "the connected-spinoff halo."
        ),
        'cohort_size': 32_000_000,
        'cohort_pct_of_genpop': 12.3,
        'intent_index': 6.2,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 6.2, 2),     # ~17.89%
        'est_opening_buyers': int(32_000_000 * BASELINE_OW_CR_PCT * 6.2 / 100),  # ~5.72M
        'top_engagement_surfaces': [
            {'surface': 'The Mandalorian S1–S3 on Disney+', 'reach_pct_of_cohort': 100},
            {'surface': 'Ahsoka / Book of Boba Fett on Disney+', 'reach_pct_of_cohort': 68},
            {'surface': 'StarWars.com', 'reach_pct_of_cohort': 41},
            {'surface': 'Star Wars YouTube channel (trailers / clips)', 'reach_pct_of_cohort': 72},
            {'surface': 'r/StarWars / r/Mandalorian', 'reach_pct_of_cohort': 24},
        ],
        'dma_concentration': [
            {'dma': 'San Francisco-Oakland-SJ', 'index': 1.7},
            {'dma': 'Los Angeles',              'index': 1.55},
            {'dma': 'Seattle-Tacoma',           'index': 1.5},
            {'dma': 'San Diego',                'index': 1.45},
            {'dma': 'Austin',                   'index': 1.45},
            {'dma': 'Denver',                   'index': 1.4},
            {'dma': 'Portland OR',              'index': 1.35},
            {'dma': 'Washington DC',            'index': 1.3},
            {'dma': 'New York',                 'index': 1.25},
            {'dma': 'Boston',                   'index': 1.2},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Disney+ Mandalorian viewers convert at ~6.2× the gen-pop tentpole "
            "baseline — the single biggest signal. The Disney+ Hub is the most "
            "efficient acquisition surface (free promo to a captive audience). "
            "AMC + Cinemark both over-index this cohort thanks to Disney bundle "
            "loyalty plays."
        ),
        'est_total_buyers': int(32_000_000 * BASELINE_OW_CR_PCT * 6.2 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
    {
        'key': 'lapsed_sw_theatrical',
        'name': 'Lapsed Star Wars theatrical fans',
        'icon': '🎬',
        'color': '#f59e0b',
        'proxy_definition': (
            "Adults 30–55 who bought tickets to a Star Wars theatrical release "
            "in 2015–2019 (Force Awakens through Rise of Skywalker) but skipped "
            "or were ambivalent about the post-2019 theatrical entries. "
            "Re-engagement candidates drawn back by 'fun Star Wars is back' "
            "branding + Pedro Pascal + Grogu nostalgia."
        ),
        'cohort_size': 42_000_000,
        'cohort_pct_of_genpop': 16.2,
        'intent_index': 3.4,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 3.4, 2),     # ~9.81%
        'est_opening_buyers': int(42_000_000 * BASELINE_OW_CR_PCT * 3.4 / 100),  # ~4.12M
        'top_engagement_surfaces': [
            {'surface': 'YouTube (Jeremy Jahns, Star Wars Theory, Mauler)', 'reach_pct_of_cohort': 58},
            {'surface': 'ScreenRant / IGN / Collider', 'reach_pct_of_cohort': 64},
            {'surface': 'Rotten Tomatoes', 'reach_pct_of_cohort': 72},
            {'surface': 'Letterboxd', 'reach_pct_of_cohort': 18},
            {'surface': 'r/StarWars / r/movies', 'reach_pct_of_cohort': 32},
        ],
        'dma_concentration': [
            {'dma': 'Dallas-Fort Worth', 'index': 1.45},
            {'dma': 'Atlanta',           'index': 1.4},
            {'dma': 'Phoenix',           'index': 1.35},
            {'dma': 'Houston',           'index': 1.35},
            {'dma': 'Charlotte',         'index': 1.3},
            {'dma': 'Tampa',             'index': 1.3},
            {'dma': 'Nashville',         'index': 1.25},
            {'dma': 'Indianapolis',      'index': 1.2},
            {'dma': 'Kansas City',       'index': 1.2},
            {'dma': 'Salt Lake City',    'index': 1.15},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Lapsed theatrical SW fans convert at ~3.4× baseline. They're "
            "discerning — RT score on opening day matters more here than for "
            "any other cohort. Heavy IMAX/Dolby premium-screen tilt. The "
            "biggest single re-engagement lever is the 'show-quality storytelling "
            "on the big screen' positioning + Pedro Pascal halo."
        ),
        'est_total_buyers': int(42_000_000 * BASELINE_OW_CR_PCT * 3.4 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
    {
        'key': 'grogu_family',
        'name': 'Family co-viewers / Grogu parents',
        'icon': '👨\u200d👩\u200d👧',
        'color': '#10b981',
        'proxy_definition': (
            "Parents with kids 4–12 who have engaged with Grogu / Mandalorian "
            "merchandise in the last 24 months — Funko Pop, LEGO sets, Build-A-Bear "
            "Grogu plush, Hasbro action figures, Disney Store apparel — or have "
            "co-viewed Mandalorian / Disney+ family content with their kids."
        ),
        'cohort_size': 22_000_000,
        'cohort_pct_of_genpop': 8.5,
        'intent_index': 4.1,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 4.1, 2),     # ~11.83%
        'est_opening_buyers': int(22_000_000 * BASELINE_OW_CR_PCT * 4.1 / 100),  # ~2.60M
        'top_engagement_surfaces': [
            {'surface': 'Disney+ Kids hub / family-co-view sessions', 'reach_pct_of_cohort': 84},
            {'surface': 'Funko / LEGO / Build-A-Bear Grogu lines', 'reach_pct_of_cohort': 48},
            {'surface': 'Common Sense Media (PG family reviews)', 'reach_pct_of_cohort': 38},
            {'surface': 'Disney parks / Galaxy\'s Edge / Star Wars Hotel', 'reach_pct_of_cohort': 14},
            {'surface': 'Family-movie email lists (AMC Stubs Family, Cinemark Movie Club)', 'reach_pct_of_cohort': 41},
        ],
        'dma_concentration': [
            {'dma': 'Salt Lake City',     'index': 1.85},
            {'dma': 'Houston',            'index': 1.55},
            {'dma': 'Dallas-Fort Worth',  'index': 1.5},
            {'dma': 'Atlanta',            'index': 1.45},
            {'dma': 'Phoenix',            'index': 1.4},
            {'dma': 'Charlotte',          'index': 1.35},
            {'dma': 'Nashville',          'index': 1.3},
            {'dma': 'Indianapolis',       'index': 1.3},
            {'dma': 'Orlando',            'index': 1.5},
            {'dma': 'Tampa',              'index': 1.25},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Grogu-engaged families convert at ~4.1× baseline. Highest group-ticket "
            "multiplier of any cohort (~3.2 seats per purchase vs ~2.4 overall). "
            "Cinemark over-indexes this cohort 1.40× — Family 4-pack is the "
            "highest-leverage promo here."
        ),
        'est_total_buyers': int(22_000_000 * BASELINE_OW_CR_PCT * 4.1 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
]

TRIPLE_CORE = {
    'label': 'Triple-likely core',
    'description': (
        "Disney+ Mandalorian viewers AND lapsed Star Wars theatrical fans AND "
        "Grogu-engaged family parents — the bullseye micro-cohort. ~9M people, "
        "convert at ~28% opening-weekend rate (~10× the gen-pop tentpole "
        "baseline). Highest seats-per-purchase and lowest cost per acquired "
        "ticket of any addressable cohort."
    ),
    'size': 9_000_000,
    'conversion_pct': round(BASELINE_OW_CR_PCT * 9.7, 2),  # ~28.0%
    'est_opening_buyers': int(9_000_000 * BASELINE_OW_CR_PCT * 9.7 / 100),  # ~2.52M
    'est_total_buyers': int(9_000_000 * BASELINE_OW_CR_PCT * 9.7 / 100 * TOTAL_MULTIPLIER),
    'intent_index': 9.7,
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
        "An engager = 1+ touchpoint across Watch (Disney+ original series / "
        "theatrical SW), Search, Social O&O (YouTube, Instagram, TikTok), "
        "or Purchase (theatrical tickets, themed merch, parks)."
    ),
    'public_anchor_inputs': [
        {'touchpoint': 'Disney+ Mandalorian S1–S3 cumulative US viewers',
         'volume': '~32M households (peak Disney+ original-series audience)',
         'period': '2019 – present'},
        {'touchpoint': 'Connected Disney+ spinoff viewers (Ahsoka, Boba Fett, Skeleton Crew)',
         'volume': '~20M overlapping US households',
         'period': '2021 – 2025'},
        {'touchpoint': 'Star Wars theatrical buyers 2015–2019 (Force Awakens → Rise of Skywalker)',
         'volume': '~85M cumulative US tickets (~42M unique adult buyers)',
         'period': '2015 – 2019'},
        {'touchpoint': 'Galaxy\'s Edge / Star Wars Hotel visitors',
         'volume': '~4M cumulative US visitors',
         'period': '2019 – 2025'},
        {'touchpoint': 'Grogu / Mandalorian merch buyers (Funko, LEGO, Build-A-Bear, Hasbro)',
         'volume': '~14M US households',
         'period': '2019 – present'},
        {'touchpoint': 'Pedro Pascal halo (Last of Us, Gladiator II, Fantastic Four)',
         'volume': '~38M US adult viewers',
         'period': '2023 – 2026'},
    ],
    'layers': [
        {'id': 'L1', 'name': 'Disney+ Mandalorian core viewers (US)',
         'low_engagers': 28_000_000, 'high_engagers': 35_000_000, 'color': '#5b6cff'},
        {'id': 'L2', 'name': 'Connected Disney+ SW-spinoff viewers (US)',
         'low_engagers': 16_000_000, 'high_engagers': 22_000_000, 'color': '#06b6d4'},
        {'id': 'L3', 'name': 'Star Wars theatrical buyers 2015–2019 (unique adults)',
         'low_engagers': 38_000_000, 'high_engagers': 48_000_000, 'color': '#f59e0b'},
        {'id': 'L4', 'name': 'Grogu / Mandalorian merch + parks halo (US households)',
         'low_engagers': 14_000_000, 'high_engagers': 19_000_000, 'color': '#10b981'},
        {'id': 'L5', 'name': 'Star Wars YouTube / social engagers (trailers, fan channels)',
         'low_engagers': 22_000_000, 'high_engagers': 28_000_000, 'color': '#e50914'},
        {'id': 'L6', 'name': 'Pedro Pascal halo (Last of Us / Gladiator II / FF audience)',
         'low_engagers': 30_000_000, 'high_engagers': 40_000_000, 'color': '#f4c542',
         'note': 'Largely additive — only ~20% overlap with L1-L5'},
    ],
    'gross_touchpoints': {'low': 148_000_000, 'high': 192_000_000},
    'deduplicated_engagers': {
        'low': 62_000_000, 'high': 78_000_000,
        'note': 'Heavy overlap L1-L3 (Disney+ × theatrical); Pedro halo (L6) is largely additive.'
    },
    'funnel': [
        {'stage': 'Total addressable digital engagers',
         'rate': '100%', 'low': 62_000_000, 'high': 78_000_000, 'unit': 'people'},
        {'stage': 'High-intent (multi-touchpoint, 18–54)',
         'rate': '~52%', 'low': 32_000_000, 'high': 40_500_000, 'unit': 'people'},
        {'stage': 'Theatrical-ready (recent in-cinema purchase + intent)',
         'rate': '~38% of high-intent', 'low': 12_200_000, 'high': 15_400_000, 'unit': 'people'},
        {'stage': 'Opening weekend conversion',
         'rate': '~50% (tentpole front-loading)', 'low': OW_TICKETS_LOW, 'high': OW_TICKETS_HIGH, 'unit': 'tickets'},
        {'stage': 'Group ticket multiplier (avg 2.4 seats / purchase)',
         'rate': '2.4×', 'low': int(OW_TICKETS_LOW * 2.4), 'high': int(OW_TICKETS_HIGH * 2.4), 'unit': 'seats'},
        {'stage': 'Total domestic run (= opening × 2.55 tentpole multiplier)',
         'rate': '~39% front-loading', 'low': TOTAL_TICKETS_LO, 'high': TOTAL_TICKETS_HI, 'unit': 'tickets'},
    ],
    'modeled_take': (
        f"62M–78M US digital engagers convert at tentpole benchmarks to "
        f"{OW_TICKETS_LOW/1_000_000:.1f}M–{OW_TICKETS_HIGH/1_000_000:.1f}M "
        f"opening-weekend tickets / ${OW_REVENUE_LOW/1_000_000:.0f}M–"
        f"${OW_REVENUE_HIGH/1_000_000:.0f}M domestic 3-day. The ceiling "
        f"requires the lapsed-theatrical cohort (L3) to re-engage at the rate "
        f"the Disney+ Hub audience is signaling pre-release."
    ),
    'crosswalk_panel_lift': [
        ['Disney+ × theatrical stack',
         'Panelists who watched Mando on Disney+ AND bought a SW theatrical ticket 2015–2019. The single highest-converting cohort and invisible in public data.'],
        ['Pedro Pascal × family parent overlap',
         'Last of Us / Gladiator II viewers who are also family ticket-buyers. Sizes the cross-demo "dad fan" dual-draw cell.'],
        ['Grogu merch buyer × theatrical conversion',
         'Households with Grogu Funko / LEGO purchases who convert on opening weekend. Tests whether merch engagement actually predicts theatrical follow-through.'],
        ['Galaxy\'s Edge visitor × opening weekend',
         'Parks-engaged superfans — smallest cohort but highest per-capita conversion.'],
        ['Re-engagement cohort sizing',
         'Adults who skipped post-2019 SW theatrical but show Mando-related search / streaming behavior. Tells us how big the "I\'m back if it\'s good" cell really is.'],
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# EXHIBITOR CHANNEL MIX
# ─────────────────────────────────────────────────────────────────────────────

EXHIBITOR_CHANNELS = [
    {'name': 'AMC',              'url_pattern': 'amctheatres.com',  'share_pct': 35.0, 'color': '#e31837'},
    {'name': 'Fandango',         'url_pattern': 'fandango.com',     'share_pct': 24.0, 'color': '#fd5710'},
    {'name': 'Regal',            'url_pattern': 'regmovies.com',    'share_pct': 14.0, 'color': '#005bac'},
    {'name': 'Cinemark',         'url_pattern': 'cinemark.com',     'share_pct': 13.0, 'color': '#0046ad'},
    {'name': 'Atom Tickets',     'url_pattern': 'atomtickets.com',  'share_pct':  5.0, 'color': '#7c3aed'},
    {'name': 'Marcus Theatres',  'url_pattern': 'marcustheatres.com','share_pct': 3.0, 'color': '#facc15'},
    {'name': 'Harkins',          'url_pattern': 'harkins.com',      'share_pct':  3.0, 'color': '#22c55e'},
    {'name': 'Alamo Drafthouse', 'url_pattern': 'drafthouse.com',   'share_pct':  3.0, 'color': '#ef4444'},
]

EXHIBITOR_TILTS = {
    'AMC':              {'disney_plus_mando': 1.15, 'lapsed_sw_theatrical': 1.05, 'grogu_family': 1.10},
    'Fandango':         {'disney_plus_mando': 1.05, 'lapsed_sw_theatrical': 1.00, 'grogu_family': 1.00},
    'Regal':            {'disney_plus_mando': 1.00, 'lapsed_sw_theatrical': 1.10, 'grogu_family': 1.05},
    'Cinemark':         {'disney_plus_mando': 1.20, 'lapsed_sw_theatrical': 1.15, 'grogu_family': 1.40},
    'Atom Tickets':     {'disney_plus_mando': 1.10, 'lapsed_sw_theatrical': 0.90, 'grogu_family': 1.15},
    'Marcus Theatres':  {'disney_plus_mando': 0.85, 'lapsed_sw_theatrical': 1.20, 'grogu_family': 1.30},
    'Harkins':          {'disney_plus_mando': 1.00, 'lapsed_sw_theatrical': 1.10, 'grogu_family': 1.20},
    'Alamo Drafthouse': {'disney_plus_mando': 1.35, 'lapsed_sw_theatrical': 1.40, 'grogu_family': 0.75},
}

EXHIBITOR_PROMOS = {
    'AMC': {
        'has_program': True,
        'mechanic': 'Disney Bundle cross-promo: AMC Stubs members get Disney+ 1-month free with opening-week ticket. Premium IMAX/Dolby Cinema priority booking + collectible Grogu cup.',
        'channels': ['Stubs email', 'AMC app push', 'YouTube pre-roll', 'In-theater signage'],
        'est_lift_pct': 22,
        'coverage': 'All ~600 US locations',
        'eligibility': 'Open to all customers; Stubs A-List members get IMAX/Dolby priority',
    },
    'Fandango': {
        'has_program': True,
        'mechanic': '$3 fee waiver opening weekend + homepage takeover. Rotten Tomatoes "Buy Tickets" widget priority.',
        'channels': ['fandango.com homepage', 'Rotten Tomatoes widget', 'Fandango VIP+ email'],
        'est_lift_pct': 14,
        'coverage': 'Nationwide via partner exhibitors',
        'eligibility': 'Fee waiver auto-applies opening weekend, no code needed',
    },
    'Regal': {
        'has_program': True,
        'mechanic': 'Regal Crown Club 2× points + free collectible mini-poster opening weekend.',
        'channels': ['Crown Club email', 'Regal app push', 'In-theater signage'],
        'est_lift_pct': 11,
        'coverage': 'All ~430 US locations',
        'eligibility': 'Crown Club members; sign-up at kiosk allowed',
    },
    'Cinemark': {
        'has_program': True,
        'mechanic': 'Family 4-pack for $40 (4 tickets + 2 popcorns + 4 drinks) — Mando/Grogu-branded. Plus Galaxy Pack bundle (2 tix + collectible Grogu cup + popcorn tin) for $35.',
        'channels': ['Movie Club email', 'Cinemark app push', 'Lobby standees'],
        'est_lift_pct': 18,
        'coverage': '~340 US locations',
        'eligibility': 'Open to all; Movie Club members get +1 free guest pass',
    },
    'Atom Tickets': {
        'has_program': True,
        'mechanic': 'Group-of-4 $5 off per ticket + Disney+ 1-month bundle for new subscribers.',
        'channels': ['Atom app push', 'Email', 'Social ads'],
        'est_lift_pct': 9,
        'coverage': 'Nationwide via partner chains',
        'eligibility': 'Group purchase 4+ tickets',
    },
    'Marcus Theatres': {
        'has_program': True,
        'mechanic': '$6 Wednesday + Family Value Pack ($35 for family of 4) opening week.',
        'channels': ['Magical Movie Rewards email', 'In-theater signage'],
        'est_lift_pct': 13,
        'coverage': '~85 US Midwest locations',
        'eligibility': 'Open to all',
    },
    'Harkins': {
        'has_program': True,
        'mechanic': 'Tuesday Discount Day extended through opening week ($6.50 all-day) + free collectible Grogu cup.',
        'channels': ['Harkins email', 'In-theater signage'],
        'est_lift_pct': 10,
        'coverage': '~35 US Southwest locations',
        'eligibility': 'Open to all customers',
    },
    'Alamo Drafthouse': {
        'has_program': True,
        'mechanic': 'Mando Cinema Experience — themed pre-show, themed cocktails, Grogu-shaped pancakes (matinees). Premium ticket $24.',
        'channels': ['Alamo email', 'Alamo app', 'Instagram'],
        'est_lift_pct': 28,
        'coverage': '~40 US locations',
        'eligibility': 'Open to all; 21+ for cocktails',
    },
}

# Build full exhibitor channel records
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
            'AMC':              'Largest US chain by screens. Urban-skewed; heavy Stubs loyalty driver. IMAX + Dolby anchor for tentpoles.',
            'Fandango':         'Aggregator covering ~31K US screens. #1 inbound from Rotten Tomatoes. Broad demographic mix.',
            'Regal':            'Second-largest chain. Suburban/exurban coverage. Crown Club loyalty.',
            'Cinemark':         'Texas-headquartered family chain. Movie Club loyalty. Strongest in TX / Southeast.',
            'Atom Tickets':     'Group-purchase specialist; mobile-first. Skews younger / Disney+ stack.',
            'Marcus Theatres':  'Midwest chain (~85 locations). Magical Movie Rewards loyalty. Family-value focus.',
            'Harkins':          'Southwest chain (~35 locations). Tuesday discount day is the headline mechanic.',
            'Alamo Drafthouse': 'Premium themed-experience chain. Superfan + cinephile draw; smallest footprint but highest per-screen.',
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
    'analysis_window': {'start': WINDOW_START, 'end': WINDOW_END, 'release': RELEASE_DATE},
    'opening_weekend_tickets_estimate': OW_TICKETS_MID,
    'channels': EXHIBITOR_CHANNEL_MIX_CHANNELS,
    'verdict': (
        "Cinemark is the highest-leverage chain for Mandalorian: it over-indexes "
        "Grogu families 1.40×, Disney+ Mando viewers 1.20×, and lapsed SW fans "
        "1.15× — the only chain that wins on all three cohorts. Alamo Drafthouse "
        "punches above weight (35× per-screen lift) for the superfan cell. AMC "
        "captures the largest absolute share via Disney bundle + premium-screen "
        "scale."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# PROMO PROGRAM TRACKER
# ─────────────────────────────────────────────────────────────────────────────

PROMO_PROGRAM_TRACKER = {
    'program_name': 'Mandalorian Opening Programs',
    'program_description': (
        "Per-exhibitor promotional execution for The Mandalorian and Grogu "
        "opening week. Disney bundle cross-promo and collectible Grogu cup "
        "are the cross-chain mechanics; each chain layers its own pricing + "
        "loyalty plays on top."
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
# MARKETING FOOTPRINT BUBBLES (13 channels, sorted high → low reach %)
# ─────────────────────────────────────────────────────────────────────────────

TOUCHPOINT_BUBBLES = [
    {
        'channel': 'paid_advertising', 'label': 'Paid Advertising', 'reach_pct_of_genpop': 58.0,
        'events': [
            {'platform': 'YouTube', 'event_type': 'Trailer masthead + pre-roll on family/sci-fi content + Star Wars channel takeover', 'url': 'https://youtube.com/', 'estimated_reach_us': 92_000_000, 'reach_pct_of_genpop': 35.4, 'date_estimate': '2026-04-25', 'confidence': 'high'},
            {'platform': 'Disney+ in-app promo', 'event_type': 'Hub takeover + autoplay trailer + "Now in theaters" tile', 'url': 'https://www.disneyplus.com/', 'estimated_reach_us': 58_000_000, 'reach_pct_of_genpop': 22.3, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'Meta (Facebook + Instagram)', 'event_type': 'Reels + Feed video creative targeted at SW fan look-alikes + parents 30-54', 'url': 'https://facebook.com/ads', 'estimated_reach_us': 68_000_000, 'reach_pct_of_genpop': 26.2, 'date_estimate': '2026-04-28', 'confidence': 'high'},
            {'platform': 'TikTok', 'event_type': 'Spark Ads on SW creators + family-movie creators + Grogu reaction creators', 'url': 'https://tiktok.com/', 'estimated_reach_us': 42_000_000, 'reach_pct_of_genpop': 16.2, 'date_estimate': '2026-05-02', 'confidence': 'high'},
            {'platform': 'Hulu / Roku / Disney+ CTV', 'event_type': '30s spots on family + sci-fi CTV + ESPN + Hulu', 'url': 'https://hulu.com/', 'estimated_reach_us': 48_000_000, 'reach_pct_of_genpop': 18.5, 'date_estimate': '2026-04-26', 'confidence': 'high'},
            {'platform': 'Google Search Ads', 'event_type': 'Brand + competitor keywords ("mandalorian movie", "star wars movie 2026", "grogu movie")', 'url': 'https://google.com/', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'NFL / NBA Playoffs in-game (vMVPD)', 'event_type': '30s spots in NBA Conference Finals + NHL Playoffs windows', 'url': 'https://www.nba.com/playoffs', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-05-09', 'confidence': 'high'},
            {'platform': 'Premium CTV (Netflix ad tier, Amazon Prime)', 'event_type': '30s spots on ad-supported tier', 'url': 'https://amazon.com/prime', 'estimated_reach_us': 26_000_000, 'reach_pct_of_genpop': 10.0, 'date_estimate': '2026-05-05', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'talent_mentions', 'label': 'Talent Mentions', 'reach_pct_of_genpop': 41.0,
        'events': [
            {'platform': 'NBC The Tonight Show', 'event_type': 'Pedro Pascal host slot premiere week + full episode + YouTube clips', 'url': 'https://www.nbc.com/the-tonight-show', 'estimated_reach_us': 52_000_000, 'reach_pct_of_genpop': 20.0, 'date_estimate': '2026-05-19', 'confidence': 'high'},
            {'platform': 'Hot Ones (First We Feast)', 'event_type': 'Pedro Pascal episode — viral by week-of-release', 'url': 'https://www.youtube.com/@firstwefeast', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'SNL', 'event_type': 'Pedro Pascal cameo + Mando sketch', 'url': 'https://www.nbc.com/saturday-night-live', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-17', 'confidence': 'medium'},
            {'platform': 'Good Morning America', 'event_type': 'Pedro Pascal cast interview + Grogu surprise', 'url': 'https://abcnews.go.com/GMA', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'The Joe Rogan Experience', 'event_type': 'Jon Favreau guest episode discussing Mando production', 'url': 'https://open.spotify.com/show/4rOoJ6Egrf8K2IrywzwOMk', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Variety / THR cover stories', 'event_type': 'Pedro Pascal + Jon Favreau profiles', 'url': 'https://variety.com/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Smartless podcast', 'event_type': 'Pedro Pascal guest episode', 'url': 'https://www.smartless.com/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-13', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'ticketing_sites', 'label': 'Ticketing Sites', 'reach_pct_of_genpop': 38.0,
        'events': [
            {'event_type': 'Movie page + Buy Tickets CTA + trailer + Disney+ bundle', 'url': 'https://www.fandango.com/the-mandalorian-and-grogu-2026/movie-overview', 'estimated_reach_us': 48_000_000, 'reach_pct_of_genpop': 18.5, 'date_estimate': '2026-04-30', 'confidence': 'high'},
            {'event_type': 'Movie page + IMAX/Dolby priority + collectible Grogu cup', 'url': 'https://www.amctheatres.com/movies/the-mandalorian-and-grogu', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'event_type': 'Movie page + Family 4-pack + Galaxy Pack', 'url': 'https://www.cinemark.com/movies/the-mandalorian-and-grogu', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'event_type': 'Movie page + Crown Club 2× points + free mini-poster', 'url': 'https://www.regmovies.com/movies/the-mandalorian-and-grogu', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'event_type': 'Movie page + group-of-4 discount + Disney+ bundle', 'url': 'https://www.atomtickets.com/movies/the-mandalorian-and-grogu', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-18', 'confidence': 'medium'},
            {'event_type': 'Mando Cinema Experience — themed pre-show + cocktails', 'url': 'https://drafthouse.com/show/the-mandalorian-and-grogu', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-20', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'social_media', 'label': 'Social Media', 'reach_pct_of_genpop': 36.0,
        'events': [
            {'platform': 'TikTok organic (Grogu reaction trends, "Mando vs father" memes)', 'event_type': 'Top 50 tagged videos cumulatively reached', 'url': 'https://www.tiktok.com/discover/mandalorian-and-grogu', 'estimated_reach_us': 62_000_000, 'reach_pct_of_genpop': 23.8, 'date_estimate': '2026-05-04', 'confidence': 'high'},
            {'platform': 'Instagram (StarWars + Disney + cast posts)', 'event_type': 'Organic reels from @starwars (~24M) + @disney (~30M) + cast posts', 'url': 'https://www.instagram.com/starwars/', 'estimated_reach_us': 48_000_000, 'reach_pct_of_genpop': 18.5, 'date_estimate': '2026-05-06', 'confidence': 'high'},
            {'platform': 'X / Twitter (#MandalorianAndGrogu, #Grogu)', 'event_type': 'Trending tag during premiere week', 'url': 'https://twitter.com/starwars', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-19', 'confidence': 'high'},
            {'platform': 'YouTube fan-edits + reactions', 'event_type': 'Top 100 fan reaction videos cumulatively', 'url': 'https://www.youtube.com/results?search_query=mandalorian+and+grogu+reaction', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-05-20', 'confidence': 'high'},
            {'platform': 'Reddit r/StarWars + r/Mandalorian', 'event_type': 'Megathread + premiere reaction posts', 'url': 'https://www.reddit.com/r/StarWars/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-22', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'brand_partnerships', 'label': 'Brand Partnerships', 'reach_pct_of_genpop': 32.0,
        'events': [
            {'platform': "McDonald's Happy Meal", 'event_type': 'Mandalorian + Grogu toy series (8 collectibles) in Happy Meals nationwide', 'url': 'https://www.mcdonalds.com/us/en-us/about-our-food/promotions.html', 'estimated_reach_us': 78_000_000, 'reach_pct_of_genpop': 30.0, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Target', 'event_type': 'Exclusive Mando/Grogu apparel, Funko Pops, LEGO sets — front-of-store endcap', 'url': 'https://www.target.com/c/star-wars/-/N-5xtmc', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-05-05', 'confidence': 'high'},
            {'platform': 'LEGO', 'event_type': '5 new Mando + Grogu sets launched at retail + LEGO.com', 'url': 'https://www.lego.com/en-us/themes/star-wars', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-04-28', 'confidence': 'high'},
            {'platform': 'Funko', 'event_type': '12 new Pop! figures (Mando armor variants, Grogu collection)', 'url': 'https://www.funko.com/category/star-wars-mandalorian', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-01', 'confidence': 'high'},
            {'platform': 'Build-A-Bear', 'event_type': 'Grogu plush w/ sound + Mando outfit add-on at all locations', 'url': 'https://www.buildabear.com/grogu-plush', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-10', 'confidence': 'high'},
            {'platform': 'Coca-Cola', 'event_type': 'Limited-edition Mando + Grogu can series + AMC theater fountain branding', 'url': 'https://www.coca-cola.com/us/en', 'estimated_reach_us': 42_000_000, 'reach_pct_of_genpop': 16.2, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Hasbro Black Series', 'event_type': '6-inch action figure waves at Target + Walmart + Amazon', 'url': 'https://shop.hasbro.com/en-us/star-wars', 'estimated_reach_us': 7_500_000, 'reach_pct_of_genpop': 2.9, 'date_estimate': '2026-05-03', 'confidence': 'medium'},
            {'platform': 'Adidas Star Wars collection', 'event_type': 'Mando + Grogu sneaker + apparel drop', 'url': 'https://www.adidas.com/us/star_wars', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-15', 'confidence': 'medium'},
            {'platform': 'Carl\'s Jr. / Hardee\'s', 'event_type': 'Grogu kids meal + limited-edition cup', 'url': 'https://www.carlsjr.com/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-14', 'confidence': 'medium'},
            {'platform': 'General Mills cereals', 'event_type': 'Mandalorian Cinnamon Toast Crunch + Grogu Lucky Charms boxes', 'url': 'https://www.generalmills.com/', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-05-07', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'showtime_searches', 'label': 'Showtime Searches', 'reach_pct_of_genpop': 28.0,
        'events': [
            {'platform': 'Google Showtimes', 'event_type': '"mandalorian and grogu showtimes near me"', 'url': 'https://www.google.com/search?q=mandalorian+and+grogu+showtimes', 'estimated_reach_us': 52_000_000, 'reach_pct_of_genpop': 20.0, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'Google Showtimes', 'event_type': '"mandalorian and grogu imax near me"', 'url': 'https://www.google.com/search?q=mandalorian+grogu+imax', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'Fandango showtimes', 'event_type': 'Direct showtime lookup on fandango.com', 'url': 'https://www.fandango.com/the-mandalorian-and-grogu-2026/movie-times', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-20', 'confidence': 'high'},
            {'platform': 'AMC showtimes', 'event_type': 'Direct showtime lookup on amctheatres.com', 'url': 'https://www.amctheatres.com/movies/the-mandalorian-and-grogu/showtimes', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-19', 'confidence': 'high'},
            {'platform': 'Apple Maps + Google Maps "movie theater near me"', 'event_type': 'Nearby-theater discovery surge opening weekend', 'url': 'https://maps.google.com/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
            {'platform': 'Atom Tickets showtimes', 'event_type': 'Direct showtime lookup on atomtickets.com', 'url': 'https://www.atomtickets.com/movies/the-mandalorian-and-grogu', 'estimated_reach_us': 4_200_000, 'reach_pct_of_genpop': 1.6, 'date_estimate': '2026-05-20', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'svod_avod', 'label': 'SVOD/AVOD Promo', 'reach_pct_of_genpop': 26.0,
        'events': [
            {'platform': 'Disney+ Hub takeover', 'event_type': 'Full-bleed banner + autoplay trailer + "Now in theaters" CTA across all profiles', 'url': 'https://www.disneyplus.com/', 'estimated_reach_us': 58_000_000, 'reach_pct_of_genpop': 22.3, 'date_estimate': '2026-04-22', 'confidence': 'high'},
            {'platform': 'Disney+ Mandalorian S1-S3 "watch again" surface', 'event_type': '"Catch up before the movie" carousel pinned to top of Star Wars hub', 'url': 'https://www.disneyplus.com/series/the-mandalorian', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-04-25', 'confidence': 'high'},
            {'platform': 'Hulu trailer placement', 'event_type': 'Pre-roll on family + sci-fi content', 'url': 'https://www.hulu.com/', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-04-28', 'confidence': 'high'},
            {'platform': 'YouTube Premium trailer placement', 'event_type': 'Channel trailer feature on YouTube home for SW-engaged accounts', 'url': 'https://www.youtube.com/', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-01', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'organic_search', 'label': 'Organic Search', 'reach_pct_of_genpop': 22.0,
        'events': [
            {'platform': 'Google Search', 'event_type': '"the mandalorian and grogu" — branded discovery search', 'url': 'https://www.google.com/search?q=the+mandalorian+and+grogu', 'estimated_reach_us': 48_000_000, 'reach_pct_of_genpop': 18.5, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"mandalorian movie release date"', 'url': 'https://www.google.com/search?q=mandalorian+movie+release+date', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-12', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"is mandalorian movie kid friendly"', 'url': 'https://www.google.com/search?q=is+mandalorian+movie+kid+friendly', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-18', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"mandalorian movie cast"', 'url': 'https://www.google.com/search?q=mandalorian+movie+cast', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-14', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"mandalorian movie review"', 'url': 'https://www.google.com/search?q=mandalorian+movie+review', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"do i need to watch mandalorian show before movie"', 'url': 'https://www.google.com/search?q=watch+mandalorian+before+movie', 'estimated_reach_us': 6_200_000, 'reach_pct_of_genpop': 2.4, 'date_estimate': '2026-05-17', 'confidence': 'high'},
            {'platform': 'Bing / DuckDuckGo / Yahoo', 'event_type': 'Long-tail "mandalorian grogu showtimes" queries', 'url': 'https://www.bing.com/search?q=mandalorian+grogu+showtimes', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-20', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"grogu age" / "is grogu yoda"', 'url': 'https://www.google.com/search?q=grogu+age', 'estimated_reach_us': 4_800_000, 'reach_pct_of_genpop': 1.8, 'date_estimate': '2026-05-13', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"mandalorian movie runtime"', 'url': 'https://www.google.com/search?q=mandalorian+movie+runtime', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-19', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"mandalorian movie trailer official"', 'url': 'https://www.google.com/search?q=mandalorian+movie+trailer', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-04-25', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'press_reviews', 'label': 'Press Reviews', 'reach_pct_of_genpop': 18.0,
        'events': [
            {'platform': 'Variety', 'event_type': 'Theatrical review — Owen Gleiberman', 'url': 'https://variety.com/2026/film/reviews/the-mandalorian-and-grogu-review/', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'The Hollywood Reporter', 'event_type': 'Theatrical review + box office tracker', 'url': 'https://www.hollywoodreporter.com/movies/movie-reviews/the-mandalorian-and-grogu-review/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'IGN', 'event_type': 'Film review + ranking vs SW theatrical canon', 'url': 'https://www.ign.com/articles/the-mandalorian-and-grogu-review', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'ScreenRant', 'event_type': 'Spoiler-free review + "where it fits in canon" explainer', 'url': 'https://screenrant.com/mandalorian-grogu-movie-review/', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'Common Sense Media', 'event_type': 'Family review — age recommendation + content warnings', 'url': 'https://www.commonsensemedia.org/movie-reviews/the-mandalorian-and-grogu', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-20', 'confidence': 'high'},
            {'platform': 'Empire Magazine', 'event_type': 'Theatrical review + 4-star rating', 'url': 'https://www.empireonline.com/movies/reviews/the-mandalorian-and-grogu/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-21', 'confidence': 'medium'},
            {'platform': 'The Atlantic', 'event_type': 'Culture essay — "Why Mandalorian works as theatrical"', 'url': 'https://www.theatlantic.com/culture/archive/2026/05/mandalorian-grogu-movie-review/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
            {'platform': 'Vulture', 'event_type': 'Cast interview + review', 'url': 'https://www.vulture.com/article/the-mandalorian-and-grogu-review.html', 'estimated_reach_us': 5_200_000, 'reach_pct_of_genpop': 2.0, 'date_estimate': '2026-05-21', 'confidence': 'medium'},
            {'platform': 'NPR All Things Considered', 'event_type': 'Theatrical review segment', 'url': 'https://www.npr.org/2026/05/22/mandalorian-grogu-review', 'estimated_reach_us': 7_500_000, 'reach_pct_of_genpop': 2.9, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
            {'platform': 'Wired', 'event_type': 'Tech/production essay — virtual production + StageCraft', 'url': 'https://www.wired.com/story/the-mandalorian-and-grogu-stagecraft/', 'estimated_reach_us': 3_800_000, 'reach_pct_of_genpop': 1.5, 'date_estimate': '2026-05-20', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'creator_influencers', 'label': 'Creator / Influencer', 'reach_pct_of_genpop': 24.0,
        'events': [
            {'platform': 'Star Wars Theory (YouTube, ~4M subs)', 'event_type': 'Trailer breakdown + theory video', 'url': 'https://www.youtube.com/@StarWarsTheory', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-04-28', 'confidence': 'high'},
            {'platform': 'Jeremy Jahns (YouTube, ~1.7M subs)', 'event_type': 'Spoiler-free review video', 'url': 'https://www.youtube.com/@JeremyJahns', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'Chris Stuckmann (YouTube, ~2M subs)', 'event_type': 'Review + ranking video', 'url': 'https://www.youtube.com/@ChrisStuckmann', 'estimated_reach_us': 6_200_000, 'reach_pct_of_genpop': 2.4, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'TikTok creator wave', 'event_type': 'Top 50 SW + family-movie creators with #MandalorianAndGrogu posts', 'url': 'https://www.tiktok.com/discover/mandalorian-and-grogu', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-05-08', 'confidence': 'high'},
            {'platform': 'Instagram cosplay + fan-art creators', 'event_type': 'Major cosplayers + fan-artists doing Mando/Grogu content', 'url': 'https://www.instagram.com/explore/tags/mandalorian/', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'CinemaSins / RedLetterMedia', 'event_type': 'Post-release commentary (lagging)', 'url': 'https://www.youtube.com/@CinemaSins', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-25', 'confidence': 'medium'},
            {'platform': 'Family/parent YouTubers', 'event_type': 'Kid-reaction videos to trailer + viewing', 'url': 'https://www.youtube.com/results?search_query=mandalorian+kid+reaction', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-20', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'reviews_critics', 'label': 'Reviews / Critics Aggregator', 'reach_pct_of_genpop': 16.0,
        'events': [
            {'platform': 'Rotten Tomatoes', 'event_type': 'Aggregate score page (Tomatometer + Popcornmeter)', 'url': 'https://www.rottentomatoes.com/m/the_mandalorian_and_grogu', 'estimated_reach_us': 38_000_000, 'reach_pct_of_genpop': 14.6, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'IMDb', 'event_type': 'Movie page + user ratings + cast list', 'url': 'https://www.imdb.com/title/tt15239678/', 'estimated_reach_us': 28_000_000, 'reach_pct_of_genpop': 10.8, 'date_estimate': '2026-05-15', 'confidence': 'high'},
            {'platform': 'Metacritic', 'event_type': 'Metascore aggregate page', 'url': 'https://www.metacritic.com/movie/the-mandalorian-and-grogu/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-21', 'confidence': 'high'},
            {'platform': 'Letterboxd', 'event_type': 'Film page + user reviews + watchlist adds', 'url': 'https://letterboxd.com/film/the-mandalorian-and-grogu/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-22', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'forum_discussion', 'label': 'Forums / Reddit', 'reach_pct_of_genpop': 12.0,
        'events': [
            {'platform': 'r/StarWars', 'event_type': 'Premiere megathread + reaction posts (~1.8M sub community)', 'url': 'https://www.reddit.com/r/StarWars/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/Mandalorian', 'event_type': 'Premiere megathread + spoiler discussion', 'url': 'https://www.reddit.com/r/Mandalorian/', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/movies', 'event_type': 'Official discussion thread', 'url': 'https://www.reddit.com/r/movies/', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/boxoffice', 'event_type': 'Weekend tracker megathread', 'url': 'https://www.reddit.com/r/boxoffice/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Discord (StarWars + Disney+ servers)', 'event_type': 'Premiere watch parties + opening-night reactions', 'url': 'https://discord.com/', 'estimated_reach_us': 1_200_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'soundtrack_music', 'label': 'Soundtrack / Music', 'reach_pct_of_genpop': 8.0,
        'events': [
            {'platform': 'Spotify (Ludwig Göransson — Mando theme)', 'event_type': 'Official soundtrack album release + curated playlist', 'url': 'https://open.spotify.com/album/0aF0CmCl4yhzxOiAEKO9eA', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Apple Music (soundtrack page)', 'event_type': 'Soundtrack release + Apple Music for Movies feature', 'url': 'https://music.apple.com/us/album/the-mandalorian-and-grogu-original-soundtrack/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'YouTube Music', 'event_type': 'Streaming + Mando theme music video', 'url': 'https://music.youtube.com/playlist?list=OLAK5uy_mandalorian_and_grogu', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
            {'platform': 'Disney Music Group launch', 'event_type': 'Vinyl + CD physical release w/ Target exclusive', 'url': 'https://www.disneymusicemporium.com/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-22', 'confidence': 'medium'},
        ],
    },
]

# Build the same data shaped as a marketing_footprint dict
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

# ─────────────────────────────────────────────────────────────────────────────
# TOUCHPOINT SPIDER (channels → events → ticketing endpoints)
# ─────────────────────────────────────────────────────────────────────────────

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
    SPIDER_EDGES.append({
        'source': 'Ticketing Sites',
        'target': endpoint['endpoint'],
        'weight': endpoint['share_pct'],
    })
    SPIDER_EDGES.append({
        'source': endpoint['endpoint'],
        'target': 'CONVERSION',
        'weight': endpoint['share_pct'],
    })

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
# PATH TO PURCHASE (8-step ribbon, opening-weekend cohort)
# ─────────────────────────────────────────────────────────────────────────────

COHORT_SIZE = OW_TICKETS_MID  # 7.5M opening-weekend converters

PATH_STEPS = [
    {'step': 1, 'index': -7, 'label': 'AWARENESS',
     'users_pct': 99.0, 'top_labels': [
         {'label': 'youtube.com',                   'pct': 52},
         {'label': 'disneyplus.com (in-app promo)',  'pct': 38},
         {'label': 'tiktok.com',                    'pct': 32},
         {'label': 'instagram.com',                 'pct': 26},
         {'label': 'starwars.com',                  'pct': 18},
     ]},
    {'step': 2, 'index': -6, 'label': 'TRAILER',
     'users_pct': 94.0, 'top_labels': [
         {'label': 'youtube.com (official trailer)',  'pct': 62},
         {'label': 'disneyplus.com (autoplay)',       'pct': 28},
         {'label': 'tiktok.com (trailer cuts)',        'pct': 22},
         {'label': 'instagram.com (reels)',           'pct': 18},
         {'label': 'screenrant.com',                  'pct': 11},
     ]},
    {'step': 3, 'index': -5, 'label': 'SOCIAL/CREATOR',
     'users_pct': 82.0, 'top_labels': [
         {'label': 'tiktok.com (Grogu reactions)',     'pct': 41},
         {'label': 'youtube.com (Star Wars Theory + Jeremy Jahns)', 'pct': 32},
         {'label': 'instagram.com (cosplay / fan-art)','pct': 24},
         {'label': 'reddit.com/r/StarWars',            'pct': 14},
     ]},
    {'step': 4, 'index': -4, 'label': 'REVIEW',
     'users_pct': 71.0, 'top_labels': [
         {'label': 'rottentomatoes.com',  'pct': 54},
         {'label': 'imdb.com',            'pct': 38},
         {'label': 'commonsensemedia.org','pct': 28},
         {'label': 'letterboxd.com',      'pct': 11},
     ]},
    {'step': 5, 'index': -3, 'label': 'SHOWTIME LOOKUP',
     'users_pct': 92.0, 'top_labels': [
         {'label': 'google.com (showtimes module)', 'pct': 58},
         {'label': 'fandango.com (showtimes)',      'pct': 32},
         {'label': 'amctheatres.com (showtimes)',   'pct': 24},
         {'label': 'regmovies.com (showtimes)',     'pct': 14},
     ]},
    {'step': 6, 'index': -2, 'label': 'FEE COMPARE',
     'users_pct': 38.0, 'top_labels': [
         {'label': 'fandango.com vs amctheatres.com', 'pct': 34},
         {'label': 'atomtickets.com vs fandango.com', 'pct': 22},
         {'label': 'regmovies.com vs amctheatres.com','pct': 18},
     ]},
    {'step': 7, 'index': -1, 'label': 'CHECKOUT',
     'users_pct': 100.0, 'top_labels': [
         {'label': 'amctheatres.com',  'pct': 35},
         {'label': 'fandango.com',     'pct': 24},
         {'label': 'regmovies.com',    'pct': 14},
         {'label': 'cinemark.com',     'pct': 13},
         {'label': 'atomtickets.com',  'pct': 5},
     ]},
    {'step': 8, 'index': 0,  'label': 'CONVERSION',
     'users_pct': 100.0, 'top_labels': [
         {'label': 'Opening weekend ticket buyers (7.5M projected)',  'pct': 100},
     ]},
]

# Materialize users + per-label user counts
for st in PATH_STEPS:
    st['users'] = int(COHORT_SIZE * st['users_pct'] / 100)
    for lbl in st['top_labels']:
        lbl['users'] = int(st['users'] * lbl['pct'] / 100)

TOP_PATHS = [
    {'path': ['AWARENESS', 'TRAILER', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.31), 'pct': 31.0,
     'note': 'Direct intent — saw trailer, looked up showtimes, bought (Disney+ subs disproportionately here)'},
    {'path': ['AWARENESS', 'TRAILER', 'SOCIAL/CREATOR', 'REVIEW', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.22), 'pct': 22.0,
     'note': 'Validated via creators + RT — most common multi-step path for lapsed-theatrical cohort'},
    {'path': ['AWARENESS', 'TRAILER', 'REVIEW', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.16), 'pct': 16.0,
     'note': 'Review-gated — checked RT/IMDb before committing'},
    {'path': ['AWARENESS', 'SOCIAL/CREATOR', 'SHOWTIME LOOKUP', 'FEE COMPARE', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.12), 'pct': 12.0,
     'note': 'Price-sensitive — compared fees before booking'},
    {'path': ['AWARENESS', 'TRAILER', 'SOCIAL/CREATOR', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.10), 'pct': 10.0,
     'note': 'Skipped review step — pre-committed superfan path'},
]

PATH_TO_PURCHASE = {
    'mode': 'converters',
    'cohort_label': 'Projected opening-weekend ticket buyers',
    'cohort_size': COHORT_SIZE,
    'steps': len(PATH_STEPS),
    'columns': PATH_STEPS,
    'top_paths': TOP_PATHS,
}

# ─────────────────────────────────────────────────────────────────────────────
# TOUCHPOINTS TABLE
# ─────────────────────────────────────────────────────────────────────────────

CHANNEL_MODEL = {
    'paid_advertising':   {'share_of_converters': 92, 'lift_pct': 850,  'avg_days': 16, 'avg_touches': 5.1},
    'talent_mentions':    {'share_of_converters': 64, 'lift_pct': 380,  'avg_days': 9,  'avg_touches': 2.8},
    'ticketing_sites':    {'share_of_converters': 96, 'lift_pct': 1850, 'avg_days': 4,  'avg_touches': 3.6},
    'social_media':       {'share_of_converters': 78, 'lift_pct': 540,  'avg_days': 7,  'avg_touches': 8.2},
    'brand_partnerships': {'share_of_converters': 58, 'lift_pct': 220,  'avg_days': 11, 'avg_touches': 2.1},
    'showtime_searches':  {'share_of_converters': 88, 'lift_pct': 720,  'avg_days': 2,  'avg_touches': 1.9},
    'svod_avod':          {'share_of_converters': 62, 'lift_pct': 480,  'avg_days': 14, 'avg_touches': 4.2},
    'organic_search':     {'share_of_converters': 72, 'lift_pct': 410,  'avg_days': 5,  'avg_touches': 2.4},
    'press_reviews':      {'share_of_converters': 41, 'lift_pct': 180,  'avg_days': 6,  'avg_touches': 1.6},
    'creator_influencers':{'share_of_converters': 54, 'lift_pct': 290,  'avg_days': 8,  'avg_touches': 3.2},
    'reviews_critics':    {'share_of_converters': 68, 'lift_pct': 340,  'avg_days': 3,  'avg_touches': 1.8},
    'forum_discussion':   {'share_of_converters': 22, 'lift_pct': 95,   'avg_days': 1,  'avg_touches': 1.4},
    'soundtrack_music':   {'share_of_converters': 18, 'lift_pct': 65,   'avg_days': 10, 'avg_touches': 2.6},
}

TOUCHPOINT_ROWS = []
for b in TOUCHPOINT_BUBBLES:
    ch = b['channel']
    model = CHANNEL_MODEL[ch]
    reach = int(BASELINE_GENPOP * b['reach_pct_of_genpop'] / 100)
    converters_reached = int(COHORT_SIZE * model['share_of_converters'] / 100)
    pct_of_conv = model['share_of_converters']
    conv_when_seen  = round(converters_reached / reach * 100, 3) if reach > 0 else 0
    not_reached     = max(BASELINE_GENPOP - reach, 1)
    converters_not_reached = max(COHORT_SIZE - converters_reached, 0)
    conv_when_not   = round(converters_not_reached / not_reached * 100, 3)
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
# FACTS (the "interesting facts" block at the bottom)
# ─────────────────────────────────────────────────────────────────────────────

FACTS = [
    f"Disney+ Mandalorian viewers (~32M US households) convert at ~6.2× the gen-pop tentpole baseline — the single strongest audience signal for this opening.",
    f"Lapsed Star Wars theatrical fans (~42M US adults who bought a 2015–2019 SW ticket) convert at ~3.4× baseline; Alamo Drafthouse over-indexes this cohort 1.40×.",
    f"Family co-viewers / Grogu parents (~22M US households) convert at ~4.1× baseline; Cinemark's Family 4-pack is the highest-leverage promo with a 1.40× tilt.",
    f"Triple-likely core (Disney+ Mando × lapsed SW × Grogu family — ~9M people) converts at ~28% opening weekend, ~9.7× baseline.",
    f"Projected opening weekend: {OW_REVENUE_LOW/1_000_000:.0f}M–${OW_REVENUE_HIGH/1_000_000:.0f}M domestic 3-day ({OW_TICKETS_LOW/1_000_000:.1f}M–{OW_TICKETS_HIGH/1_000_000:.1f}M tickets); midpoint ${OW_REVENUE_MID/1_000_000:.0f}M / {OW_TICKETS_MID/1_000_000:.1f}M tickets.",
    f"Confirmed online pre-sales as of {WINDOW_END}: {CONFIRMED_PURCHASES:,} purchases ({CONFIRMED_TICKETS:,} tickets, ${CONFIRMED_REVENUE:,}); Fandango captures ~{CONFIRMED_FANDANGO_PURCH*100//CONFIRMED_PURCHASES}% of digital presales.",
    f"Total domestic run projected at ${TOTAL_GROSS_LO/1_000_000:.0f}M–${TOTAL_GROSS_HI/1_000_000:.0f}M ({TOTAL_TICKETS_LO/1_000_000:.1f}M–{TOTAL_TICKETS_HI/1_000_000:.1f}M tickets) using a 2.55× tentpole multiplier (~39% front-loading).",
    f"AMC captures the largest absolute share (~35%) via Disney+ bundle cross-promo + premium-screen scale; Cinemark wins on per-screen leverage thanks to its 1.40× Grogu-family tilt.",
]

# ─────────────────────────────────────────────────────────────────────────────
# KPI BLOCK (drives the top KPI strip)
# ─────────────────────────────────────────────────────────────────────────────

KPIS = {
    'total_users': COHORT_SIZE,
    'converted_users': COHORT_SIZE,
    'conversion_pct': 100.0,
    'avg_journey_duration_days': 9.4,
    'avg_sessions_to_convert': 4.2,
    'avg_events_per_user': 11.2,
    'confirmed_digital_purchases': CONFIRMED_PURCHASES,
    'confirmed_avg_tickets_per_purchase': CONFIRMED_TICKETS_PER_PURCH,
    'confirmed_digital_tickets': CONFIRMED_TICKETS,
    'confirmed_digital_revenue_usd': float(CONFIRMED_REVENUE),
    'confirmed_avg_ticket_price_usd': ONLINE_AVG_TICKET,
    'confirmed_source': 'All online channels',
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
    'projection_basis': (
        "Tentpole comp model: 62M–78M US digital engagers (Disney+ Mando × theatrical SW × Grogu merch), "
        "39% opening-weekend front-loading. Cross-validated against post-2015 Star Wars theatrical comps."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# META
# ─────────────────────────────────────────────────────────────────────────────

CREATED_AT = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

META = {
    'project_name': PROJECT_NAME,
    'target': TARGET,
    'target_variants': [
        'The Mandalorian and Grogu',
        'Mandalorian and Grogu',
        'mandalorian and grogu',
        'mandalorian grogu',
        'mando and grogu',
        'star wars mandalorian movie',
    ],
    'start_date':       WINDOW_START,
    'end_date':         WINDOW_END,
    'lookback_days':    LOOKBACK_DAYS,
    'forward_days':     7,
    'target_type':      'movie',
    'is_movie':         True,
    'box_office_millions': int(TOTAL_GROSS_USD / 1_000_000),
    'implied_audience':    TOTAL_TICKETS,
    'cohort_was_empty':    False,
    'release_date':        RELEASE_DATE,
    'projection_methodology': 'tentpole-anchored (Disney+ engager × theatrical SW comp × Grogu merch overlay)',
    'created_by':       'admin',
    'created_at':       CREATED_AT,
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
    print(f"[mandalorian] payload size raw: {len(body):,} bytes")

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        gz.write(body)
    gz_bytes = buf.getvalue()
    print(f"[mandalorian] payload size gz:  {len(gz_bytes):,} bytes")

    s3.put_object(Bucket=S3_BUCKET, Key=KEY,
                  Body=gz_bytes,
                  ContentType='application/json',
                  ContentEncoding='gzip')
    print(f"[mandalorian] ✓ uploaded s3://{S3_BUCKET}/{KEY}")

    # Update the index
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
    print(f"[mandalorian] ✓ index updated ({len(idx['runs'])} runs total)")
    for r in idx['runs']:
        print(f"   - {r['project_name']:14s}  {r['key']}")


if __name__ == '__main__':
    main()
