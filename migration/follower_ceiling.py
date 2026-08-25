"""Public-metric audience ceiling cap for narrowed-audience profiles.

Rule (Jenna directive, 2026-08-19):

    When a profile is asked to be run (or a cut) on ONLY a subset of
    an audience whose size is publicly measurable — the followers of
    an account, the viewers of a video, the audience of a TV
    broadcast, the listeners of a podcast episode, the attendees of
    an event — the US Gen Pop Projection column must never exceed
    that public number. It is physically impossible for the cohort
    to project up to more people than the underlying metric.

The pipeline expresses US Gen Pop Projection via the canonical formula
(Rule #3a of profile-iq-pipeline-rules.mdc):

    Projection = Raw / PANEL * US_POP
    where PANEL = 10,000,000 and US_POP = 329,900,000

For the BRAND INPUT / SAMPLE SIZE row, Raw = subject_raw (the whole
audience). Substituting:

    max_projection = subject_raw / PANEL * US_POP <= audience_ceiling
    => subject_raw <= audience_ceiling * PANEL / US_POP
    => subject_raw <= floor(audience_ceiling / 32.99)

All downstream row-level projections scale off subject_raw
(Raw_i = BP_i/100 * subject_raw, Proj_i = Raw_i / PANEL * US_POP), so
capping subject_raw automatically caps every brand row's projection.

This module is the single source of truth for the math. The chatbot
interpret step (bg-webapp/app.py::_spec_from_draft), the queue worker
(migration/synth_queue_worker.py) and the post-generation enforcer
(migration/post_generation_enforcers.enforce_follower_ceiling_projection)
all consume the same helpers.

Detection of the capped audience type is done at interpret time by
Claude and echoed into `spec['audience_type']`. Values:

    'general'      standard TU / avid audience of the subject
                   (default; no cap applied).
    'followers'    ONLY people who follow the subject on social.
                   Ceiling = total follower count across named platforms.
    'subscribers'  ONLY subscribers to a newsletter, podcast, YouTube
                   channel, or SVOD service. Ceiling = subscriber count.
    'viewers'      ONLY viewers of a specific video / stream / episode /
                   broadcast. Ceiling = the video's public view count
                   (YouTube, TikTok, Instagram Reels, Twitch VOD) or the
                   broadcast's reported viewership (Nielsen, network
                   press release, streamer top-10 list).
    'listeners'    ONLY listeners of a specific podcast episode / radio
                   segment / streaming music release. Ceiling =
                   published play count / listener count.
    'attendees'    ONLY people who attended an in-person or virtual
                   event (concert, festival, conference, sports game).
                   Ceiling = published attendance number.
    'users'        ONLY MAU/DAU of a specific app or platform feature.
                   Ceiling = the platform's published MAU/DAU.

Any other value (or None / missing) is treated as 'general' and no cap
is applied. The 2026-08-19 initial version only handled followers /
subscribers; viewers / listeners / attendees / users were added the
same day per follow-up Jenna directive: "confirming that the followers
of or viewers of a certain video, etc that has easy to see public
metrics will never exceed that value when projected."
"""
from __future__ import annotations
import math

US_POP = 329_900_000
PANEL = 10_000_000
PROJECTION_MULTIPLIER = US_POP / PANEL  # 32.99

# Ultimate conservative fallback when the audience_type triggers the cap
# but no ceiling was provided AND persona research couldn't estimate one.
# Picks the smallest plausible tier so the file ships defensibly and the
# customer sees a small projection column rather than an over-projected
# one. Corresponds to a projection ceiling of ~500,000 people.
DEFAULT_UNKNOWN_FOLLOWER_CEILING = 500_000
DEFAULT_UNKNOWN_AUDIENCE_CEILING = DEFAULT_UNKNOWN_FOLLOWER_CEILING  # alias

