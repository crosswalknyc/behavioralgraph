"""
Meta Quest (formerly Oculus) store trending games scraper.

The Meta Horizon Store is a React SPA hydrated from an internal Relay
GraphQL stream, but every store section page ships the current
top-list inline in the initial HTML as an
`AppStoreLaserSectionUnitsConnection` / `AppStoreCuratedSectionUnitsConnection`
edge array. Anonymous curl_cffi impersonation (via `_base.http_get`
with `impersonate='chrome124'`) is sufficient to fetch it - no cookies
required.

We pull two lists to match the two rails Meta surfaces on its own
Games landing (`meta.com/experiences/view/777072216186618/`):

  Section 325830172628417 = "Top-selling this week" (revenue-ranked, a
                             mix of free-with-IAP + paid). Emit as
                             `meta_quest_paid`.
  Section 891919991406810 = "Most popular" (content_type=TRENDING,
                             free-dominated). Emit as
                             `meta_quest_free`.

Each section returns ~24 items inline. Fields harvested from the
initial HTML per item:

  id, display_name, category_name (Games / Apps / Entertainment /
  Early Access), genre_names, quality_rating_i18n_score_string,
  quality_rating_i18n_count_string, current_offer.price.formatted,
  price_or_status_display.text, assets.{cover_portrait_image,
  cover_square_image, cover_landscape_image, icon_image}.uri

Publisher / developer name lives on the PDP page (`developer_name` /
`publisher_name`) - NOT in the section blob. Skipping publisher
hydration keeps the scraper to 2 HTTP calls total; the frontend
already renders empty-publisher gracefully (Xbox rows do the same
when DisplayCatalog omits it).

Snapshot key: `s3://dashboard-inputs/trends_iq_snapshots/latest/meta_quest.json`
Layout: matches the FAST-channels shape - one snapshot, `sources`
        dict with one entry per panel key. `trends_iq.
        _fetch_gaming_trending` reads either the direct-national
        layout (Xbox) or the sources-keyed layout (Meta Quest).

Cookies: not required. Anonymous fetch works reliably. If Meta ever
tightens the anti-abuse posture, the standard `donate_cookies.py
meta.com` flow + `DOMAIN_REFRESH_MAP` entry in
`refresh_after_donation.py` will inject a session automatically.

Standalone:
    python3 -m scripts.trends_scrapers.meta_quest
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Optional

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


# ----- Section configuration -----
# (source_key, section_id, label, price_bucket)
# price_bucket:
#   'free' -> keep rows where current_offer.price.formatted == '$0.00'
#   'paid' -> keep rows where current_offer.price.formatted != '$0.00'
#   'any'  -> no price filter
#
# The "Most popular" (TRENDING) section is free-dominated but does
# occasionally surface a paid title if it is trending heavily; we
# filter to free-only so the Top Free panel reads as pure free. The
# "Top-selling this week" (TOP_SELLING) section is revenue-ranked and
# includes free-with-IAP entries; we filter to paid so the Top Paid
# panel represents actual paid catalog sales.
_SECTIONS = [
    ('meta_quest_free', '891919991406810', 'Meta Quest - Top Free',  'free'),
    ('meta_quest_paid', '325830172628417', 'Meta Quest - Top Paid',  'paid'),
]

_SECTION_URL_TMPL = 'https://www.meta.com/experiences/section/{section_id}/'
_PDP_URL_TMPL     = 'https://www.meta.com/experiences/pdp/{pid}/'

# Cap per panel. The section blobs typically ship 24 pre-hydrated
# items; more than that would require a follow-on Relay pagination
# call. 20 is plenty for the Gaming panel (which caps display at 25
# per pill).
_MAX_ITEMS_PER_PANEL = 20


def _parse_section_edges(html: str, section_id: str) -> list[dict]:
    """Locate the section block for `section_id` in the page HTML and
    walk its `edges` array, JSON-parsing each Application node with
    balanced-brace extraction (regex alone can't handle the nested
    braces inside `assets.*.uri` etc.). Returns raw node dicts."""
    # Try both section types. AppStoreLaserSection is the algorithmic
    # ordering (TOP_SELLING, TRENDING); AppStoreCuratedSection is a
    # hand-curated editorial list. Both wrap edges in
    # `AppStoreLaserSectionUnitsEdge` / `AppStoreCuratedSectionUnitsEdge`.
    #
    # The typename key varies across the Relay stream depending on
    # which fragment printed the block: `__typename`, `__isAppStoreItemList`,
    # `__isAppStoreSection`, `__isNode` all show up in practice. Match
    # any of them by anchoring on the value + id combo.
    section_marker = re.compile(
        r'"AppStore(?:Laser|Curated|Scheduled)Section","id":"'
        + re.escape(section_id) + r'"',
    )
    m = section_marker.search(html)
    if not m:
        return []

    # From the section marker, find the units{edges:[ block. There may
    # be a `units_meta` block first (with empty stub edges), so we
    # want the FIRST `"units":{` after the marker that's followed by
    # non-empty edges.
    tail = html[m.start():]
    units_iter = list(re.finditer(r'"units":\{', tail))
    edge_re = re.compile(
        r'"__typename":"AppStore(?:Laser|Curated|Scheduled)SectionUnitsEdge",'
        r'(?:"ranking_trace":"[^"]*",)?"node":\{',
    )

    nodes: list[dict] = []
    for units_m in units_iter:
        # Scan forward for the edge entries within this units{...}
        segment_start = units_m.end()
        segment = tail[segment_start:segment_start + 300_000]
        # Bail early if this units block is the "units_meta" stub
        # (no real edges).
        if '"AppStoreLaserSectionUnitsEdge","node":{' not in segment \
                and '"AppStoreCuratedSectionUnitsEdge","node":{' not in segment \
                and '"AppStoreScheduledSectionUnitsEdge","node":{' not in segment:
            continue

        for edge_m in edge_re.finditer(segment):
            # `node` starts at the opening `{` of the JSON object we
            # want (edge_m.end() is right after that `{`; back up 1).
            obj_start = segment_start + edge_m.end() - 1
            node = _balanced_json_object(tail, obj_start)
            if node:
                nodes.append(node)
        if nodes:
            break  # first non-empty units{} block wins

    return nodes


def _balanced_json_object(src: str, start: int) -> Optional[dict]:
    """Given a `{` position in `src`, walk forward counting braces
    (respecting strings + escapes) until the matching `}` and
    JSON-parse the enclosed object. Returns the parsed dict, or None
    on parse failure."""
    if start >= len(src) or src[start] != '{':
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(src)):
        ch = src[i]
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                blob = src[start:i + 1]
                try:
                    obj = json.loads(blob)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _pick_image(node: dict) -> str:
    """Portrait first (rail thumbnails render tall), then square,
    landscape, icon. Every Meta Quest node ships at least the icon."""
    assets = node.get('assets') or {}
    for k in ('cover_portrait_image', 'cover_square_image',
              'cover_landscape_image', 'icon_image'):
        blob = assets.get(k) or {}
        uri = (blob.get('uri') or '').strip()
        if uri:
            return uri
    return ''


def _pick_primary_genre(node: dict) -> str:
    """First genre entry is what Meta surfaces as the primary tag on
    the tile subtitle. Falls back to category_name if genres are
    empty (Apps often have empty genre_names)."""
    genres = node.get('genre_names') or []
    for g in genres:
        if isinstance(g, str) and g.strip():
            return g.strip()
    return (node.get('category_name') or '').strip()


def _keep_by_price(node: dict, price_bucket: str) -> bool:
    """True if this node passes the panel's price filter."""
    if price_bucket == 'any':
        return True
    offer = node.get('current_offer') or {}
    price = ((offer.get('price') or {}).get('formatted') or '').strip()
    # Empty or unparseable price -> keep on 'free' panel only (matches
    # how the store treats no-offer nodes as "Get").
    if not price:
        return price_bucket == 'free'
    is_free = price in ('$0.00', 'FREE', 'Free', '$0')
    if price_bucket == 'free':
        return is_free
    if price_bucket == 'paid':
        return not is_free
    return True


