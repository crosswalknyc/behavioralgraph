"""Cookie-gap notifier for Trends IQ scrapers.

When a scraper fails because a donated-cookie session for a specific
domain is stale / missing / cannot bypass a bot wall, the scraper
calls `notify_cookie_gap(source, domain)`. This module:

  1. Deduplicates: at most one email per (source, domain) per UTC day
     using an S3 stamp under `trends_iq_cookie_gaps/YYYY-MM-DD/...`.
  2. Sends a plain SES email to `jenna@` + `jessie@crosswalknyc.com`
     with a short note describing which platform is dark and which
     domain needs a re-donation.

Rule from user 2026-07-29:
    "dont ever show something like Bot-blocked. To enable: log into
     amctheatres.com... instead if something is blocked or cant be
     scraped just show 'Loading' and then email me and jessie@..."

Scrapers must NOT surface any operator-facing text (URLs to scripts,
domain names in dashboard copy, "log in" instructions, etc.) - the
dashboard shows a neutral 'warming up' state and this module handles
the offline notification.

Best-effort: SES failures and S3 stamp failures are logged and never
raised. A cookie-gap notification NEVER blocks a scraper run.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

RECIPIENTS = [
    "jenna@crosswalknyc.com",
    "jessie@crosswalknyc.com",
]
SOURCE_ADDR = "BehavioralGraph <jenna@crosswalknyc.com>"
AWS_REGION  = "us-east-2"

_STAMP_BUCKET = "dashboard-inputs"
_STAMP_PREFIX = "trends_iq_cookie_gaps/"

# Repo path baked into the copy-paste terminal command below. The
# recipient laptop lives at /Users/jennamenking/Desktop/finished_codes
# on Jenna's Mac; that's the operator machine where donated cookies
# are extracted from Chrome. Override at runtime via
# `COOKIE_DONATE_REPO_ROOT` if a second operator machine ever ships.
_DEFAULT_REPO_ROOT = "/Users/jennamenking/Desktop/finished_codes/bg-webapp"


def _repo_root() -> str:
    import os as _os
    return (_os.environ.get("COOKIE_DONATE_REPO_ROOT") or _DEFAULT_REPO_ROOT).rstrip("/")


def _today_iso() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _stamp_key(source: str, domain: str) -> str:
    safe_domain = domain.replace("/", "_").replace(":", "_")
    return f"{_STAMP_PREFIX}{_today_iso()}/{source}__{safe_domain}.stamp"


def _already_notified_today(source: str, domain: str) -> bool:
    try:
        import boto3
        s3 = boto3.client("s3")
        s3.head_object(Bucket=_STAMP_BUCKET, Key=_stamp_key(source, domain))
        return True
    except Exception:
        return False


def _mark_notified_today(source: str, domain: str) -> None:
    try:
        import boto3
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=_STAMP_BUCKET,
            Key=_stamp_key(source, domain),
            Body=str(int(time.time())).encode(),
            ContentType="text/plain",
        )
    except Exception as e:
        logger.info("cookie_gap_notify: stamp write failed: %s", e)


def notify_cookie_gap(source: str, domain: str,
                       *, reason: str = "",
                       force: bool = False,
                       recipients: Optional[list[str]] = None) -> bool:
    """Notify operators that `source`'s donated `domain` cookies are
    stale / missing. Returns True if the email was sent (or was
    correctly deduplicated), False on SES failure. Never raises.

    `source` is the internal scraper key ("amc", "regal", "max",
    "hulu", etc.). `domain` is the cookie domain to re-donate
    ("amctheatres.com", "hbomax.com"). `reason` is an optional
    freeform hint that goes into the email body but is NEVER
    surfaced to the dashboard.
    """
    source = (source or "").strip().lower()
    domain = (domain or "").strip().lower()
    if not source or not domain:
        return False

    if not force and _already_notified_today(source, domain):
        logger.info("cookie_gap_notify: already notified for %s/%s today",
                     source, domain)
        return True

    to = list(recipients) if recipients else list(RECIPIENTS)

    # ------------------------------------------------------------------
    # One-click / copy-paste actions.
    #
    # `cwcookie_url`  - custom URL scheme handled by the `Cookie Donate`
    #                   AppleScript app (scripts/trends_scrapers/
    #                   macos_url_handler/). Clicking from Apple Mail
    #                   opens Terminal + runs donate_cookies for this
    #                   domain. One-time install below turns every
    #                   future email into a one-click action.
    # `terminal_cmd`  - the exact shell command; every mail client
    #                   renders a <pre> block that's copy-pasteable
    #                   with one triple-click, so Gmail-web users
    #                   who can't use custom URL schemes get parity.
    # ------------------------------------------------------------------
    repo_root    = _repo_root()
    cwcookie_url = f"cwcookie://donate/{domain}"
    terminal_cmd = (
        f'cd "{repo_root}" && '
        f"python3 scripts/trends_scrapers/donate_cookies.py {domain}"
    )
    install_cmd = f'cd "{repo_root}" && bash scripts/trends_scrapers/macos_url_handler/install.sh'

    subject = f"Re-donate cookies for {domain} ({source} dark)"

    body_text = (
        f"Trends IQ scraper for '{source}' fell back to the warming-up "
        f"state today because the donated cookie session for "
        f"'{domain}' is stale or was rejected by the site.\n\n"
        f"To restore the tile, sign into {domain} in your laptop's "
        f"Chrome, then run either action below.\n\n"
        f"[1] One-click (macOS Mail.app, requires one-time setup):\n"
        f"    {cwcookie_url}\n\n"
        f"[2] Copy-paste into any Terminal on your Mac:\n"
        f"    {terminal_cmd}\n\n"
    )
    if reason:
        body_text += f"Detail: {reason}\n\n"
    body_text += (
        f"One-time setup for the clickable link above (only run once "
        f"per Mac):\n"
        f"    {install_cmd}\n\n"
        f"This notice fires at most once per (source, domain) per UTC "
        f"day and never surfaces to the dashboard - users just see "
        f"a neutral 'warming up' tile."
    )

    # HTML body - Apple Mail honors custom URL schemes (cwcookie://)
    # directly. Gmail-web strips them, but the same email always
    # includes the copy-paste <pre> block below as a fallback.
    reason_html = (
        f"<p style='color:#666;font-size:12px;margin:6px 0;'>"
        f"Detail: {reason}</p>"
        if reason else ""
    )
    body_html = f"""\
