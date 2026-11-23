"""Point-in-time snapshots: a request for the audience as it stood on a date becomes a dated cut of the existing file.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import re

# <<moved>>
# <<block: 1>>
_PM_SNAPSHOT_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July',
     'August', 'September', 'October', 'November', 'December'])}


# <<block: 2>>
def _pm_resolve_snapshot_date(text):
    """ISO date for 'N days/weeks/months ago', 'on June 14',
    'June 14 2026', '6/14/2026', or bare ISO. Past dates only.
    Self-contained twin of migration/date_snapshot_cut.resolve_
    snapshot_date (the engine lives worker-side; Render only needs
    the parse)."""
    import datetime as _dt
    t = str(text or '').lower()
    today = _dt.date.today()
    m = re.search(r'(\d{1,4})\s*(day|week|month)s?\s+ago', t)
    if m:
        n = int(m.group(1))
        days = n * (1 if m.group(2) == 'day' else
                    7 if m.group(2) == 'week' else 30)
        if 1 <= days <= 3650:
            return (today - _dt.timedelta(days=days)).isoformat()
    m = re.search(
        r'\b(january|february|march|april|may|june|july|august'
        r'|september|october|november|december)\s+(\d{1,2})'
        r'(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?', t)
    if m:
        mo = _C._PM_SNAPSHOT_MONTHS[m.group(1)]
        dd = int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else today.year
        try:
            d = _dt.date(yr, mo, dd)
        except ValueError:
            return None
        if d > today:
            d = _dt.date(yr - 1, mo, dd) if not m.group(3) else None
        return d.isoformat() if d and d <= today else None
    m = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', t)
    if m:
        try:
            d = _dt.date(int(m.group(3)), int(m.group(1)),
                         int(m.group(2)))
        except ValueError:
            return None
        return d.isoformat() if d <= today else None
    m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
    if m:
        try:
            d = _dt.date(int(m.group(1)), int(m.group(2)),
                         int(m.group(3)))
        except ValueError:
            return None
        return d.isoformat() if d <= today else None
    return None


# <<block: 3>>
def _pm_promote_date_snapshot_ask(draft, text):
    """Deterministic net under prompt rule 7a-DATE-SNAPSHOT (2026-09-23
    Jenna): an ask for a subject AS OF a specific past date, bound to a
    library parent, becomes a point-in-time snapshot cut
    (derive_cut / date_snapshot). The engine researches the date and
    re-reads the whole file row by row as it stood that day. Never
    raises; returns True when it promoted."""
    try:
        if not isinstance(draft, dict):
            return False
        t = str(text or '')
        if str(draft.get('derive_type') or '').strip().lower() == \
                'date_snapshot':
            if not draft.get('snapshot_date'):
                iso0 = _C._pm_resolve_snapshot_date(t)
                if iso0:
                    draft['snapshot_date'] = iso0
            return False
        # phrase gate: dated-state phrasings only (a bare year or an
        # explicit measurement window is a refresh, not a snapshot)
        if not re.search(
                r'\b\d{1,4}\s*(?:day|week|month)s?\s+ago\b|\bas of\b'
                r'|\b(?:on|back on)\s+(?:january|february|march|april'
                r'|may|june|july|august|september|october|november'
                r'|december)\b',
                t, re.I):
            return False
        iso = _C._pm_resolve_snapshot_date(t)
        if not iso:
            return False
        parent = str(draft.get('parent_s3_key')
                     or draft.get('existing_match_s3_key') or '').strip()
        if not parent:
            return False
        if str(draft.get('decision') or '').strip().lower() not in (
                'existing_match', 'derive_cut', 'time_shifted_refresh'):
            return False
        import datetime as _dt
        d = _dt.date.fromisoformat(iso)
        label = (list(_C._PM_SNAPSHOT_MONTHS)[d.month - 1].title()
                 + f' {d.day} {d.year}')
        draft['decision'] = 'derive_cut'
        draft['derive_type'] = 'date_snapshot'
        draft['parent_s3_key'] = parent
        draft['cut_label'] = label
        draft['snapshot_date'] = iso
        draft['decision_reason'] = (
            'Point-in-time snapshot: the audience as it stood on '
            + label + ', built as a dated cut of the existing file.')
        for k in ('ask_existing_profile', 'existing_profile_data',
                  'ask_qualifier_match', 'qualifier_match_data'):
            draft.pop(k, None)
        print(f"[date-snapshot] promoted to derive_cut/date_snapshot "
              f"({iso}, parent={parent!r})")
        return True
    except Exception as _dsp_err:
        print(f"[date-snapshot] promoter failed (non-fatal): {_dsp_err}")
        return False


__all__ = ['_PM_SNAPSHOT_MONTHS', '_pm_resolve_snapshot_date', '_pm_promote_date_snapshot_ask']
