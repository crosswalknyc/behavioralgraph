"""What the chat remembers about an ask: the remembered question and build, the last window, the history bind, the panel refund and the panel fact answer, prior entries, two named audiences, and the persisted identity.

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
import traceback
import re
from prometheus import faults as _faults
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response

# <<moved>>
# <<block: 1>>
def _pm_remember_ask(pm_user, question, subject=None, cohort=None,
                     ledger_key=None, route=None):
    """Write one resolved ask into the user's cross-session memory
    (fire-and-forget; the store is per-user and never informs another
    user's session). Marks the request so the ask-log decorator does
    not double-record."""
    try:
        import prometheus_memory as pmm
        pmm.remember(pm_user, question, subject=subject, cohort=cohort,
                     ledger_key=ledger_key, route=route)
        try:
            from flask import g as _g
            _g._pm_mem_recorded = True
        except Exception as _fx:
            _faults.note('chat', _fx)
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 2>>
def _pm_remember_ask_build(pm_user, question, subject=None, window=None,
                           region=None):
    """Record an approved build (subject + confirmed window + named
    markets) into per-user memory. Fire-and-forget; never raises."""
    try:
        import prometheus_memory as pmm
        pmm.remember(pm_user, question, subject=subject, route='build',
                     window=window, region=region or None)
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 3>>
def _pm_memory_last_window():
    """The session user's most recent non-default build window from
    per-user memory, or None. One small cached S3 GET; any failure
    returns None so the clarify keeps its current wording."""
    try:
        import prometheus_memory as pmm
        u = (session.get('username') or '').strip()
        return pmm.last_window(u) if u else None
    except Exception:
        return None


# <<block: 4>>
def _pm_history_bind_text(history):
    """Referent text for an anaphoric ask ('this audience', 'them'):
    the last few thread turns, newest last, so subject inference can
    bind whatever the thread just read."""
    parts = []
    for turn in (history or [])[-6:]:
        t = str((turn or {}).get('text') or '').strip()
        if t:
            parts.append(t[:400])
    return '\n'.join(parts)


# <<block: 5>>
def _pm_panel_refund(panel_charge):
    """Reverse a research-report charge when the read never delivered
    (job error or held-for-review). Mirrors the build flow's
    charge-then-refund posture. Never raises."""
    if not isinstance(panel_charge, dict):
        return
    try:
        u = str(panel_charge.get('user') or '').strip()
        n = int(panel_charge.get('credits') or 0)
        if u and n > 0:
            # The charge carried no job id; its ledger description and
            # pull type locate it so the refund restores the unit it
            # took (credits or dollars; refund_charge.py 2026-11-05).
            _H.refund_credit(
                u, credits=n,
                reason=('Research report did not deliver - '
                        f"{panel_charge.get('subject') or 'read'}"),
                charge_description=str(panel_charge.get('description') or ''),
                pull_type=str(panel_charge.get('pull_type') or ''))
            print(f"[pm-panel] refunded {n} credits to {u}")
    except Exception:
        traceback.print_exc()


# <<block: 6>>
def _pm_panel_fact_response(pm_user, ppu, text, base):
    """Panel facts (2026-09-30, Jenna: "lets now do the panel-fact
    queries upgrades"): a factual ask the shipped base file answers
    exactly - a demo share, a named brand's reach, a category top
    list, the audience size - returns the file's own numbers in one
    step. Byte-consistent with the dashboard, reads only what already
    shipped, and anything the file cannot answer exactly returns None
    so the full read runs unchanged. Never raises."""
    import prometheus_analysis as pma
    import insights_ledger as il
    key = str((base or {}).get('s3_key') or '')
    if not key.lower().endswith('.csv'):
        return None
    try:
        fact = pma.detect_panel_fact(text)
    except Exception:
        traceback.print_exc()
        return None
    if not fact:
        return None
    try:
        df, _etag = pma.load_profile_df(_H.s3_client, _H.S3_BUCKET, key)
        meta = pma._profile_meta(df, (base or {}).get('subject') or '')
        meta['s3_key'] = key     # the key names an account-holder universe when the SUBJECT row does not (2026-11-10)
        # 2026-10-02 audit: the subject's own platform is not a brand
        # fact ("what % of Roku subscribers ..." on The Roku Channel
        # file read back "Roku reaches 18.4% of the audience"). That
        # ask is about the audience itself; ride the full read.
        if fact.get('kind') == 'brand':
            _b = re.sub(r'[^a-z0-9]+', '', str(fact.get('brand') or '').lower())
            _s = re.sub(r'[^a-z0-9]+', '', str(meta.get('name') or '').lower())
            if _b and _s and (_b in _s or _s in _b):
                return None
        gmap = (pma.load_genpop_map(_H.s3_client, _H.S3_BUCKET)
                if fact.get('kind') == 'brand' else {})
        ans = pma.answer_panel_fact(fact, df, meta, gmap)
    except Exception:
        traceback.print_exc()
        return None
    if not ans or not ans.get('reply'):
        return None
    subj = meta.get('name') or (base or {}).get('subject') or ''
    _C._pm_ask_hint(route='panel_fact', outcome='answered', subject=subj)
    # Answered from what already shipped: metered, never free
    # (2026-09-14).
    try:
        _C._pm_meter_answer('panel_fact', ppu)
    except Exception:
        traceback.print_exc()
    try:
        _C._pm_remember_ask(pm_user, text, subject=subj,
                         route='panel_fact')
    except Exception:
        traceback.print_exc()
    chips = list(ans.get('followups') or [])[:2]
    # the profile's own universe: people with a digital touchpoint with the
    # subject in its window; each projected count stores the people in the
    # data behind it (2026-11-04)
    _pf_universe = f"US people with at least one digital touchpoint with {subj} in the window"
    entry_kw = dict(
        subject=subj, metric_family=ans.get('family') or '',
        question=text, route='panel_fact',
        metrics=il.attach_sample_people(ans.get('metrics') or [], universe=_pf_universe), reply=ans['reply'],
        followups=chips, base_profile_key=key,
        breakdown=ans.get('breakdown'),
        window_label=str(meta.get('window') or ''),
        derivation='exact values read from the shipped base profile',
        universe=_pf_universe)
    try:
        il.persist(**entry_kw)
    except Exception:
        traceback.print_exc()
    file_payload = {}
    try:
        entry = il.make_entry(**entry_kw)
        if entry.get('breakdown') or entry.get('metrics'):
            file_payload = _C._pm_answer_file_payload(
                entry,
                auto_save=bool(_C._PM_FILE_ASK_RE.search(str(text or ''))),
                username=pm_user, question=text)
            if file_payload and 'Email me this file' not in chips:
                chips.append('Email me this file')
    except Exception:
        traceback.print_exc()
    try:
        _C._pm_csv_point(subj, text, ans.get('family'))
    except Exception:
        traceback.print_exc()
    return jsonify({
        'success': True, 'action': 'answer', 'reply': ans['reply'],
        'followups': chips, 'offer_deck': False, 'deck_angle': None,
        'profile': subj, **file_payload})


# <<block: 7>>
def _pm_verify_prior_entries(res, family, led):
    """Stored ledger entries the verification pass compares a fresh
    read against: the pre-generation consult, plus a re-consult on the
    response's own subject when it resolved differently (the ask may
    have named no subject at all). Deduped; never raises."""
    import insights_ledger as il
    ents = list((led or {}).get('entries') or [])
    try:
        subj = str((res or {}).get('subject') or '').strip()
        led_subj = str((led or {}).get('subject') or '').strip()
        if subj and subj.lower() != led_subj.lower():
            led2 = il.consult(subject=subj, metric_family=family)
            ents.extend(led2.get('entries') or [])
    except Exception as _fx:
        _faults.note('chat', _fx)
    seen, out = set(), []
    for e in ents:
        if not isinstance(e, dict):
            continue
        k = (e.get('k'), e.get('ts'), e.get('qn'))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


# <<block: 8>>
def _pm_name_two_audiences(res, text, base=None):
    """An overlap read opens on the two audiences it crosses (Grace Yin,
    2026-11-08: "the overlap between Hazbin Hotel viewers and viewers of
    adult animation and anime on Netflix, Crunchyroll and HIDIVE" shipped
    under "On people who watched Hazbin Hotel on Netflix in the window:").
    Sets res['universe'] to '<A> and <B>' in the ask's own words, so the
    opener and the Base line name both and a service rides only on the
    side whose words (or whose bound audience) name it. Returns the label,
    or '' when the ask is not a two-audience read (nothing changes)."""
    try:
        from prometheus import overlap_read as _ov
        bound = [str((base or {}).get('subject') or ''), str((res or {}).get('subject') or '')]
        try:
            _two = _C._pm_two_base_detect(text)
            for side in (_two or {}).get('pair') or []:
                bound.append(str(side.get('subject') or ''))
        except Exception:
            pass
        label = _ov.two_audience_label(text, bound=[b for b in bound if b])
        if label:
            res['universe'] = label
        return label
    except Exception:
        traceback.print_exc()
        return ''


# <<block: 9>>
def _pm_persist_identity(res, base):
    """(subject, cohort) a generated read banks under. A read on a
    product view (Digital Journey IQ on screen) banks under the view's
    own title, with the model's label folded into the cohort, so every
    pass on that view consults and binds the same bucket (2026-11-04:
    Liz's six passes on Music to Long Form sat under six different
    subject labels and never saw one another). Everything else banks
    under the read's subject as before."""
    subj = str((res or {}).get('subject') or '').strip()
    coh = str((res or {}).get('cohort') or '').strip()
    try:
        if str((base or {}).get('source') or '') == 'view':
            anchor = str(base.get('subject') or '').strip()
            if anchor and _H._normalize_for_match(anchor) != _H._normalize_for_match(subj):
                bits = [b for b in (subj, coh) if b and _H._normalize_for_match(b) != _H._normalize_for_match(anchor)]
                return anchor, ' - '.join(dict.fromkeys(bits))[:120]
            if anchor:
                return anchor, coh
    except Exception as _fx:
        _faults.note('chat', _fx)
    return subj, coh


__all__ = ['_pm_remember_ask', '_pm_remember_ask_build', '_pm_memory_last_window', '_pm_history_bind_text', '_pm_panel_refund', '_pm_panel_fact_response', '_pm_verify_prior_entries', '_pm_name_two_audiences', '_pm_persist_identity']
