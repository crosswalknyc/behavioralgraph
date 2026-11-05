"""The bulk list feeder (2026-11-05): walks every active bulk ledger
(prometheus.bulk_pull) and keeps a capped number of Profile IQ builds in
flight on the profile engine.

It runs where it survives: a thread of the read worker on the build
server (migration/prometheus_read_worker.py starts ``run_forever``),
which has the web app imported (the interpret step, the spec converter,
the wallet), the engine's queue address and secret, and a drain-aware
restart. Nothing here is a Render request thread. The ledger on S3 is
the only state: a restart re-reads it, finds the rows already posted
(their run ids were minted at confirm and the engine knows them) and
carries on.

Each tick, per active ledger:

  1. reconcile: every posted row is checked against the engine's status
     (complete -> done with its keys; failed / cancelled -> failed and
     refunded; unknown after a grace period -> queued again, a third
     miss -> failed and refunded); a row waiting on another run of the
     same subject (the engine's per-subject lock) is reused when that
     run lands;
  2. throttle: nothing is posted while the engine has a backlog
     (interactive builds come first), while the ledger is backing off
     (a 429 / 5xx from the queue), or while the bulk rows in flight
     across every list sit at the cap;
  3. feed: the next queued rows go through the real interpret step to a
     complete spec; a row the library already holds (an existing_match,
     the library's own model) is reused and never built; the body
     carries the row's pre-minted run id, ``origin: bulk`` on the spec
     (the engine's batch lane; never a flag a user can set) and the
     ``bulk`` block the worker reports back on;
  4. digest: finished rows since the last digest go to the requester
     in one email every DIGEST_EVERY completions or every hour, plus a
     final summary; a probe identity or a seat with no email gets the
     record without the send.

Everything is fail-safe: a model read that fails retries the row later
and gives up after three tries with the partner-safe line and a refund;
a queue that is down backs the ledger off; an exception in one ledger
never stops the next.
"""
from __future__ import annotations

import importlib.util
import os
import re
import secrets
import threading
import time
import traceback

from . import bulk_pull as BP
from . import twin_url_list as _TUL

TICK_S = float(os.environ.get('BULK_FEEDER_TICK_S', '45'))
MAX_POSTS_PER_TICK = int(os.environ.get('BULK_FEEDER_MAX_POSTS', '4'))
INTERPRET_STALE_S = 20 * 60          # a row left 'interpreting' this long was a crashed feeder
POST_GRACE_S = 10 * 60               # a posted row the engine does not know after this long never landed
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 30 * 60.0
SOURCE = 'bulk_pull'
TAG = 'bulk-pull'


