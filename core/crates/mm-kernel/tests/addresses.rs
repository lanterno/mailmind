//! Addresses split and validated as Python's `email.utils.getaddresses` (strict) and `kernel.addresses` did: each
//! case's answer is what Python 3.14 gave.

use mm_kernel::addresses::{clean, getaddresses, idna_decode, idna_domain, parse_addr_list};

fn pairs(found: &[(String, String)]) -> Vec<(&str, &str)> {
    found.iter().map(|(n, a)| (n.as_str(), a.as_str())).collect()
}

#[test]
fn getaddresses_splits_as_python_strict_mode_does() {
    let none: &[(&str, &str)] = &[("", "")];
    for (given, want) in [
        ("lin@example.test", &[("", "lin@example.test")][..]),
        ("Lin Mori <lin@example.test>", &[("Lin Mori", "lin@example.test")]),
        ("not an address", &[("", "not")]),
        ("\"Mori, Lin\" <lin@example.test>", &[("Mori, Lin", "lin@example.test")]),
        ("a@b.c, d@e.f", &[("", "a@b.c"), ("", "d@e.f")]),
        ("alice@example.test <bob@example.test>", none),
        ("(comment) x@y.z", &[("comment", "x@y.z")]),
        ("x@y.z (Lin)", &[("Lin", "x@y.z")]),
        ("group: a@b.c, d@e.f;", &[("", "a@b.c"), ("", "d@e.f")]),
        ("<@route:x@y.z>", &[("", "x@y.z")]),
        ("\"q\\\"x\" <x@y.z>", &[("q\"x", "x@y.z")]),
        ("x@[1.2.3.4]", none),
        ("a@b@c.d", none),
        ("  spaced @ example.test ", &[("", "spaced@example.test")]),
        ("x.@y.z", &[("", "x.@y.z")]),
        ("Ünïcode <x@y.z>", &[("Ünïcode", "x@y.z")]),
        ("(unbalanced <x@y.z>", none),
    ] {
        assert_eq!(pairs(&getaddresses(&[given])), want, "{given:?}");
    }
}

#[test]
fn parse_addr_list_validates_and_normalizes_the_domain() {
    let ok = |given: &str| parse_addr_list(&[given]).map(|p| p.into_iter().map(|(_, a)| a).collect::<Vec<_>>());
    assert_eq!(ok("x@bücher.example"), Some(vec!["x@xn--bcher-kva.example".to_owned()]));
    assert_eq!(ok("x@xn--bcher-kva.example"), Some(vec!["x@xn--bcher-kva.example".to_owned()]));
    assert_eq!(ok("x@localhost"), Some(vec!["x@localhost".to_owned()]));
    assert_eq!(ok("a@b.c, A@B.C"), Some(vec!["a@b.c".to_owned()]), "the first of each, whatever its case");
    assert_eq!(ok(""), Some(vec![]));
    for refused in ["not an address", "x@nodot", "x@[1.2.3.4]", "a@b@c.d", "alice@example.test <bob@example.test>"] {
        assert_eq!(ok(refused), None, "{refused:?}");
    }
}

#[test]
fn idna_and_clean() {
    assert_eq!(idna_domain(Some(" Example.TEST. ")).as_deref(), Some("Example.TEST"));
    assert_eq!(idna_domain(Some("a..b")), None);
    assert_eq!(idna_domain(Some(&format!("{}.test", "a".repeat(64)))), None);
    assert_eq!(idna_decode("xn--bcher-kva.example").as_deref(), Some("bücher.example"));
    assert_eq!(clean(Some("\tLin\u{202e} Mori\u{0} ")), "Lin Mori");
}
