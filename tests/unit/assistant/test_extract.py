"""A message's proposals, read by Claude on a click: what the model answers is read with care, and nothing is created by it."""
from datetime import date

import pytest

from mailmind.assistant.domain import extraction
TODAY = date(2026, 11, 2)
MEETING = {"title": "Design review", "start": "2026-11-12T15:00", "end": "2026-11-12T16:00", "place": "Room 2"}


def test_a_meeting_comes_through_as_the_model_named_it():
    got = extraction.suggestions({"events": [MEETING], "tasks": []}, TODAY)
    assert got == {"events": [{**MEETING, "all_day": False}], "tasks": []}


def test_a_day_with_no_time_is_all_day():
    got = extraction.suggestions({"events": [{"title": "Conference", "start": "2026-11-12", "end": "2026-11-14", "place": ""}]}, TODAY)
    assert got["events"] == [{"title": "Conference", "start": "2026-11-12", "end": "2026-11-14", "place": "", "all_day": True}]


@pytest.mark.parametrize("start", ["", "next Tuesday", "2026-02-30", "9999-01-01", None, 7])
def test_an_event_with_no_day_we_can_read_is_dropped(start):
    assert extraction.suggestions({"events": [{**MEETING, "start": start}]}, TODAY)["events"] == []


@pytest.mark.parametrize("end", ["", "soon", "2026-11-12T14:00", "2026-11-12", "2028-01-01T00:00"])
def test_an_end_that_cant_end_it_leaves_it_without_one(end):
    assert extraction.suggestions({"events": [{**MEETING, "end": end}]}, TODAY)["events"][0]["end"] is None


def test_a_title_is_needed_and_held_to_one_line_and_its_cap():
    assert extraction.suggestions({"events": [{**MEETING, "title": "  "}], "tasks": [{"title": "", "due": ""}]}, TODAY) == {"events": [], "tasks": []}
    got = extraction.suggestions({"tasks": [{"title": "Sign\r\nthe form" + "x" * 500, "due": ""}]}, TODAY)
    assert got["tasks"][0]["title"].startswith("Sign the form") and len(got["tasks"][0]["title"]) == 200


def test_a_task_keeps_a_date_that_reads_and_stands_without_one_that_doesnt():
    got = extraction.suggestions({"tasks": [{"title": "Send the slides", "due": "2026-11-09"},
                                            {"title": "Book a room", "due": "Friday"},
                                            {"title": "Reply to Lin", "due": ""},
                                            {"title": "Pay", "due": "2026-11-09T17:00"}]}, TODAY)
    assert got["tasks"] == [{"title": "Send the slides", "due": "2026-11-09"}, {"title": "Book a room", "due": None},
                            {"title": "Reply to Lin", "due": None}, {"title": "Pay", "due": "2026-11-09"}]


def test_the_same_one_named_twice_is_listed_once():
    got = extraction.suggestions({"events": [MEETING, {**MEETING, "title": "design REVIEW"}],
                                  "tasks": [{"title": "Pay", "due": ""}, {"title": "pay", "due": ""}]}, TODAY)
    assert len(got["events"]) == 1 and len(got["tasks"]) == 1


def test_a_long_answer_is_held_to_the_caps():
    got = extraction.suggestions({"events": [{**MEETING, "title": f"m{i}"} for i in range(50)],
                                  "tasks": [{"title": f"t{i}", "due": ""} for i in range(50)]}, TODAY)
    assert len(got["events"]) == extraction.MAX_EVENTS and len(got["tasks"]) == extraction.MAX_TASKS


@pytest.mark.parametrize("answer", [{}, None, [], "text", {"events": "x", "tasks": 3}, {"events": [1, "a", None], "tasks": [[]]}])
def test_an_answer_that_wont_do_is_no_suggestions(answer):
    assert extraction.suggestions(answer, TODAY) == {"events": [], "tasks": []}


def test_quoted_lines_are_not_read_but_a_forward_is():
    assert extraction.readable("Tuesday at 3?\n> Monday at 2\n  > older") == "Tuesday at 3?"
    assert "Wednesday" in extraction.readable("---- Forwarded message ----\nFrom: Lin\nWednesday at 4")


def test_a_message_is_read_within_its_cap():
    assert len(extraction.readable("x" * (extraction.TEXT_MAX + 50))) == extraction.TEXT_MAX
