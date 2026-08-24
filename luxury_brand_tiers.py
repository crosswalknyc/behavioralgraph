"""Luxury / aspirational brand tiers with confirmed-purchase panel-reality
target bands.

Jenna 2026-08-06:
    "check most purchased brands for north west. they feel too lux. they can
     be those brands. The percentages just need to be lower since this is an
     assumed confirmed purchase ... The mass brands would likely still be fine
     ... We just need to ensure this doesn't happen again"

MOST PURCHASED BRANDS is not "brands the audience recognizes / aspires to".
It's "brands the panelist has actually paid money for in the trailing 12
months." Panel-reality confirmed-purchase for a $2000 Fendi bag or a
$4000 Loro Piana sweater is single-digit percent even for the highest-
affinity audience because most panelists in the US can't afford it.

Aspirational awareness / interest / lust indexes belong in a separate
attribute layer (TALENT halo, MEDIA index, GEN Z FLEX index), not in
MOST PURCHASED BRANDS.

This module defines four tiers with per-tier confirmed-purchase caps.
Any MPB row whose brand normalizes into one of the four sets is capped
into the tier's target band (subject-salted jitter to avoid pinning).
Sub-cat occurrences (APPAREL/FOOTWEAR, ACCESSORIES, BEAUTY, JEWELRY,
ACTIVEWEAR, INTIMATES) get the same treatment via Rule 3b.

Sources reviewed while assembling the tiers: Bain Luxury Study (2024/25),
Statista global luxury reach, YouGov brand penetration cuts for
premium/luxury, MRI Simmons digital luxury cohort, Edited retail digital
tracking, and comps from the Rolex / Hermès waitlist economy where
actual purchase is fractional-percent even among ultra-wealthy panels.
"""
from __future__ import annotations

# Tier -> (BP_lower, BP_upper) target band (percent).
# Idempotency: if a brand's current MPB BP is already within the band,
# leave it alone. Only cap DOWN to the band (never lift up).
LUX_TARGET_BANDS: dict[str, tuple[float, float]] = {
    "ULTRA":   (0.8, 2.2),
    "HI":      (1.5, 3.8),
    "MID":     (3.0, 7.0),
    "LO":      (5.5, 12.0),
}


# Each entry is the CANONICAL brand name as it appears in hostmap
# (uppercase). Lookup is case + punctuation + apostrophe insensitive
# via _norm_brand below, so "Chanel" / "CHANEL" / "chanel" all match.

# ---------------------------------------------------------------------
# ULTRA: waitlist / by-appointment / $2,000+ single-item price ceilings.
# Confirmed panel purchase is fractional-percent.
# ---------------------------------------------------------------------
LUX_ULTRA: set[str] = {
    "HERMES", "HERMÈS",
    "JUDITH LEIBER",
    "LUISA BECCARIA",
    "LORO PIANA",
    "THE ROW",
    "RIMOWA",
    "MAYGEL CORONEL",
    "AUDEMARS PIGUET",
    "PATEK PHILIPPE",
    "PIAGET",
    "GRAFF",
    "HARRY WINSTON",
    "CHOPARD",
    "BOUCHERON",
    "CHAUMET",
    "MIKIMOTO",
    "BUCCELLATI",
    "MANOLO BLANIK", "MANOLO BLAHNIK",
    "ASPREY",
    "SMYTHSON OF BOND STREET", "SMYTHSON",
    "CHRISTOFLE",
    "GOYARD",
    "ROLEX",
    "PANERAI",
    "CARTIER",
    "VAN CLEEF & ARPELS", "VAN CLEEF",
    "BULGARI", "BVLGARI",
    "MOET & CHANDON", "MOËT & CHANDON",
    "KRUG",
    "DOM PERIGNON", "DOM PÉRIGNON",
}


