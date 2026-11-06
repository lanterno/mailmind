"""Changes to the mail on a server. Trash is a move to the account's Trash folder (reversible), and then the
local row goes. A connection failure raises a member of UNREACHABLE, which the route turns into a row of the offline
queue; a server's NO or BAD means the message didn't move."""
import imaplib
import json
import socket
import ssl

from mailmind.mailbox.application import session as mail_session
from mailmind.mailbox.application.ports import Accounts, StoredMail
from mailmind.platform import profile

# Connection-level failures = the op never reached/landed on the server → QUEUE + retry.
# imaplib.IMAP4.abort subclasses IMAP4.error; socket.timeout subclasses OSError. A NON-abort
# IMAP4.error is a command-level "NO/BAD" (folder gone, UID invalid, auth) = LOGICAL, not queued.
# A read-only process (profile.ReadOnlyError) queues its changes too, for a full run to apply.
UNREACHABLE = (OSError, socket.timeout, socket.gaierror, ssl.SSLError, imaplib.IMAP4.abort,
               profile.ReadOnlyError)

_FLAG = {"seen": "\\Seen", "flagged": "\\Flagged", "answered": "\\Answered",
         "draft": "\\Draft", "deleted": "\\Deleted"}


def _ignoring_refusal(command, *args):
    """Run a session command whose refusal changes nothing here, as when imaplib handed a NO back
    unraised; a lost connection still raises."""
    try:
        command(*args)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass


def _uid_by_msgid(s, mid):
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


def _mk_move_undo(row, dest, label):
    """An undo descriptor for a move-type action; None when the message has no
    Message-ID (can't be reliably found to move back)."""
    mid = (row["message_id"] or "").strip()
    if not mid:
        return None
    return {"kind": "move", "account": row["account"], "src_folder": row["folder"],
            "dest_folder": dest, "message_id": mid, "label": label,
            "subject": row["subject"], "thread_id": row["thread_id"]}


def _seen_on_server(s, uid, otherwise):
    """Whether the selected folder's message `uid` carries \\Seen, as the server says now; the
    local row's word (`otherwise`) when it won't say."""
    try:
        flags = s.flags([uid]).get(int(uid))
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return otherwise
    return otherwise if flags is None else "\\seen" in {f.lower() for f in flags}


