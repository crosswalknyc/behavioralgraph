#!/usr/bin/env python3
"""Pathways, One viewer and Clickstream on a stored Digital Journey, in place.

Jenna, 2026-11-03: "on the ghost energy and online soda shoppers journey
add in pathways, the one viewer and clickstream like we have in music to
long form. and in general make these standard for all digital journeys
going forward."

New builds carry the three sections from `journey_synthesis.synthesize`.
This script brings a journey that is already on the shelf up to the same
standard: the clickstream is researched when the file has none (or
--force), then the pathways and the one-person walk are reasoned from
the finished file, every count and date is forced into shape, dead
links are dropped, and the payload is written back under the same key
with a copy in journey-iq/_backups/ first. The dashboard reads the key
on the next load; nothing re-registers.

    python3 scripts/journey_add_paths.py --match "Ghost Energy" --match "Online Soda" --write
    python3 scripts/journey_add_paths.py --all-missing --write      # every journey without the blocks
    python3 scripts/journey_add_paths.py --key journey-iq/admin/<file>.json.gz   # dry run, prints what would land

Needs the bg-webapp .env (ANTHROPIC_API_KEY, AWS). Without a model the
paths derive from the spine (the same fallback a build uses).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

BUCKET = 'dashboard-inputs'
INDEX_KEY = 'journey-iq/_index.json'
MODELS = ['claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6']


def _extract_json(text: str):
    text = (text or '').strip()
    if not text:
        return None
    if '```json' in text:
        text = text.split('```json', 1)[1].split('```', 1)[0].strip()
    elif '```' in text:
        text = text.split('```', 1)[1].split('```', 1)[0].strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    a, b = text.find('{'), text.rfind('}')
    if a >= 0 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except Exception:
            return None
    return None


# the same two web_search tool shapes prometheus_analysis carries, written
# out here so the script does not import the Flask app to read them
WEB_SEARCH_TOOL = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 6}
WEB_SEARCH_TOOL_LEGACY = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 6}


def make_claude_json(verbose: bool = True):
    """claude_json(system, user, max_tokens=, temperature=, surface=, tools=) -> dict."""
    from claude_client import claude_reason_json
    plans = [[WEB_SEARCH_TOOL], [WEB_SEARCH_TOOL_LEGACY], None]
    resolved = {'model': None}

    def claude_json(system, user, max_tokens=6000, temperature=0.5, surface='journey', tools=None, **_kw):
        tool_plans = plans if tools else [None]
        models = [resolved['model']] + [m for m in MODELS if m != resolved['model']] if resolved['model'] else MODELS
        for tp in tool_plans:
            for m in models:
                t0 = time.time()
                try:
                    raw = claude_reason_json(system=system, user=user, model=m, max_tokens=max_tokens, temperature=temperature,
                                             tools=tp, raise_on_error=True, usage_tag=(surface, 'journey_add_paths'))
                except Exception as exc:
                    if verbose:
                        print(f'    [{surface}] {m} {"with search" if tp else "no search"}: {str(exc)[:120]}')
                    continue
                data = _extract_json(raw)
                if isinstance(data, dict) and data:
                    resolved['model'] = m
                    if verbose:
                        print(f'    [{surface}] {m} {"with search" if tp else "no search"} answered in {time.time() - t0:.0f}s')
                    return data
                if verbose:
                    print(f'    [{surface}] {m}: no JSON object in the answer')
        return {}
    return claude_json


def _s3():
    import boto3
    return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def load_index(s3) -> list[dict]:
    idx = json.loads(s3.get_object(Bucket=BUCKET, Key=INDEX_KEY)['Body'].read())
    runs = idx.get('runs') if isinstance(idx, dict) else idx
    return [r for r in (runs or []) if isinstance(r, dict) and r.get('key')]


def load_payload(s3, key: str) -> dict:
    raw = s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
    try:
        raw = gzip.decompress(raw)
    except Exception:
        pass
    return json.loads(raw)


def write_payload(s3, key: str, payload: dict, tag: str) -> str:
    ts = _dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    name = key.rsplit('/', 1)[-1]
    bk = f'journey-iq/_backups/{name}.pre_{tag}_{ts}'
    s3.copy_object(Bucket=BUCKET, CopySource={'Bucket': BUCKET, 'Key': key}, Key=bk)
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.getvalue(), ContentType='application/json', ContentEncoding='gzip')
    return bk


def inputs_from(payload: dict) -> dict:
    from migration import journey_paths as JP
    meta = payload.get('meta') or {}
    subject = JP._subject(payload)
    platform = str(meta.get('platform_label') or '')
    if not platform:
        disp = str(meta.get('target_display') or '')
        if ' - ' in disp and disp.endswith('journey'):
            platform = disp.split(' - ', 1)[1][:-len('journey')].strip()
    return {'subject': subject, 'platform': platform, 'journey_kind': str(meta.get('journey_kind') or 'purchase'),
            'start_date': meta.get('start_date'), 'end_date': meta.get('end_date'), 'product': meta.get('product')}


def upgrade(payload: dict, claude_json, tools=None, force: bool = False, check_urls: bool = True) -> dict:
    from migration import journey_clickstream as JC, journey_paths as JP, journey_synthesis as JS
    inputs = inputs_from(payload)
    blob = payload.get('fragrance_shop_journey') or {}
    had_cs = bool(((blob.get('clickstream') or payload.get('clickstream') or {}).get('steps')))
    report = {'subject': inputs['subject'], 'platform': inputs['platform'], 'clickstream': 'kept', 'paths': 'kept', 'person': 'kept'}
    if force or not had_cs:
        raw = JP.reason_clickstream(payload, claude_json, inputs, tools) if claude_json else {}
        prim = {'clickstream': raw.get('clickstream')} if isinstance(raw, dict) and raw.get('clickstream') else {}
        for k in ('clickstream',):
            payload.pop(k, None)
            blob.pop(k, None)
        JC.attach_clickstream(payload, prim, inputs, check_urls=check_urls)
        report['clickstream'] = ('researched' if prim else 'derived from the spine') + f" ({len((payload.get('clickstream') or {}).get('steps') or [])} steps)"
    had_paths = JP.has_paths(payload)
    if force or not had_paths:
        JP.attach_paths(payload, claude_json, inputs, tools=tools, force=True)
        pw = payload.get('pathways') or {}
        ij = payload.get('individual_journey') or {}
        report['paths'] = f"{len(pw.get('sequences') or [])} paths covering {pw.get('covered_pct')}% of {pw.get('base_label', '').lower()}"
        report['person'] = f"{ij.get('label')}: {len(ij.get('steps') or [])} steps, key step {ij.get('key_step')}"
    JS.reconcile_payload(payload)
    JS.scrub_payload_text(payload)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--key', action='append', default=[], help='full S3 key under journey-iq/ (repeatable)')
    ap.add_argument('--match', action='append', default=[], help='substring of the project name in the index (repeatable)')
    ap.add_argument('--all-missing', action='store_true', help='every indexed journey without the three blocks')
    ap.add_argument('--force', action='store_true', help='rebuild the blocks even when present')
    ap.add_argument('--write', action='store_true', help='write back to S3 (default: dry run)')
    ap.add_argument('--no-model', action='store_true', help='derive from the spine only; no model calls')
    ap.add_argument('--no-url-check', action='store_true')
    args = ap.parse_args()
    s3 = _s3()
    runs = load_index(s3)
    keys = list(args.key)
    for m in args.match:
        for r in runs:
            name = f"{r.get('project_name') or ''} {r.get('target') or ''} {r.get('key')}"
            if m.lower() in name.lower() and r['key'] not in keys:
                keys.append(r['key'])
    if args.all_missing:
        from migration import journey_paths as JP
        for r in runs:
            try:
                p = load_payload(s3, r['key'])
            except Exception:
                continue
            if (p.get('meta') or {}).get('story_mode') != 'fragrance_shop_journey':
                continue
            if not JP.has_paths(p) and r['key'] not in keys:
                keys.append(r['key'])
    if not keys:
        print('nothing matched')
        return 1
    claude_json = None if args.no_model else make_claude_json()
    tools = [WEB_SEARCH_TOOL] if claude_json else None
    for key in keys:
        print(f'== {key}')
        payload = load_payload(s3, key)
        if (payload.get('meta') or {}).get('story_mode') != 'fragrance_shop_journey':
            print('   skipped: not a caterpillar journey')
            continue
        rep = upgrade(payload, claude_json, tools=tools, force=args.force, check_urls=not args.no_url_check)
        for k, v in rep.items():
            print(f'   {k}: {v}')
        cs = payload.get('clickstream') or {}
        for s in (cs.get('steps') or [])[:13]:
            print(f"     cs {s['step']:>2} {s['date']} {s['surface'][:18]:<18} {s['action'][:52]:<52} {s['people']:>12,}  {(s['urls'] or [{}])[0].get('url', '')[:60]}")
        for q in ((payload.get('pathways') or {}).get('sequences') or []):
            print(f"     path {q['rank']} {q['viewers']:>10,} {q['share_of_base_pct']:>6.1f}%  {q['depth_pct']:>5.1f}% depth  {q['avg_days']:>5.1f}d  " + ' > '.join(q['steps'])[:120])
        for st in ((payload.get('individual_journey') or {}).get('steps') or []):
            print(f"     walk {st['n']:>2} {st['date']:<13} {st['surface'][:14]:<14} {st['kind']:<5} {st['action'][:60]:<60} {(st['clips'] or [{}])[0].get('url', '')[:50]}")
        if args.write:
            bk = write_payload(s3, key, payload, 'paths')
            print(f'   written; backup {bk}')
        else:
            print('   dry run (pass --write to store)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
