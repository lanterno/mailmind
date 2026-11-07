"""What a confirm can't be talked into: a bulk command's plan and a cleanup's token, replayed or sent with something else in
hand. test_do_plans.py has what a plan does (confirmed once, ten minutes, what it files), test_confirm_token.py each thing
a token names, and test_http_cleanup.py the unsubscribe it guards.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.application import plans
from mailmind.assistant.domain import action_plan
from mailmind.bootstrap import assistant, curation
from mailmind.bootstrap.curation import safe_http
from mailmind.bootstrap.mailbox import store

LUNCH_END = "ada-inbox-02-lunch-confirm.eml"
FROM_LIN = "from:lin@example.test in:INBOX"
LOOM = "news@loom.example.test"
SPINDLE = "deals@spindle.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


def preview(app, ai, verb="archive", flt=FROM_LIN, **more):
    ai.queue(fa.answer({"intent": "act", "verb": verb, "filter": flt, **more}))
    answer = app.post("/api/ai/do-plan", {"cmd": f"{verb} that"}).json
    assert answer["intent"] == "act" and answer["count"] > 0, answer
    return answer


def folders_of(world, name):
    return [box for _, box, _, _ in world.where(name)["server"]]


# ── a plan, replayed ─────────────────────────────────────────────────────────────────────────────

def test_confirms_sent_together_carry_it_out_once(app, world, ai):
    """A double click, or a page sending it again: the one that takes the plan files, and each other hears it has gone."""
    plan = preview(app, ai)
    since = world.mark()
    answers, start = [], threading.Barrier(4)

    def confirm():
        start.wait(10)
        answers.append(app.post("/api/ai/do-confirm", {"plan": plan["plan"]}).status)
    threads = [threading.Thread(target=confirm) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(answers) == [200, 410, 410, 410]
    assert len([x for x in world.writes(since) if "MOVE" in x or "COPY" in x]) == plan["count"]


def test_the_book_gives_a_plan_to_one_taker():
    book, taken, start = plans.PlanBook(), [], threading.Barrier(8)
    made = [book.open("archive", None, []) for _ in range(action_plan.KEPT)]

    def take():
        start.wait(10)
        taken.extend(p.id for p in (book.take(m.id) for m in made) if p)
    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(taken) == sorted(m.id for m in made)


def test_a_plan_past_its_time_doesnt_come_back_with_the_clock(app, world, ai):
    """A confirm that came too late has ended it, not only been turned away."""
    plan = preview(app, ai)
    app.clock.shift(timedelta(seconds=action_plan.TTL))
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"]}).status == 410
    app.clock.shift(timedelta(seconds=-action_plan.TTL))
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"]}).status == 410


def test_a_restart_ends_every_plan(app, world, ai, monkeypatch):
    plan = preview(app, ai)
    monkeypatch.setattr(assistant.bulk, "_DO_PLANS", plans.PlanBook())
    since = world.mark()
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"]}).status == 410
    assert world.writes(since) == []


# ── a plan, and something else in hand ───────────────────────────────────────────────────────────

def test_an_id_nearly_right_is_none_and_leaves_the_plan_standing(app, world, ai):
    plan = preview(app, ai)
    since = world.mark()
    id_ = plan["plan"]
    for wrong in (id_ + "x", id_[:-1], id_.swapcase(), " " + id_, [id_], {"plan": id_}):
        assert app.post("/api/ai/do-confirm", {"plan": wrong}).status == 410, wrong
    assert world.writes(since) == []
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"]}).status == 200


@pytest.mark.parametrize("verb", ["trash", "junk", "snooze"])
def test_a_confirm_does_what_the_preview_said_whatever_else_it_names(app, world, ai, verb):
    plan = preview(app, ai)
    more = {"verb": verb, "preset": "tomorrow", "dest": "Trash", "folder": "Trash", "filter": "in:INBOX", "account": w.BEA, "count": 99,
            "pks": [1, 2, 3], "messages": [[1, "INBOX"]]}
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"], **more}).json["count"] == plan["count"]
    assert folders_of(world, LUNCH_END) == ["Archive"]
    assert folders_of(world, "ada-inbox-05-receipt.eml") == ["INBOX"]


def test_a_snooze_wakes_when_its_preview_said_whatever_its_confirm_names(app, world, ai):
    plan = preview(app, ai, "snooze", snooze_preset="next_week")
    assert app.post("/api/ai/do-confirm", {"plan": plan["plan"], "preset": "tomorrow"}).json["count"] == plan["count"]
    c = store.conn()
    woken = {r["snoozed_until"] for r in c.execute("SELECT snoozed_until FROM messages WHERE folder='INBOX' AND from_addr='lin@example.test'")}
    c.close()
    assert woken == {int(datetime(2026, 11, 9, 8, tzinfo=timezone.utc).timestamp())}


def test_a_plan_of_one_verb_cant_be_confirmed_as_another_made_since(app, world, ai):
    archive, trash = preview(app, ai), preview(app, ai, "trash")
    assert archive["plan"] != trash["plan"]
    app.post("/api/ai/do-confirm", {"plan": archive["plan"], "verb": "trash"})
    assert folders_of(world, LUNCH_END) == ["Archive"]


# ── a cleanup's token, at the route ──────────────────────────────────────────────────────────────

@pytest.fixture
def posts(monkeypatch):
    made = []

    def post(url, _redirects=1):
        made.append(url)
        return True, 200, "ok"
    monkeypatch.setattr(safe_http, "one_click_post", post)
    return made


@pytest.fixture
def issue(app, world):
    """A newer Loom issue offering one click, its body stored, in Ada's Archive where a sweep leaves it."""
    mid = "<issue-219@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              List_Unsubscribe_Post="List-Unsubscribe=One-Click", Subject="Issue 219"), (w.SEEN,))
    c = store.conn()
    pk = c.execute("SELECT pk FROM messages WHERE message_id=?", (mid,)).fetchone()["pk"]
    c.close()
    assert app.get(f"/api/message/{pk}/body").status == 200


