"""What the brief, the delta and the review keep of a message, and what a stored one says when the mail has moved on: they
keep a message by its key and the folder it was read from, and a read names each by the copy there is now. Briefs and
Reviews on stored mail of their own and a model of their own."""
import json
from datetime import datetime, timezone

import pytest
import time_machine

from assistant_ports import Model, Said, Uow, Week, calls_on
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.review import Reviews

ADA, BEA = "ada@example.test", "bea@example.test"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
DAY = "2026-11-02"
NARRATIVE = "Lin is waiting on lunch plans for Thursday, so reply today."
WEEK = "A steady week: mail came in, some went out, and one thread from Lin is still waiting on you."


def triaged(pk, key, message_id, **over):
    return {"pk": pk, "message_key": key, "folder": "INBOX", "message_id": message_id, "from_name": "Lin",
            "from_addr": "lin@example.test", "subject": f"Message {pk}", "summary": "Stub triage.", "importance": 5,
            "category": "action-needed", "action": "Reply", **over}


LUNCH = triaged(3, 30, "<lunch-3@lin.example.test>")
QUILT = triaged(4, 40, "<quilt@lin.example.test>")


class Inbox:
    """Triaged mail, as a brief and a delta read it, and where each message's copy is now (`copies`, by key)."""

    def __init__(self, *rows):
        self.rows, self.window = list(rows), list(rows)
        self.copies = {r["message_key"]: {"folder": r["folder"], "uid": r["pk"] + 100} for r in rows}

    def brief_rows(self, c, accounts, n):
        return self.rows[:n]

    def delta_rows(self, c, accounts, since_ts, n):
        return self.window[:n]

    def copy_now(self, c, key, folder):
        return self.copies.get(key)


@pytest.fixture(autouse=True)
def monday():
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def model():
    return Model()


@pytest.fixture
def said():
    return Said()


def briefs_over(model, said, inbox):
    return Briefs(calls_on(model), said, Uow(), inbox)


def copy(row, inbox):
    return {"key": row["message_key"], **inbox.copies[row["message_key"]]}


# -- the brief -------------------------------------------------------------------------------------

def brief_of(model, briefs, *rows):
    model.answer({"narrative": NARRATIVE, "groups": [{"label": "Lunch", "count": len(rows), "pks": [r["pk"] for r in rows]}]})
    return briefs.build({BEA, ADA})


def test_a_stored_brief_says_which_messages_it_holds_by_key(model, said):
    brief_of(model, briefs_over(model, said, Inbox(LUNCH)), LUNCH)
    [((accounts, day), row)] = said.briefs.items()
    assert (accounts, day, row["narrative"]) == (f"{ADA},{BEA}", DAY, NARRATIVE)
    assert json.loads(row["groups_json"]) == [{"label": "Lunch", "messages": [[30, "INBOX"]]}]


def test_a_stored_brief_counts_what_it_still_holds(model, said):
    inbox = Inbox(LUNCH, QUILT)
    briefs = briefs_over(model, said, inbox)
    assert brief_of(model, briefs, LUNCH, QUILT)["groups"][0]["count"] == 2
    del inbox.copies[QUILT["message_key"]]                # its row has gone
    [group] = briefs.build({ADA, BEA}, generate=False)["groups"]
    assert (group["count"], group["messages"]) == (1, [copy(LUNCH, inbox)])


# -- the review ------------------------------------------------------------------------------------

def needing(row):
    """`row` as needs_you_rows answers it."""
    return {**row, "uid": row["pk"] + 100, "account": ADA, "thread_id": f"t-{row['pk']}"}


@pytest.fixture
def week():
    week = Week()
    week.needs = [needing(LUNCH)]
    week.copies = {LUNCH["message_key"]: {"folder": "INBOX", "uid": 103}}
    return week


def reviews_over(model, said, week):
    return Reviews(calls_on(model), said, Uow(), week)


def review_of(model, reviews):
    model.answer({"narrative": WEEK, "next": ["Answer Lin"]})
    return reviews.build({ADA})


def named(items):
    return [{"key": i["key"], "folder": i["folder"], "uid": i["uid"]} for i in items]


def test_a_stored_review_leaves_out_a_message_that_has_gone(model, said, week):
    reviews = reviews_over(model, said, week)
    lunch = {"key": 30, "folder": "INBOX", "uid": 103}
    assert lunch in named(review_of(model, reviews)["needs"])
    week.needs, week.copies = [], {}                     # its row has gone
    again = reviews.build({ADA})
    assert lunch not in named(again["needs"])
    assert again["stale"] is True                        # and it says the mail changed


def test_a_stored_review_keeps_what_it_said_when_the_mail_moves_on(model, said, week):
    reviews = reviews_over(model, said, week)
    first = review_of(model, reviews)
    week.received += 1
    again = reviews.build({ADA})
    assert (again["narrative"], again["next"], again["stats"]) == (WEEK, ["Answer Lin"], first["stats"])
    assert (again["cached"], again["stale"], len(model.sent)) == (True, True, 1)


# -- the delta -------------------------------------------------------------------------------------

def news(n):
    return triaged(50 + n, 500 + n, f"<new-{n}@loom.example.test>", from_name="Loom", subject=f"New {n}", importance=1,
                   category="newsletter")


def delta(model, briefs, headline):
    model.answer({"headline": headline, "items": [{"who": "Loom", "gist": "New issue", "importance": 1}]})
    return briefs.build_delta({ADA}, 0)


def test_a_delta_read_again_costs_nothing(model, said):
    briefs = briefs_over(model, said, Inbox(news(1)))
    assert delta(model, briefs, "One new.")["cached"] is False
    again = delta(model, briefs, "Not asked for.")
    assert (again["cached"], again["headline"], len(model.sent)) == (True, "One new.", 1)
    assert briefs.build_delta({ADA}, 0, generate=False)["headline"] == "One new."


def test_a_delta_is_written_again_when_a_message_in_its_window_is_swapped(model, said):
    inbox = Inbox(LUNCH, news(1))
    briefs = briefs_over(model, said, inbox)
    assert delta(model, briefs, "Before.")["headline"] == "Before."
    inbox.window = [LUNCH, news(2)]                      # new-1 gone and new-2 come: as many as before
    after = delta(model, briefs, "After.")
    assert (after["headline"], after["cached"], after["count"]) == ("After.", False, 2)
