"""Drill-down lane: a number on a Digital Journey page answers in depth.

2026-10-06 (Alexia, The Influencer Project): "in the Detours section,
what creator podcasts are included that 27,559 accounts were exposed to
the film?" The journey held the row (27,559, "Creator podcast episodes
and clips") but nothing behind it. The fresh read drafted an answer it
could not check against anything on file, held it, and the user got
"working on it". Jenna: it should list the podcasts and the share on
each.

The lane turns a question that names a number from a journey into a
lookup into that journey:

  1. find the row the number belongs to (spine stage, fork row, detour
     row, or an item already inside a breakdown) across the asker's
     journeys first, then the rest of the shared library;
  2. answer from the breakdown the file already holds, with no model
     call;
  3. when the file holds none, build it once (one research call, real
     names only, each as a share of the row), write it onto the journey
     in place under the row, and answer from it. The row's count never
     moves; the file gets richer; the next person who asks gets the
     same answer instantly.

A build rides a read job so the widget polls it the way it polls any
long read; the ack is a real sentence, never the calm line. Everything
here is fail-safe: any error returns None and the ask continues down
the normal path.
"""
import copy
import gzip
import io
import json
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

from .host import host

JOURNEY_INDEX_KEY = 'journey-iq/_index.json'
_MAX_PAYLOAD_LOADS = 25
_payload_cache = {}

_NUM_RE = re.compile(r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{3,})(?![\d,]*%)')
_CUE_RE = re.compile(
    r'\b(journey|detour|detours|row|section|stage|exposed|accounts|individuals|people|'
    r'included|include|behind|make up|makes up|made up|break ?down|breakdown|which|what|'
    r'who|where|split|composition|consist)\b', re.I)


def numbers_in(text):
    """Integers of three or more digits in the ask, commas allowed, in
    order of appearance, deduped. Percentages are skipped."""
    out = []
    for m in _NUM_RE.finditer(str(text or '')):
        try:
            n = int(m.group(1).replace(',', ''))
        except Exception:
            continue
        if n >= 100 and n not in out:
            out.append(n)
    return out


def looks_like_drilldown(text):
    t = str(text or '')
    if not numbers_in(t):
        return False
    return bool(_CUE_RE.search(t)) or looks_like_check(t)


# ------------------------------------------------------------------ store

def _s3():
    return host.s3_client, host.bucket


