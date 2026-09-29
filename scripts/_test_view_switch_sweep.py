#!/usr/bin/env python3
"""Run scripts/_test_view_switch_sweep.js under JavaScriptCore.

node is not on PATH here, so JSC via `osascript -l JavaScript` stands in.
Extracts the old sweep (from a saved pre-edit copy) and the new one (from
the working tree) and runs both against the same sequences.
"""
import io
import json
import subprocess
import sys

NEW_MARK = '    // Product switching leaves exactly one view standing'
OLD_MARK = '    // Product switching leaves one view standing (2026-09-29). Each'


def extract(path, mark):
    src = io.open(path, encoding='utf-8').read()
    i = src.index(mark)
    start = src.rindex('<script>', 0, i) + len('<script>')
    end = src.index('\n</script>', i)
    return src[start:end]


def main():
    old = extract('/tmp/index.html.pre_viewswitch', OLD_MARK)
    new = extract('templates/index.html', NEW_MARK)
    harness = io.open('scripts/_test_view_switch_sweep.js',
                      encoding='utf-8').read()
    js = (harness + '\n'
          + 'var OLD=' + json.dumps(old) + ';\n'
          + 'var NEW=' + json.dumps(new) + ';\n'
          + 'evaluate("OLD sweep (deployed as 2d45746b)", OLD) + "\\n\\n" + '
            'evaluate("NEW sweep (declared target)", NEW)')
    r = subprocess.run(['osascript', '-l', 'JavaScript', '-e', js],
                       capture_output=True, text=True)
    out = (r.stdout or '').strip()
    err = (r.stderr or '').strip()
    print(out or err)
    return 0 if out else 1


if __name__ == '__main__':
    sys.exit(main())
