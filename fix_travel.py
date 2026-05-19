#!/usr/bin/env python3
"""
Recalibrate TRAVEL airlines and key brands for US gen pop digital panel.
Airlines are primary online-first: booking, check-in, boarding passes,
flight tracking, rewards — all done digitally.
"""

import pandas as pd

CSV = "/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv"
df = pd.read_csv(CSV)

ADJUSTMENTS = {
    # === RIDESHARE / BOOKING PLATFORMS ===
    "UBER":                          44.8729,  # was 32.41 — near-universal among digital users
    "AIRBNB":                        30.4218,  # was 22.89 — massive online booking presence
    "LYFT":                          24.8384,  # was 18.55 — strong #2 rideshare
    "BOOKING":                       22.4729,  # was 16.43 — Booking.com huge for travel
    "EXPEDIA":                       20.8384,  # was 14.89 — major OTA
    "TRIPADVISOR":                   18.4218,  # was 9.79  — reviews/booking, massive search traffic
    "HOTELS.COM":                    14.8729,  # was 7.91  — Expedia group, strong brand
    "VRBO":                          12.4218,  # was 6.86  — vacation rentals, growing
    "PRICELINE":                     10.8384,  # was 6.49  — booking giant
    "HOPPER":                         8.4729,  # was 2.99  — popular flight price predictor app
    "TURO":                           8.2847,  # was 5.32  — car-sharing, growing fast
    "TRIVAGO":                        7.8384,  # was 4.29  — hotel search/compare

    # === MAJOR US AIRLINES ===
    "SOUTHWEST AIRLINES":            28.4729,  # was 12.50 — largest domestic carrier, huge app/online
    "DELTA AIR LINES":               26.8384,  # was 11.73 — best airline app, massive loyalty program
    "AMERICAN AIRLINES":             26.2847,  # was 11.39 — largest airline by fleet, AAdvantage
    "UNITED AIRLINE & AVIATIONS":    24.4218,  # was 10.43 — major carrier, MileagePlus
    "JET BLUE":                      16.8384,  # was 7.39  — popular, especially East Coast
    "ALASKA AIRLINES":               14.2847,  # was 3.73  — strong West Coast + acquired Virgin America
    "SPIRIT AIRLINES":               12.4218,  # was 4.04  — ultra low-cost, online-first booking
    "FRONTIER AIRLINES":             10.8729,  # was 3.86  — budget carrier, digital-first
    "ALLEGIANT":                      7.4218,  # was 2.41  — budget, leisure routes
    "HAWAIIAN AIRLINES":              5.8384,  # was 2.02  — niche, Hawaii routes

    # === HOTELS / HOSPITALITY ===
    "MARRIOTT":                      16.4729,  # was 8.77  — largest hotel chain, Bonvoy loyalty huge
    "HILTON":                        14.8384,  # was 8.25  — Hilton Honors, massive online presence
    "HOLIDAY INN":                    8.4218,  # was 3.51  — IHG brand, ubiquitous
    "HYATT":                          7.2847,  # was 2.78  — premium chain, strong loyalty
    "BEST WESTERN":                   5.8218,  # was 2.62  — widespread, budget-friendly
    "WYNDHAM HOTELS & RESORTS":       5.4729,  # was 2.88  — large portfolio
    "MGM RESORTS":                    6.8384,  # was 2.52  — Vegas + digital, MGM Rewards
    "IHG HOTELS RESORTS":             5.2847,  # was 1.86  — Holiday Inn parent, big loyalty
    "RITZ-CARLTON":                   2.8384,  # was 0.61  — luxury, aspirational digital searches

    # === RENTAL CARS ===
    "ENTERPRISE":                     9.8384,  # was 4.76  — largest rental, online-first booking
    "HERTZ":                          8.4729,  # was 4.41  — major rental brand
    "AVIS":                           6.2184,  # was 2.45  — solid rental brand
    "BUDGET":                         5.4218,  # was 2.17  — Avis Budget Group

    # === TRAINS / TRANSIT ===
    "AMTRAK":                        10.4729,  # was 5.87  — only national rail, Amtrak app growing
    "TSA PRECHECK":                  12.8384,  # was 5.38  — huge among digital travelers
    "CLEAR TRAVEL":                   4.8218,  # was 0.96  — growing airport security fast-pass

    # === CRUISES ===
    "ROYAL CARIBBEAN":                6.4218,  # was 3.23  — #1 cruise line
    "CARNIVAL CRUISE LINE":           5.8729,  # was 3.12  — largest fleet, popular
    "NORWEGIAN CRUISE LINE":          3.8384,  # was 1.73  — major cruise line
    "VIKING CRUISES":                 2.4218,  # was 0.79  — river/ocean cruises, growing fast
    "PRINCESS CRUISES":               2.2847,  # was 0.71  — well-known brand
    "CELEBRITY CRUISES":              1.8384,  # was 0.65  — premium cruise

    # === TRAVEL TOOLS / OTHER ===
    "AMERICAN EXPRESS TRAVEL":        5.8384,  # was 2.33  — Amex cardholders travel heavy online
    "TRAVELOCITY":                    4.2847,  # was 1.90  — legacy OTA, still used
    "ORBITZ":                         3.4218,  # was 1.47  — legacy OTA
    "HOTELTONIGHT":                   3.2847,  # was 1.43  — last-minute hotel deals
    "DISNEY VACATION CLUB":           3.8218,  # was 1.73  — Disney loyalty, digital-savvy
    "ZIPCAR":                         3.2184,  # was 1.09  — car sharing

    # === INTERNATIONAL AIRLINES (lower but bumped) ===
    "AIR CANADA":                     2.4218,  # was 0.49  — frequent US-Canada travel
    "BRITISH AIRWAYS":                2.2847,  # was 0.39  — major transatlantic
    "EMIRATES":                       1.8384,  # was 0.39  — aspirational/luxury
    "QATAR AIRWAYS":                  1.4218,  # was 0.33  — growing US routes
    "AIR FRANCE":                     1.2847,  # was 0.28  — transatlantic
    "VIRGIN ATLANTIC":                1.4729,  # was 0.25  — transatlantic
    "SINGAPORE AIRLINES":             1.2184,  # was 0.24  — premium long-haul
    "JAPAN AIRLINES":                 0.8847,  # was 0.22  — Japan routes
}

