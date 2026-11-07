"""What the AI said of a message is read under its key, in every list: `assessments`."""
import pytest

from fixtures import world as w
from mailmind.bootstrap import assistant, attention, discovery
from mailmind.bootstrap.mailbox import store
from mailmind.discovery.domain.query import QuerySpec

LUNCH = "ada-inbox-02-lunch-confirm.eml"
CI = "ada-inbox-04-ci.eml"
SAID = ("Lin asks about lunch.", 5, "action-needed", "Reply", "claude-opus-5-5", "2026-11-02T11:00:00+00:00")


def rows(found):
    return [dict(r) for r in found]


# Each answers with the rows of what was said, so a reader that read nothing can't pass.
READERS = {
    "digest": lambda c: rows(store.digest(c)),
    "brief": lambda c: rows(store.brief_rows(c, [w.ADA], 50)),
    "delta": lambda c: rows(store.delta_rows(c, [w.ADA], 0, 50)),
    "needs you": lambda c: rows(store.needs_you_rows(c, [w.ADA], 50)),
    "drafting ahead": lambda c: rows(store.draft_candidates(c, 0)),
    "a folder": lambda c: rows(store.folder_rows(c, w.ADA, "INBOX", 50, 0)),
    "a folder's conversations": lambda c: rows(store.folder_conversation_rows(c, w.ADA, "INBOX", 50, 0)),
    "search": lambda c: rows(discovery._mail.search(QuerySpec.parse("category:action-needed"), w.ADA, 50, 0)),
    "search by word": lambda c: rows(discovery._mail.search(QuerySpec.parse('lunch category:action-needed'), w.ADA, 50, 0)),
    "ask": lambda c: rows(discovery._mail.for_asking(None, w.ADA, 50)),
    "ask by word": lambda c: rows(discovery._mail.for_asking('"lunch"', w.ADA, 50)),
}


def verdict_in(row):
    return row.get("importance") == SAID[1] or row.get("category") == SAID[2] or row.get("ai_summary") == SAID[0]


@pytest.fixture
def c(world):
    conn = store.conn()
    assistant.answers.save_summary(conn, world.pk(LUNCH), *SAID)
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.parametrize("reader", READERS)
def test_each_list_reads_what_was_said_under_the_key(c, reader):
    before = READERS[reader](c)
    assert any(verdict_in(r) for r in before)


@pytest.mark.parametrize("reader", READERS)
def test_no_list_says_anything_once_nothing_is_said_under_the_key(c, reader):
    c.execute("DELETE FROM assessments")
    c.commit()
    assert not any(verdict_in(r) for r in READERS[reader](c))


def test_the_kick_gate_reads_it_under_the_key_too(c, world):
    assert assistant.answers.reply_worthy(c, world.pk(LUNCH))
    assert not assistant.answers.reply_worthy(c, world.pk(CI))


def test_a_waiting_thread_is_found_by_what_was_said_under_the_key(c, world):
    lunch = world.row(LUNCH)
    c.execute("UPDATE messages SET seen=1, answered=0, draft=0, thread_id='T', ts=? WHERE pk=?", (1_800_000_000, lunch["pk"]))
    attention.book.followup_detect_received(c, 1_800_000_000 + 5 * 86400, 3, 14)
    assert [r["thread_id"] for r in c.execute("SELECT thread_id FROM followups WHERE kind='received'")] == ["T"]


def test_a_second_copy_needs_no_second_call(c, world):
    second = copy_in(c, world, "Archive")
    assert second not in {r["pk"] for r in assistant.answers.needs_summary(c)}
    [got] = [r for r in store.folder_rows(c, w.ADA, "Archive", 50, 0) if r["pk"] == second]
    assert got["summary"] == SAID[0]


def copy_in(c, world, folder):
    r = world.row(LUNCH)
    fields = ("account", "kind", "message_id", "gmail_thread", "from_addr", "from_name", "subject", "date_iso", "ts", "snippet",
              "bucket", "is_junk")
    return store.upsert_message(c, {**{k: r[k] for k in fields}, "folder": folder, "uid": 9000})


def test_the_stats_count_copies_as_they_did(c, world):
    second = copy_in(c, world, "Archive")
    assert second
    stats = store.stats(c)
    assert stats["summarized"] == 2
    needs = stats["needs"]
    store.delete_message(c, second)
    store.delete_message(c, world.pk(LUNCH))           # what was said waits under the key, and isn't counted
    stats = store.stats(c)
    assert stats["summarized"] == 0 and stats["needs"] == needs


def test_a_row_with_no_key_waits_for_the_next_start_rather_than_paying_each_round(c, world):
    """It has nowhere to keep an answer, so it would be asked about for ever."""
    pk = world.pk(CI)
    c.execute("UPDATE messages SET message_key=NULL WHERE pk=?", (pk,))
    assert pk not in {r["pk"] for r in assistant.answers.needs_summary(c)}
