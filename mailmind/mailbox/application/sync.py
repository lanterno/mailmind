"""Mail into SQLite, whole or incrementally by UID watermark: fetch the headers, classify, pull the body text of what
isn't junk. A body read on demand is here too."""
import email
import imaplib
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime
from typing import Any

from mailmind.content import mime
from mailmind.content.mime import Parsed, dec
from mailmind.content.text import body_text_of, text_body
from mailmind.kernel.ids import INBOX, AccountId, FolderName
from mailmind.kernel.row import Connection, Row
from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.pool import Pool
from mailmind.mailbox.application.ports import (Account, Accounts, Attention, Classifier, Index, OpenSession, Stored,
                                                StoredMail)
from mailmind.mailbox.application.session import MailSession
from mailmind.mailbox.domain import flags as mail_flags
from mailmind.mailbox.domain import sync_plan
from mailmind.mailbox.domain.events import FlagsChanged
from mailmind.platform import bus
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("ingest")
CUTOFF_DAYS = 90
HEADER_FIELDS = ("FROM", "TO", "CC", "SUBJECT", "DATE", "MESSAGE-ID", "IN-REPLY-TO", "REFERENCES",
                 "LIST-UNSUBSCRIBE", "LIST-UNSUBSCRIBE-POST", "LIST-ID")


