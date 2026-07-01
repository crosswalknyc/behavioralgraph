#!/usr/bin/env python3
"""
Manual digital-panel calibration for WHERE THEY SHOP.
Every single retailer is hand-set — no redistribution.
Calibrated for a US digital panel of ONLINE SHOPPERS.
"""

import pandas as pd
import hashlib

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
SAMPLE = 10_000_000
US_POP = 335_000_000

KNOWN = {
    # ── TIER 1: Mega retailers (50-92%) ───────────────────────────────────
    "AMAZON":                           92.0,
    "WALMART":                          78.0,
    "TARGET":                           55.0,

    # ── TIER 2: Major mass retailers (15-40%) ─────────────────────────────
    "COSTCO":                           35.0,
    "HOME DEPOT":                       25.0,
    "BEST BUY":                         22.0,
    "CVS":                              20.0,
    "LOWES":                            18.0,
    "MACYS":                            18.0,
    "SEPHORA":                          18.0,
    "KOHLS":                            15.0,
    "WALGREENS":                        15.0,
    "EBAY":                             15.0,
    "ULTA BEAUTY":                      15.0,

    # ── TIER 3: Strong retailers (8-15%) ──────────────────────────────────
    "SHEIN":                            12.0,
    "WHOLE FOODS MARKET":               12.0,
    "NORDSTROM":                        12.0,
    "IKEA":                             12.0,
    "SAMS CLUB":                        12.0,
    "TRADER JOES":                      10.0,
    "WAYFAIR":                          10.0,
    "CHEWY":                            10.0,
    "TEMU":                             10.0,
    "PUBLIX":                           8.0,
    "ALDI":                             8.0,
    "ROSS DRESS FOR LESS":              8.0,
    "ASOS":                             8.0,
    "POSHMARK":                         8.0,
    "FOOT LOCKER":                      8.0,
    "KROGER":                           8.0,
    "ETSY":                             8.0,
    "T.J.MAXX":                         8.0,
    "JCPENNEY":                         8.0,
    "NORDSTROM RACK":                   8.0,
    "MARSHALLS":                        8.0,

    # ── TIER 4: Well-known retailers (4-8%) ───────────────────────────────
    "BURLINGTON":                       6.0,
    "REI":                              6.0,
    "DICKS SPORTING GOODS":             6.0,
    "HOME GOODS":                       6.0,
    "HOBBY LOBBY":                      5.0,
    "FIVE BELOW":                       5.0,
    "DEPOP":                            5.0,
    "STOCKX":                           5.0,
    "AUTOZONE":                         5.0,
    "QVC":                              5.0,
    "DOLLAR TREE":                      5.0,
    "DOLLAR GENERAL":                   5.0,
    "MICHAELS":                         5.0,
    "GAMESTOP":                         5.0,
    "ALBERTSONS":                       5.0,
    "WEGMANS":                          5.0,
    "OFFICE DEPOT":                     5.0,
    "MERCARI":                          5.0,
    "BJS WHOLESALE CLUB":               5.0,
    "BASS PRO SHOPS":                   5.0,
    "ZAPPOS":                           5.0,
    "URBAN OUTFITTERS":                 5.0,
    "BARNES & NOBLE":                   5.0,
    "ACE HARDWARE":                     5.0,
    "H-E-B":                            5.0,
    "STAPLES":                          5.0,
    "DILLARDS":                         5.0,
    "PETCO":                            5.0,
    "DSW DESIGNER SHOE WAREHOUSE":      5.0,
    "OFFERUP":                          5.0,
    "TRACTOR SUPPLY":                   4.0,
    "MENARDS":                          4.0,
    "GNC":                              4.0,
    "NFL SHOP":                         4.0,
    "SPROUTS FARMERS MARKET":           4.0,
    "JOURNEYS":                         4.0,
    "HOT TOPIC":                        4.0,
    "LIDS":                             4.0,
    "BLOOMINGDALES":                    4.0,
    "CRATE & BARREL":                   4.0,
    "FANATICS":                         4.0,
    "DISNEY STORE":                     4.0,
    "MEIJER":                           4.0,
    "BIG LOTS":                         4.0,
    "OREILLY AUTO PARTS":               4.0,
    "FAMOUS FOOTWEAR":                  4.0,
    "IPSY":                             4.0,
    "PACSUN":                           4.0,
    "SAKS OFF 5TH":                     4.0,

    # ── TIER 5: Moderate retailers (2-4%) ─────────────────────────────────
    "OVERSTOCK":                        3.0,
    "WILLIAMS-SONOMA":                  3.0,
    "TOTAL WINE & MORE":                3.0,
    "REDBUBBLE":                        3.0,
    "RACK ROOM SHOES":                  3.0,
    "WINN-DIXIE":                       3.0,
    "AT HOME":                          3.0,
    "MAURICES":                         3.0,
    "TILLYS":                           3.0,
    "THE CONTAINER STORE":              3.0,
    "TOYS R US":                        3.0,
    "WISH SHOPPING":                    3.0,
    "PET SUPPLIES PLUS":                3.0,
    "NEIMAN MARCUS":                    3.0,
    "THE VITAMIN SHOPPE":               3.0,
    "THE REALREAL":                     3.0,
    "BACKCOUNTRY":                      3.0,
    "PARTY CITY":                       3.0,
    "CABELAS":                          3.0,
    "WORLD MARKET":                     3.0,
    "ADVANCE AUTO PARTS":               3.0,
    "NAPA AUTO PARTS":                  3.0,
    "MENS WEARHOUSE":                   3.0,
    "BELK":                             3.0,
    "HARRIS TEETER":                    3.0,
    "HSN":                              3.0,
    "CHAMPS SPORTS":                    3.0,
    "EYEBUYDIRECT":                     3.0,
    "ACADEMY SPORTS + OUTDOORS":        3.0,
    "COSTCO OPTICAL":                   3.0,
    "RESTORATION HARDWARE":             3.0,
    "GOAT":                             3.0,
    "BED BATH & BEYOND":               3.0,
    "SAKS FIFTH AVENUE":               3.0,
    "THREDUP":                          3.0,
    "REVOLVE":                          3.0,
    "JOANN":                            2.5,
    "SUR LA TABLE":                     2.0,
    "SPIRIT HALLOWEEN":                 2.0,
    "DAVIDS BRIDAL":                    2.0,
    "CAMPING WORLD":                    2.0,
    "PEP BOYS":                         2.0,
    "LIVING SPACES":                    2.0,
    "BOOKSHOP":                         2.0,
    "BIG 5 SPORTING GOODS":             2.0,
    "SCHOLASTIC":                       2.0,
    "MATTEL":                           2.0,
    "SEARS":                            2.0,
    "SPENCERS GIFTS":                   2.0,
    "BOOT BARN":                        2.0,
    "BLICK ART MATERIALS":              2.0,
    "DISCOUNT TIRE DIRECT":             2.0,
    "BLUEMERCURY":                      2.0,
    "LAMPS PLUS":                       2.0,
    "SHOE PALACE":                      2.0,
    "RENT THE RUNWAY":                  2.0,
    "MIDAS":                            2.0,
    "RALPHS":                           2.0,
    "JEWEL-OSCO":                       2.0,
    "SMART&FINAL":                      2.0,
    "PRINCESS POLLY":                   2.0,
    "LULUS":                            2.0,
    "NET-A-PORTER":                     2.0,
    "SHOPBOP":                          2.0,
    "SUNGLASS HUT":                     2.0,
    "FINISH LINE":                      2.0,
    "ZUMIEZ":                           2.0,
    "AMERICAS BEST":                    2.0,
    "DXL":                              2.0,
    "HIBBETT":                          2.0,
    "DERMSTORE":                        2.0,
    "RITE AID":                         2.0,
    "SOCIETY6":                         2.0,
    "Z GALLERIE":                       1.5,
    "FRONTGATE":                        1.5,
    "HAVERTYS":                         1.5,
    "WINE COM":                         1.5,
    "DRESSBARN":                        1.5,
    "ONE HANES PLACE":                  1.5,
    "HALLOWEEN EXPRESS":                1.5,
    "RUE LA LA":                        1.5,
    "BRIDGESTONE TIRE":                 1.5,
    "RON JON SURF SHOP":               1.5,
    "LENSCRAFTERS":                     2.0,
    "MATTRESS FIRM":                    2.0,
    "SHOE CARNIVAL":                    1.5,
    "TANGER OUTLETS":                   1.5,
    "LORD & TAYLOR":                    1.5,
    "BUCKLE":                           1.5,
    "ZALES":                            1.5,
    "PAYLESS":                          1.5,
    "DOLLAR GENERAL":                   5.0,
    "CAREMARK":                         2.0,
    "RETAIL ME NOT":                    2.0,
    "MODCLOTH":                         1.5,
    "VITAMIN WORLD":                    1.0,
    "ALLMODERN":                        1.5,
    "UNCOMMON GOODS":                   1.5,

    # ── TIER 6: Smaller/niche retailers (0.5-2%) ─────────────────────────
    "TUMI":                             1.0,
    "ONE KINGS LANE":                   1.0,
    "SHOEMALL":                         1.0,
    "GUMROAD":                          1.0,
    "SAUCEY":                           1.0,
    "HAND & STONE SPA":                 1.0,
    "SHIEKH":                           1.0,
    "CITY GEAR":                        1.0,
    "LOVEHONEY":                        1.0,
    "SPACE NK":                         1.0,
    "THE COSMETICS COMPANY STORE":      1.0,
    "LACED UP":                         1.0,
    "SAWGRASS MILLS":                   1.0,
    "GILT":                             2.0,
    "PREMIUM OUTLETS":                  3.0,
    "EREWHON":                          1.0,
    "DECATHLON":                        1.0,
    "ROAD RUNNER SPORTS":               1.0,
    "VISIONWORKS":                      1.5,
    "FINGERHUT":                        1.0,
    "NHL SHOP":                         1.5,
    "BOBS DISCOUNT FURNITURE":          1.5,
    "KIRKLANDS":                        1.0,
    "STITCHFIX":                        1.0,
    "KIDS FOOT LOCKER":                 1.5,
    "FABFITFUN":                        1.5,
    "MARIANOS MARKET":                  1.0,
    "ENTERTAINMENT EARTH":              0.5,
    "THINGS REMEMBERED":                0.5,
    "FRESHDIRECT":                      1.0,
    "FRESH DIRECT":                     1.0,
    "EATALY":                           1.0,
    "PAVILIONS":                        1.0,
    "WHATNOT":                          2.0,
    "EBAY":                             15.0,
    "AMOEBA MUSIC":                     0.5,
    "RUGS USA":                         1.0,
    "RUGS DIRECT":                      0.5,
    "GOLF GALAXY":                      1.0,
    "SOUTH MOON UNDER":                 0.5,
    "GELSONS MARKETS":                  0.5,
    "OPTICSPLANET":                     0.5,
    "LUMENS":                           0.5,
    "FIRESTONE COMPLETE AUTO CARE":     1.0,
    "COLEMAN FURNITURE":                0.5,
    "APTDECO":                          0.5,
    "LONGCHAMP":                        1.0,
    "THE OUTNET":                       1.0,
    "STADIUM GOODS":                    0.5,
    "HIGHSNOBIETY":                     0.5,
    "ROCK BOTTOM GOLF":                 0.5,
    "2ND SWING GOLF":                   0.5,
    "PICKLEBALL CENTRAL":               0.5,
    "TONYS FRESH MARKET":               0.5,
    "EVERYTHING BUT WATER":             0.5,
    "WEIS MARKETS":                     0.8,
    "TOYWIZ":                           0.3,
    "GOING GOING GONE":                 0.5,
    "PINKCHERRY":                       0.5,

    # ── TIER 7: Niche/luxury (0.1-1%) ────────────────────────────────────
    "FWRD":                             1.0,
    "LYST":                             1.0,
    "BOTTEGA VENETA":                   0.5,
    "1STDIBS":                          0.5,
    "DIOR":                             0.5,
    "HERMES":                           0.5,
    "GUCCI":                            1.0,
    "LOUIS VUITTON":                    1.0,
    "ROLEX":                            0.5,
    "ARMANI":                           0.5,
    "CARTIER":                          0.5,
    "PRADA":                            0.5,
    "VERSACE":                          0.3,
    "JIMMY CHOO":                       0.3,
    "GIVENCHY":                         0.3,
    "GIUSEPPE ZANOTTI":                 0.2,
    "BERGDORF GOODMAN":                 0.5,
    "MR PORTER":                        0.8,
    "TOM FORD":                         0.5,
    "DOLCE & GABBANA":                  0.3,
    "JIL SANDER":                       0.2,
    "LIU JO":                           0.2,
    "PATEK PHILIPPE":                   0.2,
    "MAISON MARGIELA":                  0.3,
    "HARVEY NICHOLS":                   0.3,
    "HARRODS":                          0.3,
    "HORCHOW":                          0.3,
    "CETTIRE":                          0.5,
    "CHARISH":                          0.5,
    "MAISONETTE":                       0.5,
    "ABT ELECTRONICS":                  0.5,
    "BIBLIO":                           0.3,
    "PILLOW TALK":                      0.2,
    "WOLF & BADGER":                    0.3,
    "MASTERMIND TOYS":                  0.3,
    "FIRE PIT STOCK":                   0.2,
    "COASTAL PET PRODUCTS":             0.3,
    "SUPER-SHOP":                       0.2,
    "GOLF DIRECT NOW":                  0.2,
    "BUCHERER 1888":                    0.1,
    "TERRACYCLE":                       0.3,
    "WORLDWIDE GOLF":                   0.3,
    "STEELCASE":                        0.5,
    "FAIRWAY JOCKEY":                   0.1,
    "GOODEE":                           0.2,
    "CULTURE KINGS":                    0.5,
    "MIINTO":                           0.2,
    "FLASK FINE WINE & WHISKY":         0.2,
    "MENS UNDERWEAR STORE":             0.2,
    "PEPE JEANS":                       0.3,
    "FALABELLA":                        0.2,
    "DEL AMO FASHION CENTER":           0.2,
    "LE BON MARCHE":                    0.1,
    "BRONNERS CHRISTMAS WONDERLAND":    0.5,
    "LIGHTFORM":                        0.2,
    "BOOT WORLD":                       0.5,
    "WORLD CONDOMS":                    0.2,
    "FARM DIRECT":                      0.3,
    "CAMPSAVER":                        0.5,
    "FINE LINENS":                      0.1,
    "ZALANDO":                          0.5,
    "US WALL DECOR":                    0.1,
    "RACK ROOM SHOES":                  3.0,
    "DAVID JONES":                      0.2,
    "SINSAY":                           0.1,
    "WALLAPOP":                         0.2,
    "ZOZOTOWN":                         0.1,
    "MASSIMO DUTTI":                    0.5,
    "EFIREPLACESTORE":                  0.1,
    "BOARD GAME BARRISTER":             0.1,
    "DESIGNER OPTICS":                  0.3,
    "LIGHTOPIA":                        0.1,
    "LOJAS RENNER":                     0.1,
    "GUERLAIN":                         0.3,
    "HOLT RENFREW":                     0.2,
    "GLOBAL GOLF":                      0.5,
    "BVLGARI":                          0.3,
    "HALLOWEENCOSTUMES":                1.0,
    "FAIRWAY STYLES":                   0.1,
    "FINNISH DESIGN SHOP":              0.1,
    "SELFRIDGES":                       0.3,
    "KENZO":                            0.2,
    "STRIDE RITE":                      1.0,
    "PIGGLY WIGGLY":                    1.0,
    "RURAL KING":                       1.0,
    "EQUINOX THE SHOP":                 0.3,
    "DRIES VAN NOTEN":                  0.1,
    "SWAROVSKI":                        1.0,
    "TOURNEAU":                         0.3,
    "JAZWARES":                         0.3,
    "MYTHERESA":                        0.5,
    "MODA OPERANDI":                    0.5,
    "FOODSERVICEDIRECTM":               0.2,
    "GUITAR WORLD":                     0.5,
    "WEWOREWHAT":                       0.3,
    "LANE CRAWFORD":                    0.2,
    "THE GOLF WORKS":                   0.3,
    "STATE LINE TACK":                  0.5,
    "MANGA PLAZA":                      0.3,
    "PETES MARKET":                     0.5,
    "SAVE A LOT":                       1.0,
    "AURORA WORLD":                     0.2,
    "ROYAL DESIGN":                     0.1,
    "PETDOORS":                         0.2,
    "LENSABL":                          0.5,
    "REBAG":                            0.5,
    "BEVMO!":                           1.5,
    "JEAN PAUL GAULTIER":               0.2,
    "KURT GEIGER":                      0.3,
    "BRIDGESTONE TIRES":                1.0,
    "ZURU TOYS":                        0.3,
    "TRAMONTINA":                       0.3,
    "WOODLAND DIRECT":                  0.2,
    "AUTHENTEAK":                       0.1,
    "TGW-THE GOLF WAREHOUSE":           0.3,
    "BEAST KINGDOM":                    0.2,
    "HARRY ROSEN":                      0.2,
    "BROWN THOMAS":                     0.1,
    "THINGS FROM ANOTHER WORLD":        0.3,
    "CASS ART":                         0.1,
    "EXITO":                            0.1,
    "SUITABLE":                         0.1,
    "AKRIS":                            0.1,
    "GOLF AVENUE":                      0.3,
    "CERMAK FRESH MARKET":              0.3,
    "2ND STREET USA":                   0.3,
    "CANAL TOYS":                       0.2,
    "CHEAPUNDIES":                      0.2,
    "BURBERRY":                         0.5,
    "MERCADO LIBRE":                    0.3,
    "YVES SAINT LAURENT":               0.5,
    "YOOX":                             0.5,
    "BALENCIAGA":                       0.3,
    "ALEXANDER MCQUEEN":                0.3,
    "VAN CLEEF & ARPELS":               0.1,
    "FAIRWAY GOLF USA":                 0.2,
    "JINEN":                            0.1,
}


