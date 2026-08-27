"""Viewer-carriage resolution for consumption-scoped universes.

2026-08-26 (Jenna, verbatim): "we need to tighten up the viewers
pipleine. a couple of days ago I pulled jimmy kimmel live viewers and
rosie hosted viewers and you can ony watch it online on disney+ or hulu
so Disney+/Hulu should have been 100%. you dont need to update those
files but it has to be smart enough to catch that in the future."

Same day, scope addition (Jenna, verbatim): "for viewers it also needs
to rip the urls of the content in brand input if it can find them, if
not it can list csv"

THE PRINCIPLE: a consumption-scoped universe ("viewers of X",
"watched X", "X hosted viewers", "streamed X") is DEFINED by having
consumed the title. Our measurement is digital clickstream, so every
member consumed it on some digital platform: the platforms that
actually carry the title must jointly account for ~100% of the
universe. Exclusive carrier -> that service reads ~100 (messy, never
exactly 100). Multi-carrier -> the union is ~100 with a reasoned split
(each carrier individually high; overlap allowed). Non-carrying
streamers keep their organic usage untouched - the constraint elevates
carriers, it never suppresses others.

This module owns:
  * detection  - detect_consumption_scoped(subject, *texts)
  * research   - research_carriage(...) via the Claude web-search
                 pattern (event_window precedent), returning carriers
                 with exclusive/shared status AND the content URLs on
                 each carrier (full-episode/title pages only; clips do
                 not make a platform a carrier)
  * caching    - S3 sidecar at system/viewer_carriage/<NORM>.json so
                 the build is reproducible and the enforcer + final
                 ship gate + writer autofix all read the same facts
  * spec hook  - ensure_carriage_resolved(spec) for the worker/engine
  * prompt ctx - carriage_reasoning_context(doc) injected into the
                 row-by-row persona brief (reasoning first,
                 enforcement second)
  * BRAND INPUT- brand_input_urls(doc) -> normalized content URL slugs
                 (domain + path, no protocol, no tracking params); the
                 engine folds them into the Value. When research
                 cannot confidently find content URLs the Value ships
                 the literal "CSV" (Jenna 2026-08-26).

On research failure the build proceeds WITHOUT the constraint (never
block a build on research) but the failure is logged and the doc is
cached with research_failed=True so BRAND INPUT falls back to "CSV".

Derived cuts never run this step: they inherit the parent's rows and
BRAND INPUT verbatim.

Twin-sync note: this module lives in BOTH the parent repo
(`migration/viewer_carriage.py`) and
`bg-webapp/migration/viewer_carriage.py`. Keep the two byte-equal
(scripts/test_module_twin_sync.py enforces it).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone

BUCKET = "dashboard-inputs"
CACHE_PREFIX = "system/viewer_carriage/"
CACHE_TTL_DAYS = 30

# Streaming categories the carriage constraint touches. STREAMING
# VIDEO is the legacy alias of STREAMING/PLATFORM; both are kept
# consistent. vMVPD categories participate only when a carrier is
# researched as a vmvpd/live-tv service.
STREAMING_ALIAS_CATS = ("STREAMING/PLATFORM", "STREAMING VIDEO")
VMVPD_CATS = ("VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD/FAST",
              "VMVPD")

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
# Consumption vocabulary tied to a specific title. Conservative on
# purpose: brand / person / persona universes must never fire. The
# qualifier must be a TRAILING audience noun ("Jimmy Kimmel Live
# Viewers", "Rosie Hosted Viewers", "Yellowstone Watchers") or a
# "viewers of X" / "watched X" head form.
_TRAILING_Q_RE = re.compile(
    r"^(?P<title>.+?)[\s_]+(?P<q>viewers?|watchers?|streamers?|bingers?|"
    r"binge[\s-]?watchers?)$",
    re.IGNORECASE,
)
_HEAD_Q_RE = re.compile(
    r"^(?:people\s+who\s+|those\s+who\s+|audience\s+(?:of|who)\s+)?"
    r"(?P<q>viewers\s+of|watchers\s+of|watched|streamed|binged)\s+"
    r"(?P<title>.+)$",
    re.IGNORECASE,
)
# Words that alone are never a title (a bare persona label like
# "Binge Watchers" or "Streamers" must not fire).
_NON_TITLE_WORDS = {
    "the", "a", "an", "tv", "movie", "show", "series", "video",
    "content", "avid", "casual", "super", "heavy", "light",
    # Qualifier stems: a bare persona label like "Binge Watchers" or
    # "Stream Viewers" carries no title and must not fire.
    "binge", "watch", "watcher", "watchers", "stream", "streamer",
    "streamers", "view", "viewer", "viewers", "binger", "bingers",
    "fan", "fans", "audience",
}


def _clean(s):
    return " ".join(str(s or "").split()).strip()


def norm_token(s):
    """Case + punctuation insensitive normalization (matches the ship
    gate's _norm_token semantics)."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def detect_consumption_scoped(subject, *extra_texts):
    """Return {'qualifier': ..., 'title_hint': ...} when the subject
    names a consumption-scoped (viewers) universe tied to a title,
    else None.

    Only the SUBJECT decides; extra_texts (universe notes, prompt) are
    used solely to enrich the title hint, never to fire detection on
    their own - a prompt mentioning "viewers" for a brand universe
    must not trip this.
    """
    subj = _clean(subject)
    if not subj or " - " in subj:
        # '{Subject} - {Cut}' names are derived cuts; cuts inherit
        # from their parent and never run carriage themselves.
        return None
    m = _TRAILING_Q_RE.match(subj)
    title = ""
    qual = ""
    if m:
        title = _clean(m.group("title"))
        qual = m.group("q").lower()
    else:
        m = _HEAD_Q_RE.match(subj)
        if m:
            title = _clean(m.group("title"))
            qual = m.group("q").lower()
    if not title or not qual:
        return None
    # The remaining title must contain at least one real word that is
    # not itself audience/qualifier vocabulary.
    toks = [t for t in re.split(r"[^A-Za-z0-9+&']+", title) if t]
    real = [t for t in toks
            if t.lower() not in _NON_TITLE_WORDS
            and not _TRAILING_Q_RE.match(t)]
    if not real:
        return None
    hint = title
    for t in extra_texts:
        t = _clean(t)
        if t and t.lower() != subj.lower() and len(t) < 300:
            hint = f"{hint} (context: {t})"
            break
    return {"qualifier": qual, "title_hint": title, "research_hint": hint}


# ---------------------------------------------------------------------------
# URL normalization (clickstream form: domain + path, no protocol,
# no tracking params, no fragment)
# ---------------------------------------------------------------------------

# Path segments that name a platform's generic landing surface, not a
# specific title. A one-segment path made of one of these qualifies
# EVERY visitor of the platform into the universe (2026-08-26 Liz QA:
# fubo.tv/welcome shipped in Paw Patrol Series Viewers' BRAND INPUT).
# Deep paths under these prefixes stay valid: fubo's real series pages
# live at fubo.tv/welcome/series/<id>/<slug>.
GENERIC_LANDING_SEGMENTS = {
    "welcome", "home", "browse", "signup", "sign-up", "signin",
    "sign-in", "login", "start", "live", "watch", "shows", "movies",
    "series", "tv", "stream", "streaming", "app", "apps", "account",
    "plans", "pricing", "deals", "offers", "gift", "help", "search",
    "explore", "discover", "landing", "index", "default", "player",
    "video", "videos", "channel", "channels", "program", "programs",
    "schedule", "guide", "new", "popular", "trending",
}

# Streaming / carriage platform domains where a bare or generic URL in
# a BRAND INPUT is always the landing-page defect (a brand's own
# domain root can be a legitimate owned-site slug; a carrier's cannot).
PLATFORM_DOMAINS = {
    "netflix.com", "fubo.tv", "philo.com", "hulu.com",
    "disneyplus.com", "paramountplus.com", "peacocktv.com", "max.com",
    "hbomax.com", "appletv.com", "tv.apple.com", "primevideo.com",
    "amazon.com", "youtube.com", "youtubetv.com", "tv.youtube.com",
    "tubi.tv", "tubitv.com", "pluto.tv", "plutotv.com", "roku.com",
    "therokuchannel.roku.com", "sling.com", "vudu.com",
    "fandango.com", "fandangonow.com", "crackle.com", "freevee.com",
    "starz.com", "showtime.com", "mgmplus.com", "amcplus.com",
    "discoveryplus.com", "crunchyroll.com", "espn.com",
    "plus.espn.com", "fox.com", "abc.com", "nbc.com", "cbs.com",
    "paramount.com", "britbox.com", "acorn.tv", "shudder.com",
    "hallmarkmovies.com", "hallmarkplus.com",
}


def _split_host_path(u):
    s = str(u or "").strip()
    if not s:
        return "", []
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s, flags=re.IGNORECASE)
    s = s.split("#", 1)[0].split("?", 1)[0].strip().lstrip("/")
    host, _, path = s.partition("/")
    host = re.sub(r"^www\.", "", host.strip().lower())
    segs = [p for p in re.sub(r"/{2,}", "/", path).strip("/").split("/")
            if p]
    return host, segs


