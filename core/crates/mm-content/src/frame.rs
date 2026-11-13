//! A mail body as the document its frame loads, and an attachment's image as the document its preview loads
//! (`mailmind.content.frame`).
//!
//! The frame's look is its parent's to give, so this holds no style. `<base target="_blank">` is there for a click
//! the parent hasn't caught yet: a sandbox with no popups stops a new tab, so the frame stays on the message.
//!
//! HTML given to `of_html` must have been through the sanitizer.

/// `html.escape` with quotes: text as HTML shows it, and safe in an attribute.
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            c => out.push(c),
        }
    }
    out
}

/// Standard base64, padded, as `base64.b64encode` writes it.
pub fn base64(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [chunk[0], chunk.get(1).copied().unwrap_or(0), chunk.get(2).copied().unwrap_or(0)];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for (i, shift) in [18, 12, 6, 0].into_iter().enumerate() {
            if i <= chunk.len() {
                out.push(char::from(ALPHABET[((n >> shift) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

fn document(inner: &str) -> String {
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head>\
         <body>{inner}</body></html>"
    )
}

/// Sanitized HTML as its frame's document.
pub fn of_html(safe_html: Option<&str>) -> String {
    document(safe_html.unwrap_or_default())
}

/// Plain text, whole and escaped. Folding a quote away is the parent's, which knows how.
pub fn of_text(text: Option<&str>) -> String {
    let text = text.filter(|t| !t.is_empty()).unwrap_or("(no content)");
    document(&format!("<pre>{}</pre>", escape(text)))
}

/// Which of the four kinds of image a preview shows these bytes are, by how they begin and never by what the
/// sender called them. An SVG is none of them: it is a document, and can hold a script.
pub fn image_type(data: &[u8]) -> Option<&'static str> {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some("image/png")
    } else if data.starts_with(b"\xff\xd8\xff") {
        Some("image/jpeg")
    } else if data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a") {
        Some("image/gif")
    } else if data.get(..4) == Some(b"RIFF") && data.get(8..12) == Some(b"WEBP") {
        Some("image/webp")
    } else {
        None
    }
}

/// One image and nothing else, as large as fits its frame. `media_type` is `image_type`'s. The bytes are in the
/// document, since the policy on it fetches nothing.
pub fn of_image(data: &[u8], media_type: &str) -> String {
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><style>html,body{{height:100%;margin:0}}\
         body{{display:flex;align-items:center;justify-content:center}}img{{max-width:100%;max-height:100%}}</style>\
         </head><body><img alt=\"\" src=\"data:{media_type};base64,{}\"></body></html>",
        base64(data)
    )
}
