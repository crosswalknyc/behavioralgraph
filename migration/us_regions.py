"""US region and sub-DMA place vocabulary for the interpret stage.

Bind-or-ask guard support (Jenna 2026-08-25): a request naming a
multi-market region ("the Southeast", "Pacific Northwest", "Sun Belt")
or a sub-DMA place ("Brooklyn", "Silicon Valley") must bind to the
canonical Nielsen DMA table instead of silently falling back to a
national default. This module is the single vocabulary both repos use:

- REGION_TO_DMAS: canonical region name -> list of DMA names spelled
  EXACTLY as they appear in scripts/_canonical_dma_baseline.py (the
  same spellings the LOCATION category carries in every profile).
  Each list is ordered biggest market first so echo copy can read
  "Southeast: 20 markets from Atlanta to Miami".
- REGION_ALIASES: lowercase phrasing variants -> canonical region.
- CITY_TO_DMA: common sub-DMA places -> the parent DMA that covers
  them (Brooklyn -> New York Ny).
- DENSITY_TERMS: rural / urban / suburban style qualifiers that do
  NOT map to any DMA set; callers must ask instead of guessing.

TWIN-SYNC RULE: this file lives at migration/us_regions.py in the
parent repo AND bg-webapp/migration/us_regions.py, byte-identical
(same convention as migration/event_window.py). Any edit lands in
both; scripts/test_interpret_semantic_guards.py enforces byte
equality and validates every DMA spelling against the canonical
baseline.

Stdlib only. No pandas, no boto3, no requests.
"""

import re

__all__ = [
    'REGION_TO_DMAS', 'REGION_ALIASES', 'CITY_TO_DMA', 'DENSITY_TERMS',
    'region_dmas', 'detect_region', 'detect_city', 'detect_density_term',
    'dma_display', 'region_echo_label',
]


