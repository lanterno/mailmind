"""An event a person confirmed, as a METHOD:PUBLISH file: for their own calendar, inviting no one, and nothing a stranger's mail
said can add a line to it."""

import pytest

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


@pytest.mark.parametrize("fields, said", [
    ({"title": "  "}, "an event needs a title"),
    ({"start": ""}, "that start isn’t a date this can use"),
    ({"start": "tomorrow"}, "that start isn’t a date this can use"),
    ({"start": "2199-01-01T10:00"}, "that start isn’t a date this can use"),
    ({"end": "2026-11-12T11:00"}, "that end doesn’t come after the start"),
    ({"end": "whenever"}, "that end doesn’t come after the start"),
])
def test_what_wont_do_is_refused_in_words(app, world, fields, said):
    r = confirm(app, world, **fields)
    assert (r.status, r.json["detail"]) == (400, said)


def test_an_empty_end_is_no_end(app, world):
    assert field(confirm(app, world, end="").json["ics"], "DTEND") == "DTEND:20261112T133000"


def test_a_message_with_no_copy_left_is_not_found(app):
    r = app.post("/api/calendar/event", {"key": 987654, "title": "x", "start": "2026-11-12"})
    assert r.status == 404
