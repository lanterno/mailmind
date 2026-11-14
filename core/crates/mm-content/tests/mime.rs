//! Reading a raw message, as the Python app read it: every expected value here is what
//! `mailmind.content.mime.parse`, `extract_part` and `inline_parts` returned for the same bytes before they called
//! this crate, except where a test says the answer changed and why. The Python suite holds the two to the same
//! answers over many more messages (tests/unit/content/test_mime_in_rust.py).

use std::borrow::Cow;

use mm_content::mime::{Attachment, Inline, extract_part, inline_parts, parse};

const HEAD: &str = "From: Ada Lin <ada@example.test>\r\nTo: bo@example.test\r\nSubject: Swatches\r\n\
                    Date: Mon, 2 Nov 2026 12:00:00 +0000\r\nMIME-Version: 1.0\r\n";

fn message(rest: &str) -> Vec<u8> {
    [HEAD.as_bytes(), rest.as_bytes()].concat()
}

fn file(part_id: u32, filename: &str, content_type: &str, size: usize) -> Attachment {
    Attachment {
        part_id,
        filename: filename.to_owned(),
        content_type: content_type.to_owned(),
        size,
        content_id: None,
        is_inline: false,
        travels: true,
    }
}

fn picture(part_id: u32, filename: &str, content_type: &str, size: usize, content_id: &str) -> Attachment {
    Attachment {
        part_id,
        filename: filename.to_owned(),
        content_type: content_type.to_owned(),
        size,
        content_id: Some(content_id.to_owned()),
        is_inline: true,
        travels: false,
    }
}

fn part(raw: &[u8], part_id: u32) -> Option<(Vec<u8>, String)> {
    extract_part(raw, part_id).map(|(data, content_type)| (data.into_owned(), content_type))
}

fn text(raw: &[u8]) -> Option<String> {
    parse(raw).text.map(Cow::into_owned)
}

// -- the body and the headers --------------------------------------------------------------------------------------

#[test]
fn a_plain_message_is_its_text_and_headers() {
    let raw = message(
        "Cc: \"Lin, Bo\" <bo@example.test>, Team: a@example.test, b@example.test;\r\n\
         Reply-To: Desk <desk@example.test>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello there\r\n",
    );
    let parsed = parse(&raw);
    assert_eq!(parsed.text.as_deref(), Some("Hello there\r\n"));
    assert_eq!(parsed.html, None);
    assert!(parsed.attachments.is_empty());
    assert_eq!(parsed.to, "bo@example.test");
    assert_eq!(parsed.cc, "\"Lin, Bo\" <bo@example.test>, Team: a@example.test, b@example.test;");
    assert_eq!(parsed.reply_to, "desk@example.test");
    assert_eq!(parsed.date, "Mon, 02 Nov 2026 12:00:00 +0000");
    assert_eq!(part(&raw, 1), Some((b"Hello there\r\n".to_vec(), "text/plain".to_owned())));
    assert_eq!(part(&raw, 2), None);
}

#[test]
fn text_that_needs_no_decoding_is_lent_from_the_message_not_copied() {
    let raw = message("Content-Type: text/plain\r\n\r\nHello there\r\n");
    assert!(matches!(parse(&raw).text, Some(Cow::Borrowed(_))));
    assert!(matches!(extract_part(&raw, 1), Some((Cow::Borrowed(_), _))));
}

#[test]
fn an_alternative_gives_its_text_and_its_html() {
    let raw = message(
        "Content-Type: multipart/alternative; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nplain\r\n\
         --b\r\nContent-Type: text/html\r\n\r\n<p>html</p>\r\n--b--\r\n",
    );
    let parsed = parse(&raw);
    assert_eq!(parsed.text.as_deref(), Some("plain"));
    assert_eq!(parsed.html.as_deref(), Some("<p>html</p>"));
    assert_eq!(part(&raw, 2), Some((b"<p>html</p>".to_vec(), "text/html".to_owned())));
}

