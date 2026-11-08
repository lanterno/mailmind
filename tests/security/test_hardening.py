"""Phase 0 hardening: live security and data-loss fixes, each pinned by a test. The routes run through
`app`. What is about the exact commands sent runs on a scripted connection in a
data directory of its own, so nothing here can reach a real server. Addresses are fictional."""
import imaplib
import itertools
import json
import os
import pathlib
import re
import smtplib
import ssl
import subprocess
import sys
import urllib.parse

import pytest

import app_client
import problems
from answered import answered
from fixtures import world as w
from mailmind.bootstrap import accounts, assistant, background, curation, mailbox
from mailmind.bootstrap.accounts import config
from mailmind.bootstrap.compose import transport
from mailmind.bootstrap.mailbox import commands, ingest, server_ops, store
from mailmind.mailbox.adapters.imap.imaplib_session import ImaplibSession
from mailmind.mailbox.domain.command import ServerAnswer
from mailmind.platform import perimeter, settings
from mailmind.platform.workers import gates

ROOT = pathlib.Path(__file__).resolve().parents[2]
ACCT = "ada@example.test"
CONFIG = {"accounts": [{"email": ACCT, "type": "imap", "host": "imap.example.test"}]}


@pytest.fixture
def home(data_dir):
    """A data directory holding the schema and Ada's config, and no mail yet, with nothing kept in
    memory from another test."""
    with open(settings.current().config, "w") as f:
        json.dump(CONFIG, f)
    app_client.reset_state()
    yield data_dir
    app_client.reset_state()


@pytest.fixture
def connected(monkeypatch):
    """connected(M): make M what every IMAP connection the app opens is."""
    def to(M):
        monkeypatch.setattr(accounts, "connect", lambda acct, timeout=60: M)
        return M
    return to


@pytest.fixture
def offline(monkeypatch):
    def unreachable(acct, timeout=60):
        raise ConnectionRefusedError("IMAP down")
    monkeypatch.setattr(accounts, "connect", unreachable)


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


@pytest.fixture
def starttls_ctx(home, monkeypatch):
    """starttls_ctx(host): the TLS context an IMAP connection to `host` gets."""
    monkeypatch.setattr(_FakeIMAP, "made", [])
    monkeypatch.setattr(imaplib, "IMAP4", _FakeIMAP)
    monkeypatch.setattr(config, "resolve_credential", lambda acct: "app-password")

    def ctx(host):
        accounts.connect({"email": ACCT, "host": host, "port": 143, "starttls": True})
        return _FakeIMAP.made[-1].ctx
    return ctx


@pytest.mark.parametrize("host", ["imap.example.test", "192.0.2.10", "127.0.0.1.example.test"])
def test_starttls_off_this_machine_verifies_the_certificate(host, starttls_ctx):
    ctx = starttls_ctx(host)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", " LOCALHOST "])
def test_starttls_on_loopback_accepts_bridges_own_certificate(host, starttls_ctx):
    ctx = starttls_ctx(host)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert not ctx.check_hostname


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


@pytest.fixture
def smtp_ctx(home, monkeypatch):
    """smtp_ctx(host, starttls): the TLS context an SMTP connection to `host` gets."""
    monkeypatch.setattr(_FakeSMTP, "ctxs", [])
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(config, "resolve_credential", lambda acct: "app-password")

    def ctx(host, starttls):
        transport.connect({"email": ACCT, "smtp_host": host, "smtp_port": 587 if starttls else 465,
                           "smtp_starttls": starttls})
        return _FakeSMTP.ctxs[-1]
    return ctx


@pytest.mark.parametrize("starttls", [True, False], ids=["starttls", "implicit_tls"])
def test_smtp_follows_the_same_loopback_rule(starttls, smtp_ctx):
    ctx = smtp_ctx("smtp.example.test", starttls)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    ctx = smtp_ctx("127.0.0.1", starttls)
    assert ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname


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
    with pytest.raises(imaplib.IMAP4.error, match="COPY refused"):
        ImaplibSession(M).move([41], "Gone")
    assert "STORE" not in M.cmds() and "EXPUNGE" not in M.cmds() and "EXPUNGE-ALL" not in M.cmds()


