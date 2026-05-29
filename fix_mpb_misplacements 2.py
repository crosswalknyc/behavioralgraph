#!/usr/bin/env python3
"""
Fix misplaced brands in MOST PURCHASED BRANDS:
1. Name mismatches (COCA-COLA, MR. CLEAN, BURTS BEES, M & MS, etc.)
2. Major brands that were missing from the known list
3. Niche brands that the redistribution placed too high
4. Propagate to all sub-categories for consistency
"""

import pandas as pd
import hashlib

CSV_PATH = '/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv'
SAMPLE_SIZE = 10_000_000
US_POP = 329_900_000

SKIP_CATEGORIES = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
    'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
    'OCCUPATION', 'LOCATION', 'BRAND CATEGORY', 'GEN POP',
    'MOST PURCHASED CATEGORIES',
}

PRODUCT_SUBCATS = {
    'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR',
    'CPG', 'ACCESSORIES', 'TECHNOLOGY BRAND',
}

# Corrections: VALUE_AS_IN_CSV → corrected_pct
FIXES: dict[str, float] = {
    # ── Name mismatches (major brands that didn't match known list) ────
    'COCA-COLA': 20.0,
    'M & MS': 10.0,
    'MR. CLEAN': 5.0,
    'BURTS BEES': 5.0,
    'MOUNTIAN DEW': 8.0,     # misspelled Mountain Dew
    'PINE-SOL': 5.0,
    'CHEEZIT': 8.0,          # was CHEEZ-IT in known list
    'KITKAT': 8.0,           # was KIT KAT
    'KELLOGGS FROSTED FLAKES': 8.0,
    'KELLOGGS POP TARTS': 5.0,
    'KELLOGGS CORN FLAKES': 5.0,
    'KELLOGGS FROOT LOOPS': 3.0,
    'CHIPS AHOY!': 5.0,
    'I CANT BELIEVE ITS NOT BUTTER': 3.0,

    # ── Major US brands that were missing from known list ─────────────
    'COLGATE': 15.0,
    'LISTERINE': 10.0,
    'KLEENEX': 12.0,
    'PURINA': 8.0,
    'SWIFFER': 5.0,
    'PAMPERS': 8.0,
    'REEBOK': 5.0,
    'BIRKENSTOCK': 4.0,
    'TIMBERLAND': 4.0,
    'DR. MARTENS': 2.0,
    'FILA': 3.0,
    'DKNY': 2.0,
    'ESTEE LAUDER': 5.0,
    'YANKEE CANDLE': 5.0,
    'FENTY BEAUTY': 3.0,
    'BEN & JERRYS': 5.0,
    'DURACELL': 10.0,
    'ENERGIZER': 8.0,
    'JOCKEY': 5.0,
    'NEOSPORIN': 8.0,
    'SALLY HANSEN': 5.0,
    'CLINIQUE': 5.0,
    'NIVEA': 8.0,
    'ANN TAYLOR': 4.0,
    'CUISINART': 3.0,
    'YETI': 5.0,
    'HOSTESS': 5.0,
    'JELL-O': 5.0,
    'OSCAR MAYER': 5.0,
    'CHEETOS': 8.0,
    'HEFTY': 5.0,
    'TOTINOS': 3.0,
    'JANSPORT': 3.0,
    'MONSTER ENERGY': 5.0,
    'LIQUID DEATH': 2.0,
    'STANLEY': 5.0,
    'HYDRO FLASK': 3.0,
    'LEE': 5.0,
    'FRITOS': 5.0,
    'AQUAFINA': 3.0,
    'DASANI': 5.0,
    'DOLLAR SHAVE CLUB': 3.0,
    'OLIPOP': 2.0,
    'HAMILTON BEACH': 3.0,
    'MICHELOB ULTRA': 5.0,
    'OATLY': 2.0,
    'MANGO': 2.0,
    'CLARKS': 3.0,
    'WARBY PARKER': 2.0,
    'DICKIES': 3.0,
    'LACOSTE': 2.0,
    'FOREVER 21': 3.0,
    'SAMSONITE': 3.0,
    'TIFFANY & CO.': 1.0,
    'REDKEN': 2.0,
    'BOBS RED MILL': 2.0,
    'KEDS': 2.0,
    'POPPI PREBIOTIC SODA': 2.0,
    'MILWAUKEE TOOLS': 3.0,
    'CALPHALON': 2.0,
    'BISQUICK': 3.0,
    'DUNCAN HINES': 3.0,
    'VITAMINWATER': 2.0,
    'TROLLI CANDY': 2.0,
    'RYOBI': 3.0,
    'DEWALT': 3.0,
    'LODGE CAST IRON': 2.0,
    'BREVILLE': 2.0,
    'GERBER BABY FOOD': 5.0,
    'BANANA BOAT': 3.0,
    'CHICKEN OF THE SEA': 3.0,
    'STARKIST': 3.0,
    'SMUCKERS': 5.0,
    'HIDDEN VALLEY': 3.0,
    'PLANTERS': 5.0,
    'BREYERS': 5.0,
    'RUFFLES': 5.0,
    'LINDT': 3.0,
    'EMERGEN-C': 3.0,
    'TROPICANA': 5.0,
    'SARGENTO FOODS': 3.0,
    'GENERAL MILLS': 10.0,
    'HERBAL ESSENCES': 3.0,
    'JOHN FRIEDA': 2.0,
    'CLAIRES': 2.0,
    'CRAYOLA': 5.0,
    'COPPERSTONE': 3.0,
    'BANANA BOAT': 3.0,
    'HAWAIIAN TROPIC': 2.0,
    'SHERWIN-WILLIAMS': 3.0,
    'BIC': 5.0,
    'WEBER': 3.0,
    'TUPPERWARE': 5.0,
    'SPEEDO': 2.0,
    'OAKLEY': 3.0,
    'NAUTICA': 2.0,
    'SAUCONY': 2.0,
    'SPERRY': 2.0,
    'KENNETH COLE': 2.0,
    'HUDA BEAUTY': 2.0,
    'KYLIE COSMETICS': 2.0,
    'SMASHBOX': 1.5,
    'THE ORDINARY': 2.0,
    'DRUNK ELEPHANT': 2.0,
    'CHARLOTTETILBURY': 2.0,
    'OLAPLEX': 2.0,
    'BOBBI BROWN': 2.0,
    'NARS COSMETICS': 2.0,
    'BEATS BY DRE': 3.0,
    'TEMPUR-PEDIC': 2.0,
    'PURPLE MATTRESS': 1.0,
    'LA CROIX SPARKLING WATER': 3.0,
    'OPI': 3.0,
    'JELLY BELLY': 3.0,
    'LUCKY BRAND': 2.0,
    'RUGGABLE': 1.0,
    'VERA BRADLEY': 2.0,
    'DOONEY & BOURKE': 1.5,
    'ASHLEY FURNITURE': 3.0,
    'SLEEP NUMBER': 1.5,
    'VIDAL SASSOON': 2.0,
    'AVEDA': 2.0,
    'MOROCCANOIL': 1.5,
    'BRAUN': 2.0,
    'WAHL': 1.5,
    'IKEA': 12.0,  # if present
    'COACH OUTLET': 3.0,
    'STUMPTOWN COFFEE': 0.5,
    'LA MER': 0.5,
    'BILLABONG': 1.0,
    'SKIMS': 2.0,
    'TORRID': 2.0,
    'PRIME DRINK': 2.0,
    'CALLAWAY': 1.0,
    'TITLEIST': 0.5,
    'AEROPOSTALE': 2.0,
    'FENDI': 0.3,
    'DAVID YURMAN': 0.5,
    'TARTE COSMETICS': 2.0,
    'REBECCA MINKOFF': 0.5,
    'LA-Z-BOY': 3.0,
    'BEAUTYREST': 2.0,
    'ETHAN ALLEN': 1.0,
    'CANADA GOOSE': 0.5,
    'CB2': 1.0,
    'BENJAMINMOORE': 2.0,
    'BENJAMIN MOORE': 2.0,
    'GYMSHARK': 1.5,
    'VUORI': 1.0,
    'ON RUNNING': 2.0,
    'LOEWE': 0.2,
    'DR. TEALS': 3.0,
    'SMYTHSON OF BOND STREET': 0.1,
    'STONEY CLOVER LANE': 0.3,
    'OMAHA STEAKS': 1.0,

    # ── Niche brands placed too high by redistribution ────────────────
    'HAPPY HAIR PEOPLE': 0.1,
    'EFFYDESK': 0.1,
    'PLANK+BEAM': 0.1,
    'ROCKET DOG': 0.3,
    'RIEDEL': 0.3,
    'MELTDOWN': 0.1,
    'KAREN MILLEN': 0.2,
    'BOLON EYEWEAR': 0.1,
    'MELIN': 0.1,
    'BODY GLOVE': 0.3,
    'OLIVER PEOPLES': 0.3,
    'MAKESY': 0.05,
    'ONZIE': 0.1,
    'HEDLEY & BENNETT': 0.2,
    'IITTALA': 0.1,
    'JUST INGREDIENTS': 0.1,
    'ANTLER': 0.1,
    'MARNI': 0.1,
    'GIRLFRIEND COLLECTIVE': 0.3,
    'ROLAND': 0.3,
    'STARFACE': 0.5,
    'MURAD': 0.5,
    'JUDITH LEIBER': 0.05,
    'PLANTIN TRUFFLES': 0.05,
    'MARINE SERRE': 0.05,
    'PETER THOMAS ROTH': 0.3,
    'MUD WTR': 0.3,
    'PIGLET IN BED': 0.1,
    'PARACHUTE HOME': 0.3,
    'LUNAR TIDES': 0.1,
    'LEMME LIVE': 0.2,
    'JACK RUDY COCKTAIL CO.': 0.05,
    'CAPEL RUGS': 0.1,
    'WUSTHOF': 0.3,
    'LUVME HAIR': 0.1,
    'EVA SOLO': 0.05,
    'BERT FRANK': 0.05,
    'BURROW': 0.2,
    'R+CO': 0.3,
    'AVERR AGLOW': 0.1,
    'MOSCOT': 0.1,
    'HOMARY': 0.1,
    'HAWKINS NEW YORK': 0.1,
    'MEPRA': 0.05,
    'CALIFORNIA NATURALS': 0.1,
    'DOWNLITE': 0.1,
    'PEACHSKINSHEETS': 0.1,
    'LUMINOX': 0.1,
    'SIXPENNY': 0.1,
    'LIV WATCHES': 0.05,
    'STOFFER HOME': 0.1,
    'SHINOLA': 0.3,
    'BRUMATE': 0.3,
    'LINIE DESIGN': 0.05,
    'ALPS OUTDOORZ': 0.1,
    'BEAR MATTRESS': 0.2,
    'MADARA COSMETICS': 0.1,
    'ASTR THE LABEL': 0.2,
    'HOURGLASS COSMETICS': 0.5,
    'MALIN + GOETZ': 0.2,
    'REVIVAL RUGS': 0.05,
    'EASTSIDE GOLF': 0.1,
    'MAISON MATINE': 0.05,
    'INVISIBOBBLE': 0.3,
    'TEJARI AND CO': 0.05,
    'NOYAH': 0.05,
    'URBAN ORIGINALS': 0.1,
    'KRISTIN ESS': 0.5,
    'CULT GAIA': 0.2,
    'MAGIC SPOILER': 0.05,
    'BIG CHILL APPLIANCES': 0.1,
    'MONIN': 0.3,
    'OUTDOOR VOICES': 0.5,
    'LE LABO': 0.3,
    'GIBSON': 0.5,
    'COLDWATER CREEK': 0.3,
    'CYAN DESIGN': 0.05,
    'MAGBAK': 0.1,
    'VERSED SKIN': 0.3,
    'MAGDA BUTRYM': 0.05,
    'KNIX': 0.5,
    'LUME CUBE': 0.1,
    'JAMES BARK': 0.05,
    'OPERA CONTEMPORARY': 0.05,
    'CHICWISH': 0.2,
    'HINKLEY LIGHTING': 0.05,
    'CITIZENS OF HUMANITY': 0.3,
    'MOON JUICE': 0.2,
    'WALLSHOPPE': 0.05,
    'BLANKNYC': 0.3,
    'ROC SKINCARE': 0.5,
    'MICHAEL ARAM': 0.1,
    'WYRMWOOD GAMING': 0.05,
    'MAGIC HOUR': 0.05,
    'SERENA & LILY': 0.3,
    'EVA NYC': 0.2,
    'MADE GOODS': 0.1,
    'POLO RALPH LAUREN FACTORY STORE': 2.0,
    'SCOTCH AND SODA': 0.3,
    'DRI DUCK': 0.1,
    'SUPERGA': 0.3,
    'CREED FRAGRANCE': 0.3,
    'FRANK AND OAK': 0.2,
    'LINOTO': 0.05,
    'HUK': 0.3,
    'LUCA FALONI': 0.05,
    'CLEVR BLENDS': 0.1,
    'LITTLE SLEEPIES': 0.3,
    'BRIONI': 0.1,
    'DRIFT': 0.05,
    'LYMA': 0.1,
    'BONALDO': 0.05,
    'SUPERDOWN': 0.2,
    'PALACE SKATEBOARDS': 0.2,
    'MANDARINA DUCK': 0.1,
    'ARCWAVE': 0.05,
    'SHAPERMINT': 0.3,
    'MELANIE MILLS HOLLYWOOD': 0.05,
    'OBERWEIS DAIRY': 0.1,
    'HOME RUN INN': 0.1,
    'CASTORE': 0.1,
    'KOS NATURALS': 0.1,
    'POLTRONA FRAU': 0.05,
    'LUGGAGEFACTORY': 0.1,
    'ALLFORM': 0.1,
    'VAKKERLIGHT': 0.05,
    'SISLEY-PARIS': 0.1,
    'CONDOM DEPOT': 0.2,
}


