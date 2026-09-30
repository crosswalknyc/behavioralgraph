#!/usr/bin/env python3
"""Sept 29 sweep tightening, three defects from today's ask log.

1. smclain approved Trinity Tatum twice in 2 minutes (second approve
   only moved the window 6 days) and two full builds ran. A re-approve
   of the same subject within 30 minutes now blocks as a duplicate
   BEFORE the queue post (so nothing is charged), unless the window
   moved materially (over 21 days on either end) - that is a
   correction and still builds.

2. scott's "over the last three years" ask drafted with the default
   trailing-12 window, so he had to re-approve with trailing 36 and
   pay for a second build. The interpret rule now spells out that
   worded durations ("over the last three years") are relative
   windows: three years = trailing 36 months.

3. scott's clarify answers ("no", "open on sceren") re-triggered
   confirms. The clarify-answer merge now also covers the "Did you
   want this on X or on Y?" copy, and bare answers (yes / no /
   something else / open on screen, typos included) re-run the
   original question clean instead of gluing "Audience: no" onto it.

Apply to bg-webapp/app.py after patch_pm_clarify_answer_and_file_ask.
Idempotent: refuses to run twice.
"""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
src = APP.read_text()

if "_pm_recent_build_guard" in src:
    raise SystemExit("already applied")
if "_pm_clarify_answer_merge" not in src:
    raise SystemExit("clarify-answer patch missing; apply it first")


def splice(old, new, desc, count=1):
    global src
    n = src.count(old)
    if n != count:
        raise SystemExit(f"[{desc}] anchor found {n}x, wanted {count}")
    src = src.replace(old, new)
    print(f"[ok] {desc}")


# ------------------------------------------------------------------
# 1a. Recent-build guard helper, module level above the approve route.
# ------------------------------------------------------------------
ROUTE_DEC = ("@app.route('/api/brief-chat/approve', methods=['POST'])\n"
             "@app.route('/api/synth-chat/approve', methods=['POST'])"
             "  # legacy alias")

HELPER = '''_PM_RECENT_BUILDS_KEY = 'system/usage/recent_builds.json'


def _pm_recent_build_guard(username, subject, ws='', we=''):
    """Same user re-approving the same subject within 30 minutes is a
    duplicate, not a second order (smclain, Trinity Tatum x2,
    2026-09-29: a 6-day window drift ran two full builds). Returns the
    earlier entry when this enqueue should be blocked, else records
    this one and returns None. A window that moved more than 21 days
    on either end is a correction and is allowed through. Fail-open:
    any storage trouble means no block."""
    try:
        norm = re.sub(r'[^a-z0-9]+', ' ',
                      str(subject or '').lower()).strip()
        username = str(username or '').strip()
        if not norm or not username:
            return None
        now = time.time()
        try:
            _r = s3_client.get_object(Bucket=S3_BUCKET,
                                      Key=_PM_RECENT_BUILDS_KEY)
            doc = json.loads(_r['Body'].read().decode('utf-8'))
        except Exception:
            doc = {}
        entries = [e for e in (doc.get('entries') or [])
                   if isinstance(e, dict)
                   and now - float(e.get('t') or 0) < 86400]

        def _d(s):
            try:
                return datetime.strptime(str(s)[:10], '%Y-%m-%d')
            except Exception:
                return None

        hit = None
        for e in entries:
            if e.get('u') != username or e.get('s') != norm:
                continue
            if now - float(e.get('t') or 0) > 1800:
                continue
            ws0, we0 = _d(e.get('ws')), _d(e.get('we'))
            ws1, we1 = _d(ws), _d(we)
            if ws0 and we0 and ws1 and we1:
                drift = max(abs((ws1 - ws0).days),
                            abs((we1 - we0).days))
                if drift > 21:
                    continue
            hit = e
            break
        if hit is None:
            entries.append({'u': username, 's': norm,
                            'ws': str(ws or '')[:10],
                            'we': str(we or '')[:10], 't': now})
            try:
                s3_client.put_object(
                    Bucket=S3_BUCKET, Key=_PM_RECENT_BUILDS_KEY,
                    Body=json.dumps(
                        {'entries': entries[-200:]}).encode('utf-8'),
                    ContentType='application/json')
            except Exception:
                pass
        return hit
    except Exception:
        traceback.print_exc()
        return None


''' + ROUTE_DEC

