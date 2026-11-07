"""The settings and maintenance POSTs: sender rules, saved views, accounts (add, test, edit, delete,
the default signature), reconcile, sweep, sync retry and the composer's "new to you" lookup. Each
step pins the answer and what it left on disk or on the server. The golden files are in
tests/golden/http_settings/.
"""
import json

from fixtures import world as w

DEE = "dee@example.test"


def disk(world, name):
    p = world.dir / name
    return json.loads(p.read_text()) if p.exists() else None


def post(app, golden, label, path, body):
    return golden.record(label, app.post(path, body))


def test_sender_rules(app, golden, world):
    post(app, golden, "keep a sender", "/api/rules", {"token": "Lin@Example.test", "verdict": "keep"})
    post(app, golden, "block a domain", "/api/rules", {"token": "prizes.example.test", "verdict": "block"})
    since = world.mark()
    post(app, golden, "block a sender and trash the message", "/api/rules",
         {"token": "news@loom.example.test", "verdict": "block", "pk": world.pk("ada-inbox-03-newsletter.eml")})
    golden.note("server writes", world.writes(since))
    golden.note("where the newsletter is", world.where("ada-inbox-03-newsletter.eml"))
    post(app, golden, "remove a rule", "/api/rules", {"token": "prizes.example.test", "verdict": "remove"})
    post(app, golden, "a token that is neither address nor domain", "/api/rules", {"token": "lin", "verdict": "keep"})
    post(app, golden, "an unknown verdict", "/api/rules", {"token": "lin@example.test", "verdict": "maybe"})
    golden.note("mailmind_rules.json", disk(world, "mailmind_rules.json"))


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
    ids = [v["id"] for v in app.get("/api/views").json["views"]]
    post(app, golden, "reorder", "/api/views", {"verb": "reorder", "ids": list(reversed(ids))})
    post(app, golden, "delete the first", "/api/views", {"verb": "delete", "id": lin})
    post(app, golden, "delete a builtin", "/api/views", {"verb": "delete", "id": "__unread__"})
    post(app, golden, "an unknown verb", "/api/views", {"verb": "rename"})
    golden.note("mailmind_views.json", disk(world, "mailmind_views.json"))


def test_accounts_add_test_edit_delete(app, golden, world):
    entry = world.server.add_account(DEE, password="dee-password")
    add = {"email": DEE, "type": "imap", "host": entry["host"], "port": entry["port"],
           "starttls": True, "alias": "Dee", "credential": {"style": "plain", "value": "dee-password"}}
    post(app, golden, "add Dee", "/api/accounts/add", add)
    post(app, golden, "add Dee again", "/api/accounts/add", add)
    post(app, golden, "add one with no credential", "/api/accounts/add", {"email": "eve@example.test", "type": "imap",
                                                                         "host": "127.0.0.1"})
    post(app, golden, "add one with no host", "/api/accounts/add",
         {"email": "eve@example.test", "type": "imap", "credential": {"style": "plain", "value": "x"}})
    post(app, golden, "add one that isn't an address", "/api/accounts/add",
         {"email": "eve", "type": "imap", "host": "127.0.0.1", "credential": {"style": "plain", "value": "x"}})
    post(app, golden, "test Dee", "/api/accounts/test", {"email": DEE})
    post(app, golden, "test an unknown account", "/api/accounts/test", {"email": "zed@example.test"})
    post(app, golden, "test a draft with the wrong password", "/api/accounts/test",
         {"draft": {**add, "credential": {"style": "plain", "value": "wrong"}}})
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


def test_sweep_moves_the_junk_buckets_out_of_the_inbox(app, golden, world):
    post(app, golden, "sweep Ada, dry run", "/api/sweep", {"account": w.ADA, "dry_run": True})
    since = world.mark()
    post(app, golden, "sweep Ada", "/api/sweep", {"account": w.ADA})
    golden.note("server writes", world.writes(since))
    golden.note("Ada's INBOX on the server", sorted(
        str(m.headers()["Message-ID"]) for m in world.server.messages(w.ADA, "INBOX").values()))
    golden.record("the audit trail", app.get("/api/audit"))


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


def test_known_contacts(app, golden):
    post(app, golden, "who is known", "/api/contacts/known", {"addrs": [
        "lin@example.test", "LIN@example.test", "Lin Mori <lin@example.test>", "noor@studio.example.test",
        "winner@prizes.example.test", "ada@example.test", "stranger@example.test", "not an address", ""]})
    post(app, golden, "addrs that isn't a list", "/api/contacts/known", {"addrs": "lin@example.test"})
    post(app, golden, "too many addrs", "/api/contacts/known", {"addrs": [f"p{i}@example.test" for i in range(101)]})
