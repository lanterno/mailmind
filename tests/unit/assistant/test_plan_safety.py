"""What is taken from a model's plan, and how far a verb may reach, with no I/O."""
import pytest

from mailmind.assistant.domain import action_plan as plan


@pytest.mark.parametrize("verb", sorted(plan.VERBS))
def test_a_clean_act_keeps_its_verb(verb):
    assert plan.verb_told({"intent": "act", "verb": verb}) == verb


@pytest.mark.parametrize("said", [
    {},
    {"intent": "search", "verb": "archive"},
    {"intent": "act"},
    {"intent": "act", "verb": "delete"},        # off the allowlist
    {"intent": "act", "verb": "forward"},
    {"intent": "act", "verb": None},
    {"verb": "archive"},
])
def test_anything_else_degrades_to_a_search(said):
    assert plan.verb_told(said) is None


def test_the_filter_is_read_as_text_and_within_its_cap():
    assert plan.filter_told({"filter": "  from:bea  "}) == "from:bea"
    assert plan.filter_told({"filter": "x" * 500}) == "x" * plan.FILTER_MAX
    assert plan.filter_told({}) == plan.filter_told({"filter": None}) == ""


def test_a_command_is_read_as_text_and_within_its_cap():
    assert plan.command_of("  archive it ") == "archive it"
    assert plan.command_of("y" * 600) == "y" * plan.COMMAND_MAX
    assert plan.command_of(None) == ""


def test_only_a_snooze_names_what_it_waits_for():
    assert plan.preset_told({"snooze_preset": "next_week"}, "snooze") == "next_week"
    assert plan.preset_told({}, "snooze") == "tomorrow"
    assert plan.preset_told({"snooze_preset": "next_week"}, "archive") == "tomorrow"


def test_trash_and_junk_have_the_lower_cap():
    assert plan.cap_of("trash") == plan.cap_of("junk") == plan.TRASH_CAP < plan.CAP == plan.cap_of("archive")


def test_a_command_leaves_your_own_trash_and_drafts_alone_unless_it_names_a_folder():
    assert plan.out_of_reach("read", False) == {"sent", "drafts", "trash", "junk"}
    assert plan.out_of_reach("read", True) == set()


@pytest.mark.parametrize("verb, home", [("archive", {"archive", "all"}), ("trash", {"trash"}), ("junk", {"junk"})])
def test_a_verb_never_reaches_its_own_home(verb, home):
    assert home <= plan.out_of_reach(verb, False)
    assert plan.out_of_reach(verb, True) == home
