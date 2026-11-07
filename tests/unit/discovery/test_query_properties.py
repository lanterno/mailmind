"""What holds of the search grammar for any text at all: it parses, what it parses to is SQL that SQLite takes
with every value bound, and a query that only negates or only names a folder or an account never narrows. The
table of what each operator means is in test_search_build.py, and of what a parse says about itself in
test_query_spec.py."""
import shlex

import pytest
from hypothesis import given, strategies as st

from mailmind.bootstrap import discovery
from mailmind.bootstrap.mailbox import store
from mailmind.discovery.adapters import fts
from mailmind.discovery.domain.query import MAX_CHARS, Clause, QuerySpec
from property_runs import runs

# Every WHERE fragment to_sql may write. Anything else in one came from the query.
FRAGMENTS = {
    "m.seen = ?", "m.flagged = ?", "m.answered = ?", "m.is_junk = ?", "m.snoozed_until IS NULL",
    "m.snoozed_until IS NOT NULL", "m.bucket = ?", "m.bucket IS NOT ?", "s.category = ?", "s.category IS NOT ?",
    "m.folder = ?", "m.folder <> ?", "m.account = ?", "m.account <> ?", "m.has_attach = ?", "m.ts < ?", "m.ts >= ?",
    "m.pk NOT IN (SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?)"}
_OPERATORS = ["from:", "subject:", "body:", "to:", "cc:", "is:", "bucket:", "category:", "has:", "in:", "account:", "before:",
              "after:", "-from:", "-is:", "-in:", "-before:", "IS:", "Has:"]
_VALUES = ["unread", "read", "flagged", "answered", "junk", "snoozed", "newsletter", "attachment", "personal", "2026-11-02",
           "2026-11-02T09:00:00+00:00", "0001-01-01", "9999-12-31", "9999-12-31T23:59:59-23:59", "2026-13-45", "someday", "x'",
           "'; DROP TABLE messages; --", '"', "a b", "m.pk", "?", "\u202e", "é\u0301", "日本語"]
_WORDS = ["-", "--", '"', '""', "'", "\\", ":", "::", "NOT", "AND", "OR", "NEAR(a b)", "*", "a*", "^a", "{from_name}", "(", ")", ";",
          *_VALUES]


def queries(text):
    """Text from `text`, and the grammar's own pieces with it, as words and as operators given a value."""
    operator = st.builds(str.__add__, st.sampled_from(_OPERATORS), st.sampled_from(_VALUES) | text)
    return text | st.lists(operator | st.sampled_from(_WORDS) | text, max_size=8).map(" ".join)


_BOUND = st.characters(codec="utf-8", exclude_characters="\x00")
# Anything a string may hold, lone surrogates included (a JSON body can send one).
ANY_TEXT = queries(st.text(st.characters(), max_size=30))
# What SQLite can be handed: a bound string can't hold a surrogate. A NUL has a test of its own.
BINDABLE = queries(st.text(_BOUND, max_size=30))
# Longer than the parser takes: in one word, and in many, with a quote the cut may leave open.
TOO_LONG = st.text(st.sampled_from("ab"), min_size=MAX_CHARS, max_size=MAX_CHARS + 60) | \
    st.text(st.sampled_from('ab -":'), min_size=MAX_CHARS, max_size=MAX_CHARS + 60)


@pytest.fixture
def one_message(data_dir):
    """A message that's in the index: with no row at all, SQLite would never run a NOT IN's MATCH."""
    c = store.conn()
    pk = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, ts, from_addr, subject, recipients) "
                   "VALUES ('ada@example.test', 'imap', 'INBOX', 1, '<a@example.test>', 1800000000, 'lin@example.test', "
                   "'Lunch', 'ada@example.test')").lastrowid
    fts.index_message(c, pk)
    c.commit()
    c.close()


@given(ANY_TEXT | TOO_LONG)
@runs(500)
def test_no_text_makes_the_parser_raise(text):
    spec = QuerySpec.parse(text)
    assert all(isinstance(c, Clause) for c in spec.clauses)
    assert all(len(c.value) <= MAX_CHARS for c in spec.clauses if isinstance(c.value, str))
    spec.narrows(), spec.names_folder(), spec.asks_about_snoozed()


@given(ANY_TEXT)
@runs(500)
def test_nothing_a_query_says_is_written_into_its_sql(text):
    """The MATCH string is one bound value, every other value is too, and each fragment is one of to_sql's own."""
    match, where, params = fts.to_sql(QuerySpec.parse(text))
    assert match is None or isinstance(match, str)
    assert set(where) <= FRAGMENTS
    assert sum(w.count("?") for w in where) == len(params)
    assert all(isinstance(p, (int, str)) for p in params)


@given(BINDABLE | TOO_LONG)
@runs(200)
def test_sqlite_takes_what_any_query_makes(one_message, text):
    """Through the search and the bulk command's reach as they run, each of which answers None for one it rejects."""
    spec = QuerySpec.parse(text)
    assert discovery._mail.search(spec, None, "rank", 5, 0) is not None, text
    assert discovery._mail.reach(spec, 5, {"trash"}) is not None, text


@given(queries(st.text(_BOUND, max_size=6)), st.integers(0, 40))
@runs(100)
def test_a_nul_at_worst_makes_a_bad_query(one_message, text, at):
    """FTS5 reads a bound string only up to a NUL, so it may see a quote left open. That's an answer, not a raise."""
    spec = QuerySpec.parse(text[:at] + "\x00" + text[at:])
    discovery._mail.search(spec, None, "rank", 5, 0)
    discovery._mail.reach(spec, 5, {"trash"})


# Short enough that even all quotes, each five characters once escaped, stay under the parser's limit.
A_WORD = st.text(min_size=1, max_size=20)
A_NEGATION = A_WORD.map(lambda word: "-" + shlex.quote(word))
A_SCOPE = st.builds(lambda op, value: op + shlex.quote(value),
                    st.sampled_from(["in:", "IN:", "account:", "Account:", "-in:", "-account:"]), A_WORD)


@given(st.lists(A_NEGATION, max_size=4).map(" ".join))
@runs(300)
def test_a_query_that_only_negates_never_narrows(text):
    assert not QuerySpec.parse(text).narrows(), text


@given(st.lists(A_SCOPE, max_size=4).map(" ".join))
@runs(300)
def test_a_query_that_only_names_a_folder_or_an_account_never_narrows(text):
    assert not QuerySpec.parse(text).narrows(), text


@given(st.lists(A_NEGATION | A_SCOPE, max_size=3), st.from_regex(r"[a-z0-9]{1,12}", fullmatch=True), st.integers(0, 3))
@runs(200)
def test_a_word_asked_for_beside_those_still_narrows(others, word, at):
    """So the two above aren't true only because nothing ever narrows."""
    others.insert(min(at, len(others)), word)
    assert QuerySpec.parse(" ".join(others)).narrows(), others
