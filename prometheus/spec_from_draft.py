"""From an approved brief draft to the build spec the engine runs.

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
import os
import traceback
import re
from prometheus import faults as _faults
import boto3

# <<moved>>
# <<block: 1>>
def _spec_from_draft(draft):
    """Convert the Claude-emitted draft into the exact spec dict shape that
    synth_engine_row_by_row.synthesize_from_spec expects on Hetzner."""
    # Tripwire scrub for every string field minted here (2026-08-24).
    # Resolved via globals() so offline harnesses that AST-extract this
    # function standalone degrade to a passthrough instead of a
    # NameError (same defensive posture as the in-function imports
    # below).
    _scrub = _H.get('_scrub_spec_text') or (
        lambda value, **_kw: '' if value is None else str(value))
    subject = draft.get('subject') or draft.get('name') or 'Unknown Subject'
    subject = _scrub(subject, field='subject', subject=str(subject)[:80],
                     max_len=200, single_line=True) or 'Unknown Subject'
    # ---- Phantom-subject + date-clause choke point (2026-09-10, Jenna:
    # Audible defect). This runs on EVERY external surface - the
    # dashboard chatbot approve route and partner API v1 both mint their
    # spec here - so it is the one place that catches a bad subject
    # regardless of which interpret path produced it.
    #
    # (a) Strip a trailing 'Date Range: X to Y' clause out of the name
    #     and route the window into spec['date_range'] instead. The
    #     shipped defect baked the clause into the subject AND ignored
    #     the window.
    _dr_from_subject = None
    try:
        _subj_no_dr, _dr_from_subject = _H._split_trailing_date_range(subject)
        if _subj_no_dr and _subj_no_dr != subject:
            print(f"[spec-guard] stripped date clause from subject: "
                  f"{subject!r} -> {_subj_no_dr!r} "
                  f"(window={_dr_from_subject or 'unparsed, default kept'})")
            subject = _subj_no_dr
    except Exception as _fx:
        _faults.note('chat', _fx)
    # (b) A request / instruction line is not a buildable subject. Both
    #     callers are exception-safe (the chatbot approve route is
    #     wrapped by _chatbot_route_guard, the v1 path has its own
    #     try/except returning 'could not interpret prompt'), so
    #     raising here surfaces as a calm partner-safe message and no
    #     frame is ever created. This is the "genuine upstream build
    #     failure surfaced BEFORE a frame exists" case from
    #     no-rebuild-level-correction.mdc, not a held file.
    try:
        _is_req = _H._is_request_instruction_line(subject)
    except Exception:
        _is_req = False
    if _is_req:
        print(f"[spec-guard] REJECT phantom subject (request line): "
              f"{subject!r}")
        raise ValueError(
            f"refusing to build: subject looks like a request line, "
            f"not an audience: {subject!r}")
    # Canonical-casing choke point (2026-08-24 SHARKNINJA directive):
    # subject + file_stem minted here flow verbatim into the worker's
    # TU key and avid display name, so fixing the casing here fixes
    # every downstream filename.
    _subject_in = subject
    try:
        subject = _H._canonical_subject_casing(subject) or subject
    except Exception:
        subject = _subject_in
    if subject != _subject_in:
        try:
            print(f"[spec-casing] subject {_subject_in!r} -> {subject!r}")
        except Exception as _fx:
            _faults.note('chat', _fx)
        stem = subject.replace(' ', '_').replace('/', '_')
    else:
        stem = draft.get('file_stem') or subject.replace(' ', '_').replace('/', '_')
    stem = ''.join(c for c in stem if c.isalnum() or c in '_-') or 'Profile'
    def _scrub_row_cell(v):
        return _scrub(v, field='row_cell', subject=subject, max_len=200,
                      single_line=True)

    subject_rows_raw = draft.get('subject_rows') or []
    subject_rows = []
    for row in subject_rows_raw:
        if isinstance(row, list) and len(row) >= 2:
            subject_rows.append((_scrub_row_cell(row[0]),
                                 _scrub_row_cell(row[1])))
        elif isinstance(row, dict) and 'column' in row and 'value' in row:
            subject_rows.append((_scrub_row_cell(row['column']),
                                 _scrub_row_cell(row['value'])))
    extra_rows_raw = draft.get('extra_rows') or []
    extra_rows = []
    for row in extra_rows_raw:
        if isinstance(row, list) and len(row) >= 2:
            extra_rows.append([_scrub_row_cell(row[0]),
                               _scrub_row_cell(row[1])])
        elif isinstance(row, dict) and 'column' in row and 'value' in row:
            extra_rows.append([_scrub_row_cell(row['column']),
                               _scrub_row_cell(row['value'])])

    # ── Phantom-column heads-up log (added 2026-08-19).
    # If Claude puts anchor rows in subject_rows / extra_rows with a
    # top-level MASTER_CATEGORIES grouping label as the Column
    # ('BRAND', 'CONTENT', 'SPORT', 'HEALTHCARE'), we let them flow
    # through to Hetzner. The worker's canonicalize_phantom_rows()
    # (migration/synth_hostmap_augment.py) will look each Value up in
    # ClickHouse's reference.host_mapping - the SAME hostmap the
    # augment pass already reads - and remap the row to the correct
    # canonical sub-category, preserving Claude's research instead of
    # dropping it. Non-hostmap brands get dropped on the Hetzner side
    # per Rule #4.
    #
    # We only log here so the operator can see what will be remapped;
    # no spec mutation.
    _PHANTOM_COLUMNS = {'BRAND', 'CONTENT', 'SPORT', 'HEALTHCARE'}
    _phantom_seen = []
    for col, val in subject_rows:
        if str(col).strip().upper() in _PHANTOM_COLUMNS:
            _phantom_seen.append(('subject_rows', col, val))
    for entry in extra_rows:
        if str(entry[0]).strip().upper() in _PHANTOM_COLUMNS:
            _phantom_seen.append(('extra_rows', entry[0], entry[1]))
    if _phantom_seen:
        try:
            print(
                f"[phantom-column-heads-up] {subject!r}: {len(_phantom_seen)} "
                f"row(s) with grouping-label Column will be remapped on "
                f"Hetzner via hostmap. Rows: {_phantom_seen[:8]}"
                + (f' ...+{len(_phantom_seen) - 8} more' if len(_phantom_seen) > 8 else '')
            )
        except Exception as _fx:
            _faults.note('chat', _fx)

    # ── Affinity-pin guard (2026-08-25, generalizing the 2026-08-19
    # car-pin guard after run vQGroDyp7TLjjA: a QSR subject's spec
    # carried peer/affinity brands in subject_rows - Pressed Juicery,
    # Whole Foods - which the engine pins at 100, so the quality gate
    # held the file twice and the run burned 67 minutes before failing
    # terminally). subject_rows are HARD PINS; a value that is not the
    # subject itself may only pin in the columns where the interpret
    # contract legitimately pins a non-subject value (distribution
    # homes for consumers-scope IP, leagues/teams for sports, IP
    # content columns whose value is the title). Everything else
    # (QSR, GROCERY, CPG, RETAILERS, AUTOMOBILE, ...) demotes to
    # extra_rows: the research is preserved unpinned and the row-by-row
    # engine assigns a plausible BP from baseline + persona lift.
    _PIN_LEGAL_COLUMNS = {
        # Distribution / platform homes (consumers-scope IP pin rule).
        # 2026-08-27: WHERE THEY SHOP removed after the Chobani Buyers
        # hold - retailers a CPG sells through are affinity rows, never
        # pins; a retailer-scoped universe ("Sephora Shoppers") still
        # pins via the subject-value match below.
        'STREAMING/PLATFORM', 'STREAMING VIDEO', 'BROADCAST/CABLE',
        'APP/PLATFORM', 'VMVPD/FAST', 'VIRTUAL MVPD/FAST',
        'VIRTUAL MVPD FAST', 'VMVPD', 'FAST PLATFORM', 'FAST CHANNEL',
        'MOVIE THEATER', 'STREAMING MUSIC', 'STREAMING/MUSIC',
        'APP/PLATFORM USAGE',
        # Sports companions (team + league + conference pins)
        'SPORTS TEAM', 'MLB', 'NBA', 'NFL', 'NHL', 'MLS', 'WNBA',
        'MILB', 'EPL', 'LA LIGA', 'SERIE A', 'LIGUE 1', 'BUNDESLIGA',
        'CFB', 'SOCCER', 'AL', 'NL', 'AFC', 'NFC',
        'SPORTS ORGANIZATIONS', 'SPORTS ORGANIZATION',
        # IP content columns (value is the title, subject may be
        # '<Title> Viewers')
        'SERIES', 'MOVIE', 'PODCAST', 'GAMES', 'GAME PLAYERS',
        'VERTICAL SHORTS',
        # Metadata rows are never affinity pins
        'SUBJECT', 'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY',
    }
    _norm_subj = ''.join(ch for ch in subject.lower() if ch.isalnum())
    _entity_part = subject.split(' - ', 1)[0].strip()
    _norm_entity = ''.join(ch for ch in _entity_part.lower()
                           if ch.isalnum())
    _clean_subject_rows_v3 = []
    _demoted_pins = []
    for col, val in subject_rows:
        col_up = str(col).strip().upper()
        val_norm = ''.join(ch for ch in str(val).lower() if ch.isalnum())
        _is_subject_val = (
            val_norm and (val_norm == _norm_subj or val_norm == _norm_entity
                          or (len(val_norm) >= 4
                              and (val_norm in _norm_subj
                                   or val_norm in _norm_entity))))
        if col_up not in _PIN_LEGAL_COLUMNS and not _is_subject_val:
            _demoted_pins.append([col, val])
            continue
        _clean_subject_rows_v3.append((col, val))
    if _demoted_pins:
        try:
            print(
                f"[affinity-pin-guard] {subject!r}: demoted "
                f"{len(_demoted_pins)} non-subject subject_row pin(s) to "
                f"extra_rows: {_demoted_pins[:6]}"
                + (f' ...+{len(_demoted_pins) - 6} more'
                   if len(_demoted_pins) > 6 else '')
            )
        except Exception as _fx:
            _faults.note('chat', _fx)
        _existing_extra_norms = {
            ''.join(ch for ch in str(e[1]).lower() if ch.isalnum())
            for e in extra_rows if len(e) >= 2}
        for _dp in _demoted_pins:
            _dpn = ''.join(ch for ch in str(_dp[1]).lower()
                           if ch.isalnum())
            if _dpn and _dpn not in _existing_extra_norms:
                extra_rows.append([_dp[0], _dp[1]])
                _existing_extra_norms.add(_dpn)
    subject_rows = _clean_subject_rows_v3

    # ── subject_rows backstop (2026-08-20, Jenna's 5-platform batch:
    # Apple TV+ / Google Play got 'queue returned 400: subject_rows
    # must be a non-empty list'). Array-mode Claude sometimes omits
    # subject_rows on individual elements. The queue hard-requires it
    # for build decisions, and the worker's hostmap augment benefits
    # from at least the entity anchor. Synthesize the minimal anchor
    # the healthy specs all share: [[brand_category, entity]] where
    # entity is the parent part of a 'Parent - Cohort' subject.
    # Harmless for derive_cut (worker ignores spec.subject_rows).
    if not subject_rows:
        _bc = str(draft.get('brand_category') or '').strip()
        _entity = subject.split(' - ', 1)[0].strip() or subject
        if _bc:
            subject_rows = [(_bc, _entity)]
            try:
                print(f"[spec-backstop] {subject!r}: subject_rows was "
                      f"empty - anchored with [[{_bc!r}, {_entity!r}]]")
            except Exception as _fx:
                _faults.note('chat', _fx)

    # Enforce the "no round sample sizes" rule from the workspace rule
    # `.cursor/rules/no-round-sample-sizes.mdc`. Every subject_raw_* that
    # flows into the profile engine passes through this function - both
    # dashboard chatbot and partner API. Jitter deterministically off
    # subject, so repeat calls for the same subject produce the same
    # messy value (idempotent, cross-cut coherent).
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
    except Exception:
        # Extremely defensive - if the helper import ever fails we still
        # want the pipeline to run, so provide an inline shim that does
        # the minimum: a subject-hashed offset to break trailing zeros.
        import hashlib as _hl
        def ensure_messy_sample_size(subj, v, minimum=800, default_if_missing=9873):
            try:
                x = int(round(float(v))) if v is not None else 0
            except Exception:
                x = 0
            if x <= 0:
                x = default_if_missing
            off = int(_hl.sha256(f"{subj}|{x}".encode()).hexdigest()[:8], 16) % 197 - 98
            x = x + off
            return max(minimum + 7, x if x % 1000 != 0 else x + 47)

    subject_raw_tu = ensure_messy_sample_size(subject, draft.get('subject_raw_tu'))
    subject_raw_avid = ensure_messy_sample_size(
        f"{subject}|avid", draft.get('subject_raw_avid'),
        default_if_missing=2937,
    )

    # ── Public-metric ceiling cap (2026-08-19, Jenna directive) ─────
    # If audience_type triggers the cap (followers / subscribers /
    # viewers / listeners / attendees / users), the US Gen Pop
    # Projection column cannot exceed the underlying public metric.
    # Since projection scales linearly with subject_raw (Proj =
    # Raw / 10M * 329.9M for every row, including SAMPLE SIZE),
    # capping subject_raw here caps every downstream projection.
    # See migration/follower_ceiling.py for the full audience_type
    # catalog + math.
    #
    # Belt-and-suspenders: the Hetzner worker re-applies the cap after
    # the persona-research agent (web-search) refines follower_ceiling,
    # and a post-generation enforcer verifies the final projection.
    _audience_type_raw = str(draft.get('audience_type') or 'general').strip().lower()
    _follower_ceiling_in = draft.get('follower_ceiling')
    audience_type_out = 'general'
    follower_ceiling_out = None
    try:
        from migration.follower_ceiling import (
            is_capped_audience as _fc_is_capped,
            cap_subject_raw as _fc_cap,
            normalize_follower_ceiling as _fc_normalize,
            summarize_cap as _fc_summary,
            CAPPED_AUDIENCE_TYPES as _fc_types,
        )
        if _fc_is_capped(_audience_type_raw):
            # Preserve the specific audience_type variant Claude picked
            # (viewers vs listeners vs followers etc.) rather than
            # flattening to 'followers'. The downstream cap math is
            # identical, but the persona-research agent uses this to
            # decide which public metric to look up.
            audience_type_out = _audience_type_raw \
                if _audience_type_raw in _fc_types else 'followers'
            follower_ceiling_out = _fc_normalize(_follower_ceiling_in)
            capped_tu, meta_tu = _fc_cap(subject_raw_tu, follower_ceiling_out)
            capped_av, meta_av = _fc_cap(subject_raw_avid, follower_ceiling_out)
            # Re-jitter the capped values so we don't ship round numbers
            # (see .cursor/rules/no-round-sample-sizes.mdc). The cap output
            # is deterministic from ceiling math and often ends in a zero;
            # ensure_messy_sample_size applies subject-hashed jitter and
            # guarantees a non-zero last digit. Cap FIRST, then jitter.
            subject_raw_tu = ensure_messy_sample_size(
                f"{subject}|{audience_type_out}|tu", capped_tu,
                default_if_missing=capped_tu or 9873,
            )
            subject_raw_avid = ensure_messy_sample_size(
                f"{subject}|{audience_type_out}|avid", capped_av,
                default_if_missing=capped_av or 2937,
            )
            # If jitter accidentally re-raised us above the cap, snap
            # back down. Rare but possible if jitter added a positive
            # offset and the cap was already at the ragged edge.
            _max_tu = meta_tu.get('max_allowed_raw') or 0
            _max_av = meta_av.get('max_allowed_raw') or 0
            if _max_tu and subject_raw_tu > _max_tu:
                subject_raw_tu = _max_tu
            if _max_av and subject_raw_avid > _max_av:
                subject_raw_avid = _max_av
            # Refresh the follower_ceiling_out with whatever was used
            # (may be the fallback if input was missing).
            follower_ceiling_out = meta_tu.get('follower_ceiling') or follower_ceiling_out
            for _msg in (_fc_summary(subject, 'TU', meta_tu),
                         _fc_summary(subject, 'AVID', meta_av)):
                if _msg:
                    try:
                        print(_msg)
                    except Exception as _fx:
                        _faults.note('chat', _fx)
    except Exception as _fc_err:
        # Never let cap-application break a build; log and continue.
        try:
            print(f"[follower-ceiling] {subject!r}: cap step raised "
                  f"{type(_fc_err).__name__}: {_fc_err}")
        except Exception as _fx:
            _faults.note('chat', _fx)

    # ── Mandatory anchor derivation + cross-spec duplicate guard ────
    # (2026-08-24, Florida/Iowa duplicated-sample defect; hardened
    # same day by Jenna's standing mandate: "the sample always has to
    # be based to the researched anchor".) Two guards at the choke
    # point where ensure_messy_sample_size runs:
    # 1. Every fresh-build sample must equal universe_anchor x
    #    engaged_share (within ~5% jitter tolerance) - reject-or-
    #    repair: a missing/absurd anchor is recovered from the draft
    #    prose when possible; a genuinely absent anchor flags the run
    #    with anchor_missing=true (visible in the ops email); a
    #    non-deriving sample is recomputed from the anchor.
    # 2. If the minted subject_raw byte-matches a DIFFERENT subject's
    #    recently minted value (rolling S3 ledger at
    #    system/recent_subject_raws.json), re-jitter deterministically
    #    so no two subjects ship identical samples.
    # The worker mirrors both in _run_new_build as defense in depth.
    _anchor_meta = _saved_trail = None
    try:
        from migration.sample_sizing_guards import apply_sizing_guards
        _guard_s3 = None
        try:
            _guard_s3 = boto3.client(
                's3',
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                region_name=_H.S3_REGION,
            )
        except Exception:
            _guard_s3 = None
        _anchor_prose = ' '.join(
            str(draft.get(k) or '')
            for k in ('persona_notes', 'assumptions', 'decision_reason',
                      'category_note')
        )
        # ── Subject benchmark anchors (2026-10-29, Jennifer Beals
        # audit). The subject's own Gen Pop row (US people who touched
        # the subject in the Gen Pop window) and its most recent prior
        # Total Universe replace a draft anchor the derivation rejects:
        # an anchor_source that is itself an engagement estimate, or a
        # round universe_anchor (divisible by 100,000) with no countable
        # source. The Beals draft carried 4,200,000 "estimated US adults
        # with meaningful digital engagement" and then discounted it
        # again by engaged_share 0.662. Fresh-build decisions only; a
        # subject with neither anchor keeps today's path. Fail-safe.
        _size_bench = _size_prior = None
        try:
            from migration.profile_build_size_guards import anchors_for_draft
            _size_bench, _size_prior = anchors_for_draft(
                draft, subject, stem, audience_type_out, s3=_guard_s3)
        except Exception as _sa_err:
            try:
                print(f"[size-benchmark] {subject!r}: anchor lookup skipped "
                      f"({type(_sa_err).__name__}: {_sa_err})")
            except Exception as _fx:
                _faults.note('chat', _fx)
        try:   # a saved audience size starts the build (2026-11-20, migration/saved_subject_size): the reader's own size, another window or a disagreeing benchmark keep today's path
            subject_raw_tu, subject_raw_avid, _saved_trail = __import__('migration.saved_subject_size', fromlist=['spec_step']).spec_step(draft, subject, subject_raw_tu, subject_raw_avid, audience_type_out, follower_ceiling_out, _size_bench, _size_prior, _guard_s3)
        except Exception as _fx:
            _faults.note('chat', _fx)
        subject_raw_tu, subject_raw_avid, _anchor_meta = apply_sizing_guards(
            subject, subject_raw_tu, subject_raw_avid,
            universe_anchor=draft.get('universe_anchor'),
            engaged_share=draft.get('engaged_share'),
            anchor_source=draft.get('anchor_source'),
            prose=_anchor_prose,
            s3_client=_guard_s3,
            persona_signature=draft.get('_persona_signature'),
            benchmark=_size_bench,
            prior_universe=_size_prior,
        )
    except Exception as _sg_err:
        try:
            print(f"[sizing-guard] {subject!r}: guard step raised "
                  f"{type(_sg_err).__name__}: {_sg_err} - continuing "
                  f"with unguarded values")
        except Exception as _fx:
            _faults.note('chat', _fx)

    # ── Brand-category + subject-row canonicalization ───────────────
    # Defense in depth against the P-Valley class of defect (2026-08-19)
    # where Claude tagged a TV show as `STREAMING PLATFORM` and pinned
    # the show name inside the streaming column at 100%. If any of the
    # patterns below trip we correct in place - the interpret prompt is
    # the primary line of defense, this is the safety net.
    _bc_raw = str(draft.get('brand_category') or 'BRAND').strip()
    _bc_upper = _bc_raw.upper()
    # Canonical validation + normalization at spec time (2026-08-27,
    # Toca Boca hold). Exact match against MASTER_CATEGORIES first,
    # then alias / separator / singular-plural normalization (TOYS ->
    # TOY), then closest-canonical with a logged note. An unresolvable
    # label passes through so the final ship gate stays the LAST line
    # of defense, not the first. Shared implementation:
    # migration/brand_category_canon.py (the worker runs the same
    # normalization at build entry, so direct queue posts inherit it;
    # the partner API v1 builds specs through THIS function).
    try:
        from migration.brand_category_canon import (
            canonicalize_brand_category as _canon_bc,
        )
        _bc_canon, _bc_note = _canon_bc(_bc_raw)
        if _bc_note:
            print(f"[brand_category-normalize] {subject!r}: {_bc_note}")
        _bc_raw = _bc_canon or _bc_raw
        _bc_upper = _bc_raw.upper()
    except Exception as _bc_exc:
        # Fallback: the pre-2026-08-27 inline synonym map, so a module
        # load failure never regresses below the old behavior.
        print(f"[brand_category-normalize] {subject!r}: shared "
              f"canonicalizer unavailable ({_bc_exc}); inline map only")
        _NONCANON_TO_CANON = {
            'STREAMING PLATFORM': 'STREAMING/PLATFORM',
            'STREAMING PLATFORMS': 'STREAMING/PLATFORM',
            'STREAMING SERVICE': 'STREAMING/PLATFORM',
            'STREAMING SERVICES': 'STREAMING/PLATFORM',
            'SVOD': 'STREAMING/PLATFORM',
            'BROADCAST CABLE': 'BROADCAST/CABLE',
            'SEARCH ENGINE AI': 'SEARCH ENGINE/AI',
        }
        if _bc_upper in _NONCANON_TO_CANON:
            _bc_upper = _NONCANON_TO_CANON[_bc_upper]
            _bc_raw = _bc_upper

    # If the subject is a TV series / movie / podcast and Claude
    # tagged it as a streaming platform / broadcaster instead, flip it
    # to the correct content category. Signal comes from BOTH the
    # subject name shape AND the subject_rows Claude produced - if any
    # subject_row anchors it in SERIES / MOVIE / PODCAST, that's the
    # ground truth.
    _series_anchor = any(
        str(c).strip().upper() == 'SERIES' for c, _ in subject_rows
    )
    _movie_anchor = any(
        str(c).strip().upper() == 'MOVIE' for c, _ in subject_rows
    )
    _pod_anchor = any(
        str(c).strip().upper() == 'PODCAST' for c, _ in subject_rows
    )
    _platform_cats = {
        'STREAMING/PLATFORM', 'BROADCAST/CABLE', 'APP/PLATFORM',
        'STREAMING MUSIC', 'MEDIA', 'MOVIE THEATER', 'PLATFORMS',
        'STREAMING VIDEO', 'VIRTUAL MVPD/FAST', 'VIRTUAL MVPD FAST',
        'VMVPD/FAST', 'VMVPD',
    }
    if _series_anchor and _bc_upper in _platform_cats:
        print(f"[brand_category-normalize] {subject!r}: overriding "
              f"{_bc_raw!r} -> SERIES (subject_rows anchored to SERIES)")
        _bc_raw = 'SERIES'
    elif _movie_anchor and _bc_upper in _platform_cats:
        print(f"[brand_category-normalize] {subject!r}: overriding "
              f"{_bc_raw!r} -> MOVIE (subject_rows anchored to MOVIE)")
        _bc_raw = 'MOVIE'
    elif _pod_anchor and _bc_upper in _platform_cats:
        print(f"[brand_category-normalize] {subject!r}: overriding "
              f"{_bc_raw!r} -> PODCAST (subject_rows anchored to PODCAST)")
        _bc_raw = 'PODCAST'

    # Rebuild subject_rows: normalize non-canonical streaming column
    # names. Only strip a subject-name-in-platform-column pin when we
    # ALSO have a content anchor (SERIES/MOVIE/PODCAST) for that same
    # subject value - that's the P-Valley shape (miscategorized show).
    # For a real platform subject (Netflix, Hulu), the self-pin
    # ['STREAMING/PLATFORM', 'Netflix'] is correct and must be
    # preserved so downstream reasoning skips scoring it.
    _norm_subject_key = ''.join(
        ch for ch in subject.lower() if ch.isalnum()
    )
    _subject_has_content_anchor = _series_anchor or _movie_anchor or _pod_anchor
    _clean_subject_rows = []
    for col, val in subject_rows:
        col_raw = str(col).strip()
        col_up = col_raw.upper()
        # Canonicalize non-slash streaming column
        if col_up == 'STREAMING PLATFORM':
            col_up = 'STREAMING/PLATFORM'
            col_raw = 'STREAMING/PLATFORM'
        # Strip miscategorized subject-as-platform pin ONLY when this
        # is a content subject (has SERIES/MOVIE/PODCAST anchor). A
        # bare platform subject (Netflix, Hulu) keeps its self-pin.
        if _subject_has_content_anchor and col_up in _platform_cats:
            _val_key = ''.join(
                ch for ch in str(val).lower() if ch.isalnum()
            )
            if _val_key == _norm_subject_key:
                print(f"[subject_rows-normalize] {subject!r}: dropping "
                      f"content-vs-platform self-pin "
                      f"({col_up}, {val!r}) - a series/movie/podcast "
                      f"is not itself a streaming service")
                continue
        _clean_subject_rows.append((col_raw, str(val)))
    subject_rows = _clean_subject_rows

    spec = {
        'name': subject,
        'file_stem': stem,
        'brand_category': _bc_raw,
        'subject_raw_tu': subject_raw_tu,
        'subject_raw_avid': subject_raw_avid,
        # 2026-08-24 Jenna mandate: "the sample always has to be based
        # to the researched anchor". The verified derivation chain
        # rides the spec so the worker can re-verify and the ops email
        # can quote it in one line.
        'universe_anchor': (_anchor_meta or {}).get('anchor'),
        'anchor_source': (_anchor_meta or {}).get('anchor_source'),
        'engaged_share': (_anchor_meta or {}).get('engaged_share'),
        'anchor_missing': bool((_anchor_meta or {}).get('anchor_missing')),
        'subject_rows': subject_rows,
        'tu_demos': draft.get('tu_demos') or {},
        'avid_demos': draft.get('avid_demos') or draft.get('tu_demos') or {},
        'extra_rows': extra_rows,
        'persona_notes': _scrub(draft.get('persona_notes') or '',
                                field='persona_notes', subject=subject,
                                max_len=4000),
        # Constraint signature (2026-09-15): rides to the engine so its
        # sizing-guard mirror enforces refinement containment too.
        '_persona_signature': draft.get('_persona_signature'),
        'category_lifts': {},
        'brand_overrides': {},
        # 2026-08-19 (Jenna): public-metric ceiling metadata. Captures
        # any cohort whose size is publicly measurable - followers,
        # subscribers, viewers of a video / broadcast, listeners of a
        # podcast, event attendees, MAU/DAU of an app. The worker's
        # persona-research agent will refine follower_ceiling via web
        # search using the metric appropriate to audience_type, then
        # re-cap raw values if the refined number is more conservative.
        # The post-generation enforcer verifies the final projection
        # cannot exceed follower_ceiling. Field name is legacy; the
        # value stores whichever public metric caps this cohort.
        'audience_type': audience_type_out,
        'follower_ceiling': follower_ceiling_out,
        'follower_platforms': (draft.get('follower_platforms')
                                if audience_type_out != 'general' else None),
    }
    # Account-holder universes are adults (Grace Yin, Amazon, 2026-11-10):
    # the interpret step's `account_holder: true`, or the universe's own
    # words (sign-ups, subscribers, members, buyers, customers, ...),
    # zero AGE '17 AND UNDER' in tu_demos / avid_demos and stamp the flag
    # so the engine, the enforcers and the cut sizing see one verdict. A
    # viewers universe keeps the bucket and carries no flag.
    try:
        if draft.get('account_holder') is True:
            spec['account_holder'] = True
        from migration.adult_universe import apply_to_spec_demos as _ah_apply
        _ah_apply(spec)
        if spec.get('account_holder') is not True:
            spec.pop('account_holder', None)
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:   # anchor_entity / geo_scope / political_lean ride the spec (2026-10-12); lazy so extracted copies of this function run
        __import__('prometheus.cohort_scope', fromlist=['apply_to_spec']).apply_to_spec(spec, draft)
    except Exception as _fx:
        _faults.note('chat', _fx)
    # The deliverable notes an order carried (Grace Yin 2026-11-10,
    # prometheus.orders): what the file must carry, on the spec so the
    # confirm and the ready notice can say it. Requirements, never a
    # second subject and never a cut.
    _dn = draft.get('deliverable_notes') if isinstance(draft, dict) else None
    if isinstance(_dn, str) and _dn.strip():
        spec['deliverable_notes'] = _scrub(_dn, field='deliverable_notes', subject=subject, max_len=600, single_line=True)
    # ---- Platform scope passthrough (2026-09-04, Jenna perceptionbox
    # rerun). Normalized here so the engine host sees a validated list
    # of canonical platform keys or None. Draft may set the field or
    # leave it absent (older drafts). Reject garbage silently to None
    # so a bad value never blocks a build.
    _ps_raw = draft.get('platform_scope')
    _ps_norm = None
    _CANONICAL_PLATFORMS = {
        'youtube', 'tiktok', 'instagram', 'facebook', 'x', 'linkedin',
        'twitch', 'snapchat', 'threads', 'substack', 'patreon', 'kick',
        'rumble', 'pinterest', 'bluesky',
    }
    _PLATFORM_ALIASES = {
        'yt': 'youtube', 'ig': 'instagram', 'tt': 'tiktok',
        'fb': 'facebook', 'twitter': 'x', 'x/twitter': 'x',
    }
    if isinstance(_ps_raw, str):
        _ps_raw = [_ps_raw]
    if isinstance(_ps_raw, (list, tuple)) and _ps_raw:
        _clean = set()
        for _p in _ps_raw:
            _pk = str(_p or '').strip().lower()
            _pk = _PLATFORM_ALIASES.get(_pk, _pk)
            if _pk in _CANONICAL_PLATFORMS:
                _clean.add(_pk)
        if _clean:
            _ps_norm = sorted(_clean)
    if _ps_norm:
        spec['platform_scope'] = _ps_norm
    # ---- Date window passthrough (2026-08-24, Rosie O'Donnell / JKL
    # defect). Resolved event/explicit windows finally reach the
    # engine: `date_range` ('START TO END') stamps the SAMPLE SIZE row
    # on fresh builds; `cut_date_range` + `cut_window_label` ride to
    # the derive/cut paths so the CUT gets the event dates while a
    # cut_needs_parent PARENT still builds on the default window. An
    # unresolved `event_window_query` is resolved by the engine host
    # pre-build (migration/event_window.ensure_event_window_resolved).
    _win_str = _H.get('_ew_window_string') or (
        lambda a, b: f"{a} TO {b}" if a and b else '')
    _def_dates = _H.get('_standing_default_dates') or __import__('prometheus.window_scope', fromlist=['standing_default']).standing_default
    if draft.get('engine_date_range'):
        spec['date_range'] = _scrub(draft['engine_date_range'],
                                    field='date_range', subject=subject,
                                    max_len=60, single_line=True)
    elif isinstance(draft.get('date_range'), dict):
        _drs = str((draft.get('date_range') or {}).get('start') or '').strip()
        _dre = str((draft.get('date_range') or {}).get('end') or '').strip()
        _drw = _win_str(_drs, _dre)
        if _drw:
            spec['date_range'] = _scrub(_drw, field='date_range',
                                        subject=subject, max_len=60,
                                        single_line=True)
        else:
            _ds, _de = _def_dates()
            spec['date_range'] = f"{_ds} TO {_de}"
    elif _dr_from_subject:
        # A 'Date Range: X to Y' clause the user wrote into the subject
        # line itself (2026-09-10 Audible defect). The interpret step
        # never turned it into a window, so it would otherwise be lost
        # AND pollute the name. Honour it per default-date-range.mdc
        # ("if the user says any explicit date ... use their range").
        # Only applies when the draft carries no resolved window, so an
        # explicitly-interpreted range always wins.
        spec['date_range'] = _dr_from_subject
        print(f"[spec-guard] date_range recovered from subject clause: "
              f"{_dr_from_subject}")
    else:
        _dec = str(draft.get('decision') or '').strip().lower()
        if _dec in ('new_build', 'time_shifted_refresh',
                    'cut_needs_parent', ''):
            _ds, _de = _def_dates()
            spec['date_range'] = f"{_ds} TO {_de}"
    # Catalog hold (2026-10-06): a profile already on the dashboard
    # for this subject anchors the sample for its window. Same window:
    # that sample, exactly. Different window: a band scaled by window
    # length, a sub-window never above the annual. The same rule the
    # sample check applies, so the brief, the check and the build agree.
    try:
        _m = re.match(r'^\s*(\d{4}-\d{2}-\d{2})\s+TO\s+(\d{4}-\d{2}-\d{2})',
                      str(spec.get('date_range') or ''), re.I)
        _held = _C._pm_hold_sample_to_catalog(
            subject, int(spec.get('subject_raw_tu') or 0),
            _m.group(1) if _m else None, _m.group(2) if _m else None, saved=bool((_saved_trail or {}).get('applied')))
        if _held and int(_held) != int(spec.get('subject_raw_tu') or 0):
            print(f"[spec-guard] sample held to the dashboard profile: "
                  f"{spec.get('subject_raw_tu')} -> {_held}")
            spec['subject_raw_tu'] = int(_held)
            if int(spec.get('subject_raw_avid') or 0) >= int(_held):
                spec['subject_raw_avid'] = max(801, int(int(_held) * 0.22))
        __import__('migration.saved_subject_size', fromlist=['spec_markers']).spec_markers(spec, _saved_trail)   # the worker holds the settled size through its own messy pass
    except Exception:
        traceback.print_exc()
    if draft.get('cut_date_range'):
        spec['cut_date_range'] = _scrub(draft['cut_date_range'],
                                        field='cut_date_range',
                                        subject=subject, max_len=60,
                                        single_line=True)
    if draft.get('cut_window_label'):
        spec['cut_window_label'] = _scrub(draft['cut_window_label'],
                                          field='cut_window_label',
                                          subject=subject, max_len=160,
                                          single_line=True)
    if draft.get('date_window_label'):
        spec['date_window_label'] = _scrub(draft['date_window_label'],
                                           field='date_window_label',
                                           subject=subject, max_len=160,
                                           single_line=True)
    if draft.get('event_window_query'):
        spec['event_window_query'] = _scrub(draft['event_window_query'],
                                            field='event_window_query',
                                            subject=subject, max_len=300,
                                            single_line=True)
    # Same-name version qualifier (2026-08-25): the resolved version's
    # short qualifier ('US', 'UK', 'CBS', 'Movie 2017') rides the spec
    # so the engine's BRAND INPUT slugs are version-qualified
    # (the-office-us), never bare forms that also match the other
    # version.
    if draft.get('identity_qualifier'):
        spec['identity_qualifier'] = _scrub(draft['identity_qualifier'],
                                            field='identity_qualifier',
                                            subject=subject, max_len=60,
                                            single_line=True)
    # StreamScout routing (2026-09-21): audience-of-a-property pulls
    # seed BRAND INPUT from reference.content_mapping when seeds
    # exist. Seed-sourcing only, never pipeline control (Jenna, same
    # day): the build always runs normally either way - no holds, no
    # refunds, no needs-help email. The backstop below stamps
    # 'content_map_soft' on consumption-scoped IP subjects the
    # interview never asked about so their seeds upgrade when
    # available. The Keke Palmer class (host-map brand seed while
    # episode URLs sat in the content map) cannot recur.
    if str(draft.get('seed_source') or '').strip() == 'content_map':
        spec['seed_source'] = 'content_map'
        spec['content_show'] = _scrub(
            str(draft.get('content_show') or subject),
            field='content_show', subject=subject, max_len=200,
            single_line=True)
        _fts = draft.get('franchise_titles')
        if isinstance(_fts, list):
            spec['franchise_titles'] = [
                _scrub(str(t), field='franchise_titles',
                       subject=subject, max_len=200, single_line=True)
                for t in _fts if str(t).strip()][:40]
    else:
        try:
            if (draft.get('is_ip_content')
                    and (draft.get('consumer_verb')
                         or str(draft.get('ip_scope') or '')
                         .strip().lower() == 'consumers')):
                from migration.viewer_carriage import (
                    detect_consumption_scoped)
                _det = detect_consumption_scoped(subject)
                if _det and _det.get('title_hint'):
                    spec['seed_source'] = 'content_map_soft'
                    spec['content_show'] = _scrub(
                        str(draft.get('resolved_title')
                            or _det['title_hint']),
                        field='content_show', subject=subject,
                        max_len=200, single_line=True)
        except Exception as _ss_err:
            print(f"[spec] content-seed backstop skipped "
                  f"(non-fatal): {_ss_err}")
    # Jess + Spike 2026-10-16: the platforms the CLIENT named and the
    # YouTube choices ride the spec (strict on named platforms).
    from prometheus.streamscout_spec import stamp_client_scope
    stamp_client_scope(spec, draft, _scrub, subject)
    from prometheus.named_platforms import stamp_spec as _stamp_platform_spec   # the kind the reader meant ('the special, not the podcast') and the strict mark
    _stamp_platform_spec(spec, draft, _scrub, subject)
    # Viewer season/film scope (2026-08-27): the bound scope rides the
    # spec so the engine host resolves the scoped content URLs, folds
    # them into BRAND INPUT, and inserts the verified rows into the
    # content mapping reference. Structure lists (seasons/films) ride
    # along so the host never has to re-research what the chatbot
    # already resolved.
    _vsc = draft.get('viewer_scope')
    if isinstance(_vsc, dict) and _vsc.get('mode'):
        try:
            _vs_out = {
                'mode': str(_vsc.get('mode'))[:12],
                'label': _scrub(_vsc.get('label') or '',
                                field='viewer_scope_label',
                                subject=subject, max_len=120,
                                single_line=True),
                'title': _scrub(_vsc.get('title') or '',
                                field='viewer_scope_title',
                                subject=subject, max_len=120,
                                single_line=True),
                'kind': str(_vsc.get('kind') or '')[:20],
                'franchise': _scrub(_vsc.get('franchise') or '',
                                    field='viewer_scope_franchise',
                                    subject=subject, max_len=120,
                                    single_line=True),
                'production': _scrub(_vsc.get('production') or '',
                                     field='viewer_scope_production',
                                     subject=subject, max_len=80,
                                     single_line=True),
                'assumed': bool(_vsc.get('assumed')),
            }
            if _vsc.get('season') is not None:
                _vs_out['season'] = int(_vsc['season'])
                _vs_out['season_year'] = str(
                    _vsc.get('season_year') or '')[:9]
            if _vsc.get('film'):
                _vs_out['film'] = _scrub(_vsc['film'],
                                         field='viewer_scope_film',
                                         subject=subject, max_len=120,
                                         single_line=True)
                _vs_out['film_year'] = str(_vsc.get('film_year') or '')[:9]
            _vs_seasons = []
            for _s in (_vsc.get('seasons') or [])[:60]:
                try:
                    _vs_seasons.append({
                        'number': int(_s.get('number')),
                        'year': str(_s.get('year') or '')[:9]})
                except (TypeError, ValueError):
                    continue
            if _vs_seasons:
                _vs_out['seasons'] = _vs_seasons
            _vs_films = []
            for _f in (_vsc.get('films') or [])[:40]:
                _ft = str((_f or {}).get('title') or '').strip()
                if _ft:
                    _vs_films.append({
                        'title': _ft[:120],
                        'year': str(_f.get('year') or '')[:9]})
            if _vs_films:
                _vs_out['films'] = _vs_films
            spec['viewer_scope'] = _vs_out
        except Exception as _vs_spec_err:
            print(f"[spec_from_draft] viewer_scope passthrough "
                  f"skipped: {_vs_spec_err}")
    # Kids-title definition (2026-08-27 Paw Patrol directive): parents
    # vs under-18 rides the spec; the membership sentence already rides
    # persona_notes and the definition rides the subject name whole.
    if draft.get('viewer_audience') in ('parents', 'under18'):
        spec['viewer_audience'] = draft['viewer_audience']
    # ---- Semantic-guard spec contract (2026-08-25, bind-or-ask
    # mandate). Every field below was either bound by
    # _apply_semantic_guards or emitted directly by the interpret step;
    # all user-text-derived values pass through _scrub before riding to
    # the engine.
    _excl = draft.get('exclusions')
    if isinstance(_excl, list) and _excl:
        _clean_excl = []
        for e in _excl:
            if not isinstance(e, dict) or not e.get('brand'):
                continue
            _clean_excl.append({
                'brand': _scrub(e['brand'], field='exclusion_brand',
                                subject=subject, max_len=80,
                                single_line=True),
                'note': _scrub(e.get('note') or '',
                               field='exclusion_note', subject=subject,
                               max_len=200, single_line=True),
            })
        if _clean_excl:
            spec['exclusions'] = _clean_excl
    if str(draft.get('universe_mode') or '').strip().lower() in \
            ('churned', 'sequence'):
        spec['universe_mode'] = str(draft['universe_mode']).strip().lower()
        if draft.get('universe_note'):
            spec['universe_note'] = _scrub(draft['universe_note'],
                                           field='universe_note',
                                           subject=subject, max_len=400,
                                           single_line=True)
    _ctry = str(draft.get('country') or '').strip()
    if _ctry and _ctry.upper() not in ('US', 'USA', 'UNITED STATES'):
        spec['country'] = _scrub(_ctry, field='country', subject=subject,
                                 max_len=40, single_line=True); spec.update(__import__('prometheus.global_scope', fromlist=['spec_markets']).spec_markets(draft, _scrub, subject))   # a worldwide build carries the subject's own researched world metros (2026-11-23)
    if draft.get('intersection_mode') in ('combined', 'separate'):
        spec['intersection_mode'] = draft['intersection_mode']
    if draft.get('intensity_note'):
        spec['intensity_note'] = _scrub(draft['intensity_note'],
                                        field='intensity_note',
                                        subject=subject, max_len=240,
                                        single_line=True)
    if draft.get('scope_note'):
        spec['scope_note'] = _scrub(draft['scope_note'],
                                    field='scope_note', subject=subject,
                                    max_len=300, single_line=True)
    try:
        _usa = int(draft.get('user_supplied_anchor') or 0)
        if _usa > 0:
            spec['user_supplied_anchor'] = _usa
    except (TypeError, ValueError):
        pass
    # Guided-flow passthrough (2026-08-19, reworked same day per Jenna):
    # the base build is ALWAYS the national total universe + avid.
    # Regions are never a build-level filter - each named market rides
    # as a DMA add-on cut (3 credits) derived from the national
    # parent, exactly like gender / generation / age cuts. region_scope
    # is intentionally NOT read from the draft anymore; the worker
    # keeps its filter only for specs queued before this change.
    _cuts = draft.get('addon_cuts') or []
    if isinstance(_cuts, list) and _cuts:
        _clean_cuts = []
        for c in _cuts:
            if not isinstance(c, dict):
                continue
            def _scrub_cut(v, f):
                return _scrub(v, field=f, subject=subject, max_len=160,
                              single_line=True)
            # Compound cuts (2026-08-25 intersection guard): one cut
            # pinning several categories at once ('Male Millennials
            # Los Angeles Ca'). They carry compound.pins instead of a
            # single top-level pin_category/pin_buckets pair.
            _comp = c.get('compound')
            _comp_pins = []
            if isinstance(_comp, dict) and isinstance(
                    _comp.get('pins'), list):
                for p in _comp['pins']:
                    if isinstance(p, dict) and p.get('category') \
                            and p.get('buckets'):
                        _comp_pins.append({
                            'category': str(p['category']).upper(),
                            'buckets': [str(b) for b in p['buckets']],
                        })
            if not c.get('cut_id'):
                continue
            if not _comp_pins and (not c.get('pin_category')
                                   or not c.get('pin_buckets')):
                continue
            _cc = {
                'cut_id': _scrub_cut(c['cut_id'], 'cut_id'),
                'label': _scrub_cut(c.get('label') or c['cut_id'],
                                    'cut_label'),
                'name_label': _scrub_cut(c.get('name_label')
                                         or c.get('label') or c['cut_id'],
                                         'cut_name_label'),
                'kind': _scrub_cut(c.get('kind') or 'demo', 'cut_kind'),
            }
            if _comp_pins:
                _cc['compound'] = {
                    'label': _scrub_cut(_comp.get('label')
                                        or _cc['name_label'],
                                        'compound_label'),
                    'pins': _comp_pins,
                }
                # Primary pin doubles as the top-level pair so older
                # worker code paths still see a valid cut shape.
                _cc['pin_category'] = _comp_pins[0]['category']
                _cc['pin_buckets'] = list(_comp_pins[0]['buckets'])
            else:
                _cc['pin_category'] = str(c['pin_category']).upper()
                _cc['pin_buckets'] = [str(b) for b in c['pin_buckets']]
            # Region cuts (2026-08-25 region guard): the region label +
            # its DMA list ride through so naming and sizing stay tied
            # to the canonical DMA table.
            if c.get('region_label'):
                _cc['region_label'] = _scrub_cut(c['region_label'],
                                                 'region_label')
                _rd = c.get('region_dmas')
                if isinstance(_rd, list) and _rd:
                    _cc['region_dmas'] = [str(b) for b in _rd]
            # Explicit age range rides through so the engine can size
            # partially-covered AGE buckets proportionally
            # (Jenna 2026-08-24 deterministic cut sample fractions).
            _ar = c.get('age_range')
            if (isinstance(_ar, (list, tuple)) and len(_ar) == 2):
                try:
                    _lo, _hi = int(_ar[0]), int(_ar[1])
                    if 0 <= _lo < _hi <= 120:
                        _cc['age_range'] = [_lo, _hi]
                except (TypeError, ValueError):
                    pass
            _clean_cuts.append(_cc)
        if _clean_cuts:
            spec['addon_cuts'] = _clean_cuts
    # Quarter cuts (2026-10-01 Jenna; Bria's 'cuts by Quarter based
    # on dates' ask): each entry ships as its own dated deliverable
    # off the finished parent - the worker fans them out as window
    # re-reads, so the numbers stay anchored to the same universe.
    # The list is the wallet's (2026-11-18): what is sent is what an approval charges for, $100 a quarter read.
    _clean_q = __import__('wallet').clean_quarter_reads(draft)
    if _clean_q:
        spec['quarter_cuts'] = _clean_q
    # If Claude proposed clickstream_signals at interpret time (for a
    # platform/retailer behavioral cohort), thread them into a partial
    # persona_doc so the synth engine can use them for BRAND INPUT even
    # before the research agent runs. The research agent will overwrite
    # this with a fuller doc when it fires, preserving these signals
    # under `_seed_clickstream_signals` for reference.
    cs_seed = draft.get('clickstream_signals') or []
    if isinstance(cs_seed, list) and cs_seed:
        normalized = []
        for entry in cs_seed:
            if not isinstance(entry, dict):
                continue
            host = _scrub(str(entry.get('host', '') or '').strip().lower(),
                          field='cs_host', subject=subject, max_len=200,
                          single_line=True)
            path = _scrub(str(entry.get('path_pattern', '')
                              or entry.get('path', '') or '').strip(),
                          field='cs_path', subject=subject, max_len=300,
                          single_line=True)
            if not host or not path:
                continue
            normalized.append({
                'host': host,
                'path_pattern': path,
                'param_hint': _scrub(
                    str(entry.get('param_hint', '') or '').strip(),
                    field='cs_param_hint', subject=subject, max_len=200,
                    single_line=True),
                'evidence': _scrub(
                    str(entry.get('evidence', '') or '').strip(),
                    field='cs_evidence', subject=subject, max_len=300,
                    single_line=True),
            })
        if normalized:
            spec['persona_doc'] = {'clickstream_signals': normalized}

    # Caller-supplied competitor brands (2026-08-31): the set of brands
    # the caller wants represented inside this subject's profile. Rides
    # from the partner API /run body or a chatbot draft; folded onto the
    # spec here so the research phase (build_persona_brief) can reason
    # them into the relevant categories at real values. Cleaned + apos-
    # stripped + capped again here so a chatbot-sourced list is held to
    # the same bar as the partner API one. Fresh builds only - derived
    # cuts inherit their brand set from the parent and never carry this.
    cb_seed = draft.get('competitor_brands') or []
    if isinstance(cb_seed, list) and cb_seed:
        cb_clean = []
        cb_seen = set()
        for entry in cb_seed:
            name = _scrub(str(entry or ''), field='competitor_brand',
                          subject=subject, max_len=80, single_line=True)
            for _ap in ("'", '\u2019', '\u2018', '\u02bc', '`'):
                name = name.replace(_ap, '')
            name = ' '.join(name.split()).strip()
            if not name:
                continue
            key = name.lower()
            if key in cb_seen:
                continue
            cb_seen.add(key)
            cb_clean.append(name)
            if len(cb_clean) >= 50:
                break
        if cb_clean:
            spec['competitor_brands'] = cb_clean
    return __import__('migration.service_scope_sizing', fromlist=['normalize_spec']).normalize_spec(spec, subject)   # a title is never its own service; a one-service build is sized by rule, under the title's whole audience (2026-11-11)


__all__ = ['_spec_from_draft']
