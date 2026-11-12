"""Digital Twins IQ API (2026-10-26). A Flask blueprint registered from
app.py so app.py stays small.

  GET  /api/digital-twins/list                       twins the caller can see (the shared library)
  GET  /api/digital-twins/how-to                     the in-product directions
  GET  /api/digital-twins/pricing                    the pricing lines for the seat
  GET  /api/digital-twins/profiles?q=                the Profile IQ profiles the seat can build a twin from (the walkthrough's picker)
  GET  /api/digital-twins/jobs/<job_id>              a build or run job's status
  POST /api/digital-twins/build                      queue a twin build (the walkthrough: source profile|demographics, profile_key,
                                                     demographics picks, panelists; or a bare subject from the chat)
  POST /api/digital-twins/list-preview               a list of content URLs (csv / txt / xlsx file or pasted text) read back: count, hosts,
                                                     rows dropped, the category by the hosts, the price; nothing charged (2026-11-04)
  POST /api/digital-twins/build-from-list            the confirm: charge once (the audience build plus the twin), queue the job that pulls
                                                     the audience the list defines and builds the twin from it
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
  POST /api/digital-twins/<key>/panel/<sid>/ask      ask the room (synchronous, seconds; the tier cap, the upgrade offer at twenty)
  POST /api/digital-twins/<key>/panel/<sid>/upgrade  move a 1-20 room to 1-40 for the difference at the plan rate (2026-11-05)
  DELETE /api/digital-twins/<key>/panel/<sid>        delete a room (creator or admin; 30-day undo copy under system/digital_twins/deleted_rooms/)
  POST /api/digital-twins/<key>/panel/<sid>/delete   the same for clients that cannot send DELETE
  DELETE /api/digital-twins/<key>                    delete a twin (creator or admin; 30-day undo copy under system/digital_twins/deleted_twins/)
  POST /api/digital-twins/<key>/delete               the same for clients that cannot send DELETE
  GET  /api/digital-twins/<key>/panel/<sid>/export.csv
  GET  /api/digital-twins/<key>/panel/<sid>/export.pdf
  POST /api/digital-twins/<key>/pilot/open           open a Pilot Testing session (one GROUP, charged at open at the plan rate; 2026-11-05). watch_url = any streamer or digital link
  POST /api/digital-twins/<key>/pilot/<sid>/frame    a frame off the mirrored screen at clock t; the room reads it and reacts (live watch-link tests)
  GET  /api/digital-twins/<key>/pilot/<sid>          the test
  PATCH /api/digital-twins/<key>/pilot/<sid>         rename
  POST /api/digital-twins/<key>/pilot/<sid>/upload   the cut (mp4 / mov / webm)
  POST /api/digital-twins/<key>/pilot/<sid>/prepare  the room watches the cut: a file goes to the read worker (job_id; poll /jobs/<id>), a slate runs here
  POST /api/digital-twins/<key>/pilot/<sid>/play     Play (the group was charged at open); the room is live on the clock (409 preparing while the worker watches)
  POST /api/digital-twins/<key>/pilot/<sid>/upgrade  move a 1-20 test to 1-40 for the difference at the plan rate (2026-11-05)
  POST /api/digital-twins/<key>/pilot/<sid>/ask      a question at the playhead; it carries the last 60 to 90 seconds of what played
  POST /api/digital-twins/<key>/pilot/<sid>/stop     stop, keep the scorecard, add the test to the norms, remove the file when the test asked for it
  GET  /api/digital-twins/<key>/pilot/<sid>/minute   ?t=<seconds>&mode=room|cohort|person&id=<cohort key or seat>: the minute under the pointer
                                                     ({t, avg, n, lines, summary, lean}); the read is cached on the test, a finished test has every minute ready
  PATCH /api/digital-twins/<key>/pilot/<sid>         rename, or set genre / mode / cut_label / rough_cut / purge_media_after
  POST /api/digital-twins/<key>/pilot/<sid>/norm     the user sets what the test is compared to ({genre}); a stopped test's norm lines and columns
                                                     re-read on the stored scorecard, the test re-filed in the norms, the forecast re-read; no re-run
  DELETE /api/digital-twins/<key>/pilot/<sid>        delete (30-day undo; the file follows the test into the undo copy)
  POST /api/digital-twins/<key>/pilot/<sid>/restore  the deleted test back from its undo copy (the deleter or an admin)
  POST /api/digital-twins/<key>/pilot/setups         save the setup form as a named setup on the twin (upsert by id); nothing seated or charged
  DELETE /api/digital-twins/<key>/pilot/setups/<id>  remove a saved setup (the person who saved it, or an admin)
  POST /api/digital-twins/<key>/pilot/setups/<id>/duplicate  a new saved setup with every input of this one ('<name> copy'); the cut it carries comes along
  POST /api/digital-twins/<key>/pilot/<sid>/duplicate  a test duplicated into a new saved setup: every input, none of the results, the cut copied,
                                                     priced like a fresh setup (Jenna 2026-11-04: duplicate a room with all of the input, change one thing)
  POST /api/digital-twins/<key>/panel/<sid>/duplicate  a new room with the same twins, cohorts and seats ('<name> copy'); no questions, nothing paid
  GET  /api/digital-twins/<key>/pilot/<sid>/media-url  a signed link to the cut (ten minutes; creator or admin only)
  GET  /api/digital-twins/<key>/pilot/<sid>/media    the uploaded cut streamed (creator or admin only; the fallback player path)
  GET  /api/digital-twins/pilot-norms?format=&genre=  the norm and genre norm columns (no sources)
  GET  /api/digital-twins/pilot-norms/all?genre=      every format's cells for the Norms tab, with our test count per cell
  POST /api/digital-twins/<key>/pilot/<sid>/assets    a further trailer, an episode, key art (image) or a script file (text)
  POST /api/digital-twins/<key>/pilot/<sid>/stimulus  options (titles), logline, text, cast, comps, quotas, mode flags, watch_url
  POST /api/digital-twins/<key>/pilot/<sid>/recut     cut B on the same room (lineage; a new group, charged at open)
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
super admins are free. Pricing is PER GROUP (Jenna 2026-11-05): one
focus group room or one pilot test, any mode, charged as it opens at the
company's plan rate (individual / package / enterprise) for its tier
(1-20 or 1-40 questions), through migration/digital_twin_pricing. The
twin build carries no charge of its own. On top of the priced steps, every model call a
build, a run or a room question makes carries the seat's pay-per-use
attribution (``_usage_extras``), so Prometheus usage is metered at the
published rate like any other Prometheus work (Jenna 2026-10-27:
'aside from these fees there needs to be billed prometheus metered
pricing'). No email is sent from here.
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import time
import traceback
import uuid

from flask import Blueprint, Response, g, jsonify, request, session

from migration import digital_twins as DT
from migration import digital_twin_company as CO
from migration import digital_twin_pricing as PR
from migration import digital_twin_synthesis as SYN

bp = Blueprint('digital_twins_api', __name__)
PREFIX = '/api/digital-twins'
CREDITS = {'digital_twins_iq': 15, 'digital_twin_run': 3, 'digital_twin_spend': 5, 'digital_twin_panel': 4, 'digital_twin_panel_more': 3, 'digital_twin_panelist': 1,
           'digital_twin_pilot': 4, 'digital_twin_pilot_more': 3,
           'digital_twin_trailer': 3, 'digital_twin_title': 2, 'digital_twin_key_art': 2, 'digital_twin_concept': 3, 'digital_twin_script': 3, 'digital_twin_recut': 3}
# the per-group lines (Jenna 2026-11-05): one focus group room or one pilot test, any mode, at the company's plan rate
CREDITS.update(PR.CREDITS)


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


class _TabDenied(Exception):
    """This seat does not have the Digital Twins tab this path needs."""


def _denied_tab_reply():
    return jsonify({'success': False, 'error': 'That part of Digital Twins IQ is not on this account.'}), 403


def _tab_from_request():
    """Product tab this path belongs to, or None when the path is shared."""
    return DT.tab_for_api_path(str(getattr(request, 'path', None) or ''))


def _gate():
    """(user, err_response). The product flag or admin, else 403."""
    user = _user()
    if not user:
        return None, (jsonify({'success': False, 'error': 'Sign in to use Digital Twins IQ.'}), 401)
    user = dict(user)
    user['role'] = _role(user)
    # the users.json record has no username field (the dict key is the username): the company scope and the
    # creator checks compare usernames, so the session's rides on the record
    try:
        user.setdefault('username', _uname(user))
    except Exception:
        pass
    if _menu_locked(user):
        return None, (jsonify({'success': False, 'error': 'Digital Twins IQ is not open for this account yet.'}), 403)
    if not DT.has_access(user):
        return None, (jsonify({'success': False, 'error': 'Digital Twins IQ access required. Ask your admin to switch it on for your account.'}), 403)
    need = _tab_from_request()
    if need and not DT.tab_allowed(user, need):
        return None, _denied_tab_reply()
    try:
        g.dt_user = user
    except Exception:
        pass
    return user, None


@bp.errorhandler(_TabDenied)
def _on_tab_denied(_e):
    return _denied_tab_reply()


def _menu_locked(user):
    """Company lock: the menu row stays, the product does not open."""
    a = _app()
    if a is None:
        return False
    try:
        import wallet as w
        data = a.load_users()
    except Exception:
        return False
    try:
        return bool(w.digital_twins_menu_locked(user, data))
    except Exception:
        return False


def _twin_or_404(user, key):
    key = str(key or '')
    if not key.startswith(DT.S3_PREFIX) or not key.endswith('.json.gz'):
        return None, (jsonify({'success': False, 'error': 'twin not found'}), 404)
    runs = DT.list_runs(_s3())
    run = next((r for r in runs if r.get('key') == key), None)
    # company scope (Jenna 2026-11-05): another company's twin reads as not there, in plain words
    if not DT.in_company(user, key, run):
        return None, (jsonify({'success': False, 'error': CO.NOT_IN_LIBRARY}), 404)
    if not DT.can_open(user, key, run):
        return None, (jsonify({'success': False, 'error': 'This twin is not on your list. Ask your admin to add it.'}), 403)
    payload = DT.load_twin(_s3(), key)
    if not isinstance(payload, dict):
        return None, (jsonify({'success': False, 'error': 'twin not found'}), 404)
    if not DT.in_company(user, key, run, payload):
        return None, (jsonify({'success': False, 'error': CO.NOT_IN_LIBRARY}), 404)
    return payload, None


def _company_filter(user):
    """The ``?company=`` a staff seat asked for on a listing; everyone
    else is their own company and the parameter is ignored."""
    if not CO.is_staff(user, _uname(user)):
        return None
    c = str(request.args.get('company') or '').strip()
    return c or None


def _twin_company(payload, key):
    return CO.twin_company(payload, None, str(key or ''))


def _norms_company(user):
    """Whose own tests the norms read from: the caller's company; for
    staff the ``?company=`` filter, else every company (the view Jenna
    has today)."""
    if CO.is_staff(user, _uname(user)):
        return _company_filter(user)
    return CO.caller_company(user, _uname(user))


def _price_fn(username):
    try:
        from prometheus.legacy.watch import _pm_usd
        return lambda key, dflt: _pm_usd(key, dflt, username)
    except Exception:
        return None


def _price_label(key, username):
    try:
        from prometheus.legacy.watch import _pm_usd_label, _pm_usd
        return _pm_usd_label(_pm_usd(key, DT.PRICING.get(key, 0.0), username), username)
    except Exception:
        return f"${DT.PRICING.get(key, 0.0):,.0f}"


def _money_symbol(username):
    try:
        from prometheus.legacy.watch import _pm_money_symbol
        return _pm_money_symbol(username) or '$'
    except Exception:
        return '$'


def _plan(user):
    """The seat's company plan (individual / package / enterprise) the
    group rates read from (migration/digital_twin_pricing)."""
    try:
        return PR.plan_for_user(user, _uname(user))
    except Exception:
        return PR.DEFAULT_PLAN


def _group_charge_fn(user):
    """``charge_fn(tool_key, description, job_id)`` for the pricing
    module: the host's consume_credit through _charge (staff free)."""
    return lambda key, desc, job_id: _charge(user, key, desc, job_id)


