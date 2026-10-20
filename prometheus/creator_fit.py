"""A creator-fit read and a profile on the same subject never disagree
(Jenna 2026-10-20).

Mark Valdez's thread held two Prometheus figures for DraftKings users:
a reasoned read said twelve YouTube creators together reach 8,947,163
DraftKings users (Jomboy Media alone 563,707), while the DraftKings
Users build he approved was quoted at 729,592 to 991,827 and projected
28.8M US people. The read had been bound to the wrong base ('BEST OF
THE BEST', the library's fuzzy hit on "the best youtubers"), carried no
anchor, and its total was the model's own sum of per-creator counts
with no overlap and no universe behind it.

The rule: when a read ranks creators, talent, brands or platforms by
their fit to a named audience, the audience's universe is looked up
before the numbers are sized: the catalog's projected US people for the
subject (a profile on the dashboard), the figures the thread already
banked for the subject, and the published rate in Gen Pop. Then:

  - the headline "together reach" is the unduplicated union of the
    rows, V x (1 - exp(-S / V)), where S is the plain sum of the rows'
    own counts and V the universe; a sum near or above the universe
    collapses to the overlap-aware union, a small sum stays additive;
  - every row's own count sits inside the universe;
  - the US rate for the subject is the universe over the US
    population, and every "index N" in a row note follows it;
  - when the subject's profile is on the dashboard, each creator's
    count is the profile's own row (the creator's share of the
    audience x the universe), which is the measurement the dashboard
    shows, so the two products agree by construction.

Pure arithmetic over the reasoned read's shape (``metrics`` +
``breakdown.rows``); the lookups are injected so this stays hermetic.
Never raises into a reply: a lookup miss leaves the read as it was.
"""
from __future__ import annotations

import math
import re

US_POP = 329_900_000

_CREATOR_RX = re.compile(
    r"\b(?:creators?|youtubers?|influencers?|streamers?|talent|personalit(?:y|ies)|podcasters?|hosts?|"
    r"channels?|tiktokers?|celebrit(?:y|ies)|athletes?|artists?)\b", re.I)
_FIT_RX = re.compile(r"\b(?:align|aligned|alignment|fit|fits|closest|best for|over-?index|match|matches|reach|affinity)\b", re.I)
_REACH_LABEL_RX = re.compile(r"\b(?:reach(?:ed)?|together|combined|total|unduplicated|across the)\b", re.I)
_RATE_LABEL_RX = re.compile(r"\b(?:US|national|baseline)\b.*\b(?:rate|penetration|share)\b|\brate\b.*\bUS\b", re.I)
_INDEX_RX = re.compile(r"\bindex\s+(\d{2,5})\b", re.I)
_NUM_RX = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
_TALENT_COLUMNS_RX = re.compile(
    r"^(?:TALENT|INFLUENCER/CREATOR|CREATOR/INFLUENCER|HOST/PERSONALITY|PODCASTER|ATHLETE|COMEDIAN|ACTOR|MUSICIAN/BAND|"
    r"[A-Z/ -]+ CONTENT)$")


