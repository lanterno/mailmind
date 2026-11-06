"""Copies Compose keeps on the server: a sent message in Sent, a draft in Drafts. Each opens a session
of its own and closes it. Standard library only."""
from datetime import datetime
import imaplib

from mailmind.mailbox.application.session import find_role


class Copies:
    def __init__(self, open_session, quote):
        self._open_session = open_session
        self._quote = quote

    def append_to_sent(self, acct, raw, message_id=None):
        """IMAP APPEND the exact sent bytes to the account's Sent folder, flagged \\Seen.
        Returns the resolved folder name. Raises if no Sent folder or the APPEND fails.
        Given `message_id`, a copy already filed under it is kept and nothing is appended: a retry
        can't tell whether the last try's APPEND landed before its connection went. One flagged
        \\Deleted is as good as gone, so it doesn't count."""
        s = self._open_session(acct)
        try:
            sent = find_role(s, "sent") or "Sent"
            if message_id:
                s.select(sent)
                if s.uids("UNDELETED", "HEADER", "Message-ID", self._quote(message_id)):
                    return sent
            s.append(sent, raw, flags=["\\Seen"], when=datetime.now().astimezone())
            return sent
        finally:
            try:
                s.logout()
            except Exception:
                pass

    def append_to_drafts(self, acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None, prev_folder=None):
        """APPEND a draft (flagged \\Draft) to the account's Drafts folder, then delete the previous
        copy (single-UID expunge — NEVER a bulk expunge). Returns
        (folder, new_uid|None, uidvalidity|None, removed_uid|None). Append first, so a save that fails or
        a crash never leaves none: at worst the old copy stays beside the new one. prev_folder: where the
        previous copy is; when that isn't this Drafts folder, nothing is deleted here (its UID means
        nothing in this one), and the caller removes it there."""
        s = self._open_session(acct)
        try:
            drafts = find_role(s, "drafts") or "Drafts"
            if prev_folder is not None and prev_folder != drafts:
                prev_uid = prev_msgid = None
            try:
                uidv = s.select(drafts, readonly=False).uidvalidity
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:          # refused: no copy is deleted, but the APPEND still goes
                uidv = None
            # the prior draft copy, if we can safely identify it: asked before the new copy can answer too
            del_uid = removed = None
            if prev_uid and (prev_uidvalidity is None or prev_uidvalidity == uidv):
                del_uid = int(prev_uid)
            elif prev_msgid:
                try:
                    ids = s.uids("HEADER", "Message-ID", self._quote(prev_msgid))
                    del_uid = ids[0] if ids else None
                except Exception:
                    del_uid = None
            new_uid = s.append(drafts, raw, flags=["\\Draft"], when=datetime.now().astimezone())
            if del_uid:
                try:
                    s.add_flags([del_uid], ["\\Deleted"])
                    s.expunge([del_uid])         # this UID alone, never a bulk EXPUNGE
                    removed = del_uid
                except Exception:
                    pass
            return drafts, new_uid, uidv, removed
        finally:
            try:
                s.logout()
            except Exception:
                pass

    def delete_draft_uid(self, acct, folder, uid, uidvalidity=None):
        """Remove one draft message by UID (single-UID expunge). Best-effort: True if it went."""
        if not uid:
            return False
        s = self._open_session(acct)
        try:
            try:
                cur = s.select(folder or "Drafts", readonly=False).uidvalidity
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                return False                               # refused: nothing to delete from
            if uidvalidity is not None and uidvalidity != cur:
                return False                               # UIDs rolled (or unsaid) — don't risk the wrong one
            try:
                s.add_flags([int(uid)], ["\\Deleted"])
                s.expunge([int(uid)])                      # this UID alone, never a bulk EXPUNGE
                return True
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                return False
        finally:
            try:
                s.logout()
            except Exception:
                pass

    def probe(self, acct, folder, uid, timeout):
        """Whether this UID is there now: (the folder's uidvalidity, whether it is), or None when that can't
        be told (unreachable, or a refusal, a busy server's NO, which says nothing). A stateless, read-only
        look with a short timeout, like the account test, so it needs no IMAP gate: a black-holed host costs
        the caller seconds, not a minute of every other mail operation."""
        try:
            s = self._open_session(acct, timeout=timeout)
        except Exception:
            return None
        try:
            cur = s.select(folder).uidvalidity
            return cur, int(uid) in s.uids("UID", str(int(uid)))
        except Exception:
            return None
        finally:
            try:
                s.logout()
            except Exception:
                pass
