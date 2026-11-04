"""The very last thing that happened in the chat (Jenna 2026-11-04).

"Download this data as a CSV", "email me this", "send that over": the
words point at whatever Prometheus just handed over. Jenna, verbatim:
"when someone says something like this or email me this it should take
the very last thing that happened in chat and that's what they want
emailed, downloaded, converted, etc".

The defect: Bria asked for The Office monthly read, got it from the
library, tapped the "Download this data as a CSV" chip under it and was
told "I do not have a recent data read to export yet". The lane had
followed a session pointer to a stored entry with no table behind it
and stopped there; the read on her screen, and the table it shipped
from, were never looked at.

``resolve(history, ...)`` walks the thread back from the end:

  * the reader's own command turns (download / email / approve) and
    Prometheus's delivery confirmations ("Saved. X is in your browser
    downloads", "Sent ...", "Which file?") are skipped, as are
    questions, offers, promises and status lines: none of those is a
    thing to hand over;
  * the first agent turn left is THE thing. A turn that carries a file
    link is a ``file``; a turn whose words match a stored read with a
    table behind it is a ``read``; any other delivered text is ``text``
    (the words themselves travel, or are turned into a table);
  * nothing delivered yet is ``none``: the caller may fall back to the
    page that is open.

Pure logic: the ledger lookup, the per-account file stash and the
clock are injected, so every surface resolves the same way and the
tests run with no network.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

__all__ = ['resolve', 'text_csv', 'file_from_url', 'is_command', 'has_data', 'KINDS']

KINDS = ('file', 'read', 'text', 'none')

# turns that are the reader giving an order, not asking a question
_COMMAND_RX = re.compile(
    r"^\W*(?:please\s+)?(?:can you\s+|could you\s+|would you\s+|can i\s+)?"
    r"(?:download|export|save|e-?mail|mail|send|forward|share|convert|turn|make|get|give me|grab)\b"
    r"[^.?!\n]{0,80}?"
    r"\b(?:this|that|it|these|those|the (?:data|numbers|figures|table|results?|read|answer|file|csv|above|same|output|list|rows)|"
    r"csv|excel|xlsx|spreadsheet|file|deck|pdf|inbox|e-?mail)\b"
    r"[^.?!\n]{0,60}$", re.I)
_ACCEPT_RX = re.compile(
    r"^\W*(?:approve\b.*|accept\b.*|yes|yeah|yep|ok(?:ay)?|sure|run it|go ahead|do it|proceed|confirm(?:ed)?|"
    r"charge (?:it|me)|cancel|decline|no(?: thanks?)?|not now|skip)\W*$", re.I)
# agent lines that confirm a hand-off or decline one: never the thing itself
_CONFIRMATION_RX = re.compile(
    r"^\s*(?:Saved\.|Sent\b|Sent\.|I could not send|The email did not go through|"
    r"I do not have a recent data read|Nothing has landed in this chat|I have not handed you a file|"
    r"There is no email on file|Which file\?|Stopped\. Nothing further|I do not have a file on|"
    r"I do not have an email address on your seat|Working on it\. Confirmed your task)", re.I)
_OFFER_RX = re.compile(r"Pulling this data will cost|\bwill cost \$|\bIt runs \$|\$\s?\d[\d,]*(?:\.\d+)? (?:for|a|per)\b", re.I)
_PROMISE_RX = re.compile(r"^\s*On it\b|\bthe read will land right here\b|^\s*Working on it\b|^\s*Queued\b|^\s*Still running\b|^\s*In progress\b", re.I)
_QUESTION_RX = re.compile(r"\?\s*$|\bwhich (?:subject|audience|profile|one|title|file)\b|\bdo you (?:want|mean)\b|\bname the\b", re.I)
_READ_HEAD_RX = re.compile(r"^On\s+(?P<who>[^:\n]{1,160}?):(?=\s|$)", re.I)
_PRESIGN_HOST_RX = re.compile(r"\.s3[.-][a-z0-9-]*\.?amazonaws\.com$|^s3\.[a-z0-9-]+\.amazonaws\.com$", re.I)

_MIME = {
    'csv': 'text/csv', 'txt': 'text/plain', 'json': 'application/json', 'pdf': 'application/pdf',
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'svg': 'image/svg+xml',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'zip': 'application/zip', 'html': 'text/html',
}


# ------------------------------------------------------------------ helpers
def _turns(history):
    return [t for t in (history or []) if isinstance(t, dict)]


def _text(t):
    return ' '.join(str((t or {}).get('text') or (t or {}).get('content') or '').split())


def _role(t):
    return str((t or {}).get('role') or '').strip().lower()


def _meta(t):
    m = (t or {}).get('meta')
    return m if isinstance(m, dict) else {}


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def _ts(value):
    """Epoch seconds from a client ISO stamp or an epoch number; 0 when unreadable."""
    if value in (None, ''):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    try:
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return 0.0


def is_command(text):
    """True when the reader's line is an order about something already
    delivered (download / email / convert this), or a chip answer."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 140:
        return False
    return bool(_COMMAND_RX.match(t) or _ACCEPT_RX.match(t))


