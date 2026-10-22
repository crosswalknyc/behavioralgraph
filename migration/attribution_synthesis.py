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


# ── adding assets to a campaign that already exists (2026-10-16) ─────────
# Alexia uploaded 1,156 creator clips for The Influencer Project and asked
# to "add these assets to the attribution iq report". The campaign keeps
# its name, its conversion and its history; the new assets join the
# normalized set (deduped on URL), get a measured view count where the
# page shows one (TikTok, YouTube), and the day's read is recomputed.

_HANDLE_RX = re.compile(r"(?:tiktok\.com|instagram\.com|x\.com|twitter\.com|youtube\.com|threads\.net|facebook\.com)/@?([A-Za-z0-9_.-]{2,40})", re.I)


def handle_from_url(url: str) -> str:
    """'@grimmofhorror' from a TikTok, Instagram, X or YouTube URL, or ''.
    Instagram post and reel links carry no handle."""
    m = _HANDLE_RX.search(str(url or ""))
    if not m:
        return ""
    h = m.group(1)
    if h.lower() in ("p", "reel", "reels", "watch", "shorts", "video", "status", "share", "embed", "i", "tv", "stories", "explore", "channel", "c"):
        return ""
    return "@" + h.lstrip("@")


def fetch_public_count(url: str, timeout: int = 10):
    """(views, caption) read off a public TikTok or YouTube page, or
    (None, ''). One GET, no key, fail-safe. Instagram and X need a login
    and return nothing."""
    m = fetch_public_meta(url, timeout=timeout)
    return m.get("views"), m.get("caption") or ""


_PAGE_DATE_RXS = (
    re.compile(r'"uploadDate":\s*"(\d{4}-\d{2}-\d{2})'),          # YouTube microformat
    re.compile(r'"publishDate":\s*"(\d{4}-\d{2}-\d{2})'),
    re.compile(r'<meta itemprop="uploadDate" content="(\d{4}-\d{2}-\d{2})'),
    re.compile(r'"datePublished":\s*"(\d{4}-\d{2}-\d{2})'),
)
_TIKTOK_CREATE_RX = re.compile(r'"createTime":\s*"?(\d{9,11})"?')


def _page_posted_date(html: str) -> str:
    """The day a public page says its video was posted (YouTube
    uploadDate / publishDate, TikTok createTime), as YYYY-MM-DD, or ''.
    The date a clip posted decides its campaign phase, so a sheet row
    that arrives without one takes the page's date before it ever falls
    back to the day of the upload (2026-10-22, The Influencer Project:
    31 YouTube shorts arrived dateless, were dated the upload day and
    tagged to the first phase)."""
    for rx in _PAGE_DATE_RXS:
        m = rx.search(html or "")
        if m:
            return m.group(1)
    m = _TIKTOK_CREATE_RX.search(html or "")
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc).date().isoformat()
        except Exception:
            return ""
    return ""


