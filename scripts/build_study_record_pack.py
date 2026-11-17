#!/usr/bin/env python3
"""Write the packaged copy of a decision of record that Prometheus answers
study questions from (prometheus.study_record, Cara Dorfman 2026-11-16).

The record itself is the one the issue banked
(scripts/bank_the_read_amazon_vs_direct.py); this writes the same figures,
definitions and per-service rows as one file the answer lane reads with no
store behind it. Nothing here is typed twice: every value comes from the
bank script.

    python3 scripts/build_study_record_pack.py            # write
    python3 scripts/build_study_record_pack.py --check    # exit 1 when the file is stale
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

OUT_DIR = os.path.join(HERE, '..', 'prometheus', 'study_records')

COLUMNS = [
    # key, label, unit
    ('starts', 'Starts', 'count'),
    ('share', 'Share of path', 'pct'),
    ('churn', 'Monthly churn', 'pct'),
    ('d30', 'Day 30', 'pct'),
    ('d60', 'Day 60', 'pct'),
    ('d90', 'Day 90', 'pct'),
    ('samplers', 'One title, gone in 35 days', 'pct'),
    ('m2m', 'Month to month', 'pct'),
    ('annual', 'Annual plans and bundles', 'pct'),
    ('months', 'Months kept in first 12', 'months'),
    ('price', 'Monthly list price', 'usd'),
    ('value', 'First-year value at list', 'usd'),
    ('switch', 'Cancellers starting another service in 30 days', 'pct'),
    ('winback', 'Cancellers back within 90 days', 'pct'),
]


def amazon_vs_direct():
    import bank_the_read_amazon_vs_direct as b
    block = b.record_block()
    order = sorted(b.COL, key=lambda k: b.COL[k])
    assert order == [c[0] for c in COLUMNS], order
    rows = {}
    for path, key in (('Via Amazon', 'amazon'), ('Direct', 'direct')):
        rows[key] = {svc: dict(zip(order, vals)) for svc, vals in b.ROWS[path].items()}
    return {
        'id': b.CAMPAIGN_ID,
        'source': block['source'],
        'subject': b.SUBJECT,
        'title': block['question'].rstrip('.'),
        'short_name': 'the Amazon vs direct issue of The Read',
        'family': block['family'],
        'window': dict(b.WINDOW),
        'universe': b.UNIVERSE,
        'services': list(b.SERVICES),
        'all_label': 'All ten services',
        'paths': [{'key': 'amazon', 'label': 'Amazon'}, {'key': 'direct', 'label': 'Direct'}],
        'columns': [{'key': k, 'label': l, 'unit': u} for k, l, u in COLUMNS],
        'rows': rows,
        'metrics': block['metrics'],
        'reply': block['reply'],
        'questions': block['questions'],
        'aliases': block['aliases'],
        'followups': block['followups'],
    }


PACKS = {'the-read-amazon-vs-direct': amazon_vs_direct}


def render(doc):
    return json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + '\n'


def main(argv=None):
    check = '--check' in (argv if argv is not None else sys.argv[1:])
    stale = 0
    for name, fn in PACKS.items():
        path = os.path.join(OUT_DIR, name + '.json')
        want = render(fn())
        have = open(path, encoding='utf-8').read() if os.path.exists(path) else ''
        if have == want:
            print('current:', os.path.relpath(path))
            continue
        if check:
            print('STALE:', os.path.relpath(path))
            stale += 1
            continue
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(want)
        print('wrote:', os.path.relpath(path))
    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main())
