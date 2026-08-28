#!/usr/bin/env python3
"""CSV export UX (2026-08-28 Jenna): a chat reply that carries
download_url auto-triggers a normal browser download (regular
downloads folder, filename set server-side to the task title). The
raw presigned link never renders in the chat. Hooked in the shared
_synthChatFetchJson helper so every widget route is covered."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_csv_autodownload.html")

OLD = """        async function _synthChatFetchJson(url, body, signal) {
            async function _once() {
                var r = await fetch(url, {
                    method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                    signal: signal
                });
                var data = null, parseFailed = false;
                try { data = await r.json(); }
                catch (_) { parseFailed = true; }
                return { r: r, data: data,
                         retriable: parseFailed || r.status === 502 ||
                                    r.status === 503 || r.status === 504 };
            }
            var first = await _once();
            if (!first.retriable) return first;
            await new Promise(function(res) { setTimeout(res, 4000); });
            return await _once();
        }"""

NEW = """        // Auto-save (2026-08-28 Jenna): a reply carrying
        // download_url triggers a normal browser download (regular
        // downloads folder; the filename is the task title, set
        // server-side). One trigger per URL; the chat text never
        // shows the raw link.
        var _synthChatSavedUrls = {};
        function _synthChatMaybeSaveFile(data) {
            try {
                if (!data || !data.success || !data.download_url) return;
                var u = String(data.download_url);
                if (_synthChatSavedUrls[u]) return;
                _synthChatSavedUrls[u] = true;
                var a = document.createElement('a');
                a.href = u;
                a.download = String(data.filename || '');
                a.style.display = 'none';
                document.body.appendChild(a);
                a.click();
                setTimeout(function() {
                    try { document.body.removeChild(a); } catch (_) {}
                }, 4000);
            } catch (_) {}
        }

        async function _synthChatFetchJson(url, body, signal) {
            async function _once() {
                var r = await fetch(url, {
                    method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                    signal: signal
                });
                var data = null, parseFailed = false;
                try { data = await r.json(); }
                catch (_) { parseFailed = true; }
                return { r: r, data: data,
                         retriable: parseFailed || r.status === 502 ||
                                    r.status === 503 || r.status === 504 };
            }
            var first = await _once();
            if (!first.retriable) {
                _synthChatMaybeSaveFile(first.data);
                return first;
            }
            await new Promise(function(res) { setTimeout(res, 4000); });
            var second = await _once();
            _synthChatMaybeSaveFile(second.data);
            return second;
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
src = splice(src, OLD, NEW, "csv auto-download hook in _synthChatFetchJson")
INDEX.write_text(src, encoding="utf-8")
print("[splice] csv auto-download hook applied")
