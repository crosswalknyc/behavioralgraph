"""Portraits for the twins in a Focus Group IQ room (2026-10-26, Jenna:
"can you use our type of photos to have photos of the panelists on
here to make it visually more appealing").

A twin in the room is a fictional composite: a name, an age, a city, a
segment (``digital_twin_panel.build_group``). It is never a real
person, so the subject-photo pipeline (``image_backfill.py``,
``deck_builder._fetch_photos``: Wikipedia and web images of a real
subject) is never used here. A real person's face labeled as a twin is
not acceptable. Every portrait is GENERATED: photoreal, fictional,
consistent with the seat's age and gender presentation, in the house
look (candid and observed, natural light, a real place rather than a
studio, a neutral to slightly warm grade that sits on Graphite Teal
#0C1618), and every prompt states that the person is fictional and
must not resemble any real or public figure. The prompt never carries
the seat's name, the segment label or the twin's subject (a subject
name would pull the generator toward its cast).

How it runs:

- ``schedule_room_portraits(s3, key, sess, twins_by_key)`` is called
  when a room opens. It starts one daemon thread; the room answers in
  seconds and the faces land in the session document when they
  finish. At most ``MAX_PER_OPEN`` (12) generations per room open; a
  seat beyond the cap waits for the next open.
- The cache is content-addressed: ``portrait_hash(seat)`` is the sha256
  of (name, age, gender, city, segment, STYLE_VERSION). The 512 px
  JPEG lives at ``digital-twins/portraits/<hash>.jpg``, the 128 px
  thumb at ``<hash>_128.jpg``, a small sidecar at ``<hash>.json``
  (style version, model, quality, created_at, generation, prompt). A
  second room with the same twins reuses the cache and generates
  nothing.
- When a portrait exists the roster entry gains ``portrait_url``
  (``/api/digital-twins/portrait/<hash>``) and ``portrait_hash``; the
  session document is re-written in place (same key, same session id)
  and the voice records that name the seat gain ``seat`` and the same
  url. ``attach_portraits`` is the cheap self-heal the GET route runs so
  a re-poll picks up faces that finished since the last write.
- Failure path: the seat simply has no ``portrait_url`` (the UI shows an
  initials avatar). A fault note is logged; nothing raises into a
  request. Kill switch ``DTIQ_PORTRAITS=0``. Hermetic tests inject a
  generator with ``configure(generator=..., background=False)`` and
  never reach the network.

Generation goes to the OpenAI images API with the deploy env's
``OPENAI_API_KEY`` at 1024x1024, quality low by default
(``DTIQ_PORTRAIT_QUALITY``), as the plain HTTPS call the SDK makes, so
the same code runs on an interpreter with the 0.x SDK (this Mac) and
the 1.x SDK (Render, the build server). The model is a cascade:
``DTIQ_PORTRAIT_MODEL`` or ``gpt-image-1.5`` first (on 2026-10-26 the
account answered "The model 'gpt-image-1' does not exist" for
generation and ``dall-e-3`` is no longer listed), then ``gpt-image-1``,
``gpt-image-1-mini``, ``gpt-image-2``, ``dall-e-3``; a model the API
says the account lacks is remembered as dead for the process. The
result is downscaled with Pillow to 512 and 128 JPEGs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
from typing import Callable, Optional

PREFIX = 'digital-twins/portraits/'
BUCKET = 'dashboard-inputs'
STYLE_VERSION = 'v1'
MODEL = 'gpt-image-1.5'
FALLBACK_MODELS = ('gpt-image-1', 'gpt-image-1-mini', 'gpt-image-2', 'dall-e-3')
GEN_SIZE = '1024x1024'
OUT_SIZE = 512
THUMB_SIZE = 128
MAX_PER_OPEN = 12
WORKERS = 3
URL_BASE = '/api/digital-twins/portrait/'
_ENDPOINT = 'https://api.openai.com/v1/images/generations'

_cfg = {'generator': None, 'background': True, 'verify_delay': 1.5}
_dead_models: set = set()  # models the API said this account lacks (per process)
_known: dict = {}          # hash -> generation, in this process (uploaded or seen on S3)
_miss_at: dict = {}        # hash -> monotonic time of the last S3 miss (HEAD throttle)
_MISS_TTL_S = 45.0
_lock = threading.Lock()
_img_cache: dict = {}      # (hash, size) -> bytes, small LRU for the image route
_IMG_CACHE_MAX = 192


# ------------------------------------------------------------- settings
def configure(generator: Optional[Callable] = None, background: Optional[bool] = None, verify_delay: Optional[float] = None) -> None:
    """Tests: inject the generator (prompt -> image bytes, or (bytes,
    model)), run the room pass inline, skip the read-back delay.
    ``configure()`` with no arguments restores the defaults."""
    _cfg['generator'] = generator
    _cfg['background'] = True if background is None else bool(background)
    _cfg['verify_delay'] = 1.5 if verify_delay is None else max(0.0, float(verify_delay))


def enabled() -> bool:
    """Off with ``DTIQ_PORTRAITS=0``; off in the regression harness and
    without a key unless a generator was injected."""
    if str(os.environ.get('DTIQ_PORTRAITS', '1')).strip().lower() in ('0', 'false', 'off', 'no'):
        return False
    if _cfg.get('generator') is not None:
        return True
    if str(os.environ.get('REGRESSION_TEST_MODE') or '').strip() in ('1', 'true', 'yes'):
        return False
    return bool(str(os.environ.get('OPENAI_API_KEY') or '').strip())


def quality() -> str:
    q = str(os.environ.get('DTIQ_PORTRAIT_QUALITY') or 'low').strip().lower()
    return q if q in ('low', 'medium', 'high') else 'low'


def model_cascade() -> list:
    """The models to try, in order: the configured one, then the
    fallbacks, minus the ones this process has seen refused."""
    first = str(os.environ.get('DTIQ_PORTRAIT_MODEL') or MODEL).strip() or MODEL
    order = [first] + [m for m in FALLBACK_MODELS if m != first]
    live = [m for m in order if m not in _dead_models]
    return live or order


def _note(label, exc=None, detail=''):
    try:
        from prometheus import faults as _faults
        _faults.note(label, exc, detail)
    except Exception:
        try:
            print(f"[fault] {label} {type(exc).__name__ + ': ' + str(exc) if exc is not None else detail}")
        except Exception:
            pass


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ---------------------------------------------------------------- the hash
def _norm(v) -> str:
    return ' '.join(str(v if v is not None else '').split()).strip().lower()


def portrait_hash(seat: dict, style_version: str = STYLE_VERSION) -> str:
    """sha256 of (name, age, gender, city, segment, style_version), hex."""
    parts = (_norm(seat.get('name')), str(int(float(seat.get('age') or 0))), _norm(seat_gender(seat)), _norm(seat.get('city')),
             _norm(seat.get('segment')), str(style_version))
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def portrait_key(h: str, size: int = OUT_SIZE) -> str:
    return f"{PREFIX}{h}.jpg" if int(size) != THUMB_SIZE else f"{PREFIX}{h}_{THUMB_SIZE}.jpg"


def sidecar_key(h: str) -> str:
    return f"{PREFIX}{h}.json"


def portrait_url(h: str, generation: int = 1) -> str:
    """The route the UI renders; a regenerated image carries ``?g=N`` so a
    browser that cached the first one fetches the new bytes."""
    g = int(generation or 1)
    return f"{URL_BASE}{h}" + (f"?g={g}" if g > 1 else '')


# ---------------------------------------------------- the seat, as a brief
def seat_gender(seat: dict, female_share: Optional[float] = None) -> str:
    """'F' or 'M'. The roster carries it; when a record lacks it the
    draw is deterministic from the room's split (never from the first
    name)."""
    g = str((seat or {}).get('gender') or '').strip().upper()[:1]
    if g in ('F', 'M'):
        return g
    from migration.digital_twin_engine import unit
    share = 0.5 if female_share is None else max(0.0, min(1.0, float(female_share)))
    u = unit('portrait-gender', _norm(seat.get('segment')), str(seat.get('seat') or ''), str(seat.get('age') or ''), _norm(seat.get('city')))
    return 'F' if u < share else 'M'


_STATE_RX = re.compile(r"\b(Al|Ak|Az|Ar|Ca|Co|Ct|De|Dc|Fl|Ga|Hi|Id|Il|In|Ia|Ks|Ky|La|Me|Md|Ma|Mi|Mn|Ms|Mo|Mt|Ne|Nv|Nh|Nj|Nm|Ny|Nc|Nd|Oh|Ok|Or|Pa|Ri|Sc|Sd|Tn|Tx|Ut|Vt|Va|Wa|Wv|Wi|Wy)\b")
_TWO_WORD_STARTS = ('San', 'Los', 'New', 'St.', 'St', 'Fort', 'Ft', 'Ft.', 'Kansas', 'Oklahoma', 'Las', 'Grand', 'Santa', 'Colorado', 'Virginia', 'Little', 'Baton', 'Corpus',
                    'El', 'Des', 'Sioux', 'Cedar', 'Green', 'Winston', 'Palm', 'Daytona', 'Myrtle', 'Panama', 'Lake', 'Long', 'Cape', 'Port', 'Mount', 'Saint', 'West', 'North', 'South', 'East')
_THREE_WORD = ('Salt Lake City', 'New York City', 'West Palm Beach', 'Fort Walton Beach')


def city_flavor(city: str) -> str:
    """A plain city name from a roster city or a DMA label: 'Boston Ma
    Manchester Nh' -> 'Boston', 'Dallas Ft Worth' -> 'Dallas', 'San
    Francisco Oakland San Jose' -> 'San Francisco'. Setting flavor only."""
    s = ' '.join(str(city or '').replace('-', ' ').split())
    if not s:
        return ''
    head = _STATE_RX.split(s)[0].strip(' ,')
    if not head:
        head = s
    for t in _THREE_WORD:
        if head.lower().startswith(t.lower()):
            return t
    words = head.split()
    if words[0] in _TWO_WORD_STARTS and len(words) >= 2:
        out = ' '.join(words[:2])
    else:
        out = words[0]
    return out.replace('Ft.', 'Fort').replace('Ft', 'Fort').replace('St ', 'St. ')


