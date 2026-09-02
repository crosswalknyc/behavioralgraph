"""One-shot lens scorer for the preserve+score split.

Runs the same batch prompt / parse / cutoff pipeline as
`lens_relevance.fetch()`, but with two changes vs the nightly
scraper:

1. `--preserve <lens_id>` KEEPS the current S3 scores for that
   lens verbatim.  Useful when a lens has already been scored and
   we only want to add or refresh the OTHER lenses.
2. `--score <lens_id>` (re)scores that lens from scratch and writes
   the merged result back to S3 with a pre-mutation backup.

Two backends:

- **Anthropic direct** (default).  Uses `ANTHROPIC_API_KEY` from
  the environment.  This is the same path the nightly scraper
  uses; performance and quality are identical.
- **Bedrock** (fallback, `--backend bedrock`).  Sends each batch to
  `us.anthropic.claude-sonnet-4-5-20250929-v1:0` on Bedrock so the
  scorer keeps working when the Anthropic direct API is unavailable
  (rate limit, credit exhaustion).  Bedrock bills against the AWS
  account; grant Bedrock model access + submit the Anthropic use-
  case form for the AWS account first, otherwise the endpoint
  returns `Model use case details have not been submitted`.

Writes the merged result back to
`s3://dashboard-inputs/trends_iq_snapshots/latest/lens_scores.json`
with a pre-mutation backup at
`s3://dashboard-inputs/_backups/lens_scores.json.pre_bedrock_<ts>.json`.
Cutoffs are recomputed across all surviving lenses so the frontend
keeps its 25-45% keep-rate per (lens, kind).

Not wired into the daily scrape.

Usage:

    # Score the four generational lenses via Anthropic direct,
    # preserving the two lenses that were already fresh:
    python3 -m scripts.trends_scrapers._bedrock_scorer \
        --preserve ms_now_reader \
        --preserve unlikely_collaborators_follower \
        --score gen_z --score millennials \
        --score gen_x --score baby_boomers
"""
from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3

# Ensure the trends_scrapers package is importable when run as
# `python3 -m scripts.trends_scrapers._bedrock_scorer` (the module
# path already handles that) OR as a bare script from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

from scripts.trends_scrapers import lens_relevance as lr  # noqa: E402

logger = logging.getLogger('lens_bedrock')


_BEDROCK_MODEL = os.environ.get(
    'LENS_BEDROCK_MODEL',
    'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
)
_BEDROCK_REGION = os.environ.get('LENS_BEDROCK_REGION', 'us-east-1')
_S3_KEY = f'{lr._S3_LATEST}lens_scores.json'


# ---------------------------------------------------------------------------
# Anthropic-shaped client that calls Bedrock InvokeModel underneath.
# `lens_relevance._score_batch` calls
#     client.messages.create(model=..., max_tokens=..., messages=...,
#                             timeout=...)
# and reads `.content[i].text`.  We mimic that surface so the existing
# batch/parse pipeline works unchanged.
# ---------------------------------------------------------------------------
class _TextBlock:
    __slots__ = ('text',)

    def __init__(self, text: str) -> None:
        self.text = text


class _BedrockResponse:
    __slots__ = ('content',)

    def __init__(self, blocks: list[_TextBlock]) -> None:
        self.content = blocks


class _BedrockMessages:
    def __init__(self, client) -> None:
        self._c = client

    def create(self, *, model: str, max_tokens: int,
                messages: list[dict], timeout: int = 120,
                temperature: float = 0.0) -> _BedrockResponse:
        # Coerce Anthropic messages into Bedrock's
        # anthropic_version=bedrock-2023-05-31 shape.  For our
        # use case every message is a plain string user turn.
        body = {
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': int(max_tokens),
            'temperature': float(temperature),
            'messages': [
                {'role': m.get('role', 'user'),
                 'content': m.get('content', '')}
                for m in messages
            ],
        }
        # Bedrock supports contentType, accept.  Read the body.
        # Retry once on transient InternalServerError / throttling.
        last_err = None
        for attempt in range(3):
            try:
                resp = self._c.invoke_model(
                    modelId=_BEDROCK_MODEL,
                    body=json.dumps(body).encode(),
                    contentType='application/json',
                    accept='application/json',
                )
                payload = json.loads(resp['body'].read())
                blocks = payload.get('content') or []
                out: list[_TextBlock] = []
                for b in blocks:
                    if isinstance(b, dict) and 'text' in b:
                        out.append(_TextBlock(str(b.get('text') or '')))
                return _BedrockResponse(out)
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e)
                if 'ThrottlingException' in msg or 'TooManyRequests' in msg or 'ServiceUnavailable' in msg:
                    time.sleep(2 * (attempt + 1))
                    continue
                if 'InternalServerError' in msg or 'timed out' in msg.lower():
                    time.sleep(1 + attempt)
                    continue
                raise
        raise last_err  # type: ignore[misc]


