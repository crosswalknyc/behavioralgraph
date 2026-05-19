"""
Sentiment IQ
============
Three-layer brand-sentiment tracker built on top of the ClickHouse
panel clickstream.

Layer 1 - Behavioral signals
    For every UID that searched / visited anything matching the brand
    terms in date range, classify each event into a sentiment bucket
    using query-modifier lexicon, domain taxonomy, and path signals.

Layer 2 - Page-content sentiment
    For the top URLs (by panel volume) per brand, fetch the page title /
    meta description / OG snippet, cache to S3, and have OpenAI
    (gpt-4o-mini) classify each into positive / negative / neutral.
    Cached forever per URL hash so this only ever costs once per URL.

Layer 3 - LLM web-search rollups
    Mirror the Fin IQ Alpha Ideas pattern: call gpt-4o-search-preview
    with web_search_options to pull live narratives about the brand
    across news / Reddit / X / forums, synthesize with gpt-4o into a
    JSON packet of three rollups (positive / negative / neutral), each
    with themes, sample quotes, and source links. Cached daily.

The composite Net Sentiment Score (-100..+100) is volume-weighted
across Layer 1 and Layer 2.

This module is intentionally framework-agnostic. The Flask routes in
app.py inject `s3_client`, `openai_client_factory`, and the ClickHouse
connect function so this file stays small and unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, date
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ============================================================================
# CONSTANTS
# ============================================================================

S3_BUCKET = "dashboard-inputs"
SENTIMENT_IQ_S3_PREFIX = "sentiment-iq/"
SENTIMENT_IQ_TRACKERS_PREFIX = SENTIMENT_IQ_S3_PREFIX + "trackers/"
SENTIMENT_IQ_RESULTS_PREFIX = SENTIMENT_IQ_S3_PREFIX + "results/"
SENTIMENT_IQ_ROLLUPS_PREFIX = SENTIMENT_IQ_S3_PREFIX + "rollups/"
SENTIMENT_IQ_PAGES_PREFIX = SENTIMENT_IQ_S3_PREFIX + "pages/"

DEFAULT_OPENAI_CLASSIFY_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_ROLLUP_MODEL = "gpt-4o"
DEFAULT_OPENAI_WEB_SEARCH_MODEL = "gpt-4o-search-preview"

# Max URLs we will fetch + content-classify per tracker run. Page metadata is
# cached per-URL forever, so once popular URLs are scored we never refetch.
MAX_PAGE_CLASSIFY_PER_RUN = 60

# Max distinct event rows we pull from ClickHouse per tracker run. This is a
# hard ceiling on the in-memory dataframe we build behavioral scores from.
MAX_EVENTS_PER_RUN = 250_000

# ── Gen-pop projection ───────────────────────────────────────────────────────
#
# Every count we surface on the dashboard (mentions, voices, demographic
# slice sizes, time-series volumes, etc.) is a *panel* count. To make those
# values comparable to brand-side or media-buying numbers we project them
# to the US gen pop using the same formula CW IQ Ranker uses:
#
#     projected = round(raw * US_POPULATION / panel_size_for_window)
#
# `panel_size_for_window` is the number of distinct UIDs that fired any
# clickstream event during the tracker's date window. We capture it once
# per run (one ClickHouse round-trip) and store it in result["meta"] so
# downstream consumers can reconstruct or sanity-check the projection.
#
# US_POPULATION is shared with iq_rankers via the same env var so both
# projections stay in lockstep — flipping IQ_RANKER_US_POPULATION updates
# Sentiment IQ too.
US_POPULATION = int(os.environ.get("IQ_RANKER_US_POPULATION", "329900000"))


# ── Query-modifier lexicon ───────────────────────────────────────────────────
#
# These are token sets we look for in the URL (which captures both raw
# search-engine query strings and content-page slugs). Tokens are lowercase,
# and we look for whole-token matches (\bWORD\b) to avoid false positives
# like "cancellation policy" triggering on the negative "cancel" cue.

NEGATIVE_QUERY_TOKENS = {
    "scam", "scams", "fraud", "lawsuit", "lawsuits", "sued", "suing",
    "complaint", "complaints", "complains", "complaining",
    "cancel", "cancellation", "unsubscribe", "refund", "refunds",
    "worst", "terrible", "awful", "horrible", "hate", "hates", "hated",
    "broken", "bug", "bugs", "glitch", "glitches", "outage", "down",
    "issue", "issues", "problem", "problems",
    "alternative", "alternatives", "vs",
    "boycott", "controversy", "scandal", "recall",
    "rip", "ripoff", "fake", "fraudulent", "misleading",
    "expensive", "overpriced",
    "delete", "deleted", "deleting",
    "fired", "layoff", "layoffs", "shutdown", "bankruptcy",
    "breach", "hacked", "leak",
    "downvote", "downvotes",
}

POSITIVE_QUERY_TOKENS = {
    "best", "love", "loved", "loving", "favorite", "favourite",
    "great", "amazing", "awesome", "excellent", "fantastic",
    "deal", "deals", "discount", "discounts", "coupon", "coupons",
    "sale", "promo", "promotion", "promotions",
    "review-good", "5-star", "five-star", "recommend", "recommended",
    "official", "shop", "buy",
    "win", "winning", "winner",
}

NEUTRAL_QUERY_TOKENS = {
    "login", "log-in", "signin", "sign-in", "signup", "sign-up",
    "pricing", "price", "cost", "how-much",
    "hours", "near-me", "location", "phone", "support",
    "what", "what-is", "how", "how-to",
    "tutorial", "guide", "faq", "help",
}


# ── Domain taxonomy ──────────────────────────────────────────────────────────
#
# Each entry maps a domain (or hostname suffix) to a (channel, default_bias)
# tuple. default_bias is the prior sentiment we attribute to "visited this
# domain at all" when no other Layer 1 / Layer 2 signal exists.
#
# Channels: search, social, news, review, complaint, forum, marketplace,
#           video, music, ai, app_store, brand, other.

DOMAIN_TAXONOMY: dict[str, tuple[str, str]] = {
    # Search engines (neutral by default — intent comes from query tokens)
    "google.com":            ("search",   "neutral"),
    "bing.com":              ("search",   "neutral"),
    "duckduckgo.com":        ("search",   "neutral"),
    "yahoo.com":             ("search",   "neutral"),
    "search.yahoo.com":      ("search",   "neutral"),
    "brave.com":             ("search",   "neutral"),
    "search.brave.com":      ("search",   "neutral"),
    "ecosia.org":            ("search",   "neutral"),
    "startpage.com":         ("search",   "neutral"),

    # AI search / chat (treat as search channel with own bucket downstream)
    "chat.openai.com":       ("ai",       "neutral"),
    "chatgpt.com":           ("ai",       "neutral"),
    "claude.ai":             ("ai",       "neutral"),
    "gemini.google.com":     ("ai",       "neutral"),
    "perplexity.ai":         ("ai",       "neutral"),
    "copilot.microsoft.com": ("ai",       "neutral"),

    # Social platforms
    "twitter.com":           ("social",   "neutral"),
    "x.com":                 ("social",   "neutral"),
    "reddit.com":            ("forum",    "neutral"),
    "old.reddit.com":        ("forum",    "neutral"),
    "facebook.com":          ("social",   "neutral"),
    "instagram.com":         ("social",   "neutral"),
    "tiktok.com":            ("social",   "neutral"),
    "linkedin.com":          ("social",   "neutral"),
    "threads.net":           ("social",   "neutral"),
    "pinterest.com":         ("social",   "neutral"),
    "snapchat.com":          ("social",   "neutral"),
    "bsky.app":              ("social",   "neutral"),
    "mastodon.social":       ("social",   "neutral"),
    "youtube.com":           ("video",    "neutral"),

    # Forums / Q&A
    "quora.com":             ("forum",    "neutral"),
    "news.ycombinator.com":  ("forum",    "neutral"),
    "discord.com":           ("forum",    "neutral"),
    "stackoverflow.com":     ("forum",    "neutral"),

    # Reviews
    "trustpilot.com":        ("review",   "neutral"),
    "g2.com":                ("review",   "neutral"),
    "capterra.com":          ("review",   "neutral"),
    "yelp.com":              ("review",   "neutral"),
    "glassdoor.com":         ("review",   "neutral"),
    "consumeraffairs.com":   ("review",   "neutral"),
    "apps.apple.com":        ("app_store","neutral"),
    "play.google.com":       ("app_store","neutral"),

    # Complaint platforms (prior is negative)
    "bbb.org":               ("complaint","negative"),
    "complaintsboard.com":   ("complaint","negative"),
    "ripoffreport.com":      ("complaint","negative"),
    "sitejabber.com":        ("complaint","negative"),
    "pissedconsumer.com":    ("complaint","negative"),

    # News / media (sentiment must come from page content — Layer 2)
    "nytimes.com":           ("news",     "neutral"),
    "wsj.com":               ("news",     "neutral"),
    "cnn.com":               ("news",     "neutral"),
    "foxnews.com":           ("news",     "neutral"),
    "reuters.com":           ("news",     "neutral"),
    "bloomberg.com":         ("news",     "neutral"),
    "ft.com":                ("news",     "neutral"),
    "forbes.com":            ("news",     "neutral"),
    "businessinsider.com":   ("news",     "neutral"),
    "techcrunch.com":        ("news",     "neutral"),
    "theverge.com":          ("news",     "neutral"),
    "engadget.com":          ("news",     "neutral"),
    "arstechnica.com":       ("news",     "neutral"),
    "washingtonpost.com":    ("news",     "neutral"),
    "cnbc.com":              ("news",     "neutral"),
    "axios.com":             ("news",     "neutral"),
    "politico.com":          ("news",     "neutral"),
    "vox.com":               ("news",     "neutral"),
    "huffpost.com":          ("news",     "neutral"),
    "buzzfeednews.com":      ("news",     "neutral"),
    "npr.org":               ("news",     "neutral"),
    "bbc.com":               ("news",     "neutral"),
    "bbc.co.uk":             ("news",     "neutral"),

    # Marketplaces (review visits skew negative when path = /reviews)
    "amazon.com":            ("marketplace","neutral"),
    "walmart.com":           ("marketplace","neutral"),
    "target.com":            ("marketplace","neutral"),
    "ebay.com":              ("marketplace","neutral"),
    "etsy.com":              ("marketplace","neutral"),
}


# ── Path signals ─────────────────────────────────────────────────────────────
#
# When we see a URL whose path contains one of these substrings, we apply
# the listed sentiment bias regardless of domain. /reviews on a marketplace
# is a review-seeking visit; /cancel anywhere is a churn-intent signal.

PATH_NEGATIVE = (
    "/cancel", "/cancellation", "/unsubscribe", "/refund",
    "/complaint", "/complaints", "/lawsuit",
    "/delete-account", "/close-account", "/downgrade",
    "/dispute", "/return", "/returns",
)
PATH_POSITIVE = (
    "/deal", "/deals", "/promo", "/promotion", "/discount", "/coupon",
    "/buy", "/checkout", "/order-confirmation", "/thank-you",
    "/welcome", "/onboarding",
)
PATH_REVIEW = (
    "/review", "/reviews", "/ratings", "/customer-reviews",
)


# Subreddit-specific bias. Default for r/* is neutral.
SUBREDDIT_BIAS = {
    "personalfinance":      "neutral",
    "frugal":               "neutral",
    "deals":                "positive",
    "buyitforlife":         "positive",
    "scams":                "negative",
    "consumeradvocacy":     "negative",
    "legaladvice":          "negative",
    "assholedesign":        "negative",
    "mildlyinfuriating":    "negative",
}


# ============================================================================
# DATA CLASSES (simple dicts so they JSON-serialize trivially)
# ============================================================================


def make_tracker_config(
    *,
    tracker_id: str,
    owner: str,
    project_name: str,
    brand_terms: list[str],
    competitor_terms: list[str] | None,
    start_date: str | None,
    end_date: str | None,
    ongoing: bool,
    alert_email: bool,
) -> dict[str, Any]:
    """Build the persisted tracker config blob."""
    now_iso = datetime.utcnow().isoformat() + "Z"
    return {
        "tracker_id": tracker_id,
        "owner": owner,
        "project_name": project_name,
        "brand_terms": [t.strip() for t in brand_terms if t and t.strip()],
        "competitor_terms": [t.strip() for t in (competitor_terms or []) if t and t.strip()],
        "start_date": start_date or None,
        "end_date": end_date or None,
        "ongoing": bool(ongoing),
        "alert_email": bool(alert_email),
        "status": "queued",
        "last_run_at": None,
        "last_run_date": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "version": 1,
    }


# ============================================================================
# S3 HELPERS  (injected client at call time)
# ============================================================================


def tracker_key(tracker_id: str) -> str:
    return f"{SENTIMENT_IQ_TRACKERS_PREFIX}{tracker_id}.json"


def latest_result_key(tracker_id: str) -> str:
    return f"{SENTIMENT_IQ_RESULTS_PREFIX}{tracker_id}/latest.json"


def daily_result_key(tracker_id: str, day: str) -> str:
    return f"{SENTIMENT_IQ_RESULTS_PREFIX}{tracker_id}/daily/{day}.json"


def rollup_key(tracker_id: str, day: str) -> str:
    return f"{SENTIMENT_IQ_ROLLUPS_PREFIX}{tracker_id}/{day}.json"


def page_cache_key(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()
    return f"{SENTIMENT_IQ_PAGES_PREFIX}{h}.json"


def s3_put_json(s3_client, key: str, data: Any, bucket: str = S3_BUCKET) -> bool:
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(data, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return True
    except Exception as e:
        print(f"[SentimentIQ] s3 put {key} failed: {e}")
        return False


def s3_get_json(s3_client, key: str, bucket: str = S3_BUCKET) -> dict | None:
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        return None


SYSTEM_TRACKER_OWNERS = {"iq_rankers"}
# iq_rankers writes tracker_id values prefixed with "iqr_" (see
# iq_rankers._tracker_id_for_profile). We use that filename signature to
# skip the body GET entirely during list_trackers, which turns a few
# hundred S3 GetObjects (one per profile) into a single ListObjectsV2 plus
# only the user-facing GETs.
SYSTEM_TRACKER_KEY_PREFIXES = ("iqr_",)


def _is_system_tracker_key(key: str) -> bool:
    name = key.rsplit("/", 1)[-1]
    return any(name.startswith(p) for p in SYSTEM_TRACKER_KEY_PREFIXES)


def list_trackers(
    s3_client,
    owner: str | None = None,
    *,
    include_system: bool = False,
) -> list[dict]:
    """List Sentiment IQ trackers, optionally filtered by owner.

    `include_system=False` (the default) hides trackers owned by internal
    system accounts (e.g. the per-profile auto-trackers created by
    iq_rankers.py). Those rows share Sentiment IQ's S3 layout for
    storage but aren't user-facing Sentiment IQ trackers — they have no
    rolled-up dashboard JSON, so showing them in the sidebar produces
    empty cards.
    """
    out: list[dict] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=SENTIMENT_IQ_TRACKERS_PREFIX):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                # Fast path: skip iq_rankers' auto-trackers without GETting
                # the body. They're identifiable by the "iqr_" filename
                # prefix and we never want them in the user-facing list.
                if not include_system and _is_system_tracker_key(key):
                    continue
                cfg = s3_get_json(s3_client, key)
                if not cfg:
                    continue
                cfg_owner = cfg.get("owner")
                if not include_system and cfg_owner in SYSTEM_TRACKER_OWNERS:
                    continue
                if owner and cfg_owner != owner:
                    continue
                out.append({
                    "tracker_id": cfg.get("tracker_id"),
                    "project_name": cfg.get("project_name"),
                    "brand_terms": cfg.get("brand_terms", []),
                    "competitor_terms": cfg.get("competitor_terms", []),
                    "ongoing": cfg.get("ongoing", False),
                    "status": cfg.get("status", "unknown"),
                    "last_run_at": cfg.get("last_run_at"),
                    "last_run_date": cfg.get("last_run_date"),
                    "start_date": cfg.get("start_date"),
                    "end_date": cfg.get("end_date"),
                    "owner": cfg.get("owner"),
                    "created_at": cfg.get("created_at"),
                })
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    except Exception as e:
        print(f"[SentimentIQ] list_trackers failed: {e}")
    return out


# ============================================================================
# LAYER 1  -  BEHAVIORAL SCORING
# ============================================================================


# Split on anything that isn't a letter/digit/dash/apostrophe so we treat
# underscores, slashes, plusses, etc. as token boundaries. This matters for
# tokens like "nike_drop_scam" inside Reddit slugs.
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-']{1,}")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9\-']+")


def _tokens(s: str) -> list[str]:
    if not s:
        return []
    parts = _TOKEN_SPLIT_RE.split(s.lower())
    return [p for p in parts if p and _WORD_RE.fullmatch(p)]


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _path_of(url: str) -> str:
    try:
        return urlparse(url if "://" in url else f"https://{url}").path.lower()
    except Exception:
        return ""


def _classify_subreddit(url: str) -> tuple[str | None, str]:
    """Return (subreddit_name, bias) if URL is a reddit /r/<sub>/ path."""
    p = _path_of(url)
    m = re.match(r"^/r/([a-z0-9_]{2,32})", p)
    if not m:
        return None, "neutral"
    sub = m.group(1)
    return sub, SUBREDDIT_BIAS.get(sub, "neutral")


def _channel_for_domain(domain: str) -> tuple[str, str]:
    """Walk up the domain looking for a taxonomy match."""
    if not domain:
        return "other", "neutral"
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in DOMAIN_TAXONOMY:
            return DOMAIN_TAXONOMY[candidate]
    return "other", "neutral"


def score_behavioral_event(url: str, common_name: str = "") -> dict[str, Any]:
    """Score a single clickstream event.

    Returns dict:
        sentiment   - 'positive' | 'negative' | 'neutral'
        score       - -1.0 .. +1.0
        channel     - taxonomy channel
        domain      - parsed eTLD+1
        subreddit   - if a Reddit URL, the subreddit name
        signals     - list of why we scored it that way (debug / UI tooltip)
    """
    domain = _domain_of(url)
    path = _path_of(url)
    channel, prior = _channel_for_domain(domain)
    signals: list[str] = []
    pos, neg = 0, 0

    # Tokenize the entire URL (catches both Google query strings and slugs)
    toks = set(_tokens(url))
    if common_name:
        toks |= set(_tokens(common_name))

    if toks & NEGATIVE_QUERY_TOKENS:
        hits = sorted(toks & NEGATIVE_QUERY_TOKENS)[:5]
        neg += len(hits)
        signals.append(f"neg_tokens:{','.join(hits)}")
    if toks & POSITIVE_QUERY_TOKENS:
        hits = sorted(toks & POSITIVE_QUERY_TOKENS)[:5]
        pos += len(hits)
        signals.append(f"pos_tokens:{','.join(hits)}")

    for needle in PATH_NEGATIVE:
        if needle in path:
            neg += 2
            signals.append(f"path_neg:{needle}")
            break
    for needle in PATH_POSITIVE:
        if needle in path:
            pos += 1
            signals.append(f"path_pos:{needle}")
            break

    review_path = any(p in path for p in PATH_REVIEW)
    if review_path and channel == "marketplace":
        # Visiting /reviews on a marketplace is research, not buy intent.
        signals.append("path_review:marketplace")

    sub, sub_bias = _classify_subreddit(url) if channel == "forum" and "reddit" in domain else (None, "neutral")
    if sub_bias == "negative":
        neg += 1
        signals.append(f"subreddit_neg:r/{sub}")
    elif sub_bias == "positive":
        pos += 1
        signals.append(f"subreddit_pos:r/{sub}")

    if prior == "negative":
        neg += 2
        signals.append(f"domain_prior_neg:{domain}")
    elif prior == "positive":
        pos += 1
        signals.append(f"domain_prior_pos:{domain}")

    if pos == 0 and neg == 0:
        return {
            "sentiment": "neutral",
            "score": 0.0,
            "channel": channel,
            "domain": domain,
            "subreddit": sub,
            "signals": signals,
        }
    total = pos + neg
    raw = (pos - neg) / max(total, 1)
    if raw > 0.15:
        label = "positive"
    elif raw < -0.15:
        label = "negative"
    else:
        label = "neutral"
    return {
        "sentiment": label,
        "score": round(raw, 3),
        "channel": channel,
        "domain": domain,
        "subreddit": sub,
        "signals": signals,
    }


# ============================================================================
# LAYER 2  -  PAGE-CONTENT SENTIMENT
# ============================================================================


class _MetaExtractor(HTMLParser):
    """Minimal HTML parser pulling <title>, og:title, og:description, meta description."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title = ""
        self.og_title = ""
        self.og_description = ""
        self.description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "title":
            self.in_title = True
            return
        if t != "meta":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        prop = a.get("property", "").lower()
        name = a.get("name", "").lower()
        content = a.get("content", "")
        if prop == "og:title" and content:
            self.og_title = content[:300]
        elif prop == "og:description" and content:
            self.og_description = content[:600]
        elif name == "description" and content:
            self.description = content[:600]

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data.strip()[:300]


