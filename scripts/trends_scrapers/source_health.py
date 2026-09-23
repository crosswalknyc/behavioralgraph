"""A rail that fell back to a lesser source has to say so.

The failure mode this exists for
--------------------------------
The Netflix rail spent weeks rendering a weekly published file under
a daily label. Nothing was broken in a way anything could see: the
scraper ran, parsed rows, wrote a snapshot, and reported success. Its
browser runner injected donated cookies directly, the cookie loader
drops a jar past its freshness window and hands back an empty list,
so it rendered signed-out, the daily rails were simply not in the
page, and it took the weekly fallback exactly as designed. Every
signal said healthy. The only thing that said otherwise was one INFO
line a night.

That is the worst shape a failure can take, and 2026-09-23 produced
four of them in a day: the Netflix session above, the HBO Max
marketing page publishing as viewership, Plex answering 200 with a
complete German lineup, and LG returning every category heading with
empty channel lists. A silent degrade is worse than an outage,
because an outage gets noticed.

What this does
--------------
A scraper that could not reach its primary source and used a
secondary calls `record`, and the degradation travels with the run
instead of evaporating into a log:

  * onto the snapshot itself, as `source_health`, so anyone reading
    the file can see the row set is not what the rail is supposed to
    be
  * into a per-run tally the nightly reports on, so a rail that has
    quietly been on its fallback for weeks is visible as a count
    rather than as an absence

Deliberately NOT a new vocabulary. `_auth_guard` already classifies a
page as signed in, signed out or unknown, and this reuses its verdict
rather than inventing a second notion of the same thing. Where a rail
has a session-gated primary, failing to reach a signed-in session is
a degraded run even when the fallback produces perfectly plausible
rows, which is the whole lesson of the Netflix weeks.

Everything is best-effort. Recording a degradation never raises into
a run, and a rail that cannot record one still ships its data.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_events: list[dict] = []

# What a rail was reaching for, so a reader does not have to infer it
# from the reason text.
PRIMARY_LIVE_CHART = 'live first-party chart'
PRIMARY_SIGNED_IN = 'signed-in session'


def record(source: str, *, used: str, primary: str = PRIMARY_LIVE_CHART,
           reason: str = '', auth: Optional[str] = None,
           covers: str = '') -> dict:
    """Note that `source` shipped from `used` rather than its primary.

    `auth` is an `_auth_guard` verdict when one was taken, so the two
    stay in one vocabulary. `covers` names the period the fallback
    content describes when that differs from the run day, which is
    what made the Netflix case invisible: fresh fetch, stale content.
    """
    ev = {
        'source':   source,
        'primary':  primary,
        'used':     used,
        'reason':   (reason or '')[:300],
        'auth':     auth,
        'covers':   covers,
        'at':       datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        _events.append(ev)
    logger.warning(
        'source health: %s could not reach its %s and shipped from %s%s%s',
        source, primary, used,
        f' ({reason})' if reason else '',
        f'; content covers {covers}' if covers else '')
    return ev


def events(source: Optional[str] = None) -> list[dict]:
    with _lock:
        if source is None:
            return list(_events)
        return [e for e in _events if e['source'] == source]


def clear(source: Optional[str] = None) -> None:
    global _events
    with _lock:
        if source is None:
            _events = []
        else:
            _events = [e for e in _events if e['source'] != source]


def stamp(payload: dict, source: str) -> dict:
    """Put this source's degradations on its own snapshot.

    A healthy run stamps nothing, so the field's presence is the
    signal and no reader has to check a boolean.
    """
    try:
        mine = events(source)
        if mine and isinstance(payload, dict):
            payload['source_health'] = {
                'degraded': True,
                'events': mine,
            }
    except Exception:
        pass
    return payload


def verdict_for(domain: str, html: str) -> Optional[str]:
    """`_auth_guard`'s reading of a page, or None when it has nothing
    registered for the domain or cannot be imported.

    Read-only use of that module: this never classifies auth itself,
    because two implementations of the same judgement is the thing
    that produced the bug in the first place.
    """
    try:
        from ._auth_guard import classify_auth
    except Exception:
        try:
            from scripts.trends_scrapers._auth_guard import classify_auth
        except Exception:
            return None
    try:
        verdict, _why = classify_auth(domain, html)
        return verdict
    except Exception:
        return None


def summary() -> dict:
    """What the run should report: which rails degraded and how."""
    evs = events()
    by_source: dict[str, list] = {}
    for e in evs:
        by_source.setdefault(e['source'], []).append(e)
    return {
        'degraded_sources': sorted(by_source),
        'count': len(by_source),
        'events': evs,
    }


def report_lines() -> list[str]:
    """One readable line per degraded rail, for a run report."""
    out = []
    for source in sorted({e['source'] for e in events()}):
        for e in events(source):
            bits = [f'{source}: wanted its {e["primary"]}, '
                    f'shipped from {e["used"]}']
            if e.get('auth'):
                bits.append(f'auth read as {e["auth"]}')
            if e.get('covers'):
                bits.append(f'content covers {e["covers"]}')
            if e.get('reason'):
                bits.append(e['reason'])
            out.append('  ' + '; '.join(bits))
    return out
