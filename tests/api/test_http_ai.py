"""The AI routes with a key, against the fake Claude API: what each route answers, and what it sent
— the system prompt, the prompt, the model, the effort and the output schema. A changed prompt therefore shows up as
a golden diff to review, like any other behaviour change. The golden files are in
tests/golden/http_ai/.

Unless a test queues replies, the fake answers each call from the call's own schema: "stub <field>"
for strings, 1 for numbers and the first value of each enum.
"""
import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w


def triage(req):
    """Triage replies a little like a model's: the mail that asks something of you is urgent,
    the rest is information. Other calls get the fake's default."""
    if not req.prompt.startswith("You are triaging ONE email"):
        return None
    urgent = any(s in req.prompt for s in ("Subject: Contract draft v2", "Subject: Design review",
                                           "Subject: Re: Guild meetup venue"))
    return fa.answer({"summary": "Wants an answer." if urgent else "For your information.",
                      "importance": 5 if urgent else 2, "category": "action-needed" if urgent else "info",
                      "action": "Reply today" if urgent else ""})


def triaged(app, ai):
    """The server's triage pass over every message, answered by `triage`; how many it summarized, as the database
    keeps them (the world starts with none)."""
    from mailmind.bootstrap.mailbox import store
    ai.responder = triage
    app.work("assess")
    ai.requests.clear()
    c = store.conn()
    try:
        return c.execute("SELECT count(*) FROM assessments").fetchone()[0]
    finally:
        c.close()


def sent(golden, ai, label):
    """Note every request the fake received since the last call, then forget them."""
    golden.note(label, [{"model": r.body.get("model"), "effort": (r.body.get("output_config") or {}).get("effort"),
                         "schema": r.schema, "system": r.system, "prompt": r.prompt} for r in ai.requests])
    ai.requests.clear()


def test_summarize_a_thread_and_a_message(app, golden, world, ai):
    lunch = world.copy("ada-inbox-02-lunch-confirm.eml")
    ai.queue(fa.answer({"tldr": "Lunch with Lin is set for Thursday at 12:30.",
                        "points": ["Lin asked about Thursday", "Ada said yes", "Lin confirmed 12:30"],
                        "action": "", "participants": ["Lin Mori", "Ada Park"]}))
    golden.record("summarize the lunch thread", app.post("/api/ai/summarize", lunch))
    sent(golden, ai, "sent for the thread")
    golden.record("again, from the cache", app.post("/api/ai/summarize", lunch))
    golden.record("again, forced", app.post("/api/ai/summarize", {**lunch, "force": True}))
    golden.note("calls for the repeat and the forced run", len(ai.requests))
    ai.requests.clear()
    ai.queue(fa.answer(fa.STUB_TEXT))                  # a reply that isn't JSON: a fallback is cached
    golden.record("by account and thread", app.post("/api/ai/summarize",
                                                    {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]}))
    sent(golden, ai, "sent for the Gmail thread")
    golden.record("a missing message", app.post("/api/ai/summarize", {"key": 999999}))
    golden.record("an unknown account", app.post("/api/ai/summarize", {"account": "zed@example.test", "thread_id": "t"}))
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    golden.record("when the API fails", app.post("/api/ai/summarize", world.copy("bea-inbox-01-client.eml")))


def test_a_message_read_for_a_summary_from_a_folder_another_client_deleted_forgets_the_folder(app, world, ai):
    # The assistant reads a body that isn't stored as opening the message would, and finds its folder gone as that does:
    # the folder is forgotten, so its mail leaves the lists and the rail.
    archived = "ada-archive-01-old-thread.eml"
    assert not world.row(archived)["body_fetched"]
    copy = world.copy(archived)
    with world.server.lock:
        world.server.accounts[w.ADA].mailboxes.pop("Archive")
    app.post("/api/ai/summarize", copy)
    assert world.where(archived)["db"] == []
    assert "Archive" not in [f["name"] for f in app.get(f"/api/folders?account={w.ADA}").json["folders"]]


