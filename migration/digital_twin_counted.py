"""Our own count of what an audience did, for settling a call
(Digital Twins IQ, 2026-11-20).

A call whose check is ``counted`` is settled from the clickstream once
its window has closed plus a day. What the count can settle today:

  title_viewers   people who watched a title in a window: the title's
                  watch addresses in the content map, the people who
                  loaded one of them between the two days, scaled to the
                  US the way every profile is (raw over the 10M panel
                  base times 329.9M)
  return_pct      of the people who watched a title in its first window,
                  the share who watched it again in the second

What it cannot settle yet (the daily check leaves these to a published
result or the client's own results):

  a week-one audience on every screen   TV and connected TV viewing is not in our count
  finishing an episode                  a page load carries no watch time
  season completion                     the content map does not mark the finale
  which app opens first                 an app open on a TV is not in our count
  sign-ups and cancellations            no shared sign-up signal across services yet
  a title missing from the content map  nothing to count it by (NCIS: New York today)

Every query is read only. ``connect`` is injectable for tests; under
REGRESSION_TEST_MODE nothing opens the warehouse.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

US_POP = 329_900_000          # the house projection (profile-iq-pipeline-rules 3a): raw over the 10M base times US people
PANEL_BASE = 10_000_000
NEEDLES_PER_CHUNK = 200

SUPPORTED = {'title_viewers': 'people who watched the title in the window', 'return_pct': 'of the first window\'s viewers, the share who watched again in the second'}
NOT_YET = (
    ('week_one_audience', 'a week-one audience on every screen: TV and connected TV viewing is not in our count'),
    ('finish_pct', 'finishing an episode: a page load carries no watch time'),
    ('season_completion_pct', 'season completion: the content map does not mark the finale'),
    ('first_app', 'which app opens first: an app open on a TV is not in our count'),
    ('signups', 'sign-ups and cancellations: no shared sign-up signal across services yet'),
    ('not_mapped', 'a title missing from the content map: nothing to count it by'),
)

PLATFORM_HOSTS = {
    'hulu': 'hulu.com', 'netflix': 'netflix.com', 'amazon': 'amazon.com', 'prime video': 'amazon.com', 'amazon prime video': 'amazon.com',
    'peacock': 'peacocktv.com', 'hbo max': 'max.com', 'max': 'max.com', 'paramount plus': 'paramountplus.com', 'paramount+': 'paramountplus.com',
    'youtube': 'youtube.com', 'starz': 'starz.com', 'disney+': 'disneyplus.com', 'disney plus': 'disneyplus.com', 'apple tv': 'tv.apple.com',
    'apple tv+': 'tv.apple.com', 'tubi': 'tubitv.com', 'pluto': 'pluto.tv', 'pluto tv': 'pluto.tv', 'roku': 'therokuchannel.roku.com',
}


class Unavailable(Exception):
    """The count could not be taken (no warehouse, a query refused)."""


def _key(show: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(show or '').upper())


def _platform_key(p: str) -> str:
    return ' '.join(str(p or '').lower().replace('&', ' and ').split())


def host_for(platform: str) -> str:
    k = _platform_key(platform)
    if k in PLATFORM_HOSTS:
        return PLATFORM_HOSTS[k]
    for name, host in PLATFORM_HOSTS.items():
        if name in k:
            return host
    return ''


def _connect(connect: Optional[Callable]):
    if connect is not None:
        return connect()
    if (os.environ.get('REGRESSION_TEST_MODE') or '').strip().lower() in ('1', 'true', 'yes'):
        raise Unavailable('a test process never opens the warehouse')
    from migration.clickhouse_connector import connect_clickhouse
    return connect_clickhouse()


def _rows(connect, sql: str) -> list:
    conn = _connect(connect)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return list(cur.fetchall() or [])
    except Unavailable:
        raise
    except Exception as e:
        raise Unavailable(str(e)[:200])
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _lit(s: str) -> str:
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'") + "'"


def mapped(show: str, platform: str = '', *, connect: Optional[Callable] = None) -> list:
    """(platform, url) rows the content map holds for the title, on the
    platform when one is named."""
    k = _key(show)
    if not k:
        return []
    sql = ("SELECT PLATFORM, URL FROM reference.content_mapping WHERE upper(replaceRegexpAll(SHOW, '[^a-zA-Z0-9]', '')) = " + _lit(k))
    rows = _rows(connect, sql)
    pk = _platform_key(platform)
    if pk:
        on = [r for r in rows if _platform_key(r[0]) == pk or host_for(r[0]) == host_for(platform)]
        rows = on or []
    return [(str(r[0] or ''), str(r[1] or '')) for r in rows if str(r[1] or '').strip()]


def has_title(show: str, platform: str = '', *, connect: Optional[Callable] = None) -> bool:
    """Whether the title has watch addresses in the content map (on the
    platform when one is named). False when the map cannot be read."""
    try:
        return bool(mapped(show, platform, connect=connect))
    except Exception:
        return False


def needles(rows: list) -> list:
    out = []
    for _p, url in rows:
        u = str(url or '').strip()
        u = re.sub(r"^[a-z]+://", '', u, flags=re.I)
        u = re.sub(r"^www\.", '', u, flags=re.I).rstrip('/')
        if len(u) >= 6 and u not in out:
            out.append(u)
    return out


def _url_filter(rows: list) -> str:
    hosts = sorted({host_for(p) for p, _u in rows if host_for(p)})
    ns = needles(rows)
    if not hosts or not ns:
        raise Unavailable('no watch addresses to count by')
    host_part = ' OR '.join(f"positionCaseInsensitive(URL, {_lit(h)}) > 0" for h in hosts)
    chunks = [ns[i:i + NEEDLES_PER_CHUNK] for i in range(0, len(ns), NEEDLES_PER_CHUNK)]
    needle_part = ' OR '.join('multiSearchAnyCaseInsensitive(URL, [' + ', '.join(_lit(n) for n in c) + '])' for c in chunks)
    return f"({host_part}) AND ({needle_part})"


def project(raw: int) -> int:
    """Raw people over the 10M base times 329.9M, the house projection;
    a count that lands on a trailing zero moves by one person."""
    v = int(round(int(raw) / PANEL_BASE * US_POP))
    if v > 0 and v % 10 == 0:
        v += 1
    return v


def _peacock_on_file(show: str, platform: str, start: str, end: str) -> Optional[dict]:
    """A show the Peacock record names, counted on Peacock: the record's
    own figure for the window (migration/peacock_actuals.py; Jenna
    2026-11-24, "cover every Peacock title figure": a title on the record
    uses the record). None for any other title or service, or no record."""
    try:
        from datetime import date, timedelta
        from migration import peacock_actuals as _pk
        if not _pk.is_peacock(platform):
            return None
        last = (date.fromisoformat(str(end)[:10]) - timedelta(days=1)).isoformat()
        v = _pk.viewers(show, str(start)[:10], last)
        return {'raw': int(v['sample']), 'people': int(v['viewers'])} if v else None
    except Exception:
        return None


def title_viewers(show: str, platform: str, start: str, end: str, *, connect: Optional[Callable] = None) -> dict:
    """People who watched the title between start (inclusive) and end
    (exclusive). {'raw', 'people'}. A show the Peacock record names,
    counted on Peacock, reads the record's own figure for the window."""
    on_file = _peacock_on_file(show, platform, start, end)
    if on_file:
        return on_file
    rows = mapped(show, platform, connect=connect)
    if not rows:
        raise Unavailable('the title is not in the content map')
    sql = ("SELECT uniqExact(UID) FROM clickstream.clickstream_final WHERE VISIT_TS >= toDateTime(" + _lit(start + ' 00:00:00') + ") "
           "AND VISIT_TS < toDateTime(" + _lit(end + ' 00:00:00') + ") AND " + _url_filter(rows))
    got = _rows(connect, sql)
    raw = int((got[0][0] if got and got[0] else 0) or 0)
    return {'raw': raw, 'people': project(raw)}


