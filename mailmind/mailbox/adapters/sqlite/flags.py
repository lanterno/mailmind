"""A message's flags as stored, and how it is classified."""
from mailmind.mailbox.domain.flags import COLUMNS as _FLAG_COLS


_FLAG_ROW = ("SELECT pk, uid, message_id, pending_removal, " + ", ".join(_FLAG_COLS)
             + " FROM messages WHERE account=? AND folder=? ")


def set_local_flags(c, pk, **flags):
    """Mirror IMAP flag changes locally. e.g. set_local_flags(c, pk, seen=1)."""
    cols = {k: int(bool(v)) for k, v in flags.items() if k in _FLAG_COLS}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE messages SET {sets} WHERE pk=?", [*cols.values(), pk])


_CLASSIFIED = "pk, from_addr, from_name, subject, bucket, reason, list_unsub_method"


def classified_from_address(c, addr):
    """What the classifier reads and said of each stored message from this address."""
    return c.execute(f"SELECT {_CLASSIFIED} FROM messages WHERE lower(from_addr)=?", (addr,)).fetchall()


def classified_from_domain(c, domain):
    """The same, of mail from this domain and its sub-domains."""
    return c.execute(f"SELECT {_CLASSIFIED} FROM messages WHERE lower(from_addr) LIKE ? OR lower(from_addr) LIKE ?",
                     (f"%@{domain}", f"%.{domain}")).fetchall()


def set_classification(c, pk, bucket, confidence, reason, is_junk):
    c.execute("UPDATE messages SET bucket=?, confidence=?, reason=?, is_junk=? WHERE pk=?",
              (bucket, confidence, reason, is_junk, pk))


def flag_rows_newest(c, account, folder, limit):
    """The newest stored rows of the folder, none on its way out, to compare with the server's flags."""
    return c.execute(_FLAG_ROW + "AND pending_removal IS NULL ORDER BY uid DESC LIMIT ?",
                     (account, folder, limit)).fetchall()


def flag_rows_of(c, account, folder, uids):
    """The stored rows of these UIDs, to compare with the server's flags."""
    return c.execute(_FLAG_ROW + "AND uid IN (" + ",".join("?" * len(uids)) + ")",
                     [account, folder, *uids]).fetchall()


def set_flags_if_still(c, pk, new, was):
    """Give the row the flags `new`, only if it still holds those of `was`, the row as it was read: a click may have
    landed since. Returns how many rows changed."""
    return c.execute(
        "UPDATE messages SET " + ", ".join(f"{k}=?" for k in _FLAG_COLS) + " WHERE pk=? AND "
        + " AND ".join(f"{k} IS ?" for k in _FLAG_COLS),
        [*(new[k] for k in _FLAG_COLS), pk, *(was[k] for k in _FLAG_COLS)]).rowcount
