"""A carrier platform is pinned only where the title is verified (2026-11-11).

Jessie's Georgie & Mandy's First Marriage builds read Peacock at 99%
under STREAMING/PLATFORM. The show is not on Peacock. The approved
brief listed three streaming homes for the show (HBO Max, Paramount+,
Peacock) from what the interpret step believed, the client had named
one (HBO Max on one build, Paramount+ on the other), and the build
pinned all three: the carriage pin guard only acted when carriage
research had resolved for the subject, and for a subject that carries
its platform in the name ("... Viewers - HBO Max") it never resolved,
so every pin in the brief rode through unchecked.

The rule, in one place:

  * On a viewers universe a streaming-platform pin is a claim that
    everyone in the universe is on that platform. It stands only on
    evidence: the platform the client named, a placement the content
    map holds for the title, a placement the live catalog scan saw
    today, or a carrier the carriage research confirmed.
  * A platform the client named defines the pin. With a platform
    named, no other streaming platform pins, verified or not: people
    who watched on HBO Max are not all on Paramount+.
  * A platform the scan marked gone, or could not check, is not
    evidence. 'Could not look' is never 'there'.
  * The seed placements are the universe (2026-11-11). When the build
    is seeded from watch URLs, the pin is the service those URLs sit
    on: one seed service pins, several share the universe, and a
    carrier the research named reads on its own merits. Research can
    add a carrier's level; it never moves the pin.
  * With no evidence at all, nothing in the streaming family pins.
    The row is still scored by the row-by-row reasoning, which is free
    to read it high; it is never set near-total on a belief.

A network pin (BROADCAST/CABLE) is the same claim and gets the same
treatment: CBS read 99% on a universe of people who watched on HBO Max
only.

Pure and fail-safe: `verify_platform_pins` never raises, never touches
a pin outside the streaming and network families, the subject's own row, or a pin a
universe backstop decided (`spec['universe_platform_pins']`: the
content-seed platform, a purchase platform, a customers-of brand, a
compound anchor).

Regression: scripts/test_carrier_pin_verified.py.
"""
from __future__ import annotations

import re

STREAM_PIN_CATS = frozenset({
    "STREAMING/PLATFORM", "STREAMING VIDEO",
    "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD/FAST", "VMVPD",
    "FAST PLATFORM",
})

# A network pin is the same claim as a streaming pin (everyone in the
# universe is on it) and stands on the same evidence (2026-11-11: CBS
# read 99% on a universe of people who watched on HBO Max only).
NETWORK_PIN_CATS = frozenset({"BROADCAST/CABLE", "BROADCAST CABLE"})
CARRIER_PIN_CATS = STREAM_PIN_CATS | NETWORK_PIN_CATS

# categories that name a place to watch, never a title
_PLATFORM_CATS = CARRIER_PIN_CATS | frozenset({
    "STREAMING PLATFORM", "STREAMING SERVICE", "APP/PLATFORM", "PLATFORMS", "MEDIA", "STREAMING MUSIC",
    "MOVIE THEATER", "FAST CHANNEL",
})
_CONTENT_CAT_PREFIXES = ("SERIES", "MOVIE", "PODCAST", "GAMES", "VERTICAL SHORTS")

_VIEWER_TYPES = ("viewers", "viewer", "watchers", "streamers")
_VIEWER_NAME_RX = re.compile(r"\b(?:viewers?|watchers?|streamers?)\b", re.I)

# resolver keys and the names a platform row can carry (the engine's
# own table is passed in when available; this is the floor)
_KEY_NAMES = {
    "amazon": ("amazon", "primevideo", "amazonprimevideo", "amazonprime", "prime"),
    "appletv": ("appletv", "appletvplus"),
    "max": ("max", "hbomax", "hbo"),
    "hbomax": ("max", "hbomax", "hbo"),
    "paramount": ("paramount", "paramountplus"),
    "paramountplus": ("paramount", "paramountplus"),
    "disney": ("disney", "disneyplus"),
    "disneyplus": ("disney", "disneyplus"),
    "mgmplus": ("mgm", "mgmplus"),
    "hallmark": ("hallmark", "hallmarkplus"),
    "peacock": ("peacock", "peacocktv"),
}