_ETHNICITY_WORDS = (
    (re.compile(r"\bwhite\b|\bcaucasian\b", re.I), ('white',)),
    (re.compile(r"\bblack\b|african", re.I), ('Black',)),
    (re.compile(r"hispanic|latin", re.I), ('Hispanic',)),
    (re.compile(r"\basian\b", re.I), ('East Asian American', 'South Asian American', 'Southeast Asian American')),
    (re.compile(r"native|american indian|alaska|hawaiian|pacific", re.I), ('Native American', 'Pacific Islander')),
    (re.compile(r"middle east|arab", re.I), ('Middle Eastern', )),
    (re.compile(r"two or more|multi|mixed|another|other", re.I), ('mixed-race', 'Middle Eastern', 'Native American')),
)


def appearance_word(seat: dict, h: str, demographics: Optional[dict]) -> str:
    """One plain descriptor drawn from the twin's own ethnicity split
    (the room should look like the audience it stands for), by a draw
    keyed on the seat hash, never from the name. '' when the twin has
    no ethnicity rows: the generator varies on its own."""
    rows = [r for r in ((demographics or {}).get('ethnicity') or []) if isinstance(r, dict) and r.get('label') is not None]
    if not rows:
        return ''
    from migration.digital_twin_engine import unit
    tot = sum(max(0.0, float(r.get('pct') or 0)) for r in rows) or 100.0
    u = unit('portrait-appearance', h)
    acc = 0.0
    pick = rows[-1]
    for r in rows:
        acc += max(0.0, float(r.get('pct') or 0)) / tot
        if u <= acc:
            pick = r
            break
    label = str(pick.get('label') or '')
    for rx, words in _ETHNICITY_WORDS:
        if rx.search(label):
            return words[int(unit('portrait-appearance-sub', h) * len(words)) % len(words)]
    return ''


