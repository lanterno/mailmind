"""Ingest INBOX mail into SQLite — full (by date) or incremental (by UID
watermark, for the sync daemon). Fetches headers, classifies, pulls body text
for non-junk. Usage: python3 ingest.py [all|<email>] [days]
"""
import sys
import os
import re
import email
import html
import imaplib
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import accounts
import mailclass
import mailparse
import pool
import store
import threads
from mailmind.accounts.domain import provider
from mailmind.bootstrap import mailbox

CUTOFF_DAYS = 90
BODY_CHARS = 3000
HEADER_FIELDS = ("FROM", "TO", "CC", "SUBJECT", "DATE", "MESSAGE-ID", "IN-REPLY-TO", "REFERENCES",
                 "LIST-UNSUBSCRIBE", "LIST-ID")


def dec(s):
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "replace"))
            except (LookupError, Exception):  # unknown/invalid charset -> safe fallback
                out.append(part.decode("utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out).replace("\n", " ").replace("\r", " ").strip()


def text_body(msg):
    plain, htmltxt = None, None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        ct = part.get_content_type()
        if ct == "text/plain" and plain is None:
            plain = text
        elif ct == "text/html" and htmltxt is None:
            htmltxt = text
    body = plain
    if body is None and htmltxt is not None:
        body = _html_to_snippet_text(htmltxt)
    body = re.sub(r"[ \t]+", " ", (body or "")).strip()
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    return body[:BODY_CHARS]


# non-content blocks whose TEXT must never leak into a preview (a bare tag-strip
# leaves the CSS/JS body between the tags — the "ReadMsgBody{width:100%}" bug)
_NONCONTENT_RE = re.compile(r"(?is)<(script|style|head|title|noscript)\b[^>]*>.*?</\1\s*>")
_COMMENT_RE = re.compile(r"(?is)<!--.*?-->")


def _html_to_snippet_text(htmltxt):
    h = _NONCONTENT_RE.sub(" ", htmltxt)
    h = _COMMENT_RE.sub(" ", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))




def _has_invite(msg):
    """Whether a message carries a calendar invite: a text/calendar or application/ics part, or an .ics file."""
    try:
        return any(p.get_content_type() in ("text/calendar", "application/ics")
                   or (p.get_filename() or "").lower().endswith(".ics") for p in msg.walk())
    except Exception:
        return False


def _flags_of(flags):
    """The local flag columns from a message's IMAP flags."""
    f = {x.lower() for x in flags}
    return {"seen": int("\\seen" in f), "flagged": int("\\flagged" in f),
            "answered": int("\\answered" in f), "draft": int("\\draft" in f),
            "deleted": int("\\deleted" in f)}


def _process(session, acct, uids, folder="INBOX", with_body=False, advance=True):
    """Store ALL of the given UIDs (junk kept as a badge, with flags) for the folder `session`
    has selected. Bodies are fetched only when with_body (INBOX, for the AI snippet);
    other folders get headers+flags only (fast) and fetch the body lazily on open.
    advance=False stores them without touching the folder's watermark: only a sync that has
    seen every UID up to a point may say the folder is synced to there."""
    rows, max_uid, kept, junk = [], 0, 0, 0
    gmail = provider.resolve(acct).gmail_threads            # only Gmail understands X-GM-THRID
    if uids:
        for h in session.headers(uids, HEADER_FIELDS, gmail_thread=gmail):
            uid = h.uid
            max_uid = max(max_uid, uid)
            flags = _flags_of(h.flags)
            msg = email.message_from_bytes(h.raw)
            name, addr = parseaddr(msg.get("From", ""))
            has_lu = bool(msg.get("List-Unsubscribe") or msg.get("List-ID"))
            subj = dec(msg.get("Subject", ""))
            b, conf, reason = mailclass.classify(addr.lower(), subj, dec(name), has_lu)
            isj = 1 if mailclass.is_junk(b) else 0
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
                         "subject": subj, "date_iso": iso, "ts": ts, "bucket": b,
                         "confidence": conf, "reason": reason, "is_junk": isj, "flags": flags,
                         "message_id": (msg.get("Message-ID") or "").strip(),
                         "gmail_thread": h.gmail_thread,
                         "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                         "refs": (msg.get("References") or "").replace("\n", " ").strip(),
                         "recipients": (dec(msg.get("To", "")) + " " + dec(msg.get("Cc", ""))).strip()[:2000]})
    c = store.conn()
    for r in rows:
        snippet, invite = "", None
        if with_body and not r["is_junk"]:
            try:
                try:
                    raw = session.body(r["uid"]) or b""
                except imaplib.IMAP4.abort:
                    raise
                except imaplib.IMAP4.error:   # refused (vs a genuinely empty body): surface it
                    print(f"[ingest] body fetch {acct['email']} uid {r['uid']}: NO", flush=True)
                    raw = b""
                msg = email.message_from_bytes(raw)
                snippet = text_body(msg)
                invite = raw if _has_invite(msg) else None
            except Exception:
                snippet = ""
        pk = store.upsert_message(c, {
            "account": acct["email"], "kind": acct["kind"], "folder": folder,
            "uid": r["uid"], "message_id": r["message_id"], "gmail_thread": r["gmail_thread"],
            "from_addr": r["from_addr"], "from_name": r["from_name"], "subject": r["subject"],
            "date_iso": r["date_iso"], "ts": r["ts"], "snippet": snippet,
            "bucket": r["bucket"], "confidence": r["confidence"], "reason": r["reason"],
            "is_junk": r["is_junk"],
        })
        store.set_local_flags(c, pk, **r["flags"])
        c.execute("UPDATE messages SET in_reply_to=?, refs=?, recipients=? WHERE pk=?",
                  (r["in_reply_to"], r["refs"], r["recipients"], pk))
        store.fts_index(c, pk)               # refresh FTS so recipients is searchable immediately
        if invite:                           # Today & next reads invites from stored bodies only
            try:
                _keep_body(c, pk, invite, mailparse.parse(invite))
            except Exception as e:
                print(f"[ingest] keep invite {acct['email']} uid {r['uid']}: {e}", flush=True)
        c.commit()
    if advance:
        store.set_watermark(c, acct["email"], max_uid, datetime.now(timezone.utc).isoformat(), folder)
        c.commit()
    c.close()
    return kept, junk, max_uid


