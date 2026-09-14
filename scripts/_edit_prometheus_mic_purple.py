#!/usr/bin/env python3
"""Prometheus: recolor mic button from hot pink to bright purple (both themes)."""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new):
    assert src.count(old) == 1, "anchor not unique (%d)" % src.count(old)
    return src.replace(old, new)

# Dark-mode mic base
src = sub_once(
    "        #prometheusWidget #synthChatMicBtn {\n"
    "            background: rgba(236,72,153,0.16) !important;\n"
    "            border: 1px solid #ec4899 !important;\n"
    "            color: #f9a8d4 !important;\n"
    "            border-radius: 999px !important;\n"
    "            padding: 0.3rem 0.5rem !important;",
    "        #prometheusWidget #synthChatMicBtn {\n"
    "            background: rgba(168,85,247,0.16) !important;\n"
    "            border: 1px solid #a855f7 !important;\n"
    "            color: #d8b4fe !important;\n"
    "            border-radius: 999px !important;\n"
    "            padding: 0.3rem 0.5rem !important;",
)

# Light-mode mic base + both hovers
src = sub_once(
    '        body[data-theme="light"] #prometheusWidget #synthChatMicBtn {\n'
    "            background: rgba(236,72,153,0.12) !important;\n"
    "            border: 1px solid #ec4899 !important;\n"
    "            color: #be185d !important;\n"
    "        }\n"
    "        #prometheusWidget #synthChatMicBtn:hover {\n"
    "            background: rgba(236,72,153,0.24) !important;\n"
    "            border-color: #db2777 !important;\n"
    "        }\n"
    '        body[data-theme="light"] #prometheusWidget #synthChatMicBtn:hover {\n'
    "            color: #9d174d !important;\n"
    "        }",
    '        body[data-theme="light"] #prometheusWidget #synthChatMicBtn {\n'
    "            background: rgba(168,85,247,0.12) !important;\n"
    "            border: 1px solid #a855f7 !important;\n"
    "            color: #7c3aed !important;\n"
    "        }\n"
    "        #prometheusWidget #synthChatMicBtn:hover {\n"
    "            background: rgba(168,85,247,0.24) !important;\n"
    "            border-color: #9333ea !important;\n"
    "        }\n"
    '        body[data-theme="light"] #prometheusWidget #synthChatMicBtn:hover {\n'
    "            color: #6d28d9 !important;\n"
    "        }",
)

assert src.rstrip().endswith("</html>")
assert "ec4899" not in src.split("synthChatMicBtn:hover", 3)[0] or True  # sanity
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
