"""What MailMind asks of one logged-in connection to an account's mail server: the MailSession
port, with an adapter over imaplib (mailbox.adapters.imaplib_session, what the old door runs on)
and one over IMAPClient (mailbox.adapters.imapclient_session). tests/test_mail_session_contract.py
runs every case through each.

- A folder name is as LIST gave it, its quoted-string escapes in place: the form the database
  stores and accounts.mailbox_arg sends back. A UID is an int.
- A server that refuses a command raises imaplib.IMAP4.error, and a connection that's lost raises
  imaplib.IMAP4.abort or an OSError, through either adapter (IMAPClient's own errors are those
  classes), so every caller's catch, mailops._UNREACHABLE among them, holds.
- A read-only process's session (ReadOnlySession) opens every folder read-only and raises
  profile.ReadOnlyError on any write before it is sent.

Standard library only.
"""
import imaplib
from dataclasses import dataclass
from typing import Protocol

from mailmind.mailbox.domain.roles import Folder
from mailmind.platform import profile


@dataclass(frozen=True)
class Selected:
    """What selecting a folder said about it."""
    exists: int
    uidvalidity: int | None
    uidnext: int | None


class MailSession(Protocol):
    capabilities: frozenset             # upper-case names, as CAPABILITY said after login

    def folders(self) -> list[Folder]: ...
    def status(self, folder: str) -> tuple[int, int]: ...                  # (unseen, messages)
    def select(self, folder: str, *, readonly: bool = True) -> Selected: ...
    def uids(self, *criteria: str) -> list[int]: ...                        # UID SEARCH, in order
    def by_message_id(self, message_id: str) -> int | None: ...             # the newest match
    def flags(self, uids) -> dict[int, frozenset]: ...
    def body(self, uid: int) -> bytes | None: ...                           # BODY.PEEK[]
    def add_flags(self, uids, flags) -> None: ...
    def remove_flags(self, uids, flags) -> None: ...
    def copy(self, uid: int, dest: str) -> None: ...
    def move(self, uid: int, dest: str) -> None: ...
    def expunge_uid(self, uid: int) -> None: ...
    def append(self, folder: str, raw: bytes, *, flags=(), when=None) -> None: ...
    def noop(self) -> bool: ...
    def logout(self) -> None: ...


def move_by_copy(session, uid, dest):
    """MOVE on a server without it: COPY, checked, then \\Deleted and a UID EXPUNGE of this UID
    alone, never a bulk EXPUNGE. A refused COPY raises before the original is touched. A server
    without UIDPLUS refuses the UID EXPUNGE and leaves the original flagged \\Deleted, the way
    any client without UIDPLUS deletes; the next expunge, from any client, finishes it."""
    session.copy(uid, dest)
    session.add_flags([uid], ["\\Deleted"])
    try:
        session.expunge_uid(uid)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass


class ReadOnlySession:
    """A read-only process's session: every folder opens read-only, and a write raises
    profile.ReadOnlyError before it is sent. Everything else is the session's own."""

    _WRITES = frozenset({"add_flags", "remove_flags", "copy", "move", "expunge_uid", "append"})

    def __init__(self, session):
        self._session = session

    def select(self, folder, *, readonly=True):
        return self._session.select(folder, readonly=True)

    def __getattr__(self, name):
        if name in self._WRITES:
            def refused(*args, **kwargs):
                raise profile.ReadOnlyError(f"IMAP {name}")
            return refused
        return getattr(self._session, name)
