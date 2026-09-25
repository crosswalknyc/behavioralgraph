"""Un-escape the titles our own Netflix read mangled.

`netflix._pinot_titles` now decodes the JavaScript escapes the Apollo
cache carries, so nothing new arrives escaped. This repairs the names
already written: one entry in the estimates store and whatever the
Netflix snapshots are holding today.

Names only. No reading moves, no row is reordered, no rank changes.
The backslash is an artifact of how we read the page, not something
the platform published, so taking it out is repairing our own read.
Both spellings already normalise to the same key, so nothing is
re-keyed and nothing can collide.

    python3 -m scripts.trends_scrapers._repair_escaped_titles --dry-run
    python3 -m scripts.trends_scrapers._repair_escaped_titles
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger(__name__)

_BUCKET = 'dashboard-inputs'
_TITLE_FIELDS = ('title', 'display_title')


def _clean(s: str) -> str:
    from scripts.trends_scrapers.netflix import _js_unescape
    return _js_unescape(s)


def _fix_rows(obj, trail: list) -> int:
    """Walk any nested structure and un-escape every title field."""
    n = 0
    if isinstance(obj, dict):
        for f in _TITLE_FIELDS:
            v = obj.get(f)
            if isinstance(v, str) and '\\' in v:
                fixed = _clean(v)
                if fixed != v:
                    trail.append((v, fixed))
                    obj[f] = fixed
                    n += 1
        for v in obj.values():
            n += _fix_rows(v, trail)
    elif isinstance(obj, list):
        for v in obj:
            n += _fix_rows(v, trail)
    return n


def _repair_s3_json(s3, key: str, dry_run: bool) -> int:
    try:
        body = s3.get_object(Bucket=_BUCKET, Key=key)['Body'].read()
    except Exception as e:
        logger.info("skip %s (%s)", key, e)
        return 0
    doc = json.loads(body.decode('utf-8'))
    trail: list = []
    n = _fix_rows(doc, trail)
    if not n:
        logger.info("clean  %s", key)
        return 0
    for was, now in trail:
        logger.info("  %s: %r -> %r", key, was, now)
    if dry_run:
        logger.info("dry-run %s: would fix %d name(s)", key, n)
        return n
    s3.put_object(Bucket=_BUCKET, Key=key,
                  Body=json.dumps(doc, ensure_ascii=False).encode('utf-8'),
                  ContentType='application/json')
    logger.info("fixed  %s: %d name(s)", key, n)
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    import boto3
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION')
                      or 'us-east-2')

    today = date.today().isoformat()
    total = 0
    for key in (f'trends_iq_snapshots/latest/netflix.json',
                f'trends_iq_snapshots/{today}/netflix.json',
                f'trends_iq_snapshots/latest/stream_estimates.json',
                f'trends_iq_snapshots/{today}/stream_estimates.json'):
        total += _repair_s3_json(s3, key, a.dry_run)

    logger.info("%s %d name(s)", 'would fix' if a.dry_run else 'fixed', total)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
