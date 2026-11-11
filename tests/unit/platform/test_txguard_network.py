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
        from mailmind.bootstrap.mailbox import store
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
