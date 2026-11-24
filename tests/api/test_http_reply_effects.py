"""What a reply's delivery tells the rest at once, over HTTP: the thread that waited on the reply leaves Waiting, and the
open tabs hear `followup`, with no minute's round of the follow-ups between.

No worker runs on its own here: the test lets the Undo Send window pass and has the server run one outbox pass, and
never the `follow-ups` job."""
from datetime import timedelta
from urllib.parse import urlencode

from event_stream import Stream
from fixtures import world as w
from mailmind.bootstrap.mailbox import store

CLIENT = "bea-inbox-01-client.eml"         # Ivo's, which Bea hasn't answered: her draft of a reply sits in Drafts


def waiting(app):
    """The threads Bea's Waiting lists."""
    rows = app.get("/api/messages?" + urlencode({"account": w.BEA, "folder": "__waiting__", "limit": 500})).json["items"]
    return [r["thread_id"] for r in rows]


def awaiting_her_reply(app, world):
    """Ivo's thread waiting on Bea's reply, as the minute's round would have found it. Its id."""
    thread = world.row(CLIENT)["thread_id"]
    with app.stopped():
        c = store.conn()
        try:
            c.execute("INSERT INTO followups(account, thread_id, kind, state, last_msg_ts, due_ts, created_at) "
                      "VALUES(?,?,'received','due',1793500000,1793600000,'2026-11-01T00:00:00+00:00')", (w.BEA, thread))
            c.commit()
        finally:
            c.close()
    return thread


def reply_delivered(app, world):
    answer = app.post("/api/send", {"account": w.BEA, "mode": "reply", "original_key": world.key(CLIENT),
                                    "to": ["ivo@harbor.example.test"], "subject": "Re: Contract draft v2",
                                    "text": "Signed, it goes back tomorrow.", "idempotency_key": "k-reply-effects"})
    assert answer.status == 200
    app.clock.shift(timedelta(seconds=answer.json["undo_seconds"] + 1))
    app.work("outbox")


def heard(stream, *topics):
    """What the stream carries until each of `topics` has come: the first data of each topic."""
    got = {}
    while not set(topics) <= set(got):
        lines = dict(line.split(": ", 1) for line in stream.event().split("\n") if ": " in line)
        got.setdefault(lines.get("event"), lines.get("data"))
    return got


def test_a_reply_takes_its_thread_out_of_waiting_at_once(app, world):
    thread = awaiting_her_reply(app, world)
    assert waiting(app) == [thread]
    stream = Stream(app)
    stream.frame()
    reply_delivered(app, world)
    heard(stream, "followup")
    stream.close()
    assert waiting(app) == []