def test_without_uidplus_the_message_stays_flagged_and_nothing_else_goes():
    M = _ScriptedIMAP(MOVE=_NO_MOVE, EXPUNGE=_BAD)
    ImaplibSession(M).move([41], "Archive")        # no exception: copied and flagged
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_a_lost_connection_during_expunge_still_raises():
    M = _ScriptedIMAP(EXPUNGE=imaplib.IMAP4.abort("socket closed"))
    with pytest.raises(imaplib.IMAP4.abort):       # so the caller can queue the op
        ImaplibSession(M).move([41], "Archive")


def test_deleting_a_draft_copy_never_bulk_expunges(home, connected):
    M = connected(_ScriptedIMAP(EXPUNGE=_BAD))
    mailbox.copies.delete_draft_uid({"email": ACCT}, "Drafts", 41, 7)
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


def test_replacing_a_draft_copy_never_bulk_expunges(home, connected):
    M = connected(_ScriptedIMAP(EXPUNGE=_BAD))
    out = mailbox.copies.append_to_drafts({"email": ACCT}, b"Subject: hi\r\n\r\nbody", prev_uid=41, prev_uidvalidity=7)
    assert out == ("Drafts", 42, 7, None)                        # the EXPUNGE was refused: it is still there
    assert ("STORE", "41", "+FLAGS", "(\\Deleted)") in M.calls
    assert "EXPUNGE-ALL" not in M.cmds()


_READS = ["/", "/api/digest", "/api/contacts?q=a", "/api/search?q=a", "/api/accounts", "/api/rules",
          "/api/no-such-route"]
_PAGE = {"Sec-Fetch-Site": "same-origin"}     # what the page's own fetch sends


def bare_get(app, path, headers=None):
    """A GET with only the headers named beside the Host, where the page's own fetch also sends its token."""
    return app.get(path, headers={"X-CSRF-Token": None, "Sec-Fetch-Site": None, **(headers or {})})


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_the_app_still_loads_on_its_own_host(host, app):
    r = bare_get(app, "/", {"Host": f"{host}:{app.port}"})
    assert r.status == 200 and perimeter.CSRF in str(r.body), r.status


def test_the_api_answers_its_own_origin(app):
    assert bare_get(app, "/api/digest", {**_PAGE, "Origin": f"http://127.0.0.1:{app.port}"}).status == 200


@pytest.mark.parametrize("path", _READS)
def test_a_rebound_name_reads_nothing(path, app):
    r = bare_get(app, path, {"Host": f"rebound.example.test:{app.port}"})
    assert r.status == 403
    assert perimeter.CSRF not in str(r.body)


@pytest.mark.parametrize("path", _READS)
def test_a_foreign_origin_reads_nothing(path, app):
    assert bare_get(app, path, {"Origin": f"http://rebound.example.test:{app.port}"}).status == 403


def test_a_refused_read_is_not_counted_as_someone_at_the_screen(app, monkeypatch):
    governor = assistant.governor()
    monkeypatch.setattr(governor, "_last_seen", 0)
    bare_get(app, "/api/digest", {"Host": f"rebound.example.test:{app.port}"})
    assert governor._last_seen == 0


_EVIL = 'INBOX"' + "\r\nA1 DELETE Archive"


@pytest.mark.parametrize("name", ["INBOX", "[Gmail]/All Mail", "Folders/Receipts", 'Notes \\"old\\"', "a\\\\b", "Tab\there"])
def test_folder_names_from_list_pass_through_unchanged(name):
    assert accounts.mailbox_arg(name) == f'"{name}"'


def test_a_refused_folder_name_is_handled_like_the_servers_bad():
    assert issubclass(accounts.BadMailbox, imaplib.IMAP4.error)


@pytest.mark.parametrize("name", [_EVIL, "a\nb", "a\rb", "a\x00b", 'x"y', "trail\\", "", None])
def test_a_folder_name_that_would_break_the_command_is_refused(name):
    with pytest.raises(accounts.BadMailbox):
        accounts.mailbox_arg(name)


def _sent_a_line_break(M):
    return [c for c in M.calls if any(isinstance(a, str) and ("\r" in a or "\n" in a) for a in c)]


def test_a_move_to_an_injected_folder_sends_nothing():
    M = _ScriptedIMAP()
    with pytest.raises(accounts.BadMailbox):
        ImaplibSession(M).move([41], "Archive\r\nA1 DELETE INBOX")
    assert M.calls == []


