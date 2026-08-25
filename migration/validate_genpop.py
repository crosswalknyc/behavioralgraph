"""
validate_genpop.py
==================

Audit-only validator that compares GENPOP_CORRECTIONS in
bg-webapp/genpop_calibration.py against SEC-derived ground truth.

Approach
--------
For each brand we know the canonical US-reach-equivalent from a primary
source (10-K, 10-Q, MAU disclosures, Nielsen/Comscore panel reports). We
convert that to an implied % of the US adult population (≈259M adults, but
the pipeline uses 329.9M total population so we use 329.9M to match).

For some brand types the right comparison is "subscribers" * "shared
account viewer multiplier" because more than one person uses each
subscription:

    Streaming SVOD:  ~2.3 viewers/household account
    Music streaming: ~1.1 (most accounts are individual)
    Banking (primary): 1.15 (joint accounts; not exact)
    Wallets (Apple Pay/Venmo): 1.0
    Telecom: 1.2 (family plans = multiple line users)
    Retail (Nike DTC etc): 1.0 (active-customer count IS person-count)
    QSR: not subscriber-based; use 30-day visitor reach reported by surveys

The script does NOT modify anything. It only reports:
   • current_pct  : what GENPOP_CORRECTIONS says
   • implied_us   : current_pct / 100 * 329.9M
   • truth_pct    : ground-truth-derived % (sub_count * mult / 329.9M * 100)
   • truth_us     : the implied reach from ground truth
   • delta_pp     : (current_pct - truth_pct), percentage points
   • flag         : OK | LOW (>5pp under) | HIGH (>5pp over) | WAY OFF (>15pp)

Run: python3 migration/validate_genpop.py
"""

import os
import sys
import json
from typing import Optional

# Locate genpop_calibration.py
# 2026-06-10 (Jenna 3am audit RCA): use append, not insert(0). The original
# `sys.path.insert(0, ...)` shoved `bg-webapp` to the front of sys.path
# during module-load, which caused subsequent `from post_generation_enforcers
# import ...` calls in BG.py to resolve to the STALE bg-webapp copy
# (missing apply_strip_tilde_from_brand_input, apply_porn_leader_invariant,
# renormalize_demographics_to_100, etc.). The wiring block then silently
# fell through its outer try/except → 19 J-cohort profiles shipped with
# D88 tilde, G1 sequential, ROUND_2DP, D118 leader-break ALL surviving.
# Append leaves migration's path-priority intact while still letting
# genpop_calibration resolve as a fallback.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_bg_webapp_dir = os.path.join(ROOT, 'bg-webapp')
if _bg_webapp_dir not in sys.path:
    sys.path.append(_bg_webapp_dir)
from genpop_calibration import GENPOP_CORRECTIONS  # type: ignore

US_POPULATION = 329_900_000  # matches pipeline constant

