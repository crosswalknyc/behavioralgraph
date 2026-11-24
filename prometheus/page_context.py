"""The page the chat is looking at: the compact numbers and the hydrated view behind an ask, the primary key for a name, the page context check, the meter and hints, the analyze and pull gates, and the KPI view helpers.

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
import re
from prometheus import faults as _faults
import time
from prometheus import kpi_definitions as _kpi

# <<moved>>
# <<block: 1>>
_PM_INTENT_HYDRATE_CACHE = {}


# <<block: 2>>
def _pm_intent_compact_numbers(doc):
    """Compact, reader-safe summary of a campaign's latest numbers:
    journey steps with per-step fall-off, ticket/checkout surfaces,
    first/last touch and assists, time to convert, and per-audience
    checkout fall-off. Bounded by construction (a few KB). Returns {}
    on any shape surprise."""
    try:
        if not isinstance(doc, dict) or not doc:
            return {}
        ov = (doc or {}).get('overall') or {}
        pt = ov.get('paths') or {}
        out = {'campaign': str((doc or {}).get('display_name')
                               or '')[:80],
               'as_of': str((doc or {}).get('as_of') or '')[:10]}
        cr = ov.get('conversion_rate')
        if isinstance(cr, (int, float)):
            out['conversion_rate_pct'] = round(cr * 100, 1)
        steps = []
        for n in (pt.get('nest') or [])[:6]:
            if not isinstance(n, dict) or n.get('stage') == '0_tam':
                continue
            acc = n.get('us_accounts')
            if isinstance(acc, (int, float)):
                steps.append({'step': str(n.get('label') or '')[:90],
                              'accounts': int(acc)})
        if steps:
            out['journey_steps'] = steps
            drops = []
            for i in range(1, len(steps)):
                a, b = steps[i - 1], steps[i]
                lost = a['accounts'] - b['accounts']
                if a['accounts'] > 0:
                    drops.append({
                        'from': a['step'], 'to': b['step'],
                        'lost_accounts': lost,
                        'lost_pct': round(100.0 * lost
                                          / a['accounts'], 1)})
            if drops:
                out['step_falloff'] = drops
        wh = pt.get('where') or {}
        if isinstance(wh.get('ticketer_partition'), list):
            out['ticket_checkout_surfaces'] = [
                {'surface': s.get('surface'),
                 'accounts': s.get('us_accounts'), 'pct': s.get('pct')}
                for s in wh['ticketer_partition'][:8]
                if isinstance(s, dict)]
        at = pt.get('attribution') or {}
        for fld in ('first_touch', 'last_touch', 'assists'):
            if isinstance(at.get(fld), list):
                out[fld] = [
                    {'touchpoint': x.get('touchpoint'),
                     'accounts': x.get('us_accounts'),
                     'pct': x.get('pct')}
                    for x in at[fld][:6] if isinstance(x, dict)]
        if isinstance(pt.get('time_to_conversion'), list):
            out['time_to_convert'] = [
                {'bucket': b.get('bucket'),
                 'accounts': b.get('us_accounts'), 'pct': b.get('pct')}
                for b in pt['time_to_conversion'][:6]
                if isinstance(b, dict)]
        for f in (pt.get('forks') or [])[:4]:
            if isinstance(f, dict) and f.get('of_stage') == '3_ticketer':
                out['compared_multiple_ticket_surfaces'] = {
                    'yes': f.get('yes'), 'no': f.get('no')}
        auds = (doc or {}).get('audiences')
        if isinstance(auds, dict):
            arows = []
            for k, a in list(auds.items())[:12]:
                if not isinstance(a, dict):
                    continue
                an = {n.get('stage'): n for n in
                      ((a.get('paths') or {}).get('nest') or [])
                      if isinstance(n, dict)}
                t = (an.get('3_ticketer') or {}).get('us_accounts')
                c = (an.get('4_paid') or {}).get('us_accounts')
                if isinstance(t, (int, float)) and t                         and isinstance(c, (int, float)):
                    arows.append({
                        'audience': str(k).replace('_', ' ').title(),
                        'ticket_page_accounts': int(t),
                        'checkout_accounts': int(c),
                        'checkout_falloff_pct':
                            round(100.0 * (t - c) / t, 1)})
            if arows:
                arows.sort(
                    key=lambda r: -r['checkout_falloff_pct'])
                out['audience_checkout_falloff'] = arows
        return out if out.get('journey_steps') else {}
    except Exception:
        traceback.print_exc()
        return {}


# <<block: 3>>
def _pm_intent_view_hydrate(view_ctx):
    """Attach the open campaign's own numbers to the intentIQ view
    context (2026-09-30 Jenna: "where is the fall-off from the ticket
    checkout page?" on the Attribution view must answer from the
    campaign on screen). The widget summary carries the campaign name
    and audience list but none of the journey numbers; this loads the
    campaign's latest daily numbers server-side, compacts them, and
    rides them under data.campaign_numbers so a grounded ask answers
    with real figures. Fail-soft: any miss returns the context
    unchanged. Cached per (campaign, day)."""
    data = (view_ctx or {}).get('data') or {}
    slug = re.sub(r'[^a-z0-9_\-]', '',
                  str(data.get('title') or '').lower())
    if not slug:
        return view_ctx
    from datetime import date as _pm_ivh_date
    ck = (slug, _pm_ivh_date.today().isoformat())
    hit = _C._PM_INTENT_HYDRATE_CACHE.get(ck)
    if hit is None:
        doc = None
        try:
            pfx = f'intent/{slug}/mta/coefficients_'
            resp = _H.s3_client.list_objects_v2(
                Bucket=_H.S3_BUCKET, Prefix=pfx)
            keys = sorted(o['Key']
                          for o in resp.get('Contents', []))
            if keys:
                doc = json.loads(_H.s3_client.get_object(
                    Bucket=_H.S3_BUCKET,
                    Key=keys[-1])['Body'].read())
        except Exception:
            doc = None
        hit = _C._pm_intent_compact_numbers(doc) if doc else {}
        if doc:
            # The campaign's own words and tables (phases, asset types,
            # channels, creative formats) ride with the numbers, so an
            # ask typed from the screen is the screen's ask and the read
            # answers from the campaign table, never a same-title profile
            # (2026-10-22, Alexia / The Influencer Project).
            try:
                from prometheus import view_base as _vb
                _assets = _formats = _phases = None
                try:
                    _adoc = json.loads(_H.s3_client.get_object(
                        Bucket=_H.S3_BUCKET, Key=f'intent/{slug}/source/normalized_assets.json')['Body'].read()) or {}
                    _assets, _phases = _adoc.get('assets'), _adoc.get('phases')   # the phases date the campaign window (2026-10-26)
                    _formats = json.loads(_H.s3_client.get_object(
                        Bucket=_H.S3_BUCKET, Key=f'intent/{slug}/mta/creative_formats.json')['Body'].read())
                except Exception:
                    pass
                hit = dict(hit or {}, **_vb.intent_vocabulary(doc, _assets, _formats, phases_in=_phases))
            except Exception:
                traceback.print_exc()
        if len(_C._PM_INTENT_HYDRATE_CACHE) > 16:
            _C._PM_INTENT_HYDRATE_CACHE.clear()
        _C._PM_INTENT_HYDRATE_CACHE[ck] = hit
    if hit:
        data = dict(data)
        data['campaign_numbers'] = hit
        view_ctx = dict(view_ctx)
        view_ctx['data'] = data
    return view_ctx


# <<block: 4>>
# One list (prometheus/view_base.py). intentIQ is the Attribution IQ view id
# the widget sends; it was missing here, so a profile left in the picker rode
# along as `primary` and became the base of a campaign ask (2026-10-22).
_PM_SELF_CONTAINED_VIEWS = __import__('prometheus.view_base', fromlist=['SELF_CONTAINED']).SELF_CONTAINED


# <<block: 5>>
def _pm_primary_key_for_name(name):
    """The base profile key for a display name the client sent without
    its key: an exact display-name match in the catalog (the Total
    Universe when the family has one), never Gen Pop, never a guess.
    '' when the library does not hold that exact name."""
    try:
        n = str(name or '').strip()
        if not n:
            return ''
        _norm = _H._normalize_for_match
        want = _norm(n)
        if not want:
            return ''
        best, best_score = '', None
        for e in _C._profile_catalog_for_chat():
            key = str(e.get('s3_key') or '').strip()
            if not key:
                continue
            disp = str(e.get('display_name') or '').strip()
            fam = disp.split(' - ')[0].strip()
            if _norm(disp) != want and not (_norm(fam) == want and ' - ' not in disp):
                continue
            try:
                from prometheus import named_subject as _ns_chk
                if _ns_chk.is_gen_pop(fam, _norm):
                    continue
            except Exception:
                pass
            score = (1 if ' - ' not in disp else 0, str(e.get('last_modified') or e.get('created_at') or ''))
            if best_score is None or score > best_score:
                best, best_score = key, score
        return best
    except Exception:
        traceback.print_exc()
        return ''


# <<block: 6>>
def _pm_validate_page_context(page_context):
    """Access-gate every s3 key in the page context. Returns
    (clean_ctx_or_None, err_response_or_None). A missing/keyless
    context is not an error; it means nothing is open.

    2026-08-26 (Jenna): the context may also carry `view_context`, a
    compact frontend summary of whatever dashboard view is on screen
    (Subscriber IQ, Trends, Microdramas IQ, ...). It is validated,
    field-whitelisted, and byte-capped server-side in
    prometheus_analysis.validate_view_context, and it can stand alone:
    a user on a data-bearing view with no profile open still gets an
    analysis grounded in the on-screen data."""
    if not isinstance(page_context, dict):
        return None, None
    view_ctx = None
    try:
        import prometheus_analysis as _pma_vc
        view_ctx = _pma_vc.validate_view_context(
            page_context.get('view_context'))
    except Exception:
        traceback.print_exc()
        view_ctx = None
    if view_ctx and str(view_ctx.get('view_id') or '') == 'intentIQ':
        try:
            view_ctx = _C._pm_intent_view_hydrate(view_ctx)
        except Exception:
            traceback.print_exc()
    primary = page_context.get('primary') or {}
    p_key = str(primary.get('s3_key') or '').strip()
    if not p_key and isinstance(primary, dict) and str(primary.get('name') or '').strip():
        # A primary named without its key (a thin client, the golden set:
        # {"primary": {"name": "Gunna"}}) is the base profile of that
        # name when the library holds exactly that subject (golden g065,
        # 2026-11-02: "what is the median age here" quoted a custom read
        # instead of reading the open profile).
        p_key = _C._pm_primary_key_for_name(str(primary.get('name')).strip())
        if p_key:
            primary = dict(primary, s3_key=p_key)
    # Self-contained views (2026-10-05, Alexia): a Digital Journey IQ
    # or Brand Partnership IQ screen carries its own subject. The
    # profile left selected in the picker (Netflix, in her case) is
    # stale context there, not the question; binding it made the chat
    # ask "Do you mean on Netflix?" about a ticketing journey.
    if view_ctx and str(view_ctx.get('view_id') or '') in _C._PM_SELF_CONTAINED_VIEWS:
        p_key = ''
    if not p_key:
        if view_ctx:
            return {'primary': None, 'cuts': [], 'extras': [],
                    'view_context': view_ctx}, None
        return None, None
    ok, err = _H._require_profile_run_access(p_key)
    if not ok and _faults.is_probe():
        ok, err = True, None   # the golden set and the canary run with no session; they read the library as the staff seat they stand in for
    if not ok:
        return None, err
    clean_cuts = []
    for c in (page_context.get('cuts') or [])[:3]:
        ck = str((c or {}).get('s3_key') or '').strip()
        if not ck or ck == p_key:
            continue
        c_ok, _c_err = _H._require_profile_run_access(ck)
        if not c_ok:
            continue
        clean_cuts.append({'s3_key': ck,
                           'name': str((c or {}).get('name') or '')[:200]})
    # Extras (2026-08-21): independent profiles pulled in for
    # cross-profile convergence / whitespace analysis (other open tabs
    # or picker selections). Same access gate as everything else.
    clean_extras = []
    seen_extra = set()
    for c in (page_context.get('extras') or [])[:3]:
        ck = str((c or {}).get('s3_key') or '').strip()
        if not ck or ck == p_key or ck in seen_extra:
            continue
        c_ok, _c_err = _H._require_profile_run_access(ck)
        if not c_ok:
            continue
        seen_extra.add(ck)
        clean_extras.append({'s3_key': ck,
                             'name': str((c or {}).get('name') or '')[:200]})
    return {'primary': {'s3_key': p_key,
                        'name': str(primary.get('name') or '')[:200]},
            'cuts': clean_cuts,
            'extras': clean_extras,
            'view_context': view_ctx}, None


# <<block: 7>>
def _pm_meter_answer(surface, ppu_extras=None):
    """Metered-usage record for an answer served from what's already
    there (insights-ledger replay, cache-served read) with no fresh
    model call.

    2026-09-14 (Jenna, verbatim: "nothing should EVER be free. if it
    doesnt have a set price it but is answerable from what's already
    there that should all be the metered usage."). Replaces the retired
    _pm_charge_async no-op (2026-09-09, which retired the per-ask
    credit debit in favor of session metering - do NOT restore a
    credit debit here). The taxonomy: set-price products charge their
    set price; every other answered ask is metered. Model-backed asks
    meter naturally through record_call; replayed answers record no
    tokens, so without this call they billed nothing. Rate:
    pricing.json:metered_answer_usd (super-admin editable in
    /admin/billing next to the markup), billed through the same
    pay-per-use session sweep as model calls. Subscribed full-tier
    users: the record carries attribution only (no pay_per_use flag),
    covered by their tier like their other asks. Never raises."""
    try:
        import render_usage_log as _rul
        extras = _C._pm_merge_extras(_C._pm_attrib_extras(), ppu_extras)
        _rul.record_metered_answer(surface, extras=extras)
    except Exception:
        traceback.print_exc()


# <<block: 8>>
def _pm_ask_hint(route=None, outcome=None, subject=None, mode=None):
    """Set ask-log inference hints on flask.g. Safe outside a request
    context (tests, threads): failures are swallowed."""
    try:
        from flask import g as _g
        if route:
            _g._pm_ask_route = route
        if outcome:
            _g._pm_ask_outcome = outcome
        if subject:
            _g._pm_ask_subject = str(subject)[:120]
        if mode:
            _g._pm_ask_mode = mode
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 9>>
def _pm_ask_stage(key, t0=None, ms=None, count=None):
    """Accumulate one per-stage duration (or count) on flask.g for the
    ask-log record (2026-08-28 latency instrumentation). Pass `t0` (a
    time.monotonic() start) for the common case, or an explicit `ms` /
    `count`. Safe outside a request context (tests, background
    threads): failures are swallowed, overhead is one dict write."""
    __import__('prometheus.progress', fromlist=['stage']).stage(key)   # the live line the widget shows (2026-10-14)
    try:
        from flask import g as _g
        stages = getattr(_g, '_pm_ask_stages', None)
        if stages is None:
            stages = {}
            _g._pm_ask_stages = stages
        if count is not None:
            stages[str(key)[:40]] = int(count)
        elif ms is not None:
            stages[str(key)[:40]] = int(ms)
        elif t0 is not None:
            stages[str(key)[:40]] = int((time.monotonic() - t0) * 1000)
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 10>>
def _pm_gate_analyze(user):
    """True when the user may hit an analysis / read / deck route.

    Super admins bypass. Everyone else must resolve to 'analysis' or
    'both'. Callers hitting the master switch off never reach this
    helper because `_synth_chat_gate` rejects first."""
    if not isinstance(user, dict):
        return False
    if str(user.get('role') or '').strip().lower() == 'super_admin':
        return True
    return _H._prometheus_mode_of(user) in ('analysis', 'both')


# <<block: 11>>
def _pm_gate_pull(user):
    """True when the user may hit a new-profile-pull route.

    Super admins bypass. Everyone else must resolve to 'pull' or
    'both'. Callers hitting the master switch off never reach this
    helper because `_synth_chat_gate` rejects first."""
    if not isinstance(user, dict):
        return False
    if str(user.get('role') or '').strip().lower() == 'super_admin':
        return True
    return _H._prometheus_mode_of(user) in ('pull', 'both')


# <<block: 12>>
def _pm_kpi_view(body_or_ctx):
    """(view_id, on_screen_labels) from a request body or a validated
    page context. Profile IQ when nothing else is open."""
    src_ = body_or_ctx if isinstance(body_or_ctx, dict) else {}
    pc = src_.get('page_context') if 'page_context' in src_ else src_
    pc = pc if isinstance(pc, dict) else {}
    vc = pc.get('view_context') if isinstance(pc.get('view_context'), dict) \
        else {}
    vid = str(vc.get('view_id') or pc.get('view') or 'profileIQ')
    try:
        labels = _kpi.on_screen_labels(vc.get('data') or {})
    except Exception:
        labels = []
    return vid, labels


# <<block: 13>>
def _pm_kpi_definition_for(text, body):
    """The glossary entry a definition-shaped ask names, or None."""
    try:
        vid, labels = _C._pm_kpi_view(body)
        if not _kpi.is_definition_ask(text, vid):
            return None
        try:
            # what the Crosswalk sample is has its own answer, the sample lane below (Tania Nasr 2026-10-20)
            from prometheus import guards as _gd
            if _gd.is_sample_definition_ask(text) or _gd.is_sample_size_ask(text):
                return None
        except Exception:
            pass
        return _kpi.find_definition(text, vid, labels)
    except Exception:
        return None


# <<block: 14>>
def _pm_kpi_prompt_extras(text, history, ctx, led_block):
    """Append the thread number bank and the view glossary to the
    binding block on a reconcile or definition ask. Never raises."""
    try:
        if not (_kpi.is_reconcile_ask(text) or _kpi.is_definition_ask(text)):
            return led_block
        vid, labels = _C._pm_kpi_view(ctx)
        parts = [str(led_block or '').strip()]
        nb = _kpi.numbers_block(history)
        if nb:
            parts.append(nb)
        db = _kpi.view_definitions_block(vid, labels)
        if db:
            parts.append(db)
        return '\n\n'.join(p for p in parts if p)
    except Exception:
        return led_block


__all__ = ['_PM_INTENT_HYDRATE_CACHE', '_pm_intent_compact_numbers', '_pm_intent_view_hydrate', '_PM_SELF_CONTAINED_VIEWS', '_pm_primary_key_for_name', '_pm_validate_page_context', '_pm_meter_answer', '_pm_ask_hint', '_pm_ask_stage', '_pm_gate_analyze', '_pm_gate_pull', '_pm_kpi_view', '_pm_kpi_definition_for', '_pm_kpi_prompt_extras']