def _fold(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def same_platform(a, b) -> bool:
    """Do two platform names (a resolver key, a stored label, a row
    value) name one service? 'max' / 'HBO Max', 'Paramount Plus' /
    'PARAMOUNT+', 'hulu' / 'Disney+/Hulu', 'amazon' / 'Prime Video'."""
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    na = set(_KEY_NAMES.get(fa, ())) | {fa}
    nb = set(_KEY_NAMES.get(fb, ())) | {fb}
    if na & nb:
        return True
    # compound rows ('Disney+/Hulu', 'HBO Max Amazon Channel') and long
    # names contain the service; a short key never matches by substring
    for x in na:
        for y in nb:
            lo, hi = (x, y) if len(x) <= len(y) else (y, x)
            if len(lo) >= 4 and lo in hi:
                return True
    return False


def is_viewers_universe(spec, subject="") -> bool:
    """A universe of people who watched a title: the audience type the
    brief set, a content-map seeded build, a resolved carriage doc, or
    a name that says so."""
    try:
        if not isinstance(spec, dict):
            return False
        if str(spec.get("audience_type") or "").strip().lower() in _VIEWER_TYPES:
            return True
        doc = spec.get("carriage_doc")
        if isinstance(doc, dict) and doc.get("consumption_scoped") \
                and str(doc.get("qualifier") or "viewers").lower() in _VIEWER_TYPES:
            return True
        name = str(subject or spec.get("name") or spec.get("subject") or "")
        seeded = bool(spec.get("content_seed_urls")) or str(spec.get("seed_source") or "").startswith("content_map")
        if seeded and (_VIEWER_NAME_RX.search(name) or str(spec.get("consumer_verb") or "").lower() in _VIEWER_TYPES
                       or str(spec.get("ip_scope") or "").lower() == "consumers"):
            return True
        return bool(_VIEWER_NAME_RX.search(name))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The seed service is the universe (2026-11-11). A viewers universe is the
# people seen on the watch URLs in its BRAND INPUT, so the service(s) those
# URLs sit on are the ones everybody in the file used. Mistletoe Murders
# Viewers was seeded from twelve Hallmark Plus placements and read Hallmark
# Plus at 57% with Netflix near 100 (a carrier the research had named);
# WICKED TUNA Viewers carried Amazon watch URLs and read Amazon at 56% with
# Disney+/Hulu at 99.57. A service the title is also carried on reads on its
# own merits; it is never the pin and never above the seed service.
# ---------------------------------------------------------------------------

# the name a service's row carries in the streaming column (naming only)
_SEED_ROW_NAMES = {
    "amazon": "Amazon Prime Video", "max": "HBO Max", "hbomax": "HBO Max", "paramount": "Paramount+",
    "paramountplus": "Paramount+", "disney": "Disney+", "disneyplus": "Disney+", "appletv": "Apple TV+",
    "appletvplus": "Apple TV+", "hallmark": "Hallmark Plus", "hallmarkplus": "Hallmark Plus", "peacock": "Peacock",
    "netflix": "Netflix", "hulu": "Hulu", "starz": "Starz", "mgmplus": "MGM+", "britbox": "BritBox",
    "pluto": "Pluto TV", "plutotv": "Pluto TV", "tubi": "Tubi", "discoveryplus": "Discovery+",
}
_SEED_GROUPS = (
    ("amazon", "primevideo", "amazonprimevideo", "amazonprime", "amazonvideo"),
    ("max", "hbomax"), ("paramount", "paramountplus"), ("disney", "disneyplus"), ("appletv", "appletvplus"),
    ("hallmarkplus",), ("peacock", "peacocktv"), ("mgmplus", "mgm"), ("pluto", "plutotv"), ("tubi", "tubitv"),
    ("rokuchannel", "therokuchannel"), ("discoveryplus",), ("amcplus",), ("youtube",), ("netflix",), ("hulu",),
    ("starz",), ("britbox",), ("spotify",), ("applepodcasts",), ("audible",), ("freeform",),
    # the row these free services carry in a profile is named with a word more or less than the service
    # (2026-11-12: a universe seeded from Plex placements has its row as PLEX TV, one from Xumo Play as XUMO)
    ("plex", "plextv"), ("xumo", "xumoplay"),
)
_SEED_KEY = {"hallmark": "hallmarkplus"}     # a resolver key that is shorter than the service's name


def _seed_key(name, resolver=True) -> str:
    """One key per service for the seed rule: exact names only, never a
    substring ('YouTube' is not 'YouTube TV' or 'YouTube Kids'). A
    resolver's short key ('hallmark') is read as its service only on the
    seed side; a profile row named HALLMARK is the card maker."""
    f = _fold(str(name or "").replace("+", " plus "))
    if resolver:
        f = _SEED_KEY.get(f, f)
    for grp in _SEED_GROUPS:
        if f in grp:
            return grp[0]
    return f


def seed_matches_row(seed, row_value) -> bool:
    """Does a profile row name the seed service? Exact or by a compound
    row's component ('Hulu' is in 'Disney+/Hulu'); never by substring."""
    k = _seed_key(seed)
    if not k:
        return False
    rv = str(row_value or "").strip()
    if not rv:
        return False
    if _seed_key(rv, resolver=False) == k:
        return True
    return any(_seed_key(part, resolver=False) == k for part in re.split(r"\s*(?:/|&|,|\|)\s*", rv) if part.strip())


def seed_row_name(seed) -> str:
    """The streaming-column row a seed service pins ('' when the service
    lives elsewhere and the engine's own resolution places it)."""
    return _SEED_ROW_NAMES.get(_fold(str(seed or "").replace("+", " plus ")), "") \
        or _SEED_ROW_NAMES.get(_seed_key(seed), "")


# watch-URL shapes by service, for a file's own BRAND INPUT (the content
# map's stored URL wins when an index is handed in; a bare id it does not
# hold is left unread rather than guessed)
_URL_SHAPES = (
    (re.compile(r"^(?:amzn1\.dv\.gti\.|detail/[0-9A-Z]{10,}|(?:https?://)?(?:www\.)?(?:amazon\.com/|primevideo\.com/))", re.I), "Amazon"),
    (re.compile(r"^(?:item/[A-Z0-9]{8,}$|(?:https?://)?(?:www\.)?hallmarkplus\.com/)", re.I), "Hallmark Plus"),
    (re.compile(r"^(?:https?://)?(?:www\.)?netflix\.com/", re.I), "Netflix"),
    (re.compile(r"^(?:https?://)?(?:www\.)?hulu\.com/", re.I), "Hulu"),
    (re.compile(r"^(?:https?://)?(?:www\.|play\.)?(?:max|hbomax)\.com/", re.I), "HBO Max"),
    (re.compile(r"^(?:ALVE01[0-9A-Z]{18,}$|(?:https?://)?(?:www\.)?paramountplus\.com/)", re.I), "Paramount+"),
    (re.compile(r"^(?:https?://)?(?:www\.)?peacocktv\.com/", re.I), "Peacock"),
    (re.compile(r"^(?:https?://)?(?:www\.)?disneyplus\.com/", re.I), "Disney+"),
    (re.compile(r"^(?:https?://)?tv\.apple\.com/", re.I), "Apple TV+"),
    (re.compile(r"^(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/|youtu\.be/)", re.I), "YouTube"),
    (re.compile(r"^(?:https?://)?therokuchannel\.roku\.com/", re.I), "The Roku Channel"),
    (re.compile(r"^(?:https?://)?watch\.plex\.tv/", re.I), "Plex"),
    (re.compile(r"^(?:https?://)?(?:www\.)?tubitv\.com/", re.I), "Tubi"),
    (re.compile(r"^(?:https?://)?(?:www\.)?pluto\.tv/", re.I), "Pluto TV"),
    (re.compile(r"^(?:https?://)?(?:www\.)?freeform\.com/", re.I), "Freeform"),
    (re.compile(r"^(?:https?://)?(?:www\.)?discoveryplus\.com/", re.I), "Discovery+"),
    (re.compile(r"^(?:https?://)?(?:www\.)?starz\.com/", re.I), "Starz"),
    (re.compile(r"^(?:https?://)?open\.spotify\.com/", re.I), "Spotify"),
    (re.compile(r"^(?:https?://)?podcasts\.apple\.com/", re.I), "Apple Podcasts"),
    (re.compile(r"^(?:https?://)?(?:www\.)?audible\.com/", re.I), "Audible"),
)


def service_of_url(token, index=None) -> str:
    """The service one seed URL sits on: the content map's own record of
    it when an index ({url: [platform]}) is handed in and holds it under
    one service, else its shape; '' when neither says."""
    t = str(token or "").strip()
    if not t:
        return ""
    if isinstance(index, dict):
        got = index.get(t.lower())
        if got:
            keys = {_seed_key(g) for g in got if str(g).strip()}
            if len(keys) == 1:
                return str(sorted(got)[0])
            return ""                       # the map holds it under two services: unread
    for rx, name in _URL_SHAPES:
        if rx.search(t):
            return name
    return ""


def seed_services_of_brand_input(value, index=None) -> dict:
    """{service key: {'label', 'n'}} for a BRAND INPUT cell made of
    watch URLs, plus '_unread' (tokens no service claims) and '_total'.
    A cell of name variants or scrape terms reads as no services."""
    out = {"_unread": 0, "_total": 0}
    for tok in [x.strip() for x in str(value or "").split(",") if x.strip()]:
        out["_total"] += 1
        svc = service_of_url(tok, index)
        if not svc:
            out["_unread"] += 1
            continue
        k = _seed_key(svc)
        rec = out.setdefault(k, {"label": seed_row_name(svc) or svc, "n": 0})
        rec["n"] += 1
    return out


def seed_services(spec) -> list:
    """The services the build's seed placements actually sit on, as the
    content map labels them, most placements first: the seed step's own
    list minus a service the live scan saw the title leave. [] when the
    build is not seeded from watch URLs."""
    try:
        if not isinstance(spec, dict) or not (spec.get("content_seed_urls") or spec.get("content_seed_platforms")):
            return []
        placements = spec.get("content_seed_placements")
        gone = [str(k) for k in ((placements or {}).get("gone") or [])] if isinstance(placements, dict) else []
        out = []
        for p in (spec.get("content_seed_platforms") or []):
            p = str(p).strip()
            if p and not any(_seed_key(p) == _seed_key(g) for g in gone) \
                    and not any(_seed_key(p) == _seed_key(o) for o in out):
                out.append(p)
        if not out and spec.get("content_seed_urls"):
            got = seed_services_of_brand_input(", ".join(str(u) for u in spec["content_seed_urls"]))
            out = [v["label"] for k, v in sorted(got.items(), key=lambda kv: -(kv[1]["n"] if isinstance(kv[1], dict) else 0))
                   if isinstance(v, dict)]
        return out
    except Exception:
        return []


def stamp_seed_carriers(spec) -> list:
    """Put the seed services on the carriage doc the writer's carrier
    constraint reads (creating a minimal doc when research did not run
    or failed), so the service that reads near total is the one the
    universe was seeded from. Returns the list stamped ([] when the
    build has no seed services). Never raises."""
    try:
        seeds = seed_services(spec)
        if not seeds:
            return []
        doc = spec.get("carriage_doc")
        if not isinstance(doc, dict):
            doc = {"consumption_scoped": True, "qualifier": "viewers", "carriers": [], "research_failed": True,
                   "title": str(spec.get("content_show") or ""), "subject": str(spec.get("name") or "")}
            spec["carriage_doc"] = doc
        doc["seed_carriers"] = [{"platform": seed_row_name(s) or s, "kind": "svod"} for s in seeds]
        spec["seed_services"] = list(seeds)
        return doc["seed_carriers"]
    except Exception:
        return []


def evidence(spec) -> dict:
    """What says the title is on a platform, by source:
    {'named': [...], 'seeds': [...], 'scan': [...], 'research': [...],
     'not_evidence': [...]} (gone and could-not-check platforms)."""
    out = {"named": [], "seeds": [], "scan": [], "research": [], "not_evidence": []}
    try:
        placements = spec.get("content_seed_placements")
        placements = placements if isinstance(placements, dict) else {}
        gone = [str(k) for k in (placements.get("gone") or []) if str(k).strip()]
        unver = [str(k) for k in (placements.get("unverified") or []) if str(k).strip()]
        held = [str(k) for k in (spec.get("content_seed_held") or []) if str(k).strip()]
        out["named"] = [str(p) for p in (spec.get("content_seed_platforms_named") or []) if str(p).strip()]
        # a stored placement on a platform the scan could not check is
        # kept as history and carried for a platform the client named
        # (content_seed_held); for a pin it is evidence only there
        not_ev = gone + [k for k in unver if not any(same_platform(k, h) for h in held)]
        out["not_evidence"] = not_ev
        out["seeds"] = [str(p) for p in (spec.get("content_seed_platforms") or []) if str(p).strip()
                        and not any(same_platform(p, k) for k in not_ev)]
        out["scan"] = [str(k) for k in (placements.get("present") or []) if str(k).strip()]
        doc = spec.get("carriage_doc")
        if isinstance(doc, dict) and doc.get("consumption_scoped") and not doc.get("research_failed") \
                and doc.get("confident", True):
            out["research"] = [str(c.get("platform") or "") for c in (doc.get("carriers") or [])
                               if isinstance(c, dict) and str(c.get("platform") or "").strip()
                               and not any(same_platform(c.get("platform"), k) for k in gone)]
    except Exception:
        pass
    return out


def named_in_subject(subject, platform) -> bool:
    """The universe's own name carries the platform ('... Viewers - HBO
    Max', 'Gilmore Girls Viewers on Hulu', 'Peacock Olympics Viewers')."""
    fs, fp = _fold(subject), _fold(platform)
    if not fs or not fp:
        return False
    names = set(_KEY_NAMES.get(fp, ())) | {fp}
    return any(len(n) >= 4 and n in fs for n in names)


def verify_platform_pins(spec, subject, subject_rows, *, title="", log=print):
    """(kept, dropped) for the brief's pins on a viewers universe.
    `dropped` is a list of (category, platform, reason). Anything that is
    not a streaming or network pin is kept untouched. Never raises;
    on any error every pin is returned kept and nothing is dropped."""
    try:
        rows = list(subject_rows or [])
        if not rows or not isinstance(spec, dict) or not is_viewers_universe(spec, subject):
            return rows, []
        ev = evidence(spec)
        subj_keys = {_fold(str(subject or "").split(" - ")[0]), _fold(subject), _fold(title),
                     _fold(spec.get("content_show")), _fold((spec.get("carriage_doc") or {}).get("title")
                                                              if isinstance(spec.get("carriage_doc"), dict) else "")}
        subj_keys.discard("")
        owner = {(str(p[0]).strip().upper(), _fold(p[1])) for p in (spec.get("universe_platform_pins") or [])
                 if isinstance(p, (list, tuple)) and len(p) >= 2}
        named = list(ev["named"])
        seeds = seed_services(spec)
        # the universe's own name can carry the platform; any streaming
        # pin it names puts the build in named mode for every other pin
        in_name_any = False
        for pin in rows:
            try:
                if str(pin[0]).strip().upper() in CARRIER_PIN_CATS and _fold(pin[1]) not in subj_keys \
                        and named_in_subject(subject, pin[1]):
                    in_name_any = True
                    break
            except Exception:
                continue
        kept, dropped = [], []
        for pin in rows:
            try:
                cat = str(pin[0]).strip().upper()
                name = str(pin[1]).strip()
            except Exception:
                kept.append(pin)
                continue
            if cat not in CARRIER_PIN_CATS or not name:
                kept.append(pin)
                continue
            if _fold(name) in subj_keys or (cat, _fold(name)) in owner:
                kept.append(pin)
                continue
            in_name = named_in_subject(subject, name)
            if seeds:
                # the seed placements are the universe: a pin stands only on a service they sit on
                if any(seed_matches_row(s, name) for s in seeds):
                    kept.append(pin)
                else:
                    dropped.append((pin[0], name, f"the universe is the people seen on {', '.join(seeds)}; "
                                                  f"another carrier reads on its own merits"))
                continue
            if named or in_name_any:
                # a platform the client named defines the pin
                if in_name or any(same_platform(n, name) for n in named):
                    kept.append(pin)
                else:
                    who = ", ".join(named) or "the platform in the name"
                    dropped.append((pin[0], name, f"the client named {who}; no other platform pins"))
                continue
            if any(same_platform(k, name) for k in ev["not_evidence"]):
                dropped.append((pin[0], name, "the live scan could not place the title there"))
                continue
            if any(same_platform(p, name) for p in ev["seeds"] + ev["scan"] + ev["research"]):
                kept.append(pin)
                continue
            dropped.append((pin[0], name, "no placement on record and none seen on the platform"))
        if dropped and callable(log):
            try:
                log(f"[carrier-pin] {subject!r}: {len(dropped)} platform pin(s) not verified, left to the "
                    f"row-by-row read: " + "; ".join(f"{c} / {n} ({why})" for c, n, why in dropped)
                    + f" | evidence named={ev['named']} seeds={ev['seeds']} scan={ev['scan']} research={ev['research']}")
            except Exception:
                pass
        return kept, dropped
    except Exception as e:   # never block a build on the guard itself
        try:
            if callable(log):
                log(f"[carrier-pin] guard failed (pins kept): {e}")
        except Exception:
            pass
        return list(subject_rows or []), []


def _is_service_name(name) -> bool:
    f = _fold(name)
    return bool(f) and any(f in names or f == k for k, names in _KEY_NAMES.items())


_TITLE_KINDS = frozenset({"series", "show", "film", "movie", "special", "podcast", "franchise", "franchise_film",
                          "standalone_film", "limited_series", "miniseries", "documentary", "game"})


def _is_a_title(spec) -> bool:
    """The universe's subject is a show, a film or a podcast, on the
    brief's own evidence: the content map holds it as a title, or the
    scope / kind the reader chose says so. A channel or a network whose
    audience is called viewers ('ESPN Viewers') is not a title and
    keeps the category the brief gave it."""
    if str(spec.get("seed_source") or "").strip() == "content_map":
        return True
    vs = spec.get("viewer_scope") if isinstance(spec.get("viewer_scope"), dict) else {}
    return (str(vs.get("kind") or "").strip().lower() in _TITLE_KINDS
            or str(spec.get("medium") or "").strip().lower() in _TITLE_KINDS)


def normalize_brief(spec, subject="", log=print):
    """The brief, before it is a build (the spec normalizer and the
    worker's build entry both call this). Mutates and returns ``spec``;
    never raises.

    1. A title is not a streaming service (2026-11-11: Georgie & Mandy's
       First Marriage sat in STREAMING/PLATFORM at 100 on its own HBO Max
       build). When the universe is the audience of a title and the
       brief filed it under a place-to-watch category, the category is
       the title's own kind (SERIES, MOVIE, PODCAST), the title is
       anchored there, and it leaves every place-to-watch column.
    2. With a service named by the client, only that service pins: every
       other streaming or network pin is left to the row-by-row read. A
       build that named none is checked later, when the placements are
       in hand (verify_platform_pins in the engine)."""
    try:
        if not isinstance(spec, dict):
            return spec
        title = str(spec.get("content_show") or "").strip()
        if not title:
            vs = spec.get("viewer_scope")
            title = str(vs.get("title") or "").strip() if isinstance(vs, dict) else ""
        rows = [list(r) for r in (spec.get("subject_rows") or []) if isinstance(r, (list, tuple)) and len(r) >= 2]
        if title and is_viewers_universe(spec, subject) and not _is_service_name(title) and _is_a_title(spec):
            bc = str(spec.get("brand_category") or "").strip().upper()
            anchored = [str(r[0]).strip().upper() for r in rows if _fold(r[1]) == _fold(title)
                        and str(r[0]).strip().upper().startswith(_CONTENT_CAT_PREFIXES)]
            if bc in _PLATFORM_CATS or (not anchored and bc.startswith(_CONTENT_CAT_PREFIXES)):
                kind = str(spec.get("medium") or "").strip().lower()
                vs = spec.get("viewer_scope") if isinstance(spec.get("viewer_scope"), dict) else {}
                kind = kind or str(vs.get("kind") or "").strip().lower()
                want = bc if bc.startswith(_CONTENT_CAT_PREFIXES) else (
                    anchored[0] if anchored else
                    "MOVIE" if kind in ("film", "movie", "franchise_film", "standalone_film") else
                    "PODCAST" if kind == "podcast" else "SERIES")
                if bc in _PLATFORM_CATS:
                    spec["brand_category"] = want
                    if callable(log):
                        log(f"[brief] {subject!r}: {title!r} is a title, filed under {want} (the brief said {bc})")
                if not anchored:
                    rows.insert(0, [want, title])
            before = len(rows)
            rows = [r for r in rows if not (str(r[0]).strip().upper() in _PLATFORM_CATS and _fold(r[1]) == _fold(title))]
            if len(rows) != before and callable(log):
                log(f"[brief] {subject!r}: {title!r} removed from the place-to-watch columns")
            spec["subject_rows"] = rows
        seeds = seed_services(spec) if is_viewers_universe(spec, subject) else []
        if rows and ([p for p in (spec.get("content_seed_platforms_named") or []) if str(p).strip()] or seeds):
            kept, dropped = verify_platform_pins(spec, subject, rows, title=title, log=log)
            if dropped:
                rows = [list(r) for r in kept]
                spec["subject_rows"] = rows
                spec["unverified_platform_pins"] = [[c, n] for c, n, _why in dropped]
        # 3. The pin is the seed service (when the placements are already in
        #    hand): one seed service pins in the streaming column; several
        #    share the universe and none pins alone.
        if len(seeds) == 1 and seed_row_name(seeds[0]) \
                and not any(str(r[0]).strip().upper() in STREAM_PIN_CATS and seed_matches_row(seeds[0], r[1]) for r in rows):
            rows = list(rows) + [["STREAMING/PLATFORM", seed_row_name(seeds[0])]]
            spec["subject_rows"] = rows
            if callable(log):
                log(f"[brief] {subject!r}: seeded from {seeds[0]} placements; {seed_row_name(seeds[0])} is the pin")
    except Exception as e:
        try:
            if callable(log):
                log(f"[brief] normalization skipped (non-fatal): {e}")
        except Exception:
            pass
    return spec


__all__ = ["normalize_brief", "NETWORK_PIN_CATS", "CARRIER_PIN_CATS", "seed_services", "seed_matches_row", "seed_row_name",
           "service_of_url", "seed_services_of_brand_input", "stamp_seed_carriers", "verify_platform_pins", "is_viewers_universe", "evidence", "same_platform", "named_in_subject",
           "STREAM_PIN_CATS"]
