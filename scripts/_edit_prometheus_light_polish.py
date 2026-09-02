#!/usr/bin/env python3
"""Light-mode polish for the Prometheus (Profile IQ Assistant) widget.

The composer box, suggestion chips, Suggestions button, and mic read
anemic on the light shell (pale white fills + ghost grey borders). Give
them tinted-purple fills, defined purple borders, and darker text so
they pop; saturate the lime "Analyze the open profile" accent; and put
crisper borders on the message box + composer input. Covers both the
static chips and the JS-rendered welcome chips (via the synthChatChipFill
onclick selector). Dark mode is untouched (all rules are light-scoped).
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

ANCHOR = "\n        /* ---- Empty-state design (2026-08-26 polish) ---------------------\n"
assert src.count(ANCHOR) == 1, "empty-state anchor not unique (%d)" % src.count(ANCHOR)

CSS = """
        /* ---- Prometheus light-mode polish (2026-09-02) ------------------
           In light mode the composer box + buttons read anemic (pale
           white fills, ghost borders). Give them our purple accent with
           defined borders + darker text so they pop; saturate the lime
           accent chip. Dark mode is untouched. */
        body[data-theme="light"] #prometheusWidget #synthChatMessages {
            background: #ffffff !important;
            border: 1px solid #cbb8f2 !important;
            box-shadow: 0 1px 3px rgba(76,29,149,0.08);
        }
        body[data-theme="light"] #prometheusWidget #synthChatInput {
            background: #ffffff !important;
            border: 1px solid #cbb8f2 !important;
            color: #1f2430 !important;
        }
        /* Muted suggestion chips (Prep a pitch / Build / Size / Cut /
           Refresh - both the static row and the JS welcome chips) and the
           Suggestions button. */
        body[data-theme="light"] #prometheusWidget button[onclick*="synthChatChipFill"],
        body[data-theme="light"] #prometheusWidget #synthChatSuggestBtn {
            background: #efeafc !important;
            border: 1px solid #8b5cf6 !important;
            color: #4c1d95 !important;
            font-weight: 600 !important;
        }
        body[data-theme="light"] #prometheusWidget button[onclick*="synthChatChipFill"]:hover,
        body[data-theme="light"] #prometheusWidget #synthChatSuggestBtn:hover {
            background: #e3d9fa !important;
            border-color: #7c3aed !important;
            color: #3b0f7a !important;
        }
        /* Primary "Analyze the open profile" accent chip: saturate the
           lime so it doesn't read olive/washed. */
        body[data-theme="light"] #prometheusWidget button[onclick*="Analyze this data"] {
            background: rgba(160,217,17,0.22) !important;
            border: 1px solid #7fae0f !important;
            color: #2f4d05 !important;
            font-weight: 600 !important;
        }
        /* Mic button: defined purple pill to match. */
        body[data-theme="light"] #prometheusWidget #synthChatMicBtn {
            background: #efeafc !important;
            border: 1px solid #8b5cf6 !important;
            color: #6d28d9 !important;
            border-radius: 999px !important;
            padding: 0.3rem 0.5rem !important;
        }
        body[data-theme="light"] #prometheusWidget #synthChatMicBtn:hover {
            background: #e3d9fa !important;
            border-color: #7c3aed !important;
        }
        /* Input hint: lift off ghost-grey. */
        body[data-theme="light"] #prometheusWidget #synthChatInputHint {
            color: #6b7280 !important;
            opacity: 0.9 !important;
        }
"""

src = src.replace(ANCHOR, CSS + ANCHOR)

assert src.rstrip().endswith("</html>")
assert src.count('#prometheusWidget button[onclick*="synthChatChipFill"]') == 2
assert src.count('body[data-theme="light"] #prometheusWidget #synthChatMicBtn {') == 1

new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
