#!/usr/bin/env python3
"""A valuation paid for in chat hands over the one valuation deck
(Jenna 2026-11-11, Samantha Kolton at CAA).

A valuation ordered and paid in chat ended on a line pointing at the
Brand Partnership tab, and a deck asked for in the thread afterwards
was laid out by the insights deck builder from the chat's words. There
is one valuation deck in the product now (migration/bpiq_deck_builder),
whichever way it was ordered: the paid chat order, a deck asked for in
the thread after it, the tab's export.

This replays the paid order end to end as a wallet seat shaped like
hers: the wizard, the real wallet arithmetic, the engine on the saved
research and the valuation of record, the job's own steps (the deck
built and stored before the job says done, the thread note, the ready
email), with an in-memory bucket. Nothing reaches the network, the app
or any real account, and nothing is mailed.

  A. the $500 order: one charge, the review deck's figures on the pages,
     14 pages, no comparison-group page, the hand-over line and link
  B. a deck asked for in the thread afterwards is that deck again
  C. the tab's export is the same deck
  D. the $1,000 order end to end: one charge, the total on the held
     figure, the comparison-group pages present and adding up
  E. the wiring in the job body, the widget and the email

Run: python3 scripts/test_pm_valuation_paid_deck.py
Set PM_VALUATION_DECK_OUT=<dir> to keep the decks it builds.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ['CORPUS_CATALOG'] = '0'
sys.modules.setdefault('app', None)          # the dashboard app is never imported here

import test_pm_valuation_charge_path as CP  # noqa: E402  (the wallet seat harness)
import test_pm_valuation_matches_held_figure as HF  # noqa: E402  (the held-figure replay)
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import bpiq_job as BJ  # noqa: E402
from prometheus import bpiq_deck as BD  # noqa: E402
from prometheus import delivered as DL  # noqa: E402
from prometheus import partnership_value as PV  # noqa: E402
from migration import bpiq_anchors as BA  # noqa: E402
from migration import bpiq_deck_builder as B  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402

FAIL = 0
N = 0
FX = HF.FX
FIGURE = HF.FIGURE
KEY = HF.KEY
BUCKET = 'dashboard-inputs'
TODAY = date(2026, 11, 12)
OUT = os.environ.get('PM_VALUATION_DECK_OUT') or ''

# the review deck's figures, as the pages print them
REVIEW_FIGURES = (
    '$4,217,683.00',     # total brand value
    '$3,215,733.50',     # earned media
    '$491,135.00',       # brand engagement
    '$434,805.00',       # brand lift
    '$76,009.50',        # conversion
    '525',               # orders
    '$144.78',           # per order
    '$542,815.80',       # JYT's share
    '319,506',           # the campaign group
    '34.3%', '61.5%',    # engagement before and after
    '86,961',            # new engagers
)
CONTROL_WORDS_RX = re.compile(r"\b(?:matched\s+group|comparison\s+group|control\s+group|holdout|unexposed|"
                              r"similar\s+people\s+who|not\s+exposed|no\s+exposure)\b", re.I)
BANNED_RX = re.compile(
    r"\b(?:synth\w*|modell?ed|estimat\w+|pipeline|panel[- ]projected|reasoned|researched|anchored|calibrat\w+|derived|"
    r"hostmap|enforcer|claude|anthropic|llm|ai[- ]generated|placeholder|draft|confidence|margin\s+of\s+error|"
    r"p[- ]value|z[- ]score|significan\w+|interval|nielsen|comscore|circana|statista|emarketer|pew|sensor\s+tower|"
    r"similarweb|launchmetrics|doors?)\b", re.I)


def check(label, cond):
    global FAIL, N
    N += 1
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def cents(v):
    return int(round(float(v) * 100))


# ------------------------------------------------------------ the stand-ins
class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.signed = []

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise KeyError(Key)
        return {'Body': _Body(self.objects[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get('IfNoneMatch') and Key in self.objects:
            raise RuntimeError('PreconditionFailed 412')
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        return {}

    def delete_object(self, Bucket, Key, **kw):
        self.objects.pop(Key, None)
        return {}

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in self.objects if k.startswith(Prefix)]}

    def generate_presigned_url(self, op, Params=None, ExpiresIn=0, **kw):
        self.signed.append((Params or {}).get('Key'))
        return f"https://files.example.test/{(Params or {}).get('Key')}?sig=1&exp={ExpiresIn}"

    def keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))


class Host(CP.Host):
    """The charge door plus the bucket and the valuation sidecar."""
    BPIQ_METADATA_KEY = 'system/brand_partnership_iq_metadata.json'
    S3_BUCKET = BUCKET

    def __init__(self, ledger, s3):
        super().__init__(ledger)
        self.s3_client = s3

    def has(self, name):
        return name in ('s3_client', 'S3_BUCKET', 'load_bpiq_metadata') or super().has(name)

    def load_bpiq_metadata(self):
        try:
            return json.loads(self.s3_client.get_object(Bucket=BUCKET, Key=self.BPIQ_METADATA_KEY)['Body'].read())
        except Exception:
            return {}


class Threads:
    """The thread store the job's note lands in (the chat module's five
    thread helpers, and nothing else of it)."""

    def __init__(self):
        self.hist = {}
        self.job_thread = {}

    def _pm_thread_for_job(self, username, job_id):
        return self.job_thread.get(job_id)

    def _pm_load_thread_or_active(self, username, tid):
        return self.hist.setdefault((username, tid), [])

    def _pm_save_thread_or_active(self, username, tid, history):
        self.hist[(username, tid)] = history

    @staticmethod
    def _pm_history_has_job_turn(history, key, job_id):
        return any((t.get('meta') or {}).get(key) == job_id for t in history or [])

    @staticmethod
    def _pm_iso_now():
        return '2026-11-12T10:00:00Z'


class Job:
    """The valuation job, step for step as prometheus.legacy.chat
    `_pm_run_bpiq_job` runs it (section E holds the two to each other):
    prepare, the engine, the file, the sidecar, the deck handed over
    with the finished status, settle, the delivered contract."""

    def __init__(self, host, store, threads, model=HF.no_model):
        self.host, self.store, self.threads, self.model = host, store, threads, model
        self.status = {}
        self.sends = []
        self.runs = []
        self.log = ''

    def status_write(self, job_id, doc):
        self.status[job_id] = dict(doc)
        self.host.s3_client.put_object(Bucket=BUCKET, Key=f"system/pm_bpiq_jobs/{job_id}.json",
                                       Body=json.dumps(doc, default=str).encode('utf-8'))

    def send(self, **kw):
        self.sends.append(kw)
        return {'sent': True, 'to': kw.get('to'), 'subject': kw.get('subject')}

    def run(self, job_id, uname, inputs, extras):
        s3 = self.host.s3_client
        self.threads.job_thread[job_id] = (extras or {}).get('thread_id')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            inputs, reg, settled = BJ.prepare(job_id, uname, inputs, extras, self.host, self.status_write)
            if settled:
                self.log += buf.getvalue()
                return
            self.status_write(job_id, {'job_id': job_id, 'user': uname, 'status': 'running',
                                       'subject': f"{inputs['qualifier']} x {inputs['brand_partner']}"})
            payload = S.synthesize(dict(inputs), self.model, created_by=uname, underpinnings=self.store, job_id=job_id)
            category = payload.pop('_bpiq_category', None)
            out_key = S.s3_key_for(inputs)
            s3.put_object(Bucket=BUCKET, Key=out_key, Body=json.dumps(payload, indent=2).encode('utf-8'))
            bare_key = out_key.replace('brand-partnership-iq/', '')
            meta = self.host.load_bpiq_metadata()
            meta[bare_key] = {'display_name': payload.get('project_name') or bare_key, 'category': (category or 'ENTERTAINMENT')}
            s3.put_object(Bucket=BUCKET, Key=self.host.BPIQ_METADATA_KEY, Body=json.dumps(meta).encode('utf-8'))
            self.status_write(job_id, {
                'job_id': job_id, 'user': uname, 'status': 'done', 'subject': payload.get('project_name'),
                's3_key': bare_key, 'total_brand_value': (payload.get('valuation') or {}).get('total_brand_value'),
                **BJ.hand_over(job_id, uname, inputs, bare_key, payload, category, self.host)})
            BJ.settle(reg, inputs, job_id, uname, bare_key, payload)
            tbv = (payload.get('valuation') or {}).get('total_brand_value')
            lead = (W.email_copy({'talent': inputs.get('qualifier'), 'campaign': inputs.get('campaign') or inputs.get('brand_partner'),
                                  'qualifier': inputs.get('qualifier'), 'brand_partner': inputs.get('brand_partner')})[1]
                    .split('\n\n') or [''])[0]
            rec = DL.on_done('bpiq', username=uname, subject=str(payload.get('project_name') or ''), s3_key=bare_key,
                             payload=payload, host=self.host, s3_client=s3, job_id=job_id, files=BJ.deck_files(job_id),
                             ready_message=lead, intro=lead,
                             figures=[{'name': 'Total brand value', 'value': tbv, 'unit': 'usd',
                                       'definition': 'Brand Partnership IQ valuation'}],
                             facts=[('Total brand value', f"${float(tbv):,.2f}")], send=self.send)
        self.log += buf.getvalue()
        self.runs.append({'job_id': job_id, 'inputs': dict(inputs), 'payload': payload, 'key': S.run_key(inputs),
                          'bare_key': bare_key, 'category': category, 'delivered': rec})


class Order:
    """One seat, one thread, one bucket."""

    def __init__(self, record=True, model=HF.no_model):
        self.led = CP.Ledger(HF.seat_doc())
        HF.install(users=self.led.data)
        self.s3 = FakeS3()
        self.host = Host(self.led, self.s3)
        self.eng = HF.Engine(record=record)
        self.threads = Threads()
        self.job = Job(self.host, self.eng.store, self.threads, model=model)
        W.UNDERPINNINGS = self.eng.store
        BJ._chat = lambda: self.threads
        BJ._JOBS.clear()
        DL._persist = lambda *a, **k: True          # the shared ledger is another system
        self.walk = HF.Walk(self.led, TODAY, self.job)
        self.walk.host = self.host

    def place(self, naming='JYT is the talent and Gap is the brand', dates='9/22/26 to today', control='no'):
        recap = None
        for m in (W.CHIP, naming, dates, 'Accept', control, 'find the campaign asset'):
            recap = self.walk.say(m)
        price = '$1,000' if control == 'yes' else '$500'
        accepted = self.walk.say(f'Accept {price}')
        return recap, accepted

    def thread(self):
        return self.threads.hist.get(('wallet_seat', self.walk.tid)) or []

    def charges(self):
        return [abs(float(t['amount_usd'])) for t in self.led.deducts()]


def slide_texts(pptx_bytes):
    """[text of page 1, text of page 2, ...] as python-pptx reads them."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(pptx_bytes))
    pages = []
    for s in prs.slides:
        bits = []
        for sh in s.shapes:
            if sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    line = ''.join(r.text for r in para.runs).strip()
                    if line:
                        bits.append(line)
            if getattr(sh, 'has_table', False) and sh.has_table:
                for row in sh.table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            bits.append(cell.text.strip())
        pages.append('\n'.join(bits))
    return pages


