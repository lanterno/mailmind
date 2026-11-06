"""The fake IMAP server answers the way the app's imaplib code expects, and its scripted trouble
reaches the app the way a real server's would.

Most tests drive it through MailMind's own accounts.connect, mail commands and ingest, so a fake that
drifted from what the app relies on fails here first.
"""
import imaplib
import json
import threading
import time

import pytest

from fakes.imap_server import FakeIMAPServer

ADA = "ada@example.test"


def _mail(n, *, frm="Grace <grace@example.test>", subject=None, mid=None, extra=""):
    return (f"From: {frm}\r\nTo: Ada <{ADA}>\r\nSubject: {subject or f'Note {n}'}\r\n"
            f"Date: Mon, 02 Nov 2026 09:{n:02d}:00 +0000\r\n"
            f"Message-ID: {mid or f'<note-{n}@example.test>'}\r\n{extra}"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\nBody of note {n}.\r\n").encode()


@pytest.fixture
def srv():
    with FakeIMAPServer() as s:
        yield s


@pytest.fixture
def app(srv, data_dir):
    """The fake's account written into this test's MailMind config. Returns the account dict."""
    from mailmind.bootstrap import accounts
    entry = srv.add_account(ADA)
    (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [entry]}))
    return accounts.get(ADA)


def _connect(app):
    from mailmind.bootstrap import accounts
    return accounts.connect(app, timeout=5)


def _session(M):
    from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
    return ImaplibSession(M)


# ── connecting ──────────────────────────────────────────────────────────────────────────────────

def test_the_app_connects_over_starttls_and_logs_in(srv, app):
    M = _connect(app)
    assert M.state == "AUTH"
    M.logout()
    login = srv.commands_for(ADA, "LOGIN")
    assert login and "<password>" in login[0]["line"] and "app-password" not in login[0]["line"]
    names = [c["command"] for c in srv.commands]
    assert names.index("STARTTLS") < names.index("LOGIN")


def test_a_login_before_starttls_is_refused(srv):
    srv.add_account(ADA)
    M = imaplib.IMAP4("127.0.0.1", srv.port, timeout=5)
    with pytest.raises(imaplib.IMAP4.error, match="PRIVACYREQUIRED"):
        M.login(ADA, "app-password")


def test_a_wrong_password_is_a_login_failure(srv, app):
    from mailmind.bootstrap import accounts
    with pytest.raises(accounts.LoginUnreachable):
        accounts.connect({**app, "credential": {"plain": "nope"}}, timeout=5)


def test_list_shows_each_folder_with_its_special_use(srv, app):
    from mailmind.mailbox.application.session import find_role
    M = _connect(app)
    typ, data = M.list()
    assert typ == "OK" and any(b"\\Trash" in d and b'"Trash"' in d for d in data)
    assert find_role(_session(M), "junk") == "Junk"
    assert find_role(_session(M), "archive") == "Archive"
    M.logout()


# ── reading ─────────────────────────────────────────────────────────────────────────────────────

def test_examine_reports_the_count_and_uidvalidity(srv, app):
    for n in range(3):
        srv.deliver(ADA, "INBOX", _mail(n))
    M = _connect(app)
    typ, d = M.select('"INBOX"', readonly=True)
    assert (typ, d) == ("OK", [b"3"])
    assert int(M.untagged_responses["UIDVALIDITY"][0]) == srv.mailbox(ADA, "INBOX").uidvalidity
    M.logout()


def test_uid_search_all_by_range_and_by_message_id(srv, app):
    from mailmind.bootstrap import accounts
    uids = [srv.deliver(ADA, "INBOX", _mail(n)) for n in range(4)]
    M = _connect(app)
    M.select('"INBOX"', readonly=True)
    assert M.uid("SEARCH", None, "ALL")[1] == [" ".join(map(str, uids)).encode()]
    assert M.uid("SEARCH", None, "UID", f"{uids[1] + 1}:*")[1] == [f"{uids[2]} {uids[3]}".encode()]
    # As on real servers, n:* past the highest UID still matches the highest one.
    assert M.uid("SEARCH", None, "UID", f"{uids[3] + 5}:*")[1] == [str(uids[3]).encode()]
    hit = M.uid("SEARCH", None, "HEADER", "Message-ID", accounts.imap_quote("<note-2@example.test>"))
    assert hit[1] == [str(uids[2]).encode()]
    M.logout()


def test_fetching_header_fields_returns_only_those_fields_folded_lines_kept(srv, app):
    from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
    folded = "References: <a@example.test>\r\n <b@example.test>\r\nX-Other: hidden\r\n"
    uid = srv.deliver(ADA, "INBOX", _mail(1, extra=folded), flags=["\\Seen"])
    M = _connect(app)
    M.select('"INBOX"', readonly=True)
    [got] = ImaplibSession(M).headers([uid], ["SUBJECT", "REFERENCES"])
    assert (got.uid, got.flags) == (uid, {"\\Seen"})
    assert got.raw == (b"Subject: Note 1\r\nReferences: <a@example.test>\r\n <b@example.test>\r\n\r\n")
    M.logout()


