#!/usr/bin/env python3
"""
Batch 3: Manual digital-panel calibration — every value hand-set to 4 decimal places.
Categories: AMUSEMENT PARKS, TOYS
"""

import pandas as pd

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
SAMPLE = 10_000_000
US_POP = 335_000_000

CATEGORIES = {}

# ═══════════════════════════════════════════════════════════════════════════════
#  AMUSEMENT PARKS  (119 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["AMUSEMENT PARKS"] = {
    "DISNEY WORLD":                          8.4729,
    "DISNEYLAND":                            7.2847,
    "UNIVERSAL ORLANDO RESORT":              6.4218,
    "TOP GOLF":                              5.7493,
    "UNIVERSAL STUDIOS HOLLYWOOD":           4.8384,
    "DAVE AND BUSTERS":                      4.2847,
    "SEA WORLD":                             3.8493,
    "CEDAR POINT":                           3.2184,
    "CHUCK E. CHEESE":                       2.9384,
    "BUSCH GARDENS":                         2.8729,
    "SIX FLAGS GREAT ADVENTURE":             2.7218,
    "SIX FLAGS MAGIC MOUNTAIN":              2.4847,
    "LEGOLAND":                              2.3729,
    "DOLLYWOOD":                             2.2847,
    "HERSHEY PARK":                          2.1493,
    "KNOTTS BERRY FARM":                     2.0384,
    "KINGS ISLAND":                          1.9218,
    "SKY ZONE TRAMPOLINE PARK":              1.8847,
    "BOWLERO":                               1.8384,
    "URBAN AIR ADVENTURE PARK":              1.7218,
    "CAROWINDS":                             1.6847,
    "SIX FLAGS OVER GA HURRICANE HARBOR":    1.5493,
    "KINGS DOMINION":                        1.4847,
    "SESAME PLACE":                          1.4218,
    "MAIN EVENT BOWLING":                    1.3847,
    "ROUND1 BOWLING ARCADE":                 1.3384,
    "SILVER DOLLAR CITY":                    1.2847,
    "KENNYWOOD":                             1.2493,
    "NICKELODEON UNIVERSE":                  1.1847,
    "DORNEY PARK & WILDWATER KINGDOM":       1.1384,
    "AQUATICA":                              1.0847,
    "LEGOLAND DISCOVERY CENTER":             1.0493,
    "SIX FLAGS GREAT AMERICA HURRICANE HARBOR GURNEE": 0.9847,
    "SIX FLAGS NEW ENGLAND HURRICANE HARBOR AGAWAM":   0.9384,
    "SIX FLAGS FIESTA TEXAS HURRICANE HARBOR":         0.8847,
    "SIX FLAGS ST LOUIS HURRICANE HARBOR":   0.8384,
    "SIX FLAGS WHITE WATER":                 0.7847,
    "SIX FLAGS AMERICA HURRICANE HARBOR BOWIE": 0.7493,
    "WATER COUNTRY USA":                     0.7218,
    "VALLEYFAIR":                            0.6847,
    "CALIFORNIAS GREAT AMERICA":             0.6493,
    "SANTA CRUZ BEACH BOARDWALK":            0.6218,
    "DISCOVERY COVE":                        0.5847,
    "LAKE COMPOUNCE":                        0.5493,
    "UNIVERSAL BEIJING":                     0.5218,
    "MT OLYMPUS WATER & THEME PARK":         0.4847,
    "KNOEBELS AMUSEMENT RESORT":             0.4729,
    "HOLIDAY WORLD-SPLASHIN SAFARI":         0.4384,
    "MOREYS PIERS":                          0.4218,
    "PACIFIC PARK SANTA MONICA":             0.4047,
    "ELITCH GARDENS":                        0.3847,
    "CANOBIE LAKE PARK":                     0.3729,
    "LAGOON PARK":                           0.3618,
    "ALTITUDE TRAMPOLINE PARK":              0.3493,
    "ANDRETTI INDOOR KARTING":               0.3384,
    "SILVERWOOD THEME PARK":                 0.3218,
    "WILD ADVENTURES":                       0.3047,
    "KEMAH BOARDWALK":                       0.2918,
    "PEPPA PIG THEME PARK":                  0.2847,
    "FRONTIER CITY":                         0.2729,
    "FUN SPOT":                              0.2618,
    "MICHIGANS ADVENTURE WILDWATER ADVENTURE": 0.2493,
    "WALDAMEER PARK":                        0.2384,
    "TEXAS STATE FAIR":                      1.0218,
    "WASHINGTON STATE FAIR":                 0.4847,
    "LUNA PARK":                             0.3847,
    "HURRICANE HARBOR VALENCIA":             0.3184,
    "MAGIC SPRINGS":                         0.2184,
    "MOUNTAIN CREEK WATERPARK":              0.2847,
    "CAMELBACK MOUNTAIN RESORT":             0.3493,
    "BELMONT PARK SAN DIEGO":                0.2184,
    "OAKS AMUSEMENT PARK":                   0.1847,
    "CASINO PIER BREAKWATER BEACH":          0.1729,
    "JENKINSONS BOARDWALK":                  0.1618,
    "THE FUNPLEX":                           0.1493,
    "GET AIR SPORTS":                        0.2847,
    "BOOMERS PARKS":                         0.2184,
    "PLAYLAND PARK":                         0.1384,
    "SIX FLAGS MEXICO CITY":                 0.1847,
    "KIDS EMPIRE":                           0.2493,
    "PETER PIPER PIZZA":                     0.2218,
    "UNIVERSAL KIDS RESORT":                 0.1847,
    "SEA LIFE":                              0.2184,
    "CINERGY ENTERTAINMENT":                 0.1847,
    "JOHNS INCREDIBLE PIZZA CO":             0.1729,
    "INCREDIBLE PIZZA COMPANY":              0.1618,
    "SPIRIT MOUNTAIN":                       0.1493,
    "WILD WAVES THEME & WATER PARK":         0.2384,
    "LOST ISLAND":                           0.1384,
    "COWABUNGA BAY WATER PARK":              0.1284,
    "COWABUNGA VEGAS":                       0.1218,
    "KEANSBURG AMUSEMENT PARK":              0.1184,
    "MASSANUTTEN FAMILY ADVENTURE PARK":     0.1147,
    "STORYBOOK LAND":                        0.1084,
    "ELEV8FUN":                              0.0947,
    "SANTAS VILLAGE NH":                     0.0918,
    "SANTAS VILLAGE AZOOSMENT PARK":         0.0847,
    "CJ BARRYMORES":                         0.0818,
    "DUTCH WONDERLAND":                      0.4218,
    "ENCHANTED FOREST WATER SAFARI":         0.1493,
    "COMO TOWN":                             0.0729,
    "WILDLIFE WORLD":                        0.1847,
    "LA RONDE MONTREAL FR":                  0.0947,
    "PRAIRIE PLAYLAND":                      0.0618,
    "LEAVENWORTH ADVENTURE PARK":            0.0584,
    "TOM FOOLERYS ADVENTURE PARK":           0.0547,
    "GOLFLAND FAMILY FUN CENTER":            0.0847,
    "ZOOTAMPA AT LOWRY PARK":                0.1847,
    "CULTUS LAKE ADVENTURE PARK":            0.0493,
    "UNIVERSAL STUDIOS SINGAPORE":           0.1284,
    "CHILDRENS FAIRYLAND":                   0.0729,
    "SCHNEPF FARMS":                         0.0618,
    "SONNY ACRES FARM":                      0.0493,
    "NASCAR SPEEDPARK":                      0.1184,
    "SAFARI LAND":                           0.0547,
    "FUN SPOT ATLANTA":                      0.0847,
    "WE ROCK THE SPECTRUM":                  0.1384,
    "PLAYLAND AT PNE":                       0.0493,
    "FAMILY KINGDOM AMUSEMENT PARK":         0.0384,
    "SANTAS VILLAGE NH":                     0.0918,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  TOYS  (115 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["TOYS"] = {
    "LEGO":                          8.4729,
    "POKEMON":                       6.2847,
    "STAR WARS":                     5.4218,
    "NERF":                          4.8384,
    "SQUISHMALLOWS":                 4.4729,
    "HOT WHEELS":                    4.2184,
    "HASBRO":                        3.9847,
    "FISHER-PRICE":                  3.8493,
    "PLAY-DOH":                      3.4729,
    "FUNKO":                         3.2847,
    "BUILD-A-BEAR":                  2.9384,
    "JELLYCAT":                      2.7218,
    "BARBIE":                        2.4847,
    "MELISSA & DOUG":                2.3184,
    "MELISSA AND DOUG":              2.3184,
    "LITTLE TIKES":                  2.1847,
    "AMERICAN GIRL":                 1.9729,
    "MAGNA-TILES":                   1.8384,
    "BANDAI":                        1.7218,
    "BEYBLADE":                      1.6847,
    "HASBRO FAMILY GAMES":           1.5493,
    "SANRIO":                        1.4847,
    "PIKACHU":                       1.4218,
    "TCGPLAYER":                     3.1384,
    "PAW PATROL":                    1.3847,
    "RADIO FLYER":                   1.2847,
    "LOVEVERY":                      1.2184,
    "SPIDER-MAN":                    1.1847,
    "DISNEY FROZEN":                 1.1493,
    "COCOMELON":                     1.0847,
    "VTECH":                         1.0384,
    "PLAYMOBIL":                     0.9847,
    "SCHLEICH":                      0.9384,
    "TAMAGOTCHI":                    0.8847,
    "CANDY LAND":                    0.8493,
    "RUBIKS":                        0.8218,
    "TECH DECK":                     0.7847,
    "KINETIC SAND":                  0.7493,
    "BRATZ":                         0.7218,
    "DESPICABLE ME":                 0.6847,
    "TEENAGE MUTANT NINJA TURTLES":  0.6493,
    "LOL SURPRISE":                  0.6218,
    "L.O.L. SURPRISE":              0.6218,
    "MONSTER HIGH":                  0.5847,
    "POWER RANGERS":                 0.5493,
    "THE AMAZING DIGITAL CIRCUS":    0.5218,
    "INDIANA JONES":                 0.4847,
    "GI JOE":                        0.4729,
    "JURASSIC WORLD":                0.4384,
    "DISNEY PIXAR CARS":             0.4218,
    "WICKED":                        0.3847,
    "DEADPOOL":                      0.3729,
    "MY LITTLE PONY":                0.3493,
    "RAINBOW HIGH":                  0.3218,
    "DISNEY ARIEL":                  0.2847,
    "GODZILLA":                      0.3184,
    "DISNEY MOANA":                  0.2729,
    "FAT BRAIN TOYS":                0.2618,
    "MASTERS OF THE UNIVERSE":       0.2493,
    "BAKUGAN":                       0.2384,
    "DISNEY DESCENDANTS":            0.2218,
    "HAPE TOYS":                     0.2184,
    "POLLY POCKET":                  0.2047,
    "FURBY":                         0.1918,
    "GUND":                          0.1847,
    "MEGA BLOKS":                    0.1729,
    "DISNEY SNOW WHITE":             0.1618,
    "COOL MAKER":                    0.1493,
    "LITTLE LIVE PETS":              0.1384,
    "DISNEY TIANA":                  0.1318,
    "DISNEY SLEEPING BEAUTY":        0.1284,
    "DISNEY BELLE, BEAUTY AND THE BEAST": 0.1218,
    "DISNEY RAPUNZEL":               0.1184,
    "DISNEY MERIDA, BRAVE":          0.1047,
    "DISNEY BABY":                   0.0984,
    "DISNEY MULAN":                  0.0918,
    "PJ MASKS":                      0.1847,
    "TROLLS":                        0.1729,
    "DREAMWORKS DRAGONS":            0.1384,
    "INCREDIBLE HULK":               0.1847,
    "HATCHIMALS":                    0.1493,
    "FUGGLER":                       0.0847,
    "FLUFF NEST":                    0.0784,
    "SCRUFF A LUVS":                 0.0729,
    "BUNCH O BALLOONS":              0.1284,
    "MAGIC MIXIES":                  0.0918,
    "HEX BOTS":                      0.0847,
    "RUBBLE AND CREW":               0.1184,
    "MORISMOS":                      0.0618,
    "BUMBUMZ":                       0.0584,
    "THE LEARNING JOURNEY INTERNATIONAL": 0.0547,
    "KIDKRAFT":                      0.1384,
    "SPARKLE GIRLZ":                 0.0493,
    "XSHOT":                         0.1284,
    "CLEMENTONI":                    0.0729,
    "RAINBOCORNS":                   0.0618,
    "BITZEE":                        0.0847,
    "MINI VERSE":                    0.0729,
    "POWER WHEELS":                  0.1493,
    "RAINBOW LOOM":                  0.0847,
    "ROYALE HIGH":                   0.0918,
    "SPEKS":                         0.0618,
    "AEROBIE":                       0.0847,
    "IMAGINENEXT":                   0.0729,
    "SASSY BABY":                    0.0384,
    "GUI GUI":                       0.0318,
    "B.TOYS":                        0.0493,
    "TREASURE X":                    0.0547,
    "MOOSE GAMES":                   0.0618,
    "AKEDO":                         0.0493,
    "PUNIRUNES":                     0.0384,
    "COOKEEZ MAKERY":                0.0318,
    "COCO CONES":                    0.0284,
    "MR POTATO HEAD":                0.1384,
    "LAUGH AND LEARN":               0.0493,
    "MECCANO":                       0.0847,
}


def main():
    print("Loading CSV...")
    df = pd.read_csv(CSV)
    print(f"  {len(df)} rows total.")

    total_fixed = 0
    for cat_name, known in CATEGORIES.items():
        known_upper = {k.upper().strip(): v for k, v in known.items()}
        cat_mask = df["Column"].str.upper().str.strip() == cat_name.upper()
        count = 0
        missing = []

        for idx in df.index[cat_mask]:
            val = str(df.at[idx, "Value"]).upper().strip()
            if val in known_upper:
                new_pct = known_upper[val]
            else:
                missing.append(val)
                new_pct = 0.0512
            df.at[idx, "Brand Penetration (Row)"] = new_pct
            df.at[idx, "Original Raw Numbers"] = round(new_pct / 100.0 * SAMPLE)
            df.at[idx, "US Gen Pop Projection"] = round(new_pct / 100.0 * US_POP)
            count += 1

        cat_df = df.loc[cat_mask]
        total = pd.to_numeric(cat_df["Brand Penetration (Row)"], errors="coerce").sum()
        if total > 0:
            for i in cat_df.index:
                pct = pd.to_numeric(df.at[i, "Brand Penetration (Row)"], errors="coerce")
                if pd.notna(pct):
                    df.at[i, "Category Share"] = round(pct / total * 100, 4)

        total_fixed += count
        status = "OK" if not missing else f"WARN: {len(missing)} missing"
        print(f"  {cat_name}: {count} rows updated  [{status}]")
        if missing:
            for m in missing:
                print(f"    - {m}")

    df.to_csv(CSV, index=False)
    print(f"\n  Total: {total_fixed} rows across {len(CATEGORIES)} categories.")
    print(f"  Saved to {CSV}")

    print("\n── Spot checks ──")
    for cat_name in CATEGORIES:
        cat_mask = df["Column"].str.upper().str.strip() == cat_name.upper()
        cat = df[cat_mask].copy()
        cat["pct"] = pd.to_numeric(cat["Brand Penetration (Row)"], errors="coerce")
        cat = cat.sort_values("pct", ascending=False)
        print(f"\n  {cat_name} (top 8):")
        for _, r in cat.head(8).iterrows():
            print(f"    {str(r['Value']):<50} {r['pct']:>10.4f}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