# ──────────────────────────────────────────────────────────────────────
# GROUND TRUTH
# ──────────────────────────────────────────────────────────────────────
# Format per entry: dict with these keys (only fill what you know):
#   us_subscribers   : count from 10-K / 10-Q (US-only when available)
#   us_active_customers: for retail (used same way as subscribers)
#   us_mau           : monthly active users (social/apps)
#   us_reach_pct_direct: directly-reported panel % if known (skips math)
#   shared_mult      : avg people per account (1.0 default)
#   notes            : citation / source line
#
# Implied reach % = (count * shared_mult) / US_POPULATION * 100
# If us_reach_pct_direct is set, that is used directly.
GROUND_TRUTH: dict[tuple[str, str], dict] = {

    # ─────────── STREAMING / SVOD (Q4 FY2025 disclosures) ───────────
    # SVOD shared_mult ~2.3 (account → adults+kids viewing)
    ('STREAMING/PLATFORM', 'NETFLIX'): {
        'us_subscribers': 89_600_000,  # Netflix Q4 2024: 89.6M US/Canada paid; ~80M US
        'shared_mult': 2.3,
        'notes': 'Q4 2024 10-K UCAN segment, US ~80M households',
    },
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): {
        'us_reach_pct_direct': 45.0,
        'notes': 'Prime is ~76% of US households but PV viewing is much narrower than Prime membership. Manually set to 45% (Jenna 2026-05-14): being-a-Prime-member ≠ active PV viewer; Antenna/MRI active-streamer panels show ~45% 30-day reach',
    },
    ('STREAMING/PLATFORM', 'HULU'): {
        'us_subscribers': 54_000_000,  # Disney FY24 10-K: 54M Hulu+SVOD+LiveTV
        'shared_mult': 2.1,
        'notes': 'Disney FY2024 10-K: Hulu SVOD+LiveTV 54.0M paid',
    },
    ('STREAMING/PLATFORM', 'DISNEY+'): {
        'us_subscribers': 58_700_000,  # Disney FY24 10-K: D+ Core US/Canada
        'shared_mult': 2.5,  # family-skewed
        'notes': 'Disney FY2024 10-K: 58.7M US/Canada paid',
    },
    ('STREAMING/PLATFORM', 'HBO MAX'): {
        'us_subscribers': 56_000_000,  # WBD FY24: 56M Max US (Direct-to-Consumer)
        'shared_mult': 2.2,
        'notes': 'WBD FY2024 10-K Direct-to-Consumer US subscriber count',
    },
    ('STREAMING/PLATFORM', 'MAX'): {
        'us_subscribers': 56_000_000,
        'shared_mult': 2.2,
        'notes': 'same as HBO MAX',
    },
    ('STREAMING/PLATFORM', 'APPLE TV+'): {
        # Apple does not disclose; Antenna 2024 estimates ~28M US subs incl
        # bundled-via-Apple-One. Liz/Jenna: closer to 20M US active accounts.
        # 20M × 2.3 shared = ~14% reach. Account for promo/free-trial uplift.
        'us_subscribers': 20_000_000,
        'shared_mult': 2.3,
        'notes': 'Analyst estimate 14M US paying (Apple does not disclose); JPM/MoffettNathanson',
    },
    ('STREAMING/PLATFORM', 'PEACOCK'): {
        'us_subscribers': 36_000_000,  # Comcast Q4 2024: 36M paid
        'shared_mult': 2.0,
        'notes': 'Comcast FY2024 10-K: 36M paid US',
    },
    ('STREAMING/PLATFORM', 'PARAMOUNT+'): {
        'us_subscribers': 30_000_000,  # Paramount FY24: 77M global, ~30M US
        'shared_mult': 2.1,
        'notes': 'Paramount FY2024 10-K: 77.5M global; US ~30M',
    },

    # ─────────── STREAMING / MUSIC ───────────
    # shared_mult ~1.1 (most music accounts are individual; family plans rare)
    ('STREAMING/MUSIC', 'SPOTIFY'): {
        'us_subscribers': 75_000_000,  # Spotify FY24: 263M premium global, US ~75M paid; +120M US ad-supported
        'shared_mult': 1.0,
        'us_reach_pct_direct': 40.0,
        'notes': 'Spotify FY2024 20-F: 263M premium global; US paid ~75M, free MAU ~120M. Manually set to 40% (Jenna 2026-05-14): Edison Infinite Dial 30-day ~39%; conservative on free-tier overlap',
    },
    ('STREAMING/MUSIC', 'APPLE MUSIC'): {
        'us_subscribers': 45_000_000,  # Apple does not disclose; analyst estimates ~88M global, ~45M US
        'shared_mult': 1.1,
        'notes': 'Analyst estimate 45M US paying',
    },
    ('STREAMING/MUSIC', 'YOUTUBE MUSIC'): {
        'us_subscribers': 28_000_000,  # Google announced 100M YT Music+Premium global Q4 2024; US ~28M
        'shared_mult': 1.1,
        'us_reach_pct_direct': 14.0,
        'notes': 'Google: 100M YT Music+Premium global; US ~28M paying. Manually set to 14% (Jenna 2026-05-14): undercount of 9.3% misses Premium-bundled users who play music inside the main YouTube app + the algorithm push from YT proper',
    },
    ('STREAMING/MUSIC', 'AMAZON MUSIC'): {
        'us_subscribers': 32_000_000,  # Analyst est: 82M global, 32M US Prime-included + paid
        'shared_mult': 1.0,
        'notes': 'Analyst est: 82M global, 32M US (mostly Prime-included)',
    },
    ('STREAMING/MUSIC', 'PANDORA MUSIC'): {
        'us_subscribers': 46_000_000,  # SiriusXM FY24: 46M Pandora monthly active
        'shared_mult': 1.0,
        'notes': 'SiriusXM FY2024 10-K: 46M Pandora MAU (US-only product)',
        'is_mau_basis': True,
    },
    ('STREAMING/MUSIC', 'SIRIUSXM'): {
        'us_subscribers': 33_000_000,  # SiriusXM FY24 10-K: 33M paid self-pay subs
        'shared_mult': 1.6,  # in-car listeners
        'notes': 'SiriusXM FY2024 10-K: 33M self-pay subs; in-car shared listening',
    },

    # ─────────── SOCIAL MEDIA (US MAU from 10-Q / public statements) ───────────
    ('SOCIAL MEDIA', 'YOUTUBE'): {
        'us_reach_pct_direct': 84.0,
        'notes': 'Pew Research 2024: 83% of US adults use YouTube',
    },
    ('SOCIAL MEDIA', 'FACEBOOK'): {
        'us_reach_pct_direct': 68.0,
        'notes': 'Pew 2024: 68% of US adults; META Q4 2024: ~196M US/CA DAP',
    },
    ('SOCIAL MEDIA', 'INSTAGRAM'): {
        'us_reach_pct_direct': 50.0,
        'notes': 'Pew 2024: 47%; META Q3 2024 mentions 169M US MAU = 51%',
    },
    ('SOCIAL MEDIA', 'TIKTOK'): {
        'us_reach_pct_direct': 47.0,
        'notes': 'Pew 2024: 33% (rising); ByteDance: 170M US MAU = 52%; midpoint 47%',
    },
    ('SOCIAL MEDIA', 'SNAPCHAT'): {
        'us_reach_pct_direct': 27.0,
        'notes': 'Snap Q4 2024 10-K: 100M N.America DAU; US MAU ~110M = 33%; survey-based 27%',
    },
    ('SOCIAL MEDIA', 'X'): {
        'us_reach_pct_direct': 22.0,
        'notes': 'X is private; eMarketer 2024: ~22% US adult MAU',
    },
    ('SOCIAL MEDIA', 'TWITTER'): {
        'us_reach_pct_direct': 22.0,
        'notes': 'same as X',
    },
    ('SOCIAL MEDIA', 'REDDIT'): {
        'us_reach_pct_direct': 22.0,
        'notes': 'Reddit Q3 2024 10-Q: 97M global DAU; US ~52M MAU = 20%; weekly 22%',
    },
    ('SOCIAL MEDIA', 'PINTEREST'): {
        'us_reach_pct_direct': 35.0,
        'notes': 'Pinterest Q4 2024 10-K: 96M N.America MAU = 35% of NA adults',
    },
    ('SOCIAL MEDIA', 'LINKEDIN'): {
        'us_reach_pct_direct': 28.0,
        'notes': 'Pew 2024: 30%; LinkedIn does not break out US MAU (~70M monthly = 27%)',
    },
    ('SOCIAL MEDIA', 'DISCORD'): {
        'us_reach_pct_direct': 16.0,
        'notes': 'Discord private: 200M global MAU; ~50M US = 15%',
    },
    ('SOCIAL MEDIA', 'TWITCH'): {
        'us_reach_pct_direct': 8.5,
        'notes': 'Twitch: 35M N.America MAU = 13%; daily reach much lower ~8-9%',
    },

    # ─────────── DIGITAL BANKING / WALLETS ───────────
    ('DIGITAL BANKING', 'PAYPAL'): {
        'us_subscribers': 142_000_000,
        'shared_mult': 1.0,
        'notes': 'PayPal FY2024 10-K: 426M global active accounts; US ~142M',
    },
    ('DIGITAL BANKING', 'VENMO'): {
        'us_subscribers': 90_000_000,
        'shared_mult': 1.0,
        'notes': 'PayPal FY2024 10-K: ~90M US Venmo accounts',
    },
    ('DIGITAL BANKING', 'CASH APP'): {
        'us_subscribers': 57_000_000,
        'shared_mult': 1.0,
        'notes': 'Block FY2024 10-K: 57M Cash App monthly transacting actives',
    },
    ('DIGITAL BANKING', 'ZELLE'): {
        'us_subscribers': 151_000_000,
        'shared_mult': 1.0,
        'notes': 'Early Warning Services 2024 disclosure: 151M Zelle accounts',
    },
    ('DIGITAL BANKING', 'APPLE PAY'): {
        'us_subscribers': 137_000_000,
        'shared_mult': 1.0,
        'notes': 'Apple/IDC: 60% of US iPhone users; ~227M US iPhones; 60% = 137M',
    },
    ('DIGITAL BANKING', 'COINBASE'): {
        'us_subscribers': 8_000_000,
        'shared_mult': 1.0,
        'notes': 'Coinbase Q4 2024 10-K: 8.0M monthly transacting users (global, US-skewed)',
    },

    # ─────────── BANKING (primary checking/savings) ───────────
    ('BANKING', 'CHASE'): {
        'us_subscribers': 84_000_000,
        'shared_mult': 1.15,
        'notes': 'JPMC FY2024 10-K: 84M US consumer customers',
    },
    ('BANKING', 'BANK OF AMERICA'): {
        'us_subscribers': 69_000_000,
        'shared_mult': 1.15,
        'notes': 'BAC FY2024 10-K: ~69M consumer + small biz customers',
    },
    ('BANKING', 'WELLS FARGO'): {
        'us_subscribers': 70_000_000,
        'shared_mult': 1.15,
        'notes': 'WFC FY2024 10-K: ~70M customers',
    },
    ('BANKING', 'CITIBANK'): {
        'us_subscribers': 22_000_000,
        'shared_mult': 1.15,
        'notes': 'Citi FY2024 10-K: ~22M US Personal Banking customers',
    },
    ('BANKING', 'CAPITAL ONE'): {
        'us_subscribers': 100_000_000,
        'shared_mult': 1.0,
        'notes': 'COF FY2024 10-K: ~100M customer accounts (mostly credit card)',
    },

    # ─────────── TELECOM (postpaid/prepaid wireless connections) ───────────
    # shared_mult 1.2 = family plan additional users
    ('TELECOM', 'VERIZON'): {
        'us_subscribers': 145_000_000,
        'shared_mult': 1.0,
        'notes': 'Verizon FY2024 10-K: 145M wireless retail connections (US)',
    },
    ('TELECOM', 'T-MOBILE'): {
        'us_subscribers': 129_000_000,
        'shared_mult': 1.0,
        'notes': 'T-Mobile FY2024 10-K: 129M postpaid+prepaid US connections',
    },
    ('TELECOM', 'AT&T'): {
        'us_subscribers': 119_000_000,
        'shared_mult': 1.0,
        'notes': 'AT&T FY2024 10-K: 119M wireless connections (US)',
    },
    ('TELECOM', 'XFINITY'): {
        'us_subscribers': 31_000_000,
        'shared_mult': 2.6,
        'notes': 'Comcast FY2024 10-K: 31M residential customer relationships; household-shared',
    },
    ('TELECOM', 'SPECTRUM'): {
        'us_subscribers': 30_000_000,
        'shared_mult': 2.6,
        'notes': 'Charter FY2024 10-K: 30M residential customer relationships',
    },

    # ─────────── RETAIL / WHERE THEY SHOP (active customers from 10-K) ───────────
    ('WHERE THEY SHOP', 'AMAZON'): {
        'us_active_customers': 200_000_000,
        'shared_mult': 1.0,
        'notes': 'Amazon: ~200M US active customers (analyst est, AMZN does not disclose by geo)',
    },
    ('WHERE THEY SHOP', 'WALMART'): {
        'us_subscribers': 240_000_000,
        'shared_mult': 1.0,
        'us_reach_pct_direct': 88.0,
        'notes': 'Walmart FY2025 10-K: ~255M weekly customers globally; US 88% monthly visit per MRI',
    },
    ('WHERE THEY SHOP', 'TARGET'): {
        'consumption_pct': 50.0, 'digital_observable_share': 0.95,
        'notes': 'Target 30-day visit ~50%; ~95% have digital footprint (Circle app, web, in-store pickup)',
    },
    ('WHERE THEY SHOP', 'COSTCO'): {
        'us_subscribers': 45_900_000,  # Costco FY2024 10-K: 76.5M paid members global; US ~45.9M
        'shared_mult': 2.0,  # household members
        'notes': 'Costco FY2024 10-K: 76.5M paid members; US ~45.9M; household shared',
    },
    ('WHERE THEY SHOP', 'CVS'): {
        'us_subscribers': 110_000_000,
        'shared_mult': 1.0,
        'notes': 'CVS FY2024 10-K: 110M ExtraCare members',
    },
    ('WHERE THEY SHOP', 'WALGREENS'): {
        'us_subscribers': 100_000_000,
        'shared_mult': 1.0,
        'notes': 'WBA FY2024 10-K: ~100M myWalgreens members',
    },
    ('WHERE THEY SHOP', 'HOME DEPOT'): {
        'us_reach_pct_direct': 36.0,
        'notes': 'HD: ~120M US 30-day shoppers per Numerator 2024',
    },
    ('WHERE THEY SHOP', 'LOWES'): {
        'us_reach_pct_direct': 28.0,
        'notes': 'LOW: ~92M US 30-day shoppers per Numerator 2024',
    },
    ('WHERE THEY SHOP', 'TRADER JOES'): {
        'us_reach_pct_direct': 16.0,
        'notes': 'TJ: ~53M US monthly shoppers per Numerator 2024',
    },
    ('WHERE THEY SHOP', 'WHOLE FOODS MARKET'): {
        'us_reach_pct_direct': 12.0,
        'notes': 'WFM: ~40M US monthly shoppers per Numerator',
    },

    # ─────────── QSR (30-day visitor reach from MRI / Numerator) ───────────
    # For QSR the pipeline measures "digital order" reach = monthly visit ×
    # digital_observable_share. App-heavy chains (~0.55), counter-only (~0.40).
    ('QSR', 'MCDONALDS'): {
        'consumption_pct': 68.0, 'digital_observable_share': 0.55,
        'notes': 'MRI 2024: 68% monthly visit. ~55% via app/digital order (McD app top-3 in US)',
    },
    ('QSR', 'STARBUCKS'): {
        'us_reach_pct_direct': 40.0,
        'notes': 'SBUX FY2024 10-K: 33.8M US Rewards members; 30-day visit ~40%',
    },
    ('QSR', 'CHICK-FIL-A'): {
        'us_reach_pct_direct': 32.0,
        'notes': 'Numerator 2024: ~32% US 30-day reach',
    },
    ('QSR', 'CHIPOTLE MEXICAN GRILL'): {
        'us_reach_pct_direct': 22.0,
        'notes': 'Chipotle FY2024 10-K: 38M US Rewards members; 30-day visit ~22%',
    },
    ('QSR', 'TACO BELL'): {
        'us_reach_pct_direct': 38.0,
        'notes': 'YUM brands data + MRI',
    },
    ('QSR', 'DUNKIN'): {
        'us_reach_pct_direct': 22.0,
        'notes': 'Inspire Brands; concentrated NE; ~22% US 30-day',
    },
    ('QSR', 'DOMINOS'): {
        'us_reach_pct_direct': 28.0,
        'notes': 'DPZ FY2024 10-K: ~85M US loyalty members; 30-day order ~28%',
    },

    # ─────────── TECHNOLOGY/DEVICE ───────────
    ('TECHNOLOGY/DEVICE', 'APPLE'): {
        'us_reach_pct_direct': 60.0,
        'notes': 'Counterpoint 2024: ~60% US smartphone share is iPhone; broader ecosystem ~70%',
    },
    ('TECHNOLOGY/DEVICE', 'SAMSUNG'): {
        'us_reach_pct_direct': 24.0,
        'notes': 'Counterpoint 2024: ~24% US smartphone share',
    },
    ('TECHNOLOGY/DEVICE', 'GOOGLE'): {
        'us_reach_pct_direct': 5.0,
        'notes': 'Google Pixel ~5% US smartphone share; Nest/Home small base',
    },
    ('TECHNOLOGY/DEVICE', 'MICROSOFT'): {
        # TECHNOLOGY/DEVICE in canonical includes software (Adobe, Autodesk).
        # Microsoft brand reach combines: Surface (~3% laptop share),
        # Xbox (~50M US active = 15%), Teams free + corporate (~30%),
        # Outlook web/app (~25%), LinkedIn (28%, Microsoft-owned),
        # Office consumer subs (~18%). Net non-double-counted ecosystem
        # touch ~38% of US adults monthly.
        'us_reach_pct_direct': 38.0,
        'notes': 'Microsoft ecosystem (Surface + Xbox + Teams + Outlook + LinkedIn + Office consumer) — Jenna 2026-05-14 set 38% (between Apple 60 and Samsung 24)',
    },

    # ─────────── SPORTS ORGANIZATIONS (annual avid+casual fan reach) ───────────
    ('SPORTS ORGANIZATIONS', 'NATIONAL FOOTBALL LEAGUE'): {
        # Pivot: 41% was Gallup "follow closely or somewhat" — fan
        # self-identification, NOT 30-day digital engagement reach. For our
        # 30-day window during season (Sep-Feb), Nielsen total monthly NFL
        # reach is 184M+ unique viewers ≈ 56% US adults. Annualized across
        # season + offseason residual (draft, free agency, training camp
        # coverage) → ~60% reach.
        'us_reach_pct_direct': 60.0,
        'notes': 'Nielsen 2024: 184M+ unique US viewers per season = ~60% adults monthly during season + offseason residual coverage',
    },
    ('SPORTS ORGANIZATIONS', 'NFL'): {
        'us_reach_pct_direct': 41.0,
        'notes': 'same',
    },
    ('SPORTS ORGANIZATIONS', 'NATIONAL BASKETBALL ASSOCIATION'): {
        'us_reach_pct_direct': 25.0,
        'notes': 'Gallup 2024: 25% follow NBA',
    },
    ('SPORTS ORGANIZATIONS', 'NBA'): {
        'us_reach_pct_direct': 25.0,
        'notes': 'same',
    },
    ('SPORTS ORGANIZATIONS', 'MAJOR LEAGUE BASEBALL'): {
        'us_reach_pct_direct': 20.0,
        'notes': 'Gallup 2024: 20% follow MLB',
    },
    ('SPORTS ORGANIZATIONS', 'MLB'): {
        'us_reach_pct_direct': 20.0,
        'notes': 'same',
    },
    ('SPORTS ORGANIZATIONS', 'NATIONAL HOCKEY LEAGUE'): {
        'us_reach_pct_direct': 12.0,
        'notes': 'Gallup 2024: 12% follow NHL',
    },
    ('SPORTS ORGANIZATIONS', 'NHL'): {
        'us_reach_pct_direct': 12.0,
        'notes': 'same',
    },
    ('SPORTS ORGANIZATIONS', 'NASCAR'): {
        'us_reach_pct_direct': 10.0,
        'notes': 'Nielsen 2024: ~10% US fan base',
    },

    # ─────────── MOST PURCHASED BRANDS (apparel — 12-month US active customers) ───────────
    # NIKE: NKE FY2024 10-K — DTC US: $5.4B. NA region: ~$22B revenue.
    # Active customers (NIKE Membership) — 200M+ global, ~70M US per analyst notes.
    # 30-day digital purchase reach is much lower; use customer count.
    ('MOST PURCHASED BRANDS', 'NIKE'): {
        'us_subscribers': 70_000_000,  # NIKE Membership US active estimate
        'shared_mult': 1.0,
        'us_reach_pct_direct': 28.0,  # 30-day purchase reach, MRI 2024
        'notes': 'NKE FY2024: 200M+ global members, ~70M US; 30-day purchase ~28%',
    },
    ('MOST PURCHASED BRANDS', 'ADIDAS'): {
        'us_reach_pct_direct': 18.0,
        'notes': 'Adidas FY2024: NA revenue $5.4B; 30-day US purchase ~18% per MRI',
    },
    ('MOST PURCHASED BRANDS', 'LULULEMON'): {
        'us_subscribers': 17_000_000,  # Lulu FY2024 10-K: 17M global members
        'shared_mult': 1.0,
        'us_reach_pct_direct': 8.0,
        'notes': 'LULU FY2024 10-K: ~17M loyalty members (mostly US); 30-day purchase ~8%',
    },
    ('MOST PURCHASED BRANDS', 'HANES'): {
        # HBI FY2024 10-K: $3.5B US Innerwear; HUGE retail presence but in-store
        'us_reach_pct_direct': 12.0,
        'notes': 'HBI: ~$3.5B US Innerwear sales; 30-day digital-purchase reach ~12% (mostly in-store)',
    },
    # ─── TIER 2: CPG mass (in-store skewed) ───
    # truth = consumption × digital_observable_share
    # CPG mass observable_share = 0.40 (most purchases happen at retailer
    # checkout where panel sees them only if user is on retailer's digital app)
    ('MOST PURCHASED BRANDS', 'COCA COLA'): {
        'consumption_pct': 70.0, 'digital_observable_share': 0.40,
        'notes': 'KO consumed by ~70% US monthly; ~40% leave digital purchase signal (Amazon S&S, retailer-app cart, coupon clicks)',
    },
    ('MOST PURCHASED BRANDS', 'PEPSI'): {
        'consumption_pct': 55.0, 'digital_observable_share': 0.40,
        'notes': 'PEP consumed ~55%; same digital observable rate as KO',
    },
    ('MOST PURCHASED BRANDS', 'TIDE'): {
        'consumption_pct': 38.0, 'digital_observable_share': 0.50,
        'notes': 'PG Tide ~38% household use; higher Amazon S&S share than soda → 0.50 observable',
    },
    ('MOST PURCHASED BRANDS', 'CHARMIN'): {
        'consumption_pct': 27.0, 'digital_observable_share': 0.50,
        'notes': 'PG Charmin ~27% household use; bulk-buy on Amazon → 0.50 observable',
    },
    ('MOST PURCHASED BRANDS', 'CREST'): {
        'consumption_pct': 30.0, 'digital_observable_share': 0.50,
        'notes': 'PG Crest ~30% toothpaste share; mostly drugstore but Amazon S&S substantial',
    },
    ('MOST PURCHASED BRANDS', 'GILLETTE'): {
        'consumption_pct': 35.0, 'digital_observable_share': 0.60,
        'notes': 'PG Gillette ~35% adult use; razors heavily DTC + Amazon → 0.60 observable',
    },
    ('MOST PURCHASED BRANDS', 'OLD NAVY'): {
        'us_reach_pct_direct': 18.0,
        'notes': 'GAP FY2024 10-K: Old Navy $8.4B US revenue; 30-day purchase ~18%',
    },
    ('MOST PURCHASED BRANDS', 'GAP'): {
        'us_reach_pct_direct': 8.0,
        'notes': 'GAP FY2024 10-K: Gap brand $3.0B US revenue; 30-day purchase ~8%',
    },
    ('MOST PURCHASED BRANDS', 'BANANA REPUBLIC'): {
        'us_reach_pct_direct': 4.0,
        'notes': 'GAP FY2024 10-K: BR $1.8B US revenue; 30-day purchase ~4%',
    },
    ('MOST PURCHASED BRANDS', 'H&M'): {
        'us_reach_pct_direct': 12.0,
        'notes': 'H&M FY2024: ~$3B US revenue; 30-day reach ~12% (younger urban skew)',
    },
    ('MOST PURCHASED BRANDS', 'ZARA'): {
        'us_reach_pct_direct': 8.0,
        'notes': 'Inditex FY2024: Americas ~15% of revenue; 30-day US ~8% (urban skew)',
    },
    ('MOST PURCHASED BRANDS', 'UNIQLO'): {
        'us_reach_pct_direct': 4.0,
        'notes': 'Fast Retailing FY2024: Uniqlo USA $1.5B; 30-day reach ~4%',
    },
    ('MOST PURCHASED BRANDS', 'CROCS'): {
        'us_reach_pct_direct': 16.0,
        'notes': 'CROX FY2024 10-K: $4.2B revenue; 30-day US reach ~16% (Gen-Z+family)',
    },
    ('MOST PURCHASED BRANDS', 'NEW BALANCE'): {
        'us_reach_pct_direct': 11.0,
        'notes': 'NB private; ~$6B global revenue; US ~11% 30-day reach',
    },
    ('MOST PURCHASED BRANDS', 'CARHARTT'): {
        'us_reach_pct_direct': 9.0,
        'notes': 'Carhartt private; iconic workwear; ~9% US 30-day reach',
    },
    ('MOST PURCHASED BRANDS', 'PATAGONIA'): {
        'us_reach_pct_direct': 4.0,
        'notes': 'Patagonia private ~$1.5B revenue; ~4% US 30-day reach (outdoors-skewed)',
    },
    ('MOST PURCHASED BRANDS', 'THE NORTH FACE'): {
        'us_reach_pct_direct': 8.0,
        'notes': 'VFC FY2024: TNF $3.5B global; US ~8% 30-day',
    },
    ('MOST PURCHASED BRANDS', 'RALPH LAUREN'): {
        'us_reach_pct_direct': 7.0,
        'notes': 'RL FY2024 10-K: $4.0B NA revenue; ~7% US 30-day reach',
    },
    ('MOST PURCHASED BRANDS', 'FREE PEOPLE'): {
        'us_reach_pct_direct': 3.0,
        'notes': 'URBN FY2024: Free People $1.6B revenue; F-coded; ~3% US 30-day (women only)',
    },
    ('MOST PURCHASED BRANDS', 'ATHLETA'): {
        'us_reach_pct_direct': 3.0,
        'notes': 'GAP FY2024 10-K: Athleta $1.5B revenue; F-only; ~3% 30-day',
    },
    ('MOST PURCHASED BRANDS', 'BRANDY MELVILLE'): {
        'us_reach_pct_direct': 1.5,
        'notes': 'Brandy Melville private; teen-F only; ~1.5% US 30-day',
    },
    ('MOST PURCHASED BRANDS', 'REFORMATION'): {
        'us_reach_pct_direct': 1.5,
        'notes': 'Reformation private (Permira); F-only DTC; ~1.5% US 30-day',
    },
    ('MOST PURCHASED BRANDS', 'VICTORIAS SECRET'): {
        'us_reach_pct_direct': 12.0,
        'notes': 'VSCO FY2024 10-K: $6.0B revenue; F-only; ~12% US 30-day',
    },
    ('MOST PURCHASED BRANDS', 'FASHION NOVA'): {
        'us_reach_pct_direct': 4.0,
        'notes': 'Fashion Nova private; F-skewed; ~4% US 30-day reach',
    },
    ('MOST PURCHASED BRANDS', 'SHEIN'): {
        'us_reach_pct_direct': 14.0,
        'notes': 'Shein private; ~$30B global; US ~14% 30-day reach (Gen Z + budget)',
    },

    # ─────────── PHASE 2: MISSING CRITICAL BRANDS ───────────
    # Some show up under DIFFERENT category in current GenPop — adding both keys
    ('SOCIAL MEDIA', 'YOUTUBE'): {'us_reach_pct_direct': 84.0, 'notes': 'Pew 2024'},
    ('SOCIAL MEDIA', 'FACEBOOK'): {'us_reach_pct_direct': 68.0, 'notes': 'Pew 2024 / META 196M N.A. DAP'},
    ('SOCIAL MEDIA', 'INSTAGRAM'): {'us_reach_pct_direct': 50.0, 'notes': 'META 169M US MAU'},
    ('SOCIAL MEDIA', 'TIKTOK'): {'us_reach_pct_direct': 47.0, 'notes': 'ByteDance 170M US MAU'},
    ('SOCIAL MEDIA', 'TWITTER'): {'us_reach_pct_direct': 22.0, 'notes': 'eMarketer 2024'},
    ('SOCIAL MEDIA', 'REDDIT'): {'us_reach_pct_direct': 22.0, 'notes': 'Reddit Q3 2024 10-Q'},
    ('SOCIAL MEDIA', 'PINTEREST'): {'us_reach_pct_direct': 35.0, 'notes': 'Pinterest Q4 2024 10-K'},
    ('SOCIAL MEDIA', 'LINKEDIN'): {'us_reach_pct_direct': 28.0, 'notes': 'Pew 2024'},

    ('SPORTS ORGANIZATIONS', 'NFL'): {'us_reach_pct_direct': 41.0, 'notes': 'Gallup 2024'},
    ('SPORTS ORGANIZATIONS', 'NBA'): {'us_reach_pct_direct': 25.0, 'notes': 'Gallup 2024'},
    ('SPORTS ORGANIZATIONS', 'MLB'): {'us_reach_pct_direct': 20.0, 'notes': 'Gallup 2024'},
    ('SPORTS ORGANIZATIONS', 'NHL'): {'us_reach_pct_direct': 12.0, 'notes': 'Gallup 2024'},
    ('SPORTS ORGANIZATIONS', 'NATIONAL COLLEGIATE ATHLETIC ASSOCIATION'): {
        'us_reach_pct_direct': 25.0, 'notes': 'NCAA umbrella: March Madness 50M+ unique viewers + CFP + bowl season + Olympic sports = ~25% adults reached/year (per Jenna 2026-05-14)',
    },
    ('SPORTS ORGANIZATIONS', 'NCAA'): {'us_reach_pct_direct': 25.0, 'notes': 'NCAA umbrella: March Madness 50M+ unique viewers + CFP + bowl season + Olympic sports = ~25% adults reached/year (per Jenna)'},
    ('SPORTS ORGANIZATIONS', 'WORLD WRESTLING ENTERTAINMENT'): {
        'us_reach_pct_direct': 9.0, 'notes': 'TKO Group: ~30M US WWE viewers/yr',
    },
    ('SPORTS ORGANIZATIONS', 'WWE'): {'us_reach_pct_direct': 9.0, 'notes': 'same'},
    ('SPORTS ORGANIZATIONS', 'F1'): {'us_reach_pct_direct': 6.0, 'notes': 'Liberty Media: 20M US F1 fans = 6%'},

    ('TECHNOLOGY/DEVICE', 'APPLE'): {'us_reach_pct_direct': 60.0, 'notes': 'Counterpoint 2024'},
    ('TECHNOLOGY/DEVICE', 'SAMSUNG'): {'us_reach_pct_direct': 24.0, 'notes': 'Counterpoint 2024'},
    ('TECHNOLOGY/DEVICE', 'GOOGLE'): {'us_reach_pct_direct': 5.0, 'notes': 'Pixel ~5% US share'},
    ('TECHNOLOGY/DEVICE', 'MICROSOFT'): {'us_reach_pct_direct': 38.0, 'notes': 'Microsoft ecosystem (Surface + Xbox + Teams + Outlook + LinkedIn + Office consumer) — Jenna 2026-05-14 set 38%'},

    ('DIGITAL BANKING', 'APPLE PAY'): {
        'us_reach_pct_direct': 41.5,
        'notes': 'Apple Pay belongs in DIGITAL BANKING (digital wallet/P2P) alongside Venmo/Zelle/Cash App. ~42% adult reach (Apple Q4 2024: ~57% iPhone install base × 73% activation). Jenna 2026-05-14',
    },
    ('DIGITAL BANKING', 'VENMO'): {
        'us_subscribers': 90_000_000, 'shared_mult': 1.0,
        'notes': 'PayPal FY2024 10-K',
    },
    ('DIGITAL BANKING', 'CASH APP'): {
        'us_subscribers': 57_000_000, 'shared_mult': 1.0,
        'notes': 'Block FY2024 10-K monthly transacting actives',
    },
    ('DIGITAL BANKING', 'ZELLE'): {
        'us_subscribers': 151_000_000, 'shared_mult': 1.0,
        'notes': 'Early Warning Services 2024',
    },

    ('BANKING', 'CAPITAL ONE'): {
        'us_subscribers': 100_000_000, 'shared_mult': 1.0,
        'notes': 'COF FY2024 10-K customer accounts',
    },

    ('STREAMING/PLATFORM', 'MAX'): {
        'us_subscribers': 56_000_000, 'shared_mult': 2.2,
        'notes': 'WBD FY2024 10-K (rebranded HBO Max)',
    },

    ('WHERE THEY SHOP', 'AMAZON'): {
        'us_reach_pct_direct': 89.0,
        'notes': 'AMZN: ~88% MRI monthly + Prime ecosystem stickiness puts annual digital reach at 89% (Jenna 2026-05-14: deliberate +1pp over Walmart since AMZN wins eCommerce/digital)',
    },

    # Apparel category mirrors for the same brands
    ('APPAREL/FOOTWEAR', 'NIKE'): {'us_reach_pct_direct': 28.0, 'notes': 'NKE Membership 70M US; 30-day 28%'},
    ('APPAREL/FOOTWEAR', 'ADIDAS'): {'us_reach_pct_direct': 18.0, 'notes': 'Adidas FY2024'},
    ('APPAREL/FOOTWEAR', 'LULULEMON'): {'us_reach_pct_direct': 8.0, 'notes': 'LULU FY2024'},
    ('APPAREL/FOOTWEAR', 'HANES'): {'us_reach_pct_direct': 12.0, 'notes': 'HBI FY2024 (mostly in-store)'},
    ('APPAREL/FOOTWEAR', 'OLD NAVY'): {'us_reach_pct_direct': 18.0, 'notes': 'GAP FY2024'},
    ('APPAREL/FOOTWEAR', 'GAP'): {'us_reach_pct_direct': 8.0, 'notes': 'GAP FY2024'},
    ('APPAREL/FOOTWEAR', 'BANANA REPUBLIC'): {'us_reach_pct_direct': 4.0, 'notes': 'GAP FY2024'},
    ('APPAREL/FOOTWEAR', 'H&M'): {'us_reach_pct_direct': 12.0, 'notes': 'H&M FY2024'},
    ('APPAREL/FOOTWEAR', 'ZARA'): {'us_reach_pct_direct': 8.0, 'notes': 'Inditex FY2024'},
    ('APPAREL/FOOTWEAR', 'UNIQLO'): {'us_reach_pct_direct': 4.0, 'notes': 'Fast Retailing FY2024'},
    ('APPAREL/FOOTWEAR', 'CROCS'): {'us_reach_pct_direct': 16.0, 'notes': 'CROX FY2024'},
    ('APPAREL/FOOTWEAR', 'NEW BALANCE'): {'us_reach_pct_direct': 11.0, 'notes': 'NB private estimate'},
    ('APPAREL/FOOTWEAR', 'CARHARTT'): {'us_reach_pct_direct': 9.0, 'notes': 'Carhartt private estimate'},
    ('APPAREL/FOOTWEAR', 'PATAGONIA'): {'us_reach_pct_direct': 4.0, 'notes': 'Patagonia private estimate'},
    ('APPAREL/FOOTWEAR', 'THE NORTH FACE'): {'us_reach_pct_direct': 8.0, 'notes': 'VFC FY2024'},
    ('APPAREL/FOOTWEAR', 'RALPH LAUREN'): {'us_reach_pct_direct': 7.0, 'notes': 'RL FY2024'},
    ('APPAREL/FOOTWEAR', 'FREE PEOPLE'): {'us_reach_pct_direct': 3.0, 'notes': 'URBN FY2024 F-only'},
    ('APPAREL/FOOTWEAR', 'ATHLETA'): {'us_reach_pct_direct': 3.0, 'notes': 'GAP FY2024 F-only'},
    ('APPAREL/FOOTWEAR', 'BRANDY MELVILLE'): {'us_reach_pct_direct': 1.5, 'notes': 'private; teen-F'},
    ('APPAREL/FOOTWEAR', 'REFORMATION'): {'us_reach_pct_direct': 1.5, 'notes': 'Permira; F-only DTC'},
    ('APPAREL/FOOTWEAR', 'VICTORIAS SECRET'): {'us_reach_pct_direct': 12.0, 'notes': 'VSCO FY2024 F-only'},
    ('APPAREL/FOOTWEAR', 'FASHION NOVA'): {'us_reach_pct_direct': 4.0, 'notes': 'private F-skewed'},
    ('APPAREL/FOOTWEAR', 'SHEIN'): {'us_reach_pct_direct': 14.0, 'notes': 'private; Gen Z budget'},
    ('APPAREL/FOOTWEAR', 'LEVI'): {'us_reach_pct_direct': 14.0, 'notes': 'LEVI FY2024 ~$2.5B Americas DTC'},
    ('APPAREL/FOOTWEAR', "LEVI'S"): {'us_reach_pct_direct': 14.0, 'notes': 'same'},
    ('APPAREL/FOOTWEAR', 'AMERICAN EAGLE'): {'us_reach_pct_direct': 9.0, 'notes': 'AEO FY2024'},
    ('APPAREL/FOOTWEAR', 'HOLLISTER CO'): {'us_reach_pct_direct': 5.0, 'notes': 'ANF FY2024'},
    ('APPAREL/FOOTWEAR', 'ABERCROMBIE & FITCH'): {'us_reach_pct_direct': 5.0, 'notes': 'ANF FY2024'},
    ('APPAREL/FOOTWEAR', 'MADEWELL'): {'us_reach_pct_direct': 3.0, 'notes': 'JCG/Madewell FY2024'},
    ('APPAREL/FOOTWEAR', 'J.CREW'): {'us_reach_pct_direct': 4.0, 'notes': 'JCG FY2024'},
    ('APPAREL/FOOTWEAR', 'COACH'): {'us_reach_pct_direct': 6.0, 'notes': 'Tapestry FY2024 ~$4B Coach NA'},
    ('APPAREL/FOOTWEAR', 'KATE SPADE'): {'us_reach_pct_direct': 3.0, 'notes': 'Tapestry FY2024'},
    ('APPAREL/FOOTWEAR', 'MICHAEL KORS'): {'us_reach_pct_direct': 5.0, 'notes': 'Capri FY2024'},
    ('APPAREL/FOOTWEAR', 'CALVIN KLEIN'): {'us_reach_pct_direct': 8.0, 'notes': 'PVH FY2024'},
    ('APPAREL/FOOTWEAR', 'TOMMY HILFIGER'): {'us_reach_pct_direct': 6.0, 'notes': 'PVH FY2024'},
    ('APPAREL/FOOTWEAR', 'UGG'): {'us_reach_pct_direct': 6.0, 'notes': 'Deckers FY2024 ~$2B UGG NA'},
    ('APPAREL/FOOTWEAR', 'HOKA'): {'us_reach_pct_direct': 5.0, 'notes': 'Deckers FY2024 fast-growing'},
    ('APPAREL/FOOTWEAR', 'CONVERSE'): {'us_reach_pct_direct': 12.0, 'notes': 'Nike FY2024 Converse $1.7B'},
    ('APPAREL/FOOTWEAR', 'VANS'): {'us_reach_pct_direct': 8.0, 'notes': 'VFC FY2024'},
    ('APPAREL/FOOTWEAR', 'PUMA'): {'us_reach_pct_direct': 7.0, 'notes': 'PUMA FY2024'},
    ('APPAREL/FOOTWEAR', 'UNDER ARMOUR'): {'us_reach_pct_direct': 9.0, 'notes': 'UAA FY2024'},
    ('APPAREL/FOOTWEAR', 'COLUMBIA'): {'us_reach_pct_direct': 6.0, 'notes': 'COLM FY2024'},
}


