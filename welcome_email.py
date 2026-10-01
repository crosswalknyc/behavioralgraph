"""Prometheus welcome email: login-page split (Option B).

Graphite header, Off-White form, olive button. Table-based, 600px,
Outlook-safe. From Prometheus. Used by admin create-user and by
seat-provision scripts.
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
        f"Hi {who},\n\n"
        "Your Crosswalk login is ready.\n\n"
        f"Login: {url}\n"
        f"Username: {username}\n"
        f"Password: {password}\n\n"
        "You can change the password after you sign in.\n\n"
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
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your Crosswalk login is ready.</title>
</head>
<body style="margin:0;padding:0;background:#D8D6CD;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#D8D6CD;">
  <tr>
    <td align="center" style="padding:24px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background:#E9E8E1;">
  <tr>
    <td style="background:#0C1618;padding:28px 40px 32px;font-family:Helvetica,Arial,sans-serif;">
      <img src="{logo}" alt="Crosswalk" width="148" height="22" style="display:block;border:0;">
      <div style="margin:22px 0 0;font-size:11px;letter-spacing:0.16em;text-transform:uppercase;color:#E9E8E1;font-weight:500;">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#C7F23E;margin-right:8px;vertical-align:middle;"></span>
        <span style="color:#9AA09B;margin-right:6px;">01</span> Account
      </div>
      <div style="margin:10px 0 0;font-size:28px;line-height:1.12;font-weight:800;letter-spacing:-0.02em;color:#E9E8E1;">Your Crosswalk login is ready.</div>
    </td>
  </tr>
  <tr>
    <td style="padding:32px 40px 40px;font-family:Helvetica,Arial,sans-serif;">
      <div style="width:40px;height:3px;background:#8E3FA8;border-radius:999px;"></div>
      <div style="margin:16px 0 0;font-size:15px;line-height:1.5;color:#5C6560;">Hi {who}. Your account is on the dashboard. Use these details to sign in.</div>
      <div style="margin:22px 0 0;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#888C89;font-weight:600;">Username</div>
      <div style="margin:6px 0 0;padding:12px 14px;background:#FFFFFF;border:1px solid rgba(59,61,56,0.32);border-radius:6px;font-size:15px;font-weight:700;color:#0C1618;">{user}</div>
      <div style="margin:14px 0 0;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#888C89;font-weight:600;">Password</div>
      <div style="margin:6px 0 0;padding:12px 14px;background:#FFFFFF;border:1px solid rgba(59,61,56,0.32);border-radius:6px;font-size:15px;font-weight:700;color:#0C1618;">{pw}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0 0;">
        <tr>
          <td style="background:#5E7E12;border-radius:6px;text-align:center;">
            <a href="{url}" style="display:block;padding:12px 16px;font-size:14px;font-weight:700;color:#E9E8E1;text-decoration:none;">Open Crosswalk</a>
          </td>
        </tr>
      </table>
      <div style="margin:20px 0 0;font-size:13px;line-height:1.5;color:#888C89;">You can change the password after you sign in.</div>
      <div style="margin:28px 0 0;padding-top:20px;border-top:1px solid #C9C6BA;font-size:13px;line-height:1.5;color:#5C6560;">
        Prometheus<br>Crosswalk
      </div>
      <div style="margin:16px 0 0;font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:#888C89;">Crosswalk / Behavioral Intelligence Engine</div>
    </td>
  </tr>
</table>
    </td>
  </tr>
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
