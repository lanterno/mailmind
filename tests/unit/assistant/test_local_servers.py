"""Finding the model servers on this Mac (#49), for a person choosing a model on this Mac: the Catalog asks only the
ones on this Mac, in the order they are known, and lists those that answered, each with the models it lists that can chat.
The model servers here are stand-ins; tests/contract/test_local_servers_contract.py asks real ones over HTTP."""
from mailmind.assistant.application.catalog import Catalog
from mailmind.assistant.domain.providers import LOCAL_SERVERS, on_loopback


class Servers:
    def __init__(self, known, lists):
        self._known, self._lists, self.asked = known, lists, []

    def known(self):
        return self._known

    def listing(self, urls):
        self.asked.append(list(urls))
        return {url: self._lists[url] for url in urls if url in self._lists}


def catalog(servers):
    def never(*args):
        raise AssertionError("asked a provider")
    return Catalog(never, never, never, servers)


def test_the_servers_there_may_be_are_each_on_this_mac_at_its_own_port_in_the_order_offered():
    assert LOCAL_SERVERS == (("Ollama", "http://127.0.0.1:11434/v1"), ("LM Studio", "http://127.0.0.1:1234/v1"),
                             ("llama.cpp", "http://127.0.0.1:8080/v1"), ("vLLM", "http://127.0.0.1:8000/v1"),
                             ("Jan", "http://127.0.0.1:1337/v1"), ("GPT4All", "http://127.0.0.1:4891/v1"))
    assert all(on_loopback(url) for _, url in LOCAL_SERVERS)


def test_those_that_answered_are_listed_in_order_with_the_models_that_can_chat():
    servers = Servers([("Jan", "http://127.0.0.1:1337/v1"), ("Ollama", "http://localhost:11434/v1"),
                       ("vLLM", "http://[::1]:8000/v1")],
                      {"http://127.0.0.1:1337/v1": ["qwen3:8b", "nomic-embed-text", "llama3.3:latest", "qwen3:8b"],
                       "http://[::1]:8000/v1": []})
    assert catalog(servers).local_servers() == {"ok": True, "servers": [
        {"name": "Jan", "url": "http://127.0.0.1:1337/v1", "models": ["llama3.3:latest", "qwen3:8b"]},
        {"name": "vLLM", "url": "http://[::1]:8000/v1", "models": []}]}


def test_a_server_said_to_be_elsewhere_is_never_asked():
    servers = Servers([("Remote", "http://10.0.0.7:11434/v1"), ("Named", "https://llm.example.test/v1"),
                       ("Ollama", "http://127.0.0.1:11434/v1")], {"http://10.0.0.7:11434/v1": ["llama3.3"]})
    assert catalog(servers).local_servers() == {"ok": True, "servers": []}
    assert servers.asked == [["http://127.0.0.1:11434/v1"]]


def test_none_answering_is_no_servers():
    assert catalog(Servers([], {})).local_servers() == {"ok": True, "servers": []}
