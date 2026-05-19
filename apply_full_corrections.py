#!/usr/bin/env python3
"""
Comprehensive gen-pop correction script.
Corrects ALL rows in ALL categories of the gen pop CSV:

1. MOST PURCHASED BRANDS (1,438 rows) — known brand corrections + power-law
   redistribution for unknown brands
2. Product sub-categories (APPAREL/FOOTWEAR, BEAUTY/WELLNESS, HOME/OUTDOOR,
   CPG, ACCESSORIES, TECHNOLOGY BRAND) — propagated from MPB for consistency
3. AUTOMOBILE — remaining uncorrected rows
4. GAMES — remaining uncorrected rows
5. AMUSEMENT PARKS, TOYS, INTEREST, SPORTS, TALENT, etc.
6. Cross-category consistency pass
7. Category Share recalculation

Run:  python3 apply_full_corrections.py
"""

import pandas as pd
import math

CSV_PATH = '/Users/jennamenking/Downloads/Gen_Pop_2026_03_04_2026_04_29.csv'
SAMPLE_SIZE = 10_000_000
US_POP = 329_900_000

SKIP_CATEGORIES = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
    'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME',
    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS',
    'OCCUPATION', 'LOCATION', 'BRAND CATEGORY', 'GEN POP',
    'MOST PURCHASED CATEGORIES',
}

# Product sub-categories that should mirror MOST PURCHASED BRANDS values
PRODUCT_SUBCATS = {
    'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR',
    'CPG', 'ACCESSORIES', 'TECHNOLOGY BRAND',
}

# =============================================================================
# PART 1: MOST PURCHASED BRANDS — Known Brand Corrections
# =============================================================================
# Realistic US purchase penetration (% of Americans who bought the brand)

