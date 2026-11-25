#!/usr/bin/env python3
"""
Convert the Crosswalk Product Methodologies .docx into the JSON schema
consumed by the dashboard's methodology modal
(/static/cw_methodologies.json).

Schema per section:
  { "title": str, "intro": [Paragraph, ...], "subs": [Sub, ...] }
Where:
  Paragraph = { "text": str, "is_list": bool, "runs": [{ "text": str, "bold": bool }, ...] }
  Sub       = { "title": str, "paragraphs": [Paragraph, ...] }

Sections are triggered by:
  - Heading 1 styled paragraphs
  - Normal paragraphs whose only run is bold and 17pt (215900 EMU) - matches
    'Glossary' and 'Contacts' in the July 28 doc, which are visually H1
    but not styled as H1 in Word.

Subsections are triggered by Heading 2 styled paragraphs.

The preamble (everything before the first section trigger) becomes a
_preamble_ section.

What we count as a view is written from prometheus/methodology.py on
every build (sync_view_definition), never from the Word file:
  python3 scripts/build_methodology_json.py --sync-view-definition [out.json]
rewrites only those paragraphs in the current JSON.
"""
import json
import re
import sys
from pathlib import Path

try:
    from docx.oxml.ns import qn
except ImportError:  # the view sync needs no Word reader
    qn = None

HEADING1_FONT_SIZE_EMU = 215900  # 17pt - matches both Heading 1 and Glossary/Contacts

# Jenna 2026-11-24 (view_definition = person): a view is one person,
# counted once, as printed under every answer, and the methodology page
# says the same. The words live once, in prometheus/methodology.py. Until
# this date the page said a pause and a restart were two views.
# (section title, sub title or None for the intro, term label or None for
# a bullet, the methodology constant the slot carries)
VIEW_SLOTS = (
    ('Key Metric Definitions', None, 'View', 'VIEW_DEFINITION'),
    ('How We Measure a View', 'Viewership', None, 'VIEW_DEFINITION'),
    ('SUBSCRIBER IQ', 'How These Numbers Are Counted', None, 'VIEW_NOTE_SHORT'),
)
# a bullet that defines a view (the one a slot rewrites)
VIEW_BULLET_RX = re.compile(
    r"(?i)\b(?:considered a view|counts? as (?:a|one|two|2) views?|a view is|views here are|"
    r"play over zero seconds)\b")
# what a play is rides right after what a view is, in the same two places
# (methodology.PLAY_DEFINITION; skipped when the module has none)
PLAY_SLOTS = (
    ('Key Metric Definitions', None, 'Play', 'View'),
    ('How We Measure a View', 'Viewership', None, None),
)
PLAY_BULLET_RX = re.compile(r"(?i)^\W*a play is\b")
# Jenna 2026-11-24 (rename_plays): the platform video counts on the
# short-form conversion and Micro-Series screens are plays, and the page
# names them the way the screens do. What a play is stays the Play entry
# above (methodology.PLAY_DEFINITION); these runs never restate it.
# (section title, sub title, the run to rewrite, the words it carries;
# None means play_count_labels.TOTAL_PLAYS)
PLAY_LABEL_SLOTS = (
    ('SHORT FORM CONVERSION', 'Core Metrics', re.compile(r"(?i)^\s*(?:duplicated views|total plays)\s*$"), None),
    ('SHORT FORM CONVERSION', 'Core Metrics', re.compile(r"(?i)^\s*the total volume of sf post views\b"),
     'Every play of an SF post on a given platform during the date range, repeat plays included. '),
    ('MICRODRAMAS', 'Title Cards', re.compile(r"(?i)^\W*primary metric:"),
     '\u2022 Primary metric: plays in the window you pick, on every platform.'),
    ('MICRODRAMAS', 'Title Cards', re.compile(r"(?i)^\W*(?:a mini bar strip|a trend chart):"),
     '\u2022 A trend chart: daily plays over the last 7 days, or rank over time where a platform '
     'shows no daily plays.'),
)


def _methodology():
    webapp = Path(__file__).resolve().parents[1]
    if str(webapp) not in sys.path:
        sys.path.insert(0, str(webapp))
    from prometheus import methodology
    return methodology


def _paragraph_text(p):
    return ''.join(r.get('text', '') for r in p.get('runs') or [])