def has_data(entry):
    """A stored read with a table behind it (a breakdown or metrics)."""
    if not isinstance(entry, dict):
        return False
    bd = entry.get('breakdown')
    if isinstance(bd, dict) and bd.get('rows'):
        return True
    m = entry.get('metrics')
    return bool(m)


def _is_deliverable(turn):
    """An agent turn that handed something over: a file link, a chart,
    or delivered words. Confirmations, questions, offers, promises and
    status lines are not."""
    meta = _meta(turn)
    if (isinstance(meta.get('link'), dict) and meta['link'].get('url')) \
            or (isinstance(meta.get('file_link'), dict) and meta['file_link'].get('url')) \
            or (isinstance(meta.get('chart'), dict) and meta['chart'].get('url')):
        return True
    txt = _text(turn)
    if len(txt) < 40:
        return False
    if _CONFIRMATION_RX.match(txt):
        return False
    if meta.get('panel_offer') or meta.get('offer') or (_OFFER_RX.search(txt) and len(txt) < 600):
        return False
    if _PROMISE_RX.search(txt) and len(txt) < 400:
        return False
    if _QUESTION_RX.search(txt[:200]) and len(txt) < 260 and not _READ_HEAD_RX.match(txt):
        return False
    return True


def file_from_url(url, *, bucket=''):
    """{url, s3_key, filename, bucket, same_origin} for a link a turn
    carries: a presigned S3 URL (the key is the path), or the
    dashboard's own ``/api/download-cached/<key>`` link."""
    u = str(url or '').strip()
    if not u:
        return None
    try:
        p = urlparse(u)
    except Exception:
        return None
    key = ''
    b = ''
    same_origin = False
    path = unquote(p.path or '')
    if _PRESIGN_HOST_RX.search(p.netloc or ''):
        host = p.netloc.lower()
        if host.startswith('s3.') or host.startswith('s3-'):
            parts = path.lstrip('/').split('/', 1)
            b = parts[0]
            key = parts[1] if len(parts) > 1 else ''
        else:
            b = host.split('.s3')[0]
            key = path.lstrip('/')
    elif '/api/download-cached/' in path:
        key = path.split('/api/download-cached/', 1)[1]
        b = bucket
        same_origin = True
    elif not p.netloc and path.startswith('/api/download-cached/'):
        key = path.split('/api/download-cached/', 1)[1]
        b = bucket
        same_origin = True
    if not key:
        return None
    fname = key.rsplit('/', 1)[-1]
    return {'url': u, 's3_key': key, 'filename': fname, 'bucket': b or bucket, 'same_origin': same_origin,
            'ext': fname.rsplit('.', 1)[-1].lower() if '.' in fname else '',
            'mime': _MIME.get(fname.rsplit('.', 1)[-1].lower() if '.' in fname else '', 'application/octet-stream')}


