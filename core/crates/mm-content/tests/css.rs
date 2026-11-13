//! The CSS sanitizer, as the Python app ran it: every expected value here is what `sanitize_inline_style` or
//! `sanitize_css_stylesheet` in mailmind/content/sanitize/html.py gave for the same input, before it called this
//! crate, with how many declarations a remote url() dropped, but where Python wrote what a browser would read
//! otherwise than it was checked: a comment mark, a line break, or a bracket a later piece closed. The core writes
//! none (`nothing_written_opens_or_closes_a_comment`, `a_browser_reads_what_is_written_as_it_was_written`). The
//! Python suite holds the two to the same answers over many more (tests/unit/content/test_sanitize_in_rust.py).

use mm_content::sanitize::css::{
    DECL_VALUE_MAX, INLINE_DECLS, INLINE_MAX, NEST_MAX, SELECTOR_MAX, SHEET_DECLS, SHEET_MAX, SHEET_RULES,
};
use mm_content::sanitize::{CssContext, sanitize_css_stylesheet, sanitize_inline_style};

fn inline(value: &str, load_images: bool) -> (String, usize) {
    let mut ctx = CssContext::new(load_images);
    let css = sanitize_inline_style(value, &mut ctx);
    (css, ctx.blocked_urls)
}

fn sheet(css: &str, load_images: bool) -> (String, usize) {
    let mut ctx = CssContext::new(load_images);
    let css = sanitize_css_stylesheet(css, &mut ctx);
    (css, ctx.blocked_urls)
}

/// Each `(input, load_images, output, blocked)` as Python answered it.
fn holds(read: fn(&str, bool) -> (String, usize), cases: &[(&str, bool, &str, usize)]) {
    for (input, load_images, output, blocked) in cases {
        assert_eq!(read(input, *load_images), (output.to_string(), *blocked), "{input:?}, load_images={load_images}");
    }
}

#[test]
fn an_escaped_url_is_decoded_before_it_is_gated() {
    holds(
        inline,
        &[
            ("background-image:\\75rl(http://tracker/beacon.png)", false, "", 1),
            ("background:url\\28http://tracker/x.png\\29", false, "", 1),
            ("background:u\\72 l(http://evil/p.png)", false, "", 1),
            ("background:url(https:\\/\\/evil/y.png)", false, "", 1),
            ("background:url(https:\\/\\/evil/y.png)", true, "background:url(\"https://evil/y.png\")", 0),
        ],
    );
}

#[test]
fn a_backslash_left_after_decoding_drops_the_declaration() {
    holds(
        inline,
        &[
            ("background:url(https://evil\\\\x.png)", true, "", 0),
            ("color:\\", false, "", 0),
            ("width:expr/* */ession(alert(1))", false, "", 0),
        ],
    );
}

#[test]
fn one_url_the_gate_wont_write_drops_the_whole_declaration_and_a_remote_one_counts_once() {
    holds(
        inline,
        &[
            ("background:url(data:image/png;base64,AAAA) , url(https://evil/beacon?id=USER)", false, "", 1),
            ("background:url(https://t/a) , url(https://t/b)", false, "", 1),
            ("list-style:url(https://cdn.example.com/dot.png) inside", false, "", 1),
        ],
    );
}

#[test]
fn what_loading_images_doesnt_change() {
    holds(
        inline,
        &[
            ("background:url(data:image/svg+xml;base64,PHN2Zz4=)", true, "", 0),
            ("background:url(javascript:alert(1))", true, "", 0),
            ("color:url(https://evil/x)", true, "", 0),
            ("background:var(--x, url(http://tracker))", true, "", 0),
            ("background:url(cid:part1)", true, "", 0),
            ("background:url(\"a;b\");color:red", true, "color:red", 0),
        ],
    );
}

