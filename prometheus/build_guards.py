"""The subject guards on an interpreted build draft (moved out of
prometheus/legacy/chat.py on 2026-10-21, with the build-entity step added).

Order, on a draft the interpret model produced:

  1. placeholder subject ('this profile'): research rescue, else the
     segments question (2026-08-20);
  2. build-entity resolution (2026-10-21, Jessie: "run a profile for
     Listerners of Ball in the Bonzo Family with Lonzo Ball" drew
     "I read that as a task, not a profile to build"): an explicit build
     verb plus any noun phrase IS a build order. The typed phrase, its
     consumer noun split into the universe scope and its host credit
     dropped, is resolved typo-tolerant against the open profile, the
     catalog and the hostmap's brands; one confident hit renames the
     draft to the dashboard's own spelling (so the existing-profile
     matchers find it and no typo becomes a Total Universe name); two
     hits close together ask WHICH one, the only question allowed back;
     no hit keeps the clean core and the build proceeds through the
     verify ladder as before;
  3. fragment subject ('Three Actually Influence Product Purchases'):
     research rescue, else "I read that as a task" (never on an explicit
     build order; after step 2 only a task-shaped ask can reach it);
  4. entity core ('Appeal of the Spiderwick Franchise' -> Spiderwick).

Pure logic over the draft; every chat-side helper comes in through
``deps`` so this module imports nothing from the legacy chat.
"""
from __future__ import annotations

import re
import traceback

from prometheus import build_entity as _be

TASK_MESSAGE = (
    "I read that as a task, not a profile to build. Which audience "
    "should this be on? Name the show, brand, or person (one line is "
    "enough) and I will take it from there.")
SEGMENTS_MESSAGE = (
    "Quick check on the audience: tell me who each profile is for - "
    "e.g. 'Amazon EST buyers' or 'Vizio TV owners' - or list the "
    "segments and I'll queue one profile per segment.")
_BUILD_DECISIONS = ('new_build', 'cut_needs_parent', 'subscriber_iq', '')


