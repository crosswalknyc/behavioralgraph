"""Per-title universe anchor registry for cross-product coherence.

2026-08-25 (Jenna): "ensure that the demographics that are used in
subscriber iq always match the ones in profile iq and vise versa same
with total number of viewers and sample size. whichever comes first
needs to ensure that the universe is equal to the number of us viewers
of the show or movie and coordinated so they always align."

The registry lives at s3://dashboard-inputs/system/title_anchors.json,
guarded with the ETag CAS helper (migration/s3_json_state.py). One
entry per title (case + punctuation insensitive key, season and cut
qualifiers stripped), storing:

    {
      "<norm key>": {
        "title":          "Landman",
        "us_viewers":     9314227,          # the universe anchor
        "sample_size":    282341,           # panel basis (us_viewers/32.99)
        "demos":          {"GENDER": {label: pct}, "AGE": {label: pct}},
        "source_product": "subscriber_iq" | "profile_iq",
        "window":         {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "season":         2 or null,
        "s3_keys":        {"subscriber_iq": "...", "profile_iq": "..."},
        "created_at":     iso ts, "updated_at": iso ts
      }
    }

Whichever product builds FIRST for a title establishes the anchor; the
second product consults it before sizing and must align: same US
viewer count, same sample basis, matching GENDER + AGE distributions.
Both products share the same canonical demo bucket labels
(migration/canonical_demos.PIPELINE_DEMO_SCHEMA GENDER + AGE match the
Subscriber IQ output schema 1:1), so demos transfer directly.

Twin-sync note: this module lives in BOTH the parent repo
(migration/title_anchors.py, imported by the engine-host worker) and
bg-webapp/migration/title_anchors.py (imported by app.py at interpret
time). Keep the two byte-equal.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

ANCHORS_BUCKET = "dashboard-inputs"
ANCHORS_KEY = "system/title_anchors.json"

# The fixed 10M virtual panel scales to 329.9M US Gen Pop.
PANEL_TO_US = 32.99

# Demo categories the anchor carries. Both products emit these buckets
# with identical labels (PIPELINE_DEMO_SCHEMA == Subscriber IQ schema).
ANCHOR_DEMO_CATS = ("GENDER", "AGE")

_SEASON_RE = re.compile(
    r"\bseason\s*(\d{1,2})\b|\bs(\d{1,2})\b(?!\d)", re.IGNORECASE)
_NORM_RE = re.compile(r"[^A-Z0-9]+")

# Audience-noun suffixes that never identify the title itself.
_TITLE_NOISE_WORDS = (
    "viewers", "watchers", "fans", "audience", "audiences", "subscribers",
    "streamers", "binge", "watchers", "households",
)


def season_from_text(text) -> int | None:
    """Pull a season number out of free text ('Landman season 2',
    'Landman S2'). None when no season qualifier is present."""
    m = _SEASON_RE.search(str(text or ""))
    if not m:
        return None
    for g in m.groups():
        if g:
            try:
                n = int(g)
                return n if 1 <= n <= 60 else None
            except ValueError:
                return None
    return None


def title_key(title) -> str:
    """Case + punctuation insensitive per-title key. Strips cut
    suffixes (' - Avid Fan'), season qualifiers, and audience-noun
    tails so 'Landman - Season 2', 'Landman Season 2 Viewers' and
    'landman' all land on the same anchor."""
    s = str(title or "").strip()
    if not s:
        return ""
    # cut suffix: '{Subject} - {Cut}' keeps only the subject
    s = s.split(" - ", 1)[0].strip()
    s = _SEASON_RE.sub(" ", s)
    words = [w for w in s.split() if w.lower() not in _TITLE_NOISE_WORDS]
    s = " ".join(words) or s
    return _NORM_RE.sub("", s.upper())


def season_to_date(air_end_iso, today=None):
    """Season-in-progress check. Returns (in_progress: bool,
    through_iso: str). A finale date on or after today means the season
    is still airing and the deliverable is 'Season N to date' through
    today. Pure function (testable offline)."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(today, str):
        today = date.fromisoformat(today)
    try:
        end = date.fromisoformat(str(air_end_iso or "").strip())
    except (ValueError, TypeError):
        return False, ""
    if end >= today:
        return True, today.isoformat()
    return False, end.isoformat()


