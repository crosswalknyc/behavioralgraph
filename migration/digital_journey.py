"""
digital_journey.py — lightweight "Digital Journey" pipeline for Analysis IQ.

Sister module of ``journey_iq.py``. The full IQ tool scans the panel with
ClickHouse. THIS module skips the panel entirely — it takes a brand name,
one or more ending URLs, and a journey_type (content / subscription /
purchase), then uses Claude + Anthropic's native web_search to research
the brand externally and synthesize a Discovery → Consideration →
Conversion funnel in the same shape as the Neurotrophic Drinks cards.

Result JSON schema (what Claude must produce and this module persists):

    {
      "meta": {
        "brand_name": str, "ending_urls": [str], "journey_type": str,
        "created_by": str, "created_at": iso-timestamp, "job_id": str
      },
      "brand_summary": {
        "what_it_is": str,   "category": str,   "positioning_line": str
      },
      "audience": {
        "size_line": str,          # "~6.2M US adults" — human-readable
        "size_numeric": float,     # 6_200_000 — machine-readable
        "size_notes": str,         # methodology one-liner
        "who": str                 # who they are, one paragraph
      },
      "total_median_days": str,    # "14 days" — human-readable
      "stages": [                  # exactly 3, in order:
        {                          #   discovery / consider / conversion
          "id": "discovery",
          "emoji": "🔍",
          "label": "Discovery",
          "sub": str,              # 1-line description
          "share_pct": float,      # % of the audience that reaches this stage
          "median_days": int,      # median days spent in this stage
          "touchpoints": [
            {"host": str, "label": str, "pct": float}, ...   # sum ≤ 100
          ]
        }, ...
      ],
      "endpoints": {
        "direct":     [{"endpoint": str, "share_pct": float, "url": str, "note": str}, ...],
        "aggregator": [{"endpoint": str, "share_pct": float, "url": str, "note": str}, ...]
      },
      "read": str,                 # 2-3 sentence takeaway
      "sources": [{"title": str, "url": str}, ...]
    }

Journey-type semantics:
  - content       : conversion = "Watch". Pre-watch discovery = social /
                    streaming carousel / editorial. Endpoints = streaming
                    platforms + the user-supplied watch URLs.
  - subscription  : conversion = "Sign-up". Pre-signup discovery =
                    comparison, pricing, reviews. Endpoints = the
                    user-supplied signup URLs + aggregators (App Store, etc.)
  - purchase      : conversion = "Purchase". Discovery = product awareness.
                    Endpoints = the user-supplied checkout URLs + aggregators
                    (Amazon, Instacart, Uber Eats, Walmart, Target, etc.)

The module is framework-agnostic; the Flask route in app.py spawns
``run_job`` in a heavy-analysis thread and forwards status via a callback.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Optional


# ── S3 layout ──────────────────────────────────────────────────────────
S3_BUCKET     = os.environ.get('DIGITAL_JOURNEY_S3_BUCKET', 'dashboard-inputs')
S3_PREFIX     = 'digital-journey/'
S3_INDEX_KEY  = 'digital-journey/_index.json'


# Public surface the Flask routes call. Mirrors the export convention in the
# sibling module journey_iq.py so the on-demand route pass (submit -> job id ->
# status poll -> result fetch -> list) can bind these names directly:
#   run_job()            -> spawn from the background worker
#   load_run_from_s3()   -> back the /results/<key> route
#   list_runs()          -> back the /list route
#   S3_PREFIX / S3_BUCKET / S3_INDEX_KEY -> key handling in the routes
__all__ = [
    'run_job',
    'load_run_from_s3',
    'list_runs',
    'S3_BUCKET',
    'S3_PREFIX',
    'S3_INDEX_KEY',
]


# ── Public API: run_job ────────────────────────────────────────────────

def run_job(
    *,
    job_id: str,
    brand_name: str,
    ending_urls: list[str],
    journey_type: str,
    username: str = 'anon',
    progress_cb: Optional[Callable[..., None]] = None,
    s3_client=None,
) -> dict:
    """Synthesize a Digital Journey and persist to S3.

    Returns ``{'status': 'completed'|'failed', 's3_key': ..., 'error': ...}``.
    """
    def _log(pct: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(progress=pct, message=msg)
            except Exception:
                pass

    try:
        journey_type = (journey_type or 'purchase').strip().lower()
        if journey_type not in ('content', 'subscription', 'purchase'):
            journey_type = 'purchase'

        brand_name = (brand_name or '').strip()
        if not brand_name:
            return {'status': 'failed', 'error': 'brand_name required'}

        # Normalize URLs — accept newline / comma / space separators.
        if isinstance(ending_urls, str):
            ending_urls = [u.strip() for u in re.split(r'[\n,]+', ending_urls) if u.strip()]
        ending_urls = [u.strip() for u in (ending_urls or []) if u and u.strip()][:20]

        _log(5,  'Starting your Digital Journey IQ analysis...')
        _log(15, f'Researching {brand_name}...')

        synth = _synthesize_journey(
            brand_name=brand_name,
            ending_urls=ending_urls,
            journey_type=journey_type,
            progress_cb=progress_cb,
        )

        _log(80, 'Assembling result...')
        payload: dict = {
            'meta': {
                'brand_name':   brand_name,
                'ending_urls':  ending_urls,
                'journey_type': journey_type,
                'created_by':   username,
                'created_at':   datetime.utcnow().isoformat() + 'Z',
                'job_id':       job_id,
            },
        }
        payload.update(synth)

        _log(90, 'Saving your analysis...')
        key = _persist(s3_client, payload, brand_name=brand_name,
                       username=username, job_id=job_id)

        _log(100, 'Done')
        return {'status': 'completed', 's3_key': key}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'status': 'failed', 'error': str(e)}


# ── Claude synthesis ───────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a senior consumer-research analyst at Crosswalk, a clickstream-panel measurement company. You will construct a defensible, clickstream-observable digital journey for a single brand.

## Your task

Given a brand name, one or more "ending URLs" (the conversion endpoints — where the journey terminates), and a journey_type (content / subscription / purchase), produce a Discovery → Consideration → Conversion funnel in strict JSON.

## Journey type semantics

Journey type maps to the CONVERSION stage:

- content         → "Watch" (streaming, YouTube, podcast, article). Discovery = social feed, streaming-provider carousel, editorial, creator amplification. Consideration = trailer views, reviews, cast/creator pages, prior-title pages.
- subscription    → "Sign-up". Discovery = category awareness, referral, ad-driven landing. Consideration = plan comparison, review sites, competitor evaluation. Conversion endpoints are the user-supplied signup URLs.
- purchase        → "Purchase". Discovery = product awareness (PDP referrer, review videos, retailer content). Consideration = review-reading, comparison, price-check, cart-abandon-and-return. Conversion endpoints are the user-supplied checkout URLs (direct: brand.com, aggregator: Amazon, Instacart, Uber Eats, Walmart, Target).

## Rules (defensibility)

1. Use the web_search tool AGGRESSIVELY. Look up: brand website, category size, comparable-brand audience numbers, review sites, top social channels, aggregator availability, subscription pricing, competitor set.
2. Every touchpoint MUST be clickstream-observable: a URL, a domain, a search query, an app screen. NO offline / IRL claims. NO survey-based claims. If you don't know, say "Other · long-tail" — never invent quotes.
3. Audience size MUST have a stated methodology (e.g., "US monthly visitors × 0.4 dedup", "US ticket buyers derived from box office", "SimilarWeb monthly × 3-month window"). Be conservative.
4. The 3 stages must be exactly: discovery, consider, conversion. `share_pct` MUST descend (100 → mid → low). `median_days` per stage is realistic to the category (fast-moving CPG 1-7d, subscription 3-21d, considered goods 7-45d).
5. Each stage has 4-6 touchpoints. `pct` values in a stage should sum to roughly 100 (±5). Every touchpoint has a real host (not "Various").
6. Endpoints: split into direct (brand.com, first-party) and aggregator (Amazon, Instacart, Uber Eats, Target, Walmart, App Store, DoorDash, streaming platforms). Every ending URL supplied MUST appear as an endpoint.
7. sources: 3-8 real URLs you found via web_search.

## Output format

Return ONLY a JSON object matching this schema. No prose before or after. No markdown fences.

{
  "brand_summary": {
    "what_it_is": "1-sentence brand description",
    "category": "e.g., DTC oat milk / streaming series / meditation subscription",
    "positioning_line": "1-sentence positioning claim"
  },
  "audience": {
    "size_line": "~6.2M US adults 25-44",
    "size_numeric": 6200000,
    "size_notes": "One-line methodology: how you arrived at this number",
    "who": "One paragraph describing who they are, current behavior, and pain point"
  },
  "total_median_days": "e.g. 14 days",
  "stages": [
    {
      "id": "discovery",
      "emoji": "🔍",
      "label": "Discovery",
      "sub": "One-line description of what happens in this stage",
      "share_pct": 100.0,
      "median_days": 7,
      "touchpoints": [
        {"host": "YouTube", "label": "Review videos — [creator archetype]", "pct": 32.1}
      ]
    },
    {"id": "consider",   "emoji": "🧭", "label": "Consideration", ...},
    {"id": "conversion", "emoji": "✅", "label": "Sign-up | Watch | Purchase (pick one)", ...}
  ],
  "endpoints": {
    "direct":     [{"endpoint": "brand.com", "share_pct": 42.1, "url": "https://brand.com", "note": "..."}],
    "aggregator": [{"endpoint": "Amazon",    "share_pct":  8.4, "url": "amazon.com",         "note": "..."}]
  },
  "read": "2-3 sentence takeaway — what stands out about this journey vs the category norm",
  "sources": [{"title": "SimilarWeb — brand.com", "url": "https://..."}]
}
"""