def fonts_embedded(pptx_bytes):
    import zipfile
    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as z:
        return sum(1 for n in z.namelist() if n.startswith('ppt/fonts/'))


def keep(name, fname, body):
    if not OUT:
        return
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), 'wb') as fh:
        fh.write(body)


# ------------------------------------------------------------------ A
def test_a_the_500_order():
    print("A. the $500 order, paid in chat as a wallet seat shaped like hers, against the valuation of record")
    o = Order(record=True)
    recap, accepted = o.place()
    check("the quote is $500 and Accept starts one job", recap is not None and 'This is $500.' in recap['reply']
          and accepted is not None and accepted['reply'] == W.RUNNING_REPLY and len(o.job.runs) == 1)
    check("one $500 charge, nothing else", o.charges() == [500.0] and o.led.balance() == 3712.37 and not o.led.refunds)
    run = o.job.runs[0]
    p = run['payload']
    check("the valuation is the review deck's, component for component", HF.same_as_review(p)
          and cents(p['valuation']['total_brand_value']) == cents(FIGURE))
    check("the saved file carries none of the engine's working keys",
          not any(k in p for k in ('_bpiq_category', '_category_for_deck', 'delivered_read')))
    st = o.job.status[run['job_id']]
    decks = o.s3.keys('generated_decks/')
    check("the deck is stored under the job before the job says done", st.get('status') == 'done'
          and decks == [f"generated_decks/{run['job_id']}/{st['deck']['filename']}"] and st['deck']['s3_key'] == decks[0])
    body = o.s3.objects[decks[0]]
    run['deck_key'] = decks[0]
    keep('paid_500', st['deck']['filename'], body)
    pages = slide_texts(body)
    text = '\n'.join(pages)
    check("14 pages", len(pages) == 14 and st['deck']['slides'] == 14)
    for fig in REVIEW_FIGURES:
        check(f"the deck prints {fig}", fig in text)
    check("the total is on the cover, the short version and the total page", sum('$4,217,683.00' in pg for pg in pages) >= 3)
    check("the reach line names the talent's own audience beside the people reached", '2,184,637' in text and '319,506' in pages[3])
    check("no comparison-group page or line on a $500 order", not CONTROL_WORDS_RX.search(text))
    hits = sorted({m.group(0).lower() for m in BANNED_RX.finditer(text)})
    check(f"no DRAFT stamp, no method word, no outside name, no confidence language {hits or ''}", not hits)
    check("no page failed to draw", B.PAGE_FAILED_LINE not in text)
    # (income brackets on the demographics page are labels, not valuation dollars)
    money_lines = '\n'.join(ln for ln in text.split('\n') if not re.search(r"^\s*(?:Under\s+|Less than\s+)?\$[\d,]+(?:\s+(?:to|or)\s+|\+|$)", ln))
    bare = [m.group(0) for m in re.finditer(r"\$\d{1,3}(?:,\d{3})+(?:\.\d+)?", money_lines) if not re.search(r"\.\d\d$", m.group(0))]
    check(f"every valuation dollar prints exact, with cents {bare or ''}", not bare)
    check("the finisher says READY and Crosswalk Inter is embedded", st['deck'].get('ready') is True and fonts_embedded(body) >= 4)
    # the hand-over line in the thread
    notes = [t for t in o.thread() if (t.get('meta') or {}).get('bpiq_job_id') == run['job_id']]
    want = ("Your Brand Partnership valuation is ready: JYT x Gap, total brand value $4,217,683.00. "
            "The deck is below, and the link is good for 7 days.\nYou were charged $500 once for this valuation.")
    check("the thread gets one line with the total and the deck, and the charge line", len(notes) == 1 and notes[0]['text'] == want)
    link = (notes[0]['meta'] if notes else {}).get('link') or {}
    check("the line carries the deck's link", link.get('label') == 'Download the deck'
          and str(link.get('url') or '').startswith('https://') and decks[0] in str(link.get('url')))
    check("the widget's poll gets the same line and link", st.get('note') == want and (st.get('link') or {}).get('url') == link.get('url'))
    check("the line says nothing about how it was made", not BANNED_RX.search(want.replace('ready', '')))
    # the ready email: one note, the deck and the raw data attached, nothing real sent
    sends = o.job.sends
    names = [f[1] for f in (sends[0].get('files') or [])] if sends else []
    check("one ready note to the seat, through the stand-in, with the deck attached", len(sends) == 1
          and sends[0].get('to') == 'seat@example.test' and st['deck']['filename'] in names
          and any(n.endswith('.csv') for n in names))
    check("the attached deck is the stored deck", bool(sends) and any(f[0] == body for f in sends[0]['files']))
    check("the note says the deck is attached", bool(sends) and 'The valuation deck and the raw data are attached.' in sends[0].get('body', ''))
    check("the note names the pairing once", bool(sends) and 'valuation is ready.' in sends[0].get('body', '')
          and 'valuation with JYT' not in sends[0].get('body', '')
          and W.email_copy({'talent': 'Jennifer Beals', 'campaign': 'Ganni x Lululemon'})[1].startswith(
              'The Ganni x Lululemon valuation with Jennifer Beals is ready.'))
    check("the note is clean", bool(sends) and not BANNED_RX.search(sends[0].get('body', '').replace('ready', ''))
          and '\u2014' not in sends[0].get('body', ''))
    # a second Accept: nothing more
    o.walk.say('Accept $500')
    check("a second Accept charges nothing and builds nothing", o.charges() == [500.0] and len(o.job.runs) == 1
          and o.s3.keys('generated_decks/') == decks and len(sends) == 1)
    W.UNDERPINNINGS = None
    return o, run, body, pages


