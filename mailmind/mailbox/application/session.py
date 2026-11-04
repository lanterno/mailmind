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
from datetime import datetime
from typing import Protocol

from mailmind.mailbox.domain import roles
from mailmind.mailbox.domain.roles import Folder
from mailmind.platform import profile


@dataclass(frozen=True)
class Selected:
    """What selecting a folder said about it."""
    exists: int
    uidvalidity: int | None
    uidnext: int | None


@dataclass(frozen=True)
class Headers:
    """One message's header fields, as a header fetch got them."""
    uid: int
    flags: frozenset
    raw: bytes                          # the fields asked for, as the server sent them
    gmail_thread: str | None = None     # Gmail's X-GM-THRID, when asked for: fetch data, not a header
    internaldate: datetime | None = None   # when the server took it in (INTERNALDATE), when asked for


HEADER_BATCH = 300                      # UIDs to a header FETCH, so no command line grows too long


class MailSession(Protocol):
    capabilities: frozenset             # upper-case names, as CAPABILITY said after login

    def folders(self) -> list[Folder]: ...
    def status(self, folder: str) -> tuple[int, int]: ...                  # (unseen, messages)
    def select(self, folder: str, *, readonly: bool = True) -> Selected: ...
    def uids(self, *criteria: str) -> list[int]: ...                        # UID SEARCH, in order
    def by_message_id(self, message_id: str) -> int | None: ...             # the newest match
    def flags(self, uids) -> dict[int, frozenset]: ...
    def headers(self, uids, fields, *, gmail_thread: bool = False,
                internaldate: bool = False) -> list[Headers]: ...                  # in UID order
    def body(self, uid: int) -> bytes | None: ...                           # BODY.PEEK[]
    def add_flags(self, uids, flags) -> None: ...
    def remove_flags(self, uids, flags) -> None: ...
    def add_labels(self, uids, labels) -> None: ...                         # Gmail's X-GM-LABELS
    def remove_labels(self, uids, labels) -> None: ...
    def copy(self, uids, dest: str) -> None: ...
    def move(self, uids, dest: str) -> None: ...                            # one MOVE for them all
    def expunge(self, uids) -> None: ...                    # UID EXPUNGE of these alone, never a bulk one
    def append(self, folder: str, raw: bytes, *, flags=(), when=None) -> int | None: ...   # its UID
    def noop(self) -> bool: ...
    def idle(self, seconds: float) -> bool: ...     # IDLE on the selected folder: True once it changed
    def interrupt(self) -> None: ...                # from another thread: end an idle() now; the session is spent
    def logout(self) -> None: ...
    def shutdown(self) -> None: ...                 # close without a LOGOUT, when the server won't answer one


def move_by_copy(session, uids, dest):
    """MOVE on a server without it: COPY, checked, then \\Deleted and a UID EXPUNGE of each UID
    alone, never a bulk EXPUNGE. A refused COPY raises before an original is touched. A server
    without UIDPLUS refuses the UID EXPUNGE and leaves the original flagged \\Deleted, the way
    any client without UIDPLUS deletes; the next expunge, from any client, finishes it."""
    uids = [int(u) for u in uids]
    session.copy(uids, dest)
    session.add_flags(uids, ["\\Deleted"])
    for uid in uids:
        try:
            session.expunge([uid])
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            pass


def find_role(session, *wanted):
    """The folder on the session's server playing the first of `wanted` that one plays
    (mailmind.mailbox.domain.roles), or None: when none does, and when the server refuses the LIST,
    so the caller falls back to its default name."""
    try:
        folders = session.folders()
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return None
    return roles.find(folders, *wanted)


class ReadOnlySession:
    """A read-only process's session: every folder opens read-only, and a write raises
    profile.ReadOnlyError before it is sent. Everything else is the session's own."""

    _WRITES = frozenset({"add_flags", "remove_flags", "add_labels", "remove_labels", "copy", "move",
                         "expunge", "append"})

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
