"""What of a message's inline images its frame may hold (mailmind.mailbox.domain.inline_images): values in and values out."""
import base64

import pytest

from mailmind.mailbox.domain import inline_images

PNG = b"\x89PNG-bytes"
LOGO = '<img src="cid:logo">'


def data_uri(ctype, data):
    return f'<img src="data:{ctype};base64,{base64.b64encode(data).decode()}">'


@pytest.mark.parametrize("ctype", sorted(inline_images.RASTER))
def test_a_raster_image_is_embedded(ctype):
    assert inline_images.embed(LOGO, {"logo": (ctype, PNG)}) == data_uri(ctype, PNG)


@pytest.mark.parametrize("ctype", ["image/svg+xml", "text/html", "application/javascript"])
def test_active_content_is_never_embedded(ctype):
    assert inline_images.embed(LOGO, {"logo": (ctype, PNG)}) == LOGO


def test_a_reference_to_no_part_stays_as_it_is():
    assert inline_images.embed(LOGO, {"other": ("image/png", PNG)}) == LOGO


def test_a_content_id_in_angle_brackets_finds_its_part():
    assert inline_images.embed('<img src="cid:<logo>">', {"logo": ("image/png", PNG)}) == data_uri("image/png", PNG)


def test_single_quotes_stay_single():
    assert inline_images.embed("<img src='cid:logo'>", {"logo": ("image/png", b"x")}).startswith("<img src='data:image/png;base64,")


def test_the_attribute_matches_whatever_its_case_and_spacing():
    assert "data:image/png" in inline_images.embed('<IMG SRC = "CID:logo">', {"logo": ("image/png", PNG)})


def test_nothing_past_the_budget_is_embedded():
    half = b"x" * (inline_images.BUDGET // 2 + 1)
    html = '<img src="cid:a"><img src="cid:b">'
    out = inline_images.embed(html, {"a": ("image/png", half), "b": ("image/png", half)})
    assert out.count("data:image/png") == 1 and 'src="cid:b"' in out


def test_an_image_that_fills_the_budget_exactly_is_embedded():
    whole = b"x" * inline_images.BUDGET
    assert "data:" in inline_images.embed(LOGO, {"logo": ("image/png", whole)})
