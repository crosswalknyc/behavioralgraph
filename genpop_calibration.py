"""
genpop_calibration.py

Index-based calibration system that anchors all behavioral data to
verified US general population penetration rates.

HOW IT WORKS:
    For each (category, value) we store:
        - corrected_pct: the real-world US gen pop penetration (ground truth)
        - original_pct:  what the pipeline naturally produced for gen pop
                         (biased due to digitally-engaged sample)

    correction_factor = corrected_pct / original_pct

    For any profile (Taylor Swift, NFL, Kroger, etc.):
        calibrated = profile_pipeline_value * correction_factor

    This preserves the relative signal:
        - If Taylor Swift fans over-index on Twitch (60% vs gen pop 51%),
          the index (60/51 = 1.18x) is preserved in the output:
          8.5% * 1.18 = 10.0%

    Values NOT in this lookup fall back to CATEGORY_DEFAULT_FACTORS.
    If the category has a default, that factor applies to all uncorrected
    values in that category. Otherwise correction_factor = 1.0 (unchanged).

UPDATING:
    To add more corrections, add entries to GENPOP_CORRECTIONS below.
    Format: (CATEGORY, VALUE): (corrected_pct, original_pct)
"""

import pandas as pd

SILENCE_VERBOSE_OUTPUT = False

US_POPULATION = 329_900_000
SAMPLE_CAP = 10_000_000

CATEGORY_DEFAULT_FACTORS: dict[str, float] = {
    'MOST PURCHASED BRANDS':  0.20,
    'APPAREL/FOOTWEAR':       0.20,
    'BEAUTY/WELLNESS':        0.25,
    'HOME/OUTDOOR':           0.30,
    'CPG':                    0.30,
    'ACCESSORIES':            0.25,
    'TECHNOLOGY BRAND':       0.30,
    'AUTOMOBILE':             0.16,
    'GAMES':                  0.17,
    'AMUSEMENT PARKS':        0.50,
    'INTEREST':               0.70,
    'SPORTS ORGANIZATIONS':   0.30,
    'TOYS':                   0.25,
    'FRANCHISE':              0.28,
    'STREAMING/PLATFORM':     0.08,
    'STREAMING/MUSIC':        0.08,
    'BROADCAST/CABLE':        0.29,
    'MEDIA':                  0.29,
    'APP/PLATFORM USAGE':     0.31,
    'SOCIAL MEDIA':           0.23,
    'DIGITAL BANKING':        0.17,
    'BANKING':                0.23,
    'CREDIT PROVIDER':        0.29,
    'INSURANCE':              0.45,
    'TELECOM':                2.05,
    'INVESTMENTS':            2.95,
    'EDUCATION & LEARNING':   0.15,
    'TRAVEL':                 0.10,
    'QSR':                    0.40,
    'WHERE THEY SHOP':        0.14,
    'WHERE THEY DINE':        12.50,
}

