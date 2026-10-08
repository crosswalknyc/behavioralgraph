"""The routing decision table (2026-10-02 RCA, W2).

One declarative list of every decision ``understand.decide`` can make,
in the order it makes them. Each row carries:

  id             stable name
  surface        analyze | interpret | deck
  reason         the exact ``reason`` string decide() emits (a prefix
                 when the reason carries a suffix, e.g. ``mode:``)
  client_action  what the dashboard widget does with it
                 (analyze | interpret | deck | analyze_menu |
                 compare_open_tabs | compare_picker | short_reply)
  examples       (text, has_ctx) pairs that MUST land on this row
  counter        (text, has_ctx) pairs that MUST NOT land on this row
  note           plain-English description

The server (``understand.decide``) is the only router; the widget
calls ``/api/prometheus/v1/understand`` and acts on ``surface`` +
``client_hint``. This table is the contract between them and the
spec the regression test (``scripts/test_pm_routing_table.py``)
enforces: every row has at least one example that lands on it, every
reason the server can emit has a row, and every client action the
table names is handled in the widget. ``GET
/api/prometheus/v1/routing-table`` serves it to session callers.

Pure data plus two helpers; no Flask.
"""
from __future__ import annotations

ROWS = [
    {
        'id': 'empty', 'surface': 'interpret', 'reason': 'empty',
        'client_action': 'interpret',
        'note': 'Nothing to route.',
        'examples': [('', False), ('   ', True)],
        'counter': [('Nike', False)],
    },
    {
        'id': 'explicit_mode', 'surface': 'analyze',
        'reason': 'explicit_mode', 'client_action': 'analyze',
        'note': 'A mode chip or confirm payload already chose analyze.',
        'examples': [], 'counter': [],
        'needs': {'mode': 'exec_summary'},
    },
    {
        'id': 'explicit_step', 'surface': 'analyze',
        'reason': 'explicit_step', 'client_action': 'analyze',
        'note': 'An armed analyze step (confirm, picker) in extra.',
        'examples': [], 'counter': [],
        'needs': {'extra': {'bind_subject': 'x'}},
    },
    {
        'id': 'tool_intake', 'surface': 'analyze',
        'reason': 'tool_intake:', 'client_action': 'analyze',
        'note': 'Attribution / Flywheel / Journey / Brand Partnership '
                'guided intakes open on the analyze surface. Host '
                'intents; exercised in the integration suites.',
        'examples': [], 'counter': [],
        'host_only': True,
    },
    {
        'id': 'deck_ask', 'surface': 'deck', 'reason': 'deck_ask',
        'client_action': 'deck',
        'note': 'Build a deck / one-pager / venn diagram.',
        'examples': [('build a pitch deck on this audience', True),
                     ('make a one-pager on the Nike runner profile', True)],
        'counter': [('who watches love island?', False)],
    },
    {
        'id': 'short_reply', 'surface': 'interpret',
        'reason': 'short_reply:', 'client_action': 'short_reply',
        'note': 'A bare yes / no / number / "approved" with nothing '
                'armed is not an ask; the ask service answers it.',
        'examples': [('yes', False), ('no', False), ('approved', False),
                     ('2', False)],
        'counter': [('yes build the Nike profile', False)],
    },
    {
        'id': 'catalog_lookup', 'surface': 'analyze',
        'reason': 'catalog_lookup', 'client_action': 'analyze',
        'host_only': True,
        'note': 'Existence / "do you see" / audience-size asks answered '
                'from the corpus catalog with no model call (2026-10-06). '
                'Served by the ask service lane, not decide(): "do we '
                'have a profile for Ms. Rachel?", "how big is the Will '
                'and Grace audience".',
        'examples': [], 'counter': [('who watches love island?', False)],
    },
    {
        'id': 'brand_coverage', 'surface': 'analyze',
        'reason': 'brand_coverage', 'client_action': 'analyze',
        'host_only': True,
        'note': 'Whether a brand is one we carry, which behavioral category '
                'it lives under, and what the open profile shows for it '
                '(2026-10-08: "Is Alexa measured in Crosswalk?", "what would '
                'alexa and echo be listed under in the behavioral tab"). '
                'Answered from Gen Pop + the profile, no model call, never '
                'the open-page confirm.',
        'examples': [], 'counter': [('what brands over-index with this audience?', False)],
    },
    {
        'id': 'document', 'surface': 'analyze',
        'reason': 'document', 'client_action': 'analyze',
        'host_only': True,
        'note': 'An ask about a file attached to the thread: answered from '
                'the file (slide / page references) or applied to a deck or '
                'document and handed back as an edited copy (2026-10-06).',
        'examples': [], 'counter': [],
    },
    {
        'id': 'design_request', 'surface': 'analyze',
        'reason': 'design_request', 'client_action': 'analyze',
        'host_only': True,
        'note': 'A change-the-page ask ("Section 4 is showing Arrow, please '
                'remove") gets one fixed reply (Prometheus cannot make design '
                'changes; the user experience team has it) and one email to '
                'Jenna + Jessie. Never a build offer (2026-10-06).',
        'examples': [], 'counter': [],
    },
    {
        'id': 'journey_drilldown', 'surface': 'analyze',
        'reason': 'journey_drilldown', 'client_action': 'analyze',
        'host_only': True,
        'note': 'A number from a Digital Journey on screen ("what '
                'podcasts are behind the 27,559?") is a lookup into that '
                'journey: answered from the breakdown the file holds, or '
                'built once and written onto the page under the row '
                '(2026-10-06).',
        'examples': [], 'counter': [],
    },
    {
        'id': 'capability_question', 'surface': 'analyze',
        'reason': 'capability_question', 'client_action': 'analyze',
        'note': 'A question about what the product can do, answered '
                'deterministically, never drafted.',
        'examples': [('Can I cut the existing Apple TV+ profile by '
                      'quarter?', False)],
        'counter': [('can you build me a Nike profile', False)],
    },
    {
        'id': 'product_fact', 'surface': 'analyze',
        'reason': 'product_fact', 'client_action': 'analyze',
        'note': 'A fact about the product with one fixed answer (the '
                'Crosswalk sample is 10 million US consumers), answered '
                'deterministically, never drafted.',
        'examples': [('I want to see what the size of teh Crosswalk '
                      'sample audience was from January 1, 2026 to date',
                      False),
                     ('how big is your panel', True)],
        'counter': [('how many Netflix viewers in the sample', False),
                    ('Nike', False)],
    },
    {
        'id': 'subiq_lookup', 'surface': 'analyze',
        'reason': 'subiq_lookup', 'client_action': 'analyze',
        'note': '"Do you see the X Subscriber IQ?" is a library lookup.',
        'examples': [('do you see the SWAT Exiles Subscriber IQ?', False)],
        'counter': [('Pull Subscriber IQ for SWAT Exiles Season 1 on '
                     'Starz', False)],
    },
    {
        'id': 'question_about_subiq', 'surface': 'analyze',
        'reason': 'question_about_subiq', 'client_action': 'analyze',
        'note': 'A question about the open Subscriber IQ page.',
        'examples': [('Is that 55% of total viewers or of new and '
                      'reactivated watchers?', True)],
        'counter': [('run subscriber iq on landman', True)],
    },
    {
        'id': 'task_not_build', 'surface': 'analyze',
        'reason': 'task_not_build', 'client_action': 'analyze',
        'note': 'An imperative task (review, compare, prepare a report) '
                'without a build order is analysis, never a build.',
        'examples': [('review these three creators and prepare a report '
                      'on which of the three actually influence product '
                      'purchases and list some brands', False),
                     ('Please analyze the churn on these two titles and '
                      'recommend which one gets the marketing dollars',
                      False)],
        'counter': [('Run a profile for Starz', False),
                    ('Reba McEntire avid fans', False)],
    },
    {
        'id': 'analyze_menu', 'surface': 'analyze',
        'reason': 'analyze_menu', 'client_action': 'analyze_menu',
        'note': '"Analyze this data" opens the analysis menu.',
        'examples': [('analyze this data', True),
                     ('Analyze the open profile', True)],
        'counter': [('analyze the churn on these two titles and recommend '
                     'one', True)],
    },
    {
        'id': 'compare_open_tabs', 'surface': 'analyze',
        'reason': 'compare_open_tabs', 'client_action': 'compare_open_tabs',
        'note': '"Compare the open tabs" compares what is open.',
        'examples': [('compare the open tabs', True),
                     ('compare across open profiles', True)],
        'counter': [('compare this against other profiles', True)],
    },
    {
        'id': 'compare_others', 'surface': 'analyze',
        'reason': 'compare_others', 'client_action': 'compare_picker',
        'note': '"Compare against other profiles" opens the picker.',
        'examples': [('compare this against other profiles', True),
                     ('compare with other audiences', True)],
        'counter': [('compare the open tabs', True)],
    },
    {
        'id': 'search_demand', 'surface': 'analyze',
        'reason': 'search_demand', 'client_action': 'analyze',
        'note': 'Search-journey demand carries its own subject.',
        'examples': [], 'counter': [],
        'host_only': True,
    },
    {
        'id': 'mode', 'surface': 'analyze', 'reason': 'mode:',
        'client_action': 'analyze',
        'note': 'A named analysis mode with data open (exec summary, '
                'whitespace, personas, new consumers, easter eggs, '
                'LinkedIn post, full read).',
        'examples': [('give me the exec summary', True),
                     ('where is the whitespace', True),
                     ('write a linkedin post on this', True)],
        'counter': [('give me the exec summary', False)],
    },
    {
        'id': 'question', 'surface': 'analyze', 'reason': 'question',
        'client_action': 'analyze',
        'note': 'A question with no data open. A wish stated as a '
                'sentence ("we want to understand ...") and a metric '
                'asked over time ("monthly consumption for ...") are '
                'questions too (2026-10-08, East Tree Media).',
        'examples': [('who watches love island?', False),
                     ('what does the love island audience buy', False),
                     ('We want to understand the consumption over time '
                      'for The Office US', False),
                     ('Show me monthly consumption for The Office US, '
                      'in the US.', False),
                     ('viewership month by month for Suits in the US',
                      False)],
        'counter': [('Run a profile for Starz', False)],
    },
    {
        'id': 'data_open', 'surface': 'analyze', 'reason': 'data_open',
        'client_action': 'analyze',
        'note': 'Data open and the ask is not a build order.',
        'examples': [('what do these fans buy', True),
                     ('top brands here', True)],
        'counter': [('build a profile of Nike runners', True)],
    },
    {
        'id': 'subiq', 'surface': 'interpret', 'reason': 'subiq',
        'client_action': 'interpret',
        'note': 'An explicit Subscriber IQ pull.',
        'examples': [('Pull Subscriber IQ for SWAT Exiles Season 1 on '
                      'Starz', False),
                     ('run subscriber iq on landman', False)],
        'counter': [('do you see the SWAT Exiles Subscriber IQ?', False)],
    },
    {
        'id': 'build_or_other', 'surface': 'interpret',
        'reason': 'build_or_other', 'client_action': 'interpret',
        'note': 'A build order or a bare subject; the interpret step '
                'drafts a brief for approval.',
        'examples': [('Run a profile for Starz', False),
                     ('Reba McEntire avid fans', False),
                     ('Nike', False),
                     ('can you build me a Nike profile', False),
                     ('is there a way to build an audience of people who '
                      'have attended a Gunna concert?', False),
                     ('is there a way to build an audience of people who '
                      'have attended a Gunna concert?', True)],
        'counter': [('who watches love island?', False)],
    },
]

CLIENT_ACTIONS = frozenset(r['client_action'] for r in ROWS)
SURFACES = frozenset(r['surface'] for r in ROWS)


def row_for(reason):
    """The row a decide() reason belongs to (prefix-aware)."""
    reason = str(reason or '')
    for r in ROWS:
        rr = r['reason']
        if rr.endswith(':'):
            if reason.startswith(rr):
                return r
        elif reason == rr:
            return r
    return None


def as_public():
    """The table as the widget / admin sees it (no test fixtures)."""
    return [{k: r[k] for k in ('id', 'surface', 'reason',
                               'client_action', 'note')}
            for r in ROWS]