REGION_TO_DMAS = {
    'Southeast': [
        'Atlanta Ga',
        'Miami Ft Lauderdale Fl',
        'Tampa St Petersburg Sarasota Fl',
        'Orlando Daytona Beach Melbourne Fl',
        'Charlotte Nc',
        'Raleigh Durham Fayetteville Nc',
        'Nashville Tn',
        'Jacksonville Fl',
        'West Palm Beach Ft Pierce Fl',
        'Greenville Spartanburg Sc Asheville Nc Anderson Sc',
        'Birmingham Anniston And Tuscaloosa Al',
        'New Orleans La',
        'Memphis Tn',
        'Greensboro High Point Winston Salem Nc',
        'Columbia Sc',
        'Charleston Sc',
        'Savannah Ga',
        'Knoxville Tn',
        'Ft Myers Naples Fl',
        'Mobile Al Pensacola Ft Walton Beach Fl',
    ],
    'Midwest': [
        'Chicago Il',
        'Detroit Mi',
        'Minneapolis St Paul Mn',
        'Cleveland Akron Canton Oh',
        'St Louis Mo',
        'Indianapolis In',
        'Columbus Oh',
        'Kansas City Mo',
        'Cincinnati Oh',
        'Milwaukee Wi',
        'Grand Rapids Kalamazoo Battle Creek Mi',
        'Omaha Ne',
        'Des Moines Ames Ia',
        'Green Bay Appleton Wi',
        'Madison Wi',
        'Dayton Oh',
        'Toledo Oh',
        'Flint Saginaw Bay City Mi',
        'Wichita Hutchinson Ks Plus',
        'South Bend Elkhart In',
    ],
    'Northeast': [
        'New York Ny',
        'Philadelphia Pa',
        'Boston Ma Manchester Nh',
        'Pittsburgh Pa',
        'Hartford & New Haven Ct',
        'Providence Ri New Bedford Ma',
        'Albany Schenectady Troy Ny',
        'Buffalo Ny',
        'Rochester Ny',
        'Syracuse Ny',
        'Portland Auburn Me',
        'Burlington Vt Plattsburgh Ny',
        'Springfield Holyoke Ma',
        'Wilkes Barre Scranton Hazleton Pa',
        'Harrisburg Lancaster Lebanon York Pa',
    ],
    'New England': [
        'Boston Ma Manchester Nh',
        'Hartford & New Haven Ct',
        'Providence Ri New Bedford Ma',
        'Portland Auburn Me',
        'Burlington Vt Plattsburgh Ny',
        'Springfield Holyoke Ma',
        'Bangor Me',
        'Presque Isle Me',
    ],
    'Pacific Northwest': [
        'Seattle Tacoma Wa',
        'Portland Or',
        'Spokane Wa',
        'Eugene Or',
        'Yakima Pasco Richland Kennewick Wa',
        'Medford Klamath Falls Or',
        'Bend Or',
    ],
    'Sun Belt': [
        'Los Angeles Ca',
        'Dallas Ft Worth Tx',
        'Houston Tx',
        'Atlanta Ga',
        'Phoenix Prescott Az',
        'Miami Ft Lauderdale Fl',
        'Tampa St Petersburg Sarasota Fl',
        'Orlando Daytona Beach Melbourne Fl',
        'San Antonio Tx',
        'Austin Tx',
        'Las Vegas Nv',
        'San Diego Ca',
        'Charlotte Nc',
        'Jacksonville Fl',
        'New Orleans La',
        'Tucson Sierra Vista Az',
        'Albuquerque Santa Fe Nm',
        'El Paso Tx Las Cruces Nm',
    ],
    'Southwest': [
        'Phoenix Prescott Az',
        'Las Vegas Nv',
        'Albuquerque Santa Fe Nm',
        'Tucson Sierra Vista Az',
        'El Paso Tx Las Cruces Nm',
        'Yuma Az El Centro Ca',
    ],
    'Mid-Atlantic': [
        'New York Ny',
        'Philadelphia Pa',
        'Washington Dc Hagerstown Md',
        'Baltimore Md',
        'Pittsburgh Pa',
        'Norfolk Portsmouth Newport News Va',
        'Richmond Petersburg Va',
        'Harrisburg Lancaster Lebanon York Pa',
        'Salisbury Md',
    ],
    'Mountain West': [
        'Denver Co',
        'Salt Lake City Ut',
        'Boise Id',
        'Colorado Springs Pueblo Co',
        'Reno Nv',
        'Billings Mt',
        'Idaho Falls Pocatello Id Jackson Wy',
        'Missoula Mt',
        'Grand Junction Montrose Co',
        'Cheyenne Wy Scottsbluff Ne',
        'Butte Bozeman Mt',
        'Great Falls Mt',
        'Casper Riverton Wy',
    ],
    'West Coast': [
        'Los Angeles Ca',
        'San Francisco Oakland San Jose Ca',
        'Seattle Tacoma Wa',
        'Portland Or',
        'San Diego Ca',
        'Sacramento Stockton Modesto Ca',
        'Fresno Visalia Ca',
        'Bakersfield Ca',
        'Santa Barbara Santa Maria San Luis Obispo Ca',
        'Monterey Salinas Ca',
        'Eugene Or',
        'Chico Redding Ca',
        'Palm Springs Ca',
        'Eureka Ca',
    ],
    'Rust Belt': [
        'Pittsburgh Pa',
        'Cleveland Akron Canton Oh',
        'Detroit Mi',
        'Buffalo Ny',
        'Milwaukee Wi',
        'Toledo Oh',
        'Youngstown Oh',
        'Flint Saginaw Bay City Mi',
        'Erie Pa',
        'Wheeling Wv Steubenville Oh',
        'South Bend Elkhart In',
    ],
    'Deep South': [
        'Atlanta Ga',
        'New Orleans La',
        'Birmingham Anniston And Tuscaloosa Al',
        'Jackson Ms',
        'Baton Rouge La',
        'Montgomery Selma Al',
        'Mobile Al Pensacola Ft Walton Beach Fl',
        'Huntsville Decatur Florence Al',
        'Columbus Ga Opelika Al',
        'Macon Ga',
        'Savannah Ga',
        'Shreveport La',
        'Biloxi Gulfport Ms',
        'Hattiesburg Laurel Ms',
    ],
    'Gulf Coast': [
        'Houston Tx',
        'Tampa St Petersburg Sarasota Fl',
        'New Orleans La',
        'Mobile Al Pensacola Ft Walton Beach Fl',
        'Baton Rouge La',
        'Corpus Christi Tx',
        'Ft Myers Naples Fl',
        'Biloxi Gulfport Ms',
    ],
    'Great Plains': [
        'Kansas City Mo',
        'Oklahoma City Ok',
        'Omaha Ne',
        'Tulsa Ok',
        'Des Moines Ames Ia',
        'Wichita Hutchinson Ks Plus',
        'Sioux Falls Mitchell Sd',
        'Fargo Nd',
        'Lincoln & Hastings Kearney Ne',
        'Topeka Ks',
    ],
}


