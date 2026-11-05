"""The HTTP layer's hard-won fixes, one test each, so a rewrite of server.py can't drop one quietly:
the IMAP lock around mail moves and the places that must never wait on it, the offline queue's
undo data, undo that fails and keeps the action, the snooze presets' edges, the delta's clamped
window, draft-ahead's fingerprint and presence rules, the folder rail's cache, cleanup's
unsubscribe lookup, and the background loops that yield or stay off.

Strict xfails at the end state the correct behaviour for bugs still present; fixing one makes it
pass, and strict makes that fail until the mark is removed. Behaviour already pinned elsewhere
(tests/test_http_*.py and their goldens, test_draft_resume.py, test_hardening.py...) isn't
repeated here.
"""
import email
import email.policy
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import gate
from fixtures import world as w
from mailmind.platform.workers import gates

RECEIPT = "ada-inbox-05-receipt.eml"
FOLDED = "ada-inbox-09-rfc2047-folded.eml"
LUNCH_CONFIRM = "ada-inbox-02-lunch-confirm.eml"
VENUE_END = "cy-inbox-02-thread-end.eml"
VENUE_START = "cy-inbox-01-thread-start.eml"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
LOOM = "news@loom.example.test"
UTC = timezone.utc


def rows(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def pk_of(message_id, account=None):
    sql = "SELECT pk FROM messages WHERE message_id=?" + (" AND account=?" if account else "")
    [r] = rows(sql, message_id, *([account] if account else []))
    return r["pk"]


def summary(pk, importance, category, action=""):
    import store
    c = store.conn()
    try:
        store.save_summary(c, pk, "stub", importance, category, action, "test", "2026-11-02T12:00:00+00:00")
        c.commit()
    finally:
        c.close()


def lock_spy(world, command, user=None):
    """Whether the app held the account's IMAP gate each time the fake IMAP server received
    `command`. The predicate never matches, so the command runs as usual."""
    held = []

    def look(args):
        held.append(gates.held(user or gates.EVERY_ACCOUNT))
        return False
    world.server.fail(command, when=look, times=None, user=user)
    return held


def server_boxes(world, name):
    return sorted((s[0], s[1]) for s in world.where(name)["server"])


# -- the IMAP lock: held for mail moves, never waited on where it would freeze the app -----

def test_hwf_trash_moves_and_queues_under_the_imap_lock(app, world, monkeypatch):
    import store
    held = lock_spy(world, "UID MOVE", user=w.ADA)
    assert app.post("/api/trash", {"pk": world.pk(RECEIPT)}).json["ok"]
    assert held == [True]
    queued, insert = [], store.read_mut_insert

    def spy(c, row):
        queued.append(gates.held(w.ADA))
        return insert(c, row)
    monkeypatch.setattr(store, "read_mut_insert", spy)
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/trash", {"pk": world.pk(FOLDED)}).json
    assert (r["status"], queued) == ("queued", [True])


def test_hwf_testing_an_account_never_waits_on_the_imap_lock(app, world):
    held = lock_spy(world, "LOGIN", user=w.ADA)
    r = app.post("/api/accounts/test", {"email": w.ADA}).json
    assert (r["ok"], r["connect"], held) == (True, True, [False])


@pytest.mark.parametrize("credential", [
    {"style": "env", "var": "MAILMIND_TEST_DEE_PASSWORD"},
    {"style": "proton_bridge"},
    {"style": "op", "ref": "op://Private/Dee/password"}], ids=["env", "proton_bridge", "op"])
def test_hwf_testing_a_draft_never_sends_a_stored_secret(app, world, monkeypatch, credential):
    monkeypatch.setenv("MAILMIND_TEST_DEE_PASSWORD", "dee-stored-secret")
    monkeypatch.setenv("MAILMIND_PROTON_BRIDGE_PASS", "dee-stored-secret")
    world.server.add_account("dee@example.test", password="dee-stored-secret")
    draft = {"email": "dee@example.test", "type": "imap", "host": "127.0.0.1", "port": world.server.port,
             "starttls": True, "credential": credential}
    r = app.post("/api/accounts/test", {"draft": draft}).json
    assert r == {"ok": False, "error": "save the account to test a stored secret; "
                                       "before then only a typed password is tried"}
    assert world.server.commands_for(user="dee@example.test", command="LOGIN") == []


def test_hwf_background_reconcile_and_expiry_yield_to_a_user_op(app, world):
    import accounts
    import rules
    import server
    rules.set_expiry("nobody.example.test", 72)              # so expiry has work that needs IMAP
    ada = accounts.get(w.ADA)
    since = len(world.server.commands)
    with gate.held_elsewhere():                              # a user op holds the lock
        server._auto_reconcile_one(ada)
        server._auto_expire_one(ada)
    assert world.server.commands[since:] == []               # both skipped this cycle
    server._auto_reconcile_one(ada)
    server._auto_expire_one(ada)
    assert len(world.server.commands_for(w.ADA, "LOGIN")) >= 2   # and run once the lock is free


def test_hwf_reconcile_takes_the_gate_once_for_each_folder(app, world, monkeypatch):
    import accounts
    import ingest
    import server
    real, jobs = server.gates.run, []

    def counted(account, fn, **kw):
        jobs.append(account)
        return real(account, fn, **kw)
    monkeypatch.setattr(server.gates, "run", counted)
    server._auto_reconcile_one(accounts.get(w.ADA))              # never holds the gate across folders
    assert len(ingest.reconciled_folders(w.ADA)) > 1
    assert jobs == [w.ADA] * len(ingest.reconciled_folders(w.ADA))


def test_hwf_a_prefetch_never_waits_for_the_imap_lock(app, world):
    pk = world.pk("bea-inbox-01-client.eml")
    fetches = len(world.server.commands_for(w.BEA, "UID FETCH"))
    with gate.held_elsewhere():
        r = app.get(f"/api/message/{pk}/body?prefetch=1").json
    assert r == {"ok": False, "busy": True}
    assert len(world.server.commands_for(w.BEA, "UID FETCH")) == fetches
    assert app.get(f"/api/message/{pk}/body?prefetch=1").json == {"ok": True, "prefetched": True}
    with gate.held_elsewhere():                              # already warm: no lock needed
        assert app.get(f"/api/message/{pk}/body?prefetch=1").json == {"ok": True, "prefetched": True}


def test_hwf_smtp_delivery_holds_no_imap_lock(app, world):
    import outbox
    held, filed = [], world.smtp.on_message

    def look(delivery):
        held.append(gates.held(w.ADA))
        if filed:
            filed(delivery)
    world.smtp.on_message = look
    r = app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Linen",
                               "text": "Half each?", "idempotency_key": "k-lockfree"}).json
    assert r["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert held == [False]


def test_hwf_the_workers_hold_the_imap_lock_for_their_mail_commands(app, world):
    """The outbox's Sent copy and \\Answered flag, a queued move and a thread summary's body fetch
    reach IMAP from worker threads, and each holds the lock as a request does."""
    import outbox
    import readmutq
    import summarize_thread
    appends, stores = lock_spy(world, "APPEND", user=w.ADA), lock_spy(world, "UID STORE", user=w.ADA)
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": world.pk("ada-inbox-02-lunch-confirm.eml"),
                               "to": ["bea@example.test"], "subject": "Re: Lunch", "text": "See you there.",
                               "idempotency_key": "k-gated"}).json
    assert r["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert (appends, stores) == ([True], [True])

    moves = lock_spy(world, "UID MOVE", user=w.ADA)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["status"] == "queued"
    readmutq.drain()
    assert moves == [True]

    pk = world.pk("bea-inbox-01-client.eml")                  # its body was never fetched
    fetches = lock_spy(world, "UID FETCH", user=w.BEA)
    summarize_thread._gather([{"pk": pk, "account": w.BEA, "body_text": None, "snippet": "", "body_fetched": 0,
                               "from_name": "Bea", "from_addr": w.BEA, "date_iso": ""}])
    assert fetches == [True]


# -- the offline queue and undo --------------------------------------------

def test_hwf_a_trash_queued_offline_knows_its_trash_folder_and_undoes_after_it_ships(app, world):
    import readmutq
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/trash", {"pk": world.pk(RECEIPT)}).json
    assert (r["status"], r["undoable"]) == ("queued", True)
    assert [x["dest"] for x in rows("SELECT dest FROM read_mutations")] == ["Trash"]   # known before any IMAP
    readmutq.drain()
    assert server_boxes(world, RECEIPT) == [(w.ADA, "Trash")]
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, RECEIPT) == [(w.ADA, "INBOX")]
    assert [(d["folder"]) for d in world.where(RECEIPT)["db"]] == ["INBOX"]


