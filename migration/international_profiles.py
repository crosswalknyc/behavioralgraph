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
  * GLOBAL (worldwide) audiences (Jenna 2026-11-23, Guinness - Global:
    "it would show new york, london, berlin, etc and all of the data
    would be indicative of a global audience"): country-neutral demo
    buckets, `City, Country` markets (the subject's own researched list
    rides spec['location_markets']), worldwide reasoning context

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
import unicodedata

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
    "GLOBAL": "GLOBAL",
    "WORLDWIDE": "GLOBAL",
    "WORLD WIDE": "GLOBAL",
    "WORLD": "GLOBAL",
    "ALL MARKETS": "GLOBAL",
    "GLOBAL FOOTPRINT": "GLOBAL",
    "AROUND THE WORLD": "GLOBAL",
    "GLOBALLY": "GLOBAL",
    "ALL COUNTRIES": "GLOBAL",
    "EVERYWHERE": "GLOBAL",
}
GLOBAL = "GLOBAL"


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


def is_global(country) -> bool:
    """True when the scope is the whole world ('<Subject> - Global')."""
    return normalize_country(country) == GLOBAL


_GLOBAL_NAME_SEGMENT_RE = re.compile(
    r"^(?:global|worldwide|world-?wide|international)"
    r"(?:\s+(?:audience|footprint|market|total universe|universe|profile))?$",
    re.I)


def name_is_global(name) -> bool:
    """True when a deliverable name is a worldwide build: a ' - Global'
    segment anywhere in its cut chain ('Guinness - Global', 'Guinness -
    Global - Avid Fan'), or a trailing bare 'Global' / 'Worldwide' token
    on the entity ('Guinness Global'). A proper name that opens on the
    word ('Global Citizen', 'International Fencing Federation') is not a
    scope. The interpret step, the worker and the matching rules read
    this one rule (Jenna 2026-11-23: labeled '<Subject> - Global')."""
    nm = re.sub(r"\s+", " ", str(name or "").strip())
    if not nm:
        return False
    parts = [p.strip() for p in nm.split(" - ")]
    if any(_GLOBAL_NAME_SEGMENT_RE.match(p) for p in parts[1:]):
        return True
    head = parts[0].split()
    return len(head) >= 2 and head[-1].lower() in ("global", "worldwide")


def global_display_name(name) -> str:
    """The deliverable name of a worldwide build: '<Subject> - Global' (the
    dash form, so it files under its subject the way a cut does; 'Omaze
    Germany' style names stay for single countries). A name that already
    carries the marker is returned as is."""
    nm = re.sub(r"\s+", " ", str(name or "").strip())
    if not nm or name_is_global(nm):
        return nm
    return f"{nm} - Global"


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

