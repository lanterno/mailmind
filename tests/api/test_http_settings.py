"""The settings and maintenance POSTs: sender rules, saved views, accounts (add, test, edit, delete,
the default signature), reconcile, sweep, sync retry and the composer's "new to you" lookup. Each
step pins the answer and what it left on disk or on the server. The golden files are in
tests/golden/http_settings/.
"""
import json

import pytest

from fixtures import world as w
from mailmind.platform.db.uow import UnitOfWork
from stored_rows import document

DEE = "dee@example.test"


def disk(world, name):
    p = world.dir / name
    return json.loads(p.read_text()) if p.exists() else None


def kept(name):
    """What the database keeps as the document `name`, its JSON parsed, or None."""
    with UnitOfWork().read() as c:
        row = c.execute("SELECT body FROM documents WHERE name=?", (name,)).fetchone()
    return None if row is None else json.loads(row["body"])


def post(app, golden, label, path, body):
    return golden.record(label, app.post(path, body))


def test_sender_rules(app, golden, world):
    post(app, golden, "keep a sender", "/api/rules", {"token": "Lin@Example.test", "verdict": "keep"})
    post(app, golden, "block a domain", "/api/rules", {"token": "prizes.example.test", "verdict": "block"})
    since = world.mark()
    post(app, golden, "block a sender and trash the message", "/api/rules",
         {"token": "news@loom.example.test", "verdict": "block", **world.copy("ada-inbox-03-newsletter.eml")})
    golden.note("server writes", world.writes(since))
    golden.note("where the newsletter is", world.where("ada-inbox-03-newsletter.eml"))
    post(app, golden, "remove a rule", "/api/rules", {"token": "prizes.example.test", "verdict": "remove"})
    post(app, golden, "a token that is neither address nor domain", "/api/rules", {"token": "lin", "verdict": "keep"})
    post(app, golden, "an unknown verdict", "/api/rules", {"token": "lin@example.test", "verdict": "maybe"})
    golden.note("the rules document", json.loads(document("rules")))


NOT_A_SENDER = "A rule names one address, such as name@example.com, or one domain, such as example.com."


@pytest.mark.parametrize("token", ["50506 spam@lottery.example.test", "lin@example.test, bo@example.test",
                                   "Lin Mori <lin@example.test>", "example..test", "lin@"])
def test_a_keep_or_block_names_one_address_or_one_domain_and_nothing_beside_it(app, token):
    """What Curate's rule field was given, pasted with a number before the address, say: a rule on it would never match a
    sender, so it is refused in words the page and the Mac app show, and nothing is kept. A removal asks only for an
    address or a domain, so a rule kept before this still goes."""
    before = app.get("/api/rules").json
    for verdict in ("keep", "block"):
        r = app.post("/api/rules", {"token": token, "verdict": verdict})
        assert (r.status, r.json["detail"]) == (400, NOT_A_SENDER)
    assert app.get("/api/rules").json == before
    assert app.post("/api/rules", {"token": token, "verdict": "remove"}).status == 200


def test_saved_views(app, golden, world):
    def made(views, name):                      # view ids are random: name them in the record
        vid = next(v["id"] for v in views["views"] if v["name"] == name)
        golden.alias(vid, f"<view {name}>")
        return vid

    lin = made(post(app, golden, "create a view", "/api/views",
                    {"verb": "create", "name": "From Lin", "q": "from:lin"}).json, "From Lin")
    made(post(app, golden, "create another", "/api/views",
              {"verb": "create", "name": "Receipts", "q": "is:receipt"}).json, "Receipts")
    post(app, golden, "create one with no query", "/api/views", {"verb": "create", "name": "Empty"})
    post(app, golden, "delete the first", "/api/views", {"verb": "delete", "id": lin})
    post(app, golden, "delete a builtin", "/api/views", {"verb": "delete", "id": "__unread__"})
    post(app, golden, "an unknown verb", "/api/views", {"verb": "rename"})
    golden.note("the views document", kept("views"))


