"""One table of intent classes for the ask log and the replay gate
(Prometheus rebuild stage 1, 2026-11-20).

The ask log names about ninety routes and sixty outcomes; the Reader
names twenty-two intents; the lanes name fifty-seven of themselves. A
drift report that groups by any one of those vocabularies is unreadable
and a shadow grade that uses another one cannot be compared with it.
This module is the one mapping all of them read:

    class_of(route, outcome, surface)   the class of a recorded or replayed ask
    class_of_intent(intent)             the class a Reader / Understanding intent lands in
    agrees(intent, route, outcome)      True when the intent's class is the route's class

The classes are families a person can read in a report:

    wizard        a Brand Partnership / journey / flywheel / attribution / twin intake turn
    build         a Profile IQ build, a cut, a tracker, a batch brief, an order queued
    read          a generated read (the measured read loop) or its quote
    page_analysis a written answer on the open page or from the library
    multi_ask     several questions in one message
    deck          a deck
    status        the reader's own runs and deliveries
    wayfinding    where a product, tab or function is
    dashboard_issue  the dashboard misbehaving
    capability    can we / is there a way to
    pricing       what does it cost
    sizing        how big is X (the sample check, the audience band)
    send_file     email me / download the file we hold
    insight       an open-ended insight ask on a subject
    catalog       do we have X / what is in the library
    definition    what does this number mean, the product facts
    provenance    where a number came from, unprojected, reconcile, US scope
    offer         a priced offer answered, accepted or declined
    short_reply   thanks, ok, a greeting, a pushback with nothing asked
    continuation  the same again, a re-run, a correction on the last turn
    data_floor    a window before January 1, 2021
    cancel        a cancel while something was armed
    order         an order the order gate took (build_order, referents)
    other         everything else

Pure text. No model, no S3, no Flask.
"""
from __future__ import annotations

CLASSES = (
    'wizard', 'build', 'read', 'page_analysis', 'multi_ask', 'deck', 'status', 'wayfinding',
    'dashboard_issue', 'capability', 'pricing', 'sizing', 'send_file', 'insight', 'catalog',
    'definition', 'provenance', 'offer', 'short_reply', 'continuation', 'data_floor', 'cancel',
    'order', 'other',
)