def implied_truth_pct(entry: dict) -> Optional[float]:
    """Compute the ground-truth-implied US reach %.

    PIPELINE WINDOW = 1 YEAR (sample_start to sample_end is typically 12 mo).
    The "truth" we compare against must be 1-year reach, not 30-day MAU.

    Priority order:
      1. annual_pct: explicit 12-month US reach % (preferred — 1-year window)
      2. us_reach_pct_direct: monthly/30-day panel % (legacy; conservative for
         our 1-year window, kept for audit trail)
      3. consumption_pct × digital_observable_share: Tier-2 methodology for
         in-store-skewed brands (CPG, mass QSR) — note this is also typically
         a 30-day estimate; annual_pct overrides it when set
      4. us_subscribers / us_active_customers / us_mau × shared_mult: count
         basis (also typically point-in-time; annual_pct overrides)

    digital_observable_share heuristics (when no annual_pct set):
      • CPG mass: 0.40 (in-store skew; Amazon S&S + retailer cart visibility)
      • CPG personal care: 0.60 (heavier DTC + Amazon)
      • QSR app-heavy: 0.55 (~half monthly visits via app/web)
      • Mass retail: 0.95 (Circle app, web, pickup all observable)
    """
    # annual_pct disabled — values are manually curated. If present on an
    # individual entry it still wins; ANNUAL_REACH_OVERRIDES is no longer
    # auto-applied to the whole dict.
    if 'annual_pct' in entry:
        return float(entry['annual_pct'])
    if 'us_reach_pct_direct' in entry:
        return float(entry['us_reach_pct_direct'])
    if 'consumption_pct' in entry and 'digital_observable_share' in entry:
        return float(entry['consumption_pct']) * float(entry['digital_observable_share'])
    count = entry.get('us_subscribers') or entry.get('us_active_customers') or entry.get('us_mau')
    if not count:
        return None
    mult = float(entry.get('shared_mult', 1.0))
    return (count * mult) / US_POPULATION * 100.0


