"""A thread's summary across a renumber, and of a message with no thread or Message-ID to go by."""
from fixtures import world as w
from assistant_world import LUNCH, LUNCH_MID, QUILT, execute, pk_of, renumber_inbox


def test_a_thread_summary_stays_fresh_across_a_renumber(app, world, ai):
    lunch, before = world.copy(LUNCH), world.pk(LUNCH)      # what the page holds, by key and folder
    assert app.post("/api/ai/summarize", lunch).status == 200
    renumber_inbox(world, w.ADA)
    assert pk_of(LUNCH_MID) != before
    r = app.post("/api/ai/summarize", lunch).json
    assert r["cached"] is True
    assert r["stale"] is False


def test_a_message_without_a_thread_id_can_be_summarized(app, world, ai):
    pk = world.pk(QUILT)
    execute("UPDATE messages SET thread_id=NULL WHERE pk=?", pk)
    first = app.post("/api/ai/summarize", w.copy_of(pk))
    assert first.status == 200 and first.json["scope"] == "message" and len(ai.requests) == 1
    assert app.post("/api/ai/summarize", w.copy_of(pk)).json["cached"] is True     # and it finds its summary again
    assert len(ai.requests) == 1


def test_a_message_with_neither_thread_nor_message_id_can_be_summarized(app, world, ai):
    pk = world.pk(QUILT)
    execute("UPDATE messages SET thread_id=NULL, message_id='' WHERE pk=?", pk)
    assert app.post("/api/ai/summarize", w.copy_of(pk)).status == 200
