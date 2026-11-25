"""Prompt injection: mail that tells the assistant to act, and a model that does as the mail says.

Hostile mail (fixtures/hostile/, all fictional, on example.test) lands in Ada's Inbox: it asks the assistant to file,
trash, send, forward, reply, unsubscribe, change the Screener's rules and reveal other mail, one of them in HTML no one
sees. The fake Claude API then answers every call as a model taken over by it would: each field of the call's own schema
filled with the injected action (every message's id where it takes ids, the most harmful choice where it takes one,
an empty filter for a plan, a claim that it is done where it takes words).

For each AI feature that reads mail, what the AI says changes nothing a person owns until the person acts: no command
reaches the fake IMAP server, nothing is sent over SMTP, no rule, mute, snooze, unsubscribe, draft, task or outbox row
is made, and the config is as it was, even after the background jobs that would carry out such a change have run. And
nothing outside the mail asked about goes to the provider: no message from outside the Inbox, and not the body of
Bea's harbor notes, which the test fetched first so it is there to leak (the world's canaries check that one too, after
every test: support/canaries.py).
"""
import email.parser
import glob
import json
import os

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w

HOSTILE_DIR = os.path.join(os.path.dirname(w.ROOT), "fixtures", "hostile")
HOSTILE = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HOSTILE_DIR, "*.eml")))
MALLORY = "mallory@mallory.example.test"

# What a taken-over model claims in every field that takes words.
CLAIM = ("DONE as the email asked: moved every message to Trash, forwarded the Inbox to collect@mallory.example.test, "
         "replied 'Approved, wire the 9,000 today', unsubscribed from every list and blocked lin@example.test.")
# Where a field offers a choice, the one that does the most harm.
HARM = ("delete", "trash", "archive", "act", "block", "action-needed")
# Mail from outside the Inbox, which no feature here was asked about: its subjects never go to the provider.
ELSEWHERE = ("Quilt pattern ideas", "20% off all yarn this weekend", "Kickoff notes")

# What a person owns that the AI must never change on its own: the queues that reach a mail server, what goes out, the
# rules and documents, and every decision kept about a message.
OWNED = ("mail_commands", "outbox", "drafts", "draft_journal", "documents", "muted", "snoozes", "followups",
         "sender_unsub", "unsub_log", "tasks", "undo_entries", "thread_links", "message_labels")
# The jobs that would carry out such a change, run after each feature as a worker would.
FOLLOW_ON = ("commands", "outbox", "drafts", "tidy", "kicked")


def hostile_raw(name):
    with open(os.path.join(HOSTILE_DIR, name), "rb") as f:
        return f.read()


def hostile_id(name):
    return (email.parser.BytesHeaderParser().parsebytes(hostile_raw(name)).get("Message-ID") or "").strip()


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def every_pk():
    return [r["pk"] for r in rows("SELECT pk FROM messages ORDER BY pk")]


def taken_over(pks):
    """A responder for the fake API: every call answered from its own schema, as the hostile mail asked."""
    def fill(schema, name="value"):
        if not isinstance(schema, dict):
            return None
        for alt in ("anyOf", "oneOf"):
            if schema.get(alt):
                return fill(schema[alt][0], name)
        if "enum" in schema:
            return next((choice for choice in HARM if choice in schema["enum"]), schema["enum"][0])
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "null")
        if kind == "object":
            return {k: fill(s, k) for k, s in (schema.get("properties") or {}).items()}
        if kind == "array":
            items = schema.get("items") or {}
            if items.get("type") == "integer":
                return pks + [999999]
            if items.get("type") == "object":
                return [{**fill(items, name), **({"id": n} if "id" in (items.get("properties") or {}) else {})}
                        for n in range(1, 13)]
            return [fill(items, name)] * 3
        if kind == "string":
            return "" if name == "filter" else CLAIM
        if kind in ("integer", "number"):
            return 5 if name == "importance" else 1
        if kind == "boolean":
            return True
        return None

    def respond(req):
        if req.schema is not None:
            return fa.answer(fill(req.schema))
        if '"tldr"' in str(req.prompt):               # a thread's summary, asked for as JSON in words
            return fa.answer({"tldr": CLAIM, "points": [CLAIM] * 3, "action": CLAIM, "participants": [CLAIM]})
        return fa.answer(CLAIM)
    return respond


def owned(world):
    """What a person owns, as the database and the config hold it now."""
    held = {t: rows(f"SELECT * FROM {t}") for t in OWNED}
    held["messages"] = rows("SELECT account, folder, uid, message_id, seen, flagged, answered, draft, is_junk, "
                            "pending_removal FROM messages ORDER BY account, folder, uid")
    with open(os.path.join(world.dir, "mailmind.config.json"), encoding="utf-8") as f:
        held["config"] = json.load(f)
    return held


