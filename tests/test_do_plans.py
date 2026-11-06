"""A bulk command's confirm carries out what its preview froze: by message key, once, within ten minutes, and
never what a request names. The preview's own guards are in test_hwf_ai.py and test_http_ai.py."""
from datetime import timedelta

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.application import plans

LUNCH_END = "ada-inbox-02-lunch-confirm.eml"    # Lin's last word: <lunch-3@...>, in Ada's Inbox
LUNCH_START = "ada-inbox-01-lunch.eml"          # Lin's first: <lunch-1@...>, in Ada's Inbox too
QUILT = "ada-inbox-11-html-cid.eml"             # Lin's quilt photo: a thread of one
FROM_LIN = "from:lin@example.test in:INBOX"


def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def preview(app, ai, verb="archive", flt=FROM_LIN, **more):
    ai.queue(fa.answer({"intent": "act", "verb": verb, "filter": flt, **more}))
    answer = app.post("/api/ai/do-plan", {"cmd": f"{verb} that"}).json
    assert answer["intent"] == "act", answer
    return answer


def confirm(app, plan, **more):
    return app.post("/api/ai/do-confirm", {"plan": plan["plan"], **more})


def folders(world, name):
    """Where the server holds the message."""
    return [box for _, box, _, _ in world.where(name)["server"]]


def lin_in_inbox():
    return q("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND from_addr='lin@example.test'", w.ADA)


def new_from_lin(n):
    return w.variant(QUILT, Message_ID=f"<later-{n}@lin.example.test>", Subject=f"Later {n}")


# -- what it files ---------------------------------------------------------------------------------

def test_the_preview_names_no_message_and_the_confirm_files_what_it_froze(app, world, ai):
    plan = preview(app, ai)
    assert "pks" not in plan
    n = plan["count"]
    assert n == len(lin_in_inbox()) and n >= 2
    r = confirm(app, plan)
    assert r.status == 200
    assert (r.json["ok"], r.json["count"], r.json["failed"], r.json["gone"]) == (True, n, 0, 0)
    assert lin_in_inbox() == []
    assert folders(world, LUNCH_END) == ["Archive"]


def test_planning_moves_nothing(app, world, ai):
    since = world.mark()
    preview(app, ai)
    assert world.writes(since) == []


def test_a_confirm_ignores_any_list_a_request_names(app, world, ai):
    other = world.pk("ada-inbox-03-newsletter.eml")
    plan = preview(app, ai, flt=f'{FROM_LIN} subject:quilt')
    assert plan["count"] == 1
    assert confirm(app, plan, pks=[other], pk=other, verb="trash").json["count"] == 1
    assert folders(world, "ada-inbox-03-newsletter.eml") == ["INBOX"]
    assert folders(world, QUILT) == ["Archive"]             # archived, as planned, not trashed


def test_a_confirm_files_the_same_messages_after_a_renumber(app, world, ai):
    import accounts
    import ingest
    before = {r["pk"] for r in lin_in_inbox()}
    plan = preview(app, ai)
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    assert not before & {r["pk"] for r in lin_in_inbox()}
    r = confirm(app, plan).json
    assert (r["count"], r["gone"]) == (plan["count"], 0)
    assert lin_in_inbox() == []


def test_mail_that_came_after_the_preview_stays(app, world, ai):
    plan = preview(app, ai)
    world.deliver(w.ADA, "INBOX", new_from_lin(1))
    assert confirm(app, plan).json["count"] == plan["count"]
    assert folders(world, QUILT) == ["Archive"]
    assert [r["subject"] for r in q("SELECT subject FROM messages WHERE folder='INBOX' AND from_addr='lin@example.test'")] == ["Later 1"]


def test_a_message_moved_since_the_preview_is_left_where_it_went(app, world, ai):
    plan = preview(app, ai, verb="trash")
    assert app.post("/api/archive", {"pk": world.pk(QUILT)}).json["ok"]
    r = confirm(app, plan).json
    assert (r["count"], r["gone"]) == (plan["count"] - 1, 1)
    assert folders(world, QUILT) == ["Archive"]


def test_a_message_with_a_move_on_its_way_is_left_alone(app, world, ai):
    plan = preview(app, ai)
    import store
    pk = world.pk(QUILT)
    c = store.conn()
    c.execute("UPDATE messages SET pending_removal=1 WHERE pk=?", (pk,))
    c.commit()
    c.close()
    assert confirm(app, plan).json["gone"] == 1


def test_only_the_message_the_preview_counted_files_not_its_conversation(app, world, ai):
    plan = preview(app, ai, flt=f"{FROM_LIN} subject:re")
    assert plan["count"] == 1
    assert confirm(app, plan).json["count"] == 1
    assert folders(world, LUNCH_END) == ["Archive"]
    assert folders(world, LUNCH_START) == ["INBOX"]


def test_a_capped_plan_files_the_newest(app, world, ai, monkeypatch):
    import server
    monkeypatch.setattr(server, "_DO_CAP", 1)
    plan = preview(app, ai)
    assert plan["capped"] is True and plan["count"] >= 2
    assert confirm(app, plan).json["count"] == 1
    assert len(lin_in_inbox()) == plan["count"] - 1


# -- when it can't -----------------------------------------------------------------------------------

def test_a_plan_can_be_confirmed_once(app, world, ai):
    plan = preview(app, ai)
    assert confirm(app, plan).json["count"] == plan["count"]
    since = world.mark()
    r = confirm(app, plan)
    assert r.status == 410 and r.json["expired"] is True and world.writes(since) == []