def test_accounts_add_test_edit_delete(app, golden, world):
    entry = world.add_account(DEE, password="dee-password")
    add = {"email": DEE, "type": "imap", "host": entry["host"], "port": entry["port"], "starttls": True,
           "smtp_host": entry["smtp_host"], "smtp_port": entry["smtp_port"], "smtp_starttls": True, "alias": "Dee",
           "credential": {"style": "keychain", "value": "dee-password"}}
    post(app, golden, "add Dee", "/api/accounts/add", add)
    post(app, golden, "add Dee again", "/api/accounts/add", add)
    post(app, golden, "add one with no credential", "/api/accounts/add", {"email": "eve@example.test", "type": "imap",
                                                                         "host": "127.0.0.1"})
    post(app, golden, "add one with no host", "/api/accounts/add",
         {"email": "eve@example.test", "type": "imap", "credential": {"style": "keychain", "value": "x"}})
    post(app, golden, "add one that isn't an address", "/api/accounts/add",
         {"email": "eve", "type": "imap", "host": "127.0.0.1", "credential": {"style": "keychain", "value": "x"}})
    post(app, golden, "test Dee", "/api/accounts/test", {"email": DEE})
    post(app, golden, "test an unknown account", "/api/accounts/test", {"email": "zed@example.test"})
    post(app, golden, "test a draft with the wrong password", "/api/accounts/test",
         {"draft": {**add, "credential": {"style": "keychain", "value": "wrong"}}})
    post(app, golden, "test a draft whose server is down", "/api/accounts/test",
         {"draft": {**add, "port": 9}})
    post(app, golden, "test a draft with no host", "/api/accounts/test", {"draft": {"email": DEE, "type": "imap"}})
    post(app, golden, "rename and sign Dee", "/api/accounts/edit",
         {"email": DEE, "alias": "Dee Q.", "signature": "Dee\n-- sent from a test"})
    post(app, golden, "edit an unknown account", "/api/accounts/edit", {"email": "zed@example.test", "alias": "Z"})
    golden.note("mailmind.config.json", disk(world, "mailmind.config.json"))
    post(app, golden, "delete Dee", "/api/accounts/delete", {"email": DEE})
    post(app, golden, "delete Dee again", "/api/accounts/delete", {"email": DEE})
    golden.note("accounts left", [a["email"] for a in disk(world, "mailmind.config.json")["accounts"]])


def test_reconcile_prunes_what_the_server_no_longer_has(app, golden, world):
    box = world.server.mailbox(w.ADA, "INBOX")
    with world.server.lock:                         # another client deletes a message
        box.messages.pop(world.uid("ada-inbox-04-ci.eml"))
    post(app, golden, "reconcile Ada, dry run", "/api/reconcile", {"account": w.ADA, "dry_run": True})
    golden.note("the row after the dry run", world.where("ada-inbox-04-ci.eml"))
    post(app, golden, "reconcile everyone", "/api/reconcile", {})
    golden.note("the row after reconcile", world.where("ada-inbox-04-ci.eml"))
    golden.record("the audit trail", app.get("/api/audit"))


# Ada's Inbox junk: Loom's issue, the CI mail and the scam.
JUNK = ("ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml")


def header_reads(world):
    """How many times the server has been asked for headers of Ada's mail, as a sweep reads them."""
    return sum("HEADER.FIELDS" in c["line"].upper() for c in world.server.commands_for(w.ADA, "UID FETCH"))


def flagged_elsewhere(app, world, flags):
    """Another client leaves each of {name: flags} so on the server, and Ada's Inbox syncs, so MailMind knows."""
    for name, said in flags.items():
        world.server_flags(name, *said)
    app.work("sync", w.ADA, "INBOX")


def test_sweep_moves_the_junk_buckets_out_of_the_inbox(app, golden, world):
    flagged_elsewhere(app, world, {name: ("\\Seen",) for name in JUNK})    # a sweep takes only read mail
    post(app, golden, "sweep Ada, dry run", "/api/sweep", {"account": w.ADA, "dry_run": True})
    since = world.mark()
    post(app, golden, "sweep Ada", "/api/sweep", {"account": w.ADA, "undo_group": "ug-sweep-ada"})
    golden.note("server writes", world.writes(since))
    golden.note("Ada's INBOX on the server", sorted(
        str(m.headers()["Message-ID"]) for m in world.server.messages(w.ADA, "INBOX").values()))
    golden.record("the audit trail", app.get("/api/audit"))


