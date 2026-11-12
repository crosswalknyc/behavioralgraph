"""'Email me this file': a command, never a build (2026-10-12, Debi).

Debi had the Austin Sprinz profile open and typed "Email me this file".
It went to the build flow, which came back with an existing-match
offer. A reader asking for their own file by email is a command: send
it. What rides:

* a journey named ("email me the excel file of the Young Sheldon
  Netflix journey that is in my library"), or a library profile named:
  that file (Carolyn Bisson, WBD, 2026-11-02: the lane knew only
  Profile IQ files, asked "Which file?" with the journey open on her
  screen, and her answer to that question started a cold read on a
  title her library already held);
* otherwise THE VERY LAST THING in the thread (Jenna 2026-11-04: "take
  the very last thing that happened in chat and that's what they want
  emailed, downloaded, converted"): a file the last reply carried goes
  as it is; a read with a table behind it goes as its CSV with the read
  in the body (prometheus/last_thing.py resolves it);
* a thread whose last reply has no file or table: with a profile or a
  Digital Journey open, that file, with the read in the body; with
  nothing open, the read as the body.

A send order is answered from what we hold. It never starts a read and
never drafts a build.

The mail goes through the house door (From Prometheus, Jenna and Liz
on BCC, vocabulary scrubbed, logged). The reader asked for it, so the
send is instructed by the reader; the caller label says so.
"""
from __future__ import annotations

import re

_SEND_RX = re.compile(
    r"^\W*(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?(?:e-?mail|send|mail|forward)\s+"
    r"(?:me\s+|us\s+)?(?:a copy of\s+)?(?:this|that|the|those|these)\s*"
    r"(?:(?:excel|xlsx|xls|csv|spreadsheet|workbook|pdf)\s+)?(?:(?:file|workbook|export|copy|version)\s+)?(?:(?:of|for)\s+(?:this|that|the)\s+)?"
    r"(?:profile|file|csv|read|report|answer|data|results?|numbers|analysis|output|journey|breakdown|workbook|spreadsheet|export|page)?s?"
    r"(?:\s+(?:to me|to my (?:e-?mail|inbox)|over|please|in (?:excel|xlsx|csv)(?: format)?))?\W*$", re.I)
_SEND_PLAIN_RX = re.compile(r"^\W*(?:e-?mail|send)\s+(?:it|this|that)\s+(?:to\s+)?me\W*$", re.I)
# "email me the Babylon 5 breakdown", "send me the Nike profile" (2026-10-14: the
# first drafted a profile build; a send order names a thing we hold, never a build)
_FORMATS = r"(?:excel|xlsx|xls|csv|spreadsheet|workbook|pdf)"
_NOUNS = r"(?:profile|file|csv|breakdown|read|report|numbers|data|analysis|journey|deck|results|answer|summary|output|tracker|valuation|campaign|workbook|spreadsheet|export)"
# a trailing clause that says where the thing lives or what shape it should come in; it names no new thing
_TAIL = (r"(?:\s+(?:to me|to my (?:e-?mail|inbox)|over|please|you (?:built|made|did|ran)|from (?:yesterday|last week|earlier)"
         r"|(?:that(?:'s| is| was)?\s+|which (?:is|was)\s+)?(?:in|from|on)\s+(?:my|the|your)\s+(?:library|dashboard|account|files?|workspace|inbox)"
         r"|(?:in|as)\s+(?:an?\s+)?" + _FORMATS + r"(?:\s+(?:file|format|export))?|" + _FORMATS + r"\s+(?:file|format|export)))*")
_SEND_NAMED_RX = re.compile(
    r"^\W*(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?(?:e-?mail|send|mail|forward)\s+(?:me\s+|us\s+)?(?:a copy of\s+)?"
    r"(?:the|that|this|those|these|your|my|our)?\s*(?P<subj>[A-Za-z0-9][^,.?!]{1,80}?)\s+"
    r"(?P<noun>" + _NOUNS + r")s?" + _TAIL + r"\W*$", re.I)
