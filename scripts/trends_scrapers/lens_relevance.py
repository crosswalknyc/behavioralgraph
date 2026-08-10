"""
Persona-lens relevance scorer.

For every visible item on the Trends IQ dashboard (podcasts / songs /
streaming titles / books / films / social posts / headlines / trending
people / trending searches), ask Claude Sonnet to score how relevant
that item is to each configured audience lens on a 0-100 scale.

The dashboard user picks a lens from a dropdown and the frontend
instantly filters every card to just the rows the persona would
actually be interested in.

Two lenses ship today:

  - ms_now_reader : the MS NOW (formerly MSNBC) reader. College-
                    educated, urban/suburban, Democratic-leaning
                    (~85% D), skews 55+. Heavy on politics, foreign
                    policy, cable-news personalities, Trump-era
                    accountability journalism, media criticism, and
                    progressive-adjacent podcasts.
  - millennials   : ages 27-42 as of 2026 (born 1981-1996). Home-
                    ownership + student-loan anxieties, nostalgic
                    reboots (Cobra Kai, Barbie), heavy podcast
                    consumption, migrating from Instagram to TikTok,
                    DTC brands, index-fund investing, gaming
                    (Nintendo / Zelda / Fortnite crossover), K-pop /
                    Marvel / Star Wars.

Output shape (kind='meta'):

    {
      "source":     "lens_scores",
      "kind":       "meta",
      "fetched_at": "...",
      "generated_at": "...",
      "lenses": [
        {"id": "ms_now_reader",
         "label": "MS NOW Reader",
         "emoji": "\U0001F4FA",
         "description": "..."},
        {"id": "millennials",
         "label": "Millennials (Ages 27-42)",
         "emoji": "\u2615",
         "description": "..."}
      ],
      "items": {
        "podcast:pod save america": {
          "kind":  "podcast",
          "title": "Pod Save America",
          "scores": {"ms_now_reader": 92, "millennials": 68}
        },
        ...
      },
      "count": 340
    }

Standalone:

    python3 -m scripts.trends_scrapers.lens_relevance
    python3 -m scripts.trends_scrapers.lens_relevance --only podcast
    python3 -m scripts.trends_scrapers.lens_relevance --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/'


_CLAUDE_MODEL = (os.environ.get('LENS_RELEVANCE_MODEL')
                  or os.environ.get('WEBSEARCH_MODEL')
                  or 'claude-sonnet-4-5')
_CONCURRENCY  = int(os.environ.get('LENS_RELEVANCE_CONCURRENCY') or '4')
_BATCH_SIZE   = int(os.environ.get('LENS_RELEVANCE_BATCH_SIZE')  or '25')
_TIMEOUT_S    = int(os.environ.get('LENS_RELEVANCE_TIMEOUT_S')   or '120')


# ---------------------------------------------------------------------------
# Text normalization - mirrors stream_estimates + trends_iq so a
# `podcast:crime junkie` key here matches the same key the dashboard
# builds when it renders a Crime Junkie row.
# ---------------------------------------------------------------------------
_STOPWORDS = {'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'to',
               'for', 'with', 'at', 'by', 'from', 'as', 'is', 'are',
               'was', 'were', 'be', 'been', 'being', 'this', 'that',
               'these', 'those'}


def _norm(text: str) -> str:
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    return ' '.join(t for t in s.split() if t and t not in _STOPWORDS)


def _key(kind: str, title: str, artist: str = '') -> str:
    """Lookup key: `<kind>:<normalized title[+artist]>`. Must match the
    frontend's `_tiqLensKey()` byte-for-byte so filtering works."""
    if kind in ('song', 'book'):
        return f'{kind}:{_norm(f"{title} {artist}")}'
    return f'{kind}:{_norm(title)}'


