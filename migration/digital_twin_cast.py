"""Who is on screen: the cast the user entered, found in the frames.

Jenna, 2026-11-02 (the NCIS: New York pilot, verbatim): "it needs to
realize the woman in the tan blazer is jennifer beals and the two men
by the bridge are ll cool j and scott kahn. i input the cast so it
should have checked their IMDB or something to find out what they look
like and figured it out by facial recognition."

A Pilot Testing session carries ``cast`` (the names the user typed).
Before the room reads a frame, the names are resolved once per test:

1. the actor's canonical name (one small model call with the title as
   context, so 'Scott Cahn' in an NCIS pilot resolves to Scott Caan);
2. a reference headshot for each (the Wikipedia summary of the actor's
   page; cached in S3 so the box and the dashboard share it);
3. whether the face index already knows the actor as a public figure
   (one celebrity-recognition probe on the headshot).

Each frame is then read for faces (AWS Rekognition): celebrity
recognition first, then the reference headshots (CompareFaces) for any
cast member the index does not know. A match is kept only when it is
on the cast list and the face is clear. The result rides into the
vision prompts as plain notes ("On screen, confirmed by face: Jennifer
Beals (center), Scott Caan (right)") so the captions, the seats' lines,
the moment under a question and the report name the cast. Never a name
that is not on the list, never a name without a face match.

Fail-safe end to end: no AWS, no headshot, no match, a disabled
switch (DTIQ_CAST_FACES=0) or REGRESSION_TEST_MODE, and the captions
read as before, by look and role.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

S3_BUCKET = 'dashboard-inputs'
FACE_PREFIX = 'system/digital_twins/cast_faces/'
CELEB_MIN = 90.0        # celebrity recognition match confidence, 0 to 100
SIMILARITY_MIN = 86.0   # CompareFaces similarity against the reference headshot
FACE_MIN_W = 0.04       # a face narrower than 4% of the frame is too small to name
MAX_REFERENCE_CALLS = 8 # CompareFaces calls per frame (one per unrecognized-by-index cast member)
UA = {'User-Agent': 'Crosswalk-PilotTesting/1.0 (jenna@crosswalknyc.com)'}
_CACHE_LOCK = threading.Lock()
_FRAME_CACHE: dict = {}      # sha1 of the frame bytes -> matches (per process; a batch retry never re-reads)
_FRAME_CACHE_MAX = 600
_HEADSHOT_MEM: dict = {}     # s3 key -> bytes (per process)

_AS_RX = re.compile(r"^\s*(?P<actor>[^()]+?)\s+(?:as|playing)\s+(?P<char>.+?)\s*$", re.I)
_PAREN_RX = re.compile(r"^\s*(?P<actor>[^()]+?)\s*\((?P<char>[^()]+)\)\s*$")
_DASH_RX = re.compile(r"^\s*(?P<actor>[^-:]+?)\s*[-:]\s*(?P<char>[^-:]+?)\s*$")

RESOLVE_SYSTEM = (
    "You resolve a TV or film cast list a user typed, for a test screening. For each entry give the actor's correct "
    "full professional name (fix misspellings; use the title as context to pick the right person), the character "
    "name when the entry gives one or when you are certain of it for this title, and the English Wikipedia article "
    "title for the actor when it differs from the name (for example 'Scott Caan'). Never invent a character; leave "
    "it empty when unsure. Return JSON only: {\"cast\": [{\"entered\": \"...\", \"actor\": \"...\", \"character\": \"\", \"wikipedia\": \"\"}]}"
)


# ------------------------------------------------------------- switches
def enabled() -> bool:
    if (os.environ.get('DTIQ_CAST_FACES') or '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    return (os.environ.get('REGRESSION_TEST_MODE') or '').strip().lower() not in ('1', 'true', 'yes', 'on')


def _region() -> str:
    return (os.environ.get('DTIQ_REKOGNITION_REGION') or os.environ.get('AWS_REGION') or 'us-east-2').strip()


_REK = None
_REK_LOCK = threading.Lock()


def _rekognition():
    global _REK
    with _REK_LOCK:
        if _REK is None:
            import boto3
            _REK = boto3.client('rekognition', region_name=_region())
        return _REK


def _s3_default():
    try:
        from prometheus.host import host
        if host.has('s3_client'):
            return host.s3_client
    except Exception:
        pass
    try:
        import boto3
        return boto3.client('s3')
    except Exception:
        return None


# ------------------------------------------------------------ the names
def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(s or '').lower())
    return ' '.join(s.split())


def same_person(a: str, b: str) -> bool:
    """Two spellings of one name: equal after folding, or one is the
    other with an initial, a middle name or a suffix dropped, or they
    differ by a short edit (Cahn / Caan)."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = na.split(), nb.split()
    if ta and tb and ta[0] == tb[0] and ta[-1] == tb[-1]:
        return True
    if len(ta) == len(tb) and all(_close(x, y) for x, y in zip(ta, tb)):
        return True
    return False