# Every audience_type value that triggers the ceiling cap. Every other
# value (including missing / None / 'general') means the cap does NOT
# apply. Kept as one flat set intentionally — the math is identical
# regardless of which public metric supplies the ceiling.
CAPPED_AUDIENCE_TYPES = frozenset({
    # Followers / subscribers of an account or list.
    'followers', 'subscribers',
    'follower', 'subscriber',
    'followers_only', 'subscribers_only',
    # Viewers of a specific piece of content.
    'viewers', 'viewer', 'viewers_only',
    'video_views', 'view_count', 'stream_viewers',
    # Listeners of an audio release / episode.
    'listeners', 'listener', 'listeners_only',
    'play_count', 'plays',
    # Physical / virtual event attendees.
    'attendees', 'attendee', 'attendees_only',
    'attendance', 'ticket_buyers',
    # MAU / DAU cohorts.
    'users', 'user', 'users_only',
    'mau', 'dau', 'active_users',
})

# Kept for backward compatibility with existing imports; alias of the
# broader set. Nothing outside this module should reference this
# directly going forward — use CAPPED_AUDIENCE_TYPES.
FOLLOWER_ONLY_TYPES = CAPPED_AUDIENCE_TYPES


def is_capped_audience(audience_type) -> bool:
    """Return True iff audience_type triggers the public-metric ceiling.

    Covers followers, subscribers, viewers, listeners, attendees, users
    — any cohort whose size is publicly observable and therefore
    physically caps the achievable projection.
    """
    if not audience_type:
        return False
    return str(audience_type).strip().lower() in CAPPED_AUDIENCE_TYPES


# Backward-compat alias. Old callsites read "is_followers_only" but the
# behaviour is now the broader capped-audience check.
def is_followers_only(audience_type) -> bool:
    return is_capped_audience(audience_type)


def subject_raw_for_metric(metric) -> int:
    """Anchor conversion: the subject_raw whose US Gen Pop Projection
    equals the published metric. published-metric = projection anchor,
    NEVER a panel percent (Jenna directive 2026-08-20, Vizio defect:
    18.5M SmartCast accounts was consumed as 18.5% of the 10M panel,
    tripling the projection to 60.9M)."""
    return max_subject_raw_for_ceiling(metric)


def detect_metric_as_percent_slip(subject_raw, metric,
                                  tolerance: float = 0.05) -> bool:
    """Return True iff subject_raw carries the metric-as-percent slip
    signature.

    A published metric of M persons misread as "M-in-millions percent"
    of the 10M panel yields subject_raw ~= M / 10. The correct
    anchor-derived raw is M * PANEL / US_POP ~= M / 32.99, over 3x
    smaller. We flag when subject_raw sits within `tolerance` of M/10.

    Only meaningful when the metric is at least 1M (smaller metrics
    read as a percent produce sub-100K raws that don't inflate) and
    when the slip value is materially above the true anchor.
    """
    try:
        m = int(metric)
        raw = int(subject_raw)
    except Exception:
        return False
    if m < 1_000_000 or raw <= 0:
        return False
    slip_raw = m / 10.0          # metric read as percent of 10M panel
    anchor_raw = m * PANEL / US_POP
    if slip_raw <= anchor_raw * 1.5:
        return False             # signature indistinguishable from anchor
    return abs(raw - slip_raw) <= slip_raw * tolerance


def max_subject_raw_for_ceiling(follower_ceiling) -> int:
    """Convert a follower-count ceiling to the maximum allowed
    subject_raw (a.k.a. sample size) such that the resulting US Gen Pop
    Projection cannot exceed the ceiling.

    Uses floor() so we never overshoot by rounding. Returns 0 for None
    or non-positive inputs so callers can trivially detect an unusable
    ceiling.
    """
    try:
        fc = int(follower_ceiling)
    except Exception:
        return 0
    if fc <= 0:
        return 0
    # Raw = ceiling / (US_POP/PANEL) = ceiling * PANEL / US_POP
    # Floor so we never project above the ceiling.
    return math.floor(fc * PANEL / US_POP)


