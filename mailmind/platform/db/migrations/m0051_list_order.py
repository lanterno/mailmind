"""Migration 51 (2026-11-13): an index in the order a list shows a folder, newest first.

A list asks for a folder's messages in some accounts, none on its way out, by `ts DESC, pk DESC`. The indexes before this
one find the rows but not in that order, so SQLite sorted the whole folder before it could give the first page: about
120 ms for an inbox of 100,000 natively, and most of a second in the browser's WebAssembly. This index holds the rows in
that order, with the account beside them, and only those a list can show (`pending_removal IS NULL`), so the first page is
the first rows read.
"""


def migrate(c):
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_listed ON messages(folder, ts DESC, pk DESC, account) "
              "WHERE pending_removal IS NULL")
