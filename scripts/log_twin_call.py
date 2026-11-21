#!/usr/bin/env python3
"""Log a call on the Digital Twins IQ track record on purpose, before its
result (Jenna 2026-11-20: "Only calls we deliberately log before the
result"). The same rules as the dashboard's Log a call form (staff).

A finished room question:
    python3 scripts/log_twin_call.py --twin-key digital-twins/... --session-id ab12 --question-id q9 \\
        --due 2026-12-08 --check certified --scored-as winner --what "Who wins the runoff on December 7?" --event election
An open call already on the record (a pilot forecast, a run's final question):
    python3 scripts/log_twin_call.py --ledger-id lg_... --due 2026-12-12 --check published --scored-as number --what "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Log a call on the track record before its result.')
    for a in ('ledger-id', 'twin-key', 'session-id', 'question-id', 'due', 'knowable', 'check', 'scored-as', 'what', 'subject', 'metric', 'unit', 'people',
              'platform', 'title', 'window-start', 'window-end', 'event'):
        ap.add_argument('--' + a, default='')
    ap.add_argument('--by', default='admin')
    args = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3')
    from migration import digital_twin_calls as CALLS
    from migration import digital_twins as DT
    body = {k.replace('-', '_'): v for k, v in vars(args).items() if v}
    body['due_at'] = body.pop('due', '')
    if 'knowable' in body:
        body['knowable_at'] = body.pop('knowable')
    rec, error, code = CALLS.log_on_purpose(s3, body, by=args.by, load_twin=lambda k: DT.load_twin(s3, k))
    if not rec:
        print(json.dumps({'logged': False, 'error': error, 'code': code}))
        return 1
    print(json.dumps({'logged': True, 'id': rec['id'], 'public': bool(rec.get('public')), 'due_on': (rec.get('call') or {}).get('due_at')}))
    return 0


if __name__ == '__main__':
    code = main()
    sys.stdout.flush()
    os._exit(code)
