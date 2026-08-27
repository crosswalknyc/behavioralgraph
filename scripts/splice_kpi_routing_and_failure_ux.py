#!/usr/bin/env python3
"""KPI metric-ask routing + failure UX copy in the chat widget.

2026-08-27 (Jenna / Paige Bueckers ad CTR defect):
1. _pmLooksMetricKpi helper (mirrors detect_metric_kpi_intent in
   prometheus_analysis.py) + wire into _pmShouldAnalyze.
2. KPI pivot bypass so an armed clarify / date-confirm step never
   swallows a measurement question.
3. Cancel copy reworded neutral.
4. SYNTH_CHAT_CALM_MSG matches the server calm line.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_kpi_routing.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


OLD_1 = """        function _pmShouldAnalyze(text, ctx) {
            var t = String(text || '').trim();
            if (!t) return false;
            // Build / pipeline asks always go to the interpret flow.
            if (/\\b(run|build|pull|create|queue|start|launch|generate)\\b[^.]{0,60}\\bprofiles?\\b/i.test(t)) return false;
            if (/\\b(cut|slice)\\b[^.]{0,40}\\bby\\b/i.test(t)) return false;
            if (/\\brefresh\\b|\\bsample size\\b|\\bincidence\\b|\\bstatus\\b|\\bcredits?\\b/i.test(t)) return false;
            var analytic ="""

NEW_1 = """        // Ad-metric / KPI vocabulary (2026-08-27, Jenna / Paige Bueckers
        // ad CTR defect). Mirrors detect_metric_kpi_intent in
        // prometheus_analysis.py: a KPI name (CTR, click-through,
        // engagement rate, CPM, ROAS, ...) is a measurement ask on its
        // own and must never fall through to the build flow or be
        // swallowed by an armed clarify step.
        function _pmLooksMetricKpi(text) {
            var t = String(text || '').trim();
            if (!t || t.length > 600) return false;
            if (/\\b(build|create|make|pull|queue|launch|refresh)\\b[^.?!]{0,40}\\b(profile|cut|audience|cohort)s?\\b/i.test(t)) return false;
            return (
                /\\bctr\\b|\\bclick[\\s-]?through(?:\\s+rates?)?\\b|\\bclick\\s+rates?\\b/i.test(t) ||
                /\\b(?:engagement|conversion|completion|response|open|bounce|view[\\s-]?through|watch[\\s-]?through|click[\\s-]?to[\\s-]?open|interaction|swipe[\\s-]?up)\\s+rates?\\b/i.test(t) ||
                /\\bcpm\\b|\\bcpc\\b|\\bcpa\\b|\\bcpv\\b|\\bcpi\\b|\\becpm\\b|\\bcvr\\b|\\bvtr\\b|\\bctor\\b|\\broas\\b/i.test(t) ||
                /\\bcost\\s+per\\s+(?:click|thousand|mille|acquisition|view|install|impression)\\b/i.test(t) ||
                /\\breturn\\s+on\\s+ad\\s+spend\\b/i.test(t) ||
                /\\bad\\s+(?:recall|impressions?|clicks?|frequency|performance|engagement|completions?|conversions?)\\b/i.test(t)
            );
        }

        function _pmShouldAnalyze(text, ctx) {
            var t = String(text || '').trim();
            if (!t) return false;
            // Build / pipeline asks always go to the interpret flow.
            if (/\\b(run|build|pull|create|queue|start|launch|generate)\\b[^.]{0,60}\\bprofiles?\\b/i.test(t)) return false;
            if (/\\b(cut|slice)\\b[^.]{0,40}\\bby\\b/i.test(t)) return false;
            if (/\\brefresh\\b|\\bsample size\\b|\\bincidence\\b|\\bstatus\\b|\\bcredits?\\b/i.test(t)) return false;
            // Metric / KPI asks route to the analyze path whether or
            // not a profile is open (base resolution happens there).
            if (_pmLooksMetricKpi(t)) return true;
            var analytic ="""

OLD_2 = """            if (synthChatBatchClarify) {
                if (synthChatBatchDrafts && synthChatBatchDrafts.length) {
                    await _synthChatBatchClarifyAnswer(text);
                    return;
                }
                synthChatBatchClarify = null;
            }
            if (synthChatClarify && synthChatClarify.steps &&
                synthChatClarify.idx < synthChatClarify.steps.length) {
                await _synthChatClarifyAnswer(text);
                return;
            }"""

NEW_2 = """            // Metric / KPI pivot (2026-08-27, ad CTR defect): a KPI
            // ask mid-clarify is a measurement question, never an
            // answer to the region / cuts picker. Skip the clarify
            // and date-confirm interceptions so it reaches the
            // analyze routing below; the draft stays open and the
            // picker can still be answered afterward.
            var _pmKpiPivot = _pmLooksMetricKpi(text);
            if (!_pmKpiPivot && synthChatBatchClarify) {
                if (synthChatBatchDrafts && synthChatBatchDrafts.length) {
                    await _synthChatBatchClarifyAnswer(text);
                    return;
                }
                synthChatBatchClarify = null;
            }
            if (!_pmKpiPivot && synthChatClarify && synthChatClarify.steps &&
                synthChatClarify.idx < synthChatClarify.steps.length) {
                await _synthChatClarifyAnswer(text);
                return;
            }"""

OLD_3 = """            if (synthChatAwaitingDateConfirm) {
                if (_synthChatIsDateConfirmation(text)) {"""

NEW_3 = """            if (synthChatAwaitingDateConfirm && !_pmKpiPivot) {
                if (_synthChatIsDateConfirmation(text)) {"""

OLD_4 = """            synthChatPushTurn('agent',
                wasBatch ? 'Batch cancelled. Send another message to try again.'
                         : 'Draft cancelled. Send another message to try again.');"""

NEW_4 = """            synthChatPushTurn('agent',
                wasBatch ? 'Batch closed. Ask me anything else when ready.'
                         : 'Draft closed. Ask me anything else when ready.');"""

OLD_5 = """        var SYNTH_CHAT_CALM_MSG = "Working on it - you'll receive an email when it's completed.";"""

NEW_5 = """        var SYNTH_CHAT_CALM_MSG = "Working on it. This one needs a closer look and will come back to you shortly.";"""


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    src = splice(src, OLD_1, NEW_1, "kpi helper + _pmShouldAnalyze")
    src = splice(src, OLD_2, NEW_2, "clarify interception bypass")
    src = splice(src, OLD_3, NEW_3, "date-confirm bypass")
    src = splice(src, OLD_4, NEW_4, "cancel copy")
    src = splice(src, OLD_5, NEW_5, "calm message")
    INDEX.write_text(src, encoding="utf-8")
    print("[splice] all 5 edits applied")


if __name__ == "__main__":
    main()
