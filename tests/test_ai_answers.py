"""The brief, the delta and the review are kept in tables of their own (migration 37), and what they say
about a message still holds when its row doesn't: they name messages by key, and a read gives the pks they
have now."""
import json
import time
from datetime import datetime, timezone

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import attention

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word: the anchor of Ada's lunch thread
LUNCH_MID = "<lunch-3@lin.example.test>"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
QUILT = "ada-inbox-11-html-cid.eml"             # Lin's quilt photo: a thread of one
NARRATIVE = "Lin is waiting on lunch plans for Thursday, so reply today."
WEEK = "A steady week: mail came in, some went out, and one thread from Lin is still waiting on you."


def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def execute(sql, *args):
    import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def summarize_as(pk, importance=5, category="action-needed", action="Reply"):
    import store
    c = store.conn()
    try:
        store.save_summary(c, pk, "Stub triage.", importance, category, action, "claude-opus-5-5",
                           datetime.now(timezone.utc).isoformat())
        c.commit()
    finally:
        c.close()


def renumber_inbox(world, account=w.ADA):
    from mailmind.bootstrap import accounts
    import ingest
    world.server.renumber(account, "INBOX")
    ingest.sync_new(accounts.get(account))


def message_ids(pks):
    return [r["message_id"] for p in pks for r in q("SELECT message_id FROM messages WHERE pk=?", p)]


def brief_of(ai, app, pks):
    ai.queue(fa.answer({"narrative": NARRATIVE, "groups": [{"label": "Lunch", "count": len(pks), "pks": pks}]}))
    return app.post("/api/briefing", {"account": w.ADA}).json


def brief_now(app):
    return app.get(f"/api/briefing?account={w.ADA}").json


def review_of(ai, app):
    ai.queue(fa.answer({"narrative": WEEK, "next": ["Answer Lin"]}))
    return app.post("/api/review", {"account": w.ADA}).json


def review_now(app):
    return app.get(f"/api/review?account={w.ADA}").json


def waiting_on(pk, thread_id):
    import store
    c = store.conn()
    attention.book.followup_record_sent(c, w.ADA, thread_id, pk, int(time.time()), "2026-11-01T00:00:00+00:00")
    c.execute("UPDATE followups SET state='due' WHERE thread_id=?", (thread_id,))
    c.commit()
    c.close()


def new_mail(n):
    return w.variant(NEWSLETTER, Message_ID=f"<new-{n}@loom.example.test>", Subject=f"New {n}",
                     Date=f"Mon, 02 Nov 2026 11:{n:02d}:00 +0000")


# -- the brief -------------------------------------------------------------------------------------

def test_a_stored_brief_says_which_messages_it_holds_by_key(app, world, ai):
    anchor = world.row(LUNCH)
    summarize_as(anchor["pk"])
    brief_of(ai, app, [anchor["pk"]])
    [row] = q("SELECT * FROM briefs")
    assert (row["accounts"], row["narrative"]) == (w.ADA, NARRATIVE)
    assert json.loads(row["groups_json"]) == [{"label": "Lunch", "messages": [[anchor["message_key"], "INBOX"]]}]


def test_a_stored_brief_finds_a_message_with_no_message_id_after_a_renumber(app, world, ai):
    raw = w.raw(QUILT).replace(b"Message-ID:", b"X-Was-Message-ID:", 1)
    assert raw != w.raw(QUILT)
    world.deliver(w.ADA, "INBOX", raw)
    [old] = q("SELECT pk FROM messages WHERE account=? AND (message_id='' OR message_id IS NULL)", w.ADA)
    summarize_as(old["pk"])
    assert brief_of(ai, app, [old["pk"]])["groups"][0]["pks"] == [old["pk"]]
    renumber_inbox(world)
    [new] = q("SELECT pk FROM messages WHERE account=? AND (message_id='' OR message_id IS NULL)", w.ADA)
    assert new["pk"] != old["pk"]
    [group] = brief_now(app)["groups"]
    assert group["pks"] == [new["pk"]]


def test_a_stored_brief_counts_what_it_still_holds(app, world, ai):
    lunch, other = world.pk(LUNCH), world.pk(QUILT)
    summarize_as(lunch)
    summarize_as(other)
    assert brief_of(ai, app, [lunch, other])["groups"][0]["count"] == 2
    execute("DELETE FROM messages WHERE pk=?", other)
    [group] = brief_now(app)["groups"]
    assert (group["count"], group["pks"]) == (1, [lunch])


def test_a_stored_brief_leaves_out_a_message_on_its_way_out(app, world, ai):
    lunch, other = world.pk(LUNCH), world.pk(QUILT)
    summarize_as(lunch)
    summarize_as(other)
    brief_of(ai, app, [lunch, other])
    execute("UPDATE messages SET pending_removal=1 WHERE pk=?", other)
    assert brief_now(app)["groups"][0]["pks"] == [lunch]


