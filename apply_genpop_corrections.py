#!/usr/bin/env python3
"""
One-time script to apply all verified US gen-pop penetration corrections
to the Gen Pop CSV and ensure cross-category consistency.

Run:  python3 apply_genpop_corrections.py
"""

import pandas as pd

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
# SEARCH ENGINE/AI and BETTING excluded per user request.
# CBS NEWS / CBS SPORTS / CNBC / NBC SPORTS / NBC NEWS kept per user request.

CORRECTIONS: dict[tuple[str, str], float] = {

    # ══════════════════════════════════════════════════════════════════════
    # STREAMING / PLATFORM  (round 2 — niche platforms)
    # ══════════════════════════════════════════════════════════════════════
    ('STREAMING/PLATFORM', 'NOW THATS TV'):       0.5,
    ('STREAMING/PLATFORM', 'LIVETV'):             0.5,
    ('STREAMING/PLATFORM', 'BOWLTV'):             0.1,
    ('STREAMING/PLATFORM', 'PPV'):                3.0,
    ('STREAMING/PLATFORM', 'FANDANGO AT HOME'):   3.0,
    ('STREAMING/PLATFORM', 'STREMIO'):            0.5,
    ('STREAMING/PLATFORM', 'CHAUPAL'):            0.2,
    ('STREAMING/PLATFORM', 'ZEE5'):               0.5,
    ('STREAMING/PLATFORM', 'GOTHAM SPORTS'):      0.5,
    ('STREAMING/PLATFORM', 'DROPOUT TV'):         0.3,
    ('STREAMING/PLATFORM', 'CRISP SHORT FORM'):   0.1,
    ('STREAMING/PLATFORM', 'FIFA+'):              1.0,
    ('STREAMING/PLATFORM', 'VIX'):                2.0,
    ('STREAMING/PLATFORM', 'NESN 360'):           0.5,
    ('STREAMING/PLATFORM', 'ULLU'):               0.2,
    ('STREAMING/PLATFORM', 'HIDIVE'):             0.3,
    ('STREAMING/PLATFORM', 'TENNIS TV'):          0.3,
    ('STREAMING/PLATFORM', 'BYUTV'):              0.5,
    ('STREAMING/PLATFORM', 'SIGHT & SOUND TV'):   0.2,
    ('STREAMING/PLATFORM', 'OSN+'):               0.1,
    ('STREAMING/PLATFORM', 'FLOSPORTS'):          0.5,
    ('STREAMING/PLATFORM', 'KOCOWA+'):            0.3,
    ('STREAMING/PLATFORM', 'TRILLERTV'):          0.2,
    ('STREAMING/PLATFORM', 'ALLBLK'):             0.3,
    ('STREAMING/PLATFORM', 'LIVE SPORTS ON TV TODAY'): 0.2,
    ('STREAMING/PLATFORM', 'BET+'):               0.5,
    ('STREAMING/PLATFORM', 'FILMZIE'):            0.1,
    ('STREAMING/PLATFORM', 'RING OF HONOR'):      0.2,
    ('STREAMING/PLATFORM', 'CRACKLE'):            0.5,
    ('STREAMING/PLATFORM', 'FLIX LATINO'):         0.3,
    ('STREAMING/PLATFORM', 'CANELA.TV'):          0.3,
    ('STREAMING/PLATFORM', 'GOODSHORT'):          0.1,

    # ══════════════════════════════════════════════════════════════════════
    # STREAMING / MUSIC  (round 2 — niche services)
    # ══════════════════════════════════════════════════════════════════════
    ('STREAMING/MUSIC', 'TUBIDY'):          1.0,
    ('STREAMING/MUSIC', 'VEVO'):            5.0,
    ('STREAMING/MUSIC', 'ONLINE RADIO BOX'):0.5,
    ('STREAMING/MUSIC', 'LIVEONE'):         0.5,
    ('STREAMING/MUSIC', 'QELLO CONCERTS'):  0.3,
    ('STREAMING/MUSIC', 'SIMPLE RADIO'):    0.5,
    ('STREAMING/MUSIC', 'RADIO NET'):       0.3,
    ('STREAMING/MUSIC', 'FREEFY'):          0.2,
    ('STREAMING/MUSIC', 'MYTUNER FM RADIO'):0.3,
    ('STREAMING/MUSIC', 'NAPSTER'):         0.3,
    ('STREAMING/MUSIC', 'ACCURADIO'):       0.2,
    ('STREAMING/MUSIC', 'POCKET FM'):       0.3,

    # ══════════════════════════════════════════════════════════════════════
    # BROADCAST / CABLE  (round 2 — mid-tier)
    # CBS NEWS/SPORTS, CNBC, NBC SPORTS/NEWS kept per user request
    # ══════════════════════════════════════════════════════════════════════
    ('BROADCAST/CABLE', 'BET NETWORK'):           3.0,
    ('BROADCAST/CABLE', 'WILLOW TV'):             0.3,
    ('BROADCAST/CABLE', 'DRAFTKINGS NETWORK'):    2.0,
    ('BROADCAST/CABLE', 'FOX BUSINESS'):          3.0,
    ('BROADCAST/CABLE', 'PBS'):                  12.0,
    ('BROADCAST/CABLE', 'THETVAPP.TO'):           0.2,
    ('BROADCAST/CABLE', 'MTV'):                   7.0,
    ('BROADCAST/CABLE', 'CNET'):                  5.0,
    ('BROADCAST/CABLE', 'NICKELODEON'):           8.0,
    ('BROADCAST/CABLE', 'NEWSMAX'):               4.0,
    ('BROADCAST/CABLE', 'FOOD NETWORK'):          8.0,
    ('BROADCAST/CABLE', 'CBS'):                  12.0,
    ('BROADCAST/CABLE', 'A&E CRIME CENTRAL'):     1.5,
    ('BROADCAST/CABLE', 'DISTROTV'):              0.3,
    ('BROADCAST/CABLE', 'TNT'):                   6.0,
    ('BROADCAST/CABLE', 'BRAVOTV'):               5.0,
    ('BROADCAST/CABLE', 'HISTORY CHANNEL'):       6.0,
    ('BROADCAST/CABLE', 'ANIMAL PLANET'):         4.0,
    ('BROADCAST/CABLE', 'ABC'):                  10.0,
    ('BROADCAST/CABLE', 'NEWSWEEK'):              4.0,

    # ══════════════════════════════════════════════════════════════════════
    # MEDIA  (round 2 — mid-tier)
    # CBS NEWS/SPORTS, CNBC, NBC SPORTS/NEWS kept per user request
    # ══════════════════════════════════════════════════════════════════════
    ('MEDIA', 'NEWSWEEK'):               4.0,
    ('MEDIA', 'YAHOO SPORTS'):           8.0,
    ('MEDIA', 'APPLE NEWS'):            15.0,
    ('MEDIA', 'GOOGLE NEWS'):           20.0,
    ('MEDIA', 'TODAY'):                  8.0,
    ('MEDIA', 'BET NETWORK'):            3.0,
    ('MEDIA', 'BRITISH BROADCASTING CORPORATION'): 5.0,
    ('MEDIA', 'YAHOO NEWS'):            10.0,
    ('MEDIA', 'ABC NEWS'):              10.0,
    ('MEDIA', 'NATIONAL PUBLIC RADIO'):  10.0,
    ('MEDIA', 'FORBES'):                 5.0,
    ('MEDIA', 'WILLOW TV'):              0.3,
    ('MEDIA', 'DRAFTKINGS NETWORK'):     2.0,
    ('MEDIA', 'FINANCIAL TIMES'):        2.0,
    ('MEDIA', 'CANADIAN BROADCASTING CORPORATION CA'): 0.5,
    ('MEDIA', 'THE NEW YORKER'):         4.0,
    ('MEDIA', 'FOX BUSINESS'):           3.0,
    ('MEDIA', 'PBS'):                   12.0,
    ('MEDIA', 'USA TODAY'):              8.0,
    ('MEDIA', 'SPORTS ILLUSTRATED'):     5.0,
    ('MEDIA', 'FANDOM'):                 8.0,

    # ══════════════════════════════════════════════════════════════════════
    # GAMES  (round 2 — across the board)
    # ══════════════════════════════════════════════════════════════════════
    ('GAMES', 'STAR WARS'):             8.0,
    ('GAMES', 'LEGO'):                 12.0,
    ('GAMES', 'GRAND THEFT AUTO'):     12.0,
    ('GAMES', 'STEAM'):                18.0,
    ('GAMES', 'CHESS.COM'):             7.0,
    ('GAMES', 'LICHESS'):              1.5,
    ('GAMES', 'VALORANT'):             4.0,
    ('GAMES', 'GAMEBANANA'):           1.0,
    ('GAMES', 'RIOT GAMES'):           6.0,
    ('GAMES', 'EPIC GAMES'):          12.0,
    ('GAMES', 'FINAL FANTASY'):        4.0,
    ('GAMES', 'BARBIE'):               4.0,
    ('GAMES', 'DOTA 2'):              1.5,
    ('GAMES', 'MORTAL KOMBAT'):        4.0,
    ('GAMES', 'ASSASSINS CREED'):      5.0,
    ('GAMES', 'SOLITAIRE'):          15.0,
    ('GAMES', 'SUPER MARIO'):         12.0,
    ('GAMES', 'EA SPORTS PGA TOUR'):   2.0,
    ('GAMES', 'HARRY POTTER'):         8.0,
    ('GAMES', 'CRAZY GAMES'):          2.0,
    ('GAMES', 'GAME OF THRONES'):      5.0,
    ('GAMES', 'CLASH ROYALE'):         3.0,
    ('GAMES', 'AMAZON LUNA'):          1.5,
    ('GAMES', 'STUMBLE GUYS'):         2.0,
    ('GAMES', 'ANGRY BIRDS'):          5.0,
    ('GAMES', 'ROCKSTAR GAMES'):       6.0,
    ('GAMES', 'PBS KIDS'):             8.0,
    ('GAMES', 'EA SPORTS'):            8.0,
    ('GAMES', 'NEOPETS'):              1.0,
    ('GAMES', 'LIODEN'):               0.3,

    # ══════════════════════════════════════════════════════════════════════
    # APP / PLATFORM USAGE  (round 2 — top inflated values)
    # ══════════════════════════════════════════════════════════════════════
    ('APP/PLATFORM USAGE', 'GOOGLE DOCS'):              28.0,
    ('APP/PLATFORM USAGE', 'ZOOM'):                     28.0,
    ('APP/PLATFORM USAGE', 'GOOGLE CALENDAR'):          25.0,
    ('APP/PLATFORM USAGE', 'YAHOO MAIL'):               12.0,
    ('APP/PLATFORM USAGE', 'NEST'):                      6.0,
    ('APP/PLATFORM USAGE', 'CANVA'):                     8.0,
    ('APP/PLATFORM USAGE', 'MICROSOFT OUTLOOK MAIL'):   18.0,
    ('APP/PLATFORM USAGE', 'GOOGLE TRANSLATE'):         18.0,
    ('APP/PLATFORM USAGE', 'GOOGLE DRIVE'):             28.0,
    ('APP/PLATFORM USAGE', 'GOOGLE MEET'):              12.0,
    ('APP/PLATFORM USAGE', 'GOOGLE MAPS'):              67.0,
    ('APP/PLATFORM USAGE', 'MICROSOFT TEAMS'):          18.0,
    ('APP/PLATFORM USAGE', 'DOORDASH'):                 12.0,
    ('APP/PLATFORM USAGE', 'UBER EATS'):                10.0,
    ('APP/PLATFORM USAGE', 'GOOGLE PLAY'):              35.0,
    ('APP/PLATFORM USAGE', 'INSTACART'):                 8.0,
    ('APP/PLATFORM USAGE', 'GOOGLE CLASSROOM'):         10.0,
    ('APP/PLATFORM USAGE', 'GOOGLE PHOTOS'):            25.0,
    ('APP/PLATFORM USAGE', 'ZILLOW'):                   12.0,
    ('APP/PLATFORM USAGE', 'DROPBOX'):                  10.0,
    ('APP/PLATFORM USAGE', 'GOOGLE ADS'):                5.0,
    ('APP/PLATFORM USAGE', 'GOOGLE SCHOLAR'):            5.0,
    ('APP/PLATFORM USAGE', 'IQIYI'):                     0.5,
    ('APP/PLATFORM USAGE', 'CRAIGSLIST'):               12.0,
    ('APP/PLATFORM USAGE', 'SHUTTERSTOCK'):              2.0,
    ('APP/PLATFORM USAGE', 'GOOGLE EARTH'):             10.0,
    ('APP/PLATFORM USAGE', 'ICLOUD'):                   45.0,
    ('APP/PLATFORM USAGE', 'GMAIL'):                    55.0,
    ('APP/PLATFORM USAGE', 'FEDEX'):                    10.0,
    ('APP/PLATFORM USAGE', 'USPS'):                     15.0,
    ('APP/PLATFORM USAGE', 'GOODREADS'):                 5.0,
    ('APP/PLATFORM USAGE', 'UPS'):                      12.0,
    ('APP/PLATFORM USAGE', 'YELP'):                     12.0,
    ('APP/PLATFORM USAGE', 'VIMEO'):                     3.0,
    ('APP/PLATFORM USAGE', 'INDEED'):                   10.0,
    ('APP/PLATFORM USAGE', 'GRAMMARLY'):                 5.0,
    ('APP/PLATFORM USAGE', 'ANCESTRY'):                  4.0,
    ('APP/PLATFORM USAGE', 'AOL MAIL'):                  5.0,
    ('APP/PLATFORM USAGE', 'SCRIBD'):                    3.0,
    ('APP/PLATFORM USAGE', 'DISCOGS'):                   1.0,
    ('APP/PLATFORM USAGE', 'MY FITNESS PAL'):            5.0,
    ('APP/PLATFORM USAGE', 'WEATHER'):                  15.0,
    ('APP/PLATFORM USAGE', 'REALTOR.COM'):               8.0,
    ('APP/PLATFORM USAGE', 'REDFIN'):                    5.0,
    ('APP/PLATFORM USAGE', 'NERDWALLET'):                5.0,
    ('APP/PLATFORM USAGE', 'TRULIA'):                    4.0,
    ('APP/PLATFORM USAGE', 'QUIZLET'):                   4.0,
    ('APP/PLATFORM USAGE', 'GOPUFF'):                    2.0,
    ('APP/PLATFORM USAGE', 'FLICKR'):                    2.0,
    ('APP/PLATFORM USAGE', 'MICROSOFT 365'):            12.0,
    ('APP/PLATFORM USAGE', 'SKYPE'):                     5.0,
    ('APP/PLATFORM USAGE', 'GLASSDOOR'):                 5.0,
    ('APP/PLATFORM USAGE', 'ESPN FANTASY'):              5.0,
    ('APP/PLATFORM USAGE', 'NEXTDOOR'):                  8.0,
    ('APP/PLATFORM USAGE', 'CREDIT KARMA'):              8.0,
    ('APP/PLATFORM USAGE', 'KICKSTARTER'):               3.0,
    ('APP/PLATFORM USAGE', 'SHAZAM'):                    5.0,
    ('APP/PLATFORM USAGE', 'WEBMD'):                     8.0,
    ('APP/PLATFORM USAGE', 'EXPERIAN'):                  5.0,
    ('APP/PLATFORM USAGE', 'AUDIBLE'):                   5.0,
    ('APP/PLATFORM USAGE', 'GRUBHUB'):                   5.0,
    ('APP/PLATFORM USAGE', 'KINDLE'):                    8.0,
    ('APP/PLATFORM USAGE', 'BUMBLE'):                    3.0,
    ('APP/PLATFORM USAGE', 'AARP'):                      8.0,
    ('APP/PLATFORM USAGE', 'GROUPON'):                   4.0,
    ('APP/PLATFORM USAGE', 'WAZE'):                     12.0,
    ('APP/PLATFORM USAGE', 'WIKIPEDIA'):                20.0,
    ('APP/PLATFORM USAGE', 'OPEN TABLE'):                5.0,
    ('APP/PLATFORM USAGE', 'HELLOFRESH'):                3.0,
    ('APP/PLATFORM USAGE', 'CALM'):                      3.0,
    ('APP/PLATFORM USAGE', 'GOODRX'):                    5.0,
    ('APP/PLATFORM USAGE', 'HINGE'):                     4.0,
    ('APP/PLATFORM USAGE', 'TURBO TAX'):                10.0,
    ('APP/PLATFORM USAGE', 'QUICKBOOKS'):                3.0,
    ('APP/PLATFORM USAGE', 'ID ME'):                     5.0,
    ('APP/PLATFORM USAGE', 'PAYPAL HONEY'):              5.0,

    # ══════════════════════════════════════════════════════════════════════
    # WHERE THEY SHOP  (round 2 — luxury / niche brands)
    # ══════════════════════════════════════════════════════════════════════
    ('WHERE THEY SHOP', 'RESTORATION HARDWARE'):    3.0,
    ('WHERE THEY SHOP', 'SAKS OFF 5TH'):            2.0,
    ('WHERE THEY SHOP', 'GILT'):                    1.0,
    ('WHERE THEY SHOP', 'THE VITAMIN SHOPPE'):      3.0,
    ('WHERE THEY SHOP', 'FWRD'):                    0.5,
    ('WHERE THEY SHOP', 'GOAT'):                    2.0,
    ('WHERE THEY SHOP', '1STDIBS'):                 0.5,
    ('WHERE THEY SHOP', 'LYST'):                    0.5,
    ('WHERE THEY SHOP', 'BOTTEGA VENETA'):          0.3,
    ('WHERE THEY SHOP', 'DILLARDS'):                5.0,
    ('WHERE THEY SHOP', 'FOOT LOCKER'):             6.0,
    ('WHERE THEY SHOP', 'HSN'):                     5.0,
    ('WHERE THEY SHOP', 'SEARS'):                   3.0,
    ('WHERE THEY SHOP', 'COSTCO OPTICAL'):          8.0,
    ('WHERE THEY SHOP', 'WOLF & BADGER'):           0.3,
    ('WHERE THEY SHOP', 'ROSS DRESS FOR LESS'):     8.0,
    ('WHERE THEY SHOP', 'ULTA BEAUTY'):            10.0,
    ('WHERE THEY SHOP', 'TOYS R US'):               3.0,
    ('WHERE THEY SHOP', 'GIVENCHY'):                0.3,
    ('WHERE THEY SHOP', 'GNC'):                     5.0,
    ('WHERE THEY SHOP', 'FIVE BELOW'):              8.0,
    ('WHERE THEY SHOP', 'DIOR'):                    0.5,
    ('WHERE THEY SHOP', 'MAISON MARGIELA'):         0.2,
    ('WHERE THEY SHOP', 'HARRODS'):                 0.2,
    ('WHERE THEY SHOP', 'WALGREENS'):              25.0,
    ('WHERE THEY SHOP', 'HOME GOODS'):             10.0,
    ('WHERE THEY SHOP', 'KOHLS'):                  12.0,
    ('WHERE THEY SHOP', 'CHEWY'):                   8.0,
    ('WHERE THEY SHOP', 'POSHMARK'):                3.0,
    ('WHERE THEY SHOP', 'NFL SHOP'):                3.0,
    ('WHERE THEY SHOP', 'WEGMANS'):                 3.0,
    ('WHERE THEY SHOP', 'WINN-DIXIE'):              3.0,
    ('WHERE THEY SHOP', 'ASOS'):                    2.0,

    # ══════════════════════════════════════════════════════════════════════
    # WHERE THEY DINE  (round 2 — smaller chains still too low)
    # ══════════════════════════════════════════════════════════════════════
    ('WHERE THEY DINE', 'IHOP'):                    5.0,
    ('WHERE THEY DINE', 'WAFFLE HOUSE'):            5.0,
    ('WHERE THEY DINE', 'P.F. CHANGS'):             2.0,
    ('WHERE THEY DINE', 'LONGHORN STEAKHOUSE'):     4.0,
    ('WHERE THEY DINE', 'TGI FRIDAYS'):             3.0,
    ('WHERE THEY DINE', 'HOOTERS'):                 2.0,
    ('WHERE THEY DINE', 'RED ROBIN'):               3.0,
    ('WHERE THEY DINE', 'BOB EVANS'):               2.0,
    ('WHERE THEY DINE', 'BONEFISH GRILL'):          1.5,
    ('WHERE THEY DINE', 'YARD HOUSE'):              1.5,

    # ══════════════════════════════════════════════════════════════════════
    # QSR  (round 2 — remaining adjustments)
    # ══════════════════════════════════════════════════════════════════════
    ('QSR', 'FIVE GUYS'):              10.0,
    ('QSR', 'SUBWAY'):                 22.0,
    ('QSR', 'SHAKE SHACK'):            5.0,
    ('QSR', 'DAIRY QUEEN'):           10.0,
    ('QSR', 'RAISING CANES CHICKEN FINGERS'): 8.0,
    ('QSR', 'KRISPY KREME'):           6.0,
    ('QSR', 'ARBYS'):                  8.0,
    ('QSR', 'WENDYS'):                15.0,
    ('QSR', 'CRUMBL COOKIES'):         4.0,
    ('QSR', 'IN-N-OUT BURGER'):        5.0,
    ('QSR', 'CULVERS'):                3.0,
    ('QSR', 'TROPICAL SMOOTHIE CAFE'): 3.0,
    ('QSR', 'JACK IN THE BOX'):        5.0,
    ('QSR', 'JOLLIBEE'):              1.5,
    ('QSR', 'BASKIN ROBBINS'):         5.0,
    ('QSR', 'CHURCHS TEXAS CHICKEN'):  2.0,
    ('QSR', 'BUFFALO WILD WINGS'):     8.0,
    ('QSR', 'JIMMY JOHNS'):            6.0,
    ('QSR', 'DUTCH BROS COFFEE'):      4.0,
    ('QSR', 'PANDA EXPRESS'):          8.0,
    ('QSR', 'FIREHOUSE SUBS'):         4.0,
    ('QSR', 'SMOOTHIE KING'):          3.0,
    ('QSR', 'HARDEES'):                4.0,
    ('QSR', 'BUONA ITALIAN BEEF'):     0.5,
    ('QSR', 'CINNABON'):               3.0,
    ('QSR', 'NESPRESSO'):              3.0,
    ('QSR', 'MRBEAST BURGER'):         1.0,

    # ══════════════════════════════════════════════════════════════════════
    # TRAVEL  (round 2 — mid/lower tier still inflated)
    # ══════════════════════════════════════════════════════════════════════
    ('TRAVEL', 'WYNDHAM HOTELS & RESORTS'):    3.0,
    ('TRAVEL', 'DOUBLETREE'):                  2.0,
    ('TRAVEL', 'CHOICE HOTELS'):               3.0,
    ('TRAVEL', 'BEST WESTERN'):                4.0,
    ('TRAVEL', 'HOLIDAY INN'):                 5.0,
    ('TRAVEL', 'WESTIN HOTELS & RESORTS'):     1.5,
    ('TRAVEL', 'AIR CANADA'):                  1.5,
    ('TRAVEL', 'FOUR POINTS HOTELS'):          1.0,
    ('TRAVEL', 'HOTEL INDIGO'):                1.0,
    ('TRAVEL', 'MSC CRUISES'):                 1.0,
    ('TRAVEL', 'SILVERSEA CRUISE'):            0.3,
    ('TRAVEL', 'WALDORF ASTORIA'):             0.3,
    ('TRAVEL', 'ZIPCAR'):                      1.0,
    ('TRAVEL', 'TRAVELOCITY'):                 3.0,
    ('TRAVEL', 'TSA PRECHECK'):                5.0,
    ('TRAVEL', 'VIRGIN VOYAGES'):              1.0,
    ('TRAVEL', 'VIKING CRUISES'):              1.5,
    ('TRAVEL', 'IHG HOTELS RESORTS'):          4.0,
    ('TRAVEL', 'CLEAR TRAVEL'):                2.0,
    ('TRAVEL', 'ALLEGIANT'):                   2.0,
    ('TRAVEL', 'BUDGET'):                      2.0,
    ('TRAVEL', 'MANDARIN ORIENTAL'):           0.2,
    ('TRAVEL', 'CAESARS PALACE & ENTERTAINMENT'): 2.0,
    ('TRAVEL', 'HAWAIIAN AIRLINES'):           0.5,
    ('TRAVEL', 'SHERATON HOTELS AND RESORTS'):  3.0,
    ('TRAVEL', 'COURTYARD BY MARRIOTT'):        3.0,
    ('TRAVEL', 'JW MARRIOTT'):                  1.5,
    ('TRAVEL', 'CELEBRITY CRUISES'):            1.0,
    ('TRAVEL', 'PRINCESS CRUISES'):             1.0,
    ('TRAVEL', 'METRA TRAIN'):                  1.0,
    ('TRAVEL', 'ROSEWOOD HOTELS'):              0.2,
    ('TRAVEL', 'BACCARAT HOTEL NEW YORK'):      0.1,
    ('TRAVEL', 'RAFFLES HOTEL'):                0.1,
    ('TRAVEL', 'CAPELLA HOTELS & RESORTS'):     0.1,
    ('TRAVEL', 'AMAN RESORTS'):                 0.1,
    ('TRAVEL', 'ARIZONA BILTMORE'):             0.3,
    ('TRAVEL', 'ASPEN SNOWMASS'):               0.5,
    ('TRAVEL', 'PARK CITY MOUNTAIN RESORT'):    0.5,
    ('TRAVEL', 'IBEROSTAR RESORTS'):            0.5,
    ('TRAVEL', 'MARGARITAVILLE AT SEA'):        0.5,
    ('TRAVEL', 'ATLANTIS'):                     1.0,
    ('TRAVEL', 'VISIT LAS VEGAS'):              2.0,
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
    value_to_pct: dict[str, float] = {}
    for (cat, val), pct in CORRECTIONS.items():
        if val not in value_to_pct:
            value_to_pct[val] = pct

    # Include all prior corrections for cross-cat matching
    PRIOR = {
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
        'NETFLIX': 67.0, 'HULU': 17.0, 'DISNEY+': 28.0,
        'HBO MAX': 22.0, 'APPLE TV+': 13.0, 'PARAMOUNT+': 11.0,
        'PEACOCK': 9.0, 'AMAZON PRIME VIDEO': 43.0,
        'CHASE': 22.0, 'BANK OF AMERICA': 14.0, 'WELLS FARGO': 11.0,
        'AMERICAN EXPRESS': 14.0, 'CAPITAL ONE': 18.0,
        'GEICO': 15.0, 'STATE FARM': 17.0, 'PROGRESSIVE': 14.0,
        'TARGET': 48.0, 'WALMART': 85.0, 'COSTCO': 28.0,
        'STARBUCKS': 40.0, 'MCDONALDS': 37.0, 'DUNKIN': 23.0,
        'CHICK-FIL-A': 32.0,
    }
    for val, pct in PRIOR.items():
        if val not in value_to_pct:
            value_to_pct[val] = pct

    consistency_count = 0
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        val = str(row.get('Value', '')).strip().upper()
        key = (cat, val)

        if cat in SKIP_CATEGORIES:
            continue
        if key in CORRECTIONS:
            continue

        if val in value_to_pct:
            new_pct = value_to_pct[val]
            try:
                current_pct = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', '') or 0)
            except (ValueError, TypeError):
                current_pct = 0
            if abs(current_pct - new_pct) > 0.01:
                new_raw = int(round((new_pct / 100.0) * SAMPLE_SIZE))
                new_genpop = int(round((new_raw / SAMPLE_SIZE) * US_POP))
                df.at[idx, 'Brand Penetration (Row)'] = round(new_pct, 4)
                df.at[idx, 'Original Raw Numbers'] = new_raw
                df.at[idx, 'US Gen Pop Projection'] = new_genpop
                consistency_count += 1
                print(f"    Cross-cat: {cat}/{val}  {current_pct:.2f}% -> {new_pct}%")

    print(f"  {consistency_count} cross-category consistency fixes")

    # ── 3. Recalculate Category Share ─────────────────────────────────
    for cat in df['Column'].unique():
        cat_upper = str(cat).strip().upper()
        if cat_upper in SKIP_CATEGORIES:
            continue
        mask = df['Column'] == cat
        raws = []
        for i in df.loc[mask].index:
            try:
                r = int(float(str(df.at[i, 'Original Raw Numbers']).replace(',', '')))
            except (ValueError, TypeError):
                r = 0
            raws.append((i, r))
        total = sum(r for _, r in raws)
        if total > 0:
            for i, r in raws:
                df.at[i, 'Category Share'] = round((r / total) * 100.0, 4)

    print("  Category Share recalculated")

    df.to_csv(CSV_PATH, index=False)
    print(f"  Saved to {CSV_PATH}")
    print(f"\nDone: {corrected_count + consistency_count} total corrections")


if __name__ == '__main__':
    apply_corrections()
