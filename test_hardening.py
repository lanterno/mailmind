"""Phase 0 hardening: live security and data-loss fixes, each pinned by a test. Hermetic: a temp
database and config, fake IMAP and SMTP connections, and fictional addresses, so nothing here
can reach a real server.
Run: python3 test_hardening.py"""
import contextlib
import dataclasses
import http.client
import imaplib
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse

import store
from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
from mailmind.platform import settings
from mailmind.platform.workers import gates
settings.configure(settings.Settings(home=tempfile.mkdtemp()))   # before anything opens a file
with open(settings.current().config, "w") as f:
    json.dump({"accounts": [{"email": "ada@example.test", "type": "imap",
                             "host": "imap.example.test"}]}, f)
import accounts
import ingest
import mailops
import readmutq
import server
import smtpsend
from mailmind.bootstrap import curation
store.init()


class _Peer:
    """A TLS socket's view of the server's certificate, as check_pin reads it on loopback."""

    def getpeercert(self, binary_form=False):
        return b"the server's own certificate"


class _FakeIMAP:
    """Stands in for imaplib.IMAP4: records the TLS context STARTTLS was given."""
    made = []

    def __init__(self, host, port, timeout=None):
        self.host, self.ctx, self.sock = host, None, _Peer()
        _FakeIMAP.made.append(self)

    def starttls(self, ctx):
        self.ctx = ctx

    def login(self, user, pw):
        return "OK", [b"logged in"]

    def capability(self):
        return "OK", [b"IMAP4rev1 AUTH=PLAIN"]


def _starttls_ctx(host):
    real_imap, real_cred = accounts.imaplib.IMAP4, accounts.config.resolve_credential
    accounts.imaplib.IMAP4 = _FakeIMAP
    accounts.config.resolve_credential = lambda acct: "app-password"
    try:
        accounts.connect({"email": "ada@example.test", "host": host, "port": 143, "starttls": True})
    finally:
        accounts.imaplib.IMAP4, accounts.config.resolve_credential = real_imap, real_cred
    return _FakeIMAP.made[-1].ctx


def test_starttls_off_this_machine_verifies_the_certificate():
    for host in ("imap.example.test", "192.0.2.10", "127.0.0.1.example.test"):
        ctx = _starttls_ctx(host)
        assert ctx.verify_mode == ssl.CERT_REQUIRED, host
        assert ctx.check_hostname, host


def test_starttls_on_loopback_accepts_bridges_own_certificate():
    for host in ("127.0.0.1", "localhost", "::1", " LOCALHOST "):
        ctx = _starttls_ctx(host)
        assert ctx.verify_mode == ssl.CERT_NONE, host
        assert not ctx.check_hostname, host


class _FakeSMTP:
    """Stands in for smtplib.SMTP and SMTP_SSL: records the TLS context each was given."""
    ctxs = []

    def __init__(self, host, port, timeout=None, context=None):
        self.sock = _Peer()
        if context is not None:
            _FakeSMTP.ctxs.append(context)

    def ehlo(self):
        pass

    def starttls(self, context=None):
        _FakeSMTP.ctxs.append(context)

    def login(self, user, pw):
        pass


def _smtp_ctx(host, starttls):
    real = smtpsend.smtplib.SMTP, smtpsend.smtplib.SMTP_SSL, smtpsend.config.resolve_credential
    smtpsend.smtplib.SMTP = smtpsend.smtplib.SMTP_SSL = _FakeSMTP
    smtpsend.config.resolve_credential = lambda acct: "app-password"
    try:
        smtpsend.smtp_connect({"email": "ada@example.test", "smtp_host": host,
                               "smtp_port": 587 if starttls else 465, "smtp_starttls": starttls})
    finally:
        smtpsend.smtplib.SMTP, smtpsend.smtplib.SMTP_SSL, smtpsend.config.resolve_credential = real
    return _FakeSMTP.ctxs[-1]


def test_smtp_follows_the_same_loopback_rule():
    for starttls in (True, False):
        ctx = _smtp_ctx("smtp.example.test", starttls)
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname, starttls
        ctx = _smtp_ctx("127.0.0.1", starttls)
        assert ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname, starttls


