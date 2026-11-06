"""Each way MailMind talks to the network refuses to while this thread has a transaction open
(mailmind.platform.db.txguard): an IMAP connection and each of its commands, an SMTP session and
everything it sends, a Claude API request and a one-click unsubscribe. Each test opens a write,
tries the call, and checks that nothing reached the fake server; once the write ends, the call
goes through."""
import pytest

from fixtures import world as w
from mailmind.platform.db.txguard import TxAcrossNetworkError
from mailmind.bootstrap.curation import safe_http


class Write:
    """A connection with an uncommitted write, the transaction the guard looks for. end() rolls it
    back; the fixture ends it too, so a failing test leaves no transaction behind for the next."""

    def __init__(self):
        self.c = None

    def open(self):
        import store
        self.c = store.conn()
        self.c.execute("INSERT INTO sync_state(account, folder, uidvalidity, last_uid) "
                       "VALUES (?, 'Guarded', 1, 1)", (w.ADA,))

    def end(self):
        if self.c is not None:
            self.c.rollback()
            self.c.close()
            self.c = None


@pytest.fixture
def write():
    held = Write()
    yield held
    held.end()


def test_an_imap_connection_is_not_opened(world, write):
    import accounts
    before = len(world.server.commands)
    write.open()
    with pytest.raises(TxAcrossNetworkError, match="^an IMAP connection while"):
        accounts.connect(accounts.get(w.ADA))
    assert len(world.server.commands) == before
    write.end()
    accounts.connect(accounts.get(w.ADA)).logout()


def test_an_open_imap_connection_sends_no_command(world, write):
    import accounts
    M = accounts.connect(accounts.get(w.ADA))
    try:
        assert M.select('"INBOX"')[0] == "OK"
        before = len(world.server.commands)
        write.open()
        with pytest.raises(TxAcrossNetworkError, match="^IMAP NOOP while"):
            M.noop()
        with pytest.raises(TxAcrossNetworkError, match="^IMAP UID while"):
            M.uid("FETCH", "1", "(FLAGS)")
        assert len(world.server.commands) == before
        write.end()
        assert M.noop()[0] == "OK"
    finally:
        M.logout()


def test_no_smtp_session_is_opened(world, write):
    import accounts
    from mailmind.bootstrap.compose import transport
    before = len(world.smtp.commands)
    write.open()
    with pytest.raises(TxAcrossNetworkError, match="^an SMTP connection while"):
        transport.send(accounts.get(w.ADA), b"Subject: x\r\n\r\nx\r\n", ["lin@example.test"])
    assert len(world.smtp.commands) == before and world.smtp.messages == []
    write.end()


def test_an_open_smtp_session_sends_nothing(world, write):
    import accounts
    from mailmind.bootstrap.compose import transport
    s = transport.connect(accounts.get(w.ADA))
    try:
        before = len(world.smtp.commands)
        write.open()
        with pytest.raises(TxAcrossNetworkError, match="^SMTP while"):
            s.noop()
        assert len(world.smtp.commands) == before
        write.end()
        assert s.noop()[0] == 250
    finally:
        s.quit()


@pytest.mark.parametrize("transport", ["sdk", "urllib"])
def test_no_claude_api_request_is_made(world, ai, write, transport, monkeypatch):
    import summarize
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(summarize, "_last", {"ok": None, "ts": 0, "error": ""})   # status() stays as it was
    assistant.use(transport)
    try:
        write.open()
        with pytest.raises(TxAcrossNetworkError, match="^a Claude API request while"):
            summarize.call_claude("Say hello to Ada.")
        assert ai.requests == []
        write.end()
        assert summarize.call_claude("Say hello to Ada.")
        assert len(ai.requests) == 1
    finally:
        assistant.use("urllib")


def test_no_unsubscribe_request_is_made_not_even_its_dns_lookup(data_dir, write, monkeypatch):
    import socket
    looked_up = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: looked_up.append(a) or [])
    write.open()
    with pytest.raises(TxAcrossNetworkError, match="^an unsubscribe request while"):
        safe_http.one_click_post("https://loom.example.test/unsubscribe")
    assert looked_up == []
    write.end()
    assert safe_http.one_click_post("https://loom.example.test/unsubscribe") == (False, 0, "dns")
    assert looked_up
