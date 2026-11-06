"""Migration 35 (2026-11-06): who a message went to, one row each.

`messages.recipients` holds the decoded To and Cc as text, cut at 2000 characters, for search. Asking who
you have written to meant reading every message you sent with a regular expression, and someone past the
cut was new to you however often you'd written. `message_recipients` holds each address of each copy, and
goes with the copy.

Mail already stored is read from that text, as the lookup read it until now: a name in quotes isn't an
address, and in a column that was cut the last token may be part of one. The same mail's next read of its
own headers (opening it, or a resync) replaces those rows with what the headers say in full.
"""
import re

_ADDR_TOKEN = re.compile(r'[^\s<>,;:"()\[\]]+@[^\s<>,;:"()\[\]]+')
_QUOTED_NAME = re.compile(r'"[^"]*"')
_CUT_AT = 2000


def _addrs(text):
    tokens = _ADDR_TOKEN.findall(_QUOTED_NAME.sub(" ", text))
    if len(text) >= _CUT_AT and tokens:
        tokens = tokens[:-1]
    return {t.strip(".'").lower() for t in tokens}


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS message_recipients(
                   message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE,
                   addr TEXT NOT NULL,
                   PRIMARY KEY(message_pk, addr))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_message_recipients_addr ON message_recipients(addr)")
    rows = c.execute("SELECT pk, recipients FROM messages WHERE recipients IS NOT NULL AND recipients != ''").fetchall()
    for row in rows:
        for addr in _addrs(row[1]):
            c.execute("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)", (row[0], addr))
