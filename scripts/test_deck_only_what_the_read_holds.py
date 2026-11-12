#!/usr/bin/env python3
"""A deck carries only what the read holds, and a name is spelled its own way.

2026-11-11 (evening), the YouTube six-category deck built through the deck
lane needed hand work again:

  1. the builder added a page of example web addresses that were in no read
  2. the lint read a real show title, "The Mandela Catalogue", as a British
     spelling and failed the deck; the title was deleted by hand to pass
  3. the plain-English check misread the headers of decks built with
     cw_deck.js (a stat with a unit or a large figure line read as the
     header; "U.S." and "vs." read as the end of a sentence)
  5. the deck printed 28.2% and 22.2% and said the two hold 50.5%
  +  a six-category page printed four of its six categories, and a table
     held five rows at most

Each is fixed at the source and held here. (Item 4, the mail gate changing
words, is held in the parent suite: scripts/test_design_gate_words.py.)

Hermetic: REGRESSION_TEST_MODE=1, no network, no model call, no mail.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ['PM_MAIL_OFF'] = '1'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import deck_builder as db  # noqa: E402
import prometheus_analysis as pma  # noqa: E402
from migration import design_skill as ds  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

FAILS = []
N = [0]


def check(name, ok, detail=''):
    N[0] += 1
    print(('PASS ' if ok else 'FAIL ') + name + (' ' + str(detail) if detail and not ok else ''))
    if not ok:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix='deck_read_only_')
SC = ds.scripts_dir()
PY = sys.executable


def texts_of(path):
    out = []
    for i, s in enumerate(Presentation(path).slides, 1):
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                out.append((i, sh.text_frame.text.replace('\u00a0', ' ').strip()))
            if getattr(sh, 'has_table', False) and sh.has_table:
                for r in sh.table.rows:
                    for c in r.cells:
                        if c.text.strip():
                            out.append((i, c.text.strip()))
    return out


def run(script, *args):
    r = subprocess.run([PY, str(SC / script), *[str(a) for a in args]], capture_output=True, text=True)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


READ = """On digital original content categories on YouTube:

Comedy leads YouTube digital originals at 4,713,882 US viewers and 28.2% of viewing across the six categories, and Drama enters third at 2,561,438 viewers and 15.6%.

Digital original category, YouTube only mix, Oct 21 2025 to Oct 21 2026 (share of YouTube digital original viewing across the six categories)
- Comedy: 28.2% share. 4,713,882 viewers on YouTube. Smosh, Game Changer and Dimension 20 (Dropout), Kill Tony, Bad Friends, Chicken Shop Date
- Lifestyle / Arts & Culture: 22.2% share. 3,842,117 viewers on YouTube. Hot Ones (2,418,963 viewers), Tiny Desk Concerts (1,612,847), Architectural Digest home tours, Vogue 73 Questions, Babish
- Drama: 15.6% share. 2,561,438 viewers on YouTube. ReelShort, DramaBox and GoodShort serialized vertical dramas
- Horror: 13.6% share. 1,994,386 viewers on YouTube. Ghost Files (Watcher, 1,072,948 viewers), The Backrooms (Kane Pixels), Local58 and the Mandela Catalogue, Mr. Nightmare
- Faith: 10.4% share. 1,614,752 viewers on YouTube. BibleProject (681,294 viewers), The Bible Recap, The Chosen companion content
- Young Adult: 9.9% share. 1,738,924 viewers on YouTube. Chicken Girls (Brat TV, 724,183 viewers), Alan's Universe, Dhar Mann Studios

