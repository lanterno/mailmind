//! The cross-site check, on what the browser says of a request in Sec-Fetch-Site.

use axum::http::HeaderMap;

use super::{Refusal, header, lowered};

pub fn is_api(path: &str) -> bool {
    path == "/api" || path.starts_with("/api/")
}

/// A request the page didn't make. Another site's no-cors GET, such as an `<img>`, carries this server's Host and no
/// Origin, so the host check passes it, yet it could still make the server sync a folder or fetch bodies. Browsers
/// mark it Sec-Fetch-Site cross-site, or same-site when it comes from another port on this machine.
///
/// Under /api/ only same-origin passes: the page's own fetches, its event stream and its downloads. A request that
/// says nothing (a browser too old to send the header, or not a browser) or comes from no page (a typed URL) is
/// refused as well. The app itself, `/`, also opens from a typed or bookmarked URL, or from another site's link: a
/// top-level visit does nothing, and its contents can't be read across origins.
pub fn refuse_cross_site(headers: &HeaderMap, path: &str) -> Option<Refusal> {
    let site = lowered(headers, "sec-fetch-site");
    if is_api(path) {
        if site == b"same-origin" {
            return None;
        }
        let error = if site == b"cross-site" || site == b"same-site" {
            "cross-site request"
        } else {
            "same-origin request required"
        };
        return Some(Refusal { status: 403, error });
    }
    if site.is_empty() || site == b"same-origin" || site == b"none" {
        return None;
    }
    if path == "/"
        && header(headers, "sec-fetch-mode") == Some(b"navigate")
        && header(headers, "sec-fetch-dest") == Some(b"document")
    {
        return None;
    }
    Some(Refusal { status: 403, error: "cross-site request" })
}