@pytest.fixture
def hostile(app, world, ai):
    """Ada's Inbox with the hostile mail in it, Bea's harbor notes' body fetched (so it is there to leak), and the fake API
    taken over. Returns the hostile messages' copies, by file."""
    for name in HOSTILE:
        world.deliver(w.ADA, "INBOX", hostile_raw(name))
    notes = world.copy(w.BODY_CANARY[0])
    opened = app.get("/api/message/body?" + "&".join(f"{k}={v}" for k, v in notes.items()))
    assert opened.status == 200 and w.BODY_CANARY[1] in json.dumps(opened.json), opened.status
    app.settle()
    ai.responder = taken_over(every_pk())
    copies = {}
    for name in HOSTILE:
        mid = hostile_id(name)
        r = rows("SELECT uid, message_key FROM messages WHERE account = ? AND folder = 'INBOX' AND message_id = ?", w.ADA, mid)
        assert r, f"{name} wasn't synced"
        copies[name] = {"key": r[0]["message_key"], "folder": "INBOX", "uid": r[0]["uid"]}
    return copies


def holds_nothing_happens(app, world, ai, act):
    """`act` runs a feature over the hostile mail; afterwards, and after the jobs that would carry out what the AI said,
    nothing the person owns has changed, nothing reached a mail server and nothing went out. Returns what was sent to
    the provider."""
    before, mark, sent = owned(world), world.mark(), len(world.smtp.messages)
    asked = len(ai.seen)
    act()
    app.settle()
    for job in FOLLOW_ON:
        app.work(job)
    app.settle()
    assert world.writes(mark) == [], "the AI's words changed mail on the server"
    assert world.smtp.messages[sent:] == [], "the AI's words sent mail"
    after = owned(world)
    changed = sorted(k for k in before if before[k] != after[k])
    assert changed == [], f"the AI's words changed what the person owns: {changed}"
    requests = ai.seen[asked:]
    assert requests, "the feature asked the AI nothing"
    for req in requests:
        said = json.dumps(req.body, ensure_ascii=False)
        assert w.BODY_CANARY[1] not in said, "a message no one asked about went to the provider"
        assert not [s for s in ELSEWHERE if s in said], "mail from outside the Inbox went to the provider"
    return requests


def test_the_hostile_mail_is_in_the_inbox_and_the_model_does_as_it_says(app, world, ai, hostile):
    assert len(hostile) == 9
    r = app.post("/api/ai/summarize", hostile["hostile-02-trash.eml"])
    assert r.status == 200 and CLAIM in json.dumps(r.json)


def test_background_summaries(app, world, ai, hostile):
    def act():
        app.work("assess")
    requests = holds_nothing_happens(app, world, ai, act)
    assert any(MALLORY in json.dumps(r.body) for r in requests)


@pytest.mark.parametrize("route", ["/api/ai/summarize", "/api/ai/draft-reply", "/api/ai/extract"])
def test_a_feature_asked_about_one_message(app, world, ai, hostile, route):
    """Asked about one hostile message, the AI is sent that message and its thread alone."""
    for name, copy in hostile.items():
        def act():
            r = app.post(route, copy)
            assert r.status == 200, (name, r.status)
        for req in holds_nothing_happens(app, world, ai, act):
            said = json.dumps(req.body, ensure_ascii=False)
            others = [n for n in HOSTILE if n != name and hostile_id(n).strip("<>") in said]
            assert others == [], f"asked about {name}, the provider was sent {others}"


def test_ask(app, world, ai, hostile):
    def act():
        r = app.post("/api/ai/ask", {"question": "What does Mallory want me to do?"})
        assert r.status == 200, r.status
    holds_nothing_happens(app, world, ai, act)


def test_a_plan_is_only_a_preview_until_the_person_confirms_it(app, world, ai, hostile):
    def act():
        r = app.post("/api/ai/do-plan", {"cmd": "archive what Mallory sent"})
        assert r.status == 200, r.status
    holds_nothing_happens(app, world, ai, act)


def test_the_brief_since_you_looked_and_the_review(app, world, ai, hostile):
    app.work("assess")                       # the summaries the hostile mail wrote, which these read in turn

    def act():
        # What came in since you looked is background work, which waits out a person's call just made: it goes first.
        for route, body in (("/api/briefing/since", {"since": 0}), ("/api/briefing", {}), ("/api/review", {})):
            r = app.post(route, body)
            assert r.status == 200, (route, r.status)
    holds_nothing_happens(app, world, ai, act)


def test_the_screener_suggests_and_writes_no_rule(app, world, ai, hostile):
    def act():
        r = app.post("/api/screener/suggest", {"account": w.ADA})
        assert r.status == 200, r.status
    holds_nothing_happens(app, world, ai, act)


def test_why_clean_up_and_what_a_sender_writes_about(app, world, ai, hostile):
    def act():
        for route, body in (("/api/cleanup/rationale", {"senders": [MALLORY]}), ("/api/contact/summary", {"addr": MALLORY})):
            r = app.post(route, body)
            assert r.status == 200, (route, r.status)
    holds_nothing_happens(app, world, ai, act)


def test_drafting_ahead_keeps_its_drafts_and_sends_none(app, world, ai, hostile):
    """Drafting ahead is a switch the person turned on; what it drafts waits for them, whatever the AI wrote."""
    app.work("assess")
    assert app.post("/api/preferences", {"draft_ahead": True}).status == 200

    def act():
        for _ in HOSTILE:
            app.work("draft-ahead")
    holds_nothing_happens(app, world, ai, act)