- Comedy and Lifestyle / Arts & Culture together hold 50.5% of YouTube viewing across the six, so Drama reshapes the middle of the table, not its head.
- The Doors tribute channel and The Panel Show sit outside the six.
"""
SUBJECT = 'Digital original content categories on YouTube'
SIX = [('Comedy', '4,713,882', 28.2), ('Lifestyle / Arts & Culture', '3,842,117', 22.2), ('Drama', '2,561,438', 15.6),
       ('Horror', '1,994,386', 13.6), ('Faith', '1,614,752', 10.4), ('Young Adult', '1,738,924', 9.9)]

PLAN = {
    'title': 'Comedy leads YouTube digital originals at 28.2% of viewing.',
    'slides': [
        {'type': 'cover', 'eyebrow': 'YOUTUBE DIGITAL ORIGINALS  \u00b7  CATEGORY READ', 'title': 'Comedy leads YouTube digital originals at 28.2% of viewing.',
         'intro': 'Six digital original categories on YouTube, their viewers and their named titles.',
         'stats': [{'big': '10,562,169', 'label': 'US viewers, six categories'}, {'big': '4,713,882', 'label': 'Comedy viewers on YouTube'},
                   {'big': '34.6%', 'label': 'Drama viewers at four or more views'}]},
        {'type': 'tiles_facts', 'eyebrow': 'Universe', 'title': 'Six categories reach 10,562,169 US viewers on YouTube, counted one person at a time.',
         'sub': "Viewers by category, with each category's share of YouTube digital original viewing in the window.",
         'source': 'Crosswalk digital original viewing, YouTube only, Oct 21 2025 to Oct 21 2026.',
         'tiles': [{'big': '10,562,169', 'label': 'US viewers, six categories'}, {'big': '9,847,263', 'label': 'US viewers, original five categories'},
                   {'big': '2,561,438', 'label': 'Drama viewers on YouTube'}, {'big': '714,906', 'label': 'Viewers only Drama reaches'}],
         'facts': [{'label': lab, 'fig': cnt, 'note': f'{pct}% of viewing'} for lab, cnt, pct in SIX],
         'read': 'Counts are US people, each counted once in the window however many devices or sessions they used.'},
        {'type': 'bars', 'eyebrow': 'Mix', 'title': 'Comedy and Lifestyle hold half of YouTube viewing.',
         'sub': 'Share of YouTube digital original viewing across the six categories.',
         'source': 'Crosswalk digital original viewing, YouTube only, Oct 21 2025 to Oct 21 2026.', 'value_suffix': '%',
         'rows': [{'label': 'Lifestyle / Arts' if lab.startswith('Lifestyle') else lab, 'value': pct, 'accent': i == 0} for i, (lab, _c, pct) in enumerate(SIX)],
         'read': 'Comedy and Lifestyle / Arts & Culture together hold 50.5% of YouTube viewing across the six, so Drama reshapes the middle of the table.'},
        {'type': 'hero', 'ground': 'accent', 'eyebrow': 'Head', 'title': 'Two categories hold half of YouTube viewing.',
         'sub': 'Comedy and Lifestyle / Arts & Culture combined.', 'big': '50.5%',
         'line': 'Comedy takes 28.2% and Lifestyle / Arts & Culture 22.2% of viewing across the six categories.',
         'support': 'Drama, Horror, Faith and Young Adult reshape the middle of the table rather than its head.'},
        {'type': 'argument', 'ground': 'light', 'eyebrow': 'Titles', 'title': 'Named franchises sit behind each of the larger categories.',
         'sub': 'The titles counted inside each category on YouTube.',
         'cards': [{'head': 'Comedy', 'body': 'Smosh, Game Changer and Dimension 20, Kill Tony and Bad Friends sit behind 4,713,882 viewers.'},
                   {'head': 'Lifestyle / Arts & Culture', 'body': 'Hot Ones, Tiny Desk Concerts and Vogue 73 Questions make up 3,842,117 viewers.'},
                   {'head': 'Drama', 'body': 'ReelShort, DramaBox and GoodShort serialized vertical dramas carry 2,561,438 viewers.'},
                   {'head': 'Horror', 'body': 'Ghost Files, The Backrooms, Local58 and The Mandela Catalogue and Mr. Nightmare reach 1,994,386 viewers.'}]},
        {'type': 'table', 'eyebrow': 'Table', 'title': 'Every category, its viewers and its share of YouTube viewing in the window.',
         'sub': 'Viewers and share of viewing for each of the six categories.',
         'source': 'Crosswalk digital original viewing, YouTube only, Oct 21 2025 to Oct 21 2026.',
         'columns': ['Category', 'YouTube viewers', 'Share of viewing', 'Largest named title'],
         'rows': [['Comedy', '4,713,882', '28.2%', 'Smosh'], ['Lifestyle / Arts & Culture', '3,842,117', '22.2%', 'Hot Ones'],
                  ['Drama', '2,561,438', '15.6%', 'ReelShort'], ['Horror', '1,994,386', '13.6%', 'Ghost Files'],
                  ['Faith', '1,614,752', '10.4%', 'BibleProject'], ['Young Adult', '1,738,924', '9.9%', 'Chicken Girls']],
         'read': 'Drama lands third on YouTube at 2,561,438 viewers.', 'read2': 'Faith is the smallest by viewers and Young Adult by share.'},
        {'type': 'paths', 'eyebrow': 'Paths', 'title': 'Example viewing paths across the six categories.', 'sub': 'Illustrative sessions inside the YouTube slate.',
         'source': 'Crosswalk digital original viewing, YouTube only, Oct 21 2025 to Oct 21 2026.',
         'rows': [{'kind': 'search', 'url': 'youtube.com/results?search_query=hot+ones+new+episode'},
                  {'kind': 'play', 'url': 'youtube.com/watch?v=hot-ones-firstwefeast-s24e07', 'lit': True},
                  {'kind': 'search', 'url': 'youtube.com/results?search_query=dimension+20+dropout'},
                  {'kind': 'play', 'url': 'youtube.com/@dropout/dimension-20-episode-4', 'lit': True},
                  {'kind': 'play', 'url': 'youtube.com/@smosh/sketch-every-sunday', 'lit': True},
                  {'kind': 'play', 'url': 'youtube.com/@watcher/ghost-files-season-finale', 'lit': True}]},
        {'type': 'close', 'eyebrow': 'Close', 'title': 'Comedy leads, Drama adds, the middle keeps coming back.',
         'cards': [{'head': 'Scale', 'body': 'Six categories of YouTube digital originals reach US viewers numbering 10,562,169.'},
                   {'head': 'Head of the table', 'body': 'Comedy and Lifestyle / Arts & Culture hold half of all six-category viewing, at 50.5%.'},
                   {'head': 'New reach', 'body': 'Drama is incremental on YouTube, widening the audience by 714,906 viewers.'},
                   {'head': 'Return rate', 'body': 'More than a third of Drama viewers watch four or more times: 34.6%.'}]},
    ],
}

# ---------------------------------------------------------------- [1] addresses
print('[1] a page of web addresses carries only addresses the read holds')
sl = copy.deepcopy(PLAN['slides'])
notes = db.hold_paths_to_read(sl, READ)
check('plan: a page of example addresses that are in no read comes off whole',
      not any(s.get('type') == 'paths' for s in sl) and len(sl) == len(PLAN['slides']) - 1 and notes, str(notes))
held_read = READ + '\nPaths seen in the window:\n- youtube.com/@dropout/dimension-20-episode-4\n- https://www.youtube.com/@smosh/sketch-every-sunday\n' \
                   '- youtube.com/results?search_query=hot ones new episode\n- youtube.com/@watcher/ghost-files-season-finale\n'
sl = copy.deepcopy(PLAN['slides'])
notes = db.hold_paths_to_read(sl, held_read)
kept = next((s for s in sl if s.get('type') == 'paths'), None)
check('plan: addresses the read does list stay, the others come off',
      kept is not None and [r['url'] for r in kept['rows']] == [
          'youtube.com/results?search_query=hot+ones+new+episode', 'youtube.com/@dropout/dimension-20-episode-4',
          'youtube.com/@smosh/sketch-every-sunday', 'youtube.com/@watcher/ghost-files-season-finale'],
      str(kept and [r['url'] for r in kept['rows']]))
sl = [copy.deepcopy(next(s for s in PLAN['slides'] if s['type'] == 'paths'))]
sl[0]['rows'] = sl[0]['rows'][:2] + [{'kind': 'play', 'url': 'youtube.com/@smosh/sketch-every-sunday'}]
db.hold_paths_to_read(sl, held_read)
check('plan: a page left with fewer than three sourced rows comes off', sl == [], str(sl))
prompt = pma.INSIGHTS_DECK_SYSTEM_PROMPT
check('prompt: the plan is no longer asked for example addresses',
      'realistic lowercase urls' not in prompt and 'example clickstream rows' not in prompt
      and 'ONLY when the digest or the material itself lists web addresses' in prompt)
chat_src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
check('deck lane: the build is handed the read as text and the names in it',
      'pma.deck_read_text(digest, history)' in chat_src and 'pma.deck_entities(_read_text, subject)' in chat_src
      and 'protected_names=_read_names, read_text=_read_text' in chat_src
      and chat_src.count('pma.enforce_insights_plan(') == chat_src.count('names=_read_names)') and chat_src.count('names=_read_names)') >= 2)

# -------------------------------------------------------------------- [2] names
print('[2] a title, a brand or a person in the read is never flagged or rewritten')
names = pma.deck_entities(READ, SUBJECT)
for want in ('Mandela Catalogue', 'Mr. Nightmare', 'Vogue 73 Questions', 'Dimension 20', 'Game Changer', 'The Doors', 'The Panel Show',
             "Alan's Universe", 'Local58', 'YouTube', 'The Backrooms', 'Tiny Desk Concerts'):
    check(f'entities: the read names {want!r}', want in names, str(names[:60]))
check('entities: a window, a month and a country code are not names',
      not any(re.match(r'(Oct|Nov)\b', n) for n in names) and 'US' not in names, str([n for n in names if n[:3] in ('Oct', 'Nov')]))
before = 'The Panel Show and Studio 5000 lead with 40,000 viewers, ahead of Synthetic Minds.'
plain = pma._clean_deck_value('X', before)
kept = pma._clean_deck_value('X', before, None, ['The Panel Show', 'Studio 5000', 'Synthetic Minds'])
check('plan scrub: without the names a title is reworded and its number re-drawn (the defect)',
      'Panel Show' not in plain or 'Studio 5000' not in plain or 'Synthetic Minds' not in plain, plain)
check('plan scrub: with the names every title passes as written, and a count beside it is still made messy',
      'The Panel Show and Studio 5000' in kept and 'Synthetic Minds' in kept and '40,000' not in kept, kept)
clean = pma.enforce_insights_plan(copy.deepcopy(PLAN), SUBJECT, names=names)
blob = json.dumps(clean)
check('plan scrub: the plan keeps The Mandela Catalogue, Mr. Nightmare and Vogue 73 Questions',
      'The Mandela Catalogue' in blob and 'Mr. Nightmare' in blob and 'Vogue 73 Questions' in blob)

nm = ds.names_module()
check('pack: the name helper is on hand (cw_names.py, packaged with the finisher)', nm is not None and 'cw_names.py' in ds.PACKAGED_SCRIPTS)
ds.set_protected_names(['The Doors', 'Stage Door Johnny', 'Me And You And Everyone We Know', 'Mandela Catalogue'])
try:
    check('door ban: a name that carries the word stays, byte for byte',
          ds.scrub_door_metaphor('The Doors and Stage Door Johnny lead the rock set.') == 'The Doors and Stage Door Johnny lead the rock set.')
    got = ds.scrub_door_metaphor('The Doors open the door to new viewers.')
    check('door ban: the metaphor on the same line is still rewritten', 'The Doors' in got and 'door to' not in got.lower(), got)
    check('casing: a title keeps the capitals the read gave it',
          ds.fix_label_casing('Me And You And Everyone We Know') == 'Me And You And Everyone We Know')
    check('casing: a title-cased label beside it is still lowered', ds.fix_label_casing('Arts And Culture') == 'Arts and Culture')
finally:
    ds.clear_protected_names()
check('door ban: with no names in force the rule is what it was',
      'door' not in ds.scrub_door_metaphor('01 The door to new viewers').lower()
      and ds.fix_label_casing('Me And You And Everyone We Know') == 'Me and You and Everyone We Know')
cf = ds.copy_fix_module()
nm.set_names(['Mandela Catalogue', 'Mr. Nightmare'])
try:
    old = 'Local58 and The Mandela Catalogue sit beside Mr. Nightmare in a category of 1,994,386 viewers.'
    check('repair: a rewrite that respells or drops a name is refused',
          not cf.acceptable(old, 'Local58 and The Mandela Catalog sit beside Mr. Nightmare in a category of 1,994,386 viewers.')
          and not cf.sane(old, 'Local58 sits in a category of 1,994,386 viewers.')
          and cf.acceptable(old, 'Local58, The Mandela Catalogue and Mr. Nightmare share a category of 1,994,386 viewers.'))
finally:
    nm.clear()

# the lint, on a finished file
def tiny_deck(path, lines, big=None):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    y = 1.0
    for text, size in lines:
        tb = s.shapes.add_textbox(Inches(0.92), Inches(y), Inches(11.0), Inches(0.8))
        r = tb.text_frame.paragraphs[0].add_run()
        r.text, r.font.size, r.font.name = text, Pt(size), 'Crosswalk Inter'
        y += 1.0
    prs.save(path)


p_cat = os.path.join(TMP, 'catalogue.pptx')
tiny_deck(p_cat, [('Horror rests on a handful of named channels.', 34),
                  ('Ghost Files, The Backrooms, Local58 and The Mandela Catalogue reach 1,994,386 viewers.', 14)])
rc, out = run('cw_brand_lint.py', p_cat)
check('lint: "The Mandela Catalogue" is a title, not a British spelling (no names handed in)', 'British spelling' not in out, out[-400:])
p_low = os.path.join(TMP, 'catalogue_low.pptx')
tiny_deck(p_low, [('The catalogue grew this year.', 34), ('Our colour palette and the centre of the page.', 14)])
rc, out = run('cw_brand_lint.py', p_low)
check('lint: a British spelling in running copy still fails', out.count('British spelling') >= 3 and rc != 0, out[-400:])
p_brand = os.path.join(TMP, 'colour_brand.pptx')
tiny_deck(p_brand, [('Colourpop and Honour lead the beauty set.', 34), ('Shoppers of Honour and of Grey Goose skew younger.', 14)])
rc, out = run('cw_brand_lint.py', p_brand)
check('lint: a one-word brand in British spelling fails until the read names it', 'British spelling' in out, out[-300:])
n_written = ds.write_names_to_file(p_brand, ['Honour', 'Grey Goose', 'Not In This Deck'])
check('finisher: the names a deck uses are written into the file, and only those',
      n_written == 2 and sorted(ds.file_names(p_brand)) == ['Grey Goose', 'Honour'], str(ds.file_names(p_brand)))
rc, out = run('cw_brand_lint.py', p_brand)
check('lint: with the names in the file the brand passes, in a fresh process', 'British spelling' not in out, out[-300:])
Presentation(p_brand)
check('finisher: the file still opens after the names are written', True)

# ----------------------------------------------------------- the builder, whole
print('[2b] the builder, end to end: names kept, no addresses page, every check passes')
out_pptx = os.path.join(TMP, 'youtube_six.pptx')
plan_run = pma.enforce_insights_plan(copy.deepcopy(PLAN), SUBJECT, names=names)
n_slides = db.render_insights_deck(plan_run, out_pptx, static_dir=os.path.join(ROOT, 'static'),
                                   protected_names=names, read_text=pma.deck_read_text(READ, []))
fin = plan_run.get('_finish') or {}
tx = texts_of(out_pptx)
allt = '\n'.join(t for _i, t in tx)
check('builder: the deck is READY with zero lint failures and zero readability flags',
      fin.get('ready') is True and (fin.get('counts') or {}).get('lint_fail') == 0
      and (fin.get('counts') or {}).get('readability_flags') == 0, str({k: fin.get(k) for k in ('ready', 'counts', 'steps')}))
check('builder: The Mandela Catalogue is on its page, spelled as the read spells it', 'The Mandela Catalogue' in allt)
check('builder: no page of example addresses', n_slides == len(PLAN['slides']) - 1 and 'youtube.com/' not in allt and 'Illustrative' not in allt, str(n_slides))
check('builder: the finished file carries the names it uses', 'Mandela Catalogue' in ds.file_names(out_pptx), str(ds.file_names(out_pptx))[:200])
gated, rep = ds.gate_attachment('youtube_six.pptx', open(out_pptx, 'rb').read())
p_gated = os.path.join(TMP, 'youtube_six_gated.pptx')
open(p_gated, 'wb').write(gated)
check('mail gate: the same file passes again and the title is still there',
      rep.ready and 'The Mandela Catalogue' in '\n'.join(t for _i, t in texts_of(p_gated)), rep.summary)
check('mail gate: no word of the deck changes', texts_of(p_gated) == tx,
      str([(a, b) for a, b in zip(tx, texts_of(p_gated)) if a != b][:3]))
rc, out = run('cw_brand_lint.py', out_pptx)
check('lint, run on its own: 0 failure(s)', rc == 0 and '0 failure(s)' in out, out[-300:])
rc, out = run('readability_check.py', out_pptx)
check('readability, run on its own: 0 flag(s)', rc == 0 and '0 flag(s)' in out, out[-300:])
rc, out = run('cw_embed_fonts.py', out_pptx, '--verify')
check('fonts: embedded and READY', 'READY' in out, out[-200:])

# ------------------------------------------------------------------- [5] sums
print('[5] parts shown side by side add to the total printed for them')
check('builder: the total for Comedy and Lifestyle is the sum of the parts as printed (50.4%, not 50.5%)',
      '50.5%' not in allt and allt.count('50.4%') >= 3, str(re.findall(r'50\.\d%', allt)))
check('builder: the job record says what was corrected and that the check is clean',
      any('50.5% set to 50.4% (28.2% + 22.2% as printed)' in n for n in (fin.get('sum_notes') or [])) and fin.get('sum_check') == 'ok',
      str(fin.get('sum_notes')))
sl = [{'type': 'bars', 'title': 'Comedy and lifestyle take 58.1% of viewing.', 'value_suffix': '%',
       'rows': [{'label': 'Comedy', 'value': 32.6}, {'label': 'Lifestyle', 'value': 25.4}, {'label': 'Horror', 'value': 16.9},
                {'label': 'Young Adult', 'value': 14.2}, {'label': 'Faith', 'value': 10.9}],
       'read': 'Comedy holds 32.6% of viewing across the five and lifestyle 25.4%; the other three split the remaining 41.9%.'},
      {'type': 'tiles_row', 'title': 'Depth.', 'tiles': [{'big': '58.1%', 'label': 'Comedy and Lifestyle share of viewing'},
                                                          {'big': '31.3%', 'label': 'Faith, four or more views'}]},
      {'type': 'hero', 'title': 'Comedy leads.', 'line': 'Comedy takes 32.6% of the total, ahead of Lifestyle at 25.4%.'}]
notes = db.reconcile_printed_sums(sl)
check('sums: a title, a read and a tile that total two rows take the sum of those rows',
      sl[0]['title'] == 'Comedy and lifestyle take 58.0% of viewing.' and sl[1]['tiles'][0]['big'] == '58.0%', str(notes))
check('sums: "the remaining" is the sum of the rows not named', 'the remaining 42.0%' in sl[0]['read'], sl[0]['read'])
check('sums: a line that only quotes two parts is left alone, and so is a figure for something else',
      sl[2]['line'] == 'Comedy takes 32.6% of the total, ahead of Lifestyle at 25.4%.' and sl[1]['tiles'][1]['big'] == '31.3%')
check('sums: the check after the correction finds nothing', db.assert_printed_sums(sl) == [])
agree = [{'type': 'bars', 'title': 'x.', 'value_suffix': '%', 'rows': [{'label': 'Comedy', 'value': 28.2}, {'label': 'Drama', 'value': 15.6}],
          'read': 'Comedy and Drama together hold 43.8% of viewing.'}]
check('sums: a total that already agrees with its parts is not touched', db.reconcile_printed_sums(agree) == [])
check('prompt: the plan is told a total is the sum of the parts it prints',
      'Parts shown side by side add to the total printed for them' in prompt)

# ------------------------------------------------------- every row the plan has
print('[+] a list in the read is shown whole')
facts_page = [t for i, t in tx if i == 2]
check('universe page: all six categories are on the page (four of six was the defect)',
      all(any(t == lab for t in facts_page) for lab, _c, _p in SIX) and all(any(t == cnt for t in facts_page) for _l, cnt, _p in SIX),
      str(facts_page))
table_page = [t for i, t in tx if i == 6]
check('table: all six rows are on the page (the cap was five)',
      all(lab in table_page for lab, _c, _p in SIX) and 'Chicken Girls' in table_page, str(table_page))
prs = Presentation(out_pptx)
low = []
for i, s in enumerate(prs.slides, 1):
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip() and 'CROSSWALK /' not in sh.text_frame.text \
                and sh.text_frame.text.strip() != 'CONFIDENTIAL' \
                and not re.fullmatch(r'\d{1,2}', sh.text_frame.text.strip()) and (sh.top + sh.height) / 914400.0 > 6.95:
            low.append((i, round((sh.top + sh.height) / 914400.0, 2), sh.text_frame.text[:40]))
check('layout: nothing runs under the footer line on any page', not low, str(low))


def overlaps(slide):
    boxes = []
    for sh in slide.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            boxes.append((sh.left, sh.top, sh.left + sh.width, sh.top + sh.height, sh.text_frame.text[:30]))
    bad = []
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            x0, y0, x1, y1, ta = boxes[a]
            u0, v0, u1, v1, tb = boxes[b]
            if min(x1, u1) - max(x0, u0) > 9144 and min(y1, v1) - max(y0, v0) > 18288:
                bad.append((ta, tb))
    return bad


clash = [(i, overlaps(s)) for i, s in enumerate(prs.slides, 1) if i in (2, 6) and overlaps(s)]
check('layout: the six-row pages have no two text boxes overlapping', not clash, str(clash)[:400])
wordy = {'title': 'Six categories reach 10,562,169 US viewers on YouTube.', 'slides': [
    {'type': 'tiles_facts', 'eyebrow': 'Universe', 'title': 'Six categories reach 10,562,169 US viewers on YouTube.',
     'sub': 'Unduplicated audience, YouTube only.', 'source': 'Crosswalk digital original viewing, YouTube only, Oct 21 2025 to Oct 21 2026.',
     'tiles': [{'big': '10,562,169', 'label': 'US viewers, six categories'}, {'big': '9,847,263', 'label': 'US viewers, original five categories'}],
     'facts': [{'label': 'Window', 'fig': 'Oct 21 2025 to Oct 21 2026', 'note': 'YouTube only'},
               {'label': 'Base', 'fig': '10M US consumers', 'note': 'Projected to the US'},
               {'label': 'Counting', 'fig': 'People, once each', 'note': 'Not accounts or impressions'},
               {'label': 'Categories measured', 'fig': '6', 'note': 'Comedy, Lifestyle / Arts & Culture, Drama, Horror, Faith, Young Adult'}],
     'read': 'Drama brings 714,906 viewers the other five categories do not reach.'},
    {'type': 'hero', 'ground': 'accent', 'eyebrow': 'Reach', 'title': 'Drama widens the audience.', 'big': '714,906',
     'line': 'Drama viewers on YouTube who watch none of the other five categories.', 'support': 'The six-category count reads 10,562,169.'}]}
p_wordy = os.path.join(TMP, 'wordy_facts.pptx')
db.render_insights_deck(copy.deepcopy(wordy), p_wordy, static_dir=os.path.join(ROOT, 'static'))
tight = []
for sh in Presentation(p_wordy).slides[0].shapes:
    if sh.has_text_frame and sh.text_frame.text.strip() in ('Oct 21 2025 to Oct 21 2026', '10M US consumers', 'People, once each'):
        run = sh.text_frame.paragraphs[0].runs[0]
        need = db._block_h(sh.text_frame.text.strip(), run.font.size.pt, sh.width / 914400.0, bold=True)
        if need > sh.height / 914400.0 + 0.03:
            tight.append((sh.text_frame.text, round(need, 2), round(sh.height / 914400.0, 2)))
check('universe page: a fact entry that is words, not a figure, sits inside its own row (it ran into the row below)', not tight, str(tight))
check('prompt: the plan is told to restate a list whole', 'A list in the material is restated whole' in prompt and 'x3-8' in prompt)

# ---------------------------------------------------------------- [3] headers
print('[3] the plain-English check reads the headers of a deck built with cw_deck.js')
sys.path.insert(0, str(SC))
import readability_check as rc_mod  # noqa: E402


def js_like(path, pages):
    """Pages the way pptxgenjs writes them: plain text boxes, no title
    placeholder, sizes on the runs."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for boxes in pages:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        for y, runs in boxes:
            tb = s.shapes.add_textbox(Inches(0.92), Inches(y), Inches(11.4), Inches(0.9))
            para = tb.text_frame.paragraphs[0]
            for text, size in runs:
                if text == '\n':
                    para = tb.text_frame.add_paragraph()
                    continue
                r = para.add_run()
                r.text, r.font.size = text, Pt(size)
    prs.save(path)


