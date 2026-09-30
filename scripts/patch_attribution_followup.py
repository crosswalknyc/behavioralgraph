#!/usr/bin/env python3
"""Attribution view follow-ups answer from the campaign on screen
(2026-09-30 Jenna: "where is the fall-off from the ticket checkout
page?" on the Attribution IQ view drew "Do you mean for Paw Patrol
Series Viewers, or Obsession?").

Four edits:
1. _PM_FUNNEL_ASK_RE + _pm_view_vocab_hit: journey-step vocabulary and
   on-screen-vocabulary overlap both ground an ask in the open view.
2. Last-rung steer: a subjectless journey-step ask gets the campaign
   steer, never profile disambiguation from memory.
3. intentIQ hydration: the validated view context gains the campaign's
   own latest numbers (journey steps, step fall-off, surfaces, touch
   attribution, timing, per-audience checkout fall-off) so a grounded
   ask answers with real figures, not the widget's thin summary.
4. Renderer block grammar (separate files): a header line stacked on
   its table or bullets splits so both render with full treatment.
"""
import ast
from pathlib import Path

APP = Path("app.py")
src = APP.read_text()


def splice(s, old, new, desc):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"[{desc}] anchor count={n}")
    print(f"  ok: {desc}")
    return s.replace(old, new)


# ------------------------------------------------------------------
# 1. Helpers: funnel vocabulary + view-vocabulary overlap.
#    Inserted inside the span scripts/test_pm_view_grounded_asks.py
#    extracts (module level uses only `re`; json imports in-function).
# ------------------------------------------------------------------
OLD1 = '''_PM_CAMPAIGN_ASK_RE = re.compile(
    r"\\b(?:campaigns?|attribution|roas|ad\\s+spend)\\b", re.I)
'''
NEW1 = '''_PM_CAMPAIGN_ASK_RE = re.compile(
    r"\\b(?:campaigns?|attribution|roas|ad\\s+spend)\\b", re.I)

# Journey-step vocabulary (2026-09-30 Jenna: "where is the fall-off
# from the ticket checkout page?" on the Attribution view drew a
# profile disambiguation). Anyone asking about a conversion journey
# uses these nouns whatever the campaign; on the Attribution view they
# ground the ask, and at the no-base last rung a subjectless hit gets
# the campaign steer instead of memory options.
_PM_FUNNEL_ASK_RE = re.compile(
    r"\\b(?:fall[\\s-]?offs?|drop[\\s-]?offs?|funnel|check[\\s-]?outs?|"
    r"tickets?|ticket(?:ing)?\\s+pages?|carts?|purchase\\s+pages?|"
    r"abandon\\w*|first[\\s-]touch|last[\\s-]touch|assists?|"
    r"retarget\\w*|touch[\\s-]?points?|converters?|"
    r"conversion\\s+rates?|exposed|info[\\s-]?seek\\w*)\\b", re.I)

_PM_VIEW_VOCAB_STOP = frozenset((
    'this', 'that', 'what', 'where', 'when', 'which', 'with', 'from',
    'have', 'does', 'data', 'view', 'show', 'tell', 'about', 'many',
    'much', 'they', 'them', 'then', 'than', 'were', 'will', 'your',
    'mean', 'like', 'into', 'over', 'under', 'most', 'more', 'less',
    'here', 'there', 'page', 'pages', 'screen', 'week', 'month',
    'year', 'window', 'number', 'numbers', 'share', 'total', 'rate',
    'rates', 'percent', 'account', 'accounts', 'people', 'audience'))


def _pm_view_vocab_hit(text, vc):
    """The ask speaks the open view's own vocabulary: distinctive
    tokens from the ask that appear in the serialized on-screen data.
    Two distinctive hits, or one distinctive bigram, bind the view.
    Never raises; an empty or tiny context never matches."""
    try:
        import json as _json
        blob = _json.dumps(vc, ensure_ascii=False, default=str).lower()
    except Exception:
        return False
    if len(blob) < 80:
        return False
    toks = re.findall(r"[a-z][a-z0-9'-]{3,}", str(text or '').lower())
    distinct = [t for t in toks if t not in _PM_VIEW_VOCAB_STOP]
    hits = {t for t in distinct if t in blob}
    if len(hits) >= 2:
        return True
    for a, b in zip(toks, toks[1:]):
        if a in _PM_VIEW_VOCAB_STOP and b in _PM_VIEW_VOCAB_STOP:
            continue
        if f"{a} {b}" in blob:
            return True
    return False
'''
src = splice(src, OLD1, NEW1, "funnel regex + vocab-hit helpers")