class _ScriptedIMAP:
    """A selected-folder IMAP connection that answers each command as scripted and records it.
    An answer that is an exception is raised, the way imaplib raises on BAD."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []
        self.untagged_responses = {"UIDVALIDITY": [b"7"]}

    def select(self, folder, readonly=False):
        self.calls.append(("SELECT", folder))
        return "OK", [b"1"]

    def response(self, code):
        return code, self.untagged_responses.pop(code, [None])

    def uid(self, cmd, *args):
        self.calls.append((cmd.upper(),) + args)
        a = self.answers.get(cmd.upper(), ("OK", [b""]))
        if isinstance(a, Exception):
            raise a
        return a

    def expunge(self):
        self.calls.append(("EXPUNGE-ALL",))
        return "OK", []

    def append(self, folder, flags, date, raw):
        self.calls.append(("APPEND", folder))
        return "OK", [b"[APPENDUID 7 42] done"]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Drafts) "/" "Drafts"']

    def logout(self):
        pass

    def cmds(self):
        return [c[0] for c in self.calls]


_NO_MOVE = ("NO", [b"MOVE not supported"])
_BAD = imaplib.IMAP4.error("BAD unknown command")


def test_a_move_without_MOVE_copies_then_expunges_only_its_uid():
    M = _ScriptedIMAP(MOVE=_NO_MOVE)
    ImaplibSession(M).move([41], "Archive")
    assert ("COPY", "41", '"Archive"') in M.calls
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert ("EXPUNGE", "41") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_a_failed_copy_leaves_the_message_untouched():
    M = _ScriptedIMAP(MOVE=_NO_MOVE, COPY=("NO", [b"[TRYCREATE] no such folder"]))
    try:
        ImaplibSession(M).move([41], "Gone")
        raise AssertionError("a failed COPY must raise")
    except imaplib.IMAP4.error as e:
        assert "COPY refused" in str(e)
    assert "STORE" not in M.cmds() and "EXPUNGE" not in M.cmds() and "EXPUNGE-ALL" not in M.cmds()


def test_without_uidplus_the_message_stays_flagged_and_nothing_else_goes():
    M = _ScriptedIMAP(MOVE=_NO_MOVE, EXPUNGE=_BAD)
    ImaplibSession(M).move([41], "Archive")        # no exception: copied and flagged
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_a_lost_connection_during_expunge_still_raises():
    M = _ScriptedIMAP(EXPUNGE=imaplib.IMAP4.abort("socket closed"))
    try:
        ImaplibSession(M).move([41], "Archive")
        raise AssertionError("an abort must propagate so the caller can queue the op")
    except imaplib.IMAP4.abort:
        pass


def _with_connection(M, fn):
    real = smtpsend.accounts.connect
    smtpsend.accounts.connect = lambda acct, timeout=60: M
    try:
        return fn()
    finally:
        smtpsend.accounts.connect = real


def test_deleting_a_draft_copy_never_bulk_expunges():
    M = _ScriptedIMAP(EXPUNGE=_BAD)
    _with_connection(M, lambda: smtpsend.delete_draft_uid({"email": "ada@example.test"},
                                                          "Drafts", 41, 7))
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_replacing_a_draft_copy_never_bulk_expunges():
    M = _ScriptedIMAP(EXPUNGE=_BAD)
    out = _with_connection(M, lambda: smtpsend.append_to_drafts(
        {"email": "ada@example.test"}, b"Subject: hi\r\n\r\nbody", prev_uid=41, prev_uidvalidity=7))
    assert out == ("Drafts", 42, 7, None)                        # the EXPUNGE was refused: it is still there
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()



@contextlib.contextmanager
def _live_server():
    """The real handler on an ephemeral 127.0.0.1 port; yields get(path, headers) -> (status, body)."""
    srv = server._Server(("127.0.0.1", 0), server.H)
    port = srv.server_address[1]
    before = settings.current()
    settings.configure(dataclasses.replace(before, port=port))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path, headers, with_headers=False):
        h = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h.request("GET", path, headers={k: v.replace("PORT", str(port)) for k, v in headers.items()})
        r = h.getresponse()
        out = r.status, r.read()
        h.close()
        return out + (r.headers,) if with_headers else out
    try:
        yield get
    finally:
        srv.shutdown()
        srv.server_close()
        settings.configure(before)


_READS = ["/", "/api/digest", "/api/contacts?q=a", "/api/search?q=a", "/api/accounts", "/api/rules",
          "/api/no-such-route"]


def test_the_app_still_loads_on_its_own_host():
    with _live_server() as get:
        for host in ("127.0.0.1:PORT", "localhost:PORT"):
            code, body = get("/", {"Host": host})
            assert code == 200 and server.CSRF.encode() in body, (host, code)
        code, _ = get("/api/digest", {**_PAGE, "Origin": "http://127.0.0.1:PORT"})
        assert code == 200, code


def test_a_rebound_name_reads_nothing():
    with _live_server() as get:
        for path in _READS:
            code, body = get(path, {"Host": "rebound.example.test:PORT"})
            assert code == 403, (path, code)
            assert server.CSRF.encode() not in body, path


def test_a_foreign_origin_reads_nothing():
    with _live_server() as get:
        for path in _READS:
            code, _ = get(path, {"Host": "127.0.0.1:PORT", "Origin": "http://rebound.example.test:PORT"})
            assert code == 403, (path, code)


def test_a_refused_read_is_not_counted_as_someone_at_the_screen():
    from mailmind.bootstrap import assistant
    governor = assistant.governor()
    with _live_server() as get:
        governor._last_seen = 0
        get("/api/digest", {"Host": "rebound.example.test:PORT"})
        assert governor._last_seen == 0, governor._last_seen


_EVIL = 'INBOX"' + "\r\nA1 DELETE Archive"


def test_folder_names_from_list_pass_through_unchanged():
    for name in ("INBOX", "[Gmail]/All Mail", "Folders/Receipts", 'Notes \\"old\\"', "a\\\\b", "Tab\there"):
        assert accounts.mailbox_arg(name) == f'"{name}"', name


def test_a_folder_name_that_would_break_the_command_is_refused():
    assert issubclass(accounts.BadMailbox, imaplib.IMAP4.error)    # handled like the server's BAD
    for name in (_EVIL, "a\nb", "a\rb", "a\x00b", 'x"y', "trail\\", "", None):
        try:
            accounts.mailbox_arg(name)
            raise AssertionError(f"accepted {name!r}")
        except accounts.BadMailbox:
            pass


def _sent_a_line_break(M):
    return [c for c in M.calls if any(isinstance(a, str) and ("\r" in a or "\n" in a) for a in c)]


def test_a_move_to_an_injected_folder_sends_nothing():
    M = _ScriptedIMAP()
    try:
        ImaplibSession(M).move([41], "Archive\r\nA1 DELETE INBOX")
        raise AssertionError("an injected destination must be refused")
    except accounts.BadMailbox:
        pass
    assert M.calls == [], M.calls


def test_an_injected_folder_in_the_url_reaches_no_imap_command():
    M = _ScriptedIMAP()
    real = accounts.connect
    accounts.connect = lambda acct, timeout=60: M
    try:
        with _live_server() as get:
            q = urllib.parse.urlencode({"account": "ada@example.test", "folder": _EVIL})
            code, _ = get("/api/messages?" + q, _PAGE)
    finally:
        accounts.connect = real
    assert code == 200, code
    assert not _sent_a_line_break(M), _sent_a_line_break(M)


_HOST = {"Host": "127.0.0.1:PORT"}
_PAGE = {**_HOST, "Sec-Fetch-Site": "same-origin"}     # what the page's own fetch sends


def test_a_read_another_site_starts_is_refused():
    with _live_server() as get:
        for site in ("cross-site", "same-site"):
            for path in ("/api/digest", "/api/stats?refresh=1", "/api/contacts?q=a", "/api/no-such-route"):
                code, _ = get(path, {**_HOST, "Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors",
                                     "Sec-Fetch-Dest": "image"})
                assert code == 403, (site, path, code)


def test_another_site_can_link_to_the_app_but_not_frame_it():
    with _live_server() as get:
        nav = {**_HOST, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        code, body = get("/", {**nav, "Sec-Fetch-Dest": "document"})
        assert code == 200 and server.CSRF.encode() in body, code
        code, _ = get("/", {**nav, "Sec-Fetch-Dest": "iframe"})
        assert code == 403, code
        code, _ = get("/api/digest", {**nav, "Sec-Fetch-Dest": "document"})
        assert code == 403, code


def test_the_api_answers_only_the_pages_own_reads():
    with _live_server() as get:
        code, _ = get("/api/digest", _PAGE)
        assert code == 200, code
        for site in ("none", None):      # a typed URL; a browser too old to say, or a script
            h = dict(_HOST, **({"Sec-Fetch-Site": site} if site else {}))
            code, _ = get("/api/digest", h)
            assert code == 403, (site, code)
            code, body = get("/", h)     # the page itself still opens
            assert code == 200 and server.CSRF.encode() in body, (site, code)


def test_no_page_may_frame_the_app():
    with _live_server() as get:
        for path in ("/", "/api/digest", "/api/no-such-route"):
            _, _, headers = get(path, _PAGE, with_headers=True)
            assert headers.get("X-Frame-Options") == "DENY", path


_uid = [500]


def _message(folder="INBOX", sender="bo@example.test", mid=None):
    """A fictional message row in the temp database; returns its pk."""
    _uid[0] += 1
    c = store.conn()
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, "
                    "subject) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("ada@example.test", "imap", folder, _uid[0], mid or f"<h{_uid[0]}@example.test>",
                     f"h{_uid[0]}", "2026-11-01T09:00:00+00:00", sender, "Lunch"))
    c.commit()
    pk = cur.lastrowid
    c.close()
    return pk


@contextlib.contextmanager
def _connection(M):
    real = accounts.connect
    accounts.connect = lambda acct, timeout=60: M
    try:
        yield M
    finally:
        accounts.connect = real


def test_a_request_can_only_star_or_mark_read():
    pk = _message()
    for flag in ("deleted", "draft", "answered", "Deleted", "bogus"):
        with _connection(_ScriptedIMAP()) as M:
            res = server.do_write("/api/flag", {"pk": pk, "flag": flag, "on": True})
        assert res["ok"] is False, (flag, res)
        assert M.calls == [], (flag, M.calls)
    for flag, imap in (("flagged", "(\\Flagged)"), ("seen", "(\\Seen)")):
        with _connection(_ScriptedIMAP()) as M:
            res = server.do_write("/api/flag", {"pk": pk, "flag": flag, "on": True})
        assert res["ok"] is True, (flag, res)
        assert ("STORE", str(_uid[0]), "+FLAGS", imap) in M.calls, (flag, M.calls)


_REFUSE = {"MOVE": ("NO", [b"[OVERQUOTA] no room"]), "COPY": ("NO", [b"[OVERQUOTA] no room"])}
_FOLDERS = ("OK", [b'(\\HasNoChildren \\Junk) "/" "Junk"', b'(\\HasNoChildren \\Archive) "/" "Archive"'])


def _still_there(pk):
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    return row is not None


def _refusing():
    M = _ScriptedIMAP(**_REFUSE)
    M.list = lambda: _FOLDERS
    return M


def test_a_refused_move_keeps_the_message_and_says_so():
    for name, op in (("trash", mailops.trash_message), ("archive", mailops.archive_message),
                     ("junk", mailops.junk_message), ("move", lambda pk: mailops.move_message(pk, "Archive"))):
        pk = _message()
        with _connection(_refusing()):
            ok, msg, undo = op(pk)
        assert ok is False and undo is None, (name, ok, msg)
        assert _still_there(pk), name


def test_a_refused_gmail_archive_keeps_the_message():
    pk = _message()
    real = accounts.get
    accounts.get = lambda email: {**real(email), "kind": "gmail"}
    try:
        with _connection(_ScriptedIMAP(STORE=("NO", [b"label not removed"]))):
            ok, _, undo = mailops.archive_message(pk)
    finally:
        accounts.get = real
    assert ok is False and undo is None, ok
    assert _still_there(pk)


def test_an_accepted_move_still_files_the_message():
    pk = _message()
    M = _ScriptedIMAP()
    M.list = lambda: _FOLDERS
    with _connection(M):
        ok, _, undo = mailops.archive_message(pk)
    assert ok is True and undo and undo["dest_folder"] == "Archive", (ok, undo)
    assert not _still_there(pk)


def test_the_route_reports_a_refusal_and_offers_no_undo():
    pk = _message()
    depth = len(server.undo_entries())
    with _connection(_refusing()):
        res = server.do_write("/api/move", {"pk": pk, "dest": "Archive"})
    assert res["ok"] is False and res["undoable"] is False, res
    assert len(server.undo_entries()) == depth
    assert _still_there(pk)


def _optimized(code):
    """Run code under python -O, which strips asserts, from this directory; return its stdout."""
    out = subprocess.run([sys.executable, "-O", "-c", code], cwd=os.path.dirname(os.path.abspath(__file__)),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_bcc_never_leaves_even_under_python_O():
    for name in ("Bcc", "BCC", "bcc "):
        got = _optimized(f"""
