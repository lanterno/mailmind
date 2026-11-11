"""A click on a server that has hung: an answer within a moment, so Undo is offered, and the worker takes the command up
later, the way a try that lost its connection leaves one. The try gives up on a socket timeout, so a test lowers the seconds
it waits and leaves the rest of the path as it is."""
import time
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.bootstrap import mailbox

RECEIPT = "ada-inbox-05-receipt.eml"
LISTEN = 0.5               # seconds a try waits for each answer in these tests, where it would be TRY_TIMEOUT


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(mailbox.commands, "TRY_TIMEOUT", LISTEN)


@pytest.fixture(params=["imaplib", "imapclient"])
def adapter(request, world):
    """Each adapter gives up the same way, with the fake server not answering a word."""
    if request.param == "imapclient":
        w.ask_for_imapclient(world.dir, w.ADA)
    return request.param


@pytest.fixture
def endings(monkeypatch):
    """How each session the test opens ends: with a LOGOUT, or hung up on with none."""
    from mailmind.mailbox.adapters.imap.imapclient_session import ImapClientSession
    from mailmind.mailbox.adapters.imap.imaplib_session import ImaplibSession
    ended = []
    for cls in (ImaplibSession, ImapClientSession):
        for how in ("logout", "shutdown"):
            def noting(self, real=getattr(cls, how), how=how):
                ended.append(how)
                return real(self)
            monkeypatch.setattr(cls, how, noting)
    return ended


def rows():
    c = mailbox.store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM mail_commands ORDER BY id")]
    finally:
        c.close()


def hidden(pk):
    c = mailbox.store.conn()
    try:
        return mailbox.store.get_message(c, pk)["pending_removal"] is not None
    finally:
        c.close()


@pytest.mark.parametrize("command", ["LOGIN", "SELECT", "UID MOVE"])
def test_a_click_on_a_hung_server_answers_once_it_has_waited_and_offers_undo(app, world, quick, adapter, endings, command):
    """Once, for each step's answer, and with no second wait for a LOGOUT nobody will answer."""
    pk = world.pk(RECEIPT)
    world.server.stall(command, user=w.ADA)
    began = time.perf_counter()
    try:
        r = app.post("/api/archive", w.copy_of(pk))
    finally:
        took = time.perf_counter() - began
        world.server.release_stalls()
    assert (r.json["ok"], r.json["status"], r.json["undoable"]) == (True, "queued", True)
    assert hidden(pk)
    assert LISTEN <= took < 10, f"took {took:.2f}s"      # it waited for the answer, and not the 12 s a person would
    assert endings == ([] if command == "LOGIN" else ["shutdown"])     # no session, or one hung up on with no LOGOUT
    [row] = rows()
    assert (row["state"], row["attempts"], row["begun"]) == ("queued", 0, 1)


def test_a_click_that_heard_nothing_is_undone_like_any_other_waiting(app, world, quick):
    pk, since = world.pk(RECEIPT), world.mark()
    world.server.stall("SELECT", user=w.ADA)
    try:
        assert app.post("/api/archive", w.copy_of(pk)).json["status"] == "queued"
    finally:
        world.server.release_stalls()
    assert app.post("/api/undo", {}).json["ok"]
    assert rows() == [] and world.row(RECEIPT)["pending_removal"] is None
    assert world.writes(since) == []


def test_the_worker_leaves_a_try_that_heard_nothing_for_a_while(app, world, quick):
    """A dropped connection went back due now; one that heard nothing may have left a command running, so it isn't."""
    from mailmind.bootstrap.mailbox import commands
    pk = world.pk(RECEIPT)
    world.server.stall("SELECT", user=w.ADA, times=1)
    try:
        assert app.post("/api/archive", w.copy_of(pk)).json["status"] == "queued"
    finally:
        world.server.release_stalls()
    selects = len(world.server.commands_for(w.ADA, "SELECT"))
    commands.drain()
    app.clock.shift(timedelta(seconds=commands.AFTER_SILENCE - 1))
    commands.drain()
    assert len(world.server.commands_for(w.ADA, "SELECT")) == selects and rows()[0]["state"] == "queued"
    app.clock.shift(timedelta(seconds=2))
    commands.drain()
    [row] = rows()
    assert (row["state"], row["applied"]) == ("done", 1)
    assert [box for _, box, _, _ in world.where(RECEIPT)["server"]] == ["Archive"]


def test_a_drop_still_goes_back_at_once(app, world):
    from mailmind.bootstrap.mailbox import commands
    pk = world.pk(RECEIPT)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", w.copy_of(pk)).json["status"] == "queued"
    assert rows()[0]["next_attempt_at"] == 0
    commands.drain()
    assert rows()[0]["state"] == "done"


@pytest.mark.parametrize("has_move", [True, False], ids=["move", "copy-and-expunge"])
def test_a_try_the_server_finished_without_saying_applies_once(app, world, quick, has_move):
    """The command ran and the answer never came, so the retry asks where the message went and moves nothing."""
    from mailmind.bootstrap.mailbox import commands
    if not has_move:
        world.server.without.add("MOVE")
    pk, since = world.pk(RECEIPT), world.mark()
    world.server.stall("UID MOVE" if has_move else "UID EXPUNGE", user=w.ADA, times=1, after=True)
    try:
        assert app.post("/api/archive", w.copy_of(pk)).json["status"] == "queued"
    finally:
        world.server.release_stalls()
    [begun] = rows()
    assert begun["begun"] == 1
    app.clock.shift(timedelta(seconds=commands.AFTER_SILENCE + 1))
    commands.drain()
    [row] = rows()
    assert (row["state"], row["applied"]) == ("done", 1)
    assert [box for _, box, _, _ in world.where(RECEIPT)["server"]] == ["Archive"]
    copied = [x for x in world.writes(since) if "UID MOVE" in x or "UID COPY" in x]
    assert len(copied) == 1, copied


def test_someone_waiting_is_the_only_one_a_short_try_is_for(app, world, monkeypatch):
    """The worker has nobody waiting, and a slow server deserves the session's whole timeout."""
    from mailmind.bootstrap.mailbox import commands, server_ops
    pk, given, real = world.pk(RECEIPT), [], server_ops._open_session

    def spy(acct, **kw):
        given.append(kw.get("timeout"))
        return real(acct, **kw)
    monkeypatch.setattr(server_ops, "_open_session", spy)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", w.copy_of(pk)).json["status"] == "queued"
    commands.drain()
    assert given == [commands.TRY_TIMEOUT, None]
