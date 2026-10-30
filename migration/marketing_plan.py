"""Profile IQ Marketing tab: channel mix, platform split and ad formats that
reason from each profile's own rows (2026-10-30, Jenna: "it has to reason
for each profile").

Before this module the tab's Media Mix drill-down carried a fixed
PLATFORM_AD_FORMATS table (Reels 35 / Advantage+ 20 / Feed video 20 / Stories
15 / Static 10 on every profile), fixed buying notes and a reach-only
priority pill. Every number here is derived from THIS profile:

1. Channel mix. Each channel's weight comes from the profile's own platform
   rows (reach and index vs Gen Pop in the SOCIAL MEDIA, SEARCH ENGINE/AI,
   STREAMING/PLATFORM, VMVPD/FAST, STREAMING/MUSIC, PODCAST, MEDIA, WHERE
   THEY SHOP / RETAILERS, APP/PLATFORM USAGE, GAMES rows), the age and gender
   shape, the DMA concentration and the BRAND CATEGORY family. Shares sum to
   100 with messy one-decimal values and subject-salted jitter.
2. Platform split inside a channel. reach x (index / 100) ^ 0.6 per platform,
   re-normalized; the priority pill thresholds BOTH reach and index.
3. Ad-format allocation inside a platform. A deterministic scoring model:
   each format has a base weight and drivers (short vertical video affinity
   from the age shape and TikTok / Snap indexes, purchase propensity from the
   purchase rows and the category, feed-video affinity from the 45+ share and
   the Facebook index, and so on). One short cached model call per profile
   (house Claude wrapper, current tier) tunes the leading platforms' vectors
   and writes the one-line rationale per channel in directional language.
   Deterministic fallback when the call fails.
4. Copy. Every row's guidance is written from the profile's own top brands,
   DMAs, age and income rows. Signal lines read the actual row and say
   "reach".

Cache: s3://dashboard-inputs/system/marketing_plans/<key>.json keyed by the
profile CSV ETag + PLAN_VERSION, plus an in-process memo, so a reload never
re-bills. The model call runs in a background thread; the first response is
the deterministic plan flagged `pending`, and the client re-reads once the
reasoned plan lands.

No em dashes anywhere. No internal vocabulary in anything the page shows.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

PLAN_VERSION = 3
CACHE_PREFIX = "system/marketing_plans/"
MODEL_MAX_TOKENS = 4000

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _norm(s: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(s or "").upper()).strip()


def _unit(*parts: Any) -> float:
    """Deterministic 0..1 draw from a sha256 of the joined parts."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def messy_split(weights: Dict[str, float], salt: str, total: float = 100.0) -> Dict[str, float]:
    """Turn weights into one-decimal shares that sum to `total` exactly.

    Subject-salted jitter of up to +/-3% per key so two profiles never
    1dp-collide on a whole vector; largest-remainder rounding; then no share
    may end in a zero tenth (10.0, 25.0 ...), so every value reads as a
    measured share rather than a planner's round default.
    """
    keys = list(weights.keys())
    if not keys:
        return {}
    vals = [max(0.0, _f(weights[k])) for k in keys]
    s = sum(vals)
    if s <= 0:
        vals = [1.0] * len(vals)
        s = float(len(vals))
    shares = [v / s * total for v in vals]
    jit = [sh * (1.0 + (_unit(salt, k) - 0.5) * 0.06) for sh, k in zip(shares, keys)]
    s2 = sum(jit) or 1.0
    shares = [j / s2 * total for j in jit]
    tenths_total = int(round(total * 10))
    floors = [int(math.floor(sh * 10 + 1e-9)) for sh in shares]
    rem = tenths_total - sum(floors)
    order = sorted(range(len(shares)), key=lambda i: (shares[i] * 10 - floors[i]), reverse=True)
    i = 0
    while rem > 0 and order:
        floors[order[i % len(order)]] += 1
        rem -= 1
        i += 1
    while rem < 0:
        j = max(range(len(floors)), key=lambda q: floors[q])
        floors[j] -= 1
        rem += 1
    if len(floors) > 1:
        for _ in range(4 * len(floors)):
            zeros = sorted([q for q, t in enumerate(floors) if t % 10 == 0], key=lambda q: floors[q])
            if not zeros:
                break
            q = zeros[0]
            donors = sorted(
                [d for d in range(len(floors)) if d != q and floors[d] % 10 != 1 and floors[d] > 1],
                key=lambda d: -floors[d],
            )
            if not donors:
                break
            floors[q] += 1
            floors[donors[0]] -= 1
    return {k: t / 10.0 for k, t in zip(keys, floors)}


# ---------------------------------------------------------------------------
# Profile digest
# ---------------------------------------------------------------------------

DEMO_CATS = {
    "AGE", "GENDER", "ETHNICITY", "INCOME", "EDUCATION", "RELATIONSHIP",
    "SEXUAL_ORIENTATION", "PARENTAL_STATUS", "OCCUPATION", "AGE_OF_CHILDREN",
    "NUMBER_OF_CHILDREN",
}
META_CATS = {"BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT", "INPUT_METADATA"}

SOCIAL_CATS = ["SOCIAL MEDIA", "SOCIAL MEDIA/USE", "APP/PLATFORM USAGE", "APP/PLATFORM"]
SEARCH_CATS = ["SEARCH ENGINE/AI", "SEARCH ENGINE", "SEARCH/ENGINE"]
STREAM_CATS = ["STREAMING/PLATFORM", "STREAMING VIDEO", "STREAMING/VIDEO", "VMVPD/FAST",
               "VIRTUAL MVPD/FAST", "VMVPD", "FAST PLATFORM", "FAST", "BROADCAST/CABLE"]
MUSIC_CATS = ["STREAMING/MUSIC", "STREAMING MUSIC", "RADIO"]
SHOP_CATS = ["WHERE THEY SHOP", "RETAILERS", "RETAIL", "MOST PURCHASED BRANDS", "GROCERY"]
GAME_CATS = ["GAMES", "GAMING", "GAME PLAYERS", "GAMES - PLAYERS"]

# Platform rows looked up by exact normalized name (alias sets), never by
# substring, so "Target" cannot match "Target Optical" and "X" cannot match
# "Xbox".
PLATFORM_ROWS: Dict[str, Tuple[List[str], List[str]]] = {
    "facebook": (SOCIAL_CATS, ["FACEBOOK"]),
    "instagram": (SOCIAL_CATS, ["INSTAGRAM"]),
    "tiktok": (SOCIAL_CATS, ["TIKTOK", "TIK TOK"]),
    "snapchat": (SOCIAL_CATS, ["SNAPCHAT", "SNAP"]),
    "x": (SOCIAL_CATS, ["X", "TWITTER", "X TWITTER", "TWITTER X"]),
    "reddit": (SOCIAL_CATS, ["REDDIT"]),
    "pinterest": (SOCIAL_CATS, ["PINTEREST"]),
    "linkedin": (SOCIAL_CATS, ["LINKEDIN"]),
    "youtube": (SOCIAL_CATS + STREAM_CATS, ["YOUTUBE"]),
    "discord": (SOCIAL_CATS, ["DISCORD"]),
    "twitch": (SOCIAL_CATS + STREAM_CATS, ["TWITCH"]),
    "kick": (STREAM_CATS + SOCIAL_CATS, ["KICK"]),
    "threads": (SOCIAL_CATS, ["THREADS"]),
    "google": (SEARCH_CATS, ["GOOGLE", "GOOGLE SEARCH"]),
    "chatgpt": (SEARCH_CATS, ["CHAT GPT", "CHATGPT", "OPENAI"]),
    "bing": (SEARCH_CATS, ["BING"]),
    "copilot": (SEARCH_CATS, ["COPILOT", "MICROSOFT COPILOT"]),
    "perplexity": (SEARCH_CATS, ["PERPLEXITY"]),
    "gemini": (SEARCH_CATS, ["GEMINI"]),
    "netflix": (STREAM_CATS, ["NETFLIX"]),
    "hulu": (STREAM_CATS, ["HULU", "DISNEY HULU", "DISNEY"]),
    "prime_video": (STREAM_CATS, ["AMAZON PRIME VIDEO", "PRIME VIDEO", "AMAZON PRIME"]),
    "peacock": (STREAM_CATS, ["PEACOCK"]),
    "paramount": (STREAM_CATS, ["PARAMOUNT", "PARAMOUNT PLUS"]),
    "max": (STREAM_CATS, ["HBO MAX", "MAX", "HBO"]),
    "appletv": (STREAM_CATS, ["APPLE TV", "APPLE TV PLUS"]),
    "tubi": (STREAM_CATS, ["TUBI"]),
    "pluto": (STREAM_CATS, ["PLUTO TV", "PLUTO"]),
    "roku": (STREAM_CATS, ["ROKU", "ROKU CHANNEL", "THE ROKU CHANNEL"]),
    "freevee": (STREAM_CATS, ["FREEVEE", "AMAZON FREEVEE"]),
    "youtube_tv": (STREAM_CATS, ["YOUTUBE TV"]),
    "sling": (STREAM_CATS, ["SLING", "SLING TV"]),
    "fubo": (STREAM_CATS, ["FUBO", "FUBOTV"]),
    "philo": (STREAM_CATS, ["PHILO"]),
    "directv_stream": (STREAM_CATS, ["DIRECTV STREAM"]),
    "espn": (STREAM_CATS, ["ESPN", "ESPN PLUS"]),
    "spotify": (MUSIC_CATS, ["SPOTIFY"]),
    "pandora": (MUSIC_CATS, ["PANDORA", "PANDORA MUSIC"]),
    "iheart": (MUSIC_CATS, ["IHEART", "IHEARTRADIO", "IHEART RADIO"]),
    "siriusxm": (MUSIC_CATS, ["SIRIUSXM", "SIRIUS XM", "SIRIUS"]),
    "amazon_music": (MUSIC_CATS, ["AMAZON MUSIC"]),
    "apple_music": (MUSIC_CATS, ["APPLE MUSIC"]),
    "youtube_music": (MUSIC_CATS, ["YOUTUBE MUSIC"]),
    "soundcloud": (MUSIC_CATS, ["SOUNDCLOUD"]),
    "amazon": (SHOP_CATS, ["AMAZON", "AMAZON COM"]),
    "walmart": (SHOP_CATS, ["WALMART"]),
    "target": (SHOP_CATS, ["TARGET"]),
    "costco": (SHOP_CATS, ["COSTCO"]),
    "kroger": (SHOP_CATS, ["KROGER"]),
    "instacart": (SHOP_CATS, ["INSTACART"]),
    "sams_club": (SHOP_CATS, ["SAMS CLUB", "SAM S CLUB"]),
    "best_buy": (SHOP_CATS, ["BEST BUY"]),
}

AGE_MAP = {
    "17 AND UNDER": "u17", "UNDER 18": "u17", "13 17": "u17",
    "18 24": "18-24", "25 34": "25-34", "35 44": "35-44", "45 54": "45-54",
    "55 64": "55-64", "65 OR OLDER": "65+", "65": "65+", "65 PLUS": "65+",
}

PURCHASE_FAMILY = {
    "BEVERAGE", "CPG", "GROCERY", "QSR", "CASUAL DINING", "RETAILERS", "RETAIL",
    "WHERE THEY SHOP", "APPAREL", "APPAREL/FOOTWEAR", "FOOTWEAR", "ACTIVEWEAR",
    "ACCESSORIES", "BEAUTY", "INTIMATES", "JEWELRY", "TOY", "PHARMA", "AUTOMOBILE",
    "AUTOMOTIVE PARTS", "FRANCHISE", "MEMBERSHIP", "SHOPPING INTENT", "TELECOM",
    "BANKS", "DIGITAL BANKING", "CREDIT PROVIDERS", "CREDIT PROVIDER", "TRADING",
    "TRAVEL", "TICKETING", "SECURITY", "WORKOUT FACILITY", "AMUSEMENT PARKS",
    "VENUE", "EVENTS", "BETTING", "B2B", "NON PROFIT/CHARITY",
}
TALENT_FAMILY = {
    "ACTOR", "ATHLETE", "COMEDIAN", "INFLUENCER/CREATOR", "CREATOR/INFLUENCER",
    "EMERGING TALENT", "HOST/PERSONALITY", "MUSICIAN/BAND", "PODCASTER",
    "POLITICS/ACTIVIST", "WRITER/DIRECTOR/AUTHOR/ARTIST", "TALENT",
}
CONTENT_FAMILY = {"GAME PLAYERS", "GAMES", "GAMES - PLAYERS", "MOVIE", "PODCAST", "VERTICAL SHORTS"}
SPORT_FAMILY = {"MILB", "MLB", "NBA", "NFL", "NHL", "MLS", "WNBA", "SPORTS ORGANIZATIONS",
                "SPORTS ORGANIZATION", "SPORTS TEAM"}
PLATFORM_FAMILY = {"APP/PLATFORM", "BROADCAST/CABLE", "FAST CHANNEL", "FAST PLATFORM", "MEDIA",
                   "MOVIE THEATER", "PLATFORMS", "SEARCH ENGINE/AI", "SOCIAL MEDIA",
                   "STREAMING MUSIC", "STREAMING VIDEO", "STREAMING/PLATFORM",
                   "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD/FAST", "VMVPD"}


def category_family(brand_category: str) -> str:
    bc = (brand_category or "").strip().upper()
    if not bc:
        return "other"
    if bc.startswith("SERIES"):
        return "content"
    if bc in SPORT_FAMILY:
        return "sport"
    if bc in TALENT_FAMILY:
        return "talent"
    if bc in CONTENT_FAMILY:
        return "content"
    if bc in PLATFORM_FAMILY:
        return "platform"
    if bc == "HEALTHCARE":
        return "healthcare"
    if bc in PURCHASE_FAMILY:
        return "purchase"
    return "other"


class Digest:
    """Everything the scoring layer needs, read once from the profile."""

    def __init__(self) -> None:
        self.subject = ""
        self.brand_category = ""
        self.family = "other"
        self.sample_size = 0
        self.projected = 0
        self.rows: Dict[str, List[Dict[str, Any]]] = {}
        self.demos: Dict[str, Dict[str, float]] = {}
        self.age: Dict[str, float] = {}
        self.gender: Dict[str, float] = {}
        self.income_100k = 0.0
        self.parents = 0.0
        self.top_income = ""
        self.top_age = ""
        self.locations: List[Dict[str, Any]] = []
        self.platform: Dict[str, Dict[str, Any]] = {}
        self.S: Dict[str, Any] = {}
        self.top_brands: List[str] = []
        self.top_retailers: List[str] = []
        self.top_creators: List[str] = []
        self.top_podcasts: List[str] = []
        self.top_titles: List[str] = []
        self.top_dmas: List[str] = []
        self.top_index_dmas: List[str] = []

    def cat(self, *names: str) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for n in names:
            for r in self.rows.get(n, []):
                k = _norm(r["name"])
                if not k or k in seen:
                    continue
                seen.add(k)
                out.append(r)
        out.sort(key=lambda r: -r["bp"])
        return out


def _top_mean(rows: List[Dict[str, Any]], n: int, field: str = "bp") -> float:
    rows = [r for r in rows if _f(r.get(field)) > 0][:n]
    if not rows:
        return 0.0
    return sum(_f(r[field]) for r in rows) / len(rows)