# Lowercase phrasing variants -> canonical REGION_TO_DMAS key. Word
# boundaries are applied by detect_region; longer aliases are matched
# first so 'pacific northwest' wins over a bare 'northwest'.
REGION_ALIASES = {
    'southeast': 'Southeast',
    'the southeast': 'Southeast',
    'southeastern us': 'Southeast',
    'southeastern united states': 'Southeast',
    'the south': 'Southeast',
    'southern us': 'Southeast',
    'midwest': 'Midwest',
    'the midwest': 'Midwest',
    'midwestern us': 'Midwest',
    'midwestern united states': 'Midwest',
    'northeast': 'Northeast',
    'the northeast': 'Northeast',
    'northeastern us': 'Northeast',
    'northeastern united states': 'Northeast',
    'new england': 'New England',
    'pacific northwest': 'Pacific Northwest',
    'the pacific northwest': 'Pacific Northwest',
    'pacific nw': 'Pacific Northwest',
    'pnw': 'Pacific Northwest',
    'the northwest': 'Pacific Northwest',
    'sun belt': 'Sun Belt',
    'sunbelt': 'Sun Belt',
    'the sun belt': 'Sun Belt',
    'southwest': 'Southwest',
    'the southwest': 'Southwest',
    'southwestern us': 'Southwest',
    'mid-atlantic': 'Mid-Atlantic',
    'mid atlantic': 'Mid-Atlantic',
    'midatlantic': 'Mid-Atlantic',
    'the mid-atlantic': 'Mid-Atlantic',
    'mountain west': 'Mountain West',
    'the mountain west': 'Mountain West',
    'the rockies': 'Mountain West',
    'rocky mountain region': 'Mountain West',
    'west coast': 'West Coast',
    'the west coast': 'West Coast',
    'rust belt': 'Rust Belt',
    'rustbelt': 'Rust Belt',
    'the rust belt': 'Rust Belt',
    'deep south': 'Deep South',
    'the deep south': 'Deep South',
    'gulf coast': 'Gulf Coast',
    'the gulf coast': 'Gulf Coast',
    'great plains': 'Great Plains',
    'the great plains': 'Great Plains',
    'the plains': 'Great Plains',
}


