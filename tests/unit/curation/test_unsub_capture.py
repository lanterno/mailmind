"""Cleanup can offer an unsubscribe for any sender. Ingest keeps a message's unsubscribe headers, mail
from before that has them read off the server on demand, and what was read outlives the sender's mail,
so trying again after the sweep still knows the way."""

from mailmind.curation.domain.unsubscribe import parse_list_unsub


def test_a_url_folded_in_the_middle_comes_back_whole():
    # RFC 2369 says whitespace within one is ignored; a space or a line break left in it can't be requested.
    r = parse_list_unsub(b"List-Unsubscribe: <https://list.example.test/u?t=abc\r\n def>,\r\n"
                                 b" <mailto:out@list.example.test?subject=\r\n stop>\r\n\r\nHello.\r\n")
    assert (r["https_uri"], r["mailto_to"], r["mailto_subject"]) == (
        "https://list.example.test/u?t=abcdef", "out@list.example.test", "stop")