def sync_folder(acct, folder, full=False, recent=None):
    """Sync one folder (UIDVALIDITY-checked). full=True re-scans the whole folder;
    otherwise pulls only UIDs above the per-folder watermark. Stores everything.
    recent=N bounds a first/full scan to the most recent N UIDs (used by background folder
    warming so a huge Sent/Archive doesn't pull thousands in one pass); incremental syncs
    are already watermark-bounded and ignore it."""
    s = mailbox.open_session(acct)
    try:
        uidv = s.select(folder).uidvalidity
        c = store.conn()
        stored = store.get_uidvalidity(c, acct["email"], folder)
        if uidv and stored and uidv != stored:
            store.reset_folder(c, acct["email"], folder)  # server reset UIDs -> re-sync clean
            c.commit()
            full = True
        if uidv:
            store.set_uidvalidity(c, acct["email"], folder, uidv)
            c.commit()
        wm = store.get_watermark(c, acct["email"], folder)
        c.close()
        if full or wm == 0:
            uids = s.uids("ALL")
            if recent and len(uids) > recent:
                uids = uids[-recent:]      # the most recent N only
        else:
            uids = [u for u in s.uids("UID", f"{wm + 1}:*") if u > wm]   # N:* always matches the last
        result = _process(s, acct, uids, folder, with_body=(folder.upper() == "INBOX"))
        c = store.conn()
        threads.recompute_threads(c, acct["email"])   # regroup conversations for new mail
        store.apply_mute_on_ingest(c, acct["email"])  # keep new members of muted threads silent
        store.resolve_followups(c, acct["email"])     # new mail / answered-flag resolves waiting threads
        c.commit(); c.close()
        return result
    finally:
        try:
            s.logout()
        except Exception:
            pass


def ingest_message(acct, folder, message_id):
    """Force-ingest ONE message identified by RFC822 Message-ID into `folder`,
    bypassing the incremental watermark. Needed by undo: a re-labeled Gmail message
    keeps its original (<= watermark) UID, so a normal incremental sync skips it.
    The watermark stays where it was. A message moved back gets a new, highest UID, and
    raising the mark to it would skip mail that arrived since the last sync; in a folder
    never opened, it would also mark the folder synced, so its first full sync never ran.
    Returns the number of messages stored (0 if not found)."""
    if not message_id:
        return 0
    s = mailbox.open_session(acct)
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
        _process(s, acct, [uid], folder, with_body=(folder.upper() == "INBOX"), advance=False)
        c = store.conn()
        threads.recompute_threads(c, acct["email"])
        store.apply_mute_on_ingest(c, acct["email"])
        store.resolve_followups(c, acct["email"])     # new mail / answered-flag resolves waiting threads
        c.commit()
        c.close()
        return 1
    finally:
        try:
            s.logout()
        except Exception:
            pass


def folder_synced(acct_email, folder):
    c = store.conn()
    r = c.execute("SELECT 1 FROM sync_state WHERE account=? AND folder=?",
                  (acct_email, folder)).fetchone()
    c.close()
    return bool(r)