# ---------------------------------------------------------------------------
# Lens definitions - the persona descriptions Claude reasons against.
# Each description must be rich enough that Claude can decide "would this
# specific persona click on / consume / engage with this specific item?"
# for any of the ~500 items the dashboard surfaces.
# ---------------------------------------------------------------------------
_LENSES: list[dict[str, Any]] = [
    {
        'id':          'ms_now_reader',
        'label':       'MS NOW Reader',
        'emoji':       '\U0001F4FA',                       # 📺
        'description': ('Politics-forward center-left audience; core '
                         'MS NOW (formerly MSNBC) viewer.'),
        'persona': (
            "MS NOW (formerly MSNBC) reader / viewer.  Demographic: "
            "US adults skewing 55+, college-educated, urban and inner-"
            "suburban, ~85% Democratic-leaning, ~65% female, higher-"
            "income (median ~$85K HHI), heavy news consumers.\n"
            "\n"
            "CORE INTERESTS (score HIGH, 80-100):\n"
            "  - Trump-administration accountability journalism, "
            "Justice Department, FBI, Special Counsel coverage.\n"
            "  - Congressional hearings (Jan 6, impeachment, etc.), "
            "Democratic strategy, DNC internals, Kamala Harris, Joe "
            "Biden, AOC, Bernie Sanders, Elizabeth Warren, Chuck "
            "Schumer, Hakeem Jeffries.\n"
            "  - Rachel Maddow, Nicolle Wallace, Chris Hayes, Joy "
            "Reid, Lawrence O'Donnell, Alex Wagner, Stephanie Ruhle, "
            "Ari Melber, Katy Tur, Jen Psaki, Symone Sanders-Townsend, "
            "Andrea Mitchell, Willie Geist, Mika Brzezinski, Joe "
            "Scarborough.\n"
            "  - Pod Save America, The Bulwark, The Daily (NYT), Up "
            "First (NPR), The Rachel Maddow Show podcast, Deadline "
            "White House, Prosecuting Donald Trump, Amicus (Slate), "
            "The Ezra Klein Show, The New Yorker Radio Hour.\n"
            "  - NYT, Washington Post, The New Yorker, The Atlantic, "
            "ProPublica, PBS NewsHour, NPR, Reuters, AP.\n"
            "  - Foreign policy (Ukraine, Israel/Gaza, China), "
            "climate policy, voting rights, Supreme Court coverage, "
            "election integrity, reproductive rights.\n"
            "  - Books by / about political figures, prestige "
            "nonfiction (history / biography / policy), literary "
            "fiction, book-club darlings.\n"
            "  - Prestige TV drama (Succession, The Diplomat, The "
            "Morning Show, Slow Horses), historical documentaries, "
            "Ken Burns, PBS Frontline.\n"
            "\n"
            "MILDLY RELEVANT (score MEDIUM, 40-65):\n"
            "  - Business coverage IF politically-adjacent (Musk, "
            "Zuckerberg, big-tech antitrust, banking-crisis stories, "
            "OPEC).\n"
            "  - Health-policy stories (Medicare, ACA, drug pricing) "
            "but not pure lifestyle wellness.\n"
            "  - Entertainment IF culturally-political (Barbie, "
            "Oppenheimer, Handmaid's Tale, activist actors).\n"
            "\n"
            "NOT INTERESTED (score LOW, 5-25):\n"
            "  - Fox News talent, Newsmax, OAN, MAGA-aligned podcasts "
            "(Rogan, Ben Shapiro, Charlie Kirk, Tucker Carlson, Tim "
            "Pool, Bannon).\n"
            "  - Sports (NFL/NBA/MLB/soccer game coverage, fantasy "
            "sports, betting).\n"
            "  - Gaming, esports, streamers (Twitch, gaming YouTube).\n"
            "  - K-pop, hip-hop rankings, mainstream pop charts, "
            "TikTok viral songs, DJ mixes.\n"
            "  - Reality TV, dating shows, teen dramas, YA romance, "
            "Wattpad-adjacent romantasy.\n"
            "  - Crypto, NFTs, day-trading, meme-stock coverage.\n"
            "  - Fashion / beauty / influencer content, most "
            "lifestyle magazines.\n"
            "  - Fast food, QSR, grocery-store trend coverage.\n"
            "  - Horror, action franchises, superhero tentpoles unless "
            "specifically culturally-political."
        ),
    },
    {
        'id':          'millennials',
        'label':       'Millennials (Ages 27-42)',
        'emoji':       '\u2615',                            # ☕
        'description': ('Ages 27-42 as of 2026 (born 1981-1996). Home / '
                         'career / nostalgia sweet spot.'),
        'persona': (
            "Millennial audience, ages 27-42 as of 2026 (born 1981-"
            "1996).  Demographic: US adults, roughly 50/50 gender, "
            "60% suburban / 30% urban / 10% rural, ~70% college-"
            "attended, median HHI ~$75K, ~55% married, ~40% have "
            "kids under 12.\n"
            "\n"
            "CORE INTERESTS (score HIGH, 80-100):\n"
            "  - Nostalgic reboots and franchises: Cobra Kai, Star "
            "Wars, Marvel (esp. Loki-era), Barbie, Fallout, Legend of "
            "Zelda, Pokemon, Harry Potter, LOTR / Rings of Power, "
            "Stranger Things, Ted Lasso, The Bear, Only Murders in "
            "the Building, Yellowjackets, Succession, Severance, "
            "Beef.\n"
            "  - Pop / hip-hop / country crossover: Taylor Swift, "
            "Olivia Rodrigo, Sabrina Carpenter, Chappell Roan, Beyonce, "
            "Bad Bunny, Zach Bryan, Morgan Wallen, Post Malone, Drake, "
            "Kendrick Lamar, SZA.\n"
            "  - Podcasts (high-consumption cohort): Smartless, "
            "Armchair Expert, Call Her Daddy, SmartLess, My Favorite "
            "Murder, Crime Junkie, Serial, The Daily, Huberman Lab, "
            "The Tim Ferriss Show, How I Built This, Reply All, "
            "Radiolab, This American Life.\n"
            "  - Books: BookTok darlings (Colleen Hoover, Rebecca "
            "Yarros, Sarah J. Maas, Emily Henry, Taylor Jenkins Reid), "
            "prestige nonfiction (Atomic Habits, Educated, Bad Blood, "
            "Braiding Sweetgrass), self-help + personal finance "
            "(Ramit Sethi, Morgan Housel).\n"
            "  - Personal finance / adulting content: student-loan "
            "coverage, first-home-buying, 401k / index-fund investing, "
            "side-hustle economy, DTC brand affinity.\n"
            "  - Gaming (broader than Gen Z): Nintendo Switch, "
            "Fortnite, Zelda, Mario, Elden Ring, Baldur's Gate 3, "
            "The Sims, Stardew Valley, Animal Crossing, retro / "
            "arcade nostalgia.\n"
            "  - Parenting content for kids under 12 (millennial "
            "parents are the largest active-parent cohort), Bluey, "
            "Cocomelon backlash, screen-time debate.\n"
            "  - Streaming platforms (heavy users): Netflix, Disney+, "
            "Hulu, HBO Max, Prime Video, Peacock, Apple TV+.\n"
            "  - K-pop (BTS, BLACKPINK, NewJeans, Stray Kids), K-"
            "drama, anime crossovers.\n"
            "  - Tech coverage that's practical: iPhone, MacBook, "
            "Google Pixel, Apple Watch, AirPods, home-tech reviews, "
            "AI-for-productivity stories.\n"
            "  - Reddit-native trends (subreddit-driven virality), "
            "TikTok algorithm content, Instagram Reels.\n"
            "\n"
            "MILDLY RELEVANT (score MEDIUM, 40-65):\n"
            "  - Politics if scandal / celebrity / meme-driven, "
            "otherwise low interest.\n"
            "  - Traditional business coverage (mergers, earnings, "
            "market moves).\n"
            "  - Hard news / world affairs (yes, but less than "
            "boomer-plus cohort).\n"
            "\n"
            "NOT INTERESTED (score LOW, 5-25):\n"
            "  - Cable-news personalities specifically (Rachel Maddow, "
            "Sean Hannity, Tucker Carlson - millennials don't watch "
            "cable news).\n"
            "  - Traditional talk radio, boomer-podcast circuit.\n"
            "  - Vintage brands their parents love (Chevrolet, Buick, "
            "Golden Corral).\n"
            "  - AARP / retirement / senior-focused content.\n"
            "  - Gen Z-only slang or aesthetic (some overlap but not "
            "core).\n"
            "  - Country music that isn't Zach Bryan / Morgan Wallen "
            "/ Kacey Musgraves crossover-tier.\n"
            "  - Cricket, rugby, most soccer (except MLS + WC), "
            "boxing, UFC (male-skew millennial only)."
        ),
    },
]


