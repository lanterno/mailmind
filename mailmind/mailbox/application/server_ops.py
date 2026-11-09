"""What a change to the mail asks of the server: one command's try (`apply`) and the reversals. Trash is a move to the
account's Trash folder, so it can be reversed. A connection failure raises a member of UNREACHABLE; a server's NO or BAD
means the message didn't move."""
import imaplib
import json
import socket
import ssl
from collections.abc import Callable, Mapping
from typing import Any

from mailmind.kernel.row import Row
from mailmind.mailbox.application import session as mail_session
from mailmind.mailbox.application.ports import Account, Accounts, OpenSession, StoredMail
from mailmind.mailbox.application.session import MailSession
from mailmind.mailbox.domain import command, destination, move_strategy, try_target
from mailmind.mailbox.domain import flags as mail_flags
from mailmind.mailbox.domain.move_strategy import Way
from mailmind.platform import profile
from mailmind.platform.db.uow import UnitOfWork

# Connection-level failures = the op never reached/landed on the server → QUEUE + retry.
# imaplib.IMAP4.abort subclasses IMAP4.error; socket.timeout subclasses OSError. A NON-abort
# IMAP4.error is a command-level "NO/BAD" (folder gone, UID invalid, auth) = LOGICAL, not queued.
# A read-only process (profile.ReadOnlyError) queues its changes too, for a full run to apply.
UNREACHABLE = (OSError, socket.timeout, socket.gaierror, ssl.SSLError, imaplib.IMAP4.abort,
               profile.ReadOnlyError)


def _ignoring_refusal[*Ts](command: Callable[[*Ts], object], *args: *Ts) -> None:
    """Run a session command whose refusal changes nothing here, as when imaplib handed a NO back
    unraised; a lost connection still raises."""
    try:
        command(*args)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass


def _uid_by_msgid(s: MailSession, mid: str | None) -> int | None:
    """The live UID of a message in the session's selected folder, by RFC822 Message-ID, or None,
    also when the server refuses the search."""
    if not mid:
        return None
    try:
        return s.by_message_id(mid)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return None


def _seen_on_server(s: MailSession, uid: int, otherwise: bool) -> bool:
    """Whether the selected folder's message `uid` carries \\Seen, as the server says now; the
    local row's word (`otherwise`) when it won't say."""
    try:
        flags = s.flags([uid]).get(int(uid))
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return otherwise
    return otherwise if flags is None else mail_flags.has_seen(flags)


