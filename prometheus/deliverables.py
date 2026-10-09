"""Deliverable lookup lane (2026-10-09).

Scott, on the Howdy profile the morning after it was built: "where do i
find the howdy deck created last night" ran as a measurement read and
failed; "no a pdf was created. where is it" drew the open-page confirm.
Neither is a question about an audience. "Where is my deck / pdf / file"
is a lookup into what Prometheus has produced for the caller, and it is
answered from the record with no model call:

  1. decks the caller asked for (deck job records)
  2. files and charts linked in the caller's threads
  3. emails Prometheus sent the caller (the outbound mail log)
  4. the dashboard catalog for the subject itself (the profile is live
     even when no deck exists)

The reply names what is on file with a fresh link, or says plainly
that nothing is and offers to build it. A subject named in the ask
filters the list; an ask that names nothing ("where is the pdf") lists
the caller's recent deliverables, newest first. Fail-safe: None on any
error, and the ask proceeds as before.
"""
from __future__ import annotations

import json
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

LOOKBACK_DAYS = 14
MAX_ITEMS = 5

_ART = (r"decks?|pdfs?|powerpoints?|pptx|slides?|slide decks?|one[- ]pagers?|"
        r"files?|csvs?|exports?|downloads?|reports?|reads?|answers?|emails?|"
        r"links?|charts?|graphics?|visuals?|images?|spreadsheets?|xlsx|docs?|"
        r"documents?|write[- ]?ups?|summar(?:y|ies)|results?|output|profiles?|cuts?")
_ART_RX = re.compile(r"\b(?:" + _ART + r")\b", re.I)
_WHERE_RX = re.compile(
    r"^\s*(?:no[,.!]?\s+|so[,.]?\s+|ok[,.]?\s+|hey[,.]?\s+|prometheus[,.]?\s+)*"
    r"(?:where(?:\s+is|'s|s|\s+are|\s+was|\s+were|\s+did|\s+do\s+i\s+(?:find|get|see|download)|"
    r"\s+can\s+i\s+(?:find|get|see|download)|\s+would\s+i\s+find|\s+could\s+i\s+find|"
    r"\s+should\s+i\s+(?:look|find))|i\s+(?:can'?t|cannot|could\s+not|couldn'?t)\s+(?:find|locate|see)|"
    r"(?:can|could)\s+you\s+(?:find|locate|resend|re-send|send\s+me|link\s+me|show\s+me)|"
    r"(?:please\s+)?(?:resend|re-send|send\s+me|link\s+me|show\s+me|find|locate)|"
    r"(?:give|get)\s+me\s+(?:the\s+)?link\s+(?:to|for))\s+"
    r"(?P<rest>.+?)[\s?.!]*$", re.I)
# "no a pdf was created. where is it" / "a deck was built last night, where is it"
_MADE_RX = re.compile(
    r"^\s*(?:no[,.!]?\s+|but\s+|well[,.]?\s+|yes[,.]?\s+)*(?:a|an|the|my|that|our)?\s*"
    r"(?P<art>" + _ART + r")(?:\s+(?:for|on|of|about)\s+(?P<subj>[^,.?!]+?))?\s+"
    r"(?:was|were|got|has\s+been|had\s+been|is)\s+"
    r"(?:created|made|built|generated|produced|sent|emailed|done|finished|ready)"
    r"(?P<when>[^.?!]*)?[.,;!]?\s*"
    r"(?:where\s+(?:is|'s)\s+(?:it|that|they)|where\s+do\s+i\s+(?:find|get|see)\s+(?:it|that|them)|"
    r"where\s+did\s+(?:it|that|they)\s+go|where\s+can\s+i\s+(?:find|get|see)\s+(?:it|that|them)|"
    r"how\s+do\s+i\s+(?:find|get|open|see)\s+(?:it|that|them))?[\s?.!]*$", re.I)
_WHERE_IS_IT_RX = re.compile(
    r"^\s*(?:no[,.!]?\s+|so[,.]?\s+)*where\s+(?:is|'s|did)\s+(?:it|that|they|those)(?:\s+go)?"
    r"(?:\s+(?:then|now|though))?[\s?.!]*$", re.I)
