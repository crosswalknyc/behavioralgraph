"""A valuation lands exactly on a figure the reader already holds
(Jenna 2026-11-11, Samantha Kolton at CAA).

The read she holds put the Gap x JYT partnership at $4,217,683 for
September 22 to November 11, 2026. The valuation, held to that read,
came out at $4,129,457.18: under it, never on it. Two answers to one
question. When the same pairing and the same window already carry a
figure given to the reader, the valuation's total is that figure, to
the cent, and every component is recomputed so each page still adds
up.

How, with nothing but the product's own arithmetic:

1. `land(payload, target, rebuild, salt)` finds the one share of the
   research at which the total sits just under the figure (the same
   rebuild the hold uses: every count moves together, rates and shares
   do not move).
2. `settle(payload, target, salt)` closes what is left, a fraction of
   a percent: the collection's orders step down until the cents can be
   met, then the people each channel added move by the few it takes,
   the largest channels first. The lenses are recomputed by
   `bpiq_matched_control.apply_lenses`, so earned media is still the
   sum of its rows, each row is still people times its rate, and the
   total is still the sum of the four lenses.

3. Every lens is a count times a rate, so the counts can only carry
   the total in steps of a few cents (two, with the rates on file). A
   figure that sits between two steps (an odd cent) is met all the
   same: the counts land on the step just under it and the cents they
   cannot carry go on the largest component (the largest lens; inside
   earned media, its largest channel, so the table still adds). The
   payload names them in `valuation.held_cents` and
   `bpiq_matched_control.apply_lenses` carries them through every
   recompute, so the rule has no exception (2026-11-11).

A valuation whose research comes in under the figure is left where it
is: nothing is raised to meet a figure (the standing rule is to sit low).
Fail-safe: anything that cannot be met exactly returns the payload it
was given and says so in the record.
"""
from __future__ import annotations

import copy
import math
from functools import reduce
from typing import Callable, Optional

try:
    from migration import bpiq_matched_control as _mc
except ImportError:  # pragma: no cover - twin-path import
    import bpiq_matched_control as _mc  # type: ignore

MAX_GAP_SHARE = 0.03          # what settle will close; past this the share was wrong, not the cents
MAX_ORDER_STEPS = 120
FINE_SPAN = 6                 # people a row may move, either way, in the last step
FINE_ROWS = 7
MAX_HELD_CENTS = 99           # the cents the counts cannot carry: always under a dollar
MAX_TRIES = 8                 # steps under the figure tried before giving up
_LENSES = ("earned_media_value", "brand_engagement_value", "brand_lift_value", "conversion_value")


def _cents(v) -> int:
    return int(round(float(v or 0) * 100))


