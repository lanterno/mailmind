//! The part of a reply or forward you typed, and the quote that comes after it (`content.quote`).

/// The quote a reply's HTML puts the original in.
pub const BLOCKQUOTE: &str =
    "<blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">";

/// `s` with `&`, `<`, `>` and both quotes escaped, as Python's `html.escape` does by default (`quote.html_escape`,
/// which reads None as "").
pub fn html_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
    out
}

/// The HTML a reply or forward opens with: what you typed, before the quote (`quote.own_html`).
pub fn own_html(new_text: &str) -> String {
    format!("<div>{}</div><br>", html_escape(new_text).replace('\n', "<br>"))
}

/// A quote with no HTML of its own, for a message whose own words have some: line for line, its `>` lines as a quote
/// (`quote.quoted_text_html`).
pub fn quoted_text_html(tail: &str) -> String {
    let mut out = String::new();
    let mut lines = tail.trim_matches('\n').split('\n').peekable();
    while let Some(first) = lines.next() {
        let quoted = first.starts_with('>');
        let mut group = vec![first];
        while let Some(line) = lines.next_if(|line| line.starts_with('>') == quoted) {
            group.push(line);
        }
        let inner = group
            .iter()
            .map(|line| {
                let line = match line.strip_prefix("> ") {
                    Some(rest) => rest,
                    None if quoted => &line[1..],
                    None => line,
                };
                html_escape(line)
            })
            .collect::<Vec<_>>()
            .join("<br>");
        if quoted {
            out.push_str(BLOCKQUOTE);
            out.push_str(&inner);
            out.push_str("</blockquote>");
        } else {
            out.push_str("<div>");
            out.push_str(&inner);
            out.push_str("</div>");
        }
    }
    out
}

/// What `reply_bodies` or `forward_bodies` added after what you typed, as text and as HTML, kept so a reopened draft
/// can send its quote again without quoting twice (`quote.quote_tail`). Each is None where `text` or `html` doesn't
/// start with what you typed, or holds nothing after it.
pub fn quote_tail(new_text: &str, text: Option<&str>, html: Option<&str>) -> (Option<String>, Option<String>) {
    let tail = text.and_then(|text| text.strip_prefix(new_text)).filter(|tail| !tail.is_empty());
    let head = own_html(new_text);
    let tail_html = html.and_then(|html| html.strip_prefix(head.as_str())).filter(|tail| !tail.is_empty());
    (tail.map(str::to_owned), tail_html.map(str::to_owned))
}