KNOWN_MPB: dict[str, float] = {
    # ── Mass Market / Everyday (20%+) ────────────────────────────────────
    'NIKE': 36.0,
    'HANES': 28.0,
    'OLD NAVY': 22.0,
    'FRUIT OF THE LOOM': 18.0,
    'LEVI': 20.0,
    'CONVERSE': 15.0,
    'ADIDAS': 18.0,
    'CALVIN KLEIN': 12.0,
    'VICTORIAS SECRET': 15.0,
    'SKECHERS': 12.0,
    'CHAMPION': 10.0,
    'GAP': 15.0,
    'NEW BALANCE': 12.0,
    'WRANGLER': 8.0,
    'CROCS': 12.0,
    'H&M': 15.0,
    'UNDER ARMOUR': 10.0,
    'OLD SPICE': 20.0,
    'NEUTROGENA': 20.0,

    # ── Popular Mainstream (8–20%) ───────────────────────────────────────
    'RALPH LAUREN': 8.0,
    'MICHAEL KORS': 8.0,
    'COACH': 6.0,
    'PUMA': 8.0,
    'UGG': 5.0,
    'VANS': 8.0,
    'TOMMY HILFIGER': 7.0,
    'BANANA REPUBLIC': 6.0,
    'COLUMBIA': 8.0,
    'THE NORTH FACE': 10.0,
    'BROOKS SHOES': 5.0,
    'ASICS': 4.0,
    'ZARA': 8.0,
    'UNIQLO': 5.0,
    'AMERICAN EAGLE': 8.0,
    'ABERCROMBIE & FITCH': 5.0,
    'HOLLISTER CO': 4.0,
    'J.CREW': 5.0,
    'EDDIE BAUER': 4.0,
    'L.L.BEAN': 5.0,
    'LANDS END': 4.0,
    'ANN TAYLOR LOFT': 5.0,
    'GARNIER': 12.0,
    'PATAGONIA': 5.0,
    'EXPRESS': 4.0,
    'CARHARTT': 5.0,

    # ── Known Mid-Tier (3–8%) ────────────────────────────────────────────
    'LULULEMON': 4.0,
    'FREE PEOPLE': 3.0,
    'ANTHROPOLOGIE': 3.0,
    'MADEWELL': 3.0,
    'TORY BURCH': 2.0,
    'KATE SPADE': 3.0,
    'SPANX': 3.0,
    'COLE HAAN': 2.0,
    'TOMMY BAHAMA': 2.0,
    'LANE BRYANT': 3.0,
    'J.JILL': 2.0,
    'BROOKS BROTHERS': 2.0,
    'ATHLETA': 4.0,
    'SAVAGE X FENTY': 3.0,
    'PANDORA JEWELRY': 5.0,
    'KENDRA SCOTT': 3.0,
    'HOKA': 5.0,
    'SOREL': 2.0,
    'BOMBAS': 2.0,
    'FIGS': 1.5,
    'BRANDY MELVILLE': 2.0,
    'GOOD AMERICAN': 1.0,
    'TOMS FOOTWEAR': 2.0,
    'RAG & BONE': 1.0,
    'ALLBIRDS': 1.0,
    'PAIGE JEANS': 1.0,
    'JUICY COUTURE': 1.0,
    'BONOBOS': 1.0,
    'LILLY PULITZER': 2.0,
    'MERRELL': 3.0,
    'CERAVE': 5.0,
    'LA ROCHE POSAY': 3.0,
    'GLOSSIER': 3.0,
    'LANCOME': 3.0,
    'MAC COSMETICS': 4.0,
    'LUSH': 3.0,
    'JO MALONE': 1.0,
    'IZOD': 2.0,
    'JACK DANIELS': 8.0,
    'BUDWEISER': 8.0,
    'BUD LIGHT': 8.0,
    'CORONA': 5.0,
    'GATORADE': 15.0,
    'HEINZ': 15.0,
    'MCCORMICK': 8.0,
    'HEAD & SHOULDERS': 8.0,
    'SENSODYNE': 5.0,
    'MENTOS': 5.0,
    'GODIVA': 3.0,
    'ROCKSTAR ENERGY': 3.0,
    'HAAGEN-DAZS': 5.0,
    'OXICLEAN': 5.0,
    'ST. IVES': 4.0,
    'KITCHENAID': 5.0,
    'POTTERY BARN': 3.0,
    'WEST ELM': 2.0,
    'LE CREUSET': 2.0,
    'HALLMARK': 5.0,
    'INSTANT POT': 5.0,
    'OURA RING': 1.0,
    'FITBIT': 5.0,
    'SKULLCANDY': 2.0,
    'OTTERBOX': 3.0,

    # ── Niche / Specialty (0.5–3%) ───────────────────────────────────────
    'REFORMATION': 1.5,
    'KITH': 0.8,
    'ALLSAINTS': 1.0,
    'THEORY': 1.0,
    'DIESEL': 1.0,
    'HUGO BOSS': 2.0,
    'COS': 2.0,
    'ED HARDY': 0.5,
    'BOOHOO': 2.0,
    'CLUB MONACO': 1.0,
    'CUTTER & BUCK': 0.5,
    'FOR LOVE & LEMONS': 0.5,
    'DESIGUAL': 0.5,
    'MONKI': 0.3,
    'MUJI USA': 1.0,
    'BERGHAUS': 0.5,
    'INTIMISSIMI': 0.5,
    'GANT': 0.5,
    'JOS. A BANK': 2.0,
    'RED KAP WORKWEAR': 0.5,
    'URBAN PLANET': 0.3,
    'HERSCHEL SUPPLY': 1.0,
    'PELOTON APPAREL': 1.0,
    'COUNTRY ROAD': 0.3,
    'BLACK DIAMOND': 1.0,
    'SAM EDELMAN': 1.0,
    'BA&SH': 0.3,
    'OAK + FORT': 0.5,
    'RYKA': 1.0,
    'ANN SUMMERS': 0.3,
    'PARADE UNDERWEAR': 0.5,
    'ROOTS': 0.5,
    'BATSHEVA': 0.2,
    'FOOTJOY': 1.0,
    'JOES JEANS': 0.5,
    'ARMOR LUX': 0.3,
    'LAZY OAF': 0.3,
    'MALBON': 0.3,
    'COTTON:ON': 1.0,
    'LA BLANCA': 0.5,
    'BOUGUESSA': 0.1,
    'PLUFFI SLIPPERS': 0.2,
    'TRESEMME': 5.0,
    'OGX': 4.0,
    'PROACTIV': 2.0,
    'MARC ANTHONY': 1.0,
    'BYREDO': 0.3,
    'ORIBE': 0.5,
    'THRIVE CAUSEMETICS': 0.5,
    'PANOXYL': 1.0,
    'MAISON LOUIS MARIE': 0.2,
    'RHODE SKIN': 1.0,
    'THERABODY': 1.0,
    'ZENNI OPTICAL': 3.0,
    'NEW ERA CAP': 3.0,
    'SWATCH': 1.0,
    'KATE SPADE OUTLET': 2.0,
    'CALPAK': 1.0,
    'CITIZEN WATCH': 1.0,
    'STETSON': 0.5,
    'THULE': 1.0,
    'TAYLORMADE GOLF': 1.0,
    'TREK BIKES': 1.0,
    'CRICUT': 2.0,
    'RAWLINGS': 1.0,
    'TERMINIX': 2.0,
    'CORELLE': 2.0,
    'ZARA HOME': 1.0,
    'ARHAUS': 0.5,
    '1800FLOWERS': 1.0,

    # ── Luxury / Designer (0.05–0.5%) ────────────────────────────────────
    'MONCLER': 0.2,
    'COMME DES GARCONS': 0.1,
    'VALENTINO': 0.3,
    'MISSONI': 0.2,
    'EMILIO PUCCI': 0.1,
    'AGENT PROVOCATEUR': 0.3,
    'LALA BERLIN': 0.1,
    'WEEKDAY': 0.5,
    'BATHER': 0.3,
    'COURREGES': 0.1,
    'NORMA KAMALI': 0.3,
    'ZADIG & VOLTAIRE': 0.3,
    'J.MCLAUGHLIN': 0.3,
    'BEN SHERMAN': 0.5,
    'HEAD SPORTING GOODS': 0.5,
    'ANINE BING': 0.5,
    'AND OTHER STORIES': 1.0,
    'A.L.C.': 0.3,
    'A.P.C.': 0.3,
    'ALICE + OLIVIA': 0.5,
    'ALO YOGA': 1.0,
    'TALBOTS': 3.0,
    'LUCCHESE': 0.3,
    'OXKNIT': 0.2,
    'MAAMGIC': 0.3,
    'LONG WHARF SUPPLY CO.': 0.2,
    'JOMA': 0.3,
    'HERSCHEL': 1.0,
    'COBIAN': 0.3,
    'THE JESSICA SIMPSON COLLECTION': 1.0,
    'ACNE STUDIOS': 0.3,
    'NEW & LINGWOOD': 0.1,

    # ── Mass Market CPG / Grocery (high purchase penetration) ───────────
    'LOREAL PARIS': 20.0,
    'BATH & BODY WORKS': 18.0,
    'DOVE BEAUTY': 18.0,
    'PRINGLES': 10.0,
    'BETTY CROCKER': 12.0,
    'OLAY': 15.0,
    'MAYBELLINE': 12.0,
    'REESES': 12.0,
    'PANTENE': 12.0,
    'DORITOS': 15.0,
    'BOUNTY': 10.0,
    'CHARMIN': 12.0,
    'HERSHEYS': 15.0,
    'GILLETTE': 15.0,
    'SNICKERS': 10.0,
    'TIDE': 18.0,
    'PILLSBURY': 10.0,
    'LAYS': 15.0,
    'PEPPERIDGE FARM GOLDFISH': 8.0,
    'ZIPLOC': 12.0,
    'LYSOL': 12.0,
    'DEGREE': 8.0,
    'CLOROX': 12.0,
    'CREST': 15.0,
    'OREO': 15.0,
    'CAMPBELLS': 10.0,
    'COVERGIRL': 8.0,
    'REVLON': 5.0,
    'SNAPPLE': 5.0,
    'FEBREZE': 8.0,
    'RED BULL': 10.0,
    'CANADA DRY': 5.0,
    'DOVE CHOCOLATE': 5.0,
    'RAY-BAN': 5.0,
    'GUESS': 3.0,
    'CARTERS': 5.0,
    'FABLETICS': 2.0,
    'CASPER': 2.0,
    'CELESTIAL SEASONINGS': 2.0,
    'MAIDENFORM': 2.0,
    'TOPO CHICO': 2.0,
    'PENDLETON': 1.0,
    'FEVER TREE': 1.0,
    'TELEFLORA': 1.0,
    'ROCKPORT': 1.0,
    'SUN BUM': 1.0,
    'GOOSE ISLAND BEER': 1.0,
    'HUNTER BOOTS': 1.0,
    'TOMMY JOHN': 1.0,
    'QUIKSILVER': 1.0,
    'US POLO ASSN': 1.0,
    'MARC JACOBS': 1.0,
    'MAUI JIM': 2.0,
    'OUAI': 1.0,
    'RUBBERMAID': 8.0,
    'PLAYTEX': 3.0,
    'ORAL B': 12.0,
    'BAND AID': 10.0,
    'DAWN': 12.0,
    'GLAD': 8.0,
    'REYNOLDS': 5.0,
    'FOLGERS': 8.0,
    'NESTLE': 10.0,
    'KRAFT': 12.0,
    'PEPSI': 15.0,
    'COCA COLA': 20.0,
    'SPRITE': 8.0,
    'MOUNTAIN DEW': 8.0,
    'DR PEPPER': 8.0,
    'FRITO LAY': 12.0,
    'CHEEZ-IT': 8.0,
    'TRISCUIT': 3.0,
    'WHEAT THINS': 3.0,
    'KIT KAT': 8.0,
    'M&MS': 10.0,
    'TWIX': 5.0,
    'MILKY WAY': 3.0,
    'SKITTLES': 5.0,
    'STARBURST': 5.0,
    'HARIBO': 5.0,
    'CHEERIOS': 12.0,
    'FROSTED FLAKES': 8.0,
    'LUCKY CHARMS': 5.0,
    'QUAKER': 8.0,
    'NATURE VALLEY': 8.0,
    'KIND': 5.0,
    'CLIF BAR': 3.0,
    'CHOBANI': 5.0,
    'DANNON': 5.0,
    'YOPLAIT': 5.0,
    'STOUFFERS': 5.0,
    'DIGIORNO': 5.0,
    'HOT POCKETS': 5.0,
    'LEAN CUISINE': 3.0,
    'PROGRESSO': 5.0,
    'RAGU': 3.0,
    'PREGO': 5.0,
    'BARILLA': 5.0,
    'VELVEETA': 3.0,
    'SARGENTO': 5.0,
    'BRITA': 5.0,
    'ARM & HAMMER': 8.0,
    'PLEDGE': 3.0,
    'WINDEX': 8.0,
    'MR CLEAN': 5.0,
    'PINE SOL': 5.0,
    'DOWNY': 8.0,
    'GAIN': 8.0,
    'SEVENTH GENERATION': 3.0,
    'METHOD': 3.0,
    'MRS MEYERS': 3.0,
    'SECRET': 5.0,
    'IRISH SPRING': 5.0,
    'DIAL': 5.0,
    'LEVER 2000': 2.0,
    'SUAVE': 5.0,
    'AVEENO': 8.0,
    'CETAPHIL': 5.0,
    'EUCERIN': 3.0,
    'JERGENS': 3.0,
    'VASELINE': 8.0,
    'AQUAPHOR': 5.0,
    'BURT\'S BEES': 5.0,
    'NOXZEMA': 2.0,
    'CLEARASIL': 2.0,
    'STRIDEX': 2.0,
    'JOHNSON & JOHNSON': 10.0,
    'BAND-AID': 10.0,
    'TYLENOL': 12.0,
    'ADVIL': 10.0,
    'BENADRYL': 5.0,
    'ZYRTEC': 5.0,
    'CLARITIN': 5.0,
    'MUCINEX': 5.0,
    'VICKS': 5.0,
    'PEPTO BISMOL': 5.0,
    'TUMS': 5.0,
    'ROBITUSSIN': 3.0,
    'DAYQUIL': 5.0,

    # ── Niche/Luxury brands that the redistribution overvalued ─────────
    'VEJA SNEAKERS': 0.5,
    'FRAME': 0.5,
    'FRUGI': 0.1,
    'KARL LAGERFELD': 0.5,
    'WHO GIVES A CRAP': 0.3,
    'JENNIFER FISHER JEWELRY': 0.2,
    'LOCK & CO. HATTERS': 0.1,
    'DOLCE VITA': 0.5,
    'R13': 0.2,
    'FEAR OF GOD': 0.3,
    'NESTLE AERO': 0.5,
    'LULULUN': 0.3,
    'HILL HOUSE HOME': 0.3,
    'JOSS & MAIN': 0.5,
    'SPROUT LIVING': 0.2,
    'BAPE': 0.3,
    'KRUPS': 0.5,
    'COMMODITY': 0.2,
    'CYNTHIA ROWLEY': 0.3,
    'HAWX PEST CONTROL': 0.5,
    'JENNI KAYNE': 0.3,
    'LNDR': 0.1,
    'MOUNTAIN WAREHOUSE': 0.3,
    'ASPINAL OF LONDON': 0.1,
    'MADISON REED': 0.5,
    'WESTERN MOUNTAINEERING': 0.3,
    'ASHER GOLF': 0.1,
    'ROOMMATES': 0.3,
    'LUMIN': 0.3,
    'RIFLE PAPER CO.': 0.5,
    'FRENCH CONNECTION USA': 0.5,
    'TELFAR': 0.3,
    'TUCKERNUCK': 0.3,
    'LUISA CERANO': 0.1,
    'WILDFOX COUTURE': 0.2,
    'ZENBIVY': 0.1,
    'TRUE RELIGION': 0.5,
    'VOLUSPA': 0.3,
    'NOAH': 0.3,
    'LARESAR': 0.1,
    'MATT & NAT': 0.3,
    'LOLA CASADEMUNT': 0.1,
    'ISABEL MARANT': 0.2,

    # ── Additional known brands ──────────────────────────────────────────
    'GODINGER': 0.1,
    'CHOPARD': 0.1,
    'AUDEMARS PIGUET': 0.05,
    'LUGGAGE ONLINE': 0.3,
    'BANTER': 0.3,
    'LOVISA': 0.5,
    'LO & SONS': 0.3,
    'SPYDER': 0.5,
    'STAUD': 0.3,
    'LOCAL ECLECTIC': 0.2,
    'CASTLERY': 0.5,
    'ECOVACS': 0.5,
    'DREAMETECH': 0.3,
    'MERI MERI': 0.3,
    'ORE-IDA': 5.0,
    'BEYOND MEAT': 3.0,
    'THE HONEST COMPANY': 2.0,
    'HARMLESS HARVEST': 0.5,
    'AVOLT': 0.2,
    'LUMEN': 0.3,
    'BAY ALARM MEDICAL': 0.3,
    'LIFELINE': 0.2,
    'CASETIFY': 1.0,
    'EUFY': 1.0,
    'KASA SMART': 0.5,
    'MEDICAL ALERT': 0.5,
    'SHARK': 3.0,
    'ANKER': 2.0,
    'WET N WILD': 5.0,
    'RITZ CRACKERS': 8.0,
    'OLLY': 2.0,
    'DE CECCO': 1.0,
    'PERRICONE MD': 0.3,
    'MUNCHKIN BABY': 1.0,
    'FARROW & BALL': 0.1,
    'SOAP & GLORY': 0.5,
    'NUGGET': 0.5,
    'STATE BAGS': 0.3,
    'GHOSTBED': 0.3,
    'AVON': 3.0,
    'MOVADO': 0.3,
    'AMERICAN TOURISTER': 1.0,
    'ACE AND TATE': 0.2,
    'ALERT1': 0.2,
    'ALESSI': 0.2,
    'CASE MATE': 0.5,
    'ROKFORM CASES': 0.2,
    'NATIVE UNION': 0.2,
    'AUGUST HOME': 0.5,
    'LIFE ALERT': 1.0,
    'PIMAX': 0.1,
}

