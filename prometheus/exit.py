"""The one exit every reader-facing string passes (stage 4 of the
2026-11-19 rebuild, approved by Jenna 2026-11-20).

One pipeline, one fixed order (``ORDER``), one vocabulary table
(``prometheus.vocabulary``). Replies, mail, PDFs, decks, CSV cells and
partner responses all leave through here:

  ``text(s, kind)``       one string, by kind
  ``payload(raw, ...)``   a reply payload in place (what envelope.wrap runs)
  ``mail(subject, body)`` outbound copy, with the refusal reason when the
                          door must refuse
  ``partner(v1)``         a key caller's response (the belt, fails closed)
  ``check(s, kind)``      what the pipeline would still object to
  ``templates()``         every stock sentence the modules compose, so a
                          test can prove the pipeline leaves each one alone

The passes themselves still live where they grew up (``guards``,
``migration.source_names``, ``methodology``, ``people_bounds``,
``subject_display``, ``dashboard_behavior``, ``index_format``,
``migration.peacock_actuals``, ``door_words``); this module owns the order
they run in and the table they read. ``prometheus_analysis.scrub_user_text``
delegates to ``text(s, PROSE)`` and returns the same bytes it did before
stage 4, plus the HH rule.

The field kinds (``vocabulary.KINDS``): PROSE is a reply, a mail body,
deck and PDF text or a CSV cell; NAME is a subject, title, brand or
person and is never rewritten; CHIP is a quick-reply label; BRIEF_FIELD
is the brief card's own audience sentence (the sample pass only, exactly
as before: the "Estimated audience" label is Jenna's decision and is not
changed here); DECK_COPY is prose plus the door rule; PARTNER is the key
belt; FILENAME is a delivered file's name.

Every pass is fail-safe: a pass that raises is skipped and the string
goes on to the next one. Nothing here ever empties a non-empty string.
No kill switch: the exit has no off position. No em dashes.
"""
from __future__ import annotations

import re

from . import vocabulary as V

PROSE, NAME, CHIP, BRIEF_FIELD, DECK_COPY, PARTNER, FILENAME, MAIL = (
    V.PROSE, V.NAME, V.CHIP, V.BRIEF_FIELD, V.DECK_COPY, V.PARTNER, V.FILENAME, V.MAIL)

# The order the passes run in, for prose. Read top to bottom.
ORDER = (
    'hold_definitions',   # the allowlisted view-definition lines out of reach
    'sources',            # the name comes off, the fact stays (migration.source_names)
    'dashes',             # em, en, bar to a hyphen
    'words',              # the word swaps (vocabulary.WORD_SWAPS)
    'estimate_noun',      # a figure, never an estimate
    'frame_break',        # a sentence that cites a vendor or claims off-clickstream behaviour goes
    'peacock',            # a figure off the Peacock record never ships
    'method',             # sentences about how a figure was made go; the figures stay
    'dashboard',          # no guessed mechanism, no product that does not exist
    'keys',               # an upper-snake key or a dated file stem becomes a name
    'spaces',             # one space between words
    'people_bounds',      # no count above the US or its base
    'index_format',       # an index of four digits reads with its separator
    'door_words',         # deck copy only
    'doubled_article',    # "the the", "the The Doors", "of of": one article
    'release_definitions',
)

_TWO_SPACES_RX = re.compile(r'[ \t]{2,}')
_SENTENCE_SPLIT_RX = re.compile(r'(?<=[.!?])\s+')
_EM_RX = re.compile('[ \t]*[\u2014\u2015][ \t]*')
# A doubled article is never English: a template that says "the {title}"
# meets a title that starts with The ("the The Fundamentals of Caring"),
# or a referent label that already carries its article ("the the last
# read"). The second copy stays (it carries the title's capital); only
# these four words, which are never legitimately repeated (replay fails
# 7dac6631b6e6 and f78c9f3ca752, 2026-11-20).
_DOUBLED_ARTICLE_RX = re.compile(r'\b(the|an?|of)\s+(\1)\b', re.I)


# ------------------------------------------------------------- the passes
def dashes(s):
    """Em dash and horizontal bar read as ' - ' (the spaces around them
    folded into the one pair), the en dash as '-'. The same bytes the
    old per-character replace plus the space collapse produced."""
    s = _EM_RX.sub(' - ', s)
    return s.replace('\u2013', '-')


