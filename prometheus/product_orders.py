"""Product orders placed in the chat: the Brand Partnership valuation, the Journey IQ study, the Flywheel and the Attribution IQ tracking pull, each with its intent, its parse, its confirm and its background job.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import json
import traceback
from prometheus import smarter as _smart
from prometheus import faults as _faults
from prometheus import intents as _intents
from prometheus import bpiq_wizard as _bpiq_wiz
import time
from datetime import datetime, timedelta, date, timezone
from prometheus import intake_draft as _intake_draft

# <<moved>>
# <<block: 1>>
# ---------------------------------------------------------------------------
# Brand Partnership Valuation via Prometheus (Jenna 2026-09-16): the
# "Pull Brand Partnership Valuation" chip collects the partnership
# inputs in chat, confirms, charges the standard Brand Partnership IQ
# pull, and runs the research + reasoning build on a background thread.
# The finished read lands in brand-partnership-iq/ and renders in the
# dashboard's Brand Partnership tab like every other valuation.
# ---------------------------------------------------------------------------
_PM_BPIQ_JOB_PREFIX = 'system/pm_bpiq_jobs/'


# <<block: 2>>
_PM_BPIQ_CHIP = 'Pull Brand Partnership Valuation'


# <<block: 3>>
_PM_BPIQ_CREDITS = 15


# <<block: 4>>
_PM_BPIQ_ASK_COPY = _intake_draft.BPIQ_ASK_COPY  # text lives in prometheus/intake_draft.py


# <<block: 5>>
def _pm_read_intent(body, intent, min_conf=0.7):
    return _intents.read_intent(body, intent, min_conf)


# <<block: 6>>
def _pm_bpiq_intent(text):
    return _intents.bpiq_intent(text, _C._PM_BPIQ_CHIP)


# <<block: 7>>
def _pm_bpiq_parse(text, usage_extras=None):
    """One small model call: free text -> the valuation inputs."""
    from migration.bpiq_synthesis import PARSE_SYSTEM_PROMPT
    parsed = _C._pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=1200, temperature=0.0,
                             surface='bpiq_parse',
                             usage_extras=usage_extras)
    return parsed if isinstance(parsed, dict) else {}


# <<block: 8>>
def _pm_bpiq_flight_research(brand, campaign, qualifier=''):
    """One web-search call dating a brand's named initiative (prometheus.bpiq_window)."""
    from prometheus import bpiq_window as _bw
    return _bw.flight_research(brand, campaign, qualifier, claude_data=_C._pm_claude_data)


# <<block: 9>>
def _pm_bpiq_inputs_complete(parsed):
    return bool(parsed.get('brand_partner') and parsed.get('qualifier')
                and parsed.get('event_start')
                and parsed.get('event_end'))


# <<block: 10>>
def _pm_bpiq_confirm_reply(parsed):
    ev = f"{parsed.get('event_start')} to {parsed.get('event_end')}"
    pre = (f"{parsed['pre_start']} to {parsed['pre_end']}"
           if parsed.get('pre_start') and parsed.get('pre_end')
           else 'the year before the campaign')
    post = (f"{parsed['post_start']} to {parsed['post_end']}"
            if parsed.get('post_start') and parsed.get('post_end')
            else 'campaign end through today')
    aud = parsed.get('audience') or 'the partner audience'
    from prometheus import bpiq_window as _bw
    return (
        f"Here's the valuation I'll run:\n"
        f"- {parsed['qualifier']} x {parsed['brand_partner']}\n"
        f"{_bw.context_lines(parsed)}"
        f"- Campaign window: {ev}\n"
        f"- Baseline window: {pre}\n"
        f"- Post window: {post}\n"
        f"- Audience: {aud}\n\n"
        f"It prices at $500 without a control group, or $1,000 "
        f"with one, and lands in the Brand Partnership tab when "
        f"finished.")