def _log(msg):
    print(f"[bulk-feeder] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ---------------------------------------------------------------- engine
def queue_health(health_fn=None):
    """{'pending': n, 'running': n} from the engine, or None when unreachable."""
    if health_fn is not None:
        try:
            return health_fn()
        except Exception:
            return None
    url, secret = _TUL._queue_env()
    if not url:
        return None
    try:
        import requests
        r = requests.get(f"{url}/synth/health", timeout=10)
        q = (r.json() or {}).get('queue') or {} if r.status_code == 200 else {}
        return {'pending': int(q.get('pending') or 0), 'running': int(q.get('running') or 0)}
    except Exception:
        return None


def _post_result(body, post=None):
    """(run_id, code, err) for one queue post: code 200 on success; 409
    with the other run's id on the per-subject lock; 0 on a connection
    failure."""
    url, secret = _TUL._queue_env()
    if post is None:
        if not url or not secret:
            return '', 0, 'the queue is not configured here'
        import requests

        def post(b):
            r = requests.post(f"{url}/synth/queue", json=b, timeout=30, headers={'X-Synth-Auth': secret, 'Content-Type': 'application/json'})
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {'error': r.text[:400]}
    try:
        code, doc = post(body)
    except Exception as e:
        return '', 0, f"queue post failed: {e}"
    doc = doc if isinstance(doc, dict) else {}
    if code == 200:
        return str(doc.get('run_id') or body.get('run_id') or ''), 200, ''
    err = str(doc.get('error') or doc)[:300]
    other = str(doc.get('existing_run_id') or '')
    return other, code, err


def run_status(run_id, status_fn=None):
    return _TUL.profile_status(run_id, status_fn=status_fn)


def _keys_of(doc):
    tu = str(doc.get('tu_key') or doc.get('tu_s3_key') or doc.get('s3_key') or '')
    av = str(doc.get('avid_key') or doc.get('avid_s3_key') or '')
    return tu, av


# ---------------------------------------------------------------- refunds
def _parent_module(name):
    """A parent-repo module by file (the build server has the parent at
    the repo root; bg-webapp's own ``migration`` package shadows it)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (os.environ.get('FINISHED_CODES_ROOT') or '', os.path.dirname(here), '/root/finished_codes'):
        path = os.path.join(root, 'migration', f'{name}.py') if root else ''
        if path and os.path.exists(path):
            try:
                spec = importlib.util.spec_from_file_location(f'_parent_{name}', path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
            except Exception:
                traceback.print_exc()
    return None


def refund_row(ledger, row, reason, refund_fn=None, log=_log):
    """Give one row's charge back, once (idempotent by the row's run id
    through the shared refund helper; the ledger flag is the second
    guard). Returns True when a refund was recorded now."""
    if not row.get('charged') or row.get('refunded'):
        return False
    uname = str((ledger.get('requester') or {}).get('username') or '')
    credits = int((ledger.get('charged') or {}).get('per_build') or BP.PROFILE_CREDITS)
    usd = float((ledger.get('charged') or {}).get('usd_each') or 0.0)
    ok = False
    try:
        if refund_fn is not None:
            ok = bool(refund_fn(uname, credits, row.get('run_id'), reason, usd))
        else:
            mod = _parent_module('credit_refunds')
            if mod is not None:
                res = mod.refund_run_credits(str(row.get('run_id')), decision='new_build', reason=reason, username=uname, credits=credits) or {}
                ok = bool(res.get('refunded') or res.get('already_refunded'))
            else:
                import sys as _sys
                app = _sys.modules.get('app')
                fn = getattr(app, 'refund_credit', None)
                ok = bool(fn(uname, credits=credits, reason=reason)) if fn else False
    except Exception:
        traceback.print_exc()
        ok = False
    if ok:
        row['refunded'] = True
        log(f"{ledger.get('bulk_id')} row {row.get('i')} ({row.get('subject')}): {credits} credit(s) back to {uname}: {reason}")
    else:
        log(f"{ledger.get('bulk_id')} row {row.get('i')}: refund did not record ({reason}); the row keeps charged=True for the next tick")
    return ok


# ---------------------------------------------------------------- the row
def row_prompt(ledger, row):
    subj = str(row.get('subject') or '').strip()
    w = BP._normalize_window(ledger.get('window'))
    lines = [f"run a profile on {subj}", '',
             (f"VERIFIED CONTEXT (treat as fact): this is one row of an uploaded list of subjects for Profile IQ. Build the Total Universe of "
              f"{subj} as the subject itself, on the window {w['start']} to {w['end']}, with no addon_cuts. If the catalog candidates already hold a "
              f"Total Universe of exactly this subject, answer existing_match with its key; a different subject that merely shares a word is not a match.")]
    hint = str(row.get('category_hint') or '').strip()
    if hint:
        lines.append(f"The list tags {subj} as: {hint}.")
    notes = str(row.get('notes') or '').strip()
    if notes:
        lines.append(f"The list's note on {subj}: {notes}")
    return '\n'.join(lines)


def interpret_row(ledger, row, *, draft_fn=None, catalog=None, usage_extras=None):
    """The draft for one row: the real interpret step with the catalog's
    candidates (so an existing_match can be answered), or the injected
    ``draft_fn(prompt, candidates)`` in tests. None on failure."""
    prompt = row_prompt(ledger, row)
    candidates = []
    try:
        from prometheus.legacy import chat as _chat
        cat = catalog if catalog is not None else _chat._profile_catalog_for_chat()
        candidates = _chat._shortlist_profile_matches(str(row.get('subject') or ''), cat) or []
    except Exception:
        candidates = []
    if draft_fn is not None:
        try:
            d = draft_fn(prompt, candidates)
            return d if isinstance(d, dict) and d else None
        except Exception:
            traceback.print_exc()
            return None
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return None
    try:
        from prometheus.legacy import chat as _chat
        from prometheus import smarter as _smart
        try:
            from iq_rankers import MASTER_CATEGORIES
        except Exception:
            MASTER_CATEGORIES = {}
        sys_p, usr_p = _chat._synth_chat_interpret_prompts(prompt, chat_history=None, master_categories=MASTER_CATEGORIES, candidate_matches=candidates)
        try:
            sys_p, _dropped = _smart.prune_rulebook(sys_p, prompt, candidates)
        except Exception:
            pass
        try:
            ceiling = _smart.draft_output_ceiling(prompt)
        except Exception:
            ceiling = 8192
        fresh = _chat._H._run_nflx_claude_agent(system_prompt=sys_p, user_prompt=usr_p, max_tokens=ceiling, temperature=0.4,
                                                 model=getattr(_chat, '_SYNTH_CHAT_INTERPRET_MODEL', None), usage_tag=('interpret', TAG, usage_extras))
        fd = (fresh or {}).get('data') or {}
        if isinstance(fd, list):
            fd = next((d for d in fd if isinstance(d, dict)), {})
        if (fresh or {}).get('success') and isinstance(fd, dict) and fd:
            return fd
        _log(f"{ledger.get('bulk_id')} row {row.get('i')}: interpret came back empty: {str(fd)[:200]}")
        return None
    except Exception:
        traceback.print_exc()
        return None


def _fits(key_or_name, subject):
    try:
        from migration.digital_twin_synthesis import profile_fits_subject
        return bool(profile_fits_subject(str(key_or_name or ''), subject))
    except Exception:
        return BP._norm(BP._base_name('', key_or_name)) == BP._norm(subject)


def reuse_key_from_draft(draft, subject):
    """The library key the draft points at when the subject is already
    on file (an existing_match, or a refresh / cut of a base profile of
    this very subject: the library serves it, never rebuilds), else ''."""
    d = draft if isinstance(draft, dict) else {}
    dec = str(d.get('decision') or '').strip().lower()
    key = str(d.get('existing_match_s3_key') or d.get('parent_s3_key') or '').strip()
    if not key:
        return ''
    if dec == 'existing_match':
        return key
    if dec in ('time_shifted_refresh', 'derive_cut', 'cut_needs_parent') and _fits(key, subject) and ' - ' not in key.rsplit('/', 1)[-1]:
        return key
    return ''


def _canonical(cat, hint=''):
    try:
        from . import twin_named_pull as _NP
        if hint and _NP.is_canonical_category(hint):
            return str(hint).strip().upper()
        if _NP.is_canonical_category(cat):
            return str(cat).strip().upper()
        if hint and _NP.is_canonical_category(_NP.fallback_category(hint.lower())):
            return _NP.fallback_category(hint.lower())
        return 'MEDIA'
    except Exception:
        return str(cat or hint or 'MEDIA').strip().upper() or 'MEDIA'


def _messy(subject, value, default_if_missing=9873):
    try:
        from . import twin_named_pull as _NP
        return _NP._messy(subject, value, default_if_missing=default_if_missing)
    except Exception:
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            v = int(default_if_missing)
        return v + 1 if v % 10 == 0 else v


def _draft_hygiene(draft, ledger, row):
    """The parts the list decides, whatever the model said: the subject
    (the row's name, a casing variant of it allowed), a fresh build, no
    cuts, the list's window, the avid choice, a canonical category."""
    d = dict(draft or {})
    subj = str(row.get('subject') or '').strip()
    ds = ' '.join(str(d.get('subject') or '').split())
    d['subject'] = ds if ds and BP._norm(ds) == BP._norm(subj) else subj
    d.pop('name', None)
    d['decision'] = 'new_build'
    d['run_avid'] = bool(row.get('run_avid', True)) and bool(ledger.get('run_avid', True))
    for k in ('addon_cuts', 'existing_match_s3_key', 'existing_match_display_name', 'existing_match_days_old', 'subiq', 'derive_type', 'cut_label',
              'universe_mode', 'universe_note', 'event_window', 'quarter_cuts'):
        d.pop(k, None)
    d['addon_cuts'] = []
    d['brand_category'] = _canonical(d.get('brand_category'), str(row.get('category_hint') or ''))
    w = BP._normalize_window(ledger.get('window'))
    d['date_range'] = {'start': w['start'], 'end': w['end']}
    d['date_range_explicit'] = True
    rows = []
    for r in (d.get('subject_rows') or []):
        try:
            col, val = (r[0], r[1]) if isinstance(r, (list, tuple)) else (r.get('column'), r.get('value'))
        except Exception:
            continue
        if str(col or '').strip() and str(val or '').strip():
            rows.append([col, val])
    if not rows:
        rows = [[d['brand_category'], d['subject']]]
    d['subject_rows'] = rows
    if not isinstance(d.get('tu_demos'), dict) or not d.get('tu_demos'):
        return None      # no demos: the engine cannot size the universe; the row retries
    if not isinstance(d.get('avid_demos'), dict) or not d.get('avid_demos'):
        d['avid_demos'] = d['tu_demos']
    return d


def row_body(ledger, row, draft, *, spec_from_draft=None):
    """The queue body for one row, or None when the draft cannot become
    a build (the converter refused it)."""
    d = _draft_hygiene(draft, ledger, row)
    if d is None:
        return None
    if spec_from_draft is None:
        from prometheus.legacy.chat import _spec_from_draft as spec_from_draft  # type: ignore
    spec = dict(spec_from_draft(d) or {})
    w = BP._normalize_window(ledger.get('window'))
    spec['name'] = ' '.join(str(spec.get('name') or d['subject']).split())[:140] or d['subject']
    if not str(spec.get('file_stem') or '').strip():
        spec['file_stem'] = re.sub(r"[^A-Za-z0-9]+", '_', spec['name']).strip('_')[:80] or 'Subject'
    spec['brand_category'] = _canonical(spec.get('brand_category') or d['brand_category'], str(row.get('category_hint') or ''))
    spec['date_range'] = f"{w['start']} TO {w['end']}"
    spec['addon_cuts'] = []
    spec['run_avid'] = d['run_avid']
    if not isinstance(spec.get('subject_rows'), list) or not spec['subject_rows']:
        spec['subject_rows'] = [(spec['brand_category'], spec['name'])]
    if not isinstance(spec.get('tu_demos'), dict) or not spec['tu_demos']:
        spec['tu_demos'] = d['tu_demos']
    if not isinstance(spec.get('avid_demos'), dict) or not spec['avid_demos']:
        spec['avid_demos'] = spec['tu_demos']
    spec['subject_raw_tu'] = _messy(spec['name'], spec.get('subject_raw_tu') or d.get('subject_raw_tu'))
    av = spec.get('subject_raw_avid') or d.get('subject_raw_avid')
    spec['subject_raw_avid'] = min(_messy(f"{spec['name']}|avid", av, default_if_missing=2937), spec['subject_raw_tu'])
    # the engine's batch lane: set here by the feeder, never by a request field (no-external-overrides)
    spec['origin'] = 'bulk'
    req = ledger.get('requester') or {}
    uname = str(req.get('username') or '').strip()
    email = str(req.get('email') or '').strip()
    body = {'run_id': str(row.get('run_id')), 'decision': 'new_build', 'run_avid': d['run_avid'], 'spec': spec,
            'user_email': email or uname or 'prometheus', 'username': uname, 'email_to': '',
            'prompt': (str(ledger.get('ask_text') or '') or f"Profile IQ list: {spec['name']}")[:4000],
            'source': SOURCE, 'requested_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'bulk': {'bulk_id': str(ledger.get('bulk_id')), 'row': int(row.get('i') or 0), 'credits': int((ledger.get('charged') or {}).get('per_build') or BP.PROFILE_CREDITS) if row.get('charged') else 0,
                     'usd': float((ledger.get('charged') or {}).get('usd_each') or 0.0) if row.get('charged') else 0.0, 'username': uname}}
    try:
        from . import seams as _seams
        body = _seams.tag_queue(body)
    except Exception:
        pass
    return body


