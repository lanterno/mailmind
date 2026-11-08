"""The week's review says what a person filed away, by the command log and true after an Undo, and what the invites in the mail
say of their week."""
from datetime import datetime, timedelta

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.domain import review
from mailmind.bootstrap import assistant
from mailmind.bootstrap.assistant import answers
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


def test_the_model_is_told_how_many(app, world, ai):
    archive(app, world, LUNCH)
    stats(app, ai)
    assert "Filed away: 1" in ai.requests[-1].prompt


@pytest.mark.parametrize("handled, says", [(3, True), (0, False)])
def test_with_no_answer_the_week_says_so_itself(handled, says):
    week = {"sent": 1, "received": 2, "by_bucket": {}, "needs_you": 0, "waiting_on": 0, "reply_later": 0, "cleaned": 0, "handled": handled}
    assert ("You filed away" in review.fallback(week, [], [])["narrative"]) is says


# ── meetings and deadlines ────────────────────────────────────────────────────────────────────────

NOON = datetime(2026, 11, 2, 12, 0)
SINCE, UNTIL = NOON - timedelta(days=7), NOON + timedelta(days=7)


def event(summary, when, all_day=False):
    return {"key": 1, "folder": "INBOX", "uid": 1, "account": w.ADA, "thread_id": "t", "summary": summary, "start": when.isoformat(timespec="minutes"),
            "all_day": all_day}


def of(gone=(), coming=()):
    return review.meetings_of(gone, coming, SINCE, NOON, UNTIL)


def test_an_event_past_and_one_to_come_are_told_apart():
    a, b = event("Before", NOON - timedelta(days=1)), event("After", NOON + timedelta(days=1))
    assert [(m["summary"], m["past"]) for m in of([a], [b])] == [("Before", True), ("After", False)]


def test_one_in_both_answers_is_listed_once():
    a = event("Standup", NOON - timedelta(days=1))
    assert len(of([a], [a])) == 1


@pytest.mark.parametrize("when, kept", [(SINCE, True), (SINCE - timedelta(minutes=1), False), (UNTIL - timedelta(minutes=1), True), (UNTIL, False)])
def test_the_week_is_its_bounds(when, kept):
    assert bool(of([event("Edge", when)])) is kept


def test_it_keeps_the_latest_gone_and_the_soonest_to_come():
    gone = [event(f"g{i}", NOON - timedelta(hours=i + 1)) for i in range(9)]
    coming = [event(f"c{i}", NOON + timedelta(hours=i + 1)) for i in range(9)]
    kept = of(gone, coming)
    assert [m["summary"] for m in kept] == [f"g{i}" for i in range(review.EVENTS_EACH_WAY - 1, -1, -1)] + [f"c{i}" for i in range(review.EVENTS_EACH_WAY)]


def test_a_long_title_is_held_to_its_cap():
    assert len(of([event("t" * 900, NOON - timedelta(hours=1))])[0]["summary"]) == review.EVENT_SUMMARY_MAX


def test_the_model_is_told_which_are_gone_and_which_are_deadlines():
    said = review.meeting_lines(of([event("Offsite", NOON - timedelta(days=1))], [event("Tax return", NOON + timedelta(days=1), all_day=True)]))
    assert said == "- 2026-11-01 12:00 (already happened): Offsite\n- 2026-11-03 (all day): Tax return"


def test_with_no_events_the_model_is_told_none():
    assert review.meeting_lines([]) == "(none)"


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


def test_a_calendar_that_cant_be_read_costs_only_its_mention(app, world, ai, monkeypatch):
    monkeypatch.setattr(assistant.reviews._mail, "events_from", lambda when, limit: 1 / 0)
    ai.queue(fa.answer(ANSWER))
    r = app.post("/api/review", {})
    assert (r.status, r.json["meetings"]) == (200, [])
    assert "(none)" in ai.requests[-1].prompt


def test_a_review_stored_before_this_still_reads(app, world):
    c = store.conn()
    day = datetime.now().astimezone().date().isoformat()
    said = '{"sent": 1, "received": 2, "by_bucket": {}, "needs_you": 0, "waiting_on": 0, "reply_later": 0, "cleaned": 0}'
    answers.save_review(c, ",".join(sorted(a["email"] for a in _enabled())), day, "fp", "Written earlier today.", "[]", said, "[]", "[]", "m", datetime.now().astimezone().isoformat())
    c.commit()
    c.close()
    r = app.get("/api/review").json
    assert (r["narrative"], r["meetings"], r["stats"].get("handled")) == ("Written earlier today.", [], None)


def _enabled():
    from mailmind.bootstrap import accounts
    return accounts.config.accounts()
