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
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


# Mirror trends_iq.py so we're guaranteed to read the same S3 keys the
# app reads. If either file moves, they move together.
_S3_BUCKET  = 'dashboard-inputs'
_S3_PREFIX  = 'trends_iq_snapshots/latest/'

# Cap how many items we ask Claude to explain per day. 30 is enough to
# cover every top-of-panel row that a user actually looks at. More than
# that just burns tokens - the long tail of trending is undifferentiated.
_MAX_ITEMS_PER_SOURCE = 8
_TOTAL_ITEM_CAP       = 30

# Cheap fast model - single-sentence explanations don't need Opus.
_CLAUDE_MODEL = os.environ.get('WHY_TRENDING_MODEL') or 'claude-3-5-haiku-latest'
_MAX_TOKENS   = 1500


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
    """Return the S3 snapshot for `source` or None on any failure."""
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=f'{_S3_PREFIX}{source}.json')
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.info("why_trending: skip %s (%s)", source, e)
        return None


def _collect_items() -> list[dict]:
    """Gather the top items across every relevant source. Each returned
    dict looks like:

        {
          'name':      "Elon Musk",
          'source':    'people',            # or 'wikipedia', 'search', 'headlines'
          'context':   "mentioned in 42 articles this week; xAI news mentions Grok",
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

    # People (GDELT). Pick the top 8. Include mention counts as context.
    people_snap = _read_snapshot('gdelt-people') or _read_snapshot('gdelt')
    for p in (people_snap or {}).get('people', [])[:_MAX_ITEMS_PER_SOURCE]:
        context = ''
        m = p.get('mentions')
        if m:
            context = f'mentioned in {m} news articles this week'
        # If the snapshot carries headline snippets, dip in for one.
        related = (p.get('related_articles') or [])[:1]
        if related:
            title = (related[0] or {}).get('title')
            if title:
                context += f'; headline: "{title}"'
        _push(p.get('name') or '', 'people', context)

    # Wikipedia. Delta % + view count is the context.
    wiki_snap = _read_snapshot('wikipedia_trending')
    for w in (wiki_snap or {}).get('national', [])[:_MAX_ITEMS_PER_SOURCE]:
        pct     = w.get('delta_pct')
        views_t = w.get('views_today') or 0
        views_p = w.get('views_prior') or 0
        if w.get('is_new'):
            context = f'brand-new in Wikipedia top-1000; {views_t:,} views yesterday'
        elif pct is not None:
            pct_int = int(round(pct * 100))
            context = f'Wikipedia views {pct_int:+d}% ({views_p:,} -> {views_t:,})'
        else:
            context = f'{views_t:,} Wikipedia pageviews yesterday'
        _push(w.get('title') or '', 'wikipedia', context)

    # Google Trends (search). Volume + related queries are the hints.
    google_snap = _read_snapshot('google_wide') or _read_snapshot('google_trends')
    searches    = (google_snap or {}).get('national') or (google_snap or {}).get('items') or []
    for s in searches[:_MAX_ITEMS_PER_SOURCE]:
        vol         = s.get('volume') or s.get('score') or 0
        related_qs  = (s.get('trend_keywords') or s.get('related_queries') or [])[:4]
        articles    = (s.get('news_articles') or [])[:1]
        context     = ''
        if vol:
            context = f'{vol:,}+ searches'
        if related_qs:
            context += ('; ' if context else '') + 'related: ' + ', '.join(related_qs)
        if articles:
            t = (articles[0] or {}).get('title') or ''
            if t:
                context += ('; ' if context else '') + f'headline: "{t}"'
        _push(s.get('term') or s.get('query') or '', 'search', context)

    return out[:_TOTAL_ITEM_CAP]


def _build_prompt(items: list[dict]) -> str:
    """Format the batch prompt to Claude. We give it every item with its
    context and ask for a JSON map back so parsing is deterministic."""
    header = (
        "You explain WHY specific topics are trending, using ONLY the "
        "context clues provided. If the clues don't establish the reason, "
        "respond with an empty string for that item. NEVER invent facts.\n\n"
        "For each item below, return a single sentence, 15 words max, in "
        "present tense. Focus on the news/event/moment driving the spike.\n\n"
        "Return ONLY a JSON object, no prose. Keys = the input keys as-is. "
        "Values = the explanation (or empty string).\n\n"
        "Items:\n"
    )
    lines = []
    for i, it in enumerate(items, start=1):
        key = it['name']  # human-readable is fine; Claude keys the response
        ctx = it['context'] or '(no context)'
        lines.append(f'  {i}. "{key}"  |  source={it["source"]}  |  context={ctx}')
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