_SETTINGS = (
    ('at a kitchen table by the window, morning light, a coffee mug in frame', 0),
    ('on the front steps of a brick house, overcast afternoon light', 0),
    ('at a diner counter, soft window light, the street blurred behind', 0),
    ('on a commuter rail platform, grey sky, a coat collar turned up', 0),
    ('in a neighborhood coffee shop by the window, a cup in hand', 0),
    ('on a back porch, late afternoon light, trees soft behind', 0),
    ('in a bookstore aisle, warm shelf light', 0),
    ('on a city sidewalk mid-stride, storefronts softened behind', 0),
    ('on a living room couch, lamp light in the evening', 0),
    ('at a farmers market stall, overcast daylight', 0),
    ('in a parked car with the window down, soft daylight on the face', 0),
    ('in a laundromat by the window, daylight mixed with the overhead light', 0),
    ('on a park bench, a dog leash in hand, overcast light', 0),
    ('in a grocery store aisle, the overhead light softened', 0),
    ('leaning on a chain-link fence at a weekend soccer field, grey morning', 0),
    ('at a bus stop under the shelter, flat grey light', 0),
    ('in a home office corner, window light, a plant soft behind', 0),
    ('walking a dog on a residential street, overcast', 0),
    ('at a kitchen counter cooking, window light and a little steam', 0),
    ('on an apartment balcony, the city soft behind, early evening', 0),
    ('in the stands at a minor league ballgame, overcast afternoon', 0),
    ('at a bar top, warm low light, the room blurred behind', 24),
)
_FRAMING = (
    'head and shoulders, turned three-quarters, eyes on something just past the lens',
    'head and shoulders, facing slightly left, looking off frame as if listening to someone',
    'tight head and shoulders, a calm glance toward the camera, not posed',
    'head and shoulders, caught mid-laugh, looking down and away',
    'head and shoulders, chin resting on a hand, mid-thought',
    'head and shoulders from a slight side angle, turned to speak to someone outside the frame',
    'head and shoulders, looking up from a phone, half surprised',
    'head and shoulders, leaning on an elbow, a small unforced smile toward the camera',
)
_EXPRESSION = ('mid-thought', 'a small, unforced smile', 'listening closely', 'about to say something', 'quietly amused',
               'calm and a little tired at the end of the day', 'skeptical, one eyebrow slightly raised', 'warm and open')