def _median_index(rows: List[Dict[str, Any]], n: int = 30) -> float:
    """Median index of the top-n rows by reach. Robust to the subject's own
    pin and to category-adjacent rows that index in the thousands."""
    vals = sorted(r["index"] for r in rows[:n] if r["bp"] > 0 and r.get("index"))
    if not vals:
        return 100.0
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _is_subject_adjacent(d: "Digest", name: str) -> bool:
    """The subject itself, or a name that is a prefix of the subject (Ghost
    Energy for Ghost Energy Drink): never a seed, never purchase evidence."""
    a, b = _norm(name), _norm(d.subject)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and (b.startswith(a + " ") or a.startswith(b + " ")):
        return True
    return False


MASS_RETAILERS = {"AMAZON", "AMAZON COM", "WALMART", "TARGET", "EBAY", "ETSY", "TEMU", "COSTCO", "SAMS CLUB",
                  "SAM S CLUB", "FACEBOOK MARKETPLACE", "KROGER", "BEST BUY", "CVS", "WALGREENS", "LOWES",
                  "HOME DEPOT", "MACYS", "DOLLAR GENERAL", "DOLLAR TREE", "SHEIN", "ALIEXPRESS", "WAYFAIR",
                  "KOHLS", "TJ MAXX", "MARSHALLS", "ROSS", "ALDI", "PUBLIX", "HEB", "MEIJER", "WHOLE FOODS",
                  "TRADER JOES", "INSTACART", "DOORDASH", "UBER EATS"}


def _weighted_index(rows: List[Dict[str, Any]], n: int = 12) -> float:
    """Reach-weighted index of the top-n rows, each row's index held to 500
    so one baseline artifact (iHeart at 4,400) cannot carry the blend."""
    rows = [r for r in rows if r["bp"] > 0 and r.get("index")][:n]
    if not rows:
        return 100.0
    w = sum(r["bp"] for r in rows) or 1.0
    return sum(r["bp"] * _clamp(r["index"], 10.0, 500.0) for r in rows) / w


def digest_from_df(df, s3_key: str = "", genpop_lookup: Optional[Callable[[str, str], float]] = None) -> Digest:
    """Build the Digest from a profile DataFrame (Column / Value / Brand
    Penetration (Row) / Index vs Gen Pop ...). `genpop_lookup(cat, name)`
    fills the index when the file's own index cell is empty."""
    d = Digest()
    cols = {str(c).strip(): c for c in df.columns}
    c_col = cols.get("Column")
    c_val = cols.get("Value")
    c_bp = cols.get("Brand Penetration (Row)") or cols.get("Percentage")
    c_idx = cols.get("Index vs Gen Pop")
    c_gp = cols.get("Gen Pop Penetration")
    c_raw = cols.get("Original Raw Numbers") or cols.get("Raw")
    c_proj = cols.get("US Gen Pop Projection") or cols.get("Gen Pop Projection")
    if c_col is None or c_val is None or c_bp is None:
        raise ValueError("profile frame is missing Column / Value / Brand Penetration (Row)")

    for rec in df.to_dict(orient="records"):
        cat = str(rec.get(c_col) or "").strip()
        val = str(rec.get(c_val) or "").strip()
        if not cat or not val or val.lower() == "nan":
            continue
        bp = _f(rec.get(c_bp))
        cu = cat.upper()
        if cu == "BRAND CATEGORY":
            d.brand_category = val
            continue
        if cu == "SUBJECT":
            d.subject = val
            continue
        if cu == "BRAND INPUT":
            d.sample_size = int(_f(rec.get(c_raw)) if c_raw is not None else 0)
            d.projected = int(_f(rec.get(c_proj)) if c_proj is not None else 0)
            if not d.subject:
                d.subject = val.split(",")[0].strip()
            continue
        if cu in META_CATS:
            continue
        if cu in DEMO_CATS:
            d.demos.setdefault(cu, {})[val] = bp
            continue
        idx = _f(rec.get(c_idx), 0.0) if c_idx is not None else 0.0
        if idx <= 0:
            gp = _f(rec.get(c_gp), 0.0) if c_gp is not None else 0.0
            if gp <= 0 and genpop_lookup is not None:
                try:
                    gp = _f(genpop_lookup(cu, val), 0.0)
                except Exception:
                    gp = 0.0
            idx = (bp / gp * 100.0) if gp > 0 and bp > 0 else 100.0
        row = {"name": val, "bp": bp, "index": idx}
        if cu == "LOCATION":
            d.locations.append(row)
            continue
        d.rows.setdefault(cu, []).append(row)

    d.family = category_family(d.brand_category)
    _derive_demos(d)
    _derive_platforms(d)
    _derive_signals(d)
    _derive_lists(d)
    return d


def _derive_demos(d: Digest) -> None:
    age = d.demos.get("AGE", {})
    tot = sum(v for v in age.values() if v > 0) or 1.0
    for label, v in age.items():
        key = AGE_MAP.get(_norm(label))
        if key is None:
            m = re.match(r"^\s*(\d{2})\s*[-\u2013]\s*(\d{2})", str(label))
            if m:
                key = f"{m.group(1)}-{m.group(2)}"
            elif re.search(r"65|OLDER|\+", str(label).upper()):
                key = "65+"
            elif re.search(r"UNDER|17", str(label).upper()):
                key = "u17"
            else:
                continue
        d.age[key] = d.age.get(key, 0.0) + v / tot
    if age:
        d.top_age = max(age.items(), key=lambda kv: kv[1])[0]
    g = d.demos.get("GENDER", {})
    gt = sum(v for v in g.values() if v > 0) or 1.0
    for label, v in g.items():
        n = _norm(label)
        if n in ("FEMALE", "WOMEN", "F"):
            d.gender["female"] = d.gender.get("female", 0.0) + v / gt
        elif n in ("MALE", "MEN", "M"):
            d.gender["male"] = d.gender.get("male", 0.0) + v / gt
    inc = d.demos.get("INCOME", {})
    it = sum(v for v in inc.values() if v > 0) or 1.0
    hi = 0.0
    for label, v in inc.items():
        nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]{2,}", str(label))]
        if nums and nums[0] >= 100000 and not re.search(r"LESS|UNDER|BELOW", str(label).upper()):
            hi += v / it
    d.income_100k = hi
    if inc:
        d.top_income = max(inc.items(), key=lambda kv: kv[1])[0]
    par = d.demos.get("PARENTAL_STATUS", {})
    pt = sum(v for v in par.values() if v > 0) or 1.0
    for label, v in par.items():
        n = _norm(label)
        if n.startswith("HAS") or n in ("PARENT", "PARENTS"):
            d.parents += v / pt


def _match_rows(d: Digest, cats: List[str], aliases: List[str]) -> List[Dict[str, Any]]:
    want = {_norm(a) for a in aliases}
    out = []
    seen = set()
    for c in cats:
        for r in d.rows.get(c, []):
            n = _norm(r["name"])
            if n in want and n not in seen:
                seen.add(n)
                out.append(r)
    return out


def _derive_platforms(d: Digest) -> None:
    for key, (cats, aliases) in PLATFORM_ROWS.items():
        rows = _match_rows(d, cats, aliases)
        if not rows:
            continue
        best = max(rows, key=lambda r: r["bp"])
        d.platform[key] = {"reach": best["bp"], "index": best["index"], "name": best["name"]}


def _blend_reach(parts: List[float]) -> float:
    """Combined reach for a surface made of several rows (Meta = Facebook +
    Instagram): the largest row plus 35% of the rest, capped at 100."""
    parts = sorted([p for p in parts if p > 0], reverse=True)
    if not parts:
        return 0.0
    return min(100.0, parts[0] + 0.35 * sum(parts[1:]))


def _reach(d: Digest, key: str) -> float:
    return _f(d.platform.get(key, {}).get("reach"))


def _idx(d: Digest, key: str) -> float:
    v = _f(d.platform.get(key, {}).get("index"))
    return v if v > 0 else 100.0


def _derive_signals(d: Digest) -> None:
    S = d.S
    S["age_u35"] = d.age.get("18-24", 0.0) + d.age.get("25-34", 0.0)
    S["age_u35_all"] = S["age_u35"] + d.age.get("u17", 0.0)
    S["age_45p"] = d.age.get("45-54", 0.0) + d.age.get("55-64", 0.0) + d.age.get("65+", 0.0)
    S["age_55p"] = d.age.get("55-64", 0.0) + d.age.get("65+", 0.0)
    S["age_2554"] = d.age.get("25-34", 0.0) + d.age.get("35-44", 0.0) + d.age.get("45-54", 0.0)
    S["female"] = d.gender.get("female", 0.5)
    S["male"] = d.gender.get("male", 0.5)
    S["income_100k"] = d.income_100k
    S["parents"] = d.parents

    social_keys = ["instagram", "tiktok", "facebook", "snapchat", "x", "pinterest", "reddit", "linkedin"]
    S["social_reach"] = _blend_reach([_reach(d, k) for k in social_keys]) / 100.0
    sw = [(_reach(d, k), _clamp(_idx(d, k), 10.0, 500.0)) for k in social_keys if _reach(d, k) > 0]
    S["social_idx"] = (sum(r * i for r, i in sw) / (sum(r for r, _ in sw) or 1.0) / 100.0) if sw else 1.0
    S["search_reach"] = _reach(d, "google") / 100.0
    S["search_idx"] = _idx(d, "google") / 100.0
    ai_keys = ["chatgpt", "copilot", "perplexity", "gemini"]
    S["ai_reach"] = _blend_reach([_reach(d, k) for k in ai_keys]) / 100.0
    S["ai_idx"] = max([_idx(d, k) for k in ai_keys if _reach(d, k) > 0] or [1.0 * 100.0]) / 100.0
    ctv_keys = ["hulu", "netflix", "prime_video", "peacock", "paramount", "max", "tubi", "pluto", "roku",
                "freevee", "youtube_tv", "sling", "fubo", "philo", "directv_stream", "espn"]
    cw = [(_reach(d, k), _clamp(_idx(d, k), 10.0, 500.0)) for k in ctv_keys if _reach(d, k) > 0]
    S["ctv_reach"] = _blend_reach([r for r, _ in cw]) / 100.0
    S["ctv_idx"] = (sum(r * i for r, i in cw) / (sum(r for r, _ in cw) or 1.0) / 100.0) if cw else 1.0
    S["youtube_reach"] = _reach(d, "youtube") / 100.0
    S["youtube_idx"] = _idx(d, "youtube") / 100.0
    audio_keys = ["spotify", "pandora", "iheart", "siriusxm", "amazon_music", "apple_music", "youtube_music", "soundcloud"]
    aw = [(_reach(d, k), _clamp(_idx(d, k), 10.0, 500.0)) for k in audio_keys if _reach(d, k) > 0]
    S["audio_reach"] = _blend_reach([r for r, _ in aw]) / 100.0
    S["audio_idx"] = (sum(r * i for r, i in aw) / (sum(r for r, _ in aw) or 1.0) / 100.0) if aw else 1.0
    pods = d.cat("PODCAST", "PODCAST RANKER")
    S["podcast"] = _clamp(_top_mean(pods, 5) * 3.0 / 100.0, 0.0, 1.0)
    S["podcast_idx"] = _clamp(_weighted_index(pods, 5) / 100.0, 0.5, 3.0) if pods else 1.0
    games = d.cat(*GAME_CATS)
    S["games"] = _clamp((_top_mean(games, 5) * 2.0 + _reach(d, "twitch") * 0.6 + _reach(d, "discord") * 0.4 + _reach(d, "kick") * 0.5) / 100.0, 0.0, 1.0)
    media = d.cat("MEDIA", "NEWS", "DIGITAL MEDIA")
    S["media"] = _clamp(_top_mean(media, 8) / 100.0, 0.0, 1.0)
    S["media_idx"] = _clamp(_weighted_index(media, 8) / 100.0, 0.5, 3.0) if media else 1.0
    retail_keys = ["amazon", "walmart", "target", "costco", "kroger", "instacart", "sams_club", "best_buy"]
    _rr = sorted([_reach(d, k) for k in retail_keys if _reach(d, k) > 0], reverse=True)[:3]
    S["retail_reach"] = (sum(_rr) / len(_rr) / 100.0) if _rr else 0.0
    S["retail_top"] = ", ".join(f"{_pretty(d.platform[k]['name'])} {_pct(_reach(d, k))}" for k in sorted([k for k in retail_keys if _reach(d, k) > 0], key=lambda k: -_reach(d, k))[:2])
    mpb = [r for r in d.cat("MOST PURCHASED BRANDS", "CPG") if not _is_subject_adjacent(d, r["name"])]
    S["purchase_prop"] = _clamp(_median_index(mpb, 30) / 100.0, 0.5, 2.0) if mpb else 1.0
    S["purchase_reach"] = _clamp(_top_mean(mpb, 12) / 100.0, 0.0, 1.0)
    creators = _fit_ranked([r for r in d.cat("CREATOR/INFLUENCER", "INFLUENCER/CREATOR", "HOST/PERSONALITY", "PODCASTER") if not _is_subject_adjacent(d, r["name"])])
    S["creator"] = _clamp(_top_mean(creators, 5) / 100.0 * math.sqrt(_clamp(_weighted_index(creators, 5) / 100.0, 0.5, 4.0)), 0.0, 1.0) if creators else 0.0
    S["creator_reach"] = _top_mean(creators, 5) / 100.0 if creators else 0.0
    locs = sorted(d.locations, key=lambda r: -r["bp"])
    S["dma_conc"] = _clamp(sum(r["bp"] for r in locs[:5]) / 100.0, 0.0, 1.0)
    ooh = d.cat("EVENTS", "VENUE", "AMUSEMENT PARKS", "MOVIE THEATER", "WORKOUT FACILITY", "WHERE THEY DINE", "CASUAL DINING")
    S["ooh"] = _clamp(_top_mean(ooh, 6) / 100.0, 0.0, 1.0)
    S["fitness"] = _clamp(_top_mean(d.cat("WORKOUT FACILITY"), 3) / 100.0, 0.0, 1.0)
    S["dine_venue"] = _clamp(_top_mean(d.cat("WHERE THEY DINE", "CASUAL DINING", "VENUE", "EVENTS"), 5) / 100.0, 0.0, 1.0)
    S["movie"] = _clamp(_top_mean(d.cat("MOVIE THEATER"), 3) / 100.0, 0.0, 1.0)
    S["app_usage"] = _clamp(_top_mean(d.cat("APP/PLATFORM USAGE", "APP/PLATFORM"), 5) / 100.0, 0.0, 1.0)
    S["community"] = _clamp((_reach(d, "discord") + _reach(d, "reddit")) / 100.0, 0.0, 1.0)
    _club = [_reach(d, k) for k in ("costco", "sams_club") if _reach(d, k) > 0] + [r["bp"] for r in d.cat("MEMBERSHIP", "LOYALTY PROGRAMS")[:3]]
    S["loyalty"] = _clamp((sum(sorted(_club, reverse=True)[:3]) / len(sorted(_club, reverse=True)[:3]) / 100.0) if _club else 0.0, 0.0, 1.0)
    S["live"] = _clamp(0.5 * (_idx(d, "x") / 100.0) + 0.3 * (_reach(d, "espn") / 30.0) + 0.3 * (_reach(d, "twitch") / 40.0), 0.0, 2.0)
    for k in ["facebook", "instagram", "tiktok", "snapchat", "x", "pinterest", "linkedin", "reddit", "youtube",
              "twitch", "google", "chatgpt", "bing", "netflix", "hulu", "prime_video", "peacock", "max",
              "spotify", "pandora", "iheart", "siriusxm", "amazon", "walmart", "target"]:
        S[f"idx_{k}"] = _idx(d, k) / 100.0
        S[f"reach_{k}"] = _reach(d, k) / 100.0


