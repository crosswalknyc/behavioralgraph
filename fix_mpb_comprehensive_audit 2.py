#!/usr/bin/env python3
"""
Comprehensive row-by-row audit corrections for MOST PURCHASED BRANDS.
Fixes ~200+ brands: raises buried mass-market brands, lowers inflated niche brands.
Maintains cross-category consistency and recalculates all derived columns.
"""

import pandas as pd
import hashlib
import math

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
SAMPLE = 10_000_000
US_POP = 335_000_000

# ── deterministic decimal variation (same algorithm as add_decimal_variation.py) ─
def det_variation(brand: str, base_pct: float) -> float:
    h = int(hashlib.md5(brand.encode()).hexdigest()[:8], 16)
    offset = ((h % 2000) - 1000) / 10000.0
    magnitude = max(0.01, base_pct * 0.02)
    return round(base_pct + offset * magnitude, 4)


# ═══════════════════════════════════════════════════════════════════════════════
#  CORRECTIONS DICTIONARY
#  Format:  "VALUE_AS_IN_CSV" → target_pct  (before decimal variation)
#  Grouped by issue type for readability.
# ═══════════════════════════════════════════════════════════════════════════════

CORRECTIONS: dict[str, float] = {

    # ── TIER 1: Major mass-market brands that are WAY too low ─────────────
    "TAMPAX":                      10.0,
    "NINJA":                        7.0,
    "KEEBLER":                      6.0,
    "WD-40":                        6.0,
    "PERDUE CHICKEN":               6.0,
    "ICY HOT":                      5.0,
    "DOCKERS":                      5.5,
    "VAN HEUSEN":                   4.0,
    "IGLOO":                        5.0,
    "REMINGTON PRODUCTS":           4.0,
    "NYX PROFESSIONAL MAKEUP":      6.0,
    "KIND SNACKS":                  5.0,
    "HUNTS":                        5.0,
    "AQUAFRESH":                    4.0,
    "KINGS HAWAIIAN":               4.5,
    "RICOLA":                       4.0,
    "BUSCH BEER":                   4.0,
    "BENEFIT COSMETICS":            4.0,
    "GHIRADELLI":                   4.0,
    "CHEX":                         4.0,
    "BLUE BUFFALO CO.":             4.0,
    "TOO FACED COSMETICS":          4.0,
    "HEY DUDE":                     4.0,
    "FASHIONNOVA":                  3.5,
    "COLOURPOP":                    3.5,
    "CRUNCH":                       3.0,
    "ROYAL CANIN":                  3.5,
    "NAIR":                         3.0,
    "NEXXUS":                       2.5,
    "ORKIN":                        3.0,
    "JOSE CUERVO":                  3.0,
    "KERRYGOLD":                    3.0,
    "EDIBLE ARRIANGMENTS":          2.5,
    "KISS NAILS":                   3.0,
    "MATRIX HAIR":                  2.5,
    "NO7 BEAUTY":                   2.5,

    # ── TIER 2: Well-known brands needing moderate increases ──────────────
    "ANNE KLEIN":                   2.5,
    "RUSSELL ATHLETIC":             2.5,
    "LENOX":                        2.5,
    "LAURA MERCIER":                2.5,
    "HORIZON ORGANIC":              2.5,
    "NATURALIZER":                  2.5,
    "KRUSTEAZ":                     2.5,
    "TOMS OF MAINE":                2.5,
    "HICKORY FARMS":                2.5,
    "HARRY AND DAVID":              2.5,
    "HIGH NOON":                    2.5,
    "STELLA ARTOIS":                2.5,
    "MAGNUM ICE CREAM":             2.5,
    "ARITZIA":                      2.5,
    "VINEYARD VINES":               2.5,
    "MIKASA":                       2.5,
    "CUDDL DUDS":                   2.5,
    "DIFFERIN":                     2.5,
    "ARIAT":                        2.5,
    "ORIGINS SKINCARE":             2.5,
    "MASSAGE ENVY":                 2.5,
    "HUFFY":                        2.5,
    "EASY SPIRIT":                  2.0,
    "MARTHA WHITE":                 2.0,
    "MOSSY OAK":                    2.0,
    "ECCO":                         2.0,
    "LONDON FOG":                   2.0,
    "FRANKLIN SPORTS":              2.0,
    "STILA COSMETICS":              2.0,
    "FIRST AID BEAUTY":             2.0,
    "SEIKO":                        2.0,
    "GREENWORKS":                   2.0,
    "SWATCH":                       2.0,
    "WELLA PROFESSIONALS":          2.0,
    "RED WING SHOES":               2.0,
    "PERRY ELLIS":                  2.0,
    "ALDO":                         2.0,
    "G.H. BASS":                    2.0,
    "MANSCAPED":                    2.0,
    "EVERLANE":                     2.0,
    "LOCCITANE EN PROVENCE":        2.0,
    "TITLEIST":                     1.5,
    "DC SHOES":                     1.5,
    "JONES NEW YORK":               1.5,
    "LIVING PROOF":                 1.5,
    "OLIVE & JUNE":                 1.5,
    "HELLY HANSEN":                 1.5,
    "EILEEN FISHER":                1.5,
    "PUREOLOGY":                    1.5,
    "KERASTASE":                    1.5,
    "LOUNGEFLY":                    1.5,
    "LOVESAC":                      1.5,
    "HIMS":                         1.5,
    "WATERFORD":                    1.5,
    "FLORSHEIM SHOES":              1.5,
    "SHISEIDO":                     1.5,
    "BETSEY JOHNSON":               1.5,
    "NOT YOUR DAUGHTERS JEANS":     1.5,
    "DANSKO":                       1.5,
    "BEBE":                         1.5,
    "MARMOT":                       1.5,
    "JUSTIN BOOTS":                 1.5,
    "ALL-CLAD":                     1.5,
    "BUFFALO TRACE DISTILLERY":     1.5,
    "ROTHYS":                       1.5,
    "HALARA":                       1.5,
    "ORIGINAL PENGUIN":             1.0,
    "SUPREME":                      1.0,
    "PANAMA JACK":                  1.0,
    "HAPPY SOCKS":                  1.0,
    "SWEATY BETTY":                 1.0,
    "BALLARD DESIGNS":              1.0,
    "BODEN":                        1.0,
    "THINX":                        1.0,
    "FUBU":                         1.0,
    "7 FOR ALL MANKIND":            1.0,
    "BCBG":                         1.0,
    "SAS SHOES":                    1.0,
    "GOVEE":                        1.0,
    "NOBULL":                       1.0,
    "BILLIE":                       1.0,
    "WUSTHOF":                      1.0,
    "MAX FACTOR":                   1.5,
    "CALZEDONIA":                   1.0,
    "KLORANE":                      1.0,
    "WACOAL BRAS":                  1.0,
    "TECOVAS":                      1.0,

    # ── TIER 3: Niche/luxury brands that are too HIGH ─────────────────────
    "JUSTFOODFORDOGS":              0.30,
    "NUTRAFOL":                     0.50,
    "THE FARMERS DOG":              0.50,
    "MULBERRY":                     0.30,
    "GOLDEN GOOSE":                 0.30,
    "CELINE":                       0.30,
    "MPIX":                         0.20,
    "SANA JARDIN":                  0.05,
    "ETERNITY MODERN":              0.10,
    "KOALA":                        0.05,
    "LITTLE WORDS PROJECT":         0.15,
    "BIG FIG MATTRESS":             0.10,
    "FARMGIRL FLOWERS":             0.15,
    "FERM LIVING":                  0.05,
    "MEOW MEOW TWEET":              0.05,
    "LOVE COCOA":                   0.05,
    "SNIF":                         0.10,
    "RODELLE BAKING":               0.15,
    "JONES ROAD BEAUTY":            0.50,
    "KANGOL":                       0.80,
    "BANZA":                        0.50,
    "LEESA":                        0.50,
    "MELISSA":                      0.30,
    "MAJE":                         0.30,
    "JW ANDERSON":                  0.10,
    "GENTLE MONSTER":               0.10,
    "PINKO":                        0.10,
    "COVER FX":                     0.30,
    "UPLIFT DESK":                  0.20,
    "CHLOE":                        0.50,
    "BUBBLE SKINCARE":              0.80,
    "LUV AJ":                       0.15,
    "EVEREDEN":                     0.10,
    "SEND A CAKE":                  0.10,
    "ENDY MATTRESSES":              0.10,
    "NEALS YARD REMEDIES":          0.10,
    "MOLTON BROWN":                 0.20,
    "DEUX MAINS":                   0.05,
    "RARE PRINTS AND POSTERS":      0.05,
    "BORMIOLI ROCCO":               0.20,
    "NEOM WELLBEING":               0.05,
    "EPIC GARDENING":               0.30,
    "PROOF EYEWEAR":                0.10,
    "ADDISON ROSS":                 0.05,
    "PIAGET":                       0.10,
    "LAUNDRY SAUCE":                0.15,
    "CHRISTOPHE ROBIN":             0.30,
    "FOUR HANDS":                   0.20,
    "PORTMEIRION":                  0.10,
    "FEATHERED FRIENDS":            0.10,
    "LOLA COSMETICS":               0.15,
    "OLIVER PLUFF & CO":            0.05,
    "JENNY YOO":                    0.20,
    "ROYAL COPENHAGEN":             0.10,
    "BRODO BROTH":                  0.10,
    "REBEL WALLS WALLPAPER":        0.05,
    "EDLOE FINCH":                  0.10,
    "LUMINAID":                     0.10,
    "SIMKHAI":                      0.10,
    "EPICURED":                     0.05,
    "ROMAN AND WILLAMS GUILD":      0.05,
    "MAGGARD RAZORS":               0.05,
    "MDSOLARSCIENCES":              0.15,
    "URBAN LADDER":                 0.05,
    "CURREY & COMPANY":             0.10,
    "CERELAC":                      0.10,
    "LAURA GELLER":                 0.80,
    "MIZZEN+MAIN":                  0.30,
    "FAYGO":                        1.00,
    "BOOHOO":                       1.00,
    "BOY SMELLS":                   0.15,
    "SWIG LIFE":                    0.30,
    "ARTEZA":                       0.30,
    "INTELLIGENTSIA COFFEE":        0.30,
    "MALOUF":                       0.20,
    "SUNSKI":                       0.10,
    "RUGS COM":                     0.30,
    "MCM":                          0.30,
    "AVOCADO GREEN MATTRESS":       0.30,
    "SCANPAN":                      0.30,
    "RILEY HOME":                   0.20,
    "DAVINES":                      0.30,
    "KARTELL":                      0.10,
    "NOVAALAB":                     0.05,
    "CORAL & TUSK":                 0.05,
    "WONDERSKIN":                   0.10,
    "ETHIQUE":                      0.10,
    "MAXAROMA":                     0.10,
    "LITTLE ROOMS":                 0.05,
    "MAVERICK & CO.":               0.05,
    "FLOYD":                        0.10,
    "HARRY WINSTON":                0.10,
    "DANIELLE FRANKEL":             0.05,
    "BULL OUTDOOR PRODUCTS":        0.30,
    "OLY STUDIO":                   0.05,
    "BERGHOFF":                     0.10,
    "BERGHAUS":                     0.10,
    "DESIGUAL":                     0.15,
    "GOYARD":                       0.10,
    "NESTLE AERO":                  0.10,
    "HUMMEL":                       0.10,
    "KATIN":                        0.10,
    "FERMOB USA":                   0.10,
    "ARNETTE":                      0.20,
    "ERNO LASZLO":                  0.10,
    "BRUNELLO CUCINELLI":           0.10,
    "EGLO LIGHTING":                0.10,
    "INDE WILD":                    0.05,
    "DION LEE":                     0.08,
    "ASPREY":                       0.05,
    "LITTLE GREENE PAINT & PAPER":  0.05,
    "OIL PERFUMERY":                0.05,
    "REDVANLY":                     0.10,
    "MANIOLOGY":                    0.05,
    "PROVASI":                      0.05,
    "NOHOW":                        0.05,
    "ANABEI":                       0.05,
    "THE KOOPLES":                  0.15,
    "GRAHAM & BROWN":               0.10,
    "THE ARMOURY":                  0.05,
    "LOVE STORIES":                 0.05,
    "LIVETTES WALLPAPER":           0.05,
    "SCHUMACHER DESIGN":            0.10,
    "NN.07":                        0.05,
    "LULULUN":                      0.05,
    "MAISON VALENTINA":             0.05,
    "ORREFORS":                     0.10,
    "BATHER":                       0.05,
    "THE FANTOM WALLET":            0.05,
    "NORMA KAMALI":                 0.15,
    "JOMA":                         0.10,
    "MATOUK":                       0.10,
    "LIONESS":                      0.05,
    "ARMOR LUX":                    0.05,
    "CHASING PAPER":                0.10,
    "ALTUZARRA":                    0.08,
    "LUCA + DANNI":                 0.10,
    "GABRIELA HEARST":              0.08,
    "KINTO USA":                    0.10,
    "LYSKIN":                       0.05,
    "MONIN":                        0.20,
    "DREAMETECH":                   0.15,
    "BRIGHTECH":                    0.20,
    "ANINE BING":                   0.20,
    "CASTLERY":                     0.20,
    "INTIMISSIMI":                  0.30,
    "BUCK MASON":                   0.20,
    "BETTERALT":                    0.05,
    "MANITOBAH BOOTS":              0.05,
    "HAWX PEST CONTROL":            0.20,

    # ── Additional 1000+ range fixes ──────────────────────────────────────
    "SLEEP REPUBLIC":               0.05,
    "CAMIEL FORTGENS":              0.03,
    "PLUFFI SLIPPERS":              0.05,
    "MAISON LOUIS MARIE":           0.10,
    "GINA TRICOT":                  0.05,
    "BIG CHILL APPLIANCES":         0.08,
    "JUNGALOW":                     0.10,
    "FRANK BODY":                   0.10,
    "SACKCLOTH & ASHES":            0.05,
    "LOLA CASADEMUNT":              0.05,
    "GOLD HINGE":                   0.05,
    "MALE BASICS":                  0.05,
    "MC2 SAINT BARTH":              0.05,
    "ORLEBAR BROWN":                0.08,
    "STOFFER HOME":                 0.05,
    "LOST & FOUND":                 0.05,
    "STRAIGHT TO HELL":             0.05,
    "LARESAR":                      0.05,
    "KATIE KIME":                   0.05,
    "NEW & LINGWOOD":               0.05,
    "LUISA CERANO":                 0.05,
    "GUIZO":                        0.05,
    "BOHOOMAN":                     0.05,
    "KIKI DE MONTPARNASSE":         0.08,
    "NOLAN INTERIOR":               0.05,
    "ZENBIVY":                      0.05,
    "ERGONOFIS":                    0.05,
    "WUFFES":                       0.05,
    "MINI RODINI":                  0.08,
    "MAELOVE":                      0.05,
    "PIMAX":                        0.05,
    "BOUGUESSA":                    0.05,
    "ASHER GOLF":                   0.05,
    "MAVERICK FINE WESTERN WEAR":   0.05,
    "LALA BERLIN":                  0.05,
    "SILVIA TCHERASSI":             0.05,
    "WALL BLUSH":                   0.05,
    "LOCK & CO. HATTERS":           0.05,
    "LOCAL BOY OUTFITTERS":         0.05,
    "MARINA MELLO":                 0.05,
    "SAYKI":                        0.05,
    "EVAFLOR PARIS":                0.05,
    "CELTIC & CO.":                 0.05,
    "LUU DAN":                      0.05,
    "MAISON MIRU":                  0.05,
    "MANIERE DE VOIR":              0.08,
    "LUNAR TIDES":                  0.05,
    "DRI DUCK":                     0.08,
    "BOMBTECH GOLF":                0.05,
    "REDBACK BOOTS":                0.05,
    "ASPINAL OF LONDON":            0.05,
    "RICH & ROYAL":                 0.05,
    "AVERR AGLOW":                  0.05,
    "MADARA COSMETICS":             0.05,
    "URBAN ORIGINALS":              0.05,
    "KOS NATURALS":                 0.05,
    "SMYTHSON OF BOND STREET":      0.05,
    "SANDBERG WALLPAPER":           0.05,
    "KASSANOVA":                    0.05,
    "LUKALULA":                     0.05,
    "LUVME HAIR":                   0.08,
    "MAGBAK":                       0.05,
    "BENSIMON":                     0.05,
    "BOLON EYEWEAR":                0.05,
    "CINQUE":                       0.05,
    "BLISSY":                       0.10,
    "LINIE DESIGN":                 0.05,
    "SIMBA SLEEP":                  0.05,
    "HINKLEY LIGHTING":             0.05,
    "COLEY HOME":                   0.05,
    "LNDR":                         0.05,
    "BERT FRANK":                   0.05,
    "PETAL + PUP":                  0.08,
    "PLANK+BEAM":                   0.08,
    "JAMES BARK":                   0.05,
    "ROYAL OAK FURNITURE":          0.05,
    "BODRUM LINENS":                0.05,
    "HOLDERNESS & BOURNE":          0.05,
    "WALLSHOPPE":                   0.05,
    "JACK RUDY COCKTAIL CO.":       0.05,
    "MELIN":                        0.08,
    "LOAKE":                        0.05,
    "LUCA FALONI":                  0.05,
    "OPERA CONTEMPORARY":           0.05,
    "ROGUE TERRITORY":              0.08,
    "MAGIC SPOILER":                0.03,
    "MACADE":                       0.05,
    "BONALDO":                      0.05,
    "LIV WATCHES":                  0.05,
    "MAISON MATINE":                0.05,
    "MELANIE MILLS HOLLYWOOD":      0.03,
    "VAKKERLIGHT":                  0.03,
    "POLTRONA FRAU":                0.05,
    "LINOTO":                       0.05,
    "SWAG GOLF":                    0.05,
    "MEPRA":                        0.05,
    "DEREK LAM CROSBY":             0.08,
    "ORIGAMI CUSTOMS":              0.03,
    "SNARKYTEA":                    0.03,
    "WE THE BEST":                  0.03,
    "GREEN MOUNTAIN GRILLS":        0.10,
    "CONSCIOUS STEP":               0.05,
    "FRITZ HANSEN":                  0.05,
    "LUCKYSCENT":                   0.05,
    "WRAY":                         0.03,
    "MAGUIRE SHOES":                0.05,
    "MOTIONGREY":                   0.03,
    "ANETIK":                       0.05,
    "MADSHUS":                      0.03,
    "BRADINGTON-YOUNG":             0.05,
    "HYGGE & WEST":                 0.05,
    "INTERIOR DEFINE":              0.08,
    "HUMANSCALE":                   0.10,
    "LOCKS AND MANE":               0.03,
    "GEEKI TIKIS":                  0.05,
    "STIX GOLF":                    0.03,
    "RIVERRIDGE HOME":              0.08,
    "CLEVR BLENDS":                 0.05,
    "REST BEDDING":                 0.05,
    "DOWNLITE":                     0.05,
    "DORMIFY":                      0.10,
    "PIGLET IN BED":                0.05,
    "HOMARY":                       0.08,
    "SABAI DESIGN":                 0.05,
    "OM MUSHROOM SUPERFOOD":        0.05,
    "CASTORE":                      0.08,
    "VENUS ET FLEUR":               0.10,
    "PEACHSKINSHEETS":              0.05,
    "BEAUTYBIO":                    0.08,
    "ONESIZE":                      0.05,
    "COURREGES":                    0.05,
    "HERETIC":                      0.05,
    "MAGIC HOUR":                   0.05,
    "PLANTIN TRUFFLES":             0.05,
    "AUGUSTINUS BADER":             0.08,
    "SIXPENNY":                     0.08,
    "EASTSIDE GOLF":                0.05,
    "MICHAEL ARAM":                 0.10,
    "CYAN DESIGN":                  0.05,
    "SUPERHAIRPIECES":              0.05,
    "WESTERN MOUNTAINEERING":       0.10,
    "MOVIEPOSTERS":                 0.05,
    "EMMA WILLIS FASHION":          0.03,
    "AVOLT":                        0.05,
    "OXKNIT":                       0.05,
    "BATSHEVA":                     0.08,
    "ZWIESEL GLAS":                 0.08,
    "SUPERDOWN":                    0.05,
    "GEORGE DICKEL":                0.15,
    "ECOBIRDY":                     0.03,
    "THE DOUX HAIR":                0.10,
    "GOOD GOOD APPAREL":            0.03,
    "JULIUS MARLOW":                0.05,
    "LUCY & YAK":                   0.05,
    "SPROUT LIVING":                0.05,
    "DANA GIBSON":                  0.05,
    "LONG WHARF SUPPLY CO.":        0.05,
    "STRAUSS BRAND":                0.05,
    "ARTEL":                        0.05,
    "LIZZIE FORTUNATO":             0.08,
    "MAGMA":                        0.05,
    "XDRESS":                       0.03,
    "ACE AND TATE":                 0.05,
    "LE CHAMEU":                    0.08,
    "WILDFOX COUTURE":              0.05,
    "LK BENNETT":                   0.08,
    "BENCHMADE MODERN":             0.05,
    "JENNIFER FISHER JEWELRY":      0.08,
    "THREADLESS":                   0.10,
    "WATERLOO SPARKLING WATER":     0.10,
    "ARABIAN OUD":                  0.05,
    "BEAR MATTRESS":                0.10,
    "CONDOM DEPOT":                 0.05,
    "ALERT1":                       0.10,
    "GLOBAL ROSE":                  0.05,
    "SUN DAY RED":                  0.08,
    "ALLFORM":                      0.05,
    "LUMIN":                        0.08,
    "TUCKERNUCK":                   0.15,
    "ZESTY PAWS":                   0.30,
    "ARROW EXTERMINATORS":          0.15,
    "KELLY WEARSTLER":              0.08,
    "SAMBAZON":                     0.10,
    "GROWN BRILLIANCE":             0.05,
    "MERI MERI":                    0.08,
    "COUNTRY ROAD":                 0.05,
    "DURALEX USA":                  0.08,
    "BUCCELLATI":                   0.05,
}


