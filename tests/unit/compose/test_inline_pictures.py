"""A picture in the text of a message going out: its mark, its part beside the HTML, and what a draft keeps of it."""
import base64
import email
import email.policy

import pytest

from mailmind.compose.domain import attachments, composition, markdown
from mailmind.compose.domain.builder import build_message
from mailmind.content import mime

PNG = b"\x89PNG\r\n\x1a\nfictional"
ACCT = "ada@example.test"


def picture(token="pic1", name="chart.png", content_type="image/png"):
    return {"filename": name, "content_type": content_type, "data": PNG, "cid": token}


def spec(text, files=(), html=None):
    html = html if html is not None else composition.as_typed(text, attachments.pictures_of(list(files))).html
    return {"account": ACCT, "to": ["lin@example.test"], "subject": "Plans", "text": text, "html": html, "attachments": list(files)}


def parsed(raw):
    return email.message_from_bytes(raw, policy=email.policy.default)


def shape(m):
    """The message's parts as nested content types."""
    return [m.get_content_type(), *(shape(p) for p in m.iter_parts())] if m.is_multipart() else m.get_content_type()


# -- the mark ---------------------------------------------------------------------------------------------------------

def test_a_mark_naming_a_picture_of_the_message_is_an_image():
    assert markdown.render("![a chart](cid:pic1)", ["pic1"]) == '<p><img src="cid:pic1@mailmind" alt="a chart"/></p>'


def test_a_mark_is_markdown_by_itself():
    assert composition.as_typed("![x](cid:t)", {"t"}).html is not None


def test_a_mark_with_no_such_picture_stays_as_typed():
    assert markdown.render("![a](cid:other)", ["pic1"]) is None
    assert markdown.render("![a](cid:other) *b*", ["pic1"]) == "<p>![a](cid:other) <em>b</em></p>"


@pytest.mark.parametrize("typed", ["![x](https://example.test/a.png)", "![x](data:image/png;base64,AAAA)", "![x](cid:../etc)"])
def test_a_picture_from_anywhere_else_is_no_image(typed):
    html = markdown.render(f"{typed} *b*", ["pic1"])
    assert "<img" not in html


def test_a_marks_name_is_escaped():
    assert 'alt="&quot;a&amp;b&quot;"' in markdown.render('!["a&b"](cid:pic1)', ["pic1"])


def test_a_bang_on_its_own_is_a_bang():
    assert markdown.render("Hello! *b*", ["pic1"]) == "<p>Hello! <em>b</em></p>"


def test_a_marks_text_reading_is_its_name():
    assert markdown.plain_pictures("see ![a chart](cid:pic1) and ![b](cid:other)", {"pic1"}) == "see [a chart] and ![b](cid:other)"


def test_a_content_id_and_its_token_are_one_way_round_each_other():
    assert markdown.content_id("pic1") == "pic1@mailmind" and markdown.token_of("pic1@mailmind") == "pic1"


@pytest.mark.parametrize("cid", ["pic1@example.test", "image001.png@01D9", "", None, "../x@mailmind"])
def test_anyone_elses_content_id_has_no_token(cid):
    assert markdown.token_of(cid) is None


# -- a file a composer marks ------------------------------------------------------------------------------------------

def decoded(**a):
    return attachments.decode([{"filename": "a.png", "content_type": "image/png", "b64": base64.b64encode(PNG).decode(), **a}])


def test_a_file_keeps_the_token_a_mark_names_it_by():
    assert decoded(cid="pic1")[0]["cid"] == "pic1"
    assert "cid" not in decoded()[0]


@pytest.mark.parametrize("cid", ["../x", "a b", "a@b", "", "x" * 65, 5, ["a"]])
def test_a_token_that_cant_be_one_is_refused(cid):
    with pytest.raises(ValueError):
        decoded(cid=cid)


@pytest.mark.parametrize("content_type", ["image/svg+xml", "text/html", "application/pdf", "image/bmp"])
def test_only_a_picture_of_a_known_kind_can_be_in_the_text(content_type):
    assert "cid" not in decoded(cid="pic1", content_type=content_type)[0]


@pytest.mark.parametrize("content_type", ["image/png", "image/jpeg", "IMAGE/GIF", "image/webp"])
def test_each_kind_of_picture_can_be_in_the_text(content_type):
    assert decoded(cid="pic1", content_type=content_type)[0]["cid"] == "pic1"


