#!/usr/bin/env python3
"""Subscriber IQ completion turns point at the Subscriber IQ tab with a
download pill (2026-09-25, the Dark Matter S2 rerun). Python byte-level
splice per index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_subiq_completion_link.html")

OLD_REOPEN = """                if (cur === 'complete') {
                    if (st.tu_key)   lines.push('File: ' + st.tu_key);
                    if (st.avid_key) lines.push('Avid file: ' + st.avid_key);
                }
                if (cur === 'error' || cur === 'failed') {
                    lines = [SYNTH_CHAT_CALM_MSG];
                }
                synthChatPushTurn('agent', lines.join('\\n'));"""

NEW_REOPEN = """                if (cur === 'complete') {
                    if (st.subiq_s3_key) {
                        // Subscriber IQ pull (2026-09-25): the read
                        // lives in the Subscriber IQ tab, not the
                        // profile dropdown, and carries a download.
                        lines = [name + ' is finished. Your Subscriber IQ read is live in the Subscriber IQ tab now.'];
                        lines.push('File: ' + st.subiq_s3_key);
                    } else {
                        if (st.tu_key)   lines.push('File: ' + st.tu_key);
                        if (st.avid_key) lines.push('Avid file: ' + st.avid_key);
                    }
                }
                if (cur === 'error' || cur === 'failed') {
                    lines = [SYNTH_CHAT_CALM_MSG];
                }
                if (cur === 'complete' && st.subiq_s3_key) {
                    _synthChatPushTurnWithMeta('agent', lines.join('\\n'), {
                        run_id: runId, status: cur,
                        link: { url: window.location.origin + '/api/download-cached/' + encodeURIComponent(st.subiq_s3_key),
                                label: 'Download the data' } });
                } else {
                    synthChatPushTurn('agent', lines.join('\\n'));
                }"""

OLD_POLL = """                        if (cur === 'complete') {
                            if (st.tu_key) msg += '\\nFile: ' + st.tu_key;
                            if (st.avid_key) msg += '\\nAvid file: ' + st.avid_key;
                        }
                        if (cur === 'error' || cur === 'failed') {
                            msg = SYNTH_CHAT_CALM_MSG;
                        }
                        if (cur === 'complete' || cur === 'error' || cur === 'failed') {
                            _pmBuildIndicatorHide();
                        }
                        synthChatPushTurn('agent', msg);"""

NEW_POLL = """                        if (cur === 'complete') {
                            if (st.subiq_s3_key) {
                                msg = subject + ' is finished. Your Subscriber IQ read is live in the Subscriber IQ tab now.'
                                    + '\\nFile: ' + st.subiq_s3_key;
                            } else {
                                if (st.tu_key) msg += '\\nFile: ' + st.tu_key;
                                if (st.avid_key) msg += '\\nAvid file: ' + st.avid_key;
                            }
                        }
                        if (cur === 'error' || cur === 'failed') {
                            msg = SYNTH_CHAT_CALM_MSG;
                        }
                        if (cur === 'complete' || cur === 'error' || cur === 'failed') {
                            _pmBuildIndicatorHide();
                        }
                        if (cur === 'complete' && st.subiq_s3_key) {
                            _synthChatPushTurnWithMeta('agent', msg, {
                                run_id: runId, status: cur,
                                link: { url: window.location.origin + '/api/download-cached/' + encodeURIComponent(st.subiq_s3_key),
                                        label: 'Download the data' } });
                        } else {
                            synthChatPushTurn('agent', msg);
                        }"""


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
src = splice(src, OLD_REOPEN, NEW_REOPEN, "reopen completion block")
src = splice(src, OLD_POLL, NEW_POLL, "live poller completion block")
INDEX.write_text(src, encoding="utf-8")
print("spliced both completion blocks")
