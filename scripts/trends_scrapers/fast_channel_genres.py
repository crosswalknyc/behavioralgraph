"""
Channel-type labels for the FAST micro-channels in the Channel Ranker.

Every FAST platform ships hundreds of linear micro-channels (Nick Jr.
Pluto TV, Forensic Files 24/7, Waypoint TV, Mr. Bean: Animated). The
MediaBiz lineup workbooks carry a `Channel Content Type` column, but
its only values are Series / Non-Series / blank, which describes
programming format rather than what the channel is about. This module
derives the channel type from the channel name instead.

Output lands at
    s3://dashboard-inputs/trends_iq_snapshots/latest/fast_channel_genres.json
shaped as

    {
      "version": 1,
      "generated_at": "2026-09-14T...",
      "taxonomy": ["Anime", "Classic TV", ...],
      "counts":   {"Sports": 118, "Espanol": 131, ...},
      "genres":   {"<normalized name>": "Sports", ...},
      "names":    {"<normalized name>": "FOX Sports", ...}
    }

Keys are `_cp_normalize(channel_name)`, the same normalizer
`trends_iq` uses to build `fast_channel:<platform>:<norm>` keys, so one
label serves every platform a channel appears on.

Two passes, in order:

  1. Deterministic name rules (`_RULE_ORDER`). Self-describing names
     resolve here for free and stay stable across runs.
  2. One batched Haiku pass over whatever the rules did not resolve,
     metered through the shared SpendMonitor and the Trends usage tap.

`refresh()` is incremental: names already present in the artifact are
never re-labeled, so a new MediaBiz lineup only pays for the channels
it actually adds. Every failure path degrades to "return what we have"
so a lineup build never blocks on this.

Usage:
    python3 -m scripts.trends_scrapers.fast_channel_genres
    python3 -m scripts.trends_scrapers.fast_channel_genres --dry-run
    python3 -m scripts.trends_scrapers.fast_channel_genres --rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from . import _base
from . import _usage_tap
from ._spend_monitor import SpendMonitor

logger = logging.getLogger(__name__)

SOURCE = 'fast_channel_genres'
LINEUPS_SOURCE = 'fast_channel_lineups'
VERSION = 1

FALLBACK = 'Other'

# Fifteen labels plus the fallback. Sized against the ~1,100 distinct
# channel names in the current lineup: every one of these carries at
# least ten channels, and folding any of them loses a real buying
# distinction (Espanol and Kids and Family especially).
TAXONOMY: tuple[str, ...] = (
    'Anime',
    'Classic TV',
    'Comedy',
    'Crime',
    'Documentary',
    'Entertainment',
    'Espanol',
    'Food and Home',
    'Kids and Family',
    'Lifestyle',
    'Movies',
    'Music',
    'News',
    'Reality',
    'Sports',
)

_VALID = set(TAXONOMY) | {FALLBACK}
_BY_LOWER = {g.lower(): g for g in _VALID}

_MODEL = os.environ.get('FAST_GENRE_MODEL') or 'claude-haiku-4-5'
_BATCH_CHUNK = 60
_BATCH_POLL_SECONDS = 15
_BATCH_MAX_MINUTES = 45
_SPEND_CAP_USD = float(os.environ.get('FAST_GENRE_SPEND_CAP') or 5.0)


def _cp_normalize(text: str) -> str:
    """Key normalizer, byte-identical in behavior to
    `trends_iq._cp_normalize`. Duplicated rather than imported because
    the scrapers run standalone on the build box without the Flask app
    on the path."""
    if not text:
        return ''
    s = str(text).lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _CP_STOPWORDS]
    return ' '.join(tokens)


# Kept byte-identical to `trends_iq._CP_STOPWORDS`. Note that it drops
# 'news', 'now', 'today', 'latest' and 'best', so the normalized key
# for "CBS News 24/7" is "cbs 24 7". Rules below therefore match the
# raw name, never the key.
_CP_STOPWORDS = {
    'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at', 'is',
    'trending', 'today', 'now', 'news', 'latest', 'best',
}


# Hand corrections, keyed by normalized name. Checked before any regex
# and re-asserted on every run, so fixing one here repairs the stored
# artifact without re-labeling the whole lineup.
_OVERRIDES: dict[str, str] = {
    # Rule table would land these somewhere defensible but wrong.
    'my wife kids':            'Comedy',      # sitcom, not a kids channel
    'home improvement':        'Comedy',      # sitcom, not a home channel
    'designated survivor':     'Entertainment',  # drama, not Survivor
    'family feud classic':     'Reality',     # game show, not a library
    'mysterious worlds':       'Documentary',
    'classic car auctions':    'Lifestyle',
    'nat geo travel':          'Lifestyle',
    'tastemade travel':        'Lifestyle',
    'xtreme outdoor by history': 'Lifestyle',
    'amp':                     'Music',
    'supermarket sweep':       'Reality',

    # Spanish-language channels whose names carry no Spanish token the
    # rule table keys on.
    'c4 en alerta':                            'Espanol',
    'caso cerrado con la dra ana maria polo':  'Espanol',
    'como dice el dicho':                      'Espanol',
    'construcciones asombrosas':               'Espanol',
    'cuando los angeles caen':                 'Espanol',
    'freetv hits':                             'Espanol',

    # Shopping, faith and community channels all sit under Lifestyle.
    'home shopping network':   'Lifestyle',
    'qvc':                     'Lifestyle',
    'qvc2':                    'Lifestyle',
    'shop lc':                 'Lifestyle',
    'jtv jewelry love':        'Lifestyle',
    'deal zone':               'Lifestyle',
    'amazon live':             'Lifestyle',
    'pureflix tv':             'Lifestyle',
    'joysauce':                'Lifestyle',
    'localish':                'Lifestyle',

    'like nastya':             'Kids and Family',
    'zhong':                   'Comedy',          # prank creator channel
    'tv land drama':           'Entertainment',
    'beyond gates':            'Entertainment',   # daytime soap
    'wanted dead or alive':    'Classic TV',
    'bargain hunt':            'Food and Home',   # matches the antiques set
    'grit xtra':               'Movies',          # westerns and action films
    'prof g pod with scott galloway': 'News',
}


# Lineups that come from a platform's own guide rather than a MediaBiz
# workbook. Each owns a snapshot; the workbook platforms share
# `fast_channel_lineups`. Kept in step with
# `trends_iq.API_LINEUP_SOURCES`.
_API_LINEUP_SOURCES = ('vizio_watchfree', 'lg_channels', 'myfree_directv',
                        'philo_free', 'plex_live', 'sling_freestream')


# Vizio and LG file every channel under their own category, DIRECTV
# under one of three headings, Philo under a genre shelf and Sling
# under a guide filter. Those labels are real editorial signal and
# mapping them is cheaper and steadier than asking a model to
# re-derive what the platform already said. This maps the ones that
# land unambiguously on our 15; anything genuinely ambiguous is left
# out deliberately and falls through to the model, which sees the
# publisher's label as context. So an absent label costs a shortcut,
# never the channel's type.
#
# Plex is absent from this map on purpose rather than by omission. Its
# rows carry `genreRatingKeys`, but they are opaque hashes and Plex
# resolves no names for them, so naming those clusters here would be
# inventing a publisher label rather than carrying one. The one Plex
# signal that is unambiguous is `language`, and the scraper ships
# Spanish-language rows under Vizio's existing wording so they land on
# 'en espanol' below instead of needing a new key.
#
# Deliberately absent, and why:
#   Vizio  FOOD + TRAVEL  - splits across Food and Home / Lifestyle
#          CREATORS       - a creator channel takes its subject's type
#   LG     Latin          - Latin-language vs Latin-music is the
#                           Espanol / Music call, and it matters
#          TV & Movies    - two of our types in one label
#   DIRECTV Entertainment - 127 of their 161 channels, says nothing
#   Sling  GAMES & ANIME  - Anime is one of our 15 and Games is not,
#                           so the label spans a type we carry and one
#                           we do not
#          ACTION & THRILLERS  - splits across Movies / Entertainment
#          BLACK ENTERTAINMENT - an audience, not one of the 15
#   Philo  Home & Lifestyle    - splits across Food and Home /
#                                Lifestyle, the LG TV & Movies case
#          Crime & Drama       - Crime and Entertainment in one label
#          Outdoors & Sports   - Sports and Lifestyle in one label
#          Action, Sci-Fi & Fantasy - both split Movies /
#                                Entertainment
_SOURCE_GENRE_MAP: dict[str, str] = {
    # Vizio WatchFree+
    'sports':               'Sports',
    'movies':               'Movies',
    'crime':                'Crime',
    'en espanol':           'Espanol',
    'en español':           'Espanol',
    'news + opinion':       'News',
    # Vizio files its 88 single-market broadcast feeds here and every
    # one of them is a local news station.
    'local channels':       'News',
    'reality':              'Reality',
    'music':                'Music',
    'kids + family':        'Kids and Family',
    'westerns + classics':  'Classic TV',
    'nature + science':     'Documentary',
    'history + docs':       'Documentary',
    'home':                 'Food and Home',
    'game shows':           'Reality',
    'comedy':               'Comedy',
    'entertainment':        'Entertainment',
    'tv':                   'Entertainment',
    'mood + ambiance':      'Lifestyle',
    'inspiration + faith':  'Lifestyle',
    'shopping':             'Lifestyle',
    # LG Channels
    'news':                 'News',
    'sport':                'Sports',
    'drama':                'Entertainment',
    'talk show & entertainment': 'Entertainment',
    'reality tv':           'Reality',
    'westerns':             'Classic TV',
    'spanish language':     'Espanol',
    'nature':               'Documentary',
    'documentary':          'Documentary',
    'kids':                 'Kids and Family',
    'food':                 'Food and Home',
    'ambiance':             'Lifestyle',
    'lifestyle':            'Lifestyle',
    'hobby/leisure':        'Lifestyle',
    # MyFree DIRECTV
    'national sports':      'Sports',
    'news & information':   'News',
    # Sling Freestream. Every one of these has a Vizio or LG
    # precedent carrying the same words with different punctuation.
    'news & opinion':       'News',            # 80 channels, the biggest
    'true crime':           'Crime',
    'classics & re-runs':   'Classic TV',
    'science & nature':     'Documentary',
    'docs & history':       'Documentary',
    'kids & family':        'Kids and Family',
    # Philo Free
    'family':               'Kids and Family',
}


def source_genre_label(source_genre: str) -> Optional[str]:
    """Our taxonomy label for a platform's own category, when that
    mapping is unambiguous. None means "ask", not "Other"."""
    return _SOURCE_GENRE_MAP.get(
        str(source_genre or '').strip().lower()) or None


def _rx(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


# Ordered rule table. First genre whose pattern matches the raw channel
# name wins, so the order encodes the tie-breaks: a Spanish-language
# channel reads Espanol before it reads News or Crime, a preschool
# channel reads Kids and Family before it reads Movies, and a music
# brand reads Music before a decade token sends it to Classic TV.
_RULE_ORDER: list[tuple[str, list[re.Pattern]]] = [
    ('Espanol', _rx(
        r'\ben espanol\b', r'\bespanol\b', r'\bespa\u00f1ol\b',
        r'\bnoticias\b', r'\btelenovela', r'\bnovelas?\b',
        r'\bpelicul', r'\bdeportes\b', r'\bcine\b', r'\bcinepolis\b',
        r'\btelemundo\b', r'\bunivision\b', r'\bazteca\b', r'\bcanela\b',
        r'\bvix\b', r'\bestrella\b', r'\bcaracol\b', r'\brcn\b',
        r'\bvenevision\b', r'\bwapa\b', r'\btudn\b', r'\bjajaja\b',
        r'\(es\)\s*$', r'\bfilmex\b', r'\bmoovimex\b', r'\btodo cine\b',
        r'\bcrimen\b', r'\bclasico', r'\bcorazon\b', r'\bgalanes\b',
        r'\bla familia\b', r'\bruntime\b', r'\brebelde\b',
    )),
    ('Kids and Family', _rx(
        r'\bkids?\b', r'\bjr\.?\b', r'\bnickelodeon\b', r'\bcartoon',
        r'\bpreschool\b', r'\bbaby\b', r'\btoddler\b', r'\bnursery\b',
        r'\bpeppa pig\b', r'\bteletubbies\b', r'\bbarney\b', r'\bcaillou\b',
        r'\bmoonbug\b', r'\bkartoon\b', r'\bsesame\b',
        r'\bstrawberry shortcake\b', r'\byo gabba gabba\b',
        r'\bpower rangers\b', r'\bpokemon\b', r'\bsonic\b',
        r'\bsuper mario\b', r'\blego\b', r'\bhasbro\b', r'\btransformers\b',
        r'\bhe-man\b', r'\bgarfield\b', r'\binspector gadget\b',
        r'\bpink panther\b', r'\bmr\.? bean\b', r'\bslugterra\b',
        r'\bgrizzy\b', r'\brainbow ruby\b', r'\blittle angel',
        r'\blittle baby bum\b', r'\bsuper simple songs\b',
        r'\bmister rogers\b', r'\blassie\b', r'\bpocket\.watch\b',
        r'\bninja kidz\b', r'\baphmau\b', r'\bbrat tv\b',
        r'\bel reino infantil\b', r'\bpitufo\b', r'\bdinos\b',
        r'\banimation\+', r'\btotally turtles\b', r'\blittle stars\b',
        r'\bdungeons & dragons adventures\b', r'\brev and roll\b',
    )),
    ('Anime', _rx(
        r'\banime\b', r'\bcrunchyroll\b', r'\bhidive\b', r'\bnaruto\b',
        r'\bboruto\b', r'\bone piece\b', r'\bhunter x hunter\b',
        r'\binuyasha\b', r'\bsailor moon\b', r'\byu-gi-oh', r'\bdragon ball\b',
    )),
    ('News', _rx(
        r'\bnews\b', r'\beuronews\b', r'\bnewsmax', r'\bweather\b',
        r'\bheadlines\b', r'\breuters\b', r'\bcnn\b', r'\bmsnbc\b',
        r'\bbloomberg\b', r'\boan plus\b', r'\btoday all day\b',
        r'\bthe hill tv\b', r'\baccuweather\b', r'\bweathernation\b',
        r'\bcheddar\b', r'\breal america', r'\btyt network\b',
        r'\byahoo! finance\b', r'\bdaily wire\b', r'\blivenow\b',
        r'\btelediario\b', r'\bscripps news\b',
    )),
    ('Sports', _rx(
        r'\bsports?\b', r'\bnfl\b', r'\bnba\b', r'\bmlb\b', r'\bnhl\b',
        r'\bwnba\b', r'\bnascar\b', r'\bufc\b', r'\bmma\b', r'\bboxing\b',
        r'\bwrestling\b', r'\bwwe\b', r'\baew\b', r'\btna\b', r'\bgolf',
        r'\bpga\b', r'\btennis\b', r'\bsoccer\b', r'\bfifa\b', r'\buefa\b',
        r'\bchampions league\b', r'\bliga\b', r'\brugby', r'\bpoker',
        r'\bracing\b', r'\bmotogp\b', r'\bformula 1\b', r'\bnhra\b',
        r'\bbowling\b', r'\bbilliard\b', r'\bpickleball\b', r'\bpickletv\b',
        r'\bsurf league\b', r'\bx games\b', r'\bdraftkings\b',
        r'\bfanduel\b', r'\bbein\b', r'\bespn', r'\bstadium\b',
        r'\bsportsgrid\b', r'\bflosports\b', r'\bflohockey\b',
        r'\bfloracing\b', r'\bpbr ridepass\b', r'\bmonster jam\b',
        r'\bkickboxing\b', r'\bone championship\b', r'\bbellator\b',
        r'\bpfl\b', r'\bdazn\b', r'\breal madrid\b', r'\bbarca\b',
        r'\bpac-12\b', r'\bbig 12\b', r'\bacc digital\b', r'\bhbcu\b',
        r'\bnesn\b', r'\bmsg\b', r'\bcowboy\+', r'\bteam usa\b',
        r'\bolympic', r'\bgolazo\b', r'\bfubo\b', r'\bwillow\b',
        r'\bovertime\b', r'\bswerve (sports|combat|women)',
        r'\bracer select\b', r'\btop rank\b', r'\bglory\b',
        r'\bthe ocho\b', r'\bjim rome\b', r'\bfutcrunch\b', r'\bestv\b',
        r'\bspeed sport\b', r'\bpro league network\b', r'\bryz\b',
        r'\bunbeaten\b', r'\bbassmaster\b', r'\bteam liquid\b',
    )),
    ('Food and Home', _rx(
        r'\bfood\b', r'\bcook', r'\bkitchen\b', r'\bchef\b', r'\brecipe',
        r'\bbaking\b', r'\bculinary\b', r'\bdining\b', r'\beats\b',
        r'\bdish\b', r'\bbon appetit\b', r'\btastemade\b',
        r'\bgordon ramsay\b', r'\biron chef\b', r'\bemeril\b',
        r'\bjamie oliver\b', r'\blidia\b', r'\bbobby flay\b',
        r'\bsmokehouse\b', r'\bgarden\b', r'\bthis old house\b',
        r'\brepair shop\b', r'\bproperty brothers\b', r'\bhouse hunters\b',
        r'\btiny house\b', r'\bhome crashers\b', r'\bcurb appeal\b',
        r'\bhome edition\b', r'\bmy first place\b', r'\brenovat',
        r'\bhandyman\b', r'\bgrand designs\b', r'\bmartha stewart\b',
        r'\bbob ross\b', r'\bcrafts\b', r'\binterior', r'\bdecorat',
        r'\bhome\.made\b', r'\bhomeful\b', r'\brustic retreats\b',
        r'\bultimate builds\b', r'\bplaces & spaces\b',
        r'\bfeels like home\b', r'\bbbc home\b',
    )),
    ('Music', _rx(
        r'\bvevo\b', r'\bxite\b', r'\biheart', r'\bmusic\b', r'\bradio\b',
        r'\bconcert', r'\bhip-?hop\b', r'\brap\b', r'\bgospel\b',
        r'\bcountry\b', r'\bjazz\b', r'\brock\b', r'\bpop\b', r'\br&b\b',
        r'\brevolt\b', r'\bveeps\b', r'\bdef jam\b', r'\blamusica\b',
        r'\byo! mtv\b', r'\bmtv (biggest pop|flow latino|spankin)',
        r'\bstingray (djazz|qello|classica|smooth jazz|easy listening)\b',
        r'\bmixtape\b', r'\bzach sang\b',
    )),
    ('Crime', _rx(
        r'\bcrime', r'\bcriminal', r'\bmurder', r'\bkiller', r'\bhomicide\b',
        r'\bforensic', r'\bdetective', r'\bcold case', r'\bcops\b',
        r'\bpolice\b', r'\bfbi\b', r'\blapd\b', r'\bnypd\b', r'\bjail\b',
        r'\bcourt\b', r'\bjudge\b', r'\bjustice\b', r'\bjury\b',
        r'\blaw & crime\b', r'\blaw and order\b', r'\bdateline\b',
        r'\bfirst 48\b', r'\b48 hours\b', r'\b20/20\b', r'\bunsolved\b',
        r'\bmyster', r'\binvestigation\b', r'\bbounty hunter\b',
        r'\blive pd\b', r'\bsmuggler\b', r'\blocked up\b', r'\bevil\b',
        r'\bscandal', r'\bstate troopers\b', r'\bcaught in providence\b',
    )),
    ('Comedy', _rx(
        r'\bcomedy\b', r'\bsitcom', r'\bstand-?up\b', r'\bfunny\b',
        r'\bfunniest\b', r'\blaughs?\b', r'\bhumor\b', r'\blol\b',
        r'\bcomedians?\b', r'\bsnl\b', r'\btosh\.0\b', r'\bwild .?n.? out\b',
        r'\bfailarmy\b', r'\bmst3k\b', r'\brifftrax\b',
        r'\bnational lampoon\b', r'\bthree stooges\b',
        r'\bcarol burnett\b', r'\bportlandia\b', r'\btrailer park boys\b',
        r'\b(quirky|sketchy|funny) af\b', r'\bfluffy tv\b',
        r'\bgraham norton\b', r'\bjohnny carson\b',
    )),
    ('Movies', _rx(
        r'\bmovies?\b', r'\bcinema\b', r'\bfilms?\b', r'\bcinevault\b',
        r'\bpictures\b', r'\bmgm presents\b', r'\bmiramax\b',
        r'\bhorror\b', r'\bterror\b', r'\bwestern', r'\bthrillers?\b',
        r'\bsci-fi\b', r'\bromcom', r'\bshudder\b', r'\bscreambox\b',
        r'\bmoviesphere\b', r'\bpopcorn central\b', r'\bflicks\b',
        r'\bhi-yah\b', r'\bthe asylum\b', r'\btribeca\b',
        r'\buniversal (action|monsters|movies)\b',
    )),
    ('Classic TV', _rx(
        r'\bclassics?\b', r'\bretro\b', r'\bthrowbacks?\b', r'\bvintage\b',
        r'\bnostalg', r'\brewind\b', r'\bbuzzr\b',
        r'\b(50s|60s|70s|80s|90s|00s)\b', r'\bed sullivan\b',
    )),
    ('Documentary', _rx(
        r'\bdocumentar', r'\bnat geo\b', r'\bsmithsonian\b',
        r'\bmagellantv\b', r'\bcuriosity\b', r'\bscience\b', r'\bnasa\b',
        r'\bhistory\b', r'\bmythbusters\b', r'\bmodern marvels\b',
        r'\bancient aliens\b', r'\bbbc earth\b', r'\bmilitary\b',
        r'\bwar channel\b', r'\bpbs (documentaries|nature|genealogy)\b',
    )),
    ('Reality', _rx(
        r'\breality\b', r'\bunscripted\b', r'\bgame show', r'\bhousewives\b',
        r'\bbig brother\b', r'\bsurvivor\b', r'\bamazing race\b',
        r'\btop model\b', r'\bgot talent\b', r'\bmasked singer\b',
        r'\bproject runway\b', r'\bstorage wars\b', r'\bpawn stars\b',
        r'\bduck dynasty\b', r'\bjersey shore\b', r'\bteen mom\b',
        r'\bridiculousness\b', r'\bthe challenge\b', r'\bbachelor\b',
        r'\bbridezilla', r'\bsay yes to the dress\b', r'\bfamily feud\b',
        r'\bprice is right\b', r'\bdeal or no deal\b', r'\bmillionaire\b',
        r'\bpyramid\b', r'\bmatch game\b', r'\bwipeout\b',
        r'\bfear factor\b', r'\bninja warrior\b', r'\bgladiators\b',
        r'\bbiggest loser\b', r'\bsupernanny\b', r'\bdance moms\b',
        r'\bink master\b', r'\bbar rescue\b', r'\blove after lockup\b',
        r'\blove & hip hop\b',
    )),
]


def rule_label(name: str) -> Optional[str]:
    """Deterministic label for a self-describing channel name, or None
    when the name needs a judgement call."""
    raw = (name or '').strip()
    if not raw:
        return None
    ov = _OVERRIDES.get(_cp_normalize(raw))
    if ov:
        return ov
    for genre, patterns in _RULE_ORDER:
        for rx in patterns:
            if rx.search(raw):
                return genre
    return None


# ---------------------------------------------------------------------------
# Batched pass for the names the rules leave open
# ---------------------------------------------------------------------------

_PROMPT_HEAD = """You label linear TV channels with one channel type.

