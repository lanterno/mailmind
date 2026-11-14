"""The body backfill over HTTP (`syncing.backfill_round`, `work backfill`): old mail's bodies fetched a little at a time,
read without marking anything read, so search finds what they say. Each account takes its turn at a round's dozen, newest
first. A body the server didn't give waits six hours; a lost connection rests only its account, for a minute; and mail on
its way out is left alone.

The world's mail all lacks its body text, and a round asks for twelve with a second and a half between, so each test
leaves only the few it needs without one, as if the rest had been opened.
"""
import json
from datetime import timedelta
from urllib.parse import quote

from fixtures import world as w
from mailmind.bootstrap.mailbox import store

ARCHIVED = "ada-archive-01-old-thread.eml"       # "What about a log cabin layout?"
SENT = "ada-sent-01-lunch-reply.eml"
CLIENTS = "bea-clients-01-harbor.eml"
RECEIPT = "ada-inbox-05-receipt.eml"


def rows(sql, *args):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def only_these_without_a_body(app, world, *names):
    """Every other message in the world has its body text, as mail opened has, written while the server is stopped."""
    pks = [world.pk(name) for name in names]
    with app.stopped():
        c = store.conn()
        try:
            c.execute("UPDATE messages SET body_text='' WHERE pk NOT IN (SELECT value FROM json_each(?))",
                      (json.dumps(pks),))
            c.commit()
        finally:
            c.close()


def where(world, name):
    return (*world.placed[name][:2], world.uid(name))


def bodies_asked(world, since):
    """The bodies asked of the server since `since`, in order: (account, folder, uid)."""
    return [(c["user"], c["mailbox"], int(c["args"][0])) for c in world.server.commands[since:]
            if c["command"] == "UID FETCH" and "BODY.PEEK[]" in c["line"].upper()]


def has_body(world, name):
    [row] = rows("SELECT body_text FROM messages WHERE pk=?", world.pk(name))
    return row["body_text"] is not None


def found(app, words):
    return [(r["account"], r["folder"], r["uid"])
            for r in app.get(f"/api/search?scope=all&q={quote(words)}").json["items"]]


def test_old_mail_has_its_body_fetched_so_search_finds_what_it_says(app, world):
    only_these_without_a_body(app, world, ARCHIVED)
    assert found(app, "cabin") == []
    since = world.mark()
    app.work("backfill")
    assert bodies_asked(world, since) == [where(world, ARCHIVED)]
    assert found(app, "cabin") == [where(world, ARCHIVED)]
    [row] = rows("SELECT body_fetched, body_text FROM messages WHERE pk=?", world.pk(ARCHIVED))
    assert row["body_fetched"] == 1 and "log cabin" in row["body_text"]
    assert world.writes(since) == []                       # read without marking it read
    # Caught up: the next round asks for nothing.
    since = world.mark()
    app.work("backfill")
    assert world.server.commands[since:] == []


def test_each_account_takes_its_turn_newest_first(app, world):
    only_these_without_a_body(app, world, ARCHIVED, SENT, CLIENTS)
    since = world.mark()
    app.work("backfill")
    assert bodies_asked(world, since) == [where(world, SENT), where(world, CLIENTS), where(world, ARCHIVED)]
    assert all(has_body(world, name) for name in (ARCHIVED, SENT, CLIENTS))


def test_a_body_the_server_no_longer_gives_is_asked_for_again_six_hours_on(app, world):
    only_these_without_a_body(app, world, ARCHIVED)
    box = world.server.mailbox(w.ADA, "Archive")
    with world.server.lock:                                # deleted elsewhere, not yet pruned here
        kept = box.messages.pop(world.uid(ARCHIVED))
    since = world.mark()
    app.work("backfill")
    assert bodies_asked(world, since) == [where(world, ARCHIVED)]
    assert not has_body(world, ARCHIVED)
    with world.server.lock:                                # it comes back, as a flaky server's would
        box.messages[world.uid(ARCHIVED)] = kept
    # Set aside: the next round asks nothing, nor one just short of six hours on.
    since = world.mark()
    app.work("backfill")
    app.clock.shift(timedelta(hours=6, seconds=-1))
    app.work("backfill")
    assert bodies_asked(world, since) == []
    app.clock.shift(timedelta(seconds=1))
    app.work("backfill")
    assert bodies_asked(world, since) == [where(world, ARCHIVED)]
    assert found(app, "cabin") == [where(world, ARCHIVED)]


def test_a_lost_connection_rests_its_account_a_minute_and_the_others_go_on(app, world):
    only_these_without_a_body(app, world, ARCHIVED, CLIENTS)
    world.server.drop("UID FETCH", user=w.ADA)
    since = world.mark()
    app.work("backfill")
    # Ada's connection went over its body; Bea's turn came all the same.
    assert bodies_asked(world, since) == [where(world, ARCHIVED), where(world, CLIENTS)]
    assert (has_body(world, ARCHIVED), has_body(world, CLIENTS)) == (False, True)
    # Ada rests: nothing is asked of her server until a minute has passed.
    since = world.mark()
    app.work("backfill")
    app.clock.shift(timedelta(seconds=59))
    app.work("backfill")
    assert world.server.commands[since:] == []
    app.clock.shift(timedelta(seconds=2))
    app.work("backfill")
    assert bodies_asked(world, since) == [where(world, ARCHIVED)]
    assert has_body(world, ARCHIVED)


def test_mail_on_its_way_out_is_left_alone(app, world):
    only_these_without_a_body(app, world, RECEIPT)
    world.server.drop("UID MOVE", user=w.ADA)              # the archive can't reach the server: it waits in the queue
    assert app.post("/api/archive", world.copy(RECEIPT)).status == 200
    assert rows("SELECT pending_removal IS NOT NULL AS going FROM messages WHERE pk=?", world.pk(RECEIPT)) == [
        {"going": 1}]
    since = world.mark()
    app.work("backfill")
    assert world.server.commands[since:] == []
    assert not has_body(world, RECEIPT)
