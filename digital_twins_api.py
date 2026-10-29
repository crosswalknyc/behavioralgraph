"""Digital Twins IQ API (2026-10-26). A Flask blueprint registered from
app.py so app.py stays small.

  GET  /api/digital-twins/list                       twins the caller can see (the shared library)
  GET  /api/digital-twins/how-to                     the in-product directions
  GET  /api/digital-twins/pricing                    the pricing lines for the seat
  GET  /api/digital-twins/profiles?q=                the Profile IQ profiles the seat can build a twin from (the walkthrough's picker)
  GET  /api/digital-twins/jobs/<job_id>              a build or run job's status
  POST /api/digital-twins/build                      queue a twin build (the walkthrough: source profile|demographics, profile_key,
                                                     demographics picks, panelists; or a bare subject from the chat)
  POST /api/digital-twins/connect-request            the Connect accounts request record
  GET  /api/digital-twins/<key>                      the twin
  POST /api/digital-twins/<key>/run                  queue a scenario run (Launch, content, pricing, platform move, compare, ...);
                                                     kind greenlight is refused with a plain line (Greenlight is a Script read or a
                                                     Concept test on the Pilot Testing room since 2026-10-28)
  GET  /api/digital-twins/<key>/status?job_id=       the job status (alias of /jobs)
  GET  /api/digital-twins/<key>/export.csv
  GET  /api/digital-twins/<key>/export.pdf
  POST /api/digital-twins/<key>/deck                 hands the twin to the deck core as material
  POST /api/digital-twins/<key>/spend/activate       run a plan forward on the twin (Spend Engine)
  POST /api/digital-twins/<key>/spend/<plan>/approve  approve one move
  POST /api/digital-twins/<key>/spend/<plan>/mode     chip or rule, with the guardrail
  GET  /api/digital-twins/<key>/spend/<plan>/moves.csv
  GET  /api/digital-twins/<key>/spend/<plan>/handoff  the Attribution IQ tracking package, prefilled
  POST /api/digital-twins/<key>/panel/open           open a Focus Group IQ room (free; an empty room with the same twins, segments,
                                                     seats and name is reused; portraits start on a thread)
  GET  /api/digital-twins/<key>/panel/<sid>          the session; the roster carries portrait_url once the faces exist (re-poll)
  PATCH /api/digital-twins/<key>/panel/<sid>         rename the room (title, 120 chars)
  POST /api/digital-twins/<key>/panel/<sid>/rename   the same for clients that post
  POST /api/digital-twins/<key>/panel/<sid>/ask      ask the room (synchronous, seconds; the session price on the first question)
  DELETE /api/digital-twins/<key>/panel/<sid>        delete a room (creator or admin; 30-day undo copy under system/digital_twins/deleted_rooms/)
  POST /api/digital-twins/<key>/panel/<sid>/delete   the same for clients that cannot send DELETE
  DELETE /api/digital-twins/<key>                    delete a twin (creator or admin; 30-day undo copy under system/digital_twins/deleted_twins/)
  POST /api/digital-twins/<key>/delete               the same for clients that cannot send DELETE
  GET  /api/digital-twins/<key>/panel/<sid>/export.csv
  GET  /api/digital-twins/<key>/panel/<sid>/export.pdf
  POST /api/digital-twins/<key>/pilot/open           open a Pilot Testing session (free; charge on first Play)
  GET  /api/digital-twins/<key>/pilot/<sid>          the test
  PATCH /api/digital-twins/<key>/pilot/<sid>         rename
  POST /api/digital-twins/<key>/pilot/<sid>/upload   the cut (mp4 / mov / webm)
  POST /api/digital-twins/<key>/pilot/<sid>/prepare  the room watches the cut: a file goes to the read worker (job_id; poll /jobs/<id>), a slate runs here
  POST /api/digital-twins/<key>/pilot/<sid>/play     first Play charges; then the room is live on the clock (409 preparing while the worker watches)
  POST /api/digital-twins/<key>/pilot/<sid>/ask      a question at the playhead; it carries the last 60 to 90 seconds of what played
  POST /api/digital-twins/<key>/pilot/<sid>/stop     stop, keep the scorecard, add the test to the norms, remove the file when the test asked for it
  PATCH /api/digital-twins/<key>/pilot/<sid>         rename, or set genre / mode / cut_label / rough_cut / purge_media_after
  DELETE /api/digital-twins/<key>/pilot/<sid>        delete (30-day undo; the file follows the test into the undo copy)
  GET  /api/digital-twins/<key>/pilot/<sid>/media-url  a signed link to the cut (ten minutes; creator or admin only)
  GET  /api/digital-twins/<key>/pilot/<sid>/media    the uploaded cut streamed (creator or admin only; the fallback player path)
  GET  /api/digital-twins/pilot-norms?format=&genre=  the norm and genre norm columns (no sources)
  GET  /api/digital-twins/pilot-norms/all?genre=      every format's cells for the Norms tab, with our test count per cell
  POST /api/digital-twins/<key>/pilot/<sid>/assets    a further trailer, an episode, key art (image) or a script file (text)
  POST /api/digital-twins/<key>/pilot/<sid>/stimulus  options (titles), logline, text, cast, comps, quotas, mode flags
  POST /api/digital-twins/<key>/pilot/<sid>/recut     cut B on the same room (lineage; the recut price on first Play)
  GET  /api/digital-twins/<key>/pilot/<sid>/compare   cut A against cut B; two audiences on one cut
  GET|POST /api/digital-twins/<key>/pilot/<sid>/comps the comp set and the versus-comps column
  POST /api/digital-twins/<key>/pilot/<sid>/critics   the critics room (reseats before Play)
  POST /api/digital-twins/<key>/pilot/<sid>/talent-attach  how much a name lifts interest, by quadrant
  GET  /api/digital-twins/<key>/pilot/<sid>/activation-brief  the Spend Engine / Launch Lab handoff from the top segments
  GET  /api/digital-twins/<key>/pilot/<sid>/report.pptx | report.pdf | dial.png  the studio report and the dial chart
  GET  /api/digital-twins/<key>/pilot/<sid>/export.csv
  GET  /api/digital-twins/<key>/pilot/<sid>/export.pdf
  GET  /api/digital-twins/<key>/pilot/<sid>/export.pptx
  GET  /api/digital-twins/portrait/<hash>?size=128   a twin's generated portrait (JPEG, long cache); 404 when absent

Access: ``has_digital_twins_iq_access`` or admin / super_admin; the
creator always sees their own; ``allowed_digital_twin_runs`` defaults
to ['*']. Charges go through the pay-per-use ledger by pull type;
super admins are free. On top of the priced steps, every model call a
build, a run or a room question makes carries the seat's pay-per-use
attribution (``_usage_extras``), so Prometheus usage is metered at the
published rate like any other Prometheus work (Jenna 2026-10-27:
'aside from these fees there needs to be billed prometheus metered
pricing'). No email is sent from here.
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
import uuid

from flask import Blueprint, Response, jsonify, request, session

from migration import digital_twins as DT
from migration import digital_twin_synthesis as SYN

bp = Blueprint('digital_twins_api', __name__)
PREFIX = '/api/digital-twins'
CREDITS = {'digital_twins_iq': 15, 'digital_twin_run': 3, 'digital_twin_spend': 5, 'digital_twin_panel': 4, 'digital_twin_panel_more': 3, 'digital_twin_panelist': 1,
           'digital_twin_pilot': 4, 'digital_twin_pilot_more': 3,
           'digital_twin_trailer': 3, 'digital_twin_title': 2, 'digital_twin_key_art': 2, 'digital_twin_concept': 3, 'digital_twin_script': 3, 'digital_twin_recut': 3}


# ---------------------------------------------------------------- host
def _app():
    return sys.modules.get('app')


def _s3():
    a = _app()
    return getattr(a, 's3_client', None) if a is not None else None


def _user():
    a = _app()
    try:
        return a.get_current_user() if a is not None else None
    except Exception:
        return None


def _uname(user):
    try:
        return str(session.get('username') or (user or {}).get('username') or (user or {}).get('email') or '').strip()
    except Exception:
        return str((user or {}).get('username') or '').strip()


def _role(user):
    a = _app()
    try:
        return a._normalize_role((user or {}).get('role', 'user')) if a is not None else str((user or {}).get('role') or 'user')
    except Exception:
        return str((user or {}).get('role') or 'user')


def _gate():
    """(user, err_response). The product flag or admin, else 403."""
    user = _user()
    if not user:
        return None, (jsonify({'success': False, 'error': 'Sign in to use Digital Twins IQ.'}), 401)
    user = dict(user)
    user['role'] = _role(user)
    if not DT.has_access(user):
        return None, (jsonify({'success': False, 'error': 'Digital Twins IQ access required. Ask your admin to switch it on for your account.'}), 403)
    return user, None


def _twin_or_404(user, key):
    key = str(key or '')
    if not key.startswith(DT.S3_PREFIX) or not key.endswith('.json.gz'):
        return None, (jsonify({'success': False, 'error': 'twin not found'}), 404)
    runs = DT.list_runs(_s3())
    run = next((r for r in runs if r.get('key') == key), None)
    if not DT.can_open(user, key, run):
        return None, (jsonify({'success': False, 'error': 'This twin is not on your list. Ask your admin to add it.'}), 403)
    payload = DT.load_twin(_s3(), key)
    if not isinstance(payload, dict):
        return None, (jsonify({'success': False, 'error': 'twin not found'}), 404)
    return payload, None


def _price_fn(username):
    try:
        from prometheus.legacy.watch import _pm_usd
        return lambda key, dflt: _pm_usd(key, dflt, username)
    except Exception:
        return None


def _price_label(key, username):
    try:
        from prometheus.legacy.watch import _pm_usd_label, _pm_usd
        return _pm_usd_label(_pm_usd(key, DT.PRICING[key], username), username)
    except Exception:
        return f"${DT.PRICING[key]:,.0f}"


def _charge(user, key, description, job_id, quantity=1):
    """Charge one priced step (``quantity`` of it for the per-panelist
    price); super admins are free. Returns None when charged (or free),
    else the funds reply."""
    uname = _uname(user)
    try:
        from prometheus.priced_read import seat_is_free
        if seat_is_free(user, uname, None):
            return None
    except Exception:
        if _role(user) == 'super_admin':
            return None
    a = _app()
    if a is None or not hasattr(a, 'consume_credit'):
        return None
    try:
        qty = max(1, int(quantity or 1))
    except (TypeError, ValueError):
        qty = 1
    try:
        if qty > 1:
            ok = a.consume_credit(uname, description=description, pull_type=DT.PULL_TYPES[key], credits_used=CREDITS[key] * qty, job_id=job_id, quantity=qty)
        else:
            ok = a.consume_credit(uname, description=description, pull_type=DT.PULL_TYPES[key], credits_used=CREDITS[key], job_id=job_id)
    except Exception:
        traceback.print_exc()
        ok = False
    if ok:
        return None
    label = _price_label(key, uname)
    if qty > 1:
        label = f"{label} x {qty}"
    return jsonify({'success': False, 'guidance': True,
                    'error': f"That step runs {label} and your account cannot cover it right now. Add funds or ask your admin, and it runs the moment you are set."}), 402


def _usage_extras(user):
    """The seat's pay-per-use attribution for the model calls a build, a
    run or a room question makes, so Prometheus usage on Digital Twins
    work is metered on top of the priced steps. None when the seat is
    not billed for usage (unlimited, super admin), exactly as the chat
    decides it."""
    try:
        from prometheus.legacy.chat import _pm_usage_extras
        return _pm_usage_extras(user)
    except Exception:
        return None


def _claude_json():
    try:
        from prometheus.host import host
        if host.has('claude_data'):
            return host.claude_data
    except Exception:
        pass
    return None


def _voices_fn():
    cj = _claude_json()
    if cj is None:
        return None

    def fn(ctx):
        from prometheus import digital_twin_lane as _lane
        return _lane.voices_call(cj, ctx)
    return fn


def _nocache(resp):
    try:
        resp.headers['Cache-Control'] = 'no-store'
    except Exception:
        pass
    return resp


def _portraits():
    """The portraits module, or None when it cannot load (the room
    never depends on it)."""
    try:
        from migration import digital_twin_portraits as PT
        return PT
    except Exception:
        traceback.print_exc()
        return None


def _attach_portraits(payload, key, sess):
    """The self-heal on read: seats whose portrait exists get their url;
    a change is written back in place. Never raises."""
    PT = _portraits()
    if PT is None or not sess:
        return
    try:
        if PT.attach_portraits(sess, _s3()):
            DT.save_twin(_s3(), key, payload)
    except Exception as e:
        try:
            from prometheus import faults as _faults
            _faults.note('digital_twins_api.attach_portraits', e)
        except Exception:
            pass


# -------------------------------------------------------------- routes
@bp.route(f'{PREFIX}/list', methods=['GET'])
def dt_list():
    user, err = _gate()
    if err:
        return err
    runs = DT.list_runs(_s3())
    vis = DT.visible_runs(runs, user)
    try:
        from migration.digital_twin_covers import schedule_missing
        schedule_missing(_s3(), vis)
    except Exception:
        traceback.print_exc()
    return _nocache(jsonify({'success': True, 'runs': vis, 'is_admin': DT.is_admin(user), 'allow_all': DT.is_admin(user) or '*' in DT.allowed_keys(user),
                             'pricing': DT.pricing_lines(_price_fn(_uname(user)))}))


@bp.route(f'{PREFIX}/how-to', methods=['GET'])
def dt_how_to():
    user, err = _gate()
    if err:
        return err
    return jsonify({'success': True, **DT.how_to(_price_fn(_uname(user)))})


@bp.route(f'{PREFIX}/pricing', methods=['GET'])
def dt_pricing():
    user, err = _gate()
    if err:
        return err
    return jsonify({'success': True, 'lines': DT.pricing_lines(_price_fn(_uname(user))), 'defaults': DT.PRICING})


_PROFILE_TS_RX = re.compile(r"_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2})?\.csv$", re.I)


def _profile_catalog():
    """The dashboard's profile catalog (system/s3_cache.json as the app
    holds it): [{display_name, s3_key, category}] for root CSVs."""
    a = _app()
    jobs = []
    try:
        jobs = list((getattr(a, 's3_cache', None) or {}).get('jobs') or []) if a is not None else []
        if not jobs and a is not None and hasattr(a, 'load_persisted_cache'):
            a.load_persisted_cache()
            jobs = list((getattr(a, 's3_cache', None) or {}).get('jobs') or [])
    except Exception:
        jobs = []
    if not jobs:
        try:
            doc = json.loads(_s3().get_object(Bucket=DT.S3_BUCKET, Key='system/s3_cache.json')['Body'].read().decode('utf-8'))
            jobs = list(doc.get('jobs') or [])
        except Exception:
            jobs = []
    out = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        key = str(j.get('s3_key') or '')
        if not key.endswith('.csv') or '/' in key or 'gen_pop' in key.lower() or key.lower().startswith('gen pop '):
            continue
        out.append({'display_name': str(j.get('display_name') or j.get('project_name') or key[:-4].replace('_', ' ')), 's3_key': key, 'category': str(j.get('category') or '')})
    return out


def _can_read_profile(user, key):
    a = _app()
    fn = getattr(a, '_user_can_access_profile_run', None) if a is not None else None
    if fn is None:
        return True
    try:
        return bool(fn(user, key))
    except Exception:
        return False


@bp.route(f'{PREFIX}/profiles', methods=['GET'])
def dt_profiles():
    """The Profile IQ profiles the seat can build a twin from, for the
    walkthrough's picker: base profiles first (no ' - ' cut suffix,
    newest build of a name), cuts when the words ask for them."""
    user, err = _gate()
    if err:
        return err
    q = ' '.join(str(request.args.get('q') or '').split()).lower()
    toks = [t for t in re.sub(r"[^a-z0-9 ]+", ' ', q).split() if t]
    try:
        limit = max(1, min(int(request.args.get('limit') or 30), 80))
    except (TypeError, ValueError):
        limit = 30
    seen, out = set(), []
    for j in _profile_catalog():
        name = j['display_name']
        low = name.lower()
        if toks and not all(t in low for t in toks):
            continue
        is_cut = ' - ' in name
        if is_cut and not q:
            continue
        score = (0 if is_cut else 2) + (1 if _PROFILE_TS_RX.search(j['s3_key']) else 0) + (1 if toks and low.startswith(q) else 0)
        out.append((score, j))
    out.sort(key=lambda x: (-x[0], x[1]['display_name'].lower()))
    picked = []
    for _score, j in out:
        if j['display_name'].lower() in seen:
            continue
        if not _can_read_profile(user, j['s3_key']):
            continue
        seen.add(j['display_name'].lower())
        picked.append(j)
        if len(picked) >= limit:
            break
    return _nocache(jsonify({'success': True, 'profiles': picked, 'q': q}))


@bp.route(f'{PREFIX}/jobs/<job_id>', methods=['GET'])
def dt_job(job_id):
    user, err = _gate()
    if err:
        return err
    from prometheus import digital_twin_job as J
    rec = J.status_read(job_id)
    if not rec:
        return jsonify({'success': False, 'error': 'job not found'}), 404
    if str(rec.get('user') or '') != _uname(user) and not DT.is_admin(user):
        return jsonify({'success': False, 'error': 'job not found'}), 404
    if str(rec.get('op') or '') == 'panel_ask':
        try:   # a room job with no update for five minutes is picked back up once; a second stall or an old job reads 'failed'
            from migration import digital_twin_room_jobs as RJ
            rec = RJ.check_stale(_s3(), rec) or rec
        except Exception:
            traceback.print_exc()
    rec = {k: v for k, v in rec.items() if k not in ('resume', 'inputs')}
    return _nocache(jsonify({'success': True, 'job': rec}))


@bp.route(f'{PREFIX}/build', methods=['POST'])
def dt_build():
    user, err = _gate()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    subject = ' '.join(str(body.get('subject') or '').split())[:140]
    source = str(body.get('source') or '').lower()
    demo_spec = SYN.demographic_spec(body.get('demographics'))
    if source == 'demographics' and not demo_spec:
        return jsonify({'success': False, 'error': 'Pick at least one thing about the group (gender, age, income, ethnicity, parents or a market).'}), 400
    if source == 'demographics' and not subject:
        subject = SYN.demographic_name(demo_spec)
    if not subject:
        return jsonify({'success': False, 'error': 'Name the subject of the twin (a title, a creator, a brand, an audience, or an idea).'}), 400
    profile_key = str(body.get('profile_key') or '').strip()
    profile_name = ''
    if profile_key:
        # the walkthrough picked a profile: a root Total Universe CSV the seat can open
        if source == 'demographics':
            profile_key = ''
        elif '/' in profile_key or not profile_key.endswith('.csv') or not _can_read_profile(user, profile_key):
            return jsonify({'success': False, 'error': 'That profile is not on your list. Pick another, or name the subject and the twin pulls the audience itself.'}), 400
        else:
            profile_name = next((p['display_name'] for p in _profile_catalog() if p['s3_key'] == profile_key), '') or profile_key[:-4].replace('_', ' ')
    try:
        panelists = int(body.get('panelists') or 0)
    except (TypeError, ValueError):
        panelists = 0
    panelists = max(2, min(panelists, DT.PANEL_SEATS_MAX)) if panelists else DT.PANEL_SEATS_INCLUDED
    inputs = {'subject': subject, 'subject_kind': str(body.get('subject_kind') or 'audience'), 'purpose': str(body.get('purpose') or '')[:400],
              'format': str(body.get('format') or ''), 'platform_home': str(body.get('platform_home') or ''), 'name': str(body.get('name') or '')[:120],
              'window': body.get('window') if isinstance(body.get('window'), dict) else None, 'ask_text': str(body.get('ask_text') or '')[:400], 'op': 'build',
              'source': source if source in ('profile', 'demographics') else '', 'profile_key': profile_key, 'profile_name': profile_name,
              'demographics': demo_spec if source == 'demographics' else None, 'panelists': panelists}
    from prometheus import digital_twin_job as J
    job_id = uuid.uuid4().hex[:12]
    funds = _charge(user, 'digital_twins_iq', f"Digital Twin: {subject}", job_id)
    if funds is not None:
        return funds
    rec = J.start('twin_build', job_id, _uname(user), inputs, thread_id=str(body.get('thread_id') or ''), extras=_usage_extras(user))
    return jsonify({'success': True, 'job_id': job_id, 'status': rec.get('status'), 'subject': subject, 'panelists': panelists,
                    'extra_panelists': DT.extra_panelists(panelists), 'message': J.build_promise(subject)})


@bp.route(f'{PREFIX}/connect-request', methods=['POST'])
def dt_connect_request():
    user, err = _gate()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    rid = uuid.uuid4().hex[:10]
    rec = {'id': rid, 'kind': 'connect_accounts', 'user': _uname(user), 'email': str((user or {}).get('email') or ''),
           'platforms': [str(p)[:40] for p in (body.get('platforms') or [])][:8], 'note': str(body.get('note') or '')[:400],
           'twin_key': str(body.get('twin_key') or ''), 'status': 'requested', 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    s3 = _s3()
    try:
        s3.put_object(Bucket=DT.S3_BUCKET, Key=f"{DT.REQUESTS_PREFIX}{rid}.json", Body=json.dumps(rec).encode('utf-8'), ContentType='application/json')
    except Exception:
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'The request could not be saved. Try again in a minute.'}), 500
    return jsonify({'success': True, 'request': rec,
                    'message': 'Request filed. Connected-account execution is switched on per account by Crosswalk; until then, the approved moves list is the handoff for your buyers.'})


@bp.route(f'{PREFIX}/<path:key>/run', methods=['POST'])
def dt_run(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    kind = str(body.get('kind') or 'greenlight').lower().replace('-', '_')
    from migration.digital_twin_engine import RUNNERS
    inputs = body.get('inputs') if isinstance(body.get('inputs'), dict) else {}
    # the old Greenlight run is retired (Jenna 2026-10-28); a compare on that scenario is a Concept test with two twins in the room
    if kind in DT.RETIRED_RUN_KINDS or (kind == 'compare' and str(inputs.get('scenario_kind') or '').lower() in DT.RETIRED_RUN_KINDS + ('',)):
        return jsonify({'success': False, 'error': DT.GREENLIGHT_RETIRED_LINE, 'retired': True, 'tab': 'greenlight'}), 400
    if kind not in RUNNERS and kind != 'compare':
        return jsonify({'success': False, 'error': f"unknown run kind: {kind}"}), 400
    other_key = str(body.get('other_key') or '')
    if kind == 'compare':
        if not other_key:
            return jsonify({'success': False, 'error': 'A compare needs the other twin (other_key).'}), 400
        _other, oerr = _twin_or_404(user, other_key)
        if oerr:
            return oerr
    scenario = {'kind': kind, 'question': str(body.get('question') or '')[:300], 'inputs': inputs}
    from prometheus import digital_twin_job as J
    job_id = uuid.uuid4().hex[:12]
    funds = _charge(user, 'digital_twin_run', f"Digital Twin run ({kind}): {(payload.get('meta') or {}).get('name')}", job_id)
    if funds is not None:
        return funds
    rec = J.start('twin_run', job_id, _uname(user), {'op': 'run', 'key': key, 'scenario': scenario, 'other_key': other_key}, thread_id=str(body.get('thread_id') or ''),
                  extras=_usage_extras(user))
    return jsonify({'success': True, 'job_id': job_id, 'status': rec.get('status'), 'message': J.run_promise(kind, (payload.get('meta') or {}).get('name'))})


@bp.route(f'{PREFIX}/<path:key>/status', methods=['GET'])
def dt_status(key):
    user, err = _gate()
    if err:
        return err
    job_id = str(request.args.get('job_id') or '')
    if job_id:
        return dt_job(job_id)
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    return _nocache(jsonify({'success': True, 'key': key, 'runs': len(DT.product_runs(payload)), 'updated_at': (payload.get('meta') or {}).get('updated_at')}))


@bp.route(f'{PREFIX}/<path:key>/export.csv', methods=['GET'])
def dt_export_csv(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    return Response(DT.csv_text(payload), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{DT.csv_filename(payload)}"'})


@bp.route(f'{PREFIX}/<path:key>/export.pdf', methods=['GET'])
def dt_export_pdf(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_exports as X
    return Response(X.build_pdf(payload), mimetype='application/pdf', headers={'Content-Disposition': f'attachment; filename="{X.pdf_filename(payload)}"'})


@bp.route(f'{PREFIX}/<path:key>/export.pptx', methods=['GET'])
def dt_export_pptx(key):
    """The twin's deck drawn from the payload alone (no model call), for
    the Deck button; the Prometheus deck core is the richer path."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_exports as X
    return Response(X.build_deck(payload), mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    headers={'Content-Disposition': f'attachment; filename="{X.deck_filename(payload)}"'})


