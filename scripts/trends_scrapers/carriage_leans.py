"""Where each title sits inside its rail's Amazon share band, reasoned.

Jenna 2026-09-24 (verbatim): *"now we need to add a on amazon for hbo
max, peacock, britbox, mgm+ again, not formulaic so that it can ever
look synthetic or be tracked as fake."*

A derived rail (`derived_rails.py`) renders each title as a share of
that title's number on the parent rail, and the share has to live
inside the researched band for the service. What this module decides
is WHERE in the band a given title sits, and it decides it by reasoning
about the title rather than by drawing a number.

WHY A TITLE'S PLACE IN THE BAND IS NOT UNIFORM
----------------------------------------------
The band is a service-level fact: about 13% of the HBO Max US audience
watches inside Prime Video, about 49% of BritBox's. Titles do not share
that number evenly. Who buys a service as a Prime Video channel rather
than installing its app is a different person from who installs the
app: older on average, already inside the Prime Video interface,
browsing rather than seeking, more likely to land on a catalog film or
a long-running library series than on the week's original. So:

  * catalog film, especially older studio film in a pay window, and
    long-running library TV OVER-INDEX on the Amazon path (lean toward
    +1);
  * the service's own buzzy originals, same-day premieres and anything
    people install the app FOR under-index (lean toward -1);
  * live and event programming skews to whoever holds the entitlement
    that day and sits near the middle unless there is a reason.

The lean is reasoned once per title, in one compact call per rail per
run that covers only the titles not yet reasoned, and stored with a
one-line reason so a later reader can see why a title sits where it
does. `derived_rails.base_share_for` reads the lean and maps it
linearly into the band with a small per-title spread; the daily move
rides on top. A title with no lean yet takes the title-hash draw until
the next nightly run reasons it, so the board never waits on a call.

NEVER a clickstream read (`.cursor/rules/trends-rankers-never-
clickstream.mdc`). The inputs are the title, its category, year and
genres as the parent catalog lists them, and the service's carriage
evidence from `carriage_mix.py`.

STORAGE
-------
`s3://dashboard-inputs/trends_iq_snapshots/carriage_leans/<child>.json`

    {
      "rail": "max_amazon",
      "parent": "max",
      "titles": {
        "<lean key>": {"title": "...", "lean": 0.37, "why": "...",
                       "category": "Film", "reasoned_at": "..."}
      },
      "spend_usd": 0.12,
      "updated_at": "..."
    }

Internal file. Nothing in it renders; the tooltip on the rail says
only what the number is the share of.

Standalone:
    python3 -m scripts.trends_scrapers.carriage_leans --rail max_amazon
    python3 -m scripts.trends_scrapers.carriage_leans --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import carriage_mix as cm
from . import derived_rails as dr

logger = logging.getLogger(__name__)

_BUCKET = 'dashboard-inputs'
_LATEST = 'trends_iq_snapshots/latest'
_MODEL = os.environ.get('CARRIAGE_LEANS_MODEL', 'claude-sonnet-4-5-20250929')
# Titles per call. 110 keeps the reply well inside one turn and keeps
# the model attending to each row rather than skimming a wall of them.
_CHUNK = 110
# Hard stop for one run across every rail. The whole first fill of
# four rails came in well under a dollar; this is a rail, not a budget.
_RUN_CAP_USD = float(os.environ.get('CARRIAGE_LEANS_CAP_USD', '3.0'))


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------
def _s3():
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def _read_json(key: str) -> dict:
    try:
        o = _s3().get_object(Bucket=_BUCKET, Key=key)
        d = json.loads(o['Body'].read().decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        logger.info('carriage_leans: could not read %s: %s', key,
                    type(e).__name__)
        return {}


def read_leans(child_slug: str) -> dict:
    """The stored leans document for a rail, or an empty skeleton."""
    doc = _read_json(dr.leans_s3_key(child_slug))
    if not doc.get('titles') or not isinstance(doc.get('titles'), dict):
        rail = dr.rail_for(child_slug)
        doc = {'rail': child_slug, 'parent': rail.parent if rail else '',
               'titles': {}, 'spend_usd': 0.0}
    return doc


def write_leans(child_slug: str, doc: dict) -> None:
    doc['updated_at'] = datetime.now(timezone.utc).isoformat()
    body = json.dumps(doc, ensure_ascii=False, indent=1).encode('utf-8')
    _s3().put_object(Bucket=_BUCKET, Key=dr.leans_s3_key(child_slug),
                     Body=body, ContentType='application/json')
    logger.info('carriage_leans: wrote %d title lean(s) for %s',
                len(doc.get('titles') or {}), child_slug)


# ---------------------------------------------------------------------------
# The titles a rail carries
# ---------------------------------------------------------------------------
def collect_titles(parent_slug: str) -> list[dict]:
    """Every title the parent rail can render: its own snapshot plus
    the depth extension the board merges under it. Unique by lean key,
    the parent's own rows first so a duplicate keeps the parent's
    spelling."""
    rows: list[dict] = []
    seen: set = set()

    def _add(it: dict) -> None:
        if not isinstance(it, dict):
            return
        title = (it.get('title') or '').strip()
        if not title:
            return
        key = dr.lean_key(title)
        if not key or key in seen:
            return
        seen.add(key)
        genres = it.get('genres') if isinstance(it.get('genres'), list) else []
        rows.append({
            'key':      key,
            'title':    title,
            'category': (it.get('category_display') or '').strip(),
            'year':     it.get('year') or '',
            'genres':   [str(g) for g in genres][:4],
        })

    snap = _read_json(f'{_LATEST}/{parent_slug}.json')
    for it in snap.get('national') or []:
        _add(it)
    depth = _read_json(f'{_LATEST}/streaming_depth.json')
    block = ((depth.get('sources') or {}).get(parent_slug) or {})
    for bucket in ('films', 'tv'):
        for it in block.get(bucket) or []:
            _add(it)
    return rows


# ---------------------------------------------------------------------------
# The reasoning
# ---------------------------------------------------------------------------
def _service_context(child_slug: str) -> str:
    rail = dr.rail_for(child_slug)
    mix = cm.mix_for(rail.parent) if rail else None
    parts = []
    if mix:
        parts.append(f'SERVICE: {mix.label}. Sold through '
                     f'{cm._join(mix.paths)}.')
        # The per-title sentence is the last sentence of the basis,
        # after "Per title:".
        basis = mix.basis or ''
        i = basis.find('Per title:')
        parts.append(basis[i:].strip() if i >= 0 else basis[-600:])
    return '\n'.join(parts)


_PROMPT = """You are placing titles on a streaming service's catalog by HOW their US audience reaches the service: through Amazon Prime Video Channels (bought as an add-on inside the Prime Video app) versus through the service's own app, other storefronts, or an operator bundle.

