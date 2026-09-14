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
    TRENDS_DIGEST_FROM   default 'Crosswalk <no_reply@crosswalknyc.com>'
    TRENDS_DIGEST_REGION default 'us-east-2'
    TRENDS_DIGEST_BUCKET default 'dashboard-inputs'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from html import escape as _html_escape
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
# Send as Crosswalk from the shared no_reply identity (a verified SES sender
# in us-east-2, same one the newsletter uses). Using jenna@ made Gmail show
# the personal contact name "Jenna Menking"; a non-personal address lets the
# "Crosswalk" display name stand. Replies route to the general inbox.
SES_FROM    = os.environ.get('TRENDS_DIGEST_FROM',   'Crosswalk <no_reply@crosswalknyc.com>')
SES_REPLY_TO = os.environ.get('TRENDS_DIGEST_REPLY_TO', 'hello@crosswalknyc.com')
# Public dashboard URL for the "manage your watchlist" link. The old
# hardcoded www.behavioralgraph.com does not resolve (NXDOMAIN); the live
# production host is the verified Render custom domain below. Overridable
# via env so the dev/staging digest can point elsewhere.
APP_URL     = (os.environ.get('TRENDS_DIGEST_APP_URL')
               or os.environ.get('APP_URL')
               or os.environ.get('PUBLIC_APP_URL')
               or 'https://dashboard.crosswalknyc.com').rstrip('/')

# Deep-link straight into the Trends IQ view (index.html reads ?view= on
# boot) so the CTA lands the reader where the watchlist lives instead of
# the default dashboard tab.
CTA_URL     = f"{APP_URL}/?view=trendsIQ"

# --- Crosswalk brand tokens (August 2026 system) --------------------------
# This email is an Off-White (light) surface, so it follows the "twin rule":
# Signal Green (#C7F23E) is dark-surface only and blows out on Off-White, so
# the accent here is Signal Olive (#5E7E12) with the small-text link olive
# (#547110). One accent, one job. Table-based single column, ~600px, inline
# styles, no web fonts (Inter if the client has it, else a system stack).
_FONT = ("'Inter 18pt','Inter',-apple-system,BlinkMacSystemFont,"
         "'Segoe UI',Roboto,Helvetica,Arial,sans-serif")
_BRAND = {
    'page':   '#E9E8E1',   # Neutral Off-White ground
    'card':   '#FFFFFF',   # raised light surface
    'ink':    '#0C1618',   # Graphite Teal / primary text
    'body':   '#5C6560',   # body + subhead grey
    'muted':  '#888C89',   # source / count grey
    'footer': '#5C6466',   # footer grey
    'border': '#C9C6BA',   # hairline divider
    'olive':  '#5E7E12',   # Signal Olive (light-surface accent)
    'link':   '#547110',   # small-text olive, for links
}


def _brand_button(url: str, label: str) -> str:
    """One bulletproof CTA. Signal Olive fill (matching the footer link)
    with an Off-White label. VML fallback keeps the shape in Outlook."""
    return (
        "<!--[if mso]>"
        f"<v:roundrect xmlns:v=\"urn:schemas-microsoft-com:vml\" "
        f"xmlns:w=\"urn:schemas-microsoft-com:office:word\" href=\"{url}\" "
        "style=\"height:46px;v-text-anchor:middle;width:220px;\" arcsize=\"18%\" "
        f"strokecolor=\"{_BRAND['link']}\" fillcolor=\"{_BRAND['link']}\">"
        f"<w:anchorlock/><center style=\"color:{_BRAND['page']};"
        "font-family:sans-serif;font-size:15px;font-weight:bold;\">"
        f"{label}</center></v:roundrect><![endif]-->"
        "<!--[if !mso]><!-- -->"
        f"<a href=\"{url}\" style=\"display:inline-block;background:{_BRAND['link']};"
        f"color:{_BRAND['page']};font-family:{_FONT};font-size:15px;font-weight:700;"
        "line-height:46px;text-decoration:none;padding:0 30px;border-radius:8px;\">"
        f"{label}</a><!--<![endif]-->"
    )


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