def _group_refund_fn(user):
    return lambda credits, reason, job_id, amount_usd, pull_type: _refund(user, credits, reason, job_id=job_id, amount_usd=amount_usd, pull_type=pull_type)


def _open_gate(user, tier, plan=None):
    """The individual plan's card-and-balance gate before a group opens
    (None when it may open, else the 402 reply with the add-funds link)."""
    try:
        ok, reply = PR.gate_open(user, _uname(user), plan=plan if plan is not None else _plan(user), tier=tier,
                                 price_fn=_price_fn(_uname(user)), symbol=_money_symbol(_uname(user)))
    except Exception:
        traceback.print_exc()
        return None
    if ok:
        return None
    return jsonify(reply), 402


def _group_card(user):
    """What the page needs to quote a group (Jenna 2026-11-05): the seat's
    plan, the tiers it may choose, the live rate per tier, the upgrade
    price and one quote line per tier."""
    uname = _uname(user)
    plan = _plan(user)
    pf = _price_fn(uname)
    sym = _money_symbol(uname)
    try:
        free = bool(PR.seat_is_free(user, uname))
        if free:
            # Included with your plan (Jenna 2026-11-08): an unlimited
            # company's page reads that in place of every per-group price
            return {'plan': plan, 'plan_label': PR.plan_label(plan), 'tiers': list(PR.tiers_for(plan)), 'default_tier': PR.default_tier(plan),
                    'rates': {str(t): 0.0 for t in PR.tiers_for(plan)}, 'labels': {str(t): PR.INCLUDED_LABEL for t in PR.tiers_for(plan)},
                    'upgrade_usd': 0.0, 'upgrade_label': PR.INCLUDED_LABEL,
                    'quote': {str(t): PR.quote_line(plan, t, pf, sym, included=True) for t in PR.tiers_for(plan)}, 'free': True,
                    'included': True, 'card': [PR.INCLUDED_LINE], 'twin_included': True}
        return {'plan': plan, 'plan_label': PR.plan_label(plan), 'tiers': list(PR.tiers_for(plan)), 'default_tier': PR.default_tier(plan),
                'rates': {str(t): PR.rate(plan, t, pf) for t in PR.tiers_for(plan)}, 'labels': {str(t): PR.money(PR.rate(plan, t, pf), sym) for t in PR.tiers_for(plan)},
                'upgrade_usd': PR.upgrade_rate(plan, pf), 'upgrade_label': PR.money(PR.upgrade_rate(plan, pf), sym),
                'quote': {str(t): PR.quote_line(plan, t, pf, sym) for t in PR.tiers_for(plan)}, 'free': free,
                'card': PR.rate_card_lines(pf, sym), 'twin_included': True}
    except Exception:
        traceback.print_exc()
        return None


def _charge_group(user, payload, sess, *, kind, tier=None, plan=None):
    """Charge one group at open (the room id is the run id). None when
    charged, free or already paid; else the funds reply."""
    return PR.charge_group_open(user, payload, sess, plan=plan if plan is not None else _plan(user), tier=tier, charge_fn=_group_charge_fn(user),
                                username=_uname(user), kind=kind, by=_uname(user))


def _refund_group(user, sess, reason):
    try:
        return PR.refund_group_open(user, sess, refund_fn=_group_refund_fn(user), reason=reason)
    except Exception:
        traceback.print_exc()
        return False


def _question_gate(user, payload, sess, body=None, *, kind='panel'):
    """Before a question runs: a group never charged (a room opened free
    before 2026-11-05, or one whose open could not charge) is charged
    now; then the tier cap. At twenty questions a 1-20 group is offered
    the move to 1-40 for the difference (402 with the offer) and the
    twenty-first waits until the body carries ``accept_upgrade``; at
    forty the group is full (409). None when the question may run."""
    body = body or {}
    plan = _plan(user)
    if not PR.group_paid(sess):
        tier = body.get('tier') if body.get('tier') is not None else sess.get('group_tier')
        funds = _charge_group(user, payload, sess, kind=kind, tier=tier, plan=plan)
        if funds is not None:
            return funds
    uname = _uname(user)
    verdict, offer = PR.question_gate(sess, plan, accept_upgrade=bool(body.get('accept_upgrade')), price_fn=_price_fn(uname), symbol=_money_symbol(uname))
    if verdict == 'ok':
        return None
    if verdict == 'accept':
        funds = PR.accept_tier_upgrade(user, payload, sess, plan=plan, charge_fn=_group_charge_fn(user), username=uname, by=uname)
        return funds
    if verdict == 'offer':
        return jsonify({'success': False, 'guidance': True, 'upgrade': offer, 'error': offer['line'], 'questions_asked': PR.questions_asked(sess),
                        'question_cap': PR.question_cap(sess, plan), 'session_id': str(sess.get('id') or '')}), 402
    return jsonify({'success': False, 'guidance': True, 'cap': True, 'error': PR.cap_reply(sess, plan), 'questions_asked': PR.questions_asked(sess),
                    'question_cap': PR.question_cap(sess, plan), 'session_id': str(sess.get('id') or '')}), 409


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


def _team_notice(fn_name, *args, **kw):
    """A team notice on a twin event (prometheus.twin_notify: a twin
    built, a pilot test created, its results, a focus group room opened;
    Jenna 2026-11-05). Idempotent per event there; never in the way of
    the action here."""
    try:
        from prometheus import twin_notify as TN
        return getattr(TN, fn_name)(*args, s3=_s3(), **kw)
    except Exception as e:
        print(f"[digital-twins] team notice {fn_name} skipped: {e}")
        return None


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
    staff = CO.is_staff(user, _uname(user))
    vis = DT.visible_runs(runs, user, company=_company_filter(user))
    try:
        from migration.digital_twin_covers import schedule_missing
        schedule_missing(_s3(), vis)
    except Exception:
        traceback.print_exc()
    out = {'success': True, 'runs': vis, 'is_admin': DT.is_admin(user), 'allow_all': DT.is_admin(user) or '*' in DT.allowed_keys(user),
           'pricing': DT.pricing_lines(_price_fn(_uname(user))), 'group_card': _group_card(user),
           'company': CO.caller_company(user, _uname(user)), 'is_staff': staff}
    if staff:
        # the company filter for staff (default: every company) and, for a super admin, the assign control's choices
        out['company_filter'] = _company_filter(user) or ''
        out['companies'] = CO.companies(CO.annotate(list(runs)))
        out['can_assign'] = _role(user) == 'super_admin'
    return _nocache(jsonify(out))


