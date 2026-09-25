"""One title on one service reads the same everywhere it is stored.

The problem
-----------
The estimates store can hold three entries for the same title:
`film:<norm>`, `tv:<norm>` and `title:<norm>`. Which one a row reads
is decided at render time by `_annotate_*_with_streams` in
`trends_iq`, which tries the key matching the row's own Film / TV
label and falls back to the other two, first hit wins. The chart pass
wrote to ONE key, built from the kind the CHART says, and that is not
always the key the render reads.

Two faces of the same bug, both live on 2026-09-25 after the pass:

  A row whose only entry is a sibling was DROPPED from the set
  entirely. `researched.get('tv:american horror story official
  podcast')` is None because the bracket created it at `film:`, so
  Disney+ Top 10 Series was sized as eight rows and the podcast kept
  278,915 between neighbours at 2,087,459 and 1,342,966. Five rows
  across three charts were dropped this way, including two the
  terminal bracket had just reasoned values for.

  A row with more than one entry was written on one and READ on the
  other. Netflix's films panel carries a blank `category_display`, so
  the render's order starts at `title:` while the chart pass wrote
  `film:`. The Whisper Man was levelled to 163,313 at `film:whisper
  man` and rendered 1,557,442 off `title:whisper man`, which is why a
  chart that reported zero coherence holds still rendered out of
  order at #6, #8 and #9.

The rule
--------
The chart pass writes to EVERY entry a reader could resolve for that
title, for that service, and creates the service block where an entry
exists without one. Not "retire the siblings": an entry may carry
readings for other services that are perfectly good, and deleting it
would take those with it. Not "change the render's preference"
either, which moves every rail on the board to fix three rows.

One title on one service is one number. Whichever entry any reader
resolves, it gets the same reading, so the question of which sibling
wins stops mattering.

What is preserved
-----------------
`est_basis` survives the write. A bracketed block that gets re-levelled
with its chart stays `est_basis='bracketed'`, so `_audience_state`
still reports it as bracketed and `collect_missing` still targets it
for research tomorrow. Re-levelling a bracket is not the same as
researching it, and the basis is what says so.

Nothing here reaches S3 or reasons anything. It resolves keys and
moves values between entries that already exist.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The kind families a title can be stored under, per key prefix. FAST
# items never carry a `title:` form, which is why the prefix decides
# the family rather than a single shared list. Mirrors the render's
# own orders in `trends_iq._annotate_streaming_with_streams` and
# `_annotate_fast_with_streams`.
_FAMILY = {
    '':      ('film', 'tv', 'title'),
    'fast_': ('film', 'tv'),
}


def entry_key_candidates(prefix: str, kind: str, norm: str) -> list[str]:
    """Every key this title could be stored under, best first.

    `kind` leads, because that is the chart's own claim about the
    title and the entry matching it is the one to prefer when more
    than one exists. The rest follow in the render's order.
    """
    if not norm:
        return []
    fam = _FAMILY.get(prefix, _FAMILY[''])
    order = [k for k in (kind,) if k in fam]
    order += [k for k in fam if k != kind]
    return [f'{prefix}{k}:{norm}' for k in order]


def present(researched: dict, candidates: list[str]) -> list[str]:
    """The candidates that actually have an entry, in candidate order."""
    return [k for k in candidates
            if isinstance((researched or {}).get(k), dict)]


def primary(researched: dict, candidates: list[str]) -> Optional[str]:
    """The entry to treat as the row's own. None when it has none."""
    hit = present(researched, candidates)
    return hit[0] if hit else None


def _block_for(researched: dict, key: str, slug: str) -> Optional[dict]:
    blk = ((researched.get(key) or {}).get('by_platform') or {}).get(slug)
    return blk if isinstance(blk, dict) else None


def reading_for(researched: dict, candidates: list[str],
                slug: str) -> Optional[int]:
    """This service's current reading on the first entry that has one."""
    for k in present(researched, candidates):
        blk = _block_for(researched, k, slug)
        v = (blk or {}).get('us_estimate')
        if isinstance(v, int) and v > 0:
            return v
    return None


def write_across(se, researched: dict, candidates: list[str], slug: str,
                 value: int, salt: str) -> dict:
    """Put `value` on this service, on every entry that could be read.

    Returns `{'set': n, 'created': n, 'keys': [...]}`.

    An entry that already carries a block for the service is moved
    with `_set_platform_reading`, so the item's aggregate follows by
    exactly the delta and its other services are left alone. An entry
    that exists without a block for the service gets one, copied from
    whichever sibling has one so the basis, label and bounds travel,
    which is the case Peacock's The Middle was stuck on: the entry the
    render reads held no Peacock block at all, the chart pass sized
    the row, the write silently did nothing and the row shipped
    carried forward under a chart that had moved.
    """
    out = {'set': 0, 'created': 0, 'keys': []}
    value = max(1, int(value))
    keys = present(researched, candidates)
    if not keys:
        return out

    # A block to model a missing one on. Prefer one for this service
    # so its basis and label are already right.
    template = None
    for k in keys:
        blk = _block_for(researched, k, slug)
        if blk:
            template = blk
            break

    for k in keys:
        it = researched[k]
        blk = _block_for(researched, k, slug)
        if blk is None:
            bp = it.get('by_platform')
            if not isinstance(bp, dict):
                bp = {}
                it['by_platform'] = bp
            seed = dict(template) if template else {}
            seed['us_estimate'] = max(1, int(value))
            # Bounds are rebuilt around the new value rather than
            # carried from the template, whose scale is another
            # title's.
            for f in ('us_estimate_low', 'us_estimate_high'):
                seed.pop(f, None)
            bp[slug] = seed
            if not isinstance(it.get('us_estimate'), int) or \
                    it['us_estimate'] <= 0:
                it['us_estimate'] = seed['us_estimate']
            out['created'] += 1
            out['keys'].append(k)
            continue
        # `est_basis` is the row's provenance, not its value. A
        # bracketed row re-levelled with its chart is still a
        # bracketed row and tomorrow's pass must still target it.
        basis = blk.get('est_basis')
        if se._set_platform_reading(it, slug, value, k, salt):
            out['set'] += 1
            out['keys'].append(k)
        if basis is not None:
            blk['est_basis'] = basis
    return out
