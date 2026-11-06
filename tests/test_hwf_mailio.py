"""Mail I/O behaviour a rewrite could silently lose: the hard-won fixes in ingest, the mail commands, the
offline queue, the outbox, drafts, sweep and expiry, the folder rail, the connection pool and the
IDLE worker, each pinned against the world's fake IMAP and SMTP servers. The fixes the older
suites already pin (test_sync_state, test_queue_drain, test_http_mail, test_http_send,
test_hardening, test_draft_resume, test_expire...) are not repeated here.

Most tests run on the frozen clock or none. Two need real time, briefly: the IDLE worker's, and
the outbox worker's, whose bug is how long it sleeps. The app's endless loops (backfill) are run
for a cycle or two with their sleeps stubbed out.
"""
import email
import email.policy
import threading
import time
from datetime import timedelta

import pytest

import gate
from fixtures import world as w
from mailmind.bootstrap import curation
from mailmind.compose.domain import outbox_item
from mailmind.bootstrap.compose import book
from mailmind.bootstrap.compose import sending

RECEIPT = "ada-inbox-05-receipt.eml"
JUNK_INBOX = ("ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml")


def acct(email=w.ADA):
    from mailmind.bootstrap import accounts
    return accounts.get(email)


def q(sql, *args):
    from mailmind.bootstrap.mailbox import store
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
    from mailmind.bootstrap.mailbox import store
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


