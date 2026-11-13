//! A reply's body and a forward's, the text an HTML original reads as, and the quote's helpers, each as the Python
//! app writes it.

use mm_compose::quote::{html_escape, own_html, quote_tail, quoted_text_html};
use mm_compose::replies::{Original, forward_bodies, plain_from_html, reply_bodies};

/// An original with the fields named.
fn original(fields: &[(&str, &str)]) -> Original {
    let mut orig = Original::default();
    for (name, value) in fields {
        let value = Some((*value).to_owned());
        match *name {
            "from_name" => orig.from_name = value,
            "from_addr" => orig.from_addr = value,
            "subject" => orig.subject = value,
            "date" => orig.date = value,
            "to" => orig.to = value,
            "cc" => orig.cc = value,
            "text" => orig.text = value,
            "html" => orig.html = value,
            _ => panic!("no field {name}"),
        }
    }
    orig
}

// Each expected value below is what the Python app's `reply_bodies`, `forward_bodies`, `plain_from_html` or
// `content.quote` gives.

#[test]
fn replies_and_forwards_are_written_as_the_python_app_writes_them() {
    // a text only original
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("text", "Hello\nthere\n"),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hello\n> there\n".to_owned(), None));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHello\nthere\n".to_owned(), None));
    assert_eq!(
        reply_bodies("", &orig),
        (
            "\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hello\n> there\n"
                .to_owned(),
            None
        )
    );
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHello\nthere\n".to_owned(), None));
    // an html only original
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        (
            "html",
            "<p>Hello <b>there</b> &amp; you &copy; &lt;b&gt; &#65;</p><p>Second<br>line</p><script>x()</script><BR >end",
        ),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hello there & you \u{a9} <b> A\n> \n> Second\n> line\n> \n> \n> end\n".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hello <b>there</b> &amp; you \u{a9} &lt;b&gt; A</p><p>Second<br/>line</p><br/>end</blockquote>".to_owned())));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHello there & you \u{a9} <b> A\n\nSecond\nline\n\n\nend".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>---------- Forwarded message ----------<br>From: Ada Lovelace &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: Plans<br>To: lin@example.test</div><hr><p>Hello <b>there</b> &amp; you \u{a9} &lt;b&gt; A</p><p>Second<br/>line</p><br/>end".to_owned())));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hello there & you \u{a9} <b> A\n> \n> Second\n> line\n> \n> \n> end\n".to_owned(), Some("<div></div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hello <b>there</b> &amp; you \u{a9} &lt;b&gt; A</p><p>Second<br/>line</p><br/>end</blockquote>".to_owned())));
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHello there & you \u{a9} <b> A\n\nSecond\nline\n\n\nend".to_owned(), Some("<div></div><br><div>---------- Forwarded message ----------<br>From: Ada Lovelace &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: Plans<br>To: lin@example.test</div><hr><p>Hello <b>there</b> &amp; you \u{a9} &lt;b&gt; A</p><p>Second<br/>line</p><br/>end".to_owned())));
    // an original with text and html
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("text", "Hello there"),
        ("html", "<p>Hello <i>there</i></p>"),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hello there\n".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hello <i>there</i></p></blockquote>".to_owned())));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHello there".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>---------- Forwarded message ----------<br>From: Ada Lovelace &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: Plans<br>To: lin@example.test</div><hr><p>Hello <i>there</i></p>".to_owned())));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hello there\n".to_owned(), Some("<div></div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hello <i>there</i></p></blockquote>".to_owned())));
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHello there".to_owned(), Some("<div></div><br><div>---------- Forwarded message ----------<br>From: Ada Lovelace &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: Plans<br>To: lin@example.test</div><hr><p>Hello <i>there</i></p>".to_owned())));
    // an original with no date
    let orig = original(&[("from_name", "Ada Lovelace"), ("from_addr", "ada@example.test"), ("text", "Hi")]);
    assert_eq!(
        reply_bodies("Sounds good.\nSee you & <them>", &orig),
        ("Sounds good.\nSee you & <them>\n\nOn , Ada Lovelace <ada@example.test> wrote:\n> Hi\n".to_owned(), None)
    );
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\n\nHi".to_owned(), None));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn , Ada Lovelace <ada@example.test> wrote:\n> Hi\n".to_owned(), None));
    assert_eq!(
        forward_bodies("", &orig),
        ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\n\nHi".to_owned(), None)
    );
    // a name that is the address
    let orig = original(&[
        ("from_name", "ada@example.test"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("text", "Hi"),
    ]);
    assert_eq!(
        reply_bodies("Sounds good.\nSee you & <them>", &orig),
        (
            "Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, ada@example.test wrote:\n> Hi\n"
                .to_owned(),
            None
        )
    );
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: ada@example.test <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHi".to_owned(), None));
    assert_eq!(
        reply_bodies("", &orig),
        ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, ada@example.test wrote:\n> Hi\n".to_owned(), None)
    );
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: ada@example.test <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\nHi".to_owned(), None));
    // an original from nobody known
    let orig = original(&[("text", "Hi"), ("subject", ""), ("date", "")]);
    assert_eq!(
        reply_bodies("Sounds good.\nSee you & <them>", &orig),
        ("Sounds good.\nSee you & <them>\n\nOn , someone wrote:\n> Hi\n".to_owned(), None)
    );
    assert_eq!(
        forward_bodies("Sounds good.\nSee you & <them>", &orig),
        ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: <>\n\nHi".to_owned(), None)
    );
    assert_eq!(reply_bodies("", &orig), ("\n\nOn , someone wrote:\n> Hi\n".to_owned(), None));
    assert_eq!(
        forward_bodies("", &orig),
        ("\n\n---------- Forwarded message ----------\nFrom: <>\n\nHi".to_owned(), None)
    );
    // an address and no name
    let orig = original(&[("from_addr", "ada@example.test"), ("date", "today"), ("text", "Hi")]);
    assert_eq!(
        reply_bodies("Sounds good.\nSee you & <them>", &orig),
        ("Sounds good.\nSee you & <them>\n\nOn today, ada@example.test wrote:\n> Hi\n".to_owned(), None)
    );
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: <ada@example.test>\nDate: today\n\nHi".to_owned(), None));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn today, ada@example.test wrote:\n> Hi\n".to_owned(), None));
    assert_eq!(
        forward_bodies("", &orig),
        ("\n\n---------- Forwarded message ----------\nFrom: <ada@example.test>\nDate: today\n\nHi".to_owned(), None)
    );
    // line ends of every kind
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("text", "a\rb\r\nc\u{2028}d\u{b}e\u{c}f\u{1c}g\u{1d}h\u{1e}i\u{85}j\u{2029}k\n\nl\n"),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> a\n> b\n> c\n> d\n> e\n> f\n> g\n> h\n> i\n> j\n> k\n> \n> l\n".to_owned(), None));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\na\rb\r\nc\u{2028}d\u{b}e\u{c}f\u{1c}g\u{1d}h\u{1e}i\u{85}j\u{2029}k\n\nl\n".to_owned(), None));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> a\n> b\n> c\n> d\n> e\n> f\n> g\n> h\n> i\n> j\n> k\n> \n> l\n".to_owned(), None));
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\na\rb\r\nc\u{2028}d\u{b}e\u{c}f\u{1c}g\u{1d}h\u{1e}i\u{85}j\u{2029}k\n\nl\n".to_owned(), None));
    // an original with no words
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("text", ""),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n>\n".to_owned(), None));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\n".to_owned(), None));
    assert_eq!(
        reply_bodies("", &orig),
        ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n>\n".to_owned(), None)
    );
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\n".to_owned(), None));
    // an empty html part
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("text", ""),
        ("html", ""),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n>\n".to_owned(), None));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\n".to_owned(), None));
    assert_eq!(
        reply_bodies("", &orig),
        ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n>\n".to_owned(), None)
    );
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\n".to_owned(), None));
    // a carbon copy and no one addressed
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", ""),
        ("cc", "cy@example.test, di@example.test"),
        ("text", "Hi"),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n".to_owned(), None));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nCc: cy@example.test, di@example.test\n\nHi".to_owned(), None));
    assert_eq!(
        reply_bodies("", &orig),
        ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n".to_owned(), None)
    );
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nCc: cy@example.test, di@example.test\n\nHi".to_owned(), None));
    // an html original that says nothing
    let orig = original(&[
        ("from_name", "Ada Lovelace"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "Plans"),
        ("to", "lin@example.test"),
        ("html", "<script>x()</script>  "),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n>\n".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">  </blockquote>".to_owned())));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\n".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>---------- Forwarded message ----------<br>From: Ada Lovelace &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: Plans<br>To: lin@example.test</div><hr>  ".to_owned())));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n>\n".to_owned(), Some("<div></div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">  </blockquote>".to_owned())));
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada Lovelace <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: Plans\nTo: lin@example.test\n\n".to_owned(), Some("<div></div><br><div>---------- Forwarded message ----------<br>From: Ada Lovelace &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: Plans<br>To: lin@example.test</div><hr>  ".to_owned())));
    // an original with markup in its headers
    let orig = original(&[
        ("from_name", "Ada <\"the\" & 'one'>"),
        ("from_addr", "ada@example.test"),
        ("date", "Mon, 2 Nov 2026 10:00:00 +0100"),
        ("subject", "<b>&"),
        ("to", "lin@example.test"),
        ("html", "<div onclick=\"x()\">hi</div>"),
    ]);
    assert_eq!(reply_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada <\"the\" & 'one'> <ada@example.test> wrote:\n> hi\n".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada &lt;&quot;the&quot; &amp; &#x27;one&#x27;&gt; &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><div>hi</div></blockquote>".to_owned())));
    assert_eq!(forward_bodies("Sounds good.\nSee you & <them>", &orig), ("Sounds good.\nSee you & <them>\n\n---------- Forwarded message ----------\nFrom: Ada <\"the\" & 'one'> <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: <b>&\nTo: lin@example.test\n\nhi".to_owned(), Some("<div>Sounds good.<br>See you &amp; &lt;them&gt;</div><br><div>---------- Forwarded message ----------<br>From: Ada &lt;&quot;the&quot; &amp; &#x27;one&#x27;&gt; &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: &lt;b&gt;&amp;<br>To: lin@example.test</div><hr><div>hi</div>".to_owned())));
    assert_eq!(reply_bodies("", &orig), ("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada <\"the\" & 'one'> <ada@example.test> wrote:\n> hi\n".to_owned(), Some("<div></div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada &lt;&quot;the&quot; &amp; &#x27;one&#x27;&gt; &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><div>hi</div></blockquote>".to_owned())));
    assert_eq!(forward_bodies("", &orig), ("\n\n---------- Forwarded message ----------\nFrom: Ada <\"the\" & 'one'> <ada@example.test>\nDate: Mon, 2 Nov 2026 10:00:00 +0100\nSubject: <b>&\nTo: lin@example.test\n\nhi".to_owned(), Some("<div></div><br><div>---------- Forwarded message ----------<br>From: Ada &lt;&quot;the&quot; &amp; &#x27;one&#x27;&gt; &lt;ada@example.test&gt;<br>Date: Mon, 2 Nov 2026 10:00:00 +0100<br>Subject: &lt;b&gt;&amp;<br>To: lin@example.test</div><hr><div>hi</div>".to_owned())));
}

#[test]
fn the_text_of_an_html_original() {
    assert_eq!(
        plain_from_html(
            "<p>Hello <b>there</b> &amp; you &copy; &lt;b&gt; &#65;</p><p>Second<br>line</p><script>x()</script><BR >end"
        ),
        "Hello there & you \u{a9} <b> A\n\nSecond\nline\n\n\nend"
    );
    assert_eq!(plain_from_html(""), "");
    assert_eq!(plain_from_html("  <p> </p> "), "");
    assert_eq!(plain_from_html("<p>a</p>\n<p>b</p>"), "a\n\n\nb");
    assert_eq!(plain_from_html("a<br/>b<br />c<BR>d</P >e"), "a\nb\nc\nd\n\ne");
    assert_eq!(plain_from_html("&amp;lt; &#x27; &quot;"), "&lt; ' \"");
    assert_eq!(plain_from_html("<a href=\"https://example.test/?a=1&amp;b=2\" title='x\"y'>link</a>"), "link");
    assert_eq!(plain_from_html("<p>\u{e9} \u{3000}</p>\u{2028}"), "\u{e9}");
    assert_eq!(plain_from_html("<style>p{color:red}</style>text"), "p{color:red}text");
    assert_eq!(plain_from_html("<blockquote>q</blockquote>after"), "qafter");
    assert_eq!(plain_from_html("<img src=\"cid:a1\" alt=\"pic\">x"), "x");
    assert_eq!(plain_from_html("<pre>  a\n    b</pre>"), "a\n    b");
    assert_eq!(
        plain_from_html("&#0;&#13;&#128;&#x9f;&#xD800;&#1114112;&#x7f;&#xFFFE;"),
        "\u{fffd}\r\u{20ac}\u{178}\u{fffd}\u{fffd}\u{7f}\u{fffe}"
    );
}

#[test]
fn the_quote_helpers() {
    assert_eq!(html_escape("a&b<c>\"d'e"), "a&amp;b&lt;c&gt;&quot;d&#x27;e");
    assert_eq!(html_escape(""), "");
    assert_eq!(html_escape("\u{e9}"), "\u{e9}");
    assert_eq!(own_html("line1\nline2 & <x>"), "<div>line1<br>line2 &amp; &lt;x&gt;</div><br>");
    assert_eq!(own_html(""), "<div></div><br>");
    assert_eq!(own_html("\n"), "<div><br></div><br>");
    assert_eq!(
        quoted_text_html("\n> a\n> b\nplain\n>c\n\n"),
        "<blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">a<br>b</blockquote><div>plain</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">c</blockquote>"
    );
    assert_eq!(quoted_text_html(""), "<div></div>");
    assert_eq!(quoted_text_html("no quote"), "<div>no quote</div>");
    assert_eq!(
        quoted_text_html("> one"),
        "<blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">one</blockquote>"
    );
    assert_eq!(
        quoted_text_html(">\n>\n"),
        "<blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><br></blockquote>"
    );
    assert_eq!(quoted_text_html("a\n\nb"), "<div>a<br><br>b</div>");
    assert_eq!(
        quoted_text_html(">> two\n> one\nnone & <x>"),
        "<blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\">&gt; two<br>one</blockquote><div>none &amp; &lt;x&gt;</div>"
    );
    assert_eq!(quote_tail("Thanks", Some("Thanks\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n> there\n"), Some("<div>Thanks</div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hi</p></blockquote>")), (Some("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n> there\n".to_owned()), Some("<div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hi</p></blockquote>".to_owned())));
    assert_eq!(quote_tail("", Some("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n> there\n"), Some("<div></div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hi</p></blockquote>")), (Some("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n> there\n".to_owned()), Some("<div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hi</p></blockquote>".to_owned())));
    assert_eq!(quote_tail("Two\nlines", Some("Two\nlines\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n> there\n"), Some("<div>Two<br>lines</div><br><div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hi</p></blockquote>")), (Some("\n\nOn Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace <ada@example.test> wrote:\n> Hi\n> there\n".to_owned()), Some("<div>On Mon, 2 Nov 2026 10:00:00 +0100, Ada Lovelace &lt;ada@example.test&gt; wrote:</div><blockquote type=\"cite\" style=\"margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex\"><p>Hi</p></blockquote>".to_owned())));
    assert_eq!(quote_tail("Thanks", Some("Other"), Some("<div>Other</div><br>x")), (None, None));
    assert_eq!(quote_tail("Thanks", Some("Thanks"), Some("<div>Thanks</div><br>")), (None, None));
    assert_eq!(quote_tail("Thanks", None, None), (None, None));
    assert_eq!(quote_tail("Thanks", Some("Thanks\n> q"), Some("")), (Some("\n> q".to_owned()), None));
}
