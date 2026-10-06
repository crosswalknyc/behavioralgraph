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
}
DEFAULT_RATES = {
    "bev_per_user": 2.5,
    "blv_per_incr_user": 5.0,
    "conv_value_per_user": 30.0,
}

BPIQ_CATEGORIES = ["BEAUTY", "AUTOMOTIVE", "FASHION", "CPG",
                   "TECHNOLOGY", "DINING", "ENTERTAINMENT", "SPORTS"]


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
  "missing": [str, ...]        // which of brand_partner / qualifier /
                               // event window are still missing
}
Month-year inputs ("Apr 2024") map to the first of the month for
starts and the last day of the month for ends. "through today" or an
open post window maps to null post_end (the builder clips to today).
Never invent a brand or dates the user did not give; list what is
missing in "missing"."""


RESEARCH_SYSTEM_PROMPT = """You are valuing a brand partnership from a
10M-consumer US clickstream panel. Research the partnership (web search
when available), then reason the measurement primitives. Everything
must read as observed panel data: messy values, no round numbers, no
two identical values. Return STRICT JSON only:

{
  "audience_size": int,            // panel consumers in the qualifier
                                   // audience (hundreds to low
                                   // thousands; messy, never round)
  "projected_audience_size": int,  // real-world US audience it
                                   // projects to (research-anchored;
                                   // messy)
  "category": str,                 // one of %s
  "totals": {"pre_pen_pct": float, "post_pen_pct": float},
                                   // share of the audience with ANY
                                   // brand touchpoint in pre vs event
                                   // window (post > pre for a working
                                   // partnership; both 4dp-messy)
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
                                   // intent brand actions); small
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
  "gen_pop_drift_pp": float,       // background brand drift in the
                                   // same window for gen pop (small,
                                   // well under the treatment delta)
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
    today = _dt.date.today()
    ev_s = _dt.date.fromisoformat(inputs["event_start"])
    ev_e = _dt.date.fromisoformat(inputs["event_end"])
    if inputs.get("pre_start") and inputs.get("pre_end"):
        pre_s = _dt.date.fromisoformat(inputs["pre_start"])
        pre_e = _dt.date.fromisoformat(inputs["pre_end"])
    else:
        pre_e = ev_s - _dt.timedelta(days=1)
        pre_s = pre_e - _dt.timedelta(days=364)
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
                  "days": (ev_e - ev_s).days + 1},
        "post": {"start": post_s.isoformat(), "end": post_e.isoformat(),
                 "days": (post_e - post_s).days + 1},
        "clipped": clipped,
    }


def _variants(name: str) -> list:
    base = re.sub(r"[^a-z0-9 ]", "", str(name).lower()).strip()
    joined = base.replace(" ", "")
    return list(dict.fromkeys(
        [base, joined, base.replace(" ", "-"), base.replace(" ", "_")]))


