#!/usr/bin/env python3
"""Fold the Implications bullet into the Weekly Summary PDF (David ask C1).

Three edits:

  1. templates/index.html : _iiqBuildWeeklyPdfPayload appends an
     Implications bullet after the four existing bullets, using the
     same phase-aware / directional language as the on-screen version.
  2. attribution_weekly_pdf.py (dark variant): bullet cap 4 -> 5, and
     the b_row_h wrap allowance shaved from 0.46in to 0.44in so five
     bullets still fit in the same visual card without pushing the
     asset + audience tables off the one-page frame.
  3. attribution_weekly_pdf.py (dark variant): "Implications for next
     week" label rendered on Signal Green (not Signal Olive dot) so
     the strategic-recommendation bullet reads as the accent moment,
     the same treatment the on-screen surface uses.

The light variant already accepts 5 bullets (rows = d['bullets'][:5])
so no change there.

One-page fit safety net: if the reduced b_row_h ever overflows on a
long two-line Implications bullet body, ship a follow-up that either
reduces to 4 (drop Soft in favor of Implications in the frontend
payload) or squeezes the leading further.
"""
from pathlib import Path
import subprocess, sys

HERE = Path(__file__).resolve().parents[1]  # bg-webapp/
INDEX = HERE / "templates" / "index.html"
PDF   = HERE / "attribution_weekly_pdf.py"
BACKUP_HTML = Path("/tmp/index.pre_pdf_implications.html")
BACKUP_PY   = Path("/tmp/attribution_weekly_pdf.py.pre_pdf_implications.py")

def splice(src, old, new, desc):
    n = src.count(old)
    if n != 1:
        print(f"  [FAIL] {desc}: anchor found {n} times, expected 1")
        print(f"         anchor first 100 chars: {old[:100]!r}")
        sys.exit(2)
    return src.replace(old, new, 1)

# ---------------------------------------------------------------------------
# SPLICE A - _iiqBuildWeeklyPdfPayload: append Implications bullet as #5.
# Anchor: the block right after the Soft bullet, before the theme comment.
# ---------------------------------------------------------------------------

HTML_OLD = (
    "                // 4. Soft signal\n"
    "                if (rated.length >= 3) {\n"
    "                    var srtSoft = rated.slice().sort(function(a, b) {\n"
    "                        var av = useInfo ? a.info_pct : a.ticket_pct;\n"
    "                        var bv = useInfo ? b.info_pct : b.ticket_pct;\n"
    "                        return av - bv;\n"
    "                    });\n"
    "                    var bottom = srtSoft[0];\n"
    "                    if (bottom) {\n"
    "                        var bLabel = bottom.asset.action_label || bottom.asset.asset_type || 'Untitled asset';\n"
    "                        var bRate = useInfo ? bottom.info_pct : bottom.ticket_pct;\n"
    "                        var metricNounB = useInfo ? 'info-seek' : 'ticketing';\n"
    "                        bullets.push('Where signals are soft: ' + bLabel + ' tends to under-index on ' + metricNounB + ' this week at ' + bRate.toFixed(1) + '%. One angle: platform-audience-content mismatch, worth watching.');\n"
    "                    }\n"
    "                }\n"
    "\n"
    "                // Theme mirrors the on-screen dashboard so the downloaded"
)

