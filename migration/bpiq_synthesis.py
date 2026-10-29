"""Brand Partnership IQ payload synthesis for Prometheus.

Jenna 2026-09-16: "make sure that prometheus is properly wired to pull
data that would end up in the brand partnership tab ... a chip would
need to say Pull Brand Partnership Valuation then when clicked it would
ask you for the input needed to synthesize the data based on real world
research and high level reasoning to give the proper output then it
would run and display in the dashboard."

This module is the generation engine behind that chip. It mirrors how
the valuation toolkit thread built fresh reads (Glen Powell x RAM,
Penelope Cruz x CHANEL, Zoe Kravitz x YSL): the caller supplies the
partnership inputs, ONE research-enabled reasoning call returns the
primitives (audience scale, per-platform penetrations, demographics,
sentiment, conversions, rate card), and the code derives every
downstream number deterministically so the payload is coherent by
construction - lifts, projections, control drift, EMV breakdown, and
the four-part valuation - with messy count hygiene throughout.

Inputs (collected by the Prometheus flow):
    brand_partner   required  e.g. "RAM Trucks"
    qualifier       required  the talent / show / property, e.g.
                              "Glen Powell"
    event_start     required  YYYY-MM-DD (campaign / endorsement start)
    event_end       required  YYYY-MM-DD
    pre_start/end   optional  default: the 365 days before event_start
    post_start/end  optional  default: event_end+1 through today
    audience        optional  audience descriptor ("show viewers",
                              "ticket purchasers", ...) for research
    rates           optional  {bev_per_user, blv_per_incr_user,
                              conv_value_per_user} overrides

Output: a full Brand Partnership IQ payload (same schema as every
shipped file under brand-partnership-iq/), written to S3 and enriched
with the metadata sidecar so it renders in the dashboard immediately.

One run per ask (Jenna 2026-10-28, Jennifer Beals x Ganni x Lululemon
built twice with different numbers: "that can never happen"):

  * `run_key(inputs)` is the valuation's identity: the normalized
    talent, campaign, campaign start, campaign end and control-group
    flag. Spelling, spacing and case do not matter ("lulu lemon" and
    "lululemon" are one key).
  * The run registry (`system/pm_bpiq_runs/<key>.json`) holds the one
    job that owns a key. The wizard claims the key the moment Accept
    is tapped, before the charge; a second Accept, a re-walk of the
    wizard, or any other caller finds the claim and reuses that job.
    The runner checks the registry again before it builds.
  * Every salted draw in the engine is seeded by the key, and the
    research primitives plus the campaign anchors are saved per key
    (`system/bpiq_underpinnings/<key>.json`). A repeat run reads them
    back instead of re-drawing, so the same subject and window always
    produce the same numbers.

The price (same day): the conversion value used to multiply converters
by a flat $30 for every brand. Now one research pass per campaign
reads the drop, where it sold, the products and their price points,
the sell-out and restock signal, and the talent's role; the average
order value is derived from that price mix (a basket of 1.2 to 1.6
pieces at the collection's weighted price, never below its cheapest
hero piece). With no confident prices the category band applies, a
salted draw inside it, never a constant. A sell-out is read as demand:
it raises the conversion rate and compresses the window. The anchors
stay internal; the deck and the read state the price band and the
sell-out in plain words with no source named.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import time
from typing import Callable, Optional

try:
    from migration.bpiq_subset_cut import validate_bpiq_payload
except ImportError:  # pragma: no cover - twin-path import
    try:
        from bpiq_subset_cut import validate_bpiq_payload  # type: ignore
    except ImportError:
        validate_bpiq_payload = None  # type: ignore

try:
    from migration import bpiq_matched_control as _mc
except ImportError:  # pragma: no cover - twin-path import
    import bpiq_matched_control as _mc  # type: ignore

S3_PREFIX = "brand-partnership-iq/"
PANEL_WEIGHT_DEFAULT = 32.99  # 329.9M US / 10M panel

PLATFORMS = [
    "TikTok", "Instagram", "Facebook", "YouTube", "X (Twitter)",
    "Snapchat", "Reddit", "Pinterest", "LinkedIn", "Threads",
    "Twitch", "Direct (Brand Site)",
]

# Earned-media rack rates ($ per incremental projected consumer), the
# same card the shipped payloads carry.
EMV_RATES = {
    "TikTok": 7.0, "Instagram": 11.0, "YouTube": 3.5, "Facebook": 14.0,
    "X": 6.0, "Twitter": 6.0, "X (Twitter)": 6.0, "Reddit": 4.0,
    "Pinterest": 6.0, "Snapchat": 5.0, "LinkedIn": 25.0,
    "Direct (Brand Site)": 5.0, "Threads": 10.0, "Twitch": 15.0,
    # earned lines of the campaign itself (2026-10-28): a reader reached
    # through the press, a person reached around a launch event
    "Press": 4.0, "Launch event": 5.0,
}
DEFAULT_RATES = {
    "bev_per_user": 2.5,
    "blv_per_incr_user": 5.0,
}
# The conversion value per converter is never a constant (2026-10-28).
# It is derived per campaign from the researched price mix, or drawn
# inside the category band below. Kept only as the last-resort band
# when the category itself is unknown.
AOV_FALLBACK_BAND = (28.0, 95.0)

BPIQ_CATEGORIES = ["BEAUTY", "AUTOMOTIVE", "FASHION", "CPG",
                   "TECHNOLOGY", "DINING", "ENTERTAINMENT", "SPORTS"]

# Average order value bands by category (USD), used only when the
# campaign research returned no confident price points. The draw lands
# in the middle sixty percent of the band, salted by the run key.
# FASHION reads as premium apparel and footwear sold direct.
AOV_BANDS = {
    "FASHION": (120.0, 260.0),
    "BEAUTY": (45.0, 110.0),
    "CPG": (16.0, 48.0),
    "TECHNOLOGY": (140.0, 620.0),
    "DINING": (14.0, 38.0),
    "ENTERTAINMENT": (22.0, 85.0),
    "SPORTS": (38.0, 140.0),
    "AUTOMOTIVE": (60.0, 240.0),
}
BASKET_RANGE = (1.2, 1.6)          # pieces per order on a collection drop

RUNS_PREFIX = "system/pm_bpiq_runs/"
UNDERPINNINGS_PREFIX = "system/bpiq_underpinnings/"
RUN_STALE_AFTER_S = 45 * 60        # an unfinished claim older than this is reclaimable
LIVE_STATUSES = ("queued", "running", "done")


# ---------------------------------------------------------------------------
# Identity: one run per ask
# ---------------------------------------------------------------------------

def _norm_token(s) -> str:
    """Case, spacing and punctuation insensitive form of a name."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def run_key(inputs: dict) -> str:
    """The valuation's identity: normalized talent, campaign (or the
    brand when no campaign was named), campaign start, campaign end
    and the control-group flag. 16 hex characters."""
    d = inputs or {}
    talent = _norm_token(d.get("qualifier") or d.get("talent"))
    campaign = _norm_token(d.get("campaign")) or _norm_token(d.get("brand_partner"))
    start = str(d.get("event_start") or d.get("campaign_start") or "")[:10]
    end = str(d.get("event_end") or d.get("campaign_end") or "")[:10]
    if "include_control" in d:
        control = bool(d.get("include_control"))
    elif "control_group" in d and not isinstance(d.get("control_group"), dict):
        control = bool(d.get("control_group"))
    else:
        control = True
    raw = "|".join([talent, campaign, start, end, "c1" if control else "c0"])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def run_salt(inputs: dict) -> str:
    """The seed every salted draw in the engine uses, so the same ask
    (any spelling) always draws the same values."""
    return f"bpiq|{run_key(inputs)}"


def registry_key(key: str) -> str:
    return f"{RUNS_PREFIX}{key}.json"


def underpinnings_key(key: str) -> str:
    return f"{UNDERPINNINGS_PREFIX}{key}.json"


def _now() -> float:
    return time.time()


def run_is_live(doc, now=None) -> bool:
    """True when a registry document owns its key: finished with a
    file, or queued / running and not stale."""
    if not isinstance(doc, dict) or not doc.get("job_id"):
        return False
    status = str(doc.get("status") or "")
    if status == "done":
        return bool(doc.get("s3_key"))
    if status in ("queued", "running"):
        try:
            last = float(doc.get("updated_at") or doc.get("claimed_at") or 0)
        except (TypeError, ValueError):
            last = 0.0
        return last > 0 and ((now or _now()) - last) < RUN_STALE_AFTER_S
    return False


class MemoryRegistry:
    """In-process run registry for tests and as a fail-safe."""

    def __init__(self):
        self.rows: dict = {}

    def load(self, key):
        d = self.rows.get(key)
        return dict(d) if isinstance(d, dict) else None

    def put(self, key, doc, if_none_match=False):
        if if_none_match and key in self.rows:
            return False
        self.rows[key] = dict(doc)
        return True

    def delete(self, key):
        self.rows.pop(key, None)
        return True


class S3Registry:
    """The shared run registry on S3. Claims use a conditional put
    (If-None-Match) so two workers cannot both own a key; where the
    client is too old for it, write-then-verify."""

    def __init__(self, s3, bucket):
        self.s3 = s3
        self.bucket = bucket

    def load(self, key):
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=registry_key(key))
            doc = json.loads(obj["Body"].read().decode("utf-8"))
            return doc if isinstance(doc, dict) else None
        except Exception:
            return None

    def put(self, key, doc, if_none_match=False):
        body = json.dumps(doc, default=str).encode("utf-8")
        kw = dict(Bucket=self.bucket, Key=registry_key(key), Body=body,
                  ContentType="application/json")
        if if_none_match:
            try:
                self.s3.put_object(IfNoneMatch="*", **kw)
                return True
            except Exception as e:
                msg = str(e)
                if "PreconditionFailed" in msg or "412" in msg:
                    return False
                if "IfNoneMatch" not in msg and "Unknown parameter" not in msg:
                    return False
                # old client: write, then verify the owner
                try:
                    self.s3.put_object(**kw)
                    time.sleep(0.8)
                    again = self.load(key)
                    return bool(again and again.get("job_id") == doc.get("job_id"))
                except Exception:
                    return False
        try:
            self.s3.put_object(**kw)
            return True
        except Exception:
            return False

    def delete(self, key):
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=registry_key(key))
        except Exception:
            pass
        return True


def registry_for(host=None, s3=None, bucket=None):
    """The shared S3 registry when a client is at hand (every live
    host binds one). Without a client (tests, a bare import) a fresh
    in-process registry: no cross-call memory, so one test's claim
    never leaks into the next. Never raises."""
    def _cap(obj, name):
        # prometheus.host raises HostNotBound (a RuntimeError) for a
        # missing capability and the legacy proxy does the same for a
        # missing attribute, so a plain getattr default never runs.
        try:
            checker = getattr(obj, "has", None)
        except Exception:
            checker = None
        try:
            if callable(checker) and not checker(name):
                return None
        except Exception:
            pass
        try:
            return getattr(obj, name)
        except Exception:
            return None

    try:
        if s3 is None and host is not None:
            s3 = _cap(host, "s3_client")
            if bucket is None:
                bucket = _cap(host, "bucket") or _cap(host, "S3_BUCKET")
        if s3 is not None:
            return S3Registry(s3, bucket or "dashboard-inputs")
    except Exception:
        pass
    return MemoryRegistry()


def claim_run(registry, inputs: dict, job_id: str, user: str = "",
              thread_id: str = "", now=None) -> tuple:
    """Own the key for `job_id`, or hand back the live run that already
    owns it. Returns (doc, created). `created` is False when another job
    owns the key (its document is returned) and True when this job now
    does. A stale or failed claim is taken over."""
    key = run_key(inputs)
    now = now or _now()
    existing = registry.load(key)
    if existing and run_is_live(existing, now) and existing.get("job_id") != job_id:
        return existing, False
    if existing and existing.get("job_id") == job_id:
        return existing, True
    doc = {
        "run_key": key, "job_id": str(job_id), "user": str(user or ""),
        "thread_id": str(thread_id or ""), "status": "queued",
        "inputs": {k: inputs.get(k) for k in (
            "qualifier", "brand_partner", "campaign", "event_start", "event_end",
            "post_start", "post_end", "control_group", "include_control") if k in inputs},
        "claimed_at": now, "updated_at": now,
    }
    if existing is None:
        if registry.put(key, doc, if_none_match=True):
            return doc, True
        again = registry.load(key)
        if again and run_is_live(again, now) and again.get("job_id") != job_id:
            return again, False
    # stale / failed owner (or a lost race on an old client): take over
    registry.put(key, doc)
    return doc, True


def touch_run(registry, inputs: dict, job_id: str, status: str = "running", **fields):
    """Move the key's document to `status` for `job_id` (heartbeat for
    the staleness read). Never raises."""
    try:
        key = run_key(inputs)
        doc = registry.load(key) or {"run_key": key, "job_id": str(job_id)}
        if doc.get("job_id") not in (None, "", str(job_id)) and run_is_live(doc) \
                and str(doc.get("status")) == "done":
            return doc     # a finished run is never overwritten by a late duplicate
        doc.update({"job_id": str(job_id), "status": status, "updated_at": _now()})
        doc.update({k: v for k, v in fields.items() if v is not None})
        registry.put(key, doc)
        return doc
    except Exception:
        return None


