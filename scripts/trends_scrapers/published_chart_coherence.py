"""Make the numbers agree with the chart the service published.

Jenna 2026-09-23, verbatim: *"our numbers should align so that they do
descend down based on that ranking does that make sense"*.

Where a service publishes a ranked list, that list is authoritative
and immutable (see `_PUBLISHED_CHARTS` in `stream_estimates`). This
module is the other half of that bargain: the reading each row carries
has to be consistent with the position the service gave it, so the
rail reads down the page instead of asking the reader to hold two
orderings in their head at once.

Two things are enforced, and only two.

**Descent across the published block.** If the service ranks a title
#2, more people watched it than the title they rank #3 and fewer than
the one they rank #1. That is not our opinion, it is what publishing
a chart means. Residual inversions are corrected with the smallest
move that removes them.

**Containment at the boundary.** A title the service does NOT chart
cannot out-draw the title it ranks last. If it could, the service
would have charted it. This is the same shape as the FAST rule that a
channel is never smaller than the largest title it airs, read the
other way round, and it is what stops position 11 landing above
position 1.

What this module deliberately will NOT do
-----------------------------------------
It will not scale a published block up to meet its tail, and it will
not crush a tail to fit under its chart. Either one buys an ordering
at the price of the levels meaning anything, and a rail whose levels
are decorative is worse than one that is out of order, because the
disorder is at least visible.

So the correction is bounded. A row moves by at most
`_MAX_MOVE_FRACTION`, and only the rows that actually break a rule
move at all: a tail row already below the chart is not touched, which
is what keeps deep catalog levels intact and stops a seam forming at
the boundary. When a rail cannot be reconciled inside those bounds the
pass says so and leaves it alone, because at that point the two blocks
were reasoned against different anchors and the answer is to reason
them again, not to paper over it here.

No ladders
----------
Forcing descent invites evenly spaced values, and an arithmetic
progression is a forensic signature we spent weeks removing. Every
separation this module makes is drawn per (rail, title, day) so the
gaps vary, and `audit_gap_uniformity` measures what came out rather
than trusting that it worked. Natural last digits are re-applied to
every value we touch, so the roughly one-in-ten readings that end in
zero keep doing so and `run_guard.check_last_digit_distribution` stays
passing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How far a single reading may be moved to satisfy the chart. Past
# this the value has stopped being the one that was reasoned for the
# title, so the rail is reported instead of forced.
_MAX_MOVE_FRACTION = 0.35

# A rail where most of the tail sits above its own chart is not a
# rail with a few inversions, it is two blocks on different scales.
_MAX_TAIL_BREACH_SHARE = 0.40

# Separation between neighbouring published positions, as a fraction
# of the higher value. Drawn per item inside this band so the gaps
# vary and no constant delta appears.
_SEP_MIN = 0.015
_SEP_MAX = 0.060


def _lazy():
    try:
        from .stream_estimates import _h01, _ensure_non_zero_last_digit
    except ImportError:
        from scripts.trends_scrapers.stream_estimates import (
            _h01, _ensure_non_zero_last_digit)
    return _h01, _ensure_non_zero_last_digit


def _natural(value: int, key: str, salt: str) -> int:
    """Re-apply natural last digits after we move a value.

    Delegates to the estimator's own helper, which draws the last
    digit from the natural distribution (about one in ten readings
    ends in zero) rather than banning zeros, per the 2026-09-09
    amendment that retired the trailing-zero ban.
    """
    _h01, natural_digits = _lazy()
    try:
        return max(1, int(natural_digits(int(value), key, salt)))
    except Exception:
        return max(1, int(value))


def _natural_under(value: int, limit: Optional[int], key: str,
                   salt: str) -> int:
    """Natural last digits, but never above `limit`.

    Re-applying the digit can round a value UP, and a value one unit
    over its platform's daily cap is not a rounding detail: the render
    clamps anything above the cap and replaces it with the title's own
    last reading, which is usually far lower and much older. That is
    how a Tubi chart measured clean here arrived on the board with its
    #1 reading 175,919 against a #2 of 857,110, an inversion at the
    very top of the rail, from an 18-unit overshoot.

    So walk down until it fits, keeping the digit natural rather than
    truncating to a flat number, which would plant a round value at
    exactly the cap on every rail that reaches it.
    """
    iv = _natural(value, key, salt)
    if not limit or iv <= limit:
        return iv
    for guard in range(24):
        iv = _natural(max(1, limit - guard), key, f'{salt}|cap{guard}')
        if iv <= limit:
            return iv
    return max(1, limit - 1)


def _pava(values: list[float], weights: Optional[list[float]] = None
          ) -> list[float]:
    """Pool adjacent violators: the closest non-increasing sequence.

    Standard isotonic regression, which is what makes this the
    SMALLEST adjustment that removes the inversions rather than one
    shape imposed over the researched numbers. Pools come out flat and
    are separated afterwards.
    """
    n = len(values)
    if n < 2:
        return list(values)
    w = list(weights or [1.0] * n)
    lvl: list[float] = []
    wt: list[float] = []
    cnt: list[int] = []
    for i in range(n):
        lvl.append(float(values[i]))
        wt.append(float(w[i]))
        cnt.append(1)
        while len(lvl) > 1 and lvl[-2] < lvl[-1]:
            v2, w2, c2 = lvl.pop(), wt.pop(), cnt.pop()
            v1, w1, c1 = lvl.pop(), wt.pop(), cnt.pop()
            tot = w1 + w2
            lvl.append((v1 * w1 + v2 * w2) / tot if tot else (v1 + v2) / 2)
            wt.append(tot)
            cnt.append(c1 + c2)
    out: list[float] = []
    for v, c in zip(lvl, cnt):
        out.extend([v] * c)
    return out


def _separate(values: list[float], keys: list[str], salt: str
              ) -> list[float]:
    """Make a non-increasing sequence strictly decreasing.

    Walks down the list and, wherever a value has caught up with the
    one above it, drops it by a fraction drawn for that specific item
    and day. The draw is what keeps the gaps irregular: a fixed step
    here would produce exactly the ladder this module exists to avoid.
    """
    _h01, _ = _lazy()
    out = list(values)
    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            continue
        span = _SEP_MAX - _SEP_MIN
        frac = _SEP_MIN + _h01(f'{salt}|{keys[i]}|sep') * span
        out[i] = out[i - 1] * (1.0 - frac)
    return out


def _solve_descending(orig: list[float], target: list[float],
                      keys: list[str], salt: str
                      ) -> tuple[Optional[list[float]], int]:
    """A strictly decreasing sequence inside each value's move budget.

    Two constraints at once: every reading stays within
    `_MAX_MOVE_FRACTION` of the number that was reasoned for it, and
    the sequence descends across the published positions. That is a
    feasibility problem, not a clamp, and an earlier draft of this
    module got it wrong by clamping after ordering: clipping a value
    back into its box put it above its neighbour again, and the rail
    shipped with fresh inversions the pass had introduced itself.

    Walk down the list keeping a running ceiling. At each position
    the value may be no higher than its own budget allows and no
    higher than a drawn step below the value above it. If that
    ceiling falls under the position's own floor the rail cannot
    descend inside the budget, and the caller is told rather than
    handed a forced answer.

    First attempt sits each value as close to `target` (the isotonic
    fit) as the ceiling permits, which keeps the researched shape.
    If that runs out of room lower down, a second attempt takes the
    highest value allowed at every step, which is the most room the
    budget can possibly leave. Only when that also fails is the rail
    infeasible.
    """
    n = len(orig)
    if n == 0:
        return [], 0
    lo = [o * (1.0 - _MAX_MOVE_FRACTION) if o > 0 else 0.0 for o in orig]
    hi = [o * (1.0 + _MAX_MOVE_FRACTION) if o > 0 else 0.0 for o in orig]
    _h01, _ = _lazy()
    steps = [_SEP_MIN + _h01(f'{salt}|{keys[i]}|sep') * (_SEP_MAX - _SEP_MIN)
             for i in range(n)]

    for greedy_high in (False, True):
        out: list[float] = []
        ok = True
        ceiling = None
        for i in range(n):
            cap = hi[i] if ceiling is None else min(
                hi[i], ceiling * (1.0 - steps[i]))
            if cap < lo[i]:
                ok = False
                break
            v = cap if greedy_high else min(cap, max(lo[i], target[i]))
            out.append(v)
            ceiling = v
        if ok:
            moved = sum(1 for o, v in zip(orig, out)
                        if abs(v - o) > max(1.0, o * 0.0005))
            return out, moved
    return None, 0


def count_inversions(values: list[int]) -> int:
    """Adjacent pairs that read the wrong way round."""
    return sum(1 for i in range(1, len(values))
               if values[i] is not None and values[i - 1] is not None
               and values[i] >= values[i - 1])


def audit_gap_uniformity(values: list[int]) -> dict:
    """Describe the spacing so a ladder would be visible in a report.

    `cv` is the coefficient of variation of the gap between
    neighbouring values. A perfect arithmetic progression scores 0.
    Anything organic sits well clear of it, and `max_equal_run` counts
    the longest stretch of identical gaps, which is the other way a
    ladder shows up.
    """
    vals = [v for v in values if isinstance(v, int) and v > 0]
    if len(vals) < 3:
        return {'n': len(vals), 'cv': None, 'max_equal_run': 0}
    gaps = [vals[i - 1] - vals[i] for i in range(1, len(vals))]
    mean = sum(gaps) / len(gaps)
    if mean == 0:
        return {'n': len(vals), 'cv': 0.0, 'max_equal_run': len(gaps)}
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    cv = (var ** 0.5) / abs(mean)
    run = best = 1
    for i in range(1, len(gaps)):
        run = run + 1 if gaps[i] == gaps[i - 1] else 1
        best = max(best, run)
    return {'n': len(vals), 'cv': round(cv, 4), 'max_equal_run': best,
            'gap_min': min(gaps), 'gap_max': max(gaps),
            'gap_mean': round(mean, 1)}


def reconcile_rail(rows: list[dict], *, salt: str,
                   get_value, set_value,
                   ceiling: Optional[int] = None) -> dict:
    """Bring one service's rail into agreement with its own chart.

    `rows` are the rail in the order it will render: the published
    block first, in the service's order, then the reasoned tail.
    `get_value(row)` and `set_value(row, v)` read and write the
    reading, so the caller decides whether that is the per-platform
    block, the aggregate, or both.

    Returns a report. Mutates nothing when the rail cannot be
    reconciled inside the move budget.
    """
    report: dict[str, Any] = {
        'published': 0, 'tail': 0, 'moved': 0,
        'inversions_before': 0, 'inversions_after': 0,
        'boundary_before': None, 'boundary_after': None,
        'held': False, 'reason': '',
    }
    pub, tail = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        v = get_value(r)
        if not isinstance(v, int) or v <= 0:
            continue
        (pub if isinstance(r.get('published_rank'), int) else tail).append(r)
    pub.sort(key=lambda r: r['published_rank'])
    tail.sort(key=lambda r: -(get_value(r) or 0))
    report['published'], report['tail'] = len(pub), len(tail)
    if not pub:
        report['reason'] = 'no published chart on this rail'
        return report

    pub_vals = [float(get_value(r)) for r in pub]
    pub_keys = [str(r.get('title') or i) for i, r in enumerate(pub)]
    report['inversions_before'] = count_inversions(
        [int(v) for v in pub_vals])

    adj, _n_moved = _solve_descending(
        pub_vals, _pava(pub_vals), pub_keys, salt)
    if adj is None:
        report['held'] = True
        report['reason'] = (
            f'this rail cannot descend across its {len(pub)} published '
            f'positions without moving a reading more than '
            f'{int(_MAX_MOVE_FRACTION * 100)}% from the one reasoned '
            f'for it, so the block was reasoned against a different '
            f'anchor than its order implies and needs reasoning again')
        return report

    # Boundary: nothing the service left off its chart may out-draw
    # the title it ranks last.
    cap = adj[-1] if adj else None
    breach = [r for r in tail if cap and (get_value(r) or 0) >= cap]
    report['boundary_before'] = (
        (int(cap), int(get_value(tail[0]))) if (cap and tail) else None)
    if cap and tail and len(breach) > len(tail) * _MAX_TAIL_BREACH_SHARE:
        report['held'] = True
        report['reason'] = (
            f'{len(breach)} of {len(tail)} unranked titles read above '
            f'the chart\'s last position, so the two blocks sit on '
            f'different scales and the levels need reasoning again '
            f'rather than reconciling here')
        return report

    # Commit the published block first, because the cap the tail has
    # to sit under is the number that actually ships at the last
    # published position, not the one before natural last digits were
    # re-applied. An earlier draft measured the breach against the
    # pre-rounding value and left a handful of tail rows one or two
    # above the chart, which is the whole defect in miniature.
    # The ceiling CASCADES down the positions rather than being applied
    # to each independently. Clamping row by row puts every position
    # that wants more than the cap at exactly the cap, and the natural
    # last digit then decides their order: Tubi's top three came out at
    # 857,105 / 857,110 / 857,118, ascending, because all three wanted
    # the cap and the digit draw is per-title. A chart whose top is
    # against its platform's ceiling still has to descend, so each
    # position's real bound is the lower of the platform cap and a step
    # below whatever the position above it actually committed to.
    _h01, _ = _lazy()
    moved = 0
    prev_committed: Optional[float] = None
    for i, (r, before, after) in enumerate(zip(pub, pub_vals, adj)):
        limit = float(ceiling) if ceiling else None
        if prev_committed is not None:
            sep = _SEP_MIN + _h01(f'{salt}|capsep|{i}') * (_SEP_MAX - _SEP_MIN)
            step = prev_committed * (1.0 - sep)
            limit = step if limit is None else min(limit, step)
        if limit is not None:
            after = min(after, limit)
        iv = _natural_under(int(round(after)),
                            int(limit) if limit is not None else None,
                            str(r.get('title') or ''), f'{salt}|pub')
        if iv != int(before):
            set_value(r, iv)
            moved += 1
        prev_committed = float(get_value(r) or iv)

    cap = int(get_value(pub[-1]) or 0)
    breach = [r for r in tail if (get_value(r) or 0) >= cap] if cap else []
    if cap and tail and len(breach) > len(tail) * _MAX_TAIL_BREACH_SHARE:
        report['held'] = True
        report['reason'] = (
            f'{len(breach)} of {len(tail)} unranked titles read above '
            f"the chart's last position, so the two blocks sit on "
            f'different scales and the levels need reasoning again '
            f'rather than reconciling here')
        report['moved'] = moved
        return report

    _h01, _ = _lazy()
    if cap and breach:
        # Only the rows that break containment move, and they are
        # placed in a band just under the cap keeping their order
        # relative to each other. Every tail row already below the
        # cap is untouched, which leaves deep catalog levels alone and
        # stops a step forming at the seam.
        head = cap * (1.0 - (_SEP_MIN + _h01(f'{salt}|boundary') *
                             (_SEP_MAX - _SEP_MIN)))
        below = [(get_value(r) or 0) for r in tail
                 if (get_value(r) or 0) < cap]
        floor_v = max(below) if below else head * 0.55
        span = max(head - floor_v, head * 0.12)
        n = len(breach)
        for i, r in enumerate(breach):
            frac = (i + 1) / (n + 1)
            wob = 0.85 + _h01(f'{salt}|{r.get("title")}|bwob') * 0.30
            want = max(1.0, head - span * frac * wob)
            if ceiling:
                want = min(want, float(ceiling))
            # Under BOTH bounds: the chart floor it must not reach, and
            # the platform ceiling it must not exceed.
            limit = min(cap - 1, int(ceiling)) if ceiling else cap - 1
            iv = _natural_under(int(round(want)), max(1, limit),
                                str(r.get('title') or ''), f'{salt}|tail')
            if iv != (get_value(r) or 0):
                set_value(r, iv)
                moved += 1

    report['moved'] = moved
    final_pub = [get_value(r) for r in pub]
    report['inversions_after'] = count_inversions(final_pub)
    report['gaps'] = audit_gap_uniformity(final_pub)
    if cap and tail:
        tail.sort(key=lambda r: -(get_value(r) or 0))
        report['boundary_after'] = (int(get_value(pub[-1])),
                                    int(get_value(tail[0])))
    return report
