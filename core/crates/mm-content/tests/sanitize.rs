//! The HTML sanitizer. Where Python's html.parser and a browser read the input alike, every expected value here is
//! what `sanitize` in mailmind/content/sanitize/html.py gave for the same input, before it called this crate. Where
//! they don't, the browser's reading wins, and the test says what a browser reads. Every output, hostile input's
//! above all, is read back as a browser would read it and held to the allowlist. The Python suite holds the two
//! sanitizers to the same answers over many more (tests/unit/content/test_sanitize_in_rust.py).

use mm_content::sanitize::{Blocked, FOLD_CLOSE, FOLD_OPEN, Read, read, sanitize, without_fold};

/// The tags the sanitizer may write: its allowlist, and the `<style>` holding a sanitized sheet.
#[rustfmt::skip]
const WRITTEN: &[&str] = &[
    "p", "br", "hr", "div", "span", "a", "b", "i", "u", "s", "strike", "em", "strong", "small", "sub", "sup", "mark",
    "abbr", "cite", "q", "blockquote", "pre", "code", "kbd", "samp", "var", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col", "h1", "h2", "h3", "h4", "h5",
    "h6", "img", "figure", "figcaption", "center", "font", "address", "article", "section", "header", "footer",
    "main", "nav", "aside", "wbr", "time", "details", "summary", "style",
];

/// Whether a tag may carry an attribute the sanitizer writes.
fn may_carry(tag: &str, attr: &str) -> bool {
    const GLOBAL: &[&str] = &[
        "dir", "title", "lang", "align", "colspan", "rowspan", "width", "height", "valign", "span", "start",
        "datetime", "style",
    ];
    GLOBAL.contains(&attr)
        || matches!(
            (tag, attr),
            ("a", "href" | "rel" | "target") | ("img", "src" | "alt") | ("font", "color") | ("details", "class")
        )
}

/// Whether a browser would read `url` as one of the kept schemes. It drops the C0 controls and spaces around a URL
/// and every tab and line break in it. A URL that names no scheme is relative to the frame's own document, and goes
/// nowhere else.
fn scheme_is_kept(url: &str, data_allowed: bool) -> bool {
    let url: String =
        url.trim_matches(|c: char| c <= ' ').chars().filter(|c| !matches!(c, '\t' | '\n' | '\r')).collect();
    let Some((scheme, rest)) = url.split_once(':') else { return true };
    let is_scheme = scheme.starts_with(|c: char| c.is_ascii_alphabetic())
        && scheme.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'));
    if !is_scheme {
        return true;
    }
    match scheme.to_ascii_lowercase().as_str() {
        "http" | "https" | "mailto" | "cid" => true,
        "data" => {
            data_allowed
                && ["png;", "jpg;", "jpeg;", "gif;", "webp;", "bmp;"]
                    .iter()
                    .any(|t| rest.to_ascii_lowercase().strip_prefix("image/").is_some_and(|after| after.starts_with(t)))
        }
        _ => false,
    }
}

/// Reads `out` as a browser would and holds every token to what the sanitizer may write.
fn assert_safe(out: &str, load_images: bool) {
    let mut in_style = false;
    for token in read(out) {
        match token {
            Read::Start { name, attrs, .. } => {
                assert!(WRITTEN.contains(&name.as_str()), "<{name}> written: {out:?}");
                in_style = name == "style";
                for (attr, value) in &attrs {
                    assert!(!attr.starts_with("on"), "{attr} on <{name}>: {out:?}");
                    assert!(may_carry(&name, attr), "{attr} on <{name}>: {out:?}");
                    if attr == "href" || attr == "src" {
                        assert!(scheme_is_kept(value, name == "img" && load_images), "{attr}={value:?}: {out:?}");
                    }
                    if name == "img" && attr == "src" {
                        assert!(load_images, "an image's source while images are blocked: {out:?}");
                    }
                }
            }
            Read::End { name } => {
                assert!(WRITTEN.contains(&name.as_str()), "</{name}> written: {out:?}");
                in_style = false;
            }
            Read::Text(text) => assert!(!in_style || !text.contains('<'), "a sheet holding <: {out:?}"),
            Read::Comment(text) => panic!("a comment written, {text:?}: {out:?}"),
        }
    }
}

fn blocked(images: usize, links: usize, css_urls: usize) -> Blocked {
    Blocked { images, links, css_urls }
}

/// Sanitizes each case, holds it to its expected output and counts, and checks the output is safe.
fn holds(cases: &[(&str, bool, bool, &str, [usize; 3])]) {
    for (html, load_images, fold, out, [images, links, css_urls]) in cases {
        let got = sanitize(html, *load_images, *fold);
        assert_eq!((got.html.as_str(), got.blocked), (*out, blocked(*images, *links, *css_urls)), "{html:?}");
        assert_safe(&got.html, *load_images);
    }
}