from mailmind.compose.domain import builder as compose
class Leaky(compose.EmailMessage):
    def as_bytes(self, *a, **k):
        return b"{name}: hidden@example.test\\r\\n" + super().as_bytes(*a, **k)
compose.EmailMessage = Leaky
try:
    compose.build_message({{"account": "ada@example.test", "to": ["bo@example.test"],
                            "bcc": ["hidden@example.test"], "subject": "Hi", "text": "Hello"}})
    print("SENT")
except ValueError:
    print("REFUSED")
""")
        assert got == "REFUSED", (name, got)


def test_a_malformed_config_is_never_written_even_under_python_O():
    got = _optimized("""
import os, tempfile, config
from mailmind.platform import settings
home = tempfile.mkdtemp()
settings.configure(settings.Settings(home=home))
open(settings.current().config, "w").write('{"accounts": []}')
try:
    config.save({"accounts": "wiped"})
    print("WRITTEN")
except ValueError:
    print("REFUSED", open(settings.current().config).read(), sorted(os.listdir(home)))
""")
    assert got == "REFUSED {\"accounts\": []} ['mailmind.config.json']", got


def test_a_failed_read_answers_without_the_exception_text():
    real = server.digest_json

    def boom(q):
        raise RuntimeError("NO [AUTHENTICATIONFAILED] ada@example.test secret=hunter2 /home/ada/mailmind.db")
    server.digest_json = boom
    try:
        with _live_server() as get:
            code, body = get("/api/digest", _PAGE)
    finally:
        server.digest_json = real
    assert code == 500, code
    assert json.loads(body) == {"ok": False, "error": "request failed"}, body


def _undo_target(uid, mid):
    """A server holding one message, found by its Message-ID at this UID."""
    head = (f"From: Bo <bo@example.test>\r\nSubject: Back again\r\nMessage-ID: {mid}\r\n"
            "Date: Mon, 2 Nov 2026 09:00:00 +0000\r\n\r\n").encode()
    return _ScriptedIMAP(SEARCH=("OK", [str(uid).encode()]),
                         FETCH=("OK", [(f"1 (UID {uid} FLAGS (\\Seen) BODY[] {{{len(head)}}}".encode(), head),
                                       b")"]))


def _watermark(folder):
    c = store.conn()
    wm = store.get_watermark(c, "ada@example.test", folder)
    c.close()
    return wm


def test_undo_ingest_leaves_the_inbox_watermark_alone():
    c = store.conn()
    store.set_watermark(c, "ada@example.test", 100, "2026-11-01T09:00:00+00:00", "INBOX")
    c.commit()
    c.close()
    with _connection(_undo_target(900, "<back@example.test>")):
        n = ingest.ingest_message(accounts.get("ada@example.test"), "INBOX", "<back@example.test>")
    assert n == 1, n
    c = store.conn()
    got = c.execute("SELECT uid FROM messages WHERE message_id=?", ("<back@example.test>",)).fetchall()
    c.close()
    assert [r["uid"] for r in got] == [900], got
    assert _watermark("INBOX") == 100, _watermark("INBOX")    # UIDs 101..899 still get synced


def test_undo_ingest_into_an_unopened_folder_keeps_its_first_full_sync():
    with _connection(_undo_target(31, "<filed@example.test>")):
        n = ingest.ingest_message(accounts.get("ada@example.test"), "Archive", "<filed@example.test>")
    assert n == 1, n
    assert not ingest.folder_synced("ada@example.test", "Archive")


@contextlib.contextmanager
def _trash_probe():
    """Replaces trash_message; yields the list of whether the IMAP gate was held at each call."""
    held, real = [], mailops.trash_message

    def probe(pk):
        held.append(gates.held())
        return True, "trashed", None
    mailops.trash_message = probe
    try:
        yield held
    finally:
        mailops.trash_message = real


def test_cleanup_and_block_trash_mail_under_the_imap_lock():
    for _ in range(2):
        _message(sender="promo@example.test")
    with _trash_probe() as held:
        res, code = curation.cleanup.execute({"from_addr": "promo@example.test"})
    assert code == 200 and res["swept"] == 2, (code, res)
    assert held == [True, True], held
    pk = _message(sender="spam@example.test")
    with _trash_probe() as held:
        res = curation.screener.rule({"token": "spam@example.test", "verdict": "block", "pk": pk})
    assert res["trashed"] is True, res
    assert held == [True], held
    assert not gates.held()


def test_nosync_reads_one_way_and_fails_toward_read_only():
    real = os.environ.get("MAILMIND_NOSYNC")
    try:
        for value, want in (("1", True), ("true", True), ("yes", True), (" On ", True), ("anything", True),
                            ("", False), ("0", False), ("false", False), ("No", False), ("off", False)):
            os.environ["MAILMIND_NOSYNC"] = value
            assert server.nosync() is want, (value, want)
        os.environ.pop("MAILMIND_NOSYNC")
        assert server.nosync() is False
    finally:
        if real is None:
            os.environ.pop("MAILMIND_NOSYNC", None)
        else:
            os.environ["MAILMIND_NOSYNC"] = real
    readers = []                                    # one place reads it, so every check agrees
    root = os.path.dirname(os.path.abspath(__file__))
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("tests", "node_modules", "__pycache__")]
        for name in files:
            if name.endswith(".py") and not name.startswith("test"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    if re.search(r"""(\.get\(|\[)\s*["']MAILMIND_NOSYNC["']""", f.read()):
                        readers.append(name)
    assert len(readers) == 1, f"read MAILMIND_NOSYNC in one place only, not {readers}"


def _offline():
    real = accounts.connect

    def unreachable(acct, timeout=60):
        raise ConnectionRefusedError("IMAP down")
    accounts.connect = unreachable
    return real


def _queued(mid):
    c = store.conn()
    rows = c.execute("SELECT id, state FROM read_mutations WHERE message_id=? ORDER BY id", (mid,)).fetchall()
    c.close()
    return rows


def test_trashing_the_same_message_offline_again_queues_it_again():
    mid = "<again@example.test>"
    real = _offline()
    try:
        first = server.do_trash({"pk": _message(mid=mid)})
        assert first["ok"] and first.get("status") == "queued", first
        c = store.conn()
        readmutq.QUEUE.finish(c, _queued(mid)[0]["id"], "2026-11-01T10:00:00+00:00")     # the queue applied it
        c.commit()
        c.close()
        second = server.do_trash({"pk": _message(mid=mid)})                # moved back, then trashed again
    finally:
        accounts.connect = real
    rows = _queued(mid)
    assert len(rows) == 2 and rows[-1]["state"] == "queued", [dict(r) for r in rows]
    assert second.get("undoable") is True, second


def test_a_resubmit_with_the_same_mutation_id_queues_once():
    mid = "<twice@example.test>"
    pk = _message(mid=mid)
    real = _offline()
    try:
        server.do_trash({"pk": pk, "mutation_id": "m-1"})
        pk2 = _message(mid=mid)
        again = server.do_trash({"pk": pk2, "mutation_id": "m-1"})
    finally:
        accounts.connect = real
    assert len(_queued(mid)) == 1, _queued(mid)
    assert again["ok"] and again.get("undoable") is False, again


def test_two_cleanups_of_one_sender_undo_separately():
    groups = []
    for _ in range(2):
        _message(sender="weekly@example.test")
        with _trash_probe():
            res, _ = curation.cleanup.execute({"from_addr": "weekly@example.test"})
        groups.append(res["undo_group"])
    assert groups[0] != groups[1], groups


def _page_functions(*names):
    """The named one-line functions from the page's script, as source."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "assets", "legacy", "app.js"), encoding="utf-8") as f:
        page = f.read()
    out = []
    for name in names:
        m = re.search(rf"^function {name}\(\)\{{.*$", page, re.M)
        assert m, name
        out.append(m.group(0))
    return "\n".join(out)


def _node(src):
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_undo_groups_differ_across_page_loads():
    fns = _page_functions("rid", "undoGroup")
    load = "globalThis.self=globalThis;\n" + fns + "\nconsole.log(undoGroup(), undoGroup());"
    first, second = _node(load), _node(load)                 # two processes: two page loads
    ids = first + second
    assert len(set(ids)) == 4, ids
    assert all(re.fullmatch(r"ug-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", i) for i in ids), ids
    bare = _node("globalThis.self={};\n" + fns + "\nconsole.log(undoGroup(), undoGroup());")   # no randomUUID
    assert len(set(bare)) == 2 and all(i.startswith("ug-") and len(i) > 10 for i in bare), bare

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ERR ", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
