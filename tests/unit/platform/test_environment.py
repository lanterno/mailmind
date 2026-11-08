"""mailmind.platform.environment: every environment variable MailMind reads, declared once, and the only
reader of the environment."""
import ast
import pathlib
import re
import sys

import pytest

import guard
from mailmind.platform import environment
from mailmind.platform.environment import Which
from mailmind.platform.logging import get_logger
from mailmind.platform.profile import Profile

PACKAGE = pathlib.Path(guard.ROOT) / "mailmind"
ITSELF = PACKAGE / "platform" / "environment.py"
NAMES = {v.name for v in environment.VARIABLES}


@pytest.fixture(autouse=True)
def _say_each_again():
    environment.forget()
    yield
    environment.forget()


def _sources():
    return [p for p in PACKAGE.rglob("*.py") if p != ITSELF]


# -- the only reader ------------------------------------------------------------------------

def _reads_the_environment(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" \
                and node.attr in ("environ", "environb", "getenv", "getenvb"):
            yield node.lineno
        if isinstance(node, ast.ImportFrom) and node.module == "os" \
                and any(a.name in ("environ", "environb", "getenv", "getenvb") for a in node.names):
            yield node.lineno


def test_no_module_but_this_one_reads_the_environment():
    readers = {f"{p.relative_to(PACKAGE)}:{line}" for p in _sources() for line in _reads_the_environment(ast.parse(p.read_text()))}
    assert readers == set()


def test_no_module_but_this_one_spells_a_variables_name():
    """A name in a string is a read waiting to happen; elsewhere a variable goes by its declaration."""
    spelled = {f"{p.relative_to(PACKAGE)}:{node.lineno}" for p in _sources() for node in ast.walk(ast.parse(p.read_text()))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and re.fullmatch(r"(MAILMIND|ANTHROPIC)_[A-Z0-9_]*", node.value)}
    assert spelled == set()


def test_every_variable_the_package_mentions_is_declared():
    mentioned = {name for p in PACKAGE.rglob("*.py") for name in re.findall(r"\b(?:MAILMIND|ANTHROPIC)_[A-Z][A-Z0-9_]*", p.read_text())}
    assert mentioned - NAMES == set()


def test_each_is_declared_once_and_listed():
    declared = [v for v in vars(environment).values() if isinstance(v, environment.Variable)]
    assert sorted(v.name for v in declared) == sorted(v.name for v in environment.VARIABLES)
    assert len(NAMES) == len(environment.VARIABLES)
    assert all(v.means for v in environment.VARIABLES)


def test_the_readme_lists_each_variable_and_no_other():
    text = (pathlib.Path(guard.ROOT) / "README.md").read_text(encoding="utf-8")
    table = text.split("## Environment variables", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `((?:MAILMIND|ANTHROPIC)_[A-Z0-9_]+)", table, flags=re.M))
    assert listed == NAMES


def test_it_is_the_standard_library_alone():
    """The command line imports it, and runs on Python alone."""
    tree = ast.parse(ITSELF.read_text())
    top = {a.name.split(".")[0] for node in tree.body if isinstance(node, ast.Import) for a in node.names}
    top |= {node.module.split(".")[0] for node in tree.body if isinstance(node, ast.ImportFrom) and node.module}
    assert top <= set(sys.stdlib_module_names)


def test_the_profiles_it_takes_are_the_profiles():
    assert environment.PROFILE.choices == tuple(p.value for p in Profile)


# -- read at each call ----------------------------------------------------------------------

def test_a_variable_is_read_at_each_call_so_a_test_can_change_it(monkeypatch):
    monkeypatch.setenv("MAILMIND_WORKERS", "account")
    assert environment.WORKERS.value() == "account"
    monkeypatch.setenv("MAILMIND_WORKERS", "global")
    assert environment.WORKERS.value() == "global"
    monkeypatch.delenv("MAILMIND_WORKERS")
    assert environment.WORKERS.value() == "global"


def test_a_reader_takes_a_mapping_in_place_of_the_environment(monkeypatch):
    monkeypatch.setenv("MAILMIND_EXPIRE_SECS", "60")
    assert environment.EXPIRE_SECS.value({"MAILMIND_EXPIRE_SECS": "90"}) == 90
    assert environment.EXPIRE_SECS.value({}) == 3600
    assert environment.EXPIRE_SECS.value() == 60


# -- each type ------------------------------------------------------------------------------

@pytest.mark.parametrize("value, want", [(None, "global"), ("", "global"), ("account", "account"), (" Account ", "account"),
                                         ("global", "global"), ("GLOBAL", "global")])
def test_a_choice_is_a_name_in_any_case(value, want):
    env = {} if value is None else {"MAILMIND_WORKERS": value}
    assert environment.WORKERS.value(env) == want
    assert environment.WORKERS.named(env) == want


@pytest.mark.parametrize("choice, value, want", [(environment.PROFILE, "readonly", "read-only"),
                                                  (environment.WORKERS, "acount", "global"),
                                                  (environment.LOG_LEVEL, "verbose", "info")], ids=repr)
def test_a_name_that_is_no_choice_reads_as_its_fallback_and_the_log_says_so(choice, value, want, capsys):
    assert choice.named({choice.name: value}) is None
    assert capsys.readouterr().out == ""
    assert choice.value({choice.name: value}) == want
    assert capsys.readouterr().out == f"[env] {choice.name} is set to a value it doesn't take, so it reads as {want}\n"


def test_a_known_choice_says_nothing(capsys):
    assert environment.LOG_LEVEL.value({"MAILMIND_LOG_LEVEL": "DEBUG"}) == "debug"
    assert environment.WORKERS.value({}) == "global"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value, want", [
    (None, Which()), ("", Which()), ("  ", Which()), ("0", Which()), (" Off ", Which()), ("false", Which()), ("no", Which()),
    ("1", Which(every=True)), ("All", Which(every=True)), (" yes ", Which(every=True)), ("ON", Which(every=True)),
    ("ada@example.test", Which(listed=frozenset({"ada@example.test"}))),
    ("ada@example.test, bo@example.test,", Which(listed=frozenset({"ada@example.test", "bo@example.test"})))])
def test_accounts_are_every_one_none_or_a_list(value, want, capsys):
    env = {} if value is None else {"MAILMIND_POOL": value}
    assert environment.POOL.value(env) == want
    assert bool(environment.POOL.value(env)) is bool(want.every or want.listed)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value, want, reads_as", [
    ("ture", Which(), "off"), ("2", Which(), "off"), ("ada@example.test, al", Which(listed=frozenset({"ada@example.test"})),
                                                       "the addresses it lists")])
def test_an_entry_that_is_no_address_names_no_account_so_a_typo_turns_nothing_on(value, want, reads_as, capsys):
    assert environment.WARM_FOLDERS.value({"MAILMIND_WARM_FOLDERS": value}) == want
    out = capsys.readouterr().out
    assert out == f"[env] MAILMIND_WARM_FOLDERS is set to a value it doesn't take, so it reads as {reads_as}\n"


@pytest.mark.parametrize("value, want", [(None, 1800), ("", 1800), (" ", 1800), ("60", 60), (" 60 ", 60), ("0", 0), ("-5", -5)])
def test_seconds_are_a_whole_number_and_empty_is_unset(value, want, capsys):
    env = {} if value is None else {"MAILMIND_RECONCILE_SECS": value}
    assert environment.RECONCILE_SECS.value(env) == want
    assert capsys.readouterr().out == ""


def test_seconds_that_are_no_number_read_as_the_default_and_the_log_names_the_variable_not_the_value(capsys):
    assert environment.EXPIRE_SECS.value({"MAILMIND_EXPIRE_SECS": "an-hour-sk-ant-123"}) == 3600
    out = capsys.readouterr().out
    assert "[env] MAILMIND_EXPIRE_SECS is set to a value it doesn't take, so it reads as 3600" in out
    assert "an-hour" not in out and "sk-ant" not in out


def test_a_value_it_doesnt_take_is_said_once(monkeypatch):
    said = []
    monkeypatch.setattr(get_logger("env"), "warning", lambda *a, **k: said.append(a))
    for _ in range(3):
        environment.EXPIRE_SECS.value({"MAILMIND_EXPIRE_SECS": "soon"})
    environment.EXPIRE_SECS.value({"MAILMIND_EXPIRE_SECS": "later"})
    assert len(said) == 2


@pytest.mark.parametrize("value, want", [(None, None), ("", None), ("claude-haiku-5-5", "claude-haiku-5-5")])
def test_text_is_as_set_and_empty_is_none(value, want):
    env = {} if value is None else {"MAILMIND_MODEL": value}
    assert environment.MODEL.value(env) == want


def test_a_secret_is_text_that_nothing_logs():
    assert isinstance(environment.PROTON_BRIDGE_PASS, environment.Secret)
    assert isinstance(environment.ANTHROPIC_API_KEY, environment.Secret)
    assert environment.ANTHROPIC_API_KEY.value({"ANTHROPIC_API_KEY": " sk-ant-x "}) == " sk-ant-x "
    assert "sk-ant" not in repr(environment.ANTHROPIC_API_KEY)


def test_a_credential_reads_the_variable_the_config_names():
    assert environment.credential("MAILMIND_TEST_DEE_PASSWORD", {"MAILMIND_TEST_DEE_PASSWORD": "pw"}) == "pw"
    assert environment.credential("MAILMIND_TEST_DEE_PASSWORD", {}) is None


@pytest.mark.parametrize("value, want", [(None, ""), ("", ""), ("~/mm", "~/mm")])
def test_a_directory_is_as_set_for_settings_to_resolve(value, want):
    env = {} if value is None else {"MAILMIND_HOME": value}
    assert environment.HOME.value(env) == want


# -- a switch: one rule ---------------------------------------------------------------------

SWITCHES = [v for v in environment.VARIABLES if isinstance(v, environment.Switch)]


@pytest.mark.parametrize("value, want", [(None, False), ("", False), (" ", False), ("0", False), ("false", False),
                                         (" No ", False), ("OFF", False), ("1", True), ("true", True), (" Yes ", True),
                                         ("on", True), ("TRUE", True)])
@pytest.mark.parametrize("switch", SWITCHES, ids=repr)
def test_every_switch_reads_one_rule(switch, value, want, capsys):
    env = {} if value is None else {switch.name: value}
    assert switch.on(env) is want
    assert capsys.readouterr().out == ""


def test_each_switch_says_which_way_a_value_it_doesnt_take_goes():
    """Toward writing, sending or spending less, and where neither does, toward off: a new switch says which."""
    assert {s.name: s.typo for s in SWITCHES} == {
        "MAILMIND_NOSYNC": True,            # read-only
        "MAILMIND_NO_DRAFT_TICK": True,     # no replies drafted ahead, which spends on a guess
        "MAILMIND_NO_BATCHES": False,       # a backlog as a batch, at half price
        "MAILMIND_NO_SCHEMA": False,        # structured output, as unset
    }


@pytest.mark.parametrize("value", ["ture", "y", "2", "enabled", "-1"])
@pytest.mark.parametrize("switch", SWITCHES, ids=repr)
def test_a_value_it_doesnt_take_reads_as_its_side_and_the_log_says_so_without_the_value(switch, value, capsys):
    assert switch.on({switch.name: value}) is switch.typo
    side = "on" if switch.typo else "off"
    out = capsys.readouterr().out
    assert out == f"[env] {switch.name} is set to a value it doesn't take, so it reads as {side}\n"


def test_a_typo_in_the_older_read_only_switch_still_fails_toward_read_only(capsys):
    from mailmind.platform import profile
    assert profile.current({"MAILMIND_NOSYNC": "ture"}) is Profile.READ_ONLY
    assert profile.current({"MAILMIND_NOSYNC": "ture", "MAILMIND_PROFILE": "full"}) is Profile.READ_ONLY
    assert "MAILMIND_NOSYNC is set to a value it doesn't take, so it reads as on" in capsys.readouterr().out


def test_a_name_that_is_no_profile_still_reads_as_read_only_and_says_so_once(capsys):
    from mailmind.platform import profile
    for _ in range(3):
        assert profile.current({"MAILMIND_PROFILE": "readonly"}) is Profile.READ_ONLY
    assert capsys.readouterr().out == "[env] MAILMIND_PROFILE is set to a value it doesn't take, so it reads as read-only\n"
