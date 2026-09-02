#!/usr/bin/env python3
"""Prometheus tweaks (2026-09-02, round 2):
1. Suggestions button + 'builds: ...' counter -> lime (#C7F23E).
   Mic stays hot pink (#ec4899). Chat highlight chips stay purple.
2. Fix the bottom-left hint: stop the confusing ellipsis truncation
   (let it wrap) and keep it legible.
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    assert src.count(old) == 1, "anchor not unique (%d): %s" % (src.count(old), label)
    return src.replace(old, new)

# --- 1. Split Suggestions (lime) from mic (pink). --------------------------
OLD_BLOCK = (
    "        /* Hot-pink accent (in-house #ec4899) for Suggestions + mic,\n"
    "           per Jessie 2026-09-02. Chat highlight chips stay purple. */\n"
    "        #prometheusWidget #synthChatSuggestBtn,\n"
    "        #prometheusWidget #synthChatMicBtn {\n"
    "            background: rgba(236,72,153,0.16) !important;\n"
    "            border: 1px solid #ec4899 !important;\n"
    "            color: #f9a8d4 !important;\n"
    "            border-radius: 999px !important;\n"
    "        }\n"
    "        #prometheusWidget #synthChatMicBtn { padding: 0.3rem 0.5rem !important; }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn,\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatMicBtn {\n"
    "            background: rgba(236,72,153,0.12) !important;\n"
    "            border: 1px solid #ec4899 !important;\n"
    "            color: #be185d !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn { font-weight: 600 !important; }\n"
    "        #prometheusWidget #synthChatSuggestBtn:hover,\n"
    "        #prometheusWidget #synthChatMicBtn:hover {\n"
    "            background: rgba(236,72,153,0.24) !important;\n"
    "            border-color: #db2777 !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn:hover,\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatMicBtn:hover {\n"
    "            color: #9d174d !important;\n"
    "        }\n"
)
NEW_BLOCK = (
    "        /* Mic: in-house hot pink (#ec4899). Suggestions + the 'builds'\n"
    "           counter: lime (#C7F23E). Chat highlight chips stay purple.\n"
    "           (Jessie 2026-09-02) */\n"
    "        #prometheusWidget #synthChatMicBtn {\n"
    "            background: rgba(236,72,153,0.16) !important;\n"
    "            border: 1px solid #ec4899 !important;\n"
    "            color: #f9a8d4 !important;\n"
    "            border-radius: 999px !important;\n"
    "            padding: 0.3rem 0.5rem !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatMicBtn {\n"
    "            background: rgba(236,72,153,0.12) !important;\n"
    "            border: 1px solid #ec4899 !important;\n"
    "            color: #be185d !important;\n"
    "        }\n"
    "        #prometheusWidget #synthChatMicBtn:hover {\n"
    "            background: rgba(236,72,153,0.24) !important;\n"
    "            border-color: #db2777 !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatMicBtn:hover {\n"
    "            color: #9d174d !important;\n"
    "        }\n"
    "        #prometheusWidget #synthChatSuggestBtn {\n"
    "            background: rgba(199,242,62,0.16) !important;\n"
    "            border: 1px solid #C7F23E !important;\n"
    "            color: #C7F23E !important;\n"
    "            border-radius: 999px !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn {\n"
    "            background: rgba(160,217,17,0.18) !important;\n"
    "            border: 1px solid #A0D911 !important;\n"
    "            color: #3d5c04 !important;\n"
    "            font-weight: 600 !important;\n"
    "        }\n"
    "        #prometheusWidget #synthChatSuggestBtn:hover {\n"
    "            background: rgba(199,242,62,0.28) !important;\n"
    "            border-color: #A0D911 !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn:hover {\n"
    "            background: rgba(160,217,17,0.28) !important;\n"
    "            color: #2c4303 !important;\n"
    "        }\n"
)
src = sub_once(OLD_BLOCK, NEW_BLOCK, "suggest/mic split")

# --- 2. Hint: allow wrap (kill the ellipsis) + keep legible. ---------------
src = sub_once(
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatInputHint {\n"
    "            color: #4b5563 !important;\n"
    "            opacity: 1 !important;\n"
    "        }\n",
    "        #prometheusWidget #synthChatInputHint {\n"
    "            white-space: normal !important;\n"
    "            overflow: visible !important;\n"
    "            text-overflow: clip !important;\n"
    "            line-height: 1.2;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatInputHint {\n"
    "            color: #4b5563 !important;\n"
    "            opacity: 1 !important;\n"
    "        }\n",
    "hint wrap",
)

# --- 1b. 'builds: ...' counter -> lime. ------------------------------------
src = sub_once(
    '<span id="synthChatQueueBadge" style="font-size: 0.72rem; color: #ec4899;"></span>',
    '<span id="synthChatQueueBadge" style="font-size: 0.72rem; color: #C7F23E;"></span>',
    "builds badge lime",
)

# --- integrity ---
assert src.rstrip().endswith("</html>")
assert 'color: #C7F23E;"></span>' in src
assert "#prometheusWidget #synthChatSuggestBtn {\n            background: rgba(199,242,62,0.16) !important;" in src
assert src.count("color: #ec4899;\"></span>") == 0

new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
