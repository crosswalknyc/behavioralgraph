#!/usr/bin/env python3
"""Attribution IQ film branch: purge every user-visible 'ticketing' /
'paid' / 'cart' string (Jenna 2026-09-23: never say bought a ticket, we
do not model ticketing; say traffic that reached the checkout page),
clamp the audience 'reached' count so it never exceeds the cohort
'people' count, and make the Weekly Summary implications copy aware of
whether the as-of cursor sits after opening (a T+222 read must not say
'concentrate spend through the final week').

Byte-level splice per index-html-safety.mdc. Each anchor carries the
number of occurrences it must match exactly.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_film_checkout_language_2026_09_23.html")


def splice(src, old, new, desc, count=1):
    n = src.count(old)
    if n != count:
        raise RuntimeError(f"[{desc}] expected {count} anchor(s), found {n}")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
before = len(src)

# ---- fallback label -------------------------------------------------------
src = splice(src, "'Ticketing'", "'Checkout page'", "fallback bottom-funnel label", count=16)

# ---- column headers / metric labels --------------------------------------
src = splice(src, "'Info-seek %' : 'Ticketing %'", "'Info-seek %' : 'Checkout page %'", "header %", count=4)
src = splice(src, "(useInfo ? 'info-seek %' : 'ticketing %')", "(useInfo ? 'info-seek %' : 'checkout page %')", "sort label %", count=2)
src = splice(src, "'info-seek driver' : 'ticketing driver'", "'info-seek driver' : 'checkout-page driver'", "summary driver noun", count=2)
src = splice(src, "var metricNounB = useInfo ? 'info-seek' : 'ticketing';",
             "var metricNounB = useInfo ? 'info-seek' : 'checkout-page reach';", "soft-signal noun", count=2)
src = splice(src, "var primaryMetricLabel = useInfo ? 'info-seek' : 'ticketing';",
             "var primaryMetricLabel = useInfo ? 'info-seek' : 'checkout page';", "trend metric label")
src = splice(src, "var indexTip = 'How much stronger this cohort\\'s ' + (useInfo ? 'info-seek' : 'ticketing') + ' rate",
             "var indexTip = 'How much stronger this cohort\\'s ' + (useInfo ? 'info-seek' : 'checkout page') + ' rate", "index tooltip")
src = splice(src, "'Info-seek rate' : 'Ticketing rate'", "'Info-seek rate' : 'Checkout page rate'", "response rate label")
src = splice(src, "info-seek before, ticketing after.", "info-seek before, checkout page after.", "T-14 switch copy", count=4)
src = splice(src, "(Info-seek before T-14, Ticketing after)", "(Info-seek before T-14, Checkout page after)", "help panel response")
src = splice(src, "(info-seek before T-14, ticketing after)", "(info-seek before T-14, checkout page after)", "help panel strongest signal")

# ---- helper tooltips -----------------------------------------------------
src = splice(src, ": 'share who then visited a ticketing site (';", ": 'share who then reached a checkout page on (';", "conv lead")
src = splice(src, "return '% of viewers who visited a ticketing site within 7 days ('",
             "return '% of viewers who reached a checkout page within 7 days ('", "asset tooltip")
src = splice(src, "return '% of REACHED cohort members who visited a ticketing site ('",
             "return '% of REACHED cohort members who reached a checkout page ('", "cohort tooltip")
src = splice(src, "return 'Ticketing visits (daily)';", "return 'Checkout page visits (daily)';", "daily series label")

# ---- asset drill-in modal ------------------------------------------------
src = splice(src,
             "var tkTileTip = 'Ticketing % is the share of viewers within a 7-day post-view window who visited a ticketing site (Fandango / AMC / Regal / Cinemark / Atom).",
             "var tkTileTip = 'Checkout page % is the share of viewers within a 7-day post-view window who reached a checkout page on a showtimes site (Fandango / AMC / Regal / Cinemark / Atom).",
             "modal tk tile tip")
src = splice(src, "var tkLabelHtml   = 'Ticketing %'", "var tkLabelHtml   = 'Checkout page %'", "modal tk label")
src = splice(src, "'Daily info-seekers' : 'Daily ticketing visits'", "'Daily info-seekers' : 'Daily checkout page visits'", "modal series")
src = splice(src, "(useInfo ? 'info-seekers' : 'ticketing visits') + ' on the right axis",
             "(useInfo ? 'info-seekers' : 'checkout page visits') + ' on the right axis", "modal axis caption")

# ---- audience drill-in modal --------------------------------------------
# (aud modal responseTip 'Ticketing' literal is covered by the 16-count fallback replace above)
src = splice(src, ": 'ticketing-site visit') + ' within a 7-day window.'", ": 'checkout page visit') + ' within a 7-day window.'", "aud modal responded tip")

# ---- reached never exceeds people ---------------------------------------
src = splice(src,
             "var reachedJit = _iiqCountJitter(slug, 'aud_reach_' + rowKey, r.reached);",
             "var reachedJit = _iiqCountJitter(slug, 'aud_reach_' + rowKey, r.reached);\n"
             "                        if (reachedJit > peopleJit) reachedJit = peopleJit;",
             "aud table reached clamp")
src = splice(src,
             "var reachedJit = _pitJit('modal_reach', row.reached);",
             "var reachedJit = _pitJit('modal_reach', row.reached);\n"
             "                if (reachedJit > peopleJit) reachedJit = peopleJit;",
             "aud modal reached clamp")

# ---- Paths card (film branch) --------------------------------------------
src = splice(src, "? 'Ticketing surface (partition, sums to 100%)'", "? 'Showtimes surface (partition, sums to 100%)'", "where label")
src = splice(src, "var attrPaidNoun = isFilm ? 'checkout page visit' : escapeHtml(overallConvNoun);",
             "var attrPaidNoun = isFilm ? 'the checkout page' : ('a ' + escapeHtml(overallConvNoun));", "attr noun")
src = splice(src, " US accounts that reached a ' + attrPaidNoun + '.</div>'", " US accounts that reached ' + attrPaidNoun + '.</div>'", "attr intro")
src = splice(src, "+ '% of paid</div>'", "+ '% of checkout</div>'", "archetype pct")
src = splice(src, "Every paid account falls into one of these four shapes.",
             "Every account that reached the checkout page falls into one of these four shapes.", "archetype intro")

# ---- Weekly Summary implications: post-opening aware --------------------
OLD_SUMMARY = """                    if (_implMover && _implAud) {
                        bulletImplications = '<strong style="color:#E9E8E1;">Implications for next week:</strong> <span style="color:#9AA09B;">One angle: ' + escapeHtml(_implMover) + ' reads as the checkout-page driver right now, and the ' + escapeHtml(_implAud) + ' cohort responds hard when reached. Prioritize retargeting there in the final week.</span>';
                    } else if (_implMover) {
                        bulletImplications = '<strong style="color:#E9E8E1;">Implications for next week:</strong> <span style="color:#9AA09B;">One angle: ' + escapeHtml(_implMover) + ' reads as the checkout-page driver right now. Concentrate spend behind it through the final week.</span>';
                    } else {
                        bulletImplications = '<strong style="color:#E9E8E1;">Implications for next week:</strong> <span style="color:#9AA09B;">One angle: pull the read forward with the picker to see the assets carrying the checkout-page signal, then concentrate spend there in the final week.</span>';
                    }"""
NEW_SUMMARY = """                    var _implPostOpen = !!(opening && asOf && String(asOf) > String(opening));
                    var _implHead = _implPostOpen ? 'What this run says:' : 'Implications for next week:';
                    if (_implMover && _implAud) {
                        bulletImplications = '<strong style="color:#E9E8E1;">' + _implHead + '</strong> <span style="color:#9AA09B;">One angle: ' + escapeHtml(_implMover) + (_implPostOpen
                            ? ' read as the checkout-page driver of the campaign, and the ' + escapeHtml(_implAud) + ' cohort responded hardest when reached. Lead the next title\\'s plan with that pairing.</span>'
                            : ' reads as the checkout-page driver right now, and the ' + escapeHtml(_implAud) + ' cohort responds hard when reached. Prioritize retargeting there in the final week.</span>');
                    } else if (_implMover) {
                        bulletImplications = '<strong style="color:#E9E8E1;">' + _implHead + '</strong> <span style="color:#9AA09B;">One angle: ' + escapeHtml(_implMover) + (_implPostOpen
                            ? ' read as the checkout-page driver of the campaign. Anchor the next launch plan around it.</span>'
                            : ' reads as the checkout-page driver right now. Concentrate spend behind it through the final week.</span>');
                    } else {
                        bulletImplications = '<strong style="color:#E9E8E1;">' + _implHead + '</strong> <span style="color:#9AA09B;">One angle: ' + (_implPostOpen
                            ? 'the campaign has run its course. Pull the picker back to opening week to see the assets that carried the checkout-page signal.</span>'
                            : 'pull the read forward with the picker to see the assets carrying the checkout-page signal, then concentrate spend there in the final week.</span>');
                    }"""
src = splice(src, OLD_SUMMARY, NEW_SUMMARY, "summary implications")

OLD_PDF = """                        if (_pdfMoverLabel && _pdfAudLabel) _pdfImpl = 'Implications for next week: One angle: ' + _pdfMoverLabel + ' reads as the checkout-page driver right now, and the ' + _pdfAudLabel + ' cohort responds hard when reached. Prioritize retargeting there in the final week.';
                        else if (_pdfMoverLabel)             _pdfImpl = 'Implications for next week: One angle: ' + _pdfMoverLabel + ' reads as the checkout-page driver right now. Concentrate spend behind it through the final week.';
                        else                                 _pdfImpl = 'Implications for next week: One angle: pull the read forward with the picker to see the assets carrying the checkout-page signal, then concentrate spend there in the final week.';"""
NEW_PDF = """                        var _pdfPostOpen = (function() { try { var _o = (window.__intentIQ && window.__intentIQ.overview) || {}; var _a = _iiqAsOfGet(); return !!(_o.opening_date && _a && String(_a) > String(_o.opening_date)); } catch (e) { return false; } })();
                        var _pdfHead = _pdfPostOpen ? 'What this run says: ' : 'Implications for next week: ';
                        if (_pdfMoverLabel && _pdfAudLabel) _pdfImpl = _pdfHead + 'One angle: ' + _pdfMoverLabel + (_pdfPostOpen ? ' read as the checkout-page driver of the campaign, and the ' + _pdfAudLabel + ' cohort responded hardest when reached. Lead the next title\\'s plan with that pairing.' : ' reads as the checkout-page driver right now, and the ' + _pdfAudLabel + ' cohort responds hard when reached. Prioritize retargeting there in the final week.');
                        else if (_pdfMoverLabel)             _pdfImpl = _pdfHead + 'One angle: ' + _pdfMoverLabel + (_pdfPostOpen ? ' read as the checkout-page driver of the campaign. Anchor the next launch plan around it.' : ' reads as the checkout-page driver right now. Concentrate spend behind it through the final week.');
                        else                                 _pdfImpl = _pdfHead + 'One angle: ' + (_pdfPostOpen ? 'the campaign has run its course. Pull the picker back to opening week to see the assets that carried the checkout-page signal.' : 'pull the read forward with the picker to see the assets carrying the checkout-page signal, then concentrate spend there in the final week.');"""
src = splice(src, OLD_PDF, NEW_PDF, "pdf implications")

INDEX.write_text(src, encoding="utf-8")
print(f"OK: {before:,} -> {len(src):,} bytes ({len(src) - before:+,}). Backup at {BACKUP}")