def _latest_present_rank(arc: dict):
    """Most recent day in the arc that actually has a rank.

    The arc's raw `current_rank` is literally the last *calendar* day
    (see trends_history._summarize_arc / _iter_recent_days, which always
    make today the final day). The digest cron runs in the early UTC
    morning, before today's snapshot has been scraped, so that last day
    is `present:false` and `current_rank` is None on every run - which
    made every watched item look unchanged and produced the perpetual
    "no material movement" digest. Walk back to the newest day that has
    real data so movement is measured off the latest available snapshot.
    """
    for d in reversed(arc.get('days') or []):
        if d.get('present') and d.get('rank') is not None:
            return d.get('rank')
    return arc.get('current_rank')


def _recent_two_present_ranks(arc: dict) -> tuple[Optional[int], Optional[int]]:
    """(prev_rank, current_rank) from the two most recent days in the arc
    that actually have a rank. Drives the 'Daily Move' column straight off
    the item's own history, so it shows a delta even when the state-based
    alert threshold reports 'no material movement'."""
    ranks = [d.get('rank') for d in (arc.get('days') or [])
             if d.get('present') and d.get('rank') is not None]
    cur = ranks[-1] if ranks else None
    prev = ranks[-2] if len(ranks) >= 2 else None
    return prev, cur


def _compute_alerts_for_user(user_slug: str) -> tuple[list[dict], dict, list[dict], list[dict]]:
    """Return (alerts, new_state, watchlist_entries, watch_items) for this user.

    `alerts` = list of alert dicts fired today (only material movements).
    `new_state` = the arcs to persist as "yesterday" for the next run.
    `watch_items` = every watched item with its current/best rank and the
                    day-over-day move, so the digest can always show the full
                    watchlist table regardless of whether any alert fired.
    """
    entries = trends_watchlist.load_watchlist(user_slug)
    if not entries:
        return [], {}, [], []
    prior = _load_prior_state(user_slug)
    prior_arcs = prior.get('arcs', {}) if isinstance(prior, dict) else {}

    alerts: list[dict] = []
    new_arcs: dict[str, dict] = {}
    watch_items: list[dict] = []
    for e in entries:
        kind   = e.get('kind') or ''
        source = e.get('source') or ''
        key    = e.get('key') or ''
        geo    = e.get('geo') or 'National'
        slug   = _entry_slug(kind, source, key)
        curr = trends_history.history_for_item(kind, source, key, geo=geo, days=14, force_refresh=True)
        # Normalize current_rank to the latest day that actually has data
        # so classify_alert_transition compares real snapshots run-over-run
        # (today's calendar day is usually unscraped at digest time).
        curr['current_rank'] = _latest_present_rank(curr)
        prev_day_rank, cur_day_rank = _recent_two_present_ranks(curr)
        new_arcs[slug] = {
            'current_rank': curr.get('current_rank'),
            'best_rank':    curr.get('best_rank'),
            'first_seen':   curr.get('first_seen'),
            'present_days': curr.get('present_days'),
            'momentum':     curr.get('momentum'),
        }
        watch_items.append({
            'label':        e.get('label') or key,
            'kind':         kind,
            'source':       source,
            'geo':          geo,
            'current_rank': curr.get('current_rank'),
            'best_rank':    curr.get('best_rank'),
            'prev_rank':    prev_day_rank,
        })
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
    return alerts, new_state, entries, watch_items