# Sanctioned transaction-path prefixes (clickstream-slug rule 4c-i
# case 4, EST Buyers precedent): these prefix slugs identify PURCHASE
# and RENTAL behavior on a storefront and are the canonical way to
# scope EST / TVOD / digital-purchaser universes. They are never the
# landing-page defect even when their first segment ("movies") reads
# generic.
SANCTIONED_TRANSACTION_PREFIXES = {
    "amazon.com/gp/video/detail",
    "apple.com/itunes/movies",
    "vudu.com/movies",
    "fandangonow.com/details/movie",
    "play.google.com/store/movies/details",
    "microsoft.com/en-us/p",
    "youtube.com/movies",
    "youtube.com/paid_memberships",
}


def is_generic_landing_url(u, require_platform_domain=True):
    """True when a URL-ish token is a platform landing page rather than
    a specific title path: bare domain, or a path whose ONLY segment is
    a generic surface word (welcome / home / browse / ...). Deep paths
    (>= 2 segments) are never generic. With require_platform_domain
    (default), only known carriage-platform domains are flagged so a
    brand's own site root never false-positives."""
    host, segs = _split_host_path(u)
    if not host or "." not in host or " " in host:
        return False
    normalized = "/".join([host] + segs)
    for pref in SANCTIONED_TRANSACTION_PREFIXES:
        if normalized == pref or normalized.startswith(pref + "/"):
            return False
    if require_platform_domain and host not in PLATFORM_DOMAINS:
        return False
    if len(segs) >= 2:
        return False
    if not segs:
        return True
    return segs[0].lower() in GENERIC_LANDING_SEGMENTS


