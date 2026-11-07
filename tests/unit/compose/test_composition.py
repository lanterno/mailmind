"""What a request to send or save comes to, with no database and no server."""
from mailmind.compose.domain import attachments, composition, limits
from mailmind.content.quote import own_html


ORIGINAL = {"from_name": "Lin Ma", "from_addr": "lin@example.test", "subject": "Fabric", "message_id": "<o@example.test>",
            "refs": "<root@example.test> <mid@example.test>", "in_reply_to": "<mid@example.test>"}
PARSED = {"date": "Mon, 2 Nov 2026 12:00:00 +0000", "to": "ada@example.test", "cc": None, "text": "Which blue?", "html": None}
ACCT = {"email": "ada@example.test", "alias": "Ada"}


def recorded(text="\n\n> quoted\n", html="<blockquote>quoted</blockquote>"):
    return {"tail_text": text, "tail_html": html}


def file(name, size):
    return {"filename": name, "content_type": "text/plain", "data": b"x" * size}


# -- nothing but what you typed ------------------------------------------------

def test_a_new_message_is_only_what_you_typed():
    made = composition.as_typed("Hello")
    assert (made.own, made.text, made.html, made.tail_text, made.tail_html, made.in_reply_to, made.references) == (
        "Hello", "Hello", None, None, None, None, None)


# -- a reopened draft -----------------------------------------------------------

def test_a_reopened_draft_is_your_text_and_the_quote_it_already_carried():
    made = composition.as_drafted("Mine\r\n", "\n\n> quoted\n", None, None, None)
    assert (made.own, made.text, made.tail_text) == ("Mine\n", "Mine\n\n\n> quoted\n", "\n\n> quoted\n")


def test_the_quotes_html_goes_only_where_the_record_vouches_for_that_exact_quote():
    made = composition.as_drafted("Mine", "\n\n> quoted\n", recorded(), None, None)
    assert made.tail_html == "<blockquote>quoted</blockquote>"
    assert made.html == own_html("Mine") + "<blockquote>quoted</blockquote>"


def test_a_record_of_another_quote_vouches_for_nothing():
    made = composition.as_drafted("Mine", "\n\n> edited\n", recorded(), None, None)
    assert (made.html, made.tail_html, made.tail_text) == (None, None, "\n\n> edited\n")


def test_a_record_compares_by_its_text_with_lf_line_ends():
    assert composition.as_drafted("Mine", "\n\n> q\n", recorded(text="\r\n\r\n> q\r\n"), None, None).tail_html is not None


def test_a_record_with_no_html_or_a_draft_with_no_quote_has_none():
    assert composition.as_drafted("Mine", "\n\n> quoted\n", recorded(html=None), None, None).html is None
    no_quote = composition.as_drafted("Mine", "", recorded(text=""), None, None)
    assert (no_quote.html, no_quote.tail_text, no_quote.text) == (None, None, "Mine")


def test_its_threading_is_its_own_well_formed_headers():
    made = composition.as_drafted("Mine", "", None, "<o@example.test>", "<r@example.test> junk <o@example.test>")
    assert (made.in_reply_to, made.references) == ("<o@example.test>", ["<r@example.test>", "<o@example.test>"])


def test_the_tail_a_request_names_comes_with_lf_line_ends_and_empty_if_it_names_none():
    assert composition.tail_of({"tail_text": "\r\n\r\n> q\r\n"}) == "\n\n> q\n"
    assert composition.tail_of({}) == ""
    assert composition.tail_of({"tail_text": None}) == ""


def test_a_pk_names_the_original_only_if_it_holds_the_message_its_headers_say():
    row = {"message_id": "<o@example.test>"}
    assert composition.named_original(row, "<o@example.test>") is row
    assert composition.named_original(row, "<other@example.test>") is None
    assert composition.named_original(None, "<o@example.test>") is None


# -- a reply and a forward ------------------------------------------------------

def test_a_reply_quotes_the_original_after_what_you_typed_and_keeps_them_apart():
    made = composition.as_reply("Navy", ORIGINAL, PARSED)
    assert made.own == "Navy"
    assert made.text == "Navy" + made.tail_text
    assert "> Which blue?" in made.tail_text and "Lin Ma <lin@example.test> wrote:" in made.tail_text
    assert made.html is None and made.tail_html is None


