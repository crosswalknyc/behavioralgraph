"""Every finished Digital Journey is delivered by email with its detailed
breakdown attached (Jenna, 2026-10-13, after Carolyn Bisson at WBD held
only the Babylon 5 file: "it should always send this kind of raw data").

The email is the spotlight card the welcome emails use, from Prometheus
through the house door (Jenna and Liz on BCC, never a Cc). The
attachment is the Babylon 5 shape: one row per step, path and detour
with the count, the share kept, the drop, the share of US, and where /
on what surface / when. The same function serves an operator send for a
journey already on the shelf (the CLI below), with the sentence the
admin asked for as the lead line.

usage (operator):
  python3 -m prometheus.journey_delivery --key journey-iq/<user>/<name>.json.gz \
      --user <username> [--intro "Admin requested ..."] [--bcc a@x,b@y] [--dry-run]
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import sys
import traceback

DASHBOARD_URL_DEFAULT = "https://dashboard.crosswalknyc.com"
JOURNEY_VIEW = "journeyIQ"
JESSIE = "jessie@crosswalknyc.com"


def _jiq():
    try:
        from migration import journey_iq as jiq   # web app layout
    except ImportError:
        import importlib.util
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            'journey_iq', os.path.join(here, 'migration', 'journey_iq.py'))
        jiq = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jiq)
    return jiq


def dashboard_url() -> str:
    app_url = (os.environ.get("APP_URL") or "").rstrip("/")
    base = app_url if app_url and "onrender.com" not in app_url else DASHBOARD_URL_DEFAULT
    return f"{base}/?view={JOURNEY_VIEW}"


def load_payload(s3_client, key: str, bucket: str = 'dashboard-inputs') -> dict:
    raw = s3_client.get_object(Bucket=bucket, Key=key)['Body'].read()
    try:
        raw = gzip.decompress(raw)
    except Exception:
        pass
    return json.loads(raw.decode('utf-8'))


def user_record(username: str, *, host=None, s3_client=None, bucket: str = 'dashboard-inputs') -> dict:
    """The seat record (email, first name) for a username. Host first,
    S3 users.json otherwise. Never raises."""
    try:
        if host is not None and hasattr(host, 'load_users'):
            data = host.load_users() or {}
            rec = (data.get('users') or {}).get(username)
            if rec:
                return rec
    except Exception:
        pass
    try:
        if s3_client is not None:
            data = json.loads(s3_client.get_object(Bucket=bucket, Key='system/users.json')['Body'].read())
            return (data.get('users') or {}).get(username) or {}
    except Exception:
        pass
    return {}


def first_name(rec: dict, username: str = '') -> str:
    for k in ('first_name', 'firstName', 'name', 'display_name'):
        v = str((rec or {}).get(k) or '').strip()
        if v:
            return v.split()[0]
    return ''


def _fmt(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return str(n or '')


def spine_summary(payload: dict) -> dict:
    """Start, end and conversion of the journey's spine (TAM row skipped)."""
    j = (payload or {}).get('fragrance_shop_journey') or {}
    spine = [s for s in (j.get('spine') or []) if isinstance(s, dict) and s.get('id') != 'tam']
    if not spine:
        return {'start': 0, 'end': 0, 'conversion': 0.0, 'start_label': '', 'end_label': ''}
    start = int(round(float(spine[0].get('accounts') or 0)))
    end = int(round(float(spine[-1].get('accounts') or 0)))
    return {'start': start, 'end': end,
            'conversion': (end / start * 100.0) if start else 0.0,
            'start_label': str(spine[0].get('label') or ''),
            'end_label': str(spine[-1].get('label') or '')}


def where_line(payload: dict) -> str:
    jiq = _jiq()
    meta = (payload or {}).get('meta') or {}
    shelf = jiq.SHELF_LABELS.get(jiq.shelf_for(meta), '')
    name = str(meta.get('project_name') or 'your journey')
    if shelf:
        return f"Find it in the dashboard under Digital Journey IQ, {shelf}: {name}."
    return f"Find it in the dashboard under Digital Journey IQ: {name}."


def _window_pretty(meta: dict) -> str:
    from datetime import date

    def _p(d):
        try:
            y, m, dd = (int(x) for x in str(d).split('-'))
            return date(y, m, dd).strftime('%b %-d, %Y')
        except Exception:
            return str(d or '')
    s, e = meta.get('start_date'), meta.get('end_date')
    return f"{_p(s)} to {_p(e)}" if s and e else str(meta.get('window_label') or '')


def _short_label(label: str, fallback: str) -> str:
    """A tracked label has room for a few words: the step's plain name."""
    low = str(label or '').lower()
    if not low.strip():
        return fallback
    if 'social' in low and ('view' in low or 'saw' in low or 'post' in low) and 'watch' not in low:
        return 'Saw a social post'
    if 'watch' in low or 'played' in low or 'stream' in low:
        return 'Watched within 30 days' if '30 day' in low else 'Watched an episode' if 'episode' in low else 'Watched'
    if 'ticket' in low:
        return 'Reached a ticketing site'
    if 'bought' in low or 'purchas' in low or 'rented' in low:
        return 'Bought or rented'
    if 'subscri' in low or 'signed up' in low:
        return 'Subscribed'
    words, out = str(label).split(), []
    for w in words:
        if len(' '.join(out + [w])) > 34:
            break
        out.append(w)
    return ' '.join(out) or fallback


