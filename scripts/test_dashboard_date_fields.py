#!/usr/bin/env python3
"""Dashboard date fields: the Rankers range is held in the right order,
and no data window starts before the data floor (Jenna 2026-11-19).

The fault this holds. On Rankers a seat typed a start of 1/1/2021 and
an end of 12/31/2023. The page raised the start to the board's first
day (Jan 1, 2026) AFTER it had checked the order of the two dates, and
never held the end at all. So the start landed after the end, a range
of minus 731 days was sent, and nothing on the page said a date had
been changed.

What this suite holds:

  1. The Rankers hold (`_tiqHoldRange`): BOTH dates are brought inside
     the days the board has before the order check. Start before the
     board's first day, end before it, both, start after end, typed and
     half-typed values, a future date, the two-month cap. Whatever is
     typed, first day <= start <= end <= today and the length is 1 to
     62 days.
  2. The line a person reads when a date was moved
     (`_tiqRangeNote`): the board's real first day, never a silent
     change, nothing when nothing moved.
  3. Apply itself (`applyTrendsIQFilters`), run in node against a fake
     page: the request that leaves carries a length of one day or more,
     the fields show the dates that were used, the line is written.
  4. The server lock: a request whose range runs backwards is refused
     with a plain sentence before anything is computed.
  5. The picker floor: every date field that sets a data window gets
     the floor as its earliest day, a typed earlier day is held to the
     floor with the standing sentence, a board's own later first day is
     kept, and a field that is not a data window can opt out.
  6. One date: the page takes the floor from the server's constant
     (migration.event_window through prometheus.data_floor). The guard
     never writes the date itself, and the old form defaults are left
     exactly as they were.
  7. The refusal behind the page (Jenna 2026-11-19: "Yes, add the same
     refusal behind all four"). Four places held the date on the page
     with nothing on the server behind it: the Attribution IQ campaign
     form, the Microdramas custom range, the Attribution IQ as-of and
     asset dates, and a Rankers end date before the board's first day.
     Each one: a date before the limit, on it, after it, a range that
     crosses it, and a value that is not a date. Each route asks before
     it computes, charges, queues or stores anything.

Hermetic: no model, no live store, no mail, no app boot. The page
functions are lifted from templates/index.html and run under node; when
node is not on the machine those checks are skipped with a note
(DATE_FIELDS_NODE_REQUIRED=1 makes that a failure).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

INDEX = os.path.join(ROOT, 'templates', 'index.html')
APP = os.path.join(ROOT, 'app.py')

PASS = 0
FAIL: list[str] = []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name} {str(detail)[:400]}')


def js_function(src: str, name: str) -> str:
    """Source of `function name(` / `async function name(` in the page.
    Brace matching skips strings, template literals and comments."""
    m = re.search(r'(?:async\s+)?function\s+' + re.escape(name) + r'\s*\(', src)
    if not m:
        raise LookupError(f'function {name} not found in index.html')
    i = src.index('{', m.end())
    depth, n, j = 0, len(src), i
    while j < n:
        ch = src[j]
        nxt = src[j + 1] if j + 1 < n else ''
        if ch == '/' and nxt == '/':
            j = src.index('\n', j)
            continue
        if ch == '/' and nxt == '*':
            j = src.index('*/', j) + 2
            continue
        if ch in ('"', "'", '`'):
            q = ch
            j += 1
            while j < n and src[j] != q:
                if src[j] == '\\':
                    j += 1
                j += 1
            j += 1
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise LookupError(f'unbalanced braces while extracting {name}')


def run_node(script: str) -> dict:
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(script)
        path = fh.name
    try:
        proc = subprocess.run([shutil.which('node'), path], capture_output=True, text=True, timeout=90)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(f'node exited {proc.returncode}: {proc.stderr.strip()[:1200]}')
    return json.loads(proc.stdout.strip().splitlines()[-1])


HTML = open(INDEX, encoding='utf-8').read()
APP_SRC = open(APP, encoding='utf-8').read()

from migration import event_window as EW  # noqa: E402

FLOOR = EW.DATA_FLOOR
SENTENCE = EW.data_floor_sentence()
BOARD_FIRST = '2026-01-01'          # the board's first day in these cases
TODAY = '2026-11-19'                # "today" handed to the pure hold

# ------------------------------------------------------------------ 1, 2
RANGE_FNS = ['_tiqShiftISO', '_tiqDaysBetween', '_tiqRangeLabel', '_tiqHoldRange', '_tiqRangeNote']

RANGE_DRIVER = r"""
var FIRST = %(first)s, TODAY = %(today)s, FLOOR = %(floor)s, SENT = %(sentence)s;
function run(s, e, n, first) {
  var h = _tiqHoldRange(s, e, (first === undefined ? FIRST : first), TODAY, n);
  h.note = _tiqRangeNote(h, FLOOR, SENT);
  return h;
}
var out = {};
out.start_before   = run('2021-01-01', '2026-03-01', NaN);
out.end_before     = run('2026-02-01', '2023-12-31', NaN);
out.both_before    = run('2021-01-01', '2023-12-31', NaN);          // the seat's own dates
out.start_after    = run('2026-03-10', '2026-03-01', NaN);
out.typed_partial  = run('0002-01-01', '2026-01-05', NaN);          // a year still being typed
out.typed_garbage  = run('abc', '13/45/2026', 7);
out.typed_long     = run('20261-01-01', '2026-02-01', 3);
out.future_end     = run('2026-11-01', '2031-05-05', NaN);
out.future_both    = run('2030-01-01', '2031-05-05', NaN);
out.blank          = run('', '', 7);
out.blank_end_only = run('2026-05-01', '', NaN);
out.in_range       = run('2026-06-01', '2026-06-30', NaN);
out.capped         = run('2026-01-01', '2026-11-19', NaN);
out.preset_reaches = run('', '2026-01-05', 30);                     // a preset counted back past the first day
out.floor_only     = run('2019-05-05', '2020-01-01', NaN, FLOOR);   // only the data floor is known
out.no_limit       = run('2021-01-01', '2023-12-31', NaN, '');      // no first day at all: still never backwards
// Whatever is typed: first <= start <= end <= today, 1 to 62 days.
var days = ['', 'x', '0002-01-01', '1999-12-31', '2020-12-31', '2021-01-01', '2023-12-31', '2025-12-31', '2026-01-01',
            '2026-01-02', '2026-03-15', '2026-09-18', '2026-11-18', '2026-11-19', '2026-11-20', '2027-01-01', '9999-12-31'];
var bad = [];
for (var a = 0; a < days.length; a++) for (var b = 0; b < days.length; b++) for (var p = 0; p < 3; p++) {
  var h = _tiqHoldRange(days[a], days[b], FIRST, TODAY, [NaN, 1, 30][p]);
  var okv = (FIRST <= h.start) && (h.start <= h.end) && (h.end <= TODAY) && (h.days >= 1) && (h.days <= 62)
            && (h.days === _tiqDaysBetween(h.start, h.end) + 1);
  var typedS = /^\d{4}-\d{2}-\d{2}$/.test(days[a]), typedE = /^\d{4}-\d{2}-\d{2}$/.test(days[b]);
  var changed = (typedS && days[a] !== h.start) || (typedE && days[b] !== h.end);
  var said = !!_tiqRangeNote(h, FLOOR, SENT);
  if (!okv || (changed && !said)) bad.push([days[a], days[b], p, h.start, h.end, h.days, said]);
}
out.grid_bad = bad; out.grid_n = days.length * days.length * 3;
process.stdout.write(JSON.stringify(out));
"""

# ------------------------------------------------------------------ 3
APPLY_FNS = RANGE_FNS + ['_tiqTodayISO', '_tiqReadControls', '_tiqShowRangeNote', 'applyTrendsIQFilters']

APPLY_PRELUDE = r"""
var FLOOR = %(floor)s, SENT = %(sentence)s;
function mkEl(v) { return { value: v || '', min: '', textContent: '', innerHTML: '', disabled: false,
  style: {}, classList: { add: function () {}, remove: function () {} }, querySelector: function () { return null; } }; }
