#!/usr/bin/env python3
"""
One-time script to apply all verified US gen-pop penetration corrections
to the Gen Pop CSV and ensure cross-category consistency.

Run:  python apply_genpop_corrections.py
"""

import pandas as pd
import sys

CSV_PATH = '/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv'
SAMPLE_SIZE = 10_000_000
US_POP = 329_900_000

SKIP_CATEGORIES = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
    'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
    'OCCUPATION', 'LOCATION',
}

# ── (CATEGORY, VALUE) -> corrected_penetration_pct ────────────────────────
# Only entries where the discrepancy vs. reality is material.
# SEARCH ENGINE/AI and BETTING are excluded per user request.

CORRECTIONS: dict[tuple[str, str], float] = {

    # ══════════════════════════════════════════════════════════════════════
    # STREAMING / PLATFORM
    # ══════════════════════════════════════════════════════════════════════
    ('STREAMING/PLATFORM', 'NETFLIX'):            67.0,
    ('STREAMING/PLATFORM', 'HULU'):               17.0,
    ('STREAMING/PLATFORM', 'DISNEY+'):            28.0,
    ('STREAMING/PLATFORM', 'HBO MAX'):            22.0,
    ('STREAMING/PLATFORM', 'APPLE TV+'):          13.0,
    ('STREAMING/PLATFORM', 'PARAMOUNT+'):         11.0,
    ('STREAMING/PLATFORM', 'PEACOCK'):             9.0,
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): 43.0,
    ('STREAMING/PLATFORM', 'KICK'):                2.5,
    ('STREAMING/PLATFORM', 'UFC FIGHT PASS'):      2.5,
    ('STREAMING/PLATFORM', 'TELEMUNDO'):           6.0,
    ('STREAMING/PLATFORM', 'KALOS TV'):            0.5,
    ('STREAMING/PLATFORM', 'SLING PLATFORM'):      3.0,
    ('STREAMING/PLATFORM', 'DAZN'):                1.0,
    ('STREAMING/PLATFORM', 'MUBI'):                0.5,
    ('STREAMING/PLATFORM', 'SHOWTIME TV'):         4.0,
    ('STREAMING/PLATFORM', 'YOUTUBE KIDS'):        8.0,
    ('STREAMING/PLATFORM', 'DISCOVERY+'):          5.0,
    ('STREAMING/PLATFORM', 'STARZ'):               3.0,
    ('STREAMING/PLATFORM', 'AMC PLUS'):            1.5,
    ('STREAMING/PLATFORM', 'BRITBOX'):             0.5,
    ('STREAMING/PLATFORM', 'HALLMARK PLUS'):       1.0,
    # ESPN already corrected to 27.0 in prior pass

    # ══════════════════════════════════════════════════════════════════════
    # TELECOM
    # ══════════════════════════════════════════════════════════════════════
    ('TELECOM', 'XFINITY'):          25.0,
    ('TELECOM', 'VERIZON'):          30.0,
    ('TELECOM', 'T-MOBILE'):         28.0,
    ('TELECOM', 'AT&T'):             25.0,
    ('TELECOM', 'STARLINK'):          1.5,
    ('TELECOM', 'SPECTRUM'):         11.0,
    ('TELECOM', 'GOOGLE FIBER'):      0.5,
    ('TELECOM', 'CENTURY LINK'):      2.0,
    ('TELECOM', 'VISIBLE'):           1.0,
    ('TELECOM', 'CRICKET WIRELESS'):  2.5,

    # ══════════════════════════════════════════════════════════════════════
    # BANKING
    # ══════════════════════════════════════════════════════════════════════
    ('BANKING', 'CHASE'):                22.0,
    ('BANKING', 'BANK OF AMERICA'):      14.0,
    ('BANKING', 'WELLS FARGO'):          11.0,
    ('BANKING', 'CITIBANK'):              6.0,
    ('BANKING', 'TD BANK'):               4.5,
    ('BANKING', 'US BANK'):               5.0,
    ('BANKING', 'SOFI BANK'):             1.5,
    ('BANKING', 'TRUIST BANK'):           5.0,
    ('BANKING', 'PNC BANK'):              5.0,
    ('BANKING', 'BANK OF MONTREAL/BMO'):  2.0,
    ('BANKING', 'VANGUARD'):              7.0,
    ('BANKING', 'FIFTH THIRD BANK'):      2.0,
    ('BANKING', 'HUNTINGTON BANK'):       2.0,
    ('BANKING', 'CITIZENS BANK'):         2.0,
    ('BANKING', 'REGIONS BANK'):          2.0,
    ('BANKING', 'KEYBANK'):               1.5,
    ('BANKING', 'BARCLAYS US'):           1.0,
    ('BANKING', 'SANTANDER BANK'):        1.0,
    ('BANKING', 'APPLE PAY'):            13.0,

    # ══════════════════════════════════════════════════════════════════════
    # CREDIT PROVIDER
    # ══════════════════════════════════════════════════════════════════════
    ('CREDIT PROVIDER', 'AMERICAN EXPRESS'):      14.0,
    ('CREDIT PROVIDER', 'CAPITAL ONE'):           18.0,
    ('CREDIT PROVIDER', 'DISCOVER CREDIT CARD'):   7.0,
    ('CREDIT PROVIDER', 'SYNCHRONY'):              6.0,
    ('CREDIT PROVIDER', 'AFFIRM PAYMENT'):         4.0,
    ('CREDIT PROVIDER', 'GM FINANCIAL'):           2.0,
    ('CREDIT PROVIDER', 'FUNDBOX'):                0.5,
    ('CREDIT PROVIDER', 'FREEDOM MORTGAGE'):       2.0,
    ('CREDIT PROVIDER', 'QUICKEN LOANS'):          3.0,

    # ══════════════════════════════════════════════════════════════════════
    # INSURANCE
    # ══════════════════════════════════════════════════════════════════════
    ('INSURANCE', 'UNITED HEALTHCARE'):  16.0,
    ('INSURANCE', 'USAA'):                6.0,
    ('INSURANCE', 'KAISER PERMANENTE'):   5.0,
    ('INSURANCE', 'GEICO'):              15.0,
    ('INSURANCE', 'AETNA'):               9.0,
    ('INSURANCE', 'STATE FARM'):         17.0,
    ('INSURANCE', 'CIGNA'):               7.0,
    ('INSURANCE', 'HUMANA'):              6.0,
    ('INSURANCE', 'METLIFE'):             5.0,
    ('INSURANCE', 'PROGRESSIVE'):        14.0,
    ('INSURANCE', 'ALLSTATE'):           10.0,
    ('INSURANCE', 'HEALTHCARE.GOV'):      5.0,
    ('INSURANCE', 'LIBERTY MUTUAL'):      7.0,

    # ══════════════════════════════════════════════════════════════════════
    # TRAVEL  (top ~35 brands with biggest discrepancies)
    # ══════════════════════════════════════════════════════════════════════
    ('TRAVEL', 'DELTA AIR LINES'):                  7.0,
    ('TRAVEL', 'AMERICAN AIRLINES'):                6.0,
    ('TRAVEL', 'UNITED AIRLINE & AVIATIONS'):       6.0,
    ('TRAVEL', 'BOOKING'):                         12.0,
    ('TRAVEL', 'EXPEDIA'):                         10.0,
    ('TRAVEL', 'AIRBNB'):                          12.0,
    ('TRAVEL', 'SOUTHWEST AIRLINES'):               8.0,
    ('TRAVEL', 'TRIPADVISOR'):                      8.0,
    ('TRAVEL', 'MARRIOTT'):                         8.0,
    ('TRAVEL', 'ALASKA AIRLINES'):                  2.0,
    ('TRAVEL', 'RADISSON HOTELS'):                  2.0,
    ('TRAVEL', 'VRBO'):                             5.0,
    ('TRAVEL', 'AMERICAN EXPRESS TRAVEL'):           3.0,
    ('TRAVEL', 'AVIS'):                             4.0,
    ('TRAVEL', 'HILTON'):                           8.0,
    ('TRAVEL', 'MSC CRUISES'):                      1.0,
    ('TRAVEL', 'HOTELS.COM'):                       6.0,
    ('TRAVEL', 'TRIVAGO'):                          4.0,
    ('TRAVEL', 'PRICELINE'):                        5.0,
    ('TRAVEL', 'HYATT'):                            3.0,
    ('TRAVEL', 'JET BLUE'):                         3.0,
    ('TRAVEL', 'UBER'):                            20.0,
    ('TRAVEL', 'MGM RESORTS'):                      3.0,
    ('TRAVEL', 'SANDALS RESORT'):                   1.0,
    ('TRAVEL', 'DISNEY VACATION CLUB'):             1.5,
    ('TRAVEL', 'RITZ-CARLTON'):                     0.5,
    ('TRAVEL', 'ROYAL CARIBBEAN'):                  3.0,
    ('TRAVEL', 'CARNIVAL CRUISE LINE'):             3.0,
    ('TRAVEL', 'NORWEGIAN CRUISE LINE'):            1.5,
    ('TRAVEL', 'AMTRAK'):                           5.0,
    ('TRAVEL', 'LYFT'):                            12.0,
    ('TRAVEL', 'HERTZ'):                            3.0,
    ('TRAVEL', 'ENTERPRISE'):                       4.0,
    ('TRAVEL', 'TURO'):                             2.0,
    ('TRAVEL', 'SPIRIT AIRLINES'):                  2.5,
    ('TRAVEL', 'FRONTIER AIRLINES'):                2.0,
    ('TRAVEL', 'FOUR SEASONS HOTEL & RESORTS'):     0.3,

    # ══════════════════════════════════════════════════════════════════════
    # QSR
    # ══════════════════════════════════════════════════════════════════════
    ('QSR', 'STARBUCKS'):                40.0,
    ('QSR', 'MCDONALDS'):               37.0,
    ('QSR', 'DOMINOS'):                  27.0,
    ('QSR', 'PIZZA HUT'):               22.0,
    ('QSR', 'TACO BELL'):               32.0,
    ('QSR', 'BURGER KING'):             22.0,
    ('QSR', 'JERSEY MIKES SUBS'):        8.0,
    ('QSR', 'PAPA JOHNS'):              12.0,
    ('QSR', 'PORTILLOS'):                2.0,
    ('QSR', 'NOODLES AND CO.'):           2.0,
    ('QSR', 'SWEETGREEN'):               2.5,
    ('QSR', 'EL POLLO LOCO'):            3.0,
    ('QSR', 'MOES SOUTHWEST GRILL'):     3.0,
    ('QSR', 'CHIPOTLE MEXICAN GRILL'):  20.0,
    ('QSR', 'BONCHON'):                   1.5,
    ('QSR', 'KFC'):                      18.0,
    ('QSR', 'SPRINKLES CUPCAKES'):        0.5,
    ('QSR', 'AUNTIE ANNES PRETZELS'):    5.0,
    ('QSR', 'CHICK-FIL-A'):             32.0,
    ('QSR', 'JENIS ICE CREAM'):           1.0,
    ('QSR', 'FRESH BROTHERS'):            0.5,
    ('QSR', 'LITTLE CAESARS'):           12.0,
    ('QSR', 'PLANET SMOOTHIE'):           1.0,
    ('QSR', 'POPEYES'):                  12.0,
    ('QSR', 'ZAXBYS'):                    4.0,
    ('QSR', 'PANERA BREAD'):            14.0,
    ('QSR', 'BOJANGLES'):                 3.0,
    ('QSR', 'PEI WEI ASIAN KITCHEN'):    1.0,
    ('QSR', 'DUNKIN'):                   23.0,
    ('QSR', 'WINGSTOP'):                  8.0,

    # ══════════════════════════════════════════════════════════════════════
    # WHERE THEY SHOP  (biggest discrepancies)
    # ══════════════════════════════════════════════════════════════════════
    ('WHERE THEY SHOP', 'TARGET'):                        48.0,
    ('WHERE THEY SHOP', 'WALMART'):                       85.0,
    ('WHERE THEY SHOP', 'ALBERTSONS'):                     8.0,
    ('WHERE THEY SHOP', 'COSTCO'):                        28.0,
    ('WHERE THEY SHOP', 'CVS'):                           30.0,
    ('WHERE THEY SHOP', 'PUBLIX'):                        12.0,
    ('WHERE THEY SHOP', 'EBAY'):                          12.0,
    ('WHERE THEY SHOP', 'SEPHORA'):                       12.0,
    ('WHERE THEY SHOP', 'LOWES'):                         20.0,
    ('WHERE THEY SHOP', 'HOME DEPOT'):                    25.0,
    ('WHERE THEY SHOP', 'WAYFAIR'):                        8.0,
    ('WHERE THEY SHOP', 'MACYS'):                         15.0,
    ('WHERE THEY SHOP', 'SHEIN'):                         12.0,
    ('WHERE THEY SHOP', 'ETSY'):                           7.0,
    ('WHERE THEY SHOP', 'IKEA'):                          10.0,
    ('WHERE THEY SHOP', 'TEMU'):                          10.0,
    ('WHERE THEY SHOP', 'PACSUN'):                         2.5,
    ('WHERE THEY SHOP', 'OPTICSPLANET'):                   1.0,
    ('WHERE THEY SHOP', 'QVC'):                            8.0,
    ('WHERE THEY SHOP', 'YVES SAINT LAURENT'):             0.5,
    ('WHERE THEY SHOP', 'OVERSTOCK'):                      3.0,
    ('WHERE THEY SHOP', 'SAKS FIFTH AVENUE'):              2.5,
    ('WHERE THEY SHOP', 'REVOLVE'):                        2.0,
    ('WHERE THEY SHOP', 'BURBERRY'):                       0.5,
    ('WHERE THEY SHOP', 'MEIJER'):                         5.0,
    ('WHERE THEY SHOP', 'MERCADO LIBRE'):                  0.5,
    ('WHERE THEY SHOP', 'PAVILIONS'):                      1.5,
    ('WHERE THEY SHOP', 'SWAROVSKI'):                      2.0,
    ('WHERE THEY SHOP', 'TRADER JOES'):                   12.0,
    ('WHERE THEY SHOP', 'YOOX'):                           0.3,
    ('WHERE THEY SHOP', 'BEST BUY'):                      20.0,
    ('WHERE THEY SHOP', 'ACADEMY SPORTS + OUTDOORS'):      4.0,
    ('WHERE THEY SHOP', 'WHOLE FOODS MARKET'):             8.0,
    ('WHERE THEY SHOP', 'BALENCIAGA'):                     0.3,
    ('WHERE THEY SHOP', 'WILLIAMS-SONOMA'):                3.0,
    ('WHERE THEY SHOP', 'ADVANCE AUTO PARTS'):             3.0,
    ('WHERE THEY SHOP', 'TRAMONTINA'):                     1.0,
    ('WHERE THEY SHOP', 'ALLMODERN'):                      1.0,
    ('WHERE THEY SHOP', 'ALEXANDER MCQUEEN'):              0.3,

    # ══════════════════════════════════════════════════════════════════════
    # WHERE THEY DINE  (uniformly too low — need to boost)
    # ══════════════════════════════════════════════════════════════════════
    ('WHERE THEY DINE', 'THE CHEESECAKE FACTORY'):                  6.0,
    ('WHERE THEY DINE', 'TEXAS ROADHOUSE'):                         5.0,
    ('WHERE THEY DINE', 'OLIVE GARDEN'):                            8.0,
    ('WHERE THEY DINE', 'RUTHS CHRIS STEAK HOUSE'):                 2.0,
    ('WHERE THEY DINE', 'GOLDEN CORRAL'):                           4.0,
    ('WHERE THEY DINE', 'FOGO DE CHAO'):                            1.0,
    ('WHERE THEY DINE', 'CHILIS'):                                  6.0,
    ('WHERE THEY DINE', 'THE CAPITAL GRILLE'):                      1.0,
    ('WHERE THEY DINE', 'BJS RESTAURANT & BREWHOUSE'):              2.0,
    ('WHERE THEY DINE', 'CRACKER BARREL'):                          4.0,
    ('WHERE THEY DINE', 'RED LOBSTER'):                             4.0,
    ('WHERE THEY DINE', 'APPLEBEES GRILL + BAR'):                   5.0,
    ('WHERE THEY DINE', 'OUTBACK STEAKHOUSE'):                      4.0,
    ('WHERE THEY DINE', 'CALIFORNIA PIZZA KITCHEN'):                1.5,
    ('WHERE THEY DINE', 'BENIHANA'):                                1.0,
}


