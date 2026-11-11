"""Summarize this sender: what is read of them, and what is taken from an answer."""
import pytest

from assistant_ports import Ledger, Model, Uow, calls_on
from fixtures import world as w
from mailmind.assistant.application.sender_digest import SenderDigest
from mailmind.assistant.domain import sender_digest as sd
from mailmind.kernel.errors import NotFound

LIN = "lin@example.test"
LOOM = "news@loom.example.test"


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


# ── the use case, on fakes behind its ports ──────────────────────────────────────────────────────

class Mail:
    """What is stored of each sender, newest first. It notes each thing it is asked."""

    def __init__(self, **rows):
        self.rows = rows
        self.asked = []

    def sender_recent(self, c, addr, n):
        self.asked.append(("sender_recent", addr, n))
        return self.rows.get(addr, [])[:n]


def summarize(model, addr=LOOM, ledger=None, mail=None):
    mail = mail or Mail(**{LOOM: [from_loom(subject="Issue 12")]})
    return SenderDigest(calls_on(model, ledger), Uow(), mail).summarize(addr)


def from_loom(**more):
    return {**row(**more), "from_name": "Loom"}


def test_an_empty_answer_comes_back_as_the_threads_themselves():
    said = summarize(Model({"summary": "", "topics": [], "open": ""}))
    assert said == {"ok": True, "addr": LOOM, "threads": 1, "summary": "1 recent thread, the latest about Issue 12.",
                    "topics": [], "open": ""}


def test_it_reads_what_is_stored_of_them_only_and_is_counted_under_its_own_feature():
    ledger, mail = Ledger(), Mail(**{LOOM: [from_loom()]})
    summarize(Model({"summary": "A weaving newsletter.", "topics": [], "open": ""}), ledger=ledger, mail=mail)
    assert mail.asked == [("sender_recent", LOOM, sd.RECENT)]          # no body fetched, nothing else asked
    assert ledger.features == ["sender"]


def test_a_sender_with_no_mail_is_not_found_and_nothing_is_sent():
    model = Model()
    with pytest.raises(NotFound, match="no mail from this sender"):
        summarize(model, "nobody@example.test")
    assert model.sent == []
