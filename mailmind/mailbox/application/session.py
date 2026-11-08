"""What MailMind asks of one logged-in connection to an account's mail server: the MailSession
port, with an adapter over imaplib (mailbox.adapters.imap.imaplib_session, the default)
and one over IMAPClient (mailbox.adapters.imap.imapclient_session). tests/contract/test_mail_session_contract.py
runs every case through each.

- A folder name is as LIST gave it, its quoted-string escapes in place: the form the database
  stores and accounts.mailbox_arg sends back. A UID is an int.
- A server that refuses a command raises imaplib.IMAP4.error, and a connection that's lost raises
  imaplib.IMAP4.abort or an OSError, through either adapter (IMAPClient's own errors are those
  classes), so every caller's catch, commands.UNREACHABLE among them, holds.
- A read-only process's session (ReadOnlySession) opens every folder read-only and raises
  profile.ReadOnlyError on any write before it is sent.

Standard library only.
"""
import imaplib
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn, Protocol

from mailmind.mailbox.domain import roles
from mailmind.mailbox.domain.roles import Folder
from mailmind.platform import profile


@dataclass(frozen=True)
class Selected:
    """What selecting a folder said about it."""
    exists: int
    uidvalidity: int | None
    uidnext: int | None
    highest_modseq: int | None = None   # once enable_condstore said yes, if the server keeps one for the folder


@dataclass(frozen=True)
class Headers:
    """One message's header fields, as a header fetch got them."""
    uid: int
    flags: frozenset[str]
    raw: bytes                          # the fields asked for, as the server sent them
    gmail_thread: str | None = None     # Gmail's X-GM-THRID, when asked for: fetch data, not a header
    internaldate: datetime | None = None   # when the server took it in (INTERNALDATE), when asked for
    gmail_labels: frozenset[str] | None = None   # Gmail's X-GM-LABELS when asked for: fetch data, a system one as \\Inbox


HEADER_BATCH = 300                      # UIDs to a header FETCH, so no command line grows too long

type Uids = Collection[int | str]       # the digits of a UID as well as the int: a sweep carries them as read


class MailSession(Protocol):
    @property
    def capabilities(self) -> frozenset[str]: ...    # upper-case names, as CAPABILITY said after login

    def folders(self) -> list[Folder]: ...
    def status(self, folder: str) -> tuple[int, int]: ...                  # (unseen, messages)
    def select(self, folder: str, *, readonly: bool = True) -> Selected: ...
    def uids(self, *criteria: str) -> list[int]: ...                        # UID SEARCH, in order
    def by_message_id(self, message_id: str) -> int | None: ...             # the newest match
    def flags(self, uids: Uids) -> dict[int, frozenset[str]]: ...
    def enable_condstore(self) -> bool: ...                     # before any select; False: it won't
    def uids_changed_since(self, modseq: int) -> list[int]: ...  # after enable_condstore
    def headers(self, uids: Uids, fields: Iterable[str], *, gmail_thread: bool = False, gmail_labels: bool = False,
                internaldate: bool = False) -> list[Headers]: ...                  # in UID order
    def body(self, uid: int) -> bytes | None: ...                           # BODY.PEEK[]
    def add_flags(self, uids: Uids, flags: Iterable[str]) -> None: ...
    def remove_flags(self, uids: Uids, flags: Iterable[str]) -> None: ...
    def add_labels(self, uids: Uids, labels: Iterable[str]) -> None: ...    # Gmail's X-GM-LABELS
    def remove_labels(self, uids: Uids, labels: Iterable[str]) -> None: ...
    def copy(self, uids: Uids, dest: str) -> None: ...
    def move(self, uids: Uids, dest: str) -> None: ...                      # one MOVE for them all
    def expunge(self, uids: Uids) -> None: ...              # UID EXPUNGE of these alone, never a bulk one
    def append(self, folder: str, raw: bytes, *, flags: Collection[str] = (),
               when: datetime | None = None) -> int | None: ...             # its UID
    def noop(self) -> bool: ...
    def set_timeout(self, seconds: float) -> None: ...   # how long any one answer may take from now on; sends nothing
    def idle(self, seconds: float) -> bool: ...     # IDLE on the selected folder: True once it changed
    def interrupt(self) -> None: ...                # from another thread: end an idle() now; the session is spent
    def logout(self) -> None: ...
    def shutdown(self) -> None: ...                 # close without a LOGOUT, when the server won't answer one


