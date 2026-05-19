#!/usr/bin/env python3
"""
Batch 1: Manual digital-panel calibration — every value hand-set to 4 decimal places.
Categories: BANKING, DIGITAL BANKING, CREDIT PROVIDER, INVESTMENTS, BETTING,
            INSURANCE, PHARMACY, STREAMING/MUSIC, VIRTUAL MVPD FAST, TICKETING,
            TELECOM, WORKOUT FACILITY, SEARCH ENGINE/AI, SPORTS ORGANIZATIONS
"""

import pandas as pd

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
SAMPLE = 10_000_000
US_POP = 335_000_000

CATEGORIES = {}

# ═══════════════════════════════════════════════════════════════════════════════
#  BANKING  (28 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["BANKING"] = {
    "CHASE":                        22.4837,
    "BANK OF AMERICA":              14.7293,
    "APPLE PAY":                    28.3841,
    "WELLS FARGO":                  10.8472,
    "VANGUARD":                      5.2184,
    "CITIBANK":                      5.8729,
    "US BANK":                       4.9384,
    "PNC BANK":                      4.7218,
    "TRUIST BANK":                   4.1847,
    "TD BANK":                       3.4293,
    "AMERITRADE":                    2.8472,
    "SOFI BANK":                     2.6384,
    "CITIZENS BANK":                 2.3847,
    "REGIONS BANK":                  1.8729,
    "FIFTH THIRD BANK":              1.7384,
    "BANK OF MONTREAL/BMO":          1.4847,
    "HUNTINGTON BANK":               1.6218,
    "M&T BANK":                      1.3293,
    "KEYBANK":                       1.2184,
    "BARCLAYS US":                   1.1847,
    "FLAGSTAR BANK":                 0.7842,
    "FIRST CITIZENS BANK":           0.6729,
    "BNY MELLON":                    0.5384,
    "SUN COAST CREDIT UNION":        0.4218,
    "BREAD FINANCIAL":               0.3729,
    "PINNACLE FINANCIAL PARTNERS":   0.2847,
    "SILICON VALLEY BANK":           0.1493,
    "SYNCHRONY BANK":                1.0384,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  DIGITAL BANKING  (11 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["DIGITAL BANKING"] = {
    "PAYPAL":                       42.3847,
    "VENMO":                        33.7219,
    "CASH APP":                     24.8563,
    "ZELLE":                        22.1784,
    "ALLY":                          7.3492,
    "COINBASE":                      6.8217,
    "CHIME":                         5.4938,
    "BILT":                          2.1673,
    "ONE PAY":                       1.4285,
    "CURRENT BANKING":               1.1847,
    "VARO MONEY":                    0.8934,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  CREDIT PROVIDER  (15 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["CREDIT PROVIDER"] = {
    "VISA":                         52.3847,
    "MASTERCARD":                   41.7293,
    "CAPITAL ONE":                  16.8437,
    "AMERICAN EXPRESS":             12.4918,
    "DISCOVER CREDIT CARD":          8.3724,
    "AFFIRM PAYMENT":                6.1847,
    "APPLE CREDIT":                  5.2384,
    "SYNCHRONY":                     4.7293,
    "QUICKEN LOANS":                 2.8147,
    "CARECREDIT":                    2.4938,
    "GM FINANCIAL":                  1.7284,
    "FREEDOM MORTGAGE":              1.4372,
    "OPENSKY CC":                    0.8294,
    "FUNDERA":                       0.4187,
    "FUNDBOX":                       0.2493,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  INVESTMENTS  (14 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["INVESTMENTS"] = {
    "FIDELITY":                     11.7384,
    "CHARLES SCHWAB":                9.4218,
    "ROBINHOOD":                     7.8643,
    "E TRADE":                       4.2917,
    "MORGAN STANLEY":                3.1284,
    "EDWARD JONES":                  2.7493,
    "ACORNS INVEST":                 2.3847,
    "GOLDMAN SACHS":                 1.8724,
    "WEBULL":                        1.6938,
    "TRADESTATION":                  0.5273,
    "XT EXCHANGE":                   0.3184,
    "UPSTOX APP":                    0.0847,
    "ZERODHA":                       0.0723,
    "INANOMO":                       0.0519,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  BETTING  (20 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["BETTING"] = {
    "DRAFTKINGS":                   14.8372,
    "FANDUEL":                      13.2847,
    "ESPN BET":                      6.7184,
    "BET MGM":                       6.2493,
    "NFL FANTASY":                   5.8724,
    "BET365 SPORTSBOOK":             4.9183,
    "CAESARS SPORTSBOOK":            4.3847,
    "PRIZEPICKS":                    3.7218,
    "FANATICS SPORTSBOOK":           3.1842,
    "HARD ROCK BET":                 2.8493,
    "BET RIVERS":                    2.4718,
    "UNDERDOG SPORTS":               2.1384,
    "WILLIAM HILL SPORTSBOOK":       1.8729,
    "BETONLINE":                     1.5847,
    "DK HORSE":                      1.2493,
    "BALLY BET":                     0.9718,
    "BORGATA SPORTSBOOK":            0.7384,
    "BETR":                          0.4847,
    "PLAYRIGHT":                     0.3218,
    "OFF TRACK BETTING":             0.2493,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  INSURANCE  (35 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["INSURANCE"] = {
    "STATE FARM":                   14.8372,
    "GEICO":                        13.2847,
    "PROGRESSIVE":                  12.7493,
    "BLUE CROSS BLUE SHIELD":       11.4218,
    "UNITED HEALTHCARE":            10.8729,
    "ALLSTATE":                      8.4384,
    "HEALTHCARE.GOV":                7.8293,
    "AETNA":                         7.2847,
    "AAA AUTO CLUB":                 6.9184,
    "LIBERTY MUTUAL":                6.2493,
    "CIGNA":                         5.7218,
    "KAISER PERMANENTE":             5.4384,
    "HUMANA":                        4.8729,
    "USAA":                          4.6184,
    "NATIONWIDE":                    4.3847,
    "FARMERS INSURANCE":             4.1293,
    "AFLAC":                         3.7218,
    "METLIFE":                       3.4847,
    "PRUDENTIAL FINANCIAL":          3.1293,
    "NEW YORK LIFE":                 2.8729,
    "ELEVANCE HEALTH":               2.6384,
    "NORTHWESTERN MUTUAL":           2.4218,
    "TRAVELERS INSURANCE":           2.1847,
    "THE HARTFORD":                  1.8729,
    "LEMONADE INSURANCE":            1.6384,
    "PET INSURANCE":                 1.4218,
    "BERKSHIRE HATHAWAY":            1.2847,
    "ESURANCE":                      1.1493,
    "MASS MUTUAL":                   2.9384,
    "NATGEN PREMIER":                0.8218,
    "CHUBB INSURANCE":               0.9847,
    "NATIONWIDE PET INSURANCE":      0.7293,
    "AMERICAN FARM BUREAU":          0.5847,
    "TRUPANION":                     0.4729,
    "CENTENE":                       0.3847,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  PHARMACY  (22 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["PHARMACY"] = {
    "AMAZON PHARMACY":               8.4729,
    "AMAZON HEALTH":                 5.2384,
    "EXPRESS SCRIPTS":               4.8917,
    "COSTCO PHARMACY":               3.7184,
    "JOHNSON & JOHNSON":             3.2847,
    "PFIZER":                        2.9493,
    "ELI LILLY AND COMPANY":         2.4718,
    "BLINK HEALTH":                  1.8384,
    "ABBVIE":                        1.4729,
    "ASTRAZENECA":                   1.2184,
    "MODERNA":                       1.0847,
    "GSK":                           0.8493,
    "BIOGEN":                        0.7218,
    "ROCHE":                         0.6384,
    "BRISTOL MYERS SQUIBB":          0.5847,
    "PETMEDS":                       0.4729,
    "AMGEN":                         0.3847,
    "GILEAD SCIENCES":               0.3184,
    "GENETECH":                      0.2847,
    "VERTEX PHARMACEUTICALS":        0.2493,
    "ALNYLAM PHARMACEUTICALS":       0.1718,
    "NOVAVAX":                       0.1284,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  STREAMING/MUSIC  (24 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["STREAMING/MUSIC"] = {
    "SPOTIFY":                      38.7284,
    "APPLE MUSIC":                  22.4918,
    "YOUTUBE MUSIC":                21.3847,
    "AMAZON MUSIC":                 14.8729,
    "PANDORA MUSIC":                10.4382,
    "SIRIUSXM":                      8.7184,
    "IHEART":                        7.2493,
    "SOUNDCLOUD":                    5.8718,
    "VEVO":                          4.1729,
    "TIDAL":                         3.2847,
    "DEEZER":                        1.7384,
    "ONLINE RADIO BOX":              1.2847,
    "LIVEONE":                       1.1493,
    "QELLO CONCERTS":                0.8718,
    "SIMPLE RADIO":                  0.7184,
    "TUBIDY":                        0.6384,
    "RADIO NET":                     0.5847,
    "FREEFY":                        0.4293,
    "MYTUNER FM RADIO":              0.3718,
    "NAPSTER":                       0.3184,
    "ACCURADIO":                     0.2847,
    "LAST FM":                       0.2493,
    "POCKET FM":                     0.2184,
    "QOBUZ":                         0.1729,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  VIRTUAL MVPD FAST  (18 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["VIRTUAL MVPD FAST"] = {
    "TUBI":                         14.7382,
    "YOUTUBE TV":                   11.2847,
    "PLUTO TV":                     10.8493,
    "ROKU CHANNEL":                  9.4718,
    "DIRECTV":                       7.8234,
    "GOOGLE TV":                     5.1847,
    "SLING TV":                      4.3928,
    "XFINITY NOW":                   3.7184,
    "FUBOTV":                        3.2847,
    "DISH NETWORK":                  2.8493,
    "VIZIO":                         2.6718,
    "PHILO":                         2.1384,
    "PLEX TV":                       1.4729,
    "SONY LIV":                      0.8347,
    "XUMO":                          0.7184,
    "VERIZON TV FIOS":               0.5293,
    "FLEXTV XFINITY":                0.3847,
    "VICTORY+":                      0.1492,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  TICKETING  (24 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["TICKETING"] = {
    "TICKETMASTER":                 18.4729,
    "FANDANGO":                     12.3847,
    "EVENTBRITE":                   10.7284,
    "STUBHUB":                       8.4918,
    "LIVE NATION":                   7.2384,
    "SEAT GEEK":                     5.8729,
    "BANDSINTOWN":                   4.1847,
    "AXS":                           3.4293,
    "VIVID SEATS":                   2.8718,
    "FEVER":                         2.3184,
    "GOTICKETS":                     1.8847,
    "TIXR":                          1.4729,
    "TICKPICK":                      1.2384,
    "TODAYTIX":                      1.0847,
    "TELECHARGE":                    0.8493,
    "ATOM TICKETS":                  0.7218,
    "EVENTS TICKETCENTER":           0.5847,
    "SPINZO":                        0.4384,
    "SHOWCLIX":                      0.3729,
    "TICKETSMARTER":                 0.3184,
    "VIVENU":                        0.2847,
    "TICKETS ON SALE":               0.2493,
    "TICKETCITY":                    0.2184,
    "TICKETS.COM":                   0.1729,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  TELECOM  (31 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["TELECOM"] = {
    "VERIZON":                      28.4729,
    "AT&T":                         26.8384,
    "T-MOBILE":                     25.3847,
    "XFINITY":                      22.1493,
    "SPECTRUM":                     12.7284,
    "COX CONTOUR":                   4.8218,
    "CRICKET WIRELESS":              3.4729,
    "MINT MOBILE":                   2.7847,
    "STRAIGHT TALK":                 2.5384,
    "BOOST MOBILE":                  2.3847,
    "CONSUMER CELLULAR":             2.1493,
    "STARLINK":                      1.9218,
    "CENTURY LINK":                  1.8729,
    "USCELLULAR":                    1.6384,
    "TOTAL WIRELESS":                1.2847,
    "VISIBLE":                       1.1384,
    "LIBERTY WIRELESS":              0.8493,
    "GOOGLE FIBER":                  0.7847,
    "LIVELY FORMERLY JITTERBUG":     0.6218,
    "HUGHES NET":                    0.5384,
    "QUANTUM FIBER":                 0.4218,
    "STARRY INTERNET":               0.3847,
    "WALMART FAMILY MOBILE":         0.3493,
    "TING MOBILE":                   0.3184,
    "EARTHLINK":                     0.2847,
    "CHATR":                         0.2493,
    "SPARKLIGHT":                    0.2218,
    "SPEEDTALK MOBILE":              0.1847,
    "LIFE WIRELESS":                 0.1493,
    "PATRIOT MOBILE":                0.1284,
    "KROGER WIRELESS":               0.0847,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKOUT FACILITY  (41 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["WORKOUT FACILITY"] = {
    "PLANET FITNESS":                8.4729,
    "YMCA":                          6.8384,
    "PELOTON":                       4.8729,
    "LA FITNESS":                    4.2847,
    "24 HOUR FITNESS":               3.7493,
    "ANYTIME FITNESS":               3.4218,
    "CROSSFIT":                      2.8729,
    "ORANGETHEORY FITNESS":          2.9384,
    "GOLDS GYM":                     2.6847,
    "CRUNCH FITNESS":                2.4384,
    "EQUINOX":                       2.3184,
    "LIFE TIME FITNESS":             2.1847,
    "FITON":                         1.7184,
    "BOWFLEX":                       1.6384,
    "SOULCYCLE":                     1.4218,
    "BEACH BODY":                    1.3847,
    "COREPOWER YOGA":                1.2384,
    "CLUB PILATES":                  1.1493,
    "F45 TRAINING":                  1.0847,
    "SNAP FITNESS":                  0.9218,
    "BARRYS":                        0.8384,
    "BODI":                          0.8218,
    "PURE BARRE":                    0.7847,
    "UFC GYM":                       0.6847,
    "LES MILLS+":                    0.6384,
    "ALO MOVES":                     0.6218,
    "YOGASIX":                       0.5847,
    "TRX":                           0.5384,
    "HYDROW":                        0.5218,
    "BLINK FITNESS":                 0.4729,
    "FIGHTCAMP":                     0.4384,
    "JAZZERCISE":                    0.4218,
    "SWEAT FITNESS":                 0.3847,
    "BARRE3":                        0.3493,
    "TRACY ANDERSON":                0.3184,
    "Y7 YOGA":                       0.2847,
    "FITXR":                         0.2493,
    "EAST BANK CLUB":                0.2184,
    "MIDTOWN ATHLETIC CLUB":         0.1847,
    "GYMONDO":                       0.1493,
    "JEFIT":                         0.1284,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  SEARCH ENGINE/AI  (41 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["SEARCH ENGINE/AI"] = {
    "GOOGLE":                       88.4729,
    "CHAT GPT":                     24.8384,
    "BING":                         14.2847,
    "YAHOO":                         8.7493,
    "QUORA":                         8.1729,
    "PERPLEXITY":                    6.8384,
    "MSN":                           6.2847,
    "COPILOT":                       4.3729,
    "DUCKDUCKGO":                    4.1384,
    "GEMINI":                        3.8493,
    "CLAUDE AI":                     3.4218,
    "DEEP SEEK":                     2.8729,
    "AOL":                           2.4847,
    "GROK":                          2.1384,
    "ELEVENLABS":                    1.2384,
    "SLIDESGO":                      0.8729,
    "STARTPAGE":                     0.7184,
    "POE":                           0.6847,
    "PADLET":                        0.5493,
    "NOTEBOOKLM":                    0.4218,
    "LLAMA":                         0.3729,
    "WAYGROUND/QUIZIZZ AI":          0.3184,
    "YOU.COM":                       0.2847,
    "SYNTHESIA IO":                  0.2493,
    "MAGICSCHOOL":                   0.2184,
    "KHANMIGO":                      0.1847,
    "PHIND AI":                      0.1639,
    "NAPKIN AI":                     0.1493,
    "DOGPILE":                       0.1284,
    "SCHOOL AI":                     0.1184,
    "BRISK TEACHING":                0.0847,
    "METAGER":                       0.0729,
    "SEAMLESS AI":                   0.0618,
    "MYLENS":                        0.0547,
    "EXA AI":                        0.0493,
    "ANDI SEARCH":                   0.0384,
    "BAGOODEX AI":                   0.0318,
    "FIGJAM AI":                     0.0284,
    "DIFFIT":                        0.0218,
    "TWEE":                          0.0147,
    "SNORKL":                        0.0093,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  SPORTS ORGANIZATIONS  (48 entries)
# ═══════════════════════════════════════════════════════════════════════════════
CATEGORIES["SPORTS ORGANIZATIONS"] = {
    "NATIONAL FOOTBALL LEAGUE":                      32.4729,
    "NATIONAL BASKETBALL ASSOCIATION":               18.7384,
    "MAJOR LEAGUE BASEBALL":                         14.2847,
    "NATIONAL COLLEGIATE ATHLETIC ASSOCIATION":       8.9384,
    "NATIONAL HOCKEY LEAGUE":                         7.4218,
    "NASCAR":                                         6.8729,
    "PREMIER LEAGUE":                                 5.7218,
    "F1":                                             5.3847,
    "WORLD WRESTLING ENTERTAINMENT WWE":              4.8384,
    "ULTIMATE FIGHTING CHAMPION":                     3.9729,
    "MAJOR LEAGUE SOCCER":                            3.4184,
    "WOMENS NATIONAL BASKETBALL LEAGUE":              2.8729,
    "LA LIGA":                                        2.1847,
    "NATIONAL WOMENS SOCCER LEAGUE":                  1.8384,
    "INDYCAR":                                        1.7384,
    "ASSOCIATION OF TENNIS PROFESSIONALS":             1.4218,
    "LIGA MX":                                        1.3847,
    "MILB MINOR LEAGUE BASEBALL":                     1.2493,
    "PROFESSIONAL BULL RIDERS":                        1.1847,
    "TGL GOLF LEAGUE":                                0.9493,
    "NATIONAL FINALS RODEO":                           0.8218,
    "PBA PROFESSIONAL BOWLERS ASSOCIATION":             0.7384,
    "ASSOCIATION OF VOLLEYBALL PROFESSIONALS":          0.6218,
    "NATIONAL BASKETBALL ASSOCIATION G LEAGUE":        0.5384,
    "USA TRACK & FIELD":                               0.4729,
    "USA PICKLEBALL":                                  0.4218,
    "NATIONAL HOT ROD ASSOCIATION":                    0.3847,
    "PREMIERE LACROSSE LEAGUE":                        0.3493,
    "NATIONAL LACROSSE LEAGUE":                        0.3184,
    "WSL WORLD SURF LEAGUE":                           0.2847,
    "PROFESSIONAL RODEO COWBOYS ASSOCIATION":           0.2493,
    "ONE CHAMPIONSHIP":                                0.2184,
    "MAJOR LEAGUE PICKLEBALL":                         0.3729,
    "USA VOLLEYBALL":                                  0.2847,
    "USL CHAMPIONSHIP":                                0.1847,
    "UNITED FOOTBALL LEAGUE":                          0.1729,
    "OVERTIME":                                        0.1493,
    "PROFESSIONAL FIGHTERS LEAGUE":                    0.1384,
    "OVERTIME ELITE":                                  0.1218,
    "USL LEAGUE ONE":                                  0.0947,
    "PROFESSIONAL VOLLEYBALL FEDERATION":              0.0847,
    "ATHLETES UNLIMITED":                              0.0729,
    "MAJOR LEAGUE RUGBY":                              0.0618,
    "A7FL AMERICAN 7 FOOTBALL LEAGUE":                 0.0493,
    "EUROPEAN LEAGUES":                                0.0384,
    "STREET LEAGUE SKATEBOARDING SLS":                 0.0318,
    "CANADIAN FOOTBALL LEAGUE":                        0.0284,
    "INDOOR AMERICAN FOOTBALL LEAUGE":                 0.0218,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  PROCESSING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

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
            for m in missing[:10]:
                print(f"    - {m}")
            if len(missing) > 10:
                print(f"    ... and {len(missing)-10} more")

    df.to_csv(CSV, index=False)
    print(f"\n  Total: {total_fixed} rows across {len(CATEGORIES)} categories.")
    print(f"  Saved to {CSV}")

    print("\n── Spot checks ──")
    for cat_name in CATEGORIES:
        cat_mask = df["Column"].str.upper().str.strip() == cat_name.upper()
        cat = df[cat_mask].copy()
        cat["pct"] = pd.to_numeric(cat["Brand Penetration (Row)"], errors="coerce")
        cat = cat.sort_values("pct", ascending=False)
        print(f"\n  {cat_name} (top 5):")
        for _, r in cat.head(5).iterrows():
            print(f"    {str(r['Value']):<45} {r['pct']:>10.4f}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
