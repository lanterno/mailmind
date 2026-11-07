"""Calendar's view of the database. It writes nothing: invites live in stored bodies, the Mailbox's, and what was sent
in the outbox, Compose's."""
from mailmind.content import mime
from mailmind.platform.db.uow import UnitOfWork

_uow = UnitOfWork()


def _body(c, pk):
    r = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (pk,)).fetchone()
    return r["raw"] if r else None


class StoredInvites:
    def __init__(self, folders):
        self._folders = folders

    def invite_parts(self, since, skipped_roles):
        """Every calendar part of mail fetched since then, newest first, as (row, text), leaving out folders that
        play those roles and any part that can't be read. One body at a time: they can be large."""
        with _uow.read() as c:
            rows = c.execute(
                """SELECT m.pk, m.message_key, m.account, m.folder, m.thread_id, a.part_id
                   FROM attachments a JOIN messages m ON m.pk=a.message_pk
                   WHERE m.body_fetched=1 AND m.ts>=? AND m.pending_removal IS NULL
                     AND (lower(a.content_type) LIKE '%calendar%' OR lower(a.filename) LIKE '%.ics')
                   ORDER BY m.ts DESC, m.pk DESC""",
                (since,)).fetchall()
            for r in rows:
                if self._folders.role_by_name(r["folder"]) in skipped_roles:
                    continue
                try:
                    payload, _ct = mime.extract_part(_body(c, r["pk"]) or b"", r["part_id"])
                    if payload is None:
                        continue
                    text = payload.decode("utf-8", "replace")
                except Exception:
                    continue
                yield r, text

    def body(self, pk):
        with _uow.read() as c:
            return _body(c, pk)

    def keys_sent(self, prefix):
        """The outbox's idempotency keys under this prefix, newest first."""
        with _uow.read() as c:
            return [r[0] for r in c.execute("SELECT idempotency_key FROM outbox WHERE substr(idempotency_key, 1, ?) = ? "
                                            "ORDER BY id DESC", (len(prefix), prefix))]