# ------------------------------------------------------------------ A2
HER_WORDS = 'JYT is the talent and GAP is the brand. the partnership started in September 2026'
HER_READ = ("The Gap x JYT partnership carries $4,217,683 of value in its first 51 days, built on 2,184,637 US people reached "
            "and 137,633 of them already confirmed Gap buyers.\n\nPartnership value, Sep 22 to Nov 11 2026: 4,217,683, "
            "Partnership value, first 12 months: 14,839,261, JYT projected US audience: 2,184,637.\n\n- Overlap is already real "
            "rather than aspirational: 137,633 of the people JYT reaches have a confirmed Gap order in the window at index 153.\n"
            "- Recruitment is the smaller but more durable half: 15,147 first-time Gap buyers in 51 days.")


def test_a2_her_own_path(first_pages):
    print("A2. her own path: a deck asked of the read she holds opens the order, on any later day, on the read's window")
    for day in (date(2026, 11, 12), date(2026, 11, 15), date(2026, 11, 18), date(2026, 12, 20)):
        o = Order(record=True)
        o.walk.today = day
        o.walk.hist = [{'role': 'user', 'text': HER_WORDS},
                       {'role': 'agent', 'text': HER_READ, 'meta': {'figures': [{'value': 4217683.0, 'kind': 'money'}],
                                                                    'read_job_id': 'f21c0ed76df6'}}]
        tag = f"[{day.isoformat()}]"
        first = o.walk.say('Can you turn this into a deck?')
        reply = (first or {}).get('reply') or ''
        check(f"{tag} the deck she asks for is the valuation order, with the names and the dates already read",
              reply.startswith('A deck on what this partnership is worth is the Brand Partnership valuation')
              and 'I read the campaign as September 22 to November 11, 2026' in reply)
        check(f"{tag} the window is the read's own, said plainly, and never a day longer",
              'the same window as the partnership figure you already have' in reply
              and 'Attribution window: September 22 to November 11, 2026.' in reply and 'through' not in reply.split('I read the campaign')[1])
        recap = None
        for m in ('Accept', 'no', 'find the campaign asset'):
            recap = o.walk.say(m)
        check(f"{tag} the quote is $500", recap is not None and 'This is $500.' in recap['reply'])
        o.walk.say('Accept $500')
        run = o.job.runs[0] if o.job.runs else None
        check(f"{tag} one $500 charge and one job", o.charges() == [500.0] and run is not None)
        if not run:
            continue
        inp = run['inputs']
        check(f"{tag} the valuation runs on Sep 22 to Nov 11 in both windows",
              (inp['event_start'], inp['event_end'], inp['post_start'], inp['post_end'])
              == ('2026-09-22', '2026-11-11', '2026-09-22', '2026-11-11'))
        check(f"{tag} it is the valuation of record, to the cent", run['key'] == KEY and HF.same_as_review(run['payload']))
        deck_key = (o.s3.keys('generated_decks/') or [''])[0]
        pages = slide_texts(o.s3.objects[deck_key]) if deck_key else []
        # the cover's date line and the file name carry the day it was built; nothing else moves
        check(f"{tag} the deck is the same deck, page for page", len(pages) == 14 and pages[1:] == first_pages[1:]
              and '$4,217,683.00' in pages[0])
        note = next((x for x in o.thread() if (x.get('meta') or {}).get('bpiq_job_id') == run['job_id']), {})
        check(f"{tag} the thread gets the total, the deck and the one charge", str(note.get('text') or '').startswith(
            'Your Brand Partnership valuation is ready: JYT x Gap, total brand value $4,217,683.00. The deck is below')
            and str(note.get('text') or '').endswith('You were charged $500 once for this valuation.')
            and ((note.get('meta') or {}).get('link') or {}).get('url', '').startswith('https://'))
    W.UNDERPINNINGS = None


