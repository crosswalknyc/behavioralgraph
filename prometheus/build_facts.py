"""What a build carries is read from the file, at answer time (Jenna
2026-11-11).

Jessie, thread ebf520b02f, 23:31 UTC, with the finished profile open:
"was the profile run on all 49 of these inputs or did you just add them
to the BRAND field?" The answer came from the model: "All of them were
run, not parked in a label field." Nothing had opened the file. Earlier
the same afternoon she was told a build "carried all 49 placements"
while the file listed 5.

The rule: a question, or a statement, about what a finished or running
build contains (the inputs it was run on, how many, whether a list the
reader sent is in it) is answered from the record and nothing else:

  * the file itself, opened when the question is asked: the inputs it
    lists, the audience it carries, its window, when it was last
    written; a list the reader sent in this thread is checked against it
    one by one;
  * the run record, when the file is not there yet: a build that is
    still running is said to be still building, and nothing is said
    about what it carries;
  * no file and no run: said plainly.

Nothing here is written by a model, nothing is priced, and nothing is
started. ``reply_truth`` takes the same facts (``facts['line']``) to
replace a sentence about a build that a written answer carries.

``wants(text)``                 the words ask what a build carries
``pasted_ids(text)``            the ids a reader pasted, in order
``read_file(key, s3, bucket)``  {'inputs', 'n_inputs', 'projected', 'in_data',
                                'window', 'written', 'name'} or None
``answer(text, ...)``           the reply payload, or None to pass

Reads S3 through the host; no model. Never raises. Regression:
scripts/test_pm_truth_2026_11_11.py.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone, timedelta

__all__ = ['wants', 'pasted_ids', 'read_file', 'answer', 'facts_line']

_HEAD_BYTES = 393216
_THING = r"(?:placements?|inputs?|seeds?|urls?|brand\s+(?:field|input|inputs)|content[_ ]map)"
_BUILD = r"(?:profile|build|file|run|pull|it|this|that)"
_WANTS_RXS = (
    # was the profile run on all 49 of these inputs / did you use all 49 placements / did you just add them to the BRAND field
    re.compile(r"\b(?:was|is|were|are|did|does|do|has|have)\b[^?\n]{0,80}\b(?:run|ran|built|build|use|used|uses|carry|carries|carried|"
               r"include|includes|included|contain|contains|seeded|add|added|load|loaded)\b[^?\n]{0,80}\b" + _THING + r"\b", re.I),
    # how many placements are in the build / went into the profile
    re.compile(r"\bhow\s+many\s+(?:of\s+(?:the|my|those|these)\s+)?(?:\w+\s+){0,3}?" + _THING + r"\b[^?\n]{0,60}\b" + _BUILD + r"\b", re.I),
    # which placements did the build use / what inputs is the profile built on
    re.compile(r"\b(?:which|what)\s+(?:\w+\s+){0,3}?" + _THING + r"\b[^?\n]{0,60}\b(?:" + _BUILD + r")\b[^?\n]{0,40}\b(?:run|ran|built|use|used|uses|"
               r"carry|carries|include|includes)\b", re.I),
    re.compile(r"\bwhat\s+(?:was|is)\s+(?:the\s+)?(?:profile|build|file|it)\s+(?:built|run|seeded)\s+(?:on|from|with)\b", re.I),
    re.compile(r"\b(?:what(?:'s|\s+is)|show\s+me|list)\s+(?:in\s+)?the\s+brand\s+(?:field|input)s?\b", re.I),
)
_NOT_RXS = (
    # an order or an instruction is the build flow's
    re.compile(r"^\W*(?:please\s+)?(?:run|build|queue|re-?run|rebuild|use|add|make|create|pull)\b", re.I),
)
_ID_LINE_RX = re.compile(r"^\s*([A-Za-z0-9_\-]{20,})\s*$")
_ID_INLINE_RX = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])([A-Za-z0-9_\-]{24,})(?![A-Za-z0-9_\-])")
_PT = timezone(timedelta(hours=-8))


def _squash(s):
    return ' '.join(str(s or '').split())


def wants(text):
    """True when the words ask what a build was run on or carries."""
    try:
        t = _squash(text)
        if not t or len(t) > 6000:
            return False
        if any(rx.search(t) for rx in _NOT_RXS) and not t.rstrip().endswith('?'):
            return False
        return any(rx.search(t) for rx in _WANTS_RXS)
    except Exception:
        return False


def pasted_ids(text):
    """The ids a reader pasted (one a line, or run together), in order,
    each once."""
    out, seen = [], set()
    try:
        raw = str(text or '')
        for ln in raw.replace('\r', '\n').split('\n'):
            m = _ID_LINE_RX.match(ln)
            hits = [m.group(1)] if m else _ID_INLINE_RX.findall(ln)
            for h in hits:
                if h not in seen:
                    seen.add(h)
                    out.append(h)
    except Exception:
        return out
    return out


def _thread_ids(text, history, lookback=8):
    """The list the reader is talking about: the ids in this message,
    else the newest earlier message of theirs that pasted any."""
    ids = pasted_ids(text)
    if ids:
        return ids
    seen = 0
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') != 'user':
            continue
        seen += 1
        if seen > lookback:
            break
        ids = pasted_ids(t.get('text') or t.get('content') or '')
        if ids:
            return ids
    return []


def _int(v):
    try:
        return int(round(float(str(v).replace(',', '').strip())))
    except Exception:
        return None


def _when(dt):
    try:
        if dt is None:
            return ''
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(_PT)
        hour = local.strftime('%I:%M %p').lstrip('0')
        return f"{local.strftime('%b')} {local.day}, {local.year} at {hour} PT"
    except Exception:
        return ''


def read_file(key, s3, bucket='dashboard-inputs', name=''):
    """What the profile file records about its own build, read now."""
    try:
        if not key or s3 is None:
            return None
        obj = s3.get_object(Bucket=bucket, Key=str(key), Range=f"bytes=0-{_HEAD_BYTES - 1}")
        blob = obj['Body'].read()
        text = blob.decode('utf-8', 'replace') if isinstance(blob, (bytes, bytearray)) else str(blob)
        rows = {}
        for i, row in enumerate(csv.reader(io.StringIO(text))):
            if i > 400 or not row:
                continue
            col = str(row[0] or '').strip().upper()
            if col in ('BRAND INPUT', 'SAMPLE SIZE', 'SUBJECT') and col not in rows:
                rows[col] = row
            if len(rows) == 3:
                break
        bi = rows.get('BRAND INPUT')
        if not bi or len(bi) < 2:
            return None
        inputs = [x.strip() for x in str(bi[1] or '').split(',') if x.strip()]
        window = ''
        ss = rows.get('SAMPLE SIZE')
        if ss and len(ss) > 1:
            m = re.search(r"\((\d{4}-\d{2}-\d{2})\s+TO\s+(\d{4}-\d{2}-\d{2})\)", str(ss[1]), re.I)
            if m:
                try:
                    a = datetime.strptime(m.group(1), '%Y-%m-%d')
                    b = datetime.strptime(m.group(2), '%Y-%m-%d')
                    window = f"{a.strftime('%b')} {a.day} {a.year} to {b.strftime('%b')} {b.day} {b.year}"
                except Exception:
                    window = ''
        in_data = _int(bi[4]) if len(bi) > 4 else None
        projected = _int(bi[5]) if len(bi) > 5 else None
        return {'key': str(key), 'name': _squash(name), 'inputs': inputs, 'n_inputs': len(inputs), 'in_data': in_data,
                'projected': projected, 'window': window, 'written': _when(obj.get('LastModified')),
                'read_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    except Exception:
        return None


def _page_file(ctx):
    try:
        pc = ctx if isinstance(ctx, dict) else {}
        prim = pc.get('primary') if isinstance(pc.get('primary'), dict) else {}
        key = str(prim.get('s3_key') or prim.get('key') or '').strip()
        name = _squash(prim.get('name') or prim.get('display_name') or '')
        if key.lower().endswith('.csv'):
            return key, name
    except Exception:
        pass
    return '', ''


def _thread_build(history):
    """(run id, subject) of the newest build this thread started."""
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        rid = str(meta.get('run_id') or meta.get('build_run_id') or '').strip()
        if rid:
            return rid, _squash(meta.get('subject') or '')
    return '', ''


def _runs(host, user):
    try:
        if host is not None and host.has('user_runs'):
            return [r for r in (host.user_runs(user, limit=60) or []) if isinstance(r, dict)]
    except Exception:
        pass
    return []


def _norm(s):
    return re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).strip()


_ACTIVE = ('queued', 'pending', 'running', 'working', 'starting', 'tu_done', 'avid_done', 'addon_cuts')


def facts_line(facts, listed=None):
    """One sentence stating what the file lists (the line a written
    answer carries in place of its own statement about the build)."""
    if not isinstance(facts, dict):
        return ''
    n = int(facts.get('n_inputs') or 0)
    line = f"The file lists {n} input{'s' if n != 1 else ''}"
    if listed:
        have = set(facts.get('inputs') or [])
        inside = [x for x in listed if x in have]
        if len(inside) == len(listed):
            line += f", all {len(listed)} of the ones you sent" if len(listed) > 1 else ", the one you sent"
        else:
            line += f"; {len(inside)} of the {len(listed)} you sent are in it and {len(listed) - len(inside)} are not"
    return line + '.'


def answer(text, *, ctx=None, history=None, user=None, uname='', host=None):
    """The reply, read from the record; None when the words do not ask
    what a build carries."""
    try:
        if not wants(text):
            return None
        s3 = host.s3_client if host is not None and host.has('s3_client') else None
        bucket = str(host.bucket) if host is not None and host.has('bucket') and host.bucket else 'dashboard-inputs'
        key, name = _page_file(ctx)
        if key:
            # the page's file is read only for a reader who can open it (the profile page's own gate)
            try:
                from . import faults as _faults
                if host is not None and host.has('can_open_profile') and not host.can_open_profile(user, key) and not _faults.is_probe():
                    key, name = '', ''
            except Exception:
                key, name = '', ''
        rid, subject = _thread_build(history)
        runs = _runs(host, user) if (rid or not key) else []
        run = next((r for r in runs if rid and str(r.get('run_id') or '') == rid), None)
        if not key and run and str(run.get('status') or '').lower() == 'complete':
            key = str(run.get('tu_key') or run.get('tu_s3_key') or run.get('s3_key') or run.get('output_key') or '').strip()
            name = name or _squash(run.get('subject') or subject)
        # a build of this subject that is still running: its file is not there to read
        target = _norm(name or subject)
        building = None
        for r in (runs or _runs(host, user)):
            st = str(r.get('status') or '').lower()
            if st in _ACTIVE and not r.get('tu_key') and target and (_norm(r.get('subject')) == target or (rid and str(r.get('run_id') or '') == rid)):
                building = r
                break
        payload = {'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None}
        if building and not key:
            who = _squash(building.get('subject') or subject or name) or 'That profile'
            payload['reply'] = (f"{who} is still building, so its file is not there to read yet. I will not say what it carries "
                                "until I can open it. Ask me again when it finishes and I will read it from the file.")
            payload['build_facts'] = {'state': 'building', 'run_id': str(building.get('run_id') or '')}
            payload['subject'] = who
            return payload
        facts = read_file(key, s3, bucket, name) if key else None
        if not facts:
            what = name or subject
            payload['reply'] = ((f"I could not open a finished file for {what}" if what else "I do not have a finished file open for that build")
                                + ", so I will not say what it carries. Open the profile in Select Profile and ask me again, "
                                  "or ask for a status update on the build.")
            payload['build_facts'] = {'state': 'no_file'}
            if what:
                payload['subject'] = what
            return payload
        listed = _thread_ids(text, history)
        n = facts['n_inputs']
        label = facts.get('name') or 'this profile'
        lines = [f"I opened the file for {label} just now. This is what it records."]
        shown = facts['inputs'][:6]
        more = n - len(shown)
        lines.append(f"- Inputs the file lists: {n}" + (f" ({', '.join(shown)}" + (f", and {more} more)" if more > 0 else ')') if n and n <= 60 else ''))
        if listed:
            have = set(facts['inputs'])
            missing = [x for x in listed if x not in have]
            if not missing:
                lines.append(f"- Your list: all {len(listed)} of the ones you sent are in the file." if len(listed) > 1
                             else "- Your list: the one you sent is in the file.")
            else:
                head = ', '.join(missing[:5]) + (f", and {len(missing) - 5} more" if len(missing) > 5 else '')
                lines.append(f"- Your list: {len(listed) - len(missing)} of the {len(listed)} you sent are in the file; "
                             f"{len(missing)} are not ({head}).")
        if facts.get('projected'):
            aud = f"- Audience the file carries: {facts['projected']:,} projected US people"
            if facts.get('in_data'):
                aud += f" ({facts['in_data']:,} people in the data)"
            lines.append(aud + (f", {facts['window']}." if facts.get('window') else '.'))
        if facts.get('written'):
            lines.append(f"- File last written: {facts['written']}.")
        lines.append("That is everything the file itself records about its inputs. I am reading the file, not describing the run.")
        payload['reply'] = '\n'.join(lines)
        payload['build_facts'] = dict(facts, line=facts_line(facts, listed), listed=len(listed), state='read')
        payload['build_facts'].pop('inputs', None)
        payload['subject'] = label
        return payload
    except Exception:
        return None
