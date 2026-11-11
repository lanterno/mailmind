"""One connection for one block: what a read block gives, what a write block holds and when it lets go."""

import pytest

from fixtures import world as w
from mailmind.platform.db.txguard import TxAcrossNetworkError
from mailmind.platform.db.uow import UnitOfWork


def test_an_imap_session_inside_a_write_block_raises_before_anything_is_sent(world):
    from mailmind.bootstrap import accounts, mailbox
    before = len(world.server.commands)
    with UnitOfWork().write():
        with pytest.raises(TxAcrossNetworkError, match="^an IMAP connection while"):
            mailbox.open_session(accounts.get(w.ADA))
    assert len(world.server.commands) == before


def test_an_smtp_session_inside_a_write_block_raises_before_anything_is_sent(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.compose import transport
    before = len(world.smtp.commands)
    with UnitOfWork().write():
        with pytest.raises(TxAcrossNetworkError, match="^an SMTP connection while"):
            transport.send(accounts.get(w.ADA), b"Subject: x\r\n\r\nx\r\n", ["lin@example.test"])
    assert len(world.smtp.commands) == before and world.smtp.messages == []


def test_a_claude_request_inside_a_write_block_raises_before_anything_is_sent(world, ai, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})   # report() stays as it was
    with UnitOfWork().write():
        with pytest.raises(TxAcrossNetworkError, match="^a Claude API request while"):
            assistant.calls.call_claude("Say hello to Ada.")
    assert ai.requests == []
