"""What a person's Markdown comes to, and what stays plain text."""
import gc
import time

import pytest

from mailmind.compose.domain import markdown


def p(inner):
    return f"<p>{inner}</p>"


# -- what is no Markdown goes out as it does today -------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "",
    None,
    "Hi Lin,\n\nThanks for the note.\n\n-- \nAda",
    "See https://example.test/a_b_c for more",
    "Write to ada@example.test or lin@example.test",
    "3 * 4 * 5 = 60, and 2*3*4 is less",
    "snake_case_name and my_var_ and _private",
    "Price* (*see below)",
    "#hashtag and #another",
    "C:\\Users\\ada\\*.txt",
    "1985. A vintage year",
    "A list of one [1] and [two] things",
    "```\nnever closed",
    "An <em>angle</em> of a tag",
    "> On Tuesday, Lin wrote:\n> Shall we meet?\n\nYes.",
])
def test_text_with_no_markdown_in_it_renders_to_nothing(text):
    if text == "1985. A vintage year":
        text = "It was\n" + text              # at the head of a block it is a list, in a paragraph a sentence
    assert markdown.render(text) is None


def test_nothing_past_the_cap_is_rendered():
    assert markdown.render("**bold** " * markdown.MAX_TEXT) is None


# -- spans -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, html", [
    ("**strong**", "<strong>strong</strong>"),
    ("__strong__", "<strong>strong</strong>"),
    ("*em*", "<em>em</em>"),
    ("_em_", "<em>em</em>"),
    ("***both***", "<strong><em>both</em></strong>"),
    ("**with *inner* em**", "<strong>with <em>inner</em> em</strong>"),
    ("`code`", "<code>code</code>"),
    ("``a ` b``", "<code>a ` b</code>"),
    ("` *not em* `", "<code>*not em*</code>"),
    ("\\*not em\\* but *em*", "*not em* but <em>em</em>"),
])
def test_spans(typed, html):
    assert markdown.render(typed) == p(html)


def test_a_line_break_stays_one():
    assert markdown.render("*Hi*\nthere") == p("<em>Hi</em><br/>\nthere")


def test_a_link_keeps_its_target_and_is_marked_as_an_outside_one():
    assert markdown.render("[the plan](https://example.test/a_(b))") == p(
        '<a href="https://example.test/a_(b)" rel="noopener noreferrer nofollow" target="_blank">the plan</a>')


def test_a_mailto_link():
    assert 'href="mailto:lin@example.test"' in markdown.render("[Lin](mailto:lin@example.test)")


def test_a_title_on_a_link_is_read_past():
    assert 'href="https://example.test"' in markdown.render('[x](https://example.test "A title")')


@pytest.mark.parametrize("typed", ["[x](javascript:alert(1))", "[x](data:text/html,hi)", "[x](/relative)", "[x](vbscript:y)"])
def test_a_link_to_anything_else_stays_as_typed(typed):
    assert markdown.render(f"{typed} *em*") == p(f"{typed.replace('&', '&amp;')} <em>em</em>")


def test_a_bare_address_is_a_link_only_in_a_message_that_has_html_anyway():
    assert markdown.render("see https://example.test/a_b.") is None
    assert markdown.render("see https://example.test/a_b. *now*") == p(
        'see <a href="https://example.test/a_b" rel="noopener noreferrer nofollow" target="_blank">https://example.test/a_b</a>. <em>now</em>')


def test_an_angled_address_is_a_link():
    assert '<a href="https://example.test"' in markdown.render("*a* <https://example.test>")


def test_no_tag_a_person_typed_gets_through():
    html = markdown.render('*x* <script>alert(1)</script> <img src=x onerror=alert(1)> <a href="javascript:y">z</a>')
    assert "<script" not in html and "<img" not in html and "<a " not in html
    assert "&lt;script&gt;" in html


def test_an_ampersand_is_escaped():
    assert markdown.render("*a & b*") == p("<em>a &amp; b</em>")


def test_a_link_can_hold_emphasis():
    assert ">a <em>word</em></a>" in markdown.render("[a *word*](https://example.test/a)")


def test_no_link_goes_inside_another():
    html = markdown.render("[outer [inner](https://example.test/a)](https://example.test/b)")
    assert all("<a " not in opened.partition("</a>")[0] for opened in html.split("<a ")[1:])