def test_hwf_a_folded_message_id_never_carries_its_line_break_to_imap(app, world):
    # A sender can fold the Message-ID header, and the folded value is stored as it came, line break
    # and all. Undo finds a message by it, and imaplib sends an argument as it is: the break would
    # end the SEARCH and start whatever followed as a command of its own.
    uid = world.deliver(w.ADA, "INBOX", late(Message_ID="<fold@example.test>\r\n A1 DELETE INBOX"))
    [row] = q("SELECT pk, message_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert "\r\n" in row["message_id"]
    since = len(world.server.commands)
    assert app.post("/api/archive", {"pk": row["pk"]}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"] is False             # it can't be found by that ID
    lines = [c["line"] for c in world.server.commands[since:]]
    assert not [line for line in lines if "DELETE" in line or "fold@" in line], lines


def test_hwf_a_header_in_an_unknown_charset_decodes_as_utf8(world):
    import base64
    word = "=?x-unknown?B?" + base64.b64encode("Caf\u00e9 menu".encode()).decode() + "?="
    uid = world.deliver(w.ADA, "INBOX", late(Subject=word))
    [r] = q("SELECT subject FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert r["subject"] == "Caf\u00e9 menu"


def test_hwf_a_folder_name_in_a_request_cant_forge_a_log_line(app, world, capsys):
    app.get(f"/api/messages?account={w.ADA}&folder=Gone%0A%5Bsync%5D%20forged&limit=5")
    out = []

    def logged():
        out.append(capsys.readouterr().out)
        return "[folder-sync]" in "".join(out)
    assert wait_until(logged)
    assert not any(line.startswith("[sync] forged") for line in "".join(out).splitlines())


def test_hwf_a_folded_subject_and_sender_are_stored_on_one_line(world):
    uid = world.deliver(w.ADA, "INBOX", b'From: "Bo\r\n Lin" <bo@example.test>\r\nTo: ada@example.test\r\n'
                                        b'Subject: Items\r\n for next week\r\nMessage-ID: <fold-1@example.test>\r\n'
                                        b'Date: Mon, 02 Nov 2026 10:00:00 +0000\r\n\r\nHello\r\n')
    [r] = q("SELECT subject, from_name, from_addr FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
            w.ADA, uid)
    assert r == {"subject": "Items for next week", "from_name": "Bo Lin", "from_addr": "bo@example.test"}


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
    from mailmind.bootstrap.mailbox import store
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


def test_hwf_mail_read_in_another_client_shows_read_after_a_sync(app, world):
    import ingest
    name = "ada-inbox-02-lunch-confirm.eml"
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid(name)].flags.add(w.SEEN)
    ingest.sync_new(acct())
    assert world.row(name)["seen"] == 1


def test_hwf_a_sent_copy_flagged_deleted_doesnt_count_as_filed(world):
    from mailmind.bootstrap import mailbox
    raw = w.raw("ada-sent-01-lunch-reply.eml")
    mid = w.message_id("ada-sent-01-lunch-reply.eml")
    with world.server.lock:
        box = world.server.mailbox(w.ADA, "Sent")
        box.messages[world.uid("ada-sent-01-lunch-reply.eml")].flags.add("\\Deleted")
    assert mailbox.copies.append_to_sent(acct(), raw, mid) == "Sent"
    with world.server.lock:
        flags = [m.flags for m in box.messages.values()]
    assert sorted("\\Deleted" in f for f in flags) == [False, True]


def modseq(folder="INBOX", email=w.ADA):
    return q("SELECT highest_modseq FROM sync_state WHERE account=? AND folder=?", email, folder)[0]["highest_modseq"]


def flag_fetches(world, since, email=w.ADA):
    """The UID FETCHes of flags alone sent since `since`, as their arguments."""
    return [c["args"] for c in world.server.commands[since:]
            if c["user"] == email and c["command"] == "UID FETCH" and c["args"][1] == ["FLAGS"]]


def elsewhere(world, name, folder="INBOX", email=w.ADA):
    with world.server.lock:
        world.server.mailbox(email, folder).messages[world.uid(name)].flags.add("\\Flagged")


def test_hwf_a_sync_with_nothing_changed_reads_no_flags(world):
    import ingest
    assert modseq()                                   # the first sync, a whole scan, left the mark
    since = len(world.server.commands)
    ingest.sync_new(acct())
    assert flag_fetches(world, since) == []


def test_hwf_a_sync_asks_only_for_what_changed_since_its_mark(world):
    import ingest
    name, mark = "ada-inbox-02-lunch-confirm.eml", modseq()
    elsewhere(world, name)
    since = len(world.server.commands)
    ingest.sync_new(acct())
    [fetch] = flag_fetches(world, since)
    assert fetch == [str(world.uid(name)), ["FLAGS"]]
    assert world.row(name)["flagged"] == 1
    assert modseq() > mark


def test_hwf_a_change_to_mail_older_than_the_newest_is_seen_too(world, monkeypatch):
    import ingest
    monkeypatch.setattr(ingest, "FLAG_REFRESH", 1)
    oldest = min(((world.row(n)["uid"], n) for n, acct_, folder, *_ in w.MAIL if acct_ == w.ADA and folder == "INBOX"))[1]
    elsewhere(world, oldest)
    ingest.sync_new(acct())
    assert world.row(oldest)["flagged"] == 1


def test_hwf_a_server_with_no_condstore_has_its_newest_read_each_time(world):
    import ingest
    world.server.without.add("CONDSTORE")
    name = "ada-inbox-02-lunch-confirm.eml"
    elsewhere(world, name)
    since = len(world.server.commands)
    ingest.sync_new(acct())
    [fetch] = flag_fetches(world, since)
    assert str(world.uid(name)) in str(fetch[0]).split(",") and "," in str(fetch[0])    # all the newest
    assert world.row(name)["flagged"] == 1


def test_hwf_a_refused_modseq_search_falls_back_to_the_newest(world):
    import ingest
    name, mark = "ada-inbox-02-lunch-confirm.eml", modseq()
    elsewhere(world, name)
    world.server.fail("UID SEARCH", "BAD [scripted] no such key", user=w.ADA,
                      when=lambda a: str(a[0]).upper() == "MODSEQ")
    ingest.sync_new(acct())
    assert world.row(name)["flagged"] == 1
    assert modseq() == mark                           # what's older than the newest is still to ask


def test_hwf_a_refused_flag_read_leaves_the_mark_so_the_next_sync_asks_again(world):
    import ingest
    name, mark = "ada-inbox-02-lunch-confirm.eml", modseq()
    elsewhere(world, name)
    world.server.fail("UID SEARCH", user=w.ADA, when=lambda a: str(a[0]).upper() == "MODSEQ")
    world.server.fail("UID FETCH", user=w.ADA, when=lambda a: a[1] == ["FLAGS"])
    ingest.sync_new(acct())
    assert (world.row(name)["flagged"], modseq()) == (0, mark)
    ingest.sync_new(acct())
    assert world.row(name)["flagged"] == 1


def test_hwf_a_click_while_the_flags_are_read_keeps_its_own_when_the_server_said_what_changed(world, monkeypatch):
    import ingest
    from mailmind.bootstrap.mailbox import store
    from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
    name = "ada-inbox-02-lunch-confirm.eml"
    elsewhere(world, name)                            # flagged elsewhere; here it's marked read meanwhile
    real, clicked = ImaplibSession.flags, []

    def click_lands_first(self, uids):
        if not clicked:
            clicked.append(True)
            c = store.conn()
            c.execute("UPDATE messages SET seen=1 WHERE pk=?", (world.pk(name),))
            c.commit()
            c.close()
        return real(self, uids)
    monkeypatch.setattr(ImaplibSession, "flags", click_lands_first)
    ingest.sync_new(acct())
    assert clicked and (world.row(name)["seen"], world.row(name)["flagged"]) == (1, 0)


def test_hwf_a_flag_change_still_queued_holds_the_mark_so_the_row_is_read_again(app, world):
    import ingest
    from mailmind.bootstrap.mailbox import store
    name = "ada-inbox-02-lunch-confirm.eml"
    world.server.fail("LOGIN", user=w.ADA)            # so this one queues
    assert app.post("/api/flag", {"pk": world.pk(name), "flag": "flagged", "on": True}).json["status"] == "queued"
    mark = modseq()
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid(name)].flags.add("\\Seen")
    ingest.sync_new(acct())
    assert modseq() == mark
    c = store.conn()
    c.execute("UPDATE read_mutations SET state='error' WHERE account=? AND op='flag'", (w.ADA,))
    c.commit()
    c.close()
    ingest.sync_new(acct())
    assert world.row(name)["seen"] == 1 and modseq() > mark


def test_hwf_a_new_uidvalidity_starts_the_mark_again(world):
    import ingest
    old = modseq()
    assert old
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(acct())
    assert modseq() == world.server.mailbox(w.ADA, "INBOX").highest_modseq


def test_hwf_a_sync_tells_an_open_list_of_mail_read_elsewhere(app, world, monkeypatch):
    import ingest
    from mailmind.platform import sse
    told = []
    real = sse.publish
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: (told.append((topic, data)), real(topic, data))[1])
    ingest.sync_new(acct())
    assert [t for t, _ in told if t == "flags-changed"] == []         # nothing changed, nothing said
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid("ada-inbox-02-lunch-confirm.eml")].flags.add(w.SEEN)
    ingest.sync_new(acct())
    assert [d for t, d in told if t == "flags-changed"] == [{"account": w.ADA, "folder": "INBOX", "count": 1}]


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


def test_hwf_an_inbox_emptied_elsewhere_can_be_pruned(app, world):
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages.clear()
    app.post("/api/reconcile", {"account": w.ADA})
    assert stored() != {}                                        # an empty answer may be the server's fault
    app.post("/api/reconcile", {"account": w.ADA, "force": True})
    assert stored() == {}


def test_hwf_mail_deleted_from_another_folder_leaves_the_list(app, world):
    name = "ada-archive-01-old-thread.eml"
    world.deliver(w.ADA, "Archive", late())                      # so the folder isn't left empty
    gone(world, name)
    r = app.post("/api/reconcile", {"account": w.ADA}).json
    assert world.where(name)["db"] == [] and r["total_pruned"] == 1


# -- when the server says no, or can't be reached --------------------------

def test_hwf_a_refused_select_keeps_the_message(app, world):
    for path in ("/api/trash", "/api/archive", "/api/junk"):
        world.server.fail("SELECT", user=w.ADA)
        since = world.mark()
        r = app.post(path, {"pk": world.pk(RECEIPT)}).json
        assert (r["ok"], r["undoable"]) == (False, False), path
        assert world.writes(since) == [], path
        assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")]), path