def ingest_account(acct, days=CUTOFF_DAYS):
    s = mailbox.open_session(acct)
    try:
        s.select("INBOX")
        if days >= 3650:
            uids = s.uids("ALL")
        else:
            uids = s.uids("SINCE", (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y"))
        return _process(s, acct, uids, "INBOX", with_body=True)
    finally:
        s.logout()


def fetch_body(pk):
    """Lazily fetch + store the raw RFC822 for one message (read-only PEEK), record
    attachment metadata, and return the parsed body. Idempotent. Returns parsed dict
    or None if the message/row is gone."""
    c = store.conn()
    row = store.get_message(c, pk)
    fetched = row and row["body_fetched"]
    raw = store.get_body(c, pk) if fetched else None
    c.close()
    if not row:
        return None
    if not fetched or not raw:
        acct = accounts.get(row["account"])
        # READ-ONLY borrow: pooled when MAILMIND_POOL enables this account, else a fresh session,
        # logged out after. Mutations never use the pool.
        with pool.borrow(acct) as s:
            s.select(row["folder"])
            try:
                raw = s.body(row["uid"]) or b""
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:       # refused: nothing to show, and nothing stored
                raw = b""
        if not raw:
            return mailparse.parse(b"")
        parsed = mailparse.parse(raw)
        c = store.conn()
        _keep_body(c, pk, raw, parsed)
        c.commit()
        c.close()
        return parsed
    return mailparse.parse(raw)


def _keep_body(c, pk, raw, parsed):
    """Store one message's raw RFC822 with what opening it records: attachment metadata, the
    parsed recipients and the full-text body. The caller commits."""
    store.store_body(c, pk, raw, datetime.now(timezone.utc).isoformat())
    c.execute("DELETE FROM attachments WHERE message_pk=?", (pk,))
    for a in parsed["attachments"]:
        c.execute("""INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,is_inline)
                     VALUES(?,?,?,?,?,?,?)""",
                  (pk, a["part_id"], a["filename"], a["content_type"], a["size"],
                   a["content_id"], a["is_inline"]))
    if parsed["attachments"]:
        c.execute("UPDATE messages SET has_attach=1 WHERE pk=?", (pk,))
    c.execute("UPDATE messages SET recipients=? WHERE pk=?", (store._recipients_of(parsed), pk))
    store.fts_set_body(c, pk, store._body_text_of(parsed))   # full body + recipients now searchable


def reconcile_account(acct, force=False, dry_run=False):
    """Prune INBOX rows whose UID is no longer live, and stamp survivors verified.
    Pure prune — preserves summaries of survivors and never lowers the watermark.
    SAFETY: never deletes on an IMAP error, and treats an OK-but-empty SEARCH
    against a non-empty stored set as 'unknown' (prunes nothing) unless force.
    Only rows stored before the SEARCH may go: the 300 s sync passes no IMAP gate, so
    it can store mail while this runs, and that mail is missing from the SEARCH's answer.
    dry_run computes the ghost count without changing anything."""
    from datetime import datetime as _dt
    res = {"account": acct["email"], "live": 0, "stored": 0, "ghosts": 0,
           "pruned": 0, "error": None}
    c = store.conn()
    stored_uids = [r["uid"] for r in c.execute(
        "SELECT uid FROM messages WHERE account=? AND folder='INBOX'", (acct["email"],))]
    c.close()
    res["stored"] = len(stored_uids)
    try:
        s = mailbox.open_session(acct, timeout=15)   # background/maintenance path: a hung account must
        try:                                          # not pin the IMAP gate for the interactive 60s
            uidv = s.select("INBOX").uidvalidity
            live = set(s.uids("ALL"))                 # a refused SEARCH raises: nothing is pruned
        finally:
            try:
                s.logout()                            # ALWAYS close the socket (no leak on select/SEARCH error)
            except Exception:
                pass
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        return res  # never prune on error
    # UIDVALIDITY guard: if the server renumbered UIDs (mailbox recreation / Bridge
    # re-index), `live` is in a DIFFERENT uid space than the stored uids — comparing
    # them would prune the ENTIRE real INBOX as "ghosts". Never prune here; sync_folder
    # detects the change and does the reset + full re-sync on its own pass.
    c = store.conn()
    stored_uidv = store.get_uidvalidity(c, acct["email"], "INBOX")
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
    c = store.conn()
    pruned, kept = store.reconcile(c, acct["email"], "INBOX", live, when, among=set(stored_uids))
    c.commit()
    c.close()
    res["pruned"] = pruned
    return res


def sync_new(acct, folder="INBOX"):
    """Incremental sync of one folder: only UIDs above the stored watermark."""
    return sync_folder(acct, folder, full=False)


def main():
    store.init()
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else CUTOFF_DAYS
    accts = accounts.accounts() if which == "all" else [accounts.get(which)]
    for a in accts:
        try:
            kept, junk, _ = ingest_account(a, days)
            print(f"{a['email']:<32} kept {kept:>4}  (skipped {junk} junk)", flush=True)
        except Exception as e:
            print(f"{a['email']:<32} ERROR: {type(e).__name__}: {e}", flush=True)
    print("stats:", store.stats(store.conn()))


if __name__ == "__main__":
    main()
