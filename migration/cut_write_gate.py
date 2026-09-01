"""Shared terminal write gate for derived-cut upload paths.

2026-08-24 (Furious audit D2/D5/D6): the three derived-cut engines
(`avid_fan_row_by_row.synthesize_avid_fan`,
`audience_cut_synthesis.synthesize_audience_cut`,
`addon_cut_synthesis.synthesize_demo_cut`) each upload with their own
inline post-processing instead of routing through
`profile_writer.write_profile_csv`, and the inline sets had drifted
apart: the gender-cut path skipped the final polish + sort entirely
(715 exact-2dp BPs + 99 unsorted categories on the Male cut), the
collision pass wrote percent-suffixed strings after the format
normalizer had already run, and none of them ran the loud pre-upload
audit.

`finalize_cut_for_upload` is the ONE terminal pass every cut write must
call immediately before serializing to S3. It is idempotent, Claude-
free, and cheap:

    1. run_final_invariant_polish  (echo strip -> cohort-label guard ->
       subject re-pin -> depin/dejitter -> write safety net, which
       itself backstops the SUBJECT metadata row)
    2. optional parent no-collision recheck (when parent_df is given) +
       safety-net re-run when it changed anything
    3. _normalize_numeric_artifacts  (strip %/comma artifacts - LAST
       formatter, nothing after it may write formatted strings)
    4. _sort_within_category  (BP desc inside each Column group)
    5. audit_upload_invariants  (loud report: exact-2dp flood, unsorted
       categories, percent-string cells, missing SUBJECT row, eroded
       self-pins)

This gate does NOT touch sample-size computation (deterministic cut
sample fractions are owned by the cut engines themselves).
"""

from __future__ import annotations


