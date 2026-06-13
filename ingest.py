"""Ingest INBOX mail into SQLite — full (by date) or incremental (by UID
watermark, for the sync daemon). Fetches headers, classifies, pulls body text
for non-junk. Usage: python3 ingest.py [all|<email>] [days]
"""
import sys
import os
import re
import email
import html
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import accounts
import mailclass
import store

CUTOFF_DAYS = 90
HDR_BATCH = 300
BODY_CHARS = 3000


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
        body = html.unescape(re.sub(r"<[^>]+>", " ", htmltxt))
    body = re.sub(r"[ \t]+", " ", (body or "")).strip()
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    return body[:BODY_CHARS]


def _uid_of(meta_bytes):
    m = re.search(r"UID (\d+)", meta_bytes.decode("utf-8", "replace"))
    return int(m.group(1)) if m else None


def _process(M, acct, uids):
    """Classify a set of UIDs; store non-junk with bodies. Returns (kept, junk, max_uid)."""
    kept, junk, max_uid = [], 0, 0
    for start in range(0, len(uids), HDR_BATCH):
        chunk = uids[start:start + HDR_BATCH]
        typ, data = M.uid("FETCH", ",".join(chunk),
                          "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE LIST-ID)])")
        for part in data:
            if not isinstance(part, tuple):
                continue
            uid = _uid_of(part[0])
            if uid is None:
                continue  # can't store or act on a message without a UID
            max_uid = max(max_uid, uid)
            msg = email.message_from_bytes(part[1])
            name, addr = parseaddr(msg.get("From", ""))
            has_lu = bool(msg.get("List-Unsubscribe") or msg.get("List-ID"))
            subj = dec(msg.get("Subject", ""))
            b, conf, reason = mailclass.classify(addr.lower(), subj, dec(name), has_lu)
            if mailclass.is_junk(b):
                junk += 1
                continue
            try:
                dt = parsedate_to_datetime(msg.get("Date", ""))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                iso, ts = dt.isoformat(), int(dt.timestamp())
            except Exception:
                iso, ts = None, 0
            kept.append({"uid": uid, "from_addr": addr.lower(), "from_name": dec(name),
                         "subject": subj, "date_iso": iso, "ts": ts, "bucket": b,
                         "confidence": conf, "reason": reason,
                         "message_id": (msg.get("Message-ID") or "").strip()})
    c = store.conn()
    for r in kept:
        try:
            typ, data = M.uid("FETCH", str(r["uid"]), "(BODY.PEEK[])")
            raw = next((p[1] for p in data if isinstance(p, tuple)), b"")
            r["snippet"] = text_body(email.message_from_bytes(raw))
        except Exception:
            r["snippet"] = ""
        store.upsert_message(c, {
            "account": acct["email"], "kind": acct["kind"], "folder": "INBOX",
            "uid": r["uid"], "message_id": r["message_id"], "gmail_thread": None,
            "from_addr": r["from_addr"], "from_name": r["from_name"], "subject": r["subject"],
            "date_iso": r["date_iso"], "ts": r["ts"], "snippet": r["snippet"],
            "bucket": r["bucket"], "confidence": r["confidence"], "reason": r["reason"],
            "is_junk": 0,
        })
        c.commit()  # short write lock per message so deletes/reads aren't blocked
    store.set_watermark(c, acct["email"], max_uid, datetime.now(timezone.utc).isoformat())
    c.commit()
    c.close()
    return len(kept), junk, max_uid


def ingest_account(acct, days=CUTOFF_DAYS):
    M = accounts.connect(acct)
    M.select('"INBOX"', readonly=True)
    if days >= 3650:  # all-time: SEARCH ALL (some servers reject ancient SINCE dates)
        typ, d = M.uid("SEARCH", None, "ALL")
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, d = M.uid("SEARCH", None, "SINCE", since)
    uids = [x.decode() for x in d[0].split()]
    res = _process(M, acct, uids)
    M.logout()
    return res


def reconcile_account(acct, force=False, dry_run=False):
    """Prune INBOX rows whose UID is no longer live, and stamp survivors verified.
    Pure prune — preserves summaries of survivors and never lowers the watermark.
    SAFETY: never deletes on an IMAP error, and treats an OK-but-empty SEARCH
    against a non-empty stored set as 'unknown' (prunes nothing) unless force.
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
        M = accounts.connect(acct)
        M.select('"INBOX"', readonly=True)
        typ, d = M.uid("SEARCH", None, "ALL")
        M.logout()
        if typ != "OK":
            raise RuntimeError(f"SEARCH returned {typ}")
        live = set(int(x) for x in d[0].split()) if d and d[0] else set()
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        return res  # never prune on error
    res["live"] = len(live)
    res["ghosts"] = sum(1 for u in stored_uids if u not in live)
    if not live and res["stored"] > 0 and not force:
        res["error"] = "empty SEARCH against non-empty store — skipped (use force)"
        return res
    if dry_run:
        return res
    when = _dt.now(timezone.utc).isoformat()
    c = store.conn()
    pruned, kept = store.reconcile(c, acct["email"], "INBOX", live, when)
    c.commit()
    c.close()
    res["pruned"] = pruned
    return res


def sync_new(acct):
    """Incremental: only UIDs above the stored watermark."""
    c = store.conn()
    wm = store.get_watermark(c, acct["email"])
    c.close()
    M = accounts.connect(acct)
    M.select('"INBOX"', readonly=True)
    typ, d = M.uid("SEARCH", None, "UID", f"{wm + 1}:*")
    uids = [x.decode() for x in d[0].split() if int(x) > wm]
    res = _process(M, acct, uids) if uids else (0, 0, wm)
    M.logout()
    return res


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
