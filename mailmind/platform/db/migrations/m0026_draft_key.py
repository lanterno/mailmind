"""Migration 26 (2026-11-05): a draft row remembers the composer that first saved it.

A composer's first save carries no draft id, because it doesn't have one until that save answers.
So a first save sent twice (a retry after an answer that never arrived, a double submit) made two
rows and two copies in Drafts, and a Send whose composer never heard the answer left the copy
behind. `drafts.idempotency_key` holds the composer's key, set when a first save makes the row, so
the next save or send under that key finds the row without its id.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "drafts", "idempotency_key TEXT")