# =============================================================================
# PART 2: AUTOMOBILE — Known Corrections
# =============================================================================

KNOWN_AUTO: dict[str, float] = {
    'TOYOTA': 16.0,
    'HONDA': 15.0,
    'FORD': 14.0,
    'CHEVROLET': 13.0,
    'NISSAN': 8.0,
    'HYUNDAI': 6.0,
    'KIA': 5.0,
    'SUBARU': 4.0,
    'VOLKSWAGON': 4.0,
    'JEEP': 6.0,
    'RAM': 4.0,
    'GMC': 4.0,
    'MAZDA': 3.0,
    'LEXUS': 3.0,
    'BUICK': 2.0,
    'DODGE': 4.0,
    'CHRYSLER': 2.0,
    'TESLA': 3.0,
    'VOLVO': 2.0,
    'ACURA': 2.0,
    'INFINITI': 1.5,
    'LINCOLN': 1.5,
    'CADILLAC': 2.0,
    'MITSUBISHI': 2.0,
    'MINI COOPER': 1.0,
    'GENESIS': 0.5,
    'MASERATI': 0.2,
    'ALFA ROMEO': 0.3,
    'JAGUAR': 0.5,
    'LAND ROVER': 0.5,
    'RIVIAN': 0.3,
    'LUCID MOTORS': 0.1,
    'POLESTAR': 0.2,
    'FISKER': 0.1,
    'CARGURUS': 5.0,
    'CARFAX': 8.0,
    'CARS.COM': 5.0,
    'AUTOTRADER': 5.0,
    'KELLEY BLUE BOOK': 5.0,
    'TRUECAR': 3.0,
    'EDMUNDS': 3.0,
    'CARVANA': 3.0,
    'VROOM': 1.0,
    'SHIFT': 0.5,
    'BMW': 9.0,
    'MERCEDES-BENZ': 7.0,
    'AUDI': 5.0,
    'PORSCHE': 1.5,
    'FERRARI': 0.5,
    'LAMBORGHINI': 0.3,
}

