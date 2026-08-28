"""Sample-sizing guards for the interpret -> spec choke point.

Two defect classes shipped on 2026-08-24 (Florida + Iowa voter
universes, both with subject_raw_tu = 41823):

1. The interpret stage picked subject_raw_tu without binding it to the
   countable universe anchor its own research produced (13.4M FL
   registered voters vs a projection that must sit under it).
2. Nothing detected two unrelated specs landing on the byte-identical
   sample value, so a duplicated default shipped on two subjects that
   differ by more than 25x.

This module hosts both guards. bg-webapp/app.py::_spec_from_draft is
the primary wire point; migration/synth_queue_worker.py::_run_new_build
mirrors the duplicate guard as defense in depth.

Pure functions (scale_under_anchor, dedupe_against_ledger) carry the
logic so they are testable without S3; the ledger IO helpers wrap the
S3 round-trip and never raise.
"""
from __future__ import annotations

import hashlib
import json
import time

try:
    from scripts._sample_size_jitter import ensure_messy_sample_size
except Exception:  # pragma: no cover - minimal shim, same contract
    def ensure_messy_sample_size(subj, v, minimum=800, default_if_missing=9873):
        try:
            x = int(round(float(v))) if v is not None else 0
        except Exception:
            x = 0
        if x <= 0:
            x = default_if_missing
        off = int(hashlib.sha256(f"{subj}|{x}".encode()).hexdigest()[:8], 16) % 197 - 98
        x = max(minimum + 7, x + off)
        while x % 10 == 0:
            x += 1 + int(hashlib.sha256(f"{subj}|{x}|w".encode()).hexdigest()[:2], 16) % 8
        return x

US_POP = 329_900_000
PANEL = 10_000_000

LEDGER_KEY = 'system/recent_subject_raws.json'
LEDGER_MAX = 200

# Anchor sanity bounds (Jenna 2026-08-24 mandate: "the sample always
# has to be based to the researched anchor"). An anchor below 500
# individuals or above the US population is not a usable universe.
ANCHOR_MIN = 500
ANCHOR_MAX = 340_000_000

# Sample-vs-anchor derivation tolerance: the messy jitter moves a
# sample by well under 1%, so 5% is generous headroom before we
# recompute from the anchor.
ANCHOR_FIT_TOLERANCE = 0.05

_ANCHOR_NOUNS = (
    'voter', 'subscriber', 'member', 'buyer', 'customer', 'owner',
    'household', 'user', 'player', 'fan', 'listener', 'viewer',
    'resident', 'adult', 'holder', 'account', 'enrollee',
    'policyholder', 'population', 'people', 'individual', 'attendee',
    'follower', 'shopper', 'renter', 'donor', 'patient', 'student',
    'employee', 'rider', 'driver', 'passenger', 'guest', 'reader',
)


def _norm_subject(subject):
    return ''.join(ch for ch in str(subject or '').lower() if ch.isalnum())


def projected_audience(sample):
    """US Gen Pop projection implied by a panel sample."""
    return int(round(float(sample) / PANEL * US_POP))


def parse_anchor(value):
    """Coerce a draft's universe_anchor to a usable int, or None when
    missing, non-numeric, or absurd (< ANCHOR_MIN or > ANCHOR_MAX)."""
    if value in (None, '', 'null', 'None'):
        return None
    try:
        v = int(round(float(str(value).replace(',', '').replace('%', ''))))
    except (TypeError, ValueError):
        return None
    if not (ANCHOR_MIN <= v <= ANCHOR_MAX):
        return None
    return v