def test_hwf_a_failed_undo_stays_on_the_stack_for_a_retry(app, world):
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    world.server.fail("UID SEARCH", user=w.ADA)              # the server can't find it this once
    r = app.post("/api/undo", {}).json
    assert r["ok"] is False and server_boxes(world, RECEIPT) == [(w.ADA, "Archive")]
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, RECEIPT) == [(w.ADA, "INBOX")]


def test_hwf_undo_keeps_the_last_25_actions(app, world):
    pk = world.pk(RECEIPT)
    for _ in range(26):
        assert app.post("/api/snooze", {"pk": pk, "preset": "tomorrow"}).json["ok"]
    answers = [app.post("/api/undo", {}).json for _ in range(26)]
    assert [a["ok"] for a in answers] == [True] * 25 + [False]
    assert answers[-1] == {"ok": False, "empty": True}


def test_marking_read_while_the_server_is_away_is_queued(app, world):
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/seen", {"pk": world.pk(LUNCH_CONFIRM), "seen": True})
    assert (r.status, r.json.get("status"), world.row(LUNCH_CONFIRM)["seen"]) == (200, "queued", 1)


def archived_pair(app, world):
    for name in (RECEIPT, FOLDED):
        assert app.post("/api/archive", {"pk": world.pk(name), "undo_group": "g1"}).json["ok"]