# <<block: 11>>
def _pm_run_bpiq_job(job_id, username, inputs, extras):
    """Background build: research + reason the partnership read, write
    the payload, register the metadata sidecar, and open access for
    the requesting user. Mirrors the deck job's thread pattern.

    One run per ask (2026-10-28): the run registry is checked before
    anything is built; a key another job owns is followed, never
    rebuilt. The campaign research and the primitives persist per key
    so a repeat reads them back."""
    reg = None
    try:
        from migration.bpiq_synthesis import synthesize, s3_key_for, S3Underpinnings
        from prometheus import bpiq_job as _bj
        # one run per ask (2026-10-28): known casing, the key claimed, a
        # key another job owns followed to its file instead of rebuilt
        inputs, reg, _settled = _bj.prepare(job_id, username, inputs, extras, _H, _C._pm_bpiq_status_write)
        if _settled:
            return
        _C._pm_bpiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': f"{inputs['qualifier']} x "
                       f"{inputs['brand_partner']}",
            'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        tools = None
        try:
            import prometheus_analysis as _pma_b
            tools = [_pma_b.WEB_SEARCH_TOOL]
        except Exception as _fx:
            _faults.note('chat', _fx)

        def _cj(system, user_prompt, **kw):
            kw.setdefault('usage_extras', extras)
            return _C._pm_claude_data(system, user_prompt, **kw)

        payload = synthesize(inputs, _cj, tools=tools,
                             created_by=username or 'prometheus',
                             underpinnings=S3Underpinnings(_H.s3_client, _H.S3_BUCKET),
                             job_id=job_id)
        category = payload.pop('_bpiq_category', None)
        out_key = s3_key_for(inputs)
        _H.s3_client.put_object(
            Bucket=_H.S3_BUCKET, Key=out_key,
            Body=json.dumps(payload, indent=2).encode('utf-8'),
            ContentType='application/json')
        # Corpus catalog (2026-10-05): the read's audience and value
        # figures join the shared subject record as they land.
        try:
            from migration import corpus_catalog as _cc_b
            _cc_b.index_bpiq(out_key, payload, user=username or '')
        except Exception as _cc_err:
            print(f"[bpiq-job {job_id}] corpus catalog hook failed: {_cc_err}")
        bare_key = out_key.replace('brand-partnership-iq/', '')
        # Metadata sidecar: title + category so the tab and the admin
        # CMS render it immediately, plus the thumbnail every hand-built
        # read carries (2026-10-05, Willow Smith x Free People shipped
        # without a photo).
        _img_url = None
        try:
            from prometheus.bpiq_image import resolve_bpiq_image
            _img_url, _img_src = resolve_bpiq_image(_H, inputs)
            print(f"[bpiq-job {job_id}] image {_img_src}: {_img_url}")
        except Exception as img_err:
            print(f"[bpiq-job {job_id}] image lookup failed "
                  f"(non-fatal): {img_err}")
        try:
            meta = _H.load_bpiq_metadata()
            _prev = meta.get(bare_key) or {}
            meta[bare_key] = {
                **_prev,
                'display_name': payload.get('project_name') or bare_key,
                'category': (category or 'ENTERTAINMENT'),
            }
            if _img_url and not _prev.get('image_url'):
                meta[bare_key]['image_url'] = _img_url
            _H.s3_client.put_object(
                Bucket=_H.S3_BUCKET, Key=_H.BPIQ_METADATA_KEY,
                Body=json.dumps(meta, indent=2).encode('utf-8'),
                ContentType='application/json')
        except Exception as meta_err:
            print(f"[bpiq-job {job_id}] metadata sidecar failed "
                  f"(non-fatal): {meta_err}")
        # Company wallet owns the pull so teammates see it. Crosswalk
        # sees every pull; other companies never inherit the library.
        try:
            _H._stamp_bpiq_ownership(out_key, username)
        except Exception as acc_err:
            print(f"[bpiq-job {job_id}] access grant skipped: {acc_err}")
        _C._pm_bpiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': payload.get('project_name'),
            's3_key': bare_key,
            'total_brand_value': (payload.get('valuation') or {})
            .get('total_brand_value'),
            'finished_at': time.time(), **_bj.hand_over(job_id, username, inputs, bare_key, payload, category, _H)})   # the valuation deck, one builder (2026-11-11)
        print(f"[bpiq-job {job_id}] done -> {out_key}")
        _bj.settle(reg, inputs, job_id, username, bare_key, payload)   # registry closes; the thread gets its note
        try:   # delivered contract (2026-10-13)
            from prometheus.delivered import on_done as _on_done
            _val = payload.get('valuation') or {}
            _tbv = _val.get('total_brand_value')
            _em_lead = ''
            try:
                _em_subj, _em_body = _bpiq_wiz.email_copy({
                    'talent': inputs.get('qualifier'),
                    'campaign': (inputs.get('campaign')
                                 or inputs.get('brand_partner')),
                    'qualifier': inputs.get('qualifier'),
                    'brand_partner': inputs.get('brand_partner'),
                })
                _em_lead = (_em_body.split('\n\n') or [''])[0]
            except Exception:
                _em_lead = ''
            _on_done('bpiq', username=username, subject=str(payload.get('project_name') or ''), s3_key=bare_key, payload=payload,
                     host=_H, s3_client=_H.s3_client, job_id=job_id, files=_bj.deck_files(job_id),
                     ready_message=_em_lead, intro=_em_lead,
                     figures=([{'name': 'Total brand value', 'value': _tbv, 'unit': 'usd', 'definition': 'Brand Partnership IQ valuation'}] if _tbv else []),
                     facts=([('Total brand value', f"${float(_tbv):,.2f}")] if _tbv else []))
        except Exception as _dl_err:
            print(f"[bpiq-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        try:
            _C._pm_bpiq_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error',
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        try:
            from prometheus import bpiq_job as _bj_f
            _bj_f.fail(reg, inputs, job_id, e)
        except Exception:
            pass
        _C._pm_engine_failure_alert(
            'brief-chat/bpiq-job', e, job_id, username, inputs, 'the valuation error line in the widget',
            f"Brand Partnership: {(inputs or {}).get('brand') or (inputs or {}).get('subject') or ''} x {(inputs or {}).get('partner') or (inputs or {}).get('brand_partner') or ''}")


# <<block: 12>>
# ---------------------------------------------------------------------------
# Digital Journey via Prometheus (Jenna 2026-09-16): the "Pull a
# Digital Journey" chip walks the user through the playbook inputs
# (subject, platform, conversion event, window, TAM), charges the
# $500 Digital Journey pull, and builds the research-anchored journey
# on a background thread. The finished run lands in the Journey IQ
# store and renders in the Digital Journey tab exactly like the Luxury
# Fragrance on TikTok Shop read.
# ---------------------------------------------------------------------------
_PM_JIQ_JOB_PREFIX = 'system/pm_jiq_jobs/'


# <<block: 13>>
_PM_JIQ_CHIP = 'Pull a Digital Journey'


# <<block: 14>>
_PM_JIQ_CREDITS = 15


# <<block: 15>>
_PM_JIQ_ASK_COPY = _intake_draft.JIQ_ASK_COPY  # text lives in prometheus/intake_draft.py


# <<block: 16>>
def _pm_jiq_intent(text):
    return _intents.jiq_intent(text, _C._PM_JIQ_CHIP)


# <<block: 17>>
def _pm_jiq_parse(text, usage_extras=None):
    from migration.journey_synthesis import PARSE_SYSTEM_PROMPT
    parsed = _C._pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=1200, temperature=0.0,
                             surface='jiq_parse',
                             usage_extras=usage_extras)
    return parsed if isinstance(parsed, dict) else {}


