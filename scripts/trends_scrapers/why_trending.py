"""
"Why is this trending?" one-line context generator.

Runs once per day in the scraper cron. Reads the top trending items
from the other snapshots we've already collected (Wikipedia, GDELT
people, Google Trends, headlines), packs them with whatever context
we have, and asks Claude to produce a single-sentence explanation
for each in a batch.

Output snapshot shape (kind='meta'):

    {
      "source":       "why_trending",
      "kind":         "meta",
      "fetched_at":   "2026-07-09T...",
      "generated_at": "...",
      "items":        {                          # keyed by normalized name
        "elon musk":         "Announced new Grok model this morning.",
        "andrey santos":     "Chelsea midfielder scored winner in Club World Cup.",
        ...
      },
      "count":        <int>,
    }

`trends_iq._read_snapshot('why_trending')` is what the app calls
(unchanged pattern). `compute_view` stamps `row['why'] = items[key]`
on matching person / wikipedia / search / mover rows.

Design decisions
----------------
- Runs DAILY in cron, NOT at request time. Dashboard latency stays
  flat and Claude cost is bounded (~$0.05/day at 30 items via haiku).
- Uses a single batch prompt so we spend one round-trip per day.
- Reads other scrapers' snapshots at their `latest/` keys. If a
  source hasn't run yet, we skip - no hard dependency ordering.
- If ANTHROPIC_API_KEY is unset, writes an empty snapshot with an
  `error` field so the app renders normally.

Standalone:

    python3 -m scripts.trends_scrapers.why_trending
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


# Mirror trends_iq.py so we're guaranteed to read the same S3 keys the
# app reads. If either file moves, they move together.
_S3_BUCKET      = 'dashboard-inputs'
_S3_PREFIX      = 'trends_iq_snapshots/latest/'
# Some sources (gdelt, gdelt-people) don't have a latest/ mirror -
# they're only written to the dated history path. Look back this many
# days for those.
_HISTORY_LOOKBACK_DAYS = 4

# Cap how many items we ask Claude to explain per day. We size to cover
# every row visible on each card:
#   Wikipedia   -> top 30 (the 🌐 Trending People card shows 30 rows,
#                  and this is the surface where bio-fallback captions
#                  were showing up as fake "why" text, so full coverage
#                  matters most here).
#   GDELT people -> top 8   (fusion People card shows dozens but the
#                            top 8 carry the vast majority of interest).
#   Search      -> top 8   (Google Trends card visible items).
_MAX_WIKI_ITEMS   = 30
_MAX_PEOPLE_ITEMS = 8
_MAX_SEARCH_ITEMS = 8
_TOTAL_ITEM_CAP   = 60

# Single-sentence explanations don't need Opus. Match the model naming
# convention the rest of the workspace uses (claude_client.py defaults
# to claude-sonnet-4-5); haiku-4-5 is the cheap fast tier from the
# same family. Overridable via WHY_TRENDING_MODEL env var.
_CLAUDE_MODEL = os.environ.get('WHY_TRENDING_MODEL') or 'claude-haiku-4-5'
# Enough to fit 60 items of context-rich prompt (~200 tokens each) +
# 60 responses (~30 tokens each). Empirical cap on haiku is 8k output.
_MAX_TOKENS   = 4000


_STOPWORDS = {'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at'}


def _cp_normalize(text: str) -> str:
    """Case-fold, strip punctuation, drop stopwords, collapse spaces.
    MUST match the normalization used in `trends_iq._cp_normalize` so
    the app can look up entries by the same key."""
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _STOPWORDS]
    return ' '.join(tokens)


def _s3():
    return boto3.client('s3')


def _read_snapshot(source: str) -> Optional[dict]:
    """Return the S3 snapshot for `source` or None on any failure.

    First tries the `latest/` prefix (fresh daily-cron output). If that
    misses, walks backwards through the dated history path up to
    _HISTORY_LOOKBACK_DAYS - covers sources like `gdelt-people` and
    `gdelt` that are only written to the dated path.
    """
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=f'{_S3_PREFIX}{source}.json')
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        pass
    for offset in range(0, _HISTORY_LOOKBACK_DAYS):
        d = date.today() - timedelta(days=offset)
        key = f'trends_iq_snapshots/{d.isoformat()}/{source}.json'
        try:
            obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
            return json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            continue
    logger.info("why_trending: skip %s (no snapshot in latest/ or last %d days)",
                 source, _HISTORY_LOOKBACK_DAYS)
    return None


def _build_name_headline_index(
    gdelt_people_snap: Optional[dict],
) -> dict[str, list[str]]:
    """Build the AUTHORITATIVE lookup: normalized-name -> already-
    attributed headlines that mention that person.

    Populated from `gdelt-people.national[i].context` (the scraper
    pre-attributes headlines to each person via NER). This is the
    strongest signal; the substring fallback in
    `_lookup_headlines_for` covers everyone else via a wider pool.
    """
    idx: dict[str, list[str]] = {}
    for p in (gdelt_people_snap or {}).get('national', []):
        name = p.get('name') or ''
        key = _cp_normalize(name)
        if not key:
            continue
        headlines = [h for h in (p.get('context') or []) if h and isinstance(h, str)]
        if headlines:
            idx.setdefault(key, []).extend(headlines[:5])
    return idx


def _flatten_headline_pool(*snaps: Optional[dict]) -> list[str]:
    """Flatten every headline-shaped title across the provided
    snapshots into a single list of unique strings. Used as the pool
    for the substring-match cross-reference in `_lookup_headlines_for`.

    Sources we mine (in order of trust):
    - `gdelt.national[i].title`             top world / US headlines
    - `reddit.national[i].title`            top Reddit posts
    - `philanthropy_news.national[i].title` philanthropy RSS
    - `youtube.national[i].title`           top YouTube trending videos
    - `x.national[i].title`                 X trending posts

    Deduped; cap at 400 headlines total (plenty of surface for
    substring matching, still cheap).
    """
    seen: set[str] = set()
    out:  list[str] = []
    for snap in snaps:
        if not snap:
            continue
        for k in ('national', 'items', 'articles', 'top_articles'):
            rows = snap.get(k)
            if isinstance(rows, list):
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    title = (r.get('title') or r.get('headline')
                             or r.get('text') or '').strip()
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    out.append(title)
                break
        if len(out) >= 400:
            break
    return out[:400]


def _lookup_headlines_for(
    display_name: str,
    name_index: dict[str, list[str]],
    headline_pool: list[str],
) -> list[str]:
    """Return up to 4 news headlines that mention `display_name`.

    Two-pass:
    1. Exact normalized-name hit in `name_index` (NER-attributed).
    2. Multi-token substring scan of `headline_pool`. For multi-word
       display names ("David Jonsson"), requires EVERY 3+ char token
       to appear (case-insensitive) in the same headline. Prevents
       false hits like "David Beckham" matching "David Jonsson" via
       the shared first name; both tokens must co-occur. Single-token
       display names (e.g. "Snapple") just need one word-boundary hit.
    """
    key = _cp_normalize(display_name)
    out: list[str] = list(name_index.get(key, [])[:4])
    if len(out) >= 3:
        return out[:4]

    raw = (display_name or '').strip()
    if len(raw) < 3:
        return out

    tokens = [t.lower() for t in re.split(r'[^\w]+', raw) if len(t) >= 3]
    if not tokens:
        return out

    for h in headline_pool:
        hlow = h.lower()
        if all(t in hlow for t in tokens) and h not in out:
            out.append(h)
        if len(out) >= 4:
            break
    return out[:4]


def _collect_items() -> list[dict]:
    """Gather the top items across every relevant source. Each returned
    dict looks like:

        {
          'name':      "Elon Musk",
          'source':    'wikipedia',         # or 'people', 'search'
          'context':   "Wikipedia views +67% (204k -> 341k). "
                       "Headlines mentioning: 'Musk unveils new Grok model.'",
        }

    De-dupes by normalized name (so an item that's top-of-list in three
    sources only gets one Claude line, applied to all three)."""
    out:  list[dict] = []
    seen: set[str]   = set()

    def _push(name: str, source: str, context: str = '') -> None:
        key = _cp_normalize(name)
        if not key or key in seen or len(key) < 3:
            return
        seen.add(key)
        out.append({'name': name, 'source': source, 'context': context.strip()})

    # Load upstream snapshots once so we can cross-reference names
    # against headlines below.
    people_snap        = _read_snapshot('gdelt-people') or _read_snapshot('gdelt')
    headlines_snap     = _read_snapshot('gdelt')
    reddit_snap        = _read_snapshot('reddit')
    youtube_snap       = _read_snapshot('youtube')
    philanthropy_snap  = _read_snapshot('philanthropy_news')
    x_snap             = _read_snapshot('x')
    wiki_snap          = _read_snapshot('wikipedia_trending')
    google_snap        = _read_snapshot('google_wide') or _read_snapshot('google_trends')

    name_index    = _build_name_headline_index(people_snap)
    headline_pool = _flatten_headline_pool(
        headlines_snap, reddit_snap, philanthropy_snap, youtube_snap, x_snap,
    )

    # Wikipedia FIRST so it takes the dedup priority - this is the
    # surface where the bio-fallback problem was most visible, so we
    # want maximum coverage here.
    for w in (wiki_snap or {}).get('national', [])[:_MAX_WIKI_ITEMS]:
        title    = w.get('title') or ''
        pct      = w.get('delta_pct')
        views_t  = w.get('views_today') or 0
        views_p  = w.get('views_prior') or 0

        clues: list[str] = []
        if w.get('is_new'):
            clues.append(f'Brand-new in Wikipedia top-1000; {views_t:,} views yesterday.')
        elif pct is not None:
            pct_int = int(round(pct * 100))
            clues.append(
                f'Wikipedia views {pct_int:+d}% ({views_p:,} -> {views_t:,}).'
            )
        else:
            clues.append(f'{views_t:,} Wikipedia pageviews yesterday.')

        # Cross-reference against the pooled news headlines. This is
        # the whole point - without headlines Claude has no event to
        # explain and will (correctly) return an empty string.
        headlines = _lookup_headlines_for(title, name_index, headline_pool)
        if headlines:
            hl_str = ' | '.join(f'"{h}"' for h in headlines)
            clues.append(f'News headlines mentioning {title}: {hl_str}')

        # Attach the Wikipedia extract as a LAST-RESORT bio clue. Claude
        # is told NEVER to output the bio directly, but the bio helps
        # it recognize domain (actor / athlete / brand) so if the only
        # signal is "views up", Claude at least won't hallucinate.
        extract = (w.get('extract') or '').strip()
        if extract:
            # Truncate to first sentence-ish so we don't blow the token
            # budget on 60 bios.
            first = extract.split('. ')[0]
            clues.append(f'Wikipedia bio (context only, do NOT quote): "{first}."')

        _push(title, 'wikipedia', ' '.join(clues))

    # People (GDELT). Reuse the context headlines that came with each
    # person entry.
    for p in (people_snap or {}).get('national', [])[:_MAX_PEOPLE_ITEMS]:
        name = p.get('name') or ''
        m    = p.get('mentions')
        clues: list[str] = []
        if m:
            clues.append(f'Mentioned in {m} top-news articles this cycle.')
        headlines = [h for h in (p.get('context') or [])[:4] if h]
        if headlines:
            hl_str = ' | '.join(f'"{h}"' for h in headlines)
            clues.append(f'Headlines: {hl_str}')
        _push(name, 'people', ' '.join(clues))

    # Google Trends (search). Volume + related queries are the hints.
    searches = (google_snap or {}).get('national') or (google_snap or {}).get('items') or []
    for s in searches[:_MAX_SEARCH_ITEMS]:
        term        = s.get('term') or s.get('query') or ''
        vol         = s.get('volume') or s.get('score') or 0
        related_qs  = (s.get('trend_keywords') or s.get('related_queries')
                       or s.get('related') or [])[:4]
        articles    = (s.get('news_articles') or [])[:2]
        clues: list[str] = []
        if vol:
            clues.append(f'{vol:,}+ searches.')
        if related_qs:
            clues.append('Related queries: ' + ', '.join(str(q) for q in related_qs) + '.')
        for a in articles:
            t = (a or {}).get('title') if isinstance(a, dict) else str(a)
            if t:
                clues.append(f'Headline: "{t}"')
        # Also cross-reference the search term against the pooled
        # headlines (reddit / gdelt / youtube / philanthropy / x).
        for h in _lookup_headlines_for(term, name_index, headline_pool):
            clues.append(f'Related headline: "{h}"')
        _push(term, 'search', ' '.join(clues))

    return out[:_TOTAL_ITEM_CAP]


def _build_prompt(items: list[dict]) -> str:
    """Format the batch prompt to Claude. We give it every item with its
    context clues (news headlines, view deltas, related queries) and
    ask for a JSON map back so parsing is deterministic.

    The prompt is engineered to prevent the failure mode this pipeline
    used to have: falling back to a Wikipedia bio ("British actor born
    1993") as the caption. That describes WHO, not WHY. If the context
    clues don't reveal an actual event, Claude MUST return an empty
    string - the frontend then renders no caption at all, which is
    better than showing a bio.
    """
    header = (
        "You write ONE-LINE explanations of WHY specific people or "
        "topics are TRENDING RIGHT NOW.\n"
        "\n"
        "STRICT RULES:\n"
        "1. Answer WHY (the news event, story, or moment). NEVER answer "
        "WHO or WHAT (biography, occupation, background).\n"
        "2. You MUST anchor every answer to a concrete signal in the "
        "clues: either (a) a news headline quoted in the clues, (b) a "
        "related search query in the clues, or (c) a Wikipedia extract "
        "that describes an EVENT (a date, an action, an incident) - "
        "NOT a bio.\n"
        "3. If the clues contain ONLY a bio line + a view/mention count "
        "and NO headline / event / related-query signal, you MUST "
        "return an empty string \"\". Do NOT guess. Do NOT extrapolate "
        "from the bio (\"probably in a new film\", \"likely upcoming "
        "match\") - that is HALLUCINATION and is banned.\n"
        "4. If a clue explicitly says 'do NOT quote' or 'context only', "
        "treat it as background only. Never paraphrase it as the "
        "answer.\n"
        "5. You MAY cross-reference other items in this batch. If item "
        "A's clues mention item B's name, the connection is a fair "
        "signal to use in either explanation.\n"
        "6. One sentence, present tense, 20 words or fewer. Lead with "
        "the event, not the person's name. Avoid the words 'or', "
        "'possibly', 'likely', 'reportedly' - those are hedges that "
        "indicate you're guessing. If you'd need a hedge, return \"\".\n"
        "7. Do not include em dashes.\n"
        "\n"
        "EXAMPLES:\n"
        "  GOOD: \"Van driven into crowd at Berlin Pride on July 25.\"                    (event from extract)\n"
        "  GOOD: \"Directing upcoming Star Wars: Starfighter film announced this week.\"  (headline signal)\n"
        "  GOOD: \"Featured at Marvel Comic-Con panel with new costume redesign.\"        (headline signal)\n"
        "  BAD:  \"Canadian-American filmmaker and actor.\"                               (bio, not why)\n"
        "  BAD:  \"British actor born 1993.\"                                             (bio, not why)\n"
        "  BAD:  \"Appeared in a recently released film generating interest.\"            (hallucinated bio)\n"
        "  BAD:  \"Announced or completed significant match or career decision.\"         (hedge word 'or')\n"
        "  BAD:  \"Wikipedia views spiked +67% yesterday.\"                               (restates the delta)\n"
        "  BAD:  \"Trending on Wikipedia today.\"                                         (says nothing)\n"
        "\n"
        "OUTPUT FORMAT: Return ONLY a JSON object, no prose. Keys = the "
        "input NAME strings exactly as given. Values = the one-sentence "
        "explanation, or \"\" if no anchoring signal is present.\n"
        "\n"
        "Items:\n"
    )
    lines = []
    for i, it in enumerate(items, start=1):
        key = it['name']
        ctx = it['context'] or '(no context clues available)'
        lines.append(f'  {i}. name={key!r}  |  source={it["source"]}  |  clues={ctx}')
    return header + '\n'.join(lines) + '\n\nJSON output:'


def _extract_json_dict(text: str) -> dict:
    """Extract the first JSON object from `text` and return as dict.
    Returns {} on parse failure."""
    if not text:
        return {}
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _ask_claude(items: list[dict]) -> dict[str, str]:
    """Call Claude with the batch prompt, return {name: explanation}.
    Returns {} on any failure. Never raises."""
    if not items:
        return {}
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        logger.warning("why_trending: ANTHROPIC_API_KEY not set; skipping")
        return {}
    try:
        import anthropic
    except ImportError as e:
        logger.warning("why_trending: anthropic SDK not installed: %s", e)
        return {}

    client  = anthropic.Anthropic(api_key=api_key)
    prompt  = _build_prompt(items)
    try:
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}],
        )
    except Exception as e:
        logger.warning("why_trending: anthropic call failed: %s", e)
        return {}

    text = ''
    for block in resp.content or []:
        if getattr(block, 'text', ''):
            text += block.text
    parsed = _extract_json_dict(text)
    if not parsed:
        logger.warning("why_trending: could not parse JSON from Claude output")
        return {}

    # Re-key by _cp_normalize so the app's stamp step can look up by
    # the same key it uses for the cross-platform annotator.
    out: dict[str, str] = {}
    for raw_key, raw_val in parsed.items():
        norm = _cp_normalize(raw_key)
        val  = (raw_val or '').strip()
        if norm and val:
            out[norm] = val
    return out


def fetch() -> dict[str, Any]:
    items = _collect_items()
    if not items:
        return {
            'items': {},
            'count': 0,
            'error': 'no upstream snapshots available',
        }
    explanations = _ask_claude(items)
    return {
        'items':  explanations,
        'count':  len(explanations),
        'inputs': [{'name': it['name'], 'source': it['source']} for it in items],
        'model':  _CLAUDE_MODEL,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('why_trending', 'Why is this trending?', 'meta', fetch)
    print(f"why_trending: count={result.get('count')} error={result.get('error')}",
           file=sys.stderr)
    for k, v in list((result.get('items') or {}).items())[:8]:
        print(f"  {k}: {v}", file=sys.stderr)
