"""budgets.toml read whole: every entry well formed, within its limit when last measured, and measured by a script;
and the release rule a measured number is judged by (docs/test-strategy.md, section 5)."""
import budgets
import pytest


def test_every_entry_has_its_fields_and_was_within_its_limit_when_last_measured():
    assert budgets.problems(budgets.load()) == []


def test_every_entry_not_measured_by_hand_is_named_by_a_script_of_its_section():
    assert budgets.unread(budgets.load()) == []


WALL = {"what": "a wall time", "on": "here", "limit": 100.0, "measured": 50.0}
MEMORY = {"what": "a footprint", "on": "here", "limit": 100.0, "measured": 50.0}


@pytest.mark.parametrize(("value", "verdict"), [
    (100.0, budgets.WITHIN),
    (110.0, budgets.TOLERATED),       # the default tenth over
    (110.5, budgets.OVER),
])
def test_a_wall_time_may_run_over_by_its_tolerance(value, verdict):
    assert budgets.judge("first_rows_ms", WALL, value) == verdict


def test_an_entry_may_give_a_tolerance_of_its_own():
    entry = dict(WALL, tolerance=0.2)
    assert budgets.judge("first_rows_ms", entry, 119.0) == budgets.TOLERATED
    assert budgets.judge("first_rows_ms", entry, 121.0) == budgets.OVER


def test_a_footprint_or_a_count_has_no_tolerance():
    assert budgets.judge("idle_memory_mb", MEMORY, 100.0) == budgets.WITHIN
    assert budgets.judge("idle_memory_mb", MEMORY, 100.1) == budgets.OVER


@pytest.mark.parametrize(("entry", "complaint"), [
    (dict(MEMORY, tolerance=0.1), "only a wall time"),
    (dict(WALL, tolerance=0.5), "at most 0.25"),
    (dict(WALL, limit=0), "over 0"),
    (dict(WALL, measured=200.0), "over its limit"),
    (dict(WALL, by="hand"), "says `how`"),
    ({"limit": 1.0, "measured": 1.0}, "no `what`"),
])
def test_the_file_check_names_what_is_wrong(entry, complaint):
    name = "idle_memory_mb" if "footprint" in entry.get("what", "") else "first_rows_ms"
    found = budgets.problems({"core": {name: entry}})
    assert any(complaint in f for f in found), found


def test_a_count_is_a_whole_number_and_may_be_measured_on_each_server():
    entry = {"what": "a count", "on": "both servers", "limit": 4, "measured": {"python": 4, "rust": 3}}
    assert budgets.problems({"counts": {"move_commands": entry}}) == []
    assert any("whole number" in f for f in budgets.problems({"counts": {"move_commands": dict(entry, limit=4.0)}}))
    over = dict(entry, measured={"python": 5, "rust": 3})
    assert any("over its limit" in f for f in budgets.problems({"counts": {"move_commands": over}}))


def test_a_section_no_script_measures_is_named():
    assert any("no script measures" in f for f in budgets.problems({"phone": {}}))


def test_a_results_file_keeps_only_name_value_lines(tmp_path):
    results = tmp_path / "budgets.txt"
    results.write_text('{\n  "problems": ["a=b"],\n  first_rows_ms=200\n# note=1\nfirst_rows_ms=180.5\nbad=x\n')
    assert budgets.read_results(results) == {"first_rows_ms": 180.5}


def test_judging_a_section_stops_on_a_number_over_or_one_not_measured(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(budgets, "load", lambda: {"apple": {
        "first_rows_ms": WALL,
        "idle_memory_mb": MEMORY,
        "size_mb": dict(MEMORY, by="hand", how="by its link map"),
    }})
    results = tmp_path / "budgets.txt"
    results.write_text("first_rows_ms=105\nidle_memory_mb=90\n")
    assert budgets.main(["judge", "apple", str(results)]) == 0
    assert "open an issue" in capsys.readouterr().out

    results.write_text("first_rows_ms=105\n")
    assert budgets.main(["judge", "apple", str(results)]) == 1
    assert "NOT MEASURED" in capsys.readouterr().out

    results.write_text("first_rows_ms=111\nidle_memory_mb=90\n")
    assert budgets.main(["judge", "apple", str(results)]) == 1
    assert "OVER: the release stops" in capsys.readouterr().out