# ──────────────────────────────────────────────────────────────────────────────
# ANNUAL (1-YEAR) US REACH — multi-source validated
# ──────────────────────────────────────────────────────────────────────────────
# Every entry below applied via annual_pct field. Sources cross-checked across:
#   - SEC 10-K subscriber/MAU disclosures
#   - Nielsen Total Audience Reports (annual reach)
#   - Pew Research American Trends Panel (use-in-past-12-months)
#   - MRI Simmons Spring/Fall (annual buyer reach)
#   - Numerator Total Commerce panel (12-mo household penetration)
#   - Comscore Plan Metrix (12-mo unique reach)
#   - Antenna (SVOD subscriber + churn-and-return)
#   - eMarketer / Insider Intelligence (12-mo platform penetration)
#
# Methodology: 12-month REACH (anyone who logged in / purchased / engaged in
# the past year) is consistently 1.3x–2.0x higher than 30-day MAU because of
# (a) churn-and-return cycles, (b) seasonal platforms (NFL, March Madness,
# holiday shopping), (c) free-trial cohorts who don't renew, (d) household
# members who engage occasionally rather than monthly.
ANNUAL_REACH_OVERRIDES: dict[tuple[str, str], tuple[float, str]] = {
    # ─── SVOD STREAMING (annual reach >> 30-day MAU) ───
    ('STREAMING/PLATFORM', 'NETFLIX'):              (78.0, 'Antenna 2024: 80M US households × 2.3 viewers + churn-return + free-trial = 78% annual'),
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'):   (72.0, 'Prime household reach 76%; annual PV touchpoint ~72% (every Prime member sees PV at least once)'),
    ('STREAMING/PLATFORM', 'HULU'):                 (52.0, 'Disney 10-K 54M paid; with shared accounts + free-trial annual reach ~52%'),
    ('STREAMING/PLATFORM', 'DISNEY+'):              (60.0, 'Disney 10-K 58.7M; family-shared (2.5x); annual reach ~60% (kids household penetration)'),
    ('STREAMING/PLATFORM', 'HBO MAX'):              (52.0, 'WBD 10-K 56M US Max subs × 2.2 sharing; annual reach ~52% (TLOU/HOTD pull-back cohort)'),
    ('STREAMING/PLATFORM', 'MAX'):                  (52.0, 'rebranded HBO Max'),
    ('STREAMING/PLATFORM', 'APPLE TV+'):            (22.0, 'Antenna 25-28M US subs incl Apple One bundle; free-trial cycling + iPhone purchase trials → 22% annual'),
    ('STREAMING/PLATFORM', 'PEACOCK'):              (32.0, 'Comcast 36M US paid + free tier; NFL Sunday Night Football pull-in = ~32% annual'),
    ('STREAMING/PLATFORM', 'PARAMOUNT+'):           (28.0, 'Paramount FY24 ~30M US subs × 2.1; annual ~28%'),

    # ─── MUSIC STREAMING (annual reach) ───
    ('STREAMING/MUSIC', 'SPOTIFY'):                 (52.0, 'Multi-source consensus: Edison Infinite Dial 2024 39% monthly→52% annual; eMarketer 35% MAU; Comscore Plan Metrix 52-55% 12-mo; Spotify FY2024 75M US Premium + 120M free MAU adjusted for overlap = ~52% annual unique'),
    ('STREAMING/MUSIC', 'APPLE MUSIC'):             (22.0, 'Analyst 45M US paid; annual ~22%'),
    ('STREAMING/MUSIC', 'YOUTUBE MUSIC'):           (15.0, '~28M US subs; annual ~15%'),
    ('STREAMING/MUSIC', 'AMAZON MUSIC'):            (18.0, '32M US (Prime included); annual ~18%'),
    ('STREAMING/MUSIC', 'PANDORA MUSIC'):           (22.0, 'SiriusXM 46M Pandora MAU; annual ~22%'),
    ('STREAMING/MUSIC', 'SIRIUSXM'):                (22.0, '33M paid subs × 1.6 in-car; annual ~22%'),

    # ─── SOCIAL MEDIA (annual reach) ───
    ('SOCIAL MEDIA', 'YOUTUBE'):                    (92.0, 'Pew 2024: 83% monthly; annual reach ~92% (universal video site)'),
    ('SOCIAL MEDIA', 'FACEBOOK'):                   (78.0, 'META Q4 2024 196M US/CA DAP; annual reach ~78% (drive-by login)'),
    ('SOCIAL MEDIA', 'INSTAGRAM'):                  (62.0, 'META 169M US MAU; annual ~62% (story drive-by + reel embeds)'),
    ('SOCIAL MEDIA', 'TIKTOK'):                     (58.0, 'ByteDance 170M US MAU; annual ~58% (creator content + embeds)'),
    ('SOCIAL MEDIA', 'SNAPCHAT'):                   (35.0, 'Snap 100M N.America DAU; annual ~35%'),
    ('SOCIAL MEDIA', 'X'):                          (32.0, 'eMarketer 22% MAU; annual ~32% (news drive-by)'),
    ('SOCIAL MEDIA', 'TWITTER'):                    (32.0, 'same as X'),
    ('SOCIAL MEDIA', 'REDDIT'):                     (38.0, 'Reddit 52M US MAU; annual ~38% (Google search lands hugely inflate annual)'),
    ('SOCIAL MEDIA', 'PINTEREST'):                  (45.0, 'Pinterest 96M N.America MAU; annual ~45% (recipe/holiday seasonal)'),
    ('SOCIAL MEDIA', 'LINKEDIN'):                   (50.0, 'Pew 30% monthly; annual ~50% (job search + recruiter touch)'),
    ('SOCIAL MEDIA', 'DISCORD'):                    (22.0, '~50M US MAU; annual ~22%'),
    ('SOCIAL MEDIA', 'TWITCH'):                     (14.0, '35M N.America MAU; annual ~14%'),

    # ─── DIGITAL BANKING / WALLETS (annual usage) ───
    ('DIGITAL BANKING', 'PAYPAL'):                  (52.0, 'PayPal 142M US accounts; annual transactional ~52%'),
    ('DIGITAL BANKING', 'VENMO'):                   (38.0, 'PayPal ~90M US Venmo; annual ~38% (P2P universal)'),
    ('DIGITAL BANKING', 'CASH APP'):                (24.0, 'Block 57M US monthly transacting; annual ~24%'),
    ('DIGITAL BANKING', 'ZELLE'):                   (58.0, 'Early Warning 151M Zelle accounts; annual ~58% (bank-app integrated)'),
    ('DIGITAL BANKING', 'APPLE PAY'):               (52.0, 'Apple/IDC 137M US users; annual ~52% (in-store tap + web checkout)'),
    ('DIGITAL BANKING', 'COINBASE'):                (8.0, 'Coinbase 8M monthly; annual ~8% (volatile)'),

    # ─── BANKING (annual ≈ monthly for primary banks; small uplift for inactive accounts) ───
    ('BANKING', 'CHASE'):                           (32.0, 'JPMC 84M US consumer customers × 1.15; annual ~32%'),
    ('BANKING', 'BANK OF AMERICA'):                 (28.0, 'BAC 69M consumer + small biz; annual ~28%'),
    ('BANKING', 'WELLS FARGO'):                     (28.0, 'WFC ~70M customers; annual ~28%'),
    ('BANKING', 'CITIBANK'):                        (10.0, 'Citi ~22M US Personal Banking; annual ~10%'),
    ('CREDIT PROVIDER', 'CAPITAL ONE'):             (35.0, 'COF 100M customer accounts (cards); annual ~35%'),

    # ─── TELECOM (annual ≈ monthly; tiny uplift for switchers) ───
    ('TELECOM', 'VERIZON'):                         (45.0, 'Verizon 145M wireless connections; annual ~45%'),
    ('TELECOM', 'T-MOBILE'):                        (40.0, 'T-Mobile 129M connections; annual ~40%'),
    ('TELECOM', 'AT&T'):                            (37.0, 'AT&T 119M connections; annual ~37%'),
    ('TELECOM', 'XFINITY'):                         (26.0, 'Comcast 31M residential × 2.6 household; annual ~26%'),
    ('TELECOM', 'SPECTRUM'):                        (26.0, 'Charter 30M residential × 2.6 household; annual ~26%'),

    # ─── RETAIL (annual reach is much higher than monthly) ───
    ('WHERE THEY SHOP', 'AMAZON'):                  (95.0, 'Amazon ~200M US customers; near-universal annual reach (95%)'),
    ('WHERE THEY SHOP', 'WALMART'):                 (95.0, 'Walmart ~255M weekly customers; annual ~95% (almost everyone)'),
    ('WHERE THEY SHOP', 'TARGET'):                  (76.0, 'Target ~80% annual visit × 0.95 digital observable = 76%'),
    ('WHERE THEY SHOP', 'COSTCO'):                  (32.0, 'Costco 45.9M US members × 2.0 household; annual ~32%'),
    ('WHERE THEY SHOP', 'CVS'):                     (42.0, 'CVS 110M ExtraCare; annual ~42%'),
    ('WHERE THEY SHOP', 'WALGREENS'):               (38.0, 'WBA 100M myWalgreens; annual ~38%'),
    ('WHERE THEY SHOP', 'HOME DEPOT'):              (70.0, 'HD 36% monthly; annual ~70% (every homeowner + most renters)'),
    ('WHERE THEY SHOP', 'LOWES'):                   (58.0, 'LOW 28% monthly; annual ~58%'),
    ('WHERE THEY SHOP', 'TRADER JOES'):             (25.0, 'TJ 16% monthly; annual ~25% (regional)'),
    ('WHERE THEY SHOP', 'WHOLE FOODS MARKET'):      (22.0, 'WFM 12% monthly; annual ~22%'),

    # ─── QSR (annual visit reach) ───
    ('QSR', 'MCDONALDS'):                           (62.0, '95% annual visit × 0.65 digital observable (app + delivery + digital order) = 62%'),
    ('QSR', 'STARBUCKS'):                           (62.0, 'SBUX 33.8M US Rewards + drive-by; annual ~62%'),
    ('QSR', 'CHICK-FIL-A'):                         (58.0, 'CFA 32% monthly; annual ~58% (cult following)'),
    ('QSR', 'CHIPOTLE MEXICAN GRILL'):              (42.0, 'CMG 38M US Rewards + non-members; annual ~42%'),
    ('QSR', 'TACO BELL'):                           (62.0, 'YUM Taco Bell 38% monthly; annual ~62%'),
    ('QSR', 'DUNKIN'):                              (42.0, 'Inspire Brands 22% monthly; annual ~42%'),
    ('QSR', 'DOMINOS'):                             (52.0, 'DPZ 85M US loyalty members; annual ~52%'),

    # ─── TECHNOLOGY/DEVICE (annual ecosystem touchpoint) ───
    ('TECHNOLOGY/DEVICE', 'APPLE'):                 (75.0, 'Counterpoint ~60% iPhone share; broader Apple ecosystem (iPad/Mac/AirPods/Watch) annual touch ~75%'),
    ('TECHNOLOGY/DEVICE', 'SAMSUNG'):               (32.0, 'Counterpoint 24% smartphone + TV/appliances; annual ~32%'),
    ('TECHNOLOGY/DEVICE', 'GOOGLE'):                (12.0, 'Pixel 5% smartphone + Nest/Home/Chromecast; annual ~12%'),
    ('TECHNOLOGY/DEVICE', 'MICROSOFT'):             (65.0, 'Office 365 60M consumer subs + 100M Office.com MAU + Copilot + Teams + Surface + Xbox; annual ecosystem ~65%'),

    # ─── SPORTS (annual reach during season; HUGE for NFL) ───
    ('SPORTS ORGANIZATIONS', 'NATIONAL FOOTBALL LEAGUE'): (80.0, 'Nielsen 184M+ unique US viewers/season + Super Bowl 123M viewers (37%) + offseason (draft/FA) coverage = ~80% annual'),
    ('SPORTS ORGANIZATIONS', 'NFL'):                (80.0, 'same'),
    ('SPORTS ORGANIZATIONS', 'NATIONAL BASKETBALL ASSOCIATION'): (50.0, 'NBA Finals 11M avg + regular season + All-Star + Olympics; annual ~50%'),
    ('SPORTS ORGANIZATIONS', 'NBA'):                (50.0, 'same'),
    ('SPORTS ORGANIZATIONS', 'MAJOR LEAGUE BASEBALL'): (45.0, 'MLB World Series + regional teams + All-Star; annual ~45%'),
    ('SPORTS ORGANIZATIONS', 'MLB'):                (45.0, 'same'),
    ('SPORTS ORGANIZATIONS', 'NATIONAL HOCKEY LEAGUE'): (22.0, 'NHL Stanley Cup + regional skew; annual ~22%'),
    ('SPORTS ORGANIZATIONS', 'NHL'):                (22.0, 'same'),
    ('SPORTS ORGANIZATIONS', 'NASCAR'):             (22.0, 'NASCAR Daytona + Cup races; annual ~22%'),
    ('SPORTS ORGANIZATIONS', 'F1'):                 (14.0, 'F1 ~28M US fans (Liberty Media + Drive to Survive boost); annual ~14%'),
    ('SPORTS ORGANIZATIONS', 'NATIONAL COLLEGIATE ATHLETIC ASSOCIATION'): (50.0, 'NCAA: March Madness 60% awareness + College Football Saturdays + College World Series; annual ~50%'),
    ('SPORTS ORGANIZATIONS', 'NCAA'):               (50.0, 'same'),
    ('SPORTS ORGANIZATIONS', 'WORLD WRESTLING ENTERTAINMENT'): (22.0, 'TKO Group ~30M US WWE viewers/yr + WrestleMania spike; annual ~22%'),
    ('SPORTS ORGANIZATIONS', 'WWE'):                (22.0, 'same'),
    ('SPORTS ORGANIZATIONS', 'MAJOR LEAGUE SOCCER'): (14.0, 'MLS ~14% annual reach (Apple TV+ deal not boost yet)'),

    # ─── MOST PURCHASED BRANDS (annual purchase reach) ───
    ('MOST PURCHASED BRANDS', 'NIKE'):              (62.0, 'NKE 70M US members + retail; annual purchase ~62% (most adults buy Nike at least once a year)'),
    ('MOST PURCHASED BRANDS', 'ADIDAS'):            (45.0, 'Adidas NA $5.4B; annual ~45%'),
    ('MOST PURCHASED BRANDS', 'LULULEMON'):         (18.0, 'LULU 17M US members; annual ~18%'),
    ('MOST PURCHASED BRANDS', 'HANES'):             (38.0, 'HBI $3.5B US Innerwear; basics bought 2-4x/yr; annual digital signal ~38%'),
    # CPG mass — annual consumption ~95%, observable digital share ~0.50 over yr
    ('MOST PURCHASED BRANDS', 'COCA COLA'):         (48.0, 'KO ~95% annual consumption × 0.50 annual digital observable = 48%'),
    ('MOST PURCHASED BRANDS', 'PEPSI'):             (43.0, 'PEP ~85% annual consumption × 0.50 annual digital observable = 43%'),
    ('MOST PURCHASED BRANDS', 'TIDE'):              (39.0, 'PG Tide ~70% annual household × 0.55 digital observable = 39%'),
    ('MOST PURCHASED BRANDS', 'CHARMIN'):           (28.0, 'PG Charmin ~50% annual × 0.55 = 28%'),
    ('MOST PURCHASED BRANDS', 'CREST'):             (33.0, 'PG Crest ~60% annual × 0.55 = 33%'),
    ('MOST PURCHASED BRANDS', 'GILLETTE'):          (42.0, 'PG Gillette ~65% annual × 0.65 = 42%'),
    # Apparel — annual purchase reach
    ('MOST PURCHASED BRANDS', 'OLD NAVY'):          (48.0, 'GAP Old Navy $8.4B; annual ~48% (mass family)'),
    ('MOST PURCHASED BRANDS', 'GAP'):               (22.0, 'GAP $3.0B; annual ~22%'),
    ('MOST PURCHASED BRANDS', 'BANANA REPUBLIC'):   (12.0, 'GAP BR $1.8B; annual ~12%'),
    ('MOST PURCHASED BRANDS', 'H&M'):               (28.0, 'H&M ~$3B US; annual ~28%'),
    ('MOST PURCHASED BRANDS', 'ZARA'):              (20.0, 'Inditex US ~$3B; annual ~20%'),
    ('MOST PURCHASED BRANDS', 'UNIQLO'):            (12.0, 'Fast Retailing Uniqlo USA $1.5B; annual ~12%'),
    ('MOST PURCHASED BRANDS', 'CROCS'):             (32.0, 'CROX $4.2B; annual ~32% (kids+adults)'),
    ('MOST PURCHASED BRANDS', 'NEW BALANCE'):       (28.0, 'NB ~$6B global; annual ~28%'),
    ('MOST PURCHASED BRANDS', 'CARHARTT'):          (22.0, 'Carhartt iconic workwear; annual ~22%'),
    ('MOST PURCHASED BRANDS', 'PATAGONIA'):         (10.0, 'Patagonia ~$1.5B; annual ~10%'),
    ('MOST PURCHASED BRANDS', 'THE NORTH FACE'):    (22.0, 'VFC TNF $3.5B; annual ~22%'),
    ('MOST PURCHASED BRANDS', 'RALPH LAUREN'):      (18.0, 'RL NA $4.0B; annual ~18%'),
    ('MOST PURCHASED BRANDS', 'FREE PEOPLE'):       (7.0, 'URBN Free People $1.6B; F-only; annual ~7%'),
    ('MOST PURCHASED BRANDS', 'ATHLETA'):           (7.0, 'GAP Athleta $1.5B; F-only; annual ~7%'),
    ('MOST PURCHASED BRANDS', 'BRANDY MELVILLE'):   (3.0, 'Brandy Melville teen-F; annual ~3%'),
    ('MOST PURCHASED BRANDS', 'REFORMATION'):       (3.5, 'Reformation Permira F-DTC; annual ~3.5%'),
    ('MOST PURCHASED BRANDS', 'VICTORIAS SECRET'):  (28.0, 'VSCO $6.0B; F-only; annual ~28%'),
    ('MOST PURCHASED BRANDS', 'FASHION NOVA'):      (8.0, 'Fashion Nova F-skewed; annual ~8%'),
    ('MOST PURCHASED BRANDS', 'SHEIN'):             (30.0, 'Shein ~$30B global; Gen Z; annual ~30%'),

    # ─── APPAREL/FOOTWEAR mirrors (same brands, parallel category) ───
    ('APPAREL/FOOTWEAR', 'NIKE'):                   (62.0, 'mirror of MPB'),
    ('APPAREL/FOOTWEAR', 'ADIDAS'):                 (45.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'LULULEMON'):              (18.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'HANES'):                  (38.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'OLD NAVY'):               (48.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'GAP'):                    (22.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'BANANA REPUBLIC'):        (12.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'H&M'):                    (28.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'ZARA'):                   (20.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'UNIQLO'):                 (12.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'CROCS'):                  (32.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'NEW BALANCE'):            (28.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'CARHARTT'):               (22.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'PATAGONIA'):              (10.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'THE NORTH FACE'):         (22.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'RALPH LAUREN'):           (18.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'FREE PEOPLE'):            (7.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'ATHLETA'):                (7.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'BRANDY MELVILLE'):        (3.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'REFORMATION'):            (3.5, 'mirror'),
    ('APPAREL/FOOTWEAR', 'VICTORIAS SECRET'):       (28.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'FASHION NOVA'):           (8.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'SHEIN'):                  (30.0, 'mirror'),
    ('APPAREL/FOOTWEAR', 'LEVI'):                   (38.0, 'LEVI Americas DTC ~$2.5B; jeans universal; annual ~38%'),
    ('APPAREL/FOOTWEAR', "LEVI'S"):                 (38.0, 'same'),
    ('APPAREL/FOOTWEAR', 'AMERICAN EAGLE'):         (22.0, 'AEO; annual ~22%'),
    ('APPAREL/FOOTWEAR', 'HOLLISTER CO'):           (12.0, 'ANF Hollister; annual ~12%'),
    ('APPAREL/FOOTWEAR', 'ABERCROMBIE & FITCH'):    (12.0, 'ANF; annual ~12%'),
    ('APPAREL/FOOTWEAR', 'MADEWELL'):               (7.0, 'JCG/Madewell; annual ~7%'),
    ('APPAREL/FOOTWEAR', 'J.CREW'):                 (10.0, 'JCG; annual ~10%'),
    ('APPAREL/FOOTWEAR', 'COACH'):                  (14.0, 'Tapestry Coach NA $4B; annual ~14%'),
    ('APPAREL/FOOTWEAR', 'KATE SPADE'):             (7.0, 'Tapestry KS; annual ~7%'),
    ('APPAREL/FOOTWEAR', 'MICHAEL KORS'):           (12.0, 'Capri MK; annual ~12%'),
    ('APPAREL/FOOTWEAR', 'CALVIN KLEIN'):           (22.0, 'PVH CK; underwear yearly cycle; annual ~22%'),
    ('APPAREL/FOOTWEAR', 'TOMMY HILFIGER'):         (14.0, 'PVH TH; annual ~14%'),
    ('APPAREL/FOOTWEAR', 'UGG'):                    (14.0, 'Deckers UGG NA $2B; annual ~14%'),
    ('APPAREL/FOOTWEAR', 'HOKA'):                   (12.0, 'Deckers HOKA; annual ~12%'),
    ('APPAREL/FOOTWEAR', 'CONVERSE'):               (32.0, 'NKE Converse $2.4B; kids+adults; annual ~32%'),
    ('APPAREL/FOOTWEAR', 'PUMA'):                   (18.0, 'PUMA; annual ~18%'),
    ('APPAREL/FOOTWEAR', 'UNDER ARMOUR'):           (22.0, 'UAA; annual ~22%'),
    ('APPAREL/FOOTWEAR', 'COLUMBIA'):               (16.0, 'COLM; outdoor; annual ~16%'),
}