p_js = os.path.join(TMP, 'js_like.pptx')
js_like(p_js, [
    [(0.54, [('ABBOTT ELEMENTARY  \u00b7  VIEWER PATHS', 10.5)]), (2.9, [('Abbott Elementary library x current season.', 44)]),
     (5.0, [('1.3', 52), ('\u2009million viewers', 32)]), (5.9, [('Library only', 12)])],
    [(0.54, [('04 \u2014 DEPTH', 10.5)]), (1.05, [('Library-first viewers watched 51 episodes before their first new one.', 34)]),
     (2.86, [('51', 70), ('\u2009episodes on average', 43)]), (2.86, [('2', 54), ('\u2009days later', 33)])],
    [(0.54, [('05 \u2014 COUNT', 10.5)]), (1.05, [('Mr. Nightmare leads U.S. horror viewing vs. last year.', 34)])],
    [(0.54, [('07 \u2014 CLOSE', 10.5)]), (2.6, [('The library sends more viewers forward ', 40), ('than new episodes send back.', 40)]),
     (5.2, [('238,617 forward against 96,204 back', 44)])],
    [(0.54, [('08 \u2014 TURN', 10.5)]), (1.05, [('Most viewers watched one side.', 46), ('\n', 0), ('The rest moved forward.', 46)])],
    [(0.54, [('MARA VALE  \u00b7  FERNHILL', 10.5)]), (2.6, [('Mara Vale x Fernhill', 48)]),
     (4.2, [('A look at the value Mara Vale brought to Fernhill in 51 days.', 20)])],
])
headers, _b, _p, _n = rc_mod.load_pptx(__import__('pathlib').Path(p_js))
hmap = {int(re.match(r'slide (\d+): ', h).group(1)): h.split(': ', 1)[1] for h in headers}
check('headers: a stat with a unit set larger than the title is not the header',
      hmap.get(1) == 'Abbott Elementary library x current season.'
      and hmap.get(2) == 'Library-first viewers watched 51 episodes before their first new one.', str(hmap))