<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
             font-size:14px;line-height:1.55;color:#111;">
  <p>Trends IQ scraper for <b>{source}</b> fell back to the
     <i>warming-up</i> state today because the donated cookie
     session for <code>{domain}</code> is stale or was rejected
     by the site.</p>

  <p style="margin:16px 0 6px;font-weight:600;">Fix in one click
     (Apple Mail on your Mac):</p>
  <p style="margin:0 0 14px;">
    <a href="{cwcookie_url}"
       style="display:inline-block;padding:10px 18px;background:#111;
              color:#fff;text-decoration:none;border-radius:6px;
              font-weight:600;">
      Donate cookies for {domain}
    </a>
  </p>

  <p style="margin:16px 0 4px;font-weight:600;">Or copy-paste this into
     any Terminal on your Mac:</p>
  <pre style="background:#f4f4f5;border:1px solid #e4e4e7;
              border-radius:6px;padding:10px 12px;font-size:12.5px;
              overflow-x:auto;user-select:all;margin:0 0 14px;"
><code>{terminal_cmd}</code></pre>

  {reason_html}

  <details style="margin-top:22px;color:#555;font-size:12px;">
    <summary style="cursor:pointer;">First time? Enable the one-click
      button (one-time setup).</summary>
    <p style="margin:8px 0 4px;">Run this in Terminal once per Mac:</p>
    <pre style="background:#f4f4f5;border:1px solid #e4e4e7;
                border-radius:6px;padding:8px 10px;font-size:12px;
                overflow-x:auto;user-select:all;"
><code>{install_cmd}</code></pre>
    <p style="margin:6px 0 0;">After that, every future cookie-gap
      email's black button opens Terminal and starts the donation
      automatically. macOS will prompt "Allow Cookie Donate to open?"
      on the first click; click Allow.</p>
  </details>

  <p style="color:#888;font-size:11px;margin-top:22px;">
    Notification is deduped to one per (source, domain) per UTC day.
    The dashboard never surfaces operator text - users see a neutral
    'warming up' tile.
  </p>
</div>"""

    try:
        import boto3
        ses = boto3.client("ses", region_name=AWS_REGION)
        ses.send_email(
            Source=SOURCE_ADDR,
            Destination={"ToAddresses": to},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Text": {"Data": body_text},
                    "Html": {"Data": body_html},
                },
            },
        )
        logger.info("cookie_gap_notify: sent %r to %s", subject, to)
        _mark_notified_today(source, domain)
        return True
    except Exception as e:
        logger.warning("cookie_gap_notify: SES send failed: %s", e)
        return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 3:
        print("usage: python3 -m scripts.trends_scrapers.cookie_gap_notify "
              "<source> <domain> [reason]", file=sys.stderr)
        sys.exit(2)
    src, dom = sys.argv[1], sys.argv[2]
    reason = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""
    ok = notify_cookie_gap(src, dom, reason=reason)
    sys.exit(0 if ok else 1)
