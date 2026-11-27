"""The limits on an answer from an AI provider's API, each attempt an `exchange` (#49): a service that speaks OpenAI's Chat
Completions (mailmind.assistant.adapters.openai_chat), the Claude API through the Anthropic SDK (anthropic_sdk), and a model
server on this Mac the finder asks. An attempt's time limit is a deadline for all of it, however the peer trickles its answer
in, and an answer runs to MOST bytes at most. Peers on loopback send what no well-behaved server would."""
import json
import socket
import threading
import time

import pytest

from fakes import anthropic_api as fa
from fakes import openai_api as fo
from mailmind.assistant.adapters import exchange, local_servers
from mailmind.assistant.adapters.openai_chat import Http
from mailmind.assistant.application.ports import ModelError

HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"


class Peer:
    """A loopback server that reads each request and answers it with `answering(conn, stop)`, then closes the connection."""

    def __init__(self, answering):
        self._answering, self.stop = answering, threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self._sock.settimeout(0.05)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}/v1"
        self._thread = threading.Thread(target=self._serve, name="peer", daemon=True)

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn):
        with conn:
            try:
                asked = b""
                while b"\r\n\r\n" not in asked:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    asked += chunk
                headers, body = asked.split(b"\r\n\r\n", 1)
                length = next((int(h.split(b":", 1)[1]) for h in headers.splitlines()
                               if h.lower().startswith(b"content-length:")), 0)
                remaining = length - len(body)
                while remaining > 0:
                    chunk = conn.recv(min(4096, remaining))
                    if not chunk:
                        return
                    remaining -= len(chunk)
                self._answering(conn, self.stop)
            except OSError:
                pass                                    # the client let it go

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self._thread.join()
        self._sock.close()


GIVES_UP = 10                       # seconds a peer trickles at most, so a deadline missed fails its test, not hangs it


def trickling(head=HEAD + b"Content-Length: 100000\r\n\r\n", piece=b" "):
    """Send `head`, then `piece` every fifth of a second until stopped: never idle a second, never done in time."""
    def answering(conn, stop):
        conn.sendall(head)
        ends = time.monotonic() + GIVES_UP
        while not stop.is_set() and time.monotonic() < ends:
            conn.sendall(piece)
            time.sleep(0.2)
    return answering


def sending(*parts):
    def answering(conn, stop):
        for part in parts:
            conn.sendall(part)
    return answering


def asked(peer, timeout=1):
    began = time.monotonic()
    with pytest.raises(ModelError) as e:
        Http("compatible", peer.url, "", timeout).send("GET", "/models")
    return str(e.value), time.monotonic() - began


# ── the deadline ──────────────────────────────────────────────────────────────

# An answer that ends its connection takes the socket from it: the deadline must still reach that socket.
ENDING = {
    "http-1.0": b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n",
    "connection-close": HEAD + b"Connection: close\r\n\r\n",
    "no-length": HEAD + b"\r\n",
}


@pytest.mark.parametrize("answering", [trickling(), trickling(HEAD, b"X-Slow: 1\r\n"),
                                       *(trickling(head, b"x") for head in ENDING.values())],
                         ids=["body", "head", *ENDING])
def test_an_answer_trickled_in_is_let_go_at_the_deadline(answering):
    with Peer(answering) as peer:
        said, took = asked(peer)
    assert said == "compatible unreachable: timed out" and 0.9 < took < 2.5


def test_an_answer_whole_within_the_deadline_is_read():
    body = b'{"data": [{"id": "llama3.3"}]}'
    with Peer(sending(HEAD + b"Content-Length: %d\r\n\r\n" % len(body), body[:10], body[10:])) as peer:
        answer = Http("local", peer.url, "", 1).send("GET", "/models")
    assert (answer.status, answer.json()) == (200, {"data": [{"id": "llama3.3"}]})


@pytest.mark.parametrize("answering", [trickling(), *(trickling(head, b"x") for head in ENDING.values())],
                         ids=["body", *ENDING])
