"""Mail I/O behaviour a rewrite could silently lose: the hard-won fixes in ingest, mailops, the
offline queue, the outbox, drafts, sweep and expiry, the folder rail, the connection pool and the
IDLE worker, each pinned against the world's fake IMAP and SMTP servers. The fixes the older
suites already pin (test_sync_state, test_queue_drain, test_http_mail, test_http_send,
test_hardening, test_draft_resume, test_expire...) are not repeated here.

The strict xfails at the end state the right behaviour for bugs still in the code: fixing one
makes its test pass, and strict makes that an error until the marker comes off.

Most tests run on the frozen clock or none. Two need real time, briefly: the IDLE worker's, and
the outbox worker's, whose bug is how long it sleeps. The app's endless loops (backfill) are run
for a cycle or two with their sleeps stubbed out.
"""
import threading
import time
from datetime import timedelta

import pytest

import gate
from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"
JUNK_INBOX = ("ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml")


def acct(email=w.ADA):
    import accounts
    return accounts.get(email)


def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def late(n=1, **headers):
    """One more of Lin's lunch replies, with its own Message-ID."""
    return w.variant("ada-inbox-02-lunch-confirm.eml", **{"Message_ID": f"<late-{n}@example.test>",
                                                          "Subject": f"Re: Lunch, late word {n}", **headers})


def stored(folder="INBOX", email=w.ADA):
    """{uid: message_id} of the folder's rows in the database."""
    return {r["uid"]: r["message_id"] for r in q(
        "SELECT uid, message_id FROM messages WHERE account=? AND folder=?", email, folder)}


def watermark(folder="INBOX", email=w.ADA):
    import store
    c = store.conn()
    try:
        return store.get_watermark(c, email, folder)
    finally:
        c.close()


def boxes(world, name):
    """Where `name` is: [(account, folder)] on the server, and in the database."""
    got = world.where(name)
    return sorted((s[0], s[1]) for s in got["server"]), sorted((d["account"], d["folder"]) for d in got["db"])


def gone(world, name):
    """Another client deletes `name` from the server."""
    with world.server.lock:
        world.server.mailbox(*world.placed[name][:2]).messages.pop(world.uid(name))


def count(world, command, user=w.ADA):
    return len(world.server.commands_for(user, command))


def queue(app, world, op="archive", name=RECEIPT, account=w.ADA, mutation_id="m-1"):
    """Ask for `op` on `name` while the server drops the connection: the move is queued."""
    world.server.drop("SELECT", user=account)
    r = app.post(f"/api/{op}", {"pk": world.pk(name), "mutation_id": mutation_id}).json
    assert r["status"] == "queued", r
    return r


def queued():
    return q("SELECT id, op, state, attempts, applied, dest FROM read_mutations ORDER BY id")


