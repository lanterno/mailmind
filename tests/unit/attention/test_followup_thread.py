"""A follow-up is kept by its account and thread: what Waiting and Reply Later show is the thread's newest message as it
stands."""
import time

from fixtures import world as w
from mailmind.bootstrap import accounts, attention
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import mailbox

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # the newest of Ada's lunch thread that isn't hers


def newest_of(thread_id):
    """The thread's newest copy, as a list names one: by its key and folder."""
    c = store.conn()
    try:
        pk = c.execute("SELECT pk FROM messages WHERE account=? AND thread_id=? ORDER BY ts DESC, pk DESC LIMIT 1",
                       (w.ADA, thread_id)).fetchone()["pk"]
    finally:
        c.close()
    return w.copy_of(pk)


def listed(app, view):
    return [{"key": i["key"], "folder": i["folder"], "uid": i["uid"]} for i in app.get(f"/api/messages?account={w.ADA}&folder={view}").json["items"]]


def nudge_on(thread_id):
    c = store.conn()
    try:
        attention.book.followup_record_sent(c, w.ADA, thread_id, int(time.time()), "2026-11-01T00:00:00+00:00")
        c.execute("UPDATE followups SET state='due', due_ts=? WHERE thread_id=?", (int(time.time()), thread_id))
        c.commit()
    finally:
        c.close()


def test_a_nudge_lists_its_thread(app, world):
    thread = world.row(LUNCH)["thread_id"]
    nudge_on(thread)
    assert listed(app, "__waiting__") == [newest_of(thread)]


def test_a_pinned_thread_lists_itself_and_unpins(app, world):
    thread = world.row(LUNCH)["thread_id"]
    assert app.post("/api/replylater", world.copy(LUNCH)).json["ok"]
    assert listed(app, "__replylater__") == [newest_of(thread)]
    assert app.post("/api/replylater", {"account": w.ADA, "thread_id": thread, "on": False}).json["ok"]
    assert listed(app, "__replylater__") == []


def test_a_nudge_outlasts_a_renumber_of_the_row_it_began_from(app, world):
    original = world.row(LUNCH)
    nudge_on(original["thread_id"])
    world.server.renumber(w.ADA, "INBOX")
    mailbox.ingest.sync_new(accounts.get(w.ADA))
    assert world.pk(LUNCH) != original["pk"]
    assert listed(app, "__waiting__") == [newest_of(original["thread_id"])]
