"""'Email me this file': a command, never a build (2026-10-12, Debi).

Debi had the Austin Sprinz profile open and typed "Email me this file".
It went to the build flow, which came back with an existing-match
offer. A reader asking for their own file by email is a command: send
it. What rides:

* with a profile open: that profile's CSV (and the Avid Fan cut when
  the library has one), from the file store;
* otherwise: the most recent read Prometheus delivered in the thread,
  as the body, with its CSV when the read carries one.

The mail goes through the house door (From Prometheus, Jenna and Liz
on BCC, vocabulary scrubbed, logged). The reader asked for it, so the
send is instructed by the reader; the caller label says so.
"""
from __future__ import annotations

import re

_SEND_RX = re.compile(
    r"^\W*(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?(?:e-?mail|send|mail|forward)\s+"
    r"(?:me\s+|us\s+)?(?:a copy of\s+)?(?:this|that|the|those|these)\s*"
    r"(?:profile|file|csv|read|report|answer|data|results?|numbers|analysis|output)?s?"
    r"(?:\s+(?:to me|to my (?:e-?mail|inbox)|over|please))?\W*$", re.I)
_SEND_PLAIN_RX = re.compile(r"^\W*(?:e-?mail|send)\s+(?:it|this|that)\s+(?:to\s+)?me\W*$", re.I)
# "email me the Babylon 5 breakdown", "send me the Nike profile" (2026-10-14: the
# first drafted a profile build; a send order names a thing we hold, never a build)
_SEND_NAMED_RX = re.compile(
    r"^\W*(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?(?:e-?mail|send|mail|forward)\s+(?:me\s+|us\s+)?(?:a copy of\s+)?"
    r"(?:the|that|this|those|these|your|my|our)?\s*(?P<subj>[A-Za-z0-9][^,.?!]{1,80}?)\s+"
    r"(?P<noun>profile|file|csv|breakdown|read|report|numbers|data|analysis|journey|deck|results|answer|summary|output|tracker|valuation|campaign)s?"
    r"(?:\s+(?:to me|to my (?:e-?mail|inbox)|over|please|you (?:built|made|did|ran)|from (?:yesterday|last week|earlier)))?\W*$", re.I)


def parse(text):
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 120:
        return False
    if _SEND_RX.match(t) or _SEND_PLAIN_RX.match(t):
        # "email me when it's ready" is the notify family, not this lane
        return not re.search(r"\bwhen\b", t, re.I)
    return bool(named_subject(t)) and not re.search(r"\bwhen\b", t, re.I)


def named_subject(text):
    """The subject a send order names ('Babylon 5' in 'email me the Babylon
    5 breakdown'), else ''."""
    m = _SEND_NAMED_RX.match(' '.join(str(text or '').split()))
    if not m:
        return ''
    subj = ' '.join(m.group('subj').split()).strip(' ,.:;-"\'')
    subj = re.sub(r"^(?:the|that|this|your|my|our|latest|last|new)\s+", '', subj, flags=re.I)
    if not subj or subj.lower() in ('it', 'this', 'that', 'same', 'latest', 'last'):
        return ''
    return subj


def _named_library_file(subject, host):
    """(display_name, s3_key) of the library profile a name points at."""
    try:
        from . import sizing_lane as _szl
        hit = _szl.resolve(subject, None)
        if hit and hit.get('name'):
            key = str(hit.get('s3_key') or hit.get('key') or '')
            if not key and host is not None and host.has('profile_catalog'):
                import re as _re
                norm = lambda x: _re.sub(r"[^a-z0-9]+", " ", str(x or '').lower()).strip()
                key = next((str(e.get('s3_key') or '') for e in (host.profile_catalog() or [])
                            if norm(e.get('display_name')) == norm(hit['name'])), '')
            return str(hit['name']), key
    except Exception as e:
        print(f"[send-file] library lookup failed for {subject!r}: {e}")
    return '', ''


def _fact_lines(anchors, limit=25):
    """The catalog's figures on a subject as plain lines (never the prompt
    block, which carries instructions meant for the model)."""
    out, seen = [], set()
    for f in (anchors or {}).get('facts') or []:
        if not isinstance(f, dict) or f.get('kind') not in ('audience_size', 'stage_count', 'conversion_pct', 'sample_size') \
                or str(f.get('product') or '') in ('chat', ''):      # dashboard figures only; chat-stated sentences are not a file
            continue
        label = str(f.get('label') or f.get('note') or f.get('kind') or '').replace('_', ' ').strip()[:90]
        v = f.get('value')
        try:
            val = f"{float(v):.1f}%" if f.get('kind') == 'conversion_pct' else f"{int(round(float(v))):,}"
        except (TypeError, ValueError):
            val = str(v or '').strip()
        if not val:
            continue
        w = f.get('window') if isinstance(f.get('window'), dict) else {}
        when = f" ({w.get('start')} to {w.get('end')})" if w.get('start') and w.get('end') else ''
        line = f"{label}: {val}{when}" if label else f"{val}{when}"
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _catalog_summary(subject):
    """Plain lines on what the catalog holds for a subject (a journey, a
    campaign, a valuation), for a send order that names no file we can
    attach."""
    try:
        from migration import corpus_catalog as cc
        from . import catalog_lane as _cl
        anchors = cc.anchors_for(subject, with_ledger=False)
        lines = _cl._summarize(anchors)
        return (anchors.get('subject') or subject), lines, anchors
    except Exception:
        return subject, [], {}