def test_a_partly_failed_group_undo_keeps_what_it_couldnt_reverse(app, world):
    archived_pair(app, world)
    world.server.fail("UID SEARCH", user=w.ADA)              # the first child reversed (FOLDED) isn't found
    r = app.post("/api/undo", {}).json
    assert (r["ok"], r["reversed"], r["failed"]) == (True, 1, 1)
    assert app.post("/api/undo", {}).json["ok"]
    assert server_boxes(world, FOLDED) == [(w.ADA, "INBOX")]


def test_a_refusal_partway_through_a_group_undo_still_reports_what_it_reversed(app, world):
    archived_pair(app, world)
    [uid] = world.server.find(w.ADA, "Archive", w.message_id(RECEIPT))
    for cmd in ("UID MOVE", "UID COPY"):                     # RECEIPT, reversed second, can't move back
        world.server.fail(cmd, when=lambda args, uid=uid: str(args[0]) == str(uid), user=w.ADA)
    r = app.post("/api/undo", {}).json
    assert server_boxes(world, FOLDED) == [(w.ADA, "INBOX")]
    assert (r["ok"], r.get("reversed"), r.get("failed")) == (True, 1, 1)


def test_a_group_undo_that_reverses_nothing_keeps_the_whole_group(app, world):
    archived_pair(app, world)
    world.server.drop("LOGIN", user=w.ADA, times=2)          # neither member can reach the server
    r = app.post("/api/undo", {}).json
    assert r["ok"] is False
    r = app.post("/api/undo", {}).json
    assert (r["ok"], r["reversed"], r["failed"]) == (True, 2, 0)
    assert server_boxes(world, RECEIPT) == server_boxes(world, FOLDED) == [(w.ADA, "INBOX")]


# -- snooze presets at the week's edges ------------------------------------

@pytest.mark.parametrize("now, preset, wake", [
    (datetime(2026, 11, 7, 10, 0, tzinfo=UTC), "weekend", datetime(2026, 11, 14, 8, 0, tzinfo=UTC)),   # Saturday
    (datetime(2026, 11, 8, 10, 0, tzinfo=UTC), "weekend", datetime(2026, 11, 14, 8, 0, tzinfo=UTC)),   # Sunday
    (datetime(2026, 11, 8, 10, 0, tzinfo=UTC), "next_week", datetime(2026, 11, 9, 8, 0, tzinfo=UTC)),  # Sunday
])
def test_hwf_weekend_and_next_week_from_the_weekend(app, world, now, preset, wake):
    app.clock.move_to(now)
    r = app.post("/api/snooze", {"pk": world.pk(RECEIPT), "preset": preset}).json
    assert datetime.fromtimestamp(r["until"], UTC) == wake


# -- AI: the delta's window, draft-ahead, the semaphore ---------------------