def parse_share(value):
    """Coerce a draft's engaged_share to a fraction in (0, 1), or None.
    Accepts 0.106, '10.6%', '10.6' (values in (1, 100] read as percent)."""
    if value in (None, '', 'null', 'None'):
        return None
    try:
        v = float(str(value).replace('%', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    if 1.0 < v <= 100.0:
        v = v / 100.0
    if not (0.0 < v < 1.0):
        return None
    return v


_ANCHOR_RE = None


def extract_anchor_from_prose(text):
    """Regex-recover a countable universe anchor from draft prose
    (persona_notes, assumptions, decision_reason): the largest counted
    figure that sits within ~60 characters of an anchor-ish noun.
    Returns (anchor_int_or_None, source_phrase_or_None)."""
    global _ANCHOR_RE
    import re
    if not text:
        return None, None
    if _ANCHOR_RE is None:
        noun = '|'.join(_ANCHOR_NOUNS)
        _ANCHOR_RE = re.compile(
            r'(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:million|m\b|mm\b)|'
            r'\d{4,9})'
            r'(?P<gap>[^.;\n]{0,60}?)'
            r'\b(?P<noun>(?:' + noun + r')s?)\b',
            re.IGNORECASE,
        )
    best = None
    best_src = None
    for m in _ANCHOR_RE.finditer(str(text)):
        num_txt = m.group(1).strip()
        low = num_txt.lower()
        try:
            if 'million' in low or low.rstrip().endswith(('m', 'mm')):
                base = float(low.replace('million', '').replace('mm', '')
                             .replace('m', '').replace(',', '').strip())
                v = int(round(base * 1_000_000))
            else:
                v = int(round(float(num_txt.replace(',', ''))))
        except (TypeError, ValueError):
            continue
        if not (ANCHOR_MIN <= v <= ANCHOR_MAX):
            continue
        if best is None or v > best:
            best = v
            gap = ' '.join((m.group('gap') or '').split())
            best_src = f"{num_txt} {gap} {m.group('noun')}".strip()
    return best, best_src


def _is_international_country(country):
    """True for a real non-US market (lazy import; never raises)."""
    try:
        from migration.international_profiles import is_international
        return is_international(country)
    except Exception:
        try:
            from international_profiles import is_international  # type: ignore
            return is_international(country)
        except Exception:
            return False


def enforce_anchor_derivation(subject, subject_raw_tu, subject_raw_avid,
                              universe_anchor=None, engaged_share=None,
                              anchor_source=None, prose=None, log=print,
                              country='US', refresh_locked=False):
    """Jenna 2026-08-24 mandate: 'the sample always has to be based to
    the researched anchor.' Every fresh-build sample must equal
    anchor x engaged_share (within ANCHOR_FIT_TOLERANCE); when it does
    not, it is recomputed from the anchor. Reject-or-repair semantics:
    a missing/absurd anchor is recovered from the draft prose when
    possible, otherwise flagged with anchor_missing=True (the run
    status and ops email surface the flag).

    Returns (subject_raw_tu, subject_raw_avid, meta). meta always has
    keys: anchor, anchor_source, engaged_share, engaged_share_derived,
    anchor_missing, repaired_from_prose, recomputed, projected."""
    meta = {
        'anchor': None, 'anchor_source': None, 'engaged_share': None,
        'engaged_share_derived': False, 'anchor_missing': False,
        'repaired_from_prose': False, 'recomputed': False,
        'projected': None,
    }
    try:
        tu = int(subject_raw_tu)
    except (TypeError, ValueError):
        return subject_raw_tu, subject_raw_avid, meta
    anchor = parse_anchor(universe_anchor)
    if anchor is None and universe_anchor not in (None, '', 'null', 'None'):
        try:
            log(f"[anchor-guard] {subject!r}: draft universe_anchor "
                f"{universe_anchor!r} is unusable (non-numeric or outside "
                f"[{ANCHOR_MIN}, {ANCHOR_MAX:,}]); attempting prose repair")
        except Exception:
            pass
    if anchor is None:
        anchor, prose_src = extract_anchor_from_prose(prose)
        if anchor is not None:
            meta['repaired_from_prose'] = True
            anchor_source = anchor_source or prose_src
            try:
                log(f"[anchor-guard] {subject!r}: recovered anchor "
                    f"{anchor:,} from draft prose ({prose_src!r})")
            except Exception:
                pass
    if anchor is None:
        meta['anchor_missing'] = True
        meta['projected'] = projected_audience(tu)
        try:
            log(f"[anchor-guard] {subject!r}: NO USABLE UNIVERSE ANCHOR "
                f"on this fresh-build draft (anchor_missing=true); sample "
                f"{tu:,} passes through the plausibility path unanchored")
        except Exception:
            pass
        return subject_raw_tu, subject_raw_avid, meta

    if _is_international_country(country):
        # International build (Omaze precedent): the anchor is the
        # COUNTRY universe and the emitted sample is already anchored to
        # it by the interpret stage / engine (Omaze UK: sample 40,247 vs
        # UK universe ~4.03M). The US 10M-panel fit walk below would
        # re-derive the sample against the 329.9M US chain and mangle
        # the country anchor, so the derivation is recorded as-is.
        share = parse_share(engaged_share)
        meta.update({
            'anchor': anchor,
            'anchor_source': (str(anchor_source).strip() or None)
            if anchor_source else None,
            'engaged_share': round(share, 6) if share else None,
            'projected': anchor,
            'country': str(country).strip().upper(),
        })
        try:
            log(f"[anchor-guard] {subject!r}: international build "
                f"({str(country).strip().upper()}); country anchor "
                f"{anchor:,} honored, US-chain fit walk skipped")
        except Exception:
            pass
        return tu, subject_raw_avid, meta

    share = parse_share(engaged_share)
    projected = projected_audience(tu)
    if share is None:
        implied = projected / float(anchor)
        if 0.0 < implied <= 1.0:
            # The sample already fits under the anchor; adopt the
            # implied share so the derivation chain is stated.
            share = implied
            meta['engaged_share_derived'] = True
        else:
            # Over-anchor with no stated share: salted engaged share,
            # same semantics as scale_under_anchor.
            h = int(hashlib.sha256(
                f"{subject}|universe-anchor|tu".encode()).hexdigest()[:8], 16)
            share = 0.72 + (h % 2101) / 10000.0
            meta['engaged_share_derived'] = True

    if refresh_locked:
        # Time-shifted refresh (2026-08-28, Joe & The Juice 22x sizing
        # defect): the sample arriving here was chosen by the refresh
        # sizing guard (parent-anchored +/-15% band, window-research
        # verdicts, every-3rd-generation re-anchor). A freshly guessed
        # anchor x share pair from THIS run's interpret step must not
        # re-derive it - on 08/26 and 08/27 the same subject shipped
        # 47,275 then 12,795 because each refresh's new anchor framing
        # (brand customers x 1.7%, then category visitors x 0.3%)
        # silently replaced the guard's verdict. The researched-anchor
        # HARD CEILING still binds: a locked sample projecting ABOVE
        # the anchor is scaled under it (that is exactly the move that
        # corrected the unanchored 284,763 parent down to ~47k).
        # Below the ceiling, the share is restated from the delivered
        # sample so the derivation chain stays coherent.
        meta['refresh_locked'] = True
        if projected > anchor:
            new_tu, _sc_meta = scale_under_anchor(
                subject, tu, anchor, salt='tu-refresh-ceiling')
            if new_tu != tu:
                try:
                    log(f"[anchor-guard] {subject!r}: refresh-locked "
                        f"sample {tu:,} projects {projected:,} ABOVE "
                        f"the researched universe anchor {anchor:,}; "
                        f"ceiling binds, scaled to {new_tu:,}")
                except Exception:
                    pass
                if subject_raw_avid:
                    try:
                        factor = new_tu / float(tu)
                        subject_raw_avid = ensure_messy_sample_size(
                            f"{subject}|avid|anchored",
                            max(int(round(int(subject_raw_avid) * factor)),
                                500),
                        )
                    except (TypeError, ValueError):
                        pass
                tu = new_tu
                meta['recomputed'] = True
        else:
            try:
                log(f"[anchor-guard] {subject!r}: refresh-locked sample "
                    f"{tu:,} kept (guard verdict wins); engaged share "
                    f"restated as projected {projected:,} / anchor "
                    f"{anchor:,}")
            except Exception:
                pass
        _share_final = projected_audience(tu) / float(anchor)
        meta.update({
            'anchor': anchor,
            'anchor_source': (str(anchor_source).strip() or None)
            if anchor_source else None,
            'engaged_share': round(_share_final, 6),
            'engaged_share_derived': True,
            'projected': projected_audience(tu),
        })
        return tu, subject_raw_avid, meta

    expected_projected = anchor * share
    if expected_projected > 0 and \
            abs(projected - expected_projected) / expected_projected \
            > ANCHOR_FIT_TOLERANCE:
        new_tu = int(round(expected_projected / US_POP * PANEL))
        new_tu = ensure_messy_sample_size(f"{subject}|anchored|tu", new_tu)
        h2 = int(hashlib.sha256(
            f"{subject}|anchor-walk".encode()).hexdigest()[:8], 16)
        guard = 0
        while projected_audience(new_tu) > anchor and guard < 50:
            new_tu -= 1 + (h2 % 7)
            guard += 1
        try:
            log(f"[anchor-guard] {subject!r}: sample {tu:,} does not "
                f"derive from anchor {anchor:,} x share {share:.4f} "
                f"(projected {projected:,} vs expected "
                f"{int(expected_projected):,}); recomputed to {new_tu:,}")
        except Exception:
            pass
        if subject_raw_avid:
            try:
                factor = new_tu / float(tu)
                subject_raw_avid = ensure_messy_sample_size(
                    f"{subject}|avid|anchored",
                    max(int(round(int(subject_raw_avid) * factor)), 500),
                )
            except (TypeError, ValueError):
                pass
        tu = new_tu
        meta['recomputed'] = True

    meta.update({
        'anchor': anchor,
        'anchor_source': (str(anchor_source).strip() or None)
        if anchor_source else None,
        'engaged_share': round(share, 6),
        'projected': projected_audience(tu),
    })
    return tu, subject_raw_avid, meta


def format_anchor_line(meta, tu_sample=None):
    """One-line ops derivation chain, e.g.
    '13,426,540 active registered FL voters x 10.6% engaged =
    1,423,222 projected; sample 43,141'. Returns '' when meta is
    unusable; an anchor-missing meta renders a visible flag."""
    if not isinstance(meta, dict):
        return ''
    if meta.get('anchor_missing'):
        return ('no countable universe anchor on this build '
                '(anchor_missing=true); sample passed the plausibility '
                'path unanchored')
    anchor = meta.get('anchor')
    share = meta.get('engaged_share')
    if not anchor or not share:
        return ''
    src = meta.get('anchor_source') or 'researched universe anchor'
    projected = meta.get('projected')
    parts = [f"{int(anchor):,} {src} x {share * 100.0:.1f}% engaged"]
    if projected:
        parts.append(f"= {int(projected):,} projected")
    if tu_sample:
        try:
            parts.append(f"; sample {int(tu_sample):,}")
        except (TypeError, ValueError):
            pass
    line = ' '.join(parts).replace(' ;', ';')
    if meta.get('repaired_from_prose'):
        line += ' (anchor recovered from draft prose)'
    if meta.get('recomputed'):
        line += ' (sample recomputed from anchor)'
    return line


def scale_under_anchor(subject, subject_raw, anchor, salt='tu'):
    """If the implied projected audience exceeds the countable universe
    anchor (registered voters, subscribers, members, district
    population), scale the sample down so the projection sits at a
    subject-salted engaged share of the anchor (72-93%). Returns
    (new_value, meta_dict_or_None). meta is None when no change."""
    try:
        subject_raw = int(subject_raw)
        anchor = int(anchor)
    except (TypeError, ValueError):
        return subject_raw, None
    if anchor <= 0 or subject_raw <= 0:
        return subject_raw, None
    projected = projected_audience(subject_raw)
    if projected <= anchor:
        return subject_raw, None
    h = int(hashlib.sha256(
        f"{subject}|universe-anchor|{salt}".encode()).hexdigest()[:8], 16)
    share = 0.72 + (h % 2101) / 10000.0  # 0.72 - 0.93
    new_projected = anchor * share
    new_sample = int(round(new_projected / US_POP * PANEL))
    new_sample = ensure_messy_sample_size(
        f"{subject}|anchored|{salt}", new_sample)
    # Jitter can only move the value by a handful of units; re-verify
    # the projection still sits under the anchor and walk down if not.
    guard = 0
    while projected_audience(new_sample) > anchor and guard < 50:
        new_sample -= 1 + (h % 7)
        guard += 1
    meta = {
        'anchor': anchor,
        'old_sample': subject_raw,
        'old_projected': projected,
        'engaged_share': round(share, 4),
        'new_sample': new_sample,
        'new_projected': projected_audience(new_sample),
    }
    return new_sample, meta


def dedupe_against_ledger(subject, value, entries, salt='tu'):
    """If `value` byte-matches a DIFFERENT subject's recently minted
    subject_raw, re-jitter deterministically per subject (+/- up to 4%,
    messiness preserved) so no two subjects ship identical samples.
    `entries` is the ledger list [{subject, value, ts, kind}, ...].
    Returns (new_value, changed_bool)."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return value, False
    me = _norm_subject(subject.split('|', 1)[0])
    clashing = set()
    for e in entries or []:
        try:
            ev = int(e.get('value'))
        except (TypeError, ValueError, AttributeError):
            continue
        if _norm_subject(e.get('subject', '').split('|', 1)[0]) != me:
            clashing.add(ev)
    if value not in clashing:
        return value, False
    h = hashlib.sha256(f"{subject}|dedupe|{salt}".encode()).hexdigest()
    frac = ((int(h[:8], 16) % 8001) - 4000) / 100000.0  # -4% .. +4%
    new_value = int(round(value * (1.0 + frac)))
    if new_value == value:
        new_value += 1 + int(h[8:10], 16) % 9
    new_value = ensure_messy_sample_size(f"{subject}|dedupe|{salt}", new_value)
    # Walk off any residual clash with a different subject's value.
    guard = 0
    step = 1 + int(h[10:12], 16) % 9
    while new_value in clashing and guard < 50:
        new_value = ensure_messy_sample_size(
            f"{subject}|dedupe|{salt}|{guard}", new_value + step)
        guard += 1
    return new_value, True


def load_recent_subject_raws(s3_client, bucket):
    """Read the rolling ledger from S3. Never raises; returns []."""
    try:
        body = s3_client.get_object(Bucket=bucket, Key=LEDGER_KEY)['Body'].read()
        data = json.loads(body)
        entries = data.get('entries') if isinstance(data, dict) else data
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def record_subject_raws(s3_client, bucket, entries, records):
    """Append `records` ({subject, value, kind}) to the ledger, trim to
    the newest LEDGER_MAX entries, write back. Never raises.

    Uses the ETag-guarded conditional write so 21 concurrent workers
    never drop each other's ledger appends. The `entries` arg (the
    caller's earlier read) is only a fallback when the re-read inside
    the guarded loop can't produce a list.
    """
    try:
        from migration.s3_json_state import update_json
    except ImportError:
        try:
            from s3_json_state import update_json
        except ImportError:
            update_json = None
    try:
        now = int(time.time())
        new_records = [{
            'subject': str(r.get('subject', '')),
            'value': int(r.get('value', 0)),
            'kind': str(r.get('kind', 'tu')),
            'ts': now,
        } for r in records]

        if update_json is not None:
            def _mutate(data):
                cur = data.get('entries') if isinstance(data, dict) else data
                if not isinstance(cur, list):
                    cur = list(entries or [])
                merged = cur + new_records
                return {'entries': merged[-LEDGER_MAX:]}

            update_json(bucket, LEDGER_KEY, _mutate, s3=s3_client,
                        default={'entries': []}, indent=None)
            return True

        merged = list(entries or []) + new_records
        merged = merged[-LEDGER_MAX:]
        s3_client.put_object(
            Bucket=bucket, Key=LEDGER_KEY,
            Body=json.dumps({'entries': merged}).encode('utf-8'),
            ContentType='application/json',
        )
        return True
    except Exception:
        return False


def apply_sizing_guards(subject, subject_raw_tu, subject_raw_avid,
                        universe_anchor=None, engaged_share=None,
                        anchor_source=None, prose=None, s3_client=None,
                        bucket='dashboard-inputs', log=print, country='US',
                        refresh_locked=False):
    """One-call convenience wrapper used by both wire points.

    Runs the MANDATORY anchor derivation on TU (Jenna 2026-08-24:
    'the sample always has to be based to the researched anchor';
    avid scales proportionally when TU moves), then the cross-spec
    duplicate guard on both values against the S3 ledger, then records
    the final values. Returns
    (subject_raw_tu, subject_raw_avid, anchor_meta)."""
    # 1. Mandatory universe-anchor derivation (reject-or-repair).
    subject_raw_tu, subject_raw_avid, anchor_meta = \
        enforce_anchor_derivation(
            subject, subject_raw_tu, subject_raw_avid,
            universe_anchor=universe_anchor, engaged_share=engaged_share,
            anchor_source=anchor_source, prose=prose, log=log,
            country=country, refresh_locked=refresh_locked,
        )
    # 2. Cross-spec duplicate guard (S3 ledger).
    entries = []
    if s3_client is not None:
        entries = load_recent_subject_raws(s3_client, bucket)
        new_tu, ch_tu = dedupe_against_ledger(
            subject, subject_raw_tu, entries, salt='tu')
        if ch_tu:
            try:
                log(f"[sizing-guard] {subject!r}: subject_raw_tu "
                    f"{subject_raw_tu:,} matched a different subject's "
                    f"recent sample; re-jittered to {new_tu:,}")
            except Exception:
                pass
            subject_raw_tu = new_tu
        if subject_raw_avid:
            new_av, ch_av = dedupe_against_ledger(
                f"{subject}|avid", subject_raw_avid, entries, salt='avid')
            if ch_av:
                try:
                    log(f"[sizing-guard] {subject!r}: subject_raw_avid "
                        f"{subject_raw_avid:,} matched a different "
                        f"subject's recent sample; re-jittered to "
                        f"{new_av:,}")
                except Exception:
                    pass
                subject_raw_avid = new_av
        if subject_raw_avid and subject_raw_avid >= subject_raw_tu:
            subject_raw_avid = ensure_messy_sample_size(
                f"{subject}|avid|subset",
                max(int(subject_raw_tu * 0.24), 500),
            )
        records = [{'subject': subject, 'value': subject_raw_tu, 'kind': 'tu'}]
        if subject_raw_avid:
            records.append({'subject': subject, 'value': subject_raw_avid,
                            'kind': 'avid'})
        record_subject_raws(s3_client, bucket, entries, records)
    try:
        # Quote the FINAL sample's projection in the derivation chain
        # (the ledger dedupe can move the sample a few percent). On an
        # international build the projection IS the country anchor and
        # must not be restated through the US chain.
        if not _is_international_country(country):
            anchor_meta['projected'] = projected_audience(int(subject_raw_tu))
    except (TypeError, ValueError):
        pass
    return subject_raw_tu, subject_raw_avid, anchor_meta
