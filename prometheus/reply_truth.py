"""A reply says a thing happened only when there is a record of it
(Jenna 2026-11-11: "these errors cannot happen").

Armine at Lionsgate, 2026-11-11 21:09 UTC. Her question ended
"Summarize in powerpoint". No deck was built and no mail was sent; the
read that came back closed with:

    "The deck is updated with this audience section and is the
    PowerPoint linked above, 13 slides, sent to your inbox with the file
    attached. Crosswalk Inter is embedded and the brand check passed."

The night before, the same thread: "The deck is built and in the
thread: 12 slides summarizing the 4,194,382 viewer audience". Jessie,
the same afternoon, on a build that had been refused and then built on
5 placements: "Paramount+ placements carried into the build: 49", "The
build already carries all 49 Paramount+ placements", "The full profile
is built across all 49 Paramount+ placements".

Every one of those sentences was written by the model that writes a
read. Nothing checked them against anything.

The rule, enforced where a written answer is finished (never by prompt
wording alone): the words of a read or an analysis answer may state

  * that a deck, a file, an email, a build or a read was sent, built,
    attached, started, queued, updated, or is running or on its way,
    only when the reply itself carries the record of it (a deck job, a
    file link, a send record, a run id, a job id);
  * what a build was run on or what it carries, only when the statement
    was read from the file or the run record at answer time
    (``prometheus.build_facts`` hands in what it read).

A sentence that claims more than the record holds is taken out (the
figures beside it stay: a clause is cut, not the read), and the reply
goes out. Where the reader plainly asked for the thing, the thing is
started by ``prometheus.ride_along`` and the reply says so in a line
that carries its own job.

This module polices written answers only. The system's own lines (a
deck's progress line with its job, "Sent the profile to <address>" from
the send lane, a build's "Profile building" line with its run) are
written by the code that did the thing and are not touched here; the
promise they make is kept true by ``prometheus.promise_truth``.

``claims(text)``                 [(kind, sentence)] for every claim found
``backed(raw)``                  the kinds the payload has a record for
``scrub(text, have, facts)``     (text, [(kind, sentence removed)])
``finish_answer(raw, ...)``      a written answer from the analyze core
``finish_read(payload, ...)``    a finished read, before it lands

Pure text; no model, no S3. Never raises: on any doubt the text goes
out as it came. Regression: scripts/test_pm_truth_2026_11_11.py.
"""
from __future__ import annotations

import re

__all__ = ['claims', 'backed', 'scrub', 'finish_answer', 'finish_read', 'KINDS']

KINDS = ('deck', 'email', 'file', 'build', 'progress', 'build_contents')

_DELIVERABLE = r"(?:deck|power\s?point|pptx|slides?|presentation|one[- ]pager)"
_FILE = r"(?:pdf|csv|spreadsheet|workbook|xlsx|excel\s+file|file|export|download)"
_STATE = (r"(?:(?:now|already|also|just)\s+)?(?:built(?!\s+(?:on|around|for|by|from|to|with|across|out)\b)|updated|ready|attached|sent|"
          r"emailed|mailed|delivered|linked|finished|complete|completed|done|generated|created|"
          r"in\s+(?:the|this|your)\s+(?:thread|chat|inbox|email)|on\s+its\s+way|queued|"
          r"building|being\s+(?:built|prepared|generated|assembled)|underway|in\s+progress)")
