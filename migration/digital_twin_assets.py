"""Focus Group IQ: content in the room (Jenna, 2026-11-05).

Jenna, verbatim: "you should be able to upload content into the focus
group (a video, image, mp3) and it should assess it for you the same way
the pilot assesses video. so that you could then ask quesions about the
content as an a/b testing kind of thing."

Three parts, all here:

  intake      ``accept`` + ``store_upload`` / ``store_url`` put one file
              (video mp4/mov/webm, image png/jpg/webp, audio mp3/m4a/wav,
              or a hosted file behind a URL) under
              ``digital-twins/assets/<twin_key>/<sid>/<asset_id>.<ext>``
              with a label ('A', 'B', or the user's own name for it) and
              append it to ``session['assets']`` at status 'processing'.
  processing  ``process_asset`` turns the file into what the seats can
              take in. Video: frames every few seconds and at the scene
              changes (ffmpeg through ``digital_twin_cut``), captioned
              (``digital_twin_watch.caption_frames``), plus a transcript
              of the audio track. Image: the image itself, read by the
              vision model into a plain description. Audio: a transcript
              plus a description of the voice, the music and the tone as
              far as the words and the pacing show them. Transcription:
              Whisper when it is installed on the host, else AWS
              Transcribe on the stored object (polled to completion),
              else none; a missing transcript is a note, never a failure.
              Runs on a background thread (``start_processing``), or on
              the read worker for a video when ffmpeg is not on the web
              process (``prometheus/room_asset_job.py``). The asset's
              status moves processing -> ready / failed with plain copy.
  assessment  ``assess_asset`` runs the room on it the way Pilot Testing
              assesses a trailer or key art: every seat takes it in and
              reacts in character (the room's own persona chain), the
              seats mirror the published split, and the moderator writes
              the read: the headline, the appeal (would watch / listen /
              click / buy as the asset implies), the clarity (what it is
              about, who it is for), what stood out and what fell flat,
              the lean by segment, the voices, and people counts sized to
              what the room stands for. Screen time or duration never
              weights appeal. The read passes the calibration-silent guard
              and the no-internal-vocabulary scrub. It is stored as a
              question-like record (``kind: 'asset_read'``) so it prints
              in the room, the export and the report like a question page
              under 'What the room made of <label>', and it counts toward
              the group's question cap as one question.

Later questions carry the assets (``attach_question_assets``): the seats
answer with the content in mind; a question naming A or B targets that
asset; one comparing them ('which do you prefer, A or B?', 'A or B?')
runs the A/B path: both in context, the count is the preference split
(A / B / neither / no difference) with people, the seats mirror it, and
the read says why by segment. The comparison is one question on the cap.

Hermetic tests swap the model (``digital_twin_room.set_model_fn`` or a
``models`` argument), the transcriber (``set_transcriber_fn``), the
vision reader (``set_vision_fn``) and S3 (any object with get_object /
put_object); REGRESSION_TEST_MODE never calls a model, Whisper or AWS.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from typing import Callable, Optional

from migration.digital_twin_engine import clamp, human_people, unit

S3_BUCKET = 'dashboard-inputs'
ASSET_PREFIX = 'digital-twins/assets/'
KINDS = ('video', 'image', 'audio')
VIDEO_EXT = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm'}
IMAGE_EXT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
AUDIO_EXT = {'.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.wav': 'audio/wav'}
EXT_BY_KIND = {'video': VIDEO_EXT, 'image': IMAGE_EXT, 'audio': AUDIO_EXT}
MIME_KIND = {'video/mp4': 'video', 'video/quicktime': 'video', 'video/webm': 'video', 'video/x-m4v': 'video',
             'image/png': 'image', 'image/jpeg': 'image', 'image/jpg': 'image', 'image/webp': 'image',
             'audio/mpeg': 'audio', 'audio/mp3': 'audio', 'audio/mp4': 'audio', 'audio/x-m4a': 'audio', 'audio/m4a': 'audio', 'audio/wav': 'audio',
             'audio/x-wav': 'audio', 'audio/wave': 'audio'}
MAX_BYTES = {'video': 2 * 1024 * 1024 * 1024, 'image': 25 * 1024 * 1024, 'audio': 250 * 1024 * 1024}
MAX_ASSETS = 8
MAX_LABEL = 40
FRAME_GRID_S = 4.0          # a frame every few seconds inside a scene
MAX_FRAMES = 24             # the most frames a video is read on
PAGE_FRAMES = 4             # frames on the export page
THUMB_PX = 480
TRANSCRIBE_TIMEOUT_S = 600.0
TRANSCRIBE_POLL_S = 6.0
URL_TIMEOUT_S = 60.0
ASSET_READ_KIND = 'asset_read'
STATUS_PROCESSING, STATUS_READY, STATUS_FAILED = 'processing', 'ready', 'failed'
FAILED_LINE = 'The room could not take this one in. Try the file again, or another format.'
FAILED_URL_LINE = 'That link did not hand back a file the room can take in. Use a direct link to the video, image or audio file.'
ASSET_CHIPS = ('Which do you prefer, A or B?', 'What would make you click?', 'Who is this for?')
IMPLIED = {'watch': ('Would watch', 'Might watch', 'Would not watch'), 'listen': ('Would keep listening', 'Might', 'Would skip it'),
           'click': ('Would click', 'Might click', 'Would scroll past'), 'buy': ('Would buy', 'Might buy', 'Would not buy'),
           'visit': ('Would go', 'Might go', 'Would not go'), 'follow': ('Would follow', 'Might follow', 'Would not follow'),
           'sign_up': ('Would sign up', 'Might', 'Would not sign up')}
DEFAULT_ACTION = {'video': 'watch', 'image': 'click', 'audio': 'listen'}
AB_LABELS = ('Neither', 'No difference')

_LABEL_RX = re.compile(r"(?<![A-Za-z0-9])([A-Z])(?![A-Za-z0-9])")
_COMPARE_RX = re.compile(r"\b(prefer|which (?:one|do you|would you|is better|works|wins|do they)|better|rather|versus|vs\.?|pick one|choose|stronger|or\b.*\?)", re.I)
_SHOWN_RX = re.compile(r"\b(this|the|that)\s+(video|clip|trailer|spot|ad|image|picture|photo|thumbnail|art|poster|cover|song|track|audio|mp3|music|content|asset|cut)\b", re.I)
_URL_RX = re.compile(r"https?://[^\s<>\"']+", re.I)

_TRANSCRIBER_FN: Optional[Callable] = None
_VISION_FN: Optional[Callable] = None
_PROCESS_HOOK: Optional[Callable] = None


# ----------------------------------------------------------------- small
def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def regression_mode() -> bool:
    return str(os.environ.get('REGRESSION_TEST_MODE') or '').strip().lower() in ('1', 'true', 'yes', 'on')


def set_transcriber_fn(fn: Optional[Callable]):
    """A test's transcriber: fn(local_path, s3_key, kind) -> [{t, end, text}] or None."""
    global _TRANSCRIBER_FN
    _TRANSCRIBER_FN = fn


def set_vision_fn(fn: Optional[Callable]):
    """A test's vision reader: fn(system, blocks) -> dict or None."""
    global _VISION_FN
    _VISION_FN = fn


def set_process_hook(fn: Optional[Callable]):
    """A test's stand-in for the whole processing step: fn(asset, local_path) -> taken_in dict."""
    global _PROCESS_HOOK
    _PROCESS_HOOK = fn


def _scrub(s: str) -> str:
    try:
        from migration.digital_twin_room import scrub
        s = scrub(str(s or ''))
    except Exception:
        s = str(s or '')
    try:
        from migration.digital_twin_copy import strip_calibration_language
        s = strip_calibration_language(s)
    except Exception:
        pass
    return s.replace('\u2014', ', ').replace('\u2013', '-').strip()


def banned(s: str) -> list:
    """The room's banned words plus the calibration-silent guard's hits."""
    out = []
    try:
        from migration.digital_twin_room import has_banned
        out += list(has_banned(str(s or '')))
    except Exception:
        pass
    try:
        from migration.digital_twin_copy import calibration_hits
        out += list(calibration_hits(str(s or '')))
    except Exception:
        pass
    return out


def _meta(t) -> dict:
    return (t or {}).get('meta') or {}


def _twin_name(t) -> str:
    return str(_meta(t).get('name') or _meta(t).get('subject') or 'the audience')


def _ext_of(filename: str) -> str:
    return os.path.splitext(str(filename or ''))[1].lower()


def kind_for(filename: str = '', content_type: str = '') -> Optional[str]:
    """'video' | 'image' | 'audio' from the file name, else the content
    type; None when the room cannot take the file."""
    ext = _ext_of(filename)
    for k, exts in EXT_BY_KIND.items():
        if ext in exts:
            return k
    ct = str(content_type or '').split(';')[0].strip().lower()
    return MIME_KIND.get(ct)


def accept(filename: str = '', content_type: str = '') -> bool:
    return kind_for(filename, content_type) is not None


def content_type_for(kind: str, filename: str, given: str = '') -> str:
    ext = _ext_of(filename)
    for k, exts in EXT_BY_KIND.items():
        if ext in exts:
            return exts[ext]
    g = str(given or '').split(';')[0].strip().lower()
    if g in MIME_KIND:
        return g
    return {'video': 'video/mp4', 'image': 'image/jpeg', 'audio': 'audio/mpeg'}.get(kind, 'application/octet-stream')


def ext_for(kind: str, filename: str, content_type: str = '') -> str:
    ext = _ext_of(filename)
    if ext in EXT_BY_KIND.get(kind, {}):
        return ext
    ct = str(content_type or '').split(';')[0].strip().lower()
    for e, m in EXT_BY_KIND.get(kind, {}).items():
        if m == ct:
            return e
    return {'video': '.mp4', 'image': '.jpg', 'audio': '.mp3'}.get(kind, '.bin')