@bp.route(f'{PREFIX}/<path:key>/company', methods=['POST', 'PATCH'])
def dt_assign_company(key):
    """Assign a twin to a company (super admin only). Its pilots, rooms,
    saved setups and reports live on the twin and move with it. Body:
    ``{company}``. Nothing is emailed."""
    user, err = _gate()
    if err:
        return err
    if _role(user) != 'super_admin':
        return jsonify({'success': False, 'error': 'Only a Crosswalk super admin can assign a twin to a company.'}), 403
    body = request.get_json(silent=True) or {}
    company = str(body.get('company') or request.form.get('company') or '').strip()
    if not company:
        return jsonify({'success': False, 'error': 'Name the company.'}), 400
    try:
        rec = CO.assign(_s3(), str(key or ''), company, by=_uname(user))
    except ValueError as e:
        msg = str(e)
        return jsonify({'success': False, 'error': 'twin not found' if 'not found' in msg else msg}), 404 if 'not found' in msg else 400
    except Exception:
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'The twin could not be assigned. Try again in a minute.'}), 500
    try:
        CO.reset_cache()
    except Exception:
        pass
    return _nocache(jsonify({'success': True, **rec, 'label': CO.company_label(rec.get('company') or ''),
                             'message': f"The twin now belongs to {CO.company_label(rec.get('company') or '')}, with its tests, rooms and saved setups."}))


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
    uname = _uname(user)
    plan = _plan(user)
    pf = _price_fn(uname)
    return jsonify({'success': True, 'lines': DT.pricing_lines(pf), 'defaults': DT.PRICING, 'plan': plan, 'plan_label': PR.plan_label(plan),
                    'rates': {f"{p}_{t}": PR.rate(p, t, pf) for (p, t) in PR.RATES}, 'upgrade_usd': PR.upgrade_rate(plan, pf),
                    'tiers': list(PR.tiers_for(plan)), 'default_tier': PR.default_tier(plan), 'card': PR.rate_card_lines(pf, _money_symbol(uname)),
                    'quote': {str(t): PR.quote_line(plan, t, pf, _money_symbol(uname)) for t in PR.tiers_for(plan)}})


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
              'demographics': demo_spec if source == 'demographics' else None, 'panelists': panelists, 'via': 'dashboard'}
    from prometheus import digital_twin_job as J
    from prometheus import twin_named_pull as TNP
    uname = _uname(user)
    job_id = uuid.uuid4().hex[:12]
    # a named subject with no audience on file is pulled first (2026-11-04): the audience build and the twin are charged
    # once here, the job queues the audience and the engine hands the twin back when it lands
    on_file = ''
    pullable = TNP.pullable(inputs)
    if pullable:
        on_file = TNP.audience_on_file(_s3(), subject, inputs.get('window'))
        if on_file:
            inputs['profile_key'] = on_file
    pull = pullable and not on_file
    profile_run_id = secrets.token_urlsafe(10) if pull else ''
    charged, funds = TNP.charge(user, uname, subject, pull=pull, profile_run_id=profile_run_id, twin_job_id=job_id)
    if funds:
        return jsonify({'success': False, 'guidance': True, 'error': funds}), 402
    inputs.update({'charged': charged, 'pull_audience': pull, 'profile_run_id': profile_run_id, 'requester_email': str((user or {}).get('email') or '').strip()})
    rec = J.start('twin_build', job_id, uname, inputs, thread_id=str(body.get('thread_id') or ''), extras=_usage_extras(user))
    return jsonify({'success': True, 'job_id': job_id, 'status': rec.get('status'), 'subject': subject, 'panelists': panelists,
                    'extra_panelists': DT.extra_panelists(panelists), 'message': TNP.promise(subject) if pull else J.build_promise(subject),
                    'pulls_audience': pull, 'price': TNP.price(uname) if pull else None})


# ---- a twin from a list of content URLs (Jenna 2026-11-04) ----
# Two calls: the list is read back first (count, hosts, rows dropped, the category by the hosts, the price;
# nothing charged), then the confirm charges once and queues the job that pulls the audience the list defines
# and builds the twin from it. The parsed list keeps the URLs only; nothing else in the upload is stored.
_LIST_MAX_BYTES = 25 * 1024 * 1024


@bp.route(f'{PREFIX}/list-preview', methods=['POST'])
def dt_list_preview():
    user, err = _gate()
    if err:
        return err
    from prometheus import twin_url_list as TUL
    file_name, data, text, name, ws, we = '', b'', '', '', '', ''
    if request.files or request.form:
        f = request.files.get('file')
        if f and f.filename:
            file_name, data = f.filename, f.read()
        text = str(request.form.get('text') or '')
        name = str(request.form.get('name') or '')
        ws, we = str(request.form.get('window_start') or ''), str(request.form.get('window_end') or '')
    else:
        body = request.get_json(silent=True) or {}
        text, name = str(body.get('text') or ''), str(body.get('name') or '')
        w = body.get('window') if isinstance(body.get('window'), dict) else {}
        ws, we = str(w.get('start') or ''), str(w.get('end') or '')
    if len(data) > _LIST_MAX_BYTES:
        return jsonify({'success': False, 'error': 'That file is larger than 25 MB. Send the URL column on its own.'}), 400
    urls, dropped = [], 0
    if data:
        urls, dropped = TUL.urls_from_bytes(file_name, data)
    if not urls and text.strip():
        urls, dropped = TUL.urls_from_text(text)
    if not urls:
        return jsonify({'success': False, 'error': 'I could not find a URL in that. One link per row works best (a column named url or link), or paste the links one per line.'}), 400
    window = {'start': ws, 'end': we} if ws and we else None
    rec = TUL.store_list(_uname(user), TUL.classify(urls), name=name, window=window, file_name=file_name or 'pasted URLs', dropped=dropped,
                         s3=_s3(), bucket=DT.S3_BUCKET)
    s = rec.get('summary') or {}
    return _nocache(jsonify({'success': True, 'list_token': rec['token'], 'count': rec['count'], 'dropped': rec['dropped'], 'hosts': (s.get('hosts') or [])[:12],
                             'platforms': s.get('platforms') or {}, 'kinds': s.get('kinds') or {}, 'category': rec['brand_category'],
                             'category_reason': rec['category_reason'], 'window': rec['window'], 'window_words': TUL.window_words(rec['window']),
                             'line': TUL.list_words(rec), 'price': TUL.price(_uname(user)), 'sample': [it['url'] for it in rec['items'][:8]],
                             'file_name': rec.get('file_name') or ''}))


@bp.route(f'{PREFIX}/build-from-list', methods=['POST'])
def dt_build_from_list():
    user, err = _gate()
    if err:
        return err
    from prometheus import twin_url_list as TUL
    body = request.get_json(silent=True) or {}
    name = ' '.join(str(body.get('name') or '').split())[:140]
    if not name:
        return jsonify({'success': False, 'error': "Name the audience first: a plain name like 'Yellowstone and Landman viewers' or 'Our fall campaign readers'."}), 400
    rec = TUL.load_list(str(body.get('list_token') or ''), s3=_s3(), bucket=DT.S3_BUCKET)
    if not rec or (str(rec.get('owner') or '') != _uname(user) and not DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'That list is not on your account any more. Upload it again and I will read it back.'}), 400
    window = body.get('window') if isinstance(body.get('window'), dict) and body['window'].get('start') else rec.get('window')
    try:
        panelists = int(body.get('panelists') or 0)
    except (TypeError, ValueError):
        panelists = 0
    panelists = max(2, min(panelists, DT.PANEL_SEATS_MAX)) if panelists else 0
    job_id, line, funds = TUL.start(user, _uname(user), rec, name, window, thread_id=str(body.get('thread_id') or ''),
                                    purpose=str(body.get('purpose') or '')[:400], panelists=panelists, ask_text=f"Build a twin of {name} from a list",
                                    extras=_usage_extras(user), via='dashboard')
    if funds:
        return jsonify({'success': False, 'guidance': True, 'error': funds}), 402
    return jsonify({'success': True, 'job_id': job_id, 'status': 'queued', 'subject': name, 'message': line, 'price': TUL.price(_uname(user))})


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
    sess = next((s for s in payload.get('panel_sessions') or [] if s.get('id') == sid), None)
    if sess:
        try:   # the room stands for its cuts (Jenna 2026-11-05); a room from before the sizing gets it here, then every seat says who it stands for
            from migration import digital_twin_sizing as SZ
            SZ.ensure_sizing(payload, sess, s3=_s3())
            from migration import digital_twin_panel as P
            P.ensure_seat_stands_for(sess)
        except Exception:
            pass
    return sess


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
    # A room is one GROUP, charged as it opens at the company's plan rate for the tier chosen here (1-20 or 1-40
    # questions; Jenna 2026-11-05, the per-group card). An empty room with the same twins, segments and seats is
    # reused instead of opening another (Jenna: repeated clicks on Open the room left four empty 'Room: Gilmore
    # Girls'); a reused room already paid is not charged again.
    plan = _plan(user)
    tier = PR.normalize_tier(body.get('tier'), plan)
    reused = P.find_reusable_session(payload, twins, segments, in_room, created_by=_uname(user), title=title)
    if reused is not None:
        sess = reused
        if P.needs_reseat(sess, twins):
            P.rebuild_group(sess, twins, seed=str(sess.get('id') or ''))
        P.ensure_seat_stands_for(sess)
    else:
        gate = _open_gate(user, tier, plan)
        if gate is not None:
            return gate
        sess = P.open_session(twins, segments=segments, in_room=in_room, title=title, created_by=_uname(user), session_id=uuid.uuid4().hex[:10])
        sess['twin_keys'] = [str((t.get('meta') or {}).get('key') or '') for t in twins]
        PR.record_tier(sess, tier, plan)
    funds = _charge_group(user, payload, sess, kind='panel', tier=tier, plan=plan)     # the group, at open (a paid room charges nothing)
    if funds is not None:
        if reused is not None:
            doc = funds[0].get_json() or {}
            doc['session'] = sess
            return jsonify(doc), funds[1]
        return funds                     # nothing is saved: the room never opened
    if reused is None:
        payload.setdefault('panel_sessions', []).insert(0, sess)
    try:
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
    except Exception:
        # the room did not open: the group's charge goes back (refund_credit by the room id)
        traceback.print_exc()
        _refund_group(user, sess, 'Focus group room did not open')
        if reused is None:
            payload['panel_sessions'] = [x for x in payload.get('panel_sessions') or [] if x is not sess]
        return jsonify({'success': False, 'error': 'The room could not be opened. Nothing was charged; try again in a moment.'}), 500
    if reused is None:
        _team_notice('notify_room_opened', payload, sess, username=_uname(user), via='dashboard')   # a new room, once (never a question)
    first_q = str(body.get('q') or body.get('question') or '').strip()
    rec = None
    if first_q:
        funds = _question_gate(user, payload, sess, body, kind='panel')
        if funds is not None:
            doc = funds[0].get_json() or {}
            doc['session'] = sess        # the room stays open; the question waits
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


