#!/usr/bin/env python3
"""Every stock sentence Prometheus composes survives the exit byte for
byte (stage 4 of the 2026-11-19 rebuild, approved by Jenna 2026-11-20).

The incident: ``orders.RAW_PHRASE`` composed "the count inside the 10
million US consumers we measure" and ``guards.scrub_sample_vocabulary``,
written in another file, cut "inside the 10 million " out of it, so a
reader got "the countUS consumers we measure" (2026-11-19). The template
and the scrub never met before a reader did. This suite makes them meet
here:

  1. ``prometheus.exit.templates()``: every stock sentence the modules
     register or compose (module constants that read as sentences, each
     module's own TEMPLATES, the rendered samples with numbers in them),
     each with its field kind. ``exit.text(kind)`` must return it
     unchanged.
  2. A sweep of every string literal in ``prometheus/*.py`` that reads
     as a sentence a reader could see (not a regex, not a prompt to the
     model, not a key, not an f-string part). The pipeline must leave
     each one alone, whitespace aside.

A failure here is a template carrying a word the exit will cut (fix the
template, or hold the exact line out in ``vocabulary.HOUSE_LINE_SOURCES``
when the words are the ones Jenna approved), or a pass misreading a
house sentence (fix the pass). The suite never weakens a ban to pass.

The brief card's "Estimated audience: about N to N US people." is
checked as a BRIEF_FIELD, the kind the card is scrubbed as (the sample
pass only): the label is Jenna's decision (2026-08-24) and the exit
leaves it exactly as written. The same line as PROSE would read
"Audience range"; that change is not made here.

Hermetic: no network, no S3, no model. Run from either repo.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB = os.path.dirname(_HERE)
if not os.path.exists(os.path.join(_WEB, 'prometheus', 'exit.py')):
    _WEB = os.path.join(_WEB, 'bg-webapp')
if _WEB not in sys.path:
    sys.path.insert(0, _WEB)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import exit as X  # noqa: E402
from prometheus import vocabulary as V  # noqa: E402

# modules whose strings never reach a reader as written: this exit's own
# samples (registered with their kinds through templates()), the store's
# SQL, the routing table's documentation rows, the table itself
_SWEEP_SKIP = {'exit.py', 'store.py', 'routing_table.py', 'vocabulary.py'}
# a name that marks text written for the model or for an operator
_MODEL_NAME_RX = re.compile(
    r'PROMPT|CHARTER|RULEBOOK|SYSTEM|INSTRUCTION|GUIDANCE|SCHEMA|RULES|NOTE|REASK|QUESTION|EXAMPLE|BANK|SEED|CANON|LEAK|'
    r'block|prompt|frame|instruction|guidance|rule', re.I)
_CALL_SKIP = frozenset(('compile', 'search', 'match', 'sub', 'findall', 'fullmatch', 'finditer', 'split',
                        'print', 'warn', 'warning', 'note', 'debug', 'info', 'error', 'exception'))
_SENTENCE_RX = re.compile(r'^[A-Z"\'(].*[a-z].*[.!?)]\s*$', re.S)


def _norm(s):
    return ' '.join(str(s or '').replace('\n', ' ').split()).strip(' ,')


def reader_literals(path):
    """[(lineno, text)] for the string literals in one module that read
    as a sentence a reader could see."""
    try:
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                skip.add(id(body[0].value))          # a docstring
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, 'attr', None) or getattr(fn, 'id', None) or ''
            if name in _CALL_SKIP:
                for a in ast.walk(node):
                    if isinstance(a, ast.Constant):
                        skip.add(id(a))              # a regex or a log line
        if isinstance(node, ast.JoinedStr):
            for a in ast.walk(node):
                if isinstance(a, ast.Constant):
                    skip.add(id(a))                  # an f-string part is glue, not a sentence
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_MODEL_NAME_RX.search(getattr(t, 'id', '') or '') for t in targets):
                for a in ast.walk(node):
                    if isinstance(a, ast.Constant):
                        skip.add(id(a))              # text for the model
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _MODEL_NAME_RX.search(node.name):
            for a in ast.walk(node):
                if isinstance(a, ast.Constant):
                    skip.add(id(a))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            v = node.value
            if 25 <= len(v) <= 600 and _SENTENCE_RX.match(v.strip()) and ' ' in v \
                    and '\\b' not in v and '(?' not in v and '{' not in v and '%s' not in v:
                out.append((node.lineno, v))
    return out


class TemplateRegistry(unittest.TestCase):
    def test_every_registered_template_survives_the_exit(self):
        ts = X.templates()
        self.assertGreater(len(ts), 100, 'the registry should collect the modules\' stock sentences')
        changed = []
        for where, kind, s in ts:
            out = X.text(s, kind)
            if _norm(out) != _norm(s):
                changed.append(f'{where} [{kind}]\n    in : {s[:200]!r}\n    out: {out[:200]!r}')
        self.assertEqual(changed, [], 'the exit would change these stock sentences:\n' + '\n'.join(changed))
        print(f'  ok   {len(ts)} registered templates unchanged by the exit')

    def test_registry_covers_the_incident_templates(self):
        wheres = {w for w, _k, _s in X.templates()}
        for need in ('orders.RAW_PHRASE in its sentence', 'brief card estimated_audience_sentence', 'guards.PROVENANCE_REPLY',
                     'priced_read.COST_LINE', 'methodology.NIELSEN_LINE', 'methodology.PEACOCK_LINE', 'data_floor.SENTENCE'):
            self.assertIn(need, wheres)

    def test_raw_phrase_keeps_its_ten_million(self):
        from prometheus import orders as _o
        line = f"Every row carries both the projected US figure and {_o.RAW_PHRASE}."
        self.assertEqual(X.text(line), line)
        # and through the payload exit, the way a reply carries it
        raw = {'success': True, 'reply': line}
        X.payload(raw, surface='analyze')
        self.assertEqual(raw['reply'], line)
        self.assertNotIn('countUS', raw['reply'])

    def test_brief_card_label_is_left_exactly_as_written(self):
        """The 'Estimated audience' label is Jenna's call (2026-08-24); the
        card field runs the sample pass only, so the line is untouched."""
        line = 'Estimated audience: about 5,222,482 to 7,279,639 US people.'
        self.assertEqual(X.text(line, X.BRIEF_FIELD), line)
        raw = {'success': True, 'spec_draft': {'estimated_audience_sentence': line}}
        X.payload(raw, surface='interpret')
        self.assertEqual(raw['spec_draft']['estimated_audience_sentence'], line)
        # the card field with the sample count in it loses only the count (2026-10-26)
        inside = 'Estimated audience: 158,305 to 220,662 inside the 10 million sample, about 5,222,482 to 7,279,639 US people.'
        self.assertEqual(X.text(inside, X.BRIEF_FIELD), 'Estimated audience: about 5,222,482 to 7,279,639 US people.')
        # for the record: the same words as prose would read differently; that decision is not made here
        self.assertTrue(X.text(line, X.PROSE).startswith(('Audience range', 'Measured audience')))

    def test_view_definition_lines_come_back_byte_for_byte(self):
        from prometheus import methodology as _meth
        for s in _meth.OTHERS_ALLOWLIST:
            self.assertEqual(X.text(s), s)
            self.assertEqual(X.text('Reach was 4.2M. ' + s + ' The share is 12%.'), 'Reach was 4.2M. ' + s + ' The share is 12%.')
        # and any other sentence that names the service still goes
        self.assertNotIn('Nielsen', X.text('Nielsen measured the premiere at 4.1M. Our count is 3.9M.'))

    def test_house_lines_come_back_byte_for_byte(self):
        lines = V.house_lines()
        self.assertEqual(len(lines), len(V.HOUSE_LINE_SOURCES))
        for s in lines:
            self.assertEqual(X.text(s), s)
            self.assertEqual(X.text('Reach was 4.2M. ' + s), 'Reach was 4.2M. ' + s)
        # the same ideas in other words are still scrubbed
        self.assertNotIn('household', X.text('Age is a household guess here.'))
        self.assertNotIn('panel', X.text('The panel projected to the US gives 3.1M.').lower())


class LiteralSweep(unittest.TestCase):
    def test_every_reader_sentence_in_the_package_survives_the_exit(self):
        pkg = os.path.join(_WEB, 'prometheus')
        files = sorted(f for f in os.listdir(pkg) if f.endswith('.py') and f not in _SWEEP_SKIP)
        total, changed = 0, []
        for f in files:
            for ln, s in reader_literals(os.path.join(pkg, f)):
                total += 1
                out = X.text(s, X.PROSE)
                if _norm(out) != _norm(s):
                    changed.append(f'{f}:{ln}\n    in : {s[:200]!r}\n    out: {out[:200]!r}')
        self.assertGreater(total, 400, 'the sweep should find the package\'s stock sentences')
        self.assertEqual(changed, [], 'the exit would change these literal sentences:\n' + '\n'.join(changed))
        print(f'  ok   {total} literal sentences in {len(files)} modules unchanged by the exit')

    def test_the_fixed_templates_read_as_intended(self):
        """The templates this suite found mangled on 2026-11-20, as they
        now read, through the real exit."""
        from prometheus import dashboard_map as _dm
        line = _dm.RAIL['line']
        self.assertIn('this window', line)
        self.assertEqual(X.text(line), line)
        raw = {'success': True, 'reply': line}
        X.payload(raw, surface='analyze')
        self.assertEqual(raw['reply'], line)          # no "left edge of this data"
        with open(os.path.join(_WEB, 'prometheus', 'kpi_definitions.py'), encoding='utf-8') as fh:
            self.assertNotIn('re-read for that', fh.read())
        for p in ('partner_api.py', 'webhooks.py'):
            with open(os.path.join(_WEB, 'prometheus', p), encoding='utf-8') as fh:
                self.assertNotIn('on our side. Poll again', fh.read())
        held = 'Held for a moment. Poll again shortly.'
        self.assertEqual(X.text(held, X.PARTNER), held)
        self.assertEqual(X.text(held), held)


if __name__ == '__main__':
    unittest.main(verbosity=1)
