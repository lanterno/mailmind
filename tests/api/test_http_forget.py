"""The forget round, daily (`bootstrap.background.forget_round`): what is kept of mail that has gone, let go once a month
has passed. A message key no copy has come back to goes, and what was said of the message with it; a change finished a
month ago goes from the queue. Until then a copy that comes back finds its key, and a finished change can be undone.

The round also lets go of the Assistant's finished batches, which the Rust server's round leaves to the Assistant; these
tests look only at the Mailbox's part.
"""
from datetime import timedelta

from fixtures import world as w
from stored_rows import q


def gone_from_the_server(app, world, name):
    """Another client deletes `name` from the server, and a reconcile finds its copy gone, so its key has none left."""
    with world.server.lock:
        world.server.mailbox(*world.placed[name][:2]).messages.pop(world.uid(name))
    app.work("reconcile", w.ADA)


def said_of(key):
    """What the AI said of a message, as its assessment row: here, that it is a receipt."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("INSERT OR REPLACE INTO assessments (message_key, summary, created_at) VALUES (?, 'A receipt.', "
                  "'2026-11-02T12:00:00+00:00')", (key,))
        c.commit()
    finally:
        c.close()


def kept(keys):
    """Which of `keys` the database still holds, and whether anything was said of each."""
    held = {r["key"] for r in q(f"SELECT key FROM message_identity WHERE key IN ({','.join('?' * len(keys))})", *keys)}
    said = {r["message_key"] for r in q("SELECT message_key FROM assessments")}
    return {key: (key in held, key in said) for key in keys}


def finished():
    """The message ids of the changes the queue still holds as done."""
    return sorted(r["message_id"] for r in q("SELECT message_id FROM mail_commands WHERE state='done'"))


def test_what_went_a_month_ago_is_let_go(app, world):
    early, late = "ada-inbox-05-receipt.eml", "ada-inbox-04-ci.eml"
    early_key, late_key = world.key(early), world.key(late)
    said_of(early_key)
    gone_from_the_server(app, world, early)
    assert app.post("/api/archive", world.copy("ada-inbox-09-rfc2047-folded.eml")).status == 200
    archived_early = finished()
    assert len(archived_early) == 1

    # Twenty-nine days on, another goes and another change finishes; nothing is a month old yet.
    app.clock.shift(timedelta(days=29))
    gone_from_the_server(app, world, late)
    assert app.post("/api/archive", world.copy("ada-inbox-12-scam.eml")).status == 200
    archived = finished()
    assert len(archived) == 2
    app.work("forget")
    assert kept([early_key, late_key]) == {early_key: (True, True), late_key: (True, False)}
    assert finished() == archived

    # A month and a second after the first went, it goes, with what was said of it, and so does the first change.
    app.clock.shift(timedelta(days=1, seconds=1))
    app.work("forget")
    assert kept([early_key, late_key]) == {early_key: (False, False), late_key: (True, False)}
    assert finished() == sorted(set(archived) - set(archived_early))


def test_a_key_whose_copy_came_back_is_kept_however_old(app, world):
    """Only a key whose every copy went is let go: an archived message's copy comes back with the archive's sync,
    finds its key, and keeps it."""
    name = "ada-inbox-09-rfc2047-folded.eml"
    key = world.key(name)
    said_of(key)
    assert app.post("/api/archive", world.copy(name)).status == 200
    [(account, archive)] = [(copy[0], copy[1]) for copy in world.where(name)["server"]]
    app.work("sync", account, archive)
    app.clock.shift(timedelta(days=90))
    app.work("forget")
    assert kept([key]) == {key: (True, True)}
    assert [(row["account"], row["folder"]) for row in world.where(name)["db"]] == [(account, archive)]
