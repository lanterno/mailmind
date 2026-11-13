//! What of a message's inline images its frame may hold (`mailbox.domain.inline_images`): `src="cid:..."` made into
//! a `data:` URI, which the frame's policy allows, so a logo shows with nothing fetched. Only a raster image is ever
//! embedded: an SVG or HTML part is active content. A set over the budget stops there, and the rest stay as cid
//! references, which show as broken images and cost nothing.

use std::collections::HashMap;

use mm_content::frame::base64;
use mm_content::mime::Inline;

/// The kinds of image that may be embedded.
pub const RASTER: [&str; 5] = ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"];
/// The bytes one message may embed.
pub const BUDGET: usize = 3 << 20;

/// A reference to a part at `at`: `src`, `=`, a quote, `cid:`, what isn't a quote, and the same quote, with space
/// around the `=` (`src\s*=\s*(['"])cid:([^'"]+)\1`, ignoring case). What it covers, the quote and the id.
fn reference(html: &str, at: usize) -> Option<(usize, char, &str)> {
    let rest = &html[at..];
    if !rest.get(..3)?.eq_ignore_ascii_case("src") {
        return None;
    }
    let mut i = 3;
    let skip = |i: &mut usize| {
        while let Some(c) = rest[*i..].chars().next().filter(|c| c.is_whitespace()) {
            *i += c.len_utf8();
        }
    };
    skip(&mut i);
    rest[i..].strip_prefix('=')?;
    i += 1;
    skip(&mut i);
    let quote = rest[i..].chars().next().filter(|c| matches!(c, '\'' | '"'))?;
    i += 1;
    if !rest.get(i..i + 4)?.eq_ignore_ascii_case("cid:") {
        return None;
    }
    i += 4;
    let id_len = rest[i..].find(['\'', '"']).unwrap_or(rest.len() - i);
    if id_len == 0 || !rest[i + id_len..].starts_with(quote) {
        return None;
    }
    Some((i + id_len + 1, quote, &rest[i..i + id_len]))
}

/// `safe_html` with each reference to one of `parts` that may be embedded, embedded.
pub fn embed(safe_html: &str, parts: &[Inline<'_>]) -> String {
    // A Content-ID given twice is the later part's.
    let by_id: HashMap<&str, &Inline<'_>> = parts.iter().map(|p| (p.content_id.as_str(), p)).collect();
    let mut used = 0usize;
    let mut out = String::with_capacity(safe_html.len());
    let mut copied = 0;
    let mut at = 0;
    while at < safe_html.len() {
        let Some((len, quote, id)) = reference(safe_html, at) else {
            at += safe_html[at..].chars().next().map_or(1, char::len_utf8);
            continue;
        };
        let id = mm_kernel::text::strip(id).trim_matches(['<', '>']);
        if let Some(part) = by_id.get(id)
            && RASTER.contains(&part.content_type.as_str())
            && used + part.data.len() <= BUDGET
        {
            used += part.data.len();
            out.push_str(&safe_html[copied..at]);
            out.push_str(&format!("src={quote}data:{};base64,{}{quote}", part.content_type, base64(&part.data)));
            copied = at + len;
        }
        at += len;
    }
    out.push_str(&safe_html[copied..]);
    out
}