def doubled_article(s):
    """"the the last read" -> "the last read"; "the The Doors" -> "The Doors";
    "The the Doors" -> "The Doors" (a sentence keeps its capital)."""
    def _one(m):
        keep = m.group(2)
        if m.group(1)[:1].isupper() and keep[:1].islower():
            keep = keep[:1].upper() + keep[1:]
        return keep
    return _DOUBLED_ARTICLE_RX.sub(_one, s)


# A sentence that names off-clickstream behaviour only to say it sits
# outside what is reported is the honest boundary statement the standing
# rules ask for, not a claim; it stays when it carries no figure (stage 4
# template check, 2026-11-20: the provenance answer lost "behavior with no
# digital trace, like linear TV tune-in or in-store purchases, is called
# out as outside it rather than guessed"). A sentence with a number in it
# is read as a claim whatever else it says.
_BOUNDARY_RX = re.compile(
    r"\boutside (?:it|of it|what|the data|our data|this|the (?:read|file|measurement|window)|crosswalk)\b|"
    r"\bno digital trace\b|\bnot (?:observed|measured|in (?:the|our) data|something we (?:see|measure|report))\b|"
    r"\b(?:cannot|can't|do not|don't|does not|doesn't) (?:see|measure|report|cover|observe|track)\b|"
    r"\bstays? out\b|\bout of scope\b|\bis called out as outside\b", re.I)
_ANY_FIGURE_RX = re.compile(r'\d')


def _frame_break(sentence):
    """A citation, a measurement-firm name, or an off-clickstream claim."""
    if V.CITATION_RX.search(sentence) or V.names_source(sentence):
        return True
    if not V.OFFCLICK_RX.search(sentence):
        return False
    if _BOUNDARY_RX.search(sentence) and not _ANY_FIGURE_RX.search(sentence):
        return False
    return True


def drop_frame_breaking_sentences(s):
    """Remove whole sentences that cite a vendor or assert an
    off-clickstream behaviour. Never empties a reply: when every sentence
    would drop, the text returns unchanged (the calm layers upstream own
    that case)."""
    if not _frame_break(s):
        return s
    out_lines = []
    changed = False
    for line in s.split('\n'):
        parts = _SENTENCE_SPLIT_RX.split(line) if line.strip() else [line]
        kept = [p for p in parts if not _frame_break(p)]
        if len(kept) != len(parts):
            changed = True
        out_lines.append(' '.join(kept).strip() if line.strip() else line)
    out = '\n'.join(out_lines)
    out = re.sub(r'\n{3,}', '\n\n', out).strip()
    if not out:
        return s
    if changed:
        try:
            print('[scrub] dropped frame-breaking sentence(s) (citation or off-clickstream claim)')
        except Exception:
            pass
    return out


def _peacock(s):
    from migration import peacock_actuals as _pk_net
    return str(_pk_net.scrub(s) or '') or s


def _method(s):
    from . import guards as _g
    return _g.scrub_method_language(s)


def _dashboard(s):
    from . import dashboard_behavior as _dbq
    return _dbq.scrub(s)


def _keys(s):
    from . import subject_display as _sd
    return _sd.scrub_keys(s)


def _people_bounds(s):
    from . import people_bounds as _pb
    return _pb.clean(s)


def _index_format(s):
    from .index_format import separate_index_figures as _sep
    return _sep(s)


def _door_words(s):
    from . import door_words as _dw
    return _dw.scrub_read(s)


def _safe(fn, s, *, note=None):
    try:
        out = fn(s)
        return out if isinstance(out, str) else s
    except Exception as e:
        if note is not None:
            note(e)
        return s