# =============================================================================
# PART 3: GAMES — Remaining Known Corrections
# =============================================================================

KNOWN_GAMES: dict[str, float] = {
    'THE OUTER WORLDS': 2.0,
    'RESIDENT EVIL': 5.0,
    'HEROES OF THE STORM': 1.0,
    'ARK': 3.0,
    'PALWORLD': 2.0,
    'DRAGON QUEST BUILDERS': 1.0,
    'POPPY PLAYTIME': 2.0,
    'WARCRAFT': 5.0,
    'Z8GAMES': 0.3,
    'MONSTER HUNTER': 3.0,
    'BALDURS GATE': 3.0,
    'REC ROOM PLAY WITH FRIENDS': 2.0,
    'EA SPORTS NHL': 2.0,
    'SQUARE ENIX GAMES': 3.0,
    'DOOM': 4.0,
    'APEX LEGENDS': 4.0,
    'DESTINY': 3.0,
    'DIABLO': 4.0,
    'COUNTER-STRIKE': 3.0,
    'POKEMON GO': 8.0,
    'POKEMON': 10.0,
    'CANDY CRUSH': 12.0,
    'WORDLE': 10.0,
    'ANIMAL CROSSING': 5.0,
    'ZELDA': 5.0,
    'FIFA': 5.0,
    'MADDEN': 5.0,
    'NBA 2K': 4.0,
    'AMONG US': 5.0,
    'FALL GUYS': 3.0,
    'ROCKET LEAGUE': 3.0,
    'ELDEN RING': 3.0,
    'HALO': 4.0,
    'GOD OF WAR': 3.0,
    'THE LAST OF US': 3.0,
    'SPIDER-MAN': 5.0,
    'CYBERPUNK 2077': 3.0,
    'SKYRIM': 5.0,
    'HOGWARTS LEGACY': 3.0,
    'STARDEW VALLEY': 3.0,
    'THE SIMS': 5.0,
    'TETRIS': 8.0,
    'WORLD OF WARCRAFT': 3.0,
    'PLAYSTATION': 15.0,
    'XBOX': 12.0,
    'NINTENDO': 15.0,
    'NINTENDO SWITCH': 12.0,
}