/// tests/security/test_sanitize.py's attacks, a representative of each class a red team tried.
const ATTACKS: &[&str] = &[
    "<script>alert(1)</script>x",
    "<img src=x onerror=alert(1)>",
    "<img src=x ONERROR=alert(1)>",
    "<img\tsrc=x\nonerror=alert(1)>",
    "<svg/onload=alert(1)>",
    "<svg><script>alert(1)</script></svg>",
    "<a href='javascript:alert(1)'>c</a>",
    "<a href='java\tscript:alert(1)'>c</a>",
    "<a href='&#106;avascript:alert(1)'>c</a>",
    "<a href='vbscript:x'>c</a>",
    "<a href='data:text/html,<script>alert(1)</script>'>c</a>",
    "<a href='//evil/track'>c</a>",
    "<iframe src='x'></iframe>",
    "<object data='x'></object>",
    "<form action='/x'><input></form>",
    "<base href='http://evil/'>b",
    "<meta http-equiv=refresh content='0;url=evil'>b",
    "<style>body{x:url(http://track)}</style>v",
    "<p style='background:url(javascript:alert(1))'>t</p>",
    "<td background='http://evil/o.gif'>x</td>",
    "<font color='expression(alert(1))'>x</font>",
    "<img src='data:image/svg+xml;base64,PHN2Zz4='>",
    "<div title='\"></div><img src=x onerror=alert(1)>'>hi</div>",
    "<a href='http://ok' title='x\" onmouseover=\"alert(1)'>y</a>",
    // The same with their character references decoded first.
    "<a href='javascript:alert(1)'>c</a>",
    // The frame tests' messages.
    "<p id=\"said\">Hello</p>\n<script>parent.document.body.dataset.pwned = \"script\"</script>\n\
     <img src=\"https://tracker.example.test/pixel.png\" alt=\"tracker\">\n\
     <img src=\"data:,\" onerror=\"parent.document.body.dataset.pwned = 'onerror'\">\n\
     <svg onload=\"parent.document.body.dataset.pwned = 'svg'\"></svg>\n\
     <iframe srcdoc=\"&lt;script&gt;top.document.body.dataset.pwned = 'nested'&lt;/script&gt;\"></iframe>\n\
     <iframe src=\"https://evil.example.test/frame\"></iframe>\n\
     <object data=\"https://evil.example.test/object\"></object>\n\
     <link rel=\"stylesheet\" href=\"https://evil.example.test/style.css\">\n\
     <style>@import url(https://evil.example.test/import.css); body{background:url(https://tracker.example.test/bg.png)}</style>\n\
     <meta http-equiv=\"refresh\" content=\"0;url=https://evil.example.test/refresh\">\n\
     <base href=\"https://evil.example.test/\">\n\
     <form action=\"https://evil.example.test/form\"><button id=\"go\">Go</button></form>\n\
     <a id=\"js\" href=\"javascript:parent.document.body.dataset.pwned = 'href'\">click</a>",
    "<!doctype html><html><head>\n<script>fire()</script>\n<script src=\"/assets/theme.js\"></script>\n\
     <meta http-equiv=\"refresh\" content=\"0;url=javascript:fire()\">\n\
     <link rel=\"stylesheet\" href=\"https://away.example.test/sheet.css\">\n\
     <style>@import url(https://away.example.test/import.css); body { background: url(https://away.example.test/back.png) }</style>\n\
     </head><body onload=\"fire()\">\n<p>A message with everything in it</p>\n<script>fire()</script>\n\
     <img src=\"data:,\" onerror=\"fire()\">\n<img src=\"https://away.example.test/pixel.png\"><img src=\"/api/folders\">\n\
     <svg onload=\"fire()\"><script>fire()</script></svg>\n<details open ontoggle=\"fire()\">more</details>\n\
     <input autofocus onfocus=\"fire()\">\n<video src=\"data:,\" onerror=\"fire()\"></video>\n\
     <math><mtext><table><mglyph><style><img src=\"data:,\" onerror=\"fire()\"></style></mglyph></table></mtext></math>\n\
     <iframe srcdoc=\"<script>fire()</script>\"></iframe>\n<object data=\"javascript:fire()\"></object>\n\
     <embed src=\"javascript:fire()\">\n<a href=\"javascript:fire()\">a link</a>\n\
     <form action=\"javascript:fire()\"><button>go</button></form>\n</body></html>",
    "<p onclick=\"parent.pwned=1\">Hello</p><script>parent.pwned=1</script>\
     <img src=\"https://tracker.example.test/pixel.png\" alt=\"tracker\">",
    // Breaking out of the raw text a browser reads, and back into it.
    "<style>p{color:red}</style><script>alert(1)</script>",
    "<p style='font-family:\"</style><script>alert(1)</script>\"'>x</p>",
    "<title></title><img src=x onerror=alert(1)>",
    "<textarea><img src=x onerror=alert(1)></textarea>",
    "<noscript><img src=x onerror=alert(1)></noscript>",
    "<xmp></xmp><img src=x onerror=alert(1)>",
    "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "<svg><![CDATA[<img src=x onerror=alert(1)>]]></svg><img src=x onerror=alert(1)>",
    "<math><style><img src=x onerror=alert(1)></style></math>",
    "<script><!--<script></script><img src=x onerror=alert(1)></script>",
    "<plaintext><img src=x onerror=alert(1)>",
    "<a href=\"https://example.test/\" href=\"javascript:alert(1)\">x</a>",
    "<a href=\"javascript:alert(1)\" href=\"https://example.test/\">x</a>",
    "<img src=\"https:\u{0}//example.test/\" onerror\u{0}=alert(1)>",
    "<a href=\"java&#0;script:alert(1)\">x</a>",
    "<a href=\"&#x6A;ava&#x09;script&colon;alert(1)\">x</a>",
    "<a href=\"\u{1}javascript:alert(1)\">x</a>",
    "<a href=\" javascript:alert(1)\">x</a>",
    "<img src=\"data:image/png;base64,AA\" onload=alert(1)>",
    "<img src=\"DATA:image/svg+xml,<svg onload=alert(1)>\">",
    "<p style=\"background:url(&quot;javascript:alert(1)&quot;)\">x</p>",
    "<p style=\"background:url(data:image/png;base64,AA)\" onmouseover=alert(1)>x</p>",
    "<img src=x onerror=alert(1)//>",
    "<img/src=x/onerror=alert(1)>",
    "<a/href=javascript:alert(1)>x</a>",
    "<p =x onclick=alert(1)>y</p>",
    "<p \"onclick\"=alert(1)>y</p>",
    "<<script>script>alert(1)<</script>/script>",
    "<scr<script>ipt>alert(1)</script>",
    "<div style=\"width:expression(alert(1))\">x</div>",
    "<blockquote cite=\"javascript:alert(1)\" onclick=alert(1)>q</blockquote>",
    "<details open ontoggle=alert(1)><summary onclick=alert(1)>s</summary></details>",
    "<a href=\"https://example.test/\" target=\"_self\" rel=\"opener\">x</a>",
    // A head's sheets, which are kept.
    "<head><style>@import url(https://e.example.test/i.css); p{background:url(javascript:alert(1))}</style></head>x",
    "<head><style></style><script>alert(1)</script><style>p{x:expression(alert(1))}</style></head>",
    "<head><noscript><style></noscript><img src=x onerror=alert(1)></style></noscript></head>",
    "<head><style>p{font-family:\"</style><img src=x onerror=alert(1)>\"}</style>",
];