def test_draft_a_reply_and_rewrite(app, golden, world, ai):
    client = world.copy("bea-inbox-01-client.eml")
    golden.record("draft a reply", app.post("/api/ai/draft-reply", client))
    golden.record("draft a short, warm reply", app.post("/api/ai/draft-reply",
                                                        {**client, "tone": "warm", "instruction": "Keep it short"}))
    sent(golden, ai, "sent for the drafts")
    golden.record("draft a reply to nothing", app.post("/api/ai/draft-reply", {}))
    for op in ("shorten", "elaborate", "formal", "friendly", "fix"):
        golden.record(f"rewrite: {op}", app.post("/api/ai/rewrite", {"op": op, "text": "thanks, see you thursday"}))
    golden.record("rewrite: translate", app.post("/api/ai/rewrite", {"op": "translate", "lang": "French",
                                                                       "text": "thanks, see you thursday"}))
    sent(golden, ai, "sent for the rewrites")
    golden.record("rewrite: an unknown op", app.post("/api/ai/rewrite", {"op": "pirate", "text": "ahoy"}))
    golden.record("rewrite: no text", app.post("/api/ai/rewrite", {"op": "shorten"}))
    golden.note("calls for the refusals", len(ai.requests))


def test_ask_across_the_mail(app, golden, world, ai):
    ai.queue(fa.answer({"answer": "Lunch is on Thursday at noon.",
                        "cited_pks": [world.pk("ada-inbox-02-lunch-confirm.eml"), 999999]}))
    golden.record("ask when lunch is", app.post("/api/ai/ask", {"question": "When is lunch with Lin?"}))
    golden.record("ask within Bea's account", app.post("/api/ai/ask", {"account": w.BEA,
                                                                        "question": "What did Ivo send?"}))
    sent(golden, ai, "sent for the questions")
    golden.record("ask nothing", app.post("/api/ai/ask", {"question": ""}))


PLANS = (
    ("archive what Lin sent", {"intent": "act", "filter": "from:lin", "verb": "archive"}),
    ("trash the newsletters", {"intent": "act", "filter": "is:newsletter", "verb": "trash"}),
    ("snooze the receipts to the weekend", {"intent": "act", "filter": "is:receipt", "verb": "snooze",
                                            "snooze_preset": "weekend"}),
    ("find mail about lunch", {"intent": "search", "filter": "lunch"}),
    ("delete Lin's mail for good", {"intent": "act", "filter": "from:lin", "verb": "delete"}),
    ("archive everything", {"intent": "act", "filter": "", "verb": "archive"}),
    ("archive the inbox", {"intent": "act", "filter": "in:INBOX", "verb": "archive"}),
    ("archive all but Lin", {"intent": "act", "filter": "-from:lin", "verb": "archive"}),
    ("archive mail from nobody", {"intent": "act", "filter": "from:nobody@example.test", "verb": "archive"}),
    ("mark all of Ada's mail unread", {"intent": "act", "filter": "account:ada@example.test", "verb": "unread"}),
    ("archive with OR, which is a word", {"intent": "act", "filter": "lunch OR quilt", "verb": "archive"}),
    ("archive by a broken filter", {"intent": "act", "filter": "\"unbalanced", "verb": "archive"}),
)


def test_do_plans_a_preview_and_degrades_to_search(app, golden, world, ai):
    since = world.mark()
    for cmd, plan in PLANS:
        ai.queue(fa.answer(plan))
        answer = golden.record(f"do: {cmd}", app.post("/api/ai/do-plan", {"cmd": cmd})).json
        if answer.get("plan"):
            golden.alias(answer["plan"], "<plan>")
    sent(golden, ai, "sent for the plans")              # one prompt, only the command differs
    golden.note("server writes while planning", world.writes(since))
    golden.record("do: nothing", app.post("/api/ai/do-plan", {"cmd": "  "}))
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    golden.record("do: when the API fails", app.post("/api/ai/do-plan", {"cmd": "archive Lin"}))


def test_why_a_cleanup_and_what_the_screener_would_do(app, golden, world, ai):
    ai.queue(fa.answer({"rationales": [{"id": 1, "text": "You open few of these."}, {"id": 9, "text": "made up"}]}))
    golden.record("why clean up Lin, Noor and nobody", app.post("/api/cleanup/rationale", {"senders": [
        " Lin@Example.test", "noor@studio.example.test", "nobody@example.test"]}))
    sent(golden, ai, "sent for the rationale")
    golden.record("why clean up no one", app.post("/api/cleanup/rationale", {"senders": []}))
    golden.record("why clean up a number", app.post("/api/cleanup/rationale", {"senders": [5]}))
    ai.queue(fa.answer({"verdicts": [{"id": 1, "verdict": "keep", "confidence": 0.95, "why": "Writes to you often"},
                                     {"id": 2, "verdict": "block", "confidence": 0.5, "why": "Unsure"},
                                     {"id": 4, "verdict": "block", "confidence": 0.875, "why": "A mailer"},
                                     {"id": 9, "verdict": "block", "confidence": 1, "why": "Not shown"}]}))
    golden.record("what the Screener would do for Ada", app.post("/api/screener/suggest", {"account": w.ADA}))
    sent(golden, ai, "sent for the Screener")
    golden.record("for no account", app.post("/api/screener/suggest", {}))
    golden.note("calls for those with nothing to ask", len(ai.requests))
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    golden.record("when the API fails", app.post("/api/screener/suggest", {"account": w.ADA}))