# ----------------------------------------------------------------- text
def text(s, kind=PROSE, *, note=None):
    """One string through the pipeline. ``note(exc)`` is called for a
    pass that raised (the caller's fault log); the string still ships."""
    s = str(s or '')
    if not s or kind == NAME:
        return s
    if kind == BRIEF_FIELD:
        # the brief card prints the draft's own audience sentence verbatim;
        # it says US people and nothing else (Jenna 2026-10-26). Only the
        # sample pass runs: the label is Jenna's.
        if V.SAMPLE_VOCAB_RX.search(s):
            from . import guards as _g
            return _safe(_g.scrub_sample_vocabulary, s, note=note)
        return s
    if kind == PARTNER:
        from . import guards as _g
        return _g.scrub_partner_text(s)
    if kind == FILENAME:
        from . import door_words as _dw
        return _safe(_dw.scrub_filename, dashes(s), note=note)
    # PROSE, CHIP, DECK_COPY, MAIL: the full order
    s, held = V.hold(s)
    s = _safe(V.strip_named_sources, s, note=note)
    s = dashes(s)
    s = V.swap_words(s)
    s = V.swap_estimate_noun(s)
    s = _safe(drop_frame_breaking_sentences, s, note=note)
    s = _safe(_peacock, s, note=note)
    s = _safe(_method, s, note=note)
    s = _safe(_dashboard, s, note=note)
    s = _safe(_keys, s, note=note)
    s = _TWO_SPACES_RX.sub(' ', s)
    s = _safe(_people_bounds, s, note=note)
    s = _safe(_index_format, s, note=note)
    if kind == DECK_COPY:
        s = _safe(_door_words, s, note=note)
    s = doubled_article(s)
    if held:
        s = V.release(s, held)
    return s


# -------------------------------------------------------------- payload
_TEXT_FIELDS = ('reply', 'message', 'question', 'summary', 'text', 'error')


def _light(s):
    """The three rules every field gets whatever else runs on it: no dash
    character, no HH, no doubled article. Nothing else; a lane's template
    is otherwise its own (the template test proves each one survives the
    full pipeline)."""
    return doubled_article(V.swap_hh(dashes(s)))


def _light_fields(raw):
    for k in _TEXT_FIELDS:
        v = raw.get(k)
        if isinstance(v, str) and v:
            nv = _light(v)
            if nv != v:
                raw[k] = nv
                raw.setdefault('_scrubbed', []).append(k + ':light')
    fu = raw.get('followups')
    if isinstance(fu, list) and any(isinstance(c, str) and _light(c) != c for c in fu):
        raw['followups'] = [_light(c) if isinstance(c, str) else c for c in fu]
        raw.setdefault('_scrubbed', []).append('followups:light')
    opts = raw.get('options')
    if isinstance(opts, list):
        for o in opts:
            if isinstance(o, dict):
                for k in ('label', 'send'):
                    v = o.get(k)
                    if isinstance(v, str) and v and _light(v) != v:
                        o[k] = _light(v)
    # the brief card's own sentence gets the two rules too (its label is
    # Jenna's and is not touched; a dash or an HH in it is not the label)
    drafts = list(raw.get('spec_drafts') or []) if isinstance(raw.get('spec_drafts'), list) else []
    if isinstance(raw.get('spec_draft'), dict):
        drafts.append(raw['spec_draft'])
    for d in drafts:
        if isinstance(d, dict):
            v = d.get('estimated_audience_sentence')
            if isinstance(v, str) and v and _light(v) != v:
                d['estimated_audience_sentence'] = _light(v)


def payload(raw, *, surface, decision=None, thread_id='', question=''):
    """A reply payload through the exit, in place. The passes
    envelope.wrap ran before stage 4, in the same order, plus the light
    rules on every text field and chip. Each pass is fail-safe."""
    if not isinstance(raw, dict):
        return raw
    # method language, the Reader's echo, sample vocabulary, dashboard
    # guesswork, the named-title line, the brief card field (2026-10-05)
    try:
        from . import guards as _g
        _g.scrub_method_language_payload(raw)
    except Exception:
        pass
    # the door rule is about decks (2026-11-11): a reply that IS deck copy
    try:
        from . import door_words as _dw
        _dw.scrub_payload(raw)
    except Exception:
        pass
    # a promise has a record (2026-11-11, promise_truth)
    try:
        from . import promise_truth as _ptr
        _ptr.back_calm(raw, thread_id=thread_id or '')
    except Exception:
        pass
    # a people count never passes the US population or its base (2026-11-11)
    try:
        from . import people_bounds as _pb
        _pb.guard_payload(raw)
    except Exception:
        pass
    if surface == 'analyze':
        # a partnership's dollar value is the valuation's to state (2026-11-11)
        try:
            from . import partnership_value as _pvg
            _pvg.guard_payload(raw, decision=decision, question=question)
        except Exception:
            pass
        # what a valuation costs is fixed (2026-11-16)
        try:
            from . import valuation_price as _vpg
            _vpg.guard_payload(raw, decision=decision, question=question)
        except Exception:
            pass
        # chat answers read as answers, not reports (2026-10-06)
        try:
            from . import reply_shape as _rs
            _rs.shape_payload(raw)
        except Exception:
            pass
    # the light rules on every text field and chip (stage 4): no dash
    # character, no HH, whatever lane composed the line
    try:
        _light_fields(raw)
    except Exception:
        pass
    return raw