def test_an_injected_folder_in_the_url_reaches_no_imap_command(app, connected):
    M = connected(_ScriptedIMAP())
    q = urllib.parse.urlencode({"account": w.ADA, "folder": _EVIL})
    assert app.get("/api/messages?" + q).status == 200
    app.settle()
    assert not _sent_a_line_break(M), M.calls


@pytest.mark.parametrize("path", ["/api/digest", "/api/stats", "/api/contacts?q=a", "/api/no-such-route"])
@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_read_another_site_starts_is_refused(site, path, app):
    r = bare_get(app, path, {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"})
    assert r.status == 403


def test_another_site_can_link_to_the_app_but_not_frame_it(app):
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
    r = bare_get(app, "/", {**nav, "Sec-Fetch-Dest": "document"})
    assert r.status == 200 and perimeter.CSRF in str(r.body), r.status
    assert bare_get(app, "/", {**nav, "Sec-Fetch-Dest": "iframe"}).status == 403
    assert bare_get(app, "/api/digest", {**nav, "Sec-Fetch-Dest": "document"}).status == 403


def test_the_api_answers_the_pages_own_reads(app):
    assert bare_get(app, "/api/digest", _PAGE).status == 200


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "none"}, {}], ids=["a_typed_url", "says_nothing"])
def test_the_api_answers_only_the_pages_own_reads(headers, app):
    # a typed URL; a browser too old to say, or a script
    assert bare_get(app, "/api/digest", headers).status == 403
    r = bare_get(app, "/", headers)     # the page itself still opens
    assert r.status == 200 and perimeter.CSRF in str(r.body), r.status


@pytest.mark.parametrize("path", ["/", "/api/digest", "/api/no-such-route"])
def test_no_page_may_frame_the_app(path, app):
    assert bare_get(app, path, _PAGE).headers.get("x-frame-options") == "DENY"


_uid = itertools.count(501)


def _message(folder="INBOX", sender="bo@example.test", mid=None):
    """A fictional message row in the temp database; returns its pk."""
    uid = next(_uid)
    c = store.conn()
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, "
                    "subject) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ACCT, "imap", folder, uid, mid or f"<h{uid}@example.test>",
                     f"h{uid}", "2026-11-01T09:00:00+00:00", sender, "Lunch"))
    c.commit()
    pk = cur.lastrowid
    c.close()
    return pk


def _uid_of(pk):
    c = store.conn()
    uid = store.get_message(c, pk)["uid"]
    c.close()
    return uid


@pytest.mark.parametrize("flag", ["deleted", "draft", "answered", "Deleted", "bogus"])
def test_a_request_can_only_star_or_mark_read(flag, home, connected):
    pk = _message()
    M = connected(_ScriptedIMAP())
    assert answered(mailbox.filing.do_write, "/api/flag", {"pk": pk, "flag": flag, "on": True}) == (problems.refused("unknown flag"), 400)
    assert M.calls == []


@pytest.mark.parametrize("flag, imap", [("flagged", "(\\Flagged)"), ("seen", "(\\Seen)")])
def test_a_request_can_star_or_mark_read(flag, imap, home, connected):
    pk = _message()
    M = connected(_ScriptedIMAP())
    res = mailbox.filing.do_write("/api/flag", {"pk": pk, "flag": flag, "on": True})
    assert res["ok"] is True, res
    assert ("STORE", str(_uid_of(pk)), "+FLAGS", imap) in M.calls, M.calls


_REFUSE = {"MOVE": ("NO", [b"[OVERQUOTA] no room"]), "COPY": ("NO", [b"[OVERQUOTA] no room"])}
_FOLDERS = ("OK", [b'(\\HasNoChildren \\Junk) "/" "Junk"', b'(\\HasNoChildren \\Archive) "/" "Archive"'])


def _still_there(pk):
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    return row is not None and row["pending_removal"] is None


def _refusing():
    M = _ScriptedIMAP(**_REFUSE)
    M.list = lambda: _FOLDERS
    return M


