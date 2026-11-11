"""Why a sender is worth cleaning up: the numbers are the mail's, and what comes back is the model's own words on them, held to what it was shown."""

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import assistant
from mailmind.bootstrap.curation import rules

LOOM, THIMBLE = "news@loom.example.test", "orders@thimble.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


# ── the route ────────────────────────────────────────────────────────────────────────────────────

def test_the_rationale_is_written_for_each_sender_with_mail(app, world, ai):
    ai.queue(fa.answer({"rationales": [{"id": 1, "text": "You have never opened these."}]}))
    r = app.post("/api/cleanup/rationale", {"senders": [LOOM, "nobody@example.test"]})
    assert (r.status, r.json["ok"], r.json["rationales"]) == (200, True, {LOOM: "You have never opened these."})
    assert LOOM in ai.requests[0].prompt and "1 messages over" in ai.requests[0].prompt


def test_each_message_counts_once_however_many_folders_hold_it(app, world, ai):
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER), (w.SEEN,))
    app.post("/api/cleanup/rationale", {"senders": [LOOM]})
    assert "1 messages over" in ai.requests[0].prompt
    assert ", 0 of them never opened" in ai.requests[0].prompt      # read in one of its copies is read


def test_it_changes_nothing_and_is_counted_under_its_own_feature(app, world, ai):
    before, mark = rules.load(), world.mark()
    app.post("/api/cleanup/rationale", {"senders": [LOOM]})
    assert (rules.load(), world.writes(mark)) == (before, [])
    assert assistant.calls.usage(1)[0]["features"]["rationale"]["calls"] == 1


def test_with_no_mail_from_any_of_them_nothing_is_sent(app, world, ai):
    r = app.post("/api/cleanup/rationale", {"senders": ["nobody@example.test"]})
    assert (r.status, r.json["rationales"], ai.requests) == (200, {}, [])


def test_a_list_that_wont_read_is_the_400(app, world, ai):
    r = app.post("/api/cleanup/rationale", {"senders": ["nobody"]})
    assert (r.status, r.json["detail"], ai.requests) == (400, "bad senders", [])
