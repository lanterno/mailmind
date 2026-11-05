"""Migration 25 (2026-11-05): Undo outlives the process (decision D3).

What Undo reverses was a stack in the server's memory, so a restart, an upgrade or a crash forgot
it. `undo_entries` holds the stack instead, one row an entry, the newest the highest id: `entry`
is the entry as JSON (what to reverse, and for a group its members), and `grp` the undo group an
entry gathers, so the next action in the same group joins it.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS undo_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grp TEXT,                 -- the undo group the entry gathers; NULL for a single action
        entry TEXT NOT NULL,      -- JSON: kind, label and what to reverse, or a group's children
        created_at TEXT NOT NULL
    )""")