def _close(x: str, y: str) -> bool:
    if x == y:
        return True
    if abs(len(x) - len(y)) > 1 or min(len(x), len(y)) < 3:
        return False
    # one substitution, insertion or deletion
    if len(x) == len(y):
        return sum(1 for p, q in zip(x, y) if p != q) <= 1
    a, b = (x, y) if len(x) < len(y) else (y, x)
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def parse_cast(entries) -> list:
    """[{entered, actor, character}] from the typed list. 'LL Cool J as
    Sam Hanna', 'Jennifer Beals (Lena)', 'Scott Caan - Danny' all carry
    a character; a bare name carries none."""
    out = []
    seen = set()
    for raw in (entries or []):
        text = ' '.join(str(raw or '').split())[:80]
        if not text:
            continue
        actor, char = text, ''
        for rx in (_AS_RX, _PAREN_RX, _DASH_RX):
            m = rx.match(text)
            if m:
                actor, char = m.group('actor').strip(), m.group('char').strip()
                break
        key = norm_name(actor)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({'entered': text, 'actor': actor, 'character': char[:60]})
    return out[:16]


def resolve_names(people: list, title: str, *, models=None, usage_extras: Optional[dict] = None) -> list:
    """The canonical actor names through one small model call; the typed
    names stand when no model answers. A model answer that is not the
    same person as the typed name is ignored for that entry."""
    if not people:
        return people
    try:
        from migration.digital_twin_room import Models, model_available, regression_mode
    except Exception:
        return people
    try:
        if regression_mode() and models is None:
            return people
        m = models or Models(usage_extras=usage_extras)
        if m.fn is None and not model_available():
            return people
        user = json.dumps({'title': str(title or '')[:120], 'cast': [p['entered'] for p in people]})
        data, _call = m.call('cast_names', RESOLVE_SYSTEM, user, max_tokens=700, temperature=0.0, tools=None)
        rows = (data or {}).get('cast') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return people
        by_entered = {}
        for r in rows:
            if isinstance(r, dict):
                by_entered[norm_name(r.get('entered'))] = r
        for i, p in enumerate(people):
            r = by_entered.get(norm_name(p['entered'])) or (rows[i] if i < len(rows) and isinstance(rows[i], dict) else None)
            if not r:
                continue
            actor = ' '.join(str(r.get('actor') or '').split())[:80]
            if actor and (same_person(actor, p['actor']) or _initials_match(actor, p['actor'])):
                p['actor'] = actor
            char = ' '.join(str(r.get('character') or '').split())[:60]
            if char and not p.get('character'):
                p['character'] = char
            wiki = ' '.join(str(r.get('wikipedia') or '').split())[:120]
            if wiki:
                p['wikipedia'] = wiki
    except Exception as e:
        print(f"[twin-cast] name resolution skipped: {e}")
    return people


def _initials_match(a: str, b: str) -> bool:
    """'LL Cool J' against 'L.L. Cool J' and the like."""
    return re.sub(r"[^a-z0-9]", "", norm_name(a)) == re.sub(r"[^a-z0-9]", "", norm_name(b))


