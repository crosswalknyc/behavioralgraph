#!/usr/bin/env python3
"""streamscout_notify.py — alert Crosswalk when a scoped platform can't be found.

The safeguard (Jess + Spike, 2026-10-16): when a client scopes a profile to a
platform and StreamScout finds NO matches there, we must NOT quietly fall back to
another platform's stored seeds (handing back Netflix viewers for a Hulu-only
ask). Instead:

  1. the build is refused (PlatformScopeError — see streamscout_query), and
  2. the client gets an honest message ("We can't see {title} on {platform} at
     this time. Notifying Crosswalk and will get back to you soon."), and
  3. Jenna + Jess get an email so a human picks it up.

This module is step 3. It reuses the repo's existing AWS SES path (same as
welcome_email / prometheus_watch_notify: boto3, region us-east-2, from
prometheus@crosswalknyc.com). It **never raises** into the query path — a mail
failure must not turn a clean refusal into a crash.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_FROM = "Prometheus <prometheus@crosswalknyc.com>"
_REPLY_TO = "jenna@crosswalknyc.com"
# Who gets the heads-up. Overridable via env (comma-separated) without a deploy.
_DEFAULT_TO = ("jenna@crosswalknyc.com", "jessie@crosswalknyc.com")


def alert_recipients():
    raw = os.environ.get("STREAMSCOUT_ALERT_TO", "")
    picked = [a.strip() for a in raw.split(",") if a.strip()]
    return picked or list(_DEFAULT_TO)


def _fmt_platforms(labels):
    labels = [str(x) for x in (labels or []) if str(x).strip()]
    if not labels:
        return "the requested platform"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return "%s and %s" % (labels[0], labels[1])
    return "%s, and %s" % (", ".join(labels[:-1]), labels[-1])


def _text(title, labels, client_message, context):
    ctx = context or {}
    when = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
    lines = [
        "StreamScout refused a platform-scoped build (no fallback).",
        "",
        "TITLE:     %s" % title,
        "PLATFORM:  %s" % _fmt_platforms(labels),
    ]
    for k in ("window", "client", "requested_by", "kind"):
        v = str(ctx.get(k) or "").strip()
        if v:
            lines.append("%-10s %s" % (k.upper() + ":", v))
    lines += [
        "WHEN:      %s" % when,
        "",
        "What the client was told:",
        "  %s" % client_message,
        "",
        "StreamScout found no current matches on the requested platform, so it "
        "did NOT substitute any other platform's seeds. Please confirm where "
        "the title is available and follow up with the client.",
        "",
        "StreamScout / Crosswalk",
    ]
    return "\n".join(lines)


def _html(title, labels, client_message, context):
    import html as _h
    ctx = context or {}
    when = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
    ink, body, muted, olive, off = (
        "#0C1618", "#5C6560", "#888C89", "#5E7E12", "#E9E8E1")

    def row(label, value):
        return (f"<div style='font-size:11px;letter-spacing:.08em;"
                f"text-transform:uppercase;color:{olive};font-weight:600;"
                f"margin:16px 0 4px'>{_h.escape(label)}</div>"
                f"<div style='background:#fff;border-radius:10px;padding:12px 14px;"
                f"color:{ink};font-size:15px;line-height:1.5'>"
                f"{_h.escape(str(value))}</div>")

    extra = ""
    for k in ("window", "client", "requested_by", "kind"):
        v = str(ctx.get(k) or "").strip()
        if v:
            extra += row(k.replace("_", " ").title(), v)

    return (
        f"<html><body style='margin:0;padding:0;background:{off};"
        f"font-family:Helvetica,Arial,sans-serif;color:{body}'>"
        f"<div style='max-width:640px;margin:0 auto;padding:32px 24px'>"
        f"<div style='font-size:11px;letter-spacing:.12em;text-transform:uppercase;"
        f"color:{olive};font-weight:600'>StreamScout · Crosswalk</div>"
        f"<h1 style='font-size:21px;color:{ink};font-weight:700;margin:10px 0 4px'>"
        f"Platform not found — build refused</h1>"
        f"<div style='color:{muted};font-size:13px'>{_h.escape(when)}</div>"
        + row("Title", title)
        + row("Requested platform", _fmt_platforms(labels))
        + extra
        + row("Client was told", client_message)
        + (f"<div style='margin:22px 0 0;color:{ink};font-size:14px;"
           f"line-height:1.55'>StreamScout found no current matches on the "
           f"requested platform, so it did <b>not</b> substitute any other "
           f"platform&rsquo;s seeds. Please confirm availability and follow up "
           f"with the client.</div>")
        + f"<div style='margin-top:26px;color:{ink}'>StreamScout<br>"
        f"<span style='color:{muted}'>Crosswalk</span></div>"
        f"</div></body></html>")


def send_scope_alert(title, missing_labels, client_message=None, context=None,
                     recipients=None):
    """Email Jenna + Jess that a scoped platform had no matches. Never raises;
    returns the SES MessageId on success or "" on any problem (so the caller can
    still refuse the build cleanly even if mail is down)."""
    try:
        import boto3  # repo already uses SES us-east-2 for all product mail
        to = recipients or alert_recipients()
        labels = list(missing_labels or [])
        cmsg = client_message or (
            "We can't see %s on %s at this time. Notifying Crosswalk and will "
            "get back to you soon." % (title, _fmt_platforms(labels)))
        subject = "StreamScout: %s not found on %s" % (
            title, _fmt_platforms(labels))
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject[:180]
        msg["From"] = _FROM
        msg["To"] = ", ".join(to)
        msg["Reply-To"] = _REPLY_TO
        msg.attach(MIMEText(_text(title, labels, cmsg, context), "plain", "utf-8"))
        msg.attach(MIMEText(_html(title, labels, cmsg, context), "html", "utf-8"))
        resp = boto3.client("ses", region_name="us-east-2").send_raw_email(
            Source=_FROM, Destinations=list(to),
            RawMessage={"Data": msg.as_string()})
        return resp.get("MessageId") or ""
    except Exception:  # noqa: BLE001 — a mail failure must never crash the query
        traceback.print_exc()
        return ""
