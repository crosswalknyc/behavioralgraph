#!/usr/bin/env python3
"""Every data answer creates its CSV; downloadable and emailable.

Jenna 2026-09-29: "every response that involves data should create a
.csv file with the data that the user can download and have emailed
to them if they want."

- The generate pass builds the CSV on every data answer (not just
  explicit file asks). The download anchor rides the reply turn; an
  explicit file ask still auto-saves to the browser.
- Ledger replays build the CSV too.
- Every handed file lands in a per-account stash; a short "email me
  this file" message (or the chip) sends it as an attachment from
  Prometheus to the account's email, or to an address typed in the
  message. Jenna rides every send.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
src = APP.read_text()


def sp(old, new, desc):
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"[fail] {desc}: anchor x{n}")
    src = src.replace(old, new)
    print(f"[ok] {desc}")


# ------------------------------------------------------------------
# 1. Helpers: stash, replay file payload, email intent + response.
# ------------------------------------------------------------------
HELPERS = '''_PM_LAST_FILE_PREFIX = 'system/usage/pm_last_file/'


def _pm_file_stash_write(username, url, filename, s3_key,
                         subject='', question=''):
    """Remember the most recent file handed to this account so
    "Email me this file" can serve it (2026-09-29 Jenna: every data
    answer creates a CSV the user can download or have emailed)."""
    try:
        uname = str(username or '').strip().lower()
        if not uname:
            return
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=f"{_PM_LAST_FILE_PREFIX}{uname}.json",
            Body=json.dumps({
                'url': url, 'filename': filename, 's3_key': s3_key,
                'subject': str(subject or '')[:120],
                'question': str(question or '')[:300],
                'ts': time.time()}).encode('utf-8'),
            ContentType='application/json')
    except Exception:
        traceback.print_exc()


def _pm_file_stash_read(username):
    try:
        uname = str(username or '').strip().lower()
        if not uname:
            return {}
        raw = s3_client.get_object(
            Bucket=S3_BUCKET,
            Key=f"{_PM_LAST_FILE_PREFIX}{uname}.json")['Body'].read()
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _pm_answer_file_payload(entry, auto_save=False, username='',
                            question=''):
    """Build, upload, and stash the CSV for a data answer (the replay
    path; the generate pass builds inline). Returns file_link always,
    plus download_url / filename when the ask explicitly requested a
    file. Failures return {} and never block the answer."""
    import prometheus_analysis as pma
    try:
        fname, csv_text = pma.build_generated_csv(entry)
        rng = ''
        if entry.get('ws') and entry.get('we'):
            rng = (f"{_fmt_study_date(entry['ws'])} - "
                   f"{_fmt_study_date(entry['we'])}")
        elif entry.get('wl'):
            rng = str(entry['wl'])
        csv_text = _stamp_csv_text(csv_text, rng)
        fname = _pm_csv_task_filename(entry) or fname
        s3_key = f"{_PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key,
                             Body=csv_text.encode('utf-8'),
                             ContentType='text/csv')
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key,
                    'ResponseContentDisposition':
                        f'attachment; filename="{fname}"'},
            ExpiresIn=7 * 24 * 3600)
    except Exception:
        traceback.print_exc()
        return {}
    _pm_file_stash_write(username, url, fname, s3_key,
                         subject=entry.get('subject'),
                         question=question or entry.get('question'))
    payload = {'file_link': {'url': url, 'label': f"Download {fname}"}}
    if auto_save:
        payload.update({'download_url': url, 'filename': fname})
    return payload


_PM_EMAIL_FILE_RE = re.compile(
    r"\\b(?:e-?mail|mail)\\b[^.?!\\n]{0,50}"
    r"\\b(?:csv|file|spreadsheet|data|it|this|that)\\b"
    r"|\\b(?:send|shoot)\\b[^.?!\\n]{0,30}\\b(?:e-?mail|inbox)\\b",
    re.I)

_PM_EMAIL_ADDR_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")


def _pm_email_file_intent(text):
    """True when a short message asks to email the file just handed
    over. Long asks that mention email while requesting new data flow
    to the normal path."""
    t = str(text or '').strip()
    if not t or len(t) > 90:
        return False
    if t.lower().strip(' .!') == 'email me this file':
        return True
    return bool(_PM_EMAIL_FILE_RE.search(t))


def _pm_email_file_response(user, text):
    """Email the most recent file handed to this account (2026-09-29
    Jenna). Sent by Prometheus; an address typed in the message wins,
    otherwise the account email on file."""
    _pm_ask_hint(route='email_file')
    uname = (session.get('username') or user.get('username')
             or '').strip()
    stash = _pm_file_stash_read(uname)
    if not stash.get('s3_key'):
        _pm_ask_hint(outcome='no_file')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('I have not handed you a file yet. Ask me a '
                      'data question first - every answer with data '
                      'comes with its CSV - then say Email me this '
                      'file.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    m = _PM_EMAIL_ADDR_RE.search(str(text or ''))
    addr = m.group(0) if m else ''
    if not addr:
        addr = str(user.get('email') or '').strip()
    if not addr:
        try:
            _udoc = json.loads(s3_client.get_object(
                Bucket=S3_BUCKET, Key=S3_USERS_KEY)['Body'].read())
            _urec = (_udoc.get('users') or _udoc or {}).get(uname) or {}
            addr = str(_urec.get('email') or '').strip()
        except Exception:
            addr = ''
    if not addr:
        _pm_ask_hint(outcome='no_email')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('There is no email on file for your account. '
                      'Tell me the address to use (for example: email '
                      'it to name@company.com) and I will send it '
                      'right over.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    fname = str(stash.get('filename') or 'data.csv')
    try:
        _fbytes = s3_client.get_object(
            Bucket=S3_BUCKET, Key=stash['s3_key'])['Body'].read()
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        msg = MIMEMultipart('mixed')
        msg['Subject'] = (fname.rsplit('.', 1)[0].replace('_', ' ')
                          or 'Your data from Prometheus')
        msg['From'] = 'Prometheus <prometheus@crosswalknyc.com>'
        msg['To'] = addr
        msg['Reply-To'] = 'jenna@crosswalknyc.com'
        _sub = str(stash.get('subject') or '').strip()
        _bl = (f"The data you asked for"
               f"{' on ' + _sub if _sub else ''} is attached.\\n\\n"
               "Prometheus\\nCrosswalk")
        msg.attach(MIMEText(_bl, 'plain'))
        part = MIMEApplication(_fbytes, _subtype='csv')
        part.add_header('Content-Disposition', 'attachment',
                        filename=fname)
        msg.attach(part)
        boto3.client('ses', region_name='us-east-2').send_raw_email(
            Source='prometheus@crosswalknyc.com',
            Destinations=[addr, 'jenna@crosswalknyc.com'],
            RawMessage={'Data': msg.as_string()})
    except Exception as e:
        traceback.print_exc()
        _chatbot_error_email('brief-chat/analyze', e)
        _pm_ask_hint(outcome='error')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('The email did not go through just now. The '
                      'download link on the reply still works, and '
                      'you can ask me to email it again in a minute.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    _pm_ask_hint(outcome='answered', subject=stash.get('subject'))
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': f"Sent. {fname} is on its way to {addr}.",
        'followups': [], 'offer_deck': False, 'deck_angle': None})


def _pm_csv_point(subject, question, family):'''

sp("def _pm_csv_point(subject, question, family):", HELPERS,
   "file stash + email helpers")

# ------------------------------------------------------------------
# 2. Email intercept in the analyze route, before any routing.
# ------------------------------------------------------------------
sp("""    # Pay-as-you-go attribution (None for subscribed users): rides
    # every model call this request makes, and switches billing from
    # credits to per-session dollar usage.
    _pm_ppu = _pm_usage_extras(user)""",
   """    # Pay-as-you-go attribution (None for subscribed users): rides
    # every model call this request makes, and switches billing from
    # credits to per-session dollar usage.
    _pm_ppu = _pm_usage_extras(user)
    # Email the last delivered file (2026-09-29 Jenna: every data
    # answer creates a CSV the user can download or have emailed).
    # Runs before routing so an open profile never hijacks it.
    if _pm_email_file_intent(text):
        return _pm_email_file_response(user, text)""",
   "email intercept in the analyze route")

# ------------------------------------------------------------------
# 3. Generate pass: retire the download chip (the file itself rides
#    the answer now); email chip lands after persist.
# ------------------------------------------------------------------
sp("""    if pma.CSV_OFFER_CHIP not in followups:
        followups.append(pma.CSV_OFFER_CHIP)
    # The answer opens by naming the audience it used (2026-09-29""",
   """    # The CSV rides the answer itself (2026-09-29 Jenna), so the
    # download chip is retired here; the email chip lands after
    # persist so it is never stored on the ledger entry.
    followups = [f for f in followups if f != pma.CSV_OFFER_CHIP]
    # The answer opens by naming the audience it used (2026-09-29""",
   "generate pass retires the download chip")