# ------------------------------------------------------------------ B
def test_b_a_deck_asked_for_afterwards(o, run, first_pages):
    print("B. a deck asked for in the thread afterwards is the valuation deck again")
    hist = list(o.walk.hist) + list(o.thread())
    seat = dict(o.led.data['users']['wallet_seat'])
    statuses, landed = {}, []
    g = {'_H': o.host, '_PM_BPIQ_JOB_PREFIX': 'system/pm_bpiq_jobs/',
         '_pm_job_owner_ok': lambda owner, user: str(owner or '') == str((user or {}).get('username') or ''),
         '_pm_deck_status_write': lambda jid, doc: statuses.__setitem__(jid, dict(doc)),
         '_pm_append_deck_to_history': lambda uname, jid, status: landed.append((uname, jid, dict(status))),
         '_pm_job_bind_thread': lambda jid, uname: None}
    calls = []
    real = B.build_deck_report
    B.build_deck_report = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    real_start = BD.start_reexport
    BD.start_reexport = lambda user, hit, gg, sync=False: real_start(user, hit, gg, sync=True)
    try:
        for words in ('Can you turn this into a deck?', 'make me a deck of the JYT x Gap valuation', 'send me that deck as a powerpoint'):
            statuses.clear()
            calls.clear()
            check(f"'{words}': not a second order", not W.wants(words, {'thread_id': o.walk.tid}, hist, store=o.walk.store,
                                                               user='wallet_seat', thread_id=o.walk.tid)
                  and W.pv_kind(words, hist) == '')
            with contextlib.redirect_stdout(io.StringIO()):
                out = W.deck_gate(seat, {'thread_id': o.walk.tid}, words, hist, '', g)
            st = next(iter(statuses.values()), {})
            out = out if isinstance(out, dict) else {}
            check(f"'{words}': the deck surface builds the valuation deck", bool(out) and out.get('success')
                  and out.get('valuation_deck') and out.get('job_id') in statuses and st.get('status') == 'done'
                  and out.get('confirm_line') == 'Building the JYT x Gap valuation deck.')
            check("  through the one valuation deck builder, once", len(calls) == 1 and 'deck_builder' not in sys.modules
                  and 'prometheus_deck' not in sys.modules)
            body = o.s3.objects.get(st.get('s3_key') or '', b'')
            pages = slide_texts(body) if body else []
            check("  the same deck, page for page", pages == first_pages)
            check("  it lands in the thread as a deck with its link", bool(landed) and landed[-1][1] == out.get('job_id')
                  and str(landed[-1][2].get('url') or '').startswith('https://') and landed[-1][2].get('slides') == 14)
        charged = o.charges()
        check("no charge for the deck of a valuation already bought", charged == [500.0])
        # the picked subject
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(seat, {}, 'Run a deck on JYT x Gap', hist, 'JYT x Gap', g)
        check("a which-subject pick of the pairing is the valuation deck too", isinstance(out, dict) and out.get('valuation_deck'))
        # what it is not
        calls.clear()
        for words in ('make a deck on Gap Customers', 'build a deck about Paige Spiranac'):
            with contextlib.redirect_stdout(io.StringIO()):
                out = W.deck_gate(seat, {}, words, hist, '', g)
            check(f"'{words}': another subject is not the valuation deck", not (isinstance(out, dict) and out.get('valuation_deck')))
        later = hist + [{'role': 'user', 'text': 'who are Gap customers?'},
                        {'role': 'agent', 'text': 'Gap Customers skew female and 25 to 44. ' * 12, 'meta': {'kind': 'read', 'figures': [1]}}]
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(seat, {}, 'turn this into a deck', later, '', g)
        check("after a later read, 'this' is that read, not the valuation", not (isinstance(out, dict) and out.get('valuation_deck')))
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(seat, {}, 'make a deck of the JYT x Gap valuation', later, '', g)
        check("but the valuation named in words still is", isinstance(out, dict) and out.get('valuation_deck'))
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(seat, {}, 'a deck for JYT x Gap please', later, 'JYT x Gap', g)
            one = W.deck_gate(seat, {}, 'build a deck on JYT', later, '', g)
        check("and so is the pairing named in full; one side's name alone, after another read, is not",
              isinstance(out, dict) and out.get('valuation_deck') and not (isinstance(one, dict) and one.get('valuation_deck')))
        # another seat holding a copy of the thread cannot open the file
        calls.clear()
        outsider = dict(o.led.data['users']['outsider'])
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(outsider, {}, 'Can you turn this into a deck?', hist, '', g)
        check("a seat that does not own the job gets no valuation deck from the thread's words",
              not (isinstance(out, dict) and out.get('valuation_deck')) and not calls)
        forged = [dict(t, meta=dict(t.get('meta') or {}, bpiq_job_id='0123456789ab', s3_key='Someone_Else.json'))
                  if (t.get('meta') or {}).get('bpiq_job_id') else t for t in hist]
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(seat, {}, 'Can you turn this into a deck?', forged, '', g)
        check("a job id the bucket does not hold builds nothing", not (isinstance(out, dict) and out.get('valuation_deck')) and not calls)
        # a probe builds nothing

        class _Probe:
            @staticmethod
            def is_probe():
                return True
        statuses.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            out = W.deck_gate(seat, {}, 'Can you turn this into a deck?', hist, '', dict(g, _faults=_Probe))
        check("a probe's ask is routed to the valuation deck and builds nothing", isinstance(out, dict) and out.get('valuation_deck')
              and not statuses and not calls)
        # the thread's own valuation counts as saved: a read may restate it, a deck is never a new order
        check("the valuation this thread delivered is a saved valuation", bool(PV.saved_valuation('', hist))
              and PV.saved_valuation('', hist).get('in_thread') and PV.owns('turn this into a deck', hist) == '')
        check("a thread with no valuation delivered still opens the order for a deck of a value read",
              BD.thread_valuation(o.walk.hist, 'turn this into a deck') is None)
        # the read guard asks the library, never the thread's own turns: a note in a thread is words
        reply = {'reply': 'The JYT x Gap partnership is worth $9,999,999 in brand value so far.', 'metrics': []}
        check("a read that prices the partnership is still held to the library's record, whatever the thread says",
              PV.guard_read(reply, 'what is the JYT x Gap partnership worth', forged) is True
              and '$9,999,999' not in reply['reply'] and PV.saved_valuation('', forged, thread_note=False) is None)
    finally:
        B.build_deck_report = real
        BD.start_reexport = real_start


