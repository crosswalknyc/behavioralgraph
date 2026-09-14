#!/usr/bin/env python3
"""Prometheus light mode: make the 'Analyze the open profile' accent chip legible.

It sits on the dark #0C1618 composer bar (no light bg override exists there),
so a translucent-lime fill + dark-green text rendered muddy. Switch to a solid
bright-lime pill with dark bold text (same family as the Send button), and add
a matching hover.
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

old = (
    '        body[data-theme="light"] #prometheusWidget button[onclick*="Analyze this data"] {\n'
    "            background: rgba(160,217,17,0.22) !important;\n"
    "            border: 1px solid #7fae0f !important;\n"
    "            color: #2f4d05 !important;\n"
    "            font-weight: 600 !important;\n"
    "        }"
)
new = (
    '        body[data-theme="light"] #prometheusWidget button[onclick*="Analyze this data"] {\n'
    "            background: #C7F23E !important;\n"
    "            border: 1px solid #A0D911 !important;\n"
    "            color: #14330A !important;\n"
    "            font-weight: 700 !important;\n"
    "        }\n"
    '        body[data-theme="light"] #prometheusWidget button[onclick*="Analyze this data"]:hover {\n'
    "            background: #D3F95E !important;\n"
    "            border-color: #B5E61A !important;\n"
    "            color: #14330A !important;\n"
    "        }"
)
assert src.count(old) == 1, "anchor not unique: %d" % src.count(old)
src = src.replace(old, new)

assert src.rstrip().endswith("</html>")
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
