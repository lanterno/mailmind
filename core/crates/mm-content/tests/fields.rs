//! A message's header fields as a sync stores them, and the text a list shows of its body.

use mm_content::mime::{fields, has_invite, text_body};

const HEADERS: &[u8] = b"From: =?utf-8?q?Ad=C3=A1?= Lovelace <Ada@Example.TEST>\r\n\
To: Bea <bea@example.test>, \"Cy, Esq.\" <CY@example.test>\r\n\
Cc: team: dee@example.test, 'eve@example.test'.;\r\n\
Subject: =?utf-8?q?Caf=C3=A9?=\r\n\
\x20 plans\r\n\
Date: Mon, 2 Nov 2026 09:30:00 +0100\r\n\
Message-ID:  <m-1@example.test> \r\n\
In-Reply-To: <m-0@example.test>\r\n\
References: <a@example.test>\r\n <m-0@example.test>\r\n\
List-Unsubscribe: <mailto:leave@example.test?subject=bye>,\r\n <https://example.test/u?x=1>\r\n\
List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\
Subject: the second one is never read\r\n\
\r\n";

#[test]
fn the_fields_a_sync_stores_are_read_as_pythons_email_read_them() {
    let f = fields(HEADERS);
    assert_eq!(f.from_name, "Ad\u{e1} Lovelace");
    assert_eq!(f.from_addr, "ada@example.test");
    assert_eq!(f.subject, "Caf\u{e9} plans");
    assert_eq!(f.date_iso.as_deref(), Some("2026-11-02T09:30:00+01:00"));
    assert_eq!(f.ts, 1_793_608_200);
    assert_eq!(f.message_id, "<m-1@example.test>");
    assert_eq!(f.in_reply_to, "<m-0@example.test>");
    assert_eq!(f.refs, "<a@example.test>\r  <m-0@example.test>");
    assert!(f.has_list_unsub);
    assert_eq!(f.unsub_method, "one-click");
    assert_eq!(
        f.unsub_raw,
        "<mailto:leave@example.test?subject=bye>, <https://example.test/u?x=1> | POST:list-unsubscribe=one-click"
    );
    assert_eq!(f.recipient_addrs, ["bea@example.test", "cy@example.test", "dee@example.test", "eve@example.test"]);
    assert_eq!(
        f.recipients,
        "Bea <bea@example.test>, \"Cy, Esq.\" <CY@example.test> team: dee@example.test, 'eve@example.test'.;"
    );
}

#[test]
fn what_is_missing_or_wont_read_is_empty() {
    let f = fields(b"Subject: hello\r\nDate: not a date\r\nList-ID: <news.example.test>\r\n\r\n");
    assert_eq!((f.from_name.as_str(), f.from_addr.as_str(), f.subject.as_str()), ("", "", "hello"));
    assert_eq!((f.date_iso, f.ts), (None, 0));
    assert!(f.has_list_unsub);
    assert_eq!((f.unsub_method, f.unsub_raw.as_str()), ("none", ""));
    assert_eq!(
        fields(b"Date: Mon, 2 Nov 2026 09:30:00 -0000\r\n\r\n").date_iso.as_deref(),
        Some("2026-11-02T09:30:00+00:00")
    );
}

#[test]
fn a_bare_https_link_is_a_page_and_a_mailto_wins_over_it() {
    let page = fields(b"List-Unsubscribe: <https://example.test/u>\r\n\r\n");
    assert_eq!(page.unsub_method, "http-page");
    let mail = fields(b"List-Unsubscribe: <https://example.test/u>, <mailto:u@example.test>\r\n\r\n");
    assert_eq!(mail.unsub_method, "mailto");
}

#[test]
fn a_bodys_text_is_its_plain_part_or_its_htmls_text_collapsed() {
    let plain = b"Content-Type: text/plain\r\n\r\nHello   there\xe2\x80\x8b\r\n\r\n\r\n\r\nBye\r\n";
    assert_eq!(text_body(plain), "Hello there\n\nBye");
    let html = b"Content-Type: text/html\r\n\r\n<html><head><title>T</title><style>p{}</style></head>\
<body><!-- hidden --><p>Fish &amp; chips&nbsp;today</p></body></html>\r\n";
    assert_eq!(text_body(html), "Fish & chips today");
}

#[test]
fn an_invite_is_a_calendar_part_or_an_ics_file() {
    let invite = b"Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n\
--b\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n--b--\r\n";
    assert!(has_invite(invite));
    assert!(!has_invite(b"Content-Type: text/plain\r\n\r\nhi\r\n"));
}