def test_hwf_since_is_held_between_a_day_ago_and_now(app, world, monkeypatch):
    import summarize_briefing
    seen = []
    monkeypatch.setattr(summarize_briefing, "build_delta",
                        lambda accts, since, generate=True: (seen.append(since), ({"ok": True}, 200))[1])
    app.clock.shift(timedelta(minutes=7))                    # 12:07
    now = int(time.time())
    for since in (0, now - 3600, now + 3600, "soon"):
        app.get(f"/api/briefing/since?since={since}")
        app.post("/api/briefing/since", {"since": since})
    day_ago = int(datetime(2026, 11, 1, 12, 0, tzinfo=UTC).timestamp())     # 12:07 a day back, to 10 minutes
    assert seen == [day_ago, day_ago, now - 3600, now - 3600, now, now, day_ago, day_ago]


def triaged_venue(world):
    """Mae's last word in Cy's venue thread, called worth a reply; returns its pk."""
    pk = world.pk(VENUE_END)
    summary(pk, 5, "action-needed", "Confirm the date")
    return pk


def draft_ahead(app, pk, peek=False):
    return app.get(f"/api/ai/draft-ahead?pk={pk}" + ("&peek=1" if peek else "")).json["status"]


@pytest.mark.parametrize("reply_warm", [False, True], ids=["drafting-fetches-a-body", "only-the-open-fetches"])
def test_hwf_opening_a_message_keeps_its_threads_draft_ready(app, world, ai, reply_warm):
    # Cy's sent reply has no snippet, so writing the draft fetches its body. Warmed first, the
    # draft fetches nothing and opening the thread's first message is the only change after it.
    pk = triaged_venue(world)
    reply = world.pk("cy-sent-01-thread-reply.eml")
    if reply_warm:
        assert app.get(f"/api/message/{reply}/body").json["ok"]
    assert world.row("cy-sent-01-thread-reply.eml")["body_fetched"] == int(reply_warm)
    assert draft_ahead(app, pk) == "generating"
    app.settle()
    assert draft_ahead(app, pk) == "ready"
    start = world.pk(VENUE_START)
    assert world.row(VENUE_START)["body_fetched"] == 0
    assert app.get(f"/api/message/{start}/body").json["ok"]
    assert world.row(VENUE_START)["body_fetched"] == 1
    assert draft_ahead(app, pk) == "ready"


def test_hwf_polling_routes_dont_count_as_someone_at_the_screen(app, world, monkeypatch):
    from mailmind.bootstrap import assistant
    from mailmind.platform import sse
    governor = assistant.governor()
    monkeypatch.setattr(sse, "HEARTBEAT", 0.1)
    monkeypatch.setattr(governor, "_last_seen", 0.0)
    for path in (f"/api/folders?account={w.ADA}", "/api/outbox", "/api/stats",
                 f"/api/ai/draft-ahead?pk={world.pk(VENUE_END)}"):
        assert app.get(path).status == 200
    s = socket.create_connection(("127.0.0.1", app.port), timeout=10)     # a hidden tab's event stream
    s.sendall(f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nSec-Fetch-Site: same-origin\r\n\r\n"
              .encode())
    assert b" 200 " in s.recv(64)
    s.close()
    deadline = time.monotonic() + 5
    while sse.HUB.listening() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert governor._last_seen == 0.0
    app.get("/api/digest")
    assert governor._last_seen == time.time()


def test_hwf_background_ai_waits_while_an_interactive_call_runs():
    from mailmind.assistant.application.governor import CostGovernor
    governor = CostGovernor(spent=lambda: 0, limit=lambda: 1)
    assert governor.held_back("background", "triage", "m", None) is None
    with governor.call("interactive", "rewrite", "m", None):              # one call in flight
        assert governor.held_back("background", "triage", "m", None) == "yielding"


# -- the folder rail's cache -----------------------------------------------

def test_hwf_a_burst_of_folder_requests_lists_the_folders_once(app, world):
    import threading
    answers = []
    clients = [threading.Thread(target=lambda: answers.append(app.get(f"/api/folders?account={w.ADA}").status))
               for _ in range(4)]
    with gate.held_elsewhere():                              # every request queues on the cold cache
        for t in clients:
            t.start()
        time.sleep(0.3)
    for t in clients:
        t.join(30)
    assert answers == [200] * 4
    assert len(world.server.commands_for(w.ADA, "LIST")) == 1


