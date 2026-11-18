"""Every door that takes a key from a request asks here first (2026-11-18).

The defect this closes: several routes took a storage key straight from
the request (a path parameter, a query string, a JSON body, the page
context a chat message carries) and read, signed, attached, renamed or
deleted whatever it named. A signed-in seat could ask the profile
download route, the image routes or the "email me this file" order for
an object that is not a deliverable at all (the account store, the
catalog's own index, a state document, a copy kept before a correction)
and receive it.

The rule, in one place:

  * Default deny. A key is served only when it sits in the location the
    door it came through is for (``servable(door, key)``): a profile
    route serves a profile file, an image route an image, a tracker
    route a tracker. Each door's location is listed in ``DOORS`` below,
    taken from what its own list shows.
  * Never, through any door, for any seat: the account and state
    folders, indexes, logs, ledgers, thread stores, credentials, and
    any internal copy (``internal_copies.is_internal_copy_key``).
    ``is_never_servable`` is the belt under the allow-list.
  * Odd keys are refused before anything else: a parent step, a leading
    slash, a backslash, an encoded slash, dot, percent or null, a
    control character, a full address, a bucket name in front of the
    key. Normalised once (``clean_key``), then checked.
  * A refusal reads as plain not found, the same words a missing file
    gets, with no hint about why. ``refuse`` writes the audit line
    (who asked, through which door, for what) to the server log.

WHO may open a file is still the product's own access model (a seat's
run list, its journeys, its company); this module decides WHAT a door
may serve at all, and the access helpers call it first.

Stdlib only, no I/O, never raises. Nothing here deletes, moves or
changes anything.
"""

from __future__ import annotations

import re

import internal_copies as _ic

__all__ = [
    'NOT_FOUND', 'DOORS', 'clean_key', 'is_never_servable', 'servable',
    'refuse', 'safe_id', 'safe_name', 'image_key', 'profile_key',
    'under_prefix', 'link_targets', 'scrub_page_context', 'audit_line',
]

# What a refused request reads as. Plain, and the same for every caller.
NOT_FOUND = 'not found'

_MAX_KEY = 600

# Top-level folders that are never a deliverable, whichever door asks.
_NEVER_FOLDERS = frozenset({
    'system', 'metadata', 'reference', 'audit_logs', 'logs', 'log',
    'tokens', 'token', 'config', 'secrets', 'secret', 'admin',
    'credentials', 'persona_cache', 'rankers_cache', 'genpop_cache',
    'gen_pop_hostmap_sync', 'trends_iq_cookies', 'trends_iq_cookie_gaps',
    'trends_iq_storage_state', 'trends_iq_debug', 'trends_iq_run_guard',
    'trends_iq_alerts', 'backups', 'backup',
})
# File names that mark a credential or an account store wherever they sit.
_NEVER_NAME_RX = re.compile(
    r'^(?:users|tokens?|secrets?|credentials?|api[_\-]?keys?|passwords?|'
    r'.*[_\-](?:tokens?|secrets?|credentials?|api[_\-]?keys?|passwords?|'
    r'storage[_\-]?state|service[_\-]?account|private[_\-]?key))'
    r'\.(?:json|txt|pem|key|env|ya?ml)$', re.I)

# Buckets the application reads. A key that starts with one of these
# names is a bucket smuggled into the key, never a file name.
_BUCKET_NAMES = frozenset({
    'dashboard-inputs', 'svod-acquisition', 'ticket-sales-iq',
    'ticket-sales-tracker', 'sf-lf-conversion', 'flywheeliq', 'talent-fit',
    'aggregated-tickers', 'llmo', 'helm-iq-financials',
})

# After the web framework has decoded a path once, any of these left in
# the key is a second layer of encoding.
_ENCODED_RX = re.compile(r'%(?:2f|5c|2e|25|00|0a|0d|3a)', re.I)
_SCHEME_RX = re.compile(r'^[a-z][a-z0-9+.\-]*:', re.I)
_ID_RX = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]{0,159}$')

_IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.gif')