_VALIDATOR = None


def listener_validator():
    """The engine's own spec check, lifted from the listener file when the
    parent repo is reachable (it imports Flask and creates the queue
    directories at import, so it is never imported whole)."""
    global _VALIDATOR
    if _VALIDATOR is not None:
        return _VALIDATOR or None
    import ast
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (os.environ.get('FINISHED_CODES_ROOT') or '', os.path.dirname(here), '/root/finished_codes', '/tmp/fc-main'):
        path = os.path.join(root, 'migration', 'synth_queue_listener.py') if root else ''
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as _fh:
                    tree = ast.parse(_fh.read())
                fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_validate_spec')
                ns = {}
                exec(compile(ast.Module(body=[fn], type_ignores=[]), path, 'exec'), ns)
                _VALIDATOR = ns['_validate_spec']
                return _VALIDATOR
            except Exception:
                continue
    _VALIDATOR = False
    return None


def build_row(ledger, row, *, draft_fn=None, spec_from_draft=None, catalog=None, usage_extras=None, log=_log):
    """{'body': ..} to post, {'reused': key} when the library holds the
    subject, or {'error': text} when the row cannot become a build now."""
    subj = str(row.get('subject') or '').strip()
    # the library may have gained the subject since confirm (or another list built it)
    try:
        cat = catalog
        if cat is None:
            try:
                from prometheus.legacy import chat as _chat
                cat = _chat._profile_catalog_for_chat()
            except Exception:
                cat = []
        hit = BP.library_match(subj, BP.library_index(cat))
        if hit:
            return {'reused': hit[0]}
    except Exception:
        traceback.print_exc()
    draft = interpret_row(ledger, row, draft_fn=draft_fn, catalog=catalog, usage_extras=usage_extras)
    if not draft:
        return {'error': 'the brief could not be read this time'}
    key = reuse_key_from_draft(draft, subj)
    if key:
        return {'reused': key}
    try:
        body = row_body(ledger, row, draft, spec_from_draft=spec_from_draft)
    except Exception as e:
        traceback.print_exc()
        return {'error': f"the brief could not become a build: {str(e)[:200]}"}
    if body is None:
        return {'error': 'the brief came back without demographics'}
    v = listener_validator()
    if v is not None:
        try:
            verdict = v(body.get('spec') or {}, decision='new_build', body=body)
        except Exception as e:
            verdict = f"validator raised {type(e).__name__}"
        if verdict:
            return {'error': f"the engine would refuse this spec: {verdict}"}
    return {'body': body}