def finish_run(registry, inputs: dict, job_id: str, *, s3_key: str,
               project_name: str = "", total_brand_value=None):
    return touch_run(registry, inputs, job_id, "done", s3_key=s3_key,
                     project_name=project_name, total_brand_value=total_brand_value,
                     finished_at=_now())


def fail_run(registry, inputs: dict, job_id: str, reason: str = ""):
    return touch_run(registry, inputs, job_id, "error", error=str(reason or "")[:200],
                     finished_at=_now())


def release_run(registry, inputs: dict, job_id: str):
    """Drop a claim this job made and did not use (the charge did not
    go through). Only the owner's claim is removed."""
    try:
        key = run_key(inputs)
        doc = registry.load(key)
        if doc and doc.get("job_id") == str(job_id) and str(doc.get("status")) != "done":
            registry.delete(key)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Display casing (the wizard keeps what the user typed; the file shows
# the names as they are known)
# ---------------------------------------------------------------------------

_SMALL_WORDS = {"x", "and", "of", "the", "by", "for", "with", "a", "an", "de", "la"}


def _title_words(s: str) -> str:
    out = []
    for i, w in enumerate(str(s or "").split()):
        lw = w.lower()
        if lw in _SMALL_WORDS and i > 0:
            out.append("x" if lw == "x" else lw)
        elif lw == "x":
            out.append("x")
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def canonical_name(name: str, lookup: Optional[Callable] = None) -> str:
    """A name typed in all lower case takes its known casing: the
    catalog's spelling when `lookup(normalized) -> str` knows it, else
    title case. A name that already carries capitals is kept."""
    s = " ".join(str(name or "").split())
    if not s or any(c.isupper() for c in s):
        return s
    parts = re.split(r"\s+[xX\u00d7]\s+", s)
    fixed = []
    for p in parts:
        hit = None
        if callable(lookup):
            try:
                hit = lookup(_norm_token(p))
            except Exception:
                hit = None
        fixed.append(str(hit).strip() if hit else _title_words(p))
    return " x ".join(fixed)


def catalog_lookup():
    """normalized -> display name from the corpus catalog, or None."""
    try:
        from migration import corpus_catalog as _cc
        idx = _cc.load_index() or {}
        subs = idx.get("subjects") or {}
        table = {}
        for skey, ent in subs.items():
            if not isinstance(ent, dict):
                continue
            disp = str(ent.get("subject") or "").strip()
            if not disp:
                continue
            table.setdefault(_norm_token(disp), disp)
            table.setdefault(_norm_token(skey), disp)
            for a in ent.get("aliases") or []:
                table.setdefault(_norm_token(a), disp)

        def _lk(norm):
            return table.get(norm)
        return _lk
    except Exception:
        return None


def canonical_inputs(inputs: dict, lookup: Optional[Callable] = None) -> dict:
    """Return inputs with qualifier, brand_partner and campaign in
    their known casing. Keys and dates are untouched."""
    d = dict(inputs or {})
    for k in ("qualifier", "brand_partner", "campaign"):
        if d.get(k):
            d[k] = canonical_name(d[k], lookup)
    return d


# ---------------------------------------------------------------------------
# Campaign research: the price, the drop, the sell-out
# ---------------------------------------------------------------------------

CAMPAIGN_RESEARCH_PROMPT = """You research one brand partnership campaign
before it is valued. Use web search when available: the brands' own
shop and press pages first, then trade and business coverage. Return
STRICT JSON only:

{
  "campaign_name": str,
  "brand": str,                    // the brand being valued
  "talent": str,
  "drop_dates": ["YYYY-MM-DD", ...],   // announce / launch / drop dates
  "campaign_window_note": str,     // how the stated window matches the drop
  "retail": {"partners": [str, ...],   // who sold it (the brand, a partner,
             "sold_at": [str, ...]},   // retailers) and where (sites, stores)
  "products": [                    // the pieces, from the shop pages or
    {"name": str, "type": str,     // coverage; price in USD as listed
     "price_usd": float|null, "hero": bool},   // hero = a lead piece of the
    ...                                          // collection (the campaign
  ],                                             // imagery, the press lead)
  "price_band": {"low_usd": float|null, "high_usd": float|null},
  "collection_size": int|null,     // number of pieces, when stated
  "sell_out": {"sold_out": bool, "days_to_sell_out": float|null,
               "partial": bool, "evidence": str},   // sold-out pages, press
  "restock": {"announced": bool, "note": str},
  "inventory_signal": "small"|"medium"|"large"|"unknown",
  "inventory_note": str,           // limited run, wide release, stores
  "talent_role": {"role": str, "assets": [str, ...], "channels": [str, ...]},
  "footprint": {"press_pieces": int|null, "social_note": str},
  "category": str,                 // one of %s
  "aov_hint_usd": float|null,      // your read of a typical order, if
                                   // no prices were readable
  "confidence": "high"|"medium"|"low",
  "sources": [{"title": str, "url": str, "used_for": str}, ...]
}

Rules: prices come from a shop page or a report that names them; never
invent a price. A piece whose price you did not see carries null. A
sell-out needs evidence (sold-out product pages, the brand saying so,
coverage); without it sold_out is false. Say when inventory was small
(a capsule or limited run) so demand is not overstated. Prefer the US
storefront and USD."""


def campaign_prompt() -> str:
    return CAMPAIGN_RESEARCH_PROMPT % ", ".join(BPIQ_CATEGORIES)


def _hints_block(inputs: dict) -> dict:
    """Facts an operator read by hand (page, count, date) ride every
    research ask as verified facts: included, never contradicted, and
    built on. They are not part of the run key."""
    hints = inputs.get("research_hints")
    if not hints:
        return {}
    facts = [str(h)[:400] for h in (hints if isinstance(hints, (list, tuple)) else [hints]) if str(h or "").strip()][:40]
    if not facts:
        return {}
    return {"verified_facts": facts,
            "verified_facts_rule": ("Read by hand on the pages named. Include them as they are, never "
                                    "contradict them, and add what you find beyond them.")}


# The first pass reads the collection; a sell-out is easy to miss in it
# (2026-10-28: the Ganni x lululemon drop sold out on lululemon.com the
# day it launched and the first pass came back "sold_out: false"). The
# second pass searches for exactly that and nothing else.
SELLOUT_CHECK_PROMPT = """You check one question about a brand campaign:
did the collection sell out, and how fast? Search for it directly
(web search when available): "<campaign> sold out", "<campaign> sell
out", "<campaign> restock", "<campaign> low stock", the brand's own
product pages (sold-out sizes, waitlist buttons), shopping coverage
the week of the drop. Return STRICT JSON only:

{
  "sold_out": bool,                // true only with evidence
  "days_to_sell_out": float|null,  // from the drop; minutes or hours
                                   // on launch day count as 1 or less
  "partial": bool,                 // sold out on one storefront or in
                                   // most styles while others remained
  "where": str,                    // the storefront(s) that sold out
  "evidence": str,                 // one plain sentence of what you saw
  "restock": {"announced": bool, "note": str},
  "inventory_signal": "small"|"medium"|"large"|"unknown",
  "price_band": {"low_usd": float|null, "high_usd": float|null},
                                   // only when a page or report lists
                                   // the range; else nulls
  "sources": [{"title": str, "url": str, "used_for": str}, ...]
}

Never invent evidence; with none, sold_out is false and evidence is
an empty string."""


# The campaign's footprint (Jenna 2026-10-28: "it should look at all
# press, the fact that there was a huge nyc party, the virality of the
# social media post and commercial"). Press, events, the film, the
# viral posts, search, the brand site and the inventory are standard
# inputs of every campaign read; the reach the valuation runs on is
# built from them (migration/bpiq_campaign.py).
CAMPAIGN_FOOTPRINT_PROMPT = """You map the footprint of one brand campaign
in its window: every press piece, every event, the campaign film, the
posts that travelled, search and the brand site. Use web search when
available; read the platforms' own pages for counts. Return STRICT
JSON only:

{
  "press": [                       // EVERY piece you can find, launch
    {"outlet": str, "title": str, "url": str, "date": "YYYY-MM-DD",
     "country": str,               // US, UK, ES, ... (edition)
     "outlet_us_monthly_uniques": int|null,   // the outlet's US monthly
                                              // readers when published
     "article_reads_us": int|null, // only when a figure exists
     "leads_with_talent": bool},   // the talent is the headline hook
    ...
  ],
  "press_pieces_total": int,       // pieces seen including syndication
                                   // and pickups not listed above
  "events": [                      // launch parties, pop-ups, dance
    {"name": str, "date": "YYYY-MM-DD", "place": str, "host": str,
     "guests": [str, ...],         // named guests with followings
     "coverage": [str, ...],       // outlets and photo agencies that
                                   // carried the night
     "attendee_followers_total": int|null,  // sum of the named guests'
                                             // followers when readable
     "social_posts": int|null, "reach_us": int|null,
     "talent_attended": bool}, ...
  ],
  "commercial": {"title": str,
    "platforms": [{"platform": str, "account": str, "url": str,
                   "views": int|null, "likes": int|null, "comments": int|null,
                   "shares": int|null, "followers": int|null, "as_of": "YYYY-MM-DD"}, ...],
    "views_total": int|null},
  "viral_posts": [                 // the posts that travelled, on the
    {"platform": str, "account": str, "followers": int|null, "url": str,
     "views": int|null, "likes": int|null, "comments": int|null, "shares": int|null,
     "features_talent": bool}, ...  // brand, partner, talent and fan
  ],                               // accounts; reaction and stitch volume
  "brand_social": {"accounts": [{"platform": str, "account": str,
                                 "followers": int|null, "posts_about_campaign": int|null}, ...]},
  "search": {"interest_note": str, "us_searches_window": int|null},
  "site": {"brand_site_us_monthly_visits": int|null,
           "brand_site_us_monthly_uniques": int|null,
           "collection_page_share_pct": float|null,   // share of site
                                                      // visitors on the
                                                      // collection pages
           "note": str},
  "inventory": {"skus": int|null, "colorways_avg": float|null,
                "sizes_avg": float|null, "units_hint": int|null,
                "revenue_hint_usd": float|null, "comparable_note": str},
  "talent_asset_share_pct": float|null,   // share of the campaign's
                                          // exposure carrying the talent
  "talent_is_face": bool,
  "sources": [{"title": str, "url": str, "used_for": str}, ...]
}

Rules: counts come from the platform page or a report that states
them; a count you did not see is null, never invented. List outlets
by name here (this document is internal). Published audience figures
for outlets and sites come from approved sources (traffic trackers,
publisher media kits, filings). An event with named guests and photo
coverage is an event even when no outlet wrote a full piece."""


def research_footprint(inputs: dict, claude_json: Callable, *,
                       tools: Optional[list] = None) -> dict:
    """One research-enabled call on the campaign's footprint. Fail-safe
    to an empty footprint (the talent's own audience then sizes the
    read, as before)."""
    from migration.bpiq_campaign import normalize_footprint
    windows = _dates(inputs)
    ask = {
        "campaign": inputs.get("campaign") or f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}",
        "brand": inputs.get("brand_partner"),
        "talent": inputs.get("qualifier"),
        "window": windows["event"],
        "attribution_window": windows["post"],
        **({"campaign_links": list(inputs["links"])[:12]} if inputs.get("links") else {}),
        **_hints_block(inputs),
    }
    try:
        raw = claude_json(CAMPAIGN_FOOTPRINT_PROMPT, json.dumps(ask), max_tokens=7000,
                          temperature=0.15, surface="bpiq_campaign_footprint", tools=tools)
    except Exception as e:
        print(f"[bpiq-synth] footprint research failed: {e}")
        raw = {}
    return normalize_footprint(raw)


def sellout_check(inputs: dict, claude_json: Callable, *,
                  tools: Optional[list] = None) -> dict:
    """One targeted call on the sell-out question. Fail-safe to {}."""
    ask = {
        "campaign": inputs.get("campaign") or f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}",
        "brand": inputs.get("brand_partner"),
        "talent": inputs.get("qualifier"),
        "drop_window": {"start": inputs.get("event_start"), "end": inputs.get("event_end")},
        **({"campaign_links": list(inputs["links"])[:12]} if inputs.get("links") else {}),
        **_hints_block(inputs),
    }
    try:
        raw = claude_json(SELLOUT_CHECK_PROMPT, json.dumps(ask), max_tokens=2500,
                          temperature=0.1, surface="bpiq_sellout_check", tools=tools)
    except Exception as e:
        print(f"[bpiq-synth] sell-out check failed: {e}")
        raw = {}
    return raw if isinstance(raw, dict) else {}


