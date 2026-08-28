"""Viewer content-scope resolution (seasons / franchise films).

2026-08-27 (Jenna, verbatim): "for the viewer pathway of profiles if
someone selects viewers it then needs to research if it's a show,
movie, etc. then if it is a show it should ask all seasons, most
recent season or specific season? if they say specific season then
list the seasons and let them pick. if it is a movie from a fanchise
you would essentially do the same thing then you'd go scrape those
urls for those episodes, etc for brand input and would then inject
them into the content_mapping table table with the right output as it
currently is in that table"

This module sits ON TOP of migration/viewer_carriage.py (which owns
consumption-scoped detection + carriage research). It owns:

  * structure research - what IS the title: series (with the season
    list), franchise film (with the film list), standalone film,
    special. One Claude web-search call, S3-cached per title.
  * scope parsing    - "season 5" / "most recent season" / "all
    seasons" / a named franchise film, from the user's own words.
  * scope resolution - loose requests ("latest") resolve to the
    concrete season/film; labels like "Season 5 (2026)".
  * naming           - a scoped universe carries the scope in its
    deliverable name ("Yellowstone Season 5 Viewers"), consistent
    with how era-scoped universes carry the era.
  * chip payload     - the dashboard clarify step's data shape
    (series: all / most recent / specific -> season list; franchise:
    whole / most recent / specific -> film list).
  * URL research     - per-carrier content URLs for the CHOSEN scope
    (episode/season paths where verifiable). These extend BRAND
    INPUT via spec['viewer_scope_urls'] and are inserted into
    ClickHouse reference.content_mapping formatted exactly like the
    existing rows.
  * content_mapping  - insert-only, idempotent (case/punctuation
    insensitive dedupe on the natural key); when ClickHouse is
    unreachable the rows queue to S3 for retry and the build
    continues.
  * worker hook      - ensure_scope_resolved(spec) called from
    _run_new_build right after carriage resolution. Cuts inherit
    from their parent and never run this.

Fail-open everywhere: research failure, ClickHouse outage, or any
exception logs and never blocks a build. Partner surfaces never ask:
a scope named in the prompt binds; otherwise the build covers all
seasons / the whole franchise and says so in the response prose.

Twin-sync note: this module lives in BOTH the parent repo
(`migration/viewer_content_scope.py`) and
`bg-webapp/migration/viewer_content_scope.py`. Keep the two byte-equal
(scripts/test_module_twin_sync.py enforces it).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

BUCKET = "dashboard-inputs"
STRUCT_PREFIX = "system/viewer_content_scope/"
PENDING_PREFIX = "system/content_mapping_pending/"
STRUCT_TTL_DAYS = 30
URLS_TTL_DAYS = 30

# Cap on how many scoped URLs ride into BRAND INPUT. Every verified
# row still lands in content_mapping; the Value just should not become
# a 60-episode wall of UUIDs.
BRAND_INPUT_URL_CAP = 24

CONTENT_MAPPING_TABLE = "reference.content_mapping"
CONTENT_MAPPING_COLUMNS = (
    "SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON",
    "CATEGORY", "SUB_CATEGORY",
)


def _clean(s):
    return " ".join(str(s or "").split()).strip()


def norm_token(s):
    """Case + punctuation insensitive normalization (matches
    viewer_carriage.norm_token)."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _strip_apostrophes(s):
    return re.sub(r"[\u2018\u2019\u02bc'`]", "", str(s or ""))


def _s3(s3_client=None):
    if s3_client is not None:
        return s3_client
    import boto3
    return boto3.client("s3", region_name="us-east-2")


# ---------------------------------------------------------------------------
# Content structure: what IS this title (series / film / franchise)
# ---------------------------------------------------------------------------

VALID_KINDS = ("series", "film", "franchise_film", "special", "other")


def structure_cache_key(title):
    n = norm_token(title)
    return f"{STRUCT_PREFIX}{n}.json" if n else ""


