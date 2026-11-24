"""Copies Compose keeps on the server: a sent message in Sent, a draft in Drafts. Each opens a session
of its own and closes it. Standard library only."""
from collections.abc import Callable, Sequence
from datetime import datetime
import imaplib

from mailmind.kernel.ids import FolderName
from mailmind.mailbox.application.ports import Account, OpenSession
from mailmind.mailbox.application.session import MailSession, find_role


class Copies:
    def __init__(self, open_session: OpenSession, quote: Callable[[str], str]):
        self._open_session = open_session
        self._quote = quote

    def append_to_sent(self, acct: Account, raw: bytes, message_id: str | None = None) -> str:
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

    def in_sent(self, acct: Account, message_id: str) -> bool:
        """Is a message under this Message-ID in the account's Sent?"""
        s = self._open_session(acct)
        try:
            s.select(find_role(s, "sent") or "Sent")
            return bool(s.uids("UNDELETED", "HEADER", "Message-ID", self._quote(message_id)))
        finally:
            try:
                s.logout()
            except Exception:
                pass

    def append_to_drafts(self, acct: Account, raw: bytes, prev_uid: int | None = None, prev_uidvalidity: int | None = None,
                         prev_msgid: str | None = None, prev_folder: str | None = None, message_id: str | None = None,
                         tried: Sequence[str] = ()) -> tuple[FolderName, int | None, int | None, list[int]]:
        """APPEND a draft (flagged \\Seen and \\Draft, as a mail app files its own) to the account's Drafts folder,
        then delete the copies it replaces (single-UID expunges — NEVER a bulk expunge). Returns
        (folder, new_uid|None, uidvalidity|None, the UIDs removed). Append first, so a save that fails or
        a crash never leaves none: at worst the old copy stays beside the new one. prev_folder: where the
        previous copy is; when that isn't this Drafts folder, nothing is deleted here (its UID means
        nothing in this one), and the caller removes it there. tried: the Message-IDs of the copies earlier
        tries may have left, which they never heard of. One that's this very message (message_id) stands, and
        nothing is appended; every other goes once the new copy is there."""
        s = self._open_session(acct)
        try:
            drafts = FolderName(find_role(s, "drafts") or "Drafts")
            if prev_folder is not None and prev_folder != drafts:
                prev_uid = prev_msgid = None
            try:
                uidv = s.select(drafts, readonly=False).uidvalidity
                selected = True
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:          # refused: no copy is deleted, but the APPEND still goes
                uidv, selected = None, False
            # The copies it replaces, where they can be told for sure, asked before the new copy can answer too: the one the
            # row names, and every other in Drafts under the Message-ID it was saved with. Another app may have left more than
            # one there (Thunderbird's earlier saves of it), both copies of the one message, by its key.
            replaced: list[int] = []
            removed: list[int] = []
            if prev_uid and selected and (prev_uidvalidity is None or prev_uidvalidity == uidv):
                replaced.append(int(prev_uid))
            if prev_msgid and selected:
                try:
                    replaced += [u for u in s.uids("UNDELETED", "HEADER", "Message-ID", self._quote(prev_msgid))
                                 if u not in replaced]
                except Exception:
                    pass
            lefts = [(mid, self._left_by(s, mid)) for mid in tried]
            landed = next((uid for mid, uid in lefts if uid and mid == message_id), None)
            new_uid = landed or s.append(drafts, raw, flags=["\\Seen", "\\Draft"], when=datetime.now().astimezone())
            for _, left in lefts:
                if left and left != landed:
                    try:
                        s.add_flags([left], ["\\Deleted"])
                        s.expunge([left])
                    except Exception:
                        pass
            for old in replaced:
                if old == new_uid:
                    continue
                try:
                    s.add_flags([old], ["\\Deleted"])
                    s.expunge([old])             # this UID alone, never a bulk EXPUNGE
                    removed.append(old)
                except Exception:
                    pass
            return drafts, new_uid, uidv, removed
        finally:
            try:
                s.logout()
            except Exception:
                pass

    def _left_by(self, s: MailSession, message_id: str | None) -> int | None:
        """The UID of a copy of ours under this Message-ID in the selected folder, or None."""
        if not message_id:
            return None
        try:
            ids = s.uids("UNDELETED", "HEADER", "Message-ID", self._quote(message_id))
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            return None
        return ids[0] if ids else None

    def delete_left_copy(self, acct: Account, message_id: str) -> bool:
        """Remove the copy a try may have left in Drafts, under a Message-ID only MailMind minted. True if one went."""
        s = self._open_session(acct)
        try:
            s.select(find_role(s, "drafts") or "Drafts", readonly=False)
            left = self._left_by(s, message_id)
            if not left:
                return False
            s.add_flags([left], ["\\Deleted"])
            s.expunge([left])
            return True
        finally:
            try:
                s.logout()
            except Exception:
                pass

    def delete_draft_uid(self, acct: Account, folder: str | None, uid: int | None, uidvalidity: int | None = None) -> bool:
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

    def probe(self, acct: Account, folder: str, uid: int, timeout: int) -> tuple[int | None, bool] | None:
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