# ---------------------------------------------------------------------
# HI: designer core; entry price mostly $500-1500, top-item $2000-6000.
# Confirmed panel purchase 1-4% even for a strongly aligned audience.
# ---------------------------------------------------------------------
LUX_HI: set[str] = {
    "CHANEL",
    "LOUIS VUITTON", "LV",
    "GUCCI",
    "PRADA",
    "DIOR", "CHRISTIAN DIOR",
    "FENDI",
    "CELINE", "CÉLINE",
    "LOEWE",
    "BALENCIAGA",
    "BOTTEGA VENETA", "BOTTEGA",
    "SAINT LAURENT", "YSL", "YVES SAINT LAURENT",
    "VALENTINO",
    "GIVENCHY",
    "VERSACE",
    "MULBERRY",
    "MONCLER",
    "KHAITE",
    "ZIMMERMANN",
    "ALTUZARRA",
    "MARINE SERRE",
    "MISSONI",
    "MAX MARA",
    "MARNI",
    "ALEXANDER MCQUEEN",
    "STELLA MCCARTNEY",
    "DOLCE & GABBANA", "DOLCE AND GABBANA",
    "CHRISTIAN LOUBOUTIN", "LOUBOUTIN",
    "OFF-WHITE", "OFF WHITE",
    "RHUDE",
    "COMME DES GARCONS", "COMME DES GARÇONS",
    "ROBERTO CAVALLI",
    "EMILIO PUCCI", "PUCCI",
    "SIMKHAI",
    "LISA MARIE FERNANDEZ",
    "GABRIELA HEARST",
    "SIMONE ROCHA",
    "ERDEM",
    "DAVID YURMAN",
    "DE BEERS",
    "TIFFANY & CO.", "TIFFANY",
    "CHLOE", "CHLOÉ",
    "CHROME HEARTS",
    "MOSCHINO",
    "TOM FORD",
    "COURREGES", "COURRÈGES",
    "HERVE LEGER", "HERVÉ LÉGER",
    "MAX MARA STUDIO",
    "BURBERRY",
    "AKRIS",
    "OSCAR DE LA RENTA",
    "CAROLINA HERRERA",
    "BRUNELLO CUCINELLI",
    "TOM DIXON",   # design/lighting but $1000+ entry
    "GENTLE MONSTER",
    "OLIVER PEOPLES",
    "PERSOL EYEWEAR", "PERSOL",
    "CREED FRAGRANCE", "CREED",
    "MAISON FRANCIS KURKDJIAN",
    "BOND NO. 9",
    "BYREDO",
    "DIPTYQUE",
    "LA MER",
    "TATA HARPER",
    "SISLEY-PARIS", "SISLEY",
    "AUGUSTINUS BADER",
    "DR. BARBARA STURM",
    "MCM",
    "FEAR OF GOD",
    "BAPE",
    "KENZO PARFUMS", "KENZO",
    "NINA RICCI",
    "MAISON LOUIS MARIE",
    "SIMKHAI",
    "SANDRO",
    "KARL LAGERFELD",
    "ISABEL MARANT",
    "JW ANDERSON", "J.W. ANDERSON",
    "JACQUEMUS",
    "KHITE", "KHAITE",
    "CULT GAIA",
    "ULLA JOHNSON",
    "LOVESHACKFANCY",
    "FOR LOVE & LEMONS",
    "STAUD",
    "REBECCA MINKOFF",
    "MICHI NY",
    "NAADAM",
    "R13",
    "KSUBI",
    "A.P.C.",
    "MAJE",
    "BA&SH",
    "STELLA MCCARTNEY",
    "DIANE VON FURSTENBERG",
    "VINCE",
    "THEORY",
    "TELFAR",
    "ALEXANDER WANG",
    "HELMUT LANG",
    "MARC JACOBS",
    "MARC BY MARC JACOBS",
    "GOLDEN GOOSE",
    "ACNE STUDIOS",
    "GANNI",
    "PALM ANGELS",
    "AMIRI",
    "BRIONI",
    "ETRO",
    "MISSONI HOME",
    "FENDI CASA",
    "ARMANI",
    "ARMANI BEAUTY",
    "ARMANI EXCHANGE",
    "GIORGIO ARMANI",
    "EMPORIO ARMANI",
    "LANVIN",
    "CHLOE ATELIER",
    "PACO RABANNE",
    "TORY BURCH",
    "JIMMY CHOO",
    "SERGIO ROSSI",
    "ROGER VIVIER",
    "BULY 1803",
    "LA PERLA",
    "ERES",
    "COBRA SNAKE",
    "PHILLIP LIM", "3.1 PHILLIP LIM",
    "MANSUR GAVRIEL",
    "STUART WEITZMAN",
    "AQUATALIA",
    "KARL LAGERFELD PARIS",
    "MICHAEL KORS COLLECTION",
    "COACH 1941",
    "TORY SPORT",
    "DIOR BEAUTY",
    "GUCCI BEAUTY",
    "LOEWE PERFUME",
    "MISSONI PROFUMI",
    "VALENTINO BEAUTY",
    "LK BENNETT",
    "M.M.LAFLEUR",
    "MOROSO",
    "SEZANE", "SÉZANE",
    "REJINA PYO",
    "PROENZA SCHOULER",
    "AJE",
    "FRAME",
    "CITIZENS OF HUMANITY",
    "AG JEANS",
    "MOTHER DENIM",
    "PAIGE",
    "AGOLDE",
    "REDONE",
    "R13 DENIM",
    "LA LIGNE",
    "LA DOUBLEJ",
    "MARYSIA",
    "SUZANNE KALAN",
    "JENNIFER FISHER JEWELRY",
    "ALEXIS BITTAR",
    "ELIZABETH LOCKE",
    "BAUBLEBAR",
    "LIZZIE FORTUNATO",
    "CLARE V.",
    "HOUSE OF HARLOW",
}