def fetch_url_metadata(url: str, timeout: float = 5.0) -> dict[str, str]:
    """Best-effort fetch of <title> + meta description + OG tags."""
    out = {"url": url, "title": "", "description": "", "og_title": "", "og_description": ""}
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CrosswalkIQ-SentimentIQ/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000)
        try:
            html = raw.decode("utf-8", errors="ignore")
        except Exception:
            html = ""
        parser = _MetaExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass
        out["title"] = parser.title.strip()
        out["description"] = parser.description.strip()
        out["og_title"] = parser.og_title.strip()
        out["og_description"] = parser.og_description.strip()
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def classify_url_content(
    url: str,
    *,
    brand_terms: list[str],
    s3_client,
    openai_client,
    model: str = DEFAULT_OPENAI_CLASSIFY_MODEL,
) -> dict[str, Any] | None:
    """Layer 2. Cached forever in S3 under SENTIMENT_IQ_PAGES_PREFIX."""
    key = page_cache_key(url)
    cached = s3_get_json(s3_client, key)
    if cached and cached.get("sentiment") in ("positive", "negative", "neutral"):
        return cached

    meta = fetch_url_metadata(url)
    blob = " | ".join(
        s for s in [meta.get("title"), meta.get("og_title"),
                    meta.get("description"), meta.get("og_description")]
        if s
    ).strip()
    if not blob or not openai_client:
        # Fallback: write a neutral cache entry so we don't try forever.
        out = {
            "url": url, "sentiment": "neutral", "score": 0.0,
            "rationale": "no content available",
            "title": meta.get("title", ""), "description": meta.get("description", ""),
            "classified_at": datetime.utcnow().isoformat() + "Z",
        }
        s3_put_json(s3_client, key, out)
        return out

    brands_str = ", ".join(brand_terms[:8])
    prompt = (
        f"You are a sentiment classifier. The following text is the title and "
        f"description of a webpage that mentions one of these brands: {brands_str}. "
        f"Classify how the page portrays the brand as exactly one of "
        f"positive, negative, or neutral, and return strict JSON.\n\n"
        f"TEXT:\n{blob[:1200]}\n\n"
        f"Return JSON: {{\"sentiment\": \"positive|negative|neutral\", "
        f"\"score\": <-1.0..1.0>, \"rationale\": \"<one short sentence>\"}}"
    )
    sentiment = "neutral"
    score = 0.0
    rationale = ""
    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise sentiment classifier. Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.1,
        )
        content = (resp.choices[0].message.content or "").strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content)
        s = str(parsed.get("sentiment", "neutral")).lower()
        if s in ("positive", "negative", "neutral"):
            sentiment = s
        try:
            score = float(parsed.get("score", 0.0))
        except Exception:
            score = {"positive": 0.6, "negative": -0.6, "neutral": 0.0}[sentiment]
        rationale = str(parsed.get("rationale", ""))[:240]
    except Exception as e:
        rationale = f"classify_error: {e}"[:240]

    out = {
        "url": url, "sentiment": sentiment, "score": score,
        "rationale": rationale,
        "title": meta.get("title", ""),
        "og_title": meta.get("og_title", ""),
        "description": meta.get("description", ""),
        "og_description": meta.get("og_description", ""),
        "classified_at": datetime.utcnow().isoformat() + "Z",
        "model": model,
    }
    s3_put_json(s3_client, key, out)
    return out


