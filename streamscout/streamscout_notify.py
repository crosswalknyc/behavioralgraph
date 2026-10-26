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
        # the product's mail guard: a test process never mails (2026-10-16)
        try:
            import os as _os, sys as _sys
            _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from prometheus.mail_guard import sending_allowed as _sending_allowed
            if not _sending_allowed():
                print("[streamscout] scope alert suppressed: test process")
                return ""
        except ImportError:
            pass
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


# ── ambiguous look-alike held for review ──────────────────────────────────────
# The companion to send_scope_alert. The look-alike guard (streamscout.
# verify_title_rows) HOLDS — never seeds, never silently drops — a resolver row
# whose SHOW is an ambiguous subset of the query: a short title sitting inside a
# longer, possibly different one ("House" -> "House of Cards", "Alien" ->
# "Alien: Covenant"). Pure tokens can't tell a right short match ("House" ->
# "House M.D.") from a wrong one, so a human confirms. This mails that queue.
def _fmt_flagged(flagged):
    """flagged: {platform_key: [SHOW, ...]} -> ['Max: House of Cards', ...]."""
    out = []
    for plat in sorted(flagged or {}):
        for show in flagged[plat]:
            out.append("%s: %s" % (plat, show))
    return out


def _review_text(title, flagged, context):
    ctx = context or {}
    when = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
    lines = [
        "StreamScout held an ambiguous look-alike for review (not seeded).",
        "",
        "QUERY:     %s" % title,
    ]
    for k in ("window", "client", "requested_by", "kind"):
        v = str(ctx.get(k) or "").strip()
        if v:
            lines.append("%-10s %s" % (k.upper() + ":", v))
    lines += ["WHEN:      %s" % when, "", "Held for review:"]
    lines += ["  - %s" % item for item in _fmt_flagged(flagged)]
    lines += [
        "",
        "Each of these is a SHORT query sitting inside a LONGER title, so "
        "StreamScout could not confirm it is the same show. It was NOT seeded "
        "(so no profile runs on the wrong title) and NOT dropped. Please "
        "confirm whether any is the title you meant, and seed it by hand if so.",
        "",
        "StreamScout / Crosswalk",
    ]
    return "\n".join(lines)


def _review_html(title, flagged, context):
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

    items = "".join(
        f"<li style='margin:4px 0'>{_h.escape(item)}</li>"
        for item in _fmt_flagged(flagged))
    held = (f"<div style='font-size:11px;letter-spacing:.08em;"
            f"text-transform:uppercase;color:{olive};font-weight:600;"
            f"margin:16px 0 4px'>Held for review</div>"
            f"<div style='background:#fff;border-radius:10px;padding:12px 14px;"
            f"color:{ink};font-size:15px;line-height:1.5'>"
            f"<ul style='margin:0;padding-left:18px'>{items}</ul></div>")

    return (
        f"<html><body style='margin:0;padding:0;background:{off};"
        f"font-family:Helvetica,Arial,sans-serif;color:{body}'>"
        f"<div style='max-width:640px;margin:0 auto;padding:32px 24px'>"
        f"<div style='font-size:11px;letter-spacing:.12em;text-transform:uppercase;"
        f"color:{olive};font-weight:600'>StreamScout · Crosswalk</div>"
        f"<h1 style='font-size:21px;color:{ink};font-weight:700;margin:10px 0 4px'>"
        f"Ambiguous match — held for review</h1>"
        f"<div style='color:{muted};font-size:13px'>{_h.escape(when)}</div>"
        + row("Query", title)
        + extra
        + held
        + (f"<div style='margin:22px 0 0;color:{ink};font-size:14px;"
           f"line-height:1.55'>Each is a <b>short</b> query sitting inside a "
           f"<b>longer</b> title, so StreamScout could not confirm it is the "
           f"same show. It was <b>not</b> seeded and <b>not</b> dropped. Please "
           f"confirm whether any is the title you meant.</div>")
        + f"<div style='margin-top:26px;color:{ink}'>StreamScout<br>"
        f"<span style='color:{muted}'>Crosswalk</span></div>"
        f"</div></body></html>")


# ── same-title variant held (US/UK, reboot, remake) ───────────────────────────
# A BARE query for a title that names more than one distinct work (The Office
# US/UK, Dune 1984/2021) is held — StreamScout won't guess which one. This mails
# Jenna + Jess to pick the version; the fix is to re-query the specific form.
def _variant_line(variants):
    vs = [str(v) for v in (variants or []) if str(v).strip()]
    return _fmt_platforms(vs) if vs else "more than one version"