@pytest.mark.parametrize("op", [
    pytest.param(lambda pk: mailbox.filing.do_trash({"pk": pk}), id="trash"),
    pytest.param(lambda pk: mailbox.filing.do_write("/api/archive", {"pk": pk}), id="archive"),
    pytest.param(lambda pk: mailbox.filing.do_write("/api/junk", {"pk": pk}), id="junk"),
])
def test_a_refused_move_keeps_the_message_and_says_so(op, home, connected):
    pk = _message()
    depth = len(mailbox.undo.entries())
    connected(_refusing())
    res, status = answered(op, pk)
    assert (status, res["type"]) == (409, "/problems/conflict"), res
    assert len(mailbox.undo.entries()) == depth
    assert _still_there(pk)


def test_a_refused_gmail_archive_keeps_the_message(home, connected, monkeypatch):
    pk = _message()
    real = accounts.get
    monkeypatch.setattr(accounts, "get", lambda email: {**real(email), "kind": "gmail"})
    connected(_ScriptedIMAP(STORE=("NO", [b"label not removed"])))
    res, status = answered(mailbox.filing.do_write, "/api/archive", {"pk": pk})
    assert (status, res["type"]) == (409, "/problems/conflict"), res
    assert _still_there(pk)


def test_an_accepted_move_still_files_the_message(home, connected):
    pk = _message()
    M = connected(_ScriptedIMAP())
    M.list = lambda: _FOLDERS
    res = mailbox.filing.do_write("/api/archive", {"pk": pk})
    assert res["ok"] is True and res["undoable"] is True, res
    c = store.conn()
    command = c.execute("SELECT dest, applied FROM mail_commands ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    assert (command["dest"], command["applied"]) == ("Archive", 1)
    assert not _still_there(pk)


def test_the_route_reports_a_refusal_and_offers_no_undo(home, connected):
    pk = _message()
    depth = len(mailbox.undo.entries())
    connected(_refusing())
    res, status = answered(mailbox.filing.do_write, "/api/archive", {"pk": pk})
    assert (status, res["type"]) == (409, "/problems/conflict"), res
    assert len(mailbox.undo.entries()) == depth
    assert _still_there(pk)


def _optimized(code, home):
    """Run code under python -O, which strips asserts, from the repo root; return its stdout."""
    out = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT, env={**os.environ, "MAILMIND_HOME": str(home)},
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.parametrize("name", ["Bcc", "BCC", "bcc "])
def test_bcc_never_leaves_even_under_python_O(name, tmp_path):
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
""", tmp_path)
    assert got == "REFUSED"


def test_a_malformed_config_is_never_written_even_under_python_O(tmp_path):
    got = _optimized(f"""
import os
from mailmind.bootstrap.accounts import config
from mailmind.platform import settings
home = {str(tmp_path)!r}
settings.configure(settings.Settings(home=home))
open(settings.current().config, "w").write('{{"accounts": []}}')
try:
    config.save({{"accounts": "wiped"}})
    print("WRITTEN")
except ValueError:
    print("REFUSED", open(settings.current().config).read(), sorted(os.listdir(home)))
""", tmp_path)
    assert got == "REFUSED {\"accounts\": []} ['mailmind.config.json']"


def test_a_failed_read_answers_without_the_exception_text(app, monkeypatch):
    def boom(q):
        raise RuntimeError("NO [AUTHENTICATIONFAILED] ada@example.test secret=hunter2 /home/ada/mailmind.db")
    monkeypatch.setattr(mailbox.digest, "answer", boom)
    r = bare_get(app, "/api/digest", _PAGE)
    assert r.status == 500
    assert r.json == problems.FAILED


def _undo_target(uid, mid):
    """A server holding one message, found by its Message-ID at this UID."""
    head = (f"From: Bo <bo@example.test>\r\nSubject: Back again\r\nMessage-ID: {mid}\r\n"
            "Date: Mon, 2 Nov 2026 09:00:00 +0000\r\n\r\n").encode()
    return _ScriptedIMAP(SEARCH=("OK", [str(uid).encode()]),
                         FETCH=("OK", [(f"1 (UID {uid} FLAGS (\\Seen) BODY[] {{{len(head)}}}".encode(), head),
                                       b")"]))


def _watermark(folder):
    c = store.conn()
    wm = store.get_watermark(c, ACCT, folder)
    c.close()
    return wm


def test_undo_ingest_leaves_the_inbox_watermark_alone(home, connected):
    c = store.conn()
    store.set_watermark(c, ACCT, 100, "2026-11-01T09:00:00+00:00", "INBOX")
    c.commit()
    c.close()
    connected(_undo_target(900, "<back@example.test>"))
    assert ingest.ingest_message(accounts.get(ACCT), "INBOX", "<back@example.test>") == 1
    c = store.conn()
    got = c.execute("SELECT uid FROM messages WHERE message_id=?", ("<back@example.test>",)).fetchall()
    c.close()
    assert [r["uid"] for r in got] == [900], got
    assert _watermark("INBOX") == 100    # UIDs 101..899 still get synced


def test_undo_ingest_into_an_unopened_folder_keeps_its_first_full_sync(home, connected):
    connected(_undo_target(31, "<filed@example.test>"))
    assert ingest.ingest_message(accounts.get(ACCT), "Archive", "<filed@example.test>") == 1
    assert not ingest.folder_synced(ACCT, "Archive")


@pytest.fixture
def trash_probe(monkeypatch):
    """Stands in for a command's try: the list of whether the IMAP gate was held at each call."""
    held = []

    def probe(row, first_try=False):
        held.append(gates.held())
        return ServerAnswer(dest="Trash")
    monkeypatch.setattr(server_ops, "apply", probe)
    return held


def test_cleanup_and_block_trash_mail_under_the_imap_lock(home, trash_probe):
    for _ in range(2):
        _message(sender="promo@example.test")
    res = curation.cleanup.execute({"from_addr": "promo@example.test"})
    assert res["swept"] == 2, res
    assert trash_probe == [True, True]
    trash_probe.clear()
    pk = _message(sender="spam@example.test")
    res = curation.screener.rule({"token": "spam@example.test", "verdict": "block", "pk": pk})
    assert res["trashed"] is True, res
    assert trash_probe == [True]
    assert not gates.held()


@pytest.mark.parametrize("value, want", [("1", True), ("true", True), ("yes", True), (" On ", True), ("anything", True),
                                         ("", False), ("0", False), ("false", False), ("No", False), ("off", False)])
def test_nosync_reads_one_way_and_fails_toward_read_only(value, want, monkeypatch):
    monkeypatch.setenv("MAILMIND_NOSYNC", value)
    assert background.nosync() is want


def test_nosync_unset_is_off(monkeypatch):
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    assert background.nosync() is False


def test_one_place_reads_nosync():
    """So every check agrees: the one module that reads the environment."""
    readers = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("tests", "node_modules", "__pycache__")]
        for name in files:
            if name.endswith(".py") and not name.startswith("test"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    if re.search(r"""["']MAILMIND_NOSYNC["']""", f.read()):
                        readers.append(name)
    assert readers == ["environment.py"], f"read MAILMIND_NOSYNC in one place only, not {readers}"


def _queued(mid):
    c = store.conn()
    rows = c.execute("SELECT id, state FROM mail_commands WHERE message_id=? ORDER BY id", (mid,)).fetchall()
    c.close()
    return rows


def test_trashing_the_same_message_offline_again_queues_it_again(home, offline):
    mid = "<again@example.test>"
    first = mailbox.filing.do_trash({"pk": _message(mid=mid)})
    assert first["ok"] and first.get("status") == "queued", first
    c = store.conn()
    commands.QUEUE.finish(c, _queued(mid)[0]["id"], "2026-11-01T10:00:00+00:00")     # the queue applied it
    c.commit()
    c.close()
    second = mailbox.filing.do_trash({"pk": _message(mid=mid)})                # moved back, then trashed again
    rows = _queued(mid)
    assert len(rows) == 2 and rows[-1]["state"] == "queued", [dict(r) for r in rows]
    assert second.get("undoable") is True, second


def test_a_resubmit_with_the_same_mutation_id_queues_once(home, offline):
    mid = "<twice@example.test>"
    mailbox.filing.do_trash({"pk": _message(mid=mid), "mutation_id": "m-1"})
    again = mailbox.filing.do_trash({"pk": _message(mid=mid), "mutation_id": "m-1"})
    assert len(_queued(mid)) == 1, _queued(mid)
    assert again["ok"] and again.get("undoable") is False, again


def test_two_cleanups_of_one_sender_undo_separately(home, trash_probe):
    groups = []
    for _ in range(2):
        _message(sender="weekly@example.test")
        res = curation.cleanup.execute({"from_addr": "weekly@example.test"})
        groups.append(res["undo_group"])
    assert groups[0] != groups[1], groups