# "Young Sheldon on Netflix - watch Journey excel format", "Babylon 5 journey in excel", "the Nike profile as a csv":
# a named thing we hold, asked for in a file shape; a file we hold or a plain no, never a read
_NAMED_FORMAT_RX = re.compile(
    r"^\W*(?:the\s+)?(?P<subj>[A-Za-z0-9][^,.?!]{1,80}?)\s*(?:[-:,]\s*)?(?:watch\s+|digital\s+)?(?:" + _NOUNS + r")?s?\s*"
    r"(?:(?:in|as)\s+(?:an?\s+)?)?" + _FORMATS + r"(?:\s+(?:file|format|export|version|copy))?(?:\s+please)?\W*$", re.I)
# "the excel file of the Young Sheldon journey": the format rides in front of the name
_FORMAT_LEAD_RX = re.compile(r"^(?:(?:an?|the)\s+)?(?:" + _FORMATS + r"\s+)?(?:file|workbook|spreadsheet|export|copy|version)\s+(?:of|for)\s+(?:the\s+)?", re.I)
CLARIFIER_LEAD = 'Which file?'


def parse(text):
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 140:
        return False
    if re.search(r"\bwhen\b", t, re.I):
        return False   # "email me when it's ready" is the notify family, not this lane
    if _SEND_RX.match(t) or _SEND_PLAIN_RX.match(t):
        return True
    return bool(named_subject(t))


def named_subject(text):
    """The subject a send order names ('Babylon 5' in 'email me the Babylon
    5 breakdown', 'Young Sheldon Netflix' in 'email me the excel file of
    the Young Sheldon Netflix journey that is in my library'), else ''."""
    t = ' '.join(str(text or '').split())
    m = _SEND_NAMED_RX.match(t)
    if not m:
        m = _NAMED_FORMAT_RX.match(t)
        if not m or not re.search(r"\b(?:journey|profile|breakdown|report|file|workbook)\b", t, re.I):
            return ''
    return clean_subject(m.group('subj'))


def clean_subject(subj):
    subj = ' '.join(str(subj or '').split()).strip(' ,.:;-"\'')
    subj = _FORMAT_LEAD_RX.sub('', subj)
    subj = re.sub(r"^(?:the|that|this|your|my|our|latest|last|new)\s+", '', subj, flags=re.I)
    subj = re.sub(r"\s+(?:watch|digital)\s*$", '', subj, flags=re.I)
    subj = re.sub(r"^(?:" + _FORMATS + r")\s+(?:of\s+)?(?:the\s+)?", '', subj, flags=re.I).strip(' ,.:;-')
    if not subj or subj.lower() in ('it', 'this', 'that', 'same', 'latest', 'last', 'excel', 'csv', 'file', 'the file',
                                    'the', 'a', 'an', 'my', 'your', 'our', 'me', 'us'):
        return ''
    return subj


def follows_clarifier(history):
    """True when the last thing Prometheus said in this thread was the
    "Which file?" question: the reader's next line is the name."""
    for t in reversed(history or []):
        if not isinstance(t, dict):
            continue
        if str(t.get('role') or '') == 'agent':
            return str(t.get('text') or '').strip().startswith(CLARIFIER_LEAD)
    return False


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


# ------------------------------------------------------------ Digital Journeys
_STOP = {'the', 'a', 'an', 'of', 'on', 'in', 'at', 'to', 'for', 'and', 'journey', 'journeys', 'digital', 'watch', 'file', 'workbook',
         'excel', 'xlsx', 'csv', 'spreadsheet', 'breakdown', 'report', 'my', 'your', 'our', 'library', 'dashboard'}
JOURNEY_INDEX_KEY = 'journey-iq/_index.json'


def _tokens(s):
    return [w for w in re.sub(r"[^a-z0-9+]+", " ", str(s or '').lower()).split() if w not in _STOP]


def _bucket(host):
    try:
        return str(host.bucket)
    except Exception:
        return 'dashboard-inputs'


