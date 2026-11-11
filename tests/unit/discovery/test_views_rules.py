"""What makes a saved view, and what may go, with no database."""
import pytest

from mailmind.discovery.domain import views


def view(vid, name="Fabric", q="from:lin"):
    return {"id": vid, "name": name, "q": q}


# -- what a person saves ----------------------------------------------------------

@pytest.mark.parametrize("name, q", [(None, "x"), ("", "x"), ("  ", "x"), ("x", None), ("x", ""), ("x", "  ")])
def test_a_view_needs_a_name_and_a_query(name, q):
    assert views.new(name, q) is None


def test_a_new_view_is_held_to_what_fits_and_named_by_a_fresh_id():
    v = views.new("  " + "n" * 80, "q" * 600)
    assert (v["name"], v["q"]) == ("n" * 60, "q" * 512)
    assert v["id"].startswith("v") and v["id"] != views.new("n", "q")["id"]


# -- what was saved --------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, 5, "views", {"id": "v1"}])
def test_what_was_saved_that_isnt_a_list_holds_no_views(raw):
    assert views.clean(raw) == []


@pytest.mark.parametrize("entry", [
    "view", 5,
    {"name": "n", "q": "q"}, {"id": "v1", "q": "q"}, {"id": "v1", "name": "n"},
    {"id": "v1", "name": " ", "q": "q"}, {"id": "v1", "name": "n", "q": " "},
    {"id": "__unread__", "name": "Mine now", "q": "q"},
])
def test_an_entry_that_cant_be_a_view_or_takes_a_builtins_id_is_dropped(entry):
    assert views.clean([entry, view("v2")]) == [view("v2")]


def test_what_was_saved_is_held_to_what_a_new_view_is():
    assert views.clean([{"id": 7, "name": " " + "n" * 80, "q": " " + "q" * 600}]) == [{"id": "7", "name": "n" * 60, "q": "q" * 512}]


# -- what is listed ---------------------------------------------------------------

def test_the_builtins_come_first_and_say_so():
    listed = views.merged([view("v1")])
    assert [v["id"] for v in listed][:5] == ["__unread__", "__flagged__", "__attach__", "__last7__", "__lane_feed__"]
    assert listed[-1] == {**view("v1"), "builtin": False}
    assert all(v["builtin"] for v in listed[:-1])


def test_lanes_can_be_left_out():
    assert not any(v["id"].startswith("__lane_") for v in views.merged([], include_lanes=False))


# -- what may go ------------------------------------------------------------------

def test_a_view_goes():
    assert views.without([view("v1"), view("v2")], "v1") == [view("v2")]


@pytest.mark.parametrize("vid", ["__unread__", "__lane_feed__"])
def test_a_builtin_cant_even_if_one_was_saved(vid):
    assert views.without([view(vid), view("v2")], vid) is None


@pytest.mark.parametrize("vid", ["nobody", "", None])
def test_one_that_isnt_there_changes_nothing(vid):
    assert views.without([view("v1")], vid) is None


def test_the_last_one_going_leaves_none_rather_than_nothing_to_do():
    assert views.without([view("v1")], "v1") == []


