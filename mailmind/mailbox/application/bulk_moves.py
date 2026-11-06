"""Moving many messages out of the INBOX, and checking they went."""
import imaplib
import time

from mailmind.mailbox.application.session import MailSession

MOVE_BATCH = 200


def _move_one(s: MailSession, uid: str, dest: str) -> None:
    """One message: MOVE, or on a server without it a checked COPY, then \\Deleted and a UID
    EXPUNGE of this UID alone. A failure leaves it for the verification pass."""
    try:
        s.move([uid], dest)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass


def _still_in_inbox(s: MailSession) -> set[str]:
    s.select("INBOX")
    return {str(u) for u in s.uids("ALL")}


def move_verified(s: MailSession, uids: list[str], dest: str) -> tuple[int, list[str]]:
    """Move uids (strings) to dest, then re-check INBOX and retry any that didn't leave.
    Returns (moved, stragglers). They go MOVE_BATCH at a time, in one MOVE where the server offers
    it, and copied together where it doesn't; a refused batch goes one message at a time
    (_move_one), so one bad UID can't sink the rest."""
    s.select("INBOX", readonly=False)
    for i in range(0, len(uids), MOVE_BATCH):
        chunk = uids[i:i + MOVE_BATCH]
        try:
            s.move(chunk, dest)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            for u in chunk:
                _move_one(s, u, dest)
        time.sleep(0.2)
    # verification: which requested uids are STILL in INBOX?
    want = set(uids)
    present = _still_in_inbox(s)
    remaining = [u for u in uids if u in present and u in want]
    if remaining:  # one retry pass
        s.select("INBOX", readonly=False)
        for u in remaining:
            _move_one(s, u, dest)
        present = _still_in_inbox(s)
        remaining = [u for u in remaining if u in present]
    return len(uids) - len(remaining), remaining
