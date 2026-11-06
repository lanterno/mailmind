"""Migration 37 (2026-11-06): the brief, the delta and the weekly review each get a table of their own.

`briefing_cache` held all three in one set of columns, told apart by the form of the key: `narrative` was a
delta's headline, and `groups_json` a different shape for each. The brief and the review named messages
in ways that didn't last (the review by pk); here they name them by message key.

What it held goes. Each answer is only read on the day it was written, so the most this costs is writing
that day's brief, delta and review once more.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS briefs(
                   accounts TEXT NOT NULL,
                   day TEXT NOT NULL,
                   fingerprint TEXT NOT NULL,
                   narrative TEXT NOT NULL,
                   groups_json TEXT NOT NULL,
                   model TEXT,
                   created_at TEXT NOT NULL,
                   PRIMARY KEY(accounts, day))""")
    c.execute("""CREATE TABLE IF NOT EXISTS deltas(
                   accounts TEXT NOT NULL,
                   since_ts INTEGER NOT NULL,
                   fingerprint TEXT NOT NULL,
                   headline TEXT NOT NULL,
                   items_json TEXT NOT NULL,
                   model TEXT,
                   created_at TEXT NOT NULL,
                   PRIMARY KEY(accounts, since_ts, fingerprint))""")
    c.execute("""CREATE TABLE IF NOT EXISTS reviews(
                   accounts TEXT NOT NULL,
                   day TEXT NOT NULL,
                   fingerprint TEXT NOT NULL,
                   narrative TEXT NOT NULL,
                   next_json TEXT NOT NULL,
                   stats_json TEXT NOT NULL,
                   needs_json TEXT NOT NULL,
                   waiting_json TEXT NOT NULL,
                   model TEXT,
                   created_at TEXT NOT NULL,
                   PRIMARY KEY(accounts, day))""")
    c.execute("DROP TABLE IF EXISTS briefing_cache")
