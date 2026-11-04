"""Mailbox mutations for MailMind. Trash = move to the account's Trash folder
(reversible), then drop the local row. Usage: python3 mailops.py trash <pk>
"""
import sys
import os
import json
import socket
import ssl
import imaplib

sys.path.insert(0, os.path.dirname(__file__))
import accounts
import store
from mailmind.accounts.domain import provider
from mailmind.mailbox.adapters import imap_list
from mailmind.mailbox.domain import roles as folder_roles
from mailmind.platform import profile

# Connection-level failures = the op never reached/landed on the server → QUEUE + retry.
# imaplib.IMAP4.abort subclasses IMAP4.error; socket.timeout subclasses OSError. A NON-abort
# IMAP4.error is a command-level "NO/BAD" (folder gone, UID invalid, auth) = LOGICAL, not queued.
# A read-only process (profile.ReadOnlyError) queues its changes too, for a full run to apply.
_UNREACHABLE = (OSError, socket.timeout, socket.gaierror, ssl.SSLError, imaplib.IMAP4.abort,
                profile.ReadOnlyError)


def account_of(pk):
    """The account the message `pk` is in, or None when there's no such message."""
    c = store.conn()
    try:
        r = c.execute("SELECT account FROM messages WHERE pk=?", (pk,)).fetchone()
    finally:
        c.close()
    return r["account"] if r else None


def _do_imap(acct, fn):
    """Connect, run fn(M), logout. A connection failure raises (a member of _UNREACHABLE) so the
    caller can queue/retry; a clean command-level IMAP4.error from fn propagates as a logical error."""
    M = accounts.connect(acct)            # OSError/timeout/ssl/abort here → _UNREACHABLE propagates
    try:
        return fn(M)
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _uid_by_msgid(M, mid):
    """The live UID of a message in the currently-selected folder, by RFC822 Message-ID, or None.
    The Message-ID is sent as an IMAP quoted-string so embedded quotes/specials (some senders mint
    malformed IDs) can't break the SEARCH command."""
    if not mid:
        return None
    typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID", accounts.imap_quote(mid))
    uids = d[0].split() if (typ == "OK" and d and d[0]) else []
    return uids[-1].decode() if uids else None


def _already_in(M, folder, mid, back_to):
    """Whether `folder` already holds a message with Message-ID `mid`, with `back_to` selected
    again after. A queued move asks before it moves: on a server without MOVE, a connection that
    dropped after the COPY landed left the original behind, and moving it again would copy twice."""
    if not mid:
        return False
    try:
        typ, _ = M.select(accounts.mailbox_arg(folder), readonly=True)
        return typ == "OK" and bool(_uid_by_msgid(M, mid))
    finally:
        M.select(accounts.mailbox_arg(back_to), readonly=False)


def _imap_mutate(acct, fn):
    """Run a mutation's IMAP work via _do_imap. A connection-level failure (a _UNREACHABLE member,
    incl. IMAP4.abort) PROPAGATES so the route can queue it offline; a command-level IMAP4.error
    (NO/BAD: no such folder, over quota, no permission) returns (False, None). The server still
    holds the message where it was, so the caller keeps its local row and reports the failure.
    Returns (ran_ok, fn_result)."""
    profile.refuse_writes("an IMAP change")    # read-only: raised before any connection, so it queues
    try:
        return True, _do_imap(acct, fn)
    except imaplib.IMAP4.abort:
        raise                                  # connection aborted mid-op → propagate (queue)
    except imaplib.IMAP4.error:
        return False, None                     # command NO/BAD → logical, swallow as today


def _resolve_dest(M, op, acct):
    """Resolve a move-type op's destination folder on the live server (used by the drain worker
    when the offline enqueue couldn't resolve it). None when the account has no such folder."""
    if op == "trash":
        return accounts.trash_folder(acct)
    if op == "junk":
        j = find_role(M, "junk")
        return j or provider.resolve(acct).junk_fallback
    if op == "archive":
        return find_role(M, "archive", "all")
    return None


