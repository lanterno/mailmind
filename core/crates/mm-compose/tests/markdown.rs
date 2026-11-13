//! What a person's Markdown comes to, and what stays plain text: the grammar case by case, and generated text
//! compared whole with what the Python app answers for it.
#![allow(clippy::unwrap_used)]

use std::collections::BTreeSet;

use mm_compose::markdown::{MAX_TEXT, content_id, is_token, plain_pictures, render, token_of};
use serde_json::Value;

fn set(tokens: &[&str]) -> BTreeSet<String> {
    tokens.iter().map(|t| (*t).to_owned()).collect()
}

fn md(text: &str) -> Option<String> {
    render(text, &BTreeSet::new())
}

fn strings(value: &Value) -> BTreeSet<String> {
    value.as_array().unwrap().iter().map(|t| t.as_str().unwrap().to_owned()).collect()
}

// Each expected value below is what the Python app's `markdown.render` or `plain_pictures` gives.

#[test]
fn paragraphs_and_line_breaks() {
    assert_eq!(md("one *x*\n\ntwo").as_deref(), Some("<p>one <em>x</em></p>\n<p>two</p>"));
    assert_eq!(md("*Hi*\nthere").as_deref(), Some("<p><em>Hi</em><br/>\nthere</p>"));
    assert_eq!(
        md("*Hi*\n\n-- \nAda\nBrightly Ltd").as_deref(),
        Some("<p><em>Hi</em></p>\n<p>--<br/>\nAda<br/>\nBrightly Ltd</p>")
    );
    assert_eq!(md("*a*\n\n\n\nb  \n  c").as_deref(), Some("<p><em>a</em></p>\n<p>b<br/>\nc</p>"));
}

#[test]
fn emphasis_and_strong() {
    assert_eq!(md("**strong**").as_deref(), Some("<p><strong>strong</strong></p>"));
    assert_eq!(md("__strong__").as_deref(), Some("<p><strong>strong</strong></p>"));
    assert_eq!(md("*em*").as_deref(), Some("<p><em>em</em></p>"));
    assert_eq!(md("_em_").as_deref(), Some("<p><em>em</em></p>"));
    assert_eq!(md("***both***").as_deref(), Some("<p><strong><em>both</em></strong></p>"));
    assert_eq!(md("**with *inner* em**").as_deref(), Some("<p><strong>with <em>inner</em> em</strong></p>"));
    assert_eq!(md("*unclosed and *em*").as_deref(), Some("<p><em>unclosed and *em</em></p>"));
    assert_eq!(md("**unclosed *em*").as_deref(), Some("<p>*<em>unclosed *em</em></p>"));
    assert_eq!(md("snake_case_name *x*").as_deref(), Some("<p>snake_case_name <em>x</em></p>"));
    assert_eq!(md("a*b*c *x*").as_deref(), Some("<p>a*b*c <em>x</em></p>"));
    assert_eq!(md("* not em* *em*").as_deref(), Some("<ul><li>not em* <em>em</em></li></ul>"));
    assert_eq!(
        md("**a** and __b__ and *c* and _d_").as_deref(),
        Some("<p><strong>a</strong> and <strong>b</strong> and <em>c</em> and <em>d</em></p>")
    );
}

