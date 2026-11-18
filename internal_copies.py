"""Internal copies never reach a reader (2026-11-17).

Standing rule (in-place corrections): when a delivered file is corrected
in place, the copy of what it held before is kept for internal audit
only and is never visible to a client. Those copies live beside the
live files in the same buckets, so every surface that lists, opens,
downloads or attaches a file by its key asks this module first.

The defect this closes: the Subscriber IQ list showed every ``.csv`` in
its bucket, including 272 copies under ``_backups/``, next to the 311
live trackers, to every seat. A copy carries the figures the correction
replaced, so a reader could open the old number beside the current one.

One predicate, used everywhere:

    is_internal_copy_key(key)        any bucket, any product
    subscriber_iq_key_servable(key)  what the Subscriber IQ tab may list
                                     and open (live trackers only)

A key is an internal copy when ANY of these holds:

  * a path segment starts with an underscore (``_backups/``,
    ``_retired/``, ``_quarantine/``, ``_tmp/``, ``_archive/``,
    ``_cache/``, a root file named ``_something.csv``). Story journey
    ids (``__demo_...``) are product ids, not copies, and pass.
  * a folder is named ``backups`` or ``backup``.
  * the name carries a ``.pre_`` correction suffix
    (``X.csv.pre_adult_signups_20261110_225014.csv``,
    ``X.pre_universe_fix_20261118_014045.csv``,
    ``X.pre_patch.bak.csv``).

Nothing here deletes, moves or renames anything. The copies stay where
they are. A refused key reads as not found to whoever asked, staff
included; the words a reader sees never say what the key was.

Stdlib only, no I/O, never raises.
"""

from __future__ import annotations

__all__ = [
    'NOT_FOUND', 'SUBIQ_NOT_FOUND', 'is_internal_copy_key',
    'subscriber_iq_bare_key', 'subscriber_iq_key_servable',
    'drop_internal', 'subscriber_iq_live_keys',
]

# What a refused request reads as. Plain, and the same for every caller.
NOT_FOUND = 'not found'
SUBIQ_NOT_FOUND = 'Subscriber IQ file not found'

_INTERNAL_FOLDERS = frozenset({'backups', 'backup'})
_PRODUCT_ID_PREFIX = '__demo_'          # story journey ids, never a copy
_SUBIQ_BUCKET_PREFIX = 'svod-acquisition/'
# Folders the Subscriber IQ tab has never listed: archived older files
# and unreleased ones. A direct request for one is refused the same way.
_SUBIQ_HIDDEN_PREFIXES = ('historic/', 'purgatory/')


def _segments(key):
    k = str(key or '').replace('\\', '/').strip()
    return k, [s.strip() for s in k.split('/') if s.strip()]


def is_internal_copy_key(key) -> bool:
    """True when ``key`` names an internal copy (see the module note).

    Works on a full key, a bare key, or a key that still carries a
    bucket-style prefix (``svod-acquisition/_backups/x.csv``). An empty
    key is not a copy; callers decide what an empty key means.
    """
    try:
        k, segs = _segments(key)
        if not k or not segs:
            return False
        if '.pre_' in k.lower():
            return True
        last = len(segs) - 1
        for i, seg in enumerate(segs):
            if seg.startswith('_') and not seg.startswith(_PRODUCT_ID_PREFIX):
                return True
            if i < last and seg.lower() in _INTERNAL_FOLDERS:
                return True
        return False
    except Exception:
        # Fail closed: a key that cannot be read is not shown.
        return True


def subscriber_iq_bare_key(key) -> str:
    """The key inside the Subscriber IQ bucket (the admin file manager
    prefixes it with the bucket name)."""
    k = str(key or '').strip()
    if k.startswith(_SUBIQ_BUCKET_PREFIX):
        k = k[len(_SUBIQ_BUCKET_PREFIX):]
    return k


def subscriber_iq_key_servable(key) -> bool:
    """True when the Subscriber IQ tab may list and open ``key``: a live
    tracker ``.csv`` that is not archived, not unreleased and not an
    internal copy. The same answer for every seat, staff included."""
    try:
        k = subscriber_iq_bare_key(key)
        if not k or not k.lower().endswith('.csv'):
            return False
        if k.replace('\\', '/').lstrip('/').startswith(_SUBIQ_HIDDEN_PREFIXES):
            return False
        return not is_internal_copy_key(k)
    except Exception:
        return False


def drop_internal(keys):
    """``keys`` without the internal copies, order kept."""
    return [k for k in (keys or []) if not is_internal_copy_key(k)]


def subscriber_iq_live_keys(keys):
    """The keys of a Subscriber IQ bucket listing the tab may show."""
    return [k for k in (keys or []) if subscriber_iq_key_servable(k)]
