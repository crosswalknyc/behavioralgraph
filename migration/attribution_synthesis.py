"""Attribution IQ tracking pull for Prometheus (Jenna 2026-09-22).

"we need prometheus to be able to pull data the way the attribution iq
pinned agent thread does ... charge someone $500 for the first setup
pull and then they should have the ability to toggle it on to refresh
daily (at 100$ x day) ... allow you to put in when you want it to stop
tracking ... the user to input all of their urls and tag them as paid
or organic, name the campaign, etc and then you would build it out the
way we have it built out in the attribution iq dashboard."

The build produces exactly the three artifacts the existing Attribution
IQ engine already consumes, so the dashboard renders the new campaign
natively with zero frontend changes:

  1. intent/registry.json           - campaign entry (enabled_tabs.mta)
  2. intent/<slug>/source/normalized_assets.json
  3. intent/<slug>/mta/coefficients_<as_of>.json
                                    - via mta_iq.compute_mta_coefficients

Daily tracking appends one coefficients_<date>.json per day (the tab's
day picker grows), driven by the tracker registry at
system/attribution_trackers.json and the 6 AM ET refresh sweep on the
engine host (migration/attribution_tracker_refresh.py). Tracking stops
by itself after the user's end date; prepaid days are never refunded.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse

S3_BUCKET = os.environ.get("INTENT_S3_BUCKET", "dashboard-inputs")
REGISTRY_KEY = "intent/registry.json"
TRACKERS_KEY = "system/attribution_trackers.json"

# What the guided collect must end up with. The parse call extracts
# from free text; url lines / pasted CSV go through parse_url_lines.
PARSE_SYSTEM_PROMPT = """You extract Attribution IQ tracking inputs
from a user's message. Return STRICT JSON only:
{
  "campaign_name": str|null,     // what the user calls the campaign
  "urls": [                      // every campaign URL they provided
    {"url": str,
     "tag": "paid"|"organic",    // as the user tagged it
     "label": str|null,          // their name for the asset, if given
     "channel": str|null}        // only if the user named it
  ],
  "conversion_event": str|null,  // one sentence: what counts as the
                                 // conversion (signup, purchase,
                                 // ticket, install)
  "end_tracking_date": "YYYY-MM-DD"|null,
                                 // when tracking should stop
  "daily_refresh": true|false|null,
                                 // did they ask for daily tracking
  "notes": str|null,
  "missing": [str, ...]          // which of campaign_name / urls /
                                 // conversion_event are still missing
                                 // (end_tracking_date + daily_refresh
                                 // only matter when daily tracking is
                                 // wanted)
}
Tags are the USER'S: never guess paid vs organic - a URL with no tag
belongs in missing as 'tags for N urls'. Never invent URLs."""

_CHANNEL_HOSTS = (
    ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
    ("instagram.com", "Instagram"), ("tiktok.com", "TikTok"),
    ("facebook.com", "Facebook"), ("fb.com", "Facebook"),
    ("x.com", "X"), ("twitter.com", "X"),
    ("snapchat.com", "Snapchat"), ("reddit.com", "Reddit"),
    ("pinterest.com", "Pinterest"), ("linkedin.com", "LinkedIn"),
    ("spotify.com", "Spotify"), ("hulu.com", "Hulu"),
    ("amazon.com", "Amazon"), ("google.com", "Google"),
    ("threads.net", "Threads"), ("twitch.tv", "Twitch"),
)


def channel_for_url(url: str) -> str:
    host = (urlparse(str(url or "")).netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for frag, label in _CHANNEL_HOSTS:
        if host == frag or host.endswith("." + frag):
            return label
    return (host.split(".")[0].title() or "Web") if host else "Web"


def parse_url_lines(text: str) -> list[dict]:
    """Deterministic extraction of tagged URLs from pasted lines OR
    pasted CSV text. Tolerant of both shapes:

      https://... paid My hero asset
      https://... , organic , Launch teaser
      URL,Tag,Label            (CSV header, any column order)

    Returns [{url, tag, label}] for every line carrying a URL + tag."""
    out = []
    body = str(text or "")
    # CSV attempt first when it looks like one
    if "," in body and re.search(r"https?://", body):
        try:
            rows = list(csv.reader(io.StringIO(body)))
            if rows:
                hdr = [str(c).strip().lower() for c in rows[0]]
                cols = {}
                for i, c in enumerate(hdr):
                    if "url" in c or "link" in c:
                        cols["url"] = i
                    elif "tag" in c or "type" in c or "paid" in c \
                            or "source" in c:
                        cols["tag"] = i
                    elif "label" in c or "name" in c or "asset" in c \
                            or "campaign" in c:
                        cols.setdefault("label", i)
                data = rows[1:] if "url" in cols else rows
                for r in data:
                    cells = [str(c).strip() for c in r]
                    u = next((c for c in cells
                              if c.lower().startswith("http")), "")
                    if not u:
                        continue
                    if "tag" in cols and cols["tag"] < len(cells):
                        rawtag = cells[cols["tag"]].lower()
                    else:
                        rawtag = " ".join(cells).lower()
                    tag = ("paid" if "paid" in rawtag else
                           "organic" if "organic" in rawtag else "")
                    label = ""
                    if ("label" in cols and cols["label"] < len(cells)
                            and not cells[cols["label"]]
                            .lower().startswith("http")):
                        label = cells[cols["label"]]
                    if tag:
                        out.append({"url": u, "tag": tag,
                                    "label": label})
        except Exception:
            pass
    if out:
        return out
    # line-by-line fallback
    for line in body.splitlines():
        m = re.search(r"(https?://\S+)", line)
        if not m:
            continue
        rest = (line[:m.start()] + " " + line[m.end():]).lower()
        tag = ("paid" if re.search(r"\bpaid\b", rest) else
               "organic" if re.search(r"\borganic\b", rest) else "")
        if not tag:
            continue
        label = re.sub(r"\b(paid|organic)\b", "",
                       line[m.end():], flags=re.I)
        label = re.sub(r"[\s,;|-]+", " ", label).strip()
        out.append({"url": m.group(1).rstrip(",;"),
                    "tag": tag, "label": label})
    return out


def slug_for_campaign(name: str, s3_client=None, registry=None) -> str:
    """Registry-unique slug from the campaign name. Underscore style
    matches the existing entries (chime_financial_mypay)."""
    base = re.sub(r"[^a-z0-9]+", "_",
                  str(name or "campaign").lower()).strip("_")[:48] \
        or "campaign"
    existing = set()
    try:
        reg = registry if registry is not None \
            else _load_registry(s3_client)
        existing = {str(e.get("title_slug") or "")
                    for e in (reg.get("titles") or [])}
    except Exception:
        pass
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}_{n}"
        n += 1
    return slug


def _s3(s3_client=None):
    if s3_client is not None:
        return s3_client
    import boto3
    return boto3.client("s3", region_name="us-east-2")


def _load_registry(s3_client=None) -> dict:
    """The live registry: {titles: [entry, ...], updated_at,
    schema_version, title_count}. LOUD on failure: a registry write
    built from a silently-empty load would clobber every campaign in
    the Attribution IQ tab (near-miss 2026-09-22, recovered via S3
    versioning) - so a load problem aborts the build instead."""
    s3 = _s3(s3_client)
    d = json.loads(s3.get_object(
        Bucket=S3_BUCKET, Key=REGISTRY_KEY)["Body"].read())
    if not isinstance(d, dict) or not isinstance(d.get("titles"), list):
        raise RuntimeError(
            "intent/registry.json does not carry a titles list; "
            "refusing to write")
    return d


def build_campaign(inputs: dict, requested_by: str = "",
                   s3_client=None, run_research=True) -> dict:
    """Create the campaign end to end: normalized assets from the
    user's tagged URLs, the registry entry (MTA tab enabled), and the
    first coefficients file. Returns {slug, display_name, asset_count,
    coefficients_key}. Raises ValueError on unusable inputs."""
    name = str(inputs.get("campaign_name") or "").strip()
    urls = [u for u in (inputs.get("urls") or [])
            if isinstance(u, dict) and str(u.get("url") or "").strip()
            and str(u.get("tag") or "").lower() in ("paid", "organic")]
    if not name:
        raise ValueError("campaign_name is required")
    if not urls:
        raise ValueError("at least one tagged URL is required")

    s3 = _s3(s3_client)
    reg = _load_registry(s3)  # loud on failure, before any writes
    slug = slug_for_campaign(name, registry=reg)
    today = date.today().isoformat()
    conversion = str(inputs.get("conversion_event") or "").strip()

    # Researched external anchors (fail-open): public view counts for
    # the URLs that carry them. The engine's read derives from the
    # asset structure either way; anchors sharpen the spread.
    ext_counts: dict = {}
    if run_research:
        try:
            ext_counts = _research_ext_counts(
                name, [u["url"] for u in urls][:40])
        except Exception:
            ext_counts = {}

    assets = []
    for u in urls:
        url = str(u["url"]).strip()
        aid = hashlib.md5(f"{slug}|{url}".encode()).hexdigest()[:16]
        label = str(u.get("label") or "").strip() \
            or _label_from_url(url)
        assets.append({
            "asset_id": aid,
            "title_slug": slug,
            "phase_name": "Campaign",
            "funnel_stage": "Exposure",
            "action_label": label,
            "asset_type": label,
            "channel": str(u.get("channel") or "").strip()
            or channel_for_url(url),
            "paid_or_organic": str(u["tag"]).lower(),
            "url": url,
            "source": ("Paid placement" if u["tag"] == "paid"
                       else "Owned / organic"),
            "note": conversion,
            "posted_date": str(inputs.get("start_date") or today),
            "talent_tags": [],
            "audience_target_tags": [],
            "ext_view_count": int(ext_counts.get(url) or 0),
            "ext_engagement_count": 0,
            "ext_engagement_source": ("public counts"
                                      if ext_counts.get(url) else ""),
            "thumbnail_s3_url": "",
            "og_metadata": "",
        })

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"intent/{slug}/source/normalized_assets.json",
        Body=json.dumps({"assets": assets}, indent=1).encode(),
        ContentType="application/json")

    entry = {
        "title_slug": slug,
        "display_name": name,
        "distributor": str(inputs.get("advertiser") or "").strip(),
        "opening_date": str(inputs.get("start_date") or today),
        "ticketing_open_date": str(inputs.get("start_date") or today),
        "source_xlsx_s3_key": "",
        "asset_count": len(assets),
        "phases": ["Campaign"],
        "audiences_of_interest": [],
        "image_url": None,
        "conversion_event": conversion,
        "requested_by": requested_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "enabled_tabs": {
            "overview": False, "assets": False, "questions": False,
            "conversion": False, "roi": False, "mta": True,
            "q1_engagement": False, "q2_paid_vs_organic": False,
            "audiences": False, "q3_intent_to_buy": False,
            "q4_talent": False, "q5_trailer": False,
            "q6_cohorts": False, "demographics": False,
            "journey": False,
        },
        "legacy_landing": False,
    }
    reg["titles"].append(entry)
    reg["title_count"] = len(reg["titles"])
    reg["updated_at"] = datetime.now(timezone.utc).isoformat()
    s3.put_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY,
                  Body=json.dumps(reg, indent=2).encode(),
                  ContentType="application/json")

    coeff_key = refresh_campaign(slug, s3_client=s3)
    return {"slug": slug, "display_name": name,
            "asset_count": len(assets),
            "coefficients_key": coeff_key}


def _label_from_url(url: str) -> str:
    p = urlparse(str(url))
    tail = (p.path or "/").rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"[-_+]+", " ", tail).strip()
    if not tail or len(tail) < 3:
        tail = channel_for_url(url) + " asset"
    return tail[:60].title()


def _research_ext_counts(campaign: str, urls: list) -> dict:
    """One search-enabled call estimating public view counts for the
    URLs that carry them (YouTube and similar). Fail-open {}."""
    try:
        try:
            from migration.claude_client import claude_reason_json
        except ImportError:
            from claude_client import claude_reason_json  # type: ignore
        listing = "\n".join(urls)
        out = claude_reason_json(
            ("For each URL below that publicly displays a view count "
             "(YouTube etc.), give the current approximate view count "
             "as an integer. Search the web. Return STRICT JSON: "
             "{\"counts\": {\"<url>\": <int>, ...}} with only the "
             "URLs you are confident about. Never guess."),
            f"Campaign: {campaign}\n{listing}",
            max_tokens=1500, web_search=True)
        counts = (out or {}).get("counts") or {}
        return {str(k): int(v) for k, v in counts.items()
                if isinstance(v, (int, float)) and v > 0}
    except Exception:
        return {}


def refresh_campaign(slug: str, as_of: str = "",
                     s3_client=None) -> str:
    """One day's read: computes and persists
    intent/<slug>/mta/coefficients_<as_of>.json (the daily-append
    model - each refresh adds a day the tab can select). Returns the
    S3 key written."""
    as_of = as_of or date.today().isoformat()
    import sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    import mta_iq
    mta_iq.compute_mta_coefficients(slug, as_of=as_of, use_cache=False)
    return f"intent/{slug}/mta/coefficients_{as_of}.json"


# ── tracker registry (the daily 6 AM ET sweep reads this) ──────────────


def load_trackers(s3_client=None) -> list:
    s3 = _s3(s3_client)
    try:
        d = json.loads(s3.get_object(
            Bucket=S3_BUCKET, Key=TRACKERS_KEY)["Body"].read())
        return d.get("trackers", []) if isinstance(d, dict) else []
    except Exception:
        return []


def save_trackers(trackers: list, s3_client=None):
    s3 = _s3(s3_client)
    s3.put_object(Bucket=S3_BUCKET, Key=TRACKERS_KEY,
                  Body=json.dumps({"trackers": trackers},
                                  indent=1).encode(),
                  ContentType="application/json")


def register_tracker(slug: str, campaign: str, user: str,
                     daily: bool, end_date: str,
                     s3_client=None) -> dict:
    entry = {
        "slug": slug, "campaign": campaign, "user": user,
        "daily": bool(daily), "end_date": str(end_date or ""),
        "active": bool(daily),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_refreshed": date.today().isoformat(),
    }
    trackers = load_trackers(s3_client)
    trackers.append(entry)
    save_trackers(trackers, s3_client)
    return entry


def stop_tracker(slug: str, s3_client=None) -> bool:
    """User-initiated stop. Prepaid days are not refunded (Jenna
    2026-09-22: charged up front through the chosen end date)."""
    trackers = load_trackers(s3_client)
    hit = False
    for t in trackers:
        if t.get("slug") == slug and t.get("active"):
            t["active"] = False
            t["stopped_at"] = datetime.now(timezone.utc).isoformat()
            hit = True
    if hit:
        save_trackers(trackers, s3_client)
    return hit


def trackers_due(today: str = "", s3_client=None) -> list:
    """Active daily trackers that still owe today's refresh. A tracker
    past its end date deactivates (tracking simply stops - the window
    was prepaid)."""
    today = today or date.today().isoformat()
    trackers = load_trackers(s3_client)
    due, changed = [], False
    for t in trackers:
        if not t.get("active") or not t.get("daily"):
            continue
        if t.get("end_date") and str(t["end_date"]) < today:
            t["active"] = False
            t["completed_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
            continue
        if str(t.get("last_refreshed") or "") < today:
            due.append(t)
    if changed:
        save_trackers(trackers, s3_client)
    return due


def mark_refreshed(slug: str, today: str = "", s3_client=None):
    today = today or date.today().isoformat()
    trackers = load_trackers(s3_client)
    for t in trackers:
        if t.get("slug") == slug:
            t["last_refreshed"] = today
    save_trackers(trackers, s3_client)
