"""Reading a raw message: its parts and which are attachments, an attached email as one part, and headers that fold."""
from email.message import EmailMessage

import pytest

from mailmind.compose.domain import attachments
from mailmind.content.text import text_body
from stored_rows import A


def _layered():
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "lin@example.test", A, "Swatches"
    m.set_content("first plain")
    m.add_alternative("<p>first html</p>", subtype="html")
    m.make_mixed()
    for subtype, body in (("plain", "second plain"), ("html", "<p>second html</p>")):
        part = EmailMessage()
        part.set_content(body, subtype=subtype)
        m.attach(part)
    m.add_attachment(b"%PDF-1.4 swatch", maintype="application", subtype="pdf", filename="swatch.pdf")
    m.add_attachment(b"\x89PNG quilt", maintype="image", subtype="png", disposition="inline", cid="<quilt@example.test>")
    return m.as_bytes()


def test_hwf_part_ids_count_every_leaf_in_walk_order_and_extract_part_agrees():
    from mailmind.content import mime
    raw = _layered()
    p = mime.parse(raw)
    assert (p["text"], p["html"]) == ("first plain\n", "<p>first html</p>\n")      # the first of each wins
    assert [(a["part_id"], a["filename"], a["is_inline"]) for a in p["attachments"]] == [
        ("5", "swatch.pdf", 0), ("6", "inline-6", 1)]
    assert mime.extract_part(raw, "5") == (b"%PDF-1.4 swatch", "application/pdf")
    assert mime.extract_part(raw, "6") == (b"\x89PNG quilt", "image/png")
    assert mime.extract_part(raw, "1")[0] == b"first plain\n"