#[test]
fn links() {
    assert_eq!(
        md("[the plan](https://example.test/a_(b))").as_deref(),
        Some(
            "<p><a href=\"https://example.test/a_(b)\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">the plan</a></p>"
        )
    );
    assert_eq!(
        md("[x](https://example.test \"A title\")").as_deref(),
        Some("<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">x</a></p>")
    );
    assert_eq!(
        md("[Lin](mailto:lin@example.test)").as_deref(),
        Some(
            "<p><a href=\"mailto:lin@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">Lin</a></p>"
        )
    );
    assert_eq!(md("[x](javascript:alert(1)) *em*").as_deref(), Some("<p>[x](javascript:alert(1)) <em>em</em></p>"));
    assert_eq!(
        md("[a *word*](https://example.test/a)").as_deref(),
        Some(
            "<p><a href=\"https://example.test/a\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">a <em>word</em></a></p>"
        )
    );
    assert_eq!(
        md("[outer [inner](https://example.test/a)](https://example.test/b)").as_deref(),
        Some(
            "<p><a href=\"https://example.test/a\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">outer [inner</a>](<a href=\"https://example.test/b\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test/b</a>)</p>"
        )
    );
    assert_eq!(
        md("[a](<https://example.test/b>)").as_deref(),
        Some("<p><a href=\"https://example.test/b\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">a</a></p>")
    );
    assert_eq!(md("[a]( https://example.test/c 'not a title' )").as_deref(), None);
    assert_eq!(md("[a](https://example.test/a<b)").as_deref(), None);
}

#[test]
fn angled_and_bare_addresses() {
    assert_eq!(
        md("*a* <https://example.test>").as_deref(),
        Some(
            "<p><em>a</em> <a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test</a></p>"
        )
    );
    assert_eq!(
        md("see https://example.test/a_b. *now*").as_deref(),
        Some(
            "<p>see <a href=\"https://example.test/a_b\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test/a_b</a>. <em>now</em></p>"
        )
    );
    assert_eq!(md("see https://example.test/a_b.").as_deref(), None);
    assert_eq!(
        md("*x* (https://example.test/a_(b)).").as_deref(),
        Some(
            "<p><em>x</em> (<a href=\"https://example.test/a_(b)\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test/a_(b)</a>).</p>"
        )
    );
    assert_eq!(md("*x* xhttps://example.test").as_deref(), Some("<p><em>x</em> xhttps://example.test</p>"));
    assert_eq!(
        md("*x* HTTPS://EXAMPLE.TEST/A?").as_deref(),
        Some(
            "<p><em>x</em> <a href=\"HTTPS://EXAMPLE.TEST/A\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">HTTPS://EXAMPLE.TEST/A</a>?</p>"
        )
    );
    assert_eq!(md("*x* <ftp://example.test>").as_deref(), Some("<p><em>x</em> &lt;ftp://example.test&gt;</p>"));
    assert_eq!(md("*x* \u{e9}https://example.test").as_deref(), Some("<p><em>x</em> \u{e9}https://example.test</p>"));
    assert_eq!(
        md("*x* \u{301}https://example.test").as_deref(),
        Some(
            "<p><em>x</em> \u{301}<a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test</a></p>"
        )
    );
}

#[test]
fn escapes() {
    assert_eq!(md("\\*not em\\* but *em*").as_deref(), Some("<p>*not em* but <em>em</em></p>"));
    assert_eq!(md("C:\\Users\\ada\\*.txt").as_deref(), None);
    assert_eq!(md("\\<b> *x*").as_deref(), Some("<p>&lt;b&gt; <em>x</em></p>"));
    assert_eq!(md("*x* \\\u{e9} \\").as_deref(), Some("<p><em>x</em> \\\u{e9} \\</p>"));
    assert_eq!(md("\\\\*x*").as_deref(), Some("<p>\\<em>x</em></p>"));
}

#[test]
fn code() {
    assert_eq!(md("`code`").as_deref(), Some("<p><code>code</code></p>"));
    assert_eq!(md("``a ` b``").as_deref(), Some("<p><code>a ` b</code></p>"));
    assert_eq!(md("` *not em* `").as_deref(), Some("<p><code>*not em*</code></p>"));
    assert_eq!(md("`unclosed *x*").as_deref(), Some("<p>`unclosed <em>x</em></p>"));
    assert_eq!(md("` `").as_deref(), Some("<p><code> </code></p>"));
    assert_eq!(md("`a\nb`").as_deref(), Some("<p><code>a b</code></p>"));
    assert_eq!(md("``` `a` ```").as_deref(), Some("<p><code>`a`</code></p>"));
}

#[test]
fn fences() {
    assert_eq!(
        md("```python\n*a* <b>\n\n  x\n```\nafter").as_deref(),
        Some("<pre><code>*a* &lt;b&gt;\n\n  x</code></pre>\n<p>after</p>")
    );
    assert_eq!(md("~~~\na\n~~~~").as_deref(), Some("<pre><code>a</code></pre>"));
    assert_eq!(md("```\na\n~~~").as_deref(), None);
    assert_eq!(md("```\nnever closed").as_deref(), None);
    assert_eq!(md("```a`b\nx\n```").as_deref(), Some("<p><code>a`b x </code></p>"));
    assert_eq!(md("   ```\nx\n```").as_deref(), Some("<pre><code>x</code></pre>"));
    assert_eq!(md("    ```\nx\n```").as_deref(), Some("<p><code>x</code></p>"));
}

#[test]
fn headings() {
    assert_eq!(md("# One").as_deref(), Some("<h1>One</h1>"));
    assert_eq!(md("###### Six").as_deref(), Some("<h6>Six</h6>"));
    assert_eq!(md("## Closed ##").as_deref(), Some("<h2>Closed</h2>"));
    assert_eq!(md("# C# and F#").as_deref(), Some("<h1>C# and F#</h1>"));
    assert_eq!(md("####### seven *x*").as_deref(), Some("<p>####### seven <em>x</em></p>"));
    assert_eq!(md("# #").as_deref(), None);
    assert_eq!(md("## a \t##").as_deref(), Some("<h2>a</h2>"));
    assert_eq!(md("#no space *x*").as_deref(), Some("<p>#no space <em>x</em></p>"));
}

#[test]
fn quotes_and_lists_inside_each_other() {
    assert_eq!(
        md("> one\n> *two*\n\nafter").as_deref(),
        Some("<blockquote><p>one<br/>\n<em>two</em></p></blockquote>\n<p>after</p>")
    );
    assert_eq!(
        md("> > *deep*").as_deref(),
        Some("<blockquote><blockquote><p><em>deep</em></p></blockquote></blockquote>")
    );
    assert_eq!(
        md("- one\n  - inner\n- two").as_deref(),
        Some("<ul><li>one\n<ul><li>inner</li></ul></li><li>two</li></ul>")
    );
    assert_eq!(md("> - a\n> - b").as_deref(), Some("<blockquote><ul><li>a</li><li>b</li></ul></blockquote>"));
    assert_eq!(md("- > a\n  > b").as_deref(), Some("<ul><li><blockquote><p>a<br/>\nb</p></blockquote></li></ul>"));
    assert_eq!(
        md("> > > > > > > > > > > > *x*").as_deref(),
        Some(
            "<blockquote><blockquote><blockquote><blockquote><blockquote><blockquote><blockquote><blockquote><p>&gt; &gt; &gt; &gt; <em>x</em></p></blockquote></blockquote></blockquote></blockquote></blockquote></blockquote></blockquote></blockquote>"
        )
    );
    assert_eq!(
        md("- - - - - - - - - - - - x").as_deref(),
        Some(
            "<ul><li><ul><li><ul><li><ul><li><ul><li><ul><li><ul><li><ul><li>- - - - x</li></ul></li></ul></li></ul></li></ul></li></ul></li></ul></li></ul></li></ul>"
        )
    );
}

#[test]
fn numbered_lists() {
    assert_eq!(md("3. three\n4. four").as_deref(), Some("<ol start=\"3\"><li>three</li><li>four</li></ol>"));
    assert_eq!(md("1. one\n2) two").as_deref(), Some("<ol><li>one</li><li>two</li></ol>"));
    assert_eq!(
        md("\u{1d7d1}. bold three\n\u{1d7d2}. four").as_deref(),
        Some("<ol start=\"3\"><li>bold three</li><li>four</li></ol>")
    );
    assert_eq!(md("\u{660}\u{661}. one").as_deref(), Some("<ol><li>one</li></ol>"));
    assert_eq!(md("1234567890. not one *x*").as_deref(), Some("<p>1234567890. not one <em>x</em></p>"));
    assert_eq!(md("- a\n1. b").as_deref(), Some("<ul><li>a</li></ul>\n<ol><li>b</li></ol>"));
}

#[test]
fn tight_and_loose_items() {
    assert_eq!(md("- a\n- b").as_deref(), Some("<ul><li>a</li><li>b</li></ul>"));
    assert_eq!(md("- a\n\n- b").as_deref(), Some("<ul><li>a</li><li>b</li></ul>"));
    assert_eq!(md("- a\n\n  more\n- b").as_deref(), Some("<ul><li><p>a</p>\n<p>more</p></li><li>b</li></ul>"));
    assert_eq!(md("Things:\n- one\n\n- two").as_deref(), Some("<p>Things:</p>\n<ul><li>one</li><li>two</li></ul>"));
    assert_eq!(md("- a\n  b\n- c").as_deref(), Some("<ul><li>a<br/>\nb</li><li>c</li></ul>"));
}

#[test]
fn a_numbered_line_inside_a_paragraph() {
    assert_eq!(md("It was\n1985. A vintage year").as_deref(), None);
    assert_eq!(md("Things:\n1. one *x*").as_deref(), Some("<p>Things:</p>\n<ol><li>one <em>x</em></li></ol>"));
    assert_eq!(md("Things:\n2. two *x*").as_deref(), Some("<p>Things:<br/>\n2. two <em>x</em></p>"));
    assert_eq!(md("Things:\n\u{661}. one *x*").as_deref(), Some("<p>Things:<br/>\n\u{661}. one <em>x</em></p>"));
}

#[test]
fn non_ascii_and_combining_characters() {
    assert_eq!(md("e\u{301}t\u{e9} *\u{e9}t\u{e9}*").as_deref(), Some("<p>e\u{301}t\u{e9} <em>\u{e9}t\u{e9}</em></p>"));
    assert_eq!(md("\u{e9}*a*").as_deref(), None);
    assert_eq!(md("\u{301}*a*").as_deref(), Some("<p>\u{301}<em>a</em></p>"));
    assert_eq!(md("\u{24b6}*a*").as_deref(), Some("<p>\u{24b6}<em>a</em></p>"));
    assert_eq!(md("\u{b2}*a*").as_deref(), None);
    assert_eq!(
        md("**\u{1f600}** _\u{645}\u{631}\u{62d}\u{628}\u{627}_").as_deref(),
        Some("<p><strong>\u{1f600}</strong> <em>\u{645}\u{631}\u{62d}\u{628}\u{627}</em></p>")
    );
    assert_eq!(md("*a\u{a0}*").as_deref(), None);
    assert_eq!(md("\u{65e5}\u{672c}*\u{8a9e}*").as_deref(), None);
}

#[test]
fn carriage_returns() {
    assert_eq!(md("*a*\r\nb\r\n\r\nc").as_deref(), Some("<p><em>a</em><br/>\nb</p>\n<p>c</p>"));
    assert_eq!(md("*a*\rb\r\rc").as_deref(), Some("<p><em>a</em><br/>\nb</p>\n<p>c</p>"));
    assert_eq!(md("- a\r\n- b").as_deref(), Some("<ul><li>a</li><li>b</li></ul>"));
}

#[test]
fn text_with_no_markdown() {
    assert_eq!(md("Hi Lin,\n\nThanks for the note.\n\n-- \nAda").as_deref(), None);
    assert_eq!(md("See https://example.test/a_b_c for more").as_deref(), None);
    assert_eq!(md("Write to ada@example.test or lin@example.test").as_deref(), None);
    assert_eq!(md("3 * 4 * 5 = 60, and 2*3*4 is less").as_deref(), None);
    assert_eq!(md("snake_case_name and my_var_ and _private").as_deref(), None);
    assert_eq!(md("Price* (*see below)").as_deref(), None);
    assert_eq!(md("#hashtag and #another").as_deref(), None);
    assert_eq!(md("A list of one [1] and [two] things").as_deref(), None);
    assert_eq!(md("An <em>angle</em> of a tag").as_deref(), None);
    assert_eq!(md("> On Tuesday, Lin wrote:\n> Shall we meet?\n\nYes.").as_deref(), None);
    assert_eq!(md("<https://example.test>").as_deref(), None);
    assert_eq!(md("").as_deref(), None);
}

#[test]
fn pictures_shown_and_not() {
    assert_eq!(
        render("![pic](cid:a1)", &set(&["a1"])).as_deref(),
        Some("<p><img src=\"cid:a1@mailmind\" alt=\"pic\"/></p>")
    );
    assert_eq!(render("![pic](cid:a1)", &set(&[])).as_deref(), None);
    assert_eq!(render("![pic](cid:zz) *x*", &set(&["a1"])).as_deref(), Some("<p>![pic](cid:zz) <em>x</em></p>"));
    assert_eq!(
        render("x![a \"b\" <c>](cid:a1)y", &set(&["a1"])).as_deref(),
        Some("<p>x<img src=\"cid:a1@mailmind\" alt=\"a &quot;b&quot; &lt;c&gt;\"/>y</p>")
    );
    assert_eq!(render("![](cid:a1)", &set(&["a1"])).as_deref(), Some("<p><img src=\"cid:a1@mailmind\" alt=\"\"/></p>"));
    assert_eq!(
        render("[![a](cid:a1)](https://example.test)", &set(&["a1"])).as_deref(),
        Some(
            "<p>[<img src=\"cid:a1@mailmind\" alt=\"a\"/>](<a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test</a>)</p>"
        )
    );
    assert_eq!(
        plain_pictures("see ![pic](cid:a1) and ![other](cid:zz)", &set(&["a1"])),
        "see [pic] and ![other](cid:zz)"
    );
    assert_eq!(plain_pictures("![pic](cid:a1)", &set(&[])), "![pic](cid:a1)");
    assert_eq!(plain_pictures("![](cid:a1)", &set(&["a1"])), "[]");
}

/// Each limit at its edge, in characters of one, two and four bytes: a link's words, an address angled or bare, a
/// picture's name.
#[test]
fn each_bound_counts_characters() {
    assert_eq!(
        render(&["[", &"a".repeat(1998), "](https://example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"a".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"a".repeat(1998), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"a".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[a [", &"a".repeat(1998), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p>[a <a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"a".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"a".repeat(1999), "](https://example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"a".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"a".repeat(1999), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"a".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[a [", &"a".repeat(1999), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p>[a <a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"a".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(render(&["[", &"a".repeat(2000), "](https://example.test) *x*"].concat(), &set(&[])), Some(["<p>[", &"a".repeat(2000), "](<a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test</a>) <em>x</em></p>"].concat()));
    assert_eq!(
        render(&["[", &"a".repeat(2000), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(["<p>[", &"a".repeat(2000), "](mailto:ada@example.test) <em>x</em></p>"].concat())
    );
    assert_eq!(
        render(&["[a [", &"a".repeat(2000), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(["<p>[a [", &"a".repeat(2000), "](mailto:ada@example.test) <em>x</em></p>"].concat())
    );
    assert_eq!(
        render(&["*x* <https:", &"a".repeat(2047), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https:",
                &"a".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https:",
                &"a".repeat(2047),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <mailto:", &"a".repeat(2047), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"mailto:",
                &"a".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">mailto:",
                &"a".repeat(2047),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"a".repeat(2047), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"a".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"a".repeat(2047),
                "</a> after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"a".repeat(2047), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"a".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"a".repeat(2047),
                "</a>).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <https:", &"a".repeat(2048), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https:",
                &"a".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https:",
                &"a".repeat(2048),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <mailto:", &"a".repeat(2048), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"mailto:",
                &"a".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">mailto:",
                &"a".repeat(2048),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"a".repeat(2048), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"a".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"a".repeat(2048),
                "</a> after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"a".repeat(2048), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"a".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"a".repeat(2048),
                "</a>).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <https:", &"a".repeat(2049), ">"].concat(), &set(&[])),
        Some(["<p><em>x</em> &lt;https:", &"a".repeat(2049), "&gt;</p>"].concat())
    );
    assert_eq!(
        render(&["*x* <mailto:", &"a".repeat(2049), ">"].concat(), &set(&[])),
        Some(["<p><em>x</em> &lt;mailto:", &"a".repeat(2049), "&gt;</p>"].concat())
    );
    assert_eq!(
        render(&["*x* https://", &"a".repeat(2049), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"a".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"a".repeat(2048),
                "</a>a after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"a".repeat(2049), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"a".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"a".repeat(2048),
                "</a>a).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["![", &"a".repeat(299), "](cid:a1)"].concat(), &set(&["a1"])),
        Some(["<p><img src=\"cid:a1@mailmind\" alt=\"", &"a".repeat(299), "\"/></p>"].concat())
    );
    assert_eq!(
        render(&["![", &"a".repeat(300), "](cid:a1)"].concat(), &set(&["a1"])),
        Some(["<p><img src=\"cid:a1@mailmind\" alt=\"", &"a".repeat(300), "\"/></p>"].concat())
    );
    assert_eq!(render(&["![", &"a".repeat(301), "](cid:a1)"].concat(), &set(&["a1"])), None);
    assert_eq!(
        render(&["[", &"\u{e9}".repeat(1998), "](https://example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{e9}".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"\u{e9}".repeat(1998), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{e9}".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[a [", &"\u{e9}".repeat(1998), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p>[a <a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{e9}".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"\u{e9}".repeat(1999), "](https://example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{e9}".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"\u{e9}".repeat(1999), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{e9}".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[a [", &"\u{e9}".repeat(1999), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p>[a <a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{e9}".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(render(&["[", &"\u{e9}".repeat(2000), "](https://example.test) *x*"].concat(), &set(&[])), Some(["<p>[", &"\u{e9}".repeat(2000), "](<a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test</a>) <em>x</em></p>"].concat()));
    assert_eq!(
        render(&["[", &"\u{e9}".repeat(2000), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(["<p>[", &"\u{e9}".repeat(2000), "](mailto:ada@example.test) <em>x</em></p>"].concat())
    );
    assert_eq!(
        render(&["[a [", &"\u{e9}".repeat(2000), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(["<p>[a [", &"\u{e9}".repeat(2000), "](mailto:ada@example.test) <em>x</em></p>"].concat())
    );
    assert_eq!(
        render(&["*x* <https:", &"\u{e9}".repeat(2047), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https:",
                &"\u{e9}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https:",
                &"\u{e9}".repeat(2047),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <mailto:", &"\u{e9}".repeat(2047), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"mailto:",
                &"\u{e9}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">mailto:",
                &"\u{e9}".repeat(2047),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{e9}".repeat(2047), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{e9}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{e9}".repeat(2047),
                "</a> after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{e9}".repeat(2047), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{e9}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{e9}".repeat(2047),
                "</a>).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <https:", &"\u{e9}".repeat(2048), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https:",
                &"\u{e9}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https:",
                &"\u{e9}".repeat(2048),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <mailto:", &"\u{e9}".repeat(2048), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"mailto:",
                &"\u{e9}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">mailto:",
                &"\u{e9}".repeat(2048),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{e9}".repeat(2048), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{e9}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{e9}".repeat(2048),
                "</a> after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{e9}".repeat(2048), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{e9}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{e9}".repeat(2048),
                "</a>).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <https:", &"\u{e9}".repeat(2049), ">"].concat(), &set(&[])),
        Some(["<p><em>x</em> &lt;https:", &"\u{e9}".repeat(2049), "&gt;</p>"].concat())
    );
    assert_eq!(
        render(&["*x* <mailto:", &"\u{e9}".repeat(2049), ">"].concat(), &set(&[])),
        Some(["<p><em>x</em> &lt;mailto:", &"\u{e9}".repeat(2049), "&gt;</p>"].concat())
    );
    assert_eq!(
        render(&["*x* https://", &"\u{e9}".repeat(2049), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{e9}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{e9}".repeat(2048),
                "</a>\u{e9} after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{e9}".repeat(2049), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{e9}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{e9}".repeat(2048),
                "</a>\u{e9}).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["![", &"\u{e9}".repeat(299), "](cid:a1)"].concat(), &set(&["a1"])),
        Some(["<p><img src=\"cid:a1@mailmind\" alt=\"", &"\u{e9}".repeat(299), "\"/></p>"].concat())
    );
    assert_eq!(
        render(&["![", &"\u{e9}".repeat(300), "](cid:a1)"].concat(), &set(&["a1"])),
        Some(["<p><img src=\"cid:a1@mailmind\" alt=\"", &"\u{e9}".repeat(300), "\"/></p>"].concat())
    );
    assert_eq!(render(&["![", &"\u{e9}".repeat(301), "](cid:a1)"].concat(), &set(&["a1"])), None);
    assert_eq!(
        render(&["[", &"\u{1f600}".repeat(1998), "](https://example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{1f600}".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"\u{1f600}".repeat(1998), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{1f600}".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[a [", &"\u{1f600}".repeat(1998), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p>[a <a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{1f600}".repeat(1998),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"\u{1f600}".repeat(1999), "](https://example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{1f600}".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[", &"\u{1f600}".repeat(1999), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p><a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{1f600}".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["[a [", &"\u{1f600}".repeat(1999), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(
            [
                "<p>[a <a href=\"mailto:ada@example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">",
                &"\u{1f600}".repeat(1999),
                "</a> <em>x</em></p>"
            ]
            .concat()
        )
    );
    assert_eq!(render(&["[", &"\u{1f600}".repeat(2000), "](https://example.test) *x*"].concat(), &set(&[])), Some(["<p>[", &"\u{1f600}".repeat(2000), "](<a href=\"https://example.test\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://example.test</a>) <em>x</em></p>"].concat()));
    assert_eq!(
        render(&["[", &"\u{1f600}".repeat(2000), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(["<p>[", &"\u{1f600}".repeat(2000), "](mailto:ada@example.test) <em>x</em></p>"].concat())
    );
    assert_eq!(
        render(&["[a [", &"\u{1f600}".repeat(2000), "](mailto:ada@example.test) *x*"].concat(), &set(&[])),
        Some(["<p>[a [", &"\u{1f600}".repeat(2000), "](mailto:ada@example.test) <em>x</em></p>"].concat())
    );
    assert_eq!(
        render(&["*x* <https:", &"\u{1f600}".repeat(2047), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https:",
                &"\u{1f600}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https:",
                &"\u{1f600}".repeat(2047),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <mailto:", &"\u{1f600}".repeat(2047), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"mailto:",
                &"\u{1f600}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">mailto:",
                &"\u{1f600}".repeat(2047),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{1f600}".repeat(2047), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{1f600}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{1f600}".repeat(2047),
                "</a> after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{1f600}".repeat(2047), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{1f600}".repeat(2047),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{1f600}".repeat(2047),
                "</a>).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <https:", &"\u{1f600}".repeat(2048), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https:",
                &"\u{1f600}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https:",
                &"\u{1f600}".repeat(2048),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <mailto:", &"\u{1f600}".repeat(2048), ">"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"mailto:",
                &"\u{1f600}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">mailto:",
                &"\u{1f600}".repeat(2048),
                "</a></p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{1f600}".repeat(2048), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{1f600}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{1f600}".repeat(2048),
                "</a> after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{1f600}".repeat(2048), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{1f600}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{1f600}".repeat(2048),
                "</a>).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* <https:", &"\u{1f600}".repeat(2049), ">"].concat(), &set(&[])),
        Some(["<p><em>x</em> &lt;https:", &"\u{1f600}".repeat(2049), "&gt;</p>"].concat())
    );
    assert_eq!(
        render(&["*x* <mailto:", &"\u{1f600}".repeat(2049), ">"].concat(), &set(&[])),
        Some(["<p><em>x</em> &lt;mailto:", &"\u{1f600}".repeat(2049), "&gt;</p>"].concat())
    );
    assert_eq!(
        render(&["*x* https://", &"\u{1f600}".repeat(2049), " after"].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{1f600}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{1f600}".repeat(2048),
                "</a>\u{1f600} after</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["*x* https://", &"\u{1f600}".repeat(2049), ")."].concat(), &set(&[])),
        Some(
            [
                "<p><em>x</em> <a href=\"https://",
                &"\u{1f600}".repeat(2048),
                "\" rel=\"noopener noreferrer nofollow\" target=\"_blank\">https://",
                &"\u{1f600}".repeat(2048),
                "</a>\u{1f600}).</p>"
            ]
            .concat()
        )
    );
    assert_eq!(
        render(&["![", &"\u{1f600}".repeat(299), "](cid:a1)"].concat(), &set(&["a1"])),
        Some(["<p><img src=\"cid:a1@mailmind\" alt=\"", &"\u{1f600}".repeat(299), "\"/></p>"].concat())
    );
    assert_eq!(
        render(&["![", &"\u{1f600}".repeat(300), "](cid:a1)"].concat(), &set(&["a1"])),
        Some(["<p><img src=\"cid:a1@mailmind\" alt=\"", &"\u{1f600}".repeat(300), "\"/></p>"].concat())
    );
    assert_eq!(render(&["![", &"\u{1f600}".repeat(301), "](cid:a1)"].concat(), &set(&["a1"])), None);
}

/// Generated text, and what the Python app's `markdown.render` and `plain_pictures` answered for it
/// (`fixtures/markdown_oracle.json`).
#[test]
fn generated_text_renders_as_the_python_app_renders_it() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/markdown_oracle.json");
    let oracle: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let mut wrong = Vec::new();
    let cases = oracle["render"].as_array().unwrap();
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let got = render(text, &strings(&case["pictures"]));
        if got.as_deref() != case["html"].as_str() {
            wrong.push(format!("{text:?}\n  python: {:?}\n  rust:   {got:?}", case["html"].as_str()));
        }
    }
    for case in oracle["plain_pictures"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        let got = plain_pictures(text, &strings(&case["shown"]));
        if got != case["plain"].as_str().unwrap() {
            wrong.push(format!("plain {text:?}\n  python: {:?}\n  rust:   {got:?}", case["plain"]));
        }
    }
    assert!(cases.len() >= 300);
    assert!(
        wrong.is_empty(),
        "{} differ:\n{}",
        wrong.len(),
        wrong.iter().take(20).cloned().collect::<Vec<_>>().join("\n")
    );
}

#[test]
fn a_token_and_its_content_id() {
    assert!(is_token("a1") && is_token("A-b_9") && is_token(&"x".repeat(64)));
    assert!(!is_token("") && !is_token(&"x".repeat(65)) && !is_token("a b") && !is_token("a.b") && !is_token("\u{e9}"));
    assert_eq!(content_id("a1"), "a1@mailmind");
    assert_eq!(token_of(Some("a1@mailmind")).as_deref(), Some("a1"));
    assert_eq!(token_of(Some("@mailmind")), None);
    assert_eq!(token_of(Some("a.1@mailmind")), None);
    assert_eq!(token_of(Some("a1@example.test")), None);
    assert_eq!(token_of(Some("")), None);
    assert_eq!(token_of(None), None);
}

#[test]
fn nothing_past_the_cap_is_rendered() {
    assert_eq!(md(&"**bold** ".repeat(MAX_TEXT)), None);
    // The cap counts characters, as Python's `len` does, not bytes. What is at it is rendered, as the Python app
    // renders it.
    for c in ["x", "\u{e9}", "\u{1f600}"] {
        let words = c.repeat(MAX_TEXT - 4);
        assert_eq!(md(&format!("*a* {words}")), Some(format!("<p><em>a</em> {words}</p>")));
        assert_eq!(md(&format!("*a* {words}{c}")), None);
    }
}

/// How long rendering `text` `times` over takes, the least of three tries.
fn time_to_render(text: &str, times: usize) -> std::time::Duration {
    (0..3)
        .map(|_| {
            let began = std::time::Instant::now();
            for _ in 0..times {
                std::hint::black_box(md(text));
            }
            began.elapsed()
        })
        .min()
        .unwrap()
}

/// Text eight times as long takes about as long as eight of the short one, where a quadratic would take eight times
/// that. The long one is as long as the renderer takes, so a blowup shows where it is largest.
#[test]
fn the_time_it_takes_follows_the_length_of_the_text() {
    let units = [
        "*a ",
        "[",
        "`",
        "**",
        "> ",
        "- ",
        "[a](",
        "*a* ",
        "_a_b ",
        "<",
        "\\",
        "https:// ",
        "```\n",
        "\n",
        "~~~\n~",
        "[\u{e9}](",
        "\u{e9}*",
        "[a](https://example.test/",
    ];
    for unit in units {
        let n = (MAX_TEXT - 1) / (8 * unit.chars().count());
        let (short, long) = (unit.repeat(n), unit.repeat(8 * n));
        let (one, eight) = (time_to_render(&long, 1), time_to_render(&short, 8));
        assert!(one < eight * 3, "{unit:?}: {one:?} against {eight:?}");
    }
}