# ---------------------------------------------------------------- digest
def _is_probe(uname):
    try:
        from .probe_identity import is_probe_user
        return bool(is_probe_user(uname))
    except Exception:
        return str(uname or '').lower().startswith('canary')


def send_digest(ledger, row_indexes, *, final=False, send_fn=None, log=_log):
    """One digest to the requester. Returns the record appended to the
    ledger's digest history (sent or the reason it was not)."""
    subject, body = BP.digest_text(ledger, row_indexes, final=final)
    req = ledger.get('requester') or {}
    uname = str(req.get('username') or '')
    email = str(req.get('email') or '').strip()
    rec = {'at': time.time(), 'rows': list(row_indexes), 'final': bool(final), 'subject': subject, 'sent': False, 'reason': ''}
    if _is_probe(uname):
        rec['reason'] = 'probe identity'
    elif os.environ.get('REGRESSION_TEST_MODE') == '1' and send_fn is None:
        rec['reason'] = 'test mode'
    elif not email or '@' not in email:
        rec['reason'] = 'no email on seat'
    else:
        try:
            if send_fn is None:
                from . import outbound_mail as _om
                res = _om.send_user_email(to=email, subject=subject, body=body, instructed=True, caller=f"bulk_pull {ledger.get('bulk_id')}")
            else:
                res = send_fn(email, subject, body)
            res = res if isinstance(res, dict) else {'sent': bool(res)}
            rec['sent'] = bool(res.get('sent'))
            rec['reason'] = str(res.get('reason') or '')
        except Exception as e:
            traceback.print_exc()
            rec['reason'] = f"send failed: {type(e).__name__}"
    log(f"{ledger.get('bulk_id')} digest ({'final' if final else 'progress'}, {len(row_indexes)} rows): {'sent to ' + email if rec['sent'] else 'not sent: ' + rec['reason']}")
    return rec