def _charge_question(user, payload, sess, body=None):
    """Before a room answers: the group's charge if the room was never
    charged (opened free before 2026-11-05), then the tier cap and the
    upgrade offer (``_question_gate``). None when the question may run."""
    return _question_gate(user, payload, sess, body, kind='panel')


def _refund(user, credits, reason, job_id='', amount_usd=None, pull_type=''):
    """Hand a charge back through the host's refund_credit (the unit the
    charge took, idempotent by `job_id`: the group's room id, the seats'
    charge id). `amount_usd` pins the dollars on a wallet seat."""
    a = _app()
    if a is None or not hasattr(a, 'refund_credit') or _role(user) == 'super_admin':
        return
    try:
        kw = {}
        if amount_usd:
            kw['amount_usd'] = float(amount_usd)
        if pull_type:
            kw['pull_type'] = str(pull_type)
        if job_id:
            a.refund_credit(_uname(user), credits=credits, reason=reason, job_id=str(job_id), **kw)
        else:
            a.refund_credit(_uname(user), credits=credits, reason=reason, **kw)
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
    dirty = False
    try:   # an in-flight question whose job stalled is picked back up once, or marked failed with a plain line (never 'researching' forever)
        from migration import digital_twin_room_jobs as RJ
        if RJ.sweep_session(_s3(), key, sess):
            dirty = True
    except Exception:
        traceback.print_exc()
    try:   # content left 'processing' after its thread died reads as failed with plain copy
        if _assets_module().sweep_assets(sess):
            dirty = True
    except Exception:
        traceback.print_exc()
    if dirty:
        DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': _panel_public(sess)}))


def _panel_public(sess):
    """The room for the page: the content without its storage keys."""
    if not isinstance(sess, dict) or not sess.get('assets'):
        return sess
    try:
        out = dict(sess)
        out['assets'] = _assets_module().public_assets(sess)
        return out
    except Exception:
        return sess


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


