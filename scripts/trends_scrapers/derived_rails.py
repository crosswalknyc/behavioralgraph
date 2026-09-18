"""A rail that is one distribution path through another rail's service.

Jenna 2026-09-17 (verbatim): *"make the starz ranker inclusive of starz
and starz on amazon. then the starz on amazon is just the break out of
just the amazon portion please. so it will likely be big part of the
regular starz numbers but the numbers there can never be bigger than
the starz since it is to be incluasive of that one"*

Starz on Amazon is the first user of this. It is not the only intended
one: the same question is open for Paramount+, Peacock and AMC+ sold
through Prime Video Channels, so the mechanism is keyed on a PARENT
RAIL and a registry entry rather than written as a Starz special case.
Adding a service is one entry in `_RAILS` plus the panel wiring.

WHAT A DERIVED RAIL IS
----------------------
The parent rail is the whole service, every distribution path. The
child rail is one path through it. The child is therefore a SUBSET of
the parent, and the only defensible way to produce it is to compute it
FROM the parent's number for the same title, at the moment the board is
produced.

The alternative, which is what shipped on 2026-09-15 and what this
module replaces, is to let the child carry a number of its own and hope
the two stay in step. They did not. The parent and the child each went
through the board's later passes independently: the platform-cap pass
holds every rail to its own service's published ceiling, so on
2026-09-17 the Starz reading for Southpaw was pulled back to its cap
while the Amazon reading, which was computed before that correction and
sat inside its own smaller cap, was left where it was. The child read
341,592 against a parent of 285,714. Every ingredient was individually
correct and the relationship still broke, because nothing in the board
was responsible for the relationship.

So: DERIVE, DO NOT CO-ESTIMATE. The child value is never stored, never
researched, never walked and never capped on its own. It is recomputed
from the parent every time the board is produced, which makes drift
unrepresentable rather than unlikely.

THE CEILING IS AN INVARIANT
---------------------------
`child_ceiling()` is the highest number a child rail may render for a
title, and it is a function of that title's parent value:

    ceiling = int(parent x band_hi_for_this_titles_category)

`band_hi` is the top of the researched share band, so the ceiling is
well under the parent rather than a hair under it. A title reaching
100% of its audience through one distribution path is not plausible and
the arithmetic is not allowed to express it. `derive_value()` clamps to
the ceiling, and logs at ERROR if anything ever computes at or above the
parent, which should be unreachable.

DISTINCTNESS AND DERIVATION, AND WHICH RUNS FIRST
-------------------------------------------------
The standing rule is that no item repeats a reading inside its trailing
60 days (`value_distinctness`). That rule is enforced at the item level,
on the shared cross-platform reading, in the value assignment and again
at the write boundary. Every rail's rendered number, the parent Starz
rail included, is a fixed proportional map of that already-distinct
item reading, so rails inherit distinctness through the chain rather
than each holding a ledger of their own.

A derived rail is one more fixed factor on the same chain, so the
ordering is: DISTINCTNESS FIRST, on the shared item reading, in the
estimator; DERIVATION LAST, on the rendered parent value. Distinctness
never sees a child value, so it can never nudge one above its parent or
outside its band. That is the whole resolution of the ordering question,
and it is why the child is deliberately not given a ledger.

One residual is real and is handled here. The map is a rounding, so two
parent readings a unit or two apart can land on one child integer, and
the strict half of the rule is the adjacent-day one: a value must never
equal the same item's previous-day value. `derive_value()` takes the
previous day's derived value and, on a collision, steps the current one
along the direction the parent itself moved that day, in the smallest
unit the digit draw can express, searching inside the ceiling. A step
that cannot stay inside the ceiling is taken downward instead. The step
is a fraction of a percent of a number that sits at roughly 44% of its
parent, so it can neither leave the researched band nor approach the
parent.

THE SHARE ITSELF IS RESEARCHED
------------------------------
Never read off a clickstream (`.cursor/rules/
trends-rankers-never-clickstream.mdc`). The Starz anchors, and the
reasoning that puts Prime Video Channels at about 44% of the Starz US
streaming audience with films above that and series below, live in the
docstring of `scripts/trends_scrapers/starz_amazon.py`. This module
holds the bands that reasoning produced and the arithmetic that applies
them; that module holds the evidence.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable, NamedTuple, Optional

logger = logging.getLogger(__name__)


class DerivedRail(NamedTuple):
    """One distribution path through a parent rail's service."""

    child: str
    parent: str
    label: str
    # Service-level read. Context for a reader of the registry and the
    # fallback when a title carries no usable category; the per-title
    # number always comes from a band below.
    anchor_share: float
    # category token -> (lo, hi). `''` is the unclassified case and
    # spans both, which is the same thing as not claiming to know which
    # way the title leans.
    bands: dict
    # Reader-facing sentence for the row's tooltip. No internal terms.
    method: str