def test_what_a_sender_has_been_writing_about(app, golden, world, ai):
    ai.queue(fa.answer({"summary": " Lin plans lunch and sends quilts. ", "topics": [" lunch ", 5, "quilts"],
                        "open": "Whether Thursday suits"}))
    golden.record("Lin", app.post("/api/contact/summary", {"addr": " Lin@Example.test"}))
    sent(golden, ai, "sent for Lin")
    ai.queue(fa.answer({"summary": "", "topics": [], "open": ""}))
    golden.record("Noor, of whom the model says nothing", app.post("/api/contact/summary",
                                                                    {"addr": "noor@studio.example.test"}))
    ai.requests.clear()
    golden.record("no address", app.post("/api/contact/summary", {"addr": "nobody"}))
    golden.record("no mail from them", app.post("/api/contact/summary", {"addr": "nobody@example.test"}))
    golden.note("calls for those", len(ai.requests))


@pytest.mark.parametrize("route", ["/api/briefing", "/api/review"])
def test_briefing_and_review_are_written_once_and_a_get_only_reads(app, golden, world, ai, route):
    golden.note("messages triaged", triaged(app, ai))
    pk = world.pk("bea-inbox-01-client.eml")
    ai.queue(fa.answer({"narrative": "A quiet week with two threads that want you: Ivo's contract "
                                     "and Noor's design review on Tuesday.",
                        "next": ["Answer Ivo about the contract"],
                        "groups": [{"label": "Contracts", "count": 1, "pks": [pk, 999999]}]}))
    golden.record(f"GET {route} before one is written", app.get(route))
    golden.note("calls for the read", len(ai.requests))
    golden.record(f"POST {route}", app.post(route, {}))
    sent(golden, ai, "sent")
    golden.record(f"GET {route} now", app.get(route))
    golden.record(f"POST {route} again", app.post(route, {}))
    golden.note("calls for the repeats", len(ai.requests))
    golden.record(f"POST {route}/refresh", app.post(f"{route}/refresh", {}))
    golden.note("calls for the refresh", len(ai.requests))


def test_a_brief_read_later_leaves_out_a_message_filed_since(app, world, ai):
    """The person archived a topic's message after the brief was written: it has gone from the topic, though its row
    follows the copy to the Archive, where search finds it."""
    triaged(app, ai)
    name = "bea-inbox-01-client.eml"
    ai.queue(fa.answer({"narrative": "Ivo's contract wants you.", "next": ["Answer Ivo"],
                        "groups": [{"label": "Contracts", "count": 1, "pks": [world.pk(name)]}]}))
    assert app.post("/api/briefing", {}).status == 200
    [group] = app.get("/api/briefing").json["groups"]
    assert (group["count"], [m["folder"] for m in group["messages"]]) == (1, ["INBOX"])
    assert app.post("/api/archive", world.copy(name)).status == 200
    app.work("commands")
    [group] = app.get("/api/briefing").json["groups"]
    assert (group["count"], group["messages"]) == (0, [])
    assert [m["folder"] for m in app.get("/api/search?scope=all&q=contract+draft+v2").json["items"]
            if m["key"] == world.copy(name)["key"]] == ["Archive"]