@bp.route(f'{PREFIX}/<path:key>/deck', methods=['POST'])
def dt_deck(key):
    """Hand the twin to the Prometheus deck core as material. Falls back
    to the payload-drawn deck when the core is not bound."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    name = str((payload.get('meta') or {}).get('name') or key)
    try:
        from prometheus.host import host
        if host.has('deck_core'):
            ctx = DT.deck_context(None, payload)
            core_body = {'text': str(body.get('text') or f"Build a deck of the {name} twin, slide for slide"), 'angle': str(body.get('angle') or ''),
                         'history': body.get('history') or [], 'thread_id': body.get('thread_id') or '', 'confirm_open_screen': True,
                         'page_context': {'view': DT.PRODUCT_KEY, 'primary': None, 'cuts': [], 'view_context': {**ctx['view_context'], 'data': dict(ctx['view_context']['data'], key=key)}}}
            return host.deck_core(user, core_body)
    except Exception:
        traceback.print_exc()
    from migration import digital_twin_exports as X
    return Response(X.build_deck(payload), mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    headers={'Content-Disposition': f'attachment; filename="{X.deck_filename(payload)}"'})


# -------------------------------------------------------- spend engine
def _find_plan(payload, plan_id):
    for sp in ((payload.get('labs') or {}).get('spend')) or []:
        if sp.get('plan_id') == plan_id:
            return sp
    return None


def _trends_for(payload):
    try:
        from migration import corpus_catalog as _cc
        tr = _cc.trends_for(str((payload.get('meta') or {}).get('subject') or ''))
        if tr:
            return {'names': [str(t.get('name') or t.get('term') or '') for t in tr if isinstance(t, dict)][:5], 'general': 1.0 + 0.04 * min(5, len(tr))}
    except Exception:
        pass
    return None


@bp.route(f'{PREFIX}/<path:key>/spend/activate', methods=['POST'])
def dt_spend_activate(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    plan = body.get('plan') if isinstance(body.get('plan'), dict) else {}
    if not plan.get('channels') and body.get('channels_text'):
        from migration.digital_twin_engine import parse_channels
        plan['channels'] = parse_channels(str(body.get('channels_text')))
    if not plan.get('channels') and body.get('from_run'):
        run = next((r for r in payload.get('runs') or [] if r.get('id') == body.get('from_run')), None)
        if run and run.get('kind') == 'launch':
            label = str(body.get('plan_label') or 'Signal-led')
            pl = next((p for p in (run.get('outputs') or {}).get('plans') or [] if str(p.get('label') or '').lower().startswith(label.lower()[:6])), None)
            if pl:
                plan = {'channels': pl.get('channels'), 'window': {'days': int((run.get('outputs') or {}).get('window_weeks') or 2) * 7},
                        'goal': (run.get('outputs') or {}).get('goal'), 'assets': plan.get('assets') or (run.get('inputs') or {}).get('assets') or [], 'platform': (run.get('outputs') or {}).get('platform')}
                body['source'] = 'launch'
    if not plan.get('channels'):
        return jsonify({'success': False, 'error': 'A plan needs channels with spend (for example Meta $60K, YouTube $40K).'}), 400
    from migration import digital_twin_spend as SP
    plan_id = uuid.uuid4().hex[:10]
    funds = _charge(user, 'digital_twin_spend', f"Spend Engine plan: {(payload.get('meta') or {}).get('name')}", plan_id)
    if funds is not None:
        return funds
    try:
        doc = SP.activate_plan(payload, plan, mode=str(body.get('mode') or 'chip'), rule=body.get('rule') if isinstance(body.get('rule'), dict) else None,
                               source=str(body.get('source') or 'manual'), trends=_trends_for(payload), created_by=_uname(user), plan_id=plan_id)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    payload.setdefault('labs', {}).setdefault('spend', []).insert(0, doc)
    from migration.digital_twin_engine import simulate  # noqa: F401  (the lab run joins the twin's Runs list)
    run_rec = {'id': uuid.uuid4().hex[:10], 'kind': 'spend', 'label': 'Spend Engine', 'question': f"Run the plan forward: {doc.get('label')}", 'inputs': {'plan_id': plan_id, 'mode': doc.get('mode')},
               'outputs': {'expected': (doc.get('ledger') or {}).get('signal_led_outcome'), 'low': (doc.get('ledger') or {}).get('as_allocated_outcome'),
                           'high': (doc.get('ledger') or {}).get('signal_led_outcome'), 'noun': 'first actions', 'ledger': doc.get('ledger'), 'plan_id': plan_id},
               'drivers': SP.spend_lines(doc)[:3], 'comparison': {'baseline_label': 'the plan as allocated', 'baseline_value': (doc.get('ledger') or {}).get('as_allocated_outcome')},
               'recommendation': (doc.get('ledger') or {}).get('read') or '', 'read': {'tldr': SP.spend_lines(doc)[:3], 'reads': [], 'what_to_do': [(doc.get('holdout') or {}).get('lift_read') or '']},
               'created_by': _uname(user), 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    payload.setdefault('runs', []).insert(0, run_rec)
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'plan': doc, 'run_id': run_rec['id']})


@bp.route(f'{PREFIX}/<path:key>/spend/<plan_id>/approve', methods=['POST'])
def dt_spend_approve(key, plan_id):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    doc = _find_plan(payload, plan_id)
    if not doc:
        return jsonify({'success': False, 'error': 'plan not found'}), 404
    body = request.get_json(silent=True) or {}
    from migration import digital_twin_spend as SP
    try:
        day = int(body.get('day') or 0)
    except (TypeError, ValueError):
        day = 0
    SP.approve_move(payload, doc, day, str(body.get('move_id') or ''), by=_uname(user), trends=_trends_for(payload))
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'plan': doc})


@bp.route(f'{PREFIX}/<path:key>/spend/<plan_id>/mode', methods=['POST'])
def dt_spend_mode(key, plan_id):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    doc = _find_plan(payload, plan_id)
    if not doc:
        return jsonify({'success': False, 'error': 'plan not found'}), 404
    body = request.get_json(silent=True) or {}
    from migration import digital_twin_spend as SP
    SP.set_mode(payload, doc, str(body.get('mode') or doc.get('mode') or 'chip'), body.get('rule') if isinstance(body.get('rule'), dict) else None, trends=_trends_for(payload))
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'plan': doc})


@bp.route(f'{PREFIX}/<path:key>/spend/<plan_id>/moves.csv', methods=['GET'])
def dt_spend_moves(key, plan_id):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    doc = _find_plan(payload, plan_id)
    if not doc:
        return jsonify({'success': False, 'error': 'plan not found'}), 404
    from migration import digital_twin_spend as SP
    name = str((payload.get('meta') or {}).get('name') or 'twin').replace(' ', '_')
    return Response(SP.moves_csv(doc), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{name}_moves_{plan_id}.csv"'})


@bp.route(f'{PREFIX}/<path:key>/spend/<plan_id>/handoff', methods=['GET'])
def dt_spend_handoff(key, plan_id):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    doc = _find_plan(payload, plan_id)
    if not doc:
        return jsonify({'success': False, 'error': 'plan not found'}), 404
    from migration import digital_twin_spend as SP
    return jsonify({'success': True, 'package': SP.handoff_package(payload, doc)})


# -------------------------------------------- focus group iq (the panel routes)
def _session(payload, sid):
    return next((s for s in payload.get('panel_sessions') or [] if s.get('id') == sid), None)


@bp.route(f'{PREFIX}/<path:key>/panel/open', methods=['POST'])
def dt_panel_open(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    twins = [payload]
    for ok in [str(k) for k in (body.get('twins') or []) if str(k) and str(k) != key][:3]:
        other, oerr = _twin_or_404(user, ok)
        if other:
            twins.append(other)
    from migration import digital_twin_panel as P
    from migration import digital_twin_room as ROOM
    from migration.digital_twin_synthesis import hydrate_identity_from_profile
    s3 = _s3()
    for t in twins:
        try:
            hydrate_identity_from_profile(t, s3)
        except Exception:
            traceback.print_exc()
    twins_by_key = {str((t.get('meta') or {}).get('key') or ''): t for t in twins}
    segments = body.get('segments') or None
    try:
        in_room = int(body.get('in_room') or 0)
    except (TypeError, ValueError):
        in_room = 0
    in_room = in_room or DT.panelists_for(payload)       # the seats chosen when the twin was built, else the twelve a session covers
    title = str(body.get('title') or '')[:120]
    # Opening a room is free; the session price is taken on the first
    # question (2026-10-26). An empty room with the same twins, segments
    # and seats is reused instead of opening another (Jenna: repeated
    # clicks on Open the room left four empty 'Room: Gilmore Girls').
    reused = P.find_reusable_session(payload, twins, segments, in_room, created_by=_uname(user), title=title)
    if reused is not None:
        sess = reused
        if P.needs_reseat(sess, twins):
            P.rebuild_group(sess, twins, seed=str(sess.get('id') or ''))
    else:
        sess = P.open_session(twins, segments=segments, in_room=in_room, title=title, created_by=_uname(user), session_id=uuid.uuid4().hex[:10])
        sess['twin_keys'] = [str((t.get('meta') or {}).get('key') or '') for t in twins]
        payload.setdefault('panel_sessions', []).insert(0, sess)
    try:
        ROOM.ensure_personas(sess, twins_by_key, session_seed=str(sess.get('id') or ''))
    except Exception:
        traceback.print_exc()
    PT = _portraits()
    if PT is not None:
        try:
            PT.attach_portraits(sess, _s3())      # seats already pictured (a second room with the same twins) carry their url at once
        except Exception:
            pass
    DT.save_twin(_s3(), key, payload)
    first_q = str(body.get('q') or body.get('question') or '').strip()
    rec = None
    if first_q:
        funds = _charge_question(user, payload, sess)
        if funds is not None:
            doc = funds[0].get_json() or {}
            doc['session'] = sess        # the room stays open and free; the question waits on funds
            return jsonify(doc), funds[1]
        rec = P.ask(sess, twins_by_key, first_q, options=body.get('options'), stimulus=body.get('stimulus') if isinstance(body.get('stimulus'), dict) else None,
                    voices_fn=_voices_fn(), asked_by=_uname(user), s3=_s3(), twin_key=key, usage_extras=_usage_extras(user))
        DT.save_twin(_s3(), key, payload)
    if PT is not None:
        try:
            # the faces land in the session document when they finish; the room answers now
            PT.schedule_room_portraits(_s3(), key, sess, twins_by_key, payload)
        except Exception as e:
            try:
                from prometheus import faults as _faults
                _faults.note('digital_twins_api.schedule_portraits', e)
            except Exception:
                pass
    return jsonify({'success': True, 'session': sess, 'question': rec, 'reused': reused is not None, 'job_id': (rec or {}).get('job_id'), 'stage': (rec or {}).get('stage'),
                    'extra_panelists': DT.extra_panelists((sess.get('group') or {}).get('in_room'))})


def _charge_question(user, payload, sess):
    """The price the next question carries, if any: the session on its
    first question (plus the panelists past the twelve a session seats,
    at the per-panelist price, once per room), a further block when the
    paid questions are used up. None when nothing is due or it was
    charged (or free)."""
    from migration import digital_twin_panel as P
    due = P.charge_due(sess, DT.PANEL_QUESTIONS_INCLUDED)
    if not due:
        return None
    name = (payload.get('meta') or {}).get('name')
    sid = str(sess.get('id') or '')
    asked = len(sess.get('questions') or [])
    extra = DT.extra_panelists((sess.get('group') or {}).get('in_room')) if due == 'digital_twin_panel' and not sess.get('panelists_paid') else 0
    if extra:
        # the extra seats first (the smaller line), then the session; a session that cannot be covered hands the seats back
        funds = _charge(user, 'digital_twin_panelist', f"Focus Group IQ, {extra} extra panelist{'s' if extra != 1 else ''}: {name}", f"{sid}-seats", quantity=extra)
        if funds is not None:
            return funds
        sess['panelists_paid'] = extra
    desc = f"Focus Group IQ session: {name}" if due == 'digital_twin_panel' else f"Focus Group IQ, {DT.PANEL_QUESTIONS_INCLUDED} more questions: {name}"
    funds = _charge(user, due, desc, sid if due == 'digital_twin_panel' else f"{sid}-{asked}")
    if funds is not None:
        if extra:
            sess.pop('panelists_paid', None)
            _refund(user, CREDITS['digital_twin_panelist'] * extra, f"Focus Group IQ seats returned, the session could not be covered: {name}")
        return funds
    P.mark_paid(sess, DT.PANEL_QUESTIONS_INCLUDED)
    return None


def _refund(user, credits, reason):
    a = _app()
    if a is None or not hasattr(a, 'refund_credit') or _role(user) == 'super_admin':
        return
    try:
        a.refund_credit(_uname(user), credits=credits, reason=reason)
    except Exception:
        traceback.print_exc()


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>', methods=['GET'])
def dt_panel_get(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    _attach_portraits(payload, key, sess)
    try:   # an in-flight question whose job stalled is picked back up once, or marked failed with a plain line (never 'researching' forever)
        from migration import digital_twin_room_jobs as RJ
        if RJ.sweep_session(_s3(), key, sess):
            DT.save_twin(_s3(), key, payload)
    except Exception:
        traceback.print_exc()
    return _nocache(jsonify({'success': True, 'session': sess}))


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>', methods=['PATCH'])
@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/rename', methods=['POST', 'PATCH'])
def dt_panel_rename(key, sid):
    """Rename a room (2026-10-27, Jenna: name it when you open it, then
    edit the title on the room page). Anyone who can see the twin can
    rename a room on it."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    from migration import digital_twin_panel as P
    body = request.get_json(silent=True) or {}
    title = str(body.get('title') or body.get('name') or '')
    name = P.rename_session(sess, title)
    if not name:
        return jsonify({'success': False, 'error': 'Name the room.'}), 400
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': sess, 'title': name}))


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/ask', methods=['POST'])
def dt_panel_ask(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    body = request.get_json(silent=True) or {}
    q = ' '.join(str(body.get('q') or body.get('question') or '').split())
    if not q:
        return jsonify({'success': False, 'error': 'Ask the room something.'}), 400
    stimulus = body.get('stimulus') if isinstance(body.get('stimulus'), dict) else None
    ask_again = bool(body.get('ask_again'))
    if not ask_again:
        # the same question again in this room: the in-flight record while it runs, the finished one with a note after;
        # nothing is charged and no second job starts (2026-10-26). The Ask again chip sends ask_again: true.
        from migration import digital_twin_room_jobs as RJ
        dup = RJ.find_duplicate(sess, q, stimulus)
        if dup is not None:
            RJ.mark_asked_again(dup)
            DT.save_twin(_s3(), key, payload)
            return jsonify({'success': True, 'question': dup, 'duplicate': True, 'note': dup.get('note'), 'session_id': sid, 'questions_asked': len(sess['questions']),
                            'questions_paid': sess.get('questions_paid'), 'job_id': dup.get('job_id'), 'stage': dup.get('stage')})
    funds = _charge_question(user, payload, sess)      # the session on its first question, a further block after twelve
    if funds is not None:
        return funds
    twins_by_key = {}
    for tk in sess.get('twin_keys') or [key]:
        t = payload if tk == key else DT.load_twin(_s3(), tk)
        if t:
            twins_by_key[tk] = t
    if key not in twins_by_key:
        twins_by_key[key] = payload
    from migration import digital_twin_panel as P
    PT = _portraits()
    if PT is not None:
        try:
            PT.attach_portraits(sess, _s3())      # so the voices of this answer carry the portraits that exist
        except Exception:
            pass
    # The room engine (migration/digital_twin_room.py) answers as a job: the record returns at once at stage
    # 'researching' with a job_id; poll GET /api/digital-twins/jobs/<job_id> for the stage and the seat answers as
    # they arrive, then the finished record (read, options, voices) lands here on the session (2026-10-26).
    rec = P.ask(sess, twins_by_key, q, kind=body.get('kind'), options=body.get('options'), stimulus=stimulus,
                voices_fn=_voices_fn(), asked_by=_uname(user), s3=_s3(), twin_key=key, ask_again=ask_again, usage_extras=_usage_extras(user))
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'question': rec, 'session_id': sid, 'questions_asked': len(sess['questions']), 'questions_paid': sess.get('questions_paid'),
                    'job_id': rec.get('job_id'), 'stage': rec.get('stage')})


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>', methods=['DELETE'])
@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/delete', methods=['POST'])
def dt_panel_delete(key, sid):
    """Delete a room (2026-10-26, Jenna: "need a way to delete a room").
    The room's creator or an admin; the session leaves the twin payload
    in place and a copy is kept under system/digital_twins/deleted_rooms/
    for a 30-day undo. Never emailed."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    from migration import digital_twin_panel as P
    if not P.can_delete_room(sess, _uname(user), DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'Only the person who opened this room, or an admin, can delete it.'}), 403
    removed = P.delete_room(_s3(), key, payload, sid, deleted_by=_uname(user))
    if removed is None:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    DT.save_twin(_s3(), key, payload)
    undo_until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + P.DELETED_ROOM_KEEP_DAYS * 86400))
    return _nocache(jsonify({'success': True, 'deleted': sid, 'sessions_left': len(payload.get('panel_sessions') or []),
                             'undo_until': undo_until, 'message': 'Room deleted. It can be restored within 30 days by an admin.'}))


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/export.csv', methods=['GET'])
def dt_panel_csv(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    from migration import digital_twin_panel as P
    name = str((payload.get('meta') or {}).get('name') or 'twin').replace(' ', '_')
    return Response(P.session_csv(sess), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{name}_panel_{sid}.csv"'})


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/export.pdf', methods=['GET'])
def dt_panel_pdf(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    from migration import digital_twin_exports as X
    PT = _portraits()
    portrait_fn = (lambda h: PT.read_portrait(_s3(), h, PT.THUMB_SIZE)) if PT is not None else None
    return Response(X.build_panel_pdf(payload, sess, portrait_fn=portrait_fn), mimetype='application/pdf', headers={'Content-Disposition': f'attachment; filename="{X.panel_pdf_filename(payload, sess)}"'})


_PORTRAIT_HASH_RX = re.compile(r"^[0-9a-f]{16,64}$")


# -------------------------------------------- pilot testing
def _pilot(payload, sid):
    return next((s for s in payload.get('pilot_sessions') or [] if s.get('id') == sid), None)


_PLAY_KEYS = frozenset({'digital_twin_pilot', 'digital_twin_trailer', 'digital_twin_title', 'digital_twin_key_art', 'digital_twin_concept', 'digital_twin_script', 'digital_twin_recut'})
_MODE_DESC = {'digital_twin_pilot': 'Pilot Testing session', 'digital_twin_trailer': 'Trailer test', 'digital_twin_title': 'Title test', 'digital_twin_key_art': 'Key art test',
              'digital_twin_concept': 'Concept test', 'digital_twin_script': 'Script read', 'digital_twin_recut': 'Recut re-test'}


def _charge_pilot(user, payload, sess, *, for_play=False):
    """First Play carries the mode's price (the session price for a
    watch, the per-asset trailer price, the title / key art / concept /
    script price, the recut price for cut B on the same room), plus the
    extra panelists once; a further question block after the included
    twelve. None when nothing is due."""
    from migration import digital_twin_pilot as PI
    due = PI.charge_due(sess)
    if not due:
        return None
    is_play = due in _PLAY_KEYS
    if is_play and not for_play:
        return None
    if due == 'digital_twin_pilot_more' and for_play:
        return None
    name = (payload.get('meta') or {}).get('name')
    sid = str(sess.get('id') or '')
    extra = DT.extra_panelists((sess.get('group') or {}).get('in_room')) if is_play and not sess.get('panelists_paid') else 0
    label = _MODE_DESC.get(due, 'Pilot Testing session')
    if extra:
        funds = _charge(user, 'digital_twin_panelist', f"{label}, {extra} extra panelist{'s' if extra != 1 else ''}: {name}", f"{sid}-seats", quantity=extra)
        if funds is not None:
            return funds
        sess['panelists_paid'] = extra
    qty = PI.play_quantity(sess) if is_play else 1
    if is_play:
        desc = f"{label}: {name}" + (f" ({qty} assets)" if qty > 1 else '')
    else:
        desc = f"Pilot Testing, {DT.PANEL_QUESTIONS_INCLUDED} more questions: {name}"
    asked = len(sess.get('questions') or [])
    funds = _charge(user, due, desc, sid if is_play else f"{sid}-q{asked}", quantity=qty) if qty > 1 else _charge(user, due, desc, sid if is_play else f"{sid}-q{asked}")
    if funds is not None:
        if extra:
            sess.pop('panelists_paid', None)
            _refund(user, CREDITS['digital_twin_panelist'] * extra, f"{label} seats returned, the session could not be covered: {name}")
        return funds
    if is_play:
        PI.mark_play_paid(sess, DT.PANEL_QUESTIONS_INCLUDED)
        sess['play_price_key'] = due
        sess['play_quantity'] = qty
    else:
        PI.mark_paid(sess, DT.PANEL_QUESTIONS_INCLUDED)
    return None


@bp.route(f'{PREFIX}/<path:key>/pilot/open', methods=['POST'])
def dt_pilot_open(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    twins = [payload]
    for ok in [str(k) for k in (body.get('twins') or []) if str(k) and str(k) != key][:3]:
        other, oerr = _twin_or_404(user, ok)
        if other:
            twins.append(other)
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_room as ROOM
    from migration.digital_twin_synthesis import hydrate_identity_from_profile
    s3 = _s3()
    for t in twins:
        try:
            hydrate_identity_from_profile(t, s3)
        except Exception:
            traceback.print_exc()
    twins_by_key = {str((t.get('meta') or {}).get('key') or ''): t for t in twins}
    segments = body.get('segments') or None
    try:
        in_room = int(body.get('in_room') or 0)
    except (TypeError, ValueError):
        in_room = 0
    in_room = in_room or DT.panelists_for(payload)
    title = str(body.get('title') or '')[:120]
    mode = str(body.get('mode') or 'pilot').strip().lower()
    mode = mode if mode in PI.MODES else 'pilot'
    reused = PI.find_reusable_session(payload, twins, title=title, created_by=_uname(user)) if mode == 'pilot' and not body.get('text') and not body.get('options') else None
    if reused is not None and str(reused.get('mode') or 'pilot') != mode:
        reused = None
    if reused is not None:
        sess = reused
    else:
        media = {}
        if body.get('cut_title') or body.get('runtime_s') or body.get('runtime'):
            try:
                runtime = float(body.get('runtime_s') or body.get('runtime') or 0)
            except (TypeError, ValueError):
                runtime = 0
            media = {'title': str(body.get('cut_title') or title or '')[:160], 'runtime_s': runtime, 'source': 'slate'}
        sess = PI.open_session(twins, segments=segments, in_room=in_room, title=title, created_by=_uname(user),
                               session_id=uuid.uuid4().hex[:10], media=media, mode=mode, logline=str(body.get('logline') or ''), text=str(body.get('text') or ''),
                               options=body.get('options') if isinstance(body.get('options'), list) else None, rough_cut=bool(body.get('rough_cut')),
                               critics_room=bool(body.get('critics_room')))
        sess['twin_keys'] = [str((t.get('meta') or {}).get('key') or '') for t in twins]
        if isinstance(body.get('cast'), list):
            PI.set_options(sess, {'cast': body['cast']})
        if isinstance(body.get('comps'), list):
            sess['comps'] = [({'title': str(c)} if not isinstance(c, dict) else c) for c in body['comps'] if str(c).strip()][:8]
        if body.get('linked_run_id'):
            sess['linked_run_id'] = str(body['linked_run_id'])[:40]
        if body.get('critics_room'):
            from migration import digital_twin_compare as CMP
            CMP.seat_critics(sess, seed=f"critics|{sess['id']}")
        if isinstance(body.get('quotas'), dict) and body['quotas']:
            from migration import digital_twin_compare as CMP
            try:
                CMP.apply_quotas(sess, twins, body['quotas'], seed=f"recruit|{sess['id']}")
            except Exception:
                traceback.print_exc()
        payload.setdefault('pilot_sessions', []).insert(0, sess)
    try:
        ROOM.ensure_personas(sess, twins_by_key, session_seed=str(sess.get('id') or ''))
    except Exception:
        traceback.print_exc()
    PT = _portraits()
    if PT is not None:
        try:
            PT.attach_portraits(sess, _s3())
        except Exception:
            pass
    DT.save_twin(_s3(), key, payload)
    if PT is not None:
        try:
            PT.schedule_room_portraits(_s3(), key, sess, twins_by_key, payload)
        except Exception:
            pass
    return jsonify({'success': True, 'session': PI.public_session(sess), 'reused': reused is not None,
                    'extra_panelists': DT.extra_panelists((sess.get('group') or {}).get('in_room'))})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>', methods=['GET'])
def dt_pilot_get(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    _attach_portraits(payload, key, sess)
    if sess.get('status') == 'preparing':
        _prepare_job_live(sess)             # a worker job that ended without a ready test flips the test back to draft
        if sess.get('status') != 'preparing':
            DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess)}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>', methods=['PATCH'])
@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/rename', methods=['POST', 'PATCH'])
def dt_pilot_rename(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    changed = PI.set_options(sess, body)   # genre chip, mode, cut label, rough cut, remove-the-file-when-done
    name = ''
    if body.get('title') or body.get('name'):
        name = PI.rename_session(sess, str(body.get('title') or body.get('name') or ''))
        if not name:
            return jsonify({'success': False, 'error': 'Name the test.'}), 400
    elif not changed:
        return jsonify({'success': False, 'error': 'Nothing to change.'}), 400
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), 'title': name or sess.get('title'), 'changed': changed}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/upload', methods=['POST'])
def dt_pilot_upload(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    f = request.files.get('file') or request.files.get('cut') or request.files.get('video')
    if f is None or not getattr(f, 'filename', ''):
        return jsonify({'success': False, 'error': 'Upload the cut (mp4, mov or webm).'}), 400
    if not PI.accept_upload_name(f.filename, getattr(f, 'mimetype', '') or ''):
        return jsonify({'success': False, 'error': 'Use an mp4, mov or webm file.'}), 400
    s3 = _s3()
    if s3 is None:
        return jsonify({'success': False, 'error': 'The cut could not be stored. Try again in a minute.'}), 500
    mk = PI.media_s3_key(_uname(user), sid, f.filename)
    raw = f.read()
    if not raw or len(raw) < 64:
        return jsonify({'success': False, 'error': 'That file is empty.'}), 400
    if len(raw) > 2 * 1024 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'That file is too large for a test. Use a cut under 2 GB.'}), 400
    ctype = getattr(f, 'mimetype', '') or 'video/mp4'
    try:
        s3.put_object(Bucket=DT.S3_BUCKET, Key=mk, Body=raw, ContentType=ctype)
    except Exception:
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'The cut could not be stored. Try again in a minute.'}), 500
    runtime = 0
    try:
        import tempfile, os
        suffix = os.path.splitext(f.filename)[1] or '.mp4'
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(tmp, 'wb') as fh:
            fh.write(raw)
        probed = PI.process_local_cut(tmp, title=str(request.form.get('title') or sess.get('title') or ''))
        runtime = float(probed.get('runtime_s') or 0)
        if probed.get('transcript'):
            sess['transcript'] = probed['transcript']
        try:
            os.remove(tmp)
        except Exception:
            pass
    except Exception:
        traceback.print_exc()
    media = sess.setdefault('media', {})
    if sess.get('created_by') and str(sess.get('created_by')) != _uname(user) and not DT.is_admin(user):
        return jsonify({'success': False, 'error': 'Only the person who opened this test can add the cut.'}), 403
    media.update({
        'filename': f.filename, 's3_key': mk, 'content_type': ctype,
        'bytes': len(raw), 'source': 'upload',
        'title': str(request.form.get('title') or media.get('title') or sess.get('title') or '')[:160],
        'hash': PI.media_hash(media.get('title') or '', runtime or media.get('runtime_s') or 0, f.filename, str(len(raw))),
    })
    media.pop('purged_at', None)
    media.pop('purged_reason', None)
    if runtime:
        media['runtime_s'] = runtime
    sess['status'] = 'draft'
    sess['watch'] = {}          # a new file is a new watch
    sess['prepare_note'] = 'Preparing the cut' if runtime else 'Add a runtime if the clock looks short, then prepare.'
    sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    DT.save_twin(s3, key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess), 'uploaded': True})


def _twins_for(payload, key, sess):
    twins_by_key = {}
    for tk in sess.get('twin_keys') or [key]:
        t = payload if tk == key else DT.load_twin(_s3(), tk)
        if t:
            twins_by_key[tk] = t
    if key not in twins_by_key:
        twins_by_key[key] = payload
    return twins_by_key


PREPARE_ATTEMPTS_MAX = 2   # after two worker attempts the clock beat sheet runs here, so Play never dead-ends


def _prepare_job_live(sess):
    """The running prepare job on this test, or None. A job that ended
    without a ready session flips the test back to draft so the page
    offers Play again."""
    jid = str(sess.get('prepare_job_id') or '')
    if not jid or sess.get('status') != 'preparing':
        return None
    try:
        from prometheus import digital_twin_job as J
        rec = J.status_read(jid)
    except Exception:
        rec = None
    st = str((rec or {}).get('status') or '')
    if not rec:
        return None
    try:
        age = time.time() - float(rec.get('started_run_at') or rec.get('queued_at') or rec.get('started_at') or time.time())
    except (TypeError, ValueError):
        age = 0.0
    stranded = (st == 'queued' and age > 20 * 60) or (st in ('running', 'claimed', 'working') and age > 3 * 3600)
    if st in ('error', 'cancelled') or stranded:
        sess['status'] = 'draft'
        sess['prepare_note'] = 'The cut could not be prepared this time. Press Play and the room runs on the clock.'
        sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        return None
    if st == 'done':
        return None
    return rec


def _start_prepare(user, key, sess, payload, body):
    """Queue the cut for the read worker (ffmpeg and the room live
    there). Returns the job id, or None when the worker has already
    tried twice (the caller prepares on the clock right here)."""
    from prometheus import pilot_prepare_job as PJ
    live = _prepare_job_live(sess)
    if live:
        return str(live.get('job_id') or sess.get('prepare_job_id'))
    attempts = int(sess.get('prepare_attempts') or 0)
    if attempts >= PREPARE_ATTEMPTS_MAX:
        return None
    job_id = uuid.uuid4().hex[:12]
    sess['status'] = 'preparing'
    sess['prepare_note'] = 'Preparing the cut'
    sess['prepare_job_id'] = job_id
    sess['prepare_attempts'] = attempts + 1
    sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    DT.save_twin(_s3(), key, payload)
    PJ.start(job_id, _uname(user), {'key': key, 'session_id': str(sess.get('id') or ''), 'notes': str((body or {}).get('notes') or '')[:400],
                                    'title': str((body or {}).get('cut_title') or (body or {}).get('title') or '')[:160]}, extras=_usage_extras(user))
    return job_id


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/prepare', methods=['POST'])
def dt_pilot_prepare(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    try:
        duration = float(body.get('runtime_s') or body.get('duration_s') or (sess.get('media') or {}).get('runtime_s') or 0)
    except (TypeError, ValueError):
        duration = 0
    title = str(body.get('cut_title') or body.get('title') or (sess.get('media') or {}).get('title') or sess.get('title') or '')
    media = sess.setdefault('media', {})
    if title:
        media['title'] = title[:160]
    if duration:
        media['runtime_s'] = duration
    if media.get('s3_key'):
        # a file: the read worker reads it (scenes, frames, words) and the room watches there
        job_id = _start_prepare(user, key, sess, payload, body)
        if job_id:
            return jsonify({'success': True, 'session': PI.public_session(sess), 'job_id': job_id, 'preparing': True})
    twins_by_key = _twins_for(payload, key, sess)
    try:
        _caption_art_for(sess, user)
    except Exception:
        traceback.print_exc()
    PI.prepare_watch(sess, twins_by_key, duration_s=duration, title=title,
                     notes=str(body.get('notes') or ''), transcript=sess.get('transcript'), usage_extras=_usage_extras(user), s3=_s3())
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess)})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/play', methods=['POST'])
def dt_pilot_play(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if sess.get('status') not in ('ready', 'playing', 'stopped') or not (sess.get('dial') or {}).get('value'):
        body = request.get_json(silent=True) or {}
        if (sess.get('media') or {}).get('s3_key'):
            # the room is still watching the file on the worker: Play waits for it (no charge yet)
            job_id = _start_prepare(user, key, sess, payload, body)
            if job_id:
                return jsonify({'success': False, 'preparing': True, 'job_id': job_id, 'session': PI.public_session(sess),
                                'error': 'Preparing the cut. Play is ready the moment the room has watched it.'}), 409
        twins_by_key = _twins_for(payload, key, sess)
        try:
            duration = float(body.get('runtime_s') or (sess.get('media') or {}).get('runtime_s') or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            _caption_art_for(sess, user)
        except Exception:
            traceback.print_exc()
        PI.prepare_watch(sess, twins_by_key, duration_s=duration,
                         title=str((sess.get('media') or {}).get('title') or sess.get('title') or ''),
                         transcript=sess.get('transcript'), usage_extras=_usage_extras(user), s3=_s3())
    funds = _charge_pilot(user, payload, sess, for_play=True)
    if funds is not None:
        return funds
    PI.mark_playing(sess)
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess), 'charged': True})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/ask', methods=['POST'])
def dt_pilot_ask(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_panel as P
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    q = ' '.join(str(body.get('q') or body.get('question') or '').split())
    if not q:
        return jsonify({'success': False, 'error': 'Ask the room something.'}), 400
    if not sess.get('play_charged'):
        return jsonify({'success': False, 'error': 'Press Play first. The room answers once the cut is running.'}), 400
    funds = _charge_pilot(user, payload, sess, for_play=False)
    if funds is not None:
        return funds
    twins_by_key = {}
    for tk in sess.get('twin_keys') or [key]:
        t = payload if tk == key else DT.load_twin(_s3(), tk)
        if t:
            twins_by_key[tk] = t
    if key not in twins_by_key:
        twins_by_key[key] = payload
    try:
        playhead = float(body.get('playhead_s') or body.get('t') or 0)
    except (TypeError, ValueError):
        playhead = 0
    # the question carries the moment: the last 60 to 90 seconds of what played, the scene on screen,
    # the top events in that window and every seat's own dial in those seconds
    stimulus = PI.ask_stimulus(sess, playhead, extra=body.get('stimulus') if isinstance(body.get('stimulus'), dict) else None)
    rec = P.ask(sess, twins_by_key, q, kind=body.get('kind'), options=body.get('options'),
                stimulus=stimulus,
                voices_fn=_voices_fn(), asked_by=_uname(user), s3=_s3(), twin_key=key,
                ask_again=bool(body.get('ask_again')), usage_extras=_usage_extras(user))
    PI.stamp_question(rec, playhead)
    rec['moment'] = {'frame': (stimulus.get('window') or {}).get('frame'), 'scene': (stimulus.get('window') or {}).get('scene'), 'dial_now': (stimulus.get('window') or {}).get('dial_now')}
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'question': rec, 'session_id': sid, 'session': PI.public_session(sess),
                    'job_id': rec.get('job_id'), 'stage': rec.get('stage')})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/stop', methods=['POST'])
def dt_pilot_stop(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    PI.stop_session(sess)
    if sess.get('scorecard'):
        PI.record_norms(sess, s3=_s3())     # every finished test grows the norms (idempotent per test)
    purged = False
    if sess.get('purge_media_after') and (sess.get('media') or {}).get('s3_key'):
        purged = PI.purge_media(_s3(), sess, reason='done')
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess), 'media_removed': purged})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>', methods=['DELETE'])
@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/delete', methods=['POST'])
def dt_pilot_delete(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if not PI.can_delete(sess, _uname(user), DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'Only the person who opened this test, or an admin, can delete it.'}), 403
    removed = PI.delete_session(_s3(), key, payload, sid, deleted_by=_uname(user))
    if removed is None:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    DT.save_twin(_s3(), key, payload)
    undo_until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + PI.DELETED_PILOT_KEEP_DAYS * 86400))
    return _nocache(jsonify({'success': True, 'deleted': sid, 'sessions_left': len(payload.get('pilot_sessions') or []),
                             'undo_until': undo_until, 'message': 'Test deleted. It can be restored within 30 days by an admin.'}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/media', methods=['GET'])
def dt_pilot_media(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    from migration import digital_twin_pilot as PI
    if not PI.can_view_media(sess, _uname(user), DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'The cut plays only for the person who uploaded it.'}), 403
    media = sess.get('media') or {}
    mk = media.get('s3_key')
    if not mk:
        return jsonify({'success': False, 'error': 'No cut is on this test yet.'}), 404
    s3 = _s3()
    if s3 is None:
        return jsonify({'success': False, 'error': 'The cut is not available right now.'}), 500
    kw = {'Bucket': DT.S3_BUCKET, 'Key': mk}
    rng = request.headers.get('Range')
    if rng and rng.lower().startswith('bytes='):
        kw['Range'] = rng
    try:
        obj = s3.get_object(**kw)
    except Exception:
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'The cut is not available right now.'}), 404
    body = obj['Body']
    ctype = media.get('content_type') or obj.get('ContentType') or 'video/mp4'
    status = 206 if rng else 200

    def chunks():
        try:
            while True:
                bit = body.read(256 * 1024)
                if not bit:
                    break
                yield bit
        except Exception:
            return
    resp = Response(chunks(), status=status, mimetype=ctype)
    resp.headers['Accept-Ranges'] = 'bytes'
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    if obj.get('ContentRange'):
        resp.headers['Content-Range'] = obj['ContentRange']
    if obj.get('ContentLength') is not None:
        resp.headers['Content-Length'] = str(obj['ContentLength'])
    return resp


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/media-url', methods=['GET'])
def dt_pilot_media_url(key, sid):
    """A signed link to the cut that lives ten minutes, for the person
    who uploaded it (or an admin). The player asks again before it
    expires. The file's storage key never travels to the browser."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    from migration import digital_twin_pilot as PI
    if not PI.can_view_media(sess, _uname(user), DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'The cut plays only for the person who uploaded it.'}), 403
    if not (sess.get('media') or {}).get('s3_key'):
        return jsonify({'success': False, 'error': 'No cut is on this test.' if not (sess.get('media') or {}).get('purged_at') else 'The file was removed when the test finished. The read stays.'}), 404
    link = PI.signed_media_url(_s3(), sess)
    if not link:
        return jsonify({'success': False, 'error': 'The cut is not available right now.'}), 500
    return _nocache(jsonify({'success': True, **link, 'watermark': {'who': str((user or {}).get('email') or _uname(user) or ''), 'test': sid}}))


