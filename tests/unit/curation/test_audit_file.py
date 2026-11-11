"""The audit trails through mailmind.curation.adapters.audit_file: a line a record, appended; a sender's cleanup that
can't be written stops nothing; and a read merges both trails newest first, past a torn line."""
import json

from mailmind.curation.adapters.audit_file import AuditFile
from mailmind.platform import settings

ADA = "ada@example.test"
ROW = {"uid": "7", "from": "bot@notify-example.test", "subject": "Your weekly digest " * 20, "bucket": "promo",
       "reason": "blocked sender"}


def lines(path):
    return [json.loads(line) for line in open(path).read().splitlines()]


def test_a_sweep_appends_a_line_a_message_with_the_subject_cut(data_dir):
    AuditFile().swept(ADA, [ROW, {**ROW, "uid": "8", "subject": "short"}])
    AuditFile().swept(ADA, [])
    got = lines(settings.current().sweep_audit)
    assert [(g["kind"], g["account"], g["uid"], g["bucket"], g["reason"]) for g in got] == \
        [("sweep", ADA, "7", "promo", "blocked sender"), ("sweep", ADA, "8", "promo", "blocked sender")]
    assert len(got[0]["subject"]) == 140 and got[1]["subject"] == "short"
    assert got[0]["ts"] == got[1]["ts"]


def test_a_cleanup_appends_its_line(data_dir):
    AuditFile().cleaned_up("bot@notify-example.test", "notify-example.test", 12, "ok")
    [got] = lines(settings.current().sweep_audit)
    assert {k: got[k] for k in ("kind", "from", "token", "swept", "status")} == \
        {"kind": "sender_cleanup", "from": "bot@notify-example.test", "token": "notify-example.test", "swept": 12, "status": "ok"}


def test_a_cleanup_that_cant_be_written_stops_nothing(data_dir, tmp_path):
    with settings.use(settings.Settings(home=str(tmp_path / "gone"))):
        AuditFile().cleaned_up("bot@notify-example.test", "notify-example.test", 12, "ok")


def test_with_no_trail_there_is_nothing_to_read(data_dir):
    assert AuditFile().read() == {"events": [], "has_more": False}


def test_a_read_merges_both_trails_newest_first_past_a_torn_or_blank_line(data_dir):
    s = settings.current()
    with open(s.sweep_audit, "w") as f:
        f.write(json.dumps({"kind": "sweep", "ts": "2026-11-01T10:00:00+00:00"}) + "\n\n")
        f.write(json.dumps({"kind": "sweep", "ts": "2026-11-02T10:00:00+00:00"}) + "\n")
        f.write('{"kind": "sweep", "ts": "2026-11-0')
    with open(s.reconcile_audit, "w") as f:
        f.write(json.dumps({"kind": "reconcile", "ts": "2026-11-01T12:00:00+00:00"}) + "\n")
    assert [(e["kind"], e["ts"][:13]) for e in AuditFile().read()["events"]] == \
        [("sweep", "2026-11-02T10"), ("reconcile", "2026-11-01T12"), ("sweep", "2026-11-01T10")]


def test_a_read_says_when_there_is_more_than_it_gave(data_dir):
    AuditFile().swept(ADA, [{**ROW, "uid": str(u)} for u in range(5)])
    assert (len(AuditFile().read(limit=3)["events"]), AuditFile().read(limit=3)["has_more"]) == (3, True)
    assert AuditFile().read(limit=5)["has_more"] is False
