"""The replayable behaviour corpus for Prometheus (rebuild stage 1).

Three pieces, each a script and a module:

    build_corpus.py    the ask log + the threads -> cases (local cache + staff-only S3)
    replay_corpus.py   cases -> through service.ask, hermetic, drift by intent class
    fixtures/          a small sanitized set (invented text) the unit test and a
                       creds-less run use

See README.md beside this file. Client text never leaves
~/.cache/crosswalk_replay/ and s3://dashboard-inputs/system/pm_replay_corpus/.
"""
