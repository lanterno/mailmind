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


class Unanswered(ConnectionError):
    """The whole message went out and no answer came, so it may have been delivered: sent again it
    could reach its recipients twice. That's for a person to decide."""


def _watch_the_dot(s, before_dot):
    """Tell apart the two sides of the message's last bytes: smtplib's data() sends the message and
    its final dot in one send(), the only call that ends so. Returns what's known so far. A send()
    that raises left those bytes unsent, since they come last."""
    said = {"sent": False}
    real = s.send

    def send(data):
        last = isinstance(data, (bytes, bytearray)) and data.endswith(b"\r\n.\r\n")
        if last and before_dot:
            before_dot()
        real(data)
        said["sent"] = said["sent"] or last
    s.send = send
    return said


def send(acct, raw, envelope, before_dot=None):
    """Deliver `raw` (Bcc-less bytes) to the full `envelope` recipient set. The
    envelope-from is forced to the account email; From in the bytes is already the
    account. Raises on failure (caller scrubs + records): Unanswered when the whole message went
    out and the server never said what became of it, which an answer of any kind isn't. `before_dot`
    is called just before the message's last bytes go, so a crash from there on can be told too."""
    if not envelope:
        raise ValueError("no envelope recipients")
    s = smtp_connect(acct)
    said = _watch_the_dot(s, before_dot)
    try:
        try:
            s.sendmail(acct["email"], envelope, raw)
        except smtplib.SMTPResponseException:
            raise
        except OSError as e:
            if said["sent"]:
                raise Unanswered("no answer to the message") from e
            raise
    finally:
        try:
            s.quit()
        except Exception:
            pass


def append_to_sent(acct, raw, message_id=None):
    """IMAP APPEND the exact sent bytes to the account's Sent folder, flagged \\Seen.
    Returns the resolved folder name. Raises if no Sent folder or the APPEND fails.
    Given `message_id`, a copy already filed under it is kept and nothing is appended: a retry
    can't tell whether the last try's APPEND landed before its connection went. One flagged
    \\Deleted is as good as gone, so it doesn't count."""
    s = mailbox.open_session(acct)
    try:
        sent = mail_session.find_role(s, "sent") or "Sent"
        if message_id:
            s.select(sent)
            if s.uids("UNDELETED", "HEADER", "Message-ID", accounts.imap_quote(message_id)):
                return sent
        s.append(sent, raw, flags=["\\Seen"], when=datetime.now().astimezone())
        return sent
    finally:
        try:
            s.logout()
        except Exception:
            pass


def append_to_drafts(acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None, prev_folder=None):
    """APPEND a draft (flagged \\Draft) to the account's Drafts folder, then delete the previous
    copy (single-UID expunge — NEVER a bulk expunge). Returns
    (folder, new_uid|None, uidvalidity|None, removed_uid|None). Append first, so a save that fails or
    a crash never leaves none: at worst the old copy stays beside the new one. prev_folder: where the
    previous copy is; when that isn't this Drafts folder, nothing is deleted here (its UID means
    nothing in this one), and the caller removes it there."""
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
        # the prior draft copy, if we can safely identify it: asked before the new copy can answer too
        del_uid = removed = None
        if prev_uid and (prev_uidvalidity is None or prev_uidvalidity == uidv):
            del_uid = int(prev_uid)
        elif prev_msgid:
            try:
                ids = s.uids("HEADER", "Message-ID", accounts.imap_quote(prev_msgid))
                del_uid = ids[0] if ids else None
            except Exception:
                del_uid = None
        new_uid = s.append(drafts, raw, flags=["\\Draft"], when=datetime.now().astimezone())
        if del_uid:
            try:
                s.add_flags([del_uid], ["\\Deleted"])
                s.expunge([del_uid])         # this UID alone, never a bulk EXPUNGE
                removed = del_uid
            except Exception:
                pass
        return drafts, new_uid, uidv, removed
    finally:
        try:
            s.logout()
        except Exception:
            pass


def delete_draft_uid(acct, folder, uid, uidvalidity=None):
    """Remove one draft message by UID (single-UID expunge). Best-effort: True if it went."""
    if not uid:
        return False
    s = mailbox.open_session(acct)
    try:
        try:
            cur = s.select(folder or "Drafts", readonly=False).uidvalidity
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            return False                               # refused: nothing to delete from
        if uidvalidity is not None and uidvalidity != cur:
            return False                               # UIDs rolled (or unsaid) — don't risk the wrong one
        try:
            s.add_flags([int(uid)], ["\\Deleted"])
            s.expunge([int(uid)])                      # this UID alone, never a bulk EXPUNGE
            return True
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            return False
    finally:
        try:
            s.logout()
        except Exception:
            pass


def permanent(e):
    """Whether the server refused for good: a 5xx reply, or every recipient refused with one. Sent
    again it gets the same answer, and a refused login sent again counts toward a lockout. A 4xx
    reply, a dropped connection or a timeout may pass."""
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        codes = [v[0] for v in (e.recipients or {}).values() if isinstance(v, tuple) and v]
        return bool(codes) and all(isinstance(k, int) and 500 <= k < 600 for k in codes)
    if isinstance(e, smtplib.SMTPResponseException):
        return isinstance(e.smtp_code, int) and 500 <= e.smtp_code < 600
    return False


def sanitize_smtp_error(e):
    """Fixed, secret-free vocabulary — never echoes recipients, banner, or AUTH state."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "auth failed"
    if isinstance(e, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return "recipient refused"
    if isinstance(e, smtplib.SMTPDataError):
        return "rejected by server"
    if isinstance(e, Unanswered):
        return "connection lost after the message went out"
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