# ============================================================================
# LAYER 3  -  LLM WEB-SEARCH ROLLUPS
# ============================================================================


def build_web_rollups(
    *,
    brand_terms: list[str],
    competitor_terms: list[str] | None,
    s3_client,
    openai_client,
    tracker_id: str,
    research_model: str = DEFAULT_OPENAI_WEB_SEARCH_MODEL,
    synth_model: str = DEFAULT_OPENAI_ROLLUP_MODEL,
    force: bool = False,
) -> dict[str, Any]:
    """Pull live narratives via web search, synthesize 3 AI rollups.

    Cached daily per tracker. Mirrors Fin IQ Alpha Ideas pattern.
    """
    today = date.today().isoformat()
    cache_key = rollup_key(tracker_id, today)
    if not force:
        cached = s3_get_json(s3_client, cache_key)
        if cached:
            return cached

    brands_str = ", ".join(brand_terms[:8]) or "the brand"
    comp_str = ", ".join((competitor_terms or [])[:8])

    out = {
        "tracker_id": tracker_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "brand_terms": brand_terms,
        "competitor_terms": competitor_terms or [],
        "positive": {"summary": "", "themes": [], "quotes": []},
        "negative": {"summary": "", "themes": [], "quotes": []},
        "neutral":  {"summary": "", "themes": [], "quotes": []},
        "model_research": research_model,
        "model_synth": synth_model,
        "warnings": [],
    }

    if not openai_client:
        out["warnings"].append("openai_unavailable")
        s3_put_json(s3_client, cache_key, out)
        return out

    research = ""
    try:
        research_prompt = (
            f"You are researching public sentiment about {brands_str} across the live web "
            f"(news media, X/Twitter, Reddit, TikTok, YouTube, review sites, forums, blogs). "
            f"Pull the most recent 30 days of discussion if available.\n\n"
            f"Summarize separately:\n"
            f"1. POSITIVE narratives — what fans / customers / press are praising. "
            f"Include 3-5 specific themes and 3 short representative quotes with source URLs.\n"
            f"2. NEGATIVE narratives — complaints, controversies, criticisms, controversies. "
            f"Include 3-5 specific themes and 3 short representative quotes with source URLs.\n"
            f"3. NEUTRAL / informational coverage — news that is neither praising nor attacking. "
            f"Include 3-5 themes and 3 short quotes.\n\n"
            f"Be concrete. Use specific dates, named sources, and verbatim quotes when possible."
        )
        if comp_str:
            research_prompt += f"\n\nFor reference, the brand competes with: {comp_str}."
        rr = openai_client.chat.completions.create(
            model=research_model,
            messages=[{"role": "user", "content": research_prompt}],
            web_search_options={"search_context_size": "high"},
        )
        research = (rr.choices[0].message.content or "").strip()
    except Exception as e:
        out["warnings"].append(f"web_search_failed: {e}")

    if not research:
        s3_put_json(s3_client, cache_key, out)
        return out

    try:
        synth_prompt = f"""
You are turning the following web research about {brands_str} into a structured
sentiment rollup for a Crosswalk IQ Sentiment IQ dashboard.

RESEARCH:
{research[:8000]}

Return STRICT JSON with this shape and nothing else:
{{
  "positive": {{
    "summary": "<2-3 sentence paragraph that captures the overall positive narrative>",
    "themes":  ["<theme 1>", "<theme 2>", "<theme 3>", "<theme 4>", "<theme 5>"],
    "quotes":  [
      {{"text": "<verbatim short quote>", "source": "<source domain or outlet>", "url": "<https://...>"}},
      {{"text": "...", "source": "...", "url": "..."}},
      {{"text": "...", "source": "...", "url": "..."}}
    ]
  }},
  "negative": {{ "summary": "...", "themes": [...], "quotes": [...] }},
  "neutral":  {{ "summary": "...", "themes": [...], "quotes": [...] }}
}}

Rules:
- 3-5 themes per bucket, short.
- 2-3 short quotes per bucket. Each must have text + source + url.
- Do NOT fabricate quotes or URLs that were not in the research.
- If a bucket has nothing in the research, return an empty themes array and empty quotes array.
""".strip()
        sr = openai_client.chat.completions.create(
            model=synth_model,
            messages=[
                {"role": "system", "content": "You are a senior brand-sentiment analyst. Return strict JSON only."},
                {"role": "user", "content": synth_prompt},
            ],
            max_tokens=2200,
            temperature=0.3,
        )
        synth = (sr.choices[0].message.content or "").strip()
        if "```" in synth:
            synth = synth.split("```")[1]
            if synth.startswith("json"):
                synth = synth[4:]
        parsed = json.loads(synth)
        for bucket in ("positive", "negative", "neutral"):
            b = parsed.get(bucket) or {}
            out[bucket]["summary"] = str(b.get("summary", ""))[:1500]
            themes = b.get("themes") or []
            out[bucket]["themes"] = [str(t)[:140] for t in themes][:6]
            quotes = b.get("quotes") or []
            cleaned = []
            for q in quotes[:5]:
                if not isinstance(q, dict):
                    continue
                cleaned.append({
                    "text": str(q.get("text", ""))[:360],
                    "source": str(q.get("source", ""))[:120],
                    "url": str(q.get("url", ""))[:500],
                })
            out[bucket]["quotes"] = cleaned
    except Exception as e:
        out["warnings"].append(f"synth_failed: {e}")
        out["raw_research"] = research[:4000]

    s3_put_json(s3_client, cache_key, out)
    return out