# ---------------------------------------------------------------------
# MID: contemporary premium. Entry price $200-500, top-item $500-1200.
# Confirmed panel purchase 3-7%.
# ---------------------------------------------------------------------
LUX_MID: set[str] = {
    "REFORMATION",
    "ZADIG & VOLTAIRE", "ZADIG AND VOLTAIRE",
    "NORMA KAMALI",
    "MANSUR GAVRIEL",
    "CINQ A SEPT", "CINQ À SEPT",
    "ALC", "A.L.C.",
    "STAUD",
    "MAJE",
    "BA&SH",
    "SANDRO",
    "IRO",
    "VINCE",
    "THEORY",
    "FRAME DENIM",
    "AG JEANS",
    "CITIZENS OF HUMANITY",
    "MOTHER",
    "PAIGE DENIM",
    "AGOLDE",
    "RAG & BONE",
    "COURREGES",
    "MARC JACOBS THE DAILY",
    "HELMUT LANG UNDERWEAR",
    "GENTLE MONSTER",
    "PERSOL",
    "MYKITA",
    "AKRIS PUNTO",
    "STUDIO NICHOLSON",
    "JOSEPH",
    "TIBI",
    "ULLA JOHNSON",
    "ALEXIS BITTAR",
    "MEJURI",
    "MISSOMA",
    "AURATE",
    "DAILY LUX",
    "BAUBLEBAR",
    "JENNIFER FISHER",
    "BEN BRIDGE",
    "PANDORA",
    "OMEGA",
    "TAG HEUER",
    "OLIVER BONAS",
    "COMPTOIR DES COTONNIERS",
    "MISS SELFRIDGE",
    "TED BAKER",
    "STUDIO 189",
    "REDONE",
    "OUTDOOR VOICES",
    "SET ACTIVE",
    "BEYOND YOGA",
    "SPIRITUAL GANGSTER",
    "SPLITS59",
    "VUORI",
    "P.E NATION",
    "ALO YOGA",
    "SWEATY BETTY",
    "GIRLFRIEND COLLECTIVE",
    "BANDIER",
    "LOUNGE UNDERWEAR",
    "PARADE UNDERWEAR",
    "SKIMS",
    "SPANX",
    "SAVAGE X FENTY",
    "GOOD AMERICAN",
    "SKKN BY KIM",
    "KHY",
    "KHY LABEL",
    "HAUS LABS",
    "BUBBLE SKINCARE",
    "STARFACE",
    "KOSAS",
    "MILK MAKEUP",
    "HAUS LABS BY LADY GAGA",
    "WESTMAN ATELIER",
    "SAIE",
    "ILIA BEAUTY",
    "TOWER 28",
    "GLOSSIER",
    "RARE BEAUTY",
    "MERIT",
    "MAKEUP BY MARIO",
    "KYLIE COSMETICS",
    "KKW BEAUTY",
    "FENTY BEAUTY",
    "BOBBI BROWN",
    "LAURA MERCIER",
    "CHARLOTTE TILBURY",
    "PAT MCGRATH LABS",
    "PAT MCGRATH",
    "PATRICK TA",
    "ROSE INC",
    "AUGUSTINUS BADER",
    "TATCHA",
    "SUNDAY RILEY",
    "DRUNK ELEPHANT",
    "PAULA'S CHOICE", "PAULAS CHOICE",
    "TATA HARPER",
    "KIEHL'S", "KIEHLS",
    "OLE HENRIKSEN",
    "IT COSMETICS",
    "URBAN DECAY",
    "TOO FACED",
    "BENEFIT COSMETICS", "BENEFIT",
    "SMASHBOX",
    "STILA COSMETICS", "STILA",
    "NARS",
    "HOURGLASS",
    "MAKE UP FOR EVER",
    "DIOR ADDICT",
    "LANCÔME", "LANCOME",
    "ESTEE LAUDER", "ESTÉE LAUDER",
    "SHISEIDO",
    "SK-II",
    "CLE DE PEAU BEAUTE", "CLÉ DE PEAU BEAUTÉ",
    "OMOROVICZA",
    "AVEDA",
    "OLAPLEX",
    "K18 HAIR",
    "IGK HAIR",
    "OUAI",
    "PATTERN",
    "R+CO",
    "SHANI DARDEN SKIN CARE",
    "DR. DENNIS GROSS", "DR DENNIS GROSS",
    "RHODE SKIN", "RHODE",
    "NIKESKIMS",
    "KHAITE",
    "SEZANE",
    "SUPERGA",
    "VEJA",
    "COMMON PROJECTS",
    "AXEL ARIGATO",
    "GIORGIO ARMANI SPORT",
    "TORY SPORT",
    "ZIMMERMANN SWIM",
    "MELISSA ODABASH",
    "ERES SWIM",
    "AMERICAN VINTAGE",
    "STAUD",
    "CULT GAIA",
    "CHLOÉ SEE BY",
    "SEE BY CHLOÉ", "SEE BY CHLOE",
    "MICHI NY",
    "MICHI",
    "NAKED WOLFE",
    "SOPHIA WEBSTER",
    "LOEFFLER RANDALL",
    "MANSUR GAVRIEL",
    "CULT GAIA",
    "BY FAR",
    "ATP ATELIER",
    "TROVE",
    "TROVE + CO",
    "AVIATOR NATION",
    "WILDFOX COUTURE", "WILDFOX",
    "FAVORITE DAUGHTER",
    "BLANKNYC",
    "SEIKO",
    "TIMEX",
    "SHINOLA",
    "MOVADO",
    "SWATCH",
    "FURLA",
    "LONGCHAMP",
    "SAM EDELMAN",
    "STEVE MADDEN",
    "MARC FISHER",
    "COLE HAAN",
    "VINCE CAMUTO",
    "NINE WEST",
    "KENNETH COLE",
    "CALVIN KLEIN",
    "TOMMY HILFIGER",
    "RALPH LAUREN",
    "MICHAEL KORS",
    "COACH",
    "COACH OUTLET",
    "KATE SPADE",
    "TORY BURCH",
    "TED BAKER",
    "KARL LAGERFELD PARIS",
    "REBECCA MINKOFF",
    "DKNY",
}