_WARDROBE = ('a chunky knit sweater', 'a denim jacket over a plain tee', 'a cardigan over a tee', 'a plaid flannel shirt', 'a hoodie', 'a simple blouse',
             'a trench coat', 'a work shirt with the sleeves rolled', 'a puffer vest over a long sleeve', 'a crewneck sweatshirt', 'a linen shirt', 'a turtleneck')
_HAIR_F = ('worn down', 'pulled back', 'in a short bob', 'in natural curls', 'in braids', 'in a loose bun', 'in shoulder-length waves', 'in a ponytail', 'in a pixie cut', 'in a long single braid', 'cropped close')
_HAIR_M = ('cut short', 'in a buzz cut', 'in curls', 'swept back', 'in a fade, with a short beard', 'shaved, with a beard', 'medium length', 'in locs', 'cropped close, with glasses', 'thinning on top')
_HAIR_COLOR_YOUNG = ('dark brown', 'black', 'blonde', 'auburn', 'light brown', 'copper', 'dark blonde', 'jet black')
_HAIR_COLOR_OLDER = ('grey-streaked', 'silver', 'dark, grey at the temples', 'blonde going grey', 'white', 'salt-and-pepper')
_FEATURES = (
    ('freckles', 0), ('a strong jaw', 0), ('a round face', 0), ('wire-rim glasses', 0), ('a gap between the front teeth', 0), ('high cheekbones', 0), ('a broad nose', 0),
    ('a dimpled chin', 0), ('fine lines around the eyes', 38), ('thick eyebrows', 0), ('a long face', 0), ('a small scar through one eyebrow', 0), ('full cheeks', 0),
    ('a narrow face', 0), ('hooded eyes', 0), ('a wide smile', 0), ('deep laugh lines', 45), ('a prominent nose', 0), ('a soft double chin', 30), ('thick-framed glasses', 0),
)


def _pick(bank, name: str, h: str, variant: int):
    from migration.digital_twin_engine import unit
    n = len(bank)
    return bank[int(unit('portrait-' + name, h, int(variant or 1)) * n) % n]