# ============================================================================
# CLICKHOUSE QUERIES
# ============================================================================


def _term_array_literal(terms: list[str]) -> str:
    cleaned = []
    for t in terms:
        s = (t or "").lower().replace("'", "''").strip()
        if s and len(s) >= 3:
            cleaned.append(s)
    if not cleaned:
        return ""
    return ", ".join(f"'{t}'" for t in cleaned)


def pull_events(
    *,
    ch_connect: Callable,
    brand_terms: list[str],
    start_date: str,
    end_date: str,
    limit: int = MAX_EVENTS_PER_RUN,
) -> tuple[list[tuple], int]:
    """Pull (URL, UID, COMMON_NAME, DELIVERED, DOMAIN) rows matching brand terms.

    Matches the term against both COMMON_NAME and URL (multiSearchAny) so we
    catch both "talked about the brand on Google" (search-engine query) and
    "visited a page about the brand" (content). Returns (rows, audience_uid_count).

    Performance notes:
      * Single-pass over clickstream_final (was two: a Memory-table
        audience build + an IN-subquery rescan). The DELIVERED partition
        is monthly so the date range is partition-pruned, and the
        ngrambf_v1 indexes on lower(URL) and lower(COMMON_NAME)
        skip granules that can't match.
      * PREWHERE is applied to the cheapest, most-selective predicates
        (date partition + ngram-indexed substring checks on
        LowCardinality COMMON_NAME) so URL gets compared on far fewer
        rows.
      * audience_uid_count is derived from the returned rows in Python,
        which saves a second `count(DISTINCT UID)` round-trip.
    """
    term_lit = _term_array_literal(brand_terms)
    if not term_lit:
        return [], 0
    conn = ch_connect(settings={
        "max_execution_time": 1200,
        "use_skip_indexes": 1,
        # Be defensive about memory on huge multi-month backfills.
        "max_memory_usage": 8_000_000_000,
        "max_threads": 16,
    })
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT URL, UID, COMMON_NAME, DELIVERED, DOMAIN
            FROM clickstream.clickstream_final
            PREWHERE DELIVERED BETWEEN toDate('{start_date}') AND toDate('{end_date}')
                 AND (multiSearchAny(lower(COMMON_NAME), [{term_lit}])
                      OR multiSearchAny(lower(URL), [{term_lit}]))
            WHERE length(URL) > 5
            LIMIT {int(limit)}
        """)
        rows = cur.fetchall()
        if not rows:
            return [], 0
        audience_uid_count = len({r[1] for r in rows if r[1]})
        return rows, audience_uid_count
    finally:
        try:
            conn.close()
        except Exception:
            pass


DEMOGRAPHIC_CATEGORIES = (
    "GENDER", "AGE", "INCOME", "ETHNICITY", "EDUCATION",
    "MARITAL_STATUS", "CHILDREN", "HOME_OWNER", "STATE", "DMA",
)
DEMOGRAPHICS_UID_CAP_PER_BUCKET = 30_000


def pull_demographics(
    *,
    ch_connect: Callable,
    uids_by_bucket: dict[str, set[str]],
) -> dict[str, dict[str, list[dict]]]:
    """For each sentiment bucket's UID set, pull demographic distributions
    from userdata.user_data_sanitized.

    Returns: {bucket: {category: [{value, count, pct}, ...]}}.

    Performance notes:
      * One ClickHouse query per bucket (was 10 per bucket - one per
        category). We arrayJoin all 10 demographic LowCardinality columns
        on a single user_data_sanitized scan so each bucket's join is
        evaluated exactly once. With three buckets that's 3 queries
        instead of 30 - a 10x reduction in round-trips and 10x fewer
        slots taken on the cross-tool ClickHouse semaphore.
      * user_data_sanitized is ORDER BY UID so `UID IN (uids)` uses the
        primary key directly.
      * Per-bucket UIDs are capped at DEMOGRAPHICS_UID_CAP_PER_BUCKET to
        keep each query body well under the 16 MiB max_query_size.
    """
    out: dict[str, dict[str, list[dict]]] = {b: {} for b in uids_by_bucket}
    bucket_payload: dict[str, list[str]] = {}
    # bucket -> scale factor to expand sampled counts up to full-bucket estimates.
    # When the bucket fits under the 30k cap this is 1.0; for larger buckets
    # the count we observe in the sample is multiplied by (full / sample)
    # before any gen-pop projection. Pcts use the unscaled sample counts
    # because within-bucket shares are sampling-invariant.
    bucket_scale: dict[str, float] = {}
    for bucket, uids in uids_by_bucket.items():
        if not uids:
            continue
        full_size = len(uids)
        sample = list(uids)[:DEMOGRAPHICS_UID_CAP_PER_BUCKET]
        bucket_payload[bucket] = sample
        sample_size = len(sample)
        bucket_scale[bucket] = (full_size / sample_size) if sample_size > 0 else 1.0
    if not bucket_payload:
        return out

    def _esc(u: str) -> str:
        return u.replace("'", "''")

    pairs_sql = ",\n                ".join(
        f"('{cat}', u.{cat})" for cat in DEMOGRAPHIC_CATEGORIES
    )

    conn = ch_connect(settings={
        "max_execution_time": 600,
        "use_skip_indexes": 1,
        "max_threads": 16,
    })
    try:
        cur = conn.cursor()
        for bucket, uid_list in bucket_payload.items():
            uid_lit = ", ".join("'" + _esc(u) + "'" for u in uid_list)
            try:
                cur.execute(f"""
                    SELECT
                        kv.1 AS category,
                        kv.2 AS value,
                        uniqExact(u.UID) AS cnt
                    FROM userdata.user_data_sanitized AS u
                    ARRAY JOIN [
                        {pairs_sql}
                    ] AS kv
                    WHERE u.UID IN ({uid_lit})
                      AND kv.2 != ''
                    GROUP BY category, value
                    ORDER BY category, cnt DESC
                """)
                rows = cur.fetchall()
            except Exception as e:
                print(f"[SentimentIQ] demographics arrayJoin path failed for bucket={bucket} ({e}); falling back to per-category scan.")
                rows = []
                for cat in DEMOGRAPHIC_CATEGORIES:
                    try:
                        cur.execute(f"""
                            SELECT '{cat}' AS category, {cat} AS value, uniqExact(UID) AS cnt
                            FROM userdata.user_data_sanitized
                            WHERE UID IN ({uid_lit}) AND {cat} != ''
                            GROUP BY {cat}
                            ORDER BY cnt DESC
                            LIMIT 100
                        """)
                        rows.extend(cur.fetchall())
                    except Exception as e2:
                        print(f"[SentimentIQ] demo fallback {bucket}/{cat} failed: {e2}")
            by_cat: dict[str, list[dict]] = {}
            for category, value, cnt in rows:
                by_cat.setdefault(category, []).append({"value": value, "count": int(cnt or 0)})
            scale = bucket_scale.get(bucket, 1.0)
            for category, items in by_cat.items():
                items.sort(key=lambda x: x["count"], reverse=True)
                total = sum(it["count"] for it in items) or 1
                out.setdefault(bucket, {})[category] = [
                    {"value": it["value"],
                     # Scale up to full-bucket estimate so the downstream
                     # gen-pop projection applies the same multiplier to
                     # every bucket regardless of sampling.
                     "count": int(round(it["count"] * scale)),
                     # Pct uses raw sample counts because within-bucket
                     # shares are sampling-invariant — scaling both
                     # numerator and denominator by `scale` is a no-op.
                     "pct": round(100.0 * it["count"] / total, 2)}
                    for it in items[:100]
                ]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


# ============================================================================
# ORCHESTRATION
# ============================================================================


def get_panel_size_for_window(
    *,
    ch_connect: Callable,
    start_date: str,
    end_date: str,
) -> int:
    """Distinct UIDs that fired any clickstream event in [start, end].

    This is the denominator we use for projecting Sentiment IQ panel
    counts to the US gen pop — mirrors `iq_rankers.get_panel_size_for_day`
    but spans the tracker's full date window (which may be one day for
    daily-cron runs or many months for backfills).

    Returns 0 on error so the projection layer falls back to raw counts
    rather than crashing the run.
    """
    conn = ch_connect(settings={
        "max_execution_time": 300,
        "use_skip_indexes": 1,
    })
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT uniqExact(UID) FROM clickstream.clickstream_final "
            f"WHERE DELIVERED BETWEEN toDate('{start_date}') AND toDate('{end_date}')"
        )
        row = cur.fetchone()
        return int((row or [0])[0] or 0)
    except Exception as e:
        print(f"[SentimentIQ] panel-size query failed for {start_date}..{end_date}: {e}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _project_result_in_place(
    result: dict,
    *,
    panel_size: int,
    us_population: int = US_POPULATION,
) -> None:
    """Mutate `result` so every panel-count field reflects the US gen pop.

    Mirrors the projection IQ Rankers applies at SQL read time
    (`round(raw * US_POPULATION / panel_size)`) but walks the in-memory
    payload because Sentiment IQ persists results to S3 as JSON and we
    want the stored numbers to already be gen-pop scaled (no double-work
    at read time, no drift if the panel grows between write and read).

    Fields touched:
      * audience_uid_count, total_events
      * kpis.{positive,negative,neutral}_events
      * kpis.unique_{positive,negative,neutral}_uids
      * time_series[].{positive, negative, neutral, total}
      * channels[].{total, positive, negative, neutral}
      * top_domains[].{total, positive, negative}
      * top_subreddits[].{total, positive, negative}
      * top_urls[].panel_visits
      * demographics[bucket][category][].count

    Fields explicitly NOT projected (already normalized or not panel sizes):
      * any *_share / pct / net field (already a ratio)
      * layer2_counts.* (count of URLs scored — not a population)
      * rollups (qualitative text)

    When panel_size is 0 (query failed) we leave counts raw and record
    projection_applied=False in result["meta"] so the UI can flag it.
    """
    meta = result.setdefault("meta", {})
    meta["panel_size"] = int(panel_size or 0)
    meta["us_population"] = int(us_population)

    if not panel_size or panel_size <= 0:
        meta["projection_applied"] = False
        meta["projection_multiplier"] = 0.0
        return

    multiplier = float(us_population) / float(panel_size)
    meta["projection_applied"] = True
    meta["projection_multiplier"] = round(multiplier, 4)

    def proj(n):
        try:
            return int(round(float(n or 0) * multiplier))
        except Exception:
            return n

    if "audience_uid_count" in result:
        result["audience_uid_count"] = proj(result.get("audience_uid_count"))
    if "total_events" in result:
        result["total_events"] = proj(result.get("total_events"))

    kpis = result.get("kpis")
    if isinstance(kpis, dict):
        for k in (
            "positive_events", "negative_events", "neutral_events",
            "unique_positive_uids", "unique_negative_uids", "unique_neutral_uids",
        ):
            if k in kpis:
                kpis[k] = proj(kpis[k])

    for row in (result.get("time_series") or []):
        for k in ("positive", "negative", "neutral", "total"):
            if k in row:
                row[k] = proj(row[k])

    for row in (result.get("channels") or []):
        for k in ("total", "positive", "negative", "neutral"):
            if k in row:
                row[k] = proj(row[k])

    for row in (result.get("top_domains") or []):
        for k in ("total", "positive", "negative"):
            if k in row:
                row[k] = proj(row[k])

    for row in (result.get("top_subreddits") or []):
        for k in ("total", "positive", "negative"):
            if k in row:
                row[k] = proj(row[k])

    for row in (result.get("top_urls") or []):
        if "panel_visits" in row:
            row["panel_visits"] = proj(row["panel_visits"])

    demo = result.get("demographics") or {}
    if isinstance(demo, dict):
        for bucket, cats in demo.items():
            if not isinstance(cats, dict):
                continue
            for category, rows in cats.items():
                for row in rows or []:
                    if "count" in row:
                        row["count"] = proj(row["count"])


def _default_date_window(cfg: dict) -> tuple[str, str]:
    """Resolve effective (start, end). Panel data for today hasn't landed
    yet during the daily cron window, so we cap end_date at yesterday.
    Backfills are detected by the presence of start_date / end_date in
    the config."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    start = cfg.get("start_date") or yesterday
    end = cfg.get("end_date") or yesterday
    return start, end


