"""The store's ops surface (Prometheus rebuild stage 6 in production,
Jenna 2026-11-20): two read-only routes for the operator.

  GET  /api/prometheus/ops/store-status     the switches, the row counts by
                                            family, the mirror's counters and
                                            this process's store faults
  GET  /api/prometheus/ops/store-parity     the mirror against S3 in this
                                            process (store.production_parity):
                                            ?since=<ISO> and ?limit=<n> optional
  POST /api/prometheus/ops/store-reconcile  the nightly reconcile, now, under
                                            its lock (store_sweep.run_once;
                                            S3 wins, counts back)

Both answer only to the ops secret (``CRON_SECRET``, the one the cron
routes use) in the ``X-Ops-Secret`` header, and say nothing without it
(404). Counts and keys only; never a document body, never a reader
surface. Read-only on S3 and on the store.
"""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, jsonify, request

from . import store as _store

bp = Blueprint('prometheus_store_ops', __name__, url_prefix='/api/prometheus/ops')


def _authorized():
    secret = os.environ.get('CRON_SECRET', '')
    given = request.headers.get('X-Ops-Secret', '')
    return bool(secret) and bool(given) and hmac.compare_digest(given, secret)


def _s3_and_bucket():
    """The host's S3 client and bucket, or (None, None) before bind."""
    try:
        from .host import host
        if host.has('s3_client'):
            return host.s3_client, host.bucket
    except Exception:
        pass
    return None, None


def _counts_by_family():
    out = {}
    for fam in ('thread', 'read', 'ledger_index'):
        out[fam] = _store.docs_count(fam)
    return out


def _store_faults():
    try:
        from . import faults as _f
        snap = _f.snapshot()
        return {k: v for k, v in (snap.get('counts') or {}).items() if str(k).startswith('store')}
    except Exception:
        return {}


@bp.route('/store-status', methods=['GET'])
def store_status():
    if not _authorized():
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'enabled': _store.enabled(),
        'read_enabled': _store.read_enabled(),
        'rows': _store.docs_count(),
        'by_family': _counts_by_family(),
        'stats': dict(_store.STATS),
        'faults': _store_faults(),
        'pid': os.getpid(),
        'version': os.environ.get('RENDER_GIT_COMMIT', '')[:12],
    })


@bp.route('/store-reconcile', methods=['POST'])
def store_reconcile():
    if not _authorized():
        return jsonify({'error': 'not found'}), 404
    s3, bucket = _s3_and_bucket()
    if s3 is None or not _store.enabled():
        return jsonify({'enabled': _store.enabled(), 'note': 'the store is off or no S3 client is bound'})
    from . import store_sweep as _ssw
    out = _ssw.run_once(s3, bucket, owner=f'ops:{os.getpid()}', force=True)
    if out is None:
        return jsonify({'ran': False, 'note': 'the lock is held by another run'}), 409
    return jsonify(dict(out, ran=True))


@bp.route('/store-parity', methods=['GET'])
def store_parity():
    if not _authorized():
        return jsonify({'error': 'not found'}), 404
    since = request.args.get('since') or None
    try:
        limit = max(1, int(request.args.get('limit') or 2000))
    except Exception:
        limit = 2000
    s3, bucket = _s3_and_bucket()
    if s3 is None or not _store.enabled():
        return jsonify({'enabled': _store.enabled(), 'rows': _store.docs_count(),
                        'note': 'the store is off or no S3 client is bound'})
    return jsonify(_store.production_parity(s3, bucket, since=since, limit=limit))