def test_the_weeks_numbers_count_all_that_needs_you_and_all_you_wait_on_not_only_what_it_lists(app, world, ai):
    """Ten more parts of Ivo's contract in Bea's Inbox, beside the three the world's mail asks, and eleven threads Ada waits
    on an answer in: more of each than the review lists. Its numbers say how many there are in all, as the brief says the
    same mail needs you, each alone in its conversation and none answered; its lists hold the first eight of each."""
    from mailmind.bootstrap.mailbox import store
    for n in range(10):
        world.deliver(w.BEA, "INBOX", w.variant("bea-inbox-01-client.eml", Message_ID=f"<contract-v2-{n}@harbor.example.test>",
                                                Subject=f"Contract draft v2, part {n}",
                                                Date=f"Thu, 29 Oct 2026 14:0{n}:00 +0000"))
    triaged(app, ai)
    with app.stopped():
        c = store.conn()
        try:
            c.executemany("INSERT INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts, due_ts, created_at) "
                          "VALUES(?,?,'sent','due',?,?,1793600000,'2026-11-01T00:00:00+00:00')",
                          [(w.ADA, f"waiting-{n}", 1793500000 + n, 1793500000 + n) for n in range(11)])
            c.commit()
        finally:
            c.close()
    needing = {it["key"] for it in app.get("/api/digest").json["items"] if it["needs_you"]}
    ai.queue(fa.answer({"narrative": "A full week: much asks for you, and you wait on many.", "next": []}))
    review = app.post("/api/review", {}).json
    assert (review["stats"]["needs_you"], review["stats"]["waiting_on"]) == (len(needing), 11) == (13, 11)
    assert (len(review["needs"]), len(review["waiting"])) == (8, 8)
    assert {it["key"] for it in review["needs"]} < needing
    assert [it["thread_id"] for it in review["waiting"]] == [f"waiting-{n}" for n in range(8)]
    assert app.get("/api/review").json["stats"] == review["stats"]          # as it was kept


def test_since_you_last_looked(app, golden, world, ai):
    triaged(app, ai)
    import time
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<lunch-4@example.test>",
                                            Subject="Re: Lunch, running late", Date="Mon, 02 Nov 2026 11:50:00 +0000"))
    since = int(time.time()) - 3600                   # the clock is frozen at NOW
    golden.record("read since an hour ago, before one is written", app.get(f"/api/briefing/since?since={since}"))
    golden.note("calls for the read", len(ai.requests))
    golden.record("since an hour ago", app.post("/api/briefing/since", {"since": since}))
    sent(golden, ai, "sent")
    golden.record("read since an hour ago", app.get(f"/api/briefing/since?since={since}"))
    golden.record("since the epoch (held to a day)", app.post("/api/briefing/since", {"since": 0}))
    golden.record("since a time that isn't one", app.post("/api/briefing/since", {"since": "soon"}))
    sent(golden, ai, "sent for those")


def test_draft_ahead_generates_once_then_serves(app, golden, world, ai):
    triaged(app, ai)
    assert app.post("/api/preferences", {"draft_ahead": True}).status == 200
    def ahead(name, **more):
        return "/api/ai/draft-ahead?" + urlencode({**world.copy(name), **more})

    end = "cy-inbox-02-thread-end.eml"                  # Mae's last word in the venue thread
    golden.record("peek", app.get(ahead(end, peek=1)))
    golden.record("ask without the CSRF token", app.get(ahead(end), headers={"X-CSRF-Token": None}))
    golden.note("calls so far", len(ai.requests))
    golden.record("ask", app.get(ahead(end)))
    app.settle()
    sent(golden, ai, "sent")
    golden.record("ask again", app.get(ahead(end)))
    golden.record("a message that isn't the thread's newest", app.get(ahead("cy-inbox-01-thread-start.eml")))
    golden.record("a message AI didn't call reply-worthy", app.get(ahead("ada-inbox-09-rfc2047-folded.eml")))
    golden.record("a key that isn't a number", app.get("/api/ai/draft-ahead?key=x"))
    app.settle()
    golden.note("calls for those", len(ai.requests))


def test_drafting_ahead_starts_off_and_a_view_drafts_nothing_until_a_person_turns_it_on(app, world, ai):
    triaged(app, ai)
    ahead = "/api/ai/draft-ahead?" + urlencode(world.copy("cy-inbox-02-thread-end.eml"))
    assert app.get("/api/preferences").json["preferences"]["draft_ahead"] is False
    assert app.get(ahead).json == {"ok": True, "status": "none"}
    app.settle()
    assert ai.requests == []
    assert app.post("/api/preferences", {"draft_ahead": True}).status == 200
    assert app.get(ahead).json["status"] == "generating"
    app.settle()
    assert len(ai.requests) == 1
    assert app.post("/api/preferences", {"draft_ahead": False}).status == 200
    assert app.get(ahead).json["status"] == "ready"          # a draft kept from before is still served
    app.settle()
    assert len(ai.requests) == 1