# -- blocks ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, html", [
    ("# One", "<h1>One</h1>"),
    ("###### Six", "<h6>Six</h6>"),
    ("## Closed ##", "<h2>Closed</h2>"),
    ("# C# and F#", "<h1>C# and F#</h1>"),
])
def test_headings(typed, html):
    assert markdown.render(typed) == html


def test_paragraphs():
    assert markdown.render("one *x*\n\ntwo") == p("one <em>x</em>") + "\n" + p("two")


def test_a_list():
    assert markdown.render("- one\n* two\n+ three") == "<ul><li>one</li><li>two</li><li>three</li></ul>"


def test_a_numbered_list():
    assert markdown.render("1. one\n2) two") == "<ol><li>one</li><li>two</li></ol>"


def test_a_numbered_list_keeps_where_it_starts():
    assert markdown.render("3. three\n4. four").startswith('<ol start="3">')


def test_a_list_goes_on_across_blank_lines_and_a_list_breaks_a_paragraph():
    assert markdown.render("Things:\n- one\n\n- two") == p("Things:") + "\n<ul><li>one</li><li>two</li></ul>"


def test_a_nested_list():
    assert markdown.render("- one\n  - inner\n- two") == "<ul><li>one\n<ul><li>inner</li></ul></li><li>two</li></ul>"


def test_a_list_item_with_a_paragraph_under_it():
    assert markdown.render("- a\n\n  more\n- b") == "<ul><li><p>a</p>\n<p>more</p></li><li>b</li></ul>"


def test_a_bullet_list_and_a_numbered_one_are_two_lists():
    assert markdown.render("- a\n1. b") == "<ul><li>a</li></ul>\n<ol><li>b</li></ol>"


def test_a_quote():
    assert markdown.render("> one\n> *two*\n\nafter") == "<blockquote><p>one<br/>\n<em>two</em></p></blockquote>\n" + p("after")


def test_a_quote_inside_a_quote():
    assert markdown.render("> > *deep*") == "<blockquote><blockquote><p><em>deep</em></p></blockquote></blockquote>"


def test_a_fenced_block_holds_its_text_as_written():
    assert markdown.render("```python\n*a* <b>\n\n  x\n```\nafter") == "<pre><code>*a* &lt;b&gt;\n\n  x</code></pre>\n" + p("after")


def test_a_fence_of_tildes_closed_by_a_longer_one():
    assert markdown.render("~~~\na\n~~~~") == "<pre><code>a</code></pre>"


def test_a_fence_is_closed_only_by_its_own_kind():
    assert markdown.render("```\na\n~~~") is None


def test_a_signature_under_markdown_keeps_its_lines():
    html = markdown.render("*Hi*\n\n-- \nAda\nBrightly Ltd")
    assert html.endswith(p("--<br/>\nAda<br/>\nBrightly Ltd"))


def test_a_list_can_be_nested_only_so_deep():
    html = markdown.render("> " * (markdown.MAX_DEPTH + 5) + "*x*")
    assert html.count("<blockquote>") == markdown.MAX_DEPTH


# -- what it comes to is what the sanitizer lets by, and in time -----------------------------------------------------

def test_what_comes_out_has_been_through_the_sanitizer():
    html = markdown.render("[a](https://example.test)")
    assert 'rel="noopener' in html                 # the sanitizer's own mark on a link


def cpu_time(text, times):
    """This thread's CPU time to render `text` `times` over, the least of three tries with the collector off: what other work on
    the machine does to it is left out."""
    least = float("inf")
    for _ in range(3):
        gc.disable()
        try:
            began = time.thread_time()
            for _ in range(times):
                markdown.render(text)
            least = min(least, time.thread_time() - began)
        finally:
            gc.enable()
    return least


@pytest.mark.parametrize("unit", [
    "*a ", "[", "`", "**", "> ", "- ", "[a](", "*a* ", "_a_b ", "<", "\\", "https:// ", "```\n", "\n",
])
def test_the_time_it_takes_follows_the_length_of_the_text(unit):
    """Text eight times as long takes about as long as eight of the short one, where a quadratic would take eight times that.
    The long one is as long as the renderer takes, so a blowup shows where it is largest."""
    n = (markdown.MAX_TEXT - 1) // (8 * len(unit))
    short, long = unit * n, unit * (8 * n)
    assert cpu_time(long, 1) < 3 * cpu_time(short, 8)