# ── Ground-truth corrections ─────────────────────────────────────────────────
# (CATEGORY, VALUE): (corrected_pct, original_pipeline_pct)
#
# corrected_pct    = verified real-world US penetration
# original_pipeline_pct = what the bg.py pipeline produced for gen pop
#                         (before we hand-corrected the CSV)
GENPOP_CORRECTIONS: dict[tuple[str, str], tuple[float, float]] = {

    # ── SOCIAL MEDIA ──────────────────────────────────────────────────────
    ('SOCIAL MEDIA', 'TWITCH'):       (8.5,   50.9977),
    ('SOCIAL MEDIA', 'DISCORD'): (16.0, 41.8806),
    ('SOCIAL MEDIA', 'X'): (22.0, 36.0697),
    ('SOCIAL MEDIA', 'PATREON'):      (4.0,   19.4275),
    ('SOCIAL MEDIA', 'TUMBLR'):       (4.0,   15.522),
    ('SOCIAL MEDIA', 'ONLYFANS'):     (2.5,   12.6628),
    ('SOCIAL MEDIA', 'SNAPCHAT'): (27.0, 10.9857),
    ('SOCIAL MEDIA', 'LETTERBOXD'):   (1.5,   7.3208),
    ('SOCIAL MEDIA', 'BLUESKY'):      (1.5,   6.4427),

    # ── STREAMING / MUSIC ────────────────────────────────────────────────
    ('STREAMING/MUSIC', 'SPOTIFY'): (45.0, 91.9063),
    ('STREAMING/MUSIC', 'APPLE MUSIC'): (15.0, 87.3336),
    ('STREAMING/MUSIC', 'YOUTUBE MUSIC'):    (9.0,   76.1088),
    ('STREAMING/MUSIC', 'SIRIUSXM'): (16.0, 62.1221),
    ('STREAMING/MUSIC', 'PANDORA MUSIC'): (13.9, 53.994),
    ('STREAMING/MUSIC', 'AMAZON MUSIC'): (9.7, 45.1202),
    ('STREAMING/MUSIC', 'LAST FM'):          (2.5,   40.0746),
    ('STREAMING/MUSIC', 'DEEZER'):           (1.5,   32.6124),
    ('STREAMING/MUSIC', 'SOUNDCLOUD'):       (6.0,   27.6805),
    ('STREAMING/MUSIC', 'QOBUZ'):            (0.5,   23.2356),
    ('STREAMING/MUSIC', 'TIDAL'):            (1.5,   14.4542),
    ('STREAMING/MUSIC', 'TUBIDY'):           (1.0,   19.3),
    ('STREAMING/MUSIC', 'VEVO'):             (5.0,   17.4),
    ('STREAMING/MUSIC', 'ONLINE RADIO BOX'): (0.5,   10.2),
    ('STREAMING/MUSIC', 'LIVEONE'):          (0.5,    9.0),
    ('STREAMING/MUSIC', 'QELLO CONCERTS'):   (0.3,    7.6),
    ('STREAMING/MUSIC', 'SIMPLE RADIO'):     (0.5,    6.4),
    ('STREAMING/MUSIC', 'RADIO NET'):        (0.3,    5.4),
    ('STREAMING/MUSIC', 'FREEFY'):           (0.2,    4.6),
    ('STREAMING/MUSIC', 'MYTUNER FM RADIO'): (0.3,    4.0),
    ('STREAMING/MUSIC', 'NAPSTER'):          (0.3,    3.4),
    ('STREAMING/MUSIC', 'ACCURADIO'):        (0.2,    3.0),
    ('STREAMING/MUSIC', 'POCKET FM'):        (0.3,    3.5),

    # ── STREAMING / PLATFORM ─────────────────────────────────────────────
    ('STREAMING/PLATFORM', 'ESPN'):               (27.0,  88.448),
    ('STREAMING/PLATFORM', 'NETFLIX'): (62.5, 90.5289),
    ('STREAMING/PLATFORM', 'HULU'): (34.4, 89.5212),
    ('STREAMING/PLATFORM', 'DISNEY+'): (44.5, 51.6918),
    ('STREAMING/PLATFORM', 'HBO MAX'): (37.3, 49.8856),
    ('STREAMING/PLATFORM', 'APPLE TV+'): (13.9, 43.755),
    ('STREAMING/PLATFORM', 'PARAMOUNT+'): (19.1, 38.7105),
    ('STREAMING/PLATFORM', 'PEACOCK'): (21.8, 34.0767),
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): (58.0, 33.3701),
    ('STREAMING/PLATFORM', 'KICK'):                (2.5,  32.4718),
    ('STREAMING/PLATFORM', 'UFC FIGHT PASS'):      (2.5,  31.2161),
    ('STREAMING/PLATFORM', 'TELEMUNDO'):           (6.0,  28.8383),
    ('STREAMING/PLATFORM', 'KALOS TV'):            (0.5,  26.7),
    ('STREAMING/PLATFORM', 'SLING PLATFORM'):      (3.0,  25.6475),
    ('STREAMING/PLATFORM', 'DAZN'):                (1.0,  25.1798),
    ('STREAMING/PLATFORM', 'MUBI'):                (0.5,  22.8008),
    ('STREAMING/PLATFORM', 'SHOWTIME TV'):         (4.0,  20.6568),
    ('STREAMING/PLATFORM', 'YOUTUBE KIDS'):        (8.0,  20.5136),
    ('STREAMING/PLATFORM', 'DISCOVERY+'):          (5.0,  15.5496),
    ('STREAMING/PLATFORM', 'STARZ'):               (3.0,   8.8117),
    ('STREAMING/PLATFORM', 'AMC PLUS'):            (1.5,   7.6514),
    ('STREAMING/PLATFORM', 'BRITBOX'):             (0.5,   7.5173),
    ('STREAMING/PLATFORM', 'HALLMARK PLUS'):       (1.0,   5.4975),
    ('STREAMING/PLATFORM', 'NOW THATS TV'):        (0.5,  18.7),
    ('STREAMING/PLATFORM', 'LIVETV'):              (0.5,  18.6),
    ('STREAMING/PLATFORM', 'BOWLTV'):              (0.1,  16.4),
    ('STREAMING/PLATFORM', 'PPV'):                 (3.0,  14.8),
    ('STREAMING/PLATFORM', 'FANDANGO AT HOME'):    (3.0,  14.1),
    ('STREAMING/PLATFORM', 'STREMIO'):             (0.5,  13.5),
    ('STREAMING/PLATFORM', 'CHAUPAL'):             (0.2,  13.3),
    ('STREAMING/PLATFORM', 'ZEE5'):                (0.5,  11.5),
    ('STREAMING/PLATFORM', 'GOTHAM SPORTS'):       (0.5,  11.1),
    ('STREAMING/PLATFORM', 'DROPOUT TV'):          (0.3,  10.6),
    ('STREAMING/PLATFORM', 'CRISP SHORT FORM'):    (0.1,  10.0),
    ('STREAMING/PLATFORM', 'FIFA+'):               (1.0,   9.5),
    ('STREAMING/PLATFORM', 'VIX'):                 (2.0,   9.4),
    ('STREAMING/PLATFORM', 'NESN 360'):            (0.5,   8.1),
    ('STREAMING/PLATFORM', 'ULLU'):                (0.2,   6.7),
    ('STREAMING/PLATFORM', 'HIDIVE'):              (0.3,   6.3),
    ('STREAMING/PLATFORM', 'TENNIS TV'):           (0.3,   6.1),
    ('STREAMING/PLATFORM', 'BYUTV'):               (0.5,   5.6),
    ('STREAMING/PLATFORM', 'SIGHT & SOUND TV'):    (0.2,   5.0),
    ('STREAMING/PLATFORM', 'OSN+'):                (0.1,   4.9),
    ('STREAMING/PLATFORM', 'FLOSPORTS'):           (0.5,   4.5),
    ('STREAMING/PLATFORM', 'KOCOWA+'):             (0.3,   4.4),
    ('STREAMING/PLATFORM', 'TRILLERTV'):           (0.2,   4.1),
    ('STREAMING/PLATFORM', 'ALLBLK'):              (0.3,   3.8),
    ('STREAMING/PLATFORM', 'LIVE SPORTS ON TV TODAY'): (0.2, 3.5),
    ('STREAMING/PLATFORM', 'BET+'):                (0.5,   3.2),
    ('STREAMING/PLATFORM', 'FILMZIE'):             (0.1,   2.5),
    ('STREAMING/PLATFORM', 'RING OF HONOR'):       (0.2,   2.8),
    ('STREAMING/PLATFORM', 'CRACKLE'):             (0.5,   3.0),
    ('STREAMING/PLATFORM', 'FLIX LATINO'):          (0.3,   2.7),
    ('STREAMING/PLATFORM', 'CANELA.TV'):           (0.3,   2.5),
    ('STREAMING/PLATFORM', 'GOODSHORT'):           (0.1,   2.0),

    # ── APP / PLATFORM USAGE ─────────────────────────────────────────────
    ('APP/PLATFORM USAGE', 'SLACK'):        (5.0,   37.3897),
    ('APP/PLATFORM USAGE', 'FIVERR'):       (2.5,   31.9298),
    ('APP/PLATFORM USAGE', 'FIGMA'):        (2.5,   31.1447),
    ('APP/PLATFORM USAGE', 'UPWORK'):       (3.5,   28.7336),
    ('APP/PLATFORM USAGE', 'CRUNCHYROLL'):  (4.5,   25.9617),
    ('APP/PLATFORM USAGE', 'HUBSPOT'):      (1.5,   24.9309),
    ('APP/PLATFORM USAGE', 'TINDER'):       (9.0,   34.5362),
    ('APP/PLATFORM USAGE', 'DUOLINGO'):     (9.0,   35.6859),
    ('APP/PLATFORM USAGE', 'WHATSAPP'):     (26.0,  41.316),
    ('APP/PLATFORM USAGE', 'IMDB'):         (11.0,  37.9748),
    ('APP/PLATFORM USAGE', 'GOOGLE DOCS'):  (28.0,  48.9),
    ('APP/PLATFORM USAGE', 'ZOOM'):         (28.0,  48.6),
    ('APP/PLATFORM USAGE', 'GOOGLE CALENDAR'): (25.0, 46.7),
    ('APP/PLATFORM USAGE', 'YAHOO MAIL'):   (12.0,  46.0),
    ('APP/PLATFORM USAGE', 'NEST'):         (6.0,   44.9),
    ('APP/PLATFORM USAGE', 'CANVA'):        (8.0,   43.6),
    ('APP/PLATFORM USAGE', 'MICROSOFT OUTLOOK MAIL'): (18.0, 42.5),
    ('APP/PLATFORM USAGE', 'GOOGLE TRANSLATE'): (18.0, 42.3),
    ('APP/PLATFORM USAGE', 'GOOGLE DRIVE'): (28.0,  41.7),
    ('APP/PLATFORM USAGE', 'GOOGLE MEET'):  (12.0,  39.1),
    ('APP/PLATFORM USAGE', 'GOOGLE MAPS'):  (67.0,  53.0),
    ('APP/PLATFORM USAGE', 'MICROSOFT TEAMS'): (18.0, 38.6),
    ('APP/PLATFORM USAGE', 'DOORDASH'):     (12.0,  35.2),
    ('APP/PLATFORM USAGE', 'UBER EATS'):    (10.0,  34.3),
    ('APP/PLATFORM USAGE', 'GOOGLE PLAY'):  (35.0,  42.0),
    ('APP/PLATFORM USAGE', 'INSTACART'):    (8.0,   31.8),
    ('APP/PLATFORM USAGE', 'GOOGLE CLASSROOM'): (10.0, 31.4),
    ('APP/PLATFORM USAGE', 'GOOGLE PHOTOS'): (25.0, 30.0),
    ('APP/PLATFORM USAGE', 'ZILLOW'):       (12.0,  28.0),
    ('APP/PLATFORM USAGE', 'DROPBOX'):      (10.0,  25.0),
    ('APP/PLATFORM USAGE', 'GOOGLE ADS'):   (5.0,   22.0),
    ('APP/PLATFORM USAGE', 'GOOGLE SCHOLAR'): (5.0, 20.0),
    ('APP/PLATFORM USAGE', 'IQIYI'):        (0.5,   28.2),
    ('APP/PLATFORM USAGE', 'CRAIGSLIST'):   (12.0,  28.0),
    ('APP/PLATFORM USAGE', 'SHUTTERSTOCK'): (2.0,   25.8),
    ('APP/PLATFORM USAGE', 'GOOGLE EARTH'): (10.0,  25.0),
    ('APP/PLATFORM USAGE', 'ICLOUD'):       (45.0,  48.0),
    ('APP/PLATFORM USAGE', 'GMAIL'):        (55.0,  58.0),
    ('APP/PLATFORM USAGE', 'FEDEX'):        (10.0,  23.0),
    ('APP/PLATFORM USAGE', 'USPS'):         (15.0,  25.0),
    ('APP/PLATFORM USAGE', 'GOODREADS'):    (5.0,   18.0),
    ('APP/PLATFORM USAGE', 'UPS'):          (12.0,  22.0),
    ('APP/PLATFORM USAGE', 'YELP'):         (12.0,  23.0),
    ('APP/PLATFORM USAGE', 'VIMEO'):        (3.0,   15.0),
    ('APP/PLATFORM USAGE', 'INDEED'):       (10.0,  22.0),
    ('APP/PLATFORM USAGE', 'GRAMMARLY'):    (5.0,   18.0),
    ('APP/PLATFORM USAGE', 'ANCESTRY'):     (4.0,   16.0),
    ('APP/PLATFORM USAGE', 'AOL MAIL'):     (5.0,   15.0),
    ('APP/PLATFORM USAGE', 'SCRIBD'):       (3.0,   14.0),
    ('APP/PLATFORM USAGE', 'DISCOGS'):      (1.0,   10.0),
    ('APP/PLATFORM USAGE', 'MY FITNESS PAL'): (5.0, 18.0),
    ('APP/PLATFORM USAGE', 'WEATHER'):      (15.0,  25.0),
    ('APP/PLATFORM USAGE', 'REALTOR.COM'):  (8.0,   20.0),
    ('APP/PLATFORM USAGE', 'REDFIN'):       (5.0,   18.0),
    ('APP/PLATFORM USAGE', 'NERDWALLET'):   (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'TRULIA'):       (4.0,   15.0),
    ('APP/PLATFORM USAGE', 'QUIZLET'):      (4.0,   14.0),
    ('APP/PLATFORM USAGE', 'GOPUFF'):       (2.0,   12.0),
    ('APP/PLATFORM USAGE', 'FLICKR'):       (2.0,   12.0),
    ('APP/PLATFORM USAGE', 'MICROSOFT 365'): (12.0, 22.0),
    ('APP/PLATFORM USAGE', 'SKYPE'):        (5.0,   15.0),
    ('APP/PLATFORM USAGE', 'GLASSDOOR'):    (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'ESPN FANTASY'): (5.0,   15.0),
    ('APP/PLATFORM USAGE', 'NEXTDOOR'):     (8.0,   20.0),
    ('APP/PLATFORM USAGE', 'CREDIT KARMA'): (8.0,   20.0),
    ('APP/PLATFORM USAGE', 'KICKSTARTER'):  (3.0,   14.0),
    ('APP/PLATFORM USAGE', 'SHAZAM'):       (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'WEBMD'):        (8.0,   20.0),
    ('APP/PLATFORM USAGE', 'EXPERIAN'):     (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'AUDIBLE'):      (5.0,   18.0),
    ('APP/PLATFORM USAGE', 'GRUBHUB'):      (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'KINDLE'):       (8.0,   20.0),
    ('APP/PLATFORM USAGE', 'BUMBLE'):       (3.0,   13.0),
    ('APP/PLATFORM USAGE', 'AARP'):         (8.0,   18.0),
    ('APP/PLATFORM USAGE', 'GROUPON'):      (4.0,   15.0),
    ('APP/PLATFORM USAGE', 'WAZE'):         (12.0,  22.0),
    ('APP/PLATFORM USAGE', 'WIKIPEDIA'):    (20.0,  30.0),
    ('APP/PLATFORM USAGE', 'OPEN TABLE'):   (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'HELLOFRESH'):   (3.0,   14.0),
    ('APP/PLATFORM USAGE', 'CALM'):         (3.0,   14.0),
    ('APP/PLATFORM USAGE', 'GOODRX'):       (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'HINGE'):        (4.0,   14.0),
    ('APP/PLATFORM USAGE', 'TURBO TAX'):    (10.0,  22.0),
    ('APP/PLATFORM USAGE', 'QUICKBOOKS'):   (3.0,   13.0),
    ('APP/PLATFORM USAGE', 'ID ME'):        (5.0,   16.0),
    ('APP/PLATFORM USAGE', 'PAYPAL HONEY'): (5.0,   16.0),

    # ── DIGITAL BANKING ──────────────────────────────────────────────────
    ('DIGITAL BANKING', 'PAYPAL'): (43.0, 77.5729),
    ('DIGITAL BANKING', 'COINBASE'): (2.4, 58.6475),
    ('DIGITAL BANKING', 'BILT'):      (1.5,   19.8593),

    # ── BANKING ──────────────────────────────────────────────────────────
    ('BANKING', 'CHASE'): (29.3, 56.4858),
    ('BANKING', 'BANK OF AMERICA'): (24.1, 44.6929),
    ('BANKING', 'WELLS FARGO'): (24.4, 39.9569),
    ('BANKING', 'CITIBANK'): (7.7, 35.4207),
    ('BANKING', 'TD BANK'):               (4.5,  30.6499),
    ('BANKING', 'US BANK'):               (5.0,  25.1706),
    ('BANKING', 'SOFI BANK'):             (1.5,  23.5161),
    ('BANKING', 'TRUIST BANK'):           (5.0,  19.1094),
    ('BANKING', 'PNC BANK'):              (5.0,  17.5011),
    ('BANKING', 'BANK OF MONTREAL/BMO'):  (2.0,  15.059),
    ('BANKING', 'VANGUARD'):              (7.0,  12.4474),
    ('BANKING', 'FIFTH THIRD BANK'):      (2.0,  10.6836),
    ('BANKING', 'HUNTINGTON BANK'):       (2.0,   9.3744),
    ('BANKING', 'CITIZENS BANK'):         (2.0,   8.8184),
    ('BANKING', 'REGIONS BANK'):          (2.0,   7.2363),
    ('BANKING', 'KEYBANK'):               (1.5,   6.6597),
    ('BANKING', 'BARCLAYS US'):           (1.0,   5.4299),
    ('BANKING', 'SANTANDER BANK'):        (1.0,   4.7013),
    ('BANKING', 'APPLE PAY'):            (13.0,   4.062),

    # ── CREDIT PROVIDER ──────────────────────────────────────────────────
    ('CREDIT PROVIDER', 'AMERICAN EXPRESS'):      (14.0,  54.9512),
    ('CREDIT PROVIDER', 'CAPITAL ONE'):           (18.0,  41.4307),
    ('CREDIT PROVIDER', 'DISCOVER CREDIT CARD'):   (7.0,  31.1285),
    ('CREDIT PROVIDER', 'SYNCHRONY'):              (6.0,  18.7532),
    ('CREDIT PROVIDER', 'AFFIRM PAYMENT'):         (4.0,  13.972),
    ('CREDIT PROVIDER', 'GM FINANCIAL'):           (2.0,  10.3617),
    ('CREDIT PROVIDER', 'FUNDBOX'):                (0.5,   8.4033),
    ('CREDIT PROVIDER', 'FREEDOM MORTGAGE'):       (2.0,   6.1155),
    ('CREDIT PROVIDER', 'QUICKEN LOANS'):          (3.0,   5.0042),

    # ── INSURANCE ────────────────────────────────────────────────────────
    ('INSURANCE', 'UNITED HEALTHCARE'):  (16.0,  35.175),
    ('INSURANCE', 'USAA'):                (6.0,  34.2204),
    ('INSURANCE', 'KAISER PERMANENTE'):   (5.0,  27.7766),
    ('INSURANCE', 'GEICO'):              (15.0,  25.0003),
    ('INSURANCE', 'AETNA'):               (9.0,  22.3726),
    ('INSURANCE', 'STATE FARM'):         (17.0,  19.7156),
    ('INSURANCE', 'CIGNA'):               (7.0,  18.3802),
    ('INSURANCE', 'HUMANA'):              (6.0,  16.9188),
    ('INSURANCE', 'METLIFE'):             (5.0,  14.1935),
    ('INSURANCE', 'PROGRESSIVE'):        (14.0,  13.096),
    ('INSURANCE', 'ALLSTATE'):           (10.0,  11.5183),
    ('INSURANCE', 'HEALTHCARE.GOV'):      (5.0,  10.4425),
    ('INSURANCE', 'LIBERTY MUTUAL'):      (7.0,   9.0071),

    # ── TELECOM ──────────────────────────────────────────────────────────
    ('TELECOM', 'XFINITY'): (24.4, 9.4175),
    ('TELECOM', 'VERIZON'): (44.0, 8.7529),
    ('TELECOM', 'T-MOBILE'): (39.1, 7.8892),
    ('TELECOM', 'AT&T'): (36.1, 6.7239),
    ('TELECOM', 'STARLINK'):          (1.5,   6.0623),
    ('TELECOM', 'SPECTRUM'): (23.6, 5.3753),
    ('TELECOM', 'GOOGLE FIBER'):      (0.5,   3.4864),
    ('TELECOM', 'CENTURY LINK'):      (2.0,   3.8632),
    ('TELECOM', 'VISIBLE'):           (1.0,   2.9655),
    ('TELECOM', 'CRICKET WIRELESS'):  (2.5,   1.3),

    # ── GAMES ────────────────────────────────────────────────────────────
    ('GAMES', 'ROBLOX'):              (16.0,  52.5852),
    ('GAMES', 'MINECRAFT'):           (16.0,  50.2475),
    ('GAMES', 'FORTNITE'):            (11.0,  37.8612),
    ('GAMES', 'LEAGUE OF LEGENDS'):   (4.0,   32.9464),
    ('GAMES', 'OVERWATCH'):           (2.5,   30.9613),
    ('GAMES', 'GENSHIN IMPACT'):      (2.5,   30.6784),
    ('GAMES', 'CALL OF DUTY'):        (9.0,   29.8885),
    ('GAMES', 'STAR WARS'):           (8.0,   44.2),
    ('GAMES', 'LEGO'):               (12.0,   41.1),
    ('GAMES', 'GRAND THEFT AUTO'):   (12.0,   39.0),
    ('GAMES', 'STEAM'):              (18.0,   37.8),
    ('GAMES', 'CHESS.COM'):           (7.0,   36.7),
    ('GAMES', 'LICHESS'):             (1.5,   36.0),
    ('GAMES', 'VALORANT'):            (4.0,   35.0),
    ('GAMES', 'GAMEBANANA'):          (1.0,   35.0),
    ('GAMES', 'RIOT GAMES'):          (6.0,   34.6),
    ('GAMES', 'EPIC GAMES'):         (12.0,   34.6),
    ('GAMES', 'FINAL FANTASY'):       (4.0,   34.5),
    ('GAMES', 'BARBIE'):              (4.0,   33.8),
    ('GAMES', 'DOTA 2'):              (1.5,   33.4),
    ('GAMES', 'MORTAL KOMBAT'):       (4.0,   33.2),
    ('GAMES', 'ASSASSINS CREED'):     (5.0,   29.9),
    ('GAMES', 'SOLITAIRE'):         (15.0,   29.0),
    ('GAMES', 'SUPER MARIO'):        (12.0,   29.8),
    ('GAMES', 'EA SPORTS PGA TOUR'):  (2.0,   28.0),
    ('GAMES', 'HARRY POTTER'):        (8.0,   28.1),
    ('GAMES', 'CRAZY GAMES'):         (2.0,   27.0),
    ('GAMES', 'GAME OF THRONES'):     (5.0,   27.8),
    ('GAMES', 'CLASH ROYALE'):        (3.0,   26.0),
    ('GAMES', 'AMAZON LUNA'):         (1.5,   25.0),
    ('GAMES', 'STUMBLE GUYS'):        (2.0,   24.0),
    ('GAMES', 'ANGRY BIRDS'):         (5.0,   26.2),
    ('GAMES', 'ROCKSTAR GAMES'):      (6.0,   27.0),
    ('GAMES', 'PBS KIDS'):            (8.0,   25.0),
    ('GAMES', 'EA SPORTS'):           (8.0,   25.0),
    ('GAMES', 'NEOPETS'):             (1.0,   22.0),
    ('GAMES', 'LIODEN'):              (0.3,   20.0),

    # ── BROADCAST / CABLE ────────────────────────────────────────────────
    ('BROADCAST/CABLE', 'ESPN'):                (27.0,  88.448),
    ('BROADCAST/CABLE', 'FOX NEWS'):            (16.0,  46.6019),
    ('BROADCAST/CABLE', 'CNN'):                 (13.0,  44.6471),
    ('BROADCAST/CABLE', 'MSNBC'):              (9.0,   41.1418),
    ('BROADCAST/CABLE', 'BET NETWORK'):         (3.0,   28.9),
    ('BROADCAST/CABLE', 'WILLOW TV'):           (0.3,   26.5),
    ('BROADCAST/CABLE', 'DRAFTKINGS NETWORK'):  (2.0,   26.3),
    ('BROADCAST/CABLE', 'FOX BUSINESS'):        (3.0,   24.9),
    ('BROADCAST/CABLE', 'PBS'):                (12.0,   24.7),
    ('BROADCAST/CABLE', 'THETVAPP.TO'):         (0.2,   23.7),
    ('BROADCAST/CABLE', 'MTV'):                 (7.0,   22.0),
    ('BROADCAST/CABLE', 'CNET'):                (5.0,   21.8),
    ('BROADCAST/CABLE', 'NICKELODEON'):         (8.0,   21.8),
    ('BROADCAST/CABLE', 'NEWSMAX'):             (4.0,   21.3),
    ('BROADCAST/CABLE', 'FOOD NETWORK'):        (8.0,   20.8),
    ('BROADCAST/CABLE', 'CBS'):                (12.0,   20.1),
    ('BROADCAST/CABLE', 'A&E CRIME CENTRAL'):   (1.5,   19.2),
    ('BROADCAST/CABLE', 'DISTROTV'):            (0.3,   18.6),
    ('BROADCAST/CABLE', 'TNT'):                 (6.0,   18.3),
    ('BROADCAST/CABLE', 'BRAVOTV'):             (5.0,   17.5),
    ('BROADCAST/CABLE', 'HISTORY CHANNEL'):     (6.0,   15.5),
    ('BROADCAST/CABLE', 'ANIMAL PLANET'):       (4.0,   15.3),
    ('BROADCAST/CABLE', 'ABC'):                (10.0,   14.3),
    ('BROADCAST/CABLE', 'NEWSWEEK'):            (4.0,   33.0),

    # ── MEDIA ────────────────────────────────────────────────────────────
    ('MEDIA', 'ESPN'):                  (27.0,  88.448),
    ('MEDIA', 'FOX NEWS'):              (16.0,  46.6019),
    ('MEDIA', 'CNN'):                   (13.0,  44.6471),
    ('MEDIA', 'MSNBC'):                (9.0,   41.1418),
    ('MEDIA', 'NEW YORK TIMES'):        (11.0,  29.4251),
    ('MEDIA', 'BUZZFEED'):              (9.0,   23.5606),
    ('MEDIA', 'NEWSWEEK'):              (4.0,   33.0),
    ('MEDIA', 'YAHOO SPORTS'):          (8.0,   29.8),
    ('MEDIA', 'APPLE NEWS'):           (15.0,   29.5),
    ('MEDIA', 'GOOGLE NEWS'):          (20.0,   29.1),
    ('MEDIA', 'TODAY'):                 (8.0,   29.1),
    ('MEDIA', 'BET NETWORK'):           (3.0,   28.9),
    ('MEDIA', 'BRITISH BROADCASTING CORPORATION'): (5.0, 27.9),
    ('MEDIA', 'YAHOO NEWS'):           (10.0,   27.6),
    ('MEDIA', 'ABC NEWS'):             (10.0,   27.5),
    ('MEDIA', 'NATIONAL PUBLIC RADIO'): (10.0,  27.3),
    ('MEDIA', 'FORBES'):                (5.0,   27.1),
    ('MEDIA', 'WILLOW TV'):             (0.3,   26.5),
    ('MEDIA', 'DRAFTKINGS NETWORK'):    (2.0,   26.3),
    ('MEDIA', 'FINANCIAL TIMES'):       (2.0,   25.9),
    ('MEDIA', 'CANADIAN BROADCASTING CORPORATION CA'): (0.5, 24.0),
    ('MEDIA', 'THE NEW YORKER'):        (4.0,   25.5),
    ('MEDIA', 'FOX BUSINESS'):          (3.0,   24.9),
    ('MEDIA', 'PBS'):                  (12.0,   24.7),
    ('MEDIA', 'USA TODAY'):             (8.0,   24.0),
    ('MEDIA', 'SPORTS ILLUSTRATED'):    (5.0,   24.0),
    ('MEDIA', 'FANDOM'):                (8.0,   15.0),
    ('MEDIA', 'THETVAPP.TO'):           (0.2,   23.7),
    ('MEDIA', 'MTV'):                   (7.0,   22.0),
    ('MEDIA', 'CNET'):                  (5.0,   21.8),
    ('MEDIA', 'NICKELODEON'):           (8.0,   21.8),
    ('MEDIA', 'NEWSMAX'):               (4.0,   21.3),
    ('MEDIA', 'FOOD NETWORK'):          (8.0,   20.8),
    ('MEDIA', 'CBS'):                  (12.0,   20.1),
    ('MEDIA', 'A&E CRIME CENTRAL'):     (1.5,   19.2),
    ('MEDIA', 'DISTROTV'):              (0.3,   18.6),
    ('MEDIA', 'TNT'):                   (6.0,   18.3),
    ('MEDIA', 'BRAVOTV'):              (5.0,   17.5),
    ('MEDIA', 'HISTORY CHANNEL'):       (6.0,   15.5),
    ('MEDIA', 'ANIMAL PLANET'):         (4.0,   15.3),
    ('MEDIA', 'ABC'):                  (10.0,   14.3),

    # ── FRANCHISE ────────────────────────────────────────────────────────
    ('FRANCHISE', 'ROBLOX'):            (16.0,  52.5852),
    ('FRANCHISE', 'MINECRAFT'):         (16.0,  50.2475),
    ('FRANCHISE', 'FORTNITE'):          (11.0,  37.8612),
    ('FRANCHISE', 'STAR WARS'):         (8.0,   44.2),
    ('FRANCHISE', 'LEGO'):             (12.0,   41.1),
    ('FRANCHISE', 'GRAND THEFT AUTO'): (12.0,   39.0),
    ('FRANCHISE', 'FINAL FANTASY'):     (4.0,   34.5),
    ('FRANCHISE', 'BARBIE'):            (4.0,   33.8),
    ('FRANCHISE', 'MORTAL KOMBAT'):     (4.0,   33.2),
    ('FRANCHISE', 'ASSASSINS CREED'):   (5.0,   29.9),
    ('FRANCHISE', 'SUPER MARIO'):      (12.0,   29.8),
    ('FRANCHISE', 'HARRY POTTER'):      (8.0,   28.1),
    ('FRANCHISE', 'GAME OF THRONES'):   (5.0,   27.8),
    ('FRANCHISE', 'ANGRY BIRDS'):       (5.0,   26.2),

    # ── EDUCATION & LEARNING ─────────────────────────────────────────────
    ('EDUCATION & LEARNING', 'UDEMY'):        (4.0,   26.4987),
    ('EDUCATION & LEARNING', 'W3 SCHOOLS'):   (2.5,   22.5087),
    ('EDUCATION & LEARNING', 'MASTER CLASS'): (2.5,   14.977),
    ('EDUCATION & LEARNING', 'SKILLSHARE'):   (1.5,   11.4138),

    # ── INVESTMENTS ──────────────────────────────────────────────────────
    ('INVESTMENTS', 'CHARLES SCHWAB'): (11.0,  6.0148),
    ('INVESTMENTS', 'FIDELITY'):       (13.0,  3.5321),
    ('INVESTMENTS', 'ROBINHOOD'):      (7.5,   2.5387),

    # ── AUTOMOBILE ───────────────────────────────────────────────────────
    ('AUTOMOBILE', 'BMW'):             (9.0,   42.1315),
    ('AUTOMOBILE', 'MERCEDES-BENZ'):   (7.0,   40.133),
    ('AUTOMOBILE', 'AUDI'):            (5.0,   31.2163),
    ('AUTOMOBILE', 'PORSCHE'):         (1.5,   29.3845),
    ('AUTOMOBILE', 'FERRARI'):         (0.5,   23.7824),
    ('AUTOMOBILE', 'LAMBORGHINI'):     (0.3,   17.0387),

    # ── TRAVEL ───────────────────────────────────────────────────────────
    ('TRAVEL', 'DELTA AIR LINES'):                  (7.0,  34.8342),
    ('TRAVEL', 'AMERICAN AIRLINES'):                (6.0,  34.6421),
    ('TRAVEL', 'UNITED AIRLINE & AVIATIONS'):       (6.0,  34.3903),
    ('TRAVEL', 'BOOKING'):                         (12.0,  33.9427),
    ('TRAVEL', 'EXPEDIA'):                         (10.0,  33.6332),
    ('TRAVEL', 'AIRBNB'):                          (12.0,  33.2392),
    ('TRAVEL', 'SOUTHWEST AIRLINES'):               (8.0,  32.6495),
    ('TRAVEL', 'TRIPADVISOR'):                      (8.0,  32.3165),
    ('TRAVEL', 'MARRIOTT'):                         (8.0,  31.4134),
    ('TRAVEL', 'ALASKA AIRLINES'):                  (2.0,  30.7458),
    ('TRAVEL', 'RADISSON HOTELS'):                  (2.0,  30.0955),
    ('TRAVEL', 'VRBO'):                             (5.0,  29.6684),
    ('TRAVEL', 'AMERICAN EXPRESS TRAVEL'):           (3.0,  29.1654),
    ('TRAVEL', 'AVIS'):                             (4.0,  28.5782),
    ('TRAVEL', 'HILTON'):                           (8.0,  28.3728),
    ('TRAVEL', 'MSC CRUISES'):                      (1.0,  28.2181),
    ('TRAVEL', 'HOTELS.COM'):                       (6.0,  26.9617),
    ('TRAVEL', 'TRIVAGO'):                          (4.0,  26.712),
    ('TRAVEL', 'PRICELINE'):                        (5.0,  26.5394),
    ('TRAVEL', 'HYATT'):                            (3.0,  25.8815),
    ('TRAVEL', 'JET BLUE'):                         (3.0,  25.5348),
    ('TRAVEL', 'UBER'):                            (20.0,  25.5328),
    ('TRAVEL', 'MGM RESORTS'):                      (3.0,  24.9849),
    ('TRAVEL', 'SANDALS RESORT'):                   (1.0,  24.3794),
    ('TRAVEL', 'DISNEY VACATION CLUB'):             (1.5,  23.7669),
    ('TRAVEL', 'RITZ-CARLTON'):                     (0.5,  17.6996),
    ('TRAVEL', 'ROYAL CARIBBEAN'):                  (3.0,   9.7366),
    ('TRAVEL', 'CARNIVAL CRUISE LINE'):             (3.0,   8.4997),
    ('TRAVEL', 'NORWEGIAN CRUISE LINE'):            (1.5,   8.3134),
    ('TRAVEL', 'AMTRAK'):                           (5.0,  14.4978),
    ('TRAVEL', 'LYFT'):                            (12.0,   5.6132),
    ('TRAVEL', 'HERTZ'):                            (3.0,  13.2592),
    ('TRAVEL', 'ENTERPRISE'):                       (4.0,   9.0043),
    ('TRAVEL', 'TURO'):                             (2.0,   7.5798),
    ('TRAVEL', 'SPIRIT AIRLINES'):                  (2.5,   6.5717),
    ('TRAVEL', 'FRONTIER AIRLINES'):                (2.0,   7.1882),
    ('TRAVEL', 'FOUR SEASONS HOTEL & RESORTS'):     (0.3,   5.6571),
    ('TRAVEL', 'WYNDHAM HOTELS & RESORTS'):         (3.0,  28.0),
    ('TRAVEL', 'DOUBLETREE'):                       (2.0,  26.0),
    ('TRAVEL', 'CHOICE HOTELS'):                    (3.0,  27.0),
    ('TRAVEL', 'BEST WESTERN'):                     (4.0,  28.0),
    ('TRAVEL', 'HOLIDAY INN'):                      (5.0,  29.0),
    ('TRAVEL', 'WESTIN HOTELS & RESORTS'):          (1.5,  25.0),
    ('TRAVEL', 'AIR CANADA'):                       (1.5,  24.0),
    ('TRAVEL', 'FOUR POINTS HOTELS'):               (1.0,  22.0),
    ('TRAVEL', 'HOTEL INDIGO'):                     (1.0,  22.0),
    ('TRAVEL', 'SILVERSEA CRUISE'):                 (0.3,  18.0),
    ('TRAVEL', 'WALDORF ASTORIA'):                  (0.3,  18.0),
    ('TRAVEL', 'ZIPCAR'):                           (1.0,  20.0),
    ('TRAVEL', 'TRAVELOCITY'):                      (3.0,  25.0),
    ('TRAVEL', 'TSA PRECHECK'):                     (5.0,  27.0),
    ('TRAVEL', 'VIRGIN VOYAGES'):                   (1.0,  20.0),
    ('TRAVEL', 'VIKING CRUISES'):                   (1.5,  22.0),
    ('TRAVEL', 'IHG HOTELS RESORTS'):               (4.0,  27.0),
    ('TRAVEL', 'CLEAR TRAVEL'):                     (2.0,  24.0),
    ('TRAVEL', 'ALLEGIANT'):                        (2.0,  22.0),
    ('TRAVEL', 'BUDGET'):                           (2.0,  22.0),
    ('TRAVEL', 'MANDARIN ORIENTAL'):                (0.2,  16.0),
    ('TRAVEL', 'CAESARS PALACE & ENTERTAINMENT'):   (2.0,  24.0),
    ('TRAVEL', 'HAWAIIAN AIRLINES'):                (0.5,  18.0),
    ('TRAVEL', 'SHERATON HOTELS AND RESORTS'):      (3.0,  26.0),
    ('TRAVEL', 'COURTYARD BY MARRIOTT'):            (3.0,  26.0),
    ('TRAVEL', 'JW MARRIOTT'):                      (1.5,  23.0),
    ('TRAVEL', 'CELEBRITY CRUISES'):                (1.0,  20.0),
    ('TRAVEL', 'PRINCESS CRUISES'):                 (1.0,  20.0),
    ('TRAVEL', 'METRA TRAIN'):                      (1.0,  18.0),
    ('TRAVEL', 'ROSEWOOD HOTELS'):                  (0.2,  15.0),
    ('TRAVEL', 'BACCARAT HOTEL NEW YORK'):          (0.1,  12.0),
    ('TRAVEL', 'RAFFLES HOTEL'):                    (0.1,  12.0),
    ('TRAVEL', 'CAPELLA HOTELS & RESORTS'):         (0.1,  12.0),
    ('TRAVEL', 'AMAN RESORTS'):                     (0.1,  12.0),
    ('TRAVEL', 'ARIZONA BILTMORE'):                 (0.3,  15.0),
    ('TRAVEL', 'ASPEN SNOWMASS'):                   (0.5,  16.0),
    ('TRAVEL', 'PARK CITY MOUNTAIN RESORT'):        (0.5,  16.0),
    ('TRAVEL', 'IBEROSTAR RESORTS'):                (0.5,  16.0),
    ('TRAVEL', 'MARGARITAVILLE AT SEA'):            (0.5,  16.0),
    ('TRAVEL', 'ATLANTIS'):                         (1.0,  20.0),
    ('TRAVEL', 'VISIT LAS VEGAS'):                  (2.0,  22.0),

    # ── QSR ──────────────────────────────────────────────────────────────
    ('QSR', 'STARBUCKS'):                (40.0,  44.6626),
    ('QSR', 'MCDONALDS'):               (37.0,  43.7229),
    ('QSR', 'DOMINOS'): (28.0, 43.6124),
    ('QSR', 'PIZZA HUT'):               (22.0,  41.7843),
    ('QSR', 'TACO BELL'): (38.0, 38.7056),
    ('QSR', 'BURGER KING'):             (22.0,  37.8374),
    ('QSR', 'JERSEY MIKES SUBS'):        (8.0,  36.2696),
    ('QSR', 'PAPA JOHNS'):              (12.0,  35.7982),
    ('QSR', 'PORTILLOS'):                (2.0,  33.7253),
    ('QSR', 'NOODLES AND CO.'):           (2.0,  32.291),
    ('QSR', 'SWEETGREEN'):               (2.5,  31.6758),
    ('QSR', 'EL POLLO LOCO'):            (3.0,  31.0576),
    ('QSR', 'MOES SOUTHWEST GRILL'):     (3.0,  28.7077),
    ('QSR', 'CHIPOTLE MEXICAN GRILL'): (22.0, 27.3746),
    ('QSR', 'BONCHON'):                   (1.5,  27.2016),
    ('QSR', 'KFC'):                      (18.0,  27.0227),
    ('QSR', 'SPRINKLES CUPCAKES'):        (0.5,  24.6615),
    ('QSR', 'AUNTIE ANNES PRETZELS'):    (5.0,  24.0393),
    ('QSR', 'CHICK-FIL-A'):             (32.0,  23.4016),
    ('QSR', 'JENIS ICE CREAM'):           (1.0,  22.0765),
    ('QSR', 'FRESH BROTHERS'):            (0.5,  21.4197),
    ('QSR', 'LITTLE CAESARS'):           (12.0,  21.1298),
    ('QSR', 'PLANET SMOOTHIE'):           (1.0,  20.3107),
    ('QSR', 'POPEYES'):                  (12.0,  20.1932),
    ('QSR', 'ZAXBYS'):                    (4.0,  18.2293),
    ('QSR', 'PANERA BREAD'):            (14.0,  18.1162),
    ('QSR', 'BOJANGLES'):                 (3.0,  17.077),
    ('QSR', 'PEI WEI ASIAN KITCHEN'):    (1.0,  17.0166),
    ('QSR', 'DUNKIN'): (22.0, 16.9972),
    ('QSR', 'WINGSTOP'):                  (8.0,  15.6374),
    ('QSR', 'FIVE GUYS'):               (10.0,  14.6),
    ('QSR', 'SUBWAY'):                   (22.0,  13.6),
    ('QSR', 'SHAKE SHACK'):              (5.0,  11.9),
    ('QSR', 'DAIRY QUEEN'):             (10.0,   7.3),
    ('QSR', 'RAISING CANES CHICKEN FINGERS'): (8.0, 7.1),
    ('QSR', 'KRISPY KREME'):             (6.0,   8.5),
    ('QSR', 'ARBYS'):                     (8.0,  13.0),
    ('QSR', 'WENDYS'):                   (15.0,  13.0),
    ('QSR', 'CRUMBL COOKIES'):            (4.0,  10.0),
    ('QSR', 'IN-N-OUT BURGER'):           (5.0,  12.0),
    ('QSR', 'CULVERS'):                   (3.0,   8.0),
    ('QSR', 'TROPICAL SMOOTHIE CAFE'):    (3.0,   8.0),
    ('QSR', 'JACK IN THE BOX'):           (5.0,  10.0),
    ('QSR', 'JOLLIBEE'):                  (1.5,   7.0),
    ('QSR', 'BASKIN ROBBINS'):            (5.0,  12.0),
    ('QSR', 'CHURCHS TEXAS CHICKEN'):     (2.0,   8.0),
    ('QSR', 'BUFFALO WILD WINGS'):        (8.0,  14.0),
    ('QSR', 'JIMMY JOHNS'):               (6.0,  12.0),
    ('QSR', 'DUTCH BROS COFFEE'):          (4.0,  10.0),
    ('QSR', 'PANDA EXPRESS'):              (8.0,  14.0),
    ('QSR', 'FIREHOUSE SUBS'):            (4.0,  10.0),
    ('QSR', 'SMOOTHIE KING'):             (3.0,   9.0),
    ('QSR', 'HARDEES'):                    (4.0,  10.0),
    ('QSR', 'BUONA ITALIAN BEEF'):         (0.5,   6.0),
    ('QSR', 'CINNABON'):                   (3.0,   9.0),
    ('QSR', 'NESPRESSO'):                  (3.0,   9.0),
    ('QSR', 'MRBEAST BURGER'):             (1.0,   7.0),

    # ── WHERE THEY SHOP ──────────────────────────────────────────────────
    ('WHERE THEY SHOP', 'TARGET'): (47.5, 79.46),
    ('WHERE THEY SHOP', 'WALMART'): (88.0, 79.24),
    ('WHERE THEY SHOP', 'ALBERTSONS'):                     (8.0,  39.9839),
    ('WHERE THEY SHOP', 'COSTCO'):                        (28.0,  38.7695),
    ('WHERE THEY SHOP', 'CVS'): (33.3, 38.4184),
    ('WHERE THEY SHOP', 'PUBLIX'):                        (12.0,  38.3802),
    ('WHERE THEY SHOP', 'EBAY'):                          (12.0,  37.2452),
    ('WHERE THEY SHOP', 'SEPHORA'):                       (12.0,  36.7636),
    ('WHERE THEY SHOP', 'LOWES'): (28.0, 36.3597),
    ('WHERE THEY SHOP', 'HOME DEPOT'): (36.0, 36.2341),
    ('WHERE THEY SHOP', 'WAYFAIR'):                        (8.0,  36.0789),
    ('WHERE THEY SHOP', 'MACYS'):                         (15.0,  36.0311),
    ('WHERE THEY SHOP', 'SHEIN'):                         (12.0,  35.8332),
    ('WHERE THEY SHOP', 'ETSY'):                           (7.0,  35.0928),
    ('WHERE THEY SHOP', 'IKEA'):                          (10.0,  34.8778),
    ('WHERE THEY SHOP', 'TEMU'):                          (10.0,  34.8131),
    ('WHERE THEY SHOP', 'PACSUN'):                         (2.5,  33.1014),
    ('WHERE THEY SHOP', 'OPTICSPLANET'):                   (1.0,  32.4233),
    ('WHERE THEY SHOP', 'QVC'):                            (8.0,  32.3199),
    ('WHERE THEY SHOP', 'YVES SAINT LAURENT'):             (0.5,  32.1242),
    ('WHERE THEY SHOP', 'OVERSTOCK'):                      (3.0,  31.6659),
    ('WHERE THEY SHOP', 'SAKS FIFTH AVENUE'):              (2.5,  31.4314),
    ('WHERE THEY SHOP', 'REVOLVE'):                        (2.0,  31.0485),
    ('WHERE THEY SHOP', 'BURBERRY'):                       (0.5,  30.5036),
    ('WHERE THEY SHOP', 'MEIJER'):                         (5.0,  30.1719),
    ('WHERE THEY SHOP', 'MERCADO LIBRE'):                  (0.5,  30.0418),
    ('WHERE THEY SHOP', 'PAVILIONS'):                      (1.5,  29.9116),
    ('WHERE THEY SHOP', 'SWAROVSKI'):                      (2.0,  29.8853),
    ('WHERE THEY SHOP', 'TRADER JOES'): (16.0, 29.3668),
    ('WHERE THEY SHOP', 'YOOX'):                           (0.3,  29.3519),
    ('WHERE THEY SHOP', 'BEST BUY'):                      (20.0,  29.2811),
    ('WHERE THEY SHOP', 'ACADEMY SPORTS + OUTDOORS'):      (4.0,  28.5627),
    ('WHERE THEY SHOP', 'WHOLE FOODS MARKET'): (12.0, 28.421),
    ('WHERE THEY SHOP', 'BALENCIAGA'):                     (0.3,  28.3079),
    ('WHERE THEY SHOP', 'WILLIAMS-SONOMA'):                (3.0,  27.7981),
    ('WHERE THEY SHOP', 'ADVANCE AUTO PARTS'):             (3.0,  27.1312),
    ('WHERE THEY SHOP', 'TRAMONTINA'):                     (1.0,  27.0919),
    ('WHERE THEY SHOP', 'ALLMODERN'):                      (1.0,  26.858),
    ('WHERE THEY SHOP', 'ALEXANDER MCQUEEN'):              (0.3,  26.8509),
    ('WHERE THEY SHOP', 'RESTORATION HARDWARE'):           (3.0,  26.7),
    ('WHERE THEY SHOP', 'SAKS OFF 5TH'):                   (2.0,  26.4),
    ('WHERE THEY SHOP', 'GILT'):                            (1.0,  26.2),
    ('WHERE THEY SHOP', 'THE VITAMIN SHOPPE'):              (3.0,  25.0),
    ('WHERE THEY SHOP', 'FWRD'):                            (0.5,  24.0),
    ('WHERE THEY SHOP', 'GOAT'):                            (2.0,  25.0),
    ('WHERE THEY SHOP', '1STDIBS'):                         (0.5,  24.0),
    ('WHERE THEY SHOP', 'LYST'):                            (0.5,  23.0),
    ('WHERE THEY SHOP', 'BOTTEGA VENETA'):                  (0.3,  24.7),
    ('WHERE THEY SHOP', 'DILLARDS'):                        (5.0,  24.0),
    ('WHERE THEY SHOP', 'FOOT LOCKER'):                     (6.0,  25.0),
    ('WHERE THEY SHOP', 'HSN'):                             (5.0,  25.0),
    ('WHERE THEY SHOP', 'SEARS'):                           (3.0,  23.0),
    ('WHERE THEY SHOP', 'COSTCO OPTICAL'):                  (8.0,  26.8),
    ('WHERE THEY SHOP', 'WOLF & BADGER'):                   (0.3,  22.0),
    ('WHERE THEY SHOP', 'ROSS DRESS FOR LESS'):             (8.0,  25.0),
    ('WHERE THEY SHOP', 'ULTA BEAUTY'):                    (10.0,  26.0),
    ('WHERE THEY SHOP', 'TOYS R US'):                       (3.0,  22.0),
    ('WHERE THEY SHOP', 'GIVENCHY'):                        (0.3,  19.2),
    ('WHERE THEY SHOP', 'GNC'):                             (5.0,  24.0),
    ('WHERE THEY SHOP', 'FIVE BELOW'):                      (8.0,  25.0),
    ('WHERE THEY SHOP', 'DIOR'):                            (0.5,  13.3),
    ('WHERE THEY SHOP', 'MAISON MARGIELA'):                 (0.2,  20.0),
    ('WHERE THEY SHOP', 'HARRODS'):                         (0.2,  17.0),
    ('WHERE THEY SHOP', 'WALGREENS'): (30.3, 30.0),
    ('WHERE THEY SHOP', 'HOME GOODS'):                     (10.0,  26.0),
    ('WHERE THEY SHOP', 'KOHLS'):                          (12.0,  27.0),
    ('WHERE THEY SHOP', 'CHEWY'):                           (8.0,  25.0),
    ('WHERE THEY SHOP', 'POSHMARK'):                        (3.0,  23.0),
    ('WHERE THEY SHOP', 'NFL SHOP'):                        (3.0,  22.0),
    ('WHERE THEY SHOP', 'WEGMANS'):                         (3.0,  22.0),
    ('WHERE THEY SHOP', 'WINN-DIXIE'):                      (3.0,  22.0),
    ('WHERE THEY SHOP', 'ASOS'):                            (2.0,  22.0),

    # ── WHERE THEY DINE ──────────────────────────────────────────────────
    ('WHERE THEY DINE', 'THE CHEESECAKE FACTORY'):                  (6.0,  0.716),
    ('WHERE THEY DINE', 'TEXAS ROADHOUSE'):                         (5.0,  0.6815),
    ('WHERE THEY DINE', 'OLIVE GARDEN'):                            (8.0,  0.6814),
    ('WHERE THEY DINE', 'RUTHS CHRIS STEAK HOUSE'):                 (2.0,  0.6341),
    ('WHERE THEY DINE', 'GOLDEN CORRAL'):                           (4.0,  0.5814),
    ('WHERE THEY DINE', 'FOGO DE CHAO'):                            (1.0,  0.5651),
    ('WHERE THEY DINE', 'CHILIS'):                                  (6.0,  0.4785),
    ('WHERE THEY DINE', 'THE CAPITAL GRILLE'):                      (1.0,  0.477),
    ('WHERE THEY DINE', 'BJS RESTAURANT & BREWHOUSE'):              (2.0,  0.4532),
    ('WHERE THEY DINE', 'CRACKER BARREL'):                          (4.0,  0.368),
    ('WHERE THEY DINE', 'RED LOBSTER'):                             (4.0,  0.3532),
    ('WHERE THEY DINE', 'APPLEBEES GRILL + BAR'):                   (5.0,  0.3115),
    ('WHERE THEY DINE', 'OUTBACK STEAKHOUSE'):                      (4.0,  0.2005),
    ('WHERE THEY DINE', 'CALIFORNIA PIZZA KITCHEN'):                (1.5,  0.2147),
    ('WHERE THEY DINE', 'BENIHANA'):                                (1.0,  0.2276),
    ('WHERE THEY DINE', 'IHOP'):                                    (5.0,  0.17),
    ('WHERE THEY DINE', 'WAFFLE HOUSE'):                            (5.0,  0.10),
    ('WHERE THEY DINE', 'P.F. CHANGS'):                             (2.0,  0.10),
    ('WHERE THEY DINE', 'LONGHORN STEAKHOUSE'):                     (4.0,  0.10),
    ('WHERE THEY DINE', 'TGI FRIDAYS'):                             (3.0,  0.10),
    ('WHERE THEY DINE', 'HOOTERS'):                                 (2.0,  0.10),
    ('WHERE THEY DINE', 'RED ROBIN'):                               (3.0,  0.10),
    ('WHERE THEY DINE', 'BOB EVANS'):                               (2.0,  0.14),
    ('WHERE THEY DINE', 'BONEFISH GRILL'):                          (1.5,  0.12),
    ('WHERE THEY DINE', 'YARD HOUSE'):                              (1.5,  0.12),

    # ══════════════════════════════════════════════════════════════════════
    # MOST PURCHASED BRANDS — known brand corrections
    # ══════════════════════════════════════════════════════════════════════
    ('MOST PURCHASED BRANDS', '1800FLOWERS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'A.L.C.'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'A.P.C.'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ABERCROMBIE & FITCH'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'ACE AND TATE'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'ACNE STUDIOS'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ADIDAS'): (18.0, 22.3),
    ('MOST PURCHASED BRANDS', 'ADVIL'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'AGENT PROVOCATEUR'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ALERT1'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'ALESSI'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'ALICE + OLIVIA'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'ALLBIRDS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'ALLSAINTS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'ALO YOGA'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'AMERICAN EAGLE'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'AMERICAN TOURISTER'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'AND OTHER STORIES'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'ANINE BING'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'ANKER'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'ANN SUMMERS'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ANN TAYLOR LOFT'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'ANTHROPOLOGIE'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'AQUAPHOR'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'ARHAUS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'ARM & HAMMER'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'ARMOR LUX'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ASHER GOLF'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'ASICS'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'ASPINAL OF LONDON'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'ATHLETA'): (3.0, 13.2),
    ('MOST PURCHASED BRANDS', 'AUDEMARS PIGUET'): (0.05, 2.3),
    ('MOST PURCHASED BRANDS', 'AUGUST HOME'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'AVEENO'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'AVOLT'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'AVON'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'BA&SH'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'BANANA REPUBLIC'): (4.0, 15.7),
    ('MOST PURCHASED BRANDS', 'BAND AID'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'BAND-AID'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'BANTER'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'BAPE'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'BARILLA'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'BATH & BODY WORKS'): (18.0, 22.3),
    ('MOST PURCHASED BRANDS', 'BATHER'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'BATSHEVA'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'BAY ALARM MEDICAL'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'BEN SHERMAN'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'BENADRYL'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'BERGHAUS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'BETTY CROCKER'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'BEYOND MEAT'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'BLACK DIAMOND'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'BOMBAS'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'BONOBOS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'BOOHOO'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'BOUGUESSA'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'BOUNTY'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'BRANDY MELVILLE'): (1.5, 9.8),
    ('MOST PURCHASED BRANDS', 'BRITA'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'BROOKS BROTHERS'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'BROOKS SHOES'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'BUD LIGHT'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'BUDWEISER'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'BURT\'S BEES'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'BYREDO'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'CALPAK'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'CALVIN KLEIN'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'CAMPBELLS'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'CANADA DRY'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CARHARTT'): (9.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CARTERS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CASE MATE'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'CASETIFY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'CASPER'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'CASTLERY'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'CELESTIAL SEASONINGS'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'CERAVE'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CETAPHIL'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CHAMPION'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'CHARMIN'): (13.5, 19.9),
    ('MOST PURCHASED BRANDS', 'CHEERIOS'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'CHEEZ-IT'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'CHOBANI'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CHOPARD'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'CITIZEN WATCH'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'CLARITIN'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'CLEARASIL'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'CLIF BAR'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'CLOROX'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'CLUB MONACO'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'COACH'): (6.0, 15.7),
    ('MOST PURCHASED BRANDS', 'COBIAN'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'COCA COLA'): (28.0, 22.5),
    ('MOST PURCHASED BRANDS', 'COLE HAAN'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'COLUMBIA'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'COMME DES GARCONS'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'COMMODITY'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'CONVERSE'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'CORELLE'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'CORONA'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'COS'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'COTTON:ON'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'COUNTRY ROAD'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'COURREGES'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'COVERGIRL'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'CREST'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'CRICUT'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'CROCS'): (16.0, 19.9),
    ('MOST PURCHASED BRANDS', 'CUTTER & BUCK'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'CYNTHIA ROWLEY'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'DANNON'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'DAWN'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'DAYQUIL'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'DE CECCO'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'DEGREE'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'DESIGUAL'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'DIAL'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'DIESEL'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'DIGIORNO'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'DOLCE VITA'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'DORITOS'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'DOVE BEAUTY'): (18.0, 22.3),
    ('MOST PURCHASED BRANDS', 'DOVE CHOCOLATE'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'DOWNY'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'DR PEPPER'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'DREAMETECH'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ECOVACS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'ED HARDY'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'EDDIE BAUER'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'EMILIO PUCCI'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'EUCERIN'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'EUFY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'EXPRESS'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'FABLETICS'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'FARROW & BALL'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'FEAR OF GOD'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'FEBREZE'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'FEVER TREE'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'FIGS'): (1.5, 8.9),
    ('MOST PURCHASED BRANDS', 'FITBIT'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'FOLGERS'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'FOOTJOY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'FOR LOVE & LEMONS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'FRAME'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'FREE PEOPLE'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'FRENCH CONNECTION USA'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'FRITO LAY'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'FROSTED FLAKES'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'FRUGI'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'FRUIT OF THE LOOM'): (18.0, 22.3),
    ('MOST PURCHASED BRANDS', 'GAIN'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'GANT'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'GAP'): (8.0, 22.0),
    ('MOST PURCHASED BRANDS', 'GARNIER'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'GATORADE'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'GHOSTBED'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'GILLETTE'): (21.0, 22.0),
    ('MOST PURCHASED BRANDS', 'GLAD'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'GLOSSIER'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'GODINGER'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'GODIVA'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'GOOD AMERICAN'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'GOOSE ISLAND BEER'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'GUESS'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'H&M'): (12.0, 22.0),
    ('MOST PURCHASED BRANDS', 'HAAGEN-DAZS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'HALLMARK'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'HANES'): (12.0, 23.3),
    ('MOST PURCHASED BRANDS', 'HARIBO'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'HARMLESS HARVEST'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'HAWX PEST CONTROL'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'HEAD & SHOULDERS'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'HEAD SPORTING GOODS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'HEINZ'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'HERSCHEL'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'HERSCHEL SUPPLY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'HERSHEYS'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'HILL HOUSE HOME'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'HOKA'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'HOLLISTER CO'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'HOT POCKETS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'HUGO BOSS'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'HUNTER BOOTS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'INSTANT POT'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'INTIMISSIMI'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'IRISH SPRING'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'ISABEL MARANT'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'IZOD'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'J.CREW'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'J.JILL'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'J.MCLAUGHLIN'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'JACK DANIELS'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'JENNI KAYNE'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'JENNIFER FISHER JEWELRY'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'JERGENS'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'JO MALONE'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'JOES JEANS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'JOHNSON & JOHNSON'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'JOMA'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'JOS. A BANK'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'JOSS & MAIN'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'JUICY COUTURE'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'KARL LAGERFELD'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'KASA SMART'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'KATE SPADE'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'KATE SPADE OUTLET'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'KENDRA SCOTT'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'KIND'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'KIT KAT'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'KITCHENAID'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'KITH'): (0.8, 6.8),
    ('MOST PURCHASED BRANDS', 'KRAFT'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'KRUPS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'L.L.BEAN'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'LA BLANCA'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'LA ROCHE POSAY'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'LALA BERLIN'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'LANCOME'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'LANDS END'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'LANE BRYANT'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'LARESAR'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'LAYS'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'LAZY OAF'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LE CREUSET'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'LEAN CUISINE'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'LEVER 2000'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'LEVI'): (20.0, 22.5),
    ('MOST PURCHASED BRANDS', 'LIFE ALERT'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'LIFELINE'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'LILLY PULITZER'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'LNDR'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'LO & SONS'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LOCAL ECLECTIC'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'LOCK & CO. HATTERS'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'LOLA CASADEMUNT'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'LONG WHARF SUPPLY CO.'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'LOREAL PARIS'): (20.0, 22.5),
    ('MOST PURCHASED BRANDS', 'LOVISA'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'LUCCHESE'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LUCKY CHARMS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'LUGGAGE ONLINE'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LUISA CERANO'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'LULULEMON'): (8.0, 13.2),
    ('MOST PURCHASED BRANDS', 'LULULUN'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LUMEN'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LUMIN'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'LUSH'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'LYSOL'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'M&MS'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'MAAMGIC'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MAC COSMETICS'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'MADEWELL'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'MADISON REED'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'MAIDENFORM'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'MAISON LOUIS MARIE'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'MALBON'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MARC ANTHONY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'MARC JACOBS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'MATT & NAT'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MAUI JIM'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'MAYBELLINE'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'MCCORMICK'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'MEDICAL ALERT'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'MENTOS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'MERI MERI'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MERRELL'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'METHOD'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'MICHAEL KORS'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'MILKY WAY'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'MISSONI'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'MONCLER'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'MONKI'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MOUNTAIN DEW'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'MOUNTAIN WAREHOUSE'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MOVADO'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'MR CLEAN'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'MRS MEYERS'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'MUCINEX'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'MUJI USA'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'MUNCHKIN BABY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'NATIVE UNION'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'NATURE VALLEY'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'NESTLE'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'NESTLE AERO'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'NEUTROGENA'): (20.0, 22.5),
    ('MOST PURCHASED BRANDS', 'NEW & LINGWOOD'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'NEW BALANCE'): (11.0, 19.9),
    ('MOST PURCHASED BRANDS', 'NEW ERA CAP'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'NIKE'): (28.0, 24.1),
    ('MOST PURCHASED BRANDS', 'NOAH'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'NORMA KAMALI'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'NOXZEMA'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'NUGGET'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'OAK + FORT'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'OGX'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'OLAY'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'OLD NAVY'): (18.0, 22.7),
    ('MOST PURCHASED BRANDS', 'OLD SPICE'): (20.0, 22.5),
    ('MOST PURCHASED BRANDS', 'OLLY'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'ORAL B'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'ORE-IDA'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'OREO'): (15.0, 22.0),
    ('MOST PURCHASED BRANDS', 'ORIBE'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'OTTERBOX'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'OUAI'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'OURA RING'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'OXICLEAN'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'OXKNIT'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'PAIGE JEANS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'PANDORA JEWELRY'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'PANOXYL'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'PANTENE'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'PARADE UNDERWEAR'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'PATAGONIA'): (4.0, 15.0),
    ('MOST PURCHASED BRANDS', 'PELOTON APPAREL'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'PENDLETON'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'PEPPERIDGE FARM GOLDFISH'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'PEPSI'): (22.0, 22.0),
    ('MOST PURCHASED BRANDS', 'PEPTO BISMOL'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'PERRICONE MD'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'PILLSBURY'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'PIMAX'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'PINE SOL'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'PLAYTEX'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'PLEDGE'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'PLUFFI SLIPPERS'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'POTTERY BARN'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'PREGO'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'PRINGLES'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'PROACTIV'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'PROGRESSO'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'PUMA'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'QUAKER'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'QUIKSILVER'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'R13'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'RAG & BONE'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'RAGU'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'RALPH LAUREN'): (7.0, 17.1),
    ('MOST PURCHASED BRANDS', 'RAWLINGS'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'RAY-BAN'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'RED BULL'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'RED KAP WORKWEAR'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'REESES'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'REFORMATION'): (1.5, 8.9),
    ('MOST PURCHASED BRANDS', 'REVLON'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'REYNOLDS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'RHODE SKIN'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'RIFLE PAPER CO.'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'RITZ CRACKERS'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'ROBITUSSIN'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'ROCKPORT'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'ROCKSTAR ENERGY'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'ROKFORM CASES'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'ROOMMATES'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ROOTS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'RUBBERMAID'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'RYKA'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'SAM EDELMAN'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'SARGENTO'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'SAVAGE X FENTY'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'SECRET'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'SENSODYNE'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'SEVENTH GENERATION'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'SHARK'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'SKECHERS'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'SKITTLES'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'SKULLCANDY'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'SNAPPLE'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'SNICKERS'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'SOAP & GLORY'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'SOREL'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'SPANX'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'SPRITE'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'SPROUT LIVING'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'SPYDER'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'ST. IVES'): (4.0, 13.2),
    ('MOST PURCHASED BRANDS', 'STARBURST'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'STATE BAGS'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'STAUD'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'STETSON'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'STOUFFERS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'STRIDEX'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'SUAVE'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'SUN BUM'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'SWATCH'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'TALBOTS'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'TAYLORMADE GOLF'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'TELEFLORA'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'TELFAR'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'TERMINIX'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'THE HONEST COMPANY'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'THE JESSICA SIMPSON COLLECTION'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'THE NORTH FACE'): (8.0, 18.5),
    ('MOST PURCHASED BRANDS', 'THEORY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'THERABODY'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'THRIVE CAUSEMETICS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'THULE'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'TIDE'): (19.0, 22.3),
    ('MOST PURCHASED BRANDS', 'TOMMY BAHAMA'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'TOMMY HILFIGER'): (7.0, 16.4),
    ('MOST PURCHASED BRANDS', 'TOMMY JOHN'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'TOMS FOOTWEAR'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'TOPO CHICO'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'TORY BURCH'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'TREK BIKES'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'TRESEMME'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'TRISCUIT'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'TRUE RELIGION'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'TUCKERNUCK'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'TUMS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'TWIX'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'TYLENOL'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'UGG'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'UNDER ARMOUR'): (10.0, 18.5),
    ('MOST PURCHASED BRANDS', 'UNIQLO'): (4.0, 15.0),
    ('MOST PURCHASED BRANDS', 'URBAN PLANET'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'US POLO ASSN'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'VALENTINO'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'VANS'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'VASELINE'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'VEJA SNEAKERS'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'VELVEETA'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'VICKS'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'VICTORIAS SECRET'): (12.0, 22.0),
    ('MOST PURCHASED BRANDS', 'VOLUSPA'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'WEEKDAY'): (0.5, 5.0),
    ('MOST PURCHASED BRANDS', 'WEST ELM'): (2.0, 9.8),
    ('MOST PURCHASED BRANDS', 'WESTERN MOUNTAINEERING'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'WET N WILD'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'WHEAT THINS'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'WHO GIVES A CRAP'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'WILDFOX COUTURE'): (0.2, 3.2),
    ('MOST PURCHASED BRANDS', 'WINDEX'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'WRANGLER'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'YOPLAIT'): (5.0, 15.0),
    ('MOST PURCHASED BRANDS', 'ZADIG & VOLTAIRE'): (0.3, 3.8),
    ('MOST PURCHASED BRANDS', 'ZARA'): (8.0, 17.1),
    ('MOST PURCHASED BRANDS', 'ZARA HOME'): (1.0, 8.0),
    ('MOST PURCHASED BRANDS', 'ZENBIVY'): (0.1, 2.6),
    ('MOST PURCHASED BRANDS', 'ZENNI OPTICAL'): (3.0, 11.5),
    ('MOST PURCHASED BRANDS', 'ZIPLOC'): (12.0, 19.9),
    ('MOST PURCHASED BRANDS', 'ZYRTEC'): (5.0, 15.0),

    # ══════════════════════════════════════════════════════════════════════
    # AUTOMOBILE — additional corrections
    # ══════════════════════════════════════════════════════════════════════
    ('AUTOMOBILE', 'ACURA'): (2.0, 15.0),
    ('AUTOMOBILE', 'ALFA ROMEO'): (0.3, 6.5),
    ('AUTOMOBILE', 'AUTOTRADER'): (5.0, 22.8),
    ('AUTOMOBILE', 'BUICK'): (2.0, 15.0),
    ('AUTOMOBILE', 'CADILLAC'): (2.0, 15.0),
    ('AUTOMOBILE', 'CARFAX'): (8.0, 27.0),
    ('AUTOMOBILE', 'CARGURUS'): (5.0, 22.8),
    ('AUTOMOBILE', 'CARS.COM'): (5.0, 22.8),
    ('AUTOMOBILE', 'CARVANA'): (3.0, 20.0),
    ('AUTOMOBILE', 'CHEVROLET'): (13.0, 39.0),
    ('AUTOMOBILE', 'CHRYSLER'): (2.0, 15.0),
    ('AUTOMOBILE', 'DODGE'): (4.0, 21.4),
    ('AUTOMOBILE', 'EDMUNDS'): (3.0, 20.0),
    ('AUTOMOBILE', 'FISKER'): (0.1, 5.5),
    ('AUTOMOBILE', 'FORD'): (14.0, 42.0),
    ('AUTOMOBILE', 'GENESIS'): (0.5, 7.5),
    ('AUTOMOBILE', 'GMC'): (4.0, 21.4),
    ('AUTOMOBILE', 'HONDA'): (15.0, 45.0),
    ('AUTOMOBILE', 'HYUNDAI'): (6.0, 24.2),
    ('AUTOMOBILE', 'INFINITI'): (1.5, 12.5),
    ('AUTOMOBILE', 'JAGUAR'): (0.5, 7.5),
    ('AUTOMOBILE', 'JEEP'): (6.0, 24.2),
    ('AUTOMOBILE', 'KELLEY BLUE BOOK'): (5.0, 22.8),
    ('AUTOMOBILE', 'KIA'): (5.0, 22.8),
    ('AUTOMOBILE', 'LAND ROVER'): (0.5, 7.5),
    ('AUTOMOBILE', 'LEXUS'): (3.0, 20.0),
    ('AUTOMOBILE', 'LINCOLN'): (1.5, 12.5),
    ('AUTOMOBILE', 'LUCID MOTORS'): (0.1, 5.5),
    ('AUTOMOBILE', 'MASERATI'): (0.2, 6.0),
    ('AUTOMOBILE', 'MAZDA'): (3.0, 20.0),
    ('AUTOMOBILE', 'MINI COOPER'): (1.0, 10.0),
    ('AUTOMOBILE', 'MITSUBISHI'): (2.0, 15.0),
    ('AUTOMOBILE', 'NISSAN'): (8.0, 27.0),
    ('AUTOMOBILE', 'POLESTAR'): (0.2, 6.0),
    ('AUTOMOBILE', 'RAM'): (4.0, 21.4),
    ('AUTOMOBILE', 'RIVIAN'): (0.3, 6.5),
    ('AUTOMOBILE', 'SHIFT'): (0.5, 7.5),
    ('AUTOMOBILE', 'SUBARU'): (4.0, 21.4),
    ('AUTOMOBILE', 'TESLA'): (3.0, 20.0),
    ('AUTOMOBILE', 'TOYOTA'): (16.0, 48.0),
    ('AUTOMOBILE', 'TRUECAR'): (3.0, 20.0),
    ('AUTOMOBILE', 'VOLKSWAGON'): (4.0, 21.4),
    ('AUTOMOBILE', 'VOLVO'): (2.0, 15.0),
    ('AUTOMOBILE', 'VROOM'): (1.0, 10.0),

    # ══════════════════════════════════════════════════════════════════════
    # GAMES — additional corrections
    # ══════════════════════════════════════════════════════════════════════
    ('GAMES', 'AMONG US'): (5.0, 20.8),
    ('GAMES', 'ANIMAL CROSSING'): (5.0, 20.8),
    ('GAMES', 'APEX LEGENDS'): (4.0, 19.4),
    ('GAMES', 'ARK'): (3.0, 18.0),
    ('GAMES', 'BALDURS GATE'): (3.0, 18.0),
    ('GAMES', 'CANDY CRUSH'): (12.0, 33.0),
    ('GAMES', 'COUNTER-STRIKE'): (3.0, 18.0),
    ('GAMES', 'CYBERPUNK 2077'): (3.0, 18.0),
    ('GAMES', 'DESTINY'): (3.0, 18.0),
    ('GAMES', 'DIABLO'): (4.0, 19.4),
    ('GAMES', 'DOOM'): (4.0, 19.4),
    ('GAMES', 'DRAGON QUEST BUILDERS'): (1.0, 10.0),
    ('GAMES', 'EA SPORTS NHL'): (2.0, 14.0),
    ('GAMES', 'ELDEN RING'): (3.0, 18.0),
    ('GAMES', 'FALL GUYS'): (3.0, 18.0),
    ('GAMES', 'FIFA'): (5.0, 20.8),
    ('GAMES', 'GOD OF WAR'): (3.0, 18.0),
    ('GAMES', 'HALO'): (4.0, 19.4),
    ('GAMES', 'HEROES OF THE STORM'): (1.0, 10.0),
    ('GAMES', 'HOGWARTS LEGACY'): (3.0, 18.0),
    ('GAMES', 'MADDEN'): (5.0, 20.8),
    ('GAMES', 'MONSTER HUNTER'): (3.0, 18.0),
    ('GAMES', 'NBA 2K'): (4.0, 19.4),
    ('GAMES', 'NINTENDO'): (15.0, 39.0),
    ('GAMES', 'NINTENDO SWITCH'): (12.0, 33.0),
    ('GAMES', 'PALWORLD'): (2.0, 14.0),
    ('GAMES', 'PLAYSTATION'): (15.0, 39.0),
    ('GAMES', 'POKEMON'): (10.0, 29.0),
    ('GAMES', 'POKEMON GO'): (8.0, 25.0),
    ('GAMES', 'POPPY PLAYTIME'): (2.0, 14.0),
    ('GAMES', 'REC ROOM PLAY WITH FRIENDS'): (2.0, 14.0),
    ('GAMES', 'RESIDENT EVIL'): (5.0, 20.8),
    ('GAMES', 'ROCKET LEAGUE'): (3.0, 18.0),
    ('GAMES', 'SKYRIM'): (5.0, 20.8),
    ('GAMES', 'SPIDER-MAN'): (5.0, 20.8),
    ('GAMES', 'SQUARE ENIX GAMES'): (3.0, 18.0),
    ('GAMES', 'STARDEW VALLEY'): (3.0, 18.0),
    ('GAMES', 'TETRIS'): (8.0, 25.0),
    ('GAMES', 'THE LAST OF US'): (3.0, 18.0),
    ('GAMES', 'THE OUTER WORLDS'): (2.0, 14.0),
    ('GAMES', 'THE SIMS'): (5.0, 20.8),
    ('GAMES', 'WARCRAFT'): (5.0, 20.8),
    ('GAMES', 'WORDLE'): (10.0, 29.0),
    ('GAMES', 'WORLD OF WARCRAFT'): (3.0, 18.0),
    ('GAMES', 'XBOX'): (12.0, 33.0),
    ('GAMES', 'Z8GAMES'): (0.3, 6.5),
    ('GAMES', 'ZELDA'): (5.0, 20.8),

    # ══════════════════════════════════════════════════════════════════════
    # AMUSEMENT PARKS
    # ══════════════════════════════════════════════════════════════════════
    ('AMUSEMENT PARKS', 'BUSCH GARDENS'): (1.5, 1.1),
    ('AMUSEMENT PARKS', 'CEDAR POINT'): (1.5, 1.1),
    ('AMUSEMENT PARKS', 'DAVE AND BUSTERS'): (3.0, 1.7),
    ('AMUSEMENT PARKS', 'DISNEY WORLD'): (5.0, 2.5),
    ('AMUSEMENT PARKS', 'DISNEYLAND'): (4.0, 2.1),
    ('AMUSEMENT PARKS', 'DOLLYWOOD'): (1.0, 0.9),
    ('AMUSEMENT PARKS', 'HERSHEYPARK'): (1.0, 0.9),
    ('AMUSEMENT PARKS', 'KNOTT\'S BERRY FARM'): (1.0, 0.9),
    ('AMUSEMENT PARKS', 'LEGOLAND'): (1.0, 0.9),
    ('AMUSEMENT PARKS', 'SEA WORLD'): (2.0, 1.3),
    ('AMUSEMENT PARKS', 'SIX FLAGS'): (2.0, 1.3),
    ('AMUSEMENT PARKS', 'SIX FLAGS AMERICA HURRICANE HARBOR BOWIE'): (1.5, 1.1),
    ('AMUSEMENT PARKS', 'SKY ZONE TRAMPOLINE PARK'): (1.5, 1.1),
    ('AMUSEMENT PARKS', 'TOP GOLF'): (3.0, 1.7),
    ('AMUSEMENT PARKS', 'UNIVERSAL ORLANDO RESORT'): (3.0, 1.7),
    ('AMUSEMENT PARKS', 'UNIVERSAL STUDIOS HOLLYWOOD'): (2.5, 1.5),

    # ══════════════════════════════════════════════════════════════════════
    # INTEREST
    # ══════════════════════════════════════════════════════════════════════
    ('INTEREST', 'ANIME'): (12.0, 15.2),
    ('INTEREST', 'ART'): (25.0, 29.5),
    ('INTEREST', 'ARTIFICIAL INTELLIGENCE'): (30.0, 35.0),
    ('INTEREST', 'ASTROLOGY'): (15.0, 18.5),
    ('INTEREST', 'BAKING'): (30.0, 35.0),
    ('INTEREST', 'BASEBALL'): (20.0, 24.0),
    ('INTEREST', 'BASKETBALL'): (25.0, 29.5),
    ('INTEREST', 'BEAUTY'): (40.0, 46.0),
    ('INTEREST', 'BOARD GAMES'): (15.0, 18.5),
    ('INTEREST', 'BOXING'): (8.0, 10.8),
    ('INTEREST', 'BUSINESS'): (45.0, 51.5),
    ('INTEREST', 'CAMPING'): (20.0, 24.0),
    ('INTEREST', 'CARS'): (30.0, 35.0),
    ('INTEREST', 'CLASSICAL MUSIC'): (8.0, 10.8),
    ('INTEREST', 'CLIMATE'): (15.0, 18.5),
    ('INTEREST', 'COCKTAILS'): (20.0, 24.0),
    ('INTEREST', 'COFFEE'): (60.0, 68.0),
    ('INTEREST', 'COMEDY'): (35.0, 40.5),
    ('INTEREST', 'COMIC BOOKS'): (8.0, 10.8),
    ('INTEREST', 'COOKING'): (55.0, 62.5),
    ('INTEREST', 'COUNTRY MUSIC'): (20.0, 24.0),
    ('INTEREST', 'CRAFT BEER'): (15.0, 18.5),
    ('INTEREST', 'CRAFTS'): (25.0, 29.5),
    ('INTEREST', 'CRYPTOCURRENCY'): (10.0, 13.0),
    ('INTEREST', 'CYCLING'): (15.0, 18.5),
    ('INTEREST', 'DANCE'): (12.0, 15.2),
    ('INTEREST', 'DIY'): (40.0, 46.0),
    ('INTEREST', 'DOCUMENTARY'): (25.0, 29.5),
    ('INTEREST', 'EDUCATION'): (35.0, 40.5),
    ('INTEREST', 'ELECTRIC VEHICLES'): (10.0, 13.0),
    ('INTEREST', 'ELECTRONIC MUSIC'): (12.0, 15.2),
    ('INTEREST', 'ENVIRONMENT'): (20.0, 24.0),
    ('INTEREST', 'ESPORTS'): (8.0, 10.8),
    ('INTEREST', 'F1'): (8.0, 10.8),
    ('INTEREST', 'FANTASY'): (12.0, 15.2),
    ('INTEREST', 'FASHION'): (55.0, 62.5),
    ('INTEREST', 'FISHING'): (15.0, 18.5),
    ('INTEREST', 'FITNESS'): (45.0, 51.5),
    ('INTEREST', 'FOOD'): (60.0, 68.0),
    ('INTEREST', 'FOOTBALL'): (35.0, 40.5),
    ('INTEREST', 'FOOTWEAR'): (65.0, 73.5),
    ('INTEREST', 'GAMING'): (40.0, 46.0),
    ('INTEREST', 'GARDENING'): (30.0, 35.0),
    ('INTEREST', 'GOLF'): (8.0, 10.8),
    ('INTEREST', 'HAIR CARE'): (30.0, 35.0),
    ('INTEREST', 'HIKING'): (25.0, 29.5),
    ('INTEREST', 'HIP HOP'): (30.0, 35.0),
    ('INTEREST', 'HISTORY'): (25.0, 29.5),
    ('INTEREST', 'HOCKEY'): (8.0, 10.8),
    ('INTEREST', 'HOME DECOR'): (40.0, 46.0),
    ('INTEREST', 'HORROR'): (15.0, 18.5),
    ('INTEREST', 'HUNTING'): (10.0, 13.0),
    ('INTEREST', 'INFLUENCER STYLE'): (20.0, 24.0),
    ('INTEREST', 'INVESTING'): (25.0, 29.5),
    ('INTEREST', 'JAZZ'): (10.0, 13.0),
    ('INTEREST', 'K-POP'): (5.0, 7.5),
    ('INTEREST', 'LIVE EVENTS'): (45.0, 51.5),
    ('INTEREST', 'LUXURY FASHION'): (8.0, 10.8),
    ('INTEREST', 'MAKEUP'): (25.0, 29.5),
    ('INTEREST', 'MALL SHOPPING'): (50.0, 57.0),
    ('INTEREST', 'MEDITATION'): (12.0, 15.2),
    ('INTEREST', 'MENTAL HEALTH'): (30.0, 35.0),
    ('INTEREST', 'MINIMALISM'): (10.0, 13.0),
    ('INTEREST', 'MMA'): (8.0, 10.8),
    ('INTEREST', 'MOTORCYCLES'): (8.0, 10.8),
    ('INTEREST', 'MOVIES'): (65.0, 73.5),
    ('INTEREST', 'MUSEUMS'): (20.0, 24.0),
    ('INTEREST', 'MUSIC'): (70.0, 79.0),
    ('INTEREST', 'NAIL ART'): (10.0, 13.0),
    ('INTEREST', 'NASCAR'): (8.0, 10.8),
    ('INTEREST', 'NATURE'): (35.0, 40.5),
    ('INTEREST', 'NEWS'): (55.0, 62.5),
    ('INTEREST', 'ONLINE COMMUNITY'): (60.0, 68.0),
    ('INTEREST', 'OUTDOOR LIFE'): (40.0, 46.0),
    ('INTEREST', 'PARENTING'): (25.0, 29.5),
    ('INTEREST', 'PETS'): (45.0, 51.5),
    ('INTEREST', 'PHOTOGRAPHY'): (35.0, 40.5),
    ('INTEREST', 'PODCASTS'): (35.0, 40.5),
    ('INTEREST', 'POLITICS'): (50.0, 57.0),
    ('INTEREST', 'PUZZLES'): (20.0, 24.0),
    ('INTEREST', 'R&B'): (25.0, 29.5),
    ('INTEREST', 'READING'): (45.0, 51.5),
    ('INTEREST', 'READING DIGITAL MEDIA'): (50.0, 57.0),
    ('INTEREST', 'REAL ESTATE'): (20.0, 24.0),
    ('INTEREST', 'REALITY TV'): (25.0, 29.5),
    ('INTEREST', 'RELIGION'): (25.0, 29.5),
    ('INTEREST', 'RESTAURANTS'): (50.0, 57.0),
    ('INTEREST', 'ROCK'): (35.0, 40.5),
    ('INTEREST', 'ROMANCE'): (15.0, 18.5),
    ('INTEREST', 'RUNNING'): (20.0, 24.0),
    ('INTEREST', 'SCI-FI'): (15.0, 18.5),
    ('INTEREST', 'SCIENCE'): (25.0, 29.5),
    ('INTEREST', 'SECONDHAND CLOTHING'): (25.0, 29.5),
    ('INTEREST', 'SKINCARE'): (35.0, 40.5),
    ('INTEREST', 'SNEAKERS'): (35.0, 40.5),
    ('INTEREST', 'SOCCER'): (15.0, 18.5),
    ('INTEREST', 'SOCIAL MEDIA'): (80.0, 90.0),
    ('INTEREST', 'SPACE'): (15.0, 18.5),
    ('INTEREST', 'SPIRITUALITY'): (15.0, 18.5),
    ('INTEREST', 'SPORTS'): (55.0, 62.5),
    ('INTEREST', 'STREAMING'): (70.0, 79.0),
    ('INTEREST', 'STREETWEAR'): (10.0, 13.0),
    ('INTEREST', 'SUSTAINABILITY'): (20.0, 24.0),
    ('INTEREST', 'SWIMMING'): (20.0, 24.0),
    ('INTEREST', 'TATTOOS'): (12.0, 15.2),
    ('INTEREST', 'TEA'): (35.0, 40.5),
    ('INTEREST', 'TECHNOLOGY'): (50.0, 57.0),
    ('INTEREST', 'TENNIS'): (6.0, 8.6),
    ('INTEREST', 'THEATER'): (12.0, 15.2),
    ('INTEREST', 'THRIFT SHOPPING'): (20.0, 24.0),
    ('INTEREST', 'TRAVEL'): (55.0, 62.5),
    ('INTEREST', 'TRUE CRIME'): (25.0, 29.5),
    ('INTEREST', 'VEGANISM'): (5.0, 7.5),
    ('INTEREST', 'VINTAGE'): (15.0, 18.5),
    ('INTEREST', 'VOLUNTEERING'): (15.0, 18.5),
    ('INTEREST', 'WELLNESS'): (35.0, 40.5),
    ('INTEREST', 'WINE'): (25.0, 29.5),
    ('INTEREST', 'WOODWORKING'): (8.0, 10.8),
    ('INTEREST', 'WRESTLING'): (5.0, 7.5),
    ('INTEREST', 'YOGA'): (15.0, 18.5),

    # ══════════════════════════════════════════════════════════════════════
    # SPORTS ORGANIZATIONS
    # ══════════════════════════════════════════════════════════════════════
    ('SPORTS ORGANIZATIONS', 'F1'): (6.0, 24.0),
    ('SPORTS ORGANIZATIONS', 'MAJOR LEAGUE BASEBALL'): (20.0, 35.0),
    ('SPORTS ORGANIZATIONS', 'NASCAR'): (10.0, 24.0),
    ('SPORTS ORGANIZATIONS', 'NATIONAL BASKETBALL ASSOCIATION'): (25.0, 35.0),
    ('SPORTS ORGANIZATIONS', 'NATIONAL COLLEGIATE ATHLETIC ASSOCIATION'): (18.0, 32.0),
    ('SPORTS ORGANIZATIONS', 'NATIONAL FOOTBALL LEAGUE'): (60.0, 45.0),
    ('SPORTS ORGANIZATIONS', 'NATIONAL HOCKEY LEAGUE'): (12.0, 18.0),
    ('SPORTS ORGANIZATIONS', 'ULTIMATE FIGHTING CHAMPION'): (5.0, 15.0),
    ('SPORTS ORGANIZATIONS', 'WORLD WRESTLING ENTERTAINMENT WWE'): (5.0, 15.0),

    # ── PHASE-2 ground-truth additions (auto-added by apply_genpop_fixes.py) ──
    ('STREAMING/PLATFORM', 'MAX'): (37.3, 55.95),  # WBD FY2024 10-K (rebranded HBO Max)
    ('SOCIAL MEDIA', 'YOUTUBE'): (84.0, 84.0),  # Pew 2024
    ('SOCIAL MEDIA', 'FACEBOOK'): (68.0, 68.0),  # Pew 2024 / META 196M N.A. DAP
    ('SOCIAL MEDIA', 'INSTAGRAM'): (50.0, 50.0),  # META 169M US MAU
    ('SOCIAL MEDIA', 'TIKTOK'): (47.0, 47.0),  # ByteDance 170M US MAU
    ('SOCIAL MEDIA', 'TWITTER'): (22.0, 33.0),  # eMarketer 2024
    ('SOCIAL MEDIA', 'REDDIT'): (22.0, 33.0),  # Reddit Q3 2024 10-Q
    ('SOCIAL MEDIA', 'PINTEREST'): (35.0, 52.5),  # Pinterest Q4 2024 10-K
    ('SOCIAL MEDIA', 'LINKEDIN'): (28.0, 42.0),  # Pew 2024
    ('DIGITAL BANKING', 'VENMO'): (27.3, 40.95),  # PayPal FY2024 10-K
    ('DIGITAL BANKING', 'CASH APP'): (17.3, 25.95),  # Block FY2024 10-K monthly transacting actives
    ('DIGITAL BANKING', 'ZELLE'): (45.8, 45.8),  # Early Warning Services 2024
    ('DIGITAL BANKING', 'APPLE PAY'): (41.5, 41.5),  # Apple/IDC: 60% of 227M US iPhones = 137M
    ('BANKING', 'CAPITAL ONE'): (30.3, 45.45),  # COF FY2024 10-K customer accounts
    ('WHERE THEY SHOP', 'AMAZON'): (88.0, 88.0),  # AMZN: ~88% of US digital adults shop monthly per MRI
    ('TECHNOLOGY/DEVICE', 'APPLE'): (60.0, 60.0),  # Counterpoint 2024
    ('TECHNOLOGY/DEVICE', 'SAMSUNG'): (24.0, 36.0),  # Counterpoint 2024
    ('TECHNOLOGY/DEVICE', 'GOOGLE'): (5.0, 15.0),  # Pixel ~5% US share
    ('TECHNOLOGY/DEVICE', 'MICROSOFT'): (14.0, 42.0),  # Surface + Xbox installed base
    ('SPORTS ORGANIZATIONS', 'NFL'): (41.0, 41.0),  # Gallup 2024
    ('SPORTS ORGANIZATIONS', 'NBA'): (25.0, 37.5),  # Gallup 2024
    ('SPORTS ORGANIZATIONS', 'MLB'): (20.0, 30.0),  # Gallup 2024
    ('SPORTS ORGANIZATIONS', 'NHL'): (12.0, 36.0),  # Gallup 2024
    ('MOST PURCHASED BRANDS', 'FASHION NOVA'): (4.0, 20.0),  # Fashion Nova private; F-skewed; ~4% US 30-day reach
    ('MOST PURCHASED BRANDS', 'SHEIN'): (14.0, 42.0),  # Shein private; ~$30B global; US ~14% 30-day reach (Gen Z + budget)
    ('SPORTS ORGANIZATIONS', 'NCAA'): (25.0, 27.0),  # same
    ('SPORTS ORGANIZATIONS', 'WORLD WRESTLING ENTERTAINMENT'): (9.0, 27.0),  # TKO Group: ~30M US WWE viewers/yr
    ('SPORTS ORGANIZATIONS', 'WWE'): (9.0, 27.0),  # same
    ('APPAREL/FOOTWEAR', 'NIKE'): (28.0, 42.0),  # NKE Membership 70M US; 30-day 28%
    ('APPAREL/FOOTWEAR', 'ADIDAS'): (18.0, 27.0),  # Adidas FY2024
    ('APPAREL/FOOTWEAR', 'LULULEMON'): (8.0, 24.0),  # LULU FY2024
    ('APPAREL/FOOTWEAR', 'HANES'): (12.0, 36.0),  # HBI FY2024 (mostly in-store)
    ('APPAREL/FOOTWEAR', 'OLD NAVY'): (18.0, 27.0),  # GAP FY2024
    ('APPAREL/FOOTWEAR', 'GAP'): (8.0, 24.0),  # GAP FY2024
    ('APPAREL/FOOTWEAR', 'BANANA REPUBLIC'): (4.0, 20.0),  # GAP FY2024
    ('APPAREL/FOOTWEAR', 'H&M'): (12.0, 36.0),  # H&M FY2024
    ('APPAREL/FOOTWEAR', 'ZARA'): (8.0, 24.0),  # Inditex FY2024
    ('APPAREL/FOOTWEAR', 'UNIQLO'): (4.0, 20.0),  # Fast Retailing FY2024
    ('APPAREL/FOOTWEAR', 'CROCS'): (16.0, 24.0),  # CROX FY2024
    ('APPAREL/FOOTWEAR', 'NEW BALANCE'): (11.0, 33.0),  # NB private estimate
    ('APPAREL/FOOTWEAR', 'CARHARTT'): (9.0, 27.0),  # Carhartt private estimate
    ('APPAREL/FOOTWEAR', 'PATAGONIA'): (4.0, 20.0),  # Patagonia private estimate
    ('APPAREL/FOOTWEAR', 'THE NORTH FACE'): (8.0, 24.0),  # VFC FY2024
    ('APPAREL/FOOTWEAR', 'RALPH LAUREN'): (7.0, 21.0),  # RL FY2024
    ('APPAREL/FOOTWEAR', 'FREE PEOPLE'): (3.0, 15.0),  # URBN FY2024 F-only
    ('APPAREL/FOOTWEAR', 'ATHLETA'): (3.0, 15.0),  # GAP FY2024 F-only
    ('APPAREL/FOOTWEAR', 'BRANDY MELVILLE'): (1.5, 7.5),  # private; teen-F
    ('APPAREL/FOOTWEAR', 'REFORMATION'): (1.5, 7.5),  # Permira; F-only DTC
    ('APPAREL/FOOTWEAR', 'VICTORIAS SECRET'): (12.0, 36.0),  # VSCO FY2024 F-only
    ('APPAREL/FOOTWEAR', 'FASHION NOVA'): (4.0, 20.0),  # private F-skewed
    ('APPAREL/FOOTWEAR', 'SHEIN'): (14.0, 42.0),  # private; Gen Z budget
    ('APPAREL/FOOTWEAR', 'LEVI'): (14.0, 42.0),  # LEVI FY2024 ~$2.5B Americas DTC
    ("APPAREL/FOOTWEAR", "LEVI'S"): (14.0, 42.0),  # same
    ('APPAREL/FOOTWEAR', 'AMERICAN EAGLE'): (9.0, 27.0),  # AEO FY2024
    ('APPAREL/FOOTWEAR', 'HOLLISTER CO'): (5.0, 15.0),  # ANF FY2024
    ('APPAREL/FOOTWEAR', 'ABERCROMBIE & FITCH'): (5.0, 15.0),  # ANF FY2024
    ('APPAREL/FOOTWEAR', 'MADEWELL'): (3.0, 15.0),  # JCG/Madewell FY2024
    ('APPAREL/FOOTWEAR', 'J.CREW'): (4.0, 20.0),  # JCG FY2024
    ('APPAREL/FOOTWEAR', 'COACH'): (6.0, 18.0),  # Tapestry FY2024 ~$4B Coach NA
    ('APPAREL/FOOTWEAR', 'KATE SPADE'): (3.0, 15.0),  # Tapestry FY2024
    ('APPAREL/FOOTWEAR', 'MICHAEL KORS'): (5.0, 15.0),  # Capri FY2024
    ('APPAREL/FOOTWEAR', 'CALVIN KLEIN'): (8.0, 24.0),  # PVH FY2024
    ('APPAREL/FOOTWEAR', 'TOMMY HILFIGER'): (6.0, 18.0),  # PVH FY2024
    ('APPAREL/FOOTWEAR', 'UGG'): (6.0, 18.0),  # Deckers FY2024 ~$2B UGG NA
    ('APPAREL/FOOTWEAR', 'HOKA'): (5.0, 15.0),  # Deckers FY2024 fast-growing
    ('APPAREL/FOOTWEAR', 'CONVERSE'): (12.0, 36.0),  # Nike FY2024 Converse $1.7B
    ('APPAREL/FOOTWEAR', 'VANS'): (8.0, 24.0),  # VFC FY2024
    ('APPAREL/FOOTWEAR', 'PUMA'): (7.0, 21.0),  # PUMA FY2024
    ('APPAREL/FOOTWEAR', 'UNDER ARMOUR'): (9.0, 27.0),  # UAA FY2024
    ('APPAREL/FOOTWEAR', 'COLUMBIA'): (6.0, 18.0),  # COLM FY2024
}


# ── Pre-compute correction factors ───────────────────────────────────────────

def _build_correction_factors() -> dict[tuple[str, str], float]:
    """Derive correction_factor = corrected / original for every entry."""
    factors: dict[tuple[str, str], float] = {}
    for key, (corrected, original) in GENPOP_CORRECTIONS.items():
        if original > 0:
            factors[key] = corrected / original
        else:
            factors[key] = 1.0
    return factors

CORRECTION_FACTORS = _build_correction_factors()


# ── Public API ────────────────────────────────────────────────────────────────

# Maximum ratio a per-profile BP may exceed its canonical (ground-truth) value.
# Personas can legitimately over-index — Pedro Pascal genuinely uses HBO Max
# (TLOU) more than gen pop. But blanket "agent ignored the anchor" overshoots
# (Pedro v3 Apple TV+ at 28.8 vs canonical 9.8 = 2.94x) need a cap.
# 1.8x allows generous persona uplift while blocking gross overshoots.
GENPOP_CEILING_RATIO = 1.8

# Load SEC-anchored ground truth from migration/validate_genpop.py.
# Restricting the ceiling enforcer to ONLY these ~140 explicitly-SEC-validated
# brands avoids over-capping for the long tail of curated entries in
# GENPOP_CORRECTIONS where the "corrected" value is hand-tuned rather than
# SEC-anchored. Older curated entries can still legitimately be exceeded by
# a persona-driven over-index.
def _load_ceiling_targets() -> dict:
    """Return {(category, value): canonical_truth_pct} for SEC-anchored brands.
    Falls back to {} if the validator module isn't importable (e.g. when
    genpop_calibration is loaded outside the repo)."""
    try:
        import os, sys as _sys
        _here = os.path.dirname(os.path.abspath(__file__))
        for _p in [
            os.path.join(_here, '..', 'migration'),
            os.path.join(_here, 'migration'),
        ]:
            if os.path.isdir(_p) and _p not in _sys.path:
                _sys.path.insert(0, _p)
        from validate_genpop import GROUND_TRUTH, implied_truth_pct  # type: ignore
        targets: dict = {}
        for key, gt in GROUND_TRUTH.items():
            t = implied_truth_pct(gt)
            if t is not None and t > 0:
                targets[key] = float(t)
        return targets
    except Exception as _e:
        if not SILENCE_VERBOSE_OUTPUT:
            print(f"⚠️ enforce_genpop_ceiling: could not load GROUND_TRUTH ({_e}); ceiling disabled")
        return {}


CANONICAL_TRUTH = _load_ceiling_targets()

# Aliases — when the per-profile output uses a short form but the canonical
# uses a long form (or vice versa), map the per-profile key to the truth key.
# Keep in sync with migration/recalibrate_canonical_genpop.py ALIAS_MAP.
_CEILING_ALIASES = {
    ('SPORTS ORGANIZATIONS', 'NFL'): ('SPORTS ORGANIZATIONS', 'NATIONAL FOOTBALL LEAGUE'),
    ('SPORTS ORGANIZATIONS', 'NBA'): ('SPORTS ORGANIZATIONS', 'NATIONAL BASKETBALL ASSOCIATION'),
    ('SPORTS ORGANIZATIONS', 'MLB'): ('SPORTS ORGANIZATIONS', 'MAJOR LEAGUE BASEBALL'),
    ('SPORTS ORGANIZATIONS', 'NHL'): ('SPORTS ORGANIZATIONS', 'NATIONAL HOCKEY LEAGUE'),
    ('SPORTS ORGANIZATIONS', 'NCAA'): ('SPORTS ORGANIZATIONS', 'NATIONAL COLLEGIATE ATHLETIC ASSOCIATION'),
    ('SPORTS ORGANIZATIONS', 'WWE'): ('SPORTS ORGANIZATIONS', 'WORLD WRESTLING ENTERTAINMENT'),
    ('STREAMING/PLATFORM', 'MAX'): ('STREAMING/PLATFORM', 'HBO MAX'),
}


# ── Persona-flagship exemption table ─────────────────────────────────────────
# When a persona is the explicit talent for a property (Pedro is THE face of
# HBO via Last of Us, Zendaya is Spider-Man on Disney+), the canonical ceiling
# should not clamp that flagship platform — the persona genuinely drives
# audience to it above the population baseline. The table below is a safety
# net used when the persona doc doesn't supply its own `flagship_brands`
# field. Persona-name match is case-insensitive substring.
KNOWN_PERSONA_FLAGSHIPS: dict[str, list[tuple[str, str]]] = {
    'PEDRO PASCAL':   [('STREAMING/PLATFORM', 'HBO MAX'),
                       ('STREAMING/PLATFORM', 'DISNEY+'),
                       ('STREAMING/PLATFORM', 'APPLE TV+')],
    'ZENDAYA':        [('STREAMING/PLATFORM', 'HBO MAX'),
                       ('STREAMING/PLATFORM', 'DISNEY+')],
    'MARGOT ROBBIE':  [('STREAMING/PLATFORM', 'HBO MAX')],  # Barbie → Max
    'BAD BUNNY':      [('STREAMING/MUSIC', 'SPOTIFY'),
                       ('STREAMING/MUSIC', 'APPLE MUSIC')],
    'TAYLOR SWIFT':   [('STREAMING/MUSIC', 'SPOTIFY'),
                       ('STREAMING/MUSIC', 'APPLE MUSIC')],
    'RYAN REYNOLDS':  [('STREAMING/PLATFORM', 'DISNEY+'),    # Deadpool/Marvel
                       ('STREAMING/PLATFORM', 'HULU')],      # Welcome to Wrexham (FX)
    'DWAYNE JOHNSON': [('SPORTS ORGANIZATIONS', 'WORLD WRESTLING ENTERTAINMENT')],
    'SELENA GOMEZ':   [('STREAMING/PLATFORM', 'HULU'),       # Only Murders in the Building (flagship)
                       ('STREAMING/MUSIC', 'SPOTIFY'),       # Top streamed Latin pop artist
                       ('SOCIAL MEDIA', 'INSTAGRAM')],       # 2nd-most-followed on Instagram
    'LEBRON JAMES':   [('SPORTS ORGANIZATIONS', 'NATIONAL BASKETBALL ASSOCIATION')],
}


def _resolve_flagships(persona_doc, project_name: str = '') -> set:
    """Return a set of (CATEGORY, VALUE) pairs that should bypass the cap for
    this persona. Prefers persona_doc['flagship_brands'] when supplied;
    falls back to KNOWN_PERSONA_FLAGSHIPS keyed off project_name.
    """
    flagships: set = set()
    if isinstance(persona_doc, dict):
        for f in (persona_doc.get('flagship_brands') or []):
            try:
                cat = str(f.get('category', '')).upper().strip()
                val = str(f.get('value', '')).upper().strip()
                if cat and val:
                    flagships.add((cat, val))
            except Exception:
                pass
    if project_name:
        # Normalize: strip non-alnum so 'Ryan_Reynolds_v1' matches 'RYAN REYNOLDS'
        import re as _re
        pn_norm = _re.sub(r'[^A-Z0-9]+', ' ', str(project_name).upper())
        for persona, brands in KNOWN_PERSONA_FLAGSHIPS.items():
            persona_norm = _re.sub(r'[^A-Z0-9]+', ' ', persona.upper())
            if persona_norm in pn_norm:
                for cat, val in brands:
                    flagships.add((cat.upper(), val.upper()))
    return flagships


def _persona_cap_noise(project_name: str, category: str, value: str) -> float:
    """Persona-deterministic ±2% noise applied to the cap value, so two
    different personas that both want to clamp at the canonical ceiling
    end up at slightly different post-cap values rather than identical ones.
    Returns a multiplier in roughly [0.98, 1.02].
    """
    import hashlib
    if not project_name:
        return 1.0
    seed = f"{project_name}|{category}|{value}".encode('utf-8')
    h = int(hashlib.blake2b(seed, digest_size=4).hexdigest(), 16)
    # map [0..2^32) → roughly [-0.02, +0.02]
    return 1.0 + (((h % 4001) - 2000) / 100000.0)


def enforce_genpop_ceiling(
    df: pd.DataFrame,
    ceiling_ratio: float = GENPOP_CEILING_RATIO,
    project_name: str = '',
    persona_doc=None,
) -> pd.DataFrame:
    """Runtime safety net — cap each brand's Brand Penetration (Row) at
    ceiling_ratio × canonical_truth_pct.

    Acts ONLY on rows where (Column, Value) is in CANONICAL_TRUTH. Rows
    without ground truth pass through unchanged. Demographic rows
    (AGE/GENDER/INCOME/EDUCATION/ETHNICITY/LOCATION/etc.) are NEVER
    touched — same skip set as calibrate_to_genpop.

    Two upgrades over a flat clamp:
      1. PERSONA-FLAGSHIP EXEMPTION — when the persona is the explicit talent
         for a property (Pedro/HBO via Last of Us, Zendaya/Disney+ via
         Spider-Man), skip the cap for that flagship pairing. Sourced from
         persona_doc['flagship_brands'] when supplied, else KNOWN_PERSONA_FLAGSHIPS.
      2. PERSONA-DETERMINISTIC CAP NOISE — when two profiles BOTH want to
         clamp on the same brand (e.g. CASH APP for any Gen-Z/Millennial
         persona), the post-cap value gets a small ±2% offset hashed off
         project_name so they don't end up identical.

    When a cap fires, recomputes Original Raw Numbers and US Gen Pop
    Projection from the new BP so downstream math stays consistent.
    """
    if df is None or df.empty or not CANONICAL_TRUTH:
        return df

    df = df.copy()

    skip_categories = {
        'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
        'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
        'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
        'OCCUPATION', 'LOCATION',
    }

    flagship_brands = _resolve_flagships(persona_doc, project_name)

    sample_size = _get_sample_size(df)
    capped_count = 0
    flagship_skipped = 0
    capped_examples = []

    for idx, row in df.iterrows():
        category = str(row.get('Column', '')).upper().strip()
        if category in skip_categories:
            continue
        value = str(row.get('Value', '')).upper().strip()
        # Try direct lookup first, then alias resolution
        truth = CANONICAL_TRUTH.get((category, value))
        truth_key = (category, value)
        if truth is None:
            alias_key = _CEILING_ALIASES.get((category, value))
            if alias_key is not None:
                truth = CANONICAL_TRUTH.get(alias_key)
                if truth is not None:
                    truth_key = alias_key
        if truth is None:
            continue

        current_pct = _safe_float(row.get('Brand Penetration (Row)', 0))
        if current_pct <= 0:
            continue

        # Flagship RELAX (not exemption): even when the persona genuinely
        # defines a brand, no audience is monolithic. Selena Gomez's audience
        # comes from music + Disney + IG + Latin pop, not just Hulu — so
        # flagship brands get a higher cap (2.4x truth instead of 1.8x) but
        # not unbounded exemption.
        is_flagship = ((category, value) in flagship_brands
                        or truth_key in flagship_brands)
        eff_ratio = (ceiling_ratio * 1.33) if is_flagship else ceiling_ratio  # 1.8 → 2.4

        # Persona-deterministic noise on the cap so two personas that both
        # want to clamp here don't land at IDENTICAL post-cap values.
        cap_with_noise = truth * eff_ratio * _persona_cap_noise(project_name, category, value)

        if current_pct <= cap_with_noise:
            continue

        new_pct = round(cap_with_noise, 4)
        new_raw = int(round((new_pct / 100.0) * sample_size))
        new_genpop = int(round((new_raw / SAMPLE_CAP) * US_POPULATION))

        df.at[idx, 'Brand Penetration (Row)'] = new_pct
        df.at[idx, 'Original Raw Numbers'] = new_raw
        df.at[idx, 'US Gen Pop Projection'] = new_genpop
        capped_count += 1
        if is_flagship:
            flagship_skipped += 1  # tracking flagship-relaxed-and-still-capped
        if len(capped_examples) < 8:
            tag = " ⭐flagship-relaxed" if is_flagship else ""
            capped_examples.append(
                f"{category}/{value}: {current_pct:.1f} → {new_pct:.1f} (cap≈{cap_with_noise:.1f}){tag}"
            )

    if capped_count and not SILENCE_VERBOSE_OUTPUT:
        print(f"🧢 Gen-pop ceiling enforced: {capped_count} brands capped (~{ceiling_ratio}x canonical, flagship at ~{ceiling_ratio*1.33:.2f}x, ±2% persona noise)")
        for ex in capped_examples:
            print(f"     · {ex}")
        if capped_count > len(capped_examples):
            print(f"     · …+{capped_count - len(capped_examples)} more")
    if flagship_skipped and not SILENCE_VERBOSE_OUTPUT:
        print(f"⭐ Flagship relax applied to {flagship_skipped} persona-flagship pairing(s) (still capped at relaxed ratio)")

    return df


def calibrate_to_genpop(df: pd.DataFrame) -> pd.DataFrame:
    """Apply gen-pop correction factors to a profile's post-pipeline DataFrame.

    For every (Column, Value) that has a correction factor:
        new_pct = current_pct * factor          (capped at 95 %)
        Original Raw Numbers  recalculated
        US Gen Pop Projection recalculated
        Brand Penetration     recalculated

    Values without a correction factor pass through unchanged.
    Demographics, metadata, and BRAND INPUT rows are never touched.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    skip_categories = {
        'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
        'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
        'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
        'OCCUPATION', 'LOCATION',
    }

    sample_size = _get_sample_size(df)

    calibrated_count = 0

    for idx, row in df.iterrows():
        category = str(row.get('Column', '')).upper().strip()
        if category in skip_categories:
            continue

        value = str(row.get('Value', '')).upper().strip()
        key = (category, value)

        factor = CORRECTION_FACTORS.get(key)
        if factor is None:
            factor = CATEGORY_DEFAULT_FACTORS.get(category)
        if factor is None:
            continue

        current_pct = _safe_float(row.get('Brand Penetration (Row)', 0))
        if current_pct <= 0:
            raw = _safe_float(row.get('Original Raw Numbers', 0))
            if raw > 0 and sample_size > 0:
                current_pct = (raw / sample_size) * 100.0
            else:
                continue

        calibrated_pct = min(current_pct * factor, 95.0)
        calibrated_pct = max(calibrated_pct, 0.0001)

        new_raw = int(round((calibrated_pct / 100.0) * sample_size))
        new_genpop = int(round((new_raw / SAMPLE_CAP) * US_POPULATION))

        df.at[idx, 'Brand Penetration (Row)'] = round(calibrated_pct, 4)
        df.at[idx, 'Original Raw Numbers'] = new_raw
        df.at[idx, 'US Gen Pop Projection'] = new_genpop

        calibrated_count += 1

    if not SILENCE_VERBOSE_OUTPUT:
        print(f"🎯 Gen-pop calibration applied: {calibrated_count} values corrected "
              f"({len(CORRECTION_FACTORS)} factors loaded)")

    return df


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_sample_size(df: pd.DataFrame) -> int:
    mask = df['Column'].str.upper() == 'SAMPLE SIZE'
    if not mask.any():
        return SAMPLE_CAP
    for col in ('Percentage', 'Category Share', 'Brand Penetration (Row)'):
        if col in df.columns:
            val = df.loc[mask, col].iloc[0]
            try:
                return int(float(str(val).replace(',', '')))
            except (ValueError, TypeError):
                continue
    return SAMPLE_CAP


def _safe_float(val) -> float:
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0.0