def _cased_question(q: str, sess: dict, payload: dict) -> str:
    """The question as typed, with the names the room or test knows (the
    cast, the title, the characters, the platforms, the cohorts) in
    their own casing, so the thread's echo and every export print
    'Jennifer Beals' however it was typed. Never fails an ask."""
    try:
        from migration.digital_twin_exports import known_names, proper_case
        return proper_case(q, **known_names(sess, payload)) or q
    except Exception:
        return q


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
    q = _cased_question(q, sess, payload)     # a name the record knows keeps its own casing in the thread and the exports (Jenna 2026-11-05)
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
    funds = _charge_question(user, payload, sess, body)      # the group if never charged, then the tier cap and the upgrade
    if funds is not None:
        DT.save_twin(_s3(), key, payload)          # an accepted upgrade or a late group charge is on the record either way
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
    # A room that answers as a job put this record on the twin before its thread started, and that thread now owns the
    # record. Saving again from here would write a copy taken while the room is working (2026-11-11); an answer that came
    # back inline, or a room whose own save did not land, is saved here as before.
    try:
        from migration import digital_twin_room as _R
        _landed = _R.saved_at_start(rec.get('job_id'))
    except Exception:
        _landed = False
    if not _landed:
        DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'question': rec, 'session_id': sid, 'questions_asked': len(sess['questions']), 'questions_paid': sess.get('questions_paid'),
                    'question_cap': PR.question_cap(sess, _plan(user)), 'group_tier': PR.group_tier(sess, _plan(user)),
                    'job_id': rec.get('job_id'), 'stage': rec.get('stage')})


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/upgrade', methods=['POST'])
def dt_panel_upgrade(key, sid):
    """Move a 1-20 room to 1-40 for the difference at the company's plan
    rate (Jenna 2026-11-05). The twenty-first question runs once this
    has been accepted; a room already at 1-40 is left alone."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    plan = _plan(user)
    if not PR.group_paid(sess):
        funds = _charge_group(user, payload, sess, kind='panel', plan=plan)
        if funds is not None:
            return funds
    funds = PR.accept_tier_upgrade(user, payload, sess, plan=plan, charge_fn=_group_charge_fn(user), username=_uname(user), by=_uname(user))
    if funds is not None:
        return funds
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': sess, **PR.public_fields(sess, plan)}))


# ---- content in the room (Jenna 2026-11-05: "upload content into the focus group (a video, image, mp3) and it should assess it
# for you the same way the pilot assesses video ... then ask questions about the content as an a/b testing kind of thing").
# One file at a time (multipart ``file``, or ``url`` to a hosted file), a label ('A', 'B' or the user's name for it); the room
# stores it, takes it in on a background thread (a video's frames on the read worker when ffmpeg is not here), reacts to it and
# lands the read as a question-like record. The read counts toward the group's question cap as one question, so the cap gate
# runs before the file is taken. Nothing else is charged: content is included in the group.
def _assets_module():
    from migration import digital_twin_assets as A
    return A


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/assets', methods=['GET'])
def dt_panel_assets_list(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    A = _assets_module()
    if A.sweep_assets(sess):
        DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'assets': A.public_assets(sess), 'session_id': sid}))


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/assets', methods=['POST'])
def dt_panel_assets_add(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    A = _assets_module()
    body = request.get_json(silent=True) or {}
    label = ' '.join(str(request.form.get('label') or body.get('label') or '').split())[:A.MAX_LABEL]
    url = str(request.form.get('url') or body.get('url') or '').strip()
    f = request.files.get('file') or request.files.get('asset') or request.files.get('content')
    if (f is None or not getattr(f, 'filename', '')) and not url:
        return jsonify({'success': False, 'error': 'Attach a video, an image or an audio file, or paste a direct link to one.'}), 400
    if f is not None and getattr(f, 'filename', '') and not A.accept(f.filename, getattr(f, 'mimetype', '') or ''):
        return jsonify({'success': False, 'error': 'Use a video (mp4, mov, webm), an image (png, jpg, webp) or audio (mp3, m4a, wav).'}), 400
    if sess.get('created_by') and str(sess.get('created_by')) != _uname(user) and not DT.is_admin(user):
        return jsonify({'success': False, 'error': 'Only the person who opened this room can add content to it.'}), 403
    inflight = [q for q in (sess.get('questions') or []) if str(q.get('stage') or '') in ('researching', 'the room is thinking', 'moderator is reading', 'picking it back up') and not q.get('finished_at')]
    if inflight:
        return jsonify({'success': False, 'error': 'The room is still working on the last question. Add the content when it lands.'}), 409
    # the read is one question on the cap (the group if never charged, then the tier cap and the upgrade offer)
    funds = _question_gate(user, payload, sess, {**body, **{k: request.form.get(k) for k in ('accept_upgrade', 'tier') if request.form.get(k) is not None}}, kind='panel')
    if funds is not None:
        DT.save_twin(_s3(), key, payload)
        return funds
    s3 = _s3()
    if f is not None and getattr(f, 'filename', ''):
        raw = f.read()
        asset, aerr = A.store_upload(s3, sess, twin_key=key, raw=raw, filename=f.filename, content_type=getattr(f, 'mimetype', '') or '', label=label, added_by=_uname(user))
    else:
        asset, aerr = A.store_url(s3, sess, twin_key=key, url=url, label=label, added_by=_uname(user))
    if asset is None:
        return jsonify({'success': False, 'error': aerr or A.FAILED_LINE}), 400
    sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    DT.save_twin(s3, key, payload)          # the record is on the room before the thread starts
    try:
        where = A.start_processing(s3, key, sid, asset, username=_uname(user), usage_extras=_usage_extras(user))
    except Exception:
        traceback.print_exc()
        where = {'where': 'none', 'job_id': None}
    if asset.get('job_id') or asset.get('stage'):
        DT.save_twin(s3, key, payload)
    return _nocache(jsonify({'success': True, 'asset': A.public_asset(asset), 'assets': A.public_assets(sess), 'session_id': sid, 'processing': where.get('where'),
                             'questions_asked': PR.questions_asked(sess), 'question_cap': PR.question_cap(sess, _plan(user)), 'session': _panel_public(sess)}))


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/assets/<aid>/image', methods=['GET'])
def dt_panel_asset_image(key, sid, aid):
    """The thumbnail (``n=0``) or a page frame (``n=1..``) of a piece of
    content, for the strip and the asset read."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    A = _assets_module()
    asset = A.asset_by_id(sess, aid)
    if not asset:
        return jsonify({'success': False, 'error': 'content not found'}), 404
    try:
        n = int(request.args.get('n') or 0)
    except (TypeError, ValueError):
        n = 0
    raw = A.image_bytes(_s3(), asset, n)
    if not raw:
        return jsonify({'success': False, 'error': 'no image for this one'}), 404
    from flask import Response
    resp = Response(raw, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/assets/<aid>', methods=['DELETE'])
@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/assets/<aid>/delete', methods=['POST'])
def dt_panel_asset_delete(key, sid, aid):
    """Take a piece of content out of the room. Its read stays on the
    record as a question the room answered; later questions no longer
    carry it."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    sess = _session(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    if sess.get('created_by') and str(sess.get('created_by')) != _uname(user) and not DT.is_admin(user):
        return jsonify({'success': False, 'error': 'Only the person who opened this room can remove content from it.'}), 403
    A = _assets_module()
    asset = A.asset_by_id(sess, aid)
    if not asset:
        return jsonify({'success': False, 'error': 'content not found'}), 404
    sess['assets'] = [a for a in (sess.get('assets') or []) if not (isinstance(a, dict) and str(a.get('id')) == str(aid))]
    s3 = _s3()
    for k in [asset.get('s3_key'), asset.get('thumb_key')] + list(asset.get('frame_keys') or []):
        if k and s3 is not None:
            try:
                s3.delete_object(Bucket=DT.S3_BUCKET, Key=k)
            except Exception:
                pass
    sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    DT.save_twin(s3, key, payload)
    return _nocache(jsonify({'success': True, 'assets': A.public_assets(sess), 'session': _panel_public(sess)}))


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
    if not (sess.get('questions') or []):
        # the group never ran: its open charge goes back (marked on the record before the 30-day copy is kept)
        _refund_group(user, sess, 'Focus group room deleted before its first question')
    removed = P.delete_room(_s3(), key, payload, sid, deleted_by=_uname(user))
    if removed is None:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    DT.save_twin(_s3(), key, payload)
    undo_until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + P.DELETED_ROOM_KEEP_DAYS * 86400))
    return _nocache(jsonify({'success': True, 'deleted': sid, 'sessions_left': len(payload.get('panel_sessions') or []),
                             'undo_until': undo_until, 'message': 'Room deleted. It can be restored within 30 days by an admin.'}))


@bp.route(f'{PREFIX}/<path:key>/panel/<sid>/duplicate', methods=['POST'])
def dt_panel_duplicate(key, sid):
    """Duplicate a room (Jenna, 2026-11-04: "duplicate a room with all of
    the input if you want to so if you just want to change one thing you
    can"): a new room, first on the twin, with the same twins, cohorts
    and seat count, the name with a copy suffix and no questions. A
    duplicate is a new group, charged as it opens at the company's plan
    rate (Jenna 2026-11-05). The source room is untouched. Anyone who
    can see the twin may duplicate a room on it; the new room is theirs."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    src = _session(payload, sid)
    if not src:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    from migration import digital_twin_panel as P
    from migration import digital_twin_room as ROOM
    from migration.digital_twin_synthesis import hydrate_identity_from_profile
    s3 = _s3()
    twins_by_key = _twins_for(payload, key, src)
    for t in twins_by_key.values():
        try:
            hydrate_identity_from_profile(t, s3)
        except Exception:
            traceback.print_exc()
    body = request.get_json(silent=True) or {}
    plan = _plan(user)
    tier = PR.normalize_tier(body.get('tier') if body.get('tier') is not None else src.get('group_tier'), plan)
    gate = _open_gate(user, tier, plan)
    if gate is not None:
        return gate
    sess = P.duplicate_room(payload, list(twins_by_key.values()), sid, created_by=_uname(user), session_id=uuid.uuid4().hex[:10])
    if sess is None:
        return jsonify({'success': False, 'error': 'session not found'}), 404
    PR.record_tier(sess, tier, plan)
    funds = _charge_group(user, payload, sess, kind='duplicate', tier=tier, plan=plan)     # a duplicate is a new group
    if funds is not None:
        payload['panel_sessions'] = [x for x in payload.get('panel_sessions') or [] if x is not sess]
        return funds
    try:
        try:
            ROOM.ensure_personas(sess, twins_by_key, session_seed=str(sess.get('id') or ''))
        except Exception:
            traceback.print_exc()
        PT = _portraits()
        if PT is not None:
            try:
                PT.attach_portraits(sess, s3)
            except Exception:
                pass
        DT.save_twin(s3, key, payload)
    except Exception:
        traceback.print_exc()
        _refund_group(user, sess, 'Duplicate room did not open')
        payload['panel_sessions'] = [x for x in payload.get('panel_sessions') or [] if x is not sess]
        return jsonify({'success': False, 'error': 'The room could not be opened. Nothing was charged; try again in a moment.'}), 500
    _team_notice('notify_room_opened', payload, sess, username=_uname(user), via='dashboard')   # a duplicate is a new room
    if PT is not None:
        try:
            PT.schedule_room_portraits(s3, key, sess, twins_by_key, payload)
        except Exception:
            pass
    return _nocache(jsonify({'success': True, 'session': sess, 'source_id': sid, 'extra_panelists': DT.extra_panelists((sess.get('group') or {}).get('in_room'))}))


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
    sess = next((s for s in payload.get('pilot_sessions') or [] if s.get('id') == sid), None)
    if sess is None:
        return None
    user = getattr(g, 'dt_user', None)
    if user is not None and not DT.tab_allowed(user, DT.tab_for_pilot_mode(sess.get('mode'))):
        raise _TabDenied()
    return sess


_PLAY_KEYS = frozenset({'digital_twin_pilot', 'digital_twin_trailer', 'digital_twin_title', 'digital_twin_key_art', 'digital_twin_concept', 'digital_twin_script', 'digital_twin_recut'})
_MODE_DESC = {'digital_twin_pilot': 'Pilot Testing session', 'digital_twin_trailer': 'Trailer test', 'digital_twin_title': 'Title test', 'digital_twin_key_art': 'Key art test',
              'digital_twin_concept': 'Concept test', 'digital_twin_script': 'Script read', 'digital_twin_recut': 'Recut re-test'}


def _pilot_kind(sess):
    """The group kind a test is charged as: a recut (cut B on a prior
    test's room) or a pilot (any screening mode)."""
    if (sess or {}).get('lineage') and ((sess or {}).get('lineage') or {}).get('parent'):
        return 'recut'
    return 'pilot'


def _charge_pilot(user, payload, sess, *, for_play=False, body=None):
    """A test is one GROUP (Jenna 2026-11-05), charged when it opens at
    the company's plan rate for its tier; first Play then charges
    nothing. A test opened before the card (or whose open could not
    charge) pays the group on first Play, the moment it used to pay.
    Questions on the clock count toward the tier: at twenty a 1-20 group
    is offered the move to 1-40, at forty the group is full. None when
    nothing is due (or it was charged, or the seat is free)."""
    from migration import digital_twin_pilot as PI
    if for_play:
        if sess.get('play_charged'):
            return None
        funds = _charge_group(user, payload, sess, kind=_pilot_kind(sess), tier=(body or {}).get('tier') if (body or {}).get('tier') is not None else sess.get('group_tier'))
        if funds is not None:
            return funds
        PI.mark_play_paid(sess, PR.question_cap(sess, _plan(user)))
        sess['play_price_key'] = str(PR.group_charge(sess).get('tool_key') or '')
        sess['play_quantity'] = 1
        return None
    if not sess.get('play_charged'):
        return None
    return _question_gate(user, payload, sess, body, kind=_pilot_kind(sess))


@bp.route(f'{PREFIX}/<path:key>/pilot/open', methods=['POST'])
def dt_pilot_open(key):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_room as ROOM
    twins = [payload]
    for ok in [str(k) for k in (body.get('twins') or []) if str(k) and str(k) != key][:3]:
        other, oerr = _twin_or_404(user, ok)
        if other:
            twins.append(other)
    from migration.digital_twin_synthesis import hydrate_identity_from_profile
    s3 = _s3()
    for t in twins:
        try:
            hydrate_identity_from_profile(t, s3)
        except Exception:
            traceback.print_exc()
    twins_by_key = {str((t.get('meta') or {}).get('key') or ''): t for t in twins}
    segments = body.get('segments') or None
    # cohorts (Jenna, 2026-11-02): the audience cut into named groups at setup, each with its composition in
    # words and the panelists wanted in it; every cohort stands for its own share of the twin, and the dial,
    # the scorecard and the answers split by cohort
    if not segments:
        segments = PI.cohort_segments(body.get('cohorts'), key) or None
    try:
        in_room = int(body.get('in_room') or 0)
    except (TypeError, ValueError):
        in_room = 0
    if segments and any(int(x.get('seats') or 0) for x in segments if isinstance(x, dict)):
        in_room = sum(int(x.get('seats') or 0) or 1 for x in segments if isinstance(x, dict))
    in_room = in_room or DT.panelists_for(payload)
    title = str(body.get('title') or '')[:120]
    mode = str(body.get('mode') or 'pilot').strip().lower()
    mode = mode if mode in PI.MODES else 'pilot'
    if not DT.tab_allowed(user, DT.tab_for_pilot_mode(mode)):
        return _denied_tab_reply()
    watch_url = str(body.get('watch_url') or body.get('url') or '').strip()
    # a test started from a saved setup that carries a cut (a duplicate of a test, 2026-11-04): the file comes
    # onto the new test here, so nothing is uploaded again; the person who saved the setup, or an admin
    setup = None
    setup_id = str(body.get('setup_id') or '').strip()
    if setup_id:
        setup = next((x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict) and str(x.get('id')) == setup_id), None)
        if setup is not None and not (isinstance(setup.get('media'), dict) and setup['media'].get('s3_key') and PI.can_edit_setup(setup, _uname(user), DT.is_admin(user))):
            setup = None
    reused = PI.find_reusable_session(payload, twins, title=title, created_by=_uname(user)) if mode == 'pilot' and not body.get('text') and not body.get('options') and not watch_url else None
    if reused is not None and str(reused.get('mode') or 'pilot') != mode:
        reused = None
    # a test is one GROUP, charged as it opens at the company's plan rate for the tier chosen here (1-20 or 1-40
    # questions on the clock; Jenna 2026-11-05). First Play then charges nothing. A test deleted before it ever
    # started hands the group back.
    plan = _plan(user)
    tier = PR.normalize_tier(body.get('tier'), plan)
    if reused is None or not PR.group_paid(reused):
        gate = _open_gate(user, tier, plan)
        if gate is not None:
            return gate
    if reused is not None:
        sess = reused
        if setup is not None and not (sess.get('media') or {}).get('s3_key'):
            PI.attach_setup_media(s3, sess, setup, _uname(user))
    else:
        media = {}
        if body.get('cut_title') or body.get('runtime_s') or body.get('runtime'):
            runtime = _runtime_seconds(body.get('runtime_s') or body.get('runtime'))
            media = {'title': str(body.get('cut_title') or title or '')[:160], 'runtime_s': runtime, 'source': 'slate'}
        sess = PI.open_session(twins, segments=segments, in_room=in_room, title=title, created_by=_uname(user),
                               session_id=uuid.uuid4().hex[:10], media=media, mode=mode, logline=str(body.get('logline') or ''), text=str(body.get('text') or ''),
                               options=body.get('options') if isinstance(body.get('options'), list) else None, rough_cut=bool(body.get('rough_cut')),
                               critics_room=bool(body.get('critics_room')))
        if setup is not None:
            PI.attach_setup_media(s3, sess, setup, _uname(user))
        if watch_url:
            runtime = _runtime_seconds(body.get('runtime_s') or body.get('runtime') or (media.get('runtime_s') if media else 0))
            PI.apply_watch_url(sess, watch_url, hint_title=str(body.get('cut_title') or title or ''), runtime_s=runtime, mode=mode)
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
                from migration import digital_twin_sizing as SZ
                SZ.size_session(payload, sess, twins=twins, s3=s3)     # the recruited cells stand for their people, sized the same way
            except Exception:
                traceback.print_exc()
        PR.record_tier(sess, tier, plan)
    funds = _charge_group(user, payload, sess, kind='pilot', tier=tier, plan=plan)      # the group, at open
    if funds is not None:
        if reused is not None:
            doc = funds[0].get_json() or {}
            doc['session'] = PI.public_session(sess)
            return jsonify(doc), funds[1]
        return funds                     # nothing is saved: the test never opened
    if reused is None:
        payload.setdefault('pilot_sessions', []).insert(0, sess)
    if 'genre' in body:
        # the Compare-to field on the setup form (Jenna 2026-11-04): the norm the test is read against; a pre-filled
        # guess the person left alone keeps following the frames' vote, a value they touched is theirs
        PI.set_norm(sess, str(body.get('genre') or ''), by_user=bool(body.get('genre_set_by_user')))
    try:
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
    except Exception:
        # the test did not open: the group's charge goes back (refund_credit by the test id)
        traceback.print_exc()
        _refund_group(user, sess, 'Pilot test did not open')
        if reused is None:
            payload['pilot_sessions'] = [x for x in payload.get('pilot_sessions') or [] if x is not sess]
        return jsonify({'success': False, 'error': 'The test could not be opened. Nothing was charged; try again in a moment.'}), 500
    if reused is None:
        # a test created (the form, a saved setup run, a duplicate run); a reused empty room is not a new test
        _team_notice('notify_pilot_created', payload, sess, username=_uname(user), via='dashboard')
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
    dirty = False
    try:   # a test from before the sizing stands for its cuts from here on (Jenna 2026-11-05)
        from migration import digital_twin_sizing as SZ
        dirty = SZ.ensure_sizing(payload, sess, s3=_s3()) or dirty
    except Exception:
        pass
    if sess.get('status') == 'preparing':
        _prepare_job_live(sess)             # a worker job that ended without a ready test flips the test back to draft
        try:
            from migration import digital_twin_live as LIVE
            LIVE.unstick(sess)              # a live finish that never came back flips to stopped
        except Exception:
            pass
        dirty = sess.get('status') != 'preparing'
    try:   # a question the room finished (or stalled on) lands on the test here, the same sweep the focus-group room read runs;
        # never 'researching' forever on the page (Jenna, 2026-11-02, the NCIS: NY room's "do you like the cast?")
        from migration import digital_twin_room_jobs as RJ
        if RJ.sweep_session(_s3(), key, sess):
            dirty = True
    except Exception:
        traceback.print_exc()
    if _reconcile(payload, sess):   # a finished question the scorecard has not read yet moves it (Jenna, 2026-11-04)
        dirty = True
    if _refresh_talent_attach(payload, sess):   # the names attached read the room's verdict; a room that moved re-reads them (Jenna, 2026-11-04)
        dirty = True
    if _auto_stop(key, payload, sess, user):   # the runtime ran out while nobody was here to press Stop (2026-11-03)
        return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), 'auto_stopped': True}))
    if dirty:
        DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess)}))