_RXS = (
    # the deck is built / updated / in the thread / linked above; "the PowerPoint linked above"
    ('deck', re.compile(
        r"\b(?:the|your|a|this|that)\s+(?:\w+\s+){0,3}?" + _DELIVERABLE + r"\b[^.!?\n]{0,60}?\b(?:is|are|was|were|has\s+been|have\s+been)\s+"
        + _STATE + r"\b|\b" + _DELIVERABLE + r"\s+(?:linked|attached)\s+(?:above|below|here)\b|"
        r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:just\s+|also\s+|now\s+)?(?:built|updated|created|generated|made|prepared|put\s+together|attached|"
        r"added)\b[^.!?\n]{0,50}\b" + _DELIVERABLE + r"\b|\bslides\s+in\s+the\s+deck\s*:|"
        r"\b\d{1,3}\s+slides\b[^.!?\n]{0,60}\b(?:sent|attached|linked|built|in\s+(?:the|this)\s+thread|in\s+your\s+inbox)\b|"
        r"\bCrosswalk\s+Inter\s+is\s+embedded\b|\bthe\s+brand\s+check\s+passed\b|"
        r"\bthe\s+deck(?:'s)?\s+\d{1,3}\s+(?:pages|slides)\b|\bdeck\s+section\s+mix\b|\b(?:carried|shown|printed)\s+on\s+the\s+deck\b|"
        r"\bshare\s+of\s+the\s+deck\b|(?:^|(?<=[.!?]\s))\W*The\s+deck\s+(?:leads|opens|closes|carries|shows|covers|ends|starts|runs)\b", re.I)),
    # sent to your inbox / emailed to you / I have sent
    ('email', re.compile(
        r"\b(?:sent|emailed|mailed|delivered|forwarded)\s+(?:it\s+|this\s+|that\s+|them\s+|over\s+|the\s+\w+\s+)?"
        r"to\s+(?:your\s+(?:inbox|e-?mail)|you\b(?!\s+(?:asked|said|wanted|named|sent))|[\w.+-]+@[\w.-]+\.[a-z]{2,})|"
        r"\b(?:is|are|was|were|has\s+been)\s+(?:now\s+)?(?:in|on\s+its\s+way\s+to)\s+your\s+(?:inbox|e-?mail)\b|"
        r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:just\s+|also\s+|now\s+)?(?:sent|emailed|mailed|forwarded)\b|"
        r"\bwith\s+the\s+file\s+attached\b|\b(?:i|we)\s+will\s+(?:e-?mail|send|mail)\s+(?:you|it|this|that|the)\b|"
        r"\byou\s+will\s+(?:get|receive|have)\s+(?:an?\s+)?(?:e-?mail|it\s+by\s+e-?mail)\b", re.I)),
    # the CSV is attached / the file is linked above
    ('file', re.compile(
        r"\b(?:the|your|a|this)\s+(?:\w+\s+){0,2}?" + _FILE + r"\b[^.!?\n]{0,40}?\b(?:is|are|was|were|has\s+been)\s+(?:now\s+)?"
        r"(?:attached|linked|sent|emailed|ready|built|generated|created)\b|\b" + _FILE + r"\s+(?:attached|linked)\s+(?:above|below|here)\b|"
        r"\battached\s+(?:above|below|here)\b", re.I)),
    # a build / a profile / a run was started, queued, is building
    ('build', re.compile(
        r"\b(?:the|your|a|this|that)\s+(?:\w+\s+){0,3}?(?:build|profile|pull|refresh|re-?run)\b[^.!?\n]{0,40}?\b(?:is|was|has\s+been)\s+"
        r"(?:now\s+|already\s+)?(?:queued|started|running|building|underway|in\s+progress|kicked\s+off|being\s+(?:built|run|re-?run))\b|"
        r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:just\s+|now\s+|already\s+)?(?:queued|started|kicked\s+off|launched|re-?queued|re-?started|re-?run|"
        r"re-?ran)\b[^.!?\n]{0,50}\b(?:build|profile|cut|run|pull|refresh)\b|\bprofile\s+building\s*:", re.I)),
    # working on it / in progress, with nothing named
    ('progress', re.compile(
        r"\bworking\s+on\s+it\b|\bconfirmed\s+your\s+task\s+is\s+in\s+progress\b|\b(?:it|this|that|your\s+(?:request|task|read|report))\s+is\s+"
        r"(?:now\s+)?(?:in\s+progress|underway|running|being\s+(?:prepared|worked\s+on))\b|"
        r"\b(?:will|is\s+going\s+to)\s+(?:land|arrive)\s+(?:right\s+)?(?:here|in\s+this\s+thread|in\s+your\s+inbox)\b", re.I)),
    # what a build was run on / carries
    ('build_contents', re.compile(
        r"\b(?:placements?|inputs?|seeds?|urls?|episodes?)\s+(?:carried|loaded|used|run|included|seeded)\s+(?:in|into|by|for)\s+the\s+"
        r"(?:build|profile|file|run)\b|"
        r"\bthe\s+(?:full\s+|new\s+|whole\s+)?(?:build|profile|file|run)\s+(?:already\s+|now\s+|still\s+)?(?:carries|carried|includes?|included|"
        r"uses|used|covers|covered|ran\s+on|was\s+(?:run|built|seeded)\s+(?:on|across|with|from)|is\s+(?:built|run|seeded)\s+(?:on|across|with|from)|"
        r"has\s+(?:been\s+)?(?:run|built)\s+(?:on|across|with))\b[^.!?\n]{0,80}\b(?:placements?|inputs?|seeds?|urls?|episodes?|seasons?)\b|"
        r"\ball\s+(?:\d+\s+)?(?:of\s+them\s+)?(?:were|was)\s+(?:run|used|loaded|seeded)\b|"
        r"\b(?:built|run|seeded)\s+(?:on|across|with|from)\s+all\s+\d+\b|"
        r"\ball\s+\d+\s+(?:\w+\s+){0,2}(?:placements?|inputs?|seeds?|urls?)\s+(?:are|were|have\s+been)\s+(?:folded|carried|loaded|included|"
        r"used|run|seeded|counted)\b|"
        r"\b(?:the\s+)?\d+\s+placements?\s+(?:from\s+the\s+content\s+map\s+)?define\b|\bacross\s+all\s+\d+\s+placements?\b|"
        r"\bplacements?\s+(?:you\s+)?(?:loaded|sent|listed|pasted)\s+define\b|\bnot\s+parked\s+in\s+a\s+label\s+field\b", re.I)),
)
_ARTIFACT_JOBS = ('read_job_id', 'deck_job_id', 'jiq_job_id', 'bpiq_job_id', 'fw_job_id', 'aiq_job_id', 'twin_job_id',
                  'tlist_job_id', 'job_id', 'bulk_id')
