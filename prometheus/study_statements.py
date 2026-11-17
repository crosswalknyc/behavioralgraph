"""What Prometheus says about a published study, as fixed text.

One sheet per decision of record (Cara Dorfman at Amazon, 2026-11-16).
Her questions about the Amazon vs direct issue of The Read were answered
by fresh reads that drew supporting figures the issue never carried:
month-start account counts that implied a different churn rate, a
monthly-only curve, survival past day 90. A question about a published
study is answered from the study, and where the study is silent the
answer says so.

This module is the one place those answers are written. Every topic is
a pattern that recognises the question and a statement that answers it.
A statement is fixed text approved for the record; a figure in it is a
placeholder filled from the record itself (``{churn_a}`` is the Amazon
path's churn, ``{churn_d}`` direct), so no figure is typed twice and no
statement is ever written by a model. ``carried`` says whether the study
holds what was asked ('yes'), holds part of it ('part'), or does not
hold it ('no').

Adding a topic: write the statement, have it approved, add it here with
the words that ask for it, and add the question to
scripts/test_pm_study_record.py.
"""
from __future__ import annotations

# The session-credit statement Jenna approved on 2026-11-16, word for word.
# ``{service}`` is the service the question names (one of the record's own).
SESSION_CREDIT = (
    "Prime Video. The session is counted in the app where it happens. The title played is still credited "
    "to the title, and the subscription is counted as a {service} subscription on the Amazon path, because "
    "the signup and billing opened in Amazon's store.")

