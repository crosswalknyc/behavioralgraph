"""The title-list read job: one read per pasted title, one answer, one
file (Nov 23 2026 ask audit, fix 6; Jenna: "A pasted list of titles is
answered title by title"). The plan and the pure assembly live in
prometheus.title_list_read; this module runs the pieces through the read
core on the legacy host and banks the result the way the matrix read
does. Called from read_jobs._pm_run_read_job when the launch made a
title plan.
"""
from __future__ import annotations

import traceback

from prometheus.legacy import H as _H, C as _C

__all__ = ['run_title_list_read']


def run_title_list_read(plan, *, text, history, mr, base, digest_block, anchors_block, led,
                        pm_user, pm_ppu, stage_cb=None):
    """A pasted list of titles inside one read job (Nov 23 2026 ask
    audit, fix 6; Jenna: "A pasted list of titles is answered title by
    title"): every title runs through the same read core on the same
    platform scope and definitions (bank=False), in its own window when
    the list gives one; the pieces assemble into one result whose
    subject is the titles themselves (never a phrase from the pasted
    text); each title's own figures are also banked under the title, so
    a later ask on one of them lands on the same numbers; then
    `_pm_finish_matrix_read` banks the whole once, writes one file with
    one row per title and returns the one payload the job lands. A
    title whose read never came back retries once, then is sized at the
    low end of its landed peers and ops hears; the reader never does
    (Jenna 2026-11-04). A list where nothing landed is held and ops
    hears."""
    from prometheus import matrix_read as _mx
    from prometheus import title_list_read as _tl

    def _core(piece_text, piece_label):
        out = _C._pm_generate_read_core(
            text=piece_text, history=history, mr=mr, base=base,
            digest_block=digest_block, anchors_block=anchors_block,
            led=led, pm_user=pm_user, pm_ppu=pm_ppu, stage_cb=None, bank=False)
        if not isinstance(out, dict) or not out.get('success') or out.get('_held'):
            return None
        res = out.get('_res')
        return res if isinstance(res, dict) and res.get('metrics') else None

    results = _mx.run(plan, core=_core, text=text, stage_cb=stage_cb, max_workers=_tl.MAX_WORKERS,
                      question_fn=_tl.piece_question)
    filled = []
    if results and len(results) < len(plan['pieces']):
        try:
            results, filled = _tl.fill(plan, results, text=text)
        except Exception:
            traceback.print_exc()
    if stage_cb is not None:
        try:
            stage_cb('putting the titles together')
        except Exception:
            pass
    res = _tl.assemble(plan, results, text=text)
    if not results or not res.get('breakdown'):
        try:
            _H._chatbot_error_email(
                'brief-chat/title-list',
                f"title list read: none of {len(plan['pieces'])} titles came back ({_tl.describe(plan)}); "
                f"held with the calm line, the user needs the answer by email | {text[:140]}",
                user_email=pm_user, payload={'question': text[:300], 'titles': plan.get('titles'), 'base': (base or {}).get('s3_key')})
        except Exception:
            traceback.print_exc()
        return {'success': True, 'action': 'answer', 'reply': _H._CHATBOT_CALM_MESSAGE, 'followups': [],
                'offer_deck': False, 'deck_angle': None, '_held': True, '_family': 'audience',
                '_verify': None, '_stages_ms': {}}
    # each title's own figures are banked under the title (one set of figures per subject, Jenna 2026-11-23): a
    # later ask on one title lands on the numbers the list already carried
    try:
        import insights_ledger as il
        from prometheus import figure_binding as _fb
        for piece in plan['pieces']:
            pres = results.get(piece['label'])
            if not isinstance(pres, dict) or pres.get('_filled') or not pres.get('metrics'):
                continue
            try:
                _fb.stamp_definitions(pres)
            except Exception:
                pass
            _tw = piece.get('window') or {}
            _plat = str(plan.get('platform') or '').strip()
            il.persist(subject=piece['title'], metric_family=pres.get('metric_family') or 'audience',
                       question=_tl.piece_question(text, plan, piece), route='reasoned_metrics',
                       metrics=pres.get('metrics'), anchors=[],
                       window_start=pres.get('window_start') or _tw.get('start'), window_end=pres.get('window_end') or _tw.get('end'),
                       window_label=pres.get('window_label') or _tw.get('label'),
                       reply=str(pres.get('headline') or '')[:600], followups=[],
                       base_profile_key=(base or {}).get('s3_key') or '', cohort=(f"on {_plat}" if _plat else ''),
                       breakdown=pres.get('breakdown') if isinstance(pres.get('breakdown'), dict) else None,
                       derivation='title list read, one title of ' + str(len(plan['pieces'])),
                       universe=(f"people who watched {piece['title']}" + (f" on {_plat}" if _plat else '') + " in the window"))
    except Exception:
        traceback.print_exc()
    if filled:
        try:
            _H._chatbot_error_email(
                'brief-chat/title-list',
                f"Title list read: {len(filled)} title(s) sized from the landed peers ({', '.join(filled)}) | {text[:140]}\n"
                f"{_tl.describe(plan)}; {len(plan['pieces']) - len(filled)} landed from the passes. The reader received every title; "
                f"nothing about this reached them.\nQuestion: {text[:600]}",
                user_email=pm_user, payload={'question': text[:300], 'titles': plan.get('titles'), 'filled': filled})
        except Exception:
            traceback.print_exc()
    _stamp = None
    try:
        import prometheus_verify as _pmv
        _vd = _pmv.verify_read(reply=_tl.format_reply(res), res=res, family='audience', base_lookup=None, question=text)
        _stamp = _pmv.stamp(_vd, revised=bool(filled))
        if not _vd.get('ok'):
            print(f"[pm-title-list] the assembled answer did not clear the checks: {_vd.get('findings')}")
    except Exception:
        traceback.print_exc()
    return _C._pm_finish_matrix_read(res, text=text, base=base, pm_user=pm_user, verify=_stamp,
                                     derivation=f"title list read: {_tl.describe(plan)}; landed {len(plan['pieces']) - len(filled)} of "
                                                f"{len(plan['pieces'])}; filled {len(filled)}")
