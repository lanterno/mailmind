//! Servers' answers read as the session reads them.

#![allow(clippy::unwrap_used)]

use super::*;

fn said(lines: &[&str]) -> Said {
    read(lines.iter().map(|l| format!("{l}\r\n")).collect::<String>().as_bytes())
}

#[test]
fn a_select_says_how_many_and_its_numbers() {
    assert_eq!(said(&["* 3 EXISTS"]), Said::Exists(3));
    assert_eq!(said(&["* OK [UIDVALIDITY 7] UIDs valid"]), Said::Code(Code::UidValidity(7)));
    assert_eq!(said(&["* OK [UIDNEXT 12] Predicted next UID"]), Said::Code(Code::UidNext(12)));
    assert_eq!(said(&["* OK [HIGHESTMODSEQ 99] Highest"]), Said::Code(Code::HighestModSeq(99)));
    assert_eq!(said(&["* FLAGS (\\Seen \\Flagged)"]), Said::Other);
    assert_eq!(said(&["* 0 RECENT"]), Said::Other);
}

#[test]
fn a_search_says_its_uids_with_or_without_a_mod_sequence() {
    assert_eq!(said(&["* SEARCH 2 5 9"]), Said::Search(vec![2, 5, 9]));
    assert_eq!(said(&["* SEARCH"]), Said::Search(vec![]));
    assert_eq!(said(&["* SEARCH 4 6 (MODSEQ 917)"]), Said::Search(vec![4, 6]));
}

#[test]
fn a_fetch_says_the_uid_flags_and_the_section_as_its_literal_held_it() {
    let header = b"Subject: Plans\r\nMessage-ID: <a@example.test>\r\n\r\n";
    let pieces = [
        Piece::Text(format!(
            "* 1 FETCH (UID 4 FLAGS (\\Seen $Forwarded) BODY[HEADER.FIELDS (SUBJECT)] {{{}}}",
            header.len()
        )),
        Piece::Literal(header.to_vec()),
        Piece::Text(")".to_owned()),
    ];
    assert_eq!(
        read(&raw(&pieces)),
        Said::Fetch(Fetched {
            uid: Some(4),
            flags: Some(vec!["\\Seen".to_owned(), "$Forwarded".to_owned()]),
            section: Some(header.to_vec()),
            gmail_thread: None,
            gmail_labels: None,
        })
    );
}

#[test]
fn a_flags_fetch_says_the_flags_even_with_none() {
    let Said::Fetch(fetched) = said(&["* 2 FETCH (UID 8 FLAGS ())"]) else { panic!("not a fetch") };
    assert_eq!((fetched.uid, fetched.flags), (Some(8), Some(vec![])));
    assert_eq!(fetched.section, None);
}

#[test]
fn a_nil_section_is_no_section() {
    let Said::Fetch(fetched) = said(&["* 2 FETCH (UID 8 BODY[] NIL)"]) else { panic!("not a fetch") };
    assert_eq!(fetched.section, None);
}

#[test]
fn gmails_thread_and_labels_are_read_as_a_person_reads_them() {
    let Said::Fetch(fetched) =
        said(&[r#"* 1 FETCH (X-GM-THRID 1278455344230334865 X-GM-LABELS (\Inbox "Caf&AOk-" "say \"hi\"") UID 3)"#])
    else {
        panic!("not a fetch");
    };
    assert_eq!(fetched.uid, Some(3));
    assert_eq!(fetched.gmail_thread, Some(1_278_455_344_230_334_865));
    assert_eq!(fetched.gmail_labels, Some(vec!["\\Inbox".to_owned(), "Caf\u{e9}".to_owned(), "say \"hi\"".to_owned()]));
}

#[test]
fn the_tagged_line_says_an_appended_messages_uid() {
    assert_eq!(said(&["MM4 OK [APPENDUID 9 13] APPEND completed"]), Said::Code(Code::AppendUid(13)));
    assert_eq!(said(&["MM5 OK done"]), Said::Other);
}

#[test]
fn capabilities_are_read_upper_case() {
    assert_eq!(
        said(&["* CAPABILITY IMAP4rev1 Move AUTH=plain"]),
        Said::Capabilities(vec!["IMAP4REV1".to_owned(), "MOVE".to_owned(), "AUTH=PLAIN".to_owned()])
    );
    assert_eq!(said(&["* ENABLED CONDSTORE"]), Said::Capabilities(vec!["CONDSTORE".to_owned()]));
}

#[test]
fn what_no_reader_knows_is_said_as_other() {
    assert_eq!(said(&["* 5 EXPUNGE"]), Said::Other);
    assert_eq!(said(&["* NONSENSE (("]), Said::Other);
    assert_eq!(read(b"no line end"), Said::Other);
}