AMAZON_VS_DIRECT = {
    'record': 'the-read-amazon-vs-direct',
    # how a question names this study in its own words
    'names': (
        r"\bamazon(?:\s+prime\s+video)?(?:\s+channels)?\s+(?:vs\.?|versus)\s+direct\b",
        r"\b(?:prime\s+video\s+)?channels\s+(?:vs\.?|versus)\s+(?:direct\b|the\s+service'?s\s+own\b)",
        r"\bcancel\s+1\.7x\s+as\s+often\b",
        r"\b(?:through|via)\s+amazon\s+(?:vs\.?|versus)\s+direct\b",
        r"\bamazon[- ]path\b[^.?!]{0,80}\bdirect\b",
        r"\bdirect\b[^.?!]{0,80}\bamazon[- ]path\b",
    ),
    # a name alone is not enough ("Nike on Amazon vs direct"): the ask is about subscriptions too
    'context': r"\b(?:subscri\w+|sign-?ups?|signed\s+up|churn\w*|surviv\w+|cancel\w*|channels|stream\w+|retention|starts)\b",
    # figures only this study prints: either of a pair alone is not a sign, both together are
    'pairs': (('7.4', '4.3'), ('38.2', '24.6'), ('41.8', '29.0'), ('18.9', '11.3'), ('92.3', '68.3'), ('5.6', '7.9')),
    'rounded': ('72/55/45', '82/70/61'),
    'topics': (
        {'id': 'active_rule', 'title': 'Active status', 'carried': 'yes',
         'when': (r"\bbilling(?:\s+status)?\s+or\s+(?:by\s+)?(?:having\s+)?(?:at\s+least\s+one\s+)?(?:viewing\s+)?sessions?\b|"
                  r"\bsessions?\s+or\s+billing\b|\bcounted\s+as\s+active\b|\bactive\s+is\s+billing\b|"
                  r"\bwhich\s+rule\s+produced\b|\bactive\s+in\s+a\s+month\b"),
         'drops': ('no_session',),
         'text': ("Billing. In the published study a subscription is active while it is still running. A cancel is dated "
                  "to the day the subscriber asks to cancel, not the day paid access ends. Viewing plays no part, so a "
                  "paying subscriber with no sessions in a month is still active.")},
        {'id': 'no_session', 'title': 'No sessions in a month', 'carried': 'yes',
         'when': (r"\bno\s+(?:[\w+]+\s+)?sessions?\b[^.?!]*\b(?:churned|cancell?ed|inactive|gone)\b|"
                  r"\bcounts?\s+as\s+churned\b"),
         'text': ("No. A paying subscriber with no sessions in a month is still active in the published study. Only a "
                  "cancel request ends a subscription there.")},
        {'id': 'cohort_basis', 'title': 'Cohorts', 'carried': 'yes',
         'when': r"\bbuilt\s+from\b|\bnew\s+signups?\s+in\s+each\s+month\b|\ball\s+accounts\s+active\s+in\s+each\s+month\b",
         'text': ("New signups. Each survival figure is the share of new starts on that path still active 30, 60 and 90 "
                  "days after each subscription's own day 0: {d30_a}%, {d60_a}% and {d90_a}% through Amazon, {d30_d}%, "
                  "{d60_d}% and {d90_d}% direct. The issue prints them rounded, as {curve_a} and {curve_d}.")},
        {'id': 'reactivation', 'title': 'Reactivations', 'carried': 'part',
         'when': (r"\bre-?enters?\b|\breactivat\w*|\b(?:cancels?|cancell?ed)\s+and\s+(?:later\s+|then\s+)?"
                  r"(?:returns?|comes?\s+back|rejoins?|resubscribes?)\b|\breturning\s+accounts?\b"),
         'text': ("The published study does not say how an account that cancels and later returns is treated inside the "
                  "survival curves. It reports comebacks on their own line: {winback_d}% of direct cancellers and "
                  "{winback_a}% of Amazon cancellers were back at the same service within 90 days.")},
        {'id': 'session_credit', 'title': 'Prime Video app viewing', 'carried': 'yes',
         'when': (r"\binside\s+the\s+prime\s+video\s+app\b|\bin\s+the\s+prime\s+video\s+app\b|"
                  r"\b(?:credit\w*|attribut\w+)\b[^.?!]*\bsession\b|\bsession\b[^.?!]*\b(?:credit\w*|attribut\w+)\b"),
         'text': SESSION_CREDIT},
        {'id': 'amazon_paths', 'title': 'What via Amazon includes', 'carried': 'no',
         'when': (r"\bfire\s+tv\s+app-?store\b|\bapp-?store\s+billing\b|\bamazon\s+retail\b|\balexa\b|"
                  r"\bwhat\s+\W?via\s+amazon\W?\s+includes\b"),
         'drops': ('device_coverage',),
         'text': ("In the published study, via Amazon means the signup and billing opened inside Amazon's store, Prime "
                  "Video Channels: {starts_a} starts. The study does not split that path further, so it carries no "
                  "separate counts for Fire TV app-store billing, Amazon retail or Alexa.")},
        {'id': 'device_coverage', 'title': 'Device coverage', 'carried': 'no',
         'when': (r"\b(?:connected\s+tv|ctv|fire\s+tv|roku|smart\s+tvs?|devices?)\b[^?]*\b(?:observ\w+|cover\w*|measur\w+|compared\s+with)\b|"
                  r"\b(?:observ\w+|cover\w*|measur\w+)\b[^?]*\b(?:connected\s+tv|ctv|smart\s+tvs?|devices?)\b"),
         'text': ("The published study does not describe how signups and viewing are observed by device, so that cannot "
                  "be answered from it.")},
        {'id': 'reconcile', 'title': 'The churn figures', 'carried': 'no',
         'when': (r"\breconcile\b|\bimpl(?:y|ies|ied)\b[^.]*\b(?:churn|cancels?)\b|\b(?:does|do)\s+not\s+(?:tie|match|add\s+up)\b|"
                  r"\bmonth-?start\b|\bopening\s+month\b|\bactive\s+accounts?\s+(?:of|at)\b"),
         'drops': ('churn_basis',),
         'text': ("The published study does not carry month-start active account counts. A churn rate worked out from "
                  "counts of that kind will not tie to its rates: the churn base is every active subscription billed on a path, "
                  "and the new starts are only one part of that base. The published rates are {churn_a}% through Amazon "
                  "and {churn_d}% direct, each the subscriptions cancelled in a month over the subscriptions active on "
                  "that path at the start of that month, averaged across the twelve months, with a cancel dated to the "
                  "cancel request.")},
        {'id': 'churn_basis', 'title': 'Headline churn', 'carried': 'yes',
         'when': (r"\ball\s+active\s+accounts\b|\bonly\s+new\s+signups\b|\bweighted\s+by\b|\bacross\s+all\s+active\b|"
                  r"\bchurn\b[^?]*\b(?:calculated|computed|worked\s+out|defined|definition|formula)\b"),
         'text': ("All active subscriptions on each path, not new signups only. Monthly churn is subscriptions cancelled "
                  "in a month over subscriptions active on that path at the start of that month, averaged across the "
                  "twelve months, with a cancel dated to the cancel request: {churn_a}% through Amazon and {churn_d}% "
                  "direct. The base is every active subscription billed on that path, including ones that started "
                  "before {window_start_month}. The published definition is an average across the twelve months. The "
                  "study states no other weighting.")},
        {'id': 'samplers', 'title': 'Samplers', 'carried': 'yes',
         'when': r"[\"\u201c]gone[\"\u201d]|\bgone\W+mean\b|\bone\s+title\s+and\s+(?:are\s+|were\s+)?gone\b[^?]*\bmean\b",
         'text': ("Cancelled. The figure is the share of starts that played a single title and cancelled within 35 days "
                  "of their own day 0, measured to the cancel request: {samplers_a}% through Amazon and {samplers_d}% "
                  "direct. It is not a count of accounts that stopped watching.")},
        {'id': 'monthly_only', 'title': 'Monthly plans only', 'carried': 'no',
         'when': (r"\bmonthly[- ]plans?\s+only\b|\bmonthly[- ]only\b|\bmonth[- ]to[- ]month\s+(?:plans?\s+)?only\b|"
                  r"\bonly\s+(?:the\s+)?monthly\s+plans?\b|\bbundle\s+signups?\b"),
         'drops': ('plan_terms', 'table'),
         'text': ("The published study does not carry a monthly-only curve, for the paths or by service. Its survival "
                  "figures cover every start on each path, whatever the plan. It splits each path two ways, month to "
                  "month or annual plans and bundles: direct is {m2m_d}% month to month and {annual_d}% annual plans and "
                  "bundles, Amazon is {m2m_a}% and {annual_a}%. On that split, bundle signups sit with annual plans, "
                  "outside month to month.")},
        {'id': 'plan_terms', 'title': 'Plan term', 'carried': 'yes',
         'when': (r"\b(?:annual|prepaid)\b[^?]*\bplans?\b|\bplan\s+terms?\b|\bplan\s+mix\b|\bmonth[- ]to[- ]month\b"),
         'text': ("The survival figures cover every start on each path, whatever the plan, so annual plans are in them. "
                  "The study splits each path two ways, month to month or annual plans and bundles: direct is {m2m_d}% "
                  "month to month and {annual_d}% annual plans and bundles, Amazon is {m2m_a}% and {annual_a}%. It has "
                  "no separate prepaid line.")},
        {'id': 'first_year_months', 'title': 'First-year value', 'carried': 'part',
         'when': (r"\bfirst-?year\s+months\b|\bmonths\s+kept\b|\bmonthly\s+survival\s+values?\b|"
                  r"\bmonths?\s+\d+\s+(?:through|to)\s+(?:month\s+)?\d+\b|\b5\.6\s+and\s+7\.9\b|\b7\.9\s+and\s+5\.6\b"),
         'drops': ('past_day_90',),
         'text': ("The {months_a} and {months_d} are the average paid months held in the first twelve months after "
                  "signup, through Amazon and direct. The published study carries survival at day 30, day 60 and day 90 "
                  "only. It does not carry survival values for later months.")},
        {'id': 'past_day_90', 'title': 'Survival past day 90', 'carried': 'no',
         'when': r"\bday\s+(?:1\d{2}|[23]\d{2})\b|\b(?:after|past|beyond)\s+day\s+90\b|\b(?:6|six|12|twelve)[- ]month\s+survival\b",
         'text': ("The published study carries survival at day 30, day 60 and day 90 only. It does not carry survival "
                  "past day 90.")},
        {'id': 'trials', 'title': 'Free trials and promotions', 'carried': 'no',
         'when': (r"\bfree\s+trials?\b|\bdiscounted\s+first\s+months?\b|\bpromotion(?:s|al)?\b|\bday\s+0\s+starts?\b|"
                  r"\bintro(?:ductory)?\s+(?:price|offer)s?\b"),
         'text': ("The published study does not carry the share of starts that began on a free trial or a discounted "
                  "first month, and it does not state whether day 0 is the start of a trial or the first full-price "
                  "payment. What it states is that it follows new paid US subscriptions, each from its own day 0.")},
        {'id': 'tiers', 'title': 'Plan tier mix', 'carried': 'no',
         'when': r"\bplan\s+tiers?\b|\btier\s+mix\b|\bmix\s+of\s+(?:plan\s+)?tiers\b|\bwith\s+ads\b|\bad-?free\b|\bad[- ]supported\b",
         'text': ("The published study does not carry a plan tier mix for any service. Its only plan split is month to "
                  "month against annual plans and bundles.")},
        {'id': 'next_services', 'title': 'Where cancellers go', 'carried': 'part',
         'when': (r"\bwhich\s+services\s+do\s+they\s+start\b|\btop\s+(?:ten|10|five|5)\b|"
                  r"\bwhere\s+(?:the\s+|do\s+)?(?:amazon[- ]path\s+|amazon\s+|direct\s+)?cancell?ers\s+go\b|\bgo\s+next\b"),
         'text': ("The published study carries the share only: {switch_a}% of Amazon cancellers and {switch_d}% of direct "
                  "cancellers start another streaming service within 30 days. It does not name the services they start "
                  "or the path they sign up through.")},
        {'id': 'direct_paths', 'title': 'What direct includes', 'carried': 'no',
         'when': (r"\bcounted\s+as\s+\W?direct\b|\bevery\s+signup\s+path\b|\bapple\s+app\s+store\b|\bgoogle\s+play\b|"
                  r"\broku\s+billing\b|\bthe\s+roku\s+channel\b|\byoutube\s+primetime\b|\bcarrier\b|\bpartner\s+bundles?\b|"
                  r"\bwhat\s+\W?direct\W?\s+includes\b"),
         'drops': ('device_coverage',),
         'text': ("In the published study, direct means the subscription was signed up for and is paid on the service's "
                  "own site or app: {starts_d} starts. The study does not split direct into further signup paths, so it "
                  "carries no counts by app store, device billing, carrier, retailer or partner bundle.")},
        {'id': 'sample', 'title': 'Counts behind the curves', 'carried': 'no', 'kind': 'sample', 'drops': ('table',),
         'when': (r"\bpanell?ists?\b|\bsample\s+sizes?\b|\bunprojected\b|\bnot\s+projected\b|\braw\s+(?:counts?|sample)\b|"
                  r"\bhow\s+many\s+(?:people|respondents|accounts)\s+(?:sit\s+|are\s+)?behind\b"),
         'text': ("The published study reports projected US subscriptions: {starts_a} on the Amazon path and {starts_d} "
                  "direct. It does not publish the counts behind the projection.")},
        {'id': 'table', 'title': 'By service', 'carried': 'yes', 'kind': 'table', 'drops': ('plan_terms',),
         'when': (r"\bper[- ]service\b|\bby\s+service\b|\bservice\s+by\s+service\b|\beach\s+(?:of\s+the\s+)?(?:ten\s+|10\s+)?services?\b|"
                  r"\b(?:the\s+)?(?:same\s+)?ten\s+services\b"),
         'text': ''},
    ),
}

SHEETS = {AMAZON_VS_DIRECT['record']: AMAZON_VS_DIRECT}

# What a reply says when a part matches nothing the record holds.
NOT_MATCHED = "I could not match this to a figure or a definition in the published study, so I have no answer to give from it."
NOT_FIGURES = "Not figures from the published study: {figures}."

__all__ = ['SHEETS', 'AMAZON_VS_DIRECT', 'SESSION_CREDIT', 'NOT_MATCHED', 'NOT_FIGURES']