def test_a_peek_leaves_mail_unread_and_a_plain_body_fetch_marks_it_read(srv, app):
    uid = srv.deliver(ADA, "INBOX", _mail(1))
    M = _connect(app)
    M.select('"INBOX"')
    M.uid("FETCH", str(uid), "(BODY.PEEK[])")
    assert "\\Seen" not in srv.messages(ADA, "INBOX")[uid].flags
    data = M.uid("FETCH", str(uid), "(BODY[])")[1]
    assert data[0][1].startswith(b"From: Grace")
    assert "\\Seen" in srv.messages(ADA, "INBOX")[uid].flags
    M.logout()


# ── writing ─────────────────────────────────────────────────────────────────────────────────────

def test_move_lands_the_message_in_the_destination(srv, app):
    uid = srv.deliver(ADA, "INBOX", _mail(1))
    M = _connect(app)
    M.select('"INBOX"')
    _session(M).move([uid], "Archive")
    M.logout()
    assert not srv.messages(ADA, "INBOX")
    assert [m.headers()["Subject"] for m in srv.messages(ADA, "Archive").values()] == ["Note 1"]


def test_without_move_the_app_copies_then_expunges_only_that_uid(app):
    with FakeIMAPServer(without=("MOVE",)) as srv:
        entry = srv.add_account(ADA)
        app = {**app, "port": entry["port"]}
        keep = srv.deliver(ADA, "INBOX", _mail(1), flags=["\\Deleted"])   # another client's pending delete
        uid = srv.deliver(ADA, "INBOX", _mail(2))
        M = _connect(app)
        M.select('"INBOX"')
        _session(M).move([uid], "Archive")
        M.logout()
        assert list(srv.messages(ADA, "INBOX")) == [keep]
        assert len(srv.messages(ADA, "Archive")) == 1
        sent = [c["command"] for c in srv.commands_for(ADA)]
        assert "UID COPY" in sent and "UID EXPUNGE" in sent and "EXPUNGE" not in sent


def test_without_uidplus_the_message_is_left_flagged_deleted(app):
    with FakeIMAPServer(without=("MOVE", "UIDPLUS")) as srv:
        entry = srv.add_account(ADA)
        app = {**app, "port": entry["port"]}
        uid = srv.deliver(ADA, "INBOX", _mail(1))
        M = _connect(app)
        M.select('"INBOX"')
        _session(M).move([uid], "Archive")
        M.logout()
        assert srv.messages(ADA, "INBOX")[uid].flags == {"\\Deleted"}
        assert len(srv.messages(ADA, "Archive")) == 1


def test_append_reports_the_new_uid(srv, app):
    from mailmind.bootstrap import mailbox
    folder, uid, uidv, _ = mailbox.copies.append_to_drafts(app, _mail(9, subject="Draft"))
    assert folder == "Drafts" and uidv == srv.mailbox(ADA, "Drafts").uidvalidity
    assert srv.messages(ADA, "Drafts")[uid].flags == {"\\Draft"}


def test_a_store_on_a_read_only_mailbox_is_refused(srv, app):
    uid = srv.deliver(ADA, "INBOX", _mail(1))
    M = _connect(app)
    M.select('"INBOX"', readonly=True)
    typ, _ = M.uid("STORE", str(uid), "+FLAGS", "(\\Flagged)")
    assert typ == "NO"
    M.logout()


def test_a_folder_name_with_spaces_and_quotes_round_trips(srv, data_dir):
    from mailmind.bootstrap import accounts
    srv.add_account(ADA, folders=['Clients "VIP"', "Old Projects"])
    entry = srv.config_entry(ADA)
    (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [entry]}))
    srv.deliver(ADA, 'Clients "VIP"', _mail(1))
    M = accounts.connect(accounts.get(ADA), timeout=5)
    typ, d = M.select(accounts.mailbox_arg('Clients \\"VIP\\"'), readonly=True)
    assert (typ, d) == ("OK", [b"1"])
    M.logout()


# ── scripted trouble ────────────────────────────────────────────────────────────────────────────