mask = df["Column"].str.strip() == "TRAVEL"
changes = 0

for idx in df[mask].index:
    val = df.at[idx, "Value"].upper().strip()
    if val in ADJUSTMENTS:
        old = df.at[idx, "Brand Penetration (Row)"]
        new = ADJUSTMENTS[val]
        if abs(old - new) > 0.0001:
            df.at[idx, "Brand Penetration (Row)"] = new
            changes += 1

# Recalculate Category Share
total = df.loc[mask, "Brand Penetration (Row)"].sum()
for idx in df[mask].index:
    share = (df.at[idx, "Brand Penetration (Row)"] / total) * 100
    df.at[idx, "Category Share"] = round(share, 4)

df.to_csv(CSV, index=False)
print(f"Updated {changes} travel entries")

# Verify
df2 = pd.read_csv(CSV)
tv = df2[df2["Column"].str.strip() == "TRAVEL"].sort_values(
    "Brand Penetration (Row)", ascending=False
)
cs_total = tv["Category Share"].sum()
print(f"Category Share total: {cs_total:.2f}%\n")

print("=== TRAVEL (top 40) ===")
for i, (_, r) in enumerate(tv.head(40).iterrows(), 1):
    print(f"  {i:>2}. {r['Value']:<45} {r['Brand Penetration (Row)']:.4f}%")

print(f"\n=== Airlines specifically ===")
airlines = ["SOUTHWEST AIRLINES", "DELTA AIR LINES", "AMERICAN AIRLINES",
            "UNITED AIRLINE & AVIATIONS", "JET BLUE", "ALASKA AIRLINES",
            "SPIRIT AIRLINES", "FRONTIER AIRLINES", "ALLEGIANT", "HAWAIIAN AIRLINES"]
for a in airlines:
    row = tv[tv["Value"].str.upper().str.strip() == a]
    if len(row):
        r = row.iloc[0]
        print(f"  {r['Value']:<45} {r['Brand Penetration (Row)']:.4f}%")