def _head(text):
    """The words of a reply with a leading 'On <subject>:' removed, folded for comparison."""
    t = str(text or '')
    m = _READ_HEAD_RX.match(t.strip())
    if m:
        t = t.strip()[m.end():]
    return _norm(t)


def _same_words(a, b, n=120):
    """Two replies are the same read when the first stretch of one sits in the other."""
    a, b = _head(a), _head(b)
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    probe = short[:n]
    return len(probe) >= 40 and probe in long_


def _subject_of(turn, question=''):
    meta = _meta(turn)
    for k in ('subject', 'profile'):
        if meta.get(k):
            return str(meta[k]).strip()
    m = _READ_HEAD_RX.match(_text(turn))
    if m:
        return m.group('who').strip()
    return ''


def _entry_for(turn, question, consult):
    """The stored read the turn shipped from, or None.

    The stored reply's words decide (the thread holds the reply the
    reader saw; the ledger holds the reply it shipped). Among matching
    entries one with a table wins, newest first. When no reply matches,
    the exact entry for the question is used only when it carries a
    table: a text-only twin of a read is never the read."""
    if consult is None:
        return None
    txt = _text(turn)
    subject = _subject_of(turn, question)
    seen, cands, exact = set(), [], None
    for kw in ({'question': question} if question else {}, {'subject': subject, 'question': question} if subject else {}):
        if not kw:
            continue
        try:
            led = consult(**kw) or {}
        except Exception:
            continue
        ex = led.get('exact')
        if ex and exact is None:
            exact = ex
        for e in list(led.get('entries') or []) + ([ex] if ex else []):
            if not isinstance(e, dict):
                continue
            ident = id(e)
            if ident in seen:
                continue
            seen.add(ident)
            cands.append(e)
    cands.sort(key=lambda e: str(e.get('ts') or ''), reverse=True)
    text_only = None
    for e in cands:
        if not e.get('reply') or not _same_words(txt, e.get('reply')):
            continue
        if has_data(e):
            return e
        if text_only is None:
            text_only = e
    if exact and has_data(exact):
        return exact
    if text_only is not None:
        # the same words under another timestamp may hold the table
        qn = text_only.get('qn')
        k = text_only.get('k')
        for e in cands:
            if has_data(e) and ((qn and e.get('qn') == qn) or (k and e.get('k') == k)) and _same_words(e.get('reply'), text_only.get('reply')):
                return e
    return text_only


def _stash_file(stash, turn, question, bucket):
    """The per-account last file when it is the file for THIS turn: its
    question is the question that drew the turn, or it was written in
    the minutes around the turn and names the turn's subject."""
    if not isinstance(stash, dict) or not stash.get('s3_key'):
        return None
    sq = _norm(stash.get('question'))
    q = _norm(question)
    if sq and q and (sq[:120] == q[:120]):
        return _stash_as_file(stash, bucket)
    t_turn = _ts(turn.get('ts'))
    t_stash = _ts(stash.get('ts'))
    subj = _norm(stash.get('subject'))
    if t_turn and t_stash and abs(t_turn - t_stash) <= 300 and subj and subj in _norm(_text(turn)):
        return _stash_as_file(stash, bucket)
    return None


def _stash_as_file(stash, bucket):
    key = str(stash.get('s3_key') or '')
    fname = str(stash.get('filename') or key.rsplit('/', 1)[-1] or 'data.csv')
    ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
    return {'url': str(stash.get('url') or ''), 's3_key': key, 'filename': fname, 'bucket': bucket,
            'same_origin': False, 'ext': ext, 'mime': _MIME.get(ext, 'application/octet-stream')}


