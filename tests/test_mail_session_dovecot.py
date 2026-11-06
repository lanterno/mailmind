"""The MailSession contract against a real server: Dovecot 2.3 in Docker, through each adapter.

The fake IMAP server (tests/fakes/imap_server.py) is MailMind's own reading of IMAP, so a case it
passes can still fail on a real server: Dovecot names MOVE and UIDPLUS only once a user has logged
in, for one. These cases hold the promises tests/test_mail_session_contract.py states, seen
through a connection of the test's own instead of the fake's insides.

Marked dovecot, so skipped unless pytest runs with --dovecot (`just test-dovecot`), which needs
Docker. One container serves the whole run. Any user logs in with its one password, so each test
logs in as a user of its own and starts with an empty mailbox. The certificate is a throwaway
from trustme, which the app pins on first use, as it does Proton Bridge's.
"""
import email
import hashlib
import imaplib
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.dovecot

IMAGE = "dovecot/dovecot:2.3.21@sha256:1c18c756f20d03867077a1b509a6e2e3008ab1eafa56377b6f2eca12dc1ba581"
PASSWORD = "contract-only"

# Any user, one password, a mailbox of its own with the five special-use folders, over implicit
# TLS only. A refused login answers at once, and IDLE looks for new mail every second. The image
# is amd64 only, and on an arm64 Mac Rosetta needs more address space than Dovecot's default cap
# of 256 MB a process leaves it, so there is no cap.
CONF = r"""
protocols = imap
default_vsz_limit = 0
listen = *
log_path = /dev/stderr
mail_home = /srv/mail/%Lu
mail_location = sdbox:~/Mail
mail_uid = 1000
mail_gid = 1000
first_valid_uid = 1000
last_valid_uid = 1000
auth_mechanisms = plain login
auth_failure_delay = 0
passdb {
  driver = static
  args = password=@PASSWORD@
}
ssl = required
ssl_cert = </etc/dovecot/cert.pem
ssl_key = </etc/dovecot/key.pem
mailbox_idle_check_interval = 1 secs
namespace inbox {
  inbox = yes
  separator = /
  mailbox Drafts {
    auto = create
    special_use = \Drafts
  }
  mailbox Sent {
    auto = create
    special_use = \Sent
  }
  mailbox Archive {
    auto = create
    special_use = \Archive
  }
  mailbox Junk {
    auto = create
    special_use = \Junk
  }
  mailbox Trash {
    auto = create
    special_use = \Trash
  }
}
service imap-login {
  inet_listener imap {
    port = 0
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}
"""


@dataclass(frozen=True)
class Dovecot:
    port: int
    cert_sha256: str


def _docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def _logs(name):
    got = subprocess.run(["docker", "logs", "--tail", "40", name], capture_output=True, text=True)
    return got.stdout + got.stderr


