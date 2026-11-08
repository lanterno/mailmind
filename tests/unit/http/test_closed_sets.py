"""The closed sets a request may name and the limits the page holds a person to: each declared once on the server, in the constant
its use case checks against, and reaching the page from there (the OpenAPI document, mailmind/http/page_limits.py), so the page
keeps no copy of its own.
"""
from datetime import datetime, timezone
from typing import get_args

import pytest

import openapi_doc
import problems
from mailmind.accounts.domain import preferences
from mailmind.assistant.application.bulk import DO_PLAN_SCHEMA
from mailmind.assistant.domain import action_plan, prompts
from mailmind.assistant.domain import limits as limits_of_ai
from mailmind.attention.domain import snooze
from mailmind.calendar.domain import rsvp
from mailmind.compose.domain import limits, outbox_item, resume
from mailmind.curation.domain import cleanup_plan, rulebook
from mailmind.discovery.domain import documents, views
from mailmind.http.page_limits import PAGE_LIMITS, PAGE_PREFERENCE_DEFAULTS
from mailmind.kernel import snoozed
from mailmind.mailbox.application.filing import Filing
from mailmind.mailbox.domain import filing

# Each, as the page has it: by its name in the document.
SETS = {"SnoozePreset": snoozed.SnoozePreset, "DraftTone": prompts.DraftTone, "RewriteOp": prompts.RewriteOp,
        "PlanVerb": action_plan.PlanVerb, "FilingVerb": filing.FilingVerb, "FlagName": filing.FlagName,
        "Verdict": rulebook.Verdict, "BlockScope": cleanup_plan.BlockScope, "RsvpDecision": rsvp.RsvpDecision,
        "ComposeMode": resume.ComposeMode, "ViewVerb": views.ViewVerb, "DocumentKind": documents.DocumentKind,
        "Density": preferences.Density, "Theme": preferences.Theme, "ReadWidth": preferences.ReadWidth, "ReadSize": preferences.ReadSize,
        "ReadFont": preferences.ReadFont, "MailColors": preferences.MailColors, "SearchScope": preferences.SearchScope}


def members(alias):
    return list(get_args(alias.__value__))


@pytest.fixture(scope="module")
def schemas():
    return openapi_doc.document()["components"]["schemas"]


@pytest.mark.parametrize("name", sorted(SETS))
def test_the_page_has_each_set_by_name_with_the_servers_members_in_its_order(schemas, name):
    assert schemas[name] == {"enum": members(SETS[name]), "type": "string"}


def test_a_rule_change_is_a_verdict_or_a_removal(schemas):
    assert schemas["RuleChange"]["anyOf"] == [{"$ref": "#/components/schemas/Verdict"}, {"const": "remove", "type": "string"}]


@pytest.mark.parametrize("table, alias", [(prompts.TONES, prompts.DraftTone), (prompts.REWRITE_OPS, prompts.RewriteOp),
                                          (rsvp.PARTSTAT, rsvp.RsvpDecision), (rsvp.VERB, rsvp.RsvpDecision)])
def test_a_table_a_use_case_reads_holds_every_member_of_its_set(table, alias):
    assert list(table) == members(alias)


def test_the_tuples_a_use_case_checks_against_are_their_sets():
    assert snoozed.SNOOZE_PRESETS == ("later_today", "tomorrow", "weekend", "next_week")
    assert action_plan.VERBS == tuple(members(action_plan.PlanVerb))
    assert (filing.FILING_VERBS, filing.FLAG_NAMES) == (tuple(members(filing.FilingVerb)), tuple(members(filing.FlagName)))
    assert rulebook.VERDICTS == ("keep", "block") and resume.MODES == tuple(members(resume.ComposeMode))
    assert {cleanup_plan.SENDER, cleanup_plan.DOMAIN} == set(members(cleanup_plan.BlockScope))
    assert prompts.DEFAULT_TONE in prompts.TONES


@pytest.mark.parametrize("content_type, filename", [("application/pdf", "a"), ("image/png", "a"), (None, "a.docx"), (None, "a.csv"),
                                                    (None, "a.key"), (None, "a.zip"), ("text/plain", "a"), (None, None)])