#[test]
fn nothing_dangerous_survives_whatever_the_message_holds() {
    for attack in ATTACKS {
        for load_images in [false, true] {
            for fold in [false, true] {
                let out = sanitize(attack, load_images, fold).html;
                assert_safe(&out, load_images);
                assert!(!out.contains("alert(1)") || !out.contains("<script"), "{attack:?} gave {out:?}");
            }
        }
    }
}

#[test]
fn nothing_of_a_dropped_element_is_shown() {
    holds(&[
        ("<script>alert(1)</script>x", false, true, "x", [0, 0, 0]),
        ("<svg><script>alert(1)</script></svg>", false, true, "", [0, 0, 0]),
        ("<iframe src='x'></iframe>", false, true, "", [0, 0, 0]),
        ("<object data='x'></object>", false, true, "", [0, 0, 0]),
        ("<form action='/x'><input></form>", false, true, "", [0, 0, 0]),
        ("<base href='http://evil/'>b", false, true, "b", [0, 0, 0]),
        ("<meta http-equiv=refresh content='0;url=evil'>b", false, true, "b", [0, 0, 0]),
        ("<template><p>t</p></template>after", false, true, "after", [0, 0, 0]),
        ("<noscript><p>n</p></noscript>after", false, true, "after", [0, 0, 0]),
        ("<object data='x'><p>o</p></object>after", false, true, "after", [0, 0, 0]),
        ("<select><option>o</option></select>after", false, true, "after", [0, 0, 0]),
        ("<button>b</button>after", false, true, "after", [0, 0, 0]),
        ("<form><input value=x>f</form>after", false, true, "after", [0, 0, 0]),
        ("<svg><svg>a</svg>b</svg>after", false, true, "after", [0, 0, 0]),
        ("<title>a<b>c</b></title>after", false, true, "after", [0, 0, 0]),
        ("<textarea>a &amp; <b>c</b></textarea>after", false, true, "after", [0, 0, 0]),
        ("<script>if (a < b) { x = '</scr' + 'ipt>' }</script>after", false, true, "after", [0, 0, 0]),
        ("<!doctype html><html><head><title>T</title></head><body>B</body></html>", false, true, "B", [0, 0, 0]),
        (
            "<head><title>T</title><meta name=x><script>s</script></head><body><p>x</p></body>",
            false,
            true,
            "<p>x</p>",
            [0, 0, 0],
        ),
        // <embed> holds nothing and has no end tag: the tag goes and what follows it stays.
        ("<embed src=x>after", false, true, "after", [0, 0, 0]),
        (
            "<p>a<embed src='https://e.example.test/x.swf' type=application/x-shockwave-flash>b</p>c",
            false,
            true,
            "<p>ab</p>c",
            [0, 0, 0],
        ),
        ("<embed src=x></embed>after<embed>", false, true, "after", [0, 0, 0]),
    ]);
}

