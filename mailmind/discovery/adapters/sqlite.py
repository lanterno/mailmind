"""Discovery's view of the database. It writes nothing here: `messages`, `assessments` and the rest are read models, and
which folder plays which role comes through `folders`."""
import sqlite3

from mailmind.discovery.adapters import fts
from mailmind.discovery.domain import known, search
from mailmind.platform.db.uow import UnitOfWork

_uow = UnitOfWork()


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

    def search(self, spec, account, limit, offset):
        """What the query finds, up to one more than `limit`, or None for one the index rejects. A message is one
        result however many folders hold it: its copy in the Inbox, else any but the one in All Mail, among the
        copies the query found."""
        match, where, params = fts.to_sql(spec)
        where = fts.visible(where) + ["m.pending_removal IS NULL"]      # on its way out by a queued move
        if account:
            where = where + ["m.account = ?"]
            params = params + [account]
        where_sql = (" AND " + " AND ".join(where)) if where else ""
        with _uow.read() as c:
            try:
                in_all, in_all_args = self._in_folders_playing(c, {"all"})
                columns = """m.pk,m.message_key,m.account,m.folder,m.from_addr,m.from_name,m.subject,m.date_iso,m.ts,
                             m.snippet,m.seen,m.flagged,m.has_attach,m.is_junk,m.bucket,m.thread_id,s.importance,s.category,s.action"""
                if match is not None:               # every hit is scored anyway. bm25 can only be read where MATCH runs
                    sql = (f"""SELECT * FROM (SELECT {columns}, h.score,
                                ROW_NUMBER() OVER (PARTITION BY COALESCE(m.message_key, -m.pk)
                                                   ORDER BY m.folder='INBOX' DESC, {in_all}, m.pk DESC) AS nth
                           FROM (SELECT rowid AS pk, {fts.BM25} AS score FROM msg_fts WHERE msg_fts MATCH ?) h
                           JOIN messages m ON m.pk=h.pk
                           LEFT JOIN assessments s ON s.message_key=m.message_key
                           WHERE 1=1{where_sql})
                           WHERE nth=1 ORDER BY score ASC, ts DESC
                           LIMIT ? OFFSET ?""")
                    return c.execute(sql, [*in_all_args, match, *params, limit + 1, offset]).fetchall()
                # newest first stops early, which sorting every copy wouldn't
                sql = (f"""SELECT {columns} FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key
                       WHERE 1=1{where_sql} ORDER BY m.ts DESC, m.folder='INBOX' DESC, {in_all}, m.pk DESC""")
                return search.first_copies(c.execute(sql, [*params, *in_all_args]), limit, offset)
            except sqlite3.OperationalError:
                return None

    def for_asking(self, match, accounts, limit):
        """What a question to Ask reads, or None for one the index rejects: what `match` finds, best first, or with
        nothing to match the newest mail. `accounts` is the one mailbox asked about, or a list of every enabled one,
        and an empty list matches nothing."""
        if isinstance(accounts, str):
            acct_clause, acct_params = "m.account = ?", [accounts]
        elif accounts:
            acct_clause, acct_params = "m.account IN (%s)" % ",".join("?" * len(accounts)), accounts
        else:
            acct_clause, acct_params = "1=0", []
        cols = ("m.pk, m.message_key, m.account, m.folder, m.from_name, m.from_addr, m.subject, m.snippet, "
                "m.body_text, m.date_iso, s.summary AS ai_summary")
        if match:
            sql = (f"SELECT {cols}, {fts.BM25} AS score "
                   "FROM msg_fts JOIN messages m ON m.pk=msg_fts.rowid "
                   "LEFT JOIN assessments s ON s.message_key=m.message_key "
                   f"WHERE msg_fts MATCH ? AND {acct_clause} AND m.pending_removal IS NULL "
                   "ORDER BY score ASC, m.ts DESC LIMIT ?")
            args = [match, *acct_params, limit]
        else:                                                 # no content terms → recent mail
            sql = (f"SELECT {cols} FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key "
                   f"WHERE {acct_clause} AND m.pending_removal IS NULL ORDER BY m.ts DESC LIMIT ?")
            args = [*acct_params, limit]
        with _uow.read() as c:
            try:
                return c.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return None

    def reach(self, spec, cap, apart):
        """-> (count, rows, matched), or None on a bad query. Mirrors `search`'s FROM and JOINs exactly."""
        match, where, params = fts.to_sql(spec)
        where = fts.visible(where)
        where_sql = (" AND " + " AND ".join(where)) if where else ""
        with _uow.read() as c:
            if match is not None:
                base = ("FROM msg_fts JOIN messages m ON m.pk=msg_fts.rowid "
                        "LEFT JOIN assessments s ON s.message_key=m.message_key "
                        f"WHERE msg_fts MATCH ? AND m.pending_removal IS NULL{where_sql}")
                bargs = [match, *params]
            else:
                base = ("FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key "
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
        return count, rows, matched

    def total(self):
        with _uow.read() as c:
            try:
                return c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            except sqlite3.OperationalError:
                return 0

    def attachments(self, accounts):
        accounts = list(accounts)
        ph = ",".join("?" for _ in accounts)
        with _uow.read() as c:
            return c.execute(
                "SELECT a.message_pk pk, m.message_key, a.part_id, a.filename, a.content_type, a.size, m.message_id, "
                "       m.account, m.from_name, m.from_addr, m.subject, m.date_iso, m.ts, m.folder "
                "FROM attachments a JOIN messages m ON m.pk=a.message_pk "
                "WHERE a.is_inline=0 AND a.filename IS NOT NULL AND a.filename!='' AND m.pending_removal IS NULL "
                f"  AND m.account IN ({ph}) "
                "ORDER BY m.ts DESC, a.id", accounts).fetchall()   # one order, or a page could repeat or skip a file

    def senders_like(self, q):
        with _uow.read() as c:
            base = ("SELECT from_addr AS addr, MAX(from_name) AS name, COUNT(*) AS n, MAX(ts) AS last "
                    "FROM messages WHERE from_addr IS NOT NULL AND from_addr != '' ")
            params = []
            if q:
                base += "AND (lower(from_addr) LIKE ? OR lower(from_name) LIKE ?) "
                params += [f"%{q}%", f"%{q}%"]
            base += "GROUP BY lower(from_addr) ORDER BY n DESC, last DESC LIMIT 8"
            return c.execute(base, params).fetchall()

    def card(self, a):
        with _uow.read() as c:
            agg = c.execute("SELECT COUNT(*) n, MAX(ts) last, MIN(ts) first, MAX(from_name) name, "
                            "SUM(CASE WHEN answered=1 THEN 1 ELSE 0 END) replied "
                            "FROM messages WHERE lower(from_addr)=?", (a,)).fetchone()
            accts = [r[0] for r in c.execute(
                "SELECT account, COUNT(*) k FROM messages WHERE lower(from_addr)=? GROUP BY account "
                "ORDER BY k DESC", (a,)).fetchall()]
            recent = c.execute(
                "SELECT pk, message_key, folder, account, subject, thread_id, ts, date_iso, seen FROM messages "
                "WHERE lower(from_addr)=? ORDER BY ts DESC LIMIT 6", (a,)).fetchall()
        return agg, accts, recent

    def known_facts(self, forms, min_history):
        with _uow.read() as c:
            holding = [r[0] for r in c.execute("SELECT DISTINCT account FROM messages") if r[0]]
            senders, synced, draft_folders, copies, envelopes = [], [], set(), [], []
            if forms:
                ph = ",".join("?" * len(forms))
                senders = [(r[0], r[1]) for r in c.execute(
                    f"SELECT DISTINCT lower(from_addr), folder FROM messages WHERE lower(from_addr) IN ({ph}) "
                    "AND is_junk=0", forms)]
                synced = [(r[0], r[1]) for r in c.execute("SELECT account, folder FROM sync_state")]
                draft_folders = {r[0] for r in c.execute(
                    "SELECT DISTINCT folder FROM drafts WHERE folder IS NOT NULL")}
                copies = c.execute(
                    "SELECT r.addr, m.account, m.folder, m.is_junk, lower(m.from_addr) sender FROM message_recipients r "
                    f"JOIN messages m ON m.pk=r.message_pk WHERE r.addr IN ({ph}) AND COALESCE(m.draft,0)=0", forms).fetchall()
                envelopes = [r[0] for r in c.execute("SELECT envelope FROM outbox WHERE delivered=1")]
            thin = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM messages WHERE is_junk=0 LIMIT ?)",
                             (min_history,)).fetchone()[0] < min_history
        return known.Facts(holding, senders, synced, draft_folders, copies, envelopes, thin)
