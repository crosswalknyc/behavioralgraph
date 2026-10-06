"""Design requests go to the user experience team, never to a build.

Jenna 2026-10-06, after Alexia asked "In Section 4, the Clickstream, it
is showing data for ARROW the series, please remove." and Prometheus
offered to run a profile on "Clickstream": "for something like that
where it is a design choice say you cannot make design choices but will
email the user experience team then email me and jessie."

The lane is deterministic: a change-the-page ask (remove, hide, move,
rename, resize, recolor, add a column or tab, "it is showing X, please
remove") about a part of the dashboard gets one fixed reply and one
email to the user experience team (Jenna + Jessie) carrying the user,
the ask verbatim, and the open view. No model call, no build offer, no
charge.
"""
import os
import re
import traceback
from datetime import datetime, timezone

UX_TEAM = ('jenna@crosswalknyc.com', 'jessie@crosswalknyc.com')
FROM = 'Prometheus <prometheus@crosswalknyc.com>'

REPLY = ("I cannot make design changes to the dashboard myself. I have passed "
         "your note to the user experience team, and they will take it from "
         "here. Nothing is needed from you.")

_CHANGE_RX = re.compile(
    r"\b(remove|delete|hide|take (?:it|this|that|them) (?:out|off|down)|take out|get rid of|"
    r"move|swap|reorder|re-order|rename|relabel|re-label|resize|shrink|enlarge|"
    r"change (?:the )?(?:colou?r|font|label|name|title|order|layout|wording|size|icon|logo)|"
    r"make (?:it|this|that|the \w+(?: \w+)?) (?:bigger|smaller|larger|wider|taller|shorter|bold|clearer)|"
    r"add (?:a |an |another )?(?:column|tab|section|button|filter|toggle|chart|row|legend|tooltip|export)|"
    r"drop the|replace the|fix the (?:layout|spacing|alignment|typo|overlap|label|header)|"
    r"should(?: not|n't)? (?:show|display|say|read|appear|be (?:shown|visible|there))|"
    r"please (?:remove|fix|change|move|hide|update|correct) the|can you (?:remove|hide|move|change|rename|fix) the)\b",
    re.I)
_UI_RX = re.compile(
    r"\b(section ?\d*|tab|column|chart|table|page|view|card|header|heading|title|label|dropdown|"
    r"button|layout|design|display|tile|panel|legend|axis|font|colou?r|graph|row|screen|dashboard|"
    r"modal|tooltip|footer|sidebar|nest|menu|icon|logo|export|it is showing|is showing|"
    r"showing data for|shows data for)\b", re.I)
_DATA_QUESTION_RX = re.compile(
    r"^\s*(how many|how much|what is|what's|what are|who |which |when |why |where |does |do |is |are |can i see|show me)",
    re.I)


_DOCUMENT_RX = re.compile(r"\b(slides?|deck|powerpoint|pptx|presentation|pdf|docx|word (?:file|doc)|spreadsheet|xlsx)\b", re.I)


def is_design_request(text):
    """A change-the-page ask about a part of the dashboard. An ask about
    a slide, deck, PDF or document is a document ask, not a design one."""
    t = str(text or '').strip()
    if not t or len(t) > 600:
        return False
    if _DOCUMENT_RX.search(t):
        return False
    if _DATA_QUESTION_RX.match(t) and not re.search(r"\b(remove|hide|move|rename|resize)\b", t, re.I):
        return False
    return bool(_CHANGE_RX.search(t) and _UI_RX.search(t))


def _view_summary(ctx):
    if not isinstance(ctx, dict):
        return ''
    bits = []
    for k in ('view', 'view_label', 'product', 'tab', 'title', 'subject', 'profile', 'run_name',
              'project_name', 'journey', 'page'):
        v = ctx.get(k)
        if isinstance(v, str) and v.strip():
            bits.append(f"{k}: {v.strip()[:120]}")
    return '; '.join(bits[:6])


def notify_ux_team(user, text, ctx=None, *, send=None):
    """One email to the user experience team. Fail-safe, never raises.
    `send` is injectable for tests; PM_DESIGN_REQUEST_EMAIL=0 disables."""
    if os.environ.get('PM_DESIGN_REQUEST_EMAIL', '1').strip() in ('0', 'false', 'no'):
        return {'sent': False, 'reason': 'disabled'}
    uname = str((user or {}).get('username') or user or '').strip() if not isinstance(user, str) else user
    email = str((user or {}).get('email') or '').strip() if isinstance(user, dict) else ''
    when = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    where = _view_summary(ctx) or 'not captured'
    subject = f"Design request from {uname or 'a dashboard user'}: {str(text or '').strip()[:70]}"
    body = ("Design request from the Prometheus chat.\n\n"
            f"User: {uname or 'unknown'}{(' (' + email + ')') if email else ''}\n"
            f"When: {when}\n"
            f"Where: {where}\n\n"
            f"The ask, verbatim:\n  {str(text or '').strip()}\n\n"
            "What they were told: Prometheus cannot make design changes and has passed this to the user experience team.\n")
    try:
        if send is None:
            import boto3
            from email.mime.text import MIMEText
            msg = MIMEText(body, 'plain', 'utf-8')
            msg['Subject'] = subject.replace('\n', ' ')
            msg['From'] = FROM
            msg['To'] = ', '.join(UX_TEAM)
            r = boto3.client('ses', region_name='us-east-2').send_raw_email(
                Source='prometheus@crosswalknyc.com', Destinations=list(UX_TEAM),
                RawMessage={'Data': msg.as_string()})
            return {'sent': True, 'message_id': r.get('MessageId'), 'to': list(UX_TEAM)}
        return send(subject, body, list(UX_TEAM))
    except Exception as e:
        traceback.print_exc()
        return {'sent': False, 'reason': str(e)[:200]}


def answer(text, user=None, ctx=None, *, send=None):
    """The lane: None when the ask is not a design request; otherwise the
    raw analyze payload, with the user experience team emailed."""
    if not is_design_request(text):
        return None
    note = notify_ux_team(user, text, ctx, send=send)
    return {'success': True, 'action': 'answer', 'reply': REPLY, 'followups': [],
            'offer_deck': False, 'deck_angle': None, 'design_request': True,
            'ux_notified': bool((note or {}).get('sent'))}