# ------------------------------------------------------------------
# 2. _pm_view_owns_ask: funnel vocabulary owns the Attribution view;
#    on-screen vocabulary overlap owns any data view.
# ------------------------------------------------------------------
OLD2 = '''    if view_id == 'intentIQ' and (
            _PM_CAMPAIGN_ASK_RE.search(t)
            or re.search(r"\\bconversions?\\b", t, re.I)):
        return True
    return False
'''
NEW2 = '''    if view_id == 'intentIQ' and (
            _PM_CAMPAIGN_ASK_RE.search(t)
            or _PM_FUNNEL_ASK_RE.search(t)
            or re.search(r"\\bconversions?\\b", t, re.I)):
        return True
    # The ask uses words that are literally on the screen (audience
    # names, journey step labels, surfaces): the view owns it whatever
    # the phrasing (2026-09-30 Jenna, checkout fall-off follow-up).
    if _pm_view_vocab_hit(t, vc):
        return True
    return False
'''
src = splice(src, OLD2, NEW2, "view-owns-ask funnel + vocab branches")

# ------------------------------------------------------------------
# 3. Last rung: subjectless journey-step asks get the campaign steer.
# ------------------------------------------------------------------
OLD3 = '''        if _PM_CAMPAIGN_ASK_RE.search(str(text or '')):
            _pm_ask_hint(outcome='campaign_clarify')
'''
NEW3 = '''        _lr_funnel = False
        try:
            _lr_funnel = bool(
                _PM_FUNNEL_ASK_RE.search(str(text or ''))
                and not str(subj_hint or '').strip()
                and not str(pma.guess_subject_from_text(text)
                            or '').strip())
        except Exception:
            _lr_funnel = False
        if _PM_CAMPAIGN_ASK_RE.search(str(text or '')) or _lr_funnel:
            _pm_ask_hint(outcome='campaign_clarify')
'''
src = splice(src, OLD3, NEW3, "last-rung funnel steer")

# ------------------------------------------------------------------
# 4. intentIQ hydration: campaign numbers ride the view context.
# ------------------------------------------------------------------
OLD4 = '''def _pm_validate_page_context(page_context):
    """Access-gate every s3 key in the page context. Returns
    (clean_ctx_or_None, err_response_or_None). A missing/keyless
    context is not an error; it means nothing is open.
'''
NEW4 = '''_PM_INTENT_HYDRATE_CACHE = {}


def _pm_intent_compact_numbers(doc):
    """Compact, reader-safe summary of a campaign's latest numbers:
    journey steps with per-step fall-off, ticket/checkout surfaces,
    first/last touch and assists, time to convert, and per-audience
    checkout fall-off. Bounded by construction (a few KB). Returns {}
    on any shape surprise."""
    try:
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
                if isinstance(t, (int, float)) and t \
                        and isinstance(c, (int, float)):
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
        return out
    except Exception:
        traceback.print_exc()
        return {}


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
    slug = re.sub(r'[^a-z0-9_\\-]', '',
                  str(data.get('title') or '').lower())
    if not slug:
        return view_ctx
    from datetime import date as _pm_ivh_date
    ck = (slug, _pm_ivh_date.today().isoformat())
    hit = _PM_INTENT_HYDRATE_CACHE.get(ck)
    if hit is None:
        doc = None
        try:
            pfx = f'intent/{slug}/mta/coefficients_'
            resp = s3_client.list_objects_v2(
                Bucket=S3_BUCKET, Prefix=pfx)
            keys = sorted(o['Key']
                          for o in resp.get('Contents', []))
            if keys:
                doc = json.loads(s3_client.get_object(
                    Bucket=S3_BUCKET,
                    Key=keys[-1])['Body'].read())
        except Exception:
            doc = None
        hit = _pm_intent_compact_numbers(doc) if doc else {}
        if len(_PM_INTENT_HYDRATE_CACHE) > 16:
            _PM_INTENT_HYDRATE_CACHE.clear()
        _PM_INTENT_HYDRATE_CACHE[ck] = hit
    if hit:
        data = dict(data)
        data['campaign_numbers'] = hit
        view_ctx = dict(view_ctx)
        view_ctx['data'] = data
    return view_ctx


def _pm_validate_page_context(page_context):
    """Access-gate every s3 key in the page context. Returns
    (clean_ctx_or_None, err_response_or_None). A missing/keyless
    context is not an error; it means nothing is open.
'''
src = splice(src, OLD4, NEW4, "hydrator + compactor definitions")

OLD5 = '''    except Exception:
        traceback.print_exc()
        view_ctx = None
    primary = page_context.get('primary') or {}
'''
NEW5 = '''    except Exception:
        traceback.print_exc()
        view_ctx = None
    if view_ctx and str(view_ctx.get('view_id') or '') == 'intentIQ':
        try:
            view_ctx = _pm_intent_view_hydrate(view_ctx)
        except Exception:
            traceback.print_exc()
    primary = page_context.get('primary') or {}
'''
src = splice(src, OLD5, NEW5, "hydration call in context validator")

ast.parse(src)
APP.write_text(src)
print("app.py spliced + parses OK")
