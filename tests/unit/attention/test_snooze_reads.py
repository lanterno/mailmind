"""Whether a message is snoozed is read from `snoozes`, by its key and folder, in every list."""
from datetime import datetime, timedelta, timezone

import pytest

from fixtures import world as w
from mailmind.bootstrap import assistant, attention, curation, discovery
from mailmind.bootstrap.mailbox import store
from mailmind.discovery.domain.query import QuerySpec

LUNCH = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"
UNTIL = 4102444800
SAID = ("Lin asks about lunch.", 5, "action-needed", "Reply", "claude-opus-5-5", "2026-11-02T11:00:00+00:00")


def pks(found):
    return {r["pk"] for r in found}


def search(text):
    return discovery._mail.search(QuerySpec.parse(text), w.ADA, 50, 0)


LISTS = {
    "digest": lambda c: store.digest(c),
    "brief": lambda c: store.brief_rows(c, [w.ADA], 50),
    "delta": lambda c: store.delta_rows(c, [w.ADA], 0, 50),
    "needs you": lambda c: store.needs_you_rows(c, [w.ADA], 50),
    "drafting ahead": lambda c: store.draft_candidates(c, 0),
    "a folder": lambda c: store.folder_rows(c, w.ADA, "INBOX", 50, 0),
    "a folder's conversations": lambda c: store.folder_conversation_rows(c, w.ADA, "INBOX", 50, 0),
    "search": lambda c: search("category:action-needed"),
    "search by word": lambda c: search("lunch category:action-needed"),
}
COUNTS = {
    "a folder's count": lambda c: store.folder_count(c, w.ADA, "INBOX"),
    "its conversations' count": lambda c: store.folder_conversation_count(c, w.ADA, "INBOX"),
}


@pytest.fixture
def c(world):
    conn = store.conn()
    assistant.answers.save_summary(conn, world.pk(LUNCH), *SAID)
    conn.commit()
    yield conn
    conn.close()


def set_snooze(c, pk, until=UNTIL):
    attention.book.set_snooze(c, pk, until)
    c.commit()


def copy_in(c, world, folder, uid=9000):
    r = world.row(LUNCH)
    fields = ("account", "kind", "message_id", "gmail_thread", "from_addr", "from_name", "subject", "date_iso", "ts", "snippet",
              "bucket", "is_junk")
    pk = store.upsert_message(c, {**{k: r[k] for k in fields}, "folder": folder, "uid": uid})
    c.commit()
    return pk


@pytest.mark.parametrize("reader", LISTS)
def test_a_list_leaves_snoozed_mail_out_by_snoozes(c, world, reader):
    pk = world.pk(LUNCH)
    assert pk in pks(LISTS[reader](c))
    set_snooze(c, pk)
    assert pk not in pks(LISTS[reader](c))
    c.execute("DELETE FROM snoozes")
    c.commit()
    assert pk in pks(LISTS[reader](c))


@pytest.mark.parametrize("reader", COUNTS)
def test_a_count_leaves_snoozed_mail_out_by_snoozes(c, world, reader):
    before = COUNTS[reader](c)
    set_snooze(c, world.pk(RECEIPT))                    # alone in its thread
    assert COUNTS[reader](c) < before


def test_the_snoozed_view_lists_it_with_the_time_snoozes_holds(c, world):
    pk = world.pk(LUNCH)
    set_snooze(c, pk)
    [row] = store.snoozed_rows(c, w.ADA, 50, 0)
    assert (row["pk"], row["snoozed_until"], row["importance"]) == (pk, UNTIL, SAID[1])
    assert store.snoozed_count(c, w.ADA) == 1
    assert store.snoozed_rows(c, w.BEA, 50, 0) == [] and store.snoozed_count(c, w.BEA) == 0


def test_the_snoozed_view_goes_soonest_first_and_leaves_out_what_is_going(c, world):
    first, second = world.pk("ada-inbox-04-ci.eml"), world.pk(LUNCH)
    set_snooze(c, second, UNTIL)
    set_snooze(c, first, UNTIL - 60)
    assert [r["pk"] for r in store.snoozed_rows(c, w.ADA, 50, 0)] == [first, second]
    store.mark_pending_removal(c, first, 1)
    assert [r["pk"] for r in store.snoozed_rows(c, w.ADA, 50, 0)] == [second]
    assert store.snoozed_count(c, w.ADA) == 1


def test_search_asks_snoozes_about_snoozed_mail(c, world):
    pk = world.pk(LUNCH)
    set_snooze(c, pk)
    c.commit()
    assert pks(search("lunch")) == pks(search("lunch -is:snoozed")) and pk not in pks(search("lunch"))
    assert pks(search("is:snoozed")) == {pk} and pks(search("lunch is:snoozed")) == {pk}


