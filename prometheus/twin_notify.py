"""Team notices for Digital Twins IQ (Jenna 2026-11-05).

Jenna, verbatim: "everytime someone creates a digital twin group email
me/jessie/liz and anytime someone creates a pilot test email us when
it's created and email us the results and then email us each time the
open a new focus group room".

Four notices, one per event, to jenna@, jessie@ and liz@ (internal
workflow mail, so Liz stays on it; never the user-facing door, never the
end user):

  notify_twin_built     a twin registered (every path: from a profile,
                        from a named subject with or without the
                        audience pull, from a list of URLs; the Twins
                        tab, Prometheus, the API)
  notify_pilot_created  a Pilot Testing session opened (the setup form,
                        a saved setup run, a duplicate run, a recut, a
                        chat order, a key caller's screening)
  notify_pilot_results  a test stopped with its scorecard final (the
                        Stop button, the auto-stop at the runtime, a
                        live watch that finished scoring, a key
                        caller's screening): the headline read, the
                        card with its tallies and norm lines, the
                        characters, the favorite moment and the dead
                        spot, the forecast, the questions, the report
                        PDF attached (the deck too when it fits)
  notify_room_opened    a Focus Group IQ room opened (never a question)

Every notice is idempotent on its event id (the twin key, the session
id, the room id): a marker under
system/digital_twins/notified/<event>/<id>.json is claimed before the
send, so a retry, a second poll or a page refresh never mails twice.
Every notice is best-effort: nothing here ever raises into the product
action; a failure is printed and the action goes on. Probe identities
(canary, smoke, sandbox keys, golden graders) never mail. Nothing
created before NOTIFY_SINCE is ever mailed about (no backfill). The
results mail goes once, at the stop; a later norm change or a reconcile
after a room question does not send it again.

Plain English only: the copy names people, seats, rooms, twins and
tests; it never names our machinery.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Optional
from urllib.parse import quote

RECIPIENTS = ('jenna@crosswalknyc.com', 'jessie@crosswalknyc.com', 'liz@crosswalknyc.com')
FROM = 'Crosswalk <jenna@crosswalknyc.com>'       # the internal workflow sender (the same one the signup and billing notices use)
REGION = 'us-east-2'
BUCKET = os.environ.get('CORPUS_CATALOG_BUCKET', 'dashboard-inputs')
MARKER_PREFIX = 'system/digital_twins/notified/'
NOTIFY_SINCE = '2026-11-05T00:00:00Z'             # nothing created before this day is mailed about (no backfill)
DASHBOARD = 'https://dashboard.crosswalknyc.com'
ATTACH_LIMIT = 7_500_000                          # SES takes 10 MB raw; the deck rides only while the whole message stays under this
EVENTS = ('twin_built', 'pilot_created', 'pilot_results', 'room_opened')
VIA_WORDS = {'dashboard': 'the dashboard', 'chat': 'Prometheus chat', 'api': 'the API'}
# words that name our machinery; the templates never use them, and the audit in the tests holds the whole body to it
BANNED_RX = re.compile(r"\b(synth\w*|model\w*|panel\w*|sample\w*|pipeline\w*|queue\w*|worker\w*|claude|anthropic|hostmap|persona\w*|s3)\b", re.I)
_TAB_FOR_MODE = {'pilot': 'pilot', 'feature': 'pilot', 'episode': 'pilot', 'binge': 'pilot', 'trailer': 'trailer', 'spot': 'trailer',
                 'title': 'titles', 'key_art': 'titles', 'script': 'script', 'concept': 'concept'}
_MODE_WORD = {'pilot': 'Pilot test', 'feature': 'Screening', 'episode': 'Episode test', 'binge': 'Binge test', 'trailer': 'Trailer test', 'spot': 'Spot test',
              'title': 'Title test', 'key_art': 'Key art test', 'script': 'Script read', 'concept': 'Concept test'}

_SENDER: Optional[Callable] = None     # a test hook: set_sender(fn) replaces the SES send for every notice in the process
_USERS_CACHE = {'at': 0.0, 'doc': None}
_USERS_TTL_S = 600.0


# ----------------------------------------------------------------- hooks
def set_sender(fn: Optional[Callable]) -> None:
    """Route every notice through ``fn(subject=, body=, files=, to=)``
    (tests); None restores the SES send."""
    global _SENDER
    _SENDER = fn


def banned_tokens(text: str) -> list:
    """The machinery words in a text, if any (the audit the tests run).
    Links are addresses, not prose: a tab id inside one is not a word."""
    s = re.sub(r'https?://\S+', ' ', str(text or ''))
    hits = [m.group(0).lower() for m in BANNED_RX.finditer(s)]
    try:   # calibrated / anchored / as aired / a ratings service: never in a notice (Jenna 2026-11-05)
        from migration.digital_twin_copy import calibration_hits
        hits.extend(h.lower() for h in calibration_hits(s))
    except Exception:
        pass
    if '\u2014' in s:
        hits.append('em dash')
    if '\u2013' in s:
        hits.append('en dash')
    return hits


def via_for(user, default: str = 'chat') -> str:
    """'api' for a key caller, else ``default`` ('chat' for the chat
    lane, 'dashboard' for the page)."""
    try:
        if str((user or {}).get('_auth_via') or '').lower() == 'api_key':
            return 'api'
    except Exception:
        pass
    return default


# --------------------------------------------------------------- helpers
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _when(iso: str = '') -> str:
    """'Nov 5, 2026, 5:12 PM ET (22:12 UTC)' from an ISO stamp, now when blank."""
    try:
        dt = datetime.strptime(str(iso), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) if iso else datetime.now(timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    utc = dt.strftime('%H:%M UTC')
    try:
        from zoneinfo import ZoneInfo
        et = dt.astimezone(ZoneInfo('America/New_York'))
        return f"{et.strftime('%b')} {et.day}, {et.year}, {et.strftime('%I:%M %p').lstrip('0')} ET ({utc})"
    except Exception:
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}, {dt.strftime('%I:%M %p').lstrip('0')} UTC"


def _safe(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', str(s or '')).strip('_')[:180] or 'x'


def _since_ok(created_at) -> bool:
    """False for anything stamped before the day the notices began."""
    s = str(created_at or '').strip()
    if not s:
        return True
    return s >= NOTIFY_SINCE


def _int(v, default=0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _people(n) -> str:
    v = _int(n)
    return f"{v:,} people" if v > 0 else ''


def _pct(v) -> str:
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return ''


def _runtime_text(seconds) -> str:
    s = _int(seconds)
    if s <= 0:
        return ''
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _default_s3():
    """The process's store for the markers. A test or replay process
    (the flags mail_guard reads) gets None: its mail is shut anyway, and a
    marker for a test event must never land in the real store."""
    try:
        from prometheus.mail_guard import test_mode_reason
        if test_mode_reason():
            return None
    except Exception:
        pass
    try:
        from prometheus.host import host
        if host.has('s3_client'):
            return host.s3_client
    except Exception:
        pass
    try:
        app = sys.modules.get('app')
        if app is not None and getattr(app, 's3_client', None) is not None:
            return app.s3_client
    except Exception:
        pass
    try:
        import boto3
        return boto3.client('s3', region_name=REGION)
    except Exception:
        return None


def _users_doc(s3=None) -> dict:
    """users.json: the app's loader when this is the dashboard process,
    the stored copy otherwise. Cached ten minutes; {} when unreadable."""
    now = time.time()
    if _USERS_CACHE['doc'] is not None and now - float(_USERS_CACHE['at'] or 0) < _USERS_TTL_S:
        return _USERS_CACHE['doc']
    doc = None
    try:
        app = sys.modules.get('app')
        if app is not None and callable(getattr(app, 'load_users', None)):
            doc = app.load_users() or {}
    except Exception:
        doc = None
    if not isinstance(doc, dict):
        try:
            cli = s3 or _default_s3()
            doc = json.loads(cli.get_object(Bucket=BUCKET, Key='system/users.json')['Body'].read()) if cli is not None else {}
        except Exception:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    _USERS_CACHE.update({'at': now, 'doc': doc})
    return doc


def seat(username: str, s3=None) -> dict:
    """{username, name, company, email} for a seat, from users.json; the
    username stands in for a name that is not on file."""
    u = str(username or '').strip()
    rec = {}
    try:
        rec = ((_users_doc(s3).get('users') or {}).get(u)) or {}
    except Exception:
        rec = {}
    first = str(rec.get('first_name') or rec.get('firstName') or '').strip()
    last = str(rec.get('last_name') or rec.get('lastName') or '').strip().rstrip('`')
    name = f"{first} {last}".strip() or str(rec.get('name') or rec.get('display_name') or '').strip() or u or 'A user'
    return {'username': u, 'name': name, 'company': str(rec.get('company') or rec.get('company_name') or '').strip(), 'email': str(rec.get('email') or '').strip()}


def _who_tag(st: dict) -> str:
    """'(Jenna Menking, Crosswalk)' for a subject line; the company only when on file."""
    return f"({st['name']}, {st['company']})" if st.get('company') else f"({st['name']})"


def _who_line(st: dict) -> str:
    bits = st['name']
    if st.get('email'):
        bits += f" ({st['email']})"
    if st.get('company'):
        bits += f" at {st['company']}"
    return bits


def twin_link(key: str, tab: str = '') -> str:
    url = f"{DASHBOARD}/?view=digitalTwinsIQ&twin={quote(str(key or ''), safe='')}"
    return url + (f"&tab={tab}" if tab else '')


def _meta(payload) -> dict:
    return (payload or {}).get('meta') or {}


def _twin_names(payload: dict, sess: dict, s3=None) -> str:
    """The twin the test or room sits on, plus any other twins in it."""
    names = [str(_meta(payload).get('name') or _meta(payload).get('subject') or 'the twin')]
    mine = str(_meta(payload).get('key') or '')
    for tk in (sess or {}).get('twin_keys') or ((sess or {}).get('group') or {}).get('twins') or []:
        if not tk or tk == mine:
            continue
        label = ''
        for sg in ((sess or {}).get('group') or {}).get('segments') or []:
            if isinstance(sg, dict) and str(sg.get('twin') or '') == str(tk) and sg.get('twin_name'):
                label = str(sg['twin_name'])
                break
        if not label and s3 is not None:
            try:
                from migration.digital_twin_synthesis import load_twin
                other = load_twin(s3, str(tk))
                label = str(_meta(other).get('name') or '') if other else ''
            except Exception:
                label = ''
        if label and label not in names:
            names.append(label)
    return ', '.join(names)


# ------------------------------------------------------------ the markers
def marker_key(event: str, ident: str) -> str:
    return f"{MARKER_PREFIX}{event}/{_safe(ident)}.json"


def _exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        pass
    try:   # a store without head_object (a test double)
        s3.get_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def _claim(s3, event: str, ident: str, note: Optional[dict] = None) -> Optional[str]:
    """Write the marker unless it exists. The key when this call owns the
    event, None when another already did (or the store is missing)."""
    if s3 is None:
        return None
    key = marker_key(event, ident)
    if _exists(s3, key):
        return None
    body = json.dumps({'event': event, 'id': str(ident), 'claimed_at': _now_iso(), **(note or {})}, default=str).encode('utf-8')
    kwargs = {'Bucket': BUCKET, 'Key': key, 'Body': body, 'ContentType': 'application/json'}
    try:
        s3.put_object(IfNoneMatch='*', **kwargs)      # atomic where the store supports it: two processes, one send
        return key
    except Exception as e:
        name = type(e).__name__
        resp = getattr(e, 'response', None) or {}
        code = str((resp.get('Error') or {}).get('Code') or '')
        status = (resp.get('ResponseMetadata') or {}).get('HTTPStatusCode')
        if code in ('PreconditionFailed', '412') or status == 412:
            return None
        if name not in ('ParamValidationError', 'TypeError'):
            traceback.print_exc()
            return None
    try:
        s3.put_object(**kwargs)
        return key
    except Exception:
        traceback.print_exc()
        return None


def _release(s3, key: str) -> None:
    try:
        if s3 is not None and key:
            s3.delete_object(Bucket=BUCKET, Key=key)
    except Exception:
        pass


def _stamp(s3, key: str, extra: dict) -> None:
    try:
        if s3 is None or not key:
            return
        try:
            doc = json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read())
        except Exception:
            doc = {}
        doc.update(extra)
        s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(doc, default=str).encode('utf-8'), ContentType='application/json')
    except Exception:
        pass


# --------------------------------------------------------------- the send
def _ses_send(*, subject: str, body: str, files=(), to=RECIPIENTS) -> dict:
    """One raw SES message to the team, attachments included. A test or
    replay process never reaches SES."""
    rec = {'sent': False, 'to': list(to)}
    try:
        from prometheus.mail_guard import sending_allowed, suppressed_note, test_mode_reason
        if not sending_allowed():
            print(suppressed_note('twin notice', subject))
            rec['reason'] = f"test mode ({test_mode_reason() or 'probe ask'})"
            return rec
    except Exception:
        pass
    try:
        import boto3
        msg = MIMEMultipart('mixed')
        msg['Subject'] = str(subject or '').replace('\n', ' ')[:180]
        msg['From'] = FROM
        msg['To'] = ', '.join(to)
        msg.attach(MIMEText(str(body or ''), 'plain', 'utf-8'))
        for f in files or ():
            try:
                data, name, mime = (list(f) + ['application/octet-stream'])[:3]
            except Exception:
                continue
            if not data or not name:
                continue
            _main, _, sub = str(mime or 'application/octet-stream').partition('/')
            part = MIMEApplication(data, _subtype=sub or 'octet-stream')
            part.add_header('Content-Disposition', 'attachment', filename=str(name))
            msg.attach(part)
        r = boto3.client('ses', region_name=REGION).send_raw_email(Source=FROM, Destinations=list(to), RawMessage={'Data': msg.as_string()})
        rec.update({'sent': True, 'message_id': r.get('MessageId')})
        print(f"[twin-notify] sent to {len(to)}: {msg['Subject'][:80]}")
    except Exception as e:
        rec['reason'] = f"send failed: {e}"[:200]
        traceback.print_exc()
    return rec


def _notify(event: str, ident: str, *, username: str, created_at, build: Callable[[], dict], s3=None, send=None) -> dict:
    """The shared path: the probe gate, the no-backfill gate, the claim,
    the build, the send. Never raises. Returns the record."""
    rec = {'event': event, 'id': str(ident or ''), 'sent': False}
    try:
        uname = str(username or '').strip()
        s3 = s3 if s3 is not None else _default_s3()
        try:
            from prometheus.probe_identity import is_probe_user
            if is_probe_user(uname, s3=s3):
                rec['reason'] = 'probe identity'
                return rec
        except Exception:
            traceback.print_exc()
        if not _since_ok(created_at):
            rec['reason'] = 'created before the notices began'
            return rec
        if not ident:
            rec['reason'] = 'no event id'
            return rec
        if s3 is None:
            rec['reason'] = 'no store for the marker'
            return rec
        key = _claim(s3, event, ident, {'user': uname})
        if key is None:
            rec['reason'] = 'already sent'
            return rec
        try:
            msg = build() or {}
        except Exception as e:
            traceback.print_exc()
            _release(s3, key)
            rec['reason'] = f"could not write the notice: {e}"[:200]
            return rec
        subject, body, files = str(msg.get('subject') or ''), str(msg.get('body') or ''), list(msg.get('files') or [])
        try:   # the output guard: no calibrated / anchored / as aired / ratings-service sentence leaves in a notice (Jenna 2026-11-05)
            from migration.digital_twin_copy import strip_calibration_language
            subject, body = strip_calibration_language(subject), strip_calibration_language(body)
        except Exception:
            traceback.print_exc()
        bad = banned_tokens(subject + '\n' + body)
        if bad:
            print(f"[twin-notify] {event} {ident}: copy carries {sorted(set(bad))}; sending as written (a name the user chose)")
        sender = send or _SENDER or _ses_send
        out = sender(subject=subject, body=body, files=files, to=RECIPIENTS) or {}
        rec.update({'subject': subject, 'sent': bool(out.get('sent')), 'reason': out.get('reason', ''), 'message_id': out.get('message_id')})
        if rec['sent']:
            _stamp(s3, key, {'sent_at': _now_iso(), 'subject': subject[:200], 'message_id': out.get('message_id')})
        elif str(out.get('reason') or '').startswith('send failed'):
            _release(s3, key)       # a failed send is not a sent notice; the next event on this id tries again
        else:
            _stamp(s3, key, {'suppressed': str(out.get('reason') or '')[:120]})
        print(f"[twin-notify] {event} {ident}: {'sent' if rec['sent'] else rec.get('reason')}")
    except Exception as e:
        traceback.print_exc()
        rec['reason'] = f"notice failed: {e}"[:200]
    return rec


# -------------------------------------------------------------- 1. twins
def _twin_source_line(meta: dict, inputs: dict, s3=None) -> str:
    inputs = inputs or {}
    if inputs.get('from_list') or inputs.get('url_list_key'):
        n, fname = 0, ''
        try:
            if s3 is not None and inputs.get('url_list_key'):
                doc = json.loads(s3.get_object(Bucket=BUCKET, Key=str(inputs['url_list_key']))['Body'].read())
                n, fname = _int(doc.get('count')), str(doc.get('file_name') or '')
        except Exception:
            pass
        line = f"a list of {n:,} URLs" if n else 'a list of URLs'
        return line + (f" ({fname})" if fname else '') + ', the audience those links define'
    if str(meta.get('source') or '') == 'demographics' or meta.get('demographics_spec'):
        try:
            from migration.digital_twin_synthesis import demographic_name
            words = demographic_name(meta.get('demographics_spec') or {})
        except Exception:
            words = ''
        return 'the demographics picked' + (f": {words}" if words else '')
    prof = str(inputs.get('profile_name') or meta.get('profile_name') or '').strip()
    subject = str(inputs.get('subject') or meta.get('subject') or meta.get('name') or '').strip()
    if inputs.get('pull_audience'):
        return f"the subject {subject}, with its audience pulled first" + (f" ({prof})" if prof and prof.lower() != subject.lower() else '')
    if str(meta.get('source') or '') == 'profile' or inputs.get('profile_key') or meta.get('profile_key'):
        return f"the {prof or subject} profile"
    return f"the subject {subject}" if subject else 'the subject named'


def _twin_body(payload: dict, key: str, st: dict, inputs: dict, via: str, s3=None) -> str:
    m = _meta(payload)
    size = _int(m.get('size_us'))
    lines = [f"{_who_line(st)} built a digital twin.", '',
             f"Twin: {m.get('name') or m.get('subject')}",
             f"Built from: {_twin_source_line(m, inputs, s3)}"]
    if size > 0:
        lines.append(f"Audience: {size:,} people in the US")
    if m.get('window_start') or m.get('window_end'):
        label = str(m.get('window_label') or '').strip()
        lines.append(f"Window: {m.get('window_start') or '?'} to {m.get('window_end') or '?'}" + (f" ({label})" if label else ''))
    kind = str(m.get('subject_kind') or '').strip()
    if kind:
        lines.append(f"Kind: {kind}" + (f", {m.get('format')}" if m.get('format') else ''))
    if m.get('platform_home'):
        lines.append(f"Home: {m.get('platform_home')}")
    if m.get('panelists'):
        lines.append(f"Seats in a room: {_int(m.get('panelists'))}")
    ask = ' '.join(str((inputs or {}).get('ask_text') or '').split())
    if ask:
        lines.append(f"Asked: \"{ask[:240]}\"")
    lines.append(f"Asked through: {VIA_WORDS.get(via, VIA_WORDS['chat'])}")
    lines.append(f"Built: {_when(m.get('created_at'))}")
    lines.append('')
    lines.append(f"Open it: {twin_link(key)}")
    return '\n'.join(lines)


def notify_twin_built(payload: dict, key: str, *, username: str = '', inputs: Optional[dict] = None, via: str = '', s3=None, send=None) -> dict:
    """A twin registered. ``inputs`` is the job's inputs (how it was
    asked for); ``via`` falls back to inputs['via'], then 'chat'."""
    m = _meta(payload)
    uname = str(username or m.get('created_by') or '').strip()
    inputs = dict(inputs or {})
    how = str(via or inputs.get('via') or 'chat').lower()
    st = seat(uname, s3)
    return _notify('twin_built', str(key or ''), username=uname, created_at=m.get('created_at'),
                   build=lambda: {'subject': f"Digital twin built: {m.get('name') or m.get('subject')} {_who_tag(st)}",
                                  'body': _twin_body(payload, key, st, inputs, how, s3)},
                   s3=s3, send=send)


# ------------------------------------------------------------- 2. pilots
def _cut_line(sess: dict) -> str:
    media = sess.get('media') if isinstance(sess.get('media'), dict) else {}
    mode = str(sess.get('mode') or 'pilot')
    if media.get('s3_key') or media.get('filename'):
        return str(media.get('filename') or media.get('title') or 'a file')
    if media.get('url') or media.get('watch_url'):
        plat = str(media.get('platform_label') or '').strip()
        return str(media.get('url') or media.get('watch_url')) + (f" ({plat})" if plat else '')
    opts = [o for o in (sess.get('options') or []) if isinstance(o, dict)]
    if mode in ('title', 'key_art') and opts:
        labels = [str(o.get('label') or o.get('title') or '') for o in opts if (o.get('label') or o.get('title'))]
        return f"{len(opts)} options" + (f": {', '.join(labels[:6])}" if labels else '')
    if mode in ('script', 'concept') and str(sess.get('text') or '').strip():
        return f"{len(str(sess['text']).split()):,} words of text"
    if media.get('title') and media.get('source') == 'slate':
        return f"{media['title']} (no file yet)"
    return 'not added yet'


def _norm_line_for(sess: dict) -> str:
    """'all pilots' / 'procedural pilots': what the test is compared to."""
    try:
        from migration import digital_twin_norms as N
        sc = sess.get('scorecard') if isinstance(sess.get('scorecard'), dict) else {}
        ch = sc.get('norm_choice') if isinstance(sc.get('norm_choice'), dict) else None
        if not ch:
            fmt = str(sc.get('format') or sess.get('format') or sess.get('mode') or 'pilot')
            ch = N.norm_choice(fmt, str(sess.get('norm_genre') or sess.get('genre') or 'all'))
        lab = N.compared_label(ch)
        return lab[0].lower() + lab[1:] if lab else ''
    except Exception:
        g = str(sess.get('norm_genre') or sess.get('genre') or '').strip()
        return f"{g} {str(sess.get('mode') or 'pilot')}s" if g else ''


def _seats_line(sess: dict) -> str:
    g = sess.get('group') if isinstance(sess.get('group'), dict) else {}
    seats = _int(g.get('in_room')) or len(g.get('roster') or [])
    cohorts = [c for c in (sess.get('cohorts') or []) if isinstance(c, dict) and c.get('label')]
    if not cohorts:
        cohorts = [s for s in (g.get('segments') or []) if isinstance(s, dict) and s.get('label') and s.get('composition')]
    line = f"{seats}"
    people = _int(g.get('size_people'))
    if people:
        line += f", standing for {people:,} people"
    if cohorts:
        line += ' (' + '; '.join(f"{c['label']}, {_int(c.get('seats'))} seats" if _int(c.get('seats')) else str(c['label']) for c in cohorts[:12]) + ')'
    return line


def _names(items) -> list:
    out = []
    for c in items or []:
        s = str((c.get('title') or c.get('name') or c.get('label') or '') if isinstance(c, dict) else c).strip()
        if s:
            out.append(s)
    return out


def _mode_line(sess: dict) -> str:
    mode = str(sess.get('mode') or 'pilot')
    word = _MODE_WORD.get(mode, 'Pilot test')
    flags = []
    if sess.get('critics_room'):
        flags.append('critics room')
    if sess.get('rough_cut'):
        flags.append('rough cut')
    lin = sess.get('lineage') if isinstance(sess.get('lineage'), dict) else {}
    if lin.get('parent'):
        flags.append(f"{sess.get('cut_label') or lin.get('label') or 'a later cut'} of {lin.get('parent_title') or 'the earlier test'}")
    return word + (f" ({', '.join(flags)})" if flags else '')


def _pilot_body(payload: dict, sess: dict, st: dict, via: str, s3=None) -> str:
    media = sess.get('media') if isinstance(sess.get('media'), dict) else {}
    lines = [f"{_who_line(st)} created a {_MODE_WORD.get(str(sess.get('mode') or 'pilot'), 'Pilot test').lower()}.", '',
             f"Test: {sess.get('title') or 'Untitled'}",
             f"Twin: {_twin_names(payload, sess, s3)}",
             f"Mode: {_mode_line(sess)}",
             f"Seats: {_seats_line(sess)}"]
    rt = _runtime_text(media.get('runtime_s'))
    if rt:
        lines.append(f"Runtime: {rt}")
    lines.append(f"The cut: {_cut_line(sess)}")
    norm = _norm_line_for(sess)
    if norm:
        lines.append(f"Compared to: {norm}")
    cast = _names(sess.get('cast'))
    if cast:
        lines.append(f"Talent named: {', '.join(cast[:12])}")
    if str(sess.get('logline') or '').strip():
        lines.append(f"Logline: {' '.join(str(sess['logline']).split())[:240]}")
    lines.append(f"Created through: {VIA_WORDS.get(via, VIA_WORDS['dashboard'])}")
    lines.append(f"Created: {_when(sess.get('created_at'))}")
    lines.append('')
    lines.append(f"Open it: {twin_link(_meta(payload).get('key') or '', _TAB_FOR_MODE.get(str(sess.get('mode') or 'pilot'), 'pilot'))}")
    return '\n'.join(lines)


def notify_pilot_created(payload: dict, sess: dict, *, username: str = '', via: str = 'dashboard', s3=None, send=None) -> dict:
    """A Pilot Testing session opened (any mode, any door)."""
    uname = str(username or (sess or {}).get('created_by') or '').strip()
    st = seat(uname, s3)
    how = str(via or (sess or {}).get('via') or 'dashboard').lower()
    return _notify('pilot_created', str((sess or {}).get('id') or ''), username=uname, created_at=(sess or {}).get('created_at'),
                   build=lambda: {'subject': f"Pilot test created: {sess.get('title') or 'Untitled'} {_who_tag(st)}",
                                  'body': _pilot_body(payload, sess, st, how, s3)},
                   s3=s3, send=send)


# ------------------------------------------------------------ 3. results
def _scorecard_lines(sess: dict) -> list:
    """Each money measure with its share and seat tally, then the norm
    lines as the card prints them, then the denominator line."""
    sc = sess.get('scorecard') if isinstance(sess.get('scorecard'), dict) else {}
    head = ((sc.get('battery') or {}).get('headline')) or {}
    try:
        from migration.digital_twin_pilot import battery_tallies
        t = battery_tallies(sc.get('battery') or {})
    except Exception:
        t = {}
    out = []
    for label, key, metric in (('Would finish', 'finish_pct', 'finish'), ('Would watch again', 'tune_in_pct', 'tune_in'),
                               ('Total positive', 'total_positive_pct', 'total_positive'), ('Definitely recommend', 'definitely_recommend_pct', 'definitely_recommend'),
                               ('Excellent', 'excellent_pct', 'excellent')):
        if head.get(key) is None:
            continue
        tally = str(t.get(metric) or '')
        out.append(f"{label}: {_pct(head.get(key))}" + (f" ({tally})" if tally else ''))
    if not out and sc.get('overall_like') is not None:
        out.append(f"Dial average: {_pct(sc.get('overall_like'))}")
    norm_lines = [str(x) for x in (sc.get('norm_lines') or []) if str(x or '').strip()]
    if norm_lines:
        out.append('')
        out.append('As the card reads them:')
        out.extend(norm_lines[:8])
    if sc.get('denominator_line'):
        out.append(str(sc['denominator_line']))
    return out


def _character_lines(sess: dict) -> list:
    d = sess.get('diagnostics') if isinstance(sess.get('diagnostics'), dict) else {}
    ch = d.get('characters') if isinstance(d.get('characters'), dict) else {}
    rows = [r for r in (ch.get('characters') or []) if isinstance(r, dict) and r.get('name')]
    follow = {str(f.get('name')): f for f in (ch.get('follow') or []) if isinstance(f, dict) and f.get('name')}
    out = []
    for r in rows[:10]:
        bits = []
        if r.get('excellent_pct') is not None:
            bits.append(f"{_pct(r.get('excellent_pct'))} excellent")
        f = follow.get(str(r['name']))
        if f is not None and f.get('pct') is not None:
            bits.append(f"{_pct(f.get('pct'))} would follow into a second episode")
        if r.get('draw_seats'):
            bits.append(f"{_int(r.get('draw_seats'))} seats would come back for {r['name']}")
        role = str(r.get('role') or '').strip()
        out.append(f"{r['name']}" + (f" ({role})" if role else '') + (": " + ', '.join(bits) if bits else ''))
    for name, f in follow.items():
        if name not in {str(r.get('name')) for r in rows} and f.get('pct') is not None:
            out.append(f"{name}: {_pct(f.get('pct'))} would follow into a second episode")
    if ch.get('read'):
        out.append(' '.join(str(ch['read']).split())[:600])
    return out


def _forecast_lines(sess: dict) -> list:
    fc = sess.get('forecast') if isinstance(sess.get('forecast'), dict) else {}
    ep, se, su = fc.get('episode') or {}, fc.get('series') or {}, fc.get('subscriptions') or {}
    if not ep:
        return []
    out = []
    if ep.get('week_one_audience'):
        out.append(f"Week-one audience if released: {_int(ep.get('week_one_audience')):,} people"
                   + (f" (range {_int(ep.get('low')):,} to {_int(ep.get('high')):,})" if ep.get('low') and ep.get('high') else ''))
    if se.get('season_completion_pct') is not None:
        out.append(f"Would finish the season: {se.get('season_completion_pct')}%")
    if se.get('second_screen_pct') is not None:
        out.append(f"On a second screen: {se.get('second_screen_pct')}%")
    if se.get('season_average_audience') or se.get('finale_audience'):
        out.append(f"Season average {_int(se.get('season_average_audience')):,} people, finale {_int(se.get('finale_audience')):,}")
    plat = str(su.get('platform') or 'the service')
    if su.get('subscribe_pct_of_nonsubscribers') is not None:
        out.append(f"Would subscribe for it: {su.get('subscribe_pct_of_nonsubscribers')}% of people without {plat}"
                   + (f", about {_int(su.get('subscribe_people')):,} people" if su.get('subscribe_people') else ''))
    if su.get('reactivate_pct_of_dormant') is not None:
        out.append(f"Would come back for it: {su.get('reactivate_pct_of_dormant')}% of dormant {plat} accounts"
                   + (f", about {_int(su.get('reactivate_people')):,} people" if su.get('reactivate_people') else ''))
    # the recorded actual is audit, never a line in the notice (Jenna 2026-11-05): the forecast reads as our own
    return out


def _question_lines(sess: dict) -> list:
    qs = [q for q in ((sess or {}).get('questions') or []) if isinstance(q, dict) and str(q.get('q') or '').strip()]
    qs.sort(key=lambda q: str(q.get('asked_at') or q.get('started_at') or ''))
    out = []
    for i, q in enumerate(qs, 1):
        st = str(q.get('stage') or '').strip().lower()
        opts = q.get('options') or []
        rows = opts if (opts and isinstance(opts[0], dict) and 'pct' in opts[0]) else (q.get('distribution') or [])
        lead = None
        for r in rows:
            if not isinstance(r, dict) or not str(r.get('label') or '').strip():
                continue
            try:
                pct = float(r.get('pct') or 0)
            except (TypeError, ValueError):
                pct = 0.0
            if lead is None or pct > lead[1]:
                lead = (' '.join(str(r['label']).split()), pct, str(r.get('tally') or '').strip())
        qtext = ' '.join(str(q['q']).split())[:200]
        if st and st not in ('done',):
            out.append(f"{i}. \"{qtext}\" ({'did not finish' if st == 'failed' else 'still being answered'})")
        elif lead is not None:
            out.append(f"{i}. \"{qtext}\": {_pct(lead[1])} {lead[0]}" + (f" ({lead[2]})" if lead[2] else ''))
        else:
            read = ' '.join(str(q.get('read') or '').split())
            out.append(f"{i}. \"{qtext}\"" + (f": {read[:160]}" if read else ''))
    return out


def _portrait_fn(s3):
    try:
        from migration import digital_twin_portraits as PT
        return lambda h: PT.read_portrait(s3, h, PT.THUMB_SIZE)
    except Exception:
        return None


def _report_files(payload: dict, sess: dict, s3=None, with_deck: bool = True) -> list:
    """The report PDF (with the faces on the room page), and the deck when
    the whole message stays under the mail limit."""
    files = []
    try:
        from migration import digital_twin_report as R
        pf = _portrait_fn(s3)
        pdf = R.build_report_pdf(payload, sess, portrait_fn=pf)
        if pdf:
            files.append((pdf, R.report_filename(payload, sess, '.pdf'), 'application/pdf'))
        if with_deck:
            try:
                deck = R.build_report_deck(payload, sess, portrait_fn=pf)
                if deck and sum(len(f[0]) for f in files) + len(deck) <= ATTACH_LIMIT:
                    files.append((deck, R.report_filename(payload, sess, '.pptx'), 'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
            except Exception:
                traceback.print_exc()
    except Exception:
        traceback.print_exc()
    return files


def _results_body(payload: dict, sess: dict, st: dict, files: list, s3=None) -> str:
    sc = sess.get('scorecard') if isinstance(sess.get('scorecard'), dict) else {}
    try:
        from migration.digital_twin_pilot import headline_read
        hl = headline_read(sess) or {}
    except Exception:
        hl = {}
    mode_word = _MODE_WORD.get(str(sess.get('mode') or 'pilot'), 'Pilot test')
    how = 'stopped on its own at the runtime' if sess.get('auto_stopped_at') else 'was stopped'
    lines = [f"The {mode_word.lower()} \"{sess.get('title') or 'Untitled'}\" on the {_twin_names(payload, sess, s3)} twin {how}; the scorecard is final.", '',
             f"Run by: {_who_line(st)}",
             f"Mode: {_mode_line(sess)}",
             f"Seats: {_seats_line(sess)}"]
    rt = _runtime_text(((sess.get('media') or {}).get('runtime_s')))
    if rt:
        lines.append(f"Runtime: {rt}")
    lines.append(f"The cut: {_cut_line(sess)}")
    norm = _norm_line_for(sess)
    if norm:
        lines.append(f"Compared to: {norm}")
    lines.append(f"Stopped: {_when(sess.get('stopped_at'))}")
    lines.append('')
    if hl.get('header'):
        lines.append(f"Headline: {hl['header']}")
        for l in (hl.get('lines') or [])[:3]:
            lines.append(str(l))
        lines.append('')
    card = _scorecard_lines(sess)
    if card:
        lines.append('Scorecard')
        lines.extend(card)
        lines.append('')
    chars = _character_lines(sess)
    if chars:
        lines.append('Characters')
        lines.extend(chars)
        lines.append('')
    fav, dead = sc.get('favorite_moment') or {}, sc.get('dead_spot') or {}
    if fav.get('clock') or fav.get('line'):
        lines.append(f"Favorite moment at {fav.get('clock') or '?'}: {fav.get('line') or ''}".rstrip(': '))
    if dead.get('clock') or dead.get('line'):
        lines.append(f"Dead spot at {dead.get('clock') or '?'}: {dead.get('line') or ''}".rstrip(': '))
    if fav or dead:
        lines.append('')
    fc = _forecast_lines(sess)
    if fc:
        lines.append('Forecast')
        lines.extend(fc)
        lines.append('')
    qs = _question_lines(sess)
    lines.append(f"Questions asked: {len(qs)}")
    lines.extend(qs[:20])
    lines.append('')
    if files:
        lines.append('Attached: ' + ', '.join(f[1] for f in files))
    else:
        lines.append('The report is on the test page (Report PDF).')
    lines.append(f"Open it: {twin_link(_meta(payload).get('key') or '', _TAB_FOR_MODE.get(str(sess.get('mode') or 'pilot'), 'pilot'))}")
    return '\n'.join(lines)


def notify_pilot_results(payload: dict, sess: dict, *, username: str = '', s3=None, send=None, with_deck: bool = True) -> dict:
    """A test stopped with its scorecard final. Once per test: the stop
    is the event; a later norm change or reconcile does not send again."""
    sess = sess or {}
    uname = str(username or sess.get('created_by') or '').strip()
    if not sess.get('scorecard'):
        return {'event': 'pilot_results', 'id': str(sess.get('id') or ''), 'sent': False, 'reason': 'no scorecard'}
    st = seat(uname, s3)

    def build():
        files = _report_files(payload, sess, s3, with_deck=with_deck)
        return {'subject': f"Pilot test results: {sess.get('title') or 'Untitled'} {_who_tag(st)}",
                'body': _results_body(payload, sess, st, files, s3), 'files': files}
    return _notify('pilot_results', str(sess.get('id') or ''), username=uname, created_at=sess.get('created_at'), build=build, s3=s3, send=send)


def notify_pilot_results_later(payload: dict, sess: dict, **kw) -> None:
    """The results notice on a thread, so a Stop or a poll answers at
    once (the report PDF takes a moment to draw). Fire and forget."""
    try:
        threading.Thread(target=notify_pilot_results, args=(payload, sess), kwargs=kw, name=f"twin-notify-results-{sess.get('id')}", daemon=True).start()
    except Exception:
        traceback.print_exc()


# -------------------------------------------------------------- 4. rooms
def _room_title(sess: dict) -> str:
    """The room's name without the product's own 'Room: ' prefix (an
    unnamed room is titled 'Room: <twins>')."""
    t = ' '.join(str((sess or {}).get('title') or '').split())
    return re.sub(r'^room:\s*', '', t, flags=re.I).strip() or 'Room'


def _room_body(payload: dict, sess: dict, st: dict, via: str, s3=None) -> str:
    g = sess.get('group') if isinstance(sess.get('group'), dict) else {}
    lines = [f"{_who_line(st)} opened a Focus Group IQ room.", '',
             f"Room: {_room_title(sess)}",
             f"Twin: {_twin_names(payload, sess, s3)}"]
    segs = [s for s in (g.get('segments') or []) if isinstance(s, dict) and s.get('label')]
    if segs:
        parts = []
        for s in segs[:12]:
            bit = str(s['label'])
            if s.get('share_pct') is not None:
                bit += f" {float(s.get('share_pct') or 0):.0f}%"
            if _int(s.get('seats')):
                bit += f", {_int(s.get('seats'))} seats"
            parts.append(bit)
        lines.append('Segments: ' + '; '.join(parts))
    seats = _int(g.get('in_room')) or len(g.get('roster') or [])
    people = _int(g.get('size_people'))
    lines.append(f"Seats: {seats}" + (f", standing for {people:,} people" if people else ''))
    if sess.get('duplicated_from'):
        lines.append('Opened as a duplicate of an earlier room.')
    lines.append(f"Opened through: {VIA_WORDS.get(via, VIA_WORDS['dashboard'])}")
    lines.append(f"Opened: {_when(sess.get('created_at'))}")
    lines.append('')
    lines.append(f"Open it: {twin_link(_meta(payload).get('key') or '', 'panel')}")
    return '\n'.join(lines)


def notify_room_opened(payload: dict, sess: dict, *, username: str = '', via: str = 'dashboard', s3=None, send=None) -> dict:
    """A Focus Group IQ room opened. One notice per room, never per question."""
    uname = str(username or (sess or {}).get('created_by') or '').strip()
    st = seat(uname, s3)
    how = str(via or 'dashboard').lower()
    return _notify('room_opened', str((sess or {}).get('id') or ''), username=uname, created_at=(sess or {}).get('created_at'),
                   build=lambda: {'subject': f"Focus group room opened: {_room_title(sess)} {_who_tag(st)}",
                                  'body': _room_body(payload, sess, st, how, s3)},
                   s3=s3, send=send)


__all__ = ['RECIPIENTS', 'FROM', 'MARKER_PREFIX', 'NOTIFY_SINCE', 'EVENTS', 'set_sender', 'banned_tokens', 'via_for', 'seat', 'marker_key', 'twin_link',
           'notify_twin_built', 'notify_pilot_created', 'notify_pilot_results', 'notify_pilot_results_later', 'notify_room_opened']
