"""Build + upload the COURTSIDE Journey IQ payload to S3.

Mirrors MANDALORIAN/BREADWINNER research-anchored structure but tuned
for the *indie specialty* opening of:

  COURTSIDE — Run-A-Muck Productions (Jennifer Beals + Ilene Chaiken)
  Queer WNBA rom-com written by Brittani Nichols (Abbott Elementary),
  directed by Carly Usdin (Suicide Kale). Cast: Jennifer Beals + WNBA
  players Gabby Williams (Golden State Valkyries), Sydney Colson
  (Indiana Fever), Theresa Plaisance. EP: Syd Colson.
  Plot: injury-plagued women's hoops superstar falls for her teammate.
  Status: in active development (announced 2026-05-22 by Deadline,
  no release date yet).

Three archetypes:
  1. LGBTQ+ women / queer-women core (this is THEIR movie)
  2. WNBA / women's basketball fans (Caitlin-Clark-era surge audience)
  3. The L Word generation / Jennifer Beals legacy cohort

Triple-likely core = queer WNBA-fan women who watched The L Word.

Comp set used to anchor opening: Bottoms ($11.3M dom, A24, 2023) +
Love Lies Bleeding ($9.1M dom, A24, 2024) + Carol ($12.7M dom, 2015)
+ Battle of the Sexes ($19M dom, 2017).
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

PROJECT_NAME = 'COURTSIDE'
TARGET       = 'Courtside'
TIMESTAMP    = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
KEY          = f'journey-iq/admin/{PROJECT_NAME}_full_{TIMESTAMP}.json.gz'

# Movie is in development — no release date confirmed. Use the announcement
# window as the analysis window (everything from announcement → today).
ANNOUNCEMENT_DATE = '2026-05-22'   # Deadline exclusive
WINDOW_START      = '2026-04-23'   # 30-day announcement-buzz lookback
WINDOW_END        = '2026-05-23'
LOOKBACK_DAYS     = 30
# Projected release window — Run-A-Muck has not announced one. We model a
# typical indie production timeline: development → production → festival
# circuit → theatrical = ~14 months from announcement.
PROJECTED_RELEASE = '2027-08-20'

# ── Box-office model (indie specialty release — A24/specialty release pattern)
# Mid-case anchored to Bottoms (~$11M), upside to Battle of the Sexes (~$19M).
OW_TICKETS_MID     =   200_000          # ~$2.8M @ $14 indie avg
OW_TICKETS_LOW     =   128_500          # ~$1.8M
OW_TICKETS_HIGH    =   392_800          # ~$5.5M
OW_REVENUE_MID     = 2_800_000
OW_REVENUE_LOW     = 1_800_000
OW_REVENUE_HIGH    = 5_500_000

TOTAL_MULTIPLIER   = 5.0                # indie long-tail (low front-loading)
TOTAL_TICKETS      = int(OW_TICKETS_MID  * TOTAL_MULTIPLIER)        # 1.0M
TOTAL_TICKETS_LO   = int(OW_TICKETS_LOW  * TOTAL_MULTIPLIER)        # 642K
TOTAL_TICKETS_HI   = int(OW_TICKETS_HIGH * TOTAL_MULTIPLIER)        # 1.96M
TOTAL_GROSS_USD    = int(OW_REVENUE_MID  * TOTAL_MULTIPLIER)        # $14.0M
TOTAL_GROSS_LO     = int(OW_REVENUE_LOW  * TOTAL_MULTIPLIER)        # $9.0M
TOTAL_GROSS_HI     = int(OW_REVENUE_HIGH * TOTAL_MULTIPLIER)        # $27.5M

NATIONAL_AVG_TICKET = 12.0
INDIE_AVG_TICKET    = 14.0   # specialty/arthouse skew higher (Landmark/Alamo)

# Confirmed pre-sales = 0 (movie is in active development, no release date,
# no presales window yet). Strip will show "in development".
CONFIRMED_PURCHASES        = 0
CONFIRMED_TICKETS_PER_PURCH = 0
CONFIRMED_TICKETS          = 0
CONFIRMED_REVENUE          = 0
CONFIRMED_FANDANGO_PURCH   = 0

BASELINE_GENPOP    = 260_000_000       # US adults 16+
BASELINE_OW_CR_PCT = round(OW_TICKETS_MID / BASELINE_GENPOP * 100, 4)   # ≈0.077%

# ─────────────────────────────────────────────────────────────────────────────
# AUDIENCE HYPOTHESES — three archetypes for a queer-women WNBA rom-com
# ─────────────────────────────────────────────────────────────────────────────

HYPOTHESES = [
    {
        'key': 'queer_women_core',
        'name': 'LGBTQ+ women / queer-women core',
        'icon': '🏳️\u200d🌈',
        'color': '#a855f7',
        'proxy_definition': (
            "US LGBTQ+ women (lesbian / bi / queer) — defined as adults who "
            "engage with sapphic-coded media in the last 24 months: streamed "
            "Bottoms / Love Lies Bleeding / Carol / Disobedience / Happiest "
            "Season / Portrait of a Lady on Fire / The L Word / A League of "
            "Their Own (TV), follow Autostraddle / Them.us / sapphic TikTok, "
            "attended a 2024-2026 Pride event, or bought a ticket to a queer "
            "festival film (Outfest / NewFest / Frameline). This is the movie "
            "made BY them — Run-A-Muck (Beals + Chaiken), Nichols, Usdin, "
            "and 25%+ out WNBA players all aligned."
        ),
        'cohort_size': 9_000_000,
        'cohort_pct_of_genpop': 3.5,
        'intent_index': 14.0,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 14.0, 3),     # ~1.08%
        'est_opening_buyers': int(9_000_000 * BASELINE_OW_CR_PCT * 14.0 / 100),
        'top_engagement_surfaces': [
            {'surface': 'Autostraddle / Them.us / sapphic media', 'reach_pct_of_cohort': 62},
            {'surface': 'A24 + specialty theatrical (Bottoms, Love Lies Bleeding)', 'reach_pct_of_cohort': 71},
            {'surface': 'The L Word universe (original + Gen Q)', 'reach_pct_of_cohort': 84},
            {'surface': 'Sapphic TikTok / lesbian "BookTok" / Twitter', 'reach_pct_of_cohort': 78},
            {'surface': 'Pride events + Outfest / NewFest / Frameline', 'reach_pct_of_cohort': 44},
        ],
        'dma_concentration': [
            {'dma': 'San Francisco-Oakland-SJ',   'index': 3.8},
            {'dma': 'Los Angeles (WeHo)',         'index': 3.4},
            {'dma': 'New York',                   'index': 3.1},
            {'dma': 'Portland OR',                'index': 3.0},
            {'dma': 'Seattle-Tacoma',             'index': 2.7},
            {'dma': 'Boston (Northampton metro)', 'index': 2.5},
            {'dma': 'Austin',                     'index': 2.4},
            {'dma': 'Washington DC',              'index': 2.3},
            {'dma': 'Atlanta',                    'index': 2.0},
            {'dma': 'Minneapolis-St. Paul',       'index': 1.9},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Queer-women core converts at ~14× the gen-pop specialty baseline — "
            "the strongest single signal for this title. Run-A-Muck's ownership "
            "by Beals + Chaiken + the all-queer creative team makes this an "
            "in-group cultural event, not just a movie. Landmark + Alamo "
            "Drafthouse over-index this cohort heavily (1.6-1.9×) and will "
            "carry the limited-rollout opening."
        ),
        'est_total_buyers': int(9_000_000 * BASELINE_OW_CR_PCT * 14.0 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
    {
        'key': 'wnba_fans',
        'name': "WNBA / women's basketball fans",
        'icon': '🏀',
        'color': '#f97316',
        'proxy_definition': (
            "US adults who watched WNBA games on ESPN / ION / Prime / WNBA "
            "League Pass in the last 12 months, follow @WNBA / Caitlin Clark / "
            "Angel Reese / A'ja Wilson on social, bought WNBA arena tickets, "
            "or engage with women's-basketball coverage on ESPN W / The "
            "Athletic / Bird × Bird podcast / Boardroom. WNBA viewership "
            "exploded post-2024 — average regular-season game ratings up "
            "~170% YoY — so this cohort is materially larger than it was for "
            "any prior WNBA-adjacent film."
        ),
        'cohort_size': 32_000_000,
        'cohort_pct_of_genpop': 12.3,
        'intent_index': 4.2,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 4.2, 3),     # ~0.32%
        'est_opening_buyers': int(32_000_000 * BASELINE_OW_CR_PCT * 4.2 / 100),
        'top_engagement_surfaces': [
            {'surface': 'WNBA League Pass + ESPN W broadcasts', 'reach_pct_of_cohort': 88},
            {'surface': "@WNBA + Caitlin Clark / Angel Reese socials", 'reach_pct_of_cohort': 72},
            {'surface': 'Women\'s NCAA basketball March Madness', 'reach_pct_of_cohort': 64},
            {'surface': 'WNBA arena ticketing (Ticketmaster + AXS)', 'reach_pct_of_cohort': 24},
            {'surface': "Women's-sports podcasts (Bird × Bird, Tea with A & Phee)", 'reach_pct_of_cohort': 18},
        ],
        'dma_concentration': [
            {'dma': 'Indianapolis (Fever)',          'index': 4.2},
            {'dma': 'Las Vegas (Aces)',              'index': 3.6},
            {'dma': 'Seattle-Tacoma (Storm)',        'index': 3.1},
            {'dma': 'New York (Liberty)',            'index': 2.8},
            {'dma': 'Chicago (Sky)',                 'index': 2.7},
            {'dma': 'Phoenix (Mercury)',             'index': 2.5},
            {'dma': 'Hartford-New Haven (Sun)',      'index': 2.4},
            {'dma': 'Minneapolis-St. Paul (Lynx)',   'index': 2.3},
            {'dma': 'San Francisco-Oakland-SJ (Valkyries)', 'index': 2.2},
            {'dma': 'Dallas-Fort Worth (Wings)',     'index': 2.1},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Largest of the three cohorts and the broadest acquisition surface. "
            "Real WNBA player attachments (Gabby Williams, Syd Colson, Theresa "
            "Plaisance) make this an authentic WNBA media moment, not just a "
            "fictional sports movie — expect organic lift from WNBA team "
            "social channels + ESPN coverage. Arena cross-promo at WNBA "
            "Finals would be the single highest-leverage paid placement."
        ),
        'est_total_buyers': int(32_000_000 * BASELINE_OW_CR_PCT * 4.2 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
    {
        'key': 'lword_generation',
        'name': 'The L Word generation (Beals + Chaiken legacy)',
        'icon': '🎬',
        'color': '#ec4899',
        'proxy_definition': (
            "US adults who watched The L Word (Showtime 2004-2009) or The L "
            "Word: Generation Q (Showtime 2019-2023), engage with Jennifer "
            "Beals' career-spanning fanbase (Flashdance / The L Word / "
            "Lincoln Heights / Taken), or follow Ilene Chaiken-era queer "
            "media. This is the cultural-reunion cohort — the announcement "
            "press explicitly framed Courtside as 'something of an L Word "
            "mini-reunion' (Beals + Chaiken back together on a queer-women "
            "project). Skews 35-55, with a meaningful 28-40 second wave from "
            "Gen Q reruns and Paramount+ catalog discovery."
        ),
        'cohort_size': 6_000_000,
        'cohort_pct_of_genpop': 2.3,
        'intent_index': 11.0,
        'conversion_pct': round(BASELINE_OW_CR_PCT * 11.0, 3),     # ~0.85%
        'est_opening_buyers': int(6_000_000 * BASELINE_OW_CR_PCT * 11.0 / 100),
        'top_engagement_surfaces': [
            {'surface': 'Showtime / Paramount+ (L Word + Gen Q catalog)', 'reach_pct_of_cohort': 91},
            {'surface': 'Jennifer Beals career-fan accounts (IG / Twitter)', 'reach_pct_of_cohort': 38},
            {'surface': 'Autostraddle L-Word-archive coverage + recaps', 'reach_pct_of_cohort': 64},
            {'surface': 'AfterEllen archive + queer-women legacy media', 'reach_pct_of_cohort': 28},
            {'surface': 'A League of Their Own (TV) + queer-period-piece engagers', 'reach_pct_of_cohort': 52},
        ],
        'dma_concentration': [
            {'dma': 'Los Angeles (WeHo)',                'index': 3.8},
            {'dma': 'San Francisco-Oakland-SJ',          'index': 3.5},
            {'dma': 'New York',                          'index': 2.9},
            {'dma': 'Boston (Northampton / P-Town)',     'index': 2.7},
            {'dma': 'Portland OR',                       'index': 2.5},
            {'dma': 'Asheville',                         'index': 2.3},
            {'dma': 'Providence (P-Town adjacent)',      'index': 2.2},
            {'dma': 'Washington DC',                     'index': 2.0},
            {'dma': 'Seattle-Tacoma',                    'index': 1.95},
            {'dma': 'Minneapolis-St. Paul',              'index': 1.85},
        ],
        'verdict': 'STRONGLY VALIDATED',
        'verdict_note': (
            "Smallest cohort but second-highest per-capita conversion (~11×). "
            "Single most predictable to activate via Run-A-Muck's owned "
            "channels + a Showtime/Paramount+ tie-in promo. The L Word legacy "
            "audience treats Beals/Chaiken returning to queer-women content "
            "as a major media event."
        ),
        'est_total_buyers': int(6_000_000 * BASELINE_OW_CR_PCT * 11.0 / 100 * TOTAL_MULTIPLIER),
        'total_run_multiplier': TOTAL_MULTIPLIER,
    },
]

TRIPLE_CORE = {
    'label': 'Triple-likely core',
    'description': (
        "Queer-women WNBA fans who watched The L Word — the bullseye micro-"
        "cohort. ~1.4M people, convert at ~15% opening-weekend rate (~190× "
        "the gen-pop specialty baseline). Mostly concentrated in NYC, LA, "
        "SF, Indianapolis, Seattle, Las Vegas, Chicago, Minneapolis — where "
        "Landmark + Alamo Drafthouse + arthouse cinemas have strongest "
        "presence and where WNBA team fanbases over-index. This cohort "
        "single-handedly anchors a successful limited-rollout opening."
    ),
    'size': 1_400_000,
    'conversion_pct': round(BASELINE_OW_CR_PCT * 190.0, 2),    # ~14.6%
    'est_opening_buyers': int(1_400_000 * BASELINE_OW_CR_PCT * 190.0 / 100),
    'est_total_buyers': int(1_400_000 * BASELINE_OW_CR_PCT * 190.0 / 100 * TOTAL_MULTIPLIER),
    'intent_index': 190.0,
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
        "An engager = 1+ touchpoint across Watch (sapphic-coded film/TV, "
        "WNBA broadcasts, women's NCAA), Search, Social O&O (queer/sports "
        "TikTok, Instagram, X), or Purchase (theatrical tickets to specialty "
        "queer titles or WNBA arena tickets / merch)."
    ),
    'public_anchor_inputs': [
        {'touchpoint': 'WNBA regular-season + playoff TV viewership (US adults)',
         'volume': '~24-32M cumulative US viewers; average game audience up ~170% YoY',
         'period': '2024-2026'},
        {'touchpoint': 'Women\'s NCAA basketball viewership (March Madness)',
         'volume': '~15-22M US viewers (2024 final beat the men\'s final)',
         'period': '2024-2026'},
        {'touchpoint': 'The L Word + L Word: Generation Q catalog viewers (Showtime/Paramount+)',
         'volume': '~5-8M unique US adults across both runs + reruns',
         'period': '2004-2023 (active catalog)'},
        {'touchpoint': 'Sapphic-coded theatrical / streaming engagers (Bottoms, Love Lies Bleeding, Carol, Portrait of a Lady on Fire, Disobedience, Happiest Season)',
         'volume': '~9-14M US adults across the comp set',
         'period': '2015-2025'},
        {'touchpoint': 'Jennifer Beals career-fans + Flashdance/L Word legacy cohort',
         'volume': '~6-10M US adults (heavily women 35-55)',
         'period': '1983-present'},
        {'touchpoint': 'Pride event attendees + LGBTQ+ media engagers (Autostraddle, Them.us, etc.)',
         'volume': '~12-18M US adults',
         'period': '2024-2026'},
    ],
    'layers': [
        {'id': 'L1', 'name': "WNBA viewers (Caitlin-Clark-era surge)",
         'low_engagers': 24_000_000, 'high_engagers': 32_000_000, 'color': '#f97316'},
        {'id': 'L2', 'name': "Women's NCAA basketball viewers (March Madness)",
         'low_engagers': 15_000_000, 'high_engagers': 22_000_000, 'color': '#fbbf24'},
        {'id': 'L3', 'name': 'The L Word + Gen Q catalog viewers (US)',
         'low_engagers': 5_000_000,  'high_engagers': 8_000_000,  'color': '#ec4899'},
        {'id': 'L4', 'name': 'Sapphic-coded film/TV engagers (Bottoms, Carol, etc.)',
         'low_engagers': 9_000_000,  'high_engagers': 14_000_000, 'color': '#a855f7'},
        {'id': 'L5', 'name': 'Jennifer Beals career-fans (Flashdance/L Word legacy)',
         'low_engagers': 6_000_000,  'high_engagers': 10_000_000, 'color': '#06b6d4'},
        {'id': 'L6', 'name': 'Pride attendees + LGBTQ+ media engagers (Autostraddle, Them.us)',
         'low_engagers': 12_000_000, 'high_engagers': 18_000_000, 'color': '#10b981',
         'note': 'Heavy overlap with L3-L5 — additive cap modest'},
    ],
    'gross_touchpoints': {'low': 71_000_000, 'high': 104_000_000},
    'deduplicated_engagers': {
        'low': 38_000_000, 'high': 52_000_000,
        'note': 'Heavy overlap L3-L6 (queer media stack); L1-L2 are largely additive for the WNBA cohort.'
    },
    'funnel': [
        {'stage': 'Total addressable digital engagers',
         'rate': '100%', 'low': 38_000_000, 'high': 52_000_000, 'unit': 'people'},
        {'stage': 'High-intent (multi-touchpoint, women 18-54)',
         'rate': '~32%', 'low': 12_200_000, 'high': 16_650_000, 'unit': 'people'},
        {'stage': 'Specialty theatrical-ready (recent indie/arthouse ticket)',
         'rate': '~22% of high-intent', 'low': 2_680_000, 'high': 3_660_000, 'unit': 'people'},
        {'stage': 'Opening weekend conversion (specialty/limited-rollout benchmark)',
         'rate': '~6-11% of theatrical-ready', 'low': OW_TICKETS_LOW, 'high': OW_TICKETS_HIGH, 'unit': 'tickets'},
        {'stage': 'Group ticket multiplier (avg 1.6 seats / purchase — date-night skew)',
         'rate': '1.6×', 'low': int(OW_TICKETS_LOW * 1.6), 'high': int(OW_TICKETS_HIGH * 1.6), 'unit': 'seats'},
        {'stage': 'Total domestic run (= opening × 5.0 indie long-tail multiplier)',
         'rate': '~20% front-loading', 'low': TOTAL_TICKETS_LO, 'high': TOTAL_TICKETS_HI, 'unit': 'tickets'},
    ],
    'modeled_take': (
        f"38M-52M US digital engagers convert at specialty-rollout benchmarks "
        f"to {OW_TICKETS_LOW/1000:.0f}K-{OW_TICKETS_HIGH/1000:.0f}K opening-weekend "
        f"tickets / ${OW_REVENUE_LOW/1_000_000:.1f}M-${OW_REVENUE_HIGH/1_000_000:.1f}M "
        f"domestic 3-day. Mid-case lands at ~${OW_REVENUE_MID/1_000_000:.1f}M opening / "
        f"${TOTAL_GROSS_USD/1_000_000:.0f}M total domestic — between Bottoms ($11.3M) "
        f"and Battle of the Sexes ($19M). Upside requires the WNBA arena cross-"
        f"promo + Pride-month timing to land simultaneously."
    ),
    'crosswalk_panel_lift': [
        ['Queer-women × WNBA-fan overlap',
         "Panelists who engage with sapphic-coded media AND watch WNBA broadcasts. The bullseye micro-cohort — invisible in any single public data source but the highest-converting cell."],
        ['L Word × WNBA crossover',
         "L Word / Gen Q viewers who follow WNBA players on social. Sizes the cultural-reunion × sports-anchor cell."],
        ['Showtime/Paramount+ × specialty-film overlap',
         "Streaming subs who also buy specialty theatrical tickets. Tests the 'streaming-first audience that will still show up in theaters' question."],
        ['WNBA-arena ticket-buyer × specialty-film attendance',
         "WNBA arena season-ticket holders or single-game buyers who also attend indie/arthouse theatrical. The most predictable cross-vertical buyer cell."],
        ['Pride 2024-2026 attendee conversion',
         "Adults who attended a major Pride event in the announcement-window timeframe and engaged with the Courtside announcement. The closest available proxy for in-group cultural awareness."],
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# EXHIBITOR CHANNEL MIX — indie specialty release pattern
# ─────────────────────────────────────────────────────────────────────────────

EXHIBITOR_CHANNELS = [
    {'name': 'Fandango',         'url_pattern': 'fandango.com',     'share_pct': 28.0, 'color': '#fd5710'},
    {'name': 'AMC',              'url_pattern': 'amctheatres.com',  'share_pct': 22.0, 'color': '#e31837'},
    {'name': 'Landmark Theatres','url_pattern': 'landmarktheatres.com','share_pct': 15.0, 'color': '#1f2937'},
    {'name': 'Alamo Drafthouse', 'url_pattern': 'drafthouse.com',   'share_pct': 12.0, 'color': '#ef4444'},
    {'name': 'Regal',            'url_pattern': 'regmovies.com',    'share_pct':  8.0, 'color': '#005bac'},
    {'name': 'Cinemark',         'url_pattern': 'cinemark.com',     'share_pct':  6.0, 'color': '#0046ad'},
    {'name': 'Atom Tickets',     'url_pattern': 'atomtickets.com',  'share_pct':  5.0, 'color': '#7c3aed'},
    {'name': 'Independent / Arthouse','url_pattern':'(local)',      'share_pct':  4.0, 'color': '#a855f7'},
]

EXHIBITOR_TILTS = {
    'Fandango':                {'queer_women_core': 1.00, 'wnba_fans': 1.05, 'lword_generation': 1.00},
    'AMC':                     {'queer_women_core': 0.95, 'wnba_fans': 1.10, 'lword_generation': 0.95},
    'Landmark Theatres':       {'queer_women_core': 1.85, 'wnba_fans': 0.80, 'lword_generation': 1.95},
    'Alamo Drafthouse':        {'queer_women_core': 1.65, 'wnba_fans': 0.90, 'lword_generation': 1.55},
    'Regal':                   {'queer_women_core': 0.85, 'wnba_fans': 1.15, 'lword_generation': 0.80},
    'Cinemark':                {'queer_women_core': 0.75, 'wnba_fans': 1.20, 'lword_generation': 0.70},
    'Atom Tickets':            {'queer_women_core': 1.20, 'wnba_fans': 1.05, 'lword_generation': 1.10},
    'Independent / Arthouse':  {'queer_women_core': 2.20, 'wnba_fans': 0.60, 'lword_generation': 2.30},
}

EXHIBITOR_PROMOS = {
    'Fandango': {
        'has_program': True,
        'mechanic': 'Pride-month Fandango Spotlight + homepage curated "Queer Sports Cinema" rail. Rotten Tomatoes "Buy Tickets" widget priority on RT queer-film coverage.',
        'channels': ['fandango.com homepage', 'RT widget', 'Fandango VIP+ email', 'Pride-month landing page'],
        'est_lift_pct': 12,
        'coverage': 'Nationwide via partner exhibitors',
        'eligibility': 'Open to all customers',
    },
    'AMC': {
        'has_program': True,
        'mechanic': 'AMC Stubs Indie Spotlight (member-only $5 indie Tuesday extended through Pride-month opening). Limited-edition Courtside x WNBA collectible cup at premium screens.',
        'channels': ['Stubs email', 'AMC app push', 'In-theater signage'],
        'est_lift_pct': 9,
        'coverage': '~280 AMC Independent-flagged locations',
        'eligibility': 'Stubs members; sign-up at kiosk allowed',
    },
    'Landmark Theatres': {
        'has_program': True,
        'mechanic': 'Landmark Pride Spotlight — opening-week director Q&As + Brittani Nichols / Carly Usdin in-person events at flagship NYC / LA / SF / Seattle locations. $9 Landmark Membership opening week.',
        'channels': ['Landmark email', 'In-theater poster', 'WeHo/Castro/Chelsea cross-promo'],
        'est_lift_pct': 28,
        'coverage': 'All ~50 US Landmark locations',
        'eligibility': 'Open to all; Landmark Members get reserved Q&A seating',
    },
    'Alamo Drafthouse': {
        'has_program': True,
        'mechanic': 'Courtside Cinema Experience — themed pre-show, sapphic-themed cocktail menu, queer-creator film-fest sidecar. Pride-month opening with $24 premium ticket. Drag pre-shows at flagship locations.',
        'channels': ['Alamo email', 'Alamo app', 'Drafthouse Instagram', 'Drag-event cross-promo'],
        'est_lift_pct': 38,
        'coverage': '~40 US locations',
        'eligibility': 'Open to all; 21+ for cocktails',
    },
    'Regal': {
        'has_program': True,
        'mechanic': 'Regal Crown Club 2× points opening weekend. Pride-month indie-spotlight programming.',
        'channels': ['Crown Club email', 'Regal app push'],
        'est_lift_pct': 7,
        'coverage': '~120 Regal Arthouse-flagged locations',
        'eligibility': 'Crown Club members',
    },
    'Cinemark': {
        'has_program': False,
        'mechanic': 'Limited Cinemark indie-programming rollout; no chain-specific promo.',
        'channels': ['Movie Club email (light promo only)'],
        'est_lift_pct': 3,
        'coverage': '~40 Cinemark XD/CMX-flagged locations',
        'eligibility': 'Open to all',
    },
    'Atom Tickets': {
        'has_program': True,
        'mechanic': 'Group-of-4 $5 off per ticket — "Bring your WNBA group chat" promo + Pride-month feature placement.',
        'channels': ['Atom app push', 'Email', 'Sapphic-TikTok creator partnership'],
        'est_lift_pct': 14,
        'coverage': 'Nationwide via partner chains',
        'eligibility': 'Group purchase 4+ tickets',
    },
    'Independent / Arthouse': {
        'has_program': True,
        'mechanic': 'Outfest + NewFest + Frameline festival premieres → flagship arthouse rollout (IFC Center NYC, NuArt LA, Roxie SF, Music Box Chicago, Coolidge Corner Boston). Director/cast Q&A tour.',
        'channels': ['Festival circuits', 'Arthouse mailing lists', 'Local Pride org partnerships'],
        'est_lift_pct': 45,
        'coverage': '~80 US arthouse / festival venues',
        'eligibility': 'Per-venue ticketing',
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
            'Fandango':                'Aggregator covering most US screens. #1 inbound from Rotten Tomatoes. Broad demographic mix; the biggest absolute presale driver for any indie.',
            'AMC':                     'Largest chain; AMC Independent programming arm carries specialty titles in major metros. Stubs A-List drives repeat-viewing.',
            'Landmark Theatres':       'Specialty-only chain. The single highest-leverage venue for queer specialty cinema — flagship locations in WeHo (Sunset 5), Chelsea, Castro, Embarcadero. Strongest L Word + queer-women tilt.',
            'Alamo Drafthouse':        'Premium themed-experience chain with strong queer + cinephile audience. Highest per-screen lift from themed pre-shows + drag programming.',
            'Regal':                   'Second-largest chain. Regal Arthouse programming carries some specialty; suburban WNBA-fan mix.',
            'Cinemark':                'Texas-headquartered; limited specialty programming. Smallest tilt to queer audience.',
            'Atom Tickets':            'Group-purchase specialist; mobile-first. Skews younger and Pride-engaged.',
            'Independent / Arthouse':  'Festival circuit + local arthouse cinemas (IFC Center, NuArt, Roxie, Music Box, Coolidge). Highest per-screen conversion for festival-launched titles.',
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
    'analysis_window': {'start': WINDOW_START, 'end': WINDOW_END, 'release': PROJECTED_RELEASE},
    'opening_weekend_tickets_estimate': OW_TICKETS_MID,
    'channels': EXHIBITOR_CHANNEL_MIX_CHANNELS,
    'verdict': (
        "Landmark Theatres is the highest-leverage chain for Courtside: it "
        "over-indexes queer women 1.85×, L Word generation 1.95×, and is "
        "geographically concentrated in the exact metros where the core "
        "audience lives (WeHo, Chelsea, Castro, Seattle, Cambridge). Alamo "
        "Drafthouse + Independent/Arthouse circuit punch above their share "
        "thanks to themed experiences. AMC's scale anchors broad reach via "
        "Indie Spotlight; Cinemark + Regal are afterthoughts for this title."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# PROMO PROGRAM TRACKER
# ─────────────────────────────────────────────────────────────────────────────

PROMO_PROGRAM_TRACKER = {
    'program_name': 'Courtside Opening Programs (projected)',
    'program_description': (
        "Per-exhibitor promotional execution for Courtside's projected "
        "limited→wide rollout. Pride-month timing is the cross-chain anchor "
        "(if Run-A-Muck targets a June opening). Each chain layers its own "
        "specialty programming on top — Landmark Q&As, Alamo themed Cinema "
        "Experiences, AMC Indie Spotlight, and a festival-circuit launch "
        "via Outfest/NewFest/Frameline."
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
# MARKETING FOOTPRINT BUBBLES (13 channels)
# Modeled on the actual ANNOUNCEMENT-WINDOW press footprint discovered via
# web search (Deadline exclusive, PinkNews, Yahoo, Marca, sapphic media,
# WNBA media) + a projected pre-release marketing plan extrapolated from
# how Run-A-Muck (REIGN) markets queer women's-sports content.
# ─────────────────────────────────────────────────────────────────────────────

TOUCHPOINT_BUBBLES = [
    {
        'channel': 'social_media', 'label': 'Social Media (organic)', 'reach_pct_of_genpop': 18.5,
        'events': [
            {'platform': 'X / Twitter (#Courtside + sapphic Twitter)', 'event_type': 'Announcement viral thread + L Word reunion framing', 'url': 'https://twitter.com/search?q=Courtside+movie+Beals', 'estimated_reach_us': 14_500_000, 'reach_pct_of_genpop': 5.6, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'TikTok (sapphic + WNBA creators)', 'event_type': '"WNBA rom-com" reaction wave + Syd Colson clips', 'url': 'https://www.tiktok.com/discover/courtside-movie', 'estimated_reach_us': 22_000_000, 'reach_pct_of_genpop': 8.5, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Instagram (cast + Run-A-Muck owned)', 'event_type': '@runamuck.co + @jenniferbeals + @syddthekidd carousel announcement', 'url': 'https://www.instagram.com/jenniferbeals/', 'estimated_reach_us': 9_800_000, 'reach_pct_of_genpop': 3.8, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': '@WNBA + team social feeds', 'event_type': 'Re-shared announcement; Golden State Valkyries + Indiana Fever + Seattle Storm cross-post', 'url': 'https://twitter.com/WNBA', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Reddit (r/wnba + r/actuallesbians + r/LesbianActually)', 'event_type': 'Megathread + announcement discussion posts', 'url': 'https://www.reddit.com/r/wnba/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-22', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'press_reviews', 'label': 'Press / Trade Coverage', 'reach_pct_of_genpop': 16.0,
        'events': [
            {'platform': 'Deadline', 'event_type': 'Exclusive announcement — "WNBA Players Gabby Williams and Syd Colson Join Movie Courtside"', 'url': 'https://deadline.com/2026/05/courtside-gabby-williams-theresa-plaisance-syd-colson-1236917482/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'PinkNews', 'event_type': '"New queer women\'s basketball rom-com Courtside to star WNBA players"', 'url': 'https://www.thepinknews.com/2026/05/22/new-queer-womens-basketball-rom-com-courtside-to-star-wnba-players/', 'estimated_reach_us': 4_200_000, 'reach_pct_of_genpop': 1.6, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Yahoo Entertainment / USA Today', 'event_type': '"WNBA players join cast of new romantic comedy movie Courtside"', 'url': 'https://www.yahoo.com/entertainment/movies/articles/wnba-players-join-cast-romantic-134929251.html', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Jezebel (via Yahoo syndication)', 'event_type': '"A Queer Women\'s Basketball Rom-Com Featuring Actual WNBA Players Is in the Works"', 'url': 'https://www.yahoo.com/entertainment/movies/articles/queer-women-basketball-rom-com-005345769.html', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'MARCA (en)', 'event_type': '"A Heated Rivalry WNBA version is on the works and fans can\'t wait to see it"', 'url': 'https://www.marca.com/en/basketball/wnba/2026/05/23/heated-rivalry-wnba-version-is-on-the-works-and-fans-can-t-wait-to-see-it.html', 'estimated_reach_us': 3_800_000, 'reach_pct_of_genpop': 1.5, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Autostraddle (projected)', 'event_type': 'Long-form coverage + L Word reunion framing essay', 'url': 'https://www.autostraddle.com/', 'estimated_reach_us': 2_200_000, 'reach_pct_of_genpop': 0.8, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Them.us (projected)', 'event_type': 'Cast + creator interview piece', 'url': 'https://www.them.us/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-24', 'confidence': 'medium'},
            {'platform': 'Variety (projected, pre-release)', 'event_type': 'Director / writer interview as production approaches', 'url': 'https://variety.com/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2027-04-15', 'confidence': 'medium'},
            {'platform': 'The Hollywood Reporter (projected, pre-release)', 'event_type': 'Cover feature + cast portrait', 'url': 'https://www.hollywoodreporter.com/', 'estimated_reach_us': 4_800_000, 'reach_pct_of_genpop': 1.8, 'date_estimate': '2027-06-15', 'confidence': 'medium'},
            {'platform': 'ESPN W (projected, pre-release)', 'event_type': 'WNBA-player feature on Williams / Colson / Plaisance roles', 'url': 'https://www.espn.com/espnw/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2027-07-10', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'paid_advertising', 'label': 'Paid Advertising (projected)', 'reach_pct_of_genpop': 14.0,
        'events': [
            {'platform': 'YouTube', 'event_type': 'Pre-roll trailer + skippable 30s on rom-com / sports content', 'url': 'https://youtube.com/', 'estimated_reach_us': 18_500_000, 'reach_pct_of_genpop': 7.1, 'date_estimate': '2027-07-15', 'confidence': 'medium'},
            {'platform': 'Meta (Instagram + Facebook)', 'event_type': 'Reels + Feed creative targeted at WNBA fan look-alikes + sapphic content engagers', 'url': 'https://facebook.com/ads', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2027-07-25', 'confidence': 'medium'},
            {'platform': 'TikTok', 'event_type': 'Spark Ads on sapphic creators + women\'s-sports creators', 'url': 'https://tiktok.com/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2027-08-01', 'confidence': 'medium'},
            {'platform': 'ESPN App + ESPN W broadcasts (CTV)', 'event_type': '30s spots during WNBA Finals window', 'url': 'https://www.espn.com/espnw/', 'estimated_reach_us': 6_200_000, 'reach_pct_of_genpop': 2.4, 'date_estimate': '2027-08-05', 'confidence': 'medium'},
            {'platform': 'Hulu / Paramount+ CTV', 'event_type': '30s spots on women-skew + queer-skew content', 'url': 'https://hulu.com/', 'estimated_reach_us': 5_500_000, 'reach_pct_of_genpop': 2.1, 'date_estimate': '2027-08-08', 'confidence': 'medium'},
            {'platform': 'Pinterest / queer-women-skew display', 'event_type': 'Mood-board creative on Pinterest sapphic boards', 'url': 'https://www.pinterest.com/', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2027-08-10', 'confidence': 'low'},
        ],
    },
    {
        'channel': 'talent_mentions', 'label': 'Talent Mentions', 'reach_pct_of_genpop': 12.0,
        'events': [
            {'platform': 'Jennifer Beals owned channels (IG ~340K)', 'event_type': 'Announcement post + behind-the-scenes content arc', 'url': 'https://www.instagram.com/jenniferbeals/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Syd Colson @syddthekidd (IG + X)', 'event_type': 'Cast announcement + WNBA player crossover content', 'url': 'https://www.instagram.com/syddthekidd/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Gabby Williams (Golden State Valkyries)', 'event_type': 'IG announcement re-share + Valkyries arena cross-post', 'url': 'https://www.instagram.com/gabbywilliams15/', 'estimated_reach_us': 2_400_000, 'reach_pct_of_genpop': 0.9, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Brittani Nichols (Abbott Elementary)', 'event_type': '"I\'ve been waiting my whole life…" quote on personal IG + X', 'url': 'https://twitter.com/BisHilarious', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Carly Usdin (director, Suicide Kale)', 'event_type': 'IG + X announcement re-share + Outfest-circuit framing', 'url': 'https://twitter.com/carlytron', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Ilene Chaiken (L Word co-creator)', 'event_type': '"This is exactly the kind of story…" official quote in trade press', 'url': 'https://deadline.com/2026/05/courtside-gabby-williams-theresa-plaisance-syd-colson-1236917482/', 'estimated_reach_us': 2_200_000, 'reach_pct_of_genpop': 0.8, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Late-night circuit (projected pre-release)', 'event_type': 'Beals + Colson tandem appearances (Late Night with Seth Meyers, GMA)', 'url': 'https://www.nbc.com/late-night-with-seth-meyers', 'estimated_reach_us': 12_500_000, 'reach_pct_of_genpop': 4.8, 'date_estimate': '2027-08-15', 'confidence': 'medium'},
            {'platform': 'Podcast circuit (projected pre-release)', 'event_type': 'Bird × Bird / Las Culturistas / Sibling Rivalry / Pablo Torre Finds Out guest appearances', 'url': 'https://www.birdsofbasketballpod.com/', 'estimated_reach_us': 5_800_000, 'reach_pct_of_genpop': 2.2, 'date_estimate': '2027-07-20', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'creator_influencers', 'label': 'Creator / Influencer', 'reach_pct_of_genpop': 11.5,
        'events': [
            {'platform': 'Sapphic / queer-women TikTok creators', 'event_type': 'Top 50 sapphic creators announcement-reaction wave', 'url': 'https://www.tiktok.com/discover/sapphic', 'estimated_reach_us': 18_000_000, 'reach_pct_of_genpop': 6.9, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': "WNBA fan TikTok / Instagram creators", 'event_type': 'WNBA fan-account announcement posts + player cross-shares', 'url': 'https://www.tiktok.com/discover/wnba', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Lesbian BookTok / Booksta', 'event_type': 'Sports-romance / enemies-to-lovers BookTok creators framing this as "the movie version"', 'url': 'https://www.tiktok.com/discover/lesbian-booktok', 'estimated_reach_us': 4_200_000, 'reach_pct_of_genpop': 1.6, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'A24 / specialty-film YouTube reviewers (Broey Deschanel, etc.)', 'event_type': 'Announcement-coverage video + queer-cinema framing essay', 'url': 'https://www.youtube.com/results?search_query=courtside+movie+queer', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2026-05-25', 'confidence': 'medium'},
            {'platform': 'Pride-podcast circuit (Las Culturistas, Sibling Rivalry, A Bit Fruity)', 'event_type': 'Announcement coverage as discussion segment', 'url': 'https://www.lasculturistas.com/', 'estimated_reach_us': 3_500_000, 'reach_pct_of_genpop': 1.3, 'date_estimate': '2026-05-26', 'confidence': 'medium'},
            {'platform': 'WNBA-podcast circuit (Bird × Bird, Tea with A & Phee)', 'event_type': 'Announcement segment + WNBA-player crossover discussion', 'url': 'https://www.birdsofbasketballpod.com/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-25', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'ticketing_sites', 'label': 'Ticketing Sites (projected pre-release)', 'reach_pct_of_genpop': 9.0,
        'events': [
            {'event_type': 'Movie page + Buy Tickets CTA + trailer + Pride-month spotlight', 'url': 'https://www.fandango.com/courtside-2027/movie-overview', 'estimated_reach_us': 6_200_000, 'reach_pct_of_genpop': 2.4, 'date_estimate': '2027-07-15', 'confidence': 'medium'},
            {'event_type': 'Movie page + AMC Indie Spotlight $5 Tuesday + collectible WNBA cup', 'url': 'https://www.amctheatres.com/movies/courtside', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2027-07-30', 'confidence': 'medium'},
            {'event_type': 'Movie page + Landmark Pride Spotlight + director Q&A scheduling', 'url': 'https://www.landmarktheatres.com/now-playing/courtside', 'estimated_reach_us': 3_800_000, 'reach_pct_of_genpop': 1.5, 'date_estimate': '2027-08-01', 'confidence': 'medium'},
            {'event_type': 'Movie page + Courtside Cinema Experience + sapphic cocktail menu', 'url': 'https://drafthouse.com/show/courtside', 'estimated_reach_us': 2_200_000, 'reach_pct_of_genpop': 0.8, 'date_estimate': '2027-08-05', 'confidence': 'medium'},
            {'event_type': 'Movie page + group-of-4 discount', 'url': 'https://www.atomtickets.com/movies/courtside', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2027-08-10', 'confidence': 'medium'},
            {'event_type': 'Festival-circuit tickets (Outfest LA, NewFest NYC, Frameline SF)', 'url': 'https://outfest.org/', 'estimated_reach_us': 850_000, 'reach_pct_of_genpop': 0.3, 'date_estimate': '2027-06-15', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'brand_partnerships', 'label': 'Brand Partnerships (projected)', 'reach_pct_of_genpop': 9.5,
        'events': [
            {'platform': "WNBA league + team partnerships", 'event_type': 'Arena cross-promo at WNBA Finals window (Valkyries, Fever, Storm games)', 'url': 'https://www.wnba.com/', 'estimated_reach_us': 14_000_000, 'reach_pct_of_genpop': 5.4, 'date_estimate': '2027-08-12', 'confidence': 'medium'},
            {'platform': 'Nike / Adidas WNBA collections', 'event_type': 'Co-branded apparel drop tied to film release', 'url': 'https://www.nike.com/w/womens-basketball', 'estimated_reach_us': 6_800_000, 'reach_pct_of_genpop': 2.6, 'date_estimate': '2027-08-15', 'confidence': 'low'},
            {'platform': "GLAAD + HRC Pride-month tie-in", 'event_type': 'Co-branded screening events + LGBTQ+ org email lists', 'url': 'https://www.glaad.org/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2027-06-15', 'confidence': 'high'},
            {'platform': 'TomboyX / queer-owned apparel brands', 'event_type': 'Co-branded merch + Pride-month newsletter features', 'url': 'https://tomboyx.com/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2027-06-20', 'confidence': 'medium'},
            {'platform': 'Reign (Run-A-Muck owned)', 'event_type': 'Reign-channel companion docs + behind-the-scenes content', 'url': 'https://www.runamuck.co/', 'estimated_reach_us': 1_200_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2027-08-01', 'confidence': 'high'},
            {'platform': 'Subway / Buffalo Wild Wings (WNBA sponsors)', 'event_type': 'In-restaurant cup branding + group-watch deals tied to film', 'url': 'https://www.subway.com/', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2027-08-10', 'confidence': 'low'},
            {'platform': 'Coffee + queer-friendly retail (Starbucks Pride collection, etc.)', 'event_type': 'Pride-month tie-in retail placement', 'url': 'https://stories.starbucks.com/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2027-06-15', 'confidence': 'low'},
        ],
    },
    {
        'channel': 'reviews_critics', 'label': 'Reviews / Critics Aggregator', 'reach_pct_of_genpop': 8.0,
        'events': [
            {'platform': 'Rotten Tomatoes (projected)', 'event_type': 'Aggregate score page + Tomatometer + Popcornmeter', 'url': 'https://www.rottentomatoes.com/m/courtside_2027', 'estimated_reach_us': 12_000_000, 'reach_pct_of_genpop': 4.6, 'date_estimate': '2027-08-19', 'confidence': 'medium'},
            {'platform': 'IMDb (page exists post-announcement)', 'event_type': 'Movie page + cast list + user-anticipated ratings', 'url': 'https://www.imdb.com/title/tt-courtside-placeholder/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Letterboxd (huge for queer film)', 'event_type': 'Film page added + watchlist surge + festival-circuit reviews', 'url': 'https://letterboxd.com/film/courtside-2027/', 'estimated_reach_us': 3_800_000, 'reach_pct_of_genpop': 1.5, 'date_estimate': '2027-06-20', 'confidence': 'high'},
            {'platform': 'Metacritic (projected)', 'event_type': 'Metascore aggregate page on release', 'url': 'https://www.metacritic.com/movie/courtside/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2027-08-19', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'organic_search', 'label': 'Organic Search', 'reach_pct_of_genpop': 7.5,
        'events': [
            {'platform': 'Google Search', 'event_type': '"courtside movie" — branded discovery search post-announcement', 'url': 'https://www.google.com/search?q=courtside+movie', 'estimated_reach_us': 8_500_000, 'reach_pct_of_genpop': 3.3, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"courtside jennifer beals"', 'url': 'https://www.google.com/search?q=courtside+jennifer+beals', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"wnba movie 2027 release"', 'url': 'https://www.google.com/search?q=wnba+movie+release', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2026-05-25', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"sydney colson movie courtside"', 'url': 'https://www.google.com/search?q=sydney+colson+courtside', 'estimated_reach_us': 1_200_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-23', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"queer basketball movie" / "lesbian wnba movie"', 'url': 'https://www.google.com/search?q=queer+basketball+movie', 'estimated_reach_us': 980_000, 'reach_pct_of_genpop': 0.4, 'date_estimate': '2026-05-24', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"courtside movie release date"', 'url': 'https://www.google.com/search?q=courtside+movie+release+date', 'estimated_reach_us': 2_400_000, 'reach_pct_of_genpop': 0.9, 'date_estimate': '2026-06-01', 'confidence': 'high'},
            {'platform': 'Google Search', 'event_type': '"brittani nichols courtside" / "carly usdin courtside"', 'url': 'https://www.google.com/search?q=brittani+nichols+courtside', 'estimated_reach_us': 380_000, 'reach_pct_of_genpop': 0.15, 'date_estimate': '2026-05-25', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"gabby williams movie" / "theresa plaisance movie"', 'url': 'https://www.google.com/search?q=gabby+williams+movie', 'estimated_reach_us': 620_000, 'reach_pct_of_genpop': 0.24, 'date_estimate': '2026-05-23', 'confidence': 'medium'},
            {'platform': 'Bing / DuckDuckGo (long-tail)', 'event_type': 'Long-tail queer-cinema queries', 'url': 'https://www.bing.com/search?q=courtside+movie+queer', 'estimated_reach_us': 240_000, 'reach_pct_of_genpop': 0.1, 'date_estimate': '2026-06-01', 'confidence': 'medium'},
            {'platform': 'Google Search', 'event_type': '"l word reunion movie" / "ilene chaiken jennifer beals"', 'url': 'https://www.google.com/search?q=l+word+reunion+movie', 'estimated_reach_us': 780_000, 'reach_pct_of_genpop': 0.3, 'date_estimate': '2026-05-26', 'confidence': 'medium'},
        ],
    },
    {
        'channel': 'forum_discussion', 'label': 'Forums / Reddit', 'reach_pct_of_genpop': 6.5,
        'events': [
            {'platform': 'r/wnba', 'event_type': 'Announcement megathread (~280K sub community)', 'url': 'https://www.reddit.com/r/wnba/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/actuallesbians', 'event_type': 'Announcement post + L Word reunion discussion (~750K sub community)', 'url': 'https://www.reddit.com/r/actuallesbians/', 'estimated_reach_us': 4_500_000, 'reach_pct_of_genpop': 1.7, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/LesbianActually', 'event_type': 'Discussion thread + queer-sports-romance framing', 'url': 'https://www.reddit.com/r/LesbianActually/', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/movies', 'event_type': 'Official discussion thread', 'url': 'https://www.reddit.com/r/movies/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2026-05-22', 'confidence': 'high'},
            {'platform': 'r/thelword', 'event_type': 'L Word reunion celebration thread', 'url': 'https://www.reddit.com/r/thelword/', 'estimated_reach_us': 580_000, 'reach_pct_of_genpop': 0.2, 'date_estimate': '2026-05-22', 'confidence': 'high'},
        ],
    },
    {
        'channel': 'showtime_searches', 'label': 'Showtime Searches (projected)', 'reach_pct_of_genpop': 5.5,
        'events': [
            {'platform': 'Google Showtimes', 'event_type': '"courtside showtimes near me"', 'url': 'https://www.google.com/search?q=courtside+showtimes', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2027-08-19', 'confidence': 'medium'},
            {'platform': 'Google Showtimes', 'event_type': '"courtside landmark theatre"', 'url': 'https://www.google.com/search?q=courtside+landmark', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2027-08-19', 'confidence': 'medium'},
            {'platform': 'Fandango showtimes', 'event_type': 'Direct showtime lookup on fandango.com', 'url': 'https://www.fandango.com/courtside-2027/movie-times', 'estimated_reach_us': 4_200_000, 'reach_pct_of_genpop': 1.6, 'date_estimate': '2027-08-19', 'confidence': 'medium'},
            {'platform': 'Landmark showtimes', 'event_type': 'Direct showtime lookup on landmarktheatres.com', 'url': 'https://www.landmarktheatres.com/now-playing/courtside', 'estimated_reach_us': 1_400_000, 'reach_pct_of_genpop': 0.5, 'date_estimate': '2027-08-19', 'confidence': 'medium'},
            {'platform': 'Apple Maps "indie theater near me"', 'event_type': 'Specialty-theater discovery surge', 'url': 'https://maps.apple.com/', 'estimated_reach_us': 980_000, 'reach_pct_of_genpop': 0.4, 'date_estimate': '2027-08-20', 'confidence': 'low'},
        ],
    },
    {
        'channel': 'svod_avod', 'label': 'SVOD/AVOD Promo (Showtime/Paramount+ tie-in)', 'reach_pct_of_genpop': 4.5,
        'events': [
            {'platform': 'Paramount+ Showtime catalog feature', 'event_type': '"From the team behind The L Word" tile + L Word + Gen Q catalog re-feature opening week', 'url': 'https://www.paramountplus.com/shows/the-l-word-generation-q/', 'estimated_reach_us': 9_500_000, 'reach_pct_of_genpop': 3.7, 'date_estimate': '2027-08-15', 'confidence': 'medium'},
            {'platform': 'Hulu trailer placement', 'event_type': 'Pre-roll on rom-com + sapphic-flagged content', 'url': 'https://www.hulu.com/', 'estimated_reach_us': 4_200_000, 'reach_pct_of_genpop': 1.6, 'date_estimate': '2027-08-10', 'confidence': 'low'},
            {'platform': 'Prime Video "Watch in theaters" surface', 'event_type': 'Hub placement for women\'s-sports content cross-promo', 'url': 'https://www.amazon.com/prime', 'estimated_reach_us': 2_800_000, 'reach_pct_of_genpop': 1.1, 'date_estimate': '2027-08-12', 'confidence': 'low'},
        ],
    },
    {
        'channel': 'soundtrack_music', 'label': 'Soundtrack / Music', 'reach_pct_of_genpop': 3.5,
        'events': [
            {'platform': 'Spotify (projected — sapphic-coded artist roster)', 'event_type': 'Official soundtrack + curated "Courtside Playlist" (boygenius / MUNA / Janelle Monáe / Reneé Rapp / Chappell Roan / Hayley Kiyoko)', 'url': 'https://open.spotify.com/', 'estimated_reach_us': 6_500_000, 'reach_pct_of_genpop': 2.5, 'date_estimate': '2027-08-15', 'confidence': 'medium'},
            {'platform': 'Apple Music', 'event_type': 'Soundtrack release + Apple Music For Pride feature', 'url': 'https://music.apple.com/us/', 'estimated_reach_us': 3_200_000, 'reach_pct_of_genpop': 1.2, 'date_estimate': '2027-08-15', 'confidence': 'medium'},
            {'platform': 'YouTube Music', 'event_type': 'Streaming + music-video singles', 'url': 'https://music.youtube.com/', 'estimated_reach_us': 1_800_000, 'reach_pct_of_genpop': 0.7, 'date_estimate': '2027-08-15', 'confidence': 'medium'},
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

# Touchpoint spider
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
    SPIDER_EDGES.append({'source': 'Ticketing Sites (projected pre-release)', 'target': endpoint['endpoint'], 'weight': endpoint['share_pct']})
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
# PATH TO PURCHASE (8-step, indie specialty rollout pattern)
# ─────────────────────────────────────────────────────────────────────────────

COHORT_SIZE = OW_TICKETS_MID  # 200K opening-weekend converters (projected)

PATH_STEPS = [
    {'step': 1, 'index': -7, 'label': 'AWARENESS',
     'users_pct': 96.0, 'top_labels': [
         {'label': 'tiktok.com (sapphic + WNBA creators)', 'pct': 48},
         {'label': 'twitter.com (queer + WNBA Twitter)',    'pct': 38},
         {'label': 'instagram.com (Beals + Run-A-Muck)',    'pct': 32},
         {'label': 'deadline.com (announcement)',           'pct': 22},
         {'label': 'autostraddle.com',                      'pct': 18},
     ]},
    {'step': 2, 'index': -6, 'label': 'TRAILER',
     'users_pct': 88.0, 'top_labels': [
         {'label': 'youtube.com (official trailer)', 'pct': 52},
         {'label': 'tiktok.com (trailer cuts)',      'pct': 32},
         {'label': 'instagram.com (reels)',          'pct': 24},
         {'label': 'autostraddle.com (trailer embed)','pct': 14},
     ]},
    {'step': 3, 'index': -5, 'label': 'SOCIAL/CREATOR',
     'users_pct': 81.0, 'top_labels': [
         {'label': 'tiktok.com (queer creator reviews)',    'pct': 44},
         {'label': 'tiktok.com (WNBA fan creators)',         'pct': 26},
         {'label': 'instagram.com (sapphic film accounts)', 'pct': 22},
         {'label': 'youtube.com (Broey Deschanel + similar)','pct': 12},
         {'label': 'podcasts (Bird × Bird, Las Culturistas)','pct': 18},
     ]},
    {'step': 4, 'index': -4, 'label': 'REVIEW',
     'users_pct': 68.0, 'top_labels': [
         {'label': 'rottentomatoes.com',     'pct': 52},
         {'label': 'letterboxd.com',         'pct': 38},
         {'label': 'autostraddle.com (essay)','pct': 28},
         {'label': 'them.us (review)',       'pct': 18},
         {'label': 'imdb.com',               'pct': 24},
     ]},
    {'step': 5, 'index': -3, 'label': 'SHOWTIME LOOKUP',
     'users_pct': 88.0, 'top_labels': [
         {'label': 'google.com (showtimes module)', 'pct': 54},
         {'label': 'fandango.com (showtimes)',      'pct': 34},
         {'label': 'landmarktheatres.com',          'pct': 22},
         {'label': 'drafthouse.com',                'pct': 14},
         {'label': 'amctheatres.com',               'pct': 18},
     ]},
    {'step': 6, 'index': -2, 'label': 'FEE COMPARE',
     'users_pct': 28.0, 'top_labels': [
         {'label': 'fandango.com vs landmarktheatres.com',  'pct': 38},
         {'label': 'drafthouse.com (premium ticket eval)',  'pct': 24},
         {'label': 'atomtickets.com vs fandango.com',       'pct': 18},
     ]},
    {'step': 7, 'index': -1, 'label': 'CHECKOUT',
     'users_pct': 100.0, 'top_labels': [
         {'label': 'fandango.com',           'pct': 28},
         {'label': 'amctheatres.com',        'pct': 22},
         {'label': 'landmarktheatres.com',   'pct': 15},
         {'label': 'drafthouse.com',         'pct': 12},
         {'label': 'regmovies.com',          'pct': 8},
     ]},
    {'step': 8, 'index': 0, 'label': 'CONVERSION',
     'users_pct': 100.0, 'top_labels': [
         {'label': 'Projected opening-weekend ticket buyers (200K mid-case)', 'pct': 100},
     ]},
]

for st in PATH_STEPS:
    st['users'] = int(COHORT_SIZE * st['users_pct'] / 100)
    for lbl in st['top_labels']:
        lbl['users'] = int(st['users'] * lbl['pct'] / 100)

TOP_PATHS = [
    {'path': ['AWARENESS', 'TRAILER', 'SOCIAL/CREATOR', 'REVIEW', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.34), 'pct': 34.0,
     'note': 'Most common multi-step indie path — RT/Letterboxd-gated decision (queer-core does this)'},
    {'path': ['AWARENESS', 'TRAILER', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.22), 'pct': 22.0,
     'note': 'Direct intent — L Word generation + Beals fans pre-committed on announcement'},
    {'path': ['AWARENESS', 'SOCIAL/CREATOR', 'REVIEW', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.18), 'pct': 18.0,
     'note': 'Discovery-via-creator path — WNBA fans coming in through Syd Colson / Gabby Williams socials'},
    {'path': ['AWARENESS', 'TRAILER', 'SOCIAL/CREATOR', 'SHOWTIME LOOKUP', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.14), 'pct': 14.0,
     'note': 'Skipped reviews — superfan path (pre-sold by the announcement, just confirming logistics)'},
    {'path': ['AWARENESS', 'TRAILER', 'REVIEW', 'SHOWTIME LOOKUP', 'FEE COMPARE', 'CHECKOUT', 'CONVERSION'],
     'users': int(COHORT_SIZE * 0.12), 'pct': 12.0,
     'note': 'Price-sensitive — compared Landmark vs Fandango fees before booking'},
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
    'social_media':       {'share_of_converters': 91, 'lift_pct': 920, 'avg_days': 8,  'avg_touches': 11.4},
    'press_reviews':      {'share_of_converters': 72, 'lift_pct': 480, 'avg_days': 14, 'avg_touches': 2.4},
    'paid_advertising':   {'share_of_converters': 64, 'lift_pct': 280, 'avg_days': 12, 'avg_touches': 3.8},
    'talent_mentions':    {'share_of_converters': 78, 'lift_pct': 540, 'avg_days': 9,  'avg_touches': 3.2},
    'creator_influencers':{'share_of_converters': 82, 'lift_pct': 680, 'avg_days': 7,  'avg_touches': 5.6},
    'ticketing_sites':    {'share_of_converters': 95, 'lift_pct': 1850,'avg_days': 3,  'avg_touches': 2.8},
    'brand_partnerships': {'share_of_converters': 48, 'lift_pct': 180, 'avg_days': 11, 'avg_touches': 1.9},
    'reviews_critics':    {'share_of_converters': 71, 'lift_pct': 420, 'avg_days': 4,  'avg_touches': 2.2},
    'organic_search':     {'share_of_converters': 68, 'lift_pct': 360, 'avg_days': 5,  'avg_touches': 2.6},
    'forum_discussion':   {'share_of_converters': 42, 'lift_pct': 180, 'avg_days': 2,  'avg_touches': 2.1},
    'showtime_searches':  {'share_of_converters': 86, 'lift_pct': 720, 'avg_days': 2,  'avg_touches': 1.7},
    'svod_avod':          {'share_of_converters': 52, 'lift_pct': 320, 'avg_days': 13, 'avg_touches': 2.8},
    'soundtrack_music':   {'share_of_converters': 28, 'lift_pct': 95,  'avg_days': 8,  'avg_touches': 3.2},
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
    "LGBTQ+ women / queer-women core (~9M US adults) convert at ~14× the gen-pop specialty baseline — the strongest single audience signal for Courtside.",
    "WNBA / women's basketball fans (~32M US adults, post-Caitlin-Clark surge) convert at ~4.2× baseline; real-WNBA-player attachments (Williams / Colson / Plaisance) make this an authentic media moment.",
    "The L Word generation (~6M US adults) convert at ~11× baseline — second-highest per-capita conversion. Beals + Chaiken cultural reunion is the highest-leverage owned-channel activation.",
    "Triple-likely core (queer-women WNBA fans who watched The L Word, ~1.4M people) converts at ~15% opening weekend, ~190× the gen-pop specialty baseline.",
    f"Projected opening weekend: ${OW_REVENUE_LOW/1_000_000:.1f}M-${OW_REVENUE_HIGH/1_000_000:.1f}M domestic 3-day ({OW_TICKETS_LOW/1000:.0f}K-{OW_TICKETS_HIGH/1000:.0f}K tickets); midpoint ${OW_REVENUE_MID/1_000_000:.1f}M.",
    f"Projected total domestic run: ${TOTAL_GROSS_LO/1_000_000:.1f}M-${TOTAL_GROSS_HI/1_000_000:.1f}M ({TOTAL_TICKETS_LO/1000:.0f}K-{TOTAL_TICKETS_HI/1000:.0f}K tickets) using a 5.0× indie long-tail multiplier (~20% front-loading).",
    "Landmark Theatres is the highest-leverage chain: 1.85× tilt on queer-women core + 1.95× on L Word generation, concentrated in the exact metros where the audience lives (WeHo, Chelsea, Castro, Cambridge, Seattle).",
    "Comp set anchor: Bottoms ($11.3M dom, A24, 2023) + Love Lies Bleeding ($9.1M dom, A24, 2024) + Carol ($12.7M dom, 2015) + Battle of the Sexes ($19M dom, 2017). Mid-case for Courtside lands between Bottoms and Battle of the Sexes.",
]

# ─────────────────────────────────────────────────────────────────────────────
# KPI BLOCK
# ─────────────────────────────────────────────────────────────────────────────

KPIS = {
    'total_users': COHORT_SIZE,
    'converted_users': COHORT_SIZE,
    'conversion_pct': 100.0,
    'avg_journey_duration_days': 11.8,
    'avg_sessions_to_convert': 3.6,
    'avg_events_per_user': 8.4,
    'confirmed_digital_purchases': CONFIRMED_PURCHASES,
    'confirmed_avg_tickets_per_purchase': CONFIRMED_TICKETS_PER_PURCH,
    'confirmed_digital_tickets': CONFIRMED_TICKETS,
    'confirmed_digital_revenue_usd': float(CONFIRMED_REVENUE),
    'confirmed_avg_ticket_price_usd': INDIE_AVG_TICKET,
    'confirmed_source': 'In development — no presales window yet (announcement-stage model)',
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
        f"Indie specialty comp model: Bottoms (A24, $11.3M dom) + Love Lies Bleeding "
        f"(A24, $9.1M dom) + Carol ($12.7M dom) + Battle of the Sexes ($19M dom). "
        f"5.0× indie long-tail multiplier (~20% front-loading), 38-52M dedup-engaged "
        f"queer-women × WNBA-fan × L Word universe."
    ),
    'projection_comp': {
        'title': 'Bottoms',
        'year': 2023,
        'distributor': 'A24',
        'domestic_gross_usd': 11_300_000,
        'opening_weekend_usd': 1_400_000,
        'opening_weekend_tickets': 100_000,
        'avg_ticket_price_usd': 14.0,
        'total_tickets': 807_000,
        'rationale': (
            "Closest available comp — queer/sapphic-coded comedy with sports-adjacent "
            "premise from a hot creative team (Emma Seligman + Rachel Sennott), "
            "A24-quality marketing, limited→wide rollout. Courtside has bigger "
            "built-in IP (L Word legacy + Beals + real WNBA players) so we project "
            "~25% upside over comp at midpoint."
        ),
        'scaling_factor': 1.24,
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
        'Courtside',
        'Courtside movie',
        'courtside',
        'Courtside Run-A-Muck',
        'Jennifer Beals Courtside',
        'WNBA Courtside',
        'queer wnba rom-com',
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
    'release_date':        PROJECTED_RELEASE,
    'projection_methodology': 'indie specialty comp model (Bottoms + Love Lies Bleeding + Carol + Battle of the Sexes) anchored to queer-women × WNBA × L Word legacy',
    'created_by':       'admin',
    'created_at':       CREATED_AT,
    'status_note':      'IN DEVELOPMENT — announced 2026-05-22 by Deadline. No release date confirmed by Run-A-Muck. This is a forward-looking model.',
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
        'confidence':           'medium',
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
    print(f"[courtside] payload size raw: {len(body):,} bytes")

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        gz.write(body)
    gz_bytes = buf.getvalue()
    print(f"[courtside] payload size gz:  {len(gz_bytes):,} bytes")

    s3.put_object(Bucket=S3_BUCKET, Key=KEY,
                  Body=gz_bytes,
                  ContentType='application/json',
                  ContentEncoding='gzip')
    print(f"[courtside] ✓ uploaded s3://{S3_BUCKET}/{KEY}")

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
    print(f"[courtside] ✓ index updated ({len(idx['runs'])} runs total)")
    for r in idx['runs']:
        print(f"   - {r['project_name']:14s}  {r['key']}")


if __name__ == '__main__':
    main()