HTML_NEW = (
    "                // 4. Soft signal\n"
    "                if (rated.length >= 3) {\n"
    "                    var srtSoft = rated.slice().sort(function(a, b) {\n"
    "                        var av = useInfo ? a.info_pct : a.ticket_pct;\n"
    "                        var bv = useInfo ? b.info_pct : b.ticket_pct;\n"
    "                        return av - bv;\n"
    "                    });\n"
    "                    var bottom = srtSoft[0];\n"
    "                    if (bottom) {\n"
    "                        var bLabel = bottom.asset.action_label || bottom.asset.asset_type || 'Untitled asset';\n"
    "                        var bRate = useInfo ? bottom.info_pct : bottom.ticket_pct;\n"
    "                        var metricNounB = useInfo ? 'info-seek' : 'ticketing';\n"
    "                        bullets.push('Where signals are soft: ' + bLabel + ' tends to under-index on ' + metricNounB + ' this week at ' + bRate.toFixed(1) + '%. One angle: platform-audience-content mismatch, worth watching.');\n"
    "                    }\n"
    "                }\n"
    "                // 5. Implications for next week (David ask C1). Names\n"
    "                // the top mover asset + top under-served cohort where\n"
    "                // both are available, phase-appropriate action. Tier 2\n"
    "                // directional language; never a bare percent.\n"
    "                try {\n"
    "                    var _pdfTopUnderserved = null;\n"
    "                    var _pdfAudRows = _iiqComputeAudienceRows(asOf, opening) || [];\n"
    "                    for (var _pi = 0; _pi < _pdfAudRows.length; _pi++) {\n"
    "                        if (_pdfAudRows[_pi] && _pdfAudRows[_pi].fit === 'underserved') { _pdfTopUnderserved = _pdfAudRows[_pi]; break; }\n"
    "                    }\n"
    "                    var _pdfMoverAsset = null;\n"
    "                    var _perByDelta = perAsset.slice().sort(function(a, b) { return b.delta - a.delta; });\n"
    "                    if (_perByDelta[0] && _perByDelta[0].delta > 0) _pdfMoverAsset = _perByDelta[0].asset;\n"
    "                    var _pdfMoverLabel = _pdfMoverAsset ? (_pdfMoverAsset.action_label || _pdfMoverAsset.asset_type || '') : '';\n"
    "                    var _pdfAudLabel = _pdfTopUnderserved ? String(_pdfTopUnderserved.display || _pdfTopUnderserved.subject_key || '').replace(/^Fans of\\s+/i, '').replace(/\\s*\\(Cast\\)\\s*$/i, '').trim() : '';\n"
    "                    var _pdfImpl = '';\n"
    "                    if (useInfo) {\n"
    "                        if (_pdfMoverLabel && _pdfAudLabel) _pdfImpl = 'Implications for next week: One angle: ' + _pdfMoverLabel + ' is carrying the exposure story and info-seek is climbing on it. Pair the next drop with the ' + _pdfAudLabel + ' cohort to lift on the axis with the most headroom.';\n"
    "                        else if (_pdfMoverLabel)             _pdfImpl = 'Implications for next week: One angle: keep ' + _pdfMoverLabel + ' in rotation while the info-seek signal is still building. Reassess after the next drop.';\n"
    "                        else                                 _pdfImpl = 'Implications for next week: One angle: the top of funnel is still building. Prioritize assets that lift info-seek before shifting attention to checkout-page reach.';\n"
    "                    } else {\n"
    "                        if (_pdfMoverLabel && _pdfAudLabel) _pdfImpl = 'Implications for next week: One angle: ' + _pdfMoverLabel + ' reads as the checkout-page driver right now, and the ' + _pdfAudLabel + ' cohort responds hard when reached. Prioritize retargeting there in the final week.';\n"
    "                        else if (_pdfMoverLabel)             _pdfImpl = 'Implications for next week: One angle: ' + _pdfMoverLabel + ' reads as the checkout-page driver right now. Concentrate spend behind it through the final week.';\n"
    "                        else                                 _pdfImpl = 'Implications for next week: One angle: pull the read forward with the picker to see the assets carrying the checkout-page signal, then concentrate spend there in the final week.';\n"
    "                    }\n"
    "                    if (_pdfImpl) bullets.push(_pdfImpl);\n"
    "                } catch (_pdfImplErr) { /* keep 4 bullets on failure */ }\n"
    "\n"
    "                // Theme mirrors the on-screen dashboard so the downloaded"
)

# ---------------------------------------------------------------------------
# SPLICE B - dark PDF bullet cap 4 -> 5, tighten row height.
# ---------------------------------------------------------------------------

PDF_OLD = (
    "    if d[\"bullets\"]:\n"
    "        rows = d[\"bullets\"][:4]\n"
    "        b_title_h = 0.30 * inch\n"
    "        b_row_h   = 0.46 * inch  # room for a two-line wrap on the body\n"
    "        b_card_h  = b_title_h + b_row_h * len(rows) + 0.10 * inch"
)

PDF_NEW = (
    "    if d[\"bullets\"]:\n"
    "        # David ask C1 (2026-09-23): allow the Implications bullet\n"
    "        # to ride as the 5th line. b_row_h shaved slightly so 5\n"
    "        # bullets still fit the same visual card without pushing\n"
    "        # the asset + audience tables off the one-page frame.\n"
    "        rows = d[\"bullets\"][:5]\n"
    "        b_title_h = 0.30 * inch\n"
    "        b_row_h   = 0.44 * inch  # room for a two-line wrap on the body\n"
    "        b_card_h  = b_title_h + b_row_h * len(rows) + 0.10 * inch"
)

