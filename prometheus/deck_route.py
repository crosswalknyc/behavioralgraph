"""Decks from the chat: the fuzzy subject suggestions, the deck subject resolve, the deck job and its route, the deck core, and the pay-per-use route.

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
from prometheus import file_names as _file_names, no_build as _no_build, study_record as _study_record
import os
import uuid
import threading
import traceback
import re
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
from prometheus import deck_sources as _deck_sources
from prometheus import bpiq_wizard as _bpiq_wiz
from prometheus import deck_pick as _deck_pick, deck_subject as _deck_subject
from prometheus import ledger_gate as _ledger_gate, probe_files as _probe_files, cut_list as _cut_list, primary_profile as _primary_profile
from prometheus import door_words as _door_words
from prometheus import promise_truth as _PT, read_repair as _read_repair, grid_facts as _grid_facts
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import read_recovery as _rr

# <<moved>>
# <<block: 1>>
_PM_DECK_PREFIX = 'system/prometheus_decks/'


# <<block: 2>>
_PM_DECK_FILE_PREFIX = 'generated_decks/'


# <<block: 3>>
def _pm_deck_fuzzy_suggestions(query):
    """Closest-catalog deck suggestions for a subject / ask that did not
    resolve to one exact profile (Jenna 2026-08-31: fuzzy-match the typed
    name and suggest the similar-named profiles instead of a generic
    punt). Reuses the interpret path's token-overlap shortlister
    (_shortlist_profile_matches) and layers per-user access gating; the
    grouping, family-set binding, and chip payloads live in
    prometheus_analysis (pure + unit-tested).

    Returns a list of confirm-chip payloads
    ({kind, label, subtitle, bind}); [] when nothing is close enough."""
    import prometheus_analysis as pma
    q = str(query or '').strip()
    if not q:
        return []
    try:
        catalog = _C._profile_catalog_for_chat()
    except Exception:
        traceback.print_exc()
        return []
    # Rank first, in memory, then gate ONLY the shortlist. The access
    # check reads the user record on every call; gating all 4,700
    # catalog entries up front took a regular seat 17 minutes on one
    # deck ask (Armine Meseldzhyan at Lionsgate, 2026-10-31) and the
    # turn came back as the calm failure line. Admin seats short-circuit
    # the check, which is why it never showed for us.
    try:
        ranked = _C._shortlist_profile_matches(
            q, catalog or [], max_candidates=max(_C.SYNTH_CHAT_MAX_CANDIDATES, 24))
    except Exception:
        ranked = None
    pool = list(ranked or [])
    if not pool:
        try:
            pool = pma.deck_fuzzy_pool(q, catalog or [])[:24] if hasattr(pma, 'deck_fuzzy_pool') else []
        except Exception:
            pool = []
    accessible = []
    for c in pool[:40]:
        k = str((c or {}).get('s3_key') or '').strip()
        if not k:
            continue
        try:
            ok, _err = _H._require_profile_run_access(k)
        except Exception:
            ok = False
        if ok:
            accessible.append(c)
    if not accessible:
        return []
    try:
        return pma.build_deck_suggestions(q, accessible, ranked=accessible)
    except Exception:
        traceback.print_exc()
        return []


# <<block: 4>>
def _pm_resolve_deck_subject(text, ctx):
    """Resolve a typed deck ask to a profile in the catalog.

    Returns (resolution, err). resolution is a dict:
      {'ctx': page-context-shaped dict, 'subject': display subject,
       'partner': partner or '', 'ask': the ask text,
       'clarify': None | {'question', 'options'}}
    err is a (jsonify, status) tuple when the ask cannot proceed.

    Order: a subject named in the ask wins over the open profile; the
    open profile covers subject-less asks ("Build a deck from this");
    multiple plausible catalog matches come back as clarify options
    (the existing clarify-chip pattern); no subject anywhere is a
    guidance error."""
    import prometheus_analysis as pma
    brief = pma.extract_deck_brief(text) if text else \
        {'subject': '', 'partner': ''}
    partner = brief.get('partner') or ''
    wanted = (brief.get('subject') or '').strip()
    _from_delivered = bool(isinstance(ctx, dict) and ctx.get('deck_source'))   # the material is the delivered journey or the attached file, never a library profile of the same name
    if _from_delivered:
        wanted, partner = '', ''

    def _norm(s):
        return _H._normalize_for_match(str(s or ''))

    def _match(phrase):
        """Catalog candidates for a subject phrase, TU files only
        (cuts are attached separately). Returns a list of catalog
        entries, best first, deduped by subject."""
        pn = _norm(phrase)
        if not pn:
            return []
        exact, prefix, contains = [], [], []
        for entry in _C._profile_catalog_for_chat():
            disp = str(entry.get('display_name') or '')
            if ' - ' in disp:
                continue  # cuts never lead a deck; the TU does
            dn = _norm(disp)
            sn = _norm(entry.get('subject'))
            if not dn:
                continue
            if pn in (dn, sn):
                exact.append(entry)
            elif dn.startswith(pn) or sn.startswith(pn):
                prefix.append(entry)
            elif pn in dn or pn in sn:
                contains.append(entry)
        seen, out = set(), []
        for entry in exact + prefix + contains:
            k = _norm(entry.get('display_name'))
            if k in seen:
                continue
            seen.add(k)
            out.append(entry)
        return out

    chosen, resolved_partner = None, partner
    if wanted:
        cands = _match(wanted)
        if not cands:   # "mistletoe murders series viewers" is Mistletoe Murders Viewers: the plain title, only after the name as written matched nothing (2026-11-12)
            wanted, cands = _deck_subject.plain_match(wanted, _match)
        if not cands and partner:
            # "deck for Noah Wyle" reads partner-shaped but names the
            # subject; if the partner matches a profile, it IS the
            # subject.
            cands = _match(partner)
            if cands:
                resolved_partner = ''
        if len(cands) == 1:
            chosen = cands[0]
        elif len(cands) > 1:
            if _norm(cands[0].get('display_name')) == _norm(wanted) or \
                    _norm(cands[0].get('subject')) == _norm(wanted):
                chosen = cands[0]
            else:
                opts = []
                for c in cands[:4]:
                    nm = str(c.get('display_name') or '').strip()
                    if nm:
                        opts.append(f"Build the {nm} insights deck")
                return ({'clarify': {
                    'question': 'Which profile should the deck cover?',
                    'options': opts}}, None)
    elif partner and not (ctx and ctx.get('primary')):
        cands = _match(partner)
        if len(cands) == 1:
            chosen = cands[0]
            resolved_partner = ''

    # Session fallback (2026-08-27, Paige Bueckers ad CTR follow-up):
    # "Build a deck from this data" right after a delivered read names
    # no subject and may have nothing open, but the session remembers
    # which subject the last read covered. Resolve that subject so the
    # deck consumes the data just delivered instead of erroring.
    if chosen is None and not wanted and not (ctx and ctx.get('primary')) and not _from_delivered:
        try:
            _last_read = session.get('pm_csv_last') or {}
        except Exception:
            _last_read = {}
        _last_subj = str(_last_read.get('subject') or '').strip()
        if _last_subj:
            cands = _match(_last_subj)
            if cands:
                chosen = cands[0]

    if chosen is not None:
        p_key = str(chosen.get('s3_key') or '').strip()
        ok, err = _probe_files.run_access(_H._require_profile_run_access, p_key)   # the golden door has no session (prometheus.probe_files)
        if not ok:
            return None, err
        subject = str(chosen.get('subject')
                      or chosen.get('display_name') or '').strip()
        cuts = []
        base_norm = _norm(chosen.get('display_name'))
        for entry in _C._profile_catalog_for_chat():
            disp = str(entry.get('display_name') or '')
            if ' - ' not in disp:
                continue
            head, _, tail = disp.partition(' - ')
            if _norm(head) == base_norm and 'avid' in tail.lower():
                ck = str(entry.get('s3_key') or '').strip()
                c_ok, _c = _probe_files.run_access(_H._require_profile_run_access, ck)
                if c_ok and ck and ck != p_key:
                    cuts.append({'s3_key': ck, 'name': disp[:200]})
                break
        new_ctx = {'primary': {'s3_key': p_key,
                               'name': str(chosen.get('display_name')
                                           or subject)[:200]},
                   'cuts': cuts, 'extras': [],
                   'view_context': (ctx or {}).get('view_context')}
        return ({'ctx': new_ctx, 'subject': subject,
                 'partner': resolved_partner, 'clarify': None}, None)

    # A dashboard view on screen is deck material (2026-10-09, Liz: a deck
    # asked for on the Digital Journey view came back as slide text because
    # decks only knew profiles). With no library profile matched, the open
    # view (its on-screen summary plus the reads already delivered in this
    # thread) is the digest; the named subject, the view's subject, or the
    # view's title names the deck.
    _vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
    # any view with figures (data or its on-screen summary) is deck material (2026-10-13 audit)
    if isinstance(_vc, dict) and ((_vc.get('data') or {}) or str(_vc.get('summary') or _vc.get('view_summary') or '').strip()) \
            and not (ctx and ctx.get('primary')):
        _vsubj = (pma.resolve_subject(ctx, _vc) or str(_vc.get('view_title') or '').strip()
                  or wanted or 'this view')   # the view names the deck; a typed phrase that matched no profile does not (2026-10-13)
        return ({'ctx': {'primary': None, 'cuts': [], 'extras': [], 'view_context': _vc},
                 'subject': _vsubj[:120], 'partner': resolved_partner, 'clarify': None,
                 'view_deck': True}, None)

    # Fuzzy suggestions (2026-08-31, Jenna): a NAMED subject that did not
    # resolve to one exact profile offers the closest catalog profiles as
    # confirm chips (including a whole-family set), not a generic punt.
    # An explicitly named subject wins even over an open profile, since
    # the user asked for something specific by name.
    if wanted:
        suggestions = _C._pm_deck_fuzzy_suggestions(wanted)
        if suggestions:
            return ({'clarify': {
                'question': (f'I could not find an exact match for '
                             f'"{wanted}". Did you mean one of these?'),
                'suggestions': suggestions}}, None)

    if ctx and ctx.get('primary'):
        name = str((ctx.get('primary') or {}).get('name') or '').strip()
        subject = name.split(' - ')[0].strip() or name or 'this audience'
        return ({'ctx': ctx, 'subject': subject,
                 'partner': _deck_subject.buyer(resolved_partner, subject), 'clarify': None,   # the open profile named after "for" is the subject, never its own buyer (2026-11-12)
                 'used_open_page': True}, None)

    # No named subject and nothing open: recover a profile name from the
    # raw ask itself (the extractor can miss a name inside a long,
    # multi-part project brief). Same confirm-chip suggestions.
    if not wanted and text:
        suggestions = _C._pm_deck_fuzzy_suggestions(text)
        if suggestions:
            return ({'clarify': {
                'question': ('Here are the closest profiles in the '
                             'library. Which should the deck cover?'),
                'suggestions': suggestions}}, None)

    if wanted:
        return None, (jsonify({
            'success': False, 'guidance': True,
            'error': (f'"{wanted}" is not in the library yet. Open the '
                      'profile from Select Profile, or build it first, '
                      'then ask for the deck again.')}), 404)
    if isinstance(_vc, dict) and str(_vc.get('view_title') or '').strip():   # a view with no figures: one honest line
        return None, (jsonify({
            'success': False, 'guidance': True,
            'error': (f"The {str(_vc.get('view_title')).strip()} page has no figures loaded for me to put on slides yet. "
                      "Open the read or profile you want the deck on, or name the subject, and ask again.")}), 400)
    return None, (jsonify({
        'success': False, 'guidance': True,
        'error': ('Name the subject ("build the Paige Bueckers insights '
                  'deck") or open a profile first, then ask for the deck '
                  'again.')}), 400)


# <<block: 5>>
# Pay-as-you-go attribution for deck jobs (2026-08-26): the kickoff
# request computes the extras (session id needs the request context)
# and stashes them here for the background thread to ride on the
# slide-plan model call. Keyed by job_id; popped when the job starts.
_PM_DECK_PPU_EXTRAS = {}


# <<block: 6>>
@_ledger_gate.job_scope(probe_kw='')
def _pm_run_deck_job(job_id, username, ctx, history, angle,
                     charge_user='', subject='', partner=''):
    """Background insights-deck build: digest -> full slide plan ->
    finished PPTX in the Crosswalk deck system -> S3 -> presigned URL.
    Status written to S3 at every phase so any worker can serve the
    poll. `charge_user` is the billing username charged at kickoff;
    refunded here if the build fails."""
    import tempfile
    base = {'job_id': job_id, 'user': username, 'angle': angle,
            'started_at': time.time()}
    _ppu_extras = _C._PM_DECK_PPU_EXTRAS.pop(job_id, None)
    try:
        import prometheus_analysis as pma
        import deck_builder
        _C._pm_deck_status_write(job_id, {**base, 'status': 'planning'})
        if isinstance(ctx, dict) and not ctx.get('primary') and isinstance(ctx.get('view_context'), dict):
            # View deck (2026-10-09, Liz): the on-screen view plus the reads
            # already delivered in this thread are the digest.
            digest, p_meta = _C._pm_view_deck_digest(ctx['view_context'], history, subject)
        else:
            digest, p_meta = pma.get_digest_bundle(_H.s3_client, _H.S3_BUCKET, ctx)
        subject = subject or p_meta.get('name') or 'this audience'
        _deck_prompt = pma.build_insights_deck_user_prompt(
            subject, partner, digest, history, angle)
        # Delivered reads ride along (2026-08-27, ad CTR follow-up):
        # "build a deck from this data" right after a measurement
        # read must consume that read even when the visible history
        # got truncated. The ledger holds every delivered number for
        # the subject; a deck that restates them stays consistent
        # with what the chat already said.
        try:
            import insights_ledger as _il_deck
            _led = _il_deck.consult(subject=subject)
            if _led.get('block'):
                _deck_prompt = (
                    f"{_deck_prompt}\n\n"
                    "DELIVERED READS (numbers already shipped for "
                    "this subject; keep any you restate consistent "
                    "with these)\n"
                    "==========================================\n"
                    f"{_led['block']}")
        except Exception:
            traceback.print_exc()
        try:
            import prometheus_knowledge as _pmk
            _kb = _pmk.knowledge_block(
                'deck', text=str(angle or ''), subject=subject,
                s3_client=_H.s3_client, bucket=_H.S3_BUCKET)
            if _kb:
                _deck_prompt = f"{_deck_prompt}\n\n{_kb}"
        except Exception as _fx:
            _faults.note('chat', _fx)
        # Corpus catalog (2026-10-06): the dashboard's own figures on
        # the subject bind the deck's headline numbers.
        try:
            _cat_block = _C._pm_catalog_block(subject)
            if _cat_block:
                _deck_prompt = f"{_deck_prompt}\n\n{_cat_block}"
        except Exception as _fx:
            _faults.note('chat', _fx)
        plan_result = _C._pm_claude_json(
            pma.INSIGHTS_DECK_SYSTEM_PROMPT, _deck_prompt,
            max_tokens=20000, temperature=0.4, surface='deck',
            usage_extras=_ppu_extras)
        if not plan_result.get('success'):
            raise RuntimeError(plan_result.get('error')
                               or 'slide plan failed')
        plan = plan_result.get('data') or {}
        if isinstance(plan, list):
            plan = next((d for d in plan if isinstance(d, dict)), {})
        # The read's own data, as text, and the names in it (2026-11-11): a
        # title, a brand or a person's name the read carries is never
        # flagged or rewritten by a spelling, casing or word-ban pass, and
        # a page of web addresses carries only addresses the read holds.
        _read_text, _read_names = '', []
        try:
            _read_text = pma.deck_read_text(digest, history)
            _read_names = pma.deck_entities(_read_text, subject)
        except Exception as _fx:
            _faults.note('chat', _fx)
        plan = pma.enforce_insights_plan(plan, subject, names=_read_names)
        if not plan.get('slides'):
            raise RuntimeError('slide plan came back empty')
        plan = _deck_pick.hold_plan_to_read(   # a deck made from a read keeps the read's figures (2026-11-11)
            plan, ctx, replan=lambda _note: _C._pm_claude_json(
                pma.INSIGHTS_DECK_SYSTEM_PROMPT, f"{_deck_prompt}\n\n{_note}", max_tokens=20000, temperature=0.4,
                surface='deck', usage_extras=_ppu_extras).get('data'),
            post=lambda _p: pma.enforce_insights_plan(_p, subject, names=_read_names))
        from prometheus import category_reach as _cre   # a card that is a group of brands carries its reach and index (2026-11-11)
        plan = _cre.annotate_deck(plan, ctx, subject)
        _C._pm_deck_status_write(job_id, {**base, 'status': 'rendering'})
        stem = re.sub(r'[^A-Za-z0-9_]+', '_',
                      str(plan.get('filename_stem')
                          or subject
                          or p_meta.get('name')
                          or 'Insights')).strip('_')[:60] or 'Insights'
        fname = _door_words.scrub_filename(f"{stem}_Insights.pptx")
        local = os.path.join(tempfile.gettempdir(),
                             f"pm_deck_{job_id}.pptx")
        static_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'static')
        # The finisher repairs whatever the plain-English check flags, in
        # place: a model pass over only the flagged lines first, then its
        # fixed repairs (2026-11-11: two decks shipped READY with a failed
        # readability check and were fixed by hand).
        try:
            _copy_rewriter = pma.make_deck_copy_rewriter(_C._pm_claude_json, usage_extras=_ppu_extras)
        except Exception as _fx:
            _faults.note('chat', _fx)
            _copy_rewriter = None
        deck_builder.render_insights_deck(
            plan, local, static_dir=static_dir,
            photo_subject=str(subject or p_meta.get('name') or ''),
            photo_kind=deck_builder.photo_kind_for_category(p_meta.get('brand_category')),
            copy_rewriter=_copy_rewriter, protected_names=_read_names, read_text=_read_text)
        base.update(image_subject=plan.get('_photo_subject') or plan.get('image_subject'),
                    photo_sources=plan.get('_photo_sources') or [],
                    # the pre-ship pass on the finished deck: ready, lint and
                    # readability counts, what it changed (2026-11-10)
                    finish=plan.get('_finish') or {})
        s3_key = f"{_C._PM_DECK_FILE_PREFIX}{job_id}/{fname}"
        with open(local, 'rb') as fh:
            _H.s3_client.put_object(
                Bucket=_H.S3_BUCKET, Key=s3_key, Body=fh.read(),
                ContentType=('application/vnd.openxmlformats-officedocument'
                             '.presentationml.presentation'))
        # A PDF copy when the ask wants one ("send me the deck as a pdf",
        # "PDF to PowerPoint and back", 2026-10-30 audit item 2) and the
        # build host can render it. Best effort: the PowerPoint always ships.
        _pdf_extra = {}
        if re.search(r"\bpdf\b", str(angle or ''), re.I):
            try:
                import shutil as _sh
                import subprocess as _sp
                _soff = _sh.which('soffice') or _sh.which('libreoffice')
                if _soff:
                    _pdf_dir = tempfile.mkdtemp(prefix='pm_deck_pdf_')
                    _sp.run([_soff, '--headless', '--convert-to', 'pdf', '--outdir', _pdf_dir, local],
                            timeout=300, capture_output=True)
                    _pdf_local = os.path.join(_pdf_dir, os.path.splitext(os.path.basename(local))[0] + '.pdf')
                    if os.path.exists(_pdf_local):
                        _pdf_name = f"{stem}_Insights.pdf"
                        _pdf_key = f"{_C._PM_DECK_FILE_PREFIX}{job_id}/{_pdf_name}"
                        with open(_pdf_local, 'rb') as _pfh:
                            _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=_pdf_key, Body=_pfh.read(),
                                                    ContentType='application/pdf')
                        _pdf_extra = {
                            'pdf_s3_key': _pdf_key, 'pdf_filename': _pdf_name,
                            'pdf_url': _H.s3_client.generate_presigned_url(
                                'get_object',
                                Params={'Bucket': _H.S3_BUCKET, 'Key': _pdf_key,
                                        'ResponseContentDisposition': _file_names.disposition(_pdf_name)},
                                ExpiresIn=7 * 24 * 3600)}
                        try:
                            os.remove(_pdf_local)
                        except OSError:
                            pass
            except Exception:
                traceback.print_exc()
        try:
            os.remove(local)
        except OSError:
            pass
        url = _H.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                    'ResponseContentDisposition':
                        _file_names.disposition(fname)},
            ExpiresIn=7 * 24 * 3600)
        _C._pm_deck_status_write(job_id, {
            **base, 'status': 'done', 'url': url, 'filename': fname,
            'title': plan.get('title'),
            'slides': len(plan.get('slides') or []), **_pdf_extra})
        # Delivered-deck anchors (2026-08-27): the shipped deck's
        # headline figures become binding ledger anchors so every
        # later read or deck for this subject stays commensurate
        # with what was delivered.
        try:
            import insights_ledger as _il_anchor
            _anchor_metrics = pma.extract_plan_anchors(plan)
            if _anchor_metrics:
                _il_anchor.ingest_deck_anchors(
                    subject=subject, metrics=_anchor_metrics,
                    source_name=fname)
        except Exception:
            traceback.print_exc()
        print(f"[prometheus] deck {job_id} done for {username}: {fname}")
        # Land the finished deck in the requester's chat thread so the
        # download link is waiting when they return, and (if they
        # opted in) email them the link.
        _deck_status = {'url': url, 'filename': fname, 's3_key': s3_key,
                        'title': plan.get('title'),
                        'slides': len(plan.get('slides') or []), **_pdf_extra}
        _C._pm_append_deck_to_history(username, job_id, _deck_status)
        if not _C._pm_flush_notify(job_id, 'deck', _deck_status):
            # A finished deck always reaches the requester's inbox with the
            # file attached (2026-10-09, Jenna: "sent to her email and that
            # she could download"), not only when they tapped notify.
            _C._pm_send_output_email('deck', _C._pm_user_email(username), _deck_status, sync=True)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/deck-job', e,
                             user_email=username,
                             payload={'job_id': job_id,
                                      'angle': str(angle)[:200]})
        _C._pm_deck_status_write(job_id, {**base, 'status': 'error',
                                       'error': str(e)[:400]})
        _PT.job_failed('deck', username, job_id, str(angle)[:300], land=lambda _p: _PT.land_note(_C._pm_thread_for_job(username, job_id), username, job_id, _p))
        _C._pm_notify_delete(job_id)
        # 2026-09-09 Jenna: decks are session-metered, not per-pull
        # charged, so there is no per-pull refund to issue on
        # failure. `charge_user` is always empty in the new flow;
        # branch retained as a defensive no-op for legacy callers.
        if charge_user:
            pass


# <<block: 7>>
@_H.app.route('/api/brief-chat/deck', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/deck')
def api_synth_chat_deck():
    """Kick off an async insights-deck build. The ask can name any
    profile in the library ("build the Paige Bueckers insights deck")
    or lean on the open page's profile ("Build a deck from this").
    Returns a job_id the frontend polls via deck-status, or a clarify
    payload when several profiles match the named subject."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Prometheus mode gate (2026-09-03, Jenna): decks are analysis
    # outputs. Pull-only users cannot kick off a deck build.
    if not _C._pm_gate_analyze(user):
        return _C._pm_gate_refusal('analyze')
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/deck', e)
        return jsonify(_H._chatbot_calm_payload())
    return _C._pm_deck_core(user, body)


