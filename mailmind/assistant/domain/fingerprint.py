"""What an AI answer was made from, as a short string. When the mail's fingerprint now differs from
the one stored with an answer, the answer is stale: a thread summary says the thread grew, a reply
drafted ahead is drafted again.

A fingerprint names messages by their Message-IDs, never by their pks. Reconcile can renumber a
folder's rows, and that changes nothing an answer says, so it must not change a fingerprint. A
message with no Message-ID counts as "": it still counts, so its arrival or removal shows, but two
such messages can be swapped unseen.

The forms are the ones drafting ahead has stored since it began ("t2:" and "m2:"), so the drafts
already stored stay valid.
"""
import zlib
from collections.abc import Iterable


def of(keys: Iterable[str | None], newest_ts: int = 0) -> str:
    """A set of things named by stable keys: Message-IDs, or the (account, thread) of a thread
    waiting on a reply. It changes when one is added, removed or swapped for another, or when
    `newest_ts` moves; never when rows are renumbered or come back in another order."""
    names = sorted(k or "" for k in keys)
    crc = zlib.crc32("\n".join(names).encode("utf-8", "replace"))
    return "t2:%d:%d:%08x" % (len(names), newest_ts, crc)


def message(message_id: str | None, body_fetched: int = 0) -> str:
    """One message, by itself: its Message-ID, and whether its body has been fetched, since a
    fetched body makes for a better answer."""
    return "m2:%s:%s" % (message_id or "", body_fetched)
