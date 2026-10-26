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
        'id': 'asked_again', 'surface': 'analyze',
        'reason': 'repeat_calm', 'client_action': 'analyze',
        'note': 'The same question again after a reply the reader did not '
                'accept (prometheus.turn_gate, before decide()). The second '
                'ask may take the other path once; the third ask, or a '
                'second ask the other path could not answer, is the standing '
                'calm line ("Working on it. Confirmed your task is in '
                'progress and I will email you when it completes.") plus '
                'ONE ops email to jenna@ and jessie@ through the chatbot '
                'error path, keyed on user + question, carrying the user, '
                'the question, the earlier replies, the bound subject and '
                'window and the suggested answer path; the ask is logged '
                'held with a two hour promise. Never a scold, never a '
                'debugging question (Jenna 2026-10-21, Jay Patel: "What did '
                'the BookTok exposed viewers watch on Prime Video in the 30 '
                'days after the film?" asked three times drew "I have '
                'answered this twice ... tell me which part is off"). Pinned '
                'by scripts/test_pm_invariants.py (I7 + door D).',
        'examples': [], 'counter': [],
        'host_only': True,
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
        'id': 'twin_lab', 'surface': 'analyze', 'reason': 'twin_lab',
        'client_action': 'analyze', 'host_only': True,
        'note': 'Digital Twins IQ (Jenna 2026-10-26): a build order ("Build a twin of '
                'Gilmore Girls Hulu viewers") opens the twin intake through the '
                'tool_intake:twin row; a lab run on a twin ("Run Greenlight Lab on the '
                'Gilmore Girls twin ...", "Run Launch Lab on this plan", "Put the '
                'signal-led plan in the Spend Engine on the rule: max 15% a day", '
                '"Compare the X and Y twins"), the twin catalog ("do you have a twin '
                'for X"), the status ("is my twin ready") and the directions ("how do '
                'I run Greenlight Lab") are served by prometheus.digital_twin_lane on '
                'the analyze surface. Host intent; exercised in '
                'scripts/test_pm_digital_twins.py.',
        'examples': [],
        'counter': [('how big is your panel', True), ('what does a panel report cost', False),
                    ('twin peaks viewers', False)],
    },
    {
        'id': 'focus_group', 'surface': 'analyze', 'reason': 'focus_group',
        'client_action': 'analyze', 'host_only': True,
        'note': 'Focus Group IQ, the focus group the reader moderates (Jenna 2026-10-26): '
                '"Open a focus group with the Mark Rober twin and ask whether they would '
                'pay $29 for the Build Box" opens the room and answers in seconds with '
                'the % table and the voices; "why?", "show of hands who watched season '
                '1", "ask them ...", "re-ask at $19" stay in the same room for the '
                'thread. Served by prometheus.digital_twin_lane.run_panel; never fires '
                'on the word panel alone (the sample fact and the panel report keep '
                'their lanes).',
        'examples': [],
        'counter': [("what's the 10 million sample you're mentioning", True), ('how big is your panel', True)],
    },
    {
        'id': 'deck_ask', 'surface': 'deck', 'reason': 'deck_ask',
        'client_action': 'deck',
        'note': 'Build a deck / one-pager / venn diagram.',
        'examples': [
                     # ask log 2026-10-20 (I9)
                     ("are you able to make a slide presentation depicting demographic, interests, behaviors and platform users as well.  I am wanting to present something to a client", True),('build a pitch deck on this audience', True),
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
        'id': 'status_lookup', 'surface': 'analyze',
        'reason': 'status_lookup', 'client_action': 'analyze',
        'host_only': True,
        'note': 'An ask that says the reader cannot see, find or has not '
                'received their work ("I don\'t see No Reservations - only '
                'Babylon 5", "have not received an email yet", "is it done") '
                'is answered from their jobs across every product, their '
                'engine runs and the delivery records; no model call '
                '(2026-10-13, Carolyn). Served by the ask service lane.',
        'examples': [], 'counter': [('where is the fall-off from the ticket step?', True)],
    },
    {
        'id': 'deliverable_lookup', 'surface': 'analyze',
        'reason': 'deliverable_lookup', 'client_action': 'analyze',
        'host_only': True,
        'note': 'Where-is-my deck / PDF / file / email asks answered from '
                'the caller\'s deck records, linked files, sent mail, and '
                'the catalog with no model call (2026-10-09, Scott: "where '
                'do i find the howdy deck created last night"). Served by '
                'the ask service lane, not decide().',
        'examples': [], 'counter': [('where is the fall-off from the ticket step?', True)],
    },
    {
        'id': 'email_file', 'surface': 'analyze',
        'reason': 'email_file', 'client_action': 'analyze',
        'host_only': True,
        'note': '"Email me this file" is a command: the open profile\'s CSV '
                '(and its cut) or the latest read in the thread goes out '
                'through the house mail door (2026-10-12, Debi: it went to '
                'the build flow and drew an existing-match offer). Served '
                'by the ask service lane, not decide().',
        'examples': [], 'counter': [('email me when it is ready', True)],
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
                '(2026-10-06). The number comes from the one shared '
                'extractor (prometheus.stated_numbers, 2026-10-21): a year '
                'inside a date, a month/day pair or a season / episode '
                'marker is never a figure and a bare four digit year never '
                'triggers the explainer; the lane fires only on a figure '
                'question (why / what is behind / how is this calculated / '
                'what makes up) and never on a question that asks how many '
                'of an audience did something or names a subject, a '
                'platform pair and a window (Bria Dotson: "How many viewers '
                'who watched Law & Order: SVU on Peacock from September 2025 '
                'to March 2026 also watched the series on Hulu?" drew the '
                '2,025-person row of a Peacock journey). Pinned by '
                'scripts/test_pm_stated_numbers.py.',
        'examples': [],
        # host_only rows carry counters for the record; the extractor test pins them
        'counter': [("How many viewers who watched Law & Order: SVU on Peacock from September 2025 to March 2026 also watched the series on Hulu?", True),
                    ('re-run in the time frame 9/16 - 9/30', True)],
    },
    {
        'id': 'where_watched', 'surface': 'analyze',
        'reason': 'where_watched', 'client_action': 'analyze',
        'note': 'Where a named title is watched ("where people watched '
                'the film mutiny", "where do people watch gilmore girls", '
                '"is the show the bear on hulu"): the title in the words is '
                'the subject, answered from the library, the content map, '
                'a live look across the services or one research call, '
                'with the split of its viewers by service. No charge, no '
                'draft, and never a confirm about the profile open on the '
                'screen (Jay Patel 2026-10-20).',
        'examples': [('where people watched the film mutiny', True),
                     ('where do people watch gilmore girls', False),
                     ('is the show the bear on hulu', True),
                     ('what platform carries mutiny', False)],
        'counter': [('how old is this audience', True),
                    ('where do the people on this profile watch tv', True),
                    ('run a profile on mountain dew', True)],
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
        'id': 'derived_read', 'surface': 'analyze',
        'reason': 'derived_read', 'client_action': 'analyze',
        'note': 'A delivered figure split by a dimension the panel observes '
                '(device, daypart, geography, demographic). A derived read '
                'at the derived-cut price, with a page open, with nothing '
                'open, or through the API (2026-10-12, Jenna: never page '
                'dependent by default).',
        'examples': [("Could you break the hours out by the method of "
                      "consumption (tv's, mobile, tablet etc.)?", False),
                     ('split the signups by time of day', True),
                     ('Pull a device-level breakdown of The Office US hours', False)],
        'counter': [('how many hours of The Office US were streamed last month', False),
                    ('build a profile on The Office US', False)],
    },
    {
        'id': 'product_fact', 'surface': 'analyze',
        'reason': 'product_fact', 'client_action': 'analyze',
        'note': 'A fact about the product with one fixed answer (the '
                'Crosswalk sample is 10 million US consumers), answered '
                'deterministically, never drafted. A question about what '
                'the ten million IS, asked back at an answer that named it '
                '("what\'s the 10 million sample you\'re mentioning"), is '
                'the same fact and never the open-tab confirm (Tania Nasr '
                '2026-10-20).',
        'examples': [('I want to see what the size of teh Crosswalk '
                      'sample audience was from January 1, 2026 to date',
                      False),
                     ('how big is your panel', True),
                     ("what's the 10 million sample you're mentioning", True),
                     ('what does inside the 10 million sample mean', True)],
        'counter': [('how many Netflix viewers in the sample', False),
                    ('Nike', False),
                    ("what's the sample size for Netflix viewers", True)],
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
        'examples': [
                     # ask log 2026-10-20 (I9)
                     ("Give me unique US viewers per surface, not just hours, so we can see hours per viewer on television versus phone", False),('who watches love island?', False),
                     # Digital Twins IQ directions (2026-10-26): question-shaped, answered by the twin lane on the analyze surface
                     ('how do I run Greenlight Lab on a twin?', False),
                     ('do you have a twin for Mark Rober?', False),
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
        'examples': [
                     # ask log 2026-10-20 (I9)
                     ("What are other titles watched (across platforms) by people who watched the Among Us series on Paramount+?", True),
                     # ask log 2026-10-20 (I9)
                     ("How many UFC fans are there in the US? And how many of those are on Paramount+?", True),
                     # ask log 2026-10-20 (I9)
                     ("how many people have read 'Lore Olympus' webtoon in the last 12 months? how incremental is this audience to prime video users?", True),
                     # ask log 2026-10-20 (I9)
                     ("I am interested in building a report for a betting client like draftkings....can you make me a report to provide them advertising directional insight?", True),('what do these fans buy', True),
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
        'examples': [
                     # ask log 2026-10-20 (I9)
                     ("run a profile on mountain dew", True),
                     # ask log 2026-10-20 (I9)
                     ("can you build a profile for the toys category that is for the brand Lego?", True),
                     # ask log 2026-10-20 (I9)
                     ("Everybody Loves Raymond viewers on Amazon Prime Video, September 2026", False),('Run a profile for Starz', False),
                     ('Reba McEntire avid fans', False),
                     ('Nike', False),
                     ('can you build me a Nike profile', False),
                     ('is there a way to build an audience of people who '
                      'have attended a Gunna concert?', False),
                     ('is there a way to build an audience of people who '
                      'have attended a Gunna concert?', True),
                     # an explicit profile order with a profile open is
                     # the build lane, whatever is on the screen or
                     # armed (2026-10-20 Jenna: 'run a profile on
                     # mountain dew' with Ghost Energy Drink open drew
                     # a $500 custom-read offer)
                     ('run a profile on mountain dew', True),
                     ('build a profile on mountain dew', True),
                     ('run aprofile on ncis: new york', True),
                     ('pull a profile for Nike runners', True),
                     # ask log 2026-10-21 (Jessie): a build verb, a
                     # consumer noun and a title with two slips is a
                     # build order; the entity resolves, the gate never
                     # asks whether it is a build (rule 60)
                     ('run a profile for Listerners of Ball in the Bonzo Family with Lonzo Ball', True),
                     ('run a profile for listeners of New Heights', False),
                     ('run a profile on mountan dew', True),
                     ('build a profile for The Spiderwick Chronicles', False)],
        'counter': [('who watches love island?', False),
                    # a correction inside the conversation never lands
                    # on the build row (Mark at Bent Pixels, 2026-10-20)
                    ('No I am interested in showing a lot of aligned '
                     'creators...I am unclear why you keep bringing up '
                     'Salish matter', True),
                    ("No, that's not what I meant", True)],
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