check('headers: a figure line set larger than the statement is not the header',
      hmap.get(4) == 'The library sends more viewers forward than new episodes send back.', str(hmap.get(4)))
check('headers: a title, a short form and an initial do not end a sentence',
      rc_mod.clever_flags(hmap.get(3) or '') == [] and len(rc_mod.split_sentences('Mr. Nightmare leads U.S. horror viewing vs. last year.')) == 1
      and len(rc_mod.split_sentences('Michael B. Jordan leads. Ryan Coogler follows at No. 2 overall.')) == 2,
      str(rc_mod.clever_flags(hmap.get(3) or '')))
check('headers: two sentences in one header are still two sentences',
      any('2 sentences' in f for f in rc_mod.clever_flags(hmap.get(5) or '')), str(rc_mod.clever_flags(hmap.get(5) or '')))
check("headers: a cover's pairing with no full stop is still the header, not the line under it",
      hmap.get(6) == 'Mara Vale x Fernhill', str(hmap.get(6)))
check('headers: every page of the deck has one', len(hmap) == 6, str(hmap))
node = shutil.which('node')
cw_deck = SC / 'cw_deck.js'
full = ds.full_skill_root()
if node and full is not None and (full / 'scripts' / 'cw_deck.js').is_file():
    js = os.path.join(TMP, 'probe.js')
    p_real = os.path.join(TMP, 'cw_deck_probe.pptx')
    open(js, 'w').write('''
const cw = require(%s);
(async () => {
  const pres = cw.newDeck({ title: "Probe" });
  let p = cw.page(pres, { mode: "dark", eyebrow: "01 \\u2014 COVER", title: "Abbott Elementary library x current season.", titleSize: 44, titleY: 2.9 });
  cw.statRow(p, [{ value: "1.3", unit: "million viewers", label: "Library only", hero: true }, { value: "238,617", label: "Went forward" }], { y: 5.0, valueSize: 40 });
  p = cw.page(pres, { mode: "light", eyebrow: "02 \\u2014 DEPTH", title: "Library-first viewers watched 51 episodes before their first new one.", subhead: "Episodes watched before the first new episode." });
  cw.statRow(p, [{ value: "51", unit: "episodes on average", label: "Before the first new one", hero: true }], { y: p.top, valueSize: 54 });
  p = cw.page(pres, { mode: "signal", eyebrow: "03 \\u2014 CLOSE" });
  cw.text(p, ["The library sends more viewers forward ", "than new episodes send back."], { x: p.left, y: 2.6, w: 10, h: 2.2, size: 40, weight: "bold", noWidowCheck: true });
  cw.text(p, "238,617 forward against 96,204 back", { x: p.left, y: 5.2, w: 10, h: 0.8, size: 44, weight: "bold", color: "accent", noWidowCheck: true });
  await cw.save(pres, %s);
})().catch((e) => { console.error(e); process.exit(1); });
''' % (json.dumps(str(full / 'scripts' / 'cw_deck.js')), json.dumps(p_real)))
    r = subprocess.run([node, js], capture_output=True, text=True, timeout=300)
    if r.returncode == 0 and os.path.exists(p_real):
        headers, _b, _p, _n = rc_mod.load_pptx(__import__('pathlib').Path(p_real))
        got = [h.split(': ', 1)[1] for h in headers]
        check('headers: a deck written by cw_deck.js itself reads the same way',
              got == ['Abbott Elementary library x current season.',
                      'Library-first viewers watched 51 episodes before their first new one.',
                      'The library sends more viewers forward than new episodes send back.'], str(got))
    else:
        print('NOTE cw_deck.js could not build here (pptxgenjs missing?); the pptxgenjs-shaped fixture above stands in')
else:
    print('NOTE node or the full design pack is not on this machine; the pptxgenjs-shaped fixture above stands in')

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{N[0] - len(FAILS)} of {N[0]} checks passed")
if FAILS:
    print('FAILED:')
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