# ------------------------------------------------------------ headshots
def _http_json(url: str, timeout: float = 12.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _http_bytes(url: str, timeout: float = 20.0, limit: int = 4_500_000) -> Optional[bytes]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        b = r.read(limit + 1)
    return b if 0 < len(b) <= limit else None


def wikipedia_headshot(name: str, wiki_title: str = '') -> tuple:
    """(image bytes, page title) for the actor's Wikipedia page image,
    (None, '') when the page has none or is not a person."""
    tries = [t for t in (wiki_title, name, f"{name} (actor)", f"{name} (actress)") if t]
    for t in tries:
        try:
            d = _http_json('https://en.wikipedia.org/api/rest_v1/page/summary/' + urllib.parse.quote(t.replace(' ', '_')))
        except Exception:
            continue
        if str(d.get('type') or '') == 'disambiguation':
            continue
        desc = str(d.get('description') or '').lower()
        if desc and not re.search(r"actor|actress|rapper|singer|musician|comedian|performer|host|presenter|model|personality|entertainer|artist|star|wrestler|athlete|player", desc):
            # a page that is not a person (a band, a film) never serves as a face
            if re.search(r"film|series|album|band|company|song|novel|episode|city|river", desc):
                continue
        orig = d.get('originalimage') if isinstance(d.get('originalimage'), dict) else {}
        thumb = d.get('thumbnail') if isinstance(d.get('thumbnail'), dict) else {}
        urls = []
        if orig.get('source') and int(orig.get('width') or 0) <= 2400:
            urls.append(orig['source'])
        if thumb.get('source'):
            urls.append(thumb['source'])
        for u in urls:
            try:
                b = _http_bytes(u)
            except Exception:
                b = None
            if b:
                return b, str(d.get('title') or t)
    return None, ''


def _face_key(actor: str) -> str:
    return FACE_PREFIX + hashlib.sha1(norm_name(actor).encode('utf-8')).hexdigest()[:20] + '.jpg'


def headshot_for(person: dict, s3=None) -> Optional[bytes]:
    """The reference headshot bytes: the per-process cache, the S3
    cache, then Wikipedia (and the S3 cache is filled)."""
    key = _face_key(person.get('actor') or person.get('entered') or '')
    with _CACHE_LOCK:
        if key in _HEADSHOT_MEM:
            return _HEADSHOT_MEM[key]
    s3 = s3 if s3 is not None else _s3_default()
    data = None
    if s3 is not None:
        try:
            data = s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read()
        except Exception:
            data = None
    if not data:
        data, page = wikipedia_headshot(person.get('actor') or '', person.get('wikipedia') or '')
        if data:
            person['headshot_page'] = page
            if s3 is not None:
                try:
                    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType='image/jpeg')
                except Exception as e:
                    print(f"[twin-cast] headshot cache write skipped: {e}")
    if data:
        person['headshot_key'] = key
        with _CACHE_LOCK:
            _HEADSHOT_MEM[key] = data
    return data


# -------------------------------------------------------------- the index
def probe_celebrity(headshot: bytes) -> tuple:
    """(name, id) when the face index knows the person on the headshot
    as a public figure, else ('', '')."""
    try:
        r = _rekognition().recognize_celebrities(Image={'Bytes': headshot})
    except Exception as e:
        print(f"[twin-cast] celebrity probe failed: {str(e)[:120]}")
        return '', ''
    best = None
    for c in r.get('CelebrityFaces') or []:
        if best is None or float(c.get('MatchConfidence') or 0) > float(best.get('MatchConfidence') or 0):
            best = c
    if best and float(best.get('MatchConfidence') or 0) >= CELEB_MIN:
        return str(best.get('Name') or ''), str(best.get('Id') or '')
    return '', ''


def cast_signature(entries, title: str = '') -> str:
    return hashlib.sha1(json.dumps({'cast': [str(e) for e in (entries or [])], 'title': str(title or '')}).encode('utf-8')).hexdigest()[:16]