def _derive_lists(d: Digest) -> None:
    subj = _norm(d.subject)
    qsr = {_norm(r["name"]) for r in d.cat("QSR", "WHERE THEY DINE")}
    pool = [r for r in d.cat("MOST PURCHASED BRANDS", "APPAREL/FOOTWEAR", "CPG", "BEAUTY/WELLNESS", "QSR")
            if not _is_subject_adjacent(d, r["name"]) and _norm(r["name"]) not in MASS_RETAILERS and r["bp"] >= 3.0]
    # purchased brands lead the seed list; a QSR only outranks them on a strong index
    pool.sort(key=lambda r: -(r["bp"] * math.sqrt(_clamp(r["index"], 50.0, 1500.0) / 100.0) * (0.55 if _norm(r["name"]) in qsr else 1.0)))
    d.top_brands = _dedupe_titlecase([r["name"] for r in pool])[:6]
    d.top_retailers = _dedupe_titlecase([r["name"] for r in d.cat("WHERE THEY SHOP", "RETAILERS") if not _is_subject_adjacent(d, r["name"])])[:4]
    cre = [r["name"] for r in _fit_ranked([r for r in d.cat("CREATOR/INFLUENCER", "INFLUENCER/CREATOR", "HOST/PERSONALITY", "PODCASTER") if not _is_subject_adjacent(d, r["name"]) and r["index"] >= 90])]
    d.top_creators = _dedupe_titlecase(cre)[:5]
    d.top_podcasts = _dedupe_titlecase([r["name"] for r in d.cat("PODCAST") if _norm(r["name"]) != subj])[:4]
    titles = [r["name"] for r in d.cat("SERIES", "TV SHOW", "MOVIE", "FRANCHISE") if _norm(r["name"]) != subj]
    d.top_titles = _dedupe_titlecase(titles)[:4]
    locs = [r for r in d.locations if _norm(r["name"]) != "UNITED STATES"]
    d.top_dmas = [_pretty_dma(r["name"]) for r in sorted(locs, key=lambda r: -r["bp"])[:3]]
    d.top_index_dmas = [_pretty_dma(r["name"]) for r in sorted(locs, key=lambda r: -r["index"])[:3]]