Pick exactly one label per channel from this list:

Anime - Japanese animation and anime-brand channels.
Classic TV - vintage library channels defined by their era (mid-century TV, westerns like Gunsmoke, decade-branded reruns, variety archives).
Comedy - channels whose hook is being funny, including classic sitcoms.
Crime - true crime, police and forensic shows, courtroom and judge shows, crime drama, detective mysteries.
Documentary - factual, science, nature-science, history, space, military, investigative documentary.
Entertainment - general scripted drama, sci-fi and fantasy series, soaps, celebrity and pop-culture channels, and general-interest channels that fit nothing narrower.
Espanol - Spanish-language channels of any subject.
Food and Home - cooking, baking, restaurants, home improvement, real estate, gardening, crafts, antiques.
Kids and Family - preschool, cartoons, tween and family programming, kid creator channels.
Lifestyle - travel, outdoors, hunting and fishing, cars and motors, pets and animals, health and wellness, faith and religion, shopping, ambient and relaxation, identity and community channels.
Movies - channels programmed primarily with feature films, including horror, western, action and romance movie channels.
Music - music video, radio simulcast, concert and music-culture channels.
News - news, business news, weather, and opinion news channels.
Reality - unscripted competition, docusoap, dating, talk and game shows.
Sports - live sport, sport highlights, sport talk, betting, motorsport, combat sport, esports.