def _node_to_item(node: dict, rank: int) -> dict:
    """Normalize a raw Application node into the row shape the Gaming
    panel renderer + game estimator expect."""
    pid = str(node.get('id') or '').strip()
    title = (node.get('display_name') or node.get('display_unit_title')
             or '').strip()
    offer = node.get('current_offer') or {}
    price = ((offer.get('price') or {}).get('formatted') or '').strip()
    status = ((node.get('price_or_status_display') or {}).get('text')
              or '').strip()
    rating_str = (node.get('quality_rating_i18n_score_string') or '').strip()
    reviews_str = (node.get('quality_rating_i18n_count_string') or '').strip()
    return {
        'rank':             rank,
        'title':            title,
        'url':              _PDP_URL_TMPL.format(pid=pid) if pid else '',
        'image':            _pick_image(node),
        # Publisher hydration would require a per-item PDP fetch. Left
        # blank on first-run; the frontend renders empty gracefully.
        'publisher':        '',
        'genre':            _pick_primary_genre(node),
        'product_id':       pid,
        'category_display': (node.get('category_name') or 'Game').strip(),
        # Price + status let the frontend show a $19.99 or Get chip
        # later if we ever want; currently unused but harmless to
        # carry through.
        'price':            price,
        'price_status':     status,
        'rating':           rating_str,
        'reviews':          reviews_str,
        # Meta Quest doesn't have a "recently added" flag on the
        # section blob (unlike Xbox's dual-rail hydration). Leaving
        # False so the frontend NEW badge stays hidden.
        'recently_added':   False,
    }


