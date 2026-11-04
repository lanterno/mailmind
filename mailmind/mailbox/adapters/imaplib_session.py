"""The MailSession port over an imaplib connection: what the old front door runs on.

imaplib hands a server's NO back as a typed answer rather than raising it; every command here
checks it and raises imaplib.IMAP4.error, as the port says. Standard library only.
"""
import imaplib
import re

from mailmind.mailbox.adapters import imap_list
from mailmind.mailbox.adapters.imap_names import imap_quote, mailbox_arg
from mailmind.mailbox.application import session as port
from mailmind.mailbox.application.session import Headers, Selected, move_by_copy

_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_UID = re.compile(rb"UID (\d+)")
_THRID = re.compile(rb"X-GM-THRID (\d+)")
_APPENDUID = re.compile(rb"APPENDUID\s+\d+\s+(\d+)")
_SYSTEM_LABEL = re.compile(r"\\[A-Za-z]+")


def _ok(what, typ, data):
    if typ != "OK":
        raise imaplib.IMAP4.error(f"{what} refused: {data!r}"[:200])
    return data


def _uid_set(uids):
    return ",".join(str(int(u)) for u in uids)


def _flag_list(flags):
    return "(" + " ".join(flags) + ")"


def _label_list(labels):
    """Gmail's labels: a system label (\\Inbox) as it is, any other quoted."""
    return "(" + " ".join(lb if _SYSTEM_LABEL.fullmatch(lb) else imap_quote(lb) for lb in labels) + ")"


def _number(M, name):
    _, data = M.response(name)
    try:
        return int(data[0]) if data and data[0] is not None else None
    except (TypeError, ValueError):
        return None


class ImaplibSession:
    def __init__(self, M):
        self._M = M
        self.capabilities = frozenset(str(c).upper() for c in getattr(M, "capabilities", ()))

    def folders(self):
        return imap_list.list_folders(self._M)

    def status(self, folder):
        data = _ok("STATUS", *self._M.status(mailbox_arg(folder), "(UNSEEN MESSAGES)"))
        text = b" ".join(d for d in data if isinstance(d, bytes))
        found = dict((k.upper(), int(v)) for k, v in re.findall(rb"(\w+) (\d+)", text))
        return found.get(b"UNSEEN", 0), found.get(b"MESSAGES", 0)

    def select(self, folder, *, readonly=True):
        data = _ok("SELECT", *self._M.select(mailbox_arg(folder), readonly=readonly))
        exists = int(data[0]) if data and data[0] else 0
        return Selected(exists, _number(self._M, "UIDVALIDITY"), _number(self._M, "UIDNEXT"))

    def uids(self, *criteria):
        data = _ok("SEARCH", *self._M.uid("SEARCH", None, *criteria))
        return sorted(int(u) for u in (data[0] or b"").split()) if data else []

    def by_message_id(self, message_id):
        if not message_id:
            return None
        found = self.uids("HEADER", "Message-ID", imap_quote(message_id))
        return found[-1] if found else None

    def flags(self, uids):
        if not uids:
            return {}
        data = _ok("FETCH", *self._M.uid("FETCH", _uid_set(uids), "(FLAGS)"))
        out = {}
        for item in data:
            line = item[0] if isinstance(item, tuple) else item
            uid, flags = _UID.search(line or b""), _FLAGS.search(line or b"")
            if uid and flags:
                out[int(uid.group(1))] = frozenset(f.decode() for f in flags.group(1).split())
        return out

    def headers(self, uids, fields, *, gmail_thread=False):
        items = ("(UID FLAGS " + ("X-GM-THRID " if gmail_thread else "")
                 + "BODY.PEEK[HEADER.FIELDS (" + " ".join(fields) + ")])")
        uids, out = [int(u) for u in uids], []
        for start in range(0, len(uids), port.HEADER_BATCH):
            data = _ok("FETCH", *self._M.uid("FETCH", _uid_set(uids[start:start + port.HEADER_BATCH]), items))
            for i, item in enumerate(data):
                if not isinstance(item, tuple):
                    continue
                after = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
                meta = (item[0] or b"") + after          # a server may send FLAGS after the literal
                uid, flags, thread = _UID.search(meta), _FLAGS.search(meta), _THRID.search(meta)
                if uid:
                    out.append(Headers(int(uid.group(1)),
                                       frozenset(f.decode() for f in flags.group(1).split()) if flags else frozenset(),
                                       item[1] or b"", thread.group(1).decode() if thread else None))
        return sorted(out, key=lambda h: h.uid)

    def body(self, uid):
        data = _ok("FETCH", *self._M.uid("FETCH", str(int(uid)), "(BODY.PEEK[])"))
        return next((p[1] for p in data if isinstance(p, tuple)), None)

    def add_flags(self, uids, flags):
        _ok("STORE", *self._M.uid("STORE", _uid_set(uids), "+FLAGS", _flag_list(flags)))

    def remove_flags(self, uids, flags):
        _ok("STORE", *self._M.uid("STORE", _uid_set(uids), "-FLAGS", _flag_list(flags)))

    def add_labels(self, uids, labels):
        _ok("STORE", *self._M.uid("STORE", _uid_set(uids), "+X-GM-LABELS", _label_list(labels)))

    def remove_labels(self, uids, labels):
        _ok("STORE", *self._M.uid("STORE", _uid_set(uids), "-X-GM-LABELS", _label_list(labels)))

    def copy(self, uids, dest):
        _ok("COPY", *self._M.uid("COPY", _uid_set(uids), mailbox_arg(dest)))

    def move(self, uids, dest):
        if "MOVE" in self.capabilities:
            try:
                _ok("MOVE", *self._M.uid("MOVE", _uid_set(uids), mailbox_arg(dest)))
                return
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                pass                     # refused: copy them instead, as a server without MOVE needs
        move_by_copy(self, uids, dest)

    def expunge_uid(self, uid):
        _ok("EXPUNGE", *self._M.uid("EXPUNGE", str(int(uid))))

    def append(self, folder, raw, *, flags=(), when=None):
        date = imaplib.Time2Internaldate(when) if when is not None else None
        data = _ok("APPEND", *self._M.append(mailbox_arg(folder), _flag_list(flags) if flags else None, date, raw))
        said = next((m for m in (_APPENDUID.search(d) for d in data if isinstance(d, bytes)) if m), None)
        return int(said.group(1)) if said else None

    def noop(self):
        typ, _ = self._M.noop()
        return typ == "OK"

    def logout(self):
        self._M.logout()
