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
`check_baseline_share` (alert when too many rendered rows fall back to
the rank-tier baseline).

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

# A healthy run has been finishing in about five hours. Alert at two
# hours past six, which clears normal variance and still leaves most of
# a working day to react before the next cron.
EXPECTED_RUNTIME_MIN = int(os.environ.get("TRENDS_RUN_EXPECTED_MIN", "360"))
WATCHDOG_MULTIPLE = float(os.environ.get("TRENDS_RUN_WATCHDOG_MULT", "1.5"))

# A run still holding the lock past this is not slow, it is stuck.
STALE_RUN_HOURS = float(os.environ.get("TRENDS_RUN_STALE_HOURS", "20"))

# Share of rendered rows allowed to sit on the rank-tier baseline
# before the board is considered wrong. A clean run lands at or near
# zero; 2026-09-15 was most of the board.
BASELINE_SHARE_ALERT_PCT = float(
    os.environ.get("TRENDS_BASELINE_ALERT_PCT", "5.0"))


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
                "show a value derived from their rank position rather "
                "than their own. Worth checking whether it is working "
                "or stuck.\n\n"
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
def check_baseline_share(summary: dict[str, Any],
                         *, threshold_pct: float = BASELINE_SHARE_ALERT_PCT,
                         ) -> Optional[float]:
    """Alert when too much of the board is on the rank-tier baseline.

    A rank-derived value carries no signal about the item it is
    attached to: two different titles at the same rank on the same list
    get near enough the same number. That is what made the 2026-09-15
    board look both too low and too uniform. A clean run leaves this at
    or near zero.

    `summary` is the coverage gate's return dict. Returns the measured
    share, or None when it could not be read.
    """
    try:
        total = int(summary.get('total') or 0)
        baseline = summary.get('baseline_after')
        if not total or baseline is None:
            logger.info("run_guard: no baseline share to check")
            return None
        share = 100.0 * int(baseline) / total
        by_list = summary.get('baseline_by_list') or {}
        logger.info("run_guard: %.2f%% of rendered rows on the rank-tier "
                     "baseline (%d/%d)", share, int(baseline), total)
        if share <= threshold_pct:
            return share

        worst = sorted(by_list.items(),
                       key=lambda kv: kv[1].get('pct', 0.0),
                       reverse=True)[:15]
        lines = "\n".join(
            f"  {name:<44} {v.get('pct', 0.0):5.1f}%  "
            f"({v.get('baseline', 0)}/{v.get('total', 0)})"
            for name, v in worst
        ) or "  (per-list detail unavailable)"
        send_alert(
            "baseline_share",
            f"Trends IQ: {share:.0f}% of rows are showing a "
            f"placeholder audience number",
            "After tonight's run, a large share of rows on the Trends "
            "IQ board are showing an audience number derived from the "
            "row's rank position rather than from the row itself.\n\n"
            f"  rows on the placeholder : {int(baseline)} of {total} "
            f"({share:.1f}%)\n"
            f"  alerts above            : {threshold_pct:.1f}%\n"
            f"  normal                  : at or near 0%\n\n"
            "These read like real audience numbers on the page but "
            "carry no information about the individual title, so items "
            "at the same rank look near identical.\n\n"
            "Worst lists:\n" + lines + "\n\n"
            "Usually this means the pricing pass did not finish or its "
            "stored values were lost.\n\n"
            "  grep -E 'coverage_gate|stream_estimates' "
            "/var/log/trends_scrapers.log | tail -40\n",
        )
        return share
    except Exception as e:
        logger.warning("run_guard: baseline share check failed: %s", e)
        return None