# -- cleanup's unsubscribe and sweep ---------------------------------------

def test_hwf_unsubscribe_comes_from_the_newest_message_with_a_stored_body(app, world):
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID="<issue-219@loom.example.test>",
                                              Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              List_Unsubscribe="<mailto:stop-219@loom.example.test>"), (w.SEEN,))
    app.get(f"/api/message/{world.pk(NEWSLETTER)}/body")    # only the older issue's body is stored
    unsub = app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]
    assert unsub["method"] != "none" and unsub.get("mailto_to") != "stop-219@loom.example.test"
    app.get(f"/api/message/{pk_of('<issue-219@loom.example.test>')}/body")
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"] == {
        "method": "mailto", "mailto_to": "stop-219@loom.example.test"}


def test_hwf_the_cleanup_preview_says_when_the_sweep_is_capped(app, world, monkeypatch):
    import server
    for n, day in ((216, "14"), (217, "21")):
        world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID=f"<issue-{n}@loom.example.test>",
                                                Date=f"Wed, {day} Oct 2026 07:00:00 +0000"))
    monkeypatch.setattr(server, "_CLEANUP_SWEEP_CAP", 3)
    r = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    assert (r["sweep_count"], r["sweep_capped"]) == (3, False)
    monkeypatch.setattr(server, "_CLEANUP_SWEEP_CAP", 2)
    r = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    assert (r["sweep_count"], r["sweep_capped"]) == (2, True)


# -- send, drafts, digest, search ------------------------------------------

def test_hwf_a_forward_leaves_inline_pictures_behind(app, world):
    r = app.post("/api/send", {"account": w.ADA, "mode": "forward", "in_reply_to_pk": world.pk("ada-inbox-11-html-cid.eml"),
                               "to": ["noor@studio.example.test"], "subject": "Fwd: The finished quilt",
                               "text": "Look!", "idempotency_key": "k-fwd-cid"}).json
    assert r["ok"]
    [row] = rows("SELECT raw FROM outbox")
    msg = email.message_from_bytes(row["raw"], policy=email.policy.default)
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == []


def test_hwf_a_draft_going_out_now_cant_be_reopened(app, world):
    import accounts
    import ingest
    import store
    saved = app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"],
                                    "subject": "Held", "text": "Going out."}).json
    ingest.sync_folder(accounts.get(w.ADA), "Drafts")
    [d] = rows("SELECT pk FROM messages WHERE account=? AND folder='Drafts' AND uid=?", w.ADA, saved["uid"])
    c = store.conn()
    oid, _ = store.outbox_insert(c, {"account": w.ADA, "idempotency_key": "k-held", "message_id": "<held@example.test>",
                                     "in_reply_to_pk": None, "envelope": "[]", "recipients": "{}", "subject": "Held",
                                     "raw": b"x", "next_attempt_at": 0, "draft_id": saved["draft_id"],
                                     "created_at": "2026-11-02T12:00:00+00:00"})
    c.execute("UPDATE outbox SET state='sending' WHERE id=?", (oid,))
    c.commit()
    c.close()
    r = app.post("/api/draft/resume", {"pk": d["pk"]})
    assert r.status == 423


def test_hwf_send_accounts_come_from_config_before_any_sync(app, world):
    dee = "dee@example.test"
    entry = world.server.add_account(dee, password="dee-password")
    assert app.post("/api/accounts/add", {"email": dee, "type": "imap", "host": entry["host"], "port": entry["port"],
                                          "starttls": True, "credential": {"style": "plain", "value": "dee-password"}
                                          }).json["ok"]
    d = app.get("/api/digest").json
    assert dee in d["send_accounts"] and dee not in d["accounts"]


def test_hwf_a_search_the_index_rejects_answers_bad_query(app, world, monkeypatch):
    import search
    monkeypatch.setattr(search, "build", lambda q: ('"', [], []))   # a MATCH string FTS5 can't parse
    r = app.get("/api/search?q=lunch")
    assert (r.status, r.json) == (200, {"ok": False, "error": "bad query"})


# -- follow-ups -----------------------------------------------------------

