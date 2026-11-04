"""SMTP transport + per-provider Sent-folder APPEND for MailMind — stdlib only.

smtp_connect(acct) mirrors accounts.connect (same credential path) but for SMTP, and —
critically — takes its TLS context from accounts.tls_context, which drops verification only
for a loopback host (the Proton Bridge case). For any real host, certificates are fully
verified, so a forged-cert MITM can never capture the SMTP AUTH password. The SMTP host/port/credential come ONLY from
config.accounts() (keyed by the account email) — never from a request — so there is no
SSRF/arbitrary-host primitive.
"""
import ssl
import socket
import smtplib
import imaplib
from datetime import datetime

import accounts
import config
from mailmind.accounts.domain import tls_pin
from mailmind.bootstrap import mailbox
from mailmind.mailbox.application import session as mail_session
from mailmind.platform import profile
from mailmind.platform.db import txguard

def smtp_connect(acct):
    profile.refuse_writes("SMTP")          # a read-only process sends nothing
    txguard.refuse_network("an SMTP connection")
    host, port = acct["smtp_host"], int(acct["smtp_port"])
    if acct.get("smtp_starttls", True):
        s = txguard.guard_calls(smtplib.SMTP(host, port, timeout=60), "send", "SMTP")   # all it sends
        s.ehlo()
        s.starttls(context=accounts.tls_context(host))
        s.ehlo()
    else:  # implicit TLS (e.g. 465)
        s = txguard.guard_calls(smtplib.SMTP_SSL(host, port, context=accounts.tls_context(host), timeout=60),
                                "send", "SMTP")
        s.ehlo()
    try:
        accounts.check_pin(acct, host, port, s)
    except tls_pin.TLSPinMismatch:
        s.close()
        raise
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
    s = mailbox.open_session(acct)
    try:
        sent = mail_session.find_role(s, "sent") or "Sent"
        s.append(sent, raw, flags=["\\Seen"], when=datetime.now().astimezone())
        return sent
    finally:
        try:
            s.logout()
        except Exception:
            pass


def append_to_drafts(acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None, prev_folder=None):
    """APPEND a draft (flagged \\Draft) to the account's Drafts folder, after deleting the
    previous copy (single-UID expunge — NEVER a bulk expunge). Returns
    (folder, new_uid|None, uidvalidity|None). Delete-then-append so a crash leaves 0 drafts.
    prev_folder: where the previous copy is; when that isn't this Drafts folder, nothing is
    deleted here (its UID means nothing in this one), and the caller removes it there."""
    s = mailbox.open_session(acct)
    try:
        drafts = mail_session.find_role(s, "drafts") or "Drafts"
        if prev_folder is not None and prev_folder != drafts:
            prev_uid = prev_msgid = None
        try:
            uidv = s.select(drafts, readonly=False).uidvalidity
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:          # refused: no copy is deleted, but the APPEND still goes
            uidv = None
        # delete the prior draft copy, if we can safely identify it
        del_uid = None
        if prev_uid and (prev_uidvalidity is None or prev_uidvalidity == uidv):
            del_uid = int(prev_uid)
        elif prev_msgid:
            try:
                ids = s.uids("HEADER", "Message-ID", accounts.imap_quote(prev_msgid))
                del_uid = ids[0] if ids else None
            except Exception:
                del_uid = None
        if del_uid:
            try:
                s.add_flags([del_uid], ["\\Deleted"])
                s.expunge([del_uid])         # this UID alone, never a bulk EXPUNGE
            except Exception:
                pass
        new_uid = s.append(drafts, raw, flags=["\\Draft"], when=datetime.now().astimezone())
        return drafts, new_uid, uidv
    finally:
        try:
            s.logout()
        except Exception:
            pass


def delete_draft_uid(acct, folder, uid, uidvalidity=None):
    """Remove one draft message by UID (single-UID expunge). Best-effort."""
    if not uid:
        return
    s = mailbox.open_session(acct)
    try:
        try:
            cur = s.select(folder or "Drafts", readonly=False).uidvalidity
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            return                                     # refused: nothing to delete from
        if uidvalidity is not None and uidvalidity != cur:
            return                                     # UIDs rolled (or unsaid) — don't risk the wrong one
        try:
            s.add_flags([int(uid)], ["\\Deleted"])
            s.expunge([int(uid)])                      # this UID alone, never a bulk EXPUNGE
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            pass
    finally:
        try:
            s.logout()
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