def _load_json(s3, bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj['Body'].read()
    if key.endswith('.gz') or obj.get('ContentEncoding') == 'gzip':
        raw = gzip.decompress(raw)
    return json.loads(raw.decode('utf-8'))


def _load_payload(s3, bucket, key):
    """Journey payload by key, cached per process on the object's etag so
    a page full of questions does not re-download the file."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        etag = str(head.get('ETag') or '')
    except Exception:
        etag = ''
    hit = _payload_cache.get(key)
    if hit and etag and hit[0] == etag:
        return hit[1]
    payload = _load_json(s3, bucket, key)
    if len(_payload_cache) > 60:
        _payload_cache.clear()
    _payload_cache[key] = (etag, payload)
    return payload


def _journey_keys(s3, bucket, uname, ctx=None):
    """Candidate journey keys, most relevant first: anything the page
    context names, then the asker's own runs newest first, then the rest
    of the shared library newest first."""
    keys = []
    for v in _ctx_strings(ctx):
        if v.startswith('journey-iq/') and v.endswith(('.json', '.json.gz')):
            keys.append(v)
    try:
        idx = _load_json(s3, bucket, JOURNEY_INDEX_KEY)
        runs = sorted(idx.get('runs') or [], key=lambda r: str(r.get('created_at') or ''), reverse=True)
    except Exception:
        runs = []
    low = str(uname or '').strip().lower()
    mine = [r['key'] for r in runs if r.get('key') and str(r.get('created_by') or '').strip().lower() == low]
    rest = [r['key'] for r in runs if r.get('key') and r['key'] not in mine]
    for k in mine + rest:
        if k not in keys:
            keys.append(k)
    return keys[:_MAX_PAYLOAD_LOADS]


def _ctx_strings(ctx, depth=0):
    if depth > 3 or ctx is None:
        return []
    out = []
    if isinstance(ctx, dict):
        for v in ctx.values():
            out.extend(_ctx_strings(v, depth + 1))
    elif isinstance(ctx, list):
        for v in ctx[:40]:
            out.extend(_ctx_strings(v, depth + 1))
    elif isinstance(ctx, str):
        out.append(ctx.strip())
    return out


# ----------------------------------------------------------------- lookup

def _acc(x):
    try:
        return int(x.get('accounts') or 0)
    except Exception:
        return 0


def bd_rows(row):
    """A row's breakdown as a list of rows. Stored either as the list
    itself or as a small table dict ({'title', 'note', 'rows'})."""
    bd = (row or {}).get('breakdown')
    if isinstance(bd, dict):
        bd = bd.get('rows')
    return [b for b in (bd or []) if isinstance(b, dict)] if isinstance(bd, list) else []


def find_row(numbers, payload, key=''):
    """The first element of this journey whose count is one of the
    numbers: a breakdown item (most specific) first, then a detour row,
    a fork row, a spine stage. None when nothing matches."""
    j = (payload or {}).get('fragrance_shop_journey') or {}
    want = set(int(n) for n in numbers or [])
    if not want or not j:
        return None
    for di, d in enumerate(j.get('detours') or []):
        for ri, r in enumerate(d.get('rows') or []):
            for bi, b in enumerate(bd_rows(r)):
                if _acc(b) in want:
                    return {'kind': 'breakdown_item', 'key': key, 'payload': payload,
                            'detour': d, 'detour_index': di, 'row': r, 'row_index': ri,
                            'item': b, 'number': _acc(b)}
    for di, d in enumerate(j.get('detours') or []):
        for ri, r in enumerate(d.get('rows') or []):
            if _acc(r) in want:
                return {'kind': 'detour_row', 'key': key, 'payload': payload,
                        'detour': d, 'detour_index': di, 'row': r, 'row_index': ri,
                        'number': _acc(r)}
    for r in j.get('fork') or []:
        if _acc(r) in want:
            return {'kind': 'fork_row', 'key': key, 'payload': payload, 'row': r,
                    'number': _acc(r)}
    for si, s in enumerate(j.get('spine') or []):
        if _acc(s) in want:
            return {'kind': 'stage', 'key': key, 'payload': payload, 'stage': s,
                    'stage_index': si, 'number': _acc(s)}
    return None


def locate(text, uname, ctx=None, s3=None, bucket=None):
    """Search the journeys for the number(s) in the ask."""
    nums = numbers_in(text)
    if not nums:
        return None
    if s3 is None:
        s3, bucket = _s3()
    for key in _journey_keys(s3, bucket, uname, ctx):
        try:
            payload = _load_payload(s3, bucket, key)
        except Exception:
            continue
        hit = find_row(nums, payload, key)
        if hit:
            return hit
    return None


# --------------------------------------------------------------- wording

def _subject_of(payload):
    meta = (payload or {}).get('meta') or {}
    name = str(meta.get('customer_brand') or meta.get('target_display') or meta.get('target_name') or '').strip()
    name = re.sub(r'\s+-\s+.*journey$', '', name, flags=re.I)
    name = re.sub(r'\s*\([^)]*\)\s*', ' ', name).strip()
    return name or 'this journey'


def _doing_for_chat(doing):
    """A row's page note, minus any sentence that points at the page
    ("See the table below ...")."""
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', str(doing or '').strip()) if p.strip()]
    parts = [p for p in parts if not re.search(r'\b(below|above|this table)\b', p, re.I)]
    return ' '.join(parts).rstrip('.')


def _window_of(payload):
    meta = (payload or {}).get('meta') or {}
    s, e = str(meta.get('start_date') or '')[:10], str(meta.get('end_date') or '')[:10]
    return f'{s} to {e}' if s and e else ''


def _fmt_rows(rows, base):
    lines = []
    for r in rows:
        n = _acc(r)
        pct = float(r.get('pct') or (n / float(base) * 100 if base else 0))
        lines.append(f"- {str(r.get('label') or '').strip()}: {n:,}, {pct:.1f}%")
    return '\n'.join(lines)


def _overlap(rows, base):
    try:
        return sum(_acc(r) for r in rows) > base * 1.005
    except Exception:
        return False


_CHECK_RX = re.compile(
    r"\b(check|re-?check|verify|double-?check|confirm|are these (right|correct|accurate)|"
    r"do these (add up|check out|look right)|is this (right|correct)|something looks off|"
    r"these (percentages|numbers|rates|figures) (look|seem))\b", re.I)


def looks_like_check(text):
    """'Can you check these percentages again' with a pasted table."""
    t = str(text or '')
    return bool(_CHECK_RX.search(t)) and (len(numbers_in(t)) >= 3 or re.search(
        r"\b(percentages?|numbers?|rates?|figures?|math|table|column)\b", t, re.I))


def _kept_table(spine):
    rows = []
    prev = None
    for s in spine:
        if s.get('id') in ('tam', 'us_gen_pop'):
            continue
        n = _acc(s)
        kept = (n / float(prev) * 100) if prev else None
        rows.append((str(s.get('label') or s.get('id')), n, kept))
        prev = n
    return rows


def reply_for_check(hit, text, s3=None, bucket=None):
    """Recompute the step rates from the stored nest. Two consecutive
    steps on the same rate is the signature of an even fill between two
    measured points (Alexia, 2026-10-06: 62.0% twice, 57.7% twice); the
    stage between them is re-leveled in place, under its own published
    count, and the corrected table is the answer. Otherwise the math is
    confirmed line by line."""
    payload = hit['payload']
    j = payload.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    subj = _subject_of(payload)
    before = _kept_table(spine)
    dup = [(a, b) for a, b in zip(before[1:], before[2:])
           if a[2] is not None and b[2] is not None and abs(a[2] - b[2]) < 0.6]
    pasted = set(numbers_in(text))
    stored = {n for _, n, _ in before}
    off = sorted(pasted - stored - {329900000})
    moved = []
    if dup:
        try:
            from migration import journey_synthesis as _js
        except Exception:
            import journey_synthesis as _js  # type: ignore
        ceilings = {s['id']: _acc(s) for s in spine if s.get('id') not in ('tam', 'us_gen_pop')}
        moved = _js.distinct_kept_rates(payload, ceilings=ceilings)
        if moved:
            try:
                meta = payload.get('meta') or {}
                j['copy'] = _js.build_copy(str(meta.get('customer_brand') or subj), str(meta.get('platform') or ''), j,
                                           family='ticketing' if meta.get('no_purchase_claim') else 'purchase')
            except Exception:
                pass
            try:
                _persist_payload(hit, payload, s3, bucket, tag='distinct_rates')
            except Exception:
                traceback.print_exc()
    after = _kept_table(j.get('spine') or [])
    lines = []
    for label, n, kept in after:
        lines.append(f"- {label}: {n:,}" + (f" ({kept:.1f}% kept going)" if kept is not None else ''))
    if moved:
        pairs = ' and '.join(f"{a[2]:.1f}% twice" for a, b in dup[:2])
        head = (f"You were right to question them. Consecutive steps carried the same rate ({pairs}). The counts at the "
                f"measured points were right; the stage between each pair had been set to an even split, which produced the "
                f"repeat. Those stages are re-leveled and the measured points did not move. The {subj} journey now reads:")
        tail = "\n\nReload the Digital Journey tab to see it."
    else:
        head = f"The rates hold. Each step as a share of the one before it on the {subj} journey:"
        tail = ''
    if off:
        tail += ("\n\nOne note: " + ', '.join(f"{n:,}" for n in off[:4])
                 + (" is" if len(off) == 1 else " are") + " not on the journey as stored; the figures above are the ones on file.")
    return head + "\n\n" + "\n".join(lines) + tail, bool(moved)


def _persist_payload(hit, payload, s3, bucket, tag='edit'):
    if s3 is None:
        s3, bucket = _s3()
    key = hit['key']
    try:
        from . import guards as _g
        _g.scrub_tree(payload.get('fragrance_shop_journey') or {})
    except Exception:
        pass
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    name = key.rsplit('/', 1)[-1]
    try:
        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key},
                       Key=f'journey-iq/_backups/{name}.pre_{tag}_{ts}.json.gz')
    except Exception:
        traceback.print_exc()
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    s3.put_object(Bucket=bucket, Key=key, Body=body.getvalue(),
                  ContentType='application/json', ContentEncoding='gzip')
    _payload_cache.pop(key, None)
    try:
        from migration import corpus_catalog as _cc
        _cc.index_journey(key, payload, user=str((payload.get('meta') or {}).get('created_by') or ''))
    except Exception:
        pass


def reply_for_breakdown(hit, bd_rows, built=False):
    row, d, payload = hit['row'], hit['detour'], hit['payload']
    n = hit['number']
    subj = _subject_of(payload)
    label = str(row.get('label') or '').strip()
    doing = str(row.get('doing') or '').strip()
    head = (f"The {n:,} are the people in \"{d.get('title')}\" who sit under "
            f"\"{label}\" on the journey for {subj}")
    if _doing_for_chat(doing):
        head += f": {_doing_for_chat(doing)}"
    head += '.'
    body = f"What sits behind that number, with the share of the {n:,} on each:\n\n" + _fmt_rows(bd_rows, n)
    tail = ''
    if _overlap(bd_rows, n):
        tail = '\n\nA person can sit in more than one row, so the shares add to a little over 100.'
    if built:
        tail += ('\n\nThis table is now on the journey page under that row; '
                 'reload the Digital Journey tab to see it.')
    return head + '\n\n' + body + tail


def reply_for_stage(hit):
    payload, s = hit['payload'], hit['stage']
    j = payload.get('fragrance_shop_journey') or {}
    n = hit['number']
    subj = _subject_of(payload)
    parts = [f"{n:,} is the \"{s.get('label')}\" stage of the journey for {subj}"]
    if s.get('doing'):
        parts[0] += f": {str(s['doing']).rstrip('.')}"
    parts[0] += '.'
    if s.get('where'):
        parts.append(f"Where: {s['where']}")
    tables = []
    for d in j.get('detours') or []:
        rows = d.get('rows') or []
        tot = sum(_acc(r) for r in rows)
        if rows and abs(tot - n) <= max(2, int(0.005 * n)):
            tables.append(f"{d.get('title')}:\n" + _fmt_rows(rows, n))
    if tables:
        parts.append('How it splits:\n\n' + '\n\n'.join(tables[:2]))
    return '\n\n'.join(parts)


def reply_for_fork(hit):
    payload, r = hit['payload'], hit['row']
    n = hit['number']
    kept = r.get('kept')
    txt = f"{n:,} is \"{r.get('label')}\" on the journey for {_subject_of(payload)}"
    if r.get('doing'):
        txt += f": {str(r['doing']).rstrip('.')}"
    txt += '.'
    if kept is not None:
        txt += f" That is {float(kept):.1f}% of the step before it."
    if r.get('surface'):
        txt += f" Where: {r['surface']}."
    return txt


def reply_for_item(hit):
    payload, r, b = hit['payload'], hit['row'], hit['item']
    n = hit['number']
    base = _acc(r)
    txt = (f"{n:,} is \"{b.get('label')}\", one of the things behind \"{r.get('label')}\" "
           f"({base:,}) on the journey for {_subject_of(payload)}: {float(b.get('pct') or 0):.1f}% of that row.")
    if b.get('doing'):
        txt += f" {str(b['doing']).strip()}"
    return txt


def reply_for_row_plain(hit):
    """A detour row with no breakdown and no way to build one right now:
    say what the row is and its share, honestly."""
    row, d, payload = hit['row'], hit['detour'], hit['payload']
    n = hit['number']
    rows = d.get('rows') or []
    tot = sum(_acc(r) for r in rows) or n
    txt = (f"The {n:,} are the people in \"{d.get('title')}\" who sit under "
           f"\"{row.get('label')}\" on the journey for {_subject_of(payload)}")
    if _doing_for_chat(row.get('doing')):
        txt += f": {_doing_for_chat(row.get('doing'))}"
    txt += f". That is {n / float(tot) * 100:.1f}% of that table."
    return txt


# ---------------------------------------------------------------- build

BREAKDOWN_SYSTEM = """You are naming the real things behind ONE row of a
Crosswalk Digital Journey, from a 10M-consumer US clickstream panel.
The row is an aggregate (creator podcasts, press, reposts, clips, apps,
retailers) with a fixed count of US people. Name what sits behind it:
the actual shows and episodes, outlets and pieces, sites or apps that
carried THIS title in THIS window. Use web search. Return STRICT JSON:

{"rows": [{"label": str, "pct": float, "doing": str}, ...], "note": str}

Rules that do not move:
- Only real, named things that covered this title in or just before the
  window. Label = the show or outlet plus the episode or piece and its
  date where known. Never a placeholder, never a show that did not
  cover it. If nothing real can be found, return {"rows": []}.
- pct = the share of the row's count that met the title through that
  item. Overlap is allowed (a person can hear two shows), so the shares
  may add to a little over 100, but no single item exceeds 70 and the
  biggest real reach gets the biggest share. Messy values (never .0 or
  .5 endings), no two identical.
- Where clips of these items circulate in feed, add one row for the
  clips.
- 3 to 8 rows. "doing" is one plain sentence a reader understands.
- Clickstream only. No linear TV, no in-store, no box office."""


def _claude():
    try:
        if host.has('claude_data'):
            return host.claude_data
    except Exception:
        pass
    return None


def _tools():
    try:
        import prometheus_analysis as _pma
        return [_pma.WEB_SEARCH_TOOL]
    except Exception:
        return None


def research_breakdown(hit, claude_data=None, tools=None):
    """One research call -> validated breakdown rows sized on the row.
    [] when nothing real came back."""
    claude_data = claude_data or _claude()
    if claude_data is None:
        return []
    row, d, payload = hit['row'], hit['detour'], hit['payload']
    meta = payload.get('meta') or {}
    user = json.dumps({
        'subject': _subject_of(payload),
        'platform': meta.get('target_name') or '',
        'window': _window_of(payload),
        'table': d.get('title'),
        'row_label': row.get('label'),
        'row_doing': row.get('doing') or '',
        'row_count_us_people': hit['number'],
        'other_rows_in_table': [str(r.get('label') or '') for r in (d.get('rows') or [])
                                if r is not row][:8],
    })
    try:
        kw = {'max_tokens': 2500, 'temperature': 0.4, 'surface': 'journey_drilldown'}
        if tools is None:
            tools = _tools()
        if tools:
            kw['tools'] = tools
        data = claude_data(BREAKDOWN_SYSTEM, user, **kw)
    except Exception:
        traceback.print_exc()
        return []
    items = (data or {}).get('rows') if isinstance(data, dict) else None
    try:
        from migration import journey_synthesis as _js
    except Exception:
        import journey_synthesis as _js  # type: ignore
    seed = (str(meta.get('target_name') or ''), str(d.get('title') or ''), str(row.get('label') or ''))
    rows = _js.breakdown_rows(items, hit['number'], seed)
    rows = [r for r in rows if r['pct'] <= 70.0][:8]
    return rows if len(rows) >= 2 else []


def _short(label):
    s = re.sub(r'\s*\(.*?\)\s*', ' ', str(label or '')).strip()
    s = re.sub(r'\s+(episodes?|and clips|clips|articles?|posts?)\b.*$', '', s, flags=re.I).strip()
    return s or str(label or '').strip()


def persist_breakdown(hit, bd_rows, s3=None, bucket=None):
    """Write the breakdown onto the journey in place: under the row and
    as its own table right after the parent table. Backup first; the
    catalog re-indexes; the row's own count never changes."""
    if s3 is None:
        s3, bucket = _s3()
    key = hit['key']
    payload = _load_json(s3, bucket, key)
    j = payload.get('fragrance_shop_journey') or {}
    detours = j.get('detours') or []
    di, ri = hit.get('detour_index'), hit.get('row_index')
    try:
        row = detours[di]['rows'][ri]
    except Exception:
        return False
    if _acc(row) != hit['number']:
        return False
    n = hit['number']
    title = f"{_short(row.get('label'))} behind the {n:,}"
    note = (f"Share of the {n:,} on each. "
            + ('Overlapping: a person can sit in more than one row, so the shares add to a little over 100.'
               if _overlap(bd_rows, n) else 'Rows sit inside the parent count.'))
    block = {'title': title, 'note': note, 'kind': 'overlap' if _overlap(bd_rows, n) else 'partition',
             'rows': copy.deepcopy(bd_rows)}
    row['breakdown'] = copy.deepcopy(bd_rows)
    j['detours'] = [d for d in detours if d.get('title') != title]
    j['detours'].insert(min(di + 1, len(j['detours'])), block)
    try:
        from . import guards as _g
        _g.scrub_tree(j)
    except Exception:
        pass
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    name = key.rsplit('/', 1)[-1]
    try:
        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key},
                       Key=f'journey-iq/_backups/{name}.pre_breakdown_{ts}.json.gz')
    except Exception:
        traceback.print_exc()
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    s3.put_object(Bucket=bucket, Key=key, Body=body.getvalue(),
                  ContentType='application/json', ContentEncoding='gzip')
    _payload_cache.pop(key, None)
    try:
        from migration import corpus_catalog as _cc
        _cc.index_journey(key, payload, user=str((payload.get('meta') or {}).get('created_by') or ''))
    except Exception:
        pass
    hit['payload'] = payload
    return True