def test_hwf_a_met_sent_follow_up_becomes_a_reply_nudge_in_the_same_tick(app, world):
    import server
    import store
    world.deliver(w.ADA, "INBOX", b"From: Lin Mori <lin@example.test>\r\nTo: Ada Park <ada@example.test>\r\n"
                                  b"Subject: Fabric swatches\r\nDate: Wed, 28 Oct 2026 09:00:00 +0000\r\n"
                                  b"Message-ID: <swatches@lin.example.test>\r\n\r\nWhich of these?\r\n", (w.SEEN,))
    pk = pk_of("<swatches@lin.example.test>")
    [m] = rows("SELECT thread_id, ts FROM messages WHERE pk=?", pk)
    summary(pk, 5, "action-needed")
    c = store.conn()
    store.followup_record_sent(c, w.ADA, m["thread_id"], pk, m["ts"] - 86400, "2026-10-27T09:00:00+00:00")
    c.commit()
    c.close()
    server.followups_tick()                                  # Lin answered: the sent one resolves first
    assert rows("SELECT kind, state FROM followups WHERE thread_id=?", m["thread_id"]) == [
        {"kind": "received", "state": "due"}]


# -- the server's own plumbing ---------------------------------------------

def test_hwf_a_browser_dropping_its_connection_prints_no_traceback(capsys):
    import server
    srv = object.__new__(server._Server)
    try:
        raise ConnectionResetError("reset by peer")
    except ConnectionResetError:
        srv.handle_error(None, ("127.0.0.1", 50000))
    assert capsys.readouterr().err == ""
    try:
        raise RuntimeError("a real fault")
    except RuntimeError:
        srv.handle_error(None, ("127.0.0.1", 50000))
    assert "RuntimeError: a real fault" in capsys.readouterr().err


# -- known bugs -----------------------------------------------------------

def test_a_refused_folder_list_keeps_the_last_list(app, world):
    first = app.get(f"/api/folders?account={w.ADA}").json["folders"]
    app.clock.shift(timedelta(minutes=1))
    world.server.fail("LIST", "NO [UNAVAILABLE] busy", user=w.ADA)
    assert app.get(f"/api/folders?account={w.ADA}").json["folders"] == first



@pytest.mark.parametrize("body", [b"[1, 2]", b'"pk"', b"7", b"null", b"{not json"])
def test_a_json_body_that_isnt_an_object_is_a_bad_request(app, body):
    r = app.post("/api/seen", body)
    assert (r.status, r.json) == (400, {"ok": False, "error": "bad request"})


def test_a_body_that_isnt_an_object_is_refused_by_the_guards_first(app):
    r = app.post("/api/seen", b"[1, 2]", headers={"X-CSRF-Token": None})
    assert r.status == 403
    assert app.post("/api/undo", b"").status == 200             # no body at all is still {}


@pytest.mark.xfail(strict=True, reason="known bug: \\Answered is set by pk, so archiving the original first skips it")
def test_a_reply_marks_its_original_answered_after_the_original_is_archived(app, world):
    import outbox
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": world.pk(LUNCH_CONFIRM),
                               "to": ["lin@example.test"], "subject": "Re: Lunch on Thursday?", "text": "See you.",
                               "idempotency_key": "k-reply-archive"}).json
    assert r["ok"]
    assert app.post("/api/archive", {"pk": world.pk(LUNCH_CONFIRM)}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [(_, box, _, flags)] = world.where(LUNCH_CONFIRM)["server"]
    assert (box, "\\Answered" in flags) == ("Archive", True)


def test_a_saved_draft_shows_in_an_open_drafts_list(app, world):
    app.get(f"/api/messages?account={w.ADA}&folder=Drafts")
    assert app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"],
                                   "subject": "Swatch order", "text": "Two of each."}).json["ok"]
    subjects = [i["subject"] for i in app.get(f"/api/messages?account={w.ADA}&folder=Drafts").json["items"]]
    assert "Swatch order" in subjects


def test_a_first_save_sent_twice_makes_one_draft(app, world):
    body = {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order",
            "text": "Two of each.", "idempotency_key": "cmp-1"}
    a, b = app.post("/api/draft", body).json, app.post("/api/draft", body).json
    assert a["draft_id"] == b["draft_id"]
    assert len(world.server.find(w.ADA, "Drafts", a["message_id"]) + world.server.find(
        w.ADA, "Drafts", b["message_id"])) == 1


def _swatch(key, **kw):
    return {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order",
            "text": "Two of each.", "idempotency_key": key, **kw}