def _render_email(user_slug: str, alerts: list[dict],
                   entries: list[dict],
                   watch_items: Optional[list[dict]] = None) -> tuple[str, str, str]:
    """Return (subject, html, text) for the digest email.

    Styled to the Crosswalk brand system (August 2026): Off-White ground,
    Inter 18pt, Signal Olive as the single light-surface accent, one
    bulletproof CTA. Single-column table layout, inline styles only.

    Always renders the full watchlist (item, current rank, best rank, and the
    day-over-day move) so the digest is useful even when nothing crossed the
    alert threshold.
    """
    watch_items = watch_items or []
    today = datetime.now(timezone.utc).strftime('%A, %b %-d')
    B = _BRAND

    watched = len(watch_items)
    movers = sum(
        1 for w in watch_items
        if w.get('prev_rank') is not None and w.get('current_rank') is not None
        and w.get('prev_rank') != w.get('current_rank')
    )
    if movers == 0:
        subject = f"Trends IQ: no watchlist moves, {today}"
        preheader = (f"Your {watched} watched item{'s' if watched != 1 else ''} held rank. "
                     "Current and best inside.")
    else:
        subject = f"Trends IQ: {movers} watchlist move{'s' if movers != 1 else ''}, {today}"
        preheader = (f"{movers} of {watched} watched item{'s' if watched != 1 else ''} "
                     "moved since yesterday.")

    def _rank(r) -> str:
        return f"#{r}" if isinstance(r, int) else "-"

    def _move(prev, cur) -> tuple[str, str]:
        """(plain, html) for the Daily Move cell. A positive delta means the
        item moved up the chart (to a smaller rank number)."""
        if cur is None and prev is None:
            return "-", f"<span style=\"color:{B['muted']};\">-</span>"
        if cur is None:
            return "off", f"<span style=\"color:{B['body']};\">off</span>"
        if prev is None:
            return "new", f"<span style=\"color:{B['olive']};font-weight:700;\">new</span>"
        d = prev - cur
        if d > 0:
            return f"+{d}", f"<span style=\"color:{B['olive']};font-weight:700;\">&#9650;{d}</span>"
        if d < 0:
            return f"{d}", f"<span style=\"color:{B['body']};font-weight:700;\">&#9660;{abs(d)}</span>"
        return "0", f"<span style=\"color:{B['muted']};\">0</span>"

    # ---- plain-text part -------------------------------------------------
    text_lines = [
        "CROSSWALK / TRENDS IQ",
        "",
        "Good morning!",
        f"Your Trends IQ Digest for {today} is here.",
        "",
        "Monitor and manage your watchlist in Trends IQ:",
        CTA_URL,
        "",
        "YOUR WATCHLIST  (current / best / daily move)",
    ]
    for w in watch_items:
        mv_text, _ = _move(w.get('prev_rank'), w.get('current_rank'))
        text_lines.append(
            f"  {w.get('label') or ''}: "
            f"current {_rank(w.get('current_rank'))}, "
            f"best {_rank(w.get('best_rank'))}, "
            f"move {mv_text}"
        )
    text_lines += ["", "--", "CROSSWALK / BEHAVIORAL INTELLIGENCE"]

    # ---- HTML watchlist table (always shown) -----------------------------
    def _num_cell(content: str) -> str:
        return (f"<td align=\"right\" style=\"padding:11px 0 11px 10px;"
                f"border-top:1px solid {B['border']};font-size:14px;font-weight:700;"
                f"color:{B['ink']};white-space:nowrap;vertical-align:top;\">{content}</td>")

    item_rows = []
    for w in watch_items:
        label = _html_escape(w.get('label') or '')
        sub_bits = [str(x) for x in (w.get('kind'), w.get('source'), w.get('geo')) if x]
        sub = _html_escape(" \u00b7 ".join(sub_bits)).upper()
        _, mv_html = _move(w.get('prev_rank'), w.get('current_rank'))
        item_rows.append(
            "<tr>"
            f"<td style=\"padding:11px 0;border-top:1px solid {B['border']};vertical-align:top;\">"
            f"<div style=\"font-size:14px;line-height:1.3;font-weight:700;color:{B['ink']};\">{label}</div>"
            f"<div style=\"margin-top:3px;font-size:10px;letter-spacing:0.5px;"
            f"text-transform:uppercase;color:{B['muted']};\">{sub}</div></td>"
            + _num_cell(_rank(w.get('current_rank')))
            + _num_cell(_rank(w.get('best_rank')))
            + _num_cell(mv_html)
            + "</tr>"
        )

    _col = "font-size:9px;letter-spacing:1px;text-transform:uppercase;color:" + B['muted'] + ";"
    section_html = (
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        "style=\"border-collapse:collapse;\">"
        f"<tr><td colspan=\"4\" style=\"padding:0 0 2px;font-size:11px;letter-spacing:1px;"
        f"text-transform:uppercase;font-weight:700;color:{B['ink']};\">Your watchlist</td></tr>"
        f"<tr><td colspan=\"4\" style=\"padding:0 0 12px;font-size:12px;line-height:1.4;"
        f"color:{B['body']};\">Current and best rank, with the move since yesterday.</td></tr>"
        "<tr><td></td>"
        f"<td align=\"right\" style=\"padding:0 0 6px 10px;{_col}\">Current</td>"
        f"<td align=\"right\" style=\"padding:0 0 6px 10px;{_col}\">Best</td>"
        f"<td align=\"right\" style=\"padding:0 0 6px 10px;{_col}\">Daily move</td></tr>"
        + ''.join(item_rows) +
        "</table>"
    )

    button = _brand_button(CTA_URL, "Open Trends IQ &rarr;")

    html = (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"x-apple-disable-message-reformatting\">"
        "<title>Crosswalk Trends IQ</title></head>"
        f"<body style=\"margin:0;padding:0;background:{B['page']};\">"
        f"<div style=\"display:none;max-height:0;overflow:hidden;opacity:0;\">{preheader}</div>"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        f"style=\"background:{B['page']};\"><tr><td align=\"center\" style=\"padding:32px 16px;\">"
        "<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" "
        f"style=\"width:600px;max-width:600px;background:{B['card']};border:1px solid {B['border']};"
        "border-radius:12px;\">"
        # header row: eyebrow (olive dot + product) left, wordmark right
        f"<tr><td style=\"padding:28px 32px 6px;font-family:{_FONT};\">"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\"><tr>"
        "<td align=\"left\" style=\"vertical-align:middle;\">"
        f"<span style=\"display:inline-block;width:8px;height:8px;border-radius:8px;"
        f"background:{B['olive']};\"></span>"
        f"<span style=\"padding-left:8px;font-size:11px;letter-spacing:2.6px;"
        f"text-transform:uppercase;color:{B['body']};\">Trends IQ</span></td>"
        f"<td align=\"right\" style=\"vertical-align:middle;font-size:16px;font-weight:800;"
        f"letter-spacing:0.2px;color:{B['ink']};\">Crosswalk</td>"
        "</tr></table></td></tr>"
        # greeting + lead
        f"<tr><td style=\"padding:16px 32px 0;font-family:{_FONT};\">"
        f"<p style=\"margin:0;font-size:20px;line-height:1.3;font-weight:800;color:{B['ink']};\">"
        "Good morning!</p>"
        f"<p style=\"margin:8px 0 0;font-size:16px;line-height:1.4;color:{B['ink']};\">"
        f"Your Trends IQ Digest for {today} is here.</p>"
        f"<p style=\"margin:12px 0 0;font-size:15px;line-height:1.5;color:{B['body']};\">"
        "Monitor and manage your watchlist in Trends IQ.</p></td></tr>"
        # CTA
        f"<tr><td style=\"padding:18px 32px 6px;font-family:{_FONT};\">{button}</td></tr>"
        # divider
        f"<tr><td style=\"padding:10px 32px 0;\"><div style=\"border-top:1px solid {B['border']};"
        "font-size:0;line-height:0;\">&nbsp;</div></td></tr>"
        # sections
        f"<tr><td style=\"padding:22px 32px 4px;font-family:{_FONT};\">{section_html}</td></tr>"
        # footer
        f"<tr><td style=\"padding:18px 32px 28px;font-family:{_FONT};border-top:1px solid {B['border']};\">"
        f"<div style=\"font-size:9px;letter-spacing:2px;text-transform:uppercase;color:{B['footer']};\">"
        "Crosswalk &nbsp;/&nbsp; Behavioral intelligence</div>"
        f"<div style=\"margin-top:8px;font-size:12px;line-height:1.5;color:{B['footer']};\">"
        f"Manage your watchlist in <a href=\"{CTA_URL}\" style=\"color:{B['link']};"
        "text-decoration:underline;\">Trends IQ</a>.</div></td></tr>"
        "</table></td></tr></table></body></html>"
    )

    return subject, html, '\n'.join(text_lines)


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
        ReplyToAddresses=[SES_REPLY_TO] if SES_REPLY_TO else [],
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

    alerts, new_state, entries, watch_items = _compute_alerts_for_user(user_slug)
    if not entries:
        return {'user_slug': user_slug, 'sent': False, 'reason': 'empty_watchlist'}

    subject, html, text = _render_email(user_slug, alerts, entries, watch_items)
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