def new(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin,\n\nThe linen came in. Want half?\n\nAda", **kw}


def outbox_row(oid):
    [r] = q("SELECT state, attempts, delivered, sent_appended, message_id, raw FROM outbox WHERE id=?", oid)
    return r


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class _Stop(BaseException):
    """Ends one of the app's endless loops from inside a stubbed sleep."""


class _Sleeps:
    """Stands in for the `time` module inside server.py: every sleep returns at once and is
    recorded, and the loop ends at the `stop_at`-th sleep of `8` seconds (the pause after a
    backfill cycle that found work)."""

    def __init__(self, stop_at):
        self.slept, self.stop_at = [], stop_at

    def sleep(self, s):
        self.slept.append(s)
        if self.slept.count(8) >= self.stop_at:
            raise _Stop()

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def idle(monkeypatch):
    """realtime with its own stop flag, kick and dirty set, so stopping a worker here leaves the
    module as the next test expects it."""
    import realtime
    monkeypatch.setattr(realtime, "_stop", threading.Event())
    monkeypatch.setattr(realtime, "_kick", threading.Event())
    monkeypatch.setattr(realtime, "_dirty", set())
    monkeypatch.setattr(realtime, "_conns", [])
    monkeypatch.setattr(realtime, "_watchers", {})
    monkeypatch.setattr(realtime, "_running", False)
    workers = []

    def start(email=w.ADA):
        t = threading.Thread(target=realtime._worker, args=(acct(email),), name="idle-worker", daemon=True)
        t.start()
        workers.append(t)
        return t
    yield start
    realtime.stop()
    for t in workers:
        t.join(5)
    assert not any(t.is_alive() for t in workers), "an IDLE worker didn't stop"


# -- snippets --------------------------------------------------------------

STYLED = ("From: Lin Mori <lin@example.test>\r\nTo: Ada Park <ada@example.test>\r\n"
          "Subject: Styled note\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
          "Message-ID: <styled-1@lin.example.test>\r\nMIME-Version: 1.0\r\n"
          "Content-Type: text/html; charset=utf-8\r\n\r\n"
          "<html><head><title>Hidden title</title><style>.ReadMsgBody{width:100%}</style></head>"
          "<body><script>var tracker = 1;</script><noscript>Turn on scripts</noscript>"
          "<!-- layout table --><p>Fabric is in.</p></body></html>\r\n")


def test_hwf_an_html_only_snippet_shows_no_style_script_title_or_comment(world):
    uid = world.deliver(w.ADA, "INBOX", STYLED)
    [r] = q("SELECT snippet FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert r["snippet"].split() == ["Fabric", "is", "in."]


def test_hwf_a_header_in_an_unknown_charset_decodes_as_utf8(world):
    import base64
    word = "=?x-unknown?B?" + base64.b64encode("Caf\u00e9 menu".encode()).decode() + "?="
    uid = world.deliver(w.ADA, "INBOX", late(Subject=word))
    [r] = q("SELECT subject FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert r["subject"] == "Caf\u00e9 menu"


def test_hwf_a_body_fetch_the_server_refuses_is_logged_and_the_row_kept(world, capsys):
    def body_fetch(args):
        items = args[1] if isinstance(args[1], list) else [args[1]]
        return [str(i).upper() for i in items] == ["BODY.PEEK[]"]
    world.server.fail("UID FETCH", user=w.ADA, when=body_fetch)
    uid = world.deliver(w.ADA, "INBOX", late())
    assert f"[ingest] body fetch {w.ADA} uid {uid}: NO" in capsys.readouterr().out
    assert stored()[uid] == "<late-1@example.test>"


# -- Gmail threads ---------------------------------------------------------

def test_hwf_x_gm_thrid_is_asked_of_gmail_only_and_read_from_the_fetch_data(world):
    spoof = b"X-GM-THRID: 4242\r\n"                     # a header claiming a thread; only Gmail's FETCH data counts
    ada_uid = world.deliver(w.ADA, "INBOX", spoof + late())
    cy_uid = world.deliver(w.CY, "INBOX", spoof + w.variant("cy-inbox-03-unrelated.eml",
                                                            Message_ID="<spoof-1@example.test>"))
    assert not any("X-GM-THRID" in c["line"] for c in world.server.commands_for(w.ADA, "UID FETCH"))
    assert any("X-GM-THRID" in c["line"] for c in world.server.commands_for(w.CY, "UID FETCH"))
    [ada] = q("SELECT gmail_thread, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
              w.ADA, ada_uid)
    assert ada["gmail_thread"] is None and not ada["thread_id"].startswith("gm:")
    [cy] = q("SELECT gmail_thread FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.CY, cy_uid)
    assert cy["gmail_thread"] == str(world.server.messages(w.CY, "INBOX")[cy_uid].gm_thrid) != "4242"


# -- what a sync stores ----------------------------------------------------

def test_hwf_a_bounded_first_sync_takes_the_newest_uids(world):
    import ingest
    for n in range(3):
        world.server.deliver(w.ADA, "Junk", late(n))
    ingest.sync_folder(acct(), "Junk", recent=2)          # how folder warming first touches a folder
    assert stored("Junk") == {2: "<late-1@example.test>", 3: "<late-2@example.test>"}


def test_hwf_rows_commit_one_by_one_and_the_watermark_moves_only_after_all(world, monkeypatch):
    import ingest
    import store
    wm = watermark()
    uids = [world.server.deliver(w.ADA, "INBOX", late(n)) for n in range(3)]
    real, seen = store.upsert_message, []

    def dies_on_the_third(c, m):
        seen.append(m["uid"])
        if len(seen) == 3:
            raise RuntimeError("the process died here")
        return real(c, m)
    monkeypatch.setattr(store, "upsert_message", dies_on_the_third)
    with pytest.raises(RuntimeError):
        ingest.sync_new(acct())
    assert [u for u in uids if u in stored()] == uids[:2]        # what was stored before the crash stays
    assert watermark() == wm                                     # but the folder isn't marked synced past it
    monkeypatch.setattr(store, "upsert_message", real)
    ingest.sync_new(acct())
    assert [u for u in uids if u in stored()] == uids and watermark() == uids[-1]


# -- reconcile -------------------------------------------------------------

def test_hwf_reconcile_connects_with_a_short_timeout_and_always_logs_out(world, monkeypatch):
    import ingest
    from mailmind.bootstrap import mailbox
    real, timeouts = mailbox.open_session, []
    monkeypatch.setattr(mailbox, "open_session", lambda a, timeout=60: (timeouts.append(timeout), real(a, timeout))[1])
    for trouble in (None, "UID SEARCH", "EXAMINE"):
        if trouble:
            world.server.fail(trouble, user=w.ADA)
        logins, logouts = count(world, "LOGIN"), count(world, "LOGOUT")
        res = ingest.reconcile_account(acct())
        assert bool(res["error"]) == bool(trouble), (trouble, res)
        assert (count(world, "LOGIN"), count(world, "LOGOUT")) == (logins + 1, logouts + 1), trouble
    assert timeouts == [15, 15, 15]


# -- when the server says no, or can't be reached --------------------------

def test_hwf_a_refused_select_keeps_the_message(app, world):
    for path in ("/api/trash", "/api/archive", "/api/junk"):
        world.server.fail("SELECT", user=w.ADA)
        since = world.mark()
        r = app.post(path, {"pk": world.pk(RECEIPT)}).json
        assert (r["ok"], r["undoable"]) == (False, False), path
        assert world.writes(since) == [], path
        assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")]), path


def test_hwf_a_refused_login_queues_the_move_instead_of_dropping_it(app, world):
    import readmutq
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] invalid credentials", user=w.ADA)
    r = app.post("/api/archive", {"pk": world.pk(RECEIPT), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [])
    readmutq.drain()                                             # the login works again
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]


def test_hwf_a_malformed_message_id_is_quoted_so_undo_finds_it(app, world):
    mid = "<b'AB\"x\\y@example.test>"                            # a quote and a backslash, as some senders mint
    raw = w.raw(RECEIPT).replace(w.message_id(RECEIPT).encode(), mid.encode(), 1)
    uid = world.deliver(w.ADA, "INBOX", raw)
    [pk] = [r["pk"] for r in q("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)]
    assert app.post("/api/trash", {"pk": pk}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    assert world.server.find(w.ADA, "INBOX", mid) and mid in stored().values()


# -- the offline queue -----------------------------------------------------

def test_hwf_the_queue_finds_its_message_by_message_id_not_its_old_uid(app, world):
    import readmutq
    queue(app, world)
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # the receipt's old UID now names the invite
    readmutq.drain()
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]
    assert boxes(world, "ada-inbox-06-outlook-invite.eml")[0] == [(w.ADA, "INBOX")]


def test_hwf_a_queued_move_undone_while_the_worker_waits_for_the_lock_never_ships(app, world, monkeypatch):
    import readmutq
    from mailmind.platform.workers import gates
    queue(app, world)
    since, undone = world.mark(), []

    class TheUndoGetsTheGateFirst:
        """The gate as the queue's worker sees it: when the worker, having claimed the row, asks
        for the gate, the user's undo takes it first."""
        def __getattr__(self, name):
            return getattr(gates, name)

        def run(self, account, fn, **kw):
            undone.append(app.post("/api/undo", {}).json["ok"])
            return gates.run(account, fn, **kw)
    monkeypatch.setattr(readmutq, "gates", TheUndoGetsTheGateFirst())
    readmutq.drain()                                            # claimed, then cancelled before it got the lock
    assert undone == [True]
    assert world.writes(since) == []
    assert queued() == []
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])


# -- sweep, expiry and the AI sweep ----------------------------------------

def audit(world):
    import json
    p = world.dir / "sweep_audit.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def test_hwf_a_sweep_retries_a_move_the_server_said_ok_to_but_didnt_do(world):
    import manage
    world.server.fail("UID MOVE", "OK [scripted] moved, or so it says", user=w.ADA)
    res = manage.sweep_account(acct())
    assert (res["moved"], res["stragglers"], res["error"]) == (3, [], None)
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "Trash")], []), name