def apply_queued_mutation(row):
    """Worker entry for the offline read-mutation queue. Re-applies a queued mutation against the
    live server, resolving the message by Message-ID (NOT the stale pk/uid — reconcile may have
    re-numbered it). Returns (applied_ok, scrubbed_err). Raises a _UNREACHABLE member on connection
    failure (worker backs off + retries). A command-level IMAP4.error, or a message already moved/
    gone, counts as DONE (exactly-once — never retried forever). Used ONLY by readmutq.py."""
    acct = accounts.get(row["account"])
    op, src, dest = row["op"], row["src_folder"], row["dest"]
    mid = (row["message_id"] or "").strip()
    payload = json.loads(row["payload"] or "{}")

    def run(M):
        M.select(accounts.mailbox_arg(src), readonly=False)
        uid = _uid_by_msgid(M, mid) or (str(payload["uid"]) if payload.get("uid") else None)
        if not uid:
            return (True, None)                       # already gone → exactly-once done
        if op == "flag":
            for k, v in (payload.get("flags") or {}).items():
                if k in _FLAG:
                    M.uid("STORE", uid, "+FLAGS" if v else "-FLAGS", f"({_FLAG[k]})")
        elif op == "archive" and payload.get("is_gmail"):
            M.uid("STORE", uid, "-X-GM-LABELS", "(\\Inbox)")   # Gmail archive = drop the Inbox label
        else:                                          # trash / junk / move / non-Gmail archive
            d = dest or _resolve_dest(M, op, acct)     # resolve junk/archive folder online if not stored
            if not d:
                return (True, "no dest folder")        # no target → logical done (don't loop forever)
            if d != dest and row["id"]:                # persist the resolved dest so undo can find it later
                cc = store.conn()
                store.read_mut_set_dest(cc, row["id"], d)
                cc.commit()
                cc.close()
            if _already_in(M, d, mid, src):            # an earlier try's COPY landed, then its connection
                M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")   # dropped: only the original is left to remove
                expunge_uid(M, uid)
            else:
                _move_uid(M, uid, d)
        return (True, None)

    try:
        return _do_imap(acct, run)
    except _UNREACHABLE:
        raise                                          # connection-level → worker retries with backoff
    except imaplib.IMAP4.error as e:                   # command NO/BAD (folder/UID gone, auth) → logical, done
        return (True, type(e).__name__)


def _mk_move_undo(row, dest, label):
    """An undo descriptor for a move-type action; None when the message has no
    Message-ID (can't be reliably found to move back)."""
    mid = (row["message_id"] or "").strip()
    if not mid:
        return None
    return {"kind": "move", "account": row["account"], "src_folder": row["folder"],
            "dest_folder": dest, "message_id": mid, "label": label,
            "subject": row["subject"], "thread_id": row["thread_id"]}


def trash_message(pk):
    # read with a short-lived connection (don't hold it across the slow IMAP move)
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    if not row:
        return False, "not found", None
    acct = accounts.get(row["account"])
    dest = accounts.trash_folder(acct)
    marks_read = provider.resolve(acct).marks_trash_read

    def op(M):
        M.select(accounts.mailbox_arg(row["folder"]), readonly=False)
        unseen = marks_read and not _seen_on_server(M, str(row["uid"]), bool(row["seen"]))
        _move_uid(M, str(row["uid"]), dest)
        return unseen

    # _UNREACHABLE (IMAP down) propagates → the route queues this offline. A command the server
    # refuses leaves the message where it was, so the local row stays and the caller is told.
    ran, unseen = _imap_mutate(acct, op)
    if not ran:
        return False, "the server refused the move", None
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    undo = _mk_move_undo(row, dest, "Trashed")
    if undo and unseen:          # the server marked it read on the way to Trash: undo unmarks it
        undo["unseen"] = True
    return True, "trashed", undo


