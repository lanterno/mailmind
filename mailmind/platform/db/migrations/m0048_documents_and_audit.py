"""Migration 48 (2026-11-11): what was kept in plaintext files beside the database, in it, where it is encrypted.

`documents` holds a document a context keeps whole, as the JSON its file held: the rule book (`rules`), the saved views
(`views`) and the snippets (`snippets`), one row each. `version` goes up with each save, so a reader that keeps one in
memory asks only that to know whether it is still current. `audit_events` holds the audit trails, one row an event:
`trail` is `sweep` or `reconcile`, `ts` when it happened, and `body` the record as a line of the trail held it.

The files these were in are read by nothing from here on.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS documents(
                   name TEXT PRIMARY KEY,
                   body TEXT NOT NULL,
                   version INTEGER NOT NULL DEFAULT 1,
                   changed_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS audit_events(
                   id INTEGER PRIMARY KEY,
                   trail TEXT NOT NULL,
                   ts TEXT NOT NULL,
                   body TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts)")