#[test]
fn the_first_text_and_the_first_html_are_the_body() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nfirst\r\n\
         --b\r\nContent-Type: text/plain\r\n\r\nsecond\r\n--b\r\nContent-Type: text/html\r\n\r\n<p>1</p>\r\n\
         --b\r\nContent-Type: text/html\r\n\r\n<p>2</p>\r\n--b--\r\n",
    );
    let parsed = parse(&raw);
    assert_eq!((parsed.text.as_deref(), parsed.html.as_deref()), (Some("first"), Some("<p>1</p>")));
}

#[test]
fn a_message_with_no_header_block_is_its_text() {
    let parsed = parse(b"just some words");
    assert_eq!(parsed.text.as_deref(), Some("just some words"));
    assert_eq!((parsed.to.as_str(), parsed.date.as_str()), ("", ""));
    assert_eq!(part(b"just some words", 1), Some((b"just some words".to_vec(), "text/plain".to_owned())));
    assert_eq!(parse(b"").text.as_deref(), Some(""));
}

// -- parts and their numbers ---------------------------------------------------------------------------------------

#[test]
fn an_attachment_is_numbered_among_the_leaves_and_decoded() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n\
         --b\r\nContent-Type: application/pdf; name=\"swatch.pdf\"\r\n\
         Content-Disposition: attachment; filename=\"swatch.pdf\"\r\nContent-Transfer-Encoding: base64\r\n\r\n\
         JVBERi0xLjQK\r\n--b--\r\n",
    );
    let parsed = parse(&raw);
    assert_eq!(parsed.text.as_deref(), Some("see attached"));
    assert_eq!(parsed.attachments, vec![file(2, "swatch.pdf", "application/pdf", 9)]);
    assert_eq!(part(&raw, 2), Some((b"%PDF-1.4\n".to_vec(), "application/pdf".to_owned())));
    assert_eq!(part(&raw, 3), None);
    assert_eq!(part(&raw, 0), None);
}

#[test]
fn a_picture_the_text_shows_by_its_content_id_stays_with_the_text() {
    let raw = message(
        "Content-Type: multipart/related; boundary=b\r\n\r\n--b\r\nContent-Type: text/html\r\n\r\n\
         <img src=\"cid:pic1@example.test\">\r\n--b\r\nContent-Type: image/png\r\nContent-ID: <pic1@example.test>\r\n\
         Content-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n--b--\r\n",
    );
    let parsed = parse(&raw);
    assert_eq!(parsed.attachments, vec![picture(2, "inline-2", "image/png", 8, "pic1@example.test")]);
    assert_eq!(
        inline_parts(&raw),
        vec![Inline {
            content_id: "pic1@example.test".to_owned(),
            content_type: "image/png".to_owned(),
            data: Cow::Borrowed(b"\x89PNG\r\n\x1a\n"),
        }]
    );
}

#[test]
fn a_content_id_given_twice_shows_the_later_part_in_the_first_place() {
    let raw = message(
        "Content-Type: multipart/related; boundary=b\r\n\r\n--b\r\nContent-Type: text/html\r\n\r\n<img src=cid:p>\r\n\
         --b\r\nContent-Type: image/png\r\nContent-ID: <p>\r\n\r\nfirst\r\n--b\r\nContent-Type: image/gif\r\n\
         Content-ID: <q>\r\n\r\nother\r\n--b\r\nContent-Type: image/jpeg\r\nContent-ID: <p>\r\n\r\nsecond\r\n--b--\r\n",
    );
    let found: Vec<_> =
        inline_parts(&raw).into_iter().map(|inline| (inline.content_id, inline.content_type, inline.data)).collect();
    assert_eq!(
        found,
        vec![
            ("p".to_owned(), "image/jpeg".to_owned(), Cow::Borrowed(&b"second"[..])),
            ("q".to_owned(), "image/gif".to_owned(), Cow::Borrowed(&b"other"[..])),
        ]
    );
    assert_eq!(parse(&raw).attachments.len(), 3);
}

#[test]
fn a_named_picture_marked_inline_stays_and_one_not_marked_travels() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nx\r\n--b\r\n\
         Content-Type: image/png\r\nContent-Disposition: inline; filename=pic.png\r\nContent-ID: <pic>\r\n\r\nPNG\r\n\
         --b\r\nContent-Type: image/png; name=named.png\r\n\r\nPNG\r\n--b--\r\n",
    );
    assert_eq!(
        parse(&raw).attachments,
        vec![picture(2, "pic.png", "image/png", 3, "pic"), file(3, "named.png", "image/png", 3)]
    );
}