# <<block: 8>>
def _pm_deck_core(user, body):
    """The deck surface for an already-gated user. Split out of
    ``api_synth_chat_deck`` (2026-10-01, Prometheus Phase 1); the
    tier, funds, and usage gates below run for every caller."""
    _C._pm_req_thread_set(body)
    # Prometheus tier gate (2026-08-26): decks are an analysis-tier
    # feature. pulls_only users without pay-as-you-go get the offer.
    _t_deck0 = time.monotonic()
    _gate_resp = _C._pm_access_gate(user)
    if _gate_resp is not None:
        return _gate_resp
    _C._pm_ask_stage('deck_access_gate', _t_deck0)
    _pm_ppu = _C._pm_usage_extras(user)
    text = str(body.get('text') or '').strip()[:600]
    angle = ((body.get('angle') or '').strip()
             or text
             or 'The strongest commercial story in this data.')[:400]
    history = body.get('history') or []
    if not isinstance(history, list):
        history = []
    ctx, ctx_err = _C._pm_validate_page_context(body.get('page_context'))
    if ctx_err:
        return ctx_err
    _ds = _deck_sources.resolve(text, ctx, history, user, _H, tid=str(body.get('thread_id') or ''))   # a PPT of the deck already delivered or attached (Debi 2026-10-21)
    if _ds:
        ctx, history = _ds
        angle = 'The same deck, slide for slide: every number, table and read exactly as delivered, in the same order, nothing added.'
        if re.search(r"\b(?:as|to|into|in)\s+(?:an?\s+)?pdf\b|\bpdf\s+(?:version|copy|export|file)\b", text, re.I):
            angle += ' Deliver a PDF copy too.'   # the deck job renders one where it can (2026-10-30)
    try:
        # a which-subject chip picked for a deck names the subject outright
        # (2026-10-31): resolve that name, keep the ask as the angle
        _bind_pick = str(body.get('bind_subject') or '').strip()
        if _bind_pick and body.get('confirm_open_screen'):
            _dp_pick = _bind_pick
        else:   # else the name the request carries ("a deck for mistletoe murders"); a name we do not hold, with another audience open, is asked once
            _dp_pick, _dp_q = ('', None) if _ds else _deck_subject.pick_or_ask(body, text, ctx, history, _C._profile_catalog_for_chat, hint=_C._pm_ask_hint)
            if _dp_q:
                return jsonify(_dp_q)
        # the picked or named subject, or no deck: a library profile, else the read delivered on it in this thread,
        # never another candidate from the list (prometheus.deck_pick, Samantha Kolton 2026-11-11)
        _pvd = None if _ds else _bpiq_wiz.deck_gate(user, body, text, history, _dp_pick, _C.globals())   # a deck of a read that put a dollar value on a partnership is the valuation order (Jenna 2026-11-11)
        if _pvd is not None:
            return jsonify(_pvd)
        if _ds:   # the material is something already delivered or attached: the words alone resolve it
            resolution, res_err = _C._pm_resolve_deck_subject(text, ctx)
            _dp_msg = ''
        else:
            resolution, res_err, _dp_msg = _deck_pick.settle(
                _dp_pick, text, ctx, history, _C._pm_resolve_deck_subject, load_read=lambda _j: _deck_pick.load_read_doc(
                    _H.s3_client, _H.S3_BUCKET, _C._PM_READ_PREFIX, _j, user.get('username') or user.get('email') or ''))
        if _dp_msg:
            return jsonify({'success': False, 'guidance': True, 'error': _dp_msg}), 404
        if resolution and resolution.get('from_read'):
            angle = _deck_pick.read_angle(resolution.get('subject'), text)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/deck', e,
                             payload={'text': text[:200]})
        return jsonify(_H._chatbot_calm_payload())
    if res_err:
        return res_err
    _C._pm_ask_stage('deck_resolve', _t_deck0)
    if resolution.get('clarify'):
        return jsonify(_deck_subject.choice_payload(resolution['clarify']))   # the did-you-mean carries its own sentence: as a block alone it read as an empty reply and the calm promise was recorded over it (2026-11-12)
    _page_named = False
    try:   # "make me a deck on New Heights" with New Heights Podcast Listeners open: the words confirm the page (2026-10-30, item 13)
        _page_named = bool(_subject_rules.words_name_page(text, str(resolution.get('subject') or ''))) \
            or bool(_subject_rules.deixis_points_at_screen(text, history)) or bool(_deck_subject.choice_picked(history, resolution.get('ctx')))   # "make this a deck" on an empty thread: this is the screen (golden g083); a did-you-mean choice just tapped is the answer, never confirmed again (2026-11-12)
    except Exception:
        _page_named = False
    if resolution.get('used_open_page') \
            and not body.get('confirm_open_screen') and not _page_named:
        page = str(resolution.get('subject') or 'this profile').strip()
        # the thread's own subject leads; a page the reader said no to is
        # never the lead chip (2026-10-20 Jenna, Mark at Bent Pixels)
        _osq = _subject_rules.open_screen_question(text, page, history, deck=True, what='the deck')
        if _osq:
            _C._pm_ask_hint(route='deck', outcome=_osq.pop('outcome'), subject=page)
            return jsonify(_osq)
        if body.get('_pm_correction') or body.get('bind_subject'):
            # the reader just corrected the page away (I2): never the page
            # yes/no again; the bound subject leads the which-subject question
            _subs = [x for x in [str(body.get('bind_subject') or '').strip()] + list(_subject_rules.thread_subjects(history, page)) if x]
            _wsp = _subject_rules.which_subject_payload(page, _subs[:3], what='the deck')
            _C._pm_ask_hint(route='deck', outcome='asked_which_subject_correction', subject=_subs[0] if _subs else page)
            return jsonify({'success': True, 'action': 'answer', 'reply': _wsp['reply'], 'followups': _wsp['followups'],
                            'offer_deck': False, 'deck_angle': None,
                            'memory_confirm': {'question': text, 'deck': True, 'options': [dict(o, deck=True) for o in _wsp['options']]}})
        yes = f'Yes, {page}'
        return jsonify({
            'success': True,
            'reply': (f'Do you want this on {page} (open on your '
                      f'screen)?'),
            'followups': [yes, 'Something else'],
            'memory_confirm': {
                'question': text,
                'deck': True,
                'options': [{'label': yes, 'subject': page,
                             'deck': True}],
            },
        })
    # Funds gate (2026-09-16, Jenna): decks are metered work too. It runs
    # AFTER the question (I8, 2026-10-20: a yes/no about the open tab
    # costs nothing and must not wait on the wallet).
    _funds_resp = _C._pm_funds_gate(user)
    if _funds_resp is not None:
        return _funds_resp
    _C._pm_ask_stage('deck_funds_gate', _t_deck0)
    ctx = resolution['ctx']
    deck_subject = resolution.get('subject') or ''
    deck_partner = resolution.get('partner') or ''
    job_id = uuid.uuid4().hex[:12]
    username = (user.get('username') or user.get('email') or '').strip()
    # 2026-09-09 Jenna: deck builds are session-metered, not per-pull
    # charged. Pay-per-use accounts bill via the session close;
    # subscribed accounts are covered by their tier. `_charge_user` is
    # kept as an empty string for downstream compatibility (the refund
    # path below reads it and no-ops when empty), but no per-pull
    # credit is deducted for a deck.
    _charge_user = ''
    # Attribute the deck's model spend to the enqueuing user for ALL
    # users (not just pay-as-you-go), so the daily spend email's
    # per-user Prometheus breakdown captures deck builds. The bg thread
    # has no request context, so identity is captured here at enqueue;
    # any pay-as-you-go billing fields in _pm_ppu are preserved.
    _deck_extras = _C._pm_merge_extras(_C._pm_attrib_extras(), _pm_ppu)
    if _deck_extras:
        _C._PM_DECK_PPU_EXTRAS[job_id] = _deck_extras
    _head = {'job_id': job_id, 'user': username, 'status': 'queued', 'angle': angle, 'started_at': time.time()}
    _C._pm_job_bind_thread(job_id, username)
    _args = (job_id, username, ctx, history[-14:], angle, _charge_user, deck_subject, deck_partner)
    if _faults.is_probe():   # the golden set grades the routing; a probe never builds a deck (2026-11-02)
        print(f"[pm-deck] probe ask: deck {job_id} on {deck_subject!r} not started")
        return jsonify({'success': True, 'job_id': job_id, 'subject': deck_subject, 'confirm_line': _deck_pick.confirm_line(resolution)})
    if _rr.queue_enabled():   # decks build on the Hetzner read worker like reads (2026-10-09); unclaimed in 150 s, the sweeper builds it here
        _head['resume'] = {'ctx': ctx, 'history': history[-14:], 'angle': angle, 'charge_user': _charge_user,
                           'subject': deck_subject, 'partner': deck_partner, 'extras': _deck_extras or {}}
        _rr.enqueue_deck(job_id, _head)
    else:
        _C._pm_deck_status_write(job_id, _head)
        _ledger_gate.note_job(job_id, username)
        threading.Thread(target=_C._pm_run_deck_job, args=_args, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id, 'subject': deck_subject, 'confirm_line': _deck_pick.confirm_line(resolution)})


