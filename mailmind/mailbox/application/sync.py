"""Mail into SQLite, whole or incrementally by UID watermark: fetch the headers, classify, pull the body text of what
isn't junk. What a sync finds gone (reconcile_account), and a body read on demand, are here too."""
import email
import imaplib
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime
from typing import Any

from mailmind.content import mime
from mailmind.content.mime import Parsed, dec
from mailmind.content.text import body_text_of, text_body
from mailmind.kernel.row import Connection, Row
from mailmind.mailbox.application.pool import Pool
from mailmind.mailbox.application.ports import (Account, Accounts, Attention, Classifier, Index, OpenSession, Pruned,
                                                Stored, StoredMail)
from mailmind.mailbox.application.session import MailSession
from mailmind.mailbox.domain.events import FlagsChanged
from mailmind.platform import bus
from mailmind.platform.logging import get_logger

log = get_logger("ingest")
CUTOFF_DAYS = 90
HEADER_FIELDS = ("FROM", "TO", "CC", "SUBJECT", "DATE", "MESSAGE-ID", "IN-REPLY-TO", "REFERENCES",
                 "LIST-UNSUBSCRIBE", "LIST-UNSUBSCRIBE-POST", "LIST-ID")
_FLAG_COLS = ("seen", "flagged", "answered", "draft", "deleted")


