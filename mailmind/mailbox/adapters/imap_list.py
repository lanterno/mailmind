"""The folders an imaplib LIST answer names.

Each line is `(attributes) delimiter name`: the delimiter quoted or NIL, the name an atom, a quoted
string or a literal. imaplib hands a literal back as a (prefix, bytes) pair. A name keeps the form
the server gave it, a quoted one with its escapes in place, so accounts.mailbox_arg can send it
back as it came; a literal's quotes and backslashes are escaped to that form, and one holding a
line break is left out, since it can't be sent back.

Standard library only.
"""
import re

from mailmind.mailbox.domain.roles import Folder

_HEAD = re.compile(r'^\(([^)]*)\)\s+("(?:[^"\\]|\\.)*"|NIL)\s*(.*)$', re.S)


def _delimiter(tok):
    if tok == "NIL":
        return None
    return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")


def parse(data):
    """[Folder] from imaplib's LIST data, skipping anything that isn't a LIST line."""
    out = []
    for item in data or []:
        literal = None
        if isinstance(item, tuple):
            item, literal = item[0], item[1]
        if not isinstance(item, (bytes, bytearray)) or not item:
            continue
        m = _HEAD.match(item.decode("utf-8", "replace"))
        if not m:
            continue
        attrs = frozenset(a.lower() for a in m.group(1).split())
        rest = m.group(3).strip()
        if literal is not None:
            name = literal.decode("utf-8", "replace")
            if "\r" in name or "\n" in name or "\x00" in name:
                continue
            name = name.replace("\\", "\\\\").replace('"', '\\"')
        elif rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
            name = rest[1:-1]
        else:
            name = rest
        if name in ("", ".", "/"):
            continue
        out.append(Folder(name, attrs, _delimiter(m.group(2))))
    return out


def list_folders(M):
    """The folders on M's server, [] when it refuses the LIST."""
    typ, data = M.list()
    if typ != "OK":
        return []
    return parse(data)
