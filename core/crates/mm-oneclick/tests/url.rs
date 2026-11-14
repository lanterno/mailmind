//! A URL as the Python app's `urllib.parse` reads one for the one-click POST: each answer here is what Python 3.14's
//! `urlsplit`, `.hostname`, `.port` and `urljoin` said of the same URL.

use mm_oneclick::url::{Parts, Unreadable, join, split};

/// (scheme, netloc, path, query, fragment), each "" where Python's `urlsplit` has none.
type Said<'a> = (&'a str, &'a str, &'a str, &'a str, &'a str);

fn said(parts: &Parts) -> (String, String, String, String, String) {
    (
        parts.scheme.clone().unwrap_or_default(),
        parts.netloc.clone().unwrap_or_default(),
        parts.path.clone(),
        parts.query.clone().unwrap_or_default(),
        parts.fragment.clone().unwrap_or_default(),
    )
}

fn owned((scheme, netloc, path, query, fragment): Said<'_>) -> (String, String, String, String, String) {
    (scheme.into(), netloc.into(), path.into(), query.into(), fragment.into())
}

/// `Err(())` where Python's `.port` raises.
type Port = Result<Option<u16>, ()>;

#[test]
fn a_url_splits_as_pythons_urlsplit_splits_it() {
    let cases: [(&str, Said<'_>, Option<&str>, Port); 30] = [
        (
            "https://unsub.example.test:8443/u/ada-218?list=weekly",
            ("https", "unsub.example.test:8443", "/u/ada-218", "list=weekly", ""),
            Some("unsub.example.test"),
            Ok(Some(8443)),
        ),
        (
            "  \x01https://Loom.Example.TEST/u",
            ("https", "Loom.Example.TEST", "/u", "", ""),
            Some("loom.example.test"),
            Ok(None),
        ),
        (
            "https://lo\r\nom.example.test/u\tx",
            ("https", "loom.example.test", "/ux", "", ""),
            Some("loom.example.test"),
            Ok(None),
        ),
        ("HTTPS://h/u", ("https", "h", "/u", "", ""), Some("h"), Ok(None)),
        (
            "https://user:secret@h.example.test/u",
            ("https", "user:secret@h.example.test", "/u", "", ""),
            Some("h.example.test"),
            Ok(None),
        ),
        (
            "https://8.8.8.8@169.254.169.254/u",
            ("https", "8.8.8.8@169.254.169.254", "/u", "", ""),
            Some("169.254.169.254"),
            Ok(None),
        ),
        ("https://[::1]/u", ("https", "[::1]", "/u", "", ""), Some("::1"), Ok(None)),
        (
            "https://[::ffff:127.0.0.1]:8443/u",
            ("https", "[::ffff:127.0.0.1]:8443", "/u", "", ""),
            Some("::ffff:127.0.0.1"),
            Ok(Some(8443)),
        ),
        ("https://[fe80::1%25en0]/u", ("https", "[fe80::1%25en0]", "/u", "", ""), Some("fe80::1%25en0"), Ok(None)),
        ("https://[v1.x]/u", ("https", "[v1.x]", "/u", "", ""), Some("v1.x"), Ok(None)),
        ("https://h:abc/u", ("https", "h:abc", "/u", "", ""), Some("h"), Err(())),
        ("https://h:65536/u", ("https", "h:65536", "/u", "", ""), Some("h"), Err(())),
        ("https://h:0/u", ("https", "h:0", "/u", "", ""), Some("h"), Ok(Some(0))),
        ("https://h:/u", ("https", "h:", "/u", "", ""), Some("h"), Ok(None)),
        ("https://h:00443/u", ("https", "h:00443", "/u", "", ""), Some("h"), Ok(Some(443))),
        ("https:///nohost", ("https", "", "/nohost", "", ""), None, Ok(None)),
        ("mailto:u@example.test", ("mailto", "", "u@example.test", "", ""), None, Ok(None)),
        ("", ("", "", "", "", ""), None, Ok(None)),
        ("ftp://h/u", ("ftp", "h", "/u", "", ""), Some("h"), Ok(None)),
        ("https:h/u", ("https", "", "h/u", "", ""), None, Ok(None)),
        ("https://h", ("https", "h", "", "", ""), Some("h"), Ok(None)),
        ("https://h?q", ("https", "h", "", "q", ""), Some("h"), Ok(None)),
        ("https://h#f", ("https", "h", "", "", "f"), Some("h"), Ok(None)),
        ("https://h/u;list=1?x#y", ("https", "h", "/u;list=1", "x", "y"), Some("h"), Ok(None)),
        ("1https://h/u", ("", "", "1https://h/u", "", ""), None, Ok(None)),
        ("https://h:\u{ff11}/u", ("https", "h:\u{ff11}", "/u", "", ""), Some("h"), Err(())),
        ("//h/u", ("", "h", "/u", "", ""), Some("h"), Ok(None)),
        ("https://[::1]:x/u", ("https", "[::1]:x", "/u", "", ""), Some("::1"), Err(())),
        ("https://h:1:2/u", ("https", "h:1:2", "/u", "", ""), Some("h"), Err(())),
        ("https://h:443", ("https", "h:443", "", "", ""), Some("h"), Ok(Some(443))),
    ];
    for (url, parts, host, port) in cases {
        let split = split(url).unwrap_or_else(|_| panic!("{url:?} splits"));
        assert_eq!(said(&split), owned(parts), "{url:?}");
        assert_eq!(split.hostname().as_deref(), host, "{url:?}");
        assert_eq!(split.port().map_err(|_| ()), port, "{url:?}");
    }
}

#[test]
fn brackets_that_dont_hold_an_ipv6_address_are_unreadable() {
    for url in ["https://[::1/u", "https://::1]/u", "https://[1.2.3.4]/u", "https://a[::1]/u", "https://[::1]x/u"] {
        assert_eq!(split(url), Err(Unreadable), "{url:?}");
    }
}

#[test]
fn a_redirect_is_read_against_the_url_that_answered_it_as_urljoin_reads_it() {
    let base = "https://unsub.example.test:8443/a/b/c?q=1#f";
    for (reference, joined) in [
        ("/hop", "https://unsub.example.test:8443/hop"),
        ("done", "https://unsub.example.test:8443/a/b/done"),
        ("../x", "https://unsub.example.test:8443/a/x"),
        ("./", "https://unsub.example.test:8443/a/b/"),
        ("..", "https://unsub.example.test:8443/a/"),
        ("../../../..", "https://unsub.example.test:8443/"),
        ("//other.example.test/p", "https://other.example.test/p"),
        ("http://h/u", "http://h/u"),
        ("https://h2/u", "https://h2/u"),
        ("?z", "https://unsub.example.test:8443/a/b/c?z"),
        ("#g", "https://unsub.example.test:8443/a/b/c?q=1#g"),
        ("", "https://unsub.example.test:8443/a/b/c?q=1#f"),
        ("https:rel", "https://unsub.example.test:8443/a/b/rel"),
        ("./a/../b/.", "https://unsub.example.test:8443/a/b/b/"),
        ("g;x", "https://unsub.example.test:8443/a/b/g;x"),
        ("g?y#s", "https://unsub.example.test:8443/a/b/g?y#s"),
        ("/./a/b/../c", "https://unsub.example.test:8443/a/c"),
        ("HTTPS://Up/u", "https://Up/u"),
    ] {
        assert_eq!(join(base, reference).as_deref(), Ok(joined), "{reference:?}");
    }
    assert_eq!(join("https://h/u", "https://[::1/x"), Err(Unreadable));
}