#[test]
fn a_kept_url_is_written_in_one_form() {
    holds(
        inline,
        &[
            (
                "background-image:url(data:image/png;base64,iVBORw0KGgo=)",
                false,
                "background-image:url(\"data:image/png;base64,iVBORw0KGgo=\")",
                0,
            ),
            (
                "background-image:url(data:image/jpeg;base64,/9j/4AAQSkZJRg==)",
                false,
                "background-image:url(\"data:image/jpeg;base64,/9j/4AAQSkZJRg==\")",
                0,
            ),
            (
                "background:url(https://cdn.example.com/hero.png)",
                true,
                "background:url(\"https://cdn.example.com/hero.png\")",
                0,
            ),
            (
                "background:url( 'https://cdn.example.com/hero.png' )",
                true,
                "background:url(\"https://cdn.example.com/hero.png\")",
                0,
            ),
            (
                "background:URL(\"https://cdn.example.com/hero.png\") no-repeat",
                true,
                "background:url(\"https://cdn.example.com/hero.png\") no-repeat",
                0,
            ),
        ],
    );
}

#[test]
fn what_could_script_load_or_cover_the_pane_is_dropped() {
    holds(
        inline,
        &[
            ("width:expression(alert(1))", false, "", 0),
            ("behavior:url(#default#xss)", false, "", 0),
            ("-moz-binding:url(http://evil/x.xml)", false, "", 0),
            ("font-family:'</style><script>'", false, "", 0),
            ("position:fixed", false, "", 0),
            ("position:absolute", false, "", 0),
            ("position:f\\69xed", false, "", 0),
            ("position:", false, "", 0),
            ("height:100vh", false, "", 0),
            ("min-height:100vh", false, "", 0),
            ("height:100%", false, "", 0),
            ("width:10VMIN", false, "", 0),
            ("z-index:9999", false, "", 0),
            ("transform:translateY(-100px)", false, "", 0),
            ("color:@import", false, "", 0),
            ("color:a&{b}", false, "", 0),
            ("color:attr(x)", false, "", 0),
            ("color:paint(x)", false, "", 0),
            ("display:url(x)", false, "", 0),
            ("display:a:b", false, "", 0),
            ("display:inline-block()", false, "", 0),
        ],
    );
}

#[test]
fn position_keeps_only_values_that_place_nothing_over_the_page() {
    holds(
        inline,
        &[
            ("position:relative", false, "position:relative", 0),
            ("position:relative static", false, "position:relative static", 0),
            ("position:relative,inherit", false, "position:relative,inherit", 0),
        ],
    );
}

#[test]
fn a_viewport_unit_counts_only_standing_alone() {
    holds(
        inline,
        &[
            ("width:100%", false, "width:100%", 0),
            ("width:10vminx", false, "width:10vminx", 0),
            ("width:avh", false, "width:avh", 0),
        ],
    );
}

#[test]
fn an_escaped_quote_cant_end_its_string_and_carry_a_declaration_in() {
    holds(
        inline,
        &[
            ("font-family:\"a\\\";position:fixed;inset:0;\"", false, "", 0),
            ("font-family:\"a\\22;position:fixed;inset:0;\\22\"", false, "", 0),
            ("font-family:'a\\';position:fixed;inset:0;'", false, "", 0),
            ("color:red)", false, "", 0),
            ("color:(red", false, "", 0),
            ("color:'red", false, "", 0),
        ],
    );
    holds(sheet, &[("p{font-family:\"a\\\"}*{position:fixed;top:0}\"}", false, "", 0)]);
}

#[test]
fn a_newsletters_styling_survives() {
    holds(
        inline,
        &[
            (
                "color:#1a1a1a; font-family:Helvetica,Arial,sans-serif; font-size:16px; line-height:1.5; padding:12px \
                 16px; background-color:#fbf7ef; border-radius:8px; text-align:center",
                false,
                "color:#1a1a1a;font-family:Helvetica,Arial,sans-serif;font-size:16px;line-height:1.5;padding:12px \
                 16px;background-color:#fbf7ef;border-radius:8px;text-align:center",
                0,
            ),
            ("font-family:Georgia,'Times New Roman',serif", false, "font-family:Georgia,'Times New Roman',serif", 0),
            (
                "text-shadow:1px 1px 2px #000; box-shadow:0 2px 4px rgba(0,0,0,.2)",
                false,
                "text-shadow:1px 1px 2px #000;box-shadow:0 2px 4px rgba(0,0,0,.2)",
                0,
            ),
            ("display:block", false, "display:block", 0),
            ("text-align:center", false, "text-align:center", 0),
        ],
    );
}