def variation(brand: str, base: float) -> float:
    h = hashlib.md5(brand.encode('utf-8')).hexdigest()
    frac = int(h[0:4], 16) % 10000 / 10000.0
    if base >= 10:
        offset = (frac - 0.5) * 1.0
    elif base >= 1:
        offset = (frac - 0.5) * 0.5
    elif base >= 0.1:
        offset = (frac - 0.5) * 0.1
    else:
        offset = (frac - 0.5) * 0.02
    result = round(base + offset, 4)
    if result == round(result, 0):
        result += 0.0137
    return max(round(result, 4), 0.0001)


def main():
    print(f"Reading: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"  {len(df)} rows")

    # Build master lookup: value → new_pct (with variation)
    master: dict[str, float] = {}
    for val, base in FIXES.items():
        master[val.upper().strip()] = variation(val.upper().strip(), base)

    corrected = 0
    cats_touched = set()

    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue

        val = str(row.get('Value', '')).strip().upper()

        if val not in master:
            continue

        new_pct = master[val]
        new_raw = int(round((new_pct / 100.0) * SAMPLE_SIZE))
        new_genpop = int(round((new_raw / SAMPLE_SIZE) * US_POP))

        df.at[idx, 'Brand Penetration (Row)'] = new_pct
        df.at[idx, 'Original Raw Numbers'] = new_raw
        df.at[idx, 'US Gen Pop Projection'] = new_genpop
        corrected += 1
        cats_touched.add(cat)

    print(f"  {corrected} values corrected across {len(cats_touched)} categories")

    # Recalculate Category Share
    for cat in df['Column'].unique():
        cat_upper = str(cat).strip().upper()
        if cat_upper in SKIP_CATEGORIES:
            continue
        mask = df['Column'] == cat
        raws = []
        for i in df.loc[mask].index:
            try:
                r = int(float(str(df.at[i, 'Original Raw Numbers']).replace(',', '')))
            except:
                r = 0
            raws.append((i, r))
        total = sum(r for _, r in raws)
        if total > 0:
            for i, r in raws:
                df.at[i, 'Category Share'] = round((r / total) * 100.0, 4)

    print("  Category Share recalculated")

    # Verify cross-category consistency
    from collections import defaultdict
    val_pcts = defaultdict(set)
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue
        val = str(row.get('Value', '')).strip().upper()
        try:
            pct = round(float(str(row.get('Brand Penetration (Row)', 0)).replace(',', '')), 4)
        except:
            continue
        val_pcts[val].add(pct)

    inconsistent = sum(1 for v, pcts in val_pcts.items() if len(pcts) > 1)
    if inconsistent == 0:
        print("  ✅ Cross-category consistency: ALL values match")
    else:
        print(f"  ⚠️ {inconsistent} values inconsistent")
        for v, pcts in val_pcts.items():
            if len(pcts) > 1:
                print(f"    {v}: {pcts}")
                if inconsistent > 5:
                    break

    df.to_csv(CSV_PATH, index=False)
    print(f"\n  Saved. Done!")


if __name__ == '__main__':
    main()
