"""The MailSession contract (mailmind.mailbox.application.session): every case runs through each
adapter, imaplib's and IMAPClient's, against the fake IMAP server, and each must behave the same.
A case states what the port promises: what a call returns, what it sends, what it leaves on the
server, and how a refusal, a dropped connection, a read-only process, a changed certificate and
an open transaction surface."""
import imaplib
from datetime import datetime, timezone

import pytest

from fixtures import world as w
from mailmind.platform.db.txguard import TxAcrossNetworkError

RECEIPT = "ada-inbox-05-receipt.eml"


@pytest.fixture(params=["imaplib", "imapclient"])
def kind(request):
    from mailmind.bootstrap import mailbox
    mailbox.use(request.param)
    yield request.param
    mailbox.use("imaplib")


def open_session(email=w.ADA):
    import accounts
    from mailmind.bootstrap import mailbox
    return mailbox.open_session(accounts.get(email), timeout=10)


@pytest.fixture
def session(world, kind):
    s = open_session()
    yield s
    try:
        s.logout()
    except Exception:
        pass


def sent(world, since, user=w.ADA):
    """The commands sent since `since`, without their tags."""
    return [c["line"].split(" ", 1)[1] for c in world.server.commands[since:] if c["user"] == user]


def box(world, name="INBOX", user=w.ADA):
    return world.server.mailbox(user, name)


def uid_of(world, name):
    return world.placed[name][2]


# ── reading ─────────────────────────────────────────────────────────────────────────────────────

def test_capabilities_are_what_the_server_said_after_login(session):
    assert {"IMAP4REV1", "MOVE", "UIDPLUS", "IDLE", "SPECIAL-USE"} <= session.capabilities


def test_folders_carry_their_attributes_and_delimiter(session):
    from mailmind.mailbox.domain import roles
    folders = {f.name: f for f in session.folders()}
    assert {"INBOX", "Sent", "Drafts", "Archive", "Junk", "Trash"} <= set(folders)
    assert "\\junk" in folders["Junk"].attributes and folders["Junk"].delimiter == "/"
    assert roles.find(list(folders.values()), "junk") == "Junk"


def test_odd_folder_names_come_back_as_list_gave_them_and_work_as_given(world, kind):
    from fakes.imap_server import Mailbox
    a = world.server.accounts[w.ADA]
    with world.server.lock:
        for name in ("Caf&AOk-", 'Say "hi"', "Back\\slash"):
            a.mailboxes[name] = Mailbox(name, uidvalidity=1_700_000_990 + len(a.mailboxes))
    s = open_session()
    try:
        names = {f.name for f in s.folders()}
        assert {"Caf&AOk-", 'Say \\"hi\\"', "Back\\\\slash"} <= names
        for name in ("Caf&AOk-", 'Say \\"hi\\"', "Back\\\\slash"):
            assert s.status(name) == (0, 0)
            s.append(name, w.raw(RECEIPT), flags=["\\Seen"])
            assert s.select(name).exists == 1
        assert [m.flags for m in a.mailboxes['Say "hi"'].messages.values()] == [{"\\Seen"}]
    finally:
        s.logout()


def test_a_folder_name_that_cant_be_sent_is_refused_before_it_is(world, session):
    import accounts
    since = len(world.server.commands)
    for bad in ('a"b', "line\r\nbreak", "trailing\\"):
        with pytest.raises(accounts.BadMailbox):
            session.select(bad)
    assert sent(world, since) == []


def test_status_counts_unseen_and_all(world, session):
    inbox = box(world)
    assert session.status("INBOX") == (sum("\\Seen" not in m.flags for m in inbox.messages.values()),
                                       len(inbox.messages))


def test_select_says_how_many_and_the_uid_validity(world, session):
    inbox = box(world)
    assert session.select("INBOX") == type(session.select("INBOX"))(len(inbox.messages), inbox.uidvalidity,
                                                                      inbox.uidnext)