var els = {};
function reset(start, end, lookback, min) {
  els = { trendsIQStart: mkEl(start), trendsIQEnd: mkEl(end), trendsIQLookback: mkEl(lookback),
          trendsIQLens: mkEl(''), trendsIQGender: mkEl(''), trendsIQGeoType: mkEl('National'), trendsIQGeoValue: mkEl(''),
          trendsIQMeta: mkEl(''), trendsIQRangeNote: mkEl(''), trendsIQLoading: mkEl(''), trendsIQPanels: mkEl(''),
          trendsIQTabStrip: mkEl('') };
  els.trendsIQStart.min = min || ''; els.trendsIQEnd.min = min || '';
  sent = []; timers = 0; loads = 0;
}
var sent = [], timers = 0, loads = 0, reply = null;
var document = { getElementById: function (id) { return els[id] || null; } };
var window = { __trendsIQ: {}, CW_DATA_FLOOR: FLOOR, CW_DATA_FLOOR_SENTENCE: SENT };
function setTimeout() { timers++; return 0; }
function AbortController() { this.signal = {}; this.abort = function () {}; }
function fetch(url, o) {
  sent.push({ url: url, body: JSON.parse(o.body) });
  var r = reply || { success: true, cards: {}, pending_sections: [] };
  return Promise.resolve({ json: function () { return Promise.resolve(r); } });
}
function _tiqTabStates() { return {}; }
function _tiqBootStart() {} function _tiqBootFinish() {} function _tiqRepaintPanel() {}
function renderTrendsIQ() {}
var console = { error: function () {}, log: function () {} };
var _tiqLoadAvailableDates = async function () { loads++; };
"""

APPLY_DRIVER = r"""
(async function () {
  var out = {};
  function snap() {
    return { body: sent.length ? sent[sent.length - 1].body : null, n: sent.length,
             start: els.trendsIQStart.value, end: els.trendsIQEnd.value,
             note: els.trendsIQRangeNote.textContent, noteShown: els.trendsIQRangeNote.style.display,
             meta: els.trendsIQMeta.textContent, timers: timers, loads: loads };
  }
  var today = _tiqTodayISO();
  out.today = today;
  // the seat's own dates on Rankers, the board's first day known
  window.__trendsIQ = { firstDay: '2026-01-01', activeTab: null, baseData: null };
  reset('2021-01-01', '2023-12-31', 'custom', '2026-01-01'); reply = null;
  await applyTrendsIQFilters();
  out.hers = snap();
  // end before the board's first day, start inside it
  reset('2026-02-01', '2023-12-31', 'custom', '2026-01-01');
  await applyTrendsIQFilters();
  out.end_before = snap();
  // a clean range: nothing moves, an old line is cleared
  reset('2026-06-01', '2026-06-30', 'custom', '2026-01-01');
  els.trendsIQRangeNote.textContent = 'old line'; els.trendsIQRangeNote.style.display = 'block';
  await applyTrendsIQFilters();
  out.clean = snap();
  // a background reload of the same filters leaves the line alone
  reset('2026-06-01', '2026-06-30', 'custom', '2026-01-01');
  els.trendsIQRangeNote.textContent = 'This board starts on Jan 1, 2026.'; els.trendsIQRangeNote.style.display = 'block';
  await applyTrendsIQFilters({ controls: { lookback: 'custom', start: '2026-06-01', end: '2026-06-30' }, tab: null });
  out.reload = snap();
  // the list of days never landed: it is asked for once, then the data floor holds
  window.__trendsIQ = { activeTab: null, baseData: null };
  reset('2019-05-05', '2020-01-01', 'custom', '');
  await applyTrendsIQFilters();
  out.floor_only = snap();
  // a preset: live, nothing moved, no wait for the list of days
  window.__trendsIQ = { activeTab: null, baseData: null };
  reset('', '', '7', '');
  await applyTrendsIQFilters();
  out.preset = snap();
  // the server lock answers: the line is its sentence, and nothing retries
  window.__trendsIQ = { firstDay: '2026-01-01', activeTab: null, baseData: null };
  reset('2026-06-01', '2026-06-30', 'custom', '2026-01-01');
  reply = { success: false, range_refused: true, error: 'The end date is before the start date. Choose an end date on or after the start date.' };
  await applyTrendsIQFilters();
  out.refused = snap();
  process.stdout.write(JSON.stringify(out));
})().catch(function (e) { process.stderr.write(String(e && e.stack || e)); process.exit(3); });
"""

# ------------------------------------------------------------------ 5
FLOOR_FNS = ['cwIsDataDateField', 'cwDateFloorFor', 'cwApplyDateFloorMin', 'cwHeldDate', 'cwDateFloorNotePlace',
             'cwDateFloorNoteHide', 'cwDateFloorNote', 'cwHoldDateInput', 'cwDateFloorInit']

FLOOR_PRELUDE = r"""
var FLOOR = %(floor)s, SENT = %(sentence)s;
var noteEl = null, listeners = {}, winListeners = {}, timers = 0, allInputs = [];
function Event(type, o) { this.type = type; this.bubbles = !!(o && o.bubbles); }
function mkInput(o) {
  o = o || {};
  var el = { tagName: o.tagName || 'INPUT', type: o.type || 'date', value: o.value || '', min: o.min || '',
    attrs: o.attrs || {}, inside: o.inside || null, events: [],
    hasAttribute: function (a) { return Object.prototype.hasOwnProperty.call(this.attrs, a); },
    closest: function (sel) { return (sel === '[data-cw-no-floor]' && (this.attrs['data-cw-no-floor'] !== undefined || this.inside === 'no-floor')) ? {} : null; },
    dispatchEvent: function (ev) { this.events.push(ev.type); return true; },
    getBoundingClientRect: function () { return { left: 40, top: 100, bottom: 130, right: 200 }; } };
  return el;
}
var document = {
  readyState: 'complete', activeElement: null,
  body: { appendChild: function (n) { noteEl = n; } },
  getElementById: function (id) { return id === 'cwDateFloorNote' ? noteEl : null; },
  createElement: function () { return { id: '', style: { cssText: '', display: '' }, textContent: '', offsetWidth: 280, offsetHeight: 40,
                                        attrs: {}, setAttribute: function (k, v) { this.attrs[k] = v; } }; },
  querySelectorAll: function () { return allInputs; },
  addEventListener: function (t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
};
var window = { CW_DATA_FLOOR: FLOOR, CW_DATA_FLOOR_SENTENCE: SENT, innerWidth: 1200, innerHeight: 800,
  addEventListener: function (t, fn) { (winListeners[t] = winListeners[t] || []).push(fn); } };
function setTimeout() { timers++; return 1; }
function clearTimeout() {}
"""

FLOOR_DRIVER = r"""
var out = {};
function held(v) { return cwHeldDate(v, FLOOR); }
out.pure = {
  before:   held('2019-12-30'), day_before: held('2020-12-31'), on: held('2021-01-01'), after: held('2025-07-01'),
  blank:    held(''), partial: held('0002-01-01'), garbage: held('12/30/2019'), long_year: held('20211-01-01'),
  no_floor: cwHeldDate('2019-12-30', ''),
};
// the earliest day a field offers
function minAfter(o) { var el = mkInput(o); cwApplyDateFloorMin(el); return el.min; }
out.min = { none: minAfter({}), later: minAfter({ min: '2026-01-01' }), earlier: minAfter({ min: '2019-01-01' }),
            text: minAfter({ type: 'text' }), not_input: minAfter({ tagName: 'SELECT' }),
            opted_out: minAfter({ attrs: { 'data-cw-no-floor': '' } }), inside_opt_out: minAfter({ inside: 'no-floor' }) };
// a typed date is held, said, and the form hears about it
function hold(o, quiet) {
  noteEl = null; var el = mkInput(o); var moved = cwHoldDateInput(el, quiet);
  return { moved: moved, value: el.value, events: el.events, note: noteEl ? noteEl.textContent : '',
           shown: noteEl ? noteEl.style.display : '', role: noteEl ? noteEl.attrs.role : '' };
}
out.hold = {
  typed_before: hold({ value: '2019-12-30' }), typed_2020: hold({ value: '2020-06-15' }),
  on_floor: hold({ value: '2021-01-01' }), default_value: hold({ value: '2025-07-01' }), blank: hold({ value: '' }),
  quiet: hold({ value: '2019-12-30' }, true),
  own_range: hold({ value: '2019-12-30', attrs: { 'data-cw-own-range': '1' } }),
  later_min: hold({ value: '2019-12-30', min: '2026-01-01' }),
  text: hold({ value: '2019-12-30', type: 'text' }),
  opted_out: hold({ value: '2019-12-30', attrs: { 'data-cw-no-floor': '' } }),
};
// the page wiring: every field on the page gets the limit, typed values are held on leaving the field
allInputs = [mkInput({}), mkInput({ min: '2026-01-01' }), mkInput({ value: '2025-07-01' })];
cwDateFloorInit();
out.init = { mins: allInputs.map(function (e) { return e.min; }), values: allInputs.map(function (e) { return e.value; }),
             doc: Object.keys(listeners).sort(), win: Object.keys(winListeners).sort() };
var typed = mkInput({ value: '2018-03-03' });
(listeners.focusout || []).forEach(function (fn) { fn({ target: typed }); });
out.init.focusout_value = typed.value;
var late = mkInput({});                                    // a field drawn after load
(listeners.focusin || []).forEach(function (fn) { fn({ target: late }); });
out.init.focusin_min = late.min;
var entered = mkInput({ value: '2017-01-01' });
(listeners.keydown || []).forEach(function (fn) { fn({ key: 'Enter', target: entered }); });
out.init.enter_value = entered.value;
var typing = mkInput({ value: '0002-01-01' });             // mid-typing: the field still has focus
document.activeElement = typing;
(listeners.change || []).forEach(function (fn) { fn({ target: typing }); });
out.init.typing_value = typing.value;
// a blank floor leaves every field as it was
window.CW_DATA_FLOOR = ''; window.CW_DATA_FLOOR_SENTENCE = ''; window.__cwDateFloorReady = false;
var before = Object.keys(listeners).length;
cwDateFloorInit();
out.blank_floor = { min: minAfter({}), hold: hold({ value: '2019-12-30' }), new_listeners: Object.keys(listeners).length - before };
process.stdout.write(JSON.stringify(out));
"""


def js_consts():
    return {'first': json.dumps(BOARD_FIRST), 'today': json.dumps(TODAY),
            'floor': json.dumps(FLOOR), 'sentence': json.dumps(SENTENCE)}


def section_range():
    print('1. The Rankers hold: both dates, then the order')
    fns = '\n'.join(js_function(HTML, n) for n in RANGE_FNS)
    out = run_node(fns + '\n' + RANGE_DRIVER % js_consts())

    r = out['start_before']
    check('start before the board: start is its first day, end kept', (r['start'], r['end']) == (BOARD_FIRST, '2026-03-01'), r)
    check('start before the board: 60 days, start moved, end not', (r['days'], r['startMoved'], r['endMoved']) == (60, True, False), r)
    r = out['end_before']
    check('end before the board: never a backwards range', r['start'] <= r['end'] and r['days'] >= 1, r)
    check('end before the board: Jan 1 to Feb 1, 2026', (r['start'], r['end'], r['days']) == (BOARD_FIRST, '2026-02-01', 32), r)
    r = out['both_before']
    check('both before the board (1/1/2021 to 12/31/2023): one day, the first day',
          (r['start'], r['end'], r['days']) == (BOARD_FIRST, BOARD_FIRST, 1), r)
    check('both before the board: both dates moved, not a swap', (r['startMoved'], r['endMoved'], r['swapped']) == (True, True, False), r)
    r = out['start_after']
    check('start after end: the earlier date is the start', (r['start'], r['end'], r['days'], r['swapped']) == ('2026-03-01', '2026-03-10', 10, True), r)
    r = out['typed_partial']
    check('a year still being typed (0002) is held to the first day', (r['start'], r['end'], r['days']) == (BOARD_FIRST, '2026-01-05', 5), r)
    r = out['typed_garbage']
    check('values that are not dates count as not picked', (r['end'], r['days'], r['startMoved'], r['endMoved']) == (TODAY, 7, False, False), r)
    r = out['typed_long']
    check('a five-digit year counts as not picked', (r['end'], r['days']) == ('2026-02-01', 3), r)
    r = out['future_end']
    check('an end after today is held to today', (r['end'], r['lowered'], r['endMoved']) == (TODAY, True, True), r)
    r = out['future_both']
    check('both after today: today, one day', (r['start'], r['end'], r['days']) == (TODAY, TODAY, 1), r)
    r = out['blank']
    check('nothing picked: the preset ending today, nothing moved',
          (r['end'], r['days'], r['startMoved'], r['endMoved'], r['note']) == (TODAY, 7, False, False, ''), r)
    r = out['blank_end_only']
    check('no end picked: the end is today', (r['start'], r['end'], r['capped']) == ('2026-09-19', TODAY, True), r)
    r = out['in_range']
    check('a range inside the board is left exactly as typed',
          (r['start'], r['end'], r['days'], r['startMoved'], r['endMoved'], r['note']) == ('2026-06-01', '2026-06-30', 30, False, False, ''), r)
    r = out['capped']
    check('the two-month cap still holds', (r['days'], r['start'], r['end'], r['capped']) == (62, '2026-09-19', TODAY, True), r)
    r = out['preset_reaches']
    check('a preset counted back past the first day stops on it', (r['start'], r['end'], r['days'], r['raised']) == (BOARD_FIRST, '2026-01-05', 5, True), r)
    r = out['no_limit']
    check('no first day known at all: still never backwards', r['start'] <= r['end'] and r['days'] >= 1, r)
    check(f"every pair on the grid ({out['grid_n']}): inside the board, in order, 1 to 62 days, and said when moved",
          out['grid_bad'] == [], out['grid_bad'][:3])

    print('2. The line a person reads')
    check('start before the board names its real first day', out['start_before']['note'] == 'This board starts on Jan 1, 2026.',
          out['start_before']['note'])
    check('both before the board: the same one line', out['both_before']['note'] == 'This board starts on Jan 1, 2026.', out['both_before']['note'])
    check('end before the board: the first day and the switch are both said',
          out['end_before']['note'].startswith('This board starts on Jan 1, 2026.') and 'switched' in out['end_before']['note'],
          out['end_before']['note'])
    check('start after end says the dates were switched', out['start_after']['note'] == 'The start date was after the end date, so the two were switched.',
          out['start_after']['note'])
    check('a future date says the board runs through today', out['future_end']['note'] == 'This board runs through today.', out['future_end']['note'])
    check('the cap is said', out['capped']['note'] == 'This board shows up to 62 days at a time.', out['capped']['note'])
    check('only the data floor known: the standing sentence', out['floor_only']['note'] == SENTENCE, out['floor_only']['note'])
    check('only the data floor known: held to it', (out['floor_only']['start'], out['floor_only']['end']) == (FLOOR, FLOOR), out['floor_only'])
    notes = ' '.join(v['note'] for v in out.values() if isinstance(v, dict) and 'note' in v)
    check('no dash characters and no internal words in any line',
          not re.search(r'[\u2013\u2014]|snapshot|clamp|payload|lookback|backend|server|cache', notes, re.I), notes)
    check('every line is a full sentence', all(v['note'] == '' or v['note'].endswith('.') for v in out.values()
                                              if isinstance(v, dict) and 'note' in v))


def section_apply():
    print('3. Apply on the page')
    fns = '\n'.join(js_function(HTML, n) for n in APPLY_FNS)
    out = run_node(APPLY_PRELUDE % js_consts() + '\n' + fns + '\n' + APPLY_DRIVER)

    r = out['hers']
    check('1/1/2021 to 12/31/2023 sends one request', r['n'] == 1, r)
    check('the request carries a length of one day, never a negative one', r['body']['lookback_days'] == 1, r['body'])
    check('the request reads the board\'s first day', r['body']['asof'] == BOARD_FIRST, r['body'])
    check('both fields show the dates that were used', (r['start'], r['end']) == (BOARD_FIRST, BOARD_FIRST), r)
    check('the line beside the fields names the first day', r['note'] == 'This board starts on Jan 1, 2026.' and r['noteShown'] == 'block', r)
    r = out['end_before']
    check('end before the board: a positive range is sent', r['body']['lookback_days'] == 32 and r['body']['asof'] == '2026-02-01', r['body'])
    check('end before the board: fields and line updated', (r['start'], r['end']) == (BOARD_FIRST, '2026-02-01') and r['note'] != '', r)
    r = out['clean']
    check('a clean range goes out as typed', (r['body']['lookback_days'], r['body']['asof'], r['start'], r['end']) == (30, '2026-06-30', '2026-06-01', '2026-06-30'), r)
    check('a clean Apply clears an old line', r['note'] == '' and r['noteShown'] == 'none', r)
    r = out['reload']
    check('a background reload leaves the line in place', r['note'] == 'This board starts on Jan 1, 2026.' and r['noteShown'] == 'block', r)
    r = out['floor_only']
    check('first day unknown: the list of days is asked for once', r['loads'] == 1, r)
    check('first day unknown: held to the data floor, said with the standing sentence',
          (r['start'], r['end'], r['note']) == (FLOOR, FLOOR, SENTENCE) and r['body']['lookback_days'] == 1, r)
    r = out['preset']
    check('a preset stays live: 7 days ending today, no line, no wait',
          (r['body']['lookback_days'], r['body']['asof'], r['note'], r['loads']) == (7, None, '', 0), r)
    r = out['refused']
    check('a refused range shows its sentence beside the fields',
          r['note'] == 'The end date is before the start date. Choose an end date on or after the start date.' and r['noteShown'] == 'block', r)
    check('a refused range is not retried', r['timers'] == 0 and r['meta'] == '', r)

    body = js_function(HTML, 'applyTrendsIQFilters')
    check('the hold runs before the request is built', 0 < body.index('_tiqHoldRange(') < body.index('var filters = {'))
    check('the old raise-after-the-order-check is gone', 'if (minISO && startVal < minISO)' not in body)
    check('the length sent is the held length', 'lookback_days: rangeDays' in body and 'rangeDays = held.days' in body)
    check('the Rankers fields hold their own range', HTML.count('id="trendsIQStart" data-cw-own-range="1"') == 1
          and HTML.count('id="trendsIQEnd" data-cw-own-range="1"') == 1)
    check('the line has one place on the page, hidden until used',
          HTML.count('id="trendsIQRangeNote"') == 1 and bool(re.search(r'id="trendsIQRangeNote"[^>]*display: none', HTML)))
    check('the board\'s first day is remembered when the list of days lands', 'window.__trendsIQ.firstDay = oldest' in HTML)
    check('no first day is written into the page', '2026-01-01' not in body and '2026-01-01' not in js_function(HTML, '_tiqHoldRange')
          and '2026-01-01' not in js_function(HTML, '_tiqRangeNote'))


def section_server():
    print('4. The server lock')
    import trends_iq as TIQ
    check('a range of minus 731 days is refused', TIQ.range_refusal(-731) == TIQ.RANGE_REFUSAL)
    check('a range of zero days is refused', TIQ.range_refusal(0) == TIQ.RANGE_REFUSAL)
    check('text that reads as a negative number is refused', TIQ.range_refusal('-3') == TIQ.RANGE_REFUSAL)
    for ok in (1, 7, 30, 62, '14', 2.0):
        check(f'a range of {ok!r} days runs', TIQ.range_refusal(ok) == '')
    for none in (None, '', 'abc', True, [], {}):
        check(f'{none!r} names no range and runs as before', TIQ.range_refusal(none) == '')
    msg = TIQ.RANGE_REFUSAL
    check('the refusal is plain: two short sentences', msg.count('.') == 2 and len(msg.split()) <= 20, msg)
    check('the refusal carries no internal words or dashes',
          not re.search(r'[\u2013\u2014]|lookback|asof|snapshot|payload|cache|compute|negative|param|field', msg, re.I), msg)

    m = re.search(r"def api_trends_iq_data\(\):.*?(?=\n@app\.route)", APP_SRC, re.S)
    route = m.group(0) if m else ''
    check('the route is found', bool(route))
    check('the route asks before it computes',
          'range_refusal(req.get(\'lookback_days\'))' in route
          and route.index('range_refusal(') < route.index('_trends_iq.compute_view('), route[:200])
    check('a refused request answers 400 with the sentence and nothing else',
          bool(re.search(r"return jsonify\(\{'success': False, 'range_refused': True,\s*'error': _tiq_range_why\}\), 400", route)))
    check('the refusal sits behind the same access check as the board', route.index('_require_trends_iq()') < route.index('range_refusal('))


def section_floor():
    print('5. The picker floor')
    fns = '\n'.join(js_function(HTML, n) for n in FLOOR_FNS)
    out = run_node(FLOOR_PRELUDE % js_consts() + '\n' + fns + '\n' + FLOOR_DRIVER)

    p = out['pure']
    check('a typed date before the floor is held to it', p['before'] == {'value': FLOOR, 'moved': True}, p['before'])
    check('the day before the floor is held to it', p['day_before'] == {'value': FLOOR, 'moved': True}, p['day_before'])
    check('the floor itself is left alone', p['on'] == {'value': FLOOR, 'moved': False}, p['on'])
    check('a later date is left alone', p['after'] == {'value': '2025-07-01', 'moved': False}, p['after'])
    check('a blank field is left alone', p['blank'] == {'value': '', 'moved': False}, p['blank'])
    check('a year left half typed is held to the floor', p['partial'] == {'value': FLOOR, 'moved': True}, p['partial'])
    check('text that is not a date is left alone', p['garbage']['moved'] is False and p['long_year']['moved'] is False, p)
    check('no floor from the server: nothing is held', p['no_floor'] == {'value': '2019-12-30', 'moved': False}, p['no_floor'])

    m = out['min']
    check('a field with no limit gets the floor as its earliest day', m['none'] == FLOOR, m)
    check('a board with a later first day keeps it', m['later'] == '2026-01-01', m)
    check('an earlier limit is raised to the floor', m['earlier'] == FLOOR, m)
    check('fields that are not dates are not touched', (m['text'], m['not_input']) == ('', ''), m)
    check('a date that is not a data window can opt out', (m['opted_out'], m['inside_opt_out']) == ('', ''), m)

    h = out['hold']
    r = h['typed_before']
    check('typed 12/30/2019: the field now reads the floor', (r['moved'], r['value']) == (True, FLOOR), r)
    check('typed 12/30/2019: the standing sentence is shown beside it', r['note'] == SENTENCE and r['shown'] == 'block' and r['role'] == 'status', r)
    check('typed 12/30/2019: the form hears the held date', r['events'] == ['input', 'change'], r)
    check('typed 2020: held and said', (h['typed_2020']['value'], h['typed_2020']['note']) == (FLOOR, SENTENCE), h['typed_2020'])
    check('the floor itself: nothing moves, nothing is said', (h['on_floor']['moved'], h['on_floor']['note']) == (False, ''), h['on_floor'])
    check('the old form default (2025-07-01) is never touched', (h['default_value']['moved'], h['default_value']['value']) == (False, '2025-07-01'),
          h['default_value'])
    check('a blank field is never filled in', (h['blank']['moved'], h['blank']['value']) == (False, ''), h['blank'])
    check('held on the field\'s own change: no second change is sent', (h['quiet']['value'], h['quiet']['events']) == (FLOOR, []), h['quiet'])
    check('the Rankers fields are left to their own hold', (h['own_range']['moved'], h['own_range']['value']) == (False, '2019-12-30'), h['own_range'])
    check('a field with a later first day is still never left before the floor', h['later_min']['value'] == FLOOR, h['later_min'])
    check('a text field is never touched', h['text']['moved'] is False, h['text'])
    check('an opted-out field is never touched', h['opted_out']['moved'] is False, h['opted_out'])

    i = out['init']
    check('every date field on the page gets its earliest day at load', i['mins'] == [FLOOR, '2026-01-01', FLOOR], i)
    check('no value is changed at load', i['values'] == ['', '', '2025-07-01'], i)
    check('typed values are held on leaving the field, on Enter and on send',
          all(k in i['doc'] for k in ('focusin', 'focusout', 'keydown', 'change', 'submit')), i['doc'])
    check('leaving a field with an earlier date holds it', i['focusout_value'] == FLOOR, i)
    check('a field drawn after load gets the limit when it is used', i['focusin_min'] == FLOOR, i)
    check('Enter in a field with an earlier date holds it', i['enter_value'] == FLOOR, i)
    check('a date still being typed is not changed under the person', i['typing_value'] == '0002-01-01', i)

    b = out['blank_floor']
    check('a blank floor leaves every field as it was', (b['min'], b['hold']['moved'], b['hold']['value'], b['new_listeners']) == ('', False, '2019-12-30', 0), b)
    check('the sentence carries no dash characters', not re.search(r'[\u2013\u2014]', SENTENCE))


def section_one_date():
    print('6. One date, and nothing else changed')
    start = HTML.index('window.CW_DATA_FLOOR = ')
    end = HTML.index('cwDateFloorInit();', start)
    guard = HTML[start:end]
    check('the page takes the floor from the server', "window.CW_DATA_FLOOR = {{ (data_floor|default(''))|tojson }};" in guard)
    check('the page takes the sentence from the server',
          "window.CW_DATA_FLOOR_SENTENCE = {{ (data_floor_sentence|default(''))|tojson }};" in guard)
    check('the guard never writes the date or the year itself', '2021' not in guard, re.findall(r'.{30}2021.{30}', guard)[:2])
    check('the guard never writes the sentence itself', 'can be pulled' not in guard)
    check('the server hands the page the shared constant',
          bool(re.search(r"from prometheus import data_floor as _dfl_page\s+_page_data_floor = str\(_dfl_page\.FLOOR or ''\)\s+"
                         r"_page_data_floor_sentence = str\(_dfl_page\.SENTENCE or ''\)", APP_SRC)))
    check('the page is rendered with both', bool(re.search(
        r"render_template\('index\.html',\s+app_build_version=APP_BUILD_VERSION,\s+data_floor=_page_data_floor,\s+"
        r"data_floor_sentence=_page_data_floor_sentence,", APP_SRC)))
    check('the page is rendered in one place', APP_SRC.count("render_template('index.html'") == 1)
    from prometheus import data_floor as DF
    check('that constant is the one in migration.event_window', DF.FLOOR == EW.DATA_FLOOR and DF.SENTENCE == EW.data_floor_sentence())
    check('the sentence is the standing one', SENTENCE == 'No data can be pulled from before January 1, 2021.', SENTENCE)
    # the two lines as the browser gets them
    two = '\n'.join(ln.strip() for ln in guard.splitlines()
                    if ln.strip().startswith(('window.CW_DATA_FLOOR =', 'window.CW_DATA_FLOOR_SENTENCE =')))
    try:
        import jinja2
        env = jinja2.Environment()
        env.filters.setdefault('tojson', lambda v: json.dumps(v))
        got = env.from_string(two).render(data_floor=DF.FLOOR, data_floor_sentence=DF.SENTENCE)
        blank = env.from_string(two).render()
    except ImportError:
        got = blank = None
    if got is not None:
        check('what the browser gets is the date and the sentence, quoted',
              got == 'window.CW_DATA_FLOOR = ' + json.dumps(EW.DATA_FLOOR) + ';\n'
                     'window.CW_DATA_FLOOR_SENTENCE = "No data can be pulled from before January 1, 2021.";', got)
        check('with nothing from the server the page gets a blank floor',
              blank == 'window.CW_DATA_FLOOR = "";\nwindow.CW_DATA_FLOOR_SENTENCE = "";', blank)

    dates = re.findall(r'<input type="date"[^>]*>', HTML)
    check('the page still has its date fields', len(dates) >= 60, len(dates))
    check('no data window on the page opts out of the floor', not any('data-cw-no-floor' in d for d in dates))
    for fid in ('start_date', 'sample_start_date', 'behavior_start_date', 'incidence_start_date', 'customTalentTheaterStartDate',
                'customSVODCampaignStart', 'customTSTStartDate', 'sflfStartDate', 'jiqStartDate', 'iiingLaunchDate', 'talentFitStartDate',
                'flywheelStartDate', 'flywheelCompareStartDate', 'iqSotStartDate', 'sotStartDate', 'microdramasIQCustomFrom', 'iiqAsOf',
                'iiqAssetFrom', 'talentSearchStartDate', 'talentTheaterStartDate', 'netflixAllDate', 'tickerCompareDateFrom',
                'trendsStartDate', 'dailyStartDate', 'historicStartDate', 'trendsIQStart'):
        check(f'{fid} is a date field the guard covers', bool(re.search(r'<input type="date" id="' + fid + r'"', HTML)))
    check('the twin window fields are date fields the guard covers', 'aria-label="Window start"' in HTML and 'aria-label="Window end"' in HTML)

    # the old defaults are a separate surface and stay exactly as they were
    check('the old start defaults are untouched', HTML.count('value="2025-07-01"') == 7, HTML.count('value="2025-07-01"'))
    check('the old end defaults are untouched', HTML.count('value="2026-06-30"') == 7, HTML.count('value="2026-06-30"'))
    check('no field was given a written-in earliest day', not re.search(r'<input type="date"[^>]*\bmin="', HTML))

    new = guard + ''.join(js_function(HTML, n) for n in ('_tiqHoldRange', '_tiqRangeNote', '_tiqShowRangeNote'))
    check('no dash characters in the new page code', not re.search(r'[\u2013\u2014]', new))
    check('the server refusals the forms already had are still there', 'def refuse_form_window_before_data_floor' in APP_SRC)


def route_source(name: str) -> str:
    """Source of one route function in app.py, up to the next route."""
    m = re.search(r'\ndef ' + re.escape(name) + r'\(.*?(?=\n@app\.route|\n@app\.before_request|\ndef [a-zA-Z_])', APP_SRC, re.S)
    return m.group(0) if m else ''


def section_behind_the_page():
    print('7. The refusal behind the page')
    import dated_requests as DR
    import trends_iq as TIQ
    from prometheus import data_floor as DF

    START_LINE = SENTENCE + ' Choose a start date on or after January 1, 2021.'
    END_LINE = SENTENCE + ' Choose an end date on or after January 1, 2021.'
    DATE_LINE = SENTENCE + ' Choose a date on or after January 1, 2021.'
    check('the start-date line is the one the other dashboard forms use', DF.form_line() == START_LINE, DF.form_line())

    # ---- 1. the Attribution IQ campaign form: launch date, end date, phases (a range; not strict)
    # ---- 2. the Microdramas custom range (the same call)
    for label in ('campaign form', 'Microdramas range'):
        check(f'{label}: a start before the date is refused', DR.refusal(start='2019-12-30', end='2020-06-30') == START_LINE)
        check(f'{label}: the day before the date is refused', DR.refusal(start='2020-12-31') == START_LINE)
        check(f'{label}: a start on the date runs', DR.refusal(start=FLOOR, end='2021-03-01') == '')
        check(f'{label}: a start after the date runs', DR.refusal(start='2025-07-01', end='2026-06-30') == '')
        check(f'{label}: a range that crosses the date is refused with the start-date line',
              DR.refusal(start='2019-12-30', end='2021-12-14') == START_LINE)
        check(f'{label}: an end before the date is refused', DR.refusal(start='2022-01-01', end='2020-01-01') == END_LINE)
        check(f'{label}: an end before the date with no start is refused', DR.refusal(end='2020-01-01') == END_LINE)
        for bad in ('12/30/2019', 'abc', '2019-13-45', '20191230', '2019'):
            check(f'{label}: {bad!r} is not a date and is not judged', DR.refusal(start=bad, end=bad) == '')
        for none in (None, '', '   ', True, False):
            check(f'{label}: {none!r} names no date and runs', DR.refusal(start=none, end=none) == '')
    check('a date with a time after it is read by its day', DR.refusal(start='2019-12-30T00:00:00') == START_LINE
          and DR.refusal(start='2022-12-30T00:00:00') == '')

    # ---- 3. the Attribution IQ as-of date and asset From / To (strict: they go straight into the read)
    check('as-of before the date is refused', DR.refusal(on='2019-12-30', strict=True) == DATE_LINE)
    check('as-of the day before the date is refused', DR.refusal(on='2020-12-31', strict=True) == DATE_LINE)
    check('as-of on the date runs', DR.refusal(on=FLOOR, strict=True) == '')
    check('as-of after the date runs', DR.refusal(on='2026-01-30', strict=True) == '')
    check('no as-of runs', DR.refusal(on=None, strict=True) == '' and DR.refusal(on='', strict=True) == '')
    check('asset From before the date is refused', DR.refusal(start='2019-12-30', end='2020-06-30', strict=True) == START_LINE)
    check('asset From on the date runs', DR.refusal(start=FLOOR, end='2021-02-01', strict=True) == '')
    check('asset From after the date runs', DR.refusal(start='2026-01-01', end='2026-02-01', strict=True) == '')
    check('an asset range that crosses the date is refused', DR.refusal(start='2019-12-30', end='2022-01-01', strict=True) == START_LINE)
    check('asset To before the date is refused', DR.refusal(end='2020-01-01', strict=True) == END_LINE)
    for bad in ('12/30/2019', 'abc', '2026-02-30', '2026-1-5', "2026-01-01' OR 1=1 --", '2026-01-01T00:00:00', "2026-01-01')"):
        check(f'as-of {bad!r} is not a date and is refused', DR.refusal(on=bad, strict=True) == DR.UNREADABLE)
        check(f'asset From {bad!r} is not a date and is refused', DR.refusal(start=bad, end='2026-02-01', strict=True) == DR.UNREADABLE)
        check(f'asset To {bad!r} is not a date and is refused', DR.refusal(start='2026-01-01', end=bad, strict=True) == DR.UNREADABLE)
    check('the weekly one-pager (not strict): an as-of before the date is refused, text that is not a date is left alone',
          DR.refusal(on='2020-05-05') == DATE_LINE and DR.refusal(on='Jan 30, 2026') == '' and DR.refusal(on='2026-01-30') == '')

    lines = [START_LINE, END_LINE, DATE_LINE, DR.UNREADABLE]
    check('every floor line opens with the standing sentence', all(DR.is_floor_refusal(x) for x in lines[:3]) and not DR.is_floor_refusal(DR.UNREADABLE))
    check('no line carries a dash or an internal word', not re.search(
        r'[\u2013\u2014]|floor|clamp|payload|param|query|sql|server|invalid|iso|format|strict', ' '.join(lines), re.I), lines)
    check('every line is short, full sentences', all(x.endswith('.') and len(x.split()) <= 22 for x in lines), lines)
    dr_src = open(os.path.join(ROOT, 'dated_requests.py'), encoding='utf-8').read()
    check('the caller never writes the date or the sentence itself', '2021' not in dr_src and 'can be pulled' not in dr_src)
    check('the caller only reads the shared helper', 'from prometheus import data_floor' in dr_src and 'window_error(' in dr_src
          and 'form_line()' in dr_src and 'DATA_FLOOR =' not in dr_src)
    real = DR._floor
    try:
        DR._floor = lambda: (_ for _ in ()).throw(RuntimeError('down'))
        check('a fault lets the request through as before', DR.refusal(start='2019-01-01', on='abc', strict=True) == '' and DR.floor_iso() == '')
    finally:
        DR._floor = real
    check('the floor handed back with a refusal is the shared constant', DR.floor_iso() == EW.DATA_FLOOR)

    # ---- 4. Rankers: an end date before the board's first held day
    check('an end date before the board is refused with its first day',
          TIQ.first_day_refusal('2023-12-31', BOARD_FIRST) == 'This board starts on Jan 1, 2026.')
    check('the day before the first day is refused', TIQ.first_day_refusal('2025-12-31', BOARD_FIRST) == 'This board starts on Jan 1, 2026.')
    check('an end date on the first day runs', TIQ.first_day_refusal(BOARD_FIRST, BOARD_FIRST) == '')
    check('an end date after the first day runs', TIQ.first_day_refusal('2026-06-30', BOARD_FIRST) == '')
    check('a range that crosses the first day (end inside the board) is not refused here',
          TIQ.first_day_refusal('2026-01-05', BOARD_FIRST) == '' and TIQ.range_refusal(30) == '')
    for bad in ('12/31/2023', 'abc', '2026-13-45', '2023-12-31x', None, '', 20231231):
        check(f'{bad!r} names no end date and runs as before', TIQ.first_day_refusal(bad, BOARD_FIRST) == '')
    check('the line uses the real first day, whatever it is',
          TIQ.first_day_refusal('2024-01-01', '2025-03-09') == 'This board starts on Mar 9, 2025.'
          and TIQ.first_day_refusal('2024-01-01', '2024-12-25') == 'This board starts on Dec 25, 2024.')
    check('first day not known: the data floor is the limit, with the standing sentence',
          TIQ.first_day_refusal('2020-06-01', '') == SENTENCE and TIQ.first_day_refusal('2023-06-01', '') == '')
    check('the line is the page\'s own line', 'This board starts on ' in js_function(HTML, '_tiqRangeNote'))

    real_list, calls = TIQ.list_available_dates, []
    today = TIQ._today_iso()
    try:
        TIQ._first_day_cache.update(at=0.0, day='')
        TIQ.list_available_dates = lambda *a, **k: (calls.append(1) or [today, '2026-03-02', '2026-01-01', '2026-01-02'])
        check('the first day is the oldest day the board lists', TIQ.board_first_day() == '2026-01-01')
        check('the first day is kept, not read again on the next request', TIQ.board_first_day() == '2026-01-01' and len(calls) == 1)
        check('with no first day handed in, the board\'s own is used',
              TIQ.first_day_refusal('2023-12-31') == 'This board starts on Jan 1, 2026.' and TIQ.first_day_refusal('2026-02-01') == '')
        TIQ._first_day_cache.update(at=0.0, day='')
        TIQ.list_available_dates = lambda *a, **k: [today]
        check('a list holding only today is not a first day', TIQ.board_first_day() == '')
        check('so a past date is never refused on a store fault', TIQ.first_day_refusal('2026-02-01') == '')
        check('and the data floor still holds then', TIQ.first_day_refusal('2019-02-01') == SENTENCE)
        TIQ.list_available_dates = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down'))
        check('a fault reading the list refuses nothing', TIQ.board_first_day() == '' and TIQ.first_day_refusal('2026-02-01') == '')
    finally:
        TIQ.list_available_dates = real_list
        TIQ._first_day_cache.update(at=0.0, day='')
    tiq_src = open(os.path.join(ROOT, 'trends_iq.py'), encoding='utf-8').read()
    new_tiq = tiq_src[tiq_src.index('_FIRST_DAY_TTL_S = '):tiq_src.index('def get_filter_options()')]
    check('no first day and no floor date is written into the board code',
          '2026-01-01' not in new_tiq and '2021' not in new_tiq and 'can be pulled' not in new_tiq)

    # ---- the door in app.py, and each route asking before it does anything
    door = route_source('_dated_request_refused')
    check('one door for every dated route', bool(door) and 'dated_requests' in door and '_dr.refusal(' in door
          and bool(re.search(r"return jsonify\(_body\), 400", door)))
    check('the door is fail-safe', door.rstrip().endswith('return None') and 'except Exception' in door)
    try:
        import flask
        fapp = flask.Flask('date_fields_door')
        ns = {'jsonify': flask.jsonify}
        exec(door, ns)
        with fapp.test_request_context('/'):
            r1 = ns['_dated_request_refused'](start='2019-12-30', end='2021-12-14')
            r2 = ns['_dated_request_refused'](on="2026-01-01' OR 1=1", strict=True)
            r3 = ns['_dated_request_refused'](start='2021-01-01', end='2021-12-14')
            r4 = ns['_dated_request_refused'](on='2026-01-30', strict=True)
            b1, b2 = r1[0].get_json(), r2[0].get_json()
        check('a refused request answers 400 with the sentence and nothing else',
              r1[1] == 400 and b1 == {'success': False, 'error': START_LINE, 'data_floor': FLOOR}, b1)
        check('a value that is not a date answers 400 with its own plain line',
              r2[1] == 400 and b2 == {'success': False, 'error': DR.UNREADABLE}, b2)
        check('a request on or after the date is not touched', r3 is None and r4 is None)
        # the real route functions, lifted from app.py and run with stand-ins for the work they would do
        calls = []

        class Work:
            def __init__(self, name):
                self.name = name

            def __getattr__(self, attr):
                def run(*a, **k):
                    calls.append(f'{self.name}.{attr}')
                    if attr == 'range_refusal':
                        return TIQ.range_refusal(*a)
                    if attr == 'first_day_refusal':
                        return TIQ.first_day_refusal(a[0], BOARD_FIRST)
                    return {'success': True}
                return run

        import traceback as _tb
        rns = {'request': flask.request, 'jsonify': flask.jsonify, 'Response': flask.Response, 'traceback': _tb, 're': re,
               '_require_microdramas_iq': lambda: (True, None), '_require_intent_iq': lambda slug: (True, None),
               '_require_trends_iq': lambda: (True, None), '_microdramas_iq': Work('miq'), '_intent_iq': Work('iiq'),
               '_mta_iq': Work('mta'), '_trends_iq': Work('tiq')}
        for name in ('_dated_request_refused', '_intent_iq_json', 'api_microdramas_iq_data', 'api_microdramas_iq_competitors',
                     'api_microdramas_iq_all',
                     'api_intent_assets', 'api_intent_in_flight', 'api_intent_mta', 'api_trends_iq_data'):
            exec(route_source(name), rns)

        def hit(fn, path, method='GET', body=None, **kw):
            del calls[:]
            import contextlib, io
            with fapp.test_request_context(path, method=method, json=body), contextlib.redirect_stderr(io.StringIO()):
                out = rns[fn](**kw)
            resp, code = out if isinstance(out, tuple) else (out, 200)
            return code, (resp.get_json() or {}), list(calls)

        for fn in ('api_microdramas_iq_data', 'api_microdramas_iq_competitors', 'api_microdramas_iq_all'):
            code, js, did = hit(fn, '/x', 'POST', {'start_date': '2019-12-30', 'end_date': '2021-06-01'})
            check(f'{fn}: a range that reaches before the date answers 400 and computes nothing',
                  (code, js.get('error'), did) == (400, START_LINE, []), (code, js, did))
            code, js, did = hit(fn, '/x', 'POST', {'start_date': FLOOR, 'end_date': '2021-06-01'})
            check(f'{fn}: a range from the date forward runs', code == 200 and len(did) == 1 and did[0].startswith('miq.compute'), (code, did))
            code, js, did = hit(fn, '/x', 'POST', {'window_days': 7})
            check(f'{fn}: a preset with no dates runs', code == 200 and len(did) == 1, (code, did))
            code, js, did = hit(fn, '/x', 'POST', {'start_date': 'abc', 'end_date': '2021-06-01'})
            check(f'{fn}: a value that is not a date runs as it did before', code == 200 and len(did) == 1, (code, did))
        code, js, did = hit('api_intent_assets', '/x?window=custom&from=2019-01-01&to=2022-01-01', title_slug='t')
        check('asset read: From before the date answers 400 and reads nothing', (code, js.get('error'), did) == (400, START_LINE, []), (code, js, did))
        code, js, did = hit('api_intent_assets', "/x?window=custom&from=2026-01-01'%20OR%201=1", title_slug='t')
        check('asset read: a From that is not a date never reaches the read', (code, js.get('error'), did) == (400, DR.UNREADABLE, []), (code, js, did))
        code, js, did = hit('api_intent_assets', '/x?window=custom&from=2021-01-01&to=2021-03-01', title_slug='t')
        check('asset read: a range from the date forward runs', (code, did) == (200, ['iiq.get_assets']), (code, did))
        code, js, did = hit('api_intent_assets', '/x?window=all', title_slug='t')
        check('asset read: no dates runs', (code, did) == (200, ['iiq.get_assets']), (code, did))
        for fn, work in (('api_intent_in_flight', 'iiq.get_in_flight'), ('api_intent_mta', 'iiq.get_overview')):
            code, js, did = hit(fn, '/x?as_of=2020-12-31', title_slug='t')
            check(f'{fn}: as-of before the date answers 400 and reads nothing', (code, js.get('error'), did) == (400, DATE_LINE, []), (code, js, did))
            code, js, did = hit(fn, '/x?as_of=2026-01-30%27)', title_slug='t')
            check(f'{fn}: an as-of that is not a date never reaches the read', (code, js.get('error'), did) == (400, DR.UNREADABLE, []), (code, js, did))
            code, js, did = hit(fn, '/x?as_of=2021-01-01', title_slug='t')
            check(f'{fn}: as-of on the date runs', code != 400 and work in did, (code, did))
            code, js, did = hit(fn, '/x', title_slug='t')
            check(f'{fn}: no as-of runs', code != 400 and work in did, (code, did))
        code, js, did = hit('api_trends_iq_data', '/x', 'POST', {'asof': '2023-12-31', 'lookback_days': 1})
        check('board: an end date before its first day answers 400 with the line and computes nothing',
              (code, js) == (400, {'success': False, 'range_refused': True, 'error': 'This board starts on Jan 1, 2026.'})
              and 'tiq.compute_view' not in did, (code, js, did))
        code, js, did = hit('api_trends_iq_data', '/x', 'POST', {'asof': BOARD_FIRST, 'lookback_days': 1})
        check('board: an end date on its first day runs', code != 400 and 'tiq.compute_view' in did, (code, did))
        code, js, did = hit('api_trends_iq_data', '/x', 'POST', {'asof': '2026-06-30', 'lookback_days': 200})
        check('board: a range that crosses its first day runs on the days it has', code != 400 and 'tiq.compute_view' in did, (code, did))
        code, js, did = hit('api_trends_iq_data', '/x', 'POST', {'asof': '12/31/2023', 'lookback_days': 1})
        check('board: an end date that is not a date is dropped and the live board runs, as before',
              code != 400 and 'tiq.compute_view' in did and 'tiq.first_day_refusal' not in did, (code, did))
        code, js, did = hit('api_trends_iq_data', '/x', 'POST', {'lookback_days': 7})
        check('board: a live request never looks up the first day', 'tiq.first_day_refusal' not in did and 'tiq.compute_view' in did, did)
    except ImportError:
        print('  note flask is not on this machine: the door and route checks were skipped')

    def asks_first(fn, before, strict=None, gate=None):
        src = route_source(fn)
        ok = bool(src) and '_dated_request_refused(' in src
        at = src.index('_dated_request_refused(') if ok else -1
        for b in before:
            ok = ok and b in src and at < src.index(b)
        if gate:
            ok = ok and gate in src and src.index(gate) < at
        if strict is not None:
            ok = ok and (('strict=True' in src[at:at + 400]) == strict)
        return ok

    check('the campaign form asks before the funds check, the job record, the charge and the run',
          asks_first('submit_intent_ingest', ['has_credits_for(', 'jobs[job_id] = {', '_save_job_status_to_s3(', 'consume_credit(',
                                              'spawn_heavy_analysis('], strict=False, gate='user_can_run_analysis_module('))
    ing = route_source('submit_intent_ingest')
    check('the campaign form reads the launch date, the end date and every phase',
          '_dated_request_refused(start=launch_date, end=end_date)' in ing
          and "start=_iiing_ph.get('start_date'), end=_iiing_ph.get('end_date')" in ing)
    for fn, work in (('api_microdramas_iq_data', '_microdramas_iq.compute_view('),
                     ('api_microdramas_iq_competitors', '_microdramas_iq.compute_competitors_view('),
                     ('api_microdramas_iq_all', '_microdramas_iq.compute_all_platforms_view(')):
        check(f'{fn} asks before it computes', asks_first(fn, [work], strict=False, gate='_require_microdramas_iq()'))
    check('the asset read asks first, and takes only whole dates',
          asks_first('api_intent_assets', ['_intent_iq.get_assets('], strict=True, gate='_require_intent_iq(title_slug)'))
    check('the in-flight read asks first, and takes only whole dates',
          asks_first('api_intent_in_flight', ['_intent_iq.get_in_flight('], strict=True, gate='_require_intent_iq(title_slug)'))
    check('the touchpoint read asks first, and takes only whole dates',
          asks_first('api_intent_mta', ['_intent_iq.get_overview(', '_mta_iq.compute_mta_coefficients('], strict=True,
                     gate='_require_intent_iq(title_slug)'))
    check('the weekly one-pager asks before it is drawn',
          asks_first('api_intent_weekly_pdf', ['_intent_iq.get_overview(', 'build_weekly_pdf('], strict=False,
                     gate='_require_intent_iq(title_slug)'))
    exp = route_source('api_intent_export_xlsx')
    check('the Excel export asks before it is built, and takes only whole dates',
          '_dated_request_refused(on=as_of, strict=True)' in exp
          and exp.index('_dated_request_refused(') < exp.index('build_workbook(') and exp.index('_require_intent_iq(') < exp.index('_dated_request_refused('))
    tiq_route = route_source('api_trends_iq_data')
    check('the board asks about its first day before it computes',
          '_trends_iq.first_day_refusal(asof)' in tiq_route
          and tiq_route.index('first_day_refusal(') < tiq_route.index('_trends_iq.compute_view(')
          and tiq_route.index('_require_trends_iq()') < tiq_route.index('first_day_refusal('))
    check('a refused end date answers the way the page already reads (the line beside the fields, no retry)',
          bool(re.search(r"return jsonify\(\{'success': False, 'range_refused': True,\s*'error': _tiq_range_why\}\), 400", tiq_route))
          and 'data.range_refused' in js_function(HTML, 'applyTrendsIQFilters'))
    check('the first day is only looked up for a dated request', 'if not _tiq_range_why and asof:' in tiq_route)
    check('the shared helper and its constant are as they were',
          EW.DATA_FLOOR == '2021-01-01' and EW.floor_status('2019-01-01', '2022-01-01') == 'straddle'
          and EW.floor_status('2019-01-01', '2020-01-01') == 'before' and EW.floor_status('2021-01-01') == 'ok'
          and EW.floor_status('abc') == '')
    check('the form refusals the other routes already had are still there',
          DF.form_error('/api/submit', 'POST', {'start_date': '2019-01-01'}) == START_LINE
          and DF.form_error('/api/intent-ingest/submit', 'POST', {'launch_date': '2019-01-01'}) == '')


def main():
    have_node = bool(shutil.which('node'))
    if have_node:
        section_range()
        section_apply()
    section_server()
    if have_node:
        section_floor()
    section_one_date()
    section_behind_the_page()
    if not have_node:
        note = 'node is not on this machine: the page-function checks were skipped'
        if os.environ.get('DATE_FIELDS_NODE_REQUIRED') == '1':
            FAIL.append(note)
        print('  note ' + note)
    print(f'\n{PASS} passed, {len(FAIL)} failed')
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
