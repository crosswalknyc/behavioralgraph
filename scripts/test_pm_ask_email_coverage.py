#!/usr/bin/env python3
"""Every Prometheus question emails Jenna with the reply (2026-09-30).

Jenna: "make sure they are all being emailed to me with the replies so
I can troubleshoot in real time." Guards the coverage of the per-ask
question email:

1. Both chat surfaces are wrapped by the ask logger.
2. The ask logger emails the question even when the route dies
   mid-request (record + notify, then re-raise to the calm guard).
3. The finished-read path emails the real answer for queued reads.
4. Only the 'On it.' placeholder is skipped; the working-on-it promise
   line still emails so a failure is visible in real time.
5. The note goes to Jenna, from Prometheus.

Hermetic: source-pattern checks plus direct imports of the standalone
notify module. No network, no Flask app boot.
"""
import os
import re
import sys
# Legacy-move shim (2026-10-01): the Prometheus code lives in
# bg-webapp/prometheus/legacy/chat.py; read app.py + legacy as one source.
import os as _pm_os, sys as _pm_sys
_pm_r = _pm_os.path.dirname(_pm_os.path.abspath(__file__))
while not _pm_os.path.exists(_pm_os.path.join(_pm_r, 'bg-webapp', 'app.py')):
    _pm_r = _pm_os.path.dirname(_pm_r)
_pm_sys.path.insert(0, _pm_os.path.join(_pm_r, 'scripts'))
from _pm_test_source import app_path as _pm_app_path, host_for as _pm_host_for  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

APP = open(str(_pm_app_path()), encoding='utf-8').read()

FAILURES = []


def check(name, ok, detail=''):
    tag = 'PASS' if ok else 'FAIL'
    print(f"[{tag}] {name}" + (f"  ({detail})" if detail and not ok else ''))
    if not ok:
        FAILURES.append(name)


# 1. Both surfaces wrapped by the ask logger.
check("interpret route carries @_ask_logged('interpret')",
      "@_ask_logged('interpret')" in APP)
check("analyze route carries @_ask_logged('analyze')",
      "@_ask_logged('analyze')" in APP)

# 2. Raise-path coverage inside _ask_logged: the wrapper records and
#    emails the ask before re-raising to the calm guard.
m = re.search(r"def _ask_logged\(surface\):(.*?)\n    return deco", APP,
              re.DOTALL)
check("_ask_logged body found", bool(m))
body = m.group(1) if m else ''
check("route exception still records the ask",
      "except Exception:" in body and "outcome='error'" in body)
check("route exception still emails the question",
      "_pm_watch_notify(" in body
      and "_CHATBOT_CALM_MESSAGE" in body)
check("route exception re-raises to the calm guard",
      re.search(r"_CHATBOT_CALM_MESSAGE\}, None\).*?\n\s+raise\n",
                body, re.DOTALL) is not None)
check("happy path still emails question + reply",
      body.count("_pm_watch_notify(") >= 2)

# 3. Finished queued reads email the real answer.
check("finished-read path calls _pm_watch_notify",
      re.search(r"_pm_append_read_to_history\(pm_user, job_id, payload\)"
                r"\s*\n\s*_pm_watch_notify\(pm_user, text, payload",
                APP) is not None)

# 4. Placeholder gating: only 'On it.' is skipped. The calm
#    working-on-it promise must still email.
import prometheus_watch_notify as pwn

check("'On it.' placeholder is skipped (finished read emails later)",
      pwn.answer_text({'reply': 'On it. Building the read now.'}) == '')
calm = ("Working on it. Confirmed your task is in progress and I will "
        "email you when it completes.")
check("working-on-it promise line still emails",
      pwn.answer_text({'reply': calm}) == calm)
check("no placeholder prefix swallows the promise line",
      not calm.startswith(pwn._PLACEHOLDER_PREFIXES))
check("plain answers pass through",
      pwn.answer_text({'reply': 'Obsession reached 19,847,331 viewers.'})
      .startswith('Obsession reached'))

# 4b. Guidance shape (2026-10-01, cbisson's 14-title ask): 'guidance'
#     is a boolean FLAG and the words ride in 'error'. The email must
#     carry the words, never str(True), and the guidance-wrapped ack
#     (text in 'error', read_job_id attached) must skip entirely -
#     the finished job emails the real answer.
_guid_ack = {'success': False, 'guidance': True, 'analysis_read': True,
             'error': ('On it. This one takes a real look at the data, '
                       'so give me a moment.'),
             'read_job_id': 'abc123def456'}
