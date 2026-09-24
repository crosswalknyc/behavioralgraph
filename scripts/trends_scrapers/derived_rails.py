"""A rail that is one distribution path through another rail's service.

Jenna 2026-09-17 (verbatim): *"make the starz ranker inclusive of starz
and starz on amazon. then the starz on amazon is just the break out of
just the amazon portion please. so it will likely be big part of the
regular starz numbers but the numbers there can never be bigger than
the starz since it is to be incluasive of that one"*

Starz on Amazon was the first user of this, which is why the mechanism
is keyed on a PARENT RAIL and a registry entry rather than written as
a Starz special case. Paramount+ on Amazon joined it on 2026-09-22.
Adding a service is one entry in `_RAILS` plus the panel wiring.

WHICH SERVICES GET A RAIL IS A RESEARCH QUESTION, NOT A CODE ONE.
`scripts/trends_scrapers/carriage_mix.py` holds the US distribution
mix of every service on the Streaming tab, with the evidence behind
each, and marks the ones whose Amazon split is published. Only those
get an entry here. Several services are certainly sold through Prime
Video Channels and still have no rail, because nobody publishes their
split and a share we invented would not be the product. Read that
file before adding anything to `_RAILS`.

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

THE TITLE'S PLACE IN THE BAND IS REASONED TOO (2026-09-24)
----------------------------------------------------------
On the rails added for HBO Max, Peacock, BritBox and MGM+ the position
a title takes inside its band is not a hash draw. It is a per-title
lean reasoned in `scripts/trends_scrapers/carriage_leans.py` from what
is known about who watches that title and how they reach the service:
catalog film and library TV that an older Prime Video audience adds as
a channel sit high in the band, buzzy originals that drive direct app
sign-ups sit low. The lean is stored with its reasoning and read here;
the daily movement rides on top of it. That is what lets a title sit
above another on the breakout while sitting below it on the parent,
which a real distribution path does and a constant multiple never can.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
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
    # True when the title's place inside its band is REASONED per title
    # (`carriage_leans.py`) rather than drawn from the title hash. The
    # Starz and Paramount+ rails predate that pass and keep the draw
    # they shipped with, so nothing that shipped moves; every rail
    # added from 2026-09-24 reasons its titles.
    reasoned: bool = False


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
    # 2026-09-22. The second rail to earn an entry, and the test it
    # had to pass was the one stated under the registry: a published
    # PER-SERVICE split, not a category average.
    #
    # Antenna, Q1 2025, reported service by service: Paramount+ takes
    # 30% of its subscriptions through Amazon Prime Video and 39%
    # direct, the balance across the app stores, the other channels
    # storefronts and operator deals. Two corrections bring that
    # subscription share down to an audience share. Antenna measures
    # a base that excludes MVPD and telco distribution and some
    # bundles, and Paramount+ carries a large bundled base outside
    # that frame, chiefly Paramount+ Essential inside Walmart+, so
    # widening the denominator to the whole US base lowers Amazon's
    # share of it. Amazon-sold subscriptions also churn faster than
    # direct ones, so a viewing share reads under a subscription
    # share again. That puts the service-level read near 27%.
    #
    # The per-title spread is the same shape as the Starz one and for
    # the same reason. The Paramount pay-one film window (Mission:
    # Impossible, A Quiet Place, Sonic) lands in front of Prime Video
    # subscribers who are already in that app, so the film library
    # over-indexes to the Amazon-carried audience. What drives
    # somebody to install the Paramount+ app itself is the flagship
    # series and the live NFL and Star Trek windows, so series
    # under-index.
    #
    # Evidence and the full working live in
    # `scripts/trends_scrapers/carriage_mix.py`.
    'paramountplus_amazon': DerivedRail(
        child='paramountplus_amazon',
        parent='paramountplus',
        label='Paramount+ on Amazon',
        anchor_share=0.27,
        bands={
            'film': (0.270, 0.318),
            'tv':   (0.228, 0.268),
            '':     (0.228, 0.318),
        },
        method=("the part of this title's Paramount+ audience that "
                'watches inside Prime Video'),
    ),
    # 2026-09-24 (Jenna: "now we need to add a on amazon for hbo max,
    # peacock, britbox, mgm+ again, not formulaic so that it can ever
    # look synthetic or be tracked as fake"). Four rails, one class of
    # evidence: none of these services publishes an Amazon split, so
    # each band is BRACKETED from published quantities around the
    # service (its disclosed US base, Antenna's event deltas and
    # category frames, the tier actually on the storefront, the size
    # of the operator-bundled base) and stated as a range with its
    # working in `carriage_mix.py`. The film band sits above the series
    # band on every one of them for the reason it does on Starz and
    # Paramount+: a service's film library lands in front of Prime
    # Video subscribers who are already in that app, while the
    # originals are what make somebody install the service's own app.
    #
    # `reasoned=True` on all four: the position of each title inside
    # its band is reasoned title by title in `carriage_leans.py` (older
    # audiences, catalog film and library TV over-index on the Amazon
    # path; buzzy originals that drive direct sign-ups under-index),
    # with a per-title spread so no two titles share a position and the
    # daily movement on top, so no reader can divide one rail by the
    # other and recover a number.
    'max_amazon': DerivedRail(
        child='max_amazon',
        parent='max',
        label='HBO Max on Amazon',
        anchor_share=0.13,
        bands={
            'film': (0.130, 0.160),
            'tv':   (0.100, 0.134),
            '':     (0.100, 0.160),
        },
        method=("the part of this title's HBO Max audience that watches "
                'inside Prime Video'),
        reasoned=True,
    ),
    # Peacock is small and recent by construction: only the ad-free
    # tier is on the storefront, and only since August 2025. It must
    # never be given a Starz-sized share. The band is 1.6% to 3.4%.
    'peacock_amazon': DerivedRail(
        child='peacock_amazon',
        parent='peacock',
        label='Peacock on Amazon',
        anchor_share=0.024,
        bands={
            'film': (0.024, 0.034),
            'tv':   (0.016, 0.026),
            '':     (0.016, 0.034),
        },
        method=("the part of this title's Peacock audience that watches "
                'inside Prime Video'),
        reasoned=True,
    ),
    # BritBox is the one most likely to be over-read on Amazon. The
    # band is built by correcting Antenna's specialty category figure
    # DOWN for the other storefronts, BritBox's measured direct base
    # and channels churn, not by adopting it.
    'britbox_amazon': DerivedRail(
        child='britbox_amazon',
        parent='britbox',
        label='BritBox on Amazon',
        anchor_share=0.49,
        bands={
            'film': (0.480, 0.550),
            'tv':   (0.440, 0.520),
            '':     (0.440, 0.550),
        },
        method=("the part of this title's BritBox audience that watches "
                'inside Prime Video'),
        reasoned=True,
    ),
    # MGM+ is Amazon-owned and the Channels path is the largest single
    # storefront path, but the majority of the base arrives through
    # cable carriage, so the Amazon slice of the whole is a minority.
    'mgmplus_amazon': DerivedRail(
        child='mgmplus_amazon',
        parent='mgmplus',
        label='MGM+ on Amazon',
        anchor_share=0.26,
        bands={
            'film': (0.260, 0.320),
            'tv':   (0.210, 0.264),
            '':     (0.210, 0.320),
        },
        method=("the part of this title's MGM+ audience that watches "
                'inside Prime Video'),
        reasoned=True,
    ),
}

# How far a title's share may move from one day to the next, as a
# fraction of the width of its band.
#
# Jenna 2026-09-22: a breakout rail shows "a share that varies day to
# day rather than sitting on a constant". Before this the share was
# drawn once per title and held for every render, so the child moved
# only because the parent moved and the RELATIONSHIP between them was
# a fixed number a reader could recover by dividing one rail by the
# other. A distribution path does not behave that way: which surface
# a title is watched on shifts with what else is in front of those
# viewers that day.
#
# The draw stays deterministic per (title, day) and the title's own
# base share stays the centre of it, so a title keeps its identity
# across days and two titles still never land on one share. The
# wobble is clamped inside the researched band, so no day can push a
# title outside the evidence, and the band top is still what sets the
# ceiling. At 0.30 the day's share sits within 15% of the band width
# either side of the title's base, which on the Starz film band is
# under two percent of the share in relative terms: visible movement,
# well inside the research.
_DAY_WOBBLE = 0.30

# NOT EVERY AMAZON-CARRIED SERVICE BELONGS HERE, and the test is not
# "is it sold on Amazon Channels".
#
# A rail earns an entry only when BOTH of these hold:
#
#   1. The parent rail is the WHOLE service across every distribution
#      path and the child is one path through it, because that is the
#      only arrangement in which the child can sit strictly below the
#      parent. Starz qualifies: it has its own app, its own storefront
#      sales, MVPD-sold OTT, and Prime Video Channels, so the Amazon
#      path is a real proper subset. Paramount+ qualifies the same
#      way.
#   2. The split is PUBLISHED per service, OR published quantities
#      around the service bracket a BAND tight enough to state with
#      its working (the 2026-09-24 class: HBO Max, Peacock, MGM+,
#      BritBox). A category average on its own is not a per-service
#      share and is never used as one; where it enters at all it is
#      corrected DOWN for what is known about the service, which is
#      how the BritBox band was built.
#
# One service carried on Amazon still fails the second test and
# deliberately has no rail: AMC+ (four storefronts plus a large
# operator path, an unnamed 18% customer in the 10-K that cannot be
# read as the Amazon line, and the specialty category figure Antenna
# itself flags as overstated would overstate Amazon badly). The reason
# is in `carriage_mix._HELD_BREAKOUTS` in one line and in its `basis`
# in full. Its main rail's scope line still states that the Amazon
# path is included, so the whole-service number is explicit. What is
# missing is a defensible number for the slice, not the knowledge that
# the slice exists.
#
# MovieSphere+ (added to the Streaming tab 2026-09-22) deliberately
# has no entry. It is not sold as an app of its own; its US carriage
# is Prime Video Channels plus YouTube Primetime Channels, and the
# only US package JustWatch lists for it is the Amazon one, which is
# what its panel is built from. A "MovieSphere+ on Amazon" rail would
# therefore be all or nearly all of its parent, and `child_ceiling`
# exists precisely to make a child that equals its parent
# unrepresentable. The MovieSphere+ panel already IS the
# Amazon-carried service and its scope label says so; there is
# nothing left to break out. See
# `scripts/trends_scrapers/moviesphereplus.py`.
#
# Lionsgate+ (added to the Streaming tab the same day) deliberately
# has no entry either, and for the same reason stated even more
# plainly by the service itself: its own FAQ answers "how do I
# subscribe" with "add it as an additional channel to Amazon Prime
# Video" and "how do I cancel" with amazon.com/yms. There is no app
# and no second storefront, so its entire US audience already is its
# Amazon audience, and a "Lionsgate+ on Amazon" rail would be 100% of
# its parent on the first render. Do not be misled by the Starz
# lineage: Lionsgate+ is the brand Starz used internationally, and
# Starz does have a legitimate Amazon child, but they are separate
# companies and separate services now, and the child of one is not
# evidence for a child of the other. See
# `scripts/trends_scrapers/lionsgateplus.py`.


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


# ---------------------------------------------------------------------------
# Reasoned per-title position (2026-09-24)
# ---------------------------------------------------------------------------
# On a `reasoned=True` rail the place a title takes inside its band is
# a LEAN reasoned for that title in `carriage_leans.py`: a number in
# [-1, 1] where +1 is a title whose audience reaches the service
# overwhelmingly through Prime Video Channels (older-skewing catalog
# film, library TV an Amazon browser lands on) and -1 is a title whose
# audience installs the service's own app for it (a buzzy original
# that drives direct sign-ups). The lean is produced once per title by
# the rail's nightly module and stored with its reasoning at
# `s3://dashboard-inputs/trends_iq_snapshots/carriage_leans/<child>.json`;
# this module only READS it. A title with no lean yet (it arrived on
# the catalog between two nightly runs) takes the title-hash draw
# until the next run reasons it, so the board never waits on a call.
#
# `DERIVED_RAIL_LEANS` in the environment selects the source: unset or
# `s3` reads the bucket, a directory path reads `<dir>/<child>.json`
# (tests), and `off` disables the pass so every rail uses the draw.
_LEANS_BUCKET = 'dashboard-inputs'
_LEANS_PREFIX = 'trends_iq_snapshots/carriage_leans'
_LEANS_TTL_S = 3600.0
_leans_cache: dict = {}
_leans_lock = threading.Lock()


def lean_key(title: str) -> str:
    """Fold a title for the leans file: casefold, punctuation to
    space, one space, no leading article. The same fold the board uses
    to match a title across sources."""
    t = re.sub(r'[^a-z0-9 ]+', ' ', (title or '').casefold())
    t = re.sub(r'\s+', ' ', t).strip()
    if t.startswith('the '):
        t = t[4:]
    return t


def leans_s3_key(child_slug: str) -> str:
    return f'{_LEANS_PREFIX}/{child_slug}.json'


def _leans_source() -> str:
    return (os.environ.get('DERIVED_RAIL_LEANS') or 's3').strip()


def _load_leans_uncached(child_slug: str) -> dict:
    src = _leans_source()
    if src.lower() in ('off', '0', 'none', 'false'):
        return {}
    try:
        if src.lower() == 's3':
            import boto3
            s3 = boto3.client('s3', region_name='us-east-2')
            o = s3.get_object(Bucket=_LEANS_BUCKET,
                              Key=leans_s3_key(child_slug))
            doc = json.loads(o['Body'].read().decode('utf-8'))
        else:
            path = os.path.join(src, f'{child_slug}.json')
            if not os.path.exists(path):
                return {}
            with open(path, encoding='utf-8') as fh:
                doc = json.load(fh)
    except Exception as e:
        logger.info('derived_rails: no leans for %s (%s)', child_slug,
                    type(e).__name__)
        return {}
    titles = (doc or {}).get('titles') if isinstance(doc, dict) else None
    out: dict = {}
    for k, v in (titles or {}).items():
        try:
            lean = float(v.get('lean') if isinstance(v, dict) else v)
        except (TypeError, ValueError, AttributeError):
            continue
        out[lean_key(k)] = max(-1.0, min(1.0, lean))
    return out


def title_leans(child_slug: str) -> dict:
    """{lean_key: lean} for a rail, cached per process for an hour.
    Empty for a rail that does not reason its titles."""
    rail = rail_for(child_slug)
    if not rail or not rail.reasoned:
        return {}
    now = time.time()
    with _leans_lock:
        hit = _leans_cache.get(child_slug)
        if hit and now - hit[0] < _LEANS_TTL_S:
            return hit[1]
    leans = _load_leans_uncached(child_slug)
    with _leans_lock:
        _leans_cache[child_slug] = (now, leans)
    return leans


def reset_leans_cache() -> None:
    with _leans_lock:
        _leans_cache.clear()


def title_lean(child_slug: str, title: str) -> Optional[float]:
    """The reasoned lean for one title on one rail, or None."""
    return title_leans(child_slug).get(lean_key(title))


def _position_from_lean(lean: float, child: str, title: str) -> float:
    """Map a lean in [-1, 1] to a place in (0, 1) inside the band.

    Linear in the lean so the reasoning is what orders titles, then a
    small per-title spread so two titles the reasoning put at the same
    lean still never share a position, reflected off the ends so the
    spread cannot push a title onto a band edge (the same reflection
    `share_for` uses for the daily move, and for the same reason).
    """
    u = 0.5 + 0.44 * max(-1.0, min(1.0, float(lean)))
    u += (_h01(f'{child}|{lean_key(title)}|lean-spread') - 0.5) * 0.10
    lo, hi = 0.015, 0.985
    while u < lo or u > hi:
        if u > hi:
            u = 2 * hi - u
        if u < lo:
            u = 2 * lo - u
    return u


def base_share_for(child_slug: str, title: str,
                   category_display: str = '') -> float:
    """The title's own place inside its band, with no day in it.

    Deterministic per title, so a title keeps one recognisable level
    across days and two titles never land on one share. On a rail that
    reasons its titles the place is the title's lean (see above); on
    every other rail, and for a title not yet reasoned, the seed is
    the child slug and the title, which is what the Starz panel has
    used since it shipped, so no share moved when this was split out
    of `share_for`.
    """
    rail = rail_for(child_slug)
    if not rail:
        return 1.0
    lo, hi = band_for(child_slug, category_display)
    lean = title_lean(child_slug, title) if rail.reasoned else None
    if lean is not None:
        return lo + (hi - lo) * _position_from_lean(lean, rail.child, title)
    return lo + (hi - lo) * _h01(f'{rail.child}|{(title or "").strip().lower()}')


def share_for(child_slug: str, title: str,
              category_display: str = '',
              day_iso: str = '') -> float:
    """The share of this title's parent-rail audience that this
    distribution path carries on `day_iso`.

    With no day given this is exactly `base_share_for`, which is what
    every caller outside the board gets and what the Starz panel has
    always used. With a day it is that base moved by a deterministic
    draw of at most `_DAY_WOBBLE` of the band width, clamped inside
    the band, so the share itself moves from one day to the next
    instead of the child being a fixed multiple of its parent.
    """
    rail = rail_for(child_slug)
    if not rail:
        return 1.0
    lo, hi = band_for(child_slug, category_display)
    base = base_share_for(child_slug, title, category_display)
    day = (day_iso or '').strip()
    if not day or hi <= lo:
        return base
    key = f'{rail.child}|{(title or "").strip().lower()}|{day}|carriage'
    moved = base + (_h01(key) - 0.5) * (hi - lo) * _DAY_WOBBLE

    # REFLECT off the band edges, never clamp to them. Clamping was
    # the first version and it pins: two titles whose base share sits
    # near the top of the band both get pushed onto exactly `hi` on a
    # day the draw runs high, and identical values across titles are
    # the one thing the pipeline rules forbid outright. Reflection
    # keeps the whole draw inside the researched band and keeps
    # distinct inputs distinct, because it is piecewise linear with
    # slope one rather than flat.
    #
    # The wobble is at most 15% of the band width either side, so a
    # single reflection always lands back inside; the loop is a
    # formality that also covers a future wobble wide enough to need
    # two.
    while moved < lo or moved > hi:
        if moved > hi:
            moved = 2 * hi - moved
        if moved < lo:
            moved = 2 * lo - moved
    return moved


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


def step_clear(value: int, taken: set, ceiling: int, lead: int,
               title: str, salt: str) -> int:
    """Move `value` off every integer in `taken` without leaving the
    ceiling. Same-day, cross-title.

    The map from parent to child is a rounding, so on a rail whose
    band is narrow and whose parent tail is small (HBO Max at 10-16%
    of a 10,000 reading lands in a 600-integer window for a hundred
    titles) two titles can land on one child integer on the same day.
    Identical values across titles are the one thing the pipeline rules
    forbid outright, so the board's derived-rail pass hands each rail's
    rendered values through here. Steps along `lead` first, in the
    digit draw's own unit, so the move is the size of a day's wobble
    and the share stays inside the research.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return value
    if v not in taken:
        return v
    step = _digit_step(v)
    order = (lead, -lead) if lead else (1, -1)
    for k in range(1, 40):
        for sign in order:
            probe = v + sign * k * step
            if probe < 1 or probe > ceiling:
                continue
            cand = _bounded_digits(probe, ceiling, title, f'{salt}|c{sign}{k}')
            if cand not in taken and 0 < cand <= ceiling:
                return cand
    for delta in range(1, 4096):
        for cand in (v - delta, v + delta):
            if 0 < cand <= ceiling and cand not in taken:
                return cand
    return v


def derive_value(parent_value: int, child_slug: str, title: str,
                 category_display: str = '',
                 prev_child: Optional[int] = None,
                 lead: int = 0,
                 day_iso: str = '') -> tuple:
    """`(value, ceiling, disposition)` for one title on one derived rail.

    `prev_child` is the previous day's value on the SAME rail, already
    derived. `lead` is the sign of the parent's own day-over-day move,
    used only to pick a direction when a rounding collision has to be
    stepped off. `day_iso` is the day the parent's number is about, and
    moves the share inside its band so the relationship between child
    and parent is not a constant a reader could divide out.

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

    share = share_for(child_slug, title, category_display, day_iso)
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
