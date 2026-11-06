#!/usr/bin/env python3
"""Size every saved Digital Twins room and test to what it stands for
(Jenna 2026-11-05), in place.

A room built on cuts stood for the whole twin (the NCIS: New York pilot
printed '16 seated, standing for 83,791,763 people' on four cuts of the
Paramount+ twin). This pass re-sizes each pilot test and focus group room
through ``digital_twin_sizing.size_session`` (the cuts' union; for a test
on a title that has aired, the title's own audience in those cuts, one
cached research call) and brings every derived people figure along
(``rescale_session_figures``: the scorecard count and line, the quadrants,
the battery rows, the questions' people, the seats' stands-for lines, the
forecast). Ratings, shares and the room's words are left alone. The twin
is backed up under ``digital-twins/_backups/`` before it is rewritten.

    python3 scripts/resize_digital_twin_rooms.py --dry-run
    python3 scripts/resize_digital_twin_rooms.py --twin digital-twins/admin/Paramount_20261030_202216_418f0c1d10.json.gz
    python3 scripts/resize_digital_twin_rooms.py            # every twin
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

BUCKET = 'dashboard-inputs'
PREFIX = 'digital-twins/'


def _twins(s3, only=None):
    if only:
        return [only]
    keys = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=PREFIX):
        for o in page.get('Contents') or []:
            k = o['Key']
            if k.endswith('.json.gz') and '/_backups/' not in k:
                keys.append(k)
    return keys


def _fc(sess):
    fc = sess.get('forecast') if isinstance(sess.get('forecast'), dict) else {}
    ep = fc.get('episode') or {}
    return {'base': fc.get('base_people'), 'week_one': ep.get('week_one_audience'), 'season_avg': (fc.get('series') or {}).get('season_average_audience')}


def _snapshot(sess):
    g = sess.get('group') or {}
    sc = sess.get('scorecard') if isinstance(sess.get('scorecard'), dict) else {}
    quads = ((sc.get('battery') or {}).get('quadrants') or []) if sc else []
    seat0 = next((r for r in (g.get('roster') or []) if isinstance(r, dict)), {})
    return {'size_people': g.get('size_people'), 'segments': [(s.get('label'), s.get('people')) for s in (g.get('segments') or []) if isinstance(s, dict)],
            'scorecard_people': sc.get('people') if sc else None, 'quad_total': next((q.get('people') for q in quads if isinstance(q, dict) and q.get('key') == 'total'), None),
            'seat0': (seat0.get('name'), seat0.get('people'), seat0.get('stands_for_people')), 'forecast': _fc(sess),
            'headline_people': [q.get('headline_people') for q in (sess.get('questions') or []) if isinstance(q, dict) and q.get('headline_people')][:3]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--twin', help='one twin key')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-research', action='store_true', help='step 1 only (the cuts), no title research')
    ap.add_argument('--force', action='store_true', help='rewrite a twin even while one of its tests is playing or preparing')
    args = ap.parse_args()
    import boto3
    from migration import digital_twin_sizing as SZ
    from migration.digital_twin_forecast import load_ledger
    from migration.digital_twin_synthesis import load_twin, save_twin
    s3 = boto3.client('s3')
    ledger = load_ledger(s3)
    ts = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
    report = []
    for key in _twins(s3, args.twin):
        if not args.dry_run and not args.no_research:
            # the research is the slow part: warm the day's cache on a throwaway copy, then size a fresh load and save at once,
            # so a test playing on the page is not overwritten with a stale record
            warm = load_twin(s3, key)
            for sess in (warm or {}).get('pilot_sessions') or []:
                if isinstance(sess, dict) and isinstance(sess.get('group'), dict):
                    SZ.size_session(warm, sess, s3=s3, research=True)
        raw = s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        twin = load_twin(s3, key)
        if not twin:
            continue
        name = (twin.get('meta') or {}).get('name')
        live = [s.get('id') for s in (twin.get('pilot_sessions') or []) if isinstance(s, dict) and str(s.get('status') or '') in ('playing', 'preparing')]
        if live and not args.dry_run and not args.force:
            print(f"[{name}] skipped: a test is {'playing or preparing'} ({live}); run again when it has stopped, or pass --force")
            continue
        changed = []
        for kind in ('pilot_sessions', 'panel_sessions'):
            for sess in twin.get(kind) or []:
                g = sess.get('group') if isinstance(sess, dict) else None
                if not isinstance(g, dict) or not g.get('roster'):
                    continue
                before = _snapshot(sess)
                before_basis = dict(((g.get('sizing') or {}).get('basis') or {})) if isinstance(g.get('sizing'), dict) else {}
                sz = SZ.size_session(twin, sess, s3=s3, research=(kind == 'pilot_sessions' and not args.no_research))
                moved = SZ.rescale_session_figures(sess, twin=twin, s3=s3, ledger=ledger) if kind == 'pilot_sessions' else SZ.rescale_session_figures(sess)
                after = _snapshot(sess)
                if before != after:
                    changed.append(sess.get('id'))
                basis = (sz or {}).get('basis') or {}
                title = basis.get('title') or {}
                report.append({'twin': name, 'kind': kind, 'id': sess.get('id'), 'title': sess.get('title'), 'mode': sess.get('mode'), 'status': sess.get('status'),
                               'before': before, 'after': after, 'step': (sz or {}).get('step'), 'share_of_title': basis.get('share_of_title'),
                               'title_research': {k: title.get(k) for k in ('name', 'released', 'released_on', 'audience_low', 'audience_range', 'measure', 'skews', 'note')} if title else None,
                               'changed': before != after})
                print(f"[{name}] {kind[:-9]} {sess.get('id')} '{sess.get('title')}' ({sess.get('mode') or 'room'}, {sess.get('status') or ''}): "
                      f"{before['size_people']:,} -> {after['size_people']:,} people, step={(sz or {}).get('step')}"
                      + (f", scorecard {before['scorecard_people']} -> {after['scorecard_people']}" if before['scorecard_people'] else '')
                      + (f", forecast week one {before['forecast']['week_one']} -> {after['forecast']['week_one']}" if before['forecast'].get('week_one') else '')
                      + (f", share of title {basis.get('share_of_title')}" if basis.get('share_of_title') else ''))
                if title:
                    print(f"      title: {json.dumps({k: title.get(k) for k in ('name', 'released', 'released_on', 'audience_low', 'audience_range', 'measure', 'skews', 'note')}, default=str)[:900]}")
        if changed and not args.dry_run:
            base = key.rsplit('/', 1)[-1].replace('.json.gz', '')
            bkey = f"{PREFIX}_backups/{base}.pre_sizing_{ts}.json.gz"
            s3.put_object(Bucket=BUCKET, Key=bkey, Body=raw, ContentType='application/json', ContentEncoding='gzip')
            save_twin(s3, key, twin)
            print(f"[{name}] saved; backup {bkey}; sessions changed: {changed}")
        elif changed:
            print(f"[{name}] dry run; would change {changed}")
        else:
            print(f"[{name}] nothing to change")
    out = f"/tmp/resize_digital_twin_rooms_{ts}.json"
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, indent=1, default=str)
    print(f"report: {out}")


if __name__ == '__main__':
    main()