def _norm(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


def page_name(body):
    """The profile open on the screen, when the request carries it."""
    try:
        pc = (body or {}).get('page_context') or {}
        prim = pc.get('primary') or {}
        name = str(prim.get('name') or pc.get('subject') or '').strip()
        if not name:
            vc = (body or {}).get('view_context') or pc.get('view_context') or {}
            name = str(vc.get('view_title') or '').strip()
        return name.split(' - ')[0].strip()
    except Exception:
        return ''


_TITLES_CACHE = {'at': 0.0, 'names': []}
_TITLES_TTL_S = 3600


def content_map_titles(connect=None, now=None):
    """The content map's distinct SHOW spellings (the podcasts, series
    and films StreamScout has seeded), loaded once an hour per process
    so a build order can resolve a title the catalog does not yet hold
    (2026-10-21: "Ball in the Bonzo Family with Lonzo Ball" is the map's
    "Ball in the Family Podcast with Lonzo & Gelo Ball" long before any
    profile exists). A test process never opens the warehouse; the
    pool is simply empty there, and a read failure keeps the last list."""
    import os
    import time as _time
    t = float(now if now is not None else _time.time())
    if _TITLES_CACHE['names'] and t - _TITLES_CACHE['at'] < _TITLES_TTL_S:
        return list(_TITLES_CACHE['names'])
    if connect is None and (os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_REPLAY_CHILD')):
        return list(_TITLES_CACHE['names'])
    try:
        if connect is None:
            try:
                from migration.clickhouse_connector import connect_clickhouse as connect
            except ImportError:
                from clickhouse_connector import connect_clickhouse as connect  # type: ignore
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT SHOW FROM reference.content_mapping WHERE SHOW != '' ORDER BY SHOW")
        rows = cur.fetchall() or []
        try:
            cur.close(); conn.close()
        except Exception:
            pass
        names = [str(r[0]).strip() for r in rows if r and str(r[0] or '').strip()]
        if names:
            _TITLES_CACHE['names'] = names
            _TITLES_CACHE['at'] = t
    except Exception as e:
        print(f"[build-entity] content map titles unavailable: {type(e).__name__}: {str(e)[:120]}")
    return list(_TITLES_CACHE['names'])


def entity_pools(catalog, body):
    """Candidate names for the resolver: the page, the catalog's display
    names, the content map's titles (cached hourly) and the hostmap's
    brands when a loaded index is at hand (no network is opened for the
    brands; the titles open the warehouse at most once an hour)."""
    names = []
    for c in (catalog or []):
        if not isinstance(c, dict):
            continue
        n = str(c.get('display_name') or '').strip() or str(c.get('subject') or '').replace('_', ' ').strip()
        if n:
            names.append(n)
    brands = []
    try:
        from migration import synth_hostmap_augment as _sha
        cache = getattr(_sha, '_BRAND_TO_COLUMN_CACHE', None)
        if cache:
            brands = [v[0] for v in cache.values() if isinstance(v, (list, tuple)) and v and v[0]]
    except Exception:
        brands = []
    try:
        titles = content_map_titles()
    except Exception:
        titles = []
    return {'page': page_name(body), 'catalog': names, 'titles': titles, 'brands': brands}


def resolve_build_entity(spec_draft, text, catalog, body, deps):
    """Step 2. Returns (draft, None) or (draft, (payload, status)) for the
    which-one question."""
    explicit = str(deps['explicit_build_subject'](text) or '').strip()
    if not explicit:
        return spec_draft, None
    dec = str(spec_draft.get('decision') or '')
    if dec not in _BUILD_DECISIONS:
        return spec_draft, None
    spec_draft['_explicit_build'] = True
    pools = entity_pools(catalog, body)
    drafted = str(spec_draft.get('subject') or '').strip()
    res = _be.resolve(drafted or explicit, **pools)
    scope = res['scope'] or _be.split_scope(explicit)[1]
    if not res['display'] and not res['ambiguous'] and drafted and _norm(drafted) != _norm(explicit):
        alt = _be.resolve(explicit, **pools)           # the words themselves
        if alt['display'] or alt['ambiguous']:
            res = alt
    if res['ambiguous']:
        q = _be.which_question(res['candidates'])
        if q:
            deps['ask_hint'](outcome='asked_which_entity', subject=res['core'])
            return spec_draft, ({'success': False, 'guidance': True, 'error': q,
                                 'followups': [c[0] for c in res['candidates'][:3]]}, 400)
    before = drafted
    if res['display']:
        name = res['display']
        if res['source'] == 'titles':
            spec_draft['content_show'] = name
            name = _be.strip_credit(name) or name
        else:
            # the page or the catalog named it; when the content map knows
            # the same show under its own spelling, that spelling seeds the
            # consumers universe (the open "Ball in the Family with Lonzo
            # Ball" is the content map's "Ball in the Family Podcast with
            # Lonzo & Gelo Ball"; the episodes hang off the latter)
            titles_pool = [str(t) for t in (pools.get('titles') or [])]
            for sp in (res.get('spellings') or []):
                if sp in titles_pool and not str(spec_draft.get('content_show') or '').strip():
                    spec_draft['content_show'] = sp
                    break
            name = _be.strip_credit(name) or name
    elif drafted:
        # nothing in the library: the model's reading of the name stands
        # (its typo correction included), with the scope and credit off
        name = _be.strip_credit(_be.split_scope(drafted)[0]) or drafted
    else:
        name = res['core'] or explicit
    if name and _norm(name) != _norm(before):
        print(f"[synth-chat interpret] build entity {before or explicit!r} -> {name!r}"
              f" ({res['source'] or 'as typed, scope and credit off'})")
        spec_draft['subject'] = name
        for k in ('name', 'deliverable_name'):
            if str(spec_draft.get(k) or '').strip() in (before, ''):
                spec_draft[k] = name
        spec_draft.pop('file_stem', None)
    if scope and not str(spec_draft.get('consumer_verb') or '').strip():
        spec_draft['consumer_verb'] = scope
        spec_draft['ip_scope'] = 'consumers'
    return spec_draft, None


def settle_subject(spec_draft, text, catalog, body, deps):
    """Steps 1 to 4 on one draft. ``deps``: is_placeholder_subject,
    rescue (draft -> {'draft'} | {'ask'}), manual_look_email, ask_hint,
    swallow, qualified_audience, explicit_build_subject, guards (the
    prometheus.guards module). Returns (draft, None) when the build may
    continue, else (draft, (payload, status)) to send back."""
    pg = deps['guards']
    # 1. placeholder subject
    if deps['is_placeholder_subject'](spec_draft.get('subject')):
        ph_subj = ''
        for f in ('resolved_title', 'resolved_identity', 'name'):
            c = str(spec_draft.get(f) or '').strip()
            if c and not deps['is_placeholder_subject'](c):
                ph_subj = c
                break
        if not ph_subj:
            m = re.search(r'\b(?:profile|audience|universe)\s+(?:on|for|of)\s+([A-Za-z0-9][^,.;\n]{1,60})', text, re.I)
            if m:
                ph_subj = m.group(1).strip()
        rescued = False
        if ph_subj:
            uv = deps['rescue']({'name': ph_subj, 'decision': 'new_build', 'tu_demos': {}})
            if isinstance(uv, dict) and isinstance(uv.get('draft'), dict):
                spec_draft = uv['draft']
                rescued = True
                print(f"[synth-chat interpret] placeholder subject rescued by research: {ph_subj!r}")
            elif isinstance(uv, dict) and uv.get('ask'):
                return spec_draft, ({'success': False, 'guidance': True, 'error': uv['ask']}, 400)
        if not rescued:
            try:
                deps['manual_look_email'](text, SEGMENTS_MESSAGE,
                                          'The audience in this ask did not resolve to a named subject, so the user was asked to rephrase.')
            except Exception:
                traceback.print_exc()
            return spec_draft, ({'success': False, 'guidance': True, 'error': SEGMENTS_MESSAGE}, 400)

    # 2. the entity behind an explicit build order
    try:
        spec_draft, resp = resolve_build_entity(spec_draft, text, catalog, body, deps)
        if resp is not None:
            return spec_draft, resp
    except Exception:
        deps['swallow']('build-entity')

    # 3. fragment subject (never an explicit build order)
    try:
        frag_subj = str(spec_draft.get('subject') or '').strip()
        frag_dec = str(spec_draft.get('decision') or '')
    except Exception:
        frag_subj, frag_dec = '', ''
    if frag_subj and frag_dec in ('new_build', 'subscriber_iq', '') \
            and not spec_draft.get('_explicit_build') \
            and pg.subject_reads_as_fragment(frag_subj) \
            and not deps['qualified_audience'](frag_subj)[0]:
        rescued = False
        try:
            uv = deps['rescue']({'name': frag_subj, 'decision': 'new_build', 'tu_demos': {}})
            if isinstance(uv, dict) and isinstance(uv.get('draft'), dict) \
                    and not pg.subject_reads_as_fragment(uv['draft'].get('subject')):
                spec_draft = uv['draft']
                rescued = True
                print(f"[synth-chat interpret] fragment subject rescued by research: {frag_subj!r}")
        except Exception:
            deps['swallow']('fragment-rescue')
        if not rescued:
            deps['ask_hint'](outcome='asked_subject', subject=frag_subj)
            try:
                deps['manual_look_email'](text, TASK_MESSAGE,
                                          'The audience in this ask did not resolve to a named subject, so the user was asked to rephrase.')
            except Exception:
                traceback.print_exc()
            return spec_draft, ({'success': False, 'guidance': True, 'error': TASK_MESSAGE}, 400)

    # 4. entity core
    try:
        ec_dec = str(spec_draft.get('decision') or '')
        ec_subj = str(spec_draft.get('subject') or '').strip()
        if ec_subj and ec_dec in ('new_build', 'cut_needs_parent', ''):
            ec_core = pg.entity_core(ec_subj)
            if ec_core and ec_core != ec_subj:
                print(f"[synth-chat interpret] entity core {ec_subj!r} -> {ec_core!r}")
                spec_draft['subject'] = ec_core
                for k in ('name', 'deliverable_name'):
                    if str(spec_draft.get(k) or '').strip() == ec_subj:
                        spec_draft[k] = ec_core
    except Exception:
        traceback.print_exc()
    return spec_draft, None


def correction_hold(text, history=None):
    """A complaint about the conversation that reached the build intake
    is never a draft (Emmet Riley 2026-10-26: "okay but why do you keep
    brining up summers eve this is an indepdent pull" drafted a $275
    build). A correction, a why-do-you-keep or a nothing-to-do-with that
    carries no fresh order gets one plain line on the thread's subject
    and no card. Returns the guidance payload, or None when the words are
    an order or a plain ask."""
    try:
        from prometheus import subject_rules as _sr
        from prometheus import thread_subject as _ts
        t = ' '.join(str(text or '').split())
        if not t or not _sr.conversational_turn(t) or _sr.priced_handoff_allowed(t):
            return None
        subj = _sr.pinned_thread_subject() or ''
        if not subj:
            for cand in _sr.thread_subjects(history or [], ''):
                if cand and not _ts.is_dropped(cand) and not _sr.is_metric_phrase(cand):
                    subj = cand
                    break
        dropped = _ts.dropped_names(t)
        shown, keep = '', []
        if dropped:
            from prometheus.turn_gate import _display_name as _dn
            keep = [d for d in dropped if not any(d != o and d in o for o in dropped)]
            names = [_dn(d, '', history, t) for d in keep[:3]]
            shown = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
        lead = (f"Understood. {shown} {'is' if len(keep) == 1 else 'are'} out of this thread for good."
                if shown else "Understood. Nothing is drafted from that.")
        tail = (f" This is {subj} on its own. Ask the question or say what to pull on it and I will take it from there."
                if subj else " Tell me the subject or the question and I will take it from there.")
        return {'success': False, 'guidance': True, 'error': lead + tail, 'followups': [], 'correction_ack': True}
    except Exception:
        traceback.print_exc()
        return None