def execute(app, token, addr=LOOM, **more):
    return app.post("/api/cleanup/execute", {"from_addr": addr, "do_unsubscribe": True, "confirm_token": token, **more}).json


def token_of(app, addr=LOOM):
    return app.get(f"/api/cleanup/sender?addr={addr}").json["confirm_token"]


def test_a_token_replayed_fires_once(app, issue, posts):
    token = token_of(app)
    assert execute(app, token)["status"] == "unsubscribed_oneclick"
    for _ in range(2):
        execute(app, token)
    assert len(posts) == 1


def test_another_senders_token_sends_nothing(app, issue, posts):
    assert execute(app, token_of(app, SPINDLE))["status"] == "no_token"
    assert posts == []


def test_a_token_changed_in_any_way_sends_nothing(app, issue, posts):
    token = token_of(app)
    time_, _, mac = token.partition(".")
    for wrong in (token + "0", token[:-1], time_ + ".", f"{int(time_) + 1}.{mac}", mac, token.upper(), time_ + ".é"):
        assert execute(app, wrong)["status"] == "no_token", wrong
    assert posts == []


def test_a_token_from_before_a_restart_sends_nothing(app, issue, posts, monkeypatch):
    """It's signed with a secret each run draws afresh."""
    with monkeypatch.context() as last_run:
        last_run.setattr(curation.cleanup, "_secret", lambda: "what-the-last-run-drew")
        token = token_of(app)
    assert execute(app, token)["status"] == "no_token"
    assert posts == []


def test_a_token_goes_no_further_than_its_sender_on_the_same_list(app, world, issue, posts):
    """Two senders offering one way out, so all that tells their tokens apart is the sender each names."""
    token = token_of(app)
    twin = "other@loom.example.test"
    way_out = app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID="<twin-1@loom.example.test>", Date="Mon, 02 Nov 2026 07:30:00 +0000",
                                              From=f"Loom <{twin}>", List_Unsubscribe_Post="List-Unsubscribe=One-Click", Subject="Twin"), (w.SEEN,))
    c = store.conn()
    pk = c.execute("SELECT pk FROM messages WHERE message_id='<twin-1@loom.example.test>'").fetchone()["pk"]
    c.close()
    assert app.get(f"/api/message/{pk}/body").status == 200
    assert app.get(f"/api/cleanup/sender?addr={twin}").json["unsub"] == way_out      # or this says nothing
    assert execute(app, token, addr=twin)["status"] == "no_token"
    assert posts == []