def test_hwf_a_sweep_reports_and_keeps_mail_that_never_moved(world):
    import manage
    uid = str(world.uid("ada-inbox-04-ci.eml"))
    world.server.fail("UID MOVE", "OK [scripted] moved, or so it says", times=None, user=w.ADA,
                      when=lambda a: uid in str(a[0]).split(","))
    res = manage.sweep_account(acct())
    assert (res["moved"], res["stragglers"]) == (2, [uid])
    assert boxes(world, "ada-inbox-04-ci.eml") == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
    assert boxes(world, "ada-inbox-12-scam.eml") == ([(w.ADA, "Trash")], [])


def test_hwf_a_sweep_audits_before_it_moves(world):
    import manage
    world.server.drop("UID MOVE", user=w.ADA)
    res = manage.sweep_account(acct())
    assert res["error"] and res["moved"] == 0
    assert sorted(int(e["uid"]) for e in audit(world)) == sorted(world.uid(n) for n in JUNK_INBOX)
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")]), name


FAR = 4_000_000_000                                              # a "now" long after every corpus date


def test_hwf_expiry_audits_before_it_moves(world):
    import manage
    import rules
    rules.set_expiry("loom.example.test", 1)
    world.server.drop("UID MOVE", user=w.ADA)
    res = manage.expire_account(acct(), now=FAR)
    assert res["error"] and res["moved"] == 0
    assert [(e["uid"], e["bucket"]) for e in audit(world)] == [(str(world.uid("ada-inbox-03-newsletter.eml")),
                                                                "expired")]
    assert boxes(world, "ada-inbox-03-newsletter.eml")[0] == [(w.ADA, "INBOX")]


