"""Journey IQ — Research-Anchored Mode.

Bypasses the ClickHouse Phase-1 keyword scan (which times out on broad
targets like "The Goat"). Instead we:

  1. Run ``research_marketing_footprint()`` — Claude + web_search
     discovers the actual marketing surfaces (top trailers, news,
     forum threads, social posts, ticketing sites) live for the target
     inside the analysis window.
  2. Use the research's ``implied_audience`` (or compute it from
     box-office) as the cohort size for the modeled dashboard view.
  3. Feed the discovered surfaces into ``synthesize_journey()`` as
     ``extra_touchpoint_keywords`` so the synth's path/touchpoints are
     grounded in the real campaign activity, not generic patterns.
  4. Compose a standard Journey IQ ``summary`` envelope (empty raw
     panel + populated ``modeled_view`` containing kpis, touchpoints,
     path_to_purchase, marketing_footprint, touchpoint_bubbles,
     touchpoint_spider) and persist to S3 + index so the existing
     dashboard picks it up.

Run it from anywhere with AWS + ANTHROPIC creds in env:

    cd bg-webapp
    python -m migration.journey_iq_research_anchored \\
        --target "The Goat" --type movie \\
        --start 2026-01-15 --end 2026-05-19 \\
        --box-office 65 \\
        --project "The_Goat_research_anchored_v1"

Total runtime: ~60-90 seconds (one Claude web_search call). No
ClickHouse, no hangs.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────
# .env loading — must run before any module that reads os.environ
# ─────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    for candidate in (
        os.path.join(repo, '.env'),
        os.path.join(os.path.dirname(repo), '.env'),
    ):
        if os.path.exists(candidate):
            load_dotenv(candidate, override=False)
except Exception:
    pass


# ─────────────────────────────────────────────────────────────────────
# Now safe to import the Journey IQ modules
# ─────────────────────────────────────────────────────────────────────
from migration.journey_iq import (  # noqa: E402
    CONVERSION_PATTERNS,
    CUT_DISPLAY_ORDER,
    DEFAULT_FORWARD_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    S3_BUCKET,
    S3_PREFIX,
    _persist,
    _target_variants,
)
from migration.journey_iq_synthesize import (  # noqa: E402
    compute_implied_audience_for_type,
    footprint_to_bubbles,
    footprint_to_spider,
    research_marketing_footprint,
    research_site_funnel,
    synth_to_dashboard_payload,
    synthesize_journey,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _surfaces_from_footprint(fp: dict) -> dict:
    """Turn the research footprint into the ``extra_touchpoint_keywords``
    shape that ``synthesize_journey`` already understands.

    Returns a dict ``{channel: [labels...]}`` where each label is a
    human-readable surface (creator name, publication, etc.) the synth
    should treat as a real touchpoint in the journey.
    """
    out: dict[str, list[str]] = {}
    mf = (fp or {}).get('marketing_footprint') or {}
    for channel, block in mf.items():
        events = (block or {}).get('events') or []
        labels: list[str] = []
        for e in events[:10]:
            label = (
                e.get('actor') or e.get('publication') or e.get('creator') or
                e.get('talent') or e.get('partner') or e.get('site') or
                e.get('platform') or e.get('forum') or e.get('engine') or
                e.get('track') or e.get('event_type')
            )
            if label:
                labels.append(str(label))
        if labels:
            out[channel] = labels
    # Endpoint breakdown for movies → flatten into a 'ticketing' bucket
    eb = (fp or {}).get('endpoint_breakdown') or []
    if eb:
        out.setdefault('ticketing_sites', [])
        for row in eb:
            ep = row.get('endpoint') or row.get('url_pattern')
            if ep and ep not in out['ticketing_sites']:
                out['ticketing_sites'].append(str(ep))
    return out


def _facts_from_footprint(fp: dict, target: str) -> list[dict]:
    """Build the "cool interesting facts" strip from the research data."""
    facts: list[dict] = []
    mf = (fp or {}).get('marketing_footprint') or {}
    # Top channel by reach
    ranked = sorted(
        [(c, float((b or {}).get('reach_pct_of_genpop') or 0.0))
         for c, b in mf.items()],
        key=lambda kv: -kv[1],
    )
    if ranked and ranked[0][1] > 0:
        c, pct = ranked[0]
        facts.append({
            'label': 'Top channel',
            'value': f"{c.replace('_', ' ').title()} reached {pct:.1f}% of US adults",
        })
    # Biggest single event across all channels
    big_event: Optional[tuple[float, str, str]] = None
    for c, block in mf.items():
        for e in (block or {}).get('events') or []:
            r = float(e.get('estimated_reach_us') or 0.0)
            who = (
                e.get('actor') or e.get('creator') or e.get('talent') or
                e.get('publication') or e.get('partner') or e.get('site') or '?'
            )
            if big_event is None or r > big_event[0]:
                big_event = (r, str(who), c)
    if big_event and big_event[0] > 0:
        r, who, c = big_event
        facts.append({
            'label': 'Single biggest touchpoint',
            'value': f"{who} ({c.replace('_', ' ')}) — est. {int(r):,} US reach",
        })
    # Endpoint share for movies
    eb = (fp or {}).get('endpoint_breakdown') or []
    if eb:
        top_ep = sorted(eb, key=lambda r: -float(r.get('share_pct') or 0.0))[0]
        facts.append({
            'label': 'Top ticketing surface',
            'value': f"{top_ep.get('endpoint')} — {float(top_ep.get('share_pct') or 0):.0f}% of ticket buys",
        })
    notes = (fp or {}).get('notes')
    if notes:
        facts.append({'label': 'Research note', 'value': notes})
    if not facts:
        facts.append({'label': 'Target', 'value': f"Research-anchored analysis for {target}"})
    return facts


# ─────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────

def run_research_anchored_job(
    *,
    target: str,
    target_type: str = 'movie',
    start_date: str,
    end_date: str,
    project_name: str = 'Journey IQ (research-anchored)',
    username: str = 'admin',
    # Movie params
    box_office_millions: float = 0.0,
    avg_ticket_price: float = 15.0,
    # Website params
    monthly_visitors_millions: float = 0.0,
    # TV show params
    us_viewers_millions: float = 0.0,
    # General
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    forward_days: int = DEFAULT_FORWARD_DAYS,
    steps: int = 10,
    s3_client: Any = None,
    job_id: Optional[str] = None,
    # Phase toggles — let the caller opt out of the heavy footprint
    # research (which can hang on broad targets) when only the
    # site-funnel block is needed.
    skip_footprint: bool = False,
    skip_synth:     bool = False,
) -> dict:
    """Run a research-anchored Journey IQ job.

    Returns ``{'status', 's3_key', 'summary', 'duration_sec'}``.
    """
    started = time.time()
    job_id = (job_id or uuid.uuid4().hex[:8])

    if not target:
        return {'status': 'failed', 'error': 'target is required'}
    if target_type not in ('movie', 'website', 'tv_show', 'brand'):
        return {'status': 'failed',
                'error': f'unsupported target_type: {target_type!r}'}

    # ── S3 client ────────────────────────────────────────────────────
    if s3_client is None:
        try:
            import boto3
            s3_client = boto3.client('s3')
        except Exception as e:
            return {'status': 'failed', 's3_error': str(e),
                    'error': f'no boto3 / AWS creds: {e}'}

    # ── Phase R: research the marketing footprint (the slow step) ────
    fp: dict = {}
    research_sec = 0.0
    if skip_footprint:
        print(f"[research-anchored] skip_footprint=True — bypassing Phase R "
              f"(marketing-footprint research)")
    else:
        print(f"[research-anchored] researching marketing footprint for "
              f"{target!r} ({target_type}) {start_date} → {end_date}...")
        t0 = time.time()
        fp = research_marketing_footprint(
            target_type=target_type, target=target,
            start_date=start_date, end_date=end_date,
        ) or {}
        research_sec = round(time.time() - t0, 1)
        if fp.get('_error'):
            print(f"[research-anchored] research returned error: {fp['_error']} "
                  f"(after {research_sec}s) — continuing with empty footprint")
            fp = {}
        else:
            n_channels = len((fp.get('marketing_footprint') or {}))
            n_events = sum(len((b or {}).get('events') or [])
                           for b in (fp.get('marketing_footprint') or {}).values())
            n_endpoints = len(fp.get('endpoint_breakdown') or [])
            print(f"[research-anchored] research OK in {research_sec}s — "
                  f"{n_channels} channels, {n_events} events, {n_endpoints} endpoints")

    # ── Phase R.2: site-funnel research (website target_type only) ────
    # Models what happens to visitors who LAND on the target site:
    # converted-on-site / switched-to-competitor / never-transacted +
    # inception referrers + companion behaviors (dinner / parking /
    # etc.). Powers the new "Visitor Funnel" dashboard cards.
    site_funnel: dict = {}
    if target_type == 'website':
        print(f"[research-anchored] researching site funnel for "
              f"{target!r} (visitor outcomes + switchers + inception "
              f"+ companion behaviors)...")
        t1 = time.time()
        site_funnel = research_site_funnel(
            target=target,
            url_pattern=_root_domain(target),
            vertical_hint='movie ticketing' if 'fandango' in target.lower() or 'atom' in target.lower() else '',
            start_date=start_date, end_date=end_date,
        ) or {}
        funnel_sec = round(time.time() - t1, 1)
        if site_funnel.get('_error'):
            print(f"[research-anchored] site_funnel error: "
                  f"{site_funnel['_error']} (after {funnel_sec}s) — "
                  f"continuing without funnel block")
            site_funnel = {}
        else:
            n_dest = len(site_funnel.get('switched_destinations') or [])
            n_ref  = len(site_funnel.get('inception_referrers') or [])
            n_comp = len(site_funnel.get('companion_behaviors') or [])
            print(f"[research-anchored] site_funnel OK in {funnel_sec}s — "
                  f"{n_dest} switched destinations, {n_ref} inception "
                  f"referrers, {n_comp} companion verticals")

    # ── Phase A: implied audience ────────────────────────────────────
    # Prefer the research-reported number (Claude can apply domain
    # nuance like opening-weekend rollups); fall back to formula.
    implied_audience = int(fp.get('implied_audience') or 0)
    if implied_audience <= 0:
        implied_audience = compute_implied_audience_for_type(
            target_type=target_type,
            box_office_millions=box_office_millions,
            ticket_price=avg_ticket_price,
            monthly_visitors_millions=monthly_visitors_millions,
            us_viewers_millions=us_viewers_millions,
            date_range_days=_date_range_days(start_date, end_date),
        )
    print(f"[research-anchored] implied_audience = {implied_audience:,}")

    # ── Phase B: synthesize the journey grounded in the research ─────
    surfaces = _surfaces_from_footprint(fp)
    synth: dict = {}
    synth_sec = 0.0
    if skip_synth:
        print(f"[research-anchored] skip_synth=True — bypassing Phase B "
              f"(journey synthesis)")
        modeled_block = {
            'kpis': {}, 'touchpoints': {'rows': []}, 'path_to_purchase': {},
            'source': 'skipped', 'notes': 'synth skipped by caller',
            'target_type': target_type,
        }
    else:
        print(f"[research-anchored] feeding {sum(len(v) for v in surfaces.values())} "
              f"discovered surfaces across {len(surfaces)} channels into synth...")
        t0 = time.time()
        synth = synthesize_journey(
            target_type=target_type,
            target=target,
            project_name=project_name,
            start_date=start_date,
            end_date=end_date,
            extra_touchpoint_keywords=surfaces,
            panel_converters=0,
            panel_observed_touchpoints=None,
            panel_top_paths=None,
            steps=steps,
            box_office_millions=box_office_millions,
            ticket_price=avg_ticket_price,
            monthly_visitors_millions=monthly_visitors_millions,
            us_viewers_millions=us_viewers_millions,
            date_range_days=_date_range_days(start_date, end_date),
        ) or {}
        synth_sec = round(time.time() - t0, 1)
        print(f"[research-anchored] synth source={synth.get('source', 'fallback')} "
              f"in {synth_sec}s")

        modeled_block = synth_to_dashboard_payload(
            synth, target_audience=max(implied_audience, 1),
        )
        modeled_block['source']      = synth.get('source', 'fallback')
        modeled_block['notes']       = synth.get('notes', '')
        modeled_block['target_type'] = target_type

    # ── Phase C: attach the research footprint to modeled_view ───────
    if fp:
        modeled_block['marketing_footprint'] = fp
        try:
            modeled_block['touchpoint_bubbles'] = footprint_to_bubbles(fp)
        except Exception as e:
            print(f"[research-anchored] bubbles failed (non-fatal): {e}")
        try:
            modeled_block['touchpoint_spider'] = footprint_to_spider(fp, target=target)
        except Exception as e:
            print(f"[research-anchored] spider failed (non-fatal): {e}")

    # ── Phase C.2: attach the site-funnel block ──────────────────────
    if site_funnel:
        modeled_block['site_funnel'] = site_funnel

    # ── Phase D: compose the summary envelope ────────────────────────
    summary = {
        'meta': {
            'project_name':   project_name,
            'target':         target,
            'target_variants': _target_variants(target),
            'start_date':     start_date,
            'end_date':       end_date,
            'lookback_days':  lookback_days,
            'forward_days':   forward_days,
            'conversion_patterns':       list(CONVERSION_PATTERNS),
            'extra_touchpoint_keywords': surfaces,
            'cut_options':    [{'key': k, 'label': lbl} for k, lbl in CUT_DISPLAY_ORDER],
            'created_by':     username,
            'created_at':     datetime.utcnow().isoformat() + 'Z',
            'duration_sec':   round(time.time() - started, 1),
            'job_id':         job_id,
            'cohort_mode':    'research',
            'target_type':    target_type,
            'is_movie':       (target_type == 'movie'),  # legacy alias
            'box_office_millions':       float(box_office_millions or 0.0),
            'avg_ticket_price':          float(avg_ticket_price or 15.0),
            'monthly_visitors_millions': float(monthly_visitors_millions or 0.0),
            'us_viewers_millions':       float(us_viewers_millions or 0.0),
            'implied_audience':          int(implied_audience or 0),
            'scaling_factor':            1.0,
            'matched_uids':              0,
            'matched_uids_total':        0,
            'events_pulled':             0,
            'cohort_was_empty':          True,
            'research_anchored':         True,
            'research_duration_sec':     research_sec,
            'synth_duration_sec':        synth_sec,
            'research_source':           ('claude' if fp else 'unavailable'),
        },
        # Raw panel views are empty (we bypassed ClickHouse on purpose).
        'kpis': {
            'total_users': 0, 'converted_users': 0, 'conversion_pct': 0.0,
            'avg_journey_duration_days': 0.0, 'avg_sessions_to_convert': 0.0,
            'avg_events_per_user': 0.0,
        },
        'clusters':    [],
        'cuts':        {axis: [] for axis, _ in CUT_DISPLAY_ORDER},
        'touchpoints': {
            'baseline_conv_rate': 0.0, 'cohort_size': 0, 'converters': 0,
            'rows': [], 'overlap': [], 'touch_distribution': [],
        },
        'keywords':   [],
        'post_hosts': [],
        'path_to_purchase': {
            'mode': 'research-anchored', 'cohort_size': 0, 'steps': 0,
            'columns': [], 'top_paths': [],
        },
        'facts':      _facts_from_footprint(fp, target),
        'modeled_view': modeled_block,
    }
    if site_funnel:
        summary['site_funnel'] = site_funnel

    # ── Phase E: persist ─────────────────────────────────────────────
    try:
        s3_key = _persist(s3_client, summary, project_name, username, job_id)
        summary['s3_key'] = s3_key
        print(f"[research-anchored] wrote s3://{S3_BUCKET}/{s3_key}")
    except Exception as e:
        return {'status': 'failed', 'error': f'S3 write failed: {e}',
                'summary': summary}

    total_sec = round(time.time() - started, 1)
    print(f"[research-anchored] DONE in {total_sec}s | "
          f"job_id={job_id} implied_audience={implied_audience:,}")

    return {
        'status':       'completed',
        's3_key':       s3_key,
        'summary':      summary,
        'duration_sec': total_sec,
        'job_id':       job_id,
    }


def _date_range_days(start_date: str, end_date: str) -> int:
    try:
        s = datetime.fromisoformat(start_date[:10])
        e = datetime.fromisoformat(end_date[:10])
        return max(1, (e - s).days)
    except Exception:
        return 30


def _root_domain(target: str) -> str:
    """Best-effort guess at the root domain for a website target.
    e.g. "Fandango" -> "fandango.com"; "fandango.com" stays as-is."""
    t = (target or '').strip().lower()
    if '.' in t:
        return t.split('/')[0]
    return f'{t.replace(" ", "")}.com'


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='Run Journey IQ in research-anchored mode '
                    '(no ClickHouse — research + synth only).')
    p.add_argument('--target', required=True, help='e.g. "The Goat"')
    p.add_argument('--type', dest='target_type', default='movie',
                   choices=['movie', 'website', 'tv_show', 'brand'])
    p.add_argument('--start', required=True, help='YYYY-MM-DD')
    p.add_argument('--end', required=True, help='YYYY-MM-DD')
    p.add_argument('--project', default='Journey IQ (research-anchored)')
    p.add_argument('--user', default='admin')
    # movie
    p.add_argument('--box-office', type=float, default=0.0,
                   help='US box office in millions USD (movie)')
    p.add_argument('--ticket-price', type=float, default=15.0)
    # website
    p.add_argument('--monthly-visitors', type=float, default=0.0,
                   help='US monthly uniques in millions (website)')
    # tv
    p.add_argument('--us-viewers', type=float, default=0.0,
                   help='US viewers in millions (tv_show)')
    # Phase toggles — skip the heavy footprint/synth calls when you only
    # need the site-funnel block (much faster, ~1 Claude call total).
    p.add_argument('--skip-footprint', action='store_true',
                   help='skip Phase R (marketing-footprint research). '
                        'Use this when you only want the site-funnel '
                        'block — fastest path for website targets.')
    p.add_argument('--skip-synth', action='store_true',
                   help='skip Phase B (journey synthesis). Combine with '
                        '--skip-footprint to get just the site-funnel '
                        'output in ~60-90s.')
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    result = run_research_anchored_job(
        target=args.target,
        target_type=args.target_type,
        start_date=args.start,
        end_date=args.end,
        project_name=args.project,
        username=args.user,
        box_office_millions=args.box_office,
        avg_ticket_price=args.ticket_price,
        monthly_visitors_millions=args.monthly_visitors,
        us_viewers_millions=args.us_viewers,
        skip_footprint=args.skip_footprint,
        skip_synth=args.skip_synth,
    )
    if result.get('status') == 'completed':
        print('')
        print('=' * 70)
        print(f"OK — s3_key = {result['s3_key']}")
        print(f"     job_id = {result['job_id']}")
        print(f"     runtime = {result['duration_sec']}s")
        print(f"View in dashboard: https://behavioral-graph-dev.onrender.com")
        print(f"  (the run will appear in /api/journey-iq/list)")
        return 0
    print(f"FAILED: {result.get('error')}")
    return 1


if __name__ == '__main__':
    raise SystemExit(main())


__all__ = ['run_research_anchored_job', 'main']
