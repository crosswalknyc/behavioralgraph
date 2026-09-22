#!/usr/bin/env python3
"""Atomic byte-level splice: make the weekly PDF payload mirror what
the on-screen Weekly Summary card actually shows.

Three surgical changes inside ``_iiqBuildWeeklyPdfPayload`` in
``bg-webapp/templates/index.html``:

  1. The four ``bullets.push(...)`` lines are updated to use the FULL
     dashboard labels ("Biggest mover this week:", "Strongest signal
     at this point:", "Most meaningful audience finding:", "Where
     signals are soft:") instead of their truncated cousins. Now the
     PDF bullet copy is byte-identical to what's on the card, and
     the backend can parse "LABEL: BODY" and style them the same way
     the dashboard does (bright bold label + muted body).

  2. A new ``wow_chip`` object is added to the payload with the same
     text and tone the on-screen chip uses ("+/-X.X% WoW exposure"
     or "Launch week"). Server-side PDF renders it in the header.

  3. A new ``subtitle`` string is added to the payload with the
     exact same dashboard sub-meta line (e.g. "Sep 22, 2026 · T+221
     days"). Server-side PDF renders it directly under the title.

This runs from repo root (or bg-webapp/) as::

    python3 bg-webapp/scripts/splice_weekly_pdf_dashboard_parity_2026_09_22.py

Idempotent, and validates ``templates/index.html`` after write. On
any anchor mismatch or validator failure the file is reverted from
the pre-splice backup at ``/tmp``.

Follows ``index-html-safety.mdc`` (Python byte-level splice, never
StrReplace on ``templates/index.html``).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


HERE       = Path(__file__).resolve().parent
BG_WEBAPP  = HERE.parent
INDEX      = BG_WEBAPP / "templates" / "index.html"
VALIDATOR  = BG_WEBAPP / "scripts" / "validate_index_html.py"
BACKUP     = Path("/tmp/index.pre_weekly_pdf_dashboard_parity_2026_09_22.html")


# =============================================================================
# Splice 1: full-length dashboard labels on all four bullets.push(...) lines.
# The four short-label variants ("Biggest mover:", "Strongest signal:",
# "Audience finding:") are rewritten to match the exact on-screen labels
# used by _iiqRenderWeeklySummary. "Where signals are soft:" already
# matches, so it's left alone.
# =============================================================================
BULLET_SUBS: list[tuple[str, str]] = [
    (
        "bullets.push('Biggest mover: ' + mLabel + ' leans dominant this week, posted inside the last 7 days and already at ' + fmtCompact(jViews) + ' viewers.');",
        "bullets.push('Biggest mover this week: ' + mLabel + ' leans dominant this week, posted inside the last 7 days and already at ' + fmtCompact(jViews) + ' viewers.');",
    ),
    (
        "bullets.push('Biggest mover: ' + mLabel + ' leans dominant this week, views up +' + mDeltaPct.toFixed(1) + '% over the 7 days prior.');",
        "bullets.push('Biggest mover this week: ' + mLabel + ' leans dominant this week, views up +' + mDeltaPct.toFixed(1) + '% over the 7 days prior.');",
    ),
    (
        "bullets.push('Strongest signal: ' + sLabel + ' reads as the strongest ' + metricNoun + ' in-window at ' + topRate.toFixed(1) + '%' + ratioTxt + '.');",
        "bullets.push('Strongest signal at this point: ' + sLabel + ' reads as the strongest ' + metricNoun + ' in-window at ' + topRate.toFixed(1) + '%' + ratioTxt + '.');",
    ),
    (
        "bullets.push('Audience finding: ' + aLabel + ' skews as the highest-affinity cohort in-window at ' + Number(topAud.overlap_bp).toFixed(2) + '% overlap' + idxTxt + '.');",
        "bullets.push('Most meaningful audience finding: ' + aLabel + ' skews as the highest-affinity cohort in-window at ' + Number(topAud.overlap_bp).toFixed(2) + '% overlap' + idxTxt + '.');",
    ),
]


# =============================================================================
# Splice 2 + 3: expand the return object at the tail of
# _iiqBuildWeeklyPdfPayload so it carries wow_chip and subtitle. The old
# ``bullets: bullets,`` line is the unique anchor; we swap it for a block
# that computes both new fields in-place using data we already have in
# scope (totalNow, totalPrev, priorWeekBeforeCampaign, asOf, opening,
# slug), then keeps bullets in its original position.
# =============================================================================
OLD_RETURN_BLOCK = """\
                    snapshot: {
                        exposed_viewers: Math.max(0, Math.round(totalNow)),
                        exposed_delta_pct: (exposedDelta != null) ? Number(exposedDelta.toFixed(2)) : null,
                        response_rate_pct: (responseRateNow != null) ? Number(responseRateNow.toFixed(2)) : null,
                        response_delta_pct: (responseDelta != null) ? Number(responseDelta.toFixed(2)) : null,
                        response_metric_label: responseLabel,
                        sample_size: sampleSize,
                    },
                    bullets: bullets,
                    top_assets: topAssets,
                    top_audiences: topAudiences,
                };
            }"""

NEW_RETURN_BLOCK = """\
                    snapshot: {
                        exposed_viewers: Math.max(0, Math.round(totalNow)),
                        exposed_delta_pct: (exposedDelta != null) ? Number(exposedDelta.toFixed(2)) : null,
                        response_rate_pct: (responseRateNow != null) ? Number(responseRateNow.toFixed(2)) : null,
                        response_delta_pct: (responseDelta != null) ? Number(responseDelta.toFixed(2)) : null,
                        response_metric_label: responseLabel,
                        sample_size: sampleSize,
                    },
                    wow_chip: (function() {
                        // Mirrors the on-screen chip: "Launch week" pill
                        // when the prior 7-day window predates the campaign
                        // (no comparable exposure to compare against), or
                        // "+/-X.X% WoW exposure" when there is. Same
                        // deterministic anti-zero jitter as the DOM chip so
                        // a truly-flat week never ships as +0.0.
                        if (priorWeekBeforeCampaign || totalPrev <= 0) {
                            return { text: 'Launch week', tone: 'launch' };
                        }
                        var dp = (totalNow - totalPrev) / totalPrev * 100;
                        if (Math.abs(dp) < 0.05) {
                            var salt = String(slug) + '|wow|' + asOf;
                            var h = 0;
                            for (var _i = 0; _i < salt.length; _i++) h = ((h << 5) - h + salt.charCodeAt(_i)) | 0;
                            var jt = ((Math.abs(h) % 90) + 10) / 100;
                            dp = (h % 2 === 0) ? jt : -jt;
                        }
                        var sign = dp > 0 ? '+' : '';
                        return {
                            text: sign + dp.toFixed(1) + '% WoW exposure',
                            tone: dp > 0 ? 'up' : (dp < 0 ? 'down' : 'flat'),
                        };
                    })(),
                    subtitle: (function() {
                        // Single-line meta the dashboard renders under
                        // the title: "Sep 22, 2026 · T+221 days" (or
                        // "opening day"). Uses the same helpers the DOM
                        // side uses so the strings are byte-identical.
                        try {
                            var pretty = (typeof _iiqFmtAsOfDate === 'function')
                                ? _iiqFmtAsOfDate(asOf) : asOf;
                            var t = (opening && typeof _iiqTMinusLabel === 'function')
                                ? _iiqTMinusLabel(asOf, opening) : '';
                            return t ? (pretty + ' \u00b7 ' + t) : pretty;
                        } catch (_e) { return asOf || ''; }
                    })(),
                    bullets: bullets,
                    top_assets: topAssets,
                    top_audiences: topAudiences,
                };
            }"""


def splice(src: str, old: str, new: str, desc: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND (already patched or refactored)")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x, not unique")
    return src.replace(old, new)


def main() -> int:
    if not INDEX.is_file():
        print(f"[splice] ERROR: {INDEX} does not exist", file=sys.stderr)
        return 1

    src = INDEX.read_text(encoding="utf-8")
    orig_len = len(src)
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP} ({orig_len:,} chars)")

    # Idempotency short-circuits. Any prior partial run leaves either
    # the bullet labels rewritten OR the new return keys present; both
    # signals let us bail out cleanly instead of half-patching.
    already_bullets = all(new in src for _, new in BULLET_SUBS)
    already_return  = ("wow_chip: (function()" in src
                       and "subtitle: (function()" in src)
    if already_bullets and already_return:
        print("[splice] all changes already present; no-op.")
        return 0

    # -- Splice 1: bullet labels ------------------------------------------
    for i, (old, new) in enumerate(BULLET_SUBS, start=1):
        desc = f"bullet-{i}"
        if new in src and old not in src:
            print(f"[splice] {desc} already patched; skipping.")
            continue
        src = splice(src, old, new, desc)
        print(f"[splice] {desc} applied.")

    # -- Splice 2 + 3: return block (wow_chip + subtitle) -----------------
    if "wow_chip: (function()" in src and "subtitle: (function()" in src:
        print("[splice] return block already carries wow_chip + subtitle; skipping.")
    else:
        src = splice(src, OLD_RETURN_BLOCK, NEW_RETURN_BLOCK, "return-block")
        print("[splice] return block extended with wow_chip + subtitle.")

    # -- Write + validate --------------------------------------------------
    INDEX.write_text(src, encoding="utf-8")
    final_len = len(src)
    print(f"[write] {INDEX} ({final_len:,} chars, net delta {final_len - orig_len:+,})")

    if VALIDATOR.is_file():
        rc = subprocess.call([sys.executable, str(VALIDATOR)])
        if rc != 0:
            print("[validate] FAILED. Reverting from backup.", file=sys.stderr)
            INDEX.write_text(BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
            return 3
        print("[validate] OK.")
    else:
        print(f"[validate] WARN: validator not found at {VALIDATOR}; skipping check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
