"""The MailSession port over IMAPClient, which the new front door can run on.

IMAPClient runs on imaplib underneath, and its errors are imaplib's classes. Folder names pass
through unencoded (folder_encode off): the port's names are as LIST gave them, modified UTF-7
included, and the database stores them so. IMAPClient quotes a name itself, so a name goes to it
unescaped, and comes back from LIST unescaped, to be escaped again. The connection's own imaplib
object (`_imap`) carries the TLS socket for the certificate pin, and the commands txguard checks.

Only this module imports imapclient; only the new door loads it.
"""
import imaplib
import re
import socket
from datetime import datetime, timezone

from imapclient import IMAPClient

from mailmind.mailbox.adapters.imap_names import escaped, unescaped
from mailmind.mailbox.application import session as port
from mailmind.mailbox.application.session import Headers, Selected, move_by_copy
from mailmind.mailbox.domain.roles import Folder


_APPENDUID = re.compile(rb"APPENDUID\s+\d+\s+(\d+)")
_CHANGES = ("EXISTS", "RECENT", "EXPUNGE")


def _text(v):
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def connect(host, port, *, starttls, ssl_context, timeout, check_tls, username, password, guard=None):
    """A logged-in session. `check_tls(imap)` runs after the TLS handshake and before LOGIN, given
    the connection's imaplib object (its `sock` the TLS socket), and may raise to stop it; `guard`
    wraps that object before anything is sent. `password` may be a function, asked only after
    check_tls passed. A refused login raises imaplib.IMAP4.error."""
    client = IMAPClient(host, port=port, ssl=not starttls, ssl_context=ssl_context, timeout=timeout)
    client.folder_encode = False
    client.normalise_times = False          # an INTERNALDATE keeps its zone, rather than turning local
    try:
        if guard is not None:
            guard(client._imap)
        if starttls:
            client.starttls(ssl_context)
        check_tls(client._imap)
        client.login(username, password() if callable(password) else password)
    except BaseException:
        try:
            client.shutdown()
        except Exception:
            pass
        raise
    return ImapClientSession(client)


class ImapClientSession:
    def __init__(self, client):
        self._c = client
        self.capabilities = frozenset(_text(c).upper() for c in client.capabilities())

    def folders(self):
        out = []
        for flags, delim, name in self._c.list_folders():
            name = _text(name)
            if any(ch in name for ch in "\r\n\x00") or name in ("", ".", "/"):
                continue
            out.append(Folder(escaped(name), frozenset(_text(f).lower() for f in flags),
                              _text(delim) if delim is not None else None))
        return out

    def status(self, folder):
        got = self._c.folder_status(unescaped(folder), [b"UNSEEN", b"MESSAGES"])
        return int(got.get(b"UNSEEN", 0)), int(got.get(b"MESSAGES", 0))

    def select(self, folder, *, readonly=True):
        got = self._c.select_folder(unescaped(folder), readonly=readonly)
        return Selected(int(got.get(b"EXISTS", 0)), got.get(b"UIDVALIDITY"), got.get(b"UIDNEXT"))

    def uids(self, *criteria):
        return sorted(int(u) for u in self._c.search(" ".join(criteria) if criteria else "ALL"))

    def by_message_id(self, message_id):
        if not message_id:
            return None
        found = sorted(self._c.search(["HEADER", "Message-ID", message_id]))
        return int(found[-1]) if found else None

    def flags(self, uids):
        if not uids:
            return {}
        return {int(u): frozenset(_text(f) for f in fs) for u, fs in self._c.get_flags(list(uids)).items()}

    def headers(self, uids, fields, *, gmail_thread=False, internaldate=False):
        items = ["UID", "FLAGS"] + (["X-GM-THRID"] if gmail_thread else []) + \
            (["INTERNALDATE"] if internaldate else []) + ["BODY.PEEK[HEADER.FIELDS (" + " ".join(fields) + ")]"]
        uids, out = [int(u) for u in uids], []
        for start in range(0, len(uids), port.HEADER_BATCH):
            for uid, got in self._c.fetch(uids[start:start + port.HEADER_BATCH], items).items():
                raw = next((v for k, v in got.items() if bytes(k).upper().startswith(b"BODY[HEADER")), None)
                thread, arrived = got.get(b"X-GM-THRID"), got.get(b"INTERNALDATE")
                out.append(Headers(int(uid), frozenset(_text(f) for f in got.get(b"FLAGS", ())), raw or b"",
                                   str(thread) if thread is not None else None,
                                   arrived.astimezone(timezone.utc) if isinstance(arrived, datetime) else None))
        return sorted(out, key=lambda h: h.uid)

    def body(self, uid):
        got = self._c.fetch([int(uid)], ["BODY.PEEK[]"])
        return (got.get(int(uid)) or {}).get(b"BODY[]")

    def add_flags(self, uids, flags):
        self._c.add_flags([int(u) for u in uids], list(flags))

    def remove_flags(self, uids, flags):
        self._c.remove_flags([int(u) for u in uids], list(flags))

    def add_labels(self, uids, labels):
        self._c.add_gmail_labels([int(u) for u in uids], list(labels), silent=True)

    def remove_labels(self, uids, labels):
        self._c.remove_gmail_labels([int(u) for u in uids], list(labels), silent=True)

    def copy(self, uids, dest):
        self._c.copy([int(u) for u in uids], unescaped(dest))

    def move(self, uids, dest):
        if "MOVE" in self.capabilities:
            try:
                self._c.move([int(u) for u in uids], unescaped(dest))
                return
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                pass                     # refused: copy them instead, as a server without MOVE needs
        move_by_copy(self, uids, dest)

    def expunge(self, uids):
        self._c.uid_expunge([int(u) for u in uids])

    def append(self, folder, raw, *, flags=(), when=None):
        said = self._c.append(unescaped(folder), raw, flags=list(flags),
                              msg_time=when if isinstance(when, datetime) else None)
        found = _APPENDUID.search(said if isinstance(said, bytes) else str(said or "").encode())
        return int(found.group(1)) if found else None

    def noop(self):
        self._c.noop()
        return True

    def idle(self, seconds):
        # Through the imaplib connection underneath: IMAPClient's idle_check reads an EOF as "nothing
        # yet", so a connection the server dropped, or interrupt() shut down, would spin until the
        # time was up. imaplib's IDLE raises IMAP4.abort on it.
        with self._c._imap.idle(duration=seconds) as idler:
            for typ, _ in idler:
                if typ in _CHANGES:
                    return True              # leaving the block sends DONE
        return False

    def interrupt(self):
        try:                             # shut down, never closed here: see the imaplib adapter
            self._c.socket().shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def logout(self):
        self._c.logout()

    def shutdown(self):
        self._c.shutdown()