def test_the_background_drafts_ahead_only_once_a_person_turns_it_on(app, world, ai):
    world.deliver(w.CY, "INBOX", w.variant("cy-inbox-02-thread-end.eml", Message_ID="<venue-again@example.test>",
                                           Date="Mon, 02 Nov 2026 11:00:00 +0000"))     # in the days it drafts ahead for
    triaged(app, ai)
    app.work("draft-ahead")
    assert ai.requests == []
    assert app.post("/api/preferences", {"draft_ahead": True}).status == 200
    app.work("draft-ahead")
    assert len(ai.requests) == 1


def test_at_the_limit_what_came_in_since_you_looked_waits_and_asks_nothing(app, world, ai):
    """The page asks for it on its own, so it is background work: held to the background's share of the day's limit,
    it asks nobody to go over and says to ask again later."""
    triaged(app, ai)
    with app.stopped():
        with open(world.dir / "mailmind.config.json") as f:
            config = json.load(f)
        config["ai_daily_limit_usd"] = 0.000001
        with open(world.dir / "mailmind.config.json", "w") as f:
            json.dump(config, f)
    allow = {"X-MailMind-AI-Over-Limit": "allow"}
    status, costly = fa.answer({"text": "the"})
    ai.queue((status, {**costly, "usage": {"input_tokens": 1000, "output_tokens": 100}}))
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}, headers=allow).status == 200
    app.clock.shift(timedelta(seconds=46))             # past the yield to it: only the limit holds it back
    ai.requests.clear()
    for headers in ({}, allow):
        r = app.post("/api/briefing/since", {"since": 0}, headers=headers)
        assert (r.status, r.json["type"], r.json["detail"], r.json["retry"]) == (
            502, "/problems/unreachable", "delta unavailable", True)
    assert ai.requests == []


def test_the_status_counts_what_waits_for_a_summary_from_the_last_30_days(app, world, ai):
    def waiting():
        return app.get("/api/digest").json["ai"].get("summaries_waiting")
    before = waiting()
    assert before > 0
    for subject, date in (("Lunch, last summer", "Tue, 01 Sep 2026 12:00:00 +0000"),
                          ("Lunch, this week", "Mon, 02 Nov 2026 11:50:00 +0000")):
        world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Subject=subject, Date=date,
                                                Message_ID=f"<{len(subject)}-{date[5:7]}@example.test>"))
    assert waiting() == before + 1
    ai.responder = triage
    app.work("assess")
    asked = [r.prompt for r in ai.requests if r.prompt.startswith("You are triaging ONE email")]
    assert len(asked) == before + 1 and not [p for p in asked if "Lunch, last summer" in p]
    assert waiting() == 0
    assert app.post("/api/preferences", {"ai_enabled": False}).status == 200
    assert waiting() is None


def test_background_summaries_read_the_inbox_alone(app, ai):
    """What a person sent, drafted, filed or deleted goes to no provider unasked, and isn't counted as waiting: the world
    holds mail in Sent, Drafts, Archive, Trash and a folder of Bea's, all of it from the last 30 days."""
    waiting = app.get("/api/digest").json["ai"]["summaries_waiting"]
    ai.responder = triage
    app.work("assess")
    asked = [r.prompt for r in ai.requests if r.prompt.startswith("You are triaging ONE email")]
    assert len(asked) == waiting > 0
    for subject in ("Notes for the review", "Quilt pattern ideas", "20% off all yarn this weekend", "Kickoff notes",
                    "Re: Contract draft v2"):
        assert not [p for p in asked if f"Subject: {subject}" in p], subject
    assert app.get("/api/digest").json["ai"]["summaries_waiting"] == 0


