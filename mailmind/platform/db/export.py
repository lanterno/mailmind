"""Local, read-only mailbox export — no IMAP, no network, cannot mutate the live DB.

Emits your synced mail to standard, portable formats so nothing is locked in MailMind:
  • eml  — one RFC822 .eml per message under  <out>/<account>/<folder>/<pk>.eml
  • mbox — one mboxrd file per account+folder  <out>/<account>/<folder>.mbox
  • db   — a consistent SQLite snapshot (checkpoint + .backup) to  <out>/mailmind-<stamp>.db
  • the page's download is the first two packed:  <out>/mailmind-<format>-<stamp>.zip

Bodies: when the raw RFC822 was fetched (body_fetched=1) it is written verbatim (lossless).
Otherwise a minimal header-only RFC822 is synthesized from the stored row (From/Subject/Date/
Message-ID/threading), stamped with X-MailMind-* so a future reimport keeps thread/bucket info.

Safety: the DB is opened mode=ro after a WAL checkpoint, so a bug here can never write or
corrupt the live database. Grep this file for `accounts.connect` — there is none. What it writes is its owner's
alone, wherever it goes: each file readable by them only, and each folder it makes listable by them only.

  python -m mailmind export --format eml  --out /tmp/mail [--account me@x.com] [--attachments]
  python -m mailmind export --format mbox --out /tmp/mail
  python -m mailmind export --format db   --out /tmp/mail
"""
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from email import policy as _policy
from typing import Literal

from mailmind.platform import private, settings
from mailmind.platform.db import database, driver

type ArchiveFormat = Literal["eml", "mbox"]

_BAD = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')        # path separators + control + shell-hostile


def _safe(name, fallback):
    """Filesystem-safe component: drop hostile chars, collapse whitespace, cap 120 bytes,
    fall back to a stable id (pk) when the source (e.g. a Message-ID) is empty/unsafe."""
    s = _BAD.sub("_", (name or "").strip()).strip("._ ")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return str(fallback)
    return s.encode("utf-8")[:120].decode("utf-8", "ignore") or str(fallback)


def _ro_conn():
    """Checkpoint the WAL into the main db, then open a READ-ONLY connection. Read-only mode
    means the export physically cannot write/corrupt the live DB even on a bug."""
    c = database.connect(settings.current().db)
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.commit()
    finally:
        c.close()
    ro = database.keyed(settings.current().db, read_only=True)
    ro.row_factory = driver.Row
    return ro


def _hdr(v):
    """A header value safe for the strict SMTP policy: collapse CR/LF (malformed stored headers
    from real mail would otherwise raise) and trim."""
    return re.sub(r"[\r\n]+", " ", str(v or "")).strip()


def _synth(row):
    """Minimal header-only RFC822 from a stored row when the raw body wasn't fetched."""
    m = EmailMessage()
    frm = (row["from_name"] and f'{row["from_name"]} <{row["from_addr"]}>') or (row["from_addr"] or "")
    for name, val in (("From", frm), ("Subject", row["subject"]), ("Date", row["date_iso"]),
                      ("Message-ID", row["message_id"]), ("In-Reply-To", row["in_reply_to"]),
                      ("References", row["refs"]), ("X-MailMind-Account", row["account"]),
                      ("X-MailMind-Folder", row["folder"]), ("X-MailMind-ThreadID", row["thread_id"]),
                      ("X-MailMind-Bucket", row["bucket"])):
        h = _hdr(val)
        if h:
            m[name] = h
    m.set_content(row["snippet"] or "")
    return m.as_bytes(policy=_policy.SMTP)


def _raw_for(c, row):
    """Lossless raw bytes if present, else a synthesized header-only message."""
    if row["body_fetched"]:
        b = c.execute("SELECT raw FROM bodies WHERE message_pk=?", (row["pk"],)).fetchone()
        if b and b["raw"]:
            return bytes(b["raw"]), True
    return _synth(row), False


def _rows(c, account=None):
    sql = ("SELECT pk,account,folder,uid,message_id,thread_id,in_reply_to,refs,from_addr,"
           "from_name,subject,date_iso,snippet,bucket,body_fetched FROM messages")
    args = []
    if account:
        sql += " WHERE account=?"
        args.append(account)
    sql += " ORDER BY account, folder, ts"
    return c.execute(sql, args).fetchall()