#[test]
fn a_value_is_written_canonical() {
    holds(
        inline,
        &[
            ("COLOR : Red ;; ; margin :0", false, "color:Red;margin:0", 0),
            ("color:red;", false, "color:red", 0),
            ("color:red\u{a0}!important", false, "color:red !important", 0),
            ("color:\u{1}red\u{2028}blue\u{3000}green", false, "color:red blue green", 0),
            ("color:java\tscript:x", false, "color:java script:x", 0),
            ("color:\\41 \\42", false, "color:AB", 0),
            ("color:\\0", false, "color:\u{fffd}", 0),
            ("color:\\110000", false, "color:\u{fffd}", 0),
            ("font-family:\"a;b\";color:red", false, "font-family:\"a;b\";color:red", 0),
            ("color:(a;b);margin:0", false, "color:(a;b);margin:0", 0),
            ("color", false, "", 0),
            (":red", false, "", 0),
            ("", false, "", 0),
        ],
    );
}

#[test]
fn a_surrogate_decodes_to_the_replacement_character() {
    // Python wrote a lone surrogate, which no Rust string holds. Nothing the sanitizer decides tells the two apart.
    assert_eq!(inline("color:\\D800", false), ("color:\u{fffd}".to_string(), 0));
}

#[test]
fn a_property_is_read_as_python_lower_cased_it() {
    holds(
        inline,
        &[
            ("\\63olor:red", false, "color:red", 0),
            ("c\\6flor:blue", false, "color:blue", 0),
            // The Kelvin sign lower-cases to k.
            ("bac\u{212a}ground-color:red", false, "background-color:red", 0),
        ],
    );
}

#[test]
fn case_is_ignored_where_python_ignored_it() {
    holds(
        inline,
        &[
            // A dotless i and a long s match i and s, ignoring case.
            ("color:javascr\u{131}pt:x", false, "", 0),
            ("color:java\u{17f}cript:x", false, "", 0),
            ("width:10\u{17f}vh", false, "", 0),
            // A dotted capital I lower-cases to i and a combining dot, which no longer spells the word.
            ("color:JAVASCR\u{130}PT:x", false, "color:JAVASCR\u{130}PT:x", 0),
            ("width:10vm\u{130}n", false, "width:10vm\u{130}n", 0),
        ],
    );
    holds(
        sheet,
        &[
            ("expre\u{17f}\u{17f}ion{color:red}", false, "", 0),
            ("javascript\u{130}:x{color:red}", false, "javascript\u{130}:x{color:red}", 0),
        ],
    );
}

#[test]
fn a_sheet_keeps_its_rules_and_drops_at_rules_that_load() {
    holds(
        sheet,
        &[
            ("@import url(http://evil/x.css); p{color:red}", false, "p{color:red}", 0),
            ("@font-face{font-family:x;src:url(http://evil/f.woff)}", false, "", 0),
            ("p{color:red}</style><script>alert(1)</script>", false, "p{color:red}", 0),
            ("@media (max-width:600px){ .a{color:#333} }", false, "@media (max-width:600px){.a{color:#333}}", 0),
            ("@media url(evil){ .a{color:red} }", false, "", 0),
            ("@keyframes spin{from{opacity:0}to{opacity:1}}", false, "", 0),
            ("a<b{color:red}", false, "", 0),
            ("body{x:url(http://track)}", false, "", 0),
            ("body{background:url(https://tracker.example.test/bg.png)}", false, "", 1),
            (
                "body{background:url(https://tracker.example.test/bg.png)}p{background:url(https://t.example.test/x)}",
                false,
                "",
                2,
            ),
            (
                "body{background:url(https://tracker.example.test/bg.png)}",
                true,
                "body{background:url(\"https://tracker.example.test/bg.png\")}",
                0,
            ),
            ("html,body{height:100%}", false, "", 0),
            (
                ".btn{background-color:#0a66c2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;\
                 display:inline-block}@media (max-width:480px){.btn{display:block;width:100%}}",
                false,
                ".btn{background-color:#0a66c2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;\
                 display:inline-block}@media (max-width:480px){.btn{display:block;width:100%}}",
                0,
            ),
        ],
    );
}