def window_conflicts(user_start, user_end, air_start, air_end,
                     tolerance_days=10) -> bool:
    """True when user-supplied dates disagree with the researched air
    window by more than `tolerance_days` on either side. Pure function
    (testable offline). Missing/invalid dates never conflict."""
    def _d(v):
        try:
            return date.fromisoformat(str(v or "").strip())
        except (ValueError, TypeError):
            return None
    us, ue = _d(user_start), _d(user_end)
    as_, ae = _d(air_start), _d(air_end)
    if not (us and ue and as_ and ae):
        return False
    return (abs((us - as_).days) > tolerance_days
            or abs((ue - ae).days) > tolerance_days)


def anchor_sample_size(subject, us_viewers) -> int:
    """Panel sample basis for a US viewer universe: viewers / 32.99,
    made messy per no-round-sample-sizes (idempotent per subject)."""
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
    except ImportError:
        try:
            from _sample_size_jitter import ensure_messy_sample_size
        except ImportError:
            def ensure_messy_sample_size(subj, v, **_kw):
                x = int(v or 0)
                if x % 10 == 0:
                    x += 1 + int(hashlib.md5(
                        str(subj).encode()).hexdigest()[:2], 16) % 8
                return x
    base = max(500, int(round(float(us_viewers or 0) / PANEL_TO_US)))
    return ensure_messy_sample_size(str(subject or "title"), base)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_title_anchor(title):
    """Fetch the anchor for a title from S3. Returns the entry dict or
    None. Never raises (a registry read failure never blocks a build)."""
    key = title_key(title)
    if not key:
        return None
    try:
        try:
            from migration.s3_json_state import read_json_with_etag
        except ImportError:
            from s3_json_state import read_json_with_etag
        obj, _etag = read_json_with_etag(ANCHORS_BUCKET, ANCHORS_KEY)
        if not isinstance(obj, dict):
            return None
        entry = obj.get(key)
        if not isinstance(entry, dict):
            return None
        if not entry.get("us_viewers"):
            return None
        return entry
    except Exception as e:
        print(f"[title-anchors] read failed (non-fatal): {e}")
        return None


def record_title_anchor(title, product, us_viewers=None, sample_size=None,
                        demos=None, s3_key=None, window=None, season=None):
    """CAS-guarded write. First writer for a title establishes the
    numeric anchor (us_viewers, sample_size, demos); later writers for
    the same title only append their product's s3 key and metadata -
    the established anchor is never overwritten (whichever comes first
    wins, by design). Returns the stored entry or None. Never raises."""
    key = title_key(title)
    if not key or product not in ("subscriber_iq", "profile_iq"):
        return None
    try:
        try:
            from migration.s3_json_state import update_json
        except ImportError:
            from s3_json_state import update_json

        def _mutate(obj):
            if not isinstance(obj, dict):
                obj = {}
            entry = obj.get(key)
            if not isinstance(entry, dict) or not entry.get("us_viewers"):
                entry = {
                    "title": str(title or "").split(" - ", 1)[0].strip(),
                    "us_viewers": int(us_viewers or 0),
                    "sample_size": int(sample_size or 0),
                    "demos": demos if isinstance(demos, dict) else {},
                    "source_product": product,
                    "window": window if isinstance(window, dict) else {},
                    "season": season,
                    "s3_keys": {},
                    "created_at": _now_iso(),
                }
                if entry["us_viewers"] <= 0:
                    return None  # nothing countable to anchor - abort
            keys = entry.setdefault("s3_keys", {})
            if s3_key:
                keys[product] = str(s3_key)
            # Backfill demos when the establishing product had none.
            if not entry.get("demos") and isinstance(demos, dict) and demos:
                entry["demos"] = demos
            entry["updated_at"] = _now_iso()
            obj[key] = entry
            return obj

        written = update_json(ANCHORS_BUCKET, ANCHORS_KEY, _mutate,
                              default={})
        return (written or {}).get(key)
    except Exception as e:
        print(f"[title-anchors] write failed (non-fatal): {e}")
        return None