/// A message may leave its `</head>` out. Python dropped all after the `<head>` then; a browser ends the head at the
/// first tag that can't be in one, or at text, and shows the rest. Chromium shows the text of each written here.
#[test]
fn a_head_ends_where_a_browser_ends_it() {
    holds(&[
        ("<html><head><meta charset=utf-8><title>T</title><body><p>Hello</p>", false, true, "<p>Hello</p>", [0, 0, 0]),
        ("<head><title>T</title><p>Hello", false, true, "<p>Hello", [0, 0, 0]),
        (
            "<head><meta name=x><link rel=icon href=x><base href=x>\n<div>D</div>",
            false,
            true,
            "<div>D</div>",
            [0, 0, 0],
        ),
        // Text ends it, past the spaces that lead it; a character reference is text.
        ("<head>  \n Hello <b>x</b>", false, true, "Hello <b>x</b>", [0, 0, 0]),
        ("<head><script>a</script>&nbsp;Hi", false, true, "\u{a0}Hi", [0, 0, 0]),
        // Raw text and a template don't.
        ("<head><title>a</head>b</title>c", false, true, "c", [0, 0, 0]),
        ("<head><template><p>t</p></template><p>y", false, true, "<p>y", [0, 0, 0]),
        ("<head><template></head><p>t</p></template>z", false, true, "z", [0, 0, 0]),
        // The frame runs no script, so a <noscript> holds markup, and a tag a head can't hold ends both.
        ("<head><noscript><p>n</p></noscript>after", false, true, "<p>n</p>after", [0, 0, 0]),
        // An end tag but these four is ignored, and so is a second <head>.
        ("<head></p>Hello", false, true, "Hello", [0, 0, 0]),
        ("<head><style>p{}</style></body>Hello", false, true, "Hello", [0, 0, 0]),
        ("<head><head></head>Hello", false, true, "Hello", [0, 0, 0]),
        ("<p>a<head><title>t</title>b", false, true, "<p>ab", [0, 0, 0]),
        ("<head><title>T</title></head><body>B</body>", false, true, "B", [0, 0, 0]),
    ]);
}

/// A head's style sheet applies as one in the body does, and Chromium applies one in a head's `<noscript>` too, with
/// scripting off. Python dropped them with the head, so a newsletter styled from its head showed plainer than sent.
#[test]
fn a_heads_style_sheet_is_kept() {
    holds(&[
        (
            "<html><head><style>p{color:red}</style></head><body><p>x</p></body></html>",
            false,
            true,
            "<style>p{color:red}</style><p>x</p>",
            [0, 0, 0],
        ),
        ("<head><style>p{color:red}</style><p>x", false, true, "<style>p{color:red}</style><p>x", [0, 0, 0]),
        (
            "<head><title>T</title><style>p{color:red}</style><meta name=x><style>b{color:blue}</style></head>B",
            false,
            true,
            "<style>p{color:red}</style><style>b{color:blue}</style>B",
            [0, 0, 0],
        ),
        (
            "<head><noscript><style>p{color:red}</style></noscript></head><p>x</p>",
            false,
            true,
            "<style>p{color:red}</style><p>x</p>",
            [0, 0, 0],
        ),
        // One never closed is written at the end, as one in the body is.
        ("<head><style>p{color:red}", false, true, "<style>p{color:red}</style>", [0, 0, 0]),
        // One in a <template> applies to nothing.
        ("<head><template><style>p{color:red}</style></template></head><p>x</p>", false, true, "<p>x</p>", [0, 0, 0]),
        // It is sanitized as any other sheet is, and what it blocks counts.
        (
            "<head><style>@import url(https://e.example.test/i.css); body{background:url(https://t.example.test/b.png)} \
             p{color:red;position:fixed}</style></head>v",
            false,
            true,
            "<style>p{color:red}</style>v",
            [0, 0, 1],
        ),
        (
            "<head><style>body{background:url(https://t.example.test/b.png)}</style></head>v",
            true,
            true,
            "<style>body{background:url(\"https://t.example.test/b.png\")}</style>v",
            [0, 0, 0],
        ),
        // Its raw text ends at its own end tag, and nothing in it is markup.
        (
            "<head><style>p{font-family:'</style><script>alert(1)</script>'}</style></head>x",
            false,
            true,
            "'}x",
            [0, 0, 0],
        ),
        ("<head><style><img src=x onerror=alert(1)></style></head>x", false, true, "x", [0, 0, 0]),
        // A sheet is no text to fold a quote under.
        (
            "<head><style>p{color:red}</style></head><blockquote>q</blockquote>",
            false,
            true,
            "<style>p{color:red}</style><blockquote>q</blockquote>",
            [0, 0, 0],
        ),
    ]);
}

