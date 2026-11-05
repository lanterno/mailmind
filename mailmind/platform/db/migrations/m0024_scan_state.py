"""Migration 24 (2026-11-05): a folder counts as synced only once a scan has read all of it.

The first open of a folder scans it whole, and a folder with a `sync_state` row was taken as
scanned. But the row is written before the scan reads anything (its UIDVALIDITY), so a first scan
that failed left a folder that looked synced and was never scanned again. And warming
(`warm.warm_tick`) reads only a folder's newest messages, moving the watermark past the rest, so a
folder warmed first never showed its older mail.

`sync_state.scan_state` says how much of the folder has been read: `never`, `recent` (only its
newest messages, as warming reads them) or `synced` (every message up to the watermark). Only
`synced` skips the first open's scan. A row that has finished a sync before now (`last_sync`
set) is taken as `synced`, since no row says it was warmed.
"""
from mailmind.platform.db.migrations.m0001_m0020 import _addcol


def migrate(c):
    _addcol(c, "sync_state", "scan_state TEXT NOT NULL DEFAULT 'never'")
    c.execute("UPDATE sync_state SET scan_state='synced' WHERE last_sync IS NOT NULL AND scan_state='never'")