def test_hwf_expiry_prunes_the_local_row_by_message_id(world):
    import manage
    import rules
    rules.set_expiry("thimble.example.test", 1)                 # the receipt's sender
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # the receipt is now UID 4 on the server
    assert manage.expire_account(acct(), now=FAR)["moved"] == 1
    assert boxes(world, RECEIPT) == ([(w.ADA, "Trash")], [])
    assert world.where("ada-inbox-04-ci.eml")["db"][0]["uid"] == 4    # the row stored at UID 4 stays


def test_hwf_a_sweep_still_moves_the_junk_when_the_server_refuses_move(world):
    import manage
    world.server.fail("UID MOVE", "BAD [CANNOT] MOVE not supported", times=None, user=w.ADA)
    assert manage.sweep_account(acct())["moved"] == 3


def test_hwf_a_sweep_and_expiry_on_a_server_without_move_copy_then_expunge_one_uid(world):
    import manage
    import rules
    world.server.without = {"MOVE"}
    since = world.mark()
    assert manage.sweep_account(acct())["moved"] == 3
    rules.set_expiry("thimble.example.test", 1)                 # the receipt's sender
    assert manage.expire_account(acct(), now=FAR)["moved"] == 1
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Trash")]
    kinds = [line.split(" ", 3)[2] + " " + line.split(" ", 3)[3].split(" ", 1)[0]
             for line in world.writes(since)]
    assert set(kinds) == {"UID COPY", "UID STORE", "UID EXPUNGE"}   # never MOVE, never a bulk EXPUNGE


def test_hwf_expiry_ages_mail_by_when_it_arrived_not_its_date_header(world):
    import email.utils
    from datetime import datetime, timezone
    import manage
    import rules
    rules.set_expiry("loom.example.test", 72)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    hour, month = now - timedelta(hours=1), now - timedelta(days=30)

    def issue(n, dated, arrived):
        raw = w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<loom-{n}@example.test>",
                        Date=email.utils.format_datetime(dated))
        world.server.deliver(w.ADA, "INBOX", raw, when=arrived)
    issue("old-header", month, hour)                            # written long ago, arrived an hour ago: kept
    issue("new-header", hour, month)                            # says an hour old, arrived a month ago: expired
    manage.expire_account(acct(), now=now.timestamp())
    assert world.server.find(w.ADA, "INBOX", "<loom-old-header@example.test>")
    assert world.server.find(w.ADA, "Trash", "<loom-new-header@example.test>")


