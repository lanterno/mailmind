"""Migration 31 (2026-11-05): how far a folder's flags have been read.

Each incremental sync read the flags of a folder's 500 newest messages again, and saw nothing of older
ones. On a server with CONDSTORE it can ask for just what changed since the HIGHESTMODSEQ it last saw, which
`sync_state.highest_modseq` holds. It stays empty on a server without.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "sync_state", "highest_modseq INTEGER")