def digest_due(ledger, now=None):
    """(row_indexes, final): the terminal rows not yet reported when a
    digest is due (DIGEST_EVERY of them, an hour with any, or the list
    complete), else ([], False)."""
    now = time.time() if now is None else now
    dg = ledger.get('digest') or {}
    reported = set(int(i) for i in (dg.get('reported') or []))
    fresh = [r['i'] for r in ledger.get('rows') or [] if r.get('state') in ('done', 'failed') or (r.get('state') == 'reused' and r.get('reused_late'))]
    fresh = [i for i in fresh if i not in reported]
    c = BP.counts(ledger)
    final = c['open'] == 0 and str(ledger.get('status') or '') == 'active'
    if final:
        return fresh, True
    if not fresh:
        return [], False
    if len(fresh) >= BP.DIGEST_EVERY or now - float(dg.get('last_at') or 0) >= BP.DIGEST_HOURS * 3600:
        return fresh, False
    return [], False


# ---------------------------------------------------------------- the tick
def _backoff(ledger, now, note):
    f = ledger.setdefault('feeder', {})
    n = int(f.get('backoff_n') or 0) + 1
    wait = min(BACKOFF_BASE_S * (2 ** (n - 1)), BACKOFF_MAX_S)
    f.update({'backoff_n': n, 'backoff_until': now + wait, 'note': str(note)[:200]})
    return wait


