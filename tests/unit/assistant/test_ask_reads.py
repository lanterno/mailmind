"""What a question to Ask reads comes from Discovery's read model, which says None for a query the index rejects."""
import sqlite3

import pytest

from fixtures import world as w
from mailmind.bootstrap import assistant, discovery
from mailmind.bootstrap.mailbox import store
from mailmind.platform.db import database


def execute(sql, *args):
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def test_a_query_the_index_rejects_is_a_bad_query(world):
    execute("DROP TABLE msg_fts")
    assert discovery.search.for_asking('"lunch"', w.ADA, 40) is None
    assert assistant.asker.ask("all", "When is lunch?") == ({"ok": False, "error": "bad query"}, 400)


def test_a_database_that_wont_open_is_not_a_bad_query(world, monkeypatch):
    def refuses(path):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(database, "connect", refuses)
    with pytest.raises(sqlite3.OperationalError):
        assistant.asker.ask(w.ADA, "When is lunch?")


@pytest.mark.parametrize("match", ['"lunch" OR "invoice" OR "newsletter" OR "quilt"', ""], ids=["matched", "newest"])
def test_a_question_reads_only_the_mailboxes_it_was_asked_about(world, match):
    def accounts_in(scope):
        return {r["account"] for r in discovery.search.for_asking(match, scope, 40)}
    assert accounts_in(w.ADA) == {w.ADA}
    assert accounts_in([w.ADA]) == {w.ADA}
    assert accounts_in([]) == set()
    assert w.ADA in accounts_in([w.ADA, w.BEA])


def test_a_message_on_its_way_out_is_not_read(world):
    pk = world.pk("ada-inbox-02-lunch-confirm.eml")
    assert pk in {r["pk"] for r in discovery.search.for_asking("", w.ADA, 40)}
    execute("UPDATE messages SET pending_removal='a-move' WHERE pk=?", pk)
    assert pk not in {r["pk"] for r in discovery.search.for_asking("", w.ADA, 40)}
    assert pk not in {r["pk"] for r in discovery.search.for_asking('"lunch"', w.ADA, 40)}