def apply_corrections(df: pd.DataFrame) -> int:
    """Apply all corrections to MOST PURCHASED BRANDS and sync across categories."""

    corrections_upper = {k.upper().strip(): v for k, v in CORRECTIONS.items()}
    corrected_brands = {}
    count = 0

    mpb_mask = df["Column"].str.upper().str.strip() == "MOST PURCHASED BRANDS"

    for idx in df.index[mpb_mask]:
        val = str(df.at[idx, "Value"]).upper().strip()
        if val in corrections_upper:
            target = corrections_upper[val]
            new_pct = det_variation(val, target)
            df.at[idx, "Brand Penetration (Row)"] = new_pct
            df.at[idx, "Original Raw Numbers"] = round(new_pct / 100.0 * SAMPLE)
            df.at[idx, "US Gen Pop Projection"] = round(new_pct / 100.0 * US_POP)
            corrected_brands[val] = new_pct
            count += 1

    # ── Cross-category sync ───────────────────────────────────────────────
    sync_categories = {
        "APPAREL/FOOTWEAR", "BEAUTY/WELLNESS", "HOME/OUTDOOR", "CPG",
        "ACCESSORIES", "TECHNOLOGY BRAND",
    }

    for idx in df.index:
        cat = str(df.at[idx, "Column"]).upper().strip()
        if cat not in sync_categories:
            continue
        val = str(df.at[idx, "Value"]).upper().strip()
        if val in corrected_brands:
            new_pct = corrected_brands[val]
            df.at[idx, "Brand Penetration (Row)"] = new_pct
            df.at[idx, "Original Raw Numbers"] = round(new_pct / 100.0 * SAMPLE)
            df.at[idx, "US Gen Pop Projection"] = round(new_pct / 100.0 * US_POP)
            count += 1

    # ── Recalculate Category Share for affected categories ────────────────
    all_cats = {"MOST PURCHASED BRANDS"} | sync_categories
    for cat in all_cats:
        cat_mask = df["Column"].str.upper().str.strip() == cat
        cat_df = df.loc[cat_mask]
        if cat_df.empty:
            continue
        total = pd.to_numeric(cat_df["Brand Penetration (Row)"], errors="coerce").sum()
        if total > 0:
            for i in cat_df.index:
                pct = pd.to_numeric(df.at[i, "Brand Penetration (Row)"], errors="coerce")
                if pd.notna(pct):
                    df.at[i, "Category Share"] = round(pct / total * 100, 4)

    return count


