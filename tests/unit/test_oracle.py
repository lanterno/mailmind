"""The oracle's invariants (tests/framework/mmtest/oracle.py), each on readings made up to hold or to break it: the
scenarios run them after every step, and these hold what each one catches and what it lets be."""
import pytest

from mmtest import oracle
from mmtest.shown import Composer, Shown
from mmtest.trace import Trace


class Page:
    keys_reach_the_page = True


class Mac:
    keys_reach_the_page = False


def history(*typed):
    trace = Trace(world=None)
    for text, body in typed:
        trace.typed_into(text, body)
    return trace


def composer(body):
    return Composer(title="Reply", to=[], cc=[], bcc=[], subject="", body=body)


def folder_place(key=None):
    opened = {"kind": "message", "key": key, "folder": "INBOX", "uid": 1} if key else None
    return {"source": {"kind": "folder", "account": "ada@example.test", "folder": "INBOX"}, "opened": opened}


# ── the keyboard is somewhere ───────────────────────────────────────────

def test_the_page_itself_holding_the_keyboard_is_somewhere_but_a_mac_window_with_nothing_focused_is_not():
    shown = Shown(focus=None)
    assert oracle.the_keyboard_is_somewhere(shown, oracle.Truth(), history(), Page()) is None
    assert "nothing has the keyboard" in oracle.the_keyboard_is_somewhere(shown, oracle.Truth(), history(), Mac())


def test_the_keyboard_in_something_no_one_can_see_breaks_it():
    hidden = Shown(focus={"region": "composer", "role": "textbox", "label": "message body", "visible": False})
    seen = Shown(focus={"region": "list", "role": "option", "label": "Venue", "visible": True})
    assert "no one can see" in oracle.the_keyboard_is_somewhere(hidden, oracle.Truth(), history(), Page())
    assert oracle.the_keyboard_is_somewhere(seen, oracle.Truth(), history(), Mac()) is None


# ── typed words are kept ────────────────────────────────────────────────

@pytest.mark.parametrize("where", ["composer", "draft", "outbox", "sent"])
def test_words_typed_into_a_body_are_kept_wherever_they_went(where):
    words = "Got it,\nthanks."
    shown = Shown(composers=[composer("Got it, thanks.\n\n-- \nAda")] if where == "composer" else [])
    truth = oracle.Truth(**{"drafts" if where == "draft" else where: ["Got it, thanks.\n\nOn Monday Lin wrote:"]}
                         if where != "composer" else {})
    assert oracle.typed_words_are_kept(shown, truth, history((words, True)), Page()) is None


def test_words_typed_into_a_body_that_are_nowhere_break_it():
    said = oracle.typed_words_are_kept(Shown(), oracle.Truth(drafts=["Something else"]),
                                       history(("Got it, thanks.", True)), Page())
    assert said == "typed words are nowhere: ['Got it, thanks.']"


def test_words_typed_elsewhere_or_thrown_away_need_not_be_kept():
    assert oracle.typed_words_are_kept(Shown(), oracle.Truth(), history(("lin@example.test,", False)), Page()) is None
    trace = history(("Never mind.", True))
    trace.discarded()
    assert oracle.typed_words_are_kept(Shown(), oracle.Truth(), trace, Page()) is None


# ── the open message is where it says ───────────────────────────────────

def test_an_open_message_its_folder_holds_is_where_it_says():
    shown = Shown(reader={"kind": "message", "subject": "Venue", "sender": "Lin Mori"})
    truth = oracle.Truth(place=folder_place(key=7), place_rows={5, 7})
    assert oracle.the_open_message_is_where_it_says(shown, truth, history(), Page()) is None


def test_an_open_message_its_folder_no_longer_holds_breaks_it_unless_the_reader_says_where():
    truth = oracle.Truth(place=folder_place(key=7), place_rows={5})
    quiet = Shown(reader={"kind": "message", "subject": "Venue", "sender": "Lin Mori"})
    saying = Shown(reader={"kind": "message", "subject": "Venue", "sender": "Lin Mori", "says_where": "In Archive"})
    assert "its list doesn't" in oracle.the_open_message_is_where_it_says(quiet, truth, history(), Page())
    assert oracle.the_open_message_is_where_it_says(saying, truth, history(), Page()) is None


def test_the_reader_s_own_key_counts_over_the_place_s_as_the_mac_says_it():
    shown = Shown(reader={"kind": "message", "subject": "Venue", "sender": "Lin Mori", "key": 9})
    truth = oracle.Truth(place=folder_place(key=7), place_rows={7})
    assert "key 9" in oracle.the_open_message_is_where_it_says(shown, truth, history(), Mac())


def test_a_list_that_is_not_a_folder_s_is_not_judged_yet():
    shown = Shown(reader={"kind": "message", "subject": "Venue", "sender": "Lin Mori"})
    assert oracle.the_open_message_is_where_it_says(shown, oracle.Truth(place_rows=None), history(), Page()) is None


# ── relaxing ────────────────────────────────────────────────────────────

def test_an_invariant_is_relaxed_for_one_step_only_and_only_with_a_reason():
    class Client(Page):
        def showing(self):
            return True

        def shown(self):
            return Shown(focus={"region": "list", "role": "option", "label": "x", "visible": False})

    checking = oracle.Oracle(world=None, client=Client(), history=history())
    oracle_read, oracle.read = oracle.read, lambda world, shown: oracle.Truth()
    try:
        with pytest.raises(ValueError):
            checking.relax("the_keyboard_is_somewhere", "")
        with pytest.raises(KeyError):
            checking.relax("no_such_invariant", "why")
        checking.relax("the_keyboard_is_somewhere", "the list scrolls its chosen row into view a beat later")
        checking.check()
        with pytest.raises(oracle.Broken, match="the_keyboard_is_somewhere"):
            checking.check()
    finally:
        oracle.read = oracle_read
