"""resume_split on arbitrary text: what you typed and the quote a save recorded come back apart, the quote is cut only
where the text ends with it, and nothing is ever lost. test_draft_resume.py has its worked cases, end to end."""
from hypothesis import assume, given, strategies as st

from mailmind.compose.domain.resume import lf, resume_split
from property_runs import runs

# Line ends in each form, blank lines, and the words a quote is made of, so a text often holds a piece of its tail.
TEXT = st.lists(st.sampled_from(["\n", "\n\n", "\r\n", "\r", "> ", "On Mon", " wrote:", "Lin", "Ada", "é", " "]) | st.text(max_size=5),
                max_size=10).map("".join)
# A quote as Composing records one: a blank line, then the words.
QUOTE = st.builds(lambda body, end: "\n\n" + body + end, TEXT.filter(lambda t: lf(t).strip("\n")), st.sampled_from(["", "\n", "\n\n"]))


def same_but_for_trailing_newlines(a, b):
    return a.rstrip("\n") == b.rstrip("\n")


@given(TEXT, QUOTE, st.sampled_from(["", "\n"]))
@runs(400)
def test_own_text_and_its_quote_split_back_as_they_were(own, tail, end):
    """As the server has it: the two together, and maybe set_content's newline."""
    got_own, got_tail, how = resume_split(own + tail + end, {"own_text": "stale", "tail_text": tail}, False)
    assert (got_tail, how) == (lf(tail), "tail")
    assert same_but_for_trailing_newlines(got_own + got_tail, lf(own + tail))


@given(TEXT, QUOTE)
@runs(300)
def test_the_quote_is_cut_only_from_the_end_where_the_text_holds_it_twice(own, tail):
    """A reply quoting its own quote: what you typed keeps the copy inside it."""
    typed = own + tail + "\n\nthen more"
    got_own, got_tail, how = resume_split(typed + tail, {"tail_text": tail}, False)
    assert (got_own, got_tail, how) == (lf(typed), lf(tail), "tail")


@given(TEXT, QUOTE, st.text(st.sampled_from("abc é"), min_size=1, max_size=8))
@runs(300)
def test_a_quote_inside_the_text_and_not_at_its_end_stays_where_it_is(before, tail, after):
    """It can't be told from something you wrote, so all of it is yours to edit."""
    assume(after.strip())
    text = before + tail + after
    assume(not lf(text).rstrip("\n").endswith(lf(tail).rstrip("\n")))
    assert resume_split(text, {"tail_text": tail}, False) == (lf(text), "", "whole")


@given(TEXT, TEXT)
@runs(300)
def test_nothing_is_lost_whatever_the_record_says(text, tail):
    got_own, got_tail, how = resume_split(text, {"tail_text": tail}, False)
    assert how in ("tail", "whole")
    if how == "whole":
        assert got_tail == "" and got_own in (lf(text), lf(text).removesuffix("\n"))
    assert same_but_for_trailing_newlines(got_own + got_tail, lf(text))


@given(TEXT, TEXT, TEXT | st.none())
@runs(200)
def test_the_very_copy_saved_believes_its_record_whatever_the_server_rewrote(own, tail, text):
    assert resume_split(text, {"own_text": own, "tail_text": tail}, True) == (lf(own), lf(tail), "exact")


@given(TEXT, TEXT, st.booleans())
@runs(100)
def test_a_tail_with_no_blank_line_before_it_is_never_cut(own, tail, exact):
    """A quote always starts with one, so without it this is no quote of ours."""
    assume(not lf(tail).startswith("\n\n"))
    got = resume_split(own + tail, {"tail_text": tail}, exact)
    assert got[1:] == ("", "whole")


def test_with_no_text_and_no_record_of_it_there_is_nothing_to_split():
    assert resume_split(None, None, False) is None
    assert resume_split(None, {"tail_text": "\n\nquote"}, False) is None
    assert resume_split(None, {"tail_text": "\n\nquote"}, True) is None
