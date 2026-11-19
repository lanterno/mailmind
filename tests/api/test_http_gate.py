"""Who waits at the gate, over HTTP: the Screener lists each sender not yet let in or screened out whose mail is in the
account's Inbox. Filing a sender's last message there, from any place (a brief's card, a list, the reader), lets them go
from the gate, so its count drops by one, and an Undo brings them back; a sender with more in the Inbox still waits."""
from fixtures import world as w

CAFE = "ada-inbox-10-raw-utf8.eml"                                           # the café's one message in Ada's Inbox
NOORS = ("ada-inbox-06-outlook-invite.eml", "ada-inbox-07-forward-rfc822.eml")   # Noor's two


def gate(app):
    """How many wait at Ada's gate, and who."""
    said = app.get(f"/api/screener?account={w.ADA}").json
    return said["total"], {s["from_addr"] for s in said["items"]}


def test_filing_a_senders_last_inbox_message_lets_them_go_from_the_gate_and_undo_brings_them_back(app, world):
    total, senders = gate(app)
    assert "hello@cafe.example.test" in senders
    assert app.post("/api/archive", world.copy(CAFE)).status == 200
    assert gate(app) == (total - 1, senders - {"hello@cafe.example.test"})
    assert app.post("/api/undo", {}).json["ok"]
    assert gate(app) == (total, senders)


def test_a_sender_with_more_in_the_inbox_still_waits(app, world):
    total, senders = gate(app)
    assert "noor@studio.example.test" in senders
    assert app.post("/api/trash", world.copy(NOORS[0])).status == 200
    assert gate(app) == (total, senders)