def sync_view_definition(sections, methodology=None):
    """Write each VIEW_SLOTS paragraph from prometheus/methodology.py.
    Returns a list of (section, sub, before, after) for every change.
    Raises ValueError when a slot cannot be found, so a rebuilt document
    that moved a slot fails loudly instead of keeping the old words."""
    meth = methodology or _methodology()
    changes = []
    by_title = {s.get('title'): s for s in sections}
    for sec_title, sub_title, term, const in VIEW_SLOTS:
        words = getattr(meth, const)
        sec = by_title.get(sec_title)
        if sec is None:
            raise ValueError(f'view slot missing: section {sec_title!r}')
        if sub_title is None:
            paras = sec.get('intro') or []
        else:
            sub = next((s for s in sec.get('subs') or [] if s.get('title') == sub_title), None)
            if sub is None:
                raise ValueError(f'view slot missing: {sec_title!r} > {sub_title!r}')
            paras = sub.get('paragraphs') or []
        done = False
        for p in paras:
            runs = p.get('runs') or []
            if term is not None:
                if runs and runs[0].get('bold') and runs[0].get('text', '').strip() == term:
                    new_runs = [runs[0], {'text': words, 'bold': False}]
                    done = True
                else:
                    continue
            else:
                idx = next((i for i, r in enumerate(runs) if VIEW_BULLET_RX.search(r.get('text', ''))), None)
                if idx is None:
                    continue
                new_runs = list(runs)
                new_runs[idx] = {'text': '• ' + words, 'bold': bool(runs[idx].get('bold'))}
                done = True
            before = p.get('text', '')
            p['runs'] = new_runs
            p['text'] = _paragraph_text(p)
            if p['text'] != before:
                changes.append((sec_title, sub_title or term, before, p['text']))
            break
        if not done:
            raise ValueError(f'view slot missing: {sec_title!r} > {sub_title or term!r}')
    play = getattr(meth, 'PLAY_DEFINITION', None)
    if play:
        changes += _sync_play(by_title, play, getattr(meth, 'VIEW_DEFINITION'))
    changes += sync_play_labels(sections)
    return changes


def sync_play_labels(sections):
    """Write each PLAY_LABEL_SLOTS run: the short-form and Micro-Series
    entries call the platform video counts plays. A section the document
    no longer carries is skipped (there is nothing left to mislabel).
    Returns a list of (section, sub, before, after) for every change."""
    webapp = Path(__file__).resolve().parents[1]
    if str(webapp) not in sys.path:
        sys.path.insert(0, str(webapp))
    from play_count_labels import TOTAL_PLAYS
    changes = []
    by_title = {s.get('title'): s for s in sections}
    for sec_title, sub_title, rx, words in PLAY_LABEL_SLOTS:
        sub = next((s for s in (by_title.get(sec_title) or {}).get('subs') or []
                    if s.get('title') == sub_title), None)
        for p in (sub or {}).get('paragraphs') or []:
            runs = p.get('runs') or []
            idx = next((i for i, r in enumerate(runs) if rx.search(r.get('text', ''))), None)
            if idx is None:
                continue
            new = TOTAL_PLAYS if words is None else words
            if runs[idx].get('text') != new:
                before = p.get('text', '')
                runs[idx] = {'text': new, 'bold': bool(runs[idx].get('bold'))}
                p['text'] = _paragraph_text(p)
                changes.append((sec_title, sub_title, before, p['text']))
            break
    return changes


def _sync_play(by_title, play, view_words):
    """What a play is, right after what a view is: a 'Play' term after the
    'View' term in Key Metric Definitions, and a bullet after the view
    bullet in How We Measure a View. Rewritten when present, added when not."""
    changes = []
    for sec_title, sub_title, term, after_term in PLAY_SLOTS:
        sec = by_title.get(sec_title) or {}
        if sub_title is None:
            paras = sec.setdefault('intro', [])
            first = [(p.get('runs') or [{}])[0] for p in paras]
            at = next((i for i, r in enumerate(first) if r.get('bold') and r.get('text', '').strip() == term), None)
            new = {'text': term + play, 'is_list': False, 'runs': [{'text': term, 'bold': True}, {'text': play, 'bold': False}]}
            if at is None:
                anchor = next((i for i, r in enumerate(first) if r.get('bold') and r.get('text', '').strip() == after_term), None)
                if anchor is None:
                    raise ValueError(f'play slot missing: {sec_title!r} > {after_term!r}')
                paras.insert(anchor + 1, new)
                changes.append((sec_title, term, '', new['text']))
            elif paras[at].get('text') != new['text'] or paras[at].get('runs') != new['runs']:
                before = paras[at].get('text', '')
                paras[at] = new
                changes.append((sec_title, term, before, new['text']))
            continue
        sub = next((s for s in sec.get('subs') or [] if s.get('title') == sub_title), None)
        para = next((p for p in (sub or {}).get('paragraphs') or []
                     if ('• ' + view_words) in [r.get('text') for r in p.get('runs') or []]), None)
        if para is None:
            raise ValueError(f'play slot missing: {sec_title!r} > {sub_title!r}')
        runs = para['runs']
        bullet = {'text': '• ' + play, 'bold': False}
        at = next((i for i, r in enumerate(runs) if PLAY_BULLET_RX.search(r.get('text', ''))), None)
        before = para.get('text', '')
        if at is None:
            vi = [r.get('text') for r in runs].index('• ' + view_words)
            runs.insert(vi + 1, bullet)
        else:
            runs[at] = bullet
        para['text'] = _paragraph_text(para)
        if para['text'] != before:
            changes.append((sec_title, sub_title + ' (play)', before, para['text']))
    return changes


