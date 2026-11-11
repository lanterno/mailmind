"""The words a person keeps for the composer: what makes one, the file and the routes. What `;name` does in the text is the
page's, in features/compose."""
import os
import threading

import pytest

from mailmind.compose.adapters import snippets_file
from mailmind.compose.application.snippets import Snippets
from mailmind.compose.domain import snippets
from mailmind.kernel.errors import Refused

SIG = {"trigger": "sig", "text": "Thanks,\nAda"}


class File:
    def __init__(self, held=()):
        self.held, self.writes = list(held), 0

    def read(self):
        return list(self.held)

    def write(self, items):
        self.held = list(items)
        self.writes += 1


@pytest.mark.parametrize("typed, trigger", [("sig", "sig"), (";sig", "sig"), ("  ;SIG ", "sig"), ("a", "a"), ("0-a_b", "0-a_b"),
                                            ("x" * 32, "x" * 32)])
def test_a_trigger_reads_however_it_was_typed(typed, trigger):
    assert snippets.trigger_of(typed) == trigger


@pytest.mark.parametrize("typed", ["", " ", ";", ";;sig", "-sig", "_sig", "two words", "niño", "a\nb", "sig\n;x", "x" * 33, None, 5, ["sig"]])
def test_what_cant_be_a_trigger_isnt_one(typed):
    assert snippets.trigger_of(typed) is None


def test_one_saved_joins_in_order_and_one_named_again_takes_its_place():
    first = snippets.saved([], ";Sig", "Ada")
    assert first == [{"trigger": "sig", "text": "Ada"}]
    assert snippets.saved(first, "addr", "1 Example Way") == [{"trigger": "addr", "text": "1 Example Way"}, {"trigger": "sig", "text": "Ada"}]
    assert snippets.saved(first, "SIG", "Bea") == [{"trigger": "sig", "text": "Bea"}]


def test_its_words_stay_as_typed_but_for_line_ends_and_the_blank_lines_around_them():
    assert snippets.saved([], "s", "\r\n  Hi {{name}},\r\n\r\nBye  \n\n")[0]["text"] == "  Hi {{name}},\n\nBye  "


@pytest.mark.parametrize("trigger, text, said", [
    ("not one", "words", "a trigger is up to 32 letters, digits, - or _"),
    (None, "words", "a trigger is up to 32 letters, digits, - or _"),
    ("s", None, "a snippet needs some text"),
    ("s", " \n ", "a snippet needs some text"),
    ("s", "x" * (snippets.MAX_TEXT + 1), "a snippet can be up to 20,000 characters"),
])
def test_what_cant_be_kept_is_refused(trigger, text, said):
    with pytest.raises(Refused) as e:
        snippets.saved([], trigger, text)
    assert str(e.value) == said


def test_the_longest_text_it_takes_is_kept_whole():
    assert len(snippets.saved([], "s", "x" * snippets.MAX_TEXT)[0]["text"]) == snippets.MAX_TEXT


def test_past_the_most_it_keeps_a_new_one_is_refused_and_one_named_again_is_not():
    full = [{"trigger": f"s{i}", "text": "x"} for i in range(snippets.MAX_SNIPPETS)]
    with pytest.raises(Refused, match="delete one first"):
        snippets.saved(full, "another", "x")
    assert len(snippets.saved(full, "s0", "y")) == snippets.MAX_SNIPPETS
    assert len(snippets.saved(full[1:], "another", "x")) == snippets.MAX_SNIPPETS


def test_taking_one_away_says_when_that_changes_nothing():
    two = [{"trigger": "a", "text": "1"}, SIG]
    assert snippets.without(two, ";A") == [SIG]
    assert snippets.without(two, "other") is None
    assert snippets.without(two, None) is None


@pytest.mark.parametrize("raw", [None, 5, "text", {"trigger": "s", "text": "t"}, [None, 5, "x", [], {}, {"trigger": "s"}, {"text": "t"},
                                                                                  {"trigger": "not one", "text": "t"}, {"trigger": "s", "text": 5},
                                                                                  {"trigger": "s", "text": "  "}]])
def test_what_isnt_a_snippet_is_left_out_of_a_file(raw):
    assert snippets.clean(raw) == []


def test_a_file_is_read_in_order_once_each_and_within_the_limits():
    raw = [{"trigger": "b", "text": "1", "extra": "x"}, {"trigger": ";A", "text": "2"}, {"trigger": "B", "text": "3"}, {"trigger": "c", "text": "x" * (snippets.MAX_TEXT + 9)}]
    got = snippets.clean(raw)
    assert [(s["trigger"], s["text"][:1]) for s in got] == [("a", "2"), ("b", "1"), ("c", "x")]
    assert set(got[0]) == {"trigger", "text"} and len(got[2]["text"]) == snippets.MAX_TEXT
    assert len(snippets.clean([{"trigger": f"s{i}", "text": "x"} for i in range(snippets.MAX_SNIPPETS + 5)])) == snippets.MAX_SNIPPETS


# ── the file ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_file_holds_them_for_its_owner_alone_and_leaves_nothing_beside_it(data_dir):
    snippets_file.write([{"trigger": "s", "text": "你好"}])
    path = data_dir / "mailmind_snippets.json"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert "你好" in path.read_text(encoding="utf-8")                                # as a person can read it
    assert snippets_file.read() == [{"trigger": "s", "text": "你好"}]
    assert not list(data_dir.glob(".snippets-*"))


def test_a_write_that_fails_leaves_the_file_as_it_was_and_no_temp_file(data_dir, monkeypatch):
    snippets_file.write([SIG])
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        snippets_file.write([])
    monkeypatch.undo()
    assert snippets_file.read() == [SIG]
    assert not list(data_dir.glob(".snippets-*"))


def test_a_file_missing_or_broken_reads_as_nothing(data_dir):
    assert snippets_file.read() == []
    (data_dir / "mailmind_snippets.json").write_bytes(b"{not json \xff")
    assert snippets_file.read() == []


# ── the use case ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_what_changes_nothing_writes_nothing():
    file = File([SIG])
    assert Snippets(file).post({"verb": "delete", "trigger": "other"}) == {"ok": True, "snippets": [SIG]}
    assert file.writes == 0


def test_two_changes_at_once_both_land():
    """The first to read waits a moment for the other: without a change being one step, the second write loses the first."""
    other_read, reads = threading.Event(), []

    class Slow(File):
        def read(self):
            held = super().read()
            reads.append(1)
            if len(reads) == 1:
                other_read.wait(0.3)
            else:
                other_read.set()
            return held

    file = Slow()
    use = Snippets(file)
    runs = [threading.Thread(target=use.post, args=[{"verb": "save", "trigger": t, "text": "x"}]) for t in ("a", "b")]
    for run in runs:
        run.start()
    for run in runs:
        run.join()
    assert [s["trigger"] for s in file.held] == ["a", "b"]