# ---------------------------------------------------------------------------
# Demo shape transfer. Both products use the same GENDER + AGE bucket
# labels, so transfer is a direct per-label copy (case-insensitive
# label match, values renormalized to sum 100).
# ---------------------------------------------------------------------------

def _norm_label(s):
    return _NORM_RE.sub("", str(s or "").upper())


def normalize_demo_pcts(pcts):
    """{label: pct} -> same dict renormalized to sum exactly 100.0
    (last bucket absorbs the residual). Returns {} on bad input."""
    if not isinstance(pcts, dict) or not pcts:
        return {}
    clean = {}
    for k, v in pcts.items():
        try:
            f = float(str(v).replace("%", "").strip())
        except (ValueError, TypeError):
            continue
        if f >= 0:
            clean[str(k)] = f
    total = sum(clean.values())
    if total <= 0:
        return {}
    out = {k: round(v * 100.0 / total, 4) for k, v in clean.items()}
    labels = list(out)
    resid = round(100.0 - sum(out.values()), 4)
    if labels and abs(resid) > 0:
        out[labels[-1]] = round(out[labels[-1]] + resid, 4)
    return out


def anchor_demos_from_tu_demos(tu_demos):
    """Extract the anchor demo payload {'GENDER': {...}, 'AGE': {...}}
    from an interpret-stage tu_demos dict (category keys are matched
    case-insensitively)."""
    out = {}
    if not isinstance(tu_demos, dict):
        return out
    by_norm = {_norm_label(k): v for k, v in tu_demos.items()
               if isinstance(v, dict)}
    for cat in ANCHOR_DEMO_CATS:
        pcts = normalize_demo_pcts(by_norm.get(_norm_label(cat)) or {})
        if pcts:
            out[cat] = pcts
    return out


def apply_anchor_to_tu_demos(tu_demos, anchor):
    """Override the GENDER + AGE categories of a tu_demos dict with the
    anchor's distributions (in place). Returns the list of categories
    replaced. Categories the anchor lacks are left alone."""
    replaced = []
    if not isinstance(tu_demos, dict) or not isinstance(anchor, dict):
        return replaced
    demos = anchor.get("demos") or {}
    for cat in ANCHOR_DEMO_CATS:
        pcts = normalize_demo_pcts(demos.get(cat) or {})
        if not pcts:
            continue
        hit = None
        for k in list(tu_demos):
            if _norm_label(k) == _norm_label(cat):
                hit = k
                break
        tu_demos[hit or cat] = dict(pcts)
        replaced.append(cat)
    return replaced


def subiq_demo_overrides_from_anchor(anchor):
    """Anchor demos -> (age_pcts, gender_pcts) dicts in the Subscriber
    IQ config-override shape (labels are already identical). Returns
    ({}, {}) when the anchor carries no usable demos."""
    demos = (anchor or {}).get("demos") or {}
    age = normalize_demo_pcts(demos.get("AGE") or {})
    gender = normalize_demo_pcts(demos.get("GENDER") or {})
    age = {k: round(v, 1) for k, v in age.items()}
    gender = {k: round(v, 1) for k, v in gender.items()}
    return age, gender


# ---------------------------------------------------------------------------
# Ship-path alignment for Profile IQ dataframes (align, don't block).
# ---------------------------------------------------------------------------

def extract_profile_demos(df):
    """Read {'GENDER': {label: pct}, 'AGE': {label: pct}} out of a
    built profile DataFrame (Column/Value/Brand Penetration (Row))."""
    out = {}
    try:
        cols = {c.lower().strip(): c for c in df.columns}
        bp_col = (cols.get("brand penetration (row)")
                  or "Brand Penetration (Row)")
        for cat in ANCHOR_DEMO_CATS:
            mask = df["Column"].astype(str).str.strip().str.upper() == cat
            pcts = {}
            for _, r in df[mask].iterrows():
                try:
                    pcts[str(r["Value"]).strip()] = float(
                        str(r[bp_col]).replace("%", "").strip())
                except (ValueError, TypeError):
                    continue
            if pcts:
                out[cat] = pcts
    except Exception as e:
        print(f"[title-anchors] demo extract failed (non-fatal): {e}")
    return out