def clean_key(raw):
    """``raw`` as a plain storage key, or None when it is not one.

    Refused: not a string, empty, too long, surrounding whitespace, a
    control character or null, a backslash, a leading slash, an empty
    segment (``a//b``), a ``.`` or ``..`` segment, percent-encoded
    separators left after one decode, a scheme (``https:``, ``s3:``,
    ``arn:``), a query or fragment mark."""
    try:
        if not isinstance(raw, str):
            return None
        k = raw
        if not k or len(k) > _MAX_KEY or k != k.strip():
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in k):
            return None
        if '\\' in k or k.startswith('/') or k.endswith('/'):
            return None
        if '?' in k or '#' in k or '://' in k or _SCHEME_RX.match(k):
            return None
        if _ENCODED_RX.search(k):
            return None
        segs = k.split('/')
        for s in segs:
            if not s or s != s.strip() or s in ('.', '..') or s.startswith('..'):
                return None
        return k
    except Exception:
        return None


def is_never_servable(key) -> bool:
    """True for a key no door serves to anyone: an internal copy, a key
    under an account / state / log / credential folder, a bucket name
    in front of the key, a credential-shaped name."""
    try:
        k = str(key or '').replace('\\', '/').strip().lstrip('/')
        if not k:
            return True
        if _ic.is_internal_copy_key(k):
            return True
        segs = [s for s in k.split('/') if s]
        first = segs[0].lower()
        if len(segs) > 1 and (first in _NEVER_FOLDERS or first in _BUCKET_NAMES):
            return True
        if first.startswith('.'):
            return True
        if any(s.startswith('.') for s in segs):
            return True
        if _NEVER_NAME_RX.match(segs[-1]):
            return True
        return False
    except Exception:
        return True


def _ext(k, exts):
    return k.lower().endswith(tuple(exts))


def _root(exts):
    """A file at the top of its bucket with one of ``exts``."""
    def rule(k, staff):
        return '/' not in k and _ext(k, exts)
    return rule


def _root_or(folders, exts):
    """A file at the top of its bucket, or one level under one of
    ``folders``, with one of ``exts``."""
    def rule(k, staff):
        if not _ext(k, exts):
            return False
        parts = k.split('/')
        if len(parts) == 1:
            return True
        return len(parts) == 2 and (parts[0] + '/') in folders
    return rule


def _under(prefix, exts, depth=(1, 1)):
    """A file ``depth`` levels under ``prefix`` with one of ``exts``."""
    lo, hi = depth

    def rule(k, staff):
        if not k.startswith(prefix) or not _ext(k, exts):
            return False
        rest = k[len(prefix):]
        n = len(rest.split('/'))
        return bool(rest) and lo <= n <= hi
    return rule


def _images(k, staff):
    if not _ext(k, _IMAGE_EXT):
        return False
    parts = k.split('/')
    if len(parts) == 2 and (parts[0] + '/') in _IMAGE_FOLDERS:
        return True
    return len(parts) == 3 and (parts[0] + '/' + parts[1] + '/') in _IMAGE_FOLDERS_2


_IMAGE_FOLDERS = frozenset({
    'profile-images/', 'ticker-images/', 'ticket-sales-images/',
    'ticket-sales-tracker-images/', 'brand-partnership-iq-images/',
    'journey-iq-images/',
})
_IMAGE_FOLDERS_2 = frozenset({'digital-twins/covers/', 'digital-twins/portraits/'})


def _subscriber(k, staff):
    return _ic.subscriber_iq_key_servable(k)


def _delivered(k, staff):
    """A file Prometheus wrote for a reader and linked in a reply: a
    data file, a deck, a chart."""
    if k.startswith('generated_data/'):
        return _ext(k, ('.csv', '.xlsx', '.pdf')) and 2 <= len(k.split('/')) - 1 <= 3
    if k.startswith('generated_decks/'):
        return _ext(k, ('.pptx', '.pdf', '.docx')) and 2 <= len(k.split('/')) - 1 <= 3
    return False