# <<block: 18>>
def _pm_jiq_inputs_complete(parsed):
    from prometheus.intake_reader import journey_inputs_complete
    return journey_inputs_complete(parsed)   # two directions (Jenna 2026-10-19): after-watch needs the title only; unclear is asked


# <<block: 19>>
def _pm_jiq_is_ticketing(inputs):
    try:
        from migration.journey_synthesis import is_ticketing_journey
        return bool(is_ticketing_journey(inputs or {}))
    except Exception:
        return False


# <<block: 20>>
def _pm_jiq_ready_message(payload, inputs):
    """The ready notice is written here, not in the widget (2026-10-05
    Jenna: Prometheus's words live with the API). Names the count by
    what it is: ticketing-site visitors, viewers, or buyers, always
    people in the US, and carries the no-purchase line on a film."""
    subj = str((payload.get('meta') or {}).get('project_name')
               or inputs.get('subject') or 'the journey')
    n = (payload.get('kpis') or {}).get('total_users')
    try:
        n_txt = f"{int(n):,}"
    except Exception:
        n_txt = ''
    ticketing = _C._pm_jiq_is_ticketing(inputs) or bool(
        (payload.get('meta') or {}).get('no_purchase_claim'))
    kind = str(inputs.get('journey_kind') or '').lower()
    msg = (f"Your Digital Journey is ready: {subj} is live in the "
           f"Digital Journey tab now.")
    if n_txt and ticketing:
        msg += (f" Ticketing-site visitors in window: {n_txt} people in "
                "the US. No claim is made on whether any of them bought "
                "a ticket; Crosswalk does not predict box office.")
    elif n_txt and kind in ('watch', 'media_week'):
        msg += f" Viewers in window: {n_txt} people in the US."
    elif n_txt:
        msg += f" Buyers in window: {n_txt} people in the US."
    return msg