def _fold(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _messy(seed, value):
    """A count that never ends in 0 (house rule), nudged by the seed."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v % 10 == 0:
        import hashlib
        h = int(hashlib.md5(str(seed).encode("utf-8")).hexdigest()[:6], 16)
        v += 1 + (h % 9)
    return v


def is_creator_fit(res, question=""):
    """A read that ranks creators / talent / brands by fit to an audience."""
    if not isinstance(res, dict):
        return False
    bd = res.get("breakdown") or {}
    dim = str((bd or {}).get("dimension") or "")
    basis = str((bd or {}).get("share_basis") or "")
    text = f"{question} {dim} {basis}"
    if not (bd.get("rows") if isinstance(bd, dict) else None):
        return False
    return bool(_CREATOR_RX.search(text)) and bool(_FIT_RX.search(text) or _REACH_LABEL_RX.search(basis))


def union_reach(total_sum, universe):
    """The unduplicated union of audiences whose plain sum is ``total_sum``
    inside a universe of ``universe`` people: V x (1 - e^(-S/V)). Small
    sums stay almost additive (S = 0.1 V -> 0.095 V); a sum at the size
    of the universe collapses to 63% of it; nothing ever exceeds V."""
    try:
        S, V = float(total_sum), float(universe)
    except (TypeError, ValueError):
        return 0
    if S <= 0 or V <= 0:
        return 0
    return int(round(V * (1.0 - math.exp(-S / V))))


def universe_from_anchors(anchors, window=None):
    """(people, source) from the corpus catalog's anchors for a subject:
    the profile's projected US people first, then a banked chat figure
    for the subject's size. (0, '') when the corpus holds neither."""
    if not isinstance(anchors, dict):
        return 0, ""
    try:
        from migration import corpus_catalog as cc
        pa = cc.profile_anchor(anchors, window=window)
    except Exception:
        pa = None
    if isinstance(pa, dict) and pa.get("audience_size"):
        try:
            v = int(float(pa["audience_size"]))
            if v > 0:
                return v, f"profile {pa.get('source_key') or ''}".strip()
        except (TypeError, ValueError):
            pass
    for e in (anchors.get("ledger") or []):
        if not isinstance(e, dict):
            continue
        for m in (e.get("metrics") or []):
            if not isinstance(m, dict):
                continue
            name = f"{m.get('name') or ''} {m.get('label') or ''}".lower()
            if re.search(r"us_audience|us audience|universe|us people|audience_size", name):
                try:
                    v = int(float(m.get("value")))
                except (TypeError, ValueError):
                    continue
                if v > 0:
                    return v, "thread"
    return 0, ""


def universe_from_genpop(subject, genpop_map, columns=("BETTING", "APP/PLATFORM", "APP/PLATFORM USAGE", "STREAMING/PLATFORM",
                                                     "SOCIAL MEDIA", "WHERE THEY SHOP", "MOST PURCHASED BRANDS")):
    """The published US rate in Gen Pop for the subject's brand (the
    audience noun stripped) x the US population. (0, '') when absent."""
    brand = re.sub(r"\b(?:users?|customers?|subscribers?|members?|payers?|players?|bettors?|fans?|viewers?|listeners?|buyers?|shoppers?)\b\s*$",
                   "", str(subject or ""), flags=re.I).strip()
    if not brand or not isinstance(genpop_map, dict):
        return 0, ""
    key = _fold(brand).replace(" ", "")
    best = 0.0
    for (cat, b), bp in genpop_map.items():
        if _fold(b).replace(" ", "") != key:
            continue
        if columns and str(cat).upper() not in columns:
            continue
        try:
            best = max(best, float(bp))
        except (TypeError, ValueError):
            continue
    if best <= 0:
        return 0, ""
    return int(round(best / 100.0 * US_POP)), "gen pop"


def profile_creator_shares(df, labels, bp_col="Brand Penetration (Row)"):
    """{row label: the creator's share (%) of the subject's audience} read
    off the subject's own profile frame: TALENT, INFLUENCER/CREATOR, the
    role columns and the content verticals, matched case and punctuation
    insensitive on the label (a slash alias like 'NELK / Full Send' tries
    each side). The profile's measurement, so the two products agree."""
    out = {}
    try:
        cols = df["Column"].astype(str).str.strip().str.upper()
        sub = df[cols.map(lambda c: bool(_TALENT_COLUMNS_RX.match(c)))]
        if bp_col not in sub.columns:
            return out
        idx = {}
        for val, bp in zip(sub["Value"].astype(str), sub[bp_col]):
            try:
                v = float(str(bp).replace("%", "").replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            k = _fold(val)
            if k and (k not in idx or v > idx[k]):
                idx[k] = v
        for label in labels:
            cands = [label] + [p.strip() for p in str(label).split("/") if p.strip()]
            for c in cands:
                k = _fold(c)
                if k in idx:
                    out[label] = idx[k]
                    break
    except Exception:
        return out
    return out


def _replace_number(text, old, new):
    if not text or not old:
        return text
    return re.sub(r"(?<![\d,])" + re.escape(f"{old:,}") + r"(?![\d,])", f"{new:,}", str(text))


def reconcile(res, universe, source="", *, profile_shares=None, audiences=None, subject=""):
    """Hold a creator-fit read to the subject's universe. Returns
    (res, notes). ``profile_shares`` ({row label: share % of the audience})
    rebuilds every row from the profile's own measurement; ``audiences``
    ({row label: the creator's own US audience}) lets the penetration of
    each creator's audience be restated as count / audience."""
    notes = []
    if not isinstance(res, dict) or not universe or universe <= 0:
        return res, notes
    out = dict(res)
    bd = dict(out.get("breakdown") or {})
    rows = [dict(r) for r in (bd.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return res, notes
    V = int(universe)
    subj = subject or str(out.get("subject") or "")
    # the plain sum the model reported: the count metric whose label reads as the reach
    metrics = [dict(m) for m in (out.get("metrics") or []) if isinstance(m, dict)]
    reach_i = next((i for i, m in enumerate(metrics)
                    if m.get("unit") not in ("pct", "percent", "%") and _REACH_LABEL_RX.search(str(m.get("label") or ""))), None)
    old_total = int(float(metrics[reach_i]["value"])) if reach_i is not None else 0
    # the per-row counts
    counts = {}
    if profile_shares:
        for r in rows:
            sh = profile_shares.get(r["label"])
            if sh is not None:
                counts[r["label"]] = _messy(f"{subj}|{r['label']}|profile", V * float(sh) / 100.0)
        notes.append(f"{len(counts)} of {len(rows)} rows from the profile's own rows")
    for r in rows:
        if r["label"] in counts:
            continue
        if old_total > 0:
            counts[r["label"]] = _messy(f"{subj}|{r['label']}", old_total * float(r.get("share_pct") or 0) / 100.0)
    # every row inside the universe
    for k, v in list(counts.items()):
        if v > V:
            counts[k] = _messy(f"{subj}|{k}|cap", V * 0.92)
            notes.append(f"{k}: count above the universe, capped")
    S = sum(counts.values())
    if S <= 0:
        return res, notes
    union = union_reach(S, V) if S > 0.35 * V else int(S)
    union = _messy(f"{subj}|union", min(union, V))
    # shares: each row's own count over the sum of the rows
    for r in rows:
        c = counts.get(r["label"], 0)
        r["share_pct"] = round(100.0 * c / S, 4) if S else r.get("share_pct")
        r["count"] = int(c)
        if audiences and audiences.get(r["label"]):
            try:
                pen = 100.0 * c / float(audiences[r["label"]])
                if 0 < pen <= 100:
                    r["penetration_pct"] = round(pen, 1)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    resid = round(100.0 - sum(r["share_pct"] for r in rows), 4)
    rows.sort(key=lambda r: -r["share_pct"])
    rows[0]["share_pct"] = round(rows[0]["share_pct"] + resid, 4)
    # the US rate for the subject and the indexes that follow it
    rate = round(100.0 * V / US_POP, 1)
    rate_i = next((i for i, m in enumerate(metrics)
                   if m.get("unit") in ("pct", "percent", "%") and _RATE_LABEL_RX.search(str(m.get("label") or ""))), None)
    old_rate = float(metrics[rate_i]["value"]) if rate_i is not None else 0.0
    if rate_i is not None and abs(old_rate - rate) >= 0.05:
        metrics[rate_i]["value"] = rate
        notes.append(f"US rate {old_rate:.1f}% -> {rate:.1f}% (universe {V:,} / US population)")
    label_index = {}
    for r in rows:
        pen = r.get("penetration_pct")
        if pen and rate > 0:
            new_idx = int(round(float(pen) / rate * 100))
            label_index[r["label"]] = new_idx
            if r.get("note"):
                r["note"] = _INDEX_RX.sub(lambda m: f"index {new_idx}", str(r["note"]), count=1)
    if label_index:
        def _swap_idx(text):
            text = str(text or "")

            def _one(m):
                # the row this index belongs to: the nearest row label named before it
                before = text[max(0, m.start() - 220):m.start()].lower()
                best, best_pos = None, -1
                for lab, idx in label_index.items():
                    pos = max(before.rfind(_fold(lab)), before.rfind(lab.lower()))
                    if pos > best_pos:
                        best, best_pos = idx, pos
                return f"index {best}" if best is not None else m.group(0)
            return _INDEX_RX.sub(_one, text)
        out["headline"] = _swap_idx(out.get("headline"))
        out["reads"] = [_swap_idx(t) for t in (out.get("reads") or [])]
    if reach_i is not None and old_total != union:
        metrics[reach_i]["value"] = union
        lab = str(metrics[reach_i].get("label") or "")
        if "unduplicated" not in lab.lower():
            metrics[reach_i]["label"] = (lab + " (unduplicated)")[:90]
        notes.append(f"reach {old_total:,} -> {union:,} unduplicated inside a universe of {V:,} ({source or 'universe'})")
        for k in ("headline",):
            out[k] = _replace_number(out.get(k), old_total, union)
        out["reads"] = [_replace_number(t, old_total, union) for t in (out.get("reads") or [])]
    # a "top alignment index" metric follows the new rate
    for m in metrics:
        if m.get("unit") not in ("pct", "percent", "%") and re.search(r"\bindex\b", str(m.get("label") or ""), re.I) and rate > 0:
            top = max((r.get("penetration_pct") or 0) for r in rows)
            if top:
                m["value"] = int(round(float(top) / rate * 100))
    bd["rows"] = rows
    bd["universe"] = V
    bd["universe_source"] = source
    out["breakdown"] = bd
    out["metrics"] = metrics
    out["_creator_fit"] = {"universe": V, "source": source, "sum": int(S), "union": union, "notes": notes}
    return out, notes


_AUDIENCE_NOUN_RX = re.compile(r"\b(?:users?|customers?|subscribers?|members?|payers?|players?|bettors?|fans?|viewers?|listeners?|buyers?|shoppers?)\b\s*$", re.I)


def subject_variants(subject, question=""):
    """The names the subject's universe may be filed under: as written,
    with the audience noun ('DraftKings' -> 'DraftKings Users'), without
    it, and the brand the question itself names with a noun."""
    out = []
    s = " ".join(str(subject or "").split())
    if s:
        out.append(s)
        bare = _AUDIENCE_NOUN_RX.sub("", s).strip()
        if bare and bare.lower() != s.lower():
            out.append(bare)
        if not _AUDIENCE_NOUN_RX.search(s):
            out.append(f"{s} Users")
    m = re.search(r"\b([A-Z][\w&'.-]+(?:\s+[A-Z][\w&'.-]+){0,2})\s+(users|customers|subscribers|members|bettors|players|fans|viewers|listeners)\b",
                  str(question or ""))
    if m:
        out.append(f"{m.group(1)} {m.group(2).title()}")
    seen, uniq = set(), []
    for v in out:
        k = _fold(v)
        if k and k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq


def apply(res, question, *, anchors=None, genpop_map=None, profile_df=None, audiences=None, window=None,
          anchors_fn=None, genpop_fn=None, profile_loader=None):
    """The one call the reasoned read makes after its coherence pass.
    Looks the universe up (the catalog's projected US people for the
    subject under any of its names, then the thread's banked figure,
    then Gen Pop) and holds the read to it. The lookups are injected
    (``anchors_fn(subject)``, ``genpop_fn()``, ``profile_loader(key)``)
    so a miss or a raise leaves the read as it was. Returns the read
    unchanged when it is not a creator-fit read or no universe is known."""
    if not is_creator_fit(res, question):
        return res
    subject = str((res or {}).get("subject") or "")
    V, src, pa_key = 0, "", ""
    for name in subject_variants(subject, question):
        a = anchors
        if a is None and callable(anchors_fn):
            try:
                a = anchors_fn(name)
            except Exception:
                a = None
        V, src = universe_from_anchors(a, window=window)
        if V:
            try:
                from migration import corpus_catalog as cc
                pa = cc.profile_anchor(a, window=window) or {}
                pa_key = str(pa.get("source_key") or "")
            except Exception:
                pa_key = ""
            break
    if not V:
        gm = genpop_map
        if gm is None and callable(genpop_fn):
            try:
                gm = genpop_fn()
            except Exception:
                gm = None
        V, src = universe_from_genpop(subject, gm)
    if not V:
        return res
    df = profile_df
    if df is None and pa_key and callable(profile_loader):
        try:
            df = profile_loader(pa_key)
        except Exception:
            df = None
    shares = None
    if df is not None:
        try:
            labels = [str(r.get("label") or "") for r in ((res.get("breakdown") or {}).get("rows") or [])]
            shares = profile_creator_shares(df, labels) or None
        except Exception:
            shares = None
    out, notes = reconcile(res, V, src, profile_shares=shares, audiences=audiences, subject=subject)
    if notes:
        print(f"[creator-fit] {subject!r}: " + "; ".join(notes))
    return out
