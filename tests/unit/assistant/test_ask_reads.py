"""What a question to Ask reads comes from Discovery's read model, which says None for a query the index rejects: the Asker on
a read model of its own."""
import problems
from answered import answered
from assistant_ports import Model, calls_on
from mailmind.assistant.application.ask import Asker

ADA = "ada@example.test"


class Mail:
    """A read model whose index rejects every query."""

    def for_asking(self, match, accounts, limit):
        return None


def test_a_query_the_index_rejects_is_a_bad_query():
    model = Model()
    assert answered(Asker(calls_on(model), Mail()).ask, ADA, "When is lunch?") == (problems.refused("bad query"), 400)
    assert model.sent == []