def test_select_read_only_is_examine(world, session):
    since = len(world.server.commands)
    session.select("INBOX")
    session.select("INBOX", readonly=False)
    assert sent(world, since) == ['EXAMINE "INBOX"', 'SELECT "INBOX"']


def test_uids_search_the_selected_folder_in_order(world, session):
    session.select("INBOX")
    every = sorted(box(world).messages)
    assert session.uids("ALL") == every
    assert session.uids("UID", f"{every[3]}:*") == every[3:]
    assert session.uids("UNSEEN") == sorted(u for u, m in box(world).messages.items() if "\\Seen" not in m.flags)


def test_a_message_is_found_by_its_message_id_quotes_and_all(world, session):
    odd = w.variant(RECEIPT, Message_ID='<odd"quote@example.test>')
    uid = world.server.deliver(w.ADA, "INBOX", odd)
    session.select("INBOX")
    assert session.by_message_id(w.message_id(RECEIPT)) == uid_of(world, RECEIPT)
    assert session.by_message_id('<odd"quote@example.test>') == uid
    assert session.by_message_id("<nobody@example.test>") is None
    assert session.by_message_id("") is None


def test_a_body_is_the_message_as_stored_and_read_leaves_it_unread(world, session):
    uid = uid_of(world, RECEIPT)
    session.select("INBOX")
    assert session.body(uid) == box(world).messages[uid].raw
    assert "\\Seen" not in box(world).messages[uid].flags
    assert session.body(99999) is None


def test_flags_read_add_and_remove(world, session):
    uid = uid_of(world, RECEIPT)
    session.select("INBOX", readonly=False)
    assert session.flags([uid]) == {uid: frozenset(box(world).messages[uid].flags)}
    session.add_flags([uid], ["\\Flagged", "\\Seen"])
    assert {"\\Flagged", "\\Seen"} <= box(world).messages[uid].flags
    session.remove_flags([uid], ["\\Flagged"])
    assert "\\Flagged" not in box(world).messages[uid].flags
    assert session.flags([]) == {}


# ── filing ──────────────────────────────────────────────────────────────────────────────────────

def test_move_moves_with_move(world, session):
    uid = uid_of(world, RECEIPT)
    archived = len(box(world, "Archive").messages)
    session.select("INBOX", readonly=False)
    since = len(world.server.commands)
    session.move(uid, "Archive")
    assert sent(world, since) == [f'UID MOVE {uid} "Archive"']
    assert uid not in box(world).messages and len(box(world, "Archive").messages) == archived + 1


def test_move_without_move_copies_then_removes_that_uid_alone(world, kind):
    world.server.without = {"MOVE"}
    other = uid_of(world, "ada-inbox-04-ci.eml")
    box(world).messages[other].flags.add("\\Deleted")       # another client's, not to be expunged
    archived = len(box(world, "Archive").messages)
    s = open_session()
    try:
        uid = uid_of(world, RECEIPT)
        s.select("INBOX", readonly=False)
        since = len(world.server.commands)
        s.move(uid, "Archive")
        assert [line.split(" ")[1] for line in sent(world, since)] == ["COPY", "STORE", "EXPUNGE"]
    finally:
        s.logout()
    assert uid not in box(world).messages and other in box(world).messages
    assert len(box(world, "Archive").messages) == archived + 1


def test_a_refused_move_falls_back_to_copy(world, session):
    uid = uid_of(world, RECEIPT)
    archived = len(box(world, "Archive").messages)
    world.server.fail("UID MOVE", "BAD [CANNOT] not today")
    session.select("INBOX", readonly=False)
    session.move(uid, "Archive")
    assert uid not in box(world).messages and len(box(world, "Archive").messages) == archived + 1


def test_a_refused_copy_raises_and_touches_nothing(world, session):
    uid = uid_of(world, RECEIPT)
    world.server.fail("UID MOVE")
    world.server.fail("UID COPY")
    archive = dict(box(world, "Archive").messages)
    session.select("INBOX", readonly=False)
    with pytest.raises(imaplib.IMAP4.error):
        session.move(uid, "Archive")
    assert box(world).messages[uid].flags == set() and box(world, "Archive").messages == archive