class Ingest:
    CUTOFF_DAYS = CUTOFF_DAYS
    FLAG_REFRESH = sync_plan.FLAG_REFRESH
    FLAG_BATCH = 500        # how many UIDs one flag read names

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, open_session: OpenSession, pool: Pool,
                 curation: Classifier, attention: Attention, index: Index):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._open_session = open_session
        self._pool = pool
        self._curation = curation
        self._attention = attention
        self._index = index

    def _flag_rows(self, c: Connection, email: AccountId, folder: FolderName, uids: Sequence[int] | None) -> list[Row]:
        """The stored rows to compare with the server's flags: those of `uids`, or with None the newest."""
        if uids is None:
            return self._store.flag_rows_newest(c, email, folder, self.FLAG_REFRESH)
        rows: list[Row] = []
        for start in range(0, len(uids), self.FLAG_BATCH):
            part = uids[start:start + self.FLAG_BATCH]
            rows += self._store.flag_rows_of(c, email, folder, part)
        return rows


    def _refresh_flags(self, session: MailSession, acct: Account, folder: FolderName, modseq: int | None = None) -> int:
        """Read the flags of the folder's mail again and store what changed, so mail read, starred or
        answered in another client shows so here. `modseq` is the HIGHESTMODSEQ the select just said, if
        the server keeps one: with the mark the last read left, it says whether there is anything to read
        and which mail, however old. Otherwise, or if the server won't say, the FLAG_REFRESH newest are read.
        A row whose flags changed here since they were read (a click, under the IMAP gate this sync doesn't
        take) keeps its own, and so does one with a flag change still queued, or on its way out by a queued
        move: the mark stays behind those two, since a refusal would put back flags this read skipped.
        Returns how many rows changed."""
        email = acct["email"]
        with self._uow.read() as c:
            since = self._store.get_modseq(c, email, folder) if modseq else None
        plan = sync_plan.plan_flags(modseq, since)
        if plan.unchanged:
            return 0                                     # the server says nothing changed
        uids: list[int] | None = None
        if plan.changed_since is not None:
            try:
                uids = session.uids_changed_since(plan.changed_since)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                log.warning("SEARCH MODSEQ refused %(account)s: reading the newest instead", {"account": email})
        with self._uow.read() as c:
            waiting = self._store.flag_changes_waiting(c, email)
            rows = self._flag_rows(c, email, folder, uids)    # before the flags are read: see above
        now: dict[int, frozenset[str]] = {}
        try:
            for start in range(0, len(rows), self.FLAG_BATCH):
                now.update(session.flags([r["uid"] for r in rows[start:start + self.FLAG_BATCH]]))
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:                      # a refused FETCH changes nothing here
            return 0
        read = mail_flags.read_against(rows, now, waiting)
        changed = 0
        mark = plan.mark(held_back=read.held_back, searched=uids is not None)
        if read.changed or mark:                         # a server with no modseq and nothing new takes no lock
            with self._uow.write() as c:
                for r, new in read.changed:              # each only if it still holds what was read
                    changed += self._store.set_flags_if_still(c, r["pk"], new, r)
                if mark:
                    self._store.set_modseq(c, email, folder, mark)
        if changed:
            bus.publish(FlagsChanged(acct["email"], folder, changed))
        return changed


    def _process(self, session: MailSession, acct: Account, uids: Sequence[int], folder: FolderName = INBOX,
                 with_body: bool = False, advance: bool = True, stored: list[int] | None = None, arriving: bool = False) -> Stored:
        """Store ALL of the given UIDs (junk kept as a badge, with flags) for the folder `session`
        has selected. Bodies are fetched only when with_body (INBOX, for the AI snippet);
        other folders get headers+flags only (fast) and fetch the body lazily on open.
        advance=False stores them without touching the folder's watermark: only a sync that has
        seen every UID up to a point may say the folder is synced to there.
        `stored` gets the pks of what threading has yet to hear of.
        arriving=True says this mail is new to the folder, so the person's filters speak for what MailMind has never seen before."""
        rows: list[dict[str, Any]] = []
        max_uid, kept, junk = 0, 0, 0
        gmail = self._accounts.profile(acct).gmail_threads            # only Gmail understands X-GM-THRID and X-GM-LABELS
        if uids:
            for h in session.headers(uids, HEADER_FIELDS, gmail_thread=gmail, gmail_labels=gmail):
                uid = h.uid
                max_uid = max(max_uid, uid)
                flags = mail_flags.columns_of(h.flags)
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
                             "gmail_labels": h.gmail_labels,
                             "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                             "refs": (msg.get("References") or "").replace("\n", " ").strip(),
                             "recipient_addrs": mime.recipient_addrs(msg),
                             "recipients": (dec(msg.get("To", "")) + " " + dec(msg.get("Cc", ""))).strip()[:self._store.RECIPIENTS_CHARS]})
        queued = False
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
            with self._uow.write() as c:
                news = arriving and not self._store.is_known(c, acct["email"], r["message_id"], r["from_addr"], r["date_iso"], r["subject"])
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
                if r["gmail_labels"] is not None:
                    self._store.set_labels(c, pk, r["gmail_labels"])
                if stored is not None and self._store.needs_threading(c, pk, r["in_reply_to"], r["refs"]):
                    stored.append(pk)
                self._store.set_ingested_headers(c, pk, r["in_reply_to"], r["refs"], r["recipients"])
                self._index.index_message(c, pk)           # refresh FTS so recipients is searchable immediately
                if news and self._apply_filters(c, pk):
                    queued = True
                if invite:                       # Today & next reads invites from stored bodies only
                    try:
                        self._keep_body(c, pk, invite, mime.parse(invite))
                    except Exception as e:
                        log.warning("keep invite %(account)s uid %(id)s", {"account": acct["email"], "id": r["uid"]}, exc_info=e)
        if queued:
            self._curation.flags_queued()
        if advance:
            with self._uow.write() as c:
                self._store.set_watermark(c, acct["email"], max_uid, datetime.now(timezone.utc).isoformat(), folder)
        return kept, junk, max_uid


    def _apply_filters(self, c: Connection, pk: int) -> bool:
        """A message never seen before, stored and indexed on this connection: what the person's filters ask of it goes through
        the command path, so the server hears. Stars and read marks only; no filter moves mail. Whether anything was queued."""
        wanted = self._curation.arrival_flags(c, pk)
        row = self._store.get_message(c, pk) if wanted else None
        if row is None:
            return False
        changes = mail_flags.still_to_say(wanted, row)
        return bool(changes) and self._store.queue_flags(c, row, changes, "filter", now_iso())


    def reclassify(self, token: str) -> int:
        """A rule on `token`, a sender or a domain, changed: classify again the stored mail it covers, as
        mail arriving now would be. Only a row a rule decides now or decided before: what else the
        classifier read at ingest wasn't all kept (List-Unsubscribe), so one that no rule had a say in is
        left as ingest judged it. Returns how many rows changed."""
        token = token.strip().lower()
        with self._uow.read() as c:
            if "@" in token:
                rows = self._store.classified_from_address(c, token)
            else:                                        # the domain and its sub-domains; the classifier decides
                rows = self._store.classified_from_domain(c, token)
        changes: list[tuple[int, str, str, str, int]] = []
        for r in rows:
            bucket, confidence, reason = self._curation.classify((r["from_addr"] or "").lower(), r["subject"] or "",
                                                            r["from_name"] or "",
                                                            (r["list_unsub_method"] or "none") != "none")
            if not (self._curation.by_rule(reason) or self._curation.by_rule(r["reason"])):
                continue
            if (bucket, reason) != (r["bucket"], r["reason"]):
                changes.append((r["pk"], bucket, confidence, reason, int(self._curation.is_junk(bucket))))
        if changes:
            with self._uow.write() as c:
                for pk, bucket, confidence, reason, is_junk in changes:
                    self._store.set_classification(c, pk, bucket, confidence, reason, is_junk)
        return len(changes)


    def sync_folder(self, acct: Account, folder: FolderName, full: bool = False, recent: int | None = None) -> Stored:
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
            with self._uow.read() as c:
                known = self._store.get_uidvalidity(c, acct["email"], folder)
                wm = self._store.get_watermark(c, acct["email"], folder)
            plan = sync_plan.plan_folder(uidvalidity=uidv, known_uidvalidity=known, watermark=wm, full=full, recent=recent)
            if plan.reset:
                with self._uow.write() as c:
                    self._store.reset_folder(c, acct["email"], folder)  # server reset UIDs -> re-sync clean
            if uidv:
                with self._uow.write() as c:
                    self._store.set_uidvalidity(c, acct["email"], folder, uidv)
            uids, scan = plan.to_fetch(s.uids(*plan.search))    # scan: the scan_state this sync leaves
            stored: list[int] = []
            arriving = not plan.whole and folder.upper() == "INBOX"     # a first or a whole scan reads what was already there
            result = self._process(s, acct, uids, folder, with_body=(folder.upper() == "INBOX"), stored=stored, arriving=arriving)
            if scan:
                with self._uow.write() as c:
                    self._store.set_scan_state(c, acct["email"], folder, scan)
            if plan.read_flags:
                self._refresh_flags(s, acct, folder, modseq)
            with self._uow.write() as c:                   # what fails here rolls back, and holds no lock past this
                if scan == sync_plan.WHOLE and modseq:     # a whole scan has just read every flag
                    self._store.set_modseq(c, acct["email"], folder, modseq)
                self._store.thread_stored(c, acct["email"], stored)   # new mail joins its conversations
                self._attention.on_ingest(c, acct["email"])  # keep new members of muted threads silent
                self._attention.resolve_followups(c, acct["email"])     # new mail / answered-flag resolves waiting threads
            return result
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def ingest_message(self, acct: Account, folder: FolderName, message_id: str | None) -> int:
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
            with self._uow.write() as c:
                self._store.thread_stored(c, acct["email"], stored)
                self._attention.on_ingest(c, acct["email"])
                self._attention.resolve_followups(c, acct["email"])     # new mail / answered-flag resolves waiting threads
            return 1
        finally:
            try:
                s.logout()
            except Exception:
                pass


    def folder_synced(self, acct_email: AccountId, folder: FolderName) -> bool:
        """Whether a scan has read the whole folder, so its first open needn't."""
        with self._uow.read() as c:
            return self._store.get_scan_state(c, acct_email, folder) == sync_plan.WHOLE


    def ingest_account(self, acct: Account, days: int = CUTOFF_DAYS) -> Stored:
        s = self._open_session(acct)
        try:
            s.select("INBOX")
            if days >= 3650:
                uids = s.uids("ALL")
            else:
                uids = s.uids("SINCE", (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y"))
            result = self._process(s, acct, uids, INBOX, with_body=True)
            with self._uow.write() as c:                 # the owner chose how far back: INBOX counts as read
                self._store.set_scan_state(c, acct["email"], INBOX, sync_plan.WHOLE)
            return result
        finally:
            s.logout()


    def fetch_body(self, pk: int) -> Parsed | None:
        """Lazily fetch + store the raw RFC822 for one message (read-only PEEK), record
        attachment metadata, and return the parsed body. Idempotent. Returns parsed dict
        or None if the message/row is gone."""
        with self._uow.read() as c:
            row = self._store.get_message(c, pk)
            fetched = row and row["body_fetched"]
            raw = self._store.get_body(c, pk) if fetched else None
        if not row:
            return None
        if not fetched or not raw:
            acct = self._accounts.get(row["account"])
            # READ-ONLY borrow: pooled when this account is asked for (pool.py), else a fresh session,
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
            with self._uow.write() as c:
                self._keep_body(c, pk, raw, parsed)
            return parsed
        parsed = mime.parse(raw)
        self._record_parts(pk, parsed)
        return parsed


    def _record_parts(self, pk: int, parsed: Parsed) -> None:
        """Rows a parsing change took away (migration 28) come back as the message is opened."""
        if not parsed["attachments"]:
            return
        with self._uow.read() as c:
            recorded = self._store.has_attachments(c, pk)
        if not recorded:
            with self._uow.write() as c:
                self._store.insert_attachments(c, pk, parsed["attachments"])


    def _keep_body(self, c: Connection, pk: int, raw: bytes, parsed: Parsed) -> None:
        """Store one message's raw RFC822 with what opening it records: attachment metadata, the
        parsed recipients and the full-text body. The caller commits."""
        self._store.store_body(c, pk, raw, datetime.now(timezone.utc).isoformat())
        self._store.delete_attachments(c, pk)
        self._store.insert_attachments(c, pk, parsed["attachments"])
        if parsed["attachments"]:
            self._store.mark_has_attachments(c, pk)
        self._store.set_recipients_text(c, pk, self._store.recipients_of(parsed))
        self._store.set_recipients(c, pk, mime.recipient_addrs(BytesHeaderParser().parsebytes(raw)))
        self._store.fts_set_body(c, pk, body_text_of(parsed))   # full body + recipients now searchable


    def sync_new(self, acct: Account, folder: FolderName = INBOX) -> Stored:
        """Incremental sync of one folder: only UIDs above the stored watermark."""
        return self.sync_folder(acct, folder, full=False)
