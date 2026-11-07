"""A message with no thread is read on its own: a reply drafted from it, and a draft ahead fingerprinted by it."""
from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.domain import fingerprint
from mailmind.bootstrap import assistant
from mailmind.bootstrap.mailbox import store

QUILT = "ada-inbox-11-html-cid.eml"


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
    res, code = assistant.replies.draft(w.ADA, pk=world.pk(QUILT))
    assert (code, res["body"]) == (200, "What a lovely quilt.")
    [asked] = ai.requests
    assert "The finished quilt" in asked.prompt


def test_a_reply_to_a_message_that_has_gone_has_nothing_to_reply_to(world, ai):
    assert assistant.replies.draft(w.ADA, pk=999999) == ({"ok": False, "error": "nothing to reply to"}, 404)
    assert ai.requests == []