def _draft_row(did):
    import store
    c = store.conn()
    try:
        return dict(store.draft_get(c, did))
    finally:
        c.close()


def test_saves_that_overlap_leave_one_copy_and_the_row_on_it(app, world, monkeypatch):
    # Both read the row they replace, so a read taken before the gate would expunge the UID the
    # first had already replaced, and leave the first's copy in Drafts with nothing tracking it.
    import server
    drafts = world.server.mailbox(w.ADA, "Drafts")
    before = len(drafts.messages)
    did = app.post("/api/draft", _swatch("cmp-2")).json["draft_id"]
    real, waiting = server.gates.run, []

    def run(scope, job, **kw):
        waiting.append(scope)
        return real(scope, job, **kw)
    monkeypatch.setattr(server.gates, "run", run)
    with gate.held_elsewhere(w.ADA):
        waiting.clear()
        saves = [threading.Thread(target=app.post, args=("/api/draft", _swatch("cmp-2", draft_id=did, text=text)))
                 for text in ("One of each.", "Three of each.")]
        for t in saves:
            t.start()
        end = time.monotonic() + 10
        while len(waiting) < 2 and time.monotonic() < end:     # both are at the gate
            threading.Event().wait(0.01)
        assert len(waiting) == 2
    for t in saves:
        t.join(10)
    assert len(drafts.messages) == before + 1
    assert _draft_row(did)["uid"] in drafts.messages


def test_a_send_that_never_heard_its_first_save_still_removes_the_copy(app, world):
    import outbox
    saved = app.post("/api/draft", _swatch("cmp-3")).json          # its answer is lost: no id in the composer
    assert world.server.find(w.ADA, "Drafts", saved["message_id"])
    assert app.post("/api/send", _swatch("cmp-3")).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert len(world.smtp.messages) == 1
    assert not world.server.find(w.ADA, "Drafts", saved["message_id"])


def test_a_first_save_retried_from_another_account_moves_the_copy_there(app, world):
    a = app.post("/api/draft", _swatch("cmp-4")).json
    b = app.post("/api/draft", _swatch("cmp-4", account=w.BEA)).json
    assert a["draft_id"] == b["draft_id"]
    assert not world.server.find(w.ADA, "Drafts", a["message_id"])
    assert world.server.find(w.BEA, "Drafts", b["message_id"])
    assert _draft_row(b["draft_id"])["account"] == w.BEA


def test_a_save_whose_row_moved_accounts_while_it_waited_takes_the_wider_gate(app, world, monkeypatch):
    # It asked for its own account's gate, and by its turn the row is in another account, whose
    # copy it must remove: it goes round again holding them all.
    import server
    a = app.post("/api/draft", _swatch("cmp-5")).json
    real, scopes = server.gates.run, []
    b = {}

    def run(scope, job, **kw):
        scopes.append(scope)
        if len(scopes) == 1:                                       # another tab's save gets in first
            b.update(app.post("/api/draft", _swatch("cmp-5", account=w.BEA, draft_id=a["draft_id"])).json)
        return real(scope, job, **kw)
    monkeypatch.setattr(server.gates, "run", run)
    r = app.post("/api/draft", _swatch("cmp-5", draft_id=a["draft_id"], text="Back from Ada.")).json
    assert r["ok"] and r["draft_id"] == a["draft_id"]
    assert scopes[0] == w.ADA and scopes[-1] == server.gates.EVERY_ACCOUNT
    assert not world.server.find(w.BEA, "Drafts", b["message_id"])
    assert world.server.find(w.ADA, "Drafts", r["message_id"])


def test_a_failed_rules_write_keeps_the_rules(app, world, monkeypatch):
    import rules
    assert app.post("/api/rules", {"token": "lin@example.test", "verdict": "keep"}).json["ok"]

    class DiskFull:
        load = staticmethod(json.load)

        @staticmethod
        def dump(obj, f, **kw):
            f.write('{"keep_senders": [')
            raise OSError("No space left on device")
    monkeypatch.setattr(rules, "json", DiskFull)
    assert app.post("/api/rules", {"token": "prizes.example.test", "verdict": "block"}).status == 500
    monkeypatch.setattr(rules, "json", json)
    r = app.get("/api/rules")
    assert r.status == 200
    assert r.json["rules"]["keep_senders"] == ["lin@example.test"]