def send_variant_alert(title, variants, context=None, recipients=None):
    """Email Jenna + Jess that a title naming >1 distinct work was held (not
    seeded) because the query didn't say which version. Never raises; returns
    the SES MessageId on success or "" on any problem."""
    try:
        try:
            import os as _os, sys as _sys
            _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from prometheus.mail_guard import sending_allowed as _sending_allowed
            if not _sending_allowed():
                print("[streamscout] variant alert suppressed: test process")
                return ""
        except ImportError:
            pass
        import boto3
        to = recipients or alert_recipients()
        vtext = _variant_line(variants)
        when = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p UTC")
        ctx = context or {}
        ctx_lines = ""
        for k in ("window", "client", "requested_by", "kind"):
            v = str(ctx.get(k) or "").strip()
            if v:
                ctx_lines += "%-10s %s\n" % (k.upper() + ":", v)
        text = (
            "StreamScout held a title that names more than one work (not "
            "seeded).\n\n"
            "QUERY:     %s\n"
            "VERSIONS:  %s\n"
            "%sWHEN:      %s\n\n"
            "'%s' maps to more than one distinct work, so StreamScout could not "
            "tell which you meant and did NOT seed any of them. Re-query the "
            "specific version (e.g. '%s (%s)') and it will resolve and seed "
            "normally.\n\nStreamScout / Crosswalk"
            % (title, vtext, ctx_lines, when, title, title,
               (list(variants or ["US"]) or ["US"])[0]))
        import html as _h
        ink, body, muted, olive, off = (
            "#0C1618", "#5C6560", "#888C89", "#5E7E12", "#E9E8E1")

        def row(label, value):
            return (f"<div style='font-size:11px;letter-spacing:.08em;"
                    f"text-transform:uppercase;color:{olive};font-weight:600;"
                    f"margin:16px 0 4px'>{_h.escape(label)}</div>"
                    f"<div style='background:#fff;border-radius:10px;"
                    f"padding:12px 14px;color:{ink};font-size:15px;"
                    f"line-height:1.5'>{_h.escape(str(value))}</div>")

        extra = ""
        for k in ("window", "client", "requested_by", "kind"):
            v = str(ctx.get(k) or "").strip()
            if v:
                extra += row(k.replace("_", " ").title(), v)
        example = "%s (%s)" % (title, (list(variants or ["US"]) or ["US"])[0])
        html_body = (
            f"<html><body style='margin:0;padding:0;background:{off};"
            f"font-family:Helvetica,Arial,sans-serif;color:{body}'>"
            f"<div style='max-width:640px;margin:0 auto;padding:32px 24px'>"
            f"<div style='font-size:11px;letter-spacing:.12em;"
            f"text-transform:uppercase;color:{olive};font-weight:600'>"
            f"StreamScout · Crosswalk</div>"
            f"<h1 style='font-size:21px;color:{ink};font-weight:700;"
            f"margin:10px 0 4px'>Which version? — held for review</h1>"
            f"<div style='color:{muted};font-size:13px'>{_h.escape(when)}</div>"
            + row("Query", title) + row("Versions that share this name", vtext)
            + extra
            + (f"<div style='margin:22px 0 0;color:{ink};font-size:14px;"
               f"line-height:1.55'><b>{_h.escape(title)}</b> names more than one "
               f"distinct work, so StreamScout did <b>not</b> seed any of them. "
               f"Re-query the specific version (e.g. "
               f"<b>{_h.escape(example)}</b>) and it resolves normally.</div>")
            + f"<div style='margin-top:26px;color:{ink}'>StreamScout<br>"
            f"<span style='color:{muted}'>Crosswalk</span></div>"
            f"</div></body></html>")
        subject = "StreamScout: %s — which version? (held)" % title
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject[:180]
        msg["From"] = _FROM
        msg["To"] = ", ".join(to)
        msg["Reply-To"] = _REPLY_TO
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        resp = boto3.client("ses", region_name="us-east-2").send_raw_email(
            Source=_FROM, Destinations=list(to),
            RawMessage={"Data": msg.as_string()})
        return resp.get("MessageId") or ""
    except Exception:  # noqa: BLE001 — a mail failure must never crash the query
        traceback.print_exc()
        return ""


def send_review_alert(title, flagged, context=None, recipients=None):
    """Email Jenna + Jess that an ambiguous look-alike was HELD for review (not
    seeded, not dropped). ``flagged`` is ``{platform_key: [SHOW, ...]}``. Never
    raises; returns the SES MessageId on success or "" on any problem."""
    try:
        if not flagged:
            return ""
        try:
            import os as _os, sys as _sys
            _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from prometheus.mail_guard import sending_allowed as _sending_allowed
            if not _sending_allowed():
                print("[streamscout] review alert suppressed: test process")
                return ""
        except ImportError:
            pass
        import boto3
        to = recipients or alert_recipients()
        subject = "StreamScout: %s — ambiguous match held for review" % title
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject[:180]
        msg["From"] = _FROM
        msg["To"] = ", ".join(to)
        msg["Reply-To"] = _REPLY_TO
        msg.attach(MIMEText(_review_text(title, flagged, context),
                            "plain", "utf-8"))
        msg.attach(MIMEText(_review_html(title, flagged, context),
                            "html", "utf-8"))
        resp = boto3.client("ses", region_name="us-east-2").send_raw_email(
            Source=_FROM, Destinations=list(to),
            RawMessage={"Data": msg.as_string()})
        return resp.get("MessageId") or ""
    except Exception:  # noqa: BLE001 — a mail failure must never crash the query
        traceback.print_exc()
        return ""