#[test]
fn an_unnamed_invite_is_listed_as_invite_ics() {
    let raw = message(
        "Content-Type: multipart/alternative; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\ninvite\r\n\
         --b\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n--b--\r\n",
    );
    let invite = Attachment { is_inline: true, travels: false, ..file(2, "invite.ics", "text/calendar", 30) };
    assert_eq!(parse(&raw).attachments, vec![invite]);
}

#[test]
fn an_attached_email_is_one_leaf_and_comes_as_it_was_sent() {
    // Python wrote the email out again from what it had parsed, with LF line ends: 53 bytes, where it was sent in 56.
    let inner = "From: Bo <bo@example.test>\r\nSubject: Inner\r\n\r\ninner text";
    let raw = message(&format!(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nfyi\r\n--b\r\n\
         Content-Type: message/rfc822\r\n\r\n{inner}\r\n--b\r\nContent-Type: text/plain\r\n\r\nafter\r\n--b--\r\n"
    ));
    let parsed = parse(&raw);
    assert_eq!(parsed.attachments, vec![file(2, "message.eml", "message/rfc822", 56)]);
    assert_eq!(part(&raw, 2), Some((inner.as_bytes().to_vec(), "message/rfc822".to_owned())));
    assert_eq!(part(&raw, 3), Some((b"after".to_vec(), "text/plain".to_owned())));
}

#[test]
fn a_digest_holds_emails() {
    let raw = message(
        "Content-Type: multipart/digest; boundary=d\r\n\r\n--d\r\n\r\nFrom: a@example.test\r\nSubject: one\r\n\r\n\
         first\r\n--d--\r\n",
    );
    assert_eq!(parse(&raw).attachments, vec![file(1, "message.eml", "message/rfc822", 43)]);
}

#[test]
fn a_type_that_wont_read_is_text_plain_and_one_cut_short_stays_as_written() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: TEXT/PLAIN\r\n\r\none\r\n\
         --b\r\nContent-Type: text/plain/extra\r\n\r\ntwo\r\n--b\r\nContent-Type: image\r\n\r\nthree\r\n\
         --b\r\nContent-Type: application/\r\n\r\nfour\r\n--b--\r\n",
    );
    let types: Vec<_> = (1..=4).filter_map(|id| part(&raw, id)).map(|(_, content_type)| content_type).collect();
    assert_eq!(types, ["text/plain", "text/plain", "text/plain", "application/"]);
    // A multipart with no subtype is text/plain too, its delimiters and all, as RFC 2045 has it.
    let no_subtype = message(
        "Content-Type: multipart; boundary=b\r\n\r\n--b\r\nContent-Type: text/html\r\n\r\n<b>x</b>\r\n--b--\r\n",
    );
    assert_eq!(
        part(&no_subtype, 1),
        Some((b"--b\r\nContent-Type: text/html\r\n\r\n<b>x</b>\r\n--b--\r\n".to_vec(), "text/plain".to_owned()))
    );
}

#[test]
fn file_names_read_as_their_senders_wrote_them() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: application/octet-stream\r\n\
         Content-Disposition: attachment; filename=\"=?utf-8?Q?r=C3=A9?= =?utf-8?Q?sum=C3=A9.pdf?=\"\r\n\r\nA\r\n\
         --b\r\nContent-Type: application/octet-stream\r\n\
         Content-Disposition: attachment; filename*=utf-8''r%C3%A9sum%C3%A9.txt\r\n\r\nB\r\n\
         --b\r\nContent-Type: application/pdf; name=\"  spaced.pdf  \"\r\n\r\nC\r\n\
         --b\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment\r\n\r\nD\r\n--b--\r\n",
    );
    let names: Vec<_> = parse(&raw).attachments.into_iter().map(|a| a.filename).collect();
    assert_eq!(names, ["r\u{e9}sum\u{e9}.pdf", "r\u{e9}sum\u{e9}.txt", "spaced.pdf", "part-4"]);
}