def align_profile_df_to_anchor(df, subject, anchor, tolerance_pp=1.5):
    """Ship-path corrector: snap the GENDER + AGE rows of a built
    profile to the anchor's distributions when any bucket drifts more
    than `tolerance_pp`. Anchor pct gets a subject-salted micro-jitter
    (+/- up to 0.03pp, never a 2dp boundary) so no value is pinned;
    the category renormalizes to 100 and Raw/Projection recompute.
    Mutates df in place; returns the number of rows corrected. Never
    raises (alignment failure leaves the profile as built)."""
    fixed = 0
    try:
        demos = (anchor or {}).get("demos") or {}
        if not demos:
            return 0
        cols = {c.lower().strip(): c for c in df.columns}
        bp_col = (cols.get("brand penetration (row)")
                  or "Brand Penetration (Row)")
        raw_col = next((c for c in df.columns
                        if c.lower().strip().startswith("original raw")),
                       None)
        proj_col = next((c for c in df.columns
                         if "projection" in c.lower().strip()), None)
        cs_col = cols.get("category share")
        # sample basis: prefer the anchor's, fall back to BRAND INPUT raw
        sample = int((anchor or {}).get("sample_size") or 0)
        if sample <= 0 and raw_col:
            bi = df[df["Column"].astype(str).str.strip().str.upper()
                    == "BRAND INPUT"]
            if len(bi):
                try:
                    sample = int(float(str(
                        bi.iloc[0][raw_col]).replace(",", "")))
                except (ValueError, TypeError):
                    sample = 0
        for cat in ANCHOR_DEMO_CATS:
            target = normalize_demo_pcts(demos.get(cat) or {})
            if not target:
                continue
            mask = (df["Column"].astype(str).str.strip().str.upper()
                    == cat)
            idxs = list(df[mask].index)
            if not idxs:
                continue
            by_norm = {_norm_label(k): v for k, v in target.items()}
            drift = 0.0
            for idx in idxs:
                lbl = _norm_label(df.at[idx, "Value"])
                if lbl not in by_norm:
                    continue
                try:
                    cur = float(str(df.at[idx, bp_col])
                                .replace("%", "").strip())
                except (ValueError, TypeError):
                    continue
                drift = max(drift, abs(cur - by_norm[lbl]))
            if drift <= tolerance_pp:
                continue
            # snap every bucket to anchor + salted micro-jitter
            new_vals = {}
            for idx in idxs:
                lbl_raw = str(df.at[idx, "Value"]).strip()
                lbl = _norm_label(lbl_raw)
                if lbl not in by_norm:
                    new_vals[idx] = None
                    continue
                h = int(hashlib.blake2b(
                    f"{subject}|{cat}|{lbl_raw}|anchor-align".encode(),
                    digest_size=8).hexdigest(), 16)
                jit = ((h % 601) - 300) / 10000.0  # +/-0.03pp
                v = max(0.0011, by_norm[lbl] + jit)
                new_vals[idx] = v
            total = sum(v for v in new_vals.values() if v)
            if total <= 0:
                continue
            for idx, v in new_vals.items():
                if v is None:
                    continue
                v = v * 100.0 / total
                v = round(v, 4)
                # never land on a 2dp boundary
                if abs(v * 100 - round(v * 100)) < 1e-4:
                    v = round(v + 0.0013, 4)
                df.at[idx, bp_col] = f"{v:.4f}%"
                if sample > 0:
                    raw = int(round(sample * v / 100.0))
                    if raw_col:
                        df.at[idx, raw_col] = raw
                    if proj_col:
                        df.at[idx, proj_col] = int(round(
                            raw / 10_000_000 * 329_900_000))
                if cs_col:
                    df.at[idx, cs_col] = f"{v:.4f}%"
                fixed += 1
            print(f"[title-anchors] {subject!r}: {cat} snapped to the "
                  f"title anchor (max drift {drift:.2f}pp, "
                  f"{len(idxs)} buckets)")
    except Exception as e:
        print(f"[title-anchors] profile alignment failed (non-fatal), "
              f"profile ships as built: {e}")
    return fixed