def test_hwf_a_refused_gmail_unarchive_is_not_reported_as_undone(app, world):
    name = "cy-inbox-03-unrelated.eml"
    assert app.post("/api/archive", {"pk": world.pk(name)}).json["ok"]
    world.server.fail("UID STORE", user=w.CY)
    assert app.post("/api/undo", {}).json["ok"] is False
    assert app.post("/api/undo", {}).json["ok"]                  # kept for a retry, which works
    assert (w.CY, "INBOX") in boxes(world, name)[0]


def test_hwf_a_refused_move_back_is_not_reported_as_undone(app, world):
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    assert app.post("/api/undo", {}).json["ok"] is False
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]
    assert app.post("/api/undo", {}).json["ok"]
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "INBOX")]


def test_hwf_a_refused_login_queues_the_move_instead_of_dropping_it(app, world):
    from mailmind.bootstrap.mailbox import offline
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] invalid credentials", user=w.ADA)
    r = app.post("/api/archive", {"pk": world.pk(RECEIPT), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [])
    offline.drain()                                             # the login works again
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
    from mailmind.bootstrap.mailbox import offline
    queue(app, world)
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # the receipt's old UID now names the invite
    offline.drain()
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]
    assert boxes(world, "ada-inbox-06-outlook-invite.eml")[0] == [(w.ADA, "INBOX")]


def test_hwf_a_queued_move_undone_while_the_worker_waits_for_the_lock_never_ships(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import offline
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
    monkeypatch.setattr(offline, "gates", TheUndoGetsTheGateFirst())
    offline.drain()                                            # claimed, then cancelled before it got the lock
    assert undone == [True]
    assert world.writes(since) == []
    assert queued() == []
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])


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


# -- sweep, expiry and the AI sweep ----------------------------------------

def audit(world):
    import json
    p = world.dir / "sweep_audit.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def test_hwf_a_sweep_retries_a_move_the_server_said_ok_to_but_didnt_do(world):
    world.server.fail("UID MOVE", "OK [scripted] moved, or so it says", user=w.ADA)
    res = curation.sweeps.sweep(acct())
    assert (res["moved"], res["stragglers"], res["error"]) == (3, [], None)
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "Trash")], []), name


def test_hwf_a_sweep_reports_and_keeps_mail_that_never_moved(world):
    uid = str(world.uid("ada-inbox-04-ci.eml"))
    world.server.fail("UID MOVE", "OK [scripted] moved, or so it says", times=None, user=w.ADA,
                      when=lambda a: uid in str(a[0]).split(","))
    res = curation.sweeps.sweep(acct())
    assert (res["moved"], res["stragglers"]) == (2, [uid])
    assert boxes(world, "ada-inbox-04-ci.eml") == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
    assert boxes(world, "ada-inbox-12-scam.eml") == ([(w.ADA, "Trash")], [])


def test_hwf_a_sweep_prunes_what_it_moved_by_message_id(world):
    """UIDVALIDITY changed and no sync has seen it, so a stored UID may now be another message's."""
    from mailmind.bootstrap.mailbox import store
    swept, kept = world.row("ada-inbox-04-ci.eml"), world.row("ada-inbox-01-lunch.eml")
    c = store.conn()
    c.execute("UPDATE messages SET uid=90001 WHERE pk=?", (swept["pk"],))
    c.execute("UPDATE messages SET uid=? WHERE pk=?", (world.uid("ada-inbox-04-ci.eml"), kept["pk"]))
    c.commit()
    c.close()
    assert curation.sweeps.sweep(acct())["moved"] == 3
    assert q("SELECT pk FROM messages WHERE pk=?", swept["pk"]) == []
    assert q("SELECT pk FROM messages WHERE pk=?", kept["pk"]) == [{"pk": kept["pk"]}]


def test_hwf_a_sweep_prunes_a_message_with_no_message_id_by_uid(world):
    uid = world.deliver(w.ADA, "INBOX", b"From: News <news@loom.example.test>\r\nTo: ada@example.test\r\n"
                                        b"Subject: No id\r\nList-Unsubscribe: <https://loom.example.test/u>\r\n"
                                        b"Date: Mon, 02 Nov 2026 10:00:00 +0000\r\n\r\nHello\r\n")
    assert curation.sweeps.sweep(acct())["moved"] == 4
    assert q("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid) == []


def test_hwf_a_sweep_audits_before_it_moves(world):
    world.server.drop("UID MOVE", user=w.ADA)
    res = curation.sweeps.sweep(acct())
    assert res["error"] and res["moved"] == 0
    assert sorted(int(e["uid"]) for e in audit(world)) == sorted(world.uid(n) for n in JUNK_INBOX)
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")]), name


FAR = 4_000_000_000                                              # a "now" long after every corpus date


def test_hwf_expiry_audits_before_it_moves(world):
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("loom.example.test", 1)
    world.server.drop("UID MOVE", user=w.ADA)
    res = curation.expiry.account(acct(), now=FAR)
    assert res["error"] and res["moved"] == 0
    assert [(e["uid"], e["bucket"]) for e in audit(world)] == [(str(world.uid("ada-inbox-03-newsletter.eml")),
                                                                "expired")]
    assert boxes(world, "ada-inbox-03-newsletter.eml")[0] == [(w.ADA, "INBOX")]


def test_hwf_expiry_prunes_the_local_row_by_message_id(world):
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("thimble.example.test", 1)                 # the receipt's sender
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # the receipt is now UID 4 on the server
    assert curation.expiry.account(acct(), now=FAR)["moved"] == 1
    assert boxes(world, RECEIPT) == ([(w.ADA, "Trash")], [])
    assert world.where("ada-inbox-04-ci.eml")["db"][0]["uid"] == 4    # the row stored at UID 4 stays


def test_hwf_a_sweep_still_moves_the_junk_when_the_server_refuses_move(world):
    world.server.fail("UID MOVE", "BAD [CANNOT] MOVE not supported", times=None, user=w.ADA)
    assert curation.sweeps.sweep(acct())["moved"] == 3


def test_hwf_a_sweep_and_expiry_on_a_server_without_move_copy_then_expunge_one_uid(world):
    from mailmind.bootstrap.curation import rules
    world.server.without = {"MOVE"}
    since = world.mark()
    assert curation.sweeps.sweep(acct())["moved"] == 3
    rules.set_expiry("thimble.example.test", 1)                 # the receipt's sender
    assert curation.expiry.account(acct(), now=FAR)["moved"] == 1
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Trash")]
    kinds = [line.split(" ", 3)[2] + " " + line.split(" ", 3)[3].split(" ", 1)[0]
             for line in world.writes(since)]
    assert set(kinds) == {"UID COPY", "UID STORE", "UID EXPUNGE"}   # never MOVE, never a bulk EXPUNGE


def test_hwf_expiry_ages_mail_by_when_it_arrived_not_its_date_header(world):
    import email.utils
    from datetime import datetime, timezone
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("loom.example.test", 72)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    hour, month = now - timedelta(hours=1), now - timedelta(days=30)

    def issue(n, dated, arrived):
        raw = w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<loom-{n}@example.test>",
                        Date=email.utils.format_datetime(dated))
        world.server.deliver(w.ADA, "INBOX", raw, when=arrived)
    issue("old-header", month, hour)                            # written long ago, arrived an hour ago: kept
    issue("new-header", hour, month)                            # says an hour old, arrived a month ago: expired
    curation.expiry.account(acct(), now=now.timestamp())
    assert world.server.find(w.ADA, "INBOX", "<loom-old-header@example.test>")
    assert world.server.find(w.ADA, "Trash", "<loom-new-header@example.test>")