def _fit_ranked(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows ordered by reach x sqrt(index / 100): the names this audience
    both reaches and over-indexes on, not just the biggest names."""
    return sorted(rows, key=lambda r: -(r["bp"] * math.sqrt(_clamp(r["index"], 50.0, 1500.0) / 100.0)))


def _dedupe_titlecase(names: List[str]) -> List[str]:
    out, seen = [], set()
    for n in names:
        k = _norm(n)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(_pretty(n))
    return out


_CASING = {
    "MCDONALDS": "McDonald's", "CHICK FIL A": "Chick-fil-A", "IN N OUT BURGER": "In-N-Out Burger", "OFF WHITE": "Off-White",
    "CERAVE": "CeraVe", "IHEART": "iHeart", "EBAY": "eBay", "YOUTUBE": "YouTube", "TIKTOK": "TikTok", "LINKEDIN": "LinkedIn",
    "SIRIUSXM": "SiriusXM", "HBO MAX": "HBO Max", "ESPN": "ESPN", "NBC": "NBC", "CBS": "CBS", "ABC": "ABC", "CNN": "CNN",
    "KFC": "KFC", "UFC": "UFC", "WWE": "WWE", "NFL": "NFL", "NBA": "NBA", "MLB": "MLB", "CVS": "CVS", "AMC": "AMC",
    "DISNEY HULU": "Disney+/Hulu", "PARAMOUNT": "Paramount+", "APPLE TV": "Apple TV+", "AMAZON PRIME VIDEO": "Prime Video",
    "CHAT GPT": "ChatGPT", "X": "X", "SNAPCHAT": "Snapchat", "GYMSHARK": "Gymshark", "ALANI NU": "Alani Nu",
    "COCA COLA": "Coca-Cola", "BATH BODY WORKS": "Bath & Body Works", "TJ MAXX": "T.J. Maxx", "LOWES": "Lowe's",
    "SAMS CLUB": "Sam's Club", "MACYS": "Macy's", "KOHLS": "Kohl's", "DOMINOS": "Domino's", "WENDYS": "Wendy's",
    "ARBYS": "Arby's", "DUNKIN": "Dunkin'", "CRUMBL COOKIES": "Crumbl Cookies", "SHEIN": "Shein", "TEMU": "Temu",
    "PANDORA MUSIC": "Pandora", "AMAZON MUSIC": "Amazon Music", "APPLE MUSIC": "Apple Music", "GOOGLE": "Google",
    "BING": "Bing", "REDDIT": "Reddit", "DISCORD": "Discord", "TWITCH": "Twitch", "NETFLIX": "Netflix", "PEACOCK": "Peacock",
}
_ACRONYMS = {"NBC", "CBS", "ABC", "NFL", "NBA", "MLB", "NHL", "MLS", "ESPN", "HBO", "CNN", "BBC", "GM", "KFC", "UFC", "WWE",
             "CVS", "AMC", "BMW", "H&M", "UPS", "USA", "TV", "AT&T", "DC", "LA", "NY", "TX", "CA", "FL", "IL", "GA", "AZ",
             "DJ", "MC", "TJ", "JT", "PJ", "JJ", "CJ", "AJ", "RJ", "YG", "NLE", "XXL", "BTS", "NCT", "ATEEZ", "MLK", "NYC", "LSU", "UCLA", "USC"}


def _pretty(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return s
    canon = _CASING.get(_norm(s))
    if canon:
        return canon
    if s.isupper() and len(s) > 3:
        words = []
        for w in s.split(" "):
            if w.upper() in _ACRONYMS:
                words.append(w.upper())
            else:
                words.append("-".join(part.capitalize() for part in w.split("-")))
        return " ".join(words)
    return s


def _pretty_age(label: str) -> str:
    s = str(label or "").strip()
    return re.sub(r"\b(OR OLDER|AND UNDER|OR MORE|AND OVER)\b", lambda m: m.group(1).lower(), s)


def _pretty_dma(name: str) -> str:
    s = str(name or "").strip()
    s = re.sub(r"\s+(ca|ny|tx|fl|il|ga|az|wa|co|nv|or|mi|oh|pa|nc|ma|tn|mo|mn|md|dc|in|wi|va)$", lambda m: " " + m.group(1).upper(), s, flags=re.I)
    return _pretty(s) if s.isupper() else s


# ---------------------------------------------------------------------------
# Drivers (multipliers around 1.0 derived from the profile signals)
# ---------------------------------------------------------------------------


def derive_drivers(d: Digest) -> Dict[str, float]:
    S = d.S
    fam = d.family
    bc = (d.brand_category or "").upper()
    purchase = fam == "purchase"
    sport = fam == "sport"
    content = fam == "content"
    D: Dict[str, float] = {}
    D["short_video"] = _clamp(0.55 + 1.5 * S["age_u35"] + 0.25 * (S["idx_tiktok"] - 1) + 0.15 * (S["idx_snapchat"] - 1), 0.5, 1.7)
    D["feed_video"] = _clamp(0.6 + 1.1 * S["age_45p"] + 0.25 * (S["idx_facebook"] - 1), 0.5, 1.6)
    fam_shop = {"purchase": 0.45, "talent": -0.2, "content": -0.25, "sport": -0.05, "platform": -0.1, "healthcare": -0.25}.get(fam, 0.0)
    D["shopping"] = _clamp(0.95 + 0.9 * (S["purchase_prop"] - 1.0) + fam_shop + 0.1 * (S["retail_reach"] - 0.6), 0.5, 1.7)
    D["stories"] = _clamp(0.6 + 0.3 * (S["idx_instagram"] - 1) + 0.3 * (S["idx_snapchat"] - 1) + 0.6 * S["age_u35"], 0.5, 1.6)
    D["static"] = _clamp(0.65 + 0.8 * S["age_45p"] + 0.25 * (S["idx_pinterest"] - 1) + 0.2 * S["income_100k"], 0.5, 1.5)
    D["creator_native"] = _clamp(0.55 + 1.0 * S["age_u35"] + 0.6 * S["creator"] + (0.2 if fam == "talent" else 0.0), 0.5, 1.7)
    D["live"] = _clamp(0.6 + (0.8 if sport else 0.0) + 0.3 * (S["idx_x"] - 1) + 0.2 * min(S["live"], 2.0), 0.5, 1.7)
    D["premium"] = _clamp(0.7 + 0.4 * (S["idx_netflix"] - 1) + 0.3 * (S["idx_max"] - 1) + 0.4 * S["income_100k"], 0.5, 1.6)
    D["podcast"] = _clamp(0.6 + 2.0 * S["podcast"] + (0.4 if bc == "PODCASTER" or bc == "PODCAST" else 0.0), 0.5, 1.7)
    D["intent"] = _clamp(0.7 + (0.5 if purchase else 0.0) + 0.3 * (S["search_idx"] - 1), 0.6, 1.5)
    D["ai_search"] = _clamp(0.5 + 0.6 * S["ai_reach"] + 0.3 * (S["ai_idx"] - 1), 0.4, 1.7)
    D["older_search"] = _clamp(0.6 + 1.0 * S["age_45p"] + 0.3 * (S["idx_bing"] - 1), 0.5, 1.6)
    D["takeover"] = _clamp(0.7 + (0.4 if (content or sport) else 0.0) + 0.3 * S["dma_conc"], 0.5, 1.5)
    D["rewarded"] = _clamp(0.6 + 1.2 * S["games"] + 0.5 * S["age_u35"], 0.5, 1.7)
    D["visual_discovery"] = _clamp(0.6 + 0.6 * S["female"] + 0.3 * (S["idx_pinterest"] - 1), 0.5, 1.6)
    D["b2b"] = _clamp(0.5 + 0.8 * (S["idx_linkedin"] - 1) + 0.5 * S["income_100k"] + (0.4 if bc == "B2B" else 0.0), 0.4, 1.6)
    D["local"] = _clamp(0.6 + 1.2 * S["dma_conc"] + (0.3 if sport else 0.0), 0.5, 1.7)
    D["family"] = _clamp(0.7 + 0.8 * S["parents"] + 0.3 * min(S["live"], 1.5), 0.5, 1.6)
    D["fitness"] = _clamp(0.6 + 2.0 * S["fitness"] + 0.3 * S["age_u35"], 0.5, 1.6)
    D["dine_venue"] = _clamp(0.6 + 1.5 * S["dine_venue"], 0.5, 1.6)
    D["movie"] = _clamp(0.6 + 2.5 * S["movie"], 0.5, 1.7)
    D["community"] = _clamp(0.6 + 1.5 * S["community"], 0.5, 1.7)
    D["loyalty"] = _clamp(0.6 + 0.9 * S["loyalty"] + (0.2 if purchase else 0.0), 0.5, 1.6)
    D["audio_core"] = 1.0
    return D


# ---------------------------------------------------------------------------
# Channel + platform + format catalog
# ---------------------------------------------------------------------------

CHANNELS: List[Dict[str, Any]] = [
    {"key": "paidSocial", "label": "Paid Social", "icon": "📱", "prior": 20,
     "description": "Meta, TikTok, Snap, X, Reddit, Pinterest, LinkedIn, sized to this audience's social reach and index."},
    {"key": "paidSearch", "label": "Paid Search", "icon": "🔎", "prior": 14,
     "description": "Google Search, Performance Max, Bing and AI search. Always-on demand capture."},
    {"key": "ctv", "label": "Connected TV / Streaming", "icon": "📺", "prior": 14,
     "description": "Ad-supported streaming and live TV replacements, sized to streaming reach and index."},
    {"key": "onlineVideo", "label": "Online Video (YouTube)", "icon": "▶️", "prior": 10,
     "description": "YouTube pre-roll, Shorts and in-feed video, sized to YouTube reach."},
    {"key": "audio", "label": "Streaming Audio + Podcasts", "icon": "🎧", "prior": 7,
     "description": "Spotify, Pandora, iHeart, SiriusXM and podcast inventory, sized to audio and podcast reach."},
    {"key": "display", "label": "Programmatic Display", "icon": "🌐", "prior": 9,
     "description": "The Trade Desk and DV360 across publisher sites, sized to this audience's publisher reach."},
    {"key": "retail", "label": "Retail Media", "icon": "🛒", "prior": 8,
     "description": "Amazon, Walmart Connect and Roundel, sized to where this audience shops and how much it buys."},
    {"key": "gaming", "label": "Gaming / In-app", "icon": "🎮", "prior": 4,
     "description": "Twitch, rewarded video and in-game placements, sized to gaming and Twitch reach."},
    {"key": "ooh", "label": "Out of Home", "icon": "🏙️", "prior": 5,
     "description": "Billboards, transit, place-based and cinema, sized to DMA concentration and out-of-home habits."},
    {"key": "creator", "label": "Influencer / Creator", "icon": "🎬", "prior": 7,
     "description": "Creator roster deals and creator marketplaces, sized to the creators this audience already follows."},
    {"key": "owned", "label": "Email / Owned", "icon": "✉️", "prior": 5,
     "description": "Email, SMS, app push, owned social and loyalty, sized to purchase propensity and app habits."},
]

# Category family multipliers per channel (a beverage, a country artist, a
# sitcom, an NBA team and a creator do not share a plan).
FAMILY_MULT: Dict[str, Dict[str, float]] = {
    "purchase": {"retail": 1.6, "paidSocial": 1.15, "paidSearch": 1.05, "ctv": 0.9, "creator": 1.1, "owned": 1.25, "ooh": 1.0, "display": 0.9, "onlineVideo": 1.0, "audio": 0.9, "gaming": 0.8},
    "talent": {"creator": 1.4, "paidSocial": 1.25, "onlineVideo": 1.15, "audio": 1.2, "ctv": 1.0, "retail": 0.5, "paidSearch": 0.75, "owned": 1.0, "ooh": 0.8, "display": 0.8, "gaming": 0.9},
    "content": {"ctv": 1.5, "onlineVideo": 1.25, "paidSocial": 1.1, "paidSearch": 0.8, "retail": 0.45, "creator": 1.0, "audio": 0.9, "display": 0.9, "ooh": 0.9, "gaming": 0.8, "owned": 0.9},
    "sport": {"ctv": 1.35, "paidSocial": 1.1, "ooh": 1.3, "audio": 1.15, "onlineVideo": 1.0, "paidSearch": 0.75, "retail": 0.6, "creator": 0.9, "display": 0.9, "gaming": 1.1, "owned": 1.1},
    "platform": {"paidSearch": 1.3, "display": 1.2, "paidSocial": 1.0, "ctv": 1.1, "onlineVideo": 1.0, "retail": 0.4, "creator": 0.8, "audio": 0.8, "ooh": 0.9, "gaming": 0.9, "owned": 1.2},
    "healthcare": {"paidSearch": 1.4, "display": 1.2, "ctv": 1.1, "paidSocial": 0.9, "audio": 0.9, "retail": 0.5, "creator": 0.6, "onlineVideo": 0.9, "ooh": 1.0, "gaming": 0.4, "owned": 1.1},
    "other": {},
}
SUBCATEGORY_MULT: Dict[str, Dict[str, float]] = {
    "MUSICIAN/BAND": {"audio": 1.5, "creator": 1.1},
    "PODCASTER": {"audio": 1.7},
    "PODCAST": {"audio": 1.7},
    "ACTOR": {"ctv": 1.3},
    "ATHLETE": {"ctv": 1.15, "gaming": 1.15},
    "GAMES": {"gaming": 1.8, "onlineVideo": 1.15},
    "GAME PLAYERS": {"gaming": 1.8},
    "MOVIE": {"ooh": 1.25, "ctv": 1.2},
    "QSR": {"ooh": 1.25, "retail": 0.8},
    "AUTOMOBILE": {"ctv": 1.15, "paidSearch": 1.2, "retail": 0.5},
    "TRAVEL": {"paidSearch": 1.25, "retail": 0.5, "ctv": 1.1},
    "B2B": {"paidSearch": 1.2, "paidSocial": 1.1, "retail": 0.2, "ctv": 0.6},
}


def channel_weights(d: Digest) -> Dict[str, float]:
    S, fam = d.S, d.family
    sig = {
        "paidSocial": 0.55 * S["social_reach"] + 0.45 * _clamp(S["social_idx"], 0.5, 1.6) * 0.6,
        "paidSearch": 0.7 * S["search_reach"] + 0.3 * _clamp(S["search_idx"], 0.5, 1.5) * 0.6,
        "ctv": 0.55 * S["ctv_reach"] + 0.45 * _clamp(S["ctv_idx"], 0.5, 1.6) * 0.6,
        "onlineVideo": 0.6 * S["youtube_reach"] + 0.4 * _clamp(S["youtube_idx"], 0.5, 1.6) * 0.6,
        "audio": 0.45 * S["audio_reach"] + 0.3 * S["podcast"] + 0.25 * _clamp(S["audio_idx"], 0.5, 1.6) * 0.6,
        "display": 0.6 * S["media"] + 0.25 + 0.15 * _clamp(S["media_idx"], 0.5, 1.6) * 0.6,
        "retail": 0.45 * S["retail_reach"] + 0.35 * _clamp(S["purchase_prop"], 0.5, 2.0) * 0.5 + 0.2 * S["purchase_reach"],
        "gaming": 0.8 * S["games"] + 0.2 * S["age_u35_all"],
        "ooh": 0.55 * S["dma_conc"] + 0.45 * S["ooh"],
        "creator": 0.5 * S["creator"] + 0.3 * S["age_u35"] + 0.2 * _clamp((S["idx_tiktok"] + S["idx_youtube"]) / 2.0, 0.5, 1.6) * 0.6,
        "owned": 0.4 * _clamp(S["purchase_prop"], 0.5, 2.0) * 0.5 + 0.3 * S["app_usage"] + 0.3 * S["social_reach"] * 0.5,
    }
    tilt = {
        "paidSocial": 0.85 + 0.4 * S["age_u35"],
        "ctv": 0.85 + 0.35 * S["age_45p"],
        "display": 0.85 + 0.4 * S["age_45p"],
        "gaming": (0.7 + 0.6 * S["age_u35_all"]) * (0.85 + 0.3 * S["male"]),
        "creator": 0.75 + 0.6 * S["age_u35"],
        "retail": (0.9 + 0.2 * S["female"]) * (0.85 + 0.3 * S["parents"]),
        "owned": 0.9 + 0.3 * S["income_100k"],
        "audio": 0.9 + 0.3 * S["age_2554"],
    }
    fmult = FAMILY_MULT.get(fam, {})
    smult = SUBCATEGORY_MULT.get((d.brand_category or "").upper(), {})
    out = {}
    for ch in CHANNELS:
        k = ch["key"]
        w = ch["prior"] * (0.35 + 1.3 * _clamp(sig.get(k, 0.5), 0.0, 1.2)) * fmult.get(k, 1.0) * smult.get(k, 1.0) * tilt.get(k, 1.0)
        out[k] = max(0.2, w)
    return out


# Platform surfaces per channel. `rows`: which platform rows feed the reach
# and index (blended when several). `signal`: a Digest-level signal name for
# surfaces that have no single row (creator roster, OOH, owned). `inv` is a
# planner's inventory-scale prior (a Reddit or LinkedIn buy cannot absorb the
# same share as Meta at equal reach); it never varies by profile, the reach
# and index do.
PLATFORMS: List[Dict[str, Any]] = [
    {"key": "meta", "channel": "paidSocial", "icon": "📘", "name": "Meta (Facebook + Instagram)", "inv": 1.0, "rows": ["instagram", "facebook"], "floor": 3.0},
    {"key": "tiktok", "channel": "paidSocial", "icon": "🎵", "name": "TikTok", "inv": 0.9, "rows": ["tiktok"], "floor": 3.0},
    {"key": "snap", "channel": "paidSocial", "icon": "👻", "name": "Snapchat", "inv": 0.7, "rows": ["snapchat"], "floor": 2.5},
    {"key": "x", "channel": "paidSocial", "icon": "𝕏", "name": "X (Twitter)", "inv": 0.6, "rows": ["x"], "floor": 2.5},
    {"key": "reddit", "channel": "paidSocial", "icon": "🤖", "name": "Reddit", "inv": 0.5, "rows": ["reddit"], "floor": 2.5},
    {"key": "pinterest", "channel": "paidSocial", "icon": "📌", "name": "Pinterest", "inv": 0.7, "rows": ["pinterest"], "floor": 2.5},
    {"key": "linkedin", "channel": "paidSocial", "icon": "💼", "name": "LinkedIn", "inv": 0.45, "rows": ["linkedin"], "floor": 2.0},
    {"key": "google_search", "channel": "paidSearch", "icon": "🔎", "name": "Google Search + Performance Max", "inv": 1.0, "rows": ["google"], "floor": 6.0},
    {"key": "bing_ai", "channel": "paidSearch", "icon": "🧠", "name": "Bing + AI Search (Copilot, ChatGPT, Perplexity)", "inv": 0.6, "rows": ["bing", "chatgpt", "copilot", "perplexity", "gemini"], "floor": 3.0},
    {"key": "hulu", "channel": "ctv", "icon": "📺", "name": "Hulu (Disney Advertising)", "inv": 1.0, "rows": ["hulu"], "floor": 3.0},
    {"key": "netflix", "channel": "ctv", "icon": "🎬", "name": "Netflix (ad tier)", "inv": 0.85, "rows": ["netflix"], "floor": 3.0},
    {"key": "prime_video", "channel": "ctv", "icon": "📦", "name": "Prime Video (Amazon ads)", "inv": 0.9, "rows": ["prime_video", "freevee"], "floor": 3.0},
    {"key": "peacock", "channel": "ctv", "icon": "🦚", "name": "Peacock", "inv": 0.8, "rows": ["peacock"], "floor": 2.5},
    {"key": "paramount", "channel": "ctv", "icon": "⛰️", "name": "Paramount+", "inv": 0.75, "rows": ["paramount"], "floor": 2.5},
    {"key": "max", "channel": "ctv", "icon": "🎭", "name": "HBO Max", "inv": 0.7, "rows": ["max"], "floor": 2.5},
    {"key": "ytv_youtubeTV", "channel": "ctv", "icon": "📡", "name": "YouTube TV / live TV replacements", "inv": 0.7, "rows": ["youtube_tv", "sling", "fubo", "philo", "directv_stream", "espn"], "floor": 2.5},
    {"key": "roku", "channel": "ctv", "icon": "🟪", "name": "Roku Channel / Roku Ads", "inv": 0.75, "rows": ["roku"], "floor": 2.5},
    {"key": "tubi_pluto", "channel": "ctv", "icon": "📼", "name": "Tubi / Pluto TV / free streaming", "inv": 0.8, "rows": ["tubi", "pluto"], "floor": 2.5},
    {"key": "youtube", "channel": "onlineVideo", "icon": "▶️", "name": "YouTube (video + Shorts)", "inv": 1.0, "rows": ["youtube"], "floor": 6.0},
    {"key": "youtube_shorts", "channel": "onlineVideo", "icon": "📱", "name": "YouTube Shorts (vertical feed)", "inv": 0.8, "rows": ["youtube"], "floor": 4.0, "scale": "short_video"},
    {"key": "spotify", "channel": "audio", "icon": "🎵", "name": "Spotify", "inv": 1.0, "rows": ["spotify"], "floor": 3.0},
    {"key": "pandora_iheart", "channel": "audio", "icon": "📻", "name": "Pandora / iHeart / SiriusXM", "inv": 0.9, "rows": ["pandora", "iheart", "siriusxm"], "floor": 3.0},
    {"key": "podcast_network", "channel": "audio", "icon": "🎙️", "name": "Podcast networks (host-read + dynamic)", "inv": 0.9, "signal": "podcast", "floor": 3.0},
    {"key": "amazon_music", "channel": "audio", "icon": "🔊", "name": "Amazon Music (ad-supported)", "inv": 0.5, "rows": ["amazon_music"], "floor": 2.0},
    {"key": "dsp_display", "channel": "display", "icon": "🌐", "name": "Programmatic display + video (The Trade Desk, DV360)", "inv": 1.0, "signal": "media", "floor": 6.0},
    {"key": "news_publishers", "channel": "display", "icon": "📰", "name": "Direct publisher deals (the outlets this audience reads)", "inv": 0.7, "signal": "media", "floor": 3.0},
    {"key": "amazon_ads", "channel": "retail", "icon": "📦", "name": "Amazon Ads + DSP", "inv": 1.0, "rows": ["amazon"], "floor": 3.0},
    {"key": "walmart_connect", "channel": "retail", "icon": "🛒", "name": "Walmart Connect", "inv": 0.85, "rows": ["walmart"], "floor": 2.5},
    {"key": "target_roundel", "channel": "retail", "icon": "🎯", "name": "Target (Roundel)", "inv": 0.7, "rows": ["target"], "floor": 2.5},
    {"key": "grocery_media", "channel": "retail", "icon": "🥫", "name": "Grocery and club media (Kroger, Instacart, Costco)", "inv": 0.6, "rows": ["kroger", "instacart", "costco", "sams_club"], "floor": 2.0},
    {"key": "twitch_gaming", "channel": "gaming", "icon": "🎮", "name": "Twitch + in-game / rewarded", "inv": 1.0, "rows": ["twitch", "kick"], "signal": "games", "floor": 4.0},
    {"key": "mobile_games", "channel": "gaming", "icon": "📲", "name": "Mobile game networks (Unity, AppLovin)", "inv": 0.9, "signal": "games", "floor": 3.0},
    {"key": "ooh_dma", "channel": "ooh", "icon": "🛣️", "name": "Billboards + transit in the top DMAs", "inv": 1.0, "signal": "dma_conc", "floor": 4.0},
    {"key": "ooh_place", "channel": "ooh", "icon": "🏟️", "name": "Place-based digital (gyms, bars, venues, campuses)", "inv": 0.8, "signal": "ooh", "floor": 3.0},
    {"key": "ooh_cinema", "channel": "ooh", "icon": "🎞️", "name": "Cinema pre-show", "inv": 0.6, "signal": "movie", "floor": 2.0},
    {"key": "ooh_retail", "channel": "ooh", "icon": "🏬", "name": "In-store screens and retail out of home", "inv": 0.7, "signal": "retail_reach", "floor": 2.0},
    {"key": "creator_roster", "channel": "creator", "icon": "⭐", "name": "Creator roster (the creators this audience already follows)", "inv": 1.0, "signal": "creator", "floor": 4.0},
    {"key": "tiktok_creator", "channel": "creator", "icon": "🎵", "name": "TikTok Creator Marketplace", "inv": 0.8, "rows": ["tiktok"], "floor": 2.5},
    {"key": "youtube_brandconnect", "channel": "creator", "icon": "▶️", "name": "YouTube BrandConnect", "inv": 0.8, "rows": ["youtube"], "floor": 2.5},
    {"key": "instagram_partnership", "channel": "creator", "icon": "📸", "name": "Instagram partnership ads", "inv": 0.8, "rows": ["instagram"], "floor": 2.5},
    {"key": "twitch_sponsorship", "channel": "creator", "icon": "🟣", "name": "Twitch / Kick streamer deals", "inv": 0.6, "rows": ["twitch", "kick"], "floor": 2.0},
    {"key": "podcast_hostread", "channel": "creator", "icon": "🎙️", "name": "Podcast host-reads", "inv": 0.7, "signal": "podcast", "floor": 2.0},
    {"key": "email_crm", "channel": "owned", "icon": "✉️", "name": "Email / CRM", "inv": 1.0, "signal": "purchase_reach", "floor": 4.0},
    {"key": "sms_push", "channel": "owned", "icon": "💬", "name": "SMS + app push", "inv": 0.8, "signal": "app_usage", "floor": 3.0},
    {"key": "owned_social", "channel": "owned", "icon": "👥", "name": "Owned social + community", "inv": 0.8, "signal": "community", "floor": 3.0},
    {"key": "loyalty", "channel": "owned", "icon": "🎁", "name": "Loyalty / membership", "inv": 0.7, "signal": "loyalty", "floor": 2.5},
]

# Ad formats per platform: base weight and drivers (driver name -> exponent).
FORMATS: Dict[str, List[Dict[str, Any]]] = {
    "meta": [
        {"name": "Reels (short vertical video, 9-15s)", "base": 30, "drivers": {"short_video": 1.0}},
        {"name": "Advantage+ Shopping / Sales campaigns", "base": 18, "drivers": {"shopping": 1.0}},
        {"name": "Feed video (mid-funnel)", "base": 20, "drivers": {"feed_video": 1.0}},
        {"name": "Stories (vertical + tap-through)", "base": 14, "drivers": {"stories": 1.0}},
        {"name": "Feed static + carousel", "base": 12, "drivers": {"static": 1.0}},
    ],
    "tiktok": [
        {"name": "Spark Ads (boost creator content)", "base": 36, "drivers": {"creator_native": 1.0}},
        {"name": "In-Feed video (5-15s, sound-on)", "base": 30, "drivers": {"short_video": 0.5}},
        {"name": "TopView / Brand Takeover", "base": 14, "drivers": {"takeover": 1.0}},
        {"name": "Branded Hashtag Challenge / Effect", "base": 10, "drivers": {"short_video": 0.6, "creator_native": 0.4}},
        {"name": "TikTok Shop / Live Shopping", "base": 8, "drivers": {"shopping": 1.0}},
    ],
    "snap": [
        {"name": "Snap Ads (5-6s vertical video)", "base": 48, "drivers": {"short_video": 0.6}},
        {"name": "Story Ads / Discover placements", "base": 24, "drivers": {"stories": 1.0}},
        {"name": "AR Lens experiences", "base": 16, "drivers": {"creator_native": 0.6, "short_video": 0.4}},
        {"name": "Commercials (non-skippable, premium)", "base": 12, "drivers": {"takeover": 1.0}},
    ],
    "x": [
        {"name": "Promoted video (autoplay in feed)", "base": 38, "drivers": {"feed_video": 0.4, "live": 0.3}},
        {"name": "Promoted post + carousel", "base": 28, "drivers": {"static": 0.6}},
        {"name": "Trend Takeover / Timeline Takeover", "base": 20, "drivers": {"live": 1.0, "takeover": 0.5}},
        {"name": "Amplify pre-roll (video partners)", "base": 14, "drivers": {"live": 0.8}},
    ],
    "reddit": [
        {"name": "Promoted posts (image/video)", "base": 52, "drivers": {"static": 0.4}},
        {"name": "Free-form / Conversation Ads", "base": 28, "drivers": {"community": 0.6, "creator_native": 0.3}},
        {"name": "Trending Takeover", "base": 20, "drivers": {"takeover": 1.0}},
    ],
    "pinterest": [
        {"name": "Idea Pins (native video)", "base": 32, "drivers": {"visual_discovery": 1.0, "short_video": 0.3}},
        {"name": "Standard Pins (static image)", "base": 28, "drivers": {"static": 1.0}},
        {"name": "Shopping Ads (product feed)", "base": 26, "drivers": {"shopping": 1.0}},
        {"name": "Collections / Carousel", "base": 14, "drivers": {"shopping": 0.5, "static": 0.5}},
    ],
    "linkedin": [
        {"name": "Sponsored Content (single + carousel)", "base": 40, "drivers": {"b2b": 0.5}},
        {"name": "Video ads (autoplay)", "base": 25, "drivers": {"feed_video": 0.5}},
        {"name": "Message / Conversation Ads", "base": 20, "drivers": {"b2b": 1.0}},
        {"name": "Text / Dynamic Ads (right rail)", "base": 15, "drivers": {"static": 0.5}},
    ],
    "google_search": [
        {"name": "Branded + competitor keywords", "base": 28, "drivers": {"intent": 0.6}},
        {"name": "Non-branded / category intent", "base": 28, "drivers": {"intent": 1.0}},
        {"name": "Performance Max", "base": 24, "drivers": {"shopping": 0.6}},
        {"name": "Shopping (product feed)", "base": 12, "drivers": {"shopping": 1.2}},
        {"name": "Demand Gen (Discover + YouTube feeds)", "base": 8, "drivers": {"short_video": 0.5}},
    ],
    "bing_ai": [
        {"name": "Bing Search (branded + long-tail)", "base": 48, "drivers": {"older_search": 1.0}},
        {"name": "Microsoft Audience Network (native)", "base": 28, "drivers": {"static": 0.5, "older_search": 0.5}},
        {"name": "Copilot / ChatGPT conversational (test)", "base": 24, "drivers": {"ai_search": 1.0}},
    ],
    "hulu": [
        {"name": "30s / 15s in-stream", "base": 52, "drivers": {"feed_video": 0.4}},
        {"name": "Binge Ads / Pause Ads", "base": 16, "drivers": {"feed_video": 0.3}},
        {"name": "Disney+ / ESPN+ co-buy (household)", "base": 20, "drivers": {"family": 1.0}},
        {"name": "Interactive / shoppable", "base": 12, "drivers": {"shopping": 0.6}},
    ],
    "netflix": [
        {"name": "30s / 15s pre-roll + mid-roll", "base": 62, "drivers": {"feed_video": 0.3}},
        {"name": "Sponsorships (Top 10, tentpoles)", "base": 26, "drivers": {"premium": 1.0, "takeover": 0.3}},
        {"name": "Pause + branded moments", "base": 12, "drivers": {"shopping": 0.4, "static": 0.3}},
    ],
    "prime_video": [
        {"name": "30s / 15s in-stream (Prime Video ads)", "base": 56, "drivers": {"feed_video": 0.3}},
        {"name": "Thursday Night Football + live sports", "base": 20, "drivers": {"live": 1.0}},
        {"name": "Shoppable + Amazon DSP retargeting", "base": 24, "drivers": {"shopping": 0.8}},
    ],
    "peacock": [
        {"name": "30s / 15s in-stream", "base": 56, "drivers": {"feed_video": 0.3}},
        {"name": "Sports + tentpole sponsorships", "base": 28, "drivers": {"live": 1.0}},
        {"name": "Interactive shoppable ads", "base": 16, "drivers": {"shopping": 1.0}},
    ],
    "paramount": [
        {"name": "30s / 15s in-stream", "base": 60, "drivers": {"feed_video": 0.3}},
        {"name": "CBS Sports + live event sponsorships", "base": 24, "drivers": {"live": 1.0}},
        {"name": "Pause + branded moments", "base": 16, "drivers": {"static": 0.4}},
    ],
    "max": [
        {"name": "30s / 15s in-stream", "base": 66, "drivers": {"feed_video": 0.3}},
        {"name": "Tentpole / season sponsorship", "base": 34, "drivers": {"premium": 1.0}},
    ],
    "ytv_youtubeTV": [
        {"name": "Live sports pre-roll + mid-roll", "base": 56, "drivers": {"live": 1.0}},
        {"name": "Programmatic Guaranteed CTV", "base": 28, "drivers": {"feed_video": 0.4}},
        {"name": "Cable-network channel takeovers", "base": 16, "drivers": {"takeover": 1.0, "older_search": 0.3}},
    ],
    "roku": [
        {"name": "OneView in-stream", "base": 44, "drivers": {"feed_video": 0.3}},
        {"name": "Roku home screen billboard", "base": 20, "drivers": {"takeover": 1.0}},
        {"name": "Free channel placements", "base": 22, "drivers": {"older_search": 0.4, "static": 0.3}},
        {"name": "Action Ads (Marquee / CTA)", "base": 14, "drivers": {"shopping": 0.5}},
    ],
    "tubi_pluto": [
        {"name": "30s / 15s pre-roll (free streaming standard)", "base": 66, "drivers": {"feed_video": 0.3}},
        {"name": "Pause + interactive units", "base": 16, "drivers": {"shopping": 0.4}},
        {"name": "Sponsorship / branded content", "base": 18, "drivers": {"takeover": 0.5}},
    ],
    "youtube": [
        {"name": "In-stream pre-roll (skippable)", "base": 32, "drivers": {"feed_video": 0.6}},
        {"name": "YouTube Shorts", "base": 22, "drivers": {"short_video": 1.0}},
        {"name": "Bumper (6s non-skippable)", "base": 15, "drivers": {"takeover": 0.5}},
        {"name": "In-feed video discovery", "base": 15, "drivers": {"intent": 0.7}},
        {"name": "Masthead / CTV takeover", "base": 8, "drivers": {"takeover": 1.0, "premium": 0.5}},
    ],
    "youtube_shorts": [
        {"name": "Shorts in-feed video (vertical, 15-30s)", "base": 55, "drivers": {"short_video": 0.6}},
        {"name": "Shorts creator collabs", "base": 25, "drivers": {"creator_native": 0.8}},
        {"name": "Shorts shopping cards", "base": 20, "drivers": {"shopping": 0.8}},
    ],
    "spotify": [
        {"name": "Audio Everywhere (15-30s spots)", "base": 42, "drivers": {"audio_core": 1.0}},
        {"name": "Video Takeover / Sponsored Sessions", "base": 20, "drivers": {"short_video": 0.5}},
        {"name": "Podcast ads via Spotify Ad Network", "base": 26, "drivers": {"podcast": 1.0}},
        {"name": "Sponsored Playlist + Marquee", "base": 12, "drivers": {"creator_native": 0.4}},
    ],
    "pandora_iheart": [
        {"name": "Audio pre-roll + mid-roll (30s)", "base": 52, "drivers": {"older_search": 0.3}},
        {"name": "Podcast host-read / dynamic insertion", "base": 26, "drivers": {"podcast": 1.0}},
        {"name": "Station / show sponsorships", "base": 16, "drivers": {"local": 0.5, "older_search": 0.3}},
        {"name": "Companion display", "base": 6, "drivers": {"static": 0.5}},
    ],
    "podcast_network": [
        {"name": "Host-read mid-roll", "base": 50, "drivers": {"podcast": 1.0}},
        {"name": "Dynamic pre-roll (programmatic)", "base": 30, "drivers": {"audio_core": 1.0}},
        {"name": "Show sponsorship / presenting", "base": 20, "drivers": {"takeover": 0.5, "podcast": 0.5}},
    ],
    "amazon_music": [
        {"name": "Audio spots (ad-supported tier)", "base": 60, "drivers": {"audio_core": 1.0}},
        {"name": "Alexa and Fire TV companion", "base": 25, "drivers": {"shopping": 0.5, "family": 0.4}},
        {"name": "Playlist and station sponsorships", "base": 15, "drivers": {"creator_native": 0.3}},
    ],
    "dsp_display": [
        {"name": "Contextual + retargeting display", "base": 40, "drivers": {"static": 0.4}},
        {"name": "Programmatic video (outstream + instream)", "base": 26, "drivers": {"feed_video": 0.5}},
        {"name": "Native (Outbrain, Taboola)", "base": 14, "drivers": {"older_search": 0.5}},
        {"name": "Digital out of home via DSP", "base": 14, "drivers": {"local": 1.0}},
        {"name": "High-impact homepage takeovers", "base": 6, "drivers": {"takeover": 1.0}},
    ],
    "news_publishers": [
        {"name": "Sponsored content with the top outlets", "base": 45, "drivers": {"older_search": 0.4}},
        {"name": "Newsletter sponsorships", "base": 30, "drivers": {"b2b": 0.4, "older_search": 0.3}},
        {"name": "Homepage and section takeovers", "base": 25, "drivers": {"takeover": 1.0}},
    ],
    "amazon_ads": [
        {"name": "Sponsored Products", "base": 38, "drivers": {"shopping": 1.0}},
        {"name": "Sponsored Brands + video", "base": 24, "drivers": {"shopping": 0.5, "feed_video": 0.3}},
        {"name": "Amazon DSP (off-Amazon + Prime Video / Fire TV)", "base": 26, "drivers": {"premium": 0.5, "feed_video": 0.5}},
        {"name": "Sponsored Display", "base": 12, "drivers": {"static": 0.5}},
    ],
    "walmart_connect": [
        {"name": "Sponsored Products (onsite)", "base": 42, "drivers": {"shopping": 1.0}},
        {"name": "Sponsored Brands + video", "base": 20, "drivers": {"feed_video": 0.4}},
        {"name": "Onsite display / homepage", "base": 16, "drivers": {"static": 0.5}},
        {"name": "Offsite via The Trade Desk", "base": 22, "drivers": {"premium": 0.3, "feed_video": 0.4}},
    ],
    "target_roundel": [
        {"name": "Onsite search + browse", "base": 38, "drivers": {"shopping": 1.0}},
        {"name": "Offsite display + video", "base": 28, "drivers": {"feed_video": 0.5}},
        {"name": "CTV via Roundel", "base": 20, "drivers": {"premium": 0.5}},
        {"name": "In-store digital + Circle promos", "base": 14, "drivers": {"local": 0.5, "shopping": 0.3}},
    ],
    "grocery_media": [
        {"name": "Sponsored search (Kroger, Instacart)", "base": 45, "drivers": {"shopping": 1.0}},
        {"name": "Onsite display + coupons", "base": 30, "drivers": {"family": 0.5, "static": 0.3}},
        {"name": "Offsite retargeting + CTV", "base": 25, "drivers": {"feed_video": 0.4}},
    ],
    "twitch_gaming": [
        {"name": "Twitch mid-roll video", "base": 30, "drivers": {"live": 0.5, "rewarded": 0.5}},
        {"name": "Rewarded video (Unity, AppLovin)", "base": 28, "drivers": {"rewarded": 1.0}},
        {"name": "In-game display / native", "base": 20, "drivers": {"rewarded": 0.5}},
        {"name": "Roblox / Fortnite branded worlds", "base": 16, "drivers": {"rewarded": 1.0, "short_video": 0.3}},
        {"name": "Direct streamer sponsorship", "base": 6, "drivers": {"creator_native": 1.0}},
    ],
    "mobile_games": [
        {"name": "Rewarded video (opt-in)", "base": 50, "drivers": {"rewarded": 1.0}},
        {"name": "Playable ads", "base": 25, "drivers": {"short_video": 0.5}},
        {"name": "Interstitial video", "base": 25, "drivers": {"feed_video": 0.3}},
    ],
    "ooh_dma": [
        {"name": "Digital billboards (top DMAs)", "base": 45, "drivers": {"local": 1.0}},
        {"name": "Transit + street furniture", "base": 25, "drivers": {"local": 0.5, "short_video": 0.3}},
        {"name": "Static bulletins (long dwell)", "base": 18, "drivers": {"static": 0.5}},
        {"name": "Airport + commuter rail", "base": 12, "drivers": {"b2b": 0.5}},
    ],
    "ooh_place": [
        {"name": "Gym and fitness screens", "base": 30, "drivers": {"fitness": 1.0}},
        {"name": "Bars, restaurants and venues", "base": 35, "drivers": {"dine_venue": 1.0}},
        {"name": "Campus and office screens", "base": 15, "drivers": {"short_video": 0.4, "b2b": 0.4}},
        {"name": "Entertainment venues and stadiums", "base": 20, "drivers": {"live": 1.0}},
    ],
    "ooh_cinema": [
        {"name": "Pre-show :30 / :60", "base": 60, "drivers": {"movie": 1.0}},
        {"name": "Lobby + concession digital", "base": 25, "drivers": {"family": 0.5}},
        {"name": "Opening weekend sponsorship", "base": 15, "drivers": {"takeover": 1.0}},
    ],
    "ooh_retail": [
        {"name": "Checkout and aisle screens (Walmart, Kroger, Target)", "base": 50, "drivers": {"shopping": 1.0}},
        {"name": "Pharmacy and convenience screens", "base": 25, "drivers": {"older_search": 0.4}},
        {"name": "Shopping center digital", "base": 25, "drivers": {"local": 0.5}},
    ],
    "creator_roster": [
        {"name": "Sponsored posts and integrations", "base": 40, "drivers": {"creator_native": 1.0}},
        {"name": "Dedicated videos / long-form integrations", "base": 25, "drivers": {"feed_video": 0.5}},
        {"name": "Whitelisted creator ads (paid amplification)", "base": 25, "drivers": {"short_video": 0.5}},
        {"name": "Affiliate and code-driven offers", "base": 10, "drivers": {"shopping": 1.0}},
    ],
    "tiktok_creator": [
        {"name": "Spark-boosted creator videos", "base": 50, "drivers": {"short_video": 0.5}},
        {"name": "Creator challenges / duets", "base": 25, "drivers": {"creator_native": 0.5}},
        {"name": "Live shopping with creators", "base": 25, "drivers": {"shopping": 1.0}},
    ],
    "youtube_brandconnect": [
        {"name": "Integrated mentions (60-90s)", "base": 50, "drivers": {"feed_video": 0.4}},
        {"name": "Dedicated videos", "base": 30, "drivers": {"creator_native": 0.4}},
        {"name": "Shorts collabs", "base": 20, "drivers": {"short_video": 1.0}},
    ],
    "instagram_partnership": [
        {"name": "Partnership Reels", "base": 45, "drivers": {"short_video": 1.0}},
        {"name": "Partnership Stories", "base": 30, "drivers": {"stories": 1.0}},
        {"name": "Feed collabs + carousels", "base": 25, "drivers": {"static": 0.5}},
    ],
    "twitch_sponsorship": [
        {"name": "Stream sponsorships (overlay + shoutout)", "base": 50, "drivers": {"live": 0.5, "rewarded": 0.5}},
        {"name": "Sponsored streams / events", "base": 30, "drivers": {"live": 1.0}},
        {"name": "Clips + VOD integrations", "base": 20, "drivers": {"short_video": 0.5}},
    ],
    "podcast_hostread": [
        {"name": "Host-read mid-roll", "base": 55, "drivers": {"podcast": 1.0}},
        {"name": "Pre-roll (dynamic insertion)", "base": 25, "drivers": {"audio_core": 1.0}},
        {"name": "Show sponsorship / presenting", "base": 20, "drivers": {"takeover": 0.5}},
    ],
    "email_crm": [
        {"name": "Lifecycle flows (welcome, replenish, win-back)", "base": 45, "drivers": {"shopping": 0.5}},
        {"name": "Promotional sends", "base": 30, "drivers": {"shopping": 0.3}},
        {"name": "Content newsletter", "base": 25, "drivers": {"older_search": 0.3}},
    ],
    "sms_push": [
        {"name": "Launch / drop alerts", "base": 40, "drivers": {"short_video": 0.3}},
        {"name": "Cart and offer reminders", "base": 35, "drivers": {"shopping": 0.5}},
        {"name": "Loyalty nudges", "base": 25, "drivers": {"loyalty": 1.0}},
    ],
    "owned_social": [
        {"name": "Organic short video (Reels, TikTok, Shorts)", "base": 40, "drivers": {"short_video": 0.6}},
        {"name": "Community (Discord, Reddit, groups)", "base": 30, "drivers": {"community": 1.0}},
        {"name": "Live / Q&A moments", "base": 30, "drivers": {"live": 0.4, "creator_native": 0.3}},
    ],
    "loyalty": [
        {"name": "Points and tiers", "base": 45, "drivers": {"shopping": 0.5}},
        {"name": "Member-only drops + early access", "base": 35, "drivers": {"short_video": 0.3}},
        {"name": "Partner perks (co-brands)", "base": 20, "drivers": {"static": 0.3}},
    ],
}

# ---------------------------------------------------------------------------
# Per-platform signal (reach, index, matched rows, line)
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x:.1f}%"


def _idx_s(x: float) -> str:
    return f"{int(round(x))} index"


def platform_signal(d: Digest, p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reach and index for one surface read from the profile's rows. None
    when the profile carries nothing for it (no pill, floor share only)."""
    parts: List[Tuple[str, float, float]] = []
    for rk in p.get("rows", []) or []:
        pr = d.platform.get(rk)
        if pr and pr["reach"] > 0:
            parts.append((pr["name"], pr["reach"], _clamp(pr["index"], 10.0, 500.0)))
    sig_name = p.get("signal")
    if parts:
        reach = _blend_reach([r for _, r, _ in parts])
        wsum = sum(r for _, r, _ in parts) or 1.0
        index = sum(r * i for _, r, i in parts) / wsum
        if p.get("scale") == "short_video":
            reach = reach * _clamp(0.35 + 0.65 * d.S["age_u35_all"], 0.3, 1.0)
        line = ", ".join(f"{_pretty(n)} {_pct(r)}" for n, r, _ in sorted(parts, key=lambda t: -t[1])[:3])
        return {"reach": reach, "index": index, "line": f"{_pct(reach)} reach ({line}), {_idx_s(index)}", "rows": [_pretty(n) for n, _, _ in parts]}
    if sig_name:
        return _signal_surface(d, sig_name)
    return None


def _signal_surface(d: Digest, sig_name: str) -> Optional[Dict[str, Any]]:
    S = d.S
    if sig_name == "podcast":
        pods = d.cat("PODCAST", "PODCAST RANKER")
        if not pods:
            return None
        reach = _top_mean(pods, 5)
        idx = _weighted_index(pods, 5)
        names = ", ".join(_pretty(r["name"]) for r in pods[:3])
        return {"reach": reach, "index": idx, "line": f"Top podcasts average {_pct(reach)} reach ({names}), {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in pods[:5]]}
    if sig_name == "media":
        media = d.cat("MEDIA", "NEWS", "DIGITAL MEDIA")
        if not media:
            return None
        reach = _top_mean(media, 8)
        idx = _weighted_index(media, 8)
        names = ", ".join(_pretty(r["name"]) for r in media[:3])
        return {"reach": reach, "index": idx, "line": f"Top outlets average {_pct(reach)} reach ({names}), {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in media[:6]]}
    if sig_name == "games":
        games = d.cat(*GAME_CATS)
        tw = d.platform.get("twitch")
        if not games and not tw:
            return None
        reach = max(_top_mean(games, 5) if games else 0.0, tw["reach"] if tw else 0.0)
        idx = _weighted_index(games, 5) if games else (tw["index"] if tw else 100.0)
        bits = []
        if tw:
            bits.append(f"Twitch {_pct(tw['reach'])}")
        if games:
            bits.append(f"top games {_pct(_top_mean(games, 5))}")
        return {"reach": reach, "index": idx, "line": f"{', '.join(bits)}, {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in games[:4]]}
    if sig_name == "dma_conc":
        locs = sorted([r for r in d.locations if _norm(r["name"]) != "UNITED STATES"], key=lambda r: -r["bp"])
        if not locs:
            return None
        top5 = locs[:5]
        reach = sum(r["bp"] for r in top5)
        idx = sum(r["index"] for r in top5) / len(top5)
        names = ", ".join(f"{_pretty_dma(r['name'])} {_pct(r['bp'])}" for r in top5[:3])
        return {"reach": reach, "index": idx, "line": f"Top 5 DMAs hold {_pct(reach)} of this audience ({names}), {_idx_s(idx)}", "rows": [_pretty_dma(r["name"]) for r in top5]}
    if sig_name == "ooh":
        rows = d.cat("EVENTS", "VENUE", "AMUSEMENT PARKS", "MOVIE THEATER", "WORKOUT FACILITY", "WHERE THEY DINE", "CASUAL DINING")
        if not rows:
            return None
        reach = _top_mean(rows, 6)
        idx = _weighted_index(rows, 6)
        names = ", ".join(_pretty(r["name"]) for r in rows[:3])
        return {"reach": reach, "index": idx, "line": f"Out-of-home habits average {_pct(reach)} reach ({names}), {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in rows[:5]]}
    if sig_name == "movie":
        rows = d.cat("MOVIE THEATER")
        if not rows:
            return None
        reach = _top_mean(rows, 3)
        idx = _weighted_index(rows, 3)
        return {"reach": reach, "index": idx, "line": f"Theater chains average {_pct(reach)} reach ({', '.join(_pretty(r['name']) for r in rows[:3])}), {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in rows[:3]]}
    if sig_name == "retail_reach":
        parts = [(d.platform[k]["name"], d.platform[k]["reach"], d.platform[k]["index"]) for k in ["walmart", "target", "kroger", "costco"] if k in d.platform]
        if not parts:
            return None
        reach = _blend_reach([r for _, r, _ in parts])
        idx = sum(r * i for _, r, i in parts) / (sum(r for _, r, _ in parts) or 1.0)
        return {"reach": reach, "index": idx, "line": f"{_pct(reach)} shop the big-box set ({', '.join(f'{_pretty(n)} {_pct(r)}' for n, r, _ in parts[:3])}), {_idx_s(idx)}", "rows": [_pretty(n) for n, _, _ in parts]}
    if sig_name == "creator":
        rows = _fit_ranked([r for r in d.cat("CREATOR/INFLUENCER", "INFLUENCER/CREATOR", "HOST/PERSONALITY", "PODCASTER") if not _is_subject_adjacent(d, r["name"])])
        if not rows:
            return None
        reach = _top_mean(rows, 5)
        idx = _weighted_index(rows, 5)
        names = ", ".join(_pretty(r["name"]) for r in rows[:3])
        return {"reach": reach, "index": idx, "line": f"Top creators average {_pct(reach)} reach ({names}), {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in rows[:5]]}
    if sig_name == "purchase_reach":
        mpb = [r for r in d.cat("MOST PURCHASED BRANDS", "CPG") if _norm(r["name"]) != _norm(d.subject)]
        if not mpb:
            return None
        reach = _top_mean(mpb, 12)
        idx = _weighted_index(mpb, 12)
        return {"reach": reach, "index": idx, "line": f"Top purchased brands average {_pct(reach)} reach, {_idx_s(idx)} (purchase propensity)", "rows": [_pretty(r["name"]) for r in mpb[:5]]}
    if sig_name == "app_usage":
        rows = d.cat("APP/PLATFORM USAGE", "APP/PLATFORM")
        if not rows:
            return None
        reach = _top_mean(rows, 5)
        idx = _weighted_index(rows, 5)
        return {"reach": reach, "index": idx, "line": f"Top apps average {_pct(reach)} reach ({', '.join(_pretty(r['name']) for r in rows[:3])}), {_idx_s(idx)}", "rows": [_pretty(r["name"]) for r in rows[:5]]}
    if sig_name == "community":
        parts = [(d.platform[k]["name"], d.platform[k]["reach"], d.platform[k]["index"]) for k in ["discord", "reddit"] if k in d.platform]
        if not parts:
            return None
        reach = _blend_reach([r for _, r, _ in parts])
        idx = sum(r * i for _, r, i in parts) / (sum(r for _, r, _ in parts) or 1.0)
        return {"reach": reach, "index": idx, "line": f"{_pct(reach)} on community platforms ({', '.join(f'{_pretty(n)} {_pct(r)}' for n, r, _ in parts)}), {_idx_s(idx)}", "rows": [_pretty(n) for n, _, _ in parts]}
    if sig_name == "loyalty":
        parts = [(d.platform[k]["name"], d.platform[k]["reach"], d.platform[k]["index"]) for k in ["costco", "sams_club"] if k in d.platform]
        mem = d.cat("MEMBERSHIP", "LOYALTY PROGRAMS")
        allrows = parts + [(r["name"], r["bp"], r["index"]) for r in mem[:3]]
        if not allrows:
            return None
        allrows.sort(key=lambda t: -t[1])
        reach = sum(r for _, r, _ in allrows[:3]) / len(allrows[:3])
        idx = sum(r * i for _, r, i in allrows) / (sum(r for _, r, _ in allrows) or 1.0)
        return {"reach": reach, "index": idx, "line": f"Club and membership rows average {_pct(reach)} reach ({', '.join(_pretty(n) for n, _, _ in allrows[:3])}), {_idx_s(idx)}", "rows": [_pretty(n) for n, _, _ in allrows[:5]]}
    return None


def priority_for(reach: float, index: float) -> Dict[str, str]:
    """Pill from BOTH reach and index. A 25% reach platform at 56 index is a
    low priority even though a quarter of the audience is there."""
    if (reach >= 25 and index >= 115) or (reach >= 50 and index >= 95):
        return {"key": "high", "label": "High Priority"}
    if (reach >= 12 and index >= 85) or reach >= 30:
        return {"key": "medium", "label": "Medium Priority"}
    if reach >= 5 and index >= 60:
        return {"key": "consider", "label": "Consider"}
    return {"key": "skip", "label": "Low Priority"}


# ---------------------------------------------------------------------------
# Deterministic copy
# ---------------------------------------------------------------------------

TIER2_VERBS = ("leans", "skews", "reads as", "tends to", "lands closer to", "consistent with")
BANNED_WORDS = ("synth", "modeled", "modelled", " model", "estimated", "predicted", "panel", "panelist",
                "claude", "anthropic", "gpt", "llm", " ai ", "door", "\u2014", "\u2013", "hostmap", "pipeline",
                "jitter", "multiplier", "clamp")


def _lead_format(d: Digest, formats: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(formats, key=lambda f: f["pct"])


def _age_phrase(d: Digest) -> str:
    S = d.S
    if S["age_u35"] >= 0.45:
        return f"{S['age_u35'] * 100:.1f}% of this audience is 18 to 34"
    if S["age_45p"] >= 0.45:
        return f"{S['age_45p'] * 100:.1f}% of this audience is 45 or older"
    return f"the center of this audience is 25 to 54 ({S['age_2554'] * 100:.1f}%)"


def _gender_phrase(d: Digest) -> str:
    f, m = d.S["female"], d.S["male"]
    if f >= 0.55:
        return f"{f * 100:.1f}% female"
    if m >= 0.55:
        return f"{m * 100:.1f}% male"
    return "near-even on gender"


def _join(items: List[str], n: int = 3) -> str:
    items = [i for i in items if i][:n]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def platform_copy(d: Digest, p: Dict[str, Any], sig: Optional[Dict[str, Any]], formats: List[Dict[str, Any]], D: Dict[str, float]) -> str:
    """Buying guidance written from this profile's own rows."""
    S = d.S
    key = p["key"]
    brands = _join(d.top_brands, 3)
    dmas = _join(d.top_dmas, 2)
    lead = _lead_format(d, formats)["name"].split(" (")[0]
    age = _age_phrase(d)
    income = (d.top_income or "").strip()
    inc_line = f" Income centers on {income}." if income else ""
    creators = _join(d.top_creators, 3)
    pods = _join(d.top_podcasts, 2)
    reach_line = f"{_pct(sig['reach'])} reach at {int(round(sig['index']))} index" if sig else "no direct row in this profile"
    if sig and priority_for(sig["reach"], sig["index"])["key"] == "skip":
        return f"Low priority for this audience: {reach_line}. Keep any test small; {lead} first."
    if key == "meta":
        seed = f"Seed lookalikes with {brands}." if brands else "Seed lookalikes with the top purchased brands below."
        return f"{seed} Layer {_pretty_age(d.top_age) or 'the top age bucket'} and the {income or 'top income'} band; weight {dmas or 'the top DMAs'}. {lead} leads because {age}."
    if key == "tiktok":
        who = f"creators in the {creators} lane" if creators else "the creators this audience already follows"
        return f"Spark Ads on {who}; interest seeds from {brands or 'the top purchased brands'}. {reach_line}; {age}."
    if key == "snap":
        return f"Vertical 5 to 6 second cuts aimed at the {_pretty_age(d.top_age) or 'youngest'} bucket; {reach_line}. Use {dmas or 'the top DMAs'} as the first geo flight."
    if key == "x":
        return f"Lean on live moments and conversation targeting around {_join(d.top_titles or d.top_creators or d.top_brands, 2) or 'the categories this audience follows'}; {reach_line}."
    if key == "reddit":
        return f"Subreddit targeting around {_join(d.top_brands or d.top_titles, 2) or 'the audience interests'}; native copy, no polish. {reach_line}."
    if key == "pinterest":
        return f"Planner and product formats; {_gender_phrase(d)} with the {income or 'top income'} band.{'' if not brands else ' Product pins on ' + brands + '.'} {reach_line}."
    if key == "linkedin":
        return f"Only for lead-gen or professional reach; {reach_line}.{inc_line} Keep spend tight unless the brief is B2B."
    if key == "google_search":
        return f"Branded plus category-intent keywords; feed Performance Max the {brands or 'top brand'} lookalikes and the {_pretty_age(d.top_age) or 'top age'} skew. Google reach {_pct(_reach(d, 'google'))} at {int(round(_idx(d, 'google')))} index."
    if key == "bing_ai":
        ai = f"AI search reach {_pct(S['ai_reach'] * 100)}" if S["ai_reach"] > 0 else "AI search is small here"
        return f"Bing for the {'older' if S['age_45p'] >= 0.4 else 'long-tail'} share ({S['age_45p'] * 100:.1f}% are 45 or older); {ai}, so {'test conversational placements with real budget' if S['ai_reach'] >= 0.3 else 'keep conversational placements as a small test'}."
    if p["channel"] == "ctv":
        verdict = "a core buy" if sig and sig["index"] >= 115 and sig["reach"] >= 25 else ("a frequency layer" if sig and sig["reach"] >= 15 else "a test line")
        return f"{reach_line}: {verdict}. Frequency-cap by DMA, starting with {dmas or 'the top DMAs'}; {lead} first."
    if key == "youtube":
        return f"YouTube reach {_pct(_reach(d, 'youtube'))} at {int(round(_idx(d, 'youtube')))} index. Build Custom Affinity from {brands or 'the top brands'}{(' and ' + _join(d.top_creators, 2)) if d.top_creators else ''}; {lead} leads because {age}."
    if key == "youtube_shorts":
        return f"Shorts earns its own line when the audience is young: {age}. Cut the pre-roll creative to vertical 15s and pair with {_join(d.top_creators, 2) or 'creator collabs'}."
    if p["channel"] == "audio":
        podline = f" Podcasts worth a host-read: {pods}." if pods else ""
        return f"{reach_line}. {lead} first;{podline} {_gender_phrase(d)}, {age}."
    if key == "dsp_display":
        outlets = _join([r for r in (sig or {}).get("rows", [])], 3)
        return f"Contextual plus retargeting across {outlets or 'the publisher set this audience reads'}; build custom lookalikes from {brands or 'the top brands'}. {reach_line}."
    if key == "news_publishers":
        outlets = _join([r for r in (sig or {}).get("rows", [])], 3)
        return f"Direct deals with {outlets or 'the top outlets'}; {reach_line}. Sponsored content reads better than banners for the {_pretty_age(d.top_age) or 'core'} bucket."
    if p["channel"] == "retail":
        pp = S["purchase_prop"]
        return f"{reach_line}. Purchase propensity reads at {int(round(pp * 100))} index on the top purchased brands ({brands or 'see Brand Partners'}); {lead} first."
    if key == "twitch_gaming" or key == "mobile_games":
        games = _join([r for r in (sig or {}).get("rows", [])], 3)
        return f"{reach_line}{('; top games ' + games) if games else ''}. {lead} first; {S['male'] * 100:.1f}% male, {age}."
    if key == "ooh_dma":
        return f"{sig['line'] if sig else 'DMA rows are thin in this profile'}. Start with {_join(d.top_index_dmas, 3) or dmas or 'the top DMAs'} where the index is highest."
    if key == "ooh_place":
        return f"{sig['line'] if sig else 'Out-of-home rows are thin here'}. {lead} first."
    if key == "ooh_cinema":
        return f"{sig['line'] if sig else 'Theater rows are thin here'}. {'Worth a flight around tentpoles' if S['movie'] >= 0.2 else 'A small test line'}."
    if key == "ooh_retail":
        return f"{sig['line'] if sig else 'Big-box rows are thin here'}. Pair with the retail media lines above."
    if key == "creator_roster":
        return f"Start with {creators or 'the Creator Roster tab'}: {sig['line'] if sig else 'creator rows are thin here'}. {lead} first; {age}."
    if key == "tiktok_creator":
        return f"TikTok reach {_pct(_reach(d, 'tiktok'))} at {int(round(_idx(d, 'tiktok')))} index; brief creators in the {creators or 'audience'} lane. {lead} first."
    if key == "youtube_brandconnect":
        return f"YouTube reach {_pct(_reach(d, 'youtube'))}; {lead} with {creators or 'the top creators'}. {age}."
    if key == "instagram_partnership":
        return f"Instagram reach {_pct(_reach(d, 'instagram'))} at {int(round(_idx(d, 'instagram')))} index; {lead} with {creators or 'the top creators'}."
    if key == "twitch_sponsorship":
        return f"Twitch reach {_pct(_reach(d, 'twitch'))} at {int(round(_idx(d, 'twitch')))} index; {lead} first. {S['male'] * 100:.1f}% male, {age}."
    if key == "podcast_hostread":
        return f"{sig['line'] if sig else 'Podcast rows are thin here'}.{(' Start with ' + pods + '.') if pods else ''}"
    if key == "email_crm":
        return f"{sig['line'] if sig else 'Purchase rows are thin here'}.{inc_line} {lead} first."
    if key == "sms_push":
        return f"{sig['line'] if sig else 'App rows are thin here'}; {age}. {lead} first."
    if key == "owned_social":
        return f"{sig['line'] if sig else 'Community rows are thin here'}. {lead} first; {age}."
    if key == "loyalty":
        return f"{sig['line'] if sig else 'Membership rows are thin here'}. {lead} first."
    return f"{reach_line}. {lead} first."


def _lean(share: float, prior: float, up: str, flat: str, down: str) -> str:
    r = share / max(prior, 0.1)
    return up if r >= 1.15 else (down if r <= 0.85 else flat)


def channel_rationale(d: Digest, ch_key: str, share: float, top_platform: Optional[Dict[str, Any]]) -> str:
    """Deterministic one-line directional read per channel."""
    S = d.S
    prior = next((c["prior"] for c in CHANNELS if c["key"] == ch_key), 10)
    tp = f"{top_platform['name'].split(' (')[0]} at {_pct(top_platform['reach'])} reach, {int(round(top_platform['index']))} index" if top_platform and top_platform.get("reach") else None
    if ch_key == "paidSocial":
        return f"{_lean(share, prior, 'Leans social-first', 'Social holds a steady line', 'Social stays lighter than usual')}: {tp or 'social rows are thin'}; {_age_phrase(d)}."
    if ch_key == "paidSearch":
        return f"Search reads as always-on demand capture: Google {_pct(_reach(d, 'google'))} reach, AI search {_pct(S['ai_reach'] * 100)}."
    if ch_key == "ctv":
        return f"Streaming {_lean(share, prior, 'skews heavy', 'tends to run at a steady pace', 'stays light')}: {tp or 'streaming rows are thin'}; {S['age_45p'] * 100:.1f}% are 45 or older."
    if ch_key == "onlineVideo":
        return f"YouTube reads as {'a reach anchor' if S['youtube_reach'] >= 0.8 else 'a mid-weight line'} at {_pct(S['youtube_reach'] * 100)} reach, {int(round(S['youtube_idx'] * 100))} index."
    if ch_key == "audio":
        return f"Audio {_lean(share, prior, 'leans in', 'holds a steady line', 'stays light')}: {tp or 'audio rows are thin'}; podcasts {'carry real reach' if S['podcast'] >= 0.3 else 'are a light layer'}."
    if ch_key == "display":
        return f"Display {_lean(share, prior, 'leans in as more than the floor', 'reads as the always-on floor', 'stays a thin floor')}; the outlets this audience reads average {_pct(S['media'] * 100)} reach at {int(round(S['media_idx'] * 100))} index."
    if ch_key == "retail":
        return f"Retail media {_lean(share, prior, 'leans in', 'holds a conversion layer', 'stays a light conversion layer')}: purchase propensity {int(round(S['purchase_prop'] * 100))} index{(' (' + S['retail_top'] + ')') if S.get('retail_top') else ''}."
    if ch_key == "gaming":
        return f"Gaming {_lean(share, prior, 'skews in', 'holds a small line', 'stays a light line')}: Twitch {_pct(_reach(d, 'twitch'))} reach, {S['male'] * 100:.1f}% male, {S['age_u35_all'] * 100:.1f}% under 35."
    if ch_key == "ooh":
        return f"Out of home {_lean(share, prior, 'leans local', 'holds a light local line', 'stays national-light')}: top 5 DMAs hold {_pct(S['dma_conc'] * 100)} of the audience."
    if ch_key == "creator":
        return f"Creator deals {_lean(share, prior, 'lead', 'hold a supporting line', 'stay light')}: the creators this audience follows most ({_join(d.top_creators, 2) or 'see Creator Roster'}) reach {_pct(S['creator_reach'] * 100)} of it; {S['age_u35'] * 100:.1f}% are 18 to 34."
    if ch_key == "owned":
        return f"Owned channels {_lean(share, prior, 'lean in', 'hold a steady line', 'stay light')}: purchase propensity {int(round(S['purchase_prop'] * 100))} index, {_pct(S['income_100k'] * 100)} earn $100K or more."
    return f"Reads as a {'core' if share >= 15 else 'supporting'} line at {share:.1f}%."


def format_note(d: Digest, fmt: Dict[str, Any], D: Dict[str, float], pct: float) -> str:
    """Why this format sits where it does, in the audience's own numbers."""
    S = d.S
    drivers = fmt.get("drivers", {})
    if not drivers:
        return f"Holds {pct:.1f}% of this platform."
    lead = max(drivers.items(), key=lambda kv: abs(math.log(max(D.get(kv[0], 1.0), 1e-6))) * kv[1])[0]
    m = D.get(lead, 1.0)
    up, down = m >= 1.12, m <= 0.88
    if lead == "short_video":
        n = f"{S['age_u35'] * 100:.1f}% are 18 to 34"
        return f"{n}; short vertical video {'leads' if up else 'stays light' if down else 'holds a middle weight'}."
    if lead == "feed_video":
        return f"{S['age_45p'] * 100:.1f}% are 45 or older; longer feed video {'leans in' if up else 'stays light' if down else 'holds'}."
    if lead == "shopping":
        return f"Purchase propensity {int(round(S['purchase_prop'] * 100))} index; shopping formats {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "stories":
        return f"Instagram {int(round(S['idx_instagram'] * 100))} and Snapchat {int(round(S['idx_snapchat'] * 100))} index; tap-through {'leans in' if up else 'stays light' if down else 'holds'}."
    if lead == "static":
        return f"{S['age_45p'] * 100:.1f}% are 45 or older, Pinterest {int(round(S['idx_pinterest'] * 100))} index; static {'leans in' if up else 'stays light' if down else 'holds'}."
    if lead == "creator_native":
        return f"The creators this audience follows reach {_pct(S['creator_reach'] * 100)} of it; creator-led units {'lead' if up else 'stay light' if down else 'hold'}."
    if lead == "live":
        return f"X {int(round(S['idx_x'] * 100))} index, ESPN {_pct(_reach(d, 'espn'))} reach; live moments {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "premium":
        return f"Netflix {int(round(S['idx_netflix'] * 100))} index, {_pct(S['income_100k'] * 100)} earn $100K or more; premium placements {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "podcast":
        return f"Top podcasts average {_pct(S['podcast'] * 100 / 3.0)} reach; host-reads {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "intent":
        return f"Google {int(round(S['search_idx'] * 100))} index; intent capture {'leans in' if up else 'stays light' if down else 'holds'}."
    if lead == "ai_search":
        return f"AI search reach {_pct(S['ai_reach'] * 100)}; conversational placements {'earn a real test' if up else 'stay a small test' if down else 'hold a test line'}."
    if lead == "older_search":
        return f"{S['age_45p'] * 100:.1f}% are 45 or older, Bing {int(round(S['idx_bing'] * 100))} index."
    if lead == "takeover":
        return f"Top 5 DMAs hold {_pct(S['dma_conc'] * 100)}; high-impact days {'lean in' if up else 'stay rare' if down else 'hold'}."
    if lead == "rewarded":
        return f"Gaming signal {_pct(S['games'] * 100)}, {S['age_u35_all'] * 100:.1f}% under 35; opt-in units {'lead' if up else 'stay light' if down else 'hold'}."
    if lead == "visual_discovery":
        return f"{S['female'] * 100:.1f}% female, Pinterest {int(round(S['idx_pinterest'] * 100))} index."
    if lead == "b2b":
        return f"LinkedIn {int(round(S['idx_linkedin'] * 100))} index, {_pct(S['income_100k'] * 100)} earn $100K or more."
    if lead == "local":
        return f"Top 5 DMAs hold {_pct(S['dma_conc'] * 100)} of the audience; local buys {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "family":
        return f"{S['parents'] * 100:.1f}% are parents; household co-buys {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "fitness":
        return f"Gym chains average {_pct(S['fitness'] * 100)} reach."
    if lead == "dine_venue":
        return f"Dining and venue rows average {_pct(S['dine_venue'] * 100)} reach."
    if lead == "movie":
        return f"Theater chains average {_pct(S['movie'] * 100)} reach."
    if lead == "community":
        return f"Discord and Reddit reach {_pct(S['community'] * 100)}; community units {'lean in' if up else 'stay light' if down else 'hold'}."
    if lead == "loyalty":
        return f"Club and membership reach {_pct(S['loyalty'] * 100)}."
    return f"Holds {pct:.1f}% of this platform."


# ---------------------------------------------------------------------------
# Deterministic plan
# ---------------------------------------------------------------------------


def _salt(d: Digest, s3_key: str, scope: str) -> str:
    return f"{d.subject}|{s3_key}|{scope}"


def format_weights(d: Digest, platform_key: str, D: Dict[str, float]) -> Dict[str, float]:
    out = {}
    for fmt in FORMATS.get(platform_key, []):
        w = float(fmt["base"])
        for drv, ex in (fmt.get("drivers") or {}).items():
            w *= max(D.get(drv, 1.0), 1e-6) ** float(ex)
        out[fmt["name"]] = w
    return out


def build_deterministic_plan(d: Digest, s3_key: str = "") -> Dict[str, Any]:
    D = derive_drivers(d)
    cw = channel_weights(d)
    ch_shares = messy_split(cw, _salt(d, s3_key, "channels"))
    channels: List[Dict[str, Any]] = []
    for ch in sorted(CHANNELS, key=lambda c: -ch_shares[c["key"]]):
        ck = ch["key"]
        plats = [p for p in PLATFORMS if p["channel"] == ck]
        enriched = []
        for p in plats:
            sig = platform_signal(d, p)
            inv = float(p.get("inv", 1.0))
            if sig and sig["reach"] > 0:
                w = sig["reach"] * (_clamp(sig["index"], 40.0, 400.0) / 100.0) ** 0.6 * inv
                w = max(w, p.get("floor", 2.5))
            else:
                w = p.get("floor", 2.5) * 0.8
            enriched.append((p, sig, w))
        p_shares = messy_split({p["key"]: w for p, _, w in enriched}, _salt(d, s3_key, f"platforms:{ck}"))
        plat_out = []
        for p, sig, _w in sorted(enriched, key=lambda t: -p_shares[t[0]["key"]]):
            fw = format_weights(d, p["key"], D)
            f_shares = messy_split(fw, _salt(d, s3_key, f"formats:{p['key']}")) if fw else {}
            formats = []
            for fmt in FORMATS.get(p["key"], []):
                pct = f_shares.get(fmt["name"], 0.0)
                formats.append({"name": fmt["name"], "pct": pct, "note": format_note(d, fmt, D, pct)})
            formats.sort(key=lambda f: -f["pct"])
            pri = priority_for(sig["reach"], sig["index"]) if sig else None
            plat_out.append({
                "key": p["key"], "name": p["name"], "icon": p["icon"],
                "share": p_shares[p["key"]],
                "reach": round(sig["reach"], 1) if sig else None,
                "index": int(round(sig["index"])) if sig else None,
                "priority": pri,
                "signal_line": sig["line"] if sig else "No direct row for this surface in the profile; floor allocation only.",
                "seed": (sig or {}).get("rows", [])[:4],
                "copy": platform_copy(d, p, sig, formats, D) if formats else "",
                "formats": formats,
            })
        top = plat_out[0] if plat_out else None
        channels.append({
            "key": ck, "label": ch["label"], "icon": ch["icon"], "share": ch_shares[ck],
            "description": ch["description"],
            "signal_line": (top["signal_line"] if top and top.get("reach") is not None else ""),
            "rationale": channel_rationale(d, ck, ch_shares[ck], top),
            "platforms": plat_out,
        })
    S = d.S
    return {
        "version": PLAN_VERSION,
        "profile_key": s3_key,
        "subject": d.subject,
        "brand_category": d.brand_category,
        "family": d.family,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reasoned": False,
        "audience": {
            "age_u35_pct": round(S["age_u35"] * 100, 1),
            "age_45p_pct": round(S["age_45p"] * 100, 1),
            "female_pct": round(S["female"] * 100, 1),
            "male_pct": round(S["male"] * 100, 1),
            "income_100k_pct": round(S["income_100k"] * 100, 1),
            "parents_pct": round(S["parents"] * 100, 1),
            "purchase_propensity_index": int(round(S["purchase_prop"] * 100)),
            "top_age": _pretty_age(d.top_age), "top_income": d.top_income,
            "top_retailers": d.top_retailers,
            "top_dmas": d.top_dmas, "top_index_dmas": d.top_index_dmas,
            "top_brands": d.top_brands, "top_creators": d.top_creators, "top_podcasts": d.top_podcasts,
        },
        "channels": channels,
    }


# ---------------------------------------------------------------------------
# Model layer (one short cached call per profile)
# ---------------------------------------------------------------------------

MODEL_SYSTEM = (
    "You are Crosswalk's senior media planner. You read ONE audience's own numbers and tune a "
    "digital media plan for it. Return ONLY a JSON object, no prose before or after.\n\n"
    "Rules:\n"
    "- Use only the numbers given. Never invent reach, index or share figures.\n"
    "- channel_rationale: one sentence per channel key, 10 to 24 words, plain words a high school "
    "student follows, and directional verbs (leans, skews, reads as, tends to). Name the audience's own "
    "platform, age, gender or brand facts from the digest.\n"
    "- platform_copy: one or two plain sentences of buying guidance per platform key that name this "
    "audience's own brands, DMAs, age band or income band from the digest.\n"
    "- format_weights: for each requested platform key, a weight per format name (exact names given), "
    "one-decimal numbers summing to about 100. Move weight toward the formats this audience's shape "
    "supports and away from the ones it does not. Change at least two formats per platform by 3 or more "
    "points when the digest gives you a reason.\n"
    "- Never use these words: synthetic, synth, modeled, model, estimated, predicted, panel, panelists, "
    "AI, Claude, door, doors. No em dashes or en dashes; use commas or periods.\n"
    "- Never mention how the numbers were produced. Speak as the owner of the data.\n"
    "- Output compact JSON on one line, no markdown fences, no commentary."
)


def _digest_payload(d: Digest, det: Dict[str, Any], requested: List[str]) -> Dict[str, Any]:
    plat_rows = []
    for ch in det["channels"]:
        for p in ch["platforms"]:
            if p.get("reach") is not None:
                plat_rows.append({"platform": p["name"], "channel": ch["label"], "reach_pct": p["reach"], "index": p["index"], "share_in_channel": p["share"]})
    plat_rows.sort(key=lambda r: -(r["reach_pct"] or 0))
    det_formats = {}
    for ch in det["channels"]:
        for p in ch["platforms"]:
            if p["key"] in requested:
                det_formats[p["key"]] = {f["name"]: f["pct"] for f in p["formats"]}
    return {
        "subject": d.subject,
        "brand_category": d.brand_category,
        "category_family": d.family,
        "audience": det["audience"],
        "age_buckets_pct": {k: round(v * 100, 1) for k, v in d.age.items()},
        "channel_mix_pct": {ch["key"]: ch["share"] for ch in det["channels"]},
        "channel_labels": {ch["key"]: ch["label"] for ch in det["channels"]},
        "platform_rows": plat_rows[:18],
        "current_format_weights": det_formats,
        "requested_platform_keys": requested,
        "schema": {
            "channel_rationale": {ch["key"]: "<one sentence>" for ch in det["channels"]},
            "platform_copy": {k: "<one or two sentences>" for k in requested},
            "format_weights": {k: {"<format name>": "<number>"} for k in requested},
        },
    }


def _call_model(system: str, user: str) -> str:
    """House Claude wrapper at the current model tier (no hard-coded id)."""
    try:
        from claude_client import claude_reason_json  # bg-webapp root
    except Exception:
        try:
            from migration.claude_client import claude_reason_json  # type: ignore
        except Exception:
            return ""
    try:
        return claude_reason_json(system=system, user=user, max_tokens=MODEL_MAX_TOKENS, temperature=0.3, usage_tag="marketing_plan") or ""
    except Exception:
        return ""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def scrub_copy(text: Any) -> str:
    """Drop or clean a model line: em/en dashes become commas, and any line
    carrying banned vocabulary is rejected (returns '')."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = s.replace("\u2014", ",").replace("\u2013", "-").replace("  ", " ")
    low = f" {s.lower()} "
    for b in BANNED_WORDS:
        if b.strip() in ("ai",):
            continue
        if b in low:
            return ""
    if re.search(r"\b(ai|a\.i\.)\b", low) and not re.search(r"\bai search\b", low):
        return ""
    if len(s) > 420:
        s = s[:417].rsplit(" ", 1)[0] + "."
    return s


TIER2_STEMS = ("lean", "skew", "reads as", "tends to", "lands closer", "consistent with", "lead", "hold", "stay")


def has_tier2_language(text: str) -> bool:
    """Directional wording (leans / skews / reads as / tends to ...) rather
    than a flat claim; see analysis-confidence-calibration.mdc."""
    low = (text or "").lower()
    return any(v in low for v in TIER2_STEMS)


def requested_platform_keys(det: Dict[str, Any], n: int = 6) -> List[str]:
    scored = []
    for ch in det["channels"]:
        for p in ch["platforms"]:
            if p["formats"]:
                scored.append((ch["share"] * p["share"], p["key"]))
    scored.sort(reverse=True)
    return [k for _, k in scored[:n]]


def apply_model_output(det: Dict[str, Any], model_out: Optional[Dict[str, Any]], d: Digest, s3_key: str = "") -> Dict[str, Any]:
    """Blend the model's format weights (50/50 with the deterministic layer,
    each format held within 0.6x to 1.5x of its deterministic share) and
    adopt its rationale and copy lines when they pass the scrub. Anything
    that fails keeps the deterministic text."""
    plan = json.loads(json.dumps(det))
    if not isinstance(model_out, dict):
        return plan
    rats = model_out.get("channel_rationale") or {}
    copies = model_out.get("platform_copy") or {}
    fws = model_out.get("format_weights") or {}
    adopted = 0
    for ch in plan["channels"]:
        line = scrub_copy(rats.get(ch["key"])) if isinstance(rats, dict) else ""
        if line and has_tier2_language(line) and 6 <= len(line.split()) <= 40:
            ch["rationale"] = line
            adopted += 1
        for p in ch["platforms"]:
            cp = scrub_copy(copies.get(p["key"])) if isinstance(copies, dict) else ""
            if cp and 6 <= len(cp.split()) <= 80:
                p["copy"] = cp
            mw = fws.get(p["key"]) if isinstance(fws, dict) else None
            if isinstance(mw, dict) and p["formats"]:
                names = [f["name"] for f in p["formats"]]
                clean = {}
                for nm in names:
                    v = None
                    for k, val in mw.items():
                        if _norm(k) == _norm(nm):
                            v = _f(val, -1)
                            break
                    if v is None or v < 0:
                        clean = {}
                        break
                    clean[nm] = v
                if clean and sum(clean.values()) > 0:
                    tot = sum(clean.values())
                    det_by = {f["name"]: f["pct"] for f in p["formats"]}
                    blended = {}
                    for nm in names:
                        mv = clean[nm] / tot * 100.0
                        dv = det_by[nm]
                        b = 0.5 * dv + 0.5 * mv
                        blended[nm] = _clamp(b, 0.6 * dv, 1.5 * dv) if dv > 0 else b
                    shares = messy_split(blended, _salt(d, s3_key, f"formats-model:{p['key']}"))
                    D = derive_drivers(d)
                    fmt_defs = {f["name"]: f for f in FORMATS.get(p["key"], [])}
                    for f in p["formats"]:
                        f["pct"] = shares[f["name"]]
                        f["note"] = format_note(d, fmt_defs.get(f["name"], {}), D, f["pct"])
                    p["formats"].sort(key=lambda f: -f["pct"])
                    adopted += 1
    plan["reasoned"] = adopted > 0
    return plan


def reason_plan(d: Digest, det: Dict[str, Any], s3_key: str = "", model_fn: Optional[Callable[[str, str], str]] = None) -> Dict[str, Any]:
    """Run the single model call and merge it. Falls back to the
    deterministic plan (flagged reasoned=False) on any failure."""
    requested = requested_platform_keys(det)
    payload = _digest_payload(d, det, requested)
    user = "PROFILE DIGEST (JSON):\n" + json.dumps(payload, ensure_ascii=False) + \
        "\n\nReturn the JSON object described in `schema`, filled in for this audience."
    fn = model_fn or _call_model
    try:
        raw = fn(MODEL_SYSTEM, user)
    except Exception:
        raw = ""
    out = _extract_json(raw)
    plan = apply_model_output(det, out, d, s3_key)
    plan["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return plan


def build_plan(df, s3_key: str = "", use_model: bool = True, model_fn: Optional[Callable[[str, str], str]] = None,
               genpop_lookup: Optional[Callable[[str, str], float]] = None) -> Dict[str, Any]:
    """Full plan for one profile frame. Synchronous (model call inline)."""
    d = digest_from_df(df, s3_key, genpop_lookup=genpop_lookup)
    det = build_deterministic_plan(d, s3_key)
    if not use_model:
        return det
    return reason_plan(d, det, s3_key, model_fn=model_fn)


# ---------------------------------------------------------------------------
# Cache (S3 + in-process) and the background model job
# ---------------------------------------------------------------------------

_memo: Dict[str, Dict[str, Any]] = {}
_memo_order: List[str] = []
_memo_lock = threading.Lock()
_inflight: Dict[str, float] = {}
_genpop_cache: Dict[str, Any] = {"loaded_at": 0.0, "etag": None, "map": None}
GENPOP_TTL_S = 3600


def cache_key_for(s3_key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "__", s3_key.replace(".csv", ""))
    return f"{CACHE_PREFIX}{safe}.json"


def _memo_get(k: str) -> Optional[Dict[str, Any]]:
    with _memo_lock:
        return _memo.get(k)


def _memo_put(k: str, v: Dict[str, Any]) -> None:
    with _memo_lock:
        _memo[k] = v
        if k in _memo_order:
            _memo_order.remove(k)
        _memo_order.append(k)
        while len(_memo_order) > 64:
            old = _memo_order.pop(0)
            _memo.pop(old, None)


def _s3_read_json(s3_client, bucket: str, key: str) -> Optional[Dict[str, Any]]:
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        return None


def _s3_write_json(s3_client, bucket: str, key: str, doc: Dict[str, Any]) -> bool:
    try:
        s3_client.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc, ensure_ascii=False).encode("utf-8"),
                             ContentType="application/json")
        return True
    except Exception:
        return False


def load_genpop_lookup(s3_client, bucket: str, genpop_key: str = "Gen_Pop_2026.csv") -> Optional[Callable[[str, str], float]]:
    """(cat, name) -> Gen Pop BP for rows whose own index cell is empty.
    Cached in-process for an hour; any failure returns None (index 100)."""
    now = time.time()
    if _genpop_cache["map"] is not None and now - _genpop_cache["loaded_at"] < GENPOP_TTL_S:
        m = _genpop_cache["map"]
        return lambda cat, name: m.get((cat.upper(), _norm(name)), 0.0)
    try:
        import pandas as pd
        obj = s3_client.get_object(Bucket=bucket, Key=genpop_key)
        df = pd.read_csv(io.BytesIO(obj["Body"].read()))
        m: Dict[Tuple[str, str], float] = {}
        cols = {str(c).strip(): c for c in df.columns}
        for rec in df[[cols["Column"], cols["Value"], cols["Brand Penetration (Row)"]]].itertuples(index=False):
            cat, val, bp = str(rec[0] or "").strip().upper(), _norm(rec[1]), _f(rec[2])
            if cat and val and bp > 0:
                m[(cat, val)] = max(m.get((cat, val), 0.0), bp)
        _genpop_cache.update({"map": m, "loaded_at": now})
        return lambda cat, name: m.get((cat.upper(), _norm(name)), 0.0)
    except Exception:
        return None


def _load_profile_df(s3_client, bucket: str, s3_key: str):
    import pandas as pd
    obj = s3_client.get_object(Bucket=bucket, Key=s3_key)
    return pd.read_csv(io.BytesIO(obj["Body"].read())).fillna("")


def _profile_etag(s3_client, bucket: str, s3_key: str) -> str:
    head = s3_client.head_object(Bucket=bucket, Key=s3_key)
    return str(head.get("ETag") or "").strip('"')


def _valid_cached(doc: Optional[Dict[str, Any]], etag: str) -> bool:
    return bool(doc) and doc.get("etag") == etag and int(doc.get("version") or 0) == PLAN_VERSION and isinstance(doc.get("plan"), dict)


def _run_model_job(s3_client, bucket: str, s3_key: str, etag: str, df, det: Dict[str, Any], model_fn=None, genpop_lookup=None) -> None:
    memo_k = f"{s3_key}|{etag}|{PLAN_VERSION}"
    try:
        d = digest_from_df(df, s3_key, genpop_lookup=genpop_lookup)
        plan = reason_plan(d, det, s3_key, model_fn=model_fn)
        plan["etag"] = etag
        doc = {"etag": etag, "version": PLAN_VERSION, "plan": plan, "written_at": plan["generated_at"]}
        _memo_put(memo_k, doc)
        if s3_client is not None:
            _s3_write_json(s3_client, bucket, cache_key_for(s3_key), doc)
    except Exception:
        pass
    finally:
        with _memo_lock:
            _inflight.pop(memo_k, None)


def get_or_build_plan(s3_key: str, s3_client, bucket: str = "dashboard-inputs", force: bool = False,
                      wait_for_model: bool = False, model_fn: Optional[Callable[[str, str], str]] = None,
                      use_model: bool = True) -> Dict[str, Any]:
    """Return the plan for a profile key.

    Cache hit (S3 or memo, same ETag and PLAN_VERSION): the stored plan,
    never a re-bill. Miss: the deterministic plan is returned at once with
    `pending: True` while one background thread runs the model call and
    writes the reasoned plan to the cache; the client re-reads. With
    `wait_for_model=True` (scripts, tests) the call runs inline.
    """
    etag = _profile_etag(s3_client, bucket, s3_key)
    memo_k = f"{s3_key}|{etag}|{PLAN_VERSION}"
    if not force:
        doc = _memo_get(memo_k)
        if not _valid_cached(doc, etag):
            doc = _s3_read_json(s3_client, bucket, cache_key_for(s3_key))
            if _valid_cached(doc, etag):
                _memo_put(memo_k, doc)
        if _valid_cached(doc, etag):
            plan = dict(doc["plan"])
            plan["pending"] = False
            plan["cache"] = "hit"
            return plan
    df = _load_profile_df(s3_client, bucket, s3_key)
    genpop_lookup = load_genpop_lookup(s3_client, bucket)
    d = digest_from_df(df, s3_key, genpop_lookup=genpop_lookup)
    det = build_deterministic_plan(d, s3_key)
    det["etag"] = etag
    if not use_model:
        doc = {"etag": etag, "version": PLAN_VERSION, "plan": det, "written_at": det["generated_at"]}
        _memo_put(memo_k, doc)
        _s3_write_json(s3_client, bucket, cache_key_for(s3_key), doc)
        det["pending"] = False
        det["cache"] = "miss"
        return det
    if wait_for_model:
        plan = reason_plan(d, det, s3_key, model_fn=model_fn)
        plan["etag"] = etag
        doc = {"etag": etag, "version": PLAN_VERSION, "plan": plan, "written_at": plan["generated_at"]}
        _memo_put(memo_k, doc)
        _s3_write_json(s3_client, bucket, cache_key_for(s3_key), doc)
        plan["pending"] = False
        plan["cache"] = "miss"
        return plan
    with _memo_lock:
        running = memo_k in _inflight and (time.time() - _inflight[memo_k]) < 240
        if not running:
            _inflight[memo_k] = time.time()
            start = True
        else:
            start = False
    if start:
        t = threading.Thread(target=_run_model_job, args=(s3_client, bucket, s3_key, etag, df, det, model_fn, genpop_lookup), daemon=True)
        t.start()
    det["pending"] = True
    det["cache"] = "miss"
    return det