def _iter_runs_including_hyperlinks(paragraph):
    """Yield (text, bold) for every visible run in a paragraph.

    python-docx's Paragraph.runs SKIPS runs nested inside <w:hyperlink>
    elements, so email links in Contacts / any linked text get silently
    dropped. Walk the XML directly to catch both plain <w:r> children and
    <w:r> children of <w:hyperlink>.
    """
    p_el = paragraph._p
    # Iterate direct children so we preserve document order across mixed
    # <w:r> and <w:hyperlink><w:r/></w:hyperlink> siblings.
    for child in p_el.iterchildren():
        tag = child.tag
        if tag == qn('w:r'):
            yield from _extract_from_run_element(child)
        elif tag == qn('w:hyperlink'):
            for r_el in child.iterchildren(qn('w:r')):
                yield from _extract_from_run_element(r_el)


def _extract_from_run_element(r_el):
    """Yield (text, bold) for each text node inside a raw <w:r> element."""
    # Bold detection: <w:rPr><w:b/></w:rPr> (or <w:b w:val="1"/>).
    bold = False
    rpr = r_el.find(qn('w:rPr'))
    if rpr is not None:
        b_el = rpr.find(qn('w:b'))
        if b_el is not None:
            val = b_el.get(qn('w:val'))
            bold = val in (None, '1', 'true')
    # <w:t>text</w:t> holds the actual visible text (there may be multiple).
    for t_el in r_el.iter(qn('w:t')):
        text = t_el.text or ''
        if text:
            yield text, bold

def _para_is_section(p):
    """Return True if this paragraph should start a new top-level section."""
    style = p.style.name if p.style else ''
    if 'Heading 1' in style:
        return True
    if style == 'Normal':
        # Use python-docx runs here (need font.size, which isn't cheap to grab
        # from raw XML). Section-title paragraphs never contain hyperlinks
        # so it's safe to skip the hyperlink walker for this check.
        runs = p.runs
        if runs and all((r.bold and r.font.size == HEADING1_FONT_SIZE_EMU) for r in runs if r.text.strip()):
            txt = p.text.strip()
            # Only promote short single-word/phrase titles; avoid promoting
            # long bold paragraphs that happen to use the same size.
            if txt and len(txt) <= 40:
                return True
    return False


def _para_is_subsection(p):
    style = p.style.name if p.style else ''
    return 'Heading 2' in style


def _para_is_list(p):
    style = p.style.name if p.style else ''
    return 'List Paragraph' in style


def _paragraph_to_obj(p):
    """Extract a docx paragraph to the schema dict.

    Concatenates run text WITHOUT separators (matches existing JSON format).
    Newlines inside runs are stripped so the definition-splitter on the client
    keeps working ('Term' bold run then 'Body' non-bold run become a defn).
    """
    text_parts = []
    runs = []
    for raw, bold in _iter_runs_including_hyperlinks(p):
        # Strip leading/trailing whitespace-newlines that arise from soft
        # line breaks; keep internal spaces intact.
        cleaned = raw.replace('\n', '').replace('\r', '')
        if not cleaned:
            continue
        runs.append({'text': cleaned, 'bold': bool(bold)})
        text_parts.append(cleaned)

    # Handle bullet-list paragraphs: prefix the text with a bullet char
    # so the client renderer recognizes it as a list item.
    is_list = _para_is_list(p)
    if is_list:
        joined = ''.join(text_parts).strip()
        if joined and not joined.startswith(('•', '·', '\u2022')):
            joined = '• ' + joined
            # Rewrite the first non-empty run to include the bullet
            if runs:
                runs[0] = {'text': '• ' + runs[0]['text'].lstrip(), 'bold': runs[0]['bold']}
        text = joined
    else:
        text = ''.join(text_parts)

    return {
        'text': text,
        'is_list': is_list,
        'runs': runs,
    }