def _clear_backoff(ledger):
    f = ledger.setdefault('feeder', {})
    f.update({'backoff_n': 0, 'backoff_until': 0})


def reconcile(ledger, *, status_fn=None, now=None, refund_fn=None, log=_log):
    """Bring every posted / running / waiting / interpreting row up to
    date with the engine. Mutates the ledger; returns how many rows moved."""
    now = time.time() if now is None else now
    moved = 0
    for r in ledger.get('rows') or []:
        st = r.get('state')
        if st == 'interpreting':
            if now - float(r.get('interpreting_at') or 0) > INTERPRET_STALE_S:
                r['state'] = 'queued'
                moved += 1
            continue
        if st in ('posted', 'running'):
            doc = run_status(r.get('run_id'), status_fn=status_fn)
            es = str(doc.get('status') or 'unknown').lower()
            if es in ('complete', 'completed', 'done'):
                # complete = the Total Universe and its Avid Fan cut both registered (the engine's terminal success)
                tu, av = _keys_of(doc)
                r.update({'state': 'done', 'key': tu, 'avid_key': av, 'finished_at': now})
                moved += 1
                continue
            if es in ('failed', 'error', 'cancelled', 'canceled'):
                r.update({'state': 'failed', 'finished_at': now, 'error': BP.FAIL_LINE})
                if doc.get('credits_refunded') or doc.get('refund_username'):
                    r['refunded'] = True
                refund_row(ledger, r, f"list row {r.get('subject')} could not be completed", refund_fn=refund_fn, log=log)
                moved += 1
                continue
            if es in ('unknown', 'read_error') or not doc:
                if st == 'posted' and now - float(r.get('posted_at') or 0) > POST_GRACE_S:
                    r['attempts'] = int(r.get('attempts') or 0) + 1
                    if r['attempts'] >= MAX_ATTEMPTS:
                        r.update({'state': 'failed', 'finished_at': now, 'error': BP.FAIL_LINE})
                        refund_row(ledger, r, f"list row {r.get('subject')} could not be queued", refund_fn=refund_fn, log=log)
                    else:
                        r['state'] = 'queued'
                    moved += 1
                continue
            if st == 'posted' and es not in ('queued',):
                r['state'] = 'running'
                moved += 1
            continue
        if st == 'waiting':
            other = str(r.get('existing_run_id') or '')
            doc = run_status(other, status_fn=status_fn) if other else {}
            es = str(doc.get('status') or 'unknown').lower()
            tu, av = _keys_of(doc)
            if es == 'complete' and tu:
                r.update({'state': 'reused', 'key': tu, 'avid_key': av, 'finished_at': now, 'reused_late': True})
                refund_row(ledger, r, f"list row {r.get('subject')} was already building and is reused", refund_fn=refund_fn, log=log)
                moved += 1
            elif es in ('failed', 'error', 'cancelled', 'canceled', 'unknown') or not doc:
                r.update({'state': 'queued', 'existing_run_id': ''})
                moved += 1
    return moved


