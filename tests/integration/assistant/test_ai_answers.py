"""The brief, the delta and the review are kept in tables of their own (migration 37), and what they say
about a message still holds when its row doesn't: they name messages by key, and a read names each copy by its
key and the folder it is in now. Here on the store: a renumber, a copy on its way out or in two folders, a window past its
cap and the tables themselves. What Briefs and Reviews keep of a message and what a stored one says once the mail has moved
on are tests/unit/assistant/test_ai_answers.py's."""
import time
from datetime import datetime, timezone

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import attention
from mailmind.bootstrap.assistant import answers

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word: the anchor of Ada's lunch thread
LUNCH_MID = "<lunch-3@lin.example.test>"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
QUILT = "ada-inbox-11-html-cid.eml"             # Lin's quilt photo: a thread of one
NARRATIVE = "Lin is waiting on lunch plans for Thursday, so reply today."
WEEK = "A steady week: mail came in, some went out, and one thread from Lin is still waiting on you."


def q(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def execute(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def summarize_as(pk, importance=5, category="action-needed", action="Reply"):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, pk, "Stub triage.", importance, category, action, "claude-opus-5-5",
                           datetime.now(timezone.utc).isoformat())
        c.commit()
    finally:
        c.close()


def renumber_inbox(world, account=w.ADA):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    world.server.renumber(account, "INBOX")
    ingest.sync_new(accounts.get(account))


def named(items):
    """The copies a list names, as the page names one: by key and folder."""
    return [{"key": i["key"], "folder": i["folder"], "uid": i["uid"]} for i in items]


def rows_named(item, *columns):
    """The rows the copy `item` names by its key and folder."""
    return q(f"SELECT {', '.join(columns)} FROM messages WHERE message_key=? AND folder=?", item["key"], item["folder"])


def brief_of(ai, app, pks):
    ai.queue(fa.answer({"narrative": NARRATIVE, "groups": [{"label": "Lunch", "count": len(pks), "pks": pks}]}))
    return app.post("/api/briefing", {}).json


def brief_now(app):
    return app.get("/api/briefing").json


def review_of(ai, app):
    ai.queue(fa.answer({"narrative": WEEK, "next": ["Answer Lin"]}))
    return app.post("/api/review", {}).json


def review_now(app):
    return app.get("/api/review").json


def waiting_on(thread_id):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    attention.book.followup_record_sent(c, w.ADA, thread_id, int(time.time()), "2026-11-01T00:00:00+00:00")
    c.execute("UPDATE followups SET state='due' WHERE thread_id=?", (thread_id,))
    c.commit()
    c.close()


def new_mail(n):
    return w.variant(NEWSLETTER, Message_ID=f"<new-{n}@loom.example.test>", Subject=f"New {n}",
                     Date=f"Mon, 02 Nov 2026 11:{n:02d}:00 +0000")


# -- the brief -------------------------------------------------------------------------------------


def test_a_stored_brief_finds_a_message_with_no_message_id_after_a_renumber(app, world, ai):
    raw = w.raw(QUILT).replace(b"Message-ID:", b"X-Was-Message-ID:", 1)
    assert raw != w.raw(QUILT)
    world.deliver(w.ADA, "INBOX", raw)
    [old] = q("SELECT pk FROM messages WHERE account=? AND (message_id='' OR message_id IS NULL)", w.ADA)
    summarize_as(old["pk"])
    assert brief_of(ai, app, [old["pk"]])["groups"][0]["messages"] == [w.copy_of(old["pk"])]
    renumber_inbox(world)
    [new] = q("SELECT pk FROM messages WHERE account=? AND (message_id='' OR message_id IS NULL)", w.ADA)
    assert new["pk"] != old["pk"]
    [group] = brief_now(app)["groups"]
    assert group["messages"] == [w.copy_of(new["pk"])]


def test_a_stored_brief_leaves_out_a_message_on_its_way_out(app, world, ai):
    lunch, other = world.pk(LUNCH), world.pk(QUILT)
    summarize_as(lunch)
    summarize_as(other)
    brief_of(ai, app, [lunch, other])
    execute("UPDATE messages SET pending_removal=1 WHERE pk=?", other)
    assert brief_now(app)["groups"][0]["messages"] == [w.copy_of(lunch)]


def test_a_stored_brief_prefers_the_copy_in_the_folder_it_was_read_from(app, world, ai):
    anchor = world.row(LUNCH)
    summarize_as(anchor["pk"])
    brief_of(ai, app, [anchor["pk"]])
    world.deliver(w.ADA, "Archive", w.raw(LUNCH))
    assert len(q("SELECT pk FROM messages WHERE message_key=?", anchor["message_key"])) == 2
    assert brief_now(app)["groups"][0]["messages"] == [{"key": anchor["message_key"], "folder": "INBOX", "uid": anchor["uid"]}]


# -- the review ------------------------------------------------------------------------------------

def test_a_stored_review_opens_the_message_it_named_after_a_renumber(app, world, ai):
    pk = world.pk(LUNCH)
    summarize_as(pk)
    first = review_of(ai, app)
    assert w.copy_of(pk) in named(first["needs"])
    renumber_inbox(world)
    again = review_now(app)
    assert again["cached"] is True and len(ai.requests) == 1
    [now] = q("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?", w.ADA, LUNCH_MID)
    assert now["pk"] != pk
    assert w.copy_of(now["pk"]) in named(again["needs"])
    assert LUNCH_MID in [r["message_id"] for n in again["needs"] for r in rows_named(n, "message_id")]


def test_a_stored_reviews_waiting_list_follows_a_renumber_too(app, world, ai):
    lunch = world.row(LUNCH)
    waiting_on(lunch["thread_id"])
    first = review_of(ai, app)
    old = [r["pk"] for i in first["waiting"] if i["thread_id"] == lunch["thread_id"] for r in rows_named(i, "pk")]
    assert old
    renumber_inbox(world)
    [item] = [i for i in review_now(app)["waiting"] if i["thread_id"] == lunch["thread_id"]]
    [now] = rows_named(item, "pk", "thread_id")
    assert now["pk"] not in old
    assert now["thread_id"] == lunch["thread_id"]


def test_a_waiting_item_that_named_no_message_stays(app, world, ai):
    waiting_on("rf:no-such-thread")
    review_of(ai, app)
    [item] = [i for i in review_now(app)["waiting"] if i["thread_id"] == "rf:no-such-thread"]
    assert "pk" not in item and item["key"] is None


# -- the delta -------------------------------------------------------------------------------------

def delta(app, ai, headline):
    ai.queue(fa.answer({"headline": headline, "items": [{"who": "Loom", "gist": "New issue", "importance": 1}]}))
    return app.post("/api/briefing/since", {"account": w.ADA, "since": 0}).json


def test_a_delta_past_its_cap_is_written_again_when_more_mail_comes(app, world, ai):
    for n in range(26):
        world.deliver(w.ADA, "INBOX", new_mail(n))
    full = delta(app, ai, "Plenty.")
    assert (full["headline"], full["count"]) == ("Plenty.", 25)
    world.deliver(w.ADA, "INBOX", new_mail(26))
    after = delta(app, ai, "One more.")
    assert (after["headline"], after["cached"], after["count"]) == ("One more.", False, 25)


# -- the tables ------------------------------------------------------------------------------------

def test_each_table_keeps_a_week(app, world, ai):
    pk = world.pk(LUNCH)
    summarize_as(pk)
    brief_of(ai, app, [pk])
    review_of(ai, app)
    delta(app, ai, "One new.")
    old = "2026-01-01T00:00:00+00:00"
    for table in ("briefs", "deltas", "reviews"):
        execute(f"UPDATE {table} SET created_at=?, {'day' if table != 'deltas' else 'since_ts'}=?", old, 1)
    app.post("/api/briefing/refresh", {})
    app.post("/api/review/refresh", {})
    world.deliver(w.ADA, "INBOX", new_mail(1))
    delta(app, ai, "Two new.")
    for table in ("briefs", "deltas", "reviews"):
        assert all(r["created_at"] != old for r in q(f"SELECT created_at FROM {table}")), table
        assert len(q(f"SELECT 1 FROM {table}")) == 1, table


def test_the_old_cache_is_gone_for_good(app, world):
    from mailmind.bootstrap.mailbox import store
    from mailmind.platform.db import migrate
    from mailmind.platform.settings import current
    migrate.init(current().db)                   # the next start's
    store.init()
    tables = {r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "briefing_cache" not in tables and {"briefs", "deltas", "reviews"} <= tables