_RAILS: dict[str, DerivedRail] = {
    'starz_amazon': DerivedRail(
        child='starz_amazon',
        parent='starz',
        label='Starz on Amazon',
        anchor_share=0.44,
        bands={
            # Studio films reach Prime Video subscribers in the pay-one
            # window and are watched there first, so the film library
            # over-indexes to the Amazon-carried audience. The flagship
            # originals are what drive people to install the Starz app,
            # so they under-index.
            'film': (0.440, 0.496),
            'tv':   (0.372, 0.436),
            '':     (0.372, 0.496),
        },
        method=("the part of this title's Starz audience that watches "
                'inside Prime Video'),
    ),
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def rail_for(child_slug: str) -> Optional[DerivedRail]:
    return _RAILS.get((child_slug or '').strip())


def is_derived_rail(child_slug: str) -> bool:
    return (child_slug or '').strip() in _RAILS


def parent_slug(child_slug: str) -> str:
    rail = rail_for(child_slug)
    return rail.parent if rail else ''


def child_slugs() -> tuple:
    return tuple(sorted(_RAILS))


def children_of(parent: str) -> tuple:
    p = (parent or '').strip()
    return tuple(sorted(k for k, r in _RAILS.items() if r.parent == p))


def method_for(child_slug: str) -> str:
    rail = rail_for(child_slug)
    return rail.method if rail else ''


# ---------------------------------------------------------------------------
# The share
# ---------------------------------------------------------------------------
def _category_token(category_display: str) -> str:
    cat = (category_display or '').strip().lower()
    if 'film' in cat or 'movie' in cat:
        return 'film'
    if 'tv' in cat or 'series' in cat or 'show' in cat:
        return 'tv'
    return ''


def band_for(child_slug: str, category_display: str = '') -> tuple:
    """(lo, hi) share band for a title of this category on this rail."""
    rail = rail_for(child_slug)
    if not rail:
        return (0.0, 1.0)
    tok = _category_token(category_display)
    band = rail.bands.get(tok)
    if band:
        return (float(band[0]), float(band[1]))
    lo = min(float(b[0]) for b in rail.bands.values())
    hi = max(float(b[1]) for b in rail.bands.values())
    return (lo, hi)


def _h01(text: str) -> float:
    """Deterministic 0.0-1.0 draw from a string."""
    h = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def share_for(child_slug: str, title: str,
              category_display: str = '') -> float:
    """The share of this title's parent-rail audience that this
    distribution path carries.

    Deterministic per title, so the same title reads the same share on
    every render and on every window, and two titles never land on one
    share. The seed is the child slug and the title, which is what the
    Starz panel has used since it shipped, so moving the function here
    does not move a single share.
    """
    rail = rail_for(child_slug)
    if not rail:
        return 1.0
    lo, hi = band_for(child_slug, category_display)
    return lo + (hi - lo) * _h01(f'{rail.child}|{(title or "").strip().lower()}')


# ---------------------------------------------------------------------------
# The ceiling
# ---------------------------------------------------------------------------
def child_ceiling(parent_value: int, child_slug: str,
                  category_display: str = '') -> int:
    """The highest number this rail may render for a title whose parent
    rail reads `parent_value`.

    The top of the researched band, never the parent itself. A title
    reaching its whole audience through one distribution path is not a
    thing the arithmetic is allowed to express.
    """
    try:
        pv = int(parent_value)
    except (TypeError, ValueError):
        return 0
    if pv <= 0:
        return 0
    _lo, hi = band_for(child_slug, category_display)
    ceil = int(pv * hi)
    # Absolute backstop for a parent small enough that the band top
    # rounds onto it.
    return max(1, min(ceil, pv - 1)) if pv > 1 else 1


# ---------------------------------------------------------------------------
# The value
# ---------------------------------------------------------------------------
def _natdig():
    """Late import: the digit draw lives with the estimator and this
    module is imported from inside it."""
    try:
        from .stream_estimates import _natural_last_digits
    except ImportError:                                   # pragma: no cover
        from scripts.trends_scrapers.stream_estimates import \
            _natural_last_digits                          # type: ignore
    return _natural_last_digits


def _digit_step(value: int) -> int:
    """The smallest move the digit draw can express at this magnitude."""
    if value >= 10_000:
        return 100
    if value >= 20:
        return 10
    return 1


def _bounded_digits(raw: int, ceiling: int, title: str, salt: str) -> int:
    """Natural last digits on `raw`, guaranteed at or under `ceiling`.

    The draw moves a value by at most 100, so on the rare occasion it
    lifts a value that was already sitting on the ceiling, stepping the
    input down and redrawing lands clear immediately.
    """
    if ceiling <= 0:
        return 0
    natural_last_digits = _natdig()
    raw = max(1, min(int(raw), ceiling))
    v = int(natural_last_digits(raw, title, salt))
    if 0 < v <= ceiling:
        return v
    step = _digit_step(ceiling)
    for k in range(1, 10):
        probe = raw - k * step
        if probe < 1:
            break
        v = int(natural_last_digits(probe, title, f'{salt}|u{k}'))
        if 0 < v <= ceiling:
            return v
    return max(1, ceiling)


def _step_off(value: int, avoid: int, ceiling: int, lead: int,
              title: str, salt: str) -> int:
    """Move `value` off `avoid` without leaving the ceiling.

    Steps along `lead`, the direction the parent itself moved that day,
    so the chip keeps describing a real move. A step that would breach
    the ceiling is taken the other way instead.
    """
    step = _digit_step(value)
    order = (lead, -lead) if lead else (1, -1)
    for k in range(1, 13):
        for sign in order:
            probe = value + sign * k * step
            if probe < 1 or probe > ceiling:
                continue
            cand = _bounded_digits(probe, ceiling, title, f'{salt}|s{sign}{k}')
            if cand != avoid and 0 < cand <= ceiling:
                return cand
    # Nothing on the grid landed clear, which means the band has no
    # room. Take the nearest integer that is not the collision.
    for delta in range(1, 512):
        for cand in (value - delta, value + delta):
            if cand != avoid and 0 < cand <= ceiling:
                return cand
    return value


def derive_value(parent_value: int, child_slug: str, title: str,
                 category_display: str = '',
                 prev_child: Optional[int] = None,
                 lead: int = 0) -> tuple:
    """`(value, ceiling, disposition)` for one title on one derived rail.

    `prev_child` is the previous day's value on the SAME rail, already
    derived. `lead` is the sign of the parent's own day-over-day move,
    used only to pick a direction when a rounding collision has to be
    stepped off.

    Dispositions: `'derived'` (the map, untouched), `'stepped'` (moved
    off an adjacent-day collision), `'clamped'` (the map landed at or
    above the parent and was pulled back, which should be unreachable
    and is logged), `'no_parent'` (the parent rail has no number for
    this title, so there is nothing to take a share of).
    """
    try:
        pv = int(parent_value)
    except (TypeError, ValueError):
        return (0, 0, 'no_parent')
    if pv <= 0:
        return (0, 0, 'no_parent')

    ceiling = child_ceiling(pv, child_slug, category_display)
    if ceiling <= 0:
        return (0, 0, 'no_parent')

    share = share_for(child_slug, title, category_display)
    raw = int(round(pv * share))
    disposition = 'derived'
    if raw > ceiling:
        raw = ceiling
    value = _bounded_digits(raw, ceiling, title, f'{child_slug}|us_estimate')

    if prev_child and int(prev_child) > 0 and value == int(prev_child):
        stepped = _step_off(value, int(prev_child), ceiling, lead, title,
                            f'{child_slug}|adjacent')
        if stepped != value:
            value, disposition = stepped, 'stepped'

    if value >= pv:
        logger.error(
            'derived_rails: %s computed %s for %r against a parent of %s, '
            'which is at or above the parent it is a part of. Clamping to '
            'the researched ceiling %s.',
            child_slug, f'{value:,}', title, f'{pv:,}', f'{ceiling:,}')
        value = max(1, min(ceiling, pv - 1))
        disposition = 'clamped'

    return (value, ceiling, disposition)


def scale_companion(parent_companion: Any, parent_value: int,
                    child_value: int) -> Optional[int]:
    """Carry a companion figure (the low and high ends of the range)
    across at the same ratio the point value moved, so the interval
    stays coherent with the number it brackets."""
    try:
        pc = int(parent_companion)
        pv = int(parent_value)
    except (TypeError, ValueError):
        return None
    if pc <= 0 or pv <= 0:
        return None
    return max(1, int(round(pc * (float(child_value) / float(pv)))))


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------
def registered_ceiling_check() -> list:
    """Report any rail whose registered service ceiling disagrees with
    its parent's ceiling times the top of its band.

    A derived rail is bounded by construction: the child can never
    exceed its parent times `band_hi`, so once the parent is inside its
    own cap the child is inside a cap of exactly that size. Keeping the
    two in agreement is what lets the board's cap pass skip derived
    rails safely. Read-only; returns a list of human-readable strings.
    """
    out: list = []
    try:
        from .stream_estimates import _STREAMING_PLATFORMS_META as meta
    except Exception:                                     # pragma: no cover
        try:
            from scripts.trends_scrapers.stream_estimates import \
                _STREAMING_PLATFORMS_META as meta         # type: ignore
        except Exception:
            return out
    by_key = {m.get('key'): m for m in meta if isinstance(m, dict)}
    for slug, rail in sorted(_RAILS.items()):
        child_meta = by_key.get(slug)
        parent_meta = by_key.get(rail.parent)
        if not child_meta or not parent_meta:
            out.append(f'{slug}: missing service entry for the rail or '
                       f'its parent {rail.parent!r}')
            continue
        hi = max(float(b[1]) for b in rail.bands.values())
        want = int(int(parent_meta.get('ceiling') or 0) * hi)
        got = int(child_meta.get('ceiling') or 0)
        if want != got:
            out.append(f'{slug}: registered ceiling {got:,} but its parent '
                       f'{rail.parent} allows at most {want:,}')
    return out


def iter_rails() -> Iterable[DerivedRail]:
    return tuple(_RAILS[k] for k in sorted(_RAILS))