# ---------------------------------------------------------------------------
# Collect items from every dashboard snapshot on S3
# ---------------------------------------------------------------------------
_s3 = boto3.client('s3')


def _read(source: str) -> Optional[dict]:
    key = f'{_S3_LATEST}{source}.json'
    try:
        body = _s3.get_object(Bucket=_S3_BUCKET, Key=key)['Body'].read()
    except Exception as e:
        logger.info("lens_relevance: no snapshot %s (%s)", source, e)
        return None
    try:
        return json.loads(body)
    except Exception as e:
        logger.warning("lens_relevance: bad JSON %s (%s)", source, e)
        return None


def _collect_all_items() -> list[dict]:
    """Union of every renderable item across every latest snapshot,
    keyed by (kind, normalized title[+artist]).  Duplicates across
    sources fold into a single scoring row so we don't waste tokens
    reasoning about the same podcast twice."""
    per: dict[str, dict] = {}

    def _add(kind: str, title: str, *, artist: str = '',
              extra: str = '', source_label: str = '') -> None:
        title = (title or '').strip()
        if not title:
            return
        k = _key(kind, title, artist)
        if k not in per:
            per[k] = {
                'key':          k,
                'kind':         kind,
                'title':        title,
                'artist':       (artist or '').strip(),
                'context':      extra.strip(),
                'seen_on':      [],
            }
        if source_label and source_label not in per[k]['seen_on']:
            per[k]['seen_on'].append(source_label)

    # Podcasts
    pod = _read('podcast_charts') or {}
    for slug, panel in (pod.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('podcast', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Songs
    music = _read('music_charts') or {}
    for slug, panel in (music.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('song', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Streaming (film + tv)
    streaming = _read('streaming_trending') or {}
    for slug, panel in (streaming or {}).items():
        for it in (panel.get('items') or []):
            kind = (it.get('kind') or 'title').lower()
            if kind not in ('film', 'tv', 'title'):
                kind = 'title'
            _add(kind, it.get('title') or '',
                  source_label=panel.get('label') or slug)

    # Films (ticketing)
    films = _read('film_ticketing') or {}
    for slug, panel in (films.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('film', it.get('title') or '',
                  source_label=panel.get('label') or slug)

    # Books (Amazon / Apple / Audible)
    books = _read('book_charts') or {}
    for slug, panel in (books.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('book', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Libby (ebook / audiobook / magazine)
    libby = _read('libby_trends') or {}
    for slug, panel in (libby.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('book', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Headlines - top articles, by-source articles, business, and
    # philanthropy all live in the same `headline:` keyspace so a
    # story appearing on multiple lists only gets scored once.
    for src in ('trending_headlines', 'philanthropy_news', 'business_news'):
        snap = _read(src) or {}
        # `trending_headlines` stores under 'articles'; the two topic
        # feeds store under 'national'. Support both.
        rows = (snap.get('articles') or snap.get('national')
                or snap.get('by_source') or [])
        # `by_source` is a dict of {source: [items]}; flatten if so.
        if isinstance(rows, dict):
            flat = []
            for lst in rows.values():
                flat.extend(lst or [])
            rows = flat
        for it in rows[:120]:
            _add('headline', it.get('title') or '',
                  extra=it.get('source_label') or it.get('source') or '',
                  source_label=snap.get('label') or src)

    # Trending searches - overall list + every per-category feed
    # (sports / entertainment / gaming / tech / weather / politics /
    # finance / retail / etc). These populate the default landing
    # "Trending" tab so the scorer MUST cover them or the persona
    # filter has no effect on the tab most users see first.
    searches = _read('trending_searches') or {}
    for it in (searches.get('all') or searches.get('national') or [])[:80]:
        _add('search', it.get('term') or it.get('title') or '',
              extra=it.get('why') or it.get('context') or '',
              source_label='Trending searches')
    for cat, items in (searches.get('by_category') or {}).items():
        for it in (items or [])[:30]:
            _add('search', it.get('term') or it.get('title') or '',
                  extra=str(cat or '') + ': ' + (it.get('why') or ''),
                  source_label='Trending searches / ' + str(cat or ''))

    # Movers (breakout / rising / cooling / sustained). Same
    # keyspace as searches so if a term appears in both it only
    # gets scored once.
    movers = _read('movers') or {}
    for bucket in ('breakout', 'rising', 'falling', 'sustained',
                    'climbing', 'cooling'):
        for it in (movers.get(bucket) or [])[:40]:
            _add('search', it.get('term') or it.get('title') or '',
                  extra='mover: ' + bucket,
                  source_label='Movers / ' + bucket)

    # Trending people (composite ranker: news + wikipedia + social).
    # This is what feeds renderTIQPeople(cards.trending_people)
    # in the frontend - separate snapshot from wikipedia_trending.
    people = _read('trending_people') or {}
    for it in (people.get('national') or people.get('people') or [])[:60]:
        name = it.get('name') or it.get('title') or ''
        _add('person', name,
              extra=(it.get('description') or it.get('why') or '')[:180],
              source_label='Trending people')

    # Wikipedia trending (people + national). Descriptions are
    # helpful context for the scorer ("American senator" vs
    # "Nigerian afrobeats singer" swings both lenses hard). Same
    # `person:` keyspace as trending_people so dedupe is automatic.
    wiki = _read('wikipedia_trending') or {}
    for it in (wiki.get('people') or wiki.get('national') or [])[:60]:
        _add('person', it.get('name') or it.get('title') or '',
              extra=(it.get('description') or '')[:180],
              source_label='Wikipedia trending')

    # Social (Reddit / YouTube / TikTok posts).
    for src, key in (('reddit', 'reddit'),
                     ('youtube', 'youtube'),
                     ('tiktok', 'tiktok')):
        snap = _read(src) or {}
        for it in (snap.get('national') or [])[:30]:
            _add('social', it.get('title') or it.get('topic') or '',
                  extra=snap.get('label') or key,
                  source_label=snap.get('label') or key)

    return list(per.values())


# ---------------------------------------------------------------------------
# Claude batch prompt
# ---------------------------------------------------------------------------
def _batch_prompt(lens: dict, batch: list[dict]) -> str:
    lines = [
        "You are an audience-strategist scoring items for a specific "
        "persona.  Return an integer 0-100 for each item measuring "
        "how likely this exact persona would be interested in "
        "clicking, streaming, reading, watching, or otherwise engaging "
        "with the item this week.  Bias LOW - most items should score "
        "in the 30-55 range; reserve 80+ for items that are core-"
        "audience content for this persona.  Return 5-25 for items "
        "the persona would actively avoid.",
        "",
        "PERSONA: " + lens['label'],
        lens['persona'],
        "",
        "OUTPUT FORMAT (STRICT):",
        "  Return a single JSON array with one object per item, IN "
        "THE SAME ORDER as the input.  Each object has:",
        '    { "id": <int>, "score": <int 0-100>, "why": "<10-15 word rationale>" }',
        "  Return ONLY the JSON array, no prose before or after.",
        "",
        "ITEMS:",
    ]
    for i, it in enumerate(batch):
        title = it['title'][:150]
        row   = f'  [{i}] kind={it["kind"]} title="{title}"'
        if it.get('artist'):
            row += f' artist="{it["artist"][:80]}"'
        if it.get('context'):
            row += f' context="{it["context"][:180]}"'
        if it.get('seen_on'):
            row += f' seen_on={it["seen_on"][:3]}'
        lines.append(row)
    return '\n'.join(lines)


_JSON_ARRAY_RE = re.compile(r'\[[\s\S]*\]')


def _parse_batch(text: str, batch_len: int) -> list[Optional[dict]]:
    """Parse Claude's JSON array back into a list aligned with the
    input batch.  Returns [None] for any slot Claude skipped or
    returned malformed."""
    if not text:
        return [None] * batch_len
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return [None] * batch_len
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return [None] * batch_len
    if not isinstance(arr, list):
        return [None] * batch_len
    by_id: dict[int, dict] = {}
    for row in arr:
        if not isinstance(row, dict):
            continue
        try:
            rid = int(row.get('id'))
        except Exception:
            continue
        try:
            score = int(row.get('score') or 0)
        except Exception:
            score = 0
        score = max(0, min(100, score))
        why = (row.get('why') or '').strip()[:200]
        by_id[rid] = {'score': score, 'why': why}
    out: list[Optional[dict]] = []
    for i in range(batch_len):
        out.append(by_id.get(i))
    return out


def _score_batch(client, lens: dict, batch: list[dict]) -> list[Optional[dict]]:
    prompt = _batch_prompt(lens, batch)
    try:
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{'role': 'user', 'content': prompt}],
            timeout=_TIMEOUT_S,
        )
    except Exception as e:
        logger.info("lens_relevance %s batch (n=%d): %s",
                     lens['id'], len(batch), e)
        return [None] * len(batch)
    text = ''.join(getattr(b, 'text', '') for b in (resp.content or []))
    return _parse_batch(text, len(batch))


def _score_lens(client, lens: dict, items: list[dict]) -> dict[str, dict]:
    """Score every item for a single lens.  Batches of `_BATCH_SIZE`
    items per Claude call.  Returns {key: {'score': int, 'why': str}}."""
    if not items:
        return {}
    out: dict[str, dict] = {}
    batches: list[list[dict]] = [
        items[i:i + _BATCH_SIZE]
        for i in range(0, len(items), _BATCH_SIZE)
    ]
    logger.info("lens_relevance %s: %d items -> %d batches (%s, concurrency=%d)",
                 lens['id'], len(items), len(batches),
                 _CLAUDE_MODEL, _CONCURRENCY)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futs = {ex.submit(_score_batch, client, lens, b): b for b in batches}
        for bi, fut in enumerate(concurrent.futures.as_completed(futs), start=1):
            batch = futs[fut]
            try:
                results = fut.result(timeout=_TIMEOUT_S + 30)
            except Exception as e:
                logger.info("lens_relevance %s batch %d failed: %s",
                             lens['id'], bi, e)
                continue
            covered = 0
            for it, res in zip(batch, results):
                if res:
                    out[it['key']] = res
                    covered += 1
            logger.info("  %s [batch %2d/%d] -> %d/%d scored",
                         lens['id'], bi, len(batches),
                         covered, len(batch))
    return out


# ---------------------------------------------------------------------------
# Fetch entry point
# ---------------------------------------------------------------------------
def fetch(only_lens: Optional[str] = None, dry_run: bool = False) -> dict[str, Any]:
    items = _collect_all_items()
    logger.info("lens_relevance: collected %d unique items across all snapshots",
                 len(items))
    lens_meta = [
        {'id': l['id'], 'label': l['label'],
         'emoji': l['emoji'], 'description': l['description']}
        for l in _LENSES
    ]
    if dry_run:
        return {'items': {it['key']: {'kind': it['kind'],
                                         'title': it['title']}
                            for it in items},
                'lenses': lens_meta,
                'count':  len(items),
                'dry_run': True}

    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        return {'items': {}, 'lenses': lens_meta, 'count': 0,
                 'error': 'ANTHROPIC_API_KEY not set'}
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        return {'items': {}, 'lenses': lens_meta, 'count': 0,
                 'error': f'anthropic SDK missing: {e}'}
    client = anthropic.Anthropic(api_key=api_key)

    # Combine per-lens results into a single item-keyed dict:
    #   items[key] = {kind, title, scores: {lens_id: {score, why}}}
    per_lens: dict[str, dict[str, dict]] = {}
    for lens in _LENSES:
        if only_lens and lens['id'] != only_lens:
            continue
        per_lens[lens['id']] = _score_lens(client, lens, items)

    combined: dict[str, dict] = {}
    for it in items:
        row: dict[str, Any] = {
            'kind':   it['kind'],
            'title':  it['title'],
            'scores': {},
        }
        if it.get('artist'):
            row['artist'] = it['artist']
        for lens_id, lens_out in per_lens.items():
            hit = lens_out.get(it['key'])
            if hit:
                row['scores'][lens_id] = hit['score']
                # `why` intentionally NOT stamped into the frontend
                # payload - it's Claude's rationale for internal audit
                # only.  Comment back in if we want tooltip context.
                # row.setdefault('_why', {})[lens_id] = hit['why']
        if row['scores']:
            combined[it['key']] = row

    return {
        'items':        combined,
        'lenses':       lens_meta,
        'count':        len(combined),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO,
                          format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='score only this lens id')
    ap.add_argument('--dry-run', action='store_true',
                     help='collect items but skip Claude calls')
    args = ap.parse_args()

    from ._base import run_scraper
    result = run_scraper(
        'lens_scores',
        'Persona lens relevance',
        'meta',
        lambda: fetch(only_lens=args.only, dry_run=args.dry_run),
    )
    print(f"lens_relevance: count={result.get('count')} "
           f"lenses={[l['id'] for l in result.get('lenses') or []]} "
           f"error={result.get('error')}",
           file=sys.stderr)