_MADE_TAIL_RX = re.compile(
    r"\s+(?:that\s+|which\s+)?(?:was\s+|were\s+|got\s+|you\s+|we\s+|prometheus\s+|it\s+)?"
    r"(?:created|made|built|generated|produced|sent|emailed|put\s+together|ran|did|finished|done)"
    r"\b.*$", re.I)
_TIME_RX = re.compile(
    r"\b(?:last\s+night|yesterday|earlier(?:\s+today)?|this\s+(?:morning|afternoon|evening|week)|"
    r"today|tonight|(?:a\s+)?(?:few|couple\s+of)?\s*(?:minutes?|hours?|days?)\s+ago|last\s+week|"
    r"on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|the\s+other\s+day|just\s+now|a\s+while\s+ago|"
    r"overnight|before|previously|already)\b", re.I)
_LEAD_RX = re.compile(r"^(?:the|my|that|a|an|our|your|this|these|those|all|any|some|of)\s+", re.I)
_FOR_RX = re.compile(r"^(?:for|on|of|about|from|covering)\s+", re.I)
_YOU_RX = re.compile(r"\b(?:you|we|prometheus|it|that|which|was|were|got)\b", re.I)
_PROD_WORDS = re.compile(
    r"\b(?:brand\s+partnerships?|insights?|digital\s+journey|journey|subscriber|attribution|"
    r"profile|audience|iq|crosswalk|dashboard|chat|thread|library|here|there|please|me|us)\b", re.I)
_STOP = frozenset(('the', 'a', 'an', 'of', 'for', 'on', 'in', 'and', 'or', 'to', 'at', 'by',
                   'with', 'from', 'about', 'that', 'this', 'my', 'our', 'your', 'it'))


def _norm(s):
    return re.sub(r'[^a-z0-9+&]+', ' ', str(s or '').lower()).strip()


def _tokens(s):
    return [t for t in _norm(s).split() if t not in _STOP]


_KINDS = (
    ('deck', re.compile(r"\b(?:decks?|powerpoints?|pptx|slides?|slide decks?|one[- ]pagers?)\b", re.I)),
    ('pdf', re.compile(r"\bpdfs?\b", re.I)),
    ('csv', re.compile(r"\b(?:csvs?|exports?|downloads?|spreadsheets?|xlsx)\b", re.I)),
    ('chart', re.compile(r"\b(?:charts?|graphics?|visuals?|images?)\b", re.I)),
    ('email', re.compile(r"\b(?:e-?mails?|e-?mailed|mailed|links?|inbox)\b", re.I)),
    ('read', re.compile(r"\b(?:reads?|answers?|reports?|docs?|documents?|write[- ]?ups?|summar(?:y|ies)|results?|output)\b", re.I)),
    ('file', re.compile(r"\bfiles?\b", re.I)),
    ('profile', re.compile(r"\b(?:profiles?|cuts?)\b", re.I)),
)


def _artifacts(text):
    t = str(text or '')
    return {kind for kind, rx in _KINDS if rx.search(t)}


_TRAIL_FOR_RX = re.compile(r"\b(?:for|on|about|covering)\s+(?P<subj>[A-Z][^,.?!]*?)\s*$")


def _clean_subject(rest):
    s = ' '.join(str(rest or '').split())
    # "the pdf you emailed me yesterday for Starz": a trailing for/on
    # phrase names the subject outright
    m = _TRAIL_FOR_RX.search(s)
    if m:
        cand = _TIME_RX.sub(' ', m.group('subj'))
        cand = _ART_RX.sub(' ', cand)
        cand = ' '.join(cand.split()).strip(' ,.?!:;-"\'')
        if cand and 1 <= len(_tokens(cand)) <= 6:
            return cand
    s = _MADE_TAIL_RX.sub('', s)
    s = _TIME_RX.sub(' ', s)
    s = _ART_RX.sub(' ', s)
    s = _PROD_WORDS.sub(' ', s)
    s = _YOU_RX.sub(' ', s)
    s = ' '.join(s.split())
    s = _LEAD_RX.sub('', s)
    s = _FOR_RX.sub('', s)
    s = _LEAD_RX.sub('', s)
    s = s.strip(' ,.?!:;-"\'')
    toks = _tokens(s)
    if not toks or len(toks) > 6:
        return ''
    return s


