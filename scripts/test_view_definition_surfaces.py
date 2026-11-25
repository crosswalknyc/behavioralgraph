#!/usr/bin/env python3
"""Every reader-facing surface defines a view the way prometheus/methodology.py does.

Jenna 2026-11-24 (view_definition = person): a view is one person, counted
once, as printed under every answer, and the methodology page says the
same. Until that date the methodology page and two dashboard notes said a
pause and a restart were two views, so a reader could find two meanings.

Checks:
- static/cw_methodologies.json: the Key Metric Definitions "View" entry and
  the How We Measure a View bullet carry methodology.VIEW_DEFINITION word for
  word; the Subscriber IQ bullet carries methodology.VIEW_NOTE_SHORT; what a
  play is (methodology.PLAY_DEFINITION) sits right after what a view is in
  both places; the build script's sync finds nothing to change (the file is
  in step with methodology.py), and it would rewrite a drifted copy.
- templates/index.html: the Subscriber IQ counting note and the Rankers
  methodology render the bullet from view_note_short (the value app.py builds
  from methodology.py), and hide it when the value is empty.
- No reader-facing file carries the old wording (a play over zero seconds is
  a view, a pause and a restart are two views, 1 second is considered a view).

Hermetic: no app boot, no network, no store.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP))

from prometheus import methodology as meth  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name} {detail}')


def load_builder():
    spec = importlib.util.spec_from_file_location('build_methodology_json', WEBAPP / 'scripts' / 'build_methodology_json.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OLD_WORDING = re.compile(
    r"(?i)(?:play over zero seconds|considered a view|counts? as (?:two|2) views|two views, not one|"
    r"still counts as 2|each press of play[^.]{0,80}\bviews?\b)")

# reader-facing files: the methodology page, the dashboard, the public site, the knowledge Prometheus answers from
SURFACES = (
    ['static/cw_methodologies.json', 'templates/index.html', 'prometheus_knowledge_pack.json', 'prometheus_decisions.json']
    + [str(p.relative_to(WEBAPP)) for p in sorted((WEBAPP / 'site').glob('*.html'))]
    + [str(p.relative_to(WEBAPP)) for p in sorted((WEBAPP / 'site').glob('*.txt'))]
    + [str(p.relative_to(WEBAPP)) for p in sorted((WEBAPP / 'static').glob('*.html'))]
)


def section(sections, title):
    return next((s for s in sections if s.get('title') == title), None)


def block_by_id(html, elem_id):
    """The markup from the element with this id to its first closing </ul>."""
    i = html.find(f'id="{elem_id}"')
    if i < 0:
        return ''
    j = html.find('</ul>', i)
    return html[i:j] if j > i else ''


def main():
    builder = load_builder()
    vd, vs = meth.VIEW_DEFINITION, meth.VIEW_NOTE_SHORT
    check('the house view definition is the person-level one',
          vd.startswith('A view is one person') and 'counted once' in vd and 'one second' in vd)

    print('methodology page (static/cw_methodologies.json)')
    sections = json.loads((WEBAPP / 'static' / 'cw_methodologies.json').read_text(encoding='utf-8'))
    km = section(sections, 'Key Metric Definitions') or {}
    view_entry = next((p for p in km.get('intro') or [] if (p.get('runs') or [{}])[0].get('text', '').strip() == 'View'), None)
    check('Key Metric Definitions has a View entry', view_entry is not None)
    if view_entry:
        body = ''.join(r.get('text', '') for r in view_entry['runs'][1:])
        check('View entry body is methodology.VIEW_DEFINITION word for word', body == vd, repr(body[:90]))
        check('View entry text is the term plus the definition', view_entry.get('text') == 'View' + vd)
    hv = section(sections, 'How We Measure a View') or {}
    vsub = next((s for s in hv.get('subs') or [] if s.get('title') == 'Viewership'), {})
    vruns = [r.get('text', '') for p in vsub.get('paragraphs') or [] for r in p.get('runs') or []]
    check('How We Measure a View carries VIEW_DEFINITION as its view bullet', ('• ' + vd) in vruns)
    sq = section(sections, 'SUBSCRIBER IQ') or {}
    ssub = next((s for s in sq.get('subs') or [] if s.get('title') == 'How These Numbers Are Counted'), {})
    sruns = [r.get('text', '') for p in ssub.get('paragraphs') or [] for r in p.get('runs') or []]
    check('Subscriber IQ counting notes carry VIEW_NOTE_SHORT as the view bullet', ('• ' + vs) in sruns)
    play = getattr(meth, 'PLAY_DEFINITION', '')
    if play:
        terms = [(p.get('runs') or [{}])[0].get('text', '').strip() for p in km.get('intro') or []]
        play_entry = (km.get('intro') or [])[terms.index('Play')] if 'Play' in terms else None
        check('Key Metric Definitions carries Play right after View, word for word from PLAY_DEFINITION',
              play_entry is not None and terms.index('Play') == terms.index('View') + 1
              and ''.join(r.get('text', '') for r in play_entry['runs'][1:]) == play)
        check('How We Measure a View carries PLAY_DEFINITION right after the view bullet',
              ('• ' + vd) in vruns and vruns.index('• ' + vd) + 1 < len(vruns) and vruns[vruns.index('• ' + vd) + 1] == '• ' + play)
    slot_paras = [view_entry] if view_entry else []
    slot_paras += [p for p in vsub.get('paragraphs') or [] if ('• ' + vd) in [r.get('text') for r in p.get('runs') or []]]
    slot_paras += [p for p in ssub.get('paragraphs') or [] if ('• ' + vs) in [r.get('text') for r in p.get('runs') or []]]
    check('each view paragraph shows the same words its runs carry',
          len(slot_paras) == 3 and all(p.get('text') == ''.join(r.get('text', '') for r in p['runs']) for p in slot_paras))
    check('the page is in step with methodology.py (the sync finds nothing to change)',
          builder.sync_view_definition(copy.deepcopy(sections)) == [])
    drifted = types.SimpleNamespace(VIEW_DEFINITION='A view is something else.', VIEW_NOTE_SHORT='Views here are something else.')
    check('a drifted definition is caught (the sync would rewrite all three slots)',
          len(builder.sync_view_definition(copy.deepcopy(sections), methodology=drifted)) == 3)
    if play:
        drift_play = types.SimpleNamespace(VIEW_DEFINITION=vd, VIEW_NOTE_SHORT=vs, PLAY_DEFINITION='A play is something else.')
        check('a drifted play definition is caught in both places',
              len(builder.sync_view_definition(copy.deepcopy(sections), methodology=drift_play)) == 2)
    old = copy.deepcopy(sections)
    for p in section(old, 'How We Measure a View')['subs'][0]['paragraphs']:
        p['runs'][0]['text'] = '• Any play over zero seconds counts as a view. A pause and a restart count as two views, not one.'
        p['text'] = ''.join(r['text'] for r in p['runs'])
        break
    changed = builder.sync_view_definition(old)
    check('the old two-views bullet is put back to the house words by the sync',
          any(c[0] == 'How We Measure a View' for c in changed)
          and ('• ' + vd) in [r['text'] for r in section(old, 'How We Measure a View')['subs'][0]['paragraphs'][0]['runs']])
    try:
        builder.sync_view_definition([{'title': 'Key Metric Definitions', 'intro': [], 'subs': []}])
        check('a missing slot fails loudly', False)
    except ValueError:
        check('a missing slot fails loudly', True)

    print('dashboard notes (templates/index.html)')
    html = (WEBAPP / 'templates' / 'index.html').read_text(encoding='utf-8')
    for elem in ('subiqCountingNote', 'tiqRankersMethodology'):
        blk = block_by_id(html, elem)
        check(f'#{elem} renders the view bullet from view_note_short',
              '{% if view_note_short %}<li>{{ view_note_short }}</li>{% endif %}' in blk)
        check(f'#{elem} carries no hand-written view definition', not OLD_WORDING.search(blk) and 'A view is' not in blk)
    app_src = (WEBAPP / 'app.py').read_text(encoding='utf-8')
    check('app.py renders the dashboard with view_note_short from methodology.py',
          'view_note_short=_view_definition_copy(short=True)' in app_src and 'return _meth.VIEW_NOTE_SHORT' in app_src)
    try:
        import jinja2
        import markupsafe
        env = jinja2.Environment(autoescape=True)
        snippet = block_by_id(html, 'subiqCountingNote')
        snippet = snippet[snippet.find('<ul'):] + '</ul>'
        out = env.from_string(snippet).render(view_note_short=vs)
        check('the Subscriber IQ note shows the methodology words', vs in markupsafe.Markup(out).unescape())
        out_empty = env.from_string(snippet).render(view_note_short='')
        check('an empty value hides the bullet instead of showing an empty line', '<li></li>' not in out_empty)
    except ImportError:
        print('  skip jinja render (jinja2 not installed)')

    print('no reader-facing surface keeps the old wording')
    for rel in SURFACES:
        p = WEBAPP / rel
        if not p.exists():
            continue
        m = OLD_WORDING.search(p.read_text(encoding='utf-8', errors='ignore'))
        check(f'{rel}: no other definition of a view', m is None, repr(m.group(0)) if m else '')

    print(f'\n{PASS} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