def build_payload(inputs: dict, prim: dict, *,
                  created_by: str = "prometheus") -> dict:
    """Derive the full BPIQ payload from reasoned primitives.

    Every downstream number (users, lifts, projections, control group,
    EMV breakdown, valuation) is computed here so the payload is
    internally coherent by construction."""
    subject = f"{inputs['qualifier']} x {inputs['brand_partner']}"
    windows = _dates(inputs)
    n = int(prim["audience_size"])
    projected = int(prim["projected_audience_size"])
    weight = projected / float(n)

    def proj(v):
        return _messy((subject, "proj", v), v * weight)

    # ---- totals ----------------------------------------------------------
    pre_pct = float(prim["totals"]["pre_pen_pct"])
    post_pct = float(prim["totals"]["post_pen_pct"])
    pre_users = _messy((subject, "pre_u"), n * pre_pct / 100.0)
    post_users = _messy((subject, "post_u"), n * post_pct / 100.0)
    hits_mult_pre = 2.0 + (_h(subject, "hm_pre") % 140) / 100.0
    hits_mult_post = 2.2 + (_h(subject, "hm_post") % 160) / 100.0
    pre_hits = _messy((subject, "pre_h"), pre_users * hits_mult_pre)
    post_hits = _messy((subject, "post_h"), post_users * hits_mult_post)
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
        on_pre = _messy((subject, p, "on_pre"),
                        n * float(row["platform_share_pre"]))
        on_post = _messy((subject, p, "on_post"),
                         n * float(row["platform_share_post"]))
        pu = _messy((subject, p, "pu"),
                    on_pre * float(row["brand_pen_pre_pct"]) / 100.0)
        qu = _messy((subject, p, "qu"),
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
    conv_pre_u = _messy((subject, "cv_pre"),
                        n * float(cv.get("pre_users_pct", 0)) / 100.0)
    conv_post_u = _messy((subject, "cv_post"),
                         n * float(cv.get("post_users_pct", 0)) / 100.0)
    conversions = {
        "pre_hits": _messy((subject, "cvh_pre"), conv_pre_u * 1.3),
        "post_hits": _messy((subject, "cvh_post"), conv_post_u * 1.4),
        "pre_users": conv_pre_u, "post_users": conv_post_u,
        "pre_users_projected": proj(conv_pre_u),
        "post_users_projected": proj(conv_post_u),
        "low_signal": conv_post_u < 25,
        "lift_pct_hits": 0.0, "lift_pct_users": round(
            (conv_post_u - conv_pre_u) / conv_pre_u * 100, 2)
        if conv_pre_u else 0.0,
        "enabled": conv_post_u > 0,
    }

    # ---- control group (gen pop drift) -----------------------------------
    drift_pp = float(prim.get("gen_pop_drift_pp", 0.4))
    # The Gen Pop cohort is SIZE-MATCHED to the partner audience: same
    # panel count, same projection, so the two deltas read on the same
    # base (dashboard contract; every hand-built read carries
    # control_size == audience_size). 2026-10-05, Jenna on Willow Smith
    # x Free People: "why is the willow control bigger than the
    # target? shouldnt they be the same number".
    ctrl_n = n
    c_pre_pct = max(pre_pct * 0.22 + (_h(subject, "cp") % 70) / 100.0, 0.3)
    c_post_pct = c_pre_pct + drift_pp
    c_pre = _messy((subject, "c_pre"), ctrl_n * c_pre_pct / 100.0)
    c_post = _messy((subject, "c_post"), ctrl_n * c_post_pct / 100.0)
    control_group = {
        "enabled": True,
        "control_size": ctrl_n,
        "projected_control_size": projected,
        "control_pre_users": c_pre, "control_post_users": c_post,
        "control_pre_hits": _messy((subject, "ch_pre"), c_pre * 2.1),
        "control_post_hits": _messy((subject, "ch_post"), c_post * 2.2),
        "treat_pre_pen_pct": totals["audience_pen_pre_pct"],
        "treat_post_pen_pct": totals["audience_pen_post_pct"],
        "control_pre_pen_pct": round(c_pre / ctrl_n * 100, 2),
        "control_post_pen_pct": round(c_post / ctrl_n * 100, 2),
        "treat_delta_pp": round(
            totals["audience_pen_post_pct"]
            - totals["audience_pen_pre_pct"], 2),
    }
    control_group["control_delta_pp"] = round(
        control_group["control_post_pen_pct"]
        - control_group["control_pre_pen_pct"], 2)
    control_group["incremental_lift_pp"] = round(
        control_group["treat_delta_pp"]
        - control_group["control_delta_pp"], 2)
    control_group["incremental_lift_rel_pct"] = (
        round(control_group["incremental_lift_pp"]
              / control_group["control_delta_pp"] * 100, 2)
        if control_group["control_delta_pp"] else None)
    control_group["control_lift_pct_users"] = round(
        (c_post - c_pre) / c_pre * 100, 2) if c_pre else 0.0

    # ---- sentiment --------------------------------------------------------
    s = prim.get("sentiment") or {}
    s_pre = {k: int(v) for k, v in (s.get("pre") or {}).items()}
    s_post = {k: int(v) for k, v in (s.get("post") or {}).items()}

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
            s.get("top_positive"), subject=subject, bucket="positive",
            pre_total=s_pre.get("positive", 0),
            post_total=s_post.get("positive", 0), limit=5),
        "top_negative": normalize_sentiment_clusters(
            s.get("top_negative"), subject=subject, bucket="negative",
            pre_total=s_pre.get("negative", 0),
            post_total=s_post.get("negative", 0), limit=3),
    }

    # ---- top brand properties --------------------------------------------
    tbp, tbp_pre = [], []
    for row in (prim.get("top_brand_properties") or [])[:12]:
        ph = _messy((subject, row["common_name"], "pre"),
                    float(row.get("pre_hits", 0)))
        qh = _messy((subject, row["common_name"], "post"),
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
    emv_rates = dict(EMV_RATES)
    emv_breakdown, emv_total = [], 0.0
    for row in per_platform:
        rate = emv_rates.get(row["platform"])
        if not rate:
            continue
        incr = row["post_users_projected"] - row["pre_users_projected"]
        if incr <= 0:
            continue
        incr = _messy((subject, row["platform"], "emv_incr"), incr)
        val = round(incr * rate, 2)
        emv_total += val
        emv_breakdown.append({
            "platform": row["platform"],
            "post_users_projected": row["post_users_projected"],
            "pre_users_projected": row["pre_users_projected"],
            "incremental_users_projected": incr,
            "emv_per_user_rate": rate,
            "emv_value": val,
        })
    emv_breakdown.sort(key=lambda r: -r["emv_value"])
    incr_users = max(totals["post_users_projected"]
                     - totals["pre_users_projected"], 0)
    # A difference of two messy counts can still land on a trailing
    # zero (Willow Smith x Free People: 410,880); keep it messy.
    incr_users = _messy((subject, "incr_users"), incr_users)
    bev = round(totals["post_users_projected"]
                * rates["bev_per_user"], 2)
    blv = round(incr_users * rates["blv_per_incr_user"], 2)
    conv_val = round(conversions["post_users_projected"]
                     * rates["conv_value_per_user"], 2)
    valuation = {
        "total_brand_value": round(bev + emv_total + blv + conv_val, 2),
        "brand_engagement_value": bev,
        "earned_media_value": round(emv_total, 2),
        "brand_lift_value": blv,
        "conversion_value": conv_val,
        "incremental_users": incr_users,
        "rates": {**rates, "emv_per_user": emv_rates},
        "emv_breakdown": emv_breakdown,
        "methodology": str(prim.get("methodology") or ""),
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
            prim.get("demographics") or {}, subject=subject,
            pre_users=pre_users, post_users=post_users),
        "sentiment": sentiment,
        "top_brand_properties": tbp,
        "top_brand_properties_pre": tbp_pre,
        "diagnostics": {
            "audience_filter_used": "qualifier_only",
            "brand_search_variants": _variants(inputs["brand_partner"]),
            "qualifier_search_variants": _variants(inputs["qualifier"]),
            "data_provenance": "synthetic_estimate",
            "synthesis_note": str(prim.get("synthesis_note") or ""),
            "post_window_clipped_to_today": windows["clipped"],
        },
        "created_at": _dt.datetime.utcnow().isoformat() + "Z",
        "created_by": created_by,
        "valuation": valuation,
    }
    return payload