Rules:
- A Spanish-language channel is Espanol even when it is also news, sport or movies. A Latin-music channel that is not itself in Spanish is Music.
- Judge a channel by what a viewer browsing for that type would expect to find.
- A channel named after a single show takes the show's type.
- Some channels carry the category their own platform files them under. Treat it as strong evidence, but it is the platform's shelf and not always our list: a channel filed under "FOOD + TRAVEL" is Food and Home if it is about cooking or homes and Lifestyle if it is about travel, and one filed under "Latin" is Espanol if it is Spanish-language and Music if it is a Latin-music channel in English.
- Use Other only when the name gives you nothing to work with.
- Reply with a JSON object only. Keys are the line numbers as strings, values are the label. No prose, no code fence.

Channels:
"""


def _client():
    try:
        import anthropic  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.warning("fast_channel_genres: anthropic SDK unavailable: %s", e)
        return None
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        logger.warning("fast_channel_genres: ANTHROPIC_API_KEY not set")
        return None
    try:
        return anthropic.Anthropic(api_key=key)
    except Exception as e:  # noqa: BLE001
        logger.warning("fast_channel_genres: client init failed: %s", e)
        return None


def _coerce(label: Any) -> str:
    return _BY_LOWER.get(str(label or '').strip().lower(), FALLBACK)


def _parse_reply(text: str, size: int) -> dict[int, str]:
    body = (text or '').strip()
    if body.startswith('```'):
        body = re.sub(r'^```[a-z]*\s*', '', body)
        body = re.sub(r'\s*```$', '', body).strip()
    start, end = body.find('{'), body.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(body[start:end + 1])
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, str] = {}
    for k, v in (data or {}).items():
        try:
            i = int(str(k).strip())
        except Exception:  # noqa: BLE001
            continue
        if 1 <= i <= size:
            out[i] = _coerce(v)
    return out


def _model_labels(names: list[str],
                  hints: Optional[dict[str, str]] = None,
                  monitor: Optional[SpendMonitor] = None) -> dict[str, str]:
    """Label `names` in one Message Batches submission. Returns
    display-name -> label for whatever came back; anything missing is
    left to the caller's fallback."""
    out: dict[str, str] = {}
    if not names:
        return out
    client = _client()
    if client is None:
        return out
    try:
        from anthropic.types.messages.batch_create_params import Request
        from anthropic.types.message_create_params import (
            MessageCreateParamsNonStreaming)
    except Exception as e:  # noqa: BLE001
        logger.warning("fast_channel_genres: batch types unavailable: %s", e)
        return out

    chunks = [names[i:i + _BATCH_CHUNK]
              for i in range(0, len(names), _BATCH_CHUNK)]
    by_cid: dict[str, list[str]] = {}
    requests: list[Any] = []
    for ci, chunk in enumerate(chunks):
        cid = f'genre_{ci:03d}'
        # Where the platform files a channel is evidence even when it
        # does not map onto our list on its own ("FOOD + TRAVEL",
        # "Latin", "TV & Movies"). Hand it over rather than throwing
        # it away and asking from the name alone.
        listing = '\n'.join(
            f'{i}. {n}' + (f'   [platform files this under: '
                            f'{(hints or {}).get(n, "")}]'
                            if (hints or {}).get(n) else '')
            for i, n in enumerate(chunk, 1))
        by_cid[cid] = chunk
        requests.append(Request(
            custom_id=cid,
            params=MessageCreateParamsNonStreaming(
                model=_MODEL,
                max_tokens=2048,
                temperature=0,
                metadata=_usage_tap.metadata_dict(),
                messages=[{'role': 'user',
                           'content': _PROMPT_HEAD + listing}],
            ),
        ))

    if monitor is not None:
        est = monitor.preflight_estimate(
            len(requests), tokens_in_per_msg=1400, tokens_out_per_msg=900,
            model=_MODEL, batch=True)
        logger.info("fast_channel_genres: %d names -> %d requests "
                    "(%s, batch), preflight ~$%.4f",
                    len(names), len(requests), _MODEL, est)

    try:
        batch = client.messages.batches.create(requests=requests)
    except Exception as e:  # noqa: BLE001
        logger.warning("fast_channel_genres: batch submit failed: %s", e)
        return out
    batch_id = batch.id
    logger.info("fast_channel_genres: submitted batch %s", batch_id)

    t0 = time.time()
    while True:
        if monitor is not None and monitor.tripped():
            try:
                client.messages.batches.cancel(batch_id)
            except Exception:  # noqa: BLE001
                pass
            logger.error("fast_channel_genres: spend cap tripped; "
                         "cancelled batch %s", batch_id)
            return out
        if (time.time() - t0) / 60.0 > _BATCH_MAX_MINUTES:
            try:
                client.messages.batches.cancel(batch_id)
            except Exception:  # noqa: BLE001
                pass
            logger.error("fast_channel_genres: batch %s exceeded the "
                         "%d minute wait cap", batch_id, _BATCH_MAX_MINUTES)
            return out
        try:
            batch = client.messages.batches.retrieve(batch_id)
        except Exception as e:  # noqa: BLE001
            logger.info("fast_channel_genres: retrieve %s: %s", batch_id, e)
            time.sleep(_BATCH_POLL_SECONDS)
            continue
        if getattr(batch, 'processing_status', '') in (
                'ended', 'canceled', 'expired', 'failed'):
            break
        time.sleep(_BATCH_POLL_SECONDS)

    if getattr(batch, 'processing_status', '') != 'ended':
        logger.error("fast_channel_genres: batch %s ended as %s",
                     batch_id, getattr(batch, 'processing_status', '?'))
        return out

    try:
        results_iter = client.messages.batches.results(batch_id)
    except Exception as e:  # noqa: BLE001
        logger.error("fast_channel_genres: results() failed: %s", e)
        return out

    for r in results_iter:
        cid = getattr(r, 'custom_id', None)
        result = getattr(r, 'result', None)
        if not cid or cid not in by_cid:
            continue
        if getattr(result, 'type', None) != 'succeeded':
            continue
        msg = getattr(result, 'message', None)
        if msg is None:
            continue
        usage = getattr(msg, 'usage', None)
        if monitor is not None:
            try:
                monitor.record_response(usage, model=_MODEL, batch=True)
            except Exception:  # noqa: BLE001
                pass
        _usage_tap.record_batch_result(_MODEL, usage)
        text = ''
        for blk in (getattr(msg, 'content', None) or []):
            if getattr(blk, 'type', '') == 'text':
                text += getattr(blk, 'text', '') or ''
        chunk = by_cid[cid]
        for idx, label in _parse_reply(text, len(chunk)).items():
            out[chunk[idx - 1]] = label
    logger.info("fast_channel_genres: batch returned %d of %d labels",
                len(out), len(names))
    return out


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

