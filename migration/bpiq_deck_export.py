"""Brand Partnership IQ: the Download as a deck response (2026-10-29).

The Brand Partnership tab POSTs the run it is showing (with any rate
edits and the active year slice) to /api/brand-partnership-iq/export-deck
and streams the .pptx back. The route in app.py is a thin wrapper over
`deck_response` here, so the same adapter mounts on a bare Flask app in
scripts/test_bpiq_deck_export.py with no S3, no auth and no app import.

Behaviour, unchanged from the inline route it replaces:

  * the deck builder cannot be imported -> 503 with a readable error
  * no `data` in the body                -> 400
  * the build raised                      -> 500 (traceback printed here)
  * otherwise                             -> the .pptx as an attachment

The cover photo and the category come from the metadata sidecar when the
caller sends an s3_key (the admin photo on the cover slide; the
automotive policy with no conversion slide), else from the body itself.
"""
from __future__ import annotations

import io
import traceback
from typing import Callable, Optional

PPTX_MIME = ('application/vnd.openxmlformats-officedocument.'
             'presentationml.presentation')


def resolve_meta(payload: dict, data: dict, *, meta_for_key: Optional[Callable] = None,
                 s3_prefix: str = '') -> tuple:
    """(image_url, category) for the deck. The sidecar fills what the body
    left blank when a key is sent; a sidecar failure never blocks the deck."""
    payload = payload if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    s3_key = str(payload.get('s3_key') or '').strip()
    image_url = str(payload.get('image_url') or '').strip()
    category = str(payload.get('category') or data.get('category') or '').strip()
    if s3_key and meta_for_key is not None:
        try:
            bare_key = s3_key.replace(s3_prefix, '') if s3_prefix else s3_key
            meta = meta_for_key(bare_key) or {}
            image_url = image_url or str(meta.get('image_url') or '').strip()
            category = category or str(meta.get('category') or '').strip()
        except Exception:
            pass
    return (image_url or None), (category or None)


def deck_response(payload, *, meta_for_key: Optional[Callable] = None, s3_prefix: str = '',
                  s3_client=None):
    """The Flask response for one export-deck POST body. `s3_client` is
    the app's own client for the cover photo read (a
    `/api/profile-image-file/<key>` image lives in the private bucket);
    None lets the builder open one."""
    from flask import jsonify, send_file
    # Imported here so a missing dependency surfaces as a readable 503
    # instead of taking the app down at startup.
    try:
        from migration.bpiq_deck_builder import build_deck, suggested_filename
    except ImportError as exc:
        return jsonify({
            'success': False,
            'error': (
                'Deck builder is not available on this server '
                f'({getattr(exc, "name", None) or exc}). The python-pptx and Pillow '
                'packages must be installed in the deployment.'
            ),
        }), 503
    try:
        payload = payload if isinstance(payload, dict) else {}
        data = payload.get('data') or {}
        if not data or not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'data payload required'}), 400
        image_url, category = resolve_meta(payload, data, meta_for_key=meta_for_key,
                                           s3_prefix=s3_prefix)
        pptx_bytes = build_deck(data, image_url=image_url, category=category, s3_client=s3_client)
        fname = suggested_filename(data, ext='pptx')
        # Streamed as an attachment; the browser saves it from the
        # Content-Disposition header.
        return send_file(
            io.BytesIO(pptx_bytes),
            mimetype=PPTX_MIME,
            as_attachment=True,
            download_name=fname,
            max_age=0,
        )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(exc)}), 500
