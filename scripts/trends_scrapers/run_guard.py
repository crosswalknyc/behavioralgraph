"""Guards around the nightly Trends IQ scraper run.

Three separate failures on 2026-09-15 each went undetected until a
colleague looked at the board and said the gaming and streaming numbers
looked too low and too similar to be real:

  1. A run from 2026-07-16 was still on the process table 61 days
     later, blocked forever on a driver handshake, with nothing
     stopping it from coexisting with the live run.
  2. That day's run took nine hours against a normal five, and nothing
     noticed or said so.
  3. For most of the day the board served values derived from each
     row's rank slot rather than from the row itself, and nothing
     measured that share.

This module supplies the missing guard for each: `RunLock` (one run at
a time), `start_watchdog` (alert on an overrun), and
`check_baseline_share` (alert on how the board came by its numbers,
counting rows carrying their own earlier reading separately from rows
that had no reading anywhere and fell to the rank tier).

Everything here is best-effort. A guard never raises into a run and
never blocks one; the worst case is a missing alert, never a lost
night's data.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 2026-09-03 (Jenna): failure / system emails go to jenna + jessie
# only, never liz. Same list as the other alerts in this package.
RECIPIENTS = [
    "jenna@crosswalknyc.com",
    "jessie@crosswalknyc.com",
]
SOURCE_ADDR = "BehavioralGraph <jenna@crosswalknyc.com>"
AWS_REGION = "us-east-2"

_STAMP_BUCKET = "dashboard-inputs"
_STAMP_PREFIX = "trends_iq_run_guard/"

LOCK_PATH = os.environ.get("TRENDS_RUN_LOCK",
                           "/var/lock/trends_scrapers_run_all.lock")

# A healthy run finishes in about five hours, so the alert lands at
# seven and a half. Picked against the incident: the 2026-09-15 run
# took 9h05m, so this would have said so around 19:30 local, roughly an
# hour before a colleague noticed the board was wrong, and with enough
# of the evening left to act on it.
EXPECTED_RUNTIME_MIN = int(os.environ.get("TRENDS_RUN_EXPECTED_MIN", "300"))
WATCHDOG_MULTIPLE = float(os.environ.get("TRENDS_RUN_WATCHDOG_MULT", "1.5"))

# A run still holding the lock past this is not slow, it is stuck.
STALE_RUN_HOURS = float(os.environ.get("TRENDS_RUN_STALE_HOURS", "20"))

# Two separate bars, because there are now two ways a row can reach
# the page without a reading of its own and they are not equally bad.
#
# Carried forward: the row is showing its own most recent reading,
# moved to today. It is about the right title and is only stale, so a
# few percent is unremarkable and a fifth of the board means the
# pricing pass is not keeping up.
#
# Rank tier: the row had no reading anywhere in the record, so its
# number comes from its position in the list and says nothing about
# the title. That is the one that made 2026-09-15 read low and
# uniform. It should be a handful of genuinely new chart entries, so
# the bar sits low.
CARRIED_SHARE_ALERT_PCT = float(
    os.environ.get("TRENDS_CARRIED_ALERT_PCT", "20.0"))
RANK_TIER_SHARE_ALERT_PCT = float(
    os.environ.get("TRENDS_RANK_TIER_ALERT_PCT", "2.0"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _already_sent_today(tag: str) -> bool:
    """One alert per tag per UTC day, so a watchdog that keeps firing
    does not turn into a mailbox full of the same sentence."""
    try:
        import boto3
        boto3.client("s3").head_object(
            Bucket=_STAMP_BUCKET,
            Key=f"{_STAMP_PREFIX}{_today_iso()}/{tag}.stamp",
        )
        return True
    except Exception:
        return False


def _mark_sent_today(tag: str) -> None:
    try:
        import boto3
        boto3.client("s3").put_object(
            Bucket=_STAMP_BUCKET,
            Key=f"{_STAMP_PREFIX}{_today_iso()}/{tag}.stamp",
            Body=str(int(time.time())).encode(),
            ContentType="text/plain",
        )
    except Exception as e:
        logger.info("run_guard: stamp write failed for %s: %s", tag, e)


def send_alert(tag: str, subject: str, body: str,
               *, force: bool = False) -> bool:
    """Send one operator alert. Deduped per tag per UTC day. Never
    raises."""
    try:
        if not force and _already_sent_today(tag):
            logger.info("run_guard: %s already alerted today", tag)
            return True
        import boto3
        boto3.client("ses", region_name=AWS_REGION).send_email(
            Source=SOURCE_ADDR,
            Destination={"ToAddresses": list(RECIPIENTS)},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        logger.warning("run_guard: alert sent (%s): %s", tag, subject)
        _mark_sent_today(tag)
        return True
    except Exception as e:
        logger.warning("run_guard: alert send failed (%s): %s", tag, e)
        return False


# ---------------------------------------------------------------------------
# Guard 1: one run at a time
# ---------------------------------------------------------------------------
class RunLock:
    """Non-blocking exclusive lock around the whole run.

    Use as a context manager. `acquired` is False when another run
    already holds it, in which case the caller should exit rather than
    start a second pass over the same snapshots. Two runs writing the
    same `latest/*.json` keys interleave their writes and neither
    output is trustworthy.

    Holds the lock through an OS file lock rather than a pid file, so
    it releases automatically if the process is killed.
    """

    def __init__(self, path: str = LOCK_PATH):
        self.path = path
        self.acquired = False
        self._fh = None

    def __enter__(self) -> "RunLock":
        try:
            import fcntl
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._fh = open(self.path, "a+")
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError):
                self._on_contended()
                self._close()
                return self
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"{os.getpid()}\n{_now_iso()}\n")
            self._fh.flush()
            self.acquired = True
        except Exception as e:
            # A broken lock must never stop the night's data. Run
            # unlocked and say so.
            logger.warning("run_guard: could not take the run lock (%s); "
                            "continuing without it", e)
            self.acquired = True
        return self

    def _on_contended(self) -> None:
        holder_pid, holder_started = self._read_holder()
        age_h = self._holder_age_hours(holder_started)
        logger.error("run_guard: another run already holds %s "
                      "(pid=%s started=%s age=%sh); exiting",
                      self.path, holder_pid or "?", holder_started or "?",
                      f"{age_h:.1f}" if age_h is not None else "?")
        if age_h is not None and age_h >= STALE_RUN_HOURS:
            send_alert(
                "stale_run",
                "Trends IQ: previous scraper run is still going",
                "Tonight's Trends IQ run did not start. The previous run "
                "is still holding the run lock.\n\n"
                f"  holder pid   : {holder_pid or 'unknown'}\n"
                f"  started (UTC): {holder_started or 'unknown'}\n"
                f"  running for  : {age_h:.1f} hours\n"
                f"  lock file    : {self.path}\n\n"
                "A run this old is stuck rather than slow. Check it, and "
                "if it is not doing any work, end it so tonight's run "
                "can take over.\n\n"
                "  ps -eo pid,lstart,etime,pcpu,args | grep run_all\n"
                "  tail -50 /var/log/trends_scrapers.log\n",
            )
        else:
            send_alert(
                "overlapping_run",
                "Trends IQ: scraper run skipped, one was already going",
                "Tonight's Trends IQ run exited without starting because "
                "the previous one had not finished.\n\n"
                f"  holder pid   : {holder_pid or 'unknown'}\n"
                f"  started (UTC): {holder_started or 'unknown'}\n"
                f"  running for  : "
                f"{f'{age_h:.1f} hours' if age_h is not None else 'unknown'}\n\n"
                "Data is not lost. The run in progress still publishes. "
                "If this repeats, the run is outgrowing its nightly "
                "window.\n",
            )

    def _read_holder(self) -> tuple[Optional[str], Optional[str]]:
        try:
            with open(self.path) as fh:
                lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
            return (lines[0] if lines else None,
                    lines[1] if len(lines) > 1 else None)
        except Exception:
            return None, None

    @staticmethod
    def _holder_age_hours(started_iso: Optional[str]) -> Optional[float]:
        if not started_iso:
            return None
        try:
            started = datetime.fromisoformat(started_iso)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - started).total_seconds() / 3600.0
        except Exception:
            return None

    def _close(self) -> None:
        try:
            if self._fh:
                self._fh.close()
        except Exception:
            pass
        self._fh = None

    def __exit__(self, *exc) -> None:
        try:
            if self._fh:
                import fcntl
                fcntl.flock(self._fh, fcntl.LOCK_UN)
        except Exception:
            pass
        self._close()
        return None


# ---------------------------------------------------------------------------
# Guard 2: runtime watchdog
# ---------------------------------------------------------------------------
def start_watchdog(expected_minutes: int = EXPECTED_RUNTIME_MIN,
                   multiple: float = WATCHDOG_MULTIPLE) -> threading.Event:
    """Alert once if the run is still going well past its normal time.

    Runs on a daemon thread, so it still fires when the main thread is
    blocked. That is the case that matters: the 2026-07-16 run sat
    blocked on a futex for 61 days and could not have reported on
    itself from the main thread.

    Returns the Event to set on a clean finish.
    """
    done = threading.Event()
    threshold_min = max(1.0, expected_minutes * multiple)

    def _watch() -> None:
        started = time.time()
        # Wake on a coarse interval; the alert is hours out and there
        # is nothing to gain from polling faster.
        while not done.wait(timeout=300):
            elapsed_min = (time.time() - started) / 60.0
            if elapsed_min < threshold_min:
                continue
            send_alert(
                "run_overrun",
                "Trends IQ: nightly run is taking much longer than usual",
                "The Trends IQ scraper run is still going well past its "
                "normal finish.\n\n"
                f"  running for : {elapsed_min / 60.0:.1f} hours\n"
                f"  normal      : about {expected_minutes / 60.0:.1f} hours\n"
                f"  pid         : {os.getpid()}\n\n"
                "While a run is unfinished, rows it has not priced yet "
                "show their own most recent reading rather than one "
                "taken today. Worth checking whether it is working or "
                "stuck.\n\n"
                "  tail -50 /var/log/trends_scrapers.log\n",
            )
            return

    threading.Thread(target=_watch, daemon=True,
                     name="trends-run-watchdog").start()
    logger.info("run_guard: watchdog armed, alerts past %.1f hours",
                 threshold_min / 60.0)
    return done


# ---------------------------------------------------------------------------
# Guard 3: quality alarm on the published board
# ---------------------------------------------------------------------------
def _worst_lists(by_list: dict, field: str, limit: int = 15) -> str:
    worst = sorted(by_list.items(),
                   key=lambda kv: kv[1].get(f'{field}_pct', 0.0),
                   reverse=True)[:limit]
    return "\n".join(
        f"  {name:<44} {v.get(f'{field}_pct', 0.0):5.1f}%  "
        f"({v.get(field, 0)}/{v.get('total', 0)})"
        for name, v in worst if v.get(field)
    ) or "  (per-list detail unavailable)"


def check_baseline_share(summary: dict[str, Any],
                         *,
                         carried_pct: float = CARRIED_SHARE_ALERT_PCT,
                         rank_tier_pct: float = RANK_TIER_SHARE_ALERT_PCT,
                         ) -> Optional[dict]:
    """Alert on how the board came by its numbers.

    Two populations, measured and alerted separately.

    Carried forward: the row is showing its own most recent reading
    moved to today. Honest and about the right title, just older than
    today. A large share means the pricing pass is falling behind.

    Rank tier: the row had no reading anywhere, so its number comes
    from its slot in the list. It looks like a real audience figure
    and carries nothing about the title, which is why two titles at
    the same rank read almost the same on 2026-09-15. This should be
    a handful of genuinely new chart entries and nothing more.

    `summary` is the coverage gate's return dict. Returns
    `{'carried_pct', 'rank_tier_pct'}`, or None when it could not be
    read.
    """
    try:
        total = int(summary.get('total') or 0)
        carried = summary.get('carried_after')
        rank_tier = summary.get('rank_tier_after')
        if not total or (carried is None and rank_tier is None):
            logger.info("run_guard: no provenance shares to check")
            return None
        carried = int(carried or 0)
        rank_tier = int(rank_tier or 0)
        c_share = 100.0 * carried / total
        r_share = 100.0 * rank_tier / total
        by_list = summary.get('by_list') or {}
        logger.info("run_guard: %.2f%% of rendered rows carried forward "
                     "(%d/%d), %.2f%% on the rank tier (%d/%d)",
                     c_share, carried, total, r_share, rank_tier, total)

        if r_share > rank_tier_pct:
            send_alert(
                "rank_tier_share",
                f"Trends IQ: {r_share:.0f}% of rows are showing a "
                f"placeholder audience number",
                "After tonight's run, rows on the Trends IQ board are "
                "showing an audience number derived from the row's "
                "position in its list rather than from the row "
                "itself.\n\n"
                f"  rows on the placeholder : {rank_tier} of {total} "
                f"({r_share:.1f}%)\n"
                f"  alerts above            : {rank_tier_pct:.1f}%\n"
                f"  normal                  : a handful of new chart "
                f"entries\n\n"
                "These read like real audience numbers on the page but "
                "carry nothing about the individual title, so items at "
                "the same rank look near identical. A row only lands "
                "here when it has no reading anywhere in the record, "
                "so a large share means either a flood of new titles "
                "or a lost store.\n\n"
                "Worst lists:\n"
                + _worst_lists(by_list, 'rank_tier') + "\n\n"
                "  grep -E 'coverage_gate|stream_estimates' "
                "/var/log/trends_scrapers.log | tail -40\n",
            )

        if c_share > carried_pct:
            send_alert(
                "carried_share",
                f"Trends IQ: {c_share:.0f}% of rows are showing an "
                f"older reading",
                "After tonight's run, a large share of rows on the "
                "Trends IQ board are showing their own most recent "
                "reading rather than one taken today.\n\n"
                f"  rows carried forward : {carried} of {total} "
                f"({c_share:.1f}%)\n"
                f"  alerts above         : {carried_pct:.1f}%\n\n"
                "Each number is about the right title, so the board is "
                "not wrong, but it is older than it should be. This "
                "usually means the pricing pass did not get through "
                "its list.\n\n"
                "Worst lists:\n"
                + _worst_lists(by_list, 'carried') + "\n\n"
                "  grep -E 'coverage_gate|stream_estimates' "
                "/var/log/trends_scrapers.log | tail -40\n",
            )

        return {'carried_pct': round(c_share, 2),
                'rank_tier_pct': round(r_share, 2)}
    except Exception as e:
        logger.warning("run_guard: provenance share check failed: %s", e)
        return None


# Chi-square on 9 degrees of freedom. 16.92 is p=0.05 and 21.67 is
# p=0.01; alert at the looser bound so a real drift is caught while a
# single unlucky night is not.
DIGIT_CHISQ_ALERT = float(os.environ.get("TRENDS_DIGIT_CHISQ_ALERT", "21.67"))
DIGIT_ZERO_MIN_PCT = float(os.environ.get("TRENDS_DIGIT_ZERO_MIN_PCT", "7.0"))


def check_last_digit_distribution(values,
                                  *,
                                  chisq_alert: float = DIGIT_CHISQ_ALERT,
                                  zero_min_pct: float = DIGIT_ZERO_MIN_PCT,
                                  ) -> Optional[dict]:
    """Alert when the audience numbers stop looking counted.

    A real count ends in each digit about a tenth of the time. An
    earlier build forced every value off zero to avoid looking round,
    which left zero unused across the whole corpus. A client analyst
    found it, because a missing digit is a far louder signal than the
    roundness it was hiding.

    Anything that rewrites values in bulk can reintroduce it by
    accident, and a uniqueness pass is the likeliest culprit, since
    the cheapest way to make values distinct is to skip digits. This
    watches for that. Returns the histogram and chi-square, or None if
    there was nothing to measure.
    """
    try:
        counts = [0] * 10
        n = 0
        for v in values:
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                continue
            counts[v % 10] += 1
            n += 1
        # Below a few thousand values the test is too noisy to act on.
        if n < 2_000:
            logger.info("run_guard: only %d values, skipping digit check", n)
            return None

        expected = n / 10.0
        chisq = sum((c - expected) ** 2 / expected for c in counts)
        zero_pct = 100.0 * counts[0] / n
        logger.info("run_guard: last-digit chi-square %.2f on 9 df, "
                     "zeros %.2f%% of %d values", chisq, zero_pct, n)

        if chisq > chisq_alert or zero_pct < zero_min_pct:
            spread = "\n".join(
                "  ends in %d : %6d  %5.2f%%" % (d, counts[d],
                                                 100.0 * counts[d] / n)
                for d in range(10))
            send_alert(
                "last_digit_distribution",
                "Trends IQ: the audience numbers have stopped looking "
                "counted",
                "The last digit of every audience number on the board "
                "should be evenly spread, because a real count is as "
                "likely to end in one digit as another.\n\n"
                f"{spread}\n\n"
                f"  values measured : {n}\n"
                f"  chi-square      : {chisq:.2f} on 9 df "
                f"(alerts above {chisq_alert:.2f})\n"
                f"  ending in zero  : {zero_pct:.2f}% "
                f"(alerts below {zero_min_pct:.1f}%)\n\n"
                "An uneven spread means something rewrote the numbers "
                "in a way that favours some endings over others. The "
                "usual cause is a pass that nudges values to make them "
                "distinct and skips certain digits while doing it. "
                "Numbers that avoid an ending are easier for an "
                "outside reader to spot than the roundness such a rule "
                "is meant to prevent.\n\n"
                "  python3 scripts/test_last_digit_distribution.py\n",
            )

        return {'chisq': round(chisq, 2),
                'zero_pct': round(zero_pct, 2),
                'counts': counts,
                'n': n}
    except Exception as e:
        logger.warning("run_guard: last-digit check failed: %s", e)
        return None