def move_by_copy(session: MailSession, uids: Uids, dest: str) -> None:
    """MOVE on a server without it: COPY, checked, then \\Deleted and a UID EXPUNGE of each UID
    alone, never a bulk EXPUNGE. A refused COPY raises before an original is touched. A server
    without UIDPLUS refuses the UID EXPUNGE and leaves the original flagged \\Deleted, the way
    any client without UIDPLUS deletes; the next expunge, from any client, finishes it."""
    numbers = [int(u) for u in uids]
    session.copy(numbers, dest)
    session.add_flags(numbers, ["\\Deleted"])
    for uid in numbers:
        try:
            session.expunge([uid])
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            pass


def find_role(session: MailSession, *wanted: str) -> str | None:
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

    def __init__(self, session: MailSession):
        self._session = session

    @property
    def capabilities(self) -> frozenset[str]:
        return self._session.capabilities

    def select(self, folder: str, *, readonly: bool = True) -> Selected:
        return self._session.select(folder, readonly=True)

    def folders(self) -> list[Folder]:
        return self._session.folders()

    def status(self, folder: str) -> tuple[int, int]:
        return self._session.status(folder)

    def uids(self, *criteria: str) -> list[int]:
        return self._session.uids(*criteria)

    def by_message_id(self, message_id: str) -> int | None:
        return self._session.by_message_id(message_id)

    def flags(self, uids: Uids) -> dict[int, frozenset[str]]:
        return self._session.flags(uids)

    def enable_condstore(self) -> bool:
        return self._session.enable_condstore()

    def uids_changed_since(self, modseq: int) -> list[int]:
        return self._session.uids_changed_since(modseq)

    def headers(self, uids: Uids, fields: Iterable[str], *, gmail_thread: bool = False, gmail_labels: bool = False,
                internaldate: bool = False) -> list[Headers]:
        return self._session.headers(uids, fields, gmail_thread=gmail_thread, gmail_labels=gmail_labels, internaldate=internaldate)

    def body(self, uid: int) -> bytes | None:
        return self._session.body(uid)

    def noop(self) -> bool:
        return self._session.noop()

    def set_timeout(self, seconds: float) -> None:
        self._session.set_timeout(seconds)

    def idle(self, seconds: float) -> bool:
        return self._session.idle(seconds)

    def interrupt(self) -> None:
        self._session.interrupt()

    def logout(self) -> None:
        self._session.logout()

    def shutdown(self) -> None:
        self._session.shutdown()

    def add_flags(self, uids: Uids, flags: Iterable[str]) -> NoReturn:
        raise profile.ReadOnlyError("IMAP add_flags")

    def remove_flags(self, uids: Uids, flags: Iterable[str]) -> NoReturn:
        raise profile.ReadOnlyError("IMAP remove_flags")

    def add_labels(self, uids: Uids, labels: Iterable[str]) -> NoReturn:
        raise profile.ReadOnlyError("IMAP add_labels")

    def remove_labels(self, uids: Uids, labels: Iterable[str]) -> NoReturn:
        raise profile.ReadOnlyError("IMAP remove_labels")

    def copy(self, uids: Uids, dest: str) -> NoReturn:
        raise profile.ReadOnlyError("IMAP copy")

    def move(self, uids: Uids, dest: str) -> NoReturn:
        raise profile.ReadOnlyError("IMAP move")

    def expunge(self, uids: Uids) -> NoReturn:
        raise profile.ReadOnlyError("IMAP expunge")

    def append(self, folder: str, raw: bytes, *, flags: Collection[str] = (), when: datetime | None = None) -> NoReturn:
        raise profile.ReadOnlyError("IMAP append")