# ----------------------------------------------------------------- mail
def mail(subject, body, family=None):
    """Outbound copy through the house scrub. Returns (subject, body,
    refusal): ``refusal`` is '' when the door may send, else the reason
    it must refuse (the story of what happened, checked on the words as
    written before any scrub; a banned token left after the scrub)."""
    try:
        from . import guards as _g
        story = _g.cause_language(subject, mail=True) + _g.cause_language(body, mail=True)
    except Exception:
        story = []
    if story:
        return (str(subject or ''), str(body or ''),
                'explains what happened; a reader gets the new data, never the story: '
                + ' | '.join(x.strip()[:90] for x in story[:3]))
    subject_c = text(subject, PROSE).replace('\n', ' ').strip()[:200]
    body_c = text(body, PROSE)
    try:   # views are never left without what a view means (Jenna 2026-10-08)
        from . import methodology as _meth
        body_c = _meth.attach_view_definition(body_c, family=family)
        body_c = _meth.ensure_period_note(body_c)
    except Exception:
        pass
    bad = V.mail_banned_tokens(subject_c) + V.mail_banned_tokens(body_c)
    if bad:
        return subject_c, body_c, f"banned vocabulary after scrub: {sorted(set(bad))}"
    return subject_c, body_c, ''


# -------------------------------------------------------------- partner
def partner(v1):
    """The key belt over a v1 response, in place and returned."""
    from . import guards as _g
    return _g.scrub_partner_payload(v1)


# ---------------------------------------------------------------- check
def check(s, kind=PROSE):
    """What the pipeline would still object to in ``s`` after running on
    it. Empty means clean. One checker for every test and audit."""
    return V.hits(text(s, kind), kind)


# ------------------------------------------------------------ templates
# Every stock sentence the modules compose, rendered with sample values.
# The template test runs each through text(kind) and fails if a byte
# changes, so a template can never again carry a word an exit would cut
# ("the countUS consumers we measure", 2026-11-19). A module may also
# publish its own ``TEMPLATES = ((kind, text), ...)``; they are collected.
_TEMPLATE_MODULES = (
    'orders', 'methodology', 'priced_read', 'build_facts', 'guards', 'envelope', 'ask_outcome', 'bpiq_deck',
    'data_floor', 'read_landing', 'sizing_lane', 'product_facts', 'status_lane', 'capability', 'pricing_lane',
    'definitions', 'wayfinding', 'answer_ready', 'subject_rules', 'unprojected', 'segment_sizing', 'insight_ask',
    'brief_terms', 'build_lane', 'no_build', 'us_scope', 'study_statements', 'twin_notify', 'outbound_mail',
    'bpiq_wizard', 'digital_twin_lane', 'journey_lane', 'catalog_lane', 'deliverables', 'send_file', 'reconcile_lane',
    'number_provenance', 'promise_truth', 'reply_truth', 'order_truth', 'question_first', 'dashboard_behavior',
    'named_title', 'thread_followups', 'help_writing', 'where_watched', 'service_subscribers', 'spend_cap',
)
_CONST_NAME_RX = re.compile(r'^[A-Z][A-Z0-9_]{2,}$')
_SENTENCE_LIKE_RX = re.compile(r'^[A-Z"\'(].{24,}[.!?:)]$', re.S)


def _looks_like_template(name, value):
    """A module constant that reads as a sentence a reader could see: not
    a regex, not a URL or key, not a prompt to the model, not a wall of
    text."""
    if not isinstance(value, str) or len(value) < 25 or len(value) > 2000:
        return False
    if name.endswith(('_RX', '_RE', '_PAT', '_PATTERN', '_PATTERNS', '_PREFIX', '_KEY', '_URL', '_PATH', '_TOKEN')):
        return False
    # text written for the model, never for a reader: prompts, charters,
    # rulebooks, the instruction notes a re-ask carries
    if any(w in name for w in ('PROMPT', 'CHARTER', 'RULEBOOK', 'SYSTEM', 'INSTRUCTION', 'GUIDANCE', 'SCHEMA',
                               'RULES', '_NOTE_FOR_MODEL', 'CONVERSATIONAL_NOTE', 'REASK')):
        return False
    v = value.strip()
    if '\\b' in v or '(?' in v or v.startswith(('http', 's3://', '/')) or '{http' in v:
        return False
    return ' ' in v and bool(re.search(r'[A-Za-z]{3,}', v))