def run_sentiment_tracker(
    cfg: dict,
    *,
    ch_connect: Callable,
    s3_client,
    openai_client,
    status_cb: Callable[[str, int | None, str | None], None] | None = None,
    mode: str = "full",
) -> dict[str, Any]:
    """Run all three layers for a tracker and write the rolled-up result JSON.

    Args:
        cfg:        tracker config (as returned by make_tracker_config).
        mode:       'full'   - obey cfg's start_date/end_date (initial backfill
                               or admin re-run).
                    'daily'  - ignore cfg dates; pull only yesterday.

    Returns the dashboard payload that's also written to S3.
    """
    def _say(msg: str, progress: int | None = None, status: str | None = None) -> None:
        if status_cb:
            try:
                status_cb(status or "running", progress, msg)
            except Exception:
                pass
        print(f"[SentimentIQ {cfg.get('tracker_id','?')}] {msg}")

    tracker_id = cfg["tracker_id"]
    brand_terms = cfg.get("brand_terms") or []
    competitor_terms = cfg.get("competitor_terms") or []
    if not brand_terms:
        _say("no brand terms — aborting", 100, "failed")
        return {"error": "no brand terms"}

    if mode == "daily":
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        start_date, end_date = yesterday, yesterday
    else:
        start_date, end_date = _default_date_window(cfg)

    _say(f"Pulling clickstream events {start_date} → {end_date}", 8)
    rows, audience_uid_count = pull_events(
        ch_connect=ch_connect,
        brand_terms=brand_terms,
        start_date=start_date,
        end_date=end_date,
    )
    _say(f"{len(rows):,} events from {audience_uid_count:,} audience UIDs", 22)

    # Gen-pop projection denominator. We capture this once per run so the
    # entire result (KPIs, time-series, channels, demos, geos) is scaled
    # by the SAME multiplier — keeps internal ratios stable and matches
    # how iq_rankers does it. If the query fails we fall back to raw
    # counts and mark projection_applied=False in result["meta"].
    panel_size = get_panel_size_for_window(
        ch_connect=ch_connect,
        start_date=start_date,
        end_date=end_date,
    )
    if panel_size > 0:
        _say(f"Panel size for window: {panel_size:,} UIDs → "
             f"gen-pop multiplier {US_POPULATION / panel_size:.2f}x", 25)
    else:
        _say("Panel-size query returned 0 — counts will not be projected.", 25)

    if not rows:
        result = _empty_result_payload(cfg, start_date, end_date, audience_uid_count)
        _project_result_in_place(result, panel_size=panel_size)
        _persist_result(s3_client, tracker_id, result, start_date, end_date)
        _say("no rows — wrote empty result", 100, "completed")
        return result

    # ── Layer 1: behavioral scoring ──────────────────────────────────────────
    _say("Layer 1: behavioral scoring", 35)
    by_bucket_uids: dict[str, set[str]] = {"positive": set(), "negative": set(), "neutral": set()}
    by_channel_counts: dict[str, Counter] = defaultdict(Counter)  # channel -> Counter[bucket]
    by_domain_counts: Counter = Counter()
    by_domain_pos: Counter = Counter()
    by_domain_neg: Counter = Counter()
    by_subreddit_counts: Counter = Counter()
    by_subreddit_pos: Counter = Counter()
    by_subreddit_neg: Counter = Counter()
    by_day_counts: dict[str, Counter] = defaultdict(Counter)
    url_panel_counts: Counter = Counter()
    url_classifications: dict[str, str] = {}  # behavioral label cache

    composite_pos = 0
    composite_neg = 0
    composite_neu = 0

    for url, uid, common_name, delivered, domain in rows:
        scored = score_behavioral_event(url, common_name)
        bucket = scored["sentiment"]
        by_bucket_uids[bucket].add(uid)
        channel = scored["channel"]
        by_channel_counts[channel][bucket] += 1
        d = scored["domain"] or (domain or "").lower()
        if d:
            by_domain_counts[d] += 1
            if bucket == "positive":
                by_domain_pos[d] += 1
            elif bucket == "negative":
                by_domain_neg[d] += 1
        if scored["subreddit"]:
            sr = f"r/{scored['subreddit']}"
            by_subreddit_counts[sr] += 1
            if bucket == "positive":
                by_subreddit_pos[sr] += 1
            elif bucket == "negative":
                by_subreddit_neg[sr] += 1
        day_str = str(delivered)[:10] if delivered else ""
        if day_str:
            by_day_counts[day_str][bucket] += 1
        if bucket == "positive":
            composite_pos += 1
        elif bucket == "negative":
            composite_neg += 1
        else:
            composite_neu += 1
        url_panel_counts[url] += 1
        url_classifications[url] = bucket

    total_events = composite_pos + composite_neg + composite_neu
    net_sentiment = round(
        100.0 * (composite_pos - composite_neg) / max(total_events, 1),
        2,
    )

    # ── Layer 2: page-content sentiment for top URLs ─────────────────────────
    _say("Layer 2: page-content sentiment", 55)
    top_urls = [
        u for u, _ in url_panel_counts.most_common(MAX_PAGE_CLASSIFY_PER_RUN)
        # Skip search-engine result URLs; they have no stable content to score.
        if _channel_for_domain(_domain_of(u))[0] not in ("search", "ai")
    ][:MAX_PAGE_CLASSIFY_PER_RUN]
    url_layer2: dict[str, dict] = {}
    layer2_pos = 0
    layer2_neg = 0
    layer2_neu = 0
    for u in top_urls:
        c = classify_url_content(
            u,
            brand_terms=brand_terms,
            s3_client=s3_client,
            openai_client=openai_client,
        )
        if c:
            url_layer2[u] = c
            s = c.get("sentiment", "neutral")
            if s == "positive":
                layer2_pos += 1
            elif s == "negative":
                layer2_neg += 1
            else:
                layer2_neu += 1

    # ── Layer 3: web-search rollups (LLM) ────────────────────────────────────
    _say("Layer 3: LLM web-search rollups", 72)
    rollups = build_web_rollups(
        brand_terms=brand_terms,
        competitor_terms=competitor_terms,
        s3_client=s3_client,
        openai_client=openai_client,
        tracker_id=tracker_id,
    )

    # ── Demographics ────────────────────────────────────────────────────────
    _say("Joining demographics", 85)
    try:
        demographics = pull_demographics(
            ch_connect=ch_connect,
            uids_by_bucket={
                "all":      by_bucket_uids["positive"] | by_bucket_uids["negative"] | by_bucket_uids["neutral"],
                "positive": by_bucket_uids["positive"],
                "negative": by_bucket_uids["negative"],
                "neutral":  by_bucket_uids["neutral"],
            },
        )
    except Exception as e:
        traceback.print_exc()
        demographics = {"all": {}, "positive": {}, "negative": {}, "neutral": {}}
        rollups.setdefault("warnings", []).append(f"demographics_failed: {e}")

    # ── Build dashboard payload ──────────────────────────────────────────────
    channel_breakdown = []
    for ch, ctr in by_channel_counts.items():
        tot = sum(ctr.values())
        channel_breakdown.append({
            "channel": ch,
            "total": tot,
            "positive": ctr.get("positive", 0),
            "negative": ctr.get("negative", 0),
            "neutral":  ctr.get("neutral", 0),
            "net": round(100.0 * (ctr.get("positive", 0) - ctr.get("negative", 0)) / max(tot, 1), 2),
        })
    channel_breakdown.sort(key=lambda r: -r["total"])

    top_domains = []
    for d, n in by_domain_counts.most_common(40):
        ch, _ = _channel_for_domain(d)
        top_domains.append({
            "domain": d, "channel": ch, "total": n,
            "positive": by_domain_pos.get(d, 0),
            "negative": by_domain_neg.get(d, 0),
            "net": round(100.0 * (by_domain_pos.get(d, 0) - by_domain_neg.get(d, 0)) / max(n, 1), 2),
        })

    top_subreddits = []
    for sr, n in by_subreddit_counts.most_common(20):
        top_subreddits.append({
            "subreddit": sr, "total": n,
            "positive": by_subreddit_pos.get(sr, 0),
            "negative": by_subreddit_neg.get(sr, 0),
            "net": round(100.0 * (by_subreddit_pos.get(sr, 0) - by_subreddit_neg.get(sr, 0)) / max(n, 1), 2),
        })

    time_series = []
    for day in sorted(by_day_counts.keys()):
        ctr = by_day_counts[day]
        tot = sum(ctr.values())
        time_series.append({
            "date": day,
            "positive": ctr.get("positive", 0),
            "negative": ctr.get("negative", 0),
            "neutral":  ctr.get("neutral", 0),
            "total": tot,
            "net": round(100.0 * (ctr.get("positive", 0) - ctr.get("negative", 0)) / max(tot, 1), 2),
        })

    top_urls_payload = []
    for u, n in url_panel_counts.most_common(80):
        layer2 = url_layer2.get(u) or {}
        top_urls_payload.append({
            "url": u,
            "domain": _domain_of(u),
            "channel": _channel_for_domain(_domain_of(u))[0],
            "panel_visits": n,
            "behavioral_sentiment": url_classifications.get(u, "neutral"),
            "content_sentiment": layer2.get("sentiment"),
            "title": (layer2.get("og_title") or layer2.get("title") or "")[:240],
            "snippet": (layer2.get("og_description") or layer2.get("description") or "")[:360],
            "rationale": layer2.get("rationale"),
        })

    result = {
        "tracker_id": tracker_id,
        "project_name": cfg.get("project_name", ""),
        "owner": cfg.get("owner", ""),
        "brand_terms": brand_terms,
        "competitor_terms": competitor_terms,
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "audience_uid_count": audience_uid_count,
        "total_events": total_events,
        "kpis": {
            "net_sentiment": net_sentiment,
            "positive_events": composite_pos,
            "negative_events": composite_neg,
            "neutral_events": composite_neu,
            "positive_share": round(100.0 * composite_pos / max(total_events, 1), 2),
            "negative_share": round(100.0 * composite_neg / max(total_events, 1), 2),
            "neutral_share":  round(100.0 * composite_neu / max(total_events, 1), 2),
            "unique_positive_uids": len(by_bucket_uids["positive"]),
            "unique_negative_uids": len(by_bucket_uids["negative"]),
            "unique_neutral_uids":  len(by_bucket_uids["neutral"]),
        },
        "layer2_counts": {
            "urls_classified": len(url_layer2),
            "positive": layer2_pos, "negative": layer2_neg, "neutral": layer2_neu,
        },
        "time_series": time_series,
        "channels": channel_breakdown,
        "top_domains": top_domains,
        "top_subreddits": top_subreddits,
        "top_urls": top_urls_payload,
        "demographics": demographics,
        "rollups": rollups,
    }

    _project_result_in_place(result, panel_size=panel_size)

    _persist_result(s3_client, tracker_id, result, start_date, end_date)
    _say(f"Done. Net Sentiment {net_sentiment:+.1f} across {total_events:,} events "
         f"(projected to US gen pop, panel_size={panel_size:,}).",
         100, "completed")
    return result


