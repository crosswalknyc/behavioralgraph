"""The base a generated read runs on: the open profile, the named subject, or a question with no base at all.

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
import traceback
import re
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
from prometheus import page_default as _page_default
from prometheus import question_universe as _question_universe
from prometheus import ledger_gate as _ledger_gate, probe_files as _probe_files, cut_list as _cut_list, primary_profile as _primary_profile
from prometheus import named_subject as _ns_mod

# <<moved>>
# <<block: 1>>
def _pm_generation_base(subject_hint, text, ctx=None,
                        prefer_catalog=False, bound=False):
    """Resolve the base that authorizes a generated read.

    Jenna 2026-08-27 (verbatim): "it can't pull full data on something
    that doesnt have a base profile. if someone asks for paw patrol
    data but there is no paw patrol base TU profile they would have to
    pay the 5 credits to run that profile first ... the synth can
    never superceed a topic not already pulled somewhere."

    A generated read is only ever a derivation of a subject already
    pulled somewhere: the profile open on the page, a profile in the
    Select Profile catalog, or a Subscriber IQ run. Returns
    {'subject', 's3_key', 'source'} or None (None = hard refuse; the
    caller steers to the 5-credit build instead of generating).

    `prefer_catalog` (2026-08-27, Paige Bueckers ad CTR): KPI asks
    name their own subject in the question, which may not be the
    profile open on screen. When set, a catalog subject named in the
    ask wins over the open page; the page stays the fallback.
    """
    import prometheus_analysis as pma
    if _page_default.brief_on_own_base(text):
        return None   # a brief on works it names, on its own base (all US people): no page, library file or Subscriber IQ run is its base (2026-11-17)
    # 0. The open product is the base (2026-10-22, Alexia / The Influencer
    # Project): with an Attribution IQ campaign, a Digital Journey or a Brand
    # Partnership read on screen, an ask that refers to it or names its own
    # title answers from that payload. The library is never consulted for
    # it, so a same-title Profile IQ file (the Potential Ticket Buyer
    # profile) can never become the base of a campaign question.
    try:
        from prometheus import view_base as _vb
        _vc0 = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
        if _vb.is_self_contained((_vc0 or {}).get('view_id')):
            # An ask that names the open view's OWN title is the view's
            # ask whatever else it lists: the services, platforms and
            # cuts in a demographics ask are its dimensions, not another
            # subject (2026-11-04: "Music to Long Form study ... Netflix,
            # Amazon Prime Video, TikTok" was vetoed here and then bound
            # to the Tracker and Gunna - 18-24 files by token luck).
            _own_title = _vb.names_view(text, _vc0, product_nouns=False) or _vb.names_view(subject_hint, _vc0, product_nouns=False)
            if _own_title:
                return _vb.view_base(_vc0)
            _own = _vb.names_view(text, _vc0) or _vb.names_view(subject_hint, _vc0)
            if not _own:
                try:
                    _own = bool(_C._PM_VIEW_DEIXIS_RE.search(str(text or ''))) or (not str(subject_hint or '').strip() and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled())   # no hint is not a pointer (Jenna 2026-11-20)
                except Exception:
                    _own = False
            if _own and not _subject_rules.names_other_subject(text, ctx):
                return _vb.view_base(_vc0)
    except Exception:
        traceback.print_exc()
    # 1. The profile open on the page IS the base (unless the caller
    # asked for ask-named catalog subjects to win; the page is still
    # the fallback below when the catalog names nothing).
    if _page_default.is_everyone(subject_hint):
        return None   # the whole panel: never a file, never the page; priced and run as a panel read (2026-10-24)
    page_base = None
    if ctx and ctx.get('primary'):
        pk = str(ctx['primary'].get('s3_key') or '').strip()
        nm = str(ctx['primary'].get('name') or '').strip()
        if nm and not pk:   # a page named without its file (an API caller, a probe): the library knows the key (2026-10-14)
            try:
                _n0 = _H._normalize_for_match(nm)
                pk = next((str(e.get('s3_key') or '') for e in _C._profile_catalog_for_chat()
                           if e.get('s3_key') and _H._normalize_for_match(e.get('display_name')) == _n0), '')
            except Exception as _fx:
                _faults.note('chat', _fx)
        if pk:
            page_base = {'subject': nm or pk, 's3_key': pk,
                         'source': 'page'}
    # The base is the universe the question names (Jenna 2026-11-06, her
    # holiday toy shopping question ran "in the Starz audience"): a
    # question that names its own universe ("people who ...", "potential
    # <brand> consumers", "<brand> buyers", "US households", "when people
    # start ...") is read on that universe. The page is not its base, and
    # no file is guessed in for it below (the partial-name tier and the
    # typo tier stand down); only a library subject the words name in
    # full binds. The page stays when the words point at it or name it
    # (prometheus.question_universe.question_universe).
    _qu_named = _qu_explicit = False
    try:
        _qu = _question_universe.question_universe(text, str((page_base or {}).get('subject') or ''))
        _qu_named = bool(_qu.kind == 'named') and not bound
        # an audience named outright: no file is guessed in for it, on the
        # ask and on the Approve that carries the quoted universe back
        # (2026-11-06: 'Potential Paw Patrol Consumers' approved drew the
        # Paw Patrol Ticket Purchasers file out of the partial-name tier)
        _qu_explicit = bool(_qu.kind == 'named') and bool(_qu.explicit)
    except Exception as _fx:
        _faults.note('chat', _fx)
    if page_base is not None and _qu_named:
        print(f"[pm-base] the ask names its own universe ({_qu.label!r}); the open page "
              f"{page_base.get('subject')!r} is not its base")
        page_base = None

    def _tokens(s):
        # Distinct: 'BEST OF THE BEST' is one ordinary word (2026-10-22)
        return list(dict.fromkeys(
            w for w in _H._normalize_for_match(s).split()
            if w not in _C._PM_BASE_GENERIC_TOKENS))

    # Subject-conflict guard (2026-09-24, the PA-09 hold): an ask that
    # NAMES ITS OWN SUBJECT must never silently ride the open page as
    # its base. 'what are congressional district pa 09 voters googling'
    # asked while 2026 Florida Gubernatorial Voters was open bound the
    # Florida file, generated a Pennsylvania read off it, and the
    # verify pass then held the read for contradicting Florida's rows.
    # The guard fires only when the router extracted a subject hint
    # with distinctive tokens (2+) sharing NOTHING with the page
    # subject - pronoun asks ('what are they googling', 'analyze
    # this') carry no hint and keep the page base exactly as before.
    if page_base is not None:
        # 2026-10-06 (Jenna, strip the dashboard assumptions): the guard
        # applies on every path, including prefer_catalog callers, so a
        # named subject with no catalog file never falls back onto the
        # open page.
        _hint_d = set(_tokens(subject_hint))
        _page_d = set(_tokens(page_base.get('subject')))
        _page_outright = False
        try:   # the page written out in full is named too: the hint is then the object, not another subject (2026-11-04)
            _page_outright = _ns_mod.page_named_outright(text, page_base.get('subject'), _H._normalize_for_match, _C._PM_CLARIFY_STOP_TOKENS)
        except Exception as _fx:
            _faults.note('chat', _fx)
        if (len(_hint_d) >= 2 and _page_d
                and not (_hint_d & _page_d) and not _page_outright):
            print(f"[pm-base] ask names its own subject "
                  f"({' '.join(sorted(_hint_d))[:60]}) with no overlap "
                  f"to the open page ({page_base.get('subject')!r}); "
                  f"page base skipped")
            page_base = None
    # A person's profile is not the audience of their films (Jenna
    # 2026-11-04, Jay Patel's Statham thread): with an ACTOR / MUSICIAN /
    # creator profile open, a question about viewers of the titles, their
    # Prime Video share or total viewers is anchored on title viewing,
    # never on the people with digital touchpoints to the person. The
    # profile stays the base for a question about the person's own
    # audience (prometheus.page_default.person_page_for_title_ask).
    if page_base is not None:
        try:
            _pcat = _page_default.profile_category(page_base.get('s3_key'), page_base.get('subject'),
                                                   _C._profile_catalog_for_chat())
            if _page_default.person_page_for_title_ask(text, _pcat, page_base.get('subject')):
                print(f"[pm-base] page {page_base.get('subject')!r} is a person ({_pcat}); the ask is about "
                      f"viewers of titles, not the person's audience; page base skipped")
                page_base = None
        except Exception:
            traceback.print_exc()
    if page_base is not None and bound and not _page_default.page_base_stands(
            '', None, page_base.get('subject'), '', subject_hint, normalize=_H._normalize_for_match, stop_tokens=_C._PM_CLARIFY_STOP_TOKENS)[0]:
        page_base = None   # a bound subject (chip, approved quote) is the base or nothing; the quote and the read agree (2026-10-24)
    if page_base is not None and not prefer_catalog:
        return page_base
    # The page written out in full in the ask is the ask's audience; a
    # library subject the ask also names is what the read measures on it
    # (Jessie 2026-11-04: "from the New Heights Podcast Listeners profile,
    # what percentage ... Tubi, Roku, Pluto" carried the hint 'Tubi', the
    # catalog tier below bound the TUBI file, and two reads ran on the
    # wrong rows with the named profile open). A cut of the page's own
    # family named outright still binds the cut.
    if page_base is not None and not bound:
        try:
            if _ns_mod.page_named_outright(text, page_base.get('subject'), _H._normalize_for_match, _C._PM_CLARIFY_STOP_TOKENS):
                _fam_hit = _ns_mod.named_entry(text, _C._profile_catalog_for_chat(), _H._normalize_for_match, _C._PM_CLARIFY_STOP_TOKENS)
                if _fam_hit and _fam_hit.get('s3_key') \
                        and _ns_mod.same_family(_fam_hit, page_base.get('subject'), _H._normalize_for_match, _C._PM_CLARIFY_STOP_TOKENS) \
                        and str(_fam_hit.get('s3_key') or '') != str(page_base.get('s3_key') or ''):
                    print(f"[pm-base] the ask names {_fam_hit.get('display_name')!r}, a cut of the open page's family; it is the base")
                    return {'subject': str(_fam_hit.get('subject') or _fam_hit.get('display_name') or '').strip(),
                            's3_key': str(_fam_hit.get('s3_key') or ''), 'source': 'catalog'}
                print(f"[pm-base] the ask names the open page {page_base.get('subject')!r} outright; the page is the base "
                      f"(the hint {str(subject_hint or '')[:40]!r} is what the read measures on it)")
                return page_base
        except Exception:
            traceback.print_exc()

    q_tokens = set(_H._normalize_for_match(text).split())
    hint_tokens = set(_H._normalize_for_match(subject_hint).split())
    # a platform named under another name is the library's file ("Prime
    # Video" is Amazon Prime Video; Cara Dorfman 2026-11-10, whose Prime
    # Video reads found no base and ran on the other side's file)
    try:
        from prometheus import base_pair as _bp_al
        _al_q = set(_bp_al.alias_tokens(text))
        _al_h = set(_bp_al.alias_tokens(subject_hint))
        q_tokens |= _al_q
        hint_tokens |= _al_h
    except Exception as _fx:
        _faults.note('chat', _fx)
    guessed = ''
    try:
        guessed = pma.guess_subject_from_text(text)
    except Exception as _fx:
        _faults.note('chat', _fx)
    guess_tokens = set(_H._normalize_for_match(guessed).split())

    # 2. Profile catalog: a profile whose distinctive subject tokens
    # all appear in the ask (or the subject hint). TU files win over
    # cuts; more distinctive subjects win over shorter ones. When no
    # subject is fully named, a partial tier binds the closest catalog
    # subject whose distinctive tokens mostly appear in the ask (at
    # least 2 tokens at two-thirds coverage), so "what are parents of
    # kids 4-7 buying in terms of toy categories" still reaches the
    # parents / toy-buyers base (2026-08-27, toy-categories routing).
    best, best_score = None, (0, 0)
    partial, partial_score = None, (0, 0.0, 0)
    ask_tokens = q_tokens | hint_tokens
    # A long analytical ask lists things that are not its subject: the
    # services, platforms and bands of a demographics ask (2026-11-04,
    # Liz's Music to Long Form reads bound 'Tracker' off the word
    # 'tracked', 'Gunna - 18-24' off the age band, 'ANIMATED' off 'Family
    # animated' and 'Amazon Prime Video' off the streaming list). In such
    # an ask a lone ordinary word is no identity, number tokens never
    # count toward a partial match, the one-typo tier stays out, and a
    # list of three or more fully named library subjects is a list, not
    # a subject, unless the hint names one of them.
    _long_ask = len(str(text or '')) > 350
    _full_hits = set()
    # 2026-10-07 (Jenna, the Gunna-on-Warriors ask): a file named by
    # display name (bound chip, cut label) is the base; a subject named
    # in subject position beats token presence (Gen Pop, Under Armour
    # and Nike sat in that ask as objects); Gen Pop is never a base.
    _norm = _H._normalize_for_match
    try:
        _cat = _subject_rules.library_named(_C._profile_catalog_for_chat(), text, hint=subject_hint)   # a plus is part of a service's name (2026-11-17)
        _hint_n = _norm(subject_hint)
        _ent = None
        if _hint_n:
            _ent = next((e for e in _cat if e.get('s3_key')
                         and _norm(e.get('display_name')) == _hint_n
                         and not _ns_mod.is_gen_pop(
                             _ns_mod.family_name(e), _norm)), None)
        if not _ent:
            _ent = _ns_mod.named_entry(
                f"{subject_hint or ''} {text or ''}", _cat, _norm,
                _C._PM_CLARIFY_STOP_TOKENS)
        if _ent and _ent.get('s3_key'):
            print(f"[pm-base] named: {_ent.get('display_name')!r}")
            return {'subject': str(_ent.get('subject')
                                   or _ent.get('display_name') or '').strip(),
                    's3_key': str(_ent.get('s3_key') or ''),
                    'source': 'catalog'}
    except Exception:
        traceback.print_exc()
    try:
        for entry in _subject_rules.library_named(_C._profile_catalog_for_chat(), text, hint=subject_hint):
            if _ns_mod.is_gen_pop(_ns_mod.family_name(entry), _norm):
                continue
            for field in ('subject', 'display_name'):
                toks = _tokens(entry.get(field))
                if not toks:
                    continue
                if sum(len(t) for t in toks) < 4 and \
                        not _C._pm_short_name_identity(
                            toks, f"{text or ''} {subject_hint or ''}"):
                    # identity-token floor, with the ALL-CAPS
                    # initialism bypass for 2-5 letter subjects
                    continue
                if len(toks) == 1 and toks[0] in _C._PM_COMMON_IDENTITY_WORDS \
                        and not _C._pm_short_name_identity(
                            toks, f"{text or ''} {subject_hint or ''}"):
                    # one ordinary word is not an identity (2026-10-06)
                    continue
                tset = set(toks)
                is_tu = ' - ' not in str(entry.get('display_name') or '')
                cand = {'subject': str(entry.get('subject')
                                       or entry.get('display_name')
                                       or '').strip(),
                        's3_key': str(entry.get('s3_key') or ''),
                        'source': 'catalog'}
                if (tset <= q_tokens or tset <= hint_tokens
                        or tset <= guess_tokens):
                    if _long_ask and len(toks) == 1 and not (tset <= hint_tokens) \
                            and re.search(r"(?:ed|ing|ly|ous|ful|ive|al|ic)$", toks[0]) \
                            and not re.search(rf"(?<![A-Za-z])[A-Z][A-Za-z]*{re.escape(toks[0][1:])}\b", str(text or '')):
                        continue   # a lone lowercase English word deep in a long ask ('animated') is not a subject
                    if _long_ask and not (tset <= hint_tokens):
                        _full_hits.add(_H._normalize_for_match(cand['subject']))
                    score = (1 if is_tu else 0, len(toks))
                    if score > best_score:
                        best_score = score
                        best = cand
                    continue
                matched = len({t for t in (tset & ask_tokens) if not t.isdigit()})
                frac = matched / len(tset)
                if matched >= 2 and frac >= 0.66:
                    pscore = (1 if is_tu else 0, frac, matched)
                    if pscore > partial_score:
                        partial_score = pscore
                        partial = cand
    except Exception:
        traceback.print_exc()
    if best and len(_full_hits) >= 3 and not (set(_tokens(best.get('subject'))) <= hint_tokens):
        print(f"[pm-base] {len(_full_hits)} library subjects named in one ask read as a list, not a subject; catalog tiers skipped")
        best, partial = None, None
    if best:
        return best
    if partial and _qu_explicit:
        print(f"[pm-base] the ask names its own universe; the partial-name tier's {partial.get('subject')!r} is not its base")
        partial = None
    if partial:
        return partial
    # 2b. Fuzzy tier (2026-09-28, Phase 1): a unique one-typo-away
    # catalog subject binds instead of falling through to a duplicate
    # build offer. Ambiguity never binds. A long ask never binds by a
    # typo (2026-11-04: 'tracked' is not 'Tracker'). A question that
    # names its own audience outright binds by a typo only inside that
    # audience's own words ("cotsco members" is Costco; "start" in "when
    # people start ... shopping" was never "Starz", 2026-11-06).
    try:
        fz = None if _long_ask else _C._pm_fuzzy_catalog_subject(
            ' '.join(_qu.phrases) if _qu_explicit else f"{text or ''} {subject_hint or ''}")
        if fz and fz.get('s3_key'):
            print(f"[pm-base] fuzzy catalog bind: {fz['subject']!r}")
            return {'subject': fz['subject'], 's3_key': fz['s3_key'],
                    'source': 'catalog'}
    except Exception:
        traceback.print_exc()
    if page_base is not None:
        return page_base

    # 3. Subscriber IQ runs count as pulled bases too.
    try:
        import prometheus_analysis as _pma_si
        idx = _pma_si._load_subiq_index(_H.s3_client, _H.SUBSCRIBER_S3_BUCKET)
        shows = [v[0] for v in idx.values()]
        named = _pma_si._xmod_subject_from_text(
            f"{subject_hint or ''} {text or ''}", shows)
        if named:
            tk = _pma_si._xmod_title_key(named)
            if tk and tk in idx:
                return {'subject': idx[tk][0],
                        's3_key': f"subiq:{idx[tk][1]}",
                        'source': 'subscriber_iq'}
    except Exception:
        traceback.print_exc()
    return None


# <<block: 2>>
_pm_generation_base = _primary_profile.held(_pm_generation_base, lambda: _C._profile_catalog_for_chat())   # the primary is the default; a year cut only when the words name its year (2026-11-12)


# <<block: 3>>
_pm_generation_base = __import__('prometheus.named_title', fromlist=['held']).held(_pm_generation_base)   # a file about another title is never the base of a question that names its own (2026-11-19)


__all__ = ['_pm_generation_base']
