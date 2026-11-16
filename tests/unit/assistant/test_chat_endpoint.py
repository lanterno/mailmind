"""Where a service that speaks OpenAI's Chat Completions is (mailmind.assistant.adapters.openai_chat, #49): its base URL read
as an endpoint, at the scheme's port where it names none, an IPv6 address among them; and nothing asked of one that names no
API. Every address is fictional, and no connection is made."""
import socket

import pytest

from mailmind.assistant.adapters.openai_chat import Endpoint, Http, endpoint
from mailmind.assistant.application.ports import ModelError


@pytest.mark.parametrize("url, end", [
    ("http://[::1]/v1", Endpoint(False, "::1", 80, "/v1")),
    ("http://[::1]:11434/v1/", Endpoint(False, "::1", 11434, "/v1")),
    ("https://[2001:db8::5]/v1", Endpoint(True, "2001:db8::5", 443, "/v1")),
    ("https://[fe80::abcd]/v1", Endpoint(True, "fe80::abcd", 443, "/v1")),
    ("http://127.0.0.1:1234/v1", Endpoint(False, "127.0.0.1", 1234, "/v1")),
    ("HTTPS://LLM.Example.test/v1beta/openai?x=1#y", Endpoint(True, "llm.example.test", 443, "/v1beta/openai")),
    ("  https://llm.example.test  ", Endpoint(True, "llm.example.test", 443, "")),
])
def test_a_base_url_is_where_its_api_is_at_its_schemes_port_where_it_names_none(url, end):
    assert endpoint(url) == end


@pytest.mark.parametrize("url", [
    "", "llm.example.test/v1", "ftp://llm.example.test/v1", "https:///v1", "https://user@llm.example.test/v1",
    "http://127.0.0.1:/v1", "http://[::1]:/v1", "http://127.0.0.1:0/v1", "http://127.0.0.1:65536/v1",
    "http://127.0.0.1:99999/v1", "http://127.0.0.1:port/v1", "http://[::1/v1", "https://llm.example.test/v 1",
    "https://llm.example.test/v1\r\nX-Other: 1", "https://llm.example.test/\x00v1",
])
def test_a_url_that_names_no_api_is_no_endpoint_and_nothing_is_asked_of_it(url, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("connected"))
    assert endpoint(url) is None
    with pytest.raises(ModelError) as e:
        Http("compatible", url, "").send("GET", "/models")
    assert (str(e.value), e.value.status) == ("compatible unreachable: no API address", None)


@pytest.mark.parametrize("url, address", [("http://[::1]/v1", ("::1", 80)), ("https://[2001:db8::5]/v1", ("2001:db8::5", 443)),
                                          ("http://[::1]:11434/v1", ("::1", 11434))])
def test_an_ipv6_address_is_connected_to_whole_at_its_port(url, address, monkeypatch):
    asked = []

    def connecting(where, *args, **kwargs):
        asked.append(where)
        raise ConnectionRefusedError
    monkeypatch.setattr(socket, "create_connection", connecting)
    with pytest.raises(ModelError, match="^local unreachable: connection refused$"):
        Http("local", url, "").send("GET", "/models")
    assert asked == [address]
