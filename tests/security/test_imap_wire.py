"""IMAP argument fuzzing: whatever a folder name, a Message-ID, a label or a search value holds, no CR, LF or NUL reaches the
wire in a command. A line break would end the command and start another of the sender's choosing (RFC 9051's quoted
strings can't carry one), and a sender can fold the Message-ID header so the ID stored holds one.

This reads what each adapter hands the socket, by way of `imaplib.IMAP4.send`, which IMAPClient writes through too. Message
bytes are the one thing that may hold line ends, in APPEND's literal. test_mail_session_contract.py has what a refusal
looks like; test_hardening.py a scripted connection's few worked cases.
"""
import imaplib
import re

import pytest
from hypothesis import assume, given, strategies as st

from fixtures import world as w
from property_runs import runs

RECEIPT = "ada-inbox-05-receipt.eml"
RAW = b"From: ada@example.test\r\nSubject: A draft\r\n\r\nHello\r\n"
INJECTED = "\r\nA1 DELETE INBOX"
# What a command line starts with: imaplib's tag, four letters this connection drew and a count, then a word.
_COMMAND = re.compile(rb"([A-P]{4})(\d+) [A-Z]")
_LITERAL = re.compile(rb"\{(\d+)\+?\}$")
NASTY = st.sampled_from(["\r", "\n", "\r\n", "\x00", INJECTED, "\n* OK", '"', "\\", '\\"', "\\\\", "{5}", "{5+}", " ", "(", ")", "%", "*"])
VALUE = st.lists(NASTY | st.sampled_from(["INBOX", "Archive", "<m@example.test>", "é", "日本"]) | st.text(max_size=3), max_size=6).map("".join)


def faults(chunks):
    """What's wrong with these writes to one connection, read as the stream a server reads. A line break in an argument
    shows as a line ending early, and the next as no command: it holds no tag of this connection's, in turn. A tag may be
    skipped, by a command imaplib couldn't encode once it had drawn one."""
    stream, wrong, at, starting, tag = b"".join(chunks), [], 0, True, None
    while at < len(stream):
        end = stream.find(b"\n", at)
        line = stream[at:] if end < 0 else stream[at:end + 1]
        at += len(line)
        if starting and line != b"DONE\r\n":
            command = _COMMAND.match(line)
            if not command or (tag and (command[1] != tag[0] or int(command[2]) <= tag[1])):
                wrong.append(("not a command of this connection's", line))
            else:
                tag = command[1], int(command[2])
        if not line.endswith(b"\r\n") or b"\r" in line[:-2] or b"\n" in line[:-1] or b"\x00" in line:
            wrong.append(("a stray line end or NUL", line))
        size = _LITERAL.search(line.rstrip(b"\r\n"))
        starting = size is None
        if size:
            literal = stream[at:at + int(size.group(1))]
            at += len(literal)
            if literal != RAW and set(literal) & set(b"\r\n\x00"):
                wrong.append(("a literal holding one", literal))
    return wrong


@pytest.fixture
def wire(monkeypatch):
    """Everything written to any IMAP connection, by connection."""
    writes = {}
    real = imaplib.IMAP4.send

    def send(self, data):
        writes.setdefault(id(self), []).append(bytes(data))
        return real(self, data)
    monkeypatch.setattr(imaplib.IMAP4, "send", send)
    return writes


def on_the_wire(writes):
    return [fault for chunks in writes.values() for fault in faults(chunks)]


def test_a_stray_line_break_is_seen():
    """So that a clean wire below means a clean wire."""
    assert faults([b"ABCD1 SELECT \"INBOX\"\r\n", b"ABCD2 NOOP\r\n"]) == []
    assert faults([b"ABCD1 APPEND \"Drafts\" {" + str(len(RAW)).encode() + b"}\r\n", RAW, b"\r\n", b"ABCD2 NOOP\r\n"]) == []
    assert faults([b"ABCD1 SELECT \"INBOX\r\nA1 DELETE INBOX\"\r\n"])
    assert faults([b"ABCD1 SELECT \"INBOX\r\nABCD2 DELETE INBOX\"\r\n", b"ABCD2 NOOP\r\n"])
    assert faults([b"ABCD1 SELECT \"INBOX\nX\"\r\n"])
    assert faults([b"ABCD1 SELECT \"IN\rBOX\"\r\n"])
    assert faults([b"ABCD1 SELECT \"IN\x00BOX\"\r\n"])
    assert faults([b"ABCD1 SELECT {3}\r\n", b"a\nb", b"\r\n"])