# <<block: 21>>
def _pm_jiq_confirm_reply(parsed):
    win = (f"{parsed['start_date']} to {parsed['end_date']}"
           if parsed.get('start_date') and parsed.get('end_date')
           else 'trailing 12 months')
    if str(parsed.get('journey_kind') or '') == 'media_week':   # what watchers do after and in addition (Jenna 2026-10-19)
        from prometheus.intake_reader import media_week_confirm_reply
        return media_week_confirm_reply(parsed, win, _C._pm_tool_price_label('journey_iq', '$500'),
                                        price_sentence=_C._pm_price_sentence('journey_iq', '$500'))
    tam = parsed.get('tam_label') or 'US gen pop (329.9M)'
    start_line = (f"- Starting point: {parsed['start_behavior']} "
                  f"(out of {tam})"
                  if parsed.get('start_behavior')
                  else f"- Starting universe: {tam}")
    end_step = parsed['conversion_event']
    if _C._pm_jiq_is_ticketing(parsed):
        # Movie tickets (2026-10-05 Jenna): the furthest point the read
        # sees is the ticketing site. Say so at the confirm, before the
        # run, so the end step is never a purchase in the reader's mind
        # (Alexia's first ask was "purchased a digital movie ticket").
        parsed['journey_kind'] = 'ticketing'
        parsed['no_purchase_claim'] = True
        end_step = ("Went to the ticketing site for a ticket. This is "
                    "the furthest point we see; it makes no claim on "
                    "what happened after the ticketing site, and "
                    "Crosswalk does not predict box office")
        parsed['conversion_event'] = (
            "Went to a ticketing site or app for a ticket to "
            f"{parsed['subject']} (no purchase claim)")
        shape = ("It's a full discovery-to-ticketing-site path - where "
                 "they see the campaign, act on it, look the film up, "
                 "look up showtimes, and reach the ticketing site")
    elif str(parsed.get('journey_kind') or '') == 'before_after':
        clip = parsed.get('clip_url') or 'this clip'
        shape = ("It's a 20-minute before-and-after around the clip "
                 f"({clip}) - last surface before they opened it, "
                 "first surface after, then research and action in "
                 "the rest of the window")
    elif str(parsed.get('journey_kind') or '') == 'discovery_existing':
        shape = ("It's new-to-the-platform vs already on it - where "
                 "each group arrived, what they opened first, and "
                 "who stayed")
    elif str(parsed.get('journey_kind') or '') == 'music':
        shape = ("It's a music-first path into the title - the song, "
                 "the sound page, the title page, and the watch")
    elif str(parsed.get('journey_kind') or '') == 'watch':
        # the Babylon 5 shape, every watch journey (Jenna 2026-10-14); the saw-then-watched read when the brief carries it (Carolyn Bisson 2026-10-22)
        from prometheus.intake_reader import watch_definition_line
        shape = watch_definition_line(parsed)
    else:
        # the same depth as a watch journey, plus why the cart leavers
        # left (Jenna 2026-10-16, Ghost Energy)
        shape = ("It's a full discovery-to-purchase path - where they learn the name, research, compare, hunt a code, "
                 "cart and leave, get retargeted, and pay - with the first content they saw and where it sat, how many "
                 "posts before they looked it up, where they looked before the cart, the time to the order, what they "
                 "bought first, the last touch and the assists, and for everyone who left the cart, what they did next "
                 "and why: coupon hunting, price checks, safety searches, reviews, competitors")
    clip_line = (f"- Clip: {parsed['clip_url']}\n"
                 if parsed.get('clip_url') else '')
    return (
        f"Here's the Digital Journey I'll build:\n"
        f"- {parsed['subject']} on {parsed['platform']}\n"
        f"- End step: {end_step}\n"
        f"{clip_line}"
        f"- Window: {win}\n"
        f"{start_line}\n\n"
        f"{shape}. Every journey carries Pathways, One viewer and "
        f"Clickstream sections: the routes people took, one person's "
        f"walk step by step, and the public URLs on each step. It lands "
        f"in the Digital Journey tab when finished. "
        f"{_C._pm_price_sentence('journey_iq', '$500')} Run it?")


