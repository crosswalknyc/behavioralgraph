#!/usr/bin/env python3
"""app.py side of the wallet queries + dollars change (2026-09-28):
dollar enrichment on /api/credit-usage and the /api/me/prometheus-
queries endpoint. Replayable string patch so it can apply on a clean
worktree without sweeping sibling work."""
import sys
from pathlib import Path

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")

OLD_RETURN = """    return jsonify({
        'success': True,
        'usage': history,
        'credits_used': user.get('credits_used', 0),
        'credits_left': credits_left,
        'wallet_balance_usd': snap['wallet_balance_usd'],
        'paying_customer': snap['paying_customer'],
        'billed_via_company': snap['billed_via_company'],
        'company_name': snap['company_name'],
        'can_export_company': can_export_company,
    })"""

NEW_RETURN = """    # Dollars, not credits (2026-09-28 Jenna: "have the credits say a
    # dollar amount"). Wallet-era rows carry amount_usd; legacy
    # credit rows convert at the standing $60/credit (a 5-credit
    # profile is the $300 sheet price).
    usage_out = []
    spend_usd = 0.0
    for row in history:
        r = dict(row)
        usd = r.get('amount_usd')
        if usd is None:
            try:
                usd = float(r.get('credits_used', 1) or 0) * 60.0
            except Exception:
                usd = 0.0
        r['usd'] = round(float(usd), 2)
        spend_usd += r['usd']
        usage_out.append(r)
    return jsonify({
        'success': True,
        'usage': usage_out,
        'credits_used': user.get('credits_used', 0),
        'spend_usd_total': round(spend_usd, 2),
        'credits_left': credits_left,
        'wallet_balance_usd': snap['wallet_balance_usd'],
        'paying_customer': snap['paying_customer'],
        'billed_via_company': snap['billed_via_company'],
        'company_name': snap['company_name'],
        'can_export_company': can_export_company,
    })


@app.route('/api/me/prometheus-queries')
@requires_auth
def api_me_prometheus_queries():
    \"\"\"The caller's Prometheus question history, newest first
    (2026-09-28 Jenna: the wallet modal lists every question under
    'Prometheus Queries', searchable). Questions come from the saved
    chat thread's user turns; chip echoes ride along - they were sent
    as questions.\"\"\"
    uname = session.get('username') or ''
    if not uname:
        return jsonify({'success': False, 'error': 'Not logged in'})
    try:
        hist = _load_synth_chat_history(uname) or []
    except Exception:
        hist = []
    out = []
    for t in reversed(hist):
        if (t.get('role') or '') != 'user':
            continue
        q = str(t.get('text') or '').strip()
        if not q:
            continue
        out.append({'q': q[:400], 'ts': str(t.get('ts') or '')})
        if len(out) >= 300:
            break
    return jsonify({'success': True, 'queries': out})"""

src = APP.read_text(encoding="utf-8")
if "api_me_prometheus_queries" in src:
    print("already applied")
    sys.exit(0)
count = src.count(OLD_RETURN)
if count != 1:
    raise RuntimeError(f"anchor found {count}x")
APP.write_text(src.replace(OLD_RETURN, NEW_RETURN), encoding="utf-8")
print("app.py patched")