def _runtime_seconds(v) -> float:
    """Seconds from a number of seconds or a typed '42:35' (2026-11-03)."""
    if v in (None, ''):
        return 0.0
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        pass
    from migration import digital_twin_pilot as PI
    return PI.parse_runtime(v)


def _reconcile(payload, sess) -> bool:
    """The scorecard brought into line with every finished question that
    bears on it (the room's own answers outrank the inferred score,
    Jenna 2026-11-04). True when something moved; never raises."""
    try:
        from migration.digital_twin_reconcile import reconcile_session
        return bool(reconcile_session(sess, payload, s3=_s3()).get('changed'))
    except Exception:
        traceback.print_exc()
        return False


def _refresh_talent_attach(payload, sess) -> bool:
    """The names attached to a test are read against the room's verdict
    (follow, excellent, who named them as the reason to come back); when
    the card has moved since a name was attached, every attached name is
    recomputed against the session as it stands. True when it ran; never
    raises."""
    try:
        from migration import digital_twin_compare as CMP
        if not sess.get('talent_attach') or not CMP.talent_attach_stale(sess):
            return False
        CMP.recompute_talent_attach(sess, payload, s3=_s3())
        return True
    except Exception:
        traceback.print_exc()
        return False


def _auto_stop(key, payload, sess, user) -> bool:
    """Stop a playing test whose runtime has run out, exactly as the Stop
    route would (a live room finishes in the background), and save. True
    when it stopped here."""
    from migration import digital_twin_pilot as PI
    try:
        if not PI.auto_stop_due(sess):
            return False
        from migration import digital_twin_live as LIVE
        sess['auto_stopped_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        live = sess.get('live') if isinstance(sess.get('live'), dict) else {}
        if LIVE.is_live(sess) and live.get('frames') and not live.get('finished_at') and not live.get('finishing_at'):
            LIVE.finish_in_background(sess, _twins_for(payload, key, sess), key=key, payload=payload,
                                      save=lambda k, p: DT.save_twin(_s3(), k, p), usage_extras=_usage_extras(user), s3=_s3())
            DT.save_twin(_s3(), key, payload)
            return True
        PI.stop_session(sess)
        if sess.get('scorecard'):
            _reconcile(payload, sess)
            PI.record_norms(sess, s3=_s3())
        if sess.get('purge_media_after') and (sess.get('media') or {}).get('s3_key'):
            PI.purge_media(_s3(), sess, reason='done')
        DT.save_twin(_s3(), key, payload)
        if sess.get('scorecard'):
            _team_notice('notify_pilot_results_later', payload, sess, username=str(sess.get('created_by') or _uname(user)))   # the card is final here
        return True
    except Exception:
        traceback.print_exc()
        return False


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
    if body.get('mode') and not DT.tab_allowed(user, DT.tab_for_pilot_mode(body.get('mode'))):
        return _denied_tab_reply()
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


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/norm', methods=['POST', 'PATCH'])
def dt_pilot_norm(key, sid):
    """The user sets what the test is compared to (Jenna 2026-11-04):
    {genre: 'procedural' | '' for the all-genres column}. On a stopped
    test the norm lines and columns are re-read on the stored scorecard,
    the test is re-filed under the new genre in the norms, and the
    forecast re-reads them; nothing is re-run and nothing is charged.
    Same access as the other pilot routes (the twin)."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_norms as N
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    body = request.get_json(silent=True) or {}
    raw = str(body.get('genre') if body.get('genre') is not None else body.get('norm_genre') or '').strip().lower()
    if raw and raw != 'all' and N._genre(raw) == 'all':
        return jsonify({'success': False, 'error': 'Pick a norm from the list.', 'options': N.norm_options(str(sess.get('mode') or 'pilot'))}), 400
    PI.session_company(sess, payload)      # the test files its norms under the twin's company (company scope)
    changed = PI.set_norm(sess, raw, by_user=True, twin=payload, s3=_s3())
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), 'changed': changed,
                             'compared_to': ((sess.get('scorecard') or {}).get('norm_choice') or N.norm_choice(str((sess.get('scorecard') or {}).get('format') or PI.format_for(sess)), sess.get('genre') or 'all'))}))


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
    from migration import digital_twin_live as LIVE
    if LIVE.is_live(sess):
        # a watch link: the room is seated and waits for the mirror; the dial, the reactions and the scorecard exist only for what it watches
        LIVE.seat_live(sess, twins_by_key)
        DT.save_twin(_s3(), key, payload)
        return jsonify({'success': True, 'session': PI.public_session(sess), 'live': True})
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
    from migration import digital_twin_live as LIVE
    if LIVE.is_live(sess) and not ((sess.get('live') or {}).get('finished_at')):
        if not (sess.get('live') or {}).get('on'):
            LIVE.seat_live(sess, _twins_for(payload, key, sess))
        funds = _charge_pilot(user, payload, sess, for_play=True, body=request.get_json(silent=True) or {})
        if funds is not None:
            return funds
        try:   # the cast's faces, resolved before the room goes live (digital_twin_cast); the frames then read them at once
            from migration import digital_twin_cast as CAST
            CAST.people_for(sess, title=str((sess.get('media') or {}).get('title') or sess.get('title') or ''), usage_extras=_usage_extras(user), s3=_s3())
        except Exception:
            traceback.print_exc()
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        DT.save_twin(_s3(), key, payload)
        return jsonify({'success': True, 'session': PI.public_session(sess), 'charged': True, 'live': True})
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
    funds = _charge_pilot(user, payload, sess, for_play=True, body=request.get_json(silent=True) or {})
    if funds is not None:
        return funds
    PI.mark_playing(sess)
    DT.save_twin(_s3(), key, payload)
    return jsonify({'success': True, 'session': PI.public_session(sess), 'charged': True})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/upgrade', methods=['POST'])
def dt_pilot_upgrade(key, sid):
    """Move a 1-20 test to 1-40 for the difference at the company's plan
    rate (Jenna 2026-11-05); the twenty-first question on the clock runs
    once this has been accepted."""
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
    plan = _plan(user)
    if not PR.group_paid(sess):
        funds = _charge_group(user, payload, sess, kind=_pilot_kind(sess), plan=plan)
        if funds is not None:
            return funds
    funds = PR.accept_tier_upgrade(user, payload, sess, plan=plan, charge_fn=_group_charge_fn(user), username=_uname(user), by=_uname(user))
    if funds is not None:
        return funds
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), **PR.public_fields(sess, plan)}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/frame', methods=['POST'])
def dt_pilot_frame(key, sid):
    """One frame off the mirrored screen at clock t (a small JPEG as a
    data URL). The room reads it and reacts; the reply carries only what
    changed so the page paints without a reload. No charge: the frames
    ride on the session price."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_live as LIVE
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    if not LIVE.is_live(sess):
        return jsonify({'success': False, 'error': 'This test is not watching a mirrored screen.'}), 400
    if not sess.get('play_charged') and sess.get('status') != 'playing':
        return jsonify({'success': False, 'error': 'Press Play first.'}), 400
    if (sess.get('live') or {}).get('finished_at'):
        return jsonify({'success': False, 'error': 'This test has been scored.'}), 400
    body = request.get_json(silent=True) or {}
    image = str(body.get('image') or '')
    mt = 'image/png' if image.startswith('data:image/png') else 'image/jpeg'
    twins_by_key = _twins_for(payload, key, sess)
    twin = twins_by_key.get(key) or payload
    try:
        t = float(body.get('t') or 0)
    except (TypeError, ValueError):
        t = 0.0
    out = LIVE.frame(sess, twin, t, image, usage_extras=_usage_extras(user), media_type=mt)
    if not out.get('skipped'):
        DT.save_twin(_s3(), key, payload)
    return jsonify(dict({'success': True}, **out))


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
    q = _cased_question(q, sess, payload)     # a name the record knows keeps its own casing in the thread and the exports (Jenna 2026-11-05)
    if not sess.get('play_charged'):
        return jsonify({'success': False, 'error': 'Press Play first. The room answers once the cut is running.'}), 400
    funds = _charge_pilot(user, payload, sess, for_play=False, body=body)      # the tier cap and the upgrade
    if funds is not None:
        DT.save_twin(_s3(), key, payload)
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
    from migration import digital_twin_live as LIVE
    live = sess.get('live') if isinstance(sess.get('live'), dict) else {}
    if LIVE.is_live(sess) and live.get('frames') and not live.get('finished_at') and not live.get('finishing_at'):
        LIVE.finish_in_background(sess, _twins_for(payload, key, sess), key=key, payload=payload,
                                  save=lambda k, p: DT.save_twin(_s3(), k, p), usage_extras=_usage_extras(user), s3=_s3())
        DT.save_twin(_s3(), key, payload)
        return jsonify({'success': True, 'session': PI.public_session(sess), 'finishing': True})
    PI.stop_session(sess)
    if sess.get('scorecard'):
        _reconcile(payload, sess)           # the room's answers during the test land on the card before it files (2026-11-04)
        PI.record_norms(sess, s3=_s3(), twin=payload)     # every finished test grows its company's norms (idempotent per test)
    purged = False
    if sess.get('purge_media_after') and (sess.get('media') or {}).get('s3_key'):
        purged = PI.purge_media(_s3(), sess, reason='done')
    DT.save_twin(_s3(), key, payload)
    if sess.get('scorecard'):
        # the team gets the results once, at the stop, with the report attached (Jenna 2026-11-05); the card is final here
        _team_notice('notify_pilot_results_later', payload, sess, username=str(sess.get('created_by') or _uname(user)))
    try:
        # every minute's read and the five-minute averages, ready before anyone hovers the finished test (Jenna 2026-11-05)
        from migration import digital_twin_minutes as MIN
        MIN.precompute_in_background(sess, key=key, payload=payload, save=lambda k, p: DT.save_twin(_s3(), k, p), usage_extras=_usage_extras(user))
    except Exception:
        traceback.print_exc()
    return jsonify({'success': True, 'session': PI.public_session(sess), 'media_removed': purged})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/minute', methods=['GET'])
def dt_pilot_minute(key, sid):
    """The minute under the pointer on the dial (Jenna 2026-11-05): the
    average for the line the page is on, how many seats were talking,
    their lines, and a one or two sentence read with its lean. The read
    is one small call, cached on the test so a second hover is instant;
    a finished test had every Room minute written at stop."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_minutes as MIN
    from migration.digital_twin_room import Models
    sess = _pilot(payload, sid)
    if not sess:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    try:
        t = float(request.args.get('t') or 0)
    except (TypeError, ValueError):
        t = 0.0
    mode = str(request.args.get('mode') or 'room').strip().lower()
    if mode not in MIN.MODES:
        return jsonify({'success': False, 'error': 'mode is room, cohort or person'}), 400
    ident = str(request.args.get('id') or '')
    if mode != 'room' and not ident:
        return jsonify({'success': False, 'error': 'id names the cohort or the seat'}), 400
    out = MIN.digest(sess, max(0.0, t), mode, ident, models=Models(usage_extras=_usage_extras(user)), memo_key=f"{key}|{sid}")
    if out.pop('fresh', False):
        try:
            DT.save_twin(_s3(), key, payload)
        except Exception:
            traceback.print_exc()
    return _nocache(jsonify(dict({'success': True}, **out)))


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
    if not sess.get('play_charged') and not (sess.get('questions') or []):
        # the pilot never started: its open charge goes back (marked on the record before the 30-day copy is kept)
        _refund_group(user, sess, 'Pilot test deleted before it started')
    removed = PI.delete_session(_s3(), key, payload, sid, deleted_by=_uname(user))
    if removed is None:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    DT.save_twin(_s3(), key, payload)
    undo_until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + PI.DELETED_PILOT_KEEP_DAYS * 86400))
    return _nocache(jsonify({'success': True, 'deleted': sid, 'sessions_left': len(payload.get('pilot_sessions') or []),
                             'undo_until': undo_until, 'message': 'Test deleted. It can be restored within 30 days by an admin.'}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/duplicate', methods=['POST'])
def dt_pilot_duplicate(key, sid):
    """Duplicate a test (Jenna, 2026-11-04: "duplicate a room with all of
    the input if you want to so if you just want to change one thing you
    can"): a new saved setup, first on the twin, with every input of the
    test and none of its results. The cut comes along (copied to the
    setup's own place, so it is not uploaded again) for the person who
    opened the test or an admin. Nothing is seated or charged; the
    setup is priced like one typed from scratch when it starts. The
    test itself is untouched. The page opens the setup in the form."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    if not _pilot(payload, sid):
        return jsonify({'success': False, 'error': 'test not found'}), 404
    rec = PI.duplicate_session_as_setup(_s3(), payload, sid, _uname(user), DT.is_admin(user))
    if rec is None:
        return jsonify({'success': False, 'error': 'test not found'}), 404
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'setup': PI.public_setup(rec), 'setups': PI.public_setups(payload), 'source_id': sid}))


