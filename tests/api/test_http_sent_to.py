"""Whom a listed message went to, over HTTP: every row carries the first two people its To and then its Cc name, each once,
with the name it was written with, and how many more (`sent_to`, `sent_to_more`), so a row of a person's own mail can name
them rather than the person. A folder's list, one conversation a row, a message's row alone, its conversation, search,
Snoozed, Waiting and the digest carry the same, read from what the store keeps of To and Cc."""
from urllib.parse import quote, urlencode

from fixtures import world as w
from mailmind.bootstrap.mailbox import store

LIN = {"name": "Lin Mori", "addr": "lin@example.test"}
BEA = {"name": None, "addr": "bea@example.test"}
LUNCH = "ada-inbox-01-lunch.eml"
REPLY = "ada-sent-01-lunch-reply.eml"


def mail(message_id, subject, to=None, cc=None, sender="Ada Park <ada@example.test>"):
    head = [f"From: {sender}", *([f"To: {to}"] if to else []), *([f"Cc: {cc}"] if cc else []), f"Subject: {subject}",
            "Date: Mon, 02 Nov 2026 10:00:00 +0000", f"Message-ID: {message_id}", "MIME-Version: 1.0",
            "Content-Type: text/plain; charset=utf-8"]
    return ("\r\n".join(head) + "\r\n\r\nSee you all there.\r\n").encode()


def sent_to_four(world):
    """Ada's mail to four people, two in To and two in Cc, in her Sent; its UID there."""
    return world.deliver(w.ADA, "Sent", mail("<four@ada.example.test>", "Plans for four",
                                             to="Lin Mori <lin@example.test>, bea@example.test",
                                             cc='"Stone, Cy" <cy@example.test>, Dee <DEE@example.test>, LIN@example.test'),
                         flags=("\\Seen",))


def listed(app, folder, account=w.ADA, **more):
    return app.get("/api/messages?" + urlencode({"account": account, "folder": folder, "limit": 500, **more})).json["items"]


def whom(row):
    return row["sent_to"], row["sent_to_more"]


def row_of(rows, subject):
    [row] = [r for r in rows if r["subject"] == subject]
    return row


def test_a_folders_rows_name_the_first_two_people_each_went_to_and_count_the_rest(app, world):
    uid = sent_to_four(world)
    rows = listed(app, "Sent")
    assert whom(row_of(rows, "Plans for four")) == ([LIN, BEA], 2)
    assert whom(row_of(rows, "Re: Lunch on Thursday?")) == ([LIN], 0)
    assert whom(row_of(listed(app, "Sent", group="thread"), "Plans for four")) == ([LIN, BEA], 2)
    alone = app.get("/api/message/row?" + urlencode({"account": w.ADA, "folder": "Sent", "uid": uid})).json["row"]
    assert whom(alone) == ([LIN, BEA], 2)


def test_mail_from_someone_else_names_whom_it_went_to_too(app, world):
    lunch = row_of(listed(app, "INBOX"), "Lunch on Thursday?")
    assert [p["addr"] for p in lunch["sent_to"]] == [w.ADA]


def test_a_name_decoded_from_an_encoded_word_keeps_its_comma(app, world):
    world.deliver(w.ADA, "Sent", mail("<mori@ada.example.test>", "Notes", to="=?utf-8?q?Mori=2C_Lin?= <lin@example.test>"))
    assert whom(row_of(listed(app, "Sent"), "Notes")) == ([{"name": "Mori, Lin", "addr": "lin@example.test"}], 0)


def test_a_draft_that_names_no_one_yet_names_no_one(app, world):
    world.deliver(w.ADA, "Drafts", mail("<empty@ada.example.test>", "Half a thought"), flags=("\\Draft",))
    assert whom(row_of(listed(app, "Drafts"), "Half a thought")) == ([], 0)


def test_a_conversation_and_search_name_them_as_the_list_does(app, world):
    sent_to_four(world)
    reply = row_of(listed(app, "Sent"), "Re: Lunch on Thursday?")
    thread = app.get("/api/thread?" + urlencode({"account": w.ADA, "thread_id": reply["thread_id"]})).json["items"]
    assert [whom(m) for m in thread if m["key"] == reply["key"]] == [([LIN], 0)]
    assert [m["sent_to"][0]["addr"] for m in thread if m["key"] != reply["key"]] == [w.ADA] * (len(thread) - 1)
    found = app.get(f"/api/search?scope=all&q={quote('Plans for four')}").json["items"]
    assert [whom(r) for r in found if r["subject"] == "Plans for four"] == [([LIN, BEA], 2)]


def with_server_stopped(app, *statements):
    with app.stopped():
        c = store.conn()
        try:
            for sql, args in statements:
                c.execute(sql, args)
            c.commit()
        finally:
            c.close()


def test_snoozed_waiting_and_the_digest_name_them_as_the_list_does(app, world):
    sent_to_four(world)
    four = row_of(listed(app, "Sent"), "Plans for four")
    lunch = row_of(listed(app, "INBOX"), "Lunch on Thursday?")
    with_server_stopped(app, (
        "INSERT INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts, due_ts, created_at) "
        "VALUES(?,?,'sent','due',1793500000,1793500000,1793600000,'2026-11-01T00:00:00+00:00')", (w.ADA, four["thread_id"])), (
        "INSERT OR REPLACE INTO assessments(message_key, summary, importance, category, action, model, created_at) "
        "VALUES(?,'A summary.',4,'personal','Say yes','stub','2026-11-02T11:00:00+00:00')", (lunch["key"],)))
    for waiting in (row_of(listed(app, "__waiting__"), "Plans for four"),
                    row_of(app.get("/api/waiting-on-others").json["items"], "Plans for four")):
        assert whom(waiting) == ([LIN, BEA], 2)
    [brief] = [it for it in app.get("/api/digest").json["items"] if it["key"] == lunch["key"]]
    assert whom(brief) == whom(lunch)
    assert app.post("/api/snooze", {"key": four["key"], "folder": "Sent", "uid": four["uid"], "preset": "tomorrow"}).status == 200
    assert whom(row_of(listed(app, "__snoozed__"), "Plans for four")) == ([LIN, BEA], 2)