// -- transfer encodings and charsets -------------------------------------------------------------------------------

#[test]
fn quoted_printable_decodes_as_python_decoded_it() {
    // `=ZZ` and a lone `=` stay, `==` is one `=`, and white space at a line's end stays. An `=` that only blanks
    // follow to the end of the line is a soft break: Python kept the `=` and the blanks, where RFC 2045 says a
    // transport may have added them.
    let raw = message(
        "Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n\
         caf=C3=A9 soft=\r\nbreak, 100=ZZ, =3d, ==, trailing  \r\nend=  \r\nnext\r\n",
    );
    assert_eq!(text(&raw).as_deref(), Some("caf\u{e9} softbreak, 100=ZZ, =, =, trailing  \r\nendnext\r\n"));
}

#[test]
fn base64_decodes_what_it_can() {
    // Python gave back the encoded text whole when its length left one character over; what decodes is the better
    // answer.
    let raw = message("Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n\r\naGVsbG8gd");
    assert_eq!(text(&raw).as_deref(), Some("hello "));
}

#[test]
fn uuencode_decodes() {
    let raw = message(
        "Content-Type: text/plain\r\nContent-Transfer-Encoding: x-uuencode\r\n\r\nbegin 644 a.txt\r\n%:&5L;&\\*\r\n`\r\nend\r\n",
    );
    assert_eq!(text(&raw).as_deref(), Some("hello"));
}

#[test]
fn a_charset_reads_as_a_browser_reads_it() {
    // Python read iso-8859-1's bytes 0x80 to 0x9F as control characters; every browser and mail client reads them as
    // windows-1252's euro sign and curly quotes, which is what a sender meant.
    let raw = message("Content-Type: text/plain; charset=iso-8859-1\r\n\r\ncaf\u{e9}\r\n");
    let raw = [&raw[..raw.len() - 7], b"caf\xe9 \x80\r\n"].concat();
    assert_eq!(text(&raw).as_deref(), Some("caf\u{e9} \u{20ac}\r\n"));
    let bom = message("Content-Type: text/plain; charset=utf-8-sig\r\n\r\n\u{feff}x\r\n");
    assert_eq!(text(&bom).as_deref(), Some("x\r\n"));
}

#[test]
fn a_charset_no_one_knows_is_read_as_utf8() {
    // Python gave an empty text: the message showed nothing at all.
    let raw = message("Content-Type: text/plain; charset=x-unknown\r\n\r\ncaf\u{e9}\r\n");
    assert_eq!(text(&raw).as_deref(), Some("caf\u{e9}\r\n"));
}

#[test]
fn format_flowed_is_read_as_it_was_written() {
    let raw = message(
        "Content-Type: text/plain; format=flowed\r\n\r\nA soft \r\nbreak.\r\n> quoted \r\n> on\r\n-- \r\nBo\r\n",
    );
    assert_eq!(text(&raw).as_deref(), Some("A soft break.\n> quoted on\n-- \nBo\n"));
    let delsp = message("Content-Type: text/plain; format=flowed; delsp=yes\r\n\r\nlongw \r\nord.\r\n");
    assert_eq!(text(&delsp).as_deref(), Some("longword.\n"));
}

// -- broken structure ----------------------------------------------------------------------------------------------

#[test]
fn a_multipart_with_no_parts_is_text() {
    assert_eq!(text(&message("Content-Type: multipart/mixed\r\n\r\njust text\r\n")).as_deref(), Some("just text\r\n"));
    let close_first =
        message("Content-Type: multipart/mixed; boundary=b\r\n\r\npreamble text\r\n--b--\r\nepilogue\r\n");
    assert_eq!(text(&close_first).as_deref(), Some("preamble text\r\n"));
}

#[test]
fn a_multipart_that_never_closed_keeps_its_last_part() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n\
         --b\r\nContent-Type: application/pdf; name=a.pdf\r\n\r\n%PDF\r\n",
    );
    assert_eq!(parse(&raw).attachments, vec![file(2, "a.pdf", "application/pdf", 4)]);
    assert_eq!(part(&raw, 2), Some((b"%PDF".to_vec(), "application/pdf".to_owned())));
}

