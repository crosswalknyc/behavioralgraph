"""The Peacock rail reads the Peacock figures on file (Jenna, 2026-11-18).

One step in the store's closing sequence, right after the partner
calibrated channels (`first_party_calibration`): every row the Peacock
rail carries for a show on file takes that show's daily level for the
day (`migration.peacock_actuals.daily_views`: the rankers' own unit, an
account that played the title that day, once), salted by title and day
so the rail keeps moving.

A Peacock row for a title the record does not name is only ever moved
DOWN here, by the correction the record taught for the rail and only as
far as the title's kind earns; what the record taught for the rail is a
lift, and a lift is never passed on to other titles on a board whose
total already runs over what the service can hold in a day. Live sport,
events and news are never touched. The chart still owns the order: the
steps after this one (ceiling, coherence, bands) run as before.

Peacock only: no other service's reading is read or written, and the
"Peacock on Amazon" rail keeps deriving from this one the way it did.
Fail-safe: no record, no network or any error leaves the store as it
was. Never queries clickstream.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SLUG = 'peacock'
_KINDS = ('film:', 'tv:', 'title:')


def _pk():
    try:
        from migration import peacock_actuals as pk
        return pk
    except Exception:  # noqa: BLE001
        return None


def apply_to_store(researched: dict, target_date_iso: str) -> dict[str, Any]:
    """Set the Peacock reading of every row for a show on file; pull a
    Peacock reading for any other title down where the record says our
    rail runs high for its kind. Returns counts."""
    stats = {'moved': 0, 'on_file': 0, 'scaled': 0, 'unchanged': 0}
    pk = _pk()
    if pk is None or not pk.available() or not target_date_iso:
        return stats
    from scripts.trends_scrapers import stream_estimates as se
    for key, it in (researched or {}).items():
        if not isinstance(it, dict) or not str(key).startswith(_KINDS):
            continue
        blk = (it.get('by_platform') or {}).get(SLUG)
        if not isinstance(blk, dict):
            continue
        cur = int(blk.get('us_estimate') or 0)
        if cur <= 0:
            continue
        norm = str(key).split(':', 1)[1]
        title = str(it.get('display_title') or norm)
        salt = f'{target_date_iso}|peacock_scale|{key}'
        target = None
        why = 'on_file'
        if str(key).startswith('tv:') or str(key).startswith('title:'):
            target = pk.daily_views(title, target_date_iso) or pk.daily_views(norm, target_date_iso)
        if target is None:
            kind = 'film' if str(key).startswith('film:') else pk.kind_of(it.get('category_display'), it.get('genre'), 'series')
            f = pk.platform_factor(title, 'daily_views', kind=kind,
                                   live=bool(it.get('is_live') or kind in ('sports', 'news')))
            if f < 1.0:
                target, why = int(round(cur * f)), 'scaled'
        if target is None:
            stats['unchanged'] += 1
            continue
        try:
            target = max(1, se._natural_last_digits(max(1, int(target)), title, salt))
        except Exception:  # noqa: BLE001
            target = max(1, int(target))
        if target == cur:
            stats['unchanged'] += 1
            continue
        if se._set_platform_reading(it, SLUG, target, key, salt):
            stats[why] += 1
            stats['moved'] += 1
        else:
            stats['unchanged'] += 1
    logger.info('peacock_scale: %s', stats)
    return stats