# ------------------------------------------------------------------ C
def test_c_the_tab_export(run, first_pages):
    print("C. the tab's export is the same deck")
    src = open(os.path.join(ROOT, 'migration', 'bpiq_deck_export.py'), encoding='utf-8').read()
    check("the tab's export calls the valuation deck builder", 'from migration.bpiq_deck_builder import build_deck' in src)
    with contextlib.redirect_stdout(io.StringIO()):
        body = B.build_deck(copy.deepcopy(run['payload']), category=run['category'])
    check("the tab's export of the saved valuation is the chat deck, page for page", slide_texts(body) == first_pages)
    check("the chat deck takes its name from the same place",
          os.path.basename(run['deck_key']) == B.suggested_filename(run['payload'], ext='pptx'))


# ------------------------------------------------------------------ D
class ResearchForTheOrder(S.MemoryUnderpinnings):
    """The research the $1,000 order's own key holds. The order has a
    different key from the $500 one (a comparison group is part of the
    key), so in production it draws research of its own. Hermetically
    that research is stood in for by the pairing's saved research: the
    same campaign facts and the same audience read, with no valuation of
    record and no hold. Nothing here is the $500 order's record."""

    def __init__(self, base):
        super().__init__()
        self.base = base
        self.served = []

    def load(self, key):
        doc = super().load(key)
        if doc is not None or key == KEY:
            return doc if doc is not None else self.base.load(key)
        u = FX['underpinnings']
        self.served.append(key)
        return {'run_key': key, 'inputs': dict(u['inputs'], control_group=True, include_control=True),
                'campaign': copy.deepcopy(u['campaign']), 'primitives': copy.deepcopy(u['primitives'])}


