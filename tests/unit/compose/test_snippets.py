"""The words a person keeps for the composer: what makes one, how the database keeps them and the use case. What `;name`
does in the text is the page's, in features/compose."""
import json

import pytest

from at_once import HeldAfterItsRead, overlap
from mailmind.compose.adapters import snippets_store
from mailmind.compose.application.snippets import Snippets
from mailmind.compose.domain import snippets
from mailmind.kernel.errors import Refused
from mailmind.platform.db.uow import UnitOfWork

SIG = {"trigger": "sig", "text": "Thanks,\nAda"}


class Counted:
    """The store, counting its writes."""
    def __init__(self):
        self.writes = 0

    def read(self, c):
        return snippets_store.read(c)

    def write(self, c, items):
        snippets_store.write(c, items)
        self.writes += 1


def kept():
    """The snippets document as the database holds it: its text, and its version."""
    with UnitOfWork().read() as c:
        row = c.execute("SELECT body, version FROM documents WHERE name='snippets'").fetchone()
    return None if row is None else (row["body"], row["version"])


def put(text):
    """`text` as the snippets document, as an old mailmind_snippets.json goes in."""
    with UnitOfWork().write() as c:
        c.execute("INSERT INTO documents(name, body, changed_at) VALUES('snippets', ?, '2026-11-11T00:00:00+00:00')", (text,))


def use(store=snippets_store):
    return Snippets(UnitOfWork(), store)


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


# ── the store ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_database_keeps_them_as_a_person_reads_them_in_the_shape_the_file_held(data_dir):
    use().post({"verb": "save", "trigger": "s", "text": "你好"})
    body, version = kept()
    assert "你好" in body and json.loads(body) == [{"trigger": "s", "text": "你好"}] and version == 1
    assert use().load() == [{"trigger": "s", "text": "你好"}]


def test_none_kept_or_what_wont_read_reads_as_nothing(data_dir):
    assert use().load() == [] and kept() is None
    put("{not json \xff")
    assert use().load() == []


def test_an_old_files_text_reads_as_its_snippets(data_dir):
    put(json.dumps([SIG], indent=2, ensure_ascii=False))
    assert use().listed() == {"ok": True, "snippets": [SIG]}


def test_a_save_that_fails_on_its_way_leaves_what_was_kept(data_dir):
    use().post({"verb": "save", **SIG})

    class Full:
        read = staticmethod(snippets_store.read)

        @staticmethod
        def write(c, items):
            snippets_store.write(c, items)
            raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        use(Full).post({"verb": "save", "trigger": "other", "text": "x"})
    assert use().load() == [SIG] and kept()[1] == 1


# ── the use case ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_what_changes_nothing_writes_nothing(data_dir):
    use().post({"verb": "save", **SIG})
    store = Counted()
    assert use(store).post({"verb": "delete", "trigger": "other"}) == {"ok": True, "snippets": [SIG]}
    assert store.writes == 0 and kept()[1] == 1


def test_one_refused_writes_nothing(data_dir):
    use().post({"verb": "save", **SIG})
    with pytest.raises(Refused):
        use().post({"verb": "save", "trigger": "two words", "text": "x"})
    assert use().load() == [SIG] and kept()[1] == 1


def test_two_changes_at_once_both_land(data_dir):
    """Two Snippets, as two processes' would be, each saving one while the other does: the first holds after its read
    until the second has read too or waits for the write lock."""
    held = HeldAfterItsRead(snippets_store)
    one, other = use(held), use(held)
    overlap(held, lambda: one.post({"verb": "save", "trigger": "a", "text": "x"}),
            lambda: other.post({"verb": "save", "trigger": "b", "text": "y"}))
    assert [s["trigger"] for s in use().load()] == ["a", "b"]


def test_a_save_and_a_delete_at_once_both_land(data_dir):
    use().post({"verb": "save", **SIG})
    held = HeldAfterItsRead(snippets_store)
    one, other = use(held), use(held)
    overlap(held, lambda: one.post({"verb": "save", "trigger": "a", "text": "x"}),
            lambda: other.post({"verb": "delete", "trigger": "sig"}))
    assert [s["trigger"] for s in use().load()] == ["a"]