# Worldwide audience: no country census applies, so the buckets are
# country-neutral. ETHNICITY reads by heritage region and INCOME in US
# dollar equivalents (labelled US$ so it never reads as a US bracket).
_GLOBAL_DEMO_SCHEMA = {
    "AGE": {
        "18-24": 15.5, "25-34": 25.5, "35-44": 21.5, "45-54": 16.5,
        "55-64": 12.5, "65+": 8.5,
    },
    "GENDER": {
        "Female": 49.0, "Male": 49.5, "Non-Binary": 0.8,
        "Trans Female": 0.3, "Prefer Not to Say": 0.4,
    },
    "ETHNICITY": {
        "Asian Heritage": 46.5, "European Heritage": 22.5,
        "African Heritage": 13.5, "Latin American Heritage": 10.5,
        "Middle Eastern / North African Heritage": 5.0,
        "Mixed / Other": 2.0,
    },
    "EDUCATION": {
        "Secondary School or Less": 41.5,
        "Vocational or Technical Qualification": 17.5,
        "Bachelors Degree": 27.5, "Masters Degree": 9.5,
        "Doctorate / PhD": 1.5, "Prefer Not to Say": 2.5,
    },
    "INCOME": {
        "Under US$10,000": 34.5, "US$10,000 - US$24,999": 24.5,
        "US$25,000 - US$49,999": 17.5, "US$50,000 - US$74,999": 9.5,
        "US$75,000 - US$99,999": 5.5, "US$100,000 - US$149,999": 3.5,
        "US$150,000+": 2.0, "Prefer Not to Say": 3.0,
    },
    "OCCUPATION": {
        "Management, Business & Professional": 17.5,
        "Service & Hospitality": 14.5,
        "Healthcare Practitioners or Support": 7.5,
        "Sales & Retail": 13.5,
        "Transportation & Logistics": 7.5,
        "Science, Technology & Technical Professions": 8.5,
        "Skilled Trades/Construction or Maintenance": 9.5,
        "Education or Library Services": 6.5,
        "Other": 5.5,
        "Manufacturing & Production": 6.5,
        "Legal": 1.0,
        "Public Safety & Protective Services": 1.5,
        "Agriculture & Outdoor": 0.5,
    },
    "PARENTAL_STATUS": {
        "No Children": 41.5, "Has Children": 56.0, "Prefer Not to Say": 2.5,
    },
    "RELATIONSHIP": {
        "Married": 51.5, "Single": 29.0, "In a Relationship": 11.5,
        "Divorced or Separated": 5.5, "Widowed": 2.5,
    },
    "SEXUAL_ORIENTATION": {
        "Straight / Heterosexual": 90.5, "LGBTQ+": 6.5,
        "Prefer Not to Say": 3.0,
    },
}

