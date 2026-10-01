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
        decisions.append({
            'id': f'correction_{cid}',
            'decided': now.strftime('%Y-%m-%d'),
            'statement': (
                f"{correction} (operator correction, "
                f"{now.strftime('%Y-%m-%d')}; supersedes the prior "
                f"delivered answer to: "
                f"{str(ctx.get('question') or '')[:140]})"),
            'match_terms': _match_terms(ctx.get('subject'),
                                        ctx.get('question')),
        })
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