#[test]
fn the_sheet_reads_each_rule_whole() {
    holds(
        sheet,
        &[
            ("p{font-family:\"a}b\";color:red}", false, "p{font-family:\"a}b\";color:red}", 0),
            ("@import \"x;{y\";p{color:red}", false, "p{color:red}", 0),
            ("@page{margin:0}p{color:red}", false, "p{color:red}", 0),
            ("@foo bar;p{color:red}", false, "p{color:red}", 0),
            ("@media screen;p{color:red}", false, "p{color:red}", 0),
            ("p{color:red}}q{color:blue}", false, "p{color:red}q{color:blue}", 0),
            ("@supports (display:grid){p{color:red}}", false, "@supports (display:grid){p{color:red}}", 0),
            ("@layer base{p{color:red}}", false, "@layer base{p{color:red}}", 0),
            ("@MEDIA Screen{p{color:red}}", false, "@media Screen{p{color:red}}", 0),
            ("@media{p{color:red}}", false, "@media {p{color:red}}", 0),
            (
                "@media screen and (min-width:1px){p{color:red}}",
                false,
                "@media screen and (min-width:1px){p{color:red}}",
                0,
            ),
            ("@media screen\u{e9}{p{color:red}}", false, "@media screen\u{e9}{p{color:red}}", 0),
            ("@media 'x'{p{color:red}}", false, "", 0),
            ("@{p{color:red}}", false, "", 0),
            ("@ media{p{color:red}}q{color:blue}", false, "q{color:blue}", 0),
            // Python kept the selector as it read it, comment and all. Nothing written marks a comment now.
            ("a /* x */ b{color:red}", false, "", 0),
            ("p:hover{color:red}q::before{color:blue}", false, "p:hover{color:red}q::before{color:blue}", 0),
            ("x{expression:1}", false, "", 0),
            ("p\\{color:red}", false, "", 0),
            ("{color:red}", false, "", 0),
            ("p{}", false, "", 0),
            ("p{color:red;margin:0;;padding:0}", false, "p{color:red;margin:0;padding:0}", 0),
        ],
    );
}