def test_d_the_1000_order():
    print("D. the $1,000 order end to end: same pairing, same window, with a comparison group")
    o = Order(record=True)
    store = ResearchForTheOrder(o.eng.store)
    o.job.store = store
    W.UNDERPINNINGS = store
    recap, accepted = o.place(control='yes')
    check("the quote is $1,000 and Accept starts one job", recap is not None and 'This is $1,000.' in recap['reply']
          and accepted is not None and accepted['reply'] == W.RUNNING_REPLY and len(o.job.runs) == 1)
    check("one $1,000 charge, nothing else", o.charges() == [1000.0] and o.led.balance() == 3212.37 and not o.led.refunds)
    if not o.job.runs:
        W.UNDERPINNINGS = None
        return
    run = o.job.runs[0]
    p = run['payload']
    v = p['valuation']
    cg = p.get('control_group') or {}
    check("it is its own valuation: a different key from the $500 one, never the $500 order's record",
          run['key'] != KEY and run['key'] in store.served and bool(run['inputs'].get('control_group'))
          and not HF.same_as_review(p))
    check("the window is the read's own, Sep 22 to Nov 11", (run['inputs']['event_start'], run['inputs']['event_end'])
          == ('2026-09-22', '2026-11-11'))
    check("the total lands on the held figure, to the cent", cents(v['total_brand_value']) == cents(FIGURE))
    check("every page adds up", HF.adds_up(p))
    check("the comparison group is in the valuation", cg.get('enabled') is True and int(cg.get('control_size') or 0) == int(p['audience_size'])
          and cg.get('incremental_lift_pp') is not None)
    lift = round(float(cg['treat_delta_pp']) - float(cg['control_delta_pp']), 2) if cg.get('treat_delta_pp') is not None else None
    check("the lift is the campaign group's gain minus the comparison group's",
          lift is not None and abs(lift - float(cg['incremental_lift_pp'])) < 0.011)
    treat_people = int(p['totals']['post_users_projected']) - int(p['totals']['pre_users_projected'])
    ctrl_people = int(cg.get('control_post_users_projected') or 0) - int(cg.get('control_pre_users_projected') or 0)
    check("new engagers are the campaign group's gain net of the comparison group's",
          cg.get('control_pre_users_projected') is not None and abs(int(v['incremental_users']) - (treat_people - ctrl_people)) <= 9
          and cents(v['brand_lift_value']) == cents(int(v['incremental_users']) * float(v['rates']['blv_per_incr_user'])))
    st = o.job.status[run['job_id']]
    decks = o.s3.keys('generated_decks/')
    body = o.s3.objects[decks[0]] if decks else b''
    keep('paid_1000', (st.get('deck') or {}).get('filename') or 'deck.pptx', body)
    pages = slide_texts(body) if body else []
    text = '\n'.join(pages)
    check("the deck is handed over, READY", len(decks) == 1 and (st.get('deck') or {}).get('ready') is True and len(pages) >= 14)
    with_control = [i + 1 for i, pg in enumerate(pages) if CONTROL_WORDS_RX.search(pg)]
    check(f"the comparison group is on the pages (pages {with_control})", len(with_control) >= 3)
    check("the deck's total is the held figure", sum('$4,217,683.00' in pg for pg in pages) >= 3)
    for k in ('earned_media_value', 'brand_engagement_value', 'brand_lift_value', 'conversion_value'):
        check(f"the deck prints its own {k.replace('_', ' ')} (${float(v[k]):,.2f})", f"${float(v[k]):,.2f}" in text)
    for k in ('control_pre_pen_pct', 'control_post_pen_pct'):
        check(f"the deck prints the comparison group's {k.split('_')[1]} rate ({float(cg[k]):.1f}%)", f"{float(cg[k]):.1f}%" in text)
    # the comparison-group pages add up, as printed
    lift_page = next((pg for pg in pages if 'New people beyond the matched group' in pg), '')
    check("the brand lift page prints the gain, the matched group's gain and the difference",
          f"{treat_people:,}" in lift_page and f"{ctrl_people:,}" in lift_page and f"{int(v['incremental_users']):,}" in lift_page
          and f"${float(v['brand_lift_value']):,.2f}" in lift_page)
    eng_page = next((pg for pg in pages if 'LIFT OVER THE MATCHED GROUP' in pg.upper()), '')
    pts = (round(float(cg['treat_delta_pp']), 1), round(float(cg['control_delta_pp']), 1), round(float(cg['incremental_lift_pp']), 1))
    check(f"the engagement page prints {pts[0]} points, less {pts[1]}, is {pts[2]}",
          all(f"+{x:.1f} points" in eng_page for x in pts) and abs(round(pts[0] - pts[1], 1) - pts[2]) < 0.11)
    check("both groups are the same size on the page", pages[2].count(f"{int(p['projected_audience_size']):,}") >= 2
          and 'MATCHED GROUP' in pages[2].upper())
    hits = sorted({m.group(0).lower() for m in BANNED_RX.finditer(text)})
    check(f"no test statistic, method word, outside name or DRAFT stamp {hits or ''}", not hits)
    notes = [t for t in o.thread() if (t.get('meta') or {}).get('bpiq_job_id') == run['job_id']]
    check("the hand-over line states the figure and one $1,000 charge", len(notes) == 1 and notes[0]['text'] == (
        "Your Brand Partnership valuation is ready: JYT x Gap, total brand value $4,217,683.00. "
        "The deck is below, and the link is good for 7 days.\nYou were charged $1,000 once for this valuation."))
    o.walk.say('Accept $1,000')
    check("a second Accept charges nothing and builds nothing", o.charges() == [1000.0] and len(o.job.runs) == 1
          and o.s3.keys('generated_decks/') == decks)
    print("     components of this hermetic $1,000 run (the review deck's in brackets):")
    r = HF.REVIEW['valuation']
    for k in ('total_brand_value', 'earned_media_value', 'brand_engagement_value', 'brand_lift_value', 'conversion_value',
              'attributable_to_talent'):
        print(f"       {k:26s} ${float(v[k]):>14,.2f}   [${float(r[k]):>14,.2f}]")
    print(f"       {'incremental_users':26s} {int(v['incremental_users']):>15,}   [{int(r['incremental_users']):>15,}]")
    print(f"       {'campaign group':26s} {int(p['projected_audience_size']):>15,}   [{int(HF.REVIEW['projected_audience_size']):>15,}]")
    print(f"       {'orders':26s} {int(p['conversions']['post_users_projected']):>15,}   "
          f"[{int(HF.REVIEW['conversions']['post_users_projected']):>15,}]")
    print(f"       campaign group {cg.get('treat_pre_pen_pct')}% to {cg.get('treat_post_pen_pct')}%, comparison group "
          f"{cg.get('control_pre_pen_pct')}% to {cg.get('control_post_pen_pct')}%, lift {cg.get('incremental_lift_pp')} points")
    W.UNDERPINNINGS = None