def _empty_result_payload(cfg, start_date, end_date, audience_uid_count):
    return {
        "tracker_id": cfg["tracker_id"],
        "project_name": cfg.get("project_name", ""),
        "owner": cfg.get("owner", ""),
        "brand_terms": cfg.get("brand_terms", []),
        "competitor_terms": cfg.get("competitor_terms", []),
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "audience_uid_count": audience_uid_count,
        "total_events": 0,
        "kpis": {"net_sentiment": 0.0,
                 "positive_events": 0, "negative_events": 0, "neutral_events": 0,
                 "positive_share": 0.0, "negative_share": 0.0, "neutral_share": 0.0,
                 "unique_positive_uids": 0, "unique_negative_uids": 0, "unique_neutral_uids": 0},
        "layer2_counts": {"urls_classified": 0, "positive": 0, "negative": 0, "neutral": 0},
        "time_series": [], "channels": [], "top_domains": [], "top_subreddits": [],
        "top_urls": [],
        "demographics": {"all": {}, "positive": {}, "negative": {}, "neutral": {}},
        "rollups": {"positive": {"summary": "", "themes": [], "quotes": []},
                    "negative": {"summary": "", "themes": [], "quotes": []},
                    "neutral":  {"summary": "", "themes": [], "quotes": []}},
        "empty": True,
    }