_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_CLAUSE_RX = re.compile(r",\s+and\s+|;\s+|,\s+(?:with|while|so)\s+|\s+-\s+|:\s+(?=[A-Z0-9])")


def claims(text):
    """[(kind, sentence)] for every claim a written answer makes."""
    out = []
    try:
        for kind, sent in _units(str(text or '')):
            out.append((kind, sent))
    except Exception:
        return []
    return out


def _kinds_in(sentence):
    return [k for k, rx in _RXS if rx.search(sentence)]


def _units(text):
    for line in text.split('\n'):
        body = line.strip()
        if not body:
            continue
        for sent in _SPLIT_RX.split(body):
            for k in _kinds_in(sent):
                yield k, sent.strip()


def backed(raw):
    """The claim kinds a payload carries a record for."""
    have = set()
    try:
        if not isinstance(raw, dict):
            return have
        if raw.get('deck_job_id') or (isinstance(raw.get('link'), dict) and 'deck' in str(raw['link'].get('label') or '').lower()) \
                or raw.get('deck_url') or raw.get('ride_along_deck_job'):
            have.add('deck')
        fl = raw.get('file_link')
        if (isinstance(fl, dict) and fl.get('url')) or raw.get('download_url') or raw.get('files'):
            have.add('file')
        if raw.get('emailed') or raw.get('sent_to') or raw.get('mail_sent') or (isinstance(raw.get('send'), dict) and raw['send'].get('sent')):
            have.add('email')
        if raw.get('run_id') or raw.get('run_ids') or raw.get('build_run_id'):
            have.update(('build', 'progress'))
        if any(raw.get(k) for k in _ARTIFACT_JOBS) or (isinstance(raw.get('job'), dict) and raw['job'].get('id')):
            have.add('progress')
        if raw.get('held_ref') or raw.get('held_id'):
            have.add('progress')
        if isinstance(raw.get('build_facts'), dict) and raw['build_facts'].get('read_at'):
            have.add('build_contents')
    except Exception:
        pass
    return have


def _cut_clauses(sentence, bad_kinds):
    """The sentence with the clauses that carry an unbacked claim taken
    out. '' when nothing true is left to say. Only a statement about
    what a build carries is cut by the clause (the figure beside it is
    the read's own); a claim that something was sent, built or started
    takes its whole sentence, and so does a labelled line whose label is
    the claim ("Placements carried into the build: 49")."""
    if set(bad_kinds) - {'build_contents'}:
        return ''
    head = sentence.split(':', 1)[0] if ':' in sentence else ''
    if head and len(head.split()) <= 9 and any(k in bad_kinds for k in _kinds_in(head)):
        return ''
    parts, seps, pos = [], [], 0
    for m in _CLAUSE_RX.finditer(sentence):
        parts.append(sentence[pos:m.start()])
        seps.append(m.group(0))
        pos = m.end()
    parts.append(sentence[pos:])
    if len(parts) == 1:
        return ''
    keep = [(p, i) for i, p in enumerate(parts) if not any(k in bad_kinds for k in _kinds_in(p))]
    keep = [(p.strip(' ,;:-'), i) for p, i in keep if len(re.findall(r"[A-Za-z0-9]+", p)) >= 4]
    if not keep:
        return ''
    out = keep[0][0]
    for p, _i in keep[1:]:
        out = out.rstrip(' ,;:') + ', and ' + (p[:1].lower() + p[1:] if p[:1].isupper() and not p[:2].isupper() else p)
    out = out.strip()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    if out and out[-1] not in '.!?':
        out += '.'
    return out


