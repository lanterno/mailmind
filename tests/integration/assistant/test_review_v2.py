"""The week's review says what a person filed away, by the command log and true after an Undo, and what the invites in the mail
say of their week. Here through the routes, the command log and the calendar; what Reviews makes of the week it is handed is
tests/unit/assistant/test_review_v2.py's."""
from datetime import datetime, timedelta


from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap.mailbox import store

LUNCH, NEWS, CLIENT, GMAIL, INVITE = ("ada-inbox-01-lunch.eml", "ada-inbox-03-newsletter.eml", "bea-inbox-01-client.eml",
                                      "cy-inbox-03-unrelated.eml", "ada-inbox-06-outlook-invite.eml")
ANSWER = {"narrative": "A steady week: you kept up and little is left waiting on you.", "next": []}


def stats(app, ai):
    ai.queue(fa.answer(ANSWER))
    return app.post("/api/review/refresh", {}).json["stats"]


def archive(app, world, name):
    assert app.post("/api/archive", world.copy(name)).status == 200
    app.settle()


# ── you handled N this week ───────────────────────────────────────────────────────────────────────

def test_what_went_to_archive_this_week_is_counted(app, world, ai):
    archive(app, world, LUNCH)
    archive(app, world, NEWS)
    assert stats(app, ai)["handled"] == 2


def test_only_archiving_counts(app, world, ai):
    assert app.post("/api/trash", world.copy(LUNCH)).status == 200
    app.settle()
    assert stats(app, ai)["handled"] == 0


def test_an_undo_takes_it_back_out(app, world, ai):
    archive(app, world, LUNCH)
    assert app.post("/api/undo", {}).status == 200
    assert stats(app, ai)["handled"] == 0


def test_filing_it_again_after_an_undo_counts_once(app, world, ai):
    archive(app, world, LUNCH)
    app.post("/api/undo", {})
    assert app.post("/api/archive", {"key": world.key(LUNCH), "folder": "INBOX"}).status == 200      # a new uid: the only copy there
    app.settle()
    assert stats(app, ai)["handled"] == 1


def test_an_undone_gmail_archive_is_taken_back_out_too(app, world, ai):
    archive(app, world, GMAIL)
    assert stats(app, ai)["handled"] == 1
    assert app.post("/api/undo", {}).status == 200
    assert stats(app, ai)["handled"] == 0


def test_a_failure_marking_it_doesnt_put_the_undo_back(app, world, ai, monkeypatch):
    archive(app, world, LUNCH)
    monkeypatch.setattr(store, "command_mark_reversed", lambda c, mid: 1 / 0)
    assert app.post("/api/undo", {}).status == 200
    assert app.post("/api/undo", {}).status == 404         # nothing left to undo: the move was reversed


def test_a_move_not_yet_made_doesnt_count(app, world, ai):
    archive(app, world, LUNCH)
    c = store.conn()
    c.execute("UPDATE mail_commands SET applied=0")
    c.commit()
    c.close()
    assert stats(app, ai)["handled"] == 0


def test_what_is_over_a_week_old_doesnt_count(app, world, ai):
    archive(app, world, LUNCH)
    app.clock.shift(timedelta(days=8))
    assert stats(app, ai)["handled"] == 0


def test_another_accounts_mail_isnt_counted_for_this_one(app, world):
    archive(app, world, CLIENT)
    c = store.conn()
    since = (datetime.now().astimezone() - timedelta(days=1)).isoformat()
    assert (store.handled_since(c, {w.ADA}, since), store.handled_since(c, {w.BEA}, since), store.handled_since(c, set(), since)) == (0, 1, 1)
    c.close()


def test_an_invite_in_the_mail_reaches_the_review_and_the_model(app, world, ai):
    ai.queue(fa.answer(ANSWER))
    r = app.post("/api/review", {}).json
    meeting = r["meetings"][0]
    assert (meeting["summary"], meeting["past"], meeting["key"]) == ("Design review", False, world.key(INVITE))
    assert "Design review" in ai.requests[-1].prompt


def test_once_it_has_happened_it_says_so(app, world, ai):
    app.clock.shift(timedelta(days=2))
    ai.queue(fa.answer(ANSWER))
    assert app.post("/api/review", {}).json["meetings"][0]["past"] is True


def test_a_review_read_later_has_them_and_loses_one_whose_mail_has_gone(app, world, ai):
    ai.queue(fa.answer(ANSWER))
    app.post("/api/review", {})
    assert [m["summary"] for m in app.get("/api/review").json["meetings"]] == ["Design review"]
    app.post("/api/trash", world.copy(INVITE))
    app.settle()
    world.deliver(w.ADA, "INBOX", w.variant(NEWS, Message_ID="<later@example.test>"), ())
    assert app.get("/api/review").json["meetings"] == []