# ----------------------------------------- the screening modes (phase 2)
def _caption_art_for(sess, user):
    """Key art options with a file get their captions before the room
    looks at them (the images are small; this runs here). Fail-safe."""
    from migration import digital_twin_modes as MODES
    opts = [o for o in (sess.get('options') or []) if isinstance(o, dict) and o.get('s3_key')]
    if not opts or str(sess.get('mode') or '') != 'key_art':
        return
    s3 = _s3()
    if s3 is None:
        return
    import os
    import tempfile
    paths = []
    try:
        for o in opts:
            try:
                fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(str(o.get('filename') or ''))[1] or '.jpg')
                os.close(fd)
                with open(tmp, 'wb') as fh:
                    fh.write(s3.get_object(Bucket=DT.S3_BUCKET, Key=o['s3_key'])['Body'].read())
                o['local_path'] = tmp
                paths.append(tmp)
            except Exception:
                traceback.print_exc()
        MODES.caption_art(opts, usage_extras=_usage_extras(user), title=str((sess.get('media') or {}).get('title') or sess.get('title') or ''))
    finally:
        for o in opts:
            o.pop('local_path', None)
        for pth in paths:
            try:
                os.remove(pth)
            except Exception:
                pass


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/assets', methods=['POST'])
def dt_pilot_assets(key, sid):
    """An asset on a test: a second or third trailer (slot 1, 2), a
    further episode in a binge test, a piece of key art (an option), or
    a script file (its text becomes the stimulus). Multipart: ``file``,
    ``slot``, ``label``, ``kind`` (video | image | text)."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_modes as MODES
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if sess.get('created_by') and str(sess.get('created_by')) != _uname(user) and not DT.is_admin(user):
        return jsonify({'success': False, 'error': 'Only the person who opened this test can add to it.'}), 403
    f = request.files.get('file') or request.files.get('asset') or request.files.get('image')
    if f is None or not getattr(f, 'filename', ''):
        return jsonify({'success': False, 'error': 'Attach the file.'}), 400
    kind = str(request.form.get('kind') or '').strip().lower()
    mode = str(sess.get('mode') or 'pilot')
    if not kind:
        kind = 'image' if mode == 'key_art' else ('text' if mode in ('script', 'concept') and MODES.accept_text_name(f.filename, getattr(f, 'mimetype', '') or '') else 'video')
    raw = f.read()
    if not raw or len(raw) < 16:
        return jsonify({'success': False, 'error': 'That file is empty.'}), 400
    label = ' '.join(str(request.form.get('label') or '').split())[:80]
    try:
        slot = int(request.form.get('slot') or 0)
    except (TypeError, ValueError):
        slot = 0
    s3 = _s3()
    if kind == 'text':
        if not MODES.accept_text_name(f.filename, getattr(f, 'mimetype', '') or ''):
            return jsonify({'success': False, 'error': 'Use a txt or pdf for the script.'}), 400
        text = MODES.text_from_upload(raw, f.filename)
        if not text.strip():
            return jsonify({'success': False, 'error': 'No text could be read from that file. Paste the script instead.'}), 400
        PI.set_options(sess, {'text': text})
        sess.setdefault('assets', []).append({'id': uuid.uuid4().hex[:6], 'kind': 'text', 'slot': 0, 'label': label or f.filename, 'filename': f.filename, 'bytes': len(raw)})
        DT.save_twin(s3, key, payload)
        return jsonify({'success': True, 'session': PI.public_session(sess), 'text_chars': len(text)})
    if kind == 'image':
        if not MODES.accept_image_name(f.filename, getattr(f, 'mimetype', '') or ''):
            return jsonify({'success': False, 'error': 'Use a jpg, png or webp for key art.'}), 400
        if len(raw) > 25 * 1024 * 1024:
            return jsonify({'success': False, 'error': 'Use an image under 25 MB.'}), 400
        opts = list(sess.get('options') or [])
        if len(opts) >= MODES.MAX_OPTIONS and slot >= len(opts):
            return jsonify({'success': False, 'error': f"A key art test carries up to {MODES.MAX_OPTIONS} options."}), 400
        if s3 is None:
            return jsonify({'success': False, 'error': 'The art could not be stored. Try again in a minute.'}), 500
        mk = PI.media_s3_key(_uname(user), sid, f"art{slot}_{f.filename}")
        ctype = getattr(f, 'mimetype', '') or 'image/jpeg'
        try:
            s3.put_object(Bucket=DT.S3_BUCKET, Key=mk, Body=raw, ContentType=ctype)
        except Exception:
            traceback.print_exc()
            return jsonify({'success': False, 'error': 'The art could not be stored. Try again in a minute.'}), 500
        row = {'id': 'abcdef'[min(slot, 5)] if slot < len(opts) or slot == len(opts) else 'abcdef'[min(len(opts), 5)], 'label': label or f"Art {chr(65 + min(slot, 5))}", 'caption': '',
               's3_key': mk, 'filename': f.filename, 'content_type': ctype, 'bytes': len(raw), 'hash': PI.media_hash(label or f.filename, 0, f.filename, str(len(raw)))}
        if slot < len(opts):
            row['id'] = opts[slot].get('id') or row['id']
            opts[slot] = row
        else:
            row['id'] = 'abcdef'[len(opts)]
            opts.append(row)
        sess['options'] = opts
        sess['mode'] = 'key_art'
        sess['status'] = 'draft'
        sess['watch'] = {}
        sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        DT.save_twin(s3, key, payload)
        return jsonify({'success': True, 'session': PI.public_session(sess), 'uploaded': True})
    # a video asset: slot 0 is the primary cut (the upload route's job); further slots ride under assets
    if not PI.accept_upload_name(f.filename, getattr(f, 'mimetype', '') or ''):
        return jsonify({'success': False, 'error': 'Use an mp4, mov or webm file.'}), 400
    if len(raw) > 2 * 1024 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'Use a cut under 2 GB.'}), 400
    cap = MODES.MAX_TRAILER_ASSETS if mode in ('trailer', 'spot') else (MODES.MAX_BINGE_ASSETS if mode == 'binge' else 1)
    assets = [a for a in (sess.get('assets') or []) if isinstance(a, dict) and a.get('kind', 'video') == 'video']
    if slot <= 0:
        slot = max([int(a.get('slot') or 0) for a in assets] + [0]) + 1 if (sess.get('media') or {}).get('s3_key') else 0
    if slot >= cap:
        return jsonify({'success': False, 'error': f"This test carries up to {cap} asset{'s' if cap != 1 else ''}."}), 400
    if s3 is None:
        return jsonify({'success': False, 'error': 'The cut could not be stored. Try again in a minute.'}), 500
    mk = PI.media_s3_key(_uname(user), sid, f"a{slot}_{f.filename}")
    ctype = getattr(f, 'mimetype', '') or 'video/mp4'
    try:
        s3.put_object(Bucket=DT.S3_BUCKET, Key=mk, Body=raw, ContentType=ctype)
    except Exception:
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'The cut could not be stored. Try again in a minute.'}), 500
    runtime = 0.0
    try:
        import os
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(f.filename)[1] or '.mp4')
        os.close(fd)
        with open(tmp, 'wb') as fh:
            fh.write(raw)
        runtime = float(PI.probe_duration(tmp) or 0)
        os.remove(tmp)
    except Exception:
        traceback.print_exc()
    try:
        runtime = runtime or float(request.form.get('runtime_s') or 0)
    except (TypeError, ValueError):
        pass
    default_label = (f"Trailer {chr(65 + slot)}" if mode in ('trailer', 'spot') else f"Episode {slot + 1}" if mode == 'binge' else f"Cut {chr(65 + slot)}")
    rec = {'id': uuid.uuid4().hex[:6], 'kind': 'video', 'slot': slot, 'label': label or default_label, 'title': label or default_label, 's3_key': mk, 'filename': f.filename, 'content_type': ctype,
           'bytes': len(raw), 'runtime_s': runtime, 'hash': PI.media_hash(label or default_label, runtime, f.filename, str(len(raw)))}
    others = [a for a in (sess.get('assets') or []) if not (isinstance(a, dict) and int(a.get('slot') or 0) == slot and a.get('kind', 'video') == 'video')]
    sess['assets'] = sorted(others + [rec], key=lambda a: int(a.get('slot') or 0))
    if slot == 0:
        media = sess.setdefault('media', {})
        media.update({'filename': f.filename, 's3_key': mk, 'content_type': ctype, 'bytes': len(raw), 'source': 'upload', 'title': label or media.get('title') or sess.get('title') or '', 'hash': rec['hash']})
        if runtime:
            media['runtime_s'] = runtime
        sess['cut_label'] = label or sess.get('cut_label') or default_label
    sess['status'] = 'draft'
    sess['watch'] = {}
    (sess.get('asset_reads') or {}).pop(rec['id'], None)
    sess['prepare_note'] = 'Preparing the cut'
    sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    DT.save_twin(s3, key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess), 'uploaded': True, 'asset': {k: v for k, v in rec.items() if k != 's3_key'}})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/stimulus', methods=['POST', 'PATCH'])
def dt_pilot_stimulus(key, sid):
    """The mode's stimulus in words: options (titles), logline, text (a
    script or a concept), cast, comps, quotas. No charge."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    changed = PI.set_options(sess, {k: body[k] for k in ('options', 'logline', 'text', 'cast', 'mode', 'genre', 'rough_cut', 'critics_room', 'cut_label') if k in body})
    if isinstance(body.get('comps'), list):
        sess['comps'] = [({'title': str(c)} if not isinstance(c, dict) else c) for c in body['comps'] if str(c).strip()][:8]
        changed['comps'] = len(sess['comps'])
    if isinstance(body.get('quotas'), dict):
        from migration import digital_twin_compare as CMP
        twins = list(_twins_for(payload, key, sess).values())
        CMP.apply_quotas(sess, twins, body['quotas'], seed=f"recruit|{sess['id']}")
        changed['quotas'] = True
    if changed.get('critics_room') and sess.get('critics_room'):
        from migration import digital_twin_compare as CMP
        CMP.seat_critics(sess, seed=f"critics|{sess['id']}")
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), 'changed': changed}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/recut', methods=['POST'])
def dt_pilot_recut(key, sid):
    """Cut B on the same room: a new test with the same seats and
    personas, the lineage pointing back. Opening is free; first Play
    carries the recut price."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_compare as CMP
    parent = _pilot(payload, sid)
    if not parent:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    twins = list(_twins_for(payload, key, parent).values())
    sess = CMP.open_recut(parent, twins, created_by=_uname(user), title=str(body.get('title') or '')[:120], session_id=uuid.uuid4().hex[:10])
    sess['twin_keys'] = list(parent.get('twin_keys') or [key])
    payload.setdefault('pilot_sessions', []).insert(0, sess)
    PT = _portraits()
    if PT is not None:
        try:
            PT.attach_portraits(sess, _s3())
        except Exception:
            pass
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess), 'parent_id': parent.get('id')})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/compare', methods=['GET'])
def dt_pilot_compare(key, sid):
    """Cut A against cut B (the lineage parent, or ``?other=<sid>``) and,
    when two twins sat in the room, the two audiences on this cut."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_compare as CMP
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    other_id = str(request.args.get('other') or (sess.get('lineage') or {}).get('parent') or '')
    out = {'success': True, 'session_id': sid}
    if other_id:
        other = _pilot(payload, other_id)
        if other and (other.get('dial') or {}).get('value') and (sess.get('dial') or {}).get('value'):
            parent, child = (other, sess) if (sess.get('lineage') or {}).get('parent') == other_id else (sess, other)
            out['recut'] = CMP.compare_recut(parent, child)
        elif other:
            out['recut_pending'] = 'Both cuts need to be prepared before they can be compared.'
    if sess.get('two_audiences'):
        out['two_audiences'] = sess['two_audiences']
    elif len({str(r.get('twin') or '') for r in ((sess.get('group') or {}).get('roster') or []) if r.get('twin')}) >= 2 and (sess.get('dial') or {}).get('value'):
        out['two_audiences'] = CMP.two_audiences(sess, _twins_for(payload, key, sess))
    out['recuts'] = sess.get('recuts') or []
    out['lineage'] = sess.get('lineage')
    return _nocache(jsonify(out))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/comps', methods=['GET', 'POST'])
