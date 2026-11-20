"""A draft keeps whatever was typed in it, as Mail keeps one: a recipient that isn't an address yet never stops it being
saved. It reopens as it was typed, from the save this Mac keeps and from its copy in the server's Drafts, and a send of
it is refused in words that name what isn't an address. What was typed reaches the draft's bytes only on one line, in a
header of MailMind's own, so no line break typed in a recipient makes a header; and nothing typed in Bcc reaches them.

The builder's own rules are tests/unit/compose/test_typed_recipients.py; what Python built, byte for byte, the Rust core
builds (core/crates/mm-compose/tests/fixtures/builder_oracle.json).
"""
import email
import email.policy

import pytest

from fixtures import world as w

LIN = "Lin Mori <lin@example.test>"
HOSTILE = "bob@example\r\nBcc: eve@example.test\r\nX-Injected: yes"
ON_ONE_LINE = "bob@example Bcc: eve@example.test X-Injected: yes"


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def draft(**kw):
    return {"account": w.ADA, "mode": "new", "to": [LIN], "subject": "Fabric order", "text": "Hi Lin,\n\nThe linen came in.",
            **kw}


def saved(app, body):
    """Save `body` as a draft, as ⌘S does: its id, once it is saved here and in the server's Drafts."""
    r = app.post("/api/draft", body)
    assert (r.status, r.json["ok"], r.json["pushed"]) == (200, True, True), r.json
    return r.json["draft_id"]


def on_the_server(world, did):
    """The draft's copy in Ada's Drafts on the server: its bytes."""
    [at] = rows("SELECT folder, uid FROM drafts WHERE id=?", did)
    return world.server.messages(w.ADA, at["folder"])[at["uid"]].raw


def from_drafts(app, did):
    """The draft reopened from its copy in the Drafts list, as a click on it there does."""
    app.work("sync", w.ADA, "Drafts")
    [copy] = rows("SELECT m.pk FROM drafts d JOIN messages m ON m.account=d.account AND m.folder=d.folder AND m.uid=d.uid "
                  "WHERE d.id=?", did)
    return app.post("/api/draft/resume", w.copy_of(copy["pk"]))


def recipients(r):
    assert r.status == 200, r.json
    return {field: r.json[field] for field in ("to", "cc", "bcc")}


@pytest.mark.parametrize("field, typed, said", [
    ("to", [LIN, "bob@example"], "“bob@example” in To isn’t a valid address."),
    ("cc", ["Cy <cy@example.test>", "cy@"], "“cy@” in Cc isn’t a valid address."),
    ("bcc", ["Quiet <quiet@example.test>", "eve at example"], "“eve at example” in Bcc isn’t a valid address."),
], ids=["to", "cc", "bcc"])
def test_a_draft_with_an_address_that_isnt_one_is_saved_reopens_as_typed_and_is_refused_at_send(app, world, field, typed, said):
    did = saved(app, draft(**{field: typed}))
    as_typed = {"to": [LIN], "cc": [], "bcc": [], field: typed}
    assert recipients(app.post("/api/draft/resume", {"draft_id": did})) == as_typed
    assert recipients(from_drafts(app, did)) == as_typed

    sent = app.post("/api/send", draft(**{field: typed}, draft_id=did, idempotency_key="k-typed"))
    assert (sent.status, sent.json["detail"]) == (400, said)
    assert rows("SELECT id FROM outbox") == []
    assert [r["id"] for r in rows("SELECT id FROM drafts")] == [did]


def test_what_isnt_an_address_is_kept_beside_the_addresses_and_bcc_nowhere_in_the_bytes(app, world):
    did = saved(app, draft(to=[LIN, "bob@example"], cc=["cy@"], bcc=["Quiet <quiet@example.test>", "eve at example"]))
    msg = email.message_from_bytes(on_the_server(world, did), policy=email.policy.default)
    assert (str(msg["To"]), msg["Cc"]) == (LIN, None)
    assert (msg.get_all("X-MailMind-Draft-To"), msg.get_all("X-MailMind-Draft-Cc")) == (["bob@example"], ["cy@"])
    assert msg.get_all("Bcc") is None
    raw = on_the_server(world, did).lower()
    assert b"quiet" not in raw and b"eve at example" not in raw


def test_a_line_break_typed_in_a_recipient_stays_on_one_line_and_makes_no_header(app, world):
    did = saved(app, draft(to=[LIN, HOSTILE], cc=["cy@\nexample test"]))
    raw = on_the_server(world, did)
    head = raw.split(b"\r\n\r\n", 1)[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg.get_all("Bcc") is None and msg.get_all("X-Injected") is None, head
    assert str(msg["To"]) == LIN
    assert msg.get_all("X-MailMind-Draft-To") == [ON_ONE_LINE]
    assert msg.get_all("X-MailMind-Draft-Cc") == ["cy@ example test"]
    as_typed = {"to": [LIN, ON_ONE_LINE], "cc": ["cy@ example test"], "bcc": []}
    assert recipients(app.post("/api/draft/resume", {"draft_id": did})) == as_typed
    assert recipients(from_drafts(app, did)) == as_typed

    sent = app.post("/api/send", draft(to=[LIN, HOSTILE], draft_id=did))
    assert (sent.status, sent.json["detail"]) == (400, f"“{ON_ONE_LINE}” in To isn’t a valid address.")
    assert rows("SELECT id FROM outbox") == []
