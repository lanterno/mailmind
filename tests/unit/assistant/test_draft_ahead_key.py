"""A reply drafted ahead is kept under its thread, or its Message-ID for a message with no thread, and checked against a
fingerprint of what those hold."""
import urllib.parse

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest, store

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word: the anchor of Ada's lunch thread
QUILT = "ada-inbox-11-html-cid.eml"             # a person's message, alone in its thread
DRAFT = "See you at 12:30."


def query(sql, *args):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def execute(sql, *args):
    c = store.conn()
    try:
        c.execute("PRAGMA foreign_keys=OFF")             # what hangs off a row isn't what's being asked about
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def pk_of(name):
    [r] = query("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?", w.ADA, w.message_id(name))
    return r["pk"]


def peek(app, pk):
    """What drafting ahead has for the message row `pk` is now, named as the page names it: by its key and folder."""
    return app.get("/api/ai/draft-ahead?" + urllib.parse.urlencode({**w.copy_of(pk), "peek": 1})).json["status"]


def banked(app, ai, pk):
    ai.queue(fa.answer({"body": DRAFT}))
    assert app.post("/api/ai/draft-reply", w.copy_of(pk)).status == 200


def test_a_draft_is_served_for_its_thread_alone(app, world, ai):
    lunch, quilt = world.pk(LUNCH), world.pk(QUILT)
    banked(app, ai, lunch)
    assert (peek(app, lunch), peek(app, quilt)) == ("ready", "none")


def test_a_lone_message_is_served_its_draft_alone(app, world, ai):
    lunch, quilt = world.pk(LUNCH), world.pk(QUILT)
    execute("UPDATE messages SET thread_id=NULL WHERE pk=?", quilt)
    banked(app, ai, quilt)
    assert (peek(app, quilt), peek(app, lunch)) == ("ready", "none")


def test_a_pk_that_has_gone_to_another_message_is_not_served_the_draft_it_was_made_from(app, world, ai):
    lunch = world.pk(LUNCH)
    banked(app, ai, lunch)
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    moved, quilt = pk_of(LUNCH), pk_of(QUILT)
    assert moved != lunch and query("SELECT 1 FROM messages WHERE pk=?", lunch) == []
    execute("UPDATE messages SET pk=? WHERE pk=?", lunch, quilt)
    assert (peek(app, moved), peek(app, lunch)) == ("ready", "none")