def _persist_result(s3_client, tracker_id, result, start_date, end_date):
    s3_put_json(s3_client, latest_result_key(tracker_id), result)
    # Daily snapshot keyed by end_date so historic time-series can be
    # rebuilt by listing the daily/ prefix.
    s3_put_json(s3_client, daily_result_key(tracker_id, end_date), result)


# ============================================================================
# DAILY APPEND (for the cron path)
# ============================================================================


def append_daily_to_latest(
    *,
    tracker_id: str,
    new_day_result: dict,
    s3_client,
) -> dict:
    """Merge yesterday's freshly computed result into latest.json so the
    dashboard's time series grows continuously across cron runs.

    Projection note: `new_day_result` is already gen-pop projected by
    run_sentiment_tracker. If `latest` predates the projection change
    (no meta.projection_applied) the historical time_series rows are
    raw panel counts, while the newly-appended rows are projected — the
    units don't match until the next full refresh rewrites everything.
    We flag this in result["meta"]["projection_inconsistent_history"]
    so the dashboard can surface a one-time banner.
    """
    latest = s3_get_json(s3_client, latest_result_key(tracker_id)) or {}
    if not latest or latest.get("empty"):
        s3_put_json(s3_client, latest_result_key(tracker_id), new_day_result)
        return new_day_result

    ts = list(latest.get("time_series") or [])
    new_ts = list(new_day_result.get("time_series") or [])
    if new_ts:
        # Remove any same-date rows so the cron is idempotent within one day.
        existing_dates = {row["date"] for row in ts}
        for row in new_ts:
            if row["date"] in existing_dates:
                ts = [r for r in ts if r["date"] != row["date"]]
        ts.extend(new_ts)
    ts.sort(key=lambda r: r.get("date", ""))
    latest["time_series"] = ts

    # Always overwrite the qualitative pieces and KPIs with the new day's
    # rollups + recomputed top-URLs (these aren't additive — they reflect
    # the latest snapshot, not cumulative history).
    for k in ("channels", "top_domains", "top_subreddits", "top_urls",
              "demographics", "rollups", "layer2_counts"):
        if k in new_day_result:
            latest[k] = new_day_result[k]

    # Recompute headline KPIs from the cumulative time series so the
    # Overview tab matches the chart.
    pos = sum(r.get("positive", 0) for r in ts)
    neg = sum(r.get("negative", 0) for r in ts)
    neu = sum(r.get("neutral", 0) for r in ts)
    tot = pos + neg + neu
    latest.setdefault("kpis", {})
    latest["kpis"].update({
        "positive_events": pos,
        "negative_events": neg,
        "neutral_events":  neu,
        "positive_share": round(100.0 * pos / max(tot, 1), 2),
        "negative_share": round(100.0 * neg / max(tot, 1), 2),
        "neutral_share":  round(100.0 * neu / max(tot, 1), 2),
        "net_sentiment":  round(100.0 * (pos - neg) / max(tot, 1), 2),
    })
    latest["total_events"] = tot
    latest["generated_at"] = datetime.utcnow().isoformat() + "Z"
    latest["end_date"] = new_day_result.get("end_date") or latest.get("end_date")

    # Carry the projection meta from the freshly-projected new day so the
    # dashboard knows the units of the merged payload. Flag a transitional
    # state when historical rows pre-date the projection.
    new_meta = (new_day_result.get("meta") or {})
    latest_meta = latest.setdefault("meta", {})
    if new_meta:
        had_projection = bool(latest_meta.get("projection_applied"))
        latest_meta.update(new_meta)
        if not had_projection and new_meta.get("projection_applied"):
            latest_meta["projection_inconsistent_history"] = True

    s3_put_json(s3_client, latest_result_key(tracker_id), latest)
    return latest


