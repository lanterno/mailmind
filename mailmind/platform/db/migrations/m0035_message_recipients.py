"""Migration 35 (2026-11-06): who a message went to, one row each.

`messages.recipients` holds the decoded To and Cc as text, cut at 2000 characters, for search. Asking who
you have written to meant reading every message you sent with a regular expression, and someone past the
cut was new to you however often you'd written. `message_recipients` holds each address of each copy, and
goes with the copy.

Mail already stored is read from that text, as the lookup read it until now: a name in quotes isn't an
address. A column that was cut is read from the message itself when its raw bytes are stored, since opening
a message whose body is stored reads nothing again. With none stored the last token may be part of an address
and is left out, and storing the body later (`ingest._keep_body`) writes the whole list.
"""
import re
from email.parser import BytesHeaderParser
from email.utils import getaddresses

_ADDR_TOKEN = re.compile(r'[^\s<>,;:"()\[\]]+@[^\s<>,;:"()\[\]]+')
_QUOTED_NAME = re.compile(r'"[^"]*"')
_FOLD = re.compile(r"\r?\n[ \t]+")
_CUT_AT = 2000


def _addrs(text):
    tokens = _ADDR_TOKEN.findall(_QUOTED_NAME.sub(" ", text))
    if len(text) >= _CUT_AT and tokens:
        tokens = tokens[:-1]
    return {t.strip(".'").lower() for t in tokens}


def _addrs_in_message(raw):
    """A copy of `mailparse.recipient_addrs` as it stood, over the raw bytes."""
    msg = BytesHeaderParser().parsebytes(raw or b"")
    values = [_FOLD.sub(" ", str(v)) for field in ("To", "Cc") for v in msg.get_all(field) or []]
    return {a.strip(".'").lower() for _, a in getaddresses(values, strict=False) if "@" in a}


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS message_recipients(
                   message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE,
                   addr TEXT NOT NULL,
                   PRIMARY KEY(message_pk, addr))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_message_recipients_addr ON message_recipients(addr)")
    rows = c.execute("SELECT pk, recipients FROM messages WHERE recipients IS NOT NULL AND recipients != ''").fetchall()
    for row in rows:
        addrs = set()
        if len(row[1]) >= _CUT_AT:                  # one at a time: only these need a body in memory
            body = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (row[0],)).fetchone()
            addrs = _addrs_in_message(body[0]) if body else set()
        for addr in addrs or _addrs(row[1]):
            c.execute("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)", (row[0], addr))