# Common sub-DMA places -> the parent DMA that covers them, spelled
# exactly as the canonical baseline does. Detection is "in <place>"
# style (see detect_city) to avoid false positives on brand names
# ('Hollywood movies') and person names.
CITY_TO_DMA = {
    'brooklyn': 'New York Ny',
    'manhattan': 'New York Ny',
    'queens': 'New York Ny',
    'the bronx': 'New York Ny',
    'staten island': 'New York Ny',
    'long island': 'New York Ny',
    'harlem': 'New York Ny',
    'newark': 'New York Ny',
    'jersey city': 'New York Ny',
    'hoboken': 'New York Ny',
    'westchester': 'New York Ny',
    'silicon valley': 'San Francisco Oakland San Jose Ca',
    'the bay area': 'San Francisco Oakland San Jose Ca',
    'bay area': 'San Francisco Oakland San Jose Ca',
    'oakland': 'San Francisco Oakland San Jose Ca',
    'san jose': 'San Francisco Oakland San Jose Ca',
    'berkeley': 'San Francisco Oakland San Jose Ca',
    'palo alto': 'San Francisco Oakland San Jose Ca',
    'mountain view': 'San Francisco Oakland San Jose Ca',
    'cupertino': 'San Francisco Oakland San Jose Ca',
    'napa valley': 'San Francisco Oakland San Jose Ca',
    'hollywood': 'Los Angeles Ca',
    'beverly hills': 'Los Angeles Ca',
    'santa monica': 'Los Angeles Ca',
    'long beach': 'Los Angeles Ca',
    'pasadena': 'Los Angeles Ca',
    'burbank': 'Los Angeles Ca',
    'anaheim': 'Los Angeles Ca',
    'orange county': 'Los Angeles Ca',
    'malibu': 'Los Angeles Ca',
    'compton': 'Los Angeles Ca',
    'inglewood': 'Los Angeles Ca',
    'south beach': 'Miami Ft Lauderdale Fl',
    'miami beach': 'Miami Ft Lauderdale Fl',
    'fort lauderdale': 'Miami Ft Lauderdale Fl',
    'ft lauderdale': 'Miami Ft Lauderdale Fl',
    'georgetown': 'Washington Dc Hagerstown Md',
    'arlington': 'Washington Dc Hagerstown Md',
    'bethesda': 'Washington Dc Hagerstown Md',
    'cambridge': 'Boston Ma Manchester Nh',
    'scottsdale': 'Phoenix Prescott Az',
    'tempe': 'Phoenix Prescott Az',
    'mesa': 'Phoenix Prescott Az',
    'fort worth': 'Dallas Ft Worth Tx',
    'plano': 'Dallas Ft Worth Tx',
    'frisco': 'Dallas Ft Worth Tx',
    'the woodlands': 'Houston Tx',
    'katy': 'Houston Tx',
    'st petersburg': 'Tampa St Petersburg Sarasota Fl',
    'sarasota': 'Tampa St Petersburg Sarasota Fl',
    'clearwater': 'Tampa St Petersburg Sarasota Fl',
    'naperville': 'Chicago Il',
    'evanston': 'Chicago Il',
    'ann arbor': 'Detroit Mi',
    'boulder': 'Denver Co',
    'tacoma': 'Seattle Tacoma Wa',
    'bellevue': 'Seattle Tacoma Wa',
    'redmond': 'Seattle Tacoma Wa',
}


# Density qualifiers that cannot be mapped onto DMAs at all. Callers
# must ask the user for a supported framing (specific markets or a
# named region) instead of guessing.
DENSITY_TERMS = ('rural', 'urban', 'suburban', 'small town',
                 'small-town', 'inner city', 'inner-city')


# Display overrides for DMA names whose leading tokens are not the
# plain city name. Everything else falls back to the first word.
_DMA_DISPLAY_OVERRIDES = {
    'New York Ny': 'New York',
    'Los Angeles Ca': 'Los Angeles',
    'San Francisco Oakland San Jose Ca': 'San Francisco',
    'San Antonio Tx': 'San Antonio',
    'San Diego Ca': 'San Diego',
    'Santa Barbara Santa Maria San Luis Obispo Ca': 'Santa Barbara',
    'New Orleans La': 'New Orleans',
    'Las Vegas Nv': 'Las Vegas',
    'St Louis Mo': 'St Louis',
    'Kansas City Mo': 'Kansas City',
    'Oklahoma City Ok': 'Oklahoma City',
    'Salt Lake City Ut': 'Salt Lake City',
    'Colorado Springs Pueblo Co': 'Colorado Springs',
    'West Palm Beach Ft Pierce Fl': 'West Palm Beach',
    'El Paso Tx Las Cruces Nm': 'El Paso',
    'Des Moines Ames Ia': 'Des Moines',
    'Green Bay Appleton Wi': 'Green Bay',
    'Grand Rapids Kalamazoo Battle Creek Mi': 'Grand Rapids',
    'Grand Junction Montrose Co': 'Grand Junction',
    'Dallas Ft Worth Tx': 'Dallas',
    'Ft Myers Naples Fl': 'Ft Myers',
    'Sioux Falls Mitchell Sd': 'Sioux Falls',
    'Great Falls Mt': 'Great Falls',
    'Idaho Falls Pocatello Id Jackson Wy': 'Idaho Falls',
    'South Bend Elkhart In': 'South Bend',
    'Baton Rouge La': 'Baton Rouge',
    'Corpus Christi Tx': 'Corpus Christi',
    'Palm Springs Ca': 'Palm Springs',
    'Washington Dc Hagerstown Md': 'Washington DC',
    'Wilkes Barre Scranton Hazleton Pa': 'Wilkes Barre',
}


