"""/api/calendar/event: an event a person confirmed, answered as a METHOD:PUBLISH file for their own calendar, and nothing sent.
What the file holds and what is refused are tests/unit/calendar/test_publish.py's."""
from mailmind.bootstrap.mailbox import store

LUNCH = "ada-inbox-02-lunch-confirm.eml"


def lines(text):
    assert text.endswith("\r\n")
    return text[:-2].split("\r\n")


def unfolded(text):
    return text.replace("\r\n ", "")


def field(text, name):
    return next((ln for ln in lines(unfolded(text)) if ln.startswith(name)), None)


def confirm(app, world, **kw):
    return app.post("/api/calendar/event", {**world.copy(LUNCH), "title": "Lunch", "start": "2026-11-12T12:30",
                                            "end": "2026-11-12T13:30", "place": "Cafe", **kw})


def test_the_route_answers_the_file(app, world):
    r = confirm(app, world)
    assert r.status == 200 and r.json["filename"] == "event.ics"
    assert "METHOD:PUBLISH" in r.json["ics"]
    assert field(r.json["ics"], "ORGANIZER") == "ORGANIZER:mailto:" + world.row(LUNCH)["account"]


def test_the_route_sends_nothing(app, world):
    confirm(app, world)
    c = store.conn()
    try:
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    finally:
        c.close()


def test_a_refusal_is_the_400_in_its_own_words(app, world):
    """What won't do, and the words for each, are Publishing's (tests/unit/calendar/test_publish.py)."""
    r = confirm(app, world, title="  ")
    assert (r.status, r.json["detail"]) == (400, "an event needs a title")


def test_a_message_with_no_copy_left_is_not_found(app):
    r = app.post("/api/calendar/event", {"key": 987654, "title": "x", "start": "2026-11-12"})
    assert r.status == 404