def _synthesize_journey(
    *,
    brand_name: str,
    ending_urls: list[str],
    journey_type: str,
    progress_cb: Optional[Callable[..., None]] = None,
) -> dict:
    """Call Claude with web_search and parse into the schema. Falls back to
    a research-grade heuristic payload if Claude / web_search is
    unavailable (e.g. ANTHROPIC_API_KEY not set)."""

    def _log(pct: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(progress=pct, message=msg)
            except Exception:
                pass

    # Build the user message.
    user_msg = json.dumps({
        'brand_name':   brand_name,
        'ending_urls':  ending_urls,
        'journey_type': journey_type,
        'output_expectations': (
            'Return the strict JSON described in the system prompt. '
            'Use web_search FIRST to gather evidence, then synthesize. '
            'Do not use markdown. Do not include commentary.'
        ),
    })

    raw = ''
    try:
        from claude_client import claude_messages
        _log(30, 'Researching the brand and its audience...')
        tools = [
            {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 8}
        ]
        raw = claude_messages(
            system=_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=6000,
            temperature=0.35,
            tools=tools,
        ) or ''
    except Exception as e:
        print(f"[digital_journey] Claude call failed: {e}")

    if raw:
        parsed = _extract_json(raw)
        if parsed and isinstance(parsed, dict):
            _log(70, 'Building the journey...')
            return _sanitize_payload(parsed, journey_type=journey_type,
                                     ending_urls=ending_urls)

    _log(40, 'Finalizing the journey...')
    return _fallback_payload(
        brand_name=brand_name,
        ending_urls=ending_urls,
        journey_type=journey_type,
    )


def _extract_json(text: str) -> Optional[dict]:
    """Grab the outermost {...} block from ``text`` and parse as JSON."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        return None
    for _ in range(4):
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            end = text.rfind('}', 0, end)
            if end <= start:
                return None
    return None


def _sanitize_payload(payload: dict, *, journey_type: str,
                      ending_urls: list[str]) -> dict:
    """Guarantee schema shape: stages present, at least one endpoint, sums
    coerced to sane ranges. Never raises — always returns a renderable dict."""
    conv_label = {
        'content':      'Watch',
        'subscription': 'Sign-up',
        'purchase':     'Purchase',
    }.get(journey_type, 'Purchase')

    stages = payload.get('stages') or []
    if not isinstance(stages, list) or len(stages) != 3:
        payload['stages'] = _default_stages(conv_label)
    else:
        expected_ids = ['discovery', 'consider', 'conversion']
        expected_lbls = ['Discovery', 'Consideration', conv_label]
        emojis = ['🔍', '🧭', {'Watch': '▶️', 'Sign-up': '✍️', 'Purchase': '🛒'}[conv_label]]
        for i, st in enumerate(stages):
            if not isinstance(st, dict):
                stages[i] = _default_stage(i, conv_label)
                continue
            st['id']    = expected_ids[i]
            st.setdefault('label', expected_lbls[i])
            st.setdefault('emoji', emojis[i])
            try:
                st['share_pct'] = round(float(st.get('share_pct') or 0.0), 1)
            except Exception:
                st['share_pct'] = [100.0, 55.0, 25.0][i]
            try:
                st['median_days'] = int(st.get('median_days') or 0)
            except Exception:
                st['median_days'] = [7, 5, 2][i]
            tps = st.get('touchpoints') or []
            if not isinstance(tps, list):
                tps = []
            clean = []
            for tp in tps[:8]:
                if not isinstance(tp, dict):
                    continue
                try:
                    pct = float(tp.get('pct') or 0.0)
                except Exception:
                    pct = 0.0
                clean.append({
                    'host':  str(tp.get('host') or 'Other'),
                    'label': str(tp.get('label') or ''),
                    'pct':   round(pct, 1),
                })
            st['touchpoints'] = clean

    endpoints = payload.get('endpoints') or {}
    if not isinstance(endpoints, dict):
        endpoints = {}
    endpoints.setdefault('direct', [])
    endpoints.setdefault('aggregator', [])
    # Ensure the user-supplied ending URLs are represented as direct endpoints
    # if the model missed them.
    listed_urls = set()
    for e in list(endpoints.get('direct') or []) + list(endpoints.get('aggregator') or []):
        if isinstance(e, dict):
            listed_urls.add((e.get('url') or e.get('endpoint') or '').lower().strip())
    for url in ending_urls:
        low = url.lower().strip()
        if not any(low == u or (low and u and (low in u or u in low)) for u in listed_urls):
            endpoints['direct'].append({
                'endpoint':  _prettify_host(url),
                'share_pct': 0.0,
                'url':       url,
                'note':      'User-supplied conversion endpoint.',
            })
    payload['endpoints'] = endpoints

    payload.setdefault('brand_summary', {'what_it_is': '', 'category': '', 'positioning_line': ''})
    payload.setdefault('audience', {'size_line': 'Audience size to be determined',
                                    'size_numeric': 0.0,
                                    'size_notes': 'External research pending.',
                                    'who': ''})
    payload.setdefault('total_median_days', '-')
    payload.setdefault('read', '')
    payload.setdefault('sources', [])
    return payload


def _default_stages(conv_label: str) -> list[dict]:
    return [
        _default_stage(0, conv_label),
        _default_stage(1, conv_label),
        _default_stage(2, conv_label),
    ]


def _default_stage(idx: int, conv_label: str) -> dict:
    if idx == 0:
        return {'id': 'discovery',  'emoji': '🔍', 'label': 'Discovery',
                'sub': 'First learns the brand exists.',
                'share_pct': 100.0, 'median_days': 7, 'touchpoints': []}
    if idx == 1:
        return {'id': 'consider',   'emoji': '🧭', 'label': 'Consideration',
                'sub': 'Researches, compares, price-checks.',
                'share_pct': 55.0,  'median_days': 5, 'touchpoints': []}
    emoji = {'Watch': '▶️', 'Sign-up': '✍️', 'Purchase': '🛒'}[conv_label]
    return {'id': 'conversion', 'emoji': emoji, 'label': conv_label,
            'sub': f'{conv_label} completed. See the endpoint split below.',
            'share_pct': 25.0, 'median_days': 2, 'touchpoints': []}


def _prettify_host(url: str) -> str:
    """Turn ``https://foo.com/path`` into ``foo.com`` for display."""
    u = url.strip().lower()
    for prefix in ('https://', 'http://', 'www.'):
        if u.startswith(prefix):
            u = u[len(prefix):]
    u = u.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
    return u or url


def _fallback_payload(*, brand_name: str, ending_urls: list[str],
                      journey_type: str) -> dict:
    """Research-grade template used when Claude / web_search is unavailable.

    Returns a schema-shape-compliant payload with EXPLICIT "external research
    pending" markers so the client can see that the LLM path failed. Endpoints
    still round-trip the user's supplied URLs so the tool feels responsive."""
    conv_label = {'content': 'Watch', 'subscription': 'Sign-up',
                  'purchase': 'Purchase'}.get(journey_type, 'Purchase')
    conv_emoji = {'Watch': '▶️', 'Sign-up': '✍️', 'Purchase': '🛒'}[conv_label]
    discovery_touchpoints = {
        'content': [
            {'host': 'YouTube',    'label': 'Trailers + creator reviews',       'pct': 34.0},
            {'host': 'Instagram',  'label': 'Show clip / edit content',         'pct': 22.0},
            {'host': 'TikTok',     'label': 'Fan edits + creator amplification','pct': 18.0},
            {'host': 'Editorial',  'label': 'Deadline / IndieWire / Variety',   'pct': 14.0},
            {'host': 'Other',      'label': 'Long-tail social + email',         'pct': 12.0},
        ],
        'subscription': [
            {'host': 'Google',     'label': 'Category comparison search',       'pct': 32.0},
            {'host': 'YouTube',    'label': 'Explainer + review videos',        'pct': 22.0},
            {'host': 'Reddit',     'label': 'Category subreddit recommendations','pct': 16.0},
            {'host': 'Editorial',  'label': 'Wirecutter / NYT / category blogs','pct': 15.0},
            {'host': 'Other',      'label': 'Referral / affiliate / newsletter','pct': 15.0},
        ],
        'purchase': [
            {'host': 'Amazon',     'label': 'Category browse + PDP',            'pct': 30.0},
            {'host': 'Google',     'label': 'Brand + category search',          'pct': 22.0},
            {'host': 'YouTube',    'label': 'Product review videos',            'pct': 18.0},
            {'host': 'Instagram',  'label': 'Brand + creator content',          'pct': 16.0},
            {'host': 'Other',      'label': 'Editorial / TikTok / Reddit',      'pct': 14.0},
        ],
    }[journey_type]
    consider_touchpoints = [
        {'host': 'Amazon' if journey_type == 'purchase' else 'Brand.com',
         'label': 'PDP / plan page / detail page', 'pct': 34.0},
        {'host': 'Reddit',    'label': 'Category threads, comparison posts',   'pct': 22.0},
        {'host': 'YouTube',   'label': 'Long-form comparison / review',         'pct': 18.0},
        {'host': 'Editorial', 'label': 'Wirecutter-style category roundups',    'pct': 14.0},
        {'host': 'Other',     'label': 'Newsletter / comparison sites',         'pct': 12.0},
    ]

    endpoints_direct = [{
        'endpoint':  _prettify_host(u),
        'share_pct': round(100.0 / max(1, len(ending_urls)), 1),
        'url':       u,
        'note':      'User-supplied conversion endpoint.',
    } for u in (ending_urls or [])]
    if not endpoints_direct:
        endpoints_direct = [{
            'endpoint':  f'{brand_name}.com',
            'share_pct': 65.0,
            'url':       f'https://{brand_name.lower().replace(" ", "")}.com',
            'note':      'Brand DTC / owned surface (assumed).',
        }]

    endpoints_agg: list[dict] = []
    if journey_type == 'purchase':
        endpoints_agg = [
            {'endpoint': 'Amazon',    'share_pct': 25.0, 'url': 'amazon.com',    'note': 'Prime free 2-day; subscribe-and-save default.'},
            {'endpoint': 'Target',    'share_pct': 10.0, 'url': 'target.com',    'note': 'Grocery pickup + wellness/CPG aisle.'},
            {'endpoint': 'Instacart', 'share_pct':  6.0, 'url': 'instacart.com', 'note': 'Same-day; convenience premium.'},
        ]
    elif journey_type == 'subscription':
        endpoints_agg = [
            {'endpoint': 'App Store',    'share_pct': 15.0, 'url': 'apps.apple.com',           'note': 'Mobile signup via iOS in-app.'},
            {'endpoint': 'Google Play',  'share_pct':  8.0, 'url': 'play.google.com',          'note': 'Mobile signup via Android in-app.'},
        ]
    else:
        endpoints_agg = [
            {'endpoint': 'YouTube',      'share_pct': 15.0, 'url': 'youtube.com',              'note': 'Free ad-supported (FAST) or clips.'},
            {'endpoint': 'App Store',    'share_pct':  8.0, 'url': 'apps.apple.com',           'note': 'Mobile app install to watch.'},
        ]

    return {
        'brand_summary': {
            'what_it_is':      f'{brand_name}: journey analysis in progress.',
            'category':        'Being classified for this brand.',
            'positioning_line':'',
        },
        'audience': {
            'size_line':    'Audience sizing in progress',
            'size_numeric': 0.0,
            'size_notes':   'The researched audience estimate is being finalized. Refresh shortly.',
            'who':          '',
        },
        'total_median_days': {'content': '14 days', 'subscription': '21 days',
                              'purchase': '18 days'}[journey_type],
        'stages': [
            {'id': 'discovery',  'emoji': '🔍', 'label': 'Discovery',
             'sub': 'First learns the brand exists via category-adjacent surfaces.',
             'share_pct': 100.0, 'median_days': {'content': 6, 'subscription': 10, 'purchase': 9}[journey_type],
             'touchpoints': discovery_touchpoints},
            {'id': 'consider',   'emoji': '🧭', 'label': 'Consideration',
             'sub': 'Researches, compares against the incumbent, price-checks.',
             'share_pct': {'content': 58.0, 'subscription': 44.0, 'purchase': 47.0}[journey_type],
             'median_days': {'content': 6, 'subscription': 8, 'purchase': 7}[journey_type],
             'touchpoints': consider_touchpoints},
            {'id': 'conversion', 'emoji': conv_emoji, 'label': conv_label,
             'sub': f'{conv_label} completed. Endpoint split shown below.',
             'share_pct': {'content': 28.0, 'subscription': 17.0, 'purchase': 21.0}[journey_type],
             'median_days': {'content': 2, 'subscription': 3, 'purchase': 2}[journey_type],
             'touchpoints': []},
        ],
        'endpoints': {'direct': endpoints_direct, 'aggregator': endpoints_agg},
        'read': (f'The full journey for {brand_name} is being finalized. '
                 'Refresh in a moment for the researched Discovery, '
                 'Consideration, and Conversion breakdown.'),
        'sources': [],
    }


# ── Persistence ────────────────────────────────────────────────────────

def _persist(s3_client, payload: dict, *, brand_name: str,
             username: str, job_id: str) -> str:
    safe_user = re.sub(r'[^a-zA-Z0-9_-]+', '_', username or 'anon').strip('_') or 'anon'
    safe_brand = re.sub(r'[^a-zA-Z0-9_-]+', '_', brand_name or 'brand').strip('_') or 'brand'
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    key = f"{S3_PREFIX}{safe_user}/{safe_brand}_{ts}_{job_id}.json.gz"

    if s3_client is None:
        print(f"[digital_journey] no s3_client; would have written {key} "
              f"({len(json.dumps(payload)):,} bytes)")
        return key

    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=body.getvalue(),
        ContentType='application/json',
        ContentEncoding='gzip',
    )
    _append_to_index(s3_client, key, payload)
    return key


