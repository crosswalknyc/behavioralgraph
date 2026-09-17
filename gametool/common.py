#!/usr/bin/env python3
"""
common.py — shared helpers for GameTool.

GameTool is StreamScout's sibling for video games: give it a game title and it
returns the "someone bought / played this" URL on every store it can reach —
digital storefronts (Steam, Epic, GOG, Nintendo, PlayStation, Xbox, Apple App
Store, Google Play, Battle.net, Amazon Luna), key marketplaces (Eneba, Loaded),
and big-box retailers (Amazon, Best Buy, GameStop, Walmart, Target).

Every store resolver returns a list of Hit dicts:
    {title, url, store_id, format, production}
and the runner writes one unified CSV:
    SHOW · URL · PRODUCTION · PLATFORM · FORMAT · STORE_ID
"""
import difflib
import gzip
import json
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ── HTTP ──────────────────────────────────────────────────────────────────────
def http_get(url, headers=None, timeout=20):
    """GET a URL and return decoded text ('' on failure)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9",
                                               **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def http_json(url, headers=None, timeout=20, data=None, method=None):
    """GET/POST JSON; returns parsed object or None."""
    hdr = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdr.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, headers=hdr, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def q(s):
    return urllib.parse.quote(s or "")


def qplus(s):
    return urllib.parse.quote_plus(s or "")


# ── fuzzy title matching ──────────────────────────────────────────────────────
_TRAIL = re.compile(
    r"\b(the\s+game|game|edition|deluxe|ultimate|standard|complete|goty|"
    r"remastered|definitive|collection|bundle)\b", re.I)


def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def norm(s):
    """Loose title for comparison — drop trademark noise + edition words."""
    s = re.sub(r"[™®©]", " ", s or "")
    s = _TRAIL.sub(" ", s)
    return " ".join(tokens(s))


def similarity(a, b):
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    jacc = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
    return 0.55 * seq + 0.45 * jacc


# companion / spin-off words that mean "this isn't the game" (guides, OSTs,
# modding kits, benchmarks, …)
NOISE = {"soundtrack", "ost", "guide", "guides", "tracker", "database", "wiki",
         "companion", "editor", "fan", "fanmade", "made", "tips", "walkthrough",
         "wallpaper", "wallpapers", "cheats", "unofficial", "quiz", "mod",
         "mods", "calculator", "planner", "assistant", "helper", "art",
         "artbook", "demo", "manual", "cookbook", "trivia", "clicker",
         "redkit", "toolkit", "modkit", "devkit", "sdk", "benchmark",
         "map", "maps", "tool", "tools", "interactive", "radio", "board",
         "quizzes", "emoji", "keyboard", "theme", "themes", "ringtones"}


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13}
_ARABIC = {v: k for k, v in _ROMAN.items()}


def _seq_sets(toks):
    """Every numeral token in a title, each as its arabic+roman equivalents:
    "Final Fantasy VII Remake" -> [{'vii','7'}];  "Left 4 Dead 2" ->
    [{'4','iv'}, {'2','ii'}].  A candidate must carry each to match."""
    sets = []
    for tk in toks:
        if tk.isdigit() and int(tk) in _ARABIC:
            sets.append({tk, _ARABIC[int(tk)]})
        elif tk in _ROMAN:
            sets.append({tk, str(_ROMAN[tk])})
    return sets


def best_matches(title, cands, key=lambda c: c, limit=6):
    """Keep only candidates whose title genuinely matches the query — no forced
    top-1 fallback, so a game that isn't on a store yields nothing (not junk)."""
    qtok = set(tokens(title))
    nq = norm(title)
    qseq = _seq_sets(tokens(title))        # e.g. "Baldur's Gate 3" -> [{'3','iii'}]
    scored = []
    for c in cands:
        t = key(c)
        ct = set(tokens(t))
        nt = norm(t)
        exact = bool(nq) and nq == nt
        if not exact and (ct - qtok) & NOISE:      # a guide/soundtrack/clone tool
            continue
        # sequel guard: "Baldur's Gate 3" must not match "Baldur's Gate" (nor D3
        # for a D4 query, nor FF III for an FF VII query). Every numeral named in
        # the query must appear (in arabic or roman form) in the candidate.
        if not exact and qseq and any(not (s & ct) for s in qseq):
            continue
        prefix = bool(nq) and nt.startswith(nq)
        # For SHORT queries (1-2 words) demand an exact or prefix match, so
        # "Hades" won't drag in "Zeus vs Hades" and "Elden Ring" won't pull
        # "MapGenie Elden Ring". Longer queries keep fuzzy reorder tolerance.
        if len(qtok) <= 2 and not (exact or prefix):
            continue
        sc = 1.0 if exact else similarity(title, t)
        # a title that STARTS with the full query is the canonical game
        # ("The Witcher 3" -> "The Witcher 3: Wild Hunt"); rank it above spin-offs
        if not exact and prefix:
            sc = min(1.0, sc + 0.18)
        contains_all = bool(qtok) and qtok.issubset(ct)   # every query word present
        # query is contained in the candidate = candidate is the more specific
        # product ("The Witcher 3" ⊂ "The Witcher 3: Wild Hunt"). The reverse
        # (candidate ⊂ query) would wrongly admit "Baldur's Gate" for "…Gate 3".
        substr = bool(nq) and nq in nt
        if sc >= 0.72 or (contains_all and sc >= 0.5) or (substr and sc >= 0.55):
            scored.append((sc, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen, out = set(), []
    for _, c in scored:
        u = key(c) if isinstance(c, str) else c.get("url", id(c))
        if u in seen:
            continue
        seen.add(u)
        out.append(c)
    return out[:limit]


def hit(title, url, store_id="", fmt="", production=""):
    return {"title": (title or "").strip(), "url": url,
            "store_id": str(store_id or ""), "format": fmt,
            "production": production or ""}