#[test]
fn safe_content_is_kept() {
    holds(&[
        ("<p>Hi <b>bold</b> <i>it</i></p>", false, true, "<p>Hi <b>bold</b> <i>it</i></p>", [0, 0, 0]),
        (
            "<a href='https://example.com'>link</a>",
            false,
            true,
            "<a href=\"https://example.com\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">link</a>",
            [0, 0, 0],
        ),
        ("<ul><li>a</li></ul>", false, true, "<ul><li>a</li></ul>", [0, 0, 0]),
        (
            "<table><tr><td colspan='2'>c</td></tr></table>",
            false,
            true,
            "<table><tr><td colspan=\"2\">c</td></tr></table>",
            [0, 0, 0],
        ),
        ("<div><div>nested</div></div>", false, true, "<div><div>nested</div></div>", [0, 0, 0]),
        ("<br><hr><wbr><col>", false, true, "<br/><hr/><wbr/><col/>", [0, 0, 0]),
        ("<br></br><img></img>", false, true, "<br/><img/>", [0, 0, 0]),
        (
            "<details open><summary>s</summary>d</details>",
            false,
            true,
            "<details><summary>s</summary>d</details>",
            [0, 0, 0],
        ),
        ("<time datetime='2026-11-02'>t</time>", false, true, "<time datetime=\"2026-11-02\">t</time>", [0, 0, 0]),
        ("<ol start='3'><li>x</li></ol>", false, true, "<ol start=\"3\"><li>x</li></ol>", [0, 0, 0]),
        ("<pre>  a\n  b</pre>", false, true, "<pre>  a\n  b</pre>", [0, 0, 0]),
        ("<P ALIGN=CENTER>x</P>", false, true, "<p align=\"CENTER\">x</p>", [0, 0, 0]),
        ("<blink>x</blink><marquee>y</marquee>", false, true, "xy", [0, 0, 0]),
        ("<xmp><b>x</b></xmp>", false, true, "&lt;b&gt;x&lt;/b&gt;", [0, 0, 0]),
        ("<plaintext><b>x</b>", false, true, "&lt;b&gt;x&lt;/b&gt;", [0, 0, 0]),
    ]);
}

#[test]
fn every_value_and_text_is_escaped() {
    holds(&[
        (
            "<p>x &amp; y &lt; z &gt; w \"q\" 'a'</p>",
            false,
            true,
            "<p>x &amp; y &lt; z &gt; w \"q\" 'a'</p>",
            [0, 0, 0],
        ),
        (
            "<p title='a \"b\" &amp; c&lt;d&gt;'>x</p>",
            false,
            true,
            "<p title=\"a &quot;b&quot; &amp; c&lt;d&gt;\">x</p>",
            [0, 0, 0],
        ),
        ("<p title=\"it's\">x</p>", false, true, "<p title=\"it&#x27;s\">x</p>", [0, 0, 0]),
        ("x < y > z", false, true, "x &lt; y &gt; z", [0, 0, 0]),
        ("<<<>>>", false, true, "&lt;&lt;&lt;&gt;&gt;&gt;", [0, 0, 0]),
        ("a &notit; b &amp c &#169; &#x41;", false, true, "a \u{ac}it; b &amp; c \u{a9} A", [0, 0, 0]),
        (
            "<div title='\"></div><img src=x onerror=alert(1)>'>hi</div>",
            false,
            true,
            "<div title=\"&quot;&gt;&lt;/div&gt;&lt;img src=x onerror=alert(1)&gt;\">hi</div>",
            [0, 0, 0],
        ),
    ]);
}

#[test]
fn attributes_keep_to_the_allowlist() {
    holds(&[
        (
            "<a title='t' href='https://example.test/' onclick='x'>c</a>",
            false,
            true,
            "<a title=\"t\" href=\"https://example.test/\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">c</a>",
            [0, 0, 0],
        ),
        (
            "<p style='color:red;position:fixed' align='center' dir='rtl'>x</p>",
            false,
            true,
            "<p style=\"color:red\" align=\"center\" dir=\"rtl\">x</p>",
            [0, 0, 0],
        ),
        ("<font color='red' face='x' size='3'>f</font>", false, true, "<font color=\"red\">f</font>", [0, 0, 0]),
        ("<font color='expression(alert(1))'>x</font>", false, true, "<font>x</font>", [0, 0, 0]),
        ("<p title='/* x */'>y</p>", false, true, "<p>y</p>", [0, 0, 0]),
        (
            "<td background='http://evil/o.gif' colspan=2 rowspan=3>x</td>",
            false,
            true,
            "<td colspan=\"2\" rowspan=\"3\">x</td>",
            [0, 0, 0],
        ),
        ("<p class='c' id='i' data-x='y'>z</p>", false, true, "<p>z</p>", [0, 0, 0]),
    ]);
    let long_title = format!("<p title='{}'>y</p>", "a".repeat(2049));
    assert_eq!(sanitize(&long_title, false, true).html, "<p>y</p>");
    let kept_title = format!("<p title='{}'>y</p>", "a".repeat(2048));
    assert_eq!(sanitize(&kept_title, false, true).html, format!("<p title=\"{}\">y</p>", "a".repeat(2048)));
    // A style attribute past twice the CSS sanitizer's own cap is dropped before it is read.
    let long_style = format!("<p style='{}'>x</p>", "color:red;".repeat(900));
    assert_eq!(sanitize(&long_style, false, true).html, "<p>x</p>");
}