def test_hwf_the_ai_sweep_finds_its_targets_in_the_live_inbox_by_message_id(world):
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # every stored UID is now one off
    res = curation.sweeps.sweep_ai(acct())
    assert (res["moved"], res["stragglers"]) == (3, [])
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "Trash")], []), name
    for name in (RECEIPT, "ada-inbox-06-outlook-invite.eml"):
        assert boxes(world, name)[0] == [(w.ADA, "INBOX")], name


# -- drafts ----------------------------------------------------------------

def drafts_on_server(world):
    """The live copies in Drafts of the subject "Fabric order", as (uid, body)."""
    with world.server.lock:
        return [uid for uid, m in world.server.mailbox(w.ADA, "Drafts").messages.items()
                if "\\Deleted" not in m.flags and b"Fabric order" in m.raw]


def test_hwf_a_draft_save_that_fails_keeps_the_last_copy(app, world):
    first = app.post("/api/draft", new(idempotency_key="d-1")).json
    assert first["ok"] and drafts_on_server(world) == [first["uid"]]
    world.server.fail("APPEND", user=w.ADA)
    second = app.post("/api/draft", {**new(idempotency_key="d-1"), "draft_id": first["draft_id"]})
    assert second.status == 502
    assert drafts_on_server(world) == [first["uid"]]


def test_hwf_a_lost_connection_during_a_draft_save_keeps_the_last_copy(app, world):
    first = app.post("/api/draft", new(idempotency_key="d-1")).json
    world.server.drop("APPEND", user=w.ADA)
    assert app.post("/api/draft", {**new(idempotency_key="d-1"), "draft_id": first["draft_id"]}).status == 502
    assert drafts_on_server(world) == [first["uid"]]


def test_hwf_a_draft_whose_old_copy_wont_go_is_saved_all_the_same(app, world):
    first = app.post("/api/draft", new(idempotency_key="d-1")).json
    world.server.fail("UID STORE", user=w.ADA)
    second = app.post("/api/draft", {**new(idempotency_key="d-1"), "draft_id": first["draft_id"]}).json
    assert second["ok"] and second["uid"] != first["uid"]
    assert sorted(drafts_on_server(world)) == sorted([first["uid"], second["uid"]])


def test_hwf_a_new_draft_copy_replaces_the_old_with_no_uid_to_go_by(app, world):
    world.server.without.add("UIDPLUS")               # no APPENDUID: the old copy goes by its Message-ID
    first = app.post("/api/draft", new(idempotency_key="d-1")).json
    assert first["uid"] is None and len(drafts_on_server(world)) == 1
    second = app.post("/api/draft", {**new(idempotency_key="d-1"), "draft_id": first["draft_id"]}).json
    assert second["ok"] and len(drafts_on_server(world)) == 1


# -- the outbox ------------------------------------------------------------