# ---------------------------------------------------------------------
# LO: accessible premium / gateway luxury. Entry price $80-200.
# Confirmed panel purchase 6-12%.
# ---------------------------------------------------------------------
LUX_LO: set[str] = {
    "EVERLANE",
    "MADEWELL",
    "J.CREW", "J CREW",
    "ANTHROPOLOGIE",
    "FREE PEOPLE",
    "ARITZIA",
    "BANANA REPUBLIC",
    "ABERCROMBIE & FITCH",
    "AMERICAN EAGLE",
    "AMERICAN EAGLE OUTFITTERS",
    "PACSUN",
    "URBAN OUTFITTERS",
    "COS",
    "AND OTHER STORIES",
    "MANGO",
    "UNIQLO",
    "HERSCHEL",
    "PATAGONIA",
    "COLUMBIA",
    "THE NORTH FACE",
    "ARC'TERYX", "ARCTERYX",
    "BIRKENSTOCK",
    "HUNTER BOOTS",
    "DR. MARTENS", "DR MARTENS",
    "UGG",
    "UGG 1974",
    "TIMBERLAND",
    "NEW BALANCE",
    "ON RUNNING",
    "HOKA",
    "ASICS",
    "SAUCONY",
    "NEW ERA",
    "HERSCHEL SUPPLY CO",
    "AWAY LUGGAGE",
    "MONOS",
    "TUMI",
    "SAMSONITE",
    "DAGNE DOVER",
    "LO & SONS",
    "PARAVEL",
    "ROLL RECS",
    "SKAGEN",
    "DANIEL WELLINGTON",
    "MVMT",
    "OLIVIA BURTON",
    "FOSSIL",
    "SWATCH",
    "SUNGLASS HUT",
    "OAKLEY",
    "RAY-BAN", "RAY BAN",
    "QUAY SUNGLASSES", "QUAY",
    "WARBY PARKER",
    "ZENNI OPTICAL",
    "EYEBUYDIRECT",
    "COACH OUTLET",
    "KATE SPADE OUTLET",
    "TORY BURCH OUTLET",
    "MICHAEL KORS OUTLET",
    "MADEWELL DENIM",
    "J. CREW OUTLET",
    "LULULEMON",
    "ATHLETA",
    "GAP",
    "GAP KIDS",
    "OLD NAVY",
    "OLD NAVY MATERNITY",
    "BANANA REPUBLIC FACTORY",
    "BOOHOO",
    "PRETTYLITTLETHING",
    "MISSGUIDED",
    "SHEIN",
    "FASHIONNOVA", "FASHION NOVA",
    "REVOLVE",
    "SHOPBOP",
    "NET-A-PORTER",   # actually retailer, but panel signal similar
    "MYTHERESA",
    "BROWNS FASHION",
    "SSENSE",
    "FARFETCH",
    "MATCHES FASHION",
}


