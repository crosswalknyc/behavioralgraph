"""Correction loop on the per-ask Prometheus question emails.

2026-10-01 (Jenna: approve of "wire your email channel into the
learning loop"). Every question email carries a signed "Correct this
answer" link. Submitting a correction:

1. retracts the delivered read from replay (the ledger index rows go
   away; the entry objects stay on S3 as audit), so no future ask
   serves the corrected numbers again, and
2. banks the correction as a standing decision of record in
   system/prometheus_decisions.json, so every future prompt that
   matches the subject or question carries it as binding guidance.

The link is HMAC-signed with the same secret the hostmap approval
links use (env HOSTMAP_APPROVAL_SECRET, S3 fallback), so holding the
email is holding the authority - no dashboard session required.
Everything here is fail-safe: a miss returns None / False and the
email still sends without the link.
"""

import hashlib
import hmac
import json
import os
import re
import traceback
import uuid
from datetime import datetime, timezone

S3_BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')
S3_REGION = os.environ.get('S3_REGION', 'us-east-2')
CTX_PREFIX = 'system/prometheus_corrections/ctx/'
LOG_PREFIX = 'system/prometheus_corrections/log/'
DECISIONS_KEY = 'system/prometheus_decisions.json'
BASE_URL = (os.environ.get('PM_CORRECT_BASE_URL')
            or 'https://dashboard.crosswalknyc.com').rstrip('/')

_STOP = {'the', 'a', 'an', 'and', 'of', 'for', 'with', 'this', 'that',
         'these', 'those', 'what', 'which', 'how', 'much', 'many',
         'does', 'do', 'you', 'see', 'any', 'carry', 'over', 'last',
         'past', 'months', 'month', 'days', 'year', 'years', 'their',
         'there', 'about', 'audience', 'viewers', 'people'}


def _s3():
    import boto3
    return boto3.client('s3', region_name=S3_REGION)


def _secret():
    try:
        from migration.hostmap_ingest import approval_secret
        return approval_secret() or ''
    except Exception:
        return ''


def sign(cid):
    sec = _secret()
    if not sec:
        return ''
    msg = f"pmcorrect:{cid}".encode('utf-8')
    return hmac.new(sec.encode('utf-8'), msg, hashlib.sha256).hexdigest()


def verify(cid, token):
    expected = sign(cid)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, str(token))


def stash_context(username, question, answer, subject=''):
    """Persist the Q&A the email shows and return (cid, url) for the
    correction link. (None, '') on any failure."""
    try:
        cid = uuid.uuid4().hex[:12]
        tok = sign(cid)
        if not tok:
            return None, ''
        _s3().put_object(
            Bucket=S3_BUCKET, Key=f"{CTX_PREFIX}{cid}.json",
            Body=json.dumps({
                'cid': cid, 'status': 'open',
                'username': str(username or '')[:80],
                'question': str(question or '')[:2000],
                'answer': str(answer or '')[:6000],
                'subject': str(subject or '')[:120],
                'ts': datetime.now(timezone.utc).strftime(
                    '%Y-%m-%dT%H:%M:%SZ'),
            }, indent=1).encode('utf-8'),
            ContentType='application/json')
        return cid, f"{BASE_URL}/prometheus/correct?id={cid}&t={tok}"
    except Exception:
        traceback.print_exc()
        return None, ''


def load_context(cid):
    try:
        body = _s3().get_object(
            Bucket=S3_BUCKET,
            Key=f"{CTX_PREFIX}{cid}.json")['Body'].read()
        return json.loads(body)
    except Exception:
        return None


def _match_terms(subject, question):
    terms = []
    subj = str(subject or '').strip()
    if subj:
        terms.append(subj.lower()[:60])
        for t in re.findall(r'[a-z0-9]{3,}', subj.lower()):
            if t not in _STOP and t not in terms:
                terms.append(t)
    toks = [t for t in re.findall(r'[a-z0-9]{5,}',
                                  str(question or '').lower())
            if t not in _STOP]
    toks.sort(key=len, reverse=True)
    for t in toks:
        if t not in terms:
            terms.append(t)
        if len(terms) >= 12:
            break
    return terms[:12]


def _scrub(text):
    try:
        import prometheus_analysis as pma
        return pma.scrub_user_text(str(text or '').strip())
    except Exception:
        return str(text or '').strip()


# ---------------------------------------------------------------- distill
#
# Jenna, 2026-10-02 (verbatim, typos cleaned): "I don't mean for what I
# typed in to be the served answer should it be asked again, but what
# to do to correct it." A correction is an instruction about behavior
# ("ask which influencers he means, then answer the actual question"),
# not replacement copy. The operator's text is distilled into one
# general rule Prometheus follows on every ask of the same shape, with
# trigger terms that describe that shape rather than the words that
# happened to be in this one question ("report", "three").