def build_prompt(seat: dict, demographics: Optional[dict] = None, h: Optional[str] = None, variant: int = 1) -> str:
    """The brief for one seat. Age and gender presentation from the seat
    record, the appearance word from the twin's own split, the city as
    setting flavor only; the hair (color and style), one distinguishing
    feature, the wardrobe, setting, framing and expression drawn
    deterministically from the seat hash so a room of twelve does not
    look stamped. ``variant`` > 1 (a regeneration) re-draws those while
    the person stays the same age, gender and appearance. Never the
    name, the segment or the subject."""
    h = h or portrait_hash(seat)
    v = int(variant or 1)
    age = int(float(seat.get('age') or 35))
    g = seat_gender(seat)
    noun = 'woman' if g == 'F' else 'man'
    who = appearance_word(seat, h, demographics)
    article = 'an' if who[:1].lower() in ('a', 'e', 'i', 'o', 'u') else 'a'
    person = f"{article} {who} {noun}" if who else ('a woman' if g == 'F' else 'a man')
    setting = _pick([s for s, min_age in _SETTINGS if age >= min_age], 'setting', h, v)
    framing = _pick(_FRAMING, 'framing', h, v)
    expression = _pick(_EXPRESSION, 'expression', h, v)
    wardrobe = _pick(_WARDROBE, 'wardrobe', h, v)
    style = _pick(_HAIR_F if g == 'F' else _HAIR_M, 'hair', h, v)
    color = _pick(_HAIR_COLOR_OLDER if age >= 55 else (_HAIR_COLOR_YOUNG + _HAIR_COLOR_OLDER[:3] if age >= 45 else _HAIR_COLOR_YOUNG), 'hair-color', h, v)
    feature = _pick([f for f, min_age in _FEATURES if age >= min_age], 'feature', h, v)
    hair = f"{color} hair {style}"
    city = city_flavor(seat.get('city'))
    where = f"{setting}, in {city}" if city else setting
    return (
        f"A candid editorial photograph of a fictional person who does not exist and must not resemble any real person, actor, or public figure: "
        f"{person}, about {age} years old and looking that age, with {hair}, {feature}, wearing {wardrobe}. "
        f"Setting: {where}. Framing: {framing}. Expression: {expression}. "
        "Natural light only, shallow depth of field, a real place rather than a studio backdrop, the background soft and unposed. "
        "Color grade neutral to slightly warm, muted and filmic, soft contrast, made to sit on a dark teal interface. "
        "Photoreal, unretouched skin, an ordinary person caught in a moment. "
        "No text, no captions, no logos, no watermark, no heavy filter, no vignette, no ring light, no glamour retouching. Square crop."
    )


# ---------------------------------------------------------------- generate
def _openai_image_b64(prompt: str, model: str, q: str, timeout: float = 120.0) -> str:
    """One call to the images API; the base64 image. Raises on failure."""
    key = str(os.environ.get('OPENAI_API_KEY') or '').strip()
    if not key:
        raise RuntimeError('no image key in the environment')
    import requests
    body = {'model': model, 'prompt': prompt, 'n': 1, 'size': GEN_SIZE}
    if model.startswith('gpt-image') or model.startswith('chatgpt-image'):
        body['quality'] = q
        body['output_format'] = 'jpeg'
    else:
        body['quality'] = 'standard'
    r = requests.post(_ENDPOINT, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, json=body, timeout=timeout)
    if r.status_code >= 400:
        detail = ''
        try:
            detail = str(((r.json() or {}).get('error') or {}).get('message') or '')[:200]
        except Exception:
            detail = r.text[:200]
        raise RuntimeError(f"images api {r.status_code}: {detail}")
    data = (r.json() or {}).get('data') or []
    first = (data[0] or {}) if data else {}
    b64 = first.get('b64_json')
    if not b64 and first.get('url'):
        got = requests.get(str(first['url']), timeout=timeout)
        got.raise_for_status()
        b64 = base64.b64encode(got.content).decode('ascii')
    if not b64:
        raise RuntimeError('images api returned no image')
    return b64


_MODEL_ACCESS_RX = re.compile(r"model|verif|not found|does not exist|permission|unsupported|access|deprecat", re.I)


def default_generator(prompt: str) -> tuple:
    """(image bytes, model used) through the cascade; one retry on a
    rate limit; a model the account lacks is skipped from then on."""
    q = quality()
    last = None
    for model in model_cascade():
        try:
            return base64.b64decode(_openai_image_b64(prompt, model, q)), model
        except Exception as e:
            msg = str(e)
            last = e
            if '429' in msg:
                time.sleep(8.0)
                try:
                    return base64.b64decode(_openai_image_b64(prompt, model, q)), model
                except Exception as e2:
                    last = e2
                    msg = str(e2)
            if 'images api 4' in msg and _MODEL_ACCESS_RX.search(msg) and 'parameter' not in msg.lower():
                _dead_models.add(model)
                continue
            raise
    raise RuntimeError(f"no image model answered: {last}")