def people_for(sess: dict, *, title: str = '', models=None, usage_extras: Optional[dict] = None, s3=None) -> list:
    """The resolved cast for a test, prepared once and kept on the
    session as ``cast_faces`` (names, character, headshot key, whether
    the index knows them). [] when the test has no cast or the switch is
    off. Never raises."""
    try:
        entries = [e for e in (sess.get('cast') or []) if str(e or '').strip()]
        if not entries or not enabled():
            return []
        sig = cast_signature(entries, title)
        cached = sess.get('cast_faces') if isinstance(sess.get('cast_faces'), dict) else None
        if cached and cached.get('sig') == sig and isinstance(cached.get('people'), list):
            return [p for p in cached['people'] if isinstance(p, dict)]
        people = parse_cast(entries)
        people = resolve_names(people, title, models=models, usage_extras=usage_extras)
        s3 = s3 if s3 is not None else _s3_default()
        for p in people:
            shot = headshot_for(p, s3=s3)
            p['has_headshot'] = bool(shot)
            if shot:
                name, cid = probe_celebrity(shot)
                if name and (same_person(name, p['actor']) or _initials_match(name, p['actor'])):
                    p['celebrity'] = name
                    p['celebrity_id'] = cid
                    if name != p['actor']:
                        p['actor'] = name       # the index's spelling is the public one
                else:
                    p['celebrity'] = ''
            else:
                p['celebrity'] = ''
        sess['cast_faces'] = {'sig': sig, 'people': people, 'prepared_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        print(f"[twin-cast] cast prepared for {sess.get('id')}: " + ', '.join(
            f"{p['actor']}" + (' (index)' if p.get('celebrity') else ' (headshot)' if p.get('has_headshot') else ' (no face on file)') for p in people))
        return people
    except Exception as e:
        print(f"[twin-cast] cast preparation skipped: {e}")
        return []


# ------------------------------------------------------------- the frames
def _pos(box: dict) -> str:
    try:
        cx = float(box.get('Left') or 0) + float(box.get('Width') or 0) / 2.0
    except (TypeError, ValueError):
        return ''
    return 'left' if cx < 0.38 else 'right' if cx > 0.62 else 'center'


def _box(b: dict) -> dict:
    return {k.lower(): round(float(b.get(k) or 0), 4) for k in ('Left', 'Top', 'Width', 'Height')}


def _frame_bytes(image) -> Optional[bytes]:
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    if isinstance(image, str):
        s = image
        if ',' in s[:64] and s.lstrip().startswith('data:'):
            s = s.split(',', 1)[1]
        try:
            import base64
            return base64.b64decode(s, validate=False)
        except Exception:
            return None
    return None


def faces_in_frame(image, people: list, *, s3=None) -> list:
    """[{actor, character, confidence, box, pos, how}] for the cast
    members whose faces are in this frame. Celebrity recognition covers
    the people the index knows (one call); the reference headshots cover
    the rest (one CompareFaces call each, in parallel). Cached per frame
    within the process. Never raises; [] when nothing can be read."""
    raw = _frame_bytes(image)
    if not raw or not people or not enabled():
        return []
    digest = hashlib.sha1(raw).hexdigest()
    with _CACHE_LOCK:
        if digest in _FRAME_CACHE:
            return list(_FRAME_CACHE[digest])
    out = []
    try:
        known = [p for p in people if p.get('celebrity')]
        by_face = [p for p in people if not p.get('celebrity') and p.get('has_headshot')]
        if known:
            try:
                r = _rekognition().recognize_celebrities(Image={'Bytes': raw})
                for c in r.get('CelebrityFaces') or []:
                    conf = float(c.get('MatchConfidence') or 0)
                    if conf < CELEB_MIN:
                        continue
                    name = str(c.get('Name') or '')
                    hit = next((p for p in known if norm_name(p['celebrity']) == norm_name(name) or same_person(p['actor'], name)), None)
                    if not hit:
                        continue
                    b = (c.get('Face') or {}).get('BoundingBox') or {}
                    if float(b.get('Width') or 0) < FACE_MIN_W:
                        continue
                    out.append({'actor': hit['actor'], 'character': hit.get('character') or '', 'confidence': round(conf, 1),
                                'box': _box(b), 'pos': _pos(b), 'how': 'index'})
            except Exception as e:
                print(f"[twin-cast] celebrity read failed: {str(e)[:120]}")
        if by_face:
            s3 = s3 if s3 is not None else _s3_default()

            def one(p):
                shot = headshot_for(p, s3=s3)
                if not shot:
                    return None
                try:
                    r = _rekognition().compare_faces(SourceImage={'Bytes': shot}, TargetImage={'Bytes': raw}, SimilarityThreshold=SIMILARITY_MIN - 6)
                except Exception as e:
                    print(f"[twin-cast] face compare failed for {p.get('actor')}: {str(e)[:120]}")
                    return None
                best = None
                for m in r.get('FaceMatches') or []:
                    if best is None or float(m.get('Similarity') or 0) > float(best.get('Similarity') or 0):
                        best = m
                if not best or float(best.get('Similarity') or 0) < SIMILARITY_MIN:
                    return None
                b = (best.get('Face') or {}).get('BoundingBox') or {}
                if float(b.get('Width') or 0) < FACE_MIN_W:
                    return None
                return {'actor': p['actor'], 'character': p.get('character') or '', 'confidence': round(float(best['Similarity']), 1),
                        'box': _box(b), 'pos': _pos(b), 'how': 'headshot'}
            work = by_face[:MAX_REFERENCE_CALLS]
            with ThreadPoolExecutor(max_workers=min(4, len(work))) as pool:
                for hit in pool.map(one, work):
                    if hit:
                        out.append(hit)
        out = _dedupe(out)
    except Exception as e:
        print(f"[twin-cast] frame read skipped: {e}")
        out = []
    with _CACHE_LOCK:
        if len(_FRAME_CACHE) >= _FRAME_CACHE_MAX:
            _FRAME_CACHE.clear()
        _FRAME_CACHE[digest] = list(out)
    return out