# ---------------------------------------------------------------------------
# SPLICE C - Give the Implications label the Signal Green treatment
# so the strategic-recommendation bullet reads as the accent moment.
# ---------------------------------------------------------------------------

PDF_OLD2 = (
    "        for b in rows:\n"
    "            label, body = _split_label_body(b)\n"
    "            is_soft = label.lower().startswith(\"where signals are soft\")\n"
    "            # Olive dot before each bullet, per dashboard convention\n"
    "            # (Signal Olive is the safe stand-in for Signal Green on\n"
    "            # anything smaller than ~4pt on a dark ground).\n"
    "            c.setFillColor(SIGNAL_OLIVE if not is_soft else DUSK)\n"
    "            c.circle(MARGIN + 0.28 * inch, y + 0.04 * inch, 0.040 * inch,\n"
    "                     stroke=0, fill=1)\n"
    "            lbl_color = DUSK if is_soft else DARK_TEXT_PRIMARY"
)

PDF_NEW2 = (
    "        for b in rows:\n"
    "            label, body = _split_label_body(b)\n"
    "            is_soft = label.lower().startswith(\"where signals are soft\")\n"
    "            is_impl = label.lower().startswith(\"implications for next week\")\n"
    "            # Dot color: Signal Green for the strategic-recommendation\n"
    "            # Implications bullet (dashboard accent), Signal Olive for\n"
    "            # standard bullets, Dusk for the soft-signal bullet.\n"
    "            if is_impl:\n"
    "                _dot_color = SIGNAL_GREEN\n"
    "            elif is_soft:\n"
    "                _dot_color = DUSK\n"
    "            else:\n"
    "                _dot_color = SIGNAL_OLIVE\n"
    "            c.setFillColor(_dot_color)\n"
    "            c.circle(MARGIN + 0.28 * inch, y + 0.04 * inch, 0.040 * inch,\n"
    "                     stroke=0, fill=1)\n"
    "            lbl_color = DUSK if is_soft else DARK_TEXT_PRIMARY"
)

def main():
    # ---- INDEX HTML ----
    src = INDEX.read_text(encoding="utf-8")
    BACKUP_HTML.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP_HTML}")
    src = splice(src, HTML_OLD, HTML_NEW, "SPLICE A: Implications bullet in PDF payload")
    INDEX.write_text(src, encoding="utf-8")
    print("  [ok] SPLICE A")

    # Validate index.html immediately per index-html-safety.mdc
    val = subprocess.run(["python3", "scripts/validate_index_html.py"],
                          capture_output=True, text=True, cwd=str(HERE))
    print()
    print(val.stdout)
    if val.returncode != 0:
        print("[FAIL] validator rejected. Reverting.")
        INDEX.write_text(BACKUP_HTML.read_text(encoding="utf-8"), encoding="utf-8")
        sys.exit(3)
    print("[ok] validate_index_html.py passed")

    # ---- PDF PY ----
    src = PDF.read_text(encoding="utf-8")
    BACKUP_PY.write_text(src, encoding="utf-8")
    print(f"\n[backup] {BACKUP_PY}")
    src = splice(src, PDF_OLD, PDF_NEW, "SPLICE B: dark PDF cap 4 -> 5")
    print("  [ok] SPLICE B")
    src = splice(src, PDF_OLD2, PDF_NEW2, "SPLICE C: Implications label Signal Green")
    print("  [ok] SPLICE C")
    PDF.write_text(src, encoding="utf-8")

    # Sanity: PDF module still imports
    r = subprocess.run([sys.executable, "-c", "import attribution_weekly_pdf; print('import OK')"],
                        capture_output=True, text=True, cwd=str(HERE))
    print()
    print(r.stdout)
    if r.stderr: print(r.stderr, file=sys.stderr)
    if r.returncode != 0:
        print("[FAIL] attribution_weekly_pdf.py did not import. Reverting.")
        PDF.write_text(BACKUP_PY.read_text(encoding="utf-8"), encoding="utf-8")
        sys.exit(4)
    print("[ok] attribution_weekly_pdf.py imports")

if __name__ == "__main__":
    main()