# ------------------------------------------------------------------
# 4. Generate pass file block: build on every data answer.
# ------------------------------------------------------------------
sp('''    # The ask requested a file (2026-09-29, Casey Pearson: "Provide
    # output as a csv" produced no file). Build the CSV from the same
    # numbers the reply shipped with, upload it, and hand back
    # download_url so the browser saves it automatically. The ledger
    # reply stays clean; the chip still serves repeats.''',
   '''    # Every answer with data creates its CSV (2026-09-29 Jenna). The
    # download anchor rides the reply turn on every data answer; an
    # explicit file ask also auto-saves to the browser; the stash
    # serves "Email me this file".''',
   "file block comment")

sp("""        if _PM_FILE_ASK_RE.search(str(text or '')) and (
                res.get('breakdown') or res.get('metrics')):""",
   """        if res.get('breakdown') or res.get('metrics'):
            _explicit = bool(_PM_FILE_ASK_RE.search(str(text or '')))""",
   "file block builds on every data answer")

sp('''            _file_payload = {'download_url': _furl, 'filename': _fn}
            reply += (f"\\n\\n{_fn} is saving to your browser "
                      "downloads now.")''',
   '''            _file_payload = {'file_link': {
                'url': _furl, 'label': f"Download {_fn}"}}
            if _explicit:
                _file_payload.update(
                    {'download_url': _furl, 'filename': _fn})
                reply += (f"\\n\\n{_fn} is saving to your browser "
                          "downloads now.")
            _pm_file_stash_write(pm_user, _furl, _fn, _fkey,
                                 subject=res.get('subject'),
                                 question=text)
            if 'Email me this file' not in followups:
                followups.append('Email me this file')''',
   "file payload: link always, auto-save on explicit ask")

