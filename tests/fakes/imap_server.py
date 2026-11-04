"""A scripted IMAP server on a loopback port, for tests that drive MailMind's real imaplib code.

It speaks enough IMAP4rev1 for everything the app sends: CAPABILITY, STARTTLS, LOGIN, LIST,
STATUS, SELECT and EXAMINE, UID SEARCH, UID FETCH, UID STORE, UID COPY, UID MOVE, UID EXPUNGE,
EXPUNGE, APPEND, IDLE, NOOP, CLOSE and LOGOUT, with literals both ways. Each account has a kind:

- "imap": a plain server with UIDPLUS, MOVE, IDLE and SPECIAL-USE.
- "proton": Proton Bridge's habits. A message copied or moved into Trash is marked read.
- "gmail": X-GM-EXT-1. Every message also sits in [Gmail]/All Mail, carries X-GM-THRID and
  X-GM-MSGID, and STORE +/-X-GM-LABELS (\\Inbox) adds it to or drops it from INBOX. A message
  re-labelled \\Inbox gets its old INBOX UID back, as the app's ingest_message expects. Trash
  and Spam are outside All Mail: a message moved into either leaves All Mail and every label,
  and one moved back out returns to All Mail under its old UID.

STARTTLS is required before LOGIN (the app must never send a password in the clear), with a
throwaway certificate from trustme. The app trusts any certificate on loopback, as it does for
Bridge.

Tests script trouble with `fail`, `drop` and `drop_after`, change a folder's UIDVALIDITY with
`renumber`, and read every command the app sent from `commands`, with passwords redacted.

    with FakeIMAPServer() as srv:
        acct = srv.add_account("ada@example.test", "pw")
        srv.deliver("ada@example.test", "INBOX", raw_bytes)
        srv.fail("UID MOVE", "NO [CANNOT] not today")
        ... point a MailMind account at 127.0.0.1:srv.port with starttls ...
"""
import email.parser
import email.utils
import hashlib
import itertools
import queue
import re
import socket
import socketserver
import ssl
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import trustme

KINDS = ("imap", "proton", "gmail")

FOLDERS = {
    "imap":   [("INBOX", ""), ("Sent", "\\Sent"), ("Drafts", "\\Drafts"), ("Trash", "\\Trash"),
               ("Junk", "\\Junk"), ("Archive", "\\Archive")],
    "proton": [("INBOX", ""), ("Sent", "\\Sent"), ("Drafts", "\\Drafts"), ("Trash", "\\Trash"),
               ("Spam", "\\Junk"), ("Archive", "\\Archive"), ("All Mail", "\\All"), ("Folders", "\\Noselect"),
               ("Labels", "\\Noselect")],
    "gmail":  [("INBOX", ""), ("[Gmail]", "\\Noselect"), ("[Gmail]/All Mail", "\\All"),
               ("[Gmail]/Sent Mail", "\\Sent"), ("[Gmail]/Drafts", "\\Drafts"), ("[Gmail]/Trash", "\\Trash"),
               ("[Gmail]/Spam", "\\Junk"), ("[Gmail]/Starred", "\\Flagged")],
}
ALL_MAIL = "[Gmail]/All Mail"
OUTSIDE_ALL_MAIL = ("\\Trash", "\\Junk")       # Gmail's Trash and Spam
SYSTEM_FLAGS = ("\\Seen", "\\Answered", "\\Flagged", "\\Deleted", "\\Draft")

UIDVALIDITY_BASE = 1_700_000_001
GM_ID_BASE = 1_800_000_000_000_000_001


# ── the mail store ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Message:
    raw: bytes
    flags: set = field(default_factory=set)
    internaldate: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    gm_msgid: int = 0
    gm_thrid: int = 0
    labels: set = field(default_factory=set)       # Gmail only: user labels, as X-GM-LABELS shows them

    def headers(self):
        return email.parser.BytesHeaderParser().parsebytes(self.raw)

    def header_block(self):
        end = self.raw.find(b"\r\n\r\n")
        sep = 4
        if end < 0:
            end, sep = self.raw.find(b"\n\n"), 2
        return self.raw if end < 0 else self.raw[:end + sep]

    def header_fields(self, names, exclude=False):
        """The header lines (folded continuations kept) whose names are in `names`, in message
        order, then the blank line: what BODY[HEADER.FIELDS (...)] returns."""
        want = {n.lower() for n in names}
        out, keep = [], False
        for line in self.header_block().splitlines(keepends=True):
            if line in (b"\r\n", b"\n"):
                break
            if line[:1] in (b" ", b"\t"):
                if keep:
                    out.append(line)
                continue
            name = line.split(b":", 1)[0].decode("ascii", "replace").strip().lower()
            keep = (name not in want) if exclude else (name in want)
            if keep:
                out.append(line)
        return b"".join(out) + b"\r\n"


@dataclass
class Mailbox:
    name: str
    special: str = ""
    uidvalidity: int = UIDVALIDITY_BASE
    uidnext: int = 1
    messages: dict = field(default_factory=dict)    # uid -> Message, in UID order
    former: dict = field(default_factory=dict)      # Gmail INBOX, All Mail: gm_msgid -> the UID it had

    @property
    def selectable(self):
        return "\\Noselect" not in self.special

    def add(self, msg, uid=None):
        if uid is None or uid in self.messages:
            uid = self.uidnext
        self.messages[uid] = msg
        self.messages = dict(sorted(self.messages.items()))
        self.uidnext = max(self.uidnext, uid + 1)
        return uid

    def uids(self):
        return list(self.messages)

    def seq_of(self, uid):
        return self.uids().index(uid) + 1