COUNTRY_DEMO_SCHEMAS = {
    "UK": _UK_DEMO_SCHEMA,
    "GERMANY": _DE_DEMO_SCHEMA,
    "GLOBAL": _GLOBAL_DEMO_SCHEMA,
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

# Worldwide fallback (big online metros), used only when a GLOBAL spec
# carries no researched list of its own. Labels are `City, Country` so a
# world city never fuzzy-matches a US DMA of the same name.
_GLOBAL_MARKETS = [
    ("New York, USA", 3.31), ("Tokyo, Japan", 3.27), ("London, UK", 2.93),
    ("Delhi, India", 2.76), ("Sao Paulo, Brazil", 2.58),
    ("Mexico City, Mexico", 2.37), ("Mumbai, India", 2.29),
    ("Los Angeles, USA", 2.21), ("Jakarta, Indonesia", 2.17),
    ("Seoul, South Korea", 2.04), ("Manila, Philippines", 1.89),
    ("Istanbul, Turkey", 1.71), ("Shanghai, China", 1.62),
    ("Paris, France", 1.61), ("Lagos, Nigeria", 1.58),
    ("Cairo, Egypt", 1.57), ("Osaka, Japan", 1.49),
    ("Buenos Aires, Argentina", 1.43), ("Bangkok, Thailand", 1.39),
    ("Beijing, China", 1.38), ("Moscow, Russia", 1.36),
    ("Chicago, USA", 1.31), ("Rio de Janeiro, Brazil", 1.22),
    ("Bangalore, India", 1.18), ("Karachi, Pakistan", 1.17),
    ("Dhaka, Bangladesh", 1.09), ("Ho Chi Minh City, Vietnam", 1.08),
    ("Toronto, Canada", 1.03), ("Bogota, Colombia", 0.97),
    ("Lima, Peru", 0.91), ("Madrid, Spain", 0.89), ("Houston, USA", 0.88),
    ("Johannesburg, South Africa", 0.87), ("Dallas, USA", 0.86),
    ("Berlin, Germany", 0.82), ("Sydney, Australia", 0.81),
    ("San Francisco, USA", 0.79), ("Miami, USA", 0.77),
    ("Melbourne, Australia", 0.72), ("Riyadh, Saudi Arabia", 0.69),
    ("Atlanta, USA", 0.68), ("Kuala Lumpur, Malaysia", 0.67),
    ("Nairobi, Kenya", 0.63), ("Rome, Italy", 0.61),
    ("Santiago, Chile", 0.59), ("Singapore, Singapore", 0.58),
    ("Taipei, Taiwan", 0.57), ("Hong Kong, China", 0.56),
    ("Milan, Italy", 0.52), ("Dubai, UAE", 0.49),
    ("Casablanca, Morocco", 0.43), ("Warsaw, Poland", 0.42),
    ("Amsterdam, Netherlands", 0.41), ("Accra, Ghana", 0.38),
    ("Dublin, Ireland", 0.33),
]

COUNTRY_MARKETS = {
    "UK": _UK_MARKETS,
    "GERMANY": _DE_MARKETS,
    "GLOBAL": _GLOBAL_MARKETS,
}


def country_markets(country) -> list | None:
    """Canonical [(market, baseline_share), ...] for LOCATION, or None
    when we have no canonical market list for the country."""
    return COUNTRY_MARKETS.get(normalize_country(country))


def markets_for_spec(spec, country=None) -> list | None:
    """[(market, baseline_share), ...] for a build's LOCATION.

    A spec may carry the subject's own researched market list in
    spec['location_markets'] (pairs, or {'market'/'label', 'share'}
    dicts); with three or more usable entries it wins over the
    canonical country list, so a GLOBAL build shows the subject's
    footprint rather than a generic world list."""
    spec = spec if isinstance(spec, dict) else {}
    raw = spec.get("location_markets")
    items = list(raw.items()) if isinstance(raw, dict) else (
        list(raw) if isinstance(raw, (list, tuple)) else [])
    out, seen = [], set()
    for it in items:
        try:
            if isinstance(it, dict):
                label = it.get("market") or it.get("label")
                share = it.get("share")
            else:
                label, share = it[0], it[1]
            label = re.sub(r"\s+", " ", str(label or "")).strip()
            share = float(share)
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        key = label.upper()
        if not label or share <= 0 or key in seen:
            continue
        seen.add(key)
        out.append((label, share))
    if len(out) >= 3:
        return out
    return country_markets(spec.get("country") if country is None
                           else country)


_MARKET_LABELS = {"UK": "UNITED KINGDOM"}


def market_label(country) -> str:
    """The MARKET tag a non-US profile carries on its SAMPLE SIZE row
    (the dashboard reads it to show the LOCATION tab as a plain city
    list instead of US DMAs). '' for US."""
    c = normalize_country(country)
    if c == "US":
        return ""
    return _MARKET_LABELS.get(c, c)


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
    "GLOBAL": {
        "currency": "US$",
        "notes": (
            "Worldwide audience. Global platforms and brands (Google, "
            "YouTube, WhatsApp, Facebook, Instagram, TikTok, Netflix, "
            "Spotify, Amazon, Apple, Samsung, Coca-Cola, Heineken, Nike, "
            "adidas) and international publications (BBC, Reuters, CNN, "
            "The Guardian, Financial Times, The Economist, Daily Mail) "
            "keep their worldwide reach. Each market's own leaders "
            "(grocers, broadcasters, banks, telcos, retailers, local "
            "leagues) score at about that market's share of the audience "
            "times their home reach. US-only services (Hulu, Peacock, "
            "Walmart, Target, CVS, US banks, insurers and telcos, US "
            "sports leagues) score at about the US share of the audience "
            "times their US level, never their full US level."
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
    if c == "GLOBAL":
        lines = [
            "AUDIENCE: WORLDWIDE (GLOBAL), NOT the US alone.",
            "This cohort is adults in many countries; the US is one "
            "market inside it. The gen_pop_bp baselines you are given "
            "are US reference values only - re-anchor every item to its "
            "real reach across the markets this audience lives in "
            "before applying persona fit.",
            "A brand or service with no presence outside the US scores "
            "at roughly the US share of this audience times its US "
            "level. Brands and services that are big outside the US can "
            "score well above their US baseline.",
            meta.get("notes") or "",
        ]
        return "\n".join(x for x in lines if x)
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


def _fold(s) -> str:
    """_norm_label with accents folded first ('Türkiye' -> 'TURKIYE')."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return _norm_label("".join(ch for ch in s if not unicodedata.combining(ch)))


# Country names a worldwide LOCATION label may end with (`City, Country`).
_WORLD_COUNTRY_NAMES = frozenset(_fold(n) for n in (
    "USA", "US", "United States", "UK", "United Kingdom", "Great Britain",
    "England", "Scotland", "Wales", "Northern Ireland", "Ireland",
    "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru",
    "Venezuela", "Ecuador", "Uruguay", "Paraguay", "Bolivia", "Jamaica",
    "Trinidad and Tobago", "Trinidad", "Barbados", "Bahamas", "Guyana",
    "Belize", "Puerto Rico", "Dominican Republic", "Costa Rica", "Panama",
    "Guatemala", "Haiti", "Cayman Islands", "Bermuda", "Antigua", "St Lucia",
    "Grenada", "Germany", "France", "Spain", "Italy", "Netherlands",
    "Belgium", "Luxembourg", "Portugal", "Switzerland", "Austria",
    "Sweden", "Norway", "Denmark", "Finland", "Iceland", "Poland",
    "Czechia", "Czech Republic", "Slovakia", "Hungary", "Romania",
    "Bulgaria", "Greece", "Croatia", "Serbia", "Slovenia", "Estonia",
    "Latvia", "Lithuania", "Ukraine", "Russia", "Turkey", "Türkiye",
    "Cyprus", "Malta", "Nigeria", "Cameroon", "Ghana", "Kenya", "Uganda",
    "Tanzania", "Ethiopia", "South Africa", "Zambia", "Zimbabwe", "Malawi",
    "Rwanda", "Botswana", "Namibia", "Mozambique", "Angola", "Senegal",
    "Ivory Coast", "Côte d'Ivoire", "Morocco", "Egypt", "Tunisia",
    "Algeria", "Seychelles", "Mauritius", "Sierra Leone", "Liberia",
    "Gabon", "DR Congo", "Burundi", "South Sudan", "Australia",
    "New Zealand", "Japan", "China", "Hong Kong", "Taiwan", "South Korea",
    "Korea", "India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal",
    "Indonesia", "Malaysia", "Singapore", "Thailand", "Vietnam",
    "Philippines", "Cambodia", "Myanmar", "Fiji", "Papua New Guinea",
    "UAE", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait",
    "Bahrain", "Oman", "Israel", "Lebanon", "Jordan", "Iran", "Iraq",
))


def location_country(label) -> str:
    """The folded country of a `City, Country` LOCATION label, or ''.
    US DMA labels ('New York Ny') carry no comma and never match."""
    s = str(label or "")
    if "," not in s:
        return ""
    tail = _fold(s.rsplit(",", 1)[1])
    return tail if tail in _WORLD_COUNTRY_NAMES else ""


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

    Returns a canonical country name ('UK', 'GERMANY', 'GLOBAL' for a
    worldwide audience), the generic marker 'INTERNATIONAL' for an
    unambiguous non-US signature we cannot place, or None for US /
    undetectable (treat None as US).
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
    # Worldwide demo labels: US dollar equivalents carry 'US$' (a US
    # bracket reads '$25,000'), ethnicity reads by heritage region.
    if "US$" in income_blob.upper() or "HERITAGE" in eth_blob:
        return "GLOBAL"
    if has_euro:
        return "INTERNATIONAL"

    # LOCATION dominated by a known country market list (>= 40% of
    # rows AND >= 5 rows - a US frame with a city named London or
    # Berlin can never reach that share).
    if len(loc_vals) >= 5:
        # Worldwide: `City, Country` labels across three or more
        # countries.
        world = [location_country(v) for v in loc_vals]
        world_hits = [w for w in world if w]
        if (len(world_hits) >= 5
                and len(world_hits) / len(loc_vals) >= 0.4
                and len(set(world_hits)) >= 3):
            return "GLOBAL"
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
