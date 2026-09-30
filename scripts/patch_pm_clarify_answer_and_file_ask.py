#!/usr/bin/env python3
"""Casey Pearson 2026-09-29 tightening, two defects from one ask.

1. Her answer to the which-audience clarify was routed as a brand new
   ask: the original question (hours by genre and platform, two cuts,
   as a csv) fell away and the open-screen confirm fired a second
   time. Fix: when the previous agent turn asked which audience the
   ask is about, this turn is the ANSWER - merge it back into the
   question that triggered the clarify and skip the confirm.

2. "Provide output as a csv" produced no file. Fix: when the ask
   itself requests a file, the generated read builds its CSV inline,
   uploads it, and returns download_url so the browser saves it
   automatically (the existing auto-save handler).

Apply to bg-webapp/app.py. Idempotent: refuses to run twice.
"""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
src = APP.read_text()

if "_pm_clarify_answer_merge" in src:
    raise SystemExit("already applied")


def splice(old, new, desc, count=1):
    global src
    n = src.count(old)
    if n != count:
        raise SystemExit(f"[{desc}] anchor found {n}x, wanted {count}")
    src = src.replace(old, new)
    print(f"[ok] {desc}")


# ------------------------------------------------------------------
# 1. Helpers, inserted above _pm_open_screen_confirm.
# ------------------------------------------------------------------
ANCHOR_DEF = "def _pm_open_screen_confirm(text, ctx):"

HELPERS = '''_PM_FILE_ASK_RE = re.compile(
    r"\\b(?:as|in|into|to)\\s+(?:a\\s+|an\\s+)?(?:csv|spreadsheet|excel|xlsx?)\\b"
    r"|\\b(?:provide|give|send|export|download|output)\\b[^.?!]{0,40}"
    r"\\b(?:csv|spreadsheet|excel|xlsx?)\\b"
    r"|\\bcsv\\s+(?:file|format|output|export)\\b",
    re.I)

_PM_CLARIFY_TURN_RE = re.compile(
    r"do you want this on |which audience should i use", re.I)


def _pm_clarify_answer_merge(history, text):
    """When the previous agent turn asked which audience the ask is
    about, this turn is the answer. Merge it back into the question
    that triggered the clarify so the original ask is not lost.

    Casey Pearson, 2026-09-29: hours by genre and platform, two cuts,
    as a csv. Her answer to the which-audience prompt was routed as a
    brand new ask, the original question fell away, and the confirm
    fired again. Returns the merged question, or '' when this turn is
    not a clarify answer."""
    t = str(text or '').strip()
    if not t or len(t) > 240 or '?' in t:
        return ''
    turns = [h for h in (history or []) if isinstance(h, dict)]
    last_agent = ''
    idx = -1
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role in ('agent', 'assistant'):
            last_agent = str(turns[i].get('text')
                             or turns[i].get('content') or '')
            idx = i
            break
        if role == 'user':
            break
    if not last_agent or not _PM_CLARIFY_TURN_RE.search(last_agent):
        return ''
    orig = ''
    for i in range(idx - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role != 'user':
            continue
        cand = str(turns[i].get('text')
                   or turns[i].get('content') or '').strip()
        if len(cand) >= 25 and cand.lower() not in (
                'something else', 'yes', 'no'):
            orig = cand
            break
    if not orig or orig.strip().lower() == t.lower():
        return ''
    return f"{orig}\\n\\nAudience: {t}"


''' + ANCHOR_DEF

splice(ANCHOR_DEF, HELPERS, "helpers above open-screen confirm")

# ------------------------------------------------------------------
# 2. Callsite: consume the clarify answer instead of re-confirming.
# ------------------------------------------------------------------
OLD_CALL = """    if isinstance(ctx, dict) and not str(body.get('mode') or '').strip():
        _osc = _pm_open_screen_confirm(text, ctx)
        if _osc is not None:
            return _osc"""

NEW_CALL = """    if isinstance(ctx, dict) and not str(body.get('mode') or '').strip():
        # An answer to the which-audience clarify is consumed here:
        # merge it into the question that triggered the clarify and
        # never re-ask (Casey Pearson, 2026-09-29).
        _ca_merged = _pm_clarify_answer_merge(history, text)
        if _ca_merged:
            text = _ca_merged
            try:
                _pm_ask_hint(route='clarify_answer_merge')
            except Exception:
                pass
        else:
            _osc = _pm_open_screen_confirm(text, ctx)
            if _osc is not None:
                return _osc"""

splice(OLD_CALL, NEW_CALL, "clarify answer consumed at callsite")

# ------------------------------------------------------------------
# 3. Generated read attaches its file when the ask requested one.
# ------------------------------------------------------------------
OLD_TAIL = """    _pm_remember_ask(pm_user, text, subject=res.get('subject'),
                     cohort=res.get('cohort'), route='generated')"""

NEW_TAIL = """    _pm_remember_ask(pm_user, text, subject=res.get('subject'),
                     cohort=res.get('cohort'), route='generated')
    # The ask requested a file (2026-09-29, Casey Pearson: "Provide
    # output as a csv" produced no file). Build the CSV from the same
    # numbers the reply shipped with, upload it, and hand back
    # download_url so the browser saves it automatically. The ledger
    # reply stays clean; the chip still serves repeats.
    _file_payload = {}
    try:
        if _PM_FILE_ASK_RE.search(str(text or '')) and (
                res.get('breakdown') or res.get('metrics')):
            _fe = {'subject': res.get('subject'),
                   'cohort': res.get('cohort'), 'question': text,
                   'metrics': res.get('metrics'),
                   'breakdown': res.get('breakdown'),
                   'ws': res.get('window_start'),
                   'we': res.get('window_end'),
                   'wl': res.get('window_label')}
            _fn, _fcsv = pma.build_generated_csv(_fe)
            _frng = ''
            if _fe.get('ws') and _fe.get('we'):
                _frng = (f"{_fmt_study_date(_fe['ws'])} - "
                         f"{_fmt_study_date(_fe['we'])}")
            elif _fe.get('wl'):
                _frng = str(_fe['wl'])
            _fcsv = _stamp_csv_text(_fcsv, _frng)
            _fn = _pm_csv_task_filename(_fe) or _fn
            _fkey = f"{_PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{_fn}"
            s3_client.put_object(Bucket=S3_BUCKET, Key=_fkey,
                                 Body=_fcsv.encode('utf-8'),
                                 ContentType='text/csv')
            _furl = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': _fkey,
                        'ResponseContentDisposition':
                            f'attachment; filename="{_fn}"'},
                ExpiresIn=7 * 24 * 3600)
            _file_payload = {'download_url': _furl, 'filename': _fn}
            reply += (f"\\n\\n{_fn} is saving to your browser "
                      "downloads now.")
    except Exception:
        traceback.print_exc()"""

splice(OLD_TAIL, NEW_TAIL, "file attach after remember")

OLD_RET = """    return {
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': res.get('subject'),
        '_family': fam0,
        '_verify': _verify_stamp,
        '_stages_ms': stages}"""

NEW_RET = """    return {
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': res.get('subject'),
        '_family': fam0,
        '_verify': _verify_stamp,
        '_stages_ms': stages,
        **_file_payload}"""

splice(OLD_RET, NEW_RET, "return carries download_url")

APP.write_text(src)
print(f"[done] {APP} patched ({len(src):,} bytes)")