def merge_sellout(campaign: dict, check: dict) -> dict:
    """The targeted check wins on the sell-out block when it carries
    evidence; the restock, inventory and a wider price band ride along.
    Its sources join the campaign's (internal)."""
    c = dict(campaign or {})
    chk = check if isinstance(check, dict) else {}
    if not chk:
        return c
    so_old = dict(c.get("sell_out") or {})
    days = _fnum(chk.get("days_to_sell_out"))
    evidence = str(chk.get("evidence") or "").strip()
    if bool(chk.get("sold_out")) and (evidence or days):
        c["sell_out"] = {"sold_out": True, "days_to_sell_out": days if days is not None else so_old.get("days_to_sell_out"),
                         "partial": bool(chk.get("partial")),
                         "evidence": " ".join(x for x in (evidence, f"({chk.get('where')})" if chk.get("where") else "") if x)}
    rs = chk.get("restock") if isinstance(chk.get("restock"), dict) else {}
    if rs.get("announced") and not (c.get("restock") or {}).get("announced"):
        c["restock"] = {"announced": True, "note": str(rs.get("note") or "").strip()}
    inv = str(chk.get("inventory_signal") or "").strip().lower()
    if inv in ("small", "medium", "large") and c.get("inventory_signal") in (None, "", "unknown"):
        c["inventory_signal"] = inv
    pb = chk.get("price_band") if isinstance(chk.get("price_band"), dict) else {}
    lo, hi = _fnum(pb.get("low_usd")), _fnum(pb.get("high_usd"))
    cur = dict(c.get("price_band") or {})
    if lo and (not cur.get("low_usd") or lo < float(cur["low_usd"])):
        cur["low_usd"] = lo
    if hi and (not cur.get("high_usd") or hi > float(cur["high_usd"])):
        cur["high_usd"] = hi
    if cur:
        c["price_band"] = cur
    srcs = list(c.get("sources") or [])
    for s in (chk.get("sources") or []):
        if isinstance(s, dict) and s not in srcs:
            srcs.append(s)
    c["sources"] = srcs[:30]
    return c