def normalize_content_url(u):
    """'https://www.hulu.com/series/x?utm=1#t' -> 'hulu.com/series/x'.
    Returns '' when the value does not look like a real content URL."""
    s = str(u or "").strip()
    if not s:
        return ""
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s, flags=re.IGNORECASE)
    s = s.split("#", 1)[0].split("?", 1)[0].strip()
    s = s.lstrip("/")
    if not s:
        return ""
    host, _, path = s.partition("/")
    host = host.strip().lower()
    host = re.sub(r"^www\.", "", host)
    if "." not in host or " " in host:
        return ""
    path = re.sub(r"/{2,}", "/", path).strip().rstrip("/")
    # A bare domain is not a CONTENT url (that would be the platform
    # itself, which is a mass signal, not a title page).
    if not path:
        return ""
    # 2026-08-26 (Liz QA, Paw Patrol): a generic landing surface is
    # not a CONTENT url either - fubo.tv/welcome qualifies every Fubo
    # visitor. Specific deep paths (fubo.tv/welcome/series/<id>/<slug>)
    # pass. Platform-domain gating off here: anything reaching this
    # normalizer is already claimed as a content URL by research.
    if is_generic_landing_url(f"{host}/{path}",
                              require_platform_domain=False):
        return ""
    return f"{host}/{path}"


