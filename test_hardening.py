"""Phase 0 hardening: live security and data-loss fixes, each pinned by a test. Hermetic: a temp
database and config, fake IMAP and SMTP connections, and fictional addresses, so nothing here
can reach a real server.
Run: python3 test_hardening.py"""
import contextlib
import http.client
import imaplib
import json
import os
import ssl
import tempfile
import threading
import urllib.parse

import store
import config
import mailclass
import rules
import views
_TMP = tempfile.mkdtemp()
store.DB = os.path.join(_TMP, "mailmind.db")        # before anything opens a connection
config.CONFIG = os.path.join(_TMP, "mailmind.config.json")
rules.FILE = mailclass.RULES_FILE = os.path.join(_TMP, "mailmind_rules.json")
views.FILE = os.path.join(_TMP, "mailmind_views.json")
with open(config.CONFIG, "w") as f:
    json.dump({"accounts": [{"email": "ada@example.test", "type": "imap",
                             "host": "imap.example.test"}]}, f)
import accounts
import mailops
import server
import smtpsend
server.RECON_AUDIT = os.path.join(_TMP, "reconcile_audit.jsonl")
server.SWEEP_AUDIT = os.path.join(_TMP, "sweep_audit.jsonl")
store.init()


class _FakeIMAP:
    """Stands in for imaplib.IMAP4: records the TLS context STARTTLS was given."""
    made = []

    def __init__(self, host, port, timeout=None):
        self.host, self.ctx = host, None
        _FakeIMAP.made.append(self)

    def starttls(self, ctx):
        self.ctx = ctx

    def login(self, user, pw):
        return "OK", [b"logged in"]


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
    mailops._move_uid(M, "41", "Archive")
    assert ("COPY", "41", '"Archive"') in M.calls
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert ("EXPUNGE", "41") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_a_failed_copy_leaves_the_message_untouched():
    M = _ScriptedIMAP(MOVE=_NO_MOVE, COPY=("NO", [b"[TRYCREATE] no such folder"]))
    try:
        mailops._move_uid(M, "41", "Gone")
        raise AssertionError("a failed COPY must raise")
    except imaplib.IMAP4.error as e:
        assert "COPY failed" in str(e)
    assert "STORE" not in M.cmds() and "EXPUNGE" not in M.cmds() and "EXPUNGE-ALL" not in M.cmds()


def test_without_uidplus_the_message_stays_flagged_and_nothing_else_goes():
    M = _ScriptedIMAP(MOVE=_NO_MOVE, EXPUNGE=_BAD)
    mailops._move_uid(M, "41", "Archive")          # no exception: copied and flagged
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_a_lost_connection_during_expunge_still_raises():
    M = _ScriptedIMAP(EXPUNGE=imaplib.IMAP4.abort("socket closed"))
    try:
        mailops.expunge_uid(M, "41")
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
    assert out == ("Drafts", 42, 7)
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()



@contextlib.contextmanager
def _live_server():
    """The real handler on an ephemeral 127.0.0.1 port; yields get(path, headers) -> (status, body)."""
    srv = server._Server(("127.0.0.1", 0), server.H)
    port = srv.server_address[1]
    old_port, server.PORT = server.PORT, port
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path, headers):
        h = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h.request("GET", path, headers={k: v.replace("PORT", str(port)) for k, v in headers.items()})
        r = h.getresponse()
        out = r.status, r.read()
        h.close()
        return out
    try:
        yield get
    finally:
        srv.shutdown()
        srv.server_close()
        server.PORT = old_port


_READS = ["/", "/api/digest", "/api/contacts?q=a", "/api/search?q=a", "/api/accounts", "/api/rules",
          "/api/no-such-route"]


def test_the_app_still_loads_on_its_own_host():
    with _live_server() as get:
        for host in ("127.0.0.1:PORT", "localhost:PORT"):
            code, body = get("/", {"Host": host})
            assert code == 200 and server.CSRF.encode() in body, (host, code)
        code, _ = get("/api/digest", {"Host": "127.0.0.1:PORT", "Origin": "http://127.0.0.1:PORT"})
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
    with _live_server() as get:
        server._LAST_UI_TS = 0
        get("/api/digest", {"Host": "rebound.example.test:PORT"})
        assert server._LAST_UI_TS == 0, server._LAST_UI_TS


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
        mailops._move_uid(M, "41", "Archive\r\nA1 DELETE INBOX")
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
            code, _ = get("/api/messages?" + q, {"Host": "127.0.0.1:PORT"})
    finally:
        accounts.connect = real
    assert code == 200, code
    assert not _sent_a_line_break(M), _sent_a_line_break(M)

if __name__ == "__main__":
    import sys
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
