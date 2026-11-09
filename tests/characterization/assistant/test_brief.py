"""The brief's narrative under its cap, and the messages its groups name, counted and found again after a renumber."""
from fakes import anthropic_api as fa
from fixtures import world as w
from assistant_world import LUNCH, LUNCH_MID, pk_of, renumber_inbox, summarize_as
from stored_rows import q


def brief_narrative(app, world, ai, narrative):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    ai.queue(fa.answer({"narrative": narrative, "groups": []}))
    return app.post("/api/briefing/refresh", {}).json["narrative"]


def test_hwf_a_long_brief_is_kept_whole_under_the_cap(app, world, ai):
    told = "".join(f"Item {i} needs a look today. " for i in range(40)).strip()     # about 1200 chars
    assert 800 < len(told) < 1500
    assert brief_narrative(app, world, ai, told) == told


def test_hwf_a_runaway_brief_ends_on_its_last_whole_sentence(app, world, ai):
    told = "".join(f"Item {i} needs a look today. " for i in range(90))
    got = brief_narrative(app, world, ai, told)
    assert 750 < len(got) <= 1500
    assert told.startswith(got) and got.endswith(".") and told[len(got)] == " "


def test_hwf_a_runaway_brief_without_sentences_ends_on_a_word_and_an_ellipsis(app, world, ai):
    told = "word " * 400
    got = brief_narrative(app, world, ai, told)
    assert got.endswith("\u2026")
    kept = got[:-1]
    assert 750 < len(kept) <= 1500 and told.startswith(kept) and told[len(kept)] == " "


def test_a_brief_chip_counts_the_messages_it_holds(app, world, ai):
    anchor = world.pk(LUNCH)
    summarize_as(anchor, 5, "action-needed", "Reply")
    ai.queue(fa.answer({"narrative": "Lin is waiting on lunch plans for Thursday, so reply today.",
                        "groups": [{"label": "Lunch", "count": 9, "pks": [anchor, 999999]}]}))
    [group] = app.post("/api/briefing", {}).json["groups"]
    assert (group["count"], group["messages"]) == (1, [world.copy(LUNCH)])


def test_a_cached_brief_still_names_its_messages_after_a_renumber(app, world, ai):
    anchor = world.pk(LUNCH)
    summarize_as(anchor, 5, "action-needed", "Reply")
    ai.queue(fa.answer({"narrative": "Lin is waiting on lunch plans for Thursday, so reply today.",
                        "groups": [{"label": "Lunch", "count": 1, "pks": [anchor]}]}))
    assert app.post("/api/briefing", {}).json["groups"][0]["messages"] == [world.copy(LUNCH)]
    renumber_inbox(world, w.ADA)
    assert pk_of(LUNCH_MID) != anchor
    named = [m for g in app.post("/api/briefing", {}).json["groups"] for m in g["messages"]]
    assert [r["message_id"] for m in named
            for r in q("SELECT message_id FROM messages WHERE message_key=? AND folder=?", m["key"], m["folder"])] == [LUNCH_MID]
