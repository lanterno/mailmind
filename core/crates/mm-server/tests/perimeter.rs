//! The perimeter's checks, each on its own, as tests/security/test_perimeter.py holds the Python app's.

#![allow(clippy::unwrap_used)]

use axum::http::{HeaderMap, HeaderName, HeaderValue};
use mm_server::perimeter::{self, Refusal};

const PORT: u16 = 8721;
const OWN: &str = "127.0.0.1:8721";
const TOKEN: &str = "the-token";

fn headers(pairs: &[(&str, &str)]) -> HeaderMap {
    let mut map = HeaderMap::new();
    for (name, value) in pairs {
        map.append(
            HeaderName::from_bytes(name.as_bytes()).unwrap(),
            HeaderValue::from_bytes(value.as_bytes()).unwrap(),
        );
    }
    map
}

/// What the page's own fetch sends, and on a POST its Origin.
fn page() -> Vec<(&'static str, &'static str)> {
    vec![("Host", OWN), ("Sec-Fetch-Site", "same-origin")]
}

fn posted() -> Vec<(&'static str, &'static str)> {
    let mut sent = page();
    sent.push(("Origin", "http://127.0.0.1:8721"));
    sent
}

fn refused(error: &'static str) -> Option<Refusal> {
    Some(Refusal { status: 403, error })
}

#[test]
fn this_servers_own_host_passes_in_any_case() {
    for host in [OWN, "localhost:8721", "LOCALHOST:8721"] {
        assert_eq!(perimeter::refuse_host(&headers(&[("Host", host)]), PORT), None, "{host}");
        let origin = format!("http://{}", host.to_lowercase());
        assert_eq!(perimeter::refuse_host(&headers(&[("Host", host), ("Origin", &origin)]), PORT), None);
    }
    assert_eq!(
        perimeter::refuse_host(&headers(&[("Host", OWN), ("Origin", "")]), PORT),
        None,
        "an empty Origin is none"
    );
}

#[test]
fn any_other_host_or_origin_is_refused() {
    let cases: [&[(&str, &str)]; 7] = [
        &[],
        &[("Host", "rebound.example.test")],
        &[("Host", "127.0.0.1:9999")],
        &[("Host", "127.0.0.1")],
        &[("Host", OWN), ("Origin", "http://rebound.example.test")],
        &[("Host", OWN), ("Origin", "https://127.0.0.1:8721")],
        &[("Host", OWN), ("Origin", "null")],
    ];
    for sent in cases {
        assert_eq!(perimeter::refuse_host(&headers(sent), PORT), refused("bad host/origin"), "{sent:?}");
    }
}

#[test]
fn only_the_pages_own_requests_reach_the_api() {
    for site in ["same-origin", "SAME-ORIGIN"] {
        assert_eq!(perimeter::refuse_cross_site(&headers(&[("Sec-Fetch-Site", site)]), "/api/digest"), None);
    }
    for sent in [&[][..], &[("Sec-Fetch-Site", "none")][..]] {
        assert_eq!(
            perimeter::refuse_cross_site(&headers(sent), "/api/digest"),
            refused("same-origin request required")
        );
        assert!(perimeter::refuse_cross_site(&headers(sent), "/api").is_some());
    }
    for site in ["cross-site", "same-site"] {
        assert_eq!(
            perimeter::refuse_cross_site(&headers(&[("Sec-Fetch-Site", site)]), "/api/x"),
            refused("cross-site request")
        );
    }
}

#[test]
fn the_page_opens_from_a_typed_url_or_another_sites_link_but_no_other_way() {
    for sent in [&[][..], &[("Sec-Fetch-Site", "none")][..], &[("Sec-Fetch-Site", "same-origin")][..]] {
        assert_eq!(perimeter::refuse_cross_site(&headers(sent), "/"), None);
        assert_eq!(perimeter::refuse_cross_site(&headers(sent), "/apical"), None, "not under /api/");
    }
    let visit = [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "document")];
    assert_eq!(perimeter::refuse_cross_site(&headers(&visit), "/"), None);
    assert_eq!(perimeter::refuse_cross_site(&headers(&visit), "/m/x"), refused("cross-site request"));
    let framed = [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "iframe")];
    assert_eq!(perimeter::refuse_cross_site(&headers(&framed), "/"), refused("cross-site request"));
}

#[test]
fn a_body_is_judged_by_its_declared_length() {
    let cases = [
        (None, false),
        (Some(""), false),
        (Some("0"), false),
        (Some("1000"), false),
        (Some("1001"), true),
        (Some("-1"), true),
        (Some("-0"), false),
        (Some("lots"), true),
        (Some("99999999999999999999999999999999999999999"), true),
    ];
    for (length, is_refused) in cases {
        let sent = headers(&length.map(|l| vec![("Content-Length", l)]).unwrap_or_default());
        let want = is_refused.then_some(Refusal { status: 413, error: "payload too large" });
        assert_eq!(perimeter::refuse_body(&sent, 1000), want, "{length:?}");
    }
}

