"""Attention's routes on their router: what the schemas refuse, what they let by, and that a request still counts."""
import pytest

from fixtures import world as w
from mailmind.bootstrap import assistant

BAD_REQUEST = {"ok": False, "error": "bad request"}
LUNCH = "ada-inbox-02-lunch-confirm.eml"


@pytest.mark.parametrize("path, body", [
    ("/api/snooze", {"pks": 5, "preset": "tomorrow"}),
    ("/api/snooze", {"pks": "1,2", "preset": "tomorrow"}),
    ("/api/snooze", {"pks": [1, [2]], "preset": "tomorrow"}),
    ("/api/unsnooze", {"pks": 5}),
    ("/api/unsnooze", {"pks": [10 ** 30]}),
    ("/api/replylater", {"account": w.ADA, "thread_id": "t", "on": "yes"}),
    ("/api/replylater", {"account": w.ADA, "thread_id": "t", "on": 1}),
    ("/api/snooze", {"preset": 5}),
    ("/api/snooze", {"preset": "tomorrow", "until": [1]}),
    ("/api/snooze", {"preset": "tomorrow", "undo_group": 5}),
    ("/api/mute", {"account": w.ADA, "thread_id": "t", "pk": [1]}),
])
def test_a_value_its_model_refuses_is_the_400(app, path, body):
    r = app.post(path, body)
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("field", ["subject", "text", "mode"])
def test_a_field_nothing_here_reads_is_nothing_to_refuse(app, world, field):
    r = app.post("/api/snooze", {"pk": world.pk(LUNCH), "preset": "tomorrow", field: 5})
    assert (r.status, r.json["ok"], r.json["count"]) == (200, True, 1)


def test_what_was_left_out_stays_left_out(app, world):
    """The use case tells a field left out from one that says nothing: with no `on`, reply later pins."""
    r = app.post("/api/replylater", {"pk": world.pk(LUNCH)})
    assert (r.status, r.json["on"]) == (200, True)
    r = app.post("/api/replylater", {"pk": world.pk(LUNCH), "on": False})
    assert (r.status, r.json["on"]) == (200, False)


def test_a_number_may_still_come_as_text(app, world):
    r = app.post("/api/mute", {"pk": str(world.pk(LUNCH))})
    assert (r.status, r.json["ok"]) == (200, True)


def test_a_request_to_a_moved_route_counts_as_someone_here(app, world, monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.post("/api/mute", {"pk": world.pk(LUNCH)}).status == 200
    assert seen == [1]


def test_a_refused_request_counts_as_no_one(app, monkeypatch):
    """As on the handler, a request gets as far as its route to count: no further than the perimeter or its schema doesn't."""
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.post("/api/mute", {}, headers={"X-CSRF-Token": "wrong"}).status == 403
    assert app.post("/api/mute", {"pk": [1]}).status == 400
    assert seen == []
