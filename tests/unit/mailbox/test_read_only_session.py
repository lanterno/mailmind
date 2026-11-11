"""A read-only process's session (mailmind.mailbox.application.session.ReadOnlySession): every read reaches the server as
asked, every folder opens read-only, and every write is refused before anything is sent."""
from datetime import datetime, timezone

import pytest

from mailmind.mailbox.application.session import ReadOnlySession
from mailmind.platform.profile import ReadOnlyError


class _Recording:
    """A session that answers each call with its name and says what it was asked."""

    def __init__(self):
        self.heard = []

    @property
    def capabilities(self):
        self.heard.append(("capabilities",))
        return frozenset({"IMAP4REV1"})

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.heard.append((name, args, kwargs))
            return name
        return call


READS = [
    ("folders", (), {}),
    ("status", ("INBOX",), {}),
    ("uids", ("UNSEEN", "SINCE", "01-Nov-2026"), {}),
    ("by_message_id", ("<note-1@example.test>",), {}),
    ("flags", ([7, 8],), {}),
    ("enable_condstore", (), {}),
    ("uids_changed_since", (41,), {}),
    ("headers", ([7], ["Subject"]), {"gmail_thread": True, "gmail_labels": True, "internaldate": True}),
    ("body", (7,), {}),
    ("noop", (), {}),
    ("set_timeout", (12.0,), {}),
    ("idle", (30.0,), {}),
    ("interrupt", (), {}),
    ("logout", (), {}),
    ("shutdown", (), {}),
]

WRITES = [
    ("add_flags", ([7], ["\\Seen"]), {}),
    ("remove_flags", ([7], ["\\Seen"]), {}),
    ("add_labels", ([7], ["Work"]), {}),
    ("remove_labels", ([7], ["Work"]), {}),
    ("copy", ([7], "Archive"), {}),
    ("move", ([7], "Archive"), {}),
    ("expunge", ([7],), {}),
    ("append", ("Drafts", b"Subject: hi\r\n\r\nhi"), {"flags": ["\\Draft"], "when": datetime(2026, 11, 2, tzinfo=timezone.utc)}),
]


@pytest.mark.parametrize("name, args, kwargs", READS, ids=[r[0] for r in READS])
def test_a_read_goes_through_as_asked(name, args, kwargs):
    inner = _Recording()
    answer = getattr(ReadOnlySession(inner), name)(*args, **kwargs)
    assert inner.heard == [(name, args, kwargs)]
    assert answer == (None if name in ("set_timeout", "interrupt", "logout", "shutdown") else name)


def test_its_capabilities_are_the_servers():
    inner = _Recording()
    assert ReadOnlySession(inner).capabilities == frozenset({"IMAP4REV1"})
    assert inner.heard == [("capabilities",)]


@pytest.mark.parametrize("readonly", [True, False])
def test_every_folder_opens_read_only_whatever_is_asked(readonly):
    inner = _Recording()
    ReadOnlySession(inner).select("INBOX", readonly=readonly)
    assert inner.heard == [("select", ("INBOX",), {"readonly": True})]


@pytest.mark.parametrize("name, args, kwargs", WRITES, ids=[w[0] for w in WRITES])
def test_a_write_is_refused_and_nothing_is_sent(name, args, kwargs):
    inner = _Recording()
    with pytest.raises(ReadOnlyError, match=f"IMAP {name}"):
        getattr(ReadOnlySession(inner), name)(*args, **kwargs)
    assert inner.heard == []


def test_every_call_a_session_offers_is_one_or_the_other():
    """A call MailSession gains is a read or a write here, never one this session lets through unsaid."""
    from mailmind.mailbox.application.session import MailSession
    offered = {n for n in vars(MailSession) if not n.startswith("_")}
    assert offered == {r[0] for r in READS} | {w[0] for w in WRITES} | {"capabilities", "select"}