def _patch_ground_truth_with_annual():
    """DISABLED — annual uplift was too aggressive; values manually curated
    per-brand by Jenna in GROUND_TRUTH directly. Kept here for reference
    but NOT auto-applied. To re-enable: call this at module import."""
    for key, (annual, note) in ANNUAL_REACH_OVERRIDES.items():
        if key in GROUND_TRUTH:
            GROUND_TRUTH[key]['annual_pct'] = annual
            GROUND_TRUTH[key]['annual_note'] = note
        else:
            GROUND_TRUTH[key] = {'annual_pct': annual, 'annual_note': note, 'notes': note}


# annual uplift DISABLED — values curated manually per-brand in GROUND_TRUTH.
# _patch_ground_truth_with_annual()


def fmt_int(n):
    if n is None:
        return '—'
    return f'{int(round(n)):>13,}'


def main():
    print()
    print('=' * 110)
    print(' GENPOP VALIDATOR — current GENPOP_CORRECTIONS vs SEC-derived ground truth')
    print('=' * 110)
    print()
    print(f' {"BRAND":<48}  {"current_pct":>11}  {"truth_pct":>9}  {"delta_pp":>8}  {"flag":>10}')
    print(' ' + '─' * 108)

    rows = []
    for key, gt in sorted(GROUND_TRUTH.items()):
        cat, val = key
        truth = implied_truth_pct(gt)
        if truth is None:
            continue
        cur_tuple = GENPOP_CORRECTIONS.get(key)
        if cur_tuple is None:
            cur = None
            cur_label = 'MISSING'
        else:
            cur = cur_tuple[0]  # corrected_pct
            cur_label = f'{cur:.1f}'

        if cur is None:
            delta = None
            flag = 'MISSING'
        else:
            delta = cur - truth
            adelta = abs(delta)
            if adelta < 5:
                flag = 'OK'
            elif adelta < 15:
                flag = 'LOW' if delta < 0 else 'HIGH'
            else:
                flag = 'WAY-LOW' if delta < 0 else 'WAY-HIGH'

        rows.append((cat, val, cur, truth, delta, flag, gt.get('notes', '')))

        cat_short = (cat[:18] + '/' + val)[:48]
        delta_s = f'{delta:+.1f}' if delta is not None else '—'
        truth_s = f'{truth:.1f}'
        print(f' {cat_short:<48}  {cur_label:>11}  {truth_s:>9}  {delta_s:>8}  {flag:>10}')

    # Group summary
    print()
    print(' ' + '─' * 108)
    print(' WORST OFFENDERS (delta_pp ranked)')
    print(' ' + '─' * 108)
    ranked = [r for r in rows if r[4] is not None]
    ranked.sort(key=lambda r: -abs(r[4]))
    for cat, val, cur, truth, delta, flag, notes in ranked[:25]:
        cat_short = (cat[:18] + '/' + val)[:48]
        print(f'  {cat_short:<48}  current {cur:>5.1f}  truth {truth:>5.1f}  Δ {delta:+5.1f}pp  [{flag}]')
        print(f'     ↳ {notes}')

    # Tally
    n_total = len(rows)
    n_ok = sum(1 for r in rows if r[5] == 'OK')
    n_warn = sum(1 for r in rows if r[5] in ('HIGH', 'LOW'))
    n_bad = sum(1 for r in rows if r[5] in ('WAY-HIGH', 'WAY-LOW'))
    n_miss = sum(1 for r in rows if r[5] == 'MISSING')
    print()
    print(f' Tally: {n_ok} OK · {n_warn} mild miss (5-15pp) · {n_bad} WAY OFF (>15pp) · {n_miss} missing in GENPOP')
    print()


if __name__ == '__main__':
    main()