def feed(ledger, room, *, post=None, draft_fn=None, spec_from_draft=None, catalog=None, now=None, refund_fn=None, usage_extras=None, log=_log, persist=None):
    """Post up to ``room`` queued rows. Mutates the ledger; returns the
    number posted. ``persist(ledger)`` writes the row's 'interpreting'
    mark before the model call so a crash never loses track."""
    now = time.time() if now is None else now
    posted = 0
    for r in [x for x in ledger.get('rows') or [] if x.get('state') == 'queued']:
        if posted >= room:
            break
        r['state'] = 'interpreting'
        r['interpreting_at'] = now
        if persist is not None:
            try:
                persist(ledger)
            except Exception:
                traceback.print_exc()
        res = build_row(ledger, r, draft_fn=draft_fn, spec_from_draft=spec_from_draft, catalog=catalog, usage_extras=usage_extras, log=log)
        if res.get('reused'):
            r.update({'state': 'reused', 'key': res['reused'], 'finished_at': now, 'reused_late': True})
            refund_row(ledger, r, f"list row {r.get('subject')} is already in the library", refund_fn=refund_fn, log=log)
            log(f"{ledger.get('bulk_id')} row {r.get('i')} ({r.get('subject')}): reused {res['reused']}")
            continue
        if res.get('error'):
            r['attempts'] = int(r.get('attempts') or 0) + 1
            r['last_error'] = str(res['error'])[:200]
            if r['attempts'] >= MAX_ATTEMPTS:
                r.update({'state': 'failed', 'finished_at': now, 'error': BP.FAIL_LINE})
                refund_row(ledger, r, f"list row {r.get('subject')} could not be built ({res['error'][:80]})", refund_fn=refund_fn, log=log)
                log(f"{ledger.get('bulk_id')} row {r.get('i')} ({r.get('subject')}): failed after {MAX_ATTEMPTS} tries: {res['error']}")
            else:
                r['state'] = 'queued'
                _backoff(ledger, now, res['error'])
                log(f"{ledger.get('bulk_id')} row {r.get('i')} ({r.get('subject')}): {res['error']}; try {r['attempts']} of {MAX_ATTEMPTS}, backing off")
                break
            continue
        rid, code, err = _post_result(res['body'], post=post)
        if code == 200 and rid:
            r.update({'state': 'posted', 'run_id': rid, 'posted_at': now})
            posted += 1
            _clear_backoff(ledger)
            log(f"{ledger.get('bulk_id')} row {r.get('i')} ({r.get('subject')}): posted as {rid}")
            continue
        if code == 409:
            if rid and rid != r.get('run_id'):
                r.update({'state': 'waiting', 'existing_run_id': rid})
                log(f"{ledger.get('bulk_id')} row {r.get('i')} ({r.get('subject')}): already building as {rid}; waiting on it")
            else:
                r['state'] = 'queued'
            continue
        if code == 400:
            r['attempts'] = int(r.get('attempts') or 0) + 1
            r['last_error'] = err[:200]
            if r['attempts'] >= MAX_ATTEMPTS:
                r.update({'state': 'failed', 'finished_at': now, 'error': BP.FAIL_LINE})
                refund_row(ledger, r, f"list row {r.get('subject')} was refused by the queue", refund_fn=refund_fn, log=log)
            else:
                r['state'] = 'queued'
            log(f"{ledger.get('bulk_id')} row {r.get('i')} ({r.get('subject')}): queue refused the spec: {err}")
            continue
        # 429 / 5xx / connection trouble: the row goes back, the ledger backs off, the tick stops posting
        r['state'] = 'queued'
        wait = _backoff(ledger, now, err or f"queue returned {code}")
        log(f"{ledger.get('bulk_id')}: queue trouble ({code}: {err}); backing off {int(wait)}s")
        break
    return posted


def tick(s3, bucket=BP.BUCKET, *, post=None, status_fn=None, health_fn=None, draft_fn=None, spec_from_draft=None, catalog=None, cap=None, now=None,
         send_fn=None, refund_fn=None, max_posts=None, usage_extras=None, log=_log):
    """One pass over every active ledger. Returns a summary dict."""
    now = time.time() if now is None else now
    cap = int(cap or BP.in_flight_cap())
    max_posts = int(max_posts or MAX_POSTS_PER_TICK)
    summary = {'ledgers': 0, 'posted': 0, 'moved': 0, 'digests': 0, 'completed': 0, 'skipped': ''}
    ledgers = BP.list_ledgers(s3=s3, bucket=bucket, active_only=True)
    if not ledgers:
        return summary
    health = queue_health(health_fn)
    engine_busy = health is None or int(health.get('pending') or 0) > 0
    summary['skipped'] = ('engine unreachable' if health is None else 'engine backlog') if engine_busy else ''
    # bulk rows in flight across every list share one cap
    in_flight = sum(BP.counts(l)['in_flight'] for l in ledgers)
    for ledger in ledgers:
        bid = ledger.get('bulk_id')
        try:
            moved = reconcile(ledger, status_fn=status_fn, now=now, refund_fn=refund_fn, log=log)
            summary['moved'] += moved
            in_flight = sum(BP.counts(l)['in_flight'] for l in ledgers)
            posted = 0
            if not engine_busy and now >= float((ledger.get('feeder') or {}).get('backoff_until') or 0):
                room = min(max(0, cap - in_flight), max_posts)
                if room > 0 and BP.counts(ledger)['queued'] > 0:
                    def _persist(l, _bid=bid):
                        BP.update_ledger(_bid, lambda doc: _merge_rows(doc, l), s3=s3, bucket=bucket)
                    posted = feed(ledger, room, post=post, draft_fn=draft_fn, spec_from_draft=spec_from_draft, catalog=catalog, now=now,
                                  refund_fn=refund_fn, usage_extras=usage_extras, log=log, persist=_persist)
                    summary['posted'] += posted
            ledger.setdefault('feeder', {})['last_tick'] = now
            idx, final = digest_due(ledger, now=now)
            if idx or final:
                rec = send_digest(ledger, idx, final=final, send_fn=send_fn, log=log)
                dg = ledger.setdefault('digest', {})
                dg['history'] = (dg.get('history') or [])[-40:] + [rec]
                dg['reported'] = sorted(set(int(i) for i in (dg.get('reported') or [])) | set(idx))
                dg['last_at'] = now
                dg['sent'] = int(dg.get('sent') or 0) + (1 if rec.get('sent') else 0)
                summary['digests'] += 1
                if final:
                    ledger['status'] = 'done'
                    ledger['finished_at'] = now
                    summary['completed'] += 1
                    log(f"{bid}: the list is complete ({BP.counts(ledger)})")
            BP.update_ledger(bid, lambda doc, l=ledger: _merge_rows(doc, l), s3=s3, bucket=bucket)
            summary['ledgers'] += 1
        except Exception:
            traceback.print_exc()
            log(f"{bid}: tick failed; the ledger is left for the next tick")
    return summary