def test_a_sweep_leaves_starred_and_unread_junk_in_the_inbox(app, world):
    """Loom's issue and the scam are unread, and Ada starred the CI mail she read: the plan says all three stay, and the
    sweep moves none of them."""
    flagged_elsewhere(app, world, {"ada-inbox-04-ci.eml": ("\\Seen", "\\Flagged")})
    [plan] = app.post("/api/sweep", {"account": w.ADA, "dry_run": True}).json["results"]
    assert (plan["planned"], plan["held_back"], plan["error"]) == (0, 3, None)
    since = world.mark()
    [swept] = app.post("/api/sweep", {"account": w.ADA}).json["results"]
    assert (swept["planned"], swept["held_back"], swept["moved"]) == (0, 3, 0)
    assert world.writes(since) == []
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["INBOX"], name


def test_a_sweep_reads_each_messages_flags_in_the_fetch_of_its_headers(app, world):
    """What a sweep holds back goes by the flags the FETCH of its headers brought, so the two agree, and it asks for no
    flags on their own: one FETCH a batch rather than two."""
    for name in JUNK:
        world.server_flags(name, "\\Seen")
    since = len(world.server.commands_for(w.ADA, "UID FETCH"))
    [swept] = app.post("/api/sweep", {"account": w.ADA}).json["results"]
    assert (swept["planned"], swept["held_back"], swept["moved"], swept["error"]) == (3, 0, 3, None)
    fetched = [c["line"].upper() for c in world.server.commands_for(w.ADA, "UID FETCH")[since:]]
    assert fetched and all("HEADER.FIELDS" in line and "FLAGS" in line for line in fetched), fetched


def test_a_sweeps_preview_comes_from_the_stored_inbox_and_asks_the_server_nothing(app, world):
    """Ada's Inbox has had its first scan, so the preview is what MailMind stored of it, read and starred as the last
    sync left it: a big Inbox's would otherwise read every message's headers off the server first."""
    flagged_elsewhere(app, world, {"ada-inbox-03-newsletter.eml": ("\\Seen",), "ada-inbox-12-scam.eml": ("\\Seen", "\\Flagged")})
    since = len(world.server.commands_for(w.ADA))
    [plan] = app.post("/api/sweep", {"account": w.ADA, "dry_run": True}).json["results"]
    assert world.server.commands_for(w.ADA)[since:] == []
    assert (plan["planned"], plan["held_back"], plan["senders"], plan["capped"], plan["no_undo"]) == (2, 1, 2, False, 0)
    assert [(b["bucket"], b["count"], b["subjects"]) for b in plan["going"]] == [
        ("newsletters/marketing", 1, ["This week: five new patterns"]),
        ("automated-notifications", 1, ["[quilt] Build #412 passed"])]


@pytest.mark.parametrize("scan", ["never", "recent", "down-to:7"])
def test_a_sweeps_preview_reads_the_server_until_a_first_scan_has_read_the_inbox_whole(app, world, scan):
    """While Ada's first scan hasn't read her whole Inbox (none has begun, a warm read only its newest, or older mail is
    still to come), only the server can say what it holds: the preview reads it there, flags and all."""
    with app.stopped():
        with UnitOfWork().write() as c:
            c.execute("UPDATE sync_state SET scan_state=? WHERE account=? AND folder='INBOX'", (scan, w.ADA))
    for name in JUNK:
        world.server_flags(name, "\\Seen")                     # read in another client, and MailMind not told
    since = header_reads(world)
    [plan] = app.post("/api/sweep", {"account": w.ADA, "dry_run": True}).json["results"]
    assert (plan["planned"], plan["held_back"], plan["error"]) == (3, 0, None)
    assert header_reads(world) > since


def test_a_sweep_reads_the_inbox_off_the_server_once_and_moves_what_it_says_is_still_read_and_unstarred(app, world):
    """The preview, from what is stored, says all three junk messages go. Before Confirm, another client stars the CI
    mail and marks the scam unread, and MailMind isn't told: Confirm reads the Inbox off the server, the only time this
    sweep does, and moves Loom's issue alone."""
    flagged_elsewhere(app, world, {name: ("\\Seen",) for name in JUNK})
    since = header_reads(world)
    [plan] = app.post("/api/sweep", {"account": w.ADA, "dry_run": True}).json["results"]
    assert (plan["planned"], plan["held_back"]) == (3, 0)
    world.server_flags("ada-inbox-04-ci.eml", "\\Seen", "\\Flagged")
    world.server_flags("ada-inbox-12-scam.eml")
    [swept] = app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-still"}).json["results"]
    assert (swept["planned"], swept["held_back"], swept["moved"], swept["capped"]) == (1, 2, 1, False)
    assert header_reads(world) - since == 1                    # Ada's Inbox is one FETCH, read once
    assert [box for _, box, *_ in world.where("ada-inbox-03-newsletter.eml")["server"]] == ["Trash"]
    for name in ("ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml"):
        assert [box for _, box, *_ in world.where(name)["server"]] == ["INBOX"], name