def _empty_artifact() -> dict[str, Any]:
    return {'version': VERSION, 'generated_at': '',
            'taxonomy': list(TAXONOMY), 'counts': {},
            'genres': {}, 'names': {}}


def load_artifact() -> dict[str, Any]:
    """Current artifact from S3, or an empty one. Never raises."""
    try:
        snap = _base.read_snapshot(SOURCE)
    except Exception:  # noqa: BLE001
        snap = None
    if not isinstance(snap, dict) or not isinstance(snap.get('genres'), dict):
        return _empty_artifact()
    art = _empty_artifact()
    art.update({k: v for k, v in snap.items() if k != 'fetched_at'})
    art['version'] = VERSION
    art['taxonomy'] = list(TAXONOMY)
    art['genres'] = {str(k): _coerce(v)
                     for k, v in (snap.get('genres') or {}).items()}
    art['names'] = {str(k): str(v)
                    for k, v in (snap.get('names') or {}).items()}
    return art


def _all_lineup_blocks() -> list[dict]:
    """Every per-platform lineup block in play: the four workbook
    platforms out of the shared artifact, plus one block per platform
    that publishes its own guide."""
    blocks: list[dict] = []
    try:
        snap = _base.read_snapshot(LINEUPS_SOURCE)
    except Exception:  # noqa: BLE001
        snap = None
    blocks.extend((b or {}) for b in
                   ((snap or {}).get('sources') or {}).values())
    for source in _API_LINEUP_SOURCES:
        try:
            s = _base.read_snapshot(source)
        except Exception:  # noqa: BLE001
            s = None
        if (s or {}).get('channels'):
            blocks.append({'channels': s['channels']})
    return blocks


