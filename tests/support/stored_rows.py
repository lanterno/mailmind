"""Rows in the temporary database, as the characterization tests make and read them directly: a SELECT's rows (`q`, `rows`), a message
stored as ingest does (`put`), what the search index holds, and a reply drafted ahead."""
import itertools

from mailmind.bootstrap import discovery
from mailmind.bootstrap.assistant import answers


def q(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


rows = q    # what some of these tests call it


A = "ada@example.test"
NOW_TS = 1_800_000_000
_uid = itertools.count(1)


def put(c, mid="", *, account=A, folder="INBOX", irt="", refs="", gm="", subject="Hello", ts=0,
        snippet="", bucket="other/personal-ish", is_junk=0, **cols):
    """Store one message as ingest does, then set any further columns (thread_id, answered, ...)."""
    from mailmind.bootstrap.mailbox import store
    pk = store.upsert_message(c, {"account": account, "kind": "gmail" if gm else "imap", "folder": folder,
                                  "uid": next(_uid), "message_id": mid, "gmail_thread": gm or None,
                                  "from_addr": "lin@example.test", "from_name": "Lin Mori", "subject": subject,
                                  "date_iso": None, "ts": ts, "snippet": snippet, "bucket": bucket,
                                  "is_junk": is_junk})
    cols = {"in_reply_to": irt, "refs": refs, **cols}
    c.execute(f"UPDATE messages SET {', '.join(k + '=?' for k in cols)} WHERE pk=?", [*cols.values(), pk])
    discovery.index_message(c, pk)
    return pk


def tid(c, pk):
    return c.execute("SELECT thread_id FROM messages WHERE pk=?", (pk,)).fetchone()["thread_id"]


def col(c, pk, name):
    return c.execute(f"SELECT {name} FROM messages WHERE pk=?", (pk,)).fetchone()[0]


def hits(c, match):
    return sorted(r[0] for r in c.execute("SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?", (match,)))


def _ahead(c, ref, created, body="Sounds good."):
    answers.draft_ahead_put(c, A, ref, "fp", body, "warm", "m", None, "dwell", created)


def _ahead_keys(c):
    return sorted(r[0] for r in c.execute("SELECT ref_key FROM draft_ahead"))


def pk_of(message_id, account=None):
    sql = "SELECT pk FROM messages WHERE message_id=?" + (" AND account=?" if account else "")
    [r] = rows(sql, message_id, *([account] if account else []))
    return r["pk"]


def summary(pk, importance, category, action=""):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, pk, "stub", importance, category, action, "test", "2026-11-02T12:00:00+00:00")
        c.commit()
    finally:
        c.close()


# What importing a document's old file comes to: its text, inserted as it is; or, for one there already, put in its place a
# version on, as a write by hand would.
IMPORT = ("INSERT INTO documents(name, body, changed_at) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET body=excluded.body, "
          "version=documents.version + 1, changed_at=excluded.changed_at")


def document(name):
    """The text the document `name` holds, as it was written, or None when there is none."""
    found = q("SELECT body FROM documents WHERE name=?", name)
    return found[0]["body"] if found else None


def put_document(name, text):
    """Keep `text` as the document `name`, as an import of its old file does."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute(IMPORT, (name, text, "2026-11-02T12:00:00+00:00"))
        c.commit()
    finally:
        c.close()
