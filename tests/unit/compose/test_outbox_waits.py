"""What a queued send is waiting for, what's still owed once it has gone out, and a Retry that finds it went."""
import time

from fixtures import world as w
from mailmind.platform.db.migrations import m0041_outbox_wait_kind


def test_the_upgrade_reads_what_is_already_queued(data_dir):
    from mailmind.bootstrap.compose import book
    from mailmind.bootstrap.mailbox import store
    now = int(time.time())
    c = store.conn()

    def queued(key, due, attempts=0, state="queued"):
        oid, _ = book.outbox_insert(c, {"account": w.ADA, "idempotency_key": key, "message_id": f"<{key}@example.test>",
                                        "envelope": "[]", "recipients": "{}", "subject": key,
                                        "raw": b"x", "next_attempt_at": due, "draft_id": None,
                                        "created_at": "2026-11-02T12:00:00+00:00"})
        c.execute("UPDATE outbox SET attempts=?, state=? WHERE id=?", (attempts, state, oid))
        return oid
    in_window, scheduled = queued("window", now + 10), queued("picked", now + 3600)
    backing_off, done = queued("failed", now + 3600, attempts=2), queued("done", now + 3600, state="sent")
    for _ in range(2):                                         # init runs on every start
        m0041_outbox_wait_kind.migrate(c)
    kinds = {r["id"]: r["wait_kind"] for r in c.execute("SELECT id, wait_kind FROM outbox")}
    c.close()
    assert kinds == {in_window: "grace", scheduled: "scheduled", backing_off: "backoff", done: "grace"}