#[test]
fn a_message_cut_off_keeps_the_part_it_was_cut_in() {
    let raw = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\nContent-Ty",
    );
    assert_eq!(part(&raw, 2), Some((b"Content-Ty".to_vec(), "text/plain".to_owned())));
    let at_a_delimiter =
        message("Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b");
    assert_eq!(part(&at_a_delimiter, 2), Some((Vec::new(), "text/plain".to_owned())));
}

#[test]
fn delimiters_with_nothing_between_hold_no_part_but_one_before_the_close_does() {
    let repeated = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n",
    );
    assert_eq!((part(&repeated, 1), part(&repeated, 2)), (Some((b"hi".to_vec(), "text/plain".to_owned())), None));
    let empty = message("Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n--b--\r\n");
    assert_eq!(part(&empty, 1), Some((Vec::new(), "text/plain".to_owned())));
}

#[test]
fn a_multipart_with_an_empty_boundary_splits_at_lines_of_two_dashes() {
    // RFC 2046 has no empty boundary, but Python split at `--` lines, and mail-parser read no parts at all: the HTML
    // in this one would have been lost.
    let nested = message(
        "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nfirst\r\n\
         --b\r\nContent-Type: multipart/alternative; boundary=\"\"\r\n\r\npre\r\n--\r\nContent-Type: text/html\r\n\r\n\
         <b>x</b>\r\n-- \r\nBo\r\n---\r\n----\r\n--b--\r\n",
    );
    assert_eq!(parse(&nested).html.as_deref(), Some("<b>x</b>"));
    assert_eq!(part(&nested, 3), Some((b"Bo\r\n---".to_vec(), "text/plain".to_owned())));
    // An unquoted empty boundary is none, as Python read it: the body is the text.
    let bare =
        message("Content-Type: multipart/mixed; boundary=\r\n\r\n--\r\nContent-Type: text/plain\r\n\r\nhi\r\n----\r\n");
    assert_eq!(text(&bare).as_deref(), Some("--\r\nContent-Type: text/plain\r\n\r\nhi\r\n----\r\n"));
}

#[test]
fn a_bare_boundary_with_no_equals_is_an_empty_one() {
    // Python read `boundary` alone as `boundary=""`, after any other parameter too, and split at `--` lines.
    for header in ["boundary", "boundary ", "charset=x; boundary"] {
        let raw = message(&format!(
            "Content-Type: multipart/mixed; {header}\r\n\r\npre\r\n--\r\nContent-Type: text/plain\r\n\r\nfirst\r\n--\r\n\r\nsecond\r\n"
        ));
        assert_eq!(part(&raw, 1), Some((b"first".to_vec(), "text/plain".to_owned())), "{header}");
        assert_eq!(part(&raw, 2), Some((b"second".to_vec(), "text/plain".to_owned())), "{header}");
    }
}

#[test]
fn an_empty_boundary_still_splits_when_a_line_of_dashes_ends_the_header_block() {
    // The `--` line ends the header block, so the body it starts is the one split: ending the block and splitting it
    // in one pass split the body mail-parser had read past that line, and lost the part.
    for boundary in ["boundary", "boundary=\"\""] {
        let raw = message(&format!("Content-Type: multipart/alternative; {boundary}\r\n--\r\n=\"x\"\r\n\r\nbody\r\n"));
        assert_eq!(part(&raw, 1), Some((b"=\"x\"\r\n\r\nbody".to_vec(), "text/plain".to_owned())), "{boundary}");
    }
}

#[test]
fn a_header_block_ends_at_its_first_line_that_isnt_a_header() {
    // mail-parser read on to the blank line, and what came before it was lost.
    let raw = b"From: a@example.test\r\nSubject: s\r\nHello there\r\n\r\nsecond paragraph\r\n";
    assert_eq!(text(raw).as_deref(), Some("Hello there\r\n\r\nsecond paragraph\r\n"));
    let part_with_none =
        message("Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\ntext with no blank line\r\n--b--\r\n");
    assert_eq!(text(&part_with_none).as_deref(), Some("text with no blank line"));
    let lone_cr = b"From: a@example.test\rTo: bo@example.test\r\n\r\nhi\r\n";
    assert_eq!(parse(lone_cr).to, "bo@example.test");
}

