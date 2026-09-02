#!/usr/bin/env python3
"""Prometheus tweaks (2026-09-02):
1. Recolor Suggestions button, mic, and the 'builds: ...' counter to the
   in-house hot pink (#ec4899). Chat highlight chips stay purple.
2. Subtitle 'Profile IQ assistant' -> 'IQ Assistant'.
3. Brighten the bottom-left 'Enter to send ...' hint.
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    assert src.count(old) == 1, "anchor not unique (%d): %s" % (src.count(old), label)
    return src.replace(old, new)

# --- 1a. Drop the Suggestions button out of the purple chip group so the
#         chips stay purple but Suggestions can go pink. ---------------------
src = sub_once(
    "        body[data-theme=\"light\"] #prometheusWidget button[onclick*=\"synthChatChipFill\"],\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn {\n"
    "            background: #efeafc !important;\n"
    "            border: 1px solid #8b5cf6 !important;\n"
    "            color: #4c1d95 !important;\n"
    "            font-weight: 600 !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget button[onclick*=\"synthChatChipFill\"]:hover,\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatSuggestBtn:hover {\n"
    "            background: #e3d9fa !important;\n"
    "            border-color: #7c3aed !important;\n"
    "            color: #3b0f7a !important;\n"
    "        }\n",
    "        body[data-theme=\"light\"] #prometheusWidget button[onclick*=\"synthChatChipFill\"] {\n"
    "            background: #efeafc !important;\n"
    "            border: 1px solid #8b5cf6 !important;\n"
    "            color: #4c1d95 !important;\n"
    "            font-weight: 600 !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget button[onclick*=\"synthChatChipFill\"]:hover {\n"
    "            background: #e3d9fa !important;\n"
    "            border-color: #7c3aed !important;\n"
    "            color: #3b0f7a !important;\n"
    "        }\n",
    "chips purple group",
)

# --- 1b. Replace the purple mic block with a hot-pink accent block that
#         covers Suggestions + mic (dark base + light override + hover). -----
src = sub_once(
    "        /* Mic button: defined purple pill to match. */\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatMicBtn {\n"
    "            background: #efeafc !important;\n"
    "            border: 1px solid #8b5cf6 !important;\n"
    "            color: #6d28d9 !important;\n"
    "            border-radius: 999px !important;\n"
    "            padding: 0.3rem 0.5rem !important;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatMicBtn:hover {\n"
    "            background: #e3d9fa !important;\n"
    "            border-color: #7c3aed !important;\n"
    "        }\n",
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
    "        }\n",
    "mic->pink block",
)

# --- 3. Brighten the 'Enter to send ...' hint in light mode. ---------------
src = sub_once(
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatInputHint {\n"
    "            color: #6b7280 !important;\n"
    "            opacity: 0.9 !important;\n"
    "        }\n",
    "        body[data-theme=\"light\"] #prometheusWidget #synthChatInputHint {\n"
    "            color: #4b5563 !important;\n"
    "            opacity: 1 !important;\n"
    "        }\n",
    "input hint",
)

# --- 2. Subtitle text. ------------------------------------------------------
src = sub_once(
    '<div id="prometheusSub">Profile IQ assistant</div>',
    '<div id="prometheusSub">IQ Assistant</div>',
    "subtitle",
)

# --- 1c. 'builds: ...' counter -> hot pink (dark header, both modes). -------
src = sub_once(
    '<span id="synthChatQueueBadge" style="font-size: 0.72rem; color: #c4b5fd;"></span>',
    '<span id="synthChatQueueBadge" style="font-size: 0.72rem; color: #ec4899;"></span>',
    "builds badge",
)

# --- integrity ---
assert src.rstrip().endswith("</html>")
assert "IQ Assistant</div>" in src
assert src.count("Profile IQ assistant") == 0
assert 'color: #ec4899;"></span>' in src
assert "#prometheusWidget #synthChatSuggestBtn,\n        #prometheusWidget #synthChatMicBtn {" in src

new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