def test_hwf_the_ai_sweep_finds_its_targets_in_the_live_inbox_by_message_id(world):
    import manage
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # every stored UID is now one off
    res = manage.sweep_account_ai(acct())
    assert (res["moved"], res["stragglers"]) == (3, [])
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "Trash")], []), name
    for name in (RECEIPT, "ada-inbox-06-outlook-invite.eml"):
        assert boxes(world, name)[0] == [(w.ADA, "INBOX")], name


# -- the outbox ------------------------------------------------------------

def test_hwf_a_crash_after_delivery_never_sends_twice(app, world, monkeypatch):
    import outbox
    import store
    oid = app.post("/api/send", new(idempotency_key="k-crash")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    real = outbox._side_effects

    def dies(*a):
        raise RuntimeError("the process died here")
    monkeypatch.setattr(outbox, "_side_effects", dies)
    outbox.drain()
    assert (outbox_row(oid)["state"], outbox_row(oid)["delivered"], len(world.smtp.messages)) == ("sending", 1, 1)
    monkeypatch.setattr(outbox, "_side_effects", real)
    c = store.conn()
    outbox.QUEUE.reset_stuck(c)                                  # what the worker does at boot
    c.commit()
    c.close()
    outbox.drain()
    r = outbox_row(oid)
    assert (r["state"], r["sent_appended"], len(world.smtp.messages)) == ("sent", 1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1


def test_hwf_the_sent_copy_is_the_bytes_that_went_on_the_wire(app, world):
    import outbox
    oid = app.post("/api/send", new(idempotency_key="k-bytes")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    r = outbox_row(oid)
    [wire] = world.smtp.messages
    [uid] = world.server.find(w.ADA, "Sent", r["message_id"])
    copy = world.server.messages(w.ADA, "Sent")[uid].raw
    assert copy == r["raw"]
    assert copy.rstrip(b"\r\n") == wire.data.rstrip(b"\r\n")


def test_hwf_only_a_send_past_the_undo_window_is_listed_as_scheduled(app):
    import outbox
    now = int(time.time())                                       # frozen
    ids = [app.post("/api/send", new(idempotency_key=f"k-{n}", **kw)).json["outbox_id"] for n, kw in enumerate((
        {}, {"send_after": now + outbox.SEND_DELAY + 5}, {"send_after": now + outbox.SEND_DELAY + 6}))]
    listed = {i["id"]: i["scheduled"] for i in app.get("/api/outbox").json["items"]}
    assert [listed[i] for i in ids] == [False, False, True]


# -- drafts ----------------------------------------------------------------

def test_hwf_a_draft_delete_declines_when_uidvalidity_rolled(app, world):
    saved = app.post("/api/draft", new(subject="Fabric order (draft)")).json
    gone(world, "ada-drafts-01-thunderbird-v1.eml")
    world.server.renumber(w.ADA, "Drafts")                      # Drafts rebuilt: every UID renumbered
    other = world.server.deliver(w.ADA, "Drafts", w.variant("ada-drafts-02-thunderbird-v2.eml",
                                                            Message_ID="<other-draft@example.test>"),
                                 flags=(w.SEEN, w.DRAFT))
    assert other == saved["uid"]                                # the saved copy's old UID now names another draft
    since = world.mark()
    assert app.post("/api/draft", {"delete": True, "draft_id": saved["draft_id"]}).json["deleted"]
    assert world.writes(since) == []
    assert world.server.find(w.ADA, "Drafts", "<other-draft@example.test>") == [other]


# -- the folder rail -------------------------------------------------------

def folders(app):
    return app.get(f"/api/folders?account={w.ADA}").json["folders"]


def test_hwf_the_folder_rail_lists_the_server_once_per_ttl_and_serves_stale_on_failure(app, world):
    n = count(world, "LIST")
    first = folders(app)
    assert folders(app) == first and count(world, "LIST") == n + 1     # within the TTL: no new login, no LIST
    app.clock.shift(timedelta(seconds=26))
    world.server.drop("LIST", user=w.ADA)
    assert folders(app) == first                                     # the refresh failed: the last list is served
    assert count(world, "LIST") == n + 2


# -- never stall the user --------------------------------------------------

def while_locked(fn):
    """Run fn in another thread while the IMAP gate is held, as a long mail operation would hold
    it. Returns (finished within 5 s, its result). One that waited for the lock finishes only
    after."""
    out = []
    with gate.held_elsewhere():
        t = threading.Thread(target=lambda: out.append(fn()), name="while-locked", daemon=True)
        t.start()
        t.join(5)
        finished = not t.is_alive()
    t.join(5)
    return finished, (out or [None])[0]


def test_hwf_prefetch_and_the_background_loops_yield_a_held_imap_lock(world, monkeypatch):
    import rules
    import server
    rules.set_expiry("loom.example.test", 1)
    pk = world.pk("bea-inbox-01-client.eml")                    # its body was never fetched
    n = len(world.server.commands)

    def background():
        got = {"prefetch": server.message_body(pk, False, prefetch=True)}
        server._auto_reconcile_one(acct())
        server._auto_expire_one(acct())
        got["backfill"] = server.backfill_round()               # yielded, its backlog left for the next round
        return got
    finished, got = while_locked(background)
    assert finished and got == {"prefetch": {"ok": False, "busy": True}, "backfill": 8}
    assert world.server.commands[n:] == []                       # none of them touched the server


# -- connections -----------------------------------------------------------

def test_hwf_a_pooled_connection_is_checked_with_noop_and_dropped_after_an_error(world, monkeypatch):
    import pool
    monkeypatch.setenv("MAILMIND_POOL", w.ADA)
    monkeypatch.setattr(pool, "_slots", {})
    a, logins = acct(), count(world, "LOGIN")
    try:
        with pool.borrow(a) as first:
            first.select('"INBOX"', readonly=True)
        with pool.borrow(a) as again:
            assert again is first
        assert (count(world, "LOGIN"), count(world, "NOOP")) == (logins + 1, 1)    # reused after a NOOP
        with pytest.raises(RuntimeError):
            with pool.borrow(a):
                raise RuntimeError("a read went wrong")
        with pool.borrow(a) as fresh:
            assert fresh is not first                            # the slot was poisoned: a new login
        assert count(world, "LOGIN") == logins + 2
        world.server.drop("NOOP", user=w.ADA)
        with pool.borrow(a) as after_drop:
            assert after_drop is not fresh                       # failed its NOOP: replaced
        assert count(world, "LOGIN") == logins + 3
    finally:
        pool.close_all()


def test_hwf_the_idle_worker_leaves_idle_before_it_asks_for_a_sync(world, idle):
    import realtime
    idle()
    assert wait_until(lambda: count(world, "IDLE") == 1)
    n = len(world.server.commands)
    world.server.deliver(w.ADA, "INBOX", late())
    assert wait_until(lambda: count(world, "IDLE") == 2)          # DONE sent, then idling again
    assert realtime._dirty == {w.ADA} and realtime._kick.is_set()
    assert [c["command"] for c in world.server.commands[n:] if c["user"] == w.ADA] == ["IDLE"]   # no sync on it


def test_hwf_stopping_idle_leaves_closing_each_socket_to_its_worker(world, idle, monkeypatch):
    # stop() wakes a worker blocked in IDLE by shutting its socket down. Closing it from the stopping
    # thread instead freed the descriptor while the worker could still be about to wait on it, so
    # the number could pass to the next socket opened and the worker would wait on (and read) that.
    import accounts
    import realtime
    closed_by = []
    real = accounts.connect

    def connect(acct, timeout=60):
        M = real(acct, timeout=timeout)
        close = M.sock.close

        def spy():
            closed_by.append(threading.current_thread().name)
            close()
        M.sock.close = spy
        return M
    monkeypatch.setattr(accounts, "connect", connect)
    worker = idle()
    assert wait_until(lambda: count(world, "IDLE") == 1)
    realtime.stop()
    worker.join(5)
    assert not worker.is_alive()
    assert closed_by and set(closed_by) == {"idle-worker"}


# -- mute ------------------------------------------------------------------

def test_hwf_a_reply_in_a_muted_thread_is_marked_read_here_only(app, world):
    # Mute is local by design (store.muted_thread_seen and apply_mute_on_ingest are pure SQL), so a
    # reply that lands in a muted thread is read in MailMind and still unread on the server.
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    reply = w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<late-mute@example.test>",
                      In_Reply_To=lunch["message_id"])
    uid = world.deliver(w.ADA, "INBOX", reply)
    [r] = q("SELECT seen, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert (r["seen"], r["thread_id"]) == (1, lunch["thread_id"])
    assert w.SEEN not in world.server.messages(w.ADA, "INBOX")[uid].flags


# -- known bugs ------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="known bug: undoing an offline trash while still offline loses the message")
def test_hwf_an_offline_trash_undone_while_offline_comes_back(app, world):
    import ingest
    name = "ada-inbox-09-rfc2047-folded.eml"
    queue(app, world, op="trash", name=name)
    world.server.drop("LOGIN", user=w.ADA, times=None)           # still unreachable when Undo is pressed
    app.post("/api/undo", {})
    world.server.clear_faults()
    app.post("/api/undo", {})                                    # back online: Undo again, then a sync
    ingest.sync_new(acct())
    assert boxes(world, name) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])


def test_hwf_a_queued_move_for_a_removed_account_is_given_up(app, world):
    import config
    import readmutq
    queue(app, world)
    assert config.remove_account(w.ADA)
    readmutq.drain()
    assert queued()[0]["state"] == "error"


class _StoppableDrain:
    """The outbox worker's drain: drains as before until stop(), then ends the worker's loop."""

    def __init__(self, drain):
        self.drain, self.stopping = drain, False

    def __call__(self):
        if self.stopping:
            raise _Stop()
        return self.drain()

    def stop(self):
        import outbox
        self.stopping = True
        outbox.kick()


@pytest.mark.xfail(strict=True, reason="known bug: a send waits for the worker's 30 s poll, not the end of its window")
def test_hwf_a_send_goes_out_when_its_undo_window_ends(world, monkeypatch):
    import outbox
    import server
    monkeypatch.setattr(outbox, "SEND_DELAY", 1)                 # a one-second Undo Send window, in real time
    drain = _StoppableDrain(outbox.drain)
    monkeypatch.setattr(outbox, "drain", drain)

    def worker():
        try:
            outbox.worker_loop()
        except _Stop:
            pass
    t = threading.Thread(target=worker, name="outbox-worker", daemon=True)
    t.start()
    try:
        assert server.do_send(new(idempotency_key="k-soon"))[1] == 200
        wait_until(lambda: world.smtp.messages, timeout=1.3)     # due within a second; the worker polls every 30
        assert world.smtp.messages
    finally:
        drain.stop()
        t.join(5)
    assert not t.is_alive()


@pytest.mark.xfail(strict=True, reason="known bug: Retry keeps the attempt count, so one more failure gives up")
def test_hwf_a_manual_retry_gets_a_fresh_set_of_attempts(app, world):
    import outbox
    world.smtp.fail("RCPT", "451 4.3.0 try again later", times=None)
    oid = app.post("/api/send", new(idempotency_key="k-retry")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    for _ in range(outbox.MAX_ATTEMPTS):
        outbox.drain()
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP + 1))
    assert outbox_row(oid)["state"] == "error"
    assert app.post("/api/outbox/retry", {"id": oid}).json["ok"]
    outbox.drain()                                               # the retry fails once more
    r = outbox_row(oid)
    assert (r["state"], r["attempts"]) == ("queued", 1)


