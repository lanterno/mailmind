"""The MailSession port over an imaplib connection: what the old front door runs on.

imaplib hands a server's NO back as a typed answer rather than raising it; every command here
checks it and raises imaplib.IMAP4.error, as the port says. Standard library only.
"""
import imaplib
import re

from mailmind.mailbox.adapters import imap_list
from mailmind.mailbox.adapters.imap_names import imap_quote, mailbox_arg
from mailmind.mailbox.application.session import Selected, move_by_copy

_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_UID = re.compile(rb"UID (\d+)")


def _ok(what, typ, data):
    if typ != "OK":
        raise imaplib.IMAP4.error(f"{what} refused: {data!r}"[:200])
    return data


def _uid_set(uids):
    return ",".join(str(int(u)) for u in uids)


def _flag_list(flags):
    return "(" + " ".join(flags) + ")"


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

    def body(self, uid):
        data = _ok("FETCH", *self._M.uid("FETCH", str(int(uid)), "(BODY.PEEK[])"))
        return next((p[1] for p in data if isinstance(p, tuple)), None)

    def add_flags(self, uids, flags):
        _ok("STORE", *self._M.uid("STORE", _uid_set(uids), "+FLAGS", _flag_list(flags)))

    def remove_flags(self, uids, flags):
        _ok("STORE", *self._M.uid("STORE", _uid_set(uids), "-FLAGS", _flag_list(flags)))

    def copy(self, uid, dest):
        _ok("COPY", *self._M.uid("COPY", str(int(uid)), mailbox_arg(dest)))

    def move(self, uid, dest):
        if "MOVE" in self.capabilities:
            try:
                _ok("MOVE", *self._M.uid("MOVE", str(int(uid)), mailbox_arg(dest)))
                return
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                pass                     # refused: copy it instead, as a server without MOVE needs
        move_by_copy(self, uid, dest)

    def expunge_uid(self, uid):
        _ok("EXPUNGE", *self._M.uid("EXPUNGE", str(int(uid))))

    def append(self, folder, raw, *, flags=(), when=None):
        date = imaplib.Time2Internaldate(when) if when is not None else None
        _ok("APPEND", *self._M.append(mailbox_arg(folder), _flag_list(flags) if flags else None, date, raw))

    def noop(self):
        typ, _ = self._M.noop()
        return typ == "OK"

    def logout(self):
        self._M.logout()
