"""A search can be held to one conversation: "this conversation only", beside the choice of this account or all of them."""
import urllib.parse

from fixtures import world as w

LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
QUILT = "ada-inbox-11-html-cid.eml"             # Lin's too, but another conversation


def found(app, query, **more):
    params = urllib.parse.urlencode({"q": query, **more})
    answer = app.get(f"/api/search?{params}").json
    assert answer["ok"], answer
    return answer["items"]


def subjects(items):
    return {i["subject"] for i in items}


def test_it_finds_only_that_conversation(app, world):
    thread = world.row(LUNCH[0])["thread_id"]
    inside = found(app, "from:lin", scope="account", account=w.ADA, thread=thread)
    everywhere = found(app, "from:lin", scope="account", account=w.ADA)
    assert {i["thread_id"] for i in inside} == {thread}
    assert len(inside) < len(everywhere)
    assert world.row(QUILT)["subject"] not in subjects(inside)


def test_it_still_searches_inside_it(app, world):
    thread = world.row(LUNCH[0])["thread_id"]
    assert found(app, "nosuchwordanywhere", scope="account", account=w.ADA, thread=thread) == []


def test_it_stays_in_its_account(app, world):
    thread = world.row(LUNCH[0])["thread_id"]
    assert found(app, "from:lin", scope="account", account=w.BEA, thread=thread) == []


def test_an_operator_only_search_is_held_to_it_too(app, world):
    thread = world.row(LUNCH[0])["thread_id"]
    items = found(app, "in:INBOX", scope="account", account=w.ADA, thread=thread)
    assert items and {i["thread_id"] for i in items} == {thread}


def test_a_blank_thread_is_no_scope(app):
    assert found(app, "from:lin", scope="all", thread="")