# door -> (bucket the door reads, rule). The rule is what the door's own
# list shows today; nothing here widens a list.
DOORS = {
    # Profile IQ: the selector invariant, a .csv at the top of the bucket.
    'profile': ('dashboard-inputs', _root(('.csv',))),
    # Subscriber IQ: live trackers only (internal_copies).
    'subscriber': ('svod-acquisition', _subscriber),
    'ticket_sales': ('ticket-sales-iq', _root(('.csv',))),
    'ticket_sales_tracker': ('ticket-sales-tracker', _root(('.csv',))),
    'fin': ('aggregated-tickers', _root(('.csv',))),
    'sf_conversion': ('sf-lf-conversion', _root_or(('purgatory/',), ('.csv',))),
    'flywheel_conversion': ('flywheeliq', _root_or(('purgatory/',), ('.csv',))),
    'talent_fit': ('talent-fit', _root(('.json',))),
    'roas': ('dashboard-inputs', _under('roas-iq/', ('.json',))),
    'ecommerce': ('dashboard-inputs', _under('ecommerce-iq/', ('.json',))),
    'brand_partnership': ('dashboard-inputs', _under('brand-partnership-iq/', ('.json',))),
    # Digital Journey IQ runs: journey-iq/<run>, journey-iq/<owner>/<run>,
    # journey-iq/demos/<id>, journey-iq/archive/... (archive is staff only
    # in the route itself).
    'journey': ('dashboard-inputs', _under('journey-iq/', ('.json', '.json.gz'), (1, 3))),
    'twin': ('dashboard-inputs', _under('digital-twins/', ('.json.gz',), (1, 2))),
    'image': ('dashboard-inputs', _images),
    'helm': ('helm-iq-financials', _root(('.csv',))),
    # A file linked in a chat reply.
    'delivered': ('dashboard-inputs', _delivered),
    # Admin file manager, archived profile files.
    'archived_profile': ('dashboard-inputs', _under('historic/', ('.csv',))),
}


def servable(door, raw, *, staff=False):
    """The clean key when ``door`` may serve ``raw``, else None.

    Default deny: an unknown door serves nothing. The same answer for
    every seat unless a rule says otherwise; WHO may open the file is
    asked afterwards by the product's own access helper."""
    try:
        spec = DOORS.get(str(door or ''))
        if spec is None:
            return None
        k = clean_key(raw)
        if k is None:
            return None
        if is_never_servable(k):
            return None
        return k if spec[1](k, bool(staff)) else None
    except Exception:
        return None


def profile_key(raw):
    """Shorthand for the Profile IQ door."""
    return servable('profile', raw)


def image_key(raw):
    """Shorthand for the image door."""
    return servable('image', raw)


def under_prefix(prefix, raw, exts):
    """The full key for a route that accepts either a bare name or the
    full key under ``prefix`` (the results routes), or None."""
    try:
        k = clean_key(raw)
        if k is None:
            return None
        full = k if k.startswith(prefix) else prefix + k
        if is_never_servable(full) or not _ext(full, exts):
            return None
        rest = full[len(prefix):]
        if not rest or '/' in rest:
            return None
        return full
    except Exception:
        return None


def safe_id(raw, max_len=160):
    """``raw`` when it is a plain identifier that can be one segment of
    a key (letters, digits, ``_ . -``; no separator, no leading dot or
    underscore), else None."""
    try:
        s = raw if isinstance(raw, str) else (str(raw) if isinstance(raw, int) else None)
        if not s or len(s) > max_len or not _ID_RX.match(s) or '..' in s:
            return None
        return s
    except Exception:
        return None


def safe_name(raw, max_len=200):
    """``raw`` when it can be the name part of a file an admin renames
    to: no separator, no leading dot or underscore, no parent step, no
    control character, no correction suffix. Else None."""
    try:
        if not isinstance(raw, str):
            return None
        s = raw.strip()
        if not s or len(s) > max_len:
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in s):
            return None
        if '/' in s or '\\' in s or '..' in s or s[0] in '._':
            return None
        if '.pre_' in s.lower() or _ENCODED_RX.search(s):
            return None
        return s
    except Exception:
        return None


_CHART_RX = re.compile(r'^system/prometheus_charts/[0-9a-f]{8,64}\.png$')