def _with_part(headers):
    """A text part, then one application/octet-stream part carrying `headers`."""
    if b"Content-Type" not in headers:
        headers = b"Content-Type: application/octet-stream\r\n" + headers
    return (b"From: lin@example.test\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"b\"\r\n\r\n"
            b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\n" + headers + b"\r\ndata\r\n--b--\r\n")


ATTACHMENT_RULE = [
    # (case, part headers, (filename, is_inline, content_id), or None when the part isn't listed)
    ("attachment, no name", b"Content-Disposition: attachment\r\n", ("part-2", 0, None)),
    ("attachment with a cid", b"Content-Disposition: attachment; filename=a.bin\r\nContent-ID: <a@example.test>\r\n",
     ("a.bin", 1, "a@example.test")),
    ("a name, no disposition", b"Content-Type: application/octet-stream; name=a.bin\r\n", ("a.bin", 0, None)),
    ("inline with a name, no cid", b"Content-Disposition: inline; filename=a.bin\r\n", ("a.bin", 1, None)),
    ("inline with a name and a cid", b"Content-Disposition: inline; filename=a.bin\r\nContent-ID: <a@example.test>\r\n",
     ("a.bin", 1, "a@example.test")),
    ("a cid alone", b"Content-ID: <a@example.test>\r\n", ("inline-2", 1, "a@example.test")),
    ("nothing", b"", None),
]


@pytest.mark.parametrize("headers,want", [c[1:] for c in ATTACHMENT_RULE], ids=[c[0] for c in ATTACHMENT_RULE])
def test_hwf_which_parts_are_listed_as_attachments(headers, want):
    from mailmind.content import mime
    got = [(a["filename"], a["is_inline"], a["content_id"]) for a in mime.parse(_with_part(headers))["attachments"]]
    assert got == ([want] if want else [])


FORWARD_RULE = [
    # (case, part headers, whether a forward takes it)
    ("attachment, no name", b"Content-Disposition: attachment\r\n", True),
    ("attachment with a cid", b"Content-Disposition: attachment; filename=a.bin\r\nContent-ID: <a@example.test>\r\n", True),
    ("a name, no disposition", b"Content-Type: application/octet-stream; name=a.bin\r\n", True),
    ("a name and a cid", b"Content-Type: application/octet-stream; name=a.bin\r\nContent-ID: <a@example.test>\r\n", False),
    ("inline with a name, no cid", b"Content-Disposition: inline; filename=a.bin\r\n", False),
    ("inline with a name and a cid", b"Content-Disposition: inline; filename=a.bin\r\nContent-ID: <a@example.test>\r\n", False),
    ("a cid alone", b"Content-ID: <a@example.test>\r\n", False),
]


@pytest.mark.parametrize("headers,taken", [c[1:] for c in FORWARD_RULE], ids=[c[0] for c in FORWARD_RULE])
def test_hwf_a_part_says_whether_a_forward_takes_it(headers, taken):
    from mailmind.content import mime
    raw = _with_part(headers)
    assert [a["travels"] for a in mime.parse(raw)["attachments"]] == [taken]
    assert len(attachments.extract(raw)) == int(taken)


def test_hwf_outlooks_unnamed_invite_is_listed_but_stays_behind_on_a_forward():
    from mailmind.content import mime
    raw = (b"From: lin@example.test\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=\"b\"\r\n\r\n"
           b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\n"
           b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n--b--\r\n")
    assert [(a["filename"], a["travels"]) for a in mime.parse(raw)["attachments"]] == [("invite.ics", False)]
    assert attachments.extract(raw) == []


def _forwarded_email(outer_text):
    inner = EmailMessage()
    inner["From"], inner["Subject"] = "lin@example.test", "Fabric"
    inner.set_content("the inner note")
    inner.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="swatch.pdf")
    outer = EmailMessage()
    outer["From"], outer["Subject"] = "noor@studio.example.test", "Fwd: Fabric"
    if outer_text:
        outer.set_content("see attached")
    else:
        outer.add_alternative("<p>see attached</p>", subtype="html")
    outer.add_attachment(inner)
    return outer.as_bytes()


def test_hwf_an_attached_emails_files_are_not_listed_as_the_outers():
    from mailmind.content import mime
    assert [a["content_type"] for a in mime.parse(_forwarded_email(True))["attachments"]] == ["message/rfc822"]


def test_hwf_an_attached_emails_text_is_not_taken_for_the_outers():
    from mailmind.content import mime
    assert mime.parse(_forwarded_email(False))["text"] is None


def test_hwf_an_attached_email_is_one_part_of_the_right_size():
    from mailmind.content import mime
    raw = _forwarded_email(True)
    [att] = mime.parse(raw)["attachments"]
    data, ctype = mime.extract_part(raw, att["part_id"])
    assert ctype == "message/rfc822" and att["size"] == len(data) and b"Subject: Fabric" in data


def test_hwf_an_attached_email_with_no_file_name_is_still_listed():
    from mailmind.content import mime
    raw = _forwarded_email(True).replace(b"Content-Disposition: attachment\n", b"")
    assert [(a["filename"], a["content_type"]) for a in mime.parse(raw)["attachments"]] == [
        ("message.eml", "message/rfc822")]


def test_hwf_a_snippet_leaves_an_attached_emails_text_out():
    import email
    assert "the inner note" not in text_body(email.message_from_bytes(_forwarded_email(False)))


def test_hwf_a_forward_carries_an_attached_email_whole_and_not_base64():
    import email
    from email import policy
    from mailmind.compose import api as compose
    from mailmind.content import mime
    [att] = attachments.extract(_forwarded_email(True))
    assert att["content_type"] == "message/rfc822" and b"Subject: Fabric" in att["data"]
    assert [a["travels"] for a in mime.parse(_forwarded_email(True))["attachments"]] == [True]
    built = compose.build_message({"account": A, "to": ["lin@example.test"], "subject": "Fwd: Fabric", "text": "fyi",
                                   "attachments": [att]})
    [out] = email.message_from_bytes(built["raw"], policy=policy.default).iter_attachments()
    assert out.get_content_type() == "message/rfc822" and out["Content-Transfer-Encoding"] != "base64"
    assert out.get_content()["Subject"] == "Fabric"
    assert [a.get_filename() for a in out.get_content().iter_attachments()] == ["swatch.pdf"]


def test_hwf_an_encoded_line_break_in_to_does_not_break_parsing():
    from mailmind.content import mime
    raw = (b"From: lin@example.test\r\nTo: =?utf-8?q?Bo=0D=0AX?= <bo@example.test>\r\nSubject: x\r\n\r\nhello\r\n")
    to = mime.parse(raw)["to"]
    assert "bo@example.test" in to and "\r" not in to and "\n" not in to


def test_hwf_a_fold_in_a_header_leaves_one_space():
    from mailmind.content import mime
    assert mime.dec("Items\r\n for next week") == "Items for next week"
    assert mime.dec("Items\n\tfor next week") == "Items for next week"


def test_hwf_a_fold_beside_an_encoded_word_keeps_its_space():
    from mailmind.content import mime
    assert mime.dec("Re:\r\n =?utf-8?B?2YXYsdit2KjYpw==?=") == "Re: \u0645\u0631\u062d\u0628\u0627"
    assert mime.dec("=?utf-8?Q?Caf=C3=A9_menu?=\r\n for next week") == "Caf\u00e9 menu for next week"
    assert mime.dec("=?utf-8?Q?no?=\r\n =?utf-8?Q?vembre?=") == "novembre"     # one word, split by the fold


def test_hwf_a_fold_in_a_quoted_sender_keeps_the_address():
    import email
    from mailmind.content import mime
    msg = email.message_from_bytes(b'From: "Bo\r\n Lin" <bo@example.test>\r\n\r\nHi\r\n')
    assert mime.sender_of(msg) == ("Bo Lin", "bo@example.test")
