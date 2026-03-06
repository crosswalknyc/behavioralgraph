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

    Values NOT in this lookup get correction_factor = 1.0 (unchanged).

UPDATING:
    To add more corrections, add entries to GENPOP_CORRECTIONS below.
    Format: (CATEGORY, VALUE): (corrected_pct, original_pct)
"""

import pandas as pd

SILENCE_VERBOSE_OUTPUT = False

US_POPULATION = 329_900_000
SAMPLE_CAP = 10_000_000

# ── Ground-truth corrections ─────────────────────────────────────────────────
# (CATEGORY, VALUE): (corrected_pct, original_pipeline_pct)
#
# corrected_pct    = verified real-world US penetration
# original_pipeline_pct = what the bg.py pipeline produced for gen pop
#                         (before we hand-corrected the CSV)
GENPOP_CORRECTIONS: dict[tuple[str, str], tuple[float, float]] = {

    # ── SOCIAL MEDIA ──────────────────────────────────────────────────────
    ('SOCIAL MEDIA', 'TWITCH'):       (8.5,   50.9977),
    ('SOCIAL MEDIA', 'DISCORD'):      (16.5,  41.8806),
    ('SOCIAL MEDIA', 'X'):            (27.5,  36.0697),
    ('SOCIAL MEDIA', 'PATREON'):      (4.0,   19.4275),
    ('SOCIAL MEDIA', 'TUMBLR'):       (4.0,   15.522),
    ('SOCIAL MEDIA', 'ONLYFANS'):     (2.5,   12.6628),
    ('SOCIAL MEDIA', 'SNAPCHAT'):     (37.5,  10.9857),
    ('SOCIAL MEDIA', 'LETTERBOXD'):   (1.5,   7.3208),
    ('SOCIAL MEDIA', 'BLUESKY'):      (1.5,   6.4427),

    # ── STREAMING / MUSIC ────────────────────────────────────────────────
    ('STREAMING/MUSIC', 'SPOTIFY'):          (33.0,  91.9063),
    ('STREAMING/MUSIC', 'APPLE MUSIC'):      (17.0,  87.3336),
    ('STREAMING/MUSIC', 'YOUTUBE MUSIC'):    (9.0,   76.1088),
    ('STREAMING/MUSIC', 'SIRIUSXM'):         (13.0,  62.1221),
    ('STREAMING/MUSIC', 'PANDORA MUSIC'):    (17.5,  53.994),
    ('STREAMING/MUSIC', 'AMAZON MUSIC'):     (16.0,  45.1202),
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
    ('STREAMING/PLATFORM', 'NETFLIX'):            (67.0,  90.5289),
    ('STREAMING/PLATFORM', 'HULU'):               (17.0,  89.5212),
    ('STREAMING/PLATFORM', 'DISNEY+'):            (28.0,  51.6918),
    ('STREAMING/PLATFORM', 'HBO MAX'):            (22.0,  49.8856),
    ('STREAMING/PLATFORM', 'APPLE TV+'):          (13.0,  43.755),
    ('STREAMING/PLATFORM', 'PARAMOUNT+'):         (11.0,  38.7105),
    ('STREAMING/PLATFORM', 'PEACOCK'):             (9.0,  34.0767),
    ('STREAMING/PLATFORM', 'AMAZON PRIME VIDEO'): (43.0,  33.3701),
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
    ('DIGITAL BANKING', 'PAYPAL'):    (47.0,  77.5729),
    ('DIGITAL BANKING', 'COINBASE'):  (10.0,  58.6475),
    ('DIGITAL BANKING', 'BILT'):      (1.5,   19.8593),

    # ── BANKING ──────────────────────────────────────────────────────────
    ('BANKING', 'CHASE'):                (22.0,  56.4858),
    ('BANKING', 'BANK OF AMERICA'):      (14.0,  44.6929),
    ('BANKING', 'WELLS FARGO'):          (11.0,  39.9569),
    ('BANKING', 'CITIBANK'):              (6.0,  35.4207),
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
    ('TELECOM', 'XFINITY'):          (25.0,   9.4175),
    ('TELECOM', 'VERIZON'):          (30.0,   8.7529),
    ('TELECOM', 'T-MOBILE'):         (28.0,   7.8892),
    ('TELECOM', 'AT&T'):             (25.0,   6.7239),
    ('TELECOM', 'STARLINK'):          (1.5,   6.0623),
    ('TELECOM', 'SPECTRUM'):         (11.0,   5.3753),
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
    ('QSR', 'DOMINOS'):                  (27.0,  43.6124),
    ('QSR', 'PIZZA HUT'):               (22.0,  41.7843),
    ('QSR', 'TACO BELL'):               (32.0,  38.7056),
    ('QSR', 'BURGER KING'):             (22.0,  37.8374),
    ('QSR', 'JERSEY MIKES SUBS'):        (8.0,  36.2696),
    ('QSR', 'PAPA JOHNS'):              (12.0,  35.7982),
    ('QSR', 'PORTILLOS'):                (2.0,  33.7253),
    ('QSR', 'NOODLES AND CO.'):           (2.0,  32.291),
    ('QSR', 'SWEETGREEN'):               (2.5,  31.6758),
    ('QSR', 'EL POLLO LOCO'):            (3.0,  31.0576),
    ('QSR', 'MOES SOUTHWEST GRILL'):     (3.0,  28.7077),
    ('QSR', 'CHIPOTLE MEXICAN GRILL'):  (20.0,  27.3746),
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
    ('QSR', 'DUNKIN'):                   (23.0,  16.9972),
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
    ('WHERE THEY SHOP', 'TARGET'):                        (48.0,  79.46),
    ('WHERE THEY SHOP', 'WALMART'):                       (85.0,  79.24),
    ('WHERE THEY SHOP', 'ALBERTSONS'):                     (8.0,  39.9839),
    ('WHERE THEY SHOP', 'COSTCO'):                        (28.0,  38.7695),
    ('WHERE THEY SHOP', 'CVS'):                           (30.0,  38.4184),
    ('WHERE THEY SHOP', 'PUBLIX'):                        (12.0,  38.3802),
    ('WHERE THEY SHOP', 'EBAY'):                          (12.0,  37.2452),
    ('WHERE THEY SHOP', 'SEPHORA'):                       (12.0,  36.7636),
    ('WHERE THEY SHOP', 'LOWES'):                         (20.0,  36.3597),
    ('WHERE THEY SHOP', 'HOME DEPOT'):                    (25.0,  36.2341),
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
    ('WHERE THEY SHOP', 'TRADER JOES'):                   (12.0,  29.3668),
    ('WHERE THEY SHOP', 'YOOX'):                           (0.3,  29.3519),
    ('WHERE THEY SHOP', 'BEST BUY'):                      (20.0,  29.2811),
    ('WHERE THEY SHOP', 'ACADEMY SPORTS + OUTDOORS'):      (4.0,  28.5627),
    ('WHERE THEY SHOP', 'WHOLE FOODS MARKET'):             (8.0,  28.421),
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
    ('WHERE THEY SHOP', 'WALGREENS'):                      (25.0,  30.0),
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
