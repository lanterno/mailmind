"""The Assistant's routes name a message by key, and its answers name each message they speak of by key and the folder of
the copy it has now: a summary, a drafted reply and a reply drafted ahead are asked for by key, and the brief's groups, the
review's lists and Ask's sources say key and folder, which a renumber leaves as they were."""
from datetime import datetime, timezone

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import mailbox

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word: the anchor of Ada's lunch thread
DRAFT = "See you at 12:30."


def renumber_inbox(world):
    world.server.renumber(w.ADA, "INBOX")
    mailbox.ingest.sync_new(accounts.get(w.ADA))


def summarize_as(world, name):
    c = store.conn()
    try:
        answers.save_summary(c, world.pk(name), "Stub triage.", 5, "action-needed", "Reply", "claude-opus-5-5",
                             datetime.now(timezone.utc).isoformat())
        c.commit()
    finally:
        c.close()


def test_a_thread_is_summarized_by_the_key_of_a_message_in_it(app, world, ai):
    thread = world.row(LUNCH)["thread_id"]
    renumber_inbox(world)
    r = app.post("/api/ai/summarize", {"key": world.key(LUNCH), "folder": "INBOX"})
    assert r.status == 200 and r.json["thread_id"] == thread


def test_a_reply_is_drafted_by_key_and_served_ahead_by_key(app, world, ai):
    ai.queue(fa.answer({"body": DRAFT}))
    assert app.post("/api/ai/draft-reply", {"key": world.key(LUNCH)}).json["body"] == DRAFT
    renumber_inbox(world)
    peek = app.get(f"/api/ai/draft-ahead?key={world.key(LUNCH)}&folder=INBOX&peek=1").json
    assert peek["status"] == "ready"


def test_a_key_with_no_copy_left_is_not_found_and_has_nothing_drafted(app, world, ai):
    for path in ("/api/ai/summarize", "/api/ai/draft-reply"):
        r = app.post(path, {"key": 987654})
        assert (r.status, r.json["detail"]) == (404, "not found"), path
    assert app.get("/api/ai/draft-ahead?key=987654&peek=1").json == {"ok": True, "status": "none"}
    assert ai.requests == []


def test_the_brief_names_each_message_by_key_and_the_folder_it_is_in(app, world, ai):
    summarize_as(world, LUNCH)
    ai.queue(fa.answer({"narrative": "Lin is waiting on lunch.", "groups": [{"label": "Lunch", "count": 1, "pks": [world.pk(LUNCH)]}]}))
    [made] = app.post("/api/briefing", {}).json["groups"]
    assert made["messages"] == [world.copy(LUNCH)]
    renumber_inbox(world)
    [group] = app.get("/api/briefing").json["groups"]
    assert group["messages"] == [world.copy(LUNCH)]


def test_the_review_names_what_needs_you_by_key_and_folder(app, world, ai):
    summarize_as(world, LUNCH)
    ai.queue(fa.answer({"narrative": "A steady week.", "next": ["Answer Lin"]}))
    needs = app.post("/api/review", {}).json["needs"]
    assert world.copy(LUNCH) in [{"key": n["key"], "folder": n["folder"], "uid": n["uid"]} for n in needs]


def test_asks_sources_name_their_messages_by_key_and_folder(app, world, ai):
    ai.responder = lambda request: fa.answer({"answer": "Thursday at 12:30.", "cited_pks": [world.pk(LUNCH)]})
    r = app.post("/api/ai/ask", {"question": "When is lunch with Lin?", "account": w.ADA}).json
    assert [{"key": s["key"], "folder": s["folder"], "uid": s["uid"]} for s in r["sources"]] == [world.copy(LUNCH)]
