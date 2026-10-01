"""Prometheus welcome email: spotlight (2026-10-01, Jenna).

Near-black card, one amethyst and orchid light behind the headline,
tiny tracked labels over large values, one Signal Green pill. Centred.
Table-based, 600px, Outlook-safe. From Prometheus. Used by admin
create-user and by seat-provision scripts.

The glow rides as a background image with bgcolor behind it, so a
client that drops background images still gets the right ground.
"""
from __future__ import annotations

import html
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

LOGIN_URL_DEFAULT = "https://dashboard.crosswalknyc.com/login"
LOGO_URL = (
    "https://dashboard.crosswalknyc.com/static/"
    "crosswalk-logo-white-transparent.png"
)
# The spotlight behind the headline. Hosted, never inlined: data URIs
# are stripped by a good share of mail clients.
GLOW_URL = (
    "https://dashboard.crosswalknyc.com/static/welcome-spotlight.png"
)
FROM_ADDR = "Prometheus <prometheus@crosswalknyc.com>"
REPLY_TO = "jenna@crosswalknyc.com"
BCC = "jenna@crosswalknyc.com"
SUBJECT = "Your Crosswalk login is ready."


def _login_url() -> str:
    app_url = (os.environ.get("APP_URL") or "").rstrip("/")
    if app_url and "onrender.com" not in app_url:
        return f"{app_url}/login"
    return LOGIN_URL_DEFAULT


def render_welcome_text(first_name: str, username: str, password: str,
                        login_url: str | None = None) -> str:
    who = (first_name or "").strip() or "there"
    url = login_url or _login_url()
    return (
        "You're in.\n\n"
        f"Hi {who}. Your workspace is open.\n\n"
        f"Username: {username}\n"
        f"Password: {password}\n\n"
        f"Sign in: {url}\n\n"
        "You can change the password once you are in.\n\n"
        "Prometheus\n"
        "Crosswalk\n"
    )


def render_welcome_html(first_name: str, username: str, password: str,
                        login_url: str | None = None) -> str:
    who = html.escape((first_name or "").strip() or "there")
    user = html.escape(username or "")
    pw = html.escape(password or "")
    url = html.escape(login_url or _login_url(), quote=True)
    logo = html.escape(LOGO_URL, quote=True)
    glow = html.escape(GLOW_URL, quote=True)
    f = "Helvetica,Arial,sans-serif"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your Crosswalk login is ready.</title>
</head>
<body style="margin:0;padding:0;background:#060B0C;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#060B0C;">
  <tr><td align="center" style="padding:34px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="width:600px;max-width:600px;">
  <tr>
    <td align="center" background="{glow}" bgcolor="#080E10"
        style="padding:44px 44px 48px;font-family:{f};background-color:#080E10;
               background-image:url({glow});background-size:cover;
               background-position:center;">

      <img src="{logo}" alt="Crosswalk" width="124" height="18"
           style="display:block;border:0;margin:0 auto;">

      <div style="height:96px;line-height:96px;font-size:0;">&nbsp;</div>

      <div style="font-size:52px;line-height:0.94;font-weight:900;
                  letter-spacing:-0.045em;color:#E9E8E1;text-align:center;">
        You&rsquo;re in.</div>
      <div style="margin:16px 0 0;font-size:14px;color:#9AA09B;text-align:center;">
        Hi {who}. Your workspace is open.</div>

      <div style="height:54px;line-height:54px;font-size:0;">&nbsp;</div>

      <div style="font-size:9px;font-weight:700;letter-spacing:0.22em;
                  text-transform:uppercase;color:#6F7A7D;text-align:center;">Username</div>
      <div style="margin:7px 0 0;font-size:21px;font-weight:800;
                  letter-spacing:-0.02em;color:#E9E8E1;text-align:center;">{user}</div>

      <div style="height:22px;line-height:22px;font-size:0;">&nbsp;</div>

      <div style="font-size:9px;font-weight:700;letter-spacing:0.22em;
                  text-transform:uppercase;color:#6F7A7D;text-align:center;">Password</div>
      <div style="margin:7px 0 0;font-size:21px;font-weight:800;
                  letter-spacing:-0.02em;color:#E9E8E1;text-align:center;">{pw}</div>

      <div style="height:38px;line-height:38px;font-size:0;">&nbsp;</div>

      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             align="center" style="margin:0 auto;">
        <tr><td style="background:#C7F23E;border-radius:999px;">
          <a href="{url}" style="display:block;padding:14px 38px;font-size:13px;
             font-weight:800;letter-spacing:0.02em;color:#0C1618;
             text-decoration:none;">Sign in</a>
        </td></tr>
      </table>

      <div style="margin:18px 0 0;font-size:12px;line-height:1.55;color:#6F7A7D;
                  text-align:center;">
        You can change the password once you are in.</div>

      <div style="height:72px;line-height:72px;font-size:0;">&nbsp;</div>

      <div style="font-size:11px;line-height:1.6;color:#6F7A7D;text-align:center;">
        Prometheus<br>Crosswalk</div>

    </td>
  </tr>
</table>
  </td></tr>
</table>
</body>
</html>"""


def send_welcome(email: str, first_name: str, username: str, password: str,
                 login_url: str | None = None) -> str:
    """Send via SES us-east-2. Returns MessageId. Raises on failure."""
    import boto3

    url = login_url or _login_url()
    text = render_welcome_text(first_name, username, password, url)
    html_body = render_welcome_html(first_name, username, password, url)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = SUBJECT
    msg["From"] = FROM_ADDR
    msg["To"] = email
    msg["Reply-To"] = REPLY_TO
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    ses = boto3.client("ses", region_name="us-east-2")
    resp = ses.send_raw_email(
        Source=FROM_ADDR,
        Destinations=[email, BCC],
        RawMessage={"Data": msg.as_string()},
    )
    return resp.get("MessageId") or ""