def _safe_key_part(s: str, n: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", '_', str(s or '')).strip('._')[:n] or 'x'


def asset_key(twin_key: str, sid: str, asset_id: str, ext: str) -> str:
    return f"{ASSET_PREFIX}{_safe_key_part(twin_key, 120)}/{_safe_key_part(sid)}/{_safe_key_part(asset_id)}{ext}"


def session_assets(session: dict) -> list:
    """The room's content assets (the ones this module made: they carry a
    status), in the order they were added."""
    return [a for a in ((session or {}).get('assets') or []) if isinstance(a, dict) and a.get('status') and a.get('kind') in KINDS]


def ready_assets(session: dict) -> list:
    return [a for a in session_assets(session) if a.get('status') == STATUS_READY]


def asset_by_id(session: dict, asset_id: str) -> Optional[dict]:
    return next((a for a in session_assets(session) if str(a.get('id')) == str(asset_id)), None)


def next_label(session: dict) -> str:
    """'A', then 'B', ... the first letter no asset in the room carries."""
    taken = {str(a.get('label') or '').strip().upper() for a in session_assets(session)}
    for i in range(26):
        c = chr(65 + i)
        if c not in taken:
            return c
    return f"Asset {len(session_assets(session)) + 1}"


def clean_label(label: str, session: dict) -> str:
    lab = ' '.join(str(label or '').split())[:MAX_LABEL]
    if not lab:
        return next_label(session)
    if len(lab) == 1 and lab.isalpha():
        lab = lab.upper()
    taken = {str(a.get('label') or '').strip().lower() for a in session_assets(session)}
    if lab.lower() in taken:
        base, n = lab, 2
        while lab.lower() in taken and n < 50:
            lab = f"{base} {n}"
            n += 1
    return lab


def public_asset(a: dict) -> dict:
    """What the page sees: no storage keys, no caches; has_file, the
    status and the label, the thumbnail count."""
    if not isinstance(a, dict):
        return {}
    out = {k: v for k, v in a.items() if k not in ('s3_key', 'thumb_key', 'frame_keys', 'taken_in', 'transcript', 'local_path')}
    out['has_file'] = bool(a.get('s3_key'))
    out['has_thumb'] = bool(a.get('thumb_key'))
    out['frames_n'] = len(a.get('frame_keys') or [])
    ti = a.get('taken_in') or {}
    if isinstance(ti, dict):
        out['description'] = str(ti.get('description') or '')[:400]
        out['what_it_is'] = str(ti.get('what_it_is') or '')
        out['implied_action'] = str(ti.get('implied_action') or '')
        out['transcript_excerpt'] = str(ti.get('transcript_excerpt') or '')[:600]
        out['duration_s'] = ti.get('duration_s')
    return out


def public_assets(session: dict) -> list:
    return [public_asset(a) for a in session_assets(session)]


def represented_people(session: dict, twin: Optional[dict] = None) -> int:
    """The people the room stands for: the represented-audience helper
    when the panel carries one (the cuts, never the full twin for a cut
    room), else the room's own total."""
    g = (session or {}).get('group') or {}
    for mod, name in (('migration.digital_twin_panel', 'represented_people'), ('migration.digital_twin_panel', 'represented_audience'),
                      ('migration.digital_twin_pilot', 'represented_people'), ('migration.digital_twin_pilot', 'represented_audience')):
        try:
            import importlib
            fn = getattr(importlib.import_module(mod), name, None)
            if callable(fn):
                try:
                    got = fn(session, twin)
                except TypeError:
                    got = fn(session)
                if isinstance(got, dict):
                    got = got.get('people') or got.get('total') or got.get('size_people')
                if got and int(float(got)) > 0:
                    return int(float(got))
        except Exception:
            continue
    try:
        from migration.digital_twin_panel import room_total_people
        n = int(room_total_people(g) or 0)
        if n > 0:
            return n
    except Exception:
        pass
    try:
        from migration.digital_twin_pilot import room_people
        n = int(room_people(g.get('roster') or [], g.get('segments') or [], twin) or 0)
        if n > 0:
            return n
    except Exception:
        pass
    return int(sum(int(float(s.get('people') or 0)) for s in (g.get('segments') or []) if isinstance(s, dict)))


# ---------------------------------------------------------------- intake
def new_asset(session: dict, *, kind: str, label: str, filename: str, content_type: str, size: int, s3_key: str, source: str = 'upload',
              url: str = '', added_by: str = '') -> dict:
    aid = uuid.uuid4().hex[:8]
    rec = {'id': aid, 'kind': kind, 'label': clean_label(label, session), 'filename': os.path.basename(str(filename or '')) or f"{kind}{ext_for(kind, filename, content_type)}",
           'content_type': content_type, 'bytes': int(size or 0), 's3_key': s3_key, 'source': source, 'url': str(url or '')[:600] if url else '',
           'status': STATUS_PROCESSING, 'stage': 'taking it in', 'note': '', 'added_by': added_by or '', 'added_at': _now(), 'updated_at': _now(),
           'read_id': None, 'hash': hashlib.sha1(f"{s3_key}|{size}|{filename}".encode('utf-8')).hexdigest()[:12]}
    session.setdefault('assets', []).append(rec)
    session['updated_at'] = _now()
    return rec


def store_upload(s3, session: dict, *, twin_key: str, raw: bytes, filename: str, content_type: str = '', label: str = '', added_by: str = '') -> tuple:
    """Put the bytes under the asset key and append the record. Returns
    (asset, None) or (None, plain error)."""
    kind = kind_for(filename, content_type)
    if not kind:
        return None, 'Use a video (mp4, mov, webm), an image (png, jpg, webp) or audio (mp3, m4a, wav).'
    if not raw or len(raw) < 64:
        return None, 'That file is empty.'
    if len(raw) > MAX_BYTES[kind]:
        return None, {'video': 'Use a video under 2 GB.', 'image': 'Use an image under 25 MB.', 'audio': 'Use an audio file under 250 MB.'}[kind]
    if len(session_assets(session)) >= MAX_ASSETS:
        return None, f"A room carries up to {MAX_ASSETS} pieces of content. Open another room for more."
    if s3 is None:
        return None, 'The file could not be stored. Try again in a minute.'
    aid_ext = ext_for(kind, filename, content_type)
    ctype = content_type_for(kind, filename, content_type)
    aid = uuid.uuid4().hex[:8]
    key = asset_key(twin_key, str(session.get('id') or ''), aid, aid_ext)
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=raw, ContentType=ctype)
    except Exception:
        traceback.print_exc()
        return None, 'The file could not be stored. Try again in a minute.'
    rec = new_asset(session, kind=kind, label=label, filename=filename, content_type=ctype, size=len(raw), s3_key=key, source='upload', added_by=added_by)
    rec['id'] = aid
    rec['hash'] = hashlib.sha1(f"{key}|{len(raw)}|{filename}".encode('utf-8')).hexdigest()[:12]
    return rec, None