class ServerOps:
    UNREACHABLE = UNREACHABLE

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, open_session: OpenSession, quote: Callable[[str], str]):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._open_session = open_session
        self._quote = quote

    def account_of(self, pk: int | None) -> str | None:
        """The account the message `pk` is in, or None when there's no such message."""
        with self._uow.read() as c:
            return self._store.account_of(c, pk)


    def _do_imap[T](self, acct: Account, fn: Callable[[MailSession], T], timeout: int | None = None) -> T:
        """Open a session, run fn(session), log out; `timeout` is seconds for each step, the session's own when None. A
        connection failure raises (a member of UNREACHABLE) so the caller can queue/retry, and hangs up with no LOGOUT: a
        server that didn't answer the last command won't answer that one, and waiting to hear so is a second timeout. A
        clean command-level IMAP4.error from fn propagates as a logical error."""
        s = self._open_session(acct, timeout=timeout) if timeout else self._open_session(acct)   # a failure here propagates too
        lost = False
        try:
            return fn(s)
        except UNREACHABLE:
            lost = True
            raise
        finally:
            try:
                s.shutdown() if lost else s.logout()
            except Exception:
                pass


    def _already_in(self, s: MailSession, folder: str, mid: str, back_to: str) -> bool:
        """Whether `folder` already holds a message with Message-ID `mid`, with `back_to` selected
        again after. A queued move asks before it moves: on a server without MOVE, a connection that
        dropped after the COPY landed left the original behind, and moving it again would copy twice.
        A copy flagged \\Deleted doesn't count: a refused EXPUNGE left it, and the next expunge from any
        client takes it, so removing the original on its word would leave nothing."""
        if not mid:
            return False
        try:
            try:
                s.select(folder)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                return False
            try:
                return bool(s.uids("UNDELETED", "HEADER", "Message-ID", self._quote(mid)))
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                return False
        finally:
            s.select(back_to, readonly=False)


    def _target(self, s: MailSession, src: str, mid: str, params: Mapping[str, Any], first_try: bool) -> int | None:
        """Select `src` and name the UID a try acts on, or None when the message has left it, as domain.try_target says."""
        selected = s.select(src, readonly=False)
        target = try_target.of_try(uid=params.get("uid"), epoch=params.get("epoch"), uidvalidity=selected.uidvalidity,
                                   first_try=first_try, has_message_id=bool(mid))
        if target.by_message_id:
            try:
                return s.by_message_id(mid)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:              # it won't search: only the UID can say
                pass
        return target.uid


    def apply(self, row: Row, first_try: bool = False, timeout: int | None = None) -> command.ServerAnswer:
        """One try at the command `row`, on the account's gate: a ServerAnswer. Raises a UNREACHABLE member when the
        connection fails. Unless this is the first try, an earlier one may have got part of the way, so it asks the
        server before it repeats anything: exactly once, however many tries it takes. `timeout` is how long each step
        waits for the server, when someone is waiting on the try."""
        profile.refuse_writes("an IMAP change")      # read-only: raised before any connection, so it queues
        acct = self._accounts.get(row["account"])
        kind, src = row["kind"], row["src_folder"]
        mid = (row["message_id"] or "").strip()
        params = json.loads(row["params"] or "{}")
        server = self._accounts.profile(acct)

        def run(s: MailSession) -> command.ServerAnswer:
            uid = self._target(s, src, mid, params, first_try)
            if not uid:
                return command.ServerAnswer()        # already gone: done, and never retried
            if kind == "flag":                       # a refusal raises IMAP4.error: refused, below
                flags: dict[str, Any] = params.get("flags") or {}
                for k, v in flags.items():
                    if k in mail_flags.IMAP_NAME:
                        (s.add_flags if v else s.remove_flags)([uid], [mail_flags.IMAP_NAME[k]])
                return command.ServerAnswer()
            trash = self._accounts.trash_folder(acct)
            where = destination.resolve(kind, named=row["dest"], archive_is_label_removal=server.archive_is_label_removal,
                                        trash=trash, junk_fallback=server.junk_fallback,
                                        find=lambda *roles: mail_session.find_role(s, *roles))
            strategy = move_strategy.choose(kind, where, capabilities=s.capabilities, marks_trash_read=server.marks_trash_read,
                                            trash=trash)
            if strategy.way is Way.LABELS:
                s.remove_labels([uid], ["\\Inbox"])
                return command.ServerAnswer()
            if not where.folder:
                return command.ServerAnswer(won_t=command.NO_FOLDER)
            unseen = strategy.read_seen_first and not _seen_on_server(s, uid, bool(params.get("seen", True)))
            if not first_try and self._already_in(s, where.folder, mid, src):
                s.add_flags([uid], ["\\Deleted"])    # an earlier try's COPY landed, then its connection
                s.expunge([uid])                     # dropped: only the original is left to remove
            elif strategy.way is Way.COPY:
                mail_session.move_by_copy(s, [uid], where.folder)
            else:
                s.move([uid], where.folder)
            return command.ServerAnswer(dest=where.folder, unseen=unseen)

        try:
            return self._do_imap(acct, run, timeout)
        except UNREACHABLE:
            raise                                    # connection-level: the caller queues or backs off
        except imaplib.IMAP4.error:                  # NO or BAD: it didn't happen, and won't
            return command.ServerAnswer(won_t=command.REFUSED)


    def mark_answered(self, pk: int | None, account: str | None, message_id: str | None) -> tuple[bool, str]:
        """\\Answered on the message a send replied to, wherever it is by now: where its row says, or
        with no row left (archiving right after replying deletes it), in the inbox and the folders filing
        moves mail to. A custom folder it was moved to isn't looked through. Answers as set_flags does."""
        with self._uow.read() as c:
            row = self._store.get_message_by_msgid(c, account, message_id) or (self._store.get_message(c, pk) if pk else None)
        if row:
            return self.set_flags(row["pk"], {"answered": True})
        if not (account and message_id):
            return False, "not found"
        profile.refuse_writes("a flag change")
        s = self._open_session(self._accounts.get(account))
        try:
            for role in ("inbox", "archive", "all", "trash", "junk"):
                folder = mail_session.find_role(s, role)
                if not folder:
                    continue
                try:
                    s.select(folder, readonly=False)
                    uid = _uid_by_msgid(s, message_id)
                    if uid:
                        s.add_flags([uid], ["\\Answered"])
                        return True, "ok"
                except imaplib.IMAP4.abort:
                    raise
                except imaplib.IMAP4.error:
                    continue
            return False, "not found"
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def set_flags(self, pk: int, flags: Mapping[str, bool]) -> tuple[bool, str]:
        """flags: {'answered':True} -> UID STORE +/-FLAGS, mirrored locally once the server took it. What a send sets
        off retries for itself, so this goes straight to the server; what a person asks goes through MailCommands. A
        refusal (the UID gone, a read-only folder) leaves the local row as the server has it, and answers (False, why)."""
        with self._uow.read() as c:
            row = self._store.get_message(c, pk)
        if not row:
            return False, "not found"
        profile.refuse_writes("a flag change")
        acct = self._accounts.get(row["account"])
        s = self._open_session(acct)
        try:
            s.select(row["folder"], readonly=False)
            for k, v in flags.items():
                if k in mail_flags.IMAP_NAME:
                    (s.add_flags if v else s.remove_flags)([row["uid"]], [mail_flags.IMAP_NAME[k]])
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            return False, "the server refused the flag change"
        finally:
            try:
                s.logout()
            except Exception:
                pass
        with self._uow.write() as c:
            self._store.set_local_flags(c, pk, **mail_flags.as_columns(flags))
        return True, "ok"


    # ----------------------------------------------------------------- undo reverse ops
    def move_by_msgid(self, account: str, search_folder: str, dest_folder: str, message_id: str,
                      unseen: bool = False) -> tuple[bool, str]:
        """Reverse primitive: find a message by EXACT RFC822 Message-ID in search_folder
        and move it to dest_folder. Fails (never guesses) if not found. unseen: take \\Seen off it
        there, as it was before a server that marks trashed mail read (Proton) set it."""
        acct = self._accounts.get(account)
        s = self._open_session(acct)
        try:
            s.select(search_folder, readonly=False)
            uid = _uid_by_msgid(s, message_id)              # the most recent copy if several
            if uid is None:
                return False, "message not found"
            try:
                s.move([uid], dest_folder)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:                     # a NO or BAD: it stays where it is
                return False, "the server refused the move"
            if unseen:
                s.select(dest_folder, readonly=False)
                back = _uid_by_msgid(s, message_id)
                if back is not None:
                    _ignoring_refusal(s.remove_flags, [back], ["\\Seen"])
            return True, "moved back"
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def relabel_by_msgid(self, account: str, search_folder: str, message_id: str, label: str = "\\Inbox") -> tuple[bool, str]:
        """Gmail-archive reverse: re-add a label (default \\Inbox) to the message found by
        Message-ID in search_folder (All Mail). A refusal answers (False, why): the label isn't there."""
        acct = self._accounts.get(account)
        s = self._open_session(acct)
        try:
            s.select(search_folder, readonly=False)
            uid = _uid_by_msgid(s, message_id)
            if uid is None:
                return False, "message not found"
            try:
                s.add_labels([uid], [label])
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                return False, "the server refused the label"
            return True, "relabeled"
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def find_special(self, account: str, *roles: str) -> str | None:
        """The folder playing the first of `roles` on an account's server (e.g. Gmail's All Mail)."""
        return self._do_imap(self._accounts.get(account), lambda s: mail_session.find_role(s, *roles))