def compose(payload: dict, *, who: str = '', intro: str = '', ready_message: str = '') -> dict:
    """Subject, plain body and HTML for a journey delivery."""
    import welcome_email as _we
    meta = (payload or {}).get('meta') or {}
    name = str(meta.get('project_name') or 'Your Digital Journey')
    summ = spine_summary(payload)
    window = _window_pretty(meta)
    where = where_line(payload)
    greet = f"Hi {who}," if who else "Hi,"
    lead = (intro or '').strip() or (ready_message or '').strip() or \
        f"Your Digital Journey for {name} is ready."
    facts = []
    if summ['start']:
        facts.append((_short_label(summ['start_label'], 'Start of the journey'), _fmt(summ['start'])))
    if summ['end']:
        facts.append((_short_label(summ['end_label'], 'End of the journey'), _fmt(summ['end'])))
    if summ['start'] and summ['end']:
        facts.append(('Conversion', f"{summ['conversion']:.1f}%"))
    attach_line = ("The detailed breakdown is attached: every step, path and detour "
                   "with its count, the share kept of the step before, and where and when it happened.")
    figures = ''
    if summ['start'] and summ['end']:
        s_lab = summ['start_label'][:1].lower() + summ['start_label'][1:]
        e_lab = summ['end_label'][:1].lower() + summ['end_label'][1:]
        figures = (f"{_fmt(summ['start'])} people {s_lab}; {_fmt(summ['end'])} {e_lab} "
                   f"({summ['conversion']:.1f}%).")
    paragraphs = [greet, lead,
                  (f"{name}. {window}." if window else f"{name}.") + (f" {figures}" if figures else ''),
                  where, attach_line, f"Open it: {dashboard_url()}", "Prometheus\nCrosswalk"]
    body = "\n\n".join(p for p in paragraphs if p)
    headline = 'Your journey is ready.' if not intro else 'Your detailed breakdown.'
    html = _we.render_card_html(
        headline, lead, facts=facts, button=('Open Digital Journey IQ', dashboard_url()),
        lines=[f"{name}. {window}." if window else name, where, attach_line],
        title=f"{name}: Digital Journey")
    subject = f"{name}: your Digital Journey is ready" if not intro else f"{name}: the detailed breakdown"
    return {'subject': subject, 'body': body, 'html': html}


def deliver_journey(*, username: str, payload: dict, s3_key: str = '', s3_client=None, host=None,
                    intro: str = '', ready_message: str = '', extra_bcc=(), caller: str = 'journey_ready',
                    to: str = '', dry_run: bool = False, send=None) -> dict:
    """Email the journey's requester with the breakdown attached. Returns
    the door's record ({sent, reason, ...}). Never raises."""
    try:
        jiq = _jiq()
        rec = user_record(username, host=host, s3_client=s3_client)
        email = (to or str(rec.get('email') or '')).strip()
        if not email or '@' not in email:
            print(f"[journey-delivery] no email on the seat for {username!r}; nothing sent")
            return {'sent': False, 'reason': 'no email on seat'}
        parts = compose(payload, who=first_name(rec, username), intro=intro, ready_message=ready_message)
        xlsx = jiq.breakdown_xlsx(payload)
        xname = jiq.breakdown_filename(payload, 'xlsx')
        csv_text = jiq.breakdown_csv(payload)
        cname = jiq.breakdown_filename(payload, 'csv')
        if send is None:
            from prometheus.outbound_mail import send_user_email as send
        return send(to=email, subject=parts['subject'], body=parts['body'], html=parts['html'],
                    instructed=True, caller=caller, csv=csv_text.encode('utf-8'), csv_name=cname,
                    files=[(xlsx, xname, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')],
                    extra_bcc=tuple(extra_bcc or ()), dry_run=dry_run)
    except Exception as e:
        traceback.print_exc()
        return {'sent': False, 'reason': f"delivery failed: {e}"[:200]}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('usage')[0].strip())
    ap.add_argument('--key', required=True, help='journey-iq/<user>/<name>.json.gz')
    ap.add_argument('--user', required=True)
    ap.add_argument('--intro', default='', help='the lead sentence the admin asked for')
    ap.add_argument('--bcc', default='', help='extra BCC, comma separated (Jenna and Liz ride always)')
    ap.add_argument('--to', default='', help='override the seat email (operator use)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3')
    payload = load_payload(s3, a.key)
    extra = tuple(x.strip() for x in a.bcc.split(',') if x.strip())
    r = deliver_journey(username=a.user, payload=payload, s3_key=a.key, s3_client=s3, intro=a.intro,
                        extra_bcc=extra, caller='journey_delivery_cli', to=a.to, dry_run=a.dry_run)
    print(json.dumps({k: v for k, v in r.items() if k != 'html'}, default=str))
    return 0 if r.get('sent') or a.dry_run else 1


if __name__ == '__main__':
    sys.exit(main())