# ------------------------------------------------------------------ E
def test_e_the_wiring():
    print("E. the wiring: the job body, the widget, the email, the fallbacks")
    chat = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
    job = chat.split('def _pm_run_bpiq_job')[1].split('\ndef ')[0]
    done = job.split("'status': 'done'")[1].split('_bj.settle(')[0]
    check("the job hands the deck over inside its finished status, before the note",
          '**_bj.hand_over(job_id, username, inputs, bare_key, payload, category, _H)' in done)
    check("the ready email carries the deck", 'files=_bj.deck_files(job_id)' in job.split('_on_done(')[1])
    check("the job body builds no deck of its own", 'deck_builder' not in job and 'render_insights_deck' not in job)
    html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
    poll = html.split('async function _pmPollBpiqJob')[1].split('async function ')[0]
    check("the widget hands over the job's own line and link", "_pmPushMetaFor(_po, 'agent', String(st.note), _bm)" in poll
          and "_bm.link = { url: st.link.url" in poll and "_pmHistoryHasJob('bpiq_job_id', jobId)" in poll)
    check("the widget's own fallback line prints cents", 'minimumFractionDigits: 2' in poll)
    # the copy
    p = {'project_name': 'JYT x Gap', 'valuation': {'total_brand_value': 4217683.0}}
    deck = {'url': 'https://x.example/deck.pptx', 'filename': 'd.pptx', 's3_key': 'generated_decks/j/d.pptx'}
    check("an included seat's line says so and names no price", BD.note(p, deck, {'included': True}).endswith('\n' + BD.INCLUDED_LINE)
          and '$500' not in BD.note(p, deck, {'included': True}))
    check("with no charge on record the line carries none", '\n' not in BD.note(p, deck, None))
    check("with no deck the line points at the tab", BD.note(p, None, {'included': False, 'label': '$500'}) == (
        "Your Brand Partnership valuation is ready: JYT x Gap, total brand value $4,217,683.00. "
        "It is in the Brand Partnership tab now.\nYou were charged $500 once for this valuation."))
    check("the charge line is the quote's own price", W.charge_stamp({'control_group': True}, {'username': 'x'}, 'x', {})
          == {'included': False, 'label': '$1,000'} and W.charge_stamp({}, {'username': 'x', 'unlimited': True}, 'x', {})
          == {'included': True})
    # a deck that cannot be built never stops the valuation

    class NoSign(FakeS3):
        def generate_presigned_url(self, *a, **k):
            raise RuntimeError('no signer')
    led = CP.Ledger(HF.seat_doc())
    host = Host(led, NoSign())
    BJ._JOBS.clear()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        more = BJ.hand_over('aaaaaaaaaaaa', 'wallet_seat', {}, 'x.json', copy.deepcopy(HF.REVIEW), 'FASHION', host)
    check("a deck that cannot be stored or signed leaves the valuation handed over as before", more == {}
          and BJ.deck_files('aaaaaaaaaaaa') == [])
    threads = Threads()
    BJ._chat = lambda: threads
    BJ.ready_note('wallet_seat', 'aaaaaaaaaaaa', copy.deepcopy(HF.REVIEW), 'x.json')
    note = (threads.hist.get(('wallet_seat', None)) or [{}])[-1]
    check("and its note points at the tab, with no link", 'It is in the Brand Partnership tab now.' in str(note.get('text'))
          and 'link' not in (note.get('meta') or {}))
    # the email wording
    seen = []
    DL._email('bpiq', username='u', name='JYT x Gap', who='Sam', email='s@example.test', shelf='',
              files=[(b'x', 'JYT_x_Gap.pptx', BD.PPTX_MIME), (b'y', 'd.csv', 'text/csv')], facts=[], intro='', ready_message='',
              extra_bcc=(), send=lambda **kw: seen.append(kw) or {'sent': True})
    DL._email('subiq', username='u', name='Tracker', who='Sam', email='s@example.test', shelf='',
              files=[(b'y', 'd.csv', 'text/csv')], facts=[], intro='', ready_message='', extra_bcc=(),
              send=lambda **kw: seen.append(kw) or {'sent': True})
    check("a valuation's note says the deck and the raw data are attached", len(seen) == 2
          and 'The valuation deck and the raw data are attached.' in seen[0]['body'])
    check("every other note keeps its own attachment line", 'The raw data is attached (1 file).' in seen[1]['body'])


def main():
    o, run, body, pages = test_a_the_500_order()
    test_b_a_deck_asked_for_afterwards(o, run, pages)
    test_a2_her_own_path(pages)
    test_c_the_tab_export(run, pages)
    test_d_the_1000_order()
    test_e_the_wiring()
    BA.INDEX_FOR_TESTS = None
    BA.ASKER_LOOKUP = None
    BA.USERS_FOR_TESTS = None
    print(f"\n{'OK' if not FAIL else 'FAIL'} ({N - FAIL if FAIL else N} of {N} checks)" if FAIL else f"\nOK ({N} checks)")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
