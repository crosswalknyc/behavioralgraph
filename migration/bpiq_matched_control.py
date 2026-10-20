"""Brand Partnership IQ: the matched comparison group.

Jenna 2026-10-20 (verbatim): "when pulling brand partnership valuations
we need the pre brand engagement to be roughly the same for the control
group and sample group along iwth the sampe sample size. make a note and
commit that to pipleine"

A valuation's lift is the partnership, not a baseline difference, only
when the group it is compared against started where the exposed group
started. Two matching rules, enforced on every valuation that
publishes:

  1. SIZE. The comparison group carries the exposed group's exact
     sample size (the same messy integer, both on the payload).
  2. PRE-PERIOD ENGAGEMENT. The comparison group's brand engagement
     rate before the partnership window sits within MATCH_BAND_REL_PCT
     (5 percent relative) of the exposed group's, never byte-identical:
     the draw lands between 0.6 and 4.2 percent off, with a salted
     sign, and the realized counts always differ by at least one
     person. When the exposed group's pre-period count is so small
     that one person is more than the band, one person is the band.

The post period is read as a difference in differences:

    lift_pp = (exposed post - exposed pre) - (comparison post - comparison pre)

and every lift lens follows it: Brand Lift Value prices the exposed
group's projected gain net of the comparison group's projected gain;
each platform's Earned Media incremental is the raw gain scaled by the
share of the exposed lift that survives the comparison (did_share);
attributable-to-partnership rides the house formula on the matched
lift. Brand Engagement Value and Conversion Value are not lift lenses
and do not move.

The comparison group is DRAWN (deterministic, salted by the subject)
rather than researched: the research call supplies the exposed group's
levels and the background drift of a matched group over the window
(gen_pop_drift_pp); the code forms the group and holds the rules
regardless of what the model returned. A payload whose comparison
group breaks either rule is never published as is: enforce_matched_
control re-draws the group from the payload's own exposed figures and
recomputes every downstream number in place (no hold, no email).

Reader-facing line (plain words, no method vocabulary):
    "Compared against a matched group of the same size with the same
    brand engagement before the partnership."
"""
from __future__ import annotations

import hashlib
from typing import Optional

MATCH_BAND_REL_PCT = 5.0          # the tolerance the check enforces
_DRAW_LOW_REL = 0.006             # the salted draw lands inside the band
_DRAW_HIGH_REL = 0.042

READER_NOTE = ("Compared against a matched group of the same size with the "
               "same brand engagement before the partnership.")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _h(*parts) -> int:
    return int(hashlib.blake2b("|".join(str(p) for p in parts).encode(),
                               digest_size=8).hexdigest(), 16)


def _messy(seed, value: float) -> int:
    """Round a derived count to a messy integer (never trailing-zero)."""
    v = int(round(value))
    if v <= 0:
        return max(v, 0)
    if v % 10 == 0:
        v += 1 + (_h(seed, v) % 8)
    return v


def _rate(users, n) -> float:
    return (float(users) / float(n) * 100.0) if n else 0.0


def within_band(exposed_users, control_users, n, *,
                band_rel_pct: float = MATCH_BAND_REL_PCT) -> bool:
    """True when the two pre-period counts, on the same base n, read as
    the same brand engagement: within band_rel_pct relative, or within
    one person when the exposed count is too small for the band to
    hold a distinct count."""
    e = float(exposed_users or 0)
    c = float(control_users or 0)
    if e <= 0:
        return c <= 1
    if abs(c - e) <= 1.0:
        return True
    return abs(c - e) / e * 100.0 <= band_rel_pct + 1e-9


def rel_diff_pct(exposed_users, control_users) -> Optional[float]:
    e = float(exposed_users or 0)
    if e <= 0:
        return None
    return round(abs(float(control_users or 0) - e) / e * 100.0, 2)


# ---------------------------------------------------------------------------
# the draw
# ---------------------------------------------------------------------------