def dma_display(dma):
    """Short human name for a canonical DMA ('Miami Ft Lauderdale Fl'
    -> 'Miami'). Used in echo copy only, never in spec fields."""
    dma = str(dma or '').strip()
    if not dma:
        return ''
    if dma in _DMA_DISPLAY_OVERRIDES:
        return _DMA_DISPLAY_OVERRIDES[dma]
    return dma.split()[0]


def region_dmas(label):
    """Alias-tolerant region lookup. Returns (canonical_region, [dma,
    ...]) or (None, None) when the label is not a known region."""
    raw = ' '.join(str(label or '').lower().split())
    if not raw:
        return None, None
    canon = REGION_ALIASES.get(raw)
    if not canon:
        for key in REGION_TO_DMAS:
            if raw == key.lower():
                canon = key
                break
    if not canon:
        return None, None
    return canon, list(REGION_TO_DMAS[canon])


def _alias_pattern():
    # Longest aliases first so 'pacific northwest' beats 'northwest'
    # and 'the sun belt' beats 'sun belt'.
    keys = sorted(REGION_ALIASES, key=len, reverse=True)
    return re.compile(
        r'\b(' + '|'.join(re.escape(k) for k in keys) + r')\b',
        re.IGNORECASE)


_REGION_RE = _alias_pattern()


def detect_region(text):
    """First multi-market region named in free text. Returns
    (canonical_region, [dma, ...]) or (None, None)."""
    m = _REGION_RE.search(str(text or ''))
    if not m:
        return None, None
    return region_dmas(m.group(1))


_CITY_RE = re.compile(
    r'\b(?:in|from|around|across)\s+(?:the\s+)?('
    + '|'.join(re.escape(k) for k in sorted(CITY_TO_DMA, key=len,
                                            reverse=True))
    + r')\b', re.IGNORECASE)


def detect_city(text):
    """Sub-DMA place named with a locative preposition ('in Brooklyn',
    'around Silicon Valley'). Returns (place_as_written, parent_dma)
    or (None, None). The preposition requirement keeps brand and
    person names ('Hollywood movies') from false-positive matching."""
    m = _CITY_RE.search(str(text or ''))
    if not m:
        return None, None
    place = m.group(1)
    key = ' '.join(place.lower().split())
    if key not in CITY_TO_DMA:
        key = 'the ' + key if ('the ' + key) in CITY_TO_DMA else key
    dma = CITY_TO_DMA.get(key)
    if not dma:
        return None, None
    return place, dma


_DENSITY_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in DENSITY_TERMS)
    + r')\b(?=.{0,40}\b(?:audience|audiences|viewers|consumers|'
    r'shoppers|customers|fans|users|households|markets|areas|'
    r'america|americans|communities|counties|towns|cut|cuts|only)\b)',
    re.IGNORECASE | re.DOTALL)


def detect_density_term(text):
    """Rural / urban / suburban style qualifier used as an audience
    scope. Returns the matched term or '' when absent. These do NOT
    map to DMAs; callers ask for a supported framing instead."""
    m = _DENSITY_RE.search(str(text or ''))
    return m.group(1).lower() if m else ''


def region_echo_label(region):
    """Echo copy for a bound region: 'Southeast: 20 markets from
    Atlanta to Miami'. Uses the first two entries of the region's
    DMA list (ordered biggest market first)."""
    canon, dmas = region_dmas(region)
    if not canon or not dmas:
        return ''
    n = len(dmas)
    if n >= 2:
        return (f"{canon}: {n} markets from {dma_display(dmas[0])} "
                f"to {dma_display(dmas[1])}")
    return f"{canon}: {dma_display(dmas[0])}"