# <<block: 22>>
def _pm_run_jiq_job(job_id, username, inputs, extras):
    try:
        from migration.journey_synthesis import synthesize, persist
        subj = (f"{inputs['subject']} viewers, a week in the life" if str(inputs.get('journey_kind') or '') == 'media_week'
                else f"{inputs['subject']} on {inputs.get('platform') or 'the platform'}")
        _C._pm_jiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': subj, 'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        tools = None
        try:
            import prometheus_analysis as _pma_j
            tools = [_pma_j.WEB_SEARCH_TOOL]
        except Exception as _fx:
            _faults.note('chat', _fx)

        def _cj(system, user_prompt, **kw):
            kw.setdefault('usage_extras', extras)
            return _C._pm_claude_data(system, user_prompt, **kw)

        payload = synthesize(inputs, _cj, tools=tools,
                             created_by=username or 'prometheus')
        if str((_C._pm_s3_json(f"{_C._PM_JIQ_JOB_PREFIX}{job_id}.json", {}) or {}).get('status') or '') == 'cancelled':
            print(f"[jiq-job {job_id}] cancelled while building; nothing persisted"); return   # Jenna 2026-10-16: stop it
        # Hero image (2026-10-05): the tab shows the title's poster or
        # the brand's image instead of initials. Best-effort, never
        # blocks the persist.
        try:
            from prometheus.jiq_image import resolve_jiq_image, attach_hero
            _img_url, _img_src = resolve_jiq_image(_H, inputs)
            if _img_url:
                attach_hero(payload, _img_url)
            print(f"[jiq-job {job_id}] hero image: {_img_src}")
        except Exception as _img_err:
            print(f"[jiq-job {job_id}] hero image skipped: {_img_err}")
        out_key = persist(_H.s3_client, payload, username or 'prometheus',
                          job_id)
        # Access: the requester must see their own run. Default-open
        # ('*' / unset) users already do; explicit-list users get the
        # key appended; users without the product get a scoped grant.
        try:
            def _grant(data):
                u = (data.get('users') or {}).get(username)
                if not u:
                    return None
                cur = u.get('allowed_journey_iq_runs')
                changed = False
                if isinstance(cur, list) and '*' not in cur \
                        and out_key not in cur:
                    u['allowed_journey_iq_runs'] = cur + [out_key]
                    changed = True
                if not u.get('has_journey_iq_access'):
                    u['has_journey_iq_access'] = True
                    changed = True
                return data if changed else None
            _H._users_cas_mutate(_grant)
        except Exception as acc_err:
            print(f"[jiq-job {job_id}] access grant skipped: {acc_err}")
        _C._pm_jiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': payload['meta']['project_name'],
            's3_key': out_key,
            'conversions': (payload.get('kpis') or {}).get('total_users'),
            'count_noun': ('ticketing-site visitors'
                           if (payload.get('meta') or {}).get('no_purchase_claim')
                           else None),
            'ready_message': _C._pm_jiq_ready_message(payload, inputs),
            'finished_at': time.time()})
        print(f"[jiq-job {job_id}] done -> {out_key}")
        # Done means the user has it (Jenna 2026-10-13): ledger, email with the
        # breakdown attached, delivery record, shelf. One contract, every product.
        try:
            from prometheus.delivered import on_done as _on_done
            from migration.journey_iq import shelf_for as _shelf_for, SHELF_LABELS as _SHELF_LABELS
            _on_done('journey', username=username, subject=str(payload['meta'].get('project_name') or subj), s3_key=out_key,
                     payload=payload, host=_H, s3_client=_H.s3_client, job_id=job_id,
                     shelf=_SHELF_LABELS.get(_shelf_for(payload.get('meta') or {}), ''),
                     ready_message=_C._pm_jiq_ready_message(payload, inputs))
        except Exception as _dl_err:
            print(f"[jiq-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        try:
            _C._pm_jiq_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error',
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        _C._pm_engine_failure_alert(
            'brief-chat/jiq-job', e, job_id, username, inputs, 'the journey error line in the widget',
            f"Digital Journey: {(inputs or {}).get('subject') or ''} on {(inputs or {}).get('platform') or ''}")


# <<block: 23>>
# --- Build a Flywheel through Prometheus (2026-09-17, Jenna) ----------------
# Method: the revised acquired/reactivated playbook. The nest starts
# at the captured users (the people who did the thing the ask wants
# to capture) - never at US gen pop.
def _pm_price_sentence(tool_key, fallback, username=None):
    """The price sentence a confirm card prints: 'It prices at $500.' for a
    paying seat; 'Included with your plan.' with no price for an unlimited
    or staff seat (the 2026-11-08 rule, priced_read.included_with_plan, the
    one check every priced surface asks; the journey card printed $500 to
    Jenna's unlimited seat on 2026-11-10). Never raises."""
    try:
        # the reader named, else the session's, else the one the ask door stamped (a Prometheus app user has no
        # session); never a field of the request body (prometheus.ask_outcome.seat_username, 2026-11-17)
        uname = __import__('prometheus.ask_outcome', fromlist=['seat_username']).seat_username(username)
        if uname:
            data = _H.load_users()
            user = (data.get('users') or {}).get(uname) or {}
            from prometheus import priced_read as _pr
            if user and _pr.included_with_plan(user, uname, _H):
                return _pr.INCLUDED_LINE
    except Exception as _fx:
        _faults.note('chat', _fx)
    return f"It prices at {_C._pm_tool_price_label(tool_key, fallback, username)}."


# <<block: 24>>
def _pm_included_seat(username=None):
    """True when priced reads are included with this seat's plan (unlimited or staff)."""
    try:
        return _C._pm_price_sentence('journey_iq', '$500', username) == __import__('prometheus.priced_read', fromlist=['INCLUDED_LINE']).INCLUDED_LINE
    except Exception:
        return False


# <<block: 25>>
def _pm_tool_price_label(tool_key, fallback, username=None):
    """Live per-pull price from the billing panel (system/pricing.json
    via wallet.tool_price_usd), so chat copy never drifts from what
    admins set. Pass username so a company sticker (Kartel $925 BPIQ)
    prints the number that will be charged. Falls back to the last
    known label on any failure."""
    try:
        import wallet as _w
        subject = None
        # the reader named, else the session's, else the one the ask door stamped (a Prometheus app user has no
        # session); never a field of the request body (prometheus.ask_outcome.seat_username, 2026-11-17)
        uname = __import__('prometheus.ask_outcome', fromlist=['seat_username']).seat_username(username)
        if uname:
            try:
                data = _H.load_users()
                user = (data.get('users') or {}).get(uname) or {}
                if user:
                    subject, _k, _n = _w.resolve_billing_subject(
                        user, data)
            except Exception:
                subject = None
        v = float(_w.tool_price_usd(tool_key, subject=subject) or 0)
        if v > 0:
            return _C._pm_usd_label(v, uname or None)
    except Exception as _fx:
        _faults.note('chat', _fx)
    return fallback


# <<block: 26>>
_PM_FW_JOB_PREFIX = 'system/pm_fw_jobs/'


# <<block: 27>>
_PM_FW_CHIP = 'Build a Flywheel'


# <<block: 28>>
_PM_FW_CREDITS = 5


# <<block: 29>>
_PM_FW_ASK_COPY = (
    "Happy to build a Flywheel. Give me, in one message:\n"
    "1. The title or brand the event is about (e.g. Gilmore Girls)\n"
    "2. The captured action that STARTS the file - the thing the "
    "users did, in one sentence (e.g. acquired or reactivated Prime "
    "after a first Gilmore Girls play following 180 days off). The "
    "flywheel always starts at those users, never the whole "
    "country. If you already have the count, give it and I lock it.\n"
    "3. The owned ecosystem the flywheel lives on (e.g. Amazon-owned "
    "surfaces, the TikTok Shop ecosystem)\n"
    "4. The conversion inside that ecosystem (e.g. an Amazon-owned "
    "checkout inside 30 days of the play)\n"
    "Optional: a two-way split (new vs reactivated), the before / "
    "after windows (default: matched, 30 days each - a long before "
    "against a short after is a clock artifact, not a compare), and "
    "the overall window (default: trailing 12 months).\n\n"
    "Example: \"Gilmore Girls, start from the 22,764 who acquired or "
    "reactivated Prime after their first play, Amazon-owned "
    "ecosystem, conversion is an Amazon-owned checkout inside 30 "
    "days, split new vs reactivated\"")


# <<block: 30>>
def _pm_fw_intent(text):
    return _intents.fw_intent(text, _C._PM_FW_CHIP)


# <<block: 31>>
def _pm_fw_parse(text, usage_extras=None):
    from migration.flywheel_synthesis import PARSE_SYSTEM_PROMPT
    parsed = _C._pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=1200, temperature=0.0,
                             surface='fw_parse',
                             usage_extras=usage_extras)
    return parsed if isinstance(parsed, dict) else {}