def test_a_scripted_no_reaches_the_app_as_a_refusal(srv, app):
    from mailmind.bootstrap import mailbox
    from mailmind.bootstrap.mailbox import store
    uid = srv.deliver(ADA, "INBOX", _mail(1))
    c = store.conn()
    pk = store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": "INBOX", "uid": uid,
                                  "message_id": "<note-1@example.test>", "gmail_thread": None,
                                  "from_addr": "grace@example.test", "from_name": "Grace",
                                  "subject": "Note 1", "date_iso": None, "ts": 0, "snippet": "",
                                  "bucket": "PERSONAL", "confidence": 1.0, "reason": "", "is_junk": 0})
    c.commit()
    c.close()
    srv.fail("UID MOVE", "NO [CANNOT] not today")
    srv.fail("UID COPY", "NO [OVERQUOTA] full")
    answer = mailbox.filing.do_trash({"pk": pk})
    assert (answer["ok"], answer["undoable"]) == (False, False)
    assert list(srv.messages(ADA, "INBOX")) == [uid]


def test_a_dropped_connection_is_an_abort_the_app_can_queue(srv, app):
    from mailmind.bootstrap.mailbox import commands
    srv.deliver(ADA, "INBOX", _mail(1))
    srv.drop("UID MOVE")
    M = _connect(app)
    M.select('"INBOX"')
    with pytest.raises(commands.UNREACHABLE):
        M.uid("MOVE", "1", '"Archive"')


def test_drop_after_runs_the_command_but_the_client_never_hears(srv, app):
    uid = srv.deliver(ADA, "INBOX", _mail(1))
    srv.drop_after("UID MOVE")
    M = _connect(app)
    M.select('"INBOX"')
    with pytest.raises(imaplib.IMAP4.abort):
        M.uid("MOVE", str(uid), '"Archive"')
    assert not srv.messages(ADA, "INBOX") and len(srv.messages(ADA, "Archive")) == 1


def test_a_fault_can_hit_only_matching_arguments_and_only_so_many_times(srv, app):
    srv.fail("SELECT", "NO [UNAVAILABLE] Bridge is busy", when=lambda args: str(args[0]) == "Trash", times=2)
    M = _connect(app)
    assert M.select('"INBOX"')[0] == "OK"
    assert M.select('"Trash"')[0] == "NO"
    assert M.select('"Trash"')[0] == "NO"
    assert M.select('"Trash"')[0] == "OK"
    M.logout()


def test_renumber_changes_uidvalidity_and_restarts_uids(srv, app):
    for n in range(3):
        srv.deliver(ADA, "INBOX", _mail(n))
    box = srv.mailbox(ADA, "INBOX")
    box.messages.pop(2)                                   # a gap, so renumbering is visible
    before = box.uidvalidity
    srv.renumber(ADA, "INBOX")
    assert box.uidvalidity != before and list(box.messages) == [1, 2]
    assert [m.headers()["Subject"] for m in box.messages.values()] == ["Note 0", "Note 2"]


def test_idle_hears_new_mail(srv, app):
    M = _connect(app)
    M.select('"INBOX"')
    heard = []

    def later():
        time.sleep(0.2)
        srv.deliver(ADA, "INBOX", _mail(1))

    threading.Thread(target=later, daemon=True).start()
    with M.idle(duration=2) as idler:
        for typ, data in idler:
            heard.append((typ, data))
            if typ == "EXISTS":
                break
    assert ("EXISTS", [b"1"]) in heard
    M.logout()


# ── provider habits ─────────────────────────────────────────────────────────────────────────────

def test_proton_marks_mail_read_on_its_way_to_trash(data_dir):
    from mailmind.bootstrap import accounts
    with FakeIMAPServer(kind="proton") as srv:
        entry = srv.add_account(ADA)
        (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [entry]}))
        uid = srv.deliver(ADA, "INBOX", _mail(1))
        M = accounts.connect(accounts.get(ADA), timeout=5)
        M.select('"INBOX"')
        _session(M).move([uid], "Trash")
        M.logout()
        assert [m.flags for m in srv.messages(ADA, "Trash").values()] == [{"\\Seen"}]


def test_gmail_threads_archive_by_label_and_get_their_inbox_uid_back(data_dir):
    from mailmind.bootstrap import accounts
    from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
    with FakeIMAPServer(kind="gmail") as srv:
        entry = srv.add_account(ADA)
        (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [entry]}))
        first = _mail(1)
        a = srv.deliver(ADA, "INBOX", first)
        b = srv.deliver(ADA, "INBOX", _mail(2, extra="In-Reply-To: <note-1@example.test>\r\n"), thread_of=first)
        acct = accounts.get(ADA)
        M = accounts.connect(acct, timeout=5)
        M.select('"INBOX"')
        thrids = {h.gmail_thread for h in ImaplibSession(M).headers([a, b], ["SUBJECT"], gmail_thread=True)}
        assert len(thrids) == 1 and None not in thrids
        assert M.uid("STORE", str(a), "-X-GM-LABELS", "(\\Inbox)")[0] == "OK"
        M.logout()
        assert list(srv.messages(ADA, "INBOX")) == [b]
        assert len(srv.messages(ADA, "[Gmail]/All Mail")) == 2
        from mailmind.bootstrap.mailbox import server_ops
        assert server_ops.relabel_by_msgid(ADA, "[Gmail]/All Mail", "<note-1@example.test>") == (True, "relabeled")
        assert list(srv.messages(ADA, "INBOX")) == [a, b]


