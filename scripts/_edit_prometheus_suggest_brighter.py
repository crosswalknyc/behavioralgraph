#!/usr/bin/env python3
"""Prometheus: brighten the 'Suggestions' word to match its lime pill edge.

The Suggestions button sits on the widget's dark composer bar (dark in
both themes), so the light-mode olive text (#4d7c0f) read muddy against
the bright lime border. Match the text to the edge: #C7F23E (hover
#d3f95e).
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    assert src.count(old) == 1, "anchor not unique (%d): %s" % (src.count(old), label)
    return src.replace(old, new)

# Light-mode Suggestions base text -> bright lime (match the pill edge).
src = sub_once(
    "            color: #4d7c0f !important;\n",
    "            color: #C7F23E !important;\n",
    "suggestions text -> lime edge",
)
# Light-mode Suggestions hover text -> brighter lime.
src = sub_once(
    "            color: #3d5c04 !important;\n",
    "            color: #d3f95e !important;\n",
    "suggestions hover text -> brighter lime",
)

assert src.rstrip().endswith("</html>")
assert src.count("color: #4d7c0f !important;") == 0
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