def lineup_channel_names() -> list[str]:
    """Distinct display names across every platform in the current
    lineup, first spelling seen wins."""
    return _names_from_blocks(_all_lineup_blocks())


def lineup_source_genres() -> dict[str, str]:
    """Normalized channel name -> the platform's own category label,
    for the platforms that publish one. First label seen wins, so a
    channel carried on two platforms keeps one classification input
    rather than flapping between them."""
    out: dict[str, str] = {}
    for block in _all_lineup_blocks():
        for ch in (block or {}).get('channels') or []:
            norm = _cp_normalize(str((ch or {}).get('name') or ''))
            label = str((ch or {}).get('source_genre') or '').strip()
            if norm and label and norm not in out:
                out[norm] = label
    return out


def names_from_lineups(lineups: dict) -> list[str]:
    return _names_from_blocks(
        [(b or {}) for b in ((lineups or {}).get('sources') or {}).values()])


def _names_from_blocks(blocks: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    for block in blocks:
        for ch in (block or {}).get('channels') or []:
            name = str((ch or {}).get('name') or '').strip()
            norm = _cp_normalize(name)
            if norm and norm not in seen:
                seen[norm] = name
    return [seen[k] for k in sorted(seen)]


def classify(names: Iterable[str], *,
             existing: Optional[dict[str, Any]] = None,
             allow_model: bool = True,
             source_genres: Optional[dict[str, str]] = None,
             monitor: Optional[SpendMonitor] = None) -> dict[str, Any]:
    """Return an artifact covering `existing` plus every name in
    `names`. Names already carrying a label are left untouched.

    `source_genres` maps a normalized name to the platform's OWN
    category label. It is consulted after the name rules and before
    the model: the rules are tuned and tested and stay authoritative,
    but a platform that has already told us a channel is Sports
    should not cost a model call to find that out. Labels that do not
    map cleanly onto our 15 still reach the model, which sees them as
    context."""
    art = dict(existing or _empty_artifact())
    genres: dict[str, str] = dict(art.get('genres') or {})
    display: dict[str, str] = dict(art.get('names') or {})
    hints = dict(source_genres or {})

    pending: list[str] = []
    n_from_source = 0
    for name in names:
        raw = str(name or '').strip()
        norm = _cp_normalize(raw)
        if not norm:
            continue
        display.setdefault(norm, raw)
        ov = _OVERRIDES.get(norm)
        if ov:
            genres[norm] = ov
            continue
        if norm in genres:
            continue
        hit = rule_label(raw)
        if hit:
            genres[norm] = hit
            continue
        from_source = source_genre_label(hints.get(norm, ''))
        if from_source:
            genres[norm] = from_source
            n_from_source += 1
            continue
        pending.append(raw)

    n_rules = len(genres) - len(art.get('genres') or {}) - n_from_source
    logger.info("fast_channel_genres: %d resolved by name rules, %d by the "
                "platform's own category, %d need a closer read",
                n_rules, n_from_source, len(pending))

    if pending and allow_model:
        prompt_hints = {raw: hints.get(_cp_normalize(raw), '')
                         for raw in pending}
        for raw, label in _model_labels(pending, hints=prompt_hints,
                                          monitor=monitor).items():
            genres[_cp_normalize(raw)] = label
    for raw in pending:
        genres.setdefault(_cp_normalize(raw), FALLBACK)

    art['version'] = VERSION
    art['taxonomy'] = list(TAXONOMY)
    art['genres'] = genres
    art['names'] = display
    art['counts'] = dict(sorted(Counter(genres.values()).items(),
                                key=lambda kv: (-kv[1], kv[0])))
    art['generated_at'] = datetime.now(timezone.utc).isoformat()
    return art


def refresh(names: Optional[Iterable[str]] = None, *,
            rebuild: bool = False, dry_run: bool = False,
            allow_model: bool = True) -> dict[str, str]:
    """Bring the artifact up to date against `names` (defaulting to the
    current lineup) and write it back. Returns the full
    normalized-name -> label mapping, empty on any failure."""
    try:
        pool = list(names) if names is not None else lineup_channel_names()
        if not pool:
            logger.warning("fast_channel_genres: no channel names to label")
            return {}
        existing = _empty_artifact() if rebuild else load_artifact()
        before = len(existing.get('genres') or {})
        monitor = SpendMonitor(cap_usd=_SPEND_CAP_USD, prefix='fast_genres')
        art = classify(pool, existing=existing, allow_model=allow_model,
                       source_genres=lineup_source_genres(),
                       monitor=monitor)
        added = len(art.get('genres') or {}) - before
        logger.info("fast_channel_genres: %d labels total (+%d this run), "
                    "spend $%.4f", len(art.get('genres') or {}), added,
                    monitor.total())
        if dry_run:
            logger.info("fast_channel_genres: dry run, nothing written")
        else:
            _base.write_snapshot(SOURCE, art)
        return dict(art.get('genres') or {})
    except Exception as e:  # noqa: BLE001
        logger.warning("fast_channel_genres: refresh failed (%s); "
                       "channel types unchanged", e)
        return {}


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true',
                    help='label but do not write to S3')
    ap.add_argument('--rebuild', action='store_true',
                    help='ignore existing labels and redo every channel')
    ap.add_argument('--rules-only', action='store_true',
                    help='skip the batched pass; unresolved names read Other')
    args = ap.parse_args(argv)

    mapping = refresh(rebuild=args.rebuild, dry_run=args.dry_run,
                      allow_model=not args.rules_only)
    if not mapping:
        return 1
    counts = Counter(mapping.values())
    width = max(len(g) for g in counts)
    for genre, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f'{genre:<{width}}  {n}')
    print(f'{"TOTAL":<{width}}  {len(mapping)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
