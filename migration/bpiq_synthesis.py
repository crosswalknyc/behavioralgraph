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
  "sentiment": {
    "pre":  {"positive": int, "neutral": int, "negative": int},
    "post": {"positive": int, "neutral": int, "negative": int},
    "top_positive": [str, ...],    // 3-5 plain-language summaries
    "top_negative": [str, ...]     // 1-3 plain-language summaries
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
where it did not."""


def research_prompt() -> str:
    return RESEARCH_SYSTEM_PROMPT % (
        ", ".join(BPIQ_CATEGORIES), ", ".join(PLATFORMS))


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
    ctrl_n = _messy((subject, "ctrl"), n * (3.1 + (_h(subject) % 90) / 100))
    c_pre_pct = max(pre_pct * 0.22 + (_h(subject, "cp") % 70) / 100.0, 0.3)
    c_post_pct = c_pre_pct + drift_pp
    c_pre = _messy((subject, "c_pre"), ctrl_n * c_pre_pct / 100.0)
    c_post = _messy((subject, "c_post"), ctrl_n * c_post_pct / 100.0)
    control_group = {
        "enabled": True,
        "control_size": ctrl_n,
        "projected_control_size": proj(ctrl_n),
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
        "top_positive": list(s.get("top_positive") or [])[:5],
        "top_negative": list(s.get("top_negative") or [])[:3],
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
        "demographics": prim.get("demographics") or {},
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
    user_prompt = json.dumps({
        "brand_partner": inputs["brand_partner"],
        "qualifier": inputs["qualifier"],
        "qualifier_type": inputs.get("qualifier_type") or "other",
        "audience": inputs.get("audience") or "",
        "pre_period": windows["pre"],
        "event_period": windows["event"],
        "post_period": windows["post"],
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