#[test]
fn the_first_content_type_of_a_part_is_its_type() {
    let raw = message("Content-Type: text/html\r\nContent-Type: text/plain\r\n\r\n<b>which</b>\r\n");
    assert_eq!(parse(&raw).html.as_deref(), Some("<b>which</b>\r\n"));
}

#[test]
fn lines_ending_in_cr_alone_read_as_lines() {
    // Python kept the lone CRs in the text, which a page shows as spaces.
    let raw = b"From: a@example.test\rTo: bo@example.test\rContent-Type: text/plain\r\rline one\rline two\r";
    let parsed = parse(raw);
    assert_eq!(parsed.text.as_deref(), Some("line one\r\nline two\r\n"));
    assert_eq!(parsed.to, "bo@example.test");
}

// -- headers -------------------------------------------------------------------------------------------------------

#[test]
fn a_date_is_written_the_rfc_5322_way_when_it_reads_as_one() {
    let date = |value: &str| parse(format!("From: a@example.test\r\nDate: {value}\r\n\r\nhi\r\n").as_bytes()).date;
    assert_eq!(date("2 Nov 2026 12:00 GMT"), "Mon, 02 Nov 2026 12:00:00 +0000");
    assert_eq!(date("Mon, 2 Nov 26 12:00:00"), "Mon, 02 Nov 2026 12:00:00 -0000");
    assert_eq!(date("=?utf-8?q?not_a_date?="), "not a date");
    // Python's parse failed whole on a year past 9999, and the message with it.
    assert_eq!(
        date("Mon, 2 Nov 99999999999999999999 12:00:00 +0000"),
        "Mon, 2 Nov 99999999999999999999 12:00:00 +0000"
    );
}

#[test]
fn addresses_are_written_as_python_wrote_them() {
    let to = parse(
        b"From: a@example.test\r\nTo: Bo  Lin <bo@example.test>, bo, Ann <>, \"quoted local\"@example.test, \
          <@relay.example.test:cy@example.test>, Di <di@ example.test>, =?utf-8?q?Caf=C3=A9?= <x@example.test>, \
          Mr. Ed <ed@example.test>, Bo Lin bo@example.test\r\n\r\nhi\r\n",
    )
    .to;
    assert_eq!(
        to,
        "Bo Lin <bo@example.test>, bo, Ann <>, \"quoted local\"@example.test, cy@example.test, \
         Di <di@example.test>, Caf\u{e9} <x@example.test>, \"Mr. Ed\" <ed@example.test>, \"Bo Lin bo\"@example.test"
    );
}

#[test]
fn a_reply_goes_to_an_address_the_header_holds() {
    let reply_to = |value: &str| parse(format!("From: a@example.test\r\n{value}\r\n\r\nhi\r\n").as_bytes()).reply_to;
    assert_eq!(reply_to("Reply-To: x <y> desk@other.test"), "desk@other.test");
    assert_eq!(reply_to("Reply-To: \"boss@bank.test\" <desk@other.test>"), "desk@other.test");
    assert_eq!(reply_to("Reply-To: no address\r\nReply-To: second@example.test."), "second@example.test");
}

// -- the corpus ----------------------------------------------------------------------------------------------------

#[test]
fn every_part_the_corpus_lists_comes_out_whole() {
    let corpus = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/corpus");
    let mut read = 0;
    for entry in std::fs::read_dir(corpus).into_iter().flatten().flatten() {
        let Ok(raw) = std::fs::read(entry.path()) else { continue };
        let parsed = parse(&raw);
        for attachment in &parsed.attachments {
            let (data, content_type) = part(&raw, attachment.part_id).unwrap_or_default();
            assert_eq!((data.len(), content_type.as_str()), (attachment.size, attachment.content_type.as_str()));
        }
        read += 1;
    }
    assert!(read >= 20, "the corpus is at tests/fixtures/corpus");
}