def test_a_sweep_after_its_preview_from_the_stored_inbox_leaves_the_mail_older_than_what_is_stored(app, world):
    """Ada's Inbox counts as read whole, but a sync that read only her recent mail (`mailmind sync`'s last N days, or an
    Inbox taken as read whole before migration 24) never stored Loom's issue or the mail before it. The preview names
    only what is stored, so Confirm, which reads her Inbox off the server, leaves Loom's issue there, read junk as it is."""
    flagged_elsewhere(app, world, {name: ("\\Seen",) for name in JUNK})
    with app.stopped():
        with UnitOfWork().write() as c:
            c.execute("DELETE FROM messages WHERE account=? AND folder='INBOX' AND uid<=?",
                      (w.ADA, world.uid("ada-inbox-03-newsletter.eml")))
    [plan] = app.post("/api/sweep", {"account": w.ADA, "dry_run": True}).json["results"]
    assert (plan["planned"], plan["held_back"], plan["error"]) == (2, 0, None)
    assert "newsletters/marketing" not in [b["bucket"] for b in plan["going"]]
    [swept] = app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-older"}).json["results"]
    assert (swept["planned"], swept["moved"], swept["error"]) == (2, 2, None)
    assert [box for _, box, *_ in world.where("ada-inbox-03-newsletter.eml")["server"]] == ["INBOX"]
    for name in ("ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml"):
        assert [box for _, box, *_ in world.where(name)["server"]] == ["Trash"], name


def test_the_stats_count_only_the_junk_a_sweep_would_move_now(app, world):
    """The Curate card's Sweep button says how many a sweep would take: Ada's junk that someone read and no one starred.
    Of her corpus junk only the CI mail is read; of three more newsletters, one read, one read and starred and one
    unread, only the first is one more."""
    for n, flags in enumerate([("\\Seen",), ("\\Seen", "\\Flagged"), ()]):
        world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Subject=f"Loom issue {300 + n}",
                                                Message_ID=f"<issue-{300 + n}@loom.example.test>"), flags=flags)
    [ada] = [a for a in app.get("/api/stats").json["accounts"] if a["account"] == w.ADA]
    assert (ada["junk"], ada["sweepable"]) == (6, 2)


def test_one_undo_takes_back_all_a_sweep_moved(app, world):
    """The page names the sweep's undo group, and Undo puts each message back in the Inbox, read as it was."""
    for name in JUNK:
        world.server_flags(name, "\\Seen")
    swept = app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-sweep-1"}).json
    assert (swept["undo_group"], swept["results"][0]["moved"], swept["results"][0]["no_undo"]) == ("ug-sweep-1", 3, 0)
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["Trash"], name
    undone = app.post("/api/undo", {}).json
    assert (undone["ok"], undone["label"], undone["reversed"], undone["failed"]) == (True, "Swept junk", 3, 0)
    for name in JUNK:
        [(_, box, _, flags)] = world.where(name)["server"]
        assert (box, "\\Seen" in flags) == ("INBOX", True), name


def test_one_undo_of_a_sweep_logs_in_at_most_twice_however_much_went(app, world):
    """Undo puts an account's swept mail back from Trash on one session and reads it all in on one more, not two
    logins a message; a server that keeps the account's connection for its jobs needs neither."""
    for name in JUNK:
        world.server_flags(name, "\\Seen")
    app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-sweep-2"})
    since = len(world.server.commands_for(w.ADA, "LOGIN"))
    undone = app.post("/api/undo", {}).json
    assert (undone["reversed"], undone["failed"]) == (3, 0)
    assert len(world.server.commands_for(w.ADA, "LOGIN")) - since <= 2
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["INBOX"], name


