//! A body as the document its frame loads, and an image as the document its preview loads, as
//! `mailmind.content.frame` writes them.

use mm_content::frame::{base64, escape, image_type, of_html, of_image, of_text};

const HEAD: &str = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head>";

#[test]
fn base64_is_padded_as_b64encode_writes_it() {
    assert_eq!(base64(b""), "");
    assert_eq!(base64(b"f"), "Zg==");
    assert_eq!(base64(b"fo"), "Zm8=");
    assert_eq!(base64(b"foo"), "Zm9v");
    assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    assert_eq!(base64(&[0xff, 0xfe, 0xfd]), "//79");
}

#[test]
fn escape_quotes_as_html_escape_does() {
    assert_eq!(escape(r#"<a href="x">Lin's & co</a>"#), "&lt;a href=&quot;x&quot;&gt;Lin&#x27;s &amp; co&lt;/a&gt;");
}

#[test]
fn text_is_escaped_whole_and_nothing_says_so() {
    assert_eq!(of_text(Some("1 < 2\r\n")), format!("{HEAD}<body><pre>1 &lt; 2\r\n</pre></body></html>"));
    assert_eq!(of_text(None), format!("{HEAD}<body><pre>(no content)</pre></body></html>"));
    assert_eq!(of_text(Some("")), of_text(None));
}

#[test]
fn html_goes_in_as_it_is() {
    assert_eq!(of_html(Some("<p>Hi</p>")), format!("{HEAD}<body><p>Hi</p></body></html>"));
    assert_eq!(of_html(None), format!("{HEAD}<body></body></html>"));
}

#[test]
fn an_image_is_known_by_its_bytes_never_its_name() {
    assert_eq!(image_type(b"\x89PNG\r\n\x1a\nrest"), Some("image/png"));
    assert_eq!(image_type(b"\xff\xd8\xff\xe0"), Some("image/jpeg"));
    assert_eq!(image_type(b"GIF89a.."), Some("image/gif"));
    assert_eq!(image_type(b"RIFF\0\0\0\0WEBPVP8 "), Some("image/webp"));
    assert_eq!(image_type(b"RIFF\0\0\0\0WAVE"), None);
    assert_eq!(image_type(b"<svg xmlns=\"http://www.w3.org/2000/svg\"/>"), None);
    assert_eq!(image_type(b""), None);
}

#[test]
fn a_preview_carries_its_image() {
    let doc = of_image(b"GIF89a", "image/gif");
    assert!(doc.starts_with("<!doctype html>"));
    assert!(doc.ends_with("<body><img alt=\"\" src=\"data:image/gif;base64,R0lGODlh\"></body></html>"));
}