def test_a_bulk_commands_reach_asks_snoozes_too(c, world):
    pk = world.pk(LUNCH)
    set_snooze(c, pk)
    c.commit()
    count, found, _ = discovery._mail.reach(QuerySpec.parse("is:snoozed"), 50, {"trash"})
    assert count == 1 and [r["message_key"] for r in found] == [world.row(LUNCH)["message_key"]]
    _, found, _ = discovery._mail.reach(QuerySpec.parse("lunch"), 50, {"trash"})
    assert found and world.row(LUNCH)["message_key"] not in {r["message_key"] for r in found}


def test_a_sweep_leaves_a_snoozed_message_alone_by_snoozes(c, world):
    r = world.row(LUNCH)
    c.execute("UPDATE messages SET is_junk=1 WHERE pk=?", (r["pk"],))
    c.commit()
    junk = curation._mail.ai_junk_ids
    assert r["message_id"] in junk(w.ADA)
    set_snooze(c, r["pk"])
    c.commit()
    assert r["message_id"] not in junk(w.ADA)


def test_a_waiting_thread_isnt_nudged_while_snoozed_by_snoozes(c, world):
    pk = world.pk(LUNCH)
    c.execute("UPDATE messages SET seen=1, answered=0, draft=0, thread_id='T', ts=? WHERE pk=?", (1_800_000_000, pk))
    set_snooze(c, pk)
    attention.book.followup_detect_received(c, 1_800_000_000 + 5 * 86400, 3, 14)
    assert c.execute("SELECT COUNT(*) FROM followups WHERE kind='received'").fetchone()[0] == 0


def test_a_message_read_alone_says_when_it_wakes(c, world):
    pk = world.pk(LUNCH)
    assert store.get_message(c, pk)["snooze_until"] is None
    set_snooze(c, pk)
    assert store.get_message(c, pk)["snooze_until"] == UNTIL


def test_drafting_ahead_passes_over_a_snoozed_message_by_snoozes(c, world):
    row = world.row(LUNCH)
    assert assistant.drafting._draft_eligible(store.get_message(c, row["pk"]))
    set_snooze(c, row["pk"])
    assert not assistant.drafting._draft_eligible(store.get_message(c, row["pk"]))


def test_due_goes_by_snoozes(c, world):
    pk = world.pk(LUNCH)
    set_snooze(c, pk, 1_000)
    assert pks(attention.book.due_snoozes(c, 999)) == set()
    assert pks(attention.book.due_snoozes(c, 1_000)) == {pk}


def test_undo_of_a_second_snooze_gives_back_the_first_time(app, world):
    pk = world.pk(LUNCH)
    subject = world.row(LUNCH)["subject"]
    first = int((datetime.now(timezone.utc) + timedelta(hours=3)).timestamp())
    second = first + 3600
    assert app.post("/api/snooze", {"pk": pk, "until": first}).json["ok"]
    assert app.post("/api/snooze", {"pk": pk, "until": second}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    [item] = [i for i in app.get(f"/api/messages?account={w.ADA}&folder=__snoozed__&limit=100").json["items"] if i["subject"] == subject]
    assert item["snoozed_until"] == first


# -- what `snoozes` being by folder means ------------------------------------------------------------------

def test_a_second_copy_in_the_folder_is_hidden_with_the_first(c, world):
    """A row-by-row column left it listed until a sync next stored it."""
    one, other = world.pk(LUNCH), copy_in(c, world, "INBOX")
    set_snooze(c, one)
    assert not {one, other} & pks(store.folder_rows(c, w.ADA, "INBOX", 50, 0))
    assert {r["pk"] for r in store.snoozed_rows(c, w.ADA, 50, 0)} == {one, other}
    assert store.snoozed_count(c, w.ADA) == 2


def test_a_copy_elsewhere_is_not_hidden(c, world):
    one, elsewhere = world.pk(LUNCH), copy_in(c, world, "Archive")
    set_snooze(c, one)
    assert elsewhere in pks(store.folder_rows(c, w.ADA, "Archive", 50, 0))
    assert [r["pk"] for r in store.snoozed_rows(c, w.ADA, 50, 0)] == [one]


def test_both_copies_wake_together(c, world):
    one, other = world.pk(LUNCH), copy_in(c, world, "INBOX")
    set_snooze(c, one, 1_000)
    assert pks(attention.book.due_snoozes(c, 1_000)) == {one, other}


def test_a_row_with_no_key_is_listed(c, world):
    pk = world.pk(LUNCH)
    set_snooze(c, pk)
    c.execute("UPDATE messages SET message_key=NULL WHERE pk=?", (pk,))
    c.commit()
    assert pk in pks(store.folder_rows(c, w.ADA, "INBOX", 50, 0))
    assert pk in pks(search("lunch")) and pk in pks(search("lunch -is:snoozed")) and pk not in pks(search("is:snoozed"))