def _unverified():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _greets(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as raw, \
                _unverified().wrap_socket(raw) as tls:
            return tls.recv(64).startswith(b"* OK")
    except OSError:
        return False


@pytest.fixture(scope="session")
def dovecot(tmp_path_factory):
    import trustme
    cert = trustme.CA().issue_cert("127.0.0.1", "localhost")
    d = tmp_path_factory.mktemp("dovecot")
    (d / "dovecot.conf").write_text(CONF.replace("@PASSWORD@", PASSWORD))
    cert.private_key_pem.write_to_path(str(d / "key.pem"))
    cert.cert_chain_pems[0].write_to_path(str(d / "cert.pem"))
    name = f"mailmind-contract-{uuid.uuid4().hex[:8]}"
    _docker("create", "--name", name, "--platform", "linux/amd64", "-p", "127.0.0.1::993", IMAGE)
    try:
        for f in ("dovecot.conf", "cert.pem", "key.pem"):
            _docker("cp", str(d / f), f"{name}:/etc/dovecot/{f}")
        _docker("start", name)
        port = int(_docker("port", name, "993/tcp").splitlines()[0].rsplit(":", 1)[1])
        ends = time.monotonic() + 90
        while not _greets(port):
            if time.monotonic() > ends:
                pytest.fail("Dovecot never answered:\n" + _logs(name))
            time.sleep(0.5)
        der = ssl.PEM_cert_to_DER_cert(cert.cert_chain_pems[0].bytes().decode())
        yield Dovecot(port, hashlib.sha256(der).hexdigest())
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture(params=["imaplib", "imapclient"])
def kind(request):
    from mailmind.bootstrap import mailbox
    mailbox.use(request.param)
    yield request.param
    mailbox.use("imaplib")


def write_config(data_dir, user, port, password=PASSWORD):
    entry = {"email": user, "type": "imap", "host": "127.0.0.1", "port": port, "starttls": False,
             "credential": {"plain": password}, "smtp_host": "127.0.0.1", "smtp_port": 9}
    with open(os.path.join(data_dir, "mailmind.config.json"), "w") as f:
        json.dump({"accounts": [entry]}, f)


@pytest.fixture
def user(dovecot, data_dir, kind):
    """A user of this test's own, the one account in the config."""
    name = f"t{uuid.uuid4().hex[:12]}@example.test"
    write_config(data_dir, name, dovecot.port)
    return name


def open_session(user):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap import mailbox
    return mailbox.open_session(accounts.get(user), timeout=20)


@pytest.fixture
def session(user):
    s = open_session(user)
    yield s
    try:
        s.logout()
    except Exception:
        pass


@contextmanager
def own(dovecot, user):
    """The test's own connection, to set things up and see what the session left behind."""
    M = imaplib.IMAP4_SSL("127.0.0.1", dovecot.port, ssl_context=_unverified(), timeout=20)
    M.login(user, PASSWORD)
    try:
        yield M
    finally:
        M.logout()


def message(n, *, mid=None):
    return (f"From: Sender {n} <s{n}@example.test>\r\nTo: you@example.test\r\nSubject: Message {n}\r\n"
            f"Date: Mon, 02 Nov 2026 12:{n:02d}:00 +0000\r\nMessage-ID: {mid or f'<m{n}@example.test>'}\r\n"
            f"\r\nBody {n}.\r\n").encode()


def seed(dovecot, user, folder, *raws, flags=(), when=None):
    """Append each message to `folder` through the test's own connection. Returns their UIDs."""
    from mailmind.mailbox.adapters.imap_names import mailbox_arg
    stamp = imaplib.Time2Internaldate(when or datetime.now(timezone.utc))
    uids = []
    with own(dovecot, user) as M:
        for raw in raws:
            typ, data = M.append(mailbox_arg(folder), "(" + " ".join(flags) + ")", stamp, raw)
            assert typ == "OK", data
            uids.append(int(re.search(rb"APPENDUID \d+ (\d+)", data[0]).group(1)))
    return uids


def on_server(dovecot, user, folder):
    """{uid: (subject, flags)} for what `folder` holds now."""
    from mailmind.mailbox.adapters.imap_names import mailbox_arg
    with own(dovecot, user) as M:
        assert M.select(mailbox_arg(folder), readonly=True)[0] == "OK"
        found = M.uid("SEARCH", None, "ALL")[1][0].split()
        if not found:
            return {}
        _, data = M.uid("FETCH", b",".join(found).decode(), "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    out = {}
    for item in data:
        if isinstance(item, tuple):
            uid = int(re.search(rb"UID (\d+)", item[0]).group(1))
            flags = set(re.search(rb"FLAGS \(([^)]*)\)", item[0]).group(1).decode().split()) - {"\\Recent"}
            out[uid] = (email.message_from_bytes(item[1])["Subject"], flags)
    return out


def subjects(dovecot, user, folder):
    return [subject for _, (subject, _) in sorted(on_server(dovecot, user, folder).items())]


def idling(session, seconds):
    def run():
        try:
            t.got = session.idle(seconds)
        except Exception as e:
            t.got = e
    t = threading.Thread(target=run, daemon=True)
    t.got = None
    t.start()
    return t


# ── reading ─────────────────────────────────────────────────────────────────────────────────────

def test_capabilities_are_what_dovecot_says_after_login(session):
    # Dovecot's greeting names neither MOVE nor UIDPLUS; its answer to LOGIN does.
    assert {"IMAP4REV1", "MOVE", "UIDPLUS", "IDLE", "SPECIAL-USE"} <= session.capabilities


def test_each_role_is_the_folder_dovecot_marks_for_it(session):
    from mailmind.mailbox.application.session import find_role
    for role, name in (("inbox", "INBOX"), ("sent", "Sent"), ("drafts", "Drafts"), ("archive", "Archive"),
                       ("junk", "Junk"), ("trash", "Trash")):
        assert find_role(session, role) == name


def test_odd_folder_names_come_back_as_list_gave_them_and_work_as_given(dovecot, user, session):
    from mailmind.mailbox.adapters.imap_names import mailbox_arg
    listed = ("Caf&AOk-", 'Say \\"hi\\"', "Back\\\\slash")
    with own(dovecot, user) as M:
        for name in listed:
            assert M.create(mailbox_arg(name))[0] == "OK"
    assert set(listed) <= {f.name for f in session.folders()}
    for name in listed:
        assert session.status(name) == (0, 0)
        session.append(name, message(1), flags=["\\Seen"])
        assert session.select(name).exists == 1
    assert [flags for _, flags in on_server(dovecot, user, 'Say \\"hi\\"').values()] == [{"\\Seen"}]


def test_status_counts_unseen_and_all(dovecot, user, session):
    seed(dovecot, user, "INBOX", message(1), message(2))
    seed(dovecot, user, "INBOX", message(3), flags=("\\Seen",))
    assert session.status("INBOX") == (2, 3)


def test_select_says_how_many_and_the_uid_validity(dovecot, user, session):
    uids = seed(dovecot, user, "INBOX", message(1), message(2))
    with own(dovecot, user) as M:
        M.select("INBOX", readonly=True)
        validity = int(M.untagged_responses["UIDVALIDITY"][0])
    got = session.select("INBOX")
    assert (got.exists, got.uidvalidity, got.uidnext) == (2, validity, uids[-1] + 1)


def test_a_folder_selected_read_only_keeps_its_flags(dovecot, user, session):
    # Dovecot answers a STORE after EXAMINE with OK and changes nothing; the fake says NO.
    [uid] = seed(dovecot, user, "INBOX", message(1))
    session.select("INBOX")
    try:
        session.add_flags([uid], ["\\Flagged"])
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        pass
    assert on_server(dovecot, user, "INBOX")[uid][1] == set()


def test_uids_changed_since_names_only_what_changed(dovecot, user, session):
    from mailmind.mailbox.adapters.imap_names import mailbox_arg
    _, second = seed(dovecot, user, "INBOX", message(1), message(2))
    assert session.enable_condstore() is True
    mark = session.select("INBOX").highest_modseq
    assert mark
    assert session.uids_changed_since(mark) == []
    with own(dovecot, user) as M:
        M.select(mailbox_arg("INBOX"))
        M.uid("STORE", str(second), "+FLAGS", "(\\Flagged)")
    assert session.select("INBOX").highest_modseq > mark
    assert session.uids_changed_since(mark) == [second]


def test_a_folder_says_no_modseq_until_condstore_is_asked_for_and_that_comes_first(dovecot, user, session):
    seed(dovecot, user, "INBOX", message(1))
    assert session.select("INBOX").highest_modseq is None
    assert session.enable_condstore() is False
    assert session.select("INBOX").highest_modseq is None


def test_uids_search_in_order_and_a_message_is_found_by_its_message_id(dovecot, user, session):
    odd = '<quo"te\\d@example.test>'
    uids = seed(dovecot, user, "INBOX", message(1), message(2, mid=odd), message(3))
    session.select("INBOX")
    assert session.uids("ALL") == uids
    assert session.uids("UNSEEN") == uids
    assert session.by_message_id(odd) == uids[1]
    assert session.by_message_id("<m3@example.test>") == uids[2]
    assert session.by_message_id("<nowhere@example.test>") is None


def test_a_message_id_holding_a_line_break_is_refused(dovecot, user, session):
    seed(dovecot, user, "INBOX", message(1))
    session.select("INBOX")
    with pytest.raises(imaplib.IMAP4.error):
        session.by_message_id("<m1@example.test>\r\n A1 DELETE INBOX")
    session.noop()
    assert subjects(dovecot, user, "INBOX") == ["Message 1"]


def test_a_body_is_the_message_as_stored_and_reading_it_leaves_it_unread(dovecot, user, session):
    [uid] = seed(dovecot, user, "INBOX", message(1))
    session.select("INBOX")
    assert session.body(uid) == message(1)
    assert "\\Seen" not in session.flags([uid])[uid]
    assert on_server(dovecot, user, "INBOX")[uid][1] == set()


def test_headers_come_in_uid_order_with_their_flags_and_only_the_fields_asked(dovecot, user, session,
                                                                               monkeypatch):
    from mailmind.mailbox.application import session as port
    monkeypatch.setattr(port, "HEADER_BATCH", 2)
    arrived = datetime(2026, 11, 2, 9, 30, tzinfo=timezone(timedelta(hours=2)))
    uids = seed(dovecot, user, "INBOX", message(1), message(2), message(3), flags=("\\Seen",), when=arrived)
    session.select("INBOX")
    got = session.headers(uids, ("SUBJECT", "MESSAGE-ID"), internaldate=True)
    assert [h.uid for h in got] == uids
    assert all("\\Seen" in h.flags for h in got)
    second = email.message_from_bytes(got[1].raw)
    assert (second["Subject"], second["Message-ID"], second["From"]) == ("Message 2", "<m2@example.test>", None)
    assert {h.internaldate for h in got} == {arrived}
    assert session.headers(uids[:1], ("SUBJECT",))[0].internaldate is None
    assert session.headers([], ("SUBJECT",)) == []


# ── writing ─────────────────────────────────────────────────────────────────────────────────────

def test_flags_are_added_and_removed(dovecot, user, session):
    [uid] = seed(dovecot, user, "INBOX", message(1))
    session.select("INBOX", readonly=False)
    session.add_flags([uid], ["\\Flagged", "\\Seen"])
    session.remove_flags([uid], ["\\Seen"])
    assert session.flags([uid])[uid] >= {"\\Flagged"} and "\\Seen" not in session.flags([uid])[uid]
    assert on_server(dovecot, user, "INBOX")[uid][1] == {"\\Flagged"}


def test_several_move_in_one_command_and_the_rest_stay(dovecot, user, session):
    uids = seed(dovecot, user, "INBOX", message(1), message(2), message(3))
    session.select("INBOX", readonly=False)
    session.move(uids[:2], "Archive")
    assert subjects(dovecot, user, "INBOX") == ["Message 3"]
    assert subjects(dovecot, user, "Archive") == ["Message 1", "Message 2"]


def test_copy_leaves_the_originals(dovecot, user, session):
    uids = seed(dovecot, user, "INBOX", message(1), message(2))
    session.select("INBOX", readonly=False)
    session.copy(uids, "Archive")
    assert subjects(dovecot, user, "INBOX") == subjects(dovecot, user, "Archive") == ["Message 1", "Message 2"]


def test_move_by_copy_removes_those_uids_and_no_other(dovecot, user, session):
    from mailmind.mailbox.application.session import move_by_copy
    uids = seed(dovecot, user, "INBOX", message(1), message(2))
    seed(dovecot, user, "INBOX", message(3), flags=("\\Deleted",))     # another client's, not ours
    session.select("INBOX", readonly=False)
    move_by_copy(session, uids, "Archive")
    assert subjects(dovecot, user, "INBOX") == ["Message 3"]
    assert subjects(dovecot, user, "Archive") == ["Message 1", "Message 2"]


def test_expunge_removes_only_the_uids_named(dovecot, user, session):
    uids = seed(dovecot, user, "INBOX", message(1), message(2), message(3), flags=("\\Deleted",))
    session.select("INBOX", readonly=False)
    session.expunge([uids[1]])
    assert subjects(dovecot, user, "INBOX") == ["Message 1", "Message 3"]


def test_append_lands_with_its_flags_and_date_and_says_its_uid(dovecot, user, session):
    arrived = datetime(2026, 11, 1, 8, 15, tzinfo=timezone(timedelta(hours=-5)))
    uid = session.append("Drafts", message(1), flags=["\\Draft", "\\Seen"], when=arrived)
    assert on_server(dovecot, user, "Drafts") == {uid: ("Message 1", {"\\Draft", "\\Seen"})}
    session.select("Drafts")
    assert session.headers([uid], ("SUBJECT",), internaldate=True)[0].internaldate == arrived


def test_a_refused_command_raises_imaplibs_error_and_the_session_goes_on(dovecot, user, session):
    [uid] = seed(dovecot, user, "INBOX", message(1))
    session.select("INBOX", readonly=False)
    for refused in (lambda: session.copy([uid], "Nowhere"), lambda: session.move([uid], "Nowhere"),
                    lambda: session.append("Nowhere", message(2)), lambda: session.status("Nowhere"),
                    lambda: session.select("Nowhere")):
        with pytest.raises(imaplib.IMAP4.error) as e:
            refused()
        assert not isinstance(e.value, imaplib.IMAP4.abort)
    assert session.noop()
    assert subjects(dovecot, user, "INBOX") == ["Message 1"]


# ── waiting for mail ────────────────────────────────────────────────────────────────────────────

def test_idle_says_when_new_mail_arrives_and_leaves_idle(dovecot, user, session):
    session.select("INBOX", readonly=False)
    t = idling(session, 30)
    seed(dovecot, user, "INBOX", message(1))
    t.join(20)
    assert t.got is True
    assert session.noop()


def test_idle_with_nothing_new_ends_when_its_time_is_up(session):
    session.select("INBOX", readonly=False)
    started = time.monotonic()
    assert session.idle(2) is False
    assert 1.5 < time.monotonic() - started < 15
    assert session.noop()


def test_an_interrupt_ends_an_idle_at_once(session):
    session.select("INBOX", readonly=False)
    t = idling(session, 60)
    time.sleep(0.5)
    started = time.monotonic()
    session.interrupt()
    t.join(5)
    assert not t.is_alive() and time.monotonic() - started < 5


# ── logging in ──────────────────────────────────────────────────────────────────────────────────

def test_a_refused_login_is_unreachable(dovecot, data_dir, kind):
    from mailmind.bootstrap import accounts
    write_config(data_dir, "wrong@example.test", dovecot.port, password="not-the-password")
    with pytest.raises(accounts.LoginUnreachable):
        open_session("wrong@example.test")


def test_the_first_session_pins_dovecots_certificate(dovecot, user):
    from mailmind.bootstrap.accounts import config
    open_session(user).logout()
    [entry] = config._raw()["accounts"]
    assert entry["tls_pins"] == {f"127.0.0.1:{dovecot.port}": "sha256:" + dovecot.cert_sha256}


def test_a_read_only_process_writes_nothing(dovecot, user, monkeypatch):
    from mailmind.platform import profile
    [uid] = seed(dovecot, user, "INBOX", message(1))
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    s = open_session(user)
    try:
        s.select("INBOX", readonly=False)
        assert s.body(uid) == message(1)
        for write in (lambda: s.add_flags([uid], ["\\Seen"]), lambda: s.move([uid], "Archive"),
                      lambda: s.copy([uid], "Archive"), lambda: s.expunge([uid]),
                      lambda: s.append("Drafts", message(2))):
            with pytest.raises(profile.ReadOnlyError):
                write()
    finally:
        s.logout()
    assert on_server(dovecot, user, "INBOX") == {uid: ("Message 1", set())}
    assert subjects(dovecot, user, "Archive") == subjects(dovecot, user, "Drafts") == []