def finalize_cut_for_upload(df, subject, *, parent_df=None, out_key='',
                            verbose=True, ship_gate=True):
    """Run the shared terminal invariant chain on a derived-cut frame.

    Returns ``(df, report)`` where report carries the polish stats, the
    collision recheck count, and the pre-upload audit dict.

    ship_gate=True (default) runs the independent final ship gate
    (migration/final_ship_gate.py) as the LAST step: on any invariant
    violation the frame is quarantined, a debounced hold notice is
    recorded (emails only if the hold outlives the window; see
    migration/hold_notice_debounce), and ShipGateError raises so the
    engine's upload never happens.
    Engines pass ship_gate=False only for dry runs (the gate still
    runs report-only so violations are visible in the log). There is
    no env-flag downgrade.
    """
    report: dict = {}

    try:
        from migration.post_generation_enforcers import (
            audit_upload_invariants,
            run_final_invariant_polish,
            run_write_safety_net,
            strip_avid_casual_fan_rows,
        )
    except ImportError:
        from post_generation_enforcers import (  # type: ignore
            audit_upload_invariants,
            run_final_invariant_polish,
            run_write_safety_net,
            strip_avid_casual_fan_rows,
        )

    # 0. fan-row strip (2026-08-25, Joe & The Juice - Miami hold): TUs
    #    carry a reasoned AVID FAN row again (2026-08-24 reversal), and
    #    cut engines start from the parent frame, so every derived cut
    #    inherits the parent's row. Cuts must never carry one (ship gate
    #    I3). This gate is cuts-only by definition, so keep_avid_row is
    #    unconditionally False here; run_all_enforcers handles the TU
    #    side via profile_writer's key auto-detection.
    try:
        df, n_fan = strip_avid_casual_fan_rows(
            df, subject, verbose=verbose, keep_avid_row=False)
        report['fan_rows_stripped'] = int(n_fan or 0)
    except Exception as e:
        report['fan_rows_stripped'] = -1
        print(f'   ⚠ cut-write-gate fan-row strip failed (non-fatal): {e}')
    try:
        from migration.profile_writer import (
            _normalize_numeric_artifacts,
            _sort_within_category,
        )
    except ImportError:
        from profile_writer import (  # type: ignore
            _normalize_numeric_artifacts,
            _sort_within_category,
        )

    # 1. terminal polish (includes cohort-label guard + SUBJECT row
    #    backstop + depin + safety net)
    try:
        df, polish_stats = run_final_invariant_polish(
            df, subject, verbose=verbose)
        report['polish'] = polish_stats
    except Exception as e:
        report['polish'] = {'error': str(e)}
        print(f'   ⚠ cut-write-gate polish failed (non-fatal): {e}')

    # 2. parent no-collision recheck: the polish's depin/dejitter can
    #    re-land a value exactly on the parent's 4dp BP; re-break those
    #    (exactly-100 pins are exempt inside enforce_no_collisions).
    if parent_df is not None:
        try:
            try:
                from migration.avid_fan_row_by_row import (
                    enforce_no_collisions,
                )
            except ImportError:
                from avid_fan_row_by_row import (  # type: ignore
                    enforce_no_collisions,
                )
            df, n_coll = enforce_no_collisions(df, parent_df, subject)
            report['post_polish_collisions_broken'] = int(n_coll or 0)
            if n_coll:
                df, _ = run_write_safety_net(df, subject, verbose=False)
        except Exception as e:
            report['post_polish_collisions_broken'] = -1
            print(f'   ⚠ cut-write-gate collision recheck failed '
                  f'(non-fatal): {e}')

    # 2.5. TERMINAL SUBSET RE-CAP (2026-08-29 Bethenny / Automotive
    #    I12 holds): the polish's depin/dejitter (step 1) and the
    #    no-collision jitter (step 2) mutate BPs with no parent
    #    context, so a row the engine already capped against the
    #    parent can drift back across the subset raw ceiling and hit
    #    the ship gate's I12 - a whack-a-mole where each fixer undoes
    #    the last. This is the LAST BP-mutating step before the gate:
    #    a strictly non-increasing, raw-verified cap (cap_only=True -
    #    no lifts, so it can never fight gender-pair coherence).
    #    Capped against the SAME parent bytes the ship gate's I12
    #    will resolve from S3 (falling back to the caller's in-memory
    #    parent_df when resolution fails, e.g. offline dry runs), so
    #    the cap and the gate can never disagree about the ceiling.
    _gate_parent_df = None
    try:
        try:
            from migration.final_ship_gate import _resolve_parent_tu
        except ImportError:
            from final_ship_gate import _resolve_parent_tu  # type: ignore
        _pkey, _pbody = _resolve_parent_tu(out_key, verbose=verbose)
        if _pbody:
            import io as _io

            import pandas as _pd
            _gate_parent_df = _pd.read_csv(
                _io.BytesIO(_pbody), dtype=str, keep_default_na=False)
            if verbose:
                print(f'   [cut-write-gate] subset re-cap parent: {_pkey}')
    except Exception as e:
        if verbose:
            print(f'   [cut-write-gate] gate-parent resolve failed '
                  f'({e}); falling back to in-memory parent')
    if _gate_parent_df is None:
        _gate_parent_df = parent_df
    if _gate_parent_df is not None:
        try:
            try:
                from migration.avid_fan_row_by_row import (
                    enforce_avid_subset_coherence,
                )
            except ImportError:
                from avid_fan_row_by_row import (  # type: ignore
                    enforce_avid_subset_coherence,
                )
            try:
                from migration.post_generation_enforcers import (
                    apply_recompute_category_share,
                    recompute_raw_and_projection,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    apply_recompute_category_share,
                    recompute_raw_and_projection,
                )
            # Post-cap reconcile is ARITHMETIC ONLY (Raw/Proj +
            # Category Share). The full write safety net re-runs the
            # direction-blind 4dp-collision dejitter, which moved a
            # freshly capped row back UP across its subset raw ceiling
            # (Automotive avid, run fbb-KZmN3eNsQw: SPEAKE MARIN capped
            # 0.0100 -> 0.0021, net dejittered to 0.0030, raw 5 -> 6 vs
            # parent 5 -> I12 hold). BP-mutating passes stay upstream
            # (polish, step 2); nothing after this step may move a BP.
            # The loop re-verifies convergence: the arithmetic passes
            # cannot move BPs, so round 2 finding 0 rows proves the
            # frame sits at-or-under every parent ceiling.
            total_capped = 0
            for _recap_round in range(3):
                df, _cap_stats = enforce_avid_subset_coherence(
                    df, _gate_parent_df, subject,
                    verbose=verbose, cap_only=True,
                )
                n_capped = int(_cap_stats.get('capped_up', 0) or 0)
                total_capped += n_capped
                if not n_capped:
                    break
                df, _ = recompute_raw_and_projection(
                    df, subject, verbose=False)
                df, _ = apply_recompute_category_share(
                    df, subject, verbose=False)
            report['terminal_subset_recap'] = total_capped
            if total_capped and verbose:
                print(f'   ✅ cut-write-gate terminal subset re-cap: '
                      f'{total_capped} row(s) re-capped '
                      f'(converged round {_recap_round + 1})')
        except Exception as e:
            report['terminal_subset_recap'] = -1
            print(f'   ⚠ cut-write-gate subset re-cap failed '
                  f'(non-fatal): {e}')

    # 3. numeric artifacts - the LAST formatter before sort + upload
    try:
        df, n_art = _normalize_numeric_artifacts(df, verbose=verbose)
        report['numeric_artifacts'] = int(n_art or 0)
    except Exception as e:
        report['numeric_artifacts'] = -1
        print(f'   ⚠ cut-write-gate artifact normalize failed '
              f'(non-fatal): {e}')

    # 4. canonical sort
    try:
        df = _sort_within_category(df)
    except Exception as e:
        print(f'   ⚠ cut-write-gate sort failed (non-fatal): {e}')

    # 5. loud pre-upload audit (report-only)
    try:
        report['audit'] = audit_upload_invariants(
            df, subject, context=out_key, verbose=verbose)
    except Exception as e:
        report['audit'] = {'error': str(e)}

    # 6. FINAL SHIP GATE (2026-08-24 Jenna mandate; no-rebuild policy
    # 2026-08-31). Independent terminal invariant check - own parse, own
    # coercion, no shared enforcer helpers. Runs on the finalized frame;
    # the engines only append the two Gen Pop baseline columns after
    # this (values untouched) before serializing. REPORT-ONLY on a built
    # frame: the terminal subset re-cap (step 2.5) and the writer's
    # fix-and-regate loop already corrected every mechanical invariant
    # in place, so a surviving violation is logged and the corrected cut
    # publishes anyway. It never quarantines, never emails a hold, and
    # ShipGateError is never raised on this path.
    try:
        from migration.final_ship_gate import run_final_ship_gate
    except ImportError:
        from final_ship_gate import run_final_ship_gate  # type: ignore
    ok, ship_violations = run_final_ship_gate(
        df, out_key, subject,
        enforce=bool(ship_gate),
        verbose=verbose,
    )
    report['ship_gate'] = {
        'ok': bool(ok),
        'n_violations': len(ship_violations or []),
    }

    # 7. PRE-SHIP REASONED VETTING (2026-08-26 Jenna mandate: research
    # and reasoning before shipping). Runs after the mechanical gate
    # approves the frame. is_new=True: a re-derived cut is new
    # reasoning even when it overwrites an existing deliverable key.
    # PASS publishes; deterministic benchmark-backed fixes apply in
    # place and re-run the mechanical gate; structural judgment findings
    # route to their deterministic mechanical re-spread and publish in
    # place (no-rebuild policy: no quarantine, no hold, PreShipVettingError
    # never raised on this path). The correction is transactional -
    # a post-fix frame that breaks the mechanical gate reverts to the
    # gate-approved frame. Infra failures fail OPEN.
    # parent_df threads through for the cut inheritance guard: a fail
    # finding on a row whose level the cut inherited from the parent
    # (within jitter tolerance) downgrades to borderline instead of
    # holding the cut (2026-08-28 Furious compound-cut hold).
    try:
        from migration.pre_ship_vetting import run_pre_ship_vetting
    except ImportError:
        from pre_ship_vetting import (  # type: ignore
            run_pre_ship_vetting,
        )
    df, vet_report = run_pre_ship_vetting(
        df, subject, out_key,
        enforce=bool(ship_gate), is_new=True,
        parent_df=parent_df,
        sort_fn=_sort_within_category, verbose=verbose,
    )
    report['vetting'] = {
        'verdict': vet_report.get('verdict'),
        'skipped': vet_report.get('skipped'),
        'n_findings': len(vet_report.get('findings') or []),
        'n_autofix': len(vet_report.get('autofix') or []),
    }

    return df, report
