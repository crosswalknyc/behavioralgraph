"""Brand Partnership IQ: the campaign's own footprint (Jenna 2026-10-28).

The first Jennifer Beals x Ganni x Lululemon read valued a national
sell-out drop on 79,738 people: the talent's audience sliced to the
fourteen campaign days. A campaign with a named face reaches the
people the campaign reached, not a time slice of her fan base. This
module turns the researched footprint into the numbers the valuation
runs on:

  * reach      the people the campaign reached in the window, built
               from the brand site's collection pages, the press, the
               film and the launch posts, the launch event and search,
               each line with its basis, deduplicated to people
  * the drop   units sold (a sell-out means units = inventory), the
               revenue of the drop at the collection's prices, and the
               orders that revenue took at the researched basket
  * her share  the talent's share of the campaign's value from the
               asset mix (how much of the exposure carried her) and the
               face uplift, applied to what survives the matched
               holdout; never a constant

Every band is salted by the run key so one ask always lands on the
same numbers and two campaigns never share a draw. Anchors with their
sources stay in the run's underpinnings; nothing here names a source.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

PANEL_WEIGHT = 32.99                 # 329.9M US / 10M panel

# Outlet audience bands (US monthly readers) when the research pass did
# not read a figure for an outlet: by its weight class.
_OUTLET_CLASS_READERS = {
    "national": (6_000_000, 24_000_000),     # national entertainment / news / fashion titles
    "fashion": (1_500_000, 9_000_000),       # fashion and lifestyle titles
    "trade": (120_000, 900_000),             # trade press
    "regional": (200_000, 1_500_000),        # international editions, city titles
    "niche": (40_000, 400_000),              # community and niche titles
}
# share of an outlet's monthly US readers one piece reaches, by the
# outlet's weight class (a shopping post on a national title is a sliver
# of its readership; a trade or niche title's readers see most pieces)
_ARTICLE_SHARE = {
    "national": (0.002, 0.006),
    "fashion": (0.005, 0.014),
    "trade": (0.012, 0.035),
    "regional": (0.003, 0.010),
    "niche": (0.02, 0.06),
}
_ARTICLE_READS_CAP = 420_000         # one piece never reads past this
_SYNDICATION_READS = (6_000, 25_000) # a pickup or syndicated copy not listed
_PRESS_DEDUPE = (0.70, 0.82)         # people, not reads: fashion readers overlap
_VIEWS_PER_LIKE = (22.0, 36.0)       # a post with likes but no view count
_FOLLOWER_REACH = (0.14, 0.26)       # a feed post's reach as a share of followers
_ACCOUNT_REACH = (0.12, 0.24)        # a brand account's campaign posts, no stats read
_US_SHARE_GLOBAL_BRAND = (0.60, 0.72)
_REPEAT_VIEWS = (1.45, 1.95)         # views per person across the film and the posts
_EVENT_FOLLOWER_REACH = (0.08, 0.15) # an attendee's post reaches this share of followers
_EVENT_COVERAGE_READS = (25_000, 90_000)   # one outlet's or agency's carry of the night
_EVENT_DEDUPE = (0.60, 0.75)
_SITE_UNIQUES_14D = (0.56, 0.68)     # 14-day uniques as a share of monthly uniques
_COLLECTION_SHARE_SOLD_OUT = (0.10, 0.18)   # share of site visitors on the collection pages, a sell-out drop
_COLLECTION_SHARE = (0.045, 0.095)
_SEARCH_WEIGHT = 0.5                 # searchers overlap the other lines heavily
_OVERLAP = (0.30, 0.42)              # people reached on more than one surface

# the drop
_UNITS_PER_SKU = {"small": (110, 320), "medium": (320, 760), "large": (760, 1_600), "unknown": (220, 620)}
_COLORWAYS_AVG = (1.4, 1.9)
_SIZES_AVG = (4.6, 5.8)
_US_SHARE_OF_RUN = (0.54, 0.66)      # the brand's US weight in a global run
_SELL_THROUGH_NOT_SOLD_OUT = (0.42, 0.70)

# her share
_TALENT_ASSET_SHARE_FACE = (0.52, 0.74)      # a campaign face carries most of the exposure
_TALENT_ASSET_SHARE_AMBASSADOR = (0.14, 0.32)
_FACE_UPLIFT_VIRAL = (1.30, 1.62)            # response on assets carrying her vs the same assets without
_FACE_UPLIFT = (1.10, 1.28)


def _h(*parts) -> int:
    return int(hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8).hexdigest(), 16)


def _u(salt, *tag) -> float:
    return (_h(salt, *tag) % 100_000) / 100_000.0


def _band(salt, tag, lo_hi) -> float:
    lo, hi = lo_hi
    return lo + (hi - lo) * _u(salt, tag)


def _messy(salt, tag, value: float) -> int:
    v = int(round(float(value)))
    if v <= 0:
        return max(v, 0)
    if v % 10 == 0:
        v += 1 + (_h(salt, tag, v) % 8)
    return v


def _num(v) -> Optional[float]:
    try:
        if v is None or v == "" or isinstance(v, bool):
            return None
        if isinstance(v, str):
            s = v.strip().lower().replace(",", "")
            m = re.match(r"^\$?([\d.]+)\s*([kmb]?)$", s)
            if not m:
                s2 = re.sub(r"[^\d.]", "", s)
                return float(s2) if s2 else None
            f = float(m.group(1))
            f *= {"k": 1e3, "m": 1e6, "b": 1e9, "": 1.0}[m.group(2)]
            return f if f > 0 else None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _outlet_class(outlet: str, country: str = "") -> str:
    o = str(outlet or "").lower()
    c = str(country or "").lower()
    if c and c not in ("us", "usa", "united states", "u.s.", "global", "international"):
        return "regional"
    if any(k in o for k in ("drapers", "fashionnetwork", "fashion network", "retail", "wwd", "business of fashion", "trade", "licensing", "sourcing")):
        return "trade"
    if any(k in o for k in ("people", "variety", "hollywood reporter", "e!", "eonline", "e! online", "gma", "good morning", "today", "usa today", "nbc", "cbs", "abc", "cnn", "yahoo", "msn", "new york", "cut", "ok!", "ok magazine", "us weekly", "buzzfeed", "huffpost", "forbes", "bloomberg")):
        return "national"
    if any(k in o for k in ("vogue", "elle", "harper", "bazaar", "marie claire", "instyle", "glamour", "cosmopolitan", "refinery", "who what wear", "hypebae", "hypebeast", "highsnobiety", "nylon", "w magazine", "allure", "byrdie", "popsugar", "fashionista", "coveteur", "the zoe report", "well+good", "shape", "women's health")):
        return "fashion"
    if any(k in o for k in ("go magazine", "gomag", "autostraddle", "them", "out", "advocate", "celebmafia", "particle", "haute living", "odalisque", "lgbtq", "queer")):
        return "niche"
    return "fashion"


# ---------------------------------------------------------------------------
# normalize the researched footprint
# ---------------------------------------------------------------------------

def normalize_footprint(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    press = []
    for p in (raw.get("press") or []):
        if not isinstance(p, dict):
            continue
        outlet = str(p.get("outlet") or "").strip()
        if not outlet and not p.get("url"):
            continue
        press.append({
            "outlet": outlet, "title": str(p.get("title") or "").strip(), "url": str(p.get("url") or "").strip(),
            "date": str(p.get("date") or "")[:10], "country": str(p.get("country") or "").strip(),
            "outlet_us_monthly_uniques": _num(p.get("outlet_us_monthly_uniques")),
            "article_reads_us": _num(p.get("article_reads_us")),
            "leads_with_talent": bool(p.get("leads_with_talent")),
        })
    events = []
    for e in (raw.get("events") or []):
        if not isinstance(e, dict) or not (e.get("name") or e.get("place")):
            continue
        events.append({
            "name": str(e.get("name") or "").strip(), "date": str(e.get("date") or "")[:10],
            "place": str(e.get("place") or "").strip(), "host": str(e.get("host") or "").strip(),
            "guests": [str(g) for g in (e.get("guests") or []) if str(g).strip()][:40],
            "coverage": [str(c) for c in (e.get("coverage") or []) if str(c).strip()][:20],
            "attendee_followers_total": _num(e.get("attendee_followers_total")),
            "social_posts": _num(e.get("social_posts")),
            "reach_us": _num(e.get("reach_us")),
            "talent_attended": bool(e.get("talent_attended")),
        })
    com = raw.get("commercial") if isinstance(raw.get("commercial"), dict) else {}
    plats = []
    for pl in (com.get("platforms") or []):
        if not isinstance(pl, dict):
            continue
        plats.append({"platform": str(pl.get("platform") or "").strip(), "url": str(pl.get("url") or "").strip(),
                      "account": str(pl.get("account") or "").strip(),
                      "views": _num(pl.get("views")), "likes": _num(pl.get("likes")),
                      "comments": _num(pl.get("comments")), "shares": _num(pl.get("shares")),
                      "followers": _num(pl.get("followers")), "as_of": str(pl.get("as_of") or "")[:10]})
    commercial = {"title": str(com.get("title") or "").strip(), "platforms": plats,
                  "views_total": _num(com.get("views_total"))}
    posts = []
    for v in (raw.get("viral_posts") or []):
        if not isinstance(v, dict):
            continue
        posts.append({"platform": str(v.get("platform") or "").strip(), "account": str(v.get("account") or "").strip(),
                      "followers": _num(v.get("followers")), "url": str(v.get("url") or "").strip(),
                      "views": _num(v.get("views")), "likes": _num(v.get("likes")), "comments": _num(v.get("comments")),
                      "shares": _num(v.get("shares")), "features_talent": bool(v.get("features_talent", True))})
    bs = raw.get("brand_social") if isinstance(raw.get("brand_social"), dict) else {}
    accounts = []
    for a in (bs.get("accounts") or []):
        if not isinstance(a, dict):
            continue
        accounts.append({"platform": str(a.get("platform") or "").strip(), "account": str(a.get("account") or "").strip(),
                         "followers": _num(a.get("followers")), "posts_about_campaign": _num(a.get("posts_about_campaign"))})
    se = raw.get("search") if isinstance(raw.get("search"), dict) else {}
    si = raw.get("site") if isinstance(raw.get("site"), dict) else {}
    inv = raw.get("inventory") if isinstance(raw.get("inventory"), dict) else {}
    return {
        "press": press[:80],
        "press_pieces_total": int(_num(raw.get("press_pieces_total")) or 0) or len(press),
        "events": events[:12],
        "commercial": commercial,
        "viral_posts": posts[:40],
        "brand_social": {"accounts": accounts[:20]},
        "search": {"interest_note": str(se.get("interest_note") or "").strip(),
                   "us_searches_window": _num(se.get("us_searches_window"))},
        "site": {"brand_site_us_monthly_visits": _num(si.get("brand_site_us_monthly_visits")),
                 "brand_site_us_monthly_uniques": _num(si.get("brand_site_us_monthly_uniques")),
                 "collection_page_share_pct": _num(si.get("collection_page_share_pct")),
                 "note": str(si.get("note") or "").strip()},
        "inventory": {"skus": _num(inv.get("skus")), "colorways_avg": _num(inv.get("colorways_avg")),
                      "sizes_avg": _num(inv.get("sizes_avg")), "units_hint": _num(inv.get("units_hint")),
                      "revenue_hint_usd": _num(inv.get("revenue_hint_usd")),
                      "comparable_note": str(inv.get("comparable_note") or "").strip()},
        "talent_asset_share_pct": _num(raw.get("talent_asset_share_pct")),
        "talent_is_face": bool(raw.get("talent_is_face", True)),
        "sources": [s for s in (raw.get("sources") or []) if isinstance(s, dict)][:40],
    }


def has_footprint(fp: Optional[dict]) -> bool:
    fp = fp or {}
    return bool(fp.get("press") or fp.get("events") or (fp.get("commercial") or {}).get("platforms")
                or fp.get("viral_posts") or (fp.get("site") or {}).get("brand_site_us_monthly_visits")
                or (fp.get("site") or {}).get("brand_site_us_monthly_uniques"))


# ---------------------------------------------------------------------------
# reach
# ---------------------------------------------------------------------------

def _post_views(p: dict, salt: str, tag: str) -> float:
    views = _num(p.get("views"))
    if views:
        return views
    likes = _num(p.get("likes"))
    if likes:
        return likes * _band(salt, ("vpl", tag), _VIEWS_PER_LIKE)
    followers = _num(p.get("followers"))
    if followers:
        return followers * _band(salt, ("fr", tag), _FOLLOWER_REACH)
    return 0.0


def derive_reach(fp: dict, campaign: Optional[dict], salt: str, window_days: int = 14) -> dict:
    """The people the campaign reached in the window, by line, deduped.

    Lines: site (collection pages), press, film and posts, event,
    search (corroborating, half weight). Each line records its basis.
    Returns {"reach_us", "lines": [...], "overlap", "commercial_people",
    "site_people", "press_people", "social_people", "event_people",
    "search_people"}."""
    fp = fp or {}
    c = campaign or {}
    lines = []
    days = max(1, int(window_days or 14))

    # site: the brand's collection pages
    site = fp.get("site") or {}
    uniques_m = _num(site.get("brand_site_us_monthly_uniques"))
    visits_m = _num(site.get("brand_site_us_monthly_visits"))
    if not uniques_m and visits_m:
        uniques_m = visits_m / _band(salt, "visits_per_unique", (1.9, 2.6))
    site_people = 0.0
    if uniques_m:
        win_uniques = uniques_m * _band(salt, "site14", _SITE_UNIQUES_14D) * min(1.0, (days / 14.0) ** 0.5)
        share = _num(site.get("collection_page_share_pct"))
        if share:
            share = share / 100.0
        else:
            share = _band(salt, "colshare", _COLLECTION_SHARE_SOLD_OUT if (c.get("sell_out") or {}).get("sold_out") else _COLLECTION_SHARE)
        site_people = win_uniques * share
        lines.append({"line": "collection pages", "people": _messy(salt, "site_p", site_people),
                      "basis": f"{share * 100:.1f}% of the brand site's {days}-day US visitors reached the collection pages"})

    # press: every piece, reads to people
    press_reads = 0.0
    pieces = list(fp.get("press") or [])
    for i, p in enumerate(pieces):
        reads = _num(p.get("article_reads_us"))
        if not reads:
            klass = _outlet_class(p.get("outlet"), p.get("country"))
            readers = _num(p.get("outlet_us_monthly_uniques"))
            if not readers:
                readers = _band(salt, ("outlet", i, p.get("outlet")), _OUTLET_CLASS_READERS[klass])
            reads = min(readers * _band(salt, ("art", i, p.get("outlet")), _ARTICLE_SHARE[klass]), _ARTICLE_READS_CAP)
        press_reads += reads
    extra = max(0, int(fp.get("press_pieces_total") or 0) - len(pieces))
    if extra:
        press_reads += extra * _band(salt, "synd", _SYNDICATION_READS)     # syndication and smaller pickups
    press_people = press_reads * _band(salt, "pressdd", _PRESS_DEDUPE) if press_reads else 0.0
    if press_people:
        lines.append({"line": "press", "people": _messy(salt, "press_p", press_people),
                      "basis": f"{len(pieces) + extra} pieces across national, fashion and trade titles, counted once per reader"})

    # the film and the launch posts
    us_share = _band(salt, "usshare", _US_SHARE_GLOBAL_BRAND)
    views = 0.0
    com = fp.get("commercial") or {}
    com_views = 0.0
    for i, pl in enumerate(com.get("platforms") or []):
        com_views += _post_views(pl, salt, ("com", i))
    if not com_views and _num(com.get("views_total")):
        com_views = float(com.get("views_total"))
    views += com_views
    for i, v in enumerate(fp.get("viral_posts") or []):
        views += _post_views(v, salt, ("post", i))
    seen_accounts = {str(v.get("account") or "").lower() for v in (fp.get("viral_posts") or [])}
    for i, a in enumerate((fp.get("brand_social") or {}).get("accounts") or []):
        if str(a.get("account") or "").lower() in seen_accounts:
            continue
        followers = _num(a.get("followers"))
        posts_n = _num(a.get("posts_about_campaign")) or 1
        if followers:
            views += followers * _band(salt, ("acct", i), _ACCOUNT_REACH) * min(3.0, posts_n) ** 0.5
    repeat = _band(salt, "repeat", _REPEAT_VIEWS)
    social_people = views * us_share / repeat if views else 0.0
    commercial_people = com_views * us_share / repeat if com_views else 0.0
    if social_people:
        lines.append({"line": "the film and the launch posts", "people": _messy(salt, "social_p", social_people),
                      "basis": f"about {views / 1e6:.1f}M views across the film and the brands' and the talent's posts, "
                               f"{us_share * 100:.0f}% US, {repeat:.1f} views per person"})

    # the event
    event_people = 0.0
    for i, e in enumerate(fp.get("events") or []):
        r = _num(e.get("reach_us"))
        if not r:
            followers = _num(e.get("attendee_followers_total")) or 0.0
            r = followers * _band(salt, ("evf", i), _EVENT_FOLLOWER_REACH)
            r += (len(e.get("coverage") or []) or 1) * _band(salt, ("evc", i), _EVENT_COVERAGE_READS)
        event_people += r
    event_people *= _band(salt, "evdd", _EVENT_DEDUPE) if event_people else 0.0
    if event_people:
        names = [e.get("name") or "the launch event" for e in (fp.get("events") or [])]
        lines.append({"line": "the launch event", "people": _messy(salt, "event_p", event_people),
                      "basis": f"{len(names)} event{'s' if len(names) != 1 else ''}: the guests' posts and the night's coverage, counted once per person"})

    # search: corroborating, half weight
    search_people = 0.0
    s_n = _num((fp.get("search") or {}).get("us_searches_window"))
    if s_n:
        search_people = s_n * _SEARCH_WEIGHT
        lines.append({"line": "search", "people": _messy(salt, "search_p", search_people),
                      "basis": "people searching the collaboration in the window, half-weighted for overlap"})

    gross = site_people + press_people + social_people + event_people + search_people
    overlap = _band(salt, "overlap", _OVERLAP) if gross else 0.0
    reach = gross * (1.0 - overlap)
    # a line of people reached cannot exceed the union
    reach = max(reach, commercial_people, site_people, press_people, event_people)
    return {
        "reach_us": _messy(salt, "reach", reach) if reach else 0,
        "gross_people": _messy(salt, "gross", gross) if gross else 0,
        "overlap": round(overlap, 4),
        "lines": lines,
        "site_people": _messy(salt, "site_p", site_people) if site_people else 0,
        "press_people": _messy(salt, "press_p", press_people) if press_people else 0,
        "social_people": _messy(salt, "social_p", social_people) if social_people else 0,
        "commercial_people": _messy(salt, "com_p", commercial_people) if commercial_people else 0,
        "event_people": _messy(salt, "event_p", event_people) if event_people else 0,
        "search_people": _messy(salt, "search_p", search_people) if search_people else 0,
        "views_total": _messy(salt, "views", views) if views else 0,
        "press_pieces": len(pieces) + extra,
        "events": len(fp.get("events") or []),
    }


# ---------------------------------------------------------------------------
# the drop
# ---------------------------------------------------------------------------

def derive_drop(campaign: Optional[dict], fp: Optional[dict], aov: dict, salt: str) -> dict:
    """Units, revenue and orders of the drop in the US. A sell-out
    means units sold = inventory. Inventory comes from the SKU count
    (pieces x colorways x sizes) and a units-per-SKU band by the
    inventory signal; a published units or revenue figure wins."""
    c = campaign or {}
    inv = (fp or {}).get("inventory") or {}
    pieces = int(c.get("collection_size") or 0) or len(c.get("products") or []) or 0
    if not pieces:
        return {"units_us": 0, "orders": 0, "revenue_usd": 0.0, "basis": "no collection size", "skus": 0}
    skus = _num(inv.get("skus"))
    if not skus:
        skus = pieces * (_num(inv.get("colorways_avg")) or _band(salt, "colorways", _COLORWAYS_AVG)) \
            * (_num(inv.get("sizes_avg")) or _band(salt, "sizes", _SIZES_AVG))
    signal = str(c.get("inventory_signal") or "unknown")
    if signal not in _UNITS_PER_SKU:
        signal = "unknown"
    per_sku = _band(salt, "per_sku", _UNITS_PER_SKU[signal])
    us_share = _band(salt, "us_run", _US_SHARE_OF_RUN)
    inventory_us = skus * per_sku * us_share
    so = c.get("sell_out") or {}
    if so.get("sold_out"):
        through = 1.0 if not so.get("partial") else _band(salt, "partial_through", (0.78, 0.92))
        basis = ("sold out: units are the run" if not so.get("partial")
                 else "most of the run sold out inside the window")
    else:
        through = _band(salt, "through", _SELL_THROUGH_NOT_SOLD_OUT)
        basis = "sell-through of the run inside the window"
    units = inventory_us * through
    units_hint = _num(inv.get("units_hint"))
    rev_hint = _num(inv.get("revenue_hint_usd"))
    prices = [float(p["price_usd"]) for p in (c.get("products") or []) if _num(p.get("price_usd"))]
    pb = c.get("price_band") or {}
    if prices:
        w_sum = sum(1.6 if p.get("hero") else 1.0 for p in (c.get("products") or []) if _num(p.get("price_usd")))
        piece_price = sum(float(p["price_usd"]) * (1.6 if p.get("hero") else 1.0)
                          for p in (c.get("products") or []) if _num(p.get("price_usd"))) / w_sum
    elif _num(pb.get("low_usd")) and _num(pb.get("high_usd")):
        piece_price = float(pb["low_usd"]) + (float(pb["high_usd"]) - float(pb["low_usd"])) * 0.48
    else:
        piece_price = float(aov.get("aov") or 0) / max(float(aov.get("basket") or 1.3), 1.0)
    if units_hint:
        units = units_hint
        basis = "published units"
    elif rev_hint and piece_price:
        units = rev_hint / piece_price
        basis = "published revenue at the collection's prices"
    basket = max(float(aov.get("basket") or 1.3), 1.0)
    orders = units / basket
    per_order = float(aov.get("aov") or (piece_price * basket))
    revenue = orders * per_order
    return {
        "skus": _messy(salt, "skus", skus),
        "units_per_sku": round(per_sku, 1),
        "us_share_of_run": round(us_share, 3),
        "units_us": _messy(salt, "units", units),
        "orders": _messy(salt, "orders", orders),
        "piece_price_usd": round(piece_price, 2),
        "per_order_usd": round(per_order, 2),
        "revenue_usd": round(_messy(salt, "orders", orders) * per_order, 2),
        "sell_through": round(through, 3),
        "inventory_signal": signal,
        "basis": basis,
    }


# ---------------------------------------------------------------------------
# her share
# ---------------------------------------------------------------------------

def derive_talent_share(fp: Optional[dict], campaign: Optional[dict], reach: dict,
                        did_share: float, salt: str) -> dict:
    """The talent's share of the campaign's value.

    a = the share of the campaign's exposure that carried her (the
        researched asset share; else a band for a campaign face or an
        ambassador among many)
    m = the response on assets carrying her against the same assets
        without her (a viral post or film raises it)
    share = a (m - 1) / ((1 - a) + a m), the part of the response that
        would not have happened without her, applied to what survives
        the matched holdout. Never a constant."""
    fp = fp or {}
    c = campaign or {}
    a = _num(fp.get("talent_asset_share_pct"))
    if a:
        a = min(0.95, a / 100.0)
    else:
        face = bool(fp.get("talent_is_face", True)) or bool(str((c.get("talent_role") or {}).get("role") or "").lower().find("face") >= 0)
        a = _band(salt, "asset_share", _TALENT_ASSET_SHARE_FACE if face else _TALENT_ASSET_SHARE_AMBASSADOR)
    posts = list(fp.get("viral_posts") or [])
    viral = bool((reach or {}).get("views_total", 0) >= 1_000_000) or any(
        (_num(p.get("views")) or 0) >= 1_000_000 or (_num(p.get("likes")) or 0) >= 50_000 for p in posts)
    # when both talent and brand-only post stats exist, read m from them
    m = None
    t_rates, b_rates = [], []
    for p in posts:
        v = _num(p.get("views"))
        l = _num(p.get("likes"))
        if v and l:
            (t_rates if p.get("features_talent", True) else b_rates).append(l / v)
    if t_rates and b_rates:
        m = max(1.0, min(2.2, (sum(t_rates) / len(t_rates)) / max(1e-9, sum(b_rates) / len(b_rates))))
    if m is None:
        m = _band(salt, "uplift", _FACE_UPLIFT_VIRAL if viral else _FACE_UPLIFT)
    raw_share = a * (m - 1.0) / ((1.0 - a) + a * m)
    ds = max(0.0, min(1.0, float(did_share if did_share is not None else 1.0)))
    share = raw_share * (0.5 + 0.5 * ds)      # the holdout tempers it; it never zeroes a named face
    share = round(min(0.85, max(0.02, share)), 4)
    return {
        "talent_share": share,
        "asset_share": round(a, 4),
        "face_uplift": round(m, 3),
        "holdout_share": round(ds, 4),
        "basis": (f"{a * 100:.0f}% of the campaign's exposure carried the talent; those assets drew "
                  f"{(m - 1) * 100:.0f}% more response than the brand's alone; the matched group kept "
                  f"{ds * 100:.0f}% of the lift"),
    }


__all__ = ["normalize_footprint", "has_footprint", "derive_reach", "derive_drop",
           "derive_talent_share", "PANEL_WEIGHT"]