# Fast lookup: normalized brand -> tier
_TIER_INDEX: dict[str, str] = {}
for _tier, _brands in [
    ("ULTRA", LUX_ULTRA),
    ("HI",    LUX_HI),
    ("MID",   LUX_MID),
    ("LO",    LUX_LO),
]:
    for _b in _brands:
        _TIER_INDEX[_b.upper().strip()] = _tier


def _norm_brand(name: str) -> str:
    """Normalize a brand name for tier lookup: uppercase, strip
    apostrophes/quotes, collapse whitespace, strip common suffixes."""
    if name is None:
        return ""
    s = str(name).strip().upper()
    s = s.replace("\u2019", "").replace("\u2018", "")
    s = s.replace("'", "").replace("\"", "")
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    import re
    s = re.sub(r"\s+", " ", s)
    return s


def lookup_tier(brand: str) -> str | None:
    """Return 'ULTRA' / 'HI' / 'MID' / 'LO' if the brand is in the lux
    canon, else None."""
    n = _norm_brand(brand)
    if not n:
        return None
    if n in _TIER_INDEX:
        return _TIER_INDEX[n]
    # secondary: strip trailing 'INC', 'CO', 'BEAUTY', 'PARFUMS', etc.
    stripped = n
    for suffix in (" BEAUTY", " PARFUMS", " PARFUM", " SPORT", " HOME",
                   " OUTLET", " COLLECTION", " CASA"):
        if stripped.endswith(suffix):
            core = stripped[: -len(suffix)].strip()
            if core in _TIER_INDEX:
                return _TIER_INDEX[core]
    return None


def target_band(tier: str) -> tuple[float, float]:
    """Return the (lower, upper) BP band for a tier."""
    return LUX_TARGET_BANDS[tier]