# <<block: 32>>
def _pm_fw_inputs_complete(parsed):
    return bool(parsed.get('subject') and parsed.get('captured_action')
                and parsed.get('ecosystem')
                and parsed.get('conversion_event'))


# <<block: 33>>
def _pm_fw_confirm_reply(parsed):
    win = (f"{parsed['start_date']} to {parsed['end_date']}"
           if parsed.get('start_date') and parsed.get('end_date')
           else 'trailing 12 months')
    lock = (f" (locked at {int(parsed['cohort_count']):,} accounts)"
            if parsed.get('cohort_count') else '')
    split = (f"\n- Split: {parsed['splits_hint']}"
             if parsed.get('splits_hint') else '')
    pre = int(parsed.get('pre_days') or 30)
    post = int(parsed.get('post_days') or pre)
    return (
        f"Here's the Flywheel I'll build:\n"
        f"- {parsed['subject']} on {parsed['ecosystem']} surfaces\n"
        f"- Starting point: {parsed['captured_action']}{lock}\n"
        f"- Conversion: {parsed['conversion_event']}\n"
        f"- Windows: {pre} days before vs {post} days after the "
        f"event (matched), inside {win}{split}\n\n"
        f"It starts at those captured users (never the whole "
        f"country) and shows three things: who they are, every "
        f"owned touch point before the event against after it, and "
        f"what the converters bought. It lands on the Flywheel IQ "
        f"page when finished. It prices at "
        f"{_C._pm_tool_price_label('flywheel_iq', '$500')}. Run it?")


# <<block: 34>>
def _pm_engine_failure_alert(route, err, job_id, username, inputs, told_user, what):
    """The ops alert for an engine job that failed on the worker (2026-11-03). The worker has no request, so
    the alert used to read "User: (unknown) / Question: (no question captured)" and the three Gilmore Girls
    flywheel failures (Grace Yin on the jaypatel14 seat) went to Jenna and Jessie looking like anonymous
    noise. The alert now carries the seat and its email, the ask as the person framed it, the job id, and
    exactly what the user was told; the promise banner is off because the user was told the build could
    not be completed and the credits went back, so nothing is owed by email."""
    try:
        who = str(username or '')
        try:
            em = _C._pm_user_email(username)
        except Exception:
            em = ''
        label = f"{who} ({em})" if em and em.lower() != who.lower() else (em or who or '(unknown seat)')
        payload = {'_no_promise': True, 'text': str(what or '')[:400], 'job_id': job_id,
                   'user_told': str(told_user or ''), 'inputs': _smart.jsonable(inputs) if inputs else None}
        _H._chatbot_error_email(route, err, user_email=label, payload=payload)
    except Exception as _ax:
        _faults.note('chat', _ax)