def fetch_url(url: str, *, max_bytes: int = MAX_BYTES['video'], timeout_s: float = URL_TIMEOUT_S) -> tuple:
    """(bytes, content_type, filename) for a hosted file, or (None, '',
    error). A page (HTML) is refused: the room takes files, not sites."""
    u = str(url or '').strip()
    if not _URL_RX.match(u):
        return None, '', 'Paste a direct link to the file (https://...).'
    if regression_mode():
        return None, '', FAILED_URL_LINE
    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (compatible; Crosswalk Focus Group IQ)', 'Accept': '*/*'})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            ctype = str(resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype.startswith('text/html') or ctype == 'application/xhtml+xml':
                return None, ctype, FAILED_URL_LINE
            buf = io.BytesIO()
            while True:
                bit = resp.read(4 * 1024 * 1024)
                if not bit:
                    break
                buf.write(bit)
                if buf.tell() > max_bytes:
                    return None, ctype, 'That file is too large for the room.'
            raw = buf.getvalue()
    except Exception as e:
        print(f"[twin-assets] url fetch failed: {str(e)[:160]}")
        return None, '', 'That link could not be reached. Check it and try again.'
    name = os.path.basename(urllib.parse.urlparse(u).path) or ''
    if not kind_for(name, ctype):
        guess = mimetypes.guess_extension(ctype) or ''
        if guess and kind_for('x' + guess, ctype):
            name = (name or 'content') + guess
    if not kind_for(name, ctype):
        return None, ctype, FAILED_URL_LINE
    return raw, ctype, name


def store_url(s3, session: dict, *, twin_key: str, url: str, label: str = '', added_by: str = '') -> tuple:
    raw, ctype, name = fetch_url(url)
    if raw is None:
        return None, name
    rec, err = store_upload(s3, session, twin_key=twin_key, raw=raw, filename=name, content_type=ctype, label=label, added_by=added_by)
    if rec is not None:
        rec['source'] = 'url'
        rec['url'] = str(url or '')[:600]
    return rec, err


def store_s3_copy(s3, session: dict, *, twin_key: str, src_key: str, filename: str, content_type: str = '', label: str = '', added_by: str = '') -> tuple:
    """An object already in the bucket (a chat attachment): copied under
    the asset key."""
    kind = kind_for(filename, content_type)
    if not kind:
        return None, 'Use a video (mp4, mov, webm), an image (png, jpg, webp) or audio (mp3, m4a, wav).'
    if len(session_assets(session)) >= MAX_ASSETS:
        return None, f"A room carries up to {MAX_ASSETS} pieces of content. Open another room for more."
    if s3 is None:
        return None, 'The file could not be stored. Try again in a minute.'
    aid = uuid.uuid4().hex[:8]
    key = asset_key(twin_key, str(session.get('id') or ''), aid, ext_for(kind, filename, content_type))
    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=src_key)
        size = int(head.get('ContentLength') or 0)
        s3.copy_object(Bucket=S3_BUCKET, CopySource={'Bucket': S3_BUCKET, 'Key': src_key}, Key=key, MetadataDirective='COPY')
    except Exception:
        traceback.print_exc()
        return None, 'The file could not be stored. Try again in a minute.'
    rec = new_asset(session, kind=kind, label=label, filename=filename, content_type=content_type_for(kind, filename, content_type), size=size, s3_key=key, source='attachment', added_by=added_by)
    rec['id'] = aid
    return rec, None


# ---------------------------------------------------------- transcription
def _lines_from_whisper(path: str) -> Optional[list]:
    try:
        from migration.digital_twin_cut import transcribe as _w
        return _w(path)
    except Exception:
        return None


def _transcribe_media_format(ext: str) -> Optional[str]:
    return {'.mp3': 'mp3', '.mp4': 'mp4', '.m4v': 'mp4', '.wav': 'wav', '.webm': 'webm', '.m4a': 'm4a', '.flac': 'flac', '.ogg': 'ogg'}.get(ext)


def _aws_transcribe(s3_key: str, *, bucket: str = S3_BUCKET, timeout_s: float = TRANSCRIBE_TIMEOUT_S, poll_s: float = TRANSCRIBE_POLL_S) -> Optional[list]:
    """AWS Transcribe on the stored object: start the job, poll it to
    completion, read the transcript into timestamped lines. None on any
    failure (permissions, format, timeout); never raises."""
    fmt = _transcribe_media_format(_ext_of(s3_key))
    if not fmt:
        return None
    try:
        import boto3
        region = os.environ.get('AWS_TRANSCRIBE_REGION') or os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-2'
        tr = boto3.client('transcribe', region_name=region)
    except Exception as e:
        print(f"[twin-assets] transcribe client unavailable: {str(e)[:120]}")
        return None
    job = f"cw-fgiq-{hashlib.sha1(s3_key.encode('utf-8')).hexdigest()[:16]}-{int(time.time())}"
    try:
        tr.start_transcription_job(TranscriptionJobName=job, Media={'MediaFileUri': f"s3://{bucket}/{s3_key}"}, MediaFormat=fmt, IdentifyLanguage=True,
                                   LanguageOptions=['en-US', 'es-US', 'fr-FR', 'de-DE', 'pt-BR'])
    except Exception as e:
        print(f"[twin-assets] transcribe start failed: {str(e)[:200]}")
        return None
    t0 = time.time()
    uri = ''
    while time.time() - t0 < timeout_s:
        try:
            got = tr.get_transcription_job(TranscriptionJobName=job).get('TranscriptionJob') or {}
        except Exception as e:
            print(f"[twin-assets] transcribe poll failed: {str(e)[:160]}")
            return None
        st = str(got.get('TranscriptionJobStatus') or '')
        if st == 'COMPLETED':
            uri = str((got.get('Transcript') or {}).get('TranscriptFileUri') or '')
            break
        if st == 'FAILED':
            print(f"[twin-assets] transcribe failed: {str(got.get('FailureReason') or '')[:200]}")
            return None
        time.sleep(poll_s)
    if not uri:
        print('[twin-assets] transcribe timed out')
        return None
    try:
        with urllib.request.urlopen(uri, timeout=60) as resp:
            doc = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"[twin-assets] transcript fetch failed: {str(e)[:160]}")
        return None
    try:
        tr.delete_transcription_job(TranscriptionJobName=job)
    except Exception:
        pass
    return transcript_lines_from_aws(doc)


def transcript_lines_from_aws(doc: dict) -> Optional[list]:
    """[{t, end, text}] from the Transcribe document: words grouped into
    lines at punctuation or every twelve words."""
    items = ((doc or {}).get('results') or {}).get('items') or []
    out, words, start = [], [], None
    for it in items:
        alts = it.get('alternatives') or []
        content = str((alts[0] if alts else {}).get('content') or '')
        if it.get('type') == 'punctuation':
            if words:
                words[-1] = words[-1] + content
            if content in ('.', '?', '!') and words:
                out.append({'t': float(start or 0), 'end': float(it.get('end_time') or start or 0), 'text': ' '.join(words)[:180]})
                words, start = [], None
            continue
        if start is None:
            start = it.get('start_time')
        words.append(content)
        if len(words) >= 12:
            out.append({'t': float(start or 0), 'end': float(it.get('end_time') or start or 0), 'text': ' '.join(words)[:180]})
            words, start = [], None
    if words:
        out.append({'t': float(start or 0), 'end': float(start or 0), 'text': ' '.join(words)[:180]})
    return out or None


def transcribe(local_path: str, s3_key: str, kind: str, *, s3=None, tell=None) -> tuple:
    """(lines or None, how): the test's transcriber, Whisper on the host,
    AWS Transcribe on the stored object, else none."""
    if _TRANSCRIBER_FN is not None:
        try:
            return _TRANSCRIBER_FN(local_path, s3_key, kind), 'test'
        except Exception as e:
            print(f"[twin-assets] fake transcriber failed: {e}")
            return None, 'none'
    if regression_mode():
        return None, 'none'
    try:
        import whisper  # type: ignore  # noqa: F401
        have_whisper = True
    except Exception:
        have_whisper = False
    if have_whisper and local_path and os.path.isfile(local_path):
        if tell:
            tell('listening to the audio')
        lines = _lines_from_whisper(local_path)
        if lines:
            return lines, 'whisper'
    key = s3_key
    if _transcribe_media_format(_ext_of(key)) is None and local_path and shutil.which('ffmpeg') and s3 is not None:
        # a .mov: the audio track as wav beside the file, transcribed from there
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            from migration.digital_twin_cut import extract_audio
            if extract_audio(local_path, tmp):
                wav_key = re.sub(r"\.[A-Za-z0-9]+$", '', key) + '.audio.wav'
                with open(tmp, 'rb') as fh:
                    s3.put_object(Bucket=S3_BUCKET, Key=wav_key, Body=fh.read(), ContentType='audio/wav')
                key = wav_key
        except Exception as e:
            print(f"[twin-assets] audio side file skipped: {str(e)[:120]}")
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    if tell:
        tell('listening to the audio')
    lines = _aws_transcribe(key)
    return (lines, 'aws') if lines else (None, 'none')


def transcript_excerpt(lines: Optional[list], limit: int = 900) -> str:
    if not lines:
        return ''
    text = ' '.join(str(r.get('text') or '').strip() for r in lines if isinstance(r, dict))
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0]
    return cut + ' ...'


# --------------------------------------------------------------- vision
IMAGE_SYSTEM = """You are the eyes of a focus group, shown one image a moderator is about to put to the room. Write, in plain words: what is on it (who or what, the pose, the setting, the mood, the words on it), the first thing the eye lands on, what kind of thing it is (one of: thumbnail, key art, poster, ad, product shot, photo, album cover, logo, screenshot, other), and the action it asks of a person (one of: watch, listen, click, buy, visit, follow, sign_up). Never name a real person. No em dashes. Never mention ratings, sources or measurement.
Return JSON only: {"description": "<two or three sentences>", "eye_lands_on": "<a few words>", "text_on_it": "<the words on it, or empty>", "what_it_is": "<one of the kinds>", "implied_action": "<one of the actions>", "mood": "<a few words>"}"""

VIDEO_SYSTEM = """You are the eyes and ears of a focus group, handed the frame captions and the transcript of a video a moderator is about to put to the room. Write, in plain words: what it is (one of: trailer, spot, ad, clip, episode, music video, short, explainer, other), what it is about and who it seems to be for, the beats in order (three to six short lines), the moment that lands hardest, and the action it asks of a person (one of: watch, listen, click, buy, visit, follow, sign_up). Never name a real actor. Never let the length of the video stand for its appeal. No em dashes. Never mention ratings, sources or measurement.
Return JSON only: {"description": "<two or three sentences>", "what_it_is": "<kind>", "about": "<one sentence>", "for_whom": "<one sentence>", "beats": ["..."], "strongest_moment": "<one sentence>", "implied_action": "<action>", "mood": "<a few words>"}"""

AUDIO_SYSTEM = """You are the ears of a focus group, handed the transcript (with timings) of an audio piece a moderator is about to put to the room, and what is known about it (its length, its name). Describe, in plain words, the voice, the music and the tone as far as the words and the pacing show them (who seems to be speaking or singing, how fast, how warm or hard, whether it reads as a song, an ad, a podcast moment, a jingle or a voice memo), what it is about and who it seems to be for, and the action it asks of a person (one of: listen, watch, click, buy, visit, follow, sign_up). Never name a real artist. Never let the length stand for its appeal. No em dashes. Never mention ratings, sources or measurement.
Return JSON only: {"description": "<two or three sentences>", "what_it_is": "<song | ad | podcast clip | jingle | voice memo | other>", "about": "<one sentence>", "for_whom": "<one sentence>", "voice": "<a few words>", "music": "<a few words>", "tone": "<a few words>", "implied_action": "<action>"}"""


def _vision(system: str, blocks: list, *, usage_extras=None, surface: str = 'twin_room_asset') -> Optional[dict]:
    if _VISION_FN is not None:
        try:
            got = _VISION_FN(system, blocks)
            return got if isinstance(got, dict) else None
        except Exception as e:
            print(f"[twin-assets] fake vision failed: {e}")
            return None
    if regression_mode():
        return None
    try:
        from migration.digital_twin_watch import _vision_json
        data, _m, _s = _vision_json(system, blocks, max_tokens=1200, usage_extras=usage_extras, surface=surface)
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[twin-assets] vision skipped: {str(e)[:160]}")
        return None


def _image_block(raw: bytes, content_type: str) -> dict:
    mt = content_type if content_type in ('image/png', 'image/jpeg', 'image/webp', 'image/gif') else 'image/jpeg'
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': base64.b64encode(raw).decode('ascii')}}


def _thumb_bytes(raw: bytes, content_type: str, px: int = THUMB_PX) -> tuple:
    """A small JPEG for the strip and the export page (PIL when present;
    the image itself when small enough)."""
    try:
        from PIL import Image  # type: ignore
        im = Image.open(io.BytesIO(raw))
        im = im.convert('RGB')
        im.thumbnail((px, px))
        out = io.BytesIO()
        im.save(out, format='JPEG', quality=84)
        return out.getvalue(), 'image/jpeg'
    except Exception:
        if len(raw) <= 600 * 1024:
            return raw, content_type
        return b'', ''


def _normalize_action(v, kind: str) -> str:
    a = re.sub(r'[^a-z_]', '', str(v or '').strip().lower().replace(' ', '_').replace('-', '_'))
    if a in IMPLIED:
        return a
    return DEFAULT_ACTION.get(kind, 'watch')


def describe_image(raw: bytes, asset: dict, *, usage_extras=None) -> dict:
    blocks = [{'type': 'text', 'text': f"The image the moderator calls '{asset.get('label')}' ({asset.get('filename') or 'image'})."}, _image_block(raw, str(asset.get('content_type') or ''))]
    data = _vision(IMAGE_SYSTEM, blocks, usage_extras=usage_extras, surface='twin_room_asset_image') or {}
    out = {'description': _scrub(str(data.get('description') or ''))[:600], 'eye_lands_on': _scrub(str(data.get('eye_lands_on') or ''))[:100],
           'text_on_it': _scrub(str(data.get('text_on_it') or ''))[:160], 'what_it_is': _scrub(str(data.get('what_it_is') or 'image'))[:30].lower(),
           'implied_action': _normalize_action(data.get('implied_action'), 'image'), 'mood': _scrub(str(data.get('mood') or ''))[:60]}
    if not out['description']:
        out['description'] = f"An image the moderator calls {asset.get('label')}."
        out['note'] = 'the image could not be read in detail this time'
    return out


def describe_video(captions: dict, frames: list, transcript: Optional[list], asset: dict, duration_s: float, *, models=None, usage_extras=None) -> dict:
    cap_lines = []
    for f in frames or []:
        c = (captions or {}).get(str(f.get('scene_id'))) or (captions or {}).get(f.get('scene_id')) or {}
        line = str((c or {}).get('on_screen') or (c or {}).get('caption') or '').strip()
        if line:
            cap_lines.append({'t': round(float(f.get('t') or 0), 1), 'on_screen': line[:200]})
    body = {'label': asset.get('label'), 'filename': asset.get('filename'), 'length_s': round(float(duration_s or 0), 1) or None,
            'frames_in_order': cap_lines[:MAX_FRAMES], 'transcript': transcript_excerpt(transcript, 1800) or None}
    data = None
    try:
        from migration.digital_twin_room import Models
        models = models or Models(usage_extras=usage_extras)
        data, _call = models.call('moderator', VIDEO_SYSTEM, json.dumps(body, default=str)[:12000], max_tokens=900, temperature=0.3, tools=None)
    except Exception as e:
        print(f"[twin-assets] video description skipped: {str(e)[:160]}")
    data = data if isinstance(data, dict) else {}
    out = {'description': _scrub(str(data.get('description') or ''))[:600], 'what_it_is': _scrub(str(data.get('what_it_is') or 'video'))[:30].lower(),
           'about': _scrub(str(data.get('about') or ''))[:240], 'for_whom': _scrub(str(data.get('for_whom') or ''))[:240],
           'beats': [_scrub(str(b))[:160] for b in (data.get('beats') or []) if str(b).strip()][:6], 'strongest_moment': _scrub(str(data.get('strongest_moment') or ''))[:200],
           'implied_action': _normalize_action(data.get('implied_action'), 'video'), 'mood': _scrub(str(data.get('mood') or ''))[:60]}
    if not out['description']:
        if cap_lines:
            out['description'] = 'On screen, in order: ' + '; '.join(c['on_screen'] for c in cap_lines[:5]) + '.'
        elif transcript:
            out['description'] = 'Heard: ' + transcript_excerpt(transcript, 300)
        else:
            out['description'] = f"A video the moderator calls {asset.get('label')}" + (f", about {int(round(duration_s))} seconds long." if duration_s else '.')
            out['note'] = 'the video could not be read in detail this time'
    out['frames_in_order'] = cap_lines[:MAX_FRAMES]
    return out


def describe_audio(transcript: Optional[list], asset: dict, duration_s: float, *, models=None, usage_extras=None) -> dict:
    pace = None
    if transcript and duration_s:
        words = sum(len(str(r.get('text') or '').split()) for r in transcript)
        pace = round(words / max(1.0, float(duration_s)) * 60.0)
    body = {'label': asset.get('label'), 'filename': asset.get('filename'), 'length_s': round(float(duration_s or 0), 1) or None, 'words_a_minute': pace,
            'transcript_with_timings': [{'t': r.get('t'), 'text': r.get('text')} for r in (transcript or [])][:120] or None}
    data = None
    if transcript:
        try:
            from migration.digital_twin_room import Models
            models = models or Models(usage_extras=usage_extras)
            data, _call = models.call('moderator', AUDIO_SYSTEM, json.dumps(body, default=str)[:12000], max_tokens=800, temperature=0.3, tools=None)
        except Exception as e:
            print(f"[twin-assets] audio description skipped: {str(e)[:160]}")
    data = data if isinstance(data, dict) else {}
    out = {'description': _scrub(str(data.get('description') or ''))[:600], 'what_it_is': _scrub(str(data.get('what_it_is') or 'audio'))[:30].lower(),
           'about': _scrub(str(data.get('about') or ''))[:240], 'for_whom': _scrub(str(data.get('for_whom') or ''))[:240],
           'voice': _scrub(str(data.get('voice') or ''))[:80], 'music': _scrub(str(data.get('music') or ''))[:80], 'tone': _scrub(str(data.get('tone') or ''))[:80],
           'implied_action': _normalize_action(data.get('implied_action'), 'audio')}
    if not out['description']:
        if transcript:
            out['description'] = 'Heard: ' + transcript_excerpt(transcript, 300)
        else:
            out['description'] = f"An audio piece the moderator calls {asset.get('label')}" + (f", about {int(round(duration_s))} seconds long." if duration_s else '.')
            out['note'] = 'no transcript could be read; the room reacts to what it was told about the piece'
    return out


# ------------------------------------------------------------- processing
def _download(s3, key: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix or '.bin', prefix='dtiq-asset-')
    os.close(fd)
    with open(path, 'wb') as fh:
        body = s3.get_object(Bucket=S3_BUCKET, Key=key)['Body']
        while True:
            bit = body.read(4 * 1024 * 1024)
            if not bit:
                break
            fh.write(bit)
    return path


def _upload_small(s3, key: str, data: bytes, ctype: str) -> bool:
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=ctype)
        return True
    except Exception as e:
        print(f"[twin-assets] side file failed: {str(e)[:120]}")
        return False


def take_in(s3, asset: dict, local_path: str, *, models=None, usage_extras=None, tell=None) -> dict:
    """What the seats take in, from the file: the taken_in block, with the
    side files (thumbnail, page frames) written beside the asset."""
    if _PROCESS_HOOK is not None:
        got = _PROCESS_HOOK(asset, local_path)
        if isinstance(got, dict):
            return got
    kind = asset.get('kind')
    base = re.sub(r"\.[A-Za-z0-9]+$", '', str(asset.get('s3_key') or ''))
    out = {'kind': kind, 'label': asset.get('label'), 'duration_s': None, 'description': '', 'implied_action': DEFAULT_ACTION.get(kind, 'watch'), 'what_it_is': kind,
           'transcript_excerpt': '', 'transcript_lines': 0, 'frames_in_order': [], 'transcribed_by': 'none', 'notes': []}
    if kind == 'image':
        with open(local_path, 'rb') as fh:
            raw = fh.read()
        th, tct = _thumb_bytes(raw, str(asset.get('content_type') or 'image/jpeg'))
        if th and s3 is not None and _upload_small(s3, base + '.thumb.jpg', th, tct or 'image/jpeg'):
            asset['thumb_key'] = base + '.thumb.jpg'
        if tell:
            tell('the room is looking at it')
        out.update(describe_image(raw, asset, usage_extras=usage_extras))
        return out
    if kind == 'audio':
        dur = None
        try:
            from migration.digital_twin_cut import probe_duration
            dur = probe_duration(local_path)
        except Exception:
            dur = None
        if dur is None:
            dur = _audio_duration_guess(local_path)
        out['duration_s'] = dur
        lines, how = transcribe(local_path, str(asset.get('s3_key') or ''), 'audio', s3=s3, tell=tell)
        out['transcribed_by'] = how
        if lines:
            asset['transcript'] = lines[:400]
            out['transcript_excerpt'] = transcript_excerpt(lines)
            out['transcript_lines'] = len(lines)
        else:
            out['notes'].append('no transcript could be read')
        if tell:
            tell('the room is listening')
        out.update(describe_audio(lines, asset, float(dur or 0), models=models, usage_extras=usage_extras))
        return out
    # video: frames every few seconds and at the scene changes, captions, the audio track transcribed
    analysis = {}
    try:
        from migration import digital_twin_cut as CUT
        tools = CUT.tools_available()
        if tools.get('ffmpeg'):
            if tell:
                tell('reading the frames')
            analysis = CUT.analyze_cut(local_path, duration_s=0, max_frames=MAX_FRAMES, progress=tell) or {}
        else:
            out['notes'].append('frames could not be read on this host')
            try:
                analysis = {'runtime_s': CUT.probe_duration(local_path)}
            except Exception:
                analysis = {}
    except Exception as e:
        print(f"[twin-assets] video read skipped: {str(e)[:160]}")
        analysis = {}
    out['duration_s'] = analysis.get('runtime_s')
    frames = list(analysis.get('frames') or [])
    captions = {}
    transcript = analysis.get('transcript')
    if not transcript:
        lines, how = transcribe(local_path, str(asset.get('s3_key') or ''), 'video', s3=s3, tell=tell)
        transcript = lines
        out['transcribed_by'] = how
    else:
        out['transcribed_by'] = 'whisper'
    if transcript:
        asset['transcript'] = transcript[:400]
        out['transcript_excerpt'] = transcript_excerpt(transcript)
        out['transcript_lines'] = len(transcript)
    if frames:
        if tell:
            tell('the room is looking at the frames')
        try:
            from migration.digital_twin_watch import caption_frames
            caps = caption_frames(frames, transcript=transcript, title=str(asset.get('label') or ''), usage_extras=usage_extras)
            captions = caps.get('captions') or {}
        except Exception as e:
            print(f"[twin-assets] captions skipped: {str(e)[:160]}")
            captions = {}
        # the page frames: up to four, spread across the run, as small JPEGs beside the asset
        keys = []
        picks = frames if len(frames) <= PAGE_FRAMES else [frames[int(round(i * (len(frames) - 1) / float(PAGE_FRAMES - 1)))] for i in range(PAGE_FRAMES)]
        for i, f in enumerate(picks):
            try:
                with open(str(f.get('path')), 'rb') as fh:
                    raw = fh.read()
                th, tct = _thumb_bytes(raw, 'image/jpeg')
                k = f"{base}.frame{i}.jpg"
                if th and s3 is not None and _upload_small(s3, k, th, tct or 'image/jpeg'):
                    keys.append(k)
            except Exception:
                continue
        if keys:
            asset['frame_keys'] = keys
            asset['thumb_key'] = keys[0]
    out.update(describe_video(captions, frames, transcript, asset, float(out['duration_s'] or 0), models=models, usage_extras=usage_extras))
    try:
        from migration.digital_twin_cut import cleanup
        cleanup(analysis)
    except Exception:
        pass
    wd = (analysis or {}).get('work_dir')
    if wd and os.path.isdir(wd):
        shutil.rmtree(wd, ignore_errors=True)
    return out


def _audio_duration_guess(path: str) -> Optional[float]:
    """Seconds for a wav without ffprobe (the header); None otherwise."""
    try:
        if path.lower().endswith('.wav'):
            import wave
            with wave.open(path, 'rb') as w:
                return round(w.getnframes() / float(w.getframerate() or 1), 1)
    except Exception:
        return None
    return None


def _find_session(payload: dict, sid: str) -> Optional[dict]:
    for lst in ('panel_sessions', 'pilot_sessions'):
        for s in (payload or {}).get(lst) or []:
            if isinstance(s, dict) and str(s.get('id')) == str(sid):
                return s
    return None


def _twins_for(s3, payload: dict, twin_key: str, session: dict) -> dict:
    out = {}
    try:
        from migration import digital_twins as DT
    except Exception:
        DT = None
    for tk in session.get('twin_keys') or [twin_key]:
        t = payload if tk == twin_key else (DT.load_twin(s3, tk) if (DT is not None and s3 is not None) else None)
        if t:
            out[tk] = t
    if twin_key not in out:
        out[twin_key] = payload
    return out


def process_asset(s3, twin_key: str, sid: str, asset_id: str, *, payload: Optional[dict] = None, twins_by_key: Optional[dict] = None, models=None,
                  usage_extras=None, tell=None, save: bool = True, assess: bool = True) -> Optional[dict]:
    """Take the file in and run the room on it. Writes the asset's status
    and the read onto the session (re-read from S3 just before the write
    so a question that landed meanwhile is kept). Returns the asset."""
    from migration import digital_twins as DT
    payload = payload or (DT.load_twin(s3, twin_key) if s3 is not None else None)
    session = _find_session(payload or {}, sid)
    asset = asset_by_id(session, asset_id) if session else None
    if not payload or not session or not asset:
        print(f"[twin-assets] {asset_id}: session or asset not found")
        return None

    def _tell(msg):
        asset['stage'] = str(msg or '')[:80]
        asset['updated_at'] = _now()
        if tell:
            try:
                tell(msg)
            except Exception:
                pass
    tmp = None
    try:
        _tell('fetching the file')
        tmp = _download(s3, str(asset.get('s3_key') or ''), _ext_of(asset.get('filename') or asset.get('s3_key') or '')) if s3 is not None else None
        if not tmp:
            raise RuntimeError('no file')
        asset['taken_in'] = take_in(s3, asset, tmp, models=models, usage_extras=usage_extras, tell=_tell)
        asset['status'] = STATUS_READY
        asset['stage'] = 'ready'
        asset['note'] = ''
        if asset['taken_in'].get('notes'):
            asset['note'] = _scrub('; '.join(asset['taken_in']['notes']))[:200]
    except Exception as e:
        traceback.print_exc()
        asset['status'] = STATUS_FAILED
        asset['stage'] = 'failed'
        asset['note'] = FAILED_LINE
        asset['error'] = str(e)[:200]
    finally:
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
    asset['updated_at'] = _now()
    rec = None
    if asset['status'] == STATUS_READY and assess:
        try:
            _tell('the room is reacting')
            tbk = twins_by_key or _twins_for(s3, payload, twin_key, session)
            rec = assess_asset(session, tbk, asset, models=models, usage_extras=usage_extras, s3=s3, twin_key=twin_key)
            asset['read_id'] = rec.get('id') if rec else None
            asset['stage'] = 'read'
        except Exception as e:
            traceback.print_exc()
            asset['note'] = (asset.get('note') or '') or 'The room could not finish its read of this one. Ask about it and it answers fresh.'
            asset['error'] = str(e)[:200]
    if save and s3 is not None:
        _land(s3, twin_key, sid, asset, rec, payload)
    return asset


def _land(s3, twin_key: str, sid: str, asset: dict, rec: Optional[dict], fallback_payload: dict):
    """The asset and its read onto a fresh copy of the twin."""
    from migration import digital_twins as DT
    try:
        fresh = DT.load_twin(s3, twin_key) or fallback_payload
    except Exception:
        fresh = fallback_payload
    sess = _find_session(fresh, sid)
    if sess is None:
        return
    lst = sess.setdefault('assets', [])
    idx = next((i for i, a in enumerate(lst) if isinstance(a, dict) and str(a.get('id')) == str(asset.get('id'))), None)
    if idx is None:
        lst.append(asset)
    else:
        lst[idx] = asset
    if rec is not None:
        qs = sess.setdefault('questions', [])
        if not any(str(q.get('id')) == str(rec.get('id')) and q.get('asset_id') == rec.get('asset_id') for q in qs if isinstance(q, dict)):
            # a question landed meanwhile: the read takes the next id, never a duplicate
            ids = {str(q.get('id')) for q in qs if isinstance(q, dict)}
            if str(rec.get('id')) in ids:
                rec['id'] = f"q{len(qs) + 1}"
                asset['read_id'] = rec['id']
            qs.append(rec)
        try:
            from migration.digital_twin_room import refresh_memory
            refresh_memory(sess)
        except Exception:
            pass
    sess['updated_at'] = _now()
    try:
        DT.save_twin(s3, twin_key, fresh)
    except Exception:
        traceback.print_exc()


def needs_worker(asset: dict) -> bool:
    """A video with no ffmpeg on this process is read on the worker."""
    if str(os.environ.get('DTIQ_ASSETS_ON_WORKER') or '') in ('0', 'false', 'no'):
        return False
    if asset.get('kind') != 'video':
        return False
    return not bool(shutil.which('ffmpeg'))


def start_processing(s3, twin_key: str, sid: str, asset: dict, *, username: str = '', usage_extras=None, run_async: bool = True, payload: Optional[dict] = None,
                     models=None) -> dict:
    """Processing in the background: the read worker for a video when
    ffmpeg is not here (``prometheus/room_asset_job.py``), else a daemon
    thread. Synchronous with ``run_async=False`` (tests). Returns {where,
    job_id}."""
    aid = str(asset.get('id'))
    if not run_async:
        process_asset(s3, twin_key, sid, aid, payload=payload, models=models, usage_extras=usage_extras, save=s3 is not None)
        return {'where': 'inline', 'job_id': None}
    if needs_worker(asset) and not regression_mode():
        try:
            from prometheus import room_asset_job as RJ
            job_id = uuid.uuid4().hex[:12]
            RJ.start(job_id, username, {'key': twin_key, 'session_id': sid, 'asset_id': aid}, usage_extras or {})
            asset['job_id'] = job_id
            asset['stage'] = 'queued for reading'
            return {'where': 'worker', 'job_id': job_id}
        except Exception as e:
            print(f"[twin-assets] worker queue skipped: {str(e)[:160]}; processing here")

    def _run():
        try:
            process_asset(s3, twin_key, sid, aid, models=models, usage_extras=usage_extras)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_run, daemon=True, name=f"twin-asset-{aid}").start()
    return {'where': 'thread', 'job_id': None}


def sweep_assets(session: dict, *, max_age_s: float = 1800.0) -> bool:
    """An asset left 'processing' for half an hour reads as failed with
    plain copy (the thread died with the process). True when changed."""
    changed = False
    now = time.time()
    for a in session_assets(session):
        if a.get('status') != STATUS_PROCESSING:
            continue
        try:
            t = time.mktime(time.strptime(str(a.get('updated_at') or a.get('added_at')), '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
        except Exception:
            continue
        if now - t > max_age_s:
            a['status'] = STATUS_FAILED
            a['stage'] = 'failed'
            a['note'] = 'The room did not finish taking this one in. Add it again and it runs fresh.'
            a['updated_at'] = _now()
            changed = True
    return changed


# ------------------------------------------------------------ assessment
ASSET_SEAT_SYSTEM = """You are one person in a focus group, answering as yourself. The moderator is Prometheus. You are given who you are (a card), what you said earlier in this session, and a piece of content Prometheus just put in front of the room: what_you_were_shown describes it (for a video the frames in order and the words heard; for an image what is on it; for audio the words heard and how it sounds). React to it the way you would in the room: in character (your age, your city, your work, your habits), in two or three natural spoken sentences, concrete (what caught you, what lost you, what it reminded you of, whether you would do what it asks). Never mention being an AI, a twin or a persona. Never name a real actor or artist. Never judge it by how long it is. Pick exactly one option by id. Then say in a few words what stood out, what fell flat, what you think it is about and who it is for, and give a confidence from 1 (unsure) to 5 (certain) with a one-sentence reason. No em dashes.
Return JSON only: {"option": "<id>", "line": "<two or three sentences>", "stood_out": "<a few words>", "fell_flat": "<a few words or empty>", "about": "<a few words>", "for_whom": "<a few words>", "confidence": <1 to 5>, "reason": "<one sentence>"}"""

ASSET_READ_SYSTEM = """You are Prometheus, moderating a focus group that was just shown a piece of content (a video, an image or an audio piece). You have the published split on the appeal question (the count: shares and people, and how the seats sit on it), every seat's reaction in its own words, what each seat said stood out and fell flat, and what the content is. Write the room's read for the person who brought the content, in plain words a high school student follows: a headline sentence (the split in words with the people, never the seat tally alone); a read of three to five short sentences on why it landed where it did; the clarity (what the room took it to be about, who the room took it to be for, and whether that was clear); what stood out (two to four short items) and what fell flat (one to three); the lean by segment when there is more than one segment, in a sentence each; the seats whose lines carry it (three seat numbers); and three short follow-up questions the moderator could put next about this content. Never let the length of the piece stand in for its appeal. Never mention ratings services, sources, calibration, benchmarks or measurement. No em dashes. Every number you use must be one you were given.
Return JSON only: {"headline": "<one sentence>", "read": "<three to five sentences>", "chat": "<two sentences for the chat>", "about": "<what it is about, as the room took it>", "for_whom": "<who it is for, as the room took it>", "clear": "<clear | mostly clear | unclear>", "stood_out": ["..."], "fell_flat": ["..."], "by_segment": [{"segment": "<label>", "line": "<one sentence>"}], "voices": [<seat numbers>], "probes_next": ["..."]}"""


def appeal_options(action: str) -> list:
    yes, maybe, no = IMPLIED.get(action) or IMPLIED['watch']
    return [{'id': 'a', 'label': yes, 'lean': 'yes'}, {'id': 'b', 'label': maybe, 'lean': 'maybe'}, {'id': 'c', 'label': no, 'lean': 'no'}]


def seat_view(asset: dict, *, full: bool = True) -> dict:
    """What a seat is handed about one asset (never a storage key)."""
    ti = (asset or {}).get('taken_in') or {}
    view = {'label': asset.get('label'), 'kind': asset.get('kind'), 'what_it_is': ti.get('what_it_is') or asset.get('kind'), 'description': ti.get('description') or '',
            'it_asks_you_to': ti.get('implied_action') or DEFAULT_ACTION.get(asset.get('kind'), 'watch')}
    if asset.get('kind') == 'video':
        view['frames_in_order'] = [f"{c.get('t')}s: {c.get('on_screen')}" for c in (ti.get('frames_in_order') or [])[: MAX_FRAMES if full else 8]]
        view['words_heard'] = ti.get('transcript_excerpt') or ''
        if ti.get('beats'):
            view['beats'] = ti['beats']
        if ti.get('strongest_moment'):
            view['strongest_moment'] = ti['strongest_moment']
    elif asset.get('kind') == 'image':
        for k in ('eye_lands_on', 'text_on_it', 'mood'):
            if ti.get(k):
                view[k] = ti[k]
    else:
        view['words_heard'] = ti.get('transcript_excerpt') or ''
        for k in ('voice', 'music', 'tone'):
            if ti.get(k):
                view[k] = ti[k]
    for k in ('about', 'for_whom'):
        if ti.get(k):
            view[k] = ti[k]
    return view


def _card_for(twin: dict, seat: dict, session_seed: str) -> dict:
    from migration.digital_twin_room import persona_card
    return persona_card(twin, seat, session_seed)


def _seat_react(models, card: dict, memory: list, view: dict, options: list, seed: str, log=None) -> dict:
    from migration.digital_twin_room import _card_text, scrub
    body = {'you': _card_text(card), 'earlier_in_this_session': memory, 'what_you_were_shown': view,
            'moderator': f"Take a look at {view.get('label')}. Would you {str(view.get('it_asks_you_to') or 'watch').replace('_', ' ')} it?",
            'options': [{'id': o['id'], 'label': o['label']} for o in options]}
    data, call = models.call('persona', ASSET_SEAT_SYSTEM, json.dumps(body, default=str)[:9000], max_tokens=460, temperature=0.85, tools=None)
    if log:
        log('persona', call)
    ids = {o['id']: o for o in options}
    if isinstance(data, dict) and str(data.get('line') or '').strip():
        oid = str(data.get('option') or '').strip().lower()
        if oid not in ids:
            hit = next((o for o in options if str(o['label']).lower() == oid), None)
            oid = hit['id'] if hit else 'b'
        try:
            conf = int(clamp(int(float(data.get('confidence') or 3)), 1, 5))
        except (TypeError, ValueError):
            conf = 3
        return {'seat': card['seat'], 'name': card['name'], 'option': oid, 'option_label': ids[oid]['label'], 'line': _scrub(str(data['line']).strip())[:520],
                'confidence': conf, 'reason': _scrub(str(data.get('reason') or '').strip())[:240], 'stood_out': _scrub(str(data.get('stood_out') or ''))[:120],
                'fell_flat': _scrub(str(data.get('fell_flat') or ''))[:120], 'about': _scrub(str(data.get('about') or ''))[:120], 'for_whom': _scrub(str(data.get('for_whom') or ''))[:120],
                'fallback': False}
    u = unit('asset-fb', seed, card['seat'])
    habits = ' '.join(card.get('habits') or [])
    pos = 0.42 if 'deeply loyal' in habits else 0.3
    o = options[0] if u < pos else options[1] if u < pos + 0.33 else options[2]
    return {'seat': card['seat'], 'name': card['name'], 'option': o['id'], 'option_label': o['label'], 'line': f"{o['label']}. That is about where I land on this one.",
            'confidence': 2, 'reason': 'answered from habit', 'stood_out': '', 'fell_flat': '', 'about': '', 'for_whom': '', 'fallback': True}


def _top_phrases(answers: list, field: str, n: int = 4) -> list:
    seen, out = {}, []
    for a in answers:
        v = ' '.join(str(a.get(field) or '').split()).strip(' .')
        if not v or len(v) < 3:
            continue
        k = v.lower()
        seen[k] = seen.get(k, 0) + 1
        if k not in [x.lower() for x in out]:
            out.append(v[0].upper() + v[1:])
    out.sort(key=lambda x: -seen.get(x.lower(), 0))
    return out[:n]


def page_title(label: str) -> str:
    return f"What the room made of {label}"


def _fallback_asset_read(view: dict, opts_out: list, by_segment: list, headline: str, answers: list) -> dict:
    yes = next((o for o in opts_out if o['lean'] == 'yes'), opts_out[0])
    no = next((o for o in opts_out if o['lean'] == 'no'), opts_out[-1])
    from migration.digital_twin_room import plain_share
    lines = [headline] if headline else []
    lines.append(f"{plain_share(yes['pct']).capitalize()} would {str(view.get('it_asks_you_to') or 'watch').replace('_', ' ')} on the strength of {view.get('label')}; {plain_share(no['pct'])} would not.")
    so = _top_phrases(answers, 'stood_out', 2)
    ff = _top_phrases(answers, 'fell_flat', 1)
    if so:
        lines.append('What stood out: ' + ', '.join(x.lower() for x in so) + '.')
    if ff:
        lines.append('What fell flat: ' + ', '.join(x.lower() for x in ff) + '.')
    segs = []
    if len(by_segment) > 1:
        for sr in by_segment:
            rows = sorted(sr.get('distribution') or [], key=lambda r: -float(r.get('pct') or 0))
            if rows:
                segs.append({'segment': sr.get('segment'), 'line': f"{sr.get('segment')}: {plain_share(float(rows[0].get('pct') or 0))} land on {str(rows[0].get('label')).lower()}."})
    return {'headline': headline, 'read': ' '.join(lines), 'chat': ' '.join(lines[:2]), 'about': str(view.get('about') or ''), 'for_whom': str(view.get('for_whom') or ''),
            'clear': 'mostly clear', 'stood_out': so, 'fell_flat': ff, 'by_segment': segs, 'voices': [], 'probes_next': []}


def assess_asset(session: dict, twins_by_key: dict, asset: dict, *, models=None, usage_extras=None, s3=None, twin_key: str = '', log=None) -> dict:
    """The room on one piece of content: the seats react in character,
    the count is weighed and the seats mirror it, the moderator writes the
    read. The record lands on ``session['questions']`` as kind
    'asset_read' and is returned."""
    from migration import digital_twin_room as ROOM
    from migration.digital_twin_engine import VERSION
    models = models or ROOM.Models(usage_extras=usage_extras)
    group = session.get('group') or {}
    roster = list(group.get('roster') or [])
    segments = list(group.get('segments') or [])
    twin_key = twin_key or str((session.get('twin_keys') or [''])[0] or '')
    twin = twins_by_key.get(twin_key) or twins_by_key.get(str((segments or [{}])[0].get('twin') or '')) or next(iter(twins_by_key.values()), {})
    view = seat_view(asset)
    action = str(view.get('it_asks_you_to') or 'watch')
    options = appeal_options(action)
    qi = len(session.get('questions') or [])
    seed = f"{session.get('id')}|asset|{asset.get('id')}|{asset.get('hash')}|{VERSION}"
    session_seed = f"room|{session.get('id')}|{VERSION}"
    try:
        ROOM.ensure_personas(session, twins_by_key, session_seed)
    except Exception as e:
        print(f"[twin-assets] personas skipped: {e}")
    t0 = time.time()
    rec = {'id': f"q{qi + 1}", 'q': page_title(asset.get('label')), 'kind': ASSET_READ_KIND, 'asset_id': asset.get('id'), 'asset_label': asset.get('label'), 'asset_kind': asset.get('kind'),
           'framing': f"The room was shown {asset.get('label')}: {view.get('description')}"[:400], 'options': [], 'option_labels': [o['label'] for o in options], 'distribution': [],
           'by_segment': [], 'read': '', 'read_short': '', 'headline': '', 'headline_people': None, 'voices': [], 'answers': [], 'probes_next': [], 'facts_used': [], 'tally': [],
           'seats_total': len(roster), 'timings': {}, 'stage': 'the room is thinking', 'job_id': None, 'topic': 'content', 'stimulus': {'assets': [view], 'asset_focus': True, 'asset_read': True},
           'follows': None, 'price': None, 'yes_pct': None, 'asked_by': asset.get('added_by') or session.get('created_by'), 'asked_at': _now(), 'started_at': _now(), 'finished_at': None,
           'engine': ROOM.ROOM_VERSION, 'trail': {'stages': [], 'fallbacks': []}, 'clarity': {}, 'stood_out': [], 'fell_flat': [], 'lean_by_segment': []}
    cards = {int(r['seat']): _card_for(ROOM._twin_for_seat(r, twins_by_key), r, session_seed) for r in roster}
    memories = {int(r['seat']): ROOM.memory_for(session, int(r['seat'])) for r in roster}
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    answers = []
    pool = ThreadPoolExecutor(max_workers=max(1, min(len(roster) or 1, 16)))
    futures = {pool.submit(_seat_react, models, cards[int(r['seat'])], memories[int(r['seat'])], view, options, seed, log): int(r['seat']) for r in roster}
    pending = set(futures)
    deadline = time.time() + ROOM.BUDGET_S.get('persona', 120.0)
    while pending and time.time() < deadline:
        done, pending = wait(pending, timeout=max(0.2, min(1.0, deadline - time.time())), return_when=FIRST_COMPLETED)
        for f in done:
            try:
                a = f.result()
            except Exception as e:
                print(f"[twin-assets] seat {futures[f]} failed: {e}")
                a = _seat_react(ROOM.Models(model_fn=lambda *a_, **k_: None), cards[futures[f]], [], view, options, seed)
            a['answered_at'] = round(time.time(), 3)
            answers.append(a)
    for f in pending:
        f.cancel()
        a = _seat_react(ROOM.Models(model_fn=lambda *a_, **k_: None), cards[futures[f]], [], view, options, seed)
        a['answered_at'] = round(time.time(), 3)
        answers.append(a)
        rec['trail']['fallbacks'].append(f"seat {futures[f]} late")
    pool.shutdown(wait=False)
    answers.sort(key=lambda a: int(a['seat']))
    rec['answers'] = answers
    rec['timings']['seats_s'] = round(time.time() - t0, 2)
    # the count: the seats' votes smoothed with each segment's prior over the appeal scale, then the seats mirror it
    total_people = represented_people(session, twin)
    priors = {}
    for sgm in segments:
        seg_twin = twins_by_key.get(str(sgm.get('twin') or '')) or twin
        try:
            priors[sgm['label']] = ROOM.prior_over_options(seg_twin, rec['q'], 'yes_no', options, {'asset': view}, None, seed + '|' + str(sgm['label']))
        except Exception:
            priors[sgm['label']] = {o['id']: 1.0 / len(options) for o in options}
    opts_out, by_segment = ROOM.weigh(answers, options, roster, segments, priors, seed)
    try:
        mirror = ROOM.mirror_seats(opts_out, by_segment, answers, roster, segments)
        if mirror.get('moved'):
            by_seat0 = {int(a['seat']): a for a in answers}
            for m in mirror['moved']:
                sid = int(m['seat'])
                if sid in cards and sid in by_seat0:
                    try:
                        v = ROOM.revoice_seat(models, cards[sid], rec['q'], rec['framing'], options, m['to'], {'subject_status': {}, 'facts': [], 'not_found': [], 'implications': []}, memories.get(sid) or [], seed, log, {'assets': [view]})
                        by_seat0[sid].update({'line': v['line'], 'confidence': v['confidence'], 'reason': v['reason'], 'fallback': bool(v.get('fallback'))})
                    except Exception as e:
                        print(f"[twin-assets] revoice skipped: {str(e)[:120]}")
        ROOM.retally(opts_out, by_segment, answers, roster)
    except Exception as e:
        print(f"[twin-assets] mirror skipped: {str(e)[:120]}")
    rec['options'] = opts_out
    rec['option_labels'] = [o['label'] for o in opts_out]
    rec['distribution'] = [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts_out]
    rec['by_segment'] = by_segment
    rec['tally'] = [{'label': o['label'], 'seats': o.get('seats_n'), 'of': len(roster), 'room_pct': o.get('room_pct')} for o in opts_out]
    rec['yes_pct'] = round(sum(float(o['pct']) for o in opts_out if o['lean'] == 'yes'), 1)
    rec['headline'], rec['headline_people'] = ROOM.headline_for(opts_out, total_people, len(roster), _twin_name(twin))
    rec['people_total'] = total_people
    # the moderator's read
    t1 = time.time()
    user = json.dumps({'content': view, 'headline': rec['headline'],
                       'options': [{'id': o['id'], 'label': o['label'], 'lean': o['lean'], 'published_pct': o['pct'], 'published_in_words': ROOM.plain_share(o['pct']), 'people': o['people'],
                                    'in_the_room': o.get('tally') or f"{len(o.get('seats') or [])} of {len(roster)}", 'seats': o.get('seats')} for o in opts_out],
                       'by_segment': [{k: v for k, v in sr.items() if k != 'tally'} for sr in by_segment],
                       'answers': [{'seat': a['seat'], 'name': a['name'], 'option': a['option_label'], 'confidence': a['confidence'], 'line': a['line'], 'reason': a['reason'],
                                    'stood_out': a.get('stood_out'), 'fell_flat': a.get('fell_flat'), 'about': a.get('about'), 'for_whom': a.get('for_whom')} for a in answers],
                       'audience': {'name': _twin_name(twin), 'people_the_room_stands_for': total_people, 'segments': [s.get('label') for s in segments]}}, default=str)[:14000]
    data, call = models.call('moderator', ASSET_READ_SYSTEM, user, max_tokens=1800, temperature=0.4, tools=None)
    if log:
        log('read', call)
    fb = _fallback_asset_read(view, opts_out, by_segment, rec['headline'], answers)
    data = data if isinstance(data, dict) and str(data.get('read') or '').strip() else None
    got = data or fb
    if data is None:
        rec['trail']['fallbacks'].append('read')
    read = _scrub(str(got.get('read') or ''))[:1400]
    read = ROOM.cohere_read(read, opts_out, len(roster), rec['headline'])[:1600]
    rec['read'] = read
    chat = _scrub(str(got.get('chat') or ''))[:600]
    rec['read_short'] = ROOM.cohere_read(chat, opts_out, len(roster), ROOM.short_read(rec['headline'], 1) if rec['headline'] else '')[:700] if chat else ROOM.short_read(read)
    head = _scrub(str(got.get('headline') or ''))[:300]
    if head and not banned(head):
        rec['headline_read'] = ROOM.cohere_read(head, opts_out, len(roster), rec['headline'])[:320]
    rec['clarity'] = {'about': _scrub(str(got.get('about') or view.get('about') or ''))[:240], 'for_whom': _scrub(str(got.get('for_whom') or view.get('for_whom') or ''))[:240],
                      'clear': str(got.get('clear') or 'mostly clear').strip().lower()[:20]}
    rec['stood_out'] = [_scrub(str(x))[:140] for x in (got.get('stood_out') or []) if str(x).strip()][:4] or _top_phrases(answers, 'stood_out', 3)
    rec['fell_flat'] = [_scrub(str(x))[:140] for x in (got.get('fell_flat') or []) if str(x).strip()][:3] or _top_phrases(answers, 'fell_flat', 2)
    segs_out = []
    for row in (got.get('by_segment') or []):
        if isinstance(row, dict) and str(row.get('line') or '').strip():
            segs_out.append({'segment': _scrub(str(row.get('segment') or ''))[:80], 'line': _scrub(str(row['line']))[:300]})
    rec['lean_by_segment'] = segs_out[:6] if segs_out else fb['by_segment']
    by_seat = {int(a['seat']): a for a in answers}
    seats = {int(r['seat']): r for r in roster}
    picks = []
    for v in (got.get('voices') or []):
        try:
            sid = int(v if not isinstance(v, dict) else (v.get('seat') or 0))
        except (TypeError, ValueError):
            continue
        if sid in by_seat and sid not in picks:
            picks.append(sid)
    if not picks:
        for o in sorted(opts_out, key=lambda o: -float(o.get('pct') or 0)):
            for sno in sorted(o.get('seats') or [], key=lambda s_: -int(by_seat.get(s_, {}).get('confidence') or 0))[:2 if o is opts_out[0] else 1]:
                if sno in by_seat and sno not in picks:
                    picks.append(sno)
        for a in answers:
            if len(picks) >= 3:
                break
            if int(a['seat']) not in picks:
                picks.append(int(a['seat']))
    rec['voices'] = []
    for sid in picks[:4]:
        a, r = by_seat.get(sid), seats.get(sid)
        if a and r:
            v = {'seat': sid, 'name': r['name'], 'age': r['age'], 'city': r['city'], 'segment': r['segment'], 'line': a['line']}
            if r.get('portrait_url'):
                v['portrait_url'] = r['portrait_url']
            rec['voices'].append(v)
    chips = list(ASSET_CHIPS)
    labels = [a.get('label') for a in ready_assets(session) if a is not asset] + [asset.get('label')]
    if len(labels) >= 2:
        chips[0] = f"Which do you prefer, {labels[-2]} or {labels[-1]}?"
    else:
        chips = chips[1:] + ['Would you share this?']
    if action in ('watch',):
        chips = [c.replace('click', 'watch') for c in chips]
    elif action == 'listen':
        chips = [c.replace('click', 'keep listening') for c in chips]
    elif action == 'buy':
        chips = [c.replace('click', 'buy') for c in chips]
    rec['probes_next'] = ([_scrub(str(p))[:120] for p in (got.get('probes_next') or []) if str(p).strip()][:1] + chips)[:4]
    rec['facts_used'] = []
    rec['timings']['read_s'] = round(time.time() - t1, 2)
    rec['timings']['total_s'] = round(time.time() - t0, 2)
    # the guard: nothing a reader sees says calibrated, anchored or names a source; nothing says synth or twin
    for k in ('read', 'read_short', 'headline', 'headline_read', 'framing'):
        if rec.get(k):
            rec[k] = _scrub(rec[k])
    rec['stage'] = 'done'
    rec['finished_at'] = _now()
    tot = models.totals()
    rec['trail'].update({'models': tot['models'], 'tokens': tot['tokens'], 'cost_usd': tot['cost_usd'], 'version': ROOM.ROOM_VERSION, 'asset': {'id': asset.get('id'), 'kind': asset.get('kind'), 'hash': asset.get('hash')}})
    session.setdefault('questions', []).append(rec)
    session['updated_at'] = _now()
    asset['read_id'] = rec['id']
    try:
        ROOM.refresh_memory(session)
    except Exception:
        pass
    return rec


# ------------------------------------------------------ later questions
def _label_tokens(label: str) -> set:
    return {t for t in re.sub(r"[^a-z0-9 ]+", ' ', str(label or '').lower()).split() if t}


def assets_named(session: dict, q: str) -> list:
    """The ready assets the question names: a single capital letter that
    is a label ('A', 'B'), or the user's own label as words."""
    text = str(q or '')
    out = []
    for a in ready_assets(session):
        lab = str(a.get('label') or '').strip()
        if not lab:
            continue
        if len(lab) == 1:
            if lab.upper() in {m.group(1) for m in _LABEL_RX.finditer(text)}:
                out.append(a)
        else:
            toks = _label_tokens(lab)
            if toks and toks <= _label_tokens(text):
                out.append(a)
    return out


def is_comparison(q: str, named: list) -> bool:
    if len(named) < 2:
        return False
    return bool(_COMPARE_RX.search(str(q or '')))


def attach_question_assets(session: dict, q: str, stimulus: Optional[dict], kind: Optional[str], options: Optional[list]) -> tuple:
    """(stimulus, kind, options) for a question in a room with content:
    every ready asset rides as context; a question naming one targets it;
    one comparing two runs the A/B path (kind 'ab', the preference split
    over A / B / neither / no difference)."""
    ready = ready_assets(session)
    stim = dict(stimulus or {})
    if not ready:
        return stim, kind, options
    named = assets_named(session, q)
    compare = is_comparison(q, named)
    if compare:
        pair = named[:2]
        stim['assets'] = [seat_view(a) for a in pair]
        stim['asset_focus'] = True
        stim['ab_assets'] = {'labels': [str(a.get('label')) for a in pair], 'ids': [str(a.get('id')) for a in pair]}
        stim['asset_ids'] = [str(a.get('id')) for a in pair]
        return stim, 'ab', [str(a.get('label')) for a in pair]
    if named:
        stim['assets'] = [seat_view(a) for a in named[:3]]
        stim['asset_focus'] = True
        stim['asset_ids'] = [str(a.get('id')) for a in named[:3]]
        return stim, kind, options
    shown = bool(_SHOWN_RX.search(str(q or '')))
    stim['assets'] = [seat_view(a, full=False) for a in ready[-3:]]
    stim['asset_ids'] = [str(a.get('id')) for a in ready[-3:]]
    if shown:
        stim['asset_focus'] = True
    return stim, kind, options


def ab_options(stimulus: Optional[dict]) -> Optional[list]:
    """The fixed board for an A/B on content: A, B, Neither, No difference."""
    ab = (stimulus or {}).get('ab_assets') if isinstance(stimulus, dict) else None
    if not isinstance(ab, dict) or len(ab.get('labels') or []) < 2:
        return None
    a, b = ab['labels'][:2]
    return [{'id': 'a', 'label': str(a), 'lean': 'yes'}, {'id': 'b', 'label': str(b), 'lean': 'yes'}, {'id': 'c', 'label': AB_LABELS[0], 'lean': 'no'}, {'id': 'd', 'label': AB_LABELS[1], 'lean': 'other'}]


def stimulus_context(stimulus: Optional[dict]) -> Optional[dict]:
    """{shown: [views], focus: bool, ab: bool} when the stimulus carries
    content, else None."""
    if not isinstance(stimulus, dict) or not stimulus.get('assets'):
        return None
    shown = [v for v in stimulus.get('assets') or [] if isinstance(v, dict)]
    if not shown:
        return None
    return {'shown': shown, 'focus': bool(stimulus.get('asset_focus')), 'ab': bool(stimulus.get('ab_assets'))}


def asset_brief(stimulus: Optional[dict]) -> dict:
    """The facts brief for a question that is about the content itself:
    no desk research; the content is what the room has."""
    ctx = stimulus_context(stimulus) or {'shown': []}
    labels = ', '.join(str(v.get('label') or '') for v in ctx['shown'])
    return {'subject_status': {'line': f"The room is looking at {labels}, content the moderator put in front of it."}, 'facts': [], 'not_found': [],
            'implications': ['Answer about the content you were shown, in your own words.'], 'what_the_room_was_shown': ctx['shown'], '_asset_focus': True}


def is_asset_read(rec: dict) -> bool:
    return str((rec or {}).get('kind') or '') == ASSET_READ_KIND


def is_ab_read(rec: dict) -> bool:
    st = (rec or {}).get('stimulus')
    return bool(isinstance(st, dict) and st.get('ab_assets'))


def page_header(rec: dict) -> str:
    """The export page header for an asset read ('What the room made of
    A.'), never a question mark."""
    if is_asset_read(rec):
        return page_title(rec.get('asset_label') or (rec.get('stimulus') or {}).get('assets', [{}])[0].get('label') or 'the content') + '.'
    return ''


def page_eyebrow(rec: dict, n: Optional[int] = None) -> str:
    if is_asset_read(rec):
        return 'Content'
    if is_ab_read(rec):
        return 'A or B' if n is None else f"Question {n}, A or B"
    return ''


def assets_line(session: dict) -> str:
    """One line for the team notice: the content in the room."""
    items = []
    for a in session_assets(session):
        bit = f"{a.get('label')} ({a.get('kind')}"
        if a.get('filename') and a.get('source') != 'url':
            bit += f", {a.get('filename')}"
        bit += ')'
        if a.get('status') != STATUS_READY:
            bit += f" {a.get('status')}"
        items.append(bit)
    return ('Content in the room: ' + '; '.join(items)) if items else ''


def image_bytes(s3, asset: dict, n: int = 0) -> Optional[bytes]:
    """The thumbnail (n=0) or a page frame (n>=1) as bytes; None when
    the asset carries none."""
    if s3 is None or not isinstance(asset, dict):
        return None
    keys = list(asset.get('frame_keys') or [])
    key = asset.get('thumb_key') if n <= 0 else (keys[n - 1] if n - 1 < len(keys) else None)
    if not key:
        return None
    try:
        return s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read()
    except Exception:
        return None


def page_images(s3, session: dict, rec: dict, max_n: int = PAGE_FRAMES) -> list:
    """The images an export page shows for an asset read: the image
    itself, or up to four frames of a video; [] for audio."""
    if not is_asset_read(rec):
        return []
    asset = asset_by_id(session, str(rec.get('asset_id') or ''))
    if not asset:
        return []
    if asset.get('kind') == 'image':
        b = image_bytes(s3, asset, 0)
        return [b] if b else []
    if asset.get('kind') == 'video':
        out = []
        for i in range(1, min(max_n, len(asset.get('frame_keys') or [])) + 1):
            b = image_bytes(s3, asset, i)
            if b:
                out.append(b)
        return out
    return []


def transcript_for_page(session: dict, rec: dict, limit: int = 700) -> str:
    asset = asset_by_id(session, str((rec or {}).get('asset_id') or ''))
    if not asset:
        return ''
    ti = asset.get('taken_in') or {}
    return str(ti.get('transcript_excerpt') or '')[:limit]


def chat_intake_url(text: str) -> str:
    """The first direct file link in a chat line, '' when none."""
    for m in _URL_RX.finditer(str(text or '')):
        u = m.group(0).rstrip('.,);')
        if kind_for(os.path.basename(urllib.parse.urlparse(u).path)):
            return u
    return ''


__all__ = ['KINDS', 'MAX_ASSETS', 'ASSET_READ_KIND', 'STATUS_PROCESSING', 'STATUS_READY', 'STATUS_FAILED', 'FAILED_LINE', 'ASSET_CHIPS', 'AB_LABELS', 'IMPLIED',
           'kind_for', 'accept', 'asset_key', 'session_assets', 'ready_assets', 'asset_by_id', 'next_label', 'clean_label', 'public_asset', 'public_assets',
           'represented_people', 'store_upload', 'store_url', 'store_s3_copy', 'fetch_url', 'transcribe', 'transcript_lines_from_aws', 'transcript_excerpt',
           'describe_image', 'describe_video', 'describe_audio', 'take_in', 'process_asset', 'start_processing', 'needs_worker', 'sweep_assets',
           'assess_asset', 'appeal_options', 'seat_view', 'attach_question_assets', 'assets_named', 'is_comparison', 'ab_options', 'stimulus_context',
           'asset_brief', 'is_asset_read', 'is_ab_read', 'page_header', 'page_eyebrow', 'page_title', 'assets_line', 'image_bytes', 'page_images',
           'transcript_for_page', 'chat_intake_url', 'set_transcriber_fn', 'set_vision_fn', 'set_process_hook', 'banned', 'regression_mode']
