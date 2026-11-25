"""Canary secrets in every world: values no test data holds anywhere else, so finding one where it must never be is a
leak, whichever code put it there.

- The world's three accounts log in with passwords of their own (fixtures/world.py's PASSWORDS), kept in the Keychain's
  stand-in as any account's is.
- The fake AI providers take keys of their own (fakes/anthropic_api.py's KEY, fakes/openai_api.py's KEY).
- One message's body carries a phrase (fixtures/world.py's BODY_CANARY), in a folder nothing sends to a provider
  unasked.

A test that makes a world (conftest's `world`, and the end-to-end framework's) watches them with `watching`, and when it
ends `Watch.check` looks for each where it must never be:

- in a log: mailmind.log and its rotations in the world's home, the output of a server the suite started (`log_file`),
  and what the app in the test's own process logged (`text`);
- in an answer: a password or a key in any answer the test's client got (`answer`, which app_client calls), the phrase
  in one that refused (a status of 400 or more);
- in a request to an AI provider (each fake in PROVIDERS, by its `seen`): a password anywhere; a key anywhere but the
  auth header of the provider it belongs to; the phrase unless the test asked about its message (the marker
  `asks_ai_about("<corpus file>")`). conftest lists the session's fake Claude API there before each test, and the
  fixtures that make another fake provider add it.

A finding names the canary and where it was found, never the value, so a failure prints no secret. Nothing here imports
MailMind, so the served launcher may use it.
"""
import contextlib
import glob
import json
import os
from dataclasses import dataclass, field

from fakes import anthropic_api, openai_api
from fixtures import world as corpus

SECRET, PHRASE = "secret", "phrase"
AUTH_HEADERS = ("authorization", "x-api-key")


@dataclass(frozen=True)
class Canary:
    name: str
    value: str
    kind: str                       # SECRET: never in a log, an answer or a request; PHRASE: mail, so in answers that work
    message: str | None = None      # the corpus file a phrase is in

    def __repr__(self):             # what a failure may print: the name, never the value
        return f"Canary({self.name!r})"


def every():
    """The canaries every world holds."""
    names = {corpus.ADA: "Ada's", corpus.BEA: "Bea's", corpus.CY: "Cy's"}
    found = [Canary(f"{names[who]} account password", pw, SECRET) for who, pw in corpus.PASSWORDS.items()]
    found.append(Canary("the Claude API key", anthropic_api.KEY, SECRET))
    found.append(Canary("the OpenAI-compatible API key", openai_api.KEY, SECRET))
    message, phrase = corpus.BODY_CANARY
    found.append(Canary(f"the body phrase of {message}", phrase, PHRASE, message))
    return found


@dataclass
class _Provider:
    name: str
    fake: object                    # a fake whose `seen` lists every request since it was reset: .headers, .body
    own_key: str | None


@dataclass
class Watch:
    """What one test's world is watched over: its home, the logs and texts beside it, the answers its client got, the AI
    providers it reached and the messages the test asked the AI about."""
    home: str
    asked: frozenset = frozenset()
    canaries: list = field(default_factory=every)
    logs: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    answers: list = field(default_factory=list)

    def log_file(self, path):
        if str(path) not in self.logs:
            self.logs.append(str(path))

    def text(self, where, supplier):
        """Text read at the check, named `where`: lines the app in this process logged."""
        self.texts.append((where, supplier))

    def answer(self, method, path, status, payload):
        self.answers.append((f"{method} {path.split('?', 1)[0]}", status, payload))

    def findings(self):
        """Where each canary is that it mustn't be, as "<canary's name> in <where>"."""
        found = []
        for where, data in self._logs():
            found += [f"{c.name} in {where}" for c in self.canaries if c.value.encode() in data]
        for request, status, payload in self.answers:
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            found += [f"{c.name} in the answer to {request} ({status})" for c in self.canaries
                      if (c.kind == SECRET or status >= 400) and c.value.encode() in data]
        for p in PROVIDERS:
            for n, req in enumerate(list(getattr(p.fake, "seen", ())), 1):
                found += [f"{name} in request {n} to {p.name}{part}" for name, part in self._in_request(p, req)]
        return list(dict.fromkeys(found))

    def _logs(self):
        paths = sorted(glob.glob(os.path.join(glob.escape(self.home), "mailmind.log*"))) + self.logs
        for path in paths:
            try:
                with open(path, "rb") as f:
                    yield os.path.basename(path) if os.path.dirname(path) == self.home else path, f.read()
            except FileNotFoundError:
                continue
        for where, supplier in self.texts:
            yield where, supplier().encode("utf-8", "replace")

    def _in_request(self, p, req):
        body = json.dumps(req.body, ensure_ascii=False)
        headers = {k.lower(): str(v) for k, v in req.headers.items()}
        for c in self.canaries:
            if c.kind == PHRASE:
                if c.value in body and c.message not in self.asked:
                    yield c.name, " (its message wasn't asked about)"
                continue
            if c.value in body:
                yield c.name, "'s body"
            for name, value in headers.items():
                if c.value in value and not (name in AUTH_HEADERS and c.value == p.own_key):
                    yield c.name, f"'s {name} header"

    def check(self):
        found = self.findings()
        if found:
            raise AssertionError("a canary was found where it must never be: " + "; ".join(found))


_current = None


def current():
    return _current


@contextlib.contextmanager
def watching(home, asked=()):
    """Watch the canaries over a world in `home` while the block runs; the caller checks the Watch it yields."""
    global _current
    watch, before = Watch(str(home), frozenset(asked)), _current
    _current = watch
    try:
        yield watch
    finally:
        _current = before


def answer(method, path, status, payload):
    """An answer the test's client got (app_client calls it), watched if a world is."""
    if _current is not None:
        _current.answer(method, path, status, payload)


def log_file(path):
    """A log the world's server writes (served.serving names its output), watched if a world is."""
    if _current is not None:
        _current.log_file(path)


# The fake AI providers this test may reach. conftest empties it before each test and lists the fake Claude API.
PROVIDERS = []


def provider(name, fake, own_key):
    """A fake AI provider this test may reach, whose requests a world's check reads."""
    if all(p.fake is not fake for p in PROVIDERS):
        PROVIDERS.append(_Provider(name, fake, own_key))