def test_a_document_is_one_of_the_kinds_an_answer_names(content_type, filename):
    assert documents.kind_of(content_type, filename) in members(documents.DocumentKind)


@pytest.mark.parametrize("preset", snoozed.SNOOZE_PRESETS)
def test_every_preset_wakes_at_a_time_ahead(preset):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    until = snooze.wake_time({"preset": preset}, now)
    assert until is not None and until > now.timestamp()


def test_the_plan_offers_the_model_the_verbs_and_presets_a_request_may_name():
    said = DO_PLAN_SCHEMA["properties"]
    assert (said["verb"]["enum"], said["snooze_preset"]["enum"]) == (list(action_plan.VERBS), list(snoozed.SNOOZE_PRESETS))


def test_a_plan_that_names_no_preset_there_is_waits_until_tomorrow_as_its_preview_says():
    assert action_plan.preset_told({"snooze_preset": "someday"}, "snooze") == "tomorrow"
    assert action_plan.preset_told({"snooze_preset": "weekend"}, "snooze") == "weekend"


# ── a value outside its set ──────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path, body", [
    ("/api/snooze", {"key": 1, "folder": "INBOX", "preset": "someday"}),
    ("/api/snooze", {"key": 1, "folder": "INBOX", "preset": ""}),
    ("/api/ai/draft-reply", {"key": 1, "tone": "sarcastic"}),
    ("/api/ai/rewrite", {"text": "hello", "op": "summarize"}),
    ("/api/bulk/file", {"verb": "move", "rows": [{"key": 1, "folder": "INBOX"}]}),
    ("/api/flag", {"key": 1, "folder": "INBOX", "flag": "deleted"}),
    ("/api/rules", {"token": "news@loom.example.test", "verdict": "maybe"}),
    ("/api/cleanup/execute", {"from_addr": "news@loom.example.test", "block_scope": "everyone"}),
    ("/api/invite/rsvp", {"key": 1, "decision": "Accept"}),
    ("/api/send", {"account": "ada@example.test", "mode": "draft", "to": "bea@example.test", "text": "hi"}),
    ("/api/draft", {"account": "ada@example.test", "mode": "draft", "text": "hi"}),
    ("/api/views", {"verb": "rename", "id": "x"}),
])
def test_a_value_outside_its_set_is_the_schemas_400(app, path, body):
    r = app.post(path, body)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)


@pytest.mark.parametrize("path, body, said", [
    ("/api/ai/rewrite", {"text": "hello"}, "unknown op"),
    ("/api/rules", {"token": "news@loom.example.test"}, "verdict must be keep|block|remove"),
    ("/api/views", {"name": "x", "q": "from:x"}, "verb must be create|delete"),
])
def test_one_left_out_is_still_the_use_cases_to_word(app, path, body, said):
    r = app.post(path, body)
    assert (r.status, r.json) == (400, problems.refused(said))


# ── the limits ───────────────────────────────────────────────────────────────────────────────────────────────────────


def test_each_limit_the_page_has_is_the_one_its_use_case_holds_to():
    assert PAGE_LIMITS == {"ai_daily_limit_usd": preferences.AI_DAILY_LIMIT_MAX,
                           "attach_bytes": limits.MAX_ATTACH_BYTES, "bulk_rows": Filing.BULK_MAX,
                           "instruction_chars": prompts.INSTRUCTION_MAX, "snooze_copies": snooze.BULK_CAP,
                           "undo_send_seconds": outbox_item.SEND_DELAY}


def test_the_limits_module_says_each_as_a_number():
    said = openapi_doc.limits_module()
    assert said.startswith("// Written by `just gen-api`") and said.endswith("} as const;\n")
    for name, value in PAGE_LIMITS.items():
        assert f"\n  {name}: {value},\n" in said


def test_the_page_starts_from_each_preference_default_as_the_server_has_it():
    assert PAGE_PREFERENCE_DEFAULTS == {**preferences.DEFAULTS, "ai_daily_limit_usd": limits_of_ai.DEFAULT_DAILY_LIMIT_USD}
    said = openapi_doc.limits_module()
    assert '\n  density: "comfortable",\n' in said and "\n  high_contrast: false,\n" in said and "\n  undo_send_seconds: 10,\n" in said