def test_since_you_last_looked_and_the_brief_read_the_inbox_alone(app, world, ai):
    """Both are written with no one asking, as the new-mail pill shows and as the Inbox opens: what a person sent,
    drafted, filed or deleted since goes to no provider, and nor does what triage summarized before it was filed."""
    triaged(app, ai)
    assert app.post("/api/archive", world.copy("bea-inbox-01-client.eml")).status == 200        # summarized, then filed
    app.work("commands")
    for folder, name, subject in (("Sent", "ada-sent-01-lunch-reply.eml", "Sent just now"),
                                  ("Trash", "ada-trash-01-promo.eml", "Trashed just now")):
        world.deliver(w.ADA, folder, w.variant(name, Message_ID=f"<{folder}-now@example.test>", Subject=subject,
                                               Date="Mon, 02 Nov 2026 11:40:00 +0000"))
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<lunch-5@example.test>",
                                            Subject="Re: Lunch, running late", Date="Mon, 02 Nov 2026 11:50:00 +0000"))
    ai.requests.clear()
    assert app.post("/api/briefing/since", {"since": 0}).json["count"] > 0
    assert app.post("/api/briefing", {}).status == 200
    asked = [r.prompt for r in ai.requests]
    assert len(asked) == 2 and "Re: Lunch, running late" in asked[0]
    for subject in ("Notes for the review", "Re: Contract draft v2", "Sent just now", "Trashed just now",
                    "— Contract draft v2"):
        assert not [p for p in asked if subject in p], subject


NO_CREDIT = fa.error(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API. "
                                                 "Please go to Plans & Billing to upgrade or purchase credits.")


def test_a_call_refused_for_want_of_credit_names_the_account_to_top_up(app, world, ai):
    ai.queue(NO_CREDIT)
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 502
    assert app.get("/api/digest").json["ai"]["last_error"] == "the Anthropic account has no credit left"


def summaries_waiting(app):
    return app.get("/api/digest").json["ai"]["summaries_waiting"]


def test_background_summaries_stopped_for_want_of_credit_go_again_once_a_call_goes_through(app, world, ai):
    """Credit is added to the same key: the next call that goes through says it is back."""
    waiting = summaries_waiting(app)
    ai.queue(NO_CREDIT)
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 502
    app.clock.shift(timedelta(seconds=46))             # past the yield to it
    ai.responder = triage
    app.work("assess")
    assert summaries_waiting(app) == waiting > 0
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 200
    app.clock.shift(timedelta(seconds=46))
    app.work("assess")
    assert summaries_waiting(app) == 0


def test_background_summaries_stopped_for_want_of_credit_try_again_an_hour_later(app, world, ai):
    waiting = summaries_waiting(app)
    ai.queue(NO_CREDIT)
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 502
    ai.responder = triage
    app.clock.shift(timedelta(minutes=59))
    app.work("assess")
    assert summaries_waiting(app) == waiting > 0
    app.clock.shift(timedelta(minutes=1))
    app.work("assess")
    assert summaries_waiting(app) == 0


def test_spending_says_what_the_last_week_and_this_month_cost_by_what_each_feature_is_called(app, world, ai):
    def costly(obj, tokens):
        status, said = fa.answer(obj)
        return status, {**said, "usage": {"input_tokens": tokens, "output_tokens": 0}}
    none = {"calls": 0, "cost_micros": 0, "features": []}
    assert app.get("/api/ai/spending").json == {"ok": True, "week": none, "month": none}
    app.clock.shift(timedelta(days=-3))                 # Friday 30 October: in the last week, not this month
    ai.queue(costly({"text": "the"}, 1000))
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 200
    app.clock.shift(timedelta(days=3))
    ai.queue(costly({"text": "the"}, 1000))
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 200
    ai.queue(costly({"tldr": "Lunch is on Thursday.", "points": [], "action": "", "participants": []}, 5000))
    assert app.post("/api/ai/summarize", world.copy("ada-inbox-02-lunch-confirm.eml")).status == 200
    spent = app.get("/api/ai/spending").json
    week, month = spent["week"], spent["month"]
    assert [(f["label"], f["calls"]) for f in week["features"]] == [("Conversation summaries", 1), ("Rewrite", 2)]
    assert [(f["label"], f["calls"]) for f in month["features"]] == [("Conversation summaries", 1), ("Rewrite", 1)]
    assert (week["calls"], month["calls"]) == (3, 2)
    assert week["cost_micros"] == sum(f["cost_micros"] for f in week["features"]) > month["cost_micros"] > 0
    assert app.post("/api/preferences", {"ai_enabled": False}).status == 200
    assert app.get("/api/ai/spending").json == spent                  # it reads what is stored, AI on or off


def test_status_after_calls(app, golden, world, ai):
    golden.record("the digest's AI status before any call", app.get("/api/digest"))
    app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"})
    golden.note("ai after a good call", app.get("/api/digest").json["ai"])
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"})
    golden.note("ai after a failed call", app.get("/api/digest").json["ai"])