def test_a_model_server_that_trickles_its_list_is_left_out_after_a_second(answering):
    with Peer(answering) as slow, fo.FakeOpenAI(key=None) as fine:
        began = time.monotonic()
        assert local_servers.listing([slow.url, fine.url]) == {fine.url: list(fo.MODELS)}
        assert time.monotonic() - began < 2.5


# ── how much is read ──────────────────────────────────────────────────────────

def test_an_answer_that_says_it_is_longer_than_the_most_is_read_no_further():
    with Peer(sending(HEAD + b"Content-Length: %d\r\n\r\n" % (exchange.MOST + 1))) as peer:
        said, took = asked(peer)
    assert said == "compatible unreachable: ResponseTooLarge" and took < 0.9


@pytest.mark.parametrize("answering", [
    sending(HEAD + b"\r\n", b"x" * 600, b"x" * 600),                                      # to the end, no length
    sending(HEAD + b"Transfer-Encoding: chunked\r\n\r\n", b"258\r\n" + b"x" * 600 + b"\r\n",
            b"258\r\n" + b"x" * 600 + b"\r\n0\r\n\r\n"),
    trickling(HEAD + b"\r\n", b"x" * 300),                                                # still coming
], ids=["to-the-end", "chunked", "still-coming"])
def test_an_answer_that_runs_past_the_most_is_let_go_there(monkeypatch, answering):
    monkeypatch.setattr(exchange, "MOST", 1000)
    with Peer(answering) as peer:
        said, _ = asked(peer, timeout=5)
    assert said == "compatible unreachable: ResponseTooLarge"


def test_an_answer_of_the_most_is_read_whole(monkeypatch):
    monkeypatch.setattr(exchange, "MOST", 1000)
    with Peer(sending(HEAD + b"Content-Length: 1000\r\n\r\n", b"x" * 1000)) as peer:
        assert len(Http("local", peer.url, "", 1).send("GET", "/models").body) == 1000


def test_an_answer_that_ends_short_of_its_length_is_no_answer():
    with Peer(sending(HEAD + b"Content-Length: 100\r\n\r\n", b'{"data": []')) as peer:
        said, _ = asked(peer)
    assert said == "compatible unreachable: IncompleteRead"


# ── Claude's, each attempt the SDK makes an exchange too ──────────────────────

def claude_asked(peer, timeout=1):
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel
    from mailmind.assistant.application.ports import Prompt
    began = time.monotonic()
    with pytest.raises(ModelError) as e:
        AnthropicModel("sk-ant-test-fictional", peer.url, timeout=timeout, retries=0).complete(
            Prompt("Hi.", "claude-opus-5-5", 100))
    return str(e.value), time.monotonic() - began


@pytest.mark.parametrize("answering", [trickling(), trickling(HEAD, b"X-Slow: 1\r\n"), trickling(ENDING["no-length"], b"x")],
                         ids=["body", "head", "no-length"])
def test_a_claude_answer_trickled_in_is_let_go_at_the_deadline(answering):
    with Peer(answering) as peer:
        said, took = claude_asked(peer)
    assert said.startswith("Claude API unreachable: Request timed out") and 0.9 < took < 2.5


def test_a_claude_answer_that_runs_past_the_most_is_let_go_there(monkeypatch):
    monkeypatch.setattr(exchange, "MOST", 1000)
    with Peer(sending(HEAD + b"\r\n", b"x" * 600, b"x" * 600)) as peer:
        said, _ = claude_asked(peer, timeout=5)
    assert said == "Claude API unreachable: Connection error."


def test_a_claude_answer_of_the_most_is_read_whole(monkeypatch):
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel
    from mailmind.assistant.application.ports import Prompt
    monkeypatch.setattr(exchange, "MOST", 1000)
    body = json.dumps(fa.answer("Read whole.")[1]).encode().ljust(1000)
    with Peer(sending(HEAD + b"Content-Length: 1000\r\n\r\n", body)) as peer:
        reply = AnthropicModel("sk-ant-test-fictional", peer.url, timeout=1, retries=0).complete(
            Prompt("Hi.", "claude-opus-5-5", 100))
    assert reply.text == "Read whole."