def link_targets(file, *, bucket='dashboard-inputs', subscriber_bucket='svod-acquisition',
                 extra_buckets=()):
    """Where a file resolved from a link in a chat turn may be read
    from: [(bucket, key, kind)] in the order to try, or [].

    The turn can come from the request, so the link is not trusted. The
    key must name a file Prometheus writes for readers (kind
    ``delivered``: a data file, a deck; ``chart``: a chart image), a
    profile file in the dashboard bucket (``profile``), or a live
    tracker in the Subscriber IQ bucket (``subscriber``). Any other
    bucket or key gives nothing. ``profile`` and ``subscriber`` still
    need the reader's own access; the caller asks."""
    try:
        if not isinstance(file, dict):
            return []
        raw = file.get('s3_key')
        if clean_key(raw) is None:
            return []
        out, seen = [], set()
        for b in [file.get('bucket'), bucket, *list(extra_buckets or ())]:
            b = str(b or '')
            if not b or b in seen:
                continue
            seen.add(b)
            if b == bucket:
                if _CHART_RX.match(raw):
                    out.append((b, raw, 'chart'))
                elif servable('delivered', raw):
                    out.append((b, raw, 'delivered'))
                elif servable('profile', raw):
                    out.append((b, raw, 'profile'))
            elif b == subscriber_bucket:
                if servable('subscriber', raw):
                    out.append((b, raw, 'subscriber'))
        return out
    except Exception:
        return []


def scrub_page_context(ctx, allowed):
    """The page context a chat message carries, with every storage key
    in it that ``allowed(kind, key)`` does not pass removed.

    ``allowed`` is the host's access check: ``allowed('profile', key)``
    for the open profile, its cuts and extras; ``allowed('journey',
    key)`` for an open Digital Journey. A refused key is dropped
    quietly, as if nothing were open; the rest of the context rides.
    Returns (clean_context, refused_keys). Never raises."""
    refused = []
    if not isinstance(ctx, dict):
        return ctx, refused
    try:
        out = dict(ctx)

        def _ok(kind, key):
            try:
                return bool(allowed(kind, key))
            except Exception:
                return False

        prim = out.get('primary')
        if isinstance(prim, dict) and prim.get('s3_key'):
            pk = prim.get('s3_key')
            if not (isinstance(pk, str) and _ok('profile', pk)):
                refused.append(str(pk)[:200])
                p2 = dict(prim)
                p2.pop('s3_key', None)
                out['primary'] = p2 if any(p2.get(f) for f in ('name',)) else None
        for field in ('cuts', 'extras', 'other_tabs', 'compare'):
            items = out.get(field)
            if not isinstance(items, list):
                continue
            keep = []
            for it in items:
                if isinstance(it, dict) and it.get('s3_key'):
                    ik = it.get('s3_key')
                    if not (isinstance(ik, str) and _ok('profile', ik)):
                        refused.append(str(ik)[:200])
                        continue
                keep.append(it)
            out[field] = keep
        vc = out.get('view_context')
        if isinstance(vc, dict):
            vc2 = dict(vc)
            data = vc2.get('data')
            if isinstance(data, dict):
                d2 = dict(data)
                for f in ('key', 'run_key', 's3_key'):
                    v = d2.get(f)
                    if v is None:
                        continue
                    if not isinstance(v, str):
                        d2.pop(f, None)
                        continue
                    if v.startswith('journey-iq/') or v.startswith('__demo_'):
                        kind = 'journey'
                    elif v.startswith('digital-twins/'):
                        kind = 'twin'
                    elif '/' in v or '\\' in v:
                        # a path that is not one of the product folders a
                        # view carries: never a deliverable
                        kind = 'other'
                    else:
                        continue          # an identity string, not a key
                    if not _ok(kind, v):
                        refused.append(v[:200])
                        d2.pop(f, None)
                vc2['data'] = d2
            sk = vc2.get('subject_key')
            if isinstance(sk, str) and sk.startswith('journey:'):
                jk = sk.split(':', 1)[1]
                if not _ok('journey', jk):
                    refused.append(jk[:200])
                    vc2.pop('subject_key', None)
            out['view_context'] = vc2
        return out, refused
    except Exception:
        return None, refused


def audit_line(door, key, username='', why=''):
    """The audit line for a refusal. Key and name are cut and stripped
    of line breaks; nothing else rides."""
    def _c(s, n):
        return re.sub(r'[\r\n\t]+', ' ', str(s or ''))[:n]
    return '[file-access] refused door=%s user=%s key=%r%s' % (
        _c(door, 40), _c(username, 80) or '-', _c(key, 240),
        (' why=' + _c(why, 60)) if why else '')


def refuse(door, key, username='', why=''):
    """Write the audit line for a refused key to the server log. The
    caller answers the request with its own not-found reply."""
    try:
        print(audit_line(door, key, username, why), flush=True)
    except Exception:
        pass
    return None