def apply_corrections():
    print(f"Reading CSV: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"  {len(df)} rows loaded")

    # ── 1. Apply direct corrections ───────────────────────────────────
    corrected_count = 0
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        val = str(row.get('Value', '')).strip().upper()
        key = (cat, val)

        if key in CORRECTIONS:
            new_pct = CORRECTIONS[key]
            new_raw = int(round((new_pct / 100.0) * SAMPLE_SIZE))
            new_genpop = int(round((new_raw / SAMPLE_SIZE) * US_POP))

            df.at[idx, 'Brand Penetration (Row)'] = round(new_pct, 4)
            df.at[idx, 'Original Raw Numbers'] = new_raw
            df.at[idx, 'US Gen Pop Projection'] = new_genpop
            corrected_count += 1

    print(f"  {corrected_count} direct corrections applied")

    # ── 2. Cross-category consistency ─────────────────────────────────
    # Build lookup: VALUE -> corrected penetration (from corrections)
    # If a VALUE appears in multiple categories, ALL should share values.
    value_to_pct: dict[str, float] = {}
    for (cat, val), pct in CORRECTIONS.items():
        value_to_pct[val] = pct

    # Also include the prior corrections (from genpop_calibration.py)
    PRIOR_CORRECTIONS = {
        'TWITCH': 8.5, 'DISCORD': 16.5, 'X': 27.5, 'PATREON': 4.0,
        'TUMBLR': 4.0, 'ONLYFANS': 2.5, 'SNAPCHAT': 37.5,
        'LETTERBOXD': 1.5, 'BLUESKY': 1.5,
        'SPOTIFY': 33.0, 'APPLE MUSIC': 17.0, 'YOUTUBE MUSIC': 9.0,
        'SIRIUSXM': 13.0, 'PANDORA MUSIC': 17.5, 'AMAZON MUSIC': 16.0,
        'LAST FM': 2.5, 'DEEZER': 1.5, 'SOUNDCLOUD': 6.0,
        'QOBUZ': 0.5, 'TIDAL': 1.5,
        'SLACK': 5.0, 'FIVERR': 2.5, 'FIGMA': 2.5, 'UPWORK': 3.5,
        'CRUNCHYROLL': 4.5, 'HUBSPOT': 1.5, 'TINDER': 9.0,
        'DUOLINGO': 9.0, 'WHATSAPP': 26.0, 'IMDB': 11.0,
        'PAYPAL': 47.0, 'COINBASE': 10.0, 'BILT': 1.5,
        'ROBLOX': 16.0, 'MINECRAFT': 16.0, 'FORTNITE': 11.0,
        'LEAGUE OF LEGENDS': 4.0, 'OVERWATCH': 2.5,
        'GENSHIN IMPACT': 2.5, 'CALL OF DUTY': 9.0,
        'ESPN': 27.0, 'FOX NEWS': 16.0, 'CNN': 13.0, 'MSNBC': 9.0,
        'NEW YORK TIMES': 11.0, 'BUZZFEED': 9.0,
        'UDEMY': 4.0, 'W3 SCHOOLS': 2.5, 'MASTER CLASS': 2.5,
        'SKILLSHARE': 1.5,
        'CHARLES SCHWAB': 11.0, 'FIDELITY': 13.0, 'ROBINHOOD': 7.5,
        'BMW': 9.0, 'MERCEDES-BENZ': 7.0, 'AUDI': 5.0,
        'PORSCHE': 1.5, 'FERRARI': 0.5, 'LAMBORGHINI': 0.3,
    }
    for val, pct in PRIOR_CORRECTIONS.items():
        if val not in value_to_pct:
            value_to_pct[val] = pct

    consistency_count = 0
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        val = str(row.get('Value', '')).strip().upper()
        key = (cat, val)

        if cat in SKIP_CATEGORIES:
            continue
        # Skip if already directly corrected above
        if key in CORRECTIONS:
            continue

        if val in value_to_pct:
            new_pct = value_to_pct[val]
            current_pct = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', '') or 0)
            if abs(current_pct - new_pct) > 0.01:
                new_raw = int(round((new_pct / 100.0) * SAMPLE_SIZE))
                new_genpop = int(round((new_raw / SAMPLE_SIZE) * US_POP))

                df.at[idx, 'Brand Penetration (Row)'] = round(new_pct, 4)
                df.at[idx, 'Original Raw Numbers'] = new_raw
                df.at[idx, 'US Gen Pop Projection'] = new_genpop
                consistency_count += 1
                print(f"  Cross-cat consistency: {cat}/{val}  {current_pct:.2f}% -> {new_pct}%")

    print(f"  {consistency_count} cross-category consistency fixes applied")

    # ── 3. Recalculate Category Share for ALL categories ──────────────
    categories = df['Column'].unique()
    for cat in categories:
        cat_upper = str(cat).strip().upper()
        if cat_upper in SKIP_CATEGORIES:
            continue

        cat_mask = df['Column'] == cat
        cat_df = df.loc[cat_mask]

        raws = []
        for idx in cat_df.index:
            try:
                r = int(float(str(df.at[idx, 'Original Raw Numbers']).replace(',', '')))
            except (ValueError, TypeError):
                r = 0
            raws.append((idx, r))

        total_raw = sum(r for _, r in raws)
        if total_raw > 0:
            for idx_val, raw in raws:
                share = (raw / total_raw) * 100.0
                df.at[idx_val, 'Category Share'] = round(share, 4)

    print("  Category Share recalculated for all categories")

    # ── 4. Save ───────────────────────────────────────────────────────
    df.to_csv(CSV_PATH, index=False)
    print(f"  Saved to {CSV_PATH}")

    # ── 5. Summary ────────────────────────────────────────────────────
    total = corrected_count + consistency_count
    print(f"\nDone: {total} values corrected ({corrected_count} direct + {consistency_count} cross-category)")


if __name__ == '__main__':
    apply_corrections()