_DISTILL_SYSTEM = (
    "You turn an operator's correction of one chat answer into a "
    "standing rule for the assistant that gave it. Return JSON only:\n"
    "{\"instruction\": str, \"trigger_terms\": [str], "
    "\"trigger_regex\": str|null, \"scope\": \"general\"|\"subject\"}\n"
    "- instruction: ONE or two sentences, imperative, addressed to the "
    "assistant, describing what to DO on every future ask of this "
    "shape. Never a replacement answer, never the user's name, never "
    "the specific question text. Example: 'When an ask points at "
    "creators, brands, or titles without naming them (these three "
    "creators, both shows), ask which ones by name first, then answer "
    "the question that was actually asked.'\n"
    "- trigger_terms: 3 to 8 short lowercase phrases that mark the "
    "SHAPE of asks this rule applies to (e.g. 'these three', 'which of "
    "the', 'those brands'). Never generic words like report, review, "
    "data, three, list, categories on their own.\n"
    "- trigger_regex: an optional Python regex (case-insensitive) that "
    "matches the ask shape, or null.\n"
    "- scope: 'subject' only when the correction is about one named "
    "subject's facts (a number, a date, a platform); otherwise "
    "'general'.\n"
    "Plain English. No em dashes. No internal vocabulary.")

_GENERIC_TERMS = {
    'report', 'reports', 'review', 'reviews', 'prepare', 'preapre',
    'data', 'numbers', 'list', 'lists', 'categories', 'category',
    'three', 'two', 'four', 'five', 'which', 'actually', 'really',
    'these', 'those', 'their', 'about', 'should', 'would', 'could',
    'please', 'question', 'answer', 'asked', 'wrong', 'right',
    'creators', 'influencers', 'brands', 'shows', 'titles', 'people',
    'product', 'products', 'purchases', 'purchase', 'influence',
}


def _fallback_distill(question, correction, subject):
    """No model reachable: a general instruction built from the
    correction text, and trigger terms that are the ask's distinctive
    shape words (never the generic ones)."""
    corr = str(correction or '').strip().rstrip('.')
    q = str(question or '')
    instruction = corr
    low = corr.lower()
    if low.startswith(('wrong answer', 'wrong.', 'wrong,', 'incorrect',
                       'no.', 'no,', 'bad answer')):
        rest = re.sub(r'^(wrong answer|wrong|incorrect|no|bad answer)'
                      r'[\s.,:;-]*', '', corr, flags=re.I).strip()
        instruction = rest or corr
    _base = {'asked': 'ask', 'answered': 'answer', 'given': 'give',
             'said': 'say', 'told': 'tell', 'compared': 'compare',
             'listed': 'list', 'used': 'use', 'pulled': 'pull',
             'built': 'build', 'shown': 'show', 'offered': 'offer',
             'checked': 'check', 'named': 'name', 'read': 'read',
             'run': 'run', 'returned': 'return', 'included': 'include',
             'quoted': 'quote', 'charged': 'charge'}

    def _imperative(m):
        verb = m.group(2).lower()
        return 'On an ask like this, ' + _base.get(verb, verb)

    instruction = re.sub(r'\b(it|prometheus)\s+should\s+have\s+(\w+)',
                         _imperative, instruction, count=1, flags=re.I)
    instruction = re.sub(r'\bhe\b|\bshe\b', 'the user', instruction,
                         flags=re.I)
    instruction = re.sub(r'\bhim\b|\bher\b', 'the user', instruction,
                         flags=re.I)
    if not instruction.endswith('.'):
        instruction += '.'
    terms = []
    ql = q.lower()
    for rx in (r'\b(?:these|those|both|all)\s+(?:\w+\s+)?'
               r'(?:creators?|influencers?|brands?|shows?|titles?|'
               r'podcasts?|profiles?|audiences?)\b',
               r'\bwhich of (?:the|these|those)\b',
               r'\b(?:compare|versus|vs\.?|against)\b'):
        for m in re.finditer(rx, ql):
            t = ' '.join(m.group(0).split())
            if t and t not in terms:
                terms.append(t)
    if subject and str(subject).strip():
        s = str(subject).strip().lower()
        s_toks = [w for w in re.findall(r'[a-z0-9]{3,}', s)
                  if w not in _STOP and w not in _GENERIC_TERMS]
        # Only a subject that is a real name earns a term; a run of
        # ordinary words (the defect shape) does not.
        if s_toks and len(s_toks) >= max(1, len(s.split()) - 1):
            terms.append(s[:60])
    for t in _match_terms('', q):
        if t not in _GENERIC_TERMS and t not in terms and len(t) >= 6:
            terms.append(t)
        if len(terms) >= 8:
            break
    return {'instruction': instruction[:400], 'trigger_terms': terms[:8],
            'trigger_regex': None,
            'scope': 'subject' if terms and subject and
            str(subject).strip().lower() in terms else 'general'}