def _seen_on_server(M, uid, otherwise):
    """Whether the selected folder's message `uid` carries \\Seen, as the server says now; the
    local row's word (`otherwise`) when it won't say."""
    typ, d = M.uid("FETCH", uid, "(FLAGS)")
    if typ != "OK" or not d or not isinstance(d[0], (bytes, bytearray)):
        return otherwise
    return b"\\SEEN" in d[0].upper()


_FLAG = {"seen": "\\Seen", "flagged": "\\Flagged", "answered": "\\Answered",
         "draft": "\\Draft", "deleted": "\\Deleted"}


def set_flags(pk, flags):
    """flags: {'seen':True,'flagged':False,...} -> UID STORE +/-FLAGS, mirrored locally."""
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    if not row:
        return False, "not found"
    profile.refuse_writes("a flag change")
    acct = accounts.get(row["account"])
    M = accounts.connect(acct)
    try:
        M.select(accounts.mailbox_arg(row["folder"]), readonly=False)
        for k, v in flags.items():
            if k in _FLAG:
                M.uid("STORE", str(row["uid"]), "+FLAGS" if v else "-FLAGS", f"({_FLAG[k]})")
    finally:
        try:
            M.logout()
        except Exception:
            pass
    c = store.conn()
    store.set_local_flags(c, pk, **{k: int(bool(v)) for k, v in flags.items() if k in _FLAG})
    c.commit()
    c.close()
    return True, "ok"


def find_role(M, *roles):
    """The folder on M's server playing the first of `roles` that one plays, by its SPECIAL-USE
    attribute, then its name (mailmind.mailbox.domain.roles); None when none does, or when the
    server refuses the LIST, so the caller falls back to its default name."""
    try:
        folders = imap_list.list_folders(M)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return None
    return folder_roles.find(folders, *roles)


def expunge_uid(M, uid):
    """Expunge this one UID (UIDPLUS). Never a bulk EXPUNGE: that also erases every other message
    already flagged \\Deleted in the folder, and Proton Bridge breaks on it. A server without
    UIDPLUS leaves the message flagged \\Deleted, the way any client without UIDPLUS deletes;
    the next expunge, from any client, finishes it."""
    try:
        M.uid("EXPUNGE", uid)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass


def _move_uid(M, uid, dest, move=True):
    """Move one message: MOVE, or, when that fails or the caller knows the server has no MOVE
    (move=False), a checked COPY, then \\Deleted and a UID EXPUNGE of this UID alone."""
    try:
        if not move:
            raise imaplib.IMAP4.error("no MOVE")
        typ, d = M.uid("MOVE", uid, accounts.mailbox_arg(dest))
        if typ != "OK":
            raise imaplib.IMAP4.error(str(d))
    except imaplib.IMAP4.error:  # no MOVE: copy this one message, then remove only its UID
        typ, d = M.uid("COPY", uid, accounts.mailbox_arg(dest))
        if typ != "OK":          # nothing landed in dest, so the original stays untouched
            raise imaplib.IMAP4.error(f"COPY failed: {d}")
        M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        expunge_uid(M, uid)


def archive_message(pk):
    """Gmail: drop the \\Inbox label (stays in All Mail). Others: move to an
    Archive folder if one exists."""
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    if not row:
        return False, "not found", None
    acct = accounts.get(row["account"])

    def op(M):
        M.select(accounts.mailbox_arg(row["folder"]), readonly=False)
        uid = str(row["uid"])
        if provider.resolve(acct).archive_is_label_removal:
            typ, d = M.uid("STORE", uid, "-X-GM-LABELS", "(\\Inbox)")
            if typ != "OK":                       # imaplib returns a NO; it doesn't raise one
                raise imaplib.IMAP4.error(f"STORE failed: {d}")
            return ("gmail", None)
        arch = find_role(M, "archive", "all")
        if not arch:
            return ("nofolder", None)
        _move_uid(M, uid, arch)
        return ("moved", arch)

    ran, info = _imap_mutate(acct, op)            # _UNREACHABLE propagates → route queues
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
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    return True, "archived", undo