def templates():
    """[(where, kind, text)] for every stock sentence the exit must leave
    alone: module constants that read as sentences, each module's own
    TEMPLATES, and the rendered samples below."""
    import importlib
    out = []
    seen = set()

    def _add(where, kind, s):
        s = str(s or '')
        if s and (where, s) not in seen:
            seen.add((where, s))
            out.append((where, kind, s))

    for modname in _TEMPLATE_MODULES:
        try:
            mod = importlib.import_module('prometheus.' + modname)
        except Exception:
            continue
        for item in (getattr(mod, 'TEMPLATES', None) or ()):
            try:
                kind, s = item
                _add(f'{modname}.TEMPLATES', kind, s)
            except Exception:
                continue
        for name in dir(mod):
            if not _CONST_NAME_RX.match(name) or name == 'TEMPLATES':
                continue
            try:
                val = getattr(mod, name)
            except Exception:
                continue
            if isinstance(val, (tuple, list)):
                for i, v in enumerate(val):
                    if _looks_like_template(name, v):
                        _add(f'{modname}.{name}[{i}]', PROSE, v)
                continue
            if _looks_like_template(name, val):
                # the methodology's view-definition lines ride as PROSE too:
                # they are held out by exact text, so the pipeline must
                # return them byte for byte like any other sentence
                _add(f'{modname}.{name}', PROSE, val)
    # rendered samples: the sentences that carry numbers or names
    for where, kind, s in _rendered_samples():
        _add(where, kind, s)
    return out


def _rendered_samples():
    out = []
    try:
        from . import orders as _o
        out.append(('orders.RAW_PHRASE in its sentence', PROSE,
                    f"Every row carries both the projected US figure and {_o.RAW_PHRASE}."))
    except Exception:
        pass
    try:
        from . import priced_read as _pr
        out.append(('priced_read.COST_LINE', PROSE, _pr.COST_LINE.format(price='$100')))
    except Exception:
        pass
    # the brief card's own sentence: the label is Jenna's (2026-08-24); the
    # card field runs the sample pass only, so this exact line must survive
    out.append(('brief card estimated_audience_sentence', BRIEF_FIELD,
                'Estimated audience: about 5,222,482 to 7,279,639 US people.'))
    out.append(('envelope default: job', PROSE, 'Working on it. The read will land in this thread when it is ready.'))
    out.append(('envelope default: deck job', PROSE, 'Working on it. The deck will land in this thread when it is ready.'))
    out.append(('envelope default: draft', PROSE, 'Here is the brief. Approve it to start the build.'))
    out.append(('envelope default: clarify', PROSE,
                'Which subject should this be on? Name the show, brand or person and I will take it from there.'))
    try:
        from . import subject_rules as _sr
        out.append(('subject_rules.clarify_for("")', PROSE, _sr.clarify_for('')))
    except Exception:
        pass
    try:
        from . import methodology as _meth
        out.append(('methodology.others_part()', PROSE, _meth.others_part()))
        out.append(('methodology.view_definition_panel()', PROSE, _meth.view_definition_panel()))
        for i, line in enumerate(_meth.others_lines(peacock=True)):
            out.append((f'methodology.others_lines[{i}]', PROSE, line))
    except Exception:
        pass
    try:
        from . import data_floor as _dfl
        out.append(('data_floor.SENTENCE', PROSE, _dfl.SENTENCE))
    except Exception:
        pass
    try:
        from . import guards as _g
        out.append(('guards.PROVENANCE_REPLY', PROSE, _g.PROVENANCE_REPLY))
    except Exception:
        pass
    return out


__all__ = ['ORDER', 'text', 'payload', 'mail', 'partner', 'check', 'templates', 'dashes', 'doubled_article',
           'drop_frame_breaking_sentences',
           'PROSE', 'NAME', 'CHIP', 'BRIEF_FIELD', 'DECK_COPY', 'PARTNER', 'FILENAME', 'MAIL']