def plan(account=None):
    """Dry-run summary: per (account,folder) message + fetched-body counts. Pure SELECT."""
    c = _ro_conn()
    try:
        where = " WHERE account=?" if account else ""
        args = [account] if account else []
        rows = c.execute(
            f"SELECT account, folder, COUNT(*) n, SUM(body_fetched) bodies FROM messages{where} "
            "GROUP BY account, folder ORDER BY account, folder", args).fetchall()
        total = sum(r["n"] for r in rows)
        bodies = sum((r["bodies"] or 0) for r in rows)
        return {"ok": True, "total": total, "bodies": bodies,
                "groups": [{"account": r["account"], "folder": r["folder"],
                            "messages": r["n"], "bodies": r["bodies"] or 0} for r in rows]}
    finally:
        c.close()


def export_eml(out, account=None, attachments=False):
    c = _ro_conn()
    n = 0
    try:
        from mailmind.content import mime
        for r in _rows(c, account):
            raw, real = _raw_for(c, r)
            d = os.path.join(out, _safe(r["account"], "acct"), _safe(r["folder"] or "INBOX", "folder"))
            private.folder(d)
            base = _safe((r["message_id"] or "").strip("<>"), r["pk"])
            with private.create(os.path.join(d, f"{base}-{r['pk']}.eml")) as f:
                f.write(raw)
            if attachments and real:
                try:
                    for a in mime.parse(raw).get("attachments", []):
                        payload, _ct = mime.extract_part(raw, a["part_id"])
                        if not payload:
                            continue
                        ad = os.path.join(d, "att")
                        private.folder(ad)
                        fn = _safe(a.get("filename") or a["part_id"], a["part_id"])
                        with private.create(os.path.join(ad, f"{r['pk']}_{fn}")) as af:
                            af.write(payload)
                except Exception:
                    pass
            n += 1
    finally:
        c.close()
    return n


def _mbox_escape(raw):
    """mboxrd: prefix any line of >*From  with an extra '>' so the From-separator is unambiguous."""
    return re.sub(rb"(?m)^(>*From )", rb">\1", raw)


def export_mbox(out, account=None):
    c = _ro_conn()
    files, handles = {}, {}
    n = 0
    try:
        private.folder(out)
        for r in _rows(c, account):
            key = (r["account"], r["folder"] or "INBOX")
            if key not in handles:
                d = os.path.join(out, _safe(r["account"], "acct"))
                private.folder(d)
                path = os.path.join(d, _safe(r["folder"] or "INBOX", "folder") + ".mbox")
                handles[key] = private.create(path)
                files[key] = path
            raw, _real = _raw_for(c, r)
            sep = ("From mailmind@localhost " + (r["date_iso"] or "")).encode("utf-8", "replace")
            h = handles[key]
            h.write(sep + b"\n")
            h.write(_mbox_escape(raw))
            if not raw.endswith(b"\n"):
                h.write(b"\n")
            h.write(b"\n")
            n += 1
    finally:
        for h in handles.values():
            h.close()
        c.close()
    return n, files


_WRITERS = {"eml": export_eml, "mbox": export_mbox}


def archive(out, form: ArchiveFormat):
    """One zip of the mail in a standard format, in `out`: what `export_eml` or `export_mbox` writes, each file gone as it is packed,
    so a large mailbox is on the disk about once and not twice."""
    tree = os.path.join(out, "mail")
    os.mkdir(tree, 0o700)
    _WRITERS[form](tree)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out, f"mailmind-{form}-{stamp}.zip")
    with private.create(path) as f, zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        for where, _folders, names in os.walk(tree):
            for name in sorted(names):
                file = os.path.join(where, name)
                z.write(file, os.path.relpath(file, tree))
                os.unlink(file)
    shutil.rmtree(tree)
    return path


def export_db(out):
    """A consistent on-disk snapshot via sqlite's online backup API (checkpoint first), encrypted with the database's
    key: it opens on this computer, or wherever that key is given (MAILMIND_DB_KEY)."""
    private.folder(out)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = os.path.join(out, f"mailmind-{stamp}.db")
    private.create(dest_path).close()
    src = _ro_conn()
    try:
        dest = database.keyed(dest_path)        # encrypted with the database's key, as the database is
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    return dest_path
