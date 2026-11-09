"""The weekly review: what it names and counts, and what stands in for a degenerate answer."""
import time
from datetime import datetime, timezone

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import curation
from assistant_world import execute

VENUE_END = "cy-inbox-02-thread-end.eml"        # Mae's last word in Cy's Gmail thread


def test_hwf_the_review_names_a_waiting_thread_by_its_thread_not_its_old_pk(app, world, ai):
    now = int(time.time())
    execute("INSERT INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts, "
            "due_ts, created_at) VALUES(?,?,?,?,?,?,?,?)", w.CY, world.row(VENUE_END)["thread_id"],
            "sent", "due", now - 3 * 86400, now - 3 * 86400, now - 86400, "2026-10-30T12:00:00+00:00")
    r = app.post("/api/review", {}).json
    assert [x["subject"] for x in r["waiting"]] == ["Re: Guild meetup venue"]


def test_hwf_a_degenerate_review_falls_back_but_keeps_the_ai_picks(app, world, ai):
    ai.queue(fa.answer({"narrative": "test", "next": ["Answer Mae about the hall"]}))
    r = app.post("/api/review", {}).json
    assert r["model"] == "fallback" and r["narrative"].startswith("In the last 7 days you sent")
    assert r["next"] == ["Answer Mae about the hall"]


def test_the_review_counts_only_this_accounts_cleanups(app, world, ai):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        curation.sqlite.unsub_log_put(c, "news@loom.example.test", "post", "ok", 200,
                            datetime.now(timezone.utc).isoformat(), "unsub-loom-1", [w.ADA])
        c.commit()
    finally:
        c.close()
    from mailmind.bootstrap.assistant import reviews
    assert reviews.build({w.ADA})["stats"]["cleaned"] == 1
    assert reviews.build({w.BEA})["stats"]["cleaned"] == 0    # Loom only ever wrote to Ada