def _resize_jpeg(raw: bytes, size: int, jpeg_quality: int) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im = im.convert('RGB')
    w, h = im.size
    if w != h:
        s = min(w, h)
        left, top = (w - s) // 2, (h - s) // 2
        im = im.crop((left, top, left + s, top + s))
    im = im.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, format='JPEG', quality=jpeg_quality, optimize=True, progressive=True)
    return out.getvalue()


def _read_sidecar(s3, h: str) -> dict:
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=sidecar_key(h))['Body'].read().decode('utf-8')) or {}
    except Exception:
        return {}


def exists(s3, h: str) -> bool:
    """Is the 512 px portrait on S3? Known hashes answer without a call;
    a miss is remembered for a short while so a re-poll does not HEAD
    the same key every second."""
    with _lock:
        if h in _known:
            return True
        t = _miss_at.get(h)
        if t is not None and time.monotonic() - t < _MISS_TTL_S:
            return False
    if s3 is None:
        return False
    try:
        s3.head_object(Bucket=BUCKET, Key=portrait_key(h))
    except Exception:
        with _lock:
            _miss_at[h] = time.monotonic()
        return False
    gen = int(_read_sidecar(s3, h).get('generation') or 1)
    with _lock:
        _known[h] = gen
        _miss_at.pop(h, None)
    return True


def generation_of(h: str) -> int:
    with _lock:
        return int(_known.get(h) or 1)


def generate_portrait(s3, seat: dict, demographics: Optional[dict] = None, *, force: bool = False, save_dir: Optional[str] = None) -> Optional[dict]:
    """Generate, downscale and store one seat's portrait. Returns the
    sidecar (with ``hash``) or None on failure. A cached portrait is
    reused unless ``force``."""
    h = portrait_hash(seat)
    if not force and exists(s3, h):
        return dict(_read_sidecar(s3, h) or {}, hash=h, cached=True, generation=generation_of(h))
    gen_fn = _cfg.get('generator') or default_generator
    prior = _read_sidecar(s3, h) if force else {}
    generation = int(prior.get('generation') or 0) + 1 if force else 1
    prompt = build_prompt(seat, demographics, h, variant=generation)
    t0 = time.monotonic()
    try:
        got = gen_fn(prompt)
        raw, model = (got if isinstance(got, tuple) else (got, MODEL))
        if not raw:
            raise RuntimeError('empty image')
        full = _resize_jpeg(raw, OUT_SIZE, 86)
        thumb = _resize_jpeg(raw, THUMB_SIZE, 84)
    except Exception as e:
        _note('digital_twin_portraits.generate', e)
        return None
    side = {'hash': h, 'style_version': STYLE_VERSION, 'model': model, 'quality': quality(), 'size': OUT_SIZE, 'thumb': THUMB_SIZE,
            'generation': generation, 'created_at': _now(), 'seconds': round(time.monotonic() - t0, 1), 'seat': {'age': seat.get('age'), 'gender': seat_gender(seat), 'city': city_flavor(seat.get('city'))},
            'prompt': prompt}
    try:
        cache = 'public, max-age=31536000, immutable'
        s3.put_object(Bucket=BUCKET, Key=portrait_key(h), Body=full, ContentType='image/jpeg', CacheControl=cache)
        s3.put_object(Bucket=BUCKET, Key=portrait_key(h, THUMB_SIZE), Body=thumb, ContentType='image/jpeg', CacheControl=cache)
        s3.put_object(Bucket=BUCKET, Key=sidecar_key(h), Body=json.dumps(side, ensure_ascii=False).encode('utf-8'), ContentType='application/json')
    except Exception as e:
        _note('digital_twin_portraits.store', e)
        return None
    with _lock:
        _known[h] = generation
        _miss_at.pop(h, None)
        for k in [k for k in _img_cache if k[0] == h]:
            _img_cache.pop(k, None)
    if save_dir:
        try:
            os.makedirs(save_dir, exist_ok=True)
            stem = re.sub(r"[^A-Za-z0-9]+", '_', f"{seat.get('seat') or 0:02d}_{seat.get('name')}_{seat.get('age')}_{city_flavor(seat.get('city'))}").strip('_')
            with open(os.path.join(save_dir, stem + '.jpg'), 'wb') as fh:
                fh.write(full)
            with open(os.path.join(save_dir, stem + '.json'), 'w', encoding='utf-8') as fh:
                json.dump(side, fh, indent=1)
        except Exception:
            pass
    return side