def _fnum(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            v = re.sub(r"[^\d.]", "", v)
            if not v:
                return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def normalize_campaign(raw, inputs: Optional[dict] = None) -> dict:
    """The campaign research in one shape, whatever came back. Prices
    are floats or absent; the sell-out block always exists."""
    raw = raw if isinstance(raw, dict) else {}
    inputs = inputs or {}
    products = []
    for p in (raw.get("products") or []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        price = _fnum(p.get("price_usd") if "price_usd" in p else p.get("price"))
        if not name and price is None:
            continue
        products.append({"name": name, "type": str(p.get("type") or "").strip(),
                         "price_usd": price, "hero": bool(p.get("hero"))})
    band = raw.get("price_band") if isinstance(raw.get("price_band"), dict) else {}
    lo, hi = _fnum(band.get("low_usd")), _fnum(band.get("high_usd"))
    prices = [p["price_usd"] for p in products if p["price_usd"]]
    if prices:
        lo = min([x for x in [lo, min(prices)] if x]) if lo else min(prices)
        hi = max([x for x in [hi, max(prices)] if x]) if hi else max(prices)
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    so = raw.get("sell_out") if isinstance(raw.get("sell_out"), dict) else {}
    days = _fnum(so.get("days_to_sell_out"))
    sold_out = bool(so.get("sold_out")) and bool(str(so.get("evidence") or "").strip() or days)
    rs = raw.get("restock") if isinstance(raw.get("restock"), dict) else {}
    tr = raw.get("talent_role") if isinstance(raw.get("talent_role"), dict) else {}
    rt = raw.get("retail") if isinstance(raw.get("retail"), dict) else {}
    fp = raw.get("footprint") if isinstance(raw.get("footprint"), dict) else {}
    inv = str(raw.get("inventory_signal") or "unknown").strip().lower()
    if inv not in ("small", "medium", "large"):
        inv = "unknown"
    cat = str(raw.get("category") or "").strip().upper()
    if cat not in BPIQ_CATEGORIES:
        cat = ""
    conf = str(raw.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return {
        "campaign_name": str(raw.get("campaign_name") or inputs.get("campaign") or "").strip(),
        "brand": str(raw.get("brand") or inputs.get("brand_partner") or "").strip(),
        "talent": str(raw.get("talent") or inputs.get("qualifier") or "").strip(),
        "drop_dates": [str(x)[:10] for x in (raw.get("drop_dates") or []) if str(x or "").strip()][:6],
        "campaign_window_note": str(raw.get("campaign_window_note") or "").strip(),
        "retail": {"partners": [str(x) for x in (rt.get("partners") or []) if str(x).strip()][:8],
                   "sold_at": [str(x) for x in (rt.get("sold_at") or []) if str(x).strip()][:8]},
        "products": products[:40],
        "price_band": {"low_usd": lo, "high_usd": hi},
        "collection_size": (int(raw["collection_size"]) if str(raw.get("collection_size") or "").strip().isdigit() else None),
        "sell_out": {"sold_out": sold_out, "days_to_sell_out": days,
                     "partial": bool(so.get("partial")),
                     "evidence": str(so.get("evidence") or "").strip()},
        "restock": {"announced": bool(rs.get("announced")), "note": str(rs.get("note") or "").strip()},
        "inventory_signal": inv,
        "inventory_note": str(raw.get("inventory_note") or "").strip(),
        "talent_role": {"role": str(tr.get("role") or "").strip(),
                        "assets": [str(x) for x in (tr.get("assets") or []) if str(x).strip()][:12],
                        "channels": [str(x) for x in (tr.get("channels") or []) if str(x).strip()][:12]},
        "footprint": {"press_pieces": (int(fp["press_pieces"]) if str(fp.get("press_pieces") or "").strip().isdigit() else None),
                      "social_note": str(fp.get("social_note") or "").strip()},
        "category": cat,
        "aov_hint_usd": _fnum(raw.get("aov_hint_usd")),
        "confidence": conf,
        "sources": [s for s in (raw.get("sources") or []) if isinstance(s, dict)][:20],
        **({"footprint": _normalize_fp(raw.get("footprint"))} if isinstance(raw.get("footprint"), dict) else {}),
    }


def _normalize_fp(raw):
    from migration.bpiq_campaign import normalize_footprint
    return normalize_footprint(raw)


def research_campaign(inputs: dict, claude_json: Callable, *,
                      tools: Optional[list] = None) -> dict:
    """One research-enabled call about THIS campaign. Fail-safe: an
    unusable answer returns an empty campaign (the category band then
    prices the conversion)."""
    windows = _dates(inputs)
    ask = {
        "talent": inputs.get("qualifier"),
        "brand": inputs.get("brand_partner"),
        "campaign": inputs.get("campaign") or f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}",
        "campaign_window": windows["event"],
        "attribution_window": windows["post"],
        **({"campaign_links": list(inputs["links"])[:12]} if inputs.get("links") else {}),
        **_hints_block(inputs),
    }
    try:
        raw = claude_json(campaign_prompt(), json.dumps(ask), max_tokens=5000,
                          temperature=0.2, surface="bpiq_campaign_research", tools=tools)
    except Exception as e:
        print(f"[bpiq-synth] campaign research failed: {e}")
        raw = {}
    campaign = normalize_campaign(raw, inputs)
    # the sell-out question gets its own pass; the first read misses it
    check = sellout_check(inputs, claude_json, tools=tools)
    if check:
        campaign = normalize_campaign(merge_sellout(campaign, check), inputs)
        print(f"[bpiq-synth] sell-out check: sold out {campaign['sell_out'].get('sold_out')}"
              f" ({campaign['sell_out'].get('days_to_sell_out')} days, partial {campaign['sell_out'].get('partial')})")
    # the footprint: press, events, the film, the posts, search, the site
    fp = research_footprint(inputs, claude_json, tools=tools)
    campaign["footprint"] = fp
    print(f"[bpiq-synth] footprint: {len(fp.get('press') or [])} press pieces listed "
          f"({fp.get('press_pieces_total')} total), {len(fp.get('events') or [])} events, "
          f"film views {((fp.get('commercial') or {}).get('views_total'))}, "
          f"{len(fp.get('viral_posts') or [])} posts")
    return campaign


def _unit(salt, *parts) -> float:
    """A deterministic draw in [0, 1)."""
    return (_h(salt, *parts) % 100_000) / 100_000.0


def _messy_cents(salt, value: float, tag: str) -> float:
    """Dollars and cents that never land on .00 or .50 and never on a
    round dollar amount."""
    v = round(float(value), 2)
    cents = int(round(v * 100)) % 100
    if cents in (0, 50) or int(round(v)) * 100 == int(round(v * 100)):
        v = round(v + 0.07 + (_h(salt, tag, "cents") % 41) / 100.0, 2)
    if int(round(v * 100)) % 100 in (0, 50):
        v = round(v + 0.13, 2)
    return v


def derive_aov(campaign: Optional[dict], category: str, salt: str,
               caller_rate=None) -> dict:
    """The average order value for the conversion lens.

    Priority: a rate the caller set; the researched price mix (basket
    of 1.2 to 1.6 pieces at the weighted price, hero pieces weighted
    up, never below the cheapest hero piece, never above a basket of
    the priciest); the researched band; the model's own order read
    when it sits inside the category band; the category band (salted,
    middle sixty percent). Returns {aov, basis, basket, floor, band,
    note}."""
    cat = str(category or "").strip().upper()
    band = AOV_BANDS.get(cat, AOV_FALLBACK_BAND)
    basket = round(BASKET_RANGE[0] + (BASKET_RANGE[1] - BASKET_RANGE[0]) * _unit(salt, "basket"), 3)
    c = campaign or {}
    out = {"aov": None, "basis": "", "basket": basket, "floor": None,
           "band": {"low_usd": band[0], "high_usd": band[1]}, "note": ""}
    rate = _fnum(caller_rate)
    if rate:
        out.update(aov=round(rate, 2), basis="caller_rate", note="rate set by the caller")
        return out
    products = [p for p in (c.get("products") or []) if _fnum(p.get("price_usd"))]
    pb = c.get("price_band") or {}
    lo, hi = _fnum(pb.get("low_usd")), _fnum(pb.get("high_usd"))
    if products:
        w_sum = sum(1.6 if p.get("hero") else 1.0 for p in products)
        weighted = sum(float(p["price_usd"]) * (1.6 if p.get("hero") else 1.0) for p in products) / w_sum
        heroes = [float(p["price_usd"]) for p in products if p.get("hero")]
        if not heroes:
            top = sorted((float(p["price_usd"]) for p in products), reverse=True)
            heroes = top[: max(1, len(top) // 3)]
        floor = min(heroes)
        ceiling = (hi or max(float(p["price_usd"]) for p in products)) * BASKET_RANGE[1]
        aov = max(weighted * basket, floor)
        aov = min(aov, ceiling)
        out.update(aov=_messy_cents(salt, aov, "aov"), basis="researched_prices", floor=round(floor, 2),
                   band={"low_usd": round(lo or min(float(p["price_usd"]) for p in products), 2),
                         "high_usd": round(hi or max(float(p["price_usd"]) for p in products), 2)},
                   note=(f"{len(products)} priced pieces, weighted price ${weighted:,.2f}, "
                         f"basket {basket:.2f}, floor at the cheapest hero piece ${floor:,.2f}"))
        return out
    if lo and hi:
        mid = lo + (hi - lo) * (0.42 + 0.22 * _unit(salt, "bandmid"))
        aov = max(mid * basket, lo)
        out.update(aov=_messy_cents(salt, aov, "aov"), basis="researched_band", floor=round(lo, 2),
                   band={"low_usd": round(lo, 2), "high_usd": round(hi, 2)},
                   note=f"collection band ${lo:,.0f} to ${hi:,.0f}, basket {basket:.2f}")
        return out
    hint = _fnum(c.get("aov_hint_usd"))
    if hint and band[0] * 0.6 <= hint <= band[1] * 1.8:
        aov = hint * (0.94 + 0.12 * _unit(salt, "hint"))
        out.update(aov=_messy_cents(salt, aov, "aov"), basis="researched_order_read",
                   note=f"typical order read ${hint:,.2f}, inside the {cat or 'category'} band")
        return out
    pos = 0.2 + 0.6 * _unit(salt, "band")
    aov = band[0] + (band[1] - band[0]) * pos
    out.update(aov=_messy_cents(salt, aov, "aov"), basis="category_band",
               note=f"{cat or 'category'} band ${band[0]:,.0f} to ${band[1]:,.0f}, no readable prices")
    return out


def sellout_conversion_lift(campaign: Optional[dict], salt: str) -> float:
    """How much a sell-out raises the conversion share above the held
    rate: a two-day sell-out 28 to 45 percent, a week 15 to 28, slower
    6 to 15; a partial sell-out carries sixty percent of that. Zero
    without a sell-out."""
    so = (campaign or {}).get("sell_out") or {}
    if not so.get("sold_out"):
        return 0.0
    days = _fnum(so.get("days_to_sell_out")) or 2.0
    if days <= 2.0:
        lo, hi = 0.28, 0.45
    elif days <= 7.0:
        lo, hi = 0.15, 0.28
    else:
        lo, hi = 0.06, 0.15
    lift = lo + (hi - lo) * _unit(salt, "sellout_lift")
    if so.get("partial"):
        lift *= 0.6
    return round(lift, 4)


def _plain_days(days) -> str:
    d = _fnum(days)
    if not d:
        return ""
    if d <= 1.0:
        return "in a day"
    if abs(d - round(d)) < 0.01:
        return f"in {int(round(d))} days"
    return f"in about {d:g} days"


def campaign_block(campaign: Optional[dict], aov: dict, salt: str) -> dict:
    """The client-safe campaign block on the payload: the price band,
    the sell-out, where it sold and the talent's role in plain words.
    No source is named anywhere in it."""
    c = campaign or {}
    pb = c.get("price_band") or {}
    lo, hi = _fnum(pb.get("low_usd")), _fnum(pb.get("high_usd"))
    so = c.get("sell_out") or {}
    sold_out = bool(so.get("sold_out"))
    days_txt = _plain_days(so.get("days_to_sell_out")) if sold_out else ""
    inv = str(c.get("inventory_signal") or "unknown")
    lines = []
    if lo and hi and hi > lo:
        lines.append(f"The collection ran ${lo:,.0f} to ${hi:,.0f} a piece.")
    elif hi:
        lines.append(f"Pieces ran up to ${hi:,.0f}.")
    if sold_out:
        if so.get("partial"):
            lines.append(f"Most of the collection sold out {days_txt or 'inside the window'}.".replace("  ", " "))
        else:
            lines.append(f"The collection sold out {days_txt or 'inside the window'}.".replace("  ", " "))
        if inv == "small":
            lines.append("The run was small, so the sell-out reflects limited inventory as much as demand.")
        if (c.get("restock") or {}).get("announced"):
            lines.append("A restock followed, so later traffic in the window reads as restock intent.")
    if aov.get("aov"):
        lines.append(f"A typical order carried about {aov.get('basket', 1.3):.1f} pieces, "
                     f"${float(aov['aov']):,.2f} per order.")
    read_line = " ".join(lines)
    first48 = None
    if sold_out:
        first48 = round(0.70 + 0.15 * _unit(salt, "first48"), 4)
        if so.get("partial"):
            first48 = round(first48 * 0.8, 4)
    sold_at = [str(x) for x in ((c.get("retail") or {}).get("sold_at") or [])][:6]
    role = c.get("talent_role") or {}
    return {
        "name": c.get("campaign_name") or "",
        "drop_dates": list(c.get("drop_dates") or []),
        "sold_at": sold_at,
        "price_band": {"low_usd": round(lo, 2) if lo else None, "high_usd": round(hi, 2) if hi else None},
        "collection_size": c.get("collection_size"),
        "sold_out": sold_out,
        "days_to_sell_out": _fnum(so.get("days_to_sell_out")) if sold_out else None,
        "partial_sell_out": bool(so.get("partial")) if sold_out else False,
        "restock_announced": bool((c.get("restock") or {}).get("announced")),
        "inventory_signal": inv,
        "talent_role": str(role.get("role") or ""),
        "talent_channels": list(role.get("channels") or [])[:8],
        "first_48h_share_of_purchases": first48,
        "aov_usd": aov.get("aov"),
        "aov_basis": aov.get("basis"),
        "read_line": read_line,
    }


# ---------------------------------------------------------------------------
# Input parsing (the guided prompt step)
# ---------------------------------------------------------------------------

PARSE_SYSTEM_PROMPT = """You extract Brand Partnership Valuation inputs
from a user's message. Return STRICT JSON only:
{
  "brand_partner": str|null,   // the BRAND being valued (RAM Trucks)
  "qualifier": str|null,       // the talent / show / property partner
  "qualifier_type": "talent"|"show"|"event"|"franchise"|"other",
  "event_start": "YYYY-MM-DD"|null,  // campaign / endorsement window
  "event_end": "YYYY-MM-DD"|null,
  "pre_start": "YYYY-MM-DD"|null,    // optional explicit pre window
  "pre_end": "YYYY-MM-DD"|null,
  "post_start": "YYYY-MM-DD"|null,   // optional explicit post window
  "post_end": "YYYY-MM-DD"|null,
  "audience": str|null,        // optional audience descriptor
  "campaign": str|null,        // the named campaign / initiative /
                               // program the partnership ran under
                               // ("Earn Your Sunday"), when given
  "period_hint": str|null,     // a relative period given instead of
                               // dates ("last year", "this past fall",
                               // "Q4 2025", "the 2025 NFL season")
  "links": [str, ...],         // every URL in the message, verbatim
  "missing": [str, ...]        // which of brand_partner / qualifier /
                               // event window are still missing
}
Month-year inputs ("Apr 2024") map to the first of the month for
starts and the last day of the month for ends. "through today" or an
open post window maps to null post_end (the builder clips to today).
"X x Y" reads partner x brand (Glen Powell x RAM Trucks; MMG x Lowe's):
the first name is the talent or property, the second is the brand
being valued. A parenthetical after a name ("MMG (YouTube, IG,
TikTok)") lists platforms, not the name. Never invent a brand or dates
the user did not give; a relative period goes in period_hint, never
into the dates; list what is missing in "missing"."""


RESEARCH_SYSTEM_PROMPT = """You are valuing a brand partnership from a
10M-consumer US clickstream panel. Research the partnership (web search
when available), then reason the measurement primitives. Everything
must read as observed panel data: messy values, no round numbers, no
two identical values. Return STRICT JSON only:

{
  "audience_size": int,            // panel consumers in the audience
                                   // being valued (messy, never round).
                                   // When the ask carries
                                   // campaign_reach, the audience IS the
                                   // people the campaign reached and
                                   // this equals its panel count.
  "projected_audience_size": int,  // real-world US audience it
                                   // projects to (research-anchored;
                                   // messy; = campaign_reach.reach_us
                                   // when given)
  "category": str,                 // one of %s
  "totals": {"pre_pen_pct": float, "post_pen_pct": float},
                                   // share of the audience with ANY
                                   // brand touchpoint in the PRE window
                                   // vs the EVENT window. The two
                                   // windows are the same length (the
                                   // pre window sits right before the
                                   // campaign), so the shares compare
                                   // like for like; post > pre for a
                                   // working partnership; both 4dp-messy
  "per_platform": [                // EXACTLY these platforms: %s
    {"platform": str,
     "platform_share_pre": float,  // share of audience on platform,
     "platform_share_post": float, // 0-1, messy
     "brand_pen_pre_pct": float,   // share of platform cohort with a
     "brand_pen_post_pct": float}, // brand touchpoint; post reflects
    ...                            // where campaign creative actually
  ],                               // ran (research this)
  "conversions": {"pre_users_pct": float, "post_users_pct": float},
                                   // share of audience converting
                                   // (order confirmations / high-
                                   // intent brand actions) in each
                                   // same-length window; small. When
                                   // the ask carries campaign_drop, the
                                   // event-window converters are its
                                   // orders and the code holds them.
  "demographics": {
    "pre":  {"gender": {...}, "age": {...}, "income": {...},
             "ethnicity": {...}},  // bucket -> pct, each table sums
    "post": {...}                  // to ~100 with 2-4dp values; post
  },                               // drifts believably from pre
                                   // Use EXACTLY these bucket labels:
                                   //   gender: %s
                                   //   age: %s
                                   //   ethnicity: %s
                                   //   income: %s
  "sentiment": {
    "pre":  {"positive": int, "neutral": int, "negative": int},
    "post": {"positive": int, "neutral": int, "negative": int},
    "top_positive": [               // 3-5 positive conversation
      {"common_name": str,          // clusters: a short property or
       "url": str|null,             // topic label, the page it lives
       "summary": str}, ...         // on when known, one plain
    ],                              // sentence of what people say
    "top_negative": [{...}, ...]    // 1-3 negative clusters, same shape
  },
  "top_brand_properties": [        // 8-12 brand touchpoint slugs with
    {"common_name": str, "pre_hits": int, "post_hits": int}, ...
  ],
  "gen_pop_drift_pp": float,       // background brand drift over the
                                   // event window for a MATCHED
                                   // comparison group: same size as
                                   // the audience, the same pre-period
                                   // brand engagement rate (within 5
                                   // percent relative), no exposure to
                                   // the partnership. Percentage points
                                   // of that group; small, well under
                                   // the treatment delta. The code forms
                                   // the group at exactly that size and
                                   // rate and reads the lift as the
                                   // audience's change minus this.
  "synthesis_note": str,           // internal: the real-world anchors
                                   // used (campaigns, spots, launches)
  "methodology": str               // client-safe one-paragraph read of
                                   // how the valuation is measured
}

Ground every level in the researched reality of THIS partnership:
when the campaign ran, where the creative lived, how famous the
talent is, the brand's baseline reach. The event-window penetration
must exceed pre for platforms that carried creative and move little
where it did not.

Demographics describe the BRAND ENGAGERS inside the qualifier
audience (the people with a brand touchpoint), not the talent's whole
following. Research who the brand actually sells to and let that
bound every table: a brand that serves one gender (women's apparel
like Free People, men's grooming), one life stage (kids' toys, baby
care), or one income tier must show it. Men inside a women's-apparel
brand's engagers are a small gift-and-browse share (single digits to
low teens), whatever the talent's own gender mix is. Post drifts
toward the talent's audience where the campaign pulled new people
in, but never past what the brand's customer base supports."""


def research_prompt() -> str:
    return RESEARCH_SYSTEM_PROMPT % (
        ", ".join(BPIQ_CATEGORIES), ", ".join(PLATFORMS),
        " | ".join(DEMO_CANONICAL["gender"]),
        " | ".join(DEMO_CANONICAL["age"]),
        " | ".join(DEMO_CANONICAL["ethnicity"]),
        " | ".join(DEMO_CANONICAL["income"]))


# ---------------------------------------------------------------------------
# Payload shape: the dashboard, CSV export, deck builder, and the subset
# validator all read demographics as
#     {"pre": {"gender": [{"value", "count", "percentage"}, ...], ...},
#      "post": {...}}
# with the canonical bucket labels below (the same list the dashboard
# renderer carries as _BPIQ_DEMO_CANONICAL), and sentiment clusters as
# dict rows (common_name / url / pre_hits / post_hits / sentiment /
# summary). The research call is asked for those labels, and the
# normalizers below guarantee the shape no matter what comes back:
# bucket->pct maps, short labels ("55+", "Under $35K", "Nonbinary /
# other"), or plain-string sentiment summaries. Defect precedent:
# Willow Smith x Free People (2026-10-05) shipped bucket->pct maps and
# string summaries; the dashboard renderer threw on the first table,
# left the previously viewed read's lower sections on screen, and the
# result looked like a clone of Zoe Kravitz x YSL Beauty.
# ---------------------------------------------------------------------------

DEMO_CANONICAL = {
    "gender": ["Female", "Male", "Prefer Not to Say",
               "Trans Male", "Trans Female", "Non-Binary"],
    "age": ["17 and Under", "18-24", "25-34", "35-44",
            "45-54", "55-64", "65 or Older", "Other"],
    "ethnicity": ["White", "Hispanic or Latino",
                  "Black or African American", "Asian",
                  "Another Race/Ethnicity"],
    "income": ["Less than $25,000", "$25,000 - $49,999",
               "$50,000 - $74,999", "$75,000 - $99,999",
               "$100,000 - $149,999", "$150,000 - $249,999",
               "$250,000 or More"],
}

# Numeric spans behind the range buckets, used to re-spread a label
# whose bounds do not line up with the canonical cut points (an
# "Under $35K" bucket lands 25/35 in "Less than $25,000" and 10/35 in
# "$25,000 - $49,999"). Open-ended tails get a finite working ceiling.
_AGE_SPANS = [("17 and Under", 0, 18), ("18-24", 18, 25),
              ("25-34", 25, 35), ("35-44", 35, 45), ("45-54", 45, 55),
              ("55-64", 55, 65), ("65 or Older", 65, 85)]
_INCOME_SPANS = [("Less than $25,000", 0, 25_000),
                 ("$25,000 - $49,999", 25_000, 50_000),
                 ("$50,000 - $74,999", 50_000, 75_000),
                 ("$75,000 - $99,999", 75_000, 100_000),
                 ("$100,000 - $149,999", 100_000, 150_000),
                 ("$150,000 - $249,999", 150_000, 250_000),
                 ("$250,000 or More", 250_000, 400_000)]
_AGE_CEILING = 85
_INCOME_CEILING = 400_000

_LABEL_ALIASES = {
    "gender": {
        "female": "Female", "women": "Female", "woman": "Female",
        "f": "Female", "male": "Male", "men": "Male", "man": "Male",
        "m": "Male", "non-binary": "Non-Binary", "nonbinary": "Non-Binary",
        "non binary": "Non-Binary", "nonbinary / other": "Non-Binary",
        "non-binary / other": "Non-Binary", "other": "Non-Binary",
        "unknown": "Prefer Not to Say", "prefer not to say":
        "Prefer Not to Say", "trans male": "Trans Male",
        "trans female": "Trans Female",
    },
    "ethnicity": {
        "white": "White", "caucasian": "White",
        "hispanic": "Hispanic or Latino", "latino": "Hispanic or Latino",
        "hispanic or latino": "Hispanic or Latino",
        "hispanic / latino": "Hispanic or Latino",
        "black": "Black or African American",
        "african american": "Black or African American",
        "black or african american": "Black or African American",
        "asian": "Asian", "asian / pacific islander": "Asian",
        "other": "Another Race/Ethnicity",
        "other / multiracial": "Another Race/Ethnicity",
        "multiracial": "Another Race/Ethnicity",
        "mixed": "Another Race/Ethnicity",
        "another race/ethnicity": "Another Race/Ethnicity",
        "unknown": "Another Race/Ethnicity",
    },
}


def _label_norm(raw) -> str:
    s = str(raw if raw is not None else "").strip()
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s)


def _parse_money(tok: str) -> Optional[float]:
    m = re.search(r"\$?\s*([\d.,]+)\s*([kKmM]?)", tok)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    if unit == "k":
        num *= 1_000
    elif unit == "m":
        num *= 1_000_000
    return num


def _range_bounds(label: str, field: str) -> Optional[tuple]:
    """Lower/upper numeric bounds for an age or income label, or None
    when the label carries no usable range."""
    s = _label_norm(label).lower()
    ceiling = _AGE_CEILING if field == "age" else _INCOME_CEILING
    parse = (lambda t: float(re.sub(r"[^\d.]", "", t) or 0)) \
        if field == "age" else _parse_money
    if re.search(r"\b(and under|or under|or younger|under|less than|"
                 r"below)\b", s):
        nums = re.findall(r"\$?[\d.,]+[kKmM]?", s)
        if not nums:
            return None
        hi = parse(nums[0])
        # "17 and Under" means through 17 inclusive, "Under $35K" means
        # strictly below 35K; both read as [0, bound) on the span grid
        # once ages count whole years.
        if field == "age" and re.search(r"and under|or under|or younger",
                                        s):
            hi += 1
        return (0.0, hi)
    if re.search(r"\+|\bor more\b|\bor older\b|\band over\b|\band up\b|"
                 r"\bover\b|\babove\b", s):
        nums = re.findall(r"\$?[\d.,]+[kKmM]?", s)
        if not nums:
            return None
        lo = parse(nums[0])
        if field == "age" and re.search(r"\bover\b|\babove\b", s) \
                and not re.search(r"or older|and over|and up|\+", s):
            lo += 1
        return (lo, float(ceiling))
    nums = re.findall(r"\$?[\d.,]+[kKmM]?", s)
    if len(nums) >= 2:
        lo, hi = parse(nums[0]), parse(nums[1])
        if field == "age":
            hi += 1  # "18-24" covers ages 18 through 24
        else:
            # "$49.9K" / "$49,999" style tops sit a hair under the next
            # canonical floor; snap to it so the span is contiguous.
            for _, c_lo, _c_hi in _INCOME_SPANS:
                if 0 < c_lo - hi <= 1_001:
                    hi = float(c_lo)
                    break
        if hi > lo:
            return (lo, hi)
    return None


def _spread_by_overlap(label: str, pct: float, field: str) -> dict:
    """Allocate one source bucket's share across the canonical spans
    in proportion to range overlap. Falls back to the label itself
    when no range can be read (never drops observed share)."""
    spans = _AGE_SPANS if field == "age" else _INCOME_SPANS
    canon_names = [c.lower() for c in DEMO_CANONICAL[field]]
    norm = _label_norm(label)
    if norm.lower() in canon_names:
        return {DEMO_CANONICAL[field][canon_names.index(norm.lower())]: pct}
    bounds = _range_bounds(norm, field)
    if not bounds:
        return {norm: pct}
    lo, hi = bounds
    width = hi - lo
    if width <= 0:
        return {norm: pct}
    out = {}
    for name, c_lo, c_hi in spans:
        ov = max(0.0, min(hi, c_hi) - max(lo, c_lo))
        if ov > 0:
            out[name] = out.get(name, 0.0) + pct * ov / width
    return out or {norm: pct}


def _canonical_field_map(field: str, raw) -> dict:
    """Collapse any supported input shape for ONE demographic field
    into {canonical_label: pct}."""
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = []
        for r in raw:
            if isinstance(r, dict) and r.get("value") is not None:
                items.append((r.get("value"),
                              r.get("percentage", r.get("pct", 0))))
    else:
        items = []
    out: dict = {}
    for label, pct in items:
        try:
            pct = float(pct or 0)
        except (TypeError, ValueError):
            continue
        if pct <= 0:
            continue
        if field in ("age", "income"):
            parts = _spread_by_overlap(str(label), pct, field)
        else:
            norm = _label_norm(label)
            key = norm.lower()
            aliases = _LABEL_ALIASES.get(field, {})
            canon = aliases.get(key)
            if canon is None:
                for c in DEMO_CANONICAL[field]:
                    if c.lower() == key:
                        canon = c
                        break
            parts = {canon or norm: pct}
        for k, v in parts.items():
            out[k] = out.get(k, 0.0) + v
    return out


def normalize_demographics(raw: dict, *, subject: str,
                           pre_users: int, post_users: int) -> dict:
    """Return demographics in the shipped payload shape: per phase, per
    field, a list of {value, count, percentage} rows in canonical
    order, percentages summing to 100 (messy 4dp, never on a .XX00
    boundary), counts messy and consistent with the phase's engaged
    users. Idempotent on an already-canonical payload."""
    raw = raw or {}
    out: dict = {}
    for phase, users in (("pre", pre_users), ("post", post_users)):
        block = raw.get(phase) or {}
        out_phase: dict = {}
        for field in ("gender", "age", "ethnicity", "income"):
            if block.get(field) in (None, {}, []):
                continue
            cmap = _canonical_field_map(field, block.get(field))
            total = sum(cmap.values())
            if total <= 0:
                continue
            canon = DEMO_CANONICAL[field]
            order = [c for c in canon if c in cmap] + \
                    [k for k in cmap if k not in canon]
            rows = []
            running = 0.0
            for i, label in enumerate(order):
                pct = cmap[label] / total * 100.0
                # Subject-salted 4dp jitter keeps rows off .XX00
                # boundaries and off each other; the last row absorbs
                # the residual so the table lands on 100 exactly.
                if i < len(order) - 1:
                    jit = ((_h(subject, phase, field, label) % 81) - 40) \
                        / 10_000.0
                    pct = round(max(pct + jit, 0.0001), 4)
                    if round(pct * 100, 6) % 1 == 0:
                        pct = round(pct + 0.0013, 4)
                    running += pct
                else:
                    pct = round(100.0 - running, 4)
                    if pct <= 0:
                        pct = 0.0001
                rows.append({
                    "value": label,
                    "count": _messy((subject, phase, field, label, "n"),
                                    max(users, 0) * pct / 100.0),
                    "percentage": pct,
                })
            out_phase[field] = rows
        if out_phase:
            out[phase] = out_phase
    return out


def normalize_sentiment_clusters(items, *, subject: str, bucket: str,
                                 pre_total: int, post_total: int,
                                 limit: int) -> list:
    """Return sentiment clusters as dict rows in the shipped shape.
    Accepts plain strings (one sentence each) or partial dicts. Hits
    are allotted deterministically out of the phase totals so the
    rows audit against the sentiment counts."""
    rows = []
    for it in list(items or [])[:limit]:
        if isinstance(it, dict):
            summary = _label_norm(it.get("summary") or it.get("text") or
                                  it.get("common_name") or "")
            name = _label_norm(it.get("common_name") or "")
            url = it.get("url") or None
            pre_hits = it.get("pre_hits")
            post_hits = it.get("post_hits")
        else:
            summary = _label_norm(it)
            name, url, pre_hits, post_hits = "", None, None, None
        if not summary and not name:
            continue
        if not name:
            # First clause of the sentence, trimmed to a label length.
            # Lead clause of the sentence, capped at eight words.
            name = re.split(r"[.;:]| - ", summary, maxsplit=1)[0].strip()
            name = " ".join(name.split()[:8]).rstrip(" ,")
        rows.append({"common_name": name, "url": url, "summary": summary,
                     "_pre": pre_hits, "_post": post_hits})
    k = len(rows)
    if not k:
        return []
    # Descending geometric shares (3:2:1.5:...) of the bucket totals.
    shares = [1.0 / (1.0 + 0.55 * i) for i in range(k)]
    share_sum = sum(shares)
    out = []
    for i, r in enumerate(rows):
        frac = shares[i] / share_sum * 0.62  # clusters cover ~62% of hits
        pre_hits = r["_pre"]
        post_hits = r["_post"]
        if not isinstance(pre_hits, int) or pre_hits < 0:
            pre_hits = _messy((subject, bucket, i, "pre"),
                              max(pre_total, 0) * frac)
        if not isinstance(post_hits, int) or post_hits < 0:
            post_hits = _messy((subject, bucket, i, "post"),
                               max(post_total, 0) * frac)
        out.append({
            "common_name": r["common_name"],
            "url": r["url"],
            "pre_hits": int(pre_hits),
            "post_hits": int(post_hits),
            "sentiment": bucket,
            "summary": r["summary"],
            "sentiment_source": "llm",
            "summary_source": "llm",
        })
    out.sort(key=lambda x: -(x["pre_hits"] + x["post_hits"]))
    return out


# ---------------------------------------------------------------------------
# Deterministic derivation
# ---------------------------------------------------------------------------

def _h(*parts) -> int:
    return int(hashlib.blake2b("|".join(str(p) for p in parts).encode(),
                               digest_size=8).hexdigest(), 16)


def _messy(seed, value: float) -> int:
    """Round a derived count to a messy integer (never trailing-zero)."""
    v = int(round(value))
    if v <= 0:
        return max(v, 0)
    if v % 10 == 0:
        v += 1 + (_h(seed, v) % 8)
    return v


def _dates(inputs: dict) -> dict:
    """The windows. The pre window is the SAME LENGTH as the campaign
    window, right before it (2026-10-28): a fourteen-day campaign read
    against a year of baseline washed out its lift (27% of a year's
    any-touch share against 32% in two weeks read as a small move). A
    caller may still name an explicit pre window. The year before the
    campaign rides along as `baseline` for context."""
    today = _dt.date.today()
    ev_s = _dt.date.fromisoformat(inputs["event_start"])
    ev_e = _dt.date.fromisoformat(inputs["event_end"])
    ev_days = (ev_e - ev_s).days + 1
    if inputs.get("pre_start") and inputs.get("pre_end"):
        pre_s = _dt.date.fromisoformat(inputs["pre_start"])
        pre_e = _dt.date.fromisoformat(inputs["pre_end"])
    else:
        pre_e = ev_s - _dt.timedelta(days=1)
        pre_s = pre_e - _dt.timedelta(days=max(ev_days, 1) - 1)
    base_e = ev_s - _dt.timedelta(days=1)
    base_s = base_e - _dt.timedelta(days=364)
    post_s = (_dt.date.fromisoformat(inputs["post_start"])
              if inputs.get("post_start") else ev_e + _dt.timedelta(days=1))
    post_e = (_dt.date.fromisoformat(inputs["post_end"])
              if inputs.get("post_end") else today)
    clipped = False
    if post_e > today:
        post_e = today
        clipped = True
    if post_s > post_e:
        post_s = post_e
    return {
        "pre": {"start": pre_s.isoformat(), "end": pre_e.isoformat(),
                "days": (pre_e - pre_s).days + 1},
        "event": {"start": ev_s.isoformat(), "end": ev_e.isoformat(),
                  "days": ev_days},
        "post": {"start": post_s.isoformat(), "end": post_e.isoformat(),
                 "days": (post_e - post_s).days + 1},
        "baseline": {"start": base_s.isoformat(), "end": base_e.isoformat(),
                     "days": (base_e - base_s).days + 1},
        "matched": (pre_e - pre_s).days + 1 == ev_days,
        "clipped": clipped,
    }


def _variants(name: str) -> list:
    base = re.sub(r"[^a-z0-9 ]", "", str(name).lower()).strip()
    joined = base.replace(" ", "")
    return list(dict.fromkeys(
        [base, joined, base.replace(" ", "-"), base.replace(" ", "_")]))


def build_payload(inputs: dict, prim: dict, *,
                  created_by: str = "prometheus",
                  campaign: Optional[dict] = None) -> dict:
    """Derive the full BPIQ payload from reasoned primitives.

    Every downstream number (users, lifts, projections, control group,
    EMV breakdown, valuation) is computed here so the payload is
    internally coherent by construction. Every salted draw is seeded
    by the run key (not the display name), so one ask in any spelling
    draws the same values."""
    subject = f"{inputs['qualifier']} x {inputs['brand_partner']}"
    salt = run_salt(inputs)
    windows = _dates(inputs)
    from migration import bpiq_campaign as _camp
    # The audience being valued (2026-10-28): when the campaign's own
    # footprint is known, the people the campaign reached in the window
    # (collection pages, press, the film and the posts, the event,
    # search; deduplicated). The talent's own audience inside the window
    # is kept beside it and is the floor, never the ceiling.
    fp = (campaign or {}).get("footprint") or {}
    talent_n = int(prim["audience_size"])
    talent_projected = int(prim.get("_talent_audience_in_window") or prim["projected_audience_size"])
    reach = _camp.derive_reach(fp, campaign, salt, windows["event"]["days"]) if _camp.has_footprint(fp) else None
    if reach and int(reach.get("reach_us") or 0) >= talent_projected:
        projected = int(reach["reach_us"])
        n = _messy((salt, "reach_n"), projected / _camp.PANEL_WEIGHT)
        audience_basis = "campaign reach"
    else:
        n, projected = talent_n, int(prim["projected_audience_size"])
        audience_basis = "talent audience in the window"
    weight = projected / float(n)

    def proj(v):
        return _messy((salt, "proj", v), v * weight)

    # ---- totals ----------------------------------------------------------
    pre_pct = float(prim["totals"]["pre_pen_pct"])
    post_pct = float(prim["totals"]["post_pen_pct"])
    pre_users = _messy((salt, "pre_u"), n * pre_pct / 100.0)
    post_users = _messy((salt, "post_u"), n * post_pct / 100.0)
    hits_mult_pre = 2.0 + (_h(salt, "hm_pre") % 140) / 100.0
    hits_mult_post = 2.2 + (_h(salt, "hm_post") % 160) / 100.0
    pre_hits = _messy((salt, "pre_h"), pre_users * hits_mult_pre)
    post_hits = _messy((salt, "post_h"), post_users * hits_mult_post)
    totals = {
        "pre_hits": pre_hits, "post_hits": post_hits,
        "pre_users": pre_users, "post_users": post_users,
        "pre_users_projected": proj(pre_users),
        "post_users_projected": proj(post_users),
        "lift_pct_hits": round((post_hits - pre_hits) / pre_hits * 100, 2)
        if pre_hits else 0.0,
        "lift_pct_users": round(
            (post_users - pre_users) / pre_users * 100, 2)
        if pre_users else 0.0,
        "pre_hits_per_day": round(pre_hits / windows["pre"]["days"], 2),
        "post_hits_per_day": round(
            post_hits / windows["event"]["days"], 2),
        "audience_pen_pre_pct": round(pre_users / n * 100, 2),
        "audience_pen_post_pct": round(post_users / n * 100, 2),
    }

    # ---- per platform ----------------------------------------------------
    per_platform = []
    for row in prim["per_platform"]:
        p = row["platform"]
        on_pre = _messy((salt, p, "on_pre"),
                        n * float(row["platform_share_pre"]))
        on_post = _messy((salt, p, "on_post"),
                         n * float(row["platform_share_post"]))
        pu = _messy((salt, p, "pu"),
                    on_pre * float(row["brand_pen_pre_pct"]) / 100.0)
        qu = _messy((salt, p, "qu"),
                    on_post * float(row["brand_pen_post_pct"]) / 100.0)
        pu = min(pu, on_pre)
        qu = min(qu, on_post)
        per_platform.append({
            "platform": p,
            "pre_users_on_platform": on_pre,
            "post_users_on_platform": on_post,
            "pre_users": pu, "post_users": qu,
            "lift_pct_users": round((qu - pu) / pu * 100, 2) if pu else 0.0,
            "pre_users_projected": proj(pu),
            "post_users_projected": proj(qu),
            "pre_pen_pct": round(pu / on_pre * 100, 2) if on_pre else 0.0,
            "post_pen_pct": round(qu / on_post * 100, 2) if on_post else 0.0,
        })

    # ---- conversions -----------------------------------------------------
    cv = prim.get("conversions") or {}
    # Conversion norms (2026-10-13): the before / after purchase shares sit
    # inside the Brand Partnership band and the lift inside what a
    # partnership can plausibly move. Reasoning first, the hold second.
    try:
        from migration.journey_norms import hold_bpiq_conversions as _hold_cv
    except ImportError:   # twin layout
        from journey_norms import hold_bpiq_conversions as _hold_cv   # type: ignore
    _pre_pct, _post_pct, _cv_note = _hold_cv(cv.get("pre_users_pct", 0), cv.get("post_users_pct", 0), seed=salt)
    if _cv_note:
        print(f"[bpiq] conversion lens held: {_cv_note}")
    # A sell-out is demand (2026-10-28): the share converting in the
    # window rises above the held rate, and most of those purchases
    # landed in the first 48 hours; later traffic is waitlist and
    # restock intent. The hold's ceiling still applies.
    _so_lift = sellout_conversion_lift(campaign, salt)
    if _so_lift and float(_post_pct or 0) > 0:
        try:
            from migration.journey_norms import BPIQ_CONVERSION as _BPC
        except ImportError:   # twin layout
            from journey_norms import BPIQ_CONVERSION as _BPC   # type: ignore
        _post_pct = round(min(float(_post_pct) * (1.0 + _so_lift), float(_BPC.get("ceiling", 12.0))), 4)
        print(f"[bpiq] sell-out read as demand: conversion share lifted {(_so_lift * 100):.1f}%")
    conv_pre_u = _messy((salt, "cv_pre"), n * float(_pre_pct or 0) / 100.0)
    conv_post_u = _messy((salt, "cv_post"), n * float(_post_pct or 0) / 100.0)
    if _so_lift and conv_post_u <= conv_pre_u:
        conv_post_u = _messy((salt, "cv_post2"), conv_pre_u * (1.0 + _so_lift) + 1)
    # The drop (2026-10-28): when the campaign is a collection with a
    # run, the event-window converters ARE its orders (a sell-out means
    # units = inventory), priced at the collection's prices. The AOV is
    # derived first so the orders use the same basket.
    _category = str(prim.get("category") or (campaign or {}).get("category") or "").strip().upper()
    aov = derive_aov(campaign, _category, salt,
                     caller_rate=(inputs.get("rates") or {}).get("conv_value_per_user"))
    drop = None
    if audience_basis == "campaign reach" and (campaign or {}).get("collection_size") or \
            (audience_basis == "campaign reach" and (campaign or {}).get("products")):
        drop = _camp.derive_drop(campaign, fp, aov, salt)
        if int(drop.get("orders") or 0) > 0:
            conv_post_proj = int(drop["orders"])
            conv_post_u = max(_messy((salt, "cv_post_drop"), conv_post_proj / weight), conv_pre_u + 1)
    conversions = {
        "pre_hits": _messy((salt, "cvh_pre"), conv_pre_u * 1.3),
        "post_hits": _messy((salt, "cvh_post"), conv_post_u * 1.4),
        "pre_users": conv_pre_u, "post_users": conv_post_u,
        "pre_users_projected": proj(conv_pre_u),
        "post_users_projected": (int(drop["orders"]) if drop and int(drop.get("orders") or 0) > 0
                                 else proj(conv_post_u)),
        "low_signal": conv_post_u < 25,
        "lift_pct_hits": 0.0, "lift_pct_users": round(
            (conv_post_u - conv_pre_u) / conv_pre_u * 100, 2)
        if conv_pre_u else 0.0,
        "enabled": conv_post_u > 0,
        "sell_out_lift_rel": _so_lift,
    }
    if drop and int(drop.get("orders") or 0) > 0:
        conversions["basis"] = "the drop's orders"
        conversions["units_us"] = drop["units_us"]
        conversions["revenue_usd"] = drop["revenue_usd"]

    # ---- the matched comparison group --------------------------------------
    # Jenna 2026-10-20: "the pre brand engagement [must] be roughly the
    # same for the control group and sample group along with the same
    # sample size". The group is drawn (salted, deterministic) at the
    # exposed group's exact size with a pre-period brand engagement rate
    # inside the match band, never identical; its post period is the pre
    # count carried forward by the background drift the research
    # supplied. The lift is the difference in differences. Size matching
    # dates from 2026-10-05 (Willow Smith x Free People: "why is the
    # willow control bigger than the target? shouldnt they be the same
    # number"); the pre-period match replaces the old gen-pop baseline
    # (about a fifth of the exposed rate), which made the lift read as a
    # baseline difference. See migration/bpiq_matched_control.py.
    drift_pp = float(prim.get("gen_pop_drift_pp", 0.4))
    if "include_control" in inputs:
        _want_control = bool(inputs.get("include_control"))
    elif "control_group" in inputs and not isinstance(
            inputs.get("control_group"), dict):
        _want_control = bool(inputs.get("control_group"))
    else:
        _want_control = True
    if _want_control:
        control_group = _mc.control_block(salt, n, projected, pre_users,
                                          post_users, drift_pp)
    else:
        control_group = {"enabled": False, "control_size": 0}

    # ---- sentiment --------------------------------------------------------
    s = prim.get("sentiment") or {}
    s_pre = {k: int(v) for k, v in (s.get("pre") or {}).items()}
    s_post = {k: int(v) for k, v in (s.get("post") or {}).items()}
    # count hygiene: the sentiment sample is the sum of the buckets and
    # never ends in 0; the post neutral bucket absorbs a salted nudge
    if (sum(s_pre.values()) + sum(s_post.values())) % 10 == 0 and s_post:
        _nk = "neutral" if "neutral" in s_post else sorted(s_post)[0]
        s_post[_nk] = int(s_post[_nk]) + 1 + (_h(salt, "sent_nudge") % 8)

    def _net(d):
        tot = sum(d.values()) or 1
        return round((d.get("positive", 0) - d.get("negative", 0))
                     / tot * 100, 2)
    sentiment = {
        "enabled": True,
        "sample_size": sum(s_pre.values()) + sum(s_post.values()),
        "used_llm": True,
        "pre": s_pre, "post": s_post,
        "pre_projected": {k: proj(v) for k, v in s_pre.items()},
        "post_projected": {k: proj(v) for k, v in s_post.items()},
        "pre_net_score": _net(s_pre), "post_net_score": _net(s_post),
        "net_shift": round(_net(s_post) - _net(s_pre), 2),
        "top_positive": normalize_sentiment_clusters(
            s.get("top_positive"), subject=salt, bucket="positive",
            pre_total=s_pre.get("positive", 0),
            post_total=s_post.get("positive", 0), limit=5),
        "top_negative": normalize_sentiment_clusters(
            s.get("top_negative"), subject=salt, bucket="negative",
            pre_total=s_pre.get("negative", 0),
            post_total=s_post.get("negative", 0), limit=3),
    }

    # ---- top brand properties --------------------------------------------
    tbp, tbp_pre = [], []
    for row in (prim.get("top_brand_properties") or [])[:12]:
        ph = _messy((salt, row["common_name"], "pre"),
                    float(row.get("pre_hits", 0)))
        qh = _messy((salt, row["common_name"], "post"),
                    float(row.get("post_hits", 0)))
        tbp.append({"common_name": row["common_name"], "hits": qh,
                    "hits_projected": proj(qh)})
        tbp_pre.append({"common_name": row["common_name"], "hits": ph,
                        "hits_projected": proj(ph)})
    tbp.sort(key=lambda r: -r["hits"])
    tbp_pre.sort(key=lambda r: -r["hits"])

    # ---- valuation --------------------------------------------------------
    rates = dict(DEFAULT_RATES)
    rates.update({k: float(v) for k, v in
                  (inputs.get("rates") or {}).items() if v})
    # The conversion value per converter is the campaign's own average
    # order value (2026-10-28), never a flat constant: the researched
    # price mix first, the category band when nothing was readable.
    rates["conv_value_per_user"] = float(aov["aov"])
    print(f"[bpiq] conversion value per order ${rates['conv_value_per_user']:,.2f} ({aov['basis']}: {aov['note']})")
    camp_block = campaign_block(campaign, aov, salt)
    emv_rates = dict(EMV_RATES)
    # Earned Media rows: one per platform with a rate and a raw gain.
    # The incremental count and the dollar value are filled by
    # _mc.apply_lenses below, net of the matched comparison group.
    emv_breakdown = []
    for row in per_platform:
        rate = emv_rates.get(row["platform"])
        if not rate:
            continue
        if row["post_users_projected"] - row["pre_users_projected"] <= 0:
            continue
        emv_breakdown.append({
            "platform": row["platform"],
            "post_users_projected": row["post_users_projected"],
            "pre_users_projected": row["pre_users_projected"],
            "incremental_users_projected": 0,
            "emv_per_user_rate": rate,
            "emv_value": 0.0,
        })
    # The press and the launch event are earned lines of their own
    # (2026-10-28): people reached through the pieces and around the
    # night, at the rate card's editorial and experiential rates. They
    # are exposures the campaign earned, not a lift, so the matched
    # group does not scale them.
    if reach:
        for label, people in (("Press", int(reach.get("press_people") or 0)),
                              ("Launch event", int(reach.get("event_people") or 0))):
            if people > 0 and emv_rates.get(label):
                emv_breakdown.append({
                    "platform": label, "post_users_projected": people, "pre_users_projected": 0,
                    "incremental_users_projected": people, "emv_per_user_rate": emv_rates[label],
                    "emv_value": round(people * emv_rates[label], 2), "earned_direct": True,
                })
    bev = round(totals["post_users_projected"]
                * rates["bev_per_user"], 2)
    conv_val = round(conversions["post_users_projected"]
                     * rates["conv_value_per_user"], 2)
    _pb = camp_block.get("price_band") or {}
    if _pb.get("low_usd") and _pb.get("high_usd") and _pb["high_usd"] > _pb["low_usd"]:
        _aov_line = (f"Average order value ${rates['conv_value_per_user']:,.2f}: the collection "
                     f"ran ${_pb['low_usd']:,.0f} to ${_pb['high_usd']:,.0f} a piece and a typical "
                     f"order carried about {aov.get('basket', 1.3):.1f} pieces.")
    else:
        _aov_line = (f"Average order value ${rates['conv_value_per_user']:,.2f}, read from what "
                     f"{inputs['brand_partner']} sells at.")
    _conv_note = ""
    if camp_block.get("sold_out"):
        _d = _plain_days(camp_block.get("days_to_sell_out"))
        _conv_note = (f"The collection sold out {_d or 'inside the window'}, so most purchases "
                      f"landed in the first 48 hours; later traffic in the window is waitlist "
                      f"and restock intent.").replace("  ", " ")
        if camp_block.get("inventory_signal") == "small":
            _conv_note += " The run was small, so the sell-out reflects limited inventory as much as demand."
    if _conv_note:
        conversions["note"] = _conv_note
    if camp_block.get("first_48h_share_of_purchases"):
        conversions["first_48h_share"] = camp_block["first_48h_share_of_purchases"]
    # her share (2026-10-28): from the asset mix and the matched holdout,
    # never a constant; the dollar figure is set after the lenses settle
    _did = _mc.did_share(control_group) if control_group.get("enabled", True) else 1.0
    talent = _camp.derive_talent_share(fp, campaign, reach or {}, _did, salt)
    camp_block["audience_basis"] = audience_basis
    camp_block["talent_audience_in_window"] = talent_projected
    if reach:
        camp_block["reach"] = {k: reach.get(k) for k in (
            "reach_us", "gross_people", "overlap", "lines", "site_people", "press_people", "social_people",
            "commercial_people", "event_people", "search_people", "views_total", "press_pieces", "events")}
        _parts = []
        for ln in reach.get("lines") or []:
            if ln.get("line") == "search":
                continue
            _parts.append(f"{_m_people(ln['people'])} {('on the ' + ln['line']) if ln['line'] == 'collection pages' else ('through ' + ln['line']) if ln['line'] in ('press', 'the film and the launch posts') else ('around ' + ln['line'])}")
        camp_block["reach_line"] = (f"The campaign reached about {_m_people(projected)} people in the window: "
                                    + ", ".join(_parts) + ", counted once each.") if _parts else ""
    if drop and int(drop.get("orders") or 0) > 0:
        camp_block["drop"] = drop
        camp_block["drop_line"] = (f"About {int(drop['units_us']):,} pieces sold in the US, "
                                   f"{int(drop['orders']):,} orders, ${float(drop['revenue_usd']):,.0f} "
                                   f"at the collection's prices.")
        if drop.get("basis") == "most of the run sold out inside the window":
            camp_block["drop_line"] += " Most of the run sold out inside the window."
    camp_block["talent_share"] = talent
    valuation = {
        "total_brand_value": 0.0,
        "brand_engagement_value": bev,
        "earned_media_value": 0.0,
        "brand_lift_value": 0.0,
        "conversion_value": conv_val,
        "incremental_users": 0,
        "rates": {**rates, "emv_per_user": emv_rates},
        "rate_sources": {"conv_value_per_user": _aov_line},
        "aov_basis": aov["basis"],
        "emv_breakdown": emv_breakdown,
        "methodology": str(prim.get("methodology") or ""),
        "talent_share": talent["talent_share"],
        "talent_share_basis": talent["basis"],
        "attributable_to_talent": 0.0,
    }

    payload = {
        "project_name": subject,
        "qualifier_type": inputs.get("qualifier_type") or "other",
        "qualifier_value": [inputs["qualifier"]],
        "brand_partner": inputs["brand_partner"],
        "start_date": windows["event"]["start"],
        "end_date": windows["event"]["end"],
        "attribution_window_days": windows["post"]["days"],
        "pre_period": windows["pre"],
        "event_period": windows["event"],
        "post_period": windows["post"],
        "audience_size": n,
        "projected_audience_size": projected,
        "pre_period_days": windows["pre"]["days"],
        "post_period_days": windows["post"]["days"],
        "totals": totals,
        "per_platform": per_platform,
        "conversions": conversions,
        "control_group": control_group,
        "demographics": normalize_demographics(
            prim.get("demographics") or {}, subject=salt,
            pre_users=pre_users, post_users=post_users),
        "sentiment": sentiment,
        "top_brand_properties": tbp,
        "top_brand_properties_pre": tbp_pre,
        "campaign": camp_block,
        "diagnostics": {
            "audience_filter_used": "qualifier_only",
            "brand_search_variants": _variants(inputs["brand_partner"]),
            "qualifier_search_variants": _variants(inputs["qualifier"]),
            "data_provenance": "synthetic_estimate",
            "synthesis_note": str(prim.get("synthesis_note") or ""),
            "post_window_clipped_to_today": windows["clipped"],
            "run_key": run_key(inputs),
        },
        "created_at": _dt.datetime.utcnow().isoformat() + "Z",
        "created_by": created_by,
        "valuation": valuation,
    }
    if inputs.get("campaign"):
        payload["campaign_name"] = str(inputs["campaign"])
    payload["windows"] = {"matched_pre_window": bool(windows.get("matched")),
                          "baseline_period": windows.get("baseline")}
    # Every lift lens (Earned Media incrementals, Brand Lift, the total,
    # attributable-to-partnership, her share) off the matched comparison
    # group, in place; the reader line rides the valuation.
    _mc.apply_lenses(payload, salt)
    _tl = payload["campaign"].get("talent_share") or {}
    if _tl:
        payload["campaign"]["talent_line"] = (
            f"{inputs['qualifier']}'s share: {float(_tl['talent_share']) * 100:.0f}% of the campaign's value, "
            f"${float(payload['valuation'].get('attributable_to_talent') or 0):,.0f}: "
            f"{_tl['basis']}.")
    return payload


def _m_people(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1e6:.1f}M"
    if v >= 10_000:
        return f"{int(round(v / 1000.0)):,}K"
    return f"{int(round(v)):,}"


class MemoryUnderpinnings:
    """In-process underpinnings store (tests)."""

    def __init__(self):
        self.rows: dict = {}

    def load(self, key):
        d = self.rows.get(key)
        return dict(d) if isinstance(d, dict) else None

    def save(self, key, doc):
        self.rows[key] = dict(doc)
        return True


class S3Underpinnings:
    """The per-run underpinnings on S3: the research primitives, the
    campaign anchors with their sources (internal only) and the README
    text (no sources). A repeat run with the same key reads these back
    instead of re-drawing; delete the document to force fresh research."""

    def __init__(self, s3, bucket):
        self.s3 = s3
        self.bucket = bucket

    def load(self, key):
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=underpinnings_key(key))
            doc = json.loads(obj["Body"].read().decode("utf-8"))
            return doc if isinstance(doc, dict) else None
        except Exception:
            return None

    def save(self, key, doc):
        try:
            self.s3.put_object(Bucket=self.bucket, Key=underpinnings_key(key),
                               Body=json.dumps(doc, indent=2, default=str).encode("utf-8"),
                               ContentType="application/json")
            return True
        except Exception as e:
            print(f"[bpiq-synth] underpinnings save failed: {e}")
            return False


def underpinnings_readme(inputs: dict, campaign: dict, aov: dict, payload: dict) -> str:
    """What the run is and how to read it, in plain words. Definitions
    and the campaign's own facts only: no source, no method word."""
    c = campaign or {}
    pb = c.get("price_band") or {}
    val = (payload or {}).get("valuation") or {}
    ev = (payload or {}).get("event_period") or {}
    post = (payload or {}).get("post_period") or {}
    lines = [
        f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}",
        f"Campaign: {inputs.get('campaign') or inputs.get('brand_partner')}",
        f"Campaign window: {ev.get('start')} to {ev.get('end')}",
        f"Attribution window: {post.get('start')} to {post.get('end')}",
        f"Run key: {run_key(inputs)}",
        "",
        "Definitions",
        "Audience: people in the talent's audience inside the window.",
        "Brand engagement: the share of that audience with at least one brand touchpoint, before and during the campaign.",
        "Conversions: the share of the audience with a purchase action for the brand in the window.",
        "Average order value: what one converting order carries, from the collection's own price points.",
        "Comparison group: a matched group of the same size with the same brand engagement before the campaign.",
        "",
        "Campaign facts",
    ]
    if c.get("drop_dates"):
        lines.append(f"Drop: {', '.join(c['drop_dates'][:3])}")
    if (c.get("retail") or {}).get("sold_at"):
        lines.append(f"Sold at: {', '.join(c['retail']['sold_at'][:6])}")
    if pb.get("low_usd") and pb.get("high_usd"):
        lines.append(f"Price band: ${pb['low_usd']:,.0f} to ${pb['high_usd']:,.0f} a piece")
    if c.get("collection_size"):
        lines.append(f"Pieces in the collection: {c['collection_size']}")
    so = c.get("sell_out") or {}
    if so.get("sold_out"):
        lines.append("Sold out " + (_plain_days(so.get("days_to_sell_out")) or "inside the window")
                     + (" (most of the collection)" if so.get("partial") else ""))
    if (c.get("restock") or {}).get("announced"):
        lines.append("Restock announced")
    if c.get("inventory_signal") in ("small", "medium", "large"):
        lines.append(f"Inventory: {c['inventory_signal']}")
    if (c.get("talent_role") or {}).get("role"):
        lines.append(f"Talent role: {c['talent_role']['role']}")
    cb = (payload or {}).get("campaign") or {}
    rc = cb.get("reach") or {}
    if rc.get("reach_us"):
        lines += ["", "Reach",
                  f"People reached in the window: {int(rc['reach_us']):,} ({cb.get('audience_basis', '')})"]
        for ln in rc.get("lines") or []:
            lines.append(f"  {ln.get('line')}: {int(ln.get('people') or 0):,} ({ln.get('basis')})")
        if rc.get("press_pieces"):
            lines.append(f"Press pieces counted: {int(rc['press_pieces'])}")
        if rc.get("events"):
            lines.append(f"Launch events counted: {int(rc['events'])}")
        if rc.get("views_total"):
            lines.append(f"Film and post views counted: {int(rc['views_total']):,}")
        if cb.get("talent_audience_in_window"):
            lines.append(f"The talent's own audience in the window: {int(cb['talent_audience_in_window']):,}")
    dr = cb.get("drop") or {}
    if dr.get("units_us"):
        lines += ["", "The drop",
                  f"Pieces sold in the US: {int(dr['units_us']):,} ({dr.get('basis')})",
                  f"Orders: {int(dr['orders']):,} at ${float(dr.get('per_order_usd') or 0):,.2f}",
                  f"Revenue at the collection's prices: ${float(dr.get('revenue_usd') or 0):,.2f}"]
    ts = cb.get("talent_share") or {}
    if ts:
        lines += ["", "Her share",
                  f"Share of the campaign's value: {float(ts.get('talent_share') or 0) * 100:.1f}% ({ts.get('basis')})",
                  f"Attributable to the talent: ${float(val.get('attributable_to_talent') or 0):,.2f}"]
    lines += [
        "",
        f"Average order value used: ${float(aov.get('aov') or 0):,.2f} "
        f"(a basket of about {float(aov.get('basket') or 1.3):.1f} pieces)",
        f"Total brand value: ${float(val.get('total_brand_value') or 0):,.2f}",
    ]
    return "\n".join(lines)


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = "prometheus",
               underpinnings=None, job_id: str = "",
               force_research: bool = False) -> dict:
    """Research + reason the primitives, then derive the payload.

    `underpinnings` (load(key) / save(key, doc)) keeps the research per
    run key: a saved document is read back and nothing is re-drawn; a
    fresh run researches the campaign (price, drop, sell-out, the
    footprint) and the audience primitives, then saves both with the
    README. `force_research` (an operator correction in place) ignores
    the saved document and researches again under the same key."""
    windows = _dates(inputs)
    key = run_key(inputs)
    saved = None
    if underpinnings is not None and not force_research:
        try:
            saved = underpinnings.load(key)
        except Exception:
            saved = None
    campaign = None
    prim = None
    if isinstance(saved, dict) and isinstance(saved.get("primitives"), dict) \
            and saved["primitives"].get("audience_size"):
        prim = dict(saved["primitives"])
        campaign = normalize_campaign(saved.get("campaign") or {}, inputs)
        print(f"[bpiq-synth] underpinnings read back for {key}; no re-draw")
    if campaign is None:
        campaign = research_campaign(inputs, claude_json, tools=tools)
        print(f"[bpiq-synth] campaign research: {len(campaign.get('products') or [])} priced pieces, "
              f"band {campaign.get('price_band')}, sold out {campaign.get('sell_out', {}).get('sold_out')}, "
              f"confidence {campaign.get('confidence')}")
    # Corpus catalog (2026-10-05): every figure already published on the
    # qualifier (its Profile IQ size, earlier partnership reads, journeys,
    # chat answers) and on the brand partner rides the research prompt as
    # binding context. Fail-safe to nothing.
    published = {}
    try:
        from migration import corpus_catalog as _cc
        ev = windows.get("event") or {}
        win = {"start": ev.get("start"), "end": ev.get("end")} if isinstance(ev, dict) else None
        for label, name in (("qualifier", inputs.get("qualifier")),
                            ("brand_partner", inputs.get("brand_partner")),
                            ("partnership", f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}")):
            blk = _cc.anchors_block(_cc.anchors_for(str(name or ""), window=win), max_lines=24)
            if blk:
                published[label] = blk
    except Exception as e:
        print(f"[bpiq-synth] catalog anchors skipped: {e}")
    campaign_facts = {k: campaign.get(k) for k in (
        "drop_dates", "campaign_window_note", "retail", "price_band", "collection_size",
        "sell_out", "restock", "inventory_signal", "inventory_note", "talent_role") if campaign.get(k) not in (None, "", [], {})}
    # the campaign's reach and its drop (2026-10-28): binding on the
    # audience primitive when the footprint is known
    from migration import bpiq_campaign as _camp
    _fp = campaign.get("footprint") or {}
    reach_ctx = None
    drop_ctx = None
    if _camp.has_footprint(_fp):
        _reach = _camp.derive_reach(_fp, campaign, run_salt(inputs), windows["event"]["days"])
        if int(_reach.get("reach_us") or 0) > 0:
            reach_ctx = {"reach_us": int(_reach["reach_us"]),
                         "panel_count": int(round(int(_reach["reach_us"]) / _camp.PANEL_WEIGHT)),
                         "lines": [{"line": ln["line"], "people": ln["people"]} for ln in (_reach.get("lines") or [])],
                         "press_pieces": _reach.get("press_pieces"), "film_and_post_views": _reach.get("views_total")}
        if campaign.get("collection_size") or campaign.get("products"):
            _aov0 = derive_aov(campaign, str(campaign.get("category") or ""), run_salt(inputs),
                               caller_rate=(inputs.get("rates") or {}).get("conv_value_per_user"))
            _drop = _camp.derive_drop(campaign, _fp, _aov0, run_salt(inputs))
            if int(_drop.get("orders") or 0) > 0:
                drop_ctx = {"units_us": _drop["units_us"], "orders": _drop["orders"],
                            "revenue_usd": _drop["revenue_usd"], "basis": _drop["basis"]}
    user_prompt = json.dumps({
        "brand_partner": inputs["brand_partner"],
        "qualifier": inputs["qualifier"],
        "qualifier_type": inputs.get("qualifier_type") or "other",
        "audience": inputs.get("audience") or "",
        "pre_period": windows["pre"],
        "event_period": windows["event"],
        "post_period": windows["post"],
        # the campaign context the ask carried (2026-10-16): the named
        # initiative and the creator's own links identify the exact
        # partner (MMG = the YouTube/TikTok creator, not a label) and
        # the assets the research should start from
        **({"campaign": inputs["campaign"]} if inputs.get("campaign") else {}),
        **({"campaign_links": list(inputs["links"])[:12],
            "campaign_links_rule": ("The partner is the account behind these links. Research them first: "
                                    "the channel or handle, its size, and the sponsored posts for the brand.")}
           if inputs.get("links") else {}),
        # the campaign research already done (2026-10-28): the drop, where
        # it sold, the price band, the sell-out and the talent's role bind
        # the levels; a sell-out means the event-window conversion share
        # and the platforms that carried creative moved hard and early
        **({"campaign_facts": campaign_facts,
            "campaign_facts_rule": ("Binding. A collection that sold out reads as strong demand: "
                                    "conversions and brand touchpoints in the event window sit at "
                                    "the high end of what the audience supports, concentrated in "
                                    "the first days. Small inventory tempers the read; say so in "
                                    "synthesis_note.")}
           if campaign_facts else {}),
        **({"campaign_reach": reach_ctx,
            "campaign_reach_rule": ("Binding. The audience being valued is the people the campaign reached: "
                                    "audience_size = panel_count and projected_audience_size = reach_us. "
                                    "Brand engagement shares, platform shares, demographics and sentiment "
                                    "describe THIS reached audience (the brand's own shoppers, the fashion "
                                    "press readers, the people the film and the posts reached), not only the "
                                    "talent's followers. The pre window is the same length right before the "
                                    "campaign.")}
           if reach_ctx else {}),
        **({"campaign_drop": drop_ctx,
            "campaign_drop_rule": ("Binding. The event-window converters are the drop's orders; set "
                                   "conversions.post_users_pct to orders / reach_us x 100 and reason the "
                                   "pre-window share for the same audience and window length.")}
           if drop_ctx else {}),
        **({"published_figures": published,
            "published_figures_rule": ("Binding. The audience size and every count "
                                       "must agree with these where they overlap and "
                                       "sit inside them for a sub-window.")}
           if published else {}),
    })
    if prim is None:
        prim = claude_json(research_prompt(), user_prompt,
                           max_tokens=9000, temperature=0.25,
                           surface="bpiq_synthesis", tools=tools)
        if not isinstance(prim, dict) or not prim.get("audience_size"):
            raise RuntimeError("bpiq research returned no primitives")
        # Catalog hold (2026-10-06): the qualifier's audience inside the
        # event window can never exceed the profile the dashboard already
        # holds for it (scaled for a shorter window). Held at the primitive
        # so every derived count, lift and value moves with it. When the
        # audience is the campaign's reach (2026-10-28) the hold does not
        # apply to it; the talent's own window audience is read from the
        # profile and kept beside the reach.
        if reach_ctx:
            try:
                tw = talent_window_audience(inputs, windows)
                if tw:
                    prim["_talent_audience_in_window"] = int(tw)
            except Exception as e:
                print(f"[bpiq-synth] talent window audience skipped: {e}")
            try:
                if int(prim.get("audience_size") or 0) < int(reach_ctx["panel_count"]):
                    prim["audience_size"] = int(reach_ctx["panel_count"])
                    prim["projected_audience_size"] = int(reach_ctx["reach_us"])
            except (TypeError, ValueError):
                pass
        else:
            try:
                prim = hold_audience_to_catalog(inputs, prim, windows)
            except Exception as e:
                print(f"[bpiq-synth] catalog hold skipped: {e}")
        if not campaign.get("category") and prim.get("category"):
            campaign["category"] = str(prim.get("category") or "").strip().upper()
    payload = build_payload(inputs, prim, created_by=created_by, campaign=campaign)
    # The matching rules hold on every published valuation regardless
    # of what the model returned: a comparison group off in size or
    # pre-period engagement is re-drawn in place, never held.
    _cg = payload.get("control_group") or {}
    if _cg.get("enabled", True):
        payload, _mc_notes = _mc.enforce_matched_control(
            payload, subject=run_salt(inputs))
        if _mc_notes:
            print(f"[bpiq-synth] comparison group corrected: {_mc_notes[:3]}")
    if underpinnings is not None and saved is None:
        try:
            aov_doc = {"aov": (payload.get("valuation") or {}).get("rates", {}).get("conv_value_per_user"),
                       "basis": (payload.get("valuation") or {}).get("aov_basis"),
                       "basket": None}
            try:
                aov_doc["basket"] = derive_aov(campaign, str(prim.get("category") or campaign.get("category") or ""),
                                               run_salt(inputs),
                                               caller_rate=(inputs.get("rates") or {}).get("conv_value_per_user")).get("basket")
            except Exception:
                pass
            underpinnings.save(key, {
                "run_key": key, "job_id": str(job_id or ""),
                "inputs": {k: inputs.get(k) for k in (
                    "qualifier", "brand_partner", "campaign", "event_start", "event_end",
                    "post_start", "post_end", "control_group", "include_control", "links") if k in inputs},
                "windows": windows,
                "campaign": campaign,          # anchors and their sources: internal only
                "primitives": prim,
                "aov": aov_doc,
                "readme": underpinnings_readme(inputs, campaign, aov_doc, payload),
                "created_at": _dt.datetime.utcnow().isoformat() + "Z",
            })
            print(f"[bpiq-synth] underpinnings saved for {key}")
        except Exception as e:
            print(f"[bpiq-synth] underpinnings save skipped: {e}")
    if validate_bpiq_payload is not None:
        try:
            issues = validate_bpiq_payload(payload)
            if issues:
                print(f"[bpiq-synth] validator notes ({len(issues)}): "
                      f"{issues[:4]}")
        except Exception as e:
            print(f"[bpiq-synth] validator skipped: {e}")
    payload["_bpiq_category"] = str(
        prim.get("category") or "").strip().upper() or None
    # Every string a reader sees passes the house vocabulary +
    # method-language scrub (2026-10-06); identifiers and dates stay.
    try:
        from prometheus import guards as _g
        _g.scrub_tree(payload)
    except Exception as e:
        print(f"[bpiq-synth] text scrub skipped: {e}")
    return payload


def talent_window_audience(inputs: dict, windows: dict) -> Optional[int]:
    """The talent's own audience inside the event window, projected to
    US people, from the Profile IQ on the dashboard (sample x window
    scale x the panel weight). None when no profile is held."""
    from migration import corpus_catalog as _cc
    ev = windows.get("event") or {}
    win = {"start": ev.get("start"), "end": ev.get("end")}
    anchors = _cc.anchors_for(str(inputs.get("qualifier") or ""), window=win, with_ledger=False)
    pa = _cc.profile_anchor(anchors, win)
    if not pa or not pa.get("sample_size"):
        return None
    ps = float(pa["sample_size"])
    pw = pa.get("window") or {}
    try:
        lp = (_dt.date.fromisoformat(str(pw["end"])) - _dt.date.fromisoformat(str(pw["start"]))).days + 1
    except Exception:
        lp = 365
    lw = int(ev.get("days") or 14)
    scale = min(1.0, (float(lw) / float(max(1, lp))) ** 0.5)
    h = int(hashlib.sha256(f"{run_salt(inputs)}|bpiq-talent-window".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    people = ps * scale * (0.90 + 0.07 * h) * 32.99
    v = int(people)
    if v % 10 == 0:
        v += 1 + int(h * 8)
    return v


def hold_audience_to_catalog(inputs: dict, prim: dict, windows: dict) -> dict:
    """Ceiling on the research audience primitive from the qualifier's
    Profile IQ on the dashboard (sample individuals). A same-or-longer
    event window may reach the profile's sample; a shorter one scales
    by the square root of the window ratio. Only ever pulls down, with
    a salted margin under the ceiling so nothing pins."""
    from migration import corpus_catalog as _cc
    ev = windows.get("event") or {}
    win = {"start": ev.get("start"), "end": ev.get("end")}
    anchors = _cc.anchors_for(str(inputs.get("qualifier") or ""), window=win, with_ledger=False)
    pa = _cc.profile_anchor(anchors, win)
    if not pa or not pa.get("sample_size"):
        return prim
    ps = float(pa["sample_size"])
    pw = pa.get("window") or {}
    try:
        lp = (_dt.date.fromisoformat(str(pw["end"])) - _dt.date.fromisoformat(str(pw["start"]))).days + 1
        lw = int(ev.get("days") or 0) or (
            (_dt.date.fromisoformat(str(win["end"])) - _dt.date.fromisoformat(str(win["start"]))).days + 1)
    except Exception:
        lp, lw = 365, 365
    scale = min(1.0, (float(lw) / float(max(1, lp))) ** 0.5)
    ceiling = ps * scale
    cur = float(prim.get("audience_size") or 0)
    if cur <= ceiling or ceiling <= 0:
        return prim
    h = int(hashlib.sha256(f"{run_salt(inputs)}|bpiq-hold".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    held = int(ceiling * (0.90 + 0.07 * h))
    if held % 10 == 0:
        held += 1 + int(h * 8)
    print(f"[bpiq-synth] audience held to the dashboard profile: {int(cur):,} -> {held:,} "
          f"(profile sample {int(ps):,}, window scale {scale:.3f})")
    prim = dict(prim)
    prim["audience_size"] = held
    prim["_held_to_profile"] = pa.get("source_key")
    return prim


def s3_key_for(inputs: dict) -> str:
    stamp = time.strftime("%m_%d_%Y_%H_%M")
    slug = re.sub(r"[^A-Za-z0-9]+", "_",
                  f"{inputs['qualifier']} x {inputs['brand_partner']}")
    slug = re.sub(r"_+", "_", slug).strip("_")
    return f"{S3_PREFIX}{slug}_{stamp}.json"
