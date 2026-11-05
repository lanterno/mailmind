"""Migration 29 (2026-11-05): an unsubscribe says which accounts the sender wrote to.

The weekly review counts the senders cleaned up, and for one account it counted every account's. Their
mail can't say whose they were: the cleanup's sweep moves it, and a move deletes the row. So `accounts`
(a JSON list) holds them, as the cleanup found them before the sweep. A row logged before this has none,
and counts for every account until it ages out of the review's seven days.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "unsub_log", "accounts TEXT")