def convert(docx_path, output_path=None):
    from docx import Document
    doc = Document(docx_path)
    sections = []
    current_section = None
    current_sub = None

    def _new_section(title):
        nonlocal current_section, current_sub
        current_section = {'title': title, 'intro': [], 'subs': []}
        current_sub = None
        sections.append(current_section)

    def _new_sub(title):
        nonlocal current_sub
        current_sub = {'title': title, 'paragraphs': []}
        current_section['subs'].append(current_sub)

    def _add_paragraph(p):
        obj = _paragraph_to_obj(p)
        if not obj['text'].strip() and not obj['runs']:
            return
        target = current_sub['paragraphs'] if current_sub else current_section['intro']
        target.append(obj)

    # Seed the preamble section (everything before first Heading 1).
    _new_section('_preamble_')

    # Skip the doc-title footer paragraph so it doesn't leak into JSON.
    SKIP_TEXTS = {'Version 3.1 · Updated July 28, 2026'}

    for p in doc.paragraphs:
        t = p.text.strip()

        if _para_is_section(p):
            _new_section(t)
            continue

        if _para_is_subsection(p):
            _new_sub(t)
            continue

        # Skip version-footer line entirely
        if t in SKIP_TEXTS:
            continue

        _add_paragraph(p)

    sync_view_definition(sections)

    if output_path:
        Path(output_path).write_text(
            json.dumps(sections, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
    return sections


DEFAULT_OUT = 'bg-webapp/static/cw_methodologies.json'


def _existing_titles(path):
    try:
        return [s.get('title') for s in json.loads(Path(path).read_text(encoding='utf-8'))]
    except Exception:
        return []


def sync_file(path):
    """Rewrite the view slots of an existing JSON in place. Returns the
    changes (empty when the file already carries methodology.py's words)."""
    p = Path(path)
    sections = json.loads(p.read_text(encoding='utf-8'))
    changes = sync_view_definition(sections)
    if changes:
        p.write_text(json.dumps(sections, indent=2, ensure_ascii=False), encoding='utf-8')
    return changes


if __name__ == '__main__':
    if '--sync-view-definition' in sys.argv:
        rest = [a for a in sys.argv[1:] if not a.startswith('--')]
        target = rest[0] if rest else DEFAULT_OUT
        changed = sync_file(target)
        print(f'{target}: {len(changed)} paragraph(s) rewritten (view and play wording)')
        for sec, sub, before, after in changed:
            print(f'  - {sec} > {sub}')
        sys.exit(0)

    # The source document must be named. This used to default to one
    # person's Downloads folder, so a bare run regenerated the whole
    # methodology from whatever vintage of the Word file happened to be
    # sitting there (Jenna 2026-11-05, after the file was lost for eight
    # days to an unrelated commit).
    if len(sys.argv) < 2:
        print(__doc__.strip())
        print('\nUsage: build_methodology_json.py <source.docx> [out.json]')
        print('\nName the .docx explicitly. There is no default: sections added')
        print('straight to the JSON since the last Word export would be lost')
        print('silently if this regenerated from a stale document.')
        sys.exit(2)

    docx = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT

    before = _existing_titles(out)
    sections = convert(docx, None)
    after = [s.get('title') for s in sections]
    dropped = [t for t in before if t and t not in after]

    # Naming the file is not enough on its own: pointing this at the old
    # export is the likeliest way to regenerate, and that is exactly the
    # run that quietly deletes the newer sections.
    if dropped and '--allow-drop' not in sys.argv:
        print('Refusing to write %s.' % out)
        print('\n%d section(s) in the current file are absent from %s:'
              % (len(dropped), docx))
        for t in dropped:
            print('  - %s' % t)
        print('\nFold them into the source document first, or pass --allow-drop')
        print('if losing them is genuinely what you want.')
        sys.exit(1)

    Path(out).write_text(
        json.dumps(sections, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    print(f'Wrote {out} with {len(sections)} sections:')
    for s in sections:
        subs = len(s.get('subs') or [])
        intro = len(s.get('intro') or [])
        print(f'  - {s["title"]!r}  (intro={intro}, subs={subs})')