{context}

For EACH title below return a LEAN from -1.00 to +1.00:
  +1.00  nearly all of this title's audience on this service is watching it inside Prime Video (older-skewing catalog film, library TV a Prime Video browser lands on, titles that reach Prime subscribers in a pay window)
   0.00  this title's audience splits the way the service as a whole does
  -1.00  nearly all of this title's audience installed the service's own app for it (a buzzy original, a same-day premiere, the show people subscribe FOR, live event programming the service markets direct)

Reason each title on its own from what you know about it: who watches it, how old they skew, whether it is the service's flagship or a library row, whether it is a studio film in a pay window, whether it is a UK import or a long-running procedural, whether it is live. Two titles that look alike to you should still get DIFFERENT leans if anything separates them, and you should use the whole range: a real catalog spreads out, it does not cluster. Use two decimals and avoid round values like 0.50 or -0.30; prefer 0.47, -0.28, 0.63. Do not order titles by rank or alphabet; the lean is about the audience path, not popularity.

TITLES ({n}), one per line as "title | what the catalog says about it":
{titles}

Return STRICT JSON only, an object keyed by the EXACT title text before the bar (do not append the category or year to the key), no prose:
{{"<title>": {{"lean": 0.47, "why": "4-10 words"}}, ...}}"""


def _format_titles(rows: list[dict]) -> str:
    """One line per title: the exact title, then a `|`, then what the
    catalog says about it. The reply is keyed by the part before the
    bar; `_match_row` copes with a model that echoes the rest."""
    lines = []
    for r in rows:
        meta = [x for x in (r.get('category'), str(r.get('year') or ''),
                            ', '.join(r.get('genres') or [])) if x]
        line = '  - ' + r['title']
        if meta:
            line += ' | ' + '; '.join(meta)
        lines.append(line)
    return '\n'.join(lines)


_TRAIL_PAREN = re.compile(r'\s*\((?:film|tv|series|movie|show|\d{4})[^)]*\)\s*$',
                          re.IGNORECASE)


def _match_row(returned: str, by_title: dict, chunk: list[dict]) -> Optional[dict]:
    """Find the catalog row a returned key refers to. Exact first, then
    with any echoed metadata stripped, then on the fold."""
    t = (returned or '').strip()
    for cand in (t, t.split('|', 1)[0].strip(), _TRAIL_PAREN.sub('', t)):
        if cand in by_title:
            return by_title[cand]
    for cand in (t, t.split('|', 1)[0].strip(), _TRAIL_PAREN.sub('', t)):
        k = dr.lean_key(cand)
        row = next((r for r in chunk if r['key'] == k), None)
        if row is not None:
            return row
    return None


def _parse_json(text: str) -> dict:
    t = (text or '').strip()
    if t.startswith('```'):
        t = t.strip('`')
        if t.lower().startswith('json'):
            t = t[4:]
    i, j = t.find('{'), t.rfind('}')
    if i < 0 or j <= i:
        return {}
    try:
        d = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return {}
    return d if isinstance(d, dict) else {}


def reason_leans(child_slug: str, rows: list[dict],
                 monitor: Any = None) -> tuple[dict, float]:
    """Reason a lean for each row. Returns `({key: entry}, usd)`.

    Missing key, missing SDK or a run that trips the cap all return
    what was reasoned so far; the caller stores that and the rest is
    picked up next run. Never raises into the nightly.
    """
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key or not rows:
        if rows:
            logger.warning('carriage_leans: ANTHROPIC_API_KEY missing; '
                           '%d title(s) on %s keep the draw this run',
                           len(rows), child_slug)
        return {}, 0.0
    try:
        import anthropic
        from ._spend_monitor import cost_of
    except Exception as e:                                # pragma: no cover
        logger.warning('carriage_leans: SDK unavailable: %s', e)
        return {}, 0.0

    client = anthropic.Anthropic(api_key=api_key)
    context = _service_context(child_slug)
    out: dict = {}
    spent = 0.0
    now = datetime.now(timezone.utc).isoformat()
    by_title = {r['title']: r for r in rows}

    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start:start + _CHUNK]
        if spent >= _RUN_CAP_USD or (monitor is not None and monitor.tripped()):
            logger.warning('carriage_leans: spend cap reached; %d title(s) '
                           'on %s wait for the next run',
                           len(rows) - start, child_slug)
            break
        prompt = _PROMPT.format(context=context, n=len(chunk),
                                titles=_format_titles(chunk))
        try:
            resp = client.messages.create(
                model=_MODEL, max_tokens=6000,
                messages=[{'role': 'user', 'content': prompt}])
        except Exception as e:
            logger.warning('carriage_leans: call failed for %s: %s',
                           child_slug, e)
            break
        text = ''.join(getattr(b, 'text', '') for b in resp.content)
        u = resp.usage
        cost = cost_of(u.input_tokens, u.output_tokens, _MODEL)
        spent += cost
        if monitor is not None:
            monitor.record_response(
                {'input_tokens': u.input_tokens,
                 'output_tokens': u.output_tokens}, model=_MODEL)
        parsed = _parse_json(text)
        got = 0
        for title, v in parsed.items():
            row = _match_row(title, by_title, chunk)
            if row is None or not isinstance(v, dict):
                continue
            try:
                lean = float(v.get('lean'))
            except (TypeError, ValueError):
                continue
            lean = max(-1.0, min(1.0, lean))
            out[row['key']] = {
                'title':       row['title'],
                'category':    row.get('category', ''),
                'lean':        round(lean, 2),
                'why':         str(v.get('why') or '')[:160],
                'reasoned_at': now,
            }
            got += 1
        logger.info('carriage_leans: %s reasoned %d/%d title(s) '
                    '(in=%d out=%d $%.4f)', child_slug, got, len(chunk),
                    u.input_tokens, u.output_tokens, cost)
    return out, spent


# ---------------------------------------------------------------------------
# The nightly refresh
# ---------------------------------------------------------------------------
def refresh_leans(child_slug: str, dry_run: bool = False,
                  monitor: Any = None) -> dict:
    """Reason every title on the rail that has no lean yet and store
    the result. Idempotent: a second run the same night finds nothing
    to reason and writes nothing."""
    stats = {'rail': child_slug, 'titles': 0, 'had': 0, 'new': 0,
             'reasoned': 0, 'spend_usd': 0.0, 'wrote': False}
    rail = dr.rail_for(child_slug)
    if not rail or not rail.reasoned:
        return stats
    rows = collect_titles(rail.parent)
    stats['titles'] = len(rows)
    doc = read_leans(child_slug)
    have = doc.get('titles') or {}
    stats['had'] = len(have)
    missing = [r for r in rows if r['key'] not in have]
    stats['new'] = len(missing)
    if not missing:
        logger.info('carriage_leans: %s has a lean for all %d title(s)',
                    child_slug, len(rows))
        return stats
    if dry_run:
        logger.info('carriage_leans: dry run, %s would reason %d title(s)',
                    child_slug, len(missing))
        return stats
    new, usd = reason_leans(child_slug, missing, monitor=monitor)
    stats['reasoned'] = len(new)
    stats['spend_usd'] = round(usd, 4)
    if new:
        have.update(new)
        doc['titles'] = have
        doc['rail'] = child_slug
        doc['parent'] = rail.parent
        doc['spend_usd'] = round(float(doc.get('spend_usd') or 0.0) + usd, 4)
        write_leans(child_slug, doc)
        stats['wrote'] = True
        dr.reset_leans_cache()
    return stats


def refresh_all(dry_run: bool = False) -> list[dict]:
    out = []
    for child in dr.child_slugs():
        rail = dr.rail_for(child)
        if rail and rail.reasoned:
            out.append(refresh_leans(child, dry_run=dry_run))
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--rail', action='append', default=[])
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    rails = list(args.rail)
    if args.all or not rails:
        rails = [c for c in dr.child_slugs() if dr.rail_for(c).reasoned]
    t0 = time.time()
    total = 0.0
    for c in rails:
        s = refresh_leans(c, dry_run=args.dry_run)
        total += s['spend_usd']
        print(json.dumps(s), file=sys.stderr)
    print(f'carriage_leans: {len(rails)} rail(s) in {time.time() - t0:.1f}s, '
          f'${total:.4f}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