def normalize_structure_doc(data, title_hint=""):
    """Coerce a raw research response into the canonical structure doc.
    Returns None when unusable."""
    if not isinstance(data, dict):
        return None
    kind = _clean(data.get("kind")).lower().replace(" ", "_")
    if kind in ("show", "tv_series", "tv_show", "television_series"):
        kind = "series"
    if kind in ("movie", "standalone_film", "feature_film"):
        kind = "film"
    if kind in ("franchise", "film_franchise", "franchise_movie",
                "movie_franchise"):
        kind = "franchise_film"
    if kind not in VALID_KINDS:
        return None
    seasons = []
    seen_nums = set()
    for s in (data.get("seasons") or []):
        try:
            if isinstance(s, dict):
                num = int(s.get("number"))
                year = _clean(s.get("year"))[:9]
            else:
                num = int(s)
                year = ""
        except (TypeError, ValueError):
            continue
        if num <= 0 or num > 99 or num in seen_nums:
            continue
        seen_nums.add(num)
        seasons.append({"number": num, "year": year})
    seasons.sort(key=lambda s: s["number"])
    films = []
    seen_films = set()
    for f in (data.get("films") or []):
        if isinstance(f, dict):
            ft = _clean(f.get("title"))
            fy = _clean(f.get("year"))[:9]
        else:
            ft = _clean(f)
            fy = ""
        k = norm_token(ft)
        if not ft or not k or k in seen_films:
            continue
        seen_films.add(k)
        films.append({"title": ft, "year": fy})
    if kind == "series" and not seasons:
        # A series with an unresearchable season list cannot drive a
        # season pick; treat as unusable so callers skip the clarify.
        return None
    if kind == "franchise_film" and len(films) < 2:
        kind = "film"
        films = films[:1]
    title = _clean(data.get("title")) or _clean(title_hint)
    audience = str(data.get("audience") or "").strip().lower()
    if audience not in ("preschool", "kids", "family", "teen", "general"):
        audience = ""
    return {
        "content_structure": True,
        "title": title,
        "kind": kind,
        "audience": audience,
        "franchise": _clean(data.get("franchise"))[:120],
        "production": _clean(data.get("production"))[:80],
        "seasons": seasons[:60],
        "films": films[:40],
        "confident": bool(data.get("confident", True)),
        "research_failed": False,
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def failed_structure_doc(title, reason):
    return {
        "content_structure": True,
        "title": _clean(title),
        "kind": "other",
        "audience": "",
        "franchise": "",
        "production": "",
        "seasons": [],
        "films": [],
        "confident": False,
        "research_failed": True,
        "failure_reason": _clean(reason)[:300],
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def load_cached_structure(title, s3_client=None,
                          max_age_days=STRUCT_TTL_DAYS):
    key = structure_cache_key(title)
    if not key:
        return None
    try:
        body = _s3(s3_client).get_object(Bucket=BUCKET, Key=key)["Body"].read()
        doc = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or not doc.get("content_structure"):
        return None
    age_limit = 1 if doc.get("research_failed") else max_age_days
    try:
        ts = datetime.fromisoformat(str(doc.get("researched_at")))
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        if age_days > age_limit:
            return None
    except Exception:
        return None
    return doc


def save_structure_cache(title, doc, s3_client=None):
    key = structure_cache_key(title)
    if not key or not isinstance(doc, dict):
        return False
    try:
        _s3(s3_client).put_object(
            Bucket=BUCKET, Key=key,
            Body=json.dumps(doc, indent=1).encode("utf-8"),
            ContentType="application/json")
        return True
    except Exception as e:
        print(f"[content-scope] structure cache write failed for "
              f"{title!r}: {e}")
        return False


def research_enabled():
    """Live research default-ON; VIEWER_SCOPE_RESEARCH=0 disables
    (tests, cost-sensitive backfills). VIEWER_CARRIAGE_RESEARCH=0 also
    disables so one switch turns off the whole viewers research
    family."""
    if os.environ.get("VIEWER_CARRIAGE_RESEARCH", "1") == "0":
        return False
    return os.environ.get("VIEWER_SCOPE_RESEARCH", "1") != "0"


def research_content_structure(title, run_id=""):
    """One web-research call: is this a series (list the seasons with
    years), a franchise film (list the films), a standalone film, or a
    special? Returns a canonical structure doc (possibly
    research_failed=True). Never raises."""
    hint = _clean(title)
    if not hint:
        return failed_structure_doc(title, "empty title")
    try:
        from migration.claude_client import claude_messages
    except ImportError:
        try:
            from claude_client import claude_messages  # twin layout
        except ImportError:
            return failed_structure_doc(hint, "claude client unavailable")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system = (
        "You are classifying a TV/film title for a US streaming "
        "audience build. Use the web_search tool and cross-check "
        "sources.\n"
        "Decide what the title IS:\n"
        "  - series: an episodic TV/streaming show. Enumerate EVERY "
        "season released to date with the year each premiered.\n"
        "  - franchise_film: a film that belongs to a multi-film "
        "franchise (or the franchise itself was named). Enumerate the "
        "franchise's theatrically/streaming released films in order "
        "with years. Main-line films plus widely-recognized spinoffs; "
        "skip shorts and specials.\n"
        "  - film: a standalone film (no multi-film franchise).\n"
        "  - special: a one-off special / live event.\n"
        "  - other: none of the above.\n"
        "Also name the production studio if clearly known (e.g. "
        "'Paramount TV Studios', 'WBD', 'Universal Television').\n"
        "Also classify the title's PRIMARY INTENDED AUDIENCE:\n"
        "  - preschool: made for ages ~2-6 (Paw Patrol, Cocomelon, "
        "Bluey, Danny Go class)\n"
        "  - kids: made for ages ~6-12\n"
        "  - family: all-ages co-viewing (Pixar class)\n"
        "  - teen: made primarily for teens\n"
        "  - general: everything else (adult / general audience)\n"
        "Respond with ONE JSON object and nothing else:\n"
        '{"title": "<canonical title>",\n'
        ' "kind": "series|franchise_film|film|special|other",\n'
        ' "audience": "preschool|kids|family|teen|general",\n'
        ' "franchise": "<franchise name or empty>",\n'
        ' "production": "<studio or empty>",\n'
        ' "seasons": [{"number": 1, "year": "2018"}, ...],\n'
        ' "films": [{"title": "<film>", "year": "2001"}, ...],\n'
        ' "confident": true|false}\n'
        "seasons only for series; films only for franchise_film. Set "
        "confident false when sources disagree or the title is "
        "ambiguous. Never invent seasons or films. Plain hyphens "
        "only, never em dashes."
    )
    user = (f"Title to classify: {hint}\n"
            f"Today is {today}. JSON only.")
    model = (os.environ.get("CLAUDE_CARRIAGE_MODEL")
             or "claude-sonnet-4-6")
    web_tool = {"type": "web_search_20260209", "name": "web_search",
                "max_uses": 6}
    web_tool_legacy = {"type": "web_search_20250305", "name": "web_search",
                       "max_uses": 6}
    raw = None
    try:
        raw = claude_messages(system=system, user=user, model=model,
                              max_tokens=1600, temperature=0.1,
                              tools=[web_tool])
        if not raw:
            raw = claude_messages(system=system, user=user,
                                  model="claude-sonnet-4-6",
                                  max_tokens=1600, temperature=0.1,
                                  tools=[web_tool_legacy])
    except Exception as e:
        print(f"[{run_id}] content-structure research raised for "
              f"{hint!r}: {e}")
        return failed_structure_doc(hint, str(e))
    if not raw:
        return failed_structure_doc(hint, "empty response")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    i, j = cleaned.find("{"), cleaned.rfind("}")
    if i < 0 or j <= i:
        return failed_structure_doc(hint, "no JSON in response")
    try:
        data = json.loads(cleaned[i:j + 1])
    except Exception as e:
        return failed_structure_doc(hint, f"bad JSON: {e}")
    if data.get("confident") is False:
        return failed_structure_doc(hint, "research not confident")
    doc = normalize_structure_doc(data, title_hint=hint)
    if doc is None:
        return failed_structure_doc(hint, "unusable structure in response")
    n_units = (len(doc["seasons"]) if doc["kind"] == "series"
               else len(doc["films"]))
    print(f"[{run_id}] content structure for {hint!r}: {doc['kind']}"
          f" ({n_units} season(s)/film(s))")
    return doc


def ensure_structure(title, s3_client=None, run_id=""):
    """Cache-first structure lookup; researches and caches on a miss.
    Never raises; returns a failed doc at worst."""
    try:
        cached = load_cached_structure(title, s3_client=s3_client)
        if cached is not None:
            return cached
        if not research_enabled():
            return failed_structure_doc(title, "research disabled by env")
        doc = research_content_structure(title, run_id=run_id)
        save_structure_cache(title, doc, s3_client=s3_client)
        return doc
    except Exception as e:
        print(f"[{run_id}] ensure_structure failed (non-fatal): {e}")
        return failed_structure_doc(title, str(e))


# ---------------------------------------------------------------------------
# Scope parsing + resolution
# ---------------------------------------------------------------------------

_ALL_RE = re.compile(
    r"\b(?:all|every)\s+(?:the\s+)?season|\ball\s+(?:the\s+)?"
    r"(?:films?|movies?)|\b(?:whole|entire|full|complete)\s+"
    r"(?:franchise|series|saga|run)\b|\ball\s+of\s+(?:it|them)\b",
    re.IGNORECASE)
_LATEST_RE = re.compile(
    r"\b(?:most\s+recent|latest|newest|current)\s+"
    r"(?:season|film|movie|installment|entry)\b",
    re.IGNORECASE)
_SPECIFIC_WORD_RE = re.compile(
    r"\b(?:a\s+)?specific\s+(?:season|film|movie)\b", re.IGNORECASE)
_SEASON_N_RE = re.compile(
    r"\bseason\s*(\d{1,2})\b|(?<![A-Za-z0-9])s(\d{1,2})(?![A-Za-z0-9])",
    re.IGNORECASE)

_WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}
_SEASON_WORD_RE = re.compile(
    r"\bseason\s+(" + "|".join(_WORD_NUMS) + r")\b", re.IGNORECASE)


def scope_from_text(text, structure=None):
    """Parse a requested scope out of free text. Returns a request
    dict {'mode': 'all'|'latest'|'specific', 'season': n, 'film':
    title} or None when the text names no scope."""
    t = _clean(text)
    if not t:
        return None
    m = _SEASON_N_RE.search(t)
    if m:
        n = int(m.group(1) or m.group(2))
        if n > 0:
            return {"mode": "specific", "season": n}
    m = _SEASON_WORD_RE.search(t)
    if m:
        return {"mode": "specific", "season": _WORD_NUMS[m.group(1).lower()]}
    if _LATEST_RE.search(t):
        return {"mode": "latest"}
    if _ALL_RE.search(t):
        return {"mode": "all"}
    # A named franchise film in the text ("Fast X viewers"). Longest
    # match wins so "Breaking Dawn pt 2" beats "Breaking Dawn".
    if isinstance(structure, dict) \
            and structure.get("kind") == "franchise_film":
        tn = norm_token(t)
        fr = norm_token(structure.get("franchise")
                        or structure.get("title"))
        best = None
        for f in (structure.get("films") or []):
            fn = norm_token(f.get("title"))
            if not fn or fn == fr:
                # The film that shares the franchise's own name can't
                # be distinguished from the franchise mention itself.
                continue
            if fn in tn and (best is None
                             or len(fn) > len(norm_token(best))):
                best = f.get("title")
        if best:
            return {"mode": "specific", "film": best}
    return None


def resolve_scope(structure, req):
    """Canonicalize a loose scope request against the researched
    structure. Always returns a resolved scope dict:
      {'mode', 'season', 'season_year', 'film', 'film_year', 'label'}
    'all' is the fallback for anything unresolvable."""
    kind = (structure or {}).get("kind") or "other"
    seasons = (structure or {}).get("seasons") or []
    films = (structure or {}).get("films") or []
    req = req if isinstance(req, dict) else {}
    mode = str(req.get("mode") or "all").strip().lower()
    out = {"mode": "all", "season": None, "season_year": "",
           "film": "", "film_year": "", "label": ""}

    def _all_label():
        if kind == "series" and seasons:
            return (f"all {len(seasons)} seasons" if len(seasons) != 1
                    else "the full season")
        if kind == "franchise_film" and films:
            return f"the whole franchise ({len(films)} films)"
        return "the full title"

    if kind == "series" and seasons:
        if mode == "latest":
            pick = seasons[-1]
        elif mode == "specific" and req.get("season"):
            pick = next((s for s in seasons
                         if s["number"] == int(req["season"])), None)
            if pick is None:
                out["label"] = _all_label()
                return out
        else:
            out["label"] = _all_label()
            return out
        out["mode"] = "latest" if mode == "latest" else "specific"
        out["season"] = pick["number"]
        out["season_year"] = pick.get("year") or ""
        yr = f" ({out['season_year']})" if out["season_year"] else ""
        out["label"] = f"Season {pick['number']}{yr}"
        return out

    if kind == "franchise_film" and films:
        if mode == "latest":
            pick = films[-1]
        elif mode == "specific" and req.get("film"):
            want = norm_token(req["film"])
            pick = next((f for f in films
                         if norm_token(f.get("title")) == want), None)
            if pick is None:
                pick = next((f for f in films
                             if want and want in norm_token(
                                 f.get("title"))), None)
            if pick is None:
                out["label"] = _all_label()
                return out
        else:
            out["label"] = _all_label()
            return out
        out["mode"] = "latest" if mode == "latest" else "specific"
        out["film"] = pick.get("title") or ""
        out["film_year"] = pick.get("year") or ""
        yr = f" ({out['film_year']})" if out["film_year"] else ""
        out["label"] = f"{out['film']}{yr}"
        return out

    out["label"] = _all_label()
    return out


def needs_scope_clarify(structure):
    """True when the structure supports a real scope choice: a series
    with 2+ seasons, or a franchise with 2+ films."""
    if not isinstance(structure, dict) or structure.get("research_failed"):
        return False
    if structure.get("kind") == "series":
        return len(structure.get("seasons") or []) >= 2
    if structure.get("kind") == "franchise_film":
        return len(structure.get("films") or []) >= 2
    return False


def scope_chip_payload(structure):
    """The clarify-step data shape the dashboard chips render from.
    Rides the draft as viewer_scope_data; mode flips 'scope' -> 'pick'
    when the user asks for a specific season/film."""
    seasons = []
    for s in (structure.get("seasons") or [])[:24]:
        yr = f" ({s['year']})" if s.get("year") else ""
        seasons.append({"number": s["number"], "year": s.get("year") or "",
                        "label": f"Season {s['number']}{yr}"})
    films = []
    for f in (structure.get("films") or [])[:20]:
        yr = f" ({f['year']})" if f.get("year") else ""
        films.append({"title": f["title"], "year": f.get("year") or "",
                      "label": f"{f['title']}{yr}"})
    return {
        "mode": "scope",
        "kind": structure.get("kind") or "other",
        "title": structure.get("title") or "",
        "franchise": structure.get("franchise") or "",
        "production": structure.get("production") or "",
        "seasons": seasons,
        "films": films,
    }


# ---------------------------------------------------------------------------
# Naming + prose
# ---------------------------------------------------------------------------

_TRAILING_QUALIFIER_RE = re.compile(
    r"\s+(viewers?|watchers?|streamers?|bingers?|binge[\s-]?watchers?)$",
    re.IGNORECASE)


def scoped_subject_name(subject, structure, scope):
    """Deliverable name for the scoped universe. TU = subject name per
    the naming rules, so the scope lives IN the name:
      'Yellowstone Viewers' + Season 5   -> 'Yellowstone Season 5 Viewers'
      'Fast and Furious Viewers' + Fast X -> 'Fast X Viewers'
    'all' keeps the name unchanged."""
    subj = _clean(subject)
    if not subj or not isinstance(scope, dict) \
            or scope.get("mode") not in ("latest", "specific"):
        return subj
    m = _TRAILING_QUALIFIER_RE.search(subj)
    qual = m.group(1) if m else "Viewers"
    stem = subj[:m.start()].strip() if m else subj
    kind = (structure or {}).get("kind")
    if kind == "series" and scope.get("season"):
        want = f"season {scope['season']}"
        if want in stem.lower():
            return subj
        return f"{stem} Season {scope['season']} {qual.title()}"
    if kind == "franchise_film" and scope.get("film"):
        film = _strip_apostrophes(_clean(scope["film"]))
        if norm_token(film) in norm_token(stem):
            return subj
        return f"{film} {qual.title()}"
    return subj


def scope_prose(structure, scope, assumed=False):
    """Partner-safe sentence stating the scope this build covers.
    No internal vocabulary; plain audience language only."""
    title = _clean((structure or {}).get("title")) or "the title"
    kind = (structure or {}).get("kind")
    scope = scope if isinstance(scope, dict) else {}
    mode = scope.get("mode") or "all"
    if kind == "series":
        n = len((structure or {}).get("seasons") or [])
        if mode in ("latest", "specific") and scope.get("season"):
            lead = f"Scoped to {title} {scope.get('label') or ''}".strip()
            return lead + "."
        lead = (f"Covers all {n} seasons of {title}" if n > 1
                else f"Covers {title}")
        if assumed and n > 1:
            return (lead + "; name a specific season in the request "
                    "to scope it.")
        return lead + "."
    if kind == "franchise_film":
        fr = _clean((structure or {}).get("franchise")) or title
        n = len((structure or {}).get("films") or [])
        if mode in ("latest", "specific") and scope.get("film"):
            return f"Scoped to {scope.get('label') or scope['film']}."
        lead = (f"Covers the whole {fr} franchise ({n} films)" if n > 1
                else f"Covers {fr}")
        if assumed and n > 1:
            return (lead + "; name a specific film in the request "
                    "to scope it.")
        return lead + "."
    return ""


def scope_sample_fraction(subject, structure, scope):
    """Deterministic salted fraction the draft sample scales by when a
    single season/film is chosen out of a larger body. Sample sizing
    is the one place a calculation is allowed; the worker's own
    audience-ceiling research still refines it downstream. 1.0 when
    the scope is 'all' (or unresolvable)."""
    if not isinstance(scope, dict) \
            or scope.get("mode") not in ("latest", "specific"):
        return 1.0
    kind = (structure or {}).get("kind")
    if kind == "series":
        units = (structure or {}).get("seasons") or []
        pos = next((i for i, s in enumerate(units)
                    if s["number"] == scope.get("season")), None)
    elif kind == "franchise_film":
        units = (structure or {}).get("films") or []
        pos = next((i for i, f in enumerate(units)
                    if norm_token(f.get("title"))
                    == norm_token(scope.get("film"))), None)
    else:
        return 1.0
    if not units or pos is None or len(units) < 2:
        return 1.0
    # Recency r: 0 = the newest unit. In a trailing-12-month digital
    # window, the newest season/film holds most of the in-window
    # audience; older units hold progressively less.
    r = (len(units) - 1) - pos
    h = hashlib.sha256(
        f"{subject}|scope|{kind}|{pos}".encode()).hexdigest()
    u = int(h[:10], 16) / float(0xFFFFFFFFFF)
    lo, hi = 0.55, 0.80
    frac = (lo + (hi - lo) * u) * (0.82 ** r)
    return max(0.12, round(frac, 4))


# ---------------------------------------------------------------------------
# Kids-title audience definition (2026-08-27 Jenna, Paw Patrol directive:
# "if the shows research comes back that it is a preschool or kid
# audience it should ask the viewer if you want actual under 18 viewers
# or parents of the viewers")
# ---------------------------------------------------------------------------

def is_kids_title(structure):
    """True when the researched primary audience is preschool/kids."""
    if not isinstance(structure, dict) or structure.get("research_failed"):
        return False
    return str(structure.get("audience") or "").lower() in (
        "preschool", "kids")


def needs_audience_clarify(structure):
    return is_kids_title(structure)


_PARENTS_OF_RE = re.compile(
    r"\bparents?\s+of\b|\b(?:moms?|dads?|mothers?|fathers?)\s+of\b"
    r"|\bparents?\s+whose\b|\bco[\s-]?viewing\s+(?:adults?|parents?)\b"
    r"|\bparent\s+(?:audience|cohort|universe)\b",
    re.IGNORECASE)
_UNDER18_RE = re.compile(
    r"\bunder[\s-]?(?:18|eighteen)\b|\b17\s+and\s+under\b"
    r"|\b(?:kids?|children)\s+who\s+watch\b"
    r"|\bactual\s+(?:kid|child|under)[\w-]*\s*(?:viewers?|audience)?\b"
    r"|\b(?:child|kid)\s+(?:viewers?|audience)\b",
    re.IGNORECASE)


def audience_from_text(text):
    """Parse an audience definition out of free text. Returns
    'parents' | 'under18' | None."""
    t = _clean(text)
    if not t:
        return None
    if _PARENTS_OF_RE.search(t):
        return "parents"
    if _UNDER18_RE.search(t):
        return "under18"
    return None


def audience_chip_payload(structure):
    """Clarify-step data for the kids-title definition ask. Rides the
    draft as viewer_audience_data."""
    return {
        "title": (structure or {}).get("title") or "",
        "audience": (structure or {}).get("audience") or "kids",
    }


def audience_subject_name(subject, choice):
    """Deliverable name carrying the universe definition whole (naming
    rule 6b: universe-defining qualifiers stay in the TU name):
      parents -> 'Parents of Paw Patrol Viewers'
      under18 -> 'Paw Patrol Under-18 Viewers'
    """
    subj = _clean(subject)
    if not subj:
        return subj
    low = subj.lower()
    if choice == "parents":
        if low.startswith("parents of"):
            return subj
        if not _TRAILING_QUALIFIER_RE.search(subj):
            subj = subj + " Viewers"
        return f"Parents of {subj}"
    if choice == "under18":
        if "under-18" in low or "under 18" in low:
            return subj
        m = _TRAILING_QUALIFIER_RE.search(subj)
        if m:
            return (f"{subj[:m.start()].strip()} Under-18 "
                    f"{m.group(1).title()}")
        return f"{subj} Under-18 Viewers"
    return subj


def audience_prose(structure, choice, assumed=False):
    """Partner-safe sentence stating the universe definition. Plain
    audience language only."""
    title = _clean((structure or {}).get("title")) or "the title"
    if choice == "parents":
        lead = (f"Defined as the parents and co-viewing adults of "
                f"{title}'s young audience")
        if assumed:
            return (lead + "; say kids who watch it to build the "
                    "actual under-18 audience instead.")
        return lead + "."
    if choice == "under18":
        return (f"Defined as the actual under-18 audience watching "
                f"{title}.")
    return ""


def audience_persona_note(structure, choice):
    """The sentence that rides persona_notes so the build reasons
    against the chosen membership definition."""
    title = _clean((structure or {}).get("title")) or "the title"
    if choice == "parents":
        return (f"Universe definition: the PARENTS AND CO-VIEWING "
                f"ADULTS of {title}'s young audience - the adults who "
                f"manage the household, purchase around the title, and "
                f"co-view it. Demo shape is adult (concentrated 25-44, "
                f"parental status very high); every behavior reads as "
                f"the managing parent, not the child.")
    if choice == "under18":
        return (f"Universe definition: the ACTUAL UNDER-18 CHILD "
                f"AUDIENCE watching {title}. AGE concentrates in the "
                f"youngest bucket (17 and Under); the behavioral shape "
                f"stays kid-appropriate (kids content platforms, toys, "
                f"games, family dining) and adult-only categories "
                f"(betting, alcohol, finance) read near-floor.")
    return ""


# ---------------------------------------------------------------------------
# Kids-product audience definition (2026-08-27 Jenna, Toca Boca
# directive: "This is a child's toy [app], but it built a parental
# audience during the prompt process. It never asked me if I wanted the
# end user which would be a child or the parent.") Generalizes the
# kids-title clarify to non-show subjects: apps, games, toys, and
# franchises whose END USERS are predominantly children.
# ---------------------------------------------------------------------------

PRODUCT_PREFIX = "system/kids_product_audience/"
PRODUCT_TTL_DAYS = 30

VALID_PRODUCT_TYPES = ("app", "game", "toy", "franchise", "brand", "other")

_PRODUCT_USER_TAIL_RE = re.compile(
    r"\s+(players?|kids?|children|users?|end[\s-]?users?)$",
    re.IGNORECASE)
_PARENTS_HEAD_RE = re.compile(
    r"^parents\s+of\s+(?:the\s+)?", re.IGNORECASE)


def _product_stem(subject):
    """The clean product name behind a subject: cut suffixes, audience
    noun tails ('Toca Boca players'), and 'Parents of ...' heads all
    reduce to the product itself."""
    subj = _clean(subject)
    if not subj:
        return subj
    stem = subj.split(" - ")[0].strip()
    stem = _PARENTS_HEAD_RE.sub("", stem).strip()
    m = _PRODUCT_USER_TAIL_RE.search(stem)
    if m:
        stem = stem[:m.start()].strip()
    return stem or subj


def product_cache_key(subject):
    n = norm_token(_product_stem(subject))
    return f"{PRODUCT_PREFIX}{n}.json" if n else ""


def normalize_product_doc(data, subject_hint=""):
    """Coerce a raw research response into the canonical kids-product
    doc. Returns None when unusable."""
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("kids_product"), bool):
        return None
    ptype = _clean(data.get("product_type")).lower()
    if ptype not in VALID_PRODUCT_TYPES:
        ptype = "other"
    age = str(data.get("end_user_age") or "").strip().lower()
    if age not in ("preschool", "kids", "teens", "general"):
        age = ""
    return {
        "kids_product_audience": True,
        "subject": _clean(data.get("subject")) or _clean(subject_hint),
        "kids_product": bool(data.get("kids_product")),
        "product_type": ptype,
        "end_user_age": age,
        "confident": bool(data.get("confident", True)),
        "research_failed": False,
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def failed_product_doc(subject, reason):
    return {
        "kids_product_audience": True,
        "subject": _clean(subject),
        "kids_product": False,
        "product_type": "other",
        "end_user_age": "",
        "confident": False,
        "research_failed": True,
        "failure_reason": _clean(reason)[:300],
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def load_cached_product_doc(subject, s3_client=None,
                            max_age_days=PRODUCT_TTL_DAYS):
    key = product_cache_key(subject)
    if not key:
        return None
    try:
        body = _s3(s3_client).get_object(Bucket=BUCKET, Key=key)["Body"].read()
        doc = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or not doc.get("kids_product_audience"):
        return None
    age_limit = 1 if doc.get("research_failed") else max_age_days
    try:
        ts = datetime.fromisoformat(str(doc.get("researched_at")))
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        if age_days > age_limit:
            return None
    except Exception:
        return None
    return doc


def save_product_doc_cache(subject, doc, s3_client=None):
    key = product_cache_key(subject)
    if not key or not isinstance(doc, dict):
        return False
    try:
        _s3(s3_client).put_object(
            Bucket=BUCKET, Key=key,
            Body=json.dumps(doc, indent=1).encode("utf-8"),
            ContentType="application/json")
        return True
    except Exception as e:
        print(f"[product-audience] cache write failed for "
              f"{subject!r}: {e}")
        return False


def research_product_audience(subject, run_id=""):
    """One web-research call: is this subject a product whose END
    USERS are predominantly children? Returns a canonical kids-product
    doc (possibly research_failed=True). Never raises."""
    hint = _clean(subject)
    if not hint:
        return failed_product_doc(subject, "empty subject")
    try:
        from migration.claude_client import claude_messages
    except ImportError:
        try:
            from claude_client import claude_messages  # twin layout
        except ImportError:
            return failed_product_doc(hint, "claude client unavailable")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system = (
        "You are classifying a subject for a US audience build. Use "
        "the web_search tool when unsure and cross-check sources.\n"
        "Decide whether the subject is a KIDS' PRODUCT: a product, "
        "app, game, toy, or franchise whose END USERS are "
        "predominantly children (roughly under 13). Examples of the "
        "class: kids play apps (Toca Boca, PBS Kids Games), "
        "kid-dominant game platforms (Roblox), toy brands, preschool "
        "character franchises.\n"
        "NOT kids products: TV shows and films (a separate pathway "
        "owns those), family co-viewing brands whose users span all "
        "ages (Pixar, Nintendo), general brands with some child "
        "users, and adult/general products.\n"
        "Also classify the product type and the end users' age band:\n"
        "  - preschool: end users roughly 2-6\n"
        "  - kids: end users roughly 6-12\n"
        "  - teens: end users predominantly 13-17\n"
        "  - general: users span all ages\n"
        "Respond with ONE JSON object and nothing else:\n"
        '{"subject": "<canonical product name>",\n'
        ' "kids_product": true|false,\n'
        ' "product_type": "app|game|toy|franchise|brand|other",\n'
        ' "end_user_age": "preschool|kids|teens|general",\n'
        ' "confident": true|false}\n'
        "Set confident false when sources disagree or the subject is "
        "ambiguous. Plain hyphens only, never em dashes."
    )
    user = (f"Subject to classify: {hint}\n"
            f"Today is {today}. JSON only.")
    model = (os.environ.get("CLAUDE_CARRIAGE_MODEL")
             or "claude-sonnet-4-6")
    web_tool = {"type": "web_search_20260209", "name": "web_search",
                "max_uses": 4}
    web_tool_legacy = {"type": "web_search_20250305", "name": "web_search",
                       "max_uses": 4}
    raw = None
    try:
        raw = claude_messages(system=system, user=user, model=model,
                              max_tokens=700, temperature=0.1,
                              tools=[web_tool])
        if not raw:
            raw = claude_messages(system=system, user=user,
                                  model="claude-sonnet-4-6",
                                  max_tokens=700, temperature=0.1,
                                  tools=[web_tool_legacy])
    except Exception as e:
        print(f"[{run_id}] kids-product research raised for "
              f"{hint!r}: {e}")
        return failed_product_doc(hint, str(e))
    if not raw:
        return failed_product_doc(hint, "empty response")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    i, j = cleaned.find("{"), cleaned.rfind("}")
    if i < 0 or j <= i:
        return failed_product_doc(hint, "no JSON in response")
    try:
        data = json.loads(cleaned[i:j + 1])
    except Exception as e:
        return failed_product_doc(hint, f"bad JSON: {e}")
    doc = normalize_product_doc(data, subject_hint=hint)
    if doc is None:
        return failed_product_doc(hint, "unusable classification")
    print(f"[{run_id}] kids-product read for {hint!r}: "
          f"kids_product={doc['kids_product']} "
          f"type={doc['product_type']} age={doc['end_user_age'] or '?'}")
    return doc


def ensure_product_audience(subject, s3_client=None, run_id=""):
    """Cache-first kids-product lookup; researches and caches on a
    miss. Research runs on the clean product stem ('Toca Boca
    players' -> 'Toca Boca'). Never raises; returns a failed doc at
    worst."""
    try:
        stem = _product_stem(subject)
        cached = load_cached_product_doc(stem, s3_client=s3_client)
        if cached is not None:
            return cached
        if not research_enabled():
            return failed_product_doc(stem, "research disabled by env")
        doc = research_product_audience(stem, run_id=run_id)
        save_product_doc_cache(stem, doc, s3_client=s3_client)
        return doc
    except Exception as e:
        print(f"[{run_id}] ensure_product_audience failed (non-fatal): {e}")
        return failed_product_doc(subject, str(e))


def is_kids_product(doc):
    """True when the researched end users are predominantly
    preschool/kids-age children. Mirrors is_kids_title's bar: teens
    and all-ages products do not clarify."""
    if not isinstance(doc, dict) or doc.get("research_failed"):
        return False
    if not doc.get("kids_product"):
        return False
    return str(doc.get("end_user_age") or "").lower() in (
        "preschool", "kids")


def needs_product_audience_clarify(doc):
    return is_kids_product(doc)


_PLAYERS_RE = re.compile(
    r"\bplayers?\b|\b(?:kids?|child(?:ren)?)\s+who\s+play\b"
    r"|\bend[\s-]?users?\b|\b(?:kid|child)\s+users?\b"
    r"|\bthe\s+kids\s+themselves\b|\busers?\s+themselves\b",
    re.IGNORECASE)
_BUYERS_RE = re.compile(
    r"\bbuyers?\b|\bpurchas(?:e|es|ers?|ing)\b|\bshoppers?\b"
    r"|\bbuy(?:s|ing)?\b|\bgift[\s-]?(?:givers?|buyers?)\b"
    r"|\bpay(?:ing|ers?)\s+(?:for|customers?)\b",
    re.IGNORECASE)
_PARENT_WORD_RE = re.compile(
    r"\bparents?\b|\bmoms?\b|\bdads?\b|\bmothers?\b|\bfathers?\b",
    re.IGNORECASE)


def product_audience_from_text(text):
    """Parse a kids-product audience definition out of free text.
    Returns 'parents' | 'under18' | None. Play-framed words (players,
    kids who play, end users) read as the under-18 end users;
    purchase-framed words (buyers, purchase, parents) read as the
    buying parents."""
    t = _clean(text)
    if not t:
        return None
    if _PARENTS_OF_RE.search(t):
        return "parents"
    if _UNDER18_RE.search(t) or _PLAYERS_RE.search(t):
        return "under18"
    if _BUYERS_RE.search(t) or _PARENT_WORD_RE.search(t):
        return "parents"
    return None


def product_audience_chip_payload(doc):
    """Clarify-step data for the kids-product definition ask. Rides
    the draft as viewer_audience_data (same step as the kids-title
    ask; kind='product' flips the wording + parse)."""
    return {
        "kind": "product",
        "title": (doc or {}).get("subject") or "",
        "audience": (doc or {}).get("end_user_age") or "kids",
        "product_type": (doc or {}).get("product_type") or "other",
    }


def product_audience_subject_name(subject, choice):
    """Deliverable name for a kids-product universe (Jenna 2026-08-27,
    Toca Boca): the definition rides as the dash suffix:
      parents -> '{Subject} - Parents of Players'
      under18 -> '{Subject} - Players'
    Idempotent; strips audience words the ask already carried
    ('Toca Boca players' -> stem 'Toca Boca')."""
    subj = _clean(subject)
    if not subj:
        return subj
    stem = _product_stem(subj)
    if choice == "parents":
        return f"{stem} - Parents of Players"
    if choice == "under18":
        return f"{stem} - Players"
    return subj


def product_audience_prose(doc, choice, assumed=False):
    """Partner-safe sentence stating the universe definition. Plain
    audience language only."""
    subject = _clean((doc or {}).get("subject")
                     or (doc or {}).get("title")) or "the product"
    if choice == "parents":
        lead = (f"Defined as the parents and buying adults behind "
                f"{subject}'s young users")
        if assumed:
            return (lead + "; say the players themselves to build "
                    "the under-18 users instead.")
        return lead + "."
    if choice == "under18":
        return (f"Defined as the under-18 players themselves - the "
                f"kids who actually use {subject}.")
    return ""


def product_audience_persona_note(doc, choice):
    """The sentence that rides persona_notes so the build reasons
    against the chosen membership definition."""
    subject = _clean((doc or {}).get("subject")
                     or (doc or {}).get("title")) or "the product"
    ptype = str((doc or {}).get("product_type") or "product")
    if ptype not in VALID_PRODUCT_TYPES or ptype == "other":
        ptype = "product"
    if choice == "parents":
        return (f"Universe definition: the PARENTS AND BUYING ADULTS "
                f"behind {subject}'s young end users - the adults who "
                f"download, purchase, subscribe, and manage the "
                f"household around the {ptype}. Demo shape is adult "
                f"(concentrated 25-44, parental status very high); "
                f"every behavior reads as the managing parent, not "
                f"the child.")
    if choice == "under18":
        return (f"Universe definition: the ACTUAL UNDER-18 END USERS "
                f"of {subject} - the kids who play with and use it "
                f"themselves. AGE concentrates in the youngest bucket "
                f"(17 and Under); the behavioral shape stays "
                f"kid-appropriate (kids content platforms, toys, "
                f"games, family dining) and adult-only categories "
                f"(betting, alcohol, finance) read near-floor.")
    return ""


# ---------------------------------------------------------------------------
# Scoped-URL research (episode/season paths per carrier)
# ---------------------------------------------------------------------------

def urls_cache_key(subject, scope):
    stem = str(subject or "").split(" - ")[0]
    n = norm_token(stem)
    tag = norm_token((scope or {}).get("label") or "ALL") or "ALL"
    return f"{STRUCT_PREFIX}{n}_{tag}_urls.json" if n else ""


def load_cached_scope_urls(subject, scope, s3_client=None,
                           max_age_days=URLS_TTL_DAYS):
    key = urls_cache_key(subject, scope)
    if not key:
        return None
    try:
        body = _s3(s3_client).get_object(Bucket=BUCKET, Key=key)["Body"].read()
        doc = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or not doc.get("scope_urls"):
        return None
    age_limit = 1 if doc.get("research_failed") else max_age_days
    try:
        ts = datetime.fromisoformat(str(doc.get("researched_at")))
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        if age_days > age_limit:
            return None
    except Exception:
        return None
    return doc


def save_scope_urls_cache(subject, scope, doc, s3_client=None):
    key = urls_cache_key(subject, scope)
    if not key or not isinstance(doc, dict):
        return False
    try:
        _s3(s3_client).put_object(
            Bucket=BUCKET, Key=key,
            Body=json.dumps(doc, indent=1).encode("utf-8"),
            ContentType="application/json")
        return True
    except Exception as e:
        print(f"[content-scope] urls cache write failed for "
              f"{subject!r}: {e}")
        return False


def normalize_scope_urls_doc(data, scope_label=""):
    """Coerce raw research output into the canonical scope-urls doc."""
    if not isinstance(data, dict):
        return None
    urls = []
    seen = set()
    for u in (data.get("urls") or []):
        if not isinstance(u, dict):
            continue
        raw = _clean(u.get("url"))
        if not raw:
            continue
        k = norm_token(raw)
        if not k or k in seen:
            continue
        seen.add(k)
        urls.append({
            "url": raw,
            "platform": _clean(u.get("platform"))[:60],
            "season_label": _clean(u.get("season_label"))[:60],
            "note": _clean(u.get("note"))[:120],
        })
    if not urls:
        return None
    return {
        "scope_urls": True,
        "scope_label": _clean(scope_label),
        "urls": urls[:200],
        "research_failed": False,
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def failed_scope_urls_doc(scope_label, reason):
    return {
        "scope_urls": True,
        "scope_label": _clean(scope_label),
        "urls": [],
        "research_failed": True,
        "failure_reason": _clean(reason)[:300],
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def research_scope_urls(subject, structure, scope, carriage_doc=None,
                        run_id=""):
    """One web-research call: the actual content URLs for the CHOSEN
    scope on the researched carriers (title / season / episode paths).
    Only verified URLs come back; an empty result is better than a
    guessed URL. Never raises."""
    title = _clean((structure or {}).get("title")) or _clean(subject)
    label = (scope or {}).get("label") or "the full title"
    try:
        from migration.claude_client import claude_messages
    except ImportError:
        try:
            from claude_client import claude_messages  # twin layout
        except ImportError:
            return failed_scope_urls_doc(label, "claude client unavailable")
    carriers = []
    if isinstance(carriage_doc, dict):
        carriers = [c.get("platform") for c in
                    (carriage_doc.get("carriers") or []) if c.get("platform")]
    kind = (structure or {}).get("kind") or "other"
    if kind == "series":
        if scope.get("mode") in ("latest", "specific") and scope.get("season"):
            want = (f"Season {scope['season']} of {title}: the season "
                    f"page and the individual episode pages for that "
                    f"season")
        else:
            want = (f"every season of {title}: the title page plus "
                    f"season/episode pages where verifiable")
    elif kind == "franchise_film":
        if scope.get("mode") in ("latest", "specific") and scope.get("film"):
            want = f"the film {scope['film']}: its title/watch page"
        else:
            fr = _clean((structure or {}).get("franchise")) or title
            want = (f"every film in the {fr} franchise: each film's "
                    f"title/watch page")
    else:
        want = f"{title}: its title/watch page"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system = (
        "You are collecting the exact CONTENT URLs where a US viewer "
        "watches a title on its digital carriers. Use the web_search "
        "tool. Only return URLs you actually verified from real "
        "sources; an empty list is better than a guessed URL.\n"
        "URL form: full path on the carrier's own domain, e.g.\n"
        "  netflix.com/title/81511993 or netflix.com/watch/81511993\n"
        "  hulu.com/watch/<episode-uuid>\n"
        "  disneyplus.com/play/<uuid>\n"
        "  max.com/... (the page's uuid path)\n"
        "  paramountplus.com/shows/video/<token>/\n"
        "  peacocktv.com/watch/... (the page's uuid path)\n"
        "  amazon.com/gp/video/detail/<token>\n"
        "  tv.apple.com/us/show/<slug>/umc.cmc.<token>\n"
        "  youtube.com/watch?v=<id>\n"
        "  starz.com/us/en/play/<id>\n"
        "No protocol, no www, keep the id/token segments exactly as "
        "they appear. For each URL name the platform and, when it "
        "belongs to a specific season, the season as 'Season N'.\n"
        "Respond with ONE JSON object and nothing else:\n"
        '{"urls": [{"url": "<domain/path>", "platform": "<service>",\n'
        '           "season_label": "Season N or empty",\n'
        '           "note": "<episode/film name, short>"}, ...],\n'
        ' "confident": true|false}\n'
        "Plain hyphens only, never em dashes."
    )
    user_lines = [f"Title: {title}", f"Collect URLs for: {want}"]
    if carriers:
        user_lines.append(
            "Verified digital carriers to search on: "
            + ", ".join(carriers[:6]))
    user_lines.append(f"Today is {today}. JSON only.")
    model = (os.environ.get("CLAUDE_CARRIAGE_MODEL")
             or "claude-sonnet-4-6")
    web_tool = {"type": "web_search_20260209", "name": "web_search",
                "max_uses": 8}
    web_tool_legacy = {"type": "web_search_20250305", "name": "web_search",
                       "max_uses": 8}
    raw = None
    try:
        raw = claude_messages(system=system, user="\n".join(user_lines),
                              model=model, max_tokens=2600,
                              temperature=0.1, tools=[web_tool])
        if not raw:
            raw = claude_messages(system=system, user="\n".join(user_lines),
                                  model="claude-sonnet-4-6",
                                  max_tokens=2600, temperature=0.1,
                                  tools=[web_tool_legacy])
    except Exception as e:
        print(f"[{run_id}] scope-url research raised for {subject!r}: {e}")
        return failed_scope_urls_doc(label, str(e))
    if not raw:
        return failed_scope_urls_doc(label, "empty response")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    i, j = cleaned.find("{"), cleaned.rfind("}")
    if i < 0 or j <= i:
        return failed_scope_urls_doc(label, "no JSON in response")
    try:
        data = json.loads(cleaned[i:j + 1])
    except Exception as e:
        return failed_scope_urls_doc(label, f"bad JSON: {e}")
    doc = normalize_scope_urls_doc(data, scope_label=label)
    if doc is None:
        return failed_scope_urls_doc(label, "no usable URLs in response")
    print(f"[{run_id}] scope URLs for {subject!r} ({label}): "
          f"{len(doc['urls'])} verified URL(s)")
    return doc


def ensure_scope_urls(subject, structure, scope, carriage_doc=None,
                      s3_client=None, run_id=""):
    """Cache-first scoped-URL lookup; researches and caches on a miss.
    Never raises; returns a failed doc at worst."""
    try:
        cached = load_cached_scope_urls(subject, scope, s3_client=s3_client)
        if cached is not None:
            return cached
        if not research_enabled():
            return failed_scope_urls_doc((scope or {}).get("label") or "",
                                         "research disabled by env")
        doc = research_scope_urls(subject, structure, scope,
                                  carriage_doc=carriage_doc, run_id=run_id)
        save_scope_urls_cache(subject, scope, doc, s3_client=s3_client)
        return doc
    except Exception as e:
        print(f"[{run_id}] ensure_scope_urls failed (non-fatal): {e}")
        return failed_scope_urls_doc((scope or {}).get("label") or "",
                                     str(e))


# ---------------------------------------------------------------------------
# content_mapping row building (formatted EXACTLY like existing rows)
# ---------------------------------------------------------------------------
#
# Observed conventions in reference.content_mapping (sampled
# 2026-08-27; 6,324 rows):
#   SHOW    clean title / franchise name ('Yellowstone', 'John Wick')
#   URL     platform-specific fragment (see per-platform rules below)
#   PRODUCTION  studio ('WBD', 'Paramount TV Studios', ...; '' ok)
#   PLATFORM    'Netflix' / 'Hulu' / 'Peacock' / 'HBO MAX' /
#               'Paramount Plus' / 'Disney Plus' / 'Amazon' /
#               'Apple TV' / 'YouTube' / 'NBC' / 'Starz' / ...
#   SEASON  'Season N' for series; the film identifier for franchise
#           entries ('Fast X', 'One', 'Ballerina'); 'Movie' for
#           standalone films
#   CATEGORY / SUB_CATEGORY  '' (empty on 6,321 of 6,324 rows)

PLATFORM_BY_DOMAIN = {
    "netflix.com": "Netflix",
    "hulu.com": "Hulu",
    "peacocktv.com": "Peacock",
    "max.com": "HBO MAX",
    "hbomax.com": "HBO MAX",
    "paramountplus.com": "Paramount Plus",
    "disneyplus.com": "Disney Plus",
    "amazon.com": "Amazon",
    "primevideo.com": "Amazon",
    "tv.apple.com": "Apple TV",
    "appletv.com": "Apple TV",
    "youtube.com": "YouTube",
    "nbc.com": "NBC",
    "starz.com": "Starz",
    "tubitv.com": "Tubi",
    "tubi.tv": "Tubi",
    "pluto.tv": "Pluto",
    "plutotv.com": "Pluto",
    "mgmplus.com": "MGM Plus",
}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE)
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")


def _host_and_segs(u):
    s = str(u or "").strip()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s, flags=re.IGNORECASE)
    s = s.split("#", 1)[0].strip().lstrip("/")
    host_and_path, _, query = s.partition("?")
    host, _, path = host_and_path.partition("/")
    host = re.sub(r"^www\.", "", host.strip().lower())
    segs = [p for p in re.sub(r"/{2,}", "/", path).strip("/").split("/")
            if p]
    return host, segs, query.strip()


def content_mapping_fragment(raw_url):
    """Convert a researched full URL into the (platform, fragment)
    pair matching that platform's observed row convention. Returns
    (None, '') when the URL doesn't fit a known convention - such URLs
    still ride BRAND INPUT; they just don't enter the table."""
    host, segs, query = _host_and_segs(raw_url)
    if not host:
        return None, ""
    platform = PLATFORM_BY_DOMAIN.get(host)
    if not platform:
        return None, ""
    if platform == "Netflix":
        # watch/<digits> is the row form; title/<id> pages share the
        # same id-space, so they canonicalize to watch/<id>.
        if len(segs) >= 2 and segs[-2] in ("watch", "title") \
                and re.match(r"^\d{5,12}$", segs[-1]):
            return platform, f"watch/{segs[-1]}"
        return None, ""
    if platform in ("Hulu", "Peacock", "HBO MAX"):
        # Bare episode/title UUID (the dominant row form).
        for seg in reversed(segs):
            if _UUID_RE.match(seg):
                return platform, seg.lower()
        return None, ""
    if platform == "Paramount Plus":
        # shows/video/<opaque-token>/ -> bare token.
        for i, seg in enumerate(segs):
            if seg == "video" and i + 1 < len(segs) \
                    and _OPAQUE_TOKEN_RE.match(segs[i + 1]):
                return platform, segs[i + 1]
        if segs and _OPAQUE_TOKEN_RE.match(segs[-1]) \
                and len(segs[-1]) >= 24:
            return platform, segs[-1]
        return None, ""
    if platform == "Disney Plus":
        # play/<uuid> is the row form; browse/entity-<uuid> shares the
        # id-space and canonicalizes to play/<uuid>.
        for seg in reversed(segs):
            m = re.match(r"^(?:entity-)?([0-9a-f-]{36})$", seg,
                         re.IGNORECASE)
            if m and _UUID_RE.match(m.group(1)):
                return platform, f"play/{m.group(1).lower()}"
        return None, ""
    if platform == "Amazon":
        # detail/<token> or the amzn1.dv.gti.<uuid> GTI form.
        for i, seg in enumerate(segs):
            if seg == "detail" and i + 1 < len(segs) \
                    and _OPAQUE_TOKEN_RE.match(segs[i + 1]):
                return platform, f"detail/{segs[i + 1]}"
        for seg in segs:
            if seg.lower().startswith("amzn1.dv.gti."):
                return platform, seg
        return None, ""
    if platform == "Apple TV":
        # Rows carry the bare umc.cmc token body.
        for seg in reversed(segs):
            m = re.match(r"^umc\.cmc\.([0-9a-z]{10,})$", seg,
                         re.IGNORECASE)
            if m:
                return platform, m.group(1).lower()
        return None, ""
    if platform == "YouTube":
        m = re.search(r"(?:^|&)v=([A-Za-z0-9_-]{6,})", query)
        if m:
            return platform, f"watch?v={m.group(1)}"
        if len(segs) >= 2 and segs[0] == "watch" \
                and re.match(r"^[A-Za-z0-9_-]{6,}$", segs[1]):
            return platform, f"watch?v={segs[1]}"
        return None, ""
    if platform == "NBC":
        # <episode-slug>/<digits> (the last two path segments).
        if len(segs) >= 2 and re.match(r"^\d{6,}$", segs[-1]):
            return platform, f"{segs[-2]}/{segs[-1]}"
        return None, ""
    if platform == "Starz":
        # Full-domain path is the row form: starz.com/us/en/play/<id>.
        if segs and re.match(r"^\d{4,}$", segs[-1]):
            return platform, "starz.com/" + "/".join(segs)
        return None, ""
    if platform in ("Tubi", "Pluto", "MGM Plus"):
        if segs and _OPAQUE_TOKEN_RE.match(segs[-1]):
            return platform, segs[-1]
        if segs and re.match(r"^\d{5,}$", segs[-1]):
            return platform, segs[-1]
        return None, ""
    return None, ""


def _season_value(structure, scope, url_entry):
    """The SEASON cell per the table's conventions."""
    kind = (structure or {}).get("kind") or "other"
    lbl = _clean((url_entry or {}).get("season_label"))
    if kind == "series":
        m = re.match(r"^season\s+(\d{1,2})$", lbl, re.IGNORECASE)
        if m:
            return f"Season {int(m.group(1))}"
        if isinstance(scope, dict) and scope.get("season"):
            return f"Season {int(scope['season'])}"
        return ""
    if kind == "franchise_film":
        if isinstance(scope, dict) and scope.get("film"):
            return _strip_apostrophes(_clean(scope["film"]))[:80]
        note = _strip_apostrophes(_clean((url_entry or {}).get("note")))
        if note:
            return note[:80]
        return ""
    if kind in ("film", "special"):
        return "Movie" if kind == "film" else ""
    return ""


def build_content_mapping_rows(structure, scope, urls_doc):
    """Rows ready for reference.content_mapping, formatted exactly
    like the existing rows. URLs that don't fit a platform's observed
    convention, or series URLs whose season can't be resolved, are
    skipped (they still ride BRAND INPUT)."""
    rows = []
    if not isinstance(urls_doc, dict) or urls_doc.get("research_failed"):
        return rows
    kind = (structure or {}).get("kind") or "other"
    show = _strip_apostrophes(_clean(
        (structure or {}).get("franchise")
        if kind == "franchise_film"
        and _clean((structure or {}).get("franchise"))
        else (structure or {}).get("title")))[:120]
    if not show:
        return rows
    production = _strip_apostrophes(_clean(
        (structure or {}).get("production")))[:80]
    seen = set()
    for u in (urls_doc.get("urls") or []):
        platform, fragment = content_mapping_fragment(u.get("url"))
        if not platform or not fragment:
            continue
        season = _season_value(structure, scope, u)
        if kind == "series" and not season:
            # A series row without a season would deviate from the
            # table's convention - skip rather than guess.
            continue
        k = (norm_token(platform), norm_token(fragment))
        if k in seen:
            continue
        seen.add(k)
        rows.append({
            "SHOW": show,
            "URL": fragment,
            "PRODUCTION": production,
            "PLATFORM": platform,
            "SEASON": season,
            "CATEGORY": "",
            "SUB_CATEGORY": "",
        })
    return rows


# ---------------------------------------------------------------------------
# ClickHouse insert (idempotent, insert-only, fail-open to S3 queue)
# ---------------------------------------------------------------------------

def _default_connect():
    try:
        from migration.clickhouse_connector import connect_clickhouse
    except ImportError:
        from clickhouse_connector import connect_clickhouse  # type: ignore
    return connect_clickhouse()


def _sql_quote(v):
    return "'" + str(v or "").replace("\\", "\\\\").replace("'", "\\'") + "'"


def insert_content_mapping_rows(rows, run_id="", connect_fn=None,
                                s3_client=None, queue_on_failure=True):
    """Insert new rows into reference.content_mapping. Insert-only:
    existing rows are never modified or deleted. Dedupe is case +
    punctuation insensitive on (PLATFORM, URL) plus (SHOW, PLATFORM,
    SEASON, URL). On connection failure the rows queue to S3 for
    retry and the caller proceeds (never blocks a build).

    Returns {'inserted': n, 'duplicates': n, 'queued': bool,
             'error': str|None}."""
    out = {"inserted": 0, "duplicates": 0, "queued": False, "error": None}
    rows = [r for r in (rows or [])
            if isinstance(r, dict) and r.get("SHOW") and r.get("URL")
            and r.get("PLATFORM")]
    if not rows:
        return out
    connect = connect_fn or _default_connect
    try:
        conn = connect()
    except Exception as e:
        out["error"] = f"connect failed: {e}"
        if queue_on_failure:
            out["queued"] = queue_pending_content_mapping(
                rows, str(e), s3_client=s3_client, run_id=run_id)
        print(f"[{run_id}] content_mapping insert deferred "
              f"({out['error']}; queued={out['queued']})")
        return out
    try:
        cur = conn.cursor()
        shows = sorted({r["SHOW"] for r in rows})
        urls = sorted({r["URL"] for r in rows})
        cur.execute(
            f"SELECT SHOW, URL, PLATFORM, SEASON FROM "
            f"{CONTENT_MAPPING_TABLE} WHERE SHOW IN "
            f"({', '.join(_sql_quote(s) for s in shows)}) "
            f"OR URL IN ({', '.join(_sql_quote(u) for u in urls)})")
        existing = set()
        for got in (cur.fetchall() or []):
            g_show, g_url, g_plat, g_season = (list(got) + ["", "", "", ""])[:4]
            existing.add((norm_token(g_plat), norm_token(g_url)))
            existing.add((norm_token(g_show), norm_token(g_plat),
                          norm_token(g_season), norm_token(g_url)))
        to_insert = []
        for r in rows:
            k2 = (norm_token(r["PLATFORM"]), norm_token(r["URL"]))
            k4 = (norm_token(r["SHOW"]), norm_token(r["PLATFORM"]),
                  norm_token(r.get("SEASON")), norm_token(r["URL"]))
            if k2 in existing or k4 in existing:
                out["duplicates"] += 1
                continue
            existing.add(k2)
            existing.add(k4)
            to_insert.append(r)
        if to_insert:
            values = ", ".join(
                "(" + ", ".join(_sql_quote(r.get(c))
                                for c in CONTENT_MAPPING_COLUMNS) + ")"
                for r in to_insert)
            cur.execute(
                f"INSERT INTO {CONTENT_MAPPING_TABLE} "
                f"({', '.join(CONTENT_MAPPING_COLUMNS)}) VALUES {values}")
            out["inserted"] = len(to_insert)
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        print(f"[{run_id}] content_mapping: {out['inserted']} row(s) "
              f"inserted, {out['duplicates']} already present")
        return out
    except Exception as e:
        out["error"] = f"insert failed: {e}"
        try:
            conn.close()
        except Exception:
            pass
        if queue_on_failure:
            out["queued"] = queue_pending_content_mapping(
                rows, str(e), s3_client=s3_client, run_id=run_id)
        print(f"[{run_id}] content_mapping insert deferred "
              f"({out['error']}; queued={out['queued']})")
        return out


def queue_pending_content_mapping(rows, reason, s3_client=None, run_id=""):
    """Persist rows that couldn't reach ClickHouse to an S3 sidecar so
    the next build (or a manual flush) can retry them."""
    try:
        payload = {
            "rows": rows,
            "reason": _clean(reason)[:300],
            "run_id": run_id,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        blob = json.dumps(payload, indent=1).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()[:16]
        key = f"{PENDING_PREFIX}{digest}.json"
        _s3(s3_client).put_object(Bucket=BUCKET, Key=key, Body=blob,
                                  ContentType="application/json")
        return True
    except Exception as e:
        print(f"[{run_id}] content_mapping pending-queue write failed: {e}")
        return False


def flush_pending_content_mapping(connect_fn=None, s3_client=None,
                                  run_id="", max_files=20):
    """Opportunistic retry of queued inserts. Deletes a sidecar only
    after its rows inserted (or deduped) successfully. Never raises."""
    flushed = 0
    try:
        s3c = _s3(s3_client)
        resp = s3c.list_objects_v2(Bucket=BUCKET, Prefix=PENDING_PREFIX,
                                   MaxKeys=max_files)
        for item in (resp.get("Contents") or []):
            key = item.get("Key") or ""
            if not key.endswith(".json"):
                continue
            try:
                body = s3c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
                payload = json.loads(body.decode("utf-8"))
                rows = payload.get("rows") or []
            except Exception:
                continue
            res = insert_content_mapping_rows(
                rows, run_id=run_id, connect_fn=connect_fn,
                s3_client=s3_client, queue_on_failure=False)
            if res.get("error"):
                # ClickHouse still unreachable; leave the sidecar.
                break
            try:
                s3c.delete_object(Bucket=BUCKET, Key=key)
                flushed += 1
            except Exception:
                pass
    except Exception as e:
        print(f"[{run_id}] pending content_mapping flush skipped: {e}")
    return flushed


# ---------------------------------------------------------------------------
# Worker hook
# ---------------------------------------------------------------------------

def _normalize_brand_input_url(u):
    try:
        from migration.viewer_carriage import normalize_content_url
    except ImportError:
        from viewer_carriage import normalize_content_url  # type: ignore
    return normalize_content_url(u)


def ensure_scope_resolved(spec, run_id="", extra_texts=(), s3_client=None,
                          connect_fn=None):
    """Pre-build hook (worker): resolve the season/film scope for a
    consumption-scoped universe, research the scoped content URLs,
    stamp them on the spec (spec['viewer_scope'] +
    spec['viewer_scope_urls']) and insert the verified rows into
    reference.content_mapping. Cache-first, idempotent, never raises,
    never blocks a build. Cuts never reach this (the caller only runs
    it on fresh builds; ' - ' subjects are skipped by detection)."""
    try:
        try:
            from migration.viewer_carriage import detect_consumption_scoped
        except ImportError:
            from viewer_carriage import (  # type: ignore
                detect_consumption_scoped,
            )
        subject = (spec.get("name") or spec.get("subject")
                   or spec.get("display_name") or "")
        det = detect_consumption_scoped(subject)
        vs_in = spec.get("viewer_scope") \
            if isinstance(spec.get("viewer_scope"), dict) else {}
        if not det and not vs_in:
            return None
        title_hint = (vs_in.get("title") or "").strip() \
            or (det["title_hint"] if det else "")
        if not title_hint:
            return None
        # Structure: prefer the doc the chatbot already resolved (its
        # season/film lists ride viewer_scope_data -> viewer_scope).
        structure = None
        if vs_in.get("kind") in VALID_KINDS and (
                vs_in.get("seasons") or vs_in.get("films")
                or vs_in.get("kind") in ("film", "special", "other")):
            structure = {
                "content_structure": True,
                "title": title_hint,
                "kind": vs_in.get("kind"),
                "franchise": vs_in.get("franchise") or "",
                "production": vs_in.get("production") or "",
                "seasons": vs_in.get("seasons") or [],
                "films": vs_in.get("films") or [],
                "confident": True,
                "research_failed": False,
            }
        if structure is None:
            structure = ensure_structure(title_hint, s3_client=s3_client,
                                         run_id=run_id)
        if structure.get("research_failed") \
                or structure.get("kind") not in ("series", "franchise_film",
                                                 "film", "special"):
            return None
        # Scope: the chatbot's bound scope wins; otherwise parse the
        # subject + prompt texts; otherwise 'all'.
        if vs_in.get("mode") in ("all", "latest", "specific"):
            req = vs_in
        else:
            texts = " ".join([str(subject)] + [str(t) for t in extra_texts
                                               if t])
            req = scope_from_text(texts, structure) or {"mode": "all"}
        scope = resolve_scope(structure, req)
        spec["viewer_scope"] = {
            "mode": scope["mode"],
            "season": scope.get("season"),
            "season_year": scope.get("season_year") or "",
            "film": scope.get("film") or "",
            "film_year": scope.get("film_year") or "",
            "label": scope.get("label") or "",
            "title": structure.get("title") or title_hint,
            "kind": structure.get("kind"),
            "assumed": bool(vs_in.get("assumed")),
        }
        # Retry any earlier deferred inserts while we're here.
        try:
            flush_pending_content_mapping(connect_fn=connect_fn,
                                          s3_client=s3_client, run_id=run_id)
        except Exception:
            pass
        urls_doc = ensure_scope_urls(
            subject, structure, scope,
            carriage_doc=spec.get("carriage_doc"),
            s3_client=s3_client, run_id=run_id)
        scoped_urls = []
        for u in (urls_doc.get("urls") or []):
            nu = _normalize_brand_input_url(u.get("url"))
            if nu and nu not in scoped_urls:
                scoped_urls.append(nu)
            if len(scoped_urls) >= BRAND_INPUT_URL_CAP:
                break
        if scoped_urls:
            spec["viewer_scope_urls"] = scoped_urls
            # Attach scoped URLs to the matching carriage carriers so
            # every downstream reader (enforcers, ship gate) sees the
            # same URL facts. Never invents a carrier.
            cdoc = spec.get("carriage_doc")
            if isinstance(cdoc, dict) and cdoc.get("carriers"):
                by_host = {}
                for nu in scoped_urls:
                    host = nu.split("/", 1)[0]
                    plat = PLATFORM_BY_DOMAIN.get(host)
                    if plat:
                        by_host.setdefault(norm_token(plat), []).append(nu)
                try:
                    from migration.viewer_carriage import carrier_matches_row
                except ImportError:
                    from viewer_carriage import (  # type: ignore
                        carrier_matches_row,
                    )
                for c in cdoc["carriers"]:
                    urls_now = list(c.get("content_urls") or [])
                    for plat_key, plat_urls in by_host.items():
                        if not carrier_matches_row(c.get("platform"),
                                                   plat_key) \
                                and norm_token(c.get("platform")) != plat_key:
                            continue
                        for nu in plat_urls:
                            if nu not in urls_now:
                                urls_now.append(nu)
                    c["content_urls"] = urls_now[:BRAND_INPUT_URL_CAP]
        rows = build_content_mapping_rows(structure, scope, urls_doc)
        if rows:
            res = insert_content_mapping_rows(
                rows, run_id=run_id, connect_fn=connect_fn,
                s3_client=s3_client)
            spec["viewer_scope"]["content_mapping"] = {
                "rows_built": len(rows),
                "inserted": res.get("inserted", 0),
                "duplicates": res.get("duplicates", 0),
                "queued": bool(res.get("queued")),
            }
        else:
            spec["viewer_scope"]["content_mapping"] = {
                "rows_built": 0, "inserted": 0, "duplicates": 0,
                "queued": False,
            }
        print(f"[{run_id}] viewer scope resolved for {subject!r}: "
              f"{spec['viewer_scope']['label'] or 'all'} "
              f"({len(scoped_urls)} scoped URL(s), "
              f"{spec['viewer_scope']['content_mapping']['inserted']} "
              f"mapping row(s) inserted)")
        return spec["viewer_scope"]
    except Exception as e:
        print(f"[{run_id}] ensure_scope_resolved failed (non-fatal): {e}")
        return None