# route -> class. Exact route names first; the substring rules below
# catch the long tail (the ask log keeps about ninety distinct routes).
_ROUTE_EXACT = {
    # wizards
    'bpiq_wizard': 'wizard', 'bpiq': 'wizard', 'brand_partnership': 'wizard', 'jiq': 'wizard',
    'journey': 'wizard', 'digital_journey': 'wizard', 'fw': 'wizard', 'flywheel': 'wizard',
    'aiq': 'wizard', 'attribution': 'wizard', 'digital_twin': 'wizard', 'twin': 'wizard', 'intake': 'wizard',
    'roster_offer': 'wizard', 'journey_drilldown': 'wizard', 'journey_file': 'wizard', 'journey_dataset': 'wizard',
    # builds
    'profile_build': 'build', 'interpret': 'build', 'new_build': 'build', 'existing_match': 'build',
    'derive_cut': 'build', 'time_shifted_refresh': 'build', 'cut_needs_parent': 'build', 'subscriber_iq': 'build',
    'subiq': 'build', 'build_reroute': 'build', 'cut_reroute': 'build', 'cut_clarify': 'build',
    'subject_unresolved': 'build', 'question_first': 'build', 'build_fix': 'build', 'build_facts': 'build',
    'dataset_build': 'build', 'talent_list': 'build', 'bulk_build': 'build',
    # the generated read and its quote
    'reasoned_metrics': 'read', 'priced_read': 'read', 'derived_read': 'read', 'read_answer': 'read',
    'read_inflight_dedupe': 'read', 'read_replay': 'read', 'ledger_replay': 'read', 'rerun_window': 'read',
    'search_demand': 'read', 'cohort_churn': 'read', 'subiq_reroute': 'read', 'subiq_lookup': 'read',
    'subiq_library_answer': 'read', 'subiq_platform_only': 'read', 'where_watched': 'read',
    'service_subscribers': 'read', 'segment_sizing': 'read', 'read_brief': 'read', 'generate': 'read',
    'viewership': 'read', 'repeat_guard': 'read', 'audience_confirm': 'read', 'audience_referent': 'read',
    'referent_clarify': 'read', 'open_screen_confirm': 'page_analysis', 'screen_bind': 'page_analysis',
    # written answers
    'page_analysis': 'page_analysis', 'analysis': 'page_analysis', 'analyze': 'page_analysis',
    'prometheus/ask:analyze': 'page_analysis', 'kpi_definition': 'definition', 'panel_fact': 'definition',
    'sample_size': 'definition', 'sample_check': 'sizing', 'quantifiability_gate': 'page_analysis',
    'document': 'page_analysis', 'document_upload': 'page_analysis', 'study_record': 'definition',
    'help_writing': 'page_analysis', 'brief_terms': 'definition', 'pull_discussion': 'definition',
    'product_fact': 'definition', 'product_facts': 'definition', 'methodology': 'definition',
    'definitions': 'definition', 'definition': 'definition', 'us_only_definition': 'definition',
    'standing': 'definition', 'meta_check': 'definition', 'brand_coverage': 'catalog',
    # several questions
    'multi_ask': 'multi_ask',
    # decks
    'deck': 'deck', 'deck_conversion': 'deck', 'design_request': 'deck',
    # the reader's own work
    'status_lookup': 'status', 'status_check': 'status', 'status': 'status', 'deliverable_lookup': 'status',
    'workorder_status': 'status', 'workorder_eta': 'status', 'workorder_cancel': 'status',
    'thread_followups': 'status', 'spend_cap': 'status', 'spend_cap_precheck': 'status',
    # the dashboard itself
    'wayfinding': 'wayfinding', 'dashboard_behavior': 'dashboard_issue', 'dashboard_issue': 'dashboard_issue',
    # can we
    'capability': 'capability', 'capability_question': 'capability', 'cant_do': 'capability',
    'can_do': 'capability', 'data_capability': 'capability',
    # money
    'pricing': 'pricing', 'pricing_fact': 'pricing',
    # size
    'sizing': 'sizing', 'incidence': 'sizing', 'sizing_quote': 'sizing', 'sizing_fallback': 'sizing',
    # files
    'email_file': 'send_file', 'send_file': 'send_file', 'csv_download': 'send_file', 'download': 'send_file',
    'command': 'send_file', 'open_screen': 'send_file',
    # insight
    'insight_ask': 'insight', 'insight': 'insight',
    # library
    'catalog_lookup': 'catalog', 'catalog': 'catalog', 'catalog_exists': 'catalog', 'discovery': 'catalog',
    'subiq_library': 'catalog',
    # numbers
    'provenance': 'provenance', 'number_provenance': 'provenance', 'unprojected': 'provenance',
    'reconcile': 'provenance', 'us_scope': 'provenance',
    # offers
    'offer_declined': 'offer', 'offer_answer': 'offer', 'offer_acceptance': 'offer',
    'priced_offer_acceptance': 'offer', 'offer': 'offer', 'year_package': 'offer',
    # small talk and corrections
    'short_reply': 'short_reply', 'chat': 'short_reply', 'correction': 'short_reply', 'match_answer': 'short_reply',
    'confirm_answer': 'short_reply',
    # the turn gate
    'continuation': 'continuation', 'turn_gate': 'continuation', 'rerun': 'continuation',
    # gates
    'data_floor': 'data_floor', 'intake_cancel': 'cancel',
    # orders
    'build_order': 'order', 'order': 'order', 'referents': 'order', 'subject_set': 'order',
    'talent_list_acceptance': 'order',
}

# (substring in route) -> class, checked in order after the exact table.
_ROUTE_SUBSTR = (
    ('bpiq', 'wizard'), ('jiq', 'wizard'), ('journey', 'wizard'), ('flywheel', 'wizard'), ('fw_', 'wizard'),
    ('aiq', 'wizard'), ('attribution', 'wizard'), ('twin', 'wizard'), ('intake', 'wizard'),
    ('subiq', 'read'), ('read', 'read'), ('churn', 'read'), ('metrics', 'read'),
    ('build', 'build'), ('profile', 'build'), ('cut', 'build'), ('draft', 'build'),
    ('deck', 'deck'), ('status', 'status'), ('deliver', 'status'), ('workorder', 'status'),
    ('capab', 'capability'), ('pric', 'pricing'), ('siz', 'sizing'), ('incidence', 'sizing'),
    ('email', 'send_file'), ('file', 'send_file'), ('download', 'send_file'),
    ('insight', 'insight'), ('catalog', 'catalog'), ('definition', 'definition'), ('fact', 'definition'),
    ('provenance', 'provenance'), ('offer', 'offer'), ('reply', 'short_reply'), ('continu', 'continuation'),
    ('floor', 'data_floor'), ('cancel', 'cancel'), ('order', 'order'), ('analysis', 'page_analysis'),
    ('analyze', 'page_analysis'),
)

