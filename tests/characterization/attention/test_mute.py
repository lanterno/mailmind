"""A mute tells the server too: a reply landing in a muted thread is marked read there."""
from fixtures import world as w
from stored_rows import q


def test_hwf_a_reply_in_a_muted_thread_is_marked_read_on_the_server_too(app, world):
    # Decision D6: other clients agree. A reply that lands in a muted thread is read in MailMind at
    # once, and on the server once the queue has told it.
    from mailmind.bootstrap.mailbox import commands
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    assert app.post("/api/mute", w.copy_of(lunch["pk"])).json["ok"]
    reply = w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<late-mute@example.test>",
                      In_Reply_To=lunch["message_id"])
    uid = world.deliver(w.ADA, "INBOX", reply)
    [r] = q("SELECT seen, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert (r["seen"], r["thread_id"]) == (1, lunch["thread_id"])
    assert w.SEEN not in world.server.messages(w.ADA, "INBOX")[uid].flags
    commands.drain()
    assert w.SEEN in world.server.messages(w.ADA, "INBOX")[uid].flags