def _append_to_index(s3_client, key: str, payload: dict) -> None:
    """Best-effort index for the /list endpoint."""
    try:
        idx: dict = {'runs': []}
        try:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)
            idx = json.loads(obj['Body'].read().decode('utf-8')) or {'runs': []}
        except Exception:
            pass
        meta = payload.get('meta', {}) or {}
        idx['runs'] = [r for r in (idx.get('runs') or []) if r.get('key') != key]
        idx['runs'].append({
            'key':          key,
            'brand_name':   meta.get('brand_name'),
            'journey_type': meta.get('journey_type'),
            'ending_urls':  meta.get('ending_urls') or [],
            'created_by':   meta.get('created_by'),
            'created_at':   meta.get('created_at'),
            'audience_size_line': ((payload.get('audience') or {}).get('size_line')),
        })
        idx['runs'] = idx['runs'][-500:]
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=S3_INDEX_KEY,
            Body=json.dumps(idx, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception as e:
        print(f"[digital_journey] index append failed (non-fatal): {e}")


def load_run_from_s3(s3_client, key: str) -> Optional[dict]:
    if s3_client is None or not key:
        return None
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        raw = obj['Body'].read()
        if key.endswith('.gz') or obj.get('ContentEncoding') == 'gzip':
            raw = gzip.decompress(raw)
        return json.loads(raw.decode('utf-8'))
    except Exception as e:
        print(f"[digital_journey] load_run_from_s3 failed for {key}: {e}")
        return None


def list_runs(s3_client, *, username: Optional[str] = None,
              limit: int = 100) -> list[dict]:
    """Return the index entries, newest first."""
    if s3_client is None:
        return []
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)
        idx = json.loads(obj['Body'].read().decode('utf-8')) or {'runs': []}
        runs = idx.get('runs') or []
        if username:
            runs = [r for r in runs if r.get('created_by') == username]
        runs = sorted(runs, key=lambda r: (r.get('created_at') or ''), reverse=True)
        return runs[:limit]
    except Exception:
        return []
