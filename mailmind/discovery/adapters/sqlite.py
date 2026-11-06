"""Discovery's view of the database. It writes nothing here: `messages`, `summaries` and the rest are read models, and
which folder plays which role comes through `folders`."""
import sqlite3

from mailmind.discovery.adapters import fts
from mailmind.platform import settings
from mailmind.platform.db import database


def _conn():
    return database.connect(settings.current().db)


class StoredMail:
    def __init__(self, folders):
        self._folders = folders

    def _in_folders_playing(self, c, roles):
        """SQL and params for: a row's folder plays one of these roles."""
        pairs = [(r["account"], r["folder"]) for r in c.execute("SELECT DISTINCT account, folder FROM messages")
                 if self._folders.role(r["account"], r["folder"]) in roles]
        if not pairs:
            return "0=1", []                # a bare 0 in an ORDER BY would name a column
        return "(m.account, m.folder) IN (VALUES " + ",".join("(?,?)" for _ in pairs) + ")", [x for p in pairs for x in p]

    def search(self, spec, account, sort, limit, offset):
        """What the query finds, up to one more than `limit`, or None for one the index rejects. A message is one
        result however many folders hold it: its copy in the Inbox, else any but the one in All Mail, among the
        copies the query found."""
        match, where, params = fts.to_sql(spec)
        where = fts.visible(where) + ["m.pending_removal IS NULL"]      # on its way out by a queued move
        if account:
            where = where + ["m.account = ?"]
            params = params + [account]
        where_sql = (" AND " + " AND ".join(where)) if where else ""
        c = _conn()
        try:
            in_all, in_all_args = self._in_folders_playing(c, {"all"})
            columns = """m.pk,m.message_key,m.account,m.folder,m.from_addr,m.from_name,m.subject,m.date_iso,m.ts,
                         m.snippet,m.seen,m.flagged,m.has_attach,m.is_junk,m.bucket,s.importance,s.category,s.action"""
            if match is not None:               # every hit is scored anyway. bm25 can only be read where MATCH runs
                sql = (f"""SELECT * FROM (SELECT {columns}, h.score,
                            ROW_NUMBER() OVER (PARTITION BY COALESCE(m.message_key, -m.pk)
                                               ORDER BY m.folder='INBOX' DESC, {in_all}, m.pk DESC) AS nth
                       FROM (SELECT rowid AS pk, {fts.BM25} AS score FROM msg_fts WHERE msg_fts MATCH ?) h
                       JOIN messages m ON m.pk=h.pk
                       LEFT JOIN summaries s ON s.message_pk=m.pk
                       WHERE 1=1{where_sql})
                       WHERE nth=1 ORDER BY {'ts DESC' if sort=='date' else 'score ASC, ts DESC'}
                       LIMIT ? OFFSET ?""")
                return c.execute(sql, [*in_all_args, match, *params, limit + 1, offset]).fetchall()
            # newest first stops early, which sorting every copy wouldn't
            sql = (f"""SELECT {columns} FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
                   WHERE 1=1{where_sql} ORDER BY m.ts DESC, m.folder='INBOX' DESC, {in_all}, m.pk DESC""")
            rows, seen = [], set()
            for r in c.execute(sql, [*params, *in_all_args]):
                key = r["message_key"] or -r["pk"]
                if key not in seen:
                    seen.add(key)
                    if len(seen) > offset:
                        rows.append(r)
                    if len(rows) > limit:
                        break
            return rows
        except sqlite3.OperationalError:
            return None
        finally:
            c.close()

    def reach(self, spec, cap, apart):
        """-> (count, rows, matched), or None on a bad query. Mirrors `search`'s FROM and JOINs exactly."""
        match, where, params = fts.to_sql(spec)
        where = fts.visible(where)
        where_sql = (" AND " + " AND ".join(where)) if where else ""
        c = _conn()
        try:
            if match is not None:
                base = ("FROM msg_fts JOIN messages m ON m.pk=msg_fts.rowid "
                        "LEFT JOIN summaries s ON s.message_pk=m.pk "
                        f"WHERE msg_fts MATCH ? AND m.pending_removal IS NULL{where_sql}")
                bargs = [match, *params]
            else:
                base = ("FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk "
                        f"WHERE m.pending_removal IS NULL{where_sql}")
                bargs = [*params]
            not_apart, apart_args = self._in_folders_playing(c, apart)
            in_all, in_all_args = self._in_folders_playing(c, {"all"})
            try:
                matched = c.execute(f"SELECT COUNT(*) {base}", bargs).fetchone()[0]
                count = c.execute(f"SELECT COUNT(DISTINCT m.message_key) {base} AND NOT {not_apart}",
                                  [*bargs, *apart_args]).fetchone()[0]
                rows = c.execute(
                    "SELECT * FROM (SELECT m.message_key, m.folder, m.from_name, m.from_addr, m.subject, m.date_iso, m.ts, "
                    f"ROW_NUMBER() OVER (PARTITION BY m.message_key ORDER BY m.folder='INBOX' DESC, {in_all}, m.pk DESC) AS nth "
                    f"{base} AND NOT {not_apart}) WHERE nth=1 ORDER BY ts DESC LIMIT ?",
                    [*in_all_args, *bargs, *apart_args, cap]).fetchall()
            except sqlite3.OperationalError:
                return None
        finally:
            c.close()
        return count, rows, matched

    def total(self):
        c = _conn()
        try:
            return c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            c.close()
