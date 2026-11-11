"""The sweep trail through mailmind.curation.adapters.audit_events: a record a row of `audit_events`, kept as the JSON a line
of sweep_audit.jsonl held; a sender's cleanup that can't be written stops nothing; a read merges both trails newest first; a
record is committed when the call returns, so the move after it may ask the server; and nothing is kept beside the
encrypted database."""
import json

import time_machine

from conftest import NOW
from mailmind.curation.adapters.audit_events import AuditEvents
from mailmind.mailbox.adapters import reconcile_trail
from mailmind.platform import settings
from mailmind.platform.db import txguard
from mailmind.platform.db.uow import UnitOfWork

ADA = "ada@example.test"
ROW = {"uid": "7", "from": "bot@notify-example.test", "subject": "Your weekly digest " * 20, "bucket": "promo",
       "reason": "blocked sender"}


def kept(trail="sweep"):
    """The trail's rows as (ts, body), in the order written."""
    with UnitOfWork().read() as c:
        return [(r["ts"], r["body"]) for r in c.execute("SELECT ts, body FROM audit_events WHERE trail=? ORDER BY id",
                                                        (trail,))]


def records(trail="sweep"):
    return [json.loads(body) for _, body in kept(trail)]


def pruned(account, n):
    with UnitOfWork().write() as c:
        reconcile_trail.pruned(c, [{"account": account, "live": 9, "stored": 10, "ghosts": n, "pruned": n, "error": None,
                                    "folders": 1}])


def test_a_sweep_keeps_a_record_a_message_with_the_subject_cut(data_dir):
    AuditEvents().swept(ADA, [ROW, {**ROW, "uid": "8", "subject": "short"}])
    AuditEvents().swept(ADA, [])
    got = records()
    assert [(g["kind"], g["account"], g["uid"], g["bucket"], g["reason"]) for g in got] == \
        [("sweep", ADA, "7", "promo", "blocked sender"), ("sweep", ADA, "8", "promo", "blocked sender")]
    assert len(got[0]["subject"]) == 140 and got[1]["subject"] == "short"
    assert got[0]["ts"] == got[1]["ts"]


def test_a_cleanup_keeps_its_record(data_dir):
    AuditEvents().cleaned_up("bot@notify-example.test", "notify-example.test", 12, "ok")
    [got] = records()
    assert {k: got[k] for k in ("kind", "from", "token", "swept", "status")} == \
        {"kind": "sender_cleanup", "from": "bot@notify-example.test", "token": "notify-example.test", "swept": 12, "status": "ok"}


def test_each_record_is_the_line_its_trail_held_and_its_ts_is_its_own(data_dir):
    """So a line of the old file goes in as it is: its text the body, its "ts" the column."""
    AuditEvents().swept(ADA, [ROW])
    AuditEvents().cleaned_up("bot@notify-example.test", "notify-example.test", 12, "ok")
    [(sweep_ts, sweep), (cleanup_ts, cleanup)] = kept()
    assert list(json.loads(sweep)) == ["kind", "ts", "account", "uid", "from", "subject", "bucket", "reason"]
    assert list(json.loads(cleanup)) == ["kind", "ts", "from", "token", "swept", "status"]
    assert sweep == json.dumps(json.loads(sweep)) and cleanup == json.dumps(json.loads(cleanup))
    assert (sweep_ts, cleanup_ts) == (json.loads(sweep)["ts"], json.loads(cleanup)["ts"])


def test_a_cleanup_that_cant_be_written_stops_nothing(data_dir, tmp_path, capsys):
    with settings.use(settings.Settings(home=str(tmp_path / "gone"))):
        AuditEvents().cleaned_up("bot@notify-example.test", "notify-example.test", 12, "ok")
    assert records() == []
    assert "[cleanup] a cleanup's record wasn't written" in capsys.readouterr().out


def test_a_sweeps_record_is_committed_before_the_move_asks_the_server(data_dir):
    AuditEvents().swept(ADA, [ROW])
    txguard.refuse_network("the move to Trash")             # no transaction is left open on this thread
    assert [r["uid"] for r in records()] == ["7"]            # and another connection reads it


def test_with_nothing_kept_there_is_nothing_to_read(data_dir):
    assert AuditEvents().read() == {"events": [], "has_more": False}


def test_a_read_merges_both_trails_newest_first(data_dir):
    with UnitOfWork().write() as c:
        for trail, ts in (("sweep", "2026-11-01T10:00:00+00:00"), ("sweep", "2026-11-02T10:00:00+00:00"),
                          ("reconcile", "2026-11-01T12:00:00+00:00")):
            c.execute("INSERT INTO audit_events(trail, ts, body) VALUES(?,?,?)",
                      (trail, ts, json.dumps({"kind": trail, "ts": ts})))
    assert [(e["kind"], e["ts"][:13]) for e in AuditEvents().read()["events"]] == \
        [("sweep", "2026-11-02T10"), ("reconcile", "2026-11-01T12"), ("sweep", "2026-11-01T10")]


def test_a_read_of_one_moment_keeps_the_order_it_was_written_in(data_dir):
    with time_machine.travel(NOW, tick=False):
        AuditEvents().swept(ADA, [{**ROW, "uid": str(u)} for u in (3, 12, 4)])
        pruned(ADA, 2)
        AuditEvents().cleaned_up("bot@notify-example.test", "notify-example.test", 3, "ok")
    assert [e.get("uid", e["kind"]) for e in AuditEvents().read()["events"]] == ["3", "12", "4", "reconcile", "sender_cleanup"]


def test_a_read_says_when_there_is_more_than_it_gave(data_dir):
    AuditEvents().swept(ADA, [{**ROW, "uid": str(u)} for u in range(5)])
    assert (len(AuditEvents().read(limit=3)["events"]), AuditEvents().read(limit=3)["has_more"]) == (3, True)
    assert AuditEvents().read(limit=5) == {"events": AuditEvents().read()["events"], "has_more": False}
    assert AuditEvents().read(limit=0) == AuditEvents().read(limit=-1) == {"events": [], "has_more": True}


def test_nothing_is_kept_beside_the_encrypted_database(data_dir):
    """A record names who wrote and what about: it is in the database, encrypted, and no file beside it says so."""
    AuditEvents().swept(ADA, [{**ROW, "subject": "Quarterly appraisal for Ada"}])
    AuditEvents().cleaned_up("bot@notify-example.test", "notify-example.test", 12, "ok")
    pruned(ADA, 1)
    assert sorted(p.name for p in data_dir.iterdir() if not p.name.startswith("mailmind.db")) == []
    for p in data_dir.iterdir():
        held = p.read_bytes()
        assert b"Quarterly appraisal" not in held and b"notify-example" not in held, p.name