class MailCommands:
    UNREACHABLE = UNREACHABLE

    def __init__(self, store: StoredMail, accounts: Accounts, open_session, quote):
        self._store = store
        self._accounts = accounts
        self._open_session = open_session
        self._quote = quote

    def account_of(self, pk):
        """The account the message `pk` is in, or None when there's no such message."""
        c = self._store.conn()
        try:
            r = c.execute("SELECT account FROM messages WHERE pk=?", (pk,)).fetchone()
        finally:
            c.close()
        return r["account"] if r else None


    def _do_imap(self, acct, fn):
        """Open a session, run fn(session), log out. A connection failure raises (a member of
        UNREACHABLE) so the caller can queue/retry; a clean command-level IMAP4.error from fn propagates
        as a logical error."""
        s = self._open_session(acct)        # OSError/timeout/ssl/abort here → UNREACHABLE propagates
        try:
            return fn(s)
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def _already_in(self, s, folder, mid, back_to):
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


    def _imap_mutate(self, acct, fn):
        """Run a mutation's IMAP work via _do_imap. A connection-level failure (a UNREACHABLE member,
        incl. IMAP4.abort) PROPAGATES so the route can queue it offline; a command-level IMAP4.error
        (NO/BAD: no such folder, over quota, no permission) returns (False, None). The server still
        holds the message where it was, so the caller keeps its local row and reports the failure.
        Returns (ran_ok, fn_result)."""
        profile.refuse_writes("an IMAP change")    # read-only: raised before any connection, so it queues
        try:
            return True, self._do_imap(acct, fn)
        except imaplib.IMAP4.abort:
            raise                                  # connection aborted mid-op → propagate (queue)
        except imaplib.IMAP4.error:
            return False, None                     # command NO/BAD → logical, swallow as today


    def _resolve_dest(self, s, op, acct):
        """Resolve a move-type op's destination folder on the live server (used by the drain worker
        when the offline enqueue couldn't resolve it). None when the account has no such folder."""
        if op == "trash":
            return self._accounts.trash_folder(acct)
        if op == "junk":
            j = mail_session.find_role(s, "junk")
            return j or self._accounts.profile(acct).junk_fallback
        if op == "archive":
            return mail_session.find_role(s, "archive", "all")
        return None


    def apply_queued_mutation(self, row):
        """Worker entry for the offline read-mutation queue. Re-applies a queued mutation against the
        live server, resolving the message by Message-ID (NOT the stale pk/uid — reconcile may have
        re-numbered it). Returns None once the server holds the change, or when the message had
        already left its folder (exactly-once: never retried forever). Returns a fixed reason when the
        change didn't happen and no retry will make it: "refused" (the server answered NO or BAD) or
        "no_folder" (the account has no folder to move it to); the message is still where it was.
        Raises a UNREACHABLE member on connection failure (worker backs off + retries). Used ONLY by
        the offline queue."""
        acct = self._accounts.get(row["account"])
        op, src, dest = row["op"], row["src_folder"], row["dest"]
        mid = (row["message_id"] or "").strip()
        payload = json.loads(row["payload"] or "{}")

        def run(s):
            s.select(src, readonly=False)
            uid = _uid_by_msgid(s, mid) or (int(payload["uid"]) if payload.get("uid") else None)
            if not uid:
                return None                               # already gone → exactly-once done
            if op == "flag":                              # a refusal raises IMAP4.error: refused, below
                for k, v in (payload.get("flags") or {}).items():
                    if k in _FLAG:
                        (s.add_flags if v else s.remove_flags)([uid], [_FLAG[k]])
            elif op == "archive" and payload.get("is_gmail"):
                s.remove_labels([uid], ["\\Inbox"])       # Gmail archive = drop the Inbox label
            else:                                          # trash / junk / move / non-Gmail archive
                d = dest or self._resolve_dest(s, op, acct)     # resolve junk/archive folder online if not stored
                if not d:
                    return "no_folder"                     # no target: it stays (don't loop forever)
                if d != dest and row["id"]:                # persist the resolved dest so undo can find it later
                    cc = self._store.conn()
                    self._store.read_mut_set_dest(cc, row["id"], d)
                    cc.commit()
                    cc.close()
                if self._already_in(s, d, mid, src):            # an earlier try's COPY landed, then its connection
                    s.add_flags([uid], ["\\Deleted"])        # dropped: only the original is left
                    s.expunge([uid])                       # to remove
                else:
                    s.move([uid], d)
            return None

        try:
            return self._do_imap(acct, run)
        except UNREACHABLE:
            raise                                          # connection-level → worker retries with backoff
        except imaplib.IMAP4.error:                        # command NO/BAD: it didn't happen, and won't
            return "refused"


    def trash_message(self, pk):
        # read with a short-lived connection (don't hold it across the slow IMAP move)
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        c.close()
        if not row:
            return False, "not found", None
        acct = self._accounts.get(row["account"])
        dest = self._accounts.trash_folder(acct)
        marks_read = self._accounts.profile(acct).marks_trash_read

        def op(s):
            s.select(row["folder"], readonly=False)
            unseen = marks_read and not _seen_on_server(s, row["uid"], bool(row["seen"]))
            s.move([row["uid"]], dest)
            return unseen

        # UNREACHABLE (IMAP down) propagates → the route queues this offline. A command the server
        # refuses leaves the message where it was, so the local row stays and the caller is told.
        ran, unseen = self._imap_mutate(acct, op)
        if not ran:
            return False, "the server refused the move", None
        c = self._store.conn()
        self._store.delete_message(c, pk)
        c.commit()
        c.close()
        undo = _mk_move_undo(row, dest, "Trashed")
        if undo and unseen:          # the server marked it read on the way to Trash: undo unmarks it
            undo["unseen"] = True
        return True, "trashed", undo


    def mark_answered(self, pk, account, message_id):
        """\\Answered on the message a send replied to, wherever it is by now: where its row says, or
        with no row left (archiving right after replying deletes it), in the inbox and the folders filing
        moves mail to. A custom folder it was moved to isn't looked through. Answers as set_flags does."""
        c = self._store.conn()
        try:
            row = self._store.get_message_by_msgid(c, account, message_id) or (self._store.get_message(c, pk) if pk else None)
        finally:
            c.close()
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


    def set_flags(self, pk, flags):
        """flags: {'seen':True,'flagged':False,...} -> UID STORE +/-FLAGS, mirrored locally once the
        server took it. A refusal (the UID gone, a read-only folder) leaves the local row as the server
        has it, and answers (False, why)."""
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        c.close()
        if not row:
            return False, "not found"
        profile.refuse_writes("a flag change")
        acct = self._accounts.get(row["account"])
        s = self._open_session(acct)
        try:
            s.select(row["folder"], readonly=False)
            for k, v in flags.items():
                if k in _FLAG:
                    (s.add_flags if v else s.remove_flags)([row["uid"]], [_FLAG[k]])
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            return False, "the server refused the flag change"
        finally:
            try:
                s.logout()
            except Exception:
                pass
        c = self._store.conn()
        self._store.set_local_flags(c, pk, **{k: int(bool(v)) for k, v in flags.items() if k in _FLAG})
        c.commit()
        c.close()
        return True, "ok"


    def archive_message(self, pk):
        """Gmail: drop the \\Inbox label (stays in All Mail). Others: move to an
        Archive folder if one exists."""
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        c.close()
        if not row:
            return False, "not found", None
        acct = self._accounts.get(row["account"])

        def op(s):
            s.select(row["folder"], readonly=False)
            if self._accounts.profile(acct).archive_is_label_removal:
                s.remove_labels([row["uid"]], ["\\Inbox"])     # refused: raises, and the message stays
                return ("gmail", None)
            arch = mail_session.find_role(s, "archive", "all")
            if not arch:
                return ("nofolder", None)
            s.move([row["uid"]], arch)
            return ("moved", arch)

        ran, info = self._imap_mutate(acct, op)            # UNREACHABLE propagates → route queues
        if not ran:                                   # refused: the message is still where it was
            return False, "the server refused the archive", None
        if info[0] == "nofolder":
            return False, "no archive folder on this account", None
        undo = None
        if info[0] == "gmail":
            mid = (row["message_id"] or "").strip()
            if mid:   # reverse = re-add the \Inbox label, found by Message-ID in All Mail
                undo = {"kind": "gmail_archive", "account": row["account"], "src_folder": "INBOX",
                        "message_id": mid, "label": "Archived",
                        "subject": row["subject"], "thread_id": row["thread_id"]}
        elif info[0] == "moved":
            undo = _mk_move_undo(row, info[1], "Archived")
        c = self._store.conn()
        self._store.delete_message(c, pk)
        c.commit()
        c.close()
        return True, "archived", undo


    def junk_message(self, pk):
        """Move a message to the account's Junk/Spam folder (Gmail: [Gmail]/Spam)."""
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        c.close()
        if not row:
            return False, "not found", None
        acct = self._accounts.get(row["account"])

        def op(s):
            s.select(row["folder"], readonly=False)
            junk = mail_session.find_role(s, "junk")
            if not junk:
                junk = self._accounts.profile(acct).junk_fallback
            if not junk:
                return ("nofolder", None)
            s.move([row["uid"]], junk)
            return ("moved", junk)

        ran, info = self._imap_mutate(acct, op)            # UNREACHABLE propagates → route queues
        if not ran:                                   # refused: the message is still where it was
            return False, "the server refused the move", None
        if info[0] == "nofolder":
            return False, "no junk folder on this account", None
        junk = info[1]
        c = self._store.conn()
        self._store.delete_message(c, pk)
        c.commit()
        c.close()
        return True, "junked", _mk_move_undo(row, junk, "Marked junk")


    def move_message(self, pk, dest):
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        c.close()
        if not row or not dest:
            return False, "not found", None
        acct = self._accounts.get(row["account"])
        ran, _ = self._imap_mutate(acct, lambda s: (s.select(row["folder"], readonly=False), s.move([row["uid"]], dest)))
        if not ran:                                   # refused: the message is still where it was
            return False, "the server refused the move", None
        c = self._store.conn()
        self._store.delete_message(c, pk)
        c.commit()
        c.close()
        return True, "moved", _mk_move_undo(row, dest, "Moved")


    # ----------------------------------------------------------------- undo reverse ops
    def move_by_msgid(self, account, search_folder, dest_folder, message_id, unseen=False):
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


    def relabel_by_msgid(self, account, search_folder, message_id, label="\\Inbox"):
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


    def find_special(self, account, *roles):
        """The folder playing the first of `roles` on an account's server (e.g. Gmail's All Mail)."""
        return self._do_imap(self._accounts.get(account), lambda s: mail_session.find_role(s, *roles))