def dt_pilot_comps(key, sid):
    """The comp set and the versus-comps column (POST sets the comps the
    test names, with any known numbers)."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_compare as CMP
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        if isinstance(body.get('comps'), list):
            sess['comps'] = [({'title': str(c)} if not isinstance(c, dict) else c) for c in body['comps'] if str(c).strip()][:8]
            sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    try:
        catalog = DT.list_runs(_s3())
    except Exception:
        catalog = []
    comps = CMP.comp_set(payload, sess, catalog_runs=catalog, load_twin=(lambda k: DT.load_twin(_s3(), k)))
    sess['comp_set'] = comps
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'comps': comps, 'session_id': sid}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/critics', methods=['POST'])
def dt_pilot_critics(key, sid):
    """Turn the room into a critics room before Play. When the catalog
    holds a critics and journalists twin, the reply names it so the
    page can open the test on that twin instead."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_compare as CMP
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if sess.get('play_charged'):
        return jsonify({'success': False, 'error': 'This test has played. Open a new test for the critics room.'}), 400
    try:
        critics_twin = CMP.find_critics_twin(DT.list_runs(_s3()))
    except Exception:
        critics_twin = None
    CMP.seat_critics(sess, seed=f"critics|{sess['id']}")
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), 'critics_twin': ({'key': critics_twin.get('key'), 'name': critics_twin.get('name')} if critics_twin else None)}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/talent-attach', methods=['POST'])
def dt_pilot_talent(key, sid):
    """How much a name lifts interest, by quadrant, with the Talent Fit
    IQ overlap when it exists and the room's 'who would you watch this
    with'. No charge beyond the test."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_compare as CMP
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    talent = ' '.join(str(body.get('talent') or body.get('name') or '').split())
    if not talent:
        return jsonify({'success': False, 'error': 'Name the talent.'}), 400
    rec = CMP.talent_attach(sess, payload, talent, str(body.get('role') or 'lead'), s3=_s3())
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'talent_attach': rec, 'session': PI.public_session(sess)}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/activation-brief', methods=['GET', 'POST'])
def dt_pilot_brief(key, sid):
    """The segments that scored highest on definitely recommend as an
    activation brief in the Spend Engine / Launch Lab handoff shape."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_compare as CMP
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if not sess.get('scorecard'):
        return jsonify({'success': False, 'error': 'Play the test first. The brief comes from the scorecard.'}), 400
    brief = CMP.activation_brief(sess, payload)
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'brief': brief, 'session_id': sid}))