def distill_correction(question, answer, correction, subject='',
                       call_fn=None):
    """The behavioral rule behind one correction. ``call_fn(system,
    user) -> str`` is injectable for tests; the default is the shared
    model client. Falls back to a deterministic distillation when the
    model is unreachable or returns nothing usable. Never raises."""
    out = None
    try:
        if call_fn is None:
            try:
                import claude_client as _cc

                def call_fn(sys_p, usr_p):
                    return _cc.claude_reason_json(
                        system=sys_p, user=usr_p, max_tokens=500,
                        temperature=0.0, usage_tag='pm_correction')
            except Exception:
                call_fn = None
        if call_fn is not None:
            user_p = (
                f"QUESTION THE USER ASKED:\n{str(question or '')[:800]}\n\n"
                f"ANSWER THAT WENT OUT:\n{str(answer or '')[:1200]}\n\n"
                f"SUBJECT THE ANSWER ASSUMED: {str(subject or '')[:120]}\n\n"
                f"OPERATOR CORRECTION:\n{str(correction or '')[:1200]}\n\n"
                "Return the JSON.")
            raw = str(call_fn(_DISTILL_SYSTEM, user_p) or '').strip()
            if raw:
                m = re.search(r'\{.*\}', raw, re.S)
                if m:
                    cand = json.loads(m.group(0))
                    instr = str(cand.get('instruction') or '').strip()
                    terms = [str(t).strip().lower()[:60]
                             for t in (cand.get('trigger_terms') or [])
                             if str(t).strip()]
                    terms = [t for t in terms
                             if t and t not in _GENERIC_TERMS][:8]
                    rx = cand.get('trigger_regex')
                    rx = str(rx).strip() if rx else None
                    if rx:
                        try:
                            re.compile(rx, re.I)
                        except re.error:
                            rx = None
                    if instr and len(instr) >= 12:
                        out = {'instruction': instr[:400],
                               'trigger_terms': terms,
                               'trigger_regex': rx,
                               'scope': ('subject'
                                         if str(cand.get('scope') or '')
                                         .lower() == 'subject'
                                         else 'general')}
    except Exception:
        traceback.print_exc()
        out = None
    if out is None:
        out = _fallback_distill(question, correction, subject)
    out['instruction'] = out['instruction'].replace('\u2014', '-')
    return out


def apply_correction(cid, correction):
    """Bank one correction: audit log, ledger retract, standing
    decision. Idempotent per cid. Returns a summary dict or None when
    the context is missing."""
    ctx = load_context(cid)
    if not isinstance(ctx, dict):
        return None
    if ctx.get('status') == 'applied':
        return {'already': True, 'retracted': 0}
    correction = _scrub(correction)[:1200].replace('\u2014', '-')
    if not correction:
        return {'empty': True, 'retracted': 0}
    now = datetime.now(timezone.utc)
    retracted = 0
    try:
        import insights_ledger as il
        retracted = il.retract(subject=ctx.get('subject') or None,
                               question=ctx.get('question') or None)
    except Exception:
        traceback.print_exc()
    # Standing decision of record. Small doc, rare writes; plain
    # read-modify-write is proportionate.
    try:
        s3 = _s3()
        try:
            doc = json.loads(s3.get_object(
                Bucket=S3_BUCKET,
                Key=DECISIONS_KEY)['Body'].read())
        except Exception:
            doc = {'decisions': []}
        decisions = doc.get('decisions') or []
        rule = distill_correction(ctx.get('question'), ctx.get('answer'),
                                  correction, ctx.get('subject'))
        entry = {
            'id': f'correction_{cid}',
            'decided': now.strftime('%Y-%m-%d'),
            # The statement is what Prometheus DOES next time, never
            # the operator's text served back as the answer.
            'statement': (f"{rule['instruction']} (operator "
                          f"correction, {now.strftime('%Y-%m-%d')})"),
            'match_terms': list(rule.get('trigger_terms') or []),
            'scope': rule.get('scope') or 'general',
            'operator_note': correction[:600],
            'example_question': str(ctx.get('question') or '')[:200],
        }
        if rule.get('trigger_regex'):
            entry['trigger'] = rule['trigger_regex']
        if entry['scope'] == 'subject' and ctx.get('subject'):
            entry['subject'] = str(ctx.get('subject'))[:120]
        decisions.append(entry)
        doc['decisions'] = decisions[-120:]
        doc['updated'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        s3.put_object(Bucket=S3_BUCKET, Key=DECISIONS_KEY,
                      Body=json.dumps(doc, indent=1).encode('utf-8'),
                      ContentType='application/json')
    except Exception:
        traceback.print_exc()
    # Audit trail + consume the context.
    try:
        rec = dict(ctx)
        rec.update({'status': 'applied', 'correction': correction,
                    'retracted': retracted,
                    'applied_at': now.strftime('%Y-%m-%dT%H:%M:%SZ')})
        s3 = _s3()
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"{LOG_PREFIX}{now.strftime('%Y-%m-%d')}/{cid}.json",
            Body=json.dumps(rec, indent=1).encode('utf-8'),
            ContentType='application/json')
        s3.put_object(
            Bucket=S3_BUCKET, Key=f"{CTX_PREFIX}{cid}.json",
            Body=json.dumps(rec, indent=1).encode('utf-8'),
            ContentType='application/json')
    except Exception:
        traceback.print_exc()
    return {'already': False, 'retracted': retracted}