/// Python wrote a comment mark wherever a value, a selector or a condition came to hold one. A browser then read what
/// followed an opener as a comment, up to a closer a later value's string held, and the rest of that string as CSS:
/// in Chromium each attack here made elements `position:fixed`. Nothing written holds either mark now, and the rest
/// of what was written stays.
#[test]
fn nothing_written_opens_or_closes_a_comment() {
    holds(
        inline,
        &[
            // An escape decodes to the opener.
            ("font-family:\\/\\*;font-family:\"\\*\\/;position:fixed;top:0;left:0;width:100px;\"", false, "", 0),
            ("color:red;font-family:\\2f\\2a", false, "color:red", 0),
            // Taking a comment out joins a slash and a star.
            ("font-family:a//**/*;font-family:\"*/;position:fixed;top:0;x:\"", false, "", 0),
            ("color:red;font-family:a//**/*", false, "color:red", 0),
            // A url() rebuilt past the gate, from a value that ends `ur/*` and a string that begins `*/l(`.
            ("background:ur//**/*;font-family:\"*/l(https://t.example.test/x);x:\"", false, "", 0),
            // A closer alone, and either mark in a url() as it is written.
            ("color:red;width:1px*/2", false, "color:red", 0),
            ("color:red;font-family:\"*/\"", false, "color:red", 0),
            ("color:red;background:url(https://e.example.test/a\\/\\*b)", true, "color:red", 0),
            ("color:red;background:url(https://e.example.test/a*\\/b)", true, "color:red", 0),
            // A whole comment, taken out, is no mark.
            ("color:/* x */red", false, "color:red", 0),
        ],
    );
    holds(
        sheet,
        &[
            ("p{font-family:\\/\\*}q{font-family:\"\\*\\/}*{position:fixed;top:0}\"}", false, "", 0),
            ("p{font-family:a//**/*}q{font-family:\"*/}*{position:fixed;top:0}\"}", false, "", 0),
            ("p{color:red;font-family:a//**/*}", false, "p{color:red}", 0),
            // A condition holding a mark goes with its at-rule.
            ("@media /*{p{font-family:\"*/{*{position:fixed}}\"}}", false, "", 0),
            ("@media a*/b{p{color:red}}q{color:red}", false, "q{color:red}", 0),
            ("@media a/**/b{p{color:red}}q{color:red}", false, "q{color:red}", 0),
            // A selector holding one goes with its rule, the rest stays.
            ("a/**/b{color:red}q{color:blue}", false, "q{color:blue}", 0),
            ("a[title=\"/*\"]{color:red}q{color:blue}", false, "q{color:blue}", 0),
            ("*{color:red}p *{color:blue}", false, "*{color:red}p *{color:blue}", 0),
            // A comment in front of a rule is read past, and no part of its selector, which Python wrote it as.
            ("p{color:red}/* x */q{color:blue}", false, "p{color:red}q{color:blue}", 0),
            ("/* a */ /* b */\np{color:red}", false, "p{color:red}", 0),
        ],
    );
}

/// What is written reads whole, as a browser reads it, so a browser can't read what follows otherwise than it was
/// checked. Python wrote each of these attacks as it read it, and in Chromium each made elements `position:fixed`.
#[test]
fn a_browser_reads_what_is_written_as_it_was_written() {
    holds(
        inline,
        &[
            // A bracket or parenthesis left open, or closed by the other's match, takes in what follows.
            ("color:red;font-family:[", false, "color:red", 0),
            ("color:red;font-family:a]", false, "color:red", 0),
            ("color:red;font-family:([)]", false, "color:red", 0),
            ("color:red;font-family:a[b]", false, "color:red;font-family:a[b]", 0),
        ],
    );
    holds(
        sheet,
        &[
            // A value opens a bracket and a selector closes it, so the next selector was read as declarations.
            ("p{font-family:[}a]{color:red}x;position:fixed;top:0;y{color:blue}", false, "", 0),
            ("a[{color:red}b]{color:red}q{color:blue}", false, "q{color:blue}", 0),
            ("a:not({color:red}b){color:red}q{color:blue}", false, "q{color:blue}", 0),
            ("x;position:fixed;y{color:red}q{color:blue}", false, "q{color:blue}", 0),
            ("@media ({p{color:red}}q{color:blue}", false, "q{color:blue}", 0),
            // A browser ends a string at a line break, and the sanitizer read on: in a selector one is a space now.
            (
                "a[title=\"x\ny\"]{color:red}q{font-family:\"]{}*{position:fixed}\"}",
                false,
                "a[title=\"x y\"]{color:red}q{font-family:\"]{}*{position:fixed}\"}",
                0,
            ),
            ("a[title=\"x\u{c}y\"]{color:red}", false, "a[title=\"x y\"]{color:red}", 0),
            ("body,\ntable,\ntd{color:red}", false, "body, table, td{color:red}", 0),
            // What reads whole stays.
            (
                "p:not(.a){color:red}a[href^=\"https\"]{color:blue}@media (min-width:1px){p{color:red}}",
                false,
                "p:not(.a){color:red}a[href^=\"https\"]{color:blue}@media (min-width:1px){p{color:red}}",
                0,
            ),
        ],
    );
}

