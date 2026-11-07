"""Curation's view of the database: stored mail by sender and folder, and its own tables, `unsub_log` and
`sender_unsub`. It reads `messages` as a read model. The rows themselves are the Mailbox's, so a change to one
goes through `rows`, which bootstrap binds to whoever holds them."""
import json

from mailmind.curation.domain import unsubscribe
from mailmind.kernel.snoozed import not_snoozed_sql
from mailmind.kernel.when import now_iso
from mailmind.platform.db.uow import UnitOfWork

_uow = UnitOfWork()


def sender_unsub_put(c, from_addr, info, accounts, when):
    """Keep how to unsubscribe from a sender, past the sender's mail. A newest message offering no way
    ends it: the list has stopped offering one."""
    if not unsubscribe.offers_a_way(info):
        c.execute("DELETE FROM sender_unsub WHERE from_addr=?", (from_addr,))
    else:
        c.execute("INSERT OR REPLACE INTO sender_unsub(from_addr, raw, accounts, updated_at) VALUES(?,?,?,?)",
                  (from_addr, info["raw"], json.dumps(accounts), when))


def sender_unsub_get(c, from_addr):
    return c.execute("SELECT * FROM sender_unsub WHERE from_addr=?", (from_addr,)).fetchone()


def unsub_log_get(c, key):
    return c.execute("SELECT * FROM unsub_log WHERE idempotency_key=?", (key,)).fetchone()


def unsub_log_put(c, from_addr, method, status, http_status, ts, key, accounts):
    """Idempotent insert; returns the row (existing on dupe key, so a repeat never re-fires).
    `accounts` are those the sender wrote to."""
    c.execute("""INSERT OR IGNORE INTO unsub_log(from_addr,method,status,http_status,ts,idempotency_key,accounts)
                 VALUES(?,?,?,?,?,?,?)""", (from_addr, method, status, http_status, ts, key, json.dumps(accounts)))
    return c.execute("SELECT * FROM unsub_log WHERE idempotency_key=?", (key,)).fetchone()


def senders_cleaned(c, since_iso, accounts):
    """How many senders were unsubscribed from since then, in any of `accounts`, or anywhere when none are named."""
    jac, jap = (" AND value IN (%s) " % ",".join("?" for _ in accounts), list(accounts)) if accounts else ("", [])
    return c.execute("SELECT COUNT(*) FROM unsub_log WHERE ts>=? AND (accounts IS NULL OR EXISTS "
                     "(SELECT 1 FROM json_each(unsub_log.accounts) WHERE 1=1" + jac + "))",
                     [since_iso, *jap]).fetchone()[0]