splice(ROUTE_DEC, HELPER, "recent-build guard helper")

# ------------------------------------------------------------------
# 1b. Guard call between the quoted-estimate block and the queue post.
# ------------------------------------------------------------------
OLD_PRE_POST = """            _q_est = _estimated_audience_range(spec.get('subject_raw_tu'))
            if _q_est:
                payload['quoted_estimate'] = _q_est
    except Exception:
        pass

    try:
        import requests as _requests"""

NEW_PRE_POST = """            _q_est = _estimated_audience_range(spec.get('subject_raw_tu'))
            if _q_est:
                payload['quoted_estimate'] = _q_est
    except Exception:
        pass

    # A re-approve of the same subject minutes later is a duplicate,
    # not a second order (smclain, Trinity Tatum x2, 2026-09-29).
    # Blocked BEFORE the queue post, so nothing is charged. A window
    # that moved materially is a correction and still builds.
    if decision == 'new_build':
        _dup_dr = (draft.get('date_range')
                   if isinstance(draft.get('date_range'), dict) else {})
        _dup = _pm_recent_build_guard(
            _approve_username, spec.get('name'),
            ws=_dup_dr.get('start') or '', we=_dup_dr.get('end') or '')
        if _dup is not None:
            return jsonify({
                'success': False,
                'guidance': True,
                'error': (f"{spec.get('name', 'That profile')} is "
                          "already building from your request a few "
                          "minutes ago, so I did not start a second "
                          "copy or charge you again. It lands in "
                          "Select Profile when it finishes. Ask me "
                          "for a status update any time."),
            })

    try:
        import requests as _requests"""

splice(OLD_PRE_POST, NEW_PRE_POST, "duplicate guard before queue post")

# ------------------------------------------------------------------
# 2. Worded durations are relative windows in the interpret rule.
# ------------------------------------------------------------------
OLD_RULE = """        "request states a relative window ('trailing 60 days', 'last "
        "90 days', 'past 3 months'), you MUST compute the concrete \""""

NEW_RULE = """        "request states a relative window ('trailing 60 days', 'last "
        "90 days', 'past 3 months', 'over the last three years', "
        "'past two years' - worded durations count, and years convert "
        "to trailing months: three years = trailing 36 months), you "
        "MUST compute the concrete \""""

splice(OLD_RULE, NEW_RULE, "worded durations bind the window")

# ------------------------------------------------------------------
# 3a. The clarify-turn detector also covers 'Did you want this on'.
# ------------------------------------------------------------------
OLD_RX = '''_PM_CLARIFY_TURN_RE = re.compile(
    r"do you want this on |which audience should i use", re.I)'''

NEW_RX = '''_PM_CLARIFY_TURN_RE = re.compile(
    r"(?:do|did) you want this on |which audience should i use", re.I)'''

splice(OLD_RX, NEW_RX, "clarify detector covers Did-variant")

# ------------------------------------------------------------------
# 3b. Bare answers re-run the original question clean.
# ------------------------------------------------------------------
OLD_TAIL = '''    if not orig or orig.strip().lower() == t.lower():
        return ''
    return f"{orig}\\n\\nAudience: {t}"'''

NEW_TAIL = '''    if not orig or orig.strip().lower() == t.lower():
        return ''
    # A bare yes / no / something-else / open-on-screen answer (typos
    # included: scott, "open on sceren", 2026-09-29) re-runs the
    # original question clean - gluing "Audience: no" onto it would
    # read as an audience named no.
    tl = t.lower().strip(' .!?')
    if tl in ('yes', 'yep', 'yeah', 'correct', 'sure', 'no', 'nope',
              'neither', 'something else', 'not that', 'the screen',
              'on screen', 'whats open', "what's open",
              'use the screen') or tl.startswith('open on'):
        return orig
    return f"{orig}\\n\\nAudience: {t}"'''

splice(OLD_TAIL, NEW_TAIL, "bare answers re-run the question")

APP.write_text(src)
print(f"[done] {APP} patched ({len(src):,} bytes)")
