"""Same-title variants at the door (Jessie's StreamScout HowTo 5g, 2026-10-26).

Some titles name more than one distinct work: a US and a UK series (The
Office, Shameless), an original and a reboot (Charmed, Gossip Girl), a
film remade years apart (Dune 1984 / 2021, IT 1990 / 2017). StreamScout
holds a BARE query for one of these (seeds nothing, mails a 'which
version?' alert) and seeds a specified one distinctly ('The Office (UK)'
-> SHOW 'THE OFFICE (UK)'), so US and UK stay separate audiences.

Prometheus catches the bare form BEFORE the build: when the interpret
draft's title sits in ``streamscout.AMBIGUOUS_TITLES`` (imported from the
tool, never copied) without a trailing marker and without a resolved
version, the chat asks which version with one chip per known version
and passes the specific form through. The chips ride the existing
identity clarify (``ask_identity`` + ``identity_data.versions``), so the
pick lands through ``_apply_identity_version``: the subject, the content
map SHOW and the profile name all carry the version.

Region words inside a title ('American Sniper', 'The Americans') never
count: only StreamScout's own ``parse_variant`` reads a marker, and only
titles on its list are ever asked about. A build that still arrives
bare (the one-shot partner API) is held by the seed step
(``migration.content_map_seed.TitleHoldRefusal``) before any frame exists.
"""
from __future__ import annotations

import os
import re
import sys

_CONTENT_MEDIUMS = ('series', 'movie', 'podcast', 'game', 'book', 'album',
                    'franchise', 'musical')
_AUDIENCE_NOUNS = ('viewers', 'watchers', 'listeners', 'readers', 'players',
                   'fans', 'audience', 'universe', 'subscribers')
_PLATFORM_TAIL_RE = re.compile(
    r'\s+(?:on|via|from)\s+[A-Za-z0-9+ ]{2,40}$', re.I)

_YEAR_RE = re.compile(r'^(?:19|20)\d{2}$')


def streamscout_module():
    """The ``streamscout`` module from bg-webapp/streamscout, or None when
    the tool is not on this machine or predates the variant layer."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ss_dir = os.path.join(here, 'streamscout')
    if os.path.isdir(ss_dir) and ss_dir not in sys.path:
        sys.path.insert(0, ss_dir)
    try:
        import streamscout as ss
    except Exception:
        return None
    if not all(callable(getattr(ss, n, None)) for n in
               ('is_ambiguous_title', 'parse_variant', 'ambiguous_variants')):
        return None
    return ss


def title_head(name) -> str:
    """The title words of a deliverable name: the ' - <cut>' suffix, a
    trailing audience noun ('The Office Viewers') and a trailing platform
    phrase ('on Hulu') come off; a real subtitle stays."""
    s = str(name or '').strip()
    if ' - ' in s:
        s = s.split(' - ', 1)[0].strip()
    changed = True
    while changed and s:
        changed = False
        m = _PLATFORM_TAIL_RE.search(s)
        if m:
            s = s[:m.start()].strip()
            changed = True
        head, _, tail = s.rpartition(' ')
        if head and tail.lower().strip('()') in _AUDIENCE_NOUNS:
            s = head.strip()
            changed = True
    return s


def known_versions(title, ss=None) -> list:
    """The known versions when ``title`` is a bare query for a title on
    StreamScout's list: ['US', 'UK'] for 'The Office'; [] for a specified
    variant, a title not on the list, or when the tool is off."""
    ss = ss or streamscout_module()
    t = str(title or '').strip()
    if ss is None or not t:
        return []
    try:
        if not ss.is_ambiguous_title(t):
            return []
        return [str(v) for v in (ss.ambiguous_variants(t) or []) if str(v).strip()]
    except Exception:
        return []


def version_label(tag) -> str:
    """A plain label for a chip: 'US' -> 'US version', '2021' -> '2021
    film', 'reboot' -> 'the reboot'."""
    t = str(tag or '').strip()
    if _YEAR_RE.match(t):
        return f'{t} film'
    if t.lower() in ('original', 'classic'):
        return 'the original'
    if t.lower() in ('reboot', 'remake', 'revival'):
        return f'the {t.lower()}'
    return f'{t} version'


def version_options(title, medium='', ss=None) -> list:
    """One identity-clarify option per known version, in the specific
    form StreamScout seeds distinctly: {'title': 'The Office (US)',
    'version_label': '', 'medium': 'series', 'platform': '',
    'qualifier': 'US'}. [] when there is nothing to ask."""
    ss = ss or streamscout_module()
    tags = known_versions(title, ss)
    if not tags:
        return []
    try:
        base, _tag = ss.parse_variant(str(title))
    except Exception:
        base = str(title).strip()
    base = str(base or title).strip()
    med = str(medium or '').strip().lower()
    out = []
    for tag in tags:
        m = med if med in _CONTENT_MEDIUMS else ('movie' if _YEAR_RE.match(tag) else 'series')
        out.append({'title': f'{base} ({tag})', 'version_label': '',
                    'medium': m, 'platform': '', 'qualifier': str(tag)})
    return out[:5]


def _content_typed(draft) -> bool:
    if draft.get('is_ip_content') is True:
        return True
    if str(draft.get('seed_source') or '').strip() or str(draft.get('consumer_verb') or '').strip():
        return True
    if str(draft.get('medium') or '').strip().lower() in _CONTENT_MEDIUMS:
        return True
    cat = str(draft.get('brand_category') or '').strip().upper()
    return cat.startswith(('SERIES', 'MOVIE', 'PODCAST', 'GAMES'))


def candidate_titles(draft) -> list:
    """The title words the draft is about, most specific first."""
    out = []
    for key in ('content_show', 'resolved_title', 'subject', 'name'):
        t = title_head(draft.get(key))
        if t and t not in out:
            out.append(t)
    for t in (draft.get('franchise_titles') or []):
        th = title_head(t)
        if th and th not in out:
            out.append(th)
    return out


def apply_variant_guard(draft, log=print) -> bool:
    """In place: when a content-typed draft names a bare title that
    StreamScout knows as more than one work, stash the 'which version?'
    clarify (``ask_identity`` + ``identity_data`` with one option per
    version) and mark the identity unconfirmed. Returns True when it
    asked. Never raises; never touches a draft that already carries a
    version (a trailing marker or ``identity_qualifier``), an existing
    identity question, or an existing-profile decision."""
    try:
        if not isinstance(draft, dict) or not _content_typed(draft):
            return False
        decision = str(draft.get('decision') or 'new_build').strip().lower()
        if decision in ('existing_match', 'derive_cut'):
            return False
        if draft.get('ask_identity') or str(draft.get('identity_qualifier') or '').strip():
            return False
        ss = streamscout_module()
        if ss is None:
            return False
        for title in candidate_titles(draft):
            options = version_options(title, draft.get('medium'), ss)
            if not options:
                continue
            draft['identity_confident'] = False
            draft['ask_identity'] = True
            draft['identity_data'] = {
                'title': title,
                'medium': str(draft.get('medium') or 'title'),
                'platform': str(draft.get('platform') or ''),
                'versions': options,
                'variant_guard': True,
            }
            try:
                log(f"[identity] {title!r} names more than one work "
                    f"({', '.join(o['qualifier'] for o in options)}); asking "
                    f"which version before the build")
            except Exception:
                pass
            return True
    except Exception as e:
        try:
            log(f'[identity] variant guard skipped: {e}')
        except Exception:
            pass
    return False