def test_the_budget_counts_a_picture_in_the_text(monkeypatch):
    monkeypatch.setattr(attachments.limits, "MAX_ATTACH_BYTES", len(PNG) - 1)
    with pytest.raises(ValueError):
        decoded(cid="pic1")


def test_which_files_the_html_shows():
    a, b = picture("pic1"), picture("pic2", name="b.png")
    shown, rest = attachments.in_the_text('<img src="cid:pic1@mailmind">', [a, b])
    assert (shown, rest) == ([a], [b])


def test_no_html_shows_no_file():
    assert attachments.in_the_text(None, [picture()]) == ([], [picture()])


# -- what goes out ----------------------------------------------------------------------------------------------------

def test_the_picture_goes_beside_the_html_it_is_shown_in():
    m = parsed(build_message(spec("![a chart](cid:pic1)\n\n*b*", [picture()]))["raw"])
    assert shape(m) == ["multipart/alternative", "text/plain", ["multipart/related", "text/html", "image/png"]]
    img = next(p for p in m.walk() if p.get_content_type() == "image/png")
    assert img["Content-ID"] == "<pic1@mailmind>"
    assert img.get_content_disposition() == "inline" and img.get_filename() == "chart.png"
    assert img.get_content() == PNG
    assert 'src="cid:pic1@mailmind"' in m.get_body(preferencelist=("html",)).get_content()


def test_a_file_of_the_message_goes_as_a_file_around_it():
    pdf = {"filename": "map.pdf", "content_type": "application/pdf", "data": b"%PDF"}
    m = parsed(build_message(spec("![a](cid:pic1) *b*", [picture(), pdf]))["raw"])
    assert shape(m) == ["multipart/mixed", ["multipart/alternative", "text/plain", ["multipart/related", "text/html", "image/png"]], "application/pdf"]


def test_two_pictures_share_one_related_part():
    m = parsed(build_message(spec("![a](cid:p1) ![b](cid:p2)", [picture("p1"), picture("p2", name="b.png")]))["raw"])
    assert shape(m) == ["multipart/alternative", "text/plain", ["multipart/related", "text/html", "image/png", "image/png"]]


def test_a_picture_with_no_mark_left_in_the_text_goes_as_a_file():
    m = parsed(build_message(spec("no mark here", [picture()]))["raw"])
    assert shape(m) == ["multipart/mixed", "text/plain", "image/png"]
    assert next(p for p in m.walk() if p.get_content_type() == "image/png").get_content_disposition() == "attachment"


def test_what_is_sent_says_the_pictures_name_in_its_text_and_a_draft_keeps_the_mark():
    made = spec("Here ![a chart](cid:pic1)", [picture()])
    plain = parsed(build_message(made)["raw"]).get_body(preferencelist=("plain",)).get_content()
    draft_text = parsed(build_message(made, draft=True)["raw"]).get_body(preferencelist=("plain",)).get_content()
    assert plain.strip() == "Here [a chart]" and draft_text.strip() == "Here ![a chart](cid:pic1)"


def test_a_reply_shows_the_picture_above_its_quote():
    original = {"from_name": "Lin", "from_addr": "lin@example.test", "subject": "x", "message_id": "<o@example.test>", "refs": None, "in_reply_to": None}
    body = {"date": "Mon, 2 Nov 2026 12:00:00 +0000", "to": ACCT, "cc": None, "text": "Which?", "html": "<p>Which?</p>"}
    made = composition.as_reply("![a](cid:pic1)", original, body, {"pic1"})
    m = parsed(build_message({**spec("x", [picture()], html=made.html), "text": made.text})["raw"])
    assert shape(m) == ["multipart/alternative", "text/plain", ["multipart/related", "text/html", "image/png"]]
    assert made.html.startswith('<p><img src="cid:pic1@mailmind"') and "<p>Which?</p>" in made.html


def test_what_goes_out_reads_back_as_an_inline_picture_that_a_forward_leaves_alone():
    parts = mime.parse(build_message(spec("![a](cid:pic1)", [picture()]))["raw"])["attachments"]
    assert [(a["content_id"], a["is_inline"], a["travels"]) for a in parts] == [("pic1@mailmind", 1, False)]