def parse(text):
    """{'artifacts': set, 'subject': str, 'when': str} or None."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 220 or t.count('?') > 2:
        return None
    if _WHERE_IS_IT_RX.match(t):
        return {'artifacts': set(), 'subject': '', 'when': '', 'bare': True}
    m = _WHERE_RX.match(t)
    if m:
        rest = m.group('rest')
        arts = _artifacts(rest)
        if not arts:
            return None
        # "where is the fall-off from the ticket step" is a journey
        # question; a deliverable ask carries no metric vocabulary
        if re.search(r"\b(?:fall[- ]?off|drop[- ]?off|lift|share|percent|%|index|viewers|"
                     r"audience size|penetration|overlap|churn|signups?|conversions?)\b", rest, re.I):
            return None
        when = ' '.join(x.group(0) for x in _TIME_RX.finditer(rest))
        return {'artifacts': arts, 'subject': _clean_subject(rest), 'when': when}
    m = _MADE_RX.match(t)
    if m:
        arts = _artifacts(m.group('art'))
        return {'artifacts': arts, 'subject': _clean_subject(m.group('subj') or ''),
                'when': ' '.join(str(m.group('when') or '').split())}
    return None


def _subject_from_history(history, lookback=6):
    """The last subject a user turn named through a deliverable word
    ("the howdy deck") so "where is it" inherits it. '' when none."""
    seen = 0
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        if str(h.get('role') or '').lower() != 'user':
            continue
        seen += 1
        if seen > lookback:
            break
        p = parse(str(h.get('text') or ''))
        if p and p.get('subject'):
            return p['subject']
    return ''


def _matches(subject, *fields):
    if not subject:
        return True
    want = set(_tokens(subject))
    if not want:
        return True
    hay = set()
    for f in fields:
        hay |= set(_tokens(f))
    hit = want & hay
    return bool(hit) and any(len(w) >= 3 for w in hit)


def _fresh_link(url, s3, bucket):
    """Re-sign an S3 link so a week-old presigned URL still opens."""
    u = str(url or '')
    if not u.lower().startswith('https://'):
        return u
    try:
        p = urlparse(u)
        if bucket and bucket in p.netloc and 'amazonaws.com' in p.netloc:
            key = unquote(p.path.lstrip('/'))
            if key:
                return s3.generate_presigned_url(
                    'get_object', Params={'Bucket': bucket, 'Key': key},
                    ExpiresIn=7 * 24 * 3600)
    except Exception:
        pass
    return u


def _when(ts):
    try:
        if isinstance(ts, str) and ts:
            try:
                d = datetime.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            except ValueError:
                d = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        else:
            d = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return d.strftime('%b %-d, %-I:%M%p UTC').replace('AM', 'am').replace('PM', 'pm'), d.timestamp()
    except Exception:
        return '', 0.0


def _s3_json(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    except Exception:
        return None


def _deck_items(uname, subject, s3, bucket, deck_prefix, since):
    out = []
    try:
        pag = s3.get_paginator('list_objects_v2')
        keys = []
        for page in pag.paginate(Bucket=bucket, Prefix=deck_prefix):
            for o in page.get('Contents') or []:
                lm = o.get('LastModified')
                ts = lm.timestamp() if hasattr(lm, 'timestamp') else time.time()
                if ts >= since and str(o.get('Key') or '').endswith('.json'):
                    keys.append((ts, o['Key']))
        keys.sort(reverse=True)
        for _, k in keys[:60]:
            d = _s3_json(s3, bucket, k) or {}
            if str(d.get('user') or '').lower() != str(uname or '').lower():
                continue
            if str(d.get('status') or '') != 'done' or not d.get('url'):
                continue
            subj = str(d.get('image_subject') or d.get('subject') or '')
            title = str(d.get('title') or '')
            fname = str(d.get('filename') or '')
            if not _matches(subject, subj, title, fname, str(d.get('angle') or '')):
                continue
            label, ts = _when(d.get('finished_at') or d.get('started_at'))
            out.append({'kind': 'deck', 'label': fname or title or 'deck', 'subject': subj,
                        'when': label, 'ts': ts, 'url': _fresh_link(d.get('url'), s3, bucket)})
    except Exception:
        traceback.print_exc()
    return out


def _thread_items(uname, subject, s3, bucket, host, since):
    out = []
    try:
        idx = host.load_threads_index(uname) or {}
        threads = sorted((idx.get('threads') or []), key=lambda t: str(t.get('updated') or ''), reverse=True)[:8]
        for th in threads:
            hist = host.s3_json(host.thread_key(uname, th.get('id')), []) or []
            last_user = ''
            for turn in hist:
                if not isinstance(turn, dict):
                    continue
                if str(turn.get('role') or '').lower() == 'user':
                    last_user = str(turn.get('text') or '')
                    continue
                meta = turn.get('meta') or {}
                for mk, kind in (('link', 'file'), ('chart', 'chart')):
                    link = meta.get(mk) if isinstance(meta.get(mk), dict) else None
                    if not link or not link.get('url'):
                        continue
                    if meta.get('kind') == 'deck' and mk == 'link':
                        continue  # deck job records carry the deck
                    label, ts = _when(turn.get('ts') or '')
                    if ts and ts < since:
                        continue
                    name = str(link.get('label') or link.get('filename') or link.get('alt') or kind)
                    if not _matches(subject, name, last_user, str(turn.get('text') or '')[:300]):
                        continue
                    out.append({'kind': kind, 'label': re.sub(r'^Download\s+', '', name), 'subject': '',
                                'when': label, 'ts': ts, 'url': _fresh_link(link['url'], s3, bucket)})
    except Exception:
        traceback.print_exc()
    return out


def _mail_items(email, subject, s3, bucket, since):
    out = []
    if not email or '@' not in str(email):
        return out
    try:
        from . import outbound_mail as _om
        prefix = _om.LOG_PREFIX
    except Exception:
        prefix = 'system/outbound_mail/'
    try:
        day = datetime.now(timezone.utc)
        days = [(day - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(LOOKBACK_DAYS + 1)]
        for d in days:
            r = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}{d}/")
            for o in r.get('Contents') or []:
                rec = _s3_json(s3, bucket, o['Key']) or {}
                if not rec.get('sent'):
                    continue
                if str(rec.get('to') or '').lower() != str(email).lower():
                    continue
                subj = str(rec.get('subject') or '')
                att = [str(rec.get(k) or '') for k in ('pdf_name', 'csv_name') if rec.get(k)]
                if not _matches(subject, subj, ' '.join(att)):
                    continue
                lm = o.get('LastModified')
                label, ts = _when(rec.get('logged_at') or (lm.timestamp() if hasattr(lm, 'timestamp') else time.time()))
                out.append({'kind': 'email', 'label': subj or 'an email', 'subject': '', 'when': label,
                            'ts': ts, 'url': '', 'attachments': att})
    except Exception:
        traceback.print_exc()
    return out


def _catalog_line(subject):
    """What the dashboard holds on the subject, or ''."""
    if not subject:
        return '', ''
    try:
        from migration import corpus_catalog as cc
        anchors = cc.anchors_for(subject, with_ledger=False)
    except Exception:
        return '', ''
    facts = anchors.get('facts') or []
    display = anchors.get('subject') or subject
    if not facts:
        return '', display
    products = []
    cuts = 0
    for f in facts:
        p = f.get('product')
        if p == 'profile' and f.get('cut'):
            cuts += 1
        elif p and p not in products:
            products.append(p)
    labels = {'profile': 'the profile', 'journey': 'a Digital Journey', 'attribution': 'an Attribution IQ read',
              'bpiq': 'a Brand Partnership IQ read', 'subiq': 'a Subscriber IQ read', 'trends': 'Trends IQ',
              'deck': 'a deck', 'chat': 'an earlier answer'}
    parts = [labels.get(p, p) for p in products]
    if cuts:
        parts.append(f"{cuts} cut{'s' if cuts != 1 else ''}")
    if not parts:
        return '', display
    return f"What is live on the dashboard for {display}: " + ', '.join(parts) + ". Open it from Select Profile.", display


_KIND_LABEL = {'deck': 'Deck', 'file': 'File', 'chart': 'Chart', 'email': 'Email'}


def answer(text, uname, user=None, ctx=None, history=None, host=None):
    """A finished reply payload for a where-is-my-deliverable ask, else
    None."""
    parsed = parse(text)
    if not parsed:
        return None
    if host is None:
        try:
            from .host import host as _host
            host = _host
        except Exception:
            return None
    subject = parsed.get('subject') or _subject_from_history(history)
    if parsed.get('bare') and not subject:
        return None  # "where is it" with no deliverable in the thread: not ours
    arts = set(parsed.get('artifacts') or ())
    try:
        s3, bucket = host.s3_client, host.bucket
    except Exception:
        return None
    since = time.time() - LOOKBACK_DAYS * 86400
    email = str((user or {}).get('email') or '') if isinstance(user, dict) else ''
    try:
        deck_prefix = host.deck_prefix
    except Exception:
        deck_prefix = 'system/prometheus_decks/'
    items = []
    items += _deck_items(uname, subject, s3, bucket, deck_prefix, since)
    items += _thread_items(uname, subject, s3, bucket, host, since)
    items += _mail_items(email, subject, s3, bucket, since)
    # an ask for a specific format lists that format first, the rest after
    want_kinds = set()
    if 'deck' in arts:
        want_kinds.add('deck')
    if 'pdf' in arts or 'email' in arts:
        want_kinds.add('email')
    if 'csv' in arts or 'file' in arts:
        want_kinds.add('file')
    if 'chart' in arts:
        want_kinds.add('chart')
    items.sort(key=lambda it: (0 if it['kind'] in want_kinds else 1, -float(it.get('ts') or 0)))
    seen, picked = set(), []
    for it in items:
        k = (it['kind'], it['label'], it['when'])
        if k in seen:
            continue
        seen.add(k)
        picked.append(it)
        if len(picked) >= MAX_ITEMS:
            break
    cat_line, display = _catalog_line(subject)
    display = display or subject
    noun = 'deck' if 'deck' in arts else ('PDF' if 'pdf' in arts else ('chart' if 'chart' in arts else 'file'))
    on = f" on {display}" if display else ''
    followups = []
    if display:
        followups.append(f"Build a {display} deck")
        followups.append(f"Open {display}")
    if picked:
        lines = []
        for it in picked:
            head = f"{_KIND_LABEL.get(it['kind'], 'File')}: {it['label']}"
            if it.get('when'):
                head += f" ({it['when']})"
            if it['kind'] == 'email':
                att = it.get('attachments') or []
                head += (f", attached {', '.join(att)}" if att else '') + ", sent to your inbox from Prometheus"
            elif it.get('url'):
                head += f" - {it['url']}"
            lines.append('- ' + head)
        reply = f"Here is what I have for you{on}, newest first:\n" + '\n'.join(lines)
        if cat_line:
            reply += '\n\n' + cat_line
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups[:3],
                'offer_deck': False, 'deck_angle': None, 'deliverable_lookup': True,
                'subject': display, 'items': picked}
    # nothing produced for the caller
    if display:
        reply = (f"I do not have a {display} {noun} on file for you, and nothing about {display} has gone "
                 f"to your inbox from Prometheus in the last two weeks.")
        if cat_line:
            reply += ' ' + cat_line
        reply += (" If you exported a PDF from the dashboard, it saved to your computer's downloads folder."
                  f" Say 'build a {display} deck' and I will put one together.")
    else:
        reply = ("I have not produced any decks or files for you in the last two weeks, and nothing has gone "
                 "to your inbox from Prometheus. Tell me the subject and the format you want (deck, PDF, or "
                 "CSV) and I will make it.")
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups[:3],
            'offer_deck': False, 'deck_angle': None, 'deliverable_lookup': True, 'subject': display}