def _dedupe(matches: list) -> list:
    """One name per face and one face per name: overlapping boxes keep
    the stronger match."""
    kept = []
    for m in sorted(matches, key=lambda x: -float(x.get('confidence') or 0)):
        if any(k['actor'] == m['actor'] for k in kept):
            continue
        if any(_overlap(k['box'], m['box']) > 0.5 for k in kept):
            continue
        kept.append(m)
    kept.sort(key=lambda x: float((x.get('box') or {}).get('left') or 0))
    return kept


def _overlap(a: dict, b: dict) -> float:
    ax0, ay0, aw, ah = a.get('left', 0), a.get('top', 0), a.get('width', 0), a.get('height', 0)
    bx0, by0, bw, bh = b.get('left', 0), b.get('top', 0), b.get('width', 0), b.get('height', 0)
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    small = min(aw * ah, bw * bh) or 1e-9
    return inter / small


# --------------------------------------------------------------- the words
def label(m: dict) -> str:
    """'Jennifer Beals' or 'LL Cool J as Sam Hanna'."""
    a = str(m.get('actor') or '').strip()
    c = str(m.get('character') or '').strip()
    return f"{a} as {c}" if c else a


def on_screen_line(matches: list) -> str:
    """The note the vision prompt carries for one frame."""
    if not matches:
        return 'No cast member is recognizable in this frame; describe anyone by look or role.'
    bits = [label(m) + (f" ({m['pos']})" if m.get('pos') else '') for m in matches]
    return 'On screen, confirmed by face: ' + ', '.join(bits) + '.'


def names(matches: list) -> list:
    return [str(m.get('actor') or '') for m in (matches or []) if m.get('actor')]


PROMPT_RULE = (
    "When the frame's notes list cast on screen (confirmed by face), name them by those names, exactly as given, "
    "in what you write about that frame; anyone the notes do not list is described by look or role. Never name an "
    "actor the notes do not give for that frame, and never guess from the face yourself."
)


__all__ = ['enabled', 'parse_cast', 'resolve_names', 'same_person', 'norm_name', 'wikipedia_headshot', 'headshot_for', 'probe_celebrity',
           'people_for', 'faces_in_frame', 'on_screen_line', 'names', 'label', 'cast_signature', 'PROMPT_RULE',
           'CELEB_MIN', 'SIMILARITY_MIN', 'FACE_MIN_W', 'MAX_REFERENCE_CALLS']
