"""Drafts: a save and the copy it leaves on the server, what counts as a draft and which copy is ours to remove, what the
lists show at once, saves that overlap, and a draft on its way out."""
import threading
import time
from datetime import timedelta
from urllib.parse import urlencode

import pytest

import gate
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.compose import book
from mailmind.platform.workers import gates
from stored_rows import q, rows
from world_helpers import gone, new


def test_hwf_a_recorded_tail_is_cut_only_when_it_starts_with_a_blank_line():
    from mailmind.compose import api as compose
    quote = "On Mon, Lin wrote:\n> hi\n"
    assert compose.resume_split("Mine\n\n" + quote, {"own_text": None, "tail_text": "\n\n" + quote}, False) == (
        "Mine", "\n\n" + quote, "tail")
    assert compose.resume_split("Mine\n" + quote, {"own_text": None, "tail_text": quote}, False) == (
        "Mine\n" + quote.rstrip("\n"), "", "whole")


def test_hwf_a_draft_going_out_now_cant_be_reopened(app, world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    saved = app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"],
                                    "subject": "Held", "text": "Going out."}).json
    ingest.sync_folder(accounts.get(w.ADA), "Drafts")
    [d] = rows("SELECT message_key AS key, folder FROM messages WHERE account=? AND folder='Drafts' AND uid=?",
               w.ADA, saved["uid"])
    c = store.conn()
    oid, _ = book.outbox_insert(c, {"account": w.ADA, "idempotency_key": "k-held", "message_id": "<held@example.test>",
                                     "envelope": "[]", "recipients": "{}", "subject": "Held",
                                     "raw": b"x", "next_attempt_at": 0, "draft_id": saved["draft_id"],
                                     "created_at": "2026-11-02T12:00:00+00:00"})
    c.execute("UPDATE outbox SET state='sending' WHERE id=?", (oid,))
    c.commit()
    c.close()
    r = app.post("/api/draft/resume", d)
    assert r.status == 423


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
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return dict(book.draft_get(c, did))
    finally:
        c.close()


def test_saves_that_overlap_leave_one_copy_and_the_row_on_it(app, world, monkeypatch):
    # Both read the row they replace, so a read taken before the gate would expunge the UID the
    # first had already replaced, and leave the first's copy in Drafts with nothing tracking it.
    drafts = world.server.mailbox(w.ADA, "Drafts")
    before = len(drafts.messages)
    did = app.post("/api/draft", _swatch("cmp-2")).json["draft_id"]
    real, waiting = gates.run, []

    def run(scope, job, **kw):
        waiting.append(scope)
        return real(scope, job, **kw)
    monkeypatch.setattr(gates, "run", run)
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
    from mailmind.bootstrap.compose import outbox
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
    a = app.post("/api/draft", _swatch("cmp-5")).json
    real, scopes = gates.run, []
    b, back = {}, {}

    def run(scope, job, **kw):
        scopes.append(scope)
        if len(scopes) == 1:                                       # another tab's saves get in first
            b.update(app.post("/api/draft", _swatch("cmp-5", account=w.BEA, draft_id=a["draft_id"])).json)
            back.update(app.post("/api/draft", _swatch("cmp-5", draft_id=a["draft_id"], text="Back from Ada.",
                                                       autosave=True)).json)
        return real(scope, job, **kw)
    monkeypatch.setattr(gates, "run", run)
    r = app.post("/api/draft", _swatch("cmp-5", draft_id=a["draft_id"], text="Typing.")).json
    assert r["ok"] and r["draft_id"] == a["draft_id"]
    assert scopes[0] == w.ADA and scopes[-1] == gates.EVERY_ACCOUNT
    assert not world.server.find(w.BEA, "Drafts", b["message_id"])
    assert world.server.find(w.ADA, "Drafts", back["message_id"])


def body_of(pk):
    """Where the reader opens the copy the row `pk` is, by its key and folder."""
    return "/api/message/body?" + urlencode(w.copy_of(pk))


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
    assert second.status == 200 and second.json["pushed"] is False    # the journal holds it, and asks again
    assert drafts_on_server(world) == [first["uid"]]


def test_hwf_a_lost_connection_during_a_draft_save_keeps_the_last_copy(app, world):
    first = app.post("/api/draft", new(idempotency_key="d-1")).json
    world.server.drop("APPEND", user=w.ADA)
    second = app.post("/api/draft", {**new(idempotency_key="d-1"), "draft_id": first["draft_id"]})
    assert second.status == 200 and second.json["pushed"] is False
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
    from mailmind.bootstrap.mailbox import ingest
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
                                     "uidvalidity": store.get_uidvalidity(c, email, folder),
                                     "mode": "new", "subject": "Filed", "created_at": "2026-10-01T09:00:00",
                                     "updated_at": "2026-10-01T09:00:00"})
        c.commit()
    finally:
        c.close()
    return did


@pytest.mark.parametrize("folder", ["Contract drafts", "Clients/Draft"])
def test_hwf_received_mail_in_a_folder_named_for_drafts_is_no_draft(app, world, folder):
    pk, _ = filed(world, folder)
    assert "is_draft" not in app.get(body_of(pk)).json
    since = world.mark()
    refused = app.post("/api/draft/resume", w.copy_of(pk))
    assert (refused.status, refused.json["type"]) == (400, "/problems/refused")
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
    assert "is_draft" not in app.get(body_of(pk)).json     # no list yet: the name says nothing
    assert app.get(f"/api/folders?account={w.ADA}").json["folders"]
    assert app.get(body_of(pk)).json["is_draft"]
    assert app.post("/api/draft/resume", w.copy_of(pk)).json["ok"]


def test_hwf_a_message_the_server_flags_a_draft_offers_edit_in_any_folder(app, world):
    pk, uid = filed(world, "Half written", name="<half@example.test>", flags=(w.SEEN, w.DRAFT))
    assert app.get(body_of(pk)).json["is_draft"]
    assert app.post("/api/draft/resume", w.copy_of(pk)).json["ok"]
    did = q("SELECT id FROM drafts WHERE folder='Half written'")[0]["id"]
    assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["deleted"]
    assert world.server.find(w.ADA, "Half written", "<half@example.test>") == []


def listed(app, folder="Drafts", email=w.ADA):
    return {i["subject"]: i["key"] for i in app.get(
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
    assert app.post("/api/draft/resume", w.copy_of(pk)).json["ok"]
    did = q("SELECT id FROM drafts WHERE folder='Half written'")[0]["id"]
    assert app.post("/api/draft", new(subject="Half done", draft_id=did)).json["ok"]
    assert q("SELECT pk FROM messages WHERE pk=?", pk) == []
    assert world.server.find(w.ADA, "Half written", "<half@example.test>") == []