# <<block: 9>>
@_H.app.route('/api/brief-chat/pay-per-use', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/pay-per-use')
def api_synth_chat_pay_per_use():
    """The user's Yes on the pay-as-you-go offer (2026-08-26, Jenna).

    Flips pay_per_use_enabled on the user's own record (ETag CAS via
    save_users), notifies Jenna/Jessie/Liz that pay per use started
    for this user, and returns enabled=True so the frontend replays
    the original ask seamlessly. Idempotent: a second Yes from an
    already-enabled user just confirms. The opt-in persists until an
    admin changes the user's tier in the admin (a tier change resets
    it)."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    import pay_per_use as ppu
    tier, already = ppu.resolve_access(user)
    if tier != ppu.ACCESS_PULLS_ONLY:
        # Full-tier (or super_admin) users are subscribed; nothing to
        # turn on.
        return jsonify({'success': True, 'enabled': False,
                        'already_subscribed': True})
    username = (session.get('username') or user.get('username')
                or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'no user'}), 400
    if not already:
        started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        try:
            data = _H.load_users()
            u = (data.get('users') or {}).get(username)
            if not u:
                return jsonify({'success': False,
                                'error': 'user not found'}), 404
            u['pay_per_use_enabled'] = True
            u['pay_per_use_started_at'] = started
            _H.save_users(data)
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False,
                            'error': 'could not update your access'}), 500
        display = (f"{user.get('first_name', '')} "
                   f"{user.get('last_name', '')}").strip() or username
        try:
            ppu.send_start_email_async(display,
                                       (user.get('email') or username))
        except Exception:
            traceback.print_exc()
        print(f"[pay-per-use] {username} opted in at {started}")
    return jsonify({'success': True, 'enabled': True})


__all__ = ['_PM_DECK_PREFIX', '_PM_DECK_FILE_PREFIX', '_pm_deck_fuzzy_suggestions', '_pm_resolve_deck_subject', '_PM_DECK_PPU_EXTRAS', '_pm_run_deck_job', 'api_synth_chat_deck', '_pm_deck_core', 'api_synth_chat_pay_per_use']
