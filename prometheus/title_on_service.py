"""A title on a service is the title; the service is where (Grace Yin,
Amazon, 2026-11-18 01:07 UTC).

Her first message in thread 9f460c9c19:

    "for customers who watched TONY,2026 coming-of-age dark comedy
     docudrama film Tony, starring Dominic Sessa as a 19-year-old
     Anthony Bourdain ... is streaming on Amazon Prime Video (released
     on October 26, 2026). can you give me the demo breakdown of the
     audience>"

On the deployed code it drew "I can pull that read on Amazon Prime
Video. Pulling this data will cost $100." The Reader had listed two
subjects, the title (TONY, not in the library) and the service (Amazon
Prime Video, in the library). ``reader._single_library_subject`` handed
over "the one library subject the reading carries", which was the
service, and the work gate reads a bound subject ahead of the words.
The binder's own reading of the words (TONY viewers) was never asked.

The rule, in one place:

  * a streaming service the words name as where something is watched
    ("on Amazon Prime Video", "streaming on Hulu") is the scope. It is
    never handed over as the subject while the words, or the Reader,
    name something else, or describe one title ("the film", "starring",
    "released on");
  * the quote names the title with its scope: "Tony viewers on Amazon
    Prime Video";
  * when the words describe one title on a service and no title can be
    named with confidence, the reader is asked which title. The read
    never runs on the whole service in its place. A question about the
    service's own audience ("what do Prime Video viewers watch", "who
    uses Netflix") is still the service's.

Pure text; no model, no S3, no Flask. Never raises. Wired in
prometheus.reader (_single_library_subject) and prometheus.priced_read
(bound_subject, work_gate). Regression:
scripts/test_pm_grace_thread_leftovers_2026_11_18.py.
"""
from __future__ import annotations

import re

__all__ = ['service_of', 'role_in', 'service_is_scope', 'describes_a_title', 'scoped_label', 'which_title', 'unnamed_title',
           'WHICH_TITLE']

WHICH_TITLE = ("Which title do you mean? Give me its name and I will read its viewers on {service}, "
               "not everyone who uses {service}.")
_WHICH_TITLE_BARE = "Which title do you mean? Give me its name (the service it is on helps) and I will pick it up from there."

_KIND = (r"(?:film|movie|show|series|docuseries|documentary|docudrama|miniseries|mini-series|limited\s+series|special|"
         r"title|sitcom|drama|thriller|comedy|feature|season|episode)")
# one work, said with a determiner: "the film", "this show", "that new series", "the 2026 dark comedy film"
_ONE_WORK_RX = re.compile(
    r"\b(?:the|this|that|its|a\s+new|an?\s+upcoming|the\s+new|the\s+recent|the\s+latest|\d{4})\s+"
    r"(?:(?!on\b|in\b|of\b|for\b|and\b|or\b)[\w'-]+\s+){0,5}?" + _KIND + r"\b(?!s\b)", re.I)
_CREDIT_RX = re.compile(
    r"\b(?:starring|stars|directed\s+by|written\s+by|created\s+by|produced\s+by|from\s+director|based\s+on\s+the)\s+\S+|"
    r"\b(?:released|premiered|launched|debuted|dropped|came\s+out|comes\s+out|premieres|launches|debuts)\s+"
    r"(?:on\s+|in\s+)?(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d|\d{1,2}/\d{1,2}|(?:19|20)\d{2})", re.I)
