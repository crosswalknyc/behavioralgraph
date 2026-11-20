# The Prometheus behaviour corpus and replay gate

Rebuild stage 1 (2026-11-20). The question log is the specification:
every real ask of the last 30 days, with what was open, the thread's last
eight turns, the Reader's reading and what the reader got. The replay runs
those asks through `service.ask` (the one entry behind
`POST /api/prometheus/v1/ask`) with nothing live behind it and reports
drift by intent class. Every later stage runs it before it ships.

## What is where

| Piece | Path | Needs |
|---|---|---|
| Corpus builder | `scripts/pm_corpus/build_corpus.py` | staff AWS credentials |
| Replay runner | `scripts/pm_corpus/replay_corpus.py` | the parent repo's `scripts/` for door mode |
| Intent classes | `prometheus/intent_classes.py` | nothing |
| Sanitized fixtures | `scripts/pm_corpus/fixtures/sanitized_cases.jsonl` | nothing |
| Unit test | `scripts/test_pm_replay_corpus.py` | nothing |
| Child isolation | `prometheus/replay.py` (fake boto3, canned model) | `scripts/_pm_behavior.py` in the parent repo |

The real corpus lives in two places and nowhere else:

- `~/.cache/crosswalk_replay/corpus/<stamp>/cases.jsonl` (mode 600) with
  `manifest.json` (counts only) and `seats.json` (the alias map, local only)
- `s3://dashboard-inputs/system/pm_replay_corpus/<stamp>/cases.jsonl.gz`
  (the staff-only `system/` prefix of the private dashboard bucket)

It carries client text. It is never committed, never printed by the
tools, never written under a client-visible key. The committed fixtures
are invented text only.

## Run it

```bash
set -a && . bg-webapp/.env; set +a
export REGRESSION_TEST_MODE=1

# 1. build (or refresh) the corpus from the last 30 days of the ask log
#    (an operator tool: run it with REGRESSION_TEST_MODE unset, the test guard refuses its upload otherwise)
env -u REGRESSION_TEST_MODE python3 scripts/pm_corpus/build_corpus.py --days 30

# 2. replay it on the branch you are about to ship
python3 scripts/pm_corpus/replay_corpus.py --corpus latest

# 3. compare with the saved baseline from main (the gate for a stage)
python3 scripts/pm_corpus/replay_corpus.py --corpus latest \
    --baseline ~/.cache/crosswalk_replay/results/<main stamp>.json

# no credentials: the committed sanitized set, lane level
python3 scripts/pm_corpus/replay_corpus.py --fixtures --mode service
```

On a machine that has not built the corpus, `--fetch` pulls the latest
one down from S3 first. Door mode needs the parent repo's `scripts/`
(it holds `_pm_behavior.py`, the chat module's offline harness); the
runner finds it beside `bg-webapp/` or through `PM_PARENT_SCRIPTS`.

Results land in `~/.cache/crosswalk_replay/results/<stamp>.json` (mode
600): the summary, the per-class table and one entry per case with the
recorded and live route, outcome, kind, decision and verdict. The
console prints the summary only.

## What the replay sees

Two modes:

- **door** (default): the chat module is loaded in a child process with
  a fake S3, no ClickHouse, mail off and `REGRESSION_TEST_MODE=1`, and
  without this machine's keys (`prometheus.replay.STRIP_ENV`: the model
  and store keys are taken out of the child's environment and the
  credential files point at `/dev/null`, so a module that opens its own
  client fails fast instead of reaching anything live). Each case posts
  to `/api/prometheus/v1/ask` as a probe (`X-Prometheus-Caller`), so no
  thread is minted, nothing is banked, nothing is charged. The model is
  canned per case: the Reader hands back the reading that was logged for
  that ask (or nothing, when none was logged, which is what production
  did), the analyze core gets one short reply, the interpret core gets a
  draft. Every lane and the cores' own regex layers run for real. A
  difference only the model could have produced is not a regression.
- **service**: `service.ask` runs in this process on a stub host with the
  three cores stubbed to markers. Lane level only. The unit test uses it
  and it needs no child and no parent repo.

What it cannot see: the library (empty unless a catalog snapshot is
bound, see `prometheus.replay.write_catalog_snapshot`), the seat's plan
and funds (every case runs as one unlimited probe seat), a reply the
model wrote, a job that landed later.

## Reading the report

```
replayed 1083 asks in door mode (74s): same 812, drift 231, regressed 14, fail 0, unchecked 26; class agreement 91.2%
by class:
  read             n= 498 agree= 95.0%  same=430 drift=52 ...
```

- **same**: the live route and outcome are the recorded ones.
- **drift**: the route or outcome moved and neither side is broken. A
  recorded outcome that production later corrected (`answered_by_correction`),
  a reply that now asks first, a now-priced offer, a move off a reply the
  reader pushed back on all land here. Read them; most are the point of a fix.
- **regressed**: a good recorded outcome now declines or dead-ends.
- **fail**: the live reply is broken (an error, empty, faulted copy, a
  leaked internal token). Always blocks.
- **unchecked**: a difference the harness cannot judge (state it does
  not carry, a replay fault).
- **class agreement**: the share of cases whose live class is the
  recorded class or its neighbour (`prometheus.intent_classes.near`).

The gate (`gate.ok`): with a baseline, zero newly `failed`, zero newly
`regressed`, and no class whose agreement drops more than 5 points; a
fail the baseline already carried is reported and belongs to whoever
owns that template. Without a baseline any fail blocks and the
regressions are listed for reading: the recorded route is what shipped
at the time, and 313 Prometheus commits moved some of it on purpose.

The verdict rules are `prometheus.replay.compare`; the class table is
`prometheus.intent_classes`. Both are shared with the stage 2
Understanding's shadow grading, so "agreement" means one thing.

### The Understanding's shadow grade (stage 2)

When the code under replay carries `prometheus/understanding.py`, each
entry also has `u`: the Understanding's intent, confidence, source
(`features`, `reader`, `reconciled`), action, scope flags, whether a
flipped executor ran or stood down on it, and whether its intent lands
in the live route's class and the recorded route's class. The summary
prints the agreement overall and per class:

```
understanding (shadow): on 1069 asks, intent agrees with the live route 81.9% and with the recorded route 81.9%; 19 answered by a flipped executor
```

In production the same line rides every ask-log record under
`extra.u`; `prometheus.understanding.grade_log_rows` reads a day of
records the same way. The kill switches are environment only:
`PM_UNDERSTANDING=0` for the step, `PM_U_OFF_<INTENT>=1` for one flipped
intent (`WAYFINDING`, `STATUS`, `SEND_FILE_ORDER`, `INSIGHT_ASK`,
`WIZARD_INTERRUPT`, `VALUATION_REFERENT`, `BRAND_FIT`).

## Refreshing the corpus

Rebuild it when the window should move (monthly is enough), or after a
stretch of real traffic you want in the gate. Each build is a new stamp;
`latest` points at the newest. The baseline for a stage is the results
file of a run on `main` against the same stamp; keep that file and name
it in the stage's report.

## Adding a fixture

A fixture is one line in `fixtures/sanitized_cases.jsonl` in the case
shape, with invented names (never a client's words, never a seat, never
a thread id) and `recorded` set to what `service.ask` does on `main` in
service mode. Avoid the house's banned words inside an invented name
(a show called "Sample Show" reaches the lanes as "data Show"). A case
whose current answer is the wrong lane carries `known_miss` and
`expected`; the stage that fixes it moves `recorded` and drops the mark.
