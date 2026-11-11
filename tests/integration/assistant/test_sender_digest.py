"""Summarize this sender: what is read of them, and what is taken from an answer."""
import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap import assistant
from mailmind.bootstrap.mailbox import store
LOOM = "news@loom.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


# ── the route ────────────────────────────────────────────────────────────────────────────────────

def test_a_summary_reads_their_threads_and_says_so(app, world, ai):
    ai.queue(fa.answer({"summary": "A weaving newsletter.", "topics": ["weaving"], "open": ""}))
    r = app.post("/api/contact/summary", {"addr": LOOM})
    assert (r.status, r.json) == (200, {"ok": True, "addr": LOOM, "threads": 1, "summary": "A weaving newsletter.", "topics": ["weaving"], "open": ""})
    assert LOOM in ai.requests[0].prompt


def test_an_empty_answer_comes_back_as_the_threads_themselves(app, world, ai):
    ai.queue(fa.answer({"summary": "", "topics": [], "open": ""}))
    r = app.post("/api/contact/summary", {"addr": LOOM})
    assert r.json["summary"].startswith("1 recent thread, the latest about")


def test_it_reads_stored_mail_only_and_is_counted_under_its_own_feature(app, world, ai):
    mark = world.mark()
    app.post("/api/contact/summary", {"addr": LOOM})
    assert world.server.commands[mark:] == []            # no IMAP asked for anything
    assert assistant.calls.usage(1)[0]["features"]["sender"]["calls"] == 1


def test_a_sender_with_no_mail_is_a_404_and_nothing_is_sent(app, world, ai):
    r = app.post("/api/contact/summary", {"addr": "nobody@example.test"})
    assert (r.status, ai.requests) == (404, [])


def test_mail_on_its_way_out_is_not_read(app, world, ai):
    c = store.conn()
    store.mark_pending_removal(c, world.pk(NEWSLETTER), 1)
    c.commit()
    c.close()
    r = app.post("/api/contact/summary", {"addr": LOOM})
    assert r.status == 404


@pytest.mark.parametrize("body", [{}, {"addr": "nobody"}])
def test_an_address_that_wont_read_is_the_400(app, world, ai, body):
    r = app.post("/api/contact/summary", body)
    assert (r.status, r.json["detail"], ai.requests) == (400, "bad address", [])


def test_a_get_spends_nothing(app, ai):
    assert app.get("/api/contact/summary").status in (404, 405)
    assert ai.requests == []
