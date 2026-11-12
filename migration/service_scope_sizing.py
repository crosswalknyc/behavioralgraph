"""The size of a build scoped to named service(s) is a rule, not a draft.

2026-11-11 (Jenna: "these errors cannot happen"). Three builds in three
days came out at a show's whole audience when the request named one
service: Georgie & Mandy's First Marriage on Paramount+ (6,117,270 and
6,340,150) and on HBO Max (6,045,319; 6,220,660 with Amazon), next to
1,285,455 for the settled Paramount+ profile. The interpret step
drafted each size by judgment, from a different whole each time
(4.2M, 6.2M, 8.2M).

The rule, enforced at the spec normalizer and again in the worker:

  * A build scoped to named service(s) is the title's whole audience
    times the share of its viewing on those services, taken at the low
    end. It never equals or exceeds the whole and sits under it by a
    real margin.
  * The whole is the first figure held for the title (first-held wins);
    a later draft never replaces it. It is a figure for a window, and a
    build on another window reads it scaled to its own.
  * The share comes from what the build already researched: the
    placements the content map holds per service, the services the
    carriage research confirmed, and the viewing we cannot place (the
    title's own network, anything off the map). A service that carries
    the title in full weighs more than one that carries a slice. The
    range runs from the share against every carrier we know of plus the
    viewing we cannot place (low) to the share against the verified
    carriers alone (high); the build ships in the bottom third of it,
    salted per title and service set. With nothing established the
    named services are read against one unnamed carrier and the
    unplaced viewing, which is the low end and never the whole.
  * A settled build of the same title on the same service(s) is the
    anchor: the same window lands on it exactly, another window scales
    from it, nothing is re-drafted.
  * Across a title's builds: a service set that contains another is at
    least as large; a service that carries a slice sits under one that
    carries the title in full; every one sits under the whole.

No table of shares and no fixed factor: every point inside a range is
salted (sha256 of the title and the service set), so two titles never
share a figure and a rerun lands on the same one.

The record lives at s3://dashboard-inputs/system/title_service_sizes.json
(ETag CAS). Silent: nothing a reader sees says any of this.

Twin: migration/service_scope_sizing.py == bg-webapp/migration/
service_scope_sizing.py (byte-equal). Regression:
scripts/test_service_scope_sizing.py.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timezone

STORE_BUCKET = "dashboard-inputs"
STORE_KEY = "system/title_service_sizes.json"
PANEL_TO_US = 32.99          # the fixed 10M panel scales to 329.9M US people
SAME_WINDOW_DAYS = 3         # two windows this close are one window

_VIEWER_TYPES = ("viewers", "viewer", "watchers", "streamers", "listeners")
_VIEWER_NAME_RX = re.compile(r"\b(?:viewers?|watchers?|streamers?|listeners?)\b", re.I)

# the ways one service is written (naming only; never a share)
_ALIASES = {
    "max": "hbomax", "hbo": "hbomax", "hbomax": "hbomax",
    "amazon": "amazon", "primevideo": "amazon", "amazonprimevideo": "amazon", "amazonprime": "amazon",
    "prime": "amazon", "amazonvideo": "amazon",
    "paramount": "paramount", "paramountplus": "paramount",
    "disney": "disney", "disneyplus": "disney",
    "appletv": "appletv", "appletvplus": "appletv",
    "peacock": "peacock", "peacocktv": "peacock",
    "mgm": "mgmplus", "mgmplus": "mgmplus",
    "hallmark": "hallmarkplus", "hallmarkplus": "hallmarkplus",
    "youtubetv": "youtubetv", "youtube": "youtube",
    "amcplus": "amcplus", "amc": "amcplus",
    "discoveryplus": "discoveryplus", "discovery": "discoveryplus",
}


def _fold(s) -> str:
    return "".join(ch for ch in str(s or "").lower().replace("+", " plus ") if ch.isalnum())


def canon_service(label) -> str:
    """One key per service however it is written ('Max' / 'HBO MAX',
    'Paramount Plus' / 'PARAMOUNT+', 'Prime Video' / 'Amazon'). A resale
    channel ('HBO Max Amazon Channel') is the service it resells."""
    f = _fold(label)
    if not f:
        return ""
    for tail in ("amazonchannel", "primevideochannel", "rokuchannel", "applechannel"):
        if f.endswith(tail) and len(f) > len(tail):
            f = f[: -len(tail)]
            break
    return _ALIASES.get(f, f)


def _unit(*parts) -> float:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _messy(subject, value) -> int:
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
    except ImportError:
        try:
            from _sample_size_jitter import ensure_messy_sample_size  # type: ignore
        except ImportError:
            def ensure_messy_sample_size(subj, v, **_kw):
                x = max(800, int(v or 0))
                if x % 10 == 0:
                    x += 1 + int(hashlib.md5(str(subj).encode()).hexdigest()[:2], 16) % 8
                return x
    return int(ensure_messy_sample_size(str(subject or "title"), int(value)))


def title_key(title) -> str:
    try:
        try:
            from migration.title_anchors import title_key as _tk
        except ImportError:
            from title_anchors import title_key as _tk  # type: ignore
        k = _tk(title)
        if k:
            return str(k)
    except Exception:
        pass
    return re.sub(r"[^A-Z0-9]+", "", str(title or "").upper())


# ------------------------------------------------------------------ the spec
def title_of(spec) -> str:
    if not isinstance(spec, dict):
        return ""
    vs = spec.get("viewer_scope") if isinstance(spec.get("viewer_scope"), dict) else {}
    doc = spec.get("carriage_doc") if isinstance(spec.get("carriage_doc"), dict) else {}
    return str(spec.get("content_show") or vs.get("title") or doc.get("title") or "").strip()


def services_of(spec, subject="") -> list:
    """The services the build is scoped to, as canonical keys, sorted:
    the platforms the client named, or a service the universe's own name
    carries after the audience noun ('... Viewers - HBO Max',
    '... Viewers (Paramount+)', '... Viewers on Hulu')."""
    out = []
    if not isinstance(spec, dict):
        return out

    def _add(v):
        k = canon_service(v)
        if k and k not in out:
            out.append(k)
    for v in (spec.get("content_seed_platforms_named") or []):
        _add(v)
    if not out:
        name = str(subject or spec.get("name") or spec.get("subject") or "")
        m = _VIEWER_NAME_RX.search(name)
        tail = name[m.end():] if m else ""
        tail = re.sub(r"^\s*(?:only\s+)?(?:on|-|\(|:)\s*", "", tail.strip(), flags=re.I).rstrip(") ")
        for part in re.split(r"\s*(?:&|,|\band\b|/)\s*", tail):
            f = _fold(part)
            if f in _ALIASES:
                _add(part)
    return sorted(out)


def window_of(spec):
    """(start, end) as dates from spec['date_range'] ('YYYY-MM-DD TO
    YYYY-MM-DD'); the trailing year when the spec names none."""
    try:
        m = re.findall(r"\d{4}-\d{2}-\d{2}", str((spec or {}).get("date_range") or ""))
        if len(m) >= 2:
            a, b = date.fromisoformat(m[0]), date.fromisoformat(m[1])
            if b > a:
                return a, b
    except Exception:
        pass
    end = datetime.now(timezone.utc).date()
    try:
        start = end.replace(year=end.year - 1)
    except ValueError:
        start = end.replace(year=end.year - 1, day=28)
    return start, end


def _days(win) -> int:
    try:
        if isinstance(win, dict):
            a, b = date.fromisoformat(str(win.get("start"))[:10]), date.fromisoformat(str(win.get("end"))[:10])
        else:
            a, b = win
        return max(1, (b - a).days)
    except Exception:
        return 365


def _same_window(a, b) -> bool:
    try:
        return all(abs((date.fromisoformat(str(a.get(k))[:10]) - date.fromisoformat(str(b.get(k))[:10])).days)
                   <= SAME_WINDOW_DAYS for k in ("start", "end"))
    except Exception:
        return False


def _win_dict(win) -> dict:
    a, b = win
    return {"start": a.isoformat(), "end": b.isoformat()}


def is_scoped(spec, subject="") -> bool:
    """A title's audience scoped to named service(s): a title on the
    spec, a viewers-type universe, at least one named service, and the
    title is not itself that service."""
    try:
        if not isinstance(spec, dict) or str(os.environ.get("SERVICE_SCOPE_SIZING", "1")).strip() == "0":
            return False
        title = title_of(spec)
        if not title:
            return False
        name = str(subject or spec.get("name") or "")
        viewers = (str(spec.get("audience_type") or "").strip().lower() in _VIEWER_TYPES
                   or str(spec.get("seed_source") or "").startswith("content_map")
                   or bool(spec.get("content_seed_urls")) or bool(_VIEWER_NAME_RX.search(name)))
        if not viewers:
            return False
        svcs = services_of(spec, subject)
        if not svcs:
            return False
        tk = canon_service(title)
        return tk not in svcs
    except Exception:
        return False


# --------------------------------------------------------------- the record
def _hermetic() -> bool:
    return str(os.environ.get("REGRESSION_TEST_MODE", "")).strip() == "1"


def load_record(title, s3=None) -> dict:
    """The title's record ({} when none, on any failure, and in a
    hermetic test run that injected no store)."""
    key = title_key(title)
    if not key or (_hermetic() and s3 is None):
        return {}
    try:
        try:
            from migration.s3_json_state import read_json_with_etag
        except ImportError:
            from s3_json_state import read_json_with_etag  # type: ignore
        obj, _etag = read_json_with_etag(STORE_BUCKET, STORE_KEY, s3=s3) if s3 is not None \
            else read_json_with_etag(STORE_BUCKET, STORE_KEY)
        rec = (obj or {}).get(key) if isinstance(obj, dict) else None
        return dict(rec) if isinstance(rec, dict) else {}
    except Exception as e:
        print(f"[service-scope] record read failed (non-fatal): {e}")
        return {}


def merge_record(rec, *, title="", whole=None, build=None, facts=None) -> dict:
    """Pure: the record after a build settles. First-held wins: a whole
    already on record is never replaced, and a service set already
    settled keeps its first build (a different window is added beside
    it; the first one stays the anchor)."""
    rec = dict(rec or {})
    if title and not rec.get("title"):
        rec["title"] = str(title)
    if isinstance(whole, dict) and int(whole.get("people") or 0) > 0 and not rec.get("whole"):
        rec["whole"] = {"people": int(whole["people"]), "window": dict(whole.get("window") or {}),
                        "set_at": whole.get("set_at") or _now(), "source_key": str(whole.get("source_key") or "")}
    if isinstance(build, dict) and int(build.get("people") or 0) > 0:
        builds = [dict(b) for b in (rec.get("builds") or []) if isinstance(b, dict)]
        svcs = sorted(canon_service(s) for s in (build.get("services") or []) if canon_service(s))
        win = dict(build.get("window") or {})
        dup = any(sorted(b.get("services") or []) == svcs and _same_window(b.get("window") or {}, win) for b in builds)
        if not dup:
            builds.append({"services": svcs, "people": int(build["people"]), "sample": int(build.get("sample") or 0),
                           "window": win, "s3_key": str(build.get("s3_key") or ""), "set_at": build.get("set_at") or _now()})
        rec["builds"] = builds
    if isinstance(facts, dict) and facts.get("placed"):
        rec["facts"] = {"placed": {str(k): int(v) for k, v in (facts.get("placed") or {}).items() if int(v or 0) > 0},
                        "research": sorted({canon_service(r) for r in (facts.get("research") or []) if canon_service(r)}),
                        "read_at": _now()}
    return rec


def save_record(title, *, whole=None, build=None, facts=None, s3=None) -> bool:
    key = title_key(title)
    if not key or (_hermetic() and s3 is None):
        return False
    try:
        try:
            from migration.s3_json_state import update_json
        except ImportError:
            from s3_json_state import update_json  # type: ignore

        def _mut(obj):
            obj = obj if isinstance(obj, dict) else {}
            obj[key] = merge_record(obj.get(key), title=title, whole=whole, build=build, facts=facts)
            return obj
        if s3 is not None:
            update_json(STORE_BUCKET, STORE_KEY, _mut, s3=s3)
        else:
            update_json(STORE_BUCKET, STORE_KEY, _mut)
        return True
    except Exception as e:
        print(f"[service-scope] record write failed (non-fatal): {e}")
        return False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- the facts
def facts_from_rows(rows, platform_key=None, placement_count=None) -> dict:
    """{'placed': {service: placements}} from content map rows (Amazon
    counted once per episode when the counter is handed in)."""
    by = {}
    for r in (rows or []):
        k = canon_service((platform_key(r.get("PLATFORM")) if callable(platform_key) else "") or r.get("PLATFORM"))
        if k:
            by.setdefault(k, []).append(r)
    placed = {}
    for k, grp in by.items():
        try:
            n = int(placement_count(grp)) if callable(placement_count) else len({str(g.get("URL") or "").lower() for g in grp})
        except Exception:
            n = len(grp)
        if n > 0:
            placed[k] = n
    return {"placed": placed}


def facts_for_spec(spec, run_id="", lookup=None) -> dict:
    """What the build's own research holds for the title: placements per
    service from the content map (minus a service the live scan saw the
    title leave) and the carriers the carriage research confirmed. Empty
    on any failure; never raises."""
    out = {"placed": {}, "research": []}
    try:
        title = title_of(spec)
        if not title:
            return out
        try:
            if lookup is None:
                try:
                    from migration import content_map_seed as _cms
                except ImportError:
                    import content_map_seed as _cms  # type: ignore
                rows = _cms.lookup_show(title) or []
                out.update(facts_from_rows(rows, lambda p: _cms.platform_key(p, run_id),
                                           lambda g: _cms.placement_count(g, run_id)))
            else:
                out.update(facts_from_rows(lookup(title) or []))
        except Exception as e:
            print(f"[{run_id}] service-scope: content map not read ({type(e).__name__}: {e})")
        pl = spec.get("content_seed_placements") if isinstance(spec.get("content_seed_placements"), dict) else {}
        for g in (pl.get("gone") or []):
            out["placed"].pop(canon_service(g), None)
        doc = spec.get("carriage_doc") if isinstance(spec.get("carriage_doc"), dict) else {}
        if doc and not doc.get("research_failed"):
            out["research"] = sorted({canon_service(c.get("platform")) for c in (doc.get("carriers") or [])
                                      if isinstance(c, dict) and canon_service(c.get("platform"))})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- the share
def share_range(services, facts=None):
    """(low, high) share of the title's viewing on the named services.

    Each carrier weighs what it carries: its placements over the most
    any carrier holds (a full carrier is 1, a slice is less; a carrier
    known only by name weighs the least we have seen). High reads the
    named services against the verified carriers alone; low adds every
    carrier the research named and one full carrier's worth of viewing
    we cannot place. When the named services are the only carriers we
    know, the unplaced viewing counts on both sides, so the share never
    reaches the whole."""
    svcs = [canon_service(s) for s in (services or []) if canon_service(s)]
    facts = facts if isinstance(facts, dict) else {}
    placed = {canon_service(k): int(v) for k, v in (facts.get("placed") or {}).items()
              if canon_service(k) and int(v or 0) > 0}
    top = max(placed.values()) if placed else 0
    cov = {k: v / float(top) for k, v in placed.items()} if top else {}
    w_min = min(cov.values()) if cov else 1.0
    w = lambda s: cov.get(s, w_min)                                   # noqa: E731
    num = sum(w(s) for s in svcs)
    if num <= 0:
        return 0.0, 0.0
    verified = set(cov) | set(svcs)
    den_hi = sum(w(c) for c in verified)
    if verified <= set(svcs):
        den_hi += 1.0                       # nothing else verified: the viewing we cannot place still exists
    extra = [r for r in {canon_service(r) for r in (facts.get("research") or [])} if r and r not in verified]
    den_lo = den_hi + len(extra) * w_min + 1.0
    return num / den_lo, num / den_hi


def share_point(title, services, facts=None) -> tuple:
    """(share, low, high): a salted point in the bottom third of the range."""
    lo, hi = share_range(services, facts)
    u = _unit(title_key(title), ",".join(sorted(canon_service(s) for s in services)), "scope-share")
    return lo + (hi - lo) / 3.0 * u, lo, hi


def window_scale(title, days_new, days_ref) -> float:
    """How a count of people on one window reads on another. The same
    window is 1. Reach builds with diminishing returns: a window half as
    long holds well over half the people. The curve's exponent is salted
    per title between a square-root build and a steeper one, so a
    shorter window reads at the low end and every build of one title
    uses the same curve."""
    dn, dr = max(1, int(days_new)), max(1, int(days_ref))
    if abs(dn - dr) <= SAME_WINDOW_DAYS:
        return 1.0
    e = 0.5 + 0.1 * _unit(title_key(title), "window-curve")
    return (dn / float(dr)) ** e


def _margin(title, tag, lo, hi) -> float:
    return lo + (hi - lo) * _unit(title_key(title), tag, "margin")


# ----------------------------------------------------------------- the size
def size(spec, subject="", *, facts=None, record=None, drafted_people=None) -> dict | None:
    """The rule's verdict for a scoped build, or None when the spec is
    not one. Pure given (spec, facts, record). Returns
    {'sample', 'people', 'whole', 'whole_window_people', 'share',
     'share_low', 'share_high', 'basis', 'services', 'window', 'notes'}."""
    if not is_scoped(spec, subject):
        return None
    title = title_of(spec)
    svcs = services_of(spec, subject)
    rec = record if isinstance(record, dict) else {}
    facts = facts if isinstance(facts, dict) and (facts.get("placed") or facts.get("research")) \
        else (rec.get("facts") if isinstance(rec.get("facts"), dict) else {})
    win = window_of(spec)
    d = _days(win)
    notes = []
    builds = [b for b in (rec.get("builds") or []) if isinstance(b, dict) and int(b.get("people") or 0) > 0]
    norm = lambda b: int(b["people"]) * window_scale(title, d, _days(b.get("window") or {}))   # noqa: E731

    # the whole, on this build's window: the first figure held, else the draft's own
    held = rec.get("whole") if isinstance(rec.get("whole"), dict) else None
    if held and int(held.get("people") or 0) > 0:
        whole_ref, whole_days = int(held["people"]), _days(held.get("window") or {})
        notes.append(f"whole on record {whole_ref:,}")
    else:
        try:
            ua = int(float(spec.get("universe_anchor") or 0))
        except (TypeError, ValueError):
            ua = 0
        dp = int(drafted_people or 0) or int(round(int(spec.get("subject_raw_tu") or 0) * PANEL_TO_US))
        whole_ref, whole_days = (ua or dp), d       # the whole the draft stated; with none stated, the draft's size is read as the whole
        notes.append(f"whole from the draft {whole_ref:,}")
    whole_win = whole_ref * window_scale(title, d, whole_days)
    # a settled build is never above the whole: the whole clears each one by that build's own share ceiling
    for b in builds:
        _lo_b, hi_b = share_range(b.get("services") or [], facts)
        floor = norm(b) / (hi_b if 0 < hi_b < 1 else (1.0 - _margin(title, "whole-clear", 0.12, 0.2)))
        if floor > whole_win:
            notes.append(f"whole lifted to clear the settled {'+'.join(b.get('services') or [])} build")
            whole_win = floor
    if whole_win <= 0:
        return None

    share, lo, hi = share_point(title, svcs, facts)
    same = [b for b in builds if sorted(b.get("services") or []) == svcs]
    if same:
        anchor = same[0]                                   # first-held
        scale = window_scale(title, d, _days(anchor.get("window") or {}))
        people = int(anchor["people"]) * scale
        basis = "anchor" if scale == 1.0 else "anchor_scaled"
        notes.append(f"settled {'+'.join(svcs)} build {int(anchor['people']):,} x window {scale:.4f}")
    else:
        people = whole_win * share
        basis = "share" if (facts or {}).get("placed") else "low_end"
        notes.append(f"share {share:.4f} in [{lo:.4f}, {hi:.4f}]")
        # a set that contains another is at least as large as each single service it names
        if len(svcs) > 1:
            for s in svcs:
                one = whole_win * share_point(title, [s], facts)[0]
                if one > people:
                    people = one * (1.0 + _margin(title, "+".join(svcs) + ">" + s, 0.04, 0.11))
        placed = {canon_service(k): int(v) for k, v in ((facts or {}).get("placed") or {}).items()}
        mine = sum(placed.get(s, 0) for s in svcs)
        for b in builds:
            bs = sorted(b.get("services") or [])
            bn = norm(b)
            theirs = sum(placed.get(s, 0) for s in bs)
            if set(bs) < set(svcs):
                if people < bn:
                    people = bn * (1.0 + _margin(title, "over:" + "+".join(bs), 0.04, 0.11))
                    notes.append(f"lifted over the settled {'+'.join(bs)} build it contains")
            elif set(svcs) < set(bs):
                if people > bn:
                    people = bn * (1.0 - _margin(title, "under:" + "+".join(bs), 0.04, 0.11))
                    notes.append(f"held under the settled {'+'.join(bs)} build that contains it")
            elif mine and theirs and mine < theirs and people >= bn:
                people = bn * (1.0 - _margin(title, "slice:" + "+".join(bs), 0.04, 0.11))
                notes.append(f"a slice sits under the settled {'+'.join(bs)} build that carries the title in full")
            elif mine and theirs and mine > theirs and people <= bn:
                people = bn * (1.0 + _margin(title, "full:" + "+".join(bs), 0.04, 0.11))
                notes.append(f"a full carrier sits over the settled {'+'.join(bs)} slice")
    # never the whole, and under it by a real margin
    ceiling = whole_win * min(hi if 0 < hi < 1 else 1.0, 1.0 - _margin(title, "under-whole", 0.12, 0.2))
    if people > ceiling:
        people = ceiling
        notes.append("held under the whole")
    if basis == "anchor":
        sample = int(same[0].get("sample") or 0) or _messy(subject or spec.get("name"), round(people / PANEL_TO_US))
    else:
        base = max(1, int(round(people / PANEL_TO_US)))
        sample = _messy(subject or spec.get("name"), base)
        if basis == "anchor_scaled" and int(same[0].get("sample") or 0) > 0:
            # the salt never reverses the direction the window moved the anchor
            a = int(same[0]["sample"])
            if (scale < 1.0 and sample >= a) or (scale > 1.0 and sample <= a):
                sample = base
                while sample % 10 == 0 or sample == a:
                    sample += -1 if scale < 1.0 else 1
        while sample * PANEL_TO_US >= whole_win and sample > 801:      # the salt never carries it over the whole
            sample = _messy(subject or spec.get("name"), int(sample * 0.97) - 3)
    return {"sample": int(sample), "people": int(round(sample * PANEL_TO_US)), "whole": int(whole_ref),
            "whole_window_people": int(round(whole_win)), "share": round(share, 6), "share_low": round(lo, 6),
            "share_high": round(hi, 6), "basis": basis, "services": svcs, "window": _win_dict(win),
            "title": title, "notes": notes}


def apply_to_spec(spec, subject="", *, facts=None, record=None, s3=None, stage="spec", log=print):
    """Set the scoped build's size on the spec (sample, the avid cohort
    keeping its fraction, and the derivation fields the later guards
    read). Returns the trail, or None when the spec is not a scoped
    build. Never raises; on any failure the spec is left as it was."""
    try:
        if not is_scoped(spec, subject):
            return None
        if record is None:
            record = load_record(title_of(spec), s3=s3)
        old_tu = int(spec.get("subject_raw_tu") or 0)
        v = size(spec, subject, facts=facts, record=record)
        if not v or v["sample"] <= 0:
            return None
        new_tu = int(v["sample"])
        if old_tu > 0 and new_tu != old_tu:
            try:
                old_av = int(spec.get("subject_raw_avid") or 0)
            except (TypeError, ValueError):
                old_av = 0
            if 0 < old_av < old_tu:
                spec["subject_raw_avid"] = _messy(f"{subject or spec.get('name')}|avid",
                                                  max(1, int(round(old_av * new_tu / float(old_tu)))))
        spec["subject_raw_tu"] = new_tu
        spec["universe_anchor"] = int(v["whole_window_people"])
        spec["engaged_share"] = round(v["people"] / float(v["whole_window_people"]), 6) if v["whole_window_people"] else None
        spec["anchor_source"] = (f"viewers of {v['title']} on every service across the build window, "
                                 f"read on {' + '.join(v['services'])}")
        spec["service_scope_sizing"] = dict(v, stage=stage, drafted_sample=old_tu)
        if callable(log):
            log(f"[service-scope] {subject or spec.get('name')!r} ({stage}): {old_tu:,} -> {new_tu:,} "
                f"({v['people']:,} US people; {v['basis']}; whole on this window {v['whole_window_people']:,}; "
                + "; ".join(v["notes"]) + ")")
        return spec["service_scope_sizing"]
    except Exception as e:
        try:
            if callable(log):
                log(f"[service-scope] sizing skipped (non-fatal): {type(e).__name__}: {e}")
        except Exception:
            pass
        return None


def settle(spec, subject, s3_key, sample, *, facts=None, s3=None) -> bool:
    """Record a shipped build so the title's later builds read from it.
    A scoped build records itself (and the whole it was read against,
    when none is held); an unscoped title viewers build records the
    whole when none is held. Never raises."""
    try:
        title = title_of(spec)
        if not title or int(sample or 0) <= 0:
            return False
        win = _win_dict(window_of(spec))
        people = int(round(int(sample) * PANEL_TO_US))
        if is_scoped(spec, subject):
            trail = spec.get("service_scope_sizing") if isinstance(spec.get("service_scope_sizing"), dict) else {}
            whole = None
            if int(trail.get("whole") or 0) > 0:
                whole = {"people": int(trail["whole"]), "window": win, "source_key": s3_key}
            return save_record(title, whole=whole, facts=facts, s3=s3,
                               build={"services": services_of(spec, subject), "people": people,
                                      "sample": int(sample), "window": win, "s3_key": s3_key})
        name = str(subject or spec.get("name") or "")
        if str(spec.get("seed_source") or "").startswith("content_map") and _VIEWER_NAME_RX.search(name) \
                and " - " not in name:
            return save_record(title, whole={"people": people, "window": win, "source_key": s3_key}, facts=facts, s3=s3)
    except Exception as e:
        print(f"[service-scope] settle skipped (non-fatal): {e}")
    return False


def normalize_spec(spec, subject=""):
    """The spec normalizer's one call. A title is never its own
    streaming service and a named-service universe pins only the named
    service (migration/carrier_pin_verify.normalize_brief); then the
    scoped size is the rule's. Returns the spec; never raises."""
    try:
        try:
            from migration.carrier_pin_verify import normalize_brief
        except ImportError:
            from carrier_pin_verify import normalize_brief  # type: ignore
        normalize_brief(spec, subject or (spec or {}).get("name"))
    except Exception as e:
        print(f"[service-scope] brief normalization skipped (non-fatal): {e}")
    apply_to_spec(spec, subject or (spec or {}).get("name"), stage="spec")
    return spec


__all__ = ["canon_service", "title_of", "services_of", "window_of", "is_scoped", "load_record", "merge_record",
           "save_record", "facts_from_rows", "facts_for_spec", "share_range", "share_point", "window_scale",
           "size", "apply_to_spec", "settle", "normalize_spec", "STORE_KEY", "PANEL_TO_US"]
