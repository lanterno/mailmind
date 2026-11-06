"""Migration 36 (2026-11-06): a backlog of triage goes to the Message Batches API, at half price.

A batch outlives the process that sent it: its answers take up to a day, and they are paid for whether or
not anything comes back for them. `ai_batches` says which batch is out and what it may cost, and
`ai_batch_items` which messages it holds, by their keys, so nothing triages those meanwhile. An item goes
when its batch's answers are in, and a message with no answer is then waiting again.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ai_batches(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   remote_id TEXT,
                   feature TEXT NOT NULL,
                   model TEXT NOT NULL,
                   state TEXT NOT NULL,
                   requests INTEGER NOT NULL,
                   estimate_micros INTEGER NOT NULL,
                   cutoff_ts INTEGER NOT NULL,
                   created_at INTEGER NOT NULL,
                   polled_at INTEGER NOT NULL DEFAULT 0,
                   ended_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_batch_items(
                   message_key INTEGER PRIMARY KEY REFERENCES message_identity(key) ON DELETE CASCADE,
                   batch_id INTEGER NOT NULL REFERENCES ai_batches(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ai_batch_items_batch ON ai_batch_items(batch_id)")
