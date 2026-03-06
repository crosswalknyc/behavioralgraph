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
    ('STREAMING/MUSIC', 'SPOTIFY'):       (33.0,  91.9063),
    ('STREAMING/MUSIC', 'APPLE MUSIC'):   (17.0,  87.3336),
    ('STREAMING/MUSIC', 'YOUTUBE MUSIC'): (9.0,   76.1088),
    ('STREAMING/MUSIC', 'SIRIUSXM'):      (13.0,  62.1221),
    ('STREAMING/MUSIC', 'PANDORA MUSIC'): (17.5,  53.994),
    ('STREAMING/MUSIC', 'AMAZON MUSIC'):  (16.0,  45.1202),
    ('STREAMING/MUSIC', 'LAST FM'):       (2.5,   40.0746),
    ('STREAMING/MUSIC', 'DEEZER'):        (1.5,   32.6124),
    ('STREAMING/MUSIC', 'SOUNDCLOUD'):    (6.0,   27.6805),
    ('STREAMING/MUSIC', 'QOBUZ'):         (0.5,   23.2356),
    ('STREAMING/MUSIC', 'TIDAL'):         (1.5,   14.4542),

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

    # ── INVESTMENTS (corrected upward — originals were too low) ──────────
    ('INVESTMENTS', 'CHARLES SCHWAB'): (11.0,  6.0148),
    ('INVESTMENTS', 'FIDELITY'):       (13.0,  3.5321),
    ('INVESTMENTS', 'ROBINHOOD'):      (7.5,   2.5387),

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

    # ── WHERE THEY DINE (corrected upward — originals too low) ───────────
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

    # ── GAMES ────────────────────────────────────────────────────────────
    ('GAMES', 'ROBLOX'):              (16.0,  52.5852),
    ('GAMES', 'MINECRAFT'):           (16.0,  50.2475),
    ('GAMES', 'FORTNITE'):            (11.0,  37.8612),
    ('GAMES', 'LEAGUE OF LEGENDS'):   (4.0,   32.9464),
    ('GAMES', 'OVERWATCH'):           (2.5,   30.9613),
    ('GAMES', 'GENSHIN IMPACT'):      (2.5,   30.6784),
    ('GAMES', 'CALL OF DUTY'):        (9.0,   29.8885),

    # ── BROADCAST / CABLE ────────────────────────────────────────────────
    ('BROADCAST/CABLE', 'ESPN'):      (27.0,  88.448),
    ('BROADCAST/CABLE', 'FOX NEWS'):  (16.0,  46.6019),
    ('BROADCAST/CABLE', 'CNN'):       (13.0,  44.6471),
    ('BROADCAST/CABLE', 'MSNBC'):     (9.0,   41.1418),

    # ── MEDIA ────────────────────────────────────────────────────────────
    ('MEDIA', 'ESPN'):                (27.0,  88.448),
    ('MEDIA', 'FOX NEWS'):            (16.0,  46.6019),
    ('MEDIA', 'CNN'):                 (13.0,  44.6471),
    ('MEDIA', 'MSNBC'):              (9.0,   41.1418),
    ('MEDIA', 'NEW YORK TIMES'):      (11.0,  29.4251),
    ('MEDIA', 'BUZZFEED'):            (9.0,   23.5606),

    # ── FRANCHISE ────────────────────────────────────────────────────────
    ('FRANCHISE', 'ROBLOX'):          (16.0,  52.5852),
    ('FRANCHISE', 'MINECRAFT'):       (16.0,  50.2475),
    ('FRANCHISE', 'FORTNITE'):        (11.0,  37.8612),

    # ── EDUCATION & LEARNING ─────────────────────────────────────────────
    ('EDUCATION & LEARNING', 'UDEMY'):        (4.0,   26.4987),
    ('EDUCATION & LEARNING', 'W3 SCHOOLS'):   (2.5,   22.5087),
    ('EDUCATION & LEARNING', 'MASTER CLASS'): (2.5,   14.977),
    ('EDUCATION & LEARNING', 'SKILLSHARE'):   (1.5,   11.4138),

    # ── AUTOMOBILE ───────────────────────────────────────────────────────
    ('AUTOMOBILE', 'BMW'):             (9.0,   42.1315),
    ('AUTOMOBILE', 'MERCEDES-BENZ'):   (7.0,   40.133),
    ('AUTOMOBILE', 'AUDI'):            (5.0,   31.2163),
    ('AUTOMOBILE', 'PORSCHE'):         (1.5,   29.3845),
    ('AUTOMOBILE', 'FERRARI'):         (0.5,   23.7824),
    ('AUTOMOBILE', 'LAMBORGHINI'):     (0.3,   17.0387),

    # ── TELECOM (corrected upward — originals too low) ───────────────────
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
