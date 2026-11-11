"""A message with no thread is read on its own: a reply drafted from it, and a draft ahead fingerprinted by it. The use cases on
a Mail of their own."""
import problems
from answered import answered
from assistant_ports import Model, Uow, calls_on
from mailmind.assistant.application.context import ContextAssembler
from mailmind.assistant.application.prepared_replies import PreparedReplies
from mailmind.assistant.application.reply_draft import ReplyDrafts
from mailmind.assistant.domain import fingerprint

ADA = "ada@example.test"
QUILT = {"pk": 11, "account": ADA, "thread_id": None, "message_id": "<quilt@lin.example.test>", "body_fetched": 1,
         "subject": "The finished quilt", "from_name": "Lin", "from_addr": "lin@example.test",
         "date_iso": "2026-11-01T09:00:00+00:00", "body_text": "Here it is at last: the finished quilt.", "snippet": ""}
GONE = 999_999


class Mail:
    """One message, alone in its thread."""

    def message_stamp(self, c, pk):
        return QUILT if pk == QUILT["pk"] else None

    def message_to_reply_to(self, c, pk):
        return [QUILT] if pk == QUILT["pk"] else []


def test_a_lone_message_is_fingerprinted_by_its_id_and_its_body():
    drafting = PreparedReplies(calls_on(Model()), lambda: None, None, Uow(), Mail(), None, lambda addr: True)
    assert drafting._draft_fp(None, ADA, None, QUILT["pk"]) == fingerprint.message(QUILT["message_id"], QUILT["body_fetched"])
    assert drafting._draft_fp(None, ADA, None, GONE) == fingerprint.message(None, 0)


def replies(model):
    mail = Mail()
    return ReplyDrafts(calls_on(model), ContextAssembler(mail), Uow(), mail)


def test_a_reply_to_a_lone_message_is_drafted_from_that_message():
    model = Model({"body": "What a lovely quilt."})
    assert replies(model).draft(ADA, pk=QUILT["pk"])["body"] == "What a lovely quilt."
    assert QUILT["body_text"] in model.prompt


def test_a_reply_to_a_message_that_has_gone_has_nothing_to_reply_to():
    model = Model()
    assert answered(replies(model).draft, ADA, pk=GONE) == (problems.not_found("nothing to reply to"), 404)
    assert model.sent == []
