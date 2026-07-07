"""
Trends IQ digest job.

Runs once daily (Hetzner cron). For every user with a non-empty
watchlist, computes today's arc for each watched item, compares it
against the previous arc snapshot, classifies each transition, and
sends a per-user SES email summarizing the movement.

The per-user "prior state" is stored at:

    s3://dashboard-inputs/trends_iq_alerts/{user_slug}/state.json

which holds the arc summary as-of the last digest run. On the first
digest run for a user, all watched items are treated as NEW.

Cron entry (add alongside run_all.py, 15 minutes later so scrapers
have written today's snapshots first):

    15 5 * * *  cd /root/finished_codes/bg-webapp && /usr/bin/python3 -m scripts.trends_digest >> /var/log/trends_digest.log 2>&1

Manual test (single user, no email send):

    python3 -m scripts.trends_digest --dry-run
    python3 -m scripts.trends_digest --dry-run --user jenna_crosswalknyc_com

Environment:
    TRENDS_DIGEST_FROM   default 'BehavioralGraph <jenna@crosswalknyc.com>'
    TRENDS_DIGEST_REGION default 'us-east-2'
    TRENDS_DIGEST_BUCKET default 'dashboard-inputs'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Allow running as `python3 -m scripts.trends_digest` from bg-webapp/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trends_history          # noqa: E402
import trends_watchlist        # noqa: E402

logger = logging.getLogger(__name__)

BUCKET      = os.environ.get('TRENDS_DIGEST_BUCKET') or os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
STATE_PREFIX = 'trends_iq_alerts/'
SES_REGION  = os.environ.get('TRENDS_DIGEST_REGION', 'us-east-2')
SES_FROM    = os.environ.get('TRENDS_DIGEST_FROM',   'BehavioralGraph <jenna@crosswalknyc.com>')


def _s3():
    import boto3  # type: ignore
    return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def _ses():
    import boto3  # type: ignore
    return boto3.client('ses', region_name=SES_REGION)


def _state_key(user_slug: str) -> str:
    return f'{STATE_PREFIX}{user_slug}/state.json'


def _load_prior_state(user_slug: str) -> dict:
    try:
        resp = _s3().get_object(Bucket=BUCKET, Key=_state_key(user_slug))
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return {}


def _save_current_state(user_slug: str, state: dict) -> None:
    try:
        _s3().put_object(
            Bucket=BUCKET,
            Key=_state_key(user_slug),
            Body=json.dumps(state, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
            ServerSideEncryption='AES256',
        )
    except Exception as e:
        logger.warning("save state for %s failed: %s", user_slug, e)


def _entry_slug(kind: str, source: str, key: str) -> str:
    return f"{kind}|{source}|{key}"


def _compute_alerts_for_user(user_slug: str) -> tuple[list[dict], dict, list[dict]]:
    """Return (alerts, new_state, watchlist_entries) for this user.

    `alerts` = list of alert dicts fired today (only material movements).
    `new_state` = the arcs to persist as "yesterday" for the next run.
    """
    entries = trends_watchlist.load_watchlist(user_slug)
    if not entries:
        return [], {}, []
    prior = _load_prior_state(user_slug)
    prior_arcs = prior.get('arcs', {}) if isinstance(prior, dict) else {}

    alerts: list[dict] = []
    new_arcs: dict[str, dict] = {}
    for e in entries:
        kind   = e.get('kind') or ''
        source = e.get('source') or ''
        key    = e.get('key') or ''
        geo    = e.get('geo') or 'National'
        slug   = _entry_slug(kind, source, key)
        curr = trends_history.history_for_item(kind, source, key, geo=geo, days=14, force_refresh=True)
        new_arcs[slug] = {
            'current_rank': curr.get('current_rank'),
            'best_rank':    curr.get('best_rank'),
            'first_seen':   curr.get('first_seen'),
            'present_days': curr.get('present_days'),
            'momentum':     curr.get('momentum'),
        }
        prev = prior_arcs.get(slug)
        prev_wrapped = {
            'current_rank': (prev or {}).get('current_rank'),
            'days':         [{'rank': (prev or {}).get('current_rank'), 'present': (prev or {}).get('current_rank') is not None}] if prev else [],
        } if prev else None
        alert = trends_history.classify_alert_transition(prev_wrapped, curr)
        if alert:
            alert.setdefault('label', e.get('label') or key)
            alerts.append(alert)

    new_state = {
        'user_slug':   user_slug,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'arcs':        new_arcs,
    }
    return alerts, new_state, entries


def _render_email(user_slug: str, alerts: list[dict],
                   entries: list[dict]) -> tuple[str, str, str]:
    """Return (subject, html, text) for the digest email."""
    today = datetime.now(timezone.utc).strftime('%A, %b %-d')
    n = len(alerts)
    if n == 0:
        subject = f"Trends IQ digest - {today} - no material moves"
    else:
        subject = f"Trends IQ digest - {today} - {n} watchlist move{'s' if n != 1 else ''}"

    bucket = {
        'BREAKOUT':  [], 'RISING':   [], 'FALLING': [],
        'DROPPED_OFF': [], 'RETURNED': [], 'NEW': [],
    }
    for a in alerts:
        bucket.setdefault(a.get('alert_type', 'NEW'), []).append(a)

    def _row_line(a: dict) -> str:
        label = a.get('label') or a.get('key') or ''
        cur = a.get('curr_rank')
        prev = a.get('prev_rank')
        if cur is not None and prev is not None:
            return f"  {label}: was #{prev} -> now #{cur}"
        if cur is not None:
            return f"  {label}: now #{cur}"
        if prev is not None:
            return f"  {label}: dropped from #{prev}"
        return f"  {label}"

    order = ['BREAKOUT', 'RISING', 'RETURNED', 'NEW', 'FALLING', 'DROPPED_OFF']
    text_lines = [f"Trends IQ digest for {today}",
                   f"You are watching {len(entries)} item(s).", ""]
    html_lines = [
        f"<p>Hey, here's your Trends IQ digest for <b>{today}</b>. You're watching <b>{len(entries)}</b> item(s).</p>",
    ]
    if not alerts:
        text_lines.append("No material movement on any of your watched items today.")
        html_lines.append("<p>No material movement on any of your watched items today.</p>")
    else:
        for tag in order:
            items = bucket.get(tag) or []
            if not items:
                continue
            text_lines.append(f"{tag} ({len(items)}):")
            html_lines.append(f"<h3 style='margin-bottom:6px;'>{tag} <span style='opacity:0.65;font-weight:400;'>({len(items)})</span></h3><ul style='margin-top:0;'>")
            for a in items:
                text_lines.append(_row_line(a))
                label = (a.get('label') or a.get('key') or '')
                cur = a.get('curr_rank'); prev = a.get('prev_rank')
                if cur is not None and prev is not None:
                    detail = f"was #{prev} -> now <b>#{cur}</b>"
                elif cur is not None:
                    detail = f"now <b>#{cur}</b>"
                elif prev is not None:
                    detail = f"dropped from #{prev}"
                else:
                    detail = ''
                html_lines.append(f"<li><b>{label}</b> {detail}</li>")
            html_lines.append("</ul>")
            text_lines.append("")

    text_lines.append("--")
    text_lines.append("Open Trends IQ to manage your watchlist:")
    text_lines.append("https://www.behavioralgraph.com/  (Culture Ranker -> Trends)")
    html_lines.append("<hr style='margin:24px 0;border:0;border-top:1px solid #ddd;'/>")
    html_lines.append("<p style='color:#666;font-size:0.9em;'>Manage your watchlist in Trends IQ: <a href='https://www.behavioralgraph.com/'>open the dashboard</a>.</p>")
    return subject, ''.join(html_lines), '\n'.join(text_lines)


def _send_email(to_addr: str, subject: str, html: str, text: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"\n--- DRY RUN: would send to {to_addr} ---")
        print(f"Subject: {subject}")
        print(text)
        print(f"--- end ---\n")
        return
    _ses().send_email(
        Source=SES_FROM,
        Destination={'ToAddresses': [to_addr]},
        Message={
            'Subject': {'Data': subject, 'Charset': 'UTF-8'},
            'Body':    {'Html': {'Data': html, 'Charset': 'UTF-8'},
                         'Text': {'Data': text, 'Charset': 'UTF-8'}},
        },
    )


def run_for_user(user_slug: str, *, dry_run: bool = False) -> dict:
    email = trends_watchlist.resolve_user_email(user_slug)
    if not email:
        logger.info("digest: no email for user_slug=%s (skipping)", user_slug)
        return {'user_slug': user_slug, 'sent': False, 'reason': 'no_email'}

    alerts, new_state, entries = _compute_alerts_for_user(user_slug)
    if not entries:
        return {'user_slug': user_slug, 'sent': False, 'reason': 'empty_watchlist'}

    subject, html, text = _render_email(user_slug, alerts, entries)
    try:
        _send_email(email, subject, html, text, dry_run=dry_run)
        sent = True
        err = None
    except Exception as e:
        sent = False
        err = str(e)
        logger.exception("digest send failed for %s (%s)", user_slug, email)

    if sent and not dry_run:
        _save_current_state(user_slug, new_state)

    return {
        'user_slug':    user_slug,
        'email':        email,
        'alerts':       len(alerts),
        'watched':      len(entries),
        'sent':         sent,
        'error':        err,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Trends IQ daily digest email job')
    ap.add_argument('--dry-run', action='store_true',
                     help='Print email bodies instead of sending via SES')
    ap.add_argument('--user', default=None,
                     help='Send only for this user_slug (for testing)')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    if args.user:
        users = [args.user]
    else:
        users = trends_watchlist.list_all_users()
        logger.info("digest: found %d watchlist(s)", len(users))
    if not users:
        print("no watchlists found - nothing to send")
        return 0
    results = []
    for u in users:
        res = run_for_user(u, dry_run=args.dry_run)
        results.append(res)
    print(f"\ndigest complete: {len(results)} users processed")
    for r in results:
        print(f"  {r.get('user_slug'):<32s} watched={r.get('watched', 0):>3d} "
               f"alerts={r.get('alerts', 0):>3d} sent={r.get('sent')} "
               f"{('err=' + r['error']) if r.get('error') else ''}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
