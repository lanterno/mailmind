"""Migration 42 (2026-11-06): which ids each copy of a message names, so threading can ask instead of reading the account.

Placing new mail means finding what it touches: a copy holding an id it names, and a copy that named its id before it
came, by any of the ids in a References header. No index on `messages` can answer that. `thread_links` holds one row
for each id a copy names (its Message-ID when it's a usable one, In-Reply-To, References, and Gmail's thread as
`gm:<thrid>`), and goes with the copy. A copy has its links once it has a thread id, and the two are written together,
so mail not threaded yet has none. The reading of the headers is a copy of `threading.Copy.names` as it stood.

A merge also asks the outbox for the replies queued under the id that went. The outbox is never cleared and each row holds
a whole message ahead of that column, so it gets an index, of the rows that name a thread.
"""
import re

_ID = re.compile(r"<[^<>\s]+>")
_WELL = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")
_REFERENCES_MAX = 1000


def _names(message_id, gmail_thread, in_reply_to, refs):
    mid, thrid = (message_id or "").strip(), (gmail_thread or "").strip()
    names = set(_ID.findall(in_reply_to or "")) | set(_ID.findall(refs or "")[-_REFERENCES_MAX:])
    if _WELL.match(mid):
        names.add(mid)
    if thrid:
        names.add("gm:" + thrid)
    return names


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS thread_links(
                   name TEXT NOT NULL,
                   message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE,
                   PRIMARY KEY(name, message_pk))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_thread_links_message ON thread_links(message_pk)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_outbox_orig_thread ON outbox(orig_account, orig_thread_id) WHERE orig_thread_id IS NOT NULL")
    rows = c.execute("SELECT pk, message_id, gmail_thread, in_reply_to, refs FROM messages WHERE thread_id IS NOT NULL").fetchall()
    for row in rows:
        for name in _names(row[1], row[2], row[3], row[4]):
            c.execute("INSERT OR IGNORE INTO thread_links(name, message_pk) VALUES (?,?)", (name, row[0]))
