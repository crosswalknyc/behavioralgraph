"""The desktop app's release surface (2026-10-15, Jenna: "will Prometheus
automatically update within that shell if someone has it installed and
we update the master brain").

Three things keep an installed Prometheus current without a reinstall:

* The brain is the service. Every ask from the app goes through the one
  door, so rules, prices, lanes and the knowledge pack apply the moment
  they deploy. Nothing here is needed for that.
* The page the window shows is served from here (``/desktop/ui/``) and
  the app loads it whenever the manifest says the installed shell can
  run it (``ui_min_shell``); the bundled copy is only the offline
  fallback. UI changes ship with every deploy.
* The shell itself (the Python around the window) carries a version; the
  manifest publishes the latest one and where to download it, and the
  page shows a one-line update bar when the installed shell is behind.

The manifest is ``desktop_manifest.json`` next to this file. The builds
live in S3 under ``system/desktop/`` and are handed out through a short
presigned redirect, so nothing in the bucket is public.
"""
from __future__ import annotations

import json
import os

from .host import host

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HERE, 'desktop_manifest.json')
UI_DIR = os.path.join(HERE, 'desktop_ui')
S3_PREFIX = 'system/desktop/'
API = '/api/prometheus/v1/desktop'
PLATFORMS = ('mac', 'mac-intel', 'win', 'win-zip')


def load_manifest():
    try:
        with open(MANIFEST_PATH, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
    except Exception:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def manifest_doc():
    """What the app reads at launch: version, notes, where the page and
    the downloads are. Paths are relative; the app prefixes its service
    URL so a seat pointed at another host still resolves."""
    m = load_manifest()
    files = m.get('files') or {}
    return {
        'schema': 'prometheus.desktop.v1',
        'version': str(m.get('version') or '0.0.0'),
        'min_version': str(m.get('min_version') or ''),
        'notes': str(m.get('notes') or ''),
        'ui_url': f'{API}/ui/',
        'ui_min_shell': str(m.get('ui_min_shell') or m.get('version') or '0.0.0'),
        'download': {k: f'{API}/download/{k}' for k in PLATFORMS if files.get(k)},
        'files': {k: str(files[k]) for k in PLATFORMS if files.get(k)},
    }


def download_target(which):
    """(s3_key, filename) for a platform build, or (None, None)."""
    which = str(which or '').strip().lower()
    files = (load_manifest().get('files') or {})
    name = files.get(which)
    if not name or which not in PLATFORMS:
        return None, None
    return S3_PREFIX + str(name), str(name)


def presigned_download(which, expires=3600):
    """A short-lived URL for the build, or None when the build is not
    published (manifest or object missing)."""
    key, name = download_target(which)
    if not key:
        return None
    s3 = host.s3_client if host.has('s3_client') else None
    bucket = host.bucket if host.has('bucket') else 'dashboard-inputs'
    if s3 is None:
        return None
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None
    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key,
                    'ResponseContentDisposition': f'attachment; filename="{name}"'},
            ExpiresIn=int(expires))
    except Exception:
        return None


def ui_index_path():
    return os.path.join(UI_DIR, 'index.html')
