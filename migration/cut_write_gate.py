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

    # 6. FINAL SHIP GATE (2026-08-24 Jenna mandate). Independent
    # terminal invariant check - own parse, own coercion, no shared
    # enforcer helpers. Runs on the finalized frame; the engines only
    # append the two Gen Pop baseline columns after this (values
    # untouched) before serializing. On violations with
    # ship_gate=True: quarantine + debounced hold notice + ShipGateError.
    # Deliberately NOT wrapped in a swallowing try/except; engine call
    # sites re-raise ShipGateError from their own wrappers.
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
    # place and re-run the mechanical gate; judgment holds raise
    # PreShipVettingError (a ShipGateError subclass, so engine call
    # sites' existing re-raise handling applies). Infra failures fail
    # OPEN. Deliberately NOT wrapped in a swallowing try/except.
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
