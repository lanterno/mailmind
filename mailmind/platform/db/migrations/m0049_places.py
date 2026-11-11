"""Migration 49 (2026-11-11): the places the page's URLs name, so that no URL names anything of a person's.

The browser keeps every URL a tab goes to in its history, in plaintext, and may sync it. A place is a route the page would
otherwise write into one (an account, a folder, a search, a topic, an opened message) under a random id, which the URL names
instead: `/p/<id>`. `route` is the page's route as the JSON text it sent, and `used_at` when it was last sent or opened, which
says which go when the table is pruned to the newest.
"""


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS places(
                   id TEXT PRIMARY KEY,
                   route TEXT NOT NULL,
                   used_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_places_used_at ON places(used_at)")