class Ingest:
    CUTOFF_DAYS = CUTOFF_DAYS
    FLAG_REFRESH = 500      # the newest stored UIDs whose flags each incremental sync reads again
    FLAG_BATCH = 500        # how many UIDs one flag read names

    def __init__(self, store: StoredMail, accounts: Accounts, open_session: OpenSession, pool: Pool, curation: Classifier,
                 attention: Attention, index: Index):
        self._store = store
        self._accounts = accounts
        self._open_session = open_session
        self._pool = pool
        self._curation = curation
        self._attention = attention
        self._index = index

    def _flags_of(self, flags: Iterable[str]) -> dict[str, int]:
        """The local flag columns from a message's IMAP flags."""
        f = {x.lower() for x in flags}
        return {"seen": int("\\seen" in f), "flagged": int("\\flagged" in f),
                "answered": int("\\answered" in f), "draft": int("\\draft" in f),
                "deleted": int("\\deleted" in f)}


    def _flag_rows(self, c: Connection, email: str, folder: str, uids: Sequence[int] | None) -> list[Row]:
        """The stored rows to compare with the server's flags: those of `uids`, or with None the newest."""
        if uids is None:
            return self._store.flag_rows_newest(c, email, folder, self.FLAG_REFRESH)
        rows: list[Row] = []
        for start in range(0, len(uids), self.FLAG_BATCH):
            part = uids[start:start + self.FLAG_BATCH]
            rows += self._store.flag_rows_of(c, email, folder, part)
        return rows


    def _refresh_flags(self, session: MailSession, acct: Account, folder: str, modseq: int | None = None) -> int:
        """Read the flags of the folder's mail again and store what changed, so mail read, starred or
        answered in another client shows so here. `modseq` is the HIGHESTMODSEQ the select just said, if
        the server keeps one: with the mark the last read left, it says whether there is anything to read
        and which mail, however old. Otherwise, or if the server won't say, the FLAG_REFRESH newest are read.
        A row whose flags changed here since they were read (a click, under the IMAP gate this sync doesn't
        take) keeps its own, and so does one with a flag change still queued, or on its way out by a queued
        move: the mark stays behind those two, since a refusal would put back flags this read skipped.
        Returns how many rows changed."""
        email = acct["email"]
        c = self._store.conn()
        try:
            since = self._store.get_modseq(c, email, folder) if modseq else None
        finally:
            c.close()
        if modseq and since == modseq:
            return 0                                     # the server says nothing changed
        behind = since if modseq and since and since < modseq else None
        uids: list[int] | None = None
        ask = behind is not None
        if behind is not None:
            try:
                uids = session.uids_changed_since(behind)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                log.warning("SEARCH MODSEQ refused %(account)s: reading the newest instead", {"account": email})
        c = self._store.conn()
        try:
            waiting = self._store.flag_changes_waiting(c, email)
            rows = self._flag_rows(c, email, folder, uids)    # before the flags are read: see above
        finally:
            c.close()
        now: dict[int, frozenset[str]] = {}
        try:
            for start in range(0, len(rows), self.FLAG_BATCH):
                now.update(session.flags([r["uid"] for r in rows[start:start + self.FLAG_BATCH]]))
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:                      # a refused FETCH changes nothing here
            return 0
        held = [r for r in rows if r["pending_removal"] is not None or r["message_id"] in waiting]
        skip = {r["pk"] for r in held}
        moved = [(r, new) for r in rows if r["uid"] in now and r["pk"] not in skip
                 for new in [self._flags_of(now[r["uid"]])] if any(new[k] != r[k] for k in _FLAG_COLS)]
        changed = 0
        c = self._store.conn()
        try:
            for r, new in moved:                         # each only if it still holds what was read
                changed += self._store.set_flags_if_still(c, r["pk"], new, r)
            if modseq and not held and (uids is not None or not ask):
                self._store.set_modseq(c, email, folder, modseq)
            c.commit()
        finally:
            c.close()
        if changed:
            bus.publish(FlagsChanged(acct["email"], folder, changed))
        return changed


    def _process(self, session: MailSession, acct: Account, uids: Sequence[int], folder: str = "INBOX",
                 with_body: bool = False, advance: bool = True, stored: list[int] | None = None) -> Stored:
        """Store ALL of the given UIDs (junk kept as a badge, with flags) for the folder `session`
        has selected. Bodies are fetched only when with_body (INBOX, for the AI snippet);
        other folders get headers+flags only (fast) and fetch the body lazily on open.
        advance=False stores them without touching the folder's watermark: only a sync that has
        seen every UID up to a point may say the folder is synced to there.
        `stored` gets the pks of what threading has yet to hear of."""
        rows: list[dict[str, Any]] = []
        max_uid, kept, junk = 0, 0, 0
        gmail = self._accounts.profile(acct).gmail_threads            # only Gmail understands X-GM-THRID
        if uids:
            for h in session.headers(uids, HEADER_FIELDS, gmail_thread=gmail):
                uid = h.uid
                max_uid = max(max_uid, uid)
                flags = self._flags_of(h.flags)
                msg = email.message_from_bytes(h.raw)
                name, addr = mime.sender_of(msg)
                has_lu = bool(msg.get("List-Unsubscribe") or msg.get("List-ID"))
                subj = dec(msg.get("Subject", ""))
                b, conf, reason = self._curation.classify(addr.lower(), subj, dec(name), has_lu)
                isj = 1 if self._curation.is_junk(b) else 0
                junk += isj
                kept += (1 - isj)
                try:
                    dt = parsedate_to_datetime(msg.get("Date", ""))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    iso, ts = dt.isoformat(), int(dt.timestamp())
                except Exception:
                    iso, ts = None, 0
                rows.append({"uid": uid, "from_addr": addr.lower(), "from_name": dec(name),
                             "unsub": mime.list_unsub(dec(msg.get("List-Unsubscribe")),
                                                           dec(msg.get("List-Unsubscribe-Post"))),
                             "subject": subj, "date_iso": iso, "ts": ts, "bucket": b,
                             "confidence": conf, "reason": reason, "is_junk": isj, "flags": flags,
                             "message_id": (msg.get("Message-ID") or "").strip(),
                             "gmail_thread": h.gmail_thread,
                             "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                             "refs": (msg.get("References") or "").replace("\n", " ").strip(),
                             "recipient_addrs": mime.recipient_addrs(msg),
                             "recipients": (dec(msg.get("To", "")) + " " + dec(msg.get("Cc", ""))).strip()[:self._store.RECIPIENTS_CHARS]})
        c = self._store.conn()
        for r in rows:
            snippet, invite = "", None
            if with_body and not r["is_junk"]:
                try:
                    try:
                        raw = session.body(r["uid"]) or b""
                    except imaplib.IMAP4.abort:
                        raise
                    except imaplib.IMAP4.error:   # refused (vs a genuinely empty body): surface it
                        log.warning("body fetch %(account)s uid %(id)s: NO", {"account": acct["email"], "id": r["uid"]})
                        raw = b""
                    msg = email.message_from_bytes(raw)
                    snippet = text_body(msg)
                    invite = raw if mime.has_invite(msg) else None
                except Exception:
                    snippet = ""
            pk = self._store.upsert_message(c, {
                "account": acct["email"], "kind": acct["kind"], "folder": folder,
                "uid": r["uid"], "message_id": r["message_id"], "gmail_thread": r["gmail_thread"],
                "from_addr": r["from_addr"], "from_name": r["from_name"], "subject": r["subject"],
                "date_iso": r["date_iso"], "ts": r["ts"], "snippet": snippet,
                "bucket": r["bucket"], "confidence": r["confidence"], "reason": r["reason"],
                "is_junk": r["is_junk"],
            })
            self._store.set_local_flags(c, pk, **r["flags"])
            self._store.set_list_unsub(c, pk, r["unsub"]["raw"], r["unsub"]["method"])
            self._store.set_recipients(c, pk, r["recipient_addrs"])
            if stored is not None and self._store.needs_threading(c, pk, r["in_reply_to"], r["refs"]):
                stored.append(pk)
            c.execute("UPDATE messages SET in_reply_to=?, refs=?, recipients=? WHERE pk=?",
                      (r["in_reply_to"], r["refs"], r["recipients"], pk))
            self._index.index_message(c, pk)               # refresh FTS so recipients is searchable immediately
            if invite:                           # Today & next reads invites from stored bodies only
                try:
                    self._keep_body(c, pk, invite, mime.parse(invite))
                except Exception as e:
                    log.warning("keep invite %(account)s uid %(id)s", {"account": acct["email"], "id": r["uid"]}, exc_info=e)
            c.commit()
        if advance:
            self._store.set_watermark(c, acct["email"], max_uid, datetime.now(timezone.utc).isoformat(), folder)
            c.commit()
        c.close()
        return kept, junk, max_uid


    def reclassify(self, token: str) -> int:
        """A rule on `token`, a sender or a domain, changed: classify again the stored mail it covers, as
        mail arriving now would be. Only a row a rule decides now or decided before: what else the
        classifier read at ingest wasn't all kept (List-Unsubscribe), so one that no rule had a say in is
        left as ingest judged it. Returns how many rows changed."""
        token = token.strip().lower()
        c = self._store.conn()
        try:
            cols = "pk, from_addr, from_name, subject, bucket, reason, list_unsub_method"
            if "@" in token:
                rows = c.execute(f"SELECT {cols} FROM messages WHERE lower(from_addr)=?", (token,)).fetchall()
            else:                                        # the domain and its sub-domains; the classifier decides
                rows = c.execute(f"SELECT {cols} FROM messages WHERE lower(from_addr) LIKE ? OR lower(from_addr) LIKE ?",
                                 (f"%@{token}", f"%.{token}")).fetchall()
            changed = 0
            for r in rows:
                bucket, confidence, reason = self._curation.classify((r["from_addr"] or "").lower(), r["subject"] or "",
                                                                r["from_name"] or "",
                                                                (r["list_unsub_method"] or "none") != "none")
                if not (self._curation.by_rule(reason) or self._curation.by_rule(r["reason"])):
                    continue
                if (bucket, reason) != (r["bucket"], r["reason"]):
                    c.execute("UPDATE messages SET bucket=?, confidence=?, reason=?, is_junk=? WHERE pk=?",
                              (bucket, confidence, reason, int(self._curation.is_junk(bucket)), r["pk"]))
                    changed += 1
            c.commit()
            return changed
        finally:
            c.close()


    def sync_folder(self, acct: Account, folder: str, full: bool = False, recent: int | None = None) -> Stored:
        """Sync one folder (UIDVALIDITY-checked). full=True re-scans the whole folder;
        otherwise pulls only UIDs above the per-folder watermark. Stores everything.
        recent=N bounds a first/full scan to the most recent N UIDs (used by background folder
        warming so a huge Sent/Archive doesn't pull thousands in one pass); incremental syncs
        are already watermark-bounded and ignore it.
        The folder's scan_state says what the scans so far have read: a whole scan makes it
        'synced', one cut to the newest N 'recent', and an incremental sync keeps it as it was.
        A scan that fails leaves it as it was too, so a first open that failed scans again."""
        s = self._open_session(acct)
        try:
            condstore = s.enable_condstore()
            selected = s.select(folder)
            uidv = selected.uidvalidity
            modseq = selected.highest_modseq if condstore else None
            c = self._store.conn()
            known = self._store.get_uidvalidity(c, acct["email"], folder)
            if uidv and known and uidv != known:
                self._store.reset_folder(c, acct["email"], folder)  # server reset UIDs -> re-sync clean
                c.commit()
                full = True
            if uidv:
                self._store.set_uidvalidity(c, acct["email"], folder, uidv)
                c.commit()
            wm = self._store.get_watermark(c, acct["email"], folder)
            c.close()
            scan = None                                    # the scan_state this sync leaves
            if full or wm == 0:
                uids = s.uids("ALL")
                scan = "synced"
                if recent and len(uids) > recent:
                    uids = uids[-recent:]      # the most recent N only
                    scan = "recent"
            else:
                uids = [u for u in s.uids("UID", f"{wm + 1}:*") if u > wm]   # N:* always matches the last
            stored: list[int] = []
            result = self._process(s, acct, uids, folder, with_body=(folder.upper() == "INBOX"), stored=stored)
            if scan:
                c = self._store.conn()
                self._store.set_scan_state(c, acct["email"], folder, scan)
                c.commit()
                c.close()
            if not full and wm:
                self._refresh_flags(s, acct, folder, modseq)
            c = self._store.conn()
            try:                                           # what fails here rolls back, and holds no lock past this
                if scan == "synced" and modseq:            # a whole scan has just read every flag
                    self._store.set_modseq(c, acct["email"], folder, modseq)
                self._store.thread_stored(c, acct["email"], stored)   # new mail joins its conversations
                self._attention.on_ingest(c, acct["email"])  # keep new members of muted threads silent
                self._attention.resolve_followups(c, acct["email"])     # new mail / answered-flag resolves waiting threads
                c.commit()
            finally:
                c.close()
            return result
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def ingest_message(self, acct: Account, folder: str, message_id: str | None) -> int:
        """Force-ingest ONE message identified by RFC822 Message-ID into `folder`,
        bypassing the incremental watermark. Needed by undo: a re-labeled Gmail message
        keeps its original (<= watermark) UID, so a normal incremental sync skips it.
        The watermark stays where it was. A message moved back gets a new, highest UID, and
        raising the mark to it would skip mail that arrived since the last sync; in a folder
        never opened, it would also mark the folder synced, so its first full sync never ran.
        Returns the number of messages stored (0 if not found)."""
        if not message_id:
            return 0
        s = self._open_session(acct)
        try:
            s.select(folder)
            try:
                uid = s.by_message_id(message_id)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:                  # a refused search finds nothing
                uid = None
            if uid is None:
                return 0
            stored: list[int] = []
            self._process(s, acct, [uid], folder, with_body=(folder.upper() == "INBOX"), advance=False, stored=stored)
            c = self._store.conn()
            try:
                self._store.thread_stored(c, acct["email"], stored)
                self._attention.on_ingest(c, acct["email"])
                self._attention.resolve_followups(c, acct["email"])     # new mail / answered-flag resolves waiting threads
                c.commit()
            finally:
                c.close()
            return 1
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def folder_synced(self, acct_email: str, folder: str) -> bool:
        """Whether a scan has read the whole folder, so its first open needn't."""
        c = self._store.conn()
        try:
            return self._store.get_scan_state(c, acct_email, folder) == "synced"
        finally:
            c.close()


    def ingest_account(self, acct: Account, days: int = CUTOFF_DAYS) -> Stored:
        s = self._open_session(acct)
        try:
            s.select("INBOX")
            if days >= 3650:
                uids = s.uids("ALL")
            else:
                uids = s.uids("SINCE", (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y"))
            result = self._process(s, acct, uids, "INBOX", with_body=True)
            c = self._store.conn()                       # the owner chose how far back: INBOX counts as read
            self._store.set_scan_state(c, acct["email"], "INBOX", "synced")
            c.commit()
            c.close()
            return result
        finally:
            s.logout()


    def fetch_body(self, pk: int) -> Parsed | None:
        """Lazily fetch + store the raw RFC822 for one message (read-only PEEK), record
        attachment metadata, and return the parsed body. Idempotent. Returns parsed dict
        or None if the message/row is gone."""
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        fetched = row and row["body_fetched"]
        raw = self._store.get_body(c, pk) if fetched else None
        c.close()
        if not row:
            return None
        if not fetched or not raw:
            acct = self._accounts.get(row["account"])
            # READ-ONLY borrow: pooled when MAILMIND_POOL enables this account, else a fresh session,
            # logged out after. Mutations never use the pool.
            with self._pool.borrow(acct) as s:
                s.select(row["folder"])
                try:
                    raw = s.body(row["uid"]) or b""
                except imaplib.IMAP4.abort:
                    raise
                except imaplib.IMAP4.error:       # refused: nothing to show, and nothing stored
                    raw = b""
            if not raw:
                return mime.parse(b"")
            parsed = mime.parse(raw)
            c = self._store.conn()
            self._keep_body(c, pk, raw, parsed)
            c.commit()
            c.close()
            return parsed
        parsed = mime.parse(raw)
        self._record_parts(pk, parsed)
        return parsed


    def _record_parts(self, pk: int, parsed: Parsed) -> None:
        """Rows a parsing change took away (migration 28) come back as the message is opened."""
        if not parsed["attachments"]:
            return
        c = self._store.conn()
        try:
            if not c.execute("SELECT 1 FROM attachments WHERE message_pk=?", (pk,)).fetchone():
                self._insert_parts(c, pk, parsed)
                c.commit()
        finally:
            c.close()


    def _insert_parts(self, c: Connection, pk: int, parsed: Parsed) -> None:
        for a in parsed["attachments"]:
            c.execute("""INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,is_inline)
                         VALUES(?,?,?,?,?,?,?)""",
                      (pk, a["part_id"], a["filename"], a["content_type"], a["size"],
                       a["content_id"], a["is_inline"]))


    def _keep_body(self, c: Connection, pk: int, raw: bytes, parsed: Parsed) -> None:
        """Store one message's raw RFC822 with what opening it records: attachment metadata, the
        parsed recipients and the full-text body. The caller commits."""
        self._store.store_body(c, pk, raw, datetime.now(timezone.utc).isoformat())
        c.execute("DELETE FROM attachments WHERE message_pk=?", (pk,))
        self._insert_parts(c, pk, parsed)
        if parsed["attachments"]:
            c.execute("UPDATE messages SET has_attach=1 WHERE pk=?", (pk,))
        c.execute("UPDATE messages SET recipients=? WHERE pk=?", (self._store.recipients_of(parsed), pk))
        self._store.set_recipients(c, pk, mime.recipient_addrs(BytesHeaderParser().parsebytes(raw)))
        self._store.fts_set_body(c, pk, body_text_of(parsed))   # full body + recipients now searchable


    def reconciled_folders(self, email: str) -> list[str]:
        """The folders reconcile looks at for an account: each one a sync has stored (it has a
        sync_state row), INBOX first."""
        c = self._store.conn()
        try:
            folders = [r["folder"] for r in c.execute(
                "SELECT folder FROM sync_state WHERE account=? ORDER BY folder <> 'INBOX', folder", (email,))]
        finally:
            c.close()
        return folders or ["INBOX"]


    def reconcile_account(self, acct: Account, force: bool = False, dry_run: bool = False, folder: str = "INBOX") -> Pruned:
        """Prune `folder`'s rows whose UID is no longer live, and stamp survivors verified.
        Pure prune — preserves summaries of survivors and never lowers the watermark.
        SAFETY: never deletes on an IMAP error, and treats an OK-but-empty SEARCH
        against a non-empty stored set as 'unknown' (prunes nothing) unless force.
        Only rows stored before the SEARCH may go: the 300 s sync passes no IMAP gate, so
        it can store mail while this runs, and that mail is missing from the SEARCH's answer.
        dry_run computes the ghost count without changing anything."""
        from datetime import datetime as _dt
        res: Pruned = {"account": acct["email"], "live": 0, "stored": 0, "ghosts": 0,
                       "pruned": 0, "error": None}
        c = self._store.conn()
        stored_uids = [r["uid"] for r in c.execute(
            "SELECT uid FROM messages WHERE account=? AND folder=?", (acct["email"], folder))]
        c.close()
        res["stored"] = len(stored_uids)
        try:
            s = self._open_session(acct, timeout=15)   # background/maintenance path: a hung account must
            try:                                          # not pin the IMAP gate for the interactive 60s
                uidv = s.select(folder).uidvalidity
                live = set(s.uids("ALL"))                 # a refused SEARCH raises: nothing is pruned
            finally:
                try:
                    s.logout()                            # ALWAYS close the socket (no leak on select/SEARCH error)
                except Exception:
                    pass
        except Exception as e:
            log.warning("reconcile: an account failed", exc_info=e)
            res["error"] = type(e).__name__      # what a server said stays out of the answer
            return res  # never prune on error
        # UIDVALIDITY guard: if the server renumbered UIDs (mailbox recreation / Bridge
        # re-index), `live` is in a DIFFERENT uid space than the stored uids — comparing
        # them would prune the ENTIRE real INBOX as "ghosts". Never prune here; sync_folder
        # detects the change and does the reset + full re-sync on its own pass.
        c = self._store.conn()
        stored_uidv = self._store.get_uidvalidity(c, acct["email"], folder)
        c.close()
        if uidv and stored_uidv and uidv != stored_uidv:
            res["error"] = f"UIDVALIDITY changed ({stored_uidv}->{uidv}) — prune skipped"
            return res
        res["live"] = len(live)
        res["ghosts"] = sum(1 for u in stored_uids if u not in live)
        if not live and res["stored"] > 0 and not force:
            res["error"] = "empty SEARCH against non-empty store — skipped (use force)"
            return res
        if dry_run:
            return res
        when = _dt.now(timezone.utc).isoformat()
        c = self._store.conn()
        pruned, _ = self._store.reconcile(c, acct["email"], folder, live, when, among=set(stored_uids))
        c.commit()
        c.close()
        res["pruned"] = pruned
        return res


    def sync_new(self, acct: Account, folder: str = "INBOX") -> Stored:
        """Incremental sync of one folder: only UIDs above the stored watermark."""
        return self.sync_folder(acct, folder, full=False)
