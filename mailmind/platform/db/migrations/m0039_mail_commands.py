"""Migration 39 (2026-11-06): the offline queue's table becomes the command log.

A change to the mail on a server now has one path, online or offline, so every such change has a row here and
not only one that couldn't reach its server. Rows keep their ids, since `messages.pending_removal` names them.

Rolling back takes the backup the upgrade made: an older commit knows only `read_mutations`.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS mail_commands(   -- keyed on message_id, never pk
                   id              INTEGER PRIMARY KEY AUTOINCREMENT,
                   account         TEXT NOT NULL,
                   idempotency_key TEXT NOT NULL UNIQUE,            -- one per request, so a resubmit dedupes
                   kind            TEXT NOT NULL,                   -- trash | archive | junk | move | flag
                   message_id      TEXT,                            -- RFC822 Message-ID: what outlasts a renumber
                   src_folder      TEXT NOT NULL,                   -- where the message was when it was accepted
                   dest            TEXT,                            -- where it goes, once that is known
                   params          TEXT NOT NULL,                   -- JSON: the copy as accepted, flags, what they replaced
                   state           TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | error
                   attempts        INTEGER NOT NULL DEFAULT 0,
                   next_attempt_at INTEGER NOT NULL DEFAULT 0,
                   applied         INTEGER NOT NULL DEFAULT 0,      -- done once: the server holds the change
                   begun           INTEGER NOT NULL DEFAULT 0,      -- a try may have reached the server: ask before repeating
                   outcome         TEXT,                            -- JSON: what a reversal needs from the server's answer
                   last_error      TEXT,                            -- a fixed code or a class name, nothing more
                   created_at      TEXT NOT NULL,
                   done_at         TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_commands_state ON mail_commands(state, next_attempt_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_commands_message ON mail_commands(account, message_id)")
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='read_mutations'").fetchone():
        # Any of these may have reached the server already, so each next try asks it first.
        c.execute("""INSERT OR IGNORE INTO mail_commands(id, account, idempotency_key, kind, message_id, src_folder, dest,
                       params, state, attempts, next_attempt_at, applied, begun, last_error, created_at, done_at)
                     SELECT id, account, idempotency_key, op, message_id, src_folder, dest,
                       payload, state, attempts, next_attempt_at, applied, 1, last_error, created_at, done_at
                     FROM read_mutations""")
        c.execute("DROP TABLE read_mutations")
