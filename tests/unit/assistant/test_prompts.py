"""The PromptBook: how a template is filled, and that its version goes up with its words."""
import hashlib
import json

import pytest

from mailmind.assistant.domain import prompts
from mailmind.assistant.domain.prompts import Template

# What each prompt's words were at its version. Words that change raise the version, and the two change here together: an
# answer stored with a version says which words it came from.
VERSIONS = {
    "triage": (1, "33e9fc0dbddea945"),
    "thread": (1, "ab077fc27c48e6de"),
    "brief": (1, "a9badb036d84b65a"),
    "delta": (1, "fd9f9fa37b51cf84"),
    "review": (1, "5cc1ae2942221739"),
    "reply": (1, "2adaceb5764624d8"),
    "rewrite": (1, "399e21cc55b96113"),
    "ask": (1, "a7fb82f9e6a42a53"),
    "plan": (1, "556fb1dcf29f853a"),
}


def words(template):
    """Everything a prompt can say: its text and the parts it is built from."""
    parts = {"thread": [prompts.THREAD_LABELS, prompts.THREAD_OMITTED],
             "reply": [prompts.TONES, prompts.REPLY_INSTRUCTED, prompts.REPLY_UNINSTRUCTED],
             "rewrite": [prompts.REWRITE_OPS]}.get(template.name)
    return json.dumps([template.text, parts], sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize("template", prompts.BOOK, ids=lambda t: t.name)
def test_a_prompts_words_are_those_of_its_version(template):
    assert (template.version, hashlib.sha256(words(template).encode()).hexdigest()[:16]) == VERSIONS[template.name]


def test_the_book_names_each_prompt_once():
    assert sorted(t.name for t in prompts.BOOK) == sorted(VERSIONS)


def test_a_value_is_not_read_again():
    # a chain of str.replace would put the thread where the subject goes
    t = Template("t", 1, "Subject: {subject}\nBody: {body}\n")
    assert t.fill(subject="{body}", body="the text") == "Subject: {body}\nBody: the text\n"
    assert t.fill(subject="s", body="{subject}") == "Subject: s\nBody: {subject}\n"


def test_a_templates_own_braces_are_left_alone():
    t = Template("t", 1, 'Reply with {"body": "<text>", "pks": [{"n": 1}]} for {who}. {} {Who} {two words}')
    assert t.fill(who="Bea") == 'Reply with {"body": "<text>", "pks": [{"n": 1}]} for Bea. {} {Who} {two words}'


def test_a_slot_with_no_value_raises_as_format_did():
    with pytest.raises(KeyError):
        Template("t", 1, "{who}").fill(what="x")


def test_a_number_reads_as_text():
    assert Template("t", 1, "{n} new").fill(n=3) == "3 new"