#[test]
fn a_link_keeps_only_the_schemes_kept_and_counts_the_rest() {
    let kept = |href: &str| format!("<a href=\"{href}\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">m</a>");
    holds(&[
        ("<a href='mailto:ada@example.test'>m</a>", false, true, &kept("mailto:ada@example.test"), [0, 0, 0]),
        ("<a href='cid:x'>m</a>", false, true, &kept("cid:x"), [0, 0, 0]),
        ("<a href=' HTTPS://example.test/'>m</a>", false, true, &kept(" HTTPS://example.test/"), [0, 0, 0]),
        ("<a href='//evil.example.test/'>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        ("<a href=''>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        ("<a href>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        ("<a>m</a>", false, true, "<a>m</a>", [0, 0, 0]),
        ("<a href='java\tscript:alert(1)'>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        ("<a href='&#106;avascript:alert(1)'>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        ("<a href='vbscript:x'>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        ("<a href='data:text/html,x'>m</a>", false, true, "<a>m</a>", [0, 1, 0]),
        // The controls a scheme could hide behind are read past, and the URL is written as it came.
        ("<a href='\u{1}https://example.test/'>m</a>", false, true, &kept("\u{1}https://example.test/"), [0, 0, 0]),
    ]);
}

#[test]
fn an_image_loads_only_when_the_person_asks() {
    holds(&[
        (
            "<img src='data:image/png;base64,iVBOR' alt='ok'>",
            true,
            true,
            "<img src=\"data:image/png;base64,iVBOR\" alt=\"ok\"/>",
            [0, 0, 0],
        ),
        ("<img src='data:image/png;base64,iVBOR' alt='ok'>", false, true, "<img alt=\"ok\"/>", [1, 0, 0]),
        ("<img src='https://e.example.test/a.png' alt='a'>", false, true, "<img alt=\"a\"/>", [1, 0, 0]),
        (
            "<img src='https://e.example.test/a.png' alt='a'>",
            true,
            true,
            "<img src=\"https://e.example.test/a.png\" alt=\"a\"/>",
            [0, 0, 0],
        ),
        ("<img src='cid:part1@example.test'>", false, true, "<img/>", [1, 0, 0]),
        ("<img src='cid:part1@example.test'>", true, true, "<img src=\"cid:part1@example.test\"/>", [0, 0, 0]),
        (
            "<img src='  HTTPS://e.example.test/a.png'>",
            true,
            true,
            "<img src=\"  HTTPS://e.example.test/a.png\"/>",
            [0, 0, 0],
        ),
        ("<img src='javascript:alert(1)'>", true, true, "<img/>", [0, 0, 0]),
        ("<img src='//e.example.test/a.png'>", true, true, "<img/>", [0, 0, 0]),
        ("<img src='mailto:ada@example.test'>", true, true, "<img/>", [0, 0, 0]),
        ("<img src='data:image/svg+xml;base64,PHN2Zz4='>", true, true, "<img/>", [0, 0, 0]),
        ("<img srcset='https://e.example.test/a.png 1x' src='cid:a'>", true, true, "<img src=\"cid:a\"/>", [0, 0, 0]),
        (
            "<p style='background:url(https://t.example.test/b.png)'>x</p>",
            true,
            true,
            "<p style=\"background:url(&quot;https://t.example.test/b.png&quot;)\">x</p>",
            [0, 0, 0],
        ),
        ("<p style='background:url(https://t.example.test/b.png)'>x</p>", false, true, "<p>x</p>", [0, 0, 1]),
    ]);
}

#[test]
fn a_style_sheet_is_written_sanitized() {
    holds(&[
        ("<style>p{color:red}</style>after", false, true, "<style>p{color:red}</style>after", [0, 0, 0]),
        ("<style>body{background:url(https://tracker.example.test/bg.png)}</style>v", false, true, "v", [0, 0, 1]),
        (
            "<style>body{background:url(https://tracker.example.test/bg.png)}</style>v",
            true,
            true,
            "<style>body{background:url(\"https://tracker.example.test/bg.png\")}</style>v",
            [0, 0, 0],
        ),
        // A sheet never closed is written at the end, so it takes nothing with it.
        ("<style>p{color:red}", false, true, "<style>p{color:red}</style>", [0, 0, 0]),
        (
            "<style>p{color:red}</style><style>q{color:blue}</style>",
            false,
            true,
            "<style>p{color:red}</style><style>q{color:blue}</style>",
            [0, 0, 0],
        ),
        ("<style>p{font-family:'</style>'}</style>x", false, true, "'}x", [0, 0, 0]),
    ]);
}

#[test]
fn a_comment_goes_with_all_it_holds() {
    holds(&[
        ("<p>a<!-- hidden -->b</p>", false, true, "<p>ab</p>", [0, 0, 0]),
        ("<!--<script>alert(1)</script>-->x", false, true, "x", [0, 0, 0]),
        ("<!--[if mso]><img src='https://e.example.test/t.gif'><![endif]-->x", false, true, "x", [0, 0, 0]),
        ("<p>hi</p><!-- <blockquote>quoted</blockquote> -->", false, true, "<p>hi</p>", [0, 0, 0]),
        ("<!-->x", false, true, "x", [0, 0, 0]),
        ("<!--->x", false, true, "x", [0, 0, 0]),
        ("<!-- a --!>x", false, true, "x", [0, 0, 0]),
        ("<!-- never closed <p>x</p>", false, true, "", [0, 0, 0]),
        ("<! bogus>x", false, true, "x", [0, 0, 0]),
        ("<?xml version='1.0'?>x", false, true, "x", [0, 0, 0]),
        ("</ x>y", false, true, "y", [0, 0, 0]),
        ("<p title=\"<!--\">x</p>-->", false, true, "<p title=\"&lt;!--\">x</p>--&gt;", [0, 0, 0]),
    ]);
}

#[test]
fn what_never_ends_is_left_for_the_browser_to_close() {
    holds(&[
        ("<p><b>unclosed", false, true, "<p><b>unclosed", [0, 0, 0]),
        ("<a href=", false, true, "", [0, 0, 0]),
        ("<p>x<b", false, true, "<p>x", [0, 0, 0]),
        ("<p>x<", false, true, "<p>x&lt;", [0, 0, 0]),
        ("</x></y>", false, true, "", [0, 0, 0]),
        ("", false, true, "", [0, 0, 0]),
    ]);
}

#[test]
fn the_quoted_reply_folds_when_something_comes_before_it() {
    let folded = |before: &str, quoted: &str| format!("{before}{FOLD_OPEN}{quoted}{FOLD_CLOSE}");
    holds(&[
        (
            "<p>Thanks!</p><blockquote>quoted</blockquote>",
            false,
            true,
            &folded("<p>Thanks!</p>", "<blockquote>quoted</blockquote>"),
            [0, 0, 0],
        ),
        (
            "<p>Thanks!</p><blockquote>quoted</blockquote>",
            false,
            false,
            "<p>Thanks!</p><blockquote>quoted</blockquote>",
            [0, 0, 0],
        ),
        ("<blockquote>all quote</blockquote>", false, true, "<blockquote>all quote</blockquote>", [0, 0, 0]),
        ("  \n <blockquote>all quote</blockquote>", false, true, "  \n <blockquote>all quote</blockquote>", [0, 0, 0]),
        (
            "<p>a</p><blockquote>q1<blockquote>q2</blockquote></blockquote><p>after</p><blockquote>q3</blockquote>",
            false,
            true,
            &folded(
                "<p>a</p>",
                "<blockquote>q1<blockquote>q2</blockquote></blockquote><p>after</p><blockquote>q3</blockquote>",
            ),
            [0, 0, 0],
        ),
        (
            "<blockquote>q</blockquote>text<blockquote>q2</blockquote>",
            false,
            true,
            &folded("<blockquote>q</blockquote>text", "<blockquote>q2</blockquote>"),
            [0, 0, 0],
        ),
        (
            "hi</blockquote><blockquote>q</blockquote>",
            false,
            true,
            &folded("hi</blockquote>", "<blockquote>q</blockquote>"),
            [0, 0, 0],
        ),
        // Text in a dropped element is nothing to fold under.
        ("<title>t</title><blockquote>q</blockquote>", false, true, "<blockquote>q</blockquote>", [0, 0, 0]),
    ]);
}

#[test]
fn without_fold_takes_the_fold_out_and_nothing_else() {
    let sanitized = sanitize("<p>Thanks!</p><blockquote>quoted</blockquote><p>tail</p>", false, true).html;
    assert_eq!(without_fold(&sanitized), "<p>Thanks!</p><blockquote>quoted</blockquote><p>tail</p>");
    assert_eq!(without_fold("<p>no fold</p>"), "<p>no fold</p>");
    assert_eq!(without_fold(&format!("a{FOLD_OPEN}b")), "ab");
    assert_eq!(without_fold(&format!("a{FOLD_OPEN}<details>b</details>c{FOLD_CLOSE}")), "a<details>b</details>c");
}

// -- as a browser reads a message ----------------------------------------------------------------------------------
// Where Python's html.parser read one otherwise, the comment says what it made of it.

#[test]
fn a_slash_closes_nothing_but_a_void_tag_svg_and_math() {
    // HTML ignores the slash on any other element, so what follows is inside it, as the browser shows it.
    // html.parser closed each at once: `<div/>after` was `<div></div>after`, and `<script/>alert(1)` showed the text.
    holds(&[
        ("<br/>x", false, true, "<br/>x", [0, 0, 0]),
        ("<div/>after", false, true, "<div>after", [0, 0, 0]),
        ("<b/>x", false, true, "<b>x", [0, 0, 0]),
        ("<SPAN/>x", false, true, "<span>x", [0, 0, 0]),
        ("<blink/>x", false, true, "x", [0, 0, 0]),
        ("<a href='javascript:alert(1)'/>x", false, true, "<a>x", [0, 1, 0]),
        (
            "<a href='https://example.test/'/>x",
            false,
            true,
            "<a href=\"https://example.test/\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">x",
            [0, 0, 0],
        ),
        // So a script, an iframe, a title or a style that "closes itself" holds the rest of the message.
        ("<script/>alert(1)", false, true, "", [0, 0, 0]),
        ("<SCRIPT/>alert(1)", false, true, "", [0, 0, 0]),
        ("<iframe src='https://e.example.test/' />after", false, true, "", [0, 0, 0]),
        ("<title/>after", false, true, "", [0, 0, 0]),
        ("<style/>p{}", false, true, "", [0, 0, 0]),
        // A browser closes <svg/> and <math/> at once, and inside them any tag written to close itself.
        ("<svg/>after", false, true, "after", [0, 0, 0]),
        ("<math/>after", false, true, "after", [0, 0, 0]),
        ("<svg><svg/>inner</svg>after", false, true, "after", [0, 0, 0]),
        ("<img src='https://e.example.test/a.png' onerror=alert(1) />", false, true, "<img/>", [1, 0, 0]),
    ]);
}

#[test]
fn raw_text_ends_where_a_browser_ends_it() {
    holds(&[
        // A script's text that opens a comment holds a <script>...</script> of its own. html.parser ended the script
        // at the first </script>, and showed the x.
        ("<script><!--<script></script>x</script>after", false, true, "after", [0, 0, 0]),
        // Inside <svg> a <style> is SVG's, whose text is markup: </svg> ends it, and what follows is shown.
        // html.parser read raw text to </style>, and dropped all the rest of the message with the <svg>.
        ("<svg><style></svg>after</style>more", false, true, "aftermore", [0, 0, 0]),
        // A CDATA section is one only inside <svg> or <math>.
        ("<svg><![CDATA[</svg>]]>after</svg>more", false, true, "more", [0, 0, 0]),
        ("<![CDATA[x]]>y", false, true, "y", [0, 0, 0]),
        ("<noembed><b>x</b></noembed>", false, true, "&lt;b&gt;x&lt;/b&gt;", [0, 0, 0]),
        ("<noframes><b>x</b></noframes>", false, true, "&lt;b&gt;x&lt;/b&gt;", [0, 0, 0]),
        // Scripting is off in the frame, so a <noscript> holds markup.
        ("<noscript></noscript><b>x</b>", false, true, "<b>x</b>", [0, 0, 0]),
    ]);
}

#[test]
fn characters_are_read_as_a_browser_reads_them() {
    holds(&[
        // A NUL in text is dropped; anywhere else it is U+FFFD. html.parser kept it, and read past it in a scheme.
        ("x\u{0}y", false, true, "xy", [0, 0, 0]),
        ("<p title='a\u{0}b'>c</p>", false, true, "<p title=\"a\u{fffd}b\">c</p>", [0, 0, 0]),
        ("<a href='https\u{0}://example.test'>c</a>", false, true, "<a>c</a>", [0, 1, 0]),
        // Line breaks are made one. html.parser kept each \r.
        ("a\r\nb\rc", false, true, "a\nb\nc", [0, 0, 0]),
        ("<p title='a\r\nb'>c</p>", false, true, "<p title=\"a\nb\">c</p>", [0, 0, 0]),
        // A reference to a control character keeps it; one to NUL, a surrogate or past Unicode is U+FFFD.
        // html.parser dropped the control character.
        ("&#0;&#x80;&#xD800;&#1;&#x110000;", false, true, "\u{fffd}\u{20ac}\u{fffd}\u{1}\u{fffd}", [0, 0, 0]),
        // In an attribute, a named reference with no semicolon before a letter, a digit or = is text.
        (
            "<a href=https://example.test/?a=1&copy=2>x</a>",
            false,
            true,
            "<a href=\"https://example.test/?a=1&amp;copy=2\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">x</a>",
            [0, 0, 0],
        ),
        ("<p title='&amp x &lt'>y</p>", false, true, "<p title=\"&amp; x &lt;\">y</p>", [0, 0, 0]),
        // A byte order mark is text already decoded, like any other character.
        ("\u{feff}hello", false, true, "\u{feff}hello", [0, 0, 0]),
    ]);
}

#[test]
fn an_attribute_named_twice_is_the_first() {
    // html.parser gave both, and the sanitizer kept whichever it could, counting the other.
    holds(&[
        (
            "<a href='https://example.test/' href='javascript:x'>c</a>",
            false,
            true,
            "<a href=\"https://example.test/\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">c</a>",
            [0, 0, 0],
        ),
        ("<a href='javascript:x' href='https://example.test/'>c</a>", false, true, "<a>c</a>", [0, 1, 0]),
    ]);
}

#[test]
fn a_tag_reads_as_a_browser_reads_it() {
    holds(&[
        ("</p foo>x", false, true, "</p>x", [0, 0, 0]),
        ("<p\tclass=x\nalign=center>y</p>", false, true, "<p align=\"center\">y</p>", [0, 0, 0]),
        ("<p align=center/>y", false, true, "<p align=\"center/\">y", [0, 0, 0]),
        ("<img/src=x>", false, true, "<img/>", [0, 0, 0]),
        (
            "<a/href='https://example.test'>x</a>",
            false,
            true,
            "<a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">x</a>",
            [0, 0, 0],
        ),
        ("<p =x>y</p>", false, true, "<p>y</p>", [0, 0, 0]),
        ("<div title=a'b>c</div>", false, true, "<div title=\"a&#x27;b\">c</div>", [0, 0, 0]),
        ("</3>y", false, true, "y", [0, 0, 0]),
        ("</>y", false, true, "y", [0, 0, 0]),
    ]);
}

#[test]
fn a_long_message_is_read_whole() {
    let paragraphs = "<p>Line <b>bold</b> &amp; more</p>".repeat(20_000);
    let out = sanitize(&paragraphs, false, true).html;
    assert_eq!(out, "<p>Line <b>bold</b> &amp; more</p>".repeat(20_000));
}