def _last_read(history):
    for t in reversed(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') != 'agent':
            continue
        txt = str(t.get('text') or '')
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if meta.get('kind') in ('read', 'answer') or meta.get('read_job_id') or len(txt) > 400:
            return t
    return None


def _clean_name(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s or '')).strip('_') or 'profile'


def answer(text, uname, *, user, ctx, history, host, send=None):
    """Payload for the chat, or None when nothing is in hand to send.
    ``send`` is injected by tests; defaults to the house mail door."""
    email = str((user or {}).get('email') or '').strip()
    if not email or '@' not in email:
        try:
            data = host.load_users()
            email = str(((data.get('users') or {}).get(uname) or {}).get('email') or '').strip()
        except Exception:
            email = ''
    if not email:
        return {'success': True, 'action': 'answer',
                'reply': 'I do not have an email address on your seat. Ask your admin to add one and I will send it.',
                'followups': [], 'offer_deck': False, 'deck_angle': None}
    primary = (ctx or {}).get('primary') if isinstance(ctx, dict) else None
    attachments = []   # (bytes, filename, mime)
    subject_name = ''
    body = ''
    named = named_subject(text)
    if named:
        # a named send: the library file by that name, else the catalog's
        # reads on it as a plain summary; never the open page, never a build
        lib_name, lib_key = _named_library_file(named, host)
        if lib_key:
            primary = {'name': lib_name, 's3_key': lib_key}
            ctx = dict(ctx or {}, primary=primary, cuts=[])
        else:
            disp, lines, anchors = _catalog_summary(named)
            if lines:
                nums = _fact_lines(anchors)
                body = (f"Here is what I hold on {disp}:\n\n" + '\n'.join('- ' + l for l in lines)
                        + (("\n\nThe numbers:\n" + '\n'.join('- ' + l for l in nums)) if nums else ''))
                subject_name = disp
                primary = None
            else:
                return {'success': True, 'action': 'answer',
                        'reply': (f"I do not have a file on {named}. Open the profile you mean and ask again, or tell me "
                                  f"to build one and I will price it."),
                        'followups': [], 'offer_deck': False, 'deck_angle': None, 'subject': named}
    if isinstance(primary, dict) and primary.get('s3_key'):
        key = str(primary.get('s3_key'))
        subject_name = str(primary.get('name') or key.rsplit('.', 1)[0]).strip()
        try:
            blob = host.s3_client.get_object(Bucket=host.bucket, Key=key)['Body'].read()
            attachments.append((blob, f"{_clean_name(subject_name)}_Profile.csv", 'text/csv'))
        except Exception as e:
            print(f"[send-file] profile fetch failed for {key}: {e}")
        # the Avid Fan cut rides along when the library has one for this subject
        try:
            for c in (ctx.get('cuts') or []):
                ck = str((c or {}).get('s3_key') or '')
                if ck and ck != key:
                    cb = host.s3_client.get_object(Bucket=host.bucket, Key=ck)['Body'].read()
                    attachments.append((cb, f"{_clean_name((c or {}).get('name') or ck.rsplit('.', 1)[0])}.csv", 'text/csv'))
        except Exception:
            pass
        body = (f"Here is the {subject_name} profile you asked for, attached as a CSV"
                + (" with its cut" + ('s' if len(attachments) > 2 else '') + " alongside" if len(attachments) > 1 else '')
                + ". It is also in your Select Profile dropdown.")
    read = _last_read(history) if not named else None
    if read is not None:
        rtxt = str(read.get('text') or '').strip()
        body = (body + "\n\nThe latest read from this thread:\n\n" + rtxt) if body else rtxt
        if not subject_name:
            meta = read.get('meta') if isinstance(read.get('meta'), dict) else {}
            subject_name = str(meta.get('subject') or read.get('subject') or '').strip() or 'your read'
    if not attachments and read is None and not body:
        return {'success': True, 'action': 'answer',
                'reply': ('Which file? Open the profile you want and ask again, or name it '
                          '("email me the Austin Sprinz profile") and I will send it.'),
                'followups': [], 'offer_deck': False, 'deck_angle': None}
    subj_line = f"{subject_name}: your Crosswalk file" if attachments else f"{subject_name}: your read from Prometheus"
    sender = send
    if sender is None:
        from . import outbound_mail as _om
        sender = _om.send_user_email
    first = attachments[0] if attachments else None
    rest = attachments[1:] if attachments else []
    r = sender(to=email, subject=subj_line, body="Hi,\n\n" + body + "\n\nPrometheus\nCrosswalk\n",
               instructed=True, caller='user:email_me_this_file',
               csv=(first[0] if first else None), csv_name=(first[1] if first else ''),
               files=rest)
    sent = bool((r or {}).get('sent'))
    what = ('the profile' + (' and its cut' if len(attachments) > 1 else '')) if attachments else ('the summary' if named else 'the read')
    reply = (f"Sent {what} to {email}." if sent
             else f"I could not send that just now ({str((r or {}).get('reason') or 'mail door closed')[:80]}). "
                  f"The file is still in your Select Profile dropdown.")
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [],
            'offer_deck': False, 'deck_angle': None, 'subject': subject_name, 'sent': sent}


__all__ = ['parse', 'answer', 'named_subject']