@dataclass
class Account:
    user: str
    password: str
    kind: str
    mailboxes: dict = field(default_factory=dict)

    def box(self, name):
        if name.upper() == "INBOX":
            name = "INBOX"
        return self.mailboxes.get(name)


# ── scripted trouble ────────────────────────────────────────────────────────────────────────────

@dataclass
class Fault:
    command: str                # "UID MOVE", "SELECT", "LOGIN"... matched on the command words
    answer: str | None = None   # the tagged reply instead of running it, e.g. "NO [UNAVAILABLE] busy"
    drop: bool = False          # close the connection instead of answering
    after: bool = False         # run the command, then drop before the tagged reply
    stall: bool = False         # answer nothing until release_stalls(), then drop: a black hole
    times: int | None = 1       # how many matching commands it hits; None = every one
    when: object = None         # optional predicate on the command's arguments (a list of tokens)
    user: str | None = None     # only this account's connections


# ── parsing ─────────────────────────────────────────────────────────────────────────────────────

class Quoted(str):
    """A quoted-string argument, so `"INBOX"` and `INBOX` stay distinguishable if a test cares."""


class ParseError(Exception):
    pass


def _tokens(data, i=0, close=None):
    """Parse IMAP arguments: atoms (brackets kept whole, so BODY.PEEK[HEADER.FIELDS (A B)] is
    one atom), quoted strings, parenthesized lists and literals (already read, as bytes)."""
    out = []
    n = len(data)
    while i < n:
        c = data[i]
        if isinstance(c, bytes):                         # a literal the reader spliced in
            out.append(c)
            i += 1
            continue
        if c == " ":
            i += 1
        elif c == "(":
            sub, i = _tokens(data, i + 1, ")")
            out.append(sub)
        elif c == ")":
            if close != ")":
                raise ParseError("unbalanced )")
            return out, i + 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and data[j] != '"':
                if data[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(data[j])
                j += 1
            if j >= n:
                raise ParseError("unterminated quoted string")
            out.append(Quoted("".join(buf)))
            i = j + 1
        else:
            j, depth = i, 0
            while j < n and not isinstance(data[j], bytes):
                ch = data[j]
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                elif depth == 0 and ch in " ()":
                    break
                j += 1
            out.append("".join(data[i:j]))
            i = j
    if close:
        raise ParseError("unbalanced (")
    return out, i


def parse_args(chars):
    return _tokens(chars)[0]


def parse_set(spec, top):
    """An IMAP sequence set (`1,3:5,7:*`) as a predicate over numbers; `*` is `top`. As on real
    servers, `n:*` with n above `top` still matches `top`."""
    ranges = []
    for part in str(spec).split(","):
        if ":" in part:
            a, b = part.split(":", 1)
            a = top if a == "*" else int(a)
            b = top if b == "*" else int(b)
            ranges.append((min(a, b), max(a, b)))
        else:
            v = top if part == "*" else int(part)
            ranges.append((v, v))
    return lambda x: any(lo <= x <= hi for lo, hi in ranges)


def _flag_list(tok):
    items = tok if isinstance(tok, list) else str(tok).strip("()").split()
    return [str(f) for f in items]


def _norm_flag(f):
    for s in SYSTEM_FLAGS:
        if f.lower() == s.lower():
            return s
    return f


def _imap_date(s):
    return datetime.strptime(str(s), "%d-%b-%Y").date()


def _quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── the protocol ────────────────────────────────────────────────────────────────────────────────

class _Drop(Exception):
    """Close the connection now, without a tagged reply."""


class _No(Exception):
    def __init__(self, text, status="NO"):
        super().__init__(text)
        self.status, self.text = status, text


class Session:
    def __init__(self, server, sock):
        self.srv, self.sock = server, sock
        self.rfile = sock.makefile("rb")
        self.tls = False
        self.acct = None
        self.box = None                                  # the selected Mailbox
        self.readonly = False
        self.events = queue.Queue()                      # untagged lines pushed while idling
        self.mute_tagged = False                         # drop_after: run, but never send the tagged reply

    # wire
    def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.sock.sendall(data)

    def line(self, text):
        self.send(text + "\r\n")

    def read_command(self):
        """One command line, with any literals read and spliced in as bytes."""
        chars = []
        while True:
            raw = self.rfile.readline(1 << 20)
            if not raw:
                return None
            text = raw.decode("utf-8", "surrogateescape").rstrip("\r\n")
            m = re.search(r"\{(\d+)(\+?)\}$", text)
            if not m:
                chars.extend(text)
                return chars
            chars.extend(text[:m.start()])
            if not m.group(2):
                self.line("+ Ready for literal data")
            chars.append(self.rfile.read(int(m.group(1))))

    def run(self):
        self.line("* OK [CAPABILITY " + self.capabilities() + "] fake IMAP ready")
        while True:
            chars = self.read_command()
            if chars is None:
                return
            text = "".join(c if isinstance(c, str) else "{literal}" for c in chars)
            tag, _, rest = text.partition(" ")
            if not tag or not rest:
                self.line("* BAD no tag")
                continue
            parts = rest.split(" ")
            cmd = parts[0].upper()
            if cmd == "UID" and len(parts) > 1:
                cmd = "UID " + parts[1].upper()
            try:
                args = parse_args(chars[len(tag) + 1 + len(cmd) + 1:])   # literals stay bytes
            except (ParseError, ValueError):
                self.line(f"{tag} BAD could not parse arguments")
                continue
            self.srv._record(self, tag, cmd, args, text)
            fault = self.srv._fault_for(self, cmd, args)
            try:
                if fault and fault.stall:
                    self.srv._unstalled.wait()
                    raise _Drop()
                if fault and fault.drop and not fault.after:
                    raise _Drop()
                if fault and fault.answer and not fault.after:
                    self.line(f"{tag} {fault.answer}")
                    continue
                self.mute_tagged = bool(fault and fault.after)
                done = self.dispatch(tag, cmd, args)
                if fault and fault.after:
                    raise _Drop()
                if done is False:
                    return
            except _Drop:
                return
            except _No as e:
                self.line(f"{tag} {e.status} {e.text}")
            except (ParseError, ValueError, IndexError, KeyError) as e:
                self.line(f"{tag} BAD {type(e).__name__}: {e}")

    def capabilities(self):
        """As Gmail and Dovecot do, the extensions only a logged-in client may use are listed only
        once it has: before login, and in the login answer unless the server is told not to."""
        caps = ["IMAP4rev1", "LITERAL+", "IDLE"]
        if self.acct is not None:
            caps += ["UIDPLUS", "MOVE", "SPECIAL-USE", "NAMESPACE"]
        if not self.tls:
            caps += ["STARTTLS", "LOGINDISABLED"]
        else:
            caps += ["AUTH=PLAIN"]
        if (self.acct.kind if self.acct else self.srv.default_kind) == "gmail":
            caps.append("X-GM-EXT-1")
        return " ".join(c for c in caps if c not in self.srv.without)

    # dispatch
    def dispatch(self, tag, cmd, args):
        handler = getattr(self, "do_" + cmd.replace(" ", "_").lower(), None)
        if handler is None or cmd in self.srv.without or cmd.replace("UID ", "") in self.srv.without:
            raise _No(f"{cmd} unknown", "BAD")
        authed = cmd in ("CAPABILITY", "NOOP", "LOGOUT", "STARTTLS", "LOGIN")
        if not authed and self.acct is None:
            raise _No("log in first", "BAD")
        needs_box = cmd.startswith("UID ") or cmd in ("FETCH", "STORE", "SEARCH", "EXPUNGE", "CLOSE", "IDLE")
        if needs_box and cmd != "IDLE" and self.box is None:
            raise _No("select a mailbox first", "BAD")
        with self.srv.lock:
            return handler(tag, args)

    def ok(self, tag, text="completed"):
        if not self.mute_tagged:
            self.line(f"{tag} OK {text}")

    def do_capability(self, tag, args):
        self.line("* CAPABILITY " + self.capabilities())
        self.ok(tag)

    def do_noop(self, tag, args):
        self.ok(tag)

    def do_logout(self, tag, args):
        self.line("* BYE fake IMAP logging out")
        self.ok(tag)
        return False

    def do_starttls(self, tag, args):
        if self.tls:
            raise _No("already in TLS", "BAD")
        self.ok(tag, "begin TLS negotiation now")
        self.sock = self.srv.tls_context.wrap_socket(self.sock, server_side=True)
        self.rfile = self.sock.makefile("rb")
        self.tls = True

    def do_login(self, tag, args):
        if self.srv.require_tls and not self.tls:
            raise _No("[PRIVACYREQUIRED] use STARTTLS first")
        user, pw = str(args[0]), str(args[1])
        acct = self.srv.accounts.get(user)
        if not acct or acct.password != pw:
            raise _No("[AUTHENTICATIONFAILED] invalid credentials")
        self.acct = acct
        self.ok(tag, "[CAPABILITY " + self.capabilities() + "] logged in" if self.srv.login_says_capabilities
                else "logged in")

    def do_list(self, tag, args):
        pattern = str(args[1]) if len(args) > 1 else "*"
        rx = re.compile("^" + re.escape(pattern).replace(r"\*", ".*").replace("%", "[^/]*") + "$")
        for box in self.acct.mailboxes.values():
            if rx.match(box.name):
                attrs = " ".join(a for a in (box.special, "\\HasNoChildren") if a)
                self.line(f'* LIST ({attrs}) "/" {_quote(box.name)}')
        self.ok(tag)

    def do_status(self, tag, args):
        box = self._box(args[0])
        items = [str(x).upper() for x in args[1]]
        vals = {"MESSAGES": len(box.messages), "UIDNEXT": box.uidnext, "UIDVALIDITY": box.uidvalidity,
                "UNSEEN": sum(1 for m in box.messages.values() if "\\Seen" not in m.flags), "RECENT": 0}
        body = " ".join(f"{k} {vals[k]}" for k in items if k in vals)
        self.line(f"* STATUS {_quote(box.name)} ({body})")
        self.ok(tag)

    def _box(self, name):
        box = self.acct.box(str(name))
        if box is None or not box.selectable:
            raise _No("[NONEXISTENT] no such mailbox")
        return box

    def _select(self, tag, args, readonly):
        self.box = None
        box = self._box(args[0])
        self.box, self.readonly = box, readonly
        self.line("* FLAGS (" + " ".join(SYSTEM_FLAGS) + ")")
        self.line("* OK [PERMANENTFLAGS (" + " ".join(SYSTEM_FLAGS) + " \\*)] flags")
        self.line(f"* {len(box.messages)} EXISTS")
        self.line("* 0 RECENT")
        self.line(f"* OK [UIDVALIDITY {box.uidvalidity}] UIDs valid")
        self.line(f"* OK [UIDNEXT {box.uidnext}] predicted next UID")
        self.ok(tag, "[READ-ONLY] EXAMINE completed" if readonly else "[READ-WRITE] SELECT completed")

    def do_select(self, tag, args):
        self._select(tag, args, False)

    def do_examine(self, tag, args):
        self._select(tag, args, True)

    def do_close(self, tag, args):
        if not self.readonly:
            for uid in [u for u, m in self.box.messages.items() if "\\Deleted" in m.flags]:
                self.srv._remove(self.acct, self.box, uid)
        self.box = None
        self.ok(tag)

    # search
    def _match(self, keys, uid, msg):
        i = 0
        while i < len(keys):
            ok, i = self._key(keys, i, uid, msg)
            if not ok:
                return False
        return True

    def _key(self, keys, i, uid, msg):
        k = keys[i]
        if isinstance(k, list):
            return self._match(k, uid, msg), i + 1
        K = str(k).upper()
        flags = {f.lower() for f in msg.flags}
        simple = {"ALL": True, "SEEN": "\\seen" in flags, "UNSEEN": "\\seen" not in flags,
                  "FLAGGED": "\\flagged" in flags, "UNFLAGGED": "\\flagged" not in flags,
                  "DELETED": "\\deleted" in flags, "UNDELETED": "\\deleted" not in flags,
                  "ANSWERED": "\\answered" in flags, "UNANSWERED": "\\answered" not in flags,
                  "DRAFT": "\\draft" in flags, "UNDRAFT": "\\draft" not in flags,
                  "NEW": "\\seen" not in flags, "OLD": True, "RECENT": False}
        if K in simple:
            return simple[K], i + 1
        if K == "NOT":
            ok, j = self._key(keys, i + 1, uid, msg)
            return not ok, j
        if K == "OR":
            a, j = self._key(keys, i + 1, uid, msg)
            b, j = self._key(keys, j, uid, msg)
            return a or b, j
        if K == "UID":
            return parse_set(keys[i + 1], max(self.box.messages, default=0))(uid), i + 2
        if K == "HEADER":
            name, val = str(keys[i + 1]), str(keys[i + 2]).lower()
            got = [str(v) for v in (msg.headers().get_all(name) or [])]
            return (any(val in g.lower() for g in got) if val else bool(got)), i + 3
        if K in ("FROM", "TO", "CC", "BCC", "SUBJECT"):
            val = str(keys[i + 1]).lower()
            return any(val in str(v).lower() for v in (msg.headers().get_all(K) or [])), i + 2
        if K in ("BODY", "TEXT"):
            val = str(keys[i + 1]).lower().encode()
            hay = msg.raw.lower() if K == "TEXT" else msg.raw[len(msg.header_block()):].lower()
            return val in hay, i + 2
        if K in ("SINCE", "BEFORE", "ON"):
            d, day = _imap_date(keys[i + 1]), msg.internaldate.date()
            return {"SINCE": day >= d, "BEFORE": day < d, "ON": day == d}[K], i + 2
        if K in ("LARGER", "SMALLER"):
            n = int(keys[i + 1])
            return (len(msg.raw) > n if K == "LARGER" else len(msg.raw) < n), i + 2
        if re.fullmatch(r"[\d:,*]+", K):
            return parse_set(K, len(self.box.messages))(self.box.seq_of(uid)), i + 1
        raise _No(f"unsupported search key {K}", "BAD")

    def do_uid_search(self, tag, args):
        if args and str(args[0]).upper() == "CHARSET":
            args = args[2:]
        hits = [u for u, m in self.box.messages.items() if self._match(args, u, m)]
        self.line("* SEARCH" + "".join(f" {u}" for u in hits))
        self.ok(tag)

    def do_search(self, tag, args):
        if args and str(args[0]).upper() == "CHARSET":
            args = args[2:]
        hits = [self.box.seq_of(u) for u, m in self.box.messages.items() if self._match(args, u, m)]
        self.line("* SEARCH" + "".join(f" {s}" for s in hits))
        self.ok(tag)

    # fetch
    def _items(self, tok):
        items = tok if isinstance(tok, list) else [tok]
        out = []
        for it in items:
            s = str(it)
            u = s.upper()
            macros = {"ALL": ["FLAGS", "INTERNALDATE", "RFC822.SIZE"],
                      "FAST": ["FLAGS", "INTERNALDATE", "RFC822.SIZE"],
                      "FULL": ["FLAGS", "INTERNALDATE", "RFC822.SIZE"]}
            out.extend(macros.get(u, [s]))
        return out

    def _fetch_one(self, uid, msg, items):
        parts, literal, set_seen = [f"UID {uid}"], None, False
        for it in items:
            u = it.upper()
            if u == "UID":
                continue
            if u == "FLAGS":
                parts.append("FLAGS (" + " ".join(sorted(msg.flags)) + ")")
            elif u == "INTERNALDATE":
                parts.append('INTERNALDATE "' + msg.internaldate.strftime("%d-%b-%Y %H:%M:%S %z") + '"')
            elif u == "RFC822.SIZE":
                parts.append(f"RFC822.SIZE {len(msg.raw)}")
            elif u in ("X-GM-THRID", "X-GM-MSGID", "X-GM-LABELS"):
                if self.acct.kind != "gmail":
                    raise _No(f"unknown fetch item {it}", "BAD")
                if u == "X-GM-THRID":
                    parts.append(f"X-GM-THRID {msg.gm_thrid}")
                elif u == "X-GM-MSGID":
                    parts.append(f"X-GM-MSGID {msg.gm_msgid}")
                else:
                    parts.append("X-GM-LABELS (" + " ".join(sorted(self.srv._labels_of(self.acct, msg))) + ")")
            else:
                m = re.fullmatch(r"(BODY|BODY\.PEEK|RFC822|RFC822\.HEADER|RFC822\.TEXT)(\[(.*)\])?(<\d+\.\d+>)?",
                                 it, re.I | re.S)
                if not m:
                    raise _No(f"unknown fetch item {it}", "BAD")
                kind, section = m.group(1).upper(), (m.group(3) or "")
                sec = section.upper()
                if kind == "RFC822.HEADER" or sec == "HEADER":
                    data = msg.header_block()
                elif kind == "RFC822.TEXT" or sec == "TEXT":
                    data = msg.raw[len(msg.header_block()):]
                elif sec.startswith("HEADER.FIELDS"):
                    names = re.findall(r"[^\s()]+", section[section.index("(") + 1:section.rindex(")")])
                    data = msg.header_fields(names, exclude=sec.startswith("HEADER.FIELDS.NOT"))
                elif sec == "":
                    data = msg.raw
                else:
                    raise _No(f"unsupported section {section}", "BAD")
                name = "BODY[" + section + "]" if kind in ("BODY", "BODY.PEEK") else kind
                literal = (name, data)
                if kind in ("BODY", "RFC822", "RFC822.TEXT") and not self.readonly:
                    set_seen = True
        if set_seen and "\\Seen" not in msg.flags:
            msg.flags.add("\\Seen")
            if not any(p.startswith("FLAGS") for p in parts):
                parts.append("FLAGS (" + " ".join(sorted(msg.flags)) + ")")
        head = " ".join(parts)
        seq = self.box.seq_of(uid)
        if literal:
            name, data = literal
            self.send(f"* {seq} FETCH ({head} {name} {{{len(data)}}}\r\n".encode() + data + b")\r\n")
        else:
            self.line(f"* {seq} FETCH ({head})")

    def do_uid_fetch(self, tag, args):
        top = max(self.box.messages, default=0)
        want = parse_set(args[0], top)
        items = self._items(args[1])
        for uid, msg in list(self.box.messages.items()):
            if want(uid):
                self._fetch_one(uid, msg, items)
        self.ok(tag)

    def do_fetch(self, tag, args):
        want = parse_set(args[0], len(self.box.messages))
        items = self._items(args[1])
        for uid, msg in list(self.box.messages.items()):
            if want(self.box.seq_of(uid)):
                self._fetch_one(uid, msg, items)
        self.ok(tag)

    # writes
    def _writable(self):
        if self.readonly:
            raise _No("[READ-ONLY] mailbox is read-only")

    def do_uid_store(self, tag, args):
        self._writable()
        want = parse_set(args[0], max(self.box.messages, default=0))
        op = str(args[1]).upper()
        silent = op.endswith(".SILENT")
        op = op.removesuffix(".SILENT")
        values = _flag_list(args[2])
        if op.endswith("X-GM-LABELS"):
            if self.acct.kind != "gmail":
                raise _No("X-GM-LABELS needs X-GM-EXT-1", "BAD")
            for uid in [u for u in list(self.box.messages) if want(u)]:
                self.srv._relabel(self.acct, self.box, uid, op[0] if op[0] in "+-" else "=", values)
            self.ok(tag)
            return
        if op not in ("FLAGS", "+FLAGS", "-FLAGS"):
            raise _No(f"bad STORE item {op}", "BAD")
        flags = {_norm_flag(f) for f in values}
        for uid, msg in list(self.box.messages.items()):
            if not want(uid):
                continue
            if op == "FLAGS":
                msg.flags = set(flags)
            elif op == "+FLAGS":
                msg.flags |= flags
            else:
                msg.flags -= flags
            if not silent:
                self.line(f"* {self.box.seq_of(uid)} FETCH (UID {uid} FLAGS (" + " ".join(sorted(msg.flags)) + "))")
        self.ok(tag)

    def _copy(self, args):
        dest = self.acct.box(str(args[1]))
        if dest is None or not dest.selectable:
            raise _No("[TRYCREATE] no such mailbox")
        want = parse_set(args[0], max(self.box.messages, default=0))
        moved = [u for u in self.box.messages if want(u)]
        new = [self.srv._copy_into(self.acct, self.box, u, dest) for u in moved]
        return dest, moved, new

    def do_uid_copy(self, tag, args):
        dest, src, new = self._copy(args)
        if not src:
            self.ok(tag, "nothing to copy")
            return
        self.ok(tag, f"[COPYUID {dest.uidvalidity} {','.join(map(str, src))} {','.join(map(str, new))}] copied")

    def do_uid_move(self, tag, args):
        self._writable()
        dest, src, new = self._copy(args)
        if src:
            self.line(f"* OK [COPYUID {dest.uidvalidity} {','.join(map(str, src))} {','.join(map(str, new))}]")
        for uid in src:
            self.line(f"* {self.box.seq_of(uid)} EXPUNGE")
            self.srv._remove(self.acct, self.box, uid)
        self.ok(tag, "moved")

    def _expunge(self, want):
        for uid in [u for u, m in self.box.messages.items() if "\\Deleted" in m.flags and want(u)]:
            self.line(f"* {self.box.seq_of(uid)} EXPUNGE")
            self.srv._remove(self.acct, self.box, uid)

    def do_uid_expunge(self, tag, args):
        self._writable()
        self._expunge(parse_set(args[0], max(self.box.messages, default=0)))
        self.ok(tag)

    def do_expunge(self, tag, args):
        self._writable()
        self._expunge(lambda u: True)
        self.ok(tag)

    def do_append(self, tag, args):
        box = self.acct.box(str(args[0]))
        if box is None or not box.selectable:
            raise _No("[TRYCREATE] no such mailbox")
        rest = args[1:]
        flags, when = set(), None
        if rest and isinstance(rest[0], list):
            flags = {_norm_flag(f) for f in _flag_list(rest[0])}
            rest = rest[1:]
        if rest and isinstance(rest[0], Quoted):
            when = datetime.strptime(str(rest[0]), "%d-%b-%Y %H:%M:%S %z")
            rest = rest[1:]
        if not rest or not isinstance(rest[0], bytes):
            raise _No("APPEND needs a literal", "BAD")
        uid = self.srv._store_new(self.acct, box, rest[0], flags, when)
        self.ok(tag, "appended" if "UIDPLUS" in self.srv.without else f"[APPENDUID {box.uidvalidity} {uid}] appended")

    def do_idle(self, tag, args):
        self.line("+ idling")
        self.srv._idlers.add(self)
        try:
            self.srv.lock.release()                      # don't hold the store while waiting
            try:
                self.sock.settimeout(0.05)
                buf = b""
                while True:
                    try:
                        while True:
                            self.line(self.events.get_nowait())
                    except queue.Empty:
                        pass
                    try:
                        chunk = self.sock.recv(64)
                    except (socket.timeout, TimeoutError, ssl.SSLWantReadError):
                        continue
                    if not chunk:
                        raise _Drop()
                    buf += chunk
                    if b"\n" in buf:
                        break
            finally:
                self.sock.settimeout(None)
                self.srv.lock.acquire()
        finally:
            self.srv._idlers.discard(self)
        if buf.strip().upper() != b"DONE":
            raise _No("expected DONE", "BAD")
        self.ok(tag, "IDLE terminated")


# ── the server ──────────────────────────────────────────────────────────────────────────────────

class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server.fake
        srv.connections += 1
        s = Session(srv, self.request)
        srv._sessions.add(s)
        try:
            if srv.implicit_tls:
                s.sock = srv.tls_context.wrap_socket(s.sock, server_side=True)
                s.rfile, s.tls = s.sock.makefile("rb"), True
            s.run()
        except (OSError, ssl.SSLError):
            pass
        finally:
            srv._sessions.discard(s)
            try:
                s.sock.close()
            except OSError:
                pass


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        # named, so the app fixture can tell the fake's sessions from threads the app started
        threading.Thread(target=self.process_request_thread, args=(request, client_address),
                         name="fake-imap-session", daemon=True).start()
    allow_reuse_address = True


class FakeIMAPServer:
    def __init__(self, *, kind="imap", require_tls=True, implicit_tls=False, without=()):
        """kind: the default account kind (imap, proton, gmail). without: capabilities or
        commands to leave out, e.g. ("MOVE",) or ("UIDPLUS", "EXPUNGE")."""
        assert kind in KINDS
        self.default_kind = kind
        self.require_tls, self.implicit_tls = require_tls, implicit_tls
        self.without = set(without)
        self.login_says_capabilities = True     # False: the login answer carries no CAPABILITY code
        if "UIDPLUS" in self.without:
            self.without.add("UID EXPUNGE")
        self.accounts = {}
        # Per server, so the same accounts and deliveries in a new server get the same UIDVALIDITY
        # and Gmail ids: a database built against one server stays valid against the next.
        self._uidv = itertools.count(UIDVALIDITY_BASE)
        self._gm = itertools.count(GM_ID_BASE)
        self.commands = []
        self.connections = 0
        self.lock = threading.RLock()
        self._faults = []
        self._unstalled = threading.Event()
        self._unstalled.set()
        self._sessions = set()
        self._idlers = set()
        self.ca = trustme.CA()
        cert = self.ca.issue_cert("127.0.0.1", "localhost")
        self.tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        cert.configure_cert(self.tls_context)
        der = ssl.PEM_cert_to_DER_cert(cert.cert_chain_pems[0].bytes().decode())
        self.cert_sha256 = hashlib.sha256(der).hexdigest()   # the certificate, as a pin names it
        self._tcp = None
        self.port = None

    # lifecycle
    def start(self):
        self._tcp = _TCP(("127.0.0.1", 0), _Handler)
        self._tcp.fake = self
        self.port = self._tcp.server_address[1]
        threading.Thread(target=self._tcp.serve_forever, kwargs={"poll_interval": 0.05},
                         name="fake-imap", daemon=True).start()
        return self

    def stop(self):
        self._unstalled.set()
        if self._tcp:
            self._tcp.shutdown()
            self._tcp.server_close()
            for s in list(self._sessions):
                try:
                    s.sock.close()
                except OSError:
                    pass
            self._tcp = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # accounts and mail
    def add_account(self, user, password="app-password", kind=None, folders=()):
        """Create an account with its kind's standard folders plus any extra `folders`, and return
        the MailMind config entry that points at it."""
        kind = kind or self.default_kind
        acct = Account(user, password, kind)
        for name, special in FOLDERS[kind] + [(f, "") for f in folders]:
            acct.mailboxes[name] = Mailbox(name, special, uidvalidity=next(self._uidv))
        with self.lock:
            self.accounts[user] = acct
        return self.config_entry(user)

    def config_entry(self, user):
        acct = self.accounts[user]
        return {"email": user, "type": acct.kind, "host": "127.0.0.1", "port": self.port,
                "starttls": not self.implicit_tls, "credential": {"plain": acct.password},
                "smtp_host": "127.0.0.1", "smtp_port": 9}   # SMTP is another fake's job

    def deliver(self, user, mailbox, raw, flags=(), when=None, thread_of=None):
        """Put a message in a mailbox, as mail arriving or already there. Returns its UID.
        when: its INTERNALDATE (default: its Date header, else now). thread_of: for Gmail, the
        raw bytes or Message-ID of a message whose X-GM-THRID it shares."""
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        with self.lock:
            acct = self.accounts[user]
            box = acct.box(mailbox)
            if when is None:
                when = self._date_of(raw)
            uid = self._store_new(acct, box, raw, {_norm_flag(f) for f in flags}, when, thread_of)
        return uid

    def mailbox(self, user, name):
        return self.accounts[user].box(name)

    def messages(self, user, name):
        """{uid: Message} for a mailbox, a snapshot."""
        with self.lock:
            return dict(self.accounts[user].box(name).messages)

    def find(self, user, name, message_id):
        """The UIDs in a mailbox whose Message-ID header equals message_id."""
        with self.lock:
            return [u for u, m in self.accounts[user].box(name).messages.items()
                    if (m.headers().get("Message-ID") or "").strip() == message_id]

    def renumber(self, user, name, uidvalidity=None):
        """A UIDVALIDITY change: new UIDVALIDITY, every message renumbered from 1, as a server
        does when it rebuilds a mailbox (Bridge after a re-sync)."""
        with self.lock:
            box = self.accounts[user].box(name)
            msgs = list(box.messages.values())
            box.uidvalidity = uidvalidity or next(self._uidv)
            box.messages, box.uidnext, box.former = {}, 1, {}
            for m in msgs:
                box.add(m)

    # scripted trouble
    def fail(self, command, answer="NO [UNAVAILABLE] scripted failure", *, times=1, when=None, user=None):
        """Answer the next `times` matching commands with `answer` instead of running them."""
        self._faults.append(Fault(command.upper(), answer=answer, times=times, when=when, user=user))

    def drop(self, command, *, times=1, when=None, user=None):
        """Close the connection when a matching command arrives, without running it."""
        self._faults.append(Fault(command.upper(), drop=True, times=times, when=when, user=user))

    def drop_after(self, command, *, times=1, when=None, user=None):
        """Run a matching command, then close the connection before its tagged reply: the client
        can't tell whether it happened."""
        self._faults.append(Fault(command.upper(), after=True, times=times, when=when, user=user))

    def stall(self, command, *, times=None, when=None, user=None):
        """Answer nothing to a matching command, as a black-holed server would, until
        release_stalls() (or stop()), then close the connection. Every match, by default."""
        self._unstalled.clear()
        self._faults.append(Fault(command.upper(), stall=True, times=times, when=when, user=user))

    def release_stalls(self):
        self._faults[:] = [f for f in self._faults if not f.stall]
        self._unstalled.set()

    def clear_faults(self):
        self._faults.clear()
        self._unstalled.set()

    def commands_for(self, user=None, command=None):
        """The recorded commands, optionally for one account and one command name."""
        return [c for c in self.commands
                if (user is None or c["user"] == user) and (command is None or c["command"] == command.upper())]

    # internals
    def _record(self, session, tag, cmd, args, text):
        with self.lock:
            self._record_locked(session, tag, cmd, args, text)

    def _record_locked(self, session, tag, cmd, args, text):
        if cmd == "LOGIN":
            text = f"{tag} LOGIN {args[0] if args else ''} <password>"
        user = session.acct.user if session.acct else (str(args[0]) if cmd == "LOGIN" and args else None)
        self.commands.append({"user": user, "mailbox": session.box.name if session.box else None,
                              "command": cmd, "args": args, "line": text})

    def _fault_for(self, session, cmd, args):
        with self.lock:
            return self._fault_for_locked(session, cmd, args)

    def _fault_for_locked(self, session, cmd, args):
        user = session.acct.user if session.acct else (str(args[0]) if cmd == "LOGIN" and args else None)
        for f in self._faults:
            if f.command != cmd or (f.user and f.user != user) or (f.when and not f.when(args)):
                continue
            if f.times is not None:
                f.times -= 1
                if f.times <= 0:
                    self._faults.remove(f)
            return f
        return None

    @staticmethod
    def _date_of(raw):
        try:
            d = email.utils.parsedate_to_datetime(
                email.parser.BytesHeaderParser().parsebytes(raw).get("Date", ""))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)

    def _thread_id(self, acct, thread_of):
        if thread_of is None:
            return None
        mid = thread_of
        if isinstance(thread_of, (bytes, bytearray)):
            mid = (email.parser.BytesHeaderParser().parsebytes(bytes(thread_of)).get("Message-ID") or "").strip()
        for box in acct.mailboxes.values():
            for m in box.messages.values():
                if (m.headers().get("Message-ID") or "").strip() == mid:
                    return m.gm_thrid
        return None

    def _store_new(self, acct, box, raw, flags, when=None, thread_of=None):
        msg = Message(raw=raw, flags=set(flags), internaldate=when or datetime.now(timezone.utc))
        if acct.kind == "gmail":
            msg.gm_msgid = next(self._gm)
            msg.gm_thrid = self._thread_id(acct, thread_of) or msg.gm_msgid
            if box.name != ALL_MAIL and box.special not in OUTSIDE_ALL_MAIL:
                self._join_all_mail(acct, msg)             # the same message object: one identity
        uid = box.add(msg)
        if box.name == "INBOX" and acct.kind == "gmail":
            box.former[msg.gm_msgid] = uid
        self._notify(acct, box)
        return uid

    def _copy_into(self, acct, src, uid, dest):
        msg = src.messages[uid]
        if acct.kind == "gmail":
            if dest.special in OUTSIDE_ALL_MAIL and src.special not in OUTSIDE_ALL_MAIL:
                self._leave_all_mail(acct, msg, keep=src)
            elif src.special in OUTSIDE_ALL_MAIL and dest.special not in OUTSIDE_ALL_MAIL:
                self._join_all_mail(acct, msg)
            if dest.name == ALL_MAIL:
                for u, m in dest.messages.items():
                    if m is msg:
                        return u
                return dest.add(msg)
            if dest.name == "INBOX":
                return self._label_inbox(acct, msg)
            new = Message(raw=msg.raw, flags=set(msg.flags), internaldate=msg.internaldate,
                          gm_msgid=msg.gm_msgid, gm_thrid=msg.gm_thrid, labels=set(msg.labels))
        else:
            new = Message(raw=msg.raw, flags=set(msg.flags), internaldate=msg.internaldate)
        new.flags.discard("\\Deleted")
        if acct.kind == "proton" and dest.special == "\\Trash":
            new.flags.add("\\Seen")                        # Bridge marks mail read on its way to Trash
        uid = dest.add(new)
        self._notify(acct, dest)
        return uid

    def _join_all_mail(self, acct, msg):
        box = acct.mailboxes[ALL_MAIL]
        for u, m in box.messages.items():
            if m.gm_msgid == msg.gm_msgid:
                return u
        uid = box.add(msg, box.former.get(msg.gm_msgid))
        box.former[msg.gm_msgid] = uid
        return uid

    def _leave_all_mail(self, acct, msg, keep):
        """Into Trash or Spam: out of All Mail and every label but `keep`, the source, which the
        command itself empties (MOVE now, COPY's client with STORE and EXPUNGE)."""
        for box in acct.mailboxes.values():
            if box is keep or box.special in OUTSIDE_ALL_MAIL:
                continue
            for u, m in list(box.messages.items()):
                if m.gm_msgid == msg.gm_msgid:
                    box.messages.pop(u)

    def _label_inbox(self, acct, msg):
        inbox = acct.mailboxes["INBOX"]
        for u, m in inbox.messages.items():
            if m.gm_msgid == msg.gm_msgid:
                return u
        uid = inbox.add(msg, inbox.former.get(msg.gm_msgid))
        inbox.former[msg.gm_msgid] = uid
        self._notify(acct, inbox)
        return uid

    def _labels_of(self, acct, msg):
        labels = set(msg.labels)
        if any(m.gm_msgid == msg.gm_msgid for m in acct.mailboxes["INBOX"].messages.values()):
            labels.add('"\\\\Inbox"')
        return labels

    def _relabel(self, acct, box, uid, op, values):
        msg = box.messages[uid]
        inbox = [v for v in values if v.lower() in ("\\inbox", "inbox")]
        others = [v for v in values if v not in inbox]
        if inbox:
            if op in "+=":
                self._label_inbox(acct, msg)
            elif op == "-":
                for u, m in list(acct.mailboxes["INBOX"].messages.items()):
                    if m.gm_msgid == msg.gm_msgid:
                        self._remove(acct, acct.mailboxes["INBOX"], u)
        if op == "+":
            msg.labels |= set(others)
        elif op == "-":
            msg.labels -= set(others)
        else:
            msg.labels = set(others)

    def _remove(self, acct, box, uid):
        """Expunge one message. On Gmail, leaving INBOX keeps it in All Mail, while leaving All
        Mail removes it everywhere."""
        msg = box.messages.pop(uid)
        if acct.kind == "gmail" and box.name == ALL_MAIL:
            for other in acct.mailboxes.values():          # gone from All Mail is gone everywhere
                for u, m in list(other.messages.items()):
                    if m is msg:
                        other.messages.pop(u)

    def _notify(self, acct, box):
        for s in list(self._idlers):
            if s.acct is acct and s.box is box:
                s.events.put(f"* {len(box.messages)} EXISTS")