def test_hwf_a_crash_after_delivery_never_sends_twice(app, world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
    from mailmind.bootstrap.mailbox import store
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
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(idempotency_key="k-bytes")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    r = outbox_row(oid)
    [wire] = world.smtp.messages
    [uid] = world.server.find(w.ADA, "Sent", r["message_id"])
    copy = world.server.messages(w.ADA, "Sent")[uid].raw
    assert copy == r["raw"]
    assert copy.rstrip(b"\r\n") == wire.data.rstrip(b"\r\n")


def test_hwf_a_sent_copy_that_failed_is_filed_once_the_server_is_back(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(idempotency_key="k-sent-later")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    world.server.drop("APPEND", user=w.ADA)                     # the connection goes as the copy goes up
    outbox.drain()
    assert len(world.smtp.messages) == 1                         # delivered all the same
    app.clock.shift(timedelta(minutes=20))                       # past any back-off
    outbox.drain()
    r = outbox_row(oid)
    assert (r["sent_appended"], len(world.smtp.messages)) == (1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1


def test_hwf_a_sent_copy_that_landed_but_lost_its_answer_is_filed_once(app, world):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", new(idempotency_key="k-landed")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    world.server.drop_after("APPEND", user=w.ADA)               # the copy is filed; its OK never comes
    outbox.drain()
    assert outbox_row(oid)["sent_appended"] == 0
    app.clock.shift(timedelta(minutes=20))
    outbox.drain()
    r = outbox_row(oid)
    assert (r["state"], r["sent_appended"], len(world.smtp.messages)) == ("sent", 1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1


def test_hwf_a_send_owing_its_sent_copy_is_listed_sent_and_retried_once_it_gives_up(app, world):
    from mailmind.bootstrap.compose import outbox

    def listed():
        return {i["id"]: i for i in app.get("/api/outbox").json["items"]}[oid]
    oid = app.post("/api/send", new(idempotency_key="k-owed")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    world.server.fail("APPEND", times=outbox.MAX_ATTEMPTS, user=w.ADA)
    outbox.drain()
    item = listed()
    assert (item["state"], item["scheduled"], item["error"]) == ("sent", False, outbox_item.SENT_COPY_PENDING)
    assert app.post("/api/outbox/cancel", {"id": oid}).json == {"ok": False}   # it went out: nothing to undo
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP))
        outbox.drain()
    item = listed()
    assert (item["state"], item["error"]) == ("error", outbox_item.SENT_COPY_FAILED)
    assert len(world.server.commands_for(w.ADA, "APPEND")) == outbox.MAX_ATTEMPTS
    assert app.post("/api/outbox/retry", {"id": oid}).json == {"ok": True}
    outbox.drain()
    r = outbox_row(oid)
    assert (r["state"], r["sent_appended"], len(world.smtp.messages)) == ("sent", 1, 1)
    assert len(world.server.find(w.ADA, "Sent", r["message_id"])) == 1
    assert (listed()["state"], listed()["error"]) == ("sent", None)


def test_hwf_only_a_send_past_the_undo_window_is_listed_as_scheduled(app):
    from mailmind.bootstrap.compose import outbox
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


def filed(world, folder, name="<filed@example.test>", flags=(w.SEEN,), special="", email=w.ADA):
    """A message in a folder of its own, on the server and synced. Returns its pk and UID."""
    from mailmind.bootstrap import accounts
    import ingest
    from fakes.imap_server import Mailbox
    a = world.server.accounts[email]
    with world.server.lock:
        a.mailboxes[folder] = Mailbox(folder, special, uidvalidity=1_700_000_998)
    uid = world.server.deliver(email, folder, w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID=name),
                               flags=flags)
    ingest.sync_folder(accounts.get(email), folder, full=True)
    return q("SELECT pk FROM messages WHERE account=? AND folder=? AND uid=?", email, folder, uid)[0]["pk"], uid


def old_row(folder, uid, name="<filed@example.test>", email=w.ADA):
    """The `drafts` row Edit left, until 2026-11, on anything in a folder whose name held "draft"."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        did = book.draft_upsert(c, {"id": None, "account": email, "folder": folder, "uid": uid, "message_id": name,
                                     "uidvalidity": store.get_uidvalidity(c, email, folder), "in_reply_to_pk": None,
                                     "mode": "new", "subject": "Filed", "created_at": "2026-10-01T09:00:00",
                                     "updated_at": "2026-10-01T09:00:00"})
        c.commit()
    finally:
        c.close()
    return did


@pytest.mark.parametrize("folder", ["Contract drafts", "Clients/Draft"])
def test_hwf_received_mail_in_a_folder_named_for_drafts_is_no_draft(app, world, folder):
    pk, _ = filed(world, folder)
    assert "is_draft" not in app.get(f"/api/message/{pk}/body").json
    since = world.mark()
    refused = app.post("/api/draft/resume", {"pk": pk})
    assert (refused.status, refused.json["ok"]) == (400, False)
    assert world.writes(since) == [] and q("SELECT id FROM drafts WHERE folder=?", folder) == []


def test_hwf_discard_forgets_an_old_row_on_received_mail_and_keeps_the_mail(app, world):
    _, uid = filed(world, "Contract drafts")
    did = old_row("Contract drafts", uid)
    since = world.mark()
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"]
    assert world.writes(since) == []
    assert world.server.find(w.ADA, "Contract drafts", "<filed@example.test>") == [uid]
    assert q("SELECT id FROM drafts WHERE id=?", did) == []


def test_hwf_sending_keeps_received_mail_an_old_row_points_at(app, world):
    from mailmind.bootstrap.compose import outbox
    _, uid = filed(world, "Contract drafts")
    did = old_row("Contract drafts", uid)
    assert app.post("/api/send", new(idempotency_key="k-filed", draft_id=did)).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert len(world.smtp.messages) == 1
    assert world.server.find(w.ADA, "Contract drafts", "<filed@example.test>") == [uid]
    assert q("SELECT id FROM drafts WHERE id=?", did) == []


def test_hwf_a_drafts_folder_the_server_names_in_another_language_offers_edit(app, world):
    with world.server.lock:
        world.server.accounts[w.ADA].mailboxes["Drafts"].special = ""
    pk, _ = filed(world, "Brouillons", name="<brouillon@example.test>", special="\\Drafts")
    assert "is_draft" not in app.get(f"/api/message/{pk}/body").json     # no list yet: the name says nothing
    assert app.get(f"/api/folders?account={w.ADA}").json["folders"]
    assert app.get(f"/api/message/{pk}/body").json["is_draft"]
    assert app.post("/api/draft/resume", {"pk": pk}).json["ok"]


def test_hwf_a_message_the_server_flags_a_draft_offers_edit_in_any_folder(app, world):
    pk, uid = filed(world, "Half written", name="<half@example.test>", flags=(w.SEEN, w.DRAFT))
    assert app.get(f"/api/message/{pk}/body").json["is_draft"]
    assert app.post("/api/draft/resume", {"pk": pk}).json["ok"]
    did = q("SELECT id FROM drafts WHERE folder='Half written'")[0]["id"]
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"]
    assert world.server.find(w.ADA, "Half written", "<half@example.test>") == []


def listed(app, folder="Drafts", email=w.ADA):
    return {i["subject"]: i["pk"] for i in app.get(
        f"/api/messages?account={email}&folder={folder}&limit=200").json["items"]}


@pytest.fixture
def published(monkeypatch):
    """Every event the live stream is asked to send, as (topic, data)."""
    from mailmind.platform import sse
    seen, real = [], sse.publish
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: (seen.append((topic, data)), real(topic, data))[1])
    return seen


def test_hwf_a_resaved_draft_leaves_one_row_at_once(app, world, published):
    first = app.post("/api/draft", new(subject="Fabric order")).json
    assert "Fabric order" in listed(app)                        # the catch-up brought the copy in
    del published[:]
    second = app.post("/api/draft", new(subject="Fabric order, again", draft_id=first["draft_id"])).json
    assert q("SELECT uid FROM messages WHERE account=? AND folder='Drafts' AND uid=?", w.ADA, first["uid"]) == []
    assert ("reconcile", {"account": w.ADA, "folder": "Drafts", "pruned": 1}) in published
    shown = listed(app)
    assert "Fabric order" not in shown and "Fabric order, again" in shown
    assert q("SELECT uid FROM messages WHERE account=? AND folder='Drafts' AND uid=?", w.ADA,
             second["uid"])[0]["uid"] == second["uid"]


def test_hwf_a_discarded_draft_leaves_the_list_at_once(app, world):
    saved = app.post("/api/draft", new(subject="Fabric order")).json
    assert "Fabric order" in listed(app)
    assert app.post("/api/draft", {"delete": True, "draft_id": saved["draft_id"]}).json["deleted"]
    assert "Fabric order" not in listed(app)


def test_hwf_a_sent_draft_leaves_the_list_at_once(app, world, published):
    from mailmind.bootstrap.compose import outbox
    saved = app.post("/api/draft", new(subject="Fabric order")).json
    assert "Fabric order" in listed(app)
    del published[:]
    app.post("/api/send", new(subject="Fabric order", draft_id=saved["draft_id"], idempotency_key="k-listed"))
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert len(world.smtp.messages) == 1 and "Fabric order" not in listed(app)
    assert ("reconcile", {"account": w.ADA, "folder": "Drafts", "pruned": 1}) in published


def test_hwf_a_draft_the_server_would_not_remove_stays_listed(app, world):
    saved = app.post("/api/draft", new(subject="Fabric order")).json
    assert "Fabric order" in listed(app)
    world.server.fail("UID STORE", user=w.ADA)
    assert app.post("/api/draft", {"delete": True, "draft_id": saved["draft_id"]}).json["deleted"]
    assert "Fabric order" in listed(app)


def test_hwf_a_draft_moved_between_folders_leaves_the_old_list(app, world):
    pk, uid = filed(world, "Half written", name="<half@example.test>", flags=(w.SEEN, w.DRAFT))
    assert app.post("/api/draft/resume", {"pk": pk}).json["ok"]
    did = q("SELECT id FROM drafts WHERE folder='Half written'")[0]["id"]
    assert app.post("/api/draft", new(subject="Half done", draft_id=did)).json["ok"]
    assert q("SELECT pk FROM messages WHERE pk=?", pk) == []
    assert world.server.find(w.ADA, "Half written", "<half@example.test>") == []


LUNCH = "ada-inbox-02-lunch-confirm.eml"


def forget_row(world, name):
    """The original's row goes, as reconcile pruning it, expiry or a queued archive would."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    store.delete_message(c, world.pk(name))
    c.commit()
    c.close()


def replying(world, **kw):
    return {"account": w.ADA, "mode": "reply", "in_reply_to_pk": world.pk(LUNCH), "to": ["lin@example.test"],
            "subject": "Re: Lunch on Thursday?", "text": "See you there.", **kw}


def test_hwf_a_reply_saved_once_no_longer_needs_its_originals_row(app, world):
    from mailmind.bootstrap.compose import outbox
    pk, resave = world.pk(LUNCH), replying(world)
    first = app.post("/api/draft", resave).json
    assert first["resume"]["in_reply_to"] and first["resume"]["tail_text"].startswith("\n\nOn ")
    forget_row(world, LUNCH)
    assert app.post("/api/draft", replying(world) | {"in_reply_to_pk": pk}).status == 404      # the old way
    named = {"in_reply_to_pk": pk, "draft_id": first["draft_id"], "resume": first["resume"]}
    saved = app.post("/api/draft", replying(world) | named | {"text": "See you there. I booked."}).json
    assert saved["ok"] and "resume" not in saved
    sent = app.post("/api/send", replying(world) | named | {"text": "See you there. I booked.",
                                                            "idempotency_key": "k-gone"}).json
    assert sent["ok"], sent
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [out] = world.smtp.messages
    assert email.message_from_bytes(out.data)["In-Reply-To"] and b"I booked." in out.data
    [(_, box, _, flags)] = world.where(LUNCH)["server"]
    assert (box, "\\Answered" in flags) == ("INBOX", True)


def test_hwf_a_forward_saved_once_carries_its_files_itself(app, world):
    from mailmind.bootstrap.compose import outbox
    with_file = w.variant(LUNCH, Message_ID="<with-file@example.test>")
    msg = email.message_from_bytes(with_file, policy=email.policy.default)
    msg.add_attachment(b"linen swatch", maintype="application", subtype="octet-stream", filename="swatch.bin")
    world.deliver(w.ADA, "INBOX", msg.as_bytes())
    [orig] = q("SELECT pk FROM messages WHERE message_id='<with-file@example.test>'")
    body = {"account": w.ADA, "mode": "forward", "in_reply_to_pk": orig["pk"], "to": ["bea@example.test"],
            "subject": "Fwd: Lunch", "text": "For you."}
    first = app.post("/api/draft", body).json
    assert [(f["filename"], f["size"]) for f in first["files"]] == [("swatch.bin", 12)]
    assert first["resume"]["in_reply_to"] == ""
    c_files = [{k: f[k] for k in ("filename", "content_type", "b64")} for f in first["files"]]
    named = {"draft_id": first["draft_id"], "resume": first["resume"], "attachments": c_files}
    assert app.post("/api/send", body | named | {"idempotency_key": "k-fwd"}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [out] = world.smtp.messages
    parts = list(email.message_from_bytes(out.data, policy=email.policy.default).iter_attachments())
    assert [p.get_filename() for p in parts] == ["swatch.bin"]              # once, not twice


def test_hwf_a_reply_records_its_follow_up_after_its_original_is_archived(app, world):
    from mailmind.bootstrap.compose import outbox
    assert app.post("/api/send", replying(world, idempotency_key="k-fu")).json["ok"]
    [before] = q("SELECT thread_id FROM messages WHERE pk=?", world.pk(LUNCH))
    assert app.post("/api/archive", {"pk": world.pk(LUNCH)}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert [r["thread_id"] for r in q("SELECT thread_id FROM followups WHERE kind='sent'")] == [before["thread_id"]]


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
    from mailmind.bootstrap.curation import rules
    import server
    rules.set_expiry("loom.example.test", 1)
    pk = world.pk("bea-inbox-01-client.eml")                    # its body was never fetched
    n = len(world.server.commands)

    def background():
        got = {"prefetch": server.message_body(pk, False, prefetch=True)}
        server._auto_reconcile_one(acct())
        curation.expiry.auto_one(acct())
        got["backfill"] = server.backfill_round()               # yielded, its backlog left for the next round
        return got
    finished, got = while_locked(background)
    assert finished and got == {"prefetch": {"ok": False, "busy": True}, "backfill": 8}
    assert world.server.commands[n:] == []                       # none of them touched the server


# -- connections -----------------------------------------------------------

def test_hwf_a_pooled_connection_is_checked_with_noop_and_dropped_after_an_error(world, monkeypatch):
    from mailmind.bootstrap.mailbox import pool
    monkeypatch.setenv("MAILMIND_POOL", w.ADA)
    monkeypatch.setattr(pool, "_slots", {})
    a, logins = acct(), count(world, "LOGIN")
    try:
        with pool.borrow(a) as first:
            first.select("INBOX")
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
    import realtime
    from mailmind.bootstrap import mailbox
    closed_by = []
    real = mailbox.open_session

    def open_session(acct, timeout=60):
        s = real(acct, timeout=timeout)
        sock = s._c.socket() if hasattr(s, "_c") else s._M.sock     # either adapter's TLS socket
        close = sock.close

        def spy():
            closed_by.append(threading.current_thread().name)
            close()
        sock.close = spy
        return s
    monkeypatch.setattr(mailbox, "open_session", open_session)
    worker = idle()
    assert wait_until(lambda: count(world, "IDLE") == 1)
    realtime.stop()
    worker.join(5)
    assert not worker.is_alive()
    assert closed_by and set(closed_by) == {"idle-worker"}


# -- mute ------------------------------------------------------------------

def test_hwf_a_reply_in_a_muted_thread_is_marked_read_on_the_server_too(app, world):
    # Decision D6: other clients agree. A reply that lands in a muted thread is read in MailMind at
    # once, and on the server once the queue has told it.
    from mailmind.bootstrap.mailbox import offline
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    reply = w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<late-mute@example.test>",
                      In_Reply_To=lunch["message_id"])
    uid = world.deliver(w.ADA, "INBOX", reply)
    [r] = q("SELECT seen, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert (r["seen"], r["thread_id"]) == (1, lunch["thread_id"])
    assert w.SEEN not in world.server.messages(w.ADA, "INBOX")[uid].flags
    offline.drain()
    assert w.SEEN in world.server.messages(w.ADA, "INBOX")[uid].flags


def test_hwf_a_queued_move_for_a_removed_account_is_given_up(app, world):
    from mailmind.bootstrap.accounts import config
    from mailmind.bootstrap.mailbox import offline
    queue(app, world)
    assert config.remove_account(w.ADA)
    offline.drain()
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
        from mailmind.bootstrap.compose import outbox
        self.stopping = True
        outbox.kick()


def test_hwf_a_send_goes_out_when_its_undo_window_ends(world, monkeypatch):
    from mailmind.bootstrap.compose import outbox
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
        assert sending.send(new(idempotency_key="k-soon"))[1] == 200
        wait_until(lambda: world.smtp.messages, timeout=15)      # due within a second; the worker polls every 30
        assert world.smtp.messages
    finally:
        drain.stop()
        t.join(5)
        for lane in threading.enumerate():                       # or a late send lands in the next test's database
            if lane.name.startswith("outbox:"):
                lane.join(15)
    assert not t.is_alive()


def test_hwf_a_manual_retry_gets_a_fresh_set_of_attempts(app, world):
    from mailmind.bootstrap.compose import outbox
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


def test_hwf_backfill_moves_past_bodies_it_cannot_fetch(world, monkeypatch):
    import server
    monkeypatch.setattr(server, "time", _Sleeps(stop_at=float("inf")))   # no real pacing between fetches
    monkeypatch.setattr(server, "_backfill_missed", {})          # world alone resets no module state
    waiting = q("SELECT pk, account, folder, uid FROM messages WHERE body_text IS NULL ORDER BY ts DESC, pk DESC")
    stuck, older = waiting[:12], [r["pk"] for r in waiting[12:]]
    with world.server.lock:                                      # deleted elsewhere, not yet pruned here
        for r in stuck:
            world.server.mailbox(r["account"], r["folder"]).messages.pop(r["uid"])
    for _ in range(2):                                           # two backfill rounds
        server.backfill_round()
    fetched = q("SELECT pk FROM messages WHERE body_text IS NOT NULL")
    assert {r["pk"] for r in fetched} & set(older)
    missed = dict(server._backfill_missed)
    assert set(missed) == {r["pk"] for r in stuck}               # each waits its turn, once
    assert all(at >= time.time() + server.BACKFILL_RETRY - 60 for at in missed.values())


def test_hwf_backfill_asks_again_for_a_missed_body_once_its_wait_is_over(world, monkeypatch):
    import server
    monkeypatch.setattr(server, "time", _Sleeps(stop_at=float("inf")))
    monkeypatch.setattr(server, "_backfill_missed", {})
    [newest] = q("SELECT pk, account, folder, uid FROM messages WHERE body_text IS NULL "
                 "ORDER BY ts DESC, pk DESC LIMIT 1")
    with world.server.lock:
        kept = world.server.mailbox(newest["account"], newest["folder"]).messages.pop(newest["uid"])
    server.backfill_round()
    assert newest["pk"] in server._backfill_missed
    with world.server.lock:                                      # it comes back, as a flaky server's would
        world.server.mailbox(newest["account"], newest["folder"]).messages[newest["uid"]] = kept
    server._backfill_missed[newest["pk"]] = 0                    # its wait is over
    while q("SELECT 1 FROM messages WHERE pk=? AND body_text IS NULL", newest["pk"]):
        assert server.backfill_round() == 8
    assert newest["pk"] not in server._backfill_missed


def test_hwf_backfill_stops_its_round_when_the_server_cant_be_reached(world, monkeypatch):
    import ingest
    import server
    monkeypatch.setattr(server, "time", _Sleeps(stop_at=float("inf")))
    monkeypatch.setattr(server, "_backfill_missed", {})
    asked = []

    def down(pk):
        asked.append(pk)
        raise ConnectionRefusedError()
    monkeypatch.setattr(ingest, "fetch_body", down)
    assert server.backfill_round() == server.BACKFILL_UNREACHABLE
    assert len(asked) == 1 and server._backfill_missed == {}     # nothing set aside: it wasn't the message


def test_hwf_a_folder_whose_first_sync_failed_is_synced_on_the_next_open(app, world):
    world.server.deliver(w.ADA, "Junk", late())
    world.server.fail("UID SEARCH", user=w.ADA)
    r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
    assert (r["items"], r.get("sync_failed")) == ([], True)          # said, not shown as an empty folder
    r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
    assert len(r["items"]) == 1 and "sync_failed" not in r


def test_hwf_opening_a_warmed_folder_shows_its_older_mail(app, world):
    import ingest
    for n in range(3):
        world.server.deliver(w.ADA, "Junk", late(n))
    ingest.sync_folder(acct(), "Junk", recent=2)                 # warm.warm_tick's first touch, WARM_RECENT=2
    assert len(app.get(f"/api/messages?account={w.ADA}&folder=Junk").json["items"]) == 3


def junk_scans(world):
    """How many times Ada's Junk has been scanned whole."""
    return sum(1 for c in world.server.commands_for(w.ADA, "UID SEARCH")
               if c["mailbox"] == "Junk" and c["args"] == ["ALL"])


def test_hwf_a_first_open_slower_than_the_wait_answers_then_lists_the_rest(app, world, monkeypatch):
    import server
    from gate import held_elsewhere
    monkeypatch.setattr(server, "FIRST_SYNC_WAIT", 0.2)
    world.server.deliver(w.ADA, "Junk", late())
    with held_elsewhere(w.ADA):                                  # a long mail operation holds the gate
        for _ in range(2):                                       # a second open joins the first's scan
            r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
            assert (r["items"], r.get("syncing")) == ([], True)
    assert wait_until(lambda: not server._folder_syncs)
    r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
    assert len(r["items"]) == 1 and "syncing" not in r
    assert junk_scans(world) == 1


def test_hwf_an_open_past_the_first_page_starts_no_scan(app, world):
    world.server.deliver(w.ADA, "Junk", late())
    assert app.get(f"/api/messages?account={w.ADA}&folder=Junk&offset=50").json["items"] == []
    assert junk_scans(world) == 0


def catch_ups(world, folder):
    """The searches above a folder's watermark: the catch-ups its opening ran."""
    return [c for c in world.server.commands_for(w.ADA, "UID SEARCH")
            if c["mailbox"] == folder and c["args"][:1] == ["UID"]]


def test_hwf_opening_a_folder_again_shows_mail_filed_there_elsewhere(app, world, monkeypatch):
    import server
    archive = f"/api/messages?account={w.ADA}&folder=Archive"
    before = len(app.get(archive).json["items"])
    world.server.deliver(w.ADA, "Archive", late())               # filed by another client
    monkeypatch.setattr(server, "CATCH_UP_EVERY", 0)
    r = app.get(archive).json
    assert len(r["items"]) == before + 1 and "catching_up" not in r


def test_hwf_a_folder_opened_again_soon_is_caught_up_once(app, world):
    for _ in range(3):
        app.get(f"/api/messages?account={w.ADA}&folder=Archive")
    assert len(catch_ups(world, "Archive")) == 1


def test_hwf_a_folder_opened_during_its_catch_up_says_so_too(app, world, monkeypatch):
    import server
    monkeypatch.setattr(server, "CATCH_UP_WAIT", 0.05)
    with gate.held_elsewhere(w.ADA):
        assert app.get(f"/api/messages?account={w.ADA}&folder=Archive").json["catching_up"]
        assert app.get(f"/api/messages?account={w.ADA}&folder=Archive").json["catching_up"]
    assert wait_until(lambda: not server._folder_syncs)
    assert "catching_up" not in app.get(f"/api/messages?account={w.ADA}&folder=Archive").json
    assert len(catch_ups(world, "Archive")) == 1


def test_hwf_opening_the_inbox_leaves_it_to_the_background_sync(app, world):
    app.get(f"/api/messages?account={w.ADA}")
    assert catch_ups(world, "INBOX") == []


def test_hwf_a_draft_saved_lets_drafts_catch_up_on_its_next_open(app, world):
    drafts = f"/api/messages?account={w.ADA}&folder=Drafts"
    app.get(drafts)
    assert app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"],
                                   "subject": "Swatch order", "text": "Two of each."}).json["ok"]
    app.get(drafts)
    assert len(catch_ups(world, "Drafts")) == 2


def test_hwf_a_slow_catch_up_answers_with_what_is_stored_then_says_what_came(app, world, monkeypatch):
    import server
    from gate import held_elsewhere
    archive = f"/api/messages?account={w.ADA}&folder=Archive"
    monkeypatch.setattr(server, "CATCH_UP_WAIT", 0.1)
    before = len(app.get(archive).json["items"])
    world.server.deliver(w.ADA, "Archive", late())
    monkeypatch.setattr(server, "CATCH_UP_EVERY", 0)
    with held_elsewhere(w.ADA):
        r = app.get(archive).json
        assert (len(r["items"]), r.get("catching_up")) == (before, True)
    assert wait_until(lambda: not server._folder_syncs)
    assert len(app.get(archive).json["items"]) == before + 1


def test_hwf_an_idle_worker_stops_watching_a_removed_account(world, idle, monkeypatch):
    from mailmind.bootstrap.accounts import config
    import realtime
    monkeypatch.setattr(realtime, "IDLE_SECONDS", 0.1)
    idle()
    assert wait_until(lambda: count(world, "IDLE") >= 1)
    assert config.remove_account(w.ADA)
    time.sleep(0.3)                                              # a few IDLE cycles to notice
    n = len(world.server.commands_for(w.ADA))
    time.sleep(0.25)
    assert len(world.server.commands_for(w.ADA)) == n