@bp.route(f'{PREFIX}/<path:key>/pilot/setups', methods=['POST'])
def dt_pilot_setup_save(key):
    """Save the Pilot Testing form as a named setup on the twin (Jenna,
    2026-11-03: come back later and edit it without typing it all again).
    Upserts when the body carries the id of a setup on this twin. Nothing
    is seated or charged."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    body = request.get_json(silent=True) or {}
    sid = str(body.get('id') or '').strip()
    if sid:
        cur = next((x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict) and str(x.get('id')) == sid), None)
        if cur is not None and not PI.can_edit_setup(cur, _uname(user), DT.is_admin(user)):
            return jsonify({'success': False, 'error': 'Only the person who saved this setup, or an admin, can change it.'}), 403
    rec = PI.save_setup(payload, body, _uname(user), s3=_s3())
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'setup': PI.public_setup(rec), 'setups': PI.public_setups(payload)}))


@bp.route(f'{PREFIX}/<path:key>/pilot/setups/<sid>', methods=['DELETE'])
@bp.route(f'{PREFIX}/<path:key>/pilot/setups/<sid>/delete', methods=['POST'])
def dt_pilot_setup_delete(key, sid):
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    cur = next((x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict) and str(x.get('id')) == str(sid)), None)
    if cur is None:
        return jsonify({'success': False, 'error': 'setup not found'}), 404
    if not PI.can_edit_setup(cur, _uname(user), DT.is_admin(user)):
        return jsonify({'success': False, 'error': 'Only the person who saved this setup, or an admin, can delete it.'}), 403
    gone = PI.delete_setup(payload, sid, s3=_s3())
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'deleted': PI.public_setup(gone), 'setups': PI.public_setups(payload)}))


@bp.route(f'{PREFIX}/<path:key>/pilot/setups/<sid>/duplicate', methods=['POST'])
def dt_pilot_setup_duplicate(key, sid):
    """Duplicate a saved setup (Jenna, 2026-11-04): a new setup, first on
    the twin, with every input of this one and the name '<name> copy'
    ('<name> copy 2' when that exists). The cut it carries comes along
    for the person who saved it or an admin. The page opens the new
    setup in the form so one thing can change."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    rec = PI.duplicate_setup(payload, sid, _uname(user), DT.is_admin(user), s3=_s3())
    if rec is None:
        return jsonify({'success': False, 'error': 'setup not found'}), 404
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'setup': PI.public_setup(rec), 'setups': PI.public_setups(payload), 'source_id': sid}))


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/restore', methods=['POST'])
def dt_pilot_restore(key, sid):
    """A deleted test back from its 30-day undo copy (Jenna, 2026-11-03:
    a way to delete tests, and the undo the delete promises). The person
    who deleted it, or an admin."""
    user, err = _gate()
    if err:
        return err
    payload, err = _twin_or_404(user, key)
    if err:
        return err
    from migration import digital_twin_pilot as PI
    import json as _json
    try:
        rec = _json.loads(_s3().get_object(Bucket='dashboard-inputs', Key=PI.deleted_pilot_key(key, sid))['Body'].read().decode('utf-8'))
    except Exception:
        rec = None
    if not isinstance(rec, dict):
        return jsonify({'success': False, 'error': 'Nothing to restore: that test is not in the 30-day undo window.'}), 404
    if not (DT.is_admin(user) or str(rec.get('deleted_by') or '') == _uname(user)):
        return jsonify({'success': False, 'error': 'Only the person who deleted this test, or an admin, can restore it.'}), 403
    sess = PI.restore_session(_s3(), key, payload, sid, restored_by=_uname(user))
    if sess is None:
        return jsonify({'success': False, 'error': 'That test could not be restored.'}), 409
    DT.save_twin(_s3(), key, payload)
    return _nocache(jsonify({'success': True, 'session': PI.public_session(sess), 'message': 'Test restored.'}))


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
    watch_url = str(body.get('watch_url') or body.get('url') or '').strip()
    if watch_url:
        try:
            runtime = float(body.get('runtime_s') or (sess.get('media') or {}).get('runtime_s') or 0)
        except (TypeError, ValueError):
            runtime = 0
        rec = PI.apply_watch_url(sess, watch_url, hint_title=str(body.get('cut_title') or sess.get('title') or ''),
                                 runtime_s=runtime, mode=str(sess.get('mode') or ''))
        if rec:
            changed['watch_url'] = rec.get('url') or True
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
    personas, the lineage pointing back. A recut is a new group, charged
    as it opens at the company's plan rate (Jenna 2026-11-05)."""
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
    plan = _plan(user)
    tier = PR.normalize_tier(body.get('tier') if body.get('tier') is not None else parent.get('group_tier'), plan)
    gate = _open_gate(user, tier, plan)
    if gate is not None:
        return gate
    twins = list(_twins_for(payload, key, parent).values())
    sess = CMP.open_recut(parent, twins, created_by=_uname(user), title=str(body.get('title') or '')[:120], session_id=uuid.uuid4().hex[:10])
    sess['twin_keys'] = list(parent.get('twin_keys') or [key])
    PR.record_tier(sess, tier, plan)
    funds = _charge_group(user, payload, sess, kind='recut', tier=tier, plan=plan)       # cut B is a new group (Jenna 2026-11-05)
    if funds is not None:
        return funds
    payload.setdefault('pilot_sessions', []).insert(0, sess)
    try:
        PT = _portraits()
        if PT is not None:
            try:
                PT.attach_portraits(sess, _s3())
            except Exception:
                pass
        DT.save_twin(_s3(), key, payload)
    except Exception:
        traceback.print_exc()
        _refund_group(user, sess, 'Recut test did not open')
        payload['pilot_sessions'] = [x for x in payload.get('pilot_sessions') or [] if x is not sess]
        return jsonify({'success': False, 'error': 'The test could not be opened. Nothing was charged; try again in a moment.'}), 500
    _team_notice('notify_pilot_created', payload, sess, username=_uname(user), via='dashboard')   # cut B is a new test on the same room
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
        catalog = DT.visible_runs(DT.list_runs(_s3()), user)    # comps come from the company's own library (company scope, 2026-11-05)
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
        critics_twin = CMP.find_critics_twin(DT.visible_runs(DT.list_runs(_s3()), user))    # only a critics twin the company can open
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
    company = _norms_company(user)
    for fmt in N.FORMATS:
        try:
            out[fmt] = PI.norms_for(fmt, genre, s3=_s3(), company=company)
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
    return _nocache(jsonify({'success': True, 'norms': PI.norms_for(str(request.args.get('format') or 'pilot'), str(request.args.get('genre') or 'all'), s3=_s3(),
                                                                     company=_norms_company(user))}))


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
    return Response(PI.session_csv(sess, payload), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{name}_pilot_{sid}.csv"'})


def _pilot_portrait_fn():
    """``hash -> JPEG bytes`` for the room page of a test's report (the
    thumb); None when portraits are off. A missing face never fails an
    export."""
    PT = _portraits()
    return (lambda h: PT.read_portrait(_s3(), h, PT.THUMB_SIZE)) if PT is not None else None


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/export.pdf', methods=['GET'])
def dt_pilot_pdf(key, sid):
    """The same report as report.pdf (one page list for every format,
    2026-11-04); the route stays for older links."""
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
    return Response(X.build_pilot_pdf(payload, sess, portrait_fn=_pilot_portrait_fn()), mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{X.pilot_pdf_filename(payload, sess)}"'})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/export.pptx', methods=['GET'])
def dt_pilot_pptx(key, sid):
    """The same report as report.pptx; the route stays for older links."""
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
    return Response(X.build_pilot_deck(payload, sess, portrait_fn=_pilot_portrait_fn()),
                    mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    headers={'Content-Disposition': f'attachment; filename="{X.pilot_deck_filename(payload, sess)}"'})


@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/report.pptx', methods=['GET'])
@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/report.pdf', methods=['GET'])
@bp.route(f'{PREFIX}/<path:key>/pilot/<sid>/dial.png', methods=['GET'])
def dt_pilot_report(key, sid):
    """The studio report (deck or PDF, the room page with its faces
    included) and the dial chart image."""
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
    portrait_fn = _pilot_portrait_fn()
    if path.endswith('.pdf'):
        return Response(R.build_report_pdf(payload, sess, portrait_fn=portrait_fn), mimetype='application/pdf', headers={'Content-Disposition': f'attachment; filename="{R.report_filename(payload, sess, ".pdf")}"'})
    return Response(R.build_report_deck(payload, sess, portrait_fn=portrait_fn), mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
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
    try:   # a test's sizing travels as its figures and its line, never its basis (what was looked up for the title)
        from migration.digital_twin_sizing import public_sizing
        out['pilot_sessions'] = [(dict(s, group=dict(s['group'], sizing=public_sizing(s['group'].get('sizing')))) if isinstance(s, dict) and isinstance(s.get('group'), dict) and isinstance(s['group'].get('sizing'), dict) else s)
                                 for s in (payload.get('pilot_sessions') or [])]
    except Exception:
        pass
    if payload.get('pilot_setups'):
        from migration import digital_twin_pilot as PI
        out['pilot_setups'] = PI.public_setups(payload)   # a saved setup's carried cut travels as has_file, never as its storage key
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