def return_pct(show: str, platform: str, a_start: str, a_end: str, b_end: str, *, connect: Optional[Callable] = None) -> dict:
    """Of the people who watched the title in [a_start, a_end), the share
    who watched it again in [a_end, b_end). {'base', 'back', 'pct'}."""
    rows = mapped(show, platform, connect=connect)
    if not rows:
        raise Unavailable('the title is not in the content map')
    sql = ("SELECT countIf(a) AS base, countIf(a AND b) AS back FROM (SELECT UID, max(VISIT_TS < toDateTime(" + _lit(a_end + ' 00:00:00') + ")) AS a, "
           "max(VISIT_TS >= toDateTime(" + _lit(a_end + ' 00:00:00') + ")) AS b FROM clickstream.clickstream_final WHERE VISIT_TS >= toDateTime(" + _lit(a_start + ' 00:00:00') + ") "
           "AND VISIT_TS < toDateTime(" + _lit(b_end + ' 00:00:00') + ") AND " + _url_filter(rows) + " GROUP BY UID)")
    got = _rows(connect, sql)
    base = int((got[0][0] if got and got[0] else 0) or 0)
    back = int((got[0][1] if got and got[0] and len(got[0]) > 1 else 0) or 0)
    if base <= 0:
        raise Unavailable('nobody watched in the first window')
    return {'base': base, 'back': back, 'pct': round(100.0 * back / base, 1)}


def settle(spec: dict, *, connect: Optional[Callable] = None) -> dict:
    """The outcome of a counted call: {'value', 'note'}. Raises
    Unavailable when the count cannot be taken, ValueError when the
    measure is one the count cannot settle."""
    meas = (spec or {}).get('measure') or {}
    metric = str(meas.get('metric') or '')
    show = str(meas.get('title') or meas.get('subject') or '')
    platform = str(meas.get('platform') or '')
    ws, we = str(meas.get('window_start') or ''), str(meas.get('window_end') or '')
    if not (ws and we):
        raise ValueError('a count needs both window days')
    if metric == 'title_viewers':
        got = title_viewers(show, platform, ws, we, connect=connect)
        return {'value': got['people'], 'note': f"{got['raw']} people in our count between {ws} and {we}"}
    if metric == 'return_pct':
        from migration.digital_twin_calls import add_days
        mid = add_days(ws, 7)
        got = return_pct(show, platform, ws, mid, we, connect=connect)
        return {'value': got['pct'], 'note': f"{got['back']} of {got['base']} people in our count came back between {mid} and {we}"}
    raise ValueError(f"our count cannot settle {metric or 'this measure'} yet")


__all__ = ['SUPPORTED', 'NOT_YET', 'Unavailable', 'host_for', 'mapped', 'has_title', 'needles', 'project', 'title_viewers', 'return_pct', 'settle']
