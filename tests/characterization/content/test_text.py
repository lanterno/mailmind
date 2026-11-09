"""The text a body comes to for snippets and search: entities, invisible padding, what HTML hides, and the cap."""
from mailmind.content.text import body_text_of, html_to_snippet_text, snippet_of


def test_hwf_snippet_unescapes_entities_then_drops_invisible_padding():
    padding = "&zwnj;&nbsp;&#8203;\u200d\u2060\ufeff\u034f\u00ad\u200e\u200f" * 5
    assert snippet_of("Doors open at nine &amp; close at six" + padding + "\n\nSee   you there.") == \
        "Doors open at nine & close at six See you there."
    assert len(snippet_of("word " * 200)) == 280


HTML_WITH_NOISE = ("<html><head><title>Issue 12</title><style>.ReadMsgBody{width:100%}</style></head>"
                   "<body><script>var tracking = 1;</script><!-- a hidden note --><p>Hello &amp; welcome</p>"
                   "</body></html>")


def test_hwf_body_text_drops_the_contents_of_script_style_head_and_title():
    for text in (body_text_of({"text": None, "html": HTML_WITH_NOISE}),
                 html_to_snippet_text(HTML_WITH_NOISE)):
        assert "Hello & welcome" in text
        assert not any(s in text for s in ("ReadMsgBody", "tracking", "Issue 12", "hidden note")), text
    assert body_text_of({"text": "plain wins", "html": HTML_WITH_NOISE}) == "plain wins"


def test_hwf_body_text_strips_json_ld_blocks_twelve_at_most():
    block = '[{"@context": "https://schema.org", "@type": "Order", "items": [{"n": 1}]}]'
    assert body_text_of({"text": f"Order shipped. {block} Thanks."}).split() == ["Order", "shipped.", "Thanks."]
    many = body_text_of({"text": " ".join([block] * 13)})
    assert many.count("@context") == 1


def test_hwf_body_text_is_capped_at_100k_characters():
    assert len(body_text_of({"text": "x" * 200_000})) == 100_000
