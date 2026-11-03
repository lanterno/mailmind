"""SMTP transport + per-provider Sent-folder APPEND for MailMind — stdlib only.

smtp_connect(acct) mirrors accounts.connect (same credential path) but for SMTP, and —
critically — takes its TLS context from accounts.tls_context, which drops verification only
for a loopback host (the Proton Bridge case). For any real host, certificates are fully
verified, so a forged-cert MITM can never capture the SMTP AUTH password. The SMTP host/port/credential come ONLY from
config.accounts() (keyed by the account email) — never from a request — so there is no
SSRF/arbitrary-host primitive.
"""
import re
import ssl
import time
import socket
import smtplib
import imaplib

import accounts
import config
import mailops

def provider_saves_sent(acct):
    """Gmail auto-files SMTP-sent mail in [Gmail]/Sent — an explicit APPEND would double it."""
    return acct.get("kind") == "gmail"


def smtp_connect(acct):
    host, port = acct["smtp_host"], int(acct["smtp_port"])
    if acct.get("smtp_starttls", True):
        s = smtplib.SMTP(host, port, timeout=60)
        s.ehlo()
        s.starttls(context=accounts.tls_context(host))
        s.ehlo()
    else:  # implicit TLS (e.g. 465)
        s = smtplib.SMTP_SSL(host, port, context=accounts.tls_context(host), timeout=60)
        s.ehlo()
    s.login(acct["email"], config.resolve_credential(acct))
    return s


def send(acct, raw, envelope):
    """Deliver `raw` (Bcc-less bytes) to the full `envelope` recipient set. The
    envelope-from is forced to the account email; From in the bytes is already the
    account. Raises on failure (caller scrubs + records)."""
    if not envelope:
        raise ValueError("no envelope recipients")
    s = smtp_connect(acct)
    try:
        s.sendmail(acct["email"], envelope, raw)
    finally:
        try:
            s.quit()
        except Exception:
            pass


def append_to_sent(acct, raw):
    """IMAP APPEND the exact sent bytes to the account's Sent folder, flagged \\Seen.
    Returns the resolved folder name. Raises if no Sent folder or the APPEND fails."""
    M = accounts.connect(acct)
    try:
        sent = mailops._find_folder(M, ("\\sent", "sent")) or "Sent"
        typ, d = M.append(accounts.mailbox_arg(sent), "(\\Seen)",
                          imaplib.Time2Internaldate(time.time()), raw)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"APPEND failed: {d}")
        return sent
    finally:
        try:
            M.logout()
        except Exception:
            pass


_APPENDUID = re.compile(rb"APPENDUID\s+(\d+)\s+(\d+)")


def append_to_drafts(acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None, prev_folder=None):
    """APPEND a draft (flagged \\Draft) to the account's Drafts folder, after deleting the
    previous copy (single-UID expunge — NEVER a bulk expunge). Returns
    (folder, new_uid|None, uidvalidity|None). Delete-then-append so a crash leaves 0 drafts.
    prev_folder: where the previous copy is; when that isn't this Drafts folder, nothing is
    deleted here (its UID means nothing in this one), and the caller removes it there."""
    M = accounts.connect(acct)
    try:
        drafts = mailops._find_folder(M, ("\\drafts", "drafts", "draft")) or "Drafts"
        if prev_folder is not None and prev_folder != drafts:
            prev_uid = prev_msgid = None
        typ, d = M.select(accounts.mailbox_arg(drafts), readonly=False)
        uidv = None
        try:
            uidv = int((M.untagged_responses.get("UIDVALIDITY") or [b"0"])[0])
        except Exception:
            uidv = None
        # delete the prior draft copy, if we can safely identify it
        del_uid = None
        if prev_uid and (prev_uidvalidity is None or prev_uidvalidity == uidv):
            del_uid = str(prev_uid)
        elif prev_msgid:
            try:
                t, dd = M.uid("SEARCH", None, "HEADER", "Message-ID", prev_msgid)
                ids = (dd[0].split() if dd and dd[0] else [])
                del_uid = ids[0].decode() if ids else None
            except Exception:
                del_uid = None
        if del_uid:
            try:
                M.uid("STORE", del_uid, "+FLAGS", "(\\Deleted)")
                mailops.expunge_uid(M, del_uid)
            except Exception:
                pass
        typ, d = M.append(accounts.mailbox_arg(drafts), "(\\Draft)", imaplib.Time2Internaldate(time.time()), raw)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"APPEND failed: {d}")
        new_uid = None
        for line in (d or []):
            m = _APPENDUID.search(line if isinstance(line, bytes) else str(line).encode())
            if m:
                new_uid = int(m.group(2))
                break
        return drafts, new_uid, uidv
    finally:
        try:
            M.logout()
        except Exception:
            pass


def delete_draft_uid(acct, folder, uid, uidvalidity=None):
    """Remove one draft message by UID (single-UID expunge). Best-effort."""
    if not uid:
        return
    M = accounts.connect(acct)
    try:
        M.select(accounts.mailbox_arg(folder or "Drafts"), readonly=False)
        try:
            cur = int((M.untagged_responses.get("UIDVALIDITY") or [b"0"])[0])
        except Exception:
            cur = None
        if uidvalidity is not None and cur is not None and uidvalidity != cur:
            return                                     # UIDs rolled — don't risk deleting the wrong one
        M.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        mailops.expunge_uid(M, str(uid))
    finally:
        try:
            M.logout()
        except Exception:
            pass


def sanitize_smtp_error(e):
    """Fixed, secret-free vocabulary — never echoes recipients, banner, or AUTH state."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "auth failed"
    if isinstance(e, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return "recipient refused"
    if isinstance(e, smtplib.SMTPDataError):
        return "rejected by server"
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return "connection lost"
    if isinstance(e, (smtplib.SMTPConnectError, ConnectionRefusedError)):
        return "connection refused"
    if isinstance(e, ssl.SSLError):
        return "tls error"
    if isinstance(e, socket.gaierror):
        return "host not found"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(e, ValueError):
        return str(e)[:80]  # our own validation messages are already secret-free
    return "send failed"