# ------------------------------------------------------------------ resolve
def resolve(history, *, consult=None, stash=None, bucket='dashboard-inputs'):
    """The very last thing Prometheus handed over in this thread.

    Returns {'kind': file|read|text|none, 'turn', 'text', 'question',
    'subject', 'file', 'entry', 'why'}. ``consult`` is the ledger lookup
    (``insights_ledger.consult`` when None and importable); ``stash`` is
    the account's last-file record; ``bucket`` names the dashboard
    bucket for same-origin links."""
    out = {'kind': 'none', 'turn': None, 'text': '', 'question': '', 'subject': '',
           'file': None, 'entry': None, 'why': 'nothing delivered in this thread'}
    turns = _turns(history)
    if consult is None and os.environ.get('REGRESSION_TEST_MODE') != '1':
        try:
            import insights_ledger as _il
            consult = _il.consult
        except Exception:
            consult = None
    idx = None
    for i in range(len(turns) - 1, -1, -1):
        t = turns[i]
        if _role(t) == 'user':
            continue
        if _is_deliverable(t):
            idx = i
            break
    if idx is None:
        return out
    turn = turns[idx]
    question = ''
    for j in range(idx - 1, -1, -1):
        if _role(turns[j]) != 'user':
            continue
        uq = _text(turns[j])
        if uq and not is_command(uq):
            question = uq
            break
    txt = _text(turn)
    out.update(turn=turn, text=str(turn.get('text') or ''), question=question, subject=_subject_of(turn, question))
    meta = _meta(turn)
    link = meta.get('link') if isinstance(meta.get('link'), dict) else (meta.get('file_link') if isinstance(meta.get('file_link'), dict) else None)
    if link and link.get('url'):
        f = file_from_url(link.get('url'), bucket=bucket)
        if f:
            f['label'] = str(link.get('label') or '')
            out.update(kind='file', file=f, why='the turn carries a file link')
            return out
    chart = meta.get('chart') if isinstance(meta.get('chart'), dict) else None
    sf = _stash_file(stash, turn, question, bucket)
    if sf:
        out.update(kind='file', file=sf, why='the account file written for this turn')
        return out
    entry = _entry_for(turn, question, consult)
    if entry is not None and has_data(entry):
        out.update(kind='read', entry=entry, subject=str(entry.get('subject') or out['subject'] or ''),
                   why='the stored read the turn shipped from')
        return out
    if chart and chart.get('url'):
        f = file_from_url(chart.get('url'), bucket=bucket)
        if f:
            f['label'] = str(chart.get('alt') or 'Chart')
            out.update(kind='file', file=f, why='the turn carries a chart')
            return out
    if entry is not None:
        out['entry'] = entry
    out.update(kind='text', why='delivered words with no table behind them')
    return out


# ---------------------------------------------------------------- text -> csv
_NUM_RX = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?(?:\s?(?:M|K|B|MM|million|billion|thousand|x)\b)?")
_LABEL_RX = re.compile(r"(?P<label>[A-Za-z][A-Za-z0-9 /&()'+.\-]{1,90}?)\s*:\s*(?=[-+]?\$?\d)")
_DATE_RX = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b(?:19|20)\d{2}\b", re.I)
_UNIT_STOP_RX = re.compile(r"^(?:and|or|but|with|while|than|that|which|who|as|at|on|in|for|from|to|by|the|a|an|is|are|was|were|of)$", re.I)
_SKIP_LINE_RX = re.compile(r"^\s*(?:How this is counted|Counting|Definitions?|Read as|Notes?)\b", re.I)
_BULLET_RX = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


def _sentences(block):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"'])", block) if s.strip()]


def _figures(segment):
    """The figures in a stretch of text, each with the unit words that
    follow it ('12,347,931 US viewers', '8.04% of hours'); dates and bare
    years are context, not figures."""
    seg = _DATE_RX.sub(' ', str(segment or ''))
    out = []
    for m in _NUM_RX.finditer(seg):
        num = m.group(0).strip().rstrip(',.;:')
        if not re.search(r"\d", num):
            continue
        tail = seg[m.end():]
        words = []
        for w in re.split(r"\s+", tail.strip()):
            w2 = w.strip(',;:.()')
            if not w2 or re.search(r"\d", w2):
                break
            if _UNIT_STOP_RX.match(w2) and not (w2.lower() == 'of' and not words):
                break
            if w2 != w and w.endswith((',', ';', ':', '.')):
                words.append(w2)
                break
            words.append(w2)
            if len(words) >= 3:
                break
        out.append(' '.join([num, *words]).strip())
    return out


