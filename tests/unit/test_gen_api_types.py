"""The API's types, generated from the answers the tests saw: shapes only, never a value."""
import json

from gen_api_types import generate, template


def lines(*answers):
    return [json.dumps(a) for a in answers]


def test_a_path_is_its_route():
    assert template("/api/message/12/attachment/3?x=1") == "/api/message/{pk}/attachment/{pk}"


def test_a_field_some_answers_lack_is_optional_and_a_null_is_said():
    text = generate(lines(["GET", "/api/a", {"n": 1, "s": "x", "maybe": None}], ["GET", "/api/a", {"n": 2, "s": None}]))
    assert "n: number;" in text and "s: string | null;" in text and "maybe?: null;" in text


def test_each_method_has_its_own_answers():
    text = generate(lines(["GET", "/api/a", {"g": True}], ["POST", "/api/a", {"p": True}]))
    get, post = text.split("export interface PostAnswers")
    assert "g: boolean" in get and "p: boolean" in post and "p: boolean" not in get


def test_lists_and_nesting():
    text = generate(lines(["GET", "/api/a", {"items": [{"pk": 1}, {"pk": 2, "key": "k"}], "none": []}]))
    assert "pk: number;" in text and "key?: string;" in text and "none: unknown[];" in text


def test_keys_that_are_data_become_a_record_and_no_value_is_written():
    text = generate(lines(["GET", "/api/a", {"counts": {"ada@example.test": 3, "lin@example.test": 4}, "who": "ada@example.test"}]))
    assert "counts: Record<string, number>;" in text and "who: string;" in text
    assert "example.test" not in text


def test_only_the_api_is_written():
    assert "/page" not in generate(lines(["GET", "/page", {"a": 1}]))
