#!/usr/bin/env python3
"""Prometheus tweaks (2026-09-02, round 3):
1. Send button = lime gradient in BOTH themes (was light-only; dark was blue).
2. Light-mode 'Suggestions' word brighter green (#3d5c04 -> #4d7c0f).
3. Light-mode 'Enter to send ...' hint = same grey as the IQ ASSISTANT
   subtitle (#9AA09B).
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    assert src.count(old) == 1, "anchor not unique (%d): %s" % (src.count(old), label)
    return src.replace(old, new)

# --- 1. Send lime in both themes (drop the light-only scope). --------------
src = sub_once(
    "        /* Light mode: Prometheus SEND button uses our lime gradient instead of the default blue. */\n"
    "        body[data-theme=\"light\"] #synthChatSubmitBtn.btn-primary {\n"
    "            background: linear-gradient(135deg, #C7F23E, #A0D911);\n"
    "            border-color: #A0D911;\n"
    "            color: #14330A;\n"
    "        }\n"
    "        body[data-theme=\"light\"] #synthChatSubmitBtn.btn-primary:hover {\n"
    "            background: linear-gradient(135deg, #D3F95E, #B5E61A);\n"
    "            border-color: #B5E61A;\n"
    "            color: #14330A;\n"
    "        }\n",
    "        /* Prometheus SEND button uses our lime gradient in both themes. */\n"
    "        #synthChatSubmitBtn.btn-primary {\n"
    "            background: linear-gradient(135deg, #C7F23E, #A0D911) !important;\n"
    "            border-color: #A0D911 !important;\n"
    "            color: #14330A !important;\n"
    "        }\n"
    "        #synthChatSubmitBtn.btn-primary:hover {\n"
    "            background: linear-gradient(135deg, #D3F95E, #B5E61A) !important;\n"
    "            border-color: #B5E61A !important;\n"
    "            color: #14330A !important;\n"
    "        }\n",
    "send lime both themes",
)

# --- 2. Brighter 'Suggestions' word in light mode. -------------------------
src = sub_once(
    "            color: #3d5c04 !important;\n",
    "            color: #4d7c0f !important;\n",
    "suggestions text brighter",
)
# hover: keep a slightly darker step
src = sub_once(
    "            color: #2c4303 !important;\n",
    "            color: #3d5c04 !important;\n",
    "suggestions hover text",
)

# --- 3. Hint grey = IQ ASSISTANT subtitle grey (#9AA09B). ------------------
src = sub_once(
    "            color: #4b5563 !important;\n",
    "            color: #9AA09B !important;\n",
    "hint grey match",
)

# --- integrity ---
assert src.rstrip().endswith("</html>")
assert "#synthChatSubmitBtn.btn-primary {\n            background: linear-gradient(135deg, #C7F23E, #A0D911) !important;" in src
assert 'body[data-theme="light"] #synthChatSubmitBtn.btn-primary' not in src
assert "color: #4d7c0f !important;" in src
assert "color: #9AA09B !important;" in src

new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
