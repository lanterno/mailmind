"""A message with no thread is read on its own: a reply drafted from it, and a draft ahead fingerprinted by it."""
from answered import answered
from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.adapters import sqlite as answers
from mailmind.assistant.domain import fingerprint
from mailmind.bootstrap import assistant
from mailmind.bootstrap.mailbox import store

QUILT = "ada-inbox-11-html-cid.eml"
LUNCH = "ada-inbox-02-lunch-confirm.eml"
PHRASE = "12:30 is perfect"


def test_a_lone_message_is_fingerprinted_by_its_id_and_its_body(world):
    pk = world.pk(QUILT)
    c = store.conn()
    try:
        row = store.get_message(c, pk)
        assert assistant.drafting._draft_fp(c, w.ADA, None, pk) == fingerprint.message(row["message_id"], row["body_fetched"])
        assert assistant.drafting._draft_fp(c, w.ADA, None, 999999) == fingerprint.message(None, 0)
    finally:
        c.close()


def test_a_reply_to_a_lone_message_is_drafted_from_that_message(world, ai):
    ai.queue(fa.answer({"body": "What a lovely quilt."}))
    assert assistant.replies.draft(w.ADA, pk=world.pk(QUILT))["body"] == "What a lovely quilt."
    [asked] = ai.requests
    assert "The finished quilt" in asked.prompt


def test_a_reply_to_a_message_that_has_gone_has_nothing_to_reply_to(world, ai):
    assert answered(assistant.replies.draft, w.ADA, pk=999999) == ({"ok": False, "error": "nothing to reply to"}, 404)
    assert ai.requests == []


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