# <<block: 35>>
def _pm_run_fw_job(job_id, username, inputs, extras):
    try:
        from migration.flywheel_synthesis import synthesize, persist
        subj = f"{inputs['subject']} {inputs['ecosystem']} flywheel"
        _C._pm_fw_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': subj, 'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        tools = None
        try:
            import prometheus_analysis as _pma_f
            tools = [_pma_f.WEB_SEARCH_TOOL]
        except Exception as _fx:
            _faults.note('chat', _fx)

        def _cf(system, user_prompt, **kw):
            kw.setdefault('usage_extras', extras)
            return _C._pm_claude_data(system, user_prompt, **kw)

        csv_text, study_name, summary = synthesize(
            inputs, _cf, tools=tools,
            created_by=username or 'prometheus')
        out_key = persist(_H.s3_client, csv_text, study_name)
        try:
            def _grant(data):
                u = (data.get('users') or {}).get(username)
                if not u:
                    return None
                if not u.get('has_flywheel_iq_access'):
                    u['has_flywheel_iq_access'] = True
                    return data
                return None
            _H._users_cas_mutate(_grant)
        except Exception as acc_err:
            print(f"[fw-job {job_id}] access grant skipped: {acc_err}")
        _C._pm_fw_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': summary['title'],
            's3_key': out_key,
            'conversions': summary.get('conversions'),
            'finished_at': time.time()})
        print(f"[fw-job {job_id}] done -> {out_key}")
        try:   # delivered contract (2026-10-13)
            from prometheus.delivered import on_done as _on_done
            _on_done('flywheel', username=username, subject=str(summary.get('title') or ''), s3_key=out_key, payload=summary,
                     host=_H, s3_client=_H.s3_client, job_id=job_id,
                     figures=([{'name': 'Captured users who converted', 'value': summary.get('conversions'), 'unit': 'people', 'definition': 'Flywheel read'}]
                              if isinstance(summary.get('conversions'), (int, float)) else []),
                     facts=([('Converted', f"{int(summary['conversions']):,}")] if isinstance(summary.get('conversions'), (int, float)) else []))
        except Exception as _dl_err:
            print(f"[fw-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        refunded = False
        try:   # the build could not be completed: the credits go back (no "snag", no hold)
            _H.refund_credit(username, credits=_C._PM_FW_CREDITS, reason=f"Flywheel did not deliver - {(inputs or {}).get('subject') or 'flywheel'}", job_id=str(job_id or ''))
            refunded = True
        except Exception as _rx:
            _faults.note('chat', _rx)
        try:
            _C._pm_fw_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error', 'refunded': refunded,
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        _C._pm_engine_failure_alert(
            'brief-chat/fw-job', e, job_id, username, inputs,
            'This build could not be completed. Credits for this run have been refunded.' if refunded
            else 'This build could not be completed. (the refund did not post; check the seat)',
            f"Flywheel: {(inputs or {}).get('entry_point') or (inputs or {}).get('captured_action') or (inputs or {}).get('subject') or ''}"
            f" / {(inputs or {}).get('ecosystem') or ''} ecosystem")


# <<block: 36>>
# ---- Attribution IQ tracking pull (Jenna 2026-09-22) -----------------
# "$500 for the first setup pull ... toggle it on to refresh daily (at
# 100$ x day) ... put in when you want it to stop tracking. so youre
# charged up front for tracking through the day you want the tracking
# to end ... input all of their urls and tag them as paid or organic,
# name the campaign, etc."
_PM_AIQ_JOB_PREFIX = 'system/pm_aiq_jobs/'


# <<block: 37>>
_PM_AIQ_CHIP = 'Set up Attribution Tracking'


# <<block: 38>>
_PM_AIQ_SETUP_CREDITS = 5      # $500 at the standard $100/credit


# <<block: 39>>
_PM_AIQ_DAILY_CREDITS = 1      # $100 per prepaid daily-refresh day


# <<block: 40>>
def _pm_aiq_intent(text):
    return _intents.aiq_intent(text, _C._PM_AIQ_CHIP)


# <<block: 41>>
def _pm_aiq_stop_intent(text):
    low = ' '.join(str(text or '').lower().split())
    return (('stop' in low or 'end' in low or 'cancel' in low)
            and ('tracking' in low or 'attribution' in low)
            and 'attribution' in low)


# <<block: 42>>
def _pm_aiq_parse(text, usage_extras=None):
    """Model extraction merged with the deterministic URL/tag parser -
    the tags are the user's own, so the deterministic read wins
    whenever it finds tagged lines (the model never guesses tags)."""
    from migration.attribution_synthesis import (PARSE_SYSTEM_PROMPT,
                                                 parse_url_lines)
    parsed = _C._pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=2500, temperature=0.0,
                             surface='aiq_parse',
                             usage_extras=usage_extras)
    parsed = parsed if isinstance(parsed, dict) else {}
    det = parse_url_lines(text)
    if det:
        parsed['urls'] = det
    return parsed


# <<block: 43>>
def _pm_aiq_days(parsed):
    """Prepaid daily-refresh days: tomorrow through the stop date
    inclusive (the setup pull itself carries today's read)."""
    from datetime import date as _d
    if not parsed.get('daily_refresh'):
        return 0
    try:
        end = datetime.strptime(
            str(parsed.get('end_tracking_date') or ''),
            '%Y-%m-%d').date()
    except Exception:
        return 0
    return max(0, (end - _d.today()).days)


# <<block: 44>>
def _pm_aiq_inputs_complete(parsed):
    urls = [u for u in (parsed.get('urls') or [])
            if isinstance(u, dict) and u.get('url')
            and str(u.get('tag') or '').lower() in ('paid', 'organic')]
    if not (parsed.get('campaign_name') and urls
            and parsed.get('conversion_event')):
        return False
    if parsed.get('daily_refresh') is None:
        return False
    if parsed.get('daily_refresh') and _C._pm_aiq_days(parsed) < 1:
        return False
    return True