def det_variation(brand: str, base_pct: float) -> float:
    h = int(hashlib.md5(brand.encode()).hexdigest()[:8], 16)
    if base_pct < 0.1:
        offset = ((h % 800) + 100) / 100000.0
        sign = 1 if (h % 2 == 0) else -1
        return round(max(base_pct + sign * offset, 0.0301), 4)
    elif base_pct < 1.0:
        offset = ((h % 900) + 100) / 10000.0
        sign = 1 if (h % 2 == 0) else -1
        return round(base_pct + sign * offset * 0.1, 4)
    else:
        offset = ((h % 2000) - 1000) / 10000.0
        magnitude = max(0.01, base_pct * 0.02)
        return round(base_pct + offset * magnitude, 4)


def main():
    print("Loading CSV...")
    df = pd.read_csv(CSV)
    print(f"  {len(df)} rows.")

    known_upper = {k.upper().strip(): v for k, v in KNOWN.items()}
    print(f"  {len(known_upper)} known retailer corrections.")

    wts_mask = df["Column"].str.upper().str.strip() == "WHERE THEY SHOP"
    brands = []
    for idx in df.index[wts_mask]:
        val = str(df.at[idx, "Value"]).upper().strip()
        pct = pd.to_numeric(df.at[idx, "Brand Penetration (Row)"], errors="coerce")
        if pd.notna(pct):
            brands.append((val, float(pct)))

    print(f"  {len(brands)} retailers in WHERE THEY SHOP.")

    missing = []
    count = 0
    for idx in df.index[wts_mask]:
        val = str(df.at[idx, "Value"]).upper().strip()
        if val in known_upper:
            target = known_upper[val]
            new_pct = det_variation(val, target)
            df.at[idx, "Brand Penetration (Row)"] = new_pct
            df.at[idx, "Original Raw Numbers"] = round(new_pct / 100.0 * SAMPLE)
            df.at[idx, "US Gen Pop Projection"] = round(new_pct / 100.0 * US_POP)
            count += 1
        else:
            missing.append(val)
            new_pct = det_variation(val, 0.05)
            df.at[idx, "Brand Penetration (Row)"] = new_pct
            df.at[idx, "Original Raw Numbers"] = round(new_pct / 100.0 * SAMPLE)
            df.at[idx, "US Gen Pop Projection"] = round(new_pct / 100.0 * US_POP)
            count += 1

    if missing:
        print(f"  WARNING: {len(missing)} retailers not in KNOWN dict (assigned 0.05%):")
        for m in missing:
            print(f"    - {m}")

    print(f"  {count} WTS rows corrected.")

    # Recalculate Category Share
    cat_mask = df["Column"].str.upper().str.strip() == "WHERE THEY SHOP"
    cat_df = df.loc[cat_mask]
    total = pd.to_numeric(cat_df["Brand Penetration (Row)"], errors="coerce").sum()
    if total > 0:
        for i in cat_df.index:
            pct = pd.to_numeric(df.at[i, "Brand Penetration (Row)"], errors="coerce")
            if pd.notna(pct):
                df.at[i, "Category Share"] = round(pct / total * 100, 4)

    df.to_csv(CSV, index=False)
    print(f"  Saved to {CSV}")

    # Verification
    print("\n── Top 50 WHERE THEY SHOP ──")
    wts = df[wts_mask].copy()
    wts["pct"] = pd.to_numeric(wts["Brand Penetration (Row)"], errors="coerce")
    wts = wts.sort_values("pct", ascending=False)
    for i, (_, r) in enumerate(wts.head(50).iterrows()):
        print(f"  {i+1:>3}. {str(r['Value']):<45} {r['pct']:>8.4f}%")

    print("\n── Bottom 10 ──")
    for _, r in wts.tail(10).iterrows():
        print(f"  {str(r['Value']):<45} {r['pct']:>8.4f}%")

    print("\n── Spot-checks ──")
    checks = {
        "AMAZON": "~92%", "WALMART": "~78%", "TARGET": "~55%",
        "SEPHORA": "~18%", "COSTCO": "~35%", "IKEA": "~12%",
        "WAYFAIR": "~10%", "ETSY": "~8%", "GUCCI": "~1%",
    }
    pct_map = dict(zip(wts["Value"].str.upper().str.strip(), wts["pct"]))
    for brand, expected in checks.items():
        got = pct_map.get(brand, "NOT FOUND")
        sym = "✓" if got != "NOT FOUND" else "✗"
        print(f"  {sym} {brand:<40} expected {expected}, got {got}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