# =============================================================================
# PART 4: AMUSEMENT PARKS — Known Corrections
# =============================================================================

KNOWN_AMUSEMENT: dict[str, float] = {
    'DISNEY WORLD': 5.0,
    'DISNEYLAND': 4.0,
    'UNIVERSAL ORLANDO RESORT': 3.0,
    'UNIVERSAL STUDIOS HOLLYWOOD': 2.5,
    'SEA WORLD': 2.0,
    'SIX FLAGS AMERICA HURRICANE HARBOR BOWIE': 1.5,
    'SIX FLAGS': 2.0,
    'CEDAR POINT': 1.5,
    'BUSCH GARDENS': 1.5,
    'LEGOLAND': 1.0,
    'HERSHEYPARK': 1.0,
    'DOLLYWOOD': 1.0,
    'KNOTT\'S BERRY FARM': 1.0,
    'TOP GOLF': 3.0,
    'SKY ZONE TRAMPOLINE PARK': 1.5,
    'DAVE AND BUSTERS': 3.0,
}

# =============================================================================
# PART 5: INTEREST — Known Corrections
# =============================================================================

KNOWN_INTEREST: dict[str, float] = {
    'SOCIAL MEDIA': 80.0,
    'FASHION': 55.0,
    'BUSINESS': 45.0,
    'OUTDOOR LIFE': 40.0,
    'SECONDHAND CLOTHING': 25.0,
    'INFLUENCER STYLE': 20.0,
    'LIVE EVENTS': 45.0,
    'ARTIFICIAL INTELLIGENCE': 30.0,
    'PHOTOGRAPHY': 35.0,
    'READING DIGITAL MEDIA': 50.0,
    'ONLINE COMMUNITY': 60.0,
    'MALL SHOPPING': 50.0,
    'FOOTWEAR': 65.0,
    'POLITICS': 50.0,
    'SNEAKERS': 35.0,
    'DIY': 40.0,
    'COOKING': 55.0,
    'TRAVEL': 55.0,
    'FITNESS': 45.0,
    'GAMING': 40.0,
    'MUSIC': 70.0,
    'MOVIES': 65.0,
    'SPORTS': 55.0,
    'TECHNOLOGY': 50.0,
    'PETS': 45.0,
    'HOME DECOR': 40.0,
    'GARDENING': 30.0,
    'READING': 45.0,
    'CRAFTS': 25.0,
    'YOGA': 15.0,
    'HIKING': 25.0,
    'CAMPING': 20.0,
    'FISHING': 15.0,
    'HUNTING': 10.0,
    'CYCLING': 15.0,
    'RUNNING': 20.0,
    'GOLF': 8.0,
    'TENNIS': 6.0,
    'SWIMMING': 20.0,
    'WINE': 25.0,
    'CRAFT BEER': 15.0,
    'COCKTAILS': 20.0,
    'COFFEE': 60.0,
    'TEA': 35.0,
    'BAKING': 30.0,
    'VEGANISM': 5.0,
    'SUSTAINABILITY': 20.0,
    'CRYPTOCURRENCY': 10.0,
    'INVESTING': 25.0,
    'REAL ESTATE': 20.0,
    'PODCASTS': 35.0,
    'STREAMING': 70.0,
    'ANIME': 12.0,
    'K-POP': 5.0,
    'HIP HOP': 30.0,
    'COUNTRY MUSIC': 20.0,
    'ROCK': 35.0,
    'R&B': 25.0,
    'ELECTRONIC MUSIC': 12.0,
    'JAZZ': 10.0,
    'CLASSICAL MUSIC': 8.0,
    'COMIC BOOKS': 8.0,
    'BOARD GAMES': 15.0,
    'PUZZLES': 20.0,
    'ASTROLOGY': 15.0,
    'TRUE CRIME': 25.0,
    'SCI-FI': 15.0,
    'FANTASY': 12.0,
    'HORROR': 15.0,
    'ROMANCE': 15.0,
    'BASKETBALL': 25.0,
    'FOOTBALL': 35.0,
    'BASEBALL': 20.0,
    'SOCCER': 15.0,
    'HOCKEY': 8.0,
    'MMA': 8.0,
    'WRESTLING': 5.0,
    'BOXING': 8.0,
    'ESPORTS': 8.0,
    'BEAUTY': 40.0,
    'SKINCARE': 35.0,
    'MAKEUP': 25.0,
    'HAIR CARE': 30.0,
    'NAIL ART': 10.0,
    'TATTOOS': 12.0,
    'THRIFT SHOPPING': 20.0,
    'LUXURY FASHION': 8.0,
    'STREETWEAR': 10.0,
    'VINTAGE': 15.0,
    'MINIMALISM': 10.0,
    'PARENTING': 25.0,
    'EDUCATION': 35.0,
    'VOLUNTEERING': 15.0,
    'MENTAL HEALTH': 30.0,
    'WELLNESS': 35.0,
    'MEDITATION': 12.0,
    'SPIRITUALITY': 15.0,
    'RELIGION': 25.0,
    'CARS': 30.0,
    'MOTORCYCLES': 8.0,
    'ELECTRIC VEHICLES': 10.0,
    'WOODWORKING': 8.0,
    'ART': 25.0,
    'MUSEUMS': 20.0,
    'THEATER': 12.0,
    'DANCE': 12.0,
    'COMEDY': 35.0,
    'DOCUMENTARY': 25.0,
    'REALITY TV': 25.0,
    'NEWS': 55.0,
    'SCIENCE': 25.0,
    'SPACE': 15.0,
    'HISTORY': 25.0,
    'NATURE': 35.0,
    'ENVIRONMENT': 20.0,
    'CLIMATE': 15.0,
    'FOOD': 60.0,
    'RESTAURANTS': 50.0,
    'NASCAR': 8.0,
    'F1': 8.0,
}

