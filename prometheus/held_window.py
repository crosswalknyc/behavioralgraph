"""A window the library already holds is reused, never rebuilt
(Jenna 2026-11-16, Grace Yin).

Grace, Amazon, 2026-11-16 20:44 UTC: "Please run two individual talent
profiles: Zac Efron and Dakota Johnson, US, calendar 2025." The library
held both on exactly that window (the SAMPLE SIZE row of each file reads
2025-01-01 TO 2025-12-31). The decision rule only knew how old each file
was (built in June, more than 120 days), so both came back as a refresh
at a price, both were rebuilt, and the rebuilt files carried a different
audience size for the same subject and the same window than the figure
she had been given four minutes earlier.

The rule: a refresh exists to move a file to a window it does not hold.
When the window asked for is exactly the window the matched file already
covers, and the file was built after that window closed, there is
nothing newer to read and the decision is a reuse (``existing_match``).
Any demographic cuts that rode the ask are then derived off the held
file by the standing cuts promoter (the caller runs it next).

Never fires when:
  * the decision is anything but ``time_shifted_refresh``;
  * no matched file is named on the draft, or its window cannot be read;
  * the asked window and the file's window differ by even a day;
  * the file was built before its window closed (it cannot hold the
    whole window, so a refresh is the right call);
  * the reader asked, in words, for a fresh pull ("refresh", "rebuild",
    "updated", "pull it again"). Their word stands.

Pure: the file's window arrives through ``read_window(key)`` so the
module needs no S3 and no host. Never raises.
"""
from __future__ import annotations

import re
from datetime import date, datetime

__all__ = ['asked_window', 'file_window', 'key_built_on', 'wants_fresh', 'holds', 'settle', 'REASON']

_ISO = r"(\d{4}-\d{2}-\d{2})"
_PAIR_RX = re.compile(_ISO + r"\s*(?:TO|to|-|,)\s*" + _ISO)
_SAMPLE_RX = re.compile(r"SAMPLE SIZE \(" + _ISO + r" TO " + _ISO + r"\)")
_KEY_TS_RX = re.compile(r"_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})$")
# the reader's own word for a fresh pull; "current" and "latest" alone are
# not one (a closed window has no later state to read)
_FRESH_RX = re.compile(
    r"\b(?:refresh(?:ed|ing)?|re-?build|re-?run|re-?pull|re-?do|redo|updated? (?:version|pull|file|build|profile|one)|"
    r"fresh (?:pull|build|version|run|one|profile)|pull (?:it|them|this|that) (?:again|fresh)|run (?:it|them) again|"
    r"new (?:pull|build|version)|from scratch|start over)\b", re.I)

REASON = "The file on record already covers {label}, so there is nothing to rebuild."


def _iso(s):
    try:
        return date.fromisoformat(str(s or '').strip()[:10])
    except Exception:
        return None


def asked_window(draft):
    """(start, end) the draft would build on, as dates, or None."""
    if not isinstance(draft, dict):
        return None
    for k in ('engine_date_range', 'date_range'):
        v = draft.get(k)
        if isinstance(v, dict):
            s, e = _iso(v.get('start')), _iso(v.get('end'))
            if s and e and s <= e:
                return s, e
        elif isinstance(v, str):
            m = _PAIR_RX.search(v)
            if m:
                s, e = _iso(m.group(1)), _iso(m.group(2))
                if s and e and s <= e:
                    return s, e
    return None


def file_window(head):
    """(start, end) off the head of a profile CSV (its SAMPLE SIZE row),
    or None. ``head`` is text or bytes."""
    try:
        if isinstance(head, (bytes, bytearray)):
            head = bytes(head).decode('utf-8', errors='replace')
        m = _SAMPLE_RX.search(str(head or ''))
        if not m:
            return None
        s, e = _iso(m.group(1)), _iso(m.group(2))
        return (s, e) if (s and e and s <= e) else None
    except Exception:
        return None


def key_built_on(key):
    """The build date in a canonical file name (Name_MM_DD_YYYY_HH_MM.csv)."""
    try:
        base = str(key or '').rsplit('/', 1)[-1].strip()
        if base.lower().endswith('.csv'):
            base = base[:-4]
        m = _KEY_TS_RX.search(base)
        if not m:
            return None
        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date()
    except Exception:
        return None


def wants_fresh(text):
    """True when the reader's words ask for a new pull of the same thing."""
    try:
        return bool(_FRESH_RX.search(' '.join(str(text or '').split())))
    except Exception:
        return False