# ------------------------------------------------------------- the room
def _demographics_for(seat: dict, twins_by_key: Optional[dict], payload: Optional[dict]) -> Optional[dict]:
    t = None
    if twins_by_key:
        t = twins_by_key.get(str(seat.get('twin') or ''))
    if t is None:
        t = payload
    return ((t or {}).get('identity') or {}).get('demographics') if isinstance(t, dict) else None


def apply_to_session(sess: dict, hashes: dict) -> int:
    """Write ``portrait_hash`` / ``portrait_url`` onto the roster seats in
    ``hashes`` ({seat_no: (hash, generation)}), and ``seat`` + the url
    onto the voices that name those seats. Returns the number of
    records changed."""
    changed = 0
    roster = ((sess or {}).get('group') or {}).get('roster') or []
    by_seat = {}
    for r in roster:
        no = int(r.get('seat') or 0)
        if no in hashes:
            h, gen = hashes[no]
            url = portrait_url(h, gen)
            if r.get('portrait_hash') != h or r.get('portrait_url') != url:
                r['portrait_hash'] = h
                r['portrait_url'] = url
                changed += 1
        if r.get('portrait_url'):
            by_seat[no] = r
    by_name = {str(r.get('name')): r for r in roster}
    for q in (sess or {}).get('questions') or []:
        for v in q.get('voices') or []:
            if not isinstance(v, dict):
                continue
            r = by_name.get(str(v.get('name') or ''))
            if r is None:
                continue
            if v.get('seat') != r.get('seat'):
                v['seat'] = r.get('seat')
                changed += 1
            if r.get('portrait_url') and v.get('portrait_url') != r.get('portrait_url'):
                v['portrait_url'] = r['portrait_url']
                changed += 1
    return changed


def attach_portraits(sess: dict, s3=None) -> int:
    """The self-heal: give every seat whose portrait already exists its
    url. No generation. Zero S3 calls when every seat is already
    carrying its url. Returns the number of records changed."""
    if not sess or not enabled():
        return 0
    roster = (sess.get('group') or {}).get('roster') or []
    hashes = {}
    for r in roster:
        h = portrait_hash(r)
        if r.get('portrait_hash') == h and r.get('portrait_url'):
            hashes[int(r.get('seat') or 0)] = (h, generation_of(h) if h in _known else _gen_from_url(r.get('portrait_url')))
            continue
        if exists(s3, h):
            hashes[int(r.get('seat') or 0)] = (h, generation_of(h))
    if not hashes:
        return 0
    return apply_to_session(sess, hashes)


def _gen_from_url(url) -> int:
    m = re.search(r"[?&]g=(\d+)", str(url or ''))
    return int(m.group(1)) if m else 1


def persist_session_portraits(s3, key: str, sid: str, hashes: dict) -> bool:
    """Re-write the session document in place: load the twin fresh (a
    question may have been asked meanwhile), apply, save, then read it
    back once and re-apply if a concurrent save dropped the urls."""
    from migration.digital_twin_synthesis import load_twin, save_twin
    for attempt in range(2):
        payload = load_twin(s3, key)
        if not isinstance(payload, dict):
            return False
        sess = next((s for s in payload.get('panel_sessions') or [] if s.get('id') == sid), None)
        if not sess:
            return False
        if apply_to_session(sess, hashes) or attempt == 0:
            save_twin(s3, key, payload)
        if attempt == 0 and _cfg.get('verify_delay'):
            time.sleep(float(_cfg['verify_delay']))
        check = load_twin(s3, key)
        sess2 = next((s for s in (check or {}).get('panel_sessions') or [] if s.get('id') == sid), None) if isinstance(check, dict) else None
        if sess2 and all(r.get('portrait_hash') == hashes[int(r.get('seat') or 0)][0] for r in ((sess2.get('group') or {}).get('roster') or []) if int(r.get('seat') or 0) in hashes):
            return True
    return False