def text_csv(text, *, subject='', question='', downloaded=''):
    """A table from the words of a reply: (filename, csv_text).

    Every line or sentence that carries a figure becomes a row. A
    'label: figures' phrase is split into Item and its Figures (several
    on one line each become a row); a sentence with figures and no label
    is a row whose Item is the sentence and whose Figures are the numbers
    in it, in order. Headings and lines with no figure are left out. The
    first rows name the subject, the question and the date downloaded."""
    raw = str(text or '')
    head = _READ_HEAD_RX.match(raw.strip())
    subj = str(subject or '').strip()
    if head:
        subj = subj or head.group('who').strip()
        raw = raw.strip()[head.end():]
    rows = []
    for line in raw.split('\n'):
        line = _BULLET_RX.sub('', line).strip()
        if not line or _SKIP_LINE_RX.match(line) or not re.search(r"\d", line) or line.endswith(':'):
            continue
        for sent in _sentences(line):
            labels = list(_LABEL_RX.finditer(sent))
            if labels:
                lead = sent[:labels[0].start()].strip(' ,;:-')
                for n, m in enumerate(labels):
                    seg_end = labels[n + 1].start() if n + 1 < len(labels) else len(sent)
                    figs = _figures(sent[m.end():seg_end])
                    label = m.group('label').strip(' ,;:-')
                    if figs and label:
                        rows.append([label, *figs])
                if not lead or _figures(lead):
                    pass
                continue
            figs = _figures(sent)
            if figs:
                rows.append([sent.rstrip('.'), *figs])
    width = max([2] + [len(r) for r in rows])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    header = ['Item', 'Figure'] + [f'Figure {i}' for i in range(2, width)]
    w.writerow(header)
    pad = lambda r: r + [''] * (width - len(r))
    if subj:
        w.writerow(pad(['Subject', subj]))
    if question:
        w.writerow(pad(['Question', ' '.join(str(question).split())[:300]]))
    w.writerow(pad(['Date downloaded', downloaded or datetime.now(timezone.utc).strftime('%B %-d, %Y')]))
    for r in rows:
        w.writerow(pad([str(c) for c in r]))
    if not rows:
        # no figures at all: the words themselves, one row per paragraph
        for para in [p.strip() for p in re.split(r"\n{2,}", raw) if p.strip()]:
            w.writerow(pad(['Text', ' '.join(para.split())]))
    name = re.sub(r'[\\/:*?"<>|]+', ' ', subj or 'Prometheus reply')
    name = re.sub(r'\s+', ' ', name).strip()[:80].strip(' .') or 'Prometheus reply'
    return f"{name}.csv", buf.getvalue()


def file_bytes(file, s3, *, bucket='dashboard-inputs', extra_buckets=()):
    """The bytes of a resolved file from S3 (its own bucket first, then
    the dashboard bucket, then any extra), or None."""
    if not isinstance(file, dict) or not file.get('s3_key') or s3 is None:
        return None
    tried = []
    for b in [file.get('bucket'), bucket, *extra_buckets]:
        if not b or b in tried:
            continue
        tried.append(b)
        try:
            return s3.get_object(Bucket=b, Key=file['s3_key'])['Body'].read()
        except Exception:
            continue
    return None


def fresh_url(file, s3, *, bucket='dashboard-inputs', expires=7 * 24 * 3600):
    """A link that opens now: a fresh presigned URL for an S3 file, or
    the same-origin link as it is."""
    if not isinstance(file, dict):
        return ''
    if file.get('same_origin') or not file.get('s3_key') or s3 is None:
        return str(file.get('url') or '')
    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': file.get('bucket') or bucket, 'Key': file['s3_key'],
                    'ResponseContentDisposition': f'attachment; filename="{file.get("filename") or "file"}"'},
            ExpiresIn=expires)
    except Exception:
        return str(file.get('url') or '')


def now_label():
    return datetime.now(timezone.utc).strftime('%B %-d, %Y')


def epoch():
    return time.time()