def test_a_stored_brief_prefers_the_copy_in_the_folder_it_was_read_from(app, world, ai):
    anchor = world.row(LUNCH)
    summarize_as(anchor["pk"])
    brief_of(ai, app, [anchor["pk"]])
    world.deliver(w.ADA, "Archive", w.raw(LUNCH))
    assert len(q("SELECT pk FROM messages WHERE message_key=?", anchor["message_key"])) == 2
    assert brief_now(app)["groups"][0]["pks"] == [anchor["pk"]]


# -- the review ------------------------------------------------------------------------------------

def test_a_stored_review_opens_the_message_it_named_after_a_renumber(app, world, ai):
    pk = world.pk(LUNCH)
    summarize_as(pk)
    first = review_of(ai, app)
    assert pk in [n["pk"] for n in first["needs"]]
    renumber_inbox(world)
    again = review_now(app)
    assert again["cached"] is True and len(ai.requests) == 1
    assert LUNCH_MID in message_ids(n["pk"] for n in again["needs"])
    assert pk not in [n["pk"] for n in again["needs"]]


def test_a_stored_reviews_waiting_list_follows_a_renumber_too(app, world, ai):
    lunch = world.row(LUNCH)
    waiting_on(lunch["pk"], lunch["thread_id"])
    first = review_of(ai, app)
    old = [i["pk"] for i in first["waiting"] if i["thread_id"] == lunch["thread_id"]]
    renumber_inbox(world)
    [item] = [i for i in review_now(app)["waiting"] if i["thread_id"] == lunch["thread_id"]]
    assert item["pk"] not in old
    assert q("SELECT thread_id FROM messages WHERE pk=?", item["pk"])[0]["thread_id"] == lunch["thread_id"]


def test_a_stored_review_leaves_out_a_message_that_has_gone(app, world, ai):
    pk = world.pk(LUNCH)
    summarize_as(pk)
    assert pk in [n["pk"] for n in review_of(ai, app)["needs"]]
    execute("DELETE FROM messages WHERE pk=?", pk)
    again = review_now(app)
    assert pk not in [n["pk"] for n in again["needs"]]
    assert again["stale"] is True            # and it says the mail changed


def test_a_waiting_item_that_named_no_message_stays(app, world, ai):
    waiting_on(world.pk(LUNCH), "rf:no-such-thread")
    review_of(ai, app)
    [item] = [i for i in review_now(app)["waiting"] if i["thread_id"] == "rf:no-such-thread"]
    assert item["pk"] is None and item["key"] is None


def test_a_stored_review_keeps_what_it_said_when_the_mail_moves_on(app, world, ai):
    pk = world.pk(LUNCH)
    summarize_as(pk)
    first = review_of(ai, app)
    world.deliver(w.ADA, "INBOX", new_mail(1))
    again = review_now(app)
    assert (again["narrative"], again["next"], again["stats"]) == (WEEK, ["Answer Lin"], first["stats"])
    assert again["stale"] is True


# -- the delta -------------------------------------------------------------------------------------

def delta(app, ai, headline):
    ai.queue(fa.answer({"headline": headline, "items": [{"who": "Loom", "gist": "New issue", "importance": 1}]}))
    return app.post("/api/briefing/since", {"account": w.ADA, "since": 0}).json


def test_a_delta_read_again_costs_nothing(app, world, ai):
    assert delta(app, ai, "One new.")["cached"] is False
    calls = len(ai.requests)
    again = delta(app, ai, "Not asked for.")
    assert (again["cached"], again["headline"]) == (True, "One new.") and len(ai.requests) == calls
    assert app.get(f"/api/briefing/since?account={w.ADA}&since=0").json["headline"] == "One new."


def test_a_delta_is_written_again_when_a_message_in_its_window_is_swapped(app, world, ai):
    world.deliver(w.ADA, "INBOX", new_mail(1))
    assert delta(app, ai, "Before.")["headline"] == "Before."
    count = app.get(f"/api/briefing/since?account={w.ADA}&since=0").json["count"]
    execute("DELETE FROM messages WHERE message_id=?", "<new-1@loom.example.test>")
    world.deliver(w.ADA, "INBOX", new_mail(2))
    after = delta(app, ai, "After.")
    assert (after["headline"], after["cached"], after["count"]) == ("After.", False, count)


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
    app.post("/api/briefing/refresh", {"account": w.ADA})
    app.post("/api/review/refresh", {"account": w.ADA})
    world.deliver(w.ADA, "INBOX", new_mail(1))
    delta(app, ai, "Two new.")
    for table in ("briefs", "deltas", "reviews"):
        assert all(r["created_at"] != old for r in q(f"SELECT created_at FROM {table}")), table
        assert len(q(f"SELECT 1 FROM {table}")) == 1, table


def test_the_old_cache_is_gone_for_good(app, world):
    import store
    from mailmind.platform.db import migrate
    from mailmind.platform.settings import current
    migrate.init(current().db)                   # the next start's
    store.init()
    tables = {r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "briefing_cache" not in tables and {"briefs", "deltas", "reviews"} <= tables