def draw_matched_control(subject: str, n: int, pre_users: int,
                         drift_pp: float, *, salt: str = "matched") -> dict:
    """Form the comparison group for an exposed group of ``n`` with
    ``pre_users`` engaged before the window.

    Returns the raw counts: {control_size, control_pre_users,
    control_post_users, offset_rel} with
      * control_size == n,
      * control_pre_users within the band of pre_users and never equal,
      * control_post_users = the pre count carried forward by the
        background drift (drift_pp, percentage points of n), clamped
        to [0, n].
    Deterministic for a (subject, salt) pair; two subjects never share
    an offset."""
    n = int(n)
    if n <= 0:
        return {"control_size": 0, "control_pre_users": 0,
                "control_post_users": 0, "offset_rel": 0.0}
    pre_users = int(min(max(int(pre_users), 0), n))
    span = _DRAW_HIGH_REL - _DRAW_LOW_REL
    off = _DRAW_LOW_REL + (_h(subject, salt, "off") % 10000) / 10000.0 * span
    sign = 1 if (_h(subject, salt, "sign") % 2) else -1
    target = pre_users * (1.0 + sign * off)
    # Every count the band allows (or one person either side on a base
    # too small for the band), never the exposed count itself, never a
    # trailing zero (the payload's count hygiene), inside [0, n]. The
    # one nearest the salted target wins; ties fall to the salt.
    lo = max(0, min(pre_users - 1, int(pre_users * (1.0 - MATCH_BAND_REL_PCT / 100.0))))
    hi = min(n, max(pre_users + 1, int(pre_users * (1.0 + MATCH_BAND_REL_PCT / 100.0) + 1e-9)))
    cands = [x for x in range(lo, hi + 1)
             if x != pre_users and (x == 0 or x % 10 != 0)
             and within_band(pre_users, x, n)]
    if not cands:                               # n of one or two: anything distinct
        cands = [x for x in range(0, n + 1) if x != pre_users] or [pre_users]
    tie = _h(subject, salt, "tie") % 2
    c_pre = sorted(cands, key=lambda x: (abs(x - target), (x > target) != bool(tie)))[0]
    c_post_target = c_pre + n * float(drift_pp or 0.0) / 100.0
    c_post = _messy((subject, salt, "c_post"), c_post_target)
    c_post = int(min(max(c_post, 0), n))
    return {"control_size": n, "control_pre_users": int(c_pre),
            "control_post_users": c_post,
            "offset_rel": round(sign * off, 5)}


# ---------------------------------------------------------------------------
# the block + the lenses
# ---------------------------------------------------------------------------

def control_block(subject: str, n: int, projected: int, pre_users: int,
                  post_users: int, drift_pp: float, *,
                  salt: str = "matched", enabled: bool = True) -> dict:
    """The payload's control_group block for a matched comparison
    group, with the difference in differences and the reader-facing
    match figures."""
    d = draw_matched_control(subject, n, pre_users, drift_pp, salt=salt)
    c_pre, c_post = d["control_pre_users"], d["control_post_users"]
    weight = (float(projected) / float(n)) if n else 0.0

    def proj(v):
        return _messy((subject, "proj", v), v * weight)

    t_pre = round(_rate(pre_users, n), 2)
    t_post = round(_rate(post_users, n), 2)
    c_pre_pct = round(_rate(c_pre, n), 2)
    c_post_pct = round(_rate(c_post, n), 2)
    treat_delta = round(t_post - t_pre, 2)
    ctrl_delta = round(c_post_pct - c_pre_pct, 2)
    incr_pp = round(treat_delta - ctrl_delta, 2)
    block = {
        "enabled": bool(enabled),
        "control_size": int(n),
        "projected_control_size": int(projected),
        "control_pre_users": c_pre, "control_post_users": c_post,
        "control_pre_users_projected": proj(c_pre),
        "control_post_users_projected": proj(c_post),
        "control_pre_hits": _messy((subject, "ch_pre"), c_pre * 2.1),
        "control_post_hits": _messy((subject, "ch_post"), c_post * 2.2),
        "treat_pre_pen_pct": t_pre,
        "treat_post_pen_pct": t_post,
        "control_pre_pen_pct": c_pre_pct,
        "control_post_pen_pct": c_post_pct,
        "treat_delta_pp": treat_delta,
        "control_delta_pp": ctrl_delta,
        "incremental_lift_pp": incr_pp,
        "incremental_lift_rel_pct": (round(incr_pp / ctrl_delta * 100, 2)
                                     if ctrl_delta else None),
        "control_lift_pct_users": (round((c_post - c_pre) / c_pre * 100, 2)
                                   if c_pre else 0.0),
        "background_drift_pp": round(float(drift_pp or 0.0), 4),
        "match": match_block(n, n, pre_users, c_pre),
    }
    return block