def fetch_public_meta(url: str, timeout: int = 10) -> dict:
    """{'views', 'caption', 'posted_date'} read off a public TikTok or
    YouTube page; every field empty when the page carries nothing or the
    URL is another platform. One GET, no key, fail-safe."""
    u = str(url or "")
    empty = {"views": None, "caption": "", "posted_date": ""}
    if not re.search(r"(tiktok\.com/@[^/]+/video/|youtube\.com/(?:watch|shorts/)|youtu\.be/)", u, re.I):
        return empty
    try:
        import urllib.request
        req = urllib.request.Request(u, headers={"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
                                                 "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(2_500_000).decode("utf-8", "ignore")
        m = re.search(r'"playCount":(\d+)', html) or re.search(r'"viewCount":"(\d+)"', html) or re.search(r'"viewCount":\{"simpleText":"([\d,]+)', html)
        views = int(m.group(1).replace(",", "")) if m else None
        cap = ""
        mc = re.search(r'"desc":"((?:[^"\\]|\\.){1,300})"', html) if "tiktok.com" in u else re.search(r'<meta name="title" content="([^"]{1,200})"', html)
        if mc:
            if "tiktok.com" in u:
                try:
                    cap = json.loads('"' + mc.group(1) + '"')   # the page's JSON string, escapes and all
                except Exception:
                    cap = ""
            else:
                import html as _html
                cap = _html.unescape(mc.group(1))
        return {"views": views, "caption": " ".join(str(cap).split())[:200], "posted_date": _page_posted_date(html)}
    except Exception:
        return empty


_POST_ID_RX = re.compile(r"(/video/\d+|/p/[A-Za-z0-9_-]+|/reel(?:s)?/[A-Za-z0-9_-]+|/status/\d+|[?&]v=[A-Za-z0-9_-]{6,}|/shorts/[A-Za-z0-9_-]{6,}|youtu\.be/[A-Za-z0-9_-]{6,})", re.I)


def is_profile_level_url(url: str) -> bool:
    """A creator's profile link rather than a post ('tiktok.com/@cvnela'):
    the shape a media plan records a placement under before the post
    goes live."""
    u = str(url or "")
    return bool(handle_from_url(u)) and not _POST_ID_RX.search(u)


def _phase_starts(doc: dict, assets: list) -> list:
    """[(phase_name, start_date)] in start order: the campaign's declared
    phase starts (the snapshot's phases block) first; a phase with no
    declared start takes the earliest posted date among its own assets.
    A phase nothing dates is left out."""
    declared = {}
    for p in ((doc or {}).get("phases") or []):
        if isinstance(p, dict) and str(p.get("phase_name") or "").strip() and str(p.get("start_date") or "").strip():
            declared[str(p["phase_name"]).strip()] = str(p["start_date"]).strip()[:10]
    earliest = {}
    for a in assets or []:
        if not isinstance(a, dict):
            continue
        ph, d = str(a.get("phase_name") or "").strip(), str(a.get("posted_date") or "").strip()[:10]
        if ph and d and ph not in declared and (ph not in earliest or d < earliest[ph]):
            earliest[ph] = d
    return sorted({**earliest, **declared}.items(), key=lambda kv: kv[1])


def phase_by_date(posted: str, ordered: list, default: str = "Campaign") -> str:
    """The phase a posted date falls in: the latest phase whose start is on
    or before the date; a date before every start joins the first phase."""
    cur = ordered[0][0] if ordered else default
    d = str(posted or "")[:10]
    for ph, start in ordered:
        if d and d >= start:
            cur = ph
    return cur


def refresh_phase_counts(doc: dict) -> dict:
    """{phase: {asset_count, view_count}} from the assets' own tags, written
    into the snapshot's demographics.phases rows (the Phases table reads
    them). Demographic shapes are left as they are."""
    assets = (doc or {}).get("assets") or []
    by: dict = {}
    for a in assets:
        if not isinstance(a, dict):
            continue
        ph = str(a.get("phase_name") or "").strip() or "Unphased"
        b = by.setdefault(ph, {"asset_count": 0, "view_count": 0})
        b["asset_count"] += 1
        b["view_count"] += int(a.get("ext_view_count") or 0)
    demo = (doc or {}).get("demographics")
    if isinstance(demo, dict):
        rows = [p for p in (demo.get("phases") or []) if isinstance(p, dict)]
        seen = set()
        for p in rows:
            ph = str(p.get("phase_name") or "")
            if ph in by:
                p["asset_count"], p["view_count"] = by[ph]["asset_count"], by[ph]["view_count"]
            seen.add(ph)
        for ph, c in by.items():
            if ph not in seen:
                rows.append({"phase_name": ph, **c})
        demo["phases"] = rows
    return by


def _parse_sheet_date(raw) -> str:
    """A sheet's date as YYYY-MM-DD ('2026-10-05', '10/5/2026',
    '2026-10-05T12:00:00'), or '' when it carries none."""
    posted = str(raw or "").strip()
    if not posted:
        return ""
    for cand in (posted[:10], posted):
        try:
            return datetime.fromisoformat(cand).date().isoformat()
        except Exception:
            pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(posted, fmt).date().isoformat()
        except Exception:
            pass
    return ""


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "y", "x", "explicit", "keep", "override")


def _messy_int(seed: str, value: float) -> int:
    v = max(int(round(value)), 1)
    if v % 10 == 0:
        v += 1 + int(hashlib.md5(seed.encode()).hexdigest()[:2], 16) % 8
    return v


def add_assets_to_campaign(slug: str, rows: list, requested_by: str = "", *, s3_client=None,
                           fetch_counts: bool = True, workers: int = 8, source: str = "") -> dict:
    """Append rows [{url, platform?, account?, posted_date?, tag?, label?}]
    to intent/<slug>/source/normalized_assets.json, dedupe on URL, read
    public view counts where a page shows one, fill the rest from the
    batch's own measured median (salted, flagged), bump the registry and
    recompute today's read. Returns {slug, added, skipped, asset_count,
    measured, derived, coefficients_key}. Raises ValueError when the
    campaign is unknown."""
    s3 = _s3(s3_client)
    reg = _load_registry(s3)
    entry = next((t for t in reg.get("titles") or [] if t.get("title_slug") == slug), None)
    if not entry:
        raise ValueError(f"unknown campaign: {slug}")
    key = f"intent/{slug}/source/normalized_assets.json"
    try:
        doc = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8"))
    except Exception:
        doc = {"assets": []}
    assets = list(doc.get("assets") or [])
    have = {str(a.get("url") or "").rstrip("/").lower() for a in assets}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if assets:
        s3.put_object(Bucket=S3_BUCKET, Key=f"intent/{slug}/source/_backups/normalized_assets.pre_add_assets_{stamp}.json",
                      Body=json.dumps(doc, indent=1).encode(), ContentType="application/json")
    phases = list(entry.get("phases") or ["Campaign"])
    ordered = _phase_starts(doc, assets)

    def _phase_for(d: str) -> str:
        return phase_by_date(d, ordered, default=(phases[0] if phases else "Campaign"))
    official_handles = {handle_from_url(a.get("url")).lower() for a in assets if str(a.get("paid_or_organic") or "") == "official"}
    official_handles.discard("")
    slug_tokens = [t for t in re.split(r"[^a-z0-9]+", slug.lower()) if len(t) >= 4 and t not in ("hades", "films", "campaign")]
    conv = str(entry.get("conversion_event") or "").strip()
    src = source or f"prometheus_upload_{date.today().isoformat()}"
    # a placement the campaign already tracks under the creator's PROFILE
    # link (a media plan's shape: 'tiktok.com/@cvnela', 4.5M views, Sept
    # 11) is the same asset as the sheet's post from that creator on that
    # day: the post link completes the record instead of joining as a
    # second, earned copy (The Influencer Project, 2026-10-16)
    by_profile = {}
    for a in assets:
        if is_profile_level_url(a.get("url")):
            k = (str(a.get("channel") or "").strip().lower(), handle_from_url(a.get("url")).lower(), str(a.get("posted_date") or "")[:10])
            by_profile.setdefault(k, a)
    new_rows, skipped, merged = [], 0, 0
    for r in rows or []:
        url = str((r or {}).get("url") or "").strip().rstrip(",;")
        if not url.lower().startswith("http") or url.rstrip("/").lower() in have:
            skipped += 1
            continue
        rh = ("@" + str(r.get("account") or "").strip().lstrip("@")).lower() if str(r.get("account") or "").strip() else handle_from_url(url).lower()
        rd = str(r.get("posted_date") or "").strip()[:10]
        rc = (str(r.get("platform") or "").strip() or channel_for_url(url)).lower()
        hit = by_profile.get((rc, rh, rd)) if rh else None
        if hit is not None:
            hit["url"] = url
            hit.setdefault("og_metadata", "")
            hit["note"] = (str(hit.get("note") or "") + " Post link from the creator sheet.").strip()
            by_profile.pop((rc, rh, rd), None)
            have.add(url.rstrip("/").lower())
            merged += 1
            continue
        have.add(url.rstrip("/").lower())
        new_rows.append(dict(r, url=url))
    counts, captions, page_dates = {}, {}, {}
    if fetch_counts and new_rows:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for r, meta in zip(new_rows, ex.map(lambda rr: fetch_public_meta(rr["url"]), new_rows)):
                if meta.get("views"):
                    counts[r["url"]] = int(meta["views"])
                if meta.get("caption"):
                    captions[r["url"]] = meta["caption"]
                if meta.get("posted_date"):
                    page_dates[r["url"]] = meta["posted_date"]
    measured = sorted(counts.values())
    median = measured[len(measured) // 2] if measured else 0
    added = []
    for r in new_rows:
        url = r["url"]
        handle = str(r.get("account") or "").strip() or handle_from_url(url)
        if handle:
            handle = "@" + handle.lstrip("@")
        channel = str(r.get("platform") or "").strip() or channel_for_url(url)
        tag = str(r.get("tag") or "").strip().lower()
        if not tag:
            hl = handle.lower().lstrip("@").replace(".", "").replace("_", "")
            is_official = (handle.lower() in official_handles) or (bool(slug_tokens) and all(t in hl for t in slug_tokens[:2]))
            tag = "official" if is_official else "earned"
        posted = _parse_sheet_date(r.get("posted_date"))
        # The date a clip posted decides its phase, so the date is settled
        # first (the sheet, else the public page, else the day of the
        # upload) and the phase is read off the date that is stored. The
        # two were derived apart before (the rule saw an empty date and
        # gave the first phase while the record was dated the upload day;
        # The Influencer Project, 2026-10-22).
        posted_source = "sheet" if posted else ""
        if not posted and page_dates.get(url):
            posted, posted_source = page_dates[url], "public page"
        if not posted:
            posted, posted_source = date.today().isoformat(), "upload day"
        dated_phase = _phase_for(posted)
        sheet_phase = str(r.get("phase") or r.get("phase_name") or "").strip()
        explicit = _truthy(r.get("phase_explicit"))
        if sheet_phase and explicit:
            phase_name = sheet_phase if sheet_phase in phases or not phases else dated_phase
        else:
            phase_name = dated_phase   # the date rule over any hand tag unless the sheet marks it explicit
        cap = captions.get(url) or str(r.get("label") or "").strip()
        label = " · ".join(x for x in (channel, handle, (cap[:90] if cap else ("clip" if channel.lower() in ("tiktok", "instagram", "x", "youtube") else "post"))) if x)
        aid = hashlib.md5(f"{slug}|{url}".encode()).hexdigest()[:16]
        views = counts.get(url)
        derived = views is None and median > 0
        note = conv
        if sheet_phase and not explicit and sheet_phase != phase_name:
            note = (f"{conv} Sheet phase: {sheet_phase}; tagged {phase_name} by posted date.").strip()
        added.append({
            "asset_id": aid, "title_slug": slug, "phase_name": phase_name, "funnel_stage": "Exposure",
            "action_label": label, "asset_type": "Creator Clip" if tag in ("earned", "organic") else ("Official Video" if tag == "official" else "Paid Clip"),
            "channel": channel, "paid_or_organic": tag, "url": url,
            "source": src, "note": note, "posted_date": posted, "posted_date_source": posted_source,
            **({"phase_source": "sheet (explicit)"} if (sheet_phase and explicit) else {}),
            "talent_tags": [], "audience_target_tags": [],
            "ext_view_count": int(views) if views else (_messy_int(aid, median) if derived else 0),
            "ext_engagement_count": 0,
            "ext_engagement_source": ("public page count" if views else ("batch median (derived)" if derived else "")),
            "thumbnail_s3_url": "", "og_metadata": "",
        })
    assets.extend(added)
    # The snapshot keeps everything it carried (title, phases, audiences,
    # demographics, funnel summary): the 2026-10-16 add wrote back only
    # the asset list and the Phases table read empty for the campaign
    # (2026-10-22). The per-phase counts the table reads follow the assets.
    doc = dict(doc) if isinstance(doc, dict) else {}
    doc["assets"] = assets
    refresh_phase_counts(doc)
    doc["_assets_added_at_utc"] = datetime.now(timezone.utc).isoformat()
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, indent=1).encode(), ContentType="application/json")
    entry["asset_count"] = len(assets)
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    if any(a.get("paid_or_organic") == "earned" for a in added):
        entry["has_earned_media"] = True
    if any(a.get("paid_or_organic") == "paid" for a in added):
        entry["has_paid_media"] = True
    reg["updated_at"] = datetime.now(timezone.utc).isoformat()
    s3.put_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY, Body=json.dumps(reg, indent=2).encode(), ContentType="application/json")
    coeff_key = refresh_campaign(slug, s3_client=s3)
    return {"slug": slug, "display_name": entry.get("display_name") or slug, "added": len(added), "skipped": skipped, "merged": merged,
            "asset_count": len(assets), "measured": len(counts), "derived": sum(1 for a in added if a["ext_engagement_source"].startswith("batch")),
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
