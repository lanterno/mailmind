"""Headers as a sync stores them: folds, an unknown charset, and a Message-ID whose line break must never reach IMAP."""
from fixtures import world as w
from stored_rows import q
from world_helpers import late


def test_hwf_a_folded_message_id_never_carries_its_line_break_to_imap(app, world):
    # A sender can fold the Message-ID header, and the folded value is stored as it came, line break
    # and all. Undo finds a message by it, and imaplib sends an argument as it is: the break would
    # end the SEARCH and start whatever followed as a command of its own.
    uid = world.deliver(w.ADA, "INBOX", late(Message_ID="<fold@example.test>\r\n A1 DELETE INBOX"))
    [row] = q("SELECT pk, message_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert "\r\n" in row["message_id"]
    since = len(world.server.commands)
    assert app.post("/api/archive", w.copy_of(row["pk"])).json["ok"]
    assert app.post("/api/undo", {}).status == 409                    # it can't be found by that ID
    lines = [c["line"] for c in world.server.commands[since:]]
    assert not [line for line in lines if "DELETE" in line or "fold@" in line], lines


def test_hwf_a_header_in_an_unknown_charset_decodes_as_utf8(world):
    import base64
    word = "=?x-unknown?B?" + base64.b64encode("Caf\u00e9 menu".encode()).decode() + "?="
    uid = world.deliver(w.ADA, "INBOX", late(Subject=word))
    [r] = q("SELECT subject FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert r["subject"] == "Caf\u00e9 menu"


def test_hwf_a_folded_subject_and_sender_are_stored_on_one_line(world):
    uid = world.deliver(w.ADA, "INBOX", b'From: "Bo\r\n Lin" <bo@example.test>\r\nTo: ada@example.test\r\n'
                                        b'Subject: Items\r\n for next week\r\nMessage-ID: <fold-1@example.test>\r\n'
                                        b'Date: Mon, 02 Nov 2026 10:00:00 +0000\r\n\r\nHello\r\n')
    [r] = q("SELECT subject, from_name, from_addr FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
            w.ADA, uid)
    assert r == {"subject": "Items for next week", "from_name": "Bo Lin", "from_addr": "bo@example.test"}