# ============================================================================
# ALERTS
# ============================================================================


def compute_alert_signal(
    prev_result: dict | None,
    new_result: dict,
    *,
    drop_pct: float,
    spike_pct: float,
) -> dict | None:
    """Detect material sentiment shifts day-over-day.

    Returns an alert dict if either:
      - positive share drops by >= drop_pct absolute percentage points, OR
      - negative event volume spikes by >= spike_pct percent over yesterday's.
    Otherwise returns None.
    """
    if not prev_result:
        return None
    pk = (prev_result or {}).get("kpis") or {}
    nk = (new_result or {}).get("kpis") or {}
    prev_pos = float(pk.get("positive_share", 0.0))
    new_pos = float(nk.get("positive_share", 0.0))
    prev_neg_events = float(pk.get("negative_events", 0.0))
    new_neg_events = float(nk.get("negative_events", 0.0))

    pos_drop = prev_pos - new_pos
    neg_spike_pct = 0.0
    if prev_neg_events > 0:
        neg_spike_pct = 100.0 * (new_neg_events - prev_neg_events) / prev_neg_events
    elif new_neg_events > 0:
        neg_spike_pct = 999.0  # zero baseline -> treat as a spike if anything new

    fired = []
    if pos_drop >= drop_pct:
        fired.append({
            "kind": "positive_drop",
            "delta": round(pos_drop, 2),
            "message": f"Positive sentiment share dropped {pos_drop:.1f} pts "
                       f"({prev_pos:.1f}% → {new_pos:.1f}%).",
        })
    if neg_spike_pct >= spike_pct:
        fired.append({
            "kind": "negative_spike",
            "delta_pct": round(neg_spike_pct, 1),
            "message": f"Negative event volume up {neg_spike_pct:.0f}% "
                       f"({int(prev_neg_events):,} → {int(new_neg_events):,}).",
        })
    if not fired:
        return None
    return {
        "tracker_id": new_result.get("tracker_id"),
        "project_name": new_result.get("project_name"),
        "fired_at": datetime.utcnow().isoformat() + "Z",
        "signals": fired,
        "net_sentiment_prev": pk.get("net_sentiment"),
        "net_sentiment_new": nk.get("net_sentiment"),
    }


def render_alert_email_html(alert: dict, dashboard_url: str = "") -> str:
    """Render the dashboard-styled alert email body."""
    rows = "".join(
        f'<div style="margin:10px 0;padding:10px 14px;background:#132f4c;border-left:4px solid '
        f'{"#ef4444" if s.get("kind") == "negative_spike" else "#f59e0b"};border-radius:6px;">'
        f'<div style="font-size:12px;color:#8892b0;text-transform:uppercase;letter-spacing:.05em;">{s.get("kind","").replace("_"," ")}</div>'
        f'<div style="font-size:14px;color:#e6f1ff;margin-top:4px;">{s.get("message","")}</div>'
        f'</div>'
        for s in alert.get("signals", [])
    )
    btn = (
        f'<a href="{dashboard_url}" style="display:inline-block;background:linear-gradient(135deg,#66d9ef,#5a9ad9);'
        f'color:#0a1929;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;margin-top:16px;">'
        f'Open Sentiment IQ</a>'
        if dashboard_url else ""
    )
    return (
        f'<p>Sentiment IQ detected a material shift for '
        f'<strong>{alert.get("project_name","")}</strong>.</p>'
        f'{rows}'
        f'<p style="margin-top:18px;color:#8892b0;font-size:12px;">'
        f'Net Sentiment: {alert.get("net_sentiment_prev")} → {alert.get("net_sentiment_new")}'
        f'</p>'
        f'{btn}'
    )
