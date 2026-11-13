//! A message opened: its inline images embedded as its frame may hold them, and which folder is an account's
//! Drafts by its name alone.

use std::borrow::Cow;

use mm_content::mime::Inline;
use mm_mailbox::inline_images::{BUDGET, embed};
use mm_mailbox::roles::names_drafts;

fn part(id: &str, kind: &str, data: &[u8]) -> Inline<'static> {
    Inline { content_id: id.to_owned(), content_type: kind.to_owned(), data: Cow::Owned(data.to_vec()) }
}

#[test]
fn a_raster_image_is_embedded_whatever_the_reference_looks_like() {
    let parts = [part("logo@x", "image/png", b"PNG")];
    assert_eq!(embed(r#"<img src="cid:logo@x">"#, &parts), r#"<img src="data:image/png;base64,UE5H">"#);
    assert_eq!(embed("<img SRC = 'CID:<logo@x>'>", &parts), "<img src='data:image/png;base64,UE5H'>");
    assert_eq!(embed(r#"<img src="cid: logo@x ">"#, &parts), r#"<img src="data:image/png;base64,UE5H">"#);
}

#[test]
fn what_isnt_a_known_raster_part_stays_a_reference() {
    let parts = [part("a", "image/svg+xml", b"<svg/>"), part("b", "image/png", b"PNG")];
    for html in [r#"<img src="cid:a">"#, r#"<img src="cid:nowhere">"#, r#"<img src="cid:b'>"#, r#"<img src="cid:">"#] {
        assert_eq!(embed(html, &parts), html);
    }
}

#[test]
fn a_content_id_given_twice_is_the_later_parts() {
    let parts = [part("a", "image/png", b"one"), part("a", "image/gif", b"two")];
    assert_eq!(embed(r#"<img src="cid:a">"#, &parts), r#"<img src="data:image/gif;base64,dHdv">"#);
}

#[test]
fn the_budget_stops_embedding_and_the_rest_stay_references() {
    let big = vec![0u8; BUDGET - 1];
    let parts = [part("a", "image/png", &big), part("b", "image/png", b"xy")];
    let html = embed(r#"<img src="cid:a"><img src="cid:b"><img src="cid:a">"#, &parts);
    assert!(html.starts_with(r#"<img src="data:image/png;base64,"#));
    assert!(html.ends_with(r#"<img src="cid:b"><img src="cid:a">"#));
}

#[test]
fn a_drafts_folder_is_named_at_the_top_or_under_a_servers_parent() {
    for name in ["Drafts", "drafts", "INBOX.Drafts", "INBOX/Drafts", "[Gmail]/Drafts", "[Google Mail]/Drafts"] {
        assert!(names_drafts(name), "{name}");
    }
    for name in ["Clients/Draft", "Contract drafts", "INBOX", "Sent", "a/b/Drafts"] {
        assert!(!names_drafts(name), "{name}");
    }
}