def cap_subject_raw(subject_raw, follower_ceiling,
                    fallback_ceiling: int = DEFAULT_UNKNOWN_FOLLOWER_CEILING
                    ) -> tuple[int, dict]:
    """Return (capped_subject_raw, meta_dict).

    meta_dict fields:
      applied           True iff a cap was actually applied (raw was reduced)
      original_raw      the input raw value (after int coercion)
      max_allowed_raw   what the cap allowed
      follower_ceiling  the ceiling actually used (may be fallback)
      used_fallback     True iff `fallback_ceiling` was used because
                        `follower_ceiling` was missing/invalid
      projection_before original raw's projection (may exceed ceiling)
      projection_after  capped raw's projection (<= ceiling)

    Never raises. If subject_raw itself is unusable (None / 0 /
    non-numeric), returns max_allowed_raw as a safe default.
    """
    used_fallback = False
    try:
        ceiling = int(follower_ceiling) if follower_ceiling is not None else 0
    except Exception:
        ceiling = 0
    if ceiling <= 0:
        ceiling = int(fallback_ceiling) if fallback_ceiling else 0
        used_fallback = ceiling > 0

    max_raw = max_subject_raw_for_ceiling(ceiling)

    try:
        raw_in = int(subject_raw) if subject_raw is not None else 0
    except Exception:
        raw_in = 0

    if max_raw <= 0:
        # No usable ceiling and no fallback - leave raw untouched.
        return raw_in, {
            'applied': False,
            'original_raw': raw_in,
            'max_allowed_raw': 0,
            'follower_ceiling': ceiling,
            'used_fallback': used_fallback,
            'projection_before': int(round(raw_in * PROJECTION_MULTIPLIER)),
            'projection_after': int(round(raw_in * PROJECTION_MULTIPLIER)),
        }

    capped = min(raw_in, max_raw) if raw_in > 0 else max_raw
    return capped, {
        'applied': raw_in > max_raw,
        'original_raw': raw_in,
        'max_allowed_raw': max_raw,
        'follower_ceiling': ceiling,
        'used_fallback': used_fallback,
        'projection_before': int(round(raw_in * PROJECTION_MULTIPLIER)),
        'projection_after': int(round(capped * PROJECTION_MULTIPLIER)),
    }


def normalize_follower_ceiling(v) -> int | None:
    """Accept follower-count in a variety of shapes and return an int
    (or None if we can't parse it).

    Handles:
      - int (as-is)
      - float (round down)
      - str with commas ('1,234,567')
      - str with unit suffixes ('12M', '500K', '2.5M', '3.4B')
      - dict with a `total` or `followers` key
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if v > 0 else None
    if isinstance(v, dict):
        for key in ('total', 'followers', 'count', 'us_followers',
                    'total_followers', 'follower_count'):
            if key in v:
                return normalize_follower_ceiling(v[key])
        # Sum a list of platform counts if given
        total = 0
        for val in v.values():
            n = normalize_follower_ceiling(val)
            if n:
                total += n
        return total if total > 0 else None
    if not isinstance(v, str):
        return None
    s = v.strip().replace(',', '').replace('_', '').replace(' ', '').lower()
    if not s:
        return None
    mult = 1
    if s.endswith('b') or s.endswith('bn'):
        mult = 1_000_000_000
        s = s.rstrip('bn')
    elif s.endswith('m') or s.endswith('mm'):
        mult = 1_000_000
        s = s.rstrip('mm')
    elif s.endswith('k'):
        mult = 1_000
        s = s.rstrip('k')
    try:
        return int(round(float(s) * mult)) if float(s) > 0 else None
    except Exception:
        return None


def summarize_cap(subject: str, cohort: str, meta: dict) -> str:
    """Human-readable one-liner for logs when a cap is applied."""
    if not meta.get('applied'):
        return ''
    fc = meta.get('follower_ceiling', 0)
    fb = ' (fallback ceiling)' if meta.get('used_fallback') else ''
    return (
        f"[follower-ceiling] {subject!r} {cohort}: "
        f"raw {meta['original_raw']:,} -> {meta['max_allowed_raw']:,}; "
        f"projection {meta['projection_before']:,} -> "
        f"{meta['projection_after']:,} (ceiling={fc:,}{fb})"
    )