# outcome -> class when the route alone is too coarse (the analyze
# surface's default route is page_analysis whatever lane answered).
_OUTCOME_HINT = {
    'turn': 'wizard', 'intake': 'wizard', 'panel_offer': 'read', 'year_package_offer': 'offer',
    'offer_declined': 'offer', 'quoted': 'read', 'landed': 'read', 'queued': None,
    'new_build': 'build', 'existing_match': 'build', 'derive_cut': 'build', 'time_shifted_refresh': 'build',
    'cut_needs_parent': 'build', 'subscriber_iq': 'build', 'batch_draft': 'build', 'explicit_build_order': 'build',
    'lane_noun_order': 'order', 'referent_resolved': 'order', 'dropped': 'cancel', 'cancelled': 'cancel',
    'before': 'data_floor', 'crosses': 'data_floor',
}

# Reader / Understanding intent -> class.
_INTENT_CLASS = {
    'exists': 'catalog', 'size': 'sizing', 'status': 'status', 'run_pending': 'order',
    'build_profile': 'build', 'cut': 'build', 'tracker': 'build',
    'journey': 'wizard', 'valuation': 'wizard', 'campaign': 'wizard', 'twin': 'wizard',
    'analyze_page': 'page_analysis', 'analyze': 'read', 'compare': 'read',
    'capability': 'capability', 'dashboard_issue': 'dashboard_issue', 'command': 'send_file',
    'deck': 'deck', 'definition': 'definition', 'pricing': 'pricing', 'decline': 'offer', 'chat': 'short_reply',
    # the lane intents the Understanding adds (stage 2)
    'order': 'order', 'pushback': 'short_reply', 'continuation_rerun': 'continuation',
    'continuation_correction': 'continuation', 'reader_will_share': 'short_reply', 'study_question': 'definition',
    'send_file_order': 'send_file', 'wayfinding': 'wayfinding', 'insight_ask': 'insight',
    'build_fix': 'build', 'build_facts': 'build', 'data_floor': 'data_floor', 'cancel': 'cancel',
    'multi_ask': 'multi_ask', 'provenance': 'provenance', 'offer_reply': 'offer',
}

# Classes that answer from the same machinery: a reading in one is not
# a disagreement with a route in the other.
_NEAR = {
    'read': {'page_analysis', 'multi_ask', 'insight', 'sizing'},
    'page_analysis': {'read', 'multi_ask', 'insight', 'definition'},
    'insight': {'read', 'page_analysis'},
    'multi_ask': {'read', 'page_analysis'},
    'build': {'order', 'wizard'},
    'order': {'build', 'wizard'},
    'wizard': {'build', 'order'},
    'status': {'send_file'},
    'send_file': {'status'},
    'sizing': {'read'},
    'definition': {'page_analysis', 'provenance'},
    'provenance': {'definition'},
    'offer': {'short_reply', 'read'},
    'short_reply': {'offer', 'continuation'},
    'continuation': {'short_reply', 'read'},
}


def _norm(s):
    return str(s or '').strip().lower()


def class_of(route, outcome='', surface=''):
    """The class of one ask from the ask log's route / outcome / surface."""
    r, o, s = _norm(route), _norm(outcome), _norm(surface)
    if r in _ROUTE_EXACT:
        cls = _ROUTE_EXACT[r]
        # the analyze surface's default route is page_analysis whatever
        # answered; an outcome that names a family is the better read
        if cls == 'page_analysis' and o in _OUTCOME_HINT and _OUTCOME_HINT[o]:
            return _OUTCOME_HINT[o]
        return cls
    for sub, cls in _ROUTE_SUBSTR:
        if sub in r:
            return cls
    if o in _OUTCOME_HINT and _OUTCOME_HINT[o]:
        return _OUTCOME_HINT[o]
    if s == 'interpret':
        return 'build'
    if s == 'deck':
        return 'deck'
    if r in ('', 'unknown') and s == 'analyze':
        return 'page_analysis'
    return 'other'


def class_of_intent(intent):
    """The class a Reader or Understanding intent lands in ('other' when unknown)."""
    return _INTENT_CLASS.get(_norm(intent), 'other')


def near(a, b):
    """True when two classes are the same family or neighbours."""
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a == b or b in _NEAR.get(a, set()))


def agrees(intent, route, outcome='', surface='', *, strict=False):
    """True when the intent's class is the route's class (neighbouring
    classes count unless strict)."""
    ci = class_of_intent(intent)
    cr = class_of(route, outcome, surface)
    if ci == 'other' or cr == 'other':
        return False
    return ci == cr if strict else near(ci, cr)


__all__ = ['CLASSES', 'class_of', 'class_of_intent', 'near', 'agrees']
