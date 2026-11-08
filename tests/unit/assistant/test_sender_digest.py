"""Summarize this sender: what is read of them, and what is taken from an answer."""
import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.domain import sender_digest as sd
from mailmind.bootstrap import assistant
from mailmind.bootstrap.mailbox import store

LIN = "lin@example.test"
LOOM = "news@loom.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


def row(subject="Lunch?", thread="t1", account=w.ADA, summary=None, snippet="short"):
    return {"subject": subject, "thread_id": thread, "account": account, "date_iso": "2026-10-20T10:00:00+00:00", "summary": summary, "snippet": snippet}


# ── what is read, and how it is told ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, addr", [("News@Loom.example.test", LOOM), ("  " + LIN, LIN)])
def test_an_address_reads_as_rows_go_by_it(raw, addr):
    assert sd.address(raw) == addr


@pytest.mark.parametrize("raw", [None, 5, "", "nobody", "x" * 400 + "@example.test"])
def test_what_is_no_address_names_nobody(raw):
    assert sd.address(raw) is None


def test_a_thread_is_counted_once_and_keeps_its_newest_row_first():
    got = sd.threads_of([row(summary="newest said this"), row(summary="older"), row("Other", "t2")])
    assert got[0] == {"subject": "Lunch?", "messages": 2, "last": "2026-10-20", "gist": "newest said this"}
    assert len(got) == 2


def test_mail_with_no_thread_is_its_own():
    assert len(sd.threads_of([row("A", None), row("B", None)])) == 2


def test_the_same_thread_id_in_two_accounts_is_two_threads():
    assert len(sd.threads_of([row(account=w.ADA), row(account=w.CY)])) == 2


def test_only_so_many_threads_are_read():
    assert len(sd.threads_of([row(f"s{i}", f"t{i}") for i in range(sd.THREADS + 5)])) == sd.THREADS


def test_what_the_model_is_told_stays_within_its_budget():
    threads = [{"subject": "s" * 3000, "messages": 1, "last": "2026-10-20", "gist": "g"} for _ in range(40)]
    assert len(sd.prompt_lines(threads)) <= sd.CAP_TOTAL


def test_one_long_subject_doesnt_crowd_out_the_rest():
    long = {"subject": "s" * sd.CAP_TOTAL, "messages": 1, "last": "2026-10-20", "gist": ""}
    assert "Lunch?" in sd.prompt_lines([long, {**long, "subject": "Lunch?"}])


# ── what is taken from an answer ─────────────────────────────────────────────────────────────────

def test_an_answer_is_held_to_its_caps():
    got = sd.clean({"summary": "s" * 2000, "topics": ["t" * 200] * 9 + [5, None, "  "], "open": "o" * 900})
    assert (len(got["summary"]), len(got["topics"]), len(got["topics"][0]), len(got["open"])) == (sd.SUMMARY_MAX, sd.TOPICS_MAX, sd.TOPIC_MAX, sd.OPEN_MAX)


@pytest.mark.parametrize("obj", [{}, {"summary": "   "}])
def test_an_empty_answer_says_nothing_of_its_own(obj):
    assert sd.clean(obj) == {"summary": "", "topics": [], "open": ""}


def test_with_nothing_said_the_mail_says_how_it_stands():
    threads = sd.threads_of([row("Lunch?"), row("Other", "t2")])
    assert sd.fallback(threads) == {"summary": "2 recent threads, the latest about Lunch?.", "topics": [], "open": ""}


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