def test_a_plan_lasts_ten_minutes(app, world, ai):
    plan = preview(app, ai)
    app.clock.shift(timedelta(seconds=plans.TTL))
    since = world.mark()
    r = confirm(app, plan)
    assert r.status == 410 and r.json["expired"] is True
    assert world.writes(since) == [] and len(lin_in_inbox()) == plan["count"]


def test_a_plan_is_good_up_to_then(app, world, ai):
    plan = preview(app, ai)
    app.clock.shift(timedelta(seconds=plans.TTL - 1))
    assert confirm(app, plan).status == 200


@pytest.mark.parametrize("body", [{}, {"plan": ""}, {"plan": "made-up"}, {"plan": None}, {"plan": ["x"]}])
def test_a_plan_no_one_made_is_refused(app, world, body):
    since = world.mark()
    r = app.post("/api/ai/do-confirm", body)
    assert r.status == 410 and world.writes(since) == []


def test_a_message_the_server_refuses_is_counted_and_the_rest_go_on(app, world, ai):
    plan = preview(app, ai)
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    r = confirm(app, plan).json
    assert (r["count"], r["failed"]) == (plan["count"] - 1, 1) and r["ok"] is True
    assert len(lin_in_inbox()) == 1


def test_a_message_that_raises_is_counted_and_the_rest_go_on(app, world, ai, monkeypatch):
    import server
    plan = preview(app, ai)
    real, calls = server.do_write, []

    def once_broken(path, body):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("something odd")
        return real(path, body)
    monkeypatch.setattr(server, "do_write", once_broken)
    r = confirm(app, plan).json
    assert (r["count"], r["failed"]) == (plan["count"] - 1, 1)


def test_a_plan_whose_messages_have_all_gone_files_nothing(app, world, ai):
    plan = preview(app, ai, flt=f"{FROM_LIN} subject:quilt")
    assert app.post("/api/archive", {"pk": world.pk(QUILT)}).json["ok"]
    since = world.mark()
    r = confirm(app, plan).json
    assert (r["ok"], r["count"], r["gone"], r["undoable"]) == (True, 0, 1, False) and world.writes(since) == []


# -- each verb ---------------------------------------------------------------------------------------

def test_one_undo_reverses_a_whole_plan(app, world, ai):
    plan = preview(app, ai)
    assert confirm(app, plan).json["undoable"] is True
    r = app.post("/api/undo", {}).json
    assert r["ok"] is True and r["reversed"] == plan["count"]
    assert len(lin_in_inbox()) == plan["count"]


@pytest.mark.parametrize("verb,folder", [("trash", "Trash"), ("junk", "Junk")])
def test_it_files_by_the_plans_verb(app, world, ai, verb, folder):
    plan = preview(app, ai, verb=verb, flt=f"{FROM_LIN} subject:quilt")
    assert confirm(app, plan).json["count"] == 1
    assert folders(world, QUILT) == [folder]


@pytest.mark.parametrize("verb,column,value,seen_first", [("read", "seen", 1, 0), ("unread", "seen", 0, 1),
                                                          ("star", "flagged", 1, 0)])
def test_it_marks_by_the_plans_verb(app, world, ai, verb, column, value, seen_first):
    import store
    pk = world.pk(QUILT)
    c = store.conn()
    c.execute("UPDATE messages SET seen=?, flagged=0 WHERE pk=?", (seen_first, pk))
    c.commit()
    c.close()
    plan = preview(app, ai, verb=verb, flt=f"{FROM_LIN} subject:quilt")
    r = confirm(app, plan).json
    assert (r["count"], r["undoable"]) == (1, False)
    assert q(f"SELECT {column} AS v FROM messages WHERE pk=?", pk)[0]["v"] == value
    assert folders(world, QUILT) == ["INBOX"]


def test_it_snoozes_until_the_preset_and_undo_brings_them_back(app, world, ai):
    plan = preview(app, ai, verb="snooze", snooze_preset="next_week")
    assert plan["snooze_preset"] == "next_week"
    r = confirm(app, plan).json
    assert (r["count"], r["undoable"]) == (plan["count"], True)
    assert all(x["snoozed_until"] for x in q("SELECT snoozed_until FROM messages WHERE folder='INBOX' "
                                             "AND from_addr='lin@example.test'"))
    assert app.post("/api/undo", {}).json["ok"] is True
    assert not any(x["snoozed_until"] for x in q("SELECT snoozed_until FROM messages WHERE folder='INBOX' "
                                                 "AND from_addr='lin@example.test'"))


def test_a_snooze_plan_with_nothing_left_snoozes_nothing(app, world, ai):
    plan = preview(app, ai, verb="snooze", flt=f"{FROM_LIN} subject:quilt")
    assert app.post("/api/archive", {"pk": world.pk(QUILT)}).json["ok"]
    r = confirm(app, plan).json
    assert (r["count"], r["gone"], r["undoable"]) == (0, 1, False)


def test_with_the_server_down_a_move_queues_as_it_does_from_a_click(app, world, ai):
    plan = preview(app, ai, flt=f"{FROM_LIN} subject:quilt")
    world.server.stop()
    r = confirm(app, plan).json
    assert (r["count"], r["queued"], r["failed"]) == (1, 1, 0)


# -- the book ----------------------------------------------------------------------------------------

def test_the_book_keeps_the_newest_plans():
    book = plans.PlanBook()
    made = [book.open("archive", None, [(1, "INBOX")]) for _ in range(plans.KEPT + 2)]
    assert book.take(made[0].id) is None and book.take(made[1].id) is None
    assert all(book.take(p.id) == p for p in made[2:])


def test_a_plans_id_is_not_guessable_and_each_is_its_own():
    book = plans.PlanBook()
    a, b = book.open("archive", None, []), book.open("archive", None, [])
    assert a.id != b.id and len(a.id) >= 32