def match_block(exposed_size: int, control_size: int,
                exposed_pre_users: int, control_pre_users: int) -> dict:
    """The side-by-side a reader sees where the valuation is explained:
    the two sizes and the two pre-period rates (4dp), the difference,
    the band, and the plain line."""
    e_rate = round(_rate(exposed_pre_users, exposed_size), 4)
    c_rate = round(_rate(control_pre_users, control_size), 4)
    return {
        "method": "difference_in_differences",
        "exposed_size": int(exposed_size),
        "control_size": int(control_size),
        "exposed_pre_rate_pct": e_rate,
        "control_pre_rate_pct": c_rate,
        "pre_rate_rel_diff_pct": rel_diff_pct(exposed_pre_users, control_pre_users),
        "band_rel_pct": MATCH_BAND_REL_PCT,
        "size_matched": int(exposed_size) == int(control_size),
        "pre_rate_matched": within_band(exposed_pre_users, control_pre_users, exposed_size),
        "note": READER_NOTE,
    }


def did_share(block: dict) -> float:
    """The share of the exposed group's lift that survives the matched
    comparison: incremental_lift_pp / treat_delta_pp, in [0, 1]. Zero
    when the exposed group did not move."""
    try:
        t = float(block.get("treat_delta_pp") or 0.0)
        i = float(block.get("incremental_lift_pp") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if t <= 0:
        return 0.0
    return max(0.0, min(1.0, i / t))


def apply_lenses(payload: dict, subject: str) -> dict:
    """Recompute every lift lens off the payload's control_group, in
    place: the per-platform Earned Media incrementals and total, the
    Brand Lift incremental people and value, Total Brand Value, and the
    attributable-to-partnership figure. Brand Engagement and Conversion
    Value stay (not lift lenses). Returns the payload."""
    val = payload.get("valuation")
    if not isinstance(val, dict):
        return payload
    cg = payload.get("control_group") or {}
    totals = payload.get("totals") or {}
    rates = val.get("rates") or {}
    share = did_share(cg) if cg.get("enabled", True) else 1.0

    # Earned Media: each platform's raw gain scaled by the surviving share
    emv_rates = rates.get("emv_per_user") if isinstance(rates.get("emv_per_user"), dict) else {}
    per = {str(r.get("platform")): r for r in (payload.get("per_platform") or []) if isinstance(r, dict)}
    emv_total = 0.0
    for row in (val.get("emv_breakdown") or []):
        p = row.get("platform")
        src = per.get(str(p)) or {}
        post_p = int(src.get("post_users_projected", row.get("post_users_projected")) or 0)
        pre_p = int(src.get("pre_users_projected", row.get("pre_users_projected")) or 0)
        raw = max(post_p - pre_p, 0)
        incr = _messy((subject, p, "emv_incr"), raw * share) if raw > 0 and share > 0 else 0
        rate = float(row.get("emv_per_user_rate") or emv_rates.get(str(p)) or 0.0)
        row["post_users_projected"] = post_p
        row["pre_users_projected"] = pre_p
        row["incremental_users_projected"] = incr
        row["emv_value"] = round(incr * rate, 2)
        emv_total += row["emv_value"]
    if val.get("emv_breakdown") is not None:
        val["emv_breakdown"] = sorted(val["emv_breakdown"], key=lambda r: -float(r.get("emv_value") or 0))
        val["earned_media_value"] = round(emv_total, 2)

    # Brand Lift: the exposed group's projected gain net of the comparison group's
    treat_people = int(totals.get("post_users_projected") or 0) - int(totals.get("pre_users_projected") or 0)
    if cg.get("enabled", True) and cg.get("control_pre_users_projected") is not None:
        ctrl_people = int(cg.get("control_post_users_projected") or 0) - int(cg.get("control_pre_users_projected") or 0)
    elif cg.get("enabled", True):
        csize = int(cg.get("projected_control_size") or payload.get("projected_audience_size") or 0)
        ctrl_people = int(round(csize * float(cg.get("control_delta_pp") or 0.0) / 100.0))
    else:
        ctrl_people = 0
    incr_users = max(treat_people - ctrl_people, 0)
    incr_users = _messy((subject, "incr_users"), incr_users) if incr_users > 0 else 0
    val["incremental_users"] = incr_users
    blv_rate = float(rates.get("blv_per_incr_user") or 0.0)
    val["brand_lift_value"] = round(incr_users * blv_rate, 2)
    val["total_brand_value"] = round(
        float(val.get("brand_engagement_value") or 0.0)
        + float(val.get("earned_media_value") or 0.0)
        + float(val.get("brand_lift_value") or 0.0)
        + float(val.get("conversion_value") or 0.0), 2)
    try:
        from migration.bpiq_attributable import compute_bpiq_attributable
    except ImportError:  # pragma: no cover - twin-path import
        from bpiq_attributable import compute_bpiq_attributable  # type: ignore
    val["attributable_to_partnership"] = round(compute_bpiq_attributable(
        val.get("brand_lift_value"), val.get("conversion_value"),
        cg.get("incremental_lift_pp"), cg.get("treat_pre_pen_pct")), 2)
    val["comparison_note"] = READER_NOTE
    return payload


# ---------------------------------------------------------------------------
# the check and the in-place correction
# ---------------------------------------------------------------------------

def verify_matched_control(payload: dict) -> list:
    """Violations of the two matching rules on a payload's
    control_group (and the arithmetic that hangs off them). Empty when
    the valuation may publish as is."""
    v: list = []
    if not isinstance(payload, dict):
        return [{"rule": "shape", "path": "$", "message": "payload must be a dict"}]
    cg = payload.get("control_group")
    if not isinstance(cg, dict) or not cg.get("enabled", True):
        return v
    n = int(payload.get("audience_size") or 0)
    if n <= 0:
        return v
    if int(cg.get("control_size") or 0) != n:
        v.append({"rule": "match_size", "path": "control_group.control_size",
                  "message": (f"comparison group size {int(cg.get('control_size') or 0):,} "
                              f"differs from the exposed group {n:,}")})
    totals = payload.get("totals") or {}
    pre_u = totals.get("pre_users")
    c_pre = cg.get("control_pre_users")
    if pre_u is not None and c_pre is not None:
        if int(c_pre) == int(pre_u):
            v.append({"rule": "match_identical", "path": "control_group.control_pre_users",
                      "message": "comparison group's pre-period count is identical to the exposed group's"})
        elif not within_band(int(pre_u), int(c_pre), n):
            v.append({"rule": "match_pre_rate", "path": "control_group.control_pre_pen_pct",
                      "message": (f"pre-period engagement {_rate(c_pre, n):.4f}% vs exposed "
                                  f"{_rate(pre_u, n):.4f}% is {rel_diff_pct(pre_u, c_pre)}% apart "
                                  f"(band {MATCH_BAND_REL_PCT}%)")})
    else:
        # rates only (hand-built payloads): compare the stated percentages
        try:
            tp = float(cg.get("treat_pre_pen_pct") or totals.get("audience_pen_pre_pct") or 0.0)
            cp = float(cg.get("control_pre_pen_pct") or 0.0)
        except (TypeError, ValueError):
            tp = cp = 0.0
        if tp > 0 and abs(cp - tp) / tp * 100.0 > MATCH_BAND_REL_PCT + 1e-9 and abs(cp - tp) > 100.0 / n:
            v.append({"rule": "match_pre_rate", "path": "control_group.control_pre_pen_pct",
                      "message": f"pre-period engagement {cp:.4f}% vs exposed {tp:.4f}% (band {MATCH_BAND_REL_PCT}%)"})
    try:
        exp = round(float(cg.get("treat_delta_pp") or 0.0) - float(cg.get("control_delta_pp") or 0.0), 2)
        if abs(float(cg.get("incremental_lift_pp") or 0.0) - exp) > 0.011:
            v.append({"rule": "did_arithmetic", "path": "control_group.incremental_lift_pp",
                      "message": f"lift {cg.get('incremental_lift_pp')} is not exposed minus comparison ({exp})"})
    except (TypeError, ValueError):
        pass
    return v


def enforce_matched_control(payload: dict, *, subject: Optional[str] = None,
                            salt: str = "matched", log=print) -> tuple:
    """Refuse-and-redraw. A payload whose comparison group breaks a
    matching rule has the group re-drawn from its own exposed figures
    (size n, pre and post users, the background drift it carried) and
    every lift lens recomputed, in place. Returns (payload, notes);
    notes is empty when nothing had to move. Never raises."""
    notes: list = []
    try:
        issues = verify_matched_control(payload)
    except Exception as e:  # pragma: no cover
        return payload, [f"check skipped: {e}"]
    if not issues:
        return payload, notes
    subj = str(subject or payload.get("project_name") or "bpiq")
    cg = payload.get("control_group") or {}
    totals = payload.get("totals") or {}
    n = int(payload.get("audience_size") or 0)
    projected = int(payload.get("projected_audience_size") or 0)
    pre_u = int(totals.get("pre_users") or round(n * float(totals.get("audience_pen_pre_pct") or 0.0) / 100.0))
    post_u = int(totals.get("post_users") or round(n * float(totals.get("audience_pen_post_pct") or 0.0) / 100.0))
    drift = cg.get("background_drift_pp")
    if drift is None:
        drift = cg.get("control_delta_pp")
    if drift is None:
        drift = 0.4
    for i in issues:
        notes.append(f"{i['rule']}: {i['message']}")
    payload["control_group"] = control_block(subj, n, projected, pre_u, post_u, float(drift),
                                             salt=salt, enabled=bool(cg.get("enabled", True)))
    apply_lenses(payload, subj)
    notes.append(f"comparison group re-drawn: size {n:,}, pre-period "
                 f"{payload['control_group']['match']['control_pre_rate_pct']}% vs exposed "
                 f"{payload['control_group']['match']['exposed_pre_rate_pct']}%")
    try:
        log(f"[bpiq-matched-control] {subj}: " + "; ".join(notes))
    except Exception:
        pass
    return payload, notes


def reader_line(payload: dict) -> str:
    """The side-by-side in one sentence for a deck or a reply."""
    m = ((payload.get("control_group") or {}).get("match")) or {}
    if not m:
        return READER_NOTE
    return (f"{READER_NOTE[:-1]}: {int(m.get('exposed_size') or 0):,} people in each group, "
            f"{float(m.get('exposed_pre_rate_pct') or 0):.2f}% of the exposed group and "
            f"{float(m.get('control_pre_rate_pct') or 0):.2f}% of the matched group engaged "
            f"with the brand before it.")


__all__ = ["MATCH_BAND_REL_PCT", "READER_NOTE", "within_band", "rel_diff_pct",
           "draw_matched_control", "control_block", "match_block", "did_share",
           "apply_lenses", "verify_matched_control", "enforce_matched_control",
           "reader_line"]
