"""What a message holds beyond its row: its body, attachments, recipients and labels, and the text search reads."""
import json

from mailmind.content.text import snippet_of
from mailmind.mailbox.adapters.sqlite import bound
from mailmind.mailbox.domain.ingested import recipients_text


def store_body(c, pk, raw, when):
    c.execute("INSERT OR REPLACE INTO bodies(message_pk,raw,fetched_at) VALUES(?,?,?)",
              (pk, raw, when))
    c.execute("UPDATE messages SET body_fetched=1 WHERE pk=?", (pk,))


def get_body(c, pk):
    r = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (pk,)).fetchone()
    return r["raw"] if r else None


def bodies_to_fetch(c, account, skip, limit):
    """An account's newest `limit` messages with no body text, none on its way out, but for the pks in `skip`."""
    return c.execute("SELECT pk, account FROM messages WHERE account=? AND body_text IS NULL "
                     "AND pending_removal IS NULL AND pk NOT IN (SELECT value FROM json_each(?)) "
                     "ORDER BY ts DESC LIMIT ?", (account, json.dumps(list(skip)), limit)).fetchall()


def still_without_body(c, pks):
    """Which of these pks still have no body text."""
    return [x["pk"] for x in c.execute(
        "SELECT pk FROM messages WHERE body_text IS NULL AND pk IN (SELECT value FROM json_each(?))",
        (json.dumps(pks),))]


def attachment_of(c, pk, part_id):
    """What's kept of one part of a message: its filename and content type."""
    return c.execute("SELECT filename,content_type FROM attachments WHERE message_pk=? AND part_id=?",
                     (pk, part_id)).fetchone()


def has_attachments(c, pk):
    return c.execute("SELECT 1 FROM attachments WHERE message_pk=?", (pk,)).fetchone() is not None


def insert_attachments(c, pk, attachments):
    for a in attachments:
        c.execute("""INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,is_inline)
                     VALUES(?,?,?,?,?,?,?)""",
                  (pk, a["part_id"], a["filename"], a["content_type"], a["size"],
                   a["content_id"], a["is_inline"]))


def delete_attachments(c, pk):
    c.execute("DELETE FROM attachments WHERE message_pk=?", (pk,))


def mark_has_attachments(c, pk):
    c.execute("UPDATE messages SET has_attach=1 WHERE pk=?", (pk,))


RECIPIENT_ROWS_MAX = 5000
LABEL_ROWS_MAX = 1000                           # a server is free to say as many as it likes


def set_recipients(c, pk, addrs):
    """Who this copy of a message went to, one row each (`mime.recipient_addrs`). Bounded, since a
    forged To can be as long as it likes."""
    c.execute("DELETE FROM message_recipients WHERE message_pk=?", (pk,))
    c.executemany("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)",
                  [(pk, a) for a in addrs[:RECIPIENT_ROWS_MAX]])


def set_labels(c, pk, labels):
    """Gmail's labels on this copy of a message, as the last header fetch read them. They go with the copy. A sync reads them
    only for what it fetches, and doesn't touch them when it reads flags again, so a label changed on Gmail's side reaches a
    copy already stored only if a scan fetches it again."""
    c.execute("DELETE FROM message_labels WHERE message_pk=?", (pk,))
    c.executemany("INSERT OR IGNORE INTO message_labels(message_pk, label) VALUES (?,?)",
                  [(pk, label) for label in sorted(labels)[:LABEL_ROWS_MAX]])


def labels_of_account(c, account):
    """An account's own labels, each with how many messages carry it. Gmail's system labels (\\Inbox and the like) are the
    folders' to show. A message counts once however many copies it has."""
    return c.execute(r"""SELECT l.label AS name, count(DISTINCT m.message_key) AS count
                         FROM message_labels l JOIN messages m ON m.pk=l.message_pk
                         WHERE m.account=? AND m.pending_removal IS NULL AND substr(l.label,1,1) <> '\'
                         GROUP BY l.label ORDER BY l.label COLLATE NOCASE""", (account,)).fetchall()


def set_recipients_text(c, pk, recipients):
    """To and Cc as the text search reads (`recipients_of`)."""
    c.execute("UPDATE messages SET recipients=? WHERE pk=?", (recipients, pk))


def set_ingested_headers(c, pk, in_reply_to, refs, recipients):
    """What ingest read of a message's headers beyond its row: whom it answers, and To and Cc as text."""
    c.execute("UPDATE messages SET in_reply_to=?, refs=?, recipients=? WHERE pk=?",
              (in_reply_to, refs, recipients, pk))


def recipients_of(parsed):
    """To + Cc as one searchable string (names + addresses), for the to:/cc: operators."""
    return recipients_text(parsed.get("to"), parsed.get("cc"))


def fts_set_body(c, pk, body_text):
    """Fill body_text once a body is fetched, derive a list/river snippet if none was
    captured at ingest, and refresh the FTS row."""
    bt = body_text or ""
    snip = snippet_of(bt)
    c.execute("UPDATE messages SET body_text=?, "
              "snippet=CASE WHEN snippet IS NULL OR snippet='' THEN ? ELSE snippet END "
              "WHERE pk=?", (bt, snip, pk))
    bound._index.index_message(c, pk)