class SenderMail:
    def __init__(self, rows):
        self._rows = rows

    def noisy_senders(self, buckets, min_count):
        ph = ",".join("?" for _ in buckets)
        with _uow.read() as c:
            return c.execute(
                f"""SELECT lower(from_addr) addr, MAX(from_name) name, COUNT(*) n,
                           SUM(CASE WHEN seen=0 THEN 1 ELSE 0 END) unseen,
                           MAX(ts) last_ts, MIN(ts) first_ts, MAX(date_iso) last_iso,
                           MAX(bucket) bucket
                    FROM messages WHERE from_addr!='' AND bucket IN ({ph})
                    GROUP BY lower(from_addr) HAVING n>=?""", (*buckets, min_count)).fetchall()

    def accounts_by_sender(self, addrs):
        """{sender: [{email, count}]}, per-account rollup for just these senders."""
        aph = ",".join("?" for _ in addrs)
        by_acct = {}
        with _uow.read() as c:
            for row in c.execute(
                    f"SELECT lower(from_addr) addr, account, COUNT(*) n FROM messages "
                    f"WHERE lower(from_addr) IN ({aph}) GROUP BY lower(from_addr), account", addrs):
                by_acct.setdefault(row["addr"], []).append({"email": row["account"], "count": row["n"]})
        return by_acct

    def subjects_by_sender(self, addrs, keep):
        """{sender: the subjects of their newest `keep` messages that have one}."""
        aph = ",".join("?" for _ in addrs)
        subj = {}
        with _uow.read() as c:
            for row in c.execute(
                    f"SELECT lower(from_addr) addr, subject, ts FROM messages "
                    f"WHERE lower(from_addr) IN ({aph}) ORDER BY ts DESC", addrs):
                lst = subj.setdefault(row["addr"], [])
                if len(lst) < keep and row["subject"]:
                    lst.append(row["subject"])
        return subj

    def counts(self, addr):
        """(how many messages from this sender, how many unread)."""
        with _uow.read() as c:
            n = c.execute("SELECT COUNT(*) k FROM messages WHERE lower(from_addr)=?", (addr,)).fetchone()["k"]
            unseen = c.execute("SELECT COUNT(*) k FROM messages WHERE lower(from_addr)=? AND seen=0",
                               (addr,)).fetchone()["k"]
        return n, unseen

    def sweep_targets(self, from_addr, limit):
        """INBOX copies of the sender's mail (the clutter to move to Trash). Junk/Trash mail is left where
        it is — already filtered."""
        with _uow.read() as c:
            return c.execute(
                "SELECT pk, account, folder, subject, seen FROM messages "
                "WHERE lower(from_addr)=? AND folder='INBOX' AND pending_removal IS NULL ORDER BY ts DESC LIMIT ?",
                (from_addr, limit)).fetchall()

    def unsub_stored(self, from_addr):
        """(the sender's rows, newest first; the accounts they wrote to; what the newest that can say says,
        or None), reading on one connection, however many rows have no body yet."""
        with _uow.read() as c:
            rows = c.execute(
                "SELECT pk, account, folder, uid, list_unsub_raw, list_unsub_method FROM messages "
                "WHERE lower(from_addr)=? ORDER BY ts DESC", (from_addr,)).fetchall()
            accts = list(dict.fromkeys(r["account"] for r in rows if r["account"]))

            def body_of(pk):
                r = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (pk,)).fetchone()
                return r["raw"] if r else None

            info = unsubscribe.stored(rows, body_of, self.remember_unsub)
        return rows, accts, info

    def remember_unsub(self, pk, parsed):
        with _uow.write() as c:
            self._rows.set_list_unsub(c, pk, parsed.get("raw", ""), parsed["method"])

    def keep_unsub(self, from_addr, info, accounts):
        with _uow.write() as c:
            sender_unsub_put(c, from_addr, info, accounts, now_iso())

    def kept_unsub(self, from_addr):
        with _uow.read() as c:
            return sender_unsub_get(c, from_addr)

    def attempt(self, key):
        with _uow.read() as c:
            return unsub_log_get(c, key)

    def record_attempt(self, from_addr, method, status, http_status, key, accounts):
        with _uow.write() as c:
            unsub_log_put(c, from_addr, method, status, http_status, now_iso(), key, accounts)

    def inbox_senders(self, account):
        """Each sender in this account's INBOX, newest first, leaving junk and what is on its way out."""
        with _uow.read() as c:
            return c.execute(
                """SELECT lower(from_addr) addr, MAX(from_name) name, COUNT(*) n,
                          MAX(ts) last, MAX(date_iso) iso
                   FROM messages WHERE account=? AND folder='INBOX' AND from_addr!='' AND is_junk=0
                     AND pending_removal IS NULL
                   GROUP BY lower(from_addr) ORDER BY last DESC""", (account,)).fetchall()

    def stored_inbox(self, account):
        with _uow.read() as c:
            return c.execute("""SELECT from_addr, from_name, subject, bucket, list_unsub_raw FROM messages
                                WHERE account=? AND folder='INBOX' AND deleted=0""", (account,)).fetchall()

    def ai_junk_ids(self, account):
        """The Message-IDs of INBOX mail stored as junk, and no snoozed mail. The classifier said so, at ingest."""
        with _uow.read() as c:
            return set(m for (m,) in c.execute(
                f"""SELECT message_id FROM messages WHERE account=? AND folder='INBOX' AND is_junk=1
                   AND deleted=0 AND {not_snoozed_sql('messages')}
                   AND message_id IS NOT NULL AND message_id!=''""", (account,)).fetchall())

    def forget_moved(self, account, moved, stragglers):
        """The local rows of what a sweep moved, found by Message-ID: a UIDVALIDITY change no sync has
        seen yet leaves that good, where a UID may be another row's. A message with none goes by UID."""
        left = set(stragglers)
        with _uow.write() as c:
            for r in moved:
                if r["uid"] in left:
                    continue
                if not r["mid"]:
                    self._rows.delete_by_uids(c, account, "INBOX", [r["uid"]])
                    continue
                row = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                                (account, r["mid"])).fetchone()
                if row:
                    self._rows.delete_message(c, row["pk"])

    def forget_expired(self, account, targets, stragglers):
        """The local rows of what expiry moved, by Message-ID alone (pk is not stable). One with none stays
        until the next reconcile, since a UID may be another row's."""
        strag = set(stragglers)
        with _uow.write() as c:
            for t in targets:
                if t["uid"] in strag or not t["mid"]:
                    continue
                row = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                                (account, t["mid"])).fetchone()
                if row:
                    self._rows.delete_message(c, row["pk"])