@bp.route(f'{PREFIX}/pilot-norms/all', methods=['GET'])
def dt_pilot_norms_all():
    """The Norms tab: every format's cells for a genre, with our test
    count behind each cell. No sources leave the server."""
    user, err = _gate()
    if err:
        return err
    from migration import digital_twin_norms as N
    from migration import digital_twin_pilot as PI
    genre = str(request.args.get('genre') or 'all')
    out = {}
    for fmt in N.FORMATS:
        try:
            out[fmt] = PI.norms_for(fmt, genre, s3=_s3())
        except Exception:
            traceback.print_exc()
            out[fmt] = {}
    labels = {m: N.METRIC_LABEL.get(m, m.replace('_', ' ').title()) for m in N.METRICS}
    return _nocache(jsonify({'success': True, 'genre': genre, 'formats': list(N.FORMATS), 'format_labels': N.FORMAT_LABEL, 'genres': list(N.GENRES), 'genre_labels': N.GENRE_LABEL,
                             'metrics': list(N.METRICS), 'metric_labels': labels, 'norms': out,
                             'line': 'Every screening adds to these. A cell with no count yet reads from the nearest format until our own tests fill it.'}))


@bp.route(f'{PREFIX}/pilot-norms', methods=['GET'])
def dt_pilot_norms():
    """The norm and genre norm columns for a format and genre (no
    sources leave the server). For the scorecard and for Phase 2."""
    user, err = _gate()
    if err:
        return err
    from migration import digital_twin_pilot as PI
    return _nocache(jsonify({'success': True, 'norms': PI.norms_for(str(request.args.get('format') or 'pilot'), str(request.args.get('genre') or 'all'), s3=_s3())}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/export.csv', methods=['GET'])
def dt_pilot_csv(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    from migration import digital_twin_pilot as PI
    name = str((payload.get('meta') or {}).get('name') or 'twin').replace(' ', '_')
    return Response(PI.session_csv(sess), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{name}_pilot_{sid}.csv"'})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/export.pdf', methods=['GET'])
def dt_pilot_pdf(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    from migration import digital_twin_exports as X
    PT = _portraits()
    portrait_fn = (lambda h: PT.read_portrait(_s3(), h, PT.THUMB_SIZE)) if PT is not None else None
    return Response(X.build_pilot_pdf(payload, sess, portrait_fn=portrait_fn), mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{X.pilot_pdf_filename(payload, sess)}"'})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/export.pptx', methods=['GET'])
def dt_pilot_pptx(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    from migration import digital_twin_exports as X
    return Response(X.build_pilot_deck(payload, sess),
                    mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    headers={'Content-Disposition': f'attachment; filename="{X.pilot_deck_filename(payload, sess)}"'})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/report.pptx', methods=['GET'])
@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/report.pdf', methods=['GET'])
@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/dial.png', methods=['GET'])
def dt_pilot_report(key, sid):
    """The studio report (deck or PDF) and the dial chart image."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    from migration import digital_twin_report as R
    path = request.path
    if path.endswith('dial.png'):
        png = R.dial_chart_png(sess)
        if not png:
            return jsonify({'success': False, 'error': 'The dial chart is not available for this test yet.'}), 404
        resp = Response(png, mimetype='image/png')
        resp.headers['Cache-Control'] = 'private, max-age=120'
        return resp
    if path.endswith('.pdf'):
        return Response(R.build_report_pdf(payload, sess), mimetype='application/pdf', headers={'Content-Disposition': f'attachment; filename="{R.report_filename(payload, sess, ".pdf")}"'})
    return Response(R.build_report_deck(payload, sess), mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    headers={'Content-Disposition': f'attachment; filename="{R.report_filename(payload, sess, ".pptx")}"'})


@bp.route(f'{PREFIX}/portrait/<hash_>', methods=['GET'])
def dt_portrait(hash_):
    """A twin's generated portrait: the 512 px JPEG, or the 128 px thumb
    with ``?size=128``. Gated like the rest of the blueprint (a signed-in
    seat with the product); the hash is unguessable, so no per-twin
    check. 404 when the image is absent or portraits are off."""
    user, err = _gate()
    if err:
        return err
    h = str(hash_ or '').strip().lower()
    if not _PORTRAIT_HASH_RX.match(h):
        return jsonify({'success': False, 'error': 'portrait not found'}), 404
    PT = _portraits()
    if PT is None:
        return jsonify({'success': False, 'error': 'portrait not found'}), 404
    size = PT.THUMB_SIZE if str(request.args.get('size') or '') == str(PT.THUMB_SIZE) else PT.OUT_SIZE
    data = PT.read_portrait(_s3(), h, size)
    if not data:
        return jsonify({'success': False, 'error': 'portrait not found'}), 404
    resp = Response(data, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    resp.headers['ETag'] = f'"{h[:24]}-{size}-{len(data)}"'
    resp.headers['Content-Length'] = str(len(data))
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@bp.route(f'{PREFIX}/<path:key>', methods=['GET'])
def dt_get(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    out = {k: v for k, v in payload.items() if k != 'diagnostics'}
    out['runs'] = DT.product_runs(payload)       # stored runs of the retired Greenlight kind stay on the twin, never on the page
    return _nocache(jsonify({'success': True, 'twin': out, 'summary': DT.view_summary(payload)}))


@bp.route(f'{PREFIX}/<path:key>', methods=['DELETE'])
@bp.route(f'{PREFIX}/<path:key>/delete', methods=['POST'])
def dt_delete(key):
    """Delete a twin (2026-10-28, Jenna: "need to have a way to delete twins too.").
    The twin's creator or an admin; the object leaves the library and a
    copy is kept under system/digital_twins/deleted_twins/ for a 30-day
    undo. Never emailed. No charge."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    if not DT.can_delete_twin(payload, _uname(user), DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'Only the person who built this twin, or an admin, can delete it.'}), 403
    try:
        runs = DT.list_runs(_s3())
        run = next((r for r in runs if r.get('key') == key), None)
        removed = DT.delete_twin(_s3(), key, payload, deleted_by=_uname(user), run=run)
    except Exception:
        traceback.print_exc()
        removed = None
    if not removed:
        return jsonify({'success': False, 'error': 'The twin could not be deleted. Try again in a minute.'}), 500
    return _nocache(jsonify({'success': True, 'deleted': key, 'undo_until': removed.get('undo_until'),
                             'message': 'The twin left the library. It can be restored within 30 days by an admin.'}))


def register(app):
    """Called from app.py once. The key callers' screenings surface
    (/api/v1/screenings) registers here too, so app.py stays small."""
    if 'digital_twins_api' not in app.blueprints:
        app.register_blueprint(bp)
    try:
        import digital_twins_partner_api as _partner
        _partner.register(app)
    except Exception as e:
        print(f"[twin-api] screenings surface not registered: {e}")
    return bp


__all__ = ['bp', 'register', 'PREFIX', 'CREDITS']