def _user_record(uname, host):
    try:
        data = host.load_users()
        return ((data.get('users') or {}).get(uname) or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def user_journeys(uname, host, *, user=None):
    """[{name, key, created_at}] the reader can open in Digital Journey
    IQ: their own runs, the runs on their seat's list, every run for an
    admin. Newest first. Fail-safe: []."""
    try:
        idx = host.s3_json(JOURNEY_INDEX_KEY, None) if host.has('s3_json') else None
        if not isinstance(idx, dict):
            import json as _json
            idx = _json.loads(host.s3_client.get_object(Bucket=_bucket(host), Key=JOURNEY_INDEX_KEY)['Body'].read().decode('utf-8'))
    except Exception as e:
        print(f"[send-file] journey index unavailable: {e}")
        return []
    rec = user if isinstance(user, dict) and user.get('username') == uname else _user_record(uname, host)
    allowed = rec.get('allowed_journey_iq_runs')
    allowed = set(allowed) if isinstance(allowed, list) else set()
    everything = ('*' in allowed) or str(rec.get('role') or '').lower() in ('super_admin', 'admin', 'superadmin')
    out = []
    for r in (idx.get('runs') or []):
        if not isinstance(r, dict) or not r.get('key'):
            continue
        key = str(r['key'])
        if not (everything or str(r.get('created_by') or '') == uname or key in allowed):
            continue
        out.append({'name': str(r.get('project_name') or r.get('target') or key.rsplit('/', 1)[-1]).strip(),
                    'key': key, 'created_at': str(r.get('created_at') or '')})
    out.sort(key=lambda r: r['created_at'], reverse=True)
    return out


def match_journeys(subject, journeys):
    """The journeys a name points at: every token of the name is in the
    journey's name (platform and year words count). An exact name wins
    alone; a name that fits several returns them all (the caller asks)."""
    want = _tokens(subject)
    if not want:
        return []
    exact = [j for j in journeys if _tokens(j['name']) == want]
    if exact:
        return exact[:1]
    hits = []
    for j in journeys:
        have = set(_tokens(j['name']))
        if all(w in have for w in want):
            hits.append(j)
    if len(hits) != 1:
        # two journeys under one show's name ("the Abbott Elementary library journey", a Season 6 journey and a
        # Library journey saved): the words that differ between them pick one (prometheus.journey_pick, 2026-11-11);
        # "library" names the Library journey here even though it is dropped above as "in my library"
        try:
            from . import journey_pick as _jp
            got = _jp.pick(subject, hits or journeys)
            if got.get('item') is not None and got.get('how') == 'told_apart':
                return [got['item']]
        except Exception:
            pass
    return hits


def open_journey_key(ctx, uname, host):
    """The journey on the reader's screen: the key the page sends with a
    Digital Journey open, else the journey the newest thread binding on
    this seat points at (the page binds a thread when it opens a run)."""
    pc = ctx if isinstance(ctx, dict) else {}
    vc = pc.get('view_context') if isinstance(pc.get('view_context'), dict) else {}
    view = str(vc.get('view_id') or pc.get('view') or '')
    if view != 'journeyIQ':
        return ''
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    key = str(data.get('key') or data.get('run_key') or '').strip()
    if key.startswith('journey-iq/'):
        return key
    sk = str(vc.get('subject_key') or '')
    if sk.startswith('journey:journey-iq/'):
        return sk.split(':', 1)[1]
    try:
        idx = host.load_threads_index(uname) if host.has('load_threads_index') else {}
        best, when = '', ''
        for t in (idx.get('threads') or []):
            b = t.get('binding') if isinstance(t.get('binding'), dict) else {}
            k = str(b.get('subject_key') or '')
            if not k.startswith('journey:journey-iq/'):
                continue
            opened = str(t.get('opened') or t.get('updated') or t.get('created') or '')
            if opened >= when:
                best, when = k.split(':', 1)[1], opened
        return best
    except Exception:
        return ''


def journey_workbook(key, host):
    """(bytes, filename, mime, project_name) of a journey's breakdown
    workbook, built from the run as it stands; None when it cannot be
    read."""
    import gzip as _gzip
    import json as _json
    try:
        raw = host.s3_client.get_object(Bucket=_bucket(host), Key=key)['Body'].read()
        try:
            raw = _gzip.decompress(raw)
        except Exception:
            pass
        payload = _json.loads(raw.decode('utf-8'))
    except Exception as e:
        print(f"[send-file] journey fetch failed for {key}: {e}")
        return None
    name = str(((payload.get('meta') or {}).get('project_name') or (payload.get('meta') or {}).get('target_name') or '')).strip() or key.rsplit('/', 1)[-1]
    try:
        from migration import journey_iq as _jiq
        try:
            return _jiq.breakdown_xlsx(payload), _jiq.breakdown_filename(payload), \
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', name
        except Exception as e:   # no workbook writer on this host: the same rows as a CSV
            print(f"[send-file] workbook build failed for {key}: {e}")
            return _jiq.breakdown_csv(payload).encode('utf-8'), _jiq.breakdown_filename(payload, 'csv'), 'text/csv', name
    except Exception as e:
        print(f"[send-file] journey breakdown failed for {key}: {e}")
        return None


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


# A read that travels with a file is cleaned for the mail door, never
# refused at it (2026-11-03, Armine Meseldzhyan: "football-anchored" in
# the New Heights read tripped the door's 'anchor' token and the file
# did not go). Descriptive uses of a banned word are reworded; a line
# that still carries one is dropped; the file always goes.
_MAIL_SWAPS = (
    (re.compile(r'\b(\w+)-anchored\b', re.I), r'\1-centered'),
    (re.compile(r'\banchored\b', re.I), 'centered'),
    (re.compile(r'\banchors\b', re.I), 'leads'),
    (re.compile(r'\banchor\b', re.I), 'lead'),
    (re.compile(r'\bplaceholder\b', re.I), 'stand-in'),
    (re.compile(r'\bresearch(?:ed|ing)?\b', re.I), 'read'),
    (re.compile(r'\breasoned\b', re.I), 'read'),
)


def mail_safe(text):
    """The text with the mail door's banned tokens reworded or, when a
    line still carries one, that line removed. Empty when nothing safe
    is left."""
    try:
        from . import outbound_mail as _om
        banned = _om.banned_tokens
    except Exception:
        return str(text or '')
    out = []
    for line in str(text or '').split('\n'):
        fixed = line
        for rx, rep in _MAIL_SWAPS:
            fixed = rx.sub(rep, fixed)
        if banned(fixed):
            continue
        out.append(fixed)
    txt = '\n'.join(out)
    txt = re.sub(r'\n{3,}', '\n\n', txt).strip()
    return txt


def _thread_file(last, host):
    """(bytes, filename, mime) for the last thing in the thread when it is
    a file or a read with a table; None otherwise."""
    from . import last_thing as _lt
    try:
        if last.get('kind') == 'file':
            f = last['file']
            extra = []
            try:
                if host.has('subiq_bucket'):
                    extra.append(str(host.subiq_bucket))
            except Exception:
                pass
            extra.append('svod-acquisition')
            blob = _lt.file_bytes(f, host.s3_client, bucket=_bucket(host), extra_buckets=tuple(extra))
            if blob is None:
                return None
            return blob, str(f.get('filename') or 'file'), str(f.get('mime') or 'application/octet-stream')
        if last.get('kind') == 'read':
            import prometheus_analysis as pma
            fname, csv_text = pma.build_generated_csv(last['entry'])
            e = last['entry']
            rng = ''
            try:
                import sys as _sys
                app_mod = _sys.modules.get('app')
                if app_mod is not None and hasattr(app_mod, '_stamp_csv_text'):
                    if e.get('ws') and e.get('we'):
                        rng = f"{app_mod._fmt_study_date(e['ws'])} - {app_mod._fmt_study_date(e['we'])}"
                    elif e.get('wl'):
                        rng = str(e['wl'])
                    csv_text = app_mod._stamp_csv_text(csv_text, rng)
            except Exception:
                pass
            subj = str(e.get('subject') or last.get('subject') or 'Data')
            return csv_text.encode('utf-8'), f"{_clean_name(subj)}.csv", 'text/csv'
    except Exception as ex:
        print(f"[send-file] thread file failed: {ex}")
    return None


def answer(text, uname, *, user, ctx, history, host, send=None, subjects=None, consult=None, stash=None):
    """Payload for the chat, or None when nothing is in hand to send.
    ``send`` is injected by tests; defaults to the house mail door.
    ``subjects`` are the names the reader found in the ask, used when
    the order itself names nothing we can parse. ``consult`` and
    ``stash`` feed the last-thing resolver (the ledger lookup and the
    account's last-file record; tests inject them)."""
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
    plain = ' '.join(str(text or '').split())
    if not named and follows_clarifier(history) and not _SEND_RX.match(plain) and not _SEND_PLAIN_RX.match(plain):
        named = clean_subject(plain)            # the reader answered "Which file?" with the name
    wants_journey = bool(re.search(r"\bjourney\b", plain, re.I))
    journeys = None
    journey_key = ''
    lib_name, lib_key = '', ''
    if named:
        journeys = user_journeys(uname, host, user=user)
        hits = match_journeys(named, journeys)
        if not (wants_journey and hits):
            lib_name, lib_key = _named_library_file(named, host)
        if len(hits) > 1 and not lib_key:
            open_key = open_journey_key(ctx, uname, host)
            on_screen = [j for j in hits if j['key'] == open_key]
            if on_screen:
                hits = on_screen
            else:
                names = '; '.join(j['name'] for j in hits[:6])
                return {'success': True, 'action': 'answer',
                        'reply': f"I hold more than one journey by that name: {names}. Which one should I send?",
                        'followups': [f"Email me the {j['name']} journey" for j in hits[:4]], 'offer_deck': False, 'deck_angle': None, 'subject': named}
        if hits and not lib_key:
            journey_key = hits[0]['key']
            primary = None
    if not journey_key and not lib_key and subjects:
        # the reader's own read of the names in the ask ("mail over the Knots Landing one"): a journey or a
        # library profile that name points at, when it points at exactly one
        journeys = journeys if journeys is not None else user_journeys(uname, host, user=user)
        for sname in subjects:
            h2 = match_journeys(sname, journeys)
            if len(h2) == 1:
                journey_key, primary, named = h2[0]['key'], None, (named or str(sname))
                break
            if not h2:
                lib_name, lib_key = _named_library_file(sname, host)
                if lib_key:
                    named = named or str(sname)
                    break
    last = None
    page_chip = []
    if not named and not journey_key:
        # the very last thing in the thread first (Jenna 2026-11-04)
        from . import last_thing as _lt
        if stash is None:
            try:
                from .legacy import jobs as _jobs
                stash = _jobs._pm_file_stash_read(uname)
            except Exception:
                stash = None
        try:
            last = _lt.resolve(history, consult=consult, stash=stash, bucket=_bucket(host))
        except Exception as ex:
            print(f"[send-file] last-thing resolve failed: {ex}")
            last = None
        if last and last.get('kind') in ('file', 'read'):
            tf = _thread_file(last, host)
            if tf is not None:
                attachments.append(tf)
                subject_name = str(last.get('subject') or tf[1].rsplit('.', 1)[0].replace('_', ' ')).strip()
                words = mail_safe(str(last.get('text') or '').strip()) if last.get('kind') == 'read' or len(str(last.get('text') or '')) > 120 else ''
                body = (f"Here is the file you asked for" if last.get('kind') == 'file' else f"Here is the data you asked for")
                body += (f" on {subject_name}." if subject_name and subject_name.lower() not in tf[1].lower() else '.')
                if words:
                    body += "\n\nThe read it came from:\n\n" + words
                if isinstance(primary, dict) and primary.get('s3_key') and primary.get('name'):
                    page_chip = [f"Email me the {primary['name']} profile"]   # a wrong guess is one tap away
                primary = None
                ctx = dict(ctx or {}, primary=None, cuts=[])
    if not named and not journey_key and not attachments and not (isinstance(primary, dict) and primary.get('s3_key')):
        journey_key = open_journey_key(ctx, uname, host)      # "email me this file" with a Digital Journey open
    if journey_key:
        wb = journey_workbook(journey_key, host)
        if wb:
            blob, fname, mime, jname = wb
            attachments.append((blob, fname, mime))
            subject_name = jname
            body = (f"Here is the {jname} Digital Journey workbook you asked for. It matches the journey in your "
                    "library row for row.")
        else:
            return {'success': True, 'action': 'answer',
                    'reply': 'I could not read that journey just now. Open it in Digital Journey IQ and ask again.',
                    'followups': [], 'offer_deck': False, 'deck_angle': None}
    elif named:
        # a named send: the library file by that name, else the catalog's
        # reads on it as a plain summary; never the open page, never a build
        if not lib_key:
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
    if named or journey_key or (last and last.get('kind') in ('file', 'read') and attachments):
        read = None            # a workbook travels alone; a thread file carries its own read
    elif last and last.get('kind') == 'text' and isinstance(last.get('turn'), dict):
        read = last['turn']    # the words of the last thing ride in the body
    else:
        read = _last_read(history)
    if read is not None:
        rtxt = mail_safe(str(read.get('text') or '').strip())
        if rtxt and attachments:
            body = body + "\n\nThe latest read from this thread:\n\n" + rtxt
        elif rtxt:
            body = rtxt
        elif not attachments:
            read = None
        if not subject_name:
            meta = read.get('meta') if isinstance(read.get('meta'), dict) else {}
            subject_name = str(meta.get('subject') or read.get('subject') or '').strip() or 'your read'
    if not attachments and read is None and not body:
        return {'success': True, 'action': 'answer',
                'reply': (CLARIFIER_LEAD + ' Open the profile or journey you want and ask again, or name it '
                          '("email me the Austin Sprinz profile", "email me the Young Sheldon on Netflix journey") and I will send it.'),
                'followups': [], 'offer_deck': False, 'deck_angle': None}
    thread_file = bool(last and last.get('kind') in ('file', 'read') and attachments)
    subj_line = (f"{subject_name}: your Digital Journey workbook" if journey_key
                 else f"{subject_name}: your Crosswalk file" if attachments else f"{subject_name}: your read from Prometheus")
    sender = send
    if sender is None:
        from . import outbound_mail as _om
        sender = _om.send_user_email
    csv_first = attachments and str(attachments[0][2] or '') == 'text/csv'
    first = attachments[0] if csv_first else None
    rest = attachments[1:] if csv_first else list(attachments)     # a workbook rides as the file it is, never as a .csv part
    r = sender(to=email, subject=subj_line, body="Hi,\n\n" + body + "\n\nPrometheus\nCrosswalk\n",
               instructed=True, caller='user:email_me_this_file',
               csv=(first[0] if first else None), csv_name=(first[1] if first else ''),
               files=rest)
    sent = bool((r or {}).get('sent'))
    what = ('the workbook' if journey_key else (attachments[0][1] if thread_file else ('the profile' + (' and its cut' if len(attachments) > 1 else '')))) if attachments \
        else ('the summary' if named else 'the read')
    where = 'Digital Journey IQ' if journey_key else ('this chat' if thread_file else 'your Select Profile dropdown')
    if not sent:
        # the door's reason is for the log, never for the reader
        print(f"[send-file] not sent to {email}: {str((r or {}).get('reason') or 'mail door closed')[:160]}")
    reply = (f"Sent {what} to {email}." if sent
             else f"I could not send that just now. The file is still in {where}; ask me again in a minute.")
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': (page_chip if sent else []),
            'offer_deck': False, 'deck_angle': None, 'subject': subject_name, 'sent': sent}


__all__ = ['parse', 'answer', 'mail_safe', 'named_subject', 'clean_subject', 'follows_clarifier', 'user_journeys', 'match_journeys', 'open_journey_key', 'journey_workbook', 'CLARIFIER_LEAD']