# <<block: 45>>
def _pm_aiq_confirm_reply(parsed):
    from prometheus import aiq_assets as _aa
    return _aa.confirm_card(parsed, _C._pm_aiq_days(parsed), _C._pm_tool_price_label('attribution_iq_setup', '$500'),
                            _C._pm_tool_price_label('attribution_iq_daily', '$100'), _C._pm_usd_label)


# <<block: 46>>
def _pm_run_aiq_job(job_id, username, inputs, extras):
    try:
        from migration.attribution_synthesis import (build_campaign,
                                                     register_tracker)
        name = str(inputs.get('campaign_name') or 'Campaign')
        _C._pm_aiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': name, 'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        if inputs.get('add_to_slug'):   # assets joining a campaign that exists (Alexia 2026-10-16)
            from migration.attribution_synthesis import add_assets_to_campaign
            out = add_assets_to_campaign(str(inputs['add_to_slug']), list(inputs.get('urls') or []), requested_by=username, s3_client=_H.s3_client)
        else:
            out = build_campaign(inputs, requested_by=username, s3_client=_H.s3_client)
        days = _C._pm_aiq_days(inputs)
        if inputs.get('daily_refresh') and days > 0:
            register_tracker(out['slug'], name, username,
                             daily=True,
                             end_date=str(
                                 inputs.get('end_tracking_date')),
                             s3_client=_H.s3_client)
        # Access: the requester sees their campaign in the tab.
        try:
            def _grant(data):
                u = (data.get('users') or {}).get(username)
                if not u:
                    return None
                changed = False
                if not u.get('has_intent_iq_access'):
                    u['has_intent_iq_access'] = True
                    changed = True
                runs = u.get('allowed_intent_iq_runs')
                if isinstance(runs, list) and '*' not in runs \
                        and out['slug'] not in runs:
                    runs.append(out['slug'])
                    changed = True
                return data if changed else None
            _H._users_cas_mutate(_grant)
        except Exception as acc_err:
            print(f"[aiq-job {job_id}] access grant skipped: "
                  f"{acc_err}")
        _C._pm_aiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': name, 'slug': out['slug'],
            'asset_count': out['asset_count'],
            'daily': bool(inputs.get('daily_refresh')),
            'end_date': str(inputs.get('end_tracking_date') or ''),
            'finished_at': time.time()})
        print(f"[aiq-job {job_id}] done -> {out['slug']}")
        try:   # delivered contract (2026-10-13)
            from prometheus.delivered import on_done as _on_done
            _on_done('attribution', username=username, subject=str(name or ''), s3_key=str(out.get('slug') or ''), payload=out,
                     host=_H, s3_client=_H.s3_client, job_id=job_id,
                     facts=[('Tracked assets', f"{int(out.get('asset_count') or 0):,}")] + ([('Added', f"{int(out.get('added') or 0):,}")] if out.get('added') else []),
                     email=not bool(inputs.get('notify_manual')))
        except Exception as _dl_err:
            print(f"[aiq-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        try:
            _C._pm_aiq_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error',
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        _C._pm_engine_failure_alert(
            'brief-chat/aiq-job', e, job_id, username, inputs, 'the tracking error line in the widget',
            f"Attribution tracking: {(inputs or {}).get('campaign_name') or (inputs or {}).get('subject') or ''}")


__all__ = ['_PM_BPIQ_JOB_PREFIX', '_PM_BPIQ_CHIP', '_PM_BPIQ_CREDITS', '_PM_BPIQ_ASK_COPY', '_pm_read_intent', '_pm_bpiq_intent', '_pm_bpiq_parse', '_pm_bpiq_flight_research', '_pm_bpiq_inputs_complete', '_pm_bpiq_confirm_reply', '_pm_run_bpiq_job', '_PM_JIQ_JOB_PREFIX', '_PM_JIQ_CHIP', '_PM_JIQ_CREDITS', '_PM_JIQ_ASK_COPY', '_pm_jiq_intent', '_pm_jiq_parse', '_pm_jiq_inputs_complete', '_pm_jiq_is_ticketing', '_pm_jiq_ready_message', '_pm_jiq_confirm_reply', '_pm_run_jiq_job', '_pm_price_sentence', '_pm_included_seat', '_pm_tool_price_label', '_PM_FW_JOB_PREFIX', '_PM_FW_CHIP', '_PM_FW_CREDITS', '_PM_FW_ASK_COPY', '_pm_fw_intent', '_pm_fw_parse', '_pm_fw_inputs_complete', '_pm_fw_confirm_reply', '_pm_engine_failure_alert', '_pm_run_fw_job', '_PM_AIQ_JOB_PREFIX', '_PM_AIQ_CHIP', '_PM_AIQ_SETUP_CREDITS', '_PM_AIQ_DAILY_CREDITS', '_pm_aiq_intent', '_pm_aiq_stop_intent', '_pm_aiq_parse', '_pm_aiq_days', '_pm_aiq_inputs_complete', '_pm_aiq_confirm_reply', '_pm_run_aiq_job']
