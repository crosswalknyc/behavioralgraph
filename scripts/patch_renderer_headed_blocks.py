#!/usr/bin/env python3
"""Header line stacked directly on its table rows or bullets (no blank
line between) splits into two blocks so both render with their full
treatment (2026-09-30: the checkout fall-off email's sections rendered
as plain text because the header shared a block with its rows)."""
import ast
from pathlib import Path

EXPAND = '''

def _expand_blocks(blocks):
    """A header line stacked directly on its table rows or bullets (no
    blank line between) splits into header block + content block so
    both render with their full treatment."""
    out = []
    for b in blocks:
        lines = [ln for ln in b.split("\\n") if ln.strip()]
        if len(lines) >= 2:
            first = lines[0].strip()
            rest = "\\n".join(lines[1:])
            if _is_header(first) and (_split_table(rest)
                                      or _is_bullets(rest)):
                out.append(first)
                out.append(rest)
                continue
        out.append(b)
    return out
'''


def splice(path, pairs):
    src = Path(path).read_text()
    for old, new, desc in pairs:
        n = src.count(old)
        if n != 1:
            raise RuntimeError(f"[{path}:{desc}] anchor count={n}")
        src = src.replace(old, new)
        print(f"  ok: {path}: {desc}")
    ast.parse(src)
    Path(path).write_text(src)


# HTML renderer: helper after _is_bullets, wiring after the block split.
splice("prometheus_email_html.py", [
    ('''def _is_bullets(block):
    lines = [ln.strip() for ln in block.split("\\n") if ln.strip()]
    return bool(lines) and all(ln.startswith(("- ", "* ", "\\u2022 "))
                               for ln in lines)
''',
     '''def _is_bullets(block):
    lines = [ln.strip() for ln in block.split("\\n") if ln.strip()]
    return bool(lines) and all(ln.startswith(("- ", "* ", "\\u2022 "))
                               for ln in lines)
''' + EXPAND,
     "expand helper"),
    ('''    blocks = [b.strip() for b in
              re.split(r"\\n\\s*\\n", _clean(body_text)) if b.strip()]
    for i, block in enumerate(blocks):
''',
     '''    blocks = _expand_blocks(
        [b.strip() for b in
         re.split(r"\\n\\s*\\n", _clean(body_text)) if b.strip()])
    for i, block in enumerate(blocks):
''',
     "wire expansion"),
])

# PDF renderer: same helper + wiring.
splice("prometheus_email_pdf.py", [
    ('''def _split_table(block):
''',
     EXPAND.strip() + '''


def _split_table(block):
''',
     "expand helper"),
    ('''    blocks = [b.strip() for b in
              re.split(r"\\n\\s*\\n", _clean(body_text)) if b.strip()]
    i = 0
''',
     '''    blocks = _expand_blocks(
        [b.strip() for b in
         re.split(r"\\n\\s*\\n", _clean(body_text)) if b.strip()])
    i = 0
''',
     "wire expansion"),
])
print("renderers patched + parse OK")