def _label(s, e):
    def one(d):
        return d.strftime('%b ') + str(d.day) + d.strftime(', %Y')
    if s.year == e.year:
        return s.strftime('%b ') + str(s.day) + ' to ' + one(e)
    return one(s) + ' to ' + one(e)


def holds(draft, read_window, text=''):
    """(key, start, end) when the matched file already holds the asked
    window in full, else None. ``read_window(key) -> (start, end) |
    None`` reads the file's own window."""
    try:
        if not isinstance(draft, dict):
            return None
        if str(draft.get('decision') or '').strip().lower() != 'time_shifted_refresh':
            return None
        if wants_fresh(text):
            return None
        if draft.get('ask_existing_profile') or draft.get('ask_qualifier_match'):
            return None          # a question to the reader is already riding the draft; their answer decides
        key = str(draft.get('existing_match_s3_key') or draft.get('parent_s3_key') or '').strip()
        if not key:
            return None
        asked = asked_window(draft)
        if not asked:
            return None
        have = read_window(key) if callable(read_window) else None
        if not have:
            return None
        hs, he = have
        if isinstance(hs, str):
            hs = _iso(hs)
        if isinstance(he, str):
            he = _iso(he)
        if not (hs and he) or (hs, he) != asked:
            return None
        built = key_built_on(key)
        if built is None or built <= he:
            return None          # built before its window closed: it cannot hold all of it
        return key, hs, he
    except Exception:
        return None


def settle(draft, read_window, text=''):
    """Turn a refresh of a window the file already holds into a reuse.
    In place. Returns True when it did."""
    hit = holds(draft, read_window, text)
    if not hit:
        return False
    try:
        key, s, e = hit
        label = _label(s, e)
        draft['decision'] = 'existing_match'
        draft['existing_match_s3_key'] = key
        draft['held_window'] = {'s3_key': key, 'start': s.isoformat(), 'end': e.isoformat(), 'label': label}
        draft['decision_reason'] = REASON.format(label=label)
        for k in ('refresh_row_hypothesis', 'engine_date_range'):
            draft.pop(k, None)   # a reuse carries the file's own window and nothing to re-reason
        return True
    except Exception:
        return False


# ------------------------------------------------------------ on the host
_WINDOW_CACHE = {}


def read_window_from(s3_client, bucket):
    """A ``read_window(key)`` over the bucket: a ranged read of the head
    of the file (the SAMPLE SIZE row sits in the first rows). Cached per
    process; a failed read is None and is not cached."""
    def _read(key):
        key = str(key or '').strip()
        if not key:
            return None
        if key in _WINDOW_CACHE:
            return _WINDOW_CACHE[key]
        try:
            head = s3_client.get_object(Bucket=bucket, Key=key, Range='bytes=0-8191')['Body'].read()
        except Exception:
            return None
        win = file_window(head)
        if win:
            _WINDOW_CACHE[key] = win
        return win
    return _read


def settle_on_host(draft, text, H, *, catalog=None, user=None, username='', est_minutes=None, log=print):
    """The interpret paths call this once the draft's window is final.
    Flips the refresh to a reuse, lets the standing cuts promoter derive
    any cuts off the held file, and prices the decision that results.
    Returns the decision name when it acted, else ''. Never raises."""
    try:
        if not isinstance(draft, dict):
            return ''
        if str(draft.get('decision') or '').strip().lower() != 'time_shifted_refresh':
            return ''
        if not settle(draft, read_window_from(H.s3_client, H.S3_BUCKET), text):
            return ''
        try:
            H._maybe_promote_embedded_cuts_to_parent(draft, catalog)
        except Exception:
            pass
        try:
            dec, _, _ = H._normalize_v1_decision(draft)
        except Exception:
            dec = str(draft.get('decision') or 'existing_match')
        if dec == 'existing_match':
            draft['run_avid'] = True
            try:
                draft['estimated_credits'] = int(H._V1_CREDITS.get('existing_match', 0))
                draft['base_credits'] = draft['estimated_credits']
                H._apply_existing_match_retail_price(draft, user, username or '')
            except Exception:
                pass
            draft['estimated_run_minutes'] = 0
        elif callable(est_minutes) and not draft.get('estimated_run_minutes'):
            try:
                draft['estimated_run_minutes'] = est_minutes(dec, bool(draft.get('run_avid')))
            except Exception:
                pass
        try:
            H._stamp_existing_match_age(draft)
        except Exception:
            pass
        if callable(log):
            try:
                hw = draft.get('held_window') or {}
                log(f"[held-window] {hw.get('s3_key')!r} already covers {hw.get('label')}: refresh -> {dec}")
            except Exception:
                pass
        return dec
    except Exception:
        return ''
