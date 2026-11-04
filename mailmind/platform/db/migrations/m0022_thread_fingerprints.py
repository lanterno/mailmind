"""Migration 22 (2026-11-04): thread summaries fingerprinted by Message-ID.

A thread summary's fingerprint was a hash of its messages' pks and Message-IDs, so a renumbered
folder made every summary in it look stale ("thread grew"). The app now fingerprints a thread by
its messages' Message-IDs and the newest one's time (mailmind.assistant.domain.fingerprint). This
rewrites each stored fingerprint that still matches its thread as the thread is now, so a summary
that was fresh stays fresh without being paid for again. One that was already stale keeps its old
fingerprint, which matches nothing, and stays stale. Run again, it finds no old fingerprint that
matches, so it changes nothing.

The thread's rows and both fingerprints are frozen copies of the code at version 21
(summarize_thread._rows and _fingerprint, and fingerprint.of).
"""
import hashlib
import zlib


def _rows(c, account, thread_id):
    """The thread's messages, oldest first, one for each Message-ID: (pk, message_id, ts)."""
    seen, out = set(), []
    for pk, mid, ts in c.execute("SELECT pk, message_id, ts FROM messages WHERE account=? AND thread_id=? "
                                 "ORDER BY ts ASC, pk ASC", (account, thread_id)).fetchall():
        k = (mid or "").strip() or f"pk:{pk}"
        if k not in seen:
            seen.add(k)
            out.append((pk, mid, ts))
    return out


def _by_pk(rows):
    basis = "|".join(f"{pk}:{mid or ''}" for pk, mid, _ in rows) + f"#{len(rows)}"
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:32]


def _by_message_id(rows):
    names = sorted(mid or "" for _, mid, _ in rows)
    crc = zlib.crc32("\n".join(names).encode("utf-8", "replace"))
    return "t2:%d:%d:%08x" % (len(names), max(ts or 0 for _, _, ts in rows), crc)


def migrate(c):
    stored = c.execute("SELECT account, thread_id, scope, fingerprint FROM thread_summaries").fetchall()
    for account, thread_id, scope, fp in stored:
        rows = _rows(c, account, thread_id)
        if rows and fp == _by_pk(rows):
            c.execute("UPDATE thread_summaries SET fingerprint=? WHERE account=? AND thread_id=? AND scope=?",
                      (_by_message_id(rows), account, thread_id, scope))