def junk_message(pk):
    """Move a message to the account's Junk/Spam folder (Gmail: [Gmail]/Spam)."""
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    if not row:
        return False, "not found", None
    acct = accounts.get(row["account"])

    def op(M):
        M.select(accounts.mailbox_arg(row["folder"]), readonly=False)
        junk = find_role(M, "junk")
        if not junk:
            junk = provider.resolve(acct).junk_fallback
        if not junk:
            return ("nofolder", None)
        _move_uid(M, str(row["uid"]), junk)
        return ("moved", junk)

    ran, info = _imap_mutate(acct, op)            # _UNREACHABLE propagates → route queues
    if not ran:                                   # refused: the message is still where it was
        return False, "the server refused the move", None
    if info[0] == "nofolder":
        return False, "no junk folder on this account", None
    junk = info[1]
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    return True, "junked", _mk_move_undo(row, junk, "Marked junk")


def move_message(pk, dest):
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    if not row or not dest:
        return False, "not found", None
    acct = accounts.get(row["account"])
    ran, _ = _imap_mutate(acct, lambda M: (M.select(accounts.mailbox_arg(row["folder"]), readonly=False),
                                           _move_uid(M, str(row["uid"]), dest)))
    if not ran:                                   # refused: the message is still where it was
        return False, "the server refused the move", None
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    return True, "moved", _mk_move_undo(row, dest, "Moved")


# ----------------------------------------------------------------- undo reverse ops
def move_by_msgid(account, search_folder, dest_folder, message_id, unseen=False):
    """Reverse primitive: find a message by EXACT RFC822 Message-ID in search_folder
    and move it to dest_folder. Fails (never guesses) if not found. unseen: take \\Seen off it
    there, as it was before a server that marks trashed mail read (Proton) set it."""
    acct = accounts.get(account)
    M = accounts.connect(acct)
    try:
        M.select(accounts.mailbox_arg(search_folder), readonly=False)
        typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID", accounts.imap_quote(message_id))
        uids = (d[0].split() if (typ == "OK" and d and d[0]) else [])
        if not uids:
            return False, "message not found"
        _move_uid(M, uids[-1].decode(), dest_folder)   # most recent copy if several
        if unseen:
            M.select(accounts.mailbox_arg(dest_folder), readonly=False)
            typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID", accounts.imap_quote(message_id))
            back = (d[0].split() if (typ == "OK" and d and d[0]) else [])
            if back:
                M.uid("STORE", back[-1].decode(), "-FLAGS", "(\\Seen)")
        return True, "moved back"
    finally:
        try:
            M.logout()
        except Exception:
            pass


def relabel_by_msgid(account, search_folder, message_id, label="\\Inbox"):
    """Gmail-archive reverse: re-add a label (default \\Inbox) to the message found by
    Message-ID in search_folder (All Mail)."""
    acct = accounts.get(account)
    M = accounts.connect(acct)
    try:
        M.select(accounts.mailbox_arg(search_folder), readonly=False)
        typ, d = M.uid("SEARCH", None, "HEADER", "Message-ID", accounts.imap_quote(message_id))
        uids = (d[0].split() if (typ == "OK" and d and d[0]) else [])
        if not uids:
            return False, "message not found"
        M.uid("STORE", uids[-1].decode(), "+X-GM-LABELS", f"({label})")
        return True, "relabeled"
    finally:
        try:
            M.logout()
        except Exception:
            pass


def find_special(account, *roles):
    """The folder playing the first of `roles` on an account's server (e.g. Gmail's All Mail)."""
    acct = accounts.get(account)
    M = accounts.connect(acct)
    try:
        return find_role(M, *roles)
    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "trash":
        print(trash_message(int(sys.argv[2])))