def test_gmail_trash_and_spam_are_outside_all_mail(data_dir):
    from mailmind.bootstrap import accounts
    with FakeIMAPServer(kind="gmail") as srv:
        entry = srv.add_account(ADA)
        (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [entry]}))
        a = srv.deliver(ADA, "INBOX", _mail(1))
        srv.deliver(ADA, "INBOX", _mail(2))
        all_uid = srv.find(ADA, "[Gmail]/All Mail", "<note-1@example.test>")
        M = accounts.connect(accounts.get(ADA), timeout=5)
        M.select('"INBOX"')
        assert M.uid("MOVE", str(a), '"[Gmail]/Trash"')[0] == "OK"
        assert srv.find(ADA, "[Gmail]/All Mail", "<note-1@example.test>") == []
        assert len(srv.messages(ADA, "[Gmail]/Trash")) == 1
        M.select('"[Gmail]/Trash"')
        t = list(srv.messages(ADA, "[Gmail]/Trash"))[0]
        assert M.uid("MOVE", str(t), '"INBOX"')[0] == "OK"          # back out: same UIDs as before
        assert srv.find(ADA, "[Gmail]/All Mail", "<note-1@example.test>") == all_uid
        assert srv.find(ADA, "INBOX", "<note-1@example.test>") == [a]
        M.select('"INBOX"')
        assert M.uid("MOVE", str(a), '"[Gmail]/Spam"')[0] == "OK"
        assert srv.find(ADA, "[Gmail]/All Mail", "<note-1@example.test>") == []
        M.logout()
        srv.deliver(ADA, "[Gmail]/Spam", _mail(3))                   # delivered straight to Spam
        assert srv.find(ADA, "[Gmail]/All Mail", "<note-3@example.test>") == []


# ── ingest end to end ───────────────────────────────────────────────────────────────────────────

def test_ingest_syncs_a_folder_then_only_what_is_new(srv, app):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    srv.without.add("CONDSTORE")
    for n in range(3):
        srv.deliver(ADA, "INBOX", _mail(n), flags=["\\Seen"] if n == 0 else [])
    assert ingest.sync_folder(app, "INBOX", full=True)[0] == 3
    new = srv.deliver(ADA, "INBOX", _mail(3))
    before = len(srv.commands_for(ADA, "UID FETCH"))
    ingest.sync_folder(app, "INBOX")
    fetched = srv.commands_for(ADA, "UID FETCH")[before:]
    content = [c for c in fetched if c["args"][1] != ["FLAGS"]]
    assert content and {str(c["args"][0]) for c in content} == {str(new)}   # headers, then the body
    assert [c["args"] for c in fetched if c not in content] == [[f"{new},3,2,1", ["FLAGS"]]]   # and every flag again
    c = store.conn()
    rows = {r["subject"]: r for r in c.execute("SELECT subject, seen FROM messages ORDER BY uid")}
    c.close()
    assert list(rows) == ["Note 0", "Note 1", "Note 2", "Note 3"]
    assert [rows[s]["seen"] for s in rows] == [1, 0, 0, 0]


def test_ingest_resyncs_a_folder_whose_uidvalidity_changed(srv, app):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    for n in range(2):
        srv.deliver(ADA, "INBOX", _mail(n))
    ingest.sync_folder(app, "INBOX", full=True)
    srv.renumber(ADA, "INBOX")
    srv.deliver(ADA, "INBOX", _mail(5))
    ingest.sync_folder(app, "INBOX")
    c = store.conn()
    got = sorted((r["uid"], r["subject"]) for r in c.execute("SELECT uid, subject FROM messages"))
    c.close()
    assert got == [(1, "Note 0"), (2, "Note 1"), (3, "Note 5")]


def test_two_servers_given_the_same_mail_agree_on_every_id():
    def build(kind):
        with FakeIMAPServer(kind=kind) as s:
            s.add_account(ADA)
            first = _mail(1)
            s.deliver(ADA, "INBOX", first)
            s.deliver(ADA, "INBOX", _mail(2), thread_of=first)
            s.renumber(ADA, "INBOX")
            return [(b.name, b.uidvalidity, [(u, m.gm_msgid, m.gm_thrid) for u, m in b.messages.items()])
                    for b in s.accounts[ADA].mailboxes.values()]
    for kind in ("imap", "gmail"):
        assert build(kind) == build(kind)
