"""A service that publishes two charts never ships with one of them empty.

The problem this closes
-----------------------
Every two-chart scraper here guards itself with a row-count floor:
Disney+ and Peacock accept a read at 6 rows, Pluto at 12, HBO Max at
any chart rail it recognises. Those floors were written against a read
that comes back short, and they do exactly that job. They cannot see
the other failure, because it does not look short.

Both charts on these services render off ONE page. When the collector
stops walking before the second rail enters the DOM, the first rail
comes back whole. Ten clean rows on a service that publishes twenty
clears a six-row floor comfortably, so the day publishes with one
chart complete and the other absent, and the floor reports health.

`latest/max.json` on 2026-09-25 is the case: the movies chart carried
five titles and the series chart carried none, and nothing in the run
objected. The board did the right thing downstream and carried
Lanterns forward from the day before, which is how a missing rail
shows up as a stale date rather than as an error.

This is the same failure Netflix had at 644b7737 and the same two
answers, lifted out so the four services that publish two charts can
share them rather than each growing its own copy:

  1. Render once more. One rail short is a lazy-render miss, not the
     service publishing an empty chart, and on Netflix the second
     render recovered it immediately.
  2. If it is still short, carry the previous day's rows for the
     MISSING chart only, marked stale. A half-width day is an archive
     day that does not come back; yesterday's order on one rail beside
     today's on the other is the lesser wrong, and the rows say so.

What a caller supplies
----------------------
`expected` is the charts the service declares, named the way its own
rows name them. `key_of` maps a row to that name, and defaults to the
`collection` field every one of these scrapers already stamps. HBO Max
passes its own, because its rail headings come off the page and it
matches them by kind rather than by exact string.

Nothing here reaches S3, renders a page, or decides whether a read is
healthy. It answers which declared charts are absent and merges rows
for them, so the scraper keeps its own floor, its own previous-capture
read and its own publish decision.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

# Stamped on a row carried from an earlier capture. Same field the
# Netflix guard uses, so a reader that already understands one
# understands all of them.
STALE_FIELD = 'stale_from_previous'


def collection_key(row: Any) -> str:
    """The chart a row belongs to, off the field these scrapers stamp."""
    if not isinstance(row, dict):
        return ''
    return str(row.get('collection') or '').strip().lower()


def kind_key(row: Any) -> str:
    """Series or movies, read off whatever the row carries.

    For a service whose rail headings come off the page rather than
    from a constant, so an exact-string match would break the day the
    service reworded a heading. `category_display` is tried first
    because it is the scraper's own classification; the collection
    name is the fallback.
    """
    if not isinstance(row, dict):
        return ''
    for field in ('category_display', 'collection'):
        k = chart_kind(str(row.get(field) or ''))
        if k:
            return k
    return ''


def chart_kind(text: str) -> str:
    """'series', 'movies', or '' for a heading or a category label."""
    t = (text or '').strip().lower()
    if not t:
        return ''
    if 'series' in t or 'show' in t or t == 'tv' or ' tv' in t:
        return 'series'
    if 'movie' in t or 'film' in t:
        return 'movies'
    return ''


def missing_rails(rows: Iterable[Any], expected: Sequence[str], *,
                  key_of: Callable[[Any], str] = collection_key
                  ) -> list[str]:
    """Which declared charts have no rows at all.

    Presence, not depth. A chart that came back at three of ten is a
    short read and belongs to the caller's row-count floor; a chart
    with nothing in it is what this guard is for.
    """
    have = {key_of(r) for r in (rows or [])}
    have.discard('')
    return [name for name in expected
            if str(name).strip().lower() not in have]


def rerender_recovered(rows: Sequence[Any], retry_rows: Sequence[Any],
                       expected: Sequence[str], *,
                       key_of: Callable[[Any], str] = collection_key,
                       label: str = '') -> list[Any]:
    """Best of the two reads, chart by chart.

    A second render that came back with BOTH charts replaces the read
    outright. One that came back with only the chart the first pass
    missed contributes that chart and leaves the rest alone, because a
    retry is a second sample of the same page and not a better one:
    swapping in its version of a rail the first pass already had whole
    would renumber rows for no reason.
    """
    if not retry_rows:
        return list(rows)
    still = missing_rails(rows, expected, key_of=key_of)
    if not still:
        return list(rows)
    recovered = [r for r in retry_rows if key_of(r) in set(still)]
    if not recovered:
        logger.warning("%s: second render did not bring back %s",
                       label or 'chart guard', ', '.join(still))
        return list(rows)
    logger.info("%s: second render recovered %d row(s) for %s",
                label or 'chart guard', len(recovered), ', '.join(still))
    return list(rows) + recovered


def carry_missing(rows: Sequence[Any], prev_rows: Optional[Sequence[Any]],
                  expected: Sequence[str], *,
                  key_of: Callable[[Any], str] = collection_key,
                  label: str = '') -> tuple[list[Any], list[str]]:
    """Fill a still-missing chart from the previous capture.

    Returns the rows to publish and the charts that are STILL absent
    after the carry, which is the caller's signal that the previous
    capture could not cover it either. Only the missing chart is
    touched: a chart that rendered today keeps today's rows, so the
    two never blend.

    Carried rows are copied before they are marked, so a caller that
    holds a reference to the previous snapshot does not find it
    modified underneath.
    """
    out = list(rows)
    missing = missing_rails(out, expected, key_of=key_of)
    if not missing:
        return out, []

    want = set(missing)
    carried_by_chart: dict[str, list[Any]] = {}
    for r in (prev_rows or []):
        k = key_of(r)
        if k in want and isinstance(r, dict):
            carried_by_chart.setdefault(k, []).append(
                dict(r, **{STALE_FIELD: True}))

    unresolved: list[str] = []
    for name in missing:
        carried = carried_by_chart.get(str(name).strip().lower()) or []
        if not carried:
            unresolved.append(name)
            logger.warning(
                "%s: %s chart is empty and the previous capture has no "
                "rows for it either; publishing without that chart",
                label or 'chart guard', name)
            continue
        out.extend(carried)
        logger.warning(
            "%s: %s chart parsed 0 rows; carrying %d row(s) forward from "
            "the previous capture rather than shipping half a day",
            label or 'chart guard', name, len(carried))
    return out, unresolved
