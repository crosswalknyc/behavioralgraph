#!/usr/bin/env python3
"""The "What we count as a view" paragraph rides a message only when the
read's own figures are viewing counts (Jenna 2026-11-11).

A book-audience answer was emailed with the paragraph attached. Its
figures are people engaged with two novels by year and source; the only
viewing word in it is one sentence about what is captured ("TikTok and
YouTube viewing on a phone is counted like everything else"). The mail
door attached the paragraph because that word was there.

The rule now: a stamped counting family decides when there is one
(purchase, engagement and sizing reads never carry it). With none, the
figures decide: a count or a share stated as views, viewers, streams or
watchers, or a table column named for one. A viewing word elsewhere in
the text decides nothing.

  A. tonight's email, as it was written, leaves the mail door's text steps without it
  B. reads whose figures are views still carry it, once
  C. purchase, engagement and sizing reads never carry it
  D. the thread's counting block was not the trigger and still is not
  E. the PDF of an email and the file note follow the same rule

Run: python3 scripts/test_pm_view_definition_only_on_view_figures.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ['PM_MAIL_OFF'] = '1'
sys.modules.setdefault('app', None)          # the dashboard app is never imported here

from prometheus import methodology as M  # noqa: E402
from prometheus import outbound_mail as OM  # noqa: E402

FAIL = 0
N = 0
EMAIL = open(os.path.join(HERE, 'fixtures', 'pm_book_audience_email_2026_11_11.txt'), encoding='utf-8').read()
HEADER = 'What we count as a view:'


def check(label, cond):
    global FAIL, N
    N += 1
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def through_the_door(body, family=None):
    """The body as the mail door words it: its scrub, then the two text
    steps it runs before a send. The door itself is never called here
    (it writes a send record and can reach the mail service), so nothing
    is sent or logged by this test."""
    return M.ensure_period_note(M.attach_view_definition(OM.scrub(body), family=family))


VIEW_READS = (
    "Landman drew 5,432,118 viewers in the window, up from 4,118,227 the season before.",
    "Scream 7 on Paramount+\n\nViewers: 2,318,447\nFinished it: 61.3%",
    "31.2% of Scream 7 viewers also watched Scream 6 in the 30 days before.",
    "Title / Viewers / Share of viewers\nLandman / 5,432,118 / 38.1%\nTulsa King / 3,912,406 / 27.4%",
    "The premiere was watched by 1,204,331 people on Peacock in its first week.",
    "The episode has 884,213 views so far, 1 in 4 of them on a phone.",
    "| Episode | Unique viewers |\n| 1 | 1,204,331 |\n| 2 | 1,118,907 |",
    "6,204,118 people streamed the album in its first week.",
    "They watched 2.4 billion hours.",
)
OTHER_READS = (
    ("a sizing read", "The Love Hypothesis reached 2,645,468 US people in 2022 and 7,381,875 so far in 2026.", M.SIZING),
    ("a sizing read that explains what is captured",
     "The audience is 1,919,985 US people. TikTok and YouTube viewing on a phone is counted like everything else, once per person.",
     M.SIZING),
    ("a purchase read", "795,158 people bought the book online in 2022; 18.4% of them bought the sequel. Many found it while viewing TikTok.",
     M.PURCHASE),
    ("an engagement read", "1,492,039 people engaged with the title on TikTok in 2022. Viewing habits were not part of this read.",
     M.ENGAGEMENT),
    ("a methods sentence", "We see viewing, search, social and commerce on the same ID. The audience is 2,184,637 people.", None),
    ("a profile read", "44% female, 62% under 35. Top streaming service: Netflix at 68.2%. They are heavy viewers of reality TV.", None),
    ("a valuation note", "The partnership is worth $4,217,683 so far. Viewers of the film were not counted separately.", None),
)


def test_a_tonights_email():
    print("A. tonight's email leaves the mail door without the view paragraph")
    check("the fixture is the email as written (no view paragraph in it)", HEADER not in EMAIL and 'THE SPLIT BY SOURCE' in EMAIL)
    check("the only viewing word is the sentence about what is captured",
          EMAIL.count('viewing') == 1 and 'TikTok and YouTube viewing on a phone is counted like everything else' in EMAIL)
    check("the old trigger fires on it (a viewing word is in the text)", M.mentions_views(EMAIL))
    check("its figures are not viewing counts", not M.figures_are_views(EMAIL))
    out = through_the_door(EMAIL)
    check("the mail door attaches nothing", HEADER not in out and 'A view is one person' not in out)
    check("the body is otherwise as written", out.strip() == OM.scrub(EMAIL).strip())
    check("the family of the read is sizing, not viewing", M.read_family(None, EMAIL, '') != M.VIEWING)
    for fam in (M.SIZING, M.PURCHASE, M.ENGAGEMENT):
        check(f"stamped {fam}, nothing is attached", HEADER not in M.attach_view_definition(EMAIL, family=fam))
    check("the door's own steps are these two, in this order",
          "body_c = _meth.attach_view_definition(body_c, family=family)\n        body_c = _meth.ensure_period_note(body_c)"
          in open(os.path.join(ROOT, 'prometheus', 'outbound_mail.py'), encoding='utf-8').read())


def test_b_view_reads_still_carry_it():
    print("B. a read whose figures are views still carries it, once")
    for text in VIEW_READS:
        out = M.attach_view_definition(text)
        check(f"carried: {text[:58]!r}", M.figures_are_views(text) and out.count(HEADER) == 1)
        check("   and not twice", M.attach_view_definition(out).count(HEADER) == 1)
    check("a stamped viewing family carries it whatever the words", HEADER in M.attach_view_definition('2,318,447 in the window.', family=M.VIEWING))
    check("the email with a real viewing table added would carry it",
          M.attach_view_definition(EMAIL + "\n\nTitle / Viewers\nThe Love Hypothesis (film) / 3,104,227").count(HEADER) == 1)


def test_c_other_reads_never_carry_it():
    print("C. purchase, engagement and sizing reads never carry it")
    for label, text, fam in OTHER_READS:
        check(f"{label}: nothing attached by the figures", HEADER not in M.attach_view_definition(text))
        if fam:
            check(f"{label}: nothing attached with its family stamped", HEADER not in M.attach_view_definition(text, family=fam))
    for fam in (M.PURCHASE, M.ENGAGEMENT, M.SIZING):
        check(f"a {fam} family is never a viewing read, even when the text states viewers",
              not M.figures_are_views(VIEW_READS[0], fam) and HEADER not in M.attach_view_definition(VIEW_READS[0], family=fam))
    check("an empty body is left alone", M.attach_view_definition('') == '' and M.attach_view_definition(None) is None)


def test_d_the_thread_block():
    print("D. the thread's counting block does not share the trigger")
    fam = M.read_family(None, EMAIL, 'how many people engaged with each novel by year')
    check("the email's text reads as a sizing read in the thread", fam == M.SIZING)
    check("a viewing read still reads as viewing", M.read_family(None, VIEW_READS[0], 'how many people watched Landman') == M.VIEWING)


def test_e_other_surfaces():
    print("E. the PDF of an email and the file note follow the same rule")
    src = open(os.path.join(ROOT, 'prometheus_email_pdf.py'), encoding='utf-8').read()
    check("the PDF attaches through the same function", '_meth.attach_view_definition(body_text)' in src)
    src = open(os.path.join(ROOT, 'prometheus', 'outbound_mail.py'), encoding='utf-8').read()
    check("the mail door passes the read's family when it has one", '_meth.attach_view_definition(body_c, family=family)' in src)
    src = open(os.path.join(ROOT, 'prometheus_analysis.py'), encoding='utf-8').read()
    check("the file note asks whether the figures are views", '_meth.figures_are_views(' in src)
    src = open(os.path.join(ROOT, 'prometheus', 'methodology.py'), encoding='utf-8').read()
    body = src[src.index('def attach_view_definition'):]
    body = body[:body.index('\ndef ', 10)]
    check("attaching never asks whether a viewing word is in the text", 'mentions_views(' not in body and 'figures_are_views(' in body)


if __name__ == '__main__':
    test_a_tonights_email()
    test_b_view_reads_still_carry_it()
    test_c_other_reads_never_carry_it()
    test_d_the_thread_block()
    test_e_other_surfaces()
    print()
    if FAIL:
        print(f"FAIL ({FAIL} of {N})")
        sys.exit(1)
    print(f"OK ({N} checks)")
