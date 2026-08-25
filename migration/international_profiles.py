#!/usr/bin/env python3
"""Shared country support for international profile builds.

Jenna 2026-08-25 (verbatim): "if someone wants something outside of the
us it will need to authentically build an international profile like we
did for omaze."

This module is the single source of truth the engine, the enforcers,
the queue worker, the gates, and the hostmap augment step all consult
when a build (or an already-built frame) is for a non-US country. It
carries:

  * country-name normalization (spec['country'] arrives as free text)
  * the canonical country-native demographic schemas (bucket labels +
    a census-shaped default distribution), lifted verbatim from the
    Omaze UK / Omaze Germany precedent (scripts/fix_omaze_uk_germany.py)
  * the canonical country market lists for LOCATION (city-level, sums
    to ~100), lifted from the shipped Omaze precedent files
  * content-signature country detection for frames that arrive WITHOUT
    a spec (post-generation enforcers, ship gates, cut paths)
  * prompt-context notes so the reasoning layer scores country-native
    brand availability instead of US panel reality

Design constraints (workspace rules):
  * detection is additive and conservative - a US frame must NEVER be
    detected as international (US enforcers must not weaken)
  * no pinning: the default distributions here are BASELINE SHAPES;
    every emitter applies subject-salted jitter + renormalize so no
    two profiles ship identical values
  * demo categories still sum to 100 with 4dp values downstream
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Country normalization
# ---------------------------------------------------------------------------

# Anything in this set (after uppercasing/stripping) means "domestic".
US_ALIASES = frozenset({
    "", "US", "USA", "U.S.", "U.S.A.", "UNITED STATES",
    "UNITED STATES OF AMERICA", "AMERICA", "DOMESTIC", "NATIONAL",
})

_COUNTRY_ALIASES = {
    "UK": "UK",
    "GB": "UK",
    "GBR": "UK",
    "UNITED KINGDOM": "UK",
    "GREAT BRITAIN": "UK",
    "BRITAIN": "UK",
    "ENGLAND": "UK",
    "SCOTLAND": "UK",
    "WALES": "UK",
    "NORTHERN IRELAND": "UK",
    "GERMANY": "GERMANY",
    "DE": "GERMANY",
    "DEU": "GERMANY",
    "DEUTSCHLAND": "GERMANY",
    "CANADA": "CANADA",
    "CA": "CANADA",
    "AUSTRALIA": "AUSTRALIA",
    "AU": "AUSTRALIA",
    "FRANCE": "FRANCE",
    "FR": "FRANCE",
    "MEXICO": "MEXICO",
    "MX": "MEXICO",
    "SPAIN": "SPAIN",
    "ES": "SPAIN",
    "ITALY": "ITALY",
    "IT": "ITALY",
    "JAPAN": "JAPAN",
    "JP": "JAPAN",
    "BRAZIL": "BRAZIL",
    "BR": "BRAZIL",
    "INDIA": "INDIA",
    "IN": "INDIA",
    "IRELAND": "IRELAND",
    "IE": "IRELAND",
    "NETHERLANDS": "NETHERLANDS",
    "NL": "NETHERLANDS",
}


def normalize_country(country) -> str:
    """Coerce a spec's free-text country to a canonical uppercase name.

    Returns 'US' for anything domestic (including missing/blank), the
    canonical alias target when known, or the uppercased input for
    countries we have no alias entry for (still treated as
    international by is_international)."""
    c = str(country or "").strip().upper()
    c = re.sub(r"\s+", " ", c)
    if c in US_ALIASES:
        return "US"
    return _COUNTRY_ALIASES.get(c, c)


def is_international(country) -> bool:
    """True when the (normalized) country is a real non-US market."""
    return normalize_country(country) != "US"


# ---------------------------------------------------------------------------
# Country-native demographic schemas (Omaze precedent, canonical)
#
# Each entry: category -> {bucket_label: default_share}. Bucket labels
# are the CANONICAL country-native labels (exact casing). The shares
# are census-shaped defaults from the Omaze precedent research; the
# engine keeps the spec's own distribution for any category whose
# bucket labels already match this schema, and falls back to these
# shapes (subject-salted jitter + renormalize) for categories whose
# spec buckets are US-native and therefore untranslatable.
# ---------------------------------------------------------------------------

_UK_DEMO_SCHEMA = {
    "AGE": {
        "18-24": 4.5, "25-34": 12.5, "35-44": 22.0, "45-54": 25.0,
        "55-64": 21.0, "65+": 15.0,
    },
    "GENDER": {
        "Female": 51.0, "Male": 47.0, "Non-Binary": 0.9,
        "Trans Female": 0.7, "Prefer Not to Say": 0.4,
    },
    "ETHNICITY": {
        "White British": 78.5, "White Other": 8.0, "Asian British": 6.0,
        "Black British": 4.0, "Mixed / Multiple Ethnic Groups": 2.5,
        "Other Ethnic Group": 1.0,
    },
    "EDUCATION": {
        "GCSE / O-Level": 22.0, "A-Level / BTEC": 30.0,
        "Bachelors Degree": 26.0, "Masters Degree": 11.0,
        "Doctorate / PhD": 2.5, "No Formal Qualification": 8.5,
    },
    "INCOME": {
        "£15,000 - £24,999": 10.0, "£25,000 - £34,999": 18.0,
        "£35,000 - £49,999": 24.0, "£50,000 - £74,999": 22.0,
        "£75,000 - £99,999": 13.0, "£100,000+": 8.5,
        "Prefer Not to Say": 4.5,
    },
    "OCCUPATION": {
        "Management, Business & Professional": 22.5,
        "Service & Hospitality": 13.0,
        "Healthcare Practitioners or Support": 12.0,
        "Sales & Retail": 11.5,
        "Transportation & Logistics": 6.5,
        "Science, Technology & Technical Professions": 8.5,
        "Skilled Trades/Construction or Maintenance": 8.0,
        "Education or Library Services": 6.5,
        "Other": 3.5,
        "Manufacturing & Production": 3.5,
        "Legal": 1.8,
        "Public Safety & Protective Services": 2.3,
        "Agriculture & Outdoor": 0.4,
    },
    "PARENTAL_STATUS": {
        "No Children": 42.0, "Has Children": 56.0, "Prefer Not to Say": 2.0,
    },
    "RELATIONSHIP": {
        "Married": 50.0, "Single": 23.0, "In a Relationship": 12.0,
        "Divorced or Separated": 11.0, "Widowed": 4.0,
    },
    "SEXUAL_ORIENTATION": {
        "Straight / Heterosexual": 89.0, "LGBTQ+": 9.0,
        "Prefer Not to Say": 2.0,
    },
}

_DE_DEMO_SCHEMA = {
    "AGE": {
        "18-24": 4.5, "25-34": 15.0, "35-44": 25.0, "45-54": 24.0,
        "55-64": 20.0, "65+": 11.5,
    },
    "GENDER": {
        "Female": 50.0, "Male": 48.0, "Non-Binary": 1.0,
        "Trans Female": 0.6, "Prefer Not to Say": 0.4,
    },
    "ETHNICITY": {
        "Deutsch (ohne Migrationshintergrund)": 68.0,
        "Deutsch (mit Migrationshintergrund)": 20.0,
        "Türkeistämmig": 4.5,
        "EU-Ausländer:in": 4.5,
        "Aus dem Nahen Osten / Nordafrika": 1.5,
        "Aus Osteuropa (Nicht-EU)": 1.0,
        "Andere": 0.5,
    },
    "EDUCATION": {
        "Hauptschulabschluss": 15.5,
        "Realschulabschluss (Mittlere Reife)": 32.0,
        "Abitur / Fachhochschulreife": 25.0,
        "Bachelor / Diplom (FH)": 14.5,
        "Master / Diplom (Uni)": 11.0,
        "Promotion / PhD": 2.0,
    },
    "INCOME": {
        "€15,000 - €24,999": 7.0, "€25,000 - €34,999": 15.5,
        "€35,000 - €49,999": 24.0, "€50,000 - €74,999": 25.5,
        "€75,000 - €99,999": 15.0, "€100,000+": 8.5,
        "Prefer Not to Say": 4.5,
    },
    "OCCUPATION": {
        "Management, Business & Professional": 20.0,
        "Service & Hospitality": 10.5,
        "Healthcare Practitioners or Support": 11.5,
        "Sales & Retail": 10.5,
        "Transportation & Logistics": 7.5,
        "Science, Technology & Technical Professions": 10.5,
        "Skilled Trades/Construction or Maintenance": 9.5,
        "Education or Library Services": 6.0,
        "Other": 3.0,
        "Manufacturing & Production": 6.5,
        "Legal": 1.5,
        "Public Safety & Protective Services": 2.5,
        "Agriculture & Outdoor": 0.5,
    },
    "PARENTAL_STATUS": {
        "No Children": 45.0, "Has Children": 53.0, "Prefer Not to Say": 2.0,
    },
    "RELATIONSHIP": {
        "Married": 46.0, "Single": 27.0, "In a Relationship": 14.0,
        "Divorced or Separated": 9.5, "Widowed": 3.5,
    },
    "SEXUAL_ORIENTATION": {
        "Straight / Heterosexual": 88.5, "LGBTQ+": 9.5,
        "Prefer Not to Say": 2.0,
    },
}

COUNTRY_DEMO_SCHEMAS = {
    "UK": _UK_DEMO_SCHEMA,
    "GERMANY": _DE_DEMO_SCHEMA,
}


def country_demo_schema(country) -> dict | None:
    """Canonical demo schema for the country, or None when we have no
    canonical bucket set (engine then instructs census-conventional
    buckets via reasoning and keeps whatever the spec supplied)."""
    return COUNTRY_DEMO_SCHEMAS.get(normalize_country(country))


# ---------------------------------------------------------------------------
# Country market lists for LOCATION (city-level, shares sum to ~100).
# Lifted from the shipped Omaze precedent files. These are BASELINE
# shapes; the engine applies subject-salted jitter + renormalize.
# ---------------------------------------------------------------------------

_UK_MARKETS = [
    ("London", 25.10), ("Birmingham", 4.39), ("Manchester", 4.19),
    ("Glasgow", 3.97), ("Liverpool", 3.35), ("Leeds", 3.14),
    ("Edinburgh", 2.93), ("Sheffield", 2.62), ("Bristol", 2.51),
    ("Cardiff", 2.40), ("Newcastle upon Tyne", 2.30), ("Belfast", 2.20),
    ("Nottingham", 2.09), ("Southampton", 1.99), ("Portsmouth", 1.78),
    ("Reading", 1.67), ("Brighton", 1.57), ("Bradford", 1.47),
    ("Leicester", 1.46), ("Plymouth", 1.46), ("Derby", 1.36),
    ("Coventry", 1.36), ("Milton Keynes", 1.26), ("Stoke-on-Trent", 1.26),
    ("Wolverhampton", 1.15), ("Kingston upon Hull", 1.15),
    ("Aberdeen", 1.04), ("Cambridge", 0.94), ("Swansea", 0.94),
    ("Norwich", 0.94), ("Oxford", 0.94), ("Bournemouth", 0.94),
    ("York", 0.84), ("Dundee", 0.74), ("Preston", 0.74),
    ("Blackpool", 0.74), ("Luton", 0.74), ("Ipswich", 0.63),
    ("Sunderland", 0.63), ("Exeter", 0.63), ("Middlesbrough", 0.62),
    ("Peterborough", 0.62), ("Bath", 0.53), ("Chester", 0.52),
    ("Slough", 0.52), ("Huddersfield", 0.52), ("Watford", 0.52),
    ("Swindon", 0.52), ("Maidstone", 0.42), ("St Albans", 0.42),
    ("Carlisle", 0.42), ("Chelmsford", 0.42), ("Gloucester", 0.42),
    ("Woking", 0.41), ("Lancaster", 0.32), ("Salisbury", 0.32),
    ("Canterbury", 0.31), ("Inverness", 0.31), ("Harrogate", 0.31),
]

_DE_MARKETS = [
    ("Berlin", 13.14), ("Hamburg", 7.20), ("München", 6.57),
    ("Köln", 4.34), ("Frankfurt am Main", 4.13), ("Stuttgart", 2.96),
    ("Düsseldorf", 2.86), ("Leipzig", 2.75), ("Dortmund", 2.65),
    ("Essen", 2.54), ("Dresden", 2.22), ("Bremen", 2.22),
    ("Hannover", 2.12), ("Nürnberg", 2.01), ("Duisburg", 1.69),
    ("Mannheim", 1.49), ("Bochum", 1.49), ("Wuppertal", 1.49),
    ("Bonn", 1.48), ("Bielefeld", 1.38), ("Münster", 1.28),
    ("Karlsruhe", 1.27), ("Augsburg", 1.17), ("Wiesbaden", 1.16),
    ("Mönchengladbach", 1.06), ("Gelsenkirchen", 1.06),
    ("Braunschweig", 1.06), ("Kiel", 0.95), ("Chemnitz", 0.95),
    ("Aachen", 0.95), ("Halle (Saale)", 0.85), ("Magdeburg", 0.85),
    ("Mainz", 0.84), ("Freiburg im Breisgau", 0.84), ("Erfurt", 0.74),
    ("Rostock", 0.74), ("Oberhausen", 0.74), ("Lübeck", 0.74),
    ("Krefeld", 0.74), ("Hagen", 0.64), ("Kassel", 0.64),
    ("Potsdam", 0.64), ("Oldenburg", 0.53), ("Saarbrücken", 0.53),
    ("Osnabrück", 0.53), ("Heidelberg", 0.53),
    ("Mülheim an der Ruhr", 0.53), ("Hamm", 0.53),
    ("Ludwigshafen am Rhein", 0.53), ("Leverkusen", 0.53),
    ("Darmstadt", 0.43), ("Solingen", 0.43), ("Herne", 0.43),
    ("Regensburg", 0.42), ("Neuss", 0.42), ("Paderborn", 0.42),
    ("Ingolstadt", 0.42), ("Fürth", 0.32), ("Offenbach am Main", 0.32),
    ("Göttingen", 0.32), ("Bremerhaven", 0.32), ("Wolfsburg", 0.32),
    ("Pforzheim", 0.32), ("Reutlingen", 0.32),
    ("Bergisch Gladbach", 0.32), ("Würzburg", 0.32), ("Heilbronn", 0.32),
    ("Bottrop", 0.32), ("Trier", 0.32), ("Recklinghausen", 0.32),
    ("Koblenz", 0.31), ("Jena", 0.31), ("Ulm", 0.31), ("Cottbus", 0.22),
    ("Hildesheim", 0.21), ("Siegen", 0.21), ("Moers", 0.21),
    ("Salzgitter", 0.21),
]

COUNTRY_MARKETS = {
    "UK": _UK_MARKETS,
    "GERMANY": _DE_MARKETS,
}


def country_markets(country) -> list | None:
    """Canonical [(market, baseline_share), ...] for LOCATION, or None
    when we have no canonical market list for the country."""
    return COUNTRY_MARKETS.get(normalize_country(country))


# ---------------------------------------------------------------------------
# Country reasoning context (currency + adult population + notes fed to
# the reasoning layer so brand availability is country-authentic).
# ---------------------------------------------------------------------------

COUNTRY_META = {
    "UK": {
        "currency": "£",
        "adult_population": 53_000_000,
        "notes": (
            "UK market. Country-native leaders: BBC iPlayer, ITV/ITVX, "
            "Channel 4, Sky, Tesco, Sainsbury's, Asda, Boots, Greggs, "
            "Nando's, Primark, JD Sports, Argos, Currys, Monzo, "
            "Barclays, Lloyds, EE, O2, Vodafone, Three. US-only "
            "services (Hulu, Peacock in its US form, Walmart, Target, "
            "CVS, Walgreens, most US regional banks/insurers/telcos, "
            "NFL/NBA regional products) have LOW or ZERO UK reach - "
            "attenuate them hard, do not import US panel ceilings."
        ),
    },
    "GERMANY": {
        "currency": "€",
        "adult_population": 69_000_000,
        "notes": (
            "German market. Country-native leaders: ARD/ZDF Mediathek, "
            "RTL+, Joyn, Sky Deutschland, Rewe, Edeka, Aldi, Lidl, dm, "
            "Rossmann, MediaMarkt, Saturn, Otto, Zalando, Deutsche "
            "Bank, Sparkasse, N26, Telekom, Vodafone DE, O2 DE. "
            "US-only services (Hulu, Peacock, Walmart, Target, CVS, US "
            "banks/insurers/telcos, US sports leagues beyond a niche "
            "following) have LOW or ZERO German reach - attenuate them "
            "hard. English-language US content carries an extra "
            "language discount versus the UK."
        ),
    },
}


def country_prompt_context(country) -> str:
    """One block of reasoning context for a non-US audience. Works for
    every country; extra depth when COUNTRY_META has an entry."""
    c = normalize_country(country)
    if c == "US":
        return ""
    meta = COUNTRY_META.get(c) or {}
    lines = [
        f"AUDIENCE COUNTRY: {c} (NOT the US).",
        f"Every panelist in this cohort is a {c} adult. The gen_pop_bp "
        f"baselines you are given are US reference values only - "
        f"re-anchor every item to its real {c} availability and reach "
        f"before applying persona fit.",
        f"Brands and services with no {c} presence score near zero no "
        f"matter how large their US baseline is. Country-native "
        f"leaders (grocers, broadcasters, banks, telcos, retailers) "
        f"take the reach US leaders would have had.",
    ]
    if meta.get("notes"):
        lines.append(meta["notes"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Content-signature country detection
#
# Enforcers and gates receive a frame, not a spec. Detection is
# CONSERVATIVE: a frame only reads as international on an unambiguous
# country-native signature (currency symbol in INCOME, country-native
# EDUCATION/ETHNICITY labels, or a LOCATION block dominated by a known
# country market list). A US frame can never trip these.
# ---------------------------------------------------------------------------

_UK_MARKET_NORMS = None
_DE_MARKET_NORMS = None


def _norm_label(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _market_norms(country):
    global _UK_MARKET_NORMS, _DE_MARKET_NORMS
    if country == "UK":
        if _UK_MARKET_NORMS is None:
            _UK_MARKET_NORMS = {_norm_label(m) for m, _ in _UK_MARKETS}
        return _UK_MARKET_NORMS
    if country == "GERMANY":
        if _DE_MARKET_NORMS is None:
            _DE_MARKET_NORMS = {_norm_label(m) for m, _ in _DE_MARKETS}
        return _DE_MARKET_NORMS
    return set()


def detect_country_from_pairs(pairs) -> str | None:
    """Detect the country of a profile from (category, value) pairs.

    Returns a canonical country name ('UK', 'GERMANY'), the generic
    marker 'INTERNATIONAL' for an unambiguous non-US signature we
    cannot place, or None for US / undetectable (treat None as US).
    """
    income_vals, edu_vals, eth_vals, loc_vals = [], [], [], []
    for cat, val in pairs:
        cu = str(cat or "").strip().upper()
        v = str(val or "")
        if not v:
            continue
        if cu == "INCOME":
            income_vals.append(v)
        elif cu == "EDUCATION":
            edu_vals.append(v)
        elif cu == "ETHNICITY":
            eth_vals.append(v)
        elif cu == "LOCATION":
            loc_vals.append(v)

    income_blob = " | ".join(income_vals)
    edu_blob = " | ".join(edu_vals).upper()
    eth_blob = " | ".join(eth_vals).upper()

    # Currency in INCOME buckets is the strongest signal.
    if "£" in income_blob:
        return "UK"
    has_euro = "€" in income_blob

    # Country-native education / ethnicity labels.
    uk_edu = any(t in edu_blob for t in ("GCSE", "A-LEVEL", "BTEC",
                                         "O-LEVEL"))
    uk_eth = any(t in eth_blob for t in ("WHITE BRITISH", "ASIAN BRITISH",
                                         "BLACK BRITISH"))
    if uk_edu or uk_eth:
        return "UK"
    de_edu = any(t in edu_blob for t in ("HAUPTSCHUL", "REALSCHUL",
                                         "ABITUR", "FACHHOCHSCHUL",
                                         "DIPLOM"))
    de_eth = ("MIGRATIONSHINTERGRUND" in eth_blob
              or "DEUTSCH (" in eth_blob
              or "TÜRKEISTÄMMIG" in eth_blob)
    if de_edu or de_eth:
        return "GERMANY"
    if has_euro:
        return "INTERNATIONAL"

    # LOCATION dominated by a known country market list (>= 40% of
    # rows AND >= 5 rows - a US frame with a city named London or
    # Berlin can never reach that share).
    if len(loc_vals) >= 5:
        loc_norms = [_norm_label(v) for v in loc_vals]
        for c in ("UK", "GERMANY"):
            norms = _market_norms(c)
            hits = sum(1 for n in loc_norms if n in norms)
            if hits >= 5 and hits / len(loc_norms) >= 0.4:
                return c
    return None


def detect_profile_country(df) -> str | None:
    """detect_country_from_pairs over a profile DataFrame with the
    standard Column / Value shape. None = US / undetectable."""
    if df is None or len(df) == 0:
        return None
    try:
        pairs = zip(df["Column"].astype(str), df["Value"].astype(str))
        return detect_country_from_pairs(list(pairs))
    except Exception:
        return None


def frame_is_international(df) -> bool:
    """True when the frame carries an unambiguous non-US signature."""
    return detect_profile_country(df) is not None