# the audience of a service itself: "Prime Video viewers", "who uses Netflix", "people who subscribe to Hulu"
_GENERAL_RX = re.compile(r"\b(?:what|which)\s+(?:films|movies|shows|series|titles|genres|content)\b|\btop\s+\d*\s*(?:films|movies|shows|series|titles)\b", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def service_of(name):
    """The canonical streaming service or platform a name is, or ''."""
    try:
        from . import platform_scope as _ps
        return _ps.canon(_squash(name)) or ''
    except Exception:
        return ''


def role_in(text, service):
    """How the words carry a service: 'scope' (where something is
    watched), 'subject' (the service's own audience), 'example',
    'mention', or '' when they do not name it."""
    try:
        from . import platform_scope as _ps
        c = _ps.canon(_squash(service))
        return str(_ps.named(text).get(c) or '') if c else ''
    except Exception:
        return ''


def describes_a_title(text):
    """True when the words are about one work: a kind of content said
    with a determiner ("the film", "this series", "the 2026 comedy
    film"), a credit ("starring", "directed by") or a release ("released
    on October 26, 2026"). "What films do Prime Video viewers watch" is
    about the service."""
    try:
        t = _squash(text)
        if not t or _GENERAL_RX.search(t):
            return False
        return bool(_ONE_WORK_RX.search(t) or _CREDIT_RX.search(t))
    except Exception:
        return False


def _words_subject(text):
    """(name, kind) the binder reads from the words alone, or ('', '')."""
    try:
        from . import subject_rules as _sr
        s = _sr.bind(text, None, '', {})
        if s and s.source == 'words' and s.name and s.kind not in ('metric', 'none') and not _sr.is_metric_phrase(s.display):
            return str(s.name), str(s.kind)
    except Exception:
        pass
    return '', ''


def _other_subjects(reading, service):
    out = []
    for s in ((reading or {}).get('subjects') or []) if isinstance(reading, dict) else []:
        if not isinstance(s, dict):
            continue
        name = _squash(s.get('as_written') or s.get('catalog') or '')
        if not name or name.lower() in ('<page>', 'this', 'that', 'it', 'this audience'):
            continue
        if str(s.get('kind') or '').lower() == 'platform' or service_of(name) == service or service_of(s.get('catalog')) == service:
            continue
        out.append(name)
    return out


_CONSUME = (r"(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|view(?:ed|s|ing)?|saw|seen|binged?|binging|finished|started|"
            r"tuned\s+in\s+to|listen(?:ed|s|ing)?\s+to)")


def _consumed_on(text, service):
    """The words say something was watched ON the service ("people
    streaming The Bear on Hulu"): what was watched is the subject."""
    try:
        from . import platform_scope as _ps
        names = sorted({service.lower(), *(_ps.PLATFORMS.get(service) or ())}, key=len, reverse=True)
        alt = '|'.join(re.escape(n) for n in names)
        m = re.search(r"\b" + _CONSUME + r"\s+(?P<obj>[^.;?!\n]{2,80}?)\s+(?:on|via|through)\s+(?:the\s+)?(?:" + alt + r")(?![\w+])",
                      _squash(text), re.I)
        if not m:
            return False
        obj = re.sub(r"\b(?:the|a|an|it|them|this|that|content|shows?|movies?|films?|series|anything|something|stuff|tv|videos?)\b", ' ',
                     m.group('obj'), flags=re.I)
        return bool(re.search(r"[A-Za-z0-9]{2,}", obj)) and service_of(_squash(m.group('obj'))) != service
    except Exception:
        return False


def service_is_scope(bind, reading=None, text=''):
    """True when ``bind`` (a library subject about to be handed over as
    the one the read runs on) is a streaming service the words name only
    as where something is watched, and the subject is something else:
    another subject in the Reader's reading, a subject the words name by
    rule, or one work the words describe without a name the library
    holds. The read is then never bound to the service."""
    try:
        svc = service_of(bind)
        if not svc:
            return False
        role = role_in(text, bind)
        if role == 'subject':
            return False                 # "Prime Video viewers", "who uses Netflix": the service's own audience
        if _other_subjects(reading, svc):
            return True
        name, _kind = _words_subject(text)
        if name and service_of(name) != svc:
            return True
        if _consumed_on(text, svc):
            return True
        return role in ('scope', 'mention') and describes_a_title(text)
    except Exception:
        return False


def unnamed_title(text, history=None, ctx=None, reading=None):
    """The which-title question for an ask about one work on a service
    that names no title anyone can place: the words describe a title
    ("the new Bourdain film that is streaming on Prime Video"), they
    name one service as where, and neither the words, the Reader, the
    thread nor the page gives a subject. None otherwise (the lanes run
    as before)."""
    try:
        t = _squash(text)
        if not t or len(t) > 900 or not describes_a_title(t):
            return None
        try:
            from . import named_platforms as _np
            hit = _np.find(t)
        except Exception:
            hit = None
        ps = list((hit or {}).get('platforms') or [])
        if len(ps) != 1 or role_in(t, ps[0]) == 'subject':
            return None
        svc = service_of(ps[0])
        if _other_subjects(reading, svc):
            return None
        page = ''
        try:
            page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip() if isinstance(ctx, dict) else ''
        except Exception:
            page = ''
        try:
            from . import subject_rules as _sr
            s = _sr.bind(t, history, page, {})
            if s and s.name and s.source in ('words', 'thread', 'page') and service_of(s.name) != svc \
                    and s.kind not in ('metric', 'none'):
                return None
            if _sr.pinned_thread_subject():
                return None
        except Exception:
            return None
        return which_title(t, ps[0])
    except Exception:
        return None


def _as_typed(name, text):
    try:
        from .followup_refine import _as_typed as _at
        return _at(name, text)
    except Exception:
        return _squash(name)


def scoped_label(display, verb, text, platform=''):
    """The label a quote shows for a title's audience: the title as the
    reader typed it, the audience word, and the one service the words
    name as its scope ("Tony viewers on Amazon Prime Video"). With no
    service, or more than one, the label is the title and the audience
    word as before."""
    try:
        name = _as_typed(display, text)
        v = _squash(verb)
        label = name if (not v or re.search(r"\b" + re.escape(v) + r"\b", name, re.I)) else f"{name} {v}"
        plat = _squash(platform)
        if not plat:
            try:
                from . import named_platforms as _np
                hit = _np.find(text)
            except Exception:
                hit = None
            ps = list((hit or {}).get('platforms') or [])
            if hit and hit.get('strong') and len(ps) == 1 and role_in(text, ps[0]) in ('scope', 'mention') \
                    and service_of(name) != service_of(ps[0]):
                plat = str(ps[0])
        if plat and not re.search(re.escape(plat), label, re.I):
            label = f"{label} on {plat}"
        return label
    except Exception:
        v = _squash(verb)
        return f"{_squash(display)} {v}".strip()


def which_title(text, service=''):
    """The one question when the words describe a title on a service and
    name none we can place: which title, never a read on everyone who
    uses the service. Shaped like the which-audience question, with no
    price and nothing started."""
    svc = _squash(service)
    if not svc:
        try:
            from . import named_platforms as _np
            hit = _np.find(text)
            ps = list((hit or {}).get('platforms') or [])
            svc = str(ps[0]) if len(ps) == 1 else ''
        except Exception:
            svc = ''
    reply = WHICH_TITLE.format(service=svc) if svc else _WHICH_TITLE_BARE
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
            'deck_angle': None, 'audience_confirm': True, 'title_confirm': True}