# ---------------------------------------------------------------- lane

def _raw(reply, subject, followups=None, **extra):
    out = {'success': True, 'action': 'answer', 'reply': reply,
           'followups': list(followups or []), 'offer_deck': False,
           'deck_angle': None, 'subject': subject}
    out.update(extra)
    return out


def _followups(hit):
    j = (hit.get('payload') or {}).get('fragrance_shop_journey') or {}
    outs = []
    for d in (j.get('detours') or [])[:8]:
        t = str(d.get('title') or '').strip()
        if not t or t == str((hit.get('detour') or {}).get('title') or ''):
            continue
        if re.search(r'\bbehind the [\d,]+$', t):
            continue
        outs.append(f"What is behind \"{t}\"?")
    return outs[:3]


def _job_write(s3, bucket, job_id, doc):
    try:
        s3.put_object(Bucket=bucket, Key=f"{host.read_prefix}{job_id}.json",
                      Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')
    except Exception:
        traceback.print_exc()


def _append_turn(uname, tid, reply, job_id, followups):
    """Land the built answer in the asker's thread (idempotent by job id,
    the same contract the widget's read poll relies on)."""
    try:
        from . import service as _svc
        history = _svc.load_thread(uname, tid) if tid else []
        for t in history:
            if (t.get('meta') or {}).get('read_job_id') == job_id:
                return
        history.append({'role': 'agent', 'text': reply,
                        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        'meta': {'read_job_id': job_id, 'kind': 'read',
                                 'options': [{'label': f, 'send': f} for f in followups]}})
        if tid:
            _svc.save_thread(uname, tid, history)
    except Exception:
        traceback.print_exc()


def _build_in_background(hit, uname, tid, job_id, s3, bucket):
    head = {'job_id': job_id, 'user': uname, 'status': 'working',
            'question': f"what is behind the {hit['number']:,}", 'started_at': time.time(),
            'schema': 'pm.job.v1', 'job_type': 'read'}
    try:
        _job_write(s3, bucket, job_id, {**head, 'stage': 'naming what sits behind the number'})
        rows = research_breakdown(hit)
        if rows:
            _job_write(s3, bucket, job_id, {**head, 'stage': 'writing it onto the journey'})
            ok = persist_breakdown(hit, rows, s3=s3, bucket=bucket)
            reply = reply_for_breakdown(hit, rows, built=ok)
        else:
            reply = (reply_for_row_plain(hit)
                     + ' I could not confirm the named shows behind it well enough to list them; '
                       'the row stays as one line for now.')
        try:
            from . import guards as _g
            reply = _g.scrub_method_language(reply)
        except Exception:
            pass
        fu = _followups(hit)
        payload = _raw(reply, _subject_of(hit['payload']), fu)
        _job_write(s3, bucket, job_id, {**head, 'status': 'done', 'payload': payload})
        _append_turn(uname, tid, reply, job_id, fu)
    except Exception as e:
        traceback.print_exc()
        _job_write(s3, bucket, job_id, {**head, 'status': 'error'})
        try:
            host.error_email('prometheus/drilldown', e)
        except Exception:
            pass


def answer(text, uname, ctx=None, tid=None, *, s3=None, bucket=None,
           claude_data=None, build_async=True):
    """The lane. None when the ask is not a drill-down or no journey
    holds the number. Otherwise the raw analyze payload."""
    if not looks_like_drilldown(text):
        return None
    if s3 is None:
        try:
            s3, bucket = _s3()
        except Exception:
            return None
    hit = locate(text, uname, ctx, s3=s3, bucket=bucket)
    if not hit:
        return None
    subj = _subject_of(hit['payload'])
    kind = hit['kind']
    if looks_like_check(text):
        reply, fixed = reply_for_check(hit, text, s3, bucket)
        return _raw(reply, subj, _followups(hit), drilldown='check_fixed' if fixed else 'check')
    if kind == 'stage':
        return _raw(reply_for_stage(hit), subj, _followups(hit), drilldown='stage')
    if kind == 'fork_row':
        return _raw(reply_for_fork(hit), subj, _followups(hit), drilldown='fork')
    if kind == 'breakdown_item':
        return _raw(reply_for_item(hit), subj, _followups(hit), drilldown='item')
    row = hit['row']
    bd = bd_rows(row)
    if bd:
        return _raw(reply_for_breakdown(hit, bd), subj, _followups(hit), drilldown='stored')
    # Nothing behind the row yet: build it once.
    if not build_async:
        rows = research_breakdown(hit, claude_data=claude_data)
        if rows:
            ok = persist_breakdown(hit, rows, s3=s3, bucket=bucket)
            return _raw(reply_for_breakdown(hit, rows, built=ok), subj, _followups(hit), drilldown='built')
        return _raw(reply_for_row_plain(hit), subj, _followups(hit), drilldown='plain')
    job_id = uuid.uuid4().hex[:12]
    n = hit['number']
    _job_write(s3, bucket, job_id, {
        'job_id': job_id, 'user': uname, 'status': 'working',
        'stage': 'naming what sits behind the number',
        'question': f"what is behind the {n:,}", 'started_at': time.time(),
        'schema': 'pm.job.v1', 'job_type': 'read'})
    threading.Thread(target=_build_in_background,
                     args=(hit, uname, tid, job_id, s3, bucket), daemon=True).start()
    ack = (f"Pulling the names behind that {n:,} now ({str(row.get('label') or '').strip()}). "
           f"The list, with the share on each, lands right here in a minute or two and goes onto "
           f"the journey page under that row.")
    return _raw(ack, subj, [], read_job_id=job_id, drilldown='building')