# =============================================================================
# PART 6: SPORTS ORGANIZATIONS — Known Corrections
# =============================================================================

KNOWN_SPORTS_ORG: dict[str, float] = {
    'F1': 8.0,
    'NASCAR': 8.0,
    'MAJOR LEAGUE BASEBALL': 15.0,
    'NATIONAL BASKETBALL ASSOCIATION': 15.0,
    'NATIONAL FOOTBALL LEAGUE': 25.0,
    'WORLD WRESTLING ENTERTAINMENT WWE': 5.0,
    'NATIONAL COLLEGIATE ATHLETIC ASSOCIATION': 12.0,
    'NATIONAL HOCKEY LEAGUE': 6.0,
    'ULTIMATE FIGHTING CHAMPION': 5.0,
}


# =============================================================================
# REDISTRIBUTION ALGORITHM
# =============================================================================

def redistribute_with_anchors(brands_pipeline_sorted, known_corrections,
                              unknown_max=3.0, unknown_min=0.03):
    """
    Known brands get their exact correction.
    Unknown brands get a power-law distribution (ranked by pipeline value)
    within [unknown_min, unknown_max], preserving relative pipeline order
    but preventing any unknown brand from being unreasonably high.
    """
    result = {}
    unknowns = []

    for brand, pipe_pct in brands_pipeline_sorted:
        b_upper = brand.upper().strip()
        if b_upper in known_corrections:
            result[b_upper] = known_corrections[b_upper]
        else:
            unknowns.append((b_upper, pipe_pct))

    # Sort unknowns by pipeline value (highest first) to preserve relative order
    unknowns.sort(key=lambda x: x[1], reverse=True)

    n = len(unknowns)
    if n > 0:
        for rank, (brand, _) in enumerate(unknowns):
            if n == 1:
                pct = (unknown_max + unknown_min) / 2.0
            else:
                # Log-linear interpolation: rank 0 → unknown_max, rank n-1 → unknown_min
                t = rank / (n - 1)
                log_max = math.log(max(unknown_max, 0.001))
                log_min = math.log(max(unknown_min, 0.001))
                pct = math.exp(log_max + t * (log_min - log_max))
            result[brand] = round(max(pct, unknown_min), 4)

    return result