#[test]
fn what_never_ends_ends_the_sheet_where_it_starts() {
    for css in [
        "p{color:red}\"abc q{color:blue}",
        "p{color:red}/* x q{color:blue}",
        "p{color:red}@media screen",
        "p{color:red}@media screen{q{color:blue}",
        "p{color:red}q{content:\"abc}r{color:blue}",
        "p{color:red}q{color:blue;/* x",
    ] {
        assert_eq!(sheet(css, false), ("p{color:red}".to_string(), 0), "{css:?}");
    }
    assert_eq!(sheet("p{color:red", false), (String::new(), 0));
    assert_eq!(sheet(&format!("p{{color:red}} /* unterminated {}", "x".repeat(10_000)), false).0, "p{color:red}");
}

#[test]
fn media_nests_to_its_cap() {
    assert_eq!(NEST_MAX, 3);
    holds(
        sheet,
        &[
            ("@media a{@media b{@media c{p{color:red}}}}", false, "@media a{@media b{@media c{p{color:red}}}}", 0),
            ("@media a{@media b{@media c{@media d{p{color:red}}}}}", false, "", 0),
        ],
    );
}

#[test]
fn a_style_attribute_is_read_to_its_caps() {
    // The cap counts characters, before any work: past it, nothing is kept, not even what comes first.
    let at_cap = format!("color:red;{}", "\u{e9}".repeat(INLINE_MAX - "color:red;".len()));
    assert_eq!(inline(&at_cap, false).0, "color:red");
    assert_eq!(inline(&format!("{at_cap}a"), false).0, "");
    assert_eq!(inline(&format!("color:red;{}", "A".repeat(100_000)), false).0, "");

    let longest = format!("color:{}", "a".repeat(DECL_VALUE_MAX));
    assert_eq!(inline(&longest, false).0, longest);
    assert_eq!(inline(&format!("{longest}a"), false).0, "");
    // The cap is on the canonical value, comments gone.
    let commented = format!("color:{}/* comment */", "a".repeat(DECL_VALUE_MAX - 4));
    assert_eq!(inline(&commented, false).0, format!("color:{}", "a".repeat(DECL_VALUE_MAX - 4)));

    let declarations = |n: usize| format!("{}margin:0", "color:red;".repeat(n));
    assert_eq!(
        inline(&declarations(INLINE_DECLS - 1), false).0,
        format!("{}margin:0", "color:red;".repeat(INLINE_DECLS - 1))
    );
    assert_eq!(inline(&declarations(INLINE_DECLS), false).0, ["color:red"; INLINE_DECLS].join(";"));
}

#[test]
fn a_sheet_is_read_to_its_caps() {
    let rules = "p{color:red}".repeat(SHEET_RULES + 1);
    assert_eq!(sheet(&rules, false).0, "p{color:red}".repeat(SHEET_RULES));
    let padded = format!("p{{color:red}}{}q{{color:blue}}", " ".repeat(SHEET_MAX));
    assert_eq!(sheet(&padded, false).0, "p{color:red}");

    let block = format!("p{{{}margin:0}}", "color:red;".repeat(SHEET_DECLS));
    assert_eq!(sheet(&block, false).0, format!("p{{{}}}", ["color:red"; SHEET_DECLS].join(";")));

    let selector = |n: usize| format!("p{{color:red}}{}{{color:blue}}", "q".repeat(n));
    assert_eq!(
        sheet(&selector(SELECTOR_MAX), false).0,
        format!("p{{color:red}}{}{{color:blue}}", "q".repeat(SELECTOR_MAX))
    );
    assert_eq!(sheet(&selector(SELECTOR_MAX + 1), false).0, "p{color:red}");
}

#[test]
fn a_sheet_never_holds_an_angle_bracket() {
    for css in [
        "p{color:red}</style><script>alert(1)</script>",
        "a>b{color:red}",
        "p{font-family:'<x>'}",
        "<!--p{color:red}-->",
    ] {
        let (out, _) = sheet(css, true);
        assert!(!out.contains(['<', '>']), "{css:?} gave {out:?}");
    }
}