def test_hwf_junk_goes_to_the_special_use_junk_folder(app, world):
    from fakes.imap_server import Mailbox
    a = world.server.accounts[w.ADA]
    with world.server.lock:                                      # listed first, as a server may list it
        a.mailboxes = {"Bulk orders": Mailbox("Bulk orders", uidvalidity=1_700_000_999), **a.mailboxes}
    name = "ada-inbox-12-scam.eml"
    assert app.post("/api/junk", {"pk": world.pk(name)}).json["ok"]
    assert boxes(world, name)[0] == [(w.ADA, "Junk")]


@pytest.mark.xfail(strict=True, reason="known bug: backfill retries the same 12 bodies forever and never reaches older mail")
def test_hwf_backfill_moves_past_bodies_it_cannot_fetch(world, monkeypatch):
    import server
    monkeypatch.setattr(server, "time", _Sleeps(stop_at=float("inf")))   # no real pacing between fetches
    waiting = q("SELECT pk, account, folder, uid FROM messages WHERE body_text IS NULL ORDER BY ts DESC, pk DESC")
    stuck, older = waiting[:12], [r["pk"] for r in waiting[12:]]
    with world.server.lock:                                      # deleted elsewhere, not yet pruned here
        for r in stuck:
            world.server.mailbox(r["account"], r["folder"]).messages.pop(r["uid"])
    for _ in range(2):                                           # two backfill rounds
        server.backfill_round()
    fetched = q("SELECT pk FROM messages WHERE body_text IS NOT NULL")
    assert {r["pk"] for r in fetched} & set(older)


