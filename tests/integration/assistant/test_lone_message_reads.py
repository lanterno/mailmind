"""A message with no thread is read on its own: a reply drafted from it, and a draft ahead fingerprinted by it."""
from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.adapters import sqlite as answers
from mailmind.bootstrap import assistant
from mailmind.bootstrap.mailbox import store

QUILT = "ada-inbox-11-html-cid.eml"
LUNCH = "ada-inbox-02-lunch-confirm.eml"
PHRASE = "12:30 is perfect"


def test_a_lone_message_with_no_body_here_is_read_from_its_server(world, ai):
    pk = world.pk(LUNCH)
    c = store.conn()
    c.execute("UPDATE messages SET body_text=NULL, snippet=NULL, body_fetched=0 WHERE pk=?", (pk,))
    c.execute("DELETE FROM bodies WHERE message_pk=?", (pk,))
    c.commit()
    c.close()
    ai.queue(fa.answer({"body": "See you there."}))
    assert assistant.replies.draft(w.ADA, pk=pk)["ok"]
    [asked] = ai.requests
    assert PHRASE in asked.prompt


def test_drafting_ahead_passes_over_mail_on_its_way_out(world):
    pk = world.pk(QUILT)
    c = store.conn()
    answers.save_summary(c, pk, "Needs a reply.", 4, "personal", "reply", "claude-opus-5-5", "2026-11-02T00:00:00+00:00")
    c.commit()
    assert pk in [r["pk"] for r in store.draft_candidates(c, 0)]
    store.mark_pending_removal(c, pk, 1)
    c.commit()
    assert pk not in [r["pk"] for r in store.draft_candidates(c, 0)]
    c.close()