# ── each session call, through each adapter ──────────────────────────────────────────────────────

OPS = {
    "select": lambda s, v, uid: s.select(v),
    "select to write": lambda s, v, uid: s.select(v, readonly=False),
    "status": lambda s, v, uid: s.status(v),
    "copy": lambda s, v, uid: s.copy([uid], v),
    "move": lambda s, v, uid: s.move([uid], v),
    "append": lambda s, v, uid: s.append(v, RAW),
    "by_message_id": lambda s, v, uid: s.by_message_id(v),
    "add_labels": lambda s, v, uid: s.add_labels([uid], [v]),
    "remove_labels": lambda s, v, uid: s.remove_labels([uid], [v]),
    "a search": lambda s, v, uid: s.uids("HEADER", "Message-ID", _quote(v)),
    "a sender search": lambda s, v, uid: s.uids("FROM", _quote(v)),
}


def _quote(value):
    from mailmind.bootstrap import accounts
    return accounts.imap_quote(value)


@pytest.fixture(params=["imaplib", "imapclient"])
def adapter(request, world):
    from mailmind.bootstrap import mailbox
    mailbox.use(request.param)
    yield request.param
    mailbox.use("imaplib")


class Connection:
    """A session that comes back if a call leaves it unusable, with INBOX selected for each call to act in."""

    def __init__(self):
        self.s = None

    def ready(self):
        from mailmind.bootstrap import accounts, mailbox
        if self.s is None:
            self.s = mailbox.open_session(accounts.get(w.ADA), timeout=10)
        self.s.select("INBOX", readonly=False)
        return self.s

    def lost(self):
        s, self.s = self.s, None
        try:
            s.shutdown()
        except Exception:
            pass


@given(op=st.sampled_from(sorted(OPS)), value=VALUE)
@runs(150)
def test_no_call_puts_a_line_break_on_the_wire(world, adapter, wire, connection_of_test, op, value):
    assume(not (adapter == "imapclient" and op == "by_message_id" and "{" in value))   # the stall pinned below
    uid = world.uid(RECEIPT)
    try:
        OPS[op](connection_of_test.ready(), value, uid)
    except imaplib.IMAP4.abort:
        connection_of_test.lost()
    except (imaplib.IMAP4.error, UnicodeError):       # refused, by the session or the server, or no way to send it
        pass
    assert on_the_wire(wire) == [], (op, value)


@pytest.fixture
def connection_of_test(adapter, wire):
    made = Connection()
    yield made
    if made.s is not None:
        made.lost()


@pytest.mark.parametrize("value", ["INBOX\r\nA1 DELETE Archive", "INBOX\nA1 DELETE Archive", "IN\rBOX", "IN\x00BOX"])
@pytest.mark.parametrize("op", sorted(OPS))
def test_a_line_break_is_refused_before_anything_is_sent(world, adapter, wire, connection_of_test, op, value):
    s = connection_of_test.ready()
    wire.clear()
    since = world.mark()
    with pytest.raises(imaplib.IMAP4.error):
        OPS[op](s, value, world.uid(RECEIPT))
    assert wire == {} and world.server.commands[since:] == []
    assert world.server.mailbox(w.ADA, "Archive") is not None


ODD_IDS = ("<a(b@example.test>", "<a)b@example.test>", "{5}")


@pytest.mark.parametrize("kind, message_id", [
    *[("imaplib", i) for i in ODD_IDS], *[("imapclient", i) for i in ODD_IDS],
    ("imaplib", "<a b@example.test>"), ("imapclient", "<a b@example.test>")])