class BedrockAnthropicClient:
    """Presents the anthropic.Anthropic().messages.create() surface
    but calls Bedrock underneath.  Only the fields lens_relevance
    actually reads are populated."""

    def __init__(self) -> None:
        self._c = boto3.client('bedrock-runtime', region_name=_BEDROCK_REGION)
        self.messages = _BedrockMessages(self._c)


# ---------------------------------------------------------------------------
# Scoring entrypoint.  Mirrors lens_relevance.fetch() but with the
# swapped client and the preserve/score split.
# ---------------------------------------------------------------------------
def _load_existing_scores() -> dict[str, Any] | None:
    s3 = boto3.client('s3')
    try:
        obj = s3.get_object(Bucket=lr._S3_BUCKET, Key=_S3_KEY)
        return json.loads(obj['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("existing lens_scores.json not readable: %s", e)
        return None


def _backup_existing(prior_bytes: bytes) -> str:
    s3 = boto3.client('s3')
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    key = f'_backups/lens_scores.json.pre_bedrock_{ts}.json'
    s3.put_object(Bucket=lr._S3_BUCKET, Key=key, Body=prior_bytes,
                   ContentType='application/json')
    return f's3://{lr._S3_BUCKET}/{key}'


def _build_client(backend: str):
    """Return a `client` object with the `.messages.create(...)` shape
    that `lens_relevance._score_batch` expects.  `backend` is
    'anthropic' (default) or 'bedrock'."""
    if backend == 'bedrock':
        return BedrockAnthropicClient()
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set; either export it or pass "
            "--backend bedrock (Bedrock model access + Anthropic "
            "use-case form must be granted for the AWS account).")
    import anthropic  # type: ignore
    return anthropic.Anthropic(api_key=api_key)


def run(preserve: list[str], score: list[str], *,
         dry_run: bool = False, backend: str = 'anthropic') -> dict[str, Any]:
    prior = _load_existing_scores() or {}
    prior_items = prior.get('items') or {}
    prior_lenses = prior.get('lenses') or []
    prior_lens_ids = {l['id'] for l in prior_lenses}

    # 1. Collect fresh item list (drops items that are no longer on
    #    the dashboard and adds any new ones).  Match `_key` shape
    #    so preserved scores map cleanly by key.
    items = lr._collect_all_items()
    logger.info("collected %d items across all snapshots", len(items))

    # 2. Build the merged scoreboard skeleton, seeding from prior
    #    scores for every lens in `preserve`.
    combined: dict[str, dict] = {}
    for it in items:
        row: dict[str, Any] = {
            'kind':   it['kind'],
            'title':  it['title'],
            'scores': {},
            'why':    {},
        }
        if it.get('artist'):
            row['artist'] = it['artist']
        # Seed from prior for preserved lenses.
        prior_row = prior_items.get(it['key']) or {}
        prior_scores = prior_row.get('scores') or {}
        prior_why = prior_row.get('why') or {}
        for pid in preserve:
            if pid in prior_scores:
                row['scores'][pid] = int(prior_scores[pid])
                if pid in prior_why:
                    row['why'][pid] = str(prior_why[pid])
        combined[it['key']] = row

    kept_preserved = sum(1 for r in combined.values()
                          if any(p in r['scores'] for p in preserve))
    logger.info("preserved scores present on %d/%d items for %s",
                 kept_preserved, len(items), preserve)

    if dry_run:
        return {'combined': combined, 'prior_items': len(prior_items),
                 'dry_run': True}

    # 3. Score each requested lens.
    client = _build_client(backend)
    lenses_by_id = {l['id']: l for l in lr._LENSES}
    per_lens_out: dict[str, dict[str, dict]] = {}
    for lens_id in score:
        lens = lenses_by_id.get(lens_id)
        if not lens:
            logger.warning("lens_id %s not registered in _LENSES; skipping",
                            lens_id)
            continue
        logger.info("=== scoring lens: %s ===", lens_id)
        t0 = time.time()
        out = lr._score_lens(client, lens, items)
        elapsed = time.time() - t0
        logger.info("=== %s done: scored %d/%d items in %.1fs ===",
                     lens_id, len(out), len(items), elapsed)
        per_lens_out[lens_id] = out

    # 4. Fold new scores into combined.
    for lens_id, out in per_lens_out.items():
        for it in items:
            hit = out.get(it['key'])
            if not hit:
                continue
            combined[it['key']]['scores'][lens_id] = int(hit['score'])
            if hit.get('why'):
                combined[it['key']]['why'][lens_id] = str(hit['why'])

    # 5. Drop rows with no scores across ANY lens, and drop empty why
    #    sub-dicts.
    final: dict[str, dict] = {}
    for k, row in combined.items():
        if not row.get('why'):
            row.pop('why', None)
        if row.get('scores'):
            final[k] = row

    # 6. Lenses meta = every lens in _LENSES that has at least one
    #    score in the final set.  Order mirrors _LENSES insertion
    #    order (dropdown ordering).
    active_lens_ids: set[str] = set()
    for row in final.values():
        active_lens_ids.update(row.get('scores', {}).keys())
    lens_meta = [
        {'id': l['id'], 'label': l['label'],
         'emoji': l['emoji'], 'description': l['description']}
        for l in lr._LENSES if l['id'] in active_lens_ids
    ]

    # 7. Recompute per-kind cutoffs across ALL surviving lenses.  This
    #    keeps existing preserved lens cutoffs intact (their score
    #    distribution didn't change) AND adds cutoffs for the newly-
    #    scored lenses.
    cutoffs = lr._compute_cutoffs(final, [l['id'] for l in lens_meta])

    result = {
        'source':        'lens_scores',
        'kind':          'meta',
        'fetched_at':    datetime.now(timezone.utc).isoformat(),
        'generated_at':  datetime.now(timezone.utc).isoformat(),
        'items':         final,
        'lenses':        lens_meta,
        'cutoffs':       cutoffs,
        'count':         len(final),
    }
    return result


def _upload(result: dict[str, Any]) -> tuple[str, str]:
    s3 = boto3.client('s3')
    # Backup the current object first.
    try:
        prior = s3.get_object(Bucket=lr._S3_BUCKET, Key=_S3_KEY)
        prior_bytes = prior['Body'].read()
        backup = _backup_existing(prior_bytes)
    except Exception as e:  # noqa: BLE001
        logger.info("no prior lens_scores.json to back up (%s)", e)
        backup = ''
    body = json.dumps(result, ensure_ascii=False).encode('utf-8')
    s3.put_object(Bucket=lr._S3_BUCKET, Key=_S3_KEY, Body=body,
                   ContentType='application/json')
    return f's3://{lr._S3_BUCKET}/{_S3_KEY}', backup


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser()
    ap.add_argument('--preserve', action='append', default=[],
                     help='lens_id to preserve from existing S3 scores '
                          '(repeatable)')
    ap.add_argument('--score', action='append', default=[],
                     help='lens_id to (re)score via Bedrock (repeatable)')
    ap.add_argument('--dry-run', action='store_true',
                     help='collect + seed but skip Bedrock calls; do not upload')
    ap.add_argument('--no-upload', action='store_true',
                     help='score but write to /tmp/lens_scores.new.json only')
    ap.add_argument('--backend', choices=['anthropic', 'bedrock'],
                     default='anthropic',
                     help='which model backend to use (default: anthropic)')
    args = ap.parse_args()

    preserve = list(args.preserve or [])
    score = list(args.score or [])
    logger.info("preserve=%s score=%s dry_run=%s backend=%s",
                 preserve, score, args.dry_run, args.backend)

    result = run(preserve, score, dry_run=args.dry_run, backend=args.backend)
    if args.dry_run:
        combined = result.get('combined', {})
        print(f"[dry-run] would score {len(combined)} items", file=sys.stderr)
        return

    if args.no_upload:
        out = '/tmp/lens_scores.new.json'
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[no-upload] wrote {out} count={result['count']}", file=sys.stderr)
        return

    key, backup = _upload(result)
    print(f"UPLOADED: {key}", file=sys.stderr)
    if backup:
        print(f"BACKUP:   {backup}", file=sys.stderr)
    print(f"count={result['count']} lenses={[l['id'] for l in result['lenses']]}",
           file=sys.stderr)


if __name__ == '__main__':
    main()
