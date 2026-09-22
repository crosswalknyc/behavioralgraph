"""
Shared shape for FAST channel lineups that come from a platform's own
public guide API rather than from a MediaBiz workbook.

The four original FAST platforms (Roku, Tubi, Pluto, Amazon) get their
Channel Ranker lineup from `build_fast_channel_lineups.py`, which parses
the "Stream Metric Schedules" xlsx workbooks. Those workbooks are an
ad-hoc human-in-the-loop delivery: one row per airing, aggregated to an
airings-per-week count per channel.

Vizio WatchFree+, LG Channels and MyFree DIRECTV have no workbook. Each
publishes its own guide, so each gets a scraper that emits the SAME
per-source block shape the workbook builder emits:

    {'service': str, 'total_airings': int, 'channels': [
        {'name', 'airings', 'content_type', 'source_genre',
         'scope', 'dma_zip_count'}, ...]}

`airings` is always AIRINGS PER WEEK, because that is the unit the
research prompt quotes back and the unit the four workbook platforms
already carry (median ~200/wk). A guide API publishes a much shorter
horizon than a week - Vizio's runs about 22 hours ahead, LG's about 13
- so the count has to be converted from the span actually covered
before it can sit in the same column. `weekly_airings` is that
conversion and it is the only place it happens.

Getting that conversion backwards is the cadence trap that has bitten
this board before: a short-window count written straight into a weekly
column reads as a channel airing seven times less than it does.

Channels whose guide carries no schedule at all (live passthrough
feeds: a sports channel showing one open-ended block, and every
MyFree DIRECTV channel, since DirecTV publishes a lineup and not a
schedule) report `airings = 0`. Zero means "no schedule signal", not
"a channel that never airs anything", and the research collector
drops the airings clause from the prompt rather than telling the
model a channel airs nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

HOURS_PER_WEEK = 168.0

# Below this much observed coverage the extrapolation to a week is
# noise rather than a rate, so the channel reports no signal instead
# of a number we would not defend. Two hours of guide is one or two
# programmes; multiplying that by 84 invents a weekly cadence.
MIN_COVERED_HOURS = 2.0


def weekly_airings(count: int, covered_hours: float) -> int:
    """Airings per week implied by `count` airings observed across
    `covered_hours` of published guide.

    Returns 0 when there is nothing to extrapolate from, which the
    collector reads as "no schedule signal for this channel".
    """
    try:
        n = int(count)
        hours = float(covered_hours)
    except (TypeError, ValueError):
        return 0
    if n <= 0 or hours < MIN_COVERED_HOURS:
        return 0
    return int(round(n * HOURS_PER_WEEK / hours))


def covered_hours(starts_epoch_s: Iterable[float],
                   ends_epoch_s: Iterable[float]) -> float:
    """Hours between the earliest start and the latest end in a
    channel's published guide. This is the channel's OWN coverage, not
    the window we asked for: a channel whose guide runs dry after six
    hours is measured across six hours, so its weekly rate is not
    depressed by the emptier part of the request window."""
    starts = [s for s in starts_epoch_s if s]
    ends = [e for e in ends_epoch_s if e]
    if not starts or not ends:
        return 0.0
    span = (max(ends) - min(starts)) / 3600.0
    return span if span > 0 else 0.0


def channel_row(name: str, *, airings: int = 0, content_type: str = '',
                 source_genre: str = '', scope: str = 'national',
                 dma_zip_count: int = 0) -> dict[str, Any]:
    """One Channel Ranker row.

    scope:
      'national' - available to every viewer on the platform.
      'local'    - carried only in named markets. The research step
                   prices these against the markets they actually
                   reach, never against the national platform, so the
                   ZIP count rides along as the size of that carriage.
    source_genre:
      The platform's OWN category label for the channel (Vizio
      'EN ESPANOL', LG 'Westerns', DirecTV 'National Sports'). Used as
      an input to the channel-type classification in
      `fast_channel_genres`, which maps it onto the fixed 15-type
      taxonomy. Carried rather than rendered.
    """
    return {
        'name':          (name or '').strip(),
        'airings':       max(0, int(airings or 0)),
        'content_type':  (content_type or '').strip(),
        'source_genre':  (source_genre or '').strip(),
        'scope':         scope if scope in ('national', 'local') else 'national',
        'dma_zip_count': max(0, int(dma_zip_count or 0)),
    }


def lineup_payload(service: str, channels: list[dict],
                    *, extra: Optional[dict] = None) -> dict[str, Any]:
    """Snapshot body for one API-sourced FAST platform.

    Sorted by airings desc so the block matches what the workbook
    builder writes, which is what `stream_estimates._collect_fast_
    channels` assumes when it derives a cost tier from list
    position. Channels with no schedule signal keep a stable
    alphabetical order at the tail instead of an arbitrary one, so a
    platform that publishes no schedule at all (MyFree DIRECTV) does
    not reshuffle its whole ranker between runs for no reason.
    """
    rows = [c for c in channels if c.get('name')]
    rows.sort(key=lambda c: (-int(c.get('airings') or 0),
                              (c.get('name') or '').lower()))
    total = sum(int(c.get('airings') or 0) for c in rows)
    scheduled = sum(1 for c in rows if int(c.get('airings') or 0) > 0)
    local = sum(1 for c in rows if c.get('scope') == 'local')
    body: dict[str, Any] = {
        'service':           service,
        'total_airings':     total,
        'channels':          rows,
        'channels_total':    len(rows),
        'channels_scheduled': scheduled,
        'channels_local':    local,
        # `national` is what run_all counts to decide a scraper did
        # something. The Channel Ranker reads `channels`.
        'national':          [{'rank': i, 'title': c['name']}
                              for i, c in enumerate(rows[:10], 1)],
    }
    if extra:
        body.update(extra)
    logger.info("%s: %d channels (%d with a schedule, %d local), "
                 "%s airings/wk total",
                 service, len(rows), scheduled, local, f'{total:,}')
    return body
