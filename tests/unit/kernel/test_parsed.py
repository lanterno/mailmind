"""mailmind.kernel.parsed: a number a request holds reads as one, or it is none and a route that needs it refuses with the 400 a
malformed request gets. tests/api/test_http_malformed_input.py holds every route to that answer, on both servers."""
import pytest

from mailmind.kernel.errors import Refused
from mailmind.kernel.parsed import as_int, required_int


@pytest.mark.parametrize("held, read", [(7, 7), ("7", 7), (True, 1), (1.5, 1), (2 ** 53, 2 ** 53), (-(2 ** 53), -(2 ** 53)),
                                        (2 ** 53 + 1, None), (10 ** 30, None), (float("inf"), None), (float("nan"), None),
                                        ("x", None), ("", None), (None, None), ([1], None), ({}, None)])
def test_a_number_reads_or_it_is_none(held, read):
    assert as_int(held) == read
    if read is None:
        with pytest.raises(Refused):
            required_int(held)
    else:
        assert required_int(held) == read