def total_of(payload) -> float:
    try:
        return float(((payload or {}).get("valuation") or {}).get("total_brand_value") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _share(payload) -> float:
    cg = payload.get("control_group") or {}
    return _mc.did_share(cg) if cg.get("enabled", True) else 1.0


def _row_cents(row, src, post, share, salt) -> int:
    """What a channel's earned media comes to, in cents, with `post`
    projected people after launch (the same arithmetic apply_lenses runs)."""
    pre = int(src.get("pre_users_projected", row.get("pre_users_projected")) or 0)
    raw = max(int(post) - pre, 0)
    if row.get("earned_direct"):
        incr = raw
    else:
        incr = _mc._messy((salt, row.get("platform"), "emv_incr"), raw * share) if raw > 0 and share > 0 else 0
    return _cents(incr * float(row.get("emv_per_user_rate") or 0.0))


def _set_orders(p, orders: int, salt) -> None:
    """The collection's orders at `orders`: the conversion lens, the
    drop and its line follow."""
    val = p["valuation"]
    cv = p.get("conversions") or {}
    aov = float((val.get("rates") or {}).get("conv_value_per_user") or 0.0)
    old = int(cv.get("post_users_projected") or 0)
    if old <= 0 or orders == old:
        return
    weight = float(p.get("projected_audience_size") or 0) / float(p.get("audience_size") or 1) or 1.0
    ratio = float(orders) / float(old)
    cv["post_users_projected"] = int(orders)
    pre_u = int(cv.get("pre_users") or 0)
    cv["post_users"] = max(_mc._messy((salt, "exact", "cv_post"), orders / weight), pre_u + 1)
    cv["post_hits"] = _mc._messy((salt, "exact", "cvh_post"), cv["post_users"] * 1.4)
    cv["low_signal"] = cv["post_users"] < 25
    cv["lift_pct_users"] = round((cv["post_users"] - pre_u) / pre_u * 100, 2) if pre_u else 0.0
    value = round(orders * aov, 2)
    val["conversion_value"] = value
    camp = p.get("campaign") if isinstance(p.get("campaign"), dict) else {}
    drop = camp.get("drop") if isinstance(camp.get("drop"), dict) else None
    if drop and int(drop.get("orders") or 0) > 0:
        drop["units_us"] = max(int(orders), _mc._messy((salt, "exact", "units"), float(drop.get("units_us") or orders) * ratio))
        if drop.get("units_per_sku"):
            drop["units_per_sku"] = round(float(drop["units_per_sku"]) * ratio, 1)
        drop["orders"] = int(orders)
        drop["revenue_usd"] = value
        if "units_us" in cv:
            cv["units_us"] = drop["units_us"]
        if "revenue_usd" in cv:
            cv["revenue_usd"] = value
        line = str(camp.get("drop_line") or "")
        head = (f"About {int(drop['units_us']):,} pieces sold in the US, {int(orders):,} orders, "
                f"${value:,.0f} at the collection's prices.")
        tail = line.split("prices.", 1)[1] if "prices." in line else ""
        camp["drop_line"] = head + tail


def _set_row(p, platform, post: int) -> None:
    """A channel's projected people after launch at `post`; its panel
    count, lift and penetration follow."""
    weight = float(p.get("projected_audience_size") or 0) / float(p.get("audience_size") or 1) or 1.0
    for row in p.get("per_platform") or []:
        if str(row.get("platform")) != str(platform):
            continue
        row["post_users_projected"] = int(post)
        on_post = int(row.get("post_users_on_platform") or 0)
        pre_u = int(row.get("pre_users") or 0)
        users = max(pre_u + 1, int(round(post / weight)))
        row["post_users"] = min(on_post, users) if on_post else users
        row["lift_pct_users"] = round((row["post_users"] - pre_u) / pre_u * 100, 2) if pre_u else 0.0
        if on_post:
            row["post_pen_pct"] = round(row["post_users"] / on_post * 100, 2)
        return


def _strip_held_cents(p) -> None:
    """Take a payload's held cents back off, so the counts are settled
    from what the counts themselves come to."""
    val = p.get("valuation")
    if not isinstance(val, dict):
        return
    adj = val.pop("held_cents", None)
    if isinstance(adj, dict) and adj.get("lens") in ("brand_engagement_value", "conversion_value"):
        try:
            val[adj["lens"]] = round(float(val.get(adj["lens"]) or 0.0) - int(adj.get("cents") or 0) / 100.0, 2)
        except (TypeError, ValueError):
            pass


def _place_cents(p, cents: int, salt) -> dict:
    """`cents` on the largest component: the largest lens, and inside
    earned media its largest channel. Returns what was placed."""
    val = p["valuation"]
    lens = max(_LENSES, key=lambda k: _cents(val.get(k)))
    adj = {"lens": lens, "cents": int(cents)}
    if lens == "earned_media_value":
        rows = [r for r in (val.get("emv_breakdown") or []) if isinstance(r, dict) and _cents(r.get("emv_value")) > 0]
        if rows:
            adj["platform"] = str(max(rows, key=lambda r: _cents(r.get("emv_value"))).get("platform"))
    elif lens in ("brand_engagement_value", "conversion_value"):
        val[lens] = round(float(val.get(lens) or 0.0) + cents / 100.0, 2)     # these two are not recomputed by the lenses
    val["held_cents"] = adj
    _mc.apply_lenses(p, salt)
    return adj


def settle(payload: dict, target: float, salt: str) -> tuple:
    """(payload, record). The payload with its total on `target` to the
    cent, or the payload as given with record['set_exactly'] False when
    the gap is too wide. The counts carry the figure when they can;
    cents they cannot carry (a figure between two steps of the counts,
    an odd cent) go on the largest component. The input is never
    modified."""
    base = copy.deepcopy(payload)
    _strip_held_cents(base)
    _mc.apply_lenses(base, salt)
    T = _cents(target)
    C = _cents((base.get("valuation") or {}).get("total_brand_value"))
    rec = {"set_exactly": False, "target_usd": round(float(target), 2), "total_before_settle_usd": C / 100.0}
    if T <= 0 or abs(T - C) > T * MAX_GAP_SHARE:
        rec["reason"] = "gap too wide to close"
        return payload, rec
    tries, why = 0, ""
    for held in range(0, MAX_HELD_CENTS + 1):
        p, srec = _settle_counts(copy.deepcopy(base), T - held, salt)
        if not srec.get("set_exactly"):
            why = srec.get("reason") or why
            if srec.get("reason") != "the cents cannot be met":
                tries += 1                      # a full pass that missed; the parity misses cost nothing
                if tries >= MAX_TRIES or srec.get("reason") == "no channel to settle on":
                    break
            continue
        rec.update(srec)
        if held:
            rec["held_cents"] = _place_cents(p, held, salt)
            val = p["valuation"]
            if _cents(val.get("total_brand_value")) != T or sum(_cents(val.get(k)) for k in _LENSES) != T:
                rec["set_exactly"] = False
                why = "the recomputed total missed the figure"
                continue
        rec["total_after_usd"] = T / 100.0
        return p, rec
    rec["reason"] = why or "the cents cannot be met"
    return payload, rec


def _settle_counts(p: dict, T: int, salt: str) -> tuple:
    """(payload, record) with the total on `T` cents by moving counts
    only: the collection's orders and the people each channel added.
    `p` is the caller's own copy, lenses already applied."""
    val = p.get("valuation") or {}
    C = _cents(val.get("total_brand_value"))
    rec = {"set_exactly": False}
    payload = p
    if C == T:
        rec["set_exactly"] = True
        rec["total_after_usd"] = T / 100.0
        return p, rec
    share = _share(p)
    per = {str(r.get("platform")): r for r in (p.get("per_platform") or []) if isinstance(r, dict)}
    rows = [r for r in (val.get("emv_breakdown") or [])
            if not r.get("earned_direct") and str(r.get("platform")) in per
            and float(r.get("emv_per_user_rate") or 0) > 0 and int(r.get("incremental_users_projected") or 0) > 0]
    if not rows:
        rec["reason"] = "no channel to settle on"
        return payload, rec
    rate_c = {str(r["platform"]): _cents(r["emv_per_user_rate"]) for r in rows}
    g = reduce(math.gcd, rate_c.values())
    need = T - C                                   # cents the channels and the orders must add (or give up)

    # the orders: step down until the cents the channels can carry are met
    cv = p.get("conversions") or {}
    orders0 = int(cv.get("post_users_projected") or 0)
    aov_c = _cents((val.get("rates") or {}).get("conv_value_per_user"))
    cv_now = _cents(val.get("conversion_value"))
    floor = max(int(cv.get("pre_users_projected") or 0) + 1, 1)
    orders = None
    if aov_c > 0 and orders0 > 0 and cv_now == orders0 * aov_c:
        for d in range(0, MAX_ORDER_STEPS + 1):
            o = orders0 - d
            if o < floor:
                break
            if (need + d * aov_c) % g == 0:
                orders = o
                break
    elif need % g == 0:
        orders = orders0
    if orders is None:
        rec["reason"] = "the cents cannot be met"
        return payload, rec
    if orders != orders0:
        _set_orders(p, orders, salt)
        need += (orders0 - orders) * aov_c

    # the channels: the gap in proportion to each one's value, then the last cents on the largest
    posts = {k: int(per[k].get("post_users_projected") or 0) for k in rate_c}
    cur = {str(r["platform"]): _row_cents(r, per[str(r["platform"])], posts[str(r["platform"])], share, salt) for r in rows}
    by_name = {str(r["platform"]): r for r in rows}
    total_now = sum(cur.values()) or 1
    need0 = need

    def _move(name, step):
        nonlocal need
        if step == 0:
            return
        new_post = max(int(per[name].get("pre_users_projected") or 0) + 1, posts[name] + step)
        new_c = _row_cents(by_name[name], per[name], new_post, share, salt)
        need -= (new_c - cur[name])
        posts[name], cur[name] = new_post, new_c

    for name in sorted(cur, key=lambda k: -cur[k]):
        _move(name, int((need0 * cur[name] / total_now) / max(rate_c[name] * (share or 1.0), 1)))
    # what the proportions left over goes on the largest channels, a rate at a time
    for _ in range(4):
        for name in sorted(cur, key=lambda k: -cur[k]):
            if abs(need) < rate_c[name]:
                continue
            _move(name, int(need / max(rate_c[name] * (share or 1.0), 1)))
        if abs(need) <= max(rate_c.values()) * 2:
            break
    order = sorted(cur, key=lambda k: -cur[k])
    names = order[:FINE_ROWS]
    # a channel whose rate carries the odd cents must be in the last step
    if reduce(math.gcd, [rate_c[n] for n in names]) != g:
        for n in order[FINE_ROWS:]:
            if reduce(math.gcd, [rate_c[x] for x in names[:-1] + [n]]) == g:
                names = names[:-1] + [n]
                break
    opts = {}
    for n in names:
        o = {}
        for d in range(-FINE_SPAN, FINE_SPAN + 1):
            post = posts[n] + d
            if post <= int(per[n].get("pre_users_projected") or 0):
                continue
            o[d] = _row_cents(by_name[n], per[n], post, share, salt) - cur[n]
        opts[n] = o
    half = len(names) // 2
    left, right = names[:half], names[half:]

    def combos(group):
        out = {0: ()}
        for n in group:
            nxt = {}
            for tot, ds in out.items():
                for d, c in opts[n].items():
                    key = tot + c
                    cand = ds + (d,)
                    if key not in nxt or sum(abs(x) for x in cand) < sum(abs(x) for x in nxt[key]):
                        nxt[key] = cand
            out = nxt
        return out

    lc, rc = combos(left), combos(right)
    best = None
    for tot, ds in lc.items():
        other = rc.get(need - tot)
        if other is None:
            continue
        cost = sum(abs(x) for x in ds) + sum(abs(x) for x in other)
        if best is None or cost < best[0]:
            best = (cost, ds, other)
    if best is None:
        rec["reason"] = "the last cents cannot be met"
        return payload, rec
    for n, d in list(zip(left, best[1])) + list(zip(right, best[2])):
        posts[n] += d
    for n, post in posts.items():
        if post != int(per[n].get("post_users_projected") or 0):
            _set_row(p, n, post)
    _mc.apply_lenses(p, salt)
    val = p["valuation"]
    lenses = sum(_cents(val.get(k)) for k in _LENSES)
    if _cents(val.get("total_brand_value")) != T or lenses != T:
        rec["reason"] = "the recomputed total missed the figure"
        return payload, rec
    rec.update({"set_exactly": True, "total_after_usd": T / 100.0, "orders": int((p.get("conversions") or {}).get("post_users_projected") or 0)})
    return p, rec


def land(payload: dict, target: float, rebuild: Callable, salt: str,
         people_ceiling: Optional[float] = None, factor: Optional[float] = None) -> tuple:
    """(payload, record). `rebuild(f)` is the valuation with every count
    at the share `f` of the research. The share is the one that puts the
    total just under `target`; `settle` closes the rest. `factor` replays
    a share saved with the run. Pulls down only: research already at or
    under the figure is settled where it stands when the gap is small,
    and left alone otherwise."""
    T = float(target or 0)
    total0 = total_of(payload)
    rec = {"set_exactly": False, "target_usd": round(T, 2), "total_before_usd": round(total0, 2),
           "people_before": int(round(float(payload.get("projected_audience_size") or 0)))}
    if T <= 0 or total0 <= 0:
        return payload, rec
    held = payload
    f = 1.0
    if total0 > T + 0.005:
        f = float(factor) if factor and 0.0 < float(factor) < 1.0 else (T / total0) * 0.9995
        for _ in range(10):
            held = rebuild(f)
            over_people = bool(people_ceiling) and float(held.get("projected_audience_size") or 0) > float(people_ceiling)
            if total_of(held) <= T and not over_people:
                break
            if factor:
                break
            f *= min(T / max(total_of(held), 1.0), 1.0) * 0.9995 if total_of(held) > T else 0.994
    if f >= 1.0 and abs(total0 - T) > 0.005:
        # the research sits under the figure: left where it is, nothing is raised to meet it
        rec["reason"] = "the research is under the figure"
        return payload, rec
    out, srec = settle(held, T, salt)
    rec.update(srec)
    rec["factor"] = round(f, 6)
    rec["total_after_usd"] = round(total_of(out), 2)
    rec["people_after"] = int(round(float(out.get("projected_audience_size") or 0)))
    if not rec.get("set_exactly"):
        return (held if total_of(held) <= T + 0.005 else payload), rec
    return out, rec


__all__ = ["land", "settle", "total_of"]
