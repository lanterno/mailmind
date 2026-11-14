"""A backlog of triage through the Claude API's Message Batches, at half the price, as the assess job sends it and brings
it in (`assistant.assessments`' batch_submit and batch_collect, from its round): against the fake Claude API, which holds
a batch in progress until a test ends it.

A backlog is 100 messages waiting with no summary. It goes as one batch, but for the 20 newest, which are triaged at once
at full price, and while it is out everything older than the newest it took waits for the next. Its answers are asked
for every five minutes, counted at half price once they are in, and the batch is then removed from the API. One the API
didn't take, or no longer has, gives its messages back. The rows are the same for either server, so a batch another
process left is brought in too, and MAILMIND_NO_BATCHES stops only the sending.
"""
import json
import re
from datetime import timedelta

from fakes import anthropic_api as fa
from fixtures import world as w
from stored_rows import q

BACKLOG = 105
NOW_TS = 1_793_620_800          # conftest.NOW, as the server's clock counts it


def note(n):
    """The n-th message of a backlog: a plain one from a colleague, an hour after the one before."""
    day, hour = divmod(n, 24)
    return (f"From: Pat Lee <pat@example.test>\r\nTo: Ada Park <ada@example.test>\r\nSubject: Backlog note {n}\r\n"
            f"Date: {day + 1:02d} Oct 2026 {hour:02d}:00:00 +0000\r\nMessage-ID: <backlog-{n}@pat.example.test>\r\n"
            "MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            f"Note {n} on the quarterly figures.\r\n").encode()


def backlog(app, world, n=BACKLOG):
    """`n` messages put on Ada's server at once, and one sync of her inbox."""
    for i in range(n):
        world.server.deliver(w.ADA, "INBOX", note(i))
    app.work("sync", w.ADA, "INBOX")


def subject(key):
    return q("SELECT subject FROM messages WHERE message_key=? LIMIT 1", key)[0]["subject"]


def heard(ai):
    """What the fake was asked since the last look, then forgotten: each batch call as its method and path, and the
    subjects of the messages triaged one call each."""
    batch = [f"{r.method} {r.path}" for r in ai.requests if r.path.startswith(fa.BATCHES)]
    single = sorted(re.search(r"^Subject: (.*)$", r.prompt, re.M).group(1) for r in ai.requests
                    if r.path == "/v1/messages")
    ai.requests.clear()
    return {"batch": batch, "triaged one at a time": single}


def batches():
    return q("SELECT remote_id, feature, model, state, requests, estimate_micros, cutoff_ts, created_at, polled_at, "
             "ended_at, prompt_version FROM ai_batches ORDER BY id")


def held():
    return sorted(subject(r["message_key"]) for r in q("SELECT message_key FROM ai_batch_items"))


def batch_waiting(app):
    return app.get("/api/digest").json["ai"].get("batch_waiting")


def said(subjects):
    """What is kept of each message named by its subject."""
    marks = ",".join("?" * len(subjects))
    return q(f"""SELECT m.subject, a.summary, a.importance, a.category, a.action, a.model, a.served_by, a.prompt_version,
                        a.outcome, a.created_at
                 FROM messages m JOIN assessments a ON a.message_key = m.message_key
                 WHERE m.subject IN ({marks}) ORDER BY m.ts""", *subjects)


def ledger():
    return q("SELECT day, feature, model, served, outcome, calls, input_tokens, output_tokens, cost_micros, last_ts "
             "FROM ai_usage ORDER BY feature, outcome, served")


def test_a_backlog_goes_as_a_batch_and_its_answers_come_in_at_half_the_price(app, golden, world, ai):
    backlog(app, world)
    ai.requests.clear()
    app.work("assess")
    [sent] = [r.body["requests"] for r in ai.requests if r.method == "POST" and r.path == fa.BATCHES]
    golden.note("the batch's requests", {"count": len(sent), "newest": [subject(int(r["custom_id"])) for r in sent[:2]],
                                         "oldest": [subject(int(r["custom_id"])) for r in sent[-2:]],
                                         "one": sent[0]["params"]})
    golden.note("what the round asked", heard(ai))
    golden.note("the batch out", batches())
    golden.note("what it holds", {"messages": len(held()), "the status says": batch_waiting(app)})

    # It has ended, but nobody asks for five minutes; then its answers come in, and the API lets it go.
    keys = [r["custom_id"] for r in sent]
    ai.end_batch(results={
        keys[0]: fa.succeeded({"summary": "Pat's figures.", "importance": 9, "category": "work-job", "action": "Read"},
                              input_tokens=1000, output_tokens=100),
        keys[1]: fa.succeeded({"summary": "More figures.", "importance": 2, "category": "info", "action": ""},
                              input_tokens=800, output_tokens=60),
        keys[2]: {"type": "succeeded", "message": fa.answer("", stop="refusal")[1]},
        keys[3]: fa.succeeded("Just prose."),
        keys[4]: fa.errored("invalid_request_error"),
        keys[5]: fa.errored("overloaded_error"),
        keys[6]: fa.EXPIRED,
        keys[7]: fa.CANCELED,
    })
    app.clock.shift(timedelta(seconds=299))
    app.work("assess")
    golden.note("asked before five minutes", heard(ai))
    app.clock.shift(timedelta(seconds=1))
    app.work("assess")
    golden.note("asked at five minutes, and the unanswered triaged at full price", heard(ai))
    golden.note("what was kept", said([subject(int(k)) for k in keys[:8]]))
    golden.note("the batch done", batches())
    golden.note("what it holds now", {"messages": len(held()), "the status says": batch_waiting(app)})
    golden.note("removed from the API", ai.deleted)
    golden.note("the ledger", ledger())
    golden.note("left with no summary", q("SELECT count(*) AS n FROM messages m LEFT JOIN assessments a "
                                          "ON a.message_key = m.message_key WHERE m.is_junk = 0 "
                                          "AND m.message_key IS NOT NULL AND a.message_key IS NULL"))


def test_a_batch_the_api_did_not_take_is_never_sent_again_and_gives_its_messages_back(app, golden, world, ai):
    """Sending one is never retried: no key says "the same batch", so another try could pay for it twice. One a crash
    left while it was being sent gives its messages back after fifteen minutes."""
    backlog(app, world)
    lost = ["Backlog note 0", "Backlog note 1"]
    with app.stopped():                     # a batch a crash cut off sixteen minutes ago, as its row says
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        try:
            batch = c.execute("INSERT INTO ai_batches(feature, model, state, requests, estimate_micros, cutoff_ts, "
                              "created_at, prompt_version) VALUES ('triage-batch', 'claude-opus-5-5', 'submitting', 2, "
                              "9000, 0, ?, 2)", (NOW_TS - 16 * 60,)).lastrowid
            c.execute("INSERT INTO ai_batch_items(message_key, batch_id) SELECT message_key, ? FROM messages "
                      "WHERE subject IN (?, ?)", (batch, *lost))
            c.commit()
        finally:
            c.close()
    ai.requests.clear()
    ai.queue(fa.error(500, "api_error", "try later"))
    app.work("assess")
    found = heard(ai)
    golden.note("the batch calls", found["batch"])
    golden.note("triaged one at a time", {"count": len(found["triaged one at a time"]),
                                          "the cut-off batch's": [s for s in found["triaged one at a time"] if s in lost]})
    golden.note("batches left", batches())
    golden.note("the ledger", ledger())


def test_a_batch_the_api_no_longer_has_gives_its_messages_back_and_the_next_goes(app, golden, world, ai):
    backlog(app, world)
    app.work("assess")
    first = batches()[0]["remote_id"]
    ai.batches.clear()                      # another key's, or removed: it answers 404
    ai.requests.clear()
    app.clock.shift(timedelta(minutes=5))
    app.work("assess")
    golden.note("what the round asked", heard(ai))
    golden.note("the batches", {"first": first, "now": batches()})


def test_a_batch_takes_only_its_share_of_what_the_daily_limit_leaves(app, golden, world, ai):
    """Half of what the limit leaves, by an estimate that errs high; the rest of the backlog waits for the next one."""
    backlog(app, world)
    with app.stopped():
        with open(world.dir / "mailmind.config.json") as f:
            config = json.load(f)
        config["ai_daily_limit_usd"] = 0.1
        with open(world.dir / "mailmind.config.json", "w") as f:
            json.dump(config, f)
    ai.requests.clear()
    app.work("assess")
    [sent] = [r.body["requests"] for r in ai.requests if r.method == "POST" and r.path == fa.BATCHES]
    golden.note("the batch", {"requests": len(sent), "out": batches()})
    golden.note("triaged one at a time", len(heard(ai)["triaged one at a time"]))


def test_a_batch_another_process_left_comes_in_and_the_switch_stops_only_the_sending(app, golden, world, ai):
    """The rows are the same for either server: a batch the other sent, before the prompt's version was kept, comes in
    under its own model and feature. MAILMIND_NO_BATCHES keeps the round from sending another, and a backlog then goes
    one message at a time."""
    backlog(app, world)
    left = ["Backlog note 3", "Backlog note 4", "Backlog note 5"]
    app.env["MAILMIND_NO_BATCHES"] = "1"
    with app.stopped():
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        try:
            batch = c.execute("INSERT INTO ai_batches(remote_id, feature, model, state, requests, estimate_micros, "
                              "cutoff_ts, created_at, polled_at) VALUES ('msgbatch_left', 'triage-batch', "
                              "'claude-sonnet-5-5', 'running', 3, 12000, 0, ?, ?)",
                              (NOW_TS - 86_400, NOW_TS - 3600)).lastrowid
            c.execute("INSERT INTO ai_batch_items(message_key, batch_id) SELECT message_key, ? FROM messages "
                      "WHERE subject IN (?, ?, ?)", (batch, *left))
            keys = [r["message_key"] for r in c.execute(
                "SELECT message_key FROM messages WHERE subject IN (?, ?, ?) ORDER BY ts", left)]
            c.commit()
        finally:
            c.close()
    ai.batches["msgbatch_left"] = {"requests": [{"custom_id": str(k), "params": {}} for k in keys],
                                   "status": "ended", "results": {}}
    ai.end_batch("msgbatch_left", results={
        str(keys[0]): fa.succeeded({"summary": "Left behind.", "importance": 3, "category": "info", "action": ""},
                                   input_tokens=500, output_tokens=50),
        str(keys[1]): fa.succeeded({"summary": "Also left.", "importance": 4, "category": "personal", "action": ""}),
        str(keys[2]): fa.EXPIRED,
    })
    ai.requests.clear()
    app.work("assess")
    found = heard(ai)
    golden.note("the batch calls", found["batch"])
    golden.note("triaged one at a time", {"count": len(found["triaged one at a time"]),
                                          "of the batch's": [s for s in found["triaged one at a time"] if s in left]})
    golden.note("what was kept", said(left))
    golden.note("the batches", batches())
    golden.note("the ledger", [r for r in ledger() if r["feature"] == "triage-batch"])