# ---------------------------------------------------------------------------
# Doc shape + validation
# ---------------------------------------------------------------------------

def _canon_platform(name):
    return _clean(name)


def normalize_carriage_doc(doc, subject="", qualifier="", title_hint=""):
    """Coerce a raw research response into the canonical carriage doc.
    Returns None when the response carries no usable carriers."""
    if not isinstance(doc, dict):
        return None
    carriers_in = doc.get("carriers")
    if not isinstance(carriers_in, list):
        return None
    carriers = []
    seen = set()
    for c in carriers_in:
        if not isinstance(c, dict):
            continue
        plat = _canon_platform(c.get("platform"))
        if not plat:
            continue
        key = norm_token(plat)
        if not key or key in seen:
            continue
        seen.add(key)
        urls = []
        for u in (c.get("content_urls") or []):
            nu = normalize_content_url(u)
            if nu and nu not in urls:
                urls.append(nu)
        kind = _clean(c.get("kind")).lower() or "svod"
        carriers.append({
            "platform": plat,
            "kind": kind,
            "content_urls": urls[:4],
            "note": _clean(c.get("note"))[:200],
        })
    if not carriers:
        return None
    exclusive = len(carriers) == 1
    title = _clean(doc.get("show_title") or doc.get("title")) or title_hint
    return {
        "consumption_scoped": True,
        "subject": _clean(subject),
        "qualifier": qualifier,
        "title": title,
        "carriers": carriers[:5],
        "exclusive": exclusive,
        "clip_platforms": [
            _canon_platform(p) for p in (doc.get("clip_platforms") or [])
            if _canon_platform(p)][:8],
        "confident": bool(doc.get("confident", True)),
        "research_failed": False,
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def failed_carriage_doc(subject, qualifier, title_hint, reason):
    """Doc stamped when detection fired but research could not resolve
    carriage. The constraint is skipped and BRAND INPUT falls back to
    the literal "CSV" (Jenna 2026-08-26)."""
    return {
        "consumption_scoped": True,
        "subject": _clean(subject),
        "qualifier": qualifier,
        "title": title_hint,
        "carriers": [],
        "exclusive": False,
        "clip_platforms": [],
        "confident": False,
        "research_failed": True,
        "failure_reason": _clean(reason)[:300],
        "researched_at": datetime.now(timezone.utc).isoformat(),
    }


def doc_is_enforceable(doc):
    """True when the doc can drive the constraint: consumption-scoped,
    confident research, at least one carrier."""
    return bool(
        isinstance(doc, dict)
        and doc.get("consumption_scoped")
        and not doc.get("research_failed")
        and doc.get("confident", True)
        and doc.get("carriers")
    )


def carrier_content_urls(doc):
    """All normalized content URLs across carriers, deduped, order
    preserved."""
    out = []
    if not isinstance(doc, dict):
        return out
    for c in (doc.get("carriers") or []):
        for u in (c.get("content_urls") or []):
            nu = normalize_content_url(u)
            if nu and nu not in out:
                out.append(nu)
    return out


# ---------------------------------------------------------------------------
# S3 cache
# ---------------------------------------------------------------------------

def _s3(s3_client=None):
    if s3_client is not None:
        return s3_client
    import boto3
    return boto3.client("s3", region_name="us-east-2")


def carriage_cache_key(subject):
    stem = str(subject or "").split(" - ")[0]
    n = norm_token(stem)
    return f"{CACHE_PREFIX}{n}.json" if n else ""


def load_cached_carriage(subject, s3_client=None, max_age_days=CACHE_TTL_DAYS,
                         verbose=False):
    """Cached doc for this subject (stem before ' - '), or None. Failed
    docs are NOT reused across runs beyond 1 day so a transient
    research outage doesn't pin a subject to CSV for a month."""
    key = carriage_cache_key(subject)
    if not key:
        return None
    try:
        body = _s3(s3_client).get_object(Bucket=BUCKET, Key=key)["Body"].read()
        doc = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or not doc.get("consumption_scoped"):
        return None
    age_limit = 1 if doc.get("research_failed") else max_age_days
    try:
        ts = datetime.fromisoformat(str(doc.get("researched_at")))
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        if age_days > age_limit:
            return None
    except Exception:
        return None
    if verbose:
        print(f"[carriage] cache hit for {subject!r} ({key})")
    return doc


def save_carriage_cache(subject, doc, s3_client=None, verbose=False):
    key = carriage_cache_key(subject)
    if not key or not isinstance(doc, dict):
        return False
    try:
        _s3(s3_client).put_object(
            Bucket=BUCKET, Key=key,
            Body=json.dumps(doc, indent=1).encode("utf-8"),
            ContentType="application/json")
        if verbose:
            print(f"[carriage] cached doc for {subject!r} -> {key}")
        return True
    except Exception as e:
        print(f"[carriage] cache write failed for {subject!r}: {e}")
        return False


# ---------------------------------------------------------------------------
# Research (Claude + web search, event_window pattern)
# ---------------------------------------------------------------------------

def research_carriage(subject, title_hint, qualifier="viewers",
                      window_label="", run_id=""):
    """One live web-research call: where can this title be watched
    digitally in the US in the build window? Full episodes vs clips
    distinguished. Also returns the content URL(s) on each carrier for
    BRAND INPUT (Jenna 2026-08-26 scope addition). Returns a canonical
    carriage doc (possibly research_failed=True). Never raises."""
    subj = _clean(subject)
    hint = _clean(title_hint) or subj
    try:
        from migration.claude_client import claude_messages
    except ImportError:
        try:
            from claude_client import claude_messages  # twin layout
        except ImportError:
            return failed_carriage_doc(subj, qualifier, hint,
                                       "claude client unavailable")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    window = _clean(window_label) or "the trailing 12 months"
    system = (
        "You are resolving the DIGITAL CARRIAGE of a TV show, movie, or "
        "streaming title for a US audience: on which digital services "
        "can a US viewer actually watch it (full episodes / the full "
        "title), and at what URL. Use the web_search tool and "
        "cross-check sources.\n"
        "Rules:\n"
        "1. FULL EPISODES ONLY define carriage. Clip channels (YouTube "
        "clips, TikTok, social highlights) are NOT carriers - list "
        "them under clip_platforms instead.\n"
        "2. If the request names a host, guest, era, or episode scope "
        "of a show (e.g. 'Rosie hosted viewers' meaning Rosie "
        "O'Donnell's guest-hosted Jimmy Kimmel Live episodes), resolve "
        "to the UNDERLYING SHOW's carriage.\n"
        "3. US availability in the stated window only.\n"
        "4. A network's own site/app that streams full episodes counts "
        "as a carrier. Live-TV-only paths (cable, antenna) do not - "
        "this is digital clickstream measurement.\n"
        "5. content_urls: the title's page URL on each carrier, as a "
        "clickstream slug: domain + path, no protocol, no www, no "
        "tracking parameters (e.g. hulu.com/series/jimmy-kimmel-live). "
        "Only include URLs you actually verified; an empty list is "
        "better than a guessed URL.\n"
        "6. Note that Hulu content is also watchable inside Disney+ "
        "(the Hulu-on-Disney+ tile); list both services when both "
        "genuinely offer the full episodes.\n"
        "Respond with ONE JSON object and nothing else:\n"
        '{"show_title": "<the underlying title>",\n'
        ' "carriers": [{"platform": "<service name>",\n'
        '               "kind": "svod|avod|vmvpd|network_app",\n'
        '               "content_urls": ["<domain/path>", ...],\n'
        '               "note": "<short>"}],\n'
        ' "clip_platforms": ["YouTube", ...],\n'
        ' "confident": true|false}\n'
        "Set confident to false when you cannot verify carriage from "
        "real sources. Never invent platforms or URLs. Plain hyphens "
        "only, never em dashes."
    )
    user = (f"Audience universe: {subj!r} (consumption qualifier: "
            f"{qualifier}).\n"
            f"Title to resolve: {hint}\n"
            f"Window: {window}. Today is {today}.\n"
            f"Where can a US viewer watch the full episodes/title "
            f"digitally in that window, and at what URLs? JSON only.")
    model = (os.environ.get("CLAUDE_CARRIAGE_MODEL")
             or "claude-sonnet-4-6")
    web_tool = {"type": "web_search_20260209", "name": "web_search",
                "max_uses": 8}
    web_tool_legacy = {"type": "web_search_20250305", "name": "web_search",
                       "max_uses": 8}
    raw = None
    try:
        raw = claude_messages(system=system, user=user, model=model,
                              max_tokens=1400, temperature=0.1,
                              tools=[web_tool])
        if not raw:
            print(f"[{run_id}] carriage research: retrying with legacy "
                  f"web-search descriptor")
            raw = claude_messages(system=system, user=user,
                                  model="claude-sonnet-4-6",
                                  max_tokens=1400, temperature=0.1,
                                  tools=[web_tool_legacy])
    except Exception as e:
        print(f"[{run_id}] carriage research raised for {subj!r}: {e}")
        return failed_carriage_doc(subj, qualifier, hint, str(e))
    if not raw:
        return failed_carriage_doc(subj, qualifier, hint, "empty response")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    i, j = cleaned.find("{"), cleaned.rfind("}")
    if i < 0 or j <= i:
        return failed_carriage_doc(subj, qualifier, hint, "no JSON in response")
    try:
        data = json.loads(cleaned[i:j + 1])
    except Exception as e:
        return failed_carriage_doc(subj, qualifier, hint, f"bad JSON: {e}")
    if data.get("confident") is False:
        print(f"[{run_id}] carriage research not confident for {subj!r}; "
              f"proceeding without the constraint")
        return failed_carriage_doc(subj, qualifier, hint,
                                   "research not confident")
    doc = normalize_carriage_doc(data, subject=subj, qualifier=qualifier,
                                 title_hint=hint)
    if doc is None:
        return failed_carriage_doc(subj, qualifier, hint,
                                   "no usable carriers in response")
    print(f"[{run_id}] carriage resolved for {subj!r}: "
          f"{', '.join(c['platform'] for c in doc['carriers'])} "
          f"({'exclusive' if doc['exclusive'] else 'shared'}); "
          f"{len(carrier_content_urls(doc))} content URL(s)")
    return doc


def research_enabled():
    """Live research default-ON; VIEWER_CARRIAGE_RESEARCH=0 disables
    (tests, cost-sensitive backfills)."""
    return os.environ.get("VIEWER_CARRIAGE_RESEARCH", "1") != "0"


def ensure_carriage_resolved(spec, run_id="", extra_texts=(),
                             s3_client=None):
    """Pre-build hook (worker + engine): when the spec names a
    consumption-scoped universe and carries no carriage_doc yet,
    resolve one (cache first, then live research) and stamp it onto
    spec['carriage_doc']. Idempotent; never raises; never blocks a
    build."""
    try:
        existing = spec.get("carriage_doc")
        if isinstance(existing, dict) and existing.get("consumption_scoped"):
            return existing
        subject = (spec.get("name") or spec.get("subject")
                   or spec.get("display_name") or "")
        texts = list(extra_texts) + [
            spec.get("universe_note"), spec.get("persona_notes"),
        ]
        det = detect_consumption_scoped(subject, *[t for t in texts if t])
        if not det:
            return None
        cached = load_cached_carriage(subject, s3_client=s3_client,
                                      verbose=True)
        if cached is not None:
            spec["carriage_doc"] = cached
            return cached
        if not research_enabled():
            print(f"[{run_id}] carriage research disabled by env; "
                  f"skipping for {subject!r}")
            return None
        doc = research_carriage(
            subject, det["research_hint"], qualifier=det["qualifier"],
            window_label=spec.get("date_window_label")
            or spec.get("date_range") or "", run_id=run_id)
        spec["carriage_doc"] = doc
        save_carriage_cache(subject, doc, s3_client=s3_client, verbose=True)
        return doc
    except Exception as e:
        print(f"[{run_id}] ensure_carriage_resolved failed (non-fatal): {e}")
        return None


# ---------------------------------------------------------------------------
# Reasoning-context injection (reasoning first, enforcement second)
# ---------------------------------------------------------------------------

def carriage_reasoning_context(doc):
    """Text block appended to the row-by-row persona brief so the
    streaming-category values are born correct. '' when the doc cannot
    drive the constraint."""
    if not doc_is_enforceable(doc):
        return ""
    carriers = doc.get("carriers") or []
    title = doc.get("title") or doc.get("subject") or "this title"
    names = [c["platform"] for c in carriers]
    lines = [
        "",
        "CONSUMPTION-SCOPED CARRIAGE FACTS (verified for this build):",
        f"This universe is defined by having WATCHED {title}. Our "
        f"measurement is digital clickstream, so every member watched "
        f"it on a digital service that actually carries the full "
        f"episodes/title.",
    ]
    if len(names) == 1:
        lines.append(
            f"{names[0]} is the ONLY digital service carrying "
            f"{title} in the window. In STREAMING/PLATFORM and "
            f"STREAMING VIDEO, the {names[0]} row must read near-total "
            f"(~99.9x, a messy 4-decimal value, never exactly 100).")
    else:
        lines.append(
            f"The full episodes are carried by: {', '.join(names)}. "
            f"Together these services must account for ~100% of this "
            f"universe in STREAMING/PLATFORM / STREAMING VIDEO: reason "
            f"a realistic split where each carrier reads HIGH (the two "
            f"may overlap; their sum should land at or above 100) and "
            f"no two carriers share the same 4-decimal value.")
    clips = doc.get("clip_platforms") or []
    if clips:
        lines.append(
            f"Clips-only platforms ({', '.join(clips)}) are NOT "
            f"carriers: score them on organic usage like any other "
            f"platform.")
    lines.append(
        "Non-carrying streamers keep normal organic usage levels - do "
        "NOT suppress them; this audience still holds Netflix/other "
        "subscriptions at plausible rates.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Carrier row matching (alias-aware, self-contained)
# ---------------------------------------------------------------------------

def carrier_matches_row(carrier_platform, row_value):
    """True when a profile row names this carrier, alias-aware:
    'Hulu' matches 'Disney+/Hulu' (compound rows treat '/', '+',
    '&', ',' as separators, with the Disney+/ESPN+/Paramount+ '+'
    kept inside the token)."""
    cn = norm_token(carrier_platform)
    rv = str(row_value or "").strip()
    if not cn or not rv:
        return False
    if norm_token(rv) == cn:
        return True
    # Component split: '/', '&', ',', ' + ' are separators; a trailing
    # '+' glued to a word (Disney+, Paramount+) stays in the token.
    parts = re.split(r"\s*(?:/|&|,|\|)\s*|\s+\+\s+", rv)
    for p in parts:
        if norm_token(p) == cn:
            return True
        # 'Disney+' should match a 'Disney' component and vice versa
        if cn.rstrip("+") and norm_token(p).rstrip("+") == cn.rstrip("+"):
            return True
    return False


def salted_unit(subject, tag, lo, hi, decimals=4):
    """Deterministic subject-salted value in [lo, hi]."""
    h = hashlib.sha256(f"{subject}|{tag}|carriage".encode()).hexdigest()
    u = int(h[:10], 16) / float(0xFFFFFFFFFF)
    return round(lo + (hi - lo) * u, decimals)


def messy_near_total(subject, tag):
    """A messy ~100 target: 100 - eps, eps in [0.005, 0.065], never on
    a .XX00 boundary, never exactly 100."""
    eps = salted_unit(subject, tag, 0.005, 0.065)
    v = round(100.0 - eps, 4)
    if v >= 100.0:
        v = 99.9871
    # Avoid 2dp boundaries (x.xx00): nudge by a salted sub-jitter.
    if abs(v * 100 - round(v * 100)) < 1e-9:
        v = round(v - salted_unit(subject, tag + "|b", 0.0003, 0.0041), 4)
    return v