def test_a_reply_threads_on_its_original_with_the_parent_last():
    made = composition.as_reply("Navy", ORIGINAL, PARSED)
    assert made.in_reply_to == "<o@example.test>"
    assert made.references == ["<root@example.test>", "<mid@example.test>", "<o@example.test>"]


def test_a_reply_to_html_quotes_it_sanitized():
    made = composition.as_reply("Navy", ORIGINAL, {**PARSED, "html": "<p>Which <b>blue</b>?</p><script>alert(1)</script>"})
    assert made.html == own_html("Navy") + made.tail_html
    assert "<b>blue</b>" in made.tail_html and "script" not in made.tail_html


def test_an_original_whose_body_couldnt_be_read_is_still_answered():
    made = composition.as_reply("Navy", ORIGINAL, {})
    assert made.text.startswith("Navy") and "Lin Ma <lin@example.test> wrote:" in made.tail_text


def test_a_forward_names_no_original():
    made = composition.as_forward("See below", ORIGINAL, PARSED)
    assert (made.in_reply_to, made.references) == (None, None)
    assert made.text == "See below" + made.tail_text
    assert "Forwarded message" in made.tail_text and "Which blue?" in made.tail_text and "From: Lin Ma <lin@example.test>" in made.tail_text


def test_a_forward_of_html_keeps_it_apart_from_what_you_typed():
    made = composition.as_forward("See below", ORIGINAL, {**PARSED, "html": "<p>Which <b>blue</b>?</p>"})
    assert made.html == own_html("See below") + made.tail_html and "<b>blue</b>" in made.tail_html


# -- the whole of it ------------------------------------------------------------

def test_from_is_the_account_with_its_alias_whatever_the_request_says():
    spec = composition.composed(ACCT, {"account": "other@example.test", "from": "other@example.test"}, composition.as_typed("x"), [], [])
    assert (spec["account"], spec["account_name"]) == ("ada@example.test", "Ada")


def test_an_account_with_no_alias_goes_by_its_address():
    assert composition.composed({"email": "ada@example.test"}, {}, composition.as_typed("x"), [], [])["account_name"] == "ada@example.test"


def test_what_a_request_leaves_out_is_empty_rather_than_absent():
    spec = composition.composed(ACCT, {"to": None, "subject": None}, composition.as_typed("x"), [], [])
    assert (spec["to"], spec["cc"], spec["bcc"], spec["subject"]) == ([], [], [], "")


def test_it_carries_what_was_made_for_building_and_what_a_save_records():
    files = [file("a.txt", 1)]
    made = composition.as_reply("Navy", ORIGINAL, PARSED)
    spec = composition.composed(ACCT, {"to": ["lin@example.test"], "subject": "Re: Fabric"}, made, files, files)
    assert (spec["to"], spec["subject"], spec["text"], spec["html"], spec["in_reply_to"], spec["references"]) == (
        ["lin@example.test"], "Re: Fabric", made.text, None, "<o@example.test>", made.references)
    assert (spec["own"], spec["tail"]) == ("Navy", {"text": made.tail_text, "html": None})
    assert spec["attachments"] is files and spec["forwarded"] is files


# -- a forward's files ----------------------------------------------------------

def test_a_forward_takes_the_originals_files_along_in_order():
    found = [file("a.txt", 1), file("b.txt", 2)]
    assert attachments.take_along([], found) == found


def test_what_you_added_counts_against_the_budget_and_exactly_full_fits():
    have = [file("mine.bin", limits.MAX_ATTACH_BYTES - 3)]
    assert [a["filename"] for a in attachments.take_along(have, [file("a.txt", 3)])] == ["a.txt"]
    assert attachments.take_along(have, [file("a.txt", 4)]) == []


def test_the_first_that_doesnt_fit_ends_it_though_a_later_one_would():
    have = [file("mine.bin", limits.MAX_ATTACH_BYTES - 3)]
    found = [file("a.txt", 2), file("big.bin", 2), file("c.txt", 1)]
    assert [a["filename"] for a in attachments.take_along(have, found)] == ["a.txt"]