@pytest.mark.xfail(strict=True, reason="known bug: no route passes force, so an INBOX emptied elsewhere is never pruned")
def test_hwf_an_inbox_emptied_elsewhere_can_be_pruned(app, world):
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages.clear()
    app.post("/api/reconcile", {"account": w.ADA, "force": True})
    assert stored() == {}


@pytest.mark.xfail(strict=True, reason="known bug: sync never re-reads flags, so mail read elsewhere stays unread")
def test_hwf_mail_read_in_another_client_shows_read_after_a_sync(app, world):
    import ingest
    name = "ada-inbox-02-lunch-confirm.eml"
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid(name)].flags.add(w.SEEN)
    ingest.sync_new(acct())
    app.post("/api/reconcile", {"account": w.ADA})
    assert world.row(name)["seen"] == 1


@pytest.mark.xfail(strict=True, reason="known bug: only INBOX is reconciled, so mail gone from other folders lingers")
def test_hwf_mail_deleted_from_another_folder_leaves_the_list(app, world):
    import ingest
    name = "ada-archive-01-old-thread.eml"
    gone(world, name)
    app.post("/api/reconcile", {"account": w.ADA})
    ingest.sync_folder(acct(), "Archive")
    assert world.where(name)["db"] == []


@pytest.mark.xfail(strict=True, reason="known bug: a first folder sync that fails still marks the folder synced")
def test_hwf_a_folder_whose_first_sync_failed_is_synced_on_the_next_open(app, world):
    world.server.deliver(w.ADA, "Junk", late())
    world.server.fail("UID SEARCH", user=w.ADA)
    app.get(f"/api/messages?account={w.ADA}&folder=Junk")
    assert len(app.get(f"/api/messages?account={w.ADA}&folder=Junk").json["items"]) == 1


