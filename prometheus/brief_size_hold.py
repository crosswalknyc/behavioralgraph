"""The first size a brief shows for a subject and window is the size
(Grace Yin, Amazon, 2026-11-18).

One thread, one title, one service, one window, three briefs:

    01:12  Tony Viewers on Amazon Prime Video            1,310,264 to 1,803,596 US people
    01:23  Tony 2026 film Viewers on Amazon Prime Video    493,695 to   670,093
    01:25  Tony (2026 film) Viewers on Amazon Prime Video  578,942 to   796,843

Each re-draft sized the audience again from its own reading of the
whole (4.2M, then 1.6M, then 1.9M people for the film everywhere), and
nothing held the first one. The standing rule for sizes (first-held
wins) only began once a build had shipped
(migration.service_scope_sizing keeps that record); a brief nobody had
approved yet held nothing.

The rule, at the one place a brief's size is settled before it is
shown (the estimate step of the build intake):

  * the first size drafted for a subject, on a set of services, for a
    window, is held. A later draft of the same subject, services and
    window, in the same thread or any other, by the same reader or
    any other, carries that size, its Avid tier and the whole it was
    read against. Names that differ only by a year, a kind word or the
    audience tail ("Tony", "Tony 2026 film", "Tony (2026 film) Viewers
    on Amazon Prime Video") are one subject; two windows within three
    days of each other at both ends are one window (a short window is
    held to a tenth of its length, so a nine-day window is its dates).
  * a narrower draft of the same subject (a window inside a held one,
    on the same services or fewer) is never larger than the held one.
  * a build that has shipped is the standing figure, as before: the
    shipped record answers and this module stands aside. A size the
    reader locked from a sample check stands too.
  * for a build scoped to named services the held size is handed to the
    scoped rule as the whole it produces that size from, so the size the
    reader approves is the size the spec carries.

Where: s3://dashboard-inputs/system/pm_brief_size_holds.json (one small
document, ETag compare-and-swap through migration.s3_json_state).
How long: 90 days from the first draft (HOLD_DAYS), or until a build of
that subject ships, whichever comes first; expired holds are dropped on
the next write. Server side only: nothing here reads a field of the
request, and a probe identity reads the holds and never writes one.

Silent: nothing a reader sees says any of this. Never raises; on any
failure the draft is left exactly as it came.
Regression: scripts/test_pm_grace_thread_leftovers_2026_11_18.py.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

__all__ = ['settle', 'ranged', 'subject_key', 'scope_of', 'window_of', 'find', 'STORE_KEY', 'STORE_BUCKET', 'HOLD_DAYS']

STORE_BUCKET = 'dashboard-inputs'
STORE_KEY = 'system/pm_brief_size_holds.json'
HOLD_DAYS = 90
SAME_WINDOW_DAYS = 3
MAX_HOLDS = 4000
_FRESH = ('new_build',)
_TUPLE = ('subject_raw_tu', 'subject_raw_avid', 'universe_anchor', 'engaged_share', 'anchor_source')

_NOISE = frozenset("""
the a an of on in at for and only film films movie movies series show shows tv season seasons special documentary
docuseries title viewers viewer watchers streamers audience audiences fans fan engagers customers consumers users
profile universe total amazon prime video netflix hulu max hbo peacock paramount plus disney apple youtube tubi
pluto roku starz
""".split())
_YEAR_RX = re.compile(r"\b((?:19|20)\d{2})\b")
_AUD_TAIL_RX = re.compile(r"\s+(?:viewers?|watchers|streamers|listeners|readers|players|fans|audience|engagers|customers|"
                          r"consumers|users|buyers|shoppers|subscribers|members)\b.*$", re.I)
_VIEWERS_RX = re.compile(r"\b(?:viewers?|watchers|streamers|listeners|readers|players)\b", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def _now(now=None):
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _hermetic():
    return str(os.environ.get('REGRESSION_TEST_MODE', '')).strip() == '1' or bool(os.environ.get('PM_REPLAY_CHILD'))


def _is_probe():
    try:
        from . import faults as _faults
        return bool(_faults.is_probe())
    except Exception:
        return False


def subject_key(draft):
    """(base, year, kind) for a draft. ``base`` is the subject's naming
    words with the year, the kind word, the service and the audience
    tail taken off ('tony' for "Tony (2026 film) Viewers on Amazon
    Prime Video"); ``year`` is the year its name carries, or ''; ``kind``
    is 'viewers' for a consumption universe, else 'broad'."""
    try:
        d = draft if isinstance(draft, dict) else {}
        name = _squash(d.get('subject') or d.get('name') or '')
        title = _squash(d.get('content_show') or d.get('resolved_title') or '')
        raw = (title or name).split(' - ', 1)[0]
        raw = _AUD_TAIL_RX.sub('', raw) if not title else raw
        toks = [w for w in re.findall(r"[a-z0-9]+", raw.lower()) if w not in _NOISE and not _YEAR_RX.fullmatch(w)]
        if not toks:
            toks = [w for w in re.findall(r"[a-z0-9]+", raw.lower()) if not _YEAR_RX.fullmatch(w)]
        base = ' '.join(toks)
        year = ''
        for src in (title, name, d.get('identity_qualifier'), str(d.get('resolved_identity') or '')[:70]):
            m = _YEAR_RX.search(str(src or ''))
            if m:
                year = m.group(1)
                break
        viewers = (str(d.get('ip_scope') or '').strip().lower() == 'consumers' or bool(str(d.get('consumer_verb') or '').strip())
                   or str(d.get('seed_source') or '').startswith('content_map') or bool(_VIEWERS_RX.search(name))
                   or str(d.get('audience_type') or '').strip().lower() in ('viewers', 'viewer', 'watchers', 'streamers', 'listeners'))
        return base, year, ('viewers' if viewers else 'broad')
    except Exception:
        return '', '', 'broad'


def _sss():
    try:
        from migration import service_scope_sizing as s
    except ImportError:
        import service_scope_sizing as s  # type: ignore
    return s


def scope_of(draft):
    """The services the draft is scoped to, as canonical keys, sorted
    ([] for every service)."""
    try:
        s = _sss()
        d = draft if isinstance(draft, dict) else {}
        view = {'content_seed_platforms_named': list(d.get('named_platforms') or d.get('content_seed_platforms_named') or []),
                'name': d.get('subject') or d.get('name') or ''}
        return list(s.services_of(view, view['name']))
    except Exception:
        return []


def window_of(draft):
    """{'start', 'end'} ISO for the draft's window; the trailing year
    when it names none."""
    d = draft if isinstance(draft, dict) else {}
    dr = d.get('date_range')
    try:
        if isinstance(dr, dict) and dr.get('start') and dr.get('end'):
            a, b = date.fromisoformat(str(dr['start'])[:10]), date.fromisoformat(str(dr['end'])[:10])
            if b >= a:
                return {'start': a.isoformat(), 'end': b.isoformat()}
        m = re.findall(r"\d{4}-\d{2}-\d{2}", str(d.get('engine_date_range') or (dr if isinstance(dr, str) else '') or ''))
        if len(m) >= 2:
            return {'start': m[0], 'end': m[1]}
    except Exception:
        pass
    end = datetime.now(timezone.utc).date()
    try:
        start = end.replace(year=end.year - 1)
    except ValueError:
        start = end.replace(year=end.year - 1, day=28)
    return {'start': start.isoformat(), 'end': end.isoformat()}


def _d(iso):
    return date.fromisoformat(str(iso)[:10])


def _tol(*wins):
    """How far apart two ends may sit and still be one window: three
    days on a long window, a tenth of its length on a short one (a
    nine-day window is its exact dates)."""
    try:
        days = min(max(1, (_d(w['end']) - _d(w['start'])).days) for w in wins)
        return min(SAME_WINDOW_DAYS, days // 10)
    except Exception:
        return 0


def _same_window(a, b):
    try:
        tol = _tol(a, b)
        return all(abs((_d(a[k]) - _d(b[k])).days) <= tol for k in ('start', 'end'))
    except Exception:
        return False


def _inside(inner, outer):
    try:
        tol = _tol(inner, outer)
        return _d(inner['start']) >= _d(outer['start']) - timedelta(days=tol) \
            and _d(inner['end']) <= _d(outer['end']) + timedelta(days=tol)
    except Exception:
        return False


def _live(h, now):
    try:
        first = datetime.strptime(str(h.get('first_at')), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        return (now - first) <= timedelta(days=HOLD_DAYS) and int(h.get('tu') or 0) > 0
    except Exception:
        return False


def _same_subject(h, base, year, kind):
    if not base or h.get('base') != base or h.get('kind') != kind:
        return False
    hy = str(h.get('year') or '')
    return not hy or not year or hy == year


def find(holds, draft, now=None):
    """(exact, cap): the live hold for this draft's subject, services and
    window (the first one drafted), and the smallest held size among live
    holds of the same subject that contain this draft (a window it sits
    inside, on the same services or more). Either may be None."""
    now = _now(now)
    base, year, kind = subject_key(draft)
    svcs, win = scope_of(draft), window_of(draft)
    exact, cap = None, None
    for h in sorted([x for x in (holds or []) if isinstance(x, dict) and _live(x, now)], key=lambda x: str(x.get('first_at'))):
        if not _same_subject(h, base, year, kind):
            continue
        hs, hw = list(h.get('services') or []), h.get('window') or {}
        if hs == svcs and _same_window(hw, win):
            exact = exact or h
            continue
        if _inside(win, hw) and (not hs or (svcs and set(svcs) <= set(hs))):
            if cap is None or int(h['tu']) < int(cap['tu']):
                cap = h
    return exact, cap


def _load(s3=None):
    if _hermetic() and s3 is None:
        return []
    try:
        try:
            from migration.s3_json_state import read_json_with_etag
        except ImportError:
            from s3_json_state import read_json_with_etag  # type: ignore
        obj, _etag = read_json_with_etag(STORE_BUCKET, STORE_KEY, s3=s3) if s3 is not None \
            else read_json_with_etag(STORE_BUCKET, STORE_KEY)
        holds = (obj or {}).get('holds') if isinstance(obj, dict) else None
        return list(holds) if isinstance(holds, list) else []
    except Exception as e:
        print(f"[brief-size] holds read failed (non-fatal): {type(e).__name__}: {e}")
        return []


def _save(entry, s3=None, now=None):
    if (_hermetic() and s3 is None) or _is_probe():
        return False
    try:
        try:
            from migration.s3_json_state import update_json
        except ImportError:
            from s3_json_state import update_json  # type: ignore
        now = _now(now)

        def _mut(obj):
            obj = obj if isinstance(obj, dict) else {}
            holds = [h for h in (obj.get('holds') or []) if isinstance(h, dict) and _live(h, now)]
            # first-held wins: a hold for the same subject, services and window is never replaced
            for h in holds:
                if _same_subject(h, entry['base'], entry['year'], entry['kind']) and list(h.get('services') or []) == entry['services'] \
                        and _same_window(h.get('window') or {}, entry['window']):
                    return None
            holds.append(entry)
            obj['v'] = 1
            obj['holds'] = holds[-MAX_HOLDS:]
            return obj
        if s3 is not None:
            update_json(STORE_BUCKET, STORE_KEY, _mut, s3=s3)
        else:
            update_json(STORE_BUCKET, STORE_KEY, _mut)
        return True
    except Exception as e:
        print(f"[brief-size] hold write failed (non-fatal): {type(e).__name__}: {e}")
        return False


def _int(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _shipped_governs(draft):
    """A shipped build's record already set this draft's size."""
    tr = draft.get('service_scope_sizing') if isinstance(draft.get('service_scope_sizing'), dict) else {}
    if str(tr.get('basis') or '') in ('anchor', 'anchor_scaled'):
        return True
    return any('whole on record' in str(n) for n in (tr.get('notes') or []))


def _carry(draft, held_tu, held_avid, held):
    """Put the held size on the draft. A build scoped to named services
    is sized by the scoped rule from the whole on the draft, so the
    whole is set to the one that rule produces the held size from (the
    spec and the build then land on the same figure); any other draft
    takes the held figures as they are."""
    scoped = False
    try:
        s = _sss()
        view = {'name': draft.get('subject') or draft.get('name') or '',
                'content_show': draft.get('content_show') or draft.get('resolved_title') or '',
                'seed_source': draft.get('seed_source') or '', 'audience_type': draft.get('audience_type') or '',
                'content_seed_platforms_named': list(draft.get('named_platforms') or draft.get('content_seed_platforms_named') or [])}
        scoped = bool(s.is_scoped(view, view['name']))
    except Exception:
        scoped = False
    if scoped:
        try:
            _fit_whole(s, draft, view, held_tu, _int((held or {}).get('universe_anchor')) or _int(draft.get('universe_anchor')))
        except Exception as e:
            print(f"[brief-size] whole not refit (non-fatal): {type(e).__name__}: {e}")
        draft['subject_raw_tu'] = held_tu
        if held_avid and held_avid < held_tu:
            draft['subject_raw_avid'] = held_avid
        return
    draft['subject_raw_tu'] = held_tu
    if held_avid and held_avid < held_tu:
        draft['subject_raw_avid'] = held_avid
    for k in ('universe_anchor', 'engaged_share', 'anchor_source'):
        if (held or {}).get(k) is not None:
            draft[k] = held[k]


def _fit_whole(s, draft, view, held_tu, ua0):
    """Set the whole on a scoped draft to the one the scoped rule reads
    the held size from. The rule's size is the whole times the share on
    the named services, carried through the house's subject salt, so the
    whole is solved for the base the salt lands on the held size from
    (or, when no base lands on it exactly, the nearest one: a handful of
    people apart, inside the band the brief shows)."""
    name = view['name']
    dr = draft.get('date_range')
    window = str(draft.get('engine_date_range') or '').strip()
    if not window and isinstance(dr, dict) and dr.get('start') and dr.get('end'):
        window = f"{str(dr.get('start'))[:10]} TO {str(dr.get('end'))[:10]}"
    probe = dict(view, date_range=window, subject_raw_tu=held_tu, universe_anchor=(ua0 or int(round(held_tu * 32.99 * 3))))
    v0 = s.size(probe, name)
    share = float((v0 or {}).get('share') or 0)
    if not v0 or share <= 0:
        return
    span = max(9, int(held_tu * 0.02) | 1) + 2
    best = None
    for delta in sorted(range(-span, span + 1), key=abs):
        base = held_tu + delta
        if base <= 800:
            continue
        gap = abs(s._messy(name, base) - held_tu)
        if best is None or gap < best[0]:
            best = (gap, base)
        if gap == 0:
            break
    if not best:
        return
    want = s._messy(name, best[1])
    ua = int(round(best[1] * s.PANEL_TO_US / share))
    for step in (0, 20, -20, 40, -40, 60, -60, 80, -80):
        got = s.size(dict(probe, universe_anchor=ua + step), name)
        if got and int(got.get('sample') or 0) == want:
            ua += step
            break
    draft['universe_anchor'] = ua
    draft['subject_raw_tu'] = held_tu
    s.apply_to_draft(draft)


def settle(draft, *, s3=None, now=None, who='', thread=''):
    """The brief's size, settled against what is held. Returns the draft.

    Same subject, services and window as a live hold: the held size (and
    its Avid tier) is carried. A narrower draft of a held subject is
    capped at the held size. Otherwise the draft's own size becomes the
    hold. Only a fresh build's draft is touched; a size a shipped build
    or a sample check already fixed is left alone."""
    try:
        if not isinstance(draft, dict):
            return draft
        if str(draft.get('decision') or '').strip().lower() not in _FRESH:
            return draft
        tu = _int(draft.get('subject_raw_tu'))
        base, year, kind = subject_key(draft)
        if tu <= 0 or not base or draft.get('sample_size_locked') or _shipped_governs(draft):
            return draft
        now = _now(now)
        holds = _load(s3)
        exact, cap = find(holds, draft, now)
        if exact:
            held_tu, held_avid = _int(exact.get('tu')), _int(exact.get('avid'))
            if held_tu > 0 and held_tu != tu:
                print(f"[brief-size] {draft.get('subject') or draft.get('name')!r}: drafted {tu:,}; the size first held for "
                      f"this subject and window ({exact.get('first_at')}) stands: {held_tu:,}")
                _carry(draft, held_tu, held_avid, exact)
            elif held_avid and 0 < held_avid < held_tu and _int(draft.get('subject_raw_avid')) != held_avid:
                draft['subject_raw_avid'] = held_avid
            draft['sample_size_held'] = True
            return draft
        if cap and tu > _int(cap.get('tu')) > 0:
            capped = _int(cap['tu'])
            avid = _int(draft.get('subject_raw_avid'))
            new_avid = max(1, int(round(avid * capped / float(tu)))) if 0 < avid < tu else 0
            print(f"[brief-size] {draft.get('subject') or draft.get('name')!r}: drafted {tu:,} for a narrower window or scope "
                  f"than a held brief of the same subject ({capped:,}); held to it")
            _carry(draft, capped, new_avid, None)
            tu = _int(draft.get('subject_raw_tu')) or capped
        if not who or not thread:
            try:
                from flask import g as _g, has_request_context
                if has_request_context():
                    who = who or str(getattr(_g, '_pm_ask_user', '') or '')
                    thread = thread or str(getattr(_g, '_pm_thread_id', '') or '')
            except Exception:
                pass
        entry = {'base': base, 'year': year, 'kind': kind, 'services': scope_of(draft), 'window': window_of(draft),
                 'tu': _int(draft.get('subject_raw_tu')) or tu, 'avid': _int(draft.get('subject_raw_avid')),
                 'universe_anchor': _int(draft.get('universe_anchor')) or None,
                 'engaged_share': draft.get('engaged_share'), 'anchor_source': draft.get('anchor_source'),
                 'name': _squash(draft.get('subject') or draft.get('name'))[:160],
                 'title': _squash(draft.get('content_show') or draft.get('resolved_title'))[:120],
                 'first_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'by': str(who or '')[:80], 'thread': str(thread or '')[:40]}
        _save(entry, s3=s3, now=now)
        return draft
    except Exception as e:
        try:
            print(f"[brief-size] skipped (non-fatal): {type(e).__name__}: {e}")
        except Exception:
            pass
        return draft


def ranged(range_fn, draft, _ruled_size=None):
    """The build intake's one call (prometheus.legacy.chat, the estimate
    step): the scoped rule has just sized the draft (its result rides in
    as ``_ruled_size`` and is already on the draft), the size is settled
    against what is held, and the band the brief shows is read from the
    size that stands. Never raises past ``range_fn``'s own behavior."""
    try:
        settle(draft)
    except Exception:
        pass
    tu = draft.get('subject_raw_tu') if isinstance(draft, dict) else _ruled_size
    return range_fn(tu)