def ensure_room_portraits(s3, key: str, sess: dict, twins_by_key: Optional[dict] = None, payload: Optional[dict] = None, *,
                          max_generate: int = MAX_PER_OPEN, save_dir: Optional[str] = None, force_seats=None, persist: bool = True) -> dict:
    """The room pass: every seat gets its cached portrait; up to
    ``max_generate`` missing ones are generated (``WORKERS`` at a time);
    the session document is re-written in place. Returns {seat_no:
    (hash, generation)} for the seats that have a portrait. Never
    raises."""
    out = {}
    try:
        if not sess or not enabled():
            return out
        roster = (sess.get('group') or {}).get('roster') or []
        force = {int(x) for x in (force_seats or []) if str(x).isdigit()} | {str(x).lower() for x in (force_seats or []) if not str(x).isdigit()}
        todo = []
        for r in roster:
            no = int(r.get('seat') or 0)
            h = portrait_hash(r)
            forced = bool(force) and (no in force or str(r.get('name') or '').lower() in force)
            if not forced and exists(s3, h):
                out[no] = (h, generation_of(h))
            else:
                todo.append((r, forced))
        todo = todo[:max(0, int(max_generate))]
        if todo:
            from concurrent.futures import ThreadPoolExecutor
            sid = str(sess.get('id') or '')

            def _one(item):
                r, forced = item
                demo = _demographics_for(r, twins_by_key, payload)
                side = generate_portrait(s3, r, demo, force=forced, save_dir=save_dir)
                return (int(r.get('seat') or 0), side)
            with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(todo)))) as ex:
                for no, side in ex.map(_one, todo):
                    if side:
                        out[no] = (side['hash'], int(side.get('generation') or 1))
            print(f"[portraits] room {sid}: {sum(1 for _ in todo)} asked, {len(out)} seats with a portrait")
        if out:
            apply_to_session(sess, out)
            if persist and s3 is not None and key:
                persist_session_portraits(s3, key, str(sess.get('id') or ''), out)
    except Exception as e:
        _note('digital_twin_portraits.room', e)
    return out


def schedule_room_portraits(s3, key: str, sess: dict, twins_by_key: Optional[dict] = None, payload: Optional[dict] = None) -> bool:
    """Start the room pass on a daemon thread (inline under
    ``configure(background=False)``). False when portraits are off."""
    if not sess or not enabled():
        return False
    if not _cfg.get('background', True):
        ensure_room_portraits(s3, key, sess, twins_by_key, payload)
        return True
    try:
        t = threading.Thread(target=ensure_room_portraits, args=(s3, key, sess, twins_by_key, payload), daemon=True,
                             name=f"dtiq-portraits-{str(sess.get('id') or '')[:10]}")
        t.start()
        return True
    except Exception as e:
        _note('digital_twin_portraits.schedule', e)
        return False


# ------------------------------------------------------------ the route
_HASH_RX = re.compile(r"^[0-9a-f]{16,64}$")


def valid_hash(h) -> bool:
    return bool(_HASH_RX.match(str(h or '')))


def read_portrait(s3, h: str, size: int = OUT_SIZE) -> Optional[bytes]:
    """The JPEG bytes for the route, or None when absent."""
    if s3 is None or not valid_hash(h):
        return None
    size = THUMB_SIZE if int(size) == THUMB_SIZE else OUT_SIZE
    ck = (h, size)
    with _lock:
        data = _img_cache.get(ck)
    if data:
        return data
    try:
        data = s3.get_object(Bucket=BUCKET, Key=portrait_key(h, size))['Body'].read()
    except Exception:
        return None
    if not data:
        return None
    with _lock:
        if len(_img_cache) >= _IMG_CACHE_MAX:
            for k in list(_img_cache)[: _IMG_CACHE_MAX // 4]:
                _img_cache.pop(k, None)
        _img_cache[ck] = data
        _known.setdefault(h, 1)
    return data


def reset_for_tests() -> None:
    with _lock:
        _known.clear()
        _miss_at.clear()
        _img_cache.clear()
    _dead_models.clear()
    configure()


__all__ = ['STYLE_VERSION', 'MODEL', 'FALLBACK_MODELS', 'PREFIX', 'MAX_PER_OPEN', 'OUT_SIZE', 'THUMB_SIZE', 'URL_BASE',
           'configure', 'enabled', 'quality', 'model_cascade', 'portrait_hash', 'portrait_key', 'sidecar_key', 'portrait_url', 'seat_gender', 'city_flavor', 'appearance_word',
           'build_prompt', 'default_generator', 'generate_portrait', 'exists', 'apply_to_session', 'attach_portraits', 'persist_session_portraits',
           'ensure_room_portraits', 'schedule_room_portraits', 'read_portrait', 'valid_hash', 'reset_for_tests']