def scrub(text, have=None, facts=None):
    """(the text with every unbacked claim taken out, [(kind, removed)]).
    ``have`` is the set of kinds the reply has a record for; ``facts``
    is what ``build_facts`` read from the file (its one-line statement
    replaces a statement about the build the model wrote)."""
    try:
        src = str(text or '')
        if not src.strip():
            return text, []
        have = set(have or ())
        removed = []
        fact_line = str((facts or {}).get('line') or '').strip() if isinstance(facts, dict) else ''
        fact_used = False
        out_lines = []
        for line in src.split('\n'):
            body = line.strip()
            if not body:
                out_lines.append(line)
                continue
            lead = re.match(r"^\s*(?:[-*\u2022]\s+|\d+[.)]\s+)?", line).group(0)
            core = line[len(lead):]
            sents, kept = _SPLIT_RX.split(core.strip()), []
            for sent in sents:
                bad = [k for k in _kinds_in(sent) if k not in have]
                if not bad:
                    kept.append(sent)
                    continue
                removed.extend((k, sent.strip()) for k in bad)
                if 'build_contents' in bad and fact_line and not fact_used:
                    kept.append(fact_line)        # what the file says, once
                    fact_used = True
                    continue
                rest = _cut_clauses(sent, set(bad))
                if rest and not _kinds_in(rest):
                    kept.append(rest)
            if kept:
                out_lines.append(lead + ' '.join(kept))
            # a line left with nothing true in it goes whole (the bullet with it)
        out = '\n'.join(out_lines)
        out = re.sub(r"\n{3,}", '\n\n', out).strip('\n')
        if not removed:
            return text, []
        if not re.search(r"[A-Za-z0-9]", out):
            return text, []          # nothing would be left: the text goes out as it came (the caller is told)
        return out, removed
    except Exception:
        return text, []


def _is_system_promise(reply):
    """The calm line and its kin are the system's own promise, kept true
    by ``promise_truth`` (a held record, a job): never a written answer."""
    try:
        from . import promise_truth as _pt
        return _pt.promise_kind(reply) in ('calm', 'email') and len(str(reply)) < 400
    except Exception:
        return str(reply or '').strip().startswith('Working on it.')


def _note(route, removed, subject=''):
    try:
        from . import faults as _faults
        if removed:
            _faults.note('reply_truth.' + route, RuntimeError(
                f"{len(removed)} unbacked claim(s) taken out of a written answer on {subject or '(no subject)'}: "
                + '; '.join(f"{k}: {s[:120]}" for k, s in removed[:4])))
    except Exception:
        pass


def finish_answer(raw, *, question='', facts=None):
    """A written answer from the analyze core: unbacked claims come out
    of ``reply``. A written answer is one the model wrote: it carries the
    model's name, a figure ledger or the profile it read. A payload that
    is a progress line for its own job, a draft, an offer, guidance or a
    line the core's own code wrote is left alone. Returns the payload
    (changed in place)."""
    try:
        if not isinstance(raw, dict) or not raw.get('success') or raw.get('action') not in ('answer', None):
            return raw
        reply = raw.get('reply')
        if not isinstance(reply, str) or not reply.strip():
            return raw
        if raw.get('panel_offer') or raw.get('memory_confirm') or raw.get('guidance') or raw.get('spec_draft') or raw.get('spec_drafts'):
            return raw
        have = backed(raw)
        if any(raw.get(k) for k in _ARTIFACT_JOBS) and len(reply) < 400:
            return raw                   # the ack of its own job ("On it: reading X now ...")
        if raw.get('held') or raw.get('held_ref') or _is_system_promise(reply):
            return raw                   # the calm promise is promise_truth's to back, never a sentence to cut
        if not (raw.get('model') or isinstance(raw.get('figures'), list) or raw.get('profile') or raw.get('_verify')):
            return raw                   # a line the core's own code wrote ("Sent. X is on its way to <address>."), not the model
        if isinstance(raw.get('build_facts'), dict):
            return raw                   # read from the file by the build-facts lane
        out, removed = scrub(reply, have, facts)
        if removed:
            raw['reply'] = out
            raw['claims_removed'] = [k for k, _s in removed][:12]
            _note('answer', removed, str(raw.get('subject') or raw.get('profile') or ''))
    except Exception:
        pass
    return raw


def finish_read(payload, *, question='', facts=None):
    """A finished read, before it lands: the same rule. The read's own
    file link backs a sentence about its file; nothing in a read backs a
    deck, a send or a build. Returns the payload (changed in place)."""
    try:
        if not isinstance(payload, dict) or not payload.get('success'):
            return payload
        reply = payload.get('reply')
        if not isinstance(reply, str) or not reply.strip():
            return payload
        if payload.get('held_ref') or _is_system_promise(reply):
            return payload               # a held read's calm line is promise_truth's to back
        have = backed({k: v for k, v in payload.items() if k in ('file_link', 'download_url', 'files', 'emailed', 'sent_to')})
        out, removed = scrub(reply, have, facts)
        if removed:
            payload['reply'] = out
            payload['claims_removed'] = [k for k, _s in removed][:12]
            _note('read', removed, str(payload.get('subject') or payload.get('profile') or ''))
    except Exception:
        pass
    return payload