#[test]
fn a_post_needs_the_host_then_the_token_then_the_origin() {
    let mut ok = posted();
    ok.push(("X-CSRF-Token", TOKEN));
    assert_eq!(perimeter::refuse_mutation("/api/seen", &headers(&ok), PORT, TOKEN), None);
    let rebound = headers(&[("Host", "evil.example.test"), ("X-CSRF-Token", "nope")]);
    assert_eq!(perimeter::refuse_mutation("/api/seen", &rebound, PORT, TOKEN), refused("bad host/origin"));
    let unmarked = headers(&[("Host", OWN), ("X-CSRF-Token", TOKEN)]);
    assert_eq!(
        perimeter::refuse_mutation("/api/seen", &unmarked, PORT, TOKEN),
        refused("same-origin request required")
    );
    for token in [None, Some("nope"), Some("the-tokenx"), Some("the-toke"), Some("")] {
        let mut sent = posted();
        sent.extend(token.map(|t| ("X-CSRF-Token", t)));
        assert_eq!(
            perimeter::refuse_mutation("/api/seen", &headers(&sent), PORT, TOKEN),
            refused("bad or missing CSRF token"),
            "{token:?}"
        );
    }
    let mut no_origin = page();
    no_origin.push(("X-CSRF-Token", TOKEN));
    assert_eq!(perimeter::refuse_mutation("/api/send", &headers(&no_origin), PORT, TOKEN), refused("origin required"));
}

#[test]
fn a_token_that_isnt_ascii_is_wrong_like_any_other() {
    let mut sent = headers(&posted());
    sent.insert("x-csrf-token", HeaderValue::from_bytes(b"t\xe9st").unwrap());
    assert_eq!(perimeter::refuse_mutation("/api/seen", &sent, PORT, TOKEN), refused("bad or missing CSRF token"));
    assert!(!perimeter::token_matches(&sent, "t\u{e9}st"), "a token is its bytes as sent, never decoded");
}

#[test]
fn a_post_is_judged_by_its_size_before_its_guards_and_a_get_by_its_host_first() {
    let big = headers(&[("Host", "evil.example.test"), ("Content-Length", "1001")]);
    assert_eq!(perimeter::refuse_post("/api/seen", &big, PORT, TOKEN, 1000).map(|r| r.status), Some(413));
    let rebound = headers(&[("Host", "evil.example.test")]);
    assert_eq!(perimeter::refuse_post("/api/seen", &rebound, PORT, TOKEN, 1000).map(|r| r.status), Some(403));
    let both = headers(&[("Host", "evil.example.test"), ("Sec-Fetch-Site", "cross-site")]);
    assert_eq!(perimeter::refuse_get("/api/digest", &both, PORT), refused("bad host/origin"));
    let cross = headers(&[("Host", OWN), ("Sec-Fetch-Site", "cross-site")]);
    assert_eq!(perimeter::refuse_get("/api/digest", &cross, PORT), refused("cross-site request"));
}

#[test]
fn every_answer_says_what_its_body_is_and_no_cache_keeps_the_apis() {
    let names = |path| perimeter::answer_headers(path).iter().map(|(n, _)| *n).collect::<Vec<_>>();
    assert!(!names("/").contains(&"cache-control"));
    assert!(names("/api/folders").contains(&"cache-control") && names("/api").contains(&"cache-control"));
    let mut set = HeaderMap::new();
    set.insert("x-frame-options", HeaderValue::from_static("SAMEORIGIN"));
    perimeter::add_answer_headers(&mut set, "/api/x");
    assert_eq!(set["x-frame-options"], "SAMEORIGIN", "a header the answer set is its own");
    assert_eq!(set["x-content-type-options"], "nosniff");
    assert_eq!(set["cache-control"], "no-store");
    assert_eq!(set.len(), 7);
}

#[test]
fn the_page_keeps_its_policy_word_for_word() {
    assert_eq!(
        perimeter::page_policy(),
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src data:; font-src 'self'; \
         connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; \
         object-src 'none'"
    );
}

#[test]
fn a_runs_token_is_32_random_bytes_in_url_safe_base64() {
    let (one, two) = (perimeter::new_token().unwrap(), perimeter::new_token().unwrap());
    assert_eq!(one.len(), 43);
    assert!(one.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'));
    assert_ne!(one, two);
}