def test_a_message_id_is_one_argument_whatever_it_holds(world, kind, message_id):
    from mailmind.bootstrap import accounts, mailbox
    uid = world.server.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID=message_id))
    mailbox.use(kind)
    s = mailbox.open_session(accounts.get(w.ADA), timeout=1)
    try:
        s.select("INBOX")
        assert s.by_message_id(message_id) == uid
    finally:
        s.shutdown()
        mailbox.use("imaplib")


# ── from the routes in ───────────────────────────────────────────────────────────────────────────

EVIL = "Archive" + INJECTED


def test_a_move_to_an_injected_folder(app, world, wire):
    pk = world.pk(RECEIPT)
    since = world.mark()
    r = app.post("/api/move", {"pk": pk, "dest": EVIL})
    assert r.json["ok"] is False
    assert on_the_wire(wire) == [] and world.writes(since) == []
    assert world.where(RECEIPT)["server"][0][1] == "INBOX"


def test_opening_a_folder_with_an_injected_name(app, world, wire):
    app.get(f"/api/messages?account={w.ADA}&folder=INBOX%0D%0AA1%20DELETE%20Archive")
    app.get(f"/api/messages?account={w.ADA}&folder=INBOX%00")
    assert on_the_wire(wire) == []


def stored(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def test_a_stored_message_id_holding_a_line_break(app, world, wire):
    """As a folded header can leave it. A later try looks the message up by this id, and so does Undo."""
    pk = world.pk(RECEIPT)
    stored("UPDATE messages SET message_id=? WHERE pk=?", "<m@example.test>" + INJECTED, pk)
    app.post("/api/archive", {"pk": pk})
    app.post("/api/undo", {})
    assert on_the_wire(wire) == []


def test_a_stored_folder_holding_a_line_break(app, world, wire):
    pk = world.pk(RECEIPT)
    stored("UPDATE messages SET folder=? WHERE pk=?", EVIL, pk)
    app.get(f"/api/message/{pk}/body")
    app.post("/api/archive", {"pk": pk})
    assert on_the_wire(wire) == []


def test_an_expiry_rule_holding_a_line_break(world, wire):
    """Its token goes into a sender search on the server."""
    from mailmind.bootstrap import accounts, curation
    curation.rules.save({**curation.rules.load(), curation.rules.EXPIRE: {"lin@example.test" + INJECTED: 1}})
    curation.expiry.account(accounts.get(w.ADA), dry=True)
    assert on_the_wire(wire) == []


# imaplib writes LOGIN's user as it is and only escapes quotes in the password, and neither path checks them first as
# mailbox_arg and imap_quote check everything else.
TYPED_LOGINS = [("dee@example.test" + INJECTED, "app-password"), ("dee@example.test", "app-password" + INJECTED),
                ("dee@example.test\x00", "app-password"), ("dee@example.test", "app\npassword")]


@pytest.mark.parametrize("user, password", TYPED_LOGINS)
def test_trying_an_account_typed_with_a_line_break_in_its_login(app, world, wire, user, password):
    """The settings page sends what was typed, to a host that was typed too, and answers with what the login said."""
    entry = world.server.config_entry(w.ADA)
    app.post("/api/accounts/test", {"draft": {
        "email": user, "type": "imap", "host": entry["host"], "port": entry["port"], "starttls": entry.get("starttls", True),
        "credential": {"style": "plain", "value": password}}})
    assert on_the_wire(wire) == []


@pytest.mark.parametrize("user, password", TYPED_LOGINS)
def test_opening_a_session_with_a_line_break_in_its_login(world, adapter, wire, user, password):
    """A saved account's address, or a password pasted with its line end into the config, on either adapter."""
    from mailmind.bootstrap import accounts, mailbox
    acct = {**accounts.get(w.ADA), "email": user, "credential": {"plain": password}}
    with pytest.raises(imaplib.IMAP4.error):
        mailbox.open_session(acct, timeout=10)
    assert on_the_wire(wire) == []
