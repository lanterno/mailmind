"""format=flowed (RFC 3676): a message read as it was written, through `mime.parse` and `text.text_body`, not as it was wrapped for the
wire. All addresses are fictional."""
import email
from email import policy

import pytest

from mailmind.content import mime
from mailmind.content.text import text_body


def raw(body, params="format=flowed", ctype="text/plain"):
    head = f"From: bo@example.test\r\nTo: ada@example.test\r\nSubject: Words\r\nContent-Type: {ctype}; charset=utf-8; {params}\r\n\r\n"
    return (head + body).encode()


def parsed(body, params="format=flowed"):
    return mime.parse(raw(body, params))["text"]


def test_a_soft_break_joins_the_lines_it_broke():
    assert parsed("A paragraph wrapped \r\nfor the wire \r\nby its sender.\r\n") == "A paragraph wrapped for the wire by its sender.\n"


def test_a_hard_break_stays():
    assert parsed("One line.\r\nAnother line.\r\n") == "One line.\nAnother line.\n"


def test_each_paragraph_stays_its_own():
    assert parsed("First \r\nparagraph.\r\n\r\nSecond \r\nparagraph.\r\n") == "First paragraph.\n\nSecond paragraph.\n"


def test_one_stuffing_space_goes_and_no_more():
    assert parsed(" From the top\r\n >not a quote\r\n  indented\r\n") == "From the top\n>not a quote\n indented\n"


def test_a_stuffed_line_can_flow():
    assert parsed(" From the \r\n top.\r\n") == "From the top.\n"


@pytest.mark.parametrize("params", ["format=flowed; delsp=yes", "Format=Flowed; DelSp=Yes", 'format="flowed"; delsp="yes"'])
def test_delsp_deletes_the_space_that_broke_a_word(params):
    assert parsed("A longw \r\nord.\r\n", params) == "A longword.\n"


def test_without_delsp_the_space_stays():
    assert parsed("A longw \r\nord.\r\n", "format=flowed; delsp=no") == "A longw ord.\n"


def test_a_quote_joins_within_its_depth_and_comes_back_marked():
    assert parsed("> quoted \r\n> on and on.\r\n>> deeper \r\n>> still.\r\nMine.\r\n") == "> quoted on and on.\n>> deeper still.\nMine.\n"


def test_a_quote_marked_with_no_stuffing_counts_the_same():
    assert parsed(">quoted \r\n>on.\r\n") == "> quoted on.\n"


def test_another_depth_ends_a_paragraph():
    """A flowed line with no next line at its depth is a fixed one."""
    assert parsed("> soft \r\nnext.\r\n") == "> soft\nnext.\n"


def test_an_empty_line_ends_a_paragraph_and_stays():
    assert parsed("soft \r\n\r\nnext.\r\n") == "soft\n\nnext.\n"
    assert parsed("> soft \r\n>\r\n> next.\r\n") == "> soft\n>\n> next.\n"


def test_a_signature_line_never_joins():
    assert parsed("Bye \r\nnow.\r\n-- \r\nBo\r\n") == "Bye now.\n-- \nBo\n"
    assert parsed("soft \r\n-- \r\nBo\r\n") == "soft\n-- \nBo\n"


def test_a_quoted_signature_line_never_joins():
    assert parsed("> Bye\r\n> -- \r\n> Bo\r\n") == "> Bye\n> -- \n> Bo\n"


def test_a_last_line_with_nothing_after_it_keeps_its_words():
    assert parsed("the end ") == "the end"


@pytest.mark.parametrize("params", ["", "format=fixed", "delsp=yes", 'format="nonsense"'])
def test_any_other_text_comes_as_it_came(params):
    body = "wrapped \r\n stuffed\r\n> quoted \r\n"
    assert parsed(body, params) == body


def test_html_is_never_read_as_flowed():
    message = raw("<p>held \r\ntogether</p>", ctype="text/html")
    assert mime.parse(message)["html"] == "<p>held \r\ntogether</p>"


def test_the_flowed_text_part_of_an_alternative():
    message = ("From: bo@example.test\r\nContent-Type: multipart/alternative; boundary=b\r\n\r\n"
               "--b\r\nContent-Type: text/plain; format=flowed\r\n\r\nSoft \r\nbreak.\r\n"
               "--b\r\nContent-Type: text/html\r\n\r\n<p>Soft break.</p>\r\n--b--\r\n").encode()
    assert mime.parse(message)["text"].strip() == "Soft break."


@pytest.mark.parametrize("message_policy", [policy.compat32, policy.default])
def test_what_a_list_shows_reads_the_same(message_policy):
    message = email.message_from_bytes(raw("A paragraph wrapped \r\nfor the wire.\r\n\r\n> a \r\n> quote\r\n"), policy=message_policy)
    assert text_body(message) == "A paragraph wrapped for the wire.\n\n> a quote"


def test_a_flowed_copy_with_delsp_reads_whole_in_a_list():
    message = email.message_from_bytes(raw("longw \r\nord\r\n", "format=flowed; delsp=yes"))
    assert text_body(message) == "longword"


def test_a_stranger_can_make_it_cost_nothing_much():
    """Linear in what was sent, however many markers or soft breaks it holds."""
    assert parsed(">" * 200_000 + "\r\n") == ">" * 200_000 + "\n"
    assert parsed("x \r\n" * 50_000) == " ".join(["x"] * 50_000) + "\n"


def test_a_part_whose_parameters_wont_read_comes_as_it_came():
    class Broken:
        def get_content_type(self):
            return "text/plain"

        def get_param(self, name):
            raise ValueError(name)

    assert mime.as_written(Broken(), "soft \nbreak") == "soft \nbreak"