# =============================================================================
# MAIN CORRECTION LOGIC
# =============================================================================

def apply_all_corrections():
    print(f"Reading CSV: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"  {len(df)} rows loaded")

    # ── Step 1: Build master brand→pct lookup from MOST PURCHASED BRANDS ──
    mpb_mask = df['Column'].str.upper().str.strip() == 'MOST PURCHASED BRANDS'
    mpb_df = df.loc[mpb_mask].copy()
    mpb_df['pct'] = pd.to_numeric(mpb_df['Brand Penetration (Row)'], errors='coerce')
    mpb_sorted = list(zip(
        mpb_df['Value'].astype(str).str.strip(),
        mpb_df['pct']
    ))
    mpb_sorted.sort(key=lambda x: x[1], reverse=True)

    print(f"  MOST PURCHASED BRANDS: {len(mpb_sorted)} brands")
    print(f"    Known corrections: {len(KNOWN_MPB)}")

    mpb_corrections = redistribute_with_anchors(mpb_sorted, KNOWN_MPB)
    print(f"    Total after redistribution: {len(mpb_corrections)}")

    # ── Step 2: Build AUTOMOBILE corrections ──
    auto_mask = df['Column'].str.upper().str.strip() == 'AUTOMOBILE'
    auto_df = df.loc[auto_mask].copy()
    auto_df['pct'] = pd.to_numeric(auto_df['Brand Penetration (Row)'], errors='coerce')
    auto_sorted = list(zip(
        auto_df['Value'].astype(str).str.strip(),
        auto_df['pct']
    ))
    auto_sorted.sort(key=lambda x: x[1], reverse=True)
    auto_corrections = redistribute_with_anchors(auto_sorted, KNOWN_AUTO)

    # ── Step 3: Build GAMES corrections ──
    from genpop_calibration import GENPOP_CORRECTIONS
    existing_games = {v: c for (cat, v), (c, _) in GENPOP_CORRECTIONS.items() if cat == 'GAMES'}
    all_games_known = {**existing_games, **KNOWN_GAMES}

    games_mask = df['Column'].str.upper().str.strip() == 'GAMES'
    games_df = df.loc[games_mask].copy()
    games_df['pct'] = pd.to_numeric(games_df['Brand Penetration (Row)'], errors='coerce')
    games_sorted = list(zip(
        games_df['Value'].astype(str).str.strip(),
        games_df['pct']
    ))
    games_sorted.sort(key=lambda x: x[1], reverse=True)
    games_corrections = redistribute_with_anchors(games_sorted, all_games_known)

    # ── Step 4: Build AMUSEMENT PARKS corrections ──
    ap_mask = df['Column'].str.upper().str.strip() == 'AMUSEMENT PARKS'
    ap_df = df.loc[ap_mask].copy()
    ap_df['pct'] = pd.to_numeric(ap_df['Brand Penetration (Row)'], errors='coerce')
    ap_sorted = list(zip(
        ap_df['Value'].astype(str).str.strip(),
        ap_df['pct']
    ))
    ap_sorted.sort(key=lambda x: x[1], reverse=True)
    ap_corrections = redistribute_with_anchors(ap_sorted, KNOWN_AMUSEMENT)

    # ── Step 5: Build INTEREST corrections ──
    int_mask = df['Column'].str.upper().str.strip() == 'INTEREST'
    int_df = df.loc[int_mask].copy()
    int_df['pct'] = pd.to_numeric(int_df['Brand Penetration (Row)'], errors='coerce')
    int_sorted = list(zip(
        int_df['Value'].astype(str).str.strip(),
        int_df['pct']
    ))
    int_sorted.sort(key=lambda x: x[1], reverse=True)
    int_corrections = redistribute_with_anchors(int_sorted, KNOWN_INTEREST)

    # ── Step 6: Build SPORTS ORGANIZATIONS corrections ──
    so_mask = df['Column'].str.upper().str.strip() == 'SPORTS ORGANIZATIONS'
    so_df = df.loc[so_mask].copy()
    so_df['pct'] = pd.to_numeric(so_df['Brand Penetration (Row)'], errors='coerce')
    so_sorted = list(zip(
        so_df['Value'].astype(str).str.strip(),
        so_df['pct']
    ))
    so_sorted.sort(key=lambda x: x[1], reverse=True)
    so_corrections = redistribute_with_anchors(so_sorted, KNOWN_SPORTS_ORG)

    # ── Step 7: Build master value→pct lookup for cross-category consistency ──
    master_lookup: dict[str, float] = {}

    # MPB is the primary source for purchase brands
    for val, pct in mpb_corrections.items():
        master_lookup[val.upper().strip()] = pct

    # Add behavioral corrections from genpop_calibration
    for (cat, val), (corrected, _) in GENPOP_CORRECTIONS.items():
        key = val.upper().strip()
        if key not in master_lookup:
            master_lookup[key] = corrected

    # Add automobile corrections
    for val, pct in auto_corrections.items():
        key = val.upper().strip()
        if key not in master_lookup:
            master_lookup[key] = pct

    # Add games corrections
    for val, pct in games_corrections.items():
        key = val.upper().strip()
        if key not in master_lookup:
            master_lookup[key] = pct

    # Add amusement parks corrections
    for val, pct in ap_corrections.items():
        key = val.upper().strip()
        if key not in master_lookup:
            master_lookup[key] = pct

    # Add interest corrections
    for val, pct in int_corrections.items():
        key = val.upper().strip()
        if key not in master_lookup:
            master_lookup[key] = pct

    # Add sports org corrections
    for val, pct in so_corrections.items():
        key = val.upper().strip()
        if key not in master_lookup:
            master_lookup[key] = pct

    print(f"  Master lookup: {len(master_lookup)} unique values")

    # ── Step 8: Apply ALL corrections ──
    corrected_count = 0
    categories_corrected = set()

    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue

        val = str(row.get('Value', '')).strip().upper()

        # Determine new penetration
        new_pct = None

        if cat == 'MOST PURCHASED BRANDS':
            new_pct = mpb_corrections.get(val)
        elif cat in PRODUCT_SUBCATS:
            new_pct = master_lookup.get(val)
        elif cat == 'AUTOMOBILE':
            new_pct = auto_corrections.get(val)
        elif cat == 'GAMES':
            new_pct = games_corrections.get(val)
        elif cat == 'AMUSEMENT PARKS':
            new_pct = ap_corrections.get(val)
        elif cat == 'INTEREST':
            new_pct = int_corrections.get(val)
        elif cat == 'SPORTS ORGANIZATIONS':
            new_pct = so_corrections.get(val)
        elif cat == 'FRANCHISE' or cat == 'TOYS':
            new_pct = master_lookup.get(val)
        else:
            # For all other categories, check if the value has a master correction
            if val in master_lookup:
                try:
                    current = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', ''))
                except (ValueError, TypeError):
                    current = 0
                target = master_lookup[val]
                if abs(current - target) > 0.01:
                    new_pct = target

        if new_pct is not None:
            try:
                current = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', ''))
            except (ValueError, TypeError):
                current = 0

            if abs(current - new_pct) > 0.001:
                new_raw = int(round((new_pct / 100.0) * SAMPLE_SIZE))
                new_genpop = int(round((new_raw / SAMPLE_SIZE) * US_POP))

                df.at[idx, 'Brand Penetration (Row)'] = round(new_pct, 4)
                df.at[idx, 'Original Raw Numbers'] = new_raw
                df.at[idx, 'US Gen Pop Projection'] = new_genpop
                corrected_count += 1
                categories_corrected.add(cat)

    print(f"  {corrected_count} values corrected across {len(categories_corrected)} categories")
    for cat in sorted(categories_corrected):
        count = sum(1 for idx, row in df.iterrows()
                    if str(row.get('Column', '')).strip().upper() == cat)
        print(f"    {cat}: corrected")

    # ── Step 9: Recalculate Category Share ──
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

    print("  Category Share recalculated for all categories")

    # ── Step 10: Verify cross-category consistency ──
    from collections import defaultdict
    val_map = defaultdict(list)
    for idx, row in df.iterrows():
        cat = str(row.get('Column', '')).strip().upper()
        if cat in SKIP_CATEGORIES:
            continue
        val = str(row.get('Value', '')).strip().upper()
        try:
            pct = float(str(row.get('Brand Penetration (Row)', 0)).replace(',', ''))
        except:
            pct = 0
        val_map[val].append((cat, pct, idx))

    inconsistent = 0
    for val, entries in val_map.items():
        behavioral = [(c, p, i) for c, p, i in entries
                      if c not in PRODUCT_SUBCATS and c != 'MOST PURCHASED BRANDS']
        if len(behavioral) < 2:
            continue
        pcts = set(round(e[1], 2) for e in behavioral)
        if len(pcts) > 1:
            inconsistent += 1

    if inconsistent == 0:
        print("  ✅ Cross-category consistency: ALL behavioral values consistent")
    else:
        print(f"  ⚠️ {inconsistent} values still inconsistent across behavioral categories")

    # Check product sub-cat consistency with MPB
    product_inconsistent = 0
    for val, entries in val_map.items():
        mpb_entries = [p for c, p, i in entries if c == 'MOST PURCHASED BRANDS']
        sub_entries = [(c, p, i) for c, p, i in entries if c in PRODUCT_SUBCATS]
        if not mpb_entries or not sub_entries:
            continue
        mpb_pct = round(mpb_entries[0], 2)
        for c, p, i in sub_entries:
            if round(p, 2) != mpb_pct:
                product_inconsistent += 1

    if product_inconsistent == 0:
        print("  ✅ Product sub-category consistency: ALL match MOST PURCHASED BRANDS")
    else:
        print(f"  ⚠️ {product_inconsistent} product sub-category values don't match MPB")

    # ── Save ──
    df.to_csv(CSV_PATH, index=False)
    print(f"\n  Saved to {CSV_PATH}")
    print(f"  DONE: {corrected_count} total corrections applied")


if __name__ == '__main__':
    apply_all_corrections()