def synthesize(inputs: dict, claude_json: Callable, *,
               tools: Optional[list] = None,
               created_by: str = "prometheus") -> dict:
    """Research + reason the primitives, then derive the payload."""
    windows = _dates(inputs)
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
    user_prompt = json.dumps({
        "brand_partner": inputs["brand_partner"],
        "qualifier": inputs["qualifier"],
        "qualifier_type": inputs.get("qualifier_type") or "other",
        "audience": inputs.get("audience") or "",
        "pre_period": windows["pre"],
        "event_period": windows["event"],
        "post_period": windows["post"],
        **({"published_figures": published,
            "published_figures_rule": ("Binding. The audience size and every count "
                                       "must agree with these where they overlap and "
                                       "sit inside them for a sub-window.")}
           if published else {}),
    })
    prim = claude_json(research_prompt(), user_prompt,
                       max_tokens=9000, temperature=0.6,
                       surface="bpiq_synthesis", tools=tools)
    if not isinstance(prim, dict) or not prim.get("audience_size"):
        raise RuntimeError("bpiq research returned no primitives")
    payload = build_payload(inputs, prim, created_by=created_by)
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
    return payload


def s3_key_for(inputs: dict) -> str:
    stamp = time.strftime("%m_%d_%Y_%H_%M")
    slug = re.sub(r"[^A-Za-z0-9]+", "_",
                  f"{inputs['qualifier']} x {inputs['brand_partner']}")
    slug = re.sub(r"_+", "_", slug).strip("_")
    return f"{S3_PREFIX}{slug}_{stamp}.json"
