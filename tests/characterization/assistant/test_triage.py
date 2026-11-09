"""Triage: a failed call is asked again, an unusable reply is stored once, and with no key no mail is sent."""
from fakes import anthropic_api as fa
from assistant_world import CLIENT, QUILT
from stored_rows import q


def test_hwf_triage_retries_a_failed_call_but_stores_an_unusable_reply_once(world, ai):
    from mailmind.bootstrap import assistant

    def reply(req):
        if "Subject: Contract draft v2" in req.prompt:
            return fa.error(400, "invalid_request_error", "bad request")
        if "Subject: The finished quilt" in req.prompt:
            return fa.answer({})                     # a reply with nothing usable in it
        return None
    ai.responder = reply
    assistant.assessments.summarize_pending(limit=100, workers=1)
    assert q("SELECT model FROM assessments WHERE message_key=?", world.row(QUILT)["message_key"]) == [{"model": "parse-error"}]
    assert q("SELECT model FROM assessments WHERE message_key=?", world.row(CLIENT)["message_key"]) == []
    ai.requests.clear()
    assistant.assessments.summarize_pending(limit=100, workers=1)
    assert [r.prompt.count("Subject: Contract draft v2") for r in ai.requests] == [1]


def test_without_a_key_triage_sends_no_mail(world, anthropic_api):
    from mailmind.bootstrap import assistant
    assistant.assessments.summarize_pending(limit=3, workers=1)
    assert anthropic_api.requests == []