def _load_previous_snapshot() -> Optional[dict]:
    """Best-effort read of yesterday's snapshot so a bad fetch day
    still surfaces something in the dashboard (stale-from-previous
    marker matches xbox_gamepass's pattern)."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                          Key='trends_iq_snapshots/latest/meta_quest.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        return d if isinstance(d, dict) else None
    except Exception as e:
        logger.info("meta_quest: could not read previous snapshot: %s", e)
        return None


def _mark_cookie_gap(reason: str = '') -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap('meta_quest', 'meta.com', reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for meta_quest: %s", e)


def _fetch_one_section(section_id: str, price_bucket: str,
                       label: str) -> list[dict]:
    """Hit the /section/<id>/ URL and return the price-filtered,
    ranked item list capped at `_MAX_ITEMS_PER_PANEL`."""
    url = _SECTION_URL_TMPL.format(section_id=section_id)
    r = http_get(url, cookie_domain='meta.com', timeout=30, retries=2)
    if r is None:
        logger.warning("meta_quest: %s (%s) fetch returned None",
                       label, section_id)
        return []
    try:
        html = r.text if hasattr(r, 'text') else r.decode('utf-8')
    except Exception:
        html = ''
    if not html:
        logger.warning("meta_quest: %s (%s) empty body", label, section_id)
        return []

    nodes = _parse_section_edges(html, section_id)
    logger.info("meta_quest: %s (%s) parsed %d raw nodes from %d-byte HTML",
                label, section_id, len(nodes), len(html))
    kept = [n for n in nodes if _keep_by_price(n, price_bucket)]
    items = []
    for i, node in enumerate(kept[:_MAX_ITEMS_PER_PANEL], 1):
        items.append(_node_to_item(node, rank=i))
    return items


def fetch() -> dict[str, Any]:
    """Fetch both Top Free + Top Paid sections and return the
    dashboard-shaped snapshot.

    Layout: `sources` is a dict keyed by panel slug. `trends_iq.
    _fetch_gaming_trending` looks up each panel via `(snapshot_slug,
    source_key)` mapping in `GAMING_PLATFORMS`.
    """
    sources: dict[str, dict] = {}
    prior: Optional[dict] = None
    total_items = 0
    for source_key, section_id, label, price_bucket in _SECTIONS:
        items = _fetch_one_section(section_id, price_bucket, label)
        if not items:
            # Fallback: yesterday's per-panel items so the dashboard
            # isn't empty on a bad fetch day. Loaded lazily.
            if prior is None:
                prior = _load_previous_snapshot() or {}
            prev_panel = ((prior.get('sources') or {}).get(source_key) or {})
            prev_items = prev_panel.get('items') or []
            if prev_items:
                logger.info("meta_quest: %s empty -> using previous %d items",
                            label, len(prev_items))
                items = prev_items
                sources[source_key] = {
                    'label':              label,
                    'items':              items,
                    'available':          bool(items),
                    'stale_from_previous': True,
                    'soft_block_reason':  f'{label} live fetch returned 0 items',
                }
                continue
            _mark_cookie_gap(reason=(f'meta_quest: {label} '
                                      f'section {section_id} returned 0 items'))
        sources[source_key] = {
            'label':     label,
            'items':     items,
            'available': bool(items),
        }
        total_items += len(items)

    logger.info("meta_quest: emitting %d items across %d panels",
                total_items, len(sources))

    return {
        # `national` stays empty: consumers look up per-panel data via
        # `sources[<panel_key>]` per the FAST-channels pattern.
        'national': [],
        'sources':  sources,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('meta_quest', 'Meta Quest', 'gaming', fetch)
    total = sum(len((s.get('items') or []))
                for s in (result.get('sources') or {}).values())
    print(f"meta_quest: {total} items across "
          f"{len(result.get('sources') or {})} panels  "
          f"error={result.get('error')}", file=sys.stderr)