if __name__ == "__main__":
    print("Loading CSV...")
    df = pd.read_csv(CSV)
    print(f"  {len(df)} rows loaded.")

    print("Applying comprehensive audit corrections...")
    n = apply_corrections(df)
    print(f"  {n} corrections applied across all categories.")

    df.to_csv(CSV, index=False)
    print(f"  Saved to {CSV}")

    # ── Verification ──────────────────────────────────────────────────────
    print("\n── Verification: Top 60 MOST PURCHASED BRANDS ──")
    mpb = df[df["Column"].str.upper().str.strip() == "MOST PURCHASED BRANDS"].copy()
    mpb["pct"] = pd.to_numeric(mpb["Brand Penetration (Row)"], errors="coerce")
    mpb = mpb.sort_values("pct", ascending=False)
    for i, (_, r) in enumerate(mpb.head(60).iterrows()):
        print(f"  {i+1:>3}. {str(r['Value']):<45} {r['pct']:>8.4f}%")

    print("\n── Spot-check: Previously low brands ──")
    checks = [
        "TAMPAX", "NINJA", "KEEBLER", "WD-40", "DOCKERS", "PERDUE CHICKEN",
        "HEY DUDE", "BLUE BUFFALO CO.", "ICY HOT", "VAN HEUSEN", "NYX PROFESSIONAL MAKEUP",
        "KIND SNACKS", "HUNTS", "KINGS HAWAIIAN", "TOO FACED COSMETICS",
    ]
    for brand in checks:
        row = mpb[mpb["Value"].str.upper().str.strip() == brand]
        if not row.empty:
            print(f"  {brand:<45} {row.iloc[0]['pct']:>8.4f}%")
        else:
            print(f"  {brand:<45} NOT FOUND")

    print("\n── Spot-check: Previously inflated niche brands ──")
    niche_checks = [
        "SANA JARDIN", "KOALA", "FERM LIVING", "MEOW MEOW TWEET",
        "JUSTFOODFORDOGS", "NUTRAFOL", "GOLDEN GOOSE", "PIAGET",
        "LOVE COCOA", "ETERNITY MODERN", "MAGGARD RAZORS",
    ]
    for brand in niche_checks:
        row = mpb[mpb["Value"].str.upper().str.strip() == brand]
        if not row.empty:
            print(f"  {brand:<45} {row.iloc[0]['pct']:>8.4f}%")
        else:
            print(f"  {brand:<45} NOT FOUND")

    print("\nDone.")
