#!/usr/bin/env python3
"""Render the CSV download anchor on data answers (2026-09-29 Jenna:
every data answer carries its file). Byte-level splice per
index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_file_link_render.html")

src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")


def sp(old, new, desc):
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"[fail] {desc}: anchor x{n}")
    src = src.replace(old, new)
    print(f"[ok] {desc}")


# Analyze success push: attach the file link to the turn meta.
sp("""                if (data.offer_deck) {
                    _pmPendingDeckAngle = data.deck_angle || null;
                    opts.push({ label: 'Build a deck from this',
                                send: 'Build a deck from this' });
                }
                if (opts.length) {
                    _synthChatPushTurnWithMeta('agent', data.reply, { options: opts });
                } else {
                    synthChatPushTurn('agent', data.reply);
                }
                return 'done';""",
   """                if (data.offer_deck) {
                    _pmPendingDeckAngle = data.deck_angle || null;
                    opts.push({ label: 'Build a deck from this',
                                send: 'Build a deck from this' });
                }
                var _turnMeta = {};
                if (opts.length) _turnMeta.options = opts;
                if (data.file_link && data.file_link.url &&
                    String(data.file_link.url).indexOf('https://') === 0) {
                    // Every data answer carries its CSV (2026-09-29):
                    // the download anchor renders under the turn.
                    _turnMeta.link = {
                        url: String(data.file_link.url),
                        label: String(data.file_link.label || 'Download CSV')
                    };
                }
                if (_turnMeta.options || _turnMeta.link) {
                    _synthChatPushTurnWithMeta('agent', data.reply, _turnMeta);
                } else {
                    synthChatPushTurn('agent', data.reply);
                }
                return 'done';""",
   "analyze push carries file link")

# Async read delivery: same link, plus auto-save on explicit asks.
sp("""                    _synthChatPushTurnWithMeta('agent', p.reply,
                        { options: opts, read_job_id: jobId });
                    return;""",
   """                    var _rjMeta = { options: opts, read_job_id: jobId };
                    if (p.file_link && p.file_link.url &&
                        String(p.file_link.url).indexOf('https://') === 0) {
                        _rjMeta.link = {
                            url: String(p.file_link.url),
                            label: String(p.file_link.label || 'Download CSV')
                        };
                    }
                    try { _synthChatMaybeSaveFile(p); } catch (_) {}
                    _synthChatPushTurnWithMeta('agent', p.reply, _rjMeta);
                    return;""",
   "async read delivery carries file link")

INDEX.write_text(src, encoding="utf-8")
print(f"[done] {INDEX} patched ({len(src):,} bytes)")
