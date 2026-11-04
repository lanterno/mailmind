"""The AI routes with a key, against the fake Claude API: what each route answers, and what it sent
— the prompt, the model, the effort and the output schema. A changed prompt therefore shows up as
a golden diff to review, like any other behaviour change. The golden files are in
tests/golden/http_ai/.

Unless a test queues replies, the fake answers each call from the call's own schema: "stub <field>"
for strings, 1 for numbers and the first value of each enum.
"""
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


def triaged(ai):
    import summarize
    ai.responder = triage
    n = summarize.summarize_pending(limit=100, workers=1)
    ai.requests.clear()
    return n


def sent(golden, ai, label):
    """Note every request the fake received since the last call, then forget them."""
    golden.note(label, [{"model": r.body.get("model"), "effort": (r.body.get("output_config") or {}).get("effort"),
                         "schema": r.schema, "prompt": r.prompt} for r in ai.requests])
    ai.requests.clear()


def test_summarize_a_thread_and_a_message(app, golden, world, ai):
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    ai.queue(fa.answer({"tldr": "Lunch with Lin is set for Thursday at 12:30.",
                        "points": ["Lin asked about Thursday", "Ada said yes", "Lin confirmed 12:30"],
                        "action": "", "participants": ["Lin Mori", "Ada Park"]}))
    golden.record("summarize the lunch thread", app.post("/api/ai/summarize", {"pk": lunch["pk"]}))
    sent(golden, ai, "sent for the thread")
    golden.record("again, from the cache", app.post("/api/ai/summarize", {"pk": lunch["pk"]}))
    golden.record("again, forced", app.post("/api/ai/summarize", {"pk": lunch["pk"], "force": True}))
    golden.note("calls for the repeat and the forced run", len(ai.requests))
    ai.requests.clear()
    ai.queue(fa.answer(fa.STUB_TEXT))                  # a reply that isn't JSON: a fallback is cached
    golden.record("by account and thread", app.post("/api/ai/summarize",
                                                    {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]}))
    sent(golden, ai, "sent for the Gmail thread")
    golden.record("a missing message", app.post("/api/ai/summarize", {"pk": 999999}))
    golden.record("an unknown account", app.post("/api/ai/summarize", {"account": "zed@example.test", "thread_id": "t"}))
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    golden.record("when the API fails", app.post("/api/ai/summarize", {"pk": world.pk("bea-inbox-01-client.eml")}))


def test_draft_a_reply_and_rewrite(app, golden, world, ai):
    pk = world.pk("bea-inbox-01-client.eml")
    golden.record("draft a reply", app.post("/api/ai/draft-reply", {"pk": pk}))
    golden.record("draft a short, warm reply", app.post("/api/ai/draft-reply",
                                                        {"pk": pk, "tone": "warm", "instruction": "Keep it short"}))
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
        golden.record(f"do: {cmd}", app.post("/api/ai/do-plan", {"cmd": cmd}))
    sent(golden, ai, "sent for the plans")              # one prompt, only the command differs
    golden.note("server writes while planning", world.writes(since))
    golden.record("do: nothing", app.post("/api/ai/do-plan", {"cmd": "  "}))
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    golden.record("do: when the API fails", app.post("/api/ai/do-plan", {"cmd": "archive Lin"}))


@pytest.mark.parametrize("route", ["/api/briefing", "/api/review"])
def test_briefing_and_review_are_written_once_and_a_get_only_reads(app, golden, world, ai, route):
    golden.note("messages triaged", triaged(ai))
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
    ai.requests.clear()
    golden.record(f"GET {route} for Bea", app.get(f"{route}?account={w.BEA}"))
    golden.record(f"POST {route} for Bea", app.post(route, {"account": w.BEA}))
    sent(golden, ai, "sent for Bea")


def test_since_you_last_looked(app, golden, world, ai):
    triaged(ai)
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
    triaged(ai)
    pk = world.pk("cy-inbox-02-thread-end.eml")         # Mae's last word in the venue thread
    golden.record("peek", app.get(f"/api/ai/draft-ahead?pk={pk}&peek=1"))
    golden.record("ask without the CSRF token", app.get(f"/api/ai/draft-ahead?pk={pk}", headers={"X-CSRF-Token": None}))
    golden.note("calls so far", len(ai.requests))
    golden.record("ask", app.get(f"/api/ai/draft-ahead?pk={pk}"))
    app.settle()
    sent(golden, ai, "sent")
    golden.record("ask again", app.get(f"/api/ai/draft-ahead?pk={pk}"))
    golden.record("a message that isn't the thread's newest", app.get(
        f"/api/ai/draft-ahead?pk={world.pk('cy-inbox-01-thread-start.eml')}"))
    golden.record("a message AI didn't call reply-worthy", app.get(
        f"/api/ai/draft-ahead?pk={world.pk('ada-inbox-09-rfc2047-folded.eml')}"))
    golden.record("a pk that isn't a number", app.get("/api/ai/draft-ahead?pk=x"))
    app.settle()
    golden.note("calls for those", len(ai.requests))


def test_status_after_calls(app, golden, world, ai):
    golden.record("the digest's AI status before any call", app.get("/api/digest"))
    app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"})
    golden.note("ai after a good call", app.get("/api/digest").json["ai"])
    ai.queue(fa.error(400, "invalid_request_error", "bad request"))
    app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"})
    golden.note("ai after a failed call", app.get("/api/digest").json["ai"])