def test_a_sweep_the_server_drops_gives_a_way_back_only_to_what_went(app, world):
    """Dropped at its MOVE, nothing went: no undo group, and nothing of it waits on the undo stack. Dropped once its MOVE
    ran, a fresh look finds it all gone, which is what moved, and one Undo brings it back."""
    for name in JUNK:
        world.server_flags(name, "\\Seen")
    world.server.drop("UID MOVE", user=w.ADA)
    [res] = (dropped := app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-dropped-1"}).json)["results"]
    assert (bool(res["error"]), res["moved"], dropped["undo_group"]) == (True, 0, None)
    assert app.post("/api/undo", {}).status == 404
    world.server.drop_after("UID MOVE", user=w.ADA)
    [res] = (dropped := app.post("/api/sweep", {"account": w.ADA, "undo_group": "ug-dropped-2"}).json)["results"]
    assert (bool(res["error"]), res["moved"], dropped["undo_group"]) == (True, 3, "ug-dropped-2")
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["Trash"], name
    undone = app.post("/api/undo", {}).json
    assert (undone["label"], undone["reversed"], undone["failed"]) == ("Swept junk", 3, 0)
    for name in JUNK:
        assert [box for _, box, *_ in world.where(name)["server"]] == ["INBOX"], name


def test_sync_retry_runs_a_sync_for_that_account(app, golden, world):
    world.server.deliver(w.CY, "INBOX", w.variant("cy-inbox-03-unrelated.eml", Message_ID="<parcel-2@parcels.example.test>",
                                                  Subject="Your parcel was delivered"))
    post(app, golden, "retry Cy", "/api/sync-retry-account", {"account": w.CY})
    app.settle()
    golden.note("Cy's INBOX in the database", [r["subject"] for r in app.get(
        f"/api/messages?account={w.CY}").json["items"]])
    post(app, golden, "retry an unknown account", "/api/sync-retry-account", {"account": "zed@example.test"})


def test_refresh_syncs_every_account(app, golden, world):
    world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml",
                                                   Message_ID="<lunch-3@example.test>", Subject="Re: Lunch, one more thing"))
    post(app, golden, "refresh", "/api/refresh", {})
    app.settle()
    golden.note("Ada's INBOX subjects", [r["subject"] for r in app.get(f"/api/messages?account={w.ADA}").json["items"]])
    golden.record("the digest's sync status", app.get("/api/accounts"))


def test_the_digest_and_each_accounts_health_say_how_the_sync_stands(app, world):
    """What the status pill reads: none has run at the start, then every account synced and what the round came to."""
    before = app.get("/api/digest").json
    assert before["sync"] == {"running": False, "last": None, "result": "idle"}
    assert before["status"] == {}
    assert {h["live"] for h in app.get("/api/account-health").json["accounts"]} == {""}

    world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml",
                                                   Message_ID="<lunch-4@example.test>", Subject="Re: Lunch, again"))
    app.post("/api/refresh", {})
    app.settle()

    after = app.get("/api/digest").json
    assert after["sync"]["running"] is False
    assert after["sync"]["last"] == after["generated"]
    assert after["sync"]["result"].endswith(" new") and after["sync"]["result"] != "0 new"
    assert {a: s["health"] for a, s in after["status"].items()} == {w.ADA: "ok", w.BEA: "ok", w.CY: "ok"}
    assert after["status"][w.ADA]["msg"].startswith("+")
    health = app.get("/api/account-health").json["accounts"]
    assert {h["account"]: h["live"] for h in health} == {w.ADA: "ok", w.BEA: "ok", w.CY: "ok"}


def test_the_digest_names_every_configured_account_in_the_configs_order_mail_stored_or_not(app, world):
    """The rail's accounts, which the page and the Mac app read from the digest: an account just added has its place
    before any of its mail is stored, after those the config lists before it, though its name sorts first."""
    abe = "abe@example.test"
    entry = world.server.add_account(abe, password="abe-password")
    assert app.post("/api/accounts/add", {"email": abe, "type": "imap", "host": entry["host"], "port": entry["port"],
                                          "starttls": True, "credential": {"style": "keychain", "value": "abe-password"}
                                          }).json["ok"]
    digest = app.get("/api/digest").json
    assert digest["accounts"] == [w.ADA, w.BEA, w.CY, abe]
    assert digest["send_accounts"] == digest["accounts"]


def test_known_contacts(app, golden):
    post(app, golden, "who is known", "/api/contacts/known", {"addrs": [
        "lin@example.test", "LIN@example.test", "Lin Mori <lin@example.test>", "noor@studio.example.test",
        "winner@prizes.example.test", "ada@example.test", "stranger@example.test", "not an address", ""]})
    post(app, golden, "addrs that isn't a list", "/api/contacts/known", {"addrs": "lin@example.test"})
    post(app, golden, "too many addrs", "/api/contacts/known", {"addrs": [f"p{i}@example.test" for i in range(101)]})