@pytest.mark.xfail(strict=True, reason="known bug: a folder warmed first only ever holds its newest WARM_RECENT messages")
def test_hwf_opening_a_warmed_folder_shows_its_older_mail(app, world):
    import ingest
    for n in range(3):
        world.server.deliver(w.ADA, "Junk", late(n))
    ingest.sync_folder(acct(), "Junk", recent=2)                 # warm.warm_tick's first touch, WARM_RECENT=2
    assert len(app.get(f"/api/messages?account={w.ADA}&folder=Junk").json["items"]) == 3


def test_hwf_an_idle_worker_stops_watching_a_removed_account(world, idle, monkeypatch):
    import config
    import realtime
    monkeypatch.setattr(realtime, "IDLE_SECONDS", 0.1)
    idle()
    assert wait_until(lambda: count(world, "IDLE") >= 1)
    assert config.remove_account(w.ADA)
    time.sleep(0.3)                                              # a few IDLE cycles to notice
    n = len(world.server.commands_for(w.ADA))
    time.sleep(0.25)
    assert len(world.server.commands_for(w.ADA)) == n


# -- bugs found while writing these ----------------------------------------

@pytest.mark.xfail(strict=True, reason="known bug: a Gmail Undo the server refuses is reported as done")
def test_hwf_a_refused_gmail_unarchive_is_not_reported_as_undone(app, world):
    name = "cy-inbox-03-unrelated.eml"
    assert app.post("/api/archive", {"pk": world.pk(name)}).json["ok"]
    world.server.fail("UID STORE", user=w.CY)
    assert app.post("/api/undo", {}).json["ok"] is False


