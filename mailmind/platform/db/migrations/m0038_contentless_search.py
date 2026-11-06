"""Migration 38 (2026-11-06): the search index stops keeping its own copy of the mail.

`msg_fts` stored every column it indexed, so each body sat in the database twice: in
`messages.body_text` and in `msg_fts_content`. Nothing reads the index's copy. A search takes rowids
and ranks from the index and everything else from `messages`. A contentless table keeps the index and
the column sizes bm25 needs, and `contentless_delete=1` lets a row go by its rowid as it did. It needs
SQLite 3.43 (`migrate.SQLITE_NEEDED`).

The new index is built from what `messages` holds now, so one that had drifted from it comes out right.
The file doesn't shrink by itself: the pages this frees go to the next mail, and
`python3 -m mailmind db compact` gives them back.
"""
COLUMNS = ("subject", "from_name", "from_addr", "recipients", "snippet", "body_text")


def migrate(c):
    row = c.execute("SELECT sql FROM sqlite_master WHERE name='msg_fts'").fetchone()
    if row and "contentless_delete" in row[0]:
        return
    c.execute("DROP TABLE IF EXISTS msg_fts")
    c.execute(f"CREATE VIRTUAL TABLE msg_fts USING fts5({', '.join(COLUMNS)}, "
              "tokenize='unicode61 remove_diacritics 2', content='', contentless_delete=1)")
    values = ", ".join(f"coalesce({column}, '')" for column in COLUMNS)
    c.execute(f"INSERT INTO msg_fts(rowid, {', '.join(COLUMNS)}) SELECT pk, {values} FROM messages")
