"""Files in the chat: the data file name and payload, the replayed file, the email-the-file intent and response, the CSV points and uploads, the page file, the last thing delivered, the CSV download, and the search demand response.

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
import uuid
import json
import traceback
import re
from prometheus import faults as _faults
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response

# <<moved>>
# <<block: 1>>
_PM_DATA_FILE_PREFIX = 'generated_data/'


# <<block: 2>>
def _pm_csv_task_filename(entry):
    """Filename for the CSV export (2026-08-28 Jenna): name the file
    after the title of the task on screen (the active view's label,
    e.g. 'Digital Journey IQ') when the ask came from a titled data
    view; otherwise a short subject-based name. The browser saves it
    like any regular download; the chat never shows the raw link."""
    title = ''
    try:
        body = request.get_json(silent=True) or {}
        vc = (body.get('page_context') or {}).get('view_context') or {}
        title = str(vc.get('view_title') or '').strip()
    except Exception:
        title = ''
    if not title:
        try:
            subject = str(entry.get('subject') or '').strip()
            bd = entry.get('breakdown') \
                if isinstance(entry.get('breakdown'), dict) else {}
            dim = str((bd or {}).get('dimension') or '').strip()
            title = ' '.join(p for p in (subject, dim) if p)
        except Exception:
            title = ''
    try:
        import prometheus_analysis as _pma_fn
        title = _pma_fn.scrub_user_text(title) or title
    except Exception as _fx:
        _faults.note('chat', _fx)
    title = re.sub(r'[\\/:*?"<>|]+', ' ', title or '')
    title = re.sub(r'\s+', ' ', title).strip()
    if len(title) > 80:   # whole words, no half-open parenthetical (2026-11-11: '... Faith, Yo.csv')
        try:
            title = _pma_fn._clip_label(title, 80)
        except Exception:
            title = title[:80]
    title = title.strip(' .') or 'Data Export'
    return f"{title}.csv"


# <<block: 3>>
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
            rng = (f"{_H._fmt_study_date(entry['ws'])} - "
                   f"{_H._fmt_study_date(entry['we'])}")
        elif entry.get('wl'):
            rng = str(entry['wl'])
        csv_text = _H._stamp_csv_text(csv_text, rng)
        fname = _C._pm_csv_task_filename(entry) or fname
        s3_key = f"{_C._PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
        _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=s3_key,
                             Body=csv_text.encode('utf-8'),
                             ContentType='text/csv')
        url = _H.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                    'ResponseContentDisposition':
                        _file_names.disposition(fname)},
            ExpiresIn=7 * 24 * 3600)
    except Exception:
        traceback.print_exc()
        return {}
    _C._pm_file_stash_write(username, url, fname, s3_key,
                         subject=entry.get('subject'),
                         question=question or entry.get('question'))
    payload = {'file_link': {'url': url, 'label': f"Download {fname}", 'name': fname, 'key': s3_key}}
    if auto_save:
        payload.update({'download_url': url, 'filename': fname})
    return payload


# <<block: 4>>
def _pm_replay_file(entry, text, username, chips):
    """The CSV for a read served from the library rides the answer and
    the Email chip joins the followups (2026-09-29 rule; Bria 2026-11-04:
    the download chip these paths offered had no file behind it). {} when
    the entry has no table or the upload fails; never blocks the reply."""
    try:
        if not (isinstance(entry, dict) and (entry.get('breakdown') or entry.get('metrics'))):
            return {}
        payload = _C._pm_answer_file_payload(
            entry, auto_save=bool(_C._PM_FILE_ASK_RE.search(str(text or ''))),
            username=username, question=text)
        if payload and isinstance(chips, list) and 'Email me this file' not in chips:
            chips.append('Email me this file')
        return payload or {}
    except Exception:
        traceback.print_exc()
        return {}


# <<block: 5>>
_PM_EMAIL_FILE_RE = re.compile(
    r"\b(?:e-?mail|mail)\b[^.?!\n]{0,50}"
    r"\b(?:csv|file|spreadsheet|data|it|this|that)\b"
    r"|\b(?:send|shoot)\b[^.?!\n]{0,30}\b(?:e-?mail|inbox)\b",
    re.I)


# <<block: 6>>
def _pm_email_file_intent(text):
    """True when a short message asks to email the file just handed
    over. Long asks that mention email while requesting new data flow
    to the normal path."""
    t = str(text or '').strip()
    if not t or len(t) > 90:
        return False
    if t.lower().strip(' .!') == 'email me this file':
        return True
    return bool(_C._PM_EMAIL_FILE_RE.search(t))


# <<block: 7>>
_pm_email_file_intent = __import__('prometheus.insight_ask', fromlist=['not_a_send']).not_a_send(_pm_email_file_intent)   # "an insight we can email to Lego" is what the reader will do with the answer, never an order to send the last file (Jenna 2026-11-19)


# <<block: 8>>
def _pm_email_file_response(user, text, history=None):
    """Email the very last thing in the thread (Jenna 2026-11-04). A
    file the last reply carried goes as it is; a read with a table is
    exported from the stored entry it shipped from and attached with
    the read in the body; a reply with no table goes as the body. Sent
    by Prometheus; an address typed in the message wins, otherwise the
    account email on file."""
    from prometheus import last_thing as _lt
    _C._pm_ask_hint(route='email_file')
    uname = (session.get('username') or user.get('username')
             or '').strip()
    last = _C._pm_last_thing(user, history)
    if last['kind'] == 'none':
        pf = _C._pm_page_file()
        if pf:
            last.update(kind='file', file=pf, subject=pf.get('label') or '', why='the profile open on the page')
    if last['kind'] == 'none':
        _C._pm_ask_hint(outcome='no_file')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Nothing has landed in this chat yet for me to send. '
                      'Ask me a data question first and then say Email me this file.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    m = _C._PM_EMAIL_ADDR_RE.search(str(text or ''))
    addr = m.group(0) if m else ''
    if not addr:
        addr = str(user.get('email') or '').strip()
    if not addr:
        try:
            _udoc = json.loads(_H.s3_client.get_object(
                Bucket=_H.S3_BUCKET, Key=_H.S3_USERS_KEY)['Body'].read())
            _urec = (_udoc.get('users') or _udoc or {}).get(uname) or {}
            addr = str(_urec.get('email') or '').strip()
        except Exception:
            addr = ''
    if not addr:
        _C._pm_ask_hint(outcome='no_email')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('There is no email on file for your account. '
                      'Tell me the address to use (for example: email '
                      'it to name@company.com) and I will send it '
                      'right over.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    try:
        from prometheus import send_file as _sfm
        from prometheus import outbound_mail as _om
        _sub = str(last.get('subject') or '').strip()
        _ltxt = str(last.get('text') or '').strip()
        # the words of the read ride along, for a table or a file that a read handed over
        words = _sfm.mail_safe(_ltxt) if (last['kind'] in ('read', 'text') or len(_ltxt) > 120) else ''
        fname, fbytes, mime = '', None, 'text/csv'
        if last['kind'] == 'file':
            f = last['file']
            fname = str(f.get('filename') or 'file')
            mime = str(f.get('mime') or 'application/octet-stream')
            from prometheus import key_scope as _ks   # a link a turn or the page carries is checked for this reader first (2026-11-18)
            fbytes = _lt.file_bytes(f, _H.s3_client, bucket=_H.S3_BUCKET, extra_buckets=('svod-acquisition',), allowed=_ks.allowed_here(user, uname))
            if fbytes is None:   # nothing this reader can open sits behind that link: a plain no, never an alert
                return jsonify({'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                                'reply': 'I do not have a file to send for that. Open the profile you mean and ask again.'})
        elif last['kind'] == 'read':
            fname, csv_text = _C._pm_entry_csv(last['entry'], question=last.get('question'))
            fbytes = csv_text.encode('utf-8')
        if fbytes is not None:
            what = ('The file you asked for' if last['kind'] == 'file' else 'The data you asked for')
            _bl = f"{what}{' on ' + _sub if _sub else ''} is attached."
            if words:
                _bl += "\n\nThe read it came from:\n\n" + words
        else:
            _bl = (f"Here is the read you asked for{' on ' + _sub if _sub else ''}:\n\n" + words) if words \
                else f"Here is what I had for you{' on ' + _sub if _sub else ''}."
        _bl += "\n\nPrometheus\nCrosswalk"
        subj_line = (fname.rsplit('.', 1)[0].replace('_', ' ') if fname else (_sub or 'Your read from Prometheus'))
        kw = {}
        if fbytes is not None and mime == 'text/csv':
            kw = {'csv': fbytes, 'csv_name': fname}
        elif fbytes is not None:
            kw = {'files': [(fbytes, fname, mime)]}
        r = _om.send_user_email(
            to=addr, subject=subj_line, body=_bl, instructed=True,
            caller='csv-by-email', bcc_liz=False, **kw)
        if isinstance(r, dict) and r.get('sent') is False:
            raise RuntimeError(str(r.get('reason') or 'mail door closed'))
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _C._pm_ask_hint(outcome='error')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('The email did not go through just now. Ask me to '
                      'email it again in a minute.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    _C._pm_ask_hint(outcome='answered', subject=last.get('subject'))
    sent_what = fname if fname else 'the read'
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': f"Sent. {sent_what} is on its way to {addr}.",
        'followups': [], 'offer_deck': False, 'deck_angle': None})


# <<block: 9>>
def _pm_csv_point(subject, question, family):
    """Remember which ledger entry the CSV offer chip points at, so
    the download builds from the SAME entry the chat reply shipped
    from. Session-scoped; failures never block the reply."""
    try:
        session['pm_csv_last'] = {
            'subject': str(subject or '')[:120],
            'question': str(question or '')[:300],
            'family': str(family or '')[:32]}
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 10>>
def _pm_upload_csv(fname, csv_text):
    """Upload a CSV under generated_data/ and return (s3_key, 7-day url)."""
    fname = str(fname)   # a CSV's name is not deck copy: the door rule is the deck's alone (2026-11-11)
    fname = _file_names.safe_name(fname, 'read.csv')   # and it is one the link can carry (an emoji in it broke the link, 2026-11-16)
    s3_key = f"{_C._PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
    _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=s3_key,
                            Body=csv_text.encode('utf-8'),
                            ContentType='text/csv')
    url = _H.s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                'ResponseContentDisposition':
                    _file_names.disposition(fname)},
        ExpiresIn=7 * 24 * 3600)
    return s3_key, url


# <<block: 11>>
def _pm_entry_csv(entry, question=''):
    """(filename, stamped csv_text) for a stored read with a table."""
    import prometheus_analysis as pma
    fname, csv_text = pma.build_generated_csv(entry)
    _rng = ''
    try:
        if entry.get('ws') and entry.get('we'):
            _rng = (f"{_H._fmt_study_date(entry['ws'])} - "
                    f"{_H._fmt_study_date(entry['we'])}")
        elif entry.get('wl'):
            _rng = str(entry['wl'])
    except Exception:
        _rng = ''
    csv_text = _H._stamp_csv_text(csv_text, _rng)
    fname = _C._pm_csv_task_filename(entry) or fname
    return fname, csv_text


# <<block: 12>>
def _pm_page_file(bucket=''):
    """The profile open on the page, as a resolved file, or None."""
    try:
        body = request.get_json(silent=True) or {}
        prim = (body.get('page_context') or {}).get('primary') or {}
        key = str(prim.get('s3_key') or '').strip()
        if not key:
            return None
        fname = key.rsplit('/', 1)[-1]
        return {'url': '', 's3_key': key, 'filename': fname, 'bucket': bucket or _H.S3_BUCKET,
                'same_origin': False, 'ext': 'csv', 'mime': 'text/csv',
                'label': str(prim.get('name') or fname), 'page': True}
    except Exception:
        return None


# <<block: 13>>
def _pm_last_thing(user, history):
    """The very last thing Prometheus handed over in this thread (Jenna
    2026-11-04: "take the very last thing that happened in chat and
    that's what they want emailed, downloaded, converted"). The thread
    first; the account's last file and the session pointer only when
    the thread holds nothing."""
    from prometheus import last_thing as _lt
    import insights_ledger as il
    uname = (session.get('username') or user.get('username') or '').strip()
    stash = _C._pm_file_stash_read(uname)
    last = _lt.resolve(history, consult=il.consult, stash=stash, bucket=_H.S3_BUCKET)
    if last['kind'] != 'none':
        return last
    if any(str((t or {}).get('role') or '') != 'user' for t in (history or []) if isinstance(t, dict)):
        return last   # a thread with nothing delivered yet answers as such; a file from another day is not "this"
    # no thread in hand at all (an API caller): the session's pointer to
    # the read the last reply shipped from, when it has a table; a
    # text-only twin never counts
    try:
        ptr = session.get('pm_csv_last') or {}
    except Exception:
        ptr = {}
    if ptr.get('subject') and ptr.get('question'):
        try:
            led = il.consult(subject=ptr['subject'], question=ptr['question'])
            for e in [led.get('exact')] + list(reversed(led.get('entries') or [])):
                if _lt.has_data(e):
                    last.update(kind='read', entry=e, subject=str(e.get('subject') or ''),
                                question=str(ptr.get('question') or ''), why='the session pointer')
                    return last
        except Exception:
            traceback.print_exc()
    if stash.get('s3_key'):
        fname = str(stash.get('filename') or stash['s3_key'].rsplit('/', 1)[-1])
        last.update(kind='file', subject=str(stash.get('subject') or ''),
                    question=str(stash.get('question') or ''), why='the account file',
                    file={'url': str(stash.get('url') or ''), 's3_key': stash['s3_key'], 'filename': fname,
                          'bucket': _H.S3_BUCKET, 'same_origin': False,
                          'ext': fname.rsplit('.', 1)[-1].lower() if '.' in fname else '', 'mime': 'text/csv'})
    return last


# <<block: 14>>
def _pm_csv_download_response(user, text, history=None):
    """Hand over the very last thing in the thread as a download (Jenna
    2026-11-04). A file the last reply carried is re-linked as it is; a
    read with a table behind it is exported from the SAME stored entry
    the reply shipped from, so file and chat numbers match exactly; a
    reply with no table is turned into a table of its figures. Nothing
    in the thread yet: the profile open on the page. No charge: handing
    over what was already delivered is a formatting step."""
    from prometheus import last_thing as _lt
    _C._pm_ask_hint(route='csv_download')
    uname = (session.get('username') or user.get('username') or '').strip()
    last = _C._pm_last_thing(user, history)
    if last['kind'] == 'none':
        pf = _C._pm_page_file()
        if pf:
            last.update(kind='file', file=pf, subject=pf.get('label') or '', why='the profile open on the page')
    if last['kind'] == 'none':
        _C._pm_ask_hint(outcome='declined_no_csv_source')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Nothing has landed in this chat yet for me to hand over. '
                      'Ask me for the data and the file will ride the answer.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    try:
        if last['kind'] == 'file':
            # the file as it is, on a link that opens now (a week-old
            # presigned link is re-signed; a same-origin link stays)
            f = last['file']
            fname = str(f.get('filename') or 'file')
            from prometheus import key_scope as _ks   # only a file this reader can open is signed (2026-11-18)
            url = _lt.fresh_url(f, _H.s3_client, bucket=_H.S3_BUCKET, allowed=_ks.allowed_here(user, uname))
            s3_key = str(f.get('s3_key') or '')
        elif last['kind'] == 'read':
            fname, csv_text = _C._pm_entry_csv(last['entry'], question=last.get('question'))
            s3_key, url = _C._pm_upload_csv(fname, csv_text)
        else:
            fname, csv_text = _lt.text_csv(last['text'], subject=last.get('subject'),
                                           question=last.get('question'))
            csv_text = _H._stamp_csv_text(csv_text)
            s3_key, url = _C._pm_upload_csv(fname, csv_text)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _C._pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    if not url:   # nothing this reader can open sits behind that link (file_access, 2026-11-18)
        return jsonify({'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                        'reply': 'I do not have a file to hand over for that. Open the profile you mean and ask again.'})
    if s3_key and not (last['kind'] == 'file' and last['file'].get('page')):
        _C._pm_file_stash_write(uname, url, fname, s3_key,
                             subject=last.get('subject'), question=last.get('question') or text)
    _C._pm_ask_hint(outcome='answered', subject=last.get('subject'))
    reply = f"Saved. {fname} is in your browser downloads."
    return jsonify({
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': ['Email me this file'], 'offer_deck': False, 'deck_angle': None,
        'download_url': url, 'filename': fname,
        'file_link': {'url': url, 'label': f"Download {fname}"}})


# <<block: 15>>
def _pm_search_demand_response(user, text, history):
    """Search-journey demand read (2026-08-26 Jenna directive): how
    people find, search for, and first touch a title or brand. Shaped
    on the Normal (Bob Odenkirk) HBO Max study: first-touch splits,
    rival hunt, destination share, interest clusters. Needs no page
    context; the subject comes from the question itself. Every count
    passes the server-side coherence pass (messy last digits, sub-counts
    sum exactly to parents) and the vocabulary scrub before it ships.

    Consistency (2026-08-26): the insights ledger is consulted first.
    A repeat of a stored ask replays the stored reply verbatim; prior
    published numbers for the same subject ride the prompt as binding
    constraints; every delivered study persists back to the ledger."""
    import prometheus_analysis as pma
    import insights_ledger as il
    _C._pm_ask_hint(route='search_demand')
    _pm_user = (session.get('username') or user.get('username') or '').strip()
    _pm_ppu = _C._pm_usage_extras(user)
    # Prometheus asks are session-metered (2026-09-09 Jenna). No
    # per-pull credit gate on this route: pay-per-use accounts still
    # bill via the session close, subscribed accounts are covered by
    # their tier. Real pipeline pulls (Profile IQ, Subscriber IQ)
    # remain credit-gated elsewhere.
    led = {'block': '', 'exact': None, 'entries': []}
    _t_ledger = time.monotonic()
    try:
        led = il.consult(question=text)
    except Exception:
        traceback.print_exc()
    _C._pm_ask_stage('ledger', t0=_t_ledger)
    exact = led.get('exact')
    if exact and exact.get('route') == 'search_demand' \
            and exact.get('reply'):
        _C._pm_ask_hint(outcome='answered', subject=led.get('subject'))
        # Served from the library: metered, never free (2026-09-14).
        _C._pm_meter_answer('replay_search', _pm_ppu)
        _replay_chips = [c for c in list(exact.get('followups') or [])[:3]
                         if c != pma.CSV_OFFER_CHIP]
        _C._pm_csv_point(led.get('subject'), text, 'search')
        _rp_file = _C._pm_replay_file(exact, text, _pm_user, _replay_chips)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': led.get('subject'),
            **_rp_file})
    _sd_led_block = led.get('block') or ''
    try:
        _cat_block = _C._pm_catalog_block(led.get('subject')
                                       or pma.guess_subject_from_text(text))
        if _cat_block:
            _sd_led_block = (_cat_block + '\n' + _sd_led_block) if _sd_led_block else _cat_block
    except Exception:
        traceback.print_exc()
    user_prompt = pma.build_search_demand_user_prompt(
        text, history, ledger_block=_sd_led_block)
    try:
        import prometheus_knowledge as _pmk
        _kb = _pmk.knowledge_block('search_demand', text=text,
                                   s3_client=_H.s3_client, bucket=_H.S3_BUCKET)
        if _kb:
            user_prompt = f"{user_prompt}\n\n{_kb}"
    except Exception as _fx:
        _faults.note('chat', _fx)
    _t_model = time.monotonic()
    # The window is the title's real premiere, looked up when the model is
    # not sure of it (Cara Dorfman 2026-11-10: Off Campus, released on Prime
    # Video May 13 2026, was read on an invented Oct 2 to Oct 18 2026
    # window). Research is additive: the tool plan falls back to no tools.
    result = _C._pm_claude_json(pma.SEARCH_DEMAND_SYSTEM_PROMPT, user_prompt,
                             max_tokens=7500, temperature=0.4,
                             usage_extras=_pm_ppu, tools=[pma.WEB_SEARCH_TOOL])
    _C._pm_ask_stage('model', t0=_t_model)
    if not result.get('success'):
        _H._chatbot_error_email(
            'brief-chat/analyze',
            'search-demand model call failed: '
            + str(result.get('error') or 'unknown')[:400],
            tb='(model chain exhausted without a usable reply)')
        _C._pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    data = result.get('data') or {}
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), {})
    if str(data.get('action') or '').strip().lower() == 'clarify':
        q = pma.scrub_user_text(
            str(data.get('clarify_question') or '').strip()
            or 'Which title or brand should I read the search demand '
               'for, and on which platform?')
        opts = [pma.scrub_user_text(str(o).strip())[:160]
                for o in (data.get('clarify_options') or [])
                if str(o).strip()][:4]
        _C._pm_ask_hint(outcome='clarify')
        return jsonify({
            'success': True, 'action': 'answer', 'reply': q,
            'followups': opts, 'offer_deck': False, 'deck_angle': None})
    try:
        study = pma.enforce_demand_coherence(data)
        reply = pma.format_search_demand_reply(study)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _C._pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    # Base-profile boundary (2026-08-27, Jenna): a generated search
    # study only ships for a subject already pulled somewhere (profile
    # catalog or a Subscriber IQ run). No base = steer to the build,
    # no numbers.
    _sd_base = None
    try:
        _sd_base = _C._pm_generation_base(study.get('subject'), text)
    except Exception:
        traceback.print_exc()
    if not _sd_base:
        _sd_subj = str(study.get('subject')
                       or pma.guess_subject_from_text(text) or '').strip()
        _sd_user = (session.get('username') or user.get('username')
                    or '').strip()
        if _sd_subj:
            _sd_label = _C._pm_panel_price_label(_sd_user)[0]
            _b_reply, _b_chips, _b_offer = pma.build_panel_report_offer(
                _sd_subj, _sd_label, question=text)
            _C._pm_ask_hint(outcome='panel_offer', subject=_sd_subj)
            return jsonify({
                'success': True, 'action': 'answer', 'reply': _b_reply,
                'followups': _b_chips, 'offer_deck': False,
                'deck_angle': None, 'panel_offer': _b_offer})
        _b_reply, _b_chips = pma.build_profile_required_reply(
            '', price_label=_C._pm_panel_price_label(_sd_user)[0])
        _C._pm_ask_hint(outcome='clarify_subject')
        return jsonify({
            'success': True, 'action': 'answer', 'reply': _b_reply,
            'followups': _b_chips, 'offer_deck': False,
            'deck_angle': None})
    if not reply.strip():
        _H._chatbot_error_email('brief-chat/analyze',
                             'search-demand study rendered empty',
                             tb='(coherence pass left no sections)')
        _C._pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip() and str(f).strip() != pma.CSV_OFFER_CHIP][:3]
    _sd_metrics = il.metrics_from_study(study)
    try:
        _sd_universe = (f"US people who searched for {study.get('subject')}"
                        + (f" on {study.get('platform')}" if study.get('platform') else '') + " in the window")
        il.persist(
            subject=study.get('subject'), metric_family='search',
            question=text, route='search_demand',
            metrics=il.attach_sample_people(_sd_metrics, universe=_sd_universe),
            universe=_sd_universe,
            anchors=[b for b in (
                f"platform: {study.get('platform')}"
                if study.get('platform') else None,
                f"rival: {study.get('rival')}"
                if study.get('rival') else None) if b],
            window_start=study.get('window_start'),
            window_end=study.get('window_end'),
            window_label=study.get('window_label'),
            reply=reply, followups=followups,
            base_profile_key=_sd_base.get('s3_key'))
    except Exception:
        traceback.print_exc()
    _C._pm_csv_point(study.get('subject'), text, 'search')
    _sd_file = _C._pm_replay_file(
        {'subject': study.get('subject'), 'metrics': _sd_metrics, 'question': text,
         'ws': study.get('window_start'), 'we': study.get('window_end'), 'wl': study.get('window_label')},
        text, _pm_user, followups)
    _C._pm_ask_hint(outcome='answered', subject=study.get('subject'))
    return jsonify({
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': study.get('subject'),
        **_sd_file})


__all__ = ['_PM_DATA_FILE_PREFIX', '_pm_csv_task_filename', '_pm_answer_file_payload', '_pm_replay_file', '_PM_EMAIL_FILE_RE', '_pm_email_file_intent', '_pm_email_file_response', '_pm_csv_point', '_pm_upload_csv', '_pm_entry_csv', '_pm_page_file', '_pm_last_thing', '_pm_csv_download_response', '_pm_search_demand_response']
