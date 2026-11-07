"""SMTP, which only this module speaks. An account's host, port and credential come only from its
configuration, never from a request, so there is no way to aim a connection elsewhere. Its TLS
context drops verification only for a loopback host (the Proton Bridge case), where the certificate
pin stands in, so a forged certificate can't capture the AUTH password."""
import smtplib
import socket
import ssl

from mailmind.compose.domain import outbox_item
from mailmind.compose.domain.errors import Unanswered
from mailmind.platform import profile
from mailmind.platform.db import txguard


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


class SmtpTransport:
    def __init__(self, credentials):
        self._credentials = credentials

    def connect(self, acct):
        profile.refuse_writes("SMTP")          # a read-only process sends nothing
        txguard.refuse_network("an SMTP connection")
        host, port = acct["smtp_host"], int(acct["smtp_port"])
        if acct.get("smtp_starttls", True):
            s = txguard.guard_calls(smtplib.SMTP(host, port, timeout=60), "send", "SMTP")   # all it sends
            s.ehlo()
            s.starttls(context=self._credentials.tls_context(host))
            s.ehlo()
        else:  # implicit TLS (e.g. 465)
            s = txguard.guard_calls(smtplib.SMTP_SSL(host, port, context=self._credentials.tls_context(host), timeout=60),
                                    "send", "SMTP")
            s.ehlo()
        try:
            self._credentials.check_pin(acct, host, port, s)
        except self._credentials.PinMismatch:
            s.close()
            raise
        s.login(acct["email"], self._credentials.secret(acct))
        return s

    def send(self, acct, raw, envelope, before_dot=None):
        """Deliver `raw` (Bcc-less bytes) to the full `envelope` recipient set. The
        envelope-from is forced to the account email; From in the bytes is already the
        account. Raises on failure (caller scrubs + records): Unanswered when the whole message went
        out and the server never said what became of it, which an answer of any kind isn't. `before_dot`
        is called just before the message's last bytes go, so a crash from there on can be told too."""
        if not envelope:
            raise ValueError("no envelope recipients")
        s = self.connect(acct)
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

    def permanent(self, e):
        """Whether the server refused for good, as its answer's codes say. A dropped connection or a timeout had no answer,
        and may pass."""
        if isinstance(e, smtplib.SMTPRecipientsRefused):
            return outbox_item.refused_for_good(v[0] for v in (e.recipients or {}).values() if isinstance(v, tuple) and v)
        if isinstance(e, smtplib.SMTPResponseException):
            return outbox_item.refused_for_good([e.smtp_code])
        return False

    def sanitize_error(self, e):
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