check("guidance-wrapped ack is skipped (job emails the real answer)",
      pwn.is_placeholder(_guid_ack))
_guid_answer = {'success': False, 'guidance': True,
                'error': 'Nip/Tuck reaches 1,482,113 US viewers.'}
check("guidance answer emails the words in 'error'",
      pwn.answer_text(_guid_answer)
      == 'Nip/Tuck reaches 1,482,113 US viewers.')
check("boolean guidance flag never emails as 'True'",
      pwn.answer_text(_guid_answer) != 'True'
      and pwn.answer_text({'success': False, 'guidance': True})
      == 'Could not answer.')
check("guidance ack without a job id still skips on the error text",
      pwn.is_placeholder({'success': False, 'guidance': True,
                          'error': 'On it. Working through the data.'})
      and pwn.answer_text({'success': False, 'guidance': True,
                           'error': 'On it. Working through the data.'})
      == '')
check("dedupe ack ('Already on it') is skipped",
      pwn.is_placeholder({'success': True,
                          'reply': 'Already on it - that exact read '
                                   'is running now.'}))
check("finished read payload (no job id) still emails",
      not pwn.is_placeholder({'success': True, 'action': 'answer',
                              'reply': 'The 14 titles pull 14,318,627 '
                                       'unique viewers.'}))
check("boolean reply never emails as 'True'",
      pwn.answer_text({'success': True, 'reply': True}) == '')

# 5. Addressing: to Jenna, from Prometheus.
check("note goes to Jenna", pwn._TO == 'jenna@crosswalknyc.com')
check("note comes from Prometheus",
      pwn._FROM.startswith('Prometheus <prometheus@'))

# 6. Every account sends (the label helper never returns '' for a
#    real record, and unknown accounts still label as Dashboard).
check("unknown account still labels (every account sends)",
      pwn.watched_label('', '') == 'Dashboard')
check("crosswalk account labels by company",
      pwn.watched_label('emma@crosswalknyc.com', 'Crosswalk')
      == 'Crosswalk')

# 7. Read output email always carries the raw data as a CSV (Jenna
#    2026-10-01: Carolyn's email had the PDF but not the raw data;
#    "should also always have a .csv file"). Built from the same
#    ledger entry as the download chip so file and chat match.
m_out = re.search(r"def _pm_send_output_email\(kind, to_email, data\):"
                  r"(.*?)\n    def _send\(\):", APP, re.DOTALL)
check("_pm_send_output_email body found", bool(m_out))
out_body = m_out.group(1) if m_out else ''
check("read email builds the CSV from the banked ledger entry",
      "_il.consult(" in out_body
      and "_pma.build_generated_csv(" in out_body)
check("csv build is fail-safe (email still ships without it)",
      "csv_bytes, csv_name = b'', ''" in out_body)
check("body copy names the CSV when attached",
      "attached \"\n                            \"as a CSV" in out_body
      or "attached as \"\n" in out_body
      or "as a CSV" in out_body)
m_send = re.search(r"def _pm_send_output_email.*?def _send\(\):(.*?)"
                   r"\n    threading\.Thread", APP, re.DOTALL)
check("send attaches the CSV alongside the PDF",
      m_send is not None and "_subtype='csv'" in m_send.group(1)
      and "filename=csv_name" in m_send.group(1))
check("finished-read flush passes the question for the CSV lookup",
      re.search(r"_pm_flush_notify\(job_id, 'read',\s*\n\s*"
                r"\{\*\*payload, 'question': text\}\)", APP) is not None)
check("already-finished notify send passes the question too",
      "'question': str(status.get('question') or '')}" in APP)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
# Jenna 2026-09-30: bcc liz on all prometheus questions.
# Jenna 2026-10-07: jessie too, on every Prometheus query.
_wn = open(os.path.join(ROOT, 'prometheus_watch_notify.py'),
           encoding='utf-8').read()
check("question emails BCC Liz and Jessie",
      "_BCC = ('liz@crosswalknyc.com', 'jessie@crosswalknyc.com')" in _wn
      and "Destinations=[_TO, *_BCC]" in _wn)
check("Liz and Jessie ride as BCC only, never in the To header",
      "msg['To'] = _TO" in _wn and "msg['To'] = _BCC" not in _wn
      and "msg['Bcc']" not in _wn)

print("ALL CHECKS PASSED")
