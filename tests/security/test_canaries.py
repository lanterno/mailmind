"""The canaries' check (support/canaries.py) fires: a canary put where it mustn't be on purpose is found, named, and never
shown, and one where it may be is let be."""
import json
from types import SimpleNamespace

import pytest

import canaries
from fakes import anthropic_api, openai_api
from fakes.anthropic_api import Request
from fixtures import world as w

PASSWORD, KEY, PHRASE = w.PASSWORDS[w.ADA], anthropic_api.KEY, w.BODY_CANARY[1]


def caught(watch):
    """What the check says, which must hold no canary's value."""
    with pytest.raises(AssertionError) as raised:
        watch.check()
    said = str(raised.value)
    assert not [c.name for c in canaries.every() if c.value in said], said
    return said


def test_every_world_holds_its_canaries(world, fake_keychain):
    assert {fake_keychain.items()[f"account:{who}"] for who in (w.ADA, w.BEA, w.CY)} == set(w.PASSWORDS.values())
    assert PHRASE.encode() in w.raw(w.BODY_CANARY[0])
    assert canaries.current() is not None and canaries.current().home == str(world.dir)


def test_a_password_written_into_the_log_is_found_by_name_and_never_shown(world):
    watch = canaries.current()
    log = world.dir / "mailmind.log.1"
    log.write_text(json.dumps({"msg": "logged in", "password": PASSWORD}) + "\n")
    assert "Ada's account password in mailmind.log.1" in caught(watch)
    log.unlink()                     # so the world's own check, as this test ends, finds it clean
    watch.check()


def test_what_the_app_in_this_process_logs_is_watched(world):
    from mailmind.platform.logging import get_logger
    watch = canaries.current()
    get_logger("canary").info("a line holding " + KEY)       # not a literal on purpose: the key is in the message
    assert "the Claude API key in what the app in the test's process logged" in caught(watch)
    watch.texts.clear()


def test_a_key_in_an_answer_and_the_phrase_in_a_refusal_are_found(tmp_path):
    watch = canaries.Watch(str(tmp_path))
    watch.answer("GET", "/api/message/body?key=1", 200, json.dumps({"text": PHRASE}).encode())
    watch.check()                                   # the phrase is mail: an answer that works may show it
    watch.answer("POST", "/api/ai/key?x=1", 200, json.dumps({"kept": [KEY]}).encode())
    watch.answer("POST", "/api/ai/summarize", 502, json.dumps({"error": "failed on " + PHRASE}).encode())
    said = caught(watch)
    assert "the Claude API key in the answer to POST /api/ai/key (200)" in said
    assert f"the body phrase of {w.BODY_CANARY[0]} in the answer to POST /api/ai/summarize (502)" in said


def test_a_key_goes_only_in_its_own_providers_auth_header(tmp_path, monkeypatch):
    claude, other = SimpleNamespace(seen=[]), SimpleNamespace(seen=[])
    monkeypatch.setattr(canaries, "PROVIDERS", [])
    canaries.provider("the fake Claude API", claude, KEY)
    canaries.provider("the fake OpenAI-compatible API", other, openai_api.KEY)
    watch = canaries.Watch(str(tmp_path))
    claude.seen.append(Request({"x-api-key": KEY}, {"messages": [{"role": "user", "content": "hi"}]}))
    watch.check()
    other.seen.append(Request({"authorization": f"Bearer {KEY}"}, {"messages": []}))
    claude.seen.append(Request({"x-api-key": KEY}, {"messages": [{"role": "user", "content": "it is " + PASSWORD}]}))
    said = caught(watch)
    assert "the Claude API key in request 1 to the fake OpenAI-compatible API's authorization header" in said
    assert "Ada's account password in request 2 to the fake Claude API's body" in said


def test_the_phrase_goes_to_a_provider_only_when_its_message_was_asked_about(tmp_path, monkeypatch):
    claude = SimpleNamespace(seen=[])
    monkeypatch.setattr(canaries, "PROVIDERS", [])
    canaries.provider("the fake Claude API", claude, KEY)
    claude.seen.append(Request({"x-api-key": KEY}, {"messages": [{"role": "user", "content": "notes: " + PHRASE}]}))
    canaries.Watch(str(tmp_path), frozenset({w.BODY_CANARY[0]})).check()
    assert "wasn't asked about" in caught(canaries.Watch(str(tmp_path)))


@pytest.mark.asks_ai_about(w.BODY_CANARY[0])
def test_the_marker_names_what_a_test_asked_about(world):
    assert canaries.current().asked == {w.BODY_CANARY[0]}