def _merge_rows(doc, ledger):
    """Write our view of the ledger over the stored one without losing
    a row the engine's worker finished in between: a terminal state
    already stored wins over our non-terminal one."""
    if not isinstance(doc, dict):
        return None
    out = dict(ledger)
    stored = {int(r.get('i')): r for r in doc.get('rows') or [] if isinstance(r, dict)}
    rows = []
    for r in ledger.get('rows') or []:
        s = stored.get(int(r.get('i')))
        if s and s.get('state') in BP.TERMINAL and r.get('state') not in BP.TERMINAL:
            rows.append(s)
        elif s and s.get('state') in BP.TERMINAL and r.get('state') in BP.TERMINAL:
            merged = dict(r)
            merged['refunded'] = bool(r.get('refunded') or s.get('refunded'))
            for k in ('key', 'avid_key'):
                if not merged.get(k) and s.get(k):
                    merged[k] = s[k]
            rows.append(merged)
        else:
            rows.append(r)
    out['rows'] = rows
    if str(doc.get('status') or '') == 'cancelled':
        out['status'] = 'cancelled'
    return out


# ---------------------------------------------------------------- the loop
def run_forever(stop_event=None, *, tick_s=None, log=_log):
    """The feeder loop for the read worker's thread: a tick every
    TICK_S, never raising, stopping when the event is set."""
    stop = stop_event or threading.Event()
    tick_s = float(tick_s or TICK_S)
    log(f"started (tick every {int(tick_s)}s, cap {BP.in_flight_cap()})")
    n = 0
    while not stop.is_set():
        try:
            s3, bucket = BP._s3()
            out = tick(s3, bucket, log=log)
            n += 1
            # a line when something moved, and a heartbeat every 20 ticks while a list is active
            if out.get('posted') or out.get('moved') or out.get('digests') or out.get('completed') or (out.get('ledgers') and n % 20 == 0):
                log(f"tick: {out}")
        except Exception:
            traceback.print_exc()
        stop.wait(tick_s)
    log('stopped')


def start_thread(stop_event):
    """Start the feeder on a daemon thread (the read worker's boot)."""
    if os.environ.get('BULK_FEEDER', '1') in ('0', 'false', 'no', 'off'):
        _log('disabled by BULK_FEEDER=0')
        return None
    t = threading.Thread(target=run_forever, args=(stop_event,), daemon=True, name='bulk-feeder')
    t.start()
    return t


__all__ = ['TICK_S', 'MAX_POSTS_PER_TICK', 'MAX_ATTEMPTS', 'SOURCE', 'TAG', 'queue_health', 'run_status', 'refund_row', 'row_prompt', 'interpret_row',
           'reuse_key_from_draft', 'row_body', 'listener_validator', 'build_row', 'send_digest', 'digest_due', 'reconcile', 'feed', 'tick',
           'run_forever', 'start_thread']
