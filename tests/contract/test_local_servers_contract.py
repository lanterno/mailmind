"""Asking the model servers on this Mac for their models over HTTP (mailmind.assistant.adapters.local_servers, #49):
fake OpenAI-compatible APIs on loopback stand in for them, and a port nothing listens on for one that isn't running."""
import socket
import time

import pytest

from fakes import openai_api as fo
from mailmind.assistant.adapters import local_servers


@pytest.fixture
def two():
    with fo.FakeOpenAI(key=None) as one, fo.FakeOpenAI(key=None) as other:
        yield one, other


def nothing_at():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}/v1"


def test_each_server_that_answers_is_listed_with_what_it_names_and_is_sent_no_key(two):
    one, other = two
    one.models, other.models = ["qwen3:8b", "nomic-embed-text"], []
    nowhere = nothing_at()
    assert local_servers.listing([one.url, nowhere, other.url]) == {one.url: ["qwen3:8b", "nomic-embed-text"],
                                                                     other.url: []}
    for fake in two:
        [req] = fake.requests
        assert (req.method, req.path) == ("GET", "/models") and "authorization" not in req.headers


@pytest.mark.parametrize("answer", [
    (200, {"models": [{"name": "llama3.3"}]}),               # not a list of models in OpenAI's form
    (200, {"data": "llama3.3"}),
    (200, ["llama3.3"]),
    (404, {"error": "not found"}),
    (500, {"data": [{"id": "llama3.3"}]}),
    (401, {"error": {"message": "a key is needed"}}),
])
def test_a_server_that_doesnt_answer_a_list_of_models_is_left_out(two, answer):
    one, other = two
    one.queue(answer)
    assert local_servers.listing([one.url, other.url]) == {other.url: list(fo.MODELS)}


def test_a_server_that_answers_what_isnt_json_is_left_out(two):
    one, _ = two
    one.queue(lambda req: (200, "not json"))                # the fake JSON-encodes it: a string, not an object
    assert local_servers.listing([one.url]) == {}


def test_each_is_waited_for_a_second_at_most_and_all_at_once(two):
    one, other = two
    slow = lambda req: (time.sleep(3), (200, {"data": [{"id": "late"}]}))[1]
    one.queue(slow)
    other.queue(slow)
    began = time.monotonic()
    assert local_servers.listing([one.url, other.url]) == {}
    assert time.monotonic() - began < 2.5


def test_asking_none_asks_nothing():
    assert local_servers.listing([]) == {}