def test_expunge_removes_only_that_uid(world, session):
    one, two = uid_of(world, RECEIPT), uid_of(world, "ada-inbox-04-ci.eml")
    session.select("INBOX", readonly=False)
    session.add_flags([one, two], ["\\Deleted"])
    session.expunge_uid(one)
    assert one not in box(world).messages and two in box(world).messages


def test_append_lands_with_its_flags_and_date(world, session):
    when = datetime(2026, 10, 30, 9, 15, tzinfo=timezone.utc)
    before = set(box(world, "Drafts").messages)
    session.append("Drafts", w.raw(RECEIPT), flags=["\\Draft", "\\Seen"], when=when)
    (m,) = [m for u, m in box(world, "Drafts").messages.items() if u not in before]
    crlf = w.raw(RECEIPT).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")     # IMAP's line ends
    assert (m.raw, m.flags, m.internaldate) == (crlf, {"\\Draft", "\\Seen"}, when)


def test_noop_answers(session):
    assert session.noop() is True


# ── when things go wrong ───────────────────────────────────────────────────────────────────────

def test_a_refused_command_raises_imaplibs_error(world, session):
    world.server.fail("STATUS")
    with pytest.raises(imaplib.IMAP4.error) as e:
        session.status("INBOX")
    assert not isinstance(e.value, imaplib.IMAP4.abort)


def test_a_dropped_connection_raises_abort_or_oserror(world, session):
    import mailops
    session.select("INBOX")
    world.server.drop("UID FETCH")
    with pytest.raises(mailops._UNREACHABLE):
        session.body(uid_of(world, RECEIPT))


def test_a_refused_login_is_unreachable(world, kind):
    import accounts
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] invalid credentials", user=w.ADA)
    with pytest.raises(accounts.LoginUnreachable):
        open_session()


def test_a_changed_certificate_is_refused_before_login(world, kind):
    import config
    from mailmind.accounts.domain import tls_pin
    raw = config._raw()
    next(a for a in raw["accounts"] if a["email"] == w.ADA)["tls_pins"] = {
        f"127.0.0.1:{world.server.port}": "sha256:" + "0" * 64}
    config.save(raw)
    with pytest.raises(tls_pin.TLSPinMismatch):
        open_session()
    assert world.server.commands_for(w.ADA, "LOGIN") == []


def test_the_first_session_pins_the_certificate(world, kind):
    import config
    open_session().logout()
    entry = next(a for a in config._raw()["accounts"] if a["email"] == w.ADA)
    assert entry["tls_pins"] == {f"127.0.0.1:{world.server.port}": "sha256:" + world.server.cert_sha256}


def test_a_read_only_process_examines_and_writes_nothing(world, kind, monkeypatch):
    from mailmind.platform import profile
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    s = open_session()
    try:
        since = len(world.server.commands)
        s.select("INBOX", readonly=False)
        for write in (lambda: s.add_flags([1], ["\\Seen"]), lambda: s.remove_flags([1], ["\\Seen"]),
                      lambda: s.move(1, "Archive"), lambda: s.copy(1, "Archive"), lambda: s.expunge_uid(1),
                      lambda: s.append("Drafts", b"x")):
            with pytest.raises(profile.ReadOnlyError):
                write()
        assert sent(world, since) == ['EXAMINE "INBOX"']
    finally:
        s.logout()


def test_no_command_goes_while_a_transaction_is_open(world, session):
    import store
    c = store.conn()
    try:
        c.execute("INSERT INTO sync_state(account, folder, uidvalidity, last_uid) VALUES (?, 'Guarded', 1, 1)",
                  (w.ADA,))
        since = len(world.server.commands)
        with pytest.raises(TxAcrossNetworkError):
            session.noop()
        with pytest.raises(TxAcrossNetworkError):
            open_session()
        assert sent(world, since) == []
    finally:
        c.rollback()
        c.close()
    assert session.noop()