# ------------------------------------------------------------------
# 5. Ledger replays carry their CSV too.
# ------------------------------------------------------------------
sp("""        _replay_chips = list(_led_exact.get('followups') or [])[:3]
        if pma.CSV_OFFER_CHIP not in _replay_chips:
            _replay_chips.append(pma.CSV_OFFER_CHIP)
        _pm_csv_point(_replay_subj, text, _led_exact.get('family'))
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _led_exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': _replay_subj})""",
   """        _replay_chips = [c for c in
                         list(_led_exact.get('followups') or [])[:3]
                         if c != pma.CSV_OFFER_CHIP]
        _pm_csv_point(_replay_subj, text, _led_exact.get('family'))
        # Replays carry their CSV too (2026-09-29 Jenna: every answer
        # with data creates the file).
        _rp_file = {}
        try:
            if _led_exact.get('breakdown') or _led_exact.get('metrics'):
                _rp_file = _pm_answer_file_payload(
                    _led_exact,
                    auto_save=bool(
                        _PM_FILE_ASK_RE.search(str(text or ''))),
                    username=_pm_user, question=text)
                if _rp_file and 'Email me this file' \\
                        not in _replay_chips:
                    _replay_chips.append('Email me this file')
        except Exception:
            traceback.print_exc()
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _led_exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': _replay_subj,
            **_rp_file})""",
   "replays build the CSV")

# ------------------------------------------------------------------
# 6. The typed-download path stashes its file for the email flow.
# ------------------------------------------------------------------
sp("""    _pm_ask_hint(outcome='answered', subject=entry.get('subject'))
    reply = f"Saved. {fname} is in your browser downloads."
""",
   """    _pm_file_stash_write(
        (session.get('username') or user.get('username') or '').strip(),
        url, fname, s3_key, subject=entry.get('subject'),
        question=text)
    _pm_ask_hint(outcome='answered', subject=entry.get('subject'))
    reply = f"Saved. {fname} is in your browser downloads."
""",
   "typed-download path stashes the file")

ast.parse(src)
APP.write_text(src)
print(f"[done] {APP} patched ({len(src):,} bytes)")
